"""RED tests for 4C03CCED Ship 1A — Runner backend selector at invoke_llm_subprocess seam.

Spec: SHARED/memory/Decisions/2026-05-23_4C03CCED_ship1a_selector_spec.md
Agreement: 4C03CCED (parent SYSTEMATIC — Runner contract)

6 tests, one per AC1-AC6. ALL must FAIL pre-GREEN because:
  - invoke_llm_subprocess has no `backend` kwarg
  - no _KNOWN_BACKENDS / _DEFAULT_BACKEND / _BACKEND_ENV_VAR module constants
  - no _resolve_backend helper
  - no E_LLM_BACKEND_UNKNOWN error code gate
  - no resolver_invoke_llm_subprocess_backend_resolved telemetry event
  - subprocess_spawned event has no "backend" key

Pre-GREEN predict: ALL 6 FAIL (TypeError: unexpected kwarg 'backend' for AC1-AC4;
  TypeError for AC5-AC6 + even if backend kwarg existed, no gate exists and no
  resolver event would be emitted).

§1q check: NO spec_from_file_location / module_from_spec / exec_module used.
§1i N/A: no singleton-resource / timing fixtures.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

HERE = Path(__file__).parent
ENGINE_ROOT = HERE.parent

# engine_py/ on sys.path (mirrors test_llm_subprocess_23680DDA.py pattern).
# suite_safety scanner flags module-level sys.path.insert; this module uses
# the same grandfathered pattern present in every sibling test.
sys.path.insert(0, str(ENGINE_ROOT))

from llm_subprocess import invoke_llm_subprocess  # noqa: E402
import telemetry_ctx  # noqa: E402


# ─── Fake event log (tuple-based, mirrors test_subprocess_telemetry.py) ───────


class _FakeEventLog:
    """Captures (event_type, payload, run_id) tuples for assertion."""

    def __init__(self):
        self.events: list[tuple[str, dict, str]] = []

    def append(self, event_type: str, payload: dict, run_id: str | None = None) -> None:
        self.events.append((event_type, payload, run_id or "ad-hoc"))


# ─── Fake Popen proc (success path) ──────────────────────────────────────────

# Canonical 3-event success stream (mirrors test_llm_subprocess_23680DDA.py).
_SYSTEM_INIT = '{"type":"system","subtype":"init","session_id":"s1"}'
_ASSISTANT_DELTA = (
    '{"type":"assistant","message":{"content":[{"type":"text","text":"Hi"}],'
    '"usage":{"input_tokens":0,"output_tokens":0}}}'
)
_RESULT_OK = (
    '{"type":"result","subtype":"success",'
    '"result":"BACKEND_SELECTOR_ANSWER",'
    '"usage":{"input_tokens":5,"output_tokens":5,'
    '"cache_read_input_tokens":0,"cache_creation_input_tokens":0},'
    '"total_cost_usd":0.001,"duration_ms":100}'
)


def _success_stdout() -> str:
    lines = [_SYSTEM_INIT, _ASSISTANT_DELTA, _RESULT_OK]
    return "".join(line + "\n" for line in lines)


def _make_success_proc() -> MagicMock:
    """Return a mock Popen process that simulates a successful stream-json run."""
    proc = MagicMock()
    proc.pid = 55555
    proc.returncode = 0
    proc.stdout = io.StringIO(_success_stdout())
    proc.stderr = io.StringIO("")
    proc.stdin = MagicMock()
    proc.wait = MagicMock(return_value=0)
    proc.communicate = MagicMock(return_value=(_success_stdout(), ""))
    return proc


# ─── Autouse fixture: clear telemetry between tests ──────────────────────────


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _clear_telemetry_ctx():
    """Reset telemetry run context before and after every test (mirrors 966E571B pattern)."""
    telemetry_ctx.clear_current_run()
    yield
    telemetry_ctx.clear_current_run()


# ─── AC1 — default path: backend=None + env unset → source="default" ─────────


def test_ac1_default_backend_no_kwarg_no_env(monkeypatch):
    """AC1: invoke_llm_subprocess(backend=None) + HAL_RUNNER_BACKEND unset.

    Spec AC1: resolver returns ("claude-subprocess", "default"); existing
    claude-subprocess dispatch path runs; subprocess_spawned event includes
    "backend": "claude-subprocess"; resolver_invoke_llm_subprocess_backend_resolved
    event with source="default", value="claude-subprocess" precedes subprocess_spawned
    in event_log.

    Pre-GREEN: FAIL — TypeError: unexpected keyword argument 'backend';
    even if kwarg existed, resolver event would not be emitted and
    subprocess_spawned would have no "backend" key.
    """
    monkeypatch.delenv("HAL_RUNNER_BACKEND", raising=False)

    log = _FakeEventLog()
    telemetry_ctx.set_current_run(event_log=log, run_id="ac1-run", step_name="s", phase="p")

    with patch("llm_subprocess.subprocess.Popen", return_value=_make_success_proc()):
        result = invoke_llm_subprocess(
            prompt="x",
            model="claude-opus-4-5",
            timeout_sec=10,
            step_name="s",
            backend=None,  # explicit None — must resolve to default
        )

    # 1. Call completed without E_LLM_BACKEND_UNKNOWN
    assert result.status == "ok", (
        f"AC1: default backend must allow dispatch to proceed; got status={result.status!r} "
        f"error_code={result.error_code!r}"
    )

    event_types = [e[0] for e in log.events]

    # 2. resolver event is present with correct source + value
    resolver_events = [
        e for e in log.events
        if e[0] == "resolver_invoke_llm_subprocess_backend_resolved"
    ]
    assert len(resolver_events) >= 1, (
        f"AC1: expected resolver_invoke_llm_subprocess_backend_resolved event; "
        f"got event_types={event_types!r}"
    )
    resolver_payload = resolver_events[0][1]
    assert resolver_payload.get("source") == "default", (
        f"AC1: resolver source must be 'default' when no kwarg/env; "
        f"got {resolver_payload.get('source')!r}"
    )
    # GH1001 C0B6D653 (2026-07-19): default-resolver value flipped
    # claude-subprocess -> agent-sdk; this is the pre-fallback resolver
    # event, so it reports the raw resolved value before any GH1001 rebind.
    assert resolver_payload.get("value") == "agent-sdk", (
        f"AC1: resolver value must be 'agent-sdk'; "
        f"got {resolver_payload.get('value')!r}"
    )

    # 3. subprocess_spawned event has "backend" key = "claude-subprocess"
    spawned_events = [e for e in log.events if e[0] == "subprocess_spawned"]
    assert len(spawned_events) == 1, (
        f"AC1: expected exactly one subprocess_spawned event; got {event_types!r}"
    )
    spawned_payload = spawned_events[0][1]
    assert spawned_payload.get("backend") == "claude-subprocess", (
        f"AC1: subprocess_spawned must carry backend='claude-subprocess'; "
        f"got backend={spawned_payload.get('backend')!r}"
    )

    # 4. resolver event PRECEDES subprocess_spawned in event_log (ordering)
    resolver_idx = next(
        i for i, e in enumerate(log.events)
        if e[0] == "resolver_invoke_llm_subprocess_backend_resolved"
    )
    spawned_idx = next(
        i for i, e in enumerate(log.events)
        if e[0] == "subprocess_spawned"
    )
    assert resolver_idx < spawned_idx, (
        f"AC1: resolver event must precede subprocess_spawned; "
        f"resolver_idx={resolver_idx} spawned_idx={spawned_idx}"
    )


# ─── AC2 — explicit kwarg "claude-subprocess" → source="kwarg" ───────────────


def test_ac2_explicit_kwarg_claude_subprocess(monkeypatch):
    """AC2: invoke_llm_subprocess(backend="claude-subprocess") + env unset.

    Spec AC2: resolver returns ("claude-subprocess", "kwarg");
    resolver_invoke_llm_subprocess_backend_resolved source="kwarg";
    dispatch unchanged; subprocess_spawned backend="claude-subprocess".

    Pre-GREEN: FAIL — TypeError: unexpected keyword argument 'backend'.
    """
    monkeypatch.delenv("HAL_RUNNER_BACKEND", raising=False)

    log = _FakeEventLog()
    telemetry_ctx.set_current_run(event_log=log, run_id="ac2-run", step_name="s", phase="p")

    with patch("llm_subprocess.subprocess.Popen", return_value=_make_success_proc()):
        result = invoke_llm_subprocess(
            prompt="x",
            model="claude-opus-4-5",
            timeout_sec=10,
            step_name="s",
            backend="claude-subprocess",
        )

    assert result.status == "ok", (
        f"AC2: explicit backend='claude-subprocess' kwarg must allow dispatch; "
        f"got status={result.status!r} error_code={result.error_code!r}"
    )

    resolver_events = [
        e for e in log.events
        if e[0] == "resolver_invoke_llm_subprocess_backend_resolved"
    ]
    assert len(resolver_events) >= 1, (
        f"AC2: expected resolver event; got events={[e[0] for e in log.events]!r}"
    )
    resolver_payload = resolver_events[0][1]
    assert resolver_payload.get("source") == "kwarg", (
        f"AC2: resolver source must be 'kwarg' when kwarg is provided; "
        f"got {resolver_payload.get('source')!r}"
    )
    assert resolver_payload.get("value") == "claude-subprocess", (
        f"AC2: resolver value must be 'claude-subprocess'; "
        f"got {resolver_payload.get('value')!r}"
    )

    spawned_events = [e for e in log.events if e[0] == "subprocess_spawned"]
    assert len(spawned_events) == 1, (
        f"AC2: expected exactly one subprocess_spawned event"
    )
    assert spawned_events[0][1].get("backend") == "claude-subprocess", (
        f"AC2: subprocess_spawned must carry backend='claude-subprocess'; "
        f"got {spawned_events[0][1].get('backend')!r}"
    )


# ─── AC3 — env HAL_RUNNER_BACKEND=claude-subprocess + kwarg=None → source="env"


def test_ac3_env_backend_claude_subprocess(monkeypatch):
    """AC3: invoke_llm_subprocess(backend=None) + HAL_RUNNER_BACKEND=claude-subprocess.

    Spec AC3: resolver returns ("claude-subprocess", "env");
    resolver_invoke_llm_subprocess_backend_resolved source="env";
    dispatch unchanged.

    Pre-GREEN: FAIL — TypeError: unexpected keyword argument 'backend'.
    """
    monkeypatch.setenv("HAL_RUNNER_BACKEND", "claude-subprocess")

    log = _FakeEventLog()
    telemetry_ctx.set_current_run(event_log=log, run_id="ac3-run", step_name="s", phase="p")

    with patch("llm_subprocess.subprocess.Popen", return_value=_make_success_proc()):
        result = invoke_llm_subprocess(
            prompt="x",
            model="claude-opus-4-5",
            timeout_sec=10,
            step_name="s",
            backend=None,
        )

    assert result.status == "ok", (
        f"AC3: HAL_RUNNER_BACKEND=claude-subprocess must allow dispatch; "
        f"got status={result.status!r} error_code={result.error_code!r}"
    )

    resolver_events = [
        e for e in log.events
        if e[0] == "resolver_invoke_llm_subprocess_backend_resolved"
    ]
    assert len(resolver_events) >= 1, (
        f"AC3: expected resolver event; got events={[e[0] for e in log.events]!r}"
    )
    resolver_payload = resolver_events[0][1]
    assert resolver_payload.get("source") == "env", (
        f"AC3: resolver source must be 'env' when HAL_RUNNER_BACKEND is set; "
        f"got {resolver_payload.get('source')!r}"
    )
    assert resolver_payload.get("value") == "claude-subprocess", (
        f"AC3: resolver value must be 'claude-subprocess'; "
        f"got {resolver_payload.get('value')!r}"
    )

    spawned_events = [e for e in log.events if e[0] == "subprocess_spawned"]
    assert len(spawned_events) == 1, "AC3: expected exactly one subprocess_spawned event"
    assert spawned_events[0][1].get("backend") == "claude-subprocess", (
        f"AC3: subprocess_spawned must carry backend='claude-subprocess'"
    )


# ─── AC4 — kwarg WINS over env (precedence: kwarg > env) ─────────────────────


def test_ac4_kwarg_wins_over_env_precedence(monkeypatch):
    """AC4: kwarg="claude-subprocess" + HAL_RUNNER_BACKEND=unknown-xyz → kwarg wins.

    Spec AC4: resolver returns ("claude-subprocess", "kwarg"); NO error; dispatch proceeds.
    Inverse of AC5: env-unknown does NOT trip the gate when kwarg overrides.

    Pre-GREEN: FAIL — TypeError: unexpected keyword argument 'backend'.
    """
    monkeypatch.setenv("HAL_RUNNER_BACKEND", "unknown-xyz")

    log = _FakeEventLog()
    telemetry_ctx.set_current_run(event_log=log, run_id="ac4-run", step_name="s", phase="p")

    with patch("llm_subprocess.subprocess.Popen", return_value=_make_success_proc()):
        result = invoke_llm_subprocess(
            prompt="x",
            model="claude-opus-4-5",
            timeout_sec=10,
            step_name="s",
            backend="claude-subprocess",  # kwarg must WIN over env unknown-xyz
        )

    # Must NOT return E_LLM_BACKEND_UNKNOWN — kwarg overrides env
    assert result.status == "ok", (
        f"AC4: kwarg 'claude-subprocess' must override env unknown-xyz and allow dispatch; "
        f"got status={result.status!r} error_code={result.error_code!r}"
    )
    assert result.error_code != "E_LLM_BACKEND_UNKNOWN", (
        f"AC4: E_LLM_BACKEND_UNKNOWN must NOT fire when kwarg overrides env; "
        f"got error_code={result.error_code!r}"
    )

    resolver_events = [
        e for e in log.events
        if e[0] == "resolver_invoke_llm_subprocess_backend_resolved"
    ]
    assert len(resolver_events) >= 1, (
        f"AC4: expected resolver event; got events={[e[0] for e in log.events]!r}"
    )
    resolver_payload = resolver_events[0][1]
    # Forcing-function: source must be "kwarg" not "env" or "default"
    assert resolver_payload.get("source") == "kwarg", (
        f"AC4: resolver source must be 'kwarg' (kwarg overrides env); "
        f"got {resolver_payload.get('source')!r}"
    )
    assert resolver_payload.get("value") == "claude-subprocess", (
        f"AC4: resolver value must be 'claude-subprocess'; "
        f"got {resolver_payload.get('value')!r}"
    )

    # Popen was called (dispatch did proceed)
    spawned_events = [e for e in log.events if e[0] == "subprocess_spawned"]
    assert len(spawned_events) == 1, (
        f"AC4: subprocess must have been spawned (dispatch proceeded); "
        f"got spawned_events count={len(spawned_events)}"
    )


# ─── AC5 — unknown kwarg backend → E_LLM_BACKEND_UNKNOWN, no spawn (FORCING) ─


def test_ac5_unknown_backend_kwarg_e_llm_backend_unknown(monkeypatch):
    """AC5 (FORCING-FUNCTION): backend="unknown-xyz" kwarg → E_LLM_BACKEND_UNKNOWN, no spawn.

    Spec AC5: returns StepResult(status="error", data=None, error_code="E_LLM_BACKEND_UNKNOWN",
    recoverable=False); subprocess.Popen mock invocation-count=0 (NO subprocess spawned);
    subprocess_spawned event NOT in event_log;
    resolver_invoke_llm_subprocess_backend_resolved IS in event_log with
    source="kwarg", value="unknown-xyz".

    Pre-GREEN: FAIL — TypeError: unexpected keyword argument 'backend'; even if kwarg
    existed, no gate exists and Popen WOULD be called (call_count > 0).
    """
    monkeypatch.delenv("HAL_RUNNER_BACKEND", raising=False)

    log = _FakeEventLog()
    telemetry_ctx.set_current_run(event_log=log, run_id="ac5-run", step_name="s", phase="p")

    with patch("llm_subprocess.subprocess.Popen") as mock_popen:
        result = invoke_llm_subprocess(
            prompt="x",
            model="claude-opus-4-5",
            timeout_sec=10,
            step_name="s",
            backend="unknown-xyz",
        )

    # 1. StepResult shape: terminal error
    assert result.status == "error", (
        f"AC5: unknown backend must yield status=error; got {result.status!r}"
    )
    assert result.data is None, (
        f"AC5: E_LLM_BACKEND_UNKNOWN must have data=None (terminal dispatch invariant); "
        f"got data={result.data!r}"
    )
    assert result.error_code == "E_LLM_BACKEND_UNKNOWN", (
        f"AC5: error_code must be 'E_LLM_BACKEND_UNKNOWN'; got {result.error_code!r}"
    )
    assert result.recoverable is False, (
        f"AC5: E_LLM_BACKEND_UNKNOWN must be recoverable=False; "
        f"got recoverable={result.recoverable!r}"
    )

    # 2. Popen NOT called — gate-before-spawn semantics
    assert mock_popen.call_count == 0, (
        f"AC5: subprocess.Popen must NOT be called when backend is unknown; "
        f"got call_count={mock_popen.call_count}"
    )

    event_types = [e[0] for e in log.events]

    # 3. subprocess_spawned ABSENT from event_log
    assert "subprocess_spawned" not in event_types, (
        f"AC5: subprocess_spawned must NOT appear in event_log when gate fires; "
        f"got events={event_types!r}"
    )

    # 4. resolver event IS present with correct source + value
    resolver_events = [
        e for e in log.events
        if e[0] == "resolver_invoke_llm_subprocess_backend_resolved"
    ]
    assert len(resolver_events) >= 1, (
        f"AC5: resolver event must be emitted even when backend is unknown; "
        f"got events={event_types!r}"
    )
    resolver_payload = resolver_events[0][1]
    assert resolver_payload.get("source") == "kwarg", (
        f"AC5: resolver source must be 'kwarg'; got {resolver_payload.get('source')!r}"
    )
    assert resolver_payload.get("value") == "unknown-xyz", (
        f"AC5: resolver value must be 'unknown-xyz'; got {resolver_payload.get('value')!r}"
    )


# ─── AC6 — unknown env backend + kwarg=None → E_LLM_BACKEND_UNKNOWN ──────────


def test_ac6_unknown_env_backend_e_llm_backend_unknown(monkeypatch):
    """AC6: HAL_RUNNER_BACKEND=unknown-xyz + backend=None kwarg → E_LLM_BACKEND_UNKNOWN.

    Spec AC6: returns same shape as AC5; resolver_invoke_llm_subprocess_backend_resolved
    source="env", value="unknown-xyz".

    Pre-GREEN: FAIL — TypeError: unexpected keyword argument 'backend'; even if kwarg
    existed, no gate exists and Popen WOULD be called (call_count > 0).
    """
    monkeypatch.setenv("HAL_RUNNER_BACKEND", "unknown-xyz")

    log = _FakeEventLog()
    telemetry_ctx.set_current_run(event_log=log, run_id="ac6-run", step_name="s", phase="p")

    with patch("llm_subprocess.subprocess.Popen") as mock_popen:
        result = invoke_llm_subprocess(
            prompt="x",
            model="claude-opus-4-5",
            timeout_sec=10,
            step_name="s",
            backend=None,
        )

    # 1. StepResult shape: terminal error (same shape as AC5)
    assert result.status == "error", (
        f"AC6: HAL_RUNNER_BACKEND=unknown-xyz must yield status=error; got {result.status!r}"
    )
    assert result.data is None, (
        f"AC6: E_LLM_BACKEND_UNKNOWN must have data=None; got data={result.data!r}"
    )
    assert result.error_code == "E_LLM_BACKEND_UNKNOWN", (
        f"AC6: error_code must be 'E_LLM_BACKEND_UNKNOWN'; got {result.error_code!r}"
    )
    assert result.recoverable is False, (
        f"AC6: E_LLM_BACKEND_UNKNOWN must be recoverable=False; "
        f"got recoverable={result.recoverable!r}"
    )

    # 2. Popen NOT called
    assert mock_popen.call_count == 0, (
        f"AC6: subprocess.Popen must NOT be called when env backend is unknown; "
        f"got call_count={mock_popen.call_count}"
    )

    event_types = [e[0] for e in log.events]

    # 3. subprocess_spawned ABSENT from event_log
    assert "subprocess_spawned" not in event_types, (
        f"AC6: subprocess_spawned must NOT appear when gate fires; "
        f"got events={event_types!r}"
    )

    # 4. resolver event IS present — source="env" distinguishes from AC5
    resolver_events = [
        e for e in log.events
        if e[0] == "resolver_invoke_llm_subprocess_backend_resolved"
    ]
    assert len(resolver_events) >= 1, (
        f"AC6: resolver event must be emitted even when env backend is unknown; "
        f"got events={event_types!r}"
    )
    resolver_payload = resolver_events[0][1]
    assert resolver_payload.get("source") == "env", (
        f"AC6: resolver source must be 'env' when resolved from HAL_RUNNER_BACKEND; "
        f"got {resolver_payload.get('source')!r}"
    )
    assert resolver_payload.get("value") == "unknown-xyz", (
        f"AC6: resolver value must be 'unknown-xyz'; got {resolver_payload.get('value')!r}"
    )
