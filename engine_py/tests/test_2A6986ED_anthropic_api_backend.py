"""RED tests — OSS reference anthropic-api LLM backend (#2A6986ED).

Spec: SHARED/memory/Decisions/2026-06-24_2A6986ED_oss_anthropic_api_backend_spec.md
Agreement: 2A6986ED-1B22-4261-9A66-17AD314AAF9C

13 tests, one per AC1–AC13. ALL must FAIL pre-GREEN because:
  - lib/reference_backends/anthropic_api.py does NOT exist yet
  - "anthropic-api" is not registered in _KNOWN_BACKENDS
  - run.py has no guarded import of the reference battery

Non-collectable RED hazard (D1CF5FDF / §1q): `lib.reference_backends.anthropic_api`
does not exist; a module-level import would raise ImportError at COLLECTION time
(~30-min engine hang). All imports of that module are deferred to _import_backend()
called INSIDE each test — fails at assert/call time (clean FAIL, not collection error).

§1q check: NO spec_from_file_location / exec_module used.
§1i: no singleton-resource / timing fixtures.
§1l stub-passability: anthropic_api_backend / _resolve_model_id are NOT patched here —
  only urllib.request.urlopen and env are patched (infra seam, not UUT).

Pre-GREEN predict: ALL 13 FAIL — ImportError on _import_backend() call for AC1–AC12;
  AC13 FAIL — "anthropic-api" absent from _KNOWN_BACKENDS after importing run.
"""
from __future__ import annotations

import json
import socket
import urllib.error
import urllib.request
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from bytedigger_engine import llm_subprocess
from bytedigger_engine.llm_subprocess import (
    _BACKEND_CAPABILITIES,
    _BACKEND_MANIFEST_SOURCE,
    _KNOWN_BACKENDS,
    invoke_llm_subprocess,
    register_backend,
    reset_backends,
)


# ---------------------------------------------------------------------------
# Helper: deferred import (non-collectable RED guard — D1CF5FDF / §1q)
# ---------------------------------------------------------------------------

def _import_backend():
    """Import the not-yet-existing backend module inside each test.

    Raises ImportError until GREEN creates the module — that ImportError
    is a valid FAIL at assert/call time, never a collection-time error.
    Returns (anthropic_api_backend, _resolve_model_id, register).
    """
    from bytedigger_engine.lib.reference_backends.anthropic_api import (  # noqa: PLC0415
        anthropic_api_backend,
        _resolve_model_id,
        register,
    )
    return anthropic_api_backend, _resolve_model_id, register


# ---------------------------------------------------------------------------
# Fake urlopen helpers
# ---------------------------------------------------------------------------

def _make_fake_urlopen(response_body: dict) -> Any:
    """Return a fake urlopen that yields a successful 200 response."""
    encoded = json.dumps(response_body).encode("utf-8")

    captured: list[Any] = []

    @contextmanager
    def _fake_urlopen(request, timeout=None):
        captured.append(request)
        resp = MagicMock()
        resp.read.return_value = encoded
        resp.__enter__ = lambda s: resp
        resp.__exit__ = MagicMock(return_value=False)
        yield resp

    _fake_urlopen.captured = captured  # type: ignore[attr-defined]
    return _fake_urlopen


def _make_raising_urlopen(exc: BaseException) -> Any:
    """Return a fake urlopen that raises exc immediately."""
    @contextmanager
    def _fake_urlopen(request, timeout=None):
        raise exc
        yield  # pragma: no cover

    return _fake_urlopen


# ---------------------------------------------------------------------------
# Autouse fixture: clean backend registry between tests
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_backend_registry():
    """Restore llm_subprocess backend registry after each test (§1i isolation)."""
    yield
    reset_backends()


# ---------------------------------------------------------------------------
# AC1 — "anthropic-api" registered in _KNOWN_BACKENDS after import
# ---------------------------------------------------------------------------

def test_ac1_backend_registered_in_known_backends():
    """AC1: After importing + calling register(), 'anthropic-api' in _KNOWN_BACKENDS.

    Pre-GREEN FAIL: ImportError from _import_backend() because the module
    does not exist yet.
    """
    _, _, register = _import_backend()
    register()
    assert "anthropic-api" in llm_subprocess._KNOWN_BACKENDS, (
        f"AC1: 'anthropic-api' must be in _KNOWN_BACKENDS after register(); "
        f"got {llm_subprocess._KNOWN_BACKENDS!r}"
    )


# ---------------------------------------------------------------------------
# AC2 — manifest_source and capabilities maps updated correctly
# ---------------------------------------------------------------------------

def test_ac2_manifest_source_and_capabilities():
    """AC2: _BACKEND_MANIFEST_SOURCE["anthropic-api"]=="api_text_response"
    AND _BACKEND_CAPABILITIES["anthropic-api"]==frozenset().

    Pre-GREEN FAIL: ImportError from _import_backend().
    """
    _, _, register = _import_backend()
    register()
    assert llm_subprocess._BACKEND_MANIFEST_SOURCE.get("anthropic-api") == "api_text_response", (
        f"AC2: _BACKEND_MANIFEST_SOURCE['anthropic-api'] must be 'api_text_response'; "
        f"got {llm_subprocess._BACKEND_MANIFEST_SOURCE.get('anthropic-api')!r}"
    )
    assert llm_subprocess._BACKEND_CAPABILITIES.get("anthropic-api") == frozenset(), (
        f"AC2: _BACKEND_CAPABILITIES['anthropic-api'] must be frozenset(); "
        f"got {llm_subprocess._BACKEND_CAPABILITIES.get('anthropic-api')!r}"
    )


# ---------------------------------------------------------------------------
# AC3 — missing ANTHROPIC_API_KEY → E_LLM_API_KEY_MISSING (recoverable=False)
# ---------------------------------------------------------------------------

def test_ac3_missing_api_key_returns_error(monkeypatch):
    """AC3: No ANTHROPIC_API_KEY → status=='error', error_code=='E_LLM_API_KEY_MISSING',
    recoverable is False.

    Pre-GREEN FAIL: ImportError from _import_backend().
    """
    anthropic_api_backend, _, register = _import_backend()
    register()
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)

    result = anthropic_api_backend(
        prompt="hello",
        model="haiku",
        timeout_sec=10,
        step_name="test-ac3",
    )
    assert result.status == "error", (
        f"AC3: missing key must yield status='error'; got {result.status!r}"
    )
    assert result.error_code == "E_LLM_API_KEY_MISSING", (
        f"AC3: error_code must be 'E_LLM_API_KEY_MISSING'; got {result.error_code!r}"
    )
    assert result.recoverable is False, (
        f"AC3: recoverable must be False for missing key; got {result.recoverable!r}"
    )


# ---------------------------------------------------------------------------
# AC4 — happy path returns ok status, raw_response, response_bytes
# ---------------------------------------------------------------------------

def test_ac4_happy_path_returns_ok(monkeypatch):
    """AC4: urlopen→200 body with text 'PONG' → status=='ok',
    data['raw_response']=='PONG', data['response_bytes']==4.

    Pre-GREEN FAIL: ImportError from _import_backend().
    """
    anthropic_api_backend, _, register = _import_backend()
    register()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")

    fake_body = {"content": [{"type": "text", "text": "PONG"}]}
    fake_urlopen = _make_fake_urlopen(fake_body)

    with patch("urllib.request.urlopen", fake_urlopen):
        result = anthropic_api_backend(
            prompt="ping",
            model="haiku",
            timeout_sec=10,
            step_name="test-ac4",
        )

    assert result.status == "ok", (
        f"AC4: happy path must return status='ok'; got {result.status!r}, "
        f"error_code={result.error_code!r}"
    )
    assert result.data is not None, "AC4: data must not be None on ok result"
    assert result.data.get("raw_response") == "PONG", (
        f"AC4: data['raw_response'] must be 'PONG'; got {result.data.get('raw_response')!r}"
    )
    assert result.data.get("response_bytes") == 4, (
        f"AC4: data['response_bytes'] must be 4; got {result.data.get('response_bytes')!r}"
    )


# ---------------------------------------------------------------------------
# AC5 — _resolve_model_id alias mapping + request body uses resolved model
# ---------------------------------------------------------------------------

def test_ac5_resolve_model_id_and_request_body(monkeypatch):
    """AC5: _resolve_model_id('haiku')=='claude-haiku-4-5-20251001';
    _resolve_model_id('claude-opus-4-8')=='claude-opus-4-8' (passthrough);
    captured POST body 'model' key reflects resolved alias.

    Pre-GREEN FAIL: ImportError from _import_backend().
    """
    anthropic_api_backend, _resolve_model_id, register = _import_backend()
    register()

    # _resolve_model_id contract
    assert _resolve_model_id("haiku") == "claude-haiku-4-5-20251001", (
        f"AC5: _resolve_model_id('haiku') must return 'claude-haiku-4-5-20251001'; "
        f"got {_resolve_model_id('haiku')!r}"
    )
    assert _resolve_model_id("claude-opus-4-8") == "claude-opus-4-8", (
        f"AC5: _resolve_model_id('claude-opus-4-8') must pass through unchanged; "
        f"got {_resolve_model_id('claude-opus-4-8')!r}"
    )

    # Capture the posted request body and verify model field
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")
    fake_body = {"content": [{"type": "text", "text": "ok"}]}
    fake_urlopen = _make_fake_urlopen(fake_body)

    with patch("urllib.request.urlopen", fake_urlopen):
        result = anthropic_api_backend(
            prompt="test",
            model="haiku",
            timeout_sec=10,
            step_name="test-ac5",
        )

    assert result.status == "ok", f"AC5: setup call failed: {result.error_code!r}"
    assert len(fake_urlopen.captured) == 1, "AC5: expected exactly one urlopen call"
    req = fake_urlopen.captured[0]
    posted_body = json.loads(req.data.decode("utf-8"))
    assert posted_body.get("model") == "claude-haiku-4-5-20251001", (
        f"AC5: POST body 'model' must be resolved alias 'claude-haiku-4-5-20251001'; "
        f"got {posted_body.get('model')!r}"
    )


# ---------------------------------------------------------------------------
# AC6 — HTTPError(429) → E_LLM_API_HTTP, recoverable=True, "429" in error
# ---------------------------------------------------------------------------

def test_ac6_http_error_429_returns_e_llm_api_http(monkeypatch):
    """AC6: urlopen raises HTTPError(code=429) → status=='error',
    error_code=='E_LLM_API_HTTP', '429' in error, recoverable is True.

    Pre-GREEN FAIL: ImportError from _import_backend().
    """
    anthropic_api_backend, _, register = _import_backend()
    register()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")

    exc = urllib.error.HTTPError(
        url="https://api.anthropic.com/v1/messages",
        code=429,
        msg="Too Many Requests",
        hdrs=MagicMock(),
        fp=None,
    )
    with patch("urllib.request.urlopen", _make_raising_urlopen(exc)):
        result = anthropic_api_backend(
            prompt="test",
            model="haiku",
            timeout_sec=10,
            step_name="test-ac6",
        )

    assert result.status == "error", (
        f"AC6: HTTPError(429) must yield status='error'; got {result.status!r}"
    )
    assert result.error_code == "E_LLM_API_HTTP", (
        f"AC6: error_code must be 'E_LLM_API_HTTP'; got {result.error_code!r}"
    )
    assert result.recoverable is True, (
        f"AC6: E_LLM_API_HTTP must be recoverable=True; got {result.recoverable!r}"
    )
    # "429" must appear somewhere in the error field
    error_str = str(result.error or "")
    assert "429" in error_str, (
        f"AC6: '429' must appear in result.error; got {result.error!r}"
    )


# ---------------------------------------------------------------------------
# AC7 — socket.timeout → E_LLM_TIMEOUT, recoverable=True
# ---------------------------------------------------------------------------

def test_ac7_socket_timeout_returns_e_llm_timeout(monkeypatch):
    """AC7: urlopen raises socket.timeout → error_code=='E_LLM_TIMEOUT',
    recoverable is True.

    Pre-GREEN FAIL: ImportError from _import_backend().
    """
    anthropic_api_backend, _, register = _import_backend()
    register()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")

    with patch("urllib.request.urlopen", _make_raising_urlopen(socket.timeout("timed out"))):
        result = anthropic_api_backend(
            prompt="test",
            model="haiku",
            timeout_sec=5,
            step_name="test-ac7",
        )

    assert result.status == "error", (
        f"AC7: socket.timeout must yield status='error'; got {result.status!r}"
    )
    assert result.error_code == "E_LLM_TIMEOUT", (
        f"AC7: error_code must be 'E_LLM_TIMEOUT'; got {result.error_code!r}"
    )
    assert result.recoverable is True, (
        f"AC7: E_LLM_TIMEOUT must be recoverable=True; got {result.recoverable!r}"
    )


# ---------------------------------------------------------------------------
# AC8 — URLError (DNS/conn) → E_LLM_API_NETWORK, recoverable=True
# ---------------------------------------------------------------------------

def test_ac8_url_error_returns_e_llm_api_network(monkeypatch):
    """AC8: urlopen raises URLError('dns') → error_code=='E_LLM_API_NETWORK',
    recoverable is True.

    Pre-GREEN FAIL: ImportError from _import_backend().
    """
    anthropic_api_backend, _, register = _import_backend()
    register()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")

    with patch("urllib.request.urlopen", _make_raising_urlopen(urllib.error.URLError("dns failure"))):
        result = anthropic_api_backend(
            prompt="test",
            model="haiku",
            timeout_sec=10,
            step_name="test-ac8",
        )

    assert result.status == "error", (
        f"AC8: URLError must yield status='error'; got {result.status!r}"
    )
    assert result.error_code == "E_LLM_API_NETWORK", (
        f"AC8: error_code must be 'E_LLM_API_NETWORK'; got {result.error_code!r}"
    )
    assert result.recoverable is True, (
        f"AC8: E_LLM_API_NETWORK must be recoverable=True; got {result.recoverable!r}"
    )


# ---------------------------------------------------------------------------
# AC9 — 200 with empty content → E_LLM_API_BAD_RESPONSE
# ---------------------------------------------------------------------------

def test_ac9_empty_content_returns_bad_response(monkeypatch):
    """AC9: 200 response with {"content":[]} (no text block) →
    error_code=='E_LLM_API_BAD_RESPONSE'.

    Pre-GREEN FAIL: ImportError from _import_backend().
    """
    anthropic_api_backend, _, register = _import_backend()
    register()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")

    fake_body = {"content": []}
    fake_urlopen = _make_fake_urlopen(fake_body)

    with patch("urllib.request.urlopen", fake_urlopen):
        result = anthropic_api_backend(
            prompt="test",
            model="haiku",
            timeout_sec=10,
            step_name="test-ac9",
        )

    assert result.status == "error", (
        f"AC9: empty content must yield status='error'; got {result.status!r}"
    )
    assert result.error_code == "E_LLM_API_BAD_RESPONSE", (
        f"AC9: error_code must be 'E_LLM_API_BAD_RESPONSE'; got {result.error_code!r}"
    )


# ---------------------------------------------------------------------------
# AC10 — extra_data caller-wins merge
# ---------------------------------------------------------------------------

def test_ac10_extra_data_caller_wins(monkeypatch):
    """AC10: extra_data={'raw_response':'X','k':1} merged caller-wins →
    data['raw_response']=='X' (caller overrides backend value),
    data['k']==1, data['response_bytes'] still present.

    Pre-GREEN FAIL: ImportError from _import_backend().
    """
    anthropic_api_backend, _, register = _import_backend()
    register()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key")

    fake_body = {"content": [{"type": "text", "text": "BACKEND_VALUE"}]}
    fake_urlopen = _make_fake_urlopen(fake_body)

    with patch("urllib.request.urlopen", fake_urlopen):
        result = anthropic_api_backend(
            prompt="test",
            model="haiku",
            timeout_sec=10,
            step_name="test-ac10",
            extra_data={"raw_response": "X", "k": 1},
        )

    assert result.status == "ok", (
        f"AC10: setup call failed: {result.error_code!r}"
    )
    assert result.data is not None, "AC10: data must not be None"
    assert result.data.get("raw_response") == "X", (
        f"AC10: caller extra_data must win on 'raw_response'; "
        f"got {result.data.get('raw_response')!r}"
    )
    assert result.data.get("k") == 1, (
        f"AC10: extra_data key 'k' must be present in data; "
        f"got {result.data.get('k')!r}"
    )
    assert "response_bytes" in result.data, (
        f"AC10: 'response_bytes' must still be present in data; "
        f"got keys={list(result.data.keys())!r}"
    )


# ---------------------------------------------------------------------------
# AC11 — request headers correct + command field has no api key
# ---------------------------------------------------------------------------

def test_ac11_request_headers_and_command_no_api_key(monkeypatch):
    """AC11: Request carries x-api-key==key, anthropic-version=='2023-06-01',
    content-type=='application/json'; command field contains NO api key.

    Pre-GREEN FAIL: ImportError from _import_backend().
    """
    anthropic_api_backend, _, register = _import_backend()
    register()
    api_key = "sk-ant-secret-key-ac11"
    monkeypatch.setenv("ANTHROPIC_API_KEY", api_key)

    fake_body = {"content": [{"type": "text", "text": "hi"}]}
    fake_urlopen = _make_fake_urlopen(fake_body)

    with patch("urllib.request.urlopen", fake_urlopen):
        result = anthropic_api_backend(
            prompt="test",
            model="sonnet",
            timeout_sec=10,
            step_name="test-ac11",
        )

    assert result.status == "ok", f"AC11: setup call failed: {result.error_code!r}"
    assert len(fake_urlopen.captured) == 1, "AC11: expected exactly one urlopen call"
    req = fake_urlopen.captured[0]

    # urllib stores headers lowercased in Request.headers dict
    headers_lower = {k.lower(): v for k, v in req.headers.items()}
    assert headers_lower.get("x-api-key") == api_key, (
        f"AC11: x-api-key header must equal the API key; "
        f"got {headers_lower.get('x-api-key')!r}"
    )
    assert headers_lower.get("anthropic-version") == "2023-06-01", (
        f"AC11: anthropic-version header must be '2023-06-01'; "
        f"got {headers_lower.get('anthropic-version')!r}"
    )
    assert headers_lower.get("content-type") == "application/json", (
        f"AC11: content-type header must be 'application/json'; "
        f"got {headers_lower.get('content-type')!r}"
    )

    # command field must not contain the api key
    assert result.data is not None, "AC11: data must not be None"
    command_str = str(result.data.get("command", []))
    assert api_key not in command_str, (
        f"AC11: command field must NOT contain the api key; "
        f"command={result.data.get('command')!r}"
    )


# ---------------------------------------------------------------------------
# AC12 — integration via invoke_llm_subprocess public API
# ---------------------------------------------------------------------------

def test_ac12_invoke_llm_subprocess_dispatches_to_backend(monkeypatch):
    """AC12: invoke_llm_subprocess(backend='anthropic-api', ...) dispatches to the
    handler and returns the happy-path StepResult.

    Pre-GREEN FAIL: ImportError from _import_backend() (module absent).
    Also: even if imported, 'anthropic-api' not in _KNOWN_BACKENDS pre-GREEN
    → E_LLM_BACKEND_UNKNOWN would fire.
    """
    _, _, register = _import_backend()
    register()
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-key-ac12")

    fake_body = {"content": [{"type": "text", "text": "INTEGRATION_PONG"}]}
    fake_urlopen = _make_fake_urlopen(fake_body)

    with patch("urllib.request.urlopen", fake_urlopen):
        result = invoke_llm_subprocess(
            backend="anthropic-api",
            prompt="ping",
            model="haiku",
            timeout_sec=30,
            step_name="t",
        )

    assert result.status == "ok", (
        f"AC12: invoke_llm_subprocess with backend='anthropic-api' must return "
        f"status='ok'; got status={result.status!r} error_code={result.error_code!r}"
    )
    assert result.data is not None, "AC12: data must not be None on ok result"
    assert result.data.get("raw_response") == "INTEGRATION_PONG", (
        f"AC12: data['raw_response'] must be 'INTEGRATION_PONG'; "
        f"got {result.data.get('raw_response')!r}"
    )


# ---------------------------------------------------------------------------
# AC13 — run.py guarded import registers the backend
# ---------------------------------------------------------------------------

def test_ac13_run_py_guarded_import_registers_backend():
    """AC13: After importing run (which contains the guarded optional import),
    'anthropic-api' is registered in _KNOWN_BACKENDS.

    Pre-GREEN FAIL: run.py does not yet contain the guarded import of
    lib.reference_backends.anthropic_api, so 'anthropic-api' will be absent
    from _KNOWN_BACKENDS after importing run.

    Note: This test imports `run` as a module (no execution, just import-time
    side-effects). The guarded import in run.py fires at import time and registers
    the backend as a side-effect. We reset_backends() in teardown but the import
    of run itself is already cached in sys.modules — testing the import side-effect
    at module level.
    """
    import importlib
    import sys

    # Force fresh evaluation of run module's top-level code by reloading.
    # (sys.modules may cache a pre-guarded-import version.)
    if "run" in sys.modules:
        run_mod = importlib.reload(sys.modules["run"])
    else:
        from bytedigger_engine import run as run_mod  # noqa: PLC0415

    assert "anthropic-api" in llm_subprocess._KNOWN_BACKENDS, (
        f"AC13: importing run.py must register 'anthropic-api' via guarded import; "
        f"got _KNOWN_BACKENDS={llm_subprocess._KNOWN_BACKENDS!r}"
    )
