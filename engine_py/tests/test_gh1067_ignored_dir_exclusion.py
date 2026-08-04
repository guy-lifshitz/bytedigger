"""RED tests (ported from hal) — registry scanners exclude git-ignored directories.

lib.scan_roots does not exist yet. All imports of the UUT are deferred
inside test bodies/helpers so this file COLLECTS cleanly and each test
FAILs at assert/import time (§1q ext).

Seam adaptations from the hal source (PORT_SPEC-style):
  - none: this port introduces no env-var seams (HAL_/BD_ n/a).
  - Reference: hal#1067.
"""
from __future__ import annotations

import subprocess

from helpers.engine_subprocess import engine_env
import sys
from pathlib import Path

ENGINE_PY_ROOT = Path(__file__).resolve().parent.parent


def _scan_roots():
    import importlib
    if str(ENGINE_PY_ROOT) not in sys.path:
        sys.path.insert(0, str(ENGINE_PY_ROOT))
    return importlib.import_module("bytedigger_engine.lib.scan_roots")


def _error_codes_mod():
    import importlib
    if str(ENGINE_PY_ROOT) not in sys.path:
        sys.path.insert(0, str(ENGINE_PY_ROOT))
    return importlib.import_module("bytedigger_engine.error_codes")


def _init_git_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=str(root), check=True, timeout=30)
    subprocess.run(
        ["git", "config", "user.email", "red@test.local"],
        cwd=str(root), check=True, timeout=30,
    )
    subprocess.run(
        ["git", "config", "user.name", "red-agent"],
        cwd=str(root), check=True, timeout=30,
    )


def _make_ignored_repo(tmp_path: Path) -> Path:
    """tmp git repo with .gitignore ignoring junkvenv/, containing x.py w/ fake code."""
    root = tmp_path.resolve()
    _init_git_repo(root)
    (root / ".gitignore").write_text("junkvenv/\n")
    junk = root / "junkvenv"
    junk.mkdir()
    (junk / "x.py").write_text('CODE = "E_TOTALLY_FAKE_GH1067"\n')
    return root


# ---------------------------------------------------------------------------
# AC1 — ignored dir excluded from both unregistered and dead sets
# ---------------------------------------------------------------------------

def test_ac1_ignored_dir_code_not_reported_unregistered_or_dead(tmp_path):
    root = _make_ignored_repo(tmp_path)
    m = _error_codes_mod()
    unregistered, dead = m.check(root)
    assert "E_TOTALLY_FAKE_GH1067" not in unregistered
    assert "E_TOTALLY_FAKE_GH1067" not in dead


# ---------------------------------------------------------------------------
# AC2 — untracked non-ignored file IS still reported (scan semantics preserved)
# ---------------------------------------------------------------------------

def test_ac2_untracked_non_ignored_file_still_reported_unregistered(tmp_path):
    root = _make_ignored_repo(tmp_path)
    (root / "newmod.py").write_text('CODE = "E_TOTALLY_FAKE_GH1067"\n')
    m = _error_codes_mod()
    unregistered, _dead = m.check(root)
    assert "E_TOTALLY_FAKE_GH1067" in unregistered


# ---------------------------------------------------------------------------
# AC3 — non-git tmp dir, dot-dir fallback prune (.venv-anything not harvested)
# ---------------------------------------------------------------------------

def test_ac3_non_git_dotdir_fallback_not_harvested(tmp_path):
    root = tmp_path.resolve()
    venv_dir = root / ".venv-anything"
    venv_dir.mkdir()
    (venv_dir / "bad.py").write_text('CODE = "E_TOTALLY_FAKE_GH1067_DOTDIR"\n')
    m = _error_codes_mod()
    unregistered, dead = m.check(root)
    assert "E_TOTALLY_FAKE_GH1067_DOTDIR" not in unregistered
    assert "E_TOTALLY_FAKE_GH1067_DOTDIR" not in dead


# ---------------------------------------------------------------------------
# AC4 — git_ignored_dirs: returns ignored dir names on git repo, empty on non-git
# ---------------------------------------------------------------------------

def test_ac4_git_ignored_dirs_returns_ignored_names(tmp_path):
    root = _make_ignored_repo(tmp_path)
    sr = _scan_roots()
    ignored = sr.git_ignored_dirs(root)
    assert isinstance(ignored, frozenset)
    assert "junkvenv" in ignored


def test_ac4_git_ignored_dirs_non_git_returns_empty_without_raising(tmp_path):
    root = tmp_path.resolve()
    (root / "not_a_repo_marker.txt").write_text("x")
    sr = _scan_roots()
    ignored = sr.git_ignored_dirs(root)
    assert ignored == frozenset()


# ---------------------------------------------------------------------------
# AC5 — production side-effect: real error_codes.py --check on real engine_py tree
# ---------------------------------------------------------------------------

def test_ac5_real_error_codes_check_exits_0_ok(tmp_path):
    result = subprocess.run(
        [sys.executable, str(ENGINE_PY_ROOT / "bytedigger_engine" / "error_codes.py"), "--check"], env=engine_env(),
        cwd=str(ENGINE_PY_ROOT),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, (
        f"expected exit 0, got {result.returncode}.\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "OK " in result.stdout


# ---------------------------------------------------------------------------
# AC6 — iter_prod_py_files excludes ignored dirs/tests/scripts/test_* files,
#        includes lib/scan_roots.py itself
# ---------------------------------------------------------------------------

def test_ac6_iter_prod_py_files_excludes_and_includes_expected(tmp_path):
    sr = _scan_roots()
    files = sr.iter_prod_py_files(ENGINE_PY_ROOT)
    scan_roots_path = ENGINE_PY_ROOT / "bytedigger_engine" / "lib" / "scan_roots.py"
    assert scan_roots_path in files

    for p in files:
        rel_parts = p.relative_to(ENGINE_PY_ROOT).parts
        assert "tests" not in rel_parts
        assert "__tests__" not in rel_parts
        assert "scripts" not in rel_parts
        assert not p.name.startswith("test_")

    ignored = sr.git_ignored_dirs(ENGINE_PY_ROOT)
    for p in files:
        rel_parts = p.relative_to(ENGINE_PY_ROOT).parts
        assert not any(seg in ignored for seg in rel_parts[:-1])


# ---------------------------------------------------------------------------
# AC7 — is_pruned_dir truth table
# ---------------------------------------------------------------------------

def test_ac7_is_pruned_dir_truth_table():
    sr = _scan_roots()
    ignored = frozenset({"junkvenv"})
    assert sr.is_pruned_dir(".foo", ignored) is True
    assert sr.is_pruned_dir("__pycache__", ignored) is True
    assert sr.is_pruned_dir("junkvenv", ignored) is True
    assert sr.is_pruned_dir("lib", ignored) is False
