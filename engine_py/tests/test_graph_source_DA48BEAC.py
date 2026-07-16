"""RED tests for DA48BEAC — graph_source.resolve_graph_or_grep (AC5).

Covers:
  AC5-clean: check-update CLEAN → returns "graph", zero graph_stale events emitted.
  AC5-dirty: check-update DIRTY → returns "grep", exactly ONE graph_stale event emitted.

Pre-GREEN predict: BOTH tests FAIL.
  The module graph_source and fn resolve_graph_or_grep do not exist yet.
  Import is deferred to inside each test body (§1q / D1CF5FDF) so the file
  COLLECTs and FAILs at ASSERT time — never hangs collection.

Frozen literals (DF-7):
  module:    graph_source
  fn:        resolve_graph_or_grep
  returns:   "graph" (clean) / "grep" (dirty)
  event:     graph_stale   (underscore, single token)
  emitter:   _emit_safe (engine's python telemetry path)

Emit-assertion harness mirrors test_emit_resolver_966E571B.py:
  EventLog + telemetry_ctx set_current_run / clear_current_run.
  sys.path injected by conftest-import-time singleton (§1q / 81F97F3D).
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

# conftest.py already inserted ENGINE_ROOT + ENGINE_ROOT/workflows onto sys.path
# at import time (conftest-import-time singleton pattern).  No sys.path mutation here.

# ---------------------------------------------------------------------------
# Helpers / imports available post-GREEN (used inside test bodies, not module
# level, so collection never fails on missing symbols).
# ---------------------------------------------------------------------------


def _get_event_log_cls():
    """Return EventLog class; imported lazily to avoid collection-time ImportError."""
    from event_log import EventLog  # type: ignore[import]
    return EventLog


def _get_telemetry_ctx():
    """Return telemetry_ctx module; imported lazily."""
    import telemetry_ctx  # type: ignore[import]
    return telemetry_ctx


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_telemetry(tmp_path):
    """Clear telemetry run context before and after every test (mirrors 966E571B)."""
    tc = _get_telemetry_ctx()
    tc.clear_current_run()
    yield
    tc.clear_current_run()


# ---------------------------------------------------------------------------
# AC5-clean — check-update CLEAN → returns "graph", zero graph_stale events
#
# §1y Point→Host→Test-path trace:
#   Point  = the "return 'graph'" branch in resolve_graph_or_grep
#   Host   = resolve_graph_or_grep (importable fn in graph_source module)
#   Test   = fixture patches subprocess so check-update returns needs_update=false;
#             EventLog sink asserts zero graph_stale events.
# §1l real-store counter: EventLog.read_all() filtered for graph_stale.
# ---------------------------------------------------------------------------

def test_ac5_clean_path_returns_graph_DA48BEAC(tmp_path):
    """AC5-clean: when graphify check-update reports needs_update=false,
    resolve_graph_or_grep returns 'graph' AND emits zero graph_stale events.

    Pre-GREEN FAIL: ImportError — graph_source module does not exist yet.
    """
    # Deferred import per §1q / D1CF5FDF — fails here (assert time), not at collection.
    from graph_source import resolve_graph_or_grep  # type: ignore[import]

    EventLog = _get_event_log_cls()
    tc = _get_telemetry_ctx()

    log = EventLog(tmp_path / "events_clean.jsonl")
    tc.set_current_run(event_log=log, run_id="ac5-clean", step_name="t")

    # Stub check-update to report CLEAN (needs_update = false in JSON output).
    fake_clean_output = '{"needs_update": false, "path": "/tmp/fake"}'

    def _mock_check_update(cmd: list, **kwargs: Any):
        # Only intercept graphify check-update; let others through.
        if "graphify" in cmd[0] and "check-update" in cmd:
            class _Proc:
                stdout = fake_clean_output
                returncode = 0
            return _Proc()
        return subprocess.run(cmd, **kwargs)  # noqa: S603

    with patch("subprocess.run", side_effect=_mock_check_update):
        result = resolve_graph_or_grep("/tmp/fake")

    # Assert return value (DF-7 literal).
    assert result == "graph", (
        f"expected 'graph' for clean check-update, got {result!r}"
    )

    # Assert zero graph_stale events in the real telemetry sink (§1l real-store counter).
    events = log.read_all()
    stale_events = [e for e in events if e.get("event_type") == "graph_stale"]
    assert len(stale_events) == 0, (
        f"expected 0 graph_stale events on clean path, got {len(stale_events)}: {stale_events}"
    )


# ---------------------------------------------------------------------------
# AC5-dirty — check-update DIRTY → returns "grep", exactly ONE graph_stale event
#
# §1l real-store counter: exactly one graph_stale in EventLog sink.
# N>1 call guard: only one resolve_graph_or_grep call per test (§1l multi-call
# note applies to the production hot-path; single-call fixture is correct here).
# §1i: no singleton resource; mock replaces subprocess.run, no race.
# ---------------------------------------------------------------------------

def test_ac5_dirty_path_emits_graph_stale_DA48BEAC(tmp_path):
    """AC5-dirty: when graphify check-update reports needs_update=true,
    resolve_graph_or_grep returns 'grep' AND emits exactly ONE graph_stale event.

    Pre-GREEN FAIL: ImportError — graph_source module does not exist yet.
    """
    # Deferred import per §1q / D1CF5FDF.
    from graph_source import resolve_graph_or_grep  # type: ignore[import]

    EventLog = _get_event_log_cls()
    tc = _get_telemetry_ctx()

    log = EventLog(tmp_path / "events_dirty.jsonl")
    tc.set_current_run(event_log=log, run_id="ac5-dirty", step_name="t")

    # Stub check-update to report DIRTY (needs_update = true in JSON output).
    fake_dirty_output = '{"needs_update": true, "path": "/tmp/fake"}'

    def _mock_check_update(cmd: list, **kwargs: Any):
        if "graphify" in cmd[0] and "check-update" in cmd:
            class _Proc:
                stdout = fake_dirty_output
                returncode = 0
            return _Proc()
        return subprocess.run(cmd, **kwargs)  # noqa: S603

    with patch("subprocess.run", side_effect=_mock_check_update):
        result = resolve_graph_or_grep("/tmp/fake")

    # Assert return value (DF-7 literal).
    assert result == "grep", (
        f"expected 'grep' for dirty check-update, got {result!r}"
    )

    # Assert exactly ONE graph_stale event in the real telemetry sink (§1l real-store counter).
    events = log.read_all()
    stale_events = [e for e in events if e.get("event_type") == "graph_stale"]
    assert len(stale_events) == 1, (
        f"expected exactly 1 graph_stale event on dirty path, got {len(stale_events)}: {stale_events}"
    )
    # Confirm the event has the canonical event_type token (DF-7 frozen literal).
    assert stale_events[0]["event_type"] == "graph_stale"
