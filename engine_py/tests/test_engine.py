"""Tests for engine_py.engine.WorkflowEngine."""

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

from contracts import (
    WorkflowContext,
    StepResult,
    StepContract,
    WorkflowDefinition,
)
from engine import WorkflowEngine
import engine


@pytest.fixture(autouse=True)
def _no_ambient_git(tmp_path, monkeypatch):
    """Engine unit tests must not read the ambient repo working tree (GH780)."""
    monkeypatch.chdir(os.path.realpath(tmp_path))   # §1j: realpath — macOS /var/folders symlink


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


def ok_step(name: str, payload):
    def _run(_ctx, _prev):
        return StepResult(status="ok", data=payload, duration_ms=0, step_name=name)
    return StepContract(name=name, execute=_run)


def err_step(name: str, *, skip_on_error: bool = False):
    def _run(_ctx, _prev):
        return StepResult(
            status="error", data=None, duration_ms=0, step_name=name,
            error="boom", error_code="E_TEST",
        )
    return StepContract(name=name, execute=_run, skip_on_error=skip_on_error)


def test_register_and_execute_single_step():
    eng = WorkflowEngine()
    eng.register("solo", WorkflowDefinition(name="solo", steps=[ok_step("s1", "hi")]))
    result, ctx = eng.execute("solo", make_ctx())
    assert result.status == "ok"
    assert result.data == "hi"
    assert result.step_name == "s1"
    assert isinstance(ctx, WorkflowContext)


def test_step_threading_prev_to_next():
    eng = WorkflowEngine()

    def echo_prev(_ctx, prev):
        return StepResult(status="ok", data=prev, duration_ms=0, step_name="echo")

    eng.register(
        "chain",
        WorkflowDefinition(
            name="chain",
            steps=[ok_step("first", "value-1"), StepContract(name="echo", execute=echo_prev)],
        ),
    )
    result, _ = eng.execute("chain", make_ctx())
    assert isinstance(result.data, StepResult)
    assert result.data.data == "value-1"


def test_error_stops_pipeline_when_not_skippable():
    eng = WorkflowEngine()
    eng.register(
        "halt",
        WorkflowDefinition(
            name="halt",
            steps=[ok_step("s1", 1), err_step("s2"), ok_step("s3", 3)],
        ),
    )
    result, _ = eng.execute("halt", make_ctx())
    assert result.status == "error"
    assert result.step_name == "s2"


def test_skip_on_error_continues_pipeline():
    eng = WorkflowEngine()
    eng.register(
        "skip",
        WorkflowDefinition(
            name="skip",
            steps=[ok_step("s1", 1), err_step("s2", skip_on_error=True), ok_step("s3", 3)],
        ),
    )
    result, _ = eng.execute("skip", make_ctx())
    assert result.status == "ok"
    assert result.step_name == "s3"
    assert result.data == 3


def test_empty_workflow_returns_ok():
    eng = WorkflowEngine()
    eng.register("empty", WorkflowDefinition(name="empty", steps=[]))
    result, _ = eng.execute("empty", make_ctx())
    assert result.status == "ok"
    assert result.step_name == "empty"


def test_register_duplicate_raises():
    eng = WorkflowEngine()
    eng.register("dup", WorkflowDefinition(name="dup", steps=[]))
    with pytest.raises(ValueError, match="already registered"):
        eng.register("dup", WorkflowDefinition(name="dup", steps=[]))


def test_execute_unknown_workflow_raises():
    eng = WorkflowEngine()
    with pytest.raises(KeyError, match="not registered"):
        eng.execute("ghost", make_ctx())


def test_duration_ms_preserved_when_step_sets_it():
    eng = WorkflowEngine()

    def with_duration(_ctx, _prev):
        return StepResult(status="ok", data=None, duration_ms=42, step_name="custom")

    eng.register(
        "preserve",
        WorkflowDefinition(name="preserve", steps=[StepContract(name="custom", execute=with_duration)]),
    )
    result, _ = eng.execute("preserve", make_ctx())
    assert result.duration_ms == 42


def test_context_immutable_through_execution():
    eng = WorkflowEngine()
    eng.register("noop", WorkflowDefinition(name="noop", steps=[ok_step("s", None)]))
    ctx_in = make_ctx()
    _, ctx_out = eng.execute("noop", ctx_in)
    assert ctx_out is ctx_in


# ─── Stage 1c: event emission ────────────────────────────────────────────────


class _FakeEventLog:
    def __init__(self):
        self.events: list[tuple[str, dict, str]] = []

    def append(self, event_type: str, payload: dict, run_id: str | None = None) -> None:
        self.events.append((event_type, payload, run_id))


def test_engine_emits_workflow_started_and_finished_when_log_attached():
    log = _FakeEventLog()
    eng = WorkflowEngine(event_log=log)
    eng.register("solo", WorkflowDefinition(name="solo", steps=[ok_step("s1", "hi")]))
    eng.execute("solo", make_ctx(), run_id="rid-1")

    kinds = [e[0] for e in log.events]
    # bd#18: run_identity (AC-E2, immediately after workflow_started) and
    # phase_artifacts (AC-E5, immediately before workflow_finished) added.
    assert kinds == [
        "workflow_started", "run_identity", "step_started", "step_finished",
        "phase_artifacts", "workflow_finished",
    ]
    # All carry the same run_id
    assert {e[2] for e in log.events} == {"rid-1"}


def test_engine_emits_step_finished_with_status_and_duration():
    log = _FakeEventLog()
    eng = WorkflowEngine(event_log=log)
    eng.register("p", WorkflowDefinition(name="p", steps=[ok_step("s", "x")]))
    eng.execute("p", make_ctx(), run_id="r")

    finished = [e for e in log.events if e[0] == "step_finished"][0]
    payload = finished[1]
    assert payload["step_name"] == "s"
    assert payload["status"] == "ok"
    assert isinstance(payload["duration_ms"], int)
    assert payload["error"] is None


def test_engine_emits_error_in_step_finished_payload():
    log = _FakeEventLog()
    eng = WorkflowEngine(event_log=log)
    eng.register("p", WorkflowDefinition(name="p", steps=[err_step("bad")]))
    eng.execute("p", make_ctx(), run_id="r")

    finished = [e for e in log.events if e[0] == "step_finished"][0]
    assert finished[1]["status"] == "error"
    assert finished[1]["error"] == "boom"
    # workflow_finished reflects the error too
    wf_finished = [e for e in log.events if e[0] == "workflow_finished"][0]
    assert wf_finished[1]["status"] == "error"


def test_workflow_finished_emits_wall_ms():
    log = _FakeEventLog()
    eng = WorkflowEngine(event_log=log)
    eng.register("solo", WorkflowDefinition(name="solo", steps=[ok_step("s1", "hi")]))
    eng.execute("solo", make_ctx(), run_id="rid-w")

    wf_finished = [e for e in log.events if e[0] == "workflow_finished"][0]
    payload = wf_finished[1]
    assert isinstance(payload["wall_ms"], int)
    assert payload["wall_ms"] >= 0


def test_engine_does_not_emit_when_no_log_attached():
    """Backward-compat: existing callers without event_log keep working."""
    eng = WorkflowEngine()  # no event_log
    eng.register("solo", WorkflowDefinition(name="solo", steps=[ok_step("s1", "hi")]))
    result, _ = eng.execute("solo", make_ctx())  # must not raise
    assert result.status == "ok"


def test_engine_generates_run_id_when_not_supplied():
    log = _FakeEventLog()
    eng = WorkflowEngine(event_log=log)
    eng.register("solo", WorkflowDefinition(name="solo", steps=[ok_step("s1", "hi")]))
    eng.execute("solo", make_ctx())

    rids = {e[2] for e in log.events}
    assert len(rids) == 1
    auto_rid = next(iter(rids))
    assert isinstance(auto_rid, str) and len(auto_rid) >= 8


def test_engine_swallows_event_log_failures():
    """Observability must not break execution: a broken log does not propagate."""

    class Broken:
        def append(self, *a, **kw):
            raise RuntimeError("disk full")

    eng = WorkflowEngine(event_log=Broken())
    eng.register("solo", WorkflowDefinition(name="solo", steps=[ok_step("s1", "hi")]))
    result, _ = eng.execute("solo", make_ctx())
    assert result.status == "ok"


def test_engine_emits_step_finished_with_skip_status_for_skipped_step():
    """If a step returns skip status, the finished event reflects it."""
    log = _FakeEventLog()
    eng = WorkflowEngine(event_log=log)

    def skip_step(_ctx, _prev):
        return StepResult(status="skip", data=None, duration_ms=0, step_name="ss")

    eng.register(
        "p",
        WorkflowDefinition(name="p", steps=[StepContract(name="ss", execute=skip_step)]),
    )
    eng.execute("p", make_ctx(), run_id="r")
    finished = [e for e in log.events if e[0] == "step_finished"][0]
    assert finished[1]["status"] == "skip"


def test_workflow_finished_emits_workflow_name(tmp_path):
    """workflow_finished payload must include workflow_name so log readers
    don't have to correlate workflow_started -> workflow_finished by run_id
    ordering. Unlocks per-phase telemetry roll-up.
    """
    from event_log import EventLog  # noqa: E402

    log = EventLog(tmp_path / "events.jsonl")
    eng = WorkflowEngine(event_log=log)
    eng.register(
        "some_phase",
        WorkflowDefinition(name="some_phase", steps=[ok_step("s1", "hi")]),
    )
    eng.execute("some_phase", make_ctx(), run_id="rid-wn")

    events = log.read_all()
    finished = [e for e in events if e["event_type"] == "workflow_finished"]
    assert len(finished) == 1, f"expected 1 workflow_finished, got {len(finished)}"
    payload = finished[0]["payload"]
    # Existing fields must remain
    assert payload["status"] == "ok"
    assert isinstance(payload["wall_ms"], int)
    # New field — currently MISSING (RED):
    assert payload.get("workflow_name") == "some_phase", (
        f"workflow_finished payload missing 'workflow_name'; got keys: "
        f"{sorted(payload.keys())}"
    )


# ─── GH780 AC8: ambient-git isolation holds (§2.2) ───────────────────────────


def test_ac8_no_ambient_git_isolation_holds():
    """Under the autouse _no_ambient_git fixture, _git_changes_vs_head() must
    return None (non-repo cwd), and existing exact-sequence tests must emit
    zero files_touched events even if the real repo tree is dirty."""
    assert engine._git_changes_vs_head() is None

    log = _FakeEventLog()
    eng = WorkflowEngine(event_log=log)
    eng.register("solo", WorkflowDefinition(name="solo", steps=[ok_step("s1", "hi")]))
    eng.execute("solo", make_ctx(), run_id="rid-ac8")

    kinds = [e[0] for e in log.events]
    assert "files_touched" not in kinds
    # bd#18: run_identity (AC-E2, immediately after workflow_started) and
    # phase_artifacts (AC-E5, immediately before workflow_finished) added.
    assert kinds == [
        "workflow_started", "run_identity", "step_started", "step_finished",
        "phase_artifacts", "workflow_finished",
    ]
