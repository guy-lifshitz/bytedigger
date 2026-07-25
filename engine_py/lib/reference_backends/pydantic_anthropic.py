"""OSS reference backend: Claude subscription (OAuth) via Pydantic AI (#852).

Sibling of `pydantic_openai.py` — same entry contract, same tool set and
git_diff manifest, obtained by importing the private helpers from
`.pydantic_openai` rather than copy-pasting the tool bodies (§1aa/§1f).

Stdlib-only at module top level (`anthropic_oauth` is also stdlib-only);
`pydantic_ai`/`anthropic` imports are lazy, inside the handler. register()
is only invoked at import time if both are importable.
"""
from __future__ import annotations

import importlib.util
import os
import sys
import threading
import time

from contracts import StepResult
from llm_subprocess import register_backend
from package_meta import EXTRA_AGENTIC_PYDANTIC, install_hint

from . import anthropic_oauth
from .anthropic_oauth import OAUTH_BETA_HEADER
from .pydantic_openai import (
    _extract_usage_tokens,
    _is_git_repo,
    _kill_active_procs,
    _manifest_since,
    _pydantic_ai_importable,
    _snapshot_pre_state,
    _tool_bash,
    _tool_edit_file,
    _tool_run_tests,
    _tool_write_file,
)

_PIP_EXTRA_HINT = (
    'pydantic_ai / anthropic is not installed. Install it via: '
    + install_hint(EXTRA_AGENTIC_PYDANTIC)
)


def _anthropic_importable() -> bool:
    if "anthropic" in sys.modules and sys.modules["anthropic"] is not None:
        return True
    try:
        return importlib.util.find_spec("anthropic") is not None
    except (ImportError, ValueError):
        return False


def _deps_importable() -> bool:
    return _pydantic_ai_importable() and _anthropic_importable()


def pydantic_anthropic_backend(
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
    """Claude subscription (OAuth) agentic backend via Pydantic AI."""
    if not _pydantic_ai_importable():
        return StepResult(
            status="error",
            data=None,
            duration_ms=0,
            step_name=step_name,
            error=_PIP_EXTRA_HINT,
            error_code="E_LLM_API_DEPS_MISSING",
            recoverable=False,
        )
    try:
        import anthropic  # noqa: F401
    except Exception:
        return StepResult(
            status="error",
            data=None,
            duration_ms=0,
            step_name=step_name,
            error=_PIP_EXTRA_HINT,
            error_code="E_LLM_API_DEPS_MISSING",
            recoverable=False,
        )

    try:
        token = anthropic_oauth.get_access_token()
    except anthropic_oauth.OAuthCredentialsError as e:
        return StepResult(
            status="error",
            data=None,
            duration_ms=0,
            step_name=step_name,
            error=str(e),
            error_code="E_LLM_API_KEY_MISSING",
            recoverable=False,
        )

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

    from anthropic import AsyncAnthropic
    from pydantic_ai import Agent, UsageLimits
    from pydantic_ai.models.anthropic import AnthropicModel
    from pydantic_ai.providers.anthropic import AnthropicProvider

    client = AsyncAnthropic(auth_token=token, default_headers={"anthropic-beta": OAUTH_BETA_HEADER})
    provider = AnthropicProvider(anthropic_client=client)
    llm_model = AnthropicModel(model, provider=provider)

    agent = Agent(llm_model, tools=[])

    cancel_event = threading.Event()

    @agent.tool_plain  # type: ignore[untyped-decorator]
    def write_file(path: str, content: str) -> str:
        if cancel_event.is_set():
            return "error: run cancelled (timeout)"
        return _tool_write_file(root, path, content)

    @agent.tool_plain  # type: ignore[untyped-decorator]
    def edit_file(path: str, old: str, new: str) -> str:
        if cancel_event.is_set():
            return "error: run cancelled (timeout)"
        return _tool_edit_file(root, path, old, new)

    @agent.tool_plain  # type: ignore[untyped-decorator]
    def run_tests(command: str) -> str:
        if cancel_event.is_set():
            return "error: run cancelled (timeout)"
        return _tool_run_tests(root, command, cancel_event=cancel_event)

    @agent.tool_plain  # type: ignore[untyped-decorator]
    def bash(command: str) -> str:
        if cancel_event.is_set():
            return "error: run cancelled (timeout)"
        return _tool_bash(root, command, cancel_event=cancel_event)

    usage_limits = UsageLimits(request_limit=50)

    pre = _snapshot_pre_state(root)

    t0 = time.monotonic()
    result_holder: dict[str, object] = {}

    def _run() -> None:
        try:
            result_holder["result"] = agent.run_sync(prompt, usage_limits=usage_limits)
        except Exception as e:  # noqa: BLE001
            result_holder["error"] = e

    thread = threading.Thread(target=_run, daemon=True)
    thread.start()
    thread.join(timeout_sec)
    duration_ms = int((time.monotonic() - t0) * 1000)

    if thread.is_alive():
        cancel_event.set()
        _kill_active_procs()
        thread.join(5.0)
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
            error=f"agent run exceeded timeout of {timeout_sec}s",
            error_code="E_LLM_API_TIMEOUT",
            recoverable=True,
        )

    if "error" in result_holder:
        err = result_holder["error"]
        err_name = type(err).__name__
        if "UsageLimitExceeded" in err_name or "usage limit" in str(err).lower():
            return StepResult(
                status="error",
                data=None,
                duration_ms=duration_ms,
                step_name=step_name,
                error=f"usage limit exceeded: {err}",
                error_code="E_LLM_API_BAD_RESPONSE",
                recoverable=True,
            )
        return StepResult(
            status="error",
            data=None,
            duration_ms=duration_ms,
            step_name=step_name,
            error=f"agent run failed: {err}",
            error_code="E_LLM_API_BAD_RESPONSE",
            recoverable=True,
        )

    agent_result = result_holder.get("result")
    raw_response = getattr(agent_result, "output", None)
    if raw_response is None:
        raw_response = getattr(agent_result, "data", None)

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

    tokens_in, tokens_out = _extract_usage_tokens(agent_result)

    base_data: dict[str, object] = {
        "raw_response": raw_response,
        "worker_written_paths": manifest,
        "manifest_source": "git_diff",
        "command": ["pydantic-anthropic", model],
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
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
    """Register 'pydantic-anthropic' backend with llm_subprocess.

    Idempotent (overwrite=True). Raises ImportError with the pip-extra hint
    from `package_meta` if deps are not importable.
    """
    if not _deps_importable():
        raise ImportError(_PIP_EXTRA_HINT)
    register_backend(
        "pydantic-anthropic",
        pydantic_anthropic_backend,
        manifest_source="git_diff",
        capabilities=frozenset(),
        overwrite=True,
    )


if _deps_importable():
    register()  # import side-effect, guarded — only if deps importable
