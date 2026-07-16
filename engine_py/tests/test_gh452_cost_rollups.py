"""RED tests for GH452 — token-cost rollup event + REVISE-cycle attribution.

Spec: SHARED/memory/Decisions/2026-07-09_GH452_cost_rollups_spec.md

DO NOT modify any non-test file. `lib.cost_rollup` does not exist yet — it is
imported INSIDE test bodies (never at module top level) so the file COLLECTS
cleanly and fails at assert/call time (§1q ext, D1CF5FDF).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

import telemetry_ctx
from telemetry_ctx import _RunCtx
from contracts import StepContract, StepResult, WorkflowContext, WorkflowDefinition
from engine import WorkflowEngine
from event_log import EventLog
import llm_subprocess


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


# ─── AC1 ────────────────────────────────────────────────────────────────────


def test_ac1_set_current_run_accepts_cycle_and_get_current_run_reflects_it(tmp_path):
    """AC1: set_current_run(..., cycle=3) -> get_current_run().cycle == 3."""
    log = EventLog(tmp_path / "events.jsonl")
    telemetry_ctx.set_current_run(
        event_log=log, run_id="r1", step_name="s1", phase="p1", cycle=3,
    )
    ctx = telemetry_ctx.get_current_run()
    assert ctx.cycle == 3


# ─── AC2 ────────────────────────────────────────────────────────────────────


def test_ac2_set_current_run_from_preserves_prev_cycle(tmp_path):
    """AC2: set_current_run_from(prev, step_name='x') preserves prev.cycle (=5)."""
    log = EventLog(tmp_path / "events.jsonl")
    telemetry_ctx.set_current_run(
        event_log=log, run_id="r2", step_name="orig", phase="p1", cycle=5,
    )
    prev = telemetry_ctx.get_current_run()
    telemetry_ctx.set_current_run_from(prev, step_name="x")
    ctx = telemetry_ctx.get_current_run()
    assert ctx.cycle == 5


# ─── AC3 / AC4 ──────────────────────────────────────────────────────────────


class _FakeProc:
    def __init__(self):
        self.pid = 4242
        self.returncode = 0
        self.stdin = None
        self.stdout = None
        self.stderr = None


def _drive_stubbed_subprocess(monkeypatch, tmp_path, cycle_value):
    """Shared fixture for AC3/AC4: stub the kernel boundary (subprocess.Popen
    + the stream-read loop + token/cost extraction) so the real payload-
    building code at llm_subprocess.py:1191/1317 executes and appends to a
    real events.jsonl via EventLog."""
    log_path = tmp_path / "events.jsonl"
    log = EventLog(log_path)

    monkeypatch.setattr(llm_subprocess.subprocess, "Popen", lambda *a, **k: _FakeProc())
    monkeypatch.setattr(
        llm_subprocess, "_stream_read_events",
        lambda **k: (False, "stdout", "", [{"type": "result"}], False, False),
    )
    monkeypatch.setattr(
        llm_subprocess, "_tokens_and_cost_from_events",
        lambda events: ({"input": 10, "output": 20}, 0.05),
    )

    run_ctx = _RunCtx(
        event_log=log, run_id="run-cyc", step_name="step1", phase="phaseA", cycle=cycle_value,
    )

    llm_subprocess._invoke_subprocess(
        prompt="hi",
        model="claude-3",
        timeout_sec=30,
        step_name="step1",
        extra_data=None,
        allowed_tools=None,
        run_ctx=run_ctx,
    )

    return EventLog(log_path).read_all()


def test_ac3_subprocess_exited_payload_carries_cycle(monkeypatch, tmp_path):
    """AC3: subprocess_exited payload contains "cycle" == run_ctx.cycle (real
    row appended to tmp events.jsonl via EventLog)."""
    rows = _drive_stubbed_subprocess(monkeypatch, tmp_path, cycle_value=2)
    exited = [r for r in rows if r["event_type"] == "subprocess_exited"]
    assert len(exited) == 1
    assert exited[0]["payload"]["cycle"] == 2


def test_ac4_subprocess_spawned_payload_carries_cycle(monkeypatch, tmp_path):
    """AC4: subprocess_spawned payload contains "cycle" == 2 (same fixture as AC3)."""
    rows = _drive_stubbed_subprocess(monkeypatch, tmp_path, cycle_value=2)
    spawned = [r for r in rows if r["event_type"] == "subprocess_spawned"]
    assert len(spawned) == 1
    assert spawned[0]["payload"]["cycle"] == 2


# ─── AC5 / AC6 ──────────────────────────────────────────────────────────────


def _write_ac5_fixture(tmp_path) -> Path:
    events_path = tmp_path / "rollup_events.jsonl"
    rows = [
        {"run_id": "R", "event_type": "subprocess_exited",
         "payload": {"phase": "p1", "cycle": 1, "tokens_in": 1, "tokens_out": 1, "cost_usd": 0.1}},
        {"run_id": "R", "event_type": "subprocess_exited",
         "payload": {"phase": "p1", "cycle": 2, "tokens_in": 1, "tokens_out": 1, "cost_usd": 0.2}},
        {"run_id": "R", "event_type": "subprocess_exited",
         "payload": {"phase": "p2", "cycle": 1, "tokens_in": 1, "tokens_out": 1, "cost_usd": 0.3}},
        {"run_id": "OTHER", "event_type": "subprocess_exited",
         "payload": {"phase": "p1", "cycle": 1, "tokens_in": 1, "tokens_out": 1, "cost_usd": 99.0}},
        {"run_id": "R", "event_type": "runner_result_consumed",
         "payload": {"phase": "p3", "cycle": 1, "tokens_in": 1, "tokens_out": 1, "cost_usd": 0.05}},
    ]
    lines = [json.dumps(r) for r in rows]
    lines.append("{not valid json")
    events_path.write_text("\n".join(lines) + "\n")
    return events_path


def test_ac5_compute_cost_rollup_aggregates_calls_phase_and_cycle(tmp_path):
    """AC5: compute_cost_rollup over a 4-row (incl. runner_result_consumed)
    +other-run+malformed fixture -> calls=4, cost_usd=0.65,
    by_phase.p1.cost_usd=0.3, by_cycle."2".cost_usd=0.2, and the
    runner_result_consumed row is included in the aggregate."""
    from lib.cost_rollup import compute_cost_rollup

    events_path = _write_ac5_fixture(tmp_path)
    rollup = compute_cost_rollup(events_path, "R")

    assert rollup["calls"] == 4
    assert rollup["cost_usd"] == pytest.approx(0.65, abs=1e-9)
    assert rollup["by_phase"]["p1"]["cost_usd"] == pytest.approx(0.3, abs=1e-9)
    assert rollup["by_cycle"]["2"]["cost_usd"] == pytest.approx(0.2, abs=1e-9)
    assert "OTHER" not in json.dumps(rollup)
    # runner_result_consumed row (cost 0.05) must be reflected in the total —
    # if it were dropped, cost_usd would be 0.6 not 0.65.
    assert rollup["cost_usd"] == pytest.approx(0.1 + 0.2 + 0.3 + 0.05, abs=1e-9)


def test_ac6_rows_without_cycle_bucket_unattributed_and_missing_file_returns_zero_rollup(tmp_path):
    """AC6: rows lacking `cycle` bucket under by_cycle."unattributed"; a row
    with missing/None token fields counts as 0, does not raise, and IS
    counted in `calls`; missing file -> {calls:0, cost_usd:0.0, ...} no raise."""
    from lib.cost_rollup import compute_cost_rollup

    events_path = tmp_path / "no_cycle_events.jsonl"
    row = {"run_id": "R2", "event_type": "subprocess_exited",
           "payload": {"phase": "p1", "tokens_in": 1, "tokens_out": 1, "cost_usd": 0.5}}
    events_path.write_text(json.dumps(row) + "\n")

    rollup = compute_cost_rollup(events_path, "R2")
    assert rollup["by_cycle"]["unattributed"]["cost_usd"] == pytest.approx(0.5, abs=1e-9)

    missing_path = tmp_path / "does_not_exist.jsonl"
    empty_rollup = compute_cost_rollup(missing_path, "anything")
    assert empty_rollup["calls"] == 0
    assert empty_rollup["cost_usd"] == 0.0

    # Third case: missing/None token fields count as 0, never raise, and
    # the row still increments `calls`.
    missing_fields_path = tmp_path / "missing_fields_events.jsonl"
    row3 = {"run_id": "R3", "event_type": "subprocess_exited",
            "payload": {"phase": "p1", "cycle": 1, "tokens_in": None, "tokens_out": None, "cost_usd": None}}
    missing_fields_path.write_text(json.dumps(row3) + "\n")

    rollup3 = compute_cost_rollup(missing_fields_path, "R3")
    assert rollup3["calls"] == 1
    assert rollup3["cost_usd"] == 0.0
    assert rollup3["tokens_in"] == 0
    assert rollup3["tokens_out"] == 0


# ─── AC7 / AC8 / AC9 ────────────────────────────────────────────────────────


def _emitting_echo_step(ctx, _prev):
    """Step that emits one subprocess_exited-shaped row via telemetry_ctx,
    mirroring what invoke_llm_subprocess would append under a set run-ctx."""
    run_ctx = telemetry_ctx.get_current_run()
    if run_ctx is not None and run_ctx.event_log is not None:
        run_ctx.event_log.append(
            "subprocess_exited",
            {"phase": run_ctx.phase, "tokens_in": 5, "tokens_out": 5, "cost_usd": 0.25},
            run_ctx.run_id,
        )
    return StepResult(status="ok", data={"echoed": ctx.question}, duration_ms=0, step_name="echo")


def _run_echo_workflow(tmp_path):
    log_path = tmp_path / "events.jsonl"
    log = EventLog(log_path)
    eng = WorkflowEngine(event_log=log)
    eng.register(
        "echo_gh452",
        WorkflowDefinition(name="echo_gh452", steps=[StepContract(name="echo", execute=_emitting_echo_step)]),
    )
    result, _ctx = eng.execute("echo_gh452", make_ctx(), run_id="run-echo-1")
    rows = EventLog(log_path).read_all()
    return result, rows


def test_ac7_workflow_cost_rollup_event_emitted_before_workflow_finished(tmp_path):
    """AC7: after engine.execute of a 1-step echo workflow with a file-backed
    EventLog whose step emits a subprocess_exited row, events.jsonl contains a
    workflow_cost_rollup event for that run_id BEFORE workflow_finished, and
    its payload.cost_usd equals the fixture sum (0.25)."""
    _result, rows = _run_echo_workflow(tmp_path)

    rollup_idxs = [i for i, r in enumerate(rows) if r["event_type"] == "workflow_cost_rollup"]
    finished_idxs = [i for i, r in enumerate(rows) if r["event_type"] == "workflow_finished"]

    assert len(rollup_idxs) == 1, "expected exactly one workflow_cost_rollup event"
    assert len(finished_idxs) == 1
    assert rollup_idxs[0] < finished_idxs[0]

    rollup_row = rows[rollup_idxs[0]]
    assert rollup_row["run_id"] == "run-echo-1"
    assert rollup_row["payload"]["cost_usd"] == pytest.approx(0.25, abs=1e-9)


def test_ac8_workflow_finished_payload_keys_unchanged(tmp_path):
    """AC8: workflow_finished payload keys remain exactly
    {workflow_name, status, wall_ms} (DA19030A byte-parity guard)."""
    _result, rows = _run_echo_workflow(tmp_path)
    finished = [r for r in rows if r["event_type"] == "workflow_finished"]
    assert len(finished) == 1
    assert set(finished[0]["payload"].keys()) == {"workflow_name", "status", "wall_ms"}


def test_ac9_rollup_emit_failure_cannot_fail_the_run(monkeypatch, tmp_path):
    """AC9: monkeypatch compute_cost_rollup to raise -> engine.execute still
    returns and workflow_finished is still emitted."""
    import lib.cost_rollup as cost_rollup_mod
    import engine as engine_mod

    def _boom(*_a, **_k):
        raise RuntimeError("boom")

    monkeypatch.setattr(cost_rollup_mod, "compute_cost_rollup", _boom)
    monkeypatch.setattr(engine_mod, "compute_cost_rollup", _boom, raising=False)

    result, rows = _run_echo_workflow(tmp_path)

    assert result.status == "ok"
    finished = [r for r in rows if r["event_type"] == "workflow_finished"]
    assert len(finished) == 1
