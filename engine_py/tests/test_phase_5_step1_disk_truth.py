"""RED tests for 95D3E5F6 Step 1 — phase_5 _commit_red_tests disk_truth rewrite.

Contract: after Step 1, _commit_red_tests derives red_test_paths from git via
_derive_red_paths_via_git_diff, emits red_files_drift event on LLM/git mismatch,
and falls back gracefully when git yields nothing.

All tests MUST FAIL until GREEN agent implements _derive_red_paths_via_git_diff
and the drift-detection logic in _commit_red_tests.

Do NOT implement the contract here — RED-only file.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).parent
ENGINE_ROOT = HERE.parent
sys.path.insert(0, str(ENGINE_ROOT))
sys.path.insert(0, str(ENGINE_ROOT / "lib"))
sys.path.insert(0, str(ENGINE_ROOT / "workflows"))

# This import will fail until GREEN implements _derive_red_paths_via_git_diff.
from phase_5_implement import (  # noqa: E402
    _derive_red_paths_via_git_diff,
    _commit_red_tests,
)
from contracts import WorkflowContext  # noqa: E402


# ─── helpers ──────────────────────────────────────────────────────────────────


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)


def _commit_file(repo: Path, relpath: str, body: str = "# x\n", msg: str = "c") -> None:
    p = repo / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    subprocess.run(["git", "add", relpath], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", msg], cwd=repo, check=True)


def _make_repo(tmp_path: Path) -> Path:
    """Minimal git repo with one committed file so HEAD is valid."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
    return repo


def _add_untracked(repo: Path, relpath: str, body: str = "# test\n") -> None:
    p = repo / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)


def _make_ctx(scratchpad: Path, git_cwd: str, **org_extra) -> WorkflowContext:
    org = {"scratchpad_dir": str(scratchpad), "git_cwd": git_cwd, **org_extra}
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question="Add foo to bar",
        session_id="test-session",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_prev(red_test_paths, cycle: int = 1):
    """Minimal prev StepResult-like object with data dict."""
    prev = MagicMock()
    prev.data = {
        "red_test_paths": red_test_paths,
        "cycle": cycle,
    }
    return prev


# ═══════════════════════════════════════════════════════════════════════════════
# TestDeriveRedPathsViaGitDiff
# ═══════════════════════════════════════════════════════════════════════════════


class TestDeriveRedPathsViaGitDiff:
    """Unit tests for the new _derive_red_paths_via_git_diff helper."""

    def test_derive_returns_dirty_test_files(self, tmp_path: Path) -> None:
        """Untracked tests/foo_test.py in repo → helper returns path in list."""
        repo = _make_repo(tmp_path)
        _add_untracked(repo, "tests/foo_test.py", "def test_it(): assert False\n")

        result = _derive_red_paths_via_git_diff(str(repo))

        assert isinstance(result, list)
        # At least one element matches the untracked test file
        assert any("foo_test.py" in p for p in result)

    def test_derive_filters_non_test_segments(self, tmp_path: Path) -> None:
        """Only paths matching segment_filter='tests/' are returned."""
        repo = _make_repo(tmp_path)
        _add_untracked(repo, "tests/foo_test.py", "def test_it(): assert False\n")
        _add_untracked(repo, "src/main.py", "# main\n")

        result = _derive_red_paths_via_git_diff(str(repo), segment_filter="tests/")

        # src/main.py must NOT appear
        assert not any("src/main.py" in p or "main.py" in p for p in result)
        # tests/foo_test.py MUST appear
        assert any("foo_test.py" in p for p in result)

    def test_derive_empty_when_no_dirty_test_files(self, tmp_path: Path) -> None:
        """Clean repo with only non-test untracked file → empty list."""
        repo = _make_repo(tmp_path)
        _add_untracked(repo, "src/main.py", "# main\n")

        result = _derive_red_paths_via_git_diff(str(repo), segment_filter="tests/")

        assert result == []


# ═══════════════════════════════════════════════════════════════════════════════
# TestCommitRedTestsGitFirst
# ═══════════════════════════════════════════════════════════════════════════════


class TestCommitRedTestsGitFirst:
    """_commit_red_tests disk_truth contract: git-first path enumeration + drift events."""

    def test_uses_git_list_when_non_empty_and_matches_llm(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """git non-empty → step succeeds, drift event fires with chosen='git' and llm_paths=[].

        γ cleanup 8.5 (A4461B8F): red_files_drift now fires unconditionally when git
        is non-empty (llm_paths always [] since _parse_red_test_paths deleted).
        """
        repo = _make_repo(tmp_path)
        _add_untracked(repo, "tests/foo.py", "def test_x(): assert False\n")
        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()

        captured: list[dict] = []
        import phase_5_implement
        monkeypatch.setattr(
            phase_5_implement,
            "_emit_safe",
            lambda et, p, severity="warning": captured.append(
                {"type": et, "payload": p, "severity": severity}
            ),
        )

        ctx = _make_ctx(scratchpad, str(repo))
        prev = _make_prev(red_test_paths=["tests/foo.py"])

        result = _commit_red_tests(ctx, prev)

        drift_events = [e for e in captured if e["type"] == "red_files_drift"]
        # drift fires unconditionally when git is non-empty
        assert len(drift_events) == 1
        assert drift_events[0]["payload"]["chosen"] == "git"
        assert drift_events[0]["payload"]["llm_paths"] == []
        # Step should not be an error_code=E_RED_NO_MARKER failure
        assert result.error_code != "E_RED_NO_MARKER"

    def test_emits_drift_event_when_lists_disagree(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """git finds 2 files, LLM only reported 1 → drift event with chosen='git'."""
        repo = _make_repo(tmp_path)
        _add_untracked(repo, "tests/foo.py", "def test_x(): assert False\n")
        _add_untracked(repo, "tests/bar.py", "def test_y(): assert False\n")
        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()

        captured: list[dict] = []
        import phase_5_implement
        monkeypatch.setattr(
            phase_5_implement,
            "_emit_safe",
            lambda et, p, severity="warning": captured.append(
                {"type": et, "payload": p, "severity": severity}
            ),
        )

        ctx = _make_ctx(scratchpad, str(repo))
        prev = _make_prev(red_test_paths=["tests/foo.py"])

        result = _commit_red_tests(ctx, prev)

        drift_events = [e for e in captured if e["type"] == "red_files_drift"]
        assert len(drift_events) == 1
        assert drift_events[0]["payload"]["chosen"] == "git"

    def test_falls_back_to_llm_when_git_empty_llm_nonempty(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """git list empty → E_RED_NO_MARKER (no LLM fallback after γ cleanup 8.5).

        γ cleanup 8.5 (A4461B8F): LLM fallback path removed. When git_diff returns
        [] (no dirty test files), the step returns E_RED_NO_MARKER unconditionally.
        """
        repo = _make_repo(tmp_path)
        # No untracked test files → git_diff returns []
        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()

        import phase_5_implement
        monkeypatch.setattr(
            phase_5_implement,
            "_emit_safe",
            lambda et, p, severity="warning": None,
        )

        ctx = _make_ctx(scratchpad, str(repo))
        prev = _make_prev(red_test_paths=["custom/foo_test.py"])

        result = _commit_red_tests(ctx, prev)

        # LLM fallback removed — git empty means E_RED_NO_MARKER
        assert result.error_code == "E_RED_NO_MARKER"

    def test_no_drift_event_when_only_git_used_and_llm_none(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """git non-empty + LLM marker missing → drift event fires with chosen='git'.

        γ cleanup 8.5 (A4461B8F): drift fires unconditionally when git is non-empty.
        llm_paths is always [] (LLM marker parser removed).
        """
        repo = _make_repo(tmp_path)
        _add_untracked(repo, "tests/foo.py", "def test_x(): assert False\n")
        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()

        captured: list[dict] = []
        import phase_5_implement
        monkeypatch.setattr(
            phase_5_implement,
            "_emit_safe",
            lambda et, p, severity="warning": captured.append(
                {"type": et, "payload": p, "severity": severity}
            ),
        )

        ctx = _make_ctx(scratchpad, str(repo))
        prev = _make_prev(red_test_paths=None)

        result = _commit_red_tests(ctx, prev)

        drift_events = [e for e in captured if e["type"] == "red_files_drift"]
        # drift fires unconditionally when git is non-empty
        assert len(drift_events) == 1
        assert drift_events[0]["payload"]["chosen"] == "git"
        assert drift_events[0]["payload"]["llm_paths"] == []
        assert result.error_code != "E_RED_NO_MARKER"

    def test_e_red_no_marker_when_both_empty_and_no_override(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """LLM=None, git=empty (no dirty test files), no override → E_RED_NO_MARKER."""
        repo = _make_repo(tmp_path)
        # No untracked test files — repo is clean
        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()

        import phase_5_implement
        monkeypatch.setattr(
            phase_5_implement,
            "_emit_safe",
            lambda et, p, severity="warning": None,
        )

        ctx = _make_ctx(scratchpad, str(repo))
        prev = _make_prev(red_test_paths=None)

        result = _commit_red_tests(ctx, prev)

        assert result.error_code == "E_RED_NO_MARKER"

    def test_drift_payload_shape(self, tmp_path: Path, monkeypatch) -> None:
        """Drift event payload must contain llm_paths, git_paths, chosen, phase==5."""
        repo = _make_repo(tmp_path)
        _add_untracked(repo, "tests/foo.py", "def test_x(): assert False\n")
        _add_untracked(repo, "tests/bar.py", "def test_y(): assert False\n")
        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()

        captured: list[dict] = []
        import phase_5_implement
        monkeypatch.setattr(
            phase_5_implement,
            "_emit_safe",
            lambda et, p, severity="warning": captured.append(
                {"type": et, "payload": p, "severity": severity}
            ),
        )

        ctx = _make_ctx(scratchpad, str(repo))
        prev = _make_prev(red_test_paths=["tests/foo.py"])

        _commit_red_tests(ctx, prev)

        drift_events = [e for e in captured if e["type"] == "red_files_drift"]
        assert len(drift_events) == 1
        payload = drift_events[0]["payload"]
        assert "llm_paths" in payload
        assert "git_paths" in payload
        assert "chosen" in payload
        assert "phase" in payload
        assert payload["phase"] == 5
