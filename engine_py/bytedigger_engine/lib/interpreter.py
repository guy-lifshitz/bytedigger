"""Canonical project-interpreter resolver (GH1626 part C, §1g).

The single copy of the `.venv` climb.  Before this module the rule that decides
"which Python is real" existed twice — `_venv_pytest` / `_main_checkout_root`
were defined in both `workflows/phase_5_implement.py` and
`workflows/phase_6_review.py` — which is how the two phases came to disagree.
Both phases are now thin callers of the definitions below.

Design v3 (frozen spec, gate round 6): resolve at USE time, provision nowhere.
Build worktrees have many producers — batch, runner-v2, `coord/lot-launch.sh`,
the target repo's own `make wt-new`, and a human typing `git worktree add` — so
any fix living at worktree-CREATION time is bypassable by the next producer
nobody enumerated.  The layer that cannot be bypassed is the one that asks
"which Python?", because every test run goes through it.  Hence the CLIMB: from
a linked worktree we reach the MAIN CHECKOUT via
`git rev-parse --git-common-dir` and use the project `.venv` found there.  The
worktree's OWN `.venv` still wins when it has one.

A dangling `<worktree>/.venv` symlink is not an environment: every probe uses
`Path.is_file()`, which FOLLOWS the link, never `lexists()`.

`source` vocabulary: `worktree_venv` (git_cwd owns it), `parent_venv` (reached
by climbing out of a linked worktree to a DIFFERENT checkout), `repo_venv`
(reached by ascending from a subdirectory to its own repository root), `none`.
`none` is loud: it emits `interpreter_unresolved` at severity="warning" naming
the roots searched, because silence is how a missing interpreter reached the
synthesizer as a product claim.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path
from typing import Any

from bytedigger_engine import telemetry_ctx

from bytedigger_engine.lib import git_port

# Probe order is fixed and shared by every helper here: `.venv` before `venv`.
_VENV_DIRNAMES = (".venv", "venv")
_PYTHON_BINARY_NAMES = ("python", "python3")


def _venv_pytest(base: str) -> str | None:
    """BF7890C8: Return the path to the venv pytest under base, or None.

    Probes <base>/.venv/bin/pytest then <base>/venv/bin/pytest; returns the
    first that is_file() and os.X_OK. Pure path logic, no subprocess.
    """
    for cand in (
        Path(base) / ".venv" / "bin" / "pytest",
        Path(base) / "venv" / "bin" / "pytest",
    ):
        if cand.is_file() and os.access(cand, os.X_OK):
            return str(cand)
    return None


def _venv_python(base: str) -> str | None:
    """Return the path to the venv python interpreter under base, or None.

    Probes <base>/{.venv,venv}/bin/{python,python3}; returns the first that
    is_file() and os.X_OK. `is_file()` follows symlinks, so a DANGLING
    `<base>/.venv` link yields None rather than a path that does not exist.
    Pure path logic, no subprocess.
    """
    for venv_dir in _VENV_DIRNAMES:
        for name in _PYTHON_BINARY_NAMES:
            cand = Path(base) / venv_dir / "bin" / name
            if cand.is_file() and os.access(cand, os.X_OK):
                return str(cand)
    return None


def _main_checkout_root(git_cwd: str) -> str | None:
    """BF7890C8: Resolve the main checkout root from a (possibly-worktree) git_cwd.

    Runs `git -C <git_cwd> rev-parse --git-common-dir` (timeout=5s, fail-soft).
    Returns the parent of the common .git dir only when git_cwd is genuinely a
    worktree or a subdirectory (i.e. root != realpath(git_cwd)); returns None
    for the checkout root itself, non-git dirs, or any subprocess error. Never
    raises, never hangs.
    """
    try:
        proc = git_port.git_read(
            ["rev-parse", "--git-common-dir"],
            dir_=git_cwd,
            timeout=5,
        )
        if proc.returncode == 124:
            return None
        if proc.returncode != 0:
            return None
        common = os.path.realpath(os.path.join(git_cwd, proc.stdout.strip()))
        root = os.path.dirname(common)
        if root != os.path.realpath(git_cwd):
            return root
        return None
    except (subprocess.CalledProcessError,
            FileNotFoundError, OSError, ValueError):
        return None


def _searched_roots(git_cwd: str, climbed: str | None) -> list[str]:
    """The venv roots probed, in probe order — named in the unresolved event."""
    roots = [str(Path(git_cwd) / d) for d in _VENV_DIRNAMES]
    if climbed:
        roots.extend(str(Path(climbed) / d) for d in _VENV_DIRNAMES)
    return roots


def resolve_project_python(git_cwd: str | None) -> tuple[str, str]:
    """Resolve the project's Python interpreter for `git_cwd`.

    Returns `(path, source)`; `("", "none")` when nothing is reachable, which
    also emits `interpreter_unresolved` at severity="warning" naming the roots
    searched. Never raises.
    """
    if not git_cwd:
        payload = {
            "severity": "warning",
            "git_cwd": git_cwd or "",
            "roots_searched": [],
            "reason": "no git_cwd supplied",
        }
        telemetry_ctx.emit_safe("interpreter_unresolved", payload)
        return "", "none"

    own = _venv_python(git_cwd)
    if own is not None:
        return own, "worktree_venv"

    root = _main_checkout_root(git_cwd)
    if root is not None:
        climbed = _venv_python(root)
        if climbed is not None:
            real_cwd = os.path.realpath(git_cwd)
            inside = real_cwd.startswith(root.rstrip(os.sep) + os.sep)
            return climbed, "repo_venv" if inside else "parent_venv"

    payload = {
        "severity": "warning",
        "git_cwd": str(git_cwd),
        "roots_searched": _searched_roots(str(git_cwd), root),
        "main_checkout_root": root or "",
        "reason": "no project virtualenv reachable from git_cwd or its main checkout",
    }
    telemetry_ctx.emit_safe("interpreter_unresolved", payload)
    return "", "none"


def resolve_pytest_runner(git_cwd: str | None) -> str | None:
    """The project's `pytest` binary for `git_cwd`, or None — same climb.

    Kept separate from `resolve_project_python` because the phase consumers run
    `pytest` directly (not `python -m pytest`) when the project ships one.
    """
    if not git_cwd:
        return None
    hit = _venv_pytest(git_cwd)
    if hit is not None:
        return hit
    root = _main_checkout_root(git_cwd)
    if root is not None:
        return _venv_pytest(root)
    return None


# ─── worker prompt block ─────────────────────────────────────────────────────
#
# Every worker prompt builder that hands over a build worktree carries this
# block, so the worker knows which interpreter to use and — crucially — that a
# missing one is INFRASTRUCTURE, never a coverage or product finding.
#
# The `make PYTHON=<abs>` COMMAND-LINE form is deliberate and measured: the
# target repo's Makefile binds `PYTHON := $(CURDIR)/.venv/bin/python` with `:=`,
# so an ENVIRONMENT variable does not override it. Teaching the env-prefix form
# would teach a no-op and land the worker back on the venv that is not there.
#
# ONE LINE, deliberately: the GREEN prompt carries this block and is capped at
# 6KB (228AB822). Every byte here is a byte the rule sheet cannot spend, so the
# block says the three load-bearing things — which interpreter, how to pass it
# to make, and that its absence is infrastructure — and nothing else.


def worker_interpreter_block(git_cwd: str | None) -> str:
    """Render the interpreter block for a worker operating in `git_cwd`."""
    path, source = resolve_project_python(git_cwd)
    if source == "none":
        return (
            "## INTERPRETER: none found — an INFRASTRUCTURE condition, not "
            "coverage/product. If you find one: `make PYTHON=<abs> <target>`.\n"
        )
    return (
        f"## INTERPRETER: `make PYTHON={path} <target>`. "
        "Missing/broken interpreter = INFRASTRUCTURE, not coverage/product.\n"
    )


def worker_interpreter_block_for(ctx: Any) -> str:
    """Render the interpreter block for the checkout `ctx` hands its worker.

    Uses the canonical git_cwd seam (`lib.git_cwd.resolve_git_cwd`) so the block
    names the same checkout every other phase step operates on.
    """
    from bytedigger_engine.lib.git_cwd import resolve_git_cwd  # noqa: PLC0415  (avoid import cycle)

    cfg = getattr(ctx, "org_config", None) or {}
    try:
        git_cwd = resolve_git_cwd(cfg)
    except (OSError, ValueError, TypeError):
        git_cwd = cfg.get("git_cwd") or cfg.get("current_worktree_path") or ""
    return worker_interpreter_block(git_cwd)
