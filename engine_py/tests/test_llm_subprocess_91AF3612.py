"""Tests for 91AF3612: subprocess token+cost capture fix.

Migration update (23680DDA, 2026-05-06): auto-injection now emits
``--output-format stream-json --verbose`` instead of bare ``json``. The AC1
trio (popen argv, data['command'], spawned event) has been UPDATED to assert
the new injected shape — the pre-23680DDA assertion of
``--output-format json`` directly contradicts the new contract pinned by
tests/test_llm_subprocess_23680DDA.py::test_auto_injects_stream_json_not_plain_json.

25e75663 migration: command= seam replaced by model=str. Tests that used
command=["claude","-p",...] now use model="sonnet". Tests that tested
caller-supplied --output-format flags or shell-kind commands via
invoke_llm_subprocess are dropped (Class C: those paths no longer reachable
via model= seam — injection is unconditional for claude_p path).

Lower-level helper tests (_parse_claude_json AC3 / AC4 family) are
unchanged: those helpers continue to support the legacy non-stream-json
path (caller-supplied ``--output-format json``) and the stream-json path
uses _tokens_and_cost_from_events directly.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

from llm_subprocess import (  # noqa: E402
    _assert_hard_gate_opus,
    _classify_cmd_kind,
    _is_opus_model,
    _parse_claude_json,
    invoke_llm_subprocess,
    register_backend,
    reset_backends,
)
import llm_subprocess  # noqa: E402
import telemetry_ctx  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_backends_and_telemetry(monkeypatch):
    monkeypatch.setattr(llm_subprocess, "emit_resolver_resolved", lambda *a, **kw: None)
    telemetry_ctx.clear_current_run()
    yield
    telemetry_ctx.clear_current_run()
    reset_backends()


class _FakeEventLog:
    def __init__(self):
        self.events: list[tuple[str, dict, str]] = []

    def append(self, event_type: str, payload: dict, run_id: str | None = None) -> None:
        self.events.append((event_type, payload, run_id or "ad-hoc"))


def _mock_proc(stdout="response", returncode=0):
    """Legacy ``communicate``-shaped mock."""
    proc = MagicMock()
    proc.pid = 12345
    proc.returncode = returncode
    proc.communicate.return_value = (stdout, "")
    return proc


def _stream_proc(stdout="response", returncode=0):
    """Stream-json mock — used for auto-injected claude_p paths so the
    streaming reader iterates a real StringIO line-by-line."""
    proc = MagicMock()
    proc.pid = 12345
    proc.returncode = returncode
    proc.stdout = io.StringIO(stdout)
    proc.stderr = io.StringIO("")
    proc.stdin = MagicMock()
    proc.wait = MagicMock(return_value=returncode)
    proc.communicate = MagicMock(return_value=(stdout, ""))
    return proc


# -- AC1 . argv injection ------------------------------------------------------

def test_ac1_popen_argv_gets_output_format_injected():
    """AC1 (updated 23680DDA + 25e75663): Popen must be called with
    --output-format stream-json --verbose appended for claude commands.
    25e75663: command= replaced by model=; argv built via _build_claude_argv."""
    with patch("llm_subprocess.subprocess.Popen") as mock_popen:
        mock_popen.return_value = _stream_proc()
        invoke_llm_subprocess(
            prompt="x",
            model="sonnet",
            timeout_sec=10,
            step_name="s",
            idle_timeout_sec=0,
            straggler_cfg=None,
        )
    spawned = mock_popen.call_args.args[0]
    assert "--output-format" in spawned, (
        "expected --output-format in spawned argv, got " + repr(spawned)
    )
    idx = spawned.index("--output-format")
    assert idx + 1 < len(spawned) and spawned[idx + 1] == "stream-json", (
        "expected stream-json after --output-format, got " + repr(spawned[idx + 1:])
    )
    assert "--verbose" in spawned, (
        "stream-json requires --verbose to be auto-injected too, got "
        + repr(spawned)
    )


def test_ac1_step_result_command_uses_augmented_argv():
    """Behavior: StepResult.data['command'] must reflect the augmented argv.
    Updated 23680DDA: the augmented value is --output-format stream-json --verbose.
    25e75663: command= replaced by model=."""
    with patch("llm_subprocess.subprocess.Popen") as mock_popen:
        mock_popen.return_value = _stream_proc(stdout=(
            '{"type":"result","subtype":"success","result":"ok","usage":{},"total_cost_usd":0}\n'
        ))
        result = invoke_llm_subprocess(
            prompt="x",
            model="sonnet",
            timeout_sec=10,
            step_name="s",
            idle_timeout_sec=0,
            straggler_cfg=None,
        )
    data_cmd = result.data["command"]
    assert "--output-format" in data_cmd, (
        "StepResult.data['command'] must use the augmented list, got " + repr(data_cmd)
    )
    idx = data_cmd.index("--output-format")
    assert idx + 1 < len(data_cmd) and data_cmd[idx + 1] == "stream-json", (
        "expected 'stream-json' after --output-format in StepResult.data['command'], got "
        + repr(data_cmd[idx + 1:])
    )
    assert "--verbose" in data_cmd, (
        "stream-json requires --verbose; StepResult.data['command'] must include it"
    )


def test_ac1_spawned_event_cmd_uses_augmented_argv():
    """Behavior: subprocess_spawned must reflect auto-injected --output-format.
    Updated for W1/AC1: event uses cmd_tail (list[str]) + output_format (str|None).
    Updated 23680DDA: the auto-injected output_format is 'stream-json'.
    25e75663: command= replaced by model=."""
    log = _FakeEventLog()
    telemetry_ctx.set_current_run(event_log=log, run_id="r1", step_name="s", phase="p")
    try:
        with patch("llm_subprocess.subprocess.Popen") as mock_popen:
            mock_popen.return_value = _stream_proc()
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
    assert len(spawned) == 1, "expected one subprocess_spawned event"
    payload = spawned[0][1]
    assert "cmd" not in payload, "old full-cmd field must be absent (replaced by cmd_tail)"
    assert "cmd_tail" in payload, "subprocess_spawned must have cmd_tail field"
    assert isinstance(payload["cmd_tail"], list), "cmd_tail must be a list"
    assert payload.get("output_format") == "stream-json", (
        "output_format must be 'stream-json' when --output-format stream-json was "
        "auto-injected, got " + repr(payload.get("output_format"))
    )


# -- AC3 . last-line JSON parsing ----------------------------------------------

_AC3_STDOUT = (
    "some text\n"
    '{"usage":{"input_tokens":10,"output_tokens":5,'
    '"cache_read_input_tokens":0,"cache_creation_input_tokens":0},'
    '"total_cost_usd":0.001}'
)


def test_ac3_multiline_stdout_last_line_json_returns_tokens():
    """AC3: multi-line stdout with valid JSON on last line returns (tokens_dict, cost_float)."""
    cmd = ["--output-format", "json"]
    tokens, cost = _parse_claude_json(cmd, _AC3_STDOUT)
    assert cost == 0.001, "expected cost=0.001, got " + repr(cost)
    assert tokens is not None, "expected tokens dict, got None"
    assert tokens["input"] == 10, "expected input_tokens=10, got " + repr(tokens["input"])
    assert tokens["output"] == 5, "expected output_tokens=5, got " + repr(tokens["output"])
    assert tokens["cache_read"] == 0
    assert tokens["cache_write"] == 0


# -- AC4 . graceful degrade on unparseable last line ---------------------------

_PRETTY_JSON = (
    "{\n"
    '  "usage": {\n'
    '    "input_tokens": 10,\n'
    '    "output_tokens": 5,\n'
    '    "cache_read_input_tokens": 0,\n'
    '    "cache_creation_input_tokens": 0\n'
    "  },\n"
    '  "total_cost_usd": 0.001\n'
    "}"
)


def test_ac4_pretty_printed_json_last_line_not_standalone_json_returns_none():
    """AC4: pretty-printed JSON has } as its last line, not valid standalone JSON.

    Current code: json.loads(full_stdout) parses the whole pretty-printed object and
    returns (tokens, 0.001) -- non-None.  Fixed code: json.loads('}') raises
    JSONDecodeError, so _parse_claude_json returns (None, None).
    This test FAILS before the fix.
    """
    cmd = ["--output-format", "json"]
    tokens, cost = _parse_claude_json(cmd, _PRETTY_JSON)
    assert tokens is None, (
        "expected tokens=None for pretty-printed stdout (last line '}'), got " + repr(tokens)
    )
    assert cost is None, (
        "expected cost=None for pretty-printed stdout (last line '}'), got " + repr(cost)
    )


# -- AC4 new code-path coverage ------------------------------------------------

def test_ac4_empty_stdout_returns_none():
    """AC4: empty stdout after strip must return (None, None)."""
    cmd = ["--output-format", "json"]
    tokens, cost = _parse_claude_json(cmd, "")
    assert tokens is None and cost is None, "empty stdout must return (None, None)"


def test_ac4_whitespace_only_lines_returns_none():
    """AC4: stdout with only whitespace lines must return (None, None)."""
    cmd = ["--output-format", "json"]
    tokens, cost = _parse_claude_json(cmd, "   \n  \t  \n")
    assert tokens is None and cost is None, "whitespace-only stdout must return (None, None)"


def test_ac4_non_json_last_line_returns_none():
    """AC4: stdout whose last non-empty line is not JSON must return (None, None)."""
    cmd = ["--output-format", "json"]
    tokens, cost = _parse_claude_json(cmd, "some output\nnot json at all")
    assert tokens is None and cost is None, "non-JSON last line must return (None, None)"


def test_cost_non_numeric_string_type_guard_drops_cost():
    """MEDIUM: total_cost_usd as string must be dropped (type guard at line 239)."""
    cmd = ["--output-format", "json"]
    stdout = (
        '{"usage":{"input_tokens":10,"output_tokens":5,'
        '"cache_read_input_tokens":0,"cache_creation_input_tokens":0},'
        '"total_cost_usd":"0.001"}'
    )
    tokens, cost = _parse_claude_json(cmd, stdout)
    assert cost is None, (
        "string-typed total_cost_usd must be dropped by type guard, got " + repr(cost)
    )
    assert tokens is not None, "tokens should still parse even when cost is dropped"


# -- AC5 . integration (probe via /build) --------------------------------------

@pytest.mark.skip(
    reason=(
        "AC5 requires a live claude invocation. After the fix lands, run one /build "
        "phase and assert cost_usd > 0.0 in at least one subprocess_exited event in "
        "SHARED/memory/State/subagent-telemetry.jsonl. "
        "Also confirm during this probe whether --output-format json emits a single-line "
        "JSON summary or a JSONL stream (Open Questions item in spec)."
    )
)
def test_ac5_subprocess_exited_cost_usd_nonzero_live():
    pass


# ─── _is_opus_model unit tests (MED #4 anchored match) ───────────────────────


@pytest.mark.parametrize("model, expected", [
    # Should pass (opus variants)
    ("opus", True),
    ("opus-3", True),
    ("claude-opus-4-5", True),
    ("claude-opus-4-7", True),
    ("claude-3-opus-20240229", True),
    ("anthropic/opus", True),
    ("claude-3-5-opus", True),
    # Should fail (non-opus or lookalikes)
    ("octopus-3", False),
    ("opus-stub-fake", True),   # starts with "opus-" → considered opus
    ("sonnet", False),
    ("haiku", False),
    ("claude-sonnet-4-5", False),
])
def test_is_opus_model_anchored_match(model, expected):
    """MED #4 — anchored match must accept real opus variants and reject lookalikes."""
    assert _is_opus_model(model) is expected, (
        f"_is_opus_model({model!r}) expected {expected}"
    )


def test_is_opus_model_rejects_octopus():
    """MED #4 invariant — 'octopus-3' must not pass as opus."""
    assert _is_opus_model("octopus-3") is False, (
        "octopus-3 must not be mistaken for opus"
    )


# ─── _assert_hard_gate_opus unit tests (CRITICAL #1 + HIGH #2) ───────────────


def test_hard_gate_refuses_claude_p_without_model():
    """CRITICAL #1 — claude binary without --model flag must be refused."""
    result = _assert_hard_gate_opus(["claude", "-p"], "step", "validation")
    assert result is not None, "gate must fire for claude -p without --model"
    assert result.error_code == "E_HARD_GATE_MODEL_DOWNGRADE"
    assert result.recoverable is False, "HIGH #2 — hard gate must be non-recoverable"
    assert "missing" in (result.error or "").lower() or "claude default" in (result.error or "").lower()


def test_hard_gate_refuses_explicit_non_opus():
    """claude binary with explicit non-opus model must be refused."""
    result = _assert_hard_gate_opus(["claude", "-p", "--model", "sonnet"], "step", "validation")
    assert result is not None
    assert result.error_code == "E_HARD_GATE_MODEL_DOWNGRADE"
    assert result.recoverable is False


def test_hard_gate_passes_claude_p_with_opus():
    """claude binary with explicit opus model must pass."""
    result = _assert_hard_gate_opus(["claude", "-p", "--model", "claude-opus-4-7"], "step", "validation")
    assert result is None, "opus command must not be refused"


def test_hard_gate_passes_test_stub_without_model():
    """LOW invariant — python3 test stubs without --model must pass gate.

    Stubs use python3 as command[0]; _classify_cmd_kind returns 'shell'.
    Gate must not refuse them so the test suite can exercise downstream steps.
    """
    stub = ["python3", "-c", "import sys; sys.stdout.write('ok')"]
    result = _assert_hard_gate_opus(stub, "step", "validation")
    assert result is None, "python3 stub without --model must pass gate"


def test_hard_gate_refuses_octopus_lookalike():
    """MED #4 — octopus-3 model must be refused (not a valid opus variant)."""
    result = _assert_hard_gate_opus(
        ["python3", "-c", "pass", "--model", "octopus-3"],
        "step",
        "validation",
    )
    assert result is not None, "octopus-3 must be rejected"
    assert result.error_code == "E_HARD_GATE_MODEL_DOWNGRADE"


def test_hard_gate_recoverable_false():
    """HIGH #2 — gate StepResult must always set recoverable=False."""
    result = _assert_hard_gate_opus(["claude", "-p"], "step", "gate")
    assert result is not None
    assert result.recoverable is False, (
        f"StepResult.recoverable must be False, got {result.recoverable!r}"
    )
