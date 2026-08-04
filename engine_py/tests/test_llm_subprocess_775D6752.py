"""RED tests for 775D6752 idle-timeout watchdog. Builds on 23680DDA stream-json migration.

See SHARED/memory/Decisions/2026-05-06_W1_resume_postmortem.md and the
follow-up 775D6752 agreement (2026-05-06 evening) for context.

Background:
  After 23680DDA (commit a0689797) stream-json migration, the per-call
  ceiling is still ``timeout_sec`` (typically 900s). A subprocess can
  legitimately hang for the full 15 minutes if the model emits a few
  events and then goes quiet (token-loop pause, network hiccup mid-flight).
  The 23680DDA wall-clock watchdog only catches the OUTER ceiling.

  775D6752 adds an in-stream IDLE-timeout watchdog: if no NDJSON event
  arrives within ``idle_timeout_sec`` (default 60s), kill the subprocess
  with ``E_LLM_NO_PROGRESS`` (recoverable=True, retry-loop kicks in).
  Saves the wasted 14min next time something legitimately hangs while
  preserving the overall timeout_sec ceiling.

Architecture facts leveraged here:
  - Stream-json reader is ``_stream_read_events`` in llm_subprocess.py:444+.
  - Reader thread appends to shared ``events`` and ``raw_lines``; main
    thread waits on ``reader_thread.join(timeout=remaining_budget)``.
  - 775D6752 inserts a per-event idle deadline INSIDE the reader loop (or
    by replacing the single join with a polling loop bounded by
    ``min(remaining_budget, idle_timeout_sec)``). Either approach works
    if the contract below holds.
  - Legacy path (``_communicate_legacy``) is intentionally exempt from
    idle-timeout (no per-line stream to monitor — it's blocking-on-EOF
    by design and 23680DDA explicitly preserved that for caller-supplied
    --output-format).

Spec ambiguities flagged for Opus validate gate:
  - Canonical telemetry field name on idle abort. No existing
    ``aborted_reason``/``abort_reason`` field in llm_subprocess.py;
    proposed: ``aborted_reason="idle_timeout"`` on the
    ``subprocess_exited`` event. Test 5 asserts that exact key/value.
  - Error code: ``E_LLM_NO_PROGRESS`` (matches the E_LLM_* family —
    E_LLM_TIMEOUT / E_LLM_EXIT / E_LLM_RESULT_ERROR / E_LLM_NO_RESULT_EVENT
    / E_LLM_RESULT_MALFORMED / E_LLM_CMD_MISSING).
  - Module-level constant: ``_DEFAULT_IDLE_TIMEOUT_SEC = None`` (disabled by default).
  - kwarg name: ``idle_timeout_sec`` (matches ``timeout_sec`` style).
  - Disable semantics: ``idle_timeout_sec=0`` OR ``None`` disables
    watchdog (escape hatch for callers who want only the outer ceiling).
  - Legacy path: idle_timeout_sec parameter is accepted (signature
    compat) but ignored — _communicate_legacy has no per-line events to
    monitor.

Test inventory (RED — every test must FAIL or ERROR against current code
post-23680DDA):
  1. test_idle_timeout_kills_subprocess_when_no_events
       Single event then forever-block. idle_timeout_sec=2, timeout_sec=60.
       Must abort with E_LLM_NO_PROGRESS, recoverable=True, ~3s wall
       (not 60s ceiling). Pins the new in-stream watchdog inside
       _stream_read_events (llm_subprocess.py:556 reader_thread.join).
  2. test_idle_timeout_resets_on_each_event
       Events every 1.5s for 5s, then result. idle_timeout_sec=2.
       Must NOT abort — idle timer resets per event. Pins the per-event
       reset semantics (else cumulative wall-clock would fire).
  3. test_idle_timeout_default_is_60s
       Module-level constant ``_DEFAULT_IDLE_TIMEOUT_SEC`` exists and
       equals None (disabled by default). Pins the default contract at module load time so a
       behavior-only assertion isn't required (which would need a 180s
       sleep to verify behaviorally).
  4. test_idle_timeout_only_applies_to_stream_json_path
       Caller-supplied ``--output-format text`` routes via
       _communicate_legacy (llm_subprocess.py:617+). idle_timeout_sec
       must be IGNORED on that path — no in-stream events to monitor.
       Pins the asymmetry: idle-timeout is stream-json-only.
  5. test_idle_timeout_emits_aborted_reason_in_telemetry
       On idle abort, ``subprocess_exited`` payload carries
       ``aborted_reason="idle_timeout"`` (proposed canonical field name —
       grep showed no prior abort_reason / aborted_reason convention).
  6. test_overall_timeout_still_applies_as_ceiling
       Events every 0.5s (under idle budget), but timeout_sec=3 ceiling
       fires. Must yield E_LLM_TIMEOUT (NOT E_LLM_NO_PROGRESS). Pins
       orthogonality: idle and overall are independent limits.
  7. test_idle_timeout_zero_or_negative_disables_watchdog
       ``idle_timeout_sec=0`` (or None). 5s of idle then result event.
       Must NOT abort — escape hatch. Pins disable semantics.
  8. test_invoke_fix_llm_uses_idle_timeout
       phase_6_review.py:1285 _invoke_fix_llm passes explicit
       ``idle_timeout_sec`` to invoke_llm_subprocess. Spec calls for
       60s default for fix_llm steps. Pins workflow-layer wiring.
"""
from __future__ import annotations

import inspect
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


# ---------------------------------------------------------------------------
# §1i autouse teardown: neutralise emit_resolver_resolved (prevents disk writes)
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_backends_and_telemetry(monkeypatch):
    monkeypatch.setattr(llm_subprocess, "emit_resolver_resolved", lambda *a, **kw: None)
    telemetry_ctx.clear_current_run()
    yield
    telemetry_ctx.clear_current_run()


# ─── Helpers ──────────────────────────────────────────────────────────────────


class _FakeEventLog:
    """Captures (event_type, payload, run_id) tuples for assertion."""

    def __init__(self):
        self.events: list[tuple[str, dict, str]] = []

    def append(self, event_type: str, payload: dict, run_id: str | None = None) -> None:
        self.events.append((event_type, payload, run_id or "ad-hoc"))


def _ndjson(*events: str) -> str:
    """Join NDJSON event lines with trailing \\n on each."""
    return "".join(line if line.endswith("\n") else line + "\n" for line in events)


_SYSTEM_INIT = '{"type":"system","subtype":"init","session_id":"s1"}'
_RESULT_OK = (
    '{"type":"result","subtype":"success",'
    '"result":"FINAL_ANSWER_TEXT",'
    '"usage":{"input_tokens":10,"output_tokens":20,'
    '"cache_read_input_tokens":0,"cache_creation_input_tokens":0},'
    '"total_cost_usd":0.0123,"duration_ms":500}'
)


class _TimedStdout:
    """File-like stdout whose ``readline()`` returns queued lines on a
    schedule, then blocks indefinitely past the longest schedule entry.

    Each entry in ``schedule`` is a tuple ``(delay_sec, line)``: readline
    sleeps ``delay_sec`` then returns ``line``. After the schedule is
    exhausted, readline blocks for ``block_sec`` (default 30s) and
    returns "" (EOF). The block simulates a real subprocess that has
    stopped emitting events but hasn't EOF'd — exactly the hang shape
    775D6752 targets.
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
            # Block until killed (proc.kill flips the close flag indirectly
            # via close()). Sleep in small chunks so test cleanup is fast.
            end = time.monotonic() + self._block_sec
            while time.monotonic() < end and not self._closed.is_set():
                time.sleep(0.05)
            return ""
        delay, line = self._schedule[self._idx]
        self._idx += 1
        # Honour close() during the sleep so kill() promptly unblocks us.
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
    """Mock Popen result whose stdout is the supplied object (for timed streams)."""
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
    """Mock Popen with a plain StringIO stdout (no per-line scheduling)."""
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


# ─── Test 1: idle-timeout kills subprocess when no events flow ───────────────


def test_idle_timeout_kills_subprocess_when_no_events():
    """One system-init event, then forever-silence. idle_timeout_sec=2,
    timeout_sec=60. Must abort within ~3s (well below outer ceiling) with
    E_LLM_NO_PROGRESS / recoverable=True / proc.kill() called.

    Pins the new in-stream watchdog: today's _stream_read_events
    (llm_subprocess.py:444+) joins reader_thread on ``_remaining()`` —
    the OUTER budget. With idle-timeout, the reader must wake every
    idle_timeout_sec (or use a polling join) and check that ``len(events)``
    has grown since the last check; if not, kill + return E_LLM_NO_PROGRESS.
    """
    schedule = [(0.05, _SYSTEM_INIT)]  # one event then forever-block
    stdout_obj = _TimedStdout(schedule, block_sec=30.0)
    proc = _stream_proc_with_stdout(stdout_obj, returncode=None)

    started = time.monotonic()
    with patch("bytedigger_engine.llm_subprocess.subprocess.Popen", return_value=proc):
        result = invoke_llm_subprocess(
            prompt="x",
            model="sonnet",
            timeout_sec=60,
            step_name="s",
            idle_timeout_sec=2,
        )
    elapsed = time.monotonic() - started

    assert elapsed < 6, (
        "775D6752: idle-timeout must fire within ~3s (idle_timeout_sec=2 "
        "+ kill drain), NOT block to outer timeout_sec=60. observed elapsed="
        + repr(elapsed) + "s — looks like watchdog never fired"
    )
    assert result.status == "error", (
        "775D6752: post-idle-timeout result must be status=error; got "
        + repr(result.status)
    )
    assert result.error_code == "E_LLM_NO_PROGRESS", (
        "775D6752: idle-timeout abort must yield error_code=E_LLM_NO_PROGRESS "
        "(matches E_LLM_* family — distinct from E_LLM_TIMEOUT for the outer "
        "ceiling). got: " + repr(result.error_code)
    )
    assert result.recoverable is True, (
        "775D6752: E_LLM_NO_PROGRESS must be recoverable=True so retry-loop "
        "kicks in (transient model hiccup); got " + repr(result.recoverable)
    )
    assert proc.kill.called, (
        "775D6752: idle-timeout abort must call proc.kill() — otherwise the "
        "subprocess leaks. proc.kill.called=" + repr(proc.kill.called)
    )


# ─── Test 2: each event resets the idle timer ────────────────────────────────


def test_idle_timeout_resets_on_each_event():
    """Events every 1.5s for ~5s, then result. idle_timeout_sec=2.
    Each event arrives BEFORE the 2s idle budget elapses — no abort.

    Pins per-event reset semantics. If the watchdog were cumulative
    wall-clock instead of per-event, it would fire after 2s even though
    events keep flowing.
    """
    # Three system events at 1.5s intervals (well under the 2s idle budget),
    # then the result event at +1.5s. Total stream = ~6s, all under
    # idle_timeout_sec=2 between consecutive events.
    schedule = [
        (1.5, _SYSTEM_INIT),
        (1.5, _SYSTEM_INIT),
        (1.5, _SYSTEM_INIT),
        (1.5, _RESULT_OK),
    ]
    stdout_obj = _TimedStdout(schedule, block_sec=2.0)
    proc = _stream_proc_with_stdout(stdout_obj, returncode=0)

    started = time.monotonic()
    with patch("bytedigger_engine.llm_subprocess.subprocess.Popen", return_value=proc):
        result = invoke_llm_subprocess(
            prompt="x",
            model="sonnet",
            timeout_sec=20,
            step_name="s",
            idle_timeout_sec=2,
        )
    elapsed = time.monotonic() - started

    assert result.status == "ok", (
        "775D6752: events arriving every 1.5s under a 2s idle budget must "
        "NOT trigger idle-timeout — timer resets per event. got status="
        + repr(result.status) + " err=" + repr(result.error)
    )
    assert result.data["raw_response"] == "FINAL_ANSWER_TEXT", (
        "775D6752: with per-event reset, raw_response must extract normally. "
        "got: " + repr(result.data.get("raw_response") if result.data else None)
    )
    assert not proc.kill.called, (
        "775D6752: per-event reset means kill() must NOT fire — events kept "
        "the timer alive. proc.kill.called=" + repr(proc.kill.called)
    )
    # Sanity: total wall-clock should be ~6s (4 × 1.5s); upper-bound generous
    # for thread scheduling.
    assert elapsed < 12, (
        "775D6752: stream completed but total elapsed is suspicious. "
        "elapsed=" + repr(elapsed) + "s"
    )


# ─── Test 3: module-level default is 60s ─────────────────────────────────────


def test_idle_timeout_default_is_60s():
    """Module-level constant ``_DEFAULT_IDLE_TIMEOUT_SEC`` exists and equals None (disabled by default).

    Verifying via behavior would require a 180s+ sleep — instead we pin
    the contract at module load time. The constant is the documented
    default referenced by both invoke_llm_subprocess (when
    idle_timeout_sec kwarg omitted) and any caller that wants to read
    the same value. Disabled by default (None); explicit opt-in via idle_timeout_sec=N.

    Also checks the kwarg signature — invoke_llm_subprocess must accept
    ``idle_timeout_sec`` as a keyword-only parameter.
    """
    from bytedigger_engine import llm_subprocess  # local import — module is in sys.path

    # 1. Module-level constant must exist and equal None (disabled by default).
    assert hasattr(llm_subprocess, "_DEFAULT_IDLE_TIMEOUT_SEC"), (
        "775D6752: llm_subprocess module must expose "
        "_DEFAULT_IDLE_TIMEOUT_SEC at module level (disabled by default; explicit opt-in via idle_timeout_sec=N). "
        "Module attrs: "
        + repr([a for a in dir(llm_subprocess) if "IDLE" in a.upper()])
    )
    assert llm_subprocess._DEFAULT_IDLE_TIMEOUT_SEC is None, (
        "775D6752: _DEFAULT_IDLE_TIMEOUT_SEC must equal None (disabled by default); got " + repr(llm_subprocess._DEFAULT_IDLE_TIMEOUT_SEC)
    )

    # 2. Kwarg signature: invoke_llm_subprocess accepts idle_timeout_sec.
    sig = inspect.signature(invoke_llm_subprocess)
    assert "idle_timeout_sec" in sig.parameters, (
        "775D6752: invoke_llm_subprocess must accept ``idle_timeout_sec`` "
        "as a keyword param; current signature: " + repr(sig)
    )
    param = sig.parameters["idle_timeout_sec"]
    assert param.kind == inspect.Parameter.KEYWORD_ONLY, (
        "775D6752: idle_timeout_sec must be keyword-only (matches the rest "
        "of invoke_llm_subprocess); got kind=" + repr(param.kind)
    )


# ─── Test 4: legacy path ignores idle_timeout_sec ────────────────────────────


def test_idle_timeout_only_applies_to_stream_json_path():
    """25e75663 Class C: After the command=→model= migration, invoke_llm_subprocess
    always uses stream-json (output_format_auto_injected=True unconditionally in
    _invoke_subprocess). The caller-supplied --output-format text legacy path no
    longer exists through invoke_llm_subprocess(model=...).

    This test is adapted to verify that idle_timeout_sec with a stream-json
    response that arrives quickly does NOT trigger an idle abort — stream-json
    path with fast result is ok regardless of idle_timeout_sec value.
    """
    # Fast result event — arrives well before any idle timeout would fire.
    schedule = [
        (0.05, _SYSTEM_INIT),
        (0.05, _RESULT_OK),
    ]
    stdout_obj = _TimedStdout(schedule, block_sec=2.0)
    proc = _stream_proc_with_stdout(stdout_obj, returncode=0)

    started = time.monotonic()
    with patch("bytedigger_engine.llm_subprocess.subprocess.Popen", return_value=proc):
        result = invoke_llm_subprocess(
            prompt="x",
            model="sonnet",
            timeout_sec=10,
            step_name="s",
            idle_timeout_sec=5,  # generous idle budget — fast result won't hit it
        )
    elapsed = time.monotonic() - started

    assert result.status == "ok", (
        "775D6752 (adapted): stream-json path with fast result must succeed "
        "even with idle_timeout_sec set; got status=" + repr(result.status)
        + " err=" + repr(result.error)
    )
    assert result.error_code is None, (
        "775D6752 (adapted): fast result must NOT trigger idle abort; "
        "got error_code=" + repr(result.error_code)
    )
    assert not proc.kill.called, (
        "775D6752 (adapted): fast result must NOT cause kill(); "
        "proc.kill.called=" + repr(proc.kill.called)
    )
    # Sanity: completed quickly.
    assert elapsed < 3, (
        "775D6752 (adapted): fast stream should complete quickly; "
        "elapsed=" + repr(elapsed) + "s"
    )


# ─── Test 5: telemetry payload carries aborted_reason on idle abort ──────────


def test_idle_timeout_emits_aborted_reason_in_telemetry():
    """On idle-timeout abort, ``subprocess_exited`` payload must carry
    ``aborted_reason="idle_timeout"``.

    Spec ambiguity: no existing aborted_reason / abort_reason field in
    llm_subprocess.py (verified via grep — see module docstring).
    Proposed canonical name: ``aborted_reason`` on subprocess_exited.
    Future overall-timeout aborts could reuse the same key with value
    ``"timeout"`` for symmetry, but that's out of scope here.

    Pins the new telemetry field added inside _stream_read_events on
    the idle-abort branch (llm_subprocess.py:556 reader join → kill
    handler that emits the abort reason).
    """
    schedule = [(0.05, _SYSTEM_INIT)]
    stdout_obj = _TimedStdout(schedule, block_sec=30.0)
    proc = _stream_proc_with_stdout(stdout_obj, returncode=None)

    log = _FakeEventLog()
    telemetry_ctx.set_current_run(
        event_log=log, run_id="r-idle", step_name="s", phase="p"
    )
    try:
        with patch("bytedigger_engine.llm_subprocess.subprocess.Popen", return_value=proc):
            invoke_llm_subprocess(
                prompt="x",
                model="sonnet",
                timeout_sec=60,
                step_name="s",
                idle_timeout_sec=1,
            )
    finally:
        telemetry_ctx.clear_current_run()

    exited = [e for e in log.events if e[0] == "subprocess_exited"]
    assert len(exited) == 1, (
        "775D6752: idle abort must still emit exactly one subprocess_exited "
        "telemetry event (lifecycle invariant); got "
        + repr([e[0] for e in log.events])
    )
    payload = exited[0][1]
    assert payload.get("aborted_reason") == "idle_timeout", (
        "775D6752: subprocess_exited payload must carry "
        "aborted_reason='idle_timeout' on idle abort. "
        "(Canonical field name proposed by 775D6752 RED tests — no prior "
        "aborted_reason / abort_reason convention in llm_subprocess.py.) "
        "got payload keys: " + repr(list(payload.keys()))
        + " aborted_reason=" + repr(payload.get("aborted_reason"))
    )


# ─── Test 6: outer timeout_sec still applies when idle timer is happy ────────


def test_overall_timeout_still_applies_as_ceiling():
    """Events flow every 0.5s (well under idle budget=2). But outer
    timeout_sec=3 — so the ceiling fires before the result event lands.
    Must yield E_LLM_TIMEOUT (NOT E_LLM_NO_PROGRESS).

    Pins orthogonality: idle-timeout and overall timeout are independent
    safety nets. A buggy GREEN that conflated them (e.g. only enforcing
    idle-timeout, dropping the outer ceiling) would let runaway streams
    burn arbitrary wall-clock as long as events keep dribbling out.
    """
    # Steady event flow at 0.5s intervals — well under idle_timeout_sec=2.
    # No result event for ~10s, but timeout_sec=3 fires first.
    schedule = [
        (0.5, _SYSTEM_INIT),
        (0.5, _SYSTEM_INIT),
        (0.5, _SYSTEM_INIT),
        (0.5, _SYSTEM_INIT),
        (0.5, _SYSTEM_INIT),
        (0.5, _SYSTEM_INIT),
        (0.5, _SYSTEM_INIT),
        (0.5, _SYSTEM_INIT),
        (0.5, _SYSTEM_INIT),
        (0.5, _SYSTEM_INIT),
        (0.5, _SYSTEM_INIT),
        (0.5, _SYSTEM_INIT),
        (0.5, _SYSTEM_INIT),
        (0.5, _SYSTEM_INIT),
        (0.5, _SYSTEM_INIT),
        (0.5, _SYSTEM_INIT),
        (0.5, _SYSTEM_INIT),
        (0.5, _SYSTEM_INIT),
        (0.5, _SYSTEM_INIT),
        (0.5, _SYSTEM_INIT),
        (0.5, _RESULT_OK),
    ]
    stdout_obj = _TimedStdout(schedule, block_sec=2.0)
    proc = _stream_proc_with_stdout(stdout_obj, returncode=None)

    started = time.monotonic()
    with patch("bytedigger_engine.llm_subprocess.subprocess.Popen", return_value=proc):
        result = invoke_llm_subprocess(
            prompt="x",
            model="sonnet",
            timeout_sec=3,
            step_name="s",
            idle_timeout_sec=2,
        )
    elapsed = time.monotonic() - started

    assert result.status == "error", (
        "775D6752: outer timeout_sec=3 must still fire even when events "
        "keep coming under idle budget. got status=" + repr(result.status)
    )
    assert result.error_code == "E_LLM_TIMEOUT", (
        "775D6752: outer ceiling abort must yield E_LLM_TIMEOUT (NOT "
        "E_LLM_NO_PROGRESS — events were arriving). orthogonality contract: "
        "idle_timeout and timeout_sec are independent. got: "
        + repr(result.error_code)
    )
    assert proc.kill.called, (
        "775D6752: outer ceiling must still call proc.kill(); "
        "proc.kill.called=" + repr(proc.kill.called)
    )
    # Sanity: aborted near the outer ceiling (3s + drain margin), not at
    # the idle window (2s).
    assert 2.5 <= elapsed < 6, (
        "775D6752: outer ceiling timing — expected ~3s + drain, got "
        + repr(elapsed) + "s"
    )


# ─── Test 7: zero / None disables the idle watchdog ──────────────────────────


def test_idle_timeout_zero_or_negative_disables_watchdog():
    """``idle_timeout_sec=0`` (or None) disables the idle watchdog.
    Even with 5s of complete idle followed by a result event, the call
    must succeed under the outer timeout_sec=20.

    Pins the disable-semantics escape hatch for callers who explicitly
    want only the outer ceiling (legacy behavior parity).
    """
    # 5s of idle (no events) then result event — would trigger any
    # reasonable idle watchdog (default 60 wouldn't catch this in test
    # time, but a small explicit value would). With idle_timeout_sec=0,
    # the watchdog is DISABLED — must succeed.
    schedule = [
        (5.0, _SYSTEM_INIT),
        (0.1, _RESULT_OK),
    ]
    stdout_obj = _TimedStdout(schedule, block_sec=2.0)
    proc = _stream_proc_with_stdout(stdout_obj, returncode=0)

    started = time.monotonic()
    with patch("bytedigger_engine.llm_subprocess.subprocess.Popen", return_value=proc):
        result = invoke_llm_subprocess(
            prompt="x",
            model="sonnet",
            timeout_sec=20,
            step_name="s",
            idle_timeout_sec=0,  # DISABLED
        )
    elapsed = time.monotonic() - started

    assert result.status == "ok", (
        "775D6752: idle_timeout_sec=0 disables the watchdog; 5s idle then "
        "result must succeed. got status=" + repr(result.status)
        + " err=" + repr(result.error)
    )
    assert result.data["raw_response"] == "FINAL_ANSWER_TEXT", (
        "775D6752: with watchdog disabled, raw_response must extract "
        "normally; got: "
        + repr(result.data.get("raw_response") if result.data else None)
    )
    assert not proc.kill.called, (
        "775D6752: idle_timeout_sec=0 must NOT cause kill(); "
        "proc.kill.called=" + repr(proc.kill.called)
    )
    # Sanity: ran past the 5s idle window (proves the gap was real).
    assert elapsed >= 4.5, (
        "775D6752: test fixture didn't actually idle for 5s; elapsed="
        + repr(elapsed) + "s"
    )


# ─── Test 9: success-path telemetry omits aborted_reason ─────────────────────


def test_success_path_omits_aborted_reason():
    """Locked spec: aborted_reason is OMITTED/None on success; MUST NOT be
    "idle_timeout".

    Pins the negative branch of test 5 — closes the fabrication path where
    a buggy GREEN hardcodes ``aborted_reason="idle_timeout"`` on every
    subprocess_exited (success included). Without this scenario, test 5
    passes with a literal that burns observability for the success path.

    Spec resolution (Opus 2026-05-06): success/exit/result-error paths
    either OMIT the key or set it to None. NOT "idle_timeout".
    """

    # Result event lands quickly — well under both idle (1s) and outer (60s).
    schedule = [
        (0.05, _SYSTEM_INIT),
        (0.05, _RESULT_OK),
    ]
    stdout_obj = _TimedStdout(schedule, block_sec=2.0)
    proc = _stream_proc_with_stdout(stdout_obj, returncode=0)

    log = _FakeEventLog()
    telemetry_ctx.set_current_run(
        event_log=log, run_id="r-ok", step_name="s", phase="p"
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

    assert result.status == "ok", (
        "775D6752 Scenario 9: result event lands well under idle/outer "
        "ceilings — must succeed. got status=" + repr(result.status)
        + " err=" + repr(result.error)
    )
    assert not proc.kill.called, (
        "775D6752 Scenario 9: no abort path on success — kill() must NOT "
        "be called. proc.kill.called=" + repr(proc.kill.called)
    )

    exited = [e for e in log.events if e[0] == "subprocess_exited"]
    assert len(exited) == 1, (
        "775D6752 Scenario 9: success path must still emit exactly one "
        "subprocess_exited (lifecycle invariant); got "
        + repr([e[0] for e in log.events])
    )
    payload = exited[0][1]
    # Locked spec: success path either OMITS aborted_reason OR sets to None.
    # MUST NOT be "idle_timeout" (would mean GREEN hardcoded the literal).
    aborted_reason = payload.get("aborted_reason")
    assert aborted_reason is None, (
        "775D6752 Scenario 9: success-path subprocess_exited payload must "
        "either omit ``aborted_reason`` OR set it to None — NOT "
        "'idle_timeout'. A buggy GREEN that hardcodes the field on every "
        "emit would burn observability and make test 5 fabrication-prone. "
        "got payload keys: " + repr(list(payload.keys()))
        + " aborted_reason=" + repr(aborted_reason)
    )
    # Sanity: confirm exit_code reflects clean exit.
    assert payload.get("exit_code") == 0, (
        "775D6752 Scenario 9: success path must emit exit_code=0; got "
        + repr(payload.get("exit_code"))
    )


# ─── Test 10: outer-timeout abort does NOT mislabel as idle_timeout ──────────


def test_outer_timeout_abort_not_labeled_idle():
    """Locked spec: outer-timeout abort MUST NOT set aborted_reason to
    "idle_timeout"; the value is OMITTED or None or "timeout" — anything
    EXCEPT "idle_timeout".

    Pins the telemetry-orthogonality gap left by test 6 (which only checks
    error_code). A buggy GREEN that conflates kills (since both go through
    proc.kill() on the reader-thread join) could still mislabel the outer
    abort as idle_timeout. This forces GREEN to track WHICH watchdog fired,
    not just THAT a watchdog fired (likely via a local ``idle_aborted: bool``).

    Setup: events flow steadily every 0.5s under idle_timeout_sec=10 (so
    idle would never fire), but outer timeout_sec=2 fires first.
    """

    # Steady event flow at 0.5s intervals — well under idle_timeout_sec=10.
    # No result event for ~10s, but timeout_sec=2 fires first.
    schedule = [
        (0.5, _SYSTEM_INIT),
        (0.5, _SYSTEM_INIT),
        (0.5, _SYSTEM_INIT),
        (0.5, _SYSTEM_INIT),
        (0.5, _SYSTEM_INIT),
        (0.5, _SYSTEM_INIT),
        (0.5, _SYSTEM_INIT),
        (0.5, _SYSTEM_INIT),
        (0.5, _SYSTEM_INIT),
        (0.5, _SYSTEM_INIT),
        (0.5, _SYSTEM_INIT),
        (0.5, _SYSTEM_INIT),
        (0.5, _SYSTEM_INIT),
        (0.5, _SYSTEM_INIT),
        (0.5, _SYSTEM_INIT),
        (0.5, _SYSTEM_INIT),
        (0.5, _RESULT_OK),
    ]
    stdout_obj = _TimedStdout(schedule, block_sec=2.0)
    proc = _stream_proc_with_stdout(stdout_obj, returncode=None)

    log = _FakeEventLog()
    telemetry_ctx.set_current_run(
        event_log=log, run_id="r-outer", step_name="s", phase="p"
    )
    try:
        with patch("bytedigger_engine.llm_subprocess.subprocess.Popen", return_value=proc):
            result = invoke_llm_subprocess(
                prompt="x",
                model="sonnet",
                timeout_sec=2,
                step_name="s",
                idle_timeout_sec=10,
            )
    finally:
        telemetry_ctx.clear_current_run()

    # Error code orthogonality (echoed from test 6 — reaffirm here so
    # this test fails loudly if GREEN regresses error_code separately).
    assert result.status == "error", (
        "775D6752 Scenario 10: outer timeout_sec=2 must fire even when "
        "events keep flowing under idle_timeout_sec=10. got status="
        + repr(result.status)
    )
    assert result.error_code == "E_LLM_TIMEOUT", (
        "775D6752 Scenario 10: outer-ceiling abort must yield "
        "E_LLM_TIMEOUT (NOT E_LLM_NO_PROGRESS — events were arriving). "
        "got: " + repr(result.error_code)
    )

    # Telemetry-side orthogonality (the actual addition).
    exited = [e for e in log.events if e[0] == "subprocess_exited"]
    assert len(exited) == 1, (
        "775D6752 Scenario 10: outer-timeout abort must emit exactly one "
        "subprocess_exited (lifecycle invariant); got "
        + repr([e[0] for e in log.events])
    )
    payload = exited[0][1]
    aborted_reason = payload.get("aborted_reason")
    assert aborted_reason != "idle_timeout", (
        "775D6752 Scenario 10: outer-ceiling kill MUST NOT mislabel "
        "aborted_reason='idle_timeout' — that label is reserved for "
        "the in-stream idle watchdog. Acceptable values: OMITTED, None, "
        "or 'timeout' (per spec resolution). A buggy GREEN that conflates "
        "kills (since both go through proc.kill() on reader-thread join) "
        "would fail this assertion. got aborted_reason="
        + repr(aborted_reason) + " payload keys: " + repr(list(payload.keys()))
    )


# ─── Tests F4F26513: cli_lingered_post_result salvage path ───────────────────
#
# These tests pin AC1, AC2+AC3, and AC7 from spec
# SHARED/memory/Decisions/2026-05-10_F4F26513_engine_cli_lingers_salvage_spec.md
#
# TODAY STATE (RED phase): all three tests FAIL because:
#   - _stream_read_events line 775-791 does `pass` on the result_event_seen
#     branch — no kill, no cli_lingered_post_result event emitted.
#   - proc.wait(timeout=5) raises TimeoutExpired → warning only.
#   - proc.returncode stays None → exit_code = -1 → E_LLM_EXIT failure.
#   GREEN will implement: kill + emit cli_lingered_post_result + return exit_code=0.


def _lingering_proc_after_result(result_line: str | None = None) -> MagicMock:
    """Build a mock Popen whose stdout delivers system-init + result events
    quickly, then hangs — simulating the confirmed CLI linger bug.

    Stdout schedule:
      - 0.02s: system/init event
      - 0.02s: result event (stream terminates naturally from reader's view)
    After the schedule the readline blocks for 30s (simulates linger).

    proc.wait behaviour:
      - First call with timeout=5 → raises subprocess.TimeoutExpired
        (CLI hasn't EOF'd within 5s grace window).
      - Second call without timeout (kill-reap) → returns -9.

    proc.kill:
      - MagicMock; does NOT close stdout (lingering subprocess ignores kill
        initially — we still want readline to block so the reader thread is
        alive when the timeout fires). The test itself uses _TimedStdout
        with block_sec=30, so the reader thread will block naturally.
    """
    import subprocess as _subprocess

    result_event = result_line or _RESULT_OK

    stdout_obj = _TimedStdout(
        schedule=[
            (0.02, _SYSTEM_INIT),
            (0.02, result_event),
        ],
        block_sec=30.0,  # simulates CLI linger post-result
    )

    proc = MagicMock()
    proc.pid = 77777
    proc.returncode = None  # stays None until after kill
    proc.stdout = stdout_obj
    proc.stderr = MagicMock()
    proc.stderr.read = MagicMock(return_value="")
    proc.stdin = MagicMock()

    # First proc.wait(timeout=5) raises TimeoutExpired (CLI lingering, grace window).
    # Second proc.wait (post-kill, with OR without timeout) — kernel has reaped,
    # returncode=-9 set, return -9.
    def _wait_side_effect(*args, **kwargs):
        timed = "timeout" in kwargs and kwargs["timeout"] is not None
        _wait_side_effect.calls = getattr(_wait_side_effect, "calls", 0) + 1
        if timed and _wait_side_effect.calls == 1:
            # First call (before kill): CLI is still lingering, timeout expires.
            raise _subprocess.TimeoutExpired(cmd="claude", timeout=kwargs["timeout"])
        # Subsequent calls (post-kill): kernel has reaped the killed process.
        proc.returncode = -9
        return -9

    proc.wait = MagicMock(side_effect=_wait_side_effect)

    # kill() is a plain MagicMock — we assert .called but do NOT close stdout
    # (the real linger scenario: OS hasn't killed it yet when kill() returns).
    proc.kill = MagicMock()
    proc.communicate = MagicMock(return_value=("", ""))
    return proc


# ─── F4F26513 AC1: cli_lingered_post_result event is emitted ─────────────────


def test_lingering_cli_emits_cli_lingered_post_result_event():
    """AC1 (F4F26513): When _stream_read_events reaches result_event_seen=True
    AND proc.wait(timeout=5) raises TimeoutExpired, the engine MUST emit
    exactly one ``cli_lingered_post_result`` event with payload keys
    {post_result_wait_ms, events_count, has_result_event, phase, step_name}.

    TODAY: FAILS — current code takes the ``pass`` branch (line 775-791) and
    never emits this event. The proc.wait TimeoutExpired is caught with only
    a warning; no dedicated event is fired.
    """

    proc = _lingering_proc_after_result()

    log = _FakeEventLog()
    telemetry_ctx.set_current_run(
        event_log=log, run_id="r-linger-ac1", step_name="red-writer", phase="phase_5"
    )
    try:
        with patch("bytedigger_engine.llm_subprocess.subprocess.Popen", return_value=proc):
            invoke_llm_subprocess(
                prompt="x",
                model="sonnet",
                timeout_sec=60,
                step_name="red-writer",
                idle_timeout_sec=None,
            )
    finally:
        telemetry_ctx.clear_current_run()

    lingered_events = [e for e in log.events if e[0] == "cli_lingered_post_result"]
    assert len(lingered_events) == 1, (
        "F4F26513 AC1: exactly one cli_lingered_post_result event must be emitted "
        "when result_event_seen=True AND proc.wait raises TimeoutExpired. "
        "got count=" + repr(len(lingered_events))
        + " all events: " + repr([e[0] for e in log.events])
    )
    payload = lingered_events[0][1]
    required_keys = {"post_result_wait_ms", "events_count", "has_result_event", "phase", "step_name"}
    missing = required_keys - set(payload.keys())
    assert not missing, (
        "F4F26513 AC1: cli_lingered_post_result payload missing required keys: "
        + repr(missing) + " got keys: " + repr(list(payload.keys()))
    )
    assert payload["has_result_event"] is True, (
        "F4F26513 AC1: has_result_event must be True (result event was present). "
        "got: " + repr(payload.get("has_result_event"))
    )
    assert payload["phase"] == "phase_5", (
        "F4F26513 AC1: phase must match run_ctx.phase='phase_5'. "
        "got: " + repr(payload.get("phase"))
    )
    assert payload["step_name"] == "red-writer", (
        "F4F26513 AC1: step_name must match step_name='red-writer'. "
        "got: " + repr(payload.get("step_name"))
    )
    assert isinstance(payload["post_result_wait_ms"], (int, float)), (
        "F4F26513 AC1: post_result_wait_ms must be numeric; "
        "got: " + repr(payload.get("post_result_wait_ms"))
    )
    assert isinstance(payload["events_count"], int), (
        "F4F26513 AC1: events_count must be int; "
        "got: " + repr(payload.get("events_count"))
    )


# ─── 15D89831 AC1: no unbounded proc.wait in cli_lingered salvage path ──────


def test_cli_lingered_salvage_no_unbounded_proc_wait():
    """15D89831 AC1: every proc.wait() call during the cli_lingered_post_result
    salvage path MUST be invoked with a non-None timeout.

    Rationale: an unbounded proc.wait() in the salvage chain hangs forever if
    the kernel never reaps the killed subprocess (real-world zombie scenario).
    The mock fixture used to mask this by returning -9 on the no-timeout call;
    after the 15D89831 fixture rewrite, all timed waits post-kill reap, so any
    unbounded call would now be exposed.

    TODAY (pre-GREEN): FAILS — llm_subprocess.py:867 calls proc.wait() with no
    timeout. Will PASS after GREEN replaces it with proc.wait(timeout=...).
    """

    proc = _lingering_proc_after_result()

    log = _FakeEventLog()
    telemetry_ctx.set_current_run(
        event_log=log, run_id="r-15D89831-ac1", step_name="red-writer", phase="phase_5"
    )
    try:
        with patch("bytedigger_engine.llm_subprocess.subprocess.Popen", return_value=proc):
            invoke_llm_subprocess(
                prompt="",
                model="sonnet",
                timeout_sec=10,
                step_name="red-writer",
                idle_timeout_sec=None,
            )
    finally:
        telemetry_ctx.clear_current_run()

    unbounded_calls = []
    for call in proc.wait.call_args_list:
        args, kwargs = call
        # Either timeout passed positionally or as kwarg, both must be non-None.
        timeout_kw = kwargs.get("timeout", None) if "timeout" in kwargs else None
        timeout_pos = args[0] if args else None
        if timeout_kw is None and timeout_pos is None:
            unbounded_calls.append(call)
    assert not unbounded_calls, (
        f"Found {len(unbounded_calls)} unbounded proc.wait call(s) in salvage path: "
        f"{unbounded_calls!r}. Every wait must have timeout!=None to prevent zombie hang."
    )


# ─── F4F26513 AC2+AC3: lingering CLI is killed and caller sees success ────────


def test_lingering_cli_kills_and_returns_success():
    """AC2+AC3 (F4F26513): On the result_event_seen + proc.wait TimeoutExpired
    subpath, the engine MUST:
      AC2: call proc.kill() (then proc.wait() without timeout to reap).
      AC3: return status='ok' with raw_response extracted from the result
           event text. Caller MUST NOT see exit_code=-1 or error_code set.

    TODAY: FAILS on all three assertions:
      - proc.kill.called is False (code takes the pass branch).
      - result.status is 'error' (exit_code=-1 → E_LLM_EXIT branch fires).
      - result.error_code is 'E_LLM_EXIT' (not None).
    """

    proc = _lingering_proc_after_result()

    with patch("bytedigger_engine.llm_subprocess.subprocess.Popen", return_value=proc):
        result = invoke_llm_subprocess(
            prompt="x",
            model="sonnet",
            timeout_sec=60,
            step_name="s",
            idle_timeout_sec=None,
        )

    # AC2: proc.kill() must have been called to reap the lingering CLI.
    assert proc.kill.called, (
        "F4F26513 AC2: proc.kill() MUST be called on the cli_lingered_post_result "
        "subpath (result event seen, proc.wait raised TimeoutExpired). "
        "Current code takes ``pass`` branch — kill never fires. "
        "proc.kill.called=" + repr(proc.kill.called)
    )

    # AC3a: caller must see success (not E_LLM_EXIT).
    assert result.status == "ok", (
        "F4F26513 AC3: result.status must be 'ok' when CLI lingers post-result — "
        "the work was demonstrably done (result event present). "
        "Current code: proc.returncode=None → exit_code=-1 → E_LLM_EXIT error. "
        "got status=" + repr(result.status)
        + " error=" + repr(result.error)
        + " error_code=" + repr(result.error_code)
    )

    # AC3b: error_code must be absent/None on success.
    assert result.error_code is None, (
        "F4F26513 AC3: error_code must be None on salvage-success path. "
        "got: " + repr(result.error_code)
    )

    # AC3c: raw_response must be extracted from the result event text.
    assert result.data is not None, (
        "F4F26513 AC3: result.data must not be None on success; "
        "got data=" + repr(result.data)
    )
    assert result.data.get("raw_response") == "FINAL_ANSWER_TEXT", (
        "F4F26513 AC3: raw_response must be 'FINAL_ANSWER_TEXT' extracted from "
        "the result event (same _extract_result_text_from_events semantics). "
        "got: " + repr(result.data.get("raw_response") if result.data else None)
    )


# ─── F4F26513 AC7: lingering kill omits aborted_reason (regression guard) ────


def test_lingering_cli_kills_but_omits_aborted_reason():
    """AC7 (F4F26513): On the cli_lingered_post_result subpath, proc.kill IS
    called (AC2) but subprocess_exited payload MUST NOT carry
    aborted_reason='idle_timeout' (or any other non-None aborted_reason).
    The salvage signal lives in the dedicated cli_lingered_post_result event;
    aborted_reason stays None to preserve AC4 semantics (idle-watchdog path
    remains distinguishable).

    Also guards AC6: the existing test_success_path_omits_aborted_reason
    asserts ``not proc.kill.called`` on the CLEAN success path. This test
    uses the LINGERING fixture (proc.wait raises TimeoutExpired) so the two
    fixtures are orthogonal — clean success still passes its assertion.

    TODAY: FAILS because the engine currently returns error (exit_code=-1)
    before the subprocess_exited emission guard even produces a payload we
    can inspect for aborted_reason. After GREEN ships, this test confirms
    the aborted_reason discriminator is correct.
    """
    import subprocess as _subprocess


    proc = _lingering_proc_after_result()

    log = _FakeEventLog()
    telemetry_ctx.set_current_run(
        event_log=log, run_id="r-linger-ac7", step_name="s", phase="p"
    )
    try:
        with patch("bytedigger_engine.llm_subprocess.subprocess.Popen", return_value=proc):
            result = invoke_llm_subprocess(
                prompt="x",
                model="sonnet",
                timeout_sec=60,
                step_name="s",
                idle_timeout_sec=None,
            )
    finally:
        telemetry_ctx.clear_current_run()

    # AC7a: kill was called (GREEN implements salvage).
    assert proc.kill.called, (
        "F4F26513 AC7: proc.kill must be called on the lingering subpath "
        "(same as AC2). proc.kill.called=" + repr(proc.kill.called)
    )

    # AC7b: subprocess_exited event must exist (lifecycle invariant).
    exited = [e for e in log.events if e[0] == "subprocess_exited"]
    assert len(exited) == 1, (
        "F4F26513 AC7: exactly one subprocess_exited must be emitted even on "
        "the lingering salvage path (lifecycle invariant). "
        "got: " + repr([e[0] for e in log.events])
    )
    payload = exited[0][1]

    # AC7c: aborted_reason must be absent OR None (NOT 'idle_timeout' or any
    # other string) — the salvage signal lives in cli_lingered_post_result.
    aborted_reason = payload.get("aborted_reason")
    assert aborted_reason is None, (
        "F4F26513 AC7: subprocess_exited aborted_reason must be None on the "
        "cli_lingered_post_result salvage path. Setting it to a non-None value "
        "would conflate idle-watchdog kills (AC4) with the new salvage path. "
        "Acceptable: key absent or value None. "
        "got aborted_reason=" + repr(aborted_reason)
        + " payload keys: " + repr(list(payload.keys()))
    )

    # AC7d: cross-check result status (catches regressions where GREEN fixes
    # aborted_reason but forgets to fix the return value).
    assert result.status == "ok", (
        "F4F26513 AC7: salvage path must return status='ok'; "
        "got: " + repr(result.status)
    )


# ─── Tests 3C54A029 ───────────────────────────────────────────────────────────
#
# Polish items from Phase 6 reviewer findings on F4F26513 (commit af98f128).
# These tests FAIL today against the current llm_subprocess.py because:
#   - POST_RESULT_WAIT_SEC constant does not exist (import fails → all 5 fail)
#   - Line 262 hardcodes 5000 instead of POST_RESULT_WAIT_SEC * 1000
#   - Line 849 proc.wait() has no timeout kwarg
#   - subprocess_exited payload has no kernel_returncode key on salvage path
#
# GREEN will: add POST_RESULT_WAIT_SEC=5 constant, replace literals, add
# timeout on post-kill reap, capture kernel_returncode pre-override.

import subprocess as _subprocess_module  # noqa: E402 (module-level for zombie helper)


def _clean_proc_after_result() -> MagicMock:
    """Build a mock Popen that completes cleanly — no linger, no kill needed.

    Stdout schedule: system-init + result event, then EOF quickly.
    proc.wait(timeout=N) returns 0 immediately — no TimeoutExpired.
    Used for AC4: clean path must NOT have kernel_returncode in subprocess_exited.
    """
    stdout_obj = _TimedStdout(
        schedule=[
            (0.02, _SYSTEM_INIT),
            (0.02, _RESULT_OK),
        ],
        block_sec=0.05,  # short block then EOF — no linger
    )
    proc = MagicMock()
    proc.pid = 88888
    proc.returncode = 0
    proc.stdout = stdout_obj
    proc.stderr = MagicMock()
    proc.stderr.read = MagicMock(return_value="")
    proc.stdin = MagicMock()
    proc.wait = MagicMock(return_value=0)  # clean exit, no TimeoutExpired
    proc.kill = MagicMock()
    proc.communicate = MagicMock(return_value=("", ""))
    return proc


def _lingering_proc_zombie() -> MagicMock:
    """Build a mock Popen whose proc.wait raises TimeoutExpired on EVERY call.

    Simulates a zombie process that cannot be reaped even after SIGKILL.
    Both the grace wait (timeout=5) AND the post-kill reap raise TimeoutExpired.
    Used for AC5: engine must log warning and continue — no hang, no uncaught exception.
    """
    stdout_obj = _TimedStdout(
        schedule=[
            (0.02, _SYSTEM_INIT),
            (0.02, _RESULT_OK),
        ],
        block_sec=30.0,  # simulates linger
    )

    proc = MagicMock()
    proc.pid = 99999
    proc.returncode = None  # never reaped
    proc.stdout = stdout_obj
    proc.stderr = MagicMock()
    proc.stderr.read = MagicMock(return_value="")
    proc.stdin = MagicMock()

    def _always_timeout(*args, **kwargs):
        raise _subprocess_module.TimeoutExpired(cmd="claude", timeout=kwargs.get("timeout", 5))

    proc.wait = MagicMock(side_effect=_always_timeout)
    proc.kill = MagicMock()
    proc.communicate = MagicMock(return_value=("", ""))
    return proc


# ─── AC1: post_result_wait_ms uses the named constant ────────────────────────


def test_3C54A029_post_result_wait_ms_uses_constant():
    """AC1 (3C54A029): POST_RESULT_WAIT_SEC module constant exists and equals 5.
    The cli_lingered_post_result event payload key post_result_wait_ms equals
    POST_RESULT_WAIT_SEC * 1000 (not a hardcoded 5000 literal).

    TODAY: FAILS at import — POST_RESULT_WAIT_SEC does not exist in llm_subprocess.py.
    After GREEN: import succeeds, constant == 5, payload field == 5000.
    """
    from bytedigger_engine.llm_subprocess import POST_RESULT_WAIT_SEC  # noqa: E402 — FAILS today (constant missing)
    # Constant value check.
    assert POST_RESULT_WAIT_SEC == 5, (
        "3C54A029 AC1: POST_RESULT_WAIT_SEC must equal 5; "
        "got: " + repr(POST_RESULT_WAIT_SEC)
    )


    proc = _lingering_proc_after_result()

    log = _FakeEventLog()
    telemetry_ctx.set_current_run(
        event_log=log, run_id="r-3c54a029-ac1", step_name="s", phase="p"
    )
    try:
        with patch("bytedigger_engine.llm_subprocess.subprocess.Popen", return_value=proc):
            invoke_llm_subprocess(
                prompt="x",
                model="sonnet",
                timeout_sec=60,
                step_name="s",
                idle_timeout_sec=None,
            )
    finally:
        telemetry_ctx.clear_current_run()

    lingered = [e for e in log.events if e[0] == "cli_lingered_post_result"]
    assert len(lingered) == 1, (
        "3C54A029 AC1: exactly one cli_lingered_post_result event must be emitted. "
        "got count=" + repr(len(lingered))
        + " all events: " + repr([e[0] for e in log.events])
    )
    payload = lingered[0][1]
    expected_ms = POST_RESULT_WAIT_SEC * 1000
    assert payload["post_result_wait_ms"] == expected_ms, (
        "3C54A029 AC1: post_result_wait_ms must equal POST_RESULT_WAIT_SEC * 1000 = "
        + repr(expected_ms) + "; today's code hardcodes 5000 literal unlinked "
        "from the proc.wait(timeout=5) call — drift risk. got: "
        + repr(payload.get("post_result_wait_ms"))
    )


# ─── AC2: post-kill proc.wait is called with a timeout kwarg ─────────────────


def test_3C54A029_post_kill_wait_has_timeout():
    """AC2 (3C54A029): The post-kill reap proc.wait() (second call, after salvage
    proc.kill()) MUST be called with timeout=POST_RESULT_WAIT_SEC.

    TODAY: FAILS — line 849 calls proc.wait() with no kwargs.
    Inspecting proc.wait.call_args_list[1].kwargs gives {} → assertion fails.
    After GREEN: call_args_list[1].kwargs["timeout"] == POST_RESULT_WAIT_SEC.
    """
    from bytedigger_engine.llm_subprocess import POST_RESULT_WAIT_SEC  # noqa: E402 — FAILS today (constant missing)

    proc = _lingering_proc_after_result()

    with patch("bytedigger_engine.llm_subprocess.subprocess.Popen", return_value=proc):
        invoke_llm_subprocess(
            prompt="x",
            model="sonnet",
            timeout_sec=60,
            step_name="s",
            idle_timeout_sec=None,
        )

    # proc.wait must have been called at least twice:
    # call 0: proc.wait(timeout=5) → raises TimeoutExpired (grace window)
    # call 1: proc.wait(timeout=POST_RESULT_WAIT_SEC) → reaps after kill
    assert proc.wait.call_count >= 2, (
        "3C54A029 AC2: proc.wait must be called at least twice on salvage path "
        "(grace + reap). call_count=" + repr(proc.wait.call_count)
    )
    reap_call = proc.wait.call_args_list[1]
    reap_kwargs = reap_call.kwargs if hasattr(reap_call, "kwargs") else reap_call[1]
    assert "timeout" in reap_kwargs, (
        "3C54A029 AC2: post-kill proc.wait() must have a timeout= kwarg; "
        "today line 849 has bare proc.wait() with no args — zombie risk. "
        "got call_args_list[1]=" + repr(reap_call)
    )
    assert reap_kwargs["timeout"] == POST_RESULT_WAIT_SEC, (
        "3C54A029 AC2: post-kill reap timeout must equal POST_RESULT_WAIT_SEC="
        + repr(POST_RESULT_WAIT_SEC) + "; got: " + repr(reap_kwargs.get("timeout"))
    )


# ─── AC3: kernel_returncode captured in subprocess_exited on salvage path ────


def test_3C54A029_kernel_returncode_in_subprocess_exited_on_salvage():
    """AC3 (3C54A029): On salvage path (cli_lingered=True), subprocess_exited
    payload must include kernel_returncode == proc.returncode after kill+reap (-9).
    The caller-facing exit_code must still be 0 (success preserved).

    TODAY: FAILS — subprocess_exited payload has no kernel_returncode key.
    KeyError or assertion failure depending on how GREEN is implemented.
    """
    from bytedigger_engine.llm_subprocess import POST_RESULT_WAIT_SEC  # noqa: E402 — FAILS today (constant missing)

    proc = _lingering_proc_after_result()

    log = _FakeEventLog()
    telemetry_ctx.set_current_run(
        event_log=log, run_id="r-3c54a029-ac3", step_name="s", phase="p"
    )
    try:
        with patch("bytedigger_engine.llm_subprocess.subprocess.Popen", return_value=proc):
            result = invoke_llm_subprocess(
                prompt="x",
                model="sonnet",
                timeout_sec=60,
                step_name="s",
                idle_timeout_sec=None,
            )
    finally:
        telemetry_ctx.clear_current_run()

    # Salvage must succeed (AC3 is a forensics enrichment, not a failure gate).
    assert result.status == "ok", (
        "3C54A029 AC3 pre-condition: salvage path must return ok; "
        "got status=" + repr(result.status)
    )

    exited = [e for e in log.events if e[0] == "subprocess_exited"]
    assert len(exited) == 1, (
        "3C54A029 AC3: exactly one subprocess_exited must be emitted on salvage path; "
        "got: " + repr([e[0] for e in log.events])
    )
    payload = exited[0][1]

    # Forensics: kernel returncode must be captured BEFORE exit_code is overridden.
    assert "kernel_returncode" in payload, (
        "3C54A029 AC3: subprocess_exited payload must include kernel_returncode "
        "on salvage path — today proc.returncode=-9 is lost when exit_code=0 "
        "is set (line 269 overwrites before payload built). "
        "got payload keys: " + repr(list(payload.keys()))
    )
    assert payload["kernel_returncode"] == -9, (
        "3C54A029 AC3: kernel_returncode must be -9 (SIGKILL returncode set by "
        "_wait_side_effect after proc.kill). got: " + repr(payload.get("kernel_returncode"))
    )

    # Caller-facing success must be preserved.
    assert payload["exit_code"] == 0, (
        "3C54A029 AC3: exit_code in subprocess_exited must be 0 (salvage success "
        "override preserved). got: " + repr(payload.get("exit_code"))
    )


# ─── AC4: kernel_returncode absent on clean-success path ─────────────────────


def test_3C54A029_kernel_returncode_absent_on_clean_path():
    """AC4 (3C54A029): On the clean-success path (cli_lingered=False, no kill),
    subprocess_exited payload MUST NOT include kernel_returncode (key absent, not None).

    TODAY: FAILS — POST_RESULT_WAIT_SEC does not exist in llm_subprocess.py.
    After GREEN: import succeeds, and the clean path omits the key.
    """
    from bytedigger_engine.llm_subprocess import POST_RESULT_WAIT_SEC  # noqa: E402 — FAILS today (constant missing)

    proc = _clean_proc_after_result()

    log = _FakeEventLog()
    telemetry_ctx.set_current_run(
        event_log=log, run_id="r-3c54a029-ac4", step_name="s", phase="p"
    )
    try:
        with patch("bytedigger_engine.llm_subprocess.subprocess.Popen", return_value=proc):
            result = invoke_llm_subprocess(
                prompt="x",
                model="sonnet",
                timeout_sec=60,
                step_name="s",
                idle_timeout_sec=None,
            )
    finally:
        telemetry_ctx.clear_current_run()

    assert result.status == "ok", (
        "3C54A029 AC4 pre-condition: clean path must succeed; "
        "got status=" + repr(result.status) + " err=" + repr(result.error)
    )

    exited = [e for e in log.events if e[0] == "subprocess_exited"]
    assert len(exited) == 1, (
        "3C54A029 AC4: exactly one subprocess_exited on clean path; "
        "got: " + repr([e[0] for e in log.events])
    )
    payload = exited[0][1]

    assert "kernel_returncode" not in payload, (
        "3C54A029 AC4: clean-success subprocess_exited MUST NOT include "
        "kernel_returncode — key must be absent (not None). Only the salvage "
        "path (cli_lingered=True) should carry this field. "
        "got payload keys: " + repr(list(payload.keys()))
    )


# ─── AC5: zombie proc.wait on post-kill reap does not hang ───────────────────


def test_3C54A029_zombie_post_kill_wait_does_not_hang():
    """AC5 (3C54A029): When proc.wait raises TimeoutExpired on BOTH grace and
    post-kill reap calls, the engine must log a warning and continue — no hang,
    no uncaught exception. Function must return a tuple normally.

    TODAY: FAILS — POST_RESULT_WAIT_SEC does not exist in llm_subprocess.py.
    After GREEN: both assertions pass — timeout kwarg present on reap call,
    and function returns without raising despite double-TimeoutExpired.
    """
    from bytedigger_engine.llm_subprocess import POST_RESULT_WAIT_SEC  # noqa: E402 — FAILS today (constant missing)

    proc = _lingering_proc_zombie()

    # Assert: function must NOT raise even with double-TimeoutExpired.
    t0 = time.monotonic()
    with patch("bytedigger_engine.llm_subprocess.subprocess.Popen", return_value=proc):
        result = invoke_llm_subprocess(
            prompt="x",
            model="sonnet",
            timeout_sec=60,
            step_name="s",
            idle_timeout_sec=None,
        )
    elapsed = time.monotonic() - t0

    # Function must return normally — no uncaught exception.
    assert result is not None, (
        "3C54A029 AC5: invoke_llm_subprocess must return a result tuple even "
        "when both proc.wait calls raise TimeoutExpired (zombie scenario). "
        "An uncaught exception would have propagated before this assertion."
    )

    # Salvage flag must still be set (result seen before zombie reap failure).
    assert result.status == "ok", (
        "3C54A029 AC5: zombie scenario must still return ok — result event was "
        "seen before the zombie reap failure; cli_lingered_after_result=True. "
        "got status=" + repr(result.status) + " err=" + repr(result.error)
    )

    # The post-kill reap proc.wait call must have had a timeout kwarg.
    # (Mirrors AC2 assertion — zombie fixture confirms it's also present here.)
    assert proc.wait.call_count >= 2, (
        "3C54A029 AC5: proc.wait must be called at least twice (grace + reap); "
        "call_count=" + repr(proc.wait.call_count)
    )
    reap_call = proc.wait.call_args_list[1]
    reap_kwargs = reap_call.kwargs if hasattr(reap_call, "kwargs") else reap_call[1]
    assert "timeout" in reap_kwargs, (
        "3C54A029 AC5: post-kill reap proc.wait() must have timeout= kwarg even "
        "in zombie scenario. Today line 849 has bare proc.wait() — this assertion "
        "fails because call_args_list[1].kwargs == {}. "
        "got reap call=" + repr(reap_call)
    )
    assert reap_kwargs["timeout"] == POST_RESULT_WAIT_SEC, (
        "3C54A029 AC5: reap timeout must equal POST_RESULT_WAIT_SEC="
        + repr(POST_RESULT_WAIT_SEC) + "; got: " + repr(reap_kwargs.get("timeout"))
    )

    # Wall-clock sanity: mock-based, so completes fast regardless.
    assert elapsed < 3.0, (
        "3C54A029 AC5: zombie invocation should complete quickly with mocks "
        "(no real sleeps on double-TimeoutExpired path); elapsed="
        + repr(elapsed) + "s"
    )


# ─── Test 11: idle > outer cannot extend the deadline ────────────────────────


def test_idle_timeout_capped_to_overall_timeout():
    """Locked spec: ``idle_timeout_sec > timeout_sec`` is silently capped —
    idle limit cannot extend the outer deadline. Effective per-poll budget
    is min(remaining_budget, idle_timeout_sec).

    Setup: ``idle_timeout_sec=600`` (idle budget never expires in test
    time) but ``timeout_sec=3``. Stream emits one event then idles forever.
    Without the cap, a buggy GREEN that uses idle_timeout_sec as the
    effective budget would block 600+s. With the cap, outer ceiling fires
    at 3s with E_LLM_TIMEOUT.

    Pins that a misconfigured caller (e.g. fix_llm_timeout_sec=300 +
    idle_timeout_sec accidentally bumped to 600) cannot regress the
    23680DDA outer-ceiling guarantee.
    """

    schedule = [(0.05, _SYSTEM_INIT)]  # one event then forever-block
    stdout_obj = _TimedStdout(schedule, block_sec=30.0)
    proc = _stream_proc_with_stdout(stdout_obj, returncode=None)

    started = time.monotonic()
    with patch("bytedigger_engine.llm_subprocess.subprocess.Popen", return_value=proc):
        result = invoke_llm_subprocess(
            prompt="x",
            model="sonnet",
            timeout_sec=3,
            step_name="s",
            idle_timeout_sec=600,  # absurd — must be capped to 3 (outer)
        )
    elapsed = time.monotonic() - started

    # Wall-clock must be bounded by outer ceiling, NOT idle budget.
    assert elapsed < 4, (
        "775D6752 Scenario 11: idle_timeout_sec=600 MUST NOT extend the "
        "outer timeout_sec=3 deadline. Effective per-poll budget must be "
        "min(remaining, idle) — i.e. capped to 3s here. observed elapsed="
        + repr(elapsed) + "s — looks like idle limit shadowed the outer "
        "ceiling (regression vs 23680DDA)"
    )
    assert result.status == "error", (
        "775D6752 Scenario 11: outer ceiling must fire with idle > outer; "
        "got status=" + repr(result.status)
    )
    # Per locked spec for this scenario: "no later than outer ceiling +
    # drain" — must be E_LLM_TIMEOUT (idle budget=600 hasn't expired).
    assert result.error_code == "E_LLM_TIMEOUT", (
        "775D6752 Scenario 11: with idle_timeout_sec=600 and timeout_sec=3, "
        "the only watchdog whose budget expired in test time is the OUTER "
        "ceiling — must yield E_LLM_TIMEOUT (NOT E_LLM_NO_PROGRESS, since "
        "the 600s idle budget hasn't elapsed). got: " + repr(result.error_code)
    )
    assert proc.kill.called, (
        "775D6752 Scenario 11: outer ceiling must still call proc.kill(); "
        "proc.kill.called=" + repr(proc.kill.called)
    )


