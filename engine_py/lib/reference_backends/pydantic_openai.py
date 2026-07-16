"""OSS reference backend: provider-agnostic agentic backend via Pydantic AI (#835).

Lazy-imports pydantic_ai — the module itself imports cleanly with zero
non-stdlib deps. register() is only invoked at import time if pydantic_ai
is importable (probed via importlib.util.find_spec, no hard import); an
explicit register() call without pydantic_ai raises a clear ImportError
pointing at the `agentic-pydantic` pip extra.

Agentic phases (write files + run tests) run on any OpenAI-compatible
provider (incl. Azure GPT) via Pydantic AI v2, without the claude CLI.

Status: EXPERIMENTAL (#840 hardening). Manifest is a pre-state-aware git
diff (`_snapshot_pre_state` / `_manifest_since`) — ground truth, never
model self-report, and resistant to the model hiding writes behind a
`git add && git commit` (diff vs the pre-run HEAD survives commits).

`bash`/`run_tests` execute through an argv0 allowlist by default
(`_BASH_ALLOWLIST`), with literal argv (shell=False) so shell metacharacters
are inert. Set `HAL_AGENTIC_BASH_UNRESTRICTED=1` to opt back into legacy
unrestricted `shell=True` execution (trusted workspace only — this is a
guardrail against accidental damage, not a security boundary).

Git ground-truth helpers (`_is_git_repo`, `_porcelain_entries` /
`_git_changed_paths`, `_snapshot_pre_state` incl. `hash-object`/`rev-parse`,
`_manifest_since`'s `diff`/`ls-files`) are pinned to the *real*
`subprocess.Popen` captured at import time (`_REAL_POPEN`) via `_git_run` —
this is anti-gaming: even if something (a test double, a compromised tool
call) tampers with `subprocess.Popen` at runtime, the manifest ground truth
cannot be faked. Only `_run_shell` (the agent's own bash/run_tests tool
execution) does a late-bound `subprocess.Popen` lookup, so a test harness's
fake Popen can still intercept the agent's own shell commands as intended.
"""
from __future__ import annotations

import importlib.util
import os
import re
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path

from contracts import StepResult
from llm_subprocess import register_backend

# Pinned at import time — git ground-truth helpers must never be fooled by
# runtime tampering with subprocess.Popen (anti-gaming, see module docstring).
_REAL_POPEN = subprocess.Popen

_PIP_EXTRA_HINT = (
    "pydantic_ai is not installed. Install it via: "
    "pip install bytedigger-engine[agentic-pydantic]"
)

_MAX_TOOL_OUTPUT = 8000

_EMPTY_TREE_OID = "4b825dc642cb6eb9a060e54bf8d69288fbee4904"
_ABSENT_SENTINEL = "<absent>"

# ---------------------------------------------------------------------------
# Scope 1: bash/run_tests allowlist + opt-in unrestricted escape hatch
# ---------------------------------------------------------------------------

_BASH_ALLOWLIST: frozenset[str] = frozenset({
    "pytest", "python", "python3", "ls", "cat", "head", "tail", "grep",
    "find", "echo", "wc", "diff", "mkdir", "touch",
})
_UNRESTRICTED_ENV = "HAL_AGENTIC_BASH_UNRESTRICTED"

_DANGEROUS_ENV_NAMES = {"LD_PRELOAD", "LD_LIBRARY_PATH"}
_DANGEROUS_ENV_PREFIX = "DYLD_"

_ENV_ASSIGNMENT_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)=(.*)$")

# Scope 3: module-level active-proc registry, killed on timeout/cancel.
_ACTIVE_PROCS: set[subprocess.Popen[str]] = set()
_ACTIVE_PROCS_LOCK = threading.Lock()


def _extract_usage_tokens(agent_result: object) -> tuple[int | None, int | None]:
    """Extract (tokens_in, tokens_out) from a pydantic-ai RunResult.

    Reads `agent_result.usage`, which may be a callable (pydantic-ai <2.10)
    or a property returning a RunUsage instance directly (pydantic-ai
    >=2.10). Reads v2 names `input_tokens`/`output_tokens`, falling back
    to v1 `request_tokens`/`response_tokens`. Any failure (raise, missing
    attrs, non-int values) yields (None, None) for the missing side.
    """
    try:
        usage_attr = getattr(agent_result, "usage", None)
    except Exception:
        return None, None
    if usage_attr is None:
        return None, None
    if callable(usage_attr):
        try:
            usage = usage_attr()
        except Exception:
            return None, None
    else:
        usage = usage_attr  # pydantic-ai >=2.10 property form: RunUsage object

    tokens_in = getattr(usage, "input_tokens", None)
    tokens_out = getattr(usage, "output_tokens", None)
    if tokens_in is None:
        tokens_in = getattr(usage, "request_tokens", None)
    if tokens_out is None:
        tokens_out = getattr(usage, "response_tokens", None)

    if not isinstance(tokens_in, int) or isinstance(tokens_in, bool):
        tokens_in = None
    if not isinstance(tokens_out, int) or isinstance(tokens_out, bool):
        tokens_out = None

    return tokens_in, tokens_out


def _pydantic_ai_importable() -> bool:
    """Probe whether `pydantic_ai` is importable, without a hard import.

    Checks `sys.modules` first (covers test-injected fake modules that
    lack `__spec__`, which would otherwise make `find_spec` raise
    ValueError), then falls back to `importlib.util.find_spec`.
    """
    if "pydantic_ai" in sys.modules and sys.modules["pydantic_ai"] is not None:
        return True
    try:
        return importlib.util.find_spec("pydantic_ai") is not None
    except (ImportError, ValueError):
        return False


# ---------------------------------------------------------------------------
# Workspace-confined local tool bodies (module-level helpers, testable)
# ---------------------------------------------------------------------------

def _resolve_confined(root: str, path: str) -> Path | None:
    root_p = Path(root).resolve()
    candidate = (root_p / path).resolve()
    try:
        candidate.relative_to(root_p)
    except ValueError:
        return None
    return candidate


def _tool_write_file(root: str, path: str, content: str) -> str:
    """Create/overwrite `path` (relative to `root`) with `content`.

    Refuses to write outside `root` — returns an error string, no exception,
    no write.
    """
    target = _resolve_confined(root, path)
    if target is None:
        return f"error: path {path!r} escapes workspace root"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    return f"wrote {path}"


def _tool_edit_file(root: str, path: str, old: str, new: str) -> str:
    """Exact-match single-occurrence replace of `old` with `new` in `path`.

    Returns an error string (file unchanged) on 0 or >1 matches.
    """
    target = _resolve_confined(root, path)
    if target is None:
        return f"error: path {path!r} escapes workspace root"
    if not target.exists():
        return f"error: file {path!r} does not exist"
    text = target.read_text()
    count = text.count(old)
    if count == 0:
        return f"error: no match for old text in {path!r}"
    if count > 1:
        return f"error: {count} matches for old text in {path!r}; must be exactly 1"
    target.write_text(text.replace(old, new, 1))
    return f"edited {path}"


def _register_proc(proc: subprocess.Popen[str]) -> None:
    with _ACTIVE_PROCS_LOCK:
        _ACTIVE_PROCS.add(proc)


def _deregister_proc(proc: subprocess.Popen[str]) -> None:
    with _ACTIVE_PROCS_LOCK:
        _ACTIVE_PROCS.discard(proc)


def _kill_active_procs() -> None:
    with _ACTIVE_PROCS_LOCK:
        procs = list(_ACTIVE_PROCS)
    for p in procs:
        try:  # nosemgrep: hal-silent-except-pass — best-effort kill/reap of dying proc
            p.kill()
        except Exception:  # pragma: no cover - defensive
            pass


def _wait_for_proc(
    proc: subprocess.Popen[str],
    cancel_event: threading.Event | None,
    timeout: float,
) -> str:
    """poll()-based wait loop (NOT communicate(timeout=)) — kills on cancel."""
    start = time.monotonic()
    while True:
        rc = proc.poll()
        if rc is not None:
            break
        if cancel_event is not None and cancel_event.is_set():
            try:  # nosemgrep: hal-silent-except-pass — best-effort kill/reap of dying proc
                proc.kill()
            except Exception:  # pragma: no cover - defensive
                pass
            break
        if time.monotonic() - start > timeout:
            try:  # nosemgrep: hal-silent-except-pass — best-effort kill/reap of dying proc
                proc.kill()
            except Exception:  # pragma: no cover - defensive
                pass
            break
        time.sleep(0.05)

    stdout, stderr = "", ""
    try:  # nosemgrep: hal-silent-except-pass — best-effort kill/reap of dying proc
        out = proc.communicate(timeout=5)
        if isinstance(out, tuple) and len(out) == 2:
            stdout, stderr = out
    except Exception:  # pragma: no cover - defensive
        pass
    output = (stdout or "") + (stderr or "")
    tail = output[-_MAX_TOOL_OUTPUT:]
    rc = getattr(proc, "returncode", None)
    return f"{tail}\n[exit code: {rc}]"


def _run_shell(
    root: str, command: str, *, cancel_event: threading.Event | None = None
) -> str:
    if cancel_event is not None and cancel_event.is_set():
        return "error: run cancelled (timeout)"

    unrestricted = os.environ.get(_UNRESTRICTED_ENV) == "1"

    if unrestricted:
        try:
            proc = subprocess.Popen(  # bounded-spawn: allow # nosemgrep: hal-subprocess-shell-true — explicit opt-in via HAL_AGENTIC_BASH_UNRESTRICTED=1, trusted workspace only
                command,
                shell=True,
                cwd=root,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except Exception as e:  # pragma: no cover - defensive
            return f"error: {e}"
        _register_proc(proc)
        try:
            return _wait_for_proc(proc, cancel_event, timeout=600)
        finally:
            _deregister_proc(proc)

    # Restricted default: parse argv, gate on allowlisted argv0, shell=False.
    try:
        argv = shlex.split(command)
    except ValueError as e:
        return f"error: could not parse command: {e}"
    if not argv:
        return "error: empty command"

    env_prefix: dict[str, str] = {}
    idx = 0
    while idx < len(argv):
        m = _ENV_ASSIGNMENT_RE.match(argv[idx])
        if not m:
            break
        name, value = m.group(1), m.group(2)
        if name not in _DANGEROUS_ENV_NAMES and not name.startswith(_DANGEROUS_ENV_PREFIX):
            env_prefix[name] = value
        idx += 1

    remaining = argv[idx:]
    if not remaining:
        return "error: empty command after env-prefix parsing"

    argv0 = remaining[0]
    basename = os.path.basename(argv0)
    if basename not in _BASH_ALLOWLIST:
        return (
            f"error: command '{argv0}' not in agentic allowlist; set "
            f"{_UNRESTRICTED_ENV}=1 for unrestricted shell (trusted workspace only)"
        )

    run_env = {**os.environ, **env_prefix}
    try:
        proc = subprocess.Popen(  # bounded-spawn: allow
            remaining,
            shell=False,
            cwd=root,
            env=run_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except Exception as e:  # pragma: no cover - defensive
        return f"error: {e}"
    _register_proc(proc)
    try:
        return _wait_for_proc(proc, cancel_event, timeout=600)
    finally:
        _deregister_proc(proc)


def _tool_run_tests(
    root: str, command: str, *, cancel_event: threading.Event | None = None
) -> str:
    return _run_shell(root, command, cancel_event=cancel_event)


def _tool_bash(
    root: str, command: str, *, cancel_event: threading.Event | None = None
) -> str:
    return _run_shell(root, command, cancel_event=cancel_event)


# ---------------------------------------------------------------------------
# Scope 2: pre-state-aware git-diff manifest (anti-gaming)
# ---------------------------------------------------------------------------

def _git_run(argv: list[str]) -> tuple[int, str]:
    """Spawn a git ground-truth subprocess via the import-time-pinned real
    Popen (`_REAL_POPEN`) — never via the (possibly test-tampered) module
    attribute `subprocess.Popen`. Returns (returncode, stdout).

    `communicate()` with text=True is used rather than a manual read loop;
    NUL bytes embedded in stdout (from `-z` output) survive intact in the
    decoded str (NUL is not a valid line-ending / decoding boundary in
    UTF-8/locale text mode), so NUL-separated parsing downstream is safe.

    Bounded with a 60s timeout (git is local — this should never approach
    that in practice); on timeout the process is killed and a RuntimeError
    is raised rather than hanging the ground-truth helper indefinitely.
    """
    proc = _REAL_POPEN(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        stdout, _stderr = proc.communicate(timeout=60)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.communicate()
        raise RuntimeError(f"git command timed out after 60s: {argv!r}")
    return proc.returncode, stdout or ""


def _porcelain_entries(root: str) -> list[tuple[str, str]]:
    """Parse `git status --porcelain -z` into (status, path) pairs.

    NUL-separated parsing only — never strips quote characters. Rename
    records ("R "/"C " status) consume the following NUL token (the
    original path) and keep only the new path.
    """
    returncode, stdout = _git_run(
        ["git", "-C", str(root), "status", "--porcelain", "-z"]
    )
    if returncode != 0:
        raise RuntimeError(f"not a git repository: {root}")
    tokens = stdout.split("\0")
    if tokens and tokens[-1] == "":
        tokens = tokens[:-1]
    entries: list[tuple[str, str]] = []
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        if len(tok) < 3:
            i += 1
            continue
        status = tok[:2]
        path = tok[3:]
        entries.append((status, path))
        if "R" in status or "C" in status:
            i += 2  # skip the ORIG_PATH token for rename/copy records
        else:
            i += 1
    return entries


def _git_changed_paths(root: str) -> set[str]:
    """Thin porcelain -z enumerator: all changed/untracked paths.

    Kept for backward compatibility (used by `_snapshot_pre_state` callers
    and tests); NUL-separated parsing, no quote-unescaping ever.
    """
    return {path for _status, path in _porcelain_entries(root)}


def _porcelain_tracked_paths(root: str) -> set[str]:
    """Tracked-dirty paths only (excludes '??' untracked marker entries)."""
    return {path for status, path in _porcelain_entries(root) if status != "??"}


def _list_untracked_files(root: str) -> set[str]:
    """Per-file untracked paths (never a bare directory path)."""
    returncode, stdout = _git_run(
        ["git", "-C", str(root), "ls-files", "-z", "--others", "--exclude-standard"]
    )
    if returncode != 0:
        raise RuntimeError(
            f"git ls-files --others failed (rc={returncode}) in {root!r}; "
            f".git may be gone or corrupted"
        )
    parts = stdout.split("\0")
    if parts and parts[-1] == "":
        parts = parts[:-1]
    return set(parts)


def _git_head_oid(root: str) -> str:
    returncode, stdout = _git_run(["git", "-C", str(root), "rev-parse", "HEAD"])
    if returncode != 0:
        return _EMPTY_TREE_OID
    return stdout.strip()


def _hash_object_or_absent(root: str, path: str) -> str:
    full = Path(root) / path
    if not full.exists():
        return _ABSENT_SENTINEL
    returncode, stdout = _git_run(["git", "-C", str(root), "hash-object", "--", path])
    if returncode != 0:
        return _ABSENT_SENTINEL
    return stdout.strip()


def _snapshot_pre_state(root: str) -> dict[str, object]:
    """Capture pre-run git state: HEAD oid + per-path pre-dirty blob oids."""
    head = _git_head_oid(root)
    dirty_paths = _porcelain_tracked_paths(root) | _list_untracked_files(root)
    dirty_hashes = {path: _hash_object_or_absent(root, path) for path in dirty_paths}
    return {"head": head, "dirty_hashes": dirty_hashes}


def _manifest_since(root: str, pre: dict[str, object]) -> list[str]:
    """Paths changed since `pre` — survives model self-`git add`/`commit`."""
    head_val = pre.get("head", _EMPTY_TREE_OID)
    head = head_val if isinstance(head_val, str) else _EMPTY_TREE_OID
    diff_returncode, diff_stdout = _git_run(
        ["git", "-C", str(root), "diff", "--name-only", "-z", head]
    )
    if diff_returncode != 0:
        raise RuntimeError(
            f"git diff --name-only failed (rc={diff_returncode}) in {root!r}; "
            f".git may be gone or corrupted"
        )
    diff_paths: set[str] = set()
    if diff_stdout:
        parts = diff_stdout.split("\0")
        if parts and parts[-1] == "":
            parts = parts[:-1]
        diff_paths = set(parts)

    candidates = diff_paths | _list_untracked_files(root)
    dirty_hashes_obj = pre.get("dirty_hashes", {})
    dirty_hashes: dict[str, str] = dirty_hashes_obj if isinstance(dirty_hashes_obj, dict) else {}

    result: list[str] = []
    for path in candidates:
        if path in dirty_hashes:
            current = _hash_object_or_absent(root, path)
            if current != dirty_hashes[path]:
                result.append(path)
        else:
            result.append(path)
    return sorted(result)


def _is_git_repo(root: str) -> bool:
    returncode, stdout = _git_run(
        ["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"]
    )
    return returncode == 0 and stdout.strip() == "true"


# ---------------------------------------------------------------------------
# Backend handler
# ---------------------------------------------------------------------------

def pydantic_openai_backend(
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
    """Provider-agnostic agentic backend (Pydantic AI v2 + Azure OpenAI)."""
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

    api_key = os.environ.get("AZURE_OPENAI_KEY")
    if not api_key:
        return StepResult(
            status="error",
            data=None,
            duration_ms=0,
            step_name=step_name,
            error="AZURE_OPENAI_KEY environment variable is not set",
            error_code="E_LLM_API_KEY_MISSING",
            recoverable=False,
        )
    endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
    if not endpoint:
        return StepResult(
            status="error",
            data=None,
            duration_ms=0,
            step_name=step_name,
            error="AZURE_OPENAI_ENDPOINT environment variable is not set",
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

    deployment = os.environ.get("PYDANTIC_BACKEND_DEPLOYMENT") or model

    from pydantic_ai import Agent, UsageLimits
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.azure import AzureProvider

    api_version = os.environ.get("AZURE_OPENAI_API_VERSION") or "2024-10-21"
    provider = AzureProvider(
        azure_endpoint=endpoint,
        api_key=api_key,
        api_version=api_version,
    )
    llm_model = OpenAIChatModel(deployment, provider=provider)

    agent = Agent(llm_model, tools=[])

    cancel_event = threading.Event()

    @agent.tool_plain
    def write_file(path: str, content: str) -> str:
        if cancel_event.is_set():
            return "error: run cancelled (timeout)"
        return _tool_write_file(root, path, content)

    @agent.tool_plain
    def edit_file(path: str, old: str, new: str) -> str:
        if cancel_event.is_set():
            return "error: run cancelled (timeout)"
        return _tool_edit_file(root, path, old, new)

    @agent.tool_plain
    def run_tests(command: str) -> str:
        if cancel_event.is_set():
            return "error: run cancelled (timeout)"
        return _tool_run_tests(root, command, cancel_event=cancel_event)

    @agent.tool_plain
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
        "command": ["pydantic-openai", deployment],
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
    """Register 'pydantic-openai' backend with llm_subprocess.

    Idempotent (overwrite=True). Raises ImportError with the
    `agentic-pydantic` pip-extra hint if pydantic_ai is not importable.
    """
    if not _pydantic_ai_importable():
        raise ImportError(_PIP_EXTRA_HINT)
    register_backend(
        "pydantic-openai",
        pydantic_openai_backend,
        manifest_source="git_diff",
        capabilities=frozenset(),
        overwrite=True,
    )


if _pydantic_ai_importable():
    register()  # import side-effect, guarded — only if pydantic_ai importable
