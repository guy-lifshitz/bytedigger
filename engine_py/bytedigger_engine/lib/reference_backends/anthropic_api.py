"""OSS reference backend: Anthropic Messages API (#302 seam, #2A6986ED).

Stdlib-only (urllib) — no anthropic SDK, no pip dependencies.
Registers as 'anthropic-api' via register_backend() so
invoke_llm_subprocess(backend='anthropic-api', ...) dispatches here.

Limitation: text-only (opaque-text phases). Agentic phases (phase_5_implement)
still require a CLI-based backend (claude-subprocess / claude-in-session).
Update _ALIAS_TO_MODEL_ID when Anthropic releases new model IDs.
"""
from __future__ import annotations

import json
import os
import socket
import time
import urllib.error
import urllib.request
from urllib.request import Request

from bytedigger_engine.contracts import StepResult
from bytedigger_engine.llm_subprocess import register_backend, _load_effort

# ---------------------------------------------------------------------------
# Module-level constants (§2.1)
# ---------------------------------------------------------------------------

_ENDPOINT = "https://api.anthropic.com/v1/messages"
_API_VERSION = "2023-06-01"
_DEFAULT_MAX_TOKENS = 8192

_ALIAS_TO_MODEL_ID: dict[str, str] = {
    "haiku": "claude-haiku-4-5-20251001",
    "sonnet": "claude-sonnet-4-6",
    "opus": "claude-opus-4-8",
    "fable": "claude-fable-5",
}

# Effort level → Anthropic `thinking.budget_tokens`. Anthropic requires
# budget_tokens >= 1024 and < max_tokens; _build_request_body bumps max_tokens
# to preserve the second invariant. Static defaults (config-override deferred → OFI).
_EFFORT_TO_BUDGET: dict[str, int] = {
    "low": 2048,
    "medium": 8192,
    "high": 16384,
}


# ---------------------------------------------------------------------------
# Named sourceable helper: model alias resolution (§1aa / AC5)
# ---------------------------------------------------------------------------

def _resolve_model_id(model: str) -> str:
    """Resolve a short alias to a canonical Anthropic model ID.

    Resolution order:
    1. Env override HAL_ANTHROPIC_MODEL_<ALIAS_UPPER> (e.g. HAL_ANTHROPIC_MODEL_HAIKU)
    2. _ALIAS_TO_MODEL_ID static map (alias → canonical id)
    3. Pass-through: model startswith 'claude-' → return unchanged
    4. Unknown string → return unchanged (let API reject it)
    """
    alias_lower = model.lower()
    env_key = f"HAL_ANTHROPIC_MODEL_{alias_lower.upper()}"
    env_override = os.environ.get(env_key)
    if env_override:
        return env_override
    mapped = _ALIAS_TO_MODEL_ID.get(alias_lower)
    if mapped is not None:
        return mapped
    # pass-through: full model id or unknown (let API reject)
    return model


# ---------------------------------------------------------------------------
# Named sourceable helpers: reasoning-effort → thinking body (§2.2 / §2.3, GH #329)
# ---------------------------------------------------------------------------

def _resolve_thinking(model, step_name, hard_gate):
    """Return an Anthropic `thinking` param dict for the resolved effort level, or None.

    hard_gate True → None (model-default; parity with claude-subprocess `if not hard_gate`).
    Effort level None / '' / unrecognized → None (inert-by-default → body byte-identical).
    """
    if hard_gate:
        return None
    level = _load_effort(model, step_name)
    budget = _EFFORT_TO_BUDGET.get(level) if level else None
    if budget is None:
        return None
    return {"type": "enabled", "budget_tokens": budget}


def _build_request_body(prompt, model_id, *, model, step_name, hard_gate, stable_prefix: str = ""):
    # GH334 §2.5: order-preserving cache-breakpoint split. Non-empty +
    # substring-of-prompt → ordered content blocks [before, stable_prefix
    # (cache_control:ephemeral), after], omitting empty before/after blocks.
    # Defensive fallback (empty, or not-found): plain-string content,
    # byte-identical to today.
    if stable_prefix and stable_prefix in prompt:
        idx = prompt.index(stable_prefix)
        before = prompt[:idx]
        after = prompt[idx + len(stable_prefix):]
        content: list = []
        if before:
            content.append({"type": "text", "text": before})
        content.append({
            "type": "text",
            "text": stable_prefix,
            "cache_control": {"type": "ephemeral"},
        })
        if after:
            content.append({"type": "text", "text": after})
    else:
        content = prompt
    body = {
        "model": model_id,
        "max_tokens": _DEFAULT_MAX_TOKENS,
        "messages": [{"role": "user", "content": content}],
    }
    thinking = _resolve_thinking(model, step_name, hard_gate)
    if thinking is not None:
        body["thinking"] = thinking
        # Anthropic: max_tokens must exceed budget_tokens; keep output headroom.
        body["max_tokens"] = thinking["budget_tokens"] + _DEFAULT_MAX_TOKENS
    return body


# ---------------------------------------------------------------------------
# Backend handler (§2.1)
# ---------------------------------------------------------------------------

def anthropic_api_backend(
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
    straggler_cfg: object = None,
    idle_timeout_sec: object = None,
    stable_prefix: str = "",
) -> StepResult:
    """Anthropic Messages API backend handler.

    Accepted-and-ignored kwargs: allowed_tools, run_ctx, hard_gate,
    straggler_cfg — text-only, non-agentic, non-streaming.
    """
    # Step 1: API key check
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return StepResult(
            status="error",
            data=None,
            duration_ms=0,
            step_name=step_name,
            error="ANTHROPIC_API_KEY environment variable is not set",
            error_code="E_LLM_API_KEY_MISSING",
            recoverable=False,
        )

    # Step 2: resolve model alias
    model_id = _resolve_model_id(model)

    # Step 3: build request
    body = _build_request_body(
        prompt,
        model_id,
        model=model,
        step_name=step_name,
        hard_gate=hard_gate,
        stable_prefix=stable_prefix,
    )
    headers = {
        "x-api-key": api_key,
        "anthropic-version": _API_VERSION,
        "content-type": "application/json",
    }
    req = Request(
        _ENDPOINT,
        data=json.dumps(body).encode("utf-8"),
        headers=headers,
        method="POST",
    )

    # Step 4–5: POST and parse
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
        with urllib.request.urlopen(req, timeout=effective_timeout) as resp:
            raw_bytes = resp.read()
        text_raw = raw_bytes.decode("utf-8")
        parsed = json.loads(text_raw)
    except urllib.error.HTTPError as e:
        duration_ms = int((time.monotonic() - t0) * 1000)
        return StepResult(
            status="error",
            data=None,
            duration_ms=duration_ms,
            step_name=step_name,
            error=f"HTTP {e.code}: {e.reason}",
            error_code="E_LLM_API_HTTP",
            recoverable=True,
        )
    except (socket.timeout, TimeoutError) as e:
        duration_ms = int((time.monotonic() - t0) * 1000)
        return StepResult(
            status="error",
            data=None,
            duration_ms=duration_ms,
            step_name=step_name,
            error=f"Request timed out: {e}",
            error_code="E_LLM_TIMEOUT",
            recoverable=True,
        )
    except urllib.error.URLError as e:
        duration_ms = int((time.monotonic() - t0) * 1000)
        return StepResult(
            status="error",
            data=None,
            duration_ms=duration_ms,
            step_name=step_name,
            error=f"Network error: {e.reason}",
            error_code="E_LLM_API_NETWORK",
            recoverable=True,
        )

    duration_ms = int((time.monotonic() - t0) * 1000)

    # Step 5: extract text from content blocks
    content_blocks = parsed.get("content", [])
    text_parts = [
        block["text"]
        for block in content_blocks
        if block.get("type") == "text"
    ]
    if not text_parts:
        return StepResult(
            status="error",
            data=None,
            duration_ms=duration_ms,
            step_name=step_name,
            error="API response contained no text content blocks",
            error_code="E_LLM_API_BAD_RESPONSE",
            recoverable=False,
        )

    text = "".join(text_parts)
    response_bytes = len(text.encode("utf-8"))

    # Step 6: build success data — caller extra_data WINS on collision
    base_data: dict = {
        "raw_response": text,
        "response_bytes": response_bytes,
        "command": ["anthropic-api", "messages", model_id],
    }
    if gate_label is not None:
        base_data["gate_label"] = gate_label
    merged: dict = {**base_data, **(extra_data or {})}

    return StepResult(
        status="ok",
        data=merged,
        duration_ms=duration_ms,
        step_name=step_name,
    )


# ---------------------------------------------------------------------------
# Named idempotent self-registration helper (§2.2 / §1aa / AC13)
# ---------------------------------------------------------------------------

def register() -> None:
    """Register 'anthropic-api' backend with llm_subprocess.

    Idempotent (overwrite=True) — safe to call after reset_backends()
    or on repeated module reloads without raising on duplicate.
    """
    register_backend(
        "anthropic-api",
        anthropic_api_backend,
        manifest_source="api_text_response",
        capabilities=frozenset(),
        overwrite=True,
    )


register()  # import side-effect: backend available once the module is imported
