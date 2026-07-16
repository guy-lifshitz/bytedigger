"""CF9CCFE7 RED tests — symlink hardening + dedup of double filter in
`_derive_red_paths_from_git`.

Issues to pin:

1. Symlink traversal vulnerability (line 344): `dir_path.rglob("*")` follows
   symlinks by default. If an untracked dir contains a symlink to outside
   the repo, rglob descends through it; either external paths leak into
   the result list, or `f.relative_to(git_cwd)` raises ValueError.

2. Redundant double filter (lines 350-368 + 371-383): file-line entries are
   filtered twice (correct outcome, wasted cycles + readability tax).
   Should be filtered once total.

These tests FAIL against current phase_5_implement.py (no symlink guard,
double-filter present). They PASS once GREEN adds:
  - rglob with symlink skip (or dir_path.walk(follow_symlinks=False)) and
    a try/except around relative_to.
  - single-pass filter (drop the first-loop file-line filter OR the second
    dedup-and-filter pass — single source of filtering).
"""
from __future__ import annotations

import inspect
import subprocess
import sys
from pathlib import Path
from unittest import mock

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "workflows"))


# ─── helpers (mirror test_phase_5_implement_B6247E87.py) ─────────────────────


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)


def _commit_file(repo: Path, relpath: str, body: str, msg: str = "c") -> None:
    p = repo / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    subprocess.run(["git", "add", relpath], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", msg], cwd=repo, check=True)


def _minimal_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
    return repo



# All 5 CF9CCFE7 tests deleted — γ cleanup 8.5 (A4461B8F) removed _derive_red_paths_from_git.
# Symlink safety now delegated to git ls-files --others --exclude-standard in git_diff_files.
