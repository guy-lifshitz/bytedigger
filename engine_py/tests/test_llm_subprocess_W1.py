"""Tests for Wave 1 llm_subprocess.py changes.

Three concerns batched:
  #8  (HIGH-7)  — cmd payload trim: subprocess_spawned uses cmd_tail + output_format
  D671D9C4 (HIGH) — flat cost/tokens propagation in subprocess_exited
  7669CD6E (MED) — E6F86B73 hardening (docstring invariants + result-event regression)

Migration update (23680DDA, 2026-05-06): auto-injection now emits
``--output-format stream-json --verbose`` instead of bare ``json`` so the
``output_format`` field on the spawned event is ``"stream-json"`` (Test
ac2 Case A). Tests ac3/ac4 now use a stream-json fixture (StringIO over a
single-line type=result event, which is also a valid 1-event stream).

25e75663 migration: command= seam replaced by model=str. Tests that used
command=["claude","-p",...] now use model="sonnet"/"opus". Tests that
depended on caller-supplied --output-format flags or shell-kind commands
(Cases B/C/D of test_ac2) are adapted to reflect that auto-inject is now
unconditional for all model= invocations (Class C: caller-supplied paths
no longer reachable via invoke_llm_subprocess(model=)).
"""
from __future__ import annotations

import io
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

from bytedigger_engine.llm_subprocess import (  # noqa: E402
    _parse_claude_json,
    _extract_result_text,
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


# ─── Helpers ──────────────────────────────────────────────────────────────────

class _FakeEventLog:
    def __init__(self):
        self.events: list[tuple[str, dict, str]] = []

    def append(self, event_type: str, payload: dict, run_id: str | None = None) -> None:
        self.events.append((event_type, payload, run_id or "ad-hoc"))


def _mock_proc(stdout: str = "response", returncode: int = 0) -> MagicMock:
    """Legacy ``communicate``-shaped mock."""
    proc = MagicMock()
    proc.pid = 12345
    proc.returncode = returncode
    proc.communicate.return_value = (stdout, "")
    return proc


def _stream_proc(stdout: str = "response", returncode: int = 0) -> MagicMock:
    """Stream-json mock — used for auto-injected claude_p paths so the
    streaming reader iterates a real StringIO line-by-line.
    """
    proc = MagicMock()
    proc.pid = 12345
    proc.returncode = returncode
    proc.stdout = io.StringIO(stdout)
    proc.stderr = io.StringIO("")
    proc.stdin = MagicMock()
    proc.wait = MagicMock(return_value=returncode)
    proc.communicate = MagicMock(return_value=(stdout, ""))
    return proc


_USAGE_STDOUT = (
    '{"type":"result","result":"ok","usage":{"input_tokens":10,"output_tokens":5,'
    '"cache_read_input_tokens":2,"cache_creation_input_tokens":1},"total_cost_usd":0.002}\n'
)


# ─── AC1: subprocess_spawned uses cmd_tail (not cmd) + len ≤ 3 ───────────────

def test_ac1_spawned_event_has_cmd_tail_not_cmd():
    """#8 AC1: subprocess_spawned payload must have 'cmd_tail' key (list, len ≤ 3)
    and must NOT have the old 'cmd' key (full argv removed).
    25e75663: command= replaced by model=; argv built by _build_claude_argv."""
    log = _FakeEventLog()
    telemetry_ctx.set_current_run(event_log=log, run_id="r1", step_name="s", phase="p")
    try:
        with patch("bytedigger_engine.llm_subprocess.subprocess.Popen") as mock_popen:
            mock_popen.return_value = _mock_proc()
            invoke_llm_subprocess(
                prompt="x",
                model="claude-opus-4-7",
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

    # cmd_tail must be present and be a list of ≤ 3 items
    assert "cmd_tail" in payload, (
        "subprocess_spawned payload must have 'cmd_tail' key, got: " + repr(list(payload.keys()))
    )
    assert isinstance(payload["cmd_tail"], list), (
        "cmd_tail must be a list, got: " + repr(type(payload["cmd_tail"]))
    )
    assert len(payload["cmd_tail"]) <= 3, (
        "cmd_tail must have len ≤ 3, got: " + repr(payload["cmd_tail"])
    )

    # full cmd must be gone
    assert "cmd" not in payload, (
        "subprocess_spawned payload must NOT have 'cmd' key (full argv removed), "
        "got keys: " + repr(list(payload.keys()))
    )


# ─── AC2: subprocess_spawned has output_format field ─────────────────────────

def test_ac2_spawned_event_has_output_format_json_when_auto_injected():
    """#8 AC2: subprocess_spawned must include output_format reflecting the
    actually-injected value.

    25e75663 migration: invoke_llm_subprocess now takes model= only. Auto-inject
    is unconditional for all model= invocations (always stream-json). The Cases
    B/C/D (caller-supplied flags, shell commands) no longer apply via this seam.

    Case A: auto-injected (model=, no caller --output-format)
    → output_format = "stream-json" (was "json" pre-23680DDA).
    """
    log = _FakeEventLog()

    # Case A: auto-injected via model= → output_format = "stream-json"
    telemetry_ctx.set_current_run(event_log=log, run_id="r1", step_name="s", phase="p")
    try:
        with patch("bytedigger_engine.llm_subprocess.subprocess.Popen") as mock_popen:
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

    spawned_a = [e for e in log.events if e[0] == "subprocess_spawned"]
    assert len(spawned_a) == 1
    payload_a = spawned_a[0][1]
    assert "output_format" in payload_a, (
        "subprocess_spawned must have 'output_format' key when auto-injected, "
        "got keys: " + repr(list(payload_a.keys()))
    )
    assert payload_a["output_format"] == "stream-json", (
        "23680DDA: output_format must be 'stream-json' when "
        "--output-format stream-json was auto-injected, got: "
        + repr(payload_a["output_format"])
    )


# ─── AC3: subprocess_exited has flat tokens_in / tokens_out ──────────────────

def test_ac3_exited_event_has_flat_tokens_in_tokens_out():
    """D671D9C4 AC3: subprocess_exited payload must have flat tokens_in and
    tokens_out keys populated from the nested tokens dict.

    Under 23680DDA the auto-injected path is stream-json — _USAGE_STDOUT is
    a single-line type=result event which doubles as a valid 1-event
    stream, so the streaming reader extracts the same usage fields the
    legacy single-shot parser used to.

    25e75663: command= replaced by model=.
    """
    log = _FakeEventLog()
    telemetry_ctx.set_current_run(event_log=log, run_id="r1", step_name="s", phase="p")
    try:
        with patch("bytedigger_engine.llm_subprocess.subprocess.Popen") as mock_popen:
            mock_popen.return_value = _stream_proc(stdout=_USAGE_STDOUT)
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

    exited = [e for e in log.events if e[0] == "subprocess_exited"]
    assert len(exited) == 1, "expected one subprocess_exited event"
    payload = exited[0][1]

    assert "tokens_in" in payload, (
        "subprocess_exited must have 'tokens_in' key, got keys: " + repr(list(payload.keys()))
    )
    assert "tokens_out" in payload, (
        "subprocess_exited must have 'tokens_out' key, got keys: " + repr(list(payload.keys()))
    )
    assert payload["tokens_in"] == 10, (
        "tokens_in must equal tokens['input']=10, got: " + repr(payload["tokens_in"])
    )
    assert payload["tokens_out"] == 5, (
        "tokens_out must equal tokens['output']=5, got: " + repr(payload["tokens_out"])
    )

    # Backwards compat: nested tokens dict must still be present
    assert "tokens" in payload, (
        "subprocess_exited must still have nested 'tokens' dict for backwards compat"
    )


# ─── AC4: subprocess_exited cost_usd unchanged; also has tokens_in/out ────────

def test_ac4_exited_event_cost_usd_present_and_matches_parse():
    """D671D9C4 AC4: subprocess_exited must have cost_usd flat field matching
    the parsed cost from the result event, AND both flat token fields
    (tokens_in, tokens_out) must co-exist with cost_usd in the same payload
    (D671D9C4 ships all three flat fields together).

    25e75663: command= replaced by model=.
    """
    log = _FakeEventLog()
    telemetry_ctx.set_current_run(event_log=log, run_id="r1", step_name="s", phase="p")
    try:
        with patch("bytedigger_engine.llm_subprocess.subprocess.Popen") as mock_popen:
            mock_popen.return_value = _stream_proc(stdout=_USAGE_STDOUT)
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

    exited = [e for e in log.events if e[0] == "subprocess_exited"]
    assert len(exited) == 1
    payload = exited[0][1]

    assert "cost_usd" in payload, (
        "subprocess_exited must have 'cost_usd' key, got keys: " + repr(list(payload.keys()))
    )

    # Verify against _parse_claude_json directly — the flat field must match
    # what the parser returns (so the event is accurate)
    effective_cmd = ["claude", "-p", "--output-format", "json"]
    _, expected_cost = _parse_claude_json(effective_cmd, _USAGE_STDOUT)
    assert expected_cost == 0.002, (
        "sanity: _parse_claude_json must return cost=0.002 from test fixture, "
        "got: " + repr(expected_cost)
    )
    assert payload["cost_usd"] == expected_cost, (
        "subprocess_exited cost_usd must match _parse_claude_json second return "
        f"({expected_cost!r}), got: " + repr(payload["cost_usd"])
    )

    # D671D9C4 ships all three flat fields together: cost_usd + tokens_in + tokens_out
    assert "tokens_in" in payload and "tokens_out" in payload, (
        "D671D9C4: cost_usd, tokens_in, and tokens_out must all be present together "
        "in subprocess_exited payload (flat fields are a unit), "
        "got keys: " + repr(list(payload.keys()))
    )


# ─── AC5: invoke_llm_subprocess docstring has INVARIANT + E6F86B73 ────────────

def test_ac5_invoke_llm_subprocess_docstring_has_invariant_and_e6f86b73():
    """7669CD6E AC5: invoke_llm_subprocess docstring must contain the literal
    token 'INVARIANT' and the literal 'E6F86B73' referencing the auto-injection
    contract."""
    doc = invoke_llm_subprocess.__doc__ or ""
    assert "INVARIANT" in doc, (
        "invoke_llm_subprocess docstring must contain literal 'INVARIANT', "
        "got docstring: " + repr(doc[:300])
    )
    assert "E6F86B73" in doc, (
        "invoke_llm_subprocess docstring must contain literal 'E6F86B73', "
        "got docstring: " + repr(doc[:300])
    )


# ─── AC6: _parse_claude_json and _extract_result_text docstrings cross-ref each other ───

def test_ac6_helper_docstrings_cross_reference_each_other():
    """7669CD6E AC6: both _parse_claude_json and _extract_result_text docstrings
    must contain the literal token 'MUST_UPDATE_BOTH' (or equivalent) and
    reference the other function by name."""
    parse_doc = _parse_claude_json.__doc__ or ""
    extract_doc = _extract_result_text.__doc__ or ""

    # Both must contain the shared sentinel
    assert "MUST_UPDATE_BOTH" in parse_doc, (
        "_parse_claude_json docstring must contain 'MUST_UPDATE_BOTH', "
        "got: " + repr(parse_doc[:300])
    )
    assert "MUST_UPDATE_BOTH" in extract_doc, (
        "_extract_result_text docstring must contain 'MUST_UPDATE_BOTH', "
        "got: " + repr(extract_doc[:300])
    )

    # Each must name the other function
    assert "_extract_result_text" in parse_doc, (
        "_parse_claude_json docstring must reference '_extract_result_text', "
        "got: " + repr(parse_doc[:300])
    )
    assert "_parse_claude_json" in extract_doc, (
        "_extract_result_text docstring must reference '_parse_claude_json', "
        "got: " + repr(extract_doc[:300])
    )


# ─── AC7 / AC7b — REMOVED (23680DDA stream-json migration, 2026-05-06) ───────
#
# Both tests previously pinned a silent ``status=ok + raw_response=raw_stdout``
# fallback under auto-injected --output-format json when the stdout failed to
# parse / lacked a result key. Under stream-json that fallback IS the
# silent-success bug class explicitly cited in the W1 post-mortem 2026-05-06,
# and the new contract (status=error + error_code=E_LLM_NO_RESULT_EVENT)
# DIRECTLY contradicts the old assertions. Per
# feedback_no_half_built_no_broken_parked: fix-or-delete same day, no parked
# broken tests.
#
# New contract is covered by:
#   - tests/test_llm_subprocess_23680DDA.py::test_no_result_event_treated_as_subprocess_failure
#     (canonical pin of E_LLM_NO_RESULT_EVENT)
#   - tests/test_llm_subprocess_E6F86B73.py::test_e6f86b73_raw_response_errors_when_parse_fails
#     (parse-fail stdout shape from old AC7)
#   - tests/test_llm_subprocess_E6F86B73.py::test_e6f86b73_raw_response_errors_when_no_result_event
#     (missing-result-key stdout shape from old AC7b)
