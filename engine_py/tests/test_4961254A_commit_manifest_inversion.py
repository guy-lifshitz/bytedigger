"""RED tests for 4961254A — commit-step blocklist → worker-manifest allowlist.

Contract (frozen spec 2026-05-17):
  - New pure helper _written_paths_from_events extracts sorted/dedup write-tool
    file paths from stream-json NDJSON event dicts.
  - invoke_llm_subprocess success path adds worker_written_paths to StepResult.data.
  - All 4 commit-steps (_commit_green_code, _commit_fix_code, _commit_fix_tests,
    _run_pytest_post_fix) consume prev.data["worker_written_paths"] as the manifest
    instead of git_diff_files + blocklist.
  - Dead symbols (_is_engine_scratch, _is_session_state, _resilient_add_filter,
    _resolve_engine_scratch_prefixes, _ENGINE_SCRATCH_PREFIXES,
    _SESSION_STATE_PREFIXES) are removed entirely.
  - lib/util/git_resilient.py is deleted.
  - _is_test_path + _TEST_* constants survive as routers.

All tests MUST FAIL until GREEN implements the inversion.
Do NOT implement the contract here — RED-only file.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

HERE = Path(__file__).parent
ENGINE_ROOT = HERE.parent
sys.path.insert(0, str(ENGINE_ROOT))
sys.path.insert(0, str(ENGINE_ROOT / "lib"))
sys.path.insert(0, str(ENGINE_ROOT / "workflows"))


# ─── git repo helpers (mirrors test_phase_5_step5_commit_green_code.py) ───────


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)


def _commit_file(repo: Path, relpath: str, body: str = "# x\n", msg: str = "c") -> str:
    p = repo / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    subprocess.run(["git", "add", relpath], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", msg], cwd=repo, check=True)
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=repo, check=True
    )
    return result.stdout.strip()


def _write_file(repo: Path, relpath: str, body: str = "# impl\n") -> None:
    p = repo / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)


def _make_repo_with_base_commit(tmp_path: Path) -> tuple[Path, str]:
    """Init repo + placeholder commit. Returns (repo_path, base_sha)."""
    repo = os.path.realpath(str(tmp_path / "repo"))
    repo_path = Path(repo)
    _init_repo(repo_path)
    base_sha = _commit_file(repo_path, "src/placeholder.py", "# placeholder\n", "init")
    return repo_path, base_sha


def _make_ctx(scratchpad: Path, git_cwd: str, **org_extra):
    from contracts import WorkflowContext
    org = {"scratchpad_dir": str(scratchpad), "git_cwd": git_cwd, **org_extra}
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question="Fix the thing",
        session_id="test-session-4961254A",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_prev(**data) -> MagicMock:
    prev = MagicMock()
    prev.data = dict(data)
    return prev


# ─── AC1 ──────────────────────────────────────────────────────────────────────

class TestWrittenPathsFromEventsBasic:
    """AC1: _written_paths_from_events returns sorted dedup Write/Edit/MultiEdit file_path."""

    def test_ac1_sorted_dedup_write_edit_multiedit(self) -> None:
        """AC1: Write + Edit + MultiEdit tool_use blocks → sorted dedup list of file_path."""
        from llm_subprocess import _written_paths_from_events  # type: ignore[import]

        events = [
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "Write",
                            "input": {"file_path": "z_module.py"},
                        },
                        {
                            "type": "tool_use",
                            "name": "Edit",
                            "input": {"file_path": "a_module.py"},
                        },
                        {
                            "type": "tool_use",
                            "name": "MultiEdit",
                            "input": {"file_path": "a_module.py"},  # duplicate — dedup
                        },
                    ]
                },
            },
            {
                "type": "tool_result",  # ignored — not "assistant"
                "content": [],
            },
        ]

        result = _written_paths_from_events(events)

        assert isinstance(result, list), f"Expected list, got {type(result)}"
        assert result == ["a_module.py", "z_module.py"], (
            f"Expected sorted dedup ['a_module.py', 'z_module.py'], got {result!r}"
        )


# ─── AC2 ──────────────────────────────────────────────────────────────────────

class TestWrittenPathsFromEventsNotebook:
    """AC2: NotebookEdit uses notebook_path (and file_path when present)."""

    def test_ac2_notebook_edit_uses_notebook_path(self) -> None:
        """AC2: NotebookEdit with notebook_path (no file_path) → notebook_path collected."""
        from llm_subprocess import _written_paths_from_events  # type: ignore[import]

        events = [
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "NotebookEdit",
                            "input": {"notebook_path": "analysis.ipynb"},
                        },
                        {
                            "type": "tool_use",
                            "name": "NotebookEdit",
                            "input": {
                                "notebook_path": "other.ipynb",
                                "file_path": "other.ipynb",  # file_path also present — dedup
                            },
                        },
                    ]
                },
            }
        ]

        result = _written_paths_from_events(events)

        assert "analysis.ipynb" in result, (
            f"Expected 'analysis.ipynb' in result, got {result!r}"
        )
        assert "other.ipynb" in result, (
            f"Expected 'other.ipynb' in result, got {result!r}"
        )
        # Must be sorted and dedup
        assert result == sorted(set(result)), f"Result must be sorted dedup: {result!r}"


# ─── AC3 ──────────────────────────────────────────────────────────────────────

class TestWrittenPathsFromEventsMalformed:
    """AC3: tolerates malformed blocks without raising; returns [] for no tool_use."""

    def test_ac3_malformed_blocks_do_not_raise(self) -> None:
        """AC3: missing keys (message/content/input/name) → no exception, best-effort result."""
        from llm_subprocess import _written_paths_from_events  # type: ignore[import]

        malformed_events = [
            {},  # empty dict
            {"type": "assistant"},  # no message
            {"type": "assistant", "message": {}},  # no content
            {"type": "assistant", "message": {"content": None}},  # content=None
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "tool_use"},  # no name, no input
                        {"type": "tool_use", "name": "Write"},  # no input
                        {"type": "tool_use", "name": "Write", "input": {}},  # no file_path
                        "not_a_dict",  # block is a string, not a dict
                    ]
                },
            },
        ]

        # Must not raise
        try:
            result = _written_paths_from_events(malformed_events)
        except Exception as exc:  # noqa: BLE001
            raise AssertionError(
                f"_written_paths_from_events raised {type(exc).__name__} on malformed input: {exc}"
            ) from exc

        assert isinstance(result, list), f"Expected list even on malformed input, got {type(result)}"

    def test_ac3_empty_events_returns_empty_list(self) -> None:
        """AC3: no tool_use blocks at all → []."""
        from llm_subprocess import _written_paths_from_events  # type: ignore[import]

        assert _written_paths_from_events([]) == [], "Expected [] for empty events"

        non_tool_events = [
            {"type": "system", "message": {"content": []}},
            {"type": "result", "subtype": "success", "result": "done"},
        ]
        assert _written_paths_from_events(non_tool_events) == [], (
            "Expected [] when no tool_use blocks present"
        )


# ─── AC4 ──────────────────────────────────────────────────────────────────────

class TestInvokeLlmSubprocessWorkerWrittenPaths:
    """AC4: invoke_llm_subprocess success path sets data["worker_written_paths"]."""

    def test_ac4_worker_written_paths_key_in_success_result_data(self, monkeypatch) -> None:
        """AC4: invoke_llm_subprocess success path sets data['worker_written_paths'] from events.

        We exercise a realistic stream-json success path by monkeypatching _stream_read_events
        to return a canned events list containing a Write tool_use block. The resulting
        StepResult.data must carry 'worker_written_paths'.
        """
        import llm_subprocess

        fake_events = [
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "Edit",
                            "input": {"file_path": "lib/foo.py"},
                        }
                    ]
                },
            },
            {
                "type": "result",
                "subtype": "success",
                "result": "all good",
                "usage": {"input_tokens": 5, "output_tokens": 3},
            },
        ]

        # Patch _stream_read_events so no real subprocess is needed.
        # Return tuple: (timed_out, stdout, stderr, events, idle_aborted, cli_lingered)
        monkeypatch.setattr(
            llm_subprocess,
            "_stream_read_events",
            lambda **kwargs: (False, "", "", fake_events, False, False),
        )

        # Use a claude-p-style command so output_format_auto_injected=True path is taken
        # (stream-json events path); patch Popen to avoid real binary.
        with patch("subprocess.Popen") as mock_popen:
            proc = MagicMock()
            proc.pid = 12345
            proc.returncode = 0
            proc.stdin = MagicMock()
            proc.stdout = MagicMock()
            proc.stderr = MagicMock()
            proc.stderr.read.return_value = b""
            proc.wait.return_value = 0
            mock_popen.return_value = proc

            result = llm_subprocess.invoke_llm_subprocess(
                prompt="fix it",
                model="sonnet",
                timeout_sec=5,
                step_name="test_ac4",
            )

        assert result.status == "ok", (
            f"Expected ok, got {result.status!r}: {getattr(result, 'error', '')} "
            f"(error_code={getattr(result, 'error_code', None)!r})"
        )
        assert result.data is not None, "data must not be None on success"
        assert "worker_written_paths" in result.data, (
            f"'worker_written_paths' key must be in success data. "
            f"Keys present: {list(result.data.keys())}"
        )
        assert result.data["worker_written_paths"] == ["lib/foo.py"], (
            f"worker_written_paths must equal ['lib/foo.py'] for the canned Edit event. "
            f"Got: {result.data['worker_written_paths']!r}"
        )
        # 4C03CCED Ship 1C: claude-subprocess backend populates manifest_source="harness_tool_record"
        assert "manifest_source" in result.data, (
            f"'manifest_source' key must be in success data after Ship 1C (AC4 additive assertion). "
            f"Keys present: {list(result.data.keys())}"
        )
        assert result.data["manifest_source"] == "harness_tool_record", (
            f"claude-subprocess manifest_source must be 'harness_tool_record'; "
            f"got {result.data['manifest_source']!r}"
        )


# ─── AC5 ──────────────────────────────────────────────────────────────────────

class TestCommitGreenCodeManifest:
    """AC5: _commit_green_code uses manifest, not git_diff_files. Ambient dirt not committed."""

    def test_ac5_commits_only_manifest_prod_paths_not_ambient_dirt(
        self, tmp_path, monkeypatch
    ) -> None:
        """AC5 forcing-function: manifest=['a.py']; ambient dirt (.hal-build/x, SHARED/state/y,
        projects/z.jsonl, unrelated dirty tracked file) → commit contains ONLY a.py."""
        from phase_5_implement import _commit_green_code  # type: ignore[import]
        import phase_5_implement

        repo, base_sha = _make_repo_with_base_commit(tmp_path)
        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()

        # Write the manifest file (a.py) that the "worker" wrote
        _write_file(repo, "a.py", "def a(): pass\n")

        # Write ambient dirt on disk (these must NOT appear in commit)
        _write_file(repo, ".hal-build/x", "scratch\n")
        _write_file(repo, "SHARED/state/y", "session\n")
        _write_file(repo, "projects/z.jsonl", "{}\n")
        # Unrelated tracked-but-modified file
        (repo / "src" / "placeholder.py").write_text("# modified but not in manifest\n")

        captured: list[dict] = []
        monkeypatch.setattr(
            phase_5_implement,
            "_emit_safe",
            lambda et, p, severity="warning": captured.append({"type": et, "payload": p}),
        )
        monkeypatch.setattr(phase_5_implement, "_paths_have_staged_changes", lambda *a, **k: True)

        ctx = _make_ctx(scratchpad, str(repo))
        prev = _make_prev(
            red_commit_sha=base_sha,
            cycle=1,
            worker_written_paths=["a.py"],
            manifest_source="harness_tool_record",  # 4C03CCED Ship 1C
        )

        result = _commit_green_code(ctx, prev)

        assert result.status == "ok", f"Expected ok, got {result.status!r}: {getattr(result, 'error', '')}"
        green_sha = (result.data or {}).get("green_commit_sha")
        assert green_sha is not None, "green_commit_sha must be set on manifest commit"

        # Verify commit contents via git show --name-only
        show = subprocess.run(
            ["git", "show", "--name-only", "--format=", green_sha],
            capture_output=True, text=True, cwd=repo, check=True,
        )
        committed_files = [line.strip() for line in show.stdout.splitlines() if line.strip()]

        assert committed_files == ["a.py"], (
            f"Commit must contain ONLY 'a.py'. Got: {committed_files!r}"
        )

        # Dirt must NOT appear in the commit
        for dirt in [".hal-build/x", "SHARED/state/y", "projects/z.jsonl"]:
            assert dirt not in committed_files, (
                f"Ambient dirt '{dirt}' must NOT be in commit. Committed: {committed_files!r}"
            )


# ─── AC6 ──────────────────────────────────────────────────────────────────────

class TestCommitFixCodeManifest:
    """AC6: _commit_fix_code uses manifest, not git_diff_files. Exact-value forcing-function."""

    def test_ac6_commits_only_manifest_prod_paths_not_ambient_dirt(
        self, tmp_path, monkeypatch
    ) -> None:
        """AC6 forcing-function: manifest=['b.py']; ambient dirt present → commit contains ONLY b.py."""
        from phase_6_review import _commit_fix_code  # type: ignore[import]
        import phase_6_review

        repo, base_sha = _make_repo_with_base_commit(tmp_path)
        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()

        # Manifest file — the "worker" wrote only b.py
        _write_file(repo, "b.py", "def b(): pass\n")

        # Ambient dirt that must NOT be committed
        _write_file(repo, ".hal-build/x", "scratch\n")
        _write_file(repo, "SHARED/state/y", "session\n")
        _write_file(repo, "projects/z.jsonl", "{}\n")
        # Unrelated tracked-but-modified file
        (repo / "src" / "placeholder.py").write_text("# dirtied\n")

        captured: list[dict] = []
        monkeypatch.setattr(
            phase_6_review,
            "_emit_safe",
            lambda et, p, **kw: captured.append({"type": et, "payload": p}),
        )

        ctx = _make_ctx(scratchpad, str(repo))
        prev = _make_prev(
            pre_fix_sha=base_sha,
            cycle=2,
            worker_written_paths=["b.py"],
            manifest_source="harness_tool_record",  # 4C03CCED Ship 1C
        )

        result = _commit_fix_code(ctx, prev)

        assert result.status == "ok", f"Expected ok, got {result.status!r}: {getattr(result, 'error', '')}"
        fix_sha = (result.data or {}).get("fix_commit_sha")
        assert fix_sha is not None, "fix_commit_sha must be set on manifest commit"

        show = subprocess.run(
            ["git", "show", "--name-only", "--format=", fix_sha],
            capture_output=True, text=True, cwd=repo, check=True,
        )
        committed_files = [line.strip() for line in show.stdout.splitlines() if line.strip()]

        assert committed_files == ["b.py"], (
            f"Commit must contain ONLY 'b.py'. Got: {committed_files!r}"
        )
        for dirt in [".hal-build/x", "SHARED/state/y", "projects/z.jsonl"]:
            assert dirt not in committed_files, (
                f"Ambient dirt '{dirt}' must NOT be in commit. Committed: {committed_files!r}"
            )


# ─── AC7 ──────────────────────────────────────────────────────────────────────

class TestCommitFixTestsManifest:
    """AC7: _commit_fix_tests commits exactly manifest paths where _is_test_path(p)."""

    def test_ac7_commits_only_test_paths_from_manifest(
        self, tmp_path, monkeypatch
    ) -> None:
        """AC7 forcing-function: manifest=['tests/test_real_change.py']; tree also has
        tests/test_ambient_unrelated.py (untracked, _is_test_path=True, NOT in manifest).
        Post-GREEN (manifest path): ONLY tests/test_real_change.py committed.
        Today's code (git_diff_files + _is_test_path filter): BOTH test files committed → FAIL.
        """
        from phase_6_review import _commit_fix_tests  # type: ignore[import]
        import phase_6_review

        repo, base_sha = _make_repo_with_base_commit(tmp_path)
        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()

        # The manifest file — the "worker" wrote only this test file
        _write_file(repo, "tests/test_real_change.py", "def test_real(): pass\n")

        # Ambient test file on disk, NOT in manifest — today's blocklist code would
        # commit it (git_diff_files sees it, _is_test_path=True, not scratch/session-state).
        # Manifest-based code must NOT commit it.
        _write_file(repo, "tests/test_ambient_unrelated.py", "def test_ambient(): pass\n")

        # Also add non-test ambient dirt to mirror AC5/AC6 pattern
        _write_file(repo, ".hal-build/scratch.txt", "scratch\n")
        _write_file(repo, "SHARED/state/junk.txt", "session\n")

        captured: list[dict] = []
        monkeypatch.setattr(
            phase_6_review,
            "_emit_safe",
            lambda et, p, **kw: captured.append({"type": et, "payload": p}),
        )

        ctx = _make_ctx(scratchpad, str(repo))
        prev = _make_prev(
            pre_fix_sha=base_sha,
            cycle=2,
            # Manifest contains ONLY tests/test_real_change.py (the one the worker wrote)
            worker_written_paths=["tests/test_real_change.py"],
            manifest_source="harness_tool_record",  # 4C03CCED Ship 1C
        )

        result = _commit_fix_tests(ctx, prev)

        assert result.status == "ok", f"Expected ok, got {result.status!r}: {getattr(result, 'error', '')}"
        fix_test_sha = (result.data or {}).get("fix_test_commit_sha")
        assert fix_test_sha is not None, "fix_test_commit_sha must be set when test files are in manifest"

        show = subprocess.run(
            ["git", "show", "--name-only", "--format=", fix_test_sha],
            capture_output=True, text=True, cwd=repo, check=True,
        )
        committed_files = [line.strip() for line in show.stdout.splitlines() if line.strip()]

        # Exact-value forcing function: ONLY the manifest test file, nothing else
        assert committed_files == ["tests/test_real_change.py"], (
            f"Commit must contain EXACTLY ['tests/test_real_change.py']. Got: {committed_files!r}"
        )

        # Ambient test file must NOT be committed even though it is _is_test_path=True
        assert "tests/test_ambient_unrelated.py" not in committed_files, (
            f"Ambient test file tests/test_ambient_unrelated.py must NOT be committed "
            f"(not in manifest). Committed: {committed_files!r}"
        )


# ─── AC8 ──────────────────────────────────────────────────────────────────────

class TestRunPytestPostFixScope:
    """AC8: _run_pytest_post_fix scope = red_test_paths if present else manifest∩_is_test_py_path; no git_diff_files fallback."""

    def test_ac8_no_git_diff_files_fallback_when_manifest_present(
        self, tmp_path, monkeypatch
    ) -> None:
        """AC8: git_diff_files must NOT be called when worker_written_paths is in prev.data."""
        from phase_6_review import _run_pytest_post_fix  # type: ignore[import]
        import phase_6_review

        repo, base_sha = _make_repo_with_base_commit(tmp_path)
        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()

        git_diff_called = []

        def _fake_git_diff(sha, root, **kwargs):
            git_diff_called.append((sha, root))
            return []

        monkeypatch.setattr(phase_6_review, "git_diff_files", _fake_git_diff)
        monkeypatch.setattr(
            phase_6_review,
            "_emit_safe",
            lambda et, p, **kw: None,
        )

        ctx = _make_ctx(scratchpad, str(repo))
        prev = _make_prev(
            pre_fix_sha=base_sha,
            cycle=2,
            # manifest has test file; red_test_paths absent → should use manifest, not git_diff
            worker_written_paths=["tests/test_bar.py"],
            manifest_source="harness_tool_record",  # 4C03CCED Ship 1C
        )

        # Patch subprocess.run to avoid real pytest execution; capture pytest invocations
        with patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="", stderr="")):
            _run_pytest_post_fix(ctx, prev)

        assert not git_diff_called, (
            f"git_diff_files must NOT be called when worker_written_paths present. "
            f"Called with: {git_diff_called!r}"
        )

    def test_ac8_red_test_paths_takes_priority_over_manifest(
        self, tmp_path, monkeypatch
    ) -> None:
        """AC8: red_test_paths channel takes priority over worker_written_paths."""
        from phase_6_review import _run_pytest_post_fix  # type: ignore[import]
        import phase_6_review

        repo, base_sha = _make_repo_with_base_commit(tmp_path)
        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()

        scope_used: list[list] = []

        def _capture_scope(cmd, **kwargs):
            if "pytest" in str(cmd):
                scope_used.append(list(cmd))
            return MagicMock(returncode=0, stdout="", stderr="")

        monkeypatch.setattr(phase_6_review, "git_diff_files", lambda *a, **kw: [])
        monkeypatch.setattr(
            phase_6_review,
            "_emit_safe",
            lambda et, p, **kw: None,
        )

        ctx = _make_ctx(scratchpad, str(repo))
        prev = _make_prev(
            pre_fix_sha=base_sha,
            cycle=2,
            red_test_paths=["tests/test_red_specific.py"],
            worker_written_paths=["tests/test_different.py"],
            manifest_source="harness_tool_record",  # 4C03CCED Ship 1C
        )

        with patch("subprocess.run", side_effect=_capture_scope):
            _run_pytest_post_fix(ctx, prev)

        # red_test_paths must be in scope, not worker_written_paths
        # When pytest IS invoked (non-empty scope), test_red_specific must appear in args,
        # test_different must NOT (it's only in worker_written_paths, not red_test_paths).
        all_scope_args = [arg for call in scope_used for arg in call]
        if scope_used:  # only assert when pytest was actually invoked
            assert any("test_red_specific" in str(a) for a in all_scope_args), (
                f"red_test_paths must take priority over worker_written_paths. "
                f"Expected 'test_red_specific' in scope args, got: {all_scope_args!r}"
            )
            assert not any("test_different" in str(a) for a in all_scope_args), (
                f"worker_written_paths 'test_different' must NOT appear when red_test_paths present. "
                f"Scope args: {all_scope_args!r}"
            )


# ─── AC9 ──────────────────────────────────────────────────────────────────────

class TestEmptyManifestGuard:
    """AC9: empty/missing manifest → <step>_skipped event reason='empty_manifest', ok, sha=None."""

    def test_ac9_commit_green_code_empty_manifest_skip(
        self, tmp_path, monkeypatch
    ) -> None:
        """AC9: worker_written_paths=[] → green_commit_skipped reason='empty_manifest', ok, sha=None."""
        from phase_5_implement import _commit_green_code  # type: ignore[import]
        import phase_5_implement

        repo, base_sha = _make_repo_with_base_commit(tmp_path)
        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()
        captured: list[dict] = []
        monkeypatch.setattr(
            phase_5_implement,
            "_emit_safe",
            lambda et, p, severity="warning": captured.append({"type": et, "payload": p}),
        )

        ctx = _make_ctx(scratchpad, str(repo))
        prev = _make_prev(red_commit_sha=base_sha, cycle=1, worker_written_paths=[],
                          manifest_source="harness_tool_record")  # 4C03CCED Ship 1C

        result = _commit_green_code(ctx, prev)

        assert result.status == "ok", f"Expected ok, got {result.status!r}"
        assert (result.data or {}).get("green_commit_sha") is None, (
            "green_commit_sha must be None on empty-manifest skip"
        )

        skip_events = [e for e in captured if e["type"] == "green_commit_skipped"]
        assert len(skip_events) == 1, (
            f"Expected 1 green_commit_skipped event. All events: {[e['type'] for e in captured]}"
        )
        assert skip_events[0]["payload"].get("reason") == "empty_manifest", (
            f"Expected reason='empty_manifest', got {skip_events[0]['payload'].get('reason')!r}"
        )

    def test_ac9_commit_fix_code_missing_manifest_returns_error(
        self, tmp_path, monkeypatch
    ) -> None:
        """AC9 (updated for 4C03CCED Ship 1C): worker_written_paths absent from prev.data
        → strict consumer gate fires → status='error', error_code='E_LLM_MANIFEST_MISSING_AT_CONSUMER',
        recoverable=False.

        Pre-Ship-1C behavior was silent-skip (status='ok', fix_commit_sha=None).
        Ship 1C reverses this: absent manifest is now a hard consumer-contract violation
        per spec §1n behavioral DELTA table. The silent-failure F9F7E4FD pattern is
        structurally unrepresentable — missing manifest is always an error.
        """
        from phase_6_review import _commit_fix_code  # type: ignore[import]
        import phase_6_review

        repo, base_sha = _make_repo_with_base_commit(tmp_path)
        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()

        captured: list[dict] = []
        monkeypatch.setattr(
            phase_6_review,
            "_emit_safe",
            lambda et, p, **kw: captured.append({"type": et, "payload": p}),
        )

        ctx = _make_ctx(scratchpad, str(repo))
        # worker_written_paths intentionally absent — triggers strict consumer gate
        prev = _make_prev(pre_fix_sha=base_sha, cycle=2)

        result = _commit_fix_code(ctx, prev)

        # Ship 1C: missing manifest → error (not silent skip)
        assert result.status == "error", (
            f"Expected error (strict manifest gate), got {result.status!r}"
        )
        assert result.error_code == "E_LLM_MANIFEST_MISSING_AT_CONSUMER", (
            f"Expected E_LLM_MANIFEST_MISSING_AT_CONSUMER, got {result.error_code!r}"
        )
        assert result.recoverable is False, (
            f"Expected recoverable=False (config-class violation), got {result.recoverable!r}"
        )

    def test_ac9_no_git_add_invoked_on_empty_manifest(
        self, tmp_path, monkeypatch
    ) -> None:
        """AC9: empty manifest → git add must NOT be invoked."""
        from phase_5_implement import _commit_green_code  # type: ignore[import]
        import phase_5_implement

        repo, base_sha = _make_repo_with_base_commit(tmp_path)
        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()

        git_add_called = []

        original_git_op = None
        try:
            import phase_5_implement as p5
            original_git_op = getattr(p5, "_git_op_with_lock_retry", None)
        except Exception:
            pass

        if original_git_op is not None:
            def _fake_git_op(cmd, **kwargs):
                if "add" in cmd:
                    git_add_called.append(cmd)
                return MagicMock(returncode=0, stdout="", stderr=""), "ok"
            monkeypatch.setattr(phase_5_implement, "_git_op_with_lock_retry", _fake_git_op)

        monkeypatch.setattr(
            phase_5_implement,
            "_emit_safe",
            lambda et, p, severity="warning": None,
        )

        ctx = _make_ctx(scratchpad, str(repo))
        prev = _make_prev(red_commit_sha=base_sha, cycle=1, worker_written_paths=[],
                          manifest_source="harness_tool_record")  # 4C03CCED Ship 1C

        result = _commit_green_code(ctx, prev)

        assert result.status == "ok", f"Expected ok, got {result.status!r}"
        assert not git_add_called, (
            f"git add must NOT be called on empty manifest. Called with: {git_add_called!r}"
        )


# ─── AC10 ─────────────────────────────────────────────────────────────────────

class TestDeadCodeGone:
    """AC10: deleted symbols have zero occurrences in engine_py/workflows/ and engine_py/lib/."""

    DEAD_SYMBOLS = [
        "_is_engine_scratch",
        "_is_session_state",
        "_resilient_add_filter",
        "_resolve_engine_scratch_prefixes",
        "_ENGINE_SCRATCH_PREFIXES",
        "_SESSION_STATE_PREFIXES",
    ]
    SEARCH_ROOTS = ["workflows", "lib"]

    def _grep_symbol(self, symbol: str) -> list[tuple[Path, int, str]]:
        """Return list of (file, lineno, line) for all occurrences of symbol in engine_py search roots."""
        matches = []
        for root_name in self.SEARCH_ROOTS:
            root = ENGINE_ROOT / root_name
            if not root.exists():
                continue
            for py_file in root.rglob("*.py"):
                for lineno, line in enumerate(py_file.read_text(errors="replace").splitlines(), 1):
                    if symbol in line:
                        matches.append((py_file, lineno, line))
        return matches

    def test_ac10_is_engine_scratch_not_present(self) -> None:
        """AC10: _is_engine_scratch must not appear anywhere in workflows/ or lib/."""
        matches = self._grep_symbol("_is_engine_scratch")
        assert not matches, (
            f"_is_engine_scratch still present (dead code — must be deleted). "
            f"Occurrences: {[(str(f.relative_to(ENGINE_ROOT)), n) for f, n, _ in matches]}"
        )

    def test_ac10_is_session_state_not_present(self) -> None:
        """AC10: _is_session_state must not appear anywhere in workflows/ or lib/."""
        matches = self._grep_symbol("_is_session_state")
        assert not matches, (
            f"_is_session_state still present (dead code — must be deleted). "
            f"Occurrences: {[(str(f.relative_to(ENGINE_ROOT)), n) for f, n, _ in matches]}"
        )

    def test_ac10_resilient_add_filter_not_present(self) -> None:
        """AC10: _resilient_add_filter must not appear anywhere in workflows/ or lib/."""
        matches = self._grep_symbol("_resilient_add_filter")
        assert not matches, (
            f"_resilient_add_filter still present (dead code — must be deleted). "
            f"Occurrences: {[(str(f.relative_to(ENGINE_ROOT)), n) for f, n, _ in matches]}"
        )

    def test_ac10_resolve_engine_scratch_prefixes_not_present(self) -> None:
        """AC10: _resolve_engine_scratch_prefixes must not appear anywhere in workflows/ or lib/."""
        matches = self._grep_symbol("_resolve_engine_scratch_prefixes")
        assert not matches, (
            f"_resolve_engine_scratch_prefixes still present (dead code — must be deleted). "
            f"Occurrences: {[(str(f.relative_to(ENGINE_ROOT)), n) for f, n, _ in matches]}"
        )

    def test_ac10_engine_scratch_prefixes_constant_not_present(self) -> None:
        """AC10: _ENGINE_SCRATCH_PREFIXES must not appear anywhere in workflows/ or lib/."""
        matches = self._grep_symbol("_ENGINE_SCRATCH_PREFIXES")
        assert not matches, (
            f"_ENGINE_SCRATCH_PREFIXES still present (dead code — must be deleted). "
            f"Occurrences: {[(str(f.relative_to(ENGINE_ROOT)), n) for f, n, _ in matches]}"
        )

    def test_ac10_session_state_prefixes_constant_not_present(self) -> None:
        """AC10: _SESSION_STATE_PREFIXES must not appear anywhere in workflows/ or lib/."""
        matches = self._grep_symbol("_SESSION_STATE_PREFIXES")
        assert not matches, (
            f"_SESSION_STATE_PREFIXES still present (dead code — must be deleted). "
            f"Occurrences: {[(str(f.relative_to(ENGINE_ROOT)), n) for f, n, _ in matches]}"
        )


# ─── AC11 ─────────────────────────────────────────────────────────────────────

class TestGitResilientDeleted:
    """AC11: lib/util/git_resilient.py deleted; no import of it remains in engine_py."""

    def test_ac11_git_resilient_file_does_not_exist(self) -> None:
        """AC11: lib/util/git_resilient.py must not exist after GREEN."""
        git_resilient = ENGINE_ROOT / "lib" / "util" / "git_resilient.py"
        assert not git_resilient.exists(), (
            f"lib/util/git_resilient.py must be deleted by GREEN. "
            f"Path still exists: {git_resilient}"
        )

    def test_ac11_no_import_of_git_resilient_in_engine_py(self) -> None:
        """AC11: no file under engine_py imports git_resilient."""
        import_matches: list[tuple[Path, int, str]] = []
        for py_file in ENGINE_ROOT.rglob("*.py"):
            for lineno, line in enumerate(py_file.read_text(errors="replace").splitlines(), 1):
                if "git_resilient" in line and not py_file.name.startswith("test_4961254A"):
                    import_matches.append((py_file, lineno, line))

        assert not import_matches, (
            f"git_resilient still imported. Occurrences: "
            f"{[(str(f.relative_to(ENGINE_ROOT)), n) for f, n, _ in import_matches]}"
        )


# ─── AC12 ─────────────────────────────────────────────────────────────────────

class TestIsTestPathSurvives:
    """AC12: _is_test_path + _TEST_* constants survive; imported by both phases (router regression guard)."""

    def test_ac12_is_test_path_importable_from_path_classifier(self) -> None:
        """AC12: _is_test_path still importable from lib.util.path_classifier."""
        try:
            from lib.util.path_classifier import _is_test_path  # type: ignore[import]
        except ImportError:
            # try direct import (sys.path includes lib/)
            from util.path_classifier import _is_test_path  # type: ignore[import]

        assert callable(_is_test_path), "_is_test_path must be a callable"
        # Sanity-check basic routing behaviour
        assert _is_test_path("tests/test_foo.py") is True
        assert _is_test_path("src/impl.py") is False

    def test_ac12_test_filename_patterns_constant_present(self) -> None:
        """AC12: _TEST_FILENAME_PATTERNS still exists in path_classifier."""
        try:
            from lib.util.path_classifier import _TEST_FILENAME_PATTERNS  # type: ignore[import]
        except ImportError:
            from util.path_classifier import _TEST_FILENAME_PATTERNS  # type: ignore[import]

        assert isinstance(_TEST_FILENAME_PATTERNS, tuple), (
            f"_TEST_FILENAME_PATTERNS must be a tuple, got {type(_TEST_FILENAME_PATTERNS)}"
        )
        assert len(_TEST_FILENAME_PATTERNS) > 0, "_TEST_FILENAME_PATTERNS must not be empty"

    def test_ac12_test_path_segments_constant_present(self) -> None:
        """AC12: _TEST_PATH_SEGMENTS still exists in path_classifier."""
        try:
            from lib.util.path_classifier import _TEST_PATH_SEGMENTS  # type: ignore[import]
        except ImportError:
            from util.path_classifier import _TEST_PATH_SEGMENTS  # type: ignore[import]

        assert isinstance(_TEST_PATH_SEGMENTS, tuple), (
            f"_TEST_PATH_SEGMENTS must be a tuple, got {type(_TEST_PATH_SEGMENTS)}"
        )
        assert "tests" in _TEST_PATH_SEGMENTS, "'tests' must be in _TEST_PATH_SEGMENTS"

    def test_ac12_phase_5_imports_is_test_path(self) -> None:
        """AC12: phase_5_implement imports _is_test_path (still uses it for routing)."""
        import phase_5_implement  # type: ignore[import]

        assert hasattr(phase_5_implement, "_is_test_path") or (
            "_is_test_path" in dir(phase_5_implement)
        ), "phase_5_implement must still reference _is_test_path"

    def test_ac12_phase_6_imports_is_test_path(self) -> None:
        """AC12: phase_6_review imports _is_test_path (still uses it for routing)."""
        import phase_6_review  # type: ignore[import]

        assert hasattr(phase_6_review, "_is_test_path") or (
            "_is_test_path" in dir(phase_6_review)
        ), "phase_6_review must still reference _is_test_path"


# ─── AC13 ─────────────────────────────────────────────────────────────────────

class TestCommitManifestResolvedTelemetry:
    """AC13: Each of the 3 commit-steps emits commit_manifest_resolved with
    {n_manifest, n_committed, step, phase} BEFORE git add.

    This event does NOT exist today → all 3 tests FAIL until GREEN ships it.
    """

    def test_ac13_commit_green_code_emits_manifest_resolved(
        self, tmp_path, monkeypatch
    ) -> None:
        """AC13: _commit_green_code emits commit_manifest_resolved before git add.

        Manifest = ['prod_a.py', 'prod_b.py', 'tests/test_x.py'] (3 total).
        Prod-only router: n_committed = 2 (prod_a + prod_b, test_x routed away).
        step='_commit_green_code', phase=5.
        """
        from phase_5_implement import _commit_green_code  # type: ignore[import]
        import phase_5_implement

        repo, base_sha = _make_repo_with_base_commit(tmp_path)
        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()

        # Materialize manifest files on disk so git add succeeds
        _write_file(repo, "prod_a.py", "def a(): pass\n")
        _write_file(repo, "prod_b.py", "def b(): pass\n")
        _write_file(repo, "tests/test_x.py", "def test_x(): pass\n")

        captured: list[dict] = []
        monkeypatch.setattr(
            phase_5_implement,
            "_emit_safe",
            lambda et, p, severity="warning": captured.append({"type": et, "payload": p}),
        )
        monkeypatch.setattr(phase_5_implement, "_paths_have_staged_changes", lambda *a, **k: True)

        ctx = _make_ctx(scratchpad, str(repo))
        prev = _make_prev(
            red_commit_sha=base_sha,
            cycle=1,
            worker_written_paths=["prod_a.py", "prod_b.py", "tests/test_x.py"],
            manifest_source="harness_tool_record",  # 4C03CCED Ship 1C
        )

        _commit_green_code(ctx, prev)

        resolved = [e for e in captured if e["type"] == "commit_manifest_resolved"]
        assert len(resolved) == 1, (
            f"Expected exactly 1 commit_manifest_resolved event, got {len(resolved)}. "
            f"All event types: {[e['type'] for e in captured]}"
        )
        payload = resolved[0]["payload"]
        assert payload.get("n_manifest") == 3, (
            f"n_manifest must be 3 (total manifest size). Got: {payload.get('n_manifest')!r}"
        )
        assert payload.get("n_committed") == 2, (
            f"n_committed must be 2 (prod-only: prod_a + prod_b). Got: {payload.get('n_committed')!r}"
        )
        assert payload.get("step") == "commit_green_code", (
            f"step must be 'commit_green_code'. Got: {payload.get('step')!r}"
        )
        assert payload.get("phase") == 5, (
            f"phase must be 5. Got: {payload.get('phase')!r}"
        )

    def test_ac13_commit_fix_code_emits_manifest_resolved(
        self, tmp_path, monkeypatch
    ) -> None:
        """AC13: _commit_fix_code emits commit_manifest_resolved before git add.

        Manifest = ['src/fix.py', 'tests/test_fix.py'] (2 total).
        Prod-only router: n_committed = 1 (src/fix.py; test_fix routed away).
        step='_commit_fix_code', phase=6.
        """
        from phase_6_review import _commit_fix_code  # type: ignore[import]
        import phase_6_review

        repo, base_sha = _make_repo_with_base_commit(tmp_path)
        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()

        # Materialize manifest files on disk so git add succeeds
        _write_file(repo, "src/fix.py", "def fix(): pass\n")
        _write_file(repo, "tests/test_fix.py", "def test_fix(): pass\n")

        captured: list[dict] = []
        monkeypatch.setattr(
            phase_6_review,
            "_emit_safe",
            lambda et, p, **kw: captured.append({"type": et, "payload": p}),
        )

        ctx = _make_ctx(scratchpad, str(repo))
        prev = _make_prev(
            pre_fix_sha=base_sha,
            cycle=2,
            worker_written_paths=["src/fix.py", "tests/test_fix.py"],
            manifest_source="harness_tool_record",  # 4C03CCED Ship 1C
        )

        _commit_fix_code(ctx, prev)

        resolved = [e for e in captured if e["type"] == "commit_manifest_resolved"]
        assert len(resolved) == 1, (
            f"Expected exactly 1 commit_manifest_resolved event, got {len(resolved)}. "
            f"All event types: {[e['type'] for e in captured]}"
        )
        payload = resolved[0]["payload"]
        assert payload.get("n_manifest") == 2, (
            f"n_manifest must be 2 (total manifest size). Got: {payload.get('n_manifest')!r}"
        )
        assert payload.get("n_committed") == 1, (
            f"n_committed must be 1 (prod-only: src/fix.py). Got: {payload.get('n_committed')!r}"
        )
        assert payload.get("step") == "commit_fix_code", (
            f"step must be 'commit_fix_code'. Got: {payload.get('step')!r}"
        )
        assert payload.get("phase") == 6, (
            f"phase must be 6. Got: {payload.get('phase')!r}"
        )

    def test_ac13_commit_fix_tests_emits_manifest_resolved(
        self, tmp_path, monkeypatch
    ) -> None:
        """AC13: _commit_fix_tests emits commit_manifest_resolved before git add.

        Manifest = ['src/impl.py', 'tests/test_a.py', 'tests/test_b.py'] (3 total).
        Test-only router: n_committed = 2 (test_a + test_b; impl.py routed away).
        step='_commit_fix_tests', phase=6.
        """
        from phase_6_review import _commit_fix_tests  # type: ignore[import]
        import phase_6_review

        repo, base_sha = _make_repo_with_base_commit(tmp_path)
        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()

        # Materialize manifest files on disk so git add succeeds
        _write_file(repo, "src/impl.py", "def impl(): pass\n")
        _write_file(repo, "tests/test_a.py", "def test_a(): pass\n")
        _write_file(repo, "tests/test_b.py", "def test_b(): pass\n")

        captured: list[dict] = []
        monkeypatch.setattr(
            phase_6_review,
            "_emit_safe",
            lambda et, p, **kw: captured.append({"type": et, "payload": p}),
        )

        ctx = _make_ctx(scratchpad, str(repo))
        prev = _make_prev(
            pre_fix_sha=base_sha,
            cycle=2,
            worker_written_paths=["src/impl.py", "tests/test_a.py", "tests/test_b.py"],
            manifest_source="harness_tool_record",  # 4C03CCED Ship 1C
        )

        _commit_fix_tests(ctx, prev)

        resolved = [e for e in captured if e["type"] == "commit_manifest_resolved"]
        assert len(resolved) == 1, (
            f"Expected exactly 1 commit_manifest_resolved event, got {len(resolved)}. "
            f"All event types: {[e['type'] for e in captured]}"
        )
        payload = resolved[0]["payload"]
        assert payload.get("n_manifest") == 3, (
            f"n_manifest must be 3 (total manifest size). Got: {payload.get('n_manifest')!r}"
        )
        assert payload.get("n_committed") == 2, (
            f"n_committed must be 2 (test-only: test_a + test_b). Got: {payload.get('n_committed')!r}"
        )
        assert payload.get("step") == "commit_fix_tests", (
            f"step must be 'commit_fix_tests'. Got: {payload.get('step')!r}"
        )
        assert payload.get("phase") == 6, (
            f"phase must be 6. Got: {payload.get('phase')!r}"
        )
