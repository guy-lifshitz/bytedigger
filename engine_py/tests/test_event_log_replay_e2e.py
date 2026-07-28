"""End-to-end: engine writes events → file → derive_state replays them.

Single integration test that proves the Stage 1c invariant:
    engine.execute(... event_log=...) → readable jsonl → replay() → derived state
matches what we'd reasonably expect a status CLI to show.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

from contracts import StepContract, StepResult, WorkflowContext, WorkflowDefinition  # noqa: E402
from derive_state import replay  # noqa: E402
from engine import WorkflowEngine  # noqa: E402
from event_log import EventLog  # noqa: E402


def make_ctx() -> WorkflowContext:
    return WorkflowContext(
        tenant_id="t",
        scope=None,
        db_path=None,
        org_config=None,
        question="",
        session_id="s",
        persona="p",
        framework=None,
        domain=None,
    )


def test_engine_run_then_replay_produces_expected_derived_state(tmp_path):
    log = EventLog(tmp_path / "events.jsonl")
    eng = WorkflowEngine(event_log=log)

    def s1(_c, _p):
        return StepResult(status="ok", data=1, duration_ms=10, step_name="s1")

    def s2(_c, _p):
        return StepResult(status="ok", data=2, duration_ms=5, step_name="s2")

    eng.register(
        "two_step",
        WorkflowDefinition(
            name="two_step",
            steps=[
                StepContract(name="s1", execute=s1),
                StepContract(name="s2", execute=s2),
            ],
        ),
    )

    eng.execute("two_step", make_ctx(), run_id="run-A")

    # Round-trip: read events back from disk and replay.
    events = EventLog(tmp_path / "events.jsonl").read_all()
    assert [e["event_type"] for e in events] == [
        "workflow_started",
        "run_identity",  # bd#18 AC-E2: immediately after workflow_started
        "step_started",
        "step_finished",
        "step_started",
        "step_finished",
        "phase_artifacts",  # bd#18 AC-E5: from execute()'s finally, before the rollup/terminal event
        "workflow_cost_rollup",  # GH452: per-run cost rollup, emitted just before workflow_finished
        "workflow_finished",
    ]

    state = replay(events)
    run = state["runs"]["run-A"]
    assert run["workflow_name"] == "two_step"
    assert run["status"] == "ok"
    assert [s["step_name"] for s in run["steps"]] == ["s1", "s2"]
    assert all(s["status"] == "ok" for s in run["steps"])
    assert run["steps"][0]["duration_ms"] == 10
    assert run["steps"][1]["duration_ms"] == 5


def test_two_consecutive_runs_are_isolated_in_replay(tmp_path):
    log = EventLog(tmp_path / "events.jsonl")
    eng = WorkflowEngine(event_log=log)
    eng.register(
        "noop",
        WorkflowDefinition(
            name="noop",
            steps=[
                StepContract(
                    name="x",
                    execute=lambda _c, _p: StepResult(status="ok", data=None, duration_ms=1, step_name="x"),
                )
            ],
        ),
    )

    eng.execute("noop", make_ctx(), run_id="A")
    eng.execute("noop", make_ctx(), run_id="B")

    state = replay(EventLog(tmp_path / "events.jsonl").read_all())
    assert set(state["runs"]) == {"A", "B"}
    for rid in ("A", "B"):
        assert state["runs"][rid]["status"] == "ok"
        assert len(state["runs"][rid]["steps"]) == 1


def test_failed_run_replays_to_error_status(tmp_path):
    log = EventLog(tmp_path / "events.jsonl")
    eng = WorkflowEngine(event_log=log)
    eng.register(
        "bad",
        WorkflowDefinition(
            name="bad",
            steps=[
                StepContract(
                    name="b",
                    execute=lambda _c, _p: StepResult(
                        status="error",
                        data=None,
                        duration_ms=2,
                        step_name="b",
                        error="kaboom",
                        error_code="E_TEST",
                    ),
                )
            ],
        ),
    )

    eng.execute("bad", make_ctx(), run_id="X")

    state = replay(EventLog(tmp_path / "events.jsonl").read_all())
    assert state["runs"]["X"]["status"] == "error"
    assert state["runs"]["X"]["steps"][0]["error"] == "kaboom"
