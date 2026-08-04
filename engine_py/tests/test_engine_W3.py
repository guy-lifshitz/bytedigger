"""Wave 3 RED tests — 4-fix batch: engine.py CRIT-1, telemetry_ctx CRIT-2, run.py CRIT-3, contracts MED-4.

All tests in this file are expected to FAIL against the current implementation.
They document the required behaviour BEFORE the GREEN fixes are applied.
"""
from __future__ import annotations

import json
import sys
import threading
from pathlib import Path
from unittest.mock import patch

# Allow imports from engine_py root
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from bytedigger_engine.contracts import (
    StepContract,
    StepResult,
    WorkflowContext,
    WorkflowDefinition,
)
from bytedigger_engine.engine import WorkflowEngine
from bytedigger_engine import telemetry_ctx


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


def ok_step(name: str, payload=None) -> StepContract:
    def _run(_ctx, _prev):
        return StepResult(status="ok", data=payload, duration_ms=0, step_name=name)
    return StepContract(name=name, execute=_run)


# ─── AC1: _execute_steps None return (CRIT-1) ─────────────────────────────────

class TestExecuteStepsBeyondRange:
    """CRIT-1: _execute_steps with start_step >= len(steps) returns None today
    (type: ignore[return-value]); should raise RuntimeError after the fix."""

    def test_ac1_start_step_beyond_range_raises_runtime_error(self):
        """When start_step equals len(workflow.steps), no step executes.
        Current code: silently returns None (caller crashes on .status).
        Required: RuntimeError with message 'workflow executed zero steps'."""
        eng = WorkflowEngine()
        workflow = WorkflowDefinition(
            name="w",
            steps=[ok_step("s1"), ok_step("s2")],
        )
        eng.register("w", workflow)
        wf = eng._workflows["w"]

        # start_step == len(steps) → loop body never executes → last_result stays None
        with pytest.raises(RuntimeError, match="workflow executed zero steps"):
            eng._execute_steps(wf, make_ctx(), run_id="r", start_step=len(wf.steps))

    def test_ac1b_empty_workflow_still_returns_ok(self):
        """Empty steps list (workflow.steps == []) exits early with ok StepResult.
        This path is already handled (lines 100-106) and must remain unchanged."""
        eng = WorkflowEngine()
        workflow = WorkflowDefinition(name="empty", steps=[])
        eng.register("empty", workflow)
        wf = eng._workflows["empty"]

        result = eng._execute_steps(wf, make_ctx(), run_id="r")

        # Must NOT raise; must return ok StepResult
        assert result.status == "ok"
        assert result.step_name == "empty"


# ─── AC2: telemetry_ctx threading.local (CRIT-2) ──────────────────────────────

class TestTelemetryCtxThreadIsolation:
    """CRIT-2: _current is module-global today → thread-unsafe.
    After fix it must be threading.local() so each thread sees its own context."""

    def test_ac2_two_threads_see_independent_run_ids(self):
        """Thread A sets run_id='run-A', Thread B sets run_id='run-B'.
        Each thread must read back its OWN run_id, never the other's.

        Current implementation uses a module-global variable, so whichever thread
        writes last wins — the first thread will read the wrong value.
        The test uses a Barrier to synchronise both set_current_run calls so the
        race is deterministic in the failing direction."""

        barrier = threading.Barrier(2)
        results: dict[str, str | None] = {}
        errors: list[Exception] = []

        def worker(my_id: str) -> None:
            try:
                telemetry_ctx.set_current_run(
                    event_log=None,
                    run_id=my_id,
                    step_name="step",
                    phase="phase",
                )
                # Both threads have now called set_current_run; race is live.
                barrier.wait()
                # Read back — with threading.local() each sees its own value.
                ctx = telemetry_ctx.get_current_run()
                results[my_id] = ctx.run_id if ctx else None
            except Exception as exc:
                errors.append(exc)

        t1 = threading.Thread(target=worker, args=("run-A",))
        t2 = threading.Thread(target=worker, args=("run-B",))
        t1.start()
        t2.start()
        t1.join(timeout=5)
        t2.join(timeout=5)

        assert not errors, f"thread errors: {errors}"
        # Each thread must see only its own run_id
        assert results.get("run-A") == "run-A", (
            f"Thread A saw {results.get('run-A')!r} instead of 'run-A' "
            "(module-global variable leaked between threads)"
        )
        assert results.get("run-B") == "run-B", (
            f"Thread B saw {results.get('run-B')!r} instead of 'run-B'"
        )


# ─── AC3: run.py opaque except Exception (CRIT-3) ─────────────────────────────

class TestRunExceptionClassification:
    """CRIT-3: bare except Exception → E_RUNNER today.
    After fix: ValueError → E_BAD_CTX, FileNotFoundError → E_FILE_NOT_FOUND,
    unknown exception → E_RUNNER + exception_type field."""

    def _run_with_argv(self, argv: list[str]) -> dict:
        """Invoke run.main() with patched sys.argv, capture stdout JSON."""
        import io
        from bytedigger_engine import run as run_module
        from contextlib import redirect_stdout

        buf = io.StringIO()
        with patch("sys.argv", argv), redirect_stdout(buf):
            try:
                run_module.main()
            except SystemExit:
                pass
        output = buf.getvalue().strip()
        assert output, "run.main() produced no stdout"
        return json.loads(output)

    def test_ac3a_value_error_maps_to_e_bad_ctx(self):
        """ValueError raised during execution → error_code == 'E_BAD_CTX'
        with an 'error' field carrying the message.

        F7BE40B9 Ship #1 sibling-test co-change: previously patched
        run_module.make_engine().eng.execute; now patches the
        execute_durable_workflow public entry imported by run.py."""
        from bytedigger_engine import run as run_module

        def raising_execute(workflow_name, ctx_dict, run_id, event_log_path=None):
            raise ValueError("bad context field: scope")

        with patch.object(run_module, "execute_durable_workflow", raising_execute), \
             patch.object(run_module, "init_dbos", lambda: None):
            data = self._run_with_argv(
                ["run.py", "--workflow", "phase_0_research",
                 "--ctx-json", '{"question":"q"}']
            )

        assert data.get("error_code") == "E_BAD_CTX", (
            f"Expected E_BAD_CTX, got {data.get('error_code')!r}. "
            "Bare except Exception catch-all is masking ValueError."
        )
        assert "bad context field" in data.get("error", "")

    def test_ac3b_file_not_found_maps_to_e_file_not_found(self):
        """FileNotFoundError raised during execution → error_code == 'E_FILE_NOT_FOUND'.

        F7BE40B9 Ship #1 sibling-test co-change (see ac3a)."""
        from bytedigger_engine import run as run_module

        def raising_execute(workflow_name, ctx_dict, run_id, event_log_path=None):
            raise FileNotFoundError("/nonexistent/path.json")

        with patch.object(run_module, "execute_durable_workflow", raising_execute), \
             patch.object(run_module, "init_dbos", lambda: None):
            data = self._run_with_argv(
                ["run.py", "--workflow", "phase_0_research",
                 "--ctx-json", '{"question":"q"}']
            )

        assert data.get("error_code") == "E_FILE_NOT_FOUND", (
            f"Expected E_FILE_NOT_FOUND, got {data.get('error_code')!r}. "
            "Bare except Exception catch-all is masking FileNotFoundError."
        )

    def test_ac3c_unknown_exception_keeps_e_runner_with_exception_type(self):
        """Unclassified exceptions still → E_RUNNER but MUST include 'exception_type' field.

        F7BE40B9 Ship #1 sibling-test co-change (see ac3a)."""
        from bytedigger_engine import run as run_module

        class _Weird(Exception):
            pass

        def raising_execute(workflow_name, ctx_dict, run_id, event_log_path=None):
            raise _Weird("something unexpected")

        with patch.object(run_module, "execute_durable_workflow", raising_execute), \
             patch.object(run_module, "init_dbos", lambda: None):
            data = self._run_with_argv(
                ["run.py", "--workflow", "phase_0_research",
                 "--ctx-json", '{"question":"q"}']
            )

        assert data.get("error_code") == "E_RUNNER"
        assert "exception_type" in data, (
            f"'exception_type' field missing from E_RUNNER response. Got keys: {list(data.keys())}"
        )
        assert data["exception_type"] == "_Weird"


# ─── AC4: StepContract.execute annotation (MED-4) ─────────────────────────────

class TestStepContractAnnotation:
    """MED-4: contracts.py line 100 uses Callable[[Any, Any], StepResult].
    After fix: Callable[[WorkflowContext, StepResult | None], StepResult]."""

    def test_ac4_execute_annotation_contains_workflow_context_and_step_result_or_none(self):
        """StepContract.execute annotation must reference WorkflowContext and
        'StepResult | None' (not 'Any, Any').

        inspect.get_annotations (Python 3.10+) or __annotations__ is used so
        from __future__ import annotations (PEP 563 lazy strings) works correctly."""
        import inspect

        # Python 3.10+ provides inspect.get_annotations with eval_str support;
        # for 3.9 fallback we read the raw string annotation via __annotations__.
        raw = StepContract.__dataclass_fields__["execute"].type
        # With `from __future__ import annotations`, the stored value is a string.
        ann_str = str(raw)

        assert "WorkflowContext" in ann_str, (
            f"Expected 'WorkflowContext' in execute annotation, got: {ann_str!r}. "
            "contracts.py line 100 still uses Callable[[Any, Any], StepResult]."
        )
        # Accept both "StepResult | None" and "Optional[StepResult]" formulations
        assert ("StepResult | None" in ann_str or "Optional[StepResult]" in ann_str), (
            f"Expected 'StepResult | None' in execute annotation, got: {ann_str!r}."
        )
