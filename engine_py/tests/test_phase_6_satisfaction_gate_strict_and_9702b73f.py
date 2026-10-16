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

from bytedigger_engine.workflows import phase_6_review  # noqa: E402  (needed for monkeypatch target)
from bytedigger_engine.contracts import StepResult, WorkflowContext  # noqa: E402
from bytedigger_engine.workflows.phase_6_review import (  # noqa: E402
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


# (bd#317: the multi-evaluator cases AC7-AC10 were removed with the multi path;
#  the single-evaluator strict-AND cases above and the AC11 helper test below remain.)

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
