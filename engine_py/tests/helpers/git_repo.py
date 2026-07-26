"""bd#102 M3 — shared fixture-repo init with a REPO-LOCAL git identity.

`git config --local` (not `--global`, not per-invocation `-c`) is required
because the product's own commits (`commit_red_tests` -> the engine's `git
commit`) run in a separate process that inherits none of a test's
per-invocation `-c` flags — only repo-local config travels with the repo.

`git init -q -b main` requires git >= 2.28 (`-b`); see docs/host-requirements.md.
"""
from __future__ import annotations

import subprocess


def init_repo(path: str) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=path, check=True)
    subprocess.run(
        ["git", "config", "--local", "user.email", "bd102@test.local"],
        cwd=path, check=True,
    )
    subprocess.run(
        ["git", "config", "--local", "user.name", "bd102 fixture"],
        cwd=path, check=True,
    )
    subprocess.run(
        ["git", "config", "--local", "commit.gpgsign", "false"],
        cwd=path, check=True,
    )
