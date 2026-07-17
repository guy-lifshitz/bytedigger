"""dirty_tree_guard — pre-RED-gate / pre-validation dirty-production-tree check.

GH961 dirty-tree guard spec (2026-07-17, Decisions archive).

Public API:
    dirty_prod_paths(git_cwd, red_test_paths, allowlist, timeout=30) -> tuple[list[str], str | None]

Mirrors the `_derive_green_paths_from_git` porcelain-parsing precedent
(workflows/phase_5_implement.py L726) and the fail-open contract: on git
failure returns ([], "<short error>") — never raises.
"""
from __future__ import annotations

from lib import git_port

TEST_SEGMENTS = ("tests/", "__tests__/")


def _is_test_segment(path: str) -> bool:
    normalized = path.strip()
    return any(seg in normalized for seg in TEST_SEGMENTS)


def _normalize(path: str) -> str:
    p = path.strip()
    if p.startswith("./"):
        p = p[2:]
    return p


def dirty_prod_paths(
    git_cwd: str, red_test_paths: list[str], allowlist: list[str], timeout: int = 30
) -> tuple[list[str], str | None]:
    """Return (sorted violations, err). Fail-open: on git failure returns ([], "<err>").

    violations = dirty paths (after test-path/test-segment exclusion) intersected
    with the normalized `allowlist` (v2 §2.2 — scopes the guard to the spec's
    `## Files` allowlist, avoiding false positives from unrelated repo dirt).
    """
    excluded = {_normalize(p) for p in (red_test_paths or [])}
    allowed = {_normalize(p) for p in (allowlist or [])}

    try:
        result = git_port.git_read(
            ["status", "--porcelain"],
            cwd=git_cwd,
            timeout=timeout,
        )
    except Exception as exc:
        return [], f"dirty_prod_paths: git status failed: {exc}"

    if result.returncode != 0:
        return [], f"dirty_prod_paths: git status failed (rc={result.returncode}, stderr={result.stderr[:200]})"

    violations: list[str] = []
    for line in result.stdout.splitlines():
        if len(line) < 4:
            continue
        raw_path = line[3:].strip()
        if " -> " in raw_path:
            raw_path = raw_path.split(" -> ", 1)[1].strip()
        norm = _normalize(raw_path)
        if not norm:
            continue
        if _is_test_segment(norm):
            continue
        if norm in excluded:
            continue
        if norm not in allowed:
            continue
        violations.append(norm)

    return sorted(set(violations)), None
