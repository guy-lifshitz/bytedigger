"""git_write_port — single source of truth for git WRITE-op lock-retry (5F06E98D).

Public API:
    GitWritePort                  — @runtime_checkable Protocol
    git_op_with_lock_retry(cmd, *, cwd, timeout=30) -> tuple[CompletedProcess|None, str]
    git_op_capture(cmd, *, cwd, timeout=30) -> GitResult
    default_git_write() -> GitWritePort
    get_git_write() -> GitWritePort
    set_default_git_write_factory(factory) -> None
    reset_default_git_write_factory() -> None

Parent SYSTEMATIC: OSS-extraction core-decoupling (plan 2026-06-18).
"""
from __future__ import annotations

import os
import time
from typing import Optional, Protocol, runtime_checkable

from bytedigger_engine.lib.bounded_spawn import bounded_run  # in-package form — canonical for lib/ modules
from bytedigger_engine.lib.git_port import GitResult
from bytedigger_engine.lib.observability.emit_git_write import emit_git_write_at_cwd


def _is_index_lock_error(stderr: str) -> bool:
    return "Unable to create" in stderr and "index.lock" in stderr


def _check_cwd_writable(cwd: "str | None") -> None:
    """GH1220 Change E (A7.1): refuse a RELATIVE ``cwd`` deterministically —
    provenance-free backstop, not a substitute for Change B. ``cwd=None``
    means "inherit the process cwd" and is always allowed."""
    if cwd is not None and not os.path.isabs(cwd):
        raise ValueError(f"git_write_port: refusing a relative cwd: {cwd!r}")


def _cmd0(cmd: "list[str]") -> str:
    """Amendment 4.2: the git SUBCOMMAND VERB — the first element after the
    leading literal "git" (argv[0] is always "git" and carries no
    information)."""
    if cmd and cmd[0] == "git":
        return cmd[1] if len(cmd) > 1 else ""
    return cmd[0] if cmd else ""


@runtime_checkable
class GitWritePort(Protocol):
    """Structural protocol for a git write-op runner with index.lock retry."""
    def op_with_lock_retry(self, cmd: list, *, cwd: str, timeout: int = 30): ...
    def op_capture(self, cmd: list, *, cwd: str, timeout: int = 30) -> "GitResult": ...


class _GitWriteSubprocess:
    """Default impl — body byte-moved verbatim from phase_5_implement._git_op_with_lock_retry."""
    def op_capture(self, cmd: list, *, cwd: str, timeout: int = 30) -> GitResult:
        """Run git command, capture stdout/stderr, return GitResult. OSError propagates."""
        _check_cwd_writable(cwd)
        emit_git_write_at_cwd(_cmd0(cmd), cwd)
        proc = bounded_run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
        return GitResult(
            returncode=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            timed_out=(proc.returncode == 124),
        )

    def op_with_lock_retry(self, cmd: list, *, cwd: str, timeout: int = 30):
        """Run git command, retry on .git/index.lock contention with backoff [1s, 2s]."""
        _check_cwd_writable(cwd)
        emit_git_write_at_cwd(_cmd0(cmd), cwd)
        last_result = None
        for attempt in range(3):
            if attempt > 0:
                time.sleep(attempt)  # 1 then 2
            try:
                last_result = bounded_run(cmd, capture_output=True, text=True, cwd=cwd, timeout=timeout)
            except OSError:
                # missing/inaccessible binary won't reappear on retry — abort immediately
                return None, "os_error"
            if last_result.returncode == 124:
                # stuck binary won't recover on retry — abort immediately
                return None, "timeout"
            if last_result.returncode == 0:
                return last_result, "ok"
            if not _is_index_lock_error(last_result.stderr):
                return last_result, "non_lock_error"  # don't retry
        return last_result, "lock_persisted"


def default_git_write() -> GitWritePort:
    return _GitWriteSubprocess()


# ── §1g single-source registry ──
_DEFAULT_FACTORY = default_git_write
_ORIGINAL_FACTORY = default_git_write


def get_git_write() -> GitWritePort:
    return _DEFAULT_FACTORY()


def set_default_git_write_factory(factory) -> None:
    global _DEFAULT_FACTORY
    _DEFAULT_FACTORY = factory


def reset_default_git_write_factory() -> None:
    global _DEFAULT_FACTORY
    _DEFAULT_FACTORY = _ORIGINAL_FACTORY


# ── module-level late-binding delegators (resolve factory at CALL time) ──
def git_op_with_lock_retry(cmd: list, *, cwd: str, timeout: int = 30):
    return get_git_write().op_with_lock_retry(cmd, cwd=cwd, timeout=timeout)


def git_op_capture(cmd: list, *, cwd: str, timeout: int = 30) -> GitResult:
    return get_git_write().op_capture(cmd, cwd=cwd, timeout=timeout)
