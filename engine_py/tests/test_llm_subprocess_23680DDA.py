"""RED tests for 23680DDA stream-json migration.

See SHARED/memory/Decisions/2026-05-06_W1_resume_postmortem.md for context.

Background:
  On 2026-05-06 a Sonnet ``claude -p --output-format json`` subprocess hung
  empty for 15min then SIGKILL. Cheap-repro confirmed root cause: ``--output-
  format json`` is NON-streaming (claude CLI buffers entire response into a
  single JSON envelope before flushing); combined with ``proc.communicate(...)``
  at llm_subprocess.py:147 (which blocks until EOF), no progress is visible
  until the model finishes. Worker was working legitimately but appeared dead.

Migration plan (these tests pin the FUTURE shape — currently RED):
  1. Auto-injected flag changes from ``--output-format json`` to
     ``--output-format stream-json --verbose`` (CLI requires --verbose with
     stream-json). Manual --output-format callers still respected (no
     override) — preserves test_does_not_override_caller_output_format.
  2. ``proc.communicate(input=prompt, timeout=timeout_sec)`` (line 147)
     replaced by streaming-read loop: feed stdin, then iterate
     ``proc.stdout`` line-by-line. Each line is one NDJSON event; the LAST
     ``type:"result"`` event carries the full result + usage + cost (same
     fields as today's single-JSON output).
  3. ``_extract_result_text`` and ``_parse_claude_json`` continue to operate
     on the LAST ``type:"result"`` event (shape-compatible — they just need
     to find that event among the streamed lines instead of parsing the
     last non-empty line of a single-shot JSON).
  4. INVARIANT E6F86B73 preserved: ``StepResult.data["raw_response"]`` is
     the extracted ``result`` string (clean text), not the full envelope.
  5. Caller contract preserved: phases still read ``prev.data["raw_response"]``;
     no signature changes.

Test inventory (RED — every test must FAIL against current code):
  1. test_auto_injects_stream_json_not_plain_json
     Auto-injection emits stream-json+--verbose, NOT bare ``json``.
     Pins llm_subprocess.py:97-102.
  2. test_does_not_override_caller_output_format
     Regression guard: caller-supplied --output-format text is preserved.
     (Currently passes — kept as a green-phase regression guard. Asserted
     stricter than today: argv equality, not just substring.)
  3. test_streaming_read_loop_used_not_communicate
     ``proc.communicate(...)`` MUST NOT be called for stream-json flow;
     ``proc.stdout`` is iterated. Pins llm_subprocess.py:147.
  4. test_raw_response_is_extracted_result_text_from_last_event
     Three NDJSON events, last is type=result; raw_response must equal
     ``result["result"]`` (not envelope, not assistant deltas concatenated).
     Preserves E6F86B73.
  5. test_cost_and_tokens_extracted_from_final_result_event
     subprocess_exited event payload has cost_usd / tokens_in / tokens_out
     drawn from the final type=result event's total_cost_usd + usage.
  6. test_result_event_with_error_subtype_returns_error_status
     Final event type=result + subtype=error_max_turns → StepResult.status
     == "error" with error_code set (defines error semantics under
     stream-json). Spec-ambiguity flag: see module docstring §AMBIGUITIES.
  7. test_no_result_event_treated_as_subprocess_failure
     Stream ends without any type=result event → status=error, error_code
     set. Must NOT silently emit raw_response="" with status="ok".
  8. test_invalid_json_line_logged_but_does_not_kill_stream
     Inject one malformed NDJSON line between valid events; parser skips
     bad line and still extracts the final result event.
  9. test_telemetry_subprocess_spawned_emits_output_format_stream_json
     subprocess_spawned event payload's output_format == "stream-json"
     (not "json") when auto-injected.

§ AMBIGUITIES flagged for Opus validate gate:
  - Test 6 error_code: spec doesn't pin which code to return on
    ``type:result + subtype:error_*``. Test asserts ``error_code is not
    None`` and not ``E_LLM_EXIT`` (since exit_code=0). Implementer should
    pick a code (suggest ``E_LLM_RESULT_ERROR`` mirroring the subtype).
  - Test 7 error_code: test asserts ``error_code is not None``. Suggest
    ``E_NO_RESULT_EVENT`` for the no-final-result case.
  - Test 8: spec doesn't pin whether warnings are logged via logger.warning
    or logger.debug — test only asserts the stream survives and final
    result is extracted.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

from llm_subprocess import invoke_llm_subprocess  # noqa: E402
import telemetry_ctx  # noqa: E402


# ─── Helpers ──────────────────────────────────────────────────────────────────


class _FakeEventLog:
    """Captures (event_type, payload, run_id) tuples for assertion."""

    def __init__(self):
        self.events: list[tuple[str, dict, str]] = []

    def append(self, event_type: str, payload: dict, run_id: str | None = None) -> None:
        self.events.append((event_type, payload, run_id or "ad-hoc"))


def _ndjson_lines(*events: str) -> str:
    """Join NDJSON event lines into a single stdout string with trailing \\n on each."""
    return "".join(line if line.endswith("\n") else line + "\n" for line in events)


def _stream_proc(stdout_text: str, returncode: int = 0) -> MagicMock:
    """Build a mock Popen process whose stdout iterates NDJSON lines.

    Mirrors what a real streaming subprocess looks like:
      - ``proc.stdout`` is an io.StringIO so iteration yields lines.
      - ``proc.stdin`` is a MagicMock (for the stdin-feeder write/close).
      - ``proc.stderr`` is an io.StringIO("") (read after exit / on timeout).
      - ``proc.communicate`` is still a MagicMock — but tests assert it is
        NOT called under the stream-json contract.
      - ``proc.wait`` returns ``returncode`` so the polled-exit path works.
    """
    proc = MagicMock()
    proc.pid = 12345
    proc.returncode = returncode
    proc.stdout = io.StringIO(stdout_text)
    proc.stderr = io.StringIO("")
    proc.stdin = MagicMock()
    proc.wait = MagicMock(return_value=returncode)
    # communicate retained as a MagicMock so we can assert NOT called;
    # if implementer accidentally calls it, return a sentinel so any
    # downstream parse-of-tuple would visibly mis-shape.
    proc.communicate = MagicMock(return_value=(stdout_text, ""))
    return proc


# Canonical 3-event success stream used by raw_response + telemetry tests.
_SYSTEM_INIT = '{"type":"system","subtype":"init","session_id":"s1"}'
_ASSISTANT_DELTA = (
    '{"type":"assistant","message":{"content":[{"type":"text","text":"Hello "}],'
    '"usage":{"input_tokens":0,"output_tokens":0}}}'
)
_RESULT_OK = (
    '{"type":"result","subtype":"success",'
    '"result":"FINAL_ANSWER_TEXT",'
    '"usage":{"input_tokens":10,"output_tokens":20,'
    '"cache_read_input_tokens":0,"cache_creation_input_tokens":0},'
    '"total_cost_usd":0.0123,"duration_ms":500}'
)


# ─── Test 1: auto-injects stream-json (not bare json) ────────────────────────


def test_auto_injects_stream_json_not_plain_json():
    """Auto-injection must emit ``--output-format stream-json --verbose``.
    Pins llm_subprocess.py:97-102 — currently injects ``json``, RED until
    migration."""
    # R1: command=["claude","-p","--model","sonnet"] -> model="sonnet"
    stdout = _ndjson_lines(_SYSTEM_INIT, _ASSISTANT_DELTA, _RESULT_OK)
    captured_argv: list[list[str]] = []

    def _capture_popen(argv, **kwargs):
        captured_argv.append(list(argv))
        return _stream_proc(stdout)

    with patch("llm_subprocess.subprocess.Popen", side_effect=_capture_popen):
        invoke_llm_subprocess(
            prompt="x", model="sonnet", timeout_sec=10, step_name="s"
        )

    assert len(captured_argv) == 1, (
        "expected exactly one Popen call, got " + repr(captured_argv)
    )
    actual = captured_argv[0]
    # Must contain stream-json + --verbose (claude CLI requires --verbose
    # with stream-json).
    assert "stream-json" in actual, (
        "auto-injection must use --output-format stream-json, got argv: "
        + repr(actual)
    )
    # Locate the --output-format flag and verify its value is stream-json.
    assert "--output-format" in actual
    of_idx = actual.index("--output-format")
    assert of_idx + 1 < len(actual) and actual[of_idx + 1] == "stream-json", (
        "--output-format value must be 'stream-json' (not 'json'), got: "
        + repr(actual[of_idx : of_idx + 2])
    )
    # --verbose required by claude CLI when stream-json is set.
    assert "--verbose" in actual, (
        "stream-json requires --verbose to be auto-injected too, got argv: "
        + repr(actual)
    )
    # Must NOT contain bare --output-format json.
    json_positions = [
        i
        for i, tok in enumerate(actual)
        if tok == "json" and i > 0 and actual[i - 1] == "--output-format"
    ]
    assert not json_positions, (
        "auto-injection must NOT use --output-format json (the non-streaming "
        "shape that caused the 15min hang), got argv: " + repr(actual)
    )


# ─── Test 2: caller-supplied --output-format is NOT overridden ───────────────


def test_does_not_override_caller_output_format():
    """Regression guard: caller-supplied --output-format text → no auto-injection.
    Argv passed to Popen must equal caller's argv exactly.

    Currently passes against the existing ``json``-injection code (the
    `\"--output-format\" not in command` guard already short-circuits when
    caller supplies the flag). Kept as a green-phase regression guard so the
    stream-json migration can't accidentally break override-respect.

    Asserts argv-equality (stricter than substring) so a buggy implementer
    can't satisfy this by appending --verbose alongside the caller's flag.

    NOTE (25e75663): caller-supplied --output-format is no longer possible via
    invoke_llm_subprocess (model= seam only builds claude argv without --output-format).
    This test now verifies that with model=, auto-injection of stream-json occurs
    and there is no duplication. The original "caller wins exactly" contract
    for --output-format text was for the legacy command= path which is removed.
    We keep this test to verify the auto-injection shape is correct.
    """
    # R1: the new seam always uses model=; the "caller-supplied" path only applies
    # when a different output format is embedded in the argv, which cannot happen
    # via model=. We verify auto-injection fires normally.
    stdout = _ndjson_lines(_SYSTEM_INIT, _ASSISTANT_DELTA, _RESULT_OK)
    captured_argv: list[list[str]] = []

    def _capture_popen(argv, **kwargs):
        captured_argv.append(list(argv))
        return _stream_proc(stdout)

    with patch("llm_subprocess.subprocess.Popen", side_effect=_capture_popen):
        invoke_llm_subprocess(
            prompt="x", model="sonnet", timeout_sec=10, step_name="s"
        )

    assert len(captured_argv) == 1
    actual = captured_argv[0]
    # Auto-injection should produce stream-json (no duplicate flags)
    of_count = actual.count("--output-format")
    assert of_count == 1, (
        "Expected exactly 1 --output-format in auto-injected argv, got "
        + repr(of_count) + " in " + repr(actual)
    )
    of_idx = actual.index("--output-format")
    assert actual[of_idx + 1] == "stream-json", (
        "Auto-injected --output-format must be 'stream-json', got "
        + repr(actual[of_idx + 1])
    )


# ─── Test 3: streaming-read loop, NOT communicate() ──────────────────────────


def test_streaming_read_loop_used_not_communicate():
    """Stream-json flow MUST NOT call ``proc.communicate(...)`` (the blocking
    EOF-wait that caused the 15min hang at llm_subprocess.py:147). Instead it
    must iterate ``proc.stdout``. Pins line 147.

    Verifies the impl reads stdout via iteration / readline, NOT via
    proc.communicate. We track stdout reads by wrapping io.StringIO in a
    proxy that records reads.
    """
    # R1: command=["claude","-p"] -> model="sonnet"
    stdout_text = _ndjson_lines(_SYSTEM_INIT, _ASSISTANT_DELTA, _RESULT_OK)

    proc = _stream_proc(stdout_text)
    # Tightening (Opus addition): if impl falls back to communicate(), it must
    # visibly mis-extract — sentinel return value the result-extractor cannot
    # confuse with the real stream contents.
    proc.communicate = MagicMock(return_value=("CORRUPTED_NEVER_SEE_THIS", ""))
    # Track whether stdout was iterated/readlined.
    real_stdout = proc.stdout
    iter_calls = {"count": 0}
    readline_calls = {"count": 0}

    class _TrackingStdout:
        def __init__(self, wrapped):
            self._wrapped = wrapped

        def __iter__(self):
            iter_calls["count"] += 1
            return iter(self._wrapped)

        def readline(self):
            readline_calls["count"] += 1
            return self._wrapped.readline()

        def read(self, *a, **k):
            return self._wrapped.read(*a, **k)

        def close(self):
            return self._wrapped.close()

    proc.stdout = _TrackingStdout(real_stdout)

    with patch("llm_subprocess.subprocess.Popen", return_value=proc):
        invoke_llm_subprocess(
            prompt="x", model="sonnet", timeout_sec=10, step_name="s"
        )

    assert proc.communicate.called is False, (
        "stream-json migration MUST NOT call proc.communicate() (the blocking "
        "EOF-wait at llm_subprocess.py:147); use iterative stdout reads "
        "instead. communicate.call_count="
        + repr(proc.communicate.call_count)
    )
    assert proc.communicate.call_count == 0, (
        "explicit call-count assertion: proc.communicate must be called 0 times "
        "under stream-json. got: " + repr(proc.communicate.call_count)
    )
    # Tightening (Opus addition): assert iteration actually happened — at
    # least 2 readline / __iter__ calls — to prevent a buggy GREEN that
    # wraps communicate() in a no-op gate without actually streaming stdout.
    total_stream_reads = iter_calls["count"] + readline_calls["count"]
    assert total_stream_reads >= 2, (
        "stream-json migration must actually iterate proc.stdout incrementally "
        "(at least 2 read calls — __iter__ counts as 1 invocation, readline "
        "called per-line). got iter_calls="
        + repr(iter_calls["count"])
        + " readline_calls="
        + repr(readline_calls["count"])
    )
    streamed = iter_calls["count"] > 0 or readline_calls["count"] > 0
    assert streamed, (
        "stream-json migration must iterate proc.stdout (via __iter__ or "
        "readline); neither was called. iter_calls="
        + repr(iter_calls["count"])
        + " readline_calls="
        + repr(readline_calls["count"])
    )


# ─── Test 4: raw_response is the result string from final type=result event ──


def test_raw_response_is_extracted_result_text_from_last_event():
    """E6F86B73 preserved under stream-json: raw_response must equal the
    ``result`` field of the ``type:"result"`` NDJSON event — not the
    envelope, not concatenated assistant deltas, not the system-init line.

    Stream-json fixture: result event is followed by a trailing assistant
    delta line. Today's _extract_result_text reads only the LAST non-empty
    line of stdout — so the trailing delta breaks extraction (returns None →
    raw_response falls back to the full multi-line stdout). Under stream-json
    the parser must walk events and pick the type:result event regardless of
    position, then return its `result` field. This trailing-delta shape is
    what makes the test RED until the migration lands.
    """
    # R1: command=["claude","-p","--model","sonnet"] -> model="sonnet"
    # Trailing assistant delta AFTER the result event — forces a stream-aware
    # parser. Today's last-line parser would try to parse the assistant line
    # and miss the result.
    trailing_delta = (
        '{"type":"assistant","message":{"content":[{"type":"text","text":"."}],'
        '"usage":{"input_tokens":0,"output_tokens":0}}}'
    )
    stdout = _ndjson_lines(_SYSTEM_INIT, _ASSISTANT_DELTA, _RESULT_OK, trailing_delta)

    with patch("llm_subprocess.subprocess.Popen", return_value=_stream_proc(stdout)):
        result = invoke_llm_subprocess(
            prompt="x", model="sonnet", timeout_sec=10, step_name="s"
        )

    assert result.status == "ok", (
        "successful 3-event stream must yield status=ok, got " + repr(result.status)
    )
    assert result.data["raw_response"] == "FINAL_ANSWER_TEXT", (
        "raw_response must equal the final type:result event's `result` field "
        "(clean model text, NOT envelope, NOT assistant-delta concatenation). "
        "got: " + repr(result.data["raw_response"])
    )


# ─── Test 5: cost_usd + tokens extracted from final result event ─────────────


def test_cost_and_tokens_extracted_from_final_result_event():
    """subprocess_exited telemetry payload must carry cost_usd / tokens_in /
    tokens_out drawn from the type:"result" NDJSON event's total_cost_usd
    and usage object — found by walking events, NOT by parsing the last line.

    Fixture appends a trailing assistant-delta line AFTER the result event
    (per claude CLI's actual stream-json output, deltas can follow). Today's
    _parse_claude_json reads only the LAST non-empty line — would parse the
    delta and return None for cost (no total_cost_usd field), making this
    RED. Stream-json parser must walk all events and pick the result event.
    """
    # R1: command=["claude","-p"] -> model="sonnet"
    trailing_delta = (
        '{"type":"assistant","message":{"content":[{"type":"text","text":"."}],'
        '"usage":{"input_tokens":0,"output_tokens":0}}}'
    )
    stdout = _ndjson_lines(_SYSTEM_INIT, _ASSISTANT_DELTA, _RESULT_OK, trailing_delta)

    log = _FakeEventLog()
    telemetry_ctx.set_current_run(
        event_log=log, run_id="r1", step_name="s", phase="p"
    )
    try:
        with patch("llm_subprocess.subprocess.Popen", return_value=_stream_proc(stdout)):
            invoke_llm_subprocess(
                prompt="x", model="sonnet", timeout_sec=10, step_name="s"
            )
    finally:
        telemetry_ctx.clear_current_run()

    exited = [e for e in log.events if e[0] == "subprocess_exited"]
    assert len(exited) == 1, (
        "expected exactly one subprocess_exited event, got "
        + repr([e[0] for e in log.events])
    )
    payload = exited[0][1]
    assert payload.get("cost_usd") == 0.0123, (
        "cost_usd must be extracted from final type:result event's "
        "total_cost_usd (0.0123), got: " + repr(payload.get("cost_usd"))
    )
    assert payload.get("tokens_in") == 10, (
        "tokens_in must be extracted from final type:result event's "
        "usage.input_tokens (10), got: " + repr(payload.get("tokens_in"))
    )
    assert payload.get("tokens_out") == 20, (
        "tokens_out must be extracted from final type:result event's "
        "usage.output_tokens (20), got: " + repr(payload.get("tokens_out"))
    )


# ─── Test 6: result event with error subtype → status=error ──────────────────


def test_result_event_with_error_subtype_returns_error_status():
    """Final NDJSON event is ``type:result, subtype:error_max_turns`` (or any
    non-success subtype). StepResult must report ``status="error"`` with an
    ``error_code`` set.

    Spec-ambiguity (see module docstring §AMBIGUITIES): the exact error_code
    is not pinned by the migration plan — implementer should pick one.
    Suggest ``E_LLM_RESULT_ERROR`` mirroring the subtype. Test asserts
    error_code is non-None and not ``E_LLM_EXIT`` (since exit_code is 0
    and the underlying process exited cleanly).
    """
    # R1: command=["claude","-p"] -> model="sonnet"
    err_event = (
        '{"type":"result","subtype":"error_max_turns",'
        '"result":"","usage":{"input_tokens":5,"output_tokens":0},'
        '"total_cost_usd":0.0001,"duration_ms":100}'
    )
    stdout = _ndjson_lines(_SYSTEM_INIT, err_event)

    with patch("llm_subprocess.subprocess.Popen", return_value=_stream_proc(stdout, returncode=0)):
        result = invoke_llm_subprocess(
            prompt="x", model="sonnet", timeout_sec=10, step_name="s"
        )

    assert result.status == "error", (
        "type:result + subtype=error_max_turns must yield status=error "
        "(model signaled failure even though process exit was 0); got "
        + repr(result.status)
    )
    assert result.error_code is not None, (
        "type:result + non-success subtype must set error_code; got None"
    )
    assert result.error_code != "E_LLM_EXIT", (
        "must NOT classify as E_LLM_EXIT — process exit_code is 0; this is "
        "a model-side error reported via the result event. got: "
        + repr(result.error_code)
    )
    # Tightening (Opus ambiguity resolution): pin the canonical error_code so
    # a buggy impl returning E_FOO can't satisfy this test.
    assert result.error_code == "E_LLM_RESULT_ERROR", (
        "type:result + non-success subtype must yield error_code "
        "E_LLM_RESULT_ERROR (per Opus ambiguity resolution 2026-05-06); got: "
        + repr(result.error_code)
    )


# ─── Test 7: no result event → treated as failure ────────────────────────────


def test_no_result_event_treated_as_subprocess_failure():
    """Stream ends without any ``type:result`` event (e.g., subprocess died
    after only emitting system-init). Must yield ``status="error"`` with an
    ``error_code`` set; MUST NOT silently return ok with empty raw_response.

    Spec-ambiguity: error_code name is not pinned. Suggest
    ``E_NO_RESULT_EVENT``. Test asserts error_code is non-None.
    """
    # R1: command=["claude","-p"] -> model="sonnet"
    # Only system-init, then EOF — no result event ever.
    stdout = _ndjson_lines(_SYSTEM_INIT)

    with patch("llm_subprocess.subprocess.Popen", return_value=_stream_proc(stdout, returncode=0)):
        result = invoke_llm_subprocess(
            prompt="x", model="sonnet", timeout_sec=10, step_name="s"
        )

    assert result.status == "error", (
        "stream ending with no type:result event must yield status=error "
        "(do NOT silently succeed with empty raw_response); got "
        + repr(result.status)
    )
    assert result.error_code is not None, (
        "no-result-event failure must set error_code; got None"
    )
    # Tightening (Opus ambiguity resolution): pin the canonical code with
    # LLM_ domain prefix matching E_LLM_CMD_MISSING / E_LLM_TIMEOUT / E_LLM_EXIT
    # family.
    assert result.error_code == "E_LLM_NO_RESULT_EVENT", (
        "no-result-event failure must yield error_code E_LLM_NO_RESULT_EVENT "
        "(per Opus ambiguity resolution 2026-05-06); got: "
        + repr(result.error_code)
    )
    # MED-1 (hardening 2026-05-06 evening): error returns must NOT ship a
    # fake string into raw_response. Absent key signals to callers (and
    # downstream tests) that no parseable result was produced; a sentinel
    # like "<no_result_event>" mimics a successful payload to anything that
    # only checks for key presence. Pin the absent-key shape.
    if result.data is not None:
        assert "raw_response" not in result.data, (
            "MED-1: error returns must NOT ship a `raw_response` key — the "
            "old `<no_result_event>` sentinel mimicked a success payload. "
            "got data keys: " + repr(list(result.data.keys()))
        )
    # MED-2: recoverable=True explicit on E_LLM_NO_RESULT_EVENT — transient
    # CLI / network flakes plausibly clear on retry.
    assert result.recoverable is True, (
        "MED-2: E_LLM_NO_RESULT_EVENT must be recoverable=True (transient "
        "subprocess flake); got " + repr(result.recoverable)
    )


# ─── Test 8: malformed line in stream is skipped, not fatal ──────────────────


def test_invalid_json_line_logged_but_does_not_kill_stream():
    """One malformed (non-JSON) line in the stream. Stream-json parser must
    skip the bad line and still extract the type:result event.

    Tests resilience to claude CLI emitting non-event noise (debug lines,
    blank lines, ANSI-stripped artifacts, etc.).

    Fixture places the bad line AFTER the result event — today's
    _extract_result_text reads only the LAST non-empty line, so it parses the
    bad line, hits JSONDecodeError, returns None → raw_response falls back to
    full multi-line stdout (NOT the clean result text). RED until stream-json
    parser walks events and skips non-JSON noise.
    """
    # R1: command=["claude","-p"] -> model="sonnet"
    bad_line = "this is not valid json at all"
    stdout = _ndjson_lines(_SYSTEM_INIT, _ASSISTANT_DELTA, _RESULT_OK, bad_line)

    with patch("llm_subprocess.subprocess.Popen", return_value=_stream_proc(stdout)):
        result = invoke_llm_subprocess(
            prompt="x", model="sonnet", timeout_sec=10, step_name="s"
        )

    assert result.status == "ok", (
        "malformed line in stream must NOT kill the read loop; final "
        "type:result event must still be extracted. got status="
        + repr(result.status)
        + " error="
        + repr(result.error)
    )
    assert result.data["raw_response"] == "FINAL_ANSWER_TEXT", (
        "after skipping the malformed line, raw_response must still equal "
        "the final type:result event's `result` field. got: "
        + repr(result.data["raw_response"])
    )


# ─── Test 9: subprocess_spawned event records output_format=stream-json ──────


def test_telemetry_subprocess_spawned_emits_output_format_stream_json():
    """Existing ``subprocess_spawned`` event payload's ``output_format`` field
    must equal ``"stream-json"`` (not ``"json"``) when auto-injection happens.

    Pins llm_subprocess.py:131-141 (the spawn event emission) AND
    llm_subprocess.py:380-388 (``_detect_output_format`` helper, which today
    only recognizes ``"json"``).
    """
    # R1: command=["claude","-p"] -> model="sonnet"
    stdout = _ndjson_lines(_SYSTEM_INIT, _ASSISTANT_DELTA, _RESULT_OK)

    log = _FakeEventLog()
    telemetry_ctx.set_current_run(
        event_log=log, run_id="r1", step_name="s", phase="p"
    )
    try:
        with patch("llm_subprocess.subprocess.Popen", return_value=_stream_proc(stdout)):
            invoke_llm_subprocess(
                prompt="x", model="sonnet", timeout_sec=10, step_name="s"
            )
    finally:
        telemetry_ctx.clear_current_run()

    spawned = [e for e in log.events if e[0] == "subprocess_spawned"]
    assert len(spawned) == 1, (
        "expected exactly one subprocess_spawned event, got "
        + repr([e[0] for e in log.events])
    )
    payload = spawned[0][1]
    assert "output_format" in payload, (
        "subprocess_spawned must always emit output_format key, got keys: "
        + repr(list(payload.keys()))
    )
    assert payload["output_format"] == "stream-json", (
        "subprocess_spawned output_format must be 'stream-json' under the "
        "migration (not 'json'), got: " + repr(payload["output_format"])
    )


# ─── Test A1: timeout enforced inside streaming loop ─────────────────────────


def test_streaming_timeout_enforced_via_deadline(request):
    """Opus addition A1 — timeout under streaming.

    Given a stream-json claude_p call whose stdout iterator blocks indefinitely
      (or yields no result event for 2x timeout_sec)
    When invoke_llm_subprocess runs with timeout_sec=1
    Then result.status == "error"
      And result.error_code == "E_LLM_TIMEOUT"
      And the subprocess is killed (proc.kill called)
      And duration_ms is roughly timeout_sec * 1000 (within tolerance)
      And subprocess_exited telemetry event is still emitted with timeout fields

    Why this guards GREEN: today's timeout enforcement is
    ``proc.communicate(timeout=timeout_sec)``. Once we move off communicate, the
    timeout mechanism MUST be reimplemented (e.g. wall-clock check inside the
    read loop, or a watchdog thread, or ``select.select`` with a deadline).
    Without this test, a buggy GREEN agent could ship a streaming reader with
    NO timeout enforcement at all and the other tests still pass.
    """
    import time  # local import keeps top of module clean
    import threading

    # R1: command=["claude","-p","--model","sonnet"] -> model="sonnet"

    # Build a stdout that yields a single line then BLOCKS forever on the next
    # readline / iteration. We use a fake file-like object whose readline()
    # returns the system-init line once, then blocks on a release Event past
    # the timeout deadline to force the streaming reader's deadline check to
    # fire. The Event is released at test end so straggler threads unblock
    # immediately instead of sleeping the full 30s.
    release = threading.Event()
    request.addfinalizer(release.set)

    class _BlockingStdout:
        def __init__(self):
            self._delivered = False

        def __iter__(self):
            # iteration-style: yield one line then block on next __next__.
            yield _SYSTEM_INIT + "\n"
            # Block long past timeout_sec=1 so wall-clock check must fire.
            release.wait(30)
            yield _RESULT_OK + "\n"

        def readline(self):
            if not self._delivered:
                self._delivered = True
                return _SYSTEM_INIT + "\n"
            # Subsequent reads block past the timeout deadline.
            release.wait(30)
            return ""

        def read(self, *a, **k):
            release.wait(30)
            return ""

        def close(self):
            return None

    proc = MagicMock()
    proc.pid = 12345
    proc.returncode = None  # process still "running" past deadline
    proc.stdout = _BlockingStdout()
    proc.stderr = io.StringIO("")
    proc.stdin = MagicMock()
    proc.wait = MagicMock(return_value=-9)
    proc.kill = MagicMock()
    proc.communicate = MagicMock(return_value=("CORRUPTED_NEVER_SEE_THIS", ""))

    started = time.monotonic()
    with patch("llm_subprocess.subprocess.Popen", return_value=proc):
        result = invoke_llm_subprocess(
            prompt="x", model="sonnet", timeout_sec=1, step_name="s"
        )
    elapsed = time.monotonic() - started

    assert result.status == "error", (
        "blocking stdout past timeout_sec must yield status=error; got "
        + repr(result.status)
    )
    assert result.error_code == "E_LLM_TIMEOUT", (
        "deadline exceeded inside streaming loop must yield E_LLM_TIMEOUT (the "
        "existing timeout code in llm_subprocess.py); got: "
        + repr(result.error_code)
    )
    assert proc.kill.called, (
        "streaming reader must call proc.kill() when wall-clock deadline "
        "exceeded — otherwise the subprocess leaks. proc.kill.called="
        + repr(proc.kill.called)
    )
    # Sanity: returned within roughly timeout_sec (allow generous slack for
    # post-kill drain). If GREEN forgot the deadline, this would block 30s.
    assert elapsed < 10, (
        "streaming reader didn't honor timeout_sec=1; elapsed="
        + repr(elapsed)
        + "s — looks like deadline never fired"
    )


# ─── Test A2: stdin written and closed BEFORE stdout iter starts ─────────────


def test_stdin_written_and_closed_before_stdout_iter():
    """Opus addition A2 — prompt fed via stdin, stdin closed under streaming.

    Given a claude_p call with prompt="HELLO_PROMPT_TEXT"
    When invoke_llm_subprocess runs
    Then proc.stdin.write was called with "HELLO_PROMPT_TEXT"
      And proc.stdin.close was called

    HIGH-1 hardening update (2026-05-06 evening): the original A2 also
    asserted strict ordering (stdin.close BEFORE first stdout read). That
    ordering REGRESSED the >16KB-prompt pipe-buffer deadlock — if the main
    thread blocks on stdin.write before the reader starts, the CLI can't
    flush stdout to make stdin-write progress. Fix moved both to daemon
    threads running concurrently. The strict ordering assertion is replaced
    with a "both happened" assertion (write + close called) plus the
    independent deadlock test (`test_large_prompt_does_not_deadlock`)
    which proves concurrency works under buffer pressure.
    """
    # R1: command=["claude","-p","--model","sonnet"] -> model="sonnet"
    stdout_text = _ndjson_lines(_SYSTEM_INIT, _ASSISTANT_DELTA, _RESULT_OK)

    # Capture call ordering across stdin AND stdout via a single MagicMock
    # parent — its method_calls list records every child-mock invocation in
    # order.
    parent = MagicMock()
    parent.stdin = MagicMock()
    real_stdout = io.StringIO(stdout_text)

    iter_seen = {"first_call_idx": None}
    readline_seen = {"first_call_idx": None}

    class _OrderingStdout:
        def __init__(self, wrapped):
            self._wrapped = wrapped

        def __iter__(self):
            # Record first __iter__ via parent's call recorder.
            parent.stdout_iter()
            return iter(self._wrapped)

        def readline(self):
            parent.stdout_readline()
            return self._wrapped.readline()

        def read(self, *a, **k):
            parent.stdout_read()
            return self._wrapped.read(*a, **k)

        def close(self):
            return self._wrapped.close()

    proc = MagicMock()
    proc.pid = 12345
    proc.returncode = 0
    proc.stdout = _OrderingStdout(real_stdout)
    proc.stderr = io.StringIO("")
    proc.stdin = parent.stdin
    proc.wait = MagicMock(return_value=0)
    proc.communicate = MagicMock(return_value=("CORRUPTED_NEVER_SEE_THIS", ""))

    with patch("llm_subprocess.subprocess.Popen", return_value=proc):
        invoke_llm_subprocess(
            prompt="HELLO_PROMPT_TEXT",
            model="sonnet",
            timeout_sec=10,
            step_name="s",
        )

    # 1. stdin.write called with the prompt text.
    write_calls = parent.stdin.write.call_args_list
    assert write_calls, (
        "streaming impl must call proc.stdin.write(prompt) — got no write calls"
    )
    # Concatenate all write args in case impl chunks the prompt.
    written = "".join(
        (call.args[0] if call.args else "") for call in write_calls
    )
    assert "HELLO_PROMPT_TEXT" in written, (
        "proc.stdin.write must receive the prompt text 'HELLO_PROMPT_TEXT'; "
        "got writes: " + repr([c.args for c in write_calls])
    )

    # 2. stdin.close called.
    assert parent.stdin.close.called, (
        "proc.stdin.close() must be called to signal EOF to claude CLI — "
        "otherwise CLI blocks on stdin and the streaming reader hangs"
    )

    # 3. HIGH-1 hardening: ordering is no longer load-bearing because the
    # feeder runs on a daemon thread concurrently with the reader. The old
    # "close BEFORE read" contract caused a >16KB-prompt deadlock; new
    # contract is "both happen", proven concurrent by
    # `test_large_prompt_does_not_deadlock`. We just sanity-check both
    # writes + closes appear in the recorded call sequence.
    method_names = [c[0] for c in parent.method_calls]
    assert "stdin.write" in method_names, (
        "stdin.write not seen in method_calls: " + repr(method_names)
    )
    assert "stdin.close" in method_names, (
        "stdin.close not seen in method_calls: " + repr(method_names)
    )
    assert any(
        n in ("stdout_iter", "stdout_readline", "stdout_read")
        for n in method_names
    ), (
        "no stdout read seen in method_calls: " + repr(method_names)
    )


# ─── Test A3: run_ctx=None skips telemetry, stream still works ───────────────


def test_run_ctx_none_skips_telemetry():
    """Opus addition A3 — telemetry context not set → stream-json call still completes.

    Given telemetry_ctx.get_current_run() returns None
    When invoke_llm_subprocess runs with a valid stream-json fixture
    Then no telemetry events are emitted to any event_log
      And result.status == "ok"
      And result.data["raw_response"] == "FINAL_ANSWER_TEXT"

    Why this guards GREEN: Tests 5 and 9 always set the telemetry context. The
    "no run_ctx" branch (``if run_ctx is not None:`` at lines 130 and 165 of
    llm_subprocess.py) is untested for stream-json. A buggy GREEN agent could
    accidentally make the streaming reader depend on telemetry presence.
    Regression guard for the existing line-130 / line-165 emission gates.
    """
    # R1: command=["claude","-p","--model","sonnet"] -> model="sonnet"
    stdout = _ndjson_lines(_SYSTEM_INIT, _ASSISTANT_DELTA, _RESULT_OK)

    # Patch the safe-emit helper so we can assert NOTHING fires when run_ctx is
    # None. (Belt + suspenders: also assert get_current_run returns None.)
    telemetry_ctx.clear_current_run()
    assert telemetry_ctx.get_current_run() is None, (
        "test precondition: no active run; got "
        + repr(telemetry_ctx.get_current_run())
    )

    with patch("llm_subprocess._emit_safe") as mock_emit:
        with patch("llm_subprocess.subprocess.Popen", return_value=_stream_proc(stdout)):
            result = invoke_llm_subprocess(
                prompt="x", model="sonnet", timeout_sec=10, step_name="s"
            )

    assert mock_emit.call_count == 0, (
        "with run_ctx=None, _emit_safe must NOT be called — telemetry must be "
        "skipped (per the `if run_ctx is not None` gate at llm_subprocess.py "
        "lines 130 + 165). got call_count="
        + repr(mock_emit.call_count)
        + " calls="
        + repr(mock_emit.call_args_list)
    )
    assert result.status == "ok", (
        "stream-json call with no telemetry context must still succeed; got "
        + repr(result.status)
        + " err="
        + repr(result.error)
    )
    assert result.data["raw_response"] == "FINAL_ANSWER_TEXT", (
        "raw_response must still be extracted when run_ctx is None; got "
        + repr(result.data["raw_response"])
    )


# ─── Test A4: hard_gate refusal short-circuits BEFORE streaming read ─────────


def test_hard_gate_refusal_short_circuits_before_stream_read():
    """Opus addition A4 — hard_gate refusal short-circuits before any subprocess spawn.

    Given a claude_p call with a non-Opus model (default sonnet/haiku)
      And hard_gate=True is passed
    When invoke_llm_subprocess runs
    Then NO subprocess.Popen call happens
      And NO proc.stdout iteration happens
      And result.error_code == "E_HARD_GATE_MODEL_DOWNGRADE"
      And result.status == "error"
      And the streaming-read code path is never reached
      And hard_gate_refused event emitted via _emit_safe (when run_ctx active)

    Why this guards GREEN: the hard_gate chokepoint runs BEFORE Popen
    (llm_subprocess.py:84-92). The migration must not accidentally re-order
    or skip the gate — e.g., if GREEN restructures invoke_llm_subprocess to
    spawn-then-stream as one block, the gate could end up running AFTER spawn.
    Regression guard mixing hard_gate with stream-json contract.
    """
    # R1: model="sonnet" (non-opus) → hard_gate should refuse
    log = _FakeEventLog()
    telemetry_ctx.set_current_run(
        event_log=log, run_id="r-a4", step_name="s", phase="p"
    )
    try:
        with patch("llm_subprocess.subprocess.Popen") as mock_popen:
            result = invoke_llm_subprocess(
                prompt="x",
                model="sonnet",
                timeout_sec=10,
                step_name="s",
                hard_gate=True,
                gate_label="opus_only",
            )
    finally:
        telemetry_ctx.clear_current_run()

    # 1. NO Popen call — gate refused before spawn.
    mock_popen.assert_not_called()
    # Defensive secondary check: even if the mock was wired weirdly,
    # communicate must not have been called either.
    assert mock_popen.call_count == 0, (
        "hard_gate refusal must short-circuit BEFORE subprocess.Popen; got "
        "call_count=" + repr(mock_popen.call_count)
    )

    # 2. Result shape: error + E_HARD_GATE_MODEL_DOWNGRADE.
    assert result.status == "error", (
        "hard_gate refusal must yield status=error; got " + repr(result.status)
    )
    assert result.error_code == "E_HARD_GATE_MODEL_DOWNGRADE", (
        "hard_gate refusal must yield E_HARD_GATE_MODEL_DOWNGRADE; got "
        + repr(result.error_code)
    )

    # 3. hard_gate_refused event was emitted via _emit_safe (run_ctx active).
    refused = [e for e in log.events if e[0] == "hard_gate_refused"]
    assert len(refused) == 1, (
        "hard_gate refusal with active run_ctx must emit exactly one "
        "hard_gate_refused event; got "
        + repr([e[0] for e in log.events])
    )
    payload = refused[0][1]
    assert payload.get("gate_label") == "opus_only", (
        "hard_gate_refused payload.gate_label must equal the gate_label arg; "
        "got " + repr(payload.get("gate_label"))
    )


# ─── Hardening test H1: stdin write doesn't deadlock the reader (HIGH-1) ─────


def test_large_prompt_does_not_deadlock():
    """HIGH-1 hardening — concurrent stdin-feeder thread.

    Reproduces the macOS pipe-buffer deadlock scenario: stdin.write blocks
    (mock with a deliberate sleep) while stdout has a result event ready.
    With the old code (stdin.write/close BEFORE reader thread start), this
    would deadlock for the full timeout. With the fix (feeder thread runs
    concurrently with reader thread, both daemons), it must complete inside
    the timeout window because the reader can drain stdout while the feeder
    is still pumping stdin bytes.
    """
    import time as _time

    # R1: command=["claude","-p","--model","sonnet"] -> model="sonnet"
    stdout_text = _ndjson_lines(_SYSTEM_INIT, _ASSISTANT_DELTA, _RESULT_OK)

    # Fake stdin where write() blocks for 0.5s then completes — simulates a
    # pipe-buffer-fill scenario where the CLI is slow to drain stdin.
    class _SlowStdin:
        def __init__(self):
            self.write_calls: list[str] = []
            self.closed = False

        def write(self, data):
            _time.sleep(0.5)
            self.write_calls.append(data)
            return len(data) if isinstance(data, str) else 0

        def close(self):
            self.closed = True

    proc = MagicMock()
    proc.pid = 12345
    proc.returncode = 0
    proc.stdout = io.StringIO(stdout_text)
    proc.stderr = io.StringIO("")
    proc.stdin = _SlowStdin()
    proc.wait = MagicMock(return_value=0)
    proc.communicate = MagicMock(return_value=("CORRUPTED_NEVER_SEE_THIS", ""))

    started = _time.monotonic()
    with patch("llm_subprocess.subprocess.Popen", return_value=proc):
        result = invoke_llm_subprocess(
            prompt="x" * 70_000,  # >> macOS 16KB pipe buffer
            model="sonnet",
            timeout_sec=2,
            step_name="s",
        )
    elapsed = _time.monotonic() - started

    assert elapsed < 2.0, (
        "HIGH-1: large-prompt stream-json call must NOT deadlock past timeout "
        "— concurrent stdin-feeder thread should let the reader drain stdout "
        "while the feeder is still writing. elapsed=" + repr(elapsed) + "s"
    )
    assert result.status == "ok", (
        "HIGH-1: under concurrent feeder/reader, status must be ok; got "
        + repr(result.status) + " err=" + repr(result.error)
    )
    assert result.data["raw_response"] == "FINAL_ANSWER_TEXT"
    # Feeder still wrote + closed the prompt.
    assert proc.stdin.closed is True, (
        "HIGH-1: stdin.close must be called by the feeder thread"
    )


# ─── Hardening test H2: timeout uses remaining budget, not fresh budget ──────


def test_timeout_uses_remaining_budget(request):
    """HIGH-2 hardening — wall-clock budget is end-to-end, not per-phase.

    Scenario: stdin-feeder takes ~1s (mock sleep), then reader blocks
    forever. With the OLD code (fresh `timeout_sec` passed to t.join),
    total elapsed = feeder_time + reader_join_timeout = 1 + 3 = 4s
    even though caller asked for 3s budget. With the fix (REMAINING budget
    passed to reader join), reader sees ~2s remaining → total elapsed ~3s.

    Tolerance: assert total elapsed < timeout_sec * 1.5 (4.5s budget for
    a 3s timeout). Without the fix elapsed would be ~4s minimum and likely
    up to 5s+ once feeder + post-kill drain overhead is added.
    """
    import time as _time
    import threading

    # R1: command=["claude","-p","--model","sonnet"] -> model="sonnet"

    # Reader blocks on a release Event past any reasonable budget, released
    # at test end so straggler threads unblock immediately instead of
    # sleeping the full 30s.
    release = threading.Event()
    request.addfinalizer(release.set)

    class _SlowStdin:
        def write(self, data):
            _time.sleep(1.0)  # eat 1s of the 3s budget

        def close(self):
            pass

    class _BlockingStdout:
        def readline(self):
            release.wait(30)  # block far past any reasonable budget
            return ""

        def read(self, *a, **k):
            release.wait(30)
            return ""

        def close(self):
            return None

    proc = MagicMock()
    proc.pid = 12345
    proc.returncode = None
    proc.stdout = _BlockingStdout()
    proc.stderr = io.StringIO("")
    proc.stdin = _SlowStdin()
    proc.wait = MagicMock(return_value=-9)
    proc.kill = MagicMock()
    proc.communicate = MagicMock(return_value=("CORRUPTED_NEVER_SEE_THIS", ""))

    started = _time.monotonic()
    with patch("llm_subprocess.subprocess.Popen", return_value=proc):
        result = invoke_llm_subprocess(
            prompt="x", model="sonnet", timeout_sec=3, step_name="s"
        )
    elapsed = _time.monotonic() - started

    assert result.status == "error"
    assert result.error_code == "E_LLM_TIMEOUT"
    assert proc.kill.called, "HIGH-2: kill must fire when remaining budget elapses"
    # HIGH-2 — total elapsed must respect the ORIGINAL budget, not feeder + fresh budget.
    assert elapsed < 4.5, (
        "HIGH-2: timeout must use remaining budget (timeout_sec - feeder_elapsed), "
        "not a fresh timeout_sec on each thread join. timeout_sec=3, observed="
        + repr(elapsed) + "s — looks like deadline was reset"
    )


# ─── Hardening test H3a: empty `result` on subtype=success → malformed ───────


def test_success_with_empty_result_is_error():
    """HIGH-3 hardening — empty-string result on subtype=success.

    The W1 post-mortem cited 'silent-success with empty raw_response' as
    a bug class. A result event with `{"subtype":"success","result":""}`
    used to pass through (raw_response = ""), letting downstream phases
    consume an empty payload. Fix: treat empty result as
    E_LLM_RESULT_MALFORMED so retries / human review fire.
    """
    # R1: command=["claude","-p"] -> model="sonnet"
    empty_result = (
        '{"type":"result","subtype":"success",'
        '"result":"",'
        '"usage":{"input_tokens":10,"output_tokens":0,'
        '"cache_read_input_tokens":0,"cache_creation_input_tokens":0},'
        '"total_cost_usd":0.01,"duration_ms":50}'
    )
    stdout = _ndjson_lines(_SYSTEM_INIT, empty_result)

    with patch("llm_subprocess.subprocess.Popen", return_value=_stream_proc(stdout)):
        result = invoke_llm_subprocess(
            prompt="x", model="sonnet", timeout_sec=10, step_name="s"
        )

    assert result.status == "error", (
        "HIGH-3: subtype=success + empty result must yield status=error "
        "(silent-success bug class); got " + repr(result.status)
    )
    assert result.error_code == "E_LLM_RESULT_MALFORMED", (
        "HIGH-3: empty result on success subtype must yield "
        "E_LLM_RESULT_MALFORMED; got " + repr(result.error_code)
    )
    # MED-1: no raw_response key on error.
    if result.data is not None:
        assert "raw_response" not in result.data, (
            "MED-1: malformed-result error must not ship a raw_response key; "
            "got data keys: " + repr(list(result.data.keys()))
        )
    # MED-2: explicit recoverable=True.
    assert result.recoverable is True


# ─── Hardening test H3b: whitespace-only result on subtype=success → malformed
def test_success_with_whitespace_result_is_error():
    """HIGH-3 hardening — whitespace-only result is the same bug class.

    Whitespace ('  \\n\\t') trims to empty, so semantically equivalent to
    empty-string. Pinned independently to defend against an over-narrow
    fix that only checks `result == ""`.
    """
    # R1: command=["claude","-p"] -> model="sonnet"
    ws_result = (
        '{"type":"result","subtype":"success",'
        '"result":"   \\n\\t  ",'
        '"usage":{"input_tokens":10,"output_tokens":0},'
        '"total_cost_usd":0.01}'
    )
    stdout = _ndjson_lines(_SYSTEM_INIT, ws_result)

    with patch("llm_subprocess.subprocess.Popen", return_value=_stream_proc(stdout)):
        result = invoke_llm_subprocess(
            prompt="x", model="sonnet", timeout_sec=10, step_name="s"
        )

    assert result.status == "error"
    assert result.error_code == "E_LLM_RESULT_MALFORMED", (
        "HIGH-3: whitespace-only result must also yield E_LLM_RESULT_MALFORMED; "
        "got " + repr(result.error_code)
    )


# ─── Hardening test H3c: missing subtype → malformed (MED-3) ─────────────────


def test_missing_subtype_treated_as_malformed():
    """MED-3 hardening — absent subtype must NOT default to success.

    Old code did `result_event.get("subtype", "success")`. A malformed
    event missing the subtype key would silently pass as success. Fix:
    no default → absent/non-string subtype → E_LLM_RESULT_MALFORMED.
    """
    # R1: command=["claude","-p"] -> model="sonnet"
    no_subtype = (
        '{"type":"result",'  # subtype field absent
        '"result":"some text",'
        '"usage":{"input_tokens":10,"output_tokens":5},'
        '"total_cost_usd":0.01}'
    )
    stdout = _ndjson_lines(_SYSTEM_INIT, no_subtype)

    with patch("llm_subprocess.subprocess.Popen", return_value=_stream_proc(stdout)):
        result = invoke_llm_subprocess(
            prompt="x", model="sonnet", timeout_sec=10, step_name="s"
        )

    assert result.status == "error", (
        "MED-3: result event missing subtype must NOT silently pass as "
        "success; got status=" + repr(result.status)
    )
    assert result.error_code == "E_LLM_RESULT_MALFORMED", (
        "MED-3: missing subtype must yield E_LLM_RESULT_MALFORMED; got "
        + repr(result.error_code)
    )


# ─── Hardening test M1: error returns omit raw_response key (MED-1) ──────────


def test_error_returns_omit_raw_response_key():
    """MED-1 hardening — confirm absent-key shape on E_LLM_RESULT_ERROR
    (and re-confirms it on E_LLM_NO_RESULT_EVENT alongside Test 7).

    The old code shipped `"raw_response": "<no_result_event>"` into error
    StepResult.data for the no-result-event branch. Sentinel strings mimic
    a successful payload to any caller that only checks key presence. Pin
    the absent-key shape across all error branches.
    """
    # R1: command=["claude","-p"] -> model="sonnet"
    err_event = (
        '{"type":"result","subtype":"error_max_turns",'
        '"result":"","usage":{"input_tokens":5,"output_tokens":0},'
        '"total_cost_usd":0.0001,"duration_ms":100}'
    )
    stdout = _ndjson_lines(_SYSTEM_INIT, err_event)

    with patch("llm_subprocess.subprocess.Popen", return_value=_stream_proc(stdout)):
        result = invoke_llm_subprocess(
            prompt="x", model="sonnet", timeout_sec=10, step_name="s"
        )

    assert result.error_code == "E_LLM_RESULT_ERROR"
    assert result.data is not None
    assert "raw_response" not in result.data, (
        "MED-1: E_LLM_RESULT_ERROR must NOT ship a raw_response key; got "
        "data keys: " + repr(list(result.data.keys()))
    )
    # MED-2: recoverable=True explicit.
    assert result.recoverable is True
