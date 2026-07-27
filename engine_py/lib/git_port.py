"""git_port — single source of truth for git READ operations (164E4EFA).

Public API:
    GitResult               — dataclass with returncode, stdout, stderr, timed_out
    GitReadPort             — @runtime_checkable Protocol for injectable git readers
    git_read(args, *, cwd, timeout, dir_) -> GitResult
    default_git_read() -> GitReadPort
    get_git_read() -> GitReadPort
    set_default_git_read_factory(factory) -> None
    reset_default_git_read_factory() -> None

Layer 1: `git_read` — rc-aware primitive that preserves returncode / timed_out
  and routes through bounded_run with NO raw subprocess import.

Layer 2: thin convenience value-funcs for stdout-unconditional callers.
  (`rev_parse`, `status_porcelain`, `ls_files_others`, `worktree_list_porcelain`)

Parent SYSTEMATIC: DC87240D (build-engine architecture hardening).
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from lib.bounded_spawn import bounded_run  # in-package form — canonical for modules living in lib/


@dataclass(frozen=True)
class GitResult:
    """Typed result of a git read operation.

    ``returncode`` is the raw subprocess exit code; 124 is the bounded_run
    timeout sentinel (shell timeout convention, matches TIMEOUT_RETURNCODE).
    ``timed_out`` is a convenience alias for ``returncode == 124``.
    OSError / SubprocessError from bounded_run may propagate to the caller
    (callers wrap as needed, e.g. _paths_have_staged_changes catches them).
    """
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool  # == (returncode == 124)


# ── Protocol (§2.1) ──────────────────────────────────────────────────────────

@runtime_checkable
class GitReadPort(Protocol):
    """Structural protocol matching git_read's callable contract.
    Any callable (args, *, cwd, timeout, dir_) -> GitResult satisfies it."""

    def __call__(
        self,
        args: list[str],
        *,
        cwd: str | None = None,
        timeout: float | None = None,
        dir_: str | None = None,
    ) -> GitResult: ...


# ── Concrete subprocess implementation (§2.2) ─────────────────────────────────

def _git_read_subprocess(
    args: list[str],
    *,
    cwd: str | None = None,
    timeout: float | None = None,
    dir_: str | None = None,
) -> GitResult:
    """Run a git read command via bounded_run; never raises on non-zero git exit.

    Builds the exact arg-vector:
        ["git"] + (["-C", dir_] if dir_ else []) + args

    ``cwd`` and ``timeout`` are threaded through to bounded_run verbatim.
    ``dir_`` produces the `git -C <dir_> ...` form (used by G4
    _main_checkout_root for `rev-parse --git-common-dir`).

    Returns a ``GitResult`` carrying the full subprocess outcome.
    OSError / SubprocessError from bounded_run MAY propagate — callers that
    need to handle these wrap the call in a try/except as they did before.

    GH1220 Change E (A7.1): a RELATIVE ``cwd`` is refused deterministically
    (``ValueError``, naming the offending value) — provenance-free backstop,
    not a substitute for the per-site ambient-cwd guards (Change B).
    ``cwd=None`` means "inherit the process cwd" (a deliberate read-path
    affordance — e.g. audit_gate.py's commit-msg hook reads) and is ALWAYS
    allowed.
    """
    if cwd is not None and not os.path.isabs(cwd):
        raise ValueError(f"git_port.git_read: refusing a relative cwd: {cwd!r}")
    cmd = ["git"] + (["-C", dir_] if dir_ else []) + args
    proc = bounded_run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return GitResult(
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
        timed_out=(proc.returncode == 124),
    )


# ── Default factory (§2.3) ────────────────────────────────────────────────────

def default_git_read() -> GitReadPort:
    """Return the default (subprocess) GitReadPort implementation."""
    return _git_read_subprocess


# ── Registry (§2.4 — §1g single-source) ──────────────────────────────────────

_DEFAULT_FACTORY = default_git_read
_ORIGINAL_FACTORY = default_git_read


# ── Resolver (§2.5) ───────────────────────────────────────────────────────────

def get_git_read() -> GitReadPort:
    """Return the currently active GitReadPort (may be overridden by tests/OSS host)."""
    return _DEFAULT_FACTORY()


# ── Injectors (§2.6) ──────────────────────────────────────────────────────────

def set_default_git_read_factory(factory) -> None:  # type: ignore[type-arg]
    """Override the active GitReadPort factory (for test injection / OSS host swap)."""
    global _DEFAULT_FACTORY
    _DEFAULT_FACTORY = factory


def reset_default_git_read_factory() -> None:
    """Restore the factory to the original default (§1i singleton-reset)."""
    global _DEFAULT_FACTORY
    _DEFAULT_FACTORY = _ORIGINAL_FACTORY


# ── Public delegator (§2.7) ───────────────────────────────────────────────────

def git_read(
    args: list[str],
    *,
    cwd: str | None = None,
    timeout: float | None = None,
    dir_: str | None = None,
) -> GitResult:
    """Run a git read command; routes through the injectable GitReadPort seam.

    Contract is identical to the original git_read — all callers are unaffected.
    With no factory override, delegates to _git_read_subprocess byte-for-byte.
    """
    return get_git_read()(args, cwd=cwd, timeout=timeout, dir_=dir_)


# ── Layer 2 — thin convenience value-funcs (stdout-unconditional callers) ──────

def rev_parse(
    ref: str = "HEAD",
    *,
    cwd: str | None = None,
    flag: str | None = None,
    timeout: float | None = None,
) -> str:
    """Return stripped stdout of `git rev-parse [<flag>] [<ref>]`.

    Produces the exact arg-vector the raw sites used.  rc-branching callers
    (git-dir gate, abbrev-ref detached, HEAD-SHA validation) use git_read
    directly — not this convenience fn.
    """
    rev_args = (
        ["rev-parse"]
        + ([flag] if flag else [])
        + ([ref] if (ref and flag is None) else [])
    )
    return git_read(rev_args, cwd=cwd, timeout=timeout).stdout.strip()


def status_porcelain(
    *,
    cwd: str | None = None,
    timeout: float | None = None,
) -> str:
    """Return stdout of `git status --porcelain` (rc-unconditional callers only).

    Callers that branch on rc use git_read directly.
    """
    return git_read(["status", "--porcelain"], cwd=cwd, timeout=timeout).stdout


def ls_files_others(
    *,
    cwd: str | None = None,
    timeout: float | None = None,
) -> list[str]:
    """Return splitlines of `git ls-files --others --exclude-standard`."""
    result = git_read(
        ["ls-files", "--others", "--exclude-standard"],
        cwd=cwd,
        timeout=timeout,
    )
    lines = result.stdout.splitlines()
    return [ln for ln in lines if ln]


def worktree_list_porcelain(
    *,
    cwd: str | None = None,
    timeout: float | None = None,
) -> str:
    """Return stdout of `git worktree list --porcelain`."""
    return git_read(
        ["worktree", "list", "--porcelain"],
        cwd=cwd,
        timeout=timeout,
    ).stdout
