"""
Tests for engine_py.contracts.

Pure dataclass contracts for orchestrator workflows.
"""

import dataclasses
import sys
from pathlib import Path

# Allow tests to import contracts.py from parent dir without pip install.
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from typing import Any, Callable

from contracts import (
    WorkflowContext,
    StepResult,
    StepContract,
    WorkflowDefinition,
)

# BudgetPolicy + WorkerExitResult are NEW (F2344E12 / 6F08F5F5 / D5699904).
# Imported lazily inside each test so existing tests still collect/pass while
# the new dataclasses are unimplemented (canonical RED).
try:
    from contracts import BudgetPolicy, WorkerExitResult  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover - RED phase
    BudgetPolicy = None  # type: ignore[assignment,misc]
    WorkerExitResult = None  # type: ignore[assignment,misc]


class TestWorkflowContext:
    """Tests for WorkflowContext frozen dataclass."""

    def test_workflow_context_creation(self):
        """Test creating WorkflowContext with all fields populated."""
        ctx = WorkflowContext(
            tenant_id="tenant-123",
            scope="PODIUM",
            db_path="/path/to/db.sqlite",
            org_config={"key": "value"},
            question="What is the security posture?",
            session_id="sess-456",
            persona="analyst",
            framework="NIST",
            domain="access_control",
            enable_rag=True,
            enable_reranker=False,
            message_history=[{"role": "user", "content": "Hello"}],
            llm_provider="azure",
            llm_model="gpt-4.1-datazone",
        )

        assert ctx.tenant_id == "tenant-123"
        assert ctx.scope == "PODIUM"
        assert ctx.db_path == "/path/to/db.sqlite"
        assert ctx.org_config == {"key": "value"}
        assert ctx.question == "What is the security posture?"
        assert ctx.session_id == "sess-456"
        assert ctx.persona == "analyst"
        assert ctx.framework == "NIST"
        assert ctx.domain == "access_control"
        assert ctx.enable_rag is True
        assert ctx.enable_reranker is False
        assert ctx.message_history == [{"role": "user", "content": "Hello"}]
        assert ctx.llm_provider == "azure"
        assert ctx.llm_model == "gpt-4.1-datazone"

    def test_workflow_context_defaults(self):
        """Test WorkflowContext with only required fields."""
        ctx = WorkflowContext(
            tenant_id="tenant-123",
            scope=None,
            db_path=None,
            org_config=None,
            question="What is the security posture?",
            session_id="sess-456",
            persona="ciso",
            framework=None,
            domain=None,
        )

        assert ctx.tenant_id == "tenant-123"
        assert ctx.scope is None
        assert ctx.db_path is None
        assert ctx.org_config is None
        assert ctx.enable_rag is True  # default
        assert ctx.enable_reranker is True  # default
        assert ctx.message_history is None  # default
        assert ctx.llm_provider == "azure"  # default
        assert ctx.llm_model == "gpt-4.1-datazone"  # default

    def test_workflow_context_frozen(self):
        """Test that WorkflowContext is frozen (immutable)."""
        ctx = WorkflowContext(
            tenant_id="tenant-123",
            scope="PODIUM",
            db_path="/path/to/db.sqlite",
            org_config={"key": "value"},
            question="What is the security posture?",
            session_id="sess-456",
            persona="analyst",
            framework="NIST",
            domain="access_control",
        )

        # Attempting to set an attribute should raise FrozenInstanceError
        with pytest.raises(dataclasses.FrozenInstanceError):
            ctx.tenant_id = "tenant-999"

    def test_workflow_context_message_history_independent(self):
        """Test that message_history defaults are independent (no shared mutable default)."""
        ctx1 = WorkflowContext(
            tenant_id="tenant-1",
            scope="PODIUM",
            db_path=None,
            org_config=None,
            question="Q1",
            session_id="sess-1",
            persona="analyst",
            framework=None,
            domain=None,
        )

        ctx2 = WorkflowContext(
            tenant_id="tenant-2",
            scope="PODIUM",
            db_path=None,
            org_config=None,
            question="Q2",
            session_id="sess-2",
            persona="analyst",
            framework=None,
            domain=None,
        )

        # Both should default to None
        assert ctx1.message_history is None
        assert ctx2.message_history is None


class TestStepResult:
    """Tests for StepResult dataclass."""

    def test_step_result_ok(self):
        """Test creating StepResult with ok status."""
        result = StepResult(
            status="ok",
            data={"key": "value"},
            duration_ms=100,
            step_name="test_step",
        )

        assert result.status == "ok"
        assert result.data == {"key": "value"}
        assert result.duration_ms == 100
        assert result.step_name == "test_step"
        assert result.error is None
        assert result.error_code is None

    def test_step_result_error(self):
        """Test creating StepResult with error status."""
        result = StepResult(
            status="error",
            data=None,
            duration_ms=50,
            step_name="failing_step",
            error="something broke",
            error_code="ERR_001",
        )

        assert result.status == "error"
        assert result.data is None
        assert result.duration_ms == 50
        assert result.step_name == "failing_step"
        assert result.error == "something broke"
        assert result.error_code == "ERR_001"

    def test_step_result_skip(self):
        """Test creating StepResult with skip status."""
        result = StepResult(
            status="skip",
            data=None,
            duration_ms=0,
            step_name="skipped_step",
        )

        assert result.status == "skip"
        assert result.data is None
        assert result.duration_ms == 0
        assert result.step_name == "skipped_step"
        assert result.error is None
        assert result.error_code is None

    def test_step_result_invalid_status(self):
        """Test that invalid status raises ValueError in __post_init__."""
        with pytest.raises(ValueError, match="status.*ok.*error.*skip"):
            StepResult(
                status="invalid",
                data=None,
                duration_ms=0,
                step_name="bad_step",
            )

    def test_step_result_data_can_be_any_type(self):
        """Test that StepResult.data can hold various types."""
        # Test with dict
        result1 = StepResult(
            status="ok", data={"nested": {"key": "value"}}, duration_ms=10, step_name="s1"
        )
        assert result1.data == {"nested": {"key": "value"}}

        # Test with list
        result2 = StepResult(
            status="ok", data=[1, 2, 3], duration_ms=10, step_name="s2"
        )
        assert result2.data == [1, 2, 3]

        # Test with string
        result3 = StepResult(
            status="ok", data="some string", duration_ms=10, step_name="s3"
        )
        assert result3.data == "some string"

    def test_step_result_negative_duration_ms(self):
        """Test that StepResult accepts negative duration_ms (data container, no validation)."""
        result = StepResult(
            status="ok",
            data={"key": "value"},
            duration_ms=-100,
            step_name="test_negative",
        )

        assert result.status == "ok"
        assert result.duration_ms == -100
        assert result.step_name == "test_negative"

    def test_step_result_empty_string_status(self):
        """Test that empty string status raises ValueError (same as invalid status)."""
        with pytest.raises(ValueError, match="status.*ok.*error.*skip"):
            StepResult(
                status="",
                data=None,
                duration_ms=0,
                step_name="empty_status_step",
            )


class TestStepContract:
    """Tests for StepContract dataclass."""

    def test_step_contract_creation(self):
        """Test creating StepContract with execute callable."""

        def dummy_execute(ctx: Any, prev_output: Any) -> StepResult:
            return StepResult(
                status="ok", data={"result": "test"}, duration_ms=10, step_name="dummy"
            )

        contract = StepContract(
            name="test_step",
            execute=dummy_execute,
            required_inputs=["input1", "input2"],
            outputs=["output1"],
            skip_on_error=False,
        )

        assert contract.name == "test_step"
        assert contract.execute == dummy_execute
        assert contract.required_inputs == ["input1", "input2"]
        assert contract.outputs == ["output1"]
        assert contract.skip_on_error is False

    def test_step_contract_defaults(self):
        """Test StepContract with default values."""

        def simple_execute(ctx: Any, prev_output: Any) -> StepResult:
            return StepResult(status="ok", data={}, duration_ms=5, step_name="simple")

        contract = StepContract(
            name="simple_step",
            execute=simple_execute,
        )

        assert contract.name == "simple_step"
        assert contract.execute == simple_execute
        assert contract.required_inputs == []
        assert contract.outputs == []
        assert contract.skip_on_error is False

    def test_step_contract_execute_returns_step_result(self):
        """Test that calling execute returns a StepResult."""

        def returning_execute(ctx: Any, prev_output: Any) -> StepResult:
            return StepResult(
                status="ok",
                data={"executed": True},
                duration_ms=25,
                step_name="returning",
            )

        contract = StepContract(
            name="returner",
            execute=returning_execute,
        )

        result = contract.execute(None, None)

        assert isinstance(result, StepResult)
        assert result.status == "ok"
        assert result.data == {"executed": True}
        assert result.duration_ms == 25

    def test_step_contract_with_lambda(self):
        """Test StepContract with lambda execute function."""
        contract = StepContract(
            name="lambda_step",
            execute=lambda ctx, prev: StepResult(
                status="ok", data={"lambda": True}, duration_ms=1, step_name="lambda_step"
            ),
        )

        assert contract.name == "lambda_step"
        result = contract.execute(None, None)
        assert result.status == "ok"
        assert result.data == {"lambda": True}


class TestWorkflowDefinition:
    """Tests for WorkflowDefinition dataclass."""

    def test_workflow_definition_creation(self):
        """Test creating WorkflowDefinition with multiple steps."""

        def step1_execute(ctx: Any, prev: Any) -> StepResult:
            return StepResult(status="ok", data={"step": 1}, duration_ms=10, step_name="s1")

        def step2_execute(ctx: Any, prev: Any) -> StepResult:
            return StepResult(status="ok", data={"step": 2}, duration_ms=15, step_name="s2")

        step1 = StepContract(name="step1", execute=step1_execute, outputs=["data1"])
        step2 = StepContract(
            name="step2",
            execute=step2_execute,
            required_inputs=["data1"],
            outputs=["data2"],
        )

        definition = WorkflowDefinition(
            name="test_workflow",
            steps=[step1, step2],
        )

        assert definition.name == "test_workflow"
        assert len(definition.steps) == 2
        assert definition.steps[0].name == "step1"
        assert definition.steps[1].name == "step2"
        assert definition.error_handler is None

    def test_workflow_definition_error_handler_optional(self):
        """Test that WorkflowDefinition.error_handler defaults to None."""

        def simple_step(ctx: Any, prev: Any) -> StepResult:
            return StepResult(status="ok", data={}, duration_ms=5, step_name="s")

        step = StepContract(name="step", execute=simple_step)

        definition = WorkflowDefinition(
            name="workflow",
            steps=[step],
        )

        assert definition.error_handler is None

    def test_workflow_definition_with_error_handler(self):
        """Test WorkflowDefinition with custom error handler."""

        def step_execute(ctx: Any, prev: Any) -> StepResult:
            return StepResult(status="ok", data={}, duration_ms=5, step_name="s")

        def error_handler(result: StepResult, ctx: Any) -> StepResult:
            return StepResult(
                status="error",
                data={"handled": True},
                duration_ms=result.duration_ms,
                step_name=result.step_name,
                error="Handled error",
            )

        step = StepContract(name="step", execute=step_execute)

        definition = WorkflowDefinition(
            name="workflow_with_handler",
            steps=[step],
            error_handler=error_handler,
        )

        assert definition.error_handler == error_handler
        assert definition.name == "workflow_with_handler"

    def test_workflow_definition_empty_steps(self):
        """Test WorkflowDefinition with empty steps list."""
        definition = WorkflowDefinition(
            name="empty_workflow",
            steps=[],
        )

        assert definition.name == "empty_workflow"
        assert definition.steps == []
        assert definition.error_handler is None

    def test_workflow_definition_complex_scenario(self):
        """Test WorkflowDefinition with complex multi-step workflow."""

        def step1(ctx: Any, prev: Any) -> StepResult:
            return StepResult(status="ok", data={"count": 1}, duration_ms=10, step_name="s1")

        def step2(ctx: Any, prev: Any) -> StepResult:
            return StepResult(status="ok", data={"count": 2}, duration_ms=20, step_name="s2")

        def step3(ctx: Any, prev: Any) -> StepResult:
            return StepResult(
                status="skip", data=None, duration_ms=0, step_name="s3"
            )

        def error_handler(result: StepResult, ctx: Any) -> StepResult:
            return StepResult(
                status="ok",
                data={"recovered": True},
                duration_ms=5,
                step_name=result.step_name,
            )

        steps = [
            StepContract(name="extract", execute=step1, outputs=["extracted"]),
            StepContract(
                name="validate",
                execute=step2,
                required_inputs=["extracted"],
                outputs=["validated"],
                skip_on_error=True,
            ),
            StepContract(
                name="enrich",
                execute=step3,
                required_inputs=["validated"],
                outputs=["enriched"],
                skip_on_error=False,
            ),
        ]

        definition = WorkflowDefinition(
            name="complex_workflow",
            steps=steps,
            error_handler=error_handler,
        )

        assert definition.name == "complex_workflow"
        assert len(definition.steps) == 3
        assert definition.steps[1].skip_on_error is True
        assert definition.error_handler is not None


class TestFormatBoundaryError:
    """Tests for format_boundary_error helper function (error-as-feed-forward pattern)."""

    def test_format_boundary_error_basic_field_presence(self):
        """Test that format_boundary_error includes all 5 required fields in output."""
        from contracts import format_boundary_error

        result = format_boundary_error(
            phase="phase_45_spec_lite",
            field="review_path",
            producer="phase_45_spec.write_review_doc",
            where="phase_45_spec_lite.py:611",
            schema="StepResult.data.review_path",
        )

        # Verify all 5 values appear as substrings in the result
        assert "phase_45_spec_lite" in result
        assert "review_path" in result
        assert "phase_45_spec.write_review_doc" in result
        assert "phase_45_spec_lite.py:611" in result
        assert "StepResult.data.review_path" in result

    def test_format_boundary_error_labeled_fields(self):
        """Test that format_boundary_error includes recognizable field labels (phase:, field:, etc)."""
        from contracts import format_boundary_error

        result = format_boundary_error(
            phase="phase_1_validate_context",
            field="scope",
            producer="run.py:150",
            where="contracts.py:42",
            schema="WorkflowContext.scope",
        )

        # Verify labeled format: each field label should appear in result
        assert "phase:" in result or "phase=" in result
        assert "field:" in result or "field=" in result
        assert "producer:" in result or "producer=" in result
        assert "where:" in result or "where=" in result
        assert "schema:" in result or "schema=" in result


# ────────────────────────────────────────────────────────────────────
# BudgetPolicy tests (F2344E12 / 6F08F5F5) — 13 tests
# ────────────────────────────────────────────────────────────────────


def test_budget_policy_rejects_negative_max_cost_per_item_usd():
    """Case 1: __post_init__ rejects negative max_cost_per_item_usd."""
    with pytest.raises(ValueError, match="max_cost_per_item_usd"):
        BudgetPolicy(max_cost_per_item_usd=-1.0)


def test_budget_policy_rejects_warn_greater_than_abort():
    """Case 2: __post_init__ rejects window with warn > abort."""
    with pytest.raises(ValueError, match="warn"):
        BudgetPolicy(
            subscription_window_warn_pct={"five_hour": 90, "seven_day": 60},
            subscription_window_abort_pct={"five_hour": 80, "seven_day": 80},
        )


def test_budget_policy_rejects_hard_ceiling_below_max_abort():
    """Case 3: __post_init__ rejects hard_ceiling_pct < max abort_pct."""
    with pytest.raises(ValueError, match="hard_ceiling_pct"):
        BudgetPolicy(
            subscription_window_abort_pct={"five_hour": 80, "seven_day": 80},
            subscription_window_hard_ceiling_pct=70,
        )


def test_budget_policy_rejects_missing_window_key():
    """Case 4: __post_init__ rejects missing window key in dicts.

    Opus concern #3: cover both abort_pct AND warn_pct missing-key branches.
    """
    # abort_pct missing seven_day
    with pytest.raises(ValueError, match="abort_pct"):
        BudgetPolicy(
            subscription_window_abort_pct={"five_hour": 80},  # missing seven_day
        )
    # warn_pct missing seven_day (abort_pct intact)
    with pytest.raises(ValueError, match="warn_pct"):
        BudgetPolicy(
            subscription_window_warn_pct={"five_hour": 60},  # missing seven_day
        )


def test_budget_policy_trip_for_window_abort():
    """Case 5: trip_for_window(82, 'five_hour') with default 80/60 → 'abort'."""
    policy = BudgetPolicy()
    assert policy.trip_for_window(82, "five_hour") == "abort"


def test_budget_policy_trip_for_window_ceiling():
    """Case 6: trip_for_window(95, 'seven_day') with default 95 ceiling → 'ceiling'."""
    policy = BudgetPolicy()
    assert policy.trip_for_window(95, "seven_day") == "ceiling"


def test_budget_policy_trip_for_window_warn():
    """Case 7: trip_for_window(65, 'five_hour') → 'warn'."""
    policy = BudgetPolicy()
    assert policy.trip_for_window(65, "five_hour") == "warn"


def test_budget_policy_trip_for_window_none():
    """Case 8: trip_for_window(40, 'five_hour') → None."""
    policy = BudgetPolicy()
    assert policy.trip_for_window(40, "five_hour") is None


def test_budget_policy_trip_for_window_unknown_raises():
    """Case 9: trip_for_window(50, 'unknown_window') raises ValueError."""
    policy = BudgetPolicy()
    with pytest.raises(ValueError, match="unknown_window"):
        policy.trip_for_window(50, "unknown_window")


def test_budget_policy_fits_per_item_cumulative_true_overflow():
    """Case 10: fits_per_item(projected=5.0, cumulative_so_far=10.0) cap=12 cumulative=True → False."""
    policy = BudgetPolicy(max_cost_per_item_usd=12.0, per_item_cumulative_cost=True)
    assert policy.fits_per_item(projected_usd=5.0, cumulative_so_far_usd=10.0) is False


def test_budget_policy_fits_per_item_cumulative_false_passes():
    """Case 11: fits_per_item with same args, cumulative=False → True."""
    policy = BudgetPolicy(max_cost_per_item_usd=12.0, per_item_cumulative_cost=False)
    assert policy.fits_per_item(projected_usd=5.0, cumulative_so_far_usd=10.0) is True


def test_budget_policy_fits_per_run():
    """Case 12: fits_per_run with cap None → always True; and cap enforced when set.

    Opus concern #4: original "no_cap_always_true" was too narrow — also
    assert the active-cap branch returns False on overflow.
    """
    # No cap: always fits
    policy_no_cap = BudgetPolicy(max_cost_per_run_usd=None)
    assert policy_no_cap.fits_per_run(projected_usd=999_999.0, run_so_far_usd=999_999.0) is True
    # Active cap: 5 + 10 = 15 > 12 → does not fit
    policy_cap = BudgetPolicy(max_cost_per_run_usd=12.0)
    assert policy_cap.fits_per_run(projected_usd=10.0, run_so_far_usd=5.0) is False


def test_budget_policy_is_frozen():
    """Case 13: Frozen dataclass — assigning to attribute raises FrozenInstanceError."""
    policy = BudgetPolicy()
    with pytest.raises(dataclasses.FrozenInstanceError):
        policy.per_chain_restart_cap = 99  # type: ignore[misc]


def test_budget_policy_dict_field_mutation_raises():
    """Dict fields are MappingProxyType-wrapped — mutation raises TypeError."""
    from types import MappingProxyType

    policy = BudgetPolicy()
    with pytest.raises(TypeError):
        policy.subscription_window_abort_pct["five_hour"] = 99  # type: ignore[index]
    with pytest.raises(TypeError):
        policy.subscription_window_warn_pct["seven_day"] = 0  # type: ignore[index]


def test_budget_policy_caller_dict_mutation_does_not_bleed():
    """Mutating the caller's original dict after construction has no effect on the policy."""
    abort_dict = {"five_hour": 80, "seven_day": 80}
    warn_dict = {"five_hour": 60, "seven_day": 60}
    policy = BudgetPolicy(
        subscription_window_abort_pct=abort_dict,
        subscription_window_warn_pct=warn_dict,
    )
    abort_dict["five_hour"] = 99
    warn_dict["seven_day"] = 0
    assert policy.subscription_window_abort_pct["five_hour"] == 80
    assert policy.subscription_window_warn_pct["seven_day"] == 60


def test_budget_policy_dict_field_is_mappingproxy():
    """Both dict fields are MappingProxyType instances after construction."""
    from types import MappingProxyType

    policy = BudgetPolicy()
    assert isinstance(policy.subscription_window_abort_pct, MappingProxyType)
    assert isinstance(policy.subscription_window_warn_pct, MappingProxyType)


# ────────────────────────────────────────────────────────────────────
# WorkerExitResult tests (D5699904 / F2344E12 CC-1+CC-3+FI-4 / 6F08F5F5) — 13 tests
# ────────────────────────────────────────────────────────────────────


def _wer_kwargs(**overrides: Any) -> dict[str, Any]:
    """Helper: minimal-valid WorkerExitResult kwargs, override per case."""
    base: dict[str, Any] = dict(
        schema_version=1,
        wid="w-1",
        status="completed",
        exit_code=0,
        sha="abc1234",
        error_code=None,
        recoverable=False,
        suggestion=None,
        cost_usd=0.0,
        tokens_in=0,
        tokens_out=0,
        cache_read_tokens=0,
        rate_limits=None,
        attempts=1,
    )
    base.update(overrides)
    return base


def test_worker_exit_result_completed_requires_sha():
    """Case 1: status='completed' with no sha → ValueError."""
    with pytest.raises(ValueError, match="sha"):
        WorkerExitResult(**_wer_kwargs(status="completed", sha=None))


def test_worker_exit_result_completed_rejects_error_code():
    """Case 2: status='completed' with error_code → ValueError."""
    with pytest.raises(ValueError, match="error_code"):
        WorkerExitResult(**_wer_kwargs(status="completed", sha="abc", error_code="oops"))


def test_worker_exit_result_failed_requires_error_code():
    """Case 3: status='failed' with no error_code → ValueError."""
    with pytest.raises(ValueError, match="error_code"):
        WorkerExitResult(
            **_wer_kwargs(status="failed", sha=None, error_code=None, exit_code=1)
        )


def test_worker_exit_result_rejects_bogus_status():
    """Case 4: status='bogus' → ValueError."""
    with pytest.raises(ValueError, match="status"):
        WorkerExitResult(**_wer_kwargs(status="bogus"))


def test_worker_exit_result_rejects_zero_attempts():
    """Case 5: attempts=0 → ValueError."""
    with pytest.raises(ValueError, match="attempts"):
        WorkerExitResult(**_wer_kwargs(attempts=0))


def test_worker_exit_result_rejects_negative_cost_usd():
    """Case 6: cost_usd=-1 → ValueError."""
    with pytest.raises(ValueError, match="cost_usd"):
        WorkerExitResult(**_wer_kwargs(cost_usd=-1.0))


@pytest.mark.parametrize("field_name", ["tokens_in", "tokens_out", "cache_read_tokens"])
def test_worker_exit_result_rejects_negative_tokens_in(field_name):
    """Case 7: negative token count on any of tokens_in/tokens_out/cache_read_tokens → ValueError.

    Opus concern #2: parametrized over all three fields so each branch of the
    validation loop is exercised; only the field under test is -1.
    """
    overrides = {"tokens_in": 0, "tokens_out": 0, "cache_read_tokens": 0, field_name: -1}
    with pytest.raises(ValueError, match=field_name):
        WorkerExitResult(**_wer_kwargs(**overrides))


def test_worker_exit_result_rate_limits_missing_five_hour():
    """Case 8: rate_limits missing 'five_hour' key → ValueError."""
    with pytest.raises(ValueError, match="five_hour"):
        WorkerExitResult(
            **_wer_kwargs(
                rate_limits={
                    "seven_day": {"used_percentage": 10, "resets_at": 1700000000}
                }
            )
        )


def test_worker_exit_result_rate_limits_missing_used_percentage():
    """Case 9: rate_limits['five_hour'] missing 'used_percentage' → ValueError."""
    with pytest.raises(ValueError, match="used_percentage"):
        WorkerExitResult(
            **_wer_kwargs(
                rate_limits={
                    "five_hour": {"resets_at": 1700000000},
                    "seven_day": {"used_percentage": 10, "resets_at": 1700000000},
                }
            )
        )


def test_worker_exit_result_rate_limits_none_accepted():
    """Case 10: rate_limits=None accepted (older harness)."""
    result = WorkerExitResult(**_wer_kwargs(rate_limits=None))
    assert result.rate_limits is None


def test_worker_exit_result_envelope_round_trip():
    """Case 11: round-trip to_envelope_dict → from_envelope_dict produces equal value."""
    original = WorkerExitResult(
        **_wer_kwargs(
            status="failed",
            sha=None,
            error_code="rate_limit_window_5h",
            exit_code=2,
            recoverable=True,
            suggestion="wait for 5h window reset (43m)",
            cost_usd=1.23,
            tokens_in=100,
            tokens_out=50,
            cache_read_tokens=200,
            rate_limits={
                "five_hour": {"used_percentage": 82, "resets_at": 1700000000},
                "seven_day": {"used_percentage": 30, "resets_at": 1700500000},
            },
            attempts=2,
        )
    )
    envelope = original.to_envelope_dict()
    restored = WorkerExitResult.from_envelope_dict(envelope)
    assert restored == original
    # Opus concern #1: dict aliasing — round-trip must produce independent dicts
    restored.metadata["aliasing_canary"] = True
    assert "aliasing_canary" not in (original.metadata or {}), "metadata dict aliased across round-trip"
    restored.rate_limits["five_hour"]["aliasing_canary"] = 1
    assert "aliasing_canary" not in original.rate_limits["five_hour"], "rate_limits dict aliased across round-trip"


def test_worker_exit_result_from_envelope_unknown_schema_version_loads():
    """Case 12: from_envelope_dict with unknown schema_version still loads (dispatcher's job to refuse)."""
    envelope = {
        "schema_version": 99,
        "wid": "w-99",
        "status": "completed",
        "exit_code": 0,
        "sha": "deadbee",
        "error_code": None,
        "recoverable": False,
        "suggestion": None,
        "cost_usd": 0.0,
        "tokens_in": 0,
        "tokens_out": 0,
        "cache_read_tokens": 0,
        "rate_limits": None,
        "attempts": 1,
        "metadata": {},
    }
    restored = WorkerExitResult.from_envelope_dict(envelope)
    assert restored.schema_version == 99


def test_worker_exit_result_metadata_default_not_shared():
    """Case 13: metadata defaults to empty dict, not shared across instances."""
    a = WorkerExitResult(**_wer_kwargs(wid="w-a"))
    b = WorkerExitResult(**_wer_kwargs(wid="w-b"))
    assert a.metadata == {}
    assert b.metadata == {}
    a.metadata["phase"] = "phase_2"
    assert "phase" not in b.metadata


# Code-review M1+M2 fixes — bug repros that should ValueError, not silently pass / KeyError.

def test_budget_policy_rejects_asymmetric_window_dicts():
    """M1: abort_pct and warn_pct must have identical key sets.

    Without this guard, trip_for_window() raises bare KeyError instead of
    ValueError when called for a window present in abort_pct but missing
    from warn_pct.
    """
    with pytest.raises(ValueError, match="identical key sets"):
        BudgetPolicy(
            subscription_window_abort_pct={"five_hour": 80, "seven_day": 80, "thirty_day": 50},
            subscription_window_warn_pct={"five_hour": 60, "seven_day": 60},
        )


def test_worker_exit_result_rate_limits_used_percentage_must_be_int():
    """M2: used_percentage must be int, not str (e.g. malformed hook returning '80')."""
    with pytest.raises(ValueError, match="used_percentage.*int"):
        WorkerExitResult(
            **_wer_kwargs(
                rate_limits={
                    "five_hour": {"used_percentage": "80", "resets_at": 1700000000},
                    "seven_day": {"used_percentage": 10, "resets_at": 1700000000},
                }
            )
        )


def test_worker_exit_result_rate_limits_resets_at_must_be_int():
    """M2: resets_at must be int, not None (e.g. malformed hook returning null)."""
    with pytest.raises(ValueError, match="resets_at.*int"):
        WorkerExitResult(
            **_wer_kwargs(
                rate_limits={
                    "five_hour": {"used_percentage": 10, "resets_at": None},
                    "seven_day": {"used_percentage": 10, "resets_at": 1700000000},
                }
            )
        )
