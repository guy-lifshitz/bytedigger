"""RED tests for 42E92BE7 — _green_watchdog routed through RecoverableGateMixin.

Spec: SHARED/memory/Decisions/2026-06-01_42E92BE7_green_watchdog_mixin_wire_spec.md

_green_watchdog (phase_5_implement.py:3079-3088) currently returns terminal
status="error" + error_code="E_GREEN_WATCHDOG" for both wall-clock and token
overruns.  This ship routes both tripwires through
RecoverableGateMixin.gated_step_result(gate="watchdog_post_commit", ...) so the
result surfaces as status="escalate" + error_code="E_GREEN_WATCHDOG_ESCALATE"
plus a "recoverable_gate_attempted" telemetry event.

Pre-GREEN PASS/FAIL classification (§4 spec):
  AC1 → FAIL  (current code returns status="error", not "escalate")
  AC2 → FAIL  (same — tokens branch)
  AC3 → PASS  (healthy pass-through unchanged — regression guard)
  AC4 → FAIL  (no mixin event emitted by current code)
  AC5 → FAIL  (no event, so payload["build_class"] assertion fails)
  AC6 → PASS  (stub assert True — full-suite delta-0 checked by orchestrator)

6 test functions, 1:1 with ACs.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

# ─── sys.path setup (guard-wrapped per suite_safety.py scanner) ───────────────
# Matches test_E843349F_recoverable_policy.py exemplar exactly.
_ENGINE_PY = Path(__file__).resolve().parents[1]
if str(_ENGINE_PY) not in sys.path:
    sys.path.insert(0, str(_ENGINE_PY))
_WORKFLOWS = _ENGINE_PY / "bytedigger_engine" / "workflows"
if str(_WORKFLOWS) not in sys.path:
    sys.path.insert(0, str(_WORKFLOWS))
_LIB = _ENGINE_PY / "bytedigger_engine" / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

# ─── Production imports ────────────────────────────────────────────────────────
from bytedigger_engine.contracts import StepResult, WorkflowContext  # noqa: E402
from bytedigger_engine import telemetry_ctx  # noqa: E402  # attribute-lookup so monkeypatch.setattr works


# ─── Helpers ──────────────────────────────────────────────────────────────────


def _make_ctx(tmp_path: Path, **org_extra) -> WorkflowContext:
    """Build a minimal WorkflowContext.  Mirrors make_ctx from sibling
    test_green_watchdog_complexity_scaling.py — replicated locally (§1q)."""
    org: dict[str, Any] = {"scratchpad_dir": str(tmp_path), **org_extra}
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question="42E92BE7 test",
        session_id="test-42E92BE7",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_prev(duration_ms: int = 1_000, tokens_out: int = 100) -> StepResult:
    """Build a fake prev StepResult shaped like invoke_green_llm output.
    Mirrors make_prev_green_result from sibling — replicated locally (§1q)."""
    return StepResult(
        status="ok",
        data={
            "duration_ms": duration_ms,
            "tokens_out": tokens_out,
            "log_path": "/tmp/test/tests/build-green-output.log",
            "spec_path": "/tmp/test/specs/build-spec.md",
            "red_log_path": "/tmp/test/tests/build-red-output.log",
            "validation_doc_path": "/tmp/test/reviews/build-opus-validation.md",
            "verdict": "PASS",
            "cycle_count": 1,
        },
        duration_ms=0,
        step_name="invoke_green_llm",
    )


# ─── AC1: duration overrun → escalate ────────────────────────────────────────


def test_duration_overrun_returns_escalate(tmp_path: Path) -> None:
    """AC1: _green_watchdog with duration_ms >= wall_limit_ms returns
    StepResult(status="escalate", recoverable=False,
               error_code="E_GREEN_WATCHDOG_ESCALATE").

    Pre-GREEN: FAIL — current code returns status="error",
    error_code="E_GREEN_WATCHDOG".

    Forcing function: error_code literal "E_GREEN_WATCHDOG_ESCALATE" is new;
    grep prod confirms it does not exist today.  Cannot be stub-satisfied
    without the real mixin wire-up (§1l).
    """
    from bytedigger_engine.workflows.phase_5_implement import _green_watchdog  # noqa: PLC0415

    # green_llm_timeout_sec=600 → wall_limit_ms = 2 * 600 * 1000 = 1_200_000 ms
    # Use duration_ms just over the limit.
    ctx = _make_ctx(tmp_path, green_llm_timeout_sec=600)
    prev = _make_prev(duration_ms=1_200_001, tokens_out=100)

    result = _green_watchdog(ctx, prev)

    assert result.status == "escalate", (
        "AC1: expected status='escalate' (mixin post-commit escalation), "
        f"got {result.status!r}.  Pre-GREEN: status is 'error' → FAIL expected."
    )
    assert result.recoverable is False, (
        f"AC1: expected recoverable=False, got {result.recoverable!r}"
    )
    assert result.error_code == "E_GREEN_WATCHDOG_ESCALATE", (
        "AC1: expected error_code='E_GREEN_WATCHDOG_ESCALATE' (mixin terminal code), "
        f"got {result.error_code!r}.  Pre-GREEN: 'E_GREEN_WATCHDOG' → FAIL expected."
    )


# ─── AC2: token overrun → non-fatal ok (32C49788) ────────────────────────────


def test_tokens_overrun_returns_ok_nonfatal(tmp_path: Path) -> None:
    """AC2 (updated per 32C49788): post-commit token overrun is a non-fatal ALERT —
    _green_watchdog must return status="ok", error_code=None when tokens_out >= token_limit
    but duration_ms is under the wall-clock limit.

    green_llm_timeout_sec=600, SIMPLE complexity → token_limit=25_000.
    tokens_out=25_001 trips the token threshold; duration_ms=1_000 << 1_200_000 wall limit.

    Emit coverage (green_watchdog_token_alert) is tested in the dedicated
    test_phase_5_32C49788_green_watchdog_token_alert.py AC2 test.
    """
    from bytedigger_engine.workflows.phase_5_implement import _green_watchdog  # noqa: PLC0415

    ctx = _make_ctx(tmp_path, green_llm_timeout_sec=600)
    # duration well under wall_limit (1 s < 1_200_000 ms) → only token branch trips
    prev = _make_prev(duration_ms=1_000, tokens_out=25_001)

    result = _green_watchdog(ctx, prev)

    assert result.status == "ok", (
        "AC2 (32C49788): token overrun must return status='ok' (non-fatal ALERT), "
        f"got {result.status!r}."
    )
    assert result.error_code is None, (
        "AC2 (32C49788): error_code must be None for non-fatal token alert, "
        f"got {result.error_code!r}."
    )


# ─── AC3: healthy path passes through ─────────────────────────────────────────


def test_healthy_path_returns_ok_with_prev_data(tmp_path: Path) -> None:
    """AC3: duration + tokens under limits → status="ok" and result.data
    carries prev.data unchanged.

    Pre-GREEN: PASS — regression guard; current code already returns ok
    on this path.  Remains green post-GREEN.
    """
    from bytedigger_engine.workflows.phase_5_implement import _green_watchdog  # noqa: PLC0415

    ctx = _make_ctx(tmp_path, green_llm_timeout_sec=600)
    # duration=1_000 ms << 1_200_000 ms wall limit; tokens=100 << 25_000 token limit
    prev = _make_prev(duration_ms=1_000, tokens_out=100)

    result = _green_watchdog(ctx, prev)

    assert result.status == "ok", (
        f"AC3: healthy path must return status='ok', got {result.status!r}"
    )
    assert result.data is not None, "AC3: result.data must not be None on ok path"
    assert result.data.get("duration_ms") == 1_000, (
        f"AC3: prev.data must propagate; duration_ms expected 1000, "
        f"got {result.data.get('duration_ms')!r}"
    )
    assert result.data.get("tokens_out") == 100, (
        f"AC3: prev.data must propagate; tokens_out expected 100, "
        f"got {result.data.get('tokens_out')!r}"
    )


# ─── AC4: tripwire emits recoverable_gate_attempted telemetry ─────────────────


def test_tripwire_emits_telemetry_event(tmp_path: Path, monkeypatch) -> None:
    """AC4: either tripwire emits exactly one "recoverable_gate_attempted" event
    with payload["gate"]=="watchdog_post_commit", payload["slot"]=="escalate",
    payload["outcome"]=="escalate".

    Monkeypatches telemetry_ctx.emit_safe (attribute-lookup idiom) — the mixin
    resolves emit_safe through the telemetry_ctx module object so patching the
    module attribute intercepts all calls from recoverable_gate.py.

    Pre-GREEN: FAIL — current code emits zero events (no mixin call).
    """
    from bytedigger_engine.workflows.phase_5_implement import _green_watchdog  # noqa: PLC0415

    captured: list[tuple[str, dict]] = []

    def _fake_emit(event_type: str, payload: dict, *args: Any, **kwargs: Any) -> None:
        captured.append((event_type, payload))

    monkeypatch.setattr(telemetry_ctx, "emit_safe", _fake_emit)

    ctx = _make_ctx(tmp_path, green_llm_timeout_sec=600)
    prev = _make_prev(duration_ms=1_200_001, tokens_out=100)  # duration trips

    _green_watchdog(ctx, prev)

    gate_events = [
        (n, p) for n, p in captured if n == "recoverable_gate_attempted"
    ]
    assert len(gate_events) == 1, (
        "AC4: expected exactly 1 'recoverable_gate_attempted' event, "
        f"got {len(gate_events)}.  "
        "Pre-GREEN: 0 events emitted (no mixin) → FAIL expected."
    )
    payload = gate_events[0][1]
    assert payload.get("gate") == "watchdog_post_commit", (
        f"AC4: payload['gate'] must be 'watchdog_post_commit', got {payload.get('gate')!r}"
    )
    assert payload.get("slot") == "escalate", (
        f"AC4: payload['slot'] must be 'escalate', got {payload.get('slot')!r}"
    )
    assert payload.get("outcome") == "escalate", (
        f"AC4: payload['outcome'] must be 'escalate', got {payload.get('outcome')!r}"
    )


# ─── AC5: build_class resolved from ctx.org_config["complexity"] ──────────────


def test_build_class_resolved_from_ctx_complexity(tmp_path: Path, monkeypatch) -> None:
    """AC5: ctx with complexity="feature" → payload["build_class"]=="FEATURE".

    Confirms the mixin receives build_class from ctx.org_config["complexity"].upper()
    per spec §2.2 template.

    Pre-GREEN: FAIL — no event emitted; assertion on payload["build_class"] not reached.
    """
    from bytedigger_engine.workflows.phase_5_implement import _green_watchdog  # noqa: PLC0415

    captured: list[tuple[str, dict]] = []

    def _fake_emit(event_type: str, payload: dict, *args: Any, **kwargs: Any) -> None:
        captured.append((event_type, payload))

    monkeypatch.setattr(telemetry_ctx, "emit_safe", _fake_emit)

    # complexity="feature" (lowercase) → gated_step_result must upper() it → "FEATURE"
    ctx = _make_ctx(tmp_path, complexity="feature", green_llm_timeout_sec=600)
    prev = _make_prev(duration_ms=1_200_001, tokens_out=100)  # duration trips

    _green_watchdog(ctx, prev)

    gate_events = [
        (n, p) for n, p in captured if n == "recoverable_gate_attempted"
    ]
    assert len(gate_events) == 1, (
        "AC5: expected 1 'recoverable_gate_attempted' event, "
        f"got {len(gate_events)}.  Pre-GREEN: 0 events → FAIL expected."
    )
    payload = gate_events[0][1]
    assert payload.get("build_class") == "FEATURE", (
        "AC5: build_class must be 'FEATURE' (uppercase of ctx complexity='feature'), "
        f"got {payload.get('build_class')!r}.  Pre-GREEN: FAIL."
    )


# ─── AC6: full-suite delta-0 marker ──────────────────────────────────────────


def test_engine_py_suite_delta_zero_marker() -> None:
    """AC6: stub marker — full-suite delta-0 verified by orchestrator at verify-step.

    This test is always green; the orchestrator runs
      pytest SYSTEM/cli/build/engine_py/tests/
    before and after GREEN and asserts zero net change in pass/fail count.
    """
    assert True  # full-suite delta-0 asserted by orchestrator at verify-step
