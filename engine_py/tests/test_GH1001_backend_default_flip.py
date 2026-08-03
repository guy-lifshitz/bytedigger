"""RED tests for C0B6D653 (GH1001) — engine `_DEFAULT_BACKEND` flip
claude-subprocess -> agent-sdk, with graceful default-path fallback.

Spec: SHARED/memory/Decisions/2026-07-19_C0B6D653_backend_default_flip_spec.md

7 tests, one per AC1-AC7. Pre-GREEN predict:
  A1 — FAIL: _DEFAULT_BACKEND is still "claude-subprocess".
  A2 — FAIL: _resolve_backend(None, {}) returns ("claude-subprocess", "default").
  A3 — FAIL: no fallback rebind exists; default-path with agent-sdk absent
       currently dispatches "claude-subprocess" with source "default" (not
       "default-fallback") because _DEFAULT_BACKEND itself is still subprocess.
       Once _DEFAULT_BACKEND flips, default-path resolves to "agent-sdk" which
       is NOT in _KNOWN_BACKENDS (unregistered) -> today this trips
       E_LLM_BACKEND_UNKNOWN, not the new fallback -> FAIL.
  A4 — FAIL: default resolves to "claude-subprocess" today so a registered
       stub "agent-sdk" backend is never dispatched on the default path.
  A5 — regression-guard: fail-loud already correct today (pre-existing
       behavior); documented as PASS-today in its docstring.
  A6 — regression-guard: same as A5, kwarg path; PASS-today.
  A7 — regression-guard: explicit env pin already dispatches subprocess with
       source "env" today; PASS-today (rollback lever must keep working).

§1q: no exec_module/spec_from_file_location; conftest.py injects engine_py
root into sys.path at import time (mirrors test_68E964FB pattern) — plain
`import bytedigger_engine.llm_subprocess` / `import bytedigger_engine.telemetry_ctx`.
§1i: A3/A4 use reset_backends()/register_backend() teardown (existing
fixture pattern shared with test_68E964FB) — no timing races, state is
pre-staged deterministically before each UUT call.
§1l: A3 anchors the real event-log side-effect via a run_ctx fixture and a
mocked-Popen real dispatch through invoke_llm_subprocess (the real UUT) —
never mocks invoke_llm_subprocess itself.
"""
from __future__ import annotations

import io
import os

import pytest

# conftest.py injects engine_py root into sys.path at import time (§1q singleton).
from bytedigger_engine import llm_subprocess
from bytedigger_engine import telemetry_ctx


@pytest.fixture(autouse=True)
def _reset_backends_registry():
    """§1i: pre-stage the backend registry to the clean default before/after
    each test — mirrors test_68E964FB's teardown fixture."""
    llm_subprocess.reset_backends()
    telemetry_ctx.clear_current_run()
    yield
    llm_subprocess.reset_backends()
    telemetry_ctx.clear_current_run()


# ─── Fake event log (tuple-based, mirrors test_4C03CCED pattern) ─────────────


class _FakeEventLog:
    def __init__(self):
        self.events: list[tuple[str, dict, str]] = []

    def append(self, event_type: str, payload: dict, run_id: str | None = None) -> None:
        self.events.append((event_type, payload, run_id or "ad-hoc"))


_SYSTEM_INIT = '{"type":"system","subtype":"init","session_id":"s1"}'
_ASSISTANT_DELTA = (
    '{"type":"assistant","message":{"content":[{"type":"text","text":"Hi"}],'
    '"usage":{"input_tokens":0,"output_tokens":0}}}'
)
_RESULT_OK = (
    '{"type":"result","subtype":"success",'
    '"result":"GH1001_ANSWER",'
    '"usage":{"input_tokens":5,"output_tokens":5,'
    '"cache_read_input_tokens":0,"cache_creation_input_tokens":0},'
    '"total_cost_usd":0.001,"duration_ms":100}'
)


def _success_stdout() -> str:
    lines = [_SYSTEM_INIT, _ASSISTANT_DELTA, _RESULT_OK]
    return "".join(line + "\n" for line in lines)


def _make_success_proc():
    from unittest.mock import MagicMock

    proc = MagicMock()
    proc.pid = 55555
    proc.returncode = 0
    proc.stdout = io.StringIO(_success_stdout())
    proc.stderr = io.StringIO("")
    proc.stdin = MagicMock()
    proc.wait = MagicMock(return_value=0)
    proc.communicate = MagicMock(return_value=(_success_stdout(), ""))
    return proc


def _minimal_invoke_kwargs(**overrides):
    base = dict(
        prompt="x",
        model="sonnet",
        timeout_sec=5,
        step_name="test-GH1001",
    )
    base.update(overrides)
    return base


# ─── A1 — module constant flip ────────────────────────────────────────────────


def test_a1_default_backend_constant_is_agent_sdk():
    """A1: llm_subprocess._DEFAULT_BACKEND == "agent-sdk".

    Pre-GREEN: FAIL — constant is still "claude-subprocess".
    """
    assert llm_subprocess._DEFAULT_BACKEND == "agent-sdk", (
        f"A1: _DEFAULT_BACKEND must be 'agent-sdk'; got "
        f"{llm_subprocess._DEFAULT_BACKEND!r}"
    )


# ─── A2 — resolver flows the new default through unchanged ───────────────────


def test_a2_resolve_backend_default_returns_agent_sdk():
    """A2: _resolve_backend(None, {}) returns ("agent-sdk", "default").

    Pre-GREEN: FAIL — returns ("claude-subprocess", "default") today.
    """
    result, source = llm_subprocess._resolve_backend(None, {})
    assert result == "agent-sdk", f"A2: expected 'agent-sdk', got {result!r}"
    assert source == "default", f"A2: expected source 'default', got {source!r}"


# ─── A3 — graceful fallback: default path, agent-sdk absent ──────────────────


def test_a3_default_path_falls_back_to_subprocess_when_agent_sdk_absent(monkeypatch):
    """A3: reset_backends() (agent-sdk absent) + default-path invocation
    dispatches "claude-subprocess"; event log has exactly one
    runner_backend_resolved event with {"backend":"claude-subprocess",
    "source":"default-fallback"}; status is NOT E_LLM_BACKEND_UNKNOWN.

    Pre-GREEN: FAIL — no fallback rebind exists yet, so once A1/A2 land the
    default path resolves to "agent-sdk" (unregistered) and trips
    E_LLM_BACKEND_UNKNOWN instead of falling back; today (pre-A1) it also
    fails because source is "default", not "default-fallback".
    """
    monkeypatch.delenv("HAL_RUNNER_BACKEND", raising=False)

    log = _FakeEventLog()
    telemetry_ctx.set_current_run(event_log=log, run_id="a3-run", step_name="s", phase="p")

    from unittest.mock import patch

    with patch("bytedigger_engine.llm_subprocess.subprocess.Popen", return_value=_make_success_proc()):
        result = llm_subprocess.invoke_llm_subprocess(**_minimal_invoke_kwargs(backend=None))

    assert result.error_code != "E_LLM_BACKEND_UNKNOWN", (
        f"A3: default-path fallback must not trip E_LLM_BACKEND_UNKNOWN; "
        f"got error_code={result.error_code!r} status={result.status!r}"
    )

    resolved_events = [e for e in log.events if e[0] == "runner_backend_resolved"]
    assert len(resolved_events) == 1, (
        f"A3: expected exactly one runner_backend_resolved event; "
        f"got {[e[0] for e in log.events]!r}"
    )
    payload = resolved_events[0][1]
    assert payload.get("backend") == "claude-subprocess", (
        f"A3: fallback must dispatch 'claude-subprocess'; got {payload.get('backend')!r}"
    )
    assert payload.get("source") == "default-fallback", (
        f"A3: fallback source must be 'default-fallback'; got {payload.get('source')!r}"
    )


# ─── A4 — registered path: stub agent-sdk backend dispatches, source=default ─


def test_a4_default_path_dispatches_registered_agent_sdk_backend(monkeypatch):
    """A4: with a stub agent-sdk backend registered via register_backend(...,
    overwrite=True), default-path invocation dispatches it; event source
    == "default" (no fallback).

    Pre-GREEN: FAIL — default resolves to "claude-subprocess" today, so the
    registered agent-sdk stub is never invoked on the default path.
    """
    monkeypatch.delenv("HAL_RUNNER_BACKEND", raising=False)
    from unittest.mock import MagicMock

    stub = MagicMock(return_value=llm_subprocess.StepResult(
        status="ok",
        data={"raw_response": "stub-agent-sdk-answer"},
        duration_ms=1,
        step_name="test-GH1001",
        error=None,
        error_code=None,
        recoverable=True,
    ))
    llm_subprocess.register_backend(
        "agent-sdk", stub, manifest_source="test-stub", overwrite=True,
    )

    log = _FakeEventLog()
    telemetry_ctx.set_current_run(event_log=log, run_id="a4-run", step_name="s", phase="p")

    result = llm_subprocess.invoke_llm_subprocess(**_minimal_invoke_kwargs(backend=None))

    assert stub.called, "A4: registered agent-sdk stub was NOT dispatched on default path"
    assert result.status == "ok", (
        f"A4: default-path dispatch to registered agent-sdk must succeed; "
        f"got status={result.status!r} error_code={result.error_code!r}"
    )

    resolved_events = [e for e in log.events if e[0] == "runner_backend_resolved"]
    assert len(resolved_events) == 1, (
        f"A4: expected exactly one runner_backend_resolved event; "
        f"got {[e[0] for e in log.events]!r}"
    )
    payload = resolved_events[0][1]
    assert payload.get("backend") == "agent-sdk", (
        f"A4: resolved backend must be 'agent-sdk'; got {payload.get('backend')!r}"
    )
    assert payload.get("source") == "default", (
        f"A4: source must be 'default' (no fallback needed); got {payload.get('source')!r}"
    )


# ─── A5 — explicit env selection stays fail-loud ──────────────────────────────


def test_a5_explicit_env_agent_sdk_absent_fails_loud(monkeypatch):
    """A5: explicit env HAL_RUNNER_BACKEND=agent-sdk with agent-sdk absent
    -> E_LLM_BACKEND_UNKNOWN, message contains 'pip install claude-agent-sdk'.

    Regression-guard / PASS-today: fail-loud gate + GH898 install hint already
    exist and are unconditional on 'source' — explicit env selection of an
    unregistered backend already returns E_LLM_BACKEND_UNKNOWN today, before
    and after the default flip (spec: explicit selections never fall back).
    """
    monkeypatch.setenv("HAL_RUNNER_BACKEND", "agent-sdk")

    result = llm_subprocess.invoke_llm_subprocess(**_minimal_invoke_kwargs(backend=None))

    assert result.status == "error", f"A5: expected status=error, got {result.status!r}"
    assert result.error_code == "E_LLM_BACKEND_UNKNOWN", (
        f"A5: expected E_LLM_BACKEND_UNKNOWN, got {result.error_code!r}"
    )
    assert "pip install claude-agent-sdk" in (result.error or ""), (
        f"A5: error message must contain the GH898 install hint; got {result.error!r}"
    )


# ─── A6 — explicit kwarg selection stays fail-loud ────────────────────────────


def test_a6_explicit_kwarg_agent_sdk_absent_fails_loud(monkeypatch):
    """A6: explicit kwarg backend="agent-sdk" with agent-sdk absent -> same
    fail-loud as A5 (no fallback for explicit selections).

    Regression-guard / PASS-today: same fail-loud gate as A5, kwarg source.
    """
    monkeypatch.delenv("HAL_RUNNER_BACKEND", raising=False)

    result = llm_subprocess.invoke_llm_subprocess(
        **_minimal_invoke_kwargs(backend="agent-sdk")
    )

    assert result.status == "error", f"A6: expected status=error, got {result.status!r}"
    assert result.error_code == "E_LLM_BACKEND_UNKNOWN", (
        f"A6: expected E_LLM_BACKEND_UNKNOWN, got {result.error_code!r}"
    )
    assert "pip install claude-agent-sdk" in (result.error or ""), (
        f"A6: error message must contain the GH898 install hint; got {result.error!r}"
    )


# ─── A7 — rollback lever: env pin to claude-subprocess still works ───────────


def test_a7_env_pin_claude_subprocess_still_dispatches(monkeypatch):
    """A7: env HAL_RUNNER_BACKEND=claude-subprocess -> dispatches subprocess,
    source "env" (rollback lever (a) from the spec).

    Regression-guard / PASS-today: explicit env selection of a known backend
    already dispatches correctly today; must keep working post-flip as the
    zero-code rollback lever.
    """
    monkeypatch.setenv("HAL_RUNNER_BACKEND", "claude-subprocess")

    log = _FakeEventLog()
    telemetry_ctx.set_current_run(event_log=log, run_id="a7-run", step_name="s", phase="p")

    from unittest.mock import patch

    with patch("bytedigger_engine.llm_subprocess.subprocess.Popen", return_value=_make_success_proc()):
        result = llm_subprocess.invoke_llm_subprocess(**_minimal_invoke_kwargs(backend=None))

    assert result.status == "ok", (
        f"A7: env pin to claude-subprocess must dispatch successfully; "
        f"got status={result.status!r} error_code={result.error_code!r}"
    )

    resolved_events = [e for e in log.events if e[0] == "runner_backend_resolved"]
    assert len(resolved_events) == 1, (
        f"A7: expected exactly one runner_backend_resolved event; "
        f"got {[e[0] for e in log.events]!r}"
    )
    payload = resolved_events[0][1]
    assert payload.get("backend") == "claude-subprocess", (
        f"A7: resolved backend must be 'claude-subprocess'; got {payload.get('backend')!r}"
    )
    assert payload.get("source") == "env", (
        f"A7: source must be 'env'; got {payload.get('source')!r}"
    )
