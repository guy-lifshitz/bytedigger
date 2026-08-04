"""RED tests for 56D695F2 Change 6: engine.py retry-data forwarding.

These tests FAIL today because engine.py:259 hardcodes
    initial_data={"cycle": next_cycle, "findings": findings}
dropping every other key in result.data.

After GREEN all three should pass.

AC11 — Engine forwards non-control keys from retry StepResult.data into initial_data.
AC12 — Engine emits 'retry_data_forwarded' event with {phase, forwarded_keys: sorted}.
AC13 — Backward compat: phase_45_spec-style retry (only cycle_count/findings/
         retry_from_step) produces initial_data with exactly {cycle, findings} —
         no spurious keys.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

from bytedigger_engine.contracts import (  # noqa: E402
    StepContract,
    StepResult,
    WorkflowContext,
    WorkflowDefinition,
)
from bytedigger_engine.engine import WorkflowEngine  # noqa: E402


# ─── helpers ──────────────────────────────────────────────────────────────────


def _make_ctx(tmp_path: Path) -> WorkflowContext:
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={"git_cwd": str(tmp_path)},
        question="q",
        session_id="test-session",
        persona="hal",
        framework=None,
        domain=None,
    )


class _FakeEventLog:
    """Minimal duck-typed event log that records (event_type, payload, run_id) tuples."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict, str]] = []

    def append(self, event_type: str, payload: dict, run_id: str) -> None:
        self.events.append((event_type, payload, run_id))

    def of_type(self, event_type: str) -> list[dict]:
        return [p for et, p, _ in self.events if et == event_type]


def _build_two_step_workflow(
    step0_retry_data: dict,
    captured_prev: list[Any],
) -> WorkflowDefinition:
    """Build a minimal 2-step workflow for forwarding tests.

    Step 0: returns a recoverable retry on first call (carrying step0_retry_data),
            then passes on subsequent calls.
    Step 1: probe step — captures its prev into captured_prev list, returns ok.

    Because engine recurses (retries from step 1 after step 0 returns retry),
    the captured_prev from step 1 on the RETRY pass is what we assert against.
    """
    call_counts = {"step0": 0}

    def step0_execute(ctx, prev):
        call_counts["step0"] += 1
        if call_counts["step0"] == 1:
            return StepResult(
                status="error",
                data=step0_retry_data,
                duration_ms=0,
                step_name="step0",
                error_code="E_GREEN_LINT_RETRY",
                recoverable=True,
            )
        # Second call (this step is NOT part of the retry — retry starts at step 1)
        # In practice, engine.recurse starts at start_step=1, so step0 is only
        # called once (initial pass). This branch is unreachable but harmless.
        return StepResult(status="ok", data={"step0_second": True}, duration_ms=0, step_name="step0")

    def step1_execute(ctx, prev):
        """Probe step — captures prev."""
        captured_prev.append(prev)
        return StepResult(status="ok", data={"probe": True}, duration_ms=0, step_name="step1")

    return WorkflowDefinition(
        name="test_forwarding",
        steps=[
            StepContract(name="step0", execute=step0_execute),
            StepContract(name="step1_probe", execute=step1_execute),
        ],
    )


# ─── AC11: non-control keys forwarded into initial_data ───────────────────────


def test_ac11_engine_forwards_non_control_keys_into_initial_data(tmp_path):
    """AC11: When step returns data={'retry_from_step': N, 'cycle_count': 1,
    'findings': 'f', 'custom_forwarded_key': 'v'}, engine forwards custom_forwarded_key
    into initial_data alongside cycle/findings. Control keys (retry_from_step,
    cycle_count, recoverable, findings_truncated, findings_original_bytes) are NOT
    forwarded.

    FAILS TODAY: engine.py:259 hardcodes initial_data={'cycle': next_cycle, 'findings': findings}
    — custom_forwarded_key is dropped.
    """
    captured: list[Any] = []
    retry_data = {
        "retry_from_step": 1,
        "cycle_count": 1,
        "findings": "f",
        "custom_forwarded_key": "v",
        # Control keys that must NOT be forwarded:
        "recoverable": True,
        "findings_truncated": False,
        "findings_original_bytes": 0,
    }
    workflow = _build_two_step_workflow(retry_data, captured)

    event_log = _FakeEventLog()
    engine = WorkflowEngine(event_log=event_log)
    engine.register("test_forwarding", workflow)

    ctx = _make_ctx(tmp_path)
    result, _ = engine.execute("test_forwarding", ctx)

    assert result.status == "ok", (
        f"expected ok after retry, got {result.status!r} error={result.error!r}"
    )
    # captured[0] is the initial pass's step1 prev (a StepResult from step0 first call —
    # but wait: step0's first call returns error+recoverable, so step1 is NOT called
    # in the initial pass (engine stops at the recoverable error and recurses).
    # captured[0] is therefore from the RETRY pass where prev = initial_data dict.
    assert len(captured) >= 1, (
        f"expected probe step called at least once, got captured={captured!r}"
    )
    # The retry pass prev is the initial_data dict
    retry_prev = captured[0]
    assert isinstance(retry_prev, dict), (
        f"expected initial_data dict as prev on retry pass, got {type(retry_prev)}"
    )
    assert "custom_forwarded_key" in retry_prev, (
        f"expected custom_forwarded_key in initial_data, got {list(retry_prev.keys())!r}. "
        "FAILS TODAY: engine.py:259 drops all non-cycle/findings keys."
    )
    assert retry_prev["custom_forwarded_key"] == "v", (
        f"expected custom_forwarded_key='v', got {retry_prev['custom_forwarded_key']!r}"
    )
    # cycle and findings must be present (always)
    assert retry_prev.get("cycle") == 2, (
        f"expected cycle=2 in initial_data, got {retry_prev.get('cycle')!r}"
    )
    assert retry_prev.get("findings") == "f", (
        f"expected findings='f' in initial_data, got {retry_prev.get('findings')!r}"
    )
    # Control keys must NOT appear in forwarded initial_data
    for control_key in ("retry_from_step", "cycle_count", "recoverable",
                        "findings_truncated", "findings_original_bytes"):
        assert control_key not in retry_prev, (
            f"control key {control_key!r} must NOT be forwarded into initial_data, "
            f"but found in {list(retry_prev.keys())!r}"
        )


# ─── AC12: retry_data_forwarded telemetry event ───────────────────────────────


def test_ac12_engine_emits_retry_data_forwarded_event(tmp_path):
    """AC12: Engine emits 'retry_data_forwarded' event with {phase, forwarded_keys: sorted([...])}
    on the recoverable-retry path.

    FAILS TODAY: no retry_data_forwarded event is emitted — engine.py does not have
    this event. The phase_retry_triggered event exists but does not carry forwarded_keys.
    """
    captured: list[Any] = []
    retry_data = {
        "retry_from_step": 1,
        "cycle_count": 1,
        "findings": "f",
        "red_commit_sha": "abc",
        "spec_path": "sp",
        "green_lint_findings": [{"check_id": "bare-except", "path": "f.py", "start": 1}],
    }
    workflow = _build_two_step_workflow(retry_data, captured)

    event_log = _FakeEventLog()
    engine = WorkflowEngine(event_log=event_log)
    engine.register("test_forwarding", workflow)

    ctx = _make_ctx(tmp_path)
    engine.execute("test_forwarding", ctx)

    forwarded_events = event_log.of_type("retry_data_forwarded")
    assert len(forwarded_events) == 1, (
        f"expected exactly 1 'retry_data_forwarded' event, got {len(forwarded_events)}. "
        "FAILS TODAY: this event type does not exist in engine.py."
    )
    evt = forwarded_events[0]
    assert "phase" in evt, (
        f"expected 'phase' key in retry_data_forwarded event, got {evt!r}"
    )
    assert evt["phase"] == "test_forwarding", (
        f"expected phase='test_forwarding', got {evt['phase']!r}"
    )
    assert "forwarded_keys" in evt, (
        f"expected 'forwarded_keys' in event, got {evt!r}"
    )
    fk = evt["forwarded_keys"]
    assert isinstance(fk, list), f"expected list, got {type(fk)}"
    # Must be sorted
    assert fk == sorted(fk), f"expected forwarded_keys to be sorted, got {fk!r}"
    # Must contain the custom keys (not control keys)
    assert "red_commit_sha" in fk, (
        f"expected red_commit_sha in forwarded_keys, got {fk!r}"
    )
    assert "spec_path" in fk, (
        f"expected spec_path in forwarded_keys, got {fk!r}"
    )
    assert "green_lint_findings" in fk, (
        f"expected green_lint_findings in forwarded_keys, got {fk!r}"
    )
    # Control keys must NOT appear in forwarded_keys
    for control_key in ("retry_from_step", "cycle_count", "findings"):
        assert control_key not in fk, (
            f"control key {control_key!r} must NOT appear in forwarded_keys list"
        )


# ─── AC13: backward compat — phase_45_spec retry produces no spurious keys ────


def test_ac13_backward_compat_phase45_retry_produces_clean_initial_data(tmp_path):
    """AC13: Backward compat: phase_45_spec retry-trigger (returning ONLY cycle_count,
    findings, retry_from_step) produces initial_data dict containing exactly
    {'cycle': N, 'findings': '...'} — no spurious forwarded keys.

    This test PASSES today and must continue to pass after GREEN (regression guard).
    """
    captured: list[Any] = []
    # phase_45_spec-style retry: only the 3 control keys. retry_from_step=1 used
    # here (not 0) so the probe at step 1 captures the initial_data dict directly
    # — semantically equivalent for testing FORWARDING behavior. retry_from_step=0
    # would route engine back to step 0 (returning a StepResult on its 2nd call),
    # making step 1's prev a StepResult and breaking the dict-introspection assertion.
    retry_data = {
        "retry_from_step": 1,
        "cycle_count": 1,
        "findings": "spec needs work",
    }
    workflow = _build_two_step_workflow(retry_data, captured)

    event_log = _FakeEventLog()
    engine = WorkflowEngine(event_log=event_log)
    engine.register("test_forwarding", workflow)

    ctx = _make_ctx(tmp_path)
    result, _ = engine.execute("test_forwarding", ctx)

    assert result.status == "ok", (
        f"expected ok, got {result.status!r}"
    )
    assert len(captured) >= 1, "expected probe step called"
    retry_prev = captured[0]
    assert isinstance(retry_prev, dict), (
        f"expected dict initial_data, got {type(retry_prev)}"
    )
    # Must contain cycle and findings
    assert retry_prev.get("cycle") == 2, (
        f"expected cycle=2, got {retry_prev.get('cycle')!r}"
    )
    assert retry_prev.get("findings") == "spec needs work", (
        f"expected findings='spec needs work', got {retry_prev.get('findings')!r}"
    )
    # Must NOT contain spurious forwarded keys (all 3 keys were control keys)
    allowed_keys = {"cycle", "findings"}
    extra_keys = set(retry_prev.keys()) - allowed_keys
    assert extra_keys == set(), (
        f"expected no spurious keys in initial_data for phase_45_spec-style retry, "
        f"got extra keys={extra_keys!r}. "
        "FAILS TODAY: not applicable (today no keys are forwarded, so this passes). "
        "After GREEN this guards that forwarding only applies to non-control keys."
    )
