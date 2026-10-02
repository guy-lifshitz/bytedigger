"""bd#206 RED: class-M carried model output is declared at the three carry sites (R3.2).

Spec: docs/decisions/2026-10-02-bd206-class-m-sites.md (r3, gate r3 APPROVED).
Gate files: 2026-10-02-bd206-gate-r1.md, -r2.md, -r3.md (adversarial edges folded in).

SPEC GAPS (resolved here, not guessed silently; none blocks the RED)
--------------------------------------------------------------------
G1. AC5 "prompt bytes identical across the first three": read as {declared sidecar,
    other-run sidecar, legacy sidecar}. The `[]` sidecar inlines "[]", so it cannot be
    byte-equal to the others and is not part of the comparison.
G2. AUTHORSHIP_SPEC heading `(R3.1, declared limit)` (gate r3 n-1) is not in AC8's text.
    Only the stale sentence "until those declarations exist" is asserted; the heading is not.
G3. AC1 "invoke_fix_llm_retry attestation": the retry dispatch is attested here by a backend
    that returns an error result on the second call (bd147 AC4 pattern). Assumes the
    chokepoint attests every dispatched call, errored or not (bd152 AC7 records refusals).
G4. AC2 evaluator "B: 0 fixes" is an evaluator that FAILs with `fixes_required: []`
    (a valid evaluator with no lines), not an errored one.
G5. The shape of a forged/forwarded `findings_blocks` element is only given as "block dicts"
    with `source_id` and `content` (spec 2.2); the AC3(b)/(d) fixtures use exactly those keys.
G6. Spec 2.3 says the provenance file records `run_id` from the current run; a run whose
    event log is None carries no attestation id at all, so "no id" subsumes "no event log".

Harness: a router backend registered as "claude-subprocess" (the backend conftest pins), the
real chokepoint, a real `telemetry_ctx` run with a REAL `EventLog` on tmp_path; every
attestation is read back from the log FILE (workflows.md 1l). Expected digests come from
`hashlib` (AUTHORSHIP_SPEC 0.2), never `attest.hash_text`. The backend registry and the
telemetry slot are singletons: pre-staged to a known baseline and restored by the autouse
fixture; nothing here depends on timing (workflows.md 1i). Thread-pool completion order of the
COMPLEX evaluators is never relied on: evaluator i is mapped to its log event through
`output_sha256 == sha256(evaluator_responses[i]["raw_response"])`.

`lib/findings_provenance.py` does not exist on the base; it is imported INSIDE test bodies so
the file collects and each test fails at assert time. No `sys.path` mutation, no conftest import.
Guard-type ACs (no block / not refused) carry a positive control that fails until GREEN, so a
stub that declares nothing cannot satisfy them.

AC -> test map
--------------
AC1  test_ac1_single_evaluator_fix_declares_satisfaction_block
     test_ac1_marker_retry_declares_same_block
AC2  test_ac2_complex_declares_one_block_per_contributing_evaluator
AC2b test_ac2b_partial_evaluator_ids_forward_nothing_and_do_not_refuse   (gate r3 m-3)
AC3  test_ac3a_no_fixes_fallback_text_is_not_declared
     test_ac3b_forged_findings_blocks_not_declared_not_refused
     test_ac3c_no_event_log_forwards_no_findings_blocks
     test_ac3d_decoy_review_driven_prompt_does_not_declare
AC4  test_ac4_provenance_file_carries_logged_id_and_telemetry_run
     test_ac4_failed_or_unattested_review_leaves_no_file[error|pin_mismatch]
     test_ac4_review_source_id_returns_none_on_every_failure
AC5  test_ac5_sidecar_carries_run_id_and_source_id_and_next_review_declares
     test_ac5_sidecar_negatives_declare_nothing
AC6  test_ac6_verifier_attestations_declare_finding_fields
     test_ac6_integer_line_is_declared_as_its_string
     test_ac6_without_provenance_no_injections_kwarg
AC7  test_ac7_later_reference_fails_earlier_passes
     test_ac7_self_reference_fails
     test_ac7_malformed_invocation_block_fails
     test_ac7_wrong_step_fails
     test_ac7_cross_run_reference_fails_interleaved
     test_ac7_output_sha256_form
     test_ac7_invocation_id_form
     test_ac7_form_violation_observes_but_form_valid_does_not
     test_ac7_none_run_never_resolves
     test_ac7_real_log_from_ac1_passes
AC8  test_ac8_authorship_spec_class_m_paragraph
     test_ac8_authorship_spec_r32_row
     test_ac8_inventory_key_and_lint
"""
from __future__ import annotations

import hashlib
import importlib
import json
import os
import re
import subprocess
import threading
from pathlib import Path

import pytest

from bytedigger_engine import llm_subprocess, telemetry_ctx
from bytedigger_engine.contracts import StepResult, WorkflowContext
from bytedigger_engine.event_log import EventLog

EVENT_TYPE = "model_invocation_attested"
RUN = "RUN-BD206"
SESSION = "bd206-session-NOT-THE-RUN-ID"
HEX32 = re.compile(r"^[0-9a-f]{32}$")
ID_R = "0123456789abcdef" * 2  # a valid 32-hex id for hand-written provenance
FIX_NO_MARKER_RAW = "did some work but forgot the completion marker"
UNVERIFIED_CANNOT_DECIDE = "UNVERIFIED:\nreason: cannot_decide\n"
REFUTED_RAW = "REFUTED:\nreason: fine\nrationale: the loop is bounded\n"
CAPS = ("manifest", "progress_since", "abort", "tool_allowlist", "warm_resume", "effort")


def sha256_of(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _p6():
    return importlib.import_module("bytedigger_engine.workflows.phase_6_review")


def _sv():
    return importlib.import_module("bytedigger_engine.lib.plugins.anti_hallucination.semantic_verifier")


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

_GIT_ENV = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}


def _git(repo: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t",
         "-c", "commit.gpgsign=false", *args],
        cwd=str(repo), check=True, capture_output=True, text=True, env=_GIT_ENV,
    )
    return out.stdout.strip()


class _Env:
    """A git repo holding a scratchpad (the bd147 fixture), for the fix chain."""

    def __init__(self, root: Path) -> None:
        self.repo = root / "repo"
        self.repo.mkdir(parents=True)
        self.scratch = self.repo / "scratch"
        _git(self.repo, "init", "-q")
        (self.repo / "a.txt").write_text("one\n", encoding="utf-8")
        _git(self.repo, "add", "a.txt")
        _git(self.repo, "commit", "-q", "-m", "base")
        self.scratch.mkdir()
        self.cfg: dict = {
            "scratchpad_dir": str(self.scratch),
            "model": "sonnet",
            "current_worktree_path": str(self.repo),
            "git_cwd": str(self.repo),
        }

    def ctx(self, **extra) -> WorkflowContext:
        return _workflow_ctx({**self.cfg, **extra})


def _workflow_ctx(cfg: dict) -> WorkflowContext:
    return WorkflowContext(
        tenant_id="hal", scope=None, db_path=None, org_config=cfg,
        question="bd206 feature request", session_id=SESSION,
        persona="hal", framework=None, domain=None,
    )


def _bare_ctx(root: Path, **extra) -> WorkflowContext:
    scratch = root / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    return _workflow_ctx({"scratchpad_dir": str(scratch), **extra})


class _Router:
    """Backend merging `extra_data` like the real one. `script[step_name]` is a list of
    responses consumed in call order: a str (ok raw_response), a dict (ok data), or a
    StepResult (returned as-is). Anything beyond the script returns a default ok answer."""

    def __init__(self, script: "dict | None" = None, default: str = "done\n") -> None:
        self.script = {k: list(v) for k, v in (script or {}).items()}
        self.default = default
        self.counts: dict = {}
        self.calls: list = []
        self._lock = threading.Lock()

    def __call__(self, **kwargs) -> StepResult:
        step = kwargs.get("step_name", "?")
        with self._lock:
            n = self.counts.get(step, 0)
            self.counts[step] = n + 1
            self.calls.append(dict(kwargs))
            queue = self.script.get(step, [])
            resp = queue[n] if n < len(queue) else self.default
        if isinstance(resp, StepResult):
            return resp
        data = {"raw_response": resp, "worker_written_paths": [],
                "manifest_source": "harness_tool_record"} if isinstance(resp, str) else dict(resp)
        data.setdefault("worker_written_paths", [])
        data.setdefault("manifest_source", "harness_tool_record")
        data.update(kwargs.get("extra_data") or {})
        return StepResult(status="ok", data=data, duration_ms=0, step_name=step)


def _register(router) -> None:
    llm_subprocess.register_backend(
        "claude-subprocess", router, manifest_source="harness_tool_record",
        capabilities=frozenset(CAPS), overwrite=True,
    )


def _set_run(log, *, step: str = "write_satisfaction_doc", cycle: int = 1) -> None:
    telemetry_ctx.set_current_run(
        event_log=log, run_id=RUN, step_name=step,
        phase="phase_6_review", tier=None, cycle=cycle,
    )


def _attests(log: EventLog, step: "str | None" = None) -> list:
    out = [e["payload"] for e in log.read_all() if e["event_type"] == EVENT_TYPE]
    return [p for p in out if step is None or p.get("step_name") == step]


def _class_m(payload: dict) -> list:
    return [b for b in (payload.get("injections") or [])
            if str(b.get("source_id", "")).startswith("invocation:")]


@pytest.fixture(autouse=True)
def _bd206_isolation(monkeypatch):
    monkeypatch.setattr(llm_subprocess, "emit_resolver_resolved", lambda *a, **kw: None)
    llm_subprocess.reset_backends()
    telemetry_ctx.clear_current_run()
    yield
    telemetry_ctx.clear_current_run()
    llm_subprocess.reset_backends()


# ---------------------------------------------------------------------------
# M1 fixtures: satisfaction -> fix
# ---------------------------------------------------------------------------

def _sat_raw(fixes: list, note: str = "") -> str:
    return "\n".join([
        "## Evaluation", "SCORE: 50", "VERDICT: FAIL", note, "",
        "## satisfaction-output (structured)\n```json\n"
        + json.dumps({"satisfied": False, "fixes_required": fixes}) + "\n```",
    ])


def _lines(fixes: list) -> str:
    return "\n".join(f"- {f['file']}: {f['issue']}" for f in fixes)


FIXES_SINGLE = [{"file": "a.py", "issue": "BD206 issue one"}, {"file": "b.py", "issue": "BD206 issue two"}]
FIXES_A = [{"file": "a1.py", "issue": "A issue one"}, {"file": "a2.py", "issue": "A issue two"}]
FIXES_C = [{"file": "c1.py", "issue": "C issue one"}]


class _Sat:
    """One driven satisfaction step: env, ctx, log, router, the invoke result and the
    write_satisfaction_doc result (the fix-loop retry data)."""


def _drive_sat(root: Path, raws: list, *, complex_: bool = False, with_log: bool = True,
               fix_script: "dict | None" = None) -> _Sat:
    s = _Sat()
    s.env = _Env(root)
    s.log = EventLog(root / "events.jsonl")
    script = {"invoke_satisfaction_llm": raws}
    script.update(fix_script or {})
    s.router = _Router(script)
    _register(s.router)
    _set_run(s.log if with_log else None)
    s.ctx = s.env.ctx(satisfaction_threshold=85, satisfaction_model="opus", **({"complexity": "COMPLEX"} if complex_ else {}))
    reviews = s.env.scratch / "reviews"
    reviews.mkdir(parents=True, exist_ok=True)
    review_doc = reviews / "build-review.md"
    review_doc.write_text("# Review\nVERDICT: PASS\n", encoding="utf-8")
    (reviews / "build-review-fix.md").write_text("# verified findings\n", encoding="utf-8")
    prev = StepResult(
        status="ok", duration_ms=0, step_name="build_satisfaction_prompt",
        data={"prompt": "BD206 SATISFACTION PROMPT",
              "doc_path": str(reviews / "build-satisfaction.md"),
              "spec_path": str(s.env.scratch / "specs" / "build-spec.md"),
              "review_doc_path": str(review_doc),
              "fix_doc_path": str(reviews / "build-fix.md"),
              "review_verdict": "PASS"},
    )
    p6 = _p6()
    s.sat = p6._invoke_satisfaction_llm(s.ctx, prev)
    assert s.sat.status == "ok", f"fixture precondition: satisfaction dispatch ok, got {s.sat.error_code!r}"
    s.r = p6._write_satisfaction_doc(s.ctx, s.sat)
    assert s.r.recoverable is True and s.r.data.get("fix_loop_source") == "satisfaction", (
        f"fixture precondition: satisfaction FAIL must enter the fix loop, got {s.r.error_code!r}")
    return s


def _fix_from(s: _Sat, data: dict):
    """build_fix_prompt (as the engine re-enters it, with a dict) then the real fix dispatch."""
    p6 = _p6()
    built = p6._build_fix_prompt(s.ctx, dict(data))
    assert built.status == "ok", f"fixture precondition: fix prompt ok, got {built.error_code!r}"
    return built, p6._invoke_fix_llm(s.ctx, built)


def _sat_event_id(s: _Sat) -> str:
    evs = _attests(s.log, "invoke_satisfaction_llm")
    assert len(evs) == 1, f"fixture precondition: one satisfaction attestation, got {len(evs)}"
    return evs[0]["invocation_id"]


# ---------------------------------------------------------------------------
# AC1 - M1 single, end-to-end
# ---------------------------------------------------------------------------

def test_ac1_single_evaluator_fix_declares_satisfaction_block(tmp_path) -> None:
    """AC1: the fix attestation declares ONE class-M block naming the logged satisfaction id,
    digest = sha256 of the two rendered lines joined by newline; the prompt is unchanged."""
    s = _drive_sat(tmp_path, [_sat_raw(FIXES_SINGLE)])
    built, fixed = _fix_from(s, s.r.data)
    assert fixed.status == "ok", f"fixture precondition: fix dispatch ok, got {fixed.error_code!r}"
    sat_id = _sat_event_id(s)
    fix_events = _attests(s.log, "invoke_fix_llm")
    assert len(fix_events) == 1, f"fixture precondition: one fix attestation, got {len(fix_events)}"
    expected = _lines(FIXES_SINGLE)
    assert _class_m(fix_events[0]) == [{
        "source_id": "invocation:invoke_satisfaction_llm:" + sat_id,
        "sha256": sha256_of(expected),
    }], f"AC1: fix event must declare the satisfaction block; got {fix_events[0].get('injections')!r}"
    assert expected in built.data["prompt"]
    no_log_data = {k: v for k, v in s.r.data.items() if k != "findings_blocks"}
    assert _p6()._build_fix_prompt(s.ctx, no_log_data).data["prompt"] == built.data["prompt"], (
        "AC1: declaring the block must not change the fix prompt bytes")


def test_ac1_marker_retry_declares_same_block(tmp_path) -> None:
    """AC1 (retry): a first fix answer without the marker -> the invoke_fix_llm_retry
    attestation carries the same class-M block (G3: the retry's backend answer is an error)."""
    stop = StepResult(status="error", data=None, duration_ms=0, step_name="invoke_fix_llm_retry",
                      error="bd206 spy retry stop", error_code="E_SPY_STOP")
    s = _drive_sat(tmp_path, [_sat_raw(FIXES_SINGLE)], fix_script={
        "invoke_fix_llm": [FIX_NO_MARKER_RAW], "invoke_fix_llm_retry": [stop]})
    p6 = _p6()
    built = p6._build_fix_prompt(s.ctx, dict(s.r.data))
    first = p6._invoke_fix_llm(s.ctx, built)
    assert first.status == "ok"
    p6._write_fix_artifact(s.ctx, first)
    first_ev = _attests(s.log, "invoke_fix_llm")
    retry_ev = _attests(s.log, "invoke_fix_llm_retry")
    assert len(first_ev) == 1 and len(retry_ev) == 1, (
        f"fixture precondition: first dispatch + retry attested, got {len(first_ev)}/{len(retry_ev)}")
    expected = [{"source_id": "invocation:invoke_satisfaction_llm:" + _sat_event_id(s),
                 "sha256": sha256_of(_lines(FIXES_SINGLE))}]
    assert _class_m(retry_ev[0]) == expected, f"AC1 retry: got {retry_ev[0].get('injections')!r}"
    assert _class_m(first_ev[0]) == expected


# ---------------------------------------------------------------------------
# AC2 - M1 COMPLEX
# ---------------------------------------------------------------------------

def _complex_raws():
    raw_a = _sat_raw(FIXES_A, "evaluator A")
    raw_b = _sat_raw([], "evaluator B")
    raw_c = _sat_raw(FIXES_C, "evaluator C")
    lines_of = {raw_a: _lines(FIXES_A), raw_b: "", raw_c: _lines(FIXES_C)}
    return [raw_a, raw_b, raw_c], lines_of


def _expected_groups(s: _Sat, lines_of: dict) -> list:
    """[(source_id, lines)] per evaluator WITH fixes, in evaluator index order, ids mapped
    through output_sha256 (thread-pool completion order is never relied on)."""
    ids_by_out = {p["output_sha256"]: p["invocation_id"] for p in _attests(s.log, "invoke_satisfaction_llm")}
    out = []
    for entry in s.sat.data["evaluator_responses"]:
        raw = entry["raw_response"]
        if lines_of[raw]:
            out.append(("invocation:invoke_satisfaction_llm:" + ids_by_out[sha256_of(raw)], lines_of[raw]))
    return out


def test_ac2_complex_declares_one_block_per_contributing_evaluator(tmp_path) -> None:
    """AC2: A(2 fixes), B(0), C(1) -> two blocks, index order, each naming its own
    evaluator's logged id and covering only its own lines; evaluator_responses carry ids."""
    raws, lines_of = _complex_raws()
    s = _drive_sat(tmp_path, raws, complex_=True)
    evs = _attests(s.log, "invoke_satisfaction_llm")
    assert len(evs) == 3, f"fixture precondition: three evaluator attestations, got {len(evs)}"
    ids_by_out = {p["output_sha256"]: p["invocation_id"] for p in evs}
    assert [e.get("invocation_id") for e in s.sat.data["evaluator_responses"]] == [
        ids_by_out[sha256_of(e["raw_response"])] for e in s.sat.data["evaluator_responses"]
    ], "AC2: evaluator_responses[i]['invocation_id'] must equal the i-th evaluator's logged id"
    groups = _expected_groups(s, lines_of)
    assert len(groups) == 2
    built, fixed = _fix_from(s, s.r.data)
    assert fixed.status == "ok"
    fix_ev = _attests(s.log, "invoke_fix_llm")
    assert len(fix_ev) == 1
    assert _class_m(fix_ev[0]) == [{"source_id": sid, "sha256": sha256_of(text)} for sid, text in groups], (
        f"AC2: one block per contributing evaluator in index order; got {fix_ev[0].get('injections')!r}")
    assert s.r.data["findings"] == "\n".join(text for _, text in groups), (
        "AC2: the full findings string stays the in-order join of the groups")


def test_ac2b_partial_evaluator_ids_forward_nothing_and_do_not_refuse(tmp_path) -> None:
    """AC2b (gate r3 m-3): if one group with fixes has no valid id, no findings_blocks is
    forwarded, no class-M block is declared and the dispatch is not refused."""
    raws, lines_of = _complex_raws()
    s = _drive_sat(tmp_path, raws, complex_=True)
    groups = _expected_groups(s, lines_of)
    # Positive control: with all ids present the blocks are forwarded and declared.
    control_blocks = s.r.data.get("findings_blocks")
    assert isinstance(control_blocks, list) and len(control_blocks) == 2, (
        f"AC2b control: complete ids must forward two findings_blocks; got {control_blocks!r}")
    _, fixed_control = _fix_from(s, s.r.data)
    assert fixed_control.status == "ok"
    assert len(_class_m(_attests(s.log, "invoke_fix_llm")[0])) == 2
    # Strip the id of the evaluator whose group is C's (one with fixes).
    er = [dict(e) for e in s.sat.data["evaluator_responses"]]
    c_idx = next(i for i, e in enumerate(er) if lines_of[e["raw_response"]] == _lines(FIXES_C))
    er[c_idx]["invocation_id"] = None
    partial = StepResult(status="ok", duration_ms=0, step_name="invoke_satisfaction_llm",
                         data={**s.sat.data, "evaluator_responses": er})
    r2 = _p6()._write_satisfaction_doc(s.ctx, partial)
    assert r2.recoverable is True and "findings_blocks" not in r2.data, (
        f"AC2b: a partial id set must forward no findings_blocks; got {r2.data.get('findings_blocks')!r}")
    _, fixed = _fix_from(s, r2.data)
    assert fixed.status == "ok" and fixed.error_code != "E_INJECT_UNATTRIBUTED", (
        f"AC2b: the dispatch must not be refused; got {fixed.error_code!r}")
    second = _attests(s.log, "invoke_fix_llm")[1]
    assert _class_m(second) == [], f"AC2b: no class-M block; got {second.get('injections')!r}"
    assert len(groups) == 2


# ---------------------------------------------------------------------------
# AC3 - M1 guards (each with a positive control that is red until GREEN)
# ---------------------------------------------------------------------------

def test_ac3a_no_fixes_fallback_text_is_not_declared(tmp_path) -> None:
    """AC3(a): a FAIL without fixes inlines the engine's fallback text (class E): no block.
    Control: the same chain with fixes declares one."""
    control = _drive_sat(tmp_path / "with_fixes", [_sat_raw(FIXES_SINGLE)])
    _, fixed_c = _fix_from(control, control.r.data)
    assert fixed_c.status == "ok"
    assert len(_class_m(_attests(control.log, "invoke_fix_llm")[0])) == 1, (
        "AC3(a) control: the with-fixes chain must declare its block")
    s = _drive_sat(tmp_path / "no_fixes", [_sat_raw([])])
    assert "findings_blocks" not in s.r.data, "AC3(a): fallback text must not be forwarded as a block"
    built, fixed = _fix_from(s, s.r.data)
    assert fixed.status == "ok"
    assert _class_m(_attests(s.log, "invoke_fix_llm")[0]) == []
    assert s.r.data["findings"] in built.data["prompt"]


def test_ac3b_forged_findings_blocks_not_declared_not_refused(tmp_path) -> None:
    """AC3(b): findings_blocks whose joined content differs from the inlined findings -> no
    class-M block and the dispatch is not refused. Control: the genuine blocks are declared."""
    s = _drive_sat(tmp_path, [_sat_raw(FIXES_SINGLE)])
    sat_id = _sat_event_id(s)
    _, fixed_c = _fix_from(s, s.r.data)
    assert fixed_c.status == "ok"
    assert len(_class_m(_attests(s.log, "invoke_fix_llm")[0])) == 1, (
        "AC3(b) control: the genuine blocks must be declared")
    forged = {**s.r.data, "findings_blocks": [
        {"source_id": "invocation:invoke_satisfaction_llm:" + sat_id, "content": "- forged: not what is inlined"}]}
    _, fixed = _fix_from(s, forged)
    assert fixed.status == "ok" and fixed.error_code != "E_INJECT_UNATTRIBUTED", (
        f"AC3(b): a stale/forged record must not refuse the dispatch; got {fixed.error_code!r}")
    assert _class_m(_attests(s.log, "invoke_fix_llm")[1]) == []


def test_ac3c_no_event_log_forwards_no_findings_blocks(tmp_path) -> None:
    """AC3(c): with no event log there is no attested id, so no findings_blocks key.
    Control: the identical chain with a log forwards the key."""
    with_log = _drive_sat(tmp_path / "log", [_sat_raw(FIXES_SINGLE)])
    assert isinstance(with_log.r.data.get("findings_blocks"), list), (
        "AC3(c) control: with an event log the blocks are forwarded")
    telemetry_ctx.clear_current_run()
    llm_subprocess.reset_backends()
    no_log = _drive_sat(tmp_path / "nolog", [_sat_raw(FIXES_SINGLE)], with_log=False)
    assert "findings_blocks" not in no_log.r.data, (
        f"AC3(c): no event log -> no findings_blocks; got {no_log.r.data.get('findings_blocks')!r}")


def test_ac3d_decoy_review_driven_prompt_does_not_declare(tmp_path) -> None:
    """AC3(d): a review-driven fix prompt (fix_loop_source absent) whose prev.data carries
    findings_blocks and a matching findings -> no class-M block, not refused with
    E_INJECT_UNATTRIBUTED. Control: with fix_loop_source=satisfaction the block is declared."""
    s = _drive_sat(tmp_path, [_sat_raw(FIXES_SINGLE)])
    sat_id = _sat_event_id(s)
    text = _lines(FIXES_SINGLE)
    base = {k: v for k, v in s.r.data.items() if k not in ("fix_loop_source", "findings_blocks", "findings")}
    block = {"source_id": "invocation:invoke_satisfaction_llm:" + sat_id, "content": text}
    _, fixed_c = _fix_from(s, {**base, "fix_loop_source": "satisfaction", "findings": text,
                               "findings_blocks": [block]})
    assert fixed_c.status == "ok"
    assert len(_class_m(_attests(s.log, "invoke_fix_llm")[0])) == 1, (
        "AC3(d) control: a satisfaction loop declares the matching block")
    _, fixed = _fix_from(s, {**base, "findings": text, "findings_blocks": [block]})
    assert fixed.status == "ok" and fixed.error_code != "E_INJECT_UNATTRIBUTED", (
        f"AC3(d): the decoy must not refuse the dispatch; got {fixed.error_code!r}")
    assert _class_m(_attests(s.log, "invoke_fix_llm")[1]) == [], (
        "AC3(d): without sat_loop the findings are not in the prompt, so nothing may be declared")


# ---------------------------------------------------------------------------
# AC4 - provenance writer / reader
# ---------------------------------------------------------------------------

def _review_prev(root: Path, doc: Path, prompt: str = "BD206 REVIEW PROMPT") -> StepResult:
    return StepResult(status="ok", duration_ms=0, step_name="build_review_prompt", data={
        "prompt": prompt, "doc_path": str(doc), "spec_path": str(root / "spec.md"),
        "red_log_path": str(root / "red.log"), "green_log_path": str(root / "green.log")})


def _prov_path(doc: Path) -> Path:
    return doc.parent / "review_invocation.json"


def _write_prov_json(doc: Path, **over) -> Path:
    body = {"step_name": "invoke_review_llm", "invocation_id": ID_R, "run_id": RUN,
            "review_doc_path": str(doc), **over}
    path = _prov_path(doc)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body), encoding="utf-8")
    return path


def test_ac4_provenance_file_carries_logged_id_and_telemetry_run(tmp_path) -> None:
    """AC4: the file exists with the logged review event's id and the telemetry run id
    (ctx.session_id is deliberately different)."""
    log = EventLog(tmp_path / "events.jsonl")
    _register(_Router())
    _set_run(log, step="invoke_review_llm")
    ctx = _bare_ctx(tmp_path)
    doc = tmp_path / "scratch" / "reviews" / "build-review.md"
    result = _p6()._invoke_review_llm(ctx, _review_prev(tmp_path, doc))
    assert result.status == "ok"
    evs = _attests(log, "invoke_review_llm")
    assert len(evs) == 1 and HEX32.match(evs[0]["invocation_id"]), f"fixture precondition: {evs!r}"
    path = _prov_path(doc)
    assert path.is_file(), "AC4: the provenance file must be written next to the review doc"
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "step_name": "invoke_review_llm", "invocation_id": evs[0]["invocation_id"],
        "run_id": RUN, "review_doc_path": str(doc)}
    assert ctx.session_id != RUN


@pytest.mark.parametrize("case", ["error", "pin_mismatch"])
def test_ac4_failed_or_unattested_review_leaves_no_file(tmp_path, case) -> None:
    """AC4: an error result (no id) and an error result WITH a stamped id (pin mismatch) write
    nothing, and remove a stale file that existed before (pre-staged deterministically)."""
    log = EventLog(tmp_path / "events.jsonl")
    if case == "error":
        resp = StepResult(status="error", data=None, duration_ms=0, step_name="invoke_review_llm",
                          error="bd206 backend error", error_code="E_SPY_STOP")
    else:
        resp = {"raw_response": "drifted answer", "observed_model": "claude-haiku-4-5-20251001"}
    _register(_Router({"invoke_review_llm": [resp]}))
    _set_run(log, step="invoke_review_llm")
    doc = tmp_path / "scratch" / "reviews" / "build-review.md"
    stale = _write_prov_json(doc, invocation_id="f" * 32)  # pre-staged stale provenance
    assert stale.is_file()
    result = _p6()._invoke_review_llm(_bare_ctx(tmp_path), _review_prev(tmp_path, doc))
    assert result.status == "error", f"fixture precondition: refused/errored review, got {result.status!r}"
    if case == "pin_mismatch":
        assert result.error_code == "E_MODEL_PIN_MISMATCH"
        assert (result.data or {}).get("invocation_id") == _attests(log, "invoke_review_llm")[0]["invocation_id"], (
            "fixture precondition: the refusal carries a stamped id")
    assert not stale.exists(), "AC4: a failed or unattested review must leave no provenance file"


def test_ac4_review_source_id_returns_none_on_every_failure(tmp_path) -> None:
    """AC4: review_source_id returns the id form on a good file and None on every failure,
    never raising."""
    fp = importlib.import_module("bytedigger_engine.lib.findings_provenance")
    doc = tmp_path / "reviews" / "build-review.md"
    doc.parent.mkdir(parents=True)
    fp.write_review_provenance(str(doc), RUN, ID_R)
    assert fp.review_source_id(str(doc), RUN) == "invocation:invoke_review_llm:" + ID_R
    assert json.loads(_prov_path(doc).read_text(encoding="utf-8")) == {
        "step_name": "invoke_review_llm", "invocation_id": ID_R, "run_id": RUN, "review_doc_path": str(doc)}
    assert fp.review_source_id(str(doc), "OTHER-RUN") is None
    assert fp.review_source_id(str(doc.parent / "other-review.md"), RUN) is None
    _write_prov_json(doc, invocation_id="not-hex")
    assert fp.review_source_id(str(doc), RUN) is None
    _write_prov_json(doc, invocation_id=ID_R.upper())
    assert fp.review_source_id(str(doc), RUN) is None
    _prov_path(doc).write_text("{not json", encoding="utf-8")
    assert fp.review_source_id(str(doc), RUN) is None
    _prov_path(doc).unlink()
    assert fp.review_source_id(str(doc), RUN) is None
    assert fp.review_source_id(None, RUN) is None and fp.review_source_id(str(doc), None) is None


# ---------------------------------------------------------------------------
# AC5 - M2 (review doc -> last_findings.json -> next review prompt)
# ---------------------------------------------------------------------------

PRIOR_FINDINGS = [
    {"id": "1", "severity": "HIGH", "path": "src/foo.py", "description": "BD206 prior finding one"},
    {"id": "2", "severity": "LOW", "path": "src/bar.py", "description": "BD206 prior finding two"},
]


def _review_doc_text(findings: list) -> str:
    return ("# Review\n\n## Findings (structured)\n```json\n"
            + json.dumps(findings, indent=2) + "\n```\n\nVERDICT: FAIL\n")


def test_ac5_sidecar_carries_run_id_and_source_id_and_next_review_declares(tmp_path) -> None:
    """AC5: sidecar written on satisfaction FAIL carries run_id + source_id (the review
    event's id form); the next review attestation declares a block naming it with the digest
    of the inlined json.dumps(structured_findings, indent=2)."""
    log = EventLog(tmp_path / "events.jsonl")
    _register(_Router({"invoke_review_llm": ["REVIEW ONE", "REVIEW TWO"]}))
    _set_run(log, step="invoke_review_llm")
    ctx = _bare_ctx(tmp_path, satisfaction_threshold=85)
    p6 = _p6()
    built1 = p6._build_review_prompt(ctx, None)
    assert built1.status == "ok"
    assert p6._invoke_review_llm(ctx, built1).status == "ok"
    review_ev = _attests(log, "invoke_review_llm")
    assert len(review_ev) == 1
    review_id = review_ev[0]["invocation_id"]
    doc = Path(built1.data["doc_path"])
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text(_review_doc_text(PRIOR_FINDINGS), encoding="utf-8")
    scratch = Path(ctx.org_config["scratchpad_dir"])
    sat_prev = StepResult(status="ok", duration_ms=0, step_name="invoke_satisfaction_llm", data={
        "raw_response": _sat_raw(FIXES_SINGLE), "doc_path": str(doc.parent / "build-satisfaction.md"),
        "spec_path": str(scratch / "specs" / "build-spec.md"), "review_doc_path": str(doc),
        "fix_doc_path": str(doc.parent / "build-fix.md"), "review_verdict": "PASS"})
    (doc.parent / "build-review-fix.md").write_text("# verified\n", encoding="utf-8")
    failed = p6._write_satisfaction_doc(ctx, sat_prev)
    assert failed.recoverable is True, f"fixture precondition: satisfaction FAIL, got {failed.error_code!r}"
    sidecar = json.loads((doc.parent / "last_findings.json").read_text(encoding="utf-8"))
    assert sidecar.get("run_id") == RUN and ctx.session_id != RUN, (
        f"AC5: sidecar run_id must be the telemetry run; got {sidecar.get('run_id')!r}")
    source_id = "invocation:invoke_review_llm:" + review_id
    assert sidecar.get("source_id") == source_id, f"AC5: sidecar source_id; got {sidecar.get('source_id')!r}"
    built2 = p6._build_review_prompt(ctx, None)
    inlined = json.dumps(PRIOR_FINDINGS, indent=2)
    assert inlined in built2.data["prompt"], "fixture precondition: prior findings inlined"
    assert p6._invoke_review_llm(ctx, built2).status == "ok"
    second = _attests(log, "invoke_review_llm")[1]
    assert {"source_id": source_id, "sha256": sha256_of(inlined)} in second["injections"], (
        f"AC5: next review must declare the prior-findings block; got {second['injections']!r}")


def _dispatch_review_with_sidecar(root: Path, sidecar: dict) -> tuple:
    """Fresh env + log; hand-written sidecar; real _build_review_prompt + _invoke_review_llm.
    Returns (prompt, review attestation payload)."""
    root.mkdir(parents=True, exist_ok=True)
    log = EventLog(root / "events.jsonl")
    _register(_Router())
    _set_run(log, step="invoke_review_llm")
    ctx = _bare_ctx(root)
    reviews = root / "scratch" / "reviews"
    reviews.mkdir(parents=True, exist_ok=True)
    (reviews / "last_findings.json").write_text(json.dumps(sidecar), encoding="utf-8")
    p6 = _p6()
    built = p6._build_review_prompt(ctx, None)
    assert built.status == "ok"
    assert p6._invoke_review_llm(ctx, built).status == "ok"
    return built.data["prompt"], _attests(log, "invoke_review_llm")[0]


def test_ac5_sidecar_negatives_declare_nothing(tmp_path) -> None:
    """AC5: other-run sidecar, legacy sidecar (no keys) and a same-run `[]` sidecar declare no
    class-M block; the prompt bytes equal the declared case's (G1)."""
    sid = "invocation:invoke_review_llm:" + ID_R
    base = {"attempt": 1, "score": 60, "threshold": 85, "review_doc_path": "x/build-review.md",
            "structured_findings": PRIOR_FINDINGS}
    prompt_pos, ev_pos = _dispatch_review_with_sidecar(
        tmp_path / "pos", {**base, "run_id": RUN, "source_id": sid})
    inlined = json.dumps(PRIOR_FINDINGS, indent=2)
    assert _class_m(ev_pos) == [{"source_id": sid, "sha256": sha256_of(inlined)}], (
        f"AC5 control: a same-run sidecar must declare its block; got {ev_pos['injections']!r}")
    prompt_other, ev_other = _dispatch_review_with_sidecar(
        tmp_path / "other", {**base, "run_id": "SOME-OTHER-RUN", "source_id": sid})
    prompt_legacy, ev_legacy = _dispatch_review_with_sidecar(tmp_path / "legacy", dict(base))
    _, ev_empty = _dispatch_review_with_sidecar(
        tmp_path / "empty", {**base, "structured_findings": [], "run_id": RUN, "source_id": sid})
    assert _class_m(ev_other) == [] and _class_m(ev_legacy) == [] and _class_m(ev_empty) == []
    assert prompt_pos == prompt_other == prompt_legacy, "AC5: declaring must not change prompt bytes"


# ---------------------------------------------------------------------------
# AC6 - M3 (semantic verifier)
# ---------------------------------------------------------------------------

REVIEW_DOC_ONE_FINDING = (
    "# Review\n\n## Aggregated Findings\n\n"
    "### SEVERITY: HIGH — Off by one\n"
    "> src/a.py:12: x = y + 1\n"
    "Claim: loop runs once too many\n"
)
FIELD_VALUES = ["src/a.py", "12", "x = y + 1", "loop runs once too many"]  # file, line, quote, claim


def _verify_run(root: Path, *, provenance: bool, script: "dict | None" = None):
    log = EventLog(root / "events.jsonl")
    router = _Router(script or {})
    _register(router)
    _set_run(log, step="verify_findings_semantic")
    ctx = _bare_ctx(root)
    doc = root / "scratch" / "reviews" / "build-review.md"
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text(REVIEW_DOC_ONE_FINDING, encoding="utf-8")
    if provenance:
        _write_prov_json(doc)
    prev = StepResult(status="ok", duration_ms=0, step_name="aggregate_review_findings",
                      data={"review_doc_path": str(doc), "verdict": "PASS"})
    return log, router, _sv().verify_findings_semantic(ctx, prev)


def test_ac6_verifier_attestations_declare_finding_fields(tmp_path) -> None:
    """AC6: with provenance present, BOTH the haiku call and the opus escalation declare a
    block per non-empty field, in file/line/quote/claim order, naming the review id."""
    log, _, result = _verify_run(tmp_path, provenance=True, script={
        "verify_findings_semantic": [UNVERIFIED_CANNOT_DECIDE, REFUTED_RAW]})
    assert result.status == "ok"
    evs = _attests(log, "verify_findings_semantic")
    assert len(evs) == 2, f"fixture precondition: haiku + opus escalation attested, got {len(evs)}"
    expected = [{"source_id": "invocation:invoke_review_llm:" + ID_R, "sha256": sha256_of(v)}
                for v in FIELD_VALUES]
    for ev in evs:
        assert ev["injections"] == expected, f"AC6: got {ev['injections']!r}"


def test_ac6_integer_line_is_declared_as_its_string(tmp_path) -> None:
    """AC6 (n-2): a caller may pass "line": 1; the digest is sha256 of str(1) == "1". Reachable
    only through a direct _invoke_verifier_agent call with finding['source_id'] set."""
    log = EventLog(tmp_path / "events.jsonl")
    _register(_Router({"verify_findings_semantic": [REFUTED_RAW]}))
    _set_run(log, step="verify_findings_semantic")
    sid = "invocation:invoke_review_llm:" + ID_R
    finding = {"file": "x.py", "line": 1, "quote": "q", "claim": "off by one",
               "severity": "HIGH", "source_id": sid}
    _sv()._invoke_verifier_agent(finding, model_tier="haiku")
    ev = _attests(log, "verify_findings_semantic")[0]
    assert ev["injections"] == [{"source_id": sid, "sha256": sha256_of(v)}
                                for v in ("x.py", "1", "q", "off by one")], (
        f"AC6: got {ev['injections']!r}")


def test_ac6_without_provenance_no_injections_kwarg(tmp_path, monkeypatch) -> None:
    """AC6: without provenance the injections kwarg is not passed at all (fakes without the
    kwarg keep working). Control: with a source_id the kwarg carries the four blocks."""
    sv = _sv()
    calls: list = []

    def fake(**kwargs):
        calls.append(dict(kwargs))
        return StepResult(status="ok", data={"raw_response": REFUTED_RAW}, duration_ms=0,
                          step_name=kwargs.get("step_name", "x"))

    monkeypatch.setattr(llm_subprocess, "invoke_llm_subprocess", fake)
    sid = "invocation:invoke_review_llm:" + ID_R
    base = {"file": "x.py", "line": "3", "quote": "q", "claim": "c", "severity": "HIGH"}
    sv._invoke_verifier_agent({**base, "source_id": sid}, model_tier="haiku")
    assert [(b.source_id, b.content) for b in calls[0].get("injections", ())] == [
        (sid, "x.py"), (sid, "3"), (sid, "q"), (sid, "c")], (
        f"AC6 control: with provenance the blocks are passed; got {calls[0].get('injections')!r}")
    sv._invoke_verifier_agent(dict(base), model_tier="haiku")
    assert "injections" not in calls[1], "AC6: no provenance -> no injections kwarg"
    log, router, result = _verify_run(tmp_path, provenance=False)  # real chain, real backend
    assert result.status == "ok"
    assert len(calls) >= 3, "fixture precondition: the verifier chain dispatched through the fake"
    assert all("injections" not in c for c in calls[2:]), (
        "AC6: verify_findings_semantic without a provenance file must pass no injections kwarg")


# ---------------------------------------------------------------------------
# AC7 - checker (one log per case)
# ---------------------------------------------------------------------------

_OMIT = object()
X1 = "1" * 32
X2 = "2" * 32
Y1 = "3" * 32
ROLE_BLOCK = {"source_id": "role-template", "sha256": "sha256:" + "0" * 64}


def _cm(step: str, inv: str) -> dict:
    return {"source_id": f"invocation:{step}:{inv}", "sha256": "sha256:" + "2" * 64}


def _ev(run, step: str, inv=_OMIT, *, injections=None, output_sha=_OMIT) -> dict:
    """The test_bd28 `_attested` shape (valid prompt_sha256, conformant R3.1/3/5/6) plus the
    bd#152 keys; `run` None omits run_id (harness shape)."""
    payload = {
        "step_name": step, "backend": "claude-subprocess", "model_requested": "sonnet",
        "prompt_sha256": "sha256:" + "0" * 64,
        "injections": [dict(ROLE_BLOCK)] if injections is None else injections,
        "declared_capabilities": ["Read"], "capability_enforcement": "runtime-allowlist",
        "observed_model": "sonnet", "observed_tools": ["Read"],
    }
    if inv is not _OMIT:
        payload["invocation_id"] = inv
    if output_sha is not _OMIT:
        payload["output_sha256"] = output_sha
    ev = {"type": EVENT_TYPE, "payload": payload}
    if run is not None:
        ev["run_id"] = run
    return ev


def _r32_verdict(events) -> tuple:
    from bytedigger_engine.conformance import tokens  # noqa: PLC0415
    from bytedigger_engine.conformance.bd_l3 import check_bd_l3  # noqa: PLC0415
    report = check_bd_l3(events)
    return report.labels["verdict:R3.2"], tokens, report


def _assert_r32_failed(events, why: str) -> None:
    verdict, tokens, report = _r32_verdict(events)
    assert verdict == tokens.REQUIREMENT_FAILED, f"{why}: R3.2 must be failed, got {verdict!r}"
    assert any(v.startswith("R3.2:") for v in report.violations), f"{why}: {report.violations!r}"


def _assert_r32_clean(events, why: str) -> None:
    verdict, tokens, report = _r32_verdict(events)
    assert verdict == tokens.REQUIREMENT_PASSED and report.violations == (), (
        f"{why}: expected R3.2 passed and no violations, got {verdict!r} {report.violations!r}")


GOOD = "sha256:" + "1" * 64


def test_ac7_later_reference_fails_earlier_passes() -> None:
    naming = _ev("A", "s2", Y1, output_sha=GOOD, injections=[dict(ROLE_BLOCK), _cm("s1", X1)])
    target = _ev("A", "s1", X1, output_sha=GOOD)
    _assert_r32_failed([naming, target], "block naming a LATER attestation")
    _assert_r32_clean([target, naming], "same block after the attestation it names")


def test_ac7_self_reference_fails() -> None:
    _assert_r32_failed(
        [_ev("A", "s1", X1, output_sha=GOOD, injections=[dict(ROLE_BLOCK), _cm("s1", X1)])],
        "a block naming its own payload's id")


def test_ac7_malformed_invocation_block_fails() -> None:
    target = _ev("A", "s1", X1, output_sha=GOOD)
    bad = _ev("A", "s2", Y1, output_sha=GOOD,
              injections=[dict(ROLE_BLOCK), {"source_id": "invocation:x:short", "sha256": "sha256:" + "2" * 64}])
    _assert_r32_failed([target, bad], "malformed invocation:x:short")


def test_ac7_wrong_step_fails() -> None:
    target = _ev("A", "s1", X1, output_sha=GOOD)
    wrong = _ev("A", "s2", Y1, output_sha=GOOD, injections=[dict(ROLE_BLOCK), _cm("s9", X1)])
    _assert_r32_failed([target, wrong], "right id, wrong step")


def test_ac7_cross_run_reference_fails_interleaved() -> None:
    a1 = _ev("A", "s1", X1, output_sha=GOOD)
    b1 = _ev("B", "s1", X2, output_sha=GOOD)
    b2 = _ev("B", "s2", Y1, output_sha=GOOD, injections=[dict(ROLE_BLOCK), _cm("s1", X1)])
    a2 = _ev("A", "s3", "4" * 32, output_sha=GOOD)
    _assert_r32_failed([a1, b1, b2, a2], "run B naming run A's attestation")
    own = _ev("B", "s2", Y1, output_sha=GOOD, injections=[dict(ROLE_BLOCK), _cm("s1", X2)])
    _assert_r32_clean([a1, b1, own, a2], "run B naming its own earlier attestation")


def test_ac7_output_sha256_form() -> None:
    _assert_r32_failed([_ev("A", "s1", X1, output_sha="deadbeef")], "output_sha256 'deadbeef'")
    _assert_r32_failed([_ev("A", "s1", X1, output_sha="sha256:" + "A" * 64)], "uppercase hex digest")
    _assert_r32_clean([_ev("A", "s1", X1, output_sha=None)], "output_sha256 null is not a violation")


def test_ac7_invocation_id_form() -> None:
    _assert_r32_failed([_ev("A", "s1", "XYZ", output_sha=GOOD)], "invocation_id 'XYZ'")
    _assert_r32_clean([_ev("A", "s1")], "a pre-#152 payload without both keys")


def test_ac7_form_violation_observes_but_form_valid_does_not() -> None:
    _assert_r32_failed([_ev("A", "s1", X1, output_sha="deadbeef", injections=[])],
                       "a form violation counts as an R3.2 observation")
    verdict, tokens, report = _r32_verdict([_ev("A", "s1", X1, output_sha=GOOD, injections=[])])
    assert verdict == tokens.REQUIREMENT_NOT_CHECKED, (
        f"form-valid post-#152 payload without injections stays not-checked; got {verdict!r}")
    assert not [v for v in report.violations if v.startswith("R3.2:")]


def test_ac7_none_run_never_resolves() -> None:
    """Gate r3 m-2: the attestation it names ALSO has no run_id (None == None must not resolve)."""
    target = _ev(None, "s1", X1, output_sha=GOOD)
    naming = _ev(None, "s2", Y1, output_sha=GOOD, injections=[dict(ROLE_BLOCK), _cm("s1", X1)])
    _assert_r32_failed([target, naming], "an invocation: block in an event with no run_id")


def test_ac7_real_log_from_ac1_passes(tmp_path) -> None:
    """AC7: the real log of the AC1 chain is R3.2 passed (class-M block resolves in-run)."""
    s = _drive_sat(tmp_path, [_sat_raw(FIXES_SINGLE)])
    _, fixed = _fix_from(s, s.r.data)
    assert fixed.status == "ok"
    from bytedigger_engine.conformance import tokens  # noqa: PLC0415
    from bytedigger_engine.conformance.bd_l3 import check_bd_l3  # noqa: PLC0415
    events = s.log.read_all()
    assert _class_m(_attests(s.log, "invoke_fix_llm")[0]), "fixture precondition: AC1 block declared"
    report = check_bd_l3(events)
    assert report.labels["verdict:R3.2"] == tokens.REQUIREMENT_PASSED, (
        f"AC7: real log R3.2 verdict {report.labels['verdict:R3.2']!r}; {report.violations!r}")
    assert not [v for v in report.violations if v.startswith("R3.2:")]


# ---------------------------------------------------------------------------
# AC8 - docs + lint
# ---------------------------------------------------------------------------

def _engine_root() -> Path:
    import bytedigger_engine
    return Path(bytedigger_engine.__file__).parent


def _authorship_text() -> str:
    return (_engine_root() / "conformance" / "AUTHORSHIP_SPEC.md").read_text(encoding="utf-8")


def test_ac8_authorship_spec_class_m_paragraph() -> None:
    text = _authorship_text()
    start = text.index("**Class M,")
    end = text.index("**Chunk rule.**", start)
    flat = " ".join(text[start:end].split())
    assert "deferred to bd#206" not in flat, "AC8: the 'deferred to bd#206' sentence must be replaced"
    assert "until those declarations exist" not in flat, "AC8 (gate r3 n-1): stale sentence must go"
    for needle in ("invoke_satisfaction_llm", "last_findings.json", "verify_findings_semantic",
                   "invocation:<step_name>:<invocation_id>"):
        assert needle in flat, f"AC8: class-M paragraph must name {needle!r}"


def test_ac8_authorship_spec_r32_row() -> None:
    rows = [ln for ln in _authorship_text().splitlines() if ln.lstrip().startswith("| `R3.2` |")]
    assert len(rows) == 1, f"AC8: expected exactly one R3.2 row, got {len(rows)}"
    flat = " ".join(rows[0].split())
    assert "class-M block declarations are a follow-up (bd#206)" not in flat
    assert "declared at the three carry sites (bd#206)" in flat, f"AC8: R3.2 row text: {flat!r}"


def test_ac8_inventory_key_and_lint() -> None:
    lint = importlib.import_module("bytedigger_engine.conformance.class_i_lint")
    inv = lint.load_inventory()
    key = "lib/findings_provenance.py::review_source_id::read_text#0"
    assert key in inv["sites"], f"AC8: inventory must key the new read site {key!r}"
    assert inv["sites"][key]["class"] == "not-prompt"
    assert lint.check(_engine_root(), inv) == []
