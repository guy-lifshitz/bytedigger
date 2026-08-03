"""RED tests for 4C0056FA — phase_5 _commit_green_code (engine-owned GREEN commit).

Contract: after Step 4.8 verify_green_passing succeeds, _commit_green_code:
  - reads red_commit_sha from prev.data as SHA boundary
  - enumerates production (non-test) paths via git_diff_files since red_commit_sha
  - skips commit + emits green_commit_skipped when no production paths found
  - commits production paths + emits green_commit event with sha/paths/n_files
  - excludes test files from the commit even if they are dirty
  - returns E_MISSING_RED_BOUNDARY when red_commit_sha absent from prev.data
  - phase_5_implement_workflow() registers commit_green_code as the last step
  - returns E_GIT_LOCKED when _git_op_with_lock_retry signals lock_persisted

All tests MUST FAIL until GREEN agent implements _commit_green_code.

Do NOT implement the contract here — RED-only file.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

HERE = Path(__file__).parent
ENGINE_ROOT = HERE.parent
sys.path.insert(0, str(ENGINE_ROOT))

# This import will fail with ImportError until GREEN implements _commit_green_code.
from bytedigger_engine.workflows.phase_5_implement import _commit_green_code  # noqa: E402
from bytedigger_engine.contracts import WorkflowContext  # noqa: E402


# ─── helpers ──────────────────────────────────────────────────────────────────


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)


def _commit_file(repo: Path, relpath: str, body: str = "# x\n", msg: str = "c") -> str:
    """Write, stage, commit a file. Returns the resulting HEAD SHA."""
    p = repo / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    subprocess.run(["git", "add", relpath], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", msg], cwd=repo, check=True)
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=repo, check=True
    )
    return result.stdout.strip()


def _make_repo_with_red_commit(tmp_path: Path) -> tuple[Path, str]:
    """Seed repo: init + placeholder (seed) + a test-file RED commit.

    Returns (repo_path, red_commit_sha).
    """
    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
    red_sha = _commit_file(repo, "tests/test_red.py", "def test_fail(): assert False\n", "RED: add failing test")
    return repo, red_sha


def _write_file(repo: Path, relpath: str, body: str = "# impl\n") -> None:
    """Write a file to the working tree without staging/committing."""
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
        question="Fix the thing",
        session_id="test-session",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_prev(red_commit_sha=None, cycle: int = 1, **extra) -> MagicMock:
    """Minimal prev StepResult-like object with data dict."""
    prev = MagicMock()
    data: dict = {"cycle": cycle, **extra}
    if red_commit_sha is not None:
        data["red_commit_sha"] = red_commit_sha
    prev.data = data
    return prev


# ═══════════════════════════════════════════════════════════════════════════════
# TestCommitGreenCode
# ═══════════════════════════════════════════════════════════════════════════════


class TestCommitGreenCode:
    """_commit_green_code: engine-authoritative GREEN commit step."""

    def test_commit_green_code_skips_when_no_production_paths(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """RED commit done, no green files in working tree → emit green_commit_skipped, return ok with commit_sha=None.

        AC3: empty-paths skip path.
        """
        repo, red_sha = _make_repo_with_red_commit(tmp_path)
        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()

        captured: list[dict] = []
        from bytedigger_engine.workflows import phase_5_implement
        monkeypatch.setattr(
            phase_5_implement,
            "_emit_safe",
            lambda et, p, severity="warning": captured.append(
                {"type": et, "payload": p, "severity": severity}
            ),
        )

        ctx = _make_ctx(scratchpad, str(repo))
        prev = _make_prev(red_commit_sha=red_sha, cycle=1,
                          worker_written_paths=[], manifest_source="harness_tool_record")  # 4C03CCED Ship 1C

        result = _commit_green_code(ctx, prev)

        # Must return ok (not error)
        assert result.status == "ok", f"Expected ok, got {result.status}: {getattr(result, 'error', '')}"
        # commit_sha must be None on skip
        assert result.data is not None
        assert result.data.get("green_commit_sha") is None
        # Must emit green_commit_skipped with reason=empty_manifest (4961254A)
        skip_events = [e for e in captured if e["type"] == "green_commit_skipped"]
        assert len(skip_events) == 1, f"Expected 1 green_commit_skipped event, got {len(skip_events)}"
        assert skip_events[0]["payload"].get("reason") == "empty_manifest"
        assert skip_events[0]["payload"].get("phase") == 5
        # AC6: **prev.data keys must be preserved on skip path too
        assert result.data.get("red_commit_sha") == red_sha
        assert result.data.get("cycle") == 1

    def test_commit_green_code_commits_when_production_files_present(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """RED commit done, GREEN modifies a real .py file → git add + commit, returns ok with 40-char hex green_commit_sha, emits green_commit event.

        AC4 + AC5 + AC6: non-empty commit path, cycle propagation, SHA in result and event.
        """
        repo, red_sha = _make_repo_with_red_commit(tmp_path)
        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()

        # Write a production file to the working tree (untracked = new GREEN file)
        _write_file(repo, "src/module.py", "def foo(): return 42\n")

        captured: list[dict] = []
        from bytedigger_engine.workflows import phase_5_implement
        monkeypatch.setattr(
            phase_5_implement,
            "_emit_safe",
            lambda et, p, severity="warning": captured.append(
                {"type": et, "payload": p, "severity": severity}
            ),
        )

        ctx = _make_ctx(scratchpad, str(repo))
        # 4961254A: commit-step reads from worker manifest, not git_diff_files
        prev = _make_prev(red_commit_sha=red_sha, cycle=1, worker_written_paths=["src/module.py"],
                          manifest_source="harness_tool_record")  # 4C03CCED Ship 1C

        # Capture HEAD before the call
        head_before = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=repo, check=True
        ).stdout.strip()

        result = _commit_green_code(ctx, prev)

        # Must return ok
        assert result.status == "ok", f"Expected ok, got {result.status}: {getattr(result, 'error', '')}"
        # green_commit_sha must be a 40-char hex string
        assert result.data is not None
        green_sha = result.data.get("green_commit_sha")
        assert green_sha is not None, "green_commit_sha must be set on successful commit"
        assert isinstance(green_sha, str)
        assert len(green_sha) == 40, f"Expected 40-char hex SHA, got {len(green_sha)}: {green_sha!r}"
        assert re.fullmatch(r"[0-9a-f]{40}", green_sha), f"Not a valid hex SHA: {green_sha!r}"

        # AC6: HEAD must have advanced (new commit created)
        head_after = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=repo, check=True
        ).stdout.strip()
        assert head_after != head_before, "HEAD must advance after green commit"
        assert head_after == green_sha, f"HEAD {head_after!r} must equal result green_commit_sha {green_sha!r}"

        # prev.data keys must be preserved (AC6: **prev.data spread)
        assert result.data.get("cycle") == 1
        assert result.data.get("red_commit_sha") == red_sha

        # Must emit green_commit event with required keys
        commit_events = [e for e in captured if e["type"] == "green_commit"]
        assert len(commit_events) == 1, f"Expected 1 green_commit event, got {len(commit_events)}"
        payload = commit_events[0]["payload"]
        assert "paths" in payload
        assert "commit_sha" in payload
        assert "cycle" in payload
        assert "n_files" in payload
        assert payload.get("phase") == 5
        assert payload["n_files"] >= 1
        assert any("module.py" in p for p in payload["paths"])
        # AC5: event payload commit_sha must match result
        assert payload["commit_sha"] == green_sha, (
            f"Event commit_sha {payload['commit_sha']!r} must match result green_commit_sha {green_sha!r}"
        )
        # AC5: cycle propagated into event
        assert payload["cycle"] == 1

        # AC5: commit message contains "green cycle 1"
        commit_msg = subprocess.run(
            ["git", "log", "-1", "--pretty=%B"], capture_output=True, text=True, cwd=repo, check=True
        ).stdout.strip()
        assert "green cycle 1" in commit_msg, (
            f"Expected commit message to contain 'green cycle 1', got: {commit_msg!r}"
        )

    def test_commit_green_code_excludes_test_files_from_commit(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """GREEN modifies both a test file and a prod file; only prod file committed; test file remains dirty.

        AC7: test-file filter.
        """
        repo, red_sha = _make_repo_with_red_commit(tmp_path)
        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()

        # Write both a production file and a test file to working tree
        _write_file(repo, "src/module.py", "def bar(): return 99\n")
        _write_file(repo, "tests/test_x.py", "def test_new(): assert True\n")

        captured: list[dict] = []
        from bytedigger_engine.workflows import phase_5_implement
        monkeypatch.setattr(
            phase_5_implement,
            "_emit_safe",
            lambda et, p, severity="warning": captured.append(
                {"type": et, "payload": p, "severity": severity}
            ),
        )

        ctx = _make_ctx(scratchpad, str(repo))
        # 4961254A: manifest includes both paths; _is_test_path filters test file out
        prev = _make_prev(
            red_commit_sha=red_sha, cycle=1,
            worker_written_paths=["src/module.py", "tests/test_x.py"],
            manifest_source="harness_tool_record",  # 4C03CCED Ship 1C
        )

        result = _commit_green_code(ctx, prev)

        assert result.status == "ok", f"Expected ok, got {result.status}: {getattr(result, 'error', '')}"

        # Only module.py must be in the commit — test file excluded
        commit_events = [e for e in captured if e["type"] == "green_commit"]
        assert len(commit_events) == 1
        paths_committed = commit_events[0]["payload"]["paths"]
        assert any("module.py" in p for p in paths_committed), "module.py must be committed"
        assert not any("test_x.py" in p for p in paths_committed), "test_x.py must NOT be in commit"

        # Verify test file is still dirty in working tree (not committed)
        st = subprocess.run(
            ["git", "status", "--porcelain"], capture_output=True, text=True, cwd=repo
        )
        assert "test_x.py" in st.stdout, "test_x.py should remain uncommitted in working tree"

    def test_commit_green_code_errors_when_red_commit_sha_missing(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """prev.data lacks red_commit_sha → returns error with E_MISSING_RED_BOUNDARY.

        AC2: SHA boundary guard (missing case).
        """
        repo, _ = _make_repo_with_red_commit(tmp_path)
        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()

        from bytedigger_engine.workflows import phase_5_implement
        monkeypatch.setattr(
            phase_5_implement,
            "_emit_safe",
            lambda et, p, severity="warning": None,
        )

        ctx = _make_ctx(scratchpad, str(repo))
        # Deliberately omit red_commit_sha from prev.data
        prev = _make_prev(cycle=1)  # red_commit_sha NOT set

        result = _commit_green_code(ctx, prev)

        assert result.status == "error", f"Expected error, got {result.status}"
        assert result.error_code == "E_MISSING_RED_BOUNDARY", (
            f"Expected E_MISSING_RED_BOUNDARY, got {result.error_code!r}"
        )

    # ── NEW tests (cycle-2 Opus audit fixes) ─────────────────────────────────

    def test_workflow_registers_commit_green_code_second_to_last(self) -> None:
        """phase_5_implement_workflow() must list commit_green_code as the second-to-last step.

        AC1: step registered. 4C0056FA originally placed commit_green_code last.
        8FFC0E05 moved green_watchdog to the last position so a verified-good GREEN
        is committed before the watchdog observes budget breach.
        """
        from bytedigger_engine.workflows.phase_5_implement import phase_5_implement_workflow

        workflow = phase_5_implement_workflow()
        second_to_last = workflow.steps[-2]
        last_step = workflow.steps[-1]
        assert second_to_last.name == "commit_green_code", (
            f"Expected second-to-last step to be 'commit_green_code', got {second_to_last.name!r}. "
            f"All steps: {[s.name for s in workflow.steps]}"
        )
        assert last_step.name == "green_watchdog", (
            f"Expected last step to be 'green_watchdog' (8FFC0E05), got {last_step.name!r}. "
            f"All steps: {[s.name for s in workflow.steps]}"
        )

    def test_commit_green_code_errors_when_red_commit_sha_invalid(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """prev.data has red_commit_sha but value is not a valid 40-char hex SHA → E_MISSING_RED_BOUNDARY.

        AC2: SHA boundary guard (invalid value branch).
        """
        repo, _ = _make_repo_with_red_commit(tmp_path)
        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()

        from bytedigger_engine.workflows import phase_5_implement
        monkeypatch.setattr(
            phase_5_implement,
            "_emit_safe",
            lambda et, p, severity="warning": None,
        )

        ctx = _make_ctx(scratchpad, str(repo))

        for bad_sha in ("not-a-valid-sha", "abc"):
            prev = _make_prev(red_commit_sha=bad_sha, cycle=1)
            result = _commit_green_code(ctx, prev)
            assert result.status == "error", (
                f"Expected error for bad sha {bad_sha!r}, got {result.status}"
            )
            assert result.error_code == "E_MISSING_RED_BOUNDARY", (
                f"Expected E_MISSING_RED_BOUNDARY for bad sha {bad_sha!r}, got {result.error_code!r}"
            )

    def test_commit_green_code_lock_persisted_returns_e_git_locked(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """When _git_op_with_lock_retry returns lock_persisted outcome → E_GIT_LOCKED.

        AC8: lock-retry mirrors RED side.
        """
        repo, red_sha = _make_repo_with_red_commit(tmp_path)
        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()

        # Write a production file so the step reaches the git-add path
        _write_file(repo, "src/impl.py", "def impl(): pass\n")

        from bytedigger_engine.workflows import phase_5_implement
        monkeypatch.setattr(
            phase_5_implement,
            "_emit_safe",
            lambda et, p, severity="warning": None,
        )

        # Build a fake CompletedProcess-like object for the lock_persisted return
        fake_proc = MagicMock()
        fake_proc.returncode = 128
        fake_proc.stderr = "fatal: Unable to create '.git/index.lock': File exists."

        monkeypatch.setattr(
            phase_5_implement,
            "_git_op_with_lock_retry",
            lambda cmd, cwd, timeout=30: (fake_proc, "lock_persisted"),
        )

        ctx = _make_ctx(scratchpad, str(repo))
        # 4961254A: manifest drives the commit; must include the prod path
        prev = _make_prev(red_commit_sha=red_sha, cycle=1, worker_written_paths=["src/impl.py"],
                          manifest_source="harness_tool_record")  # 4C03CCED Ship 1C

        result = _commit_green_code(ctx, prev)

        assert result.status == "error", f"Expected error, got {result.status}"
        assert result.error_code == "E_GIT_LOCKED", (
            f"Expected E_GIT_LOCKED, got {result.error_code!r}"
        )
