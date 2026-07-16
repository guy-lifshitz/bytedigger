"""RED tests for 4B50D02B — lib/project_root._git_toplevel routes through git_port.git_read.

Spec: SHARED/memory/Decisions/2026-06-21_4B50D02B_project_root_git_read_spec.md §3

Pre-GREEN predict:
  AC1: FAILS — spy never hit (bounded_run still used, set_default_git_read_factory has no effect)
  AC2: FAILS — same reason; spy not invoked
  AC3: FAILS — same reason
  AC4: FAILS — same reason
  AC5: FAILS — same reason (OSError not triggered via spy)
  AC6: FAILS — prod file still contains 'bounded_run' and lacks 'from lib.git_port import git_read'
  AC7: PASSES — uses real subprocess through existing bounded_run path (regression still works)

§1i (singleton/time): set_default_git_read_factory/reset_default_git_read_factory used in
teardown fixture — spy never leaks across tests (§1i singleton-reset cited per workflows.md §1i).

COLLECTABILITY (D1CF5FDF / §1q): all imported symbols exist today (_git_toplevel, git_read,
set_default_git_read_factory, GitResult) — imports at module level are safe. File COLLECTS
and FAILS on assertions, never hangs.

Do NOT mutate sys.path here — conftest.py provides the engine_py root at import-time (§1q /
966E571B). No sys.path.insert() anywhere in this file.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import pytest

# All imported symbols exist in the current production code — safe at module level.
from lib.git_port import (  # type: ignore[import]
    GitResult,
    set_default_git_read_factory,
    reset_default_git_read_factory,
)
from lib.project_root import _git_toplevel, resolve_project_root  # type: ignore[import]


# ─── Shared helpers ────────────────────────────────────────────────────────────

def _make_real_git_repo() -> Path:
    """Create a temp dir, git init it, add a minimal commit. Returns resolved Path.

    Uses subprocess.run for fixture setup only (not the unit under test).
    §1j macOS: Path.resolve() to normalise /var/folders symlinks.
    """
    d = tempfile.mkdtemp()
    p = Path(os.path.realpath(d))  # macOS /var → /private/var realpath
    subprocess.run(["git", "init", "-q"], cwd=str(p), check=True)
    subprocess.run(["git", "config", "user.email", "test@test.test"], cwd=str(p), check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=str(p), check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=str(p), check=True)
    (p / ".gitkeep").write_text("")
    subprocess.run(["git", "add", ".gitkeep"], cwd=str(p), check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=str(p), check=True)
    return p


@pytest.fixture(autouse=False)
def reset_git_read_factory():
    """Teardown-only: always reset the factory after spy tests (§1i singleton-reset)."""
    yield
    reset_default_git_read_factory()


# ─── AC1: spy hit exactly once with correct args ───────────────────────────────

def test_ac1_spy_called_once_with_correct_args_4B50D02B(reset_git_read_factory):
    """AC1: _git_toplevel(Path("/x")) hits git_read exactly once with
    args=["rev-parse","--show-toplevel"], dir_="/x", timeout=5.

    Pre-GREEN FAILS: bounded_run is still used; the injected spy is never called.
    """
    calls: list[dict] = []

    def spy(args, *, cwd=None, timeout=None, dir_=None) -> GitResult:
        calls.append({"args": args, "cwd": cwd, "timeout": timeout, "dir_": dir_})
        return GitResult(returncode=0, stdout="/repo/top\n", stderr="", timed_out=False)

    set_default_git_read_factory(lambda: spy)

    _git_toplevel(Path("/x"))

    assert len(calls) == 1, (
        f"Expected git_read spy hit exactly once, got {len(calls)} calls: {calls!r}. "
        "Pre-GREEN: bounded_run still used — spy never hit."
    )
    call = calls[0]
    assert call["args"] == ["rev-parse", "--show-toplevel"], (
        f"args mismatch: {call['args']!r}"
    )
    assert call["dir_"] == "/x", (
        f"dir_ mismatch: expected '/x', got {call['dir_']!r}"
    )
    assert call["timeout"] == 5, (
        f"timeout mismatch: expected 5, got {call['timeout']!r}"
    )


# ─── AC2: rc=0 + stdout → resolved Path ───────────────────────────────────────

def test_ac2_rc0_returns_resolved_path_4B50D02B(reset_git_read_factory):
    """AC2: spy returns rc=0, stdout="/repo/top\\n" → _git_toplevel returns Path("/repo/top").resolve().

    Pre-GREEN FAILS: bounded_run used; spy not invoked; real git call made instead.
    """
    def spy(args, *, cwd=None, timeout=None, dir_=None) -> GitResult:
        return GitResult(returncode=0, stdout="/repo/top\n", stderr="", timed_out=False)

    set_default_git_read_factory(lambda: spy)

    result = _git_toplevel(Path("/x"))

    expected = Path("/repo/top").resolve()
    assert result == expected, (
        f"Expected {expected!r}, got {result!r}. "
        "Pre-GREEN: spy not hit; bounded_run runs real git on /x → different result or None."
    )


# ─── AC3: rc=128 → None ───────────────────────────────────────────────────────

def test_ac3_rc128_returns_none_4B50D02B(reset_git_read_factory):
    """AC3: spy returns rc=128 → _git_toplevel returns None.

    Pre-GREEN FAILS: spy not used; real git called; outcome depends on whether /x is a repo.
    """
    def spy(args, *, cwd=None, timeout=None, dir_=None) -> GitResult:
        return GitResult(returncode=128, stdout="", stderr="fatal: not a git repo", timed_out=False)

    set_default_git_read_factory(lambda: spy)

    result = _git_toplevel(Path("/tmp"))  # /tmp may be a real git repo; spy must win

    assert result is None, (
        f"Expected None for rc=128, got {result!r}. "
        "Pre-GREEN: spy not invoked; real git determines the return value."
    )


# ─── AC4: rc=0 blank stdout → None ────────────────────────────────────────────

def test_ac4_rc0_blank_stdout_returns_none_4B50D02B(reset_git_read_factory):
    """AC4: spy returns rc=0 but stdout="   \\n" (blank) → returns None.

    Pre-GREEN FAILS: spy not invoked.
    """
    def spy(args, *, cwd=None, timeout=None, dir_=None) -> GitResult:
        return GitResult(returncode=0, stdout="   \n", stderr="", timed_out=False)

    set_default_git_read_factory(lambda: spy)

    result = _git_toplevel(Path("/x"))

    assert result is None, (
        f"Expected None for rc=0 blank stdout, got {result!r}. "
        "Pre-GREEN: spy not hit; real git result used."
    )


# ─── AC5: spy raises OSError → caught, returns None ──────────────────────────

def test_ac5_oserror_caught_returns_none_4B50D02B(reset_git_read_factory):
    """AC5: spy raises OSError("boom") → caught by except Exception → returns None.

    Pre-GREEN FAILS: spy not invoked; real git subprocess runs instead.
    """
    def spy(args, *, cwd=None, timeout=None, dir_=None) -> GitResult:
        raise OSError("boom")

    set_default_git_read_factory(lambda: spy)

    result = _git_toplevel(Path("/x"))

    assert result is None, (
        f"Expected None after OSError, got {result!r}. "
        "Pre-GREEN: spy not invoked; no OSError raised."
    )


# ─── AC6: source-grep — prod file contains git_read, zero bounded_run etc. ────

def test_ac6_source_grep_4B50D02B():
    """AC6: lib/project_root.py source must contain 'from lib.git_port import git_read'
    and must NOT contain 'bounded_run', 'import subprocess', or raw '["git"' literal.

    Pre-GREEN FAILS: prod file still contains 'bounded_run' (L20, L41) and lacks git_read import.
    Post-GREEN PASSES: import swapped, call replaced, dead subprocess import removed.

    This is a behavior-forcing source grep (§1l): the absence of bounded_run in the source
    is a structural proxy for the routing change — the file cannot call bounded_run if it
    is not imported/referenced.
    """
    import importlib.util

    spec = importlib.util.find_spec("lib.project_root")
    assert spec is not None and spec.origin is not None, "lib.project_root not found"

    source = Path(spec.origin).read_text(encoding="utf-8")

    assert "from lib.git_port import git_read" in source, (
        "FAIL: 'from lib.git_port import git_read' not found in lib/project_root.py. "
        "Pre-GREEN expected: import not yet added."
    )
    assert "bounded_run" not in source, (
        "FAIL: 'bounded_run' still present in lib/project_root.py. "
        "Pre-GREEN expected: import and call site not yet replaced."
    )
    assert "import subprocess" not in source, (
        "FAIL: dead 'import subprocess' still present in lib/project_root.py. "
        "Pre-GREEN expected: not yet removed."
    )
    assert '["git"' not in source, (
        "FAIL: raw '[\"git\"' literal still present in lib/project_root.py. "
        "Pre-GREEN expected: raw git spawn not yet removed."
    )


# ─── AC7: regression — real git repo via real subprocess path ─────────────────

def test_ac7_regression_real_git_repo_4B50D02B():
    """AC7: resolve_project_root({}, cwd=<real tmp git repo>) → (repo_realpath, "git_toplevel")
    via real subprocess path (NO spy installed).

    Pre-GREEN PASSES: bounded_run still used, which spawns real git → correct result.
    Post-GREEN PASSES: git_read routes to _git_read_subprocess → same real git result.

    This is a regression guard ensuring the routing change preserves end-to-end behavior.
    §1j: compare Path.resolve() on both sides (macOS /var/folders symlinks).
    """
    repo = _make_real_git_repo()

    resolved_path, source = resolve_project_root({}, cwd=repo)

    assert source == "git_toplevel", (
        f"Expected source='git_toplevel', got {source!r}"
    )

    expected = Path(
        subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    ).resolve()

    assert resolved_path == expected, (
        f"Expected toplevel {expected!r}, got {resolved_path!r}"
    )
