"""RED tests for D8CB354F R4: .venv/venv pytest detection in _runner_for_path.

Today `_runner_for_path` takes only `path: str` — no `git_cwd` kwarg.
These 12 tests ALL FAIL pre-GREEN (TypeError on unexpected kwarg for
AC1/AC2/AC4/AC5/AC6/AC7/AC8/AC10/AC11/AC12; ValueError or wrong value
for the few remaining cases). AC3 and AC9 call WITHOUT git_cwd and
pass today as backward-compat guards (they protect the no-kwarg path
from regression post-GREEN).

Planned contract (not yet in production):
  _runner_for_path(path: str, git_cwd: str | None = None) -> dict | None
  _infer_test_command_for_paths(paths, git_cwd: str | None = None) -> dict

For the .py branch, if git_cwd is given:
  1. probe <git_cwd>/.venv/bin/pytest  (.is_file() AND os.access X_OK)
  2. probe <git_cwd>/venv/bin/pytest   (.is_file() AND os.access X_OK)
  First hit → argv_prefix = [str(that_path), "-x", "--tb=no", "-q"]
  No hit   → bare fallback: ["python3", "-m", "pytest", "-x", "--tb=no", "-q"]
Non-py extensions ignore git_cwd entirely.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "workflows"))

from phase_5_implement import _runner_for_path, _infer_test_command_for_paths  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_exec_pytest(base: Path) -> Path:
    """Create an executable pytest stub at base/bin/pytest and return its path."""
    bin_dir = base / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    p = bin_dir / "pytest"
    p.write_text("#!/bin/sh\nexec python3 -m pytest \"$@\"\n")
    os.chmod(p, 0o755)
    return p


# ---------------------------------------------------------------------------
# AC1 — .venv/bin/pytest preferred (forcing, exact full argv_prefix)
# ---------------------------------------------------------------------------

def test_ac1_venv_pytest_preferred(tmp_path):
    """_runner_for_path("x.py", git_cwd=tmp) with tmp/.venv/bin/pytest executable
    must return argv_prefix == [str(venv_pytest), "-x", "--tb=no", "-q"] EXACTLY."""
    venv_pytest = _make_exec_pytest(tmp_path / ".venv")

    result = _runner_for_path("x.py", git_cwd=str(tmp_path))

    assert result is not None, f"expected a runner dict, got None"
    assert result["kind"] == "py"
    assert result["argv_prefix"] == [str(venv_pytest), "-x", "--tb=no", "-q"], (
        f"AC1 FAIL: expected [str(venv_pytest), '-x', '--tb=no', '-q'], got {result['argv_prefix']!r}"
    )


# ---------------------------------------------------------------------------
# AC2 — no .venv present → bare fallback
# ---------------------------------------------------------------------------

def test_ac2_no_venv_bare_fallback(tmp_path):
    """git_cwd given but no .venv or venv under it → bare argv."""
    result = _runner_for_path("x.py", git_cwd=str(tmp_path))

    assert result is not None
    assert result["argv_prefix"] == ["python3", "-m", "pytest", "-x", "--tb=no", "-q"], (
        f"AC2 FAIL: expected bare argv, got {result['argv_prefix']!r}"
    )


# ---------------------------------------------------------------------------
# AC3 — no git_cwd → bare argv (backward-compat guard; PASSES today)
# ---------------------------------------------------------------------------

def test_ac3_no_git_cwd_bare_argv():
    """_runner_for_path('x.py') with no git_cwd → bare argv unchanged."""
    result = _runner_for_path("x.py")

    assert result is not None
    assert result["argv_prefix"] == ["python3", "-m", "pytest", "-x", "--tb=no", "-q"], (
        f"AC3 FAIL: backward-compat broken, got {result['argv_prefix']!r}"
    )


# ---------------------------------------------------------------------------
# AC4 — only venv/ (no leading dot) executable
# ---------------------------------------------------------------------------

def test_ac4_nodot_venv_used(tmp_path):
    """Only tmp/venv/bin/pytest exists (no .venv) → that path wins."""
    venv_pytest = _make_exec_pytest(tmp_path / "venv")

    result = _runner_for_path("x.py", git_cwd=str(tmp_path))

    assert result is not None
    assert result["argv_prefix"][0] == str(venv_pytest), (
        f"AC4 FAIL: expected {str(venv_pytest)!r}, got {result['argv_prefix'][0]!r}"
    )


# ---------------------------------------------------------------------------
# AC5 — .venv/bin/pytest exists but NOT executable → bare fallback
# ---------------------------------------------------------------------------

def test_ac5_non_executable_falls_back(tmp_path):
    """.venv/bin/pytest with mode 644 (not executable) → bare argv."""
    bin_dir = tmp_path / ".venv" / "bin"
    bin_dir.mkdir(parents=True)
    p = bin_dir / "pytest"
    p.write_text("#!/bin/sh\nexec python3 -m pytest \"$@\"\n")
    os.chmod(p, 0o644)  # readable but NOT executable

    result = _runner_for_path("x.py", git_cwd=str(tmp_path))

    assert result is not None
    assert result["argv_prefix"] == ["python3", "-m", "pytest", "-x", "--tb=no", "-q"], (
        f"AC5 FAIL: non-executable .venv pytest should fall back to bare argv, got {result['argv_prefix']!r}"
    )


# ---------------------------------------------------------------------------
# AC6 — BOTH .venv and venv executable → .venv wins (precedence, forcing exact)
# ---------------------------------------------------------------------------

def test_ac6_dotted_venv_takes_precedence(tmp_path):
    """Both .venv/bin/pytest AND venv/bin/pytest executable → .venv wins."""
    dot_venv_pytest = _make_exec_pytest(tmp_path / ".venv")
    _make_exec_pytest(tmp_path / "venv")  # also present

    result = _runner_for_path("x.py", git_cwd=str(tmp_path))

    assert result is not None
    assert result["argv_prefix"][0] == str(dot_venv_pytest), (
        f"AC6 FAIL: .venv should take precedence, got {result['argv_prefix'][0]!r}"
    )


# ---------------------------------------------------------------------------
# AC7 — non-py (.ts) ignores git_cwd
# ---------------------------------------------------------------------------

def test_ac7_ts_ignores_git_cwd(tmp_path):
    """_runner_for_path('x.ts', git_cwd=...) → ["bun", "test"] regardless of venv."""
    _make_exec_pytest(tmp_path / ".venv")  # venv present but irrelevant for .ts

    result = _runner_for_path("x.ts", git_cwd=str(tmp_path))

    assert result is not None
    assert result["argv_prefix"] == ["bun", "test"], (
        f"AC7 FAIL: .ts should always return ['bun','test'], got {result['argv_prefix']!r}"
    )


# ---------------------------------------------------------------------------
# AC8 — _infer_test_command_for_paths threads git_cwd (venv used in groups)
# ---------------------------------------------------------------------------

def test_ac8_infer_threads_git_cwd(tmp_path):
    """_infer_test_command_for_paths(['a.py'], git_cwd=...) → groups[0].argv[0] == venv pytest."""
    venv_pytest = _make_exec_pytest(tmp_path / ".venv")

    result = _infer_test_command_for_paths(["a.py"], git_cwd=str(tmp_path))

    assert "groups" in result, f"AC8 FAIL: expected 'groups', got {result!r}"
    py_group = next((g for g in result["groups"] if g["kind"] == "py"), None)
    assert py_group is not None, f"AC8 FAIL: no py group in {result['groups']!r}"
    assert py_group["argv"][0] == str(venv_pytest), (
        f"AC8 FAIL: expected argv[0]=={str(venv_pytest)!r}, got {py_group['argv'][0]!r}"
    )


# ---------------------------------------------------------------------------
# AC9 — no git_cwd → bare argv (backward-compat guard; PASSES today)
# ---------------------------------------------------------------------------

def test_ac9_infer_no_git_cwd_bare_argv():
    """_infer_test_command_for_paths(['a.py']) with no git_cwd → bare argv unchanged."""
    result = _infer_test_command_for_paths(["a.py"])

    assert "groups" in result, f"AC9 FAIL: got {result!r}"
    py_group = next((g for g in result["groups"] if g["kind"] == "py"), None)
    assert py_group is not None
    assert py_group["argv"] == ["python3", "-m", "pytest", "-x", "--tb=no", "-q", "a.py"], (
        f"AC9 FAIL: backward-compat broken, got {py_group['argv']!r}"
    )


# ---------------------------------------------------------------------------
# AC10 — mixed .py + .ts with git_cwd: py uses venv, ts stays ["bun","test",...]
# ---------------------------------------------------------------------------

def test_ac10_mixed_py_ts_with_git_cwd(tmp_path):
    """Mixed py+ts: py group uses venv pytest, ts group stays bun test."""
    venv_pytest = _make_exec_pytest(tmp_path / ".venv")

    result = _infer_test_command_for_paths(["a.py", "b.ts"], git_cwd=str(tmp_path))

    assert "groups" in result, f"AC10 FAIL: got {result!r}"
    py_group = next((g for g in result["groups"] if g["kind"] == "py"), None)
    ts_group = next((g for g in result["groups"] if g["kind"] == "ts"), None)
    assert py_group is not None, "AC10 FAIL: no py group"
    assert ts_group is not None, "AC10 FAIL: no ts group"
    assert py_group["argv"][0] == str(venv_pytest), (
        f"AC10 FAIL: py group argv[0] expected {str(venv_pytest)!r}, got {py_group['argv'][0]!r}"
    )
    assert ts_group["argv"] == ["bun", "test", "b.ts"], (
        f"AC10 FAIL: ts group argv expected ['bun','test','b.ts'], got {ts_group['argv']!r}"
    )


# ---------------------------------------------------------------------------
# AC11 — .venv/bin/pytest exists as a DIRECTORY → bare fallback (.is_file() guard)
# ---------------------------------------------------------------------------

def test_ac11_pytest_is_directory_falls_back(tmp_path):
    """tmp/.venv/bin/pytest is a DIRECTORY (not a file) → .is_file() fails → bare argv."""
    pytest_as_dir = tmp_path / ".venv" / "bin" / "pytest"
    pytest_as_dir.mkdir(parents=True)  # it's a directory, not a file

    result = _runner_for_path("x.py", git_cwd=str(tmp_path))

    assert result is not None
    assert result["argv_prefix"] == ["python3", "-m", "pytest", "-x", "--tb=no", "-q"], (
        f"AC11 FAIL: pytest-as-directory should fall back to bare argv, got {result['argv_prefix']!r}"
    )


# ---------------------------------------------------------------------------
# AC12 — full argv_prefix exact (forcing): trailing flags preserved exactly
# ---------------------------------------------------------------------------

def test_ac12_full_argv_prefix_exact(tmp_path):
    """AC1 scenario: confirm the FULL argv_prefix list is exactly
    [str(venv_pytest), '-x', '--tb=no', '-q'] — no extra or missing tokens."""
    venv_pytest = _make_exec_pytest(tmp_path / ".venv")

    result = _runner_for_path("x.py", git_cwd=str(tmp_path))

    assert result is not None
    expected = [str(venv_pytest), "-x", "--tb=no", "-q"]
    assert result["argv_prefix"] == expected, (
        f"AC12 FAIL: full argv_prefix mismatch.\n"
        f"  expected: {expected!r}\n"
        f"  got:      {result['argv_prefix']!r}"
    )
