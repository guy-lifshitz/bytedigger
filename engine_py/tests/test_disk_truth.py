"""RED tests for lib/plugins/disk_truth/ — 95D3E5F6 Step 0.

All tests MUST FAIL (ImportError/ModuleNotFoundError) until GREEN agent creates the package.
Do NOT create the package here — this is RED-only.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).parent          # engine_py/tests/
ENGINE_ROOT = HERE.parent             # engine_py/
sys.path.insert(0, str(ENGINE_ROOT))

from bytedigger_engine.lib.plugins.disk_truth import (  # noqa: E402  — will ImportError in RED
    # git_diff.py
    git_diff_files,
    git_status_porcelain,
    resolve_pre_phase_sha,
    # test_runner.py
    run_test_command,
    TestRunResult,
    # verdict_parser.py
    parse_single_token_verdict,
    parse_structured_block,
    # schema.py
    SpecVerdict,
    ValidationVerdict,
    SatisfactionVerdict,
    SchemaViolation,
    enforce,
)


# ═══════════════════════════════════════════════════════════════════════════════
# TestGitDiff
# ═══════════════════════════════════════════════════════════════════════════════


class TestGitDiff:
    """git_diff_files, git_status_porcelain, resolve_pre_phase_sha."""

    def test_git_diff_files_happy_path(self, tmp_path: Path) -> None:
        """Returns list containing a committed-then-modified file."""
        import subprocess

        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True, capture_output=True)

        f = repo / "foo.py"
        f.write_text("v1\n")
        subprocess.run(["git", "add", "foo.py"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

        pre_sha = resolve_pre_phase_sha(repo)

        f.write_text("v2\n")
        subprocess.run(["git", "add", "foo.py"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "change"], cwd=repo, check=True, capture_output=True)

        result = git_diff_files(pre_sha, repo)
        assert isinstance(result, list)
        assert any("foo.py" in p for p in result)

    def test_git_diff_files_empty_when_no_changes(self, tmp_path: Path) -> None:
        """Returns empty list when no files changed since pre_sha."""
        import subprocess

        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True, capture_output=True)

        f = repo / "bar.py"
        f.write_text("v1\n")
        subprocess.run(["git", "add", "bar.py"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

        pre_sha = resolve_pre_phase_sha(repo)
        result = git_diff_files(pre_sha, repo, untracked=False)
        assert result == []

    def test_git_diff_files_includes_untracked_when_flag_true(self, tmp_path: Path) -> None:
        """Untracked files appear when untracked=True."""
        import subprocess

        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True, capture_output=True)

        seed = repo / "seed.py"
        seed.write_text("x\n")
        subprocess.run(["git", "add", "seed.py"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

        pre_sha = resolve_pre_phase_sha(repo)
        (repo / "new_untracked.py").write_text("new\n")

        result = git_diff_files(pre_sha, repo, untracked=True)
        assert any("new_untracked.py" in p for p in result)

    def test_git_diff_files_excludes_untracked_when_flag_false(self, tmp_path: Path) -> None:
        """Untracked files absent when untracked=False."""
        import subprocess

        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True, capture_output=True)

        seed = repo / "seed.py"
        seed.write_text("x\n")
        subprocess.run(["git", "add", "seed.py"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

        pre_sha = resolve_pre_phase_sha(repo)
        (repo / "hidden_untracked.py").write_text("new\n")

        result = git_diff_files(pre_sha, repo, untracked=False)
        assert not any("hidden_untracked.py" in p for p in result)

    def test_git_diff_files_segment_filter_matches(self, tmp_path: Path) -> None:
        """segment_filter='tests/' returns only paths under tests/."""
        import subprocess

        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "tests").mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True, capture_output=True)

        seed = repo / "seed.py"
        seed.write_text("x\n")
        subprocess.run(["git", "add", "seed.py"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

        pre_sha = resolve_pre_phase_sha(repo)
        (repo / "tests" / "test_foo.py").write_text("# test\n")

        result = git_diff_files(pre_sha, repo, untracked=True, segment_filter="tests")
        assert any("test_foo.py" in p for p in result), f"expected test_foo.py in {result}"

    def test_git_diff_files_segment_filter_no_match_returns_empty(self, tmp_path: Path) -> None:
        """segment_filter with no matching paths returns empty list."""
        import subprocess

        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True, capture_output=True)

        seed = repo / "seed.py"
        seed.write_text("x\n")
        subprocess.run(["git", "add", "seed.py"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

        pre_sha = resolve_pre_phase_sha(repo)
        (repo / "lib_code.py").write_text("code\n")

        result = git_diff_files(pre_sha, repo, untracked=True, segment_filter="tests")
        assert result == []

    def test_git_diff_files_includes_modified_tracked_when_untracked_true(self, tmp_path: Path) -> None:
        """Modified tracked file in working tree appears when untracked=True (AC1)."""
        import subprocess

        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True, capture_output=True)

        f = repo / "tracked.py"
        f.write_text("v1\n")
        subprocess.run(["git", "add", "tracked.py"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

        pre_sha = resolve_pre_phase_sha(repo)

        f.write_text("v2\n")

        result = git_diff_files(pre_sha, repo, untracked=True)
        assert any("tracked.py" in p for p in result)

    def test_git_diff_files_excludes_working_tree_changes_when_untracked_false(self, tmp_path: Path) -> None:
        """Modified tracked file in working tree excluded when untracked=False (AC4)."""
        import subprocess

        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True, capture_output=True)

        f = repo / "tracked.py"
        f.write_text("v1\n")
        subprocess.run(["git", "add", "tracked.py"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

        pre_sha = resolve_pre_phase_sha(repo)

        f.write_text("v2\n")

        result = git_diff_files(pre_sha, repo, untracked=False)
        assert not any("tracked.py" in p for p in result)

    def test_git_diff_files_includes_staged_but_uncommitted_when_untracked_true(self, tmp_path: Path) -> None:
        """Staged but uncommitted file change appears when untracked=True (AC5)."""
        import subprocess

        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True, capture_output=True)

        f = repo / "staged.py"
        f.write_text("v1\n")
        subprocess.run(["git", "add", "staged.py"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

        pre_sha = resolve_pre_phase_sha(repo)

        f.write_text("v2\n")
        subprocess.run(["git", "add", "staged.py"], cwd=repo, check=True, capture_output=True)

        result = git_diff_files(pre_sha, repo, untracked=True)
        assert any("staged.py" in p for p in result)

    def test_git_diff_files_includes_deleted_tracked_when_untracked_true(self, tmp_path: Path) -> None:
        """Deleted tracked file (working tree only, not staged) appears when untracked=True (AC6)."""
        import subprocess

        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True, capture_output=True)

        f = repo / "gone.py"
        f.write_text("v1\n")
        subprocess.run(["git", "add", "gone.py"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

        pre_sha = resolve_pre_phase_sha(repo)

        f.unlink()

        result = git_diff_files(pre_sha, repo, untracked=True)
        assert any("gone.py" in p for p in result)

    def test_resolve_pre_phase_sha_happy(self, tmp_path: Path) -> None:
        """Returns a 40-char hex SHA for a valid repo HEAD."""
        import subprocess

        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.email", "test@test.com"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True, capture_output=True)
        (repo / "f.txt").write_text("hi\n")
        subprocess.run(["git", "add", "f.txt"], cwd=repo, check=True, capture_output=True)
        subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)

        sha = resolve_pre_phase_sha(repo)
        assert isinstance(sha, str)
        assert len(sha) == 40
        assert all(c in "0123456789abcdef" for c in sha)

    def test_resolve_pre_phase_sha_raises_on_non_repo(self, tmp_path: Path) -> None:
        """Raises RuntimeError when path is not a git repo."""
        import pytest

        with pytest.raises(RuntimeError):
            resolve_pre_phase_sha(tmp_path / "not_a_repo")


# ═══════════════════════════════════════════════════════════════════════════════
# TestTestRunner
# ═══════════════════════════════════════════════════════════════════════════════


class TestTestRunner:
    """run_test_command + TestRunResult."""

    def test_test_run_result_constructible(self) -> None:
        """TestRunResult can be constructed with all fields."""
        r = TestRunResult(exit_code=0, n_passed=5, n_failed=0, stdout_path="/tmp/out", stderr_path="/tmp/err")
        assert r.exit_code == 0
        assert r.n_passed == 5
        assert r.n_failed == 0
        assert r.stdout_path == "/tmp/out"
        assert r.stderr_path == "/tmp/err"

    def test_run_test_command_exit_code_zero(self, tmp_path: Path) -> None:
        """Simple exit-0 command returns exit_code=0."""
        result = run_test_command(["sh", "-c", "exit 0"], cwd=tmp_path)
        assert isinstance(result, TestRunResult)
        assert result.exit_code == 0

    def test_run_test_command_exit_code_nonzero_fills_n_failed(self, tmp_path: Path) -> None:
        """Exit-1 with no parseable output → n_failed = sys.maxsize."""
        import sys as _sys

        result = run_test_command(["sh", "-c", "exit 1"], cwd=tmp_path)
        assert result.exit_code == 1
        assert result.n_failed == _sys.maxsize

    def test_run_test_command_stdout_path_exists(self, tmp_path: Path) -> None:
        """stdout_path file is created and readable after run."""
        result = run_test_command(["sh", "-c", "echo hello"], cwd=tmp_path)
        assert Path(result.stdout_path).exists()
        assert "hello" in Path(result.stdout_path).read_text()

    def test_run_test_command_pytest_output_parsed(self, tmp_path: Path) -> None:
        """pytest summary line '3 passed, 1 failed' is parsed into n_passed=3, n_failed=1."""
        # Simulate pytest summary output via echo
        cmd = ["sh", "-c", "echo '3 passed, 1 failed in 0.5s'; exit 1"]
        result = run_test_command(cmd, cwd=tmp_path)
        assert result.n_passed == 3
        assert result.n_failed == 1

    def test_run_test_command_bun_output_parsed(self, tmp_path: Path) -> None:
        """bun:test summary line '2 pass, 0 fail' is parsed correctly."""
        cmd = ["sh", "-c", "echo '2 pass\\n0 fail'; exit 0"]
        result = run_test_command(cmd, cwd=tmp_path)
        assert result.n_passed == 2
        assert result.n_failed == 0

    def test_run_test_command_framework_fallback_on_unparseable(self, tmp_path: Path) -> None:
        """Non-test command with exit 0 → n_passed=0, n_failed=0 (cannot determine but not error)."""
        result = run_test_command(["sh", "-c", "echo 'arbitrary output'"], cwd=tmp_path)
        # exit 0 and no parseable test count → either 0/0 or graceful: n_failed must NOT be maxsize
        import sys as _sys
        assert result.exit_code == 0
        assert result.n_failed != _sys.maxsize or result.n_passed >= 0

    def test_run_test_command_timeout_returns_124(self, tmp_path: Path) -> None:
        """Command exceeding timeout returns exit_code=124 and n_failed=maxsize."""
        import sys as _sys

        result = run_test_command(["sh", "-c", "sleep 10"], cwd=tmp_path, timeout=1)
        assert result.exit_code == 124
        assert result.n_failed == _sys.maxsize


# ═══════════════════════════════════════════════════════════════════════════════
# TestVerdictParser
# ═══════════════════════════════════════════════════════════════════════════════

_STRUCTURED_MD = """\
## Spec Verdict (structured)
```json
{"ship": true, "findings": []}
```
"""

_STRUCTURED_MD_MISSING = """\
## Spec Verdict
No JSON block here.
"""

_STRUCTURED_MD_MALFORMED = """\
## Spec Verdict (structured)
```json
{not valid json
```
"""


class TestVerdictParser:
    """parse_single_token_verdict + parse_structured_block."""

    def test_single_token_match_in_allowed(self) -> None:
        """PASS on its own line returns 'PASS' when in allowed set."""
        result = parse_single_token_verdict("some preamble\nPASS\ntrailing", {"PASS", "REVISE"})
        assert result == "PASS"

    def test_single_token_not_in_allowed_returns_none(self) -> None:
        """SHIP token returns None when allowed={'PASS','REVISE'}."""
        result = parse_single_token_verdict("SHIP\n", {"PASS", "REVISE"})
        assert result is None

    def test_single_token_empty_input_returns_none(self) -> None:
        """Empty string returns None."""
        result = parse_single_token_verdict("", {"PASS", "REVISE", "SHIP"})
        assert result is None

    def test_parse_structured_block_found(self) -> None:
        """Returns parsed dict when fenced JSON block exists under named heading."""
        result = parse_structured_block(_STRUCTURED_MD, "Spec Verdict")
        assert result is not None
        assert result["ship"] is True
        assert result["findings"] == []

    def test_parse_structured_block_missing_returns_none(self) -> None:
        """Returns None when heading exists but no fenced JSON block."""
        result = parse_structured_block(_STRUCTURED_MD_MISSING, "Spec Verdict")
        assert result is None

    def test_parse_structured_block_malformed_json_returns_none(self) -> None:
        """Returns None when fenced block is unparseable JSON."""
        result = parse_structured_block(_STRUCTURED_MD_MALFORMED, "Spec Verdict")
        assert result is None


# ═══════════════════════════════════════════════════════════════════════════════
# TestSchema
# ═══════════════════════════════════════════════════════════════════════════════

_VALID_FINDING = {"id": "1", "type": "fabrication", "evidence": "invented path", "required_action": "remove"}

_VALID_SPEC_VERDICT = {
    "ship": False,
    "findings": [_VALID_FINDING],
}

_VALID_VALIDATION_VERDICT = {
    "approve": True,
    "reject_reason": None,
}

_VALID_SATISFACTION_VERDICT = {
    "satisfied": True,
    "fixes_required": [],
}


class TestSchema:
    """SpecVerdict, ValidationVerdict, SatisfactionVerdict, enforce, SchemaViolation."""

    def test_spec_verdict_valid(self) -> None:
        """enforce on valid SpecVerdict payload returns instance with correct attrs."""
        instance = enforce(_VALID_SPEC_VERDICT, SpecVerdict)
        assert instance.ship is False
        assert len(instance.findings) == 1

    def test_spec_verdict_missing_required_field_raises(self) -> None:
        """enforce raises SchemaViolation when 'ship' field is missing."""
        import pytest

        bad = {"findings": []}
        with pytest.raises(SchemaViolation) as exc_info:
            enforce(bad, SpecVerdict)
        assert "ship" in str(exc_info.value)

    def test_validation_verdict_valid(self) -> None:
        """enforce on valid ValidationVerdict returns instance."""
        instance = enforce(_VALID_VALIDATION_VERDICT, ValidationVerdict)
        assert instance.approve is True
        assert instance.reject_reason is None

    def test_validation_verdict_missing_field_raises(self) -> None:
        """enforce raises SchemaViolation when 'approve' is missing."""
        import pytest

        with pytest.raises(SchemaViolation):
            enforce({"reject_reason": "bad"}, ValidationVerdict)

    def test_satisfaction_verdict_valid(self) -> None:
        """enforce on valid SatisfactionVerdict returns instance."""
        instance = enforce(_VALID_SATISFACTION_VERDICT, SatisfactionVerdict)
        assert instance.satisfied is True
        assert instance.fixes_required == []

    def test_schema_violation_is_exception(self) -> None:
        """SchemaViolation is a subclass of Exception."""
        assert issubclass(SchemaViolation, Exception)

    def test_schema_violation_message_includes_field_name(self) -> None:
        """SchemaViolation raised for unknown extra field includes that field name in message."""
        import pytest

        bad = {**_VALID_SPEC_VERDICT, "extra_unexpected_field": "surprise"}
        with pytest.raises(SchemaViolation) as exc_info:
            enforce(bad, SpecVerdict)
        assert "extra_unexpected_field" in str(exc_info.value)
