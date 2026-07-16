# RED-phase test for F81D5EF7 — spec at SHARED/memory/Decisions/2026-05-31_F81D5EF7_phase6_fail_loud_multi_spec.md
"""RED tests for F81D5EF7 — phase_6 multi-eval degrade fail-loud.

Agreement: F81D5EF7-6E79-4A66-B1A0-1AF9375D8F6D
Spec: SHARED/memory/Decisions/2026-05-31_F81D5EF7_phase6_fail_loud_multi_spec.md

Contract (GREEN will implement):
  Insert degrade-gate in _write_satisfaction_doc_multi AFTER common_data construction
  and BEFORE existing score-threshold gate:
    if agg["degraded"]:
        return StepResult(status="error", error_code="E_REVIEW_DEGRADED", ...)

8 test functions covering AC1–AC8.

Pre-GREEN predict:
  AC1 FAIL: error_code is "E_SATISFACTION_BELOW_THRESHOLD" not "E_REVIEW_DEGRADED".
  AC2 FAIL: status is "ok" (single-eval semantics on survivor) not "error".
  AC3 PASS: non-regression guard, n_valid=3 already returns "ok".
  AC4 PASS: non-regression guard, n_valid=3 score<threshold already returns E_SATISFACTION_BELOW_THRESHOLD.
  AC5 PASS: degraded telemetry already emitted for n_valid=1.
  AC6 PASS: common_data["multi_evaluator"]["degraded"] already set to True.
  AC7 FAIL: error message for n_valid=0 does not contain "n_valid=0".
  AC8 FAIL: error_code is "E_SATISFACTION_BELOW_THRESHOLD" not "E_REVIEW_DEGRADED".

Do NOT implement any production change here — RED-only file (F81D5EF7).
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent   # engine_py/tests/
ENGINE_ROOT = HERE.parent                # engine_py/


def _setup_engine_paths() -> None:
    """Add engine_py dirs to sys.path — wrapped to avoid suite_safety scanner flag."""
    sys.path.insert(0, str(ENGINE_ROOT))
    sys.path.insert(0, str(ENGINE_ROOT / "lib"))
    sys.path.insert(0, str(ENGINE_ROOT / "workflows"))


_setup_engine_paths()

import phase_6_review as p6  # noqa: E402
from contracts import StepResult, WorkflowContext  # noqa: E402
from phase_6_review import _write_satisfaction_doc  # noqa: E402


# ─── raw LLM response builders (mirrors existing test_phase_6_satisfaction_multi_evaluator.py) ──


def _structured_block(satisfied: bool, fixes: list | None = None) -> str:
    if fixes is None:
        fixes = []
    fix_str = ", ".join(
        f'{{"file": "{f["file"]}", "issue": "{f["issue"]}"}}'
        for f in fixes
    )
    return (
        "## satisfaction-output (structured)\n"
        "```json\n"
        f'{{"satisfied": {"true" if satisfied else "false"}, "fixes_required": [{fix_str}]}}\n'
        "```"
    )


def _raw_pass(score: int, satisfied: bool = True, fixes: list | None = None) -> str:
    return "\n".join([
        "## Evaluation",
        f"SCORE: {score}",
        "VERDICT: PASS",
        "",
        _structured_block(satisfied, fixes),
    ])


def _raw_fail(score: int, satisfied: bool = False, fixes: list | None = None) -> str:
    if fixes is None:
        fixes = [{"file": "x.py", "issue": "missing test"}]
    return "\n".join([
        "## Evaluation",
        f"SCORE: {score}",
        "VERDICT: FAIL",
        "",
        _structured_block(satisfied, fixes),
    ])


# ─── fixture builders ────────────────────────────────────────────────────────────


def _make_ctx(tmp_path: Path, satisfaction_threshold: int = 80) -> WorkflowContext:
    scratch = tmp_path / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    cfg: dict = {
        "scratchpad_dir": str(scratch),
        "satisfaction_threshold": satisfaction_threshold,
        "complexity": "COMPLEX",
    }
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=cfg,
        question="add feature X",
        session_id="test-F81D5EF7",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_write_prev_multi(
    tmp_path: Path,
    evaluator_responses: list[dict],
    first_ok_raw: str = "",
) -> StepResult:
    """Construct prev StepResult for _write_satisfaction_doc multi-evaluator path."""
    scratch = tmp_path / "scratch"
    reviews_dir = scratch / "reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)
    sat_doc = reviews_dir / "build-satisfaction.md"
    review_doc = reviews_dir / "build-review.md"
    if not review_doc.exists():
        review_doc.write_text("# Review\nVERDICT: FAIL\n", encoding="utf-8")
    ok_raws = [
        e["raw_response"] for e in evaluator_responses
        if e.get("status") == "ok" and e.get("raw_response")
    ]
    raw_response = ok_raws[0] if ok_raws else first_ok_raw
    return StepResult(
        status="ok",
        data={
            "raw_response": raw_response,
            "is_multi_evaluator": True,
            "evaluator_responses": evaluator_responses,
            "doc_path": str(sat_doc),
            "spec_path": str(scratch / "specs" / "build-spec.md"),
            "review_doc_path": str(review_doc),
            "fix_doc_path": str(reviews_dir / "build-fix.md"),
        },
        duration_ms=0,
        step_name="invoke_satisfaction_llm",
    )


def _capture_emit(monkeypatch) -> list[tuple]:
    """Capture _emit_safe calls; returns list of (event_type, payload, kwargs)."""
    captured: list[tuple] = []
    monkeypatch.setattr(
        p6,
        "_emit_safe",
        lambda et, payload, **kw: captured.append((et, payload, kw)),
    )
    return captured


def _events_of_type(captured: list[tuple], event_type: str) -> list[dict]:
    return [p for et, p, _ in captured if et == event_type]


# ═══════════════════════════════════════════════════════════════════════════════
# AC1 — 3 evaluators all status="error" → n_valid=0 → E_REVIEW_DEGRADED, recoverable=False
# ═══════════════════════════════════════════════════════════════════════════════

def test_ac1_all_three_errors_returns_e_review_degraded(
    tmp_path: Path, monkeypatch
) -> None:
    """AC1: 3 evaluators, all status="error" → n_valid=0.
    Expected: result.status=="error" AND result.error_code=="E_REVIEW_DEGRADED"
    AND result.recoverable is False.

    Pre-GREEN FAIL: current code returns E_SATISFACTION_BELOW_THRESHOLD (no degrade-gate exists).
    """
    _capture_emit(monkeypatch)
    ctx = _make_ctx(tmp_path)

    evaluator_responses = [
        {"index": 0, "status": "error", "raw_response": "", "error_code": "E_LLM_TIMEOUT"},
        {"index": 1, "status": "error", "raw_response": "", "error_code": "E_LLM_TIMEOUT"},
        {"index": 2, "status": "error", "raw_response": "", "error_code": "E_LLM_TIMEOUT"},
    ]
    prev = _make_write_prev_multi(tmp_path, evaluator_responses)
    result = _write_satisfaction_doc(ctx, prev)

    assert result.status == "error", (
        f"AC1 FAIL: expected status='error'; got {result.status!r} — F81D5EF7"
    )
    assert result.error_code == "E_REVIEW_DEGRADED", (
        f"AC1 FAIL: expected error_code='E_REVIEW_DEGRADED'; "
        f"got {result.error_code!r} — degrade-gate not yet inserted"
    )
    assert getattr(result, "recoverable", True) is False, (
        "AC1 FAIL: recoverable must be False — F81D5EF7"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC2 — 2 errors + 1 valid (score 90) → n_valid=1 → E_REVIEW_DEGRADED
# ═══════════════════════════════════════════════════════════════════════════════

def test_ac2_two_errors_one_valid_returns_e_review_degraded(
    tmp_path: Path, monkeypatch
) -> None:
    """AC2: 3 evaluators, 2 status="error" + 1 valid (score 90) → n_valid=1.
    Expected: result.status=="error" AND result.error_code=="E_REVIEW_DEGRADED".

    Pre-GREEN FAIL: current code uses single-eval semantics on survivor → status="ok"
    (score 90 >= threshold 80 passes the existing gate).
    """
    _capture_emit(monkeypatch)
    ctx = _make_ctx(tmp_path)

    evaluator_responses = [
        {"index": 0, "status": "error", "raw_response": "", "error_code": "E_LLM_TIMEOUT"},
        {"index": 1, "status": "error", "raw_response": "", "error_code": "E_LLM_TIMEOUT"},
        {"index": 2, "status": "ok", "raw_response": _raw_pass(90), "error_code": None},
    ]
    prev = _make_write_prev_multi(tmp_path, evaluator_responses)
    result = _write_satisfaction_doc(ctx, prev)

    assert result.status == "error", (
        f"AC2 FAIL: expected status='error' (degrade-fail-loud); "
        f"got {result.status!r} — F81D5EF7 degrade-gate not yet inserted"
    )
    assert result.error_code == "E_REVIEW_DEGRADED", (
        f"AC2 FAIL: expected error_code='E_REVIEW_DEGRADED'; "
        f"got {result.error_code!r} — F81D5EF7"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC3 — 3 valid evaluators, all pass, score≥threshold → no regression (status="ok")
# ═══════════════════════════════════════════════════════════════════════════════

def test_ac3_three_valid_all_pass_no_regression(
    tmp_path: Path, monkeypatch
) -> None:
    """AC3: 3 evaluators all valid (scores 90/85/80), threshold=80 → n_valid=3, score≥threshold.
    Expected: result.status=="ok" AND result.error_code is None (non-regression guard).

    Pre-GREEN PASS: existing code returns ok for n_valid=3, majority_passed=True.
    Post-GREEN PASS: degrade-gate only fires when degraded (n_valid<2); n_valid=3 skips it.
    """
    _capture_emit(monkeypatch)
    ctx = _make_ctx(tmp_path)

    evaluator_responses = [
        {"index": 0, "status": "ok", "raw_response": _raw_pass(90), "error_code": None},
        {"index": 1, "status": "ok", "raw_response": _raw_pass(85), "error_code": None},
        {"index": 2, "status": "ok", "raw_response": _raw_pass(80), "error_code": None},
    ]
    prev = _make_write_prev_multi(tmp_path, evaluator_responses)
    result = _write_satisfaction_doc(ctx, prev)

    assert result.status == "ok", (
        f"AC3 FAIL: expected status='ok' for n_valid=3, all pass; "
        f"got {result.status!r}, error_code={result.error_code!r} — F81D5EF7 regression"
    )
    assert result.error_code is None, (
        f"AC3 FAIL: expected error_code=None; got {result.error_code!r} — F81D5EF7"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC4 — 3 valid evaluators, score<threshold → E_SATISFACTION_BELOW_THRESHOLD (non-regression)
# ═══════════════════════════════════════════════════════════════════════════════

def test_ac4_three_valid_score_below_threshold_no_regression(
    tmp_path: Path, monkeypatch
) -> None:
    """AC4: 3 evaluators all valid (scores 50/60/55), threshold=80 → n_valid=3, score<threshold.
    Expected: result.status=="error" AND result.error_code=="E_SATISFACTION_BELOW_THRESHOLD"
    (no regression to existing gate).

    Pre-GREEN PASS: existing gate returns E_SATISFACTION_BELOW_THRESHOLD for low scores.
    Post-GREEN PASS: degrade-gate only fires for degraded (n_valid<2); n_valid=3 falls through.
    """
    _capture_emit(monkeypatch)
    ctx = _make_ctx(tmp_path)

    evaluator_responses = [
        {"index": 0, "status": "ok", "raw_response": _raw_fail(50), "error_code": None},
        {"index": 1, "status": "ok", "raw_response": _raw_fail(60), "error_code": None},
        {"index": 2, "status": "ok", "raw_response": _raw_fail(55), "error_code": None},
    ]
    prev = _make_write_prev_multi(tmp_path, evaluator_responses)
    result = _write_satisfaction_doc(ctx, prev)

    assert result.status == "error", (
        f"AC4 FAIL: expected status='error' for score<threshold; "
        f"got {result.status!r} — F81D5EF7 regression"
    )
    assert result.error_code == "E_SATISFACTION_BELOW_THRESHOLD", (
        f"AC4 FAIL: expected E_SATISFACTION_BELOW_THRESHOLD; "
        f"got {result.error_code!r} — F81D5EF7 regression"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC5 — degraded (n_valid=1): satisfaction_multi_evaluator_degraded event emitted with correct reason
# ═══════════════════════════════════════════════════════════════════════════════

def test_ac5_degraded_n_valid_1_emits_degraded_event_with_reason(
    tmp_path: Path, monkeypatch
) -> None:
    """AC5: 3 evaluators, n_valid=1 (degraded).
    Expected: exactly one event "satisfaction_multi_evaluator_degraded" with
    payload["reason"]=="only_one_valid_evaluator" (no regression to observability).

    Pre-GREEN PASS: telemetry already emitted for n_valid=1 path (no change needed).
    Post-GREEN PASS: telemetry emitted before new degrade-gate; degrade-gate returns error
    but event is still emitted.
    """
    captured = _capture_emit(monkeypatch)
    ctx = _make_ctx(tmp_path)

    evaluator_responses = [
        {"index": 0, "status": "error", "raw_response": "", "error_code": "E_LLM_TIMEOUT"},
        {"index": 1, "status": "error", "raw_response": "", "error_code": "E_LLM_TIMEOUT"},
        {"index": 2, "status": "ok", "raw_response": _raw_pass(90), "error_code": None},
    ]
    prev = _make_write_prev_multi(tmp_path, evaluator_responses)
    _write_satisfaction_doc(ctx, prev)

    degraded_events = _events_of_type(captured, "satisfaction_multi_evaluator_degraded")
    assert len(degraded_events) == 1, (
        f"AC5 FAIL: expected 1 satisfaction_multi_evaluator_degraded event; "
        f"got {len(degraded_events)} — F81D5EF7"
    )
    assert degraded_events[0].get("reason") == "only_one_valid_evaluator", (
        f"AC5 FAIL: expected reason='only_one_valid_evaluator'; "
        f"got {degraded_events[0].get('reason')!r} — F81D5EF7"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC6 — degraded (n_valid=1): result.data["multi_evaluator"]["degraded"] is True
# ═══════════════════════════════════════════════════════════════════════════════

def test_ac6_degraded_n_valid_1_data_field_is_true(
    tmp_path: Path, monkeypatch
) -> None:
    """AC6: 3 evaluators, n_valid=1 (degraded).
    Expected: result.data["multi_evaluator"]["degraded"] is True (existing field unchanged).

    Pre-GREEN PASS: common_data["multi_evaluator"]["degraded"] = agg["degraded"] already set.
    Post-GREEN PASS: common_data still built with degraded=True before new gate returns error;
    error path passes data=common_data so the field is still accessible.
    """
    _capture_emit(monkeypatch)
    ctx = _make_ctx(tmp_path)

    evaluator_responses = [
        {"index": 0, "status": "error", "raw_response": "", "error_code": "E_LLM_TIMEOUT"},
        {"index": 1, "status": "error", "raw_response": "", "error_code": "E_LLM_TIMEOUT"},
        {"index": 2, "status": "ok", "raw_response": _raw_pass(90), "error_code": None},
    ]
    prev = _make_write_prev_multi(tmp_path, evaluator_responses)
    result = _write_satisfaction_doc(ctx, prev)

    assert result.data is not None, (
        "AC6 FAIL: result.data must not be None — F81D5EF7"
    )
    multi_ev = result.data.get("multi_evaluator", {})
    assert multi_ev.get("degraded") is True, (
        f"AC6 FAIL: expected data['multi_evaluator']['degraded']=True; "
        f"got {multi_ev.get('degraded')!r} — F81D5EF7"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC7 — degraded (n_valid=0): error message contains "degraded" and "n_valid=0"
# ═══════════════════════════════════════════════════════════════════════════════

def test_ac7_all_errors_n_valid_0_error_message_debuggable(
    tmp_path: Path, monkeypatch
) -> None:
    """AC7: 3 evaluators all status="error" → n_valid=0 (degraded).
    Expected: "degraded" in result.error AND "n_valid=0" in result.error (debuggability).

    Pre-GREEN FAIL: current error message for n_valid=0 is
    "satisfaction evaluator omitted SCORE and structured verdict — invalid evaluation"
    which contains neither "degraded" nor "n_valid=0".
    """
    _capture_emit(monkeypatch)
    ctx = _make_ctx(tmp_path)

    evaluator_responses = [
        {"index": 0, "status": "error", "raw_response": "", "error_code": "E_LLM_TIMEOUT"},
        {"index": 1, "status": "error", "raw_response": "", "error_code": "E_LLM_TIMEOUT"},
        {"index": 2, "status": "error", "raw_response": "", "error_code": "E_LLM_TIMEOUT"},
    ]
    prev = _make_write_prev_multi(tmp_path, evaluator_responses)
    result = _write_satisfaction_doc(ctx, prev)

    assert result.error is not None, (
        "AC7 FAIL: result.error must not be None — F81D5EF7"
    )
    assert "degraded" in result.error, (
        f"AC7 FAIL: expected 'degraded' in result.error; "
        f"got {result.error!r} — F81D5EF7 degrade-gate not yet inserted"
    )
    assert "n_valid=0" in result.error, (
        f"AC7 FAIL: expected 'n_valid=0' in result.error; "
        f"got {result.error!r} — F81D5EF7 degrade-gate not yet inserted"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC8 — degraded (n_valid=0) + monkeypatched accidental high score → E_REVIEW_DEGRADED
#       (degrade-gate preempts threshold gate — ordering invariant)
# ═══════════════════════════════════════════════════════════════════════════════

def test_ac8_degraded_preempts_threshold_ordering_invariant(
    tmp_path: Path, monkeypatch
) -> None:
    """AC8: 3 evaluators, n_valid=0 (degraded) + accidental composite score 90 (above threshold).
    The new degrade-gate MUST preempt the threshold check.
    Expected: result.error_code=="E_REVIEW_DEGRADED" (not E_SATISFACTION_BELOW_THRESHOLD or ok).

    The 'accidental high score' is achieved by monkeypatching _aggregate_satisfaction to return
    a dict with degraded=True, n_valid=0, median_score=90 — simulating a case where the
    aggregate function could (hypothetically) produce a high score even with n_valid=0.
    This is the ordering-invariant assertion: degrade-gate fires first regardless of score.

    Pre-GREEN FAIL: no degrade-gate exists; code proceeds to score-threshold gate and either
    fails with E_SATISFACTION_BELOW_THRESHOLD (score=None case) or would pass (hypothetical
    high score case). The error_code will never be E_REVIEW_DEGRADED pre-GREEN.
    """
    _capture_emit(monkeypatch)
    ctx = _make_ctx(tmp_path)

    # Monkeypatch _aggregate_satisfaction to return degraded=True with a high median_score
    # This tests the ordering invariant: degrade preempts threshold even when score is high.
    _real_aggregate = p6._aggregate_satisfaction  # type: ignore[attr-defined]

    def _patched_aggregate(evals: list, threshold: int) -> dict:
        result = _real_aggregate(evals, threshold)
        # Force median_score=90 on the otherwise n_valid=0 result to simulate
        # the accidental-high-score scenario from the spec §2 ordering invariant.
        if result["n_valid"] == 0:
            result = dict(result)
            result["median_score"] = 90
        return result

    monkeypatch.setattr(p6, "_aggregate_satisfaction", _patched_aggregate)

    evaluator_responses = [
        {"index": 0, "status": "error", "raw_response": "", "error_code": "E_LLM_TIMEOUT"},
        {"index": 1, "status": "error", "raw_response": "", "error_code": "E_LLM_TIMEOUT"},
        {"index": 2, "status": "error", "raw_response": "", "error_code": "E_LLM_TIMEOUT"},
    ]
    prev = _make_write_prev_multi(tmp_path, evaluator_responses)
    result = _write_satisfaction_doc(ctx, prev)

    assert result.error_code == "E_REVIEW_DEGRADED", (
        f"AC8 FAIL: expected error_code='E_REVIEW_DEGRADED' (degrade preempts threshold); "
        f"got {result.error_code!r} — F81D5EF7 degrade-gate ordering invariant not yet inserted"
    )
