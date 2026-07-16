"""Tests for RetryPolicy + step() factory (F576B3F7).

Cloudflare-Workflows-style co-located retry-and-timeout configuration.
``step(name, fn, retries=RetryPolicy(...), timeout_sec=...)`` wraps a
closure with declarative retry + timeout instead of markdown contracts.
Pairs with E2440DB5 LoopStepContract — declarative cycles + declarative
retries replace hand-rolled state machines.

StepResult.recoverable is the fatal-vs-transient signal: recoverable=True
errors are retried up to max_retries; recoverable=False errors are
terminal (no retry).
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

from contracts import (  # noqa: E402
    RetryPolicy,
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
        question="retry test",
        session_id="t",
        persona="hal",
        framework=None,
        domain=None,
    )


# ─── RetryPolicy validation ──────────────────────────────────────────────────


def test_retry_policy_default_no_backoff_zero_delay():
    p = RetryPolicy()
    assert p.max_retries == 0
    assert p.initial_delay_sec == 0.0
    assert p.backoff == "none"


def test_retry_policy_invalid_max_retries_raises():
    import pytest
    with pytest.raises(ValueError):
        RetryPolicy(max_retries=-1)


def test_retry_policy_invalid_backoff_raises():
    import pytest
    with pytest.raises(ValueError):
        RetryPolicy(backoff="quadratic")


def test_retry_policy_delay_for_attempt_none_returns_initial():
    p = RetryPolicy(max_retries=3, initial_delay_sec=2.0, backoff="none")
    assert p.delay_for_attempt(1) == 2.0
    assert p.delay_for_attempt(3) == 2.0


def test_retry_policy_delay_for_attempt_linear():
    p = RetryPolicy(max_retries=4, initial_delay_sec=1.0, backoff="linear")
    assert p.delay_for_attempt(1) == 1.0
    assert p.delay_for_attempt(2) == 2.0
    assert p.delay_for_attempt(3) == 3.0


def test_retry_policy_delay_for_attempt_exponential():
    p = RetryPolicy(max_retries=4, initial_delay_sec=1.0, backoff="exponential")
    assert p.delay_for_attempt(1) == 1.0
    assert p.delay_for_attempt(2) == 2.0
    assert p.delay_for_attempt(3) == 4.0
    assert p.delay_for_attempt(4) == 8.0


def test_retry_policy_delay_capped_by_max_delay():
    p = RetryPolicy(max_retries=10, initial_delay_sec=10.0,
                    backoff="exponential", max_delay_sec=15.0)
    assert p.delay_for_attempt(1) == 10.0
    assert p.delay_for_attempt(2) == 15.0  # 20 capped
    assert p.delay_for_attempt(5) == 15.0


# ─── step() retry behaviour ──────────────────────────────────────────────────


def test_step_no_retry_policy_runs_once():
    """retries=None → fn called exactly once, even on recoverable error."""
    calls = {"n": 0}

    def fn(_ctx, _prev) -> StepResult:
        calls["n"] += 1
        return StepResult(status="error", data=None, duration_ms=0,
                          step_name="s", error="fail", error_code="E_F",
                          recoverable=True)

    contract = step("s", fn)
    result = contract.execute(_ctx(), None)
    assert calls["n"] == 1
    assert result.status == "error"


def test_step_retries_on_recoverable_error_until_success():
    """Retries until ok, attempts metadata = 3."""
    calls = {"n": 0}

    def fn(_ctx, _prev) -> StepResult:
        calls["n"] += 1
        if calls["n"] < 3:
            return StepResult(status="error", data=None, duration_ms=0,
                              step_name="s", error="transient",
                              error_code="E_T", recoverable=True)
        return StepResult(status="ok", data={"raw_response": "ok"},
                          duration_ms=0, step_name="s")

    sleeps = []
    contract = step("s", fn, retries=RetryPolicy(max_retries=5),
                    sleep_fn=sleeps.append)
    result = contract.execute(_ctx(), None)
    assert result.status == "ok"
    assert calls["n"] == 3
    assert result.metadata["attempts"] == 3


def test_step_no_retry_on_fatal_recoverable_false():
    """recoverable=False → no retry, attempts=1."""
    calls = {"n": 0}

    def fn(_ctx, _prev) -> StepResult:
        calls["n"] += 1
        return StepResult(status="error", data=None, duration_ms=0,
                          step_name="s", error="fatal", error_code="E_FATAL",
                          recoverable=False)

    contract = step("s", fn, retries=RetryPolicy(max_retries=5))
    result = contract.execute(_ctx(), None)
    assert calls["n"] == 1
    assert result.status == "error"
    assert result.metadata["attempts"] == 1


def test_step_caps_at_max_retries_then_returns_last_error():
    """All attempts fail recoverable → return last error with attempts=max+1."""
    calls = {"n": 0}

    def fn(_ctx, _prev) -> StepResult:
        calls["n"] += 1
        return StepResult(status="error", data=None, duration_ms=0,
                          step_name="s", error=f"f{calls['n']}",
                          error_code="E_R", recoverable=True)

    sleeps = []
    contract = step("s", fn, retries=RetryPolicy(max_retries=3),
                    sleep_fn=sleeps.append)
    result = contract.execute(_ctx(), None)
    assert result.status == "error"
    assert calls["n"] == 4   # 1 initial + 3 retries
    assert result.metadata["attempts"] == 4
    assert result.error == "f4"


def test_step_backoff_sleeps_called_with_correct_delays():
    """Exponential backoff should sleep with 1, 2, 4 between 4 attempts."""
    calls = {"n": 0}

    def fn(_ctx, _prev) -> StepResult:
        calls["n"] += 1
        return StepResult(status="error", data=None, duration_ms=0,
                          step_name="s", error="x", error_code="E_R",
                          recoverable=True)

    sleeps = []
    contract = step(
        "s", fn,
        retries=RetryPolicy(max_retries=3, initial_delay_sec=1.0,
                            backoff="exponential"),
        sleep_fn=sleeps.append,
    )
    contract.execute(_ctx(), None)
    # 3 retries → 3 sleeps between attempts (after attempt 1, 2, 3)
    assert sleeps == [1.0, 2.0, 4.0]


def test_step_no_sleep_when_initial_delay_zero():
    """initial_delay=0 → sleep_fn not called."""
    def fn(_ctx, _prev) -> StepResult:
        return StepResult(status="error", data=None, duration_ms=0,
                          step_name="s", error="x", error_code="E_R",
                          recoverable=True)

    sleeps = []
    contract = step("s", fn, retries=RetryPolicy(max_retries=2),
                    sleep_fn=sleeps.append)
    contract.execute(_ctx(), None)
    assert sleeps == []


def test_step_passes_through_ok_first_attempt_no_metadata_clutter():
    """Successful first attempt with no retry policy → no attempts metadata."""
    def fn(_ctx, _prev) -> StepResult:
        return StepResult(status="ok", data={"raw_response": "ok"},
                          duration_ms=0, step_name="s")

    contract = step("s", fn)
    result = contract.execute(_ctx(), None)
    assert result.status == "ok"
    assert "attempts" not in (result.metadata or {})


def test_step_skip_on_error_propagates():
    """skip_on_error flag is honored on the wrapping StepContract."""
    def fn(_ctx, _prev) -> StepResult:
        return StepResult(status="ok", data=None, duration_ms=0, step_name="s")

    contract = step("s", fn, skip_on_error=True)
    assert contract.skip_on_error is True


# ─── timeout enforcement ────────────────────────────────────────────────────


def test_step_timeout_fires_on_slow_fn():
    """fn that sleeps longer than timeout returns E_STEP_TIMEOUT."""
    def slow(_ctx, _prev) -> StepResult:
        time.sleep(0.5)
        return StepResult(status="ok", data=None, duration_ms=0, step_name="s")

    contract = step("s", slow, timeout_sec=0.1)
    result = contract.execute(_ctx(), None)
    assert result.status == "error"
    assert result.error_code == "E_STEP_TIMEOUT"


def test_step_timeout_does_not_fire_on_fast_fn():
    """fn faster than timeout completes normally."""
    def fast(_ctx, _prev) -> StepResult:
        return StepResult(status="ok", data={"raw_response": "fast"},
                          duration_ms=0, step_name="s")

    contract = step("s", fast, timeout_sec=2.0)
    result = contract.execute(_ctx(), None)
    assert result.status == "ok"


def test_step_timeout_retries_when_recoverable():
    """E_STEP_TIMEOUT is recoverable=True → retried under RetryPolicy."""
    calls = {"n": 0}

    def fn(_ctx, _prev) -> StepResult:
        calls["n"] += 1
        if calls["n"] == 1:
            time.sleep(0.3)
        return StepResult(status="ok", data={"raw_response": "ok"},
                          duration_ms=0, step_name="s")

    contract = step("s", fn, retries=RetryPolicy(max_retries=2),
                    timeout_sec=0.1, sleep_fn=lambda _: None)
    result = contract.execute(_ctx(), None)
    assert result.status == "ok"
    assert calls["n"] == 2
    assert result.metadata["attempts"] == 2
