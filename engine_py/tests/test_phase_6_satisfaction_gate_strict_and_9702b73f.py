"""RED tests for 9702B73F — satisfaction gate strict-AND (single-evaluator).

Agreement: 9702B73F (Track B B3 of /build pipeline bug-audit, PPBA #591).
Spec: SHARED/memory/Decisions/2026-05-13_9702B73F_satisfaction_gate_strict_and_spec.md

Contract (GREEN will implement in _write_satisfaction_doc, ~line 2494):
  Replace `passed = bool(structured.satisfied) if structured is not None else md_says_pass`
  with strict-AND when both signals present; fail-closed when structured present but SCORE absent.

All six tests MUST FAIL until GREEN implements the fix.
Do NOT implement any production change here — RED-only file (9702B73F).
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).parent
ENGINE_ROOT = HERE.parent
sys.path.insert(0, str(ENGINE_ROOT))
sys.path.insert(0, str(ENGINE_ROOT / "lib"))
sys.path.insert(0, str(ENGINE_ROOT / "workflows"))

import phase_6_review  # noqa: E402  (needed for monkeypatch target)
from contracts import StepResult, WorkflowContext  # noqa: E402
from phase_6_review import (  # noqa: E402
    _write_satisfaction_doc,
    SatisfactionVerdict,
    _parse_satisfaction_structured,
)


# ─── structured-block markdown builders ──────────────────────────────────────
# Exact format _parse_satisfaction_structured expects (regex-sensitive).

_VALID_BLOCK_PASS = (
    "## satisfaction-output (structured)\n"
    "```json\n"
    '{"satisfied": true, "fixes_required": []}\n'
    "```"
)

_VALID_BLOCK_FAIL = (
    "## satisfaction-output (structured)\n"
    "```json\n"
    '{"satisfied": false, "fixes_required": [{"file": "a.py", "issue": "x"}]}\n'
    "```"
)


def _raw_with_score(score_line: str, block: str | None) -> str:
    """Build a raw LLM response combining a SCORE line and optional structured block."""
    parts = [
        "## Composite",
        score_line,
        "VERDICT: PASS",
        "",
    ]
    if block is not None:
        parts.append(block)
    return "\n".join(parts)


def _raw_fail_with_score(score_line: str, block: str | None) -> str:
    """Build a raw LLM response for a FAIL scenario."""
    parts = [
        "## Composite",
        score_line,
        "VERDICT: FAIL",
        "",
    ]
    if block is not None:
        parts.append(block)
    return "\n".join(parts)


def _raw_no_score_with_block(block: str) -> str:
    """Build a raw LLM response with structured block but NO SCORE line (AC5)."""
    return "\n".join([
        "## Composite",
        "VERDICT: PASS",
        "",
        block,
    ])


# ─── fixture builders ─────────────────────────────────────────────────────────


def _make_ctx(tmp_path: Path, satisfaction_threshold: int = 85) -> WorkflowContext:
    scratch = tmp_path / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={
            "scratchpad_dir": str(scratch),
            "satisfaction_threshold": satisfaction_threshold,
        },
        question="add feature X",
        session_id="test-9702B73F",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_satisfaction_prev(
    tmp_path: Path,
    raw_response: str,
) -> StepResult:
    """Construct a prev StepResult that _write_satisfaction_doc expects."""
    scratch = tmp_path / "scratch"
    reviews_dir = scratch / "reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)

    sat_doc = reviews_dir / "build-satisfaction.md"
    review_doc = reviews_dir / "build-review.md"
    review_doc.write_text("# Review\nVERDICT: FAIL\n", encoding="utf-8")

    return StepResult(
        status="ok",
        data={
            "raw_response": raw_response,
            "doc_path": str(sat_doc),
            "spec_path": str(scratch / "specs" / "build-spec.md"),
            "review_doc_path": str(review_doc),
            "fix_doc_path": str(scratch / "reviews" / "build-fix.md"),
        },
        duration_ms=100,
        step_name="invoke_satisfaction_llm",
    )


def _suppress_emit(monkeypatch) -> list[tuple]:
    """Suppress _emit_safe calls and capture them for optional assertion."""
    captured: list[tuple] = []
    monkeypatch.setattr(
        phase_6_review,
        "_emit_safe",
        lambda et, p, **kw: captured.append((et, p, kw)),
    )
    return captured


# ═══════════════════════════════════════════════════════════════════════════════
# AC1 — score=80, threshold=85, structured.satisfied=True → fail-closed (the #591 case)
# ═══════════════════════════════════════════════════════════════════════════════


def test_strict_and_drift_structured_pass_score_below_fails_closed_9702b73f(
    tmp_path: Path, monkeypatch
) -> None:
    """AC1: score=80 < threshold=85 but structured.satisfied=True → gate must fail-closed.

    Current main: passes (structured overrides score). POST-fix: status=error.
    FAILS on current main because line 2494 lets structured.satisfied=True win unconditionally.
    """
    captured = _suppress_emit(monkeypatch)
    ctx = _make_ctx(tmp_path, satisfaction_threshold=85)
    raw = _raw_with_score("SCORE: 80", _VALID_BLOCK_PASS)
    prev = _make_satisfaction_prev(tmp_path, raw)

    result = _write_satisfaction_doc(ctx, prev)

    assert result.status == "error", (
        f"Expected error (drift fail-closed) but got status={result.status!r}"
    )
    assert result.error_code == "E_SATISFACTION_BELOW_THRESHOLD"
    assert "drift" in result.error.lower(), f"Expected 'drift' in error: {result.error!r}"
    assert "fail-closed" in result.error.lower(), f"Expected 'fail-closed' in error: {result.error!r}"

    # Drift event must carry gate_decision="fail-closed"
    drift_events = [e for e in captured if e[0] == "satisfaction_verdict_drift"]
    assert drift_events, "Expected satisfaction_verdict_drift event to be emitted"
    drift_payload = drift_events[0][1]
    assert drift_payload.get("gate_decision") == "fail-closed", (
        f"Expected gate_decision='fail-closed' in drift payload: {drift_payload!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC2 — score=90, threshold=85, structured.satisfied=True → concurring pass
# ═══════════════════════════════════════════════════════════════════════════════


def test_strict_and_concurring_pass_returns_ok_9702b73f(
    tmp_path: Path, monkeypatch
) -> None:
    """AC2: score=90 >= threshold=85 AND structured.satisfied=True → both concur, pass.

    Current main: also passes (for the wrong reason). POST-fix: still ok (same outcome).
    This test will PASS on current main — it is a correctness guard protecting post-GREEN behavior.
    Included per spec to confirm the happy path survives the fix.
    """
    captured = _suppress_emit(monkeypatch)
    ctx = _make_ctx(tmp_path, satisfaction_threshold=85)
    raw = _raw_with_score("SCORE: 90", _VALID_BLOCK_PASS)
    prev = _make_satisfaction_prev(tmp_path, raw)

    result = _write_satisfaction_doc(ctx, prev)

    assert result.status == "ok", (
        f"Expected ok (concurring pass) but got status={result.status!r}, "
        f"error={getattr(result, 'error', None)!r}"
    )

    # No drift event when signals agree
    drift_events = [e for e in captured if e[0] == "satisfaction_verdict_drift"]
    assert not drift_events, (
        f"Expected NO satisfaction_verdict_drift event on concurring pass, got: {drift_events!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC3 — score=70, threshold=85, structured.satisfied=False → concurring fail, no drift event
# ═══════════════════════════════════════════════════════════════════════════════


def test_strict_and_concurring_fail_no_drift_event_9702b73f(
    tmp_path: Path, monkeypatch
) -> None:
    """AC3: score=70 < threshold=85 AND structured.satisfied=False → both concur on fail.

    No drift → no drift event. Error must reference fix count.
    Current main: also errors (structured.satisfied=False dominates). Guard for post-GREEN.
    """
    captured = _suppress_emit(monkeypatch)
    ctx = _make_ctx(tmp_path, satisfaction_threshold=85)
    raw = _raw_fail_with_score("SCORE: 70", _VALID_BLOCK_FAIL)
    prev = _make_satisfaction_prev(tmp_path, raw)

    result = _write_satisfaction_doc(ctx, prev)

    assert result.status == "error", (
        f"Expected error (concurring fail) but got status={result.status!r}"
    )
    # Error should reference fixes (structured block has 1 fix)
    assert "1" in result.error or "fix" in result.error.lower(), (
        f"Expected fix count reference in error: {result.error!r}"
    )

    # No drift event when signals agree
    drift_events = [e for e in captured if e[0] == "satisfaction_verdict_drift"]
    assert not drift_events, (
        f"Expected NO satisfaction_verdict_drift event on concurring fail, got: {drift_events!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC4 — score=90, threshold=85, structured.satisfied=False → reverse-direction drift, fail-closed
# ═══════════════════════════════════════════════════════════════════════════════


def test_strict_and_drift_structured_fail_score_above_fails_closed_9702b73f(
    tmp_path: Path, monkeypatch
) -> None:
    """AC4: score=90 >= threshold=85 but structured.satisfied=False → reverse drift, fail-closed.

    Current main: errors (structured.satisfied=False wins). POST-fix: still error, but now
    the error message must contain 'drift' AND 'fail-closed', and the drift event must fire
    with gate_decision='fail-closed'.
    FAILS on current main because the error message doesn't contain 'drift'/'fail-closed'
    and the drift event is not emitted (no drift detected in current logic for this direction).
    """
    captured = _suppress_emit(monkeypatch)
    ctx = _make_ctx(tmp_path, satisfaction_threshold=85)
    raw = _raw_fail_with_score("SCORE: 90", _VALID_BLOCK_FAIL)
    prev = _make_satisfaction_prev(tmp_path, raw)

    result = _write_satisfaction_doc(ctx, prev)

    assert result.status == "error", (
        f"Expected error (reverse drift fail-closed) but got status={result.status!r}"
    )
    assert result.error_code == "E_SATISFACTION_BELOW_THRESHOLD"
    assert "drift" in result.error.lower(), f"Expected 'drift' in error: {result.error!r}"
    assert "fail-closed" in result.error.lower(), f"Expected 'fail-closed' in error: {result.error!r}"

    # Drift event must carry gate_decision="fail-closed"
    drift_events = [e for e in captured if e[0] == "satisfaction_verdict_drift"]
    assert drift_events, "Expected satisfaction_verdict_drift event to be emitted"
    drift_payload = drift_events[0][1]
    assert drift_payload.get("gate_decision") == "fail-closed", (
        f"Expected gate_decision='fail-closed' in drift payload: {drift_payload!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC5 — score=None, structured.satisfied=True → omitted SCORE, fail-closed
# ═══════════════════════════════════════════════════════════════════════════════


def test_strict_and_structured_present_score_missing_fails_closed_9702b73f(
    tmp_path: Path, monkeypatch
) -> None:
    """AC5: structured.satisfied=True but SCORE line absent → fail-closed (can't verify numerically).

    Current main: passes (structured.satisfied=True wins with no score check).
    POST-fix: status=error, error mentions 'omitted SCORE' and 'fail-closed'.
    FAILS on current main because missing SCORE + structured.satisfied=True currently passes.
    """
    captured = _suppress_emit(monkeypatch)
    ctx = _make_ctx(tmp_path, satisfaction_threshold=85)
    # No SCORE line — _parse_satisfaction_score returns None
    raw = _raw_no_score_with_block(_VALID_BLOCK_PASS)
    prev = _make_satisfaction_prev(tmp_path, raw)

    result = _write_satisfaction_doc(ctx, prev)

    assert result.status == "error", (
        f"Expected error (omitted SCORE fail-closed) but got status={result.status!r}"
    )
    assert result.error_code == "E_SATISFACTION_BELOW_THRESHOLD"
    error_lower = result.error.lower()
    assert "omitted" in error_lower and "score" in error_lower, (
        f"Expected 'omitted SCORE' reference in error: {result.error!r}"
    )
    assert "fail-closed" in error_lower, f"Expected 'fail-closed' in error: {result.error!r}"

    # No drift event — only one signal present, nothing to disagree with
    drift_events = [e for e in captured if e[0] == "satisfaction_verdict_drift"]
    assert not drift_events, (
        f"Expected NO satisfaction_verdict_drift event (only one signal), got: {drift_events!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC6 — score=90, threshold=85, no structured block → markdown fallback preserved (BACKCOMPAT)
# ═══════════════════════════════════════════════════════════════════════════════


def test_strict_and_no_structured_score_above_returns_ok_backcompat_9702b73f(
    tmp_path: Path, monkeypatch
) -> None:
    """AC6: no structured block, score=90 >= threshold=85 → markdown fallback path, ok.

    Current main: passes. POST-fix: still passes (backcompat preserved).
    This test PASSES on current main — correctness guard that markdown-only path survives the fix.
    Included per spec to confirm BACKCOMPAT.
    """
    _suppress_emit(monkeypatch)
    ctx = _make_ctx(tmp_path, satisfaction_threshold=85)
    raw = _raw_with_score("SCORE: 90", None)  # No structured block
    prev = _make_satisfaction_prev(tmp_path, raw)

    result = _write_satisfaction_doc(ctx, prev)

    assert result.status == "ok", (
        f"Expected ok (no structured block, score above threshold) but got "
        f"status={result.status!r}, error={getattr(result, 'error', None)!r}"
    )


# ─── multi-eval helpers (copied from test_phase_6_satisfaction_multi_evaluator.py) ───

def _structured_block_multi(satisfied: bool, fixes: list | None = None) -> str:
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


def _raw_pass_multi(score: int, satisfied: bool = True, fixes: list | None = None) -> str:
    return "\n".join([
        "## Evaluation",
        f"SCORE: {score}",
        "VERDICT: PASS",
        "",
        _structured_block_multi(satisfied, fixes),
    ])


def _raw_fail_multi(score: int, satisfied: bool = False, fixes: list | None = None) -> str:
    if fixes is None:
        fixes = [{"file": "x.py", "issue": "missing test"}]
    return "\n".join([
        "## Evaluation",
        f"SCORE: {score}",
        "VERDICT: FAIL",
        "",
        _structured_block_multi(satisfied, fixes),
    ])


def _raw_no_score_struct_pass_multi(satisfied: bool = True) -> str:
    """LLM response with structured block but NO SCORE line."""
    return "\n".join([
        "## Evaluation",
        "VERDICT: PASS",
        "",
        _structured_block_multi(satisfied),
    ])


def _make_ctx_multi(
    tmp_path: Path,
    satisfaction_threshold: int = 85,
    complexity: str | None = "COMPLEX",
) -> WorkflowContext:
    scratch = tmp_path / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    cfg: dict = {
        "scratchpad_dir": str(scratch),
        "satisfaction_threshold": satisfaction_threshold,
    }
    if complexity is not None:
        cfg["complexity"] = complexity
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=cfg,
        question="add feature X",
        session_id="test-9702B73F-multi",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_write_prev_multi_9702(
    tmp_path: Path,
    evaluator_responses: list[dict],
    first_ok_raw: str = "",
) -> StepResult:
    """Construct a prev StepResult for _write_satisfaction_doc multi-evaluator path."""
    scratch = tmp_path / "scratch"
    reviews_dir = scratch / "reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)
    sat_doc = reviews_dir / "build-satisfaction.md"
    review_doc = reviews_dir / "build-review.md"
    if not review_doc.exists():
        review_doc.write_text("# Review\nVERDICT: FAIL\n", encoding="utf-8")
    ok_raws = [
        e["raw_response"]
        for e in evaluator_responses
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


def _capture_emit_9702(monkeypatch) -> list[tuple]:
    """Capture _emit_safe calls for multi-eval tests — uses phase_6_review module ref."""
    captured: list[tuple] = []
    monkeypatch.setattr(
        phase_6_review,
        "_emit_safe",
        lambda et, payload, **kw: captured.append((et, payload, kw)),
    )
    return captured


# ═══════════════════════════════════════════════════════════════════════════════
# AC7 — _aggregate_satisfaction: evaluator drift is detected per-evaluator; drift that
#        leaves only 1 passer out of 3 flips majority_passed=False (gate FAIL).
# ═══════════════════════════════════════════════════════════════════════════════


def test_strict_and_per_eval_drift_structured_pass_score_below_majority_fails_9702b73f(
    tmp_path: Path, monkeypatch
) -> None:
    """AC7a: 3 evaluators (s=80,struct=T), (s=90,struct=T), (s=92,struct=T); threshold=85.

    Evaluator 0 is drift (score below threshold but struct passes) → passed=False,
    reason_code starts with 'drift_'; evaluators 1 and 2 concurring-pass → passed=True.
    passed_count==2; majority_passed=True (2*2=4 > 3).
    A satisfaction_verdict_drift event must be emitted for evaluator_index=0
    with gate_decision='fail-closed' and phase=6.

    FAILS until GREEN adds reason_code to per_eval and emits per-evaluator drift events.
    """
    _aggregate_satisfaction = getattr(phase_6_review, "_aggregate_satisfaction", None)
    assert _aggregate_satisfaction is not None, (
        "AC7a FAIL: _aggregate_satisfaction not found in phase_6_review"
    )

    captured = _capture_emit_9702(monkeypatch)

    sv_pass = SatisfactionVerdict(satisfied=True, fixes_required=[])
    evals = [
        {"index": 0, "score": 80, "structured": sv_pass, "status": "ok", "error_code": None},
        {"index": 1, "score": 90, "structured": sv_pass, "status": "ok", "error_code": None},
        {"index": 2, "score": 92, "structured": sv_pass, "status": "ok", "error_code": None},
    ]
    result = _aggregate_satisfaction(evals, threshold=85)

    per_eval = result["per_eval"]
    assert len(per_eval) == 3

    # Evaluator 0: drift (score 80 < threshold 85, struct satisfied=True)
    assert per_eval[0]["passed"] is False, (
        f"AC7a: evaluator 0 (s=80, struct=T) should be drift→passed=False; "
        f"got {per_eval[0]['passed']!r}"
    )
    assert "reason_code" in per_eval[0], (
        "AC7a FAIL: per_eval[0] missing 'reason_code' — not implemented yet (9702B73F)"
    )
    assert per_eval[0]["reason_code"].startswith("drift_"), (
        f"AC7a: per_eval[0] reason_code must start with 'drift_'; "
        f"got {per_eval[0].get('reason_code')!r}"
    )

    # Evaluators 1 and 2: concurring pass
    assert per_eval[1]["passed"] is True, (
        f"AC7a: evaluator 1 (s=90, struct=T, thresh=85) should be concurring_pass; "
        f"got {per_eval[1]['passed']!r}"
    )
    assert per_eval[2]["passed"] is True, (
        f"AC7a: evaluator 2 (s=92, struct=T) should be concurring_pass; "
        f"got {per_eval[2]['passed']!r}"
    )

    assert result["passed_count"] == 2, (
        f"AC7a: passed_count expected 2; got {result['passed_count']}"
    )
    assert result["majority_passed"] is True, (
        f"AC7a: majority_passed expected True (2*2=4>3); got {result['majority_passed']!r}"
    )

    # Drift event must be emitted for evaluator 0
    drift_events = [p for et, p, _ in captured if et == "satisfaction_verdict_drift"]
    assert len(drift_events) >= 1, (
        "AC7a FAIL: expected at least one satisfaction_verdict_drift event; none emitted"
    )
    ev0 = next(
        (e for e in drift_events if e.get("evaluator_index") == 0),
        None,
    )
    assert ev0 is not None, (
        f"AC7a FAIL: no drift event with evaluator_index=0; got drift_events={drift_events!r}"
    )
    assert ev0.get("gate_decision") == "fail-closed", (
        f"AC7a FAIL: drift event evaluator_index=0 must have gate_decision='fail-closed'; "
        f"got {ev0!r}"
    )
    assert ev0.get("phase") == 6, (
        f"AC7a FAIL: drift event must carry phase=6; got {ev0!r}"
    )


def test_strict_and_per_eval_drift_tips_majority_fails_9702b73f(
    tmp_path: Path, monkeypatch
) -> None:
    """AC7b (tips-majority): 3 evaluators (s=80,struct=T), (s=82,struct=T), (s=92,struct=T);
    threshold=85.

    Evaluators 0 and 1 are both drift → passed=False each;
    evaluator 2 is concurring_pass → passed=True.
    passed_count==1; majority_passed=False (1*2=2 ≤ 3 → fail-closed).
    Two satisfaction_verdict_drift events emitted with evaluator_index in {0, 1}.

    FAILS until GREEN adds reason_code + per-evaluator drift emit to _aggregate_satisfaction.
    """
    _aggregate_satisfaction = getattr(phase_6_review, "_aggregate_satisfaction", None)
    assert _aggregate_satisfaction is not None, (
        "AC7b FAIL: _aggregate_satisfaction not found in phase_6_review"
    )

    captured = _capture_emit_9702(monkeypatch)

    sv_pass = SatisfactionVerdict(satisfied=True, fixes_required=[])
    evals = [
        {"index": 0, "score": 80, "structured": sv_pass, "status": "ok", "error_code": None},
        {"index": 1, "score": 82, "structured": sv_pass, "status": "ok", "error_code": None},
        {"index": 2, "score": 92, "structured": sv_pass, "status": "ok", "error_code": None},
    ]
    result = _aggregate_satisfaction(evals, threshold=85)

    per_eval = result["per_eval"]
    assert len(per_eval) == 3

    assert per_eval[0]["passed"] is False, (
        f"AC7b: evaluator 0 (s=80) should be drift→passed=False; got {per_eval[0]['passed']!r}"
    )
    assert "reason_code" in per_eval[0], (
        "AC7b FAIL: per_eval[0] missing 'reason_code' — not implemented (9702B73F)"
    )
    assert per_eval[0]["reason_code"].startswith("drift_"), (
        f"AC7b: per_eval[0] reason_code must start with 'drift_'; "
        f"got {per_eval[0].get('reason_code')!r}"
    )

    assert per_eval[1]["passed"] is False, (
        f"AC7b: evaluator 1 (s=82) should be drift→passed=False; got {per_eval[1]['passed']!r}"
    )
    assert "reason_code" in per_eval[1], (
        "AC7b FAIL: per_eval[1] missing 'reason_code' — not implemented (9702B73F)"
    )
    assert per_eval[1]["reason_code"].startswith("drift_"), (
        f"AC7b: per_eval[1] reason_code must start with 'drift_'; "
        f"got {per_eval[1].get('reason_code')!r}"
    )

    assert per_eval[2]["passed"] is True, (
        f"AC7b: evaluator 2 (s=92) should be concurring_pass; got {per_eval[2]['passed']!r}"
    )

    assert result["passed_count"] == 1, (
        f"AC7b: passed_count expected 1 (only evaluator 2 passes); got {result['passed_count']}"
    )
    assert result["majority_passed"] is False, (
        f"AC7b: majority_passed expected False (1*2=2 ≤ 3); got {result['majority_passed']!r}"
    )

    drift_events = [p for et, p, _ in captured if et == "satisfaction_verdict_drift"]
    drift_indices = {e.get("evaluator_index") for e in drift_events}
    assert 0 in drift_indices, (
        f"AC7b FAIL: no drift event for evaluator_index=0; drift_events={drift_events!r}"
    )
    assert 1 in drift_indices, (
        f"AC7b FAIL: no drift event for evaluator_index=1; drift_events={drift_events!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC8 — _aggregate_satisfaction: all concurring-pass, no drift events
# ═══════════════════════════════════════════════════════════════════════════════


def test_strict_and_per_eval_concurring_pass_no_drift_events_9702b73f(
    tmp_path: Path, monkeypatch
) -> None:
    """AC8: 3 evaluators scores 90/92/88, structured.satisfied=True; threshold=80.

    All concurring-pass → every per_eval passed=True, reason_code=='concurring_pass';
    majority_passed=True; NO satisfaction_verdict_drift events emitted.

    FAILS today because per_eval doesn't have reason_code key.
    """
    _aggregate_satisfaction = getattr(phase_6_review, "_aggregate_satisfaction", None)
    assert _aggregate_satisfaction is not None, (
        "AC8 FAIL: _aggregate_satisfaction not found in phase_6_review"
    )

    captured = _capture_emit_9702(monkeypatch)

    sv_pass = SatisfactionVerdict(satisfied=True, fixes_required=[])
    evals = [
        {"index": 0, "score": 90, "structured": sv_pass, "status": "ok", "error_code": None},
        {"index": 1, "score": 92, "structured": sv_pass, "status": "ok", "error_code": None},
        {"index": 2, "score": 88, "structured": sv_pass, "status": "ok", "error_code": None},
    ]
    result = _aggregate_satisfaction(evals, threshold=80)

    per_eval = result["per_eval"]
    for i, pe in enumerate(per_eval):
        assert pe["passed"] is True, (
            f"AC8: evaluator {i} should be concurring_pass→passed=True; got {pe['passed']!r}"
        )
        assert "reason_code" in pe, (
            f"AC8 FAIL: per_eval[{i}] missing 'reason_code' — not implemented (9702B73F)"
        )
        assert pe["reason_code"] == "concurring_pass", (
            f"AC8: per_eval[{i}] reason_code must be 'concurring_pass'; "
            f"got {pe.get('reason_code')!r}"
        )

    assert result["majority_passed"] is True, (
        f"AC8: majority_passed expected True; got {result['majority_passed']!r}"
    )

    drift_events = [p for et, p, _ in captured if et == "satisfaction_verdict_drift"]
    assert len(drift_events) == 0, (
        f"AC8 FAIL: expected NO drift events on all-concurring-pass; got {drift_events!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC9 — _write_satisfaction_doc_multi n_valid==1 survivor drift → fail-closed
# ═══════════════════════════════════════════════════════════════════════════════


def test_strict_and_multi_eval_n_valid_1_survivor_drift_fails_closed_9702b73f(
    tmp_path: Path, monkeypatch
) -> None:
    """AC9: multi-eval, 2 errored + 1 valid survivor that drifts (score=70, struct=T, threshold=85).

    n_valid==1 single-eval semantics: _decide_satisfaction_passed sees drift → fail-closed.
    result.status == 'error'; error contains 'drift' AND 'fail-closed';
    one satisfaction_verdict_drift event with evaluator_index==2 (survivor's index),
    gate_decision='fail-closed', phase=6.

    FAILS today because n_valid==1 path uses old `passed = bool(structured_s.satisfied)` (line 2807).
    """
    captured = _capture_emit_9702(monkeypatch)
    ctx = _make_ctx_multi(tmp_path, satisfaction_threshold=85)

    # evaluators 0&1 errored; evaluator 2 is the surviving valid drift case
    drift_raw = _raw_pass_multi(score=70, satisfied=True)  # score 70 < threshold 85
    evaluator_responses = [
        {"index": 0, "status": "error", "raw_response": "", "error_code": "E_LLM_TIMEOUT"},
        {"index": 1, "status": "error", "raw_response": "", "error_code": "E_LLM_TIMEOUT"},
        {"index": 2, "status": "ok", "raw_response": drift_raw, "error_code": None},
    ]
    prev = _make_write_prev_multi_9702(tmp_path, evaluator_responses)

    result = _write_satisfaction_doc(ctx, prev)

    assert result.status == "error", (
        f"AC9 FAIL: expected error (n_valid=1 survivor drift → fail-closed); "
        f"got {result.status!r}"
    )
    assert result.error_code == "E_REVIEW_DEGRADED", (
        f"AC9 FAIL: expected error_code='E_REVIEW_DEGRADED' (degrade preempts strict-AND drift per F81D5EF7); "
        f"got {result.error_code!r}"
    )

    drift_events = [p for et, p, _ in captured if et == "satisfaction_verdict_drift"]
    assert len(drift_events) >= 1, (
        f"AC9 FAIL: expected at least one satisfaction_verdict_drift event; none emitted"
    )
    ev = next(
        (e for e in drift_events if e.get("evaluator_index") == 2),
        None,
    )
    assert ev is not None, (
        f"AC9 FAIL: no drift event with evaluator_index=2 (survivor); "
        f"drift_events={drift_events!r}"
    )
    assert ev.get("gate_decision") == "fail-closed", (
        f"AC9 FAIL: drift event must have gate_decision='fail-closed'; got {ev!r}"
    )
    assert ev.get("phase") == 6, (
        f"AC9 FAIL: drift event must carry phase=6; got {ev!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC10 — _aggregate_satisfaction: score=None structured=True → structured_only_no_score,
#         no 100-fallback inflation in eff, median_score=None
# ═══════════════════════════════════════════════════════════════════════════════


def test_strict_and_aggregate_eff_excludes_score_none_no_100_inflation_9702b73f(
    tmp_path: Path, monkeypatch
) -> None:
    """AC10: 3 evaluators all score=None, structured.satisfied=True; threshold=85.

    Under new policy: reason_code='structured_only_no_score' → passed=False for all three.
    eff list is empty (no score is not None) → median_score=None.
    majority_passed=False (0 pass of 3).
    NO satisfaction_verdict_drift events (no two-values to disagree).

    FAILS today because current eff uses `100 if satisfied else 0` fallback → median=100
    and passed_count=3 under old policy; with strict-AND the new policy flips all to False
    and drops them from eff entirely.
    """
    _aggregate_satisfaction = getattr(phase_6_review, "_aggregate_satisfaction", None)
    assert _aggregate_satisfaction is not None, (
        "AC10 FAIL: _aggregate_satisfaction not found in phase_6_review"
    )

    captured = _capture_emit_9702(monkeypatch)

    sv_pass = SatisfactionVerdict(satisfied=True, fixes_required=[])
    evals = [
        {"index": 0, "score": None, "structured": sv_pass, "status": "ok", "error_code": None},
        {"index": 1, "score": None, "structured": sv_pass, "status": "ok", "error_code": None},
        {"index": 2, "score": None, "structured": sv_pass, "status": "ok", "error_code": None},
    ]
    result = _aggregate_satisfaction(evals, threshold=85)

    assert result["n_valid"] == 3, (
        f"AC10: n_valid expected 3 (structured present counts as valid signal); "
        f"got {result['n_valid']}"
    )

    per_eval = result["per_eval"]
    for i, pe in enumerate(per_eval):
        assert pe["passed"] is False, (
            f"AC10: evaluator {i} (score=None, struct=T) should be structured_only_no_score→False; "
            f"got {pe['passed']!r}"
        )
        assert "reason_code" in pe, (
            f"AC10 FAIL: per_eval[{i}] missing 'reason_code' — not implemented (9702B73F)"
        )
        assert pe["reason_code"] == "structured_only_no_score", (
            f"AC10: per_eval[{i}] reason_code must be 'structured_only_no_score'; "
            f"got {pe.get('reason_code')!r}"
        )

    assert result["median_score"] is None, (
        f"AC10 FAIL: median_score must be None when eff list is empty (no scores present); "
        f"got {result['median_score']!r} — 100-fallback inflation not removed"
    )

    assert result["majority_passed"] is False, (
        f"AC10: majority_passed expected False (0 pass of 3); got {result['majority_passed']!r}"
    )

    drift_events = [p for et, p, _ in captured if et == "satisfaction_verdict_drift"]
    assert len(drift_events) == 0, (
        f"AC10 FAIL: expected NO drift events (only one signal per evaluator); "
        f"got {drift_events!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC11 — _decide_satisfaction_passed: unit test of all 7 reason_codes
# ═══════════════════════════════════════════════════════════════════════════════


def test_strict_and_decide_satisfaction_passed_helper_all_reason_codes_9702b73f(
    tmp_path: Path,
) -> None:
    """AC11: direct unit test of _decide_satisfaction_passed helper.

    All 7 reason_codes must be returned correctly:
      concurring_pass, concurring_fail,
      drift_structured_pass_score_below, drift_structured_fail_score_above,
      structured_only_no_score, score_only_below_threshold, no_signals.

    FAILS until GREEN implements _decide_satisfaction_passed in phase_6_review.
    """
    _decide = getattr(phase_6_review, "_decide_satisfaction_passed", None)
    assert _decide is not None, (
        "AC11 FAIL: _decide_satisfaction_passed not found in phase_6_review — "
        "helper not implemented yet (9702B73F)"
    )

    sv_true = SatisfactionVerdict(satisfied=True, fixes_required=[])
    sv_false = SatisfactionVerdict(satisfied=False, fixes_required=[{"file": "x.py", "issue": "y"}])
    threshold = 85

    # 1. concurring_pass: both signals agree PASS
    passed, rc = _decide(sv_true, 90, threshold)
    assert passed is True and rc == "concurring_pass", (
        f"AC11: (struct=T, score=90, t=85) → expected (True, 'concurring_pass'); got ({passed!r}, {rc!r})"
    )

    # 2. concurring_fail: both signals agree FAIL
    passed, rc = _decide(sv_false, 70, threshold)
    assert passed is False and rc == "concurring_fail", (
        f"AC11: (struct=F, score=70, t=85) → expected (False, 'concurring_fail'); got ({passed!r}, {rc!r})"
    )

    # 3. drift_structured_pass_score_below: struct=T, score below threshold
    passed, rc = _decide(sv_true, 80, threshold)
    assert passed is False and rc == "drift_structured_pass_score_below", (
        f"AC11: (struct=T, score=80, t=85) → expected (False, 'drift_structured_pass_score_below'); "
        f"got ({passed!r}, {rc!r})"
    )

    # 4. drift_structured_fail_score_above: struct=F, score above threshold
    passed, rc = _decide(sv_false, 90, threshold)
    assert passed is False and rc == "drift_structured_fail_score_above", (
        f"AC11: (struct=F, score=90, t=85) → expected (False, 'drift_structured_fail_score_above'); "
        f"got ({passed!r}, {rc!r})"
    )

    # 5. structured_only_no_score: struct present, score=None
    passed, rc = _decide(sv_true, None, threshold)
    assert passed is False and rc == "structured_only_no_score", (
        f"AC11: (struct=T, score=None, t=85) → expected (False, 'structured_only_no_score'); "
        f"got ({passed!r}, {rc!r})"
    )

    # 6. score_only_below_threshold: no struct, score below threshold
    passed, rc = _decide(None, 70, threshold)
    assert passed is False and rc == "score_only_below_threshold", (
        f"AC11: (struct=None, score=70, t=85) → expected (False, 'score_only_below_threshold'); "
        f"got ({passed!r}, {rc!r})"
    )

    # 7. no_signals: no struct, no score
    passed, rc = _decide(None, None, threshold)
    assert passed is False and rc == "no_signals", (
        f"AC11: (struct=None, score=None, t=85) → expected (False, 'no_signals'); "
        f"got ({passed!r}, {rc!r})"
    )
