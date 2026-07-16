"""RED tests for CCBB65DC — phase_6 composite-reviewer straggler-abort watchdog.
Spec: SHARED/memory/Decisions/2026-05-12_CCBB65DC_straggler_watchdog_spec.md (incl. AMENDMENT 2026-05-12).
"""
from __future__ import annotations

import inspect
import subprocess
import sys
import threading
import time
import types
from pathlib import Path

import pytest  # noqa: F401

ENGINE_PY = Path(__file__).resolve().parents[1]
if str(ENGINE_PY) not in sys.path:
    sys.path.insert(0, str(ENGINE_PY))
WORKFLOWS = ENGINE_PY / "workflows"
if str(WORKFLOWS) not in sys.path:
    sys.path.insert(0, str(WORKFLOWS))

import llm_subprocess  # noqa: E402
from llm_subprocess import _StragglerWatchdog, invoke_llm_subprocess  # noqa: E402
from contracts import StepResult  # noqa: E402
from phase_6_review import _invoke_review_llm, _select_reviewers  # noqa: E402


# ─── Fixtures ────────────────────────────────────────────────────────────────


class _FakeProc:
    """Minimal fake subprocess.Popen stand-in for watchdog tests."""

    pid = 4242

    def __init__(self, *, exited: bool = False, wait_raises: bool = False) -> None:
        self._exited = exited
        self._wait_raises = wait_raises
        self.terminate_calls = 0
        self.kill_calls = 0

    def poll(self):
        return 0 if self._exited else None

    def terminate(self):
        self.terminate_calls += 1

    def kill(self):
        self.kill_calls += 1

    def wait(self, timeout=None):
        if self._wait_raises:
            raise subprocess.TimeoutExpired(cmd="x", timeout=timeout)
        return 0


def _touch_role_files(reviews_dir: Path, n: int) -> None:
    """Write role-r1.md … role-rN.md with stub content."""
    reviews_dir.mkdir(parents=True, exist_ok=True)
    for i in range(1, n + 1):
        (reviews_dir / f"role-r{i}.md").write_text("stub", encoding="utf-8")


# ─── AC1 ─────────────────────────────────────────────────────────────────────


def test_ac1_watchdog_aborts_straggler(tmp_path):
    """AC1: after patience_sec elapses with N-1 files, terminate+kill and aborted=True."""
    reviews_dir = tmp_path / "reviews"
    _touch_role_files(reviews_dir, 5)  # 5 of 6 → N-1 condition met immediately

    proc = _FakeProc(wait_raises=True)  # grace wait raises → exercises kill path
    wd = _StragglerWatchdog(
        proc=proc,
        reviews_dir=reviews_dir,
        expected_n=6,
        patience_sec=0.05,
        poll_interval_sec=0.01,
    )

    # Run in a thread with a safety timeout so test doesn't hang if run() loops
    t = threading.Thread(target=wd.run, daemon=True)
    t.start()
    t.join(timeout=2.0)
    assert not t.is_alive(), "watchdog run() did not return within 2s"

    assert proc.terminate_calls == 1, "expected exactly one terminate() call"
    assert proc.kill_calls >= 1, "expected at least one kill() call after grace wait"
    assert wd.aborted is True, "expected aborted=True after straggler kill"


# ─── AC2 ─────────────────────────────────────────────────────────────────────


def test_ac2_watchdog_no_abort_when_all_files_present(tmp_path):
    """AC2: all N files present before patience elapses → no terminate, aborted stays False."""
    reviews_dir = tmp_path / "reviews"
    _touch_role_files(reviews_dir, 6)  # full 6 of 6 → first poll sees N → returns immediately

    proc = _FakeProc(wait_raises=True)
    wd = _StragglerWatchdog(
        proc=proc,
        reviews_dir=reviews_dir,
        expected_n=6,
        patience_sec=0.05,
        poll_interval_sec=0.01,
    )

    t = threading.Thread(target=wd.run, daemon=True)
    t.start()
    t.join(timeout=2.0)
    assert not t.is_alive(), "watchdog run() did not return within 2s"

    assert proc.terminate_calls == 0, "terminate() must NOT be called when all files present"
    assert wd.aborted is False, "aborted must remain False when straggler never triggered"


# ─── AC3 ─────────────────────────────────────────────────────────────────────


def test_ac3_watchdog_no_abort_when_below_n_minus_1(tmp_path):
    """AC3: only 3 of 6 files → timer never starts, no terminate(), aborted=False.

    The proc is flipped to "exited" from a helper thread after 0.2s so run()
    terminates via the proc.poll() != None path rather than looping forever.
    """
    reviews_dir = tmp_path / "reviews"
    _touch_role_files(reviews_dir, 3)  # 3 < N-1=5, timer never starts

    proc = _FakeProc()

    def _flip_exited():
        time.sleep(0.15)
        proc._exited = True

    wd = _StragglerWatchdog(
        proc=proc,
        reviews_dir=reviews_dir,
        expected_n=6,
        patience_sec=0.05,
        poll_interval_sec=0.01,
    )

    flipper = threading.Thread(target=_flip_exited, daemon=True)
    flipper.start()

    t = threading.Thread(target=wd.run, daemon=True)
    t.start()
    t.join(timeout=2.0)
    assert not t.is_alive(), "watchdog run() did not return within 2s"

    assert proc.terminate_calls == 0, "terminate() must NOT be called when below N-1"
    assert wd.aborted is False, "aborted must remain False when timer never started"


# ─── AC4 ─────────────────────────────────────────────────────────────────────


def test_ac4_watchdog_exits_immediately_when_proc_already_done(tmp_path):
    """AC4: proc.poll() returns non-None from start → run() returns immediately, no terminate/kill."""
    reviews_dir = tmp_path / "reviews"
    _touch_role_files(reviews_dir, 5)  # N-1 files present but proc already exited

    proc = _FakeProc(exited=True)  # poll() returns 0 from the start
    wd = _StragglerWatchdog(
        proc=proc,
        reviews_dir=reviews_dir,
        expected_n=6,
        patience_sec=0.05,
        poll_interval_sec=0.01,
    )

    t = threading.Thread(target=wd.run, daemon=True)
    t.start()
    t.join(timeout=2.0)
    assert not t.is_alive(), "watchdog run() did not return within 2s"

    assert proc.terminate_calls == 0, "terminate() must not be called when proc already exited"
    assert proc.kill_calls == 0, "kill() must not be called when proc already exited"


# ─── AC5 ─────────────────────────────────────────────────────────────────────


def test_ac5_watchdog_swallows_exception_in_poll_body(tmp_path, monkeypatch):
    """AC5: glob raises RuntimeError inside poll body → run() does NOT propagate; no terminate.

    NOTE: The file-path approach (pointing reviews_dir at a regular file) was
    abandoned because Path(file).glob('role-*.md') yields nothing on CPython
    instead of raising — it does not exercise the try/except path at all.
    Instead we monkeypatch Path.glob to raise unconditionally so the poll
    body's exception-swallow contract is actually tested.
    """
    reviews_dir = tmp_path / "reviews"
    reviews_dir.mkdir()

    # Monkeypatch Path.glob to raise so every poll-body invocation raises.
    import pathlib

    def _glob_raises(self, pattern):
        raise RuntimeError("boom — injected by AC5 to test exception swallow")

    monkeypatch.setattr(pathlib.Path, "glob", _glob_raises)

    # Proc that returns None for 3 polls then 0, so the loop terminates even
    # though glob keeps raising on every iteration.
    class _CountingProc:
        pid = 5555
        terminate_calls = 0
        kill_calls = 0
        _poll_count = 0

        def poll(self):
            self._poll_count += 1
            return 0 if self._poll_count > 3 else None

        def terminate(self):
            self.terminate_calls += 1

        def kill(self):
            self.kill_calls += 1

        def wait(self, timeout=None):
            return 0

    proc = _CountingProc()

    wd = _StragglerWatchdog(
        proc=proc,
        reviews_dir=reviews_dir,
        expected_n=6,
        patience_sec=0.05,
        poll_interval_sec=0.01,
    )

    raised = []

    def _run_capturing():
        try:
            wd.run()
        except Exception as e:
            raised.append(e)

    t = threading.Thread(target=_run_capturing, daemon=True)
    t.start()
    t.join(timeout=2.0)
    assert not t.is_alive(), "watchdog run() did not return within 2s"
    assert not raised, f"run() must not propagate exceptions; got: {raised}"
    assert proc.terminate_calls == 0, "terminate() must not be called after a poll-body crash"


# ─── AC6 ─────────────────────────────────────────────────────────────────────


def test_ac6_invoke_llm_subprocess_straggler_cfg_none_no_watchdog(tmp_path, monkeypatch):
    """AC6: straggler_cfg=None → _StragglerWatchdog never instantiated.

    Also verifies the kwarg exists with default None (will fail today since kwarg absent).
    """
    # First assert the new kwarg is in the signature (fails today — kwarg doesn't exist)
    sig = inspect.signature(invoke_llm_subprocess)
    assert "straggler_cfg" in sig.parameters, (
        "invoke_llm_subprocess must accept 'straggler_cfg' kwarg"
    )
    assert sig.parameters["straggler_cfg"].default is None, (
        "straggler_cfg default must be None"
    )

    instantiated = []

    class _SpyWatchdog:
        def __init__(self, **kwargs):
            instantiated.append(kwargs)

        def start(self):
            pass

        def stop(self):
            pass

        aborted = False

    monkeypatch.setattr(llm_subprocess, "_StragglerWatchdog", _SpyWatchdog)

    # Monkeypatch subprocess.Popen to return a fake that produces immediate EOF
    class _FakePopen:
        pid = 9999
        returncode = 0

        def __init__(self, *args, **kwargs):
            pass

        @property
        def stdout(self):
            return _ImmediateEOFStream()

        @property
        def stderr(self):
            return _ImmediateEOFStream()

        @property
        def stdin(self):
            return _DevNull()

        def communicate(self, input=None, timeout=None):
            # Required by _communicate_legacy for shell-stub commands (echo path)
            return ("", "")

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

        def kill(self):
            pass

    class _ImmediateEOFStream:
        def __iter__(self):
            return iter([])

        def read(self, n=-1):
            return ""

        def close(self):
            pass

        def readline(self):
            return ""

    class _DevNull:
        def write(self, data):
            pass

        def flush(self):
            pass

        def close(self):
            pass

    monkeypatch.setattr(llm_subprocess.subprocess, "Popen", _FakePopen)

    # Call with straggler_cfg=None (the default) — spy must NOT be instantiated
    invoke_llm_subprocess(
        prompt="test",
        model="sonnet",  # 25e75663: model= replaces command=; Popen is mocked
        timeout_sec=5,
        step_name="test_ac6",
        straggler_cfg=None,
    )

    assert instantiated == [], (
        "_StragglerWatchdog must NOT be instantiated when straggler_cfg=None"
    )


# ─── AC7 ─────────────────────────────────────────────────────────────────────


def test_ac7_invoke_review_llm_passes_straggler_cfg_when_enabled(tmp_path, monkeypatch):
    """AC7: _invoke_review_llm with straggler_abort=True → correct straggler_cfg dict passed."""
    captured = {}

    def _fake_invoke(**kwargs):
        captured.update(kwargs)
        return StepResult(
            status="ok",
            data={"raw_response": "x", "response_bytes": 1, "command": []},
            duration_ms=0,
            step_name="invoke_review_llm",
        )

    import phase_6_review
    monkeypatch.setattr(phase_6_review, "invoke_llm_subprocess", _fake_invoke)

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()

    ctx = types.SimpleNamespace(
        org_config={
            "scratchpad_dir": str(scratchpad),
            "straggler_abort": True,
            # No override for patience/poll — should use module defaults
        }
    )

    prev_data = {
        "doc_path": "doc.md",
        "spec_path": "spec.md",
        "red_log_path": "red.log",
        "green_log_path": "green.log",
        "prompt": "review this",
    }
    prev = StepResult(
        status="ok",
        data=prev_data,
        duration_ms=0,
        step_name="build_review_prompt",
    )

    _invoke_review_llm(ctx, prev)

    assert "straggler_cfg" in captured, (
        "invoke_llm_subprocess must receive straggler_cfg kwarg when straggler_abort=True"
    )
    cfg = captured["straggler_cfg"]
    assert cfg is not None, "straggler_cfg must not be None when straggler_abort=True"
    assert isinstance(cfg, dict), "straggler_cfg must be a dict"

    # reviews_dir ends with /reviews
    assert cfg["reviews_dir"].endswith("/reviews"), (
        f"reviews_dir must end with '/reviews', got: {cfg['reviews_dir']!r}"
    )

    # expected_n == count from _select_reviewers (no artifact_type → FEATURE → 6)
    _, expected_count = _select_reviewers("FEATURE")
    assert cfg["expected_n"] == expected_count, (
        f"expected_n must equal len(selected_reviewers)={expected_count}, got {cfg['expected_n']}"
    )

    # patience_sec and poll_interval_sec == module defaults
    assert cfg["patience_sec"] == 60.0, (
        f"patience_sec must be 60.0 (STRAGGLER_PATIENCE_SEC), got {cfg['patience_sec']}"
    )
    assert cfg["poll_interval_sec"] == 5.0, (
        f"poll_interval_sec must be 5.0 (STRAGGLER_POLL_INTERVAL_SEC), got {cfg['poll_interval_sec']}"
    )


# ─── AC8 ─────────────────────────────────────────────────────────────────────


def test_ac8_invoke_review_llm_no_straggler_cfg_when_disabled(tmp_path, monkeypatch):
    """AC8: _invoke_review_llm with straggler_abort absent/falsy → straggler_cfg=None."""
    captured = {}

    def _fake_invoke(**kwargs):
        captured.update(kwargs)
        return StepResult(
            status="ok",
            data={"raw_response": "x", "response_bytes": 1, "command": []},
            duration_ms=0,
            step_name="invoke_review_llm",
        )

    import phase_6_review
    monkeypatch.setattr(phase_6_review, "invoke_llm_subprocess", _fake_invoke)

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()

    ctx = types.SimpleNamespace(
        org_config={
            "scratchpad_dir": str(scratchpad),
            # straggler_abort intentionally absent
        }
    )

    prev_data = {
        "doc_path": "doc.md",
        "spec_path": "spec.md",
        "red_log_path": "red.log",
        "green_log_path": "green.log",
        "prompt": "review this",
    }
    prev = StepResult(
        status="ok",
        data=prev_data,
        duration_ms=0,
        step_name="build_review_prompt",
    )

    _invoke_review_llm(ctx, prev)

    cfg = captured.get("straggler_cfg")
    assert cfg is None, (
        f"straggler_cfg must be None when straggler_abort is absent, got {cfg!r}"
    )


# ─── AC9 ─────────────────────────────────────────────────────────────────────


def test_ac9_watchdog_honours_patience_override(tmp_path):
    """AC9a: patience_sec=0.05 (not 60s) causes abort within 2s wall clock."""
    reviews_dir = tmp_path / "reviews"
    _touch_role_files(reviews_dir, 5)  # N-1 condition met immediately

    proc = _FakeProc(wait_raises=True)
    start = time.monotonic()
    wd = _StragglerWatchdog(
        proc=proc,
        reviews_dir=reviews_dir,
        expected_n=6,
        patience_sec=0.05,  # tiny — should fire quickly, NOT at the 60s module default
        poll_interval_sec=0.01,
    )

    t = threading.Thread(target=wd.run, daemon=True)
    t.start()
    t.join(timeout=2.0)
    elapsed = time.monotonic() - start

    assert not t.is_alive(), "watchdog run() did not return within 2s"
    assert wd.aborted is True, "watchdog must have aborted (patience_sec override not honored)"
    assert elapsed < 2.0, f"abort took {elapsed:.2f}s — patience override was not honored (would have been 60s)"


def test_ac9b_invoke_review_llm_honours_patience_override(tmp_path, monkeypatch):
    """AC9b: straggler_patience_sec=7 in org_config → straggler_cfg['patience_sec'] == 7.0."""
    captured = {}

    def _fake_invoke(**kwargs):
        captured.update(kwargs)
        return StepResult(
            status="ok",
            data={"raw_response": "x", "response_bytes": 1, "command": []},
            duration_ms=0,
            step_name="invoke_review_llm",
        )

    import phase_6_review
    monkeypatch.setattr(phase_6_review, "invoke_llm_subprocess", _fake_invoke)

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()

    ctx = types.SimpleNamespace(
        org_config={
            "scratchpad_dir": str(scratchpad),
            "straggler_abort": True,
            "straggler_patience_sec": 7,  # override from org_config
        }
    )

    prev_data = {
        "doc_path": "doc.md",
        "spec_path": "spec.md",
        "red_log_path": "red.log",
        "green_log_path": "green.log",
        "prompt": "review this",
    }
    prev = StepResult(
        status="ok",
        data=prev_data,
        duration_ms=0,
        step_name="build_review_prompt",
    )

    _invoke_review_llm(ctx, prev)

    cfg = captured.get("straggler_cfg")
    assert cfg is not None, "straggler_cfg must be set when straggler_abort=True"
    assert cfg["patience_sec"] == 7.0, (
        f"patience_sec must be 7.0 (from org_config override), got {cfg['patience_sec']!r}"
    )


# ─── AC10 ────────────────────────────────────────────────────────────────────


def test_ac10_invoke_llm_subprocess_straggler_abort_returns_ok(tmp_path, monkeypatch):
    """AC10: when watchdog aborts, invoke_llm_subprocess returns status='ok',
    data['straggler_aborted'] is True, and extra_data keys survive.

    Approach: monkeypatch _StragglerWatchdog with a stub whose start() immediately
    marks aborted=True, then monkeypatch subprocess.Popen to return a fake that
    yields an immediate non-zero exit so _stream_read_events short-circuits.
    """

    class _AbortingWatchdog:
        """Immediately marks aborted=True when started."""
        aborted = False

        def __init__(self, **kwargs):
            pass

        def start(self):
            _AbortingWatchdog.aborted = True
            # Also set on the instance — but class attr is fine since only one instance
            self.aborted = True

        def stop(self):
            pass

    monkeypatch.setattr(llm_subprocess, "_StragglerWatchdog", _AbortingWatchdog)

    # Fake Popen that produces immediate EOF on stdout (empty stream → no result event)
    class _FakePopen:
        pid = 7777
        returncode = 0

        def __init__(self, *args, **kwargs):
            pass

        stdout = None  # will be set via property
        stderr = None
        stdin = None

    import io

    class _FakePopenInstance:
        pid = 7777
        returncode = 0
        stdout = io.StringIO("")
        stderr = io.StringIO("")
        stdin = io.StringIO()

        def communicate(self, input=None, timeout=None):
            # Required by _communicate_legacy for shell-stub commands (echo path)
            return ("", "")

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

        def kill(self):
            pass

    class _FakePopenCtor:
        def __new__(cls, *args, **kwargs):
            return _FakePopenInstance()

    monkeypatch.setattr(llm_subprocess.subprocess, "Popen", _FakePopenCtor)

    reviews_dir = tmp_path / "reviews"
    result = invoke_llm_subprocess(
        prompt="x",
        model="sonnet",  # 25e75663: model= replaces command=; Popen is mocked
        timeout_sec=5,
        step_name="invoke_review_llm",
        extra_data={"complexity": "FEATURE", "doc_path": "d"},
        straggler_cfg={
            "reviews_dir": str(reviews_dir),
            "expected_n": 6,
        },
    )

    assert result.status == "ok", (
        f"straggler abort must yield status='ok', got {result.status!r} "
        f"(error_code={result.error_code!r})"
    )
    assert result.error_code is None, (
        f"straggler abort result must have error_code=None, got {result.error_code!r}"
    )
    assert result.data is not None and result.data.get("straggler_aborted") is True, (
        f"data['straggler_aborted'] must be True; data={result.data!r}"
    )
    assert result.data.get("complexity") == "FEATURE", (
        f"extra_data key 'complexity' must survive in result.data; data={result.data!r}"
    )
    assert result.data.get("doc_path") == "d", (
        f"extra_data key 'doc_path' must survive in result.data; data={result.data!r}"
    )


# ─── AC11 ────────────────────────────────────────────────────────────────────


def test_ac11_invoke_llm_subprocess_no_straggler_aborted_when_cfg_none(tmp_path, monkeypatch):
    """AC11: straggler_cfg=None → 'straggler_aborted' must never appear in result.data.

    Uses a fake Popen producing immediate EOF (error result) — the key point
    is that regardless of outcome, straggler_aborted must be absent.
    """

    class _FakePopenInstance:
        pid = 8888
        returncode = 0
        stdout = None
        stderr = None
        stdin = None

    import io

    class _FakePopen:
        def __new__(cls, *args, **kwargs):
            inst = object.__new__(cls)
            inst.pid = 8888
            inst.returncode = 0
            inst.stdout = io.StringIO("")
            inst.stderr = io.StringIO("")
            inst.stdin = io.StringIO()
            return inst

        def communicate(self, input=None, timeout=None):
            # Required by _communicate_legacy for shell-stub commands (echo path)
            return ("", "")

        def poll(self):
            return 0

        def wait(self, timeout=None):
            return 0

        def kill(self):
            pass

    monkeypatch.setattr(llm_subprocess.subprocess, "Popen", _FakePopen)

    result = invoke_llm_subprocess(
        prompt="x",
        model="sonnet",  # 25e75663: model= replaces command=; Popen is mocked
        timeout_sec=5,
        step_name="test_ac11",
        straggler_cfg=None,
    )

    # Whatever the outcome, straggler_aborted must not appear
    if result.data is not None:
        assert "straggler_aborted" not in result.data, (
            f"'straggler_aborted' must NOT appear in result.data when straggler_cfg=None; "
            f"data={result.data!r}"
        )
