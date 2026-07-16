"""git_diff.py — git utilities for disk-truth verification.

Public API:
    GitDiffPort                            — @runtime_checkable Protocol (OSS seam #6)
    default_git_diff() -> GitDiffPort      — return the default (subprocess) implementation
    get_git_diff() -> GitDiffPort          — resolver: returns the currently active factory result
    set_default_git_diff_factory(factory)  — override factory (test injection / OSS host swap)
    reset_default_git_diff_factory()       — restore original factory (§1i singleton-reset)
    git_diff_files(pre_sha, cwd, untracked=True, segment_filter=None) -> list[str]
    git_status_porcelain(cwd, segment_filter=None) -> list[str]
    resolve_pre_phase_sha(cwd) -> str
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional, Protocol, runtime_checkable

from lib.git_port import git_read, GitResult


def _run_git(args: list[str], cwd: str) -> GitResult:
    return git_read(args, cwd=cwd, timeout=30)


def _apply_segment_filter(paths: list[str], segment_filter: Optional[str]) -> list[str]:
    """Keep paths where any path segment (split on '/') contains segment_filter."""
    if segment_filter is None:
        return paths
    result = []
    for p in paths:
        segments = p.replace("\\", "/").split("/")
        if any(segment_filter in seg for seg in segments):
            result.append(p)
    return result


# ── Protocol (§2.1) ──────────────────────────────────────────────────────────

@runtime_checkable
class GitDiffPort(Protocol):
    """Structural protocol for the disk-truth git-diff read operations.
    Any object exposing these 3 methods satisfies it (OSS host swap point)."""
    def diff_files(self, pre_sha: str, cwd: "str | Path",
                   untracked: bool = True,
                   segment_filter: Optional[str] = None) -> list[str]: ...
    def status_porcelain(self, cwd: "str | Path",
                         segment_filter: Optional[str] = None) -> list[str]: ...
    def resolve_pre_phase_sha(self, cwd: "str | Path") -> str: ...


# ── Concrete subprocess implementation (§2.2) ─────────────────────────────────

class _GitDiffSubprocess:
    """Default subprocess-backed implementation of GitDiffPort."""

    def diff_files(
        self,
        pre_sha: str,
        cwd: "str | Path",
        untracked: bool = True,
        segment_filter: Optional[str] = None,
    ) -> list[str]:
        cwd_str = str(cwd)
        paths: set[str] = set()

        # Tracked changes since pre_sha
        result = _run_git(["diff", "--name-only", pre_sha, "HEAD"], cwd_str)
        for line in result.stdout.splitlines():
            line = line.strip()
            if line:
                paths.add(line)

        # Untracked files and working-tree/staged changes vs HEAD
        if untracked:
            result = _run_git(["diff", "--name-only", "HEAD"], cwd_str)
            for line in result.stdout.splitlines():
                line = line.strip()
                if line:
                    paths.add(line)

            result = _run_git(["ls-files", "--others", "--exclude-standard"], cwd_str)
            for line in result.stdout.splitlines():
                line = line.strip()
                if line:
                    paths.add(line)

        combined = sorted(paths)
        return _apply_segment_filter(combined, segment_filter)

    def status_porcelain(
        self,
        cwd: "str | Path",
        segment_filter: Optional[str] = None,
    ) -> list[str]:
        cwd_str = str(cwd)
        result = _run_git(["status", "--porcelain"], cwd_str)
        paths: list[str] = []
        for line in result.stdout.splitlines():
            # Format: "XY path" or "XY old -> new"
            if len(line) >= 3:
                path_part = line[3:].strip()
                # Handle renames: "old -> new" — take the new path
                if " -> " in path_part:
                    path_part = path_part.split(" -> ", 1)[1].strip()
                if path_part:
                    paths.append(path_part)
        return _apply_segment_filter(paths, segment_filter)

    def resolve_pre_phase_sha(self, cwd: "str | Path") -> str:
        cwd_str = str(cwd)
        try:
            result = _run_git(["rev-parse", "HEAD"], cwd_str)
        except (FileNotFoundError, OSError) as exc:
            raise RuntimeError(
                f"git rev-parse HEAD failed in '{cwd_str}': {exc}"
            ) from exc
        if result.returncode != 0:
            raise RuntimeError(
                f"git rev-parse HEAD failed in '{cwd_str}': {result.stderr.strip()}"
            )
        return result.stdout.strip()


# ── Default factory (§2.3) ────────────────────────────────────────────────────

def default_git_diff() -> GitDiffPort:
    """Return the default (subprocess) GitDiffPort implementation."""
    return _GitDiffSubprocess()


# ── Registry (§2.4 — §1g single-source) ──────────────────────────────────────

_DEFAULT_FACTORY = default_git_diff
_ORIGINAL_FACTORY = default_git_diff


# ── Resolver (§2.5) ───────────────────────────────────────────────────────────

def get_git_diff() -> GitDiffPort:
    """Return the currently active GitDiffPort (overridable by tests / OSS host)."""
    return _DEFAULT_FACTORY()


# ── Injectors (§2.6) ──────────────────────────────────────────────────────────

def set_default_git_diff_factory(factory) -> None:  # type: ignore[type-arg]
    global _DEFAULT_FACTORY
    _DEFAULT_FACTORY = factory


def reset_default_git_diff_factory() -> None:
    global _DEFAULT_FACTORY
    _DEFAULT_FACTORY = _ORIGINAL_FACTORY


# ── Public delegators (§2.7) ─────────────────────────────────────────────────

def git_diff_files(
    pre_sha: str,
    cwd: "str | Path",
    untracked: bool = True,
    segment_filter: Optional[str] = None,
) -> list[str]:
    """Return deduplicated sorted list of files changed since pre_sha.

    - Tracked changes: git diff --name-only <pre_sha> HEAD
    - If untracked=True, also includes working-tree+staged changes via git diff --name-only HEAD
      and new untracked files via git ls-files --others --exclude-standard
    - segment_filter: keep only paths where any segment contains this string
    """
    return get_git_diff().diff_files(pre_sha, cwd, untracked, segment_filter)


def git_status_porcelain(
    cwd: "str | Path",
    segment_filter: Optional[str] = None,
) -> list[str]:
    """Return list of paths from git status --porcelain (path component only).

    Strips the 2-char status prefix + space. Honors segment_filter.
    """
    return get_git_diff().status_porcelain(cwd, segment_filter)


def resolve_pre_phase_sha(cwd: "str | Path") -> str:
    """Return the current HEAD SHA (40 hex chars) for the given repo.

    Raises RuntimeError if path is not a git repo or git command fails.
    """
    return get_git_diff().resolve_pre_phase_sha(cwd)
