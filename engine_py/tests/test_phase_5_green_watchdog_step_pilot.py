"""C37015E8 pilot: assert green_watchdog uses step()+RetryPolicy.

Type-level pilot — retries never fire because watchdog returns
recoverable=False, but exercises the F576B3F7 step() factory in
production code (phase_5_implement workflow). Validates step()
ergonomics on real critical-path code without behavioral change.

Three RED tests:
1. test_green_watchdog_step_uses_retry_policy — registration shape.
2. test_green_watchdog_step_smoke_passthrough_ok — wrapped watchdog
   still returns ok on healthy duration+tokens.
3. test_green_watchdog_step_smoke_terminal_on_overrun — wrapped
   watchdog still returns terminal E_GREEN_WATCHDOG when duration
   exceeds 2x SLA. Demonstrates RetryPolicy short-circuits on
   recoverable=False.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

from bytedigger_engine.contracts import StepResult, WorkflowContext  # noqa: E402


def _make_ctx(scratchpad: Path, **org_extra) -> WorkflowContext:
    org = {"scratchpad_dir": str(scratchpad), **org_extra}
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question="C37015E8 pilot",
        session_id="test-c37015e8",
        persona="hal",
        framework=None,
        domain=None,
    )


def test_green_watchdog_step_uses_retry_policy():
    """C37015E8: green_watchdog registered via step() factory.

    The StepContract returned by step() has skip_on_error=False (default
    for step()) and a name of 'green_watchdog'. Asserting via the public
    StepContract surface — step() returns a StepContract, so we cannot
    introspect the RetryPolicy directly without leaking factory internals.
    The behavioral pilot tests below pin that the wrapped execute still
    runs the underlying _green_watchdog logic.
    """
    from bytedigger_engine.workflows.phase_5_implement import phase_5_implement_workflow  # noqa: PLC0415

    wf = phase_5_implement_workflow()
    watchdog_step = next(s for s in wf.steps if s.name == "green_watchdog")
    assert watchdog_step.name == "green_watchdog"
    assert watchdog_step.skip_on_error is False


def test_green_watchdog_step_smoke_passthrough_ok(tmp_path: Path):
    """Wrapped watchdog still returns ok on healthy duration+tokens."""
    from bytedigger_engine.workflows.phase_5_implement import phase_5_implement_workflow  # noqa: PLC0415

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    ctx = _make_ctx(scratchpad, green_llm_timeout_sec=900)

    wf = phase_5_implement_workflow()
    watchdog_step = next(s for s in wf.steps if s.name == "green_watchdog")
    prev = StepResult(
        status="ok",
        data={"duration_ms": 1000, "tokens_out": 100},
        duration_ms=0,
        step_name="invoke_green_llm",
    )
    result = watchdog_step.execute(ctx, prev)
    assert result.status == "ok"
    assert result.step_name == "green_watchdog"


def test_green_watchdog_step_smoke_terminal_on_overrun(tmp_path: Path):
    """Wrapped watchdog still returns terminal error when duration exceeds 2x SLA.

    Demonstrates RetryPolicy short-circuits on recoverable=False — the
    step() retry loop sees recoverable=False and returns immediately
    instead of retrying.
    """
    from bytedigger_engine.workflows.phase_5_implement import (  # noqa: PLC0415
        DEFAULT_GREEN_TIMEOUT_SEC,
        GREEN_WATCHDOG_WALL_MULTIPLIER,
        phase_5_implement_workflow,
    )

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    ctx = _make_ctx(scratchpad)

    wf = phase_5_implement_workflow()
    watchdog_step = next(s for s in wf.steps if s.name == "green_watchdog")
    overrun_ms = (
        GREEN_WATCHDOG_WALL_MULTIPLIER * DEFAULT_GREEN_TIMEOUT_SEC * 1000
    ) + 1
    prev = StepResult(
        status="ok",
        data={"duration_ms": overrun_ms, "tokens_out": 100},
        duration_ms=0,
        step_name="invoke_green_llm",
    )
    result = watchdog_step.execute(ctx, prev)
    assert result.status == "escalate"
    assert result.error_code == "E_GREEN_WATCHDOG_ESCALATE"
    assert result.recoverable is False
