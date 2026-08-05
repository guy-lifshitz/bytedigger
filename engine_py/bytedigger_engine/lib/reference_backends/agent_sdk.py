"""OSS reference backend: warm-session agent via `claude-agent-sdk` (GH885).

Sibling of `pydantic_anthropic.py` — same entry contract, same git_diff
manifest, obtained by importing the shared usage-extraction helper rather
than copy-pasting it (§1aa/§1f).

Stdlib-only at module top level; `claude_agent_sdk` import is lazy, inside
the handler / guarded probes (§1q — no heavy/absent module imported at
module scope).

GH1169 — per-attempt IDLE-GAP watchdog (opt-in, disabled by default):
    ``HAL_AGENT_SDK_IDLE_TIMEOUT_SEC`` bounds the GAP BETWEEN yielded
    messages from ``claude_agent_sdk.query(...)`` — never the total attempt
    duration. A stream that keeps yielding never trips it, regardless of how
    long it runs; only silence (the SDK accepting a request and never
    yielding again) trips it. Default is ``"0"`` -> ``0.0`` -> DISABLED: the
    knob is opt-in, and an empty/whitespace value is treated identically to
    unset (never a crash). The watchdog only arms while the remaining outer
    budget can still hold a full idle gap plus a small epsilon
    (``remaining_outer >= idle + _OUTER_TIE_EPSILON_SEC``); a value at or
    above the step's ``timeout_sec`` budget never arms at all. On a fire, the
    in-flight attempt is retried (same attempt/backoff budget as other
    retryable failures) unless the remaining outer budget is at/below
    ``_HANG_RETRY_FLOOR_SEC``, in which case the existing outer-timeout path
    takes over. Recommended first armed lane: ``implement.red`` at ``900``
    (see the spec's §2.4 arming arithmetic for the reasoning). No default
    value is shipped ON until a live-replay baseline of healthy inter-message
    gaps exists — an unmeasured default risks cancelling legitimate long
    turns (lost work, doubled step budget via the one-shot fallback below).
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import random
import re
import sys
import time
import urllib.request
from collections import deque

from bytedigger_engine.contracts import StepResult
from bytedigger_engine.llm_subprocess import register_backend, _emit_safe
from bytedigger_engine.telemetry_ctx import _RunCtx

from .pydantic_openai import _extract_usage_tokens
from .pydantic_openai import _is_git_repo, _manifest_since, _snapshot_pre_state

_PIP_EXTRA_HINT = (
    "claude_agent_sdk is not installed. Install it via: python3 -m pip install claude-agent-sdk"
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

def _accumulate_observations(msg: object, holder: dict[str, object]) -> None:
    """bd#71: harvest tool names and the dispatched model from one SDK message.

    Defensive by construction — this runs on EVERY message of a live stream and
    must never be able to break the run it observes. Any shape it does not
    recognise is skipped silently; telemetry is not allowed to cost a dispatch.

    The model is taken from what the stream REPORTED, never from what was
    requested: writing the requested value back would have R3.3 compare the pin
    with itself, and the check could never fail.
    """
    content = getattr(msg, "content", None)
    if isinstance(content, list):
        tools = holder["tools"]
        if isinstance(tools, set):
            for block in content:
                if getattr(block, "type", None) != "tool_use":
                    continue
                name = getattr(block, "name", None)
                if isinstance(name, str) and name:
                    tools.add(name)
    model = getattr(msg, "model", None)
    if isinstance(model, str) and model:
        holder["model"] = model


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
# GH1157 Fix#1: retry classification + bounded jittered exponential backoff
# ---------------------------------------------------------------------------

_RETRYABLE_STATUS_CODES = frozenset({429, 503, 529})
_RETRYABLE_TEXT_RE = re.compile(
    r"(\b429\b|\b503\b|\b529\b|rate.?limit|too many requests|overloaded"
    r"|ECONNRESET|EPIPE|ETIMEDOUT|connection reset|socket hang up"
    r"|connection closed|server disconnected)",
    re.IGNORECASE,
)


def _is_retryable_agent_sdk_failure(exc: BaseException | None, stderr_text: str) -> bool:
    """Retryable iff a duck-typed 429/503/529 status_code, or the exception
    text / stderr tail matches a known transient-failure signature. Never
    called with asyncio.TimeoutError/CancelledError — timeout is handled
    upstream and is never retried."""
    if exc is not None and getattr(exc, "status_code", None) in _RETRYABLE_STATUS_CODES:
        return True
    haystack = f"{exc if exc is not None else ''}\n{stderr_text}"
    return bool(_RETRYABLE_TEXT_RE.search(haystack))


def _max_sdk_attempts() -> int:
    val = int(os.environ.get("HAL_AGENT_SDK_MAX_ATTEMPTS", "3"))
    return max(1, val)


def _backoff_base_sec() -> float:
    return float(os.environ.get("HAL_AGENT_SDK_BACKOFF_BASE_SEC", "1.0"))


def _backoff_cap_sec() -> float:
    return float(os.environ.get("HAL_AGENT_SDK_BACKOFF_CAP_SEC", "8.0"))


def _backoff_delay_sec(attempt: int) -> float:
    """attempt = 0-based count of completed failures."""
    base = _backoff_base_sec()
    cap = _backoff_cap_sec()
    raw: float = min(cap, base * (2 ** attempt))
    return float(raw * (0.5 + random.random() * 0.5))


# ---------------------------------------------------------------------------
# GH1169: per-attempt IDLE-GAP watchdog — opt-in, disabled by default.
# ---------------------------------------------------------------------------

_OUTER_TIE_EPSILON_SEC = 0.25
_HANG_RETRY_FLOOR_SEC = 5.0


def _sdk_idle_timeout_sec() -> float:
    """Max seconds of SILENCE between messages; ``0`` (default) = disabled.

    An empty or all-whitespace value is treated identically to unset (0.0,
    never a crash) — the `.strip() or "0"` pair is normative, not cosmetic
    (§2.1.1): a bare `os.environ.get(..., "0")` returns `""` for an empty
    export, and `float("")` raises inside the attempt loop, misclassifying
    the whole invoke as non-retryable. Genuinely non-numeric garbage still
    raises deliberately (idiom parity with `_max_sdk_attempts`).
    """
    return float((os.environ.get("HAL_AGENT_SDK_IDLE_TIMEOUT_SEC") or "0").strip() or "0")


def _remaining_outer_budget(t0: float, outer_timeout_sec: float) -> float:
    """Seconds left of the outer ``timeout_sec`` budget, run-anchored at ``t0``."""
    return outer_timeout_sec - (time.monotonic() - t0)


def _effective_idle_gap(idle_timeout: float, t0: float, outer_timeout_sec: float) -> float:
    """Idle gap clamped to what the remaining outer budget can still hold.

    Returns ``min(idle_timeout, _remaining_outer_budget(t0, outer_timeout_sec)
    - _OUTER_TIE_EPSILON_SEC)`` — the epsilon guarantees the inner timer can
    never expire at the same monotonic instant as the outer one. Whether a
    timer is armed at all is decided separately (§2.1.3's three-way
    conjunction); a clamped return value here means the caller must NOT arm.
    """
    return min(idle_timeout, _remaining_outer_budget(t0, outer_timeout_sec) - _OUTER_TIE_EPSILON_SEC)


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
    # bd#71: the observation channel BD-L3 reads. The stream carries both
    # `ToolUseBlock.name` and `AssistantMessage.model`, and this loop used to
    # keep only the ResultMessage — the evidence passed through and was
    # dropped, so R3.3/R3.5/R3.6 were silent on the DEFAULT backend.
    observed_holder: dict[str, object] = {"tools": set(), "model": None}

    def _on_stderr(line: str) -> None:
        stderr_buf.append(line)

    attempt_state: dict[str, object] = {"attempts": 0, "hang_attempts": 0}
    resume_used: dict[str, object] = {"value": None}
    salvage_error: str | None = None
    hang_attempts: int = 0  # typed counter (GH1169) — attempt_state["hang_attempts"] mirrors it

    def _emit_retry(attempt: int, reason: str, delay: float) -> None:
        if run_ctx is not None and getattr(run_ctx, "event_log", None) is not None:
            _emit_safe(
                run_ctx.event_log,
                "agent_sdk_retry",
                {
                    "backend": "agent-sdk",
                    "attempt": attempt,
                    "max_attempts": _max_sdk_attempts(),
                    "delay_sec": delay,
                    "reason": reason,
                    "classified_retryable": True,
                    "model": model,
                    "step_name": getattr(run_ctx, "step_name", None) or step_name,
                },
                getattr(run_ctx, "run_id", ""),
            )

    async def _run() -> object:
        nonlocal salvage_error, hang_attempts
        attempt = 0
        while True:
            attempt += 1
            attempt_state["attempts"] = attempt
            result_holder["msg"] = None
            # Reset with its sibling: observations from a failed attempt must
            # not leak into the next one.
            observed_holder["tools"] = set()
            observed_holder["model"] = None
            resume_this = resume_sid if attempt == 1 else None
            resume_used["value"] = resume_this
            if attempt > 1 and key is not None:
                _invalidate(key)

            # GH933: try with stderr callback (new SDK); fall back if the
            # options constructor rejects the kwarg (old SDK, duck-typed).
            try:
                options = claude_agent_sdk.ClaudeAgentOptions(
                    model=model,
                    resume=resume_this,
                    allowed_tools=allowed_tools or [],
                    permission_mode="bypassPermissions",
                    cwd=root,
                    stderr=_on_stderr,
                )
            except TypeError:
                options = claude_agent_sdk.ClaudeAgentOptions(
                    model=model,
                    resume=resume_this,
                    allowed_tools=allowed_tools or [],
                    permission_mode="bypassPermissions",
                    cwd=root,
                )
            result_cls = getattr(claude_agent_sdk, "ResultMessage", None)
            result_msg: object = None
            agen = claude_agent_sdk.query(prompt=prompt, options=options)
            it = agen.__aiter__()
            try:
                while True:
                    # GH1169: idle-gap watchdog — evaluate the clamped gap
                    # UNCONDITIONALLY, every message, before testing whether
                    # to arm (disabled case short-circuits the DECISION only,
                    # never the helper call — §2.1.3/D14).
                    idle = _sdk_idle_timeout_sec()
                    effective = _effective_idle_gap(idle, t0, timeout_sec)
                    try:
                        if idle > 0 and effective > 0 and effective >= idle:
                            msg = await asyncio.wait_for(it.__anext__(), effective)
                        else:
                            msg = await it.__anext__()
                    except StopAsyncIteration:
                        # Normal stream end — loop control, not an error.
                        break
                    _accumulate_observations(msg, observed_holder)
                    if result_cls is not None and isinstance(msg, result_cls):
                        result_msg = msg
                        result_holder["msg"] = msg
                    elif result_cls is None:
                        result_msg = msg
                        result_holder["msg"] = msg
            except asyncio.TimeoutError:
                # GH1169: the per-message idle-gap timer fired (silence
                # between messages) — distinct from the outer wait_for's
                # total-budget expiry. Salvage precedence first (GH956 rule,
                # §2.1.4b): a success already received wins over retry, and
                # does NOT count as a hang.
                try:
                    await agen.aclose()
                except Exception as close_err:  # noqa: BLE001
                    # Best-effort cleanup only — never let it change control
                    # flow. Recorded (not swallowed) via the same stderr-tail
                    # buffer `_stderr_tail` already surfaces in the result.
                    stderr_buf.append(
                        f"[GH1169] agen.aclose() after idle-gap fire failed: {close_err}"
                    )
                salvaged = _salvage_success_result(result_holder)
                if salvaged is not None:
                    return salvaged
                hang_attempts += 1
                attempt_state["hang_attempts"] = hang_attempts
                max_attempts = _max_sdk_attempts()
                if (
                    attempt < max_attempts
                    and _remaining_outer_budget(t0, timeout_sec) > _HANG_RETRY_FLOOR_SEC
                ):
                    delay = _backoff_delay_sec(attempt - 1)
                    _emit_retry(attempt, "idle_gap_timeout", delay)
                    await asyncio.sleep(delay)
                    continue
                raise
            except Exception as e:  # noqa: BLE001
                # GH956: a successful ResultMessage may have already arrived
                # before the CLI's trailing stream error; salvage wins over
                # retry — a success is a success, do not retry after it.
                salvaged = _salvage_success_result(result_holder)
                if salvaged is not None:
                    salvage_error = f"{e}"
                    return salvaged
                stderr_text = "\n".join(stderr_buf)
                max_attempts = _max_sdk_attempts()
                if _is_retryable_agent_sdk_failure(e, stderr_text) and attempt < max_attempts:
                    delay = _backoff_delay_sec(attempt - 1)
                    _emit_retry(attempt, "exception", delay)
                    await asyncio.sleep(delay)
                    continue
                raise

            if result_msg is not None:
                return result_msg

            # Clean exit, no ResultMessage (silent death) — classify for retry.
            stderr_text = "\n".join(stderr_buf)
            max_attempts = _max_sdk_attempts()
            if _is_retryable_agent_sdk_failure(None, stderr_text) and attempt < max_attempts:
                delay = _backoff_delay_sec(attempt - 1)
                _emit_retry(attempt, "silent_no_result", delay)
                await asyncio.sleep(delay)
                continue
            return None

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
            "retry_attempts": attempt_state["attempts"],
            "hang_attempts": attempt_state["hang_attempts"],
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
        # Salvage (if any) is already handled inside _run(); an exception
        # escaping here means non-retryable or retry-budget exhausted.
        duration_ms = int((time.monotonic() - t0) * 1000)
        if key is not None:
            _invalidate(key)
        tail = _stderr_tail(stderr_buf, e)
        error = f"agent-sdk run failed: {e}"
        if tail:
            error += f"; stderr tail: {tail}"
        exc_data: dict[str, object] = {
            "stderr_tail": tail,
            "exit_code": getattr(e, "exit_code", None),
            "retry_attempts": attempt_state["attempts"],
        }
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
        no_result_data: dict[str, object] = {
            "stderr_tail": _stderr_tail(stderr_buf),
            "exit_code": 0,
            "retry_attempts": attempt_state["attempts"],
            "silent_no_result": True,
        }
        no_result_error = "agent-sdk run produced no ResultMessage"
        ind = _external_outage_indicator()
        if ind is not None:
            no_result_data["external_outage"] = True
            no_result_data["outage_indicator"] = ind
            no_result_error += f" [external_outage: {ind}]"
        return StepResult(
            status="error",
            data=no_result_data,
            duration_ms=duration_ms,
            step_name=step_name,
            error=no_result_error,
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
    warm_resumed = resume_used["value"] is not None

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
        # bd#71: BD-L3's observation channel, from the stream this backend
        # already reads. Success path only, like its siblings.
        "observed_tools": sorted(observed_holder["tools"])
        if isinstance(observed_holder["tools"], set) else [],
        "observed_model": observed_holder["model"],
        "manifest_source": "git_diff",
        "command": ["agent-sdk", model],
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "warm_resumed": warm_resumed,
        "cost_usd": cost_usd,
        "cache_creation_input_tokens": cache_creation_input_tokens,
        "cache_read_input_tokens": cache_read_input_tokens,
        "retry_attempts": attempt_state["attempts"],
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
