"""RED tests for bd#109 -- three /build failure classes checked on bytedigger.

Spec: docs/decisions/2026-10-16-bd109-build-failure-classes.md (section 3, AC3-AC9).

AC -> test map
  AC1/AC2 live in test_bd109_class1_interpreter_pin.py (class 1 pins, GREEN today)
  AC3 test_ac3_*  _invoke_spec_llm snapshots the prior spec at cycle >= 2, nothing at cycle 1
  AC4 test_ac4_*  shrunk writer output is blocked, canonical restored byte for byte
  AC5 test_ac5_*  HAL_SPEC_SHRINK_GUARD=0 restores the old write (contrast with the guard-on run)
  AC6 test_ac6_*  a new text above the ratio is written (contrast with a blocked stub)
  AC7 test_ac7_*  shrink at the spent spec_gates budget is terminal E_SPEC_INCOMPLETE_FATAL
  AC8 test_ac8_*  _gate_on_review REVISE at the cap keeps artifacts and now names spec_path
  AC9 test_ac9_*  HAL_SPEC_SHRINK_GUARD registered in flags_catalog.FLAGS, default "1"
  AC10 test_ac10_* ratio 0.5 pinned: prior 1000 lines, 450 blocked, 520 written
  AC11 test_ac11_* exact boundary: new == 0.5*prior written (strict '<'), one line less blocked
  AC12 test_ac12_* raw_response source, blank/absent canonical, no prior: written, no block
  AC13 test_ac13_* prior_spec_path missing or unreadable: fail open to the write
  AC14 test_ac14_* guard=0 at cycle 2: no prior file, no prior_spec_path
  AC15 test_ac15_* blank canonical at cycle 2 with a stale prior on disk: not reported
  (AC4 uses a CRLF prior so the restore must be byte-exact)

New behaviour (prior snapshot, shrink guard, spec_path on the terminal result, the flag) is
reached only inside test bodies, so the module collects and each RED test fails at assert time.
Only the LLM subprocess boundary and telemetry emit are stubbed; every unit under test is real.
No singleton-resource or timing fixtures (workflows.md 1i); no sys.path mutation (1q).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from bytedigger_engine import telemetry_ctx
from bytedigger_engine.contracts import StepResult, WorkflowContext
from bytedigger_engine.event_log import EventLog
from bytedigger_engine.workflows import phase_45_spec

PRIOR_NAME = ".build-spec.prior.md"
FLAG = "HAL_SPEC_SHRINK_GUARD"


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    monkeypatch.delenv(FLAG, raising=False)
    for k in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"):
        monkeypatch.delenv(k, raising=False)
    telemetry_ctx.clear_current_run()
    yield
    telemetry_ctx.clear_current_run()


# --- helpers -------------------------------------------------------------------


def _wf_ctx(scratch: Path, **extra) -> WorkflowContext:
    scratch.mkdir(parents=True, exist_ok=True)
    return WorkflowContext(
        tenant_id="t", scope=None, db_path=None,
        org_config={"scratchpad_dir": str(scratch), **extra},
        question="bd109", session_id="s", persona="p", framework=None, domain=None,
    )


def _lines(n: int, head: str = "## Context\n") -> str:
    """n lines of constant width, first line the required heading."""
    return head + "".join(f"L{i:04d} spec body text\n" for i in range(1, n))


# --- AC3: _invoke_spec_llm snapshots the prior spec -----------------------------


def _invoke(tmp_path: Path, monkeypatch, *, cycle: int, doc_text: str, stale_prior: str | None = None):
    scratch = tmp_path / "scratch"
    doc = scratch / phase_45_spec.SPEC_DOC_RELPATH
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text(doc_text, encoding="utf-8")
    if stale_prior is not None:
        (doc.parent / PRIOR_NAME).write_text(stale_prior, encoding="utf-8")
    seen: dict = {}

    def _fake_llm(**kwargs):
        seen["doc_present_at_call"] = doc.exists()
        return StepResult(status="ok", data={**kwargs["extra_data"], "raw_response": ""},
                          duration_ms=0, step_name="invoke_spec_llm")

    monkeypatch.setattr(phase_45_spec, "invoke_llm_subprocess", _fake_llm)
    prev = StepResult(status="ok", data={"prompt": "write the spec", "doc_path": str(doc), "cycle": cycle},
                      duration_ms=0, step_name="build_spec_prompt")
    result = phase_45_spec._invoke_spec_llm(_wf_ctx(scratch), prev)
    return result, doc, seen


def test_ac3_cycle2_snapshots_prior_spec_and_cycle1_snapshots_nothing(tmp_path, monkeypatch):
    prior_text = _lines(300)
    result, doc, seen = _invoke(tmp_path / "c2", monkeypatch, cycle=2, doc_text=prior_text)
    prior = doc.parent / PRIOR_NAME
    assert result.status == "ok", result
    assert prior.is_file(), "cycle 2 must copy the finished spec to .build-spec.prior.md before the unlink"
    assert prior.read_text(encoding="utf-8") == prior_text
    assert result.data.get("prior_spec_path") == str(prior), result.data
    assert seen["doc_present_at_call"] is False, "the canonical spec is still unlinked before the writer runs"

    result1, doc1, _ = _invoke(tmp_path / "c1", monkeypatch, cycle=1, doc_text=prior_text)
    assert not (doc1.parent / PRIOR_NAME).exists(), "cycle 1 must snapshot nothing"
    assert not result1.data.get("prior_spec_path")


# --- AC4-AC7: _write_spec_doc shrink guard --------------------------------------


def _shrink_case(tmp_path: Path, monkeypatch, *, new_text: str, prior_text: str | None = None,
                 gate_attempts: dict | None = None):
    """Cycle 2: canonical holds what the writer left (new_text); prior copy holds the old spec."""
    prior_text = _lines(300) if prior_text is None else prior_text
    scratch = tmp_path / "scratch"
    canonical = scratch / phase_45_spec.SPEC_DOC_RELPATH
    canonical.parent.mkdir(parents=True, exist_ok=True)
    prior = canonical.parent / PRIOR_NAME
    prior.write_bytes(prior_text.encode("utf-8"))  # bytes: a CRLF prior must survive untouched
    canonical.write_text(new_text, encoding="utf-8")
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(phase_45_spec, "_emit_safe", lambda t, p=None, *a, **k: events.append((t, p)))
    data: dict = {"doc_path": str(canonical), "raw_response": "", "cycle": 2, "prior_spec_path": str(prior)}
    if gate_attempts is not None:
        data["gate_attempts"] = gate_attempts
    prev = StepResult(status="ok", data=data, duration_ms=0, step_name="invoke_spec_llm")
    result = phase_45_spec._write_spec_doc(_wf_ctx(scratch), prev)
    cycle_copy = canonical.parent / Path(phase_45_spec._spec_cycle_relpath(2)).name
    return result, canonical, cycle_copy, prior_text, events


def test_ac4_shrunk_output_is_blocked_and_canonical_restored(tmp_path, monkeypatch):
    stub = _lines(101)
    crlf_prior = _lines(300).replace("\n", "\r\n")  # restore must be byte-exact, not newline-normalised
    r, canonical, cycle_copy, prior_text, events = _shrink_case(
        tmp_path, monkeypatch, new_text=stub, prior_text=crlf_prior,
    )
    assert b"\r\n" in prior_text.encode("utf-8")
    assert r.status == "error" and r.error_code == "E_SPEC_INCOMPLETE" and r.recoverable is True, r
    assert canonical.read_bytes() == prior_text.encode("utf-8"), "canonical must hold the prior text byte for byte"
    assert not cycle_copy.exists(), "no per-cycle copy of the stub"
    blocked = [p for t, p in events if t == "spec_shrink_blocked"]
    assert len(blocked) == 1, [t for t, _ in events]
    assert blocked[0] == {"cycle": 2, "prior_bytes": len(prior_text.encode("utf-8")),
                          "new_bytes": len(stub.encode("utf-8"))}
    findings = r.data.get("findings", "")
    assert "REWRITE the FULL spec body" in findings
    assert str(len(prior_text.encode("utf-8"))) in findings and str(len(stub.encode("utf-8"))) in findings


def test_ac5_kill_switch_zero_restores_the_stub_write(tmp_path, monkeypatch):
    stub = _lines(101)
    # contrast: guard on (default) blocks the very same inputs
    on, canon_on, _, prior_text, _ = _shrink_case(tmp_path / "on", monkeypatch, new_text=stub)
    assert on.error_code == "E_SPEC_INCOMPLETE", on
    assert canon_on.read_text(encoding="utf-8") == prior_text

    monkeypatch.setenv(FLAG, "0")
    off, canon_off, cycle_copy, _, events = _shrink_case(tmp_path / "off", monkeypatch, new_text=stub)
    assert off.status == "ok", off
    assert canon_off.read_bytes() == stub.encode("utf-8"), "guard off: the stub is persisted as before"
    assert cycle_copy.is_file()
    assert not [t for t, _ in events if t == "spec_shrink_blocked"]


def test_ac6_text_above_the_ratio_is_written(tmp_path, monkeypatch):
    # contrast: a stub is blocked, so "always write" cannot pass
    stub_r, *_ = _shrink_case(tmp_path / "stub", monkeypatch, new_text=_lines(101))
    assert stub_r.error_code == "E_SPEC_INCOMPLETE", stub_r

    new_text = _lines(280)
    r, canonical, cycle_copy, _, events = _shrink_case(tmp_path / "ok", monkeypatch, new_text=new_text)
    assert r.status == "ok", r
    assert canonical.read_bytes() == new_text.encode("utf-8")
    assert cycle_copy.read_bytes() == new_text.encode("utf-8")
    assert not [t for t, _ in events if t == "spec_shrink_blocked"]


def test_ac7_shrink_at_spent_retry_budget_is_terminal(tmp_path, monkeypatch):
    r, canonical, cycle_copy, prior_text, events = _shrink_case(
        tmp_path, monkeypatch, new_text=_lines(101), gate_attempts={"spec_gates": 1},
    )
    assert r.status == "error" and r.error_code == "E_SPEC_INCOMPLETE_FATAL" and r.recoverable is False, r
    assert canonical.read_bytes() == prior_text.encode("utf-8"), "prior text must still be on disk at the cap"
    assert not cycle_copy.exists()
    assert [p for t, p in events if t == "spec_shrink_blocked"]


# --- AC8: review cap keeps artifacts and names spec_path ------------------------

FINDINGS = [{"id": "F1", "type": "gap", "evidence": "e", "required_action": "fix it"}]
REVISE_RAW = (
    "## Verdict\nREVISE\n\n## Findings (structured)\n```json\n" + json.dumps(FINDINGS)
    + "\n```\n\n## Findings\n- fix it\n"
)


def _review_prev(scratch: Path, cycle: int, **extra) -> StepResult:
    spec = scratch / phase_45_spec.SPEC_DOC_RELPATH
    spec.parent.mkdir(parents=True, exist_ok=True)
    spec.write_text("## Context\nbd109 spec body\n", encoding="utf-8")
    review = scratch / "specs" / "review.md"
    review.write_text("review\n", encoding="utf-8")
    return StepResult(
        status="ok",
        data={"verdict": "REVISE", "review_path": str(review), "spec_path": str(spec),
              "cycle": cycle, "review_raw": REVISE_RAW, **extra},
        duration_ms=0, step_name="write_review_doc",
    )


def test_ac8_review_cap_keeps_artifacts_and_names_spec_path(tmp_path):
    from bytedigger_engine.findings_sidecar import SIDECAR_RELNAME

    scratch = tmp_path / "scratch"
    telemetry_ctx.set_current_run(
        event_log=EventLog(path=tmp_path / "events.jsonl"), run_id="r109", step_name="bd109",
        phase="phase_45_spec", cycle=1,
    )
    # cycle 1 REVISE (retry) persists the findings thread; cycle 2 REVISE at the cap is terminal
    first = phase_45_spec._gate_on_review(_wf_ctx(scratch), _review_prev(scratch, 1))
    assert first.recoverable is True, first
    prev = _review_prev(scratch, 2, gate_attempts={"spec_review": 1})
    r = phase_45_spec._gate_on_review(_wf_ctx(scratch), prev)
    assert r.status == "error" and r.error_code == "E_REVIEW_FAILED" and r.recoverable is False, r
    spec_path = prev.data["spec_path"]
    assert Path(spec_path).is_file()
    assert Path(prev.data["review_path"]).is_file()
    assert (scratch / SIDECAR_RELNAME).is_file(), "findings thread must stay on disk"
    assert r.data["review_path"] == prev.data["review_path"]
    assert r.data.get("spec_path") == spec_path, "terminal result must point at the spec"


# --- AC9: kill switch registered -------------------------------------------------


def test_ac9_shrink_guard_flag_registered_with_default_on():
    from bytedigger_engine import flags_catalog

    entry = flags_catalog.FLAGS.get(FLAG)
    assert entry is not None, f"{FLAG} missing from flags_catalog.FLAGS"
    assert entry["default"] == "1"
    assert entry["kind"] == "gate"
    assert entry["module"] == "workflows/phase_45_spec.py"
    assert FLAG not in flags_catalog.unregistered_tokens_in_files([Path(phase_45_spec.__file__)])


# --- AC10-AC15: threshold pin, fail-open paths, snapshot gating ------------------


def _uniform(n: int) -> str:
    """n lines, every line exactly 21 bytes (incl. the required heading line)."""
    head = "## Context\n" + "x" * 9 + "\n"
    return head + "".join(f"L{i:04d} spec body text\n" for i in range(1, n))


def test_ac10_ratio_half_is_pinned_at_1000_lines(tmp_path, monkeypatch):
    prior = _uniform(1000)
    assert len(prior.encode()) == 21 * 1000
    blocked, canon_b, copy_b, _, ev_b = _shrink_case(tmp_path / "b", monkeypatch, new_text=_uniform(450), prior_text=prior)
    assert blocked.error_code == "E_SPEC_INCOMPLETE", blocked
    assert canon_b.read_bytes() == prior.encode() and not copy_b.exists()
    assert [p for t, p in ev_b if t == "spec_shrink_blocked"]

    new = _uniform(520)
    ok, canon_w, copy_w, _, ev_w = _shrink_case(tmp_path / "w", monkeypatch, new_text=new, prior_text=prior)
    assert ok.status == "ok", ok
    assert canon_w.read_bytes() == new.encode() and copy_w.read_bytes() == new.encode()
    assert not [t for t, _ in ev_w if t == "spec_shrink_blocked"]


def test_ac11_exact_half_is_written_one_line_less_is_blocked(tmp_path, monkeypatch):
    prior = _uniform(1000)
    exact, one_less = _uniform(500), _uniform(499)
    assert len(exact.encode()) * 2 == len(prior.encode())  # new_bytes == 0.5 * prior_bytes exactly
    assert len(one_less.encode()) * 2 < len(prior.encode())

    less_r, canon_l, copy_l, _, ev_l = _shrink_case(tmp_path / "l", monkeypatch, new_text=one_less, prior_text=prior)
    assert less_r.error_code == "E_SPEC_INCOMPLETE", less_r
    assert canon_l.read_bytes() == prior.encode() and not copy_l.exists()
    assert [p for t, p in ev_l if t == "spec_shrink_blocked"]

    r, canon_e, copy_e, _, ev_e = _shrink_case(tmp_path / "e", monkeypatch, new_text=exact, prior_text=prior)
    assert r.status == "ok", "guard is strictly '<': exactly half must be written"
    assert canon_e.read_bytes() == exact.encode() and copy_e.read_bytes() == exact.encode()
    assert not [t for t, _ in ev_e if t == "spec_shrink_blocked"]


def _raw_case(tmp_path: Path, monkeypatch, *, canonical_text: str | None, raw_response: str,
              prior_text: str | None, prior_kind: str = "file"):
    """Cycle 2 where the writer's file is blank/absent; prior_kind: file | absent | dir | none."""
    scratch = tmp_path / "scratch"
    canonical = scratch / phase_45_spec.SPEC_DOC_RELPATH
    canonical.parent.mkdir(parents=True, exist_ok=True)
    if canonical_text is not None:
        canonical.write_text(canonical_text, encoding="utf-8")
    prior = canonical.parent / PRIOR_NAME
    if prior_kind == "file":
        prior.write_bytes((prior_text or "").encode("utf-8"))
    elif prior_kind == "dir":
        prior.mkdir()
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(phase_45_spec, "_emit_safe", lambda t, p=None, *a, **k: events.append((t, p)))
    data: dict = {"doc_path": str(canonical), "raw_response": raw_response, "cycle": 2}
    if prior_kind != "none":
        data["prior_spec_path"] = str(prior)
    prev = StepResult(status="ok", data=data, duration_ms=0, step_name="invoke_spec_llm")
    result = phase_45_spec._write_spec_doc(_wf_ctx(scratch), prev)
    return result, canonical, events


def test_ac12_raw_response_source_without_prior_is_written_as_before(tmp_path, monkeypatch):
    short = _lines(101)
    prior_text = _lines(300)
    # contrast: with a prior snapshot the same short raw_response (blank canonical) is blocked
    blocked, _, ev_b = _raw_case(tmp_path / "with", monkeypatch, canonical_text="  \n",
                                 raw_response=short, prior_text=prior_text)
    assert blocked.error_code == "E_SPEC_INCOMPLETE", blocked
    assert [p for t, p in ev_b if t == "spec_shrink_blocked"]

    # no prior_spec_path (blank/absent canonical at cycle 2 snapshots nothing): written as before
    for canon in ("  \n", None):
        r, canonical, ev = _raw_case(tmp_path / f"none-{canon is None}", monkeypatch, canonical_text=canon,
                                     raw_response=short, prior_text=None, prior_kind="none")
        assert r.status == "ok", r
        assert canonical.read_bytes() == short.encode("utf-8")
        assert not [t for t, _ in ev if t == "spec_shrink_blocked"]
        assert not (r.data or {}).get("prior_spec_path")


@pytest.mark.parametrize("kind", ["absent", "dir"])
def test_ac13_missing_or_unreadable_prior_fails_open(tmp_path, monkeypatch, kind):
    stub = _lines(101)
    # contrast: a readable prior blocks the same stub
    blocked, *_ = _shrink_case(tmp_path / "ctl", monkeypatch, new_text=stub)
    assert blocked.error_code == "E_SPEC_INCOMPLETE", blocked

    r, canonical, ev = _raw_case(tmp_path / kind, monkeypatch, canonical_text=stub,
                                 raw_response="", prior_text=None, prior_kind=kind)
    assert r.status == "ok", r
    assert canonical.read_bytes() == stub.encode("utf-8")
    assert not [t for t, _ in ev if t == "spec_shrink_blocked"]


def test_ac14_kill_switch_zero_snapshots_nothing_at_cycle_2(tmp_path, monkeypatch):
    # contrast: guard on creates the snapshot
    on, doc_on, _ = _invoke(tmp_path / "on", monkeypatch, cycle=2, doc_text=_lines(300))
    assert (doc_on.parent / PRIOR_NAME).is_file() and on.data.get("prior_spec_path")

    monkeypatch.setenv(FLAG, "0")
    off, doc_off, _ = _invoke(tmp_path / "off", monkeypatch, cycle=2, doc_text=_lines(300))
    assert off.status == "ok", off
    assert not (doc_off.parent / PRIOR_NAME).exists()
    assert not off.data.get("prior_spec_path")


def test_ac15_blank_canonical_does_not_report_a_stale_prior(tmp_path, monkeypatch):
    # contrast: non-blank canonical reports the (refreshed) prior
    on, doc_on, _ = _invoke(tmp_path / "on", monkeypatch, cycle=2, doc_text=_lines(300), stale_prior="old\n")
    assert on.data.get("prior_spec_path") == str(doc_on.parent / PRIOR_NAME)

    for blank in ("", "  \n\t\n"):
        r, doc, _ = _invoke(tmp_path / f"blank{len(blank)}", monkeypatch, cycle=2, doc_text=blank,
                            stale_prior=_lines(300))
        assert r.status == "ok", r
        assert not r.data.get("prior_spec_path"), "a stale file on disk must not be reported as the prior"
