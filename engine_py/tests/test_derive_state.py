"""Tests for engine_py.derive_state.replay()."""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

from derive_state import replay, query_run_events  # noqa: E402


def evt(event_type: str, payload: dict, run_id: str = "r1", ts: str = "2026-04-25T08:00:00.000Z") -> dict:
    return {"ts": ts, "run_id": run_id, "event_type": event_type, "payload": payload}


def test_empty_log_returns_empty_state():
    state = replay([])
    assert state == {"runs": {}, "last_event_ts": None, "unknown_events": 0}


def test_workflow_started_records_run():
    events = [evt("workflow_started", {"workflow_name": "phase_6_smoke"})]
    state = replay(events)
    assert "r1" in state["runs"]
    run = state["runs"]["r1"]
    assert run["workflow_name"] == "phase_6_smoke"
    assert run["status"] == "running"
    assert run["started_at"].startswith("2026-04-25T08:00:00")
    assert run["steps"] == []


def test_step_started_appends_running_step():
    events = [
        evt("workflow_started", {"workflow_name": "wf"}),
        evt("step_started", {"step_name": "s1"}, ts="2026-04-25T08:00:01.000Z"),
    ]
    state = replay(events)
    steps = state["runs"]["r1"]["steps"]
    assert len(steps) == 1
    assert steps[0]["step_name"] == "s1"
    assert steps[0]["status"] == "running"


def test_step_finished_closes_open_step_and_records_duration():
    events = [
        evt("workflow_started", {"workflow_name": "wf"}),
        evt("step_started", {"step_name": "s1"}),
        evt(
            "step_finished",
            {"step_name": "s1", "status": "ok", "duration_ms": 42},
            ts="2026-04-25T08:00:05.000Z",
        ),
    ]
    state = replay(events)
    step = state["runs"]["r1"]["steps"][0]
    assert step["status"] == "ok"
    assert step["duration_ms"] == 42
    assert step["finished_at"].startswith("2026-04-25T08:00:05")


def test_step_finished_with_error_records_error_field():
    events = [
        evt("workflow_started", {"workflow_name": "wf"}),
        evt("step_started", {"step_name": "s1"}),
        evt(
            "step_finished",
            {"step_name": "s1", "status": "error", "duration_ms": 7, "error": "boom"},
        ),
    ]
    state = replay(events)
    step = state["runs"]["r1"]["steps"][0]
    assert step["status"] == "error"
    assert step["error"] == "boom"


def test_step_finished_without_started_records_retroactively():
    events = [
        evt("step_finished", {"step_name": "ghost", "status": "ok", "duration_ms": 1}),
    ]
    state = replay(events)
    step = state["runs"]["r1"]["steps"][0]
    assert step["step_name"] == "ghost"
    assert step["status"] == "ok"


def test_workflow_finished_sets_terminal_status():
    events = [
        evt("workflow_started", {"workflow_name": "wf"}),
        evt("step_started", {"step_name": "s"}),
        evt("step_finished", {"step_name": "s", "status": "error", "duration_ms": 1, "error": "x"}),
        evt("workflow_finished", {"status": "error"}, ts="2026-04-25T08:00:10.000Z"),
    ]
    state = replay(events)
    run = state["runs"]["r1"]
    assert run["status"] == "error"
    assert run["finished_at"].startswith("2026-04-25T08:00:10")


def test_multiple_runs_isolated_by_run_id():
    events = [
        evt("workflow_started", {"workflow_name": "a"}, run_id="A"),
        evt("workflow_started", {"workflow_name": "b"}, run_id="B"),
        evt("step_started", {"step_name": "x"}, run_id="A"),
        evt("step_started", {"step_name": "y"}, run_id="B"),
    ]
    state = replay(events)
    assert set(state["runs"]) == {"A", "B"}
    assert state["runs"]["A"]["workflow_name"] == "a"
    assert state["runs"]["B"]["workflow_name"] == "b"
    assert [s["step_name"] for s in state["runs"]["A"]["steps"]] == ["x"]
    assert [s["step_name"] for s in state["runs"]["B"]["steps"]] == ["y"]


def test_repeated_step_name_finishes_most_recent_open_one():
    """If same step_name runs twice (e.g. retry), each finished closes its own started."""
    events = [
        evt("step_started", {"step_name": "s"}),
        evt("step_finished", {"step_name": "s", "status": "error", "duration_ms": 1, "error": "first"}),
        evt("step_started", {"step_name": "s"}),
        evt("step_finished", {"step_name": "s", "status": "ok", "duration_ms": 2}),
    ]
    state = replay(events)
    steps = state["runs"]["r1"]["steps"]
    assert len(steps) == 2
    assert steps[0]["status"] == "error"
    assert steps[0]["error"] == "first"
    assert steps[1]["status"] == "ok"
    assert "error" not in steps[1]


def test_unknown_event_types_counted_but_not_stateful():
    events = [
        evt("workflow_started", {"workflow_name": "wf"}),
        evt("custom_user_event", {"foo": "bar"}),
        evt("another_unknown", {}),
    ]
    state = replay(events)
    assert state["unknown_events"] == 2
    assert state["runs"]["r1"]["status"] == "running"


def test_last_event_ts_tracks_most_recent_timestamp():
    events = [
        evt("workflow_started", {"workflow_name": "wf"}, ts="2026-04-25T08:00:00.000Z"),
        evt("step_started", {"step_name": "s"}, ts="2026-04-25T08:00:01.000Z"),
        evt("workflow_finished", {"status": "ok"}, ts="2026-04-25T08:00:02.000Z"),
    ]
    state = replay(events)
    assert state["last_event_ts"] == "2026-04-25T08:00:02.000Z"


# ─── query_run_events tests (9AB90CA0) ─────


def test_query_run_events_filters_by_run_id():
    events = [
        evt("step_started", {"step_name": "a"}, run_id="X"),
        evt("step_started", {"step_name": "b"}, run_id="Y"),
        evt("step_finished", {"step_name": "a", "status": "ok", "duration_ms": 1}, run_id="X"),
    ]
    out = query_run_events(events, run_id="X")
    assert len(out) == 2
    assert all(e["run_id"] == "X" for e in out)


def test_query_run_events_filters_by_event_type():
    events = [
        evt("workflow_started", {"workflow_name": "wf"}),
        evt("step_started", {"step_name": "a"}),
        evt("step_finished", {"step_name": "a", "status": "ok", "duration_ms": 1}),
        evt("step_finished", {"step_name": "b", "status": "ok", "duration_ms": 2}),
    ]
    out = query_run_events(events, event_type="step_finished")
    assert len(out) == 2
    assert all(e["event_type"] == "step_finished" for e in out)


def test_query_run_events_filters_by_phase_via_workflow_name():
    events = [
        evt("workflow_started", {"workflow_name": "phase_5_implement"}, run_id="A"),
        evt("workflow_started", {"workflow_name": "phase_6_review"}, run_id="B"),
    ]
    out = query_run_events(events, phase="phase_5_implement")
    assert len(out) == 1
    assert out[0]["run_id"] == "A"


def test_query_run_events_filters_by_phase_via_payload_phase():
    events = [
        evt("workflow_started", {"workflow_name": "phase_5_implement"}, run_id="A"),
        evt("subprocess_exited", {"phase": "phase_5_implement", "model": "sonnet"}, run_id="A"),
        evt("subprocess_exited", {"phase": "phase_6_review", "model": "haiku"}, run_id="B"),
    ]
    out = query_run_events(events, phase="phase_5_implement")
    assert len(out) == 2
    types = {e["event_type"] for e in out}
    assert types == {"workflow_started", "subprocess_exited"}


def test_query_run_events_filters_by_step_name():
    events = [
        evt("step_started", {"step_name": "invoke_red_llm"}),
        evt("step_finished", {"step_name": "invoke_red_llm", "status": "ok", "duration_ms": 1}),
        evt("step_finished", {"step_name": "invoke_fix_llm", "status": "ok", "duration_ms": 2}),
    ]
    out = query_run_events(events, step="invoke_red_llm", event_type="step_finished")
    assert len(out) == 1
    assert out[0]["payload"]["step_name"] == "invoke_red_llm"


def test_query_run_events_combined_filters_AND():
    events = [
        evt("step_finished", {"step_name": "invoke_red_llm", "status": "ok", "duration_ms": 1, "phase": "phase_5_implement"}, run_id="A"),
        evt("step_finished", {"step_name": "invoke_red_llm", "status": "ok", "duration_ms": 1, "phase": "phase_6_review"}, run_id="A"),
        evt("step_started", {"step_name": "invoke_red_llm", "phase": "phase_5_implement"}, run_id="A"),
        evt("step_finished", {"step_name": "invoke_fix_llm", "status": "ok", "duration_ms": 1, "phase": "phase_5_implement"}, run_id="A"),
    ]
    out = query_run_events(
        events,
        phase="phase_5_implement",
        step="invoke_red_llm",
        event_type="step_finished",
    )
    assert len(out) == 1
    assert out[0]["payload"]["step_name"] == "invoke_red_llm"
    assert out[0]["payload"]["phase"] == "phase_5_implement"
    assert out[0]["event_type"] == "step_finished"


def test_query_run_events_preserves_log_order():
    events = [
        evt("step_started", {"step_name": "a"}, ts="2026-04-25T08:00:00.000Z"),
        evt("step_started", {"step_name": "b"}, ts="2026-04-25T08:00:01.000Z"),
        evt("step_started", {"step_name": "c"}, ts="2026-04-25T08:00:02.000Z"),
    ]
    out = query_run_events(events, event_type="step_started")
    assert [e["payload"]["step_name"] for e in out] == ["a", "b", "c"]


def test_query_run_events_no_filters_returns_all():
    events = [
        evt("workflow_started", {"workflow_name": "wf"}),
        evt("step_started", {"step_name": "a"}),
    ]
    out = query_run_events(events)
    assert isinstance(out, list)
    assert out == events


def test_query_run_events_loads_from_jsonl_path(tmp_path):
    path = tmp_path / "events.jsonl"
    e1 = evt("workflow_started", {"workflow_name": "phase_5_implement"})
    e2 = evt("step_started", {"step_name": "a"})
    # Include one blank line to confirm blank-line skipping.
    path.write_text(json.dumps(e1) + "\n\n" + json.dumps(e2) + "\n")
    out = query_run_events(str(path))
    assert len(out) == 2
    assert out[0]["event_type"] == "workflow_started"
    assert out[1]["event_type"] == "step_started"


def test_query_run_events_returns_empty_for_missing_path(tmp_path):
    missing = tmp_path / "does_not_exist.jsonl"
    out = query_run_events(str(missing))
    assert out == []


def test_query_run_events_default_source_when_none(tmp_path, monkeypatch):
    path = tmp_path / "default.jsonl"
    e1 = evt("workflow_started", {"workflow_name": "phase_5_implement"})
    path.write_text(json.dumps(e1) + "\n")
    import derive_state as ds

    monkeypatch.setattr(ds, "default_log_path", lambda: path, raising=False)
    out = query_run_events(None)
    assert len(out) == 1
    assert out[0]["event_type"] == "workflow_started"
