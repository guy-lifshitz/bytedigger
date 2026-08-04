"""dirty_tree_guard — pre-RED-gate / pre-validation dirty-production-tree check.

GH961 dirty-tree guard spec (2026-07-17, Decisions archive).

Public API:
    dirty_prod_paths(git_cwd, red_test_paths, allowlist, timeout=30) -> tuple[list[str], str | None]

Mirrors the `_derive_green_paths_from_git` porcelain-parsing precedent
(workflows/phase_5_implement.py L726) and the fail-open contract: on git
failure returns ([], "<short error>") — never raises.
"""
from __future__ import annotations

from bytedigger_engine.lib import git_port

TEST_SEGMENTS = ("tests/", "__tests__/")


def _is_test_segment(path: str) -> bool:
    normalized = path.strip()
    return any(seg in normalized for seg in TEST_SEGMENTS)


def _normalize(path: str) -> str:
    p = path.strip()
    if p.startswith("./"):
        p = p[2:]
    return p


def parse_porcelain_lines(text: str) -> "list[tuple[str, str]]":
    """`git status --porcelain` text -> sorted (XY code, normalized path) pairs.

    GH1406 §1g: the PURE parse is the only thing shared with `red_skeleton`.
    The `git_port.git_read` CALL deliberately stays in `dirty_prod_paths` below
    — 18 sibling monkeypatch points bind `dirty_tree_guard.git_port.git_read`,
    and routing the call through a shared module would kill that seam silently.

    The XY column is preserved verbatim: the guard does not care WHAT a path is
    dirty with, but the GH1406 provenance classifier does (` D` is a deletion,
    not a skeleton).
    """
    entries: list[tuple[str, str]] = []
    for line in (text or "").splitlines():
        if len(line) < 4:
            continue
        xy = line[:2]
        raw_path = line[3:].strip()
        if " -> " in raw_path:
            raw_path = raw_path.split(" -> ", 1)[1].strip()
        norm = _normalize(raw_path)
        if not norm:
            continue
        entries.append((xy, norm))
    return sorted(set(entries), key=lambda e: (e[1], e[0]))


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
    for _xy, norm in parse_porcelain_lines(result.stdout):
        if _is_test_segment(norm):
            continue
        if norm in excluded:
            continue
        if norm not in allowed:
            continue
        violations.append(norm)

    return sorted(set(violations)), None
