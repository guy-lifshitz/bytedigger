"""RED tests for GH707 — phase-4.5 reviewer repoll: early-termination + cap.

Spec: SHARED/memory/Decisions/2026-07-13_GH707_reviewer_repoll_cap_spec.md

UUT: real ``_frozen_revise_repoll`` / ``_invoke_review_llm`` / (new)
``_ship_reachable`` in phase_45_spec.py. Only ``invoke_llm_subprocess`` (leaf
subprocess call) and ``_emit_safe`` (event sink) are mocked — never the UUT
itself (§1l). ``_ship_reachable`` does not exist yet on current code; it is
referenced only inside test bodies (never imported at module scope) so the
file COLLECTS cleanly and fails at assert-time, never at collection (§1q).

Sibling pattern: test_gh514p1_frozen_review_repoll.py.
"""
from __future__ import annotations

import inspect
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


# ─── AC1 ────────────────────────────────────────────────────────────────────


def test_ac1_happy_path_first_ship_frozen_single_invoke():
    """AC1: frozen + first SHIP -> exactly 1 spawn; verdict SHIP; no repoll
    event. Regression lock — already passes on current code."""
    calls = []
    events = []

    def _invoke(**kwargs):
        calls.append(kwargs)
        return _ok(_raw("SHIP"))

    with patch.object(p45, "invoke_llm_subprocess", side_effect=_invoke), \
         patch.object(p45, "_emit_safe", side_effect=lambda t, p: events.append((t, p))):
        result = p45._invoke_review_llm(_ctx(), _prev(is_frozen=True))

    assert len(calls) == 1, f"expected exactly 1 spawn, got {len(calls)}"
    assert p45._parse_verdict(result.data["raw_response"]) == p45.VERDICT_SHIP
    assert not [e for e in events if e[0] == "phase_45_spec_review_repoll"]


# ─── AC2 ────────────────────────────────────────────────────────────────────


def test_ac2_happy_path_first_ship_nonfrozen_single_invoke():
    """AC2: non-frozen + first SHIP -> exactly 1 spawn; verdict SHIP; no
    repoll event. Regression lock — already passes on current code."""
    calls = []
    events = []

    def _invoke(**kwargs):
        calls.append(kwargs)
        return _ok(_raw("SHIP"))

    with patch.object(p45, "invoke_llm_subprocess", side_effect=_invoke), \
         patch.object(p45, "_emit_safe", side_effect=lambda t, p: events.append((t, p))):
        result = p45._invoke_review_llm(_ctx(), _prev(is_frozen=False))

    assert len(calls) == 1, f"expected exactly 1 spawn, got {len(calls)}"
    assert p45._parse_verdict(result.data["raw_response"]) == p45.VERDICT_SHIP
    assert not [e for e in events if e[0] == "phase_45_spec_review_repoll"]


# ─── AC3 ────────────────────────────────────────────────────────────────────


def test_ac3_early_stop_first_revise_repoll1_revise_two_spawns():
    """AC3: first REVISE + repoll1 REVISE (n_repolls=2) -> SHIP unreachable
    after repoll1 -> break BEFORE the 3rd scripted SHIP is ever polled.
    Exactly 2 spawns total; final REVISE; votes==['REVISE','REVISE']."""
    calls = []
    events = []
    seq = ["REVISE", "REVISE", "SHIP"]  # 3rd item MUST never be consumed

    def _invoke(**kwargs):
        calls.append(kwargs)
        assert len(calls) <= 2, "early-stop did not fire: 3rd spawn happened"
        return _ok(_raw(seq[len(calls) - 1]))

    with patch.object(p45, "invoke_llm_subprocess", side_effect=_invoke), \
         patch.object(p45, "_emit_safe", side_effect=lambda t, p: events.append((t, p))):
        result = p45._invoke_review_llm(_ctx(), _prev(is_frozen=True))

    assert len(calls) == 2, f"expected exactly 2 spawns (early-stop), got {len(calls)}"
    assert p45._parse_verdict(result.data["raw_response"]) == p45.VERDICT_REVISE
    repoll_events = [e for e in events if e[0] == "phase_45_spec_review_repoll"]
    assert len(repoll_events) == 1
    assert repoll_events[0][1]["votes"] == ["REVISE", "REVISE"]


# ─── AC4 ────────────────────────────────────────────────────────────────────


def test_ac4_early_stop_on_error_first_revise_repoll1_error_two_spawns():
    """AC4: first REVISE + repoll1 ERROR (n_repolls=2) -> ERROR casts no
    vote, but SHIP is unreachable (0 ship + 0 remaining !> 1 revise) ->
    break before the 3rd scripted SHIP is polled. Exactly 2 spawns; 'ERROR'
    in votes; final REVISE."""
    calls = []
    events = []

    def _invoke(**kwargs):
        calls.append(kwargs)
        assert len(calls) <= 2, "early-stop did not fire: 3rd spawn happened"
        n = len(calls)
        if n == 1:
            return _ok(_raw("REVISE"))
        if n == 2:
            return _err()
        return _ok(_raw("SHIP"))

    with patch.object(p45, "invoke_llm_subprocess", side_effect=_invoke), \
         patch.object(p45, "_emit_safe", side_effect=lambda t, p: events.append((t, p))):
        result = p45._invoke_review_llm(_ctx(), _prev(is_frozen=True))

    assert len(calls) == 2, f"expected exactly 2 spawns (early-stop on ERROR), got {len(calls)}"
    assert p45._parse_verdict(result.data["raw_response"]) == p45.VERDICT_REVISE
    repoll_events = [e for e in events if e[0] == "phase_45_spec_review_repoll"]
    assert len(repoll_events) == 1
    assert "ERROR" in repoll_events[0][1]["votes"]


# ─── AC5 ────────────────────────────────────────────────────────────────────


def test_ac5_fail_closed_all_repolls_error_never_silent_ship():
    """AC5: first REVISE + all repolls error -> final REVISE (first
    returned, never SHIP); 'ERROR' in votes; no silent PASS. Regression
    lock (already passes today at 3 spawns; early-stop makes it 2)."""
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

    assert p45._parse_verdict(result.data["raw_response"]) == p45.VERDICT_REVISE
    repoll_events = [e for e in events if e[0] == "phase_45_spec_review_repoll"]
    assert len(repoll_events) == 1
    assert "ERROR" in repoll_events[0][1]["votes"]


# ─── AC6 ────────────────────────────────────────────────────────────────────


def test_ac6_unknown_garbage_repoll_counts_as_revise_fail_closed():
    """AC6: garbage/unparseable raw counts as REVISE -> no SHIP majority ->
    final REVISE. Regression lock (early-stop fires after the garbage vote
    makes SHIP unreachable, so this now also caps at 2 spawns)."""
    calls = []

    def _invoke(**kwargs):
        calls.append(kwargs)
        n = len(calls)
        if n == 1:
            return _ok(_raw("REVISE"))
        return _ok("no verdict heading here, just garbage prose")

    with patch.object(p45, "invoke_llm_subprocess", side_effect=_invoke), \
         patch.object(p45, "_emit_safe"):
        result = p45._invoke_review_llm(_ctx(), _prev(is_frozen=True))

    assert p45._parse_verdict(result.data["raw_response"]) == p45.VERDICT_REVISE


# ─── AC7 ────────────────────────────────────────────────────────────────────


def test_ac7_kill_switch_zero_repolls_single_invoke_no_event():
    """AC7: spec_frozen_review_repolls=0 + first REVISE -> exactly 1 spawn;
    first returned unchanged; no repoll event. Regression lock — already
    passes on current code."""
    calls = []
    events = []

    def _invoke(**kwargs):
        calls.append(kwargs)
        return _ok(_raw("REVISE"))

    ctx = _ctx(spec_frozen_review_repolls=0)
    with patch.object(p45, "invoke_llm_subprocess", side_effect=_invoke), \
         patch.object(p45, "_emit_safe", side_effect=lambda t, p: events.append((t, p))):
        result = p45._invoke_review_llm(ctx, _prev(is_frozen=True))

    assert len(calls) == 1, f"expected exactly 1 spawn (kill-switch), got {len(calls)}"
    assert p45._parse_verdict(result.data["raw_response"]) == p45.VERDICT_REVISE
    assert not [e for e in events if e[0] == "phase_45_spec_review_repoll"]


# ─── AC8 ────────────────────────────────────────────────────────────────────


def test_ac8_ship_reachable_early_stop_must_not_fire_three_spawns():
    """AC8 — CRITICAL REGRESSION GUARD (GH514/GH541): first REVISE +
    repolls [SHIP, SHIP] -> SHIP is reachable at every step -> early-stop
    must NEVER break this path. Exactly 3 spawns; final SHIP."""
    calls = []

    def _invoke(**kwargs):
        calls.append(kwargs)
        seq = ["REVISE", "SHIP", "SHIP"]
        return _ok(_raw(seq[len(calls) - 1]))

    with patch.object(p45, "invoke_llm_subprocess", side_effect=_invoke), \
         patch.object(p45, "_emit_safe"):
        result = p45._invoke_review_llm(_ctx(), _prev(is_frozen=True))

    assert len(calls) == 3, f"expected 3 spawns (SHIP reachable, no early-stop), got {len(calls)}"
    assert p45._parse_verdict(result.data["raw_response"]) == p45.VERDICT_SHIP


# ─── AC9 ────────────────────────────────────────────────────────────────────


def test_ac9_ship_reachable_then_flips_three_spawns_final_revise():
    """AC9: first REVISE + repolls [SHIP, REVISE] -> SHIP was reachable
    after repoll1 (1 ship, 1 remaining, 1 revise: 1+1>1) so no early-stop;
    3rd repoll flips it back. Exactly 3 spawns; final REVISE."""
    calls = []

    def _invoke(**kwargs):
        calls.append(kwargs)
        seq = ["REVISE", "SHIP", "REVISE"]
        return _ok(_raw(seq[len(calls) - 1]))

    with patch.object(p45, "invoke_llm_subprocess", side_effect=_invoke), \
         patch.object(p45, "_emit_safe"):
        result = p45._invoke_review_llm(_ctx(), _prev(is_frozen=True))

    assert len(calls) == 3, f"expected 3 spawns (reachable after repoll1), got {len(calls)}"
    assert p45._parse_verdict(result.data["raw_response"]) == p45.VERDICT_REVISE


# ─── AC10 ───────────────────────────────────────────────────────────────────


def test_ac10a_stateless_identical_repeated_calls_no_cross_call_accumulation():
    """AC10(a): _frozen_revise_repoll invoked twice with identical scripted
    inputs -> identical len(calls) and identical final each run (fresh
    counter per run; no cross-call accumulation)."""
    def _run():
        calls = []
        seq = ["REVISE", "REVISE"]

        def _invoke(**kwargs):
            calls.append(kwargs)
            return _ok(_raw(seq[len(calls) - 1]))

        with patch.object(p45, "invoke_llm_subprocess", side_effect=_invoke), \
             patch.object(p45, "_emit_safe"):
            result = p45._invoke_review_llm(_ctx(), _prev(is_frozen=True))
        return len(calls), p45._parse_verdict(result.data["raw_response"])

    run1_count, run1_final = _run()
    run2_count, run2_final = _run()

    assert run1_count == run2_count, f"non-deterministic spawn count across runs: {run1_count} vs {run2_count}"
    assert run1_final == run2_final == p45.VERDICT_REVISE


def test_ac10b_frozen_revise_repoll_has_no_durable_write_side_effect():
    """AC10(b): structural — _frozen_revise_repoll's source contains no
    open(/.write(/Path( write calls, proving no durable counter/sidecar;
    resume_sentinel=True on the enclosing step is the sole idempotency
    boundary."""
    src = inspect.getsource(p45._frozen_revise_repoll)
    assert "open(" not in src, "found open( in _frozen_revise_repoll — durable write side-effect"
    assert ".write(" not in src, "found .write( in _frozen_revise_repoll — durable write side-effect"


def test_ac10c_invoke_review_llm_step_registered_with_resume_sentinel_true():
    """AC10(c): the invoke_review_llm StepContract in the phase_45_spec step
    list is registered with resume_sentinel=True (durable resume/DBOS-replay
    idempotency boundary)."""
    src = inspect.getsource(p45)
    marker = 'StepContract(name="invoke_review_llm"'
    idx = src.index(marker)
    # scan the same statement (up to the next ')' that closes the call) for the flag
    stmt_end = src.index(")\n", idx)
    stmt = src[idx:stmt_end]
    assert "resume_sentinel=True" in stmt, (
        f"invoke_review_llm StepContract missing resume_sentinel=True: {stmt!r}"
    )


# ─── AC11 ───────────────────────────────────────────────────────────────────


def test_ac11_ship_reachable_pure_helper():
    """AC11: _ship_reachable(ship_n, revise_n, remaining) -> bool, pure
    helper, best-case-all-remaining-vote-SHIP semantics. Does not exist on
    current code — referenced only here (never at module import time) so
    the file collects cleanly and fails at assert-time (§1q)."""
    assert hasattr(p45, "_ship_reachable"), "_ship_reachable not implemented yet"
    assert p45._ship_reachable(2, 1, 0) is True
    assert p45._ship_reachable(0, 2, 1) is False
    assert p45._ship_reachable(1, 1, 1) is True
    assert p45._ship_reachable(0, 1, 1) is False
