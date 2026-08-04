"""RED tests for agreement 67F3404F — test-path detection fix.

Tests cover ACs 1-7 from:
  SHARED/memory/Decisions/2026-05-09_67F3404F_test_path_detection_spec.md

These tests are expected to FAIL until GREEN ships the _is_test_path helper
and updates _derive_red_paths_via_git_diff / _derive_red_paths_from_git.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
ENGINE_ROOT = HERE.parent

from bytedigger_engine.workflows import phase_5_implement  # noqa: E402

import pytest


# ---------------------------------------------------------------------------
# Git-repo fixture helper
# ---------------------------------------------------------------------------

def _init_repo(repo_path: Path) -> None:
    """Bootstrap a minimal git repo with a HEAD commit so resolve_pre_phase_sha works."""
    repo_path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=str(repo_path), check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=str(repo_path), check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=str(repo_path), check=True,
    )
    (repo_path / "README.md").write_text("seed")
    subprocess.run(["git", "add", "README.md"], cwd=str(repo_path), check=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "seed"],
        cwd=str(repo_path), check=True,
    )


# ---------------------------------------------------------------------------
# TestDeriveRedPathsViaGitDiff  (ACs 1–5)
# ---------------------------------------------------------------------------

class TestDeriveRedPathsViaGitDiff:
    """Tests for _derive_red_paths_via_git_diff with the new _is_test_path predicate."""

    def test_ac1_sibling_test_sh_detected(self, tmp_path):
        """AC1 — sibling-naming *.test.sh must be returned."""
        _init_repo(tmp_path)
        target = tmp_path / "SYSTEM" / "cli" / "build" / "restart-budget.test.sh"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("#!/usr/bin/env bash\necho test")

        result = phase_5_implement._derive_red_paths_via_git_diff(str(tmp_path))

        assert "SYSTEM/cli/build/restart-budget.test.sh" in result, (
            f"Expected 'SYSTEM/cli/build/restart-budget.test.sh' in result, got: {result}"
        )

    def test_ac2_sibling_test_tsx_detected(self, tmp_path):
        """AC2 — sibling-naming *.test.tsx must be returned."""
        _init_repo(tmp_path)
        target = tmp_path / "src" / "components" / "Foo.test.tsx"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("// test")

        result = phase_5_implement._derive_red_paths_via_git_diff(str(tmp_path))

        assert "src/components/Foo.test.tsx" in result, (
            f"Expected 'src/components/Foo.test.tsx' in result, got: {result}"
        )

    def test_ac3_pytest_outside_tests_dir_detected(self, tmp_path):
        """AC3 — test_*.py file outside tests/ must be returned."""
        _init_repo(tmp_path)
        target = tmp_path / "bin" / "test_helpers.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("def test_foo(): pass")

        result = phase_5_implement._derive_red_paths_via_git_diff(str(tmp_path))

        assert "bin/test_helpers.py" in result, (
            f"Expected 'bin/test_helpers.py' in result, got: {result}"
        )

    def test_ac4_tests_segment_path_still_detected(self, tmp_path):
        """AC4 — tests/ segment path must still be returned (non-regression).

        foo_spec.py does NOT match pytest filename patterns; it relies on the
        'tests' segment match. This guards against over-narrowing the predicate.
        """
        _init_repo(tmp_path)
        target = tmp_path / "tests" / "foo_spec.py"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("# spec")

        result = phase_5_implement._derive_red_paths_via_git_diff(str(tmp_path))

        assert "tests/foo_spec.py" in result, (
            f"Expected 'tests/foo_spec.py' in result, got: {result}"
        )

    def test_ac5_non_test_file_excluded(self, tmp_path):
        """AC5 — plain .sh file (no .test. infix, not in tests/) must NOT appear."""
        _init_repo(tmp_path)
        target = tmp_path / "SYSTEM" / "cli" / "build" / "restart-budget.sh"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("#!/usr/bin/env bash\necho prod")

        result = phase_5_implement._derive_red_paths_via_git_diff(str(tmp_path))

        assert "SYSTEM/cli/build/restart-budget.sh" not in result, (
            f"Expected 'SYSTEM/cli/build/restart-budget.sh' to be excluded, got: {result}"
        )


# ---------------------------------------------------------------------------
# TestDeriveRedPathsFromGitParity  (AC7)
# ---------------------------------------------------------------------------

class TestDeriveRedPathsFromGitParity:
    """_derive_red_paths_from_git parity tests — deleted by γ cleanup 8.5 (A4461B8F)."""

    def test_ac7_legacy_fallback_detects_test_sh(self, tmp_path):
        """γ cleanup 8.5: _derive_red_paths_from_git deleted; this test is a no-op guard.
        AC7 coverage moved to _derive_red_paths_via_git_diff / TestDeriveRedPathsViaGitDiff."""
        # _derive_red_paths_from_git no longer exists — just assert it's gone
        assert not hasattr(phase_5_implement, "_derive_red_paths_from_git"), (
            "_derive_red_paths_from_git should have been deleted by γ cleanup 8.5"
        )


# ---------------------------------------------------------------------------
# TestSharedHelperPresent  (AC6)
# ---------------------------------------------------------------------------

class TestSharedHelperPresent:
    """Verify _is_test_path helper exists and works correctly (AC6)."""

    def test_ac6_is_test_path_helper_callable(self):
        """AC6 — _is_test_path must be defined at module level and be callable."""
        helper = getattr(phase_5_implement, "_is_test_path", None)
        assert helper is not None, (
            "_is_test_path not found on phase_5_implement — GREEN has not shipped yet"
        )
        assert callable(helper), "_is_test_path exists but is not callable"

    def test_ac6_is_test_path_correct(self):
        """AC6 — _is_test_path returns correct values for known paths."""
        helper = getattr(phase_5_implement, "_is_test_path", None)
        if helper is None:
            pytest.fail(
                "_is_test_path not found on phase_5_implement — GREEN has not shipped yet"
            )

        # Must return True for sibling-naming convention
        assert helper("SYSTEM/cli/build/restart-budget.test.sh") is True, (
            "_is_test_path('...restart-budget.test.sh') should be True"
        )
        # Must return True for tests/ segment
        assert helper("tests/foo_spec.py") is True, (
            "_is_test_path('tests/foo_spec.py') should be True"
        )
        # Must return False for plain non-test file
        assert helper("README.md") is False, (
            "_is_test_path('README.md') should be False"
        )
        # Must return True for pytest convention outside tests/
        assert helper("bin/test_helpers.py") is True, (
            "_is_test_path('bin/test_helpers.py') should be True"
        )
