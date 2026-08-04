"""RED tests for GH886 — fix-tail auto-commit + integrity self-heal.

Frozen spec: SHARED/memory/Decisions/2026-07-16_GH886_fix_tail_autocommit_spec.md

Change 1: new helper `_autocommit_fix_tail` in phase_6_review.py, called at the
end of `_commit_fix_tests`, auto-commits any dirty tail left after the fix
worker's own manifest commit (unless git_cwd resolved from ambient cwd — GH381
hazard) and refreshes the integrity/fix-commit-sha.txt sidecar.

Change 2: `_dirty_worktree_guard` in phase_6_fix_integrity.py gains a bounded
(1-attempt, sentinel-gated) self-heal call into the same helper before
terminally failing E_FIX_UNCOMMITTED_CHANGES.

Symbols under test (`_autocommit_fix_tail`, self-heal branch of
`_dirty_worktree_guard`) do NOT exist on `main` yet — deferred imports per
D1CF5FDF so this file COLLECTS cleanly and fails at assert time.

Do NOT implement the contract here — RED-only file.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# conftest.py already wires engine_py root / workflows / lib onto sys.path
# (§1q / 81F97F3D conftest-import-time singleton) — no module-level
# sys.path manipulation here.


# ─── git repo helpers (mirrors test_gh449_fixcycle_commit.py) ────────────────


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


def _write_file(repo: Path, relpath: str, body: str = "# dirty\n") -> None:
    p = repo / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)


def _head(repo: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=repo, check=True
    ).stdout.strip()


def _head_subject(repo: Path) -> str:
    return subprocess.run(
        ["git", "log", "-1", "--format=%s"], capture_output=True, text=True, cwd=repo, check=True
    ).stdout.strip()


def _porcelain(repo: Path) -> str:
    return subprocess.run(
        ["git", "status", "--porcelain"], capture_output=True, text=True, cwd=repo, check=True
    ).stdout


def _make_repo_with_base_commit(tmp_path: Path):
    import os

    repo = os.path.realpath(str(tmp_path / "repo"))
    repo_path = Path(repo)
    _init_repo(repo_path)
    base_sha = _commit_file(repo_path, "src/placeholder.py", "# placeholder\n", "init")
    return repo_path, base_sha


# ─── AC1-6, 11, 12: direct _autocommit_fix_tail helper tests ────────────────


def _get_autocommit_fix_tail():
    """Presence gate (D1CF5FDF): fails the calling test at assert time, not
    collection time, when the symbol does not exist yet."""
    from bytedigger_engine.workflows import phase_6_review

    fn = getattr(phase_6_review, "_autocommit_fix_tail", None)
    assert fn is not None, (
        "phase_6_review._autocommit_fix_tail does not exist yet (GH886 Change 1 "
        "not implemented)"
    )
    return fn, phase_6_review


class TestAC1TailAutocommitSubjectAndCleanTree:
    def test_ac1_tail_autocommit_subject_and_clean_tree(self, tmp_path, monkeypatch) -> None:
        fn, mod = _get_autocommit_fix_tail()
        repo, pre_sha = _make_repo_with_base_commit(tmp_path)
        _write_file(repo, "src/B.py", "def b(): return 2\n")

        monkeypatch.setattr(mod, "_emit_safe", lambda et, p, **kw: None)

        fn({}, str(repo), "cfg_git_cwd", 1, pre_sha, "commit_fix_tests")

        assert _head_subject(repo) == "build: fix cycle 1 (auto-commit tail)", (
            f"expected tail-commit subject, got {_head_subject(repo)!r}"
        )
        assert _porcelain(repo).strip() == "", (
            f"expected clean tree after tail auto-commit, got {_porcelain(repo)!r}"
        )


class TestAC2TailAutocommitEventCommitShaMatchesHead:
    def test_ac2_event_commit_sha_matches_new_head(self, tmp_path, monkeypatch) -> None:
        fn, mod = _get_autocommit_fix_tail()
        repo, pre_sha = _make_repo_with_base_commit(tmp_path)
        _write_file(repo, "src/B.py", "def b(): return 2\n")

        captured: list[dict] = []
        monkeypatch.setattr(
            mod, "_emit_safe",
            lambda et, p, **kw: captured.append({"type": et, "payload": p}),
        )

        fn({}, str(repo), "cfg_git_cwd", 1, pre_sha, "commit_fix_tests")

        new_head = _head(repo)
        events = [e for e in captured if e["type"] == "fix_tail_autocommit"]
        assert len(events) == 1, f"expected exactly 1 fix_tail_autocommit event, got {events!r}"
        assert events[0]["payload"].get("commit_sha") == new_head, (
            f"expected commit_sha == new HEAD {new_head!r}, got "
            f"{events[0]['payload'].get('commit_sha')!r}"
        )


class TestAC3SidecarUpdatedToNewHead:
    def test_ac3_sidecar_fix_commit_sha_updated_to_new_head(self, tmp_path, monkeypatch) -> None:
        fn, mod = _get_autocommit_fix_tail()
        repo, pre_sha = _make_repo_with_base_commit(tmp_path)
        _write_file(repo, "src/B.py", "def b(): return 2\n")
        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()

        monkeypatch.setattr(mod, "_emit_safe", lambda et, p, **kw: None)

        cfg = {"scratchpad_dir": str(scratchpad)}
        fn(cfg, str(repo), "cfg_git_cwd", 1, pre_sha, "commit_fix_tests")

        sidecar = scratchpad / "integrity" / "fix-commit-sha.txt"
        assert sidecar.is_file(), f"expected sidecar to exist at {sidecar}"
        assert sidecar.read_text().strip() == _head(repo), (
            f"expected sidecar to equal new HEAD {_head(repo)!r}, got "
            f"{sidecar.read_text().strip()!r}"
        )


class TestAC4CwdDefaultSourceNoop:
    def test_ac4_cwd_default_source_noop_skip_event(self, tmp_path, monkeypatch) -> None:
        fn, mod = _get_autocommit_fix_tail()
        repo, pre_sha = _make_repo_with_base_commit(tmp_path)
        _write_file(repo, "src/B.py", "def b(): return 2\n")

        captured: list[dict] = []
        monkeypatch.setattr(
            mod, "_emit_safe",
            lambda et, p, **kw: captured.append({"type": et, "payload": p}),
        )
        pre_head = _head(repo)

        fn({}, str(repo), "cwd", 1, pre_sha, "commit_fix_tests")

        assert _head(repo) == pre_head, "cwd-default source must never commit the ambient tree"
        assert _porcelain(repo).strip() != "", "tree must remain dirty on cwd-default no-op"
        skip_events = [e for e in captured if e["type"] == "fix_tail_skipped"]
        assert len(skip_events) == 1, f"expected 1 fix_tail_skipped event, got {captured!r}"
        assert skip_events[0]["payload"].get("reason") == "cwd_default"


class TestAC5TamperedTailBlocksAutocommit:
    def test_ac5_tampered_red_tail_returns_tampered_error_no_commit(
        self, tmp_path, monkeypatch
    ) -> None:
        fn, mod = _get_autocommit_fix_tail()
        repo, base_sha = _make_repo_with_base_commit(tmp_path)
        pre_sha = _commit_file(repo, "tests/test_foo.py", "def test_foo(): pass\n", "red")
        # Tail: uncommitted edit to the frozen RED test file.
        _write_file(repo, "tests/test_foo.py", "def test_foo(): assert True\n")

        monkeypatch.setattr(mod, "_emit_safe", lambda et, p, **kw: None)
        monkeypatch.setenv("HAL_AUTHORED_BOUNDARY_GATE", "1")

        fake_scan_result = MagicMock()
        fake_scan_result.tampered_tests = ["tests/test_foo.py"]
        fake_scan_result.suppression_hits = []
        monkeypatch.setattr(
            mod.authored_boundary, "scan_boundary", lambda *a, **kw: fake_scan_result
        )
        pre_head = _head(repo)

        result = fn({}, str(repo), "cfg_git_cwd", 1, pre_sha, "commit_fix_tests")

        assert _head(repo) == pre_head, "tampered tail must NOT be committed"
        error_code = getattr(result, "error_code", None) if not isinstance(result, dict) else result.get("error_code")
        assert error_code == "E_RED_TESTS_TAMPERED", (
            f"expected E_RED_TESTS_TAMPERED for a tampered RED tail, got {error_code!r}"
        )


class TestAC6CleanTreeNoop:
    def test_ac6_clean_tree_helper_is_pure_noop(self, tmp_path, monkeypatch) -> None:
        fn, mod = _get_autocommit_fix_tail()
        repo, pre_sha = _make_repo_with_base_commit(tmp_path)

        captured: list[dict] = []
        monkeypatch.setattr(
            mod, "_emit_safe",
            lambda et, p, **kw: captured.append({"type": et, "payload": p}),
        )
        pre_head = _head(repo)

        fn({}, str(repo), "cfg_git_cwd", 1, pre_sha, "commit_fix_tests")

        assert _head(repo) == pre_head, "clean tree must produce no commit"
        tail_events = [
            e for e in captured
            if e["type"] in ("fix_tail_autocommit", "fix_tail_skipped")
        ]
        assert not tail_events, f"clean tree must emit no tail events, got {tail_events!r}"


class TestAC11IdempotentReentryOnAlreadyCleanTree:
    def test_ac11_second_call_on_clean_tree_creates_no_second_commit(
        self, tmp_path, monkeypatch
    ) -> None:
        fn, mod = _get_autocommit_fix_tail()
        repo, pre_sha = _make_repo_with_base_commit(tmp_path)
        _write_file(repo, "src/B.py", "def b(): return 2\n")
        monkeypatch.setattr(mod, "_emit_safe", lambda et, p, **kw: None)

        fn({}, str(repo), "cfg_git_cwd", 1, pre_sha, "commit_fix_tests")
        head_after_first = _head(repo)

        count_before = subprocess.run(
            ["git", "rev-list", "--count", "HEAD"],
            capture_output=True, text=True, cwd=repo, check=True,
        ).stdout.strip()

        fn({}, str(repo), "cfg_git_cwd", 1, pre_sha, "commit_fix_tests")

        count_after = subprocess.run(
            ["git", "rev-list", "--count", "HEAD"],
            capture_output=True, text=True, cwd=repo, check=True,
        ).stdout.strip()

        assert _head(repo) == head_after_first, "re-entry on already-clean tree must not commit again"
        assert int(count_after) - int(count_before) == 0, (
            f"expected commit-count delta 0 on idempotent re-entry, got "
            f"{count_before!r} -> {count_after!r}"
        )


class TestAC12GitLockPersistedMapsToELockCode:
    def test_ac12_lock_persisted_maps_to_e_git_locked(self, tmp_path, monkeypatch) -> None:
        fn, mod = _get_autocommit_fix_tail()
        repo, pre_sha = _make_repo_with_base_commit(tmp_path)
        _write_file(repo, "src/B.py", "def b(): return 2\n")
        monkeypatch.setattr(mod, "_emit_safe", lambda et, p, **kw: None)

        fake_completed = MagicMock()
        fake_completed.stderr = "index.lock contention persisted"
        monkeypatch.setattr(
            mod, "_git_op_with_lock_retry",
            lambda *a, **kw: (fake_completed, "lock_persisted"),
        )
        pre_head = _head(repo)

        result = fn({}, str(repo), "cfg_git_cwd", 1, pre_sha, "commit_fix_tests")

        assert _head(repo) == pre_head, "lock-persisted git op must not leave a commit"
        error_code = getattr(result, "error_code", None) if not isinstance(result, dict) else result.get("error_code")
        assert error_code == "E_GIT_LOCKED", f"expected E_GIT_LOCKED, got {error_code!r}"


# ─── AC7-10: integrity-guard self-heal via _build_fix_integrity_prompt ──────


def _make_fi_ctx(git_cwd: str, pre_fix_sha: str, fix_commit_sha: str, scratchpad: Path, **extra):
    from bytedigger_engine.contracts import WorkflowContext

    org = {
        "git_cwd": git_cwd,
        "pre_fix_sha": pre_fix_sha,
        "fix_commit_sha": fix_commit_sha,
        "scratchpad_dir": str(scratchpad),
        **extra,
    }
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question="Fix the thing",
        session_id="test-session-gh886-fi",
        persona="hal",
        framework=None,
        domain=None,
    )


def _sentinel_path(scratchpad: Path) -> Path:
    return scratchpad / "integrity" / "tail-autocommit-attempted.txt"


class TestAC7SelfHealCommitsAndGuardReturnsNone:
    def test_ac7_selfheal_commits_and_step_continues_with_sentinel_created(
        self, tmp_path, monkeypatch
    ) -> None:
        from bytedigger_engine.workflows.phase_6_fix_integrity import _build_fix_integrity_prompt  # type: ignore[import]

        repo, sha = _make_repo_with_base_commit(tmp_path)
        _write_file(repo, "src/dirty.py", "# uncommitted fix edit\n")
        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()

        pre_head = _head(repo)
        ctx = _make_fi_ctx(str(repo), sha, sha, scratchpad)
        result = _build_fix_integrity_prompt(ctx, None)

        assert result.status != "error" or result.error_code != "E_FIX_UNCOMMITTED_CHANGES", (
            f"expected self-heal to clear the dirty tail on first entry (no sentinel "
            f"pre-staged), got status={result.status!r} error_code="
            f"{getattr(result, 'error_code', None)!r}"
        )
        # AC7 strengthened (gate MAJOR finding): triple assert — HEAD advanced,
        # tree clean, sentinel present — not just "guard returns non-terminal".
        assert _head(repo) != pre_head, (
            "expected self-heal to actually advance HEAD via a real auto-commit"
        )
        assert _porcelain(repo).strip() == "", (
            f"expected clean tree after self-heal, got {_porcelain(repo)!r}"
        )
        assert _sentinel_path(scratchpad).is_file(), (
            "expected self-heal sentinel to be created after the one allowed attempt"
        )


class TestAC8SentinelAlreadyPresentTerminalFail:
    def test_ac8_sentinel_present_skips_selfheal_terminal_fail(
        self, tmp_path, monkeypatch
    ) -> None:
        from bytedigger_engine.workflows.phase_6_fix_integrity import _build_fix_integrity_prompt  # type: ignore[import]

        repo, sha = _make_repo_with_base_commit(tmp_path)
        _write_file(repo, "src/dirty.py", "# uncommitted fix edit\n")
        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()
        sentinel = _sentinel_path(scratchpad)
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.write_text("attempted")

        pre_head = _head(repo)
        ctx = _make_fi_ctx(str(repo), sha, sha, scratchpad)
        result = _build_fix_integrity_prompt(ctx, None)

        assert result.status == "error", (
            f"expected terminal error when sentinel already present, got {result.status!r}"
        )
        assert result.error_code == "E_FIX_UNCOMMITTED_CHANGES", (
            f"expected E_FIX_UNCOMMITTED_CHANGES, got {result.error_code!r}"
        )
        assert result.recoverable is False
        assert _head(repo) == pre_head, "must NOT attempt self-heal when sentinel already exists"


class TestAC9DirtyUnknownSkipsSelfHeal:
    def test_ac9_git_status_rc_nonzero_no_selfheal_attempt(
        self, tmp_path, monkeypatch
    ) -> None:
        from bytedigger_engine.workflows.phase_6_fix_integrity import _build_fix_integrity_prompt  # type: ignore[import]
        from bytedigger_engine.workflows import phase_6_fix_integrity

        repo, sha = _make_repo_with_base_commit(tmp_path)
        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()

        real_git_read = phase_6_fix_integrity.git_port.git_read

        def _flaky_git_read(args, **kwargs):
            if list(args)[:2] == ["status", "--porcelain"]:
                raise FileNotFoundError("git binary not found (simulated)")
            return real_git_read(args, **kwargs)

        monkeypatch.setattr(phase_6_fix_integrity.git_port, "git_read", _flaky_git_read)

        ctx = _make_fi_ctx(str(repo), sha, sha, scratchpad)
        result = _build_fix_integrity_prompt(ctx, None)

        assert result.status == "error"
        assert result.error_code == "E_FIX_UNCOMMITTED_CHANGES"
        assert not _sentinel_path(scratchpad).is_file(), (
            "dirty-unknown (git-status failure) must NOT attempt/record a self-heal try"
        )


class TestAC10SelfHealUnsuccessfulCwdDefaultTerminalFail:
    def test_ac10_selfheal_noop_cwd_default_terminal_fail(
        self, tmp_path, monkeypatch
    ) -> None:
        from bytedigger_engine.workflows.phase_6_fix_integrity import _build_fix_integrity_prompt  # type: ignore[import]

        repo, sha = _make_repo_with_base_commit(tmp_path)
        _write_file(repo, "src/dirty.py", "# uncommitted fix edit\n")
        monkeypatch.chdir(repo)
        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()

        # No git_cwd in cfg -> resolver must default to ambient cwd (source
        # "cwd"), so the self-heal helper no-ops (GH381 hazard, AC4) and the
        # tree stays dirty -> terminal fail, same as before self-heal existed.
        from bytedigger_engine.contracts import WorkflowContext

        ctx = WorkflowContext(
            tenant_id="hal",
            scope=None,
            db_path=None,
            org_config={
                "pre_fix_sha": sha,
                "fix_commit_sha": sha,
                "scratchpad_dir": str(scratchpad),
            },
            question="Fix the thing",
            session_id="test-session-gh886-ac10",
            persona="hal",
            framework=None,
            domain=None,
        )

        pre_head = _head(repo)
        result = _build_fix_integrity_prompt(ctx, None)

        assert result.status == "error"
        assert result.error_code == "E_FIX_UNCOMMITTED_CHANGES"
        assert result.recoverable is False
        assert _head(repo) == pre_head, "cwd-default self-heal no-op must never commit"


# ─── AC13-15: cycle-2 gate findings (E2E + boundary-suppression + gitignore) ─


def _make_step_ctx(scratchpad: Path, git_cwd: str, **org_extra):
    from bytedigger_engine.contracts import WorkflowContext

    org = {"scratchpad_dir": str(scratchpad), "git_cwd": git_cwd, **org_extra}
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question="Fix the thing",
        session_id="test-session-gh886-e2e",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_step_prev(**data) -> MagicMock:
    prev = MagicMock()
    prev.data = dict(data)
    return prev


class TestAC13E2ECommitFixTestsAutocommitsTail:
    """AC13 (gate BLOCKER): direct call to the real step function
    _commit_fix_tests must, on a manifest-committed test path plus a dirty
    non-manifest tail file, land the tail-autocommit itself (not just the
    helper in isolation) and advance HEAD past the step's own commit."""

    def test_ac13_commit_fix_tests_e2e_autocommits_non_manifest_tail(
        self, tmp_path, monkeypatch
    ) -> None:
        from bytedigger_engine.workflows.phase_6_review import _commit_fix_tests  # type: ignore[import]
        from bytedigger_engine.workflows import phase_6_review

        repo, pre_sha = _make_repo_with_base_commit(tmp_path)
        # Manifest path: worker wrote a test file, self-reported.
        _write_file(repo, "tests/test_gh886_worker.py", "def test_x(): pass\n")
        # Tail: a NON-manifest file also left dirty by the worker.
        _write_file(repo, "src/gh886_tail.py", "def tail(): return 1\n")

        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()

        captured: list[dict] = []
        monkeypatch.setattr(
            phase_6_review, "_emit_safe",
            lambda et, p, **kw: captured.append({"type": et, "payload": p}),
        )

        ctx = _make_step_ctx(scratchpad, str(repo))
        prev = _make_step_prev(
            pre_fix_sha=pre_sha,
            cycle=1,
            worker_written_paths=["tests/test_gh886_worker.py"],
            manifest_source="harness_tool_record",
            git_diff_files=["tests/test_gh886_worker.py"],
        )

        result = _commit_fix_tests(ctx, prev)

        assert result.status == "ok", (
            f"expected ok, got {result.status!r}: {getattr(result, 'error', '')} "
            f"(error_code={getattr(result, 'error_code', None)!r})"
        )
        assert _head(repo) != pre_sha, "expected HEAD to advance past the step's own commit"
        assert _head_subject(repo) == "build: fix cycle 1 (auto-commit tail)", (
            f"expected the tail-commit subject on the final HEAD, got "
            f"{_head_subject(repo)!r} (events: {[e['type'] for e in captured]!r})"
        )
        assert _porcelain(repo).strip() == "", (
            f"expected clean tree after step + tail auto-commit, got {_porcelain(repo)!r}"
        )


class TestAC14BoundarySuppressionBlocksTail:
    """AC14 (gate MAJOR): scan_boundary returning suppression_hits on the tail
    must map to E_BOUNDARY_SUPPRESSION and leave HEAD unchanged."""

    def test_ac14_suppression_hits_on_tail_maps_to_e_boundary_suppression(
        self, tmp_path, monkeypatch
    ) -> None:
        fn, mod = _get_autocommit_fix_tail()
        repo, pre_sha = _make_repo_with_base_commit(tmp_path)
        _write_file(repo, "src/B.py", "def b(): return 2\n")

        monkeypatch.setattr(mod, "_emit_safe", lambda et, p, **kw: None)
        monkeypatch.setenv("HAL_AUTHORED_BOUNDARY_GATE", "1")

        fake_scan_result = MagicMock()
        fake_scan_result.tampered_tests = []
        fake_scan_result.suppression_hits = ["src/B.py"]
        monkeypatch.setattr(
            mod.authored_boundary, "scan_boundary", lambda *a, **kw: fake_scan_result
        )
        pre_head = _head(repo)

        result = fn({}, str(repo), "cfg_git_cwd", 1, pre_sha, "commit_fix_tests")

        assert _head(repo) == pre_head, "suppression-flagged tail must NOT be committed"
        error_code = (
            getattr(result, "error_code", None)
            if not isinstance(result, dict) else result.get("error_code")
        )
        assert error_code == "E_BOUNDARY_SUPPRESSION", (
            f"expected E_BOUNDARY_SUPPRESSION for a suppression-flagged tail, got {error_code!r}"
        )


# AC15 (gitignored-only tail no-op) removed per gate cycle-2 finding N1: its
# precondition is unreachable. Default `git status --porcelain` never surfaces
# untracked-ignored files, and the index-consulted `git check-ignore` path
# never flags an already-tracked file as gitignored — so the
# gitignored_only branch this AC targeted was removed from the spec as dead
# code. No replacement test needed.
