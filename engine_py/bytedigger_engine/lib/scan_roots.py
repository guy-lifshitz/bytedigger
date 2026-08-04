"""Shared file-enumeration helper for registry scanners (hal#1067).

Chokepoint for excluding git-ignored directories from production-code
scans so stray venvs/build artifacts never pollute error-code harvests
or lint file lists. Host-decoupled: the one git read routes through the
injectable ``lib.git_port`` seam (D52228C3 §2.10).
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from bytedigger_engine.lib import git_port  # D52228C3 §2.10 — reads route through the injectable git seam

SEMANTIC_EXCLUDE_DIRS: frozenset[str] = frozenset({"tests", "__tests__", "scripts"})


def git_ignored_dirs(root: Path) -> frozenset[str]:
    """Return top-relative dir names ignored by git under root.

    Runs one `git ls-files --others --ignored --exclude-standard --directory`
    call. Any failure (not a repo, git missing, timeout, non-zero exit)
    returns an empty frozenset — never raises.
    """
    try:
        result = git_port.git_read(
            ["ls-files", "--others", "--ignored",
             "--exclude-standard", "--directory"],
            dir_=str(root),
            timeout=30,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return frozenset()

    if result.returncode != 0:
        return frozenset()

    names: set[str] = set()
    for line in result.stdout.splitlines():
        line = line.strip()
        if not line.endswith("/"):
            continue
        rel = line[:-1]
        parts = rel.split("/")
        if len(parts) == 1:
            names.add(parts[0])
    return frozenset(names)


def is_pruned_dir(name: str, ignored: frozenset[str]) -> bool:
    """True if a dir name should be pruned from a walk."""
    return name in ignored or name.startswith(".") or name == "__pycache__"


def iter_prod_py_files(root: Path) -> list[Path]:
    """Enumerate production .py files under root.

    Prunes git-ignored dirs, dot-dirs, __pycache__, and semantic
    exclude dirs (tests/__tests__/scripts). Skips test_*.py files.
    """
    root = Path(root)
    ignored = git_ignored_dirs(root)
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames
            if not is_pruned_dir(d, ignored) and d not in SEMANTIC_EXCLUDE_DIRS
        ]
        for fname in filenames:
            if not fname.endswith(".py"):
                continue
            if fname.startswith("test_"):
                continue
            files.append(Path(dirpath) / fname)
    return files
