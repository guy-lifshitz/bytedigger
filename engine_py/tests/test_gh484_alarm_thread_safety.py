"""RED tests for GH484 — `_alarm` SIGALRM crash off the main thread.

DBOS recovery re-executes workflows inside a worker thread. `contracts._alarm`
unconditionally calls `signal.signal(SIGALRM, ...)`, which raises ValueError
off the main thread. Spec: SHARED/memory/Decisions/
2026-07-10_GH484_alarm_thread_safety_spec.md
"""
from __future__ import annotations

import logging
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

from contracts import (  # noqa: E402
    StepResult,
    WorkflowContext,
    step,
)


def _ctx() -> WorkflowContext:
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={},
        question="gh484 test",
        session_id="t",
        persona="hal",
        framework=None,
        domain=None,
    )


def test_step_execute_in_worker_thread_no_valueerror():
    """AC1: real step().execute run inside a worker thread must not raise
    ValueError from signal.signal off the main thread."""
    def fn(ctx, prev):
        return StepResult(status="ok", data=None, duration_ms=0, step_name="t")

    contract = step("t", fn, timeout_sec=5)
    ctx = _ctx()
    outcome = {}

    def runner():
        try:
            outcome["result"] = contract.execute(ctx, None)
        except BaseException as exc:  # noqa: BLE001 - capture any exception
            outcome["exc"] = exc

    thread = threading.Thread(target=runner)
    thread.start()
    thread.join(timeout=10)

    assert "exc" not in outcome, f"unexpected exception in worker thread: {outcome.get('exc')!r}"
    assert "result" in outcome, "worker thread did not produce a result"
    assert outcome["result"].status == "ok"


def test_worker_thread_logs_sigalrm_warning(caplog):
    """AC2: worker-thread execution logs a warning mentioning SIGALRM
    unavailability instead of crashing."""
    def fn(ctx, prev):
        return StepResult(status="ok", data=None, duration_ms=0, step_name="t")

    contract = step("t", fn, timeout_sec=5)
    ctx = _ctx()
    outcome = {}

    def runner():
        try:
            outcome["result"] = contract.execute(ctx, None)
        except BaseException as exc:  # noqa: BLE001
            outcome["exc"] = exc

    with caplog.at_level(logging.WARNING):
        thread = threading.Thread(target=runner)
        thread.start()
        thread.join(timeout=10)

    messages = " ".join(r.getMessage() for r in caplog.records)
    assert "SIGALRM unavailable" in messages, (
        f"expected a warning containing 'SIGALRM unavailable', got: {messages!r}"
    )


def test_main_thread_timeout_still_enforced():
    """AC3: main-thread timeout enforcement is unchanged (regression guard)."""
    def fn(ctx, prev):
        time.sleep(1.0)
        return StepResult(status="ok", data=None, duration_ms=0, step_name="t")

    contract = step("t", fn, timeout_sec=0.2)
    ctx = _ctx()

    result = contract.execute(ctx, None)

    assert result.error_code == "E_STEP_TIMEOUT"


def test_worker_thread_none_timeout_ok():
    """AC4: worker-thread execution with timeout_sec=None still yields
    normally (early-return path is untouched by the guard)."""
    def fn(ctx, prev):
        return StepResult(status="ok", data=None, duration_ms=0, step_name="t")

    contract = step("t", fn, timeout_sec=None)
    ctx = _ctx()
    outcome = {}

    def runner():
        try:
            outcome["result"] = contract.execute(ctx, None)
        except BaseException as exc:  # noqa: BLE001
            outcome["exc"] = exc

    thread = threading.Thread(target=runner)
    thread.start()
    thread.join(timeout=10)

    assert "exc" not in outcome, f"unexpected exception in worker thread: {outcome.get('exc')!r}"
    assert outcome["result"].status == "ok"
