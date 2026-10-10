"""RED tests for bd#83: OpenAI-compatible provider with a base_url, and a phase_6
review path that needs no file tools.

Spec: docs/decisions/2026-10-10-bd83-openai-compat-provider.md (AC1-AC15).

Hermetic: every HTTP call goes to a stdlib http.server bound to 127.0.0.1:0 in a
thread. No external network. The new backend module and the new phase_6 helper are
reached lazily inside each test, so the file collects and every test fails at call
time (missing module / missing behaviour), independently of the others.

Singleton-resource AC (slow server, refused connection): the contested resource is
pre-staged deterministically. The slow server blocks on a threading.Event that the
test releases at teardown; the refused port is a socket bound but never listening.
No sleep-vs-timeout race is relied on beyond "the client gave up before the event".
"""
from __future__ import annotations

import http.client
import importlib
import json
import socket
import struct
import sys
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from bytedigger_engine import llm_subprocess, telemetry_ctx
from bytedigger_engine.contracts import StepResult, WorkflowContext
from bytedigger_engine.event_log import EventLog
from bytedigger_engine.llm_subprocess import (
    _BACKEND_CAPABILITIES,
    _BACKEND_MANIFEST_SOURCE,
    invoke_llm_subprocess,
    register_backend,
    reset_backends,
)
from bytedigger_engine.workflows import phase_6_review

ENGINE_PY = Path(__file__).resolve().parents[1]
BACKEND = "openai-compat"
KEY = "sk-local-secret-bd83-xyz"
_OK_BODY = {
    "model": "served-model",
    "choices": [{"message": {"role": "assistant", "content": "PONG"}}],
    "usage": {"prompt_tokens": 11, "completion_tokens": 7},
}


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    monkeypatch.setattr(llm_subprocess, "emit_resolver_resolved", lambda *a, **kw: None)
    for var in list(__import__("os").environ):
        if var.startswith("HAL_OPENAI_"):
            monkeypatch.delenv(var, raising=False)
    for var in ("HAL_RUNNER_BACKEND_JUDGE", "HAL_RUNNER_BACKEND_WORKER"):
        monkeypatch.delenv(var, raising=False)
    # A local server must never be routed through a developer's proxy.
    for var in ("http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")
    telemetry_ctx.clear_current_run()
    yield
    telemetry_ctx.clear_current_run()
    reset_backends()


def _module():
    """Deferred import: the module does not exist before the change."""
    return importlib.import_module("bytedigger_engine.lib.reference_backends.openai_compat")


def _register() -> bool:
    return _module().register()


class _Server:
    def __init__(self, httpd, thread, requests, release):
        self.httpd, self.thread, self.requests, self.release = httpd, thread, requests, release
        self.base = f"http://127.0.0.1:{httpd.server_address[1]}/v1"


@contextmanager
def _serve(*, status=200, body=_OK_BODY, raw=None, block=False, headers=None, reset=None, framing="length"):
    """One local OpenAI-style server. `raw` (bytes) overrides the JSON body;
    `block=True` makes every request wait on an event released at teardown;
    `headers` adds response headers; `reset` is "before" (drop the connection
    with a TCP reset before any response) or "mid" (reset after a partial body);
    `framing` is "length" (Content-Length), "close" (no length, body ends at close)
    or "chunked". Requests of every HTTP method are recorded, under "method"."""
    requests: list[dict] = []
    release = threading.Event()

    class _Handler(BaseHTTPRequestHandler):
        def log_message(self, *args, **kwargs):  # silence
            pass

        def _hard_close(self):
            self.connection.setsockopt(
                socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0)
            )
            self.close_connection = True
            self.connection.close()

        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length") or 0)
            data = self.rfile.read(length) if length else b""
            requests.append({
                "method": "POST",
                "path": self.path,
                "headers": {k.lower(): v for k, v in self.headers.items()},
                "body": data,
            })
            if block:
                release.wait(15)
            try:
                if reset == "before":
                    self._hard_close()
                    return
                payload = raw if raw is not None else json.dumps(body).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                if framing == "length":
                    self.send_header("Content-Length", str(len(payload)))
                elif framing == "chunked":
                    self.send_header("Transfer-Encoding", "chunked")
                self.send_header("Connection", "close")
                for name, value in (headers or {}).items():
                    self.send_header(name, value)
                self.end_headers()
                self.close_connection = True
                if reset == "mid":
                    self.wfile.write(payload[: len(payload) // 2])
                    self.wfile.flush()
                    self._hard_close()
                    return
                if framing == "chunked":
                    step = 1024 * 1024
                    for start in range(0, len(payload), step):
                        piece = payload[start:start + step]
                        self.wfile.write(f"{len(piece):x}\r\n".encode("ascii") + piece + b"\r\n")
                    self.wfile.write(b"0\r\n\r\n")
                else:
                    self.wfile.write(payload)
            except OSError:
                pass

        def _other_method(self):
            requests.append({
                "method": self.command,
                "path": self.path,
                "headers": {k.lower(): v for k, v in self.headers.items()},
                "body": b"",
            })
            try:
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")
            except OSError:
                pass

        do_GET = do_HEAD = do_PUT = do_DELETE = do_PATCH = do_OPTIONS = _other_method

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    httpd.daemon_threads = True
    httpd.handle_error = lambda *args, **kwargs: None  # a client that hung up is expected
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield _Server(httpd, thread, requests, release)
    finally:
        release.set()
        httpd.shutdown()
        httpd.server_close()
        thread.join(timeout=5)


def _invoke(model="gemma4:26b", prompt="ping", **kw):
    kw.setdefault("timeout_sec", 5)
    kw.setdefault("idle_timeout_sec", 0)
    return invoke_llm_subprocess(
        prompt=prompt, model=model, step_name="bd83", backend=BACKEND, **kw
    )


def _log(tmp_path):
    log = EventLog(tmp_path / "events.jsonl")
    telemetry_ctx.set_current_run(event_log=log, run_id="run-bd83", step_name="s", phase="phase_6", cycle=1)
    return log


def _events(log, event_type="llm_cost_observed"):
    return [e["payload"] for e in log.read_all() if e["event_type"] == event_type]


def _fake_backend(name, data, *, caps=()):
    def _impl(**kwargs):
        merged = {**data, **(kwargs.get("extra_data") or {})}
        return StepResult(status="ok", data=merged, duration_ms=1, step_name=kwargs["step_name"])

    register_backend(name, _impl, manifest_source="git_diff", capabilities=set(caps), overwrite=True)


# ---------------------------------------------------------------------------
# AC1 - opt-in: nothing registered without HAL_OPENAI_BASE_URL
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("value", [None, "", "   "])
def test_ac1_unset_or_blank_base_url_registers_nothing(monkeypatch, value):
    if value is not None:
        monkeypatch.setenv("HAL_OPENAI_BASE_URL", value)
    assert _register() is False
    assert BACKEND not in llm_subprocess._KNOWN_BACKENDS
    assert BACKEND not in _BACKEND_CAPABILITIES
    result = _invoke()
    assert result.status == "error"
    assert result.error_code == "E_LLM_BACKEND_UNKNOWN"


# ---------------------------------------------------------------------------
# AC2 - registration with the env set
# ---------------------------------------------------------------------------

def test_ac2_register_true_and_dispatch_through_chokepoint(monkeypatch):
    with _serve() as srv:
        monkeypatch.setenv("HAL_OPENAI_BASE_URL", srv.base)
        assert _register() is True
        assert _register() is True  # idempotent (overwrite=True)
        assert BACKEND in llm_subprocess._KNOWN_BACKENDS
        assert _BACKEND_MANIFEST_SOURCE[BACKEND] == "api_text_response"
        assert "no_tools" in _BACKEND_CAPABILITIES[BACKEND]
        result = _invoke()
    assert result.status == "ok", f"{result.error_code}: {result.error}"
    assert result.data["raw_response"] == "PONG"


# ---------------------------------------------------------------------------
# AC3 - request shape
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("suffix", ["", "/"])
def test_ac3_request_goes_to_chat_completions_with_expected_body(monkeypatch, suffix):
    with _serve() as srv:
        monkeypatch.setenv("HAL_OPENAI_BASE_URL", srv.base + suffix)
        assert _register() is True
        result = _invoke(model="gemma4:26b", prompt="ping-ac3")
        reqs = list(srv.requests)
    assert result.status == "ok", f"{result.error_code}: {result.error}"
    assert len(reqs) == 1
    assert reqs[0]["path"] == "/v1/chat/completions"
    body = json.loads(reqs[0]["body"].decode("utf-8"))
    assert body["model"] == "gemma4:26b"
    assert body["messages"] == [{"role": "user", "content": "ping-ac3"}]
    assert body["stream"] is False
    assert result.data["command"] == [BACKEND, "chat.completions", "gemma4:26b"]


# ---------------------------------------------------------------------------
# AC4 - keyless vs keyed (metered)
# ---------------------------------------------------------------------------

def test_ac4_keyless_has_no_auth_no_billing_mode_no_metered_capability(monkeypatch):
    with _serve() as srv:
        monkeypatch.setenv("HAL_OPENAI_BASE_URL", srv.base)
        assert _register() is True
        caps = _BACKEND_CAPABILITIES[BACKEND]
        result = _invoke()
        headers = srv.requests[0]["headers"]
    assert "billing:metered" not in caps
    assert "authorization" not in headers
    assert result.status == "ok"
    assert "billing_mode" not in result.data


@pytest.mark.parametrize("blank", ["", "   "])
def test_ac4_blank_key_counts_as_keyless(monkeypatch, blank):
    with _serve() as srv:
        monkeypatch.setenv("HAL_OPENAI_BASE_URL", srv.base)
        monkeypatch.setenv("HAL_OPENAI_API_KEY", blank)
        assert _register() is True
        assert "billing:metered" not in _BACKEND_CAPABILITIES[BACKEND]
        result = _invoke()
        headers = srv.requests[0]["headers"]
    assert "authorization" not in headers
    assert "billing_mode" not in result.data


def test_ac4_keyed_sends_bearer_and_is_metered(monkeypatch):
    with _serve() as srv:
        monkeypatch.setenv("HAL_OPENAI_BASE_URL", srv.base)
        monkeypatch.setenv("HAL_OPENAI_API_KEY", KEY)
        assert _register() is True
        caps = _BACKEND_CAPABILITIES[BACKEND]
        result = _invoke()
        headers = srv.requests[0]["headers"]
    assert caps == frozenset({"no_tools", "billing:metered"})
    assert headers["authorization"] == f"Bearer {KEY}"
    assert result.status == "ok"
    assert result.data["billing_mode"] == "metered"


# ---------------------------------------------------------------------------
# AC5 - model alias override, verbatim passthrough otherwise
# ---------------------------------------------------------------------------

def test_ac5_alias_override_applies(monkeypatch):
    with _serve() as srv:
        monkeypatch.setenv("HAL_OPENAI_BASE_URL", srv.base)
        monkeypatch.setenv("HAL_OPENAI_MODEL_SONNET", "local-big-model")
        assert _register() is True
        result = _invoke(model="Sonnet")  # lowercased then uppercased -> SONNET
        body = json.loads(srv.requests[0]["body"].decode("utf-8"))
    assert result.status == "ok"
    assert body["model"] == "local-big-model"
    assert result.data["command"][-1] == "local-big-model"


def test_ac5_unaliased_model_goes_through_verbatim(monkeypatch):
    with _serve() as srv:
        monkeypatch.setenv("HAL_OPENAI_BASE_URL", srv.base)
        monkeypatch.setenv("HAL_OPENAI_MODEL_SONNET", "local-big-model")  # unrelated alias
        assert _register() is True
        result = _invoke(model="gemma4:26b")
        body = json.loads(srv.requests[0]["body"].decode("utf-8"))
    assert result.status == "ok"
    assert body["model"] == "gemma4:26b"


# ---------------------------------------------------------------------------
# AC6 - success data and usage mapping
# ---------------------------------------------------------------------------

def test_ac6_success_data_fields(monkeypatch):
    with _serve() as srv:
        monkeypatch.setenv("HAL_OPENAI_BASE_URL", srv.base)
        assert _register() is True
        result = _invoke()
    data = result.data
    assert data["raw_response"] == "PONG"
    assert data["response_bytes"] == len("PONG".encode("utf-8"))
    assert data["command"] == [BACKEND, "chat.completions", "gemma4:26b"]
    assert isinstance(data["wall_ms"], int) and not isinstance(data["wall_ms"], bool)
    assert data["wall_ms"] >= 0
    assert data["usage"] == {"input_tokens": 11, "output_tokens": 7}
    assert data["observed_model"] == "served-model"
    assert result.duration_ms >= 0


@pytest.mark.parametrize(
    "usage_value, expected",
    [
        ("absent", None),
        ({"prompt_tokens": True, "completion_tokens": -1}, None),
        ({"prompt_tokens": "5", "completion_tokens": 5.0}, None),
        ("not-a-dict", None),
        ({"prompt_tokens": 5}, {"input_tokens": 5}),
        ({"prompt_tokens": 0, "completion_tokens": 9}, {"input_tokens": 0, "output_tokens": 9}),
        ({"prompt_tokens": 5, "completion_tokens": None}, {"input_tokens": 5}),
    ],
)
def test_ac6_usage_copied_only_for_valid_counts(monkeypatch, usage_value, expected):
    body = {k: v for k, v in _OK_BODY.items() if k != "usage"}
    if usage_value != "absent":
        body["usage"] = usage_value
    with _serve(body=body) as srv:
        monkeypatch.setenv("HAL_OPENAI_BASE_URL", srv.base)
        assert _register() is True
        result = _invoke()
    assert result.status == "ok", f"{result.error_code}: {result.error}"
    if expected is None:
        assert "usage" not in result.data
    else:
        assert result.data["usage"] == expected


def test_ac6_blank_or_non_string_served_model_is_not_observed(monkeypatch):
    with _serve(body={**_OK_BODY, "model": "  "}) as srv:
        monkeypatch.setenv("HAL_OPENAI_BASE_URL", srv.base)
        assert _register() is True
        result = _invoke()
    assert result.status == "ok"
    assert "observed_model" not in result.data


def test_ac6_extra_data_cannot_shadow_reserved_keys(monkeypatch):
    forged = {
        "raw_response": "FORGED",
        "response_bytes": 0,
        "command": ["forged"],
        "wall_ms": -5,
        "usage": {"input_tokens": 999, "output_tokens": 999},
        "observed_model": "forged-model",
        "billing_mode": "subscription",
        "caller_key": "kept",
    }
    with _serve() as srv:
        monkeypatch.setenv("HAL_OPENAI_BASE_URL", srv.base)
        monkeypatch.setenv("HAL_OPENAI_API_KEY", KEY)
        assert _register() is True
        result = _invoke(extra_data=forged)
    data = result.data
    assert data["caller_key"] == "kept"
    assert data["raw_response"] == "PONG"
    assert data["response_bytes"] == 4
    assert data["command"] == [BACKEND, "chat.completions", "gemma4:26b"]
    assert data["wall_ms"] >= 0
    assert data["usage"] == {"input_tokens": 11, "output_tokens": 7}
    assert data["observed_model"] == "served-model"
    assert data["billing_mode"] == "metered"


# ---------------------------------------------------------------------------
# AC7 - llm_cost_observed carries tokens and wall_ms
# ---------------------------------------------------------------------------

def test_ac7_cost_event_for_openai_compat_has_tokens_and_wall_ms(monkeypatch, tmp_path):
    log = _log(tmp_path)
    with _serve() as srv:
        monkeypatch.setenv("HAL_OPENAI_BASE_URL", srv.base)
        assert _register() is True
        result = _invoke()
    (payload,) = _events(log)
    assert payload["backend"] == BACKEND
    assert payload["tokens_in"] == 11
    assert payload["tokens_out"] == 7
    assert payload["wall_ms"] == result.data["wall_ms"]


def test_ac7_wall_ms_is_forwarded_from_any_backend_that_sets_it(monkeypatch, tmp_path):
    log = _log(tmp_path)
    _fake_backend("walled", {"raw_response": "x", "wall_ms": 123, "usage": {"input_tokens": 1, "output_tokens": 2}})
    invoke_llm_subprocess(prompt="p", model="m", timeout_sec=5, step_name="bd83", backend="walled", idle_timeout_sec=0)
    (payload,) = _events(log)
    assert payload["wall_ms"] == 123


def test_ac7_wall_ms_zero_is_valid(monkeypatch, tmp_path):
    log = _log(tmp_path)
    _fake_backend("walled", {"raw_response": "x", "wall_ms": 0})
    invoke_llm_subprocess(prompt="p", model="m", timeout_sec=5, step_name="bd83", backend="walled", idle_timeout_sec=0)
    (payload,) = _events(log)
    assert payload["wall_ms"] == 0


# ---------------------------------------------------------------------------
# AC8 - URL validation before any I/O
# ---------------------------------------------------------------------------

@pytest.fixture
def _no_sockets(monkeypatch):
    """Count every attempt to open a connection or resolve a name."""
    import urllib.request

    attempts: list[str] = []

    def _trap(name):
        def _fn(*args, **kwargs):
            attempts.append(name)
            raise AssertionError(f"network I/O attempted via {name}")
        return _fn

    monkeypatch.setattr(socket.socket, "connect", _trap("socket.connect"))
    monkeypatch.setattr(socket, "create_connection", _trap("create_connection"))
    monkeypatch.setattr(socket, "getaddrinfo", _trap("getaddrinfo"))
    monkeypatch.setattr(urllib.request, "urlopen", _trap("urlopen"))
    return attempts


@pytest.mark.parametrize("bad", ["file:///x", "ftp://h", "http://", "https://", "localhost:11434", "/v1"])
def test_ac8_invalid_base_url_is_rejected_before_any_io(monkeypatch, _no_sockets, bad):
    monkeypatch.setenv("HAL_OPENAI_BASE_URL", bad)
    assert _register() is True
    result = _invoke()
    assert result.status == "error"
    assert result.error_code == "E_LLM_BASE_URL_INVALID"
    assert result.recoverable is False
    assert _no_sockets == []


@pytest.mark.parametrize("mode", ["unset", "blank"])
def test_ac8_base_url_missing_at_call_time(monkeypatch, _no_sockets, mode):
    monkeypatch.setenv("HAL_OPENAI_BASE_URL", "http://127.0.0.1:9/v1")
    assert _register() is True
    if mode == "unset":
        monkeypatch.delenv("HAL_OPENAI_BASE_URL")
    else:
        monkeypatch.setenv("HAL_OPENAI_BASE_URL", "   ")
    result = _invoke()
    assert result.status == "error"
    assert result.error_code == "E_LLM_BASE_URL_MISSING"
    assert result.recoverable is False
    assert _no_sockets == []


def test_ac8_base_url_is_read_at_call_time_not_at_registration(monkeypatch):
    with _serve() as first, _serve() as second:
        monkeypatch.setenv("HAL_OPENAI_BASE_URL", first.base)
        assert _register() is True
        monkeypatch.setenv("HAL_OPENAI_BASE_URL", second.base)
        result = _invoke()
        assert result.status == "ok"
        assert first.requests == []
        assert len(second.requests) == 1


# ---------------------------------------------------------------------------
# AC9 - degrade, never raise
# ---------------------------------------------------------------------------

def test_ac9_http_500_is_recoverable_api_http_error(monkeypatch):
    with _serve(status=500, body={"error": "boom"}) as srv:
        monkeypatch.setenv("HAL_OPENAI_BASE_URL", srv.base)
        assert _register() is True
        result = _invoke()
    assert result.status == "error"
    assert result.error_code == "E_LLM_API_HTTP"
    assert result.recoverable is True
    assert "500" in str(result.error)
    assert result.duration_ms is not None and result.duration_ms >= 0


def test_ac9_connection_refused_is_recoverable_network_error(monkeypatch):
    # Pre-staged: a port that was bound, read and closed before connecting, so
    # nothing listens on it and a connect is refused (workflows.md section 1i).
    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    holder.bind(("127.0.0.1", 0))
    port = holder.getsockname()[1]
    holder.close()
    monkeypatch.setenv("HAL_OPENAI_BASE_URL", f"http://127.0.0.1:{port}/v1")
    assert _register() is True
    result = _invoke()
    assert result.status == "error"
    assert result.error_code == "E_LLM_API_NETWORK"
    assert result.recoverable is True
    assert result.duration_ms is not None and result.duration_ms >= 0


def test_ac9_server_slower_than_timeout_is_recoverable_timeout(monkeypatch):
    # Pre-staged: the server blocks on an event that is only released at teardown,
    # so the response can never arrive before the client's deadline.
    with _serve(block=True) as srv:
        monkeypatch.setenv("HAL_OPENAI_BASE_URL", srv.base)
        assert _register() is True
        result = _invoke(timeout_sec=1)
    assert result.status == "error"
    assert result.error_code == "E_LLM_TIMEOUT"
    assert result.recoverable is True
    assert result.duration_ms is not None and result.duration_ms >= 0


def test_ac9_explicit_idle_timeout_fails_loud_without_progress_since(monkeypatch):
    with _serve(block=True) as srv:
        monkeypatch.setenv("HAL_OPENAI_BASE_URL", srv.base)
        assert _register() is True
        assert "progress_since" not in _BACKEND_CAPABILITIES[BACKEND]
        result = _invoke(timeout_sec=30, idle_timeout_sec=1)
    assert result.status == "error"
    assert result.error_code == "E_LLM_WATCHDOG_UNSUPPORTED"
    assert result.recoverable is False


@pytest.mark.parametrize(
    "raw",
    [
        b"not json",
        b"{}",
        b'{"choices":[]}',
        b'{"choices":[{"message":{"content":""}}]}',
        b'{"choices":[{"message":{"content":null}}]}',
        b'{"choices":[{"message":{"content":42}}]}',
        b'{"choices":[{}]}',
        b'{"choices":"x"}',
        b"[1,2,3]",
    ],
)
def test_ac9_malformed_200_is_not_recoverable_bad_response(monkeypatch, raw):
    with _serve(raw=raw) as srv:
        monkeypatch.setenv("HAL_OPENAI_BASE_URL", srv.base)
        assert _register() is True
        result = _invoke()
    assert result.status == "error"
    assert result.error_code == "E_LLM_API_BAD_RESPONSE"
    assert result.recoverable is False
    assert result.duration_ms is not None and result.duration_ms >= 0


# ---------------------------------------------------------------------------
# No API key leakage (errors, logs, data)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("scenario", ["http_error_echoing_key", "bad_json_echoing_key", "ok"])
def test_ac18_api_key_never_appears_in_result_or_logs(monkeypatch, caplog, tmp_path, scenario):
    log = _log(tmp_path)
    echo = f"rejected credential {KEY}".encode("utf-8")
    kwargs = {
        "http_error_echoing_key": {"status": 401, "raw": echo},
        "bad_json_echoing_key": {"status": 200, "raw": echo},
        "ok": {},
    }[scenario]
    caplog.set_level("DEBUG")
    with _serve(**kwargs) as srv:
        monkeypatch.setenv("HAL_OPENAI_BASE_URL", srv.base)
        monkeypatch.setenv("HAL_OPENAI_API_KEY", KEY)
        assert _register() is True
        result = _invoke()
    assert KEY not in str(result.error)
    assert KEY not in json.dumps(result.data, default=str)
    assert KEY not in caplog.text
    assert KEY not in json.dumps(log.read_all(), default=str)
    if scenario != "ok":
        assert result.status == "error"


# ---------------------------------------------------------------------------
# AC10 - _judge_can_write
# ---------------------------------------------------------------------------

def _wf_ctx(tmp_path: Path, **extra) -> WorkflowContext:
    scratch = tmp_path / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    worktree = tmp_path / "fake_worktree"
    worktree.mkdir(parents=True, exist_ok=True)
    org = {
        "scratchpad_dir": str(scratch),
        "current_worktree_path": str(worktree),
        "complexity": "FEATURE",
    }
    org.update(extra)
    return WorkflowContext(
        tenant_id="t", scope=None, db_path=None, org_config=org,
        question="add feature X", session_id="test-bd83", persona="p",
        framework=None, domain=None,
    )


def _text_only_backend(name="text-only", caps=("no_tools",)):
    def _impl(**kwargs):
        return StepResult(status="ok", data={"raw_response": "x"}, duration_ms=1, step_name=kwargs["step_name"])

    register_backend(name, _impl, manifest_source="api_text_response", capabilities=set(caps), overwrite=True)
    return name


def test_ac10_no_tools_judge_cannot_write(monkeypatch, tmp_path):
    monkeypatch.setenv("HAL_RUNNER_BACKEND_JUDGE", _text_only_backend())
    assert phase_6_review._judge_can_write(_wf_ctx(tmp_path)) is False


def test_ac10_default_subprocess_judge_can_write(monkeypatch, tmp_path):
    monkeypatch.setenv("HAL_RUNNER_BACKEND_JUDGE", "claude-subprocess")
    assert phase_6_review._judge_can_write(_wf_ctx(tmp_path)) is True


def test_ac10_default_resolution_can_write(monkeypatch, tmp_path):
    # conftest pins HAL_RUNNER_BACKEND=claude-subprocess; no judge override.
    assert phase_6_review._judge_can_write(_wf_ctx(tmp_path)) is True


def test_ac10_no_tools_with_tool_allowlist_can_write(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "HAL_RUNNER_BACKEND_JUDGE", _text_only_backend("tools-too", ("no_tools", "tool_allowlist"))
    )
    assert phase_6_review._judge_can_write(_wf_ctx(tmp_path)) is True


def test_ac10_unknown_backend_can_write(monkeypatch, tmp_path):
    monkeypatch.setenv("HAL_RUNNER_BACKEND_JUDGE", "no-such-backend-bd83")
    assert phase_6_review._judge_can_write(_wf_ctx(tmp_path)) is True


def test_ac10_backend_without_no_tools_can_write(monkeypatch, tmp_path):
    monkeypatch.setenv("HAL_RUNNER_BACKEND_JUDGE", _text_only_backend("plain", ()))
    assert phase_6_review._judge_can_write(_wf_ctx(tmp_path)) is True


def test_ac10_real_openai_compat_registration_is_a_no_write_judge(monkeypatch, tmp_path):
    monkeypatch.setenv("HAL_OPENAI_BASE_URL", "http://127.0.0.1:9/v1")
    assert _register() is True
    monkeypatch.setenv("HAL_RUNNER_BACKEND_JUDGE", BACKEND)
    assert phase_6_review._judge_can_write(_wf_ctx(tmp_path)) is False


# ---------------------------------------------------------------------------
# AC11 - prompt swap
# ---------------------------------------------------------------------------

_WRITE_MARKERS = (
    "Use the Write tool",
    "write your findings",
    "write the aggregated review into",
    "Use the Write tool to write your findings to ONE file",
)

_NO_WRITE_SENTENCE = "Reply with the complete aggregated review as your final answer; do not write files."


def _prompt(ctx) -> str:
    result = phase_6_review._build_review_prompt(ctx, None)
    assert result.status == "ok", f"{result.error_code}: {result.error}"
    return result.data["prompt"]


def test_ac11_no_tools_judge_prompt_has_no_write_instructions(monkeypatch, tmp_path):
    monkeypatch.setenv("HAL_RUNNER_BACKEND_JUDGE", _text_only_backend())
    prompt = _prompt(_wf_ctx(tmp_path))
    for marker in _WRITE_MARKERS:
        assert marker not in prompt, f"no-tools prompt still says {marker!r}"
    assert _NO_WRITE_SENTENCE in prompt
    remainder = prompt.replace(_NO_WRITE_SENTENCE, "")
    assert "VERDICT:" in remainder
    assert "## Aggregated Findings" in remainder


def test_ac11_default_judge_prompt_is_byte_identical_to_the_pre_change_prompt(monkeypatch, tmp_path):
    ctx = _wf_ctx(tmp_path)
    with monkeypatch.context() as forced:
        forced.setattr(phase_6_review, "_judge_can_write", lambda _ctx: True)
        golden = _prompt(ctx)
    actual = _prompt(ctx)  # default backend, no override
    assert actual == golden
    for marker in _WRITE_MARKERS:
        assert marker in golden


# ---------------------------------------------------------------------------
# AC12 - end to end: a no-tools judge's reply becomes the review document
# ---------------------------------------------------------------------------

_REVIEW_TEXT = (
    "## Aggregated Findings\n\n"
    "## Findings (structured)\n"
    "```json\n"
    '[{"id": "F1", "severity": "HIGH", "path": "src/main.py:42", "description": "serious problem bd83"}]\n'
    "```\n\n"
    "VERDICT: FAIL\n"
)


def test_ac12_no_tools_judge_reply_is_written_as_the_review(monkeypatch, tmp_path):
    ctx = _wf_ctx(tmp_path, review_model="gemma4:26b")
    with _serve(body={"choices": [{"message": {"content": _REVIEW_TEXT}}],
                      "usage": {"prompt_tokens": 5, "completion_tokens": 6}}) as srv:
        monkeypatch.setenv("HAL_OPENAI_BASE_URL", srv.base)
        assert _register() is True
        monkeypatch.setenv("HAL_RUNNER_BACKEND_JUDGE", BACKEND)
        built = phase_6_review._build_review_prompt(ctx, None)
        assert built.status == "ok", f"{built.error_code}: {built.error}"
        invoked = phase_6_review._invoke_review_llm(ctx, built)
        assert invoked.status == "ok", f"{invoked.error_code}: {invoked.error}"
        aggregated = phase_6_review._aggregate_review_findings(ctx, invoked)
        assert aggregated.status == "ok", f"{aggregated.error_code}: {aggregated.error}"
        written = phase_6_review._write_review_artifact(ctx, aggregated)
        sent = json.loads(srv.requests[0]["body"].decode("utf-8"))["messages"][0]["content"]
    assert "Use the Write tool" not in sent
    assert written.status == "ok", f"{written.error_code}: {written.error}"
    assert written.data["verdict"] == phase_6_review.VERDICT_FAIL
    doc = Path(written.data["review_doc_path"]).read_text(encoding="utf-8")
    assert "serious problem bd83" in doc


# ---------------------------------------------------------------------------
# AC13 - default path untouched; kill switch
# ---------------------------------------------------------------------------

def _reload_run():
    mod = sys.modules.get("bytedigger_engine.run")
    if mod is None:
        return importlib.import_module("bytedigger_engine.run")
    return importlib.reload(mod)


def test_ac13_env_unset_leaves_the_registry_without_the_new_backend(monkeypatch):
    reset_backends()
    baseline = set(llm_subprocess._KNOWN_BACKENDS)
    _module()  # importing the module must not register anything by itself
    assert set(llm_subprocess._KNOWN_BACKENDS) == baseline
    _reload_run()
    assert BACKEND not in llm_subprocess._KNOWN_BACKENDS
    assert BACKEND not in _BACKEND_CAPABILITIES
    assert BACKEND not in _BACKEND_MANIFEST_SOURCE
    assert {"claude-subprocess", "claude-in-session"} <= set(llm_subprocess._KNOWN_BACKENDS)


def test_ac13_run_import_registers_the_backend_when_opted_in(monkeypatch):
    monkeypatch.setenv("HAL_OPENAI_BASE_URL", "http://127.0.0.1:9/v1")
    reset_backends()
    _reload_run()
    assert BACKEND in llm_subprocess._KNOWN_BACKENDS


def test_ac13_reset_backends_removes_the_new_backend(monkeypatch):
    monkeypatch.setenv("HAL_OPENAI_BASE_URL", "http://127.0.0.1:9/v1")
    assert _register() is True
    assert BACKEND in llm_subprocess._KNOWN_BACKENDS
    reset_backends()
    assert BACKEND not in llm_subprocess._KNOWN_BACKENDS
    assert BACKEND not in _BACKEND_CAPABILITIES
    assert BACKEND not in _BACKEND_MANIFEST_SOURCE


def test_ac13_registering_does_not_disturb_the_default_backends(monkeypatch):
    reset_backends()
    before = {k: _BACKEND_CAPABILITIES[k] for k in ("claude-subprocess", "claude-in-session")}
    monkeypatch.setenv("HAL_OPENAI_BASE_URL", "http://127.0.0.1:9/v1")
    assert _register() is True
    after = {k: _BACKEND_CAPABILITIES[k] for k in before}
    assert before == after


# ---------------------------------------------------------------------------
# AC14 (config half) - marker declared, deselected by default
# ---------------------------------------------------------------------------

def test_ac14_live_ollama_marker_is_declared_and_deselected_by_default():
    tomllib = pytest.importorskip("tomllib")  # Python 3.11+
    data = tomllib.loads((ENGINE_PY / "pyproject.toml").read_text(encoding="utf-8"))
    opts = data.get("tool", {}).get("pytest", {}).get("ini_options", {})
    markers = opts.get("markers", [])
    assert any(str(m).split(":", 1)[0].strip() == "live_ollama" for m in markers), markers
    addopts = opts.get("addopts", "")
    addopts = " ".join(addopts) if isinstance(addopts, list) else str(addopts)
    assert "not live_ollama" in addopts, addopts


# ---------------------------------------------------------------------------
# AC15 - both billing modes through the same chokepoint
# ---------------------------------------------------------------------------

def test_ac15_subscription_and_metered_calls_are_recorded_side_by_side(monkeypatch, tmp_path):
    log = _log(tmp_path)
    _fake_backend(
        "claude-subprocess",
        {"raw_response": "x", "billing_mode": "subscription", "usage": {"input_tokens": 1, "output_tokens": 1}},
    )
    with _serve() as srv:
        monkeypatch.setenv("HAL_OPENAI_BASE_URL", srv.base)
        monkeypatch.setenv("HAL_OPENAI_API_KEY", KEY)
        assert _register() is True
        sub = invoke_llm_subprocess(
            prompt="p", model="sonnet", timeout_sec=5, step_name="bd83",
            backend="claude-subprocess", idle_timeout_sec=0,
        )
        met = _invoke()
    assert sub.status == "ok" and met.status == "ok"
    observed = _events(log)
    assert [(p["backend"], p["billing_mode"]) for p in observed] == [
        ("claude-subprocess", "subscription"),
        (BACKEND, "metered"),
    ]
    assert "wall_ms" not in observed[0]
    assert observed[1]["wall_ms"] == met.data["wall_ms"]


# ---------------------------------------------------------------------------
# AC16 - effective_model hook
# ---------------------------------------------------------------------------

def test_ac16_effective_model_hook_returns_the_remapped_model(monkeypatch):
    monkeypatch.setenv("HAL_OPENAI_BASE_URL", "http://127.0.0.1:9/v1")
    monkeypatch.setenv("HAL_OPENAI_MODEL_SONNET", "local-big-model")
    assert _register() is True
    hook = llm_subprocess._BACKEND_EFFECTIVE_MODEL[BACKEND]
    assert hook("sonnet") == "local-big-model"
    assert hook("Sonnet") == "local-big-model"
    assert hook("gemma4:26b") == "gemma4:26b"


def test_ac16_hook_reads_the_env_at_call_time(monkeypatch):
    monkeypatch.setenv("HAL_OPENAI_BASE_URL", "http://127.0.0.1:9/v1")
    assert _register() is True
    hook = llm_subprocess._BACKEND_EFFECTIVE_MODEL[BACKEND]
    assert hook("sonnet") == "sonnet"
    monkeypatch.setenv("HAL_OPENAI_MODEL_SONNET", "set-after-register")
    assert hook("sonnet") == "set-after-register"
    monkeypatch.delenv("HAL_OPENAI_MODEL_SONNET")
    assert hook("sonnet") == "sonnet"


def test_ac16_without_override_the_hook_returns_the_requested_model(monkeypatch):
    monkeypatch.setenv("HAL_OPENAI_BASE_URL", "http://127.0.0.1:9/v1")
    assert _register() is True
    hook = llm_subprocess._BACKEND_EFFECTIVE_MODEL[BACKEND]
    assert hook("sonnet") == "sonnet"
    assert hook("gemma4:26b") == "gemma4:26b"


def test_ac16_chokepoint_records_the_remap_and_the_request_carries_it(monkeypatch, tmp_path):
    log = _log(tmp_path)
    with _serve() as srv:
        monkeypatch.setenv("HAL_OPENAI_BASE_URL", srv.base)
        monkeypatch.setenv("HAL_OPENAI_MODEL_SONNET", "local-big-model")
        assert _register() is True
        result = _invoke(model="sonnet")
        body = json.loads(srv.requests[0]["body"].decode("utf-8"))
    assert result.status == "ok", f"{result.error_code}: {result.error}"
    assert body["model"] == "local-big-model"
    (remap,) = _events(log, "effective_model_remapped")
    assert remap["backend"] == BACKEND
    assert remap["model"] == "sonnet"
    assert remap["effective_model"] == "local-big-model"


# ---------------------------------------------------------------------------
# AC17 - no redirects, recoverability by status
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status", [301, 302, 307])
def test_ac17_redirect_is_not_followed_and_no_key_reaches_the_second_host(monkeypatch, status):
    with _serve() as second, _serve(
        status=status, raw=b"", headers={"Location": second.base + "/chat/completions"}
    ) as first:
        monkeypatch.setenv("HAL_OPENAI_BASE_URL", first.base)
        monkeypatch.setenv("HAL_OPENAI_API_KEY", KEY)
        assert _register() is True
        result = _invoke()
        seen_by_first = len(first.requests)
        seen_by_second = list(second.requests)
    assert seen_by_first == 1
    assert seen_by_second == [], [(r["method"], r["path"]) for r in seen_by_second]
    assert result.status == "error"
    assert result.error_code == "E_LLM_API_HTTP"
    assert result.recoverable is False
    assert KEY not in str(result.error)


@pytest.mark.parametrize(
    "status, recoverable",
    [(401, False), (403, False), (404, False), (408, True), (429, True), (500, True), (503, True)],
)
def test_ac17_recoverability_follows_the_http_status(monkeypatch, status, recoverable):
    with _serve(status=status, body={"error": "x"}) as srv:
        monkeypatch.setenv("HAL_OPENAI_BASE_URL", srv.base)
        assert _register() is True
        result = _invoke()
    assert result.status == "error"
    assert result.error_code == "E_LLM_API_HTTP"
    assert result.recoverable is recoverable
    assert str(status) in str(result.error)


# ---------------------------------------------------------------------------
# AC18 - transport failures map to codes, oversized bodies are refused
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("reset", ["before", "mid"])
def test_ac18_connection_reset_is_a_recoverable_network_error(monkeypatch, reset):
    with _serve(reset=reset, raw=b'{"choices":[{"message":{"content":"' + b"a" * 4096 + b'"}}]}') as srv:
        monkeypatch.setenv("HAL_OPENAI_BASE_URL", srv.base)
        assert _register() is True
        result = _invoke()
    assert result.status == "error"
    assert result.error_code == "E_LLM_API_NETWORK"
    assert result.recoverable is True
    assert result.duration_ms is not None and result.duration_ms >= 0


@pytest.mark.parametrize(
    "exc, code, recoverable",
    [
        (lambda: urllib.error.URLError(socket.timeout("timed out")), "E_LLM_TIMEOUT", True),
        (lambda: urllib.error.URLError(TimeoutError("timed out")), "E_LLM_TIMEOUT", True),
        (lambda: socket.timeout("timed out"), "E_LLM_TIMEOUT", True),
        (lambda: urllib.error.URLError("dns failure"), "E_LLM_API_NETWORK", True),
        (lambda: ConnectionResetError("reset"), "E_LLM_API_NETWORK", True),
        (lambda: http.client.RemoteDisconnected("closed"), "E_LLM_API_NETWORK", True),
        (lambda: http.client.IncompleteRead(b"ab", 10), "E_LLM_API_NETWORK", True),
        (lambda: OSError("other"), "E_LLM_API_NETWORK", True),
        (lambda: RuntimeError("surprise"), "E_LLM_API_BAD_RESPONSE", False),
    ],
)
def test_ac18_transport_exceptions_map_to_codes_and_never_raise(monkeypatch, exc, code, recoverable):
    # Patched at the opener seam (OpenerDirector.open serves both urlopen and a
    # build_opener() opener), so no socket is needed for these cases.
    def _raise(self, *args, **kwargs):
        raise exc()

    monkeypatch.setattr(urllib.request.OpenerDirector, "open", _raise)
    monkeypatch.setenv("HAL_OPENAI_BASE_URL", "http://127.0.0.1:9/v1")
    assert _register() is True
    result = _invoke()
    assert result.status == "error"
    assert result.error_code == code
    assert result.recoverable is recoverable
    assert result.duration_ms is not None and result.duration_ms >= 0


def test_ac18_body_over_16_mib_is_a_bad_response(monkeypatch):
    # A syntactically valid, otherwise acceptable reply: only its size is wrong.
    big = b'{"choices":[{"message":{"content":"' + b"a" * (17 * 1024 * 1024) + b'"}}]}'
    with _serve(raw=big) as srv:
        monkeypatch.setenv("HAL_OPENAI_BASE_URL", srv.base)
        assert _register() is True
        result = _invoke(timeout_sec=30)
    assert result.status == "error"
    assert result.error_code == "E_LLM_API_BAD_RESPONSE"
    assert result.recoverable is False


_CAP = 16 * 1024 * 1024


def _json_body_of_size(total: int) -> bytes:
    """A valid, otherwise acceptable reply whose whole body is exactly `total` bytes."""
    head, tail = b'{"choices":[{"message":{"content":"', b'"}}]}'
    return head + b"a" * (total - len(head) - len(tail)) + tail


@pytest.mark.parametrize("framing", ["length", "close", "chunked"])
@pytest.mark.parametrize("delta, accepted", [(-1024, True), (1024, False)])
def test_ac18_cap_is_on_bytes_actually_read_at_the_16_mib_boundary(monkeypatch, framing, delta, accepted):
    body = _json_body_of_size(_CAP + delta)
    with _serve(raw=body, framing=framing) as srv:
        monkeypatch.setenv("HAL_OPENAI_BASE_URL", srv.base)
        assert _register() is True
        result = _invoke(timeout_sec=60)
    if accepted:
        assert result.status == "ok", f"{result.error_code}: {result.error}"
        assert result.data["response_bytes"] == len(body) - len(b'{"choices":[{"message":{"content":""}}]}')
    else:
        assert result.status == "error"
        assert result.error_code == "E_LLM_API_BAD_RESPONSE"
        assert result.recoverable is False


def test_ac18_small_body_is_accepted(monkeypatch):
    ok = _json_body_of_size(1024 * 1024)
    with _serve(raw=ok) as srv:
        monkeypatch.setenv("HAL_OPENAI_BASE_URL", srv.base)
        assert _register() is True
        result = _invoke(timeout_sec=30)
    assert result.status == "ok", f"{result.error_code}: {result.error}"


# ---------------------------------------------------------------------------
# AC19 - error codes registered, observed_tools present
# ---------------------------------------------------------------------------

def test_ac19_new_error_codes_are_registered_and_documented():
    from bytedigger_engine import error_codes

    package_dir = Path(error_codes.__file__).resolve().parent
    for code in ("E_LLM_BASE_URL_INVALID", "E_LLM_BASE_URL_MISSING"):
        assert code in error_codes.ERROR_CODES, code
        for md in (ENGINE_PY / "ERROR_CODES.md", package_dir / "ERROR_CODES.md"):
            assert f"`{code}`" in md.read_text(encoding="utf-8"), (code, str(md))
    rendered = error_codes.render_markdown()
    assert (ENGINE_PY / "ERROR_CODES.md").read_text(encoding="utf-8") == rendered
    assert (package_dir / "ERROR_CODES.md").read_text(encoding="utf-8") == rendered
    unregistered, dead = error_codes.check(package_dir)
    assert not unregistered and not dead, (sorted(unregistered), sorted(dead))


def test_ac19_success_data_has_empty_observed_tools(monkeypatch):
    with _serve() as srv:
        monkeypatch.setenv("HAL_OPENAI_BASE_URL", srv.base)
        assert _register() is True
        result = _invoke()
    assert result.status == "ok"
    assert result.data["observed_tools"] == []


# ---------------------------------------------------------------------------
# Spec 3.1 item 10 - gate_label is only echoed into data
# ---------------------------------------------------------------------------

def test_gate_label_is_echoed_into_data(monkeypatch):
    with _serve() as srv:
        monkeypatch.setenv("HAL_OPENAI_BASE_URL", srv.base)
        assert _register() is True
        result = _invoke(gate_label="spec-gate")
        sent = json.loads(srv.requests[0]["body"].decode("utf-8"))
    assert result.status == "ok", f"{result.error_code}: {result.error}"
    assert result.data["gate_label"] == "spec-gate"
    assert "spec-gate" not in json.dumps(sent)
