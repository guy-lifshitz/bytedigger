"""RED tests for bd#85 (P4) — separate retry budgets, spec gate failures retry
the writer with their own findings, satisfaction FAIL runs a fix loop.

Spec: docs/decisions/2026-09-27-bd85-retry-budgets-phase-resume.md §1-§3.

The modules under test exist today and are imported at module level. The
behavior (new gate rows, recoverable lint results, the satisfaction retry)
is asserted inside test bodies, so the file collects cleanly and fails at
run time. No unit under test is patched: only the lint subprocess seam
(`bounded_run`), the directed-repair switch and `_emit_safe` are replaced.
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from bytedigger_engine import telemetry_ctx
from bytedigger_engine.contracts import StepContract, StepResult, WorkflowContext, WorkflowDefinition
from bytedigger_engine.engine import WorkflowEngine
from bytedigger_engine.event_log import EventLog
from bytedigger_engine.lib.resume_keying import resume_sentinel_name
from bytedigger_engine.workflows import _recoverable_policy as policy
from bytedigger_engine.workflows import phase_45_spec, phase_6_review

_CLASSES = ("SIMPLE", "FEATURE", "COMPLEX")


# ─── helpers ──────────────────────────────────────────────────────────────────


def _ctx(scratch: Path, **extra) -> WorkflowContext:
    scratch.mkdir(parents=True, exist_ok=True)
    return WorkflowContext(
        tenant_id="t", scope=None, db_path=None,
        org_config={"scratchpad_dir": str(scratch), **extra},
        question="bd85", session_id="s", persona="p", framework=None, domain=None,
    )


class _FakeProc:
    def __init__(self, returncode: int, stdout: str = "", stderr: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _spec_prev(tmp_path: Path, **data) -> StepResult:
    spec_path = tmp_path / "spec.md"
    spec_path.write_text("## Context\nstub\n", encoding="utf-8")
    return StepResult(
        status="ok", data={"cycle": 1, "spec_path": str(spec_path), **data},
        duration_ms=0, step_name="prev",
    )


def _no_repair(monkeypatch) -> None:
    monkeypatch.setattr(phase_45_spec, "_directed_repair_enabled", lambda ctx: False)
    monkeypatch.setattr(phase_45_spec, "_emit_safe", lambda *a, **k: None)


def _driver_present(monkeypatch) -> None:
    real_is_file = Path.is_file
    monkeypatch.setattr(
        phase_45_spec.Path, "is_file",
        lambda self: self.name in ("spec-cite-lint.py", "lint_spec.py") or real_is_file(self),
    )


def _cite_json(*items: tuple[str, str, str]) -> str:
    return json.dumps({"findings": [
        {"file": f, "symbol": s, "status": st} for f, s, st in items
    ]})


# ─── §1 budgets ───────────────────────────────────────────────────────────────


@pytest.mark.parametrize("bc", _CLASSES)
def test_ac1_matrix_has_explicit_split_rows(bc: str) -> None:
    m = policy._POLICY_MATRIX
    assert m.get((bc, "spec_gates")) == policy.RecoverablePolicy("recoverable_once", 1)
    assert m.get((bc, "spec_review")) == policy.RecoverablePolicy("recoverable_once", 1)
    assert m.get((bc, "satisfaction")) == policy.RecoverablePolicy("recoverable_twice", 2)
    assert (bc, "spec_retry") not in m, "the shared spec_retry budget must be gone"


def _gate_prev(tmp_path: Path, gate_attempts: dict) -> StepResult:
    review = tmp_path / "build-plan-review.md"
    raw = "## Verdict\nREVISE\n## Findings\n1. fix this\n"
    review.write_text(raw, encoding="utf-8")
    spec = tmp_path / "build-spec.md"
    spec.write_text("## Context\nstub\n", encoding="utf-8")
    return StepResult(
        status="ok",
        data={"verdict": "REVISE", "cycle": 2, "review_path": str(review),
              "spec_path": str(spec), "review_raw": raw, "gate_attempts": gate_attempts},
        duration_ms=0, step_name="write_review_doc",
    )


def test_ac2_review_retry_survives_a_spent_lint_budget(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(phase_45_spec, "_emit_safe", lambda *a, **k: None)
    r = phase_45_spec._gate_on_review(None, _gate_prev(tmp_path, {"spec_gates": 1}))
    assert r.recoverable is True, f"a lint retry must not spend the reviewer's budget: {r!r}"
    assert r.data["gate_attempts"] == {"spec_gates": 1, "spec_review": 1}, r.data


def test_ac2b_review_budget_caps_on_its_own_gate(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(phase_45_spec, "_emit_safe", lambda *a, **k: None)
    r = phase_45_spec._gate_on_review(None, _gate_prev(tmp_path, {"spec_review": 1}))
    assert r.recoverable is False and r.error_code == "E_REVIEW_FAILED", r
    assert r.data.get("invalidate_cycle_sentinels_on_fail") is True


def test_ac3_spec_gate_spends_spec_gates_and_marks_its_retry(tmp_path: Path) -> None:
    prev = StepResult(
        status="ok",
        data={"cycle": 2, "spec_path": str(tmp_path / "absent.md"),
              "gate_attempts": {"spec_review": 1},
              "structured_findings": [{"id": "F1", "required_action": "old reviewer finding"}]},
        duration_ms=0, step_name="prev",
    )
    r = phase_45_spec._verify_spec_citations(_ctx(tmp_path / "s"), prev)
    assert r.error_code == "E_SPEC_FILE_MISSING" and r.recoverable is True, r
    assert r.data["gate_attempts"] == {"spec_review": 1, "spec_gates": 1}, r.data
    assert r.data.get("retry_source") == "spec_gates", r.data
    assert not r.data.get("structured_findings"), "a gate retry must drop stale reviewer findings"
    assert "absent.md" in r.data.get("findings", ""), "a file-missing retry must still tell the writer why"


def test_ac3b_spec_gate_caps_on_spec_gates(tmp_path: Path) -> None:
    prev = StepResult(
        status="ok",
        data={"cycle": 2, "spec_path": str(tmp_path / "absent.md"), "gate_attempts": {"spec_gates": 1}},
        duration_ms=0, step_name="prev",
    )
    r = phase_45_spec._verify_spec_citations(_ctx(tmp_path / "s"), prev)
    assert r.recoverable is False, r


def test_ac3c_one_helper_builds_every_spec_gates_retry() -> None:
    src = Path(phase_45_spec.__file__).read_text(encoding="utf-8")
    assert 'gate="spec_retry"' not in src
    assert src.count('gate="spec_gates"') == 1, "every spec_gates retry must go through one helper"
    assert '"spec_retry"' not in src


# ─── §2 lint / cite-lint / preflight retry with findings ──────────────────────


def test_ac4_spec_lint_fail_retries_writer_with_findings(tmp_path: Path, monkeypatch) -> None:
    _no_repair(monkeypatch)
    _driver_present(monkeypatch)
    with patch.object(phase_45_spec, "bounded_run",
                      return_value=_FakeProc(1, "R7: section ## Scope missing\n")):
        r = phase_45_spec._verify_spec_lint(_ctx(tmp_path / "s"), _spec_prev(tmp_path))
    assert r.error_code == "E_SPEC_LINT_FAIL", r
    assert r.recoverable is True, f"a lint FAIL must retry the writer: {r!r}"
    assert r.data["retry_from_step"] == 0
    assert "R7: section ## Scope missing" in r.data["findings"], r.data
    assert r.data["gate_attempts"] == {"spec_gates": 1}
    assert r.data.get("retry_source") == "spec_gates"


def test_ac4b_spec_lint_fail_at_cap_is_terminal(tmp_path: Path, monkeypatch) -> None:
    _no_repair(monkeypatch)
    _driver_present(monkeypatch)
    with patch.object(phase_45_spec, "bounded_run", return_value=_FakeProc(1, "R7: bad\n")):
        r = phase_45_spec._verify_spec_lint(
            _ctx(tmp_path / "s"), _spec_prev(tmp_path, gate_attempts={"spec_gates": 1}))
    assert r.error_code == "E_SPEC_LINT_FAIL" and r.recoverable is False, r
    assert r.data.get("invalidate_cycle_sentinels_on_fail") is True, (
        "a driver resume must re-run the writer, not replay the outputs that failed the gate")


@pytest.mark.parametrize("proc", [_FakeProc(2, "", "usage"), _FakeProc(124), _FakeProc(3)])
def test_ac4c_lint_infra_stays_terminal(tmp_path: Path, monkeypatch, proc) -> None:
    _no_repair(monkeypatch)
    _driver_present(monkeypatch)
    with patch.object(phase_45_spec, "bounded_run", return_value=proc):
        r = phase_45_spec._verify_spec_lint(_ctx(tmp_path / "s"), _spec_prev(tmp_path))
    assert r.status == "error" and r.recoverable is False, r


def test_ac4d_lint_rc1_with_no_findings_is_a_crash(tmp_path: Path, monkeypatch) -> None:
    _no_repair(monkeypatch)
    _driver_present(monkeypatch)
    with patch.object(phase_45_spec, "bounded_run", return_value=_FakeProc(1, "", "Traceback: boom")):
        r = phase_45_spec._verify_spec_lint(_ctx(tmp_path / "s"), _spec_prev(tmp_path))
    assert r.recoverable is False and "boom" in (r.error or ""), r


def test_ac5_cite_lint_fail_retries_writer_with_findings(tmp_path: Path, monkeypatch) -> None:
    _no_repair(monkeypatch)
    _driver_present(monkeypatch)
    out = _cite_json(("nothere/ghost.py", "f", "missing_file"))
    with patch.object(phase_45_spec, "bounded_run", return_value=_FakeProc(1, out)):
        r = phase_45_spec._verify_spec_cite_lint(_ctx(tmp_path / "s"), _spec_prev(tmp_path))
    assert r.error_code == "E_SPEC_CITE_LINT_FAIL", r
    assert r.recoverable is True, f"a cite-lint FAIL must retry the writer: {r!r}"
    assert "nothere/ghost.py" in r.data["findings"], r.data
    assert r.data["gate_attempts"] == {"spec_gates": 1}
    assert r.data.get("retry_source") == "spec_gates"


def test_ac5b_cite_lint_fail_at_cap_is_terminal(tmp_path: Path, monkeypatch) -> None:
    _no_repair(monkeypatch)
    _driver_present(monkeypatch)
    out = _cite_json(("nothere/ghost.py", "f", "missing_file"))
    with patch.object(phase_45_spec, "bounded_run", return_value=_FakeProc(1, out)):
        r = phase_45_spec._verify_spec_cite_lint(
            _ctx(tmp_path / "s"), _spec_prev(tmp_path, gate_attempts={"spec_gates": 1}))
    assert r.error_code == "E_SPEC_CITE_LINT_FAIL" and r.recoverable is False, r


@pytest.mark.parametrize("proc", [_FakeProc(1, "not json"), _FakeProc(2, "", "usage"), _FakeProc(124), _FakeProc(3)])
def test_ac5c_cite_lint_blindness_stays_terminal(tmp_path: Path, monkeypatch, proc) -> None:
    _no_repair(monkeypatch)
    _driver_present(monkeypatch)
    with patch.object(phase_45_spec, "bounded_run", return_value=proc):
        r = phase_45_spec._verify_spec_cite_lint(_ctx(tmp_path / "s"), _spec_prev(tmp_path))
    assert r.status == "error" and r.recoverable is False, r


def test_ac6_preflight_content_findings_retry_and_keep_prev_data(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("HAL_SPEC_PREFLIGHT_BATCH", "1")
    _no_repair(monkeypatch)
    out = _cite_json(("nothere/ghost.py", "f", "missing_file"))

    def _fake(cmd, **kwargs):
        if "spec-cite-lint" in " ".join(str(c) for c in cmd):
            return _FakeProc(1, out)
        return _FakeProc(0)

    prev = _spec_prev(tmp_path, gate_attempts={"spec_review": 1})
    with patch.object(phase_45_spec, "bounded_run", side_effect=_fake):
        r = phase_45_spec._verify_spec_preflight_batch(_ctx(tmp_path / "s"), prev)
    assert r.error_code == "E_SPEC_PREFLIGHT_BATCH" and r.recoverable is True, r
    assert r.data["spec_path"] == prev.data["spec_path"], "prev.data must survive the retry"
    assert r.data["gate_attempts"] == {"spec_review": 1, "spec_gates": 1}, r.data
    assert "nothere/ghost.py" in r.data["findings"], r.data
    assert r.data.get("retry_source") == "spec_gates"
    assert isinstance(r.data.get("spec_lint_findings"), list) and r.data["spec_lint_findings"], (
        "directed repair re-reads the finding list; it must survive as a list")


@pytest.mark.parametrize("lint, cite", [
    (_FakeProc(2, "", "usage"), _FakeProc(0)),                   # lint driver broken
    (_FakeProc(0), _FakeProc(1, "not json")),                    # cite blind
    (_FakeProc(0), _FakeProc(2, "", "usage")),                   # cite driver broken
    (_FakeProc(1, "R7: real finding\n"), _FakeProc(2, "", "u")),  # content mixed with infra
    (_FakeProc(1, "", "Traceback"), _FakeProc(0)),               # lint crashed, rc 1
])
def test_ac6b_preflight_infra_finding_stays_terminal(tmp_path: Path, monkeypatch, lint, cite) -> None:
    monkeypatch.setenv("HAL_SPEC_PREFLIGHT_BATCH", "1")
    _no_repair(monkeypatch)

    def _fake(cmd, **kwargs):
        if "spec-cite-lint" in " ".join(str(c) for c in cmd):
            return cite
        return lint

    with patch.object(phase_45_spec, "bounded_run", side_effect=_fake):
        r = phase_45_spec._verify_spec_preflight_batch(_ctx(tmp_path / "s"), _spec_prev(tmp_path))
    assert r.error_code == "E_SPEC_PREFLIGHT_BATCH" and r.recoverable is False, r


def test_ac7_gate_retry_prompt_uses_gate_findings_not_stale_thread(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("HAL_SPEC_DELTA_RETRY", raising=False)
    from bytedigger_engine.findings_sidecar import persist_findings_thread

    scratch = tmp_path / "scratch"
    spec_path = scratch / phase_45_spec.SPEC_DOC_RELPATH
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text("## Context\nprior spec\n", encoding="utf-8")
    persist_findings_thread(
        scratch, [{"id": "F1", "type": "gap", "evidence": "e",
                   "required_action": "STALE_REVIEWER_ACTION"}], cycle=1)
    prev = StepResult(
        status="ok",
        data={"cycle": 3, "findings": "spec_cite_lint: GATE_FINDING_GHOST_PATH",
              "retry_source": "spec_gates", "is_frozen": False},
        duration_ms=0, step_name="detect_frozen_spec",
    )
    r = phase_45_spec._build_spec_prompt(_ctx(scratch), prev)
    prompt = r.data["prompt"]
    assert "GATE_FINDING_GHOST_PATH" in prompt, "the writer must see the gate findings"
    assert "STALE_REVIEWER_ACTION" not in prompt, "a gate retry must not replay the old review thread"
    assert "address reviewer findings" not in prompt, "gate findings are not reviewer findings"
    assert r.data.get("delta_retry") is True, (
        "a gate retry takes the delta lane")


def test_ac8_frozen_fallback_threads_gate_attempts(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(phase_45_spec, "_emit_safe", lambda *a, **k: None)
    prev = _gate_prev(tmp_path, {"spec_gates": 1})
    prev.data["is_frozen"] = True
    r = phase_45_spec._gate_on_review(None, prev)
    assert r.recoverable is True and r.data.get("frozen_fallback") is True, r
    assert r.data.get("gate_attempts") == {"spec_gates": 1}, r.data


# ─── §3 satisfaction fix loop ─────────────────────────────────────────────────


_RAW_FAIL = "\n".join([
    "## Evaluation", "SCORE: 50", "VERDICT: FAIL", "",
    '## satisfaction-output (structured)\n```json\n'
    '{"satisfied": false, "fixes_required": [{"file": "a.py", "issue": "SAT_ISSUE_MARKER"}]}\n```',
])


def _sat_prev(scratch: Path, raw: str = _RAW_FAIL, **extra) -> StepResult:
    reviews = scratch / "reviews"
    reviews.mkdir(parents=True, exist_ok=True)
    review_doc = reviews / "build-review.md"
    review_doc.write_text("# Review\nVERDICT: PASS\n", encoding="utf-8")
    (reviews / "build-review-fix.md").write_text("# verified findings\n", encoding="utf-8")
    return StepResult(
        status="ok",
        data={"raw_response": raw, "doc_path": str(reviews / "build-satisfaction.md"),
              "spec_path": str(scratch / "specs" / "build-spec.md"),
              "review_doc_path": str(review_doc), "fix_doc_path": str(reviews / "build-fix.md"),
              "review_verdict": "PASS", **extra},
        duration_ms=0, step_name="invoke_satisfaction_llm",
    )


def _under_cycle(cycle: int):
    telemetry_ctx.set_current_run(
        event_log=None, run_id="r85", step_name="write_satisfaction_doc",
        phase="phase_6_review", cycle=cycle,
    )


@pytest.fixture
def _clean_run_ctx():
    telemetry_ctx.clear_current_run()
    yield
    telemetry_ctx.clear_current_run()


def _fix_prompt_index() -> int:
    names = [s.name for s in phase_6_review.phase_6_review_workflow().steps]
    return names.index("build_fix_prompt")


def test_ac9_satisfaction_fail_retries_fix_step(tmp_path: Path, monkeypatch, _clean_run_ctx) -> None:
    monkeypatch.setattr(phase_6_review, "_emit_safe", lambda *a, **k: None)
    scratch = tmp_path / "scratch"
    ctx = _ctx(scratch, satisfaction_threshold=85)
    _under_cycle(1)
    r = phase_6_review._write_satisfaction_doc(ctx, _sat_prev(scratch))
    assert r.error_code == "E_SATISFACTION_BELOW_THRESHOLD", r
    assert r.recoverable is True, f"satisfaction FAIL must run the fix loop: {r!r}"
    assert r.data["retry_from_step"] == _fix_prompt_index()
    assert "SAT_ISSUE_MARKER" in r.data["findings"], r.data
    assert r.data["fix_loop_source"] == "satisfaction"
    assert r.data["verdict"] == "PASS"
    assert r.data["review_fix_doc_path"].endswith("build-review-fix.md"), r.data
    assert r.data["gate_budget_ok"] is True
    assert r.data["cycle_count"] == 1, "the engine must advance to cycle 2, not replay cycle 1"


def test_ac9b_satisfaction_cap_after_two_fix_loops(tmp_path: Path, monkeypatch, _clean_run_ctx) -> None:
    monkeypatch.setattr(phase_6_review, "_emit_safe", lambda *a, **k: None)
    scratch = tmp_path / "scratch"
    ctx = _ctx(scratch, satisfaction_threshold=85)
    _under_cycle(2)
    second = phase_6_review._write_satisfaction_doc(ctx, _sat_prev(scratch))
    assert second.recoverable is True and second.data["cycle_count"] == 2, second
    _under_cycle(3)
    r = phase_6_review._write_satisfaction_doc(ctx, _sat_prev(scratch))
    assert r.recoverable is False and r.error_code == "E_SATISFACTION_BELOW_THRESHOLD", r
    assert r.data.get("invalidate_cycle_sentinels_on_fail") is True


def test_ac9c_no_step_context_fails_closed(tmp_path: Path, monkeypatch, _clean_run_ctx) -> None:
    monkeypatch.setattr(phase_6_review, "_emit_safe", lambda *a, **k: None)
    scratch = tmp_path / "scratch"
    r = phase_6_review._write_satisfaction_doc(_ctx(scratch, satisfaction_threshold=85), _sat_prev(scratch))
    assert r.recoverable is False and r.error_code == "E_SATISFACTION_BELOW_THRESHOLD", r


def test_ac9d_ac_checklist_fail_also_retries(tmp_path: Path, monkeypatch, _clean_run_ctx) -> None:
    monkeypatch.setattr(phase_6_review, "_emit_safe", lambda *a, **k: None)
    scratch = tmp_path / "scratch"
    spec = scratch / "specs" / "build-spec.md"
    spec.parent.mkdir(parents=True, exist_ok=True)
    spec.write_text("## Acceptance Criteria\n1. does X\n2. does Y\n", encoding="utf-8")
    raw = "\n".join([
        "## Evaluation", "SCORE: 95", "VERDICT: PASS", "",
        "## AC Checklist", "- AC1: PASS — a.py:1 ok", "- AC2: FAIL — a.py:2 missing", "",
        '## satisfaction-output (structured)\n```json\n{"satisfied": true, "fixes_required": []}\n```',
    ])
    _under_cycle(1)
    r = phase_6_review._write_satisfaction_doc(_ctx(scratch, satisfaction_threshold=85), _sat_prev(scratch, raw))
    assert r.error_code == "E_SATISFACTION_AC_CHECKLIST", r
    assert r.recoverable is True, r
    assert r.data["findings"], "the failing AC must reach the fix worker"


def test_ac9e_multi_evaluator_fail_retries(tmp_path: Path, monkeypatch, _clean_run_ctx) -> None:
    monkeypatch.setattr(phase_6_review, "_emit_safe", lambda *a, **k: None)
    scratch = tmp_path / "scratch"
    evals = [{"index": i, "status": "ok", "raw_response": _RAW_FAIL} for i in range(3)]
    prev = _sat_prev(scratch, is_multi_evaluator=True, evaluator_responses=evals)
    _under_cycle(1)
    r = phase_6_review._write_satisfaction_doc(_ctx(scratch, satisfaction_threshold=85), prev)
    assert r.error_code == "E_SATISFACTION_BELOW_THRESHOLD", r
    assert r.recoverable is True, r
    assert "SAT_ISSUE_MARKER" in r.data["findings"], r.data


def test_ac9f_workflow_without_fix_step_stays_terminal(tmp_path: Path, monkeypatch, _clean_run_ctx) -> None:
    # bd#89 P2b: re-pointed from the dropped SIMPLE fast path. The kept behavior is
    # "outside a workflow that has a fix step the satisfaction gate fails closed";
    # a workflow label other than phase_6_review has no fix step.
    monkeypatch.setattr(phase_6_review, "_emit_safe", lambda *a, **k: None)
    scratch = tmp_path / "scratch"
    telemetry_ctx.set_current_run(
        event_log=None, run_id="r85", step_name="write_satisfaction_doc",
        phase="phase_7_synthesize", cycle=1,
    )
    r = phase_6_review._write_satisfaction_doc(_ctx(scratch, satisfaction_threshold=85), _sat_prev(scratch))
    assert r.error_code == "E_SATISFACTION_BELOW_THRESHOLD" and r.recoverable is False, r


@pytest.mark.parametrize("raw", [
    "## Evaluation\nno score and no structured block here\n",                     # no_signals
    '## Evaluation\n## satisfaction-output (structured)\n```json\n'
    '{"satisfied": false, "fixes_required": [{"file": "a.py", "issue": "x"}]}\n```',  # no SCORE
    "## Evaluation\nSCORE: 95\nVERDICT: PASS\n\n## satisfaction-output (structured)\n```json\n"
    '{"satisfied": false, "fixes_required": [{"file": "a.py", "issue": "x"}]}\n```',  # drift
])
def test_ac9g_evaluator_format_failure_stays_terminal(tmp_path: Path, monkeypatch, _clean_run_ctx, raw) -> None:
    monkeypatch.setattr(phase_6_review, "_emit_safe", lambda *a, **k: None)
    scratch = tmp_path / "scratch"
    _under_cycle(1)
    r = phase_6_review._write_satisfaction_doc(_ctx(scratch, satisfaction_threshold=85), _sat_prev(scratch, raw))
    assert r.status == "error" and r.recoverable is False, r


@pytest.mark.parametrize("n_ok", [0, 1])
def test_ac9h_review_degraded_stays_terminal(tmp_path: Path, monkeypatch, _clean_run_ctx, n_ok) -> None:
    monkeypatch.setattr(phase_6_review, "_emit_safe", lambda *a, **k: None)
    scratch = tmp_path / "scratch"
    evals = [{"index": i, "status": "ok" if i < n_ok else "error",
              "raw_response": _RAW_FAIL if i < n_ok else ""} for i in range(3)]
    prev = _sat_prev(scratch, is_multi_evaluator=True, evaluator_responses=evals)
    _under_cycle(1)
    r = phase_6_review._write_satisfaction_doc(_ctx(scratch, satisfaction_threshold=85), prev)
    assert r.error_code == "E_REVIEW_DEGRADED" and r.recoverable is False, r


def test_ac9i_missing_ac_checklist_is_format_not_work(tmp_path: Path, monkeypatch, _clean_run_ctx) -> None:
    monkeypatch.setattr(phase_6_review, "_emit_safe", lambda *a, **k: None)
    scratch = tmp_path / "scratch"
    spec = scratch / "specs" / "build-spec.md"
    spec.parent.mkdir(parents=True, exist_ok=True)
    spec.write_text("## Acceptance Criteria\n1. does X\n", encoding="utf-8")
    raw = "\n".join([
        "## Evaluation", "SCORE: 95", "VERDICT: PASS", "",
        '## satisfaction-output (structured)\n```json\n{"satisfied": true, "fixes_required": []}\n```',
    ])
    _under_cycle(1)
    r = phase_6_review._write_satisfaction_doc(_ctx(scratch, satisfaction_threshold=85), _sat_prev(scratch, raw))
    assert r.error_code == "E_SATISFACTION_AC_CHECKLIST" and r.recoverable is False, r


def test_ac9j_multi_fail_without_fixes_stays_terminal(tmp_path: Path, monkeypatch, _clean_run_ctx) -> None:
    monkeypatch.setattr(phase_6_review, "_emit_safe", lambda *a, **k: None)
    scratch = tmp_path / "scratch"
    evals = [{"index": i, "status": "ok", "raw_response": "## Evaluation\nSCORE: 40\nVERDICT: FAIL\n"}
             for i in range(3)]
    prev = _sat_prev(scratch, is_multi_evaluator=True, evaluator_responses=evals)
    _under_cycle(1)
    r = phase_6_review._write_satisfaction_doc(_ctx(scratch, satisfaction_threshold=85), prev)
    assert r.error_code == "E_SATISFACTION_BELOW_THRESHOLD" and r.recoverable is False, r


def test_ac10_fix_prompt_accepts_retry_dict_and_carries_satisfaction(tmp_path: Path) -> None:
    scratch = tmp_path / "scratch"
    reviews = scratch / "reviews"
    reviews.mkdir(parents=True, exist_ok=True)
    (reviews / "build-review.md").write_text("# Review\nVERDICT: PASS\n", encoding="utf-8")
    (reviews / "build-review-fix.md").write_text("# verified\n", encoding="utf-8")
    prev = {
        "cycle": 2, "findings": "- a.py: SAT_ISSUE_MARKER",
        "fix_loop_source": "satisfaction", "verdict": "PASS",
        "spec_path": str(scratch / "specs" / "build-spec.md"),
        "review_doc_path": str(reviews / "build-review.md"),
        "review_fix_doc_path": str(reviews / "build-review-fix.md"),
        "satisfaction_doc_path": str(reviews / "build-satisfaction.md"),
    }
    r = phase_6_review._build_fix_prompt(_ctx(scratch), prev)
    assert r.status == "ok", r
    assert "SATISFACTION FINDINGS" in r.data["prompt"]
    assert "SAT_ISSUE_MARKER" in r.data["prompt"]
    assert "EARLY-RETURN" not in r.data["prompt"], (
        "a satisfaction loop must not let the worker skip because the review verdict was PASS")
    assert "FIX SKIPPED" not in r.data["prompt"], "no skip marker on a satisfaction loop"
    assert "build-satisfaction.md" in r.data["prompt"], "the worker must be able to read the evaluator's report"


def test_ac11_terminal_exit_invalidates_every_cycle(tmp_path: Path) -> None:
    scratch = tmp_path / "scratch"
    run_id = "run85"

    def _llm(ctx, prev):
        return StepResult(status="ok", data={}, duration_ms=0, step_name="llm_step")

    def _gate(ctx, prev):
        cycle = telemetry_ctx.get_current_run().cycle
        if cycle < 2:
            return StepResult(
                status="error", data={"retry_from_step": 0, "cycle_count": cycle, "gate_budget_ok": True},
                duration_ms=0, step_name="gate", error="retry", error_code="E_VALIDATION_RETRY",
                recoverable=True,
            )
        return StepResult(
            status="error", data={"invalidate_cycle_sentinels_on_fail": True}, duration_ms=0,
            step_name="gate", error="cap", error_code="E_SATISFACTION_BELOW_THRESHOLD", recoverable=False,
        )

    wf = WorkflowDefinition(name="wf85", steps=[
        StepContract(name="llm_step", execute=_llm, resume_sentinel=True),
        StepContract(name="gate", execute=_gate),
    ])
    eng = WorkflowEngine(event_log=EventLog(tmp_path / "events.jsonl"))
    eng.register("wf85", wf)
    result, _ = eng.execute("wf85", _ctx(scratch), run_id=run_id)
    assert result.error_code == "E_SATISFACTION_BELOW_THRESHOLD"
    for cycle in (1, 2):
        f = scratch / "resume" / resume_sentinel_name("llm_step", cycle, run_id, None, "wf85")
        assert not f.exists(), f"cycle {cycle} sentinel survived a terminal exit: {f}"


def test_ac12_workflow_finished_carries_error_code(tmp_path: Path) -> None:
    def _fail(ctx, prev):
        return StepResult(status="error", data={}, duration_ms=0, step_name="s",
                          error="x", error_code="E_SOME_FAIL", recoverable=False)

    log = EventLog(tmp_path / "events.jsonl")
    eng = WorkflowEngine(event_log=log)
    eng.register("wf85b", WorkflowDefinition(name="wf85b", steps=[StepContract(name="s", execute=_fail)]))
    eng.execute("wf85b", _ctx(tmp_path / "s"), run_id="r1")
    finished = [e for e in log.read_all() if e.get("event_type") == "workflow_finished"]
    assert finished and finished[-1]["payload"].get("error_code") == "E_SOME_FAIL", finished


# ac18 (the dropped SIMPLE-only spec loop keeps lint terminal) retired by bd#89 P2b: GAP-3.
