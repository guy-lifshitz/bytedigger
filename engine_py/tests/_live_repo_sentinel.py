"""Post-suite live-repo HEAD drift sentinel — GH1220 Change C.

Detects a test suite that mutated the LIVE repo of this worktree (the exact
incident class GH1220 repairs): a cheap, subprocess-free HEAD read is taken
at `pytest_configure`, re-checked (via one cheap subprocess call) at
`pytest_sessionfinish`; any drift in commit count or HEAD sha fails the
session loudly, naming the culprit test and the new commit(s).

Fail-open on infrastructure (root/git unresolvable), fail-closed on
detection (C6) — this is a detector for a bug class, not a correctness
gate; reddening every suite on a git hiccup would get it disabled within a
day.

All state lives in ONE module-global `_STATE` dict with `reset_state()` (C7)
so an AC in this file that drives `pytest_configure` against a tmp repo can
restore the conftest-wired live instance's baseline before the real
`pytest_sessionfinish` runs.

`find_repo_root` and `subprocess.run` are called as bare module-global
lookups so tests can monkeypatch them directly (C8).
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

_KILL_SWITCH_ENV = "HAL_LIVE_REPO_SENTINEL"


def _default_state() -> dict:
    return {
        "enabled": False,
        "root": None,
        "baseline": None,
        "culprit": None,
    }


_STATE: dict = _default_state()


def reset_state() -> None:
    """C7: restore `_STATE` to its default (disabled, no baseline)."""
    global _STATE
    _STATE = _default_state()


def find_repo_root() -> "Path | None":
    """Climb from this file's directory up to the entry named '.git'
    (dir OR file — a linked worktree's `.git` is a file)."""
    here = Path(__file__).resolve().parent
    for parent in [here, *here.parents]:
        if (parent / ".git").exists():
            return parent
    return None


def _read_gitdir(root: Path) -> "Path | None":
    git_entry = root / ".git"
    if git_entry.is_dir():
        return git_entry
    if git_entry.is_file():
        try:
            content = git_entry.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        if content.startswith("gitdir:"):
            raw = content[len("gitdir:"):].strip()
            p = Path(raw)
            if not p.is_absolute():
                p = (root / p)
            return p
    return None


def _common_dir(gitdir: Path) -> Path:
    """Resolve the commondir for a per-worktree gitdir (MINOR-7): contents
    are relative to `gitdir` when not absolute."""
    commondir_file = gitdir / "commondir"
    if commondir_file.is_file():
        try:
            raw = commondir_file.read_text(encoding="utf-8").strip()
        except OSError:
            return gitdir
        p = Path(raw)
        if not p.is_absolute():
            p = (gitdir / p)
        return p
    return gitdir


def cheap_head_sha(root: "str | Path") -> "str | None":
    """File-read-only HEAD resolution — no subprocess. Follows:
    `<root>/.git` -> gitdir -> HEAD -> ref -> try gitdir/<ref>, then
    commondir/<ref>, then scan commondir/packed-refs. Returns None when
    unresolvable at any step."""
    root = Path(root)
    gitdir = _read_gitdir(root)
    if gitdir is None:
        return None
    head_file = gitdir / "HEAD"
    try:
        head_content = head_file.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not head_content:
        return None
    if not head_content.startswith("ref:"):
        # Detached HEAD — a raw sha.
        if len(head_content) == 40 and all(c in "0123456789abcdef" for c in head_content):
            return head_content
        return None
    ref = head_content[len("ref:"):].strip()
    common = _common_dir(gitdir)
    for base in (gitdir, common):
        ref_path = base / ref
        if ref_path.is_file():
            try:
                val = ref_path.read_text(encoding="utf-8").strip()
            except OSError:
                continue
            if val:
                return val
    packed = common / "packed-refs"
    if packed.is_file():
        try:
            for line in packed.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or line.startswith("^"):
                    continue
                parts = line.split(" ", 1)
                if len(parts) == 2 and parts[1].strip() == ref:
                    return parts[0]
        except OSError:
            logger.debug("cheap_head_sha: packed-refs read failed", exc_info=True)
    return None


def capture_baseline(root: "str | Path") -> "dict | None":
    """`{"count": int, "sha": str|None}` via ONE subprocess call
    (`git rev-list --count HEAD`); `sha` from the cheap file-based read.
    Returns None on infrastructure failure (fail-open, C6)."""
    root = Path(root)
    try:
        proc = subprocess.run(
            ["git", "rev-list", "--count", "HEAD"],
            capture_output=True, text=True, cwd=str(root), timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    try:
        count = int((proc.stdout or "0").strip() or 0)
    except ValueError:
        return None
    return {"count": count, "sha": cheap_head_sha(root)}


def _disable(reason: str) -> None:
    _STATE["enabled"] = False
    print(f"LIVE-REPO-SENTINEL: disabled ({reason})", file=sys.stderr)


def pytest_configure(config) -> None:  # noqa: ARG001
    """C1: resolve the live repo root, capture the baseline. C5: the kill
    switch makes this (and everything else) a no-op — no subprocess."""
    reset_state()
    if os.environ.get(_KILL_SWITCH_ENV) == "0":
        return
    root = find_repo_root()
    if root is None:
        _disable("repo root unresolvable")
        return
    baseline = capture_baseline(root)
    if baseline is None:
        _disable("git rev-list failed")
        return
    _STATE["enabled"] = True
    _STATE["root"] = root
    _STATE["baseline"] = baseline


def pytest_runtest_teardown(item) -> None:  # noqa: ARG001
    """C3: cheap HEAD read; record the FIRST divergence (culprit
    attribution) only."""
    if not _STATE.get("enabled"):
        return
    if _STATE.get("culprit") is not None:
        return
    root = _STATE.get("root")
    baseline = _STATE.get("baseline") or {}
    if root is None:
        return
    current_sha = cheap_head_sha(root)
    if current_sha != baseline.get("sha"):
        _STATE["culprit"] = (getattr(item, "nodeid", "<unknown>"), baseline.get("sha"), current_sha)


def pytest_sessionfinish(session) -> None:
    """C4: re-check via ONE subprocess call; on drift, a loud stderr report
    naming both counts/shas, the new commits, and the culprit nodeid, and
    `session.exitstatus = 1`. No drift -> exitstatus untouched (AC19)."""
    if not _STATE.get("enabled"):
        return
    root = _STATE.get("root")
    baseline = _STATE.get("baseline") or {}
    if root is None:
        return
    current = capture_baseline(root)
    if current is None:
        return  # fail-open on infra (C6) — a passing session is not reddened
    if current.get("count") == baseline.get("count") and current.get("sha") == baseline.get("sha"):
        return
    culprit = _STATE.get("culprit")
    culprit_nodeid = culprit[0] if culprit else "<unknown — no test observed a HEAD change>"
    try:
        log_proc = subprocess.run(
            ["git", "log", "--oneline", f"{baseline.get('sha')}..{current.get('sha')}"],
            capture_output=True, text=True, cwd=str(root), timeout=30,
        )
        new_commits = log_proc.stdout or ""
    except (OSError, subprocess.SubprocessError):
        new_commits = ""
    report = (
        "LIVE-REPO-SENTINEL: live-repo HEAD drift detected during the test run!\n"
        f"  before: count={baseline.get('count')} sha={baseline.get('sha')}\n"
        f"  after:  count={current.get('count')} sha={current.get('sha')}\n"
        f"  culprit (first test observed after the drift): {culprit_nodeid}\n"
        f"  new commits:\n{new_commits}"
    )
    print(report, file=sys.stderr)
    session.exitstatus = 1
