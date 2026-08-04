"""RED tests — pydantic_anthropic reference backend + RunUsage tokens (#852).

Spec: SHARED/memory/Decisions/2026-07-15_GH852_pydantic_anthropic_backend_spec.md

13 tests, one per AC1-AC13. Modules `lib.reference_backends.pydantic_anthropic`
and `lib.reference_backends.anthropic_oauth` do NOT exist yet — RED phase.
All imports of those modules (and the not-yet-existing `_extract_usage_tokens`
symbol) are deferred to inside each test body — fails at
assert/import-in-body time (clean FAIL, not collection error, per
D1CF5FDF/§1q).

§1q check: NO spec_from_file_location / exec_module used; no sys.path
mutation at module level; no `from conftest import`.
§1i: OAuth-file / tmp-git-repo state is pre-staged deterministically inside
each test before invoking the UUT — no timing races. AC9's blocking-thread
path uses a bounded threading.Event with timeout_sec=0.5 (well under 2s).
§1l stub-passability: `pydantic_ai` and `anthropic` are faked via
sys.modules injection (external dependency seams, not the UUT itself); the
UUT symbols (pydantic_anthropic_backend, get_access_token,
_extract_usage_tokens, register) are exercised directly, unstubbed.

Pre-GREEN predict: ALL 13 FAIL — ImportError for AC1-AC10/AC13 (modules
absent); AttributeError for AC12 (_extract_usage_tokens absent); AC11 fails
because pydantic_openai_backend's success data lacks tokens_in/tokens_out.
"""
from __future__ import annotations

import json
import subprocess
import sys
import threading
import types

import pytest

from bytedigger_engine import llm_subprocess
from bytedigger_engine.llm_subprocess import reset_backends


# ---------------------------------------------------------------------------
# Deferred imports (non-collectable RED guard — D1CF5FDF / §1q)
# ---------------------------------------------------------------------------

def _import_anthropic_backend():
    from bytedigger_engine.lib.reference_backends import pydantic_anthropic as mod  # noqa: PLC0415
    return mod


def _import_oauth():
    from bytedigger_engine.lib.reference_backends import anthropic_oauth as mod  # noqa: PLC0415
    return mod


def _import_openai_backend():
    from bytedigger_engine.lib.reference_backends import pydantic_openai as mod  # noqa: PLC0415
    return mod


# ---------------------------------------------------------------------------
# Fake pydantic_ai / anthropic injection
# ---------------------------------------------------------------------------

class _FakeUsageLimits:
    def __init__(self, request_limit=None, **kwargs):
        self.request_limit = request_limit


class _FakeAgent:
    last_instance = None

    def __init__(self, model, tools=None, **kwargs):
        self.model = model
        self.tools = tools or []
        self._run_sync_result_text = "FAKE_ANTHROPIC_TEXT"
        _FakeAgent.last_instance = self

    def tool_plain(self, fn=None, **kwargs):
        def _wrap(f):
            self.tools.append(f)
            return f
        if fn is not None:
            return _wrap(fn)
        return _wrap

    def run_sync(self, prompt, usage_limits=None, **kwargs):
        usage_obj = types.SimpleNamespace(input_tokens=1316, output_tokens=80)
        result = types.SimpleNamespace(
            output=self._run_sync_result_text,
            data=self._run_sync_result_text,
            usage=lambda: usage_obj,
        )
        return result


class _FakeAnthropicModel:
    def __init__(self, model_name, provider=None, **kwargs):
        self.model_name = model_name
        self.provider = provider


class _FakeAnthropicProvider:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


class _FakeAsyncAnthropic:
    last_instance = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        _FakeAsyncAnthropic.last_instance = self


def _install_fake_pydantic_ai_anthropic(monkeypatch):
    """Inject fake `pydantic_ai` (anthropic submodules) + `anthropic`."""
    fake_mod = types.ModuleType("pydantic_ai")
    fake_mod.Agent = _FakeAgent
    fake_mod.UsageLimits = _FakeUsageLimits

    fake_models_mod = types.ModuleType("pydantic_ai.models")
    fake_anthropic_model_mod = types.ModuleType("pydantic_ai.models.anthropic")
    fake_anthropic_model_mod.AnthropicModel = _FakeAnthropicModel

    fake_providers_mod = types.ModuleType("pydantic_ai.providers")
    fake_anthropic_provider_mod = types.ModuleType("pydantic_ai.providers.anthropic")
    fake_anthropic_provider_mod.AnthropicProvider = _FakeAnthropicProvider

    fake_mod.models = fake_models_mod
    fake_mod.providers = fake_providers_mod

    monkeypatch.setitem(sys.modules, "pydantic_ai", fake_mod)
    monkeypatch.setitem(sys.modules, "pydantic_ai.models", fake_models_mod)
    monkeypatch.setitem(sys.modules, "pydantic_ai.models.anthropic", fake_anthropic_model_mod)
    monkeypatch.setitem(sys.modules, "pydantic_ai.providers", fake_providers_mod)
    monkeypatch.setitem(sys.modules, "pydantic_ai.providers.anthropic", fake_anthropic_provider_mod)

    fake_anthropic_pkg = types.ModuleType("anthropic")
    fake_anthropic_pkg.AsyncAnthropic = _FakeAsyncAnthropic
    monkeypatch.setitem(sys.modules, "anthropic", fake_anthropic_pkg)

    return fake_mod, fake_anthropic_pkg


# ---------------------------------------------------------------------------
# tmp git repo helper (§1i pre-staged deterministic state)
# ---------------------------------------------------------------------------

def _git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root)] + list(args),
        check=True,
        capture_output=True,
        text=True,
    )


def _init_repo(root):
    _git(root, "init", "-q")
    _git(root, "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "--allow-empty", "-q", "-m", "init")


def _write_creds(path, access_token, refresh_token, expires_at_ms, extra_top_level=None):
    payload = {
        "claudeAiOauth": {
            "accessToken": access_token,
            "refreshToken": refresh_token,
            "expiresAt": expires_at_ms,
        }
    }
    if extra_top_level:
        payload.update(extra_top_level)
    path.write_text(json.dumps(payload))


# ---------------------------------------------------------------------------
# Autouse fixture: clean module + backend registry between tests
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_state():
    yield
    reset_backends()
    sys.modules.pop("lib.reference_backends.pydantic_anthropic", None)
    sys.modules.pop("lib.reference_backends.anthropic_oauth", None)
    sys.modules.pop("lib.reference_backends.pydantic_openai", None)
    for mod_name in list(sys.modules):
        if mod_name == "pydantic_ai" or mod_name.startswith("pydantic_ai."):
            sys.modules.pop(mod_name, None)
        if mod_name == "anthropic" or mod_name.startswith("anthropic."):
            sys.modules.pop(mod_name, None)


# ---------------------------------------------------------------------------
# AC1 — deps not importable -> E_LLM_API_DEPS_MISSING, recoverable=False
# ---------------------------------------------------------------------------

def test_ac1_deps_missing_returns_error_with_pip_hint(monkeypatch, tmp_path):
    """AC1: deps not importable -> StepResult error E_LLM_API_DEPS_MISSING,
    recoverable=False, error string contains pip hint.
    """
    for mod_name in list(sys.modules):
        if mod_name in ("pydantic_ai", "anthropic") or mod_name.startswith(
            ("pydantic_ai.", "anthropic.")
        ):
            monkeypatch.delitem(sys.modules, mod_name, raising=False)

    import importlib.util as importlib_util
    real_find_spec = importlib_util.find_spec

    def _fake_find_spec(name, *args, **kwargs):
        if name in ("pydantic_ai", "anthropic"):
            return None
        return real_find_spec(name, *args, **kwargs)

    monkeypatch.setattr(importlib_util, "find_spec", _fake_find_spec)

    mod = _import_anthropic_backend()
    _init_repo(tmp_path)

    result = mod.pydantic_anthropic_backend(
        prompt="hello",
        model="claude-sonnet",
        timeout_sec=10,
        step_name="test-ac1",
        extra_data={"workspace_root": str(tmp_path)},
    )
    assert result.status == "error", f"AC1: expected status='error'; got {result.status!r}"
    assert result.error_code == "E_LLM_API_DEPS_MISSING", (
        f"AC1: error_code must be 'E_LLM_API_DEPS_MISSING'; got {result.error_code!r}"
    )
    assert result.recoverable is False
    # D52228C3 / GH1112: hint is single-sourced via package_meta.install_hint.
    from bytedigger_engine import package_meta  # noqa: PLC0415

    assert package_meta.install_hint("agentic-pydantic") in (result.error or ""), (
        f"AC1: error must contain a pip install hint; got {result.error!r}"
    )


# ---------------------------------------------------------------------------
# AC2 — creds file absent -> E_LLM_API_KEY_MISSING, recoverable=False
# ---------------------------------------------------------------------------

def test_ac2_missing_creds_file_returns_key_missing(monkeypatch, tmp_path):
    """AC2: creds file absent -> E_LLM_API_KEY_MISSING, recoverable=False,
    error contains the creds path (via CLAUDE_CODE_OAUTH_CREDENTIALS).
    """
    _install_fake_pydantic_ai_anthropic(monkeypatch)
    mod = _import_anthropic_backend()

    missing_path = tmp_path / "nope" / "creds.json"
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_CREDENTIALS", str(missing_path))

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _init_repo(repo_root)

    result = mod.pydantic_anthropic_backend(
        prompt="hello",
        model="claude-sonnet",
        timeout_sec=10,
        step_name="test-ac2",
        extra_data={"workspace_root": str(repo_root)},
    )
    assert result.status == "error", f"AC2: expected status='error'; got {result.status!r}"
    assert result.error_code == "E_LLM_API_KEY_MISSING", (
        f"AC2: error_code must be 'E_LLM_API_KEY_MISSING'; got {result.error_code!r}"
    )
    assert result.recoverable is False
    assert str(missing_path) in (result.error or ""), (
        f"AC2: error must contain the creds path {missing_path!r}; got {result.error!r}"
    )


# ---------------------------------------------------------------------------
# AC3 — fresh token: no HTTP call, file unchanged
# ---------------------------------------------------------------------------

def test_ac3_fresh_token_returns_stored_no_http_no_write(tmp_path):
    """AC3: fresh token (expiresAt far future) -> get_access_token returns
    stored accessToken; http_post seam NOT called; file bytes unchanged.
    """
    mod = _import_oauth()

    creds_path = tmp_path / "creds.json"
    far_future_ms = int((__import__("time").time() + 100_000) * 1000)
    _write_creds(creds_path, "stored-access-token", "stored-refresh-token", far_future_ms)
    before_bytes = creds_path.read_bytes()

    http_calls = []

    def _spy_http_post(url, body):
        http_calls.append((url, body))
        raise AssertionError("AC3: http_post must NOT be called for a fresh token")

    token = mod.get_access_token(path=creds_path, now=None, http_post=_spy_http_post)

    assert token == "stored-access-token", f"AC3: expected stored token; got {token!r}"
    assert http_calls == [], f"AC3: http_post must not be called; got {http_calls!r}"
    assert creds_path.read_bytes() == before_bytes, "AC3: creds file must be byte-unchanged"


# ---------------------------------------------------------------------------
# AC4 — expired token refresh: correct POST body + atomic update
# ---------------------------------------------------------------------------

def test_ac4_expired_token_refreshes_and_atomically_updates_file(tmp_path):
    """AC4: expired token -> POST body has grant_type/refresh_token/client_id;
    returns new access_token; file atomically updated (new fields, unrelated
    top-level key preserved).
    """
    mod = _import_oauth()

    creds_path = tmp_path / "creds.json"
    fixed_now = 1_000_000.0
    past_ms = int((fixed_now - 100_000) * 1000)
    _write_creds(
        creds_path,
        "old-access-token",
        "old-refresh-token",
        past_ms,
        extra_top_level={"unrelatedKey": "preserve-me"},
    )

    captured = {}

    def _fake_http_post(url, body):
        captured["url"] = url
        captured["body"] = body
        return {
            "access_token": "new-access-token",
            "refresh_token": "new-refresh-token",
            "expires_in": 3600,
        }

    token = mod.get_access_token(path=creds_path, now=fixed_now, http_post=_fake_http_post)

    assert token == "new-access-token", f"AC4: expected new access token; got {token!r}"
    assert captured["url"] == mod.OAUTH_TOKEN_URL
    assert captured["body"]["grant_type"] == "refresh_token"
    assert captured["body"]["refresh_token"] == "old-refresh-token"
    assert captured["body"]["client_id"] == mod.OAUTH_CLIENT_ID
    assert mod.OAUTH_CLIENT_ID == "9d1c250a-e61b-44d9-88ed-5944d1962f5e"

    updated = json.loads(creds_path.read_text())
    assert updated["claudeAiOauth"]["accessToken"] == "new-access-token"
    assert updated["claudeAiOauth"]["refreshToken"] == "new-refresh-token"
    assert updated["claudeAiOauth"]["expiresAt"] == int((fixed_now + 3600) * 1000)
    assert updated["unrelatedKey"] == "preserve-me", (
        "AC4: unrelated top-level key must be preserved through the atomic write"
    )


# ---------------------------------------------------------------------------
# AC5 — http_post raises -> OAuthCredentialsError + backend maps to
# E_LLM_API_KEY_MISSING
# ---------------------------------------------------------------------------

def test_ac5_http_post_raises_maps_to_key_missing(monkeypatch, tmp_path):
    """AC5: http_post raises -> get_access_token raises OAuthCredentialsError;
    backend maps that to E_LLM_API_KEY_MISSING recoverable=False.
    """
    oauth_mod = _import_oauth()

    creds_path = tmp_path / "creds.json"
    past_ms = int((__import__("time").time() - 100_000) * 1000)
    _write_creds(creds_path, "old-access-token", "old-refresh-token", past_ms)

    def _raising_http_post(url, body):
        raise RuntimeError("network down")

    with pytest.raises(oauth_mod.OAuthCredentialsError):
        oauth_mod.get_access_token(path=creds_path, now=None, http_post=_raising_http_post)

    _install_fake_pydantic_ai_anthropic(monkeypatch)
    mod = _import_anthropic_backend()
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_CREDENTIALS", str(creds_path))

    def _patched_get_access_token(*, path=None, now=None, http_post=None):
        raise mod.anthropic_oauth.OAuthCredentialsError("refresh failed: network down")

    monkeypatch.setattr(mod.anthropic_oauth, "get_access_token", _patched_get_access_token)

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _init_repo(repo_root)

    result = mod.pydantic_anthropic_backend(
        prompt="hello",
        model="claude-sonnet",
        timeout_sec=10,
        step_name="test-ac5",
        extra_data={"workspace_root": str(repo_root)},
    )
    assert result.status == "error"
    assert result.error_code == "E_LLM_API_KEY_MISSING", (
        f"AC5: error_code must be 'E_LLM_API_KEY_MISSING'; got {result.error_code!r}"
    )
    assert result.recoverable is False


# ---------------------------------------------------------------------------
# AC6 — AsyncAnthropic gets auth_token + oauth beta header, no api_key
# ---------------------------------------------------------------------------

def test_ac6_client_wired_with_oauth_token_no_api_key(monkeypatch, tmp_path):
    """AC6: fake AsyncAnthropic captures auth_token=<token> and
    default_headers['anthropic-beta']=='oauth-2025-04-20'; no api_key kwarg;
    AnthropicModel got model + provider wrapping that client.
    """
    fake_pydantic_ai, fake_anthropic_pkg = _install_fake_pydantic_ai_anthropic(monkeypatch)
    mod = _import_anthropic_backend()

    creds_path = tmp_path / "creds.json"
    far_future_ms = int((__import__("time").time() + 100_000) * 1000)
    _write_creds(creds_path, "the-access-token", "refresh", far_future_ms)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_CREDENTIALS", str(creds_path))

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _init_repo(repo_root)

    result = mod.pydantic_anthropic_backend(
        prompt="hello",
        model="claude-sonnet",
        timeout_sec=10,
        step_name="test-ac6",
        extra_data={"workspace_root": str(repo_root)},
    )
    assert result.status == "ok", (
        f"AC6: expected status='ok' for the wiring probe; got {result.status!r} "
        f"error_code={result.error_code!r} error={result.error!r}"
    )

    client = fake_anthropic_pkg.AsyncAnthropic.last_instance
    assert client is not None, "AC6: AsyncAnthropic must have been instantiated"
    assert client.kwargs.get("auth_token") == "the-access-token", (
        f"AC6: auth_token must equal the OAuth token; got {client.kwargs!r}"
    )
    assert "api_key" not in client.kwargs, "AC6: no api_key kwarg must be present"
    headers = client.kwargs.get("default_headers") or {}
    assert headers.get("anthropic-beta") == "oauth-2025-04-20", (
        f"AC6: default_headers['anthropic-beta'] must be 'oauth-2025-04-20'; got {headers!r}"
    )


# ---------------------------------------------------------------------------
# AC7 — exactly 4 tool_plain registrations; delegation proof via write_file
# ---------------------------------------------------------------------------

def test_ac7_registers_four_tools_and_write_file_delegates(monkeypatch, tmp_path):
    """AC7: fake Agent captures exactly 4 tool_plain registrations named
    write_file/edit_file/run_tests/bash; calling captured write_file writes
    the file under the tmp git root (delegation proof).
    """
    fake_pydantic_ai, _ = _install_fake_pydantic_ai_anthropic(monkeypatch)
    mod = _import_anthropic_backend()

    creds_path = tmp_path / "creds.json"
    far_future_ms = int((__import__("time").time() + 100_000) * 1000)
    _write_creds(creds_path, "tok", "refresh", far_future_ms)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_CREDENTIALS", str(creds_path))

    repo_root = tmp_path / "repo"
    repo_root.mkdir()
    _init_repo(repo_root)

    result = mod.pydantic_anthropic_backend(
        prompt="do something",
        model="claude-sonnet",
        timeout_sec=10,
        step_name="test-ac7",
        extra_data={"workspace_root": str(repo_root)},
    )
    assert result.status == "ok"

    agent = fake_pydantic_ai.Agent.last_instance
    assert agent is not None
    tool_names = sorted(getattr(t, "__name__", "") for t in agent.tools)
    assert tool_names == ["bash", "edit_file", "run_tests", "write_file"], (
        f"AC7: expected exactly 4 named tools; got {tool_names!r}"
    )

    write_file_tool = next(t for t in agent.tools if getattr(t, "__name__", "") == "write_file")
    write_file_tool("delegated.txt", "hello from tool")
    written = repo_root / "delegated.txt"
    assert written.exists() and written.read_text() == "hello from tool", (
        "AC7: captured write_file must delegate to the real _tool_write_file helper"
    )


# ---------------------------------------------------------------------------
# AC8 — success path: manifest, tokens_in/out, command
# ---------------------------------------------------------------------------

def test_ac8_success_path_manifest_and_tokens(monkeypatch, tmp_path):
    """AC8: success run -> status ok; manifest_source=='git_diff',
    worker_written_paths lists the file the fake tool wrote,
    command==['pydantic-anthropic', model], tokens_in==1316, tokens_out==80.
    """
    fake_pydantic_ai, _ = _install_fake_pydantic_ai_anthropic(monkeypatch)
    mod = _import_anthropic_backend()

    creds_path = tmp_path / "creds.json"
    far_future_ms = int((__import__("time").time() + 100_000) * 1000)
    _write_creds(creds_path, "tok", "refresh", far_future_ms)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_CREDENTIALS", str(creds_path))

    root = tmp_path / "repo"
    root.mkdir()
    _init_repo(root)

    model_text = "I created the file for you."

    class _WritingAgent(_FakeAgent):
        def run_sync(self, prompt, usage_limits=None, **kwargs):
            (root / "created_by_agent.txt").write_text("agent output")
            usage_obj = types.SimpleNamespace(input_tokens=1316, output_tokens=80)
            return types.SimpleNamespace(output=model_text, data=model_text, usage=lambda: usage_obj)

    monkeypatch.setattr(fake_pydantic_ai, "Agent", _WritingAgent)

    result = mod.pydantic_anthropic_backend(
        prompt="create a file",
        model="claude-sonnet",
        timeout_sec=30,
        step_name="test-ac8",
        extra_data={"workspace_root": str(root)},
    )

    assert result.status == "ok", (
        f"AC8: expected status='ok'; got {result.status!r} error_code={result.error_code!r} "
        f"error={result.error!r}"
    )
    assert result.data.get("manifest_source") == "git_diff"
    assert result.data.get("worker_written_paths") == ["created_by_agent.txt"], (
        f"AC8: expected only created_by_agent.txt; got {result.data.get('worker_written_paths')!r}"
    )
    assert result.data.get("command") == ["pydantic-anthropic", "claude-sonnet"], (
        f"AC8: expected command tuple; got {result.data.get('command')!r}"
    )
    assert result.data.get("tokens_in") == 1316, f"AC8: tokens_in mismatch; got {result.data!r}"
    assert result.data.get("tokens_out") == 80, f"AC8: tokens_out mismatch; got {result.data!r}"


# ---------------------------------------------------------------------------
# AC9 — timeout path
# ---------------------------------------------------------------------------

def test_ac9_timeout_returns_timed_out_error_with_manifest(monkeypatch, tmp_path):
    """AC9: fake agent blocks past timeout_sec -> E_LLM_API_TIMEOUT,
    recoverable=True, data.timed_out==True, data has worker_written_paths.
    """
    fake_pydantic_ai, _ = _install_fake_pydantic_ai_anthropic(monkeypatch)
    mod = _import_anthropic_backend()

    creds_path = tmp_path / "creds.json"
    far_future_ms = int((__import__("time").time() + 100_000) * 1000)
    _write_creds(creds_path, "tok", "refresh", far_future_ms)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_CREDENTIALS", str(creds_path))

    root = tmp_path / "repo"
    root.mkdir()
    _init_repo(root)

    release_event = threading.Event()

    class _BlockingAgent(_FakeAgent):
        def run_sync(self, prompt, usage_limits=None, **kwargs):
            (root / "written_before_block.txt").write_text("data")
            release_event.wait(timeout=8)
            return types.SimpleNamespace(output="done", data="done")

    monkeypatch.setattr(fake_pydantic_ai, "Agent", _BlockingAgent)

    result = mod.pydantic_anthropic_backend(
        prompt="do something",
        model="claude-sonnet",
        timeout_sec=0.5,
        step_name="test-ac9",
        extra_data={"workspace_root": str(root)},
    )
    release_event.set()

    assert result.status == "error", f"AC9: expected status='error'; got {result.status!r}"
    assert result.error_code == "E_LLM_API_TIMEOUT", (
        f"AC9: expected 'E_LLM_API_TIMEOUT'; got {result.error_code!r}"
    )
    assert result.recoverable is True
    assert result.data is not None, "AC9: data must not be None on timeout"
    assert result.data.get("timed_out") is True
    assert "written_before_block.txt" in result.data.get("worker_written_paths", []), (
        f"AC9: pre-block write must be captured in the manifest; got {result.data!r}"
    )


# ---------------------------------------------------------------------------
# AC10 — register(): spy on register_backend, ImportError when deps absent
# ---------------------------------------------------------------------------

def test_ac10_register_calls_register_backend_or_raises_importerror(monkeypatch):
    """AC10: register() with deps present -> register_backend spy called
    with ('pydantic-anthropic', handler, manifest_source='git_diff'); with
    deps absent -> ImportError with pip hint.
    """
    for mod_name in list(sys.modules):
        if mod_name in ("pydantic_ai", "anthropic") or mod_name.startswith(
            ("pydantic_ai.", "anthropic.")
        ):
            monkeypatch.delitem(sys.modules, mod_name, raising=False)

    import importlib.util as importlib_util
    real_find_spec = importlib_util.find_spec

    def _fake_find_spec_missing(name, *args, **kwargs):
        if name in ("pydantic_ai", "anthropic"):
            return None
        return real_find_spec(name, *args, **kwargs)

    monkeypatch.setattr(importlib_util, "find_spec", _fake_find_spec_missing)
    mod = _import_anthropic_backend()

    # D52228C3 / GH1112: hint is single-sourced via package_meta.install_hint.
    import re  # noqa: PLC0415

    from bytedigger_engine import package_meta  # noqa: PLC0415

    with pytest.raises(ImportError, match=re.escape(package_meta.install_hint("agentic-pydantic"))):
        mod.register()

    _install_fake_pydantic_ai_anthropic(monkeypatch)
    sys.modules.pop("lib.reference_backends.pydantic_anthropic", None)
    mod2 = _import_anthropic_backend()

    calls = []
    original_register_backend = llm_subprocess.register_backend

    def _spy_register_backend(name, handler, **kwargs):
        calls.append((name, handler, kwargs))
        return original_register_backend(name, handler, **kwargs)

    monkeypatch.setattr(mod2, "register_backend", _spy_register_backend)
    mod2.register()

    assert len(calls) == 1, f"AC10: expected exactly one register_backend call; got {calls!r}"
    name, handler, kwargs = calls[0]
    assert name == "pydantic-anthropic"
    assert handler is mod2.pydantic_anthropic_backend
    assert kwargs.get("manifest_source") == "git_diff"


# ---------------------------------------------------------------------------
# AC11 — pydantic_openai_backend success data now includes tokens_in/out
# ---------------------------------------------------------------------------

def test_ac11_openai_backend_success_includes_tokens(monkeypatch, tmp_path):
    """AC11: pydantic_openai_backend success data now includes
    tokens_in/tokens_out from fake result.usage() (input_tokens/output_tokens).
    """
    fake_mod = types.ModuleType("pydantic_ai")
    fake_mod.UsageLimits = _FakeUsageLimits

    fake_models_mod = types.ModuleType("pydantic_ai.models")
    fake_openai_mod = types.ModuleType("pydantic_ai.models.openai")

    class _FakeOpenAIChatModel:
        def __init__(self, model_name, provider=None, **kwargs):
            self.model_name = model_name
            self.provider = provider

    fake_openai_mod.OpenAIChatModel = _FakeOpenAIChatModel

    fake_providers_mod = types.ModuleType("pydantic_ai.providers")
    fake_azure_mod = types.ModuleType("pydantic_ai.providers.azure")

    class _FakeAzureProvider:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    fake_azure_mod.AzureProvider = _FakeAzureProvider

    model_text = "done"

    class _UsageAgent:
        last_instance = None

        def __init__(self, model, tools=None, **kwargs):
            self.tools = tools or []
            _UsageAgent.last_instance = self

        def tool_plain(self, fn=None, **kwargs):
            def _wrap(f):
                self.tools.append(f)
                return f
            if fn is not None:
                return _wrap(fn)
            return _wrap

        def run_sync(self, prompt, usage_limits=None, **kwargs):
            usage_obj = types.SimpleNamespace(input_tokens=200, output_tokens=40)
            return types.SimpleNamespace(output=model_text, data=model_text, usage=lambda: usage_obj)

    fake_mod.Agent = _UsageAgent
    fake_mod.models = fake_models_mod
    fake_mod.providers = fake_providers_mod

    monkeypatch.setitem(sys.modules, "pydantic_ai", fake_mod)
    monkeypatch.setitem(sys.modules, "pydantic_ai.models", fake_models_mod)
    monkeypatch.setitem(sys.modules, "pydantic_ai.models.openai", fake_openai_mod)
    monkeypatch.setitem(sys.modules, "pydantic_ai.providers", fake_providers_mod)
    monkeypatch.setitem(sys.modules, "pydantic_ai.providers.azure", fake_azure_mod)

    mod = _import_openai_backend()
    mod.register()

    monkeypatch.setenv("AZURE_OPENAI_KEY", "fake-key")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://fake.openai.azure.com")

    root = tmp_path / "repo"
    root.mkdir()
    _init_repo(root)

    result = mod.pydantic_openai_backend(
        prompt="do something",
        model="gpt-4o",
        timeout_sec=30,
        step_name="test-ac11",
        extra_data={"workspace_root": str(root)},
    )
    assert result.status == "ok"
    assert result.data.get("tokens_in") == 200, f"AC11: expected tokens_in==200; got {result.data!r}"
    assert result.data.get("tokens_out") == 40, f"AC11: expected tokens_out==40; got {result.data!r}"


# ---------------------------------------------------------------------------
# AC12 — _extract_usage_tokens: v2 names, v1 fallback, raising -> (None, None)
# ---------------------------------------------------------------------------

def test_ac12_extract_usage_tokens_v2_v1_and_raising():
    """AC12: _extract_usage_tokens: v2 names -> ints; v1 names
    (request_tokens/response_tokens) -> ints; usage() raising -> (None, None).
    """
    mod = _import_openai_backend()
    extract = getattr(mod, "_extract_usage_tokens", None)
    assert extract is not None, "AC12: _extract_usage_tokens must exist on pydantic_openai module"

    v2_usage = types.SimpleNamespace(input_tokens=111, output_tokens=22)
    v2_result = types.SimpleNamespace(usage=lambda: v2_usage)
    assert extract(v2_result) == (111, 22), f"AC12: v2 names must yield (111,22); got {extract(v2_result)!r}"

    v1_usage = types.SimpleNamespace(request_tokens=55, response_tokens=6)
    v1_result = types.SimpleNamespace(usage=lambda: v1_usage)
    assert extract(v1_result) == (55, 6), f"AC12: v1 fallback must yield (55,6); got {extract(v1_result)!r}"

    def _raising_usage():
        raise RuntimeError("no usage")

    raising_result = types.SimpleNamespace(usage=_raising_usage)
    assert extract(raising_result) == (None, None), (
        f"AC12: raising usage() must yield (None, None); got {extract(raising_result)!r}"
    )


# ---------------------------------------------------------------------------
# AC13 — non-git workspace_root -> E_MANIFEST_NO_GIT
# ---------------------------------------------------------------------------

def test_ac13_non_git_root_returns_manifest_no_git(monkeypatch, tmp_path):
    """AC13: non-git workspace_root -> E_MANIFEST_NO_GIT, recoverable=False
    (anthropic backend).
    """
    _install_fake_pydantic_ai_anthropic(monkeypatch)
    mod = _import_anthropic_backend()

    creds_path = tmp_path / "creds.json"
    far_future_ms = int((__import__("time").time() + 100_000) * 1000)
    _write_creds(creds_path, "tok", "refresh", far_future_ms)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_CREDENTIALS", str(creds_path))

    non_git_root = tmp_path / "not_a_repo"
    non_git_root.mkdir()

    result = mod.pydantic_anthropic_backend(
        prompt="do something",
        model="claude-sonnet",
        timeout_sec=30,
        step_name="test-ac13",
        extra_data={"workspace_root": str(non_git_root)},
    )
    assert result.status == "error", f"AC13: expected status='error'; got {result.status!r}"
    assert result.error_code == "E_MANIFEST_NO_GIT", (
        f"AC13: error_code must be 'E_MANIFEST_NO_GIT'; got {result.error_code!r}"
    )
    assert result.recoverable is False


# ---------------------------------------------------------------------------
# AC14 — run_sync raises -> E_LLM_API_BAD_RESPONSE, recoverable=True, data=None
# ---------------------------------------------------------------------------

def test_ac14_run_sync_raises_returns_bad_response(monkeypatch, tmp_path):
    """AC14(a): fake agent whose run_sync raises a plain RuntimeError ->
    status='error', error_code=='E_LLM_API_BAD_RESPONSE', recoverable=True,
    data is None.
    """
    fake_pydantic_ai, _ = _install_fake_pydantic_ai_anthropic(monkeypatch)
    mod = _import_anthropic_backend()

    creds_path = tmp_path / "creds.json"
    far_future_ms = int((__import__("time").time() + 100_000) * 1000)
    _write_creds(creds_path, "tok", "refresh", far_future_ms)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_CREDENTIALS", str(creds_path))

    root = tmp_path / "repo"
    root.mkdir()
    _init_repo(root)

    class _RaisingAgent(_FakeAgent):
        def run_sync(self, prompt, usage_limits=None, **kwargs):
            raise RuntimeError("model call failed")

    monkeypatch.setattr(fake_pydantic_ai, "Agent", _RaisingAgent)

    result = mod.pydantic_anthropic_backend(
        prompt="do something",
        model="claude-sonnet",
        timeout_sec=10,
        step_name="test-ac14a",
        extra_data={"workspace_root": str(root)},
    )
    assert result.status == "error", f"AC14a: expected status='error'; got {result.status!r}"
    assert result.error_code == "E_LLM_API_BAD_RESPONSE", (
        f"AC14a: expected 'E_LLM_API_BAD_RESPONSE'; got {result.error_code!r}"
    )
    assert result.recoverable is True
    assert result.data is None, f"AC14a: data must be None; got {result.data!r}"


def test_ac14_usage_limit_exceeded_returns_bad_response_with_usage_message(monkeypatch, tmp_path):
    """AC14(b): fake agent whose run_sync raises an exception whose type name
    contains 'UsageLimitExceeded' -> same error_code E_LLM_API_BAD_RESPONSE,
    error message mentions usage limit, recoverable=True, data is None.
    """
    fake_pydantic_ai, _ = _install_fake_pydantic_ai_anthropic(monkeypatch)
    mod = _import_anthropic_backend()

    creds_path = tmp_path / "creds.json"
    far_future_ms = int((__import__("time").time() + 100_000) * 1000)
    _write_creds(creds_path, "tok", "refresh", far_future_ms)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_CREDENTIALS", str(creds_path))

    root = tmp_path / "repo"
    root.mkdir()
    _init_repo(root)

    class UsageLimitExceeded(Exception):
        pass

    class _LimitAgent(_FakeAgent):
        def run_sync(self, prompt, usage_limits=None, **kwargs):
            raise UsageLimitExceeded("request limit reached")

    monkeypatch.setattr(fake_pydantic_ai, "Agent", _LimitAgent)

    result = mod.pydantic_anthropic_backend(
        prompt="do something",
        model="claude-sonnet",
        timeout_sec=10,
        step_name="test-ac14b",
        extra_data={"workspace_root": str(root)},
    )
    assert result.status == "error", f"AC14b: expected status='error'; got {result.status!r}"
    assert result.error_code == "E_LLM_API_BAD_RESPONSE", (
        f"AC14b: expected 'E_LLM_API_BAD_RESPONSE'; got {result.error_code!r}"
    )
    assert result.recoverable is True
    assert result.data is None, f"AC14b: data must be None; got {result.data!r}"
    assert "usage limit" in (result.error or "").lower(), (
        f"AC14b: error message must mention usage limit; got {result.error!r}"
    )
