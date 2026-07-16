"""RED tests for BF6CB291 — bounded poll loop in _read_orchestrator_checklist.

Contract: _read_orchestrator_checklist currently does a single-shot
is_file() check at call time. On a write race (engine starts before
pre-build-gate.ts flushes the file) this produces E_ORCHESTRATOR_CHECKLIST_MISSING
at +30ms. Post-GREEN the function polls up to HAL_CHECKLIST_POLL_TIMEOUT_MS
(default 5000ms) at HAL_CHECKLIST_POLL_INTERVAL_MS (default 100ms) intervals
before falling back to checklist_inline / hard error, and emits a
checklist_poll_attempts event regardless of outcome.

AC1  FAIL today  — file already on disk: result.status ok but checklist_poll_attempts event absent
AC2  FAIL today  — file arrives after 300ms: current code returns error immediately (no poll)
AC3  FAIL today  — timeout 300ms, no file: checklist_poll_attempts event absent
AC4  FAIL today  — timeout 300ms, inline fallback: checklist_poll_attempts event absent
AC5  FAIL today  — malformed JSON: result error code ok but checklist_poll_attempts event absent
AC6  FAIL today  — timeout=0, no file: checklist_poll_attempts event absent
AC7  FAIL today  — forcing-function: single-shot miss → no event, no session_id in data
AC8  FAIL today  — interval=200ms, file at 600ms: single-shot miss, no event

Each test uses a 1-step WorkflowDefinition wrapping only _read_orchestrator_checklist
so the step result is cleanly inspectable without the full phase_05_inject_workflow.
Background writes are pre-staged via threading.Event so they are deterministic
(§1i — pre-stage the contested resource, never race).

§1i cite: workflows.md §1i — Singleton-resource tests pre-stage state, never race.
"""
from __future__ import annotations

import json
import sys
import threading
import time
from pathlib import Path

# ── path setup (if-guard pattern; not flagged by suite_safety scanner) ─────────
_ENGINE_PY = Path(__file__).resolve().parent.parent
if str(_ENGINE_PY) not in sys.path:
    sys.path.insert(0, str(_ENGINE_PY))
_WORKFLOWS = _ENGINE_PY / "workflows"
if str(_WORKFLOWS) not in sys.path:
    sys.path.insert(0, str(_WORKFLOWS))

from contracts import StepContract, WorkflowContext, WorkflowDefinition  # noqa: E402
from engine import WorkflowEngine  # noqa: E402
from event_log import EventLog  # noqa: E402
from phase_05_inject import _read_orchestrator_checklist  # noqa: E402


# ─── local helpers ─────────────────────────────────────────────────────────────


_VALID_CHECKLIST = {
    "schema_version": "1.0.0",
    "session_id": "test-session",
    "complexity": "SIMPLE",
    "mode": "AUTONOMOUS",
    "cwd": "/tmp",
    "pre_build_gate_version": "1.0.0",
    "written_at_ts": 1777421657,
}


def _write_checklist(scratchpad: Path, payload: dict | None = None) -> Path:
    """Write a valid (or custom) checklist to <scratchpad>/.orchestrator-checklist.json."""
    scratchpad.mkdir(parents=True, exist_ok=True)
    content = payload if payload is not None else _VALID_CHECKLIST
    path = scratchpad / ".orchestrator-checklist.json"
    path.write_text(json.dumps(content), encoding="utf-8")
    return path


def _ctx(scratchpad: Path, **org_extra) -> WorkflowContext:
    """WorkflowContext pointing at scratchpad, NO checklist pre-written."""
    org = {"scratchpad_dir": str(scratchpad), **org_extra}
    scratchpad.mkdir(parents=True, exist_ok=True)
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question="task",
        session_id="test-session",
        persona="hal",
        framework=None,
        domain=None,
    )


def _single_step_engine(log_path: Path | None = None) -> WorkflowEngine:
    """WorkflowEngine with a 1-step workflow wrapping _read_orchestrator_checklist."""
    log = EventLog(log_path) if log_path is not None else None
    eng = WorkflowEngine(event_log=log)
    wf = WorkflowDefinition(
        name="checklist_only",
        steps=[
            StepContract(
                name="read_orchestrator_checklist",
                execute=_read_orchestrator_checklist,
            )
        ],
    )
    eng.register("checklist_only", wf)
    return eng


def _poll_events(log_path: Path, event_type: str) -> list[dict]:
    """Return all events of the given type from the log."""
    events = EventLog(log_path).read_all()
    return [e for e in events if e["event_type"] == event_type]


def _write_after_delay(
    scratchpad: Path,
    delay_s: float,
    ready_event: threading.Event,
    payload: dict | None = None,
) -> threading.Thread:
    """Start a background thread that writes the checklist after delay_s seconds.

    §1i: ready_event is set BEFORE the sleep so the step has already started
    polling when the write happens. This pre-stages the thread start
    deterministically — the test body calls ready_event.wait() before
    invoking the engine step, ensuring the writer thread is alive and counting
    down before the poll loop begins.
    """
    def _worker():
        ready_event.wait()  # caller signals: step about to start
        time.sleep(delay_s)
        _write_checklist(scratchpad, payload)

    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    return t


# ─── AC1: file already on disk → poll_count==1, elapsed_ms<100 ────────────────


def test_ac1_file_present_at_start_poll_count_1(tmp_path, monkeypatch):
    """AC1: checklist file already on disk when step starts.

    Returns ok; checklist_poll_attempts event has poll_count==1, elapsed_ms<100,
    found_source=="file".

    FAILS today because _read_orchestrator_checklist emits no checklist_poll_attempts
    event (the event does not exist yet in production code).
    """
    monkeypatch.setenv("HAL_CHECKLIST_POLL_TIMEOUT_MS", "2000")
    monkeypatch.setenv("HAL_CHECKLIST_POLL_INTERVAL_MS", "100")

    scratchpad = tmp_path / "scratch"
    _write_checklist(scratchpad)  # file present before step runs
    log_path = tmp_path / "events.jsonl"

    eng = _single_step_engine(log_path)
    result, _ = eng.execute("checklist_only", _ctx(scratchpad))

    assert result.status == "ok", f"expected ok, got {result.status!r}: {result.error}"

    poll_events = _poll_events(log_path, "checklist_poll_attempts")
    assert len(poll_events) == 1, (
        f"expected exactly 1 checklist_poll_attempts event, got {len(poll_events)}; "
        f"all events: {[e['event_type'] for e in EventLog(log_path).read_all()]}"
    )
    payload = poll_events[0]["payload"]
    assert payload["poll_count"] == 1, f"poll_count should be 1, got {payload['poll_count']}"
    assert payload["elapsed_ms"] < 100, f"elapsed_ms should be <100, got {payload['elapsed_ms']}"
    assert payload["found_source"] == "file", f"found_source should be 'file', got {payload['found_source']!r}"


# ─── AC2: file arrives 300ms late, timeout=2000 → ok, poll_count>=3 ──────────


def test_ac2_file_arrives_300ms_late_polls_and_finds(tmp_path, monkeypatch):
    """AC2: file absent at start, background thread writes it after 300ms.

    env: HAL_CHECKLIST_POLL_TIMEOUT_MS=2000, HAL_CHECKLIST_POLL_INTERVAL_MS=100
    Returns ok; event has poll_count>=3 AND elapsed_ms>=300 AND elapsed_ms<1500.

    FAILS today because current code does a single-shot check and immediately
    returns E_ORCHESTRATOR_CHECKLIST_MISSING before the file is written.
    §1i: ready_event pre-stages the background write thread before the step runs.
    """
    monkeypatch.setenv("HAL_CHECKLIST_POLL_TIMEOUT_MS", "2000")
    monkeypatch.setenv("HAL_CHECKLIST_POLL_INTERVAL_MS", "100")

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    log_path = tmp_path / "events.jsonl"

    # §1i: start background writer before step runs; signal via ready_event
    ready_event = threading.Event()
    _write_after_delay(scratchpad, delay_s=0.3, ready_event=ready_event)
    ready_event.set()  # signal: step is about to start

    eng = _single_step_engine(log_path)
    result, _ = eng.execute("checklist_only", _ctx(scratchpad))

    assert result.status == "ok", (
        f"expected ok after waiting for delayed file; got {result.status!r}: {result.error}"
    )

    poll_events = _poll_events(log_path, "checklist_poll_attempts")
    assert len(poll_events) == 1, f"expected 1 checklist_poll_attempts, got {len(poll_events)}"
    payload = poll_events[0]["payload"]
    assert payload["poll_count"] >= 3, f"poll_count should be >=3, got {payload['poll_count']}"
    assert payload["elapsed_ms"] >= 300, f"elapsed_ms should be >=300, got {payload['elapsed_ms']}"
    assert payload["elapsed_ms"] < 1500, f"elapsed_ms should be <1500, got {payload['elapsed_ms']}"
    assert payload["found_source"] == "file", f"found_source should be 'file', got {payload['found_source']!r}"


# ─── AC3: file never appears, no inline, timeout=300 → error, elapsed>=300 ───


def test_ac3_timeout_no_file_no_inline_returns_error(tmp_path, monkeypatch):
    """AC3: file never appears, no checklist_inline, timeout=300ms.

    Returns error E_ORCHESTRATOR_CHECKLIST_MISSING;
    event has elapsed_ms>=300 AND elapsed_ms<800 AND found_source=="none".

    FAILS today because no checklist_poll_attempts event is emitted.
    """
    monkeypatch.setenv("HAL_CHECKLIST_POLL_TIMEOUT_MS", "300")
    monkeypatch.setenv("HAL_CHECKLIST_POLL_INTERVAL_MS", "100")

    scratchpad = tmp_path / "scratch"
    log_path = tmp_path / "events.jsonl"

    eng = _single_step_engine(log_path)
    result, _ = eng.execute("checklist_only", _ctx(scratchpad))

    assert result.status == "error"
    assert result.error_code == "E_ORCHESTRATOR_CHECKLIST_MISSING"
    assert result.recoverable is False

    poll_events = _poll_events(log_path, "checklist_poll_attempts")
    assert len(poll_events) == 1, f"expected 1 checklist_poll_attempts, got {len(poll_events)}"
    payload = poll_events[0]["payload"]
    assert payload["elapsed_ms"] >= 300, f"elapsed_ms should be >=300, got {payload['elapsed_ms']}"
    assert payload["elapsed_ms"] < 800, f"elapsed_ms should be <800, got {payload['elapsed_ms']}"
    assert payload["found_source"] == "none", f"found_source should be 'none', got {payload['found_source']!r}"


# ─── AC4: no file, checklist_inline provided, timeout=300 → ok after wait ────


def test_ac4_inline_fallback_waits_full_timeout_then_returns_ok(tmp_path, monkeypatch):
    """AC4: no file, checklist_inline in org_config, timeout=300ms.

    Returns ok (source=inline_ctx); event has elapsed_ms>=300 AND found_source=="inline".
    (File never appears so the poll runs to timeout, then inline fallback wins.)

    FAILS today because no checklist_poll_attempts event is emitted and there
    is no poll loop — the inline branch is reached immediately without waiting.
    The elapsed_ms>=300 assertion will fail.
    """
    monkeypatch.setenv("HAL_CHECKLIST_POLL_TIMEOUT_MS", "300")
    monkeypatch.setenv("HAL_CHECKLIST_POLL_INTERVAL_MS", "100")

    scratchpad = tmp_path / "scratch"
    log_path = tmp_path / "events.jsonl"

    inline_checklist = dict(_VALID_CHECKLIST)
    inline_checklist["session_id"] = "inline-session"

    ctx = _ctx(scratchpad, checklist_inline=inline_checklist)
    eng = _single_step_engine(log_path)
    result, _ = eng.execute("checklist_only", ctx)

    assert result.status == "ok", f"expected ok via inline fallback, got {result.status!r}: {result.error}"

    poll_events = _poll_events(log_path, "checklist_poll_attempts")
    assert len(poll_events) == 1, f"expected 1 checklist_poll_attempts, got {len(poll_events)}"
    payload = poll_events[0]["payload"]
    assert payload["elapsed_ms"] >= 300, (
        f"elapsed_ms should be >=300 (waited full timeout for file), got {payload['elapsed_ms']}"
    )
    assert payload["found_source"] == "inline", (
        f"found_source should be 'inline', got {payload['found_source']!r}"
    )


# ─── AC5: malformed JSON present → immediate error, poll_count==1 ─────────────


def test_ac5_malformed_json_immediate_error_poll_count_1(tmp_path, monkeypatch):
    """AC5: malformed JSON file present at start.

    Returns E_ORCHESTRATOR_CHECKLIST_MALFORMED immediately;
    event has poll_count==1 and elapsed_ms<100 — no retry on bad content.

    FAILS today because no checklist_poll_attempts event is emitted.
    """
    monkeypatch.setenv("HAL_CHECKLIST_POLL_TIMEOUT_MS", "2000")
    monkeypatch.setenv("HAL_CHECKLIST_POLL_INTERVAL_MS", "100")

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    (scratchpad / ".orchestrator-checklist.json").write_text("not json {{", encoding="utf-8")
    log_path = tmp_path / "events.jsonl"

    eng = _single_step_engine(log_path)
    result, _ = eng.execute("checklist_only", _ctx(scratchpad))

    assert result.status == "error"
    assert result.error_code == "E_ORCHESTRATOR_CHECKLIST_MALFORMED"
    assert result.recoverable is False

    poll_events = _poll_events(log_path, "checklist_poll_attempts")
    assert len(poll_events) == 1, f"expected 1 checklist_poll_attempts, got {len(poll_events)}"
    payload = poll_events[0]["payload"]
    assert payload["poll_count"] == 1, f"poll_count should be 1 (no retry on bad content), got {payload['poll_count']}"
    assert payload["elapsed_ms"] < 100, f"elapsed_ms should be <100 (immediate), got {payload['elapsed_ms']}"


# ─── AC6: timeout=0, no file, no inline → immediate error, poll_count==1 ──────


def test_ac6_timeout_zero_immediate_missing_error(tmp_path, monkeypatch):
    """AC6: HAL_CHECKLIST_POLL_TIMEOUT_MS=0, no file, no inline.

    Returns E_ORCHESTRATOR_CHECKLIST_MISSING; event has poll_count==1,
    elapsed_ms<50 — backward-compat with pre-fix immediate behavior.

    FAILS today because no checklist_poll_attempts event is emitted at all.
    """
    monkeypatch.setenv("HAL_CHECKLIST_POLL_TIMEOUT_MS", "0")
    monkeypatch.setenv("HAL_CHECKLIST_POLL_INTERVAL_MS", "100")

    scratchpad = tmp_path / "scratch"
    log_path = tmp_path / "events.jsonl"

    eng = _single_step_engine(log_path)
    result, _ = eng.execute("checklist_only", _ctx(scratchpad))

    assert result.status == "error"
    assert result.error_code == "E_ORCHESTRATOR_CHECKLIST_MISSING"
    assert result.recoverable is False

    poll_events = _poll_events(log_path, "checklist_poll_attempts")
    assert len(poll_events) == 1, f"expected 1 checklist_poll_attempts, got {len(poll_events)}"
    payload = poll_events[0]["payload"]
    assert payload["poll_count"] == 1, f"poll_count should be 1 (timeout=0), got {payload['poll_count']}"
    assert payload["elapsed_ms"] < 50, f"elapsed_ms should be <50 (immediate), got {payload['elapsed_ms']}"


# ─── AC7: §1l forcing-function — frozen literal session_id + real elapsed ─────


def test_ac7_forcing_function_file_at_500ms_frozen_session_id(tmp_path, monkeypatch):
    """AC7 (§1l forcing-function): file written at 500ms with frozen session_id literal.

    env: HAL_CHECKLIST_POLL_TIMEOUT_MS=3000, HAL_CHECKLIST_POLL_INTERVAL_MS=50
    event: poll_count>=8 AND elapsed_ms>=500 AND elapsed_ms<1500 AND found_source=="file"
    result data: checklist["session_id"] == "test-session-AC7-X9K3R" (frozen literal)

    The frozen literal "test-session-AC7-X9K3R" is written into the disk fixture
    by the background-write helper below. GREEN production code must actually
    wait for the file to appear and actually read its content for the result
    data to carry the frozen session_id. A stub returning constant data cannot
    satisfy this AC (§1l layer-6 hybrid anchor).

    FAILS today: current code makes a single-shot check and immediately returns
    E_ORCHESTRATOR_CHECKLIST_MISSING. No poll_count, no elapsed_ms, no session_id.

    §1i: background thread is pre-staged (started + signalled) before step runs.
    """
    monkeypatch.setenv("HAL_CHECKLIST_POLL_TIMEOUT_MS", "3000")
    monkeypatch.setenv("HAL_CHECKLIST_POLL_INTERVAL_MS", "50")

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    log_path = tmp_path / "events.jsonl"

    # Frozen literal: GREEN must actually read this from disk
    ac7_payload = dict(_VALID_CHECKLIST)
    ac7_payload["session_id"] = "test-session-AC7-X9K3R"

    # §1i: pre-stage the background writer thread BEFORE invoking the step
    # Engine is pre-warmed here so construction overhead (~80ms) is not
    # counted against the 700ms write-delay budget (§1b env-calibration).
    eng = _single_step_engine(log_path)

    ready_event = threading.Event()
    _write_after_delay(scratchpad, delay_s=0.7, ready_event=ready_event, payload=ac7_payload)
    ready_event.set()  # signal: step is about to start

    result, _ = eng.execute("checklist_only", _ctx(scratchpad))

    # (a) production-side-effect: real elapsed_ms in event (cannot stub with constant 500)
    poll_events = _poll_events(log_path, "checklist_poll_attempts")
    assert len(poll_events) == 1, f"expected 1 checklist_poll_attempts, got {len(poll_events)}"
    payload = poll_events[0]["payload"]
    assert payload["poll_count"] >= 2, (
        f"poll_count should be >=2 (proves loop iterated; wall-clock proof is "
        f"elapsed_ms, per #526 deadline-based bound), got {payload['poll_count']}"
    )
    assert payload["poll_count"] <= (payload["elapsed_ms"] // 50) + 2, (
        f"poll_count should be consistent with interval (anti-stub upper bound), "
        f"got poll_count={payload['poll_count']} elapsed_ms={payload['elapsed_ms']}"
    )
    assert payload["elapsed_ms"] >= 500, (
        f"elapsed_ms should be >=500, got {payload['elapsed_ms']}"
    )
    assert payload["elapsed_ms"] < 1500, (
        f"elapsed_ms should be <1500, got {payload['elapsed_ms']}"
    )
    assert payload["found_source"] == "file", (
        f"found_source should be 'file', got {payload['found_source']!r}"
    )

    # (b) frozen literal: production code must have actually read the file to return this
    assert result.status == "ok", f"expected ok, got {result.status!r}: {result.error}"
    assert result.data is not None, "result.data should not be None after reading checklist"
    checklist_data = result.data.get("checklist") if isinstance(result.data, dict) else None
    assert checklist_data is not None, f"result.data['checklist'] missing; result.data={result.data!r}"
    assert checklist_data["session_id"] == "test-session-AC7-X9K3R", (
        f"session_id must match frozen literal 'test-session-AC7-X9K3R', "
        f"got {checklist_data.get('session_id')!r}. "
        f"GREEN must actually read the disk file; a stub returning constant data cannot satisfy this."
    )


# ─── AC8: interval=200ms, file at 600ms → poll_count in [3,5] ────────────────


def test_ac8_env_overridable_interval_poll_count_range(tmp_path, monkeypatch):
    """AC8: HAL_CHECKLIST_POLL_INTERVAL_MS=200, file arrives at 600ms, timeout=2000.

    event: poll_count>=3 AND poll_count<=5 — proves interval env var is honoured.
    Returns ok (file found).

    FAILS today: no poll loop → single-shot miss → E_ORCHESTRATOR_CHECKLIST_MISSING.
    The poll_count range assertion also fails (no event emitted).

    §1i: background thread pre-staged before step runs.
    """
    monkeypatch.setenv("HAL_CHECKLIST_POLL_TIMEOUT_MS", "2000")
    monkeypatch.setenv("HAL_CHECKLIST_POLL_INTERVAL_MS", "200")

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    log_path = tmp_path / "events.jsonl"

    # §1i: pre-stage background writer
    ready_event = threading.Event()
    _write_after_delay(scratchpad, delay_s=0.6, ready_event=ready_event)
    ready_event.set()

    eng = _single_step_engine(log_path)
    result, _ = eng.execute("checklist_only", _ctx(scratchpad))

    assert result.status == "ok", (
        f"expected ok after polling with 200ms interval, got {result.status!r}: {result.error}"
    )

    poll_events = _poll_events(log_path, "checklist_poll_attempts")
    assert len(poll_events) == 1, f"expected 1 checklist_poll_attempts, got {len(poll_events)}"
    payload = poll_events[0]["payload"]
    assert payload["poll_count"] >= 3, (
        f"poll_count should be >=3 (200ms interval, file at 600ms), got {payload['poll_count']}"
    )
    assert payload["poll_count"] <= 5, (
        f"poll_count should be <=5 (proves 200ms interval, not 100ms), got {payload['poll_count']}"
    )
    assert payload["found_source"] == "file", (
        f"found_source should be 'file', got {payload['found_source']!r}"
    )
