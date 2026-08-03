"""Tests for E6F86B73: raw_response must be extracted JSON ``result`` field
when --output-format is auto-injected by invoke_llm_subprocess.

Migration note (2026-05-06, agreement 23680DDA): auto-injection now uses
``--output-format stream-json --verbose`` (not bare ``json``). Under
stream-json:

  - Tests 1 + 2 are PRESERVED as regression guards. Test 1's stdout fixture
    is a single-line ``{"type":"result","result":"..."}`` envelope, which is
    a valid stream-json result event — the extraction works the same way.
    Test 2 covers the legacy non-streaming path (caller-supplied
    ``--output-format json``) and is unchanged.

  - Tests 3 + 4 are REPLACED. Under the OLD contract a parse-fail or
    missing-result-field silently fell back to ``raw_response = raw_stdout``
    with ``status="ok"`` — the silent-success bug class explicitly cited in
    the 2026-05-06 W1 post-mortem. Under the NEW contract a stream-json call
    that produces no usable ``type=result`` event yields
    ``status="error"`` + ``error_code="E_LLM_NO_RESULT_EVENT"``. Tests 3 + 4
    have been UPDATED (rather than deleted) to pin the new error contract on
    these stdout shapes — they remain useful as fixture-shape regression
    guards but assert error semantics now. See
    ``tests/test_llm_subprocess_23680DDA.py`` Test 7 for the canonical pin
    of the no-result-event contract; these two tests cover the stdout
    shapes the previous E6F86B73 tests pinned.

Mirrors style/conventions of tests/test_llm_subprocess_91AF3612.py.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

from bytedigger_engine.llm_subprocess import invoke_llm_subprocess  # noqa: E402


def _stream_proc(stdout="response", returncode=0):
    """Build a mock Popen process whose stdout iterates the given stream-json
    text line-by-line. Mirrors test_llm_subprocess_23680DDA._stream_proc so
    auto-inject paths exercise the streaming reader, not the legacy
    ``communicate``-shaped fixtures.
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


def _mock_proc(stdout="response", returncode=0):
    """Legacy ``communicate``-shaped mock — kept for the caller-supplied
    --output-format path (Test 2), which still uses the single-shot
    communicate semantics by design."""
    proc = MagicMock()
    proc.pid = 12345
    proc.returncode = returncode
    proc.communicate.return_value = (stdout, "")
    return proc


# ── Test 1: auto-injection → raw_response is extracted "result" string ───────


def test_e6f86b73_raw_response_is_extracted_result_when_output_format_auto_injected():
    """Auto-injected --output-format stream-json + parseable result field →
    raw_response must equal the extracted result string (not the envelope).

    Under 23680DDA, the single-line ``{"type":"result",...}`` envelope from
    the legacy fixture is also a valid stream-json result event (one event
    per line, this stream just happens to have one event), so the extraction
    path produces the same answer.

    Hardening pass 2026-05-06 evening: fixture extended with explicit
    ``"subtype":"success"`` because MED-3 now requires the field (no default
    fallback; absent subtype → E_LLM_RESULT_MALFORMED).

    Migration (25e75663): R1 — command=["claude","-p","--model","sonnet"] -> model="sonnet".
    """
    # R1: command=["claude","-p","--model","sonnet"] -> model="sonnet"
    stdout = '{"type":"result","subtype":"success","result":"## Hello\\nworld","usage":{}}\n'
    with patch("bytedigger_engine.llm_subprocess.subprocess.Popen") as mock_popen:
        mock_popen.return_value = _stream_proc(stdout=stdout)
        result = invoke_llm_subprocess(
            prompt="x", model="sonnet", timeout_sec=10, step_name="s"
        )
    assert result.data["raw_response"] == "## Hello\nworld", (
        "raw_response must equal extracted result event's 'result' field, got "
        + repr(result.data["raw_response"])
    )


# ── Test 2: caller-supplied --output-format → raw_response is raw stdout ─────


def test_e6f86b73_raw_response_is_extracted_not_envelope_multiline_stream():
    """E6F86B73 invariant on a multi-event stream: raw_response must equal the
    extracted ``result`` field from the type=result event, NOT the full JSON
    envelope and NOT the raw concatenated stdout.

    Migration note (25e75663): The original Test 2 tested the caller-supplied
    --output-format path, which no longer exists in invoke_llm_subprocess
    (model= seam always auto-injects stream-json). Adapted to test the same
    E6F86B73 invariant on a multi-event stream — a shape that previously
    would have confused the last-line parser and could have returned the
    full envelope. Preserves the spirit: raw_response is always the clean
    result string, regardless of stream shape.

    R1: model="sonnet" (no command= parameter in new seam).
    """
    # Multi-event stream: system-init + assistant-delta + result.
    # The invariant: raw_response must be the "result" field ("## Hello\nworld"),
    # not the JSON envelope, not the system-init line, not concatenated.
    stdout = (
        '{"type":"system","subtype":"init","session_id":"s1"}\n'
        '{"type":"assistant","message":{"content":[{"type":"text","text":"Hi "}],'
        '"usage":{"input_tokens":0,"output_tokens":0}}}\n'
        '{"type":"result","subtype":"success","result":"## Hello\\nworld",'
        '"usage":{"input_tokens":5,"output_tokens":10,'
        '"cache_read_input_tokens":0,"cache_creation_input_tokens":0},'
        '"total_cost_usd":0.001}\n'
    )
    with patch("bytedigger_engine.llm_subprocess.subprocess.Popen") as mock_popen:
        mock_popen.return_value = _stream_proc(stdout=stdout)
        result = invoke_llm_subprocess(
            prompt="x", model="sonnet", timeout_sec=10, step_name="s"
        )
    assert result.status == "ok", (
        "multi-event stream with result event must succeed; got status="
        + repr(result.status) + " err=" + repr(result.error)
    )
    assert result.data["raw_response"] == "## Hello\nworld", (
        "E6F86B73 invariant: raw_response must be the clean extracted "
        "'result' field from the type=result event — NOT the JSON envelope, "
        "NOT the full multi-line stdout. got: "
        + repr(result.data["raw_response"])
    )


# ── Test 3: parse fails (stream-json) → status=error E_LLM_NO_RESULT_EVENT ──


def test_e6f86b73_raw_response_errors_when_parse_fails():
    """23680DDA replaces the old silent-fallback contract: under stream-json,
    stdout that contains no parseable ``type=result`` event yields
    ``status=error`` + ``error_code=E_LLM_NO_RESULT_EVENT``. The previous
    contract (silent ``status=ok`` with raw stdout in raw_response) is
    explicitly retired — see W1 post-mortem 2026-05-06 silent-success bug
    class.

    Migration (25e75663): R1 — command=["claude","-p"] -> model="sonnet".
    """
    # R1: command=["claude","-p"] -> model="sonnet"
    stdout = "not valid json on the last line\n"
    with patch("bytedigger_engine.llm_subprocess.subprocess.Popen") as mock_popen:
        mock_popen.return_value = _stream_proc(stdout=stdout)
        result = invoke_llm_subprocess(
            prompt="x", model="sonnet", timeout_sec=10, step_name="s"
        )
    assert result.status == "error", (
        "unparseable stream must yield status=error under stream-json, got "
        + repr(result.status)
    )
    assert result.error_code == "E_LLM_NO_RESULT_EVENT", (
        "unparseable stream must yield E_LLM_NO_RESULT_EVENT, got "
        + repr(result.error_code)
    )


# ── Test 4: parsed JSON lacks "result" event → status=error ─────────────────


def test_e6f86b73_raw_response_errors_when_no_result_event():
    """23680DDA replaces the old silent-fallback contract: stream-json output
    that parses but has no ``type=result`` event (e.g. only error envelopes
    or only system/assistant deltas) yields ``status=error`` +
    ``error_code=E_LLM_NO_RESULT_EVENT``.

    Migration (25e75663): R1 — command=["claude","-p","--model","sonnet"] -> model="sonnet".
    """
    # R1: command=["claude","-p","--model","sonnet"] -> model="sonnet"
    # Valid JSON line but type="error", not "result" — stream-json walker
    # never finds a result event.
    stdout = '{"type":"error","error":"oops"}\n'
    with patch("bytedigger_engine.llm_subprocess.subprocess.Popen") as mock_popen:
        mock_popen.return_value = _stream_proc(stdout=stdout)
        result = invoke_llm_subprocess(
            prompt="x", model="sonnet", timeout_sec=10, step_name="s"
        )
    assert result.status == "error", (
        "stream without type=result event must yield status=error, got "
        + repr(result.status)
    )
    assert result.error_code == "E_LLM_NO_RESULT_EVENT", (
        "stream without type=result event must yield E_LLM_NO_RESULT_EVENT, got "
        + repr(result.error_code)
    )
