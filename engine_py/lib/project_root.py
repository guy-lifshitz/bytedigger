"""Shared project-root resolver — agreement 043A9BF3.

Single source of truth (§1g) for resolving the root that spec citations are
relative to (the repo being built). Replaces two hardcoded `Path.home()/".claude"`
defaults in phase_45_spec.py and lint_spec.py.

Precedence (preserves 789CDF56):
  1. cfg["hal_root"]              -> "cfg_hal_root"
  2. cfg["git_cwd"]               -> "cfg_git_cwd"
  3. cfg["checklist"]["hal_root"] -> "checklist_hal_root"
  4. cfg["scratchpad_dir"] -> repo root -> "scratchpad_repo_root"
  5. git -C <cwd> rev-parse --show-toplevel -> "git_toplevel"
  6. <cwd>                        -> "cwd"
"""
from __future__ import annotations

from pathlib import Path

from config_provider import foreign_state_dirname
from lib.git_port import git_read
from lib.observability.emit_resolver import emit_resolver_resolved


def _scratchpad_repo_root(scratchpad_dir: str) -> Path | None:
    """Derive the build's target-repo root from a scratchpad path.

    scratchpad_dir lives at <repo>/<foreign_state_dirname()>/scratchpad/<run>;
    strip to <repo>. The dirname is read from the config_provider seam at call
    time (§1g single source) so foreign hosts resolve too.
    Returns the repo root only if it is a git checkout (.git exists), else None.
    """
    segment = f"/{foreign_state_dirname()}/"
    if segment not in scratchpad_dir:
        return None
    candidate = Path(scratchpad_dir.split(segment)[0]).expanduser().resolve()
    if (candidate / ".git").exists():
        return candidate
    return None


def _git_toplevel(base: Path) -> Path | None:
    """Return the git toplevel for *base*, or None on any error/non-repo."""
    try:
        result = git_read(
            ["rev-parse", "--show-toplevel"],
            dir_=str(base),
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return Path(result.stdout.strip()).resolve()
    except Exception:  # noqa: BLE001
        pass
    return None


def resolve_project_root(
    cfg: dict | None,
    *,
    cwd: Path | None = None,
) -> tuple[Path, str]:
    """Resolve the root that spec citations are relative to (the repo being built).

    Silent project-aware derive (no hardcoded HAL root). Returns (resolved_root, source).

    Precedence (preserves 789CDF56):
      1. cfg["hal_root"]              -> "cfg_hal_root"
      2. cfg["git_cwd"]               -> "cfg_git_cwd"
      3. cfg["checklist"]["hal_root"] -> "checklist_hal_root"
      4. cfg["scratchpad_dir"] -> repo root -> "scratchpad_repo_root"
      5. `git -C <cwd> rev-parse --show-toplevel` -> "git_toplevel"
      6. <cwd>                        -> "cwd"

    All returned paths are .expanduser().resolve()'d. cwd defaults to Path.cwd().
    """
    if cfg is None:
        cfg = {}

    effective_cwd = cwd if cwd is not None else Path.cwd()

    resolved: Path | None = None
    source: str = "cwd"

    if cfg.get("hal_root"):
        resolved = Path(str(cfg["hal_root"])).expanduser().resolve()
        source = "cfg_hal_root"
    elif cfg.get("git_cwd"):
        resolved = Path(str(cfg["git_cwd"])).expanduser().resolve()
        source = "cfg_git_cwd"
    elif (cfg.get("checklist") or {}).get("hal_root"):
        resolved = Path(str((cfg.get("checklist") or {})["hal_root"])).expanduser().resolve()
        source = "checklist_hal_root"
    elif cfg.get("scratchpad_dir") and (
        _r := _scratchpad_repo_root(str(cfg["scratchpad_dir"]))
    ) is not None:
        resolved = _r
        source = "scratchpad_repo_root"
    else:
        toplevel = _git_toplevel(effective_cwd)
        if toplevel is not None:
            resolved = toplevel
            source = "git_toplevel"
        else:
            resolved = effective_cwd.expanduser().resolve()
            source = "cwd"

    emit_resolver_resolved("project_root", source, str(resolved))
    return resolved, source
