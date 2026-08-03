"""Category A — subprocess lifecycle telemetry (decree 2026-04-26).

Verifies invoke_llm_subprocess emits subprocess_spawned/subprocess_exited
when a current-run context is active, with correct payload shape and
graceful degradation when no event log / no JSON output.

25e75663 migration: command= seam replaced by model=str. All tests that
used echo_stub/passthrough_stub/python3-c are adapted to use model= and
patch Popen. cmd_kind is now always "claude_p" (Class C: was "shell" for
python3 stubs). The --output-format json token path is gone from
invoke_llm_subprocess (always stream-json now); token/cost tests adapted.
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

from bytedigger_engine.llm_subprocess import (  # noqa: E402
    invoke_llm_subprocess,
    register_backend,
    reset_backends,
)
from bytedigger_engine import llm_subprocess  # noqa: E402
from bytedigger_engine import telemetry_ctx  # noqa: E402


# ---------------------------------------------------------------------------
# §1i autouse teardown
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_backends_and_telemetry(monkeypatch):
    monkeypatch.setattr(llm_subprocess, "emit_resolver_resolved", lambda *a, **kw: None)
    telemetry_ctx.clear_current_run()
    yield
    telemetry_ctx.clear_current_run()
    reset_backends()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

class _FakeEventLog:
    def __init__(self):
        self.events: list[tuple[str, dict, str]] = []

    def append(self, event_type: str, payload: dict, run_id: str | None = None) -> None:
        self.events.append((event_type, payload, run_id or "ad-hoc"))


_RESULT_EVENT_OK = (
    '{"type":"result","subtype":"success",'
    '"result":"ok",'
    '"usage":{"input_tokens":1,"output_tokens":1,'
    '"cache_read_input_tokens":0,"cache_creation_input_tokens":0},'
    '"total_cost_usd":0.001,"duration_ms":10}\n'
)

_RESULT_EVENT_HELLO = (
    '{"type":"result","subtype":"success",'
    '"result":"HELLO",'
    '"usage":{"input_tokens":1,"output_tokens":1,'
    '"cache_read_input_tokens":0,"cache_creation_input_tokens":0},'
    '"total_cost_usd":0.001,"duration_ms":10}\n'
)

_RESULT_EVENT_NOTOKENS = (
    '{"type":"result","subtype":"success",'
    '"result":"plain text response",'
    '"usage":{},'
    '"total_cost_usd":null,"duration_ms":10}\n'
)


def _stream_proc(stdout: str = _RESULT_EVENT_OK, returncode: int = 0) -> MagicMock:
    """Stream-json Popen mock for testing telemetry."""
    proc = MagicMock()
    proc.pid = 12345
    proc.returncode = returncode
    proc.stdout = io.StringIO(stdout)
    proc.stderr = io.StringIO("")
    proc.stdin = MagicMock()
    proc.wait = MagicMock(return_value=returncode)
    proc.communicate = MagicMock(return_value=(stdout, ""))
    return proc


def _stream_proc_with_stderr(stdout: str, stderr_text: str, returncode: int) -> MagicMock:
    proc = MagicMock()
    proc.pid = 12345
    proc.returncode = returncode
    proc.stdout = io.StringIO(stdout)
    proc.stderr = io.StringIO(stderr_text)
    proc.stdin = MagicMock()
    proc.wait = MagicMock(return_value=returncode)
    proc.communicate = MagicMock(return_value=(stdout, stderr_text))
    return proc


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_no_current_run_no_telemetry():
    """When no current run is set, helper still works but emits nothing."""
    log = _FakeEventLog()
    telemetry_ctx.clear_current_run()
    with patch("bytedigger_engine.llm_subprocess.subprocess.Popen", return_value=_stream_proc(_RESULT_EVENT_OK)):
        r = invoke_llm_subprocess(
            prompt="hi",
            model="sonnet",
            timeout_sec=10,
            step_name="step",
            idle_timeout_sec=0,
            straggler_cfg=None,
        )
    assert r.status == "ok"
    assert log.events == []  # no telemetry without active run ctx


def test_subprocess_spawned_fires_with_pid_cmd_step_name():
    log = _FakeEventLog()
    telemetry_ctx.set_current_run(event_log=log, run_id="r1", step_name="my_step", phase="phase_1")
    try:
        with patch("bytedigger_engine.llm_subprocess.subprocess.Popen", return_value=_stream_proc(_RESULT_EVENT_OK)):
            invoke_llm_subprocess(
                prompt="hi",
                model="sonnet",
                timeout_sec=10,
                step_name="my_step",
                idle_timeout_sec=0,
                straggler_cfg=None,
            )
    finally:
        telemetry_ctx.clear_current_run()

    spawned = [e for e in log.events if e[0] == "subprocess_spawned"]
    assert len(spawned) == 1
    payload = spawned[0][1]
    assert "cmd" not in payload, "old full-cmd field must be absent (replaced by cmd_tail per W1/AC1)"
    assert "cmd_tail" in payload, "subprocess_spawned must have cmd_tail field"
    assert isinstance(payload["cmd_tail"], list), "cmd_tail must be a list"
    assert payload["step_name"] == "my_step"
    assert payload["phase"] == "phase_1"
    assert isinstance(payload["pid"], int)
    assert payload["pid"] > 0
    assert payload["prompt_size_bytes"] == len("hi")
    # 25e75663 Class C: cmd_kind is now always "claude_p" for model= invocations
    assert payload["cmd_kind"] == "claude_p", (
        "25e75663: cmd_kind must be 'claude_p' for model= invocations; "
        "got: " + repr(payload.get("cmd_kind"))
    )


def test_subprocess_exited_paired_with_spawn_pid_and_duration():
    log = _FakeEventLog()
    telemetry_ctx.set_current_run(event_log=log, run_id="r1", step_name="s", phase="p")
    try:
        with patch("bytedigger_engine.llm_subprocess.subprocess.Popen", return_value=_stream_proc(_RESULT_EVENT_HELLO)):
            invoke_llm_subprocess(
                prompt="hi",
                model="sonnet",
                timeout_sec=10,
                step_name="s",
                idle_timeout_sec=0,
                straggler_cfg=None,
            )
    finally:
        telemetry_ctx.clear_current_run()

    spawned = [e for e in log.events if e[0] == "subprocess_spawned"][0]
    exited = [e for e in log.events if e[0] == "subprocess_exited"][0]
    assert spawned[1]["pid"] == exited[1]["pid"]
    assert exited[1]["exit_code"] == 0
    assert isinstance(exited[1]["duration_ms"], int)
    assert exited[1]["duration_ms"] >= 0
    # response_size_bytes = len(stdout.encode()) where stdout is the full raw NDJSON stream
    # (llm_subprocess.py line 1155: len(stdout.encode("utf-8"))), not the extracted result text.
    assert exited[1]["response_size_bytes"] == len(_RESULT_EVENT_HELLO.encode("utf-8"))
    assert "HELLO" in exited[1]["stdout_tail"]


def test_claude_p_cmd_kind_detected():
    """argv[0] containing 'claude' is classified as claude_p."""
    log = _FakeEventLog()
    telemetry_ctx.set_current_run(event_log=log, run_id="r", step_name="s", phase="p")
    try:
        with patch("bytedigger_engine.llm_subprocess.subprocess.Popen", return_value=_stream_proc()):
            invoke_llm_subprocess(
                prompt="x",
                model="sonnet",
                timeout_sec=10,
                step_name="s",
                idle_timeout_sec=0,
                straggler_cfg=None,
            )
    finally:
        telemetry_ctx.clear_current_run()
    # model= always produces claude_p
    spawned = [e for e in log.events if e[0] == "subprocess_spawned"][0]
    assert spawned[1]["cmd_kind"] == "claude_p"

    # Now test claude detection — use a path string that contains 'claude'.
    # We can't actually invoke it, but we test argv[0]-based classification
    # via an explicit test of the helper.
    from bytedigger_engine.llm_subprocess import _classify_cmd_kind  # noqa: E402
    assert _classify_cmd_kind(["/usr/local/bin/claude", "-p"]) == "claude_p"
    assert _classify_cmd_kind(["claude", "-p"]) == "claude_p"
    assert _classify_cmd_kind(["python3", "-c", "x"]) == "shell"


def test_model_extracted_from_argv():
    from bytedigger_engine.llm_subprocess import _extract_model  # noqa: E402
    assert _extract_model(["claude", "-p", "--model", "opus-4.7"]) == "opus-4.7"
    assert _extract_model(["claude", "--model=sonnet"]) == "sonnet"
    assert _extract_model(["claude", "-p"]) is None


def test_claude_json_output_populates_tokens_and_cost():
    """When stream-json result event has usage, tokens and cost are parsed.
    25e75663 Class C: the --output-format json caller-supplied path is gone.
    Adapted to test the stream-json token extraction path."""
    log = _FakeEventLog()
    telemetry_ctx.set_current_run(event_log=log, run_id="r", step_name="s", phase="p")
    try:
        # Stream-json result event with usage data
        stream_event = json.dumps({
            "type": "result",
            "subtype": "success",
            "result": "ok",
            "usage": {
                "input_tokens": 100,
                "output_tokens": 200,
                "cache_read_input_tokens": 50,
                "cache_creation_input_tokens": 10,
            },
            "total_cost_usd": 0.0123,
            "duration_ms": 100,
        }) + "\n"
        with patch("bytedigger_engine.llm_subprocess.subprocess.Popen", return_value=_stream_proc(stream_event)):
            invoke_llm_subprocess(
                prompt="x",
                model="sonnet",
                timeout_sec=10,
                step_name="s",
                idle_timeout_sec=0,
                straggler_cfg=None,
            )
    finally:
        telemetry_ctx.clear_current_run()

    exited = [e for e in log.events if e[0] == "subprocess_exited"][0]
    assert exited[1]["tokens"] == {
        "input": 100, "output": 200, "cache_read": 50, "cache_write": 10,
    }
    assert exited[1]["cost_usd"] == 0.0123


def test_non_json_output_tokens_and_cost_null():
    """25e75663 adaptation: When the stream-json result event has no/empty usage,
    tokens and cost should be None in the subprocess_exited event."""
    log = _FakeEventLog()
    telemetry_ctx.set_current_run(event_log=log, run_id="r", step_name="s", phase="p")
    try:
        # Result event with no usage fields → tokens=None, cost=None
        stream_event = '{"type":"result","subtype":"success","result":"plain text response","usage":{},"total_cost_usd":null,"duration_ms":10}\n'
        with patch("bytedigger_engine.llm_subprocess.subprocess.Popen", return_value=_stream_proc(stream_event)):
            invoke_llm_subprocess(
                prompt="x",
                model="sonnet",
                timeout_sec=10,
                step_name="s",
                idle_timeout_sec=0,
                straggler_cfg=None,
            )
    finally:
        telemetry_ctx.clear_current_run()

    exited = [e for e in log.events if e[0] == "subprocess_exited"][0]
    assert exited[1]["tokens"] is None
    assert exited[1]["cost_usd"] is None


def test_stdout_stderr_tail_capped_at_2kb():
    log = _FakeEventLog()
    telemetry_ctx.set_current_run(event_log=log, run_id="r", step_name="s", phase="p")
    try:
        # Generate >2KB stdout in the stream-json result
        big = "X" * 5000
        stream_event = json.dumps({
            "type": "result",
            "subtype": "success",
            "result": big,
            "usage": {},
            "total_cost_usd": None,
            "duration_ms": 10,
        }) + "\n"
        proc = MagicMock()
        proc.pid = 12345
        proc.returncode = 0
        proc.stdout = io.StringIO(stream_event)
        # Large stderr to test 2KB cap
        proc.stderr = io.StringIO("Y" * 5000)
        proc.stdin = MagicMock()
        proc.wait = MagicMock(return_value=0)
        proc.communicate = MagicMock(return_value=(stream_event, "Y" * 5000))
        with patch("bytedigger_engine.llm_subprocess.subprocess.Popen", return_value=proc):
            invoke_llm_subprocess(
                prompt="x",
                model="sonnet",
                timeout_sec=10,
                step_name="s",
                idle_timeout_sec=0,
                straggler_cfg=None,
            )
    finally:
        telemetry_ctx.clear_current_run()

    exited = [e for e in log.events if e[0] == "subprocess_exited"][0]
    # stdout_tail is capped at 2KB
    assert len(exited[1]["stdout_tail"]) <= 2048
    # stderr_tail is capped at 2KB
    assert len(exited[1]["stderr_tail"]) <= 2048


def test_telemetry_emits_even_on_nonzero_exit():
    log = _FakeEventLog()
    telemetry_ctx.set_current_run(event_log=log, run_id="r", step_name="s", phase="p")
    try:
        proc = MagicMock()
        proc.pid = 12345
        proc.returncode = 2
        proc.stdout = io.StringIO("")  # no result event → E_LLM_NO_RESULT_EVENT or E_LLM_EXIT
        proc.stderr = io.StringIO("fail")
        proc.stdin = MagicMock()
        proc.wait = MagicMock(return_value=2)
        proc.communicate = MagicMock(return_value=("", "fail"))
        with patch("bytedigger_engine.llm_subprocess.subprocess.Popen", return_value=proc):
            invoke_llm_subprocess(
                prompt="x",
                model="sonnet",
                timeout_sec=10,
                step_name="s",
                idle_timeout_sec=0,
                straggler_cfg=None,
            )
    finally:
        telemetry_ctx.clear_current_run()

    spawned = [e for e in log.events if e[0] == "subprocess_spawned"]
    exited = [e for e in log.events if e[0] == "subprocess_exited"]
    assert len(spawned) == 1
    assert len(exited) == 1
    # exit_code must be 2 (nonzero)
    assert exited[0][1]["exit_code"] == 2
    assert "fail" in exited[0][1]["stderr_tail"]


def test_telemetry_failure_does_not_break_subprocess():
    """If event_log raises, invoke_llm_subprocess still returns normal result."""
    class Broken:
        def append(self, *a, **kw):
            raise RuntimeError("disk full")

    telemetry_ctx.set_current_run(event_log=Broken(), run_id="r", step_name="s", phase="p")
    try:
        with patch("bytedigger_engine.llm_subprocess.subprocess.Popen", return_value=_stream_proc(_RESULT_EVENT_OK)):
            r = invoke_llm_subprocess(
                prompt="x",
                model="sonnet",
                timeout_sec=10,
                step_name="s",
                idle_timeout_sec=0,
                straggler_cfg=None,
            )
    finally:
        telemetry_ctx.clear_current_run()
    assert r.status == "ok"


def test_subprocess_spawned_includes_parent_skill_from_env(monkeypatch):
    monkeypatch.setenv("HAL_PARENT_SKILL", "forge")
    log = _FakeEventLog()
    telemetry_ctx.set_current_run(event_log=log, run_id="r", step_name="s", phase="p")
    try:
        with patch("bytedigger_engine.llm_subprocess.subprocess.Popen", return_value=_stream_proc(_RESULT_EVENT_OK)):
            invoke_llm_subprocess(
                prompt="hi",
                model="sonnet",
                timeout_sec=10,
                step_name="s",
                idle_timeout_sec=0,
                straggler_cfg=None,
            )
    finally:
        telemetry_ctx.clear_current_run()

    spawned = [e for e in log.events if e[0] == "subprocess_spawned"]
    assert len(spawned) == 1
    assert spawned[0][1]["parent_skill"] == "forge"


def test_subprocess_spawned_parent_skill_none_when_env_unset(monkeypatch):
    monkeypatch.delenv("HAL_PARENT_SKILL", raising=False)
    log = _FakeEventLog()
    telemetry_ctx.set_current_run(event_log=log, run_id="r", step_name="s", phase="p")
    try:
        with patch("bytedigger_engine.llm_subprocess.subprocess.Popen", return_value=_stream_proc(_RESULT_EVENT_OK)):
            invoke_llm_subprocess(
                prompt="hi",
                model="sonnet",
                timeout_sec=10,
                step_name="s",
                idle_timeout_sec=0,
                straggler_cfg=None,
            )
    finally:
        telemetry_ctx.clear_current_run()

    spawned = [e for e in log.events if e[0] == "subprocess_spawned"]
    assert len(spawned) == 1
    assert spawned[0][1]["parent_skill"] is None
