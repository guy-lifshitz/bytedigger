"""RED tests for bd#92 — per-cycle artifacts are cleared or keyed before a later cycle can read them.

Spec: docs/decisions/2026-10-02-bd92-per-cycle-artifacts.md (§6 AC1-AC14; AC15 is
the full-suite delta, checked by the orchestrator, not here).

AC -> test map
  AC1  test_ac1_stale_review_doc_is_gone_and_fresh_stdout_is_written
  AC2  test_ac2_review_doc_unlink_is_attempted_and_oserror_does_not_fail_the_step
  AC3  test_ac3_load_rejects_other_run / _cycle_mismatch / _legacy_payload,
       test_ac3_load_returns_list_for_same_run_previous_cycle (+ persist round trip)
  AC4  test_ac4_prompt_ignores_other_run_thread_and_emits_rejected_event
  AC5  test_ac5_gate_retry_does_not_apply_cycle1_thread   (regression lock, passes today)
  AC6  test_ac6_revise_branch_persists_run_id_and_cycle
  AC7  test_ac7_lint_pass_does_not_write_ship_sidecar
  AC8  test_ac8_ship_verdict_writes_verdict_sidecar (+ direct _write_ship_sidecar check)
  AC9  test_ac9_revise_retry_unlinks_ship_sidecar / test_ac9_revise_terminal_unlinks_ship_sidecar
  AC10 test_ac10_prior_base_requires_ship_verdict
  AC11 test_ac11_every_sentinel_name_matches_its_glob / test_ac11_run_id_prefix_does_not_overmatch
  AC12 test_ac12_invalidate_cycle_sentinels_clears_norun_hashed /
       test_ac12_validation_invalidate_clears_norun
  AC13 test_ac13_cycle_none_clears_every_cycle_scoped_to_run_and_workflow
  AC14 test_ac14_reroute_entry_clears_stale_cycle2_sentinel

New symbols (``resume_sentinel_glob``, the ``run_id``/``for_cycle`` keywords, the
new events) are reached lazily inside the tests, so the module collects and each
AC fails at assert time on its own. Only the LLM subprocess boundary and the lint
subprocess boundary (``bounded_run``) are stubbed; every unit under test is real.
No singleton-resource or timing fixtures are used (workflows.md 1i).
"""
from __future__ import annotations

import fnmatch
import hashlib
import json
import types
from pathlib import Path
from unittest.mock import patch

import pytest

from bytedigger_engine import telemetry_ctx
from bytedigger_engine.contracts import StepContract, StepResult, WorkflowContext, WorkflowDefinition
from bytedigger_engine.engine import WorkflowEngine
from bytedigger_engine.event_log import EventLog
from bytedigger_engine.lib.resume_keying import resume_sentinel_name
from bytedigger_engine.workflows import phase_45_spec, phase_6_review

RUN_A = "aaaaaaaaaaaa"
RUN_B = "bbbbbbbbbbbb"
SHIP_SIDECAR = ".build-spec.ship.json"

FINDINGS = [{
    "id": "F1", "type": "gap", "evidence": "e",
    "required_action": "MARKER_BD92_FINDING_ACTION",
}]
REVISE_RAW = (
    "## Verdict\nREVISE\n\n## Findings (structured)\n```json\n"
    + json.dumps(FINDINGS)
    + "\n```\n\n## Findings\n- fix it\n"
)


@pytest.fixture(autouse=True)
def _isolated_run_ctx(monkeypatch):
    monkeypatch.delenv("HAL_SPEC_DELTA_RETRY", raising=False)
    telemetry_ctx.clear_current_run()
    yield
    telemetry_ctx.clear_current_run()


# --- helpers -------------------------------------------------------------------


def _wf_ctx(scratch: Path, **extra) -> WorkflowContext:
    scratch.mkdir(parents=True, exist_ok=True)
    return WorkflowContext(
        tenant_id="t", scope=None, db_path=None,
        org_config={"scratchpad_dir": str(scratch), **extra},
        question="bd92", session_id="s", persona="p", framework=None, domain=None,
    )


def _set_run(tmp_path: Path, run_id: str, cycle: int = 1) -> EventLog:
    log = EventLog(path=tmp_path / "events.jsonl")
    telemetry_ctx.set_current_run(
        event_log=log, run_id=run_id, step_name="bd92", phase="phase_45_spec", cycle=cycle,
    )
    return log


def _events(log: EventLog, event_type: str) -> list[dict]:
    return [e for e in log.read_all() if e["event_type"] == event_type]


def _write_thread(scratch: Path, *, run_id, cycle: int, with_run_key: bool = True) -> None:
    from bytedigger_engine.findings_sidecar import SIDECAR_RELNAME

    scratch.mkdir(parents=True, exist_ok=True)
    payload: dict = {"structured_findings": FINDINGS, "cycle": cycle}
    if with_run_key:
        payload["run_id"] = run_id
    (scratch / SIDECAR_RELNAME).write_text(json.dumps(payload), encoding="utf-8")


def _write_spec(scratch: Path, text: str = "## Context\nbd92 spec body\n") -> Path:
    spec = scratch / phase_45_spec.SPEC_DOC_RELPATH
    spec.parent.mkdir(parents=True, exist_ok=True)
    spec.write_text(text, encoding="utf-8")
    return spec


def _gate_prev(scratch: Path, verdict: str, cycle: int = 1, **extra) -> StepResult:
    spec = _write_spec(scratch)
    review = scratch / "specs" / "review.md"
    review.write_text("review\n", encoding="utf-8")
    return StepResult(
        status="ok",
        data={"verdict": verdict, "review_path": str(review), "spec_path": str(spec),
              "cycle": cycle, "review_raw": REVISE_RAW if verdict != "SHIP" else "## Verdict\nSHIP\n",
              **extra},
        duration_ms=0, step_name="write_review_doc",
    )


# --- AC1 / AC2: phase 6 review doc is unlinked before the reviewer runs --------


def _p6_setup(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("HAL_RUNNER_BACKEND", "claude-subprocess")
    monkeypatch.setenv("HAL_RUNNER_BACKEND_JUDGE", "claude-subprocess")
    scratch = tmp_path / "scratch"
    reviews = scratch / "reviews"
    reviews.mkdir(parents=True)
    doc = reviews / "build-review.md"
    ctx = types.SimpleNamespace(org_config={"scratchpad_dir": str(scratch), "complexity": "FEATURE"})
    prev = StepResult(status="ok", data={
        "doc_path": str(doc), "spec_path": str(scratch / "spec.md"),
        "red_log_path": "red.log", "green_log_path": "green.log", "prompt": "review this",
    }, duration_ms=0, step_name="build_review_prompt")
    return ctx, prev, doc


def test_ac1_stale_review_doc_is_gone_and_fresh_stdout_is_written(tmp_path, monkeypatch):
    ctx, prev, doc = _p6_setup(tmp_path, monkeypatch)
    doc.write_text("STALE_BD92_CYCLE1_REVIEW\n## Aggregated Findings\nold\n", encoding="utf-8")
    seen: dict = {}

    def _fake_llm(**kwargs):
        seen["stale_present_at_call"] = doc.exists()
        # the reviewer writes neither the composite nor the doc; stdout only
        return StepResult(
            status="ok",
            data={**kwargs["extra_data"], "raw_response": "FRESH_BD92_STDOUT_REVIEW\nVERDICT: PASS\n"},
            duration_ms=0, step_name="invoke_review_llm",
        )

    monkeypatch.setattr(phase_6_review, "invoke_llm_subprocess", _fake_llm)
    invoked = phase_6_review._invoke_review_llm(ctx, prev)
    assert invoked.status == "ok", invoked
    assert seen["stale_present_at_call"] is False, "stale build-review.md must be unlinked before the reviewer runs"
    assert not doc.exists(), "stale build-review.md still on disk after the invoke step"

    written = phase_6_review._write_review_artifact(ctx, invoked)
    assert written.status == "ok", f"{written.error_code}: {written.error}"
    content = doc.read_text(encoding="utf-8")
    assert "FRESH_BD92_STDOUT_REVIEW" in content
    assert "STALE_BD92_CYCLE1_REVIEW" not in content


def test_ac2_review_doc_unlink_is_attempted_and_oserror_does_not_fail_the_step(tmp_path, monkeypatch):
    ctx, prev, doc = _p6_setup(tmp_path, monkeypatch)
    doc.write_text("stale\n", encoding="utf-8")
    attempts: list[str] = []
    real_unlink = Path.unlink

    def _unlink(self, missing_ok=False):
        if self.name == "build-review.md":
            attempts.append(str(self))
            raise OSError("simulated unlink failure")
        return real_unlink(self, missing_ok=missing_ok)

    called: list[int] = []

    def _fake_llm(**kwargs):
        called.append(1)
        return StepResult(status="ok", data={"raw_response": "x"}, duration_ms=0, step_name="invoke_review_llm")

    monkeypatch.setattr(phase_6_review, "invoke_llm_subprocess", _fake_llm)
    monkeypatch.setattr(Path, "unlink", _unlink)
    result = phase_6_review._invoke_review_llm(ctx, prev)
    assert attempts, "the review doc unlink must be ATTEMPTED before the reviewer runs"
    assert result.status == "ok", "an OSError from the unlink must not fail the step"
    assert called, "the reviewer must still be invoked after a failed unlink"


# --- AC3: load_findings_thread is keyed by (run_id, producing cycle) ------------


def test_ac3_load_rejects_other_run(tmp_path):
    from bytedigger_engine.findings_sidecar import load_findings_thread

    _write_thread(tmp_path, run_id=RUN_A, cycle=1)
    assert load_findings_thread(tmp_path, run_id=RUN_B, for_cycle=2) is None


def test_ac3_load_rejects_cycle_mismatch(tmp_path):
    from bytedigger_engine.findings_sidecar import load_findings_thread

    _write_thread(tmp_path, run_id=RUN_A, cycle=1)
    assert load_findings_thread(tmp_path, run_id=RUN_A, for_cycle=3) is None, "cycle N-2 thread"
    assert load_findings_thread(tmp_path, run_id=RUN_A, for_cycle=1) is None, "future thread"


def test_ac3_load_rejects_legacy_payload_without_run_id(tmp_path):
    from bytedigger_engine.findings_sidecar import load_findings_thread

    _write_thread(tmp_path, run_id=None, cycle=1, with_run_key=False)
    assert load_findings_thread(tmp_path, run_id=RUN_A, for_cycle=2) is None
    assert load_findings_thread(tmp_path, run_id=None, for_cycle=2) is None


def test_ac3_load_returns_list_for_same_run_previous_cycle(tmp_path):
    from bytedigger_engine.findings_sidecar import SIDECAR_RELNAME, load_findings_thread, persist_findings_thread

    _write_thread(tmp_path, run_id=RUN_A, cycle=1)
    assert load_findings_thread(tmp_path, run_id=RUN_A, for_cycle=2) == FINDINGS

    other = tmp_path / "rt"
    other.mkdir()
    persist_findings_thread(other, FINDINGS, cycle=2, run_id=RUN_A)
    on_disk = json.loads((other / SIDECAR_RELNAME).read_text(encoding="utf-8"))
    assert on_disk["run_id"] == RUN_A and on_disk["cycle"] == 2
    assert load_findings_thread(other, run_id=RUN_A, for_cycle=3) == FINDINGS
    assert load_findings_thread(other, run_id=RUN_B, for_cycle=3) is None


# --- AC4 / AC5: _build_spec_prompt --------------------------------------------


def test_ac4_prompt_ignores_other_run_thread_and_emits_rejected_event(tmp_path):
    scratch = tmp_path / "other"
    _write_spec(scratch)
    _write_thread(scratch, run_id=RUN_A, cycle=1)
    log = _set_run(tmp_path, RUN_B, cycle=2)
    prev = StepResult(status="ok", data={"cycle": 2}, duration_ms=0, step_name="gate_on_review")
    r = phase_45_spec._build_spec_prompt(_wf_ctx(scratch), prev)
    assert "MARKER_BD92_FINDING_ACTION" not in r.data["prompt"], "another run's thread leaked into the prompt"
    rejected = _events(log, "spec_findings_thread_rejected")
    assert len(rejected) == 1, rejected
    assert rejected[0]["payload"]["reason"] == "run_mismatch"
    assert rejected[0]["payload"]["stored_cycle"] == 1 and rejected[0]["payload"]["cycle"] == 2

    # positive control: same run, producing cycle 1 -> applied
    same = tmp_path / "same"
    _write_spec(same)
    _write_thread(same, run_id=RUN_B, cycle=1)
    r2 = phase_45_spec._build_spec_prompt(_wf_ctx(same), prev)
    assert "MARKER_BD92_FINDING_ACTION" in r2.data["prompt"]


def test_ac5_gate_retry_does_not_apply_cycle1_thread(tmp_path):
    scratch = tmp_path / "gate"
    _write_spec(scratch)
    _write_thread(scratch, run_id=RUN_B, cycle=2)
    _set_run(tmp_path, RUN_B, cycle=3)
    prev = StepResult(
        status="ok",
        data={"cycle": 3, "findings": "spec_cite_lint: GATE_BD92_GHOST", "retry_source": "spec_gates"},
        duration_ms=0, step_name="detect_frozen_spec",
    )
    r = phase_45_spec._build_spec_prompt(_wf_ctx(scratch), prev)
    assert "GATE_BD92_GHOST" in r.data["prompt"]
    assert "MARKER_BD92_FINDING_ACTION" not in r.data["prompt"]


# --- AC6: REVISE persists run_id and cycle ------------------------------------


def test_ac6_revise_branch_persists_run_id_and_cycle(tmp_path):
    from bytedigger_engine.findings_sidecar import SIDECAR_RELNAME

    scratch = tmp_path / "s6"
    _set_run(tmp_path, RUN_B, cycle=1)
    r = phase_45_spec._gate_on_review(_wf_ctx(scratch), _gate_prev(scratch, "REVISE", cycle=1))
    assert r.recoverable is True, r
    on_disk = json.loads((scratch / SIDECAR_RELNAME).read_text(encoding="utf-8"))
    assert on_disk.get("run_id") == RUN_B
    assert on_disk.get("cycle") == 1
    assert on_disk["structured_findings"] == FINDINGS


# --- AC7-AC10: the ship sidecar means "the reviewer said SHIP" -----------------


class _FakeProc:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode, self.stdout, self.stderr = returncode, stdout, stderr


def test_ac7_lint_pass_does_not_write_ship_sidecar(tmp_path, monkeypatch):
    monkeypatch.setattr(phase_45_spec, "_emit_safe", lambda *a, **k: None)
    real_is_file = Path.is_file
    monkeypatch.setattr(
        phase_45_spec.Path, "is_file",
        lambda self: self.name in ("lint_spec.py",) or real_is_file(self),
    )
    spec = tmp_path / "spec.md"
    spec.write_text("## Context\nstub\n", encoding="utf-8")
    prev = StepResult(status="ok", data={"cycle": 1, "spec_path": str(spec)}, duration_ms=0, step_name="prev")
    with patch.object(phase_45_spec, "bounded_run", return_value=_FakeProc(0, "")):
        r = phase_45_spec._verify_spec_lint(_wf_ctx(tmp_path / "s"), prev)
    assert r.status == "ok" and r.data.get("spec_lint_findings") == [], r
    assert not (tmp_path / SHIP_SIDECAR).exists(), "lint pass must not claim the spec shipped"


def test_ac8_ship_verdict_writes_verdict_sidecar(tmp_path):
    scratch = tmp_path / "s8"
    _set_run(tmp_path, RUN_B, cycle=1)
    prev = _gate_prev(scratch, "SHIP")
    r = phase_45_spec._gate_on_review(_wf_ctx(scratch), prev)
    assert r.status == "ok", r
    sidecar = Path(prev.data["spec_path"]).parent / SHIP_SIDECAR
    assert sidecar.is_file(), "a SHIP verdict must write the ship sidecar"
    data = json.loads(sidecar.read_text(encoding="utf-8"))
    spec_text = Path(prev.data["spec_path"]).read_text(encoding="utf-8")
    assert data.get("verdict") == "SHIP"
    assert data.get("spec_sha") == hashlib.sha256(spec_text.encode("utf-8")).hexdigest()
    assert data.get("run_id") == RUN_B


def test_ac8_direct_write_ship_sidecar_records_ship_verdict(tmp_path):
    """bd150 calls _write_ship_sidecar(path) directly: signature stays, verdict is always SHIP."""
    spec = _write_spec(tmp_path / "d8")
    phase_45_spec._write_ship_sidecar(str(spec))
    data = json.loads((spec.parent / SHIP_SIDECAR).read_text(encoding="utf-8"))
    assert data.get("verdict") == "SHIP"


def _plant_ship_sidecar(spec: Path) -> Path:
    sc = spec.parent / SHIP_SIDECAR
    sc.write_text(json.dumps({
        "spec_sha": hashlib.sha256(spec.read_text(encoding="utf-8").encode()).hexdigest(),
        "run_id": RUN_A, "verdict": "SHIP",
    }), encoding="utf-8")
    return sc


def test_ac9_revise_retry_unlinks_ship_sidecar(tmp_path):
    scratch = tmp_path / "s9"
    prev = _gate_prev(scratch, "REVISE")
    sc = _plant_ship_sidecar(Path(prev.data["spec_path"]))
    r = phase_45_spec._gate_on_review(_wf_ctx(scratch), prev)
    assert r.recoverable is True, r
    assert not sc.exists(), "a spec the reviewer sent back must not remain a prior shipped base"


def test_ac9_revise_terminal_unlinks_ship_sidecar(tmp_path):
    scratch = tmp_path / "s9t"
    prev = _gate_prev(scratch, "REVISE", cycle=2, gate_attempts={"spec_review": 1})
    sc = _plant_ship_sidecar(Path(prev.data["spec_path"]))
    r = phase_45_spec._gate_on_review(_wf_ctx(scratch), prev)
    assert r.status == "error" and r.error_code == "E_REVIEW_FAILED", r
    assert not sc.exists()


def test_ac10_prior_base_requires_ship_verdict(tmp_path):
    log = _set_run(tmp_path, RUN_B)
    spec = _write_spec(tmp_path / "s10")
    sha = hashlib.sha256(spec.read_text(encoding="utf-8").encode()).hexdigest()
    sc = spec.parent / SHIP_SIDECAR
    sc.write_text(json.dumps({"spec_sha": sha, "run_id": RUN_A}), encoding="utf-8")  # legacy, no verdict
    block, records = phase_45_spec._prior_ship_base_inline(str(spec))
    assert (block, records) == ("", [])
    assert len(_events(log, "spec_prior_base_unverified")) == 1

    sc.write_text(json.dumps({"spec_sha": sha, "run_id": RUN_A, "verdict": "SHIP"}), encoding="utf-8")
    block, records = phase_45_spec._prior_ship_base_inline(str(spec))
    assert "PRIOR SHIP-SPEC BASE" in block and records


# --- AC11: one name/glob source -------------------------------------------------


def test_ac11_every_sentinel_name_matches_its_glob():
    from bytedigger_engine.lib.resume_keying import resume_sentinel_glob

    for run_id in ("0123456789abcdef0123456789abcdef", None, ""):
        for wf in (None, "phase_45_spec"):
            for ctx_hash in (None, "0123456789abcdef0123"):
                for cycle in (1, 2):
                    name = resume_sentinel_name("step_x", cycle, run_id, ctx_hash, wf)
                    hashed = ctx_hash is not None
                    for c in (cycle, "*"):
                        pat = resume_sentinel_glob("step_x", c, run_id, wf, hashed=hashed)
                        assert fnmatch.fnmatchcase(name, pat), (name, pat)


def test_ac11_run_id_prefix_does_not_overmatch():
    from bytedigger_engine.lib.resume_keying import resume_sentinel_glob

    for hashed, h in ((False, None), (True, "0123456789abcdef")):
        name = resume_sentinel_name("step_x", 1, "R12", h, "wf")
        for c in (1, "*"):
            assert not fnmatch.fnmatchcase(name, resume_sentinel_glob("step_x", c, "R1", "wf", hashed=hashed))


# --- AC12 / AC13: invalidation ---------------------------------------------------


def _step(name: str, input_field: str | None = None):
    return types.SimpleNamespace(name=name, resume_sentinel=True, sentinel_input_field=input_field)


def _touch(sentinel_dir: Path, name: str) -> Path:
    sentinel_dir.mkdir(parents=True, exist_ok=True)
    p = sentinel_dir / name
    p.write_text("{}", encoding="utf-8")
    return p


def test_ac12_invalidate_cycle_sentinels_clears_norun_hashed(tmp_path):
    from bytedigger_engine.lib.step_sentinel import invalidate_cycle_sentinels

    ctx = types.SimpleNamespace(org_config={"scratchpad_dir": str(tmp_path)})
    f = _touch(tmp_path / "resume", resume_sentinel_name("inp", 1, None, "abcdef012345", "wf"))
    assert "_rnorun_h" in f.name
    removed = invalidate_cycle_sentinels(ctx, [_step("inp", "field")], 1, None, None, workflow_name="wf")
    assert f.name in removed
    assert not f.exists()


def test_ac12_validation_invalidate_clears_norun(tmp_path):
    from bytedigger_engine.lib.step_sentinel import invalidate_validation_sentinels_for_run

    base = "phase_5_validation_cycle__invoke_validation_llm_done_c1_rnorun"
    d = tmp_path / "resume"
    plain = _touch(d, base + ".json")
    hashed = _touch(d, base + "_habcdef012345.json")
    invalidate_validation_sentinels_for_run(tmp_path, None)
    assert not plain.exists() and not hashed.exists()


def test_ac13_cycle_none_clears_every_cycle_scoped_to_run_and_workflow(tmp_path):
    from bytedigger_engine.lib.step_sentinel import compute_ctx_hash, invalidate_cycle_sentinels

    ctx = types.SimpleNamespace(
        org_config={"scratchpad_dir": str(tmp_path), "task_description": "bd92 task"}, question="q",
    )
    ch = compute_ctx_hash(ctx)
    assert ch, "fixture: task_description must yield a ctx hash"
    d = tmp_path / "resume"
    targets, keep = [], []
    for c in (1, 2, 3):
        targets += [
            _touch(d, resume_sentinel_name("leg", c, RUN_A, None, "wf")),
            _touch(d, resume_sentinel_name("leg", c, RUN_A, ch, "wf")),
            _touch(d, resume_sentinel_name("inp", c, RUN_A, "feedfacecafe", "wf")),
            _touch(d, resume_sentinel_name("inp", c, RUN_A, None, "wf")),
        ]
        keep += [
            _touch(d, resume_sentinel_name("leg", c, RUN_B, None, "wf")),
            _touch(d, resume_sentinel_name("inp", c, RUN_B, "feedfacecafe", "wf")),
            _touch(d, resume_sentinel_name("leg", c, RUN_A, None, "wf_other")),
            _touch(d, resume_sentinel_name("inp", c, RUN_A, "feedfacecafe", "wf_other")),
        ]
    invalidate_cycle_sentinels(ctx, [_step("leg"), _step("inp", "field")], None, RUN_A, None, workflow_name="wf")
    assert [p.name for p in targets if p.exists()] == [], "all cycles of the run+workflow must be cleared"
    assert [p.name for p in keep if not p.exists()] == [], "another run's / workflow's sentinels must stay"


# --- AC14: reroute entry clears every cycle -----------------------------------


def test_ac14_reroute_entry_clears_stale_cycle2_sentinel(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    scratch = tmp_path / "scratch"
    d = scratch / "resume"
    _touch(d, resume_sentinel_name("s1", 1, "r1", None, "wf"))
    stale2 = _touch(d, resume_sentinel_name("s1", 2, "r1", None, "wf"))

    def _run(_ctx, _prev):
        return StepResult(status="ok", data=None, duration_ms=0, step_name="s1")

    log = EventLog(path=tmp_path / "ev.jsonl")
    eng = WorkflowEngine(event_log=log)
    eng.register("wf", WorkflowDefinition(
        name="wf", steps=[StepContract(name="s1", execute=_run, resume_sentinel=True)],
    ))
    ctx = WorkflowContext(
        tenant_id="t", scope=None, db_path=None,
        org_config={"phase_reroute": {"attempt": 1, "from_phase": "prior"}, "scratchpad_dir": str(scratch)},
        question="q", session_id="s", persona="p", framework=None, domain=None,
    )
    eng.execute("wf", ctx, run_id="r1")
    entry = _events(log, "phase_reroute_entry")
    assert len(entry) == 1, entry
    assert not stale2.exists(), "stale cycle-2 sentinel must be gone before the first step"
    assert entry[0]["payload"]["sentinels_invalidated"] == 2, "cycle-1 and cycle-2 sentinels both counted"
