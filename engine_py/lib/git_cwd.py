"""Shared git_cwd resolver — GH#381.

Single source of truth (§1g) for the git working directory the engine
should treat as "the repo under build". Kills the process-CWD default
class (engine git ops silently running in the wrong repo when
``git_cwd`` is unset — see spec 2026-07-07_GH381).

Precedence:
  1. cfg["git_cwd"]                 -> "cfg_git_cwd"      (verbatim str, as today)
  2. prev_data["git_cwd"]           -> "prev_data"        (verbatim str, as today)
  3. cfg["current_worktree_path"]   -> "cfg_current_worktree_path" (expanduser().resolve())
  4. climb cfg["scratchpad_dir"] parents until (p / ".git").exists() -> "scratchpad_climb"
     (mirrors lib/worktree_root.py semantics; .git file OR dir; scratchpad
      expanduser().resolve() first, OSError during resolve -> fall through to 5)
  5. Path.cwd()                     -> "cwd"

Amendment 1 (GH449): `resolve_git_cwd_with_source` exposes the source label
alongside the resolved path so callers can gate behavior on whether
resolution came from an explicit source or defaulted to the ambient
process cwd (e.g. never dirty-tree-fallback-commit an ambient Path.cwd()).
`resolve_git_cwd` is a thin wrapper that returns just the path.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from lib.observability.emit_resolver import emit_resolver_resolved

# Amendment 1 (GH449) / A4 (GH1220 v2): sources whose PATH is anchored to the
# ambient process CWD -- either directly ("cwd") or because a verbatim,
# unresolved relative string from an otherwise-explicit config key will be
# anchored to the ambient CWD the moment a caller hands it to `cwd=`
# (levels 1/2, MAJOR-3), or because an explicit key's *value* was itself
# relative and therefore anchored during resolution (levels 3/4).
EXPLICIT_GIT_CWD_SOURCES = frozenset({
    "cfg_git_cwd", "prev_data", "cfg_current_worktree_path", "scratchpad_climb",
})
AMBIENT_GIT_CWD_SOURCES = frozenset({
    "cwd",
    "cfg_git_cwd_relative", "prev_data_relative",
    "cfg_current_worktree_path_relative", "scratchpad_climb_relative",
})


def is_ambient_git_cwd(source: str) -> bool:
    """True => `source` was derived from the ambient process CWD => NEVER
    run a mutating git op there."""
    return source in AMBIENT_GIT_CWD_SOURCES


def resolve_git_cwd_with_source(cfg: "dict | None", prev_data: "dict | None" = None) -> "tuple[str, str]":
    cfg = cfg or {}

    raw_git_cwd = cfg.get("git_cwd")
    if raw_git_cwd:
        resolved = str(raw_git_cwd)
        source = "cfg_git_cwd" if os.path.isabs(resolved) else "cfg_git_cwd_relative"
        emit_resolver_resolved("git_cwd", source, resolved)
        return resolved, source

    prev_git_cwd = prev_data.get("git_cwd") if isinstance(prev_data, dict) else None
    if prev_git_cwd:
        resolved = str(prev_git_cwd)
        source = "prev_data" if os.path.isabs(resolved) else "prev_data_relative"
        emit_resolver_resolved("git_cwd", source, resolved)
        return resolved, source

    raw_worktree = cfg.get("current_worktree_path")
    if raw_worktree:
        was_absolute = os.path.isabs(str(raw_worktree))
        resolved = str(Path(raw_worktree).expanduser().resolve())
        source = "cfg_current_worktree_path" if was_absolute else "cfg_current_worktree_path_relative"
        emit_resolver_resolved("git_cwd", source, resolved)
        return resolved, source

    raw_scratchpad = cfg.get("scratchpad_dir")
    if raw_scratchpad:
        was_absolute = os.path.isabs(str(raw_scratchpad))
        try:
            cur = Path(raw_scratchpad).expanduser().resolve()
        except OSError:
            cur = None
        if cur is not None:
            for parent in [cur, *cur.parents]:
                if (parent / ".git").exists():
                    resolved = str(parent)
                    source = "scratchpad_climb" if was_absolute else "scratchpad_climb_relative"
                    emit_resolver_resolved("git_cwd", source, resolved)
                    return resolved, source

    resolved = str(Path.cwd())
    emit_resolver_resolved("git_cwd", "cwd", resolved)
    return resolved, "cwd"


def resolve_git_cwd(cfg: "dict | None", prev_data: "dict | None" = None) -> str:
    resolved, _source = resolve_git_cwd_with_source(cfg, prev_data)
    return resolved


def resolve_git_cwd_for_write(
    cfg: "dict[str, Any] | None", prev_data: "dict[str, Any] | None" = None,
) -> "tuple[str | None, str]":
    """Write-scoped resolver: (path, source) for an explicit source; (None,
    source) when ambient -- the caller MUST NOT run any mutating git op."""
    resolved, source = resolve_git_cwd_with_source(cfg, prev_data)
    if is_ambient_git_cwd(source):
        return None, source
    return resolved, source
