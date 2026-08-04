"""GH727 RED tests — complexity-aware `green_token_budget_alert` threshold.

Frozen spec: SHARED/memory/Decisions/2026-07-13_GH727_green_token_budget_threshold_spec.md

Covers §2 AC1-AC12:
  - AC1-AC8: new resolver `_resolve_green_output_token_budget(cfg)` (does NOT
    exist in prod yet). Imported/fetched INSIDE each test body via getattr —
    never at module top level — so the file COLLECTS cleanly and the tests
    FAIL at assert time, not at collection time (§1q extension / D1CF5FDF).
  - AC9-AC12: existing `_check_green_token_budget(ctx, prev)` must resolve
    the budget via the new (missing) resolver and use it as the alert
    threshold. AC9 is the core regression this ship fixes: a COMPLEX ctx
    with tokens_out=21309 must NOT fire the flat-5000 alert.

Event-capture pattern mirrors test_phase_5_implement_B7442146.py's
`test_check_green_token_budget_emits_alert_event_when_exceeded`: a minimal
event log wired via `telemetry_ctx.set_current_run(event_log=...)`, NOT a
monkeypatch of `_emit_safe` (matches sibling convention exactly).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))


# ─── autouse fixtures ──────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clear_budget_env(monkeypatch):
    """Ensure HAL_GREEN_OUTPUT_TOKEN_BUDGET never leaks between tests."""
    monkeypatch.delenv("HAL_GREEN_OUTPUT_TOKEN_BUDGET", raising=False)


class _AlertEventLog:
    """Minimal event log capturing (event_type, payload, run_id) tuples."""

    def __init__(self):
        self.events: list[tuple[str, dict, str | None]] = []

    def append(self, event_type: str, payload: dict, run_id: str | None = None) -> None:
        self.events.append((event_type, payload, run_id))


class _Ctx:
    """Minimal ctx stub exposing org_config, matching prod's
    `getattr(ctx, "org_config", None)` access pattern."""

    def __init__(self, org_config: dict):
        self.org_config = org_config


def _make_prev(tokens_out: int):
    from bytedigger_engine.contracts import StepResult  # noqa: PLC0415

    return StepResult(
        status="ok",
        data={
            "tokens_out": tokens_out,
            "tokens_in": 200,
            "raw_response": "GREEN COMPLETE — all 1 tests passing. Files modified: [x]\n",
            "log_path": "/tmp/x.log",
            "spec_path": "/tmp/spec.md",
            "red_log_path": "/tmp/red.log",
            "validation_doc_path": "/tmp/v.md",
            "verdict": "PASS",
            "prompt": "p",
        },
        duration_ms=0,
        step_name="invoke_green_llm",
    )


def _get_resolver():
    from bytedigger_engine.workflows import phase_5_implement  # noqa: PLC0415

    fn = getattr(phase_5_implement, "_resolve_green_output_token_budget", None)
    assert fn is not None, (
        "GH727: phase_5_implement._resolve_green_output_token_budget is missing. "
        "This resolver must exist and be complexity-aware per spec §1."
    )
    return fn


# ─── AC1-AC8: _resolve_green_output_token_budget(cfg) ─────────────────────


def test_ac1_resolver_none_cfg_returns_base_5000():
    fn = _get_resolver()
    assert fn(None) == 5000


def test_ac2_resolver_empty_cfg_no_complexity_returns_base_5000():
    fn = _get_resolver()
    assert fn({}) == 5000


def test_ac3_resolver_complex_cfg_returns_40000():
    fn = _get_resolver()
    assert fn({"complexity": "COMPLEX"}) == 40000


def test_ac4_resolver_lowercase_complex_case_insensitive_returns_40000():
    fn = _get_resolver()
    assert fn({"complexity": "complex"}) == 40000


def test_ac5_resolver_cfg_absolute_wins_over_complexity():
    fn = _get_resolver()
    assert fn({"green_output_token_budget": 15000, "complexity": "COMPLEX"}) == 15000


def test_ac6_resolver_env_wins_over_cfg_and_complexity(monkeypatch):
    monkeypatch.setenv("HAL_GREEN_OUTPUT_TOKEN_BUDGET", "10000")
    fn = _get_resolver()
    assert fn({"complexity": "COMPLEX"}) == 10000


def test_ac7_resolver_malformed_env_falls_through_to_complex_40000(monkeypatch):
    monkeypatch.setenv("HAL_GREEN_OUTPUT_TOKEN_BUDGET", "abc")
    fn = _get_resolver()
    assert fn({"complexity": "COMPLEX"}) == 40000


def test_ac8_resolver_falsy_zero_cfg_override_falls_through_to_complex_40000():
    fn = _get_resolver()
    assert fn({"green_output_token_budget": 0, "complexity": "COMPLEX"}) == 40000


# ─── AC9-AC12: _check_green_token_budget(ctx, prev) ────────────────────────


def test_ac9_complex_ctx_tokens_out_21309_does_not_alert():
    """Core regression fix: a COMPLEX build legitimately writing 21309 tokens
    out (cal7 datapoint) must NOT fire the flat-5000 alert. FAILS today
    because prod ignores ctx.org_config complexity entirely."""
    from bytedigger_engine.workflows import phase_5_implement  # noqa: PLC0415
    from bytedigger_engine import telemetry_ctx  # noqa: PLC0415

    prev = _make_prev(21309)
    ctx = _Ctx({"complexity": "COMPLEX"})
    log = _AlertEventLog()
    telemetry_ctx.set_current_run(
        event_log=log,
        run_id="gh727-ac9-test",
        step_name="check_green_token_budget",
        phase="phase_5_implement",
    )
    try:
        result = phase_5_implement._check_green_token_budget(ctx, prev)
    finally:
        telemetry_ctx.clear_current_run()

    assert result.status == "ok"
    alerts = [e for e in log.events if e[0] == "green_token_budget_alert"]
    assert len(alerts) == 0, (
        f"GH727 AC9: COMPLEX ctx with tokens_out=21309 must NOT alert "
        f"(complex threshold=40000). Got {len(alerts)} alert(s): {alerts!r}"
    )


def test_ac10_complex_ctx_tokens_out_40001_alerts_with_complex_threshold():
    from bytedigger_engine.workflows import phase_5_implement  # noqa: PLC0415
    from bytedigger_engine import telemetry_ctx  # noqa: PLC0415

    prev = _make_prev(40001)
    ctx = _Ctx({"complexity": "COMPLEX"})
    log = _AlertEventLog()
    telemetry_ctx.set_current_run(
        event_log=log,
        run_id="gh727-ac10-test",
        step_name="check_green_token_budget",
        phase="phase_5_implement",
    )
    try:
        result = phase_5_implement._check_green_token_budget(ctx, prev)
    finally:
        telemetry_ctx.clear_current_run()

    assert result.status == "ok"
    alerts = [e for e in log.events if e[0] == "green_token_budget_alert"]
    assert len(alerts) == 1, f"Expected exactly 1 alert, got {len(alerts)}: {alerts!r}"
    _evt_type, payload, _run_id = alerts[0]
    assert payload == {
        "tokens_out": 40001,
        "threshold": 40000,
        "exceeded_by": 1,
    }, f"GH727 AC10 payload mismatch: {payload!r}"


def test_ac11_none_ctx_tokens_out_13043_alerts_with_base_threshold_sibling_compat():
    """Sibling-compat: ctx=None must still resolve to base 5000 (unchanged
    from B7442146's existing coverage)."""
    from bytedigger_engine.workflows import phase_5_implement  # noqa: PLC0415
    from bytedigger_engine import telemetry_ctx  # noqa: PLC0415

    prev = _make_prev(13043)
    log = _AlertEventLog()
    telemetry_ctx.set_current_run(
        event_log=log,
        run_id="gh727-ac11-test",
        step_name="check_green_token_budget",
        phase="phase_5_implement",
    )
    try:
        result = phase_5_implement._check_green_token_budget(None, prev)
    finally:
        telemetry_ctx.clear_current_run()

    assert result.status == "ok"
    alerts = [e for e in log.events if e[0] == "green_token_budget_alert"]
    assert len(alerts) == 1, f"Expected exactly 1 alert, got {len(alerts)}: {alerts!r}"
    _evt_type, payload, _run_id = alerts[0]
    assert payload == {
        "tokens_out": 13043,
        "threshold": 5000,
        "exceeded_by": 8043,
    }, f"GH727 AC11 payload mismatch: {payload!r}"


def test_ac12_none_ctx_tokens_out_4999_no_alert_regression_guard():
    from bytedigger_engine.workflows import phase_5_implement  # noqa: PLC0415
    from bytedigger_engine import telemetry_ctx  # noqa: PLC0415

    prev = _make_prev(4999)
    log = _AlertEventLog()
    telemetry_ctx.set_current_run(
        event_log=log,
        run_id="gh727-ac12-test",
        step_name="check_green_token_budget",
        phase="phase_5_implement",
    )
    try:
        result = phase_5_implement._check_green_token_budget(None, prev)
    finally:
        telemetry_ctx.clear_current_run()

    assert result.status == "ok"
    alerts = [e for e in log.events if e[0] == "green_token_budget_alert"]
    assert len(alerts) == 0, f"Expected no alert for tokens_out=4999, got {alerts!r}"
