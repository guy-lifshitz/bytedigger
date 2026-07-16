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

from pathlib import Path

from lib.observability.emit_resolver import emit_resolver_resolved


def resolve_git_cwd_with_source(cfg: "dict | None", prev_data: "dict | None" = None) -> "tuple[str, str]":
    cfg = cfg or {}

    raw_git_cwd = cfg.get("git_cwd")
    if raw_git_cwd:
        resolved = str(raw_git_cwd)
        emit_resolver_resolved("git_cwd", "cfg_git_cwd", resolved)
        return resolved, "cfg_git_cwd"

    prev_git_cwd = prev_data.get("git_cwd") if isinstance(prev_data, dict) else None
    if prev_git_cwd:
        resolved = str(prev_git_cwd)
        emit_resolver_resolved("git_cwd", "prev_data", resolved)
        return resolved, "prev_data"

    raw_worktree = cfg.get("current_worktree_path")
    if raw_worktree:
        resolved = str(Path(raw_worktree).expanduser().resolve())
        emit_resolver_resolved("git_cwd", "cfg_current_worktree_path", resolved)
        return resolved, "cfg_current_worktree_path"

    raw_scratchpad = cfg.get("scratchpad_dir")
    if raw_scratchpad:
        try:
            cur = Path(raw_scratchpad).expanduser().resolve()
        except OSError:
            cur = None
        if cur is not None:
            for parent in [cur, *cur.parents]:
                if (parent / ".git").exists():
                    resolved = str(parent)
                    emit_resolver_resolved("git_cwd", "scratchpad_climb", resolved)
                    return resolved, "scratchpad_climb"

    resolved = str(Path.cwd())
    emit_resolver_resolved("git_cwd", "cwd", resolved)
    return resolved, "cwd"


def resolve_git_cwd(cfg: "dict | None", prev_data: "dict | None" = None) -> str:
    resolved, _source = resolve_git_cwd_with_source(cfg, prev_data)
    return resolved
