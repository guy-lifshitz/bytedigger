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
import json
import os
import sys
import time
import urllib.request
from collections import deque

from contracts import StepResult
from llm_subprocess import register_backend, _emit_safe
from telemetry_ctx import _RunCtx

from .pydantic_openai import _extract_usage_tokens
from .pydantic_openai import _is_git_repo, _manifest_since, _snapshot_pre_state

_PIP_EXTRA_HINT = (
    "claude_agent_sdk is not installed. Install it via: pip install claude-agent-sdk"
)

# ---------------------------------------------------------------------------
# Warm-session cache (§2.3) — module-level, per-process, in-memory
# ---------------------------------------------------------------------------

_SESSION_CACHE: dict[str, tuple[str, int]] = {}


def _session_key(run_ctx: _RunCtx | None, step_name: str) -> str | None:
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
# GH956: salvage a success ResultMessage received before a trailing stream
# error (CLI exits non-zero AFTER emitting its result envelope; the SDK's
# message-reader then raises out of the async-for loop).
# ---------------------------------------------------------------------------

def _salvage_success_result(holder: dict[str, object]) -> object | None:
    """Return the already-received success ResultMessage, else None.

    Salvageable iff a ResultMessage arrived AND its `is_error` is falsy
    (missing attr == falsy). is_error=True or no message → None.
    """
    msg = holder.get("msg")
    if msg is None or getattr(msg, "is_error", False):
        return None
    return msg


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


def _ledger_usage_fields(usage: object) -> dict[str, int | None]:
    """Tolerant extraction of ledger-relevant usage fields (§1aa).

    Supports dict usage (real SDK) and attrs-style objects (fakes / other
    SDK versions). Missing/non-int values become None. `usage is None`
    yields all-None. Never raises.
    """
    keys = (
        "tokens_in",
        "tokens_out",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
    )
    source_keys = (
        "input_tokens",
        "output_tokens",
        "cache_creation_input_tokens",
        "cache_read_input_tokens",
    )
    if usage is None:
        return {k: None for k in keys}
    if isinstance(usage, dict):
        values = [usage.get(sk) for sk in source_keys]
    else:
        values = [getattr(usage, sk, None) for sk in source_keys]
    return {
        k: (v if isinstance(v, int) else None)
        for k, v in zip(keys, values)
    }


# ---------------------------------------------------------------------------
# GH933: stderr-tail capture + external-outage classification (fail-open)
# ---------------------------------------------------------------------------

_STDERR_TAIL_BYTES = 4096


def _stderr_tail_lines() -> int:
    return int(os.environ.get("HAL_AGENT_SDK_STDERR_TAIL_LINES", "50"))


def _stderr_tail(buf: "deque[str]", exc: BaseException | None = None) -> str:
    """Duck-typed stderr tail: prefer a non-empty `exc.stderr` str, else join
    the buffered callback lines. Cap to the last `_STDERR_TAIL_BYTES` bytes,
    utf-8-safe.
    """
    exc_stderr = getattr(exc, "stderr", None) if exc is not None else None
    if isinstance(exc_stderr, str) and exc_stderr:
        text = exc_stderr
    else:
        text = "\n".join(buf)
    return text.encode("utf-8")[-_STDERR_TAIL_BYTES:].decode("utf-8", errors="replace")


def _external_outage_indicator() -> str | None:
    """Fail-open probe against the public status page. Never raises."""
    if os.environ.get("HAL_OUTAGE_PROBE", "1") == "0":
        return None
    url = os.environ.get(
        "HAL_STATUS_PROBE_URL", "https://status.claude.com/api/v2/status.json"
    )
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:  # noqa: S310
            payload = json.loads(resp.read())
        indicator = payload.get("status", {}).get("indicator")
    except Exception:  # noqa: BLE001
        return None
    if isinstance(indicator, str) and indicator != "none":
        return indicator
    return None


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
    run_ctx: _RunCtx | None = None,
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

    stderr_buf: "deque[str]" = deque(maxlen=_stderr_tail_lines())
    result_holder: dict[str, object] = {"msg": None}

    def _on_stderr(line: str) -> None:
        stderr_buf.append(line)

    async def _run() -> object:
        # GH933: try with stderr callback (new SDK); fall back if the
        # options constructor rejects the kwarg (old SDK, duck-typed).
        try:
            options = claude_agent_sdk.ClaudeAgentOptions(
                model=model,
                resume=resume_sid,
                allowed_tools=allowed_tools or [],
                permission_mode="bypassPermissions",
                cwd=root,
                stderr=_on_stderr,
            )
        except TypeError:
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
                result_holder["msg"] = msg
            elif result_cls is None:
                result_msg = msg
                result_holder["msg"] = msg
        return result_msg

    salvage_error: str | None = None
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
        timeout_data: dict[str, object] = {
            "worker_written_paths": manifest,
            "manifest_source": "git_diff",
            "timed_out": True,
            "stderr_tail": _stderr_tail(stderr_buf),
        }
        timeout_error = f"agent-sdk run exceeded timeout of {timeout_sec}s"
        ind = _external_outage_indicator()
        if ind is not None:
            timeout_data["external_outage"] = True
            timeout_data["outage_indicator"] = ind
            timeout_error += f" [external_outage: {ind}]"
        return StepResult(
            status="error",
            data=timeout_data,
            duration_ms=duration_ms,
            step_name=step_name,
            error=timeout_error,
            error_code="E_LLM_API_TIMEOUT",
            recoverable=True,
        )
    except Exception as e:  # noqa: BLE001
        # GH956: a successful ResultMessage may have already arrived before
        # the CLI's trailing stream error; try to salvage it first.
        salvaged = _salvage_success_result(result_holder)
        if salvaged is not None:
            result = salvaged
            salvage_error = f"{e}"
            # Do NOT invalidate the session cache on the salvage path — fall
            # through to the normal success tail below.
        else:
            duration_ms = int((time.monotonic() - t0) * 1000)
            if key is not None:
                _invalidate(key)
            tail = _stderr_tail(stderr_buf, e)
            error = f"agent-sdk run failed: {e}"
            if tail:
                error += f"; stderr tail: {tail}"
            exc_data: dict[str, object] = {"stderr_tail": tail}
            ind = _external_outage_indicator()
            if ind is not None:
                exc_data["external_outage"] = True
                exc_data["outage_indicator"] = ind
                error += f" [external_outage: {ind}]"
            return StepResult(
                status="error",
                data=exc_data,
                duration_ms=duration_ms,
                step_name=step_name,
                error=error,
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
    usage_obj = getattr(result, "usage", None)
    tokens_in, tokens_out = _extract_tokens_from_usage(usage_obj)
    ledger_usage = _ledger_usage_fields(usage_obj)
    cache_creation_input_tokens = ledger_usage["cache_creation_input_tokens"]
    cache_read_input_tokens = ledger_usage["cache_read_input_tokens"]
    cost = getattr(result, "total_cost_usd", None)
    cost_usd = float(cost) if isinstance(cost, (int, float)) else None
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
        "cost_usd": cost_usd,
        "cache_creation_input_tokens": cache_creation_input_tokens,
        "cache_read_input_tokens": cache_read_input_tokens,
    }
    if gate_label is not None:
        base_data["gate_label"] = gate_label
    if salvage_error is not None:
        base_data["salvaged_stream_error"] = salvage_error

    caller_extra = {k: v for k, v in extra_data.items() if k != "workspace_root"}
    merged = {**base_data, **caller_extra}

    if run_ctx is not None and getattr(run_ctx, "event_log", None) is not None:
        payload: dict[str, object] = {
            "backend": "agent-sdk",
            "outcome": "success",
            "duration_ms": duration_ms,
            "response_size_bytes": (
                len(raw_response.encode("utf-8"))
                if isinstance(raw_response, str)
                else 0
            ),
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "cache_creation_input_tokens": cache_creation_input_tokens,
            "cache_read_input_tokens": cache_read_input_tokens,
            "cost_usd": cost_usd,
            "model": model,
            "session_id": session_id,
            "warm_resumed": warm_resumed,
            "phase": getattr(run_ctx, "phase", None),
            "step_name": getattr(run_ctx, "step_name", None) or step_name,
        }
        if getattr(run_ctx, "cycle", None) is not None:
            payload["cycle"] = run_ctx.cycle
        if salvage_error is not None:
            payload["salvaged"] = True
        _emit_safe(
            run_ctx.event_log,
            "runner_result_consumed",
            payload,
            getattr(run_ctx, "run_id", ""),
        )

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
