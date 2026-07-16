"""RED tests for FDDEE42A — route disk_truth/git_diff._run_git through git_port.git_read.

Expected RED state (before GREEN):
  FAIL — AC2 test_ac2_run_git_routes_through_git_port_seam
         (_run_git still calls raw bounded_run; injected factory sentinel not returned)
  PASS — AC3 test_ac3_default_factory_diff_files_preserved  (behavior-preservation guard)
  PASS — AC4 test_ac4_resolve_pre_phase_sha_real_and_nonrepo (behavior-preservation guard)
  FAIL — AC5 test_ac5_import_subprocess_removed
         (import subprocess still present in git_diff.py)
"""
from __future__ import annotations

import os
import re
import subprocess
import tempfile
from pathlib import Path

import pytest

import lib.plugins.disk_truth.git_diff as git_diff
from lib.git_port import (
    GitResult,
    reset_default_git_read_factory,
    set_default_git_read_factory,
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _engine_py_root() -> Path:
    """Return the engine_py root (parent of the tests/ directory)."""
    return Path(__file__).resolve().parents[1]


def _make_git_repo(tmp_dir: str) -> str:
    """Initialise a minimal git repo in tmp_dir and return its path."""
    subprocess.run(["git", "init", tmp_dir], check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        check=True, cwd=tmp_dir, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        check=True, cwd=tmp_dir, capture_output=True,
    )
    # initial commit
    a_txt = Path(tmp_dir) / "a.txt"
    a_txt.write_text("hello\n")
    subprocess.run(["git", "add", "a.txt"], check=True, cwd=tmp_dir, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        check=True, cwd=tmp_dir, capture_output=True,
    )
    return tmp_dir


# ---------------------------------------------------------------------------
# AC2 — forcing function: _run_git routes through the central git_port seam
# ---------------------------------------------------------------------------

def test_ac2_run_git_routes_through_git_port_seam():
    """Inject a fake reader via the CENTRAL git_port seam (not patching _run_git itself).

    FAILS now: _run_git calls raw bounded_run, never touches the injected factory.
    After GREEN: _run_git calls git_read() which calls get_git_read() which returns
    the injected fake, so result.stdout == "SENTINEL_SHA\\n".
    """
    sentinel = GitResult(
        returncode=0,
        stdout="SENTINEL_SHA\n",
        stderr="",
        timed_out=False,
    )
    set_default_git_read_factory(
        lambda: (lambda args, *, cwd=None, timeout=None, dir_=None: sentinel)
    )
    try:
        result = git_diff._run_git(["rev-parse", "HEAD"], "/tmp")
        assert result.stdout == "SENTINEL_SHA\n", (
            f"_run_git did not route through the git_port seam: "
            f"expected stdout='SENTINEL_SHA\\n', got {result.stdout!r}. "
            "This means _run_git still calls raw bounded_run instead of git_read."
        )
    finally:
        reset_default_git_read_factory()


# ---------------------------------------------------------------------------
# AC3 — behavior-preservation guard (PASSES before and after GREEN)
# ---------------------------------------------------------------------------

def test_ac3_default_factory_diff_files_preserved():
    """With default factory, git_diff_files returns untracked files in a real repo.

    PASSES throughout (behavior-preservation guard — must not regress after GREEN).
    """
    with tempfile.TemporaryDirectory() as tmp_dir:
        _make_git_repo(tmp_dir)

        # Capture HEAD SHA to use as pre_sha
        r = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            check=True, cwd=tmp_dir, capture_output=True, text=True,
        )
        head_sha = r.stdout.strip()

        # Create an untracked file
        untracked = Path(tmp_dir) / "b.txt"
        untracked.write_text("untracked\n")

        files = git_diff.git_diff_files(head_sha, tmp_dir, untracked=True)
        assert "b.txt" in files, (
            f"Expected 'b.txt' in git_diff_files result, got: {files}"
        )


# ---------------------------------------------------------------------------
# AC4 — resolve_pre_phase_sha guard (PASSES throughout)
# ---------------------------------------------------------------------------

def test_ac4_resolve_pre_phase_sha_real_and_nonrepo():
    """resolve_pre_phase_sha returns 40-hex on a real repo; raises RuntimeError on a non-repo.

    PASSES throughout (behavior-preservation guard — must not regress after GREEN).
    """
    # Real repo case
    with tempfile.TemporaryDirectory() as tmp_dir:
        _make_git_repo(tmp_dir)
        sha = git_diff.resolve_pre_phase_sha(tmp_dir)
        assert len(sha) == 40, (
            f"Expected 40-char hex SHA, got {sha!r} (len={len(sha)})"
        )
        assert all(c in "0123456789abcdefABCDEF" for c in sha), (
            f"SHA is not all hex: {sha!r}"
        )

    # Non-repo case
    with tempfile.TemporaryDirectory() as nonrepo_dir:
        with pytest.raises(RuntimeError):
            git_diff.resolve_pre_phase_sha(nonrepo_dir)


# ---------------------------------------------------------------------------
# AC5 — import subprocess removed (FAILS now; source still has "import subprocess")
# ---------------------------------------------------------------------------

def test_ac5_import_subprocess_removed():
    """git_diff.py must NOT contain a top-level 'import subprocess' after GREEN.

    FAILS now: the line 'import subprocess' is present in the module source.
    After GREEN: the import is removed (dead after return-annotation change).
    """
    git_diff_path = (
        _engine_py_root() / "lib" / "plugins" / "disk_truth" / "git_diff.py"
    )
    source = git_diff_path.read_text(encoding="utf-8")
    match = re.search(r"^import subprocess$", source, re.MULTILINE)
    assert match is None, (
        f"Found top-level 'import subprocess' in {git_diff_path} — "
        "this import must be removed as part of the git_port seam migration (FDDEE42A §1.2). "
        f"Found at position {match.start() if match else 'n/a'} in source."
    )
