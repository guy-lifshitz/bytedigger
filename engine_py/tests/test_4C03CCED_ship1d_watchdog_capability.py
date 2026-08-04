"""RED tests for 4C03CCED Ship 1D — G2 backend-agnostic watchdog capability contract.

Spec: SHARED/memory/Decisions/2026-05-23_4C03CCED_ship1d_watchdog_parity_spec.md
Agreement: 4C03CCED (parent SYSTEMATIC — runner contract, sub-ship 1D of 4, final)

10 tests: G2-AC1a, G2-AC1b, G2-AC1c, G2-AC1d, G2-AC2, G2-AC3, G2-AC4, G2-AC5, G2-AC-N1.

Pre-GREEN expected FAIL (5): AC1a, AC1b, AC1d, AC1c, AC-N1 — symbols
  _BACKEND_CAPABILITIES, _assert_backend_supports_watchdog, E_LLM_WATCHDOG_UNSUPPORTED,
  and event "runner_capability_probe_failed" do NOT exist yet.
Pre-GREEN expected PASS (5): AC2, AC3, AC4, AC5 — regression-fences on existing
  claude-subprocess watchdog behavior that Ship 1D must preserve byte-for-byte.
  (AC1c is listed as FAIL because it asserts the ABSENCE of
  runner_capability_probe_failed for claude-subprocess; pre-GREEN the probe machinery
  does not exist yet — see test docstring for exact forcing reason.)

§1q: NO spec_from_file_location / module_from_spec / exec_module.
  Grandfathered sys.path.insert pattern (matches all sibling tests in this dir).
§1i: G2-AC5 pre-stages _StragglerWatchdog.aborted = True via monkeypatch BEFORE
  invoking the unit; no racy thread sleep. Cites workflows.md §1i (ABD52613).
§1l: AC1a asserts returned StepResult.error_code + filesystem inspection (no nonce
  dir). AC-N1 is the N>1 multi-call forcing function (6AD2F3F8).
"""
from __future__ import annotations

import io
import os
import re
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

HERE = Path(__file__).parent
ENGINE_ROOT = HERE.parent

# Grandfathered sys.path pattern — matches all sibling tests in this dir.
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

from bytedigger_engine import llm_subprocess as _llm_module  # noqa: E402
from bytedigger_engine.llm_subprocess import invoke_llm_subprocess  # noqa: E402
from bytedigger_engine import telemetry_ctx  # noqa: E402


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

class _FakeEventLog:
    """Captures (event_type, payload, run_id) tuples for assertion."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict, str]] = []

    def append(self, event_type: str, payload: dict, run_id: str | None = None) -> None:
        self.events.append((event_type, dict(payload), run_id or "ad-hoc"))

    def by_type(self, event_type: str) -> list[dict]:
        """Return payloads for all events of the given type."""
        return [payload for (et, payload, _) in self.events if et == event_type]


def _minimal_mock_proc(pid: int = 99999) -> MagicMock:
    """Minimal subprocess.Popen mock that won't block."""
    proc = MagicMock()
    proc.pid = pid
    proc.returncode = 0
    proc.stdout = io.StringIO("")
    proc.stderr = io.StringIO("")
    proc.stdin = MagicMock()
    proc.wait = MagicMock(return_value=0)
    proc.kill = MagicMock()
    proc.communicate = MagicMock(return_value=("", ""))
    proc.poll = MagicMock(return_value=0)
    return proc


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clear_ctx():
    """Ensure no stale RunContext leaks between tests."""
    telemetry_ctx.clear_current_run()
    yield
    telemetry_ctx.clear_current_run()


@pytest.fixture
def event_log():
    return _FakeEventLog()


@pytest.fixture
def active_run_ctx(event_log):
    """Set an active RunContext with a real event log for telemetry capture."""
    telemetry_ctx.set_current_run(
        event_log=event_log,
        run_id="RUN-1D",
        step_name="test_step",
        phase="phase_test",
    )
    yield event_log
    telemetry_ctx.clear_current_run()


# ---------------------------------------------------------------------------
# G2-AC1a — claude-in-session + idle_timeout_sec=60 → E_LLM_WATCHDOG_UNSUPPORTED
#            AND no nonce dir created (never entered _invoke_in_session)
# ---------------------------------------------------------------------------

def test_g2_ac1a_in_session_idle_watchdog_fail_closed(
    tmp_path: Path, active_run_ctx: _FakeEventLog, monkeypatch
) -> None:
    """G2-AC1a: invoke_llm_subprocess(backend='claude-in-session', idle_timeout_sec=60)
    must return E_LLM_WATCHDOG_UNSUPPORTED (status='error', recoverable=False)
    WITHOUT entering _invoke_in_session — no nonce dir created under request_dir.

    Pre-GREEN FAIL: _BACKEND_CAPABILITIES and _assert_backend_supports_watchdog do
    not exist yet → the capability probe is absent → the call passes through to
    _invoke_in_session (which may fail with E_LLM_RUN_ID_MISSING or succeed), but
    it does NOT return E_LLM_WATCHDOG_UNSUPPORTED.

    §1l layer 4+5: error_code assertion = layer 4 (production counter);
    filesystem inspection (no nonce dir) = layer 5.
    """
    request_dir = str(tmp_path / "requests")
    monkeypatch.setenv("HAL_RUNNER_REQUEST_DIR", request_dir)

    # Provide a valid run_id so _invoke_in_session wouldn't fail on E_LLM_RUN_ID_MISSING
    # (we need to distinguish the probe-fail from a run-id-fail)
    # active_run_ctx already sets RUN-1D via telemetry_ctx.set_current_run

    result = invoke_llm_subprocess(
        prompt="test prompt",
        model="sonnet",
        timeout_sec=30,
        step_name="test_step",
        backend="claude-in-session",
        idle_timeout_sec=60,
    )

    assert result.status == "error", (
        f"G2-AC1a: expected status='error'; got {result.status!r}"
    )
    assert result.error_code == "E_LLM_WATCHDOG_UNSUPPORTED", (
        f"G2-AC1a: expected error_code='E_LLM_WATCHDOG_UNSUPPORTED' (probe fail-closed); "
        f"got {result.error_code!r}. Note: if E_LLM_RUN_ID_MISSING, probe is missing "
        f"and call entered _invoke_in_session; if E_LLM_NO_RESULT_EVENT, in-session ran."
    )
    assert result.recoverable is False, (
        f"G2-AC1a: E_LLM_WATCHDOG_UNSUPPORTED must be recoverable=False (config error); "
        f"got recoverable={result.recoverable!r}"
    )

    # Filesystem check: _invoke_in_session creates nonce dirs under request_dir.
    # If ANY dir was created under request_dir, the probe failed to short-circuit.
    request_path = tmp_path / "requests"
    if request_path.exists():
        # Walk subtree for any .req.json artifacts
        req_files = list(request_path.rglob("*.req.json"))
        assert len(req_files) == 0, (
            f"G2-AC1a: found {len(req_files)} .req.json file(s) under request_dir — "
            f"_invoke_in_session was entered despite idle watchdog request; "
            f"probe must short-circuit BEFORE in-session dispatch. Files: {req_files!r}"
        )


# ---------------------------------------------------------------------------
# G2-AC1b — claude-in-session + straggler_cfg → E_LLM_WATCHDOG_UNSUPPORTED
#            AND event log carries runner_capability_probe_failed with correct payload
# ---------------------------------------------------------------------------

def test_g2_ac1b_in_session_straggler_fail_closed(
    tmp_path: Path, active_run_ctx: _FakeEventLog, monkeypatch
) -> None:
    """G2-AC1b: invoke_llm_subprocess(backend='claude-in-session',
    straggler_cfg={'reviews_dir': ..., 'expected_n': 2}) returns
    E_LLM_WATCHDOG_UNSUPPORTED and emits runner_capability_probe_failed
    with payload {backend: 'claude-in-session', reason: 'no_watchdog_support',
    idle_enabled: False, straggler_enabled: True}.

    Pre-GREEN FAIL: probe machinery does not exist → call does NOT return
    E_LLM_WATCHDOG_UNSUPPORTED AND event log does NOT carry
    runner_capability_probe_failed.

    §1l layer 4 (N=1 counter via event-log read).
    """
    request_dir = str(tmp_path / "requests")
    reviews_dir = tmp_path / "reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("HAL_RUNNER_REQUEST_DIR", request_dir)

    result = invoke_llm_subprocess(
        prompt="test prompt",
        model="sonnet",
        timeout_sec=30,
        step_name="test_step",
        backend="claude-in-session",
        idle_timeout_sec=0,
        straggler_cfg={"reviews_dir": str(reviews_dir), "expected_n": 2},
    )

    assert result.status == "error", (
        f"G2-AC1b: expected status='error'; got {result.status!r}"
    )
    assert result.error_code == "E_LLM_WATCHDOG_UNSUPPORTED", (
        f"G2-AC1b: expected error_code='E_LLM_WATCHDOG_UNSUPPORTED'; "
        f"got {result.error_code!r}"
    )
    assert result.recoverable is False, (
        f"G2-AC1b: recoverable must be False; got {result.recoverable!r}"
    )

    # Event log MUST carry runner_capability_probe_failed with the exact payload
    probe_events = active_run_ctx.by_type("runner_capability_probe_failed")
    assert len(probe_events) >= 1, (
        f"G2-AC1b: expected at least 1 'runner_capability_probe_failed' event; "
        f"got {len(probe_events)}. All events: "
        f"{[(et, p) for et, p, _ in active_run_ctx.events]!r}"
    )
    payload = probe_events[0]
    assert payload.get("backend") == "claude-in-session", (
        f"G2-AC1b: event payload backend must be 'claude-in-session'; got {payload!r}"
    )
    assert payload.get("reason") == "no_watchdog_support", (
        f"G2-AC1b: event payload reason must be 'no_watchdog_support'; got {payload!r}"
    )
    assert payload.get("idle_enabled") is False, (
        f"G2-AC1b: idle_enabled must be False (idle_timeout_sec=0); got {payload!r}"
    )
    assert payload.get("straggler_enabled") is True, (
        f"G2-AC1b: straggler_enabled must be True (straggler_cfg provided); got {payload!r}"
    )


# ---------------------------------------------------------------------------
# G2-AC1c — claude-subprocess + idle_timeout_sec=60 → probe passes, no
#            runner_capability_probe_failed event in log
# ---------------------------------------------------------------------------

def test_g2_ac1c_claude_subprocess_idle_watchdog_passes_probe(
    active_run_ctx: _FakeEventLog, monkeypatch
) -> None:
    """G2-AC1c: invoke_llm_subprocess(backend='claude-subprocess', idle_timeout_sec=60)
    must NOT emit runner_capability_probe_failed with reason='no_watchdog_support'.
    The probe returns None for claude-subprocess (has progress_since+abort caps).

    Pre-GREEN FAIL reasoning: pre-GREEN there is NO probe machinery at all, so
    the call proceeds to _stream_read_events. The test asserts absence of
    runner_capability_probe_failed; this absence holds pre-GREEN (event never
    emitted without the probe). HOWEVER, the test also asserts the call proceeds
    past the probe point (no E_LLM_WATCHDOG_UNSUPPORTED returned) — this passes
    pre-GREEN too. The REAL failure condition for this test post-GREEN: it must
    pass the probe for claude-subprocess, which is the behavior this test pins.

    §1l layer 5 (negative event-log assertion: probe-failed event ABSENT).

    NOTE: Pre-GREEN this test PASSES (no probe exists, no event emitted,
    mock spawn is reached). This is a regression-fence: after GREEN inserts
    the probe, claude-subprocess must still pass through cleanly.
    """
    # Mock _stream_read_events to return a synthetic idle_aborted=True result
    # immediately so the call terminates quickly without a real subprocess.
    def _fake_stream(*args, **kwargs):
        # Returns (timed_out, stdout, stderr, events, idle_aborted, cli_lingered)
        # Simulate idle abort so we get a defined StepResult (E_LLM_NO_PROGRESS)
        # without needing a real subprocess — but the key is: no probe event fired.
        return (False, "", "", [], True, False)

    popen_mock = _minimal_mock_proc()

    with patch("bytedigger_engine.llm_subprocess.subprocess.Popen", return_value=popen_mock), \
         patch("bytedigger_engine.llm_subprocess._stream_read_events", side_effect=_fake_stream):
        result = invoke_llm_subprocess(
            prompt="x",
            model="sonnet",
            timeout_sec=30,
            step_name="test_step",
            backend="claude-subprocess",
            idle_timeout_sec=60,
        )

    # MUST NOT have returned E_LLM_WATCHDOG_UNSUPPORTED
    assert result.error_code != "E_LLM_WATCHDOG_UNSUPPORTED", (
        f"G2-AC1c: claude-subprocess must NOT get E_LLM_WATCHDOG_UNSUPPORTED from probe; "
        f"got error_code={result.error_code!r}"
    )

    # MUST NOT have runner_capability_probe_failed with no_watchdog_support in log
    probe_events = [
        p for p in active_run_ctx.by_type("runner_capability_probe_failed")
        if p.get("reason") == "no_watchdog_support"
    ]
    assert len(probe_events) == 0, (
        f"G2-AC1c: claude-subprocess must NOT emit runner_capability_probe_failed "
        f"with reason='no_watchdog_support'; found {len(probe_events)} such events: "
        f"{probe_events!r}"
    )


# ---------------------------------------------------------------------------
# G2-AC1d — claude-in-session + idle_timeout_sec=0, straggler_cfg=None →
#            probe no-op, _invoke_in_session IS entered
# ---------------------------------------------------------------------------

def test_g2_ac1d_in_session_no_watchdog_request_passes(
    tmp_path: Path, active_run_ctx: _FakeEventLog, monkeypatch
) -> None:
    """G2-AC1d: invoke_llm_subprocess(backend='claude-in-session',
    idle_timeout_sec=0, straggler_cfg=None) — probe is no-op (returns None
    unconditionally when both disabled). _invoke_in_session IS entered.

    Evidence that _invoke_in_session was entered: it either
    (a) creates a nonce dir under request_dir (file-system evidence), OR
    (b) returns E_LLM_NO_RESULT_EVENT after bounded poll timeout (only
        reachable from _invoke_in_session path, NOT from probe-fail path
        which returns E_LLM_WATCHDOG_UNSUPPORTED).

    Pre-GREEN FAIL: _assert_backend_supports_watchdog does not exist yet,
    but the probe itself doesn't block anything pre-GREEN (no probe code).
    Pre-GREEN, the call already enters _invoke_in_session. So this test
    PASSES pre-GREEN (existing behavior). It is a regression-fence: after
    GREEN inserts the probe, watchdog-OFF calls must still enter in-session.

    §1l layer 5 (filesystem or terminal-path observation).
    """
    request_dir = str(tmp_path / "requests")
    monkeypatch.setenv("HAL_RUNNER_REQUEST_DIR", request_dir)
    # Use a very short timeout so the bounded poll exits quickly
    result = invoke_llm_subprocess(
        prompt="test",
        model="sonnet",
        timeout_sec=2,
        step_name="test_step",
        backend="claude-in-session",
        idle_timeout_sec=0,
        straggler_cfg=None,
    )

    # Must NOT be E_LLM_WATCHDOG_UNSUPPORTED — probe must not have fired
    assert result.error_code != "E_LLM_WATCHDOG_UNSUPPORTED", (
        f"G2-AC1d: watchdog-OFF call must NOT return E_LLM_WATCHDOG_UNSUPPORTED; "
        f"got error_code={result.error_code!r}. Probe fired when it should have been no-op."
    )

    # Evidence that _invoke_in_session was entered:
    # Either a nonce dir was created (file written), OR we got E_LLM_NO_RESULT_EVENT
    # (the only terminal that _invoke_in_session produces on bounded-poll timeout).
    request_path = tmp_path / "requests"
    nonce_dir_created = request_path.exists() and any(request_path.rglob("*.req.json"))
    in_session_terminal = result.error_code in (
        "E_LLM_NO_RESULT_EVENT",
        "E_LLM_RESULT_MALFORMED",
        "E_LLM_RUN_ID_MISSING",
    )
    assert nonce_dir_created or in_session_terminal, (
        f"G2-AC1d: _invoke_in_session was NOT entered. Neither a nonce req file exists "
        f"under request_dir nor was an in-session terminal error returned. "
        f"error_code={result.error_code!r}, status={result.status!r}. "
        f"request_path exists={request_path.exists()}"
    )


# ---------------------------------------------------------------------------
# G2-AC2 — regression-fence: idle abort on claude-subprocess → E_LLM_NO_PROGRESS
#           (byte-identical to pre-Ship-1D behavior)
# ---------------------------------------------------------------------------

def test_g2_ac2_idle_abort_error_code_byte_identical_baseline(monkeypatch) -> None:
    """G2-AC2: mocked _stream_read_events returning idle_aborted=True on
    claude-subprocess yields StepResult(status='error',
    error_code='E_LLM_NO_PROGRESS', recoverable=True).

    This pins the existing idle-abort shape so Ship 1D's capability-layer
    wrapping cannot alter it for claude-subprocess callers.

    Pre-GREEN PASS: existing behavior already satisfies this; regression-fence.

    §1l layer 5 (regression-baseline equivalence).
    """
    def _fake_stream_idle(*args, **kwargs):
        # Simulate idle abort: (timed_out, stdout, stderr, events, idle_aborted, cli_lingered)
        return (False, "", "", [], True, False)

    popen_mock = _minimal_mock_proc()

    with patch("bytedigger_engine.llm_subprocess.subprocess.Popen", return_value=popen_mock), \
         patch("bytedigger_engine.llm_subprocess._stream_read_events", side_effect=_fake_stream_idle):
        result = invoke_llm_subprocess(
            prompt="x",
            model="sonnet",
            timeout_sec=60,
            step_name="s",
            backend="claude-subprocess",
            idle_timeout_sec=60,
        )

    assert result.status == "error", (
        f"G2-AC2: idle abort must return status='error'; got {result.status!r}"
    )
    assert result.error_code == "E_LLM_NO_PROGRESS", (
        f"G2-AC2: idle abort must return error_code='E_LLM_NO_PROGRESS'; "
        f"got {result.error_code!r}"
    )
    assert result.recoverable is True, (
        f"G2-AC2: E_LLM_NO_PROGRESS must be recoverable=True; got {result.recoverable!r}"
    )


# ---------------------------------------------------------------------------
# G2-AC3 — regression-fence: straggler abort on claude-subprocess →
#           status='ok', data['straggler_aborted'] is True, straggler_abort event
# ---------------------------------------------------------------------------

def test_g2_ac3_straggler_abort_step_result_shape_byte_identical_baseline(
    tmp_path: Path, active_run_ctx: _FakeEventLog, monkeypatch
) -> None:
    """G2-AC3: when _StragglerWatchdog.aborted is pre-staged True (§1i),
    invoke_llm_subprocess on claude-subprocess returns
    StepResult(status='ok', data['straggler_aborted'] == True) and emits
    'straggler_abort' event with expected_n and pid keys.

    Pre-staged (§1i / ABD52613 / workflows.md §1i): monkeypatch sets
    _StragglerWatchdog.aborted = True on the instance before the main-thread
    check runs. No racy real-thread sleep.

    Pre-GREEN PASS: existing straggler-abort shape preserved; regression-fence.

    §1l layer 5 (regression-baseline equivalence).
    """
    reviews_dir = tmp_path / "reviews"
    reviews_dir.mkdir()
    for i in range(1, 3):
        (reviews_dir / f"role-r{i}.md").write_text("stub", encoding="utf-8")

    # §1i: Pre-stage straggler condition. We monkeypatch _StragglerWatchdog.__init__
    # to set self.aborted = True immediately on construction (before start() runs
    # its thread). This deterministically ensures the aborted-check fires without
    # relying on thread timing.
    from bytedigger_engine import llm_subprocess as _llm
    original_init = _llm._StragglerWatchdog.__init__

    def _pre_staged_init(self, *, proc, reviews_dir, expected_n,
                         patience_sec, poll_interval_sec):
        original_init(
            self,
            proc=proc,
            reviews_dir=reviews_dir,
            expected_n=expected_n,
            patience_sec=patience_sec,
            poll_interval_sec=poll_interval_sec,
        )
        # Pre-stage: mark aborted immediately so the main-thread check sees it
        self.aborted = True

    monkeypatch.setattr(_llm._StragglerWatchdog, "__init__", _pre_staged_init)

    # Also patch _StragglerWatchdog.start so it doesn't spawn a real thread
    monkeypatch.setattr(_llm._StragglerWatchdog, "start", lambda self: None)

    def _fake_stream_ok(*args, **kwargs):
        # Returns without idle_aborted so straggler branch is reached
        return (False, "", "", [], False, False)

    popen_mock = _minimal_mock_proc(pid=4242)

    with patch("bytedigger_engine.llm_subprocess.subprocess.Popen", return_value=popen_mock), \
         patch("bytedigger_engine.llm_subprocess._stream_read_events", side_effect=_fake_stream_ok):
        result = invoke_llm_subprocess(
            prompt="x",
            model="sonnet",
            timeout_sec=30,
            step_name="test_step",
            backend="claude-subprocess",
            straggler_cfg={
                "reviews_dir": str(reviews_dir),
                "expected_n": 3,
            },
        )

    assert result.status == "ok", (
        f"G2-AC3: straggler-abort must return status='ok'; "
        f"got status={result.status!r} error_code={result.error_code!r}"
    )
    assert result.data is not None, "G2-AC3: result.data must not be None"
    assert result.data.get("straggler_aborted") is True, (
        f"G2-AC3: data['straggler_aborted'] must be True; "
        f"got {result.data.get('straggler_aborted')!r}"
    )

    # straggler_abort event must be in log with expected_n and pid
    straggler_events = active_run_ctx.by_type("straggler_abort")
    assert len(straggler_events) >= 1, (
        f"G2-AC3: expected 'straggler_abort' event in log; "
        f"got {len(straggler_events)}. All events: "
        f"{[(et, p) for et, p, _ in active_run_ctx.events]!r}"
    )
    ev = straggler_events[0]
    assert "expected_n" in ev, f"G2-AC3: straggler_abort event must have 'expected_n'; got {ev!r}"
    assert "pid" in ev, f"G2-AC3: straggler_abort event must have 'pid'; got {ev!r}"


# ---------------------------------------------------------------------------
# G2-AC4 — single emit site: E_LLM_NO_PROGRESS appears as error_code= exactly once
# ---------------------------------------------------------------------------

def test_g2_ac4_no_progress_single_emit_site() -> None:
    """G2-AC4: grep llm_subprocess.py body for the literal
    error_code assignment 'error_code\\s*=\\s*"E_LLM_NO_PROGRESS"' and assert
    exactly 1 match. This pins the single-source-of-truth requirement: any
    backend reaching idle-abort (via capability layer) MUST emit via this one
    site, not a new parallel emission site.

    Pre-GREEN PASS: currently 1 such match at line ~1023; regression-fence.

    §1l layer 1 (single-source-of-truth grep count).
    """
    source_path = ENGINE_ROOT / "bytedigger_engine" / "llm_subprocess.py"
    assert source_path.exists(), f"G2-AC4: llm_subprocess.py not found at {source_path}"
    source_text = source_path.read_text(encoding="utf-8")

    pattern = re.compile(r'error_code\s*=\s*"E_LLM_NO_PROGRESS"')
    matches = pattern.findall(source_text)
    assert len(matches) == 1, (
        f"G2-AC4: E_LLM_NO_PROGRESS must appear as error_code assignment exactly "
        f"ONCE in llm_subprocess.py (single source-of-truth per §2.4); "
        f"found {len(matches)} matches. Ship 1D must NOT add a second emit site."
    )


# ---------------------------------------------------------------------------
# G2-AC5 — no deadlock: straggler-abort returns within bounded wall time
# ---------------------------------------------------------------------------

def test_g2_ac5_straggler_abort_no_deadlock_wall_bound(
    tmp_path: Path, active_run_ctx: _FakeEventLog, monkeypatch
) -> None:
    """G2-AC5: straggler-abort flow on claude-subprocess returns within 5 seconds
    (generous bound — real kill flow finishes in <100ms with mocked proc).
    Asserts no deadlock between proc.kill() and _stream_read_events reader-thread.

    §1i / ABD52613 / workflows.md §1i: straggler condition pre-staged by
    monkeypatching __init__ to set self.aborted = True immediately. NEVER
    race a real thread.

    Pre-GREEN PASS: existing behavior already terminates promptly; regression-fence.

    §1l layer 4 (wall-clock bound on production-side termination).
    """
    reviews_dir = tmp_path / "reviews"
    reviews_dir.mkdir()

    from bytedigger_engine import llm_subprocess as _llm
    original_init = _llm._StragglerWatchdog.__init__

    def _pre_staged_init(self, *, proc, reviews_dir, expected_n,
                         patience_sec, poll_interval_sec):
        original_init(
            self,
            proc=proc,
            reviews_dir=reviews_dir,
            expected_n=expected_n,
            patience_sec=patience_sec,
            poll_interval_sec=poll_interval_sec,
        )
        self.aborted = True  # §1i: pre-staged, no race

    monkeypatch.setattr(_llm._StragglerWatchdog, "__init__", _pre_staged_init)
    monkeypatch.setattr(_llm._StragglerWatchdog, "start", lambda self: None)

    def _fake_stream_ok(*args, **kwargs):
        return (False, "", "", [], False, False)

    popen_mock = _minimal_mock_proc()

    started = time.monotonic()
    with patch("bytedigger_engine.llm_subprocess.subprocess.Popen", return_value=popen_mock), \
         patch("bytedigger_engine.llm_subprocess._stream_read_events", side_effect=_fake_stream_ok):
        result = invoke_llm_subprocess(
            prompt="x",
            model="sonnet",
            timeout_sec=60,
            step_name="test_step",
            backend="claude-subprocess",
            straggler_cfg={
                "reviews_dir": str(reviews_dir),
                "expected_n": 2,
            },
        )
    elapsed = time.monotonic() - started

    assert elapsed < 5.0, (
        f"G2-AC5: straggler-abort must return within 5s (no deadlock); "
        f"elapsed={elapsed:.2f}s"
    )
    assert result.status == "ok", (
        f"G2-AC5: straggler-abort must return status='ok'; "
        f"got status={result.status!r} error_code={result.error_code!r}"
    )


# ---------------------------------------------------------------------------
# G2-AC-N1 — N>1 multi-call: two sequential probe failures counted (§1l 6AD2F3F8)
# ---------------------------------------------------------------------------

def test_g2_acN1_two_sequential_probe_failures_counted(
    tmp_path: Path, active_run_ctx: _FakeEventLog, monkeypatch
) -> None:
    """G2-AC-N1 (§1l 6AD2F3F8 N>1 forcing function): two sequential
    invoke_llm_subprocess(backend='claude-in-session', idle_timeout_sec=60)
    calls in the same RunContext must each emit a runner_capability_probe_failed
    event — event log carries EXACTLY TWO such events with reason='no_watchdog_support'.

    Pre-GREEN FAIL: probe machinery does not exist yet → zero events emitted.

    §1l layer 4 (N>1 counter — N=2 events in real production event-log store).
    """
    request_dir = str(tmp_path / "requests")
    monkeypatch.setenv("HAL_RUNNER_REQUEST_DIR", request_dir)

    # First call
    result1 = invoke_llm_subprocess(
        prompt="call-1",
        model="sonnet",
        timeout_sec=30,
        step_name="test_step",
        backend="claude-in-session",
        idle_timeout_sec=60,
    )
    # Second call — same RunContext (active_run_ctx fixture did not clear)
    result2 = invoke_llm_subprocess(
        prompt="call-2",
        model="sonnet",
        timeout_sec=30,
        step_name="test_step",
        backend="claude-in-session",
        idle_timeout_sec=60,
    )

    # Both must return E_LLM_WATCHDOG_UNSUPPORTED
    assert result1.error_code == "E_LLM_WATCHDOG_UNSUPPORTED", (
        f"G2-AC-N1: call-1 must return E_LLM_WATCHDOG_UNSUPPORTED; "
        f"got {result1.error_code!r}"
    )
    assert result2.error_code == "E_LLM_WATCHDOG_UNSUPPORTED", (
        f"G2-AC-N1: call-2 must return E_LLM_WATCHDOG_UNSUPPORTED; "
        f"got {result2.error_code!r}"
    )

    # Event log must carry EXACTLY TWO runner_capability_probe_failed events
    probe_events = [
        p for p in active_run_ctx.by_type("runner_capability_probe_failed")
        if p.get("reason") == "no_watchdog_support"
    ]
    assert len(probe_events) == 2, (
        f"G2-AC-N1: expected EXACTLY 2 'runner_capability_probe_failed' events "
        f"with reason='no_watchdog_support' (one per call); "
        f"found {len(probe_events)}. All probe events: "
        f"{active_run_ctx.by_type('runner_capability_probe_failed')!r}"
    )
    # Both events must carry backend='claude-in-session'
    for i, ev in enumerate(probe_events):
        assert ev.get("backend") == "claude-in-session", (
            f"G2-AC-N1: probe event {i} must have backend='claude-in-session'; "
            f"got {ev!r}"
        )
