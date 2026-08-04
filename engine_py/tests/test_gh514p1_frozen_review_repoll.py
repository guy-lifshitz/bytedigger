"""RED tests for GH514(1) — majority-of-3 re-poll on frozen-spec REVISE.

Spec: SHARED/memory/Decisions/2026-07-10_GH514P1_frozen_review_repoll_spec.md

UUT: ``_frozen_revise_repoll`` (does not exist yet) and the modified tail
of ``_invoke_review_llm`` in phase_45_spec.py, which is expected to call
the new helper on a frozen+REVISE first verdict.

Sibling pattern: test_llm_subprocess_hard_gate.py:366 (patch.object on the
module-namespace ``invoke_llm_subprocess`` symbol). ``invoke_llm_subprocess``
is never mocked at the UUT itself (_frozen_revise_repoll, _invoke_review_llm
are real) — only the subprocess-invoking leaf is stubbed (§1l).
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

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
    """side_effect callable stubbing invoke_llm_subprocess: first call
    returns raws_or_status[0], subsequent calls consume the rest in order."""
    it = iter(raws_or_status)

    def _invoke(**kwargs):
        item = next(it)
        if item == "ERROR":
            return _err()
        return _ok(_raw(item))

    return _invoke


# ─── AC1 ───────────────────────────────────────────────────────────────────


def test_ac1_frozen_revise_ship_ship_majority_returns_ship_and_three_invokes():
    """AC1: frozen + first REVISE + repolls [SHIP, SHIP] -> returned
    StepResult's raw_response is a SHIP raw; invoke called 3x total."""
    assert hasattr(p45, "_frozen_revise_repoll"), "_frozen_revise_repoll not implemented yet"
    calls = []

    def _invoke(**kwargs):
        calls.append(kwargs)
        seq = ["REVISE", "SHIP", "SHIP"]
        return _ok(_raw(seq[len(calls) - 1]))

    events = []
    with patch.object(p45, "invoke_llm_subprocess", side_effect=_invoke), \
         patch.object(p45, "_emit_safe", side_effect=lambda t, p: events.append((t, p))):
        result = p45._invoke_review_llm(_ctx(), _prev(is_frozen=True))

    assert len(calls) == 3, f"expected 3 invokes, got {len(calls)}"
    assert p45._parse_verdict(result.data["raw_response"]) == p45.VERDICT_SHIP


# ─── AC2 ───────────────────────────────────────────────────────────────────


def test_ac2_frozen_revise_ship_revise_no_majority_returns_first():
    """AC2: frozen + first REVISE + repolls [SHIP, REVISE] -> returns FIRST
    result (REVISE raw); 3 invokes total (1 SHIP of 3 is not a strict
    majority over revise)."""
    assert hasattr(p45, "_frozen_revise_repoll"), "_frozen_revise_repoll not implemented yet"
    calls = []

    def _invoke(**kwargs):
        calls.append(kwargs)
        seq = ["REVISE", "SHIP", "REVISE"]
        return _ok(_raw(seq[len(calls) - 1]))

    with patch.object(p45, "invoke_llm_subprocess", side_effect=_invoke), \
         patch.object(p45, "_emit_safe"):
        result = p45._invoke_review_llm(_ctx(), _prev(is_frozen=True))

    assert len(calls) == 3, f"expected 3 invokes, got {len(calls)}"
    assert p45._parse_verdict(result.data["raw_response"]) == p45.VERDICT_REVISE


# ─── AC3 ────────────────────────────────────────────────────────────────────


def test_ac3_frozen_first_ship_single_invoke_no_repoll_event():
    """AC3: frozen + first SHIP -> single invoke, result returned unchanged,
    no repoll event."""
    assert hasattr(p45, "_frozen_revise_repoll"), "_frozen_revise_repoll not implemented yet"
    calls = []
    events = []

    def _invoke(**kwargs):
        calls.append(kwargs)
        return _ok(_raw("SHIP"))

    with patch.object(p45, "invoke_llm_subprocess", side_effect=_invoke), \
         patch.object(p45, "_emit_safe", side_effect=lambda t, p: events.append((t, p))):
        result = p45._invoke_review_llm(_ctx(), _prev(is_frozen=True))

    assert len(calls) == 1, f"expected exactly 1 invoke, got {len(calls)}"
    assert p45._parse_verdict(result.data["raw_response"]) == p45.VERDICT_SHIP
    assert not [e for e in events if e[0] == "phase_45_spec_review_repoll"]


# ─── AC4 ────────────────────────────────────────────────────────────────────


def test_ac4_not_frozen_first_revise_single_invoke_no_repoll_event():
    """AC4: non-frozen with repoll disabled via kill-switch
    (spec_review_repolls=0) + first REVISE -> single invoke, unchanged, no
    repoll event."""
    assert hasattr(p45, "_frozen_revise_repoll"), "_frozen_revise_repoll not implemented yet"
    calls = []
    events = []

    def _invoke(**kwargs):
        calls.append(kwargs)
        return _ok(_raw("REVISE"))

    with patch.object(p45, "invoke_llm_subprocess", side_effect=_invoke), \
         patch.object(p45, "_emit_safe", side_effect=lambda t, p: events.append((t, p))):
        result = p45._invoke_review_llm(_ctx(spec_review_repolls=0), _prev(is_frozen=False))

    assert len(calls) == 1, f"expected exactly 1 invoke, got {len(calls)}"
    assert p45._parse_verdict(result.data["raw_response"]) == p45.VERDICT_REVISE
    assert not [e for e in events if e[0] == "phase_45_spec_review_repoll"]


# ─── AC5 ────────────────────────────────────────────────────────────────────


def test_ac5_kill_switch_zero_repolls_single_invoke_no_event():
    """AC5: spec_frozen_review_repolls=0 in org_config + frozen + REVISE ->
    single invoke, unchanged, no repoll event emitted from the helper loop
    (kill-switch early return)."""
    assert hasattr(p45, "_frozen_revise_repoll"), "_frozen_revise_repoll not implemented yet"
    calls = []
    events = []

    def _invoke(**kwargs):
        calls.append(kwargs)
        return _ok(_raw("REVISE"))

    ctx = _ctx(spec_frozen_review_repolls=0)
    with patch.object(p45, "invoke_llm_subprocess", side_effect=_invoke), \
         patch.object(p45, "_emit_safe", side_effect=lambda t, p: events.append((t, p))):
        result = p45._invoke_review_llm(ctx, _prev(is_frozen=True))

    assert len(calls) == 1, f"expected exactly 1 invoke (kill-switch), got {len(calls)}"
    assert p45._parse_verdict(result.data["raw_response"]) == p45.VERDICT_REVISE
    assert not [e for e in events if e[0] == "phase_45_spec_review_repoll"]


# ─── AC6 ────────────────────────────────────────────────────────────────────


def test_ac6_repoll_errors_swallowed_as_no_vote_returns_first():
    """AC6: frozen + first REVISE + repolls raise/error (status='error') x2
    -> returns first REVISE unchanged; votes recorded as
    ['REVISE','ERROR','ERROR'] in the emitted event."""
    assert hasattr(p45, "_frozen_revise_repoll"), "_frozen_revise_repoll not implemented yet"
    calls = []
    events = []

    def _invoke(**kwargs):
        calls.append(kwargs)
        if len(calls) == 1:
            return _ok(_raw("REVISE"))
        return _err()

    with patch.object(p45, "invoke_llm_subprocess", side_effect=_invoke), \
         patch.object(p45, "_emit_safe", side_effect=lambda t, p: events.append((t, p))):
        result = p45._invoke_review_llm(_ctx(), _prev(is_frozen=True))

    assert len(calls) == 2, f"expected 2 invokes, got {len(calls)}"
    assert p45._parse_verdict(result.data["raw_response"]) == p45.VERDICT_REVISE
    repoll_events = [e for e in events if e[0] == "phase_45_spec_review_repoll"]
    assert len(repoll_events) == 1
    assert repoll_events[0][1]["votes"] == ["REVISE", "ERROR"]


# ─── AC7 ────────────────────────────────────────────────────────────────────


def test_ac7_repoll_event_payload_keys_and_final_field():
    """AC7: phase_45_spec_review_repoll emitted exactly once with keys
    votes/final/n_repolls; final=='SHIP' for the AC1 shape,
    final=='REVISE' for the AC2/AC6 shapes."""
    assert hasattr(p45, "_frozen_revise_repoll"), "_frozen_revise_repoll not implemented yet"

    # AC1 shape -> final == SHIP
    calls = []
    events = []

    def _invoke_ship(**kwargs):
        calls.append(kwargs)
        seq = ["REVISE", "SHIP", "SHIP"]
        return _ok(_raw(seq[len(calls) - 1]))

    with patch.object(p45, "invoke_llm_subprocess", side_effect=_invoke_ship), \
         patch.object(p45, "_emit_safe", side_effect=lambda t, p: events.append((t, p))):
        p45._invoke_review_llm(_ctx(), _prev(is_frozen=True))

    repoll_events = [e for e in events if e[0] == "phase_45_spec_review_repoll"]
    assert len(repoll_events) == 1, f"expected exactly 1 repoll event, got {len(repoll_events)}"
    payload = repoll_events[0][1]
    for key in ("votes", "final", "n_repolls"):
        assert key in payload, f"repoll event missing key {key!r}: {payload!r}"
    assert payload["final"] == "SHIP"

    # AC2 shape -> final == REVISE
    calls2 = []
    events2 = []

    def _invoke_revise(**kwargs):
        calls2.append(kwargs)
        seq = ["REVISE", "SHIP", "REVISE"]
        return _ok(_raw(seq[len(calls2) - 1]))

    with patch.object(p45, "invoke_llm_subprocess", side_effect=_invoke_revise), \
         patch.object(p45, "_emit_safe", side_effect=lambda t, p: events2.append((t, p))):
        p45._invoke_review_llm(_ctx(), _prev(is_frozen=True))

    repoll_events2 = [e for e in events2 if e[0] == "phase_45_spec_review_repoll"]
    assert len(repoll_events2) == 1
    assert repoll_events2[0][1]["final"] == "REVISE"


# ─── AC8 ────────────────────────────────────────────────────────────────────


def test_ac8_unknown_repoll_raw_counts_as_revise_fail_closed():
    """AC8: UNKNOWN repoll raw (unparseable verdict) counts as REVISE:
    first REVISE + repolls [SHIP, UNKNOWN-garbage] -> first returned
    (1 SHIP vs 2 REVISE, no strict majority)."""
    assert hasattr(p45, "_frozen_revise_repoll"), "_frozen_revise_repoll not implemented yet"
    calls = []

    def _invoke(**kwargs):
        calls.append(kwargs)
        n = len(calls)
        if n == 1:
            return _ok(_raw("REVISE"))
        if n == 2:
            return _ok(_raw("SHIP"))
        return _ok("no verdict heading here, just garbage prose")

    with patch.object(p45, "invoke_llm_subprocess", side_effect=_invoke), \
         patch.object(p45, "_emit_safe"):
        result = p45._invoke_review_llm(_ctx(), _prev(is_frozen=True))

    assert len(calls) == 3
    assert p45._parse_verdict(result.data["raw_response"]) == p45.VERDICT_REVISE


# ─── AC9 ────────────────────────────────────────────────────────────────────


def test_ac9_hard_gate_kwargs_preserved_across_all_repoll_invokes():
    """AC9: hard-gate kwargs preserved: in the AC1 shape every invoke call
    received hard_gate=True and identical prompt/model as the first."""
    assert hasattr(p45, "_frozen_revise_repoll"), "_frozen_revise_repoll not implemented yet"
    calls = []

    def _invoke(**kwargs):
        calls.append(kwargs)
        seq = ["REVISE", "SHIP", "SHIP"]
        return _ok(_raw(seq[len(calls) - 1]))

    with patch.object(p45, "invoke_llm_subprocess", side_effect=_invoke), \
         patch.object(p45, "_emit_safe"):
        p45._invoke_review_llm(_ctx(), _prev(is_frozen=True))

    assert len(calls) == 3
    assert all(c.get("hard_gate") is True for c in calls), (
        f"hard_gate=True not preserved across all calls: {[c.get('hard_gate') for c in calls]!r}"
    )
    first_prompt, first_model = calls[0].get("prompt"), calls[0].get("model")
    for c in calls[1:]:
        assert c.get("prompt") == first_prompt
        assert c.get("model") == first_model
