"""RED tests for BF7890C8 — probe parent-repo .venv before system-python3 fallback.

Spec: SHARED/memory/Decisions/2026-06-10_BF7890C8_red-collect-probe-parent-venv_spec.md
Agreement: BF7890C8-A928-49F1-85DC-2E7414AA0DD5

Problem: _runner_for_path probes only git_cwd for a venv. Under a git worktree
(worktree has no .venv, main checkout has .venv/bin/pytest) the prefix stays
["python3","-m","pytest",...] which hangs ~1h when global pytest is absent.

Fix (not yet implemented):
  - New helper _venv_pytest(base) -> str|None
  - New helper _main_checkout_root(git_cwd) -> str|None
  - _runner_for_path .py branch: probe git_cwd venv → then main-checkout venv → fallback

These tests FAIL against current code (helpers absent, branch not rewired).
They PASS once GREEN adds the two helpers and rewires the .py branch.

COLLECTABILITY NOTE (D1CF5FDF / §1q):
  - NO top-level import of _venv_pytest / _main_checkout_root — they don't exist yet.
  - sys.path is set by conftest.py (conftest-import-time singleton, §1q / 81F97F3D).
  - 'import bytedigger_engine.workflows.phase_5_implement' is deferred to inside each test body so missing
    attrs raise AttributeError at ASSERT time, never at collection time.
"""
from __future__ import annotations

import math
import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_executable_pytest(base: Path, variant: str = ".venv") -> Path:
    """Create <base>/<variant>/bin/pytest as a chmod +x stub."""
    p = base / variant / "bin" / "pytest"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("#!/bin/sh\nexec python3 -m pytest \"$@\"\n")
    os.chmod(p, 0o755)
    return p


def _git_init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)
    (repo / "placeholder.py").write_text("# placeholder\n")
    subprocess.run(["git", "add", "placeholder.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)


def _skip_if_no_git() -> None:
    """Skip the test when git is unavailable, per the FROZEN availability map.

    bd#49: this used to call `shutil.which("git")` live. That contradicted the
    invariant `helpers/host_tools.py` documents for itself — two tests
    monkeypatch `shutil.which` process-wide, so a live lookup can report a tool
    absent on a machine that has it. Delegating to `skip_without` reads the map
    frozen once at `pytest_configure`, which no monkeypatch can spoof.
    """
    from helpers.host_tools import skip_without  # noqa: PLC0415

    skip_without("git")


# ---------------------------------------------------------------------------
# AC1 — _venv_pytest returns <base>/.venv/bin/pytest when file exists + X_OK
# ---------------------------------------------------------------------------


def test_ac1_venv_pytest_returns_dot_venv_path(tmp_path):
    """AC1: _venv_pytest(base) -> '<base>/.venv/bin/pytest' when that file exists and is executable."""
    from bytedigger_engine.workflows import phase_5_implement  # noqa: PLC0415

    assert hasattr(phase_5_implement, "_venv_pytest"), (
        "phase_5_implement._venv_pytest does not exist — GREEN must add this helper"
    )

    base = tmp_path / "myrepo"
    expected = _make_executable_pytest(base, ".venv")

    result = phase_5_implement._venv_pytest(str(base))
    assert result is not None, (
        "_venv_pytest returned None; expected the .venv/bin/pytest path"
    )
    assert os.path.realpath(result) == os.path.realpath(str(expected)), (
        f"_venv_pytest returned {result!r}, expected realpath-equivalent of {expected!r}"
    )


# ---------------------------------------------------------------------------
# AC2 — _venv_pytest prefers .venv over venv when both exist
# ---------------------------------------------------------------------------


def test_ac2_venv_pytest_prefers_dot_venv_over_venv(tmp_path):
    """AC2: when both .venv/bin/pytest and venv/bin/pytest exist, .venv wins."""
    from bytedigger_engine.workflows import phase_5_implement  # noqa: PLC0415

    assert hasattr(phase_5_implement, "_venv_pytest"), (
        "phase_5_implement._venv_pytest does not exist — GREEN must add this helper"
    )

    base = tmp_path / "myrepo"
    dot_venv_path = _make_executable_pytest(base, ".venv")
    _make_executable_pytest(base, "venv")

    result = phase_5_implement._venv_pytest(str(base))
    assert result is not None, "_venv_pytest returned None; expected .venv/bin/pytest"
    assert os.path.realpath(result) == os.path.realpath(str(dot_venv_path)), (
        f"_venv_pytest returned {result!r}; expected the .venv path {dot_venv_path!r} (priority order wrong)"
    )


# ---------------------------------------------------------------------------
# AC3 — _venv_pytest returns None when no venv present
# ---------------------------------------------------------------------------


def test_ac3_venv_pytest_returns_none_when_no_venv(tmp_path):
    """AC3: _venv_pytest(empty_dir) -> None (no venv)."""
    from bytedigger_engine.workflows import phase_5_implement  # noqa: PLC0415

    assert hasattr(phase_5_implement, "_venv_pytest"), (
        "phase_5_implement._venv_pytest does not exist — GREEN must add this helper"
    )

    empty = tmp_path / "empty_repo"
    empty.mkdir()

    result = phase_5_implement._venv_pytest(str(empty))
    assert result is None, (
        f"_venv_pytest returned {result!r} for an empty dir; expected None"
    )


# ---------------------------------------------------------------------------
# AC4 — _main_checkout_root returns main-repo root for a real git worktree
# ---------------------------------------------------------------------------


def test_ac4_main_checkout_root_returns_main_for_worktree(tmp_path):
    """AC4: _main_checkout_root(worktree_path) -> realpath of main checkout root."""
    _skip_if_no_git()

    from bytedigger_engine.workflows import phase_5_implement  # noqa: PLC0415

    assert hasattr(phase_5_implement, "_main_checkout_root"), (
        "phase_5_implement._main_checkout_root does not exist — GREEN must add this helper"
    )

    main_repo = tmp_path / "main_repo"
    _git_init_repo(main_repo)

    worktree = tmp_path / "worktree_a"
    subprocess.run(
        ["git", "worktree", "add", str(worktree), "-b", "feat-bf7890c8"],
        cwd=main_repo,
        check=True,
    )

    result = phase_5_implement._main_checkout_root(str(worktree))
    assert result is not None, (
        f"_main_checkout_root({worktree!s}) returned None; expected the main checkout root"
    )
    expected = os.path.realpath(str(main_repo))
    actual = os.path.realpath(result)
    assert actual == expected, (
        f"_main_checkout_root returned {actual!r}; expected main checkout {expected!r}"
    )


# ---------------------------------------------------------------------------
# AC5 — _main_checkout_root returns None when git_cwd IS the main checkout
# ---------------------------------------------------------------------------


def test_ac5_main_checkout_root_returns_none_for_main_repo(tmp_path):
    """AC5: _main_checkout_root(main_repo) -> None (not a worktree)."""
    _skip_if_no_git()

    from bytedigger_engine.workflows import phase_5_implement  # noqa: PLC0415

    assert hasattr(phase_5_implement, "_main_checkout_root"), (
        "phase_5_implement._main_checkout_root does not exist — GREEN must add this helper"
    )

    main_repo = tmp_path / "main_repo"
    _git_init_repo(main_repo)

    result = phase_5_implement._main_checkout_root(str(main_repo))
    assert result is None, (
        f"_main_checkout_root on a plain repo returned {result!r}; expected None (not a worktree)"
    )


# ---------------------------------------------------------------------------
# AC6 — _main_checkout_root returns None for a non-git directory (fail-soft)
# ---------------------------------------------------------------------------


def test_ac6_main_checkout_root_returns_none_for_non_git_dir(tmp_path):
    """AC6: _main_checkout_root(non_git_dir) -> None, no exception raised."""
    from bytedigger_engine.workflows import phase_5_implement  # noqa: PLC0415

    assert hasattr(phase_5_implement, "_main_checkout_root"), (
        "phase_5_implement._main_checkout_root does not exist — GREEN must add this helper"
    )

    non_git = tmp_path / "not_a_repo"
    non_git.mkdir()

    # Must not raise — fail-soft contract
    try:
        result = phase_5_implement._main_checkout_root(str(non_git))
    except Exception as exc:  # noqa: BLE001
        raise AssertionError(
            f"_main_checkout_root raised {type(exc).__name__}: {exc!r} "
            "on a non-git dir; must fail-soft to None"
        ) from exc

    assert result is None, (
        f"_main_checkout_root returned {result!r} for a non-git dir; expected None"
    )


# ---------------------------------------------------------------------------
# AC7 — [core regression] _runner_for_path uses main-repo venv from worktree
# ---------------------------------------------------------------------------


def test_ac7_runner_for_path_uses_main_repo_venv_from_worktree(tmp_path):
    """AC7 (core regression): worktree has NO venv, main checkout HAS .venv/bin/pytest
    → _runner_for_path(<pyfile>, worktree) must return argv_prefix[0] == main_repo/.venv/bin/pytest.
    """
    _skip_if_no_git()

    from bytedigger_engine.workflows import phase_5_implement  # noqa: PLC0415

    main_repo = tmp_path / "main_repo"
    _git_init_repo(main_repo)

    # Plant .venv/bin/pytest in main repo only (not in worktree)
    main_pytest = _make_executable_pytest(main_repo, ".venv")

    worktree = tmp_path / "worktree_b"
    subprocess.run(
        ["git", "worktree", "add", str(worktree), "-b", "feat-ac7"],
        cwd=main_repo,
        check=True,
    )
    # Confirm worktree has NO venv
    assert not (worktree / ".venv").exists(), "fixture error: worktree should not have .venv"

    result = phase_5_implement._runner_for_path("tests/test_foo.py", str(worktree))
    assert result is not None, "_runner_for_path returned None for a .py path"
    assert result["kind"] == "py", f"expected kind='py', got {result['kind']!r}"

    prefix = result["argv_prefix"]
    assert len(prefix) > 0, "argv_prefix is empty"
    actual_pytest = os.path.realpath(prefix[0])
    expected_pytest = os.path.realpath(str(main_pytest))
    assert actual_pytest == expected_pytest, (
        f"argv_prefix[0]={prefix[0]!r} (realpath={actual_pytest!r}); "
        f"expected main-repo .venv pytest {expected_pytest!r}. "
        "Worktree venv fallback to main checkout not implemented yet."
    )


# ---------------------------------------------------------------------------
# AC8 — _runner_for_path still uses git_cwd's own .venv when present (no regression)
# ---------------------------------------------------------------------------


def test_ac8_runner_for_path_uses_own_venv_when_present(tmp_path):
    """AC8: git_cwd has its own .venv/bin/pytest → argv_prefix[0] is that path."""
    from bytedigger_engine.workflows import phase_5_implement  # noqa: PLC0415

    git_cwd = tmp_path / "my_repo"
    own_pytest = _make_executable_pytest(git_cwd, ".venv")

    result = phase_5_implement._runner_for_path("tests/test_thing.py", str(git_cwd))
    assert result is not None, "_runner_for_path returned None for a .py path"
    assert result["kind"] == "py"

    prefix = result["argv_prefix"]
    actual = os.path.realpath(prefix[0])
    expected = os.path.realpath(str(own_pytest))
    assert actual == expected, (
        f"argv_prefix[0]={prefix[0]!r}; expected own .venv pytest {expected!r}"
    )


# ---------------------------------------------------------------------------
# AC9 — _runner_for_path falls back to python3 -m pytest when neither has a venv
# ---------------------------------------------------------------------------


def test_ac9_runner_for_path_falls_back_to_python3_when_no_venv(tmp_path):
    """AC9: worktree + main repo both venv-less → prefix = ['python3','-m','pytest',...].

    This test requires _main_checkout_root to be wired into _runner_for_path.
    With real git worktree fixture so the new code path is exercised.
    """
    _skip_if_no_git()

    from bytedigger_engine.workflows import phase_5_implement  # noqa: PLC0415

    # Must have _main_checkout_root wired — otherwise behaviour is same as today
    assert hasattr(phase_5_implement, "_main_checkout_root"), (
        "phase_5_implement._main_checkout_root does not exist — GREEN must add this helper"
    )

    main_repo = tmp_path / "main_repo"
    _git_init_repo(main_repo)
    # No .venv in main repo

    worktree = tmp_path / "worktree_c"
    subprocess.run(
        ["git", "worktree", "add", str(worktree), "-b", "feat-ac9"],
        cwd=main_repo,
        check=True,
    )
    # No .venv in worktree either

    result = phase_5_implement._runner_for_path("src/test_mod.py", str(worktree))
    assert result is not None
    assert result["kind"] == "py"

    prefix = result["argv_prefix"]
    assert prefix == ["python3", "-m", "pytest", "-x", "--tb=no", "-q"], (
        f"Expected python3 -m pytest fallback, got {prefix!r}"
    )


# ---------------------------------------------------------------------------
# AC10 — _main_checkout_root passes a bounded timeout= to subprocess.run
#         and returns None on TimeoutExpired (fail-soft, parent SYSTEMATIC)
# ---------------------------------------------------------------------------


def test_ac10_main_checkout_root_passes_finite_timeout(tmp_path):
    """AC10a: _main_checkout_root passes a finite timeout= kwarg to subprocess.run."""
    from bytedigger_engine.workflows import phase_5_implement  # noqa: PLC0415

    assert hasattr(phase_5_implement, "_main_checkout_root"), (
        "phase_5_implement._main_checkout_root does not exist — GREEN must add this helper"
    )

    captured_kwargs: list[dict] = []

    def fake_run(*args, **kwargs):
        captured_kwargs.append(kwargs)
        # Simulate success: return proc with returncode=0 and stdout=".git\n"
        m = MagicMock()
        m.returncode = 0
        m.stdout = ".git\n"
        return m

    # Temporarily replace subprocess.run inside phase_5_implement
    original = phase_5_implement.subprocess.run
    phase_5_implement.subprocess.run = fake_run
    try:
        phase_5_implement._main_checkout_root(str(tmp_path))
    finally:
        phase_5_implement.subprocess.run = original

    assert captured_kwargs, "subprocess.run was not called inside _main_checkout_root"
    kwargs = captured_kwargs[0]
    assert "timeout" in kwargs, (
        f"subprocess.run called without timeout= kwarg; got {kwargs!r}. "
        "Bounded timeout is required (parent SYSTEMATIC A375DD88 fast-fail contract)."
    )
    timeout_val = kwargs["timeout"]
    assert isinstance(timeout_val, (int, float)) and math.isfinite(timeout_val), (
        f"timeout={timeout_val!r} is not a finite number"
    )
    assert timeout_val > 0, f"timeout={timeout_val!r} must be positive"


def test_ac10_main_checkout_root_returns_none_on_timeout_expired(tmp_path):
    """AC10b: _main_checkout_root returns None (not raises) when subprocess raises TimeoutExpired."""
    from bytedigger_engine.workflows import phase_5_implement  # noqa: PLC0415

    assert hasattr(phase_5_implement, "_main_checkout_root"), (
        "phase_5_implement._main_checkout_root does not exist — GREEN must add this helper"
    )

    def raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=args[0] if args else "git", timeout=5)

    original = phase_5_implement.subprocess.run
    phase_5_implement.subprocess.run = raise_timeout
    try:
        result = phase_5_implement._main_checkout_root(str(tmp_path))
    except subprocess.TimeoutExpired as exc:
        raise AssertionError(
            f"_main_checkout_root let TimeoutExpired propagate: {exc!r}. "
            "Must fail-soft to None (parent SYSTEMATIC A375DD88)."
        ) from exc
    finally:
        phase_5_implement.subprocess.run = original

    assert result is None, (
        f"_main_checkout_root returned {result!r} on TimeoutExpired; expected None"
    )
