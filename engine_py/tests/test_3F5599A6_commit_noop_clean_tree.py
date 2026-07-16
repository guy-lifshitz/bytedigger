"""RED tests for 3F5599A6 — phase_6 fix-commit noop guard + clean-tree detector.

Contract (frozen spec 2026-07-06, GH279 slice 1):
  - _paths_have_staged_changes moves from phase_5_implement.py to
    phase_workflows_common.py (§2.1 single-source); phase_5_implement keeps
    the name resolvable via import binding, but the local def is gone.
  - _commit_fix_code / _commit_fix_tests gain a noop guard: when the manifest
    paths have no staged diff, skip the git-commit block, emit
    fix_commit_noop {step, phase, cycle, n_manifest_paths, n_working_tree_changes},
    and return ok with fix_commit_sha/fix_test_commit_sha == current HEAD
    (idempotent re-entry semantics, NOT None).
  - New helpers in phase_6_review.py: _count_working_tree_changes and
    _assert_clean_tree (998C880F detector) — observability-only, never raise,
    never alter control flow.
  - Both commit fns call _assert_clean_tree(paths, git_cwd, step_name=...)
    after a successful commit, on the success path.
  - AC12 pins pre-existing deletion-commit behavior as closure evidence for
    128027B3 (structurally already fixed by 4961254A).
  - AC13 pins the returncode guard surviving the §2.1 move to its new home.

All tests MUST FAIL until GREEN implements the contract, EXCEPT AC6 and AC12
which are pins on already-correct behavior (expected to PASS today too).
D1CF5FDF: no top-level import of not-yet-existing GREEN symbols — all such
imports/attr checks are deferred to inside test function bodies so this file
COLLECTS even before GREEN ships.

Do NOT implement the contract here — RED-only file.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

HERE = Path(__file__).parent
ENGINE_ROOT = HERE.parent
sys.path.insert(0, str(ENGINE_ROOT))
sys.path.insert(0, str(ENGINE_ROOT / "lib"))
sys.path.insert(0, str(ENGINE_ROOT / "workflows"))


# ─── git repo helpers (mirrors test_4961254A_commit_manifest_inversion.py) ────


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
    """Init repo + placeholder commit. Returns (repo_path, base_sha).

    §1j: wrap tmp_path in os.path.realpath (macOS /var symlink breaks
    subprocess-cwd equality).
    """
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
        session_id="test-session-3F5599A6",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_prev(**data) -> MagicMock:
    prev = MagicMock()
    prev.data = dict(data)
    return prev


# ─── AC1 ────────────────────────────────────────────────────────────────────


class TestPathsHaveStagedChangesCentralized:
    """AC1: _paths_have_staged_changes importable from phase_workflows_common."""

    def test_ac1_paths_have_staged_changes_centralized(self, tmp_path) -> None:
        """AC1: deferred import from phase_workflows_common; staged->True, clean->False."""
        try:
            from phase_workflows_common import _paths_have_staged_changes  # type: ignore[import]
        except ImportError as exc:
            pytest.fail(
                f"_paths_have_staged_changes not yet centralized in "
                f"phase_workflows_common (§2.1 3F5599A6 not shipped): {exc}"
            )

        repo, base_sha = _make_repo_with_base_commit(tmp_path)
        _write_file(repo, "src/f.py", "# new file\n")
        subprocess.run(["git", "add", "--", "src/f.py"], cwd=repo, check=True)

        assert _paths_have_staged_changes(str(repo), ["src/f.py"]) is True, (
            "expected True for a path with staged changes"
        )

        subprocess.run(["git", "commit", "-q", "-m", "commit f"], cwd=repo, check=True)

        assert _paths_have_staged_changes(str(repo), ["src/f.py"]) is False, (
            "expected False once the staged change is committed (clean tree)"
        )


# ─── AC2 ────────────────────────────────────────────────────────────────────


class TestPathsHaveStagedChangesClosure:
    """AC2: §1g closure — local def removed from phase_5_implement, attr still resolves."""

    def test_ac2_phase5_local_def_removed_attr_still_resolves(self) -> None:
        """AC2: phase_5_implement.py source has NO local def; import binding still works."""
        src_path = ENGINE_ROOT / "workflows" / "phase_5_implement.py"
        src_text = src_path.read_text()
        assert "def _paths_have_staged_changes" not in src_text, (
            "phase_5_implement.py must not define _paths_have_staged_changes "
            "locally after §2.1 centralization (3F5599A6)"
        )

        import phase_5_implement  # type: ignore[import]

        assert hasattr(phase_5_implement, "_paths_have_staged_changes"), (
            "phase_5_implement must still expose _paths_have_staged_changes "
            "via the phase_workflows_common import binding"
        )


# ─── AC3 / AC4 ──────────────────────────────────────────────────────────────


class TestCommitFixCodeNoopGuard:
    """AC3/AC4: _commit_fix_code noop guard on a no-diff manifest."""

    def test_ac3_commit_fix_code_noop_returns_ok_with_head_and_event(
        self, tmp_path, monkeypatch
    ) -> None:
        """AC3: manifest=['src/f.py'] committed & unchanged -> ok, sha==pre-call HEAD, 1 noop event."""
        from phase_6_review import _commit_fix_code  # type: ignore[import]
        import phase_6_review

        repo, base_sha = _make_repo_with_base_commit(tmp_path)
        pre_sha = _commit_file(repo, "src/f.py", "# f\n", "add f")
        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()

        captured: list[dict] = []
        monkeypatch.setattr(
            phase_6_review,
            "_emit_safe",
            lambda et, p, **kw: captured.append({"type": et, "payload": p}),
        )

        ctx = _make_ctx(scratchpad, str(repo))
        prev = _make_prev(
            pre_fix_sha=pre_sha,
            cycle=3,
            worker_written_paths=["src/f.py"],
            manifest_source="harness_tool_record",
        )

        result = _commit_fix_code(ctx, prev)

        assert result.status == "ok", (
            f"noop commit (no diff on manifest paths) must return ok, not hard-fail. "
            f"Got {result.status!r}: {getattr(result, 'error', '')} "
            f"(error_code={getattr(result, 'error_code', None)!r})"
        )
        fix_sha = (result.data or {}).get("fix_commit_sha")
        assert fix_sha == pre_sha, (
            f"noop fix_commit_sha must equal pre-call HEAD (idempotent re-entry, "
            f"NOT None): got {fix_sha!r} vs pre-call HEAD {pre_sha!r}"
        )
        assert isinstance(fix_sha, str) and len(fix_sha) == 40, (
            f"fix_commit_sha must be a 40-hex SHA, got {fix_sha!r}"
        )

        noop_events = [e for e in captured if e["type"] == "fix_commit_noop"]
        assert len(noop_events) == 1, (
            f"Expected exactly 1 fix_commit_noop event. All events: "
            f"{[e['type'] for e in captured]}"
        )
        payload = noop_events[0]["payload"]
        assert payload.get("step") == "commit_fix_code"
        assert payload.get("phase") == 6
        assert payload.get("n_manifest_paths") == 1
        assert isinstance(payload.get("n_working_tree_changes"), int), (
            f"n_working_tree_changes must be an int, got {payload.get('n_working_tree_changes')!r}"
        )

    def test_ac4_commit_fix_code_noop_counts_ambient_working_tree_changes(
        self, tmp_path, monkeypatch
    ) -> None:
        """AC4: noop + 1 extra untracked off-manifest file -> n_working_tree_changes >= 1."""
        from phase_6_review import _commit_fix_code  # type: ignore[import]
        import phase_6_review

        repo, base_sha = _make_repo_with_base_commit(tmp_path)
        pre_sha = _commit_file(repo, "src/f.py", "# f\n", "add f")
        _write_file(repo, "src/extra_untracked.py", "# extra\n")
        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()

        captured: list[dict] = []
        monkeypatch.setattr(
            phase_6_review,
            "_emit_safe",
            lambda et, p, **kw: captured.append({"type": et, "payload": p}),
        )

        ctx = _make_ctx(scratchpad, str(repo))
        prev = _make_prev(
            pre_fix_sha=pre_sha,
            cycle=3,
            worker_written_paths=["src/f.py"],
            manifest_source="harness_tool_record",
        )

        result = _commit_fix_code(ctx, prev)

        noop_events = [e for e in captured if e["type"] == "fix_commit_noop"]
        assert len(noop_events) == 1, (
            f"Expected exactly 1 fix_commit_noop event. All events: "
            f"{[e['type'] for e in captured]} (result.status={result.status!r})"
        )
        n_wtc = noop_events[0]["payload"].get("n_working_tree_changes")
        assert isinstance(n_wtc, int) and n_wtc >= 1, (
            f"n_working_tree_changes must be >=1 with an untracked off-manifest "
            f"file present, got {n_wtc!r}"
        )


# ─── AC5 ────────────────────────────────────────────────────────────────────


class TestCommitFixTestsNoopGuard:
    """AC5: _commit_fix_tests noop guard mirrors _commit_fix_code."""

    def test_ac5_commit_fix_tests_noop_returns_ok_with_head_and_event(
        self, tmp_path, monkeypatch
    ) -> None:
        """AC5: manifest=['tests/test_f.py'] unchanged -> ok, sha==HEAD, step=='commit_fix_tests'."""
        from phase_6_review import _commit_fix_tests  # type: ignore[import]
        import phase_6_review

        repo, base_sha = _make_repo_with_base_commit(tmp_path)
        pre_sha = _commit_file(repo, "tests/test_f.py", "def test_f(): pass\n", "add test_f")
        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()

        captured: list[dict] = []
        monkeypatch.setattr(
            phase_6_review,
            "_emit_safe",
            lambda et, p, **kw: captured.append({"type": et, "payload": p}),
        )

        ctx = _make_ctx(scratchpad, str(repo))
        prev = _make_prev(
            pre_fix_sha=pre_sha,
            cycle=3,
            worker_written_paths=["tests/test_f.py"],
            manifest_source="harness_tool_record",
        )

        result = _commit_fix_tests(ctx, prev)

        assert result.status == "ok", (
            f"noop commit must return ok. Got {result.status!r}: {getattr(result, 'error', '')} "
            f"(error_code={getattr(result, 'error_code', None)!r})"
        )
        fix_test_sha = (result.data or {}).get("fix_test_commit_sha")
        assert fix_test_sha == pre_sha, (
            f"noop fix_test_commit_sha must equal pre-call HEAD, got {fix_test_sha!r} "
            f"vs {pre_sha!r}"
        )

        noop_events = [e for e in captured if e["type"] == "fix_commit_noop"]
        assert len(noop_events) == 1, (
            f"Expected exactly 1 fix_commit_noop event. All events: "
            f"{[e['type'] for e in captured]}"
        )
        assert noop_events[0]["payload"].get("step") == "commit_fix_tests"


# ─── AC6 (pin) ──────────────────────────────────────────────────────────────


class TestCommitFixCodeNonNoopPin:
    """AC6: non-noop pin — real diff still advances HEAD, no noop event."""

    def test_ac6_commit_fix_code_real_diff_advances_head_no_noop_event(
        self, tmp_path, monkeypatch
    ) -> None:
        """AC6: modified manifest prod path -> HEAD advances, fix_commit emitted, no noop, sidecar==HEAD."""
        from phase_6_review import _commit_fix_code  # type: ignore[import]
        import phase_6_review

        repo, base_sha = _make_repo_with_base_commit(tmp_path)
        pre_sha = _commit_file(repo, "src/f.py", "# f\n", "add f")
        _write_file(repo, "src/f.py", "# f changed\n")
        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()

        captured: list[dict] = []
        monkeypatch.setattr(
            phase_6_review,
            "_emit_safe",
            lambda et, p, **kw: captured.append({"type": et, "payload": p}),
        )

        ctx = _make_ctx(scratchpad, str(repo))
        prev = _make_prev(
            pre_fix_sha=pre_sha,
            cycle=3,
            worker_written_paths=["src/f.py"],
            manifest_source="harness_tool_record",
        )

        result = _commit_fix_code(ctx, prev)

        assert result.status == "ok", (
            f"Expected ok, got {result.status!r}: {getattr(result, 'error', '')}"
        )
        fix_sha = (result.data or {}).get("fix_commit_sha")
        assert fix_sha is not None and fix_sha != pre_sha, (
            f"HEAD must advance on a real diff; got fix_commit_sha={fix_sha!r} "
            f"vs pre-call HEAD {pre_sha!r}"
        )

        commit_events = [e for e in captured if e["type"] == "fix_commit"]
        assert len(commit_events) == 1, (
            f"Expected exactly 1 fix_commit event. All events: {[e['type'] for e in captured]}"
        )
        noop_events = [e for e in captured if e["type"] == "fix_commit_noop"]
        assert not noop_events, f"Must NOT emit fix_commit_noop on a real diff. Got: {noop_events!r}"

        sidecar = scratchpad / "integrity" / "fix-commit-sha.txt"
        assert sidecar.exists(), "sidecar integrity/fix-commit-sha.txt must be written"
        assert sidecar.read_text().strip() == fix_sha, (
            f"sidecar must contain the new HEAD {fix_sha!r}, got "
            f"{sidecar.read_text().strip()!r}"
        )


# ─── AC7 ────────────────────────────────────────────────────────────────────


class TestAssertCleanTreeClean:
    """AC7: _assert_clean_tree on a clean repo -> True, no event."""

    def test_ac7_assert_clean_tree_clean_repo_returns_true_no_event(
        self, tmp_path, monkeypatch
    ) -> None:
        """AC7: deferred presence gate; clean repo -> True, no fix_commit_dirty_residue."""
        import phase_6_review

        if not hasattr(phase_6_review, "_assert_clean_tree"):
            pytest.fail(
                "_assert_clean_tree not yet implemented in phase_6_review (§2.3 3F5599A6)"
            )

        repo, base_sha = _make_repo_with_base_commit(tmp_path)
        _commit_file(repo, "src/f.py", "# f\n", "add f")

        captured: list[dict] = []
        monkeypatch.setattr(
            phase_6_review,
            "_emit_safe",
            lambda et, p, **kw: captured.append({"type": et, "payload": p}),
        )

        result = phase_6_review._assert_clean_tree(["src/f.py"], str(repo), "x")

        assert result is True, f"clean tree must return True, got {result!r}"
        dirty_events = [e for e in captured if e["type"] == "fix_commit_dirty_residue"]
        assert not dirty_events, (
            f"clean tree must not emit fix_commit_dirty_residue: {dirty_events!r}"
        )


# ─── AC8 ────────────────────────────────────────────────────────────────────


class TestAssertCleanTreeDirty:
    """AC8: _assert_clean_tree with an uncommitted tracked-file change -> False + event."""

    def test_ac8_assert_clean_tree_dirty_residue_returns_false_with_event(
        self, tmp_path, monkeypatch
    ) -> None:
        """AC8: tracked src/f.py modified uncommitted -> False + 1 fix_commit_dirty_residue."""
        import phase_6_review

        if not hasattr(phase_6_review, "_assert_clean_tree"):
            pytest.fail(
                "_assert_clean_tree not yet implemented in phase_6_review (§2.3 3F5599A6)"
            )

        repo, base_sha = _make_repo_with_base_commit(tmp_path)
        _commit_file(repo, "src/f.py", "# f\n", "add f")
        _write_file(repo, "src/f.py", "# f dirtied uncommitted\n")

        captured: list[dict] = []
        monkeypatch.setattr(
            phase_6_review,
            "_emit_safe",
            lambda et, p, **kw: captured.append({"type": et, "payload": p}),
        )

        result = phase_6_review._assert_clean_tree(["src/f.py"], str(repo), "x")

        assert result is False, f"dirty residue must return False, got {result!r}"
        dirty_events = [e for e in captured if e["type"] == "fix_commit_dirty_residue"]
        assert len(dirty_events) == 1, (
            f"Expected exactly 1 fix_commit_dirty_residue event. All: "
            f"{[e['type'] for e in captured]}"
        )
        payload = dirty_events[0]["payload"]
        assert payload.get("step") == "x"
        assert payload.get("phase") == 6
        assert payload.get("n_dirty") == 1
        assert payload.get("dirty_paths"), "dirty_paths must be non-empty"


# ─── AC9 ────────────────────────────────────────────────────────────────────


class TestAssertCleanTreeNonexistentDir:
    """AC9: _assert_clean_tree with a nonexistent git_cwd -> True, no event, no exception."""

    def test_ac9_assert_clean_tree_nonexistent_git_cwd_degrades_to_true(
        self, tmp_path, monkeypatch
    ) -> None:
        """AC9: nonexistent dir -> degrades to True (fail-open observability), never raises."""
        import phase_6_review

        if not hasattr(phase_6_review, "_assert_clean_tree"):
            pytest.fail(
                "_assert_clean_tree not yet implemented in phase_6_review (§2.3 3F5599A6)"
            )

        nonexistent = os.path.join(str(tmp_path), "does-not-exist")

        captured: list[dict] = []
        monkeypatch.setattr(
            phase_6_review,
            "_emit_safe",
            lambda et, p, **kw: captured.append({"type": et, "payload": p}),
        )

        try:
            result = phase_6_review._assert_clean_tree(["src/f.py"], nonexistent, "x")
        except Exception as exc:  # noqa: BLE001
            raise AssertionError(
                f"_assert_clean_tree must never raise on a nonexistent git_cwd, got "
                f"{type(exc).__name__}: {exc}"
            ) from exc

        assert result is True, f"nonexistent git_cwd must degrade to True, got {result!r}"
        assert not captured, f"nonexistent git_cwd must not emit any event, got: {captured!r}"


# ─── AC10 ───────────────────────────────────────────────────────────────────


class TestAssertCleanTreeWiringFixCode:
    """AC10: _commit_fix_code wires _assert_clean_tree on the success path."""

    def test_ac10_commit_fix_code_calls_assert_clean_tree_with_prod_paths(
        self, tmp_path, monkeypatch
    ) -> None:
        """AC10: success path calls _assert_clean_tree(prod_paths, git_cwd, step_name='commit_fix_code')."""
        from phase_6_review import _commit_fix_code  # type: ignore[import]
        import phase_6_review

        repo, base_sha = _make_repo_with_base_commit(tmp_path)
        pre_sha = _commit_file(repo, "src/f.py", "# f\n", "add f")
        _write_file(repo, "src/f.py", "# f changed\n")
        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()

        monkeypatch.setattr(phase_6_review, "_emit_safe", lambda et, p, **kw: None)

        calls: list[dict] = []

        def _recorder(paths, git_cwd_arg, step_name=None, **kw):
            calls.append({"paths": paths, "git_cwd": git_cwd_arg, "step_name": step_name})
            return True

        # Collaborator patch (UUT here is _commit_fix_code) — allowed per spec §3.
        monkeypatch.setattr(phase_6_review, "_assert_clean_tree", _recorder, raising=False)

        ctx = _make_ctx(scratchpad, str(repo))
        prev = _make_prev(
            pre_fix_sha=pre_sha,
            cycle=3,
            worker_written_paths=["src/f.py"],
            manifest_source="harness_tool_record",
        )

        result = _commit_fix_code(ctx, prev)

        assert result.status == "ok", (
            f"Expected ok, got {result.status!r}: {getattr(result, 'error', '')}"
        )
        assert len(calls) == 1, (
            f"_commit_fix_code must call _assert_clean_tree exactly once on the "
            f"success path. Calls recorded: {calls!r}"
        )
        assert calls[0]["paths"] == ["src/f.py"], (
            f"expected prod_paths ['src/f.py'], got {calls[0]['paths']!r}"
        )
        assert calls[0]["git_cwd"] == str(repo), (
            f"expected git_cwd={str(repo)!r}, got {calls[0]['git_cwd']!r}"
        )
        assert calls[0]["step_name"] == "commit_fix_code", (
            f"expected step_name='commit_fix_code', got {calls[0]['step_name']!r}"
        )


# ─── AC11 ───────────────────────────────────────────────────────────────────


class TestAssertCleanTreeWiringFixTests:
    """AC11: _commit_fix_tests wires _assert_clean_tree on the success path."""

    def test_ac11_commit_fix_tests_calls_assert_clean_tree_with_test_paths(
        self, tmp_path, monkeypatch
    ) -> None:
        """AC11: success path calls _assert_clean_tree(test_paths, git_cwd, step_name='commit_fix_tests')."""
        from phase_6_review import _commit_fix_tests  # type: ignore[import]
        import phase_6_review

        repo, base_sha = _make_repo_with_base_commit(tmp_path)
        pre_sha = _commit_file(repo, "tests/test_f.py", "def test_f(): pass\n", "add test_f")
        _write_file(repo, "tests/test_f.py", "def test_f(): assert True\n")
        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()

        monkeypatch.setattr(phase_6_review, "_emit_safe", lambda et, p, **kw: None)

        calls: list[dict] = []

        def _recorder(paths, git_cwd_arg, step_name=None, **kw):
            calls.append({"paths": paths, "git_cwd": git_cwd_arg, "step_name": step_name})
            return True

        monkeypatch.setattr(phase_6_review, "_assert_clean_tree", _recorder, raising=False)

        ctx = _make_ctx(scratchpad, str(repo))
        prev = _make_prev(
            pre_fix_sha=pre_sha,
            cycle=3,
            worker_written_paths=["tests/test_f.py"],
            manifest_source="harness_tool_record",
        )

        result = _commit_fix_tests(ctx, prev)

        assert result.status == "ok", (
            f"Expected ok, got {result.status!r}: {getattr(result, 'error', '')}"
        )
        assert len(calls) == 1, (
            f"_commit_fix_tests must call _assert_clean_tree exactly once on the "
            f"success path. Calls recorded: {calls!r}"
        )
        assert calls[0]["paths"] == ["tests/test_f.py"], f"got {calls[0]['paths']!r}"
        assert calls[0]["git_cwd"] == str(repo)
        assert calls[0]["step_name"] == "commit_fix_tests", (
            f"expected step_name='commit_fix_tests', got {calls[0]['step_name']!r}"
        )


# ─── AC12 (pin — closure evidence) ─────────────────────────────────────────


class TestDeletionCommitClosureEvidence:
    """AC12: 128027B3 closure evidence — deletion-only manifest still commits cleanly."""

    def test_ac12_commit_fix_code_commits_deletion_of_gone_path(
        self, tmp_path, monkeypatch
    ) -> None:
        """AC12: base commit has src/gone.py; os.remove; manifest=['src/gone.py'] ->
        ok, HEAD advances, git show HEAD:src/gone.py fails (deletion committed)."""
        from phase_6_review import _commit_fix_code  # type: ignore[import]
        import phase_6_review

        repo, base_sha = _make_repo_with_base_commit(tmp_path)
        gone_sha = _commit_file(repo, "src/gone.py", "# gone\n", "add gone")
        os.remove(str(repo / "src" / "gone.py"))
        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()

        monkeypatch.setattr(phase_6_review, "_emit_safe", lambda et, p, **kw: None)

        ctx = _make_ctx(scratchpad, str(repo))
        prev = _make_prev(
            pre_fix_sha=gone_sha,
            cycle=4,
            worker_written_paths=["src/gone.py"],
            manifest_source="harness_tool_record",
        )

        result = _commit_fix_code(ctx, prev)

        assert result.status == "ok", (
            f"deletion commit must succeed (128027B3 closure evidence). "
            f"Got {result.status!r}: {getattr(result, 'error', '')}"
        )
        fix_sha = (result.data or {}).get("fix_commit_sha")
        assert fix_sha is not None and fix_sha != gone_sha, (
            f"HEAD must advance on a deletion commit; got {fix_sha!r} vs "
            f"pre-call HEAD {gone_sha!r}"
        )

        show = subprocess.run(
            ["git", "show", f"{fix_sha}:src/gone.py"],
            capture_output=True, text=True, cwd=repo,
        )
        assert show.returncode != 0, (
            "src/gone.py must NOT exist at the new HEAD (deletion committed); "
            f"git show succeeded unexpectedly: {show.stdout!r}"
        )


# ─── AC13 ───────────────────────────────────────────────────────────────────


class TestPathsHaveStagedChangesReturncodeGuardPin:
    """AC13: _paths_have_staged_changes at its new home retains the returncode guard."""

    def test_ac13_returncode_guard_pinned_at_new_home(self) -> None:
        """AC13: def _paths_have_staged_changes exists in phase_workflows_common.py
        and its body still references returncode (9EDB7588 guard preserved verbatim)."""
        import phase_workflows_common

        source = Path(phase_workflows_common.__file__).read_text(encoding="utf-8")

        pattern = re.compile(r"^def _paths_have_staged_changes\b", re.MULTILINE)
        m = pattern.search(source)
        assert m is not None, (
            "_paths_have_staged_changes def not found in phase_workflows_common.py "
            "(§2.1 3F5599A6 move not shipped)"
        )

        start = m.start()
        next_top = re.compile(r"\ndef |\nclass ", re.MULTILINE)
        nm = next_top.search(source, start + 1)
        end = nm.start() if nm else len(source)
        body = source[start:end]

        assert "returncode" in body, (
            "_paths_have_staged_changes body at new home must still contain the "
            "returncode guard (9EDB7588)"
        )
