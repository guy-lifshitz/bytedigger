"""OSS reference backend: OpenAI-compatible chat completions with a base_url (bd#83).

Stdlib-only (urllib). Talks to any server that speaks ``POST <base>/chat/completions``
(Ollama, LM Studio, vLLM, mlx, hosted gateways). Text-only: no tools, no streaming.

Opt-in: ``register()`` registers nothing and returns False unless
``HAL_OPENAI_BASE_URL`` is set. Config is read at call time through the config
provider's environment mapping (the source the dispatcher uses).

Every failure comes back as an error ``StepResult``; nothing is raised.
"""
from __future__ import annotations

import http.client
import json
import socket
import time
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlparse
from urllib.request import Request

from bytedigger_engine.config_provider import env_mapping
from bytedigger_engine.contracts import StepResult
from bytedigger_engine.llm_subprocess import register_backend

_BACKEND_NAME = "openai-compat"
_ENV_BASE_URL = "HAL_OPENAI_BASE_URL"
_ENV_API_KEY = "HAL_OPENAI_API_KEY"
_ENV_MODEL_PREFIX = "HAL_OPENAI_MODEL_"
_MAX_BODY_BYTES = 16 * 1024 * 1024
_RECOVERABLE_HTTP = frozenset({408, 429})


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse every 3xx: the key is never sent to a second host."""

    def redirect_request(
        self, req: Request, fp: Any, code: int, msg: str, headers: Any, newurl: str,
    ) -> Request | None:
        return None


def _env_text(name: str) -> str:
    value = env_mapping().get(name)
    return value.strip() if isinstance(value, str) else ""


def _resolve_model(model: str) -> str:
    override = _env_text(_ENV_MODEL_PREFIX + model.lower().upper())
    return override or model


def _effective_model(model: str) -> str:
    """The model actually sent; read at call time (registered as the effective_model hook)."""
    return _resolve_model(model)


def _error(step_name: str, t0: float | None, message: str, code: str, recoverable: bool) -> StepResult:
    duration_ms = int((time.monotonic() - t0) * 1000) if t0 is not None else 0
    return StepResult(
        status="error",
        data=None,
        duration_ms=duration_ms,
        step_name=step_name,
        error=message,
        error_code=code,
        recoverable=recoverable,
    )


def _check_base_url(raw: str) -> str | None:
    """Return the base URL without a trailing slash, or None when it is not a usable http(s) URL."""
    try:
        parsed = urlparse(raw)
        if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname:
            return None
        _ = parsed.port  # ValueError on a malformed or out-of-range port
    except ValueError:
        return None
    return raw.rstrip("/")


def _post_chat_completions(req: Request, timeout: float) -> bytes:
    opener = urllib.request.build_opener(_NoRedirect())
    with opener.open(req, timeout=timeout) as resp:
        return bytes(resp.read(_MAX_BODY_BYTES + 1))


def _is_timeout(exc: object) -> bool:
    return isinstance(exc, (socket.timeout, TimeoutError))


def openai_compat_backend(
    *,
    prompt: str,
    model: str,
    timeout_sec: int | float,
    step_name: str,
    extra_data: dict | None = None,
    allowed_tools: object = None,
    run_ctx: object = None,
    hard_gate: bool = False,
    gate_label: str | None = None,
    idle_timeout_sec: object = None,
    stable_prefix: str = "",
    effort: object = None,
) -> StepResult:
    """OpenAI-compatible chat completions backend (text-only).

    Accepted-and-ignored kwargs: allowed_tools, run_ctx, hard_gate,
    stable_prefix, effort.
    """
    base_raw = _env_text(_ENV_BASE_URL)
    if not base_raw:
        return _error(step_name, None, f"{_ENV_BASE_URL} is not set", "E_LLM_BASE_URL_MISSING", False)
    base = _check_base_url(base_raw)
    if base is None:
        return _error(
            step_name, None,
            f"{_ENV_BASE_URL} must be an http or https URL with a host and a valid port",
            "E_LLM_BASE_URL_INVALID", False,
        )

    api_key = _env_text(_ENV_API_KEY)
    model_id = _resolve_model(model)
    body = {
        "model": model_id,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
    }
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    if (
        isinstance(idle_timeout_sec, (int, float))
        and not isinstance(idle_timeout_sec, bool)
        and idle_timeout_sec > 0
    ):
        effective_timeout = min(timeout_sec, idle_timeout_sec)
    else:
        effective_timeout = timeout_sec

    t0 = time.monotonic()
    try:
        req = Request(
            base + "/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        raw_bytes = _post_chat_completions(req, effective_timeout)
    except urllib.error.HTTPError as e:
        try:
            e.close()
        except Exception:  # noqa: BLE001
            pass
        recoverable = e.code in _RECOVERABLE_HTTP or e.code >= 500
        return _error(step_name, t0, f"HTTP {e.code}: {e.reason}", "E_LLM_API_HTTP", recoverable)
    except (socket.timeout, TimeoutError) as e:
        return _error(step_name, t0, f"Request timed out: {type(e).__name__}", "E_LLM_TIMEOUT", True)
    except urllib.error.URLError as e:
        if _is_timeout(e.reason):
            return _error(step_name, t0, "Request timed out", "E_LLM_TIMEOUT", True)
        return _error(step_name, t0, f"Network error: {type(e.reason).__name__}", "E_LLM_API_NETWORK", True)
    except (http.client.HTTPException, OSError) as e:
        return _error(step_name, t0, f"Network error: {type(e).__name__}", "E_LLM_API_NETWORK", True)
    except Exception as e:  # noqa: BLE001
        return _error(step_name, t0, f"Unexpected error: {type(e).__name__}", "E_LLM_API_BAD_RESPONSE", False)
    wall_ms = int((time.monotonic() - t0) * 1000)

    if len(raw_bytes) > _MAX_BODY_BYTES:
        return _error(step_name, t0, "Response body exceeds the 16 MiB limit", "E_LLM_API_BAD_RESPONSE", False)
    try:
        parsed = json.loads(raw_bytes)
        content = parsed["choices"][0]["message"]["content"]
    except Exception:  # noqa: BLE001
        return _error(step_name, t0, "Response is not a chat completion", "E_LLM_API_BAD_RESPONSE", False)
    if not isinstance(content, str) or not content:
        return _error(step_name, t0, "Response has no text content", "E_LLM_API_BAD_RESPONSE", False)

    merged: dict = dict(extra_data or {})
    if gate_label is not None:
        merged["gate_label"] = gate_label
    for reserved in ("observed_model", "usage", "billing_mode"):
        merged.pop(reserved, None)
    merged["raw_response"] = content
    merged["response_bytes"] = len(content.encode("utf-8"))
    merged["command"] = [_BACKEND_NAME, "chat.completions", model_id]
    merged["wall_ms"] = wall_ms
    merged["observed_tools"] = []
    served = parsed.get("model")
    if isinstance(served, str) and served.strip():
        merged["observed_model"] = served
    raw_usage = parsed.get("usage")
    if isinstance(raw_usage, dict):
        usage: dict = {}
        for src, dst in (("prompt_tokens", "input_tokens"), ("completion_tokens", "output_tokens")):
            count = raw_usage.get(src)
            if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
                usage[dst] = count
        if usage:
            merged["usage"] = usage
    if api_key:
        merged["billing_mode"] = "metered"

    return StepResult(
        status="ok",
        data=merged,
        duration_ms=int((time.monotonic() - t0) * 1000),
        step_name=step_name,
    )


def register() -> bool:
    """Register 'openai-compat' when HAL_OPENAI_BASE_URL is set; return whether it did.

    Idempotent (overwrite=True). Nothing is registered, and False is returned,
    when the base URL is unset or blank. ``billing:metered`` is declared only when
    an API key is configured now.
    """
    if not _env_text(_ENV_BASE_URL):
        return False
    caps = {"no_tools"}
    if _env_text(_ENV_API_KEY):
        caps.add("billing:metered")
    register_backend(
        _BACKEND_NAME,
        openai_compat_backend,
        manifest_source="api_text_response",
        capabilities=frozenset(caps),
        overwrite=True,
        effective_model=_effective_model,
    )
    return True
