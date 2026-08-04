"""RED tests for B07BC910 — engine.py retry-predicate generalization.

Both tests MUST FAIL against the current implementation (engine.py line 219
whitelist check) and PASS after the GREEN fix replaces the whitelist with:
    result.recoverable and isinstance(result.data, dict)
    and result.data.get("retry_from_step") is not None

DO NOT modify engine.py or any non-test file.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from bytedigger_engine.contracts import (
    StepContract,
    StepResult,
    WorkflowContext,
    WorkflowDefinition,
)
from bytedigger_engine.engine import WorkflowEngine


# ─── helpers ──────────────────────────────────────────────────────────────────


def make_ctx() -> WorkflowContext:
    return WorkflowContext(
        tenant_id="t",
        scope=None,
        db_path=None,
        org_config=None,
        question="q",
        session_id="s",
        persona="p",
        framework=None,
        domain=None,
    )


class _FakeEventLog:
    def __init__(self):
        self.events: list[tuple[str, dict, str]] = []

    def append(self, event_type: str, payload: dict, run_id: str | None = None) -> None:
        self.events.append((event_type, payload, run_id))


# ─── Test 1 ────────────────────────────────────────────────────────────────────


def test_engine_retries_on_recoverable_with_retry_from_step_regardless_of_error_code():
    """Engine MUST retry when recoverable=True + retry_from_step set, even for
    error codes NOT in the current whitelist.

    Current behaviour (RED): engine checks error_code in (whitelist) first →
    E_CUSTOM_NOT_IN_WHITELIST is rejected → no retry → final status "error".

    Expected behaviour (GREEN): predicate is
        result.recoverable and isinstance(result.data, dict)
        and result.data.get("retry_from_step") is not None
    → engine retries → step_0 called twice → final status "ok".
    """
    log = _FakeEventLog()
    eng = WorkflowEngine(event_log=log)

    step0_calls = {"n": 0}
    step1_calls = {"n": 0}

    def step0(_ctx, _prev):
        step0_calls["n"] += 1
        return StepResult(status="ok", data={"from": "step0"}, duration_ms=0, step_name="step0")

    def step1(_ctx, _prev):
        step1_calls["n"] += 1
        if step1_calls["n"] == 1:
            # First call: signal retry from step 0 using a NON-WHITELISTED code
            return StepResult(
                status="error",
                error_code="E_CUSTOM_NOT_IN_WHITELIST",
                error="custom gate failure",
                recoverable=True,
                data={"retry_from_step": 0, "cycle_count": 1, "findings": "retry needed"},
                duration_ms=0,
                step_name="step1",
            )
        # Second call (after retry): succeed
        return StepResult(status="ok", data={"done": True}, duration_ms=0, step_name="step1")

    eng.register(
        "two_step",
        WorkflowDefinition(
            name="two_step",
            steps=[
                StepContract(name="step0", execute=step0),
                StepContract(name="step1", execute=step1),
            ],
        ),
    )

    result, _ = eng.execute("two_step", make_ctx(), run_id="r-B07BC910-1")

    # GREEN assertions — currently FAIL because engine does not retry custom codes
    assert result.status == "ok", (
        f"Expected final status 'ok' after retry, got {result.status!r}. "
        "Engine did not retry on E_CUSTOM_NOT_IN_WHITELIST even though "
        "recoverable=True and retry_from_step=0 were set. "
        "Fix: replace whitelist check with recoverable+retry_from_step predicate."
    )
    assert step0_calls["n"] == 2, (
        f"Expected step0 to be called twice (once initial, once after retry), "
        f"got {step0_calls['n']}. Engine did not jump back to step index 0."
    )
    emitted_kinds = [e[0] for e in log.events]
    assert "iteration_started" in emitted_kinds or "phase_retry_triggered" in emitted_kinds, (
        f"Expected iteration_started or phase_retry_triggered event in log, "
        f"got: {emitted_kinds}"
    )


# ─── Test 2 ────────────────────────────────────────────────────────────────────


def test_engine_retries_on_E_SPEC_FILE_MISSING_with_retry_from_step():
    """Engine MUST retry on E_SPEC_FILE_MISSING when recoverable=True + retry_from_step set.

    E_SPEC_FILE_MISSING was introduced today (B34645DB) and already exists in
    _verify_spec_citations. Both sites set recoverable=True + retry_from_step=0
    to signal retry intent. The current whitelist does NOT include
    E_SPEC_FILE_MISSING → engine ignores the retry signal → latent bug.

    Current behaviour (RED): engine sees E_SPEC_FILE_MISSING, skips retry block,
    returns error immediately. step0 called only once.

    Expected behaviour (GREEN): predicate ignores error_code, honours
    recoverable+retry_from_step → engine retries → step0 called twice →
    final status "ok".
    """
    log = _FakeEventLog()
    eng = WorkflowEngine(event_log=log)

    step0_calls = {"n": 0}
    step1_calls = {"n": 0}

    def step0(_ctx, _prev):
        step0_calls["n"] += 1
        return StepResult(status="ok", data={"spec": "ready"}, duration_ms=0, step_name="step0")

    def step1(_ctx, _prev):
        step1_calls["n"] += 1
        if step1_calls["n"] == 1:
            # Simulate verify_spec_completeness finding spec file missing on first run
            return StepResult(
                status="error",
                error_code="E_SPEC_FILE_MISSING",
                error="spec file not found: spec.md",
                recoverable=True,
                data={"retry_from_step": 0, "cycle_count": 1, "findings": "spec.md absent"},
                duration_ms=0,
                step_name="step1",
            )
        # Second call (after retry): spec was created, proceed
        return StepResult(status="ok", data={"verified": True}, duration_ms=0, step_name="step1")

    eng.register(
        "spec_verify_flow",
        WorkflowDefinition(
            name="spec_verify_flow",
            steps=[
                StepContract(name="step0", execute=step0),
                StepContract(name="step1", execute=step1),
            ],
        ),
    )

    result, _ = eng.execute("spec_verify_flow", make_ctx(), run_id="r-B07BC910-2")

    # GREEN assertions — currently FAIL because E_SPEC_FILE_MISSING not in whitelist
    assert result.status == "ok", (
        f"Expected final status 'ok' after retry on E_SPEC_FILE_MISSING, got {result.status!r}. "
        "Engine did not retry even though recoverable=True and retry_from_step=0 were set. "
        "E_SPEC_FILE_MISSING must be covered by the generalized retry predicate."
    )
    assert step0_calls["n"] == 2, (
        f"Expected step0 called twice (retry happened), got {step0_calls['n']}. "
        "Engine did not jump back to step index 0 on E_SPEC_FILE_MISSING."
    )
    emitted_kinds = [e[0] for e in log.events]
    assert "iteration_started" in emitted_kinds or "phase_retry_triggered" in emitted_kinds, (
        f"Expected retry event emitted, got: {emitted_kinds}"
    )


# ─── Test 3 ────────────────────────────────────────────────────────────────────


def test_engine_does_not_retry_when_recoverable_false():
    """Engine MUST NOT retry when recoverable=False, even if retry_from_step is set.

    The new predicate checks recoverable first — if False, the entire condition
    short-circuits and no retry happens, regardless of error_code or data content.

    Current behaviour (RED): would retry if error_code in whitelist.

    Expected behaviour (GREEN): predicate checks recoverable=False first →
    condition fails → no retry → final status "error", step 0 called once.
    """
    log = _FakeEventLog()
    eng = WorkflowEngine(event_log=log)

    step0_calls = {"n": 0}
    step1_calls = {"n": 0}

    def step0(_ctx, _prev):
        step0_calls["n"] += 1
        return StepResult(status="ok", data={"from": "step0"}, duration_ms=0, step_name="step0")

    def step1(_ctx, _prev):
        step1_calls["n"] += 1
        # Return error with recoverable=False; should never retry
        return StepResult(
            status="error",
            error_code="E_VALIDATION_RETRY",
            error="validation gate failure (not recoverable)",
            recoverable=False,
            data={"retry_from_step": 0, "cycle_count": 1},
            duration_ms=0,
            step_name="step1",
        )

    eng.register(
        "nonrecoverable_flow",
        WorkflowDefinition(
            name="nonrecoverable_flow",
            steps=[
                StepContract(name="step0", execute=step0),
                StepContract(name="step1", execute=step1),
            ],
        ),
    )

    result, _ = eng.execute("nonrecoverable_flow", make_ctx(), run_id="r-B07BC910-3")

    # GREEN assertions
    assert result.status == "error", (
        f"Expected final status 'error' when recoverable=False, got {result.status!r}. "
        "Engine should not retry regardless of retry_from_step when recoverable=False."
    )
    assert step0_calls["n"] == 1, (
        f"Expected step0 to be called once (no retry), got {step0_calls['n']}. "
        "Engine should not jump back when recoverable=False."
    )


# ─── Test 4 ────────────────────────────────────────────────────────────────────


def test_engine_does_not_retry_when_data_missing_retry_from_step():
    """Engine MUST NOT retry when data does not contain retry_from_step key,
    even if recoverable=True and error_code is in the old whitelist.

    The new predicate requires result.data.get("retry_from_step") is not None.
    If the key is missing (None), no retry happens.

    Current behaviour (RED): might retry based on error_code if in whitelist.

    Expected behaviour (GREEN): predicate checks retry_from_step is not None →
    condition fails → no retry → final status "error", step 0 called once.
    """
    log = _FakeEventLog()
    eng = WorkflowEngine(event_log=log)

    step0_calls = {"n": 0}
    step1_calls = {"n": 0}

    def step0(_ctx, _prev):
        step0_calls["n"] += 1
        return StepResult(status="ok", data={"from": "step0"}, duration_ms=0, step_name="step0")

    def step1(_ctx, _prev):
        step1_calls["n"] += 1
        # Return error with recoverable=True but NO retry_from_step key
        return StepResult(
            status="error",
            error_code="E_VALIDATION_RETRY",
            error="validation gate failure",
            recoverable=True,
            data={"cycle_count": 1, "findings": "some findings"},
            duration_ms=0,
            step_name="step1",
        )

    eng.register(
        "missing_retry_key_flow",
        WorkflowDefinition(
            name="missing_retry_key_flow",
            steps=[
                StepContract(name="step0", execute=step0),
                StepContract(name="step1", execute=step1),
            ],
        ),
    )

    result, _ = eng.execute("missing_retry_key_flow", make_ctx(), run_id="r-B07BC910-4")

    # GREEN assertions
    assert result.status == "error", (
        f"Expected final status 'error' when retry_from_step is missing, got {result.status!r}. "
        "Engine should not retry when the data dict lacks the retry_from_step key."
    )
    assert step0_calls["n"] == 1, (
        f"Expected step0 to be called once (no retry), got {step0_calls['n']}. "
        "Engine should not jump back when retry_from_step is None/missing."
    )
