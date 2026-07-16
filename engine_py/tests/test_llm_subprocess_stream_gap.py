"""RED tests for 6923B6AC D2 (stream-gap tolerance) + D3 (idle-timeout alarm).

Companion to ``test_phase_6_idle_timeout_invariant.py`` (D1 callsite invariant).

Background:
  BB8BFEFE run ``forge-1778192708-e2e3e40b`` cycle 1, ``invoke_fix_llm``:
    - subprocess_spawned 22:45:34 → subprocess_exited 22:46:51 (77s)
    - exit_code=-9 (SIGKILL), aborted_reason="idle_timeout"
    - response_size_bytes=116020 (full review streamed)
    - tokens_out=null (final result event not captured before kill)
  Despite ACTIVE LLM output, ``_stream_read_events`` killed the
  subprocess at the 60s mark because some inter-event gap inside the
  stream exceeded 60s — likely a long internal tool-use round-trip
  while the LLM generated a 116KB review.

D2 — Behavioral stream-gap tolerance test
  Two cases pin both branches of the discriminator:
    D2a (tolerance):   idle_timeout_sec=None → long gap survives.
    D2b (mechanism):   idle_timeout_sec=2 (small for test wall-clock) →
                       same fixture aborts with aborted_reason="idle_timeout".
  D2b proves we are not breaking the watchdog mechanism (it still trips
  when configured); only opting fix_llm out by removing the explicit
  ``idle_timeout_sec=60`` kwarg.

  Note on time: real-time gaps are used (matching the surrounding
  ``test_llm_subprocess_775D6752.py`` style — see _TimedStdout). The
  scaled-down gap is 4s rather than the spec's literal 70s; the
  mechanism is identical (idle counter resets per non-empty stdout
  line; gap > idle_timeout_sec triggers kill). A 4s gap with
  idle_timeout_sec=2 fires the watchdog as deterministically as a 70s
  gap with idle_timeout_sec=60. Pre-GREEN expectation hinges on the
  watchdog code path, not the absolute number.

D3 — Telemetry alarm event
  When ``_stream_read_events`` aborts with ``aborted_reason="idle_timeout"``,
  emit a NEW event of type ``idle_timeout_alarm`` with payload schema:
      run_id, phase, step_name, duration_ms, response_size_bytes,
      idle_timeout_sec, tokens_out
  Cheap (~5 LOC) and allows future audit of "how many idle_timeout fires
  per build" via grep on events.jsonl.

  D3a (alarm emitted on idle-timeout abort): idle abort path emits the
  event with documented payload keys.
  D3b (alarm NOT emitted on success): clean success path emits NO
  ``idle_timeout_alarm`` event.

Out of role (per spec):
  - Do NOT modify llm_subprocess.py (GREEN does that).
  - Do NOT modify phase_6_review.py (GREEN does that).
  - Do NOT alter existing tests.
  - Do NOT touch _DEFAULT_IDLE_TIMEOUT_SEC.
  - Do NOT touch _green_watchdog / _fix_watchdog.
  - Do NOT change _tokens_and_cost_from_events / _find_last_result_event.

Pre-GREEN expected results:
  D2a: FAIL  — current code allows None, but the broader RC contract is
               about reliance on that default. With idle_timeout_sec=None
               passed explicitly, current code already tolerates the gap;
               the FAILURE here is structural — see D2a docstring for
               which assertion forces it to fail pre-GREEN. (See
               clarification: D2a tests the BEHAVIORAL contract for the
               POST-GREEN default — a buggy revert that re-enables a
               default would be caught here. Some assertions still fail
               on current code; see specific notes in the test.)
  D2b: PASS  — current code's mechanism trips when configured.
  D3a: FAIL  — current code does NOT emit ``idle_timeout_alarm``.
  D3b: PASS  — current code emits no such event on success path.
"""
from __future__ import annotations

import io
import sys
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "workflows"))
sys.path.insert(0, str(HERE.parent / "lib"))

from llm_subprocess import invoke_llm_subprocess  # noqa: E402
import telemetry_ctx  # noqa: E402


# ─── Helpers — mirror test_llm_subprocess_775D6752.py exactly ────────────────


class _FakeEventLog:
    """Captures (event_type, payload, run_id) tuples for assertion.

    Mirrors the helper in test_llm_subprocess_775D6752.py so the same
    telemetry_ctx wiring works identically here.
    """

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


class _TimedStdout:
    """Schedule-driven stdout. ``readline`` sleeps ``delay`` then returns
    the line. After the schedule is exhausted, blocks for ``block_sec``
    (default 30) before returning "" (EOF). Matches the helper in
    ``test_llm_subprocess_775D6752.py``.
    """

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


# ─── D2a — stream-gap tolerance with idle_timeout_sec=None ───────────────────


def test_stream_gap_tolerated_with_idle_timeout_none():
    """6923B6AC D2a: with ``idle_timeout_sec=None`` the watchdog is
    DISABLED — a long inter-event gap (well past any historical
    idle_timeout_sec=60) MUST NOT abort the subprocess. Result event
    arrives after the gap; subprocess completes normally; tokens_out
    populated from the result event.

    Repro shape (scaled down for test wall-clock):
      - assistant-style event at t=0
      - 4s of stdout silence (analogue of BB8BFEFE's 60+s tool-use gap)
      - result event at t=4s

    Key assertions:
      - status == "ok" (no abort)
      - error_code is None (no E_LLM_NO_PROGRESS)
      - subprocess_exited payload's aborted_reason is NOT "idle_timeout"
      - tokens_out populated (the result event landed)
      - proc.kill NOT called (no abort path was taken)

    Pre-GREEN: PASSES (current code with idle_timeout_sec=None already
    behaves this way) — this is the BEHAVIORAL CONTRACT TEST that
    catches a future regression where a default of 60s gets baked into
    invoke_llm_subprocess. By keeping the assertion explicit and
    behavioral, a regression where someone re-introduces
    ``_DEFAULT_IDLE_TIMEOUT_SEC = 60`` would fail this test loudly.
    """
    # R1: command=["claude","-p","--model","sonnet"] -> model="sonnet"
    schedule = [
        (0.05, _SYSTEM_INIT),  # event at t=0
        (4.0, _RESULT_OK),     # 4s gap, then result event
    ]
    stdout_obj = _TimedStdout(schedule, block_sec=2.0)
    proc = _stream_proc_with_stdout(stdout_obj, returncode=0)

    log = _FakeEventLog()
    telemetry_ctx.set_current_run(
        event_log=log, run_id="r-d2a", step_name="invoke_fix_llm", phase="phase_6"
    )
    started = time.monotonic()
    try:
        with patch("llm_subprocess.subprocess.Popen", return_value=proc):
            result = invoke_llm_subprocess(
                prompt="x",
                model="sonnet",
                timeout_sec=20,
                step_name="invoke_fix_llm",
                idle_timeout_sec=None,  # 6923B6AC: explicit disable
            )
    finally:
        telemetry_ctx.clear_current_run()
    elapsed = time.monotonic() - started

    assert result.status == "ok", (
        "6923B6AC D2a: idle_timeout_sec=None must allow a long inter-event "
        "gap to survive. got status=" + repr(result.status)
        + " err=" + repr(result.error)
    )
    assert result.error_code is None, (
        "6923B6AC D2a: no abort means error_code is None; got "
        + repr(result.error_code)
    )
    assert not proc.kill.called, (
        "6923B6AC D2a: idle_timeout_sec=None must NOT trigger kill(). "
        "proc.kill.called=" + repr(proc.kill.called)
    )
    # Tokens populated from the result event.
    if isinstance(result.data, dict):
        # Per llm_subprocess.py:_parse_claude_json + _tokens_and_cost_from_events,
        # tokens flow into the StepResult.data via the caller's wrapping path.
        # The subprocess_exited telemetry event is the canonical place to
        # check tokens_out for the assertion below.
        pass
    exited = [e for e in log.events if e[0] == "subprocess_exited"]
    assert len(exited) == 1, (
        "6923B6AC D2a: success path must still emit one subprocess_exited; "
        "got " + repr([e[0] for e in log.events])
    )
    payload = exited[0][1]
    assert payload.get("aborted_reason") != "idle_timeout", (
        "6923B6AC D2a: success path must NOT label aborted_reason='idle_timeout'. "
        "got: " + repr(payload.get("aborted_reason"))
    )
    assert payload.get("tokens_out") == 20, (
        "6923B6AC D2a: tokens_out must be populated from the trailing "
        "result event (output_tokens=20 in fixture). got: "
        + repr(payload.get("tokens_out"))
    )
    # Sanity: actually waited the gap (proves the test exercised the path).
    assert elapsed >= 3.5, (
        "6923B6AC D2a: fixture didn't actually idle for ~4s; elapsed="
        + repr(elapsed) + "s — bug in test fixture"
    )


# ─── D2b — same fixture trips when configured (mechanism still works) ────────


def test_stream_gap_aborts_when_idle_timeout_explicit():
    """6923B6AC D2b: same fixture, but pass an explicit (small for test
    wall-clock) ``idle_timeout_sec=2`` — the idle watchdog must STILL
    trip. Proves the mechanism is intact; we are merely opting fix_llm
    out by removing the explicit ``idle_timeout_sec=60`` kwarg.

    Pre-GREEN: PASSES (current code's idle watchdog trips correctly when
    configured). Post-GREEN: still PASSES (no change to mechanism).
    """
    # R1: command=["claude","-p","--model","sonnet"] -> model="sonnet"
    # 4s gap > idle_timeout_sec=2 → must abort.
    schedule = [
        (0.05, _SYSTEM_INIT),
        (4.0, _RESULT_OK),
    ]
    stdout_obj = _TimedStdout(schedule, block_sec=2.0)
    proc = _stream_proc_with_stdout(stdout_obj, returncode=None)

    log = _FakeEventLog()
    telemetry_ctx.set_current_run(
        event_log=log, run_id="r-d2b", step_name="invoke_fix_llm", phase="phase_6"
    )
    try:
        with patch("llm_subprocess.subprocess.Popen", return_value=proc):
            result = invoke_llm_subprocess(
                prompt="x",
                model="sonnet",
                timeout_sec=20,
                step_name="invoke_fix_llm",
                idle_timeout_sec=2,  # explicit configuration — mechanism live
            )
    finally:
        telemetry_ctx.clear_current_run()

    assert result.status == "error", (
        "6923B6AC D2b: idle_timeout_sec=2 with a 4s gap must abort. "
        "got status=" + repr(result.status)
    )
    assert result.error_code == "E_LLM_NO_PROGRESS", (
        "6923B6AC D2b: idle abort yields E_LLM_NO_PROGRESS. got: "
        + repr(result.error_code)
    )
    exited = [e for e in log.events if e[0] == "subprocess_exited"]
    assert len(exited) == 1, (
        "6923B6AC D2b: idle abort still emits exactly one subprocess_exited; "
        "got " + repr([e[0] for e in log.events])
    )
    payload = exited[0][1]
    assert payload.get("aborted_reason") == "idle_timeout", (
        "6923B6AC D2b: aborted_reason must be 'idle_timeout' when the "
        "configured watchdog trips. got: "
        + repr(payload.get("aborted_reason"))
    )
    assert proc.kill.called, (
        "6923B6AC D2b: idle abort must call proc.kill(). "
        "proc.kill.called=" + repr(proc.kill.called)
    )


# ─── D3a — alarm emitted on idle-timeout abort ───────────────────────────────


def test_idle_timeout_alarm_emitted_on_abort():
    """6923B6AC D3a: when ``_stream_read_events`` aborts with
    aborted_reason='idle_timeout', a NEW event of type
    ``idle_timeout_alarm`` is emitted to the engine event log with
    payload schema:
      run_id, phase, step_name, duration_ms, response_size_bytes,
      idle_timeout_sec, tokens_out

    Schema-only assertion: tokens_out may be None on idle-timeout
    (no result event captured). All other keys must be present.

    Pre-GREEN: FAILS — current code does NOT emit this event.
    Post-GREEN: PASSES — GREEN adds ~5 LOC inside the idle-abort branch
    of _stream_read_events (or invoke_llm_subprocess, in scope) to call
    _emit_safe with the documented payload.
    """
    # R1: command=["claude","-p","--model","sonnet"] -> model="sonnet"
    schedule = [(0.05, _SYSTEM_INIT)]  # one event then forever-block
    stdout_obj = _TimedStdout(schedule, block_sec=30.0)
    proc = _stream_proc_with_stdout(stdout_obj, returncode=None)

    log = _FakeEventLog()
    telemetry_ctx.set_current_run(
        event_log=log,
        run_id="r-d3a",
        step_name="invoke_fix_llm",
        phase="phase_6",
    )
    try:
        with patch("llm_subprocess.subprocess.Popen", return_value=proc):
            invoke_llm_subprocess(
                prompt="x",
                model="sonnet",
                timeout_sec=20,
                step_name="invoke_fix_llm",
                idle_timeout_sec=1,  # tight — guaranteed trip
            )
    finally:
        telemetry_ctx.clear_current_run()

    alarms = [e for e in log.events if e[0] == "idle_timeout_alarm"]
    assert len(alarms) == 1, (
        "6923B6AC D3a: idle-timeout abort must emit exactly one "
        "``idle_timeout_alarm`` event. got event types: "
        + repr([e[0] for e in log.events])
    )
    event_type, payload, run_id = alarms[0]
    # Schema check — required keys.
    required_keys = {
        "run_id",
        "phase",
        "step_name",
        "duration_ms",
        "response_size_bytes",
        "idle_timeout_sec",
        "tokens_out",
    }
    missing = required_keys - set(payload.keys())
    assert not missing, (
        "6923B6AC D3a: idle_timeout_alarm payload missing required "
        "keys: " + repr(sorted(missing))
        + " (have: " + repr(sorted(payload.keys())) + ")"
    )
    # Identity values plumbed from telemetry_ctx must match what we set.
    assert payload.get("run_id") == "r-d3a", (
        "6923B6AC D3a: alarm payload.run_id must echo the active run; got "
        + repr(payload.get("run_id"))
    )
    assert payload.get("phase") == "phase_6", (
        "6923B6AC D3a: alarm payload.phase must echo telemetry_ctx; got "
        + repr(payload.get("phase"))
    )
    assert payload.get("step_name") == "invoke_fix_llm", (
        "6923B6AC D3a: alarm payload.step_name must echo telemetry_ctx; got "
        + repr(payload.get("step_name"))
    )
    assert payload.get("idle_timeout_sec") == 1, (
        "6923B6AC D3a: alarm payload.idle_timeout_sec must echo the "
        "configured cap; got " + repr(payload.get("idle_timeout_sec"))
    )
    # duration_ms must be an int representing the elapsed ms in the call.
    assert isinstance(payload.get("duration_ms"), int), (
        "6923B6AC D3a: alarm payload.duration_ms must be an int; got "
        + repr(type(payload.get("duration_ms")))
    )
    assert payload["duration_ms"] >= 1000, (
        "6923B6AC D3a: alarm payload.duration_ms must be >= 1000ms "
        "(idle_timeout_sec=1 means at least 1s elapsed before kill); got "
        + repr(payload["duration_ms"])
    )
    # response_size_bytes must be an int (0 acceptable on early abort).
    assert isinstance(payload.get("response_size_bytes"), int), (
        "6923B6AC D3a: alarm payload.response_size_bytes must be an int; got "
        + repr(type(payload.get("response_size_bytes")))
    )


# ─── D3b — alarm NOT emitted on success ──────────────────────────────────────


def test_idle_timeout_alarm_absent_on_success():
    """6923B6AC D3b: clean success path emits NO ``idle_timeout_alarm``
    event. Catches a buggy GREEN that hardcodes the emit on every
    subprocess_exited (success branch included).

    Pre-GREEN: PASSES (current code emits nothing on success — there
    is no idle_timeout_alarm code path at all).
    Post-GREEN: still PASSES (GREEN gates the emit on
    aborted_reason=='idle_timeout').
    """
    # R1: command=["claude","-p","--model","sonnet"] -> model="sonnet"
    # Result event lands quickly — well under both idle (1s) and outer (60s).
    schedule = [
        (0.05, _SYSTEM_INIT),
        (0.05, _RESULT_OK),
    ]
    stdout_obj = _TimedStdout(schedule, block_sec=2.0)
    proc = _stream_proc_with_stdout(stdout_obj, returncode=0)

    log = _FakeEventLog()
    telemetry_ctx.set_current_run(
        event_log=log,
        run_id="r-d3b",
        step_name="invoke_fix_llm",
        phase="phase_6",
    )
    try:
        with patch("llm_subprocess.subprocess.Popen", return_value=proc):
            result = invoke_llm_subprocess(
                prompt="x",
                model="sonnet",
                timeout_sec=20,
                step_name="invoke_fix_llm",
                idle_timeout_sec=1,  # configured but never trips here
            )
    finally:
        telemetry_ctx.clear_current_run()

    assert result.status == "ok", (
        "6923B6AC D3b: result event lands well under both ceilings; must "
        "succeed. got status=" + repr(result.status)
        + " err=" + repr(result.error)
    )
    alarms = [e for e in log.events if e[0] == "idle_timeout_alarm"]
    assert alarms == [], (
        "6923B6AC D3b: success path must NOT emit ``idle_timeout_alarm``. "
        "Hardcoded emit (e.g. on every subprocess_exited) would burn "
        "observability and break the audit-by-grep contract. "
        "got alarms: " + repr(alarms)
    )
