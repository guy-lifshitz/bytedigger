"""RED tests for GH285-C3 — ACTIVE/STUCK timeout-state classification.

Spec: SHARED/memory/Decisions/2026-07-10_GH285_timeout_state_c3_spec.md

§1q: ``classify_timeout_state`` / ``resolve_active_threshold_bytes`` /
``ACTIVE_THRESHOLD_BYTES_DEFAULT`` do not exist yet in lib/timeout_policy.py.
Every import of those symbols happens INSIDE the test function body so the
file COLLECTS cleanly and each test FAILS at assert/import-in-body time
(never at collection time — see D1CF5FDF).

Harness reused verbatim from tests/test_llm_subprocess_775D6752.py: the
outer-deadline ``_TimedStdout`` / ``_stream_proc_with_stdout`` mock-Popen
rig (drives ``invoke_llm_subprocess`` -> ``_invoke_subprocess`` into the
``timed_out`` branch with controllable partial stdout), the idle-abort
harness (idle_timeout_sec + forever-block schedule -> E_LLM_NO_PROGRESS),
and the ``_FakeEventLog`` / ``telemetry_ctx.set_current_run`` fixture for
event-log assertions.
"""
from __future__ import annotations

import io
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest  # noqa: E402

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

from bytedigger_engine.llm_subprocess import invoke_llm_subprocess  # noqa: E402
from bytedigger_engine import llm_subprocess  # noqa: E402
from bytedigger_engine import telemetry_ctx  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_backends_and_telemetry(monkeypatch):
    monkeypatch.setattr(llm_subprocess, "emit_resolver_resolved", lambda *a, **kw: None)
    telemetry_ctx.clear_current_run()
    yield
    telemetry_ctx.clear_current_run()


# ─── Harness (mirrored from test_llm_subprocess_775D6752.py) ────────────────


class _FakeEventLog:
    def __init__(self):
        self.events: list[tuple[str, dict, str]] = []

    def append(self, event_type: str, payload: dict, run_id: str | None = None) -> None:
        self.events.append((event_type, payload, run_id or "ad-hoc"))


_SYSTEM_INIT = '{"type":"system","subtype":"init","session_id":"s1"}'
_RESULT_OK = (
    '{"type":"result","subtype":"success",'
    '"result":"FINAL_ANSWER_TEXT",'
    '"usage":{"input_tokens":10,"output_tokens":20,'
    '"cache_read_input_tokens":0,"cache_creation_input_tokens":0},'
    '"total_cost_usd":0.0123,"duration_ms":500}'
)


def _fat_system_event(pad_bytes: int) -> str:
    """A single system-init NDJSON line padded to at least ``pad_bytes``."""
    return (
        '{"type":"system","subtype":"init","session_id":"'
        + ("x" * pad_bytes)
        + '"}'
    )


class _TimedStdout:
    """Same forever-block-after-schedule stdout fake as 775D6752."""

    def __init__(self, schedule: list[tuple[float, str]], block_sec: float = 30.0):
        self._schedule = list(schedule)
        self._block_sec = block_sec
        self._idx = 0
        self._closed = threading.Event()

    def readline(self):
        if self._closed.is_set():
            return ""
        if self._idx >= len(self._schedule):
            end = time.monotonic() + self._block_sec
            while time.monotonic() < end and not self._closed.is_set():
                time.sleep(0.05)
            return ""
        delay, line = self._schedule[self._idx]
        self._idx += 1
        end = time.monotonic() + delay
        while time.monotonic() < end and not self._closed.is_set():
            time.sleep(0.05)
        if self._closed.is_set():
            return ""
        return line if line.endswith("\n") else line + "\n"

    def __iter__(self):
        while True:
            ln = self.readline()
            if not ln:
                return
            yield ln

    def read(self, *a, **k):
        return ""

    def close(self):
        self._closed.set()


def _stream_proc_with_stdout(stdout_obj, returncode: int = 0) -> MagicMock:
    proc = MagicMock()
    proc.pid = 12345
    proc.returncode = returncode
    proc.stdout = stdout_obj
    proc.stderr = io.StringIO("")
    proc.stdin = MagicMock()
    proc.wait = MagicMock(return_value=returncode)
    proc.kill = MagicMock(side_effect=lambda: getattr(stdout_obj, "close", lambda: None)())
    proc.communicate = MagicMock(return_value=("", ""))
    return proc


def _static_stream_proc(stdout_text: str, returncode: int = 0) -> MagicMock:
    proc = MagicMock()
    proc.pid = 12345
    proc.returncode = returncode
    proc.stdout = io.StringIO(stdout_text)
    proc.stderr = io.StringIO("")
    proc.stdin = MagicMock()
    proc.wait = MagicMock(return_value=returncode)
    proc.kill = MagicMock()
    proc.communicate = MagicMock(return_value=(stdout_text, ""))
    return proc


def _run_outer_timeout(schedule, timeout_sec=1, block_sec=30.0, **kwargs):
    """Drive invoke_llm_subprocess into the outer ``timed_out`` branch with
    the given NDJSON schedule feeding stdout content."""
    stdout_obj = _TimedStdout(schedule, block_sec=block_sec)
    proc = _stream_proc_with_stdout(stdout_obj, returncode=None)
    with patch("bytedigger_engine.llm_subprocess.subprocess.Popen", return_value=proc):
        result = invoke_llm_subprocess(
            prompt="x",
            model="sonnet",
            timeout_sec=timeout_sec,
            step_name="s",
            **kwargs,
        )
    return result


# ─── AC1-5: pure helper direct calls ─────────────────────────────────────────


def test_ac1_classify_timeout_state_active_above_threshold():
    """AC1: classify_timeout_state(50001, 50000) == "active" (strict >)."""
    from bytedigger_engine.lib.timeout_policy import classify_timeout_state

    assert classify_timeout_state(50001, 50000) == "active"


def test_ac2_classify_timeout_state_stuck_at_boundary():
    """AC2: classify_timeout_state(50000, 50000) == "stuck" (== is stuck)."""
    from bytedigger_engine.lib.timeout_policy import classify_timeout_state

    assert classify_timeout_state(50000, 50000) == "stuck"


def test_ac3_classify_timeout_state_stuck_at_zero():
    """AC3: classify_timeout_state(0, 50000) == "stuck"."""
    from bytedigger_engine.lib.timeout_policy import classify_timeout_state

    assert classify_timeout_state(0, 50000) == "stuck"


def test_ac4_resolve_active_threshold_bytes_default():
    """AC4: resolve_active_threshold_bytes(None) == 50000 and
    ACTIVE_THRESHOLD_BYTES_DEFAULT == 50000."""
    from bytedigger_engine.lib.timeout_policy import (
        ACTIVE_THRESHOLD_BYTES_DEFAULT,
        resolve_active_threshold_bytes,
    )

    assert resolve_active_threshold_bytes(None) == 50000
    assert ACTIVE_THRESHOLD_BYTES_DEFAULT == 50000


def test_ac5_resolve_active_threshold_bytes_override_and_fallback():
    """AC5: "120000" -> 120000; "abc" -> 50000 (malformed fall-through);
    "0" -> 1 (max(1, ·) mirror of resolve_timeout_sec semantics)."""
    from bytedigger_engine.lib.timeout_policy import resolve_active_threshold_bytes

    assert resolve_active_threshold_bytes("120000") == 120000
    assert resolve_active_threshold_bytes("abc") == 50000
    assert resolve_active_threshold_bytes("0") == 1


# ─── AC6-8, 11: outer-timeout StepResult payload/error contract ─────────────


def test_ac6_timed_out_small_stdout_yields_stuck():
    """AC6: timed-out invocation with small partial stdout ->
    data["timeout_state"] == "stuck", data["response_bytes"] == len(stdout
    bytes), data["active_threshold_bytes"] == 50000."""
    schedule = [(0.05, _SYSTEM_INIT)]
    result = _run_outer_timeout(schedule, timeout_sec=1, block_sec=10.0)

    expected_bytes = len((_SYSTEM_INIT + "\n").encode("utf-8"))
    assert result.data.get("timeout_state") == "stuck"
    assert result.data.get("response_bytes") == expected_bytes
    assert result.data.get("active_threshold_bytes") == 50000


def test_ac7_timed_out_fat_stdout_yields_active():
    """AC7: timed-out invocation with partial stdout > 50000 bytes ->
    data["timeout_state"] == "active"."""
    schedule = [(0.05, _fat_system_event(60000))]
    result = _run_outer_timeout(schedule, timeout_sec=1, block_sec=10.0)

    assert result.data.get("timeout_state") == "active"


def test_ac8_error_message_contains_timeout_state():
    """AC8: result.error contains "timeout_state=stuck" (AC6 run)."""
    schedule = [(0.05, _SYSTEM_INIT)]
    result = _run_outer_timeout(schedule, timeout_sec=1, block_sec=10.0)

    assert "timeout_state=stuck" in (result.error or "")


def test_ac11_error_code_unchanged():
    """AC11: AC6/AC7 result.error_code == "E_LLM_TIMEOUT" (unchanged)."""
    schedule = [(0.05, _SYSTEM_INIT)]
    result = _run_outer_timeout(schedule, timeout_sec=1, block_sec=10.0)

    assert result.error_code == "E_LLM_TIMEOUT"


# ─── AC9/9b: llm_timeout_state event emission (positive + negative) ─────────


def test_ac9_llm_timeout_state_event_emitted_once():
    """AC9: AC6/AC7 run with a real run_ctx/event_log fixture -> exactly
    ONE llm_timeout_state event, payload carries timeout_state,
    response_bytes, active_threshold_bytes, timeout_sec."""
    schedule = [(0.05, _SYSTEM_INIT)]
    log = _FakeEventLog()
    telemetry_ctx.set_current_run(
        event_log=log, run_id="r-timeout-state", step_name="s", phase="p"
    )
    try:
        stdout_obj = _TimedStdout(schedule, block_sec=10.0)
        proc = _stream_proc_with_stdout(stdout_obj, returncode=None)
        with patch("bytedigger_engine.llm_subprocess.subprocess.Popen", return_value=proc):
            invoke_llm_subprocess(
                prompt="x", model="sonnet", timeout_sec=1, step_name="s"
            )
    finally:
        telemetry_ctx.clear_current_run()

    matches = [e for e in log.events if e[0] == "llm_timeout_state"]
    assert len(matches) == 1, (
        "expected exactly one llm_timeout_state event, got "
        + repr([e[0] for e in log.events])
    )
    payload = matches[0][1]
    for key in ("timeout_state", "response_bytes", "active_threshold_bytes", "timeout_sec"):
        assert key in payload, f"llm_timeout_state payload missing key {key!r}"


def test_ac9b_success_path_emits_zero_llm_timeout_state_events():
    """AC9b: success-path invocation emits ZERO llm_timeout_state events."""
    log = _FakeEventLog()
    telemetry_ctx.set_current_run(
        event_log=log, run_id="r-success", step_name="s", phase="p"
    )
    try:
        proc = _static_stream_proc(_RESULT_OK + "\n", returncode=0)
        with patch("bytedigger_engine.llm_subprocess.subprocess.Popen", return_value=proc):
            result = invoke_llm_subprocess(
                prompt="x", model="sonnet", timeout_sec=20, step_name="s"
            )
    finally:
        telemetry_ctx.clear_current_run()

    assert result.status == "ok"
    matches = [e for e in log.events if e[0] == "llm_timeout_state"]
    assert matches == []


# ─── AC10: idle-abort path never carries timeout_state ──────────────────────


def test_ac10_idle_abort_no_timeout_state_key_or_event():
    """AC10: idle-abort path (E_LLM_NO_PROGRESS) -> no "timeout_state" key
    in data, zero llm_timeout_state events."""
    schedule = [(0.05, _SYSTEM_INIT)]
    stdout_obj = _TimedStdout(schedule, block_sec=30.0)
    proc = _stream_proc_with_stdout(stdout_obj, returncode=None)

    log = _FakeEventLog()
    telemetry_ctx.set_current_run(
        event_log=log, run_id="r-idle", step_name="s", phase="p"
    )
    try:
        with patch("bytedigger_engine.llm_subprocess.subprocess.Popen", return_value=proc):
            result = invoke_llm_subprocess(
                prompt="x",
                model="sonnet",
                timeout_sec=60,
                step_name="s",
                idle_timeout_sec=1,
            )
    finally:
        telemetry_ctx.clear_current_run()

    assert result.error_code == "E_LLM_NO_PROGRESS"
    assert "timeout_state" not in (result.data or {})
    matches = [e for e in log.events if e[0] == "llm_timeout_state"]
    assert matches == []


# ─── AC12: env override end-to-end ───────────────────────────────────────────


def test_ac12_env_override_lowers_active_threshold(monkeypatch):
    """AC12: env HAL_TIMEOUT_ACTIVE_THRESHOLD_BYTES=10 (monkeypatch) +
    stdout of ~100 bytes -> timeout_state == "active",
    active_threshold_bytes == 10."""
    monkeypatch.setenv("HAL_TIMEOUT_ACTIVE_THRESHOLD_BYTES", "10")
    schedule = [(0.05, _fat_system_event(100))]
    result = _run_outer_timeout(schedule, timeout_sec=1, block_sec=10.0)

    assert result.data.get("timeout_state") == "active"
    assert result.data.get("active_threshold_bytes") == 10
