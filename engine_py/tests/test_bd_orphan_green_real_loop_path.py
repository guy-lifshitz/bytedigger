"""Orphan-GREEN recovery through the REAL validation-loop composite (hal#1630/#1914
port follow-up).

The ported ACs replace workflow step 0 (`validation_cycle_loop`) with a function
that returns the guard's result directly, so they never exercise how a routing
result raised INSIDE the loop body leaves the composite. Here step 0 keeps its
production `execute` (`_validation_cycle_loop_execute` -> `LoopRunner.run`);
only the loop body is narrowed to the dirty-tree guard, so no LLM step runs.

The validation loop does not set `consume_recoverable_retry`, so `LoopRunner`
returns a recoverable body error unchanged; the outer engine then reads its
`retry_from_step` as an index into the top-level workflow and must re-enter at
`write_green_artifact`.
"""
from __future__ import annotations

from bytedigger_engine.contracts import LoopStepContract, StepContract, WorkflowDefinition
from bytedigger_engine.engine import WorkflowEngine
from bytedigger_engine.event_log import EventLog
from bytedigger_engine.workflows import phase_5_implement as p5

import test_gh1018_orphan_green_cycle_resolution as _gh1018


def test_routing_from_inside_the_loop_body_reaches_write_green_artifact(tmp_path, monkeypatch):
    manifest = ["src/module.py"]
    stage = _gh1018._stage(tmp_path, "realloop", monkeypatch, manifest=manifest, dirty_paths=manifest)
    spawned: list = []
    _gh1018._fence_billed_llm(monkeypatch, spawned)
    guard_prev = stage.prev

    real_builder = p5.build_validation_loop_contract
    monkeypatch.setattr(p5, "build_validation_loop_contract", lambda cap: _narrow(real_builder, cap, guard_prev))

    steps = p5.phase_5_implement_workflow().steps
    route_idx = _gh1018._step_index(_gh1018.ROUTE_TARGET)
    recorded: list = []
    workflow = WorkflowDefinition(
        name=_gh1018.PHASE_5_WORKFLOW_NAME,
        steps=_gh1018._observed_steps(list(steps[: route_idx + 1]), recorded),
    )
    engine = WorkflowEngine(EventLog(stage.log_path))
    engine.register(_gh1018.PHASE_5_WORKFLOW_NAME, workflow)
    final, _ = engine.execute(_gh1018.PHASE_5_WORKFLOW_NAME, stage.ctx, stage.run_id)

    executed = [r["name"] for r in recorded]
    assert executed == ["validation_cycle_loop", _gh1018.ROUTE_TARGET], (
        f"expected the real loop composite to hand the route to the outer engine, "
        f"which re-enters at {_gh1018.ROUTE_TARGET!r}; executed={executed!r}, "
        f"final={final.status}/{final.error_code}"
    )
    assert spawned == []


def _narrow(real_builder, cap: int, guard_prev) -> LoopStepContract:
    real = real_builder(cap)
    assert not real.consume_recoverable_retry, (
        "precondition: the validation loop hands recoverable body errors up to "
        "the outer engine; if it starts consuming them this test must change"
    )
    return LoopStepContract(
        name=real.name,
        body=[StepContract(
            name="verify_red_fails_mechanically",
            execute=lambda ctx, prev: p5._verify_red_fails_mechanically(ctx, guard_prev),
        )],
        max_iterations=cap,
        until_marker=real.until_marker,
        marker_field=real.marker_field,
    )
