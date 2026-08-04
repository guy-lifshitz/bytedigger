"""RED tests for 026599E4 — E1 event-sink seam (EventSink Protocol + factory).

Covers AC1–AC7. ALL tests FAIL pre-GREEN because event_sink.py does not exist.

Collection safety (§1q / D1CF5FDF): `event_sink` is NOT imported at module
level. Every `from bytedigger_engine.event_sink import ...` is deferred to inside the test
function body (or guarded by pytest.importorskip). The file collects cleanly;
each test fails at assert time, not collection time.

Pre-GREEN predict:
  AC1: ImportError inside body → pytest raises, test FAILS
  AC2: ImportError inside body → FAIL
  AC3: ImportError inside body → FAIL
  AC4: ImportError inside body → FAIL
  AC5: ImportError inside body → FAIL
  AC6: FAIL — WorkflowEngine._emit does NOT call rec.calls (event_sink absent)
       (WorkflowEngine itself exists; test body imports engine directly)
  AC7: FAIL — run.make_engine still uses EventLog directly, not get_event_sink
       (the assertion type(eng._event_log).__name__ == "EventLog" still holds
        post-GREEN, but the seam routing assertion is checked via run.py import)
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

# sys.path is already set by conftest.py (engine_py root + workflows/).
# Do NOT add sys.path manipulation here (§1q / 81F97F3D gate).


# ── autouse teardown fixture (§1i — module-global registry reset) ─────────────

@pytest.fixture(autouse=True)
def _reset_factory_after_test():
    """Reset the event_sink factory after every test (§1i singleton-resource).

    Guards against test-order pollution of the module-global _DEFAULT_FACTORY.
    The try/except around the import ensures teardown never errors during the
    RED phase when event_sink.py does not exist yet.
    """
    yield
    try:
        from bytedigger_engine.event_sink import reset_default_event_sink_factory  # type: ignore[import]
        reset_default_event_sink_factory()
    except Exception:
        pass


# ── AC1 — EventSink is a @runtime_checkable Protocol ─────────────────────────

def test_ac1_eventsink_protocol_isinstance_check(tmp_path):
    """AC1: EventSink is a @runtime_checkable Protocol.

    EventLog(path) satisfies it (isinstance == True).
    A plain object() with no .append is NOT an instance.

    Pre-GREEN: FAIL — ImportError: event_sink module does not exist.
    """
    from bytedigger_engine.event_sink import EventSink  # type: ignore[import]
    from bytedigger_engine.event_log import EventLog

    log_path = tmp_path / "e.jsonl"
    log = EventLog(log_path)

    # EventLog has .append(event_type, payload, run_id) → satisfies Protocol
    assert isinstance(log, EventSink) is True, (
        "EventLog must satisfy the EventSink Protocol (isinstance check)"
    )

    # A bare object with no .append must NOT match
    bare = object()
    assert isinstance(bare, EventSink) is False, (
        "object() with no .append must NOT satisfy EventSink"
    )


# ── AC2 — default_event_sink() / get_event_sink() return EventLog ─────────────

def test_ac2_default_factories_return_eventlog_at_default_path(tmp_path):
    """AC2: get_event_sink() and default_event_sink() (no arg) both return an
    EventLog whose .path equals default_log_path().

    Pre-GREEN: FAIL — ImportError: event_sink module does not exist.
    """
    from bytedigger_engine.event_sink import get_event_sink, default_event_sink  # type: ignore[import]
    from bytedigger_engine.event_log import EventLog, default_log_path

    sink_get = get_event_sink()
    assert type(sink_get).__name__ == "EventLog", (
        f"get_event_sink() should return EventLog, got {type(sink_get).__name__!r}"
    )
    assert sink_get.path == default_log_path(), (
        f"get_event_sink().path should equal default_log_path(), "
        f"got {sink_get.path!r} vs {default_log_path()!r}"
    )

    sink_default = default_event_sink()
    assert type(sink_default).__name__ == "EventLog", (
        f"default_event_sink() should return EventLog, got {type(sink_default).__name__!r}"
    )
    assert sink_default.path == default_log_path(), (
        f"default_event_sink().path should equal default_log_path(), "
        f"got {sink_default.path!r} vs {default_log_path()!r}"
    )


# ── AC3 — get_event_sink(path).append writes canonical JSONL shape ────────────

def test_ac3_get_event_sink_writes_canonical_jsonl(tmp_path):
    """AC3: get_event_sink(tmp) returns a sink whose .append writes the same
    JSONL shape as EventLog today: exactly one line, keys {ts, run_id,
    event_type, payload}, with correct field values.

    Pre-GREEN: FAIL — ImportError: event_sink module does not exist.
    """
    from bytedigger_engine.event_sink import get_event_sink  # type: ignore[import]

    event_log_path = tmp_path / "e.jsonl"
    sink = get_event_sink(str(event_log_path))

    result = sink.append("e", {"k": 1}, "rid")

    # File must exist and contain exactly one non-empty line
    assert event_log_path.exists(), "sink.append must create the JSONL file"
    raw_lines = [ln for ln in event_log_path.read_text().split("\n") if ln.strip()]
    assert len(raw_lines) == 1, (
        f"expected exactly 1 JSONL line after one append, got {len(raw_lines)}"
    )

    row = json.loads(raw_lines[0])

    # Exact key set: {ts, run_id, event_type, payload}
    assert set(row.keys()) == {"ts", "run_id", "event_type", "payload"}, (
        f"JSONL keys mismatch: expected {{'ts','run_id','event_type','payload'}}, "
        f"got {set(row.keys())}"
    )
    assert row["event_type"] == "e", f"event_type mismatch: {row['event_type']!r}"
    assert row["payload"] == {"k": 1}, f"payload mismatch: {row['payload']!r}"
    assert row["run_id"] == "rid", f"run_id mismatch: {row['run_id']!r}"
    assert row["ts"], "ts must be non-empty"

    # .append return value must be a dict (same contract as EventLog)
    assert isinstance(result, dict), (
        f"sink.append must return a dict, got {type(result)!r}"
    )


# ── AC4 — set_default_event_sink_factory injects a custom sink ────────────────

def test_ac4_set_default_event_sink_factory_injects_custom_sink(tmp_path):
    """AC4: set_default_event_sink_factory(factory) makes get_event_sink()
    return the injected sink; subsequent .append calls are routed to it.

    Pre-GREEN: FAIL — ImportError: event_sink module does not exist.
    """
    from bytedigger_engine.event_sink import get_event_sink, set_default_event_sink_factory  # type: ignore[import]

    class RecordingSink:
        def __init__(self):
            self.calls: list[tuple] = []

        def append(self, event_type: str, payload: dict, run_id: str | None = None) -> dict:
            self.calls.append((event_type, payload, run_id))
            return {"event_type": event_type, "payload": payload, "run_id": run_id}

    rec = RecordingSink()
    set_default_event_sink_factory(lambda path=None: rec)

    # get_event_sink() must return the injected instance
    resolved = get_event_sink()
    assert resolved is rec, (
        "get_event_sink() must return the recording sink after factory override"
    )

    # Events must route to the recording sink
    resolved.append("e", {"a": 2}, "rid")
    assert ("e", {"a": 2}, "rid") in rec.calls, (
        f"expected ('e', {{'a': 2}}, 'rid') in rec.calls, got {rec.calls!r}"
    )


# ── AC5 — reset_default_event_sink_factory restores EventLog default ──────────

def test_ac5_reset_restores_eventlog_default(tmp_path):
    """AC5: after registering a custom factory then calling
    reset_default_event_sink_factory(), get_event_sink() returns EventLog again.

    Pre-GREEN: FAIL — ImportError: event_sink module does not exist.
    """
    from bytedigger_engine.event_sink import (  # type: ignore[import]
        get_event_sink,
        set_default_event_sink_factory,
        reset_default_event_sink_factory,
    )

    class RecordingSink:
        def append(self, event_type: str, payload: dict, run_id: str | None = None) -> dict:
            return {}

    rec = RecordingSink()
    set_default_event_sink_factory(lambda path=None: rec)

    # Confirm override is in effect
    assert get_event_sink() is rec

    # Reset restores the original factory
    reset_default_event_sink_factory()
    restored = get_event_sink()
    assert type(restored).__name__ == "EventLog", (
        f"after reset, get_event_sink() should return EventLog, "
        f"got {type(restored).__name__!r}"
    )


# ── AC6 — WorkflowEngine._emit routes through injected duck-typed sink ─────────

def test_ac6_workflow_engine_emits_through_injected_sink(tmp_path):
    """AC6: WorkflowEngine(event_log=recording_fake) forwards _emit calls to the
    fake sink. Duck-typing preserved; the type-annotation change (EventSink | None)
    does not break runtime routing.

    Pre-GREEN: FAIL — _emit exists but the spec's annotation edit has not been
    applied; however the functional routing DOES work pre-GREEN (engine already
    duck-types). Actual fail reason: the test imports event_sink indirectly via
    the AC contract check below.

    NOTE: AC6 tests functional routing through the *existing* duck-typing seam.
    The _emit machinery already exists; this AC confirms behavior is preserved
    post-annotation change. To make this test RED pre-GREEN we assert the
    EventSink Protocol isinstance check on RecordingSink succeeds (which requires
    event_sink.py to exist).
    """
    from bytedigger_engine.event_sink import EventSink  # type: ignore[import]
    from bytedigger_engine.engine import WorkflowEngine

    class RecordingSink:
        def __init__(self):
            self.calls: list[tuple] = []

        def append(self, event_type: str, payload: dict, run_id: str | None = None) -> dict:
            self.calls.append((event_type, payload, run_id))
            return {"event_type": event_type, "payload": payload, "run_id": run_id}

    rec = RecordingSink()

    # RecordingSink has .append matching the Protocol → must satisfy EventSink
    assert isinstance(rec, EventSink) is True, (
        "RecordingSink with correct .append must satisfy EventSink Protocol"
    )

    eng = WorkflowEngine(event_log=rec)
    eng._emit("x", {}, "rid")

    assert ("x", {}, "rid") in rec.calls, (
        f"WorkflowEngine._emit must forward to injected sink; "
        f"expected ('x', {{}}, 'rid') in rec.calls, got {rec.calls!r}"
    )


# ── AC7 — run.make_engine explicit-path branch routes through seam ─────────────

def test_ac7_make_engine_explicit_path_routes_through_seam(tmp_path):
    """AC7: run.make_engine(str(path)) returns a WorkflowEngine whose _event_log
    is an EventLog at that explicit path.

    Tests ONLY the explicit-path branch. The no-path (None) branch keeps
    _event_log=None and is NOT asserted here (AC7 scope per spec).

    Pre-GREEN: FAIL — run.make_engine still uses EventLog(path) directly without
    routing through get_event_sink. Post-GREEN, make_engine calls
    get_event_sink(path) which returns EventLog(path) by default.
    The assertion on type name and .path value will PASS only after GREEN wires
    the seam; currently make_engine constructs EventLog directly, which means
    the assertion actually passes IF EventLog is returned either way.

    To make this genuinely RED pre-GREEN: after GREEN, run.py calls get_event_sink
    (the single resolver). We verify the seam is wired by checking that overriding
    the factory changes what make_engine returns. Without the seam, a factory
    override would be ignored.
    """
    from bytedigger_engine.event_sink import (  # type: ignore[import]
        get_event_sink,
        set_default_event_sink_factory,
        reset_default_event_sink_factory,
    )
    from bytedigger_engine.run import make_engine  # type: ignore[import]

    # Part A: explicit path → EventLog at that path (basic regression guard)
    explicit_path = str(tmp_path / "e.jsonl")
    eng = make_engine(explicit_path)
    assert type(eng._event_log).__name__ == "EventLog", (
        f"make_engine(path) must produce EventLog, got {type(eng._event_log).__name__!r}"
    )
    assert eng._event_log.path == Path(explicit_path), (
        f"_event_log.path must equal {explicit_path!r}, got {eng._event_log.path!r}"
    )

    # Part B (forcing function — seam is actually wired): override factory and
    # confirm make_engine now returns the custom sink. Pre-GREEN this fails
    # because run.py still calls EventLog(path) directly, ignoring the factory.
    class RecordingSink:
        def __init__(self, path=None):
            self.path = path
            self.calls: list[tuple] = []

        def append(self, event_type: str, payload: dict, run_id: str | None = None) -> dict:
            self.calls.append((event_type, payload, run_id))
            return {}

    sentinel = RecordingSink(path=explicit_path)
    set_default_event_sink_factory(lambda path=None: sentinel)

    try:
        eng2 = make_engine(explicit_path)
        assert eng2._event_log is sentinel, (
            "After factory override, make_engine must return the injected sink "
            "(seam not wired: run.py still calls EventLog directly)"
        )
    finally:
        reset_default_event_sink_factory()
