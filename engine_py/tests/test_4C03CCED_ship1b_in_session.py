"""RED tests for 4C03CCED Ship 1B — claude-in-session backend + G3 file-protocol.

Spec: SHARED/memory/Decisions/2026-05-23_4C03CCED_ship1b_in_session_spec.md
Agreement: 4C03CCED (parent SYSTEMATIC — Runner contract, sub-ship 1B of 4)

8 tests, one per AC1-AC8.  ALL must FAIL pre-GREEN because:
  - _KNOWN_BACKENDS is ("claude-subprocess",) — "claude-in-session" absent → AC1 fails
  - resolver gate returns E_LLM_BACKEND_UNKNOWN for "claude-in-session" →
    AC2..AC8 all receive E_LLM_BACKEND_UNKNOWN instead of their asserted behaviour

§1q check: NO spec_from_file_location / module_from_spec / exec_module used.
§1i compliance: stale (AC5) and torn (AC6) fixtures are materialized on disk
  BEFORE invoking the unit-under-test; AC7 uses a background thread that waits
  for the request file to appear then atomically writes the result — never races.
§1m compliance: all fixture files written via Path.write_text() to real tmp_path dirs.
"""
from __future__ import annotations

import json
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

HERE = Path(__file__).parent
ENGINE_ROOT = HERE.parent

# engine_py/ on sys.path — grandfathered pattern shared by all sibling tests
# (test_subprocess_telemetry.py, test_4C03CCED_ship1a_backend_selector.py, etc.).
import sys
sys.path.insert(0, str(ENGINE_ROOT))

from bytedigger_engine.llm_subprocess import invoke_llm_subprocess  # noqa: E402
from bytedigger_engine import llm_subprocess as _llm_module              # noqa: E402
from bytedigger_engine import telemetry_ctx                               # noqa: E402


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

class _FakeEventLog:
    """Captures (event_type, payload, run_id) tuples for assertion."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict, str]] = []

    def append(self, event_type: str, payload: dict, run_id: str | None = None) -> None:
        self.events.append((event_type, dict(payload), run_id or "ad-hoc"))


_DETERMINISTIC_NONCE = "deadbeef" * 4   # 32-char hex string used in AC6/AC7/AC8


class _FakeUUID:
    """Stand-in for uuid.uuid4() whose .hex is deterministic."""
    def __init__(self) -> None:
        self.hex = _DETERMINISTIC_NONCE


def _fake_uuid4() -> _FakeUUID:
    return _FakeUUID()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clear_ctx():
    """Ensure no stale RunContext leaks between tests.

    Also resets the _BACKENDS registry (xdist isolation): a co-located test
    on the same xdist worker that imported anthropic_api (module-level
    register() side-effect) would otherwise leak 'anthropic-api' into this
    worker's registry.
    """
    _llm_module.reset_backends()
    telemetry_ctx.clear_current_run()
    yield
    telemetry_ctx.clear_current_run()
    _llm_module.reset_backends()


@pytest.fixture
def event_log():
    return _FakeEventLog()


@pytest.fixture
def active_run_ctx(event_log):
    """Sets an active RunContext with run_id='RUN-1B', teardown clears it."""
    telemetry_ctx.set_current_run(
        event_log=event_log, run_id="RUN-1B", step_name="t", phase="phase_X"
    )
    yield event_log
    telemetry_ctx.clear_current_run()


# Model string for in-session invocations (25e75663: model= replaces command=).
_MODEL = "claude-opus-4-5"


# ---------------------------------------------------------------------------
# AC1 — _KNOWN_BACKENDS contains "claude-in-session"
# ---------------------------------------------------------------------------

def test_AC1_claude_in_session_registered_in_known_backends(monkeypatch, tmp_path, active_run_ctx):
    """AC1: _KNOWN_BACKENDS is ("claude-subprocess", "claude-in-session") post-GREEN.

    Pre-GREEN FAIL: _KNOWN_BACKENDS == ("claude-subprocess",), so:
      (a) "claude-in-session" not in _KNOWN_BACKENDS → assertion fails, AND
      (b) invoke_llm_subprocess(backend="claude-in-session") returns
          E_LLM_BACKEND_UNKNOWN, not a non-E_LLM_BACKEND_UNKNOWN error.
    """
    # Part A: structural constant assertion
    assert "claude-in-session" in _llm_module._KNOWN_BACKENDS, (
        "AC1: 'claude-in-session' must be in _KNOWN_BACKENDS; "
        f"got {_llm_module._KNOWN_BACKENDS!r}"
    )
    assert _llm_module._KNOWN_BACKENDS == ("claude-subprocess", "claude-in-session"), (
        "AC1: _KNOWN_BACKENDS must be exactly ('claude-subprocess', 'claude-in-session'); "
        f"got {_llm_module._KNOWN_BACKENDS!r}"
    )

    # Part B: invoking with backend="claude-in-session" must NOT return E_LLM_BACKEND_UNKNOWN
    # (it may return E_LLM_RUN_ID_MISSING or E_LLM_NO_RESULT_EVENT depending on ctx,
    # but must NOT be the unknown-backend sentinel).
    monkeypatch.setenv("HAL_RUNNER_REQUEST_DIR", str(tmp_path))
    result = invoke_llm_subprocess(
        prompt="x",
        model=_MODEL,
        timeout_sec=1,
        step_name="t",
        backend="claude-in-session",
    )
    assert result.error_code != "E_LLM_BACKEND_UNKNOWN", (
        "AC1: backend='claude-in-session' must NOT return E_LLM_BACKEND_UNKNOWN; "
        f"got error_code={result.error_code!r}"
    )


# ---------------------------------------------------------------------------
# AC2 — No subprocess.Popen invoked on in-session path
# ---------------------------------------------------------------------------

def test_AC2_no_popen_on_in_session_path(monkeypatch, tmp_path, active_run_ctx):
    """AC2 forcing-function: Popen call_count == 0 on in-session path.

    Pre-GREEN FAIL: today the resolver gate returns E_LLM_BACKEND_UNKNOWN for
    'claude-in-session' before any Popen would be called — but ONLY because the
    backend is not in _KNOWN_BACKENDS.  After GREEN adds 'claude-in-session' to
    _KNOWN_BACKENDS, the old code falls through to the Popen block (no in-session
    fork exists yet), so Popen would be called with call_count >= 1.
    The test asserts Popen call_count == 0 AND that we get non-BACKEND_UNKNOWN
    result — this will FAIL under both pre-1B production code states:
      - pre-GREEN: returns E_LLM_BACKEND_UNKNOWN (backend unknown) — error_code assertion fails
      Actually pre-GREEN: E_LLM_BACKEND_UNKNOWN, so the first assert (error_code) fails.
    """
    popen_mock = MagicMock()
    monkeypatch.setenv("HAL_RUNNER_REQUEST_DIR", str(tmp_path))

    with patch("bytedigger_engine.llm_subprocess.subprocess.Popen", popen_mock):
        result = invoke_llm_subprocess(
            prompt="hello",
            model=_MODEL,
            timeout_sec=1,
            step_name="t",
            backend="claude-in-session",
        )

    # Must not be unknown-backend (the in-session path is recognised)
    assert result.error_code != "E_LLM_BACKEND_UNKNOWN", (
        "AC2: backend='claude-in-session' must be recognised (not BACKEND_UNKNOWN); "
        f"got error_code={result.error_code!r}"
    )

    # No Popen spawn on the in-session path (core forcing-function)
    assert popen_mock.call_count == 0, (
        f"AC2: subprocess.Popen must NOT be called on in-session path; "
        f"call_count={popen_mock.call_count}"
    )

    # 'subprocess_spawned' must be absent from event log (no subprocess)
    event_types = [e[0] for e in active_run_ctx.events]
    assert "subprocess_spawned" not in event_types, (
        f"AC2: 'subprocess_spawned' must be absent on in-session path; got {event_types!r}"
    )


# ---------------------------------------------------------------------------
# AC3 — run_id required; E_LLM_RUN_ID_MISSING terminal with data=None
# ---------------------------------------------------------------------------

def test_AC3_run_id_required_no_run_ctx(monkeypatch, tmp_path):
    """AC3 forcing-function: no active RunContext → E_LLM_RUN_ID_MISSING, data=None.

    Pre-GREEN FAIL: no such code path exists; invoke returns E_LLM_BACKEND_UNKNOWN
    immediately (backend not in _KNOWN_BACKENDS), so error_code != E_LLM_RUN_ID_MISSING.
    """
    telemetry_ctx.clear_current_run()
    monkeypatch.setenv("HAL_RUNNER_REQUEST_DIR", str(tmp_path))

    result = invoke_llm_subprocess(
        prompt="x",
        model=_MODEL,
        timeout_sec=1,
        step_name="t",
        backend="claude-in-session",
    )

    assert result.error_code == "E_LLM_RUN_ID_MISSING", (
        f"AC3: no RunContext must yield E_LLM_RUN_ID_MISSING; got {result.error_code!r}"
    )
    assert result.data is None, (
        f"AC3: terminal error must have data=None; got data={result.data!r}"
    )
    assert result.recoverable is False, (
        f"AC3: E_LLM_RUN_ID_MISSING must be non-recoverable; got {result.recoverable!r}"
    )
    assert result.status == "error", (
        f"AC3: status must be 'error'; got {result.status!r}"
    )


def test_AC3_run_id_required_empty_string(monkeypatch, tmp_path, event_log):
    """AC3 variant: RunContext with run_id='' also triggers E_LLM_RUN_ID_MISSING.

    The spec (§1d _RunCtx shape + §2.5 guard) requires guard against empty run_id
    as well as None RunContext.

    Pre-GREEN FAIL: same reason — no in-session fork exists; E_LLM_BACKEND_UNKNOWN.
    """
    telemetry_ctx.set_current_run(
        event_log=event_log, run_id="", step_name="t", phase="phase_X"
    )
    monkeypatch.setenv("HAL_RUNNER_REQUEST_DIR", str(tmp_path))
    try:
        result = invoke_llm_subprocess(
            prompt="x",
            model=_MODEL,
            timeout_sec=1,
            step_name="t",
            backend="claude-in-session",
        )
    finally:
        telemetry_ctx.clear_current_run()

    assert result.error_code == "E_LLM_RUN_ID_MISSING", (
        f"AC3b: empty run_id must yield E_LLM_RUN_ID_MISSING; got {result.error_code!r}"
    )
    assert result.data is None, (
        f"AC3b: terminal error must have data=None; got {result.data!r}"
    )


# ---------------------------------------------------------------------------
# AC4 — request artifact written with all required fields
# ---------------------------------------------------------------------------

def test_AC4_request_artifact_written_with_required_fields(monkeypatch, tmp_path, active_run_ctx):
    """AC4: after invoke, a .req.json file exists at the expected path with all fields.

    Pre-GREEN FAIL: no file-protocol code path exists; returns E_LLM_BACKEND_UNKNOWN
    immediately, no file written — and even if backend were recognised, no write code.
    """
    monkeypatch.setenv("HAL_RUNNER_REQUEST_DIR", str(tmp_path))

    # We allow the call to time out (timeout_sec=1) — we only care that the
    # request file was written before polling starts.
    result = invoke_llm_subprocess(
        prompt="test-prompt-AC4",
        model=_MODEL,
        timeout_sec=1,
        step_name="t",
        backend="claude-in-session",
    )

    # The call must have progressed past the backend gate (not BACKEND_UNKNOWN)
    assert result.error_code != "E_LLM_BACKEND_UNKNOWN", (
        f"AC4: backend must be recognised; got error_code={result.error_code!r}"
    )

    # Locate the request file — it lives under tmp_path/RUN-1B/phase_X/<nonce>.req.json
    nonce_dir = tmp_path / "RUN-1B" / "phase_X"
    req_files = list(nonce_dir.glob("*.req.json"))
    assert len(req_files) == 1, (
        f"AC4: expected exactly 1 .req.json under {nonce_dir}; found {req_files!r}"
    )

    req_path = req_files[0]
    content = json.loads(req_path.read_text(encoding="utf-8"))

    required_keys = {
        "request_nonce", "prompt", "step_name",
        "allowed_tools", "model", "cwd", "timeout_sec", "phase",
        "run_id", "__hal_integrity",
    }
    # 25e75663 Class C: "command" dropped from .req.json; "model" is the seam now.
    missing = required_keys - content.keys()
    assert not missing, f"AC4: .req.json missing keys {missing!r}; got keys {set(content.keys())!r}"

    # Nonce in file must match filename stem
    filename_nonce = req_path.name.split(".")[0]  # e.g. "deadbeef..."
    assert content["request_nonce"] == filename_nonce, (
        f"AC4: request_nonce in JSON ({content['request_nonce']!r}) must match filename stem ({filename_nonce!r})"
    )

    assert content["__hal_integrity"] == "end", (
        f"AC4: __hal_integrity must be 'end'; got {content['__hal_integrity']!r}"
    )
    assert content["prompt"] == "test-prompt-AC4", (
        f"AC4: prompt mismatch; got {content['prompt']!r}"
    )
    assert content["run_id"] == "RUN-1B", (
        f"AC4: run_id mismatch; got {content['run_id']!r}"
    )
    assert content["phase"] == "phase_X", (
        f"AC4: phase mismatch; got {content['phase']!r}"
    )


# ---------------------------------------------------------------------------
# AC5 — stale nonce rejection (§1i + §1m: pre-staged before invoke)
# ---------------------------------------------------------------------------

def test_AC5_stale_nonce_rejected(monkeypatch, tmp_path, active_run_ctx):
    """AC5 forcing-function: stale result file (mismatched nonce) is NOT consumed.

    §1i compliance: stale file is written to disk BEFORE invoke_llm_subprocess.
    §1m compliance: file materialized via Path.write_text().

    Pre-GREEN FAIL: backend not in _KNOWN_BACKENDS → E_LLM_BACKEND_UNKNOWN;
    no polling code exists to reject or accept any file.
    """
    monkeypatch.setenv("HAL_RUNNER_REQUEST_DIR", str(tmp_path))

    # Pre-stage stale result file at a DIFFERENT nonce path (§1i pre-stage)
    stale_nonce_dir = tmp_path / "RUN-1B" / "phase_X"
    stale_nonce_dir.mkdir(parents=True, exist_ok=True)
    stale_nonce = "0" * 32  # distinct from any real uuid4
    stale_result_path = stale_nonce_dir / f"{stale_nonce}.res.json"
    stale_payload = {
        "request_nonce": "old-stale-nonce-AAA",  # deliberately wrong nonce
        "subtype": "success",
        "raw_response": "STALE",
        "__hal_integrity": "end",
    }
    stale_result_path.write_text(json.dumps(stale_payload), encoding="utf-8")

    # Invoke with short timeout — should time out waiting for a VALID result file
    result = invoke_llm_subprocess(
        prompt="x",
        model=_MODEL,
        timeout_sec=2,
        step_name="t",
        backend="claude-in-session",
    )

    # Must have passed the backend gate
    assert result.error_code != "E_LLM_BACKEND_UNKNOWN", (
        f"AC5: backend must be recognised; got {result.error_code!r}"
    )

    # Must time out — stale file must not be consumed
    assert result.error_code == "E_LLM_NO_RESULT_EVENT", (
        f"AC5: stale-nonce must cause timeout (E_LLM_NO_RESULT_EVENT); "
        f"got {result.error_code!r}"
    )
    assert result.recoverable is True, (
        f"AC5: E_LLM_NO_RESULT_EVENT must be recoverable; got {result.recoverable!r}"
    )

    # The STALE string must NOT appear in returned data (stale file not consumed)
    returned_data = result.data or {}
    assert "raw_response" not in returned_data or returned_data.get("raw_response") != "STALE", (
        "AC5: stale raw_response 'STALE' must NOT be returned as the result"
    )

    # Stale file must still be present on disk (not deleted by the new code)
    assert stale_result_path.exists(), (
        "AC5: stale file must remain untouched (different nonce path)"
    )

    # 'subprocess_spawned' must be absent (in-session path)
    event_types = [e[0] for e in active_run_ctx.events]
    assert "subprocess_spawned" not in event_types, (
        f"AC5: 'subprocess_spawned' must be absent; got {event_types!r}"
    )

    # runner_result_consumed with outcome="timeout" must be emitted
    consumed_events = [e for e in active_run_ctx.events if e[0] == "runner_result_consumed"]
    assert len(consumed_events) >= 1, (
        f"AC5: 'runner_result_consumed' event must be emitted on timeout path; "
        f"got event_types={event_types!r}"
    )
    outcome = consumed_events[-1][1].get("outcome")
    assert outcome == "timeout", (
        f"AC5: runner_result_consumed outcome must be 'timeout'; got {outcome!r}"
    )


# ---------------------------------------------------------------------------
# AC6 — torn-write rejection (deterministic nonce via monkeypatch, §1i pre-stage)
# ---------------------------------------------------------------------------

def test_AC6_torn_write_rejected(monkeypatch, tmp_path, active_run_ctx):
    """AC6 forcing-function: result file missing __hal_integrity marker → treated as absent.

    §1i compliance: torn file pre-staged before invoke.
    §1m compliance: file materialized via Path.write_text().
    Deterministic nonce: uuid.uuid4 monkeypatched to _DETERMINISTIC_NONCE so we can
    compute the exact result path before invocation.

    Pre-GREEN FAIL: backend not in _KNOWN_BACKENDS → E_LLM_BACKEND_UNKNOWN;
    no integrity-check code path exists.
    """
    monkeypatch.setenv("HAL_RUNNER_REQUEST_DIR", str(tmp_path))

    # Patch uuid.uuid4 in the llm_subprocess module to return deterministic nonce
    monkeypatch.setattr(_llm_module, "uuid", type("_FakeUUIDMod", (), {"uuid4": staticmethod(_fake_uuid4)})())

    # Pre-stage torn file at the EXACT nonce path production will compute (§1i)
    torn_nonce_dir = tmp_path / "RUN-1B" / "phase_X"
    torn_nonce_dir.mkdir(parents=True, exist_ok=True)
    torn_result_path = torn_nonce_dir / f"{_DETERMINISTIC_NONCE}.res.json"
    torn_payload = {
        "request_nonce": _DETERMINISTIC_NONCE,
        "subtype": "success",
        "raw_response": "TORN",
        # __hal_integrity deliberately ABSENT — simulates torn write
    }
    torn_result_path.write_text(json.dumps(torn_payload), encoding="utf-8")

    result = invoke_llm_subprocess(
        prompt="x",
        model=_MODEL,
        timeout_sec=2,
        step_name="t",
        backend="claude-in-session",
    )

    # Must have passed the backend gate
    assert result.error_code != "E_LLM_BACKEND_UNKNOWN", (
        f"AC6: backend must be recognised; got {result.error_code!r}"
    )

    # Must time out — torn file is ignored (no integrity marker)
    assert result.error_code == "E_LLM_NO_RESULT_EVENT", (
        f"AC6: torn file without __hal_integrity must cause timeout (E_LLM_NO_RESULT_EVENT); "
        f"got {result.error_code!r}"
    )

    # TORN string must NOT appear in returned data
    returned_data = result.data or {}
    assert returned_data.get("raw_response") != "TORN", (
        "AC6: torn raw_response 'TORN' must NOT be consumed/returned"
    )


# ---------------------------------------------------------------------------
# AC7 — success path: StepResult mirrors claude-subprocess shape
# ---------------------------------------------------------------------------

def test_AC7_success_path_result_consumed(monkeypatch, tmp_path, active_run_ctx):
    """AC7 forcing-function: background thread writes well-formed result → StepResult(status=ok).

    §1i compliance: background thread polls for request file; writes result
    atomically AFTER request file appears (never pre-written — avoids race with
    pre-write of a stale file).  The STALE/TORN pre-staging pattern (AC5/AC6)
    is NOT used here; instead a cooperative thread drives the happy path.
    §1m compliance: result file written to real tmp_path via json.dump + os.rename.
    Deterministic nonce: monkeypatched so we know the exact request path to watch.

    Pre-GREEN FAIL: backend not in _KNOWN_BACKENDS → E_LLM_BACKEND_UNKNOWN;
    no file-protocol code path; result StepResult can never be status='ok' here.
    """
    monkeypatch.setenv("HAL_RUNNER_REQUEST_DIR", str(tmp_path))
    monkeypatch.setattr(_llm_module, "uuid", type("_FakeUUIDMod", (), {"uuid4": staticmethod(_fake_uuid4)})())

    nonce = _DETERMINISTIC_NONCE
    nonce_dir = tmp_path / "RUN-1B" / "phase_X"
    nonce_dir.mkdir(parents=True, exist_ok=True)

    request_path = nonce_dir / f"{nonce}.req.json"
    result_path = nonce_dir / f"{nonce}.res.json"

    result_payload = {
        "request_nonce": nonce,
        "subtype": "success",
        "raw_response": "HELLO-FROM-RUNNER",
        "tokens_in": {"input_tokens": 5},
        "tokens_out": {"output_tokens": 7},
        "worker_written_paths": ["a.py", "b.py"],
        "manifest_source": "orchestrator_observed",  # 4C03CCED Ship 1C
        "__hal_integrity": "end",
    }

    runner_done = threading.Event()

    def _runner_thread() -> None:
        """Poll for request file, then atomically write result."""
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            if request_path.exists():
                # Atomic write: tmp + fsync + rename (mirrors _atomic_write_json spec)
                tmp_result = str(result_path) + ".tmp"
                with open(tmp_result, "w", encoding="utf-8") as f:
                    json.dump(result_payload, f)
                    f.flush()
                    os.fsync(f.fileno())
                os.rename(tmp_result, str(result_path))
                runner_done.set()
                return
            time.sleep(0.02)
        # No request appeared — set event anyway so main thread isn't stuck
        runner_done.set()

    t = threading.Thread(target=_runner_thread, daemon=True)
    t.start()

    result = invoke_llm_subprocess(
        prompt="AC7-test",
        model=_MODEL,
        timeout_sec=5,
        step_name="t",
        backend="claude-in-session",
    )

    t.join(timeout=12.0)

    # Core assertions
    assert result.status == "ok", (
        f"AC7: expected status='ok'; got status={result.status!r} "
        f"error_code={result.error_code!r} error={result.error!r}"
    )
    assert result.data is not None, "AC7: data must not be None on success path"
    assert result.data["raw_response"] == "HELLO-FROM-RUNNER", (
        f"AC7: raw_response mismatch; got {result.data.get('raw_response')!r}"
    )
    assert result.data["tokens_in"] == {"input_tokens": 5}, (
        f"AC7: tokens_in mismatch; got {result.data.get('tokens_in')!r}"
    )
    assert result.data["tokens_out"] == {"output_tokens": 7}, (
        f"AC7: tokens_out mismatch; got {result.data.get('tokens_out')!r}"
    )
    assert result.data["worker_written_paths"] == ["a.py", "b.py"], (
        f"AC7: worker_written_paths mismatch; got {result.data.get('worker_written_paths')!r}"
    )
    assert result.data.get("manifest_source") == "orchestrator_observed", (  # 4C03CCED Ship 1C
        f"AC7: manifest_source must be 'orchestrator_observed'; "
        f"got {result.data.get('manifest_source')!r}"
    )
    # 25e75663 Class C: "command" key removed from in-session success data (was argv,
    # now backend builds it internally from model=; data carries "model" instead).
    assert result.data["response_bytes"] == len("HELLO-FROM-RUNNER".encode("utf-8")), (
        f"AC7: response_bytes mismatch; got {result.data.get('response_bytes')!r}"
    )

    # Best-effort cleanup: result file should be deleted post-success (G3-AC7)
    assert not result_path.exists(), (
        f"AC7: result artifact must be cleaned up after success; still present at {result_path}"
    )


# ---------------------------------------------------------------------------
# AC8 — telemetry parity: runner_request_built + runner_result_consumed emitted
# ---------------------------------------------------------------------------

def test_AC8_telemetry_events_emitted_on_success(monkeypatch, tmp_path, active_run_ctx):
    """AC8: on success path, event_log has runner_request_built then runner_result_consumed.

    subprocess_spawned / subprocess_exited must be ABSENT.
    resolver_runner_request_dir_resolved must be present with source='env'.

    Pre-GREEN FAIL: backend not in _KNOWN_BACKENDS → E_LLM_BACKEND_UNKNOWN;
    no runner_request_built / runner_result_consumed events emitted; no code paths.
    """
    monkeypatch.setenv("HAL_RUNNER_REQUEST_DIR", str(tmp_path))
    monkeypatch.setattr(_llm_module, "uuid", type("_FakeUUIDMod", (), {"uuid4": staticmethod(_fake_uuid4)})())

    nonce = _DETERMINISTIC_NONCE
    nonce_dir = tmp_path / "RUN-1B" / "phase_X"
    nonce_dir.mkdir(parents=True, exist_ok=True)
    request_path = nonce_dir / f"{nonce}.req.json"
    result_path = nonce_dir / f"{nonce}.res.json"

    result_payload = {
        "request_nonce": nonce,
        "subtype": "success",
        "raw_response": "HELLO-FROM-RUNNER",
        "tokens_in": {"input_tokens": 5},
        "tokens_out": {"output_tokens": 7},
        "worker_written_paths": [],
        "manifest_source": "orchestrator_observed",  # 4C03CCED Ship 1C
        "__hal_integrity": "end",
    }

    def _runner_thread() -> None:
        deadline = time.monotonic() + 10.0
        while time.monotonic() < deadline:
            if request_path.exists():
                tmp_result = str(result_path) + ".tmp"
                with open(tmp_result, "w", encoding="utf-8") as f:
                    json.dump(result_payload, f)
                    f.flush()
                    os.fsync(f.fileno())
                os.rename(tmp_result, str(result_path))
                return
            time.sleep(0.02)

    t = threading.Thread(target=_runner_thread, daemon=True)
    t.start()

    result = invoke_llm_subprocess(
        prompt="AC8-test",
        model=_MODEL,
        timeout_sec=5,
        step_name="t",
        backend="claude-in-session",
    )

    t.join(timeout=12.0)

    assert result.status == "ok", (
        f"AC8 precondition: expected success; got {result.status!r} / {result.error_code!r}"
    )
    # 4C03CCED Ship 1C: manifest_source must be propagated on success path
    assert result.data is not None, "AC8: data must not be None on success"
    assert result.data.get("manifest_source") == "orchestrator_observed", (
        f"AC8: manifest_source must be 'orchestrator_observed'; "
        f"got {result.data.get('manifest_source')!r}"
    )

    log = active_run_ctx  # _FakeEventLog
    event_types = [e[0] for e in log.events]

    # runner_request_built must be present
    built_events = [e for e in log.events if e[0] == "runner_request_built"]
    assert len(built_events) >= 1, (
        f"AC8: 'runner_request_built' must be emitted; got event_types={event_types!r}"
    )
    built = built_events[0][1]
    assert built.get("backend") == "claude-in-session", (
        f"AC8: runner_request_built.backend must be 'claude-in-session'; got {built.get('backend')!r}"
    )
    assert built.get("request_nonce") == nonce, (
        f"AC8: runner_request_built.request_nonce mismatch; got {built.get('request_nonce')!r}"
    )
    assert "model" in built, f"AC8: runner_request_built missing 'model' key; got {built!r}"
    assert "prompt_size_bytes" in built, (
        f"AC8: runner_request_built missing 'prompt_size_bytes'; got {built!r}"
    )
    assert "step_name" in built, (
        f"AC8: runner_request_built missing 'step_name'; got {built!r}"
    )
    # 25e75663 Class C: cmd_kind dropped from runner_request_built payload (always
    # "claude_p" post-migration; now only in subprocess_spawned for subprocess path).
    assert "pid" not in built, (
        f"AC8: runner_request_built must NOT have 'pid' key (not a subprocess); got {built!r}"
    )

    # runner_result_consumed with outcome="success" must be present
    consumed_events = [e for e in log.events if e[0] == "runner_result_consumed"]
    assert len(consumed_events) >= 1, (
        f"AC8: 'runner_result_consumed' must be emitted; got event_types={event_types!r}"
    )
    consumed = consumed_events[-1][1]
    assert consumed.get("backend") == "claude-in-session", (
        f"AC8: runner_result_consumed.backend must be 'claude-in-session'; got {consumed.get('backend')!r}"
    )
    assert consumed.get("request_nonce") == nonce, (
        f"AC8: runner_result_consumed.request_nonce mismatch; got {consumed.get('request_nonce')!r}"
    )
    assert consumed.get("outcome") == "success", (
        f"AC8: runner_result_consumed.outcome must be 'success'; got {consumed.get('outcome')!r}"
    )
    assert "duration_ms" in consumed, (
        f"AC8: runner_result_consumed missing 'duration_ms'; got {consumed!r}"
    )
    assert "response_size_bytes" in consumed, (
        f"AC8: runner_result_consumed missing 'response_size_bytes'; got {consumed!r}"
    )

    # runner_request_built must precede runner_result_consumed in event_log
    built_idx = next(i for i, e in enumerate(log.events) if e[0] == "runner_request_built")
    consumed_idx = next(i for i, e in enumerate(log.events) if e[0] == "runner_result_consumed")
    assert built_idx < consumed_idx, (
        f"AC8: runner_request_built (idx={built_idx}) must precede "
        f"runner_result_consumed (idx={consumed_idx})"
    )

    # subprocess_spawned / subprocess_exited must be ABSENT
    assert "subprocess_spawned" not in event_types, (
        f"AC8: 'subprocess_spawned' must be absent on in-session path; got {event_types!r}"
    )
    assert "subprocess_exited" not in event_types, (
        f"AC8: 'subprocess_exited' must be absent on in-session path; got {event_types!r}"
    )

    # resolver_runner_request_dir_resolved must be present with source='env'
    dir_resolver_events = [
        e for e in log.events if e[0] == "resolver_runner_request_dir_resolved"
    ]
    assert len(dir_resolver_events) >= 1, (
        f"AC8: 'resolver_runner_request_dir_resolved' must be emitted; "
        f"got event_types={event_types!r}"
    )
    dir_resolver_payload = dir_resolver_events[0][1]
    assert dir_resolver_payload.get("source") == "env", (
        f"AC8: dir resolver source must be 'env' when HAL_RUNNER_REQUEST_DIR is set; "
        f"got {dir_resolver_payload.get('source')!r}"
    )
