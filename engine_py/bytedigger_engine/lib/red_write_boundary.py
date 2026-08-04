"""lib/red_write_boundary.py — GH1179 deterministic post-RED write-boundary gate.

Spec (frozen v3): SHARED/memory/Decisions/2026-07-25_6B28230E_gh1179_red_write_boundary_spec.md
Agreement: 6B28230E-E6B4-4308-A0C3-AF66795FC076

Detects a RED subagent straying a test-shaped write into the MAIN checkout
instead of its build worktree, via a differential dirty-path snapshot taken
immediately before/after the RED LLM subprocess call (§1.1). This module is
pure detection/classification; the phase-5 call site (`_enforce_red_write_boundary`
in workflows/phase_5_implement.py) owns the StepResult/telemetry side effects.

Public API:
    resolve_main_repo_root(worktree_root: str | Path) -> Path | None
    snapshot_boundary_state(worktree_root: str | Path) -> dict[str, Any]
    is_test_shaped(path: str) -> bool
    classify_boundary_delta(before, after, shape_filter=is_test_shaped) -> dict[str, Any]
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from bytedigger_engine.lib import git_port
from bytedigger_engine.lib.util.path_classifier import _is_test_path


def resolve_main_repo_root(worktree_root: str | Path) -> Path | None:
    """Run `git worktree list --porcelain` and return the first `worktree`
    entry (the canonical main checkout root) as a resolved Path.

    Accepts `worktree_root` as str or Path (§1.6-1, explicit coercion).
    Returns None — never raises — on any git failure: rc != 0, rc == 124,
    FileNotFoundError, OSError (§1.6-2).
    """
    root = Path(worktree_root)
    try:
        proc = git_port.git_read(
            ["worktree", "list", "--porcelain"], cwd=str(root), timeout=10,
        )
    except (FileNotFoundError, OSError):
        return None
    if proc.returncode == 124 or proc.returncode != 0:
        return None
    for line in proc.stdout.splitlines():
        if line.startswith("worktree "):
            return Path(line[len("worktree "):]).resolve()
    return None


def _status_paths(cwd: Path) -> frozenset[str] | None:
    """Run `git status --porcelain` at cwd; return the set of paths
    (including `??` untracked entries). Returns None — never raises — on
    any git failure (§1.6-2), guarded independently of the worktree-list call.
    """
    try:
        proc = git_port.git_read(["status", "--porcelain"], cwd=str(cwd), timeout=10)
    except (FileNotFoundError, OSError):
        return None
    if proc.returncode == 124 or proc.returncode != 0:
        return None
    paths: set[str] = set()
    for raw_line in proc.stdout.splitlines():
        if not raw_line:
            continue
        path = raw_line[3:].strip() if len(raw_line) > 3 else ""
        if path:
            paths.add(path)
    return frozenset(paths)


def _excluded_by_subtree(path: str, rel_worktree: str) -> bool:
    """§1.3 (gate n2, bidirectional): drop a MAIN path that is a descendant
    of, equal to, or a path-prefix ancestor of the worktree's own relative
    path. The ancestor case matters because `git status --porcelain`
    collapses a wholly-untracked directory to a single entry."""
    p = path.rstrip("/")
    w = rel_worktree.rstrip("/")
    if p == w:
        return True
    if p.startswith(w + "/"):
        return True
    if w.startswith(p + "/"):
        return True
    return False


def snapshot_boundary_state(worktree_root: str | Path) -> dict[str, Any]:
    """Snapshot the dirty-path sets of both the worktree and its main repo.

    Returns:
        {"available": bool, "main_repo_root": str | None, "worktree_root": str,
         "main_paths": frozenset[str], "worktree_paths": frozenset[str]}

    available=False ONLY when worktree_root resolves to the main repo root
    itself (§1.3) — a genuine single-tree build. A worktree nested under the
    main root stays armed (available=True); MAIN paths belonging to the
    worktree's own subtree are excluded from main_paths.
    """
    root = Path(worktree_root)
    unavailable: dict[str, Any] = {
        "available": False, "main_repo_root": None, "worktree_root": str(root),
        "main_paths": frozenset(), "worktree_paths": frozenset(),
    }
    main_root = resolve_main_repo_root(root)
    if main_root is None:
        return unavailable
    try:
        worktree_resolved = root.resolve()
    except OSError:
        worktree_resolved = root
    if main_root == worktree_resolved:
        # single-tree build — cannot cross-tree by construction (§1.3).
        return unavailable

    worktree_paths = _status_paths(worktree_resolved)
    if worktree_paths is None:
        return unavailable
    main_paths_raw = _status_paths(main_root)
    if main_paths_raw is None:
        return unavailable

    try:
        rel_worktree = str(worktree_resolved.relative_to(main_root))
    except ValueError:
        rel_worktree = None
    if rel_worktree is not None:
        main_paths = frozenset(
            p for p in main_paths_raw if not _excluded_by_subtree(p, rel_worktree)
        )
    else:
        main_paths = main_paths_raw

    return {
        "available": True,
        "main_repo_root": str(main_root),
        "worktree_root": str(worktree_resolved),
        "main_paths": main_paths,
        "worktree_paths": worktree_paths,
    }


def is_test_shaped(path: str) -> bool:
    """Thin delegation to the single-source test-path classifier (gate n1,
    §1.6-10) — NOT a fresh predicate. Keeps this gate's notion of
    "test-shaped" identical to `_derive_red_paths_via_git_diff`'s."""
    return bool(_is_test_path(path))


def classify_boundary_delta(
    before: dict[str, Any],
    after: dict[str, Any],
    shape_filter: Callable[[str], bool] = is_test_shaped,
) -> dict[str, Any]:
    """Classify a before/after snapshot pair (§1.4). Pure function, no I/O.

    Returns:
        {"verdict": "unavailable" | "clean" | "ambiguous" | "violation",
         "main_repo_root": str | None, "stray_paths": list[str],
         "worktree_new_paths": list[str]}
    """
    if (
        not before.get("available")
        or not after.get("available")
        or before.get("main_repo_root") != after.get("main_repo_root")
    ):
        return {
            "verdict": "unavailable", "main_repo_root": None,
            "stray_paths": [], "worktree_new_paths": [],
        }

    main_repo_root = after.get("main_repo_root")
    new_main = after.get("main_paths", frozenset()) - before.get("main_paths", frozenset())
    new_worktree = after.get("worktree_paths", frozenset()) - before.get("worktree_paths", frozenset())

    stray_paths = sorted(p for p in new_main if shape_filter(p))
    worktree_new_paths = sorted(p for p in new_worktree if shape_filter(p))

    if stray_paths and worktree_new_paths:
        verdict = "ambiguous"
    elif stray_paths:
        verdict = "violation"
    else:
        verdict = "clean"

    return {
        "verdict": verdict,
        "main_repo_root": main_repo_root,
        "stray_paths": stray_paths,
        "worktree_new_paths": worktree_new_paths,
    }
