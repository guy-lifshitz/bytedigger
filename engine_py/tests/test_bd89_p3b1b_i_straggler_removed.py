"""RED tests for bd#89 P3b1b-i: the straggler-abort machinery is removed from llm_subprocess.py.

Frozen spec: docs/decisions/2026-10-02-bd89-p3b1b-i-straggler-machinery.md (AC1-AC11).

Class: PROCESS. Chokepoint: `invoke_llm_subprocess`, the single public entry that
turns a caller's `straggler_cfg` into a watchdog arm.

Seams: stub backends via `register_backend` (cleaned by `reset_backends`), a real
on-disk `EventLog` under `tmp_path` bound through `telemetry_ctx` (section 1l: the
unit under test is never mocked), and the `_stream_read_events` seam of the real
`claude-subprocess` handler (same seam as test_4C03CCED G2-AC2).
No sys.path mutation, no conftest import. No time-dependent or singleton-resource
fixtures: every test is deterministic (workflows.md section 1i not triggered).
"""
from __future__ import annotations

import ast
import inspect
import threading
import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bytedigger_engine import llm_subprocess, telemetry_ctx
from bytedigger_engine.contracts import StepResult
from bytedigger_engine.event_log import EventLog

TESTS_DIR = Path(__file__).resolve().parent
ENGINE_PY = TESTS_DIR.parent
REPO = ENGINE_PY.parent
LLM_SRC = ENGINE_PY / "bytedigger_engine" / "llm_subprocess.py"
P6_SRC = ENGINE_PY / "bytedigger_engine" / "workflows" / "phase_6_review.py"

WATCHDOG_THREAD = "llm-straggler-watchdog"
BARE = "p3b1b-bare"
SPY = "p3b1b-spy"


class _Spy:
    """Registered stub backend: records kwargs, returns a plain ok StepResult."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, **kwargs):
        self.calls.append(dict(kwargs))
        return StepResult(
            status="ok", data={"raw_response": "spy"}, duration_ms=0,
            step_name=kwargs.get("step_name", "spy"),
        )


@pytest.fixture(autouse=True)
def _isolation(monkeypatch):
    monkeypatch.setattr(llm_subprocess, "emit_resolver_resolved", lambda *a, **kw: None)
    llm_subprocess.reset_backends()
    telemetry_ctx.clear_current_run()
    yield
    telemetry_ctx.clear_current_run()
    llm_subprocess.reset_backends()


@pytest.fixture
def started_threads(monkeypatch):
    """Names of every thread started during the test (deterministic, no sleeps)."""
    names: list[str] = []
    real_start = threading.Thread.start

    def _recording_start(self, *a, **kw):
        names.append(self.name)
        return real_start(self, *a, **kw)

    monkeypatch.setattr(threading.Thread, "start", _recording_start)
    return names


@pytest.fixture
def real_log(tmp_path):
    """Real on-disk EventLog bound as the active run context."""
    log = EventLog(tmp_path / "events.jsonl")
    telemetry_ctx.set_current_run(
        event_log=log, run_id="RUN-P3B1BI", step_name="p3b1bi", phase="phase_6",
    )
    return log


def _records(log: EventLog, event_type: str) -> list[dict]:
    return [e for e in log.read_all() if e["event_type"] == event_type]


def _register(name: str, caps=None) -> _Spy:
    spy = _Spy()
    llm_subprocess.register_backend(
        name, spy, manifest_source="harness_tool_record", capabilities=caps,
    )
    return spy


def _call(backend: str, **kw):
    base = dict(prompt="p", model="sonnet", timeout_sec=5, step_name="p3b1bi-step", backend=backend)
    base.update(kw)
    return llm_subprocess.invoke_llm_subprocess(**base)


# --- AC1 --------------------------------------------------------------------

def test_ac1_watchdog_symbols_and_strings_gone():
    """AC1: class + two constants gone, no watchdog string constants in the source.

    Pre-GREEN fail: all three attributes still exist and the constants are emitted.
    """
    for name in ("_StragglerWatchdog", "STRAGGLER_PATIENCE_SEC", "STRAGGLER_POLL_INTERVAL_SEC"):
        assert not hasattr(llm_subprocess, name), f"{name} still present"
    tree = ast.parse(LLM_SRC.read_text(encoding="utf-8"))
    strings = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    left = sorted(strings & {WATCHDOG_THREAD, "straggler_abort", "straggler_aborted"})
    assert not left, f"watchdog string constants still in llm_subprocess.py: {left}"


# --- AC2 --------------------------------------------------------------------

def test_ac2_watchdog_probe_signature_and_behaviour():
    """AC2: probe takes exactly (backend, idle_enabled); no 'abort' in the error.

    Pre-GREEN fail: signature still has straggler_enabled (and calls raise TypeError).
    """
    fn = llm_subprocess._assert_backend_supports_watchdog
    assert list(inspect.signature(fn).parameters) == ["backend", "idle_enabled"]
    _register(BARE)
    assert fn(BARE, idle_enabled=False) is None
    err = fn(BARE, idle_enabled=True)
    assert err is not None and err.error_code == "E_LLM_WATCHDOG_UNSUPPORTED"
    assert "['progress_since']" in err.error
    assert "abort" not in err.error


# --- AC3 --------------------------------------------------------------------

def test_ac3_fallback_hang_attempts_signature():
    """AC3: _fallback_hang_attempts(resolved_backend, result, idle_enabled).

    Pre-GREEN fail: signature still has straggler_enabled.
    """
    params = list(inspect.signature(llm_subprocess._fallback_hang_attempts).parameters)
    assert params == ["resolved_backend", "result", "idle_enabled"]


# --- AC4 (side effect, real EventLog) ---------------------------------------

def test_ac4_probe_failed_payload_has_no_straggler_key(real_log):
    """AC4: runner_capability_probe_failed payload keys are exactly {backend, reason, idle_enabled}.

    Pre-GREEN fail: payload still carries straggler_enabled.
    """
    _register(BARE)
    result = _call(BARE, idle_timeout_sec=5)
    assert result.error_code == "E_LLM_WATCHDOG_UNSUPPORTED"
    recs = _records(real_log, "runner_capability_probe_failed")
    assert len(recs) == 1, recs
    assert set(recs[0]["payload"]) == {"backend", "reason", "idle_enabled"}, recs[0]["payload"]


# --- AC5 (side effect, real EventLog) ---------------------------------------

def test_ac5_non_none_cfg_is_ignored_with_event(real_log, tmp_path, started_threads):
    """AC5: non-None straggler_cfg -> spy result unchanged, one straggler_cfg_ignored,
    spy gets straggler_cfg=None, no watchdog thread.

    Pre-GREEN fail: the {"progress_since"} spy lacks 'abort', so the probe rejects the call.
    """
    spy = _register(SPY, {"progress_since"})
    result = _call(SPY, straggler_cfg={"reviews_dir": str(tmp_path), "expected_n": 2})
    assert result.status == "ok", (result.status, result.error_code)
    assert "straggler_aborted" not in (result.data or {})
    recs = _records(real_log, "straggler_cfg_ignored")
    assert len(recs) == 1, recs
    assert recs[0]["payload"] == {"step_name": "p3b1bi-step"}
    assert len(spy.calls) == 1
    assert "straggler_cfg" in spy.calls[0] and spy.calls[0]["straggler_cfg"] is None
    assert WATCHDOG_THREAD not in started_threads


# --- AC6 --------------------------------------------------------------------

@pytest.mark.parametrize("passed", [True, False], ids=["explicit-none", "omitted"])
def test_ac6_none_or_omitted_emits_nothing(real_log, passed):
    """AC6: None / omitted kwarg -> no straggler_cfg_ignored, spy still gets straggler_cfg=None.

    Guard leg: passes pre-GREEN (no such event exists today).
    """
    spy = _register(SPY, {"progress_since"})
    kw = {"straggler_cfg": None} if passed else {}
    assert _call(SPY, **kw).status == "ok"
    assert _records(real_log, "straggler_cfg_ignored") == []
    assert spy.calls[0].get("straggler_cfg", "MISSING") is None


def test_ac6_non_none_without_run_context_is_silent_ok(tmp_path):
    """AC6: non-None cfg and no active run context -> ok, nothing raised.

    Pre-GREEN fail: the probe rejects the {"progress_since"} spy for lack of 'abort'.
    """
    telemetry_ctx.clear_current_run()
    _register(SPY, {"progress_since"})
    result = _call(SPY, straggler_cfg={"reviews_dir": str(tmp_path), "expected_n": 2})
    assert result.status == "ok", (result.status, result.error_code)


# --- AC7 --------------------------------------------------------------------

def test_ac7_claude_subprocess_never_arms_watchdog(tmp_path, started_threads):
    """AC7: the real claude-subprocess handler ignores straggler_cfg.

    Pre-GREEN fail: the handler starts a thread named llm-straggler-watchdog.
    """
    result_event = {
        "type": "result", "subtype": "success", "result": "ok",
        "usage": {"input_tokens": 1, "output_tokens": 1,
                  "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0},
        "total_cost_usd": 0.001, "duration_ms": 10,
    }
    proc = MagicMock()
    proc.pid = 4242
    proc.returncode = 0
    proc.stdin = MagicMock()
    proc.wait = MagicMock(return_value=0)
    proc.poll = MagicMock(return_value=0)

    def _ok_stream(*a, **kw):
        return (False, "", "", [result_event], False, False)

    with patch("bytedigger_engine.llm_subprocess.subprocess.Popen", return_value=proc), \
         patch("bytedigger_engine.llm_subprocess._stream_read_events", side_effect=_ok_stream):
        result = _call(
            "claude-subprocess",
            straggler_cfg={"reviews_dir": str(tmp_path), "expected_n": 2},
        )
    assert result.status == "ok", (result.status, result.error_code)
    assert "straggler_aborted" not in (result.data or {})
    assert WATCHDOG_THREAD not in started_threads


# --- AC8 --------------------------------------------------------------------

def test_ac8_phase6_has_no_straggler_token():
    """AC8: phase_6_review.py has no 'straggler' token; the review call carries no straggler_cfg.

    Pre-GREEN fail: phase 6 still passes straggler_cfg=None.
    """
    from bytedigger_engine.workflows import phase_6_review as p6  # noqa: PLC0415

    assert "straggler" not in P6_SRC.read_text(encoding="utf-8").lower()
    seen: list[dict] = []

    def _capture(**kwargs):
        seen.append(kwargs)
        return StepResult(status="ok", data={"raw_response": "x"}, duration_ms=0, step_name="s")

    prev = StepResult(status="ok", duration_ms=0, step_name="build_review_prompt", data={
        "doc_path": "d", "spec_path": "s", "red_log_path": "r", "green_log_path": "g",
        "prompt": "review",
    })
    ctx = types.SimpleNamespace(org_config={"scratchpad_dir": "/nonexistent-p3b1bi"})
    with patch.object(p6, "invoke_llm_subprocess", _capture):
        p6._invoke_review_llm(ctx, prev)
    assert seen and "straggler_cfg" not in seen[0]


# --- AC9 (GUARD) ------------------------------------------------------------

def test_ac9_guard_kept_public_seam():
    """AC9 GUARD: kwarg, protocol parameter and 'abort' token are kept on purpose.

    Passes pre-GREEN by design (premise guard); fails if GREEN over-deletes.
    """
    p = inspect.signature(llm_subprocess.invoke_llm_subprocess).parameters["straggler_cfg"]
    assert p.default is None
    assert "straggler_cfg" in inspect.signature(llm_subprocess.LLMBackend.__call__).parameters
    assert "abort" in llm_subprocess._DEFAULT_BACKEND_CAPABILITIES["claude-subprocess"]


# --- AC10 -------------------------------------------------------------------

def test_ac10_docs_text():
    """AC10: CHANGELOG top section and docs/backends.md describe the deprecation.

    Pre-GREEN fail: neither mentions the new behaviour.
    """
    changelog = (REPO / "CHANGELOG.md").read_text(encoding="utf-8")
    parts = changelog.split("\n## ")
    assert len(parts) >= 2, "CHANGELOG has no '## ' section"
    first = parts[1]
    assert "straggler_cfg_ignored" in first and "#202" in first
    backends = (REPO / "docs" / "backends.md").read_text(encoding="utf-8")
    assert "straggler_cfg" in backends and "deprecated" in backends


# --- AC11 (GUARD) -----------------------------------------------------------

def test_ac11_guard_retired_file_gone_and_siblings_clean():
    """AC11 GUARD: the watchdog test file is deleted; edited siblings drop the dead references.

    Fails until the orchestrator `git rm`s test_ccbb65dc_straggler_watchdog.py.
    """
    assert not (TESTS_DIR / "test_ccbb65dc_straggler_watchdog.py").exists()
    siblings = [
        "test_4C03CCED_ship1d_watchdog_capability.py",
        "test_bd145_reserved_observation_fields.py",
        "test_register_backend_A60F1FE3.py",
        "test_bd139_single_reviewer.py",
        "test_bd82_role_backend_effort.py",
        "test_bd89_p3b1_single_reviewer_only.py",
    ]
    bad = []
    for name in siblings:
        text = (TESTS_DIR / name).read_text(encoding="utf-8")
        for needle in ("_StragglerWatchdog", "straggler_enabled=", "straggler_aborted"):
            if needle in text:
                bad.append(f"{name}: {needle}")
    assert not bad, bad
