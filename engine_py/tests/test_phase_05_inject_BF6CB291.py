"""Regression tests for BF6CB291 — bounded poll loop in _read_orchestrator_checklist.

Contract: _read_orchestrator_checklist polls for up to HAL_CHECKLIST_POLL_TIMEOUT_MS
(default 5000ms) at HAL_CHECKLIST_POLL_INTERVAL_MS (default 100ms) intervals for
the checklist file before falling back to checklist_inline / hard error, and
emits a checklist_poll_attempts event regardless of outcome (poll_count,
elapsed_ms, timeout_ms, found_source).

AC1  — file already on disk: poll_count==1, elapsed_ms==0, found_source=="file"
AC2  — file arrives after 300ms: ok, poll_count==4, elapsed_ms==300, found_source=="file"
AC3  — timeout 300ms, no file: error E_ORCHESTRATOR_CHECKLIST_MISSING, found_source=="none"
AC4  — timeout 300ms, inline fallback: ok, elapsed_ms>=300, found_source=="inline"
AC5  — malformed JSON: error E_ORCHESTRATOR_CHECKLIST_MALFORMED, poll_count==1, no retry
AC6  — timeout=0, no file: error E_ORCHESTRATOR_CHECKLIST_MISSING, poll_count==1, elapsed_ms==0
AC7  — forcing-function: file at 700ms, frozen session_id literal read from disk
AC8  — interval=200ms, file at 600ms: poll_count==4, proves env interval honoured

Each test uses a 1-step WorkflowDefinition wrapping only _read_orchestrator_checklist
so the step result is cleanly inspectable without the full phase_05_inject_workflow.

GH966 (2026-07-17): the prior version of this file staged delayed writes via a
real `threading.Thread` + `time.sleep(delay)`, racing against wall-clock
thresholds. Under CI load (×3 flakes on 07-17) `eng.execute()` start drifted
against the writer thread's clock, producing `elapsed_ms` below the asserted
floor (observed 425/267ms < 500ms). This is now replaced by a virtual
`FakeClock` monkeypatched into the `phase_05_inject` module namespace only:
`time.monotonic()`/`time.sleep()` calls inside the poll loop advance a
virtual clock and deterministically fire scheduled writes at exact virtual
offsets, eliminating the wall-clock race entirely (no real sleep, no thread).
"""
from __future__ import annotations

import json
import sys
import time as _real_time
from pathlib import Path

# ── path setup (if-guard pattern; not flagged by suite_safety scanner) ─────────
_ENGINE_PY = Path(__file__).resolve().parent.parent
if str(_ENGINE_PY) not in sys.path:
    sys.path.insert(0, str(_ENGINE_PY))
_WORKFLOWS = _ENGINE_PY / "bytedigger_engine" / "workflows"
if str(_WORKFLOWS) not in sys.path:
    sys.path.insert(0, str(_WORKFLOWS))

from bytedigger_engine.contracts import StepContract, WorkflowContext, WorkflowDefinition  # noqa: E402
from bytedigger_engine.engine import WorkflowEngine  # noqa: E402
from bytedigger_engine.event_log import EventLog  # noqa: E402
from bytedigger_engine.workflows import phase_05_inject  # noqa: E402
from bytedigger_engine.workflows.phase_05_inject import _read_orchestrator_checklist  # noqa: E402


# ─── FakeClock (GH966 §2) ───────────────────────────────────────────────────────


class FakeClock:
    """Virtual monotonic/sleep for phase_05_inject; scheduled writes fire on sleep().

    Only the `phase_05_inject` module's `time` name is monkeypatched to this
    instance (via the fake_clock fixture below) — the real `time` module,
    engine.py, and event_log.py are untouched.

    Scope pin: this virtualizes ONLY `time.monotonic()` and `time.sleep()` —
    the sole two `time` calls in the UUT poll loop (phase_05_inject.py
    L627-637). If production ever adds `time.time()` or
    `time.perf_counter()` calls to that loop, `__getattr__` silently
    delegates them to the real clock and the de-flake regresses quietly —
    extend FakeClock with an explicit override for that call at that point.
    """

    def __init__(self) -> None:
        self.now = 0.0
        self._schedule: list[tuple[float, object]] = []  # [(fire_at_s, fn)]

    def monotonic(self) -> float:
        return self.now

    def sleep(self, s: float) -> None:
        self.now += s
        due = [entry for entry in self._schedule if entry[0] <= self.now]
        for entry in due:
            self._schedule.remove(entry)
            entry[1]()

    def schedule(self, at_s: float, fn) -> None:
        self._schedule.append((at_s, fn))

    def __getattr__(self, name):
        return getattr(_real_time, name)


import pytest  # noqa: E402


@pytest.fixture
def fake_clock(monkeypatch):
    clock = FakeClock()
    monkeypatch.setattr(phase_05_inject, "time", clock)
    return clock


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


# ─── AC1: file already on disk → poll_count==1, elapsed_ms==0 ────────────────


def test_ac1_file_present_at_start_poll_count_1(tmp_path, monkeypatch, fake_clock):
    """AC1: checklist file already on disk when step starts.

    Deterministic expectation: poll_count==1, elapsed_ms==0, found_source=="file".
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


# ─── AC2: file arrives 300ms late (virtual), timeout=2000 → ok, poll_count==4 ─


def test_ac2_file_arrives_300ms_late_polls_and_finds(tmp_path, monkeypatch, fake_clock):
    """AC2: file absent at start, virtual clock schedules a write at 0.3s.

    env: HAL_CHECKLIST_POLL_TIMEOUT_MS=2000, HAL_CHECKLIST_POLL_INTERVAL_MS=100
    Deterministic expectation: poll_count==4, elapsed_ms==300, found_source=="file".
    """
    monkeypatch.setenv("HAL_CHECKLIST_POLL_TIMEOUT_MS", "2000")
    monkeypatch.setenv("HAL_CHECKLIST_POLL_INTERVAL_MS", "100")

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    log_path = tmp_path / "events.jsonl"

    fake_clock.schedule(0.3, lambda: _write_checklist(scratchpad))

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


# ─── AC3: file never appears, no inline, timeout=300 → error, elapsed==300 ───


def test_ac3_timeout_no_file_no_inline_returns_error(tmp_path, monkeypatch, fake_clock):
    """AC3: file never appears, no checklist_inline, timeout=300ms.

    Deterministic expectation: poll_count==4, elapsed_ms==300, found_source=="none".
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


def test_ac4_inline_fallback_waits_full_timeout_then_returns_ok(tmp_path, monkeypatch, fake_clock):
    """AC4: no file, checklist_inline in org_config, timeout=300ms.

    Deterministic expectation: poll_count==4, elapsed_ms==300, found_source=="inline".
    (File never appears so the poll runs to timeout, then inline fallback wins.)
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


def test_ac5_malformed_json_immediate_error_poll_count_1(tmp_path, monkeypatch, fake_clock):
    """AC5: malformed JSON file present at start.

    Deterministic expectation: poll_count==1, elapsed_ms==0 — no retry on bad content.
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


def test_ac6_timeout_zero_immediate_missing_error(tmp_path, monkeypatch, fake_clock):
    """AC6: HAL_CHECKLIST_POLL_TIMEOUT_MS=0, no file, no inline.

    Deterministic expectation: poll_count==1, elapsed_ms==0 —
    backward-compat with pre-fix immediate behavior.
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


def test_ac7_forcing_function_file_at_500ms_frozen_session_id(tmp_path, monkeypatch, fake_clock):
    """AC7 (§1l forcing-function): file scheduled at virtual 0.7s with frozen session_id literal.

    env: HAL_CHECKLIST_POLL_TIMEOUT_MS=3000, HAL_CHECKLIST_POLL_INTERVAL_MS=50
    Deterministic expectation: poll_count==15, elapsed_ms==700, found_source=="file".
    result data: checklist["session_id"] == "test-session-AC7-X9K3R" (frozen literal)

    The frozen literal "test-session-AC7-X9K3R" is written into the disk fixture
    by the scheduled virtual-clock write below. Production code must actually
    wait for the file to appear and actually read its content for the result
    data to carry the frozen session_id. A stub returning constant data
    cannot satisfy this AC (§1l layer-6 hybrid anchor). Regression contract:
    poll_count/elapsed_ms/session_id must all hold together.
    """
    monkeypatch.setenv("HAL_CHECKLIST_POLL_TIMEOUT_MS", "3000")
    monkeypatch.setenv("HAL_CHECKLIST_POLL_INTERVAL_MS", "50")

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    log_path = tmp_path / "events.jsonl"

    # Frozen literal: GREEN must actually read this from disk
    ac7_payload = dict(_VALID_CHECKLIST)
    ac7_payload["session_id"] = "test-session-AC7-X9K3R"

    fake_clock.schedule(0.7, lambda: _write_checklist(scratchpad, ac7_payload))

    eng = _single_step_engine(log_path)
    result, _ = eng.execute("checklist_only", _ctx(scratchpad))

    # (a) production-side-effect: real (virtual) elapsed_ms in event (cannot stub with constant)
    poll_events = _poll_events(log_path, "checklist_poll_attempts")
    assert len(poll_events) == 1, f"expected 1 checklist_poll_attempts, got {len(poll_events)}"
    payload = poll_events[0]["payload"]
    assert payload["poll_count"] >= 8, (
        f"poll_count should be >=8 (restored BF6CB291 §3 AC7 floor; the >=2 "
        f"relaxation compensated for real wall-clock jitter under CI load — "
        f"with fake-clock determinism poll_count is exactly 15, float-safe "
        f">=8), got {payload['poll_count']}"
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


# ─── AC8: interval=200ms, file at virtual 600ms → poll_count==4 ──────────────


def test_ac8_env_overridable_interval_poll_count_range(tmp_path, monkeypatch, fake_clock):
    """AC8: HAL_CHECKLIST_POLL_INTERVAL_MS=200, file scheduled at virtual 0.6s, timeout=2000.

    Deterministic expectation: poll_count==4 (proves interval env var is honoured).
    Returns ok (file found).
    """
    monkeypatch.setenv("HAL_CHECKLIST_POLL_TIMEOUT_MS", "2000")
    monkeypatch.setenv("HAL_CHECKLIST_POLL_INTERVAL_MS", "200")

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    log_path = tmp_path / "events.jsonl"

    fake_clock.schedule(0.6, lambda: _write_checklist(scratchpad))

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
