"""RED tests for 9F3A7C21 — phase_6 pytest venv parent-checkout climb.

Spec: SHARED/memory/Decisions/2026-07-04_9F3A7C21_phase6_pytest_venv_parent_climb_spec.md
Agreement: CACFAB22 (parent SYSTEMATIC) / GitHub issue #274.

Problem: phase_6_review._resolve_pytest_argv(git_cwd) probes only
<git_cwd>/.venv/bin/pytest (then venv/) then falls back to bare
["python3", "-m", "pytest", ...]. Under an isolated git worktree the
.venv lives only in the main checkout, never the worktree, so the local
probe always misses and phase_6 runs deps-less system python3. This is
the un-migrated twin of BF7890C8 (phase_5's fix).

Fix (not yet implemented in phase_6_review.py):
  - New helper _venv_pytest(base) -> str | None
  - New helper _main_checkout_root(git_cwd) -> str | None
  - _resolve_pytest_argv rewritten to climb via _main_checkout_root
    between the local-venv probe and the system fallback.

These tests FAIL against current code (helpers absent; _resolve_pytest_argv
does not climb). They PASS once GREEN adds the two helpers and rewires
_resolve_pytest_argv per spec §2.3.

COLLECTABILITY NOTE (D1CF5FDF / §1q):
  - NO top-level import of phase_6_review / _venv_pytest / _main_checkout_root
    — the two new helpers don't exist yet.
  - sys.path is set by conftest.py (conftest-import-time singleton, §1q / 81F97F3D).
  - 'import bytedigger_engine.workflows.phase_6_review' is deferred to inside each test body so missing
    attrs raise AssertionError at ASSERT time (via explicit hasattr guards),
    never at collection time.

Mocking discipline (§1l / 7AD3D393): ONLY phase_6_review.git_port.git_read
is mocked (a dependency, not the UUT). _resolve_pytest_argv, _main_checkout_root,
and _venv_pytest are NEVER patched — they are the units under test.
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch


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


def _git_result(returncode: int = 0, stdout: str = "") -> MagicMock:
    m = MagicMock()
    m.returncode = returncode
    m.stdout = stdout
    m.timed_out = returncode == 124
    return m


# ---------------------------------------------------------------------------
# AC1 — _venv_pytest returns <base>/.venv/bin/pytest when file exists + X_OK
# ---------------------------------------------------------------------------


def test_ac1_venv_pytest_returns_dot_venv_path(tmp_path):
    """AC1: _venv_pytest(base) -> '<base>/.venv/bin/pytest' when that file exists and is executable."""
    from bytedigger_engine.workflows import phase_6_review  # noqa: PLC0415

    assert hasattr(phase_6_review, "_venv_pytest"), (
        "phase_6_review._venv_pytest does not exist — GREEN must add this helper"
    )

    base = tmp_path / "myrepo"
    expected = _make_executable_pytest(base, ".venv")

    result = phase_6_review._venv_pytest(str(base))
    assert result is not None, "_venv_pytest returned None; expected the .venv/bin/pytest path"
    assert os.path.realpath(result) == os.path.realpath(str(expected)), (
        f"_venv_pytest returned {result!r}, expected realpath-equivalent of {expected!r}"
    )


# ---------------------------------------------------------------------------
# AC2 — _venv_pytest prefers .venv over venv when both exist
# ---------------------------------------------------------------------------


def test_ac2_venv_pytest_prefers_dot_venv_over_venv(tmp_path):
    """AC2: when both .venv/bin/pytest and venv/bin/pytest exist, .venv wins."""
    from bytedigger_engine.workflows import phase_6_review  # noqa: PLC0415

    assert hasattr(phase_6_review, "_venv_pytest"), (
        "phase_6_review._venv_pytest does not exist — GREEN must add this helper"
    )

    base = tmp_path / "myrepo"
    dot_venv_path = _make_executable_pytest(base, ".venv")
    _make_executable_pytest(base, "venv")

    result = phase_6_review._venv_pytest(str(base))
    assert result is not None, "_venv_pytest returned None; expected .venv/bin/pytest"
    assert os.path.realpath(result) == os.path.realpath(str(dot_venv_path)), (
        f"_venv_pytest returned {result!r}; expected the .venv path {dot_venv_path!r} (priority order wrong)"
    )


# ---------------------------------------------------------------------------
# AC3 — _venv_pytest returns None when no venv present
# ---------------------------------------------------------------------------


def test_ac3_venv_pytest_returns_none_when_no_venv(tmp_path):
    """AC3: _venv_pytest(empty_dir) -> None (no venv)."""
    from bytedigger_engine.workflows import phase_6_review  # noqa: PLC0415

    assert hasattr(phase_6_review, "_venv_pytest"), (
        "phase_6_review._venv_pytest does not exist — GREEN must add this helper"
    )

    empty = tmp_path / "empty_repo"
    empty.mkdir()

    result = phase_6_review._venv_pytest(str(empty))
    assert result is None, f"_venv_pytest returned {result!r} for an empty dir; expected None"


# ---------------------------------------------------------------------------
# AC4 — _main_checkout_root returns parent-of-common-dir for a worktree
# ---------------------------------------------------------------------------


def test_ac4_main_checkout_root_returns_parent_for_worktree(tmp_path):
    """AC4: _main_checkout_root(worktree) -> realpath(<tmp>/main) when git_read
    stdout points --git-common-dir at a DIFFERENT checkout (mocked git_port.git_read,
    real fs dirs)."""
    from bytedigger_engine.workflows import phase_6_review  # noqa: PLC0415

    assert hasattr(phase_6_review, "_main_checkout_root"), (
        "phase_6_review._main_checkout_root does not exist — GREEN must add this helper"
    )

    worktree = tmp_path / "worktree"
    worktree.mkdir()
    main = tmp_path / "main"
    main.mkdir()

    with patch.object(phase_6_review.git_port, "git_read") as mock_git_read:
        mock_git_read.return_value = _git_result(returncode=0, stdout="../main/.git\n")
        result = phase_6_review._main_checkout_root(str(worktree))

    assert result is not None, (
        f"_main_checkout_root({worktree!s}) returned None; expected the main checkout root"
    )
    expected = os.path.realpath(str(main))
    actual = os.path.realpath(result)
    assert actual == expected, (
        f"_main_checkout_root returned {actual!r}; expected main checkout {expected!r}"
    )


# ---------------------------------------------------------------------------
# AC5 — _main_checkout_root returns None when common-dir root == realpath(git_cwd)
# ---------------------------------------------------------------------------


def test_ac5_main_checkout_root_returns_none_for_main_repo(tmp_path):
    """AC5: _main_checkout_root(git_cwd) -> None when --git-common-dir resolves
    back to git_cwd itself (main checkout, not a worktree)."""
    from bytedigger_engine.workflows import phase_6_review  # noqa: PLC0415

    assert hasattr(phase_6_review, "_main_checkout_root"), (
        "phase_6_review._main_checkout_root does not exist — GREEN must add this helper"
    )

    main_repo = tmp_path / "main_repo"
    main_repo.mkdir()

    with patch.object(phase_6_review.git_port, "git_read") as mock_git_read:
        mock_git_read.return_value = _git_result(returncode=0, stdout=".git\n")
        result = phase_6_review._main_checkout_root(str(main_repo))

    assert result is None, (
        f"_main_checkout_root on a plain repo returned {result!r}; expected None (not a worktree)"
    )


# ---------------------------------------------------------------------------
# AC6 — _main_checkout_root fail-soft to None: rc==124, rc!=0, and OSError
# ---------------------------------------------------------------------------


def test_ac6_main_checkout_root_returns_none_on_timeout(tmp_path):
    """AC6a: returncode==124 (bounded-run timeout) -> None."""
    from bytedigger_engine.workflows import phase_6_review  # noqa: PLC0415

    assert hasattr(phase_6_review, "_main_checkout_root"), (
        "phase_6_review._main_checkout_root does not exist — GREEN must add this helper"
    )

    with patch.object(phase_6_review.git_port, "git_read") as mock_git_read:
        mock_git_read.return_value = _git_result(returncode=124, stdout="")
        result = phase_6_review._main_checkout_root(str(tmp_path))

    assert result is None, f"_main_checkout_root returned {result!r} on rc=124; expected None"


def test_ac6_main_checkout_root_returns_none_on_nonzero_rc(tmp_path):
    """AC6b: returncode==1 (git failure, e.g. not a git repo) -> None."""
    from bytedigger_engine.workflows import phase_6_review  # noqa: PLC0415

    assert hasattr(phase_6_review, "_main_checkout_root"), (
        "phase_6_review._main_checkout_root does not exist — GREEN must add this helper"
    )

    with patch.object(phase_6_review.git_port, "git_read") as mock_git_read:
        mock_git_read.return_value = _git_result(returncode=1, stdout="")
        result = phase_6_review._main_checkout_root(str(tmp_path))

    assert result is None, f"_main_checkout_root returned {result!r} on rc=1; expected None"


def test_ac6_main_checkout_root_returns_none_on_oserror(tmp_path):
    """AC6c: git_port.git_read raising OSError -> None, no exception propagates (fail-soft)."""
    from bytedigger_engine.workflows import phase_6_review  # noqa: PLC0415

    assert hasattr(phase_6_review, "_main_checkout_root"), (
        "phase_6_review._main_checkout_root does not exist — GREEN must add this helper"
    )

    with patch.object(phase_6_review.git_port, "git_read") as mock_git_read:
        mock_git_read.side_effect = OSError("git binary not found")
        try:
            result = phase_6_review._main_checkout_root(str(tmp_path))
        except OSError as exc:
            raise AssertionError(
                f"_main_checkout_root let OSError propagate: {exc!r}; must fail-soft to None"
            ) from exc

    assert result is None, f"_main_checkout_root returned {result!r} on OSError; expected None"


# ---------------------------------------------------------------------------
# AC7 — [forcing fn] _resolve_pytest_argv climbs to the parent checkout's venv
# ---------------------------------------------------------------------------


def test_ac7_resolve_pytest_argv_climbs_to_parent_checkout_venv(tmp_path):
    """AC7 (core regression, forcing fn): worktree has NO local venv but the
    mocked common-dir parent DOES -> _resolve_pytest_argv(worktree) must return
    [<parent>/.venv/bin/pytest, "--tb=short", "-q"].

    MUST FAIL on current code (returns bare python3 -m pytest fallback,
    no parent-checkout climb implemented yet)."""
    from bytedigger_engine.workflows import phase_6_review  # noqa: PLC0415

    worktree = tmp_path / "worktree"
    worktree.mkdir()
    main = tmp_path / "main"
    main_pytest = _make_executable_pytest(main, ".venv")

    assert not (worktree / ".venv").exists(), "fixture error: worktree should not have .venv"

    with patch.object(phase_6_review.git_port, "git_read") as mock_git_read:
        mock_git_read.return_value = _git_result(returncode=0, stdout="../main/.git\n")
        result = phase_6_review._resolve_pytest_argv(str(worktree))

    assert isinstance(result, list) and len(result) > 0, f"unexpected argv shape: {result!r}"
    actual_pytest = os.path.realpath(result[0])
    expected_pytest = os.path.realpath(str(main_pytest))
    assert actual_pytest == expected_pytest, (
        f"_resolve_pytest_argv(worktree)[0]={result[0]!r} (realpath={actual_pytest!r}); "
        f"expected parent-checkout .venv pytest {expected_pytest!r}. "
        "Parent-checkout climb not implemented yet."
    )
    assert result[1:] == ["--tb=short", "-q"], f"suffix mismatch: {result!r}"


# ---------------------------------------------------------------------------
# AC8 — _resolve_pytest_argv uses the LOCAL venv when present; no climb attempted
# ---------------------------------------------------------------------------


def test_ac8_resolve_pytest_argv_uses_local_venv_no_climb(tmp_path):
    """AC8: git_cwd has its own .venv/bin/pytest -> argv[0] is that path AND
    git_port.git_read is NOT called (no parent-checkout probe needed)."""
    from bytedigger_engine.workflows import phase_6_review  # noqa: PLC0415

    git_cwd = tmp_path / "my_repo"
    own_pytest = _make_executable_pytest(git_cwd, ".venv")

    with patch.object(phase_6_review.git_port, "git_read") as mock_git_read:
        result = phase_6_review._resolve_pytest_argv(str(git_cwd))
        mock_git_read.assert_not_called()

    assert isinstance(result, list) and len(result) > 0
    actual = os.path.realpath(result[0])
    expected = os.path.realpath(str(own_pytest))
    assert actual == expected, f"argv[0]={result[0]!r}; expected own .venv pytest {expected!r}"
    assert result[1:] == ["--tb=short", "-q"], f"suffix mismatch: {result!r}"


# ---------------------------------------------------------------------------
# AC9 — _resolve_pytest_argv falls back to python3 -m pytest when no venv anywhere
# ---------------------------------------------------------------------------


def test_ac9_resolve_pytest_argv_falls_back_when_no_venv_anywhere(tmp_path):
    """AC9: neither local nor parent-checkout venv exists -> bare python3 fallback."""
    from bytedigger_engine.workflows import phase_6_review  # noqa: PLC0415

    worktree = tmp_path / "worktree2"
    worktree.mkdir()

    with patch.object(phase_6_review.git_port, "git_read") as mock_git_read:
        mock_git_read.return_value = _git_result(returncode=1, stdout="")
        result = phase_6_review._resolve_pytest_argv(str(worktree))

    assert result == ["python3", "-m", "pytest", "--tb=short", "-q"], (
        f"Expected bare python3 -m pytest fallback, got {result!r}"
    )


# ---------------------------------------------------------------------------
# AC10 — _resolve_pytest_argv(None) short-circuits; no fs/subprocess touch
# ---------------------------------------------------------------------------


def test_ac10_resolve_pytest_argv_none_short_circuits(tmp_path):
    """AC10: git_cwd=None -> bare python3 fallback, no fs/subprocess touch
    (git_port.git_read is never called)."""
    from bytedigger_engine.workflows import phase_6_review  # noqa: PLC0415

    with patch.object(phase_6_review.git_port, "git_read") as mock_git_read:
        result = phase_6_review._resolve_pytest_argv(None)
        mock_git_read.assert_not_called()

    assert result == ["python3", "-m", "pytest", "--tb=short", "-q"], (
        f"Expected bare python3 -m pytest fallback for git_cwd=None, got {result!r}"
    )


# ---------------------------------------------------------------------------
# AC11 — Return of _resolve_pytest_argv is a list (consumer contract, §1o)
# ---------------------------------------------------------------------------


def test_ac11_resolve_pytest_argv_returns_list_consumer_contract(tmp_path):
    """AC11: consumer at phase_6_review.py:4128 does
    `argv = _resolve_pytest_argv(git_cwd) + py_test_paths` — return must be a
    list so list-concatenation with py_test_paths works without error."""
    from bytedigger_engine.workflows import phase_6_review  # noqa: PLC0415

    result = phase_6_review._resolve_pytest_argv(None)
    assert isinstance(result, list), f"expected list, got {type(result).__name__}: {result!r}"

    py_test_paths = ["tests/test_foo.py", "tests/test_bar.py"]
    combined = result + py_test_paths
    assert combined == result + py_test_paths
    assert combined[-2:] == py_test_paths, (
        f"list concatenation with py_test_paths failed to preserve order: {combined!r}"
    )
