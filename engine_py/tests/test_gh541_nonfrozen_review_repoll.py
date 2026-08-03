"""RED tests for GH541 — majority re-poll extended to non-frozen REVISE.

Spec: SHARED/memory/Decisions/2026-07-10_GH541_nonfrozen_review_repoll_spec.md

UUT: real ``_invoke_review_llm`` / ``_frozen_revise_repoll`` in
phase_45_spec.py. Only ``invoke_llm_subprocess`` (leaf subprocess call) and
``_emit_safe`` (event sink) are mocked — never the UUT itself (§1l).

Sibling pattern: test_gh514p1_frozen_review_repoll.py.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

from bytedigger_engine.workflows import phase_45_spec as p45  # noqa: E402
from bytedigger_engine.contracts import StepResult  # noqa: E402


def _ctx(**cfg):
    """Minimal ctx stand-in — only org_config is read by _invoke_review_llm."""
    class _Ctx:
        org_config = cfg
    return _Ctx()


def _prev(is_frozen: bool, cycle: int = 1):
    return StepResult(
        status="ok",
        data={
            "prompt": "review this spec",
            "doc_path": "/tmp/review.md",
            "spec_path": "/tmp/spec.md",
            "cycle": cycle,
            "is_frozen": is_frozen,
        },
        duration_ms=0,
        step_name="build_review_prompt",
    )


def _raw(verdict: str) -> str:
    return f"## Verdict\n{verdict}\n"


def _ok(raw: str) -> StepResult:
    return StepResult(status="ok", data={"raw_response": raw}, duration_ms=0, step_name="invoke_review_llm")


def _err() -> StepResult:
    return StepResult(
        status="error", data=None, duration_ms=0, step_name="invoke_review_llm",
        error="boom", error_code="E_LLM_TRANSIENT", recoverable=True,
    )


def _scripted(raws_or_status):
    it = iter(raws_or_status)

    def _invoke(**kwargs):
        item = next(it)
        if item == "ERROR":
            return _err()
        return _ok(_raw(item))

    return _invoke


# ─── AC1 ───────────────────────────────────────────────────────────────────


def test_ac1_nonfrozen_revise_ship_ship_majority_returns_ship_and_three_invokes():
    """AC1: non-frozen + first REVISE + repolls [SHIP, SHIP] -> final SHIP;
    invoke called 3x total. Currently non-frozen never repolls -> 1 invoke,
    so this fails on current code."""
    calls = []

    def _invoke(**kwargs):
        calls.append(kwargs)
        seq = ["REVISE", "SHIP", "SHIP"]
        return _ok(_raw(seq[len(calls) - 1]))

    with patch.object(p45, "invoke_llm_subprocess", side_effect=_invoke), \
         patch.object(p45, "_emit_safe"):
        result = p45._invoke_review_llm(_ctx(), _prev(is_frozen=False))

    assert len(calls) == 3, f"expected 3 invokes, got {len(calls)}"
    assert p45._parse_verdict(result.data["raw_response"]) == p45.VERDICT_SHIP


# ─── AC2 ───────────────────────────────────────────────────────────────────


def test_ac2_nonfrozen_revise_ship_revise_no_majority_returns_first():
    """AC2: non-frozen + [REVISE, SHIP, REVISE] -> no strict majority ->
    returns first (REVISE); 3 invokes total."""
    calls = []

    def _invoke(**kwargs):
        calls.append(kwargs)
        seq = ["REVISE", "SHIP", "REVISE"]
        return _ok(_raw(seq[len(calls) - 1]))

    with patch.object(p45, "invoke_llm_subprocess", side_effect=_invoke), \
         patch.object(p45, "_emit_safe"):
        result = p45._invoke_review_llm(_ctx(), _prev(is_frozen=False))

    assert len(calls) == 3, f"expected 3 invokes, got {len(calls)}"
    assert p45._parse_verdict(result.data["raw_response"]) == p45.VERDICT_REVISE


# ─── AC3 ────────────────────────────────────────────────────────────────────


def test_ac3_nonfrozen_unknown_raw_counts_as_revise_fail_closed():
    """AC3: non-frozen + [REVISE, UNKNOWN-garbage, SHIP] -> UNKNOWN counts
    as REVISE -> no strict majority -> final REVISE."""
    calls = []

    def _invoke(**kwargs):
        calls.append(kwargs)
        n = len(calls)
        if n == 1:
            return _ok(_raw("REVISE"))
        if n == 2:
            return _ok("no verdict heading here, just garbage prose")
        return _ok(_raw("SHIP"))

    with patch.object(p45, "invoke_llm_subprocess", side_effect=_invoke), \
         patch.object(p45, "_emit_safe"):
        result = p45._invoke_review_llm(_ctx(), _prev(is_frozen=False))

    assert len(calls) == 2
    assert p45._parse_verdict(result.data["raw_response"]) == p45.VERDICT_REVISE


# ─── AC4 ────────────────────────────────────────────────────────────────────


def test_ac4_nonfrozen_first_ship_single_invoke_no_repoll_event():
    """AC4: non-frozen + first SHIP -> exactly 1 invoke (re-poll doesn't
    trigger on a SHIP verdict), no repoll event."""
    calls = []
    events = []

    def _invoke(**kwargs):
        calls.append(kwargs)
        return _ok(_raw("SHIP"))

    with patch.object(p45, "invoke_llm_subprocess", side_effect=_invoke), \
         patch.object(p45, "_emit_safe", side_effect=lambda t, p: events.append((t, p))):
        result = p45._invoke_review_llm(_ctx(), _prev(is_frozen=False))

    assert len(calls) == 1, f"expected exactly 1 invoke, got {len(calls)}"
    assert p45._parse_verdict(result.data["raw_response"]) == p45.VERDICT_SHIP
    assert not [e for e in events if e[0] == "phase_45_spec_review_repoll"]


# ─── AC5 ────────────────────────────────────────────────────────────────────


def test_ac5_nonfrozen_kill_switch_zero_repolls_single_invoke():
    """AC5: non-frozen + spec_review_repolls=0 + first REVISE -> kill-switch
    -> exactly 1 invoke, first result returned unchanged."""
    calls = []
    events = []

    def _invoke(**kwargs):
        calls.append(kwargs)
        return _ok(_raw("REVISE"))

    ctx = _ctx(spec_review_repolls=0)
    with patch.object(p45, "invoke_llm_subprocess", side_effect=_invoke), \
         patch.object(p45, "_emit_safe", side_effect=lambda t, p: events.append((t, p))):
        result = p45._invoke_review_llm(ctx, _prev(is_frozen=False))

    assert len(calls) == 1, f"expected exactly 1 invoke (kill-switch), got {len(calls)}"
    assert p45._parse_verdict(result.data["raw_response"]) == p45.VERDICT_REVISE
    assert not [e for e in events if e[0] == "phase_45_spec_review_repoll"]


# ─── AC6 ────────────────────────────────────────────────────────────────────


def test_ac6_nonfrozen_repoll_event_has_frozen_false_and_expected_fields():
    """AC6: non-frozen AC1 scenario emits phase_45_spec_review_repoll with
    frozen is False, votes == [REVISE, SHIP, SHIP], final == SHIP."""
    calls = []
    events = []

    def _invoke(**kwargs):
        calls.append(kwargs)
        seq = ["REVISE", "SHIP", "SHIP"]
        return _ok(_raw(seq[len(calls) - 1]))

    with patch.object(p45, "invoke_llm_subprocess", side_effect=_invoke), \
         patch.object(p45, "_emit_safe", side_effect=lambda t, p: events.append((t, p))):
        p45._invoke_review_llm(_ctx(), _prev(is_frozen=False))

    repoll_events = [e for e in events if e[0] == "phase_45_spec_review_repoll"]
    assert len(repoll_events) == 1, f"expected exactly 1 repoll event, got {len(repoll_events)}"
    payload = repoll_events[0][1]
    assert payload.get("frozen") is False, f"expected frozen is False, got {payload.get('frozen')!r}"
    assert payload["votes"] == ["REVISE", "SHIP", "SHIP"]
    assert payload["final"] == "SHIP"


# ─── AC7 ────────────────────────────────────────────────────────────────────


def test_ac7_frozen_path_still_uses_its_own_config_key_and_frozen_true():
    """AC7: frozen + [REVISE, SHIP, SHIP] with spec_frozen_review_repolls=2
    AND spec_review_repolls=0 -> final SHIP (frozen path reads its OWN key,
    not the non-frozen kill-switch key); event has frozen is True.
    Regression guard for GH514(1)."""
    calls = []
    events = []

    def _invoke(**kwargs):
        calls.append(kwargs)
        seq = ["REVISE", "SHIP", "SHIP"]
        return _ok(_raw(seq[len(calls) - 1]))

    ctx = _ctx(spec_frozen_review_repolls=2, spec_review_repolls=0)
    with patch.object(p45, "invoke_llm_subprocess", side_effect=_invoke), \
         patch.object(p45, "_emit_safe", side_effect=lambda t, p: events.append((t, p))):
        result = p45._invoke_review_llm(ctx, _prev(is_frozen=True))

    assert len(calls) == 3, f"expected 3 invokes (frozen key must NOT be zeroed by non-frozen key), got {len(calls)}"
    assert p45._parse_verdict(result.data["raw_response"]) == p45.VERDICT_SHIP
    repoll_events = [e for e in events if e[0] == "phase_45_spec_review_repoll"]
    assert len(repoll_events) == 1
    assert repoll_events[0][1].get("frozen") is True, (
        f"expected frozen is True, got {repoll_events[0][1].get('frozen')!r}"
    )


# ─── AC8 ────────────────────────────────────────────────────────────────────


def test_ac8_nonfrozen_error_does_not_vote_no_strict_majority():
    """AC8: non-frozen + [REVISE, ERROR, SHIP] -> ERROR does not vote ->
    votes contains 'ERROR'; 1 SHIP vs 1 REVISE is not strict majority ->
    final REVISE (first returned)."""
    calls = []
    events = []

    def _invoke(**kwargs):
        calls.append(kwargs)
        n = len(calls)
        if n == 1:
            return _ok(_raw("REVISE"))
        if n == 2:
            return _err()
        return _ok(_raw("SHIP"))

    with patch.object(p45, "invoke_llm_subprocess", side_effect=_invoke), \
         patch.object(p45, "_emit_safe", side_effect=lambda t, p: events.append((t, p))):
        result = p45._invoke_review_llm(_ctx(), _prev(is_frozen=False))

    assert len(calls) == 2
    assert p45._parse_verdict(result.data["raw_response"]) == p45.VERDICT_REVISE
    repoll_events = [e for e in events if e[0] == "phase_45_spec_review_repoll"]
    assert len(repoll_events) == 1
    assert "ERROR" in repoll_events[0][1]["votes"]
