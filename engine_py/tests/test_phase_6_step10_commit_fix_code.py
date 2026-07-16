"""RED tests for 7547E02F — phase_6 _commit_fix_code (engine-owned FIX commit).

Contract: after Phase 6's write_fix_artifact succeeds, _commit_fix_code:
  - reads pre_fix_sha from prev.data as SHA boundary (falls back to resolve_pre_phase_sha)
  - returns E_MISSING_FIX_BOUNDARY when neither source yields a valid SHA
  - skips commit + emits fix_commit_skipped when no production paths found
  - commits production paths + emits fix_commit event with sha/paths/n_files/cycle/phase=6
  - excludes test files from the commit (fix CODE, never tests)
  - returns StepResult.data = {**prev.data, "fix_commit_sha": <sha-or-None>}
  - phase_6_review_workflow() registers commit_fix_code as the LAST step

All tests MUST FAIL until GREEN agent implements _commit_fix_code in phase_6_review.py.

Do NOT implement the contract here — RED-only file.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call

HERE = Path(__file__).parent
ENGINE_ROOT = HERE.parent
sys.path.insert(0, str(ENGINE_ROOT))
sys.path.insert(0, str(ENGINE_ROOT / "lib"))
sys.path.insert(0, str(ENGINE_ROOT / "workflows"))

# These imports WILL fail with ImportError until GREEN implements _commit_fix_code.
# That ImportError IS the RED signal — correct behavior.
from phase_6_review import _commit_fix_code, phase_6_review_workflow  # noqa: E402
from contracts import WorkflowContext, StepResult  # noqa: E402
from lib.git_port import GitResult, set_default_git_read_factory, reset_default_git_read_factory  # noqa: E402


# ─── helpers ──────────────────────────────────────────────────────────────────

_VALID_SHA = "a" * 40  # valid 40-char hex SHA fixture


class _SpyGitRead:
    """Args-aware stub for git_port.git_read — D5D6A364 repoint helper.

    Used by the 5 repointed tests to stub the post-commit rev-parse call that
    after GREEN routes through git_port.git_read instead of bounded_run.

    Args-aware dispatch (D5D6A364 Opus fix): _filter_gitignored_paths in
    phase_workflows_common calls git_read with ["check-ignore",...] on the same
    injected seam.  Return rc=0/empty for those so prod_paths are not filtered
    out before reaching the rev-parse Point.  All other args (rev-parse) return
    self.result (the configured post_sha GitResult).
    """

    def __init__(self, result: GitResult) -> None:
        self.result = result
        self.calls: list[tuple] = []

    def __call__(
        self,
        args: list[str],
        *,
        cwd: str | None = None,
        timeout: float | None = None,
        dir_: str | None = None,
    ) -> GitResult:
        self.calls.append((args, cwd, timeout))
        if args[:1] == ["check-ignore"]:
            return GitResult(returncode=0, stdout="", stderr="", timed_out=False)
        return self.result


def _make_ctx(tmp_path: Path, **org_extra) -> WorkflowContext:
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    git_cwd = tmp_path / "repo"
    git_cwd.mkdir(parents=True, exist_ok=True)
    org = {
        "scratchpad_dir": str(scratchpad),
        "git_cwd": str(git_cwd),
        **org_extra,
    }
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question="Apply fix",
        session_id="test-session-fix",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_prev(pre_fix_sha=None, cycle: int = 2, **extra) -> MagicMock:
    """Minimal prev StepResult-like object with data dict."""
    prev = MagicMock()
    data: dict = {"cycle": cycle, **extra}
    if pre_fix_sha is not None:
        data["pre_fix_sha"] = pre_fix_sha
    prev.data = data
    return prev


# ═══════════════════════════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestCommitFixCode:
    """_commit_fix_code: engine-authoritative FIX commit step (7547E02F)."""

    # ── AC1: step registration ─────────────────────────────────────────────────

    def test_commit_fix_code_workflow_registration(self) -> None:
        """phase_6_review_workflow() must list commit_fix_code immediately after write_fix_artifact.

        AC1: commit_fix_code registered between write_fix_artifact and build_satisfaction_prompt;
        detect_mass_unverified must remain the LAST step (pre-existing invariant).
        """
        workflow = phase_6_review_workflow()
        step_names = [s.name for s in workflow.steps]

        assert "commit_fix_code" in step_names, (
            f"'commit_fix_code' not found in workflow steps: {step_names!r}"
        )

        idx_write_fix = step_names.index("write_fix_artifact")
        idx_commit = step_names.index("commit_fix_code")
        assert idx_commit == idx_write_fix + 1, (
            f"Expected 'commit_fix_code' immediately after 'write_fix_artifact' "
            f"(positions {idx_write_fix+1} vs {idx_commit}). All steps: {step_names!r}"
        )

        assert step_names[-1] == "detect_mass_unverified", (
            f"Expected last step to be 'detect_mass_unverified', got {step_names[-1]!r}. "
            f"All steps: {step_names!r}"
        )

    # ── AC2: SHA boundary resolution ───────────────────────────────────────────

    def test_commit_fix_code_missing_boundary(self, tmp_path: Path, monkeypatch) -> None:
        """prev.data has no pre_fix_sha AND resolve_pre_phase_sha returns empty → E_MISSING_FIX_BOUNDARY.

        AC2: SHA boundary guard, both paths exhausted.
        """
        import phase_6_review

        monkeypatch.setattr(
            phase_6_review,
            "resolve_pre_phase_sha",
            lambda worktree_root: "",
        )
        monkeypatch.setattr(
            phase_6_review,
            "_emit_safe",
            lambda et, p, **kw: None,
        )

        ctx = _make_ctx(tmp_path)
        prev = _make_prev(cycle=1)  # no pre_fix_sha

        result = _commit_fix_code(ctx, prev)

        assert result.status == "error", f"Expected error, got {result.status!r}"
        assert result.error_code == "E_MISSING_FIX_BOUNDARY", (
            f"Expected E_MISSING_FIX_BOUNDARY, got {result.error_code!r}"
        )

    def test_commit_fix_code_invalid_pre_fix_sha_in_prev_data(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """prev.data has pre_fix_sha but value is not valid AND fallback also returns empty → E_MISSING_FIX_BOUNDARY.

        AC2: SHA boundary guard (invalid value branch — not-hex, too-short, right-length-not-hex).
        """
        import phase_6_review

        monkeypatch.setattr(
            phase_6_review,
            "resolve_pre_phase_sha",
            lambda worktree_root: "",
        )
        monkeypatch.setattr(
            phase_6_review,
            "_emit_safe",
            lambda et, p, **kw: None,
        )

        ctx = _make_ctx(tmp_path)

        for bad_sha in ("not-a-valid-sha", "abc", "Z" * 40):
            prev = _make_prev(pre_fix_sha=bad_sha, cycle=1)
            result = _commit_fix_code(ctx, prev)
            assert result.status == "error", (
                f"Expected error for bad sha {bad_sha!r}, got {result.status!r}"
            )
            assert result.error_code == "E_MISSING_FIX_BOUNDARY", (
                f"Expected E_MISSING_FIX_BOUNDARY for bad sha {bad_sha!r}, got {result.error_code!r}"
            )

    # ── AC3: empty-paths skip ──────────────────────────────────────────────────

    def test_commit_fix_code_empty_paths_skip(self, tmp_path: Path, monkeypatch) -> None:
        """Valid pre_fix_sha in prev.data; no worker_written_paths → ok, fix_commit_sha=None, fix_commit_skipped event emitted.

        AC3: empty manifest → skip path (4961254A manifest allowlist inversion).
        """
        import phase_6_review

        captured: list[dict] = []
        monkeypatch.setattr(
            phase_6_review,
            "_emit_safe",
            lambda et, p, **kw: captured.append({"type": et, "payload": p}),
        )

        ctx = _make_ctx(tmp_path)
        # 4961254A: no worker_written_paths → empty manifest → skip
        prev = _make_prev(pre_fix_sha=_VALID_SHA, cycle=2, spec_path="/tmp/spec.md", worker_written_paths=[], manifest_source="harness_tool_record")

        result = _commit_fix_code(ctx, prev)

        assert result.status == "ok", f"Expected ok, got {result.status!r}"
        assert result.data is not None
        assert result.data.get("fix_commit_sha") is None, (
            "fix_commit_sha must be None on skip"
        )

        skip_events = [e for e in captured if e["type"] == "fix_commit_skipped"]
        assert len(skip_events) == 1, (
            f"Expected 1 fix_commit_skipped event, got {len(skip_events)}. All events: {[e['type'] for e in captured]}"
        )
        payload = skip_events[0]["payload"]
        assert payload.get("reason") == "empty_manifest", (
            f"Expected reason='empty_manifest', got {payload.get('reason')!r}"
        )
        assert payload.get("phase") == 6, (
            f"Expected phase=6, got {payload.get('phase')!r}"
        )

        # AC3 + AC6: prev.data must be spread into result.data even on skip path
        assert result.data["pre_fix_sha"] == _VALID_SHA, (
            "pre_fix_sha must be preserved in result.data on skip"
        )
        assert result.data["cycle"] == 2, (
            "cycle must be preserved in result.data on skip"
        )
        assert result.data["spec_path"] == "/tmp/spec.md", (
            "spec_path must be preserved in result.data on skip"
        )

    # ── AC4: non-empty commits ─────────────────────────────────────────────────

    def test_commit_fix_code_non_empty_commits(self, tmp_path: Path, monkeypatch) -> None:
        """Valid pre_fix_sha; worker_written_paths=['src/foo.py']; git ops succeed → ok, fix_commit event emitted with phase=6 n_files=1 cycle.

        AC4: non-empty commit path — also verifies git add + git commit called with correct args.
        4961254A: manifest drives path selection (not git_diff_files).
        """
        import phase_6_review

        post_sha = "b" * 40
        captured: list[dict] = []
        calls: list[tuple] = []

        monkeypatch.setattr(
            phase_6_review,
            "_emit_safe",
            lambda et, p, **kw: captured.append({"type": et, "payload": p}),
        )

        def _retry(cmd, cwd, timeout=30):
            calls.append((cmd, cwd, timeout))
            fake = MagicMock()
            fake.returncode = 0
            return (fake, "ok")

        monkeypatch.setattr(phase_6_review, "_git_op_with_lock_retry", _retry)
        monkeypatch.setattr(phase_6_review, "_paths_have_staged_changes", lambda *a, **k: True)  # 3F5599A6 §2.6: fixture stubs add without staging; force guard open

        # D5D6A364 repoint: rev-parse now routes through git_port.git_read, not bounded_run.
        # Install a stub factory returning GitResult(0, post_sha+"\n", "", False).
        _rev_stub = _SpyGitRead(GitResult(returncode=0, stdout=post_sha + "\n", stderr="", timed_out=False))
        try:
            set_default_git_read_factory(lambda: _rev_stub)

            ctx = _make_ctx(tmp_path)
            # 4961254A: manifest provides the path; file must exist on disk for git add
            import os as _os
            _full = _os.path.join(ctx.org_config["git_cwd"], "src", "foo.py")
            _os.makedirs(_os.path.dirname(_full), exist_ok=True)
            open(_full, "w").close()
            prev = _make_prev(pre_fix_sha=_VALID_SHA, cycle=3, worker_written_paths=["src/foo.py"],
                              manifest_source="harness_tool_record")  # 4C03CCED Ship 1C

            result = _commit_fix_code(ctx, prev)
        finally:
            reset_default_git_read_factory()

        assert result.status == "ok", f"Expected ok, got {result.status!r}: {getattr(result, 'error', '')}"
        assert result.data is not None
        fix_sha = result.data.get("fix_commit_sha")
        assert fix_sha is not None, "fix_commit_sha must be set on successful commit"
        assert isinstance(fix_sha, str) and len(fix_sha) == 40 and re.fullmatch(r"[0-9a-f]{40}", fix_sha), (
            f"Expected 40-char hex SHA, got {fix_sha!r}"
        )

        commit_events = [e for e in captured if e["type"] == "fix_commit"]
        assert len(commit_events) == 1, (
            f"Expected 1 fix_commit event, got {len(commit_events)}. Events: {[e['type'] for e in captured]}"
        )
        ev_payload = commit_events[0]["payload"]
        assert ev_payload.get("phase") == 6, f"Expected phase=6, got {ev_payload.get('phase')!r}"
        assert ev_payload.get("n_files") == 1, f"Expected n_files=1, got {ev_payload.get('n_files')!r}"
        assert ev_payload.get("cycle") == 3, f"Expected cycle=3, got {ev_payload.get('cycle')!r}"
        assert "paths" in ev_payload, "Event must include paths"

        # Revision 1: verify git add and git commit were actually called
        git_cmds = [c[0] for c in calls]  # list of cmd lists
        assert any(cmd[:2] == ["git", "add"] for cmd in git_cmds), \
            f"git add must be invoked; saw {git_cmds}"
        assert any(cmd[:2] == ["git", "commit"] for cmd in git_cmds), \
            f"git commit must be invoked; saw {git_cmds}"

        # Verify commit message shape ("build: fix cycle N\n\nFiles: ...")
        commit_cmd = next(cmd for cmd in git_cmds if cmd[:2] == ["git", "commit"])
        m_idx = commit_cmd.index("-m")
        commit_msg = commit_cmd[m_idx + 1]
        assert "fix cycle 3" in commit_msg, f"commit message must contain 'fix cycle 3'; got {commit_msg!r}"
        assert "Files:" in commit_msg
        assert "foo.py" in commit_msg  # basename only per _build_fix_commit_message

        # Verify event payload paths + sha equality
        assert ev_payload["paths"] == ["src/foo.py"], ev_payload
        assert ev_payload["commit_sha"] == fix_sha, "event commit_sha must equal returned fix_commit_sha"

    # ── Revision 3: fallback SHA path ─────────────────────────────────────────

    def test_commit_fix_code_fallback_used_when_pre_fix_sha_absent(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """prev.data has NO pre_fix_sha; resolve_pre_phase_sha returns valid SHA → fallback used, ok returned.

        Revision 3 (4961254A update): SHA fallback resolves correctly; then empty manifest → skip.
        (git_diff_files is no longer called — 4961254A manifest allowlist inversion.)
        """
        import phase_6_review

        fallback_sha = _VALID_SHA
        resolve_calls: list = []

        def _fake_resolve(worktree_root):
            resolve_calls.append(worktree_root)
            return fallback_sha

        monkeypatch.setattr(
            phase_6_review,
            "resolve_pre_phase_sha",
            _fake_resolve,
        )
        monkeypatch.setattr(
            phase_6_review,
            "_emit_safe",
            lambda et, p, **kw: None,
        )

        ctx = _make_ctx(tmp_path)
        prev = _make_prev(cycle=1)  # NO pre_fix_sha key at all
        prev.data = {"cycle": 1,
                     "worker_written_paths": [],           # 4C03CCED Ship 1C
                     "manifest_source": "harness_tool_record"}  # 4C03CCED Ship 1C

        result = _commit_fix_code(ctx, prev)

        assert result.status == "ok", f"Expected ok, got {result.status!r}: {getattr(result, 'error', '')}"
        assert resolve_calls, "resolve_pre_phase_sha must be called when pre_fix_sha is absent"

    # ── AC5: test paths filtered ───────────────────────────────────────────────

    def test_commit_fix_code_test_paths_filtered(self, tmp_path: Path, monkeypatch) -> None:
        """worker_written_paths contains production + test paths; only production paths passed to git add.

        AC5: _is_test_path filter excludes test files from manifest (4961254A manifest allowlist).
        """
        import phase_6_review

        # 4961254A: manifest drives selection; test paths excluded by _is_test_path
        manifest_paths = ["src/foo.py", "tests/test_foo.py", "src/bar.test.ts"]
        committed_paths: list[list[str]] = []

        monkeypatch.setattr(
            phase_6_review,
            "_emit_safe",
            lambda et, p, **kw: None,
        )

        def _capture_git_op(cmd, cwd, timeout=30):
            # Capture the paths passed to `git add`
            if cmd and len(cmd) > 1 and cmd[0] == "git" and cmd[1] == "add":
                paths_arg = cmd[2:]  # everything after "git add"
                committed_paths.append(paths_arg)
            fake = MagicMock()
            fake.returncode = 0
            return (fake, "ok")

        monkeypatch.setattr(phase_6_review, "_git_op_with_lock_retry", _capture_git_op)

        # D5D6A364 repoint: rev-parse now routes through git_port.git_read, not bounded_run.
        _rev_stub = _SpyGitRead(GitResult(returncode=0, stdout="c" * 40 + "\n", stderr="", timed_out=False))
        try:
            set_default_git_read_factory(lambda: _rev_stub)

            ctx = _make_ctx(tmp_path)
            import os as _os
            for _rel in ["src/foo.py"]:
                _full = _os.path.join(ctx.org_config["git_cwd"], _rel)
                _os.makedirs(_os.path.dirname(_full), exist_ok=True)
                open(_full, "w").close()
            prev = _make_prev(
                pre_fix_sha=_VALID_SHA, cycle=1,
                worker_written_paths=manifest_paths,
                manifest_source="harness_tool_record",  # 4C03CCED Ship 1C
            )

            result = _commit_fix_code(ctx, prev)
        finally:
            reset_default_git_read_factory()

        assert result.status == "ok", f"Expected ok, got {result.status!r}: {getattr(result, 'error', '')}"
        assert len(committed_paths) >= 1, "Expected at least one git add call"

        # Flatten all paths passed to git add across all calls
        all_committed = [p for paths in committed_paths for p in paths]

        assert "src/foo.py" in all_committed, (
            f"src/foo.py must be in committed paths; got {all_committed!r}"
        )
        assert not any("test_foo.py" in p for p in all_committed), (
            f"tests/test_foo.py must NOT be in committed paths; got {all_committed!r}"
        )
        assert not any("bar.test.ts" in p for p in all_committed), (
            f"src/bar.test.ts must NOT be in committed paths; got {all_committed!r}"
        )

    # ── AC6: prev.data preserved ───────────────────────────────────────────────

    def test_commit_fix_code_prev_data_preserved(self, tmp_path: Path, monkeypatch) -> None:
        """All original prev.data keys survive into result.data; fix_commit_sha added.

        AC6: {**prev.data, fix_commit_sha: ...} spread.
        """
        import phase_6_review

        post_sha = "d" * 40

        monkeypatch.setattr(
            phase_6_review,
            "_emit_safe",
            lambda et, p, **kw: None,
        )
        fake_proc = MagicMock()
        fake_proc.returncode = 0
        monkeypatch.setattr(
            phase_6_review,
            "_git_op_with_lock_retry",
            lambda cmd, cwd, timeout=30: (fake_proc, "ok"),
        )

        # D5D6A364 repoint: rev-parse now routes through git_port.git_read, not bounded_run.
        _rev_stub = _SpyGitRead(GitResult(returncode=0, stdout=post_sha + "\n", stderr="", timed_out=False))
        try:
            set_default_git_read_factory(lambda: _rev_stub)

            ctx = _make_ctx(tmp_path)
            # 4961254A: manifest provides the path
            import os as _os
            _full = _os.path.join(ctx.org_config["git_cwd"], "src", "impl.py")
            _os.makedirs(_os.path.dirname(_full), exist_ok=True)
            open(_full, "w").close()
            prev = _make_prev(
                pre_fix_sha=_VALID_SHA,
                cycle=3,
                spec_path="/tmp/spec.md",  # extra key that must survive
                worker_written_paths=["src/impl.py"],
                manifest_source="harness_tool_record",  # 4C03CCED Ship 1C
            )

            result = _commit_fix_code(ctx, prev)
        finally:
            reset_default_git_read_factory()

        assert result.status == "ok", f"Expected ok, got {result.status!r}"
        assert result.data is not None

        # Original keys preserved
        assert result.data.get("pre_fix_sha") == _VALID_SHA, (
            "pre_fix_sha must be preserved in result.data"
        )
        assert result.data.get("spec_path") == "/tmp/spec.md", (
            "spec_path must be preserved in result.data"
        )
        assert result.data.get("cycle") == 3, (
            "cycle must be preserved in result.data"
        )

        # New key added
        fix_sha = result.data.get("fix_commit_sha")
        assert fix_sha is not None, "fix_commit_sha must be added to result.data"
        assert isinstance(fix_sha, str) and len(fix_sha) == 40, (
            f"fix_commit_sha must be 40-char hex, got {fix_sha!r}"
        )

    # ── DD34EEBF: scratchpad integrity refs ───────────────────────────────────

    def test_commit_fix_code_writes_pre_fix_ref_on_success(self, tmp_path: Path, monkeypatch) -> None:
        """AC1 (DD34EEBF): on successful commit, scratchpad/integrity/pre-fix-ref.txt
        contains the resolved pre_fix_sha (40-char hex, no trailing newline)."""
        import phase_6_review

        post_sha = "e" * 40

        monkeypatch.setattr(
            phase_6_review,
            "_emit_safe",
            lambda et, p, **kw: None,
        )

        fake_proc = MagicMock()
        fake_proc.returncode = 0
        monkeypatch.setattr(
            phase_6_review,
            "_git_op_with_lock_retry",
            lambda cmd, cwd, timeout=30: (fake_proc, "ok"),
        )

        # D5D6A364 repoint: rev-parse now routes through git_port.git_read, not bounded_run.
        _rev_stub = _SpyGitRead(GitResult(returncode=0, stdout=post_sha + "\n", stderr="", timed_out=False))
        try:
            set_default_git_read_factory(lambda: _rev_stub)

            ctx = _make_ctx(tmp_path)
            # 4961254A: manifest drives commit path selection
            prev = _make_prev(pre_fix_sha=_VALID_SHA, cycle=2, worker_written_paths=["src/impl.py"],
                              manifest_source="harness_tool_record")  # 4C03CCED Ship 1C

            result = _commit_fix_code(ctx, prev)
        finally:
            reset_default_git_read_factory()

        assert result.status == "ok", f"Expected ok, got {result.status!r}"

        scratchpad = tmp_path / "scratch"
        ref_file = scratchpad / "integrity" / "pre-fix-ref.txt"
        assert ref_file.is_file(), (
            f"scratchpad/integrity/pre-fix-ref.txt must exist after successful commit; "
            f"scratchpad contents: {list(scratchpad.rglob('*'))}"
        )
        assert ref_file.read_text() == _VALID_SHA, (
            f"pre-fix-ref.txt must contain the pre_fix_sha {_VALID_SHA!r} "
            f"(no trailing newline); got {ref_file.read_text()!r}"
        )

    def test_commit_fix_code_writes_fix_commit_sha_on_success(self, tmp_path: Path, monkeypatch) -> None:
        """AC2 (DD34EEBF): on successful commit, scratchpad/integrity/fix-commit-sha.txt
        contains the post-commit HEAD SHA."""
        import phase_6_review

        post_sha = "f" * 40

        monkeypatch.setattr(
            phase_6_review,
            "_emit_safe",
            lambda et, p, **kw: None,
        )

        fake_proc = MagicMock()
        fake_proc.returncode = 0
        monkeypatch.setattr(
            phase_6_review,
            "_git_op_with_lock_retry",
            lambda cmd, cwd, timeout=30: (fake_proc, "ok"),
        )

        # D5D6A364 repoint: rev-parse now routes through git_port.git_read, not bounded_run.
        _rev_stub = _SpyGitRead(GitResult(returncode=0, stdout=post_sha + "\n", stderr="", timed_out=False))
        try:
            set_default_git_read_factory(lambda: _rev_stub)

            ctx = _make_ctx(tmp_path)
            # 4961254A: manifest drives commit path selection; materialize the file
            import os as _os
            _full = _os.path.join(ctx.org_config["git_cwd"], "src", "impl.py")
            _os.makedirs(_os.path.dirname(_full), exist_ok=True)
            open(_full, "w").close()
            prev = _make_prev(pre_fix_sha=_VALID_SHA, cycle=2, worker_written_paths=["src/impl.py"],
                              manifest_source="harness_tool_record")  # 4C03CCED Ship 1C

            result = _commit_fix_code(ctx, prev)
        finally:
            reset_default_git_read_factory()

        assert result.status == "ok", f"Expected ok, got {result.status!r}"

        scratchpad = tmp_path / "scratch"
        sha_file = scratchpad / "integrity" / "fix-commit-sha.txt"
        assert sha_file.is_file(), (
            f"scratchpad/integrity/fix-commit-sha.txt must exist after successful commit; "
            f"scratchpad contents: {list(scratchpad.rglob('*'))}"
        )
        returned_sha = result.data.get("fix_commit_sha")
        assert sha_file.read_text() == returned_sha, (
            f"fix-commit-sha.txt must contain the post-commit SHA {returned_sha!r} "
            f"(no trailing newline); got {sha_file.read_text()!r}"
        )

    def test_commit_fix_code_no_scratchpad_writes_on_skip(self, tmp_path: Path, monkeypatch) -> None:
        """AC3 (DD34EEBF, superseded by 7B6A9AD1): on empty-manifest skip path,
        pre-fix-ref.txt IS written (boundary needed by integrity gate),
        fix-commit-sha.txt is NOT (no commit happened).
        4961254A: skip triggered by empty worker_written_paths (not git_diff_files)."""
        import phase_6_review

        monkeypatch.setattr(
            phase_6_review,
            "_emit_safe",
            lambda et, p, **kw: None,
        )

        ctx = _make_ctx(tmp_path)
        # Empty manifest → skip path (no worker_written_paths key)
        prev = _make_prev(pre_fix_sha=_VALID_SHA, cycle=2, worker_written_paths=[], manifest_source="harness_tool_record")

        result = _commit_fix_code(ctx, prev)

        assert result.status == "ok", f"Expected ok, got {result.status!r}"
        assert result.data.get("fix_commit_sha") is None, (
            "fix_commit_sha must be None on skip"
        )

        scratchpad = tmp_path / "scratch"
        pre_fix_ref = scratchpad / "integrity" / "pre-fix-ref.txt"
        fix_sha_ref = scratchpad / "integrity" / "fix-commit-sha.txt"
        assert pre_fix_ref.exists() and pre_fix_ref.read_text().strip() == _VALID_SHA, (
            f"pre-fix-ref.txt must be written even on no_production_paths skip per 7B6A9AD1 "
            f"(integrity gate needs the boundary); found exists={pre_fix_ref.exists()}"
        )
        assert not fix_sha_ref.exists(), (
            f"fix-commit-sha.txt must NOT be written on skip path; found at {fix_sha_ref}"
        )

    def test_commit_fix_code_no_scratchpad_writes_on_git_diff_failed(self, tmp_path: Path, monkeypatch) -> None:
        """AC4 (DD34EEBF, superseded by 7B6A9AD1, updated by 4961254A):
        on empty-manifest skip path, pre-fix-ref.txt IS written (boundary before skip),
        fix-commit-sha.txt is NOT (no commit happened).

        4961254A: git_diff_files is no longer called — manifest drives selection.
        This test now mirrors AC3 to confirm integrity-file semantics on skip path
        independent of the skip trigger.
        """
        import phase_6_review

        monkeypatch.setattr(
            phase_6_review,
            "_emit_safe",
            lambda et, p, **kw: None,
        )

        ctx = _make_ctx(tmp_path)
        # Empty manifest (no worker_written_paths) → skip path fires
        prev = _make_prev(pre_fix_sha=_VALID_SHA, cycle=2, worker_written_paths=[], manifest_source="harness_tool_record")

        result = _commit_fix_code(ctx, prev)

        assert result.status == "ok", f"Expected ok on empty-manifest skip, got {result.status!r}"
        assert result.data.get("fix_commit_sha") is None, (
            "fix_commit_sha must be None when manifest is empty"
        )

        scratchpad = tmp_path / "scratch"
        pre_fix_ref = scratchpad / "integrity" / "pre-fix-ref.txt"
        fix_sha_ref = scratchpad / "integrity" / "fix-commit-sha.txt"
        assert pre_fix_ref.exists() and pre_fix_ref.read_text().strip() == _VALID_SHA, (
            f"pre-fix-ref.txt must be written even on empty_manifest skip per 7B6A9AD1 "
            f"(integrity gate needs the boundary); found exists={pre_fix_ref.exists()}"
        )
        assert not fix_sha_ref.exists(), (
            f"fix-commit-sha.txt must NOT be written on skip path (no commit happened); "
            f"found at {fix_sha_ref}."
        )
