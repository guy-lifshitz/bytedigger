"""RED tests for GH419 — frozen-spec REVISE in-process retry (not a dead-end).

Spec: SHARED/memory/Decisions/2026-07-08_GH419_frozen_revise_inprocess_retry_spec.md

Target: `_gate_on_review` (workflows/phase_45_spec.py), the `if prev.data.get("is_frozen"):`
branch. Today it returns a bare StepResult with data={"frozen_fallback": True,
"findings": raw_review} and NO "retry_from_step" — so the engine's in-process
retry hook (engine.py:379-383) never fires. GREEN adds "retry_from_step": 0 and
"cycle_count": cycle to that data dict.

All symbols under test already exist in production — plain top-level imports
are safe (§1q / D1CF5FDF: no not-yet-existing symbol imported at module level).
sys.path idiom copied from test_engine_retry_data_forwarding.py / test_E6602155_frozen_spec_ingest.py.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "workflows"))

from contracts import (  # noqa: E402
    StepContract,
    StepResult,
    WorkflowContext,
    WorkflowDefinition,
)
from engine import WorkflowEngine  # noqa: E402
import phase_45_spec as _p45  # noqa: E402
from phase_45_spec import (  # noqa: E402
    VERDICT_REVISE,
    VERDICT_SHIP,
    _gate_on_review,
)


# ─── shared fixtures ───────────────────────────────────────────────────────────

# A frozen decision doc needs BOTH an AC-table heading AND the preflight marker
# (skip_logic.is_frozen_spec_text). Content below is a generic filled-in template.
_FROZEN_DOC_BODY = (
    "# A Feature Spec\n"
    "\n"
    "## Context\n"
    "Ratified before build.\n"
    "\n"
    "## §3 Acceptance Criteria\n"
    "| # | AC | Forcing-function |\n"
    "|---|----|------------------|\n"
    "| 1 | thing works | unit test |\n"
    "\n"
    "§1-PREFLIGHT self-audit passed.\n"
)


def _make_ctx(*, org_extra: dict[str, Any] | None = None) -> WorkflowContext:
    org: dict[str, Any] = dict(org_extra or {})
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question="q",
        session_id="test-gh419",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_gate_prev(
    tmp_path: Path,
    *,
    cycle: int = 1,
    is_frozen: bool = True,
    raw_review: str = "## Verdict\nREVISE\n## Findings\n1. needs rework\n",
    verdict: str = VERDICT_REVISE,
) -> StepResult:
    review_path = tmp_path / "build-plan-review.md"
    review_path.write_text(raw_review)
    spec_path = tmp_path / "build-spec.md"
    spec_path.write_text(_FROZEN_DOC_BODY if is_frozen else "## Context\nstub\n")
    return StepResult(
        status="ok",
        data={
            "verdict": verdict,
            "cycle": cycle,
            "review_path": str(review_path),
            "spec_path": str(spec_path),
            "review_raw": raw_review,
            "is_frozen": is_frozen,
        },
        duration_ms=0,
        step_name="write_review_doc",
    )


# ─── AC1: retry_from_step == 0 present ─────────────────────────────────────────


def test_ac1_frozen_revise_data_has_retry_from_step_zero(tmp_path):
    """AC1: frozen REVISE StepResult.data contains retry_from_step == 0.

    FAILS TODAY: the is_frozen branch (phase_45_spec.py:2340-2354) returns
    data={"frozen_fallback": True, "findings": raw_review} — no retry_from_step key.
    """
    ctx = _make_ctx()
    prev = _make_gate_prev(tmp_path, cycle=1, is_frozen=True)

    result = _gate_on_review(ctx, prev)

    assert isinstance(result.data, dict), f"expected dict data, got {result.data!r}"
    assert "retry_from_step" in result.data, (
        f"AC1 FAIL: 'retry_from_step' missing from frozen-REVISE data: {result.data!r}"
    )
    assert result.data["retry_from_step"] == 0, (
        f"AC1 FAIL: expected retry_from_step == 0, got {result.data['retry_from_step']!r}"
    )


# ─── AC2: cycle_count threaded, not hardcoded ──────────────────────────────────


def test_ac2_frozen_revise_cycle_count_threaded_not_hardcoded(tmp_path):
    """AC2: cycle_count in the returned data equals the incoming cycle (2), not a
    hardcoded 1. FAILS TODAY: no cycle_count key at all in the frozen branch."""
    ctx = _make_ctx()
    prev = _make_gate_prev(tmp_path, cycle=2, is_frozen=True)

    result = _gate_on_review(ctx, prev)

    assert isinstance(result.data, dict)
    assert "cycle_count" in result.data, (
        f"AC2 FAIL: 'cycle_count' missing from frozen-REVISE data: {result.data!r}"
    )
    assert result.data["cycle_count"] == 2, (
        f"AC2 FAIL: expected cycle_count == 2 (threaded from prev.data['cycle']), "
        f"got {result.data['cycle_count']!r}"
    )


# ─── AC3: no regression of frozen_fallback / findings keys (E6602155) ──────────


def test_ac3_frozen_fallback_and_findings_keys_preserved(tmp_path):
    """AC3: regression guard — frozen_fallback is True and findings == raw_review
    are still present in the returned data (pre-existing E6602155 contract)."""
    ctx = _make_ctx()
    raw = "## Verdict\nREVISE\n## Findings\n1. more work needed\n"
    prev = _make_gate_prev(tmp_path, cycle=1, is_frozen=True, raw_review=raw)

    result = _gate_on_review(ctx, prev)

    assert result.data.get("frozen_fallback") is True, (
        f"AC3 FAIL: frozen_fallback must be True, got {result.data.get('frozen_fallback')!r}"
    )
    assert result.data.get("findings") == raw, (
        f"AC3 FAIL: findings must equal raw_review, got {result.data.get('findings')!r}"
    )


# ─── AC4: status/error_code/recoverable attributes ─────────────────────────────


def test_ac4_frozen_revise_status_error_code_recoverable(tmp_path):
    """AC4: recoverable is True, error_code == 'E_VALIDATION_RETRY', status == 'error'.
    This attribute triple is pre-existing (regression guard) — 'retry_from_step'
    threading (AC1) is the new part."""
    ctx = _make_ctx()
    prev = _make_gate_prev(tmp_path, cycle=1, is_frozen=True)

    result = _gate_on_review(ctx, prev)

    assert result.status == "error", f"AC4 FAIL: expected status=='error', got {result.status!r}"
    assert result.error_code == "E_VALIDATION_RETRY", (
        f"AC4 FAIL: expected error_code=='E_VALIDATION_RETRY', got {result.error_code!r}"
    )
    assert result.recoverable is True, (
        f"AC4 FAIL: expected recoverable is True, got {result.recoverable!r}"
    )


# ─── AC5: frozen_spec_fallback_to_full emitted exactly once ────────────────────


def test_ac5_fallback_event_emitted_once_with_payload(tmp_path, monkeypatch):
    """AC5: frozen_spec_fallback_to_full emitted exactly once with
    {phase, cycle, verdict}. Regression guard — pre-existing emit, unchanged by GREEN."""
    captured: list[tuple[str, dict]] = []
    monkeypatch.setattr(_p45, "_emit_safe", lambda et, p: captured.append((et, p)))

    ctx = _make_ctx()
    prev = _make_gate_prev(tmp_path, cycle=3, is_frozen=True, verdict=VERDICT_REVISE)

    _gate_on_review(ctx, prev)

    matches = [p for et, p in captured if et == "frozen_spec_fallback_to_full"]
    assert len(matches) == 1, (
        f"AC5 FAIL: expected exactly 1 'frozen_spec_fallback_to_full' event, "
        f"got {len(matches)}. Captured: {captured!r}"
    )
    payload = matches[0]
    assert payload.get("phase") == "phase_45_spec"
    assert payload.get("cycle") == 3
    assert payload.get("verdict") == VERDICT_REVISE


# ─── AC6: end-to-end through the real engine — in-process re-entry ─────────────


def _build_gh419_workflow(call_log: list[dict], tmp_path: Path) -> WorkflowDefinition:
    """Synthetic 2-step workflow mirroring §1y reachability trace:

    step0 ("frozen_counter"): mimics _step_detect_frozen_spec semantics — returns
        is_frozen=True on its FIRST invocation, is_frozen=False on later invocations
        (bounding the retry). Records (call_number, prev_shape) into call_log.
    step1 ("gate_on_review"): calls the REAL production `_gate_on_review`.
    """
    counter = {"n": 0}

    def step0_execute(ctx, prev):
        counter["n"] += 1
        prev_data = (
            prev.data if isinstance(prev, StepResult) and isinstance(prev.data, dict)
            else (prev if isinstance(prev, dict) else {})
        )
        call_log.append({"call_number": counter["n"], "prev_data": dict(prev_data)})

        is_frozen = counter["n"] == 1
        raw_review = "## Verdict\nREVISE\n## Findings\n1. rework needed\n"
        review_path = tmp_path / f"review_{counter['n']}.md"
        review_path.write_text(raw_review)
        spec_path = tmp_path / "build-spec.md"
        if not spec_path.exists():
            spec_path.write_text(_FROZEN_DOC_BODY)

        verdict = VERDICT_REVISE if counter["n"] == 1 else VERDICT_SHIP
        return StepResult(
            status="ok",
            data={
                "verdict": verdict,
                "cycle": prev_data.get("cycle", 1),
                "review_path": str(review_path),
                "spec_path": str(spec_path),
                "review_raw": raw_review,
                "is_frozen": is_frozen,
            },
            duration_ms=0,
            step_name="frozen_counter",
        )

    def step1_execute(ctx, prev):
        return _gate_on_review(ctx, prev)

    return WorkflowDefinition(
        name="gh419_e2e",
        steps=[
            StepContract(name="frozen_counter", execute=step0_execute),
            StepContract(name="gate_on_review", execute=step1_execute),
        ],
    )


def test_ac6_engine_reenters_step0_in_process_on_frozen_revise(tmp_path):
    """AC6: with the real WorkflowEngine, a frozen-REVISE from _gate_on_review
    causes the engine to re-enter step 0 in the SAME process (call count >= 2),
    and the second invocation's prev/initial_data carries frozen_fallback=True.

    FAILS TODAY: no 'retry_from_step' key in the frozen branch's data means the
    engine's retry-trigger condition (engine.py:379-383) never fires — step0
    is only ever called once.
    """
    call_log: list[dict] = []
    workflow = _build_gh419_workflow(call_log, tmp_path)

    engine = WorkflowEngine(event_log=None)
    engine.register("gh419_e2e", workflow)
    ctx = _make_ctx()

    engine.execute("gh419_e2e", ctx)

    assert len(call_log) >= 2, (
        f"AC6 FAIL: expected step0 ('frozen_counter') to be invoked >= 2 times "
        f"(in-process retry), got {len(call_log)} invocations: {call_log!r}"
    )
    second_entry = call_log[1]
    assert second_entry["prev_data"].get("frozen_fallback") is True, (
        f"AC6 FAIL: second invocation's prev/initial_data must carry "
        f"frozen_fallback=True, got {second_entry['prev_data']!r}"
    )


# ─── AC7: _step_detect_frozen_spec suppresses re-detection on fallback ─────────


def test_ac7_detect_frozen_spec_fallback_suppresses_redetection(tmp_path):
    """AC7 (consumer-contract regression guard): _step_detect_frozen_spec given a
    prev dict {frozen_fallback: True, cycle: 2} plus a frozen doc on disk still
    returns is_frozen=False (fallback suppresses re-detection). Pre-existing
    behavior in production (skip_logic.detect_frozen_spec + phase_45_spec.py:2476)."""
    frozen_doc = tmp_path / "frozen_decision_doc.md"
    frozen_doc.write_text(_FROZEN_DOC_BODY)

    ctx = _make_ctx(org_extra={"decision_doc": str(frozen_doc), "scratchpad_dir": str(tmp_path / "scratch")})
    prev = {"frozen_fallback": True, "cycle": 2}

    result = _p45._step_detect_frozen_spec(ctx, prev)

    assert isinstance(result.data, dict)
    assert result.data.get("is_frozen") is False, (
        f"AC7 FAIL: fallback flag must suppress re-detection, got "
        f"is_frozen={result.data.get('is_frozen')!r}, data={result.data!r}"
    )


# ─── AC8: non-frozen REVISE path unchanged (sibling regression) ───────────────


def test_ac8_non_frozen_revise_uses_gated_step_result_shape(tmp_path):
    """AC8: with is_frozen falsy, _gate_on_review REVISE still returns via
    gated_step_result — data has retry_from_step == 0 AND recoverable per the
    default SIMPLE policy. Sibling regression guard — this branch is untouched
    by the GH419 GREEN change (only the is_frozen branch is in scope)."""
    ctx = _make_ctx()
    prev = _make_gate_prev(tmp_path, cycle=1, is_frozen=False, verdict=VERDICT_REVISE)

    result = _gate_on_review(ctx, prev)

    assert isinstance(result.data, dict)
    assert result.data.get("retry_from_step") == 0, (
        f"AC8 FAIL: expected retry_from_step == 0 for non-frozen REVISE (SIMPLE policy), "
        f"got {result.data.get('retry_from_step')!r}"
    )
    assert result.recoverable is True, (
        f"AC8 FAIL: expected recoverable is True (retry outcome under default SIMPLE policy), "
        f"got {result.recoverable!r}"
    )
