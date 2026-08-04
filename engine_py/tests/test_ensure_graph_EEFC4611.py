"""RED tests for EEFC4611 — graph_source helpers (AC1–AC7, AC10).

UUTs: ensure_graph, _graph_paths, _run_graphify_update (all NEW in graph_source.py).

Pre-GREEN predict: ALL tests FAIL (ImportError inside test body → clean assert-time fail).
  ensure_graph / _graph_paths / _run_graphify_update do not exist yet (verified: grep found
  zero matches in workflows/graph_source.py).

§1q: conftest-import-time singleton provides sys.path. NO sys.path mutation here.
D1CF5FDF: all imports of not-yet-existing symbols deferred to inside test bodies so the
  file COLLECTS cleanly and tests FAIL at assert time, not collection time.
§1l: real telemetry sink (EventLog + telemetry_ctx.set_current_run) — not a fake log list.
§1i: no singleton resources; subprocess.run is patched deterministically, no timing races.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest

# conftest.py inserts ENGINE_ROOT + ENGINE_ROOT/workflows onto sys.path at import time.
# No sys.path.insert here (§1q / 81F97F3D gate).


# ---------------------------------------------------------------------------
# Lazy import helpers — deferred to body to avoid collection-time ImportError
# ---------------------------------------------------------------------------

def _import_ensure_graph():
    from bytedigger_engine.workflows.graph_source import ensure_graph  # type: ignore[import]
    return ensure_graph


def _import_graph_paths():
    from bytedigger_engine.workflows.graph_source import _graph_paths  # type: ignore[import]
    return _graph_paths


def _import_run_graphify_update():
    from bytedigger_engine.workflows.graph_source import _run_graphify_update  # type: ignore[import]
    return _run_graphify_update


def _get_event_log_cls():
    from bytedigger_engine.event_log import EventLog  # type: ignore[import]
    return EventLog


def _get_telemetry_ctx():
    from bytedigger_engine import telemetry_ctx  # type: ignore[import]
    return telemetry_ctx


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_telemetry(tmp_path):
    """Clear telemetry run context before and after each test (mirrors DA48BEAC pattern)."""
    tc = _get_telemetry_ctx()
    tc.clear_current_run()
    yield
    tc.clear_current_run()


@pytest.fixture()
def _run_ctx(tmp_path):
    """Set up a real EventLog + telemetry_ctx run for tests that need telemetry capture."""
    EventLog = _get_event_log_cls()
    tc = _get_telemetry_ctx()
    log = EventLog(tmp_path / "events.jsonl")
    tc.set_current_run(event_log=log, run_id="eefc4611-test", step_name="test")
    yield log
    tc.clear_current_run()


# ---------------------------------------------------------------------------
# AC1: absent graph → calls subprocess.run with exact graphify update args once
# §1y Point→Host→Test: ensure_graph → subprocess.run patch → call_args assert
# §1l: real call-count on patched subprocess (not a fake counter)
# ---------------------------------------------------------------------------

def test_ac1_absent_graph_calls_graphify_update_EEFC4611(tmp_path, _run_ctx):
    """AC1: ensure_graph(root) with graph.json absent calls subprocess.run with
    ['graphify', 'update', '<root>/SYSTEM/cli/build', '--no-cluster'] exactly once.

    Pre-GREEN FAIL: ImportError — ensure_graph does not exist in graph_source yet.
    """
    ensure_graph = _import_ensure_graph()

    repo = str(tmp_path)
    expected_scan_root = str(tmp_path / "SYSTEM" / "cli" / "build")

    mock_proc = MagicMock()
    mock_proc.returncode = 0

    with patch("subprocess.run", return_value=mock_proc) as mock_run, \
         patch("pathlib.Path.exists", return_value=False):
        ensure_graph(repo)

    # Assert called exactly once with the canonical graphify update args (AC1).
    assert mock_run.call_count == 1, (
        f"AC1 FAIL: expected subprocess.run called once, got {mock_run.call_count}"
    )
    actual_args = mock_run.call_args[0][0]  # first positional arg = cmd list
    assert actual_args == ["graphify", "update", expected_scan_root, "--no-cluster"], (
        f"AC1 FAIL: subprocess.run call args mismatch.\n"
        f"Expected: ['graphify', 'update', {expected_scan_root!r}, '--no-cluster']\n"
        f"Got:      {actual_args!r}"
    )


# ---------------------------------------------------------------------------
# AC2: absent graph → emits exactly one graph_stale event with correct path
# §1y Point→Host→Test: _emit_safe in ensure_graph → real EventLog sink
# §1l: real EventLog.read_all() count assertion
# ---------------------------------------------------------------------------

def test_ac2_absent_graph_emits_one_graph_stale_EEFC4611(tmp_path, _run_ctx):
    """AC2: ensure_graph absent path emits exactly ONE graph_stale event
    whose payload 'path' == <root>/SYSTEM/cli/build.

    Pre-GREEN FAIL: ImportError — ensure_graph does not exist yet.
    """
    ensure_graph = _import_ensure_graph()
    log = _run_ctx  # the EventLog instance from fixture

    repo = str(tmp_path)
    expected_scan_root = str(tmp_path / "SYSTEM" / "cli" / "build")

    mock_proc = MagicMock()
    mock_proc.returncode = 0

    with patch("subprocess.run", return_value=mock_proc), \
         patch("pathlib.Path.exists", return_value=False):
        ensure_graph(repo)

    events = log.read_all()
    stale_events = [e for e in events if e.get("event_type") == "graph_stale"]

    assert len(stale_events) == 1, (
        f"AC2 FAIL: expected exactly 1 graph_stale event, got {len(stale_events)}.\n"
        f"All events: {events}"
    )
    payload = stale_events[0].get("payload", stale_events[0])
    # payload may be nested under "payload" key or top-level depending on EventLog schema
    path_val = payload.get("path") if isinstance(payload, dict) else None
    # Also check top-level event dict for "path" field
    if path_val is None:
        path_val = stale_events[0].get("path")
    assert path_val == expected_scan_root, (
        f"AC2 FAIL: graph_stale event path mismatch.\n"
        f"Expected: {expected_scan_root!r}\n"
        f"Got:      {path_val!r}\n"
        f"Full event: {stale_events[0]}"
    )


# ---------------------------------------------------------------------------
# AC3: absent graph, subprocess succeeds, graph.json exists after → returns "graph"
# §1y Point→Host→Test: ensure_graph return value assert
# ---------------------------------------------------------------------------

def test_ac3_returns_graph_when_update_succeeds_EEFC4611(tmp_path, _run_ctx):
    """AC3: ensure_graph returns 'graph' when subprocess.run rc=0 and graph.json
    exists after update (Path.exists side_effect=[False, True]).

    Pre-GREEN FAIL: ImportError — ensure_graph does not exist yet.
    """
    ensure_graph = _import_ensure_graph()

    repo = str(tmp_path)
    mock_proc = MagicMock()
    mock_proc.returncode = 0

    # First call: pre-check (absent), second call: post-update check (present)
    with patch("subprocess.run", return_value=mock_proc), \
         patch("pathlib.Path.exists", side_effect=[False, True]):
        result = ensure_graph(repo)

    assert result == "graph", (
        f"AC3 FAIL: expected 'graph' when graph.json exists post-update, got {result!r}"
    )


# ---------------------------------------------------------------------------
# AC4: graphify binary missing (FileNotFoundError) → returns "grep", emits graph_stale
# §1y Point→Host→Test: FileNotFoundError path in ensure_graph
# §1l: real EventLog count
# ---------------------------------------------------------------------------

def test_ac4_binary_missing_returns_grep_emits_stale_EEFC4611(tmp_path, _run_ctx):
    """AC4: ensure_graph returns 'grep' and emits ONE graph_stale when subprocess.run
    raises FileNotFoundError (graphify binary absent).

    Pre-GREEN FAIL: ImportError — ensure_graph does not exist yet.
    """
    ensure_graph = _import_ensure_graph()
    log = _run_ctx

    repo = str(tmp_path)

    with patch("subprocess.run", side_effect=FileNotFoundError("graphify not found")), \
         patch("pathlib.Path.exists", return_value=False):
        result = ensure_graph(repo)

    assert result == "grep", (
        f"AC4 FAIL: expected 'grep' when graphify binary missing, got {result!r}"
    )

    events = log.read_all()
    stale_events = [e for e in events if e.get("event_type") == "graph_stale"]
    assert len(stale_events) == 1, (
        f"AC4 FAIL: expected 1 graph_stale event on binary-missing path, "
        f"got {len(stale_events)}.\nAll events: {events}"
    )


# ---------------------------------------------------------------------------
# AC5: graph.json present → returns "graph", subprocess NOT called, zero graph_stale
# §1y Point→Host→Test: present-graph early-return branch in ensure_graph
# §1l: subprocess.run.assert_not_called() + EventLog count=0
# ---------------------------------------------------------------------------

def test_ac5_present_graph_returns_graph_no_subprocess_no_event_EEFC4611(tmp_path, _run_ctx):
    """AC5: ensure_graph with graph.json present returns 'graph', does NOT call
    subprocess.run, emits ZERO graph_stale events.

    Pre-GREEN FAIL: ImportError — ensure_graph does not exist yet.
    """
    ensure_graph = _import_ensure_graph()
    log = _run_ctx

    repo = str(tmp_path)

    with patch("subprocess.run") as mock_run, \
         patch("pathlib.Path.exists", return_value=True):
        result = ensure_graph(repo)

    assert result == "graph", (
        f"AC5 FAIL: expected 'graph' when graph.json present, got {result!r}"
    )
    mock_run.assert_not_called()

    events = log.read_all()
    stale_events = [e for e in events if e.get("event_type") == "graph_stale"]
    assert len(stale_events) == 0, (
        f"AC5 FAIL: expected 0 graph_stale events on present-graph path, "
        f"got {len(stale_events)}.\nAll events: {events}"
    )


# ---------------------------------------------------------------------------
# AC6: ensure_graph sets os.environ["GRAPHIFY_OUT"] to absolute path (both variants)
# §1y Point→Host→Test: os.environ mutation in ensure_graph
# §1i: save/restore os.environ around each test variant
# ---------------------------------------------------------------------------

def test_ac6_sets_graphify_out_env_absent_EEFC4611(tmp_path, _run_ctx):
    """AC6 (absent variant): ensure_graph sets os.environ['GRAPHIFY_OUT'] to
    <root>/SYSTEM/cli/build/graphify-out (absolute).

    Pre-GREEN FAIL: ImportError — ensure_graph does not exist yet.
    """
    ensure_graph = _import_ensure_graph()

    repo = str(tmp_path)
    expected_graphify_out = str(tmp_path / "SYSTEM" / "cli" / "build" / "graphify-out")

    saved = os.environ.get("GRAPHIFY_OUT")
    try:
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        with patch("subprocess.run", return_value=mock_proc), \
             patch("pathlib.Path.exists", return_value=False):
            ensure_graph(repo)

        assert "GRAPHIFY_OUT" in os.environ, (
            "AC6 FAIL: os.environ['GRAPHIFY_OUT'] was not set by ensure_graph (absent path)"
        )
        assert os.environ["GRAPHIFY_OUT"] == expected_graphify_out, (
            f"AC6 FAIL: GRAPHIFY_OUT mismatch (absent path).\n"
            f"Expected: {expected_graphify_out!r}\n"
            f"Got:      {os.environ['GRAPHIFY_OUT']!r}"
        )
    finally:
        if saved is None:
            os.environ.pop("GRAPHIFY_OUT", None)
        else:
            os.environ["GRAPHIFY_OUT"] = saved


def test_ac6_sets_graphify_out_env_present_EEFC4611(tmp_path, _run_ctx):
    """AC6 (present variant): ensure_graph sets os.environ['GRAPHIFY_OUT'] to
    <root>/SYSTEM/cli/build/graphify-out even when graph.json is already present.

    Pre-GREEN FAIL: ImportError — ensure_graph does not exist yet.
    """
    ensure_graph = _import_ensure_graph()

    repo = str(tmp_path)
    expected_graphify_out = str(tmp_path / "SYSTEM" / "cli" / "build" / "graphify-out")

    saved = os.environ.get("GRAPHIFY_OUT")
    try:
        with patch("subprocess.run") as mock_run, \
             patch("pathlib.Path.exists", return_value=True):
            ensure_graph(repo)

        assert "GRAPHIFY_OUT" in os.environ, (
            "AC6 FAIL: os.environ['GRAPHIFY_OUT'] was not set by ensure_graph (present path)"
        )
        assert os.environ["GRAPHIFY_OUT"] == expected_graphify_out, (
            f"AC6 FAIL: GRAPHIFY_OUT mismatch (present path).\n"
            f"Expected: {expected_graphify_out!r}\n"
            f"Got:      {os.environ['GRAPHIFY_OUT']!r}"
        )
    finally:
        if saved is None:
            os.environ.pop("GRAPHIFY_OUT", None)
        else:
            os.environ["GRAPHIFY_OUT"] = saved


# ---------------------------------------------------------------------------
# AC7: _graph_paths(root) returns correct (scan_root, graph_json) Paths
# §1y Point→Host→Test: direct call, return-value assert
# (§1l: anchor on both Path values — AND-mode per-token per §1l)
# ---------------------------------------------------------------------------

def test_ac7_graph_paths_returns_correct_tuple_EEFC4611(tmp_path):
    """AC7: _graph_paths(root) returns
    (Path(<root>/SYSTEM/cli/build), Path(<root>/SYSTEM/cli/build/graphify-out/graph.json)).

    Pre-GREEN FAIL: ImportError — _graph_paths does not exist yet.
    """
    _graph_paths = _import_graph_paths()

    repo = str(tmp_path)
    scan_root, graph_json = _graph_paths(repo)

    expected_scan_root = Path(tmp_path) / "SYSTEM" / "cli" / "build"
    expected_graph_json = expected_scan_root / "graphify-out" / "graph.json"

    assert scan_root == expected_scan_root, (
        f"AC7 FAIL: scan_root mismatch.\n"
        f"Expected: {expected_scan_root}\n"
        f"Got:      {scan_root}"
    )
    assert graph_json == expected_graph_json, (
        f"AC7 FAIL: graph_json mismatch.\n"
        f"Expected: {expected_graph_json}\n"
        f"Got:      {graph_json}"
    )


# ---------------------------------------------------------------------------
# AC10: _run_graphify_update returns False (not raise) on subprocess.TimeoutExpired
# §1y Point→Host→Test: TimeoutExpired catch in _run_graphify_update
# §1l: return-value assertion (False, not exception)
# ---------------------------------------------------------------------------

def test_ac10_run_graphify_update_returns_false_on_timeout_EEFC4611():
    """AC10: _run_graphify_update('/x') returns False (does not raise) when
    subprocess.run raises subprocess.TimeoutExpired.

    Pre-GREEN FAIL: ImportError — _run_graphify_update does not exist yet.
    """
    _run_graphify_update = _import_run_graphify_update()

    timeout_exc = subprocess.TimeoutExpired(cmd=["graphify", "update", "/x"], timeout=120)
    with patch("subprocess.run", side_effect=timeout_exc):
        result = _run_graphify_update("/x")

    assert result is False, (
        f"AC10 FAIL: expected False on TimeoutExpired, got {result!r}"
    )
