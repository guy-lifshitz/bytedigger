"""OSS reference backend: warm-session agent via `claude-agent-sdk` (GH885).

Sibling of `pydantic_anthropic.py` — same entry contract, same git_diff
manifest, obtained by importing the shared usage-extraction helper rather
than copy-pasting it (§1aa/§1f).

Stdlib-only at module top level; `claude_agent_sdk` import is lazy, inside
the handler / guarded probes (§1q — no heavy/absent module imported at
module scope).
"""
from __future__ import annotations

import asyncio
import importlib.util
import os
import sys
import time

from contracts import StepResult
from llm_subprocess import register_backend

from .pydantic_openai import _extract_usage_tokens
from .pydantic_openai import _is_git_repo, _manifest_since, _snapshot_pre_state

_PIP_EXTRA_HINT = (
    "claude_agent_sdk is not installed. Install it via: pip install claude-agent-sdk"
)

# ---------------------------------------------------------------------------
# Warm-session cache (§2.3) — module-level, per-process, in-memory
# ---------------------------------------------------------------------------

_SESSION_CACHE: dict[str, tuple[str, int]] = {}


def _session_key(run_ctx: object, step_name: str) -> str | None:
    run_id = getattr(run_ctx, "run_id", None) if run_ctx is not None else None
    if not run_id:
        return None
    step_family = step_name.split(".")[0]
    return f"{run_id}:{step_family}"


def _max_resumes() -> int:
    return int(os.environ.get("HAL_AGENT_SDK_MAX_RESUMES", "8"))


def _should_resume(key: str) -> str | None:
    entry = _SESSION_CACHE.get(key)
    if entry is None:
        return None
    session_id, resume_count = entry
    if resume_count < _max_resumes():
        return session_id
    return None


def _invalidate(key: str) -> None:
    _SESSION_CACHE.pop(key, None)


# ---------------------------------------------------------------------------
# Deps probe
# ---------------------------------------------------------------------------

def _deps_importable() -> bool:
    if "claude_agent_sdk" in sys.modules and sys.modules["claude_agent_sdk"] is not None:
        return True
    try:
        return importlib.util.find_spec("claude_agent_sdk") is not None
    except (ImportError, ValueError):
        return False


def _extract_tokens_from_usage(usage: object) -> tuple[int | None, int | None]:
    """Tolerant token extraction: `usage` may be a dict (real SDK) or an
    attrs-style object (fakes / other SDK versions) — delegate to the
    shared helper for the latter, read dict keys directly for the former.
    """
    if isinstance(usage, dict):
        tokens_in = usage.get("input_tokens")
        tokens_out = usage.get("output_tokens")
        tokens_in = tokens_in if isinstance(tokens_in, int) else None
        tokens_out = tokens_out if isinstance(tokens_out, int) else None
        return tokens_in, tokens_out
    return _extract_usage_tokens(type("_Wrap", (), {"usage": usage})())


# ---------------------------------------------------------------------------
# Backend handler
# ---------------------------------------------------------------------------

def agent_sdk_backend(
    *,
    prompt: str,
    model: str,
    timeout_sec: int | float,
    step_name: str,
    extra_data: dict[str, object] | None = None,
    allowed_tools: object = None,
    run_ctx: object = None,
    hard_gate: bool = False,
    gate_label: str | None = None,
    straggler_cfg: object = None,
    idle_timeout_sec: object = None,
    stable_prefix: str = "",
) -> StepResult:
    """Warm-session agentic backend via `claude-agent-sdk` (resume semantics)."""
    if not _deps_importable():
        return StepResult(
            status="error",
            data=None,
            duration_ms=0,
            step_name=step_name,
            error=_PIP_EXTRA_HINT,
            error_code="E_LLM_API_DEPS_MISSING",
            recoverable=False,
        )

    import claude_agent_sdk

    extra_data = extra_data or {}
    root = extra_data.get("workspace_root") or os.getcwd()
    root = str(root)

    if not _is_git_repo(root):
        return StepResult(
            status="error",
            data=None,
            duration_ms=0,
            step_name=step_name,
            error=f"workspace root is not a git repository: {root}",
            error_code="E_MANIFEST_NO_GIT",
            recoverable=False,
        )

    key = _session_key(run_ctx, step_name)
    resume_sid = _should_resume(key) if key is not None else None

    pre = _snapshot_pre_state(root)
    t0 = time.monotonic()

    async def _run() -> object:
        options = claude_agent_sdk.ClaudeAgentOptions(
            model=model,
            resume=resume_sid,
            allowed_tools=allowed_tools or [],
            permission_mode="bypassPermissions",
            cwd=root,
        )
        result_cls = getattr(claude_agent_sdk, "ResultMessage", None)
        result_msg: object = None
        async for msg in claude_agent_sdk.query(prompt=prompt, options=options):
            if result_cls is not None and isinstance(msg, result_cls):
                result_msg = msg
            elif result_cls is None:
                result_msg = msg
        return result_msg

    try:
        result = asyncio.run(asyncio.wait_for(_run(), timeout_sec))
    except asyncio.TimeoutError:
        duration_ms = int((time.monotonic() - t0) * 1000)
        if key is not None:
            _invalidate(key)
        try:
            manifest = _manifest_since(root, pre)
        except RuntimeError as e:
            return StepResult(
                status="error",
                data=None,
                duration_ms=duration_ms,
                step_name=step_name,
                error=f"manifest ground-truth failed: {e}",
                error_code="E_MANIFEST_NO_GIT",
                recoverable=False,
            )
        return StepResult(
            status="error",
            data={
                "worker_written_paths": manifest,
                "manifest_source": "git_diff",
                "timed_out": True,
            },
            duration_ms=duration_ms,
            step_name=step_name,
            error=f"agent-sdk run exceeded timeout of {timeout_sec}s",
            error_code="E_LLM_API_TIMEOUT",
            recoverable=True,
        )
    except Exception as e:  # noqa: BLE001
        duration_ms = int((time.monotonic() - t0) * 1000)
        if key is not None:
            _invalidate(key)
        return StepResult(
            status="error",
            data=None,
            duration_ms=duration_ms,
            step_name=step_name,
            error=f"agent-sdk run failed: {e}",
            error_code="E_LLM_API_BAD_RESPONSE",
            recoverable=True,
        )

    duration_ms = int((time.monotonic() - t0) * 1000)

    if result is None:
        if key is not None:
            _invalidate(key)
        return StepResult(
            status="error",
            data=None,
            duration_ms=duration_ms,
            step_name=step_name,
            error="agent-sdk run produced no ResultMessage",
            error_code="E_LLM_API_BAD_RESPONSE",
            recoverable=True,
        )

    try:
        manifest = _manifest_since(root, pre)
    except RuntimeError as e:
        return StepResult(
            status="error",
            data=None,
            duration_ms=duration_ms,
            step_name=step_name,
            error=f"manifest ground-truth failed: {e}",
            error_code="E_MANIFEST_NO_GIT",
            recoverable=False,
        )

    session_id = getattr(result, "session_id", None)
    tokens_in, tokens_out = _extract_tokens_from_usage(getattr(result, "usage", None))
    warm_resumed = resume_sid is not None

    if key is not None and session_id:
        if warm_resumed:
            prior = _SESSION_CACHE.get(key)
            prior_count = prior[1] if prior is not None else 0
            _SESSION_CACHE[key] = (session_id, prior_count + 1)
        else:
            _SESSION_CACHE[key] = (session_id, 0)

    raw_response = getattr(result, "result", None)

    base_data: dict[str, object] = {
        "raw_response": raw_response,
        "worker_written_paths": manifest,
        "manifest_source": "git_diff",
        "command": ["agent-sdk", model],
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "warm_resumed": warm_resumed,
    }
    if gate_label is not None:
        base_data["gate_label"] = gate_label

    caller_extra = {k: v for k, v in extra_data.items() if k != "workspace_root"}
    merged = {**base_data, **caller_extra}

    return StepResult(
        status="ok",
        data=merged,
        duration_ms=duration_ms,
        step_name=step_name,
    )


# ---------------------------------------------------------------------------
# Registration
# ---------------------------------------------------------------------------

def register() -> None:
    """Register 'agent-sdk' backend with llm_subprocess.

    Idempotent (overwrite=True). Raises ImportError with the
    `claude-agent-sdk` pip hint if deps are not importable.
    """
    if not _deps_importable():
        raise ImportError(_PIP_EXTRA_HINT)
    register_backend(
        "agent-sdk",
        agent_sdk_backend,
        manifest_source="git_diff",
        capabilities=frozenset(),
        overwrite=True,
    )


if _deps_importable():
    register()  # import side-effect, guarded — only if deps importable
