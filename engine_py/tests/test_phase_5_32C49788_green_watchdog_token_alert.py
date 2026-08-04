"""RED tests for 32C49788 — _green_watchdog token branch downgraded to non-fatal ALERT.

Spec: SHARED/memory/Decisions/2026-06-08_32C49788_green_watchdog_token_alert_spec.md

Current production code (phase_5_implement.py:3490–3502) returns terminal
status="escalate" / error_code="E_GREEN_WATCHDOG_ESCALATE" when
tokens_out >= token_limit (5 × GREEN_OUTPUT_TOKEN_BUDGET = 25 000 for SIMPLE/FEATURE).

This ship replaces that with a non-fatal ALERT: emit "green_watchdog_token_alert"
via _emit_safe(...) then fall through to the healthy return StepResult(status="ok").
The wall-clock branch remains terminal and is unchanged.

Pre-GREEN PASS/FAIL classification (§3):
  AC1 → FAIL  (token overrun currently returns status="escalate", not "ok")
  AC2 → FAIL  (no "green_watchdog_token_alert" event emitted by current code)
  AC3 → PASS  (wall-clock branch unchanged — regression guard)
  AC4 → PASS  (healthy path already returns ok with no alert)
  AC5 → PASS  (combined overrun: wall-clock fires first, returns escalate)
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

# ─── sys.path setup (guard-wrapped per suite_safety.py scanner) ───────────────
_ENGINE_PY = Path(__file__).resolve().parents[1]
if str(_ENGINE_PY) not in sys.path:
    sys.path.insert(0, str(_ENGINE_PY))
_WORKFLOWS = _ENGINE_PY / "bytedigger_engine" / "workflows"
if str(_WORKFLOWS) not in sys.path:
    sys.path.insert(0, str(_WORKFLOWS))
_LIB = _ENGINE_PY / "bytedigger_engine" / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

# ─── Production imports (module-level — types only, no not-yet-existing symbols) ─
from bytedigger_engine.contracts import StepResult, WorkflowContext  # noqa: E402


# ─── Helpers (mirrored from test_42E92BE7_green_watchdog_mixin.py per §4 idiom) ─


def _make_ctx(tmp_path: Path, **org_extra) -> WorkflowContext:
    """Build a minimal WorkflowContext.  Mirrors make_ctx from sibling — replicated locally (§1q)."""
    org: dict[str, Any] = {"scratchpad_dir": str(tmp_path), **org_extra}
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question="32C49788 test",
        session_id="test-32C49788",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_prev(duration_ms: int = 1_000, tokens_out: int = 100) -> StepResult:
    """Build a fake prev StepResult shaped like invoke_green_llm output.
    Mirrors _make_prev from sibling test_42E92BE7 — replicated locally (§1q)."""
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


# ─── AC1: token-overrun + healthy duration → status=="ok" ────────────────────


def test_token_overrun_healthy_duration_returns_ok(tmp_path: Path) -> None:
    """AC1: _green_watchdog with tokens_out=25_001 (>= SIMPLE limit 25_000) but
    duration_ms=1000 (well under wall_limit 1_200_000 ms) must return
    StepResult(status="ok", error_code=None, data contains tokens_out=25_001).

    Post-32C49788 contract: token overrun is a non-fatal ALERT; only wall-clock
    is terminal.  Current code (pre-GREEN) returns status="escalate" → FAIL expected.

    Forcing function: status literal "ok" + error_code None — cannot be satisfied
    without flipping the token branch away from gated_step_result (§1l, §1y).
    """
    from bytedigger_engine.workflows.phase_5_implement import _green_watchdog  # noqa: PLC0415

    # green_llm_timeout_sec=600 → wall_limit_ms = 2 * 600 * 1000 = 1_200_000 ms
    ctx = _make_ctx(tmp_path, green_llm_timeout_sec=600)
    # duration_ms=1000 << 1_200_000 → wall branch NOT tripped; tokens_out=25_001 >= 25_000
    prev = _make_prev(duration_ms=1_000, tokens_out=25_001)

    result = _green_watchdog(ctx, prev)

    assert result.status == "ok", (
        "AC1: token-overrun with healthy duration must return status='ok' "
        f"(post-32C49788 non-fatal ALERT), got {result.status!r}.  "
        "Pre-GREEN: 'escalate' → FAIL expected."
    )
    assert result.error_code is None, (
        f"AC1: error_code must be None for non-fatal token alert, got {result.error_code!r}"
    )
    assert result.data is not None, "AC1: data must not be None (pass-through)"
    assert result.data.get("tokens_out") == 25_001, (
        "AC1: prev.data must be passed through; tokens_out expected 25_001, "
        f"got {result.data.get('tokens_out')!r}"
    )


# ─── AC2: token-overrun emits green_watchdog_token_alert event ────────────────


def test_token_overrun_emits_token_alert(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """AC2: same token-overrun fixture must emit a "green_watchdog_token_alert"
    event via _emit_safe with payload tokens_out==25_001, token_limit==25_000,
    exceeded_by==1.

    Monkeypatches phase_5_implement._emit_safe to capture calls.

    Anti-stub forcing function (§1l): an impl that returns ok WITHOUT emitting
    the alert event fails this test.  Cannot be stub-satisfied without the real
    emit call in the token branch.

    Pre-GREEN: FAIL — current code calls gated_step_result (no _emit_safe for
    token alert), zero "green_watchdog_token_alert" events captured.
    """
    from bytedigger_engine.workflows import phase_5_implement as _p5m  # noqa: PLC0415

    events: list[tuple[str, dict, dict]] = []

    def _capture_emit(name: str, payload: dict, **kw: Any) -> None:
        events.append((name, payload, kw))

    monkeypatch.setattr(_p5m, "_emit_safe", _capture_emit)

    ctx = _make_ctx(tmp_path, green_llm_timeout_sec=600)
    prev = _make_prev(duration_ms=1_000, tokens_out=25_001)

    _p5m._green_watchdog(ctx, prev)

    token_alerts = [
        (name, payload, kw)
        for name, payload, kw in events
        if name == "green_watchdog_token_alert"
    ]
    assert len(token_alerts) >= 1, (
        "AC2: expected at least 1 'green_watchdog_token_alert' event, "
        f"got {len(token_alerts)}.  "
        "Pre-GREEN: 0 events emitted (token branch uses gated_step_result) → FAIL expected."
    )
    _, payload, _ = token_alerts[0]
    assert payload.get("tokens_out") == 25_001, (
        f"AC2: payload['tokens_out'] must be 25_001, got {payload.get('tokens_out')!r}"
    )
    assert payload.get("token_limit") == 25_000, (
        f"AC2: payload['token_limit'] must be 25_000, got {payload.get('token_limit')!r}"
    )
    assert payload.get("exceeded_by") == 1, (
        f"AC2: payload['exceeded_by'] must be 1 (25_001 - 25_000), "
        f"got {payload.get('exceeded_by')!r}"
    )


# ─── AC3: wall-clock overrun still escalates (regression guard) ───────────────


def test_wall_clock_overrun_still_escalates(tmp_path: Path) -> None:
    """AC3: wall-clock overrun (duration_ms=1_200_001 >= 1_200_000 ms wall_limit)
    with low tokens_out must still return status=="escalate",
    error_code=="E_GREEN_WATCHDOG_ESCALATE", recoverable is False.

    Wall-clock branch is UNCHANGED by 32C49788 — this is a regression guard.
    Pre-GREEN: PASS (wall-clock branch still terminal).  Stays green post-GREEN.
    """
    from bytedigger_engine.workflows.phase_5_implement import _green_watchdog  # noqa: PLC0415

    ctx = _make_ctx(tmp_path, green_llm_timeout_sec=600)
    prev = _make_prev(duration_ms=1_200_001, tokens_out=100)

    result = _green_watchdog(ctx, prev)

    assert result.status == "escalate", (
        f"AC3: wall-clock overrun must still return status='escalate', got {result.status!r}"
    )
    assert result.error_code == "E_GREEN_WATCHDOG_ESCALATE", (
        "AC3: wall-clock overrun error_code must be 'E_GREEN_WATCHDOG_ESCALATE', "
        f"got {result.error_code!r}"
    )
    assert result.recoverable is False, (
        f"AC3: wall-clock overrun must be non-recoverable, got {result.recoverable!r}"
    )


# ─── AC4: healthy path — no token-alert event ─────────────────────────────────


def test_healthy_no_token_alert(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """AC4: healthy call (duration_ms=1000, tokens_out=100 — both under limits)
    must return status=="ok" AND must NOT emit any "green_watchdog_token_alert" event.

    Presence-gate: confirm status=="ok" first (production code reachable), then
    assert absence of the alert event to prevent a vacuous pass (§1l guard).

    Pre-GREEN: PASS — healthy path already returns ok; no alert emitted.
    Stays green post-GREEN.
    """
    from bytedigger_engine.workflows import phase_5_implement as _p5m  # noqa: PLC0415

    events: list[tuple[str, dict, dict]] = []

    def _capture_emit(name: str, payload: dict, **kw: Any) -> None:
        events.append((name, payload, kw))

    monkeypatch.setattr(_p5m, "_emit_safe", _capture_emit)

    ctx = _make_ctx(tmp_path, green_llm_timeout_sec=600)
    prev = _make_prev(duration_ms=1_000, tokens_out=100)

    result = _p5m._green_watchdog(ctx, prev)

    assert result.status == "ok", (
        f"AC4: healthy path must return status='ok', got {result.status!r}"
    )
    token_alerts = [name for name, _, _ in events if name == "green_watchdog_token_alert"]
    assert len(token_alerts) == 0, (
        f"AC4: healthy path must NOT emit 'green_watchdog_token_alert', "
        f"got {len(token_alerts)} such event(s)"
    )


# ─── AC5: combined overrun — wall-clock fires first (regression guard) ────────


def test_combined_overrun_wall_clock_fires_first(tmp_path: Path) -> None:
    """AC5: combined overrun (duration_ms=2_400_000 >= wall_limit, tokens_out=25_001
    >= token_limit) → wall-clock branch returns terminal escalate BEFORE the token
    branch is reached.  Result must be status=="escalate".

    Ordering guard: wall-clock branch at :3477 precedes token branch at :3490 —
    unchanged by 32C49788.  Pre-GREEN: PASS (wall-clock terminal already).
    Stays green post-GREEN.
    """
    from bytedigger_engine.workflows.phase_5_implement import _green_watchdog  # noqa: PLC0415

    ctx = _make_ctx(tmp_path, green_llm_timeout_sec=600)
    prev = _make_prev(duration_ms=2_400_000, tokens_out=25_001)

    result = _green_watchdog(ctx, prev)

    assert result.status == "escalate", (
        "AC5: combined overrun must return status='escalate' (wall-clock fires first), "
        f"got {result.status!r}"
    )
