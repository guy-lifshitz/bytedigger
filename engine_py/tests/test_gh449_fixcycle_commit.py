"""RED tests for GH449 — fix-cycle uncommitted edits must never classify NO_CHANGES.

Frozen spec: SHARED/memory/Decisions/2026-07-09_GH449_fixcycle_commit_spec.md

Contract:
  Change 1 (phase_6_review._commit_fix_code): empty manifest + non-empty dirty-tree
  enumeration since pre_fix_sha -> commit those paths as prod_paths (dirty-tree
  fallback) instead of skipping; emit fix_commit_manifest_fallback telemetry.
  Only when BOTH manifest and dirty-tree enumeration are empty does the existing
  fix_commit_skipped/empty_manifest skip fire.

  Change 2 (phase_6_fix_integrity._build_fix_integrity_prompt): at both NO_CHANGES
  short-circuits (equal SHAs; empty committed diff), check working tree via
  git_port.git_read(["status", "--porcelain"], cwd=git_cwd) before returning
  verdict_override=NO_CHANGES. Non-empty / unverifiable (git failure) -> error
  E_FIX_UNCOMMITTED_CHANGES, recoverable=False. Clean tree -> NO_CHANGES unchanged.

AC1-AC7 per spec. Do NOT implement the contract here — RED-only file.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

HERE = Path(__file__).parent
ENGINE_ROOT = HERE.parent
sys.path.insert(0, str(ENGINE_ROOT))


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


def _make_repo_with_base_commit(tmp_path: Path):
    """Init repo + placeholder commit (real filesystem git repo). Returns (repo_path, base_sha)."""
    import os

    repo = os.path.realpath(str(tmp_path / "repo"))
    repo_path = Path(repo)
    _init_repo(repo_path)
    base_sha = _commit_file(repo_path, "src/placeholder.py", "# placeholder\n", "init")
    return repo_path, base_sha


def _make_ctx(scratchpad: Path, git_cwd: str, **org_extra):
    from bytedigger_engine.contracts import WorkflowContext

    org = {"scratchpad_dir": str(scratchpad), "git_cwd": git_cwd, **org_extra}
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question="Fix the thing",
        session_id="test-session-gh449",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_prev(**data) -> MagicMock:
    prev = MagicMock()
    prev.data = dict(data)
    return prev


# ─── AC1 ────────────────────────────────────────────────────────────────────


class TestCommitFixCodeDirtyTreeFallback:
    """AC1: empty manifest + dirty non-test tree since pre_fix_sha -> commits those
    paths and emits fix_commit_manifest_fallback (instead of silently skipping)."""

    def test_ac1_empty_manifest_dirty_tree_fallback_commits_and_emits_event(
        self, tmp_path, monkeypatch
    ) -> None:
        from bytedigger_engine.workflows.phase_6_review import _commit_fix_code  # type: ignore[import]
        from bytedigger_engine.workflows import phase_6_review

        repo, pre_sha = _make_repo_with_base_commit(tmp_path)
        # Dirty non-test prod file created after pre_fix_sha, never staged/committed.
        _write_file(repo, "src/uncommitted_fix.py", "def fixed(): return 2\n")

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
            cycle=1,
            worker_written_paths=[],  # empty manifest — worker never self-reported
            manifest_source="harness_tool_record",
        )

        result = _commit_fix_code(ctx, prev)

        assert result.status == "ok", (
            f"Expected ok, got {result.status!r}: {getattr(result, 'error', '')} "
            f"(error_code={getattr(result, 'error_code', None)!r})"
        )
        fix_sha = (result.data or {}).get("fix_commit_sha")
        assert fix_sha is not None and fix_sha != pre_sha, (
            f"empty-manifest + dirty tree must commit the dirty file and advance "
            f"HEAD (dirty-tree fallback per GH449 spec), not skip. Got "
            f"fix_commit_sha={fix_sha!r} vs pre-call HEAD {pre_sha!r}"
        )

        show = subprocess.run(
            ["git", "show", "--name-only", "--format=", fix_sha],
            capture_output=True, text=True, cwd=repo, check=True,
        )
        committed_files = [line.strip() for line in show.stdout.splitlines() if line.strip()]
        assert "src/uncommitted_fix.py" in committed_files, (
            f"dirty-tree fallback must commit src/uncommitted_fix.py; got {committed_files!r}"
        )

        fallback_events = [e for e in captured if e["type"] == "fix_commit_manifest_fallback"]
        assert len(fallback_events) == 1, (
            f"Expected exactly 1 fix_commit_manifest_fallback event. All events: "
            f"{[e['type'] for e in captured]}"
        )
        payload = fallback_events[0]["payload"]
        assert payload.get("n_fallback_paths") == 1, (
            f"n_fallback_paths must be 1, got {payload.get('n_fallback_paths')!r}"
        )
        assert payload.get("phase") == 6

        skip_events = [e for e in captured if e["type"] == "fix_commit_skipped"]
        assert not skip_events, (
            f"Must NOT emit fix_commit_skipped when the dirty-tree fallback path "
            f"commits real changes. Got: {skip_events!r}"
        )


# ─── AC2 (pin) ──────────────────────────────────────────────────────────────


class TestCommitFixCodeEmptyManifestCleanTreeSkip:
    """AC2 (regression pin): empty manifest + clean tree -> keeps today's skip
    behavior (ok, fix_commit_sha None, fix_commit_skipped/empty_manifest)."""

    def test_ac2_empty_manifest_clean_tree_still_skips(self, tmp_path, monkeypatch) -> None:
        from bytedigger_engine.workflows.phase_6_review import _commit_fix_code  # type: ignore[import]
        from bytedigger_engine.workflows import phase_6_review

        repo, pre_sha = _make_repo_with_base_commit(tmp_path)
        # No dirty tree at all — repo is clean at pre_sha.
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
            cycle=1,
            worker_written_paths=[],
            manifest_source="harness_tool_record",
        )

        result = _commit_fix_code(ctx, prev)

        assert result.status == "ok", f"Expected ok, got {result.status!r}"
        assert (result.data or {}).get("fix_commit_sha") is None, (
            "fix_commit_sha must remain None when both manifest and dirty-tree "
            "enumeration are empty (nothing to commit)"
        )
        skip_events = [e for e in captured if e["type"] == "fix_commit_skipped"]
        assert len(skip_events) == 1, (
            f"Expected 1 fix_commit_skipped event. All events: {[e['type'] for e in captured]}"
        )
        assert skip_events[0]["payload"].get("reason") == "empty_manifest"


# ─── shared fix_integrity helpers ───────────────────────────────────────────


def _make_fi_ctx(git_cwd: str, pre_fix_sha: str, fix_commit_sha: str, scratchpad: Path):
    from bytedigger_engine.contracts import WorkflowContext

    org = {
        "git_cwd": git_cwd,
        "pre_fix_sha": pre_fix_sha,
        "fix_commit_sha": fix_commit_sha,
        "scratchpad_dir": str(scratchpad),
    }
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question="Fix the thing",
        session_id="test-session-gh449-fi",
        persona="hal",
        framework=None,
        domain=None,
    )


# ─── AC3 ────────────────────────────────────────────────────────────────────


class TestFixIntegrityEqualShasDirtyTree:
    """AC3: equal pre_fix_sha/fix_commit_sha + dirty worktree -> error
    E_FIX_UNCOMMITTED_CHANGES (NOT verdict_override=NO_CHANGES)."""

    def test_ac3_equal_shas_dirty_worktree_returns_error(self, tmp_path) -> None:
        from bytedigger_engine.workflows.phase_6_fix_integrity import _build_fix_integrity_prompt  # type: ignore[import]

        repo, sha = _make_repo_with_base_commit(tmp_path)
        # Dirty the tree AFTER the commit — uncommitted fix edits present.
        _write_file(repo, "src/dirty.py", "# uncommitted fix edit\n")
        scratchpad = tmp_path / "scratch"

        # GH886: pre-stage the self-heal sentinel so the bounded self-heal budget
        # is already exhausted — this test still exercises the original terminal
        # E_FIX_UNCOMMITTED_CHANGES contract (dirty never yields silent NO_CHANGES).
        sentinel = scratchpad / "integrity" / "tail-autocommit-attempted.txt"
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.write_text("attempted\n")

        ctx = _make_fi_ctx(str(repo), sha, sha, scratchpad)

        result = _build_fix_integrity_prompt(ctx, None)

        assert result.status == "error", (
            f"Expected error (dirty tree must never yield NO_CHANGES), got "
            f"{result.status!r}; data={result.data!r}"
        )
        assert result.error_code == "E_FIX_UNCOMMITTED_CHANGES", (
            f"Expected E_FIX_UNCOMMITTED_CHANGES, got {result.error_code!r}"
        )
        assert result.recoverable is False, (
            f"Expected recoverable=False, got {result.recoverable!r}"
        )


# ─── AC4 ────────────────────────────────────────────────────────────────────


class TestFixIntegrityEmptyCommittedDiffDirtyTree:
    """AC4: pre != post real commits, committed diff over test patterns empty
    (second commit only touches a plain prod file), but dirty worktree present
    -> error E_FIX_UNCOMMITTED_CHANGES."""

    def test_ac4_empty_committed_diff_dirty_worktree_returns_error(self, tmp_path) -> None:
        from bytedigger_engine.workflows.phase_6_fix_integrity import _build_fix_integrity_prompt  # type: ignore[import]

        repo, base_sha = _make_repo_with_base_commit(tmp_path)
        pre_sha = _commit_file(repo, "tests/test_foo.py", "def test_foo(): pass\n", "red")
        # fix_commit only touches a plain prod file -- default diff patterns
        # ('*test*', '*spec*', '*.test.*') filter it out -> empty committed diff.
        fix_sha = _commit_file(repo, "app.py", "def app(): return 1\n", "fix: prod only")
        # Uncommitted fix edit left in the tree (never committed).
        _write_file(repo, "app.py", "def app(): return 2\n")

        scratchpad = tmp_path / "scratch"

        # GH886: pre-stage the self-heal sentinel so the bounded self-heal budget
        # is already exhausted — this test still exercises the original terminal
        # E_FIX_UNCOMMITTED_CHANGES contract (dirty never yields silent NO_CHANGES).
        sentinel = scratchpad / "integrity" / "tail-autocommit-attempted.txt"
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        sentinel.write_text("attempted\n")

        ctx = _make_fi_ctx(str(repo), pre_sha, fix_sha, scratchpad)

        result = _build_fix_integrity_prompt(ctx, None)

        assert result.status == "error", (
            f"Expected error (dirty tree must never yield NO_CHANGES), got "
            f"{result.status!r}; data={result.data!r}"
        )
        assert result.error_code == "E_FIX_UNCOMMITTED_CHANGES", (
            f"Expected E_FIX_UNCOMMITTED_CHANGES, got {result.error_code!r}"
        )
        assert result.recoverable is False


# ─── AC5 ────────────────────────────────────────────────────────────────────


class TestFixIntegrityEqualShasCleanTree:
    """AC5 (regression pin): equal SHAs + clean worktree -> NO_CHANGES override
    unchanged (ok)."""

    def test_ac5_equal_shas_clean_tree_still_no_changes(self, tmp_path) -> None:
        from bytedigger_engine.workflows.phase_6_fix_integrity import _build_fix_integrity_prompt  # type: ignore[import]

        repo, sha = _make_repo_with_base_commit(tmp_path)
        scratchpad = tmp_path / "scratch"

        ctx = _make_fi_ctx(str(repo), sha, sha, scratchpad)

        result = _build_fix_integrity_prompt(ctx, None)

        assert result.status == "ok", (
            f"Expected ok on a clean tree, got {result.status!r}: {getattr(result, 'error', '')}"
        )
        assert result.data.get("verdict_override") == "NO_CHANGES", (
            f"Expected verdict_override='NO_CHANGES', got {result.data.get('verdict_override')!r}"
        )


# ─── AC6 ────────────────────────────────────────────────────────────────────


class TestFixIntegrityEmptyCommittedDiffCleanTree:
    """AC6 (regression pin): empty committed diff + clean worktree -> NO_CHANGES
    override unchanged."""

    def test_ac6_empty_committed_diff_clean_tree_still_no_changes(self, tmp_path) -> None:
        from bytedigger_engine.workflows.phase_6_fix_integrity import _build_fix_integrity_prompt  # type: ignore[import]

        repo, base_sha = _make_repo_with_base_commit(tmp_path)
        pre_sha = _commit_file(repo, "tests/test_foo.py", "def test_foo(): pass\n", "red")
        fix_sha = _commit_file(repo, "app.py", "def app(): return 1\n", "fix: prod only")
        # No dirty tree left behind this time.

        scratchpad = tmp_path / "scratch"
        ctx = _make_fi_ctx(str(repo), pre_sha, fix_sha, scratchpad)

        result = _build_fix_integrity_prompt(ctx, None)

        assert result.status == "ok", (
            f"Expected ok on a clean tree, got {result.status!r}: {getattr(result, 'error', '')}"
        )
        assert result.data.get("verdict_override") == "NO_CHANGES", (
            f"Expected verdict_override='NO_CHANGES', got {result.data.get('verdict_override')!r}"
        )


# ─── AC7 ────────────────────────────────────────────────────────────────────


class TestFixIntegrityGitStatusFailureTreatedDirty:
    """AC7: git status subprocess failure at the guard -> E_FIX_UNCOMMITTED_CHANGES
    (cautious default; never silently NO_CHANGES when tree state is unverifiable)."""

    def test_ac7_git_status_failure_at_guard_returns_error(self, tmp_path, monkeypatch) -> None:
        from bytedigger_engine.workflows.phase_6_fix_integrity import _build_fix_integrity_prompt  # type: ignore[import]
        from bytedigger_engine.workflows import phase_6_fix_integrity
        from bytedigger_engine.lib import git_port  # type: ignore[import]

        repo, sha = _make_repo_with_base_commit(tmp_path)
        scratchpad = tmp_path / "scratch"
        ctx = _make_fi_ctx(str(repo), sha, sha, scratchpad)

        real_git_read = git_port.git_read

        def _flaky_git_read(args, **kwargs):
            if list(args)[:2] == ["status", "--porcelain"]:
                raise FileNotFoundError("git binary not found (simulated)")
            return real_git_read(args, **kwargs)

        # Patch at the module used by phase_6_fix_integrity (collaborator patch,
        # not the UUT itself — UUT is _build_fix_integrity_prompt).
        monkeypatch.setattr(phase_6_fix_integrity.git_port, "git_read", _flaky_git_read)

        result = _build_fix_integrity_prompt(ctx, None)

        assert result.status == "error", (
            f"git status failure at the dirty-tree guard must be treated as "
            f"dirty-unknown (error), got {result.status!r}: {getattr(result, 'error', '')}"
        )
        assert result.error_code == "E_FIX_UNCOMMITTED_CHANGES", (
            f"Expected E_FIX_UNCOMMITTED_CHANGES, got {result.error_code!r}"
        )
        assert result.recoverable is False


# ─── Amendment 1: AC8 ───────────────────────────────────────────────────────


class TestCommitFixCodeDirtyTreeFallbackGatedOnExplicitGitCwdSource:
    """AC8 (Amendment 1, GH381 hazard): the Change-1 dirty-tree fallback must
    fire ONLY when git_cwd resolution came from an explicit source. When the
    resolver defaults to Path.cwd() (no cfg git_cwd / prev_data / worktree /
    scratchpad_climb hit), keep today's skip and label the reason
    'empty_manifest_cwd_default' — never enumerate/commit the ambient cwd.
    """

    def test_ac8_cwd_default_source_keeps_skip_never_commits_ambient_cwd(
        self, tmp_path, monkeypatch
    ) -> None:
        from bytedigger_engine.workflows.phase_6_review import _commit_fix_code  # type: ignore[import]
        from bytedigger_engine.workflows import phase_6_review

        # Safe tmp git repo that Path.cwd() will resolve to (never the real checkout).
        repo, pre_sha = _make_repo_with_base_commit(tmp_path)
        _write_file(repo, "src/uncommitted_fix.py", "def fixed(): return 2\n")
        monkeypatch.chdir(repo)

        # scratchpad_dir points somewhere with NO .git ancestor, and cfg has no
        # git_cwd / current_worktree_path -> resolver must fall through to cwd().
        no_git_scratch = tmp_path / "no_git_ancestor" / "scratch"
        no_git_scratch.mkdir(parents=True)

        captured: list[dict] = []
        monkeypatch.setattr(
            phase_6_review,
            "_emit_safe",
            lambda et, p, **kw: captured.append({"type": et, "payload": p}),
        )

        from bytedigger_engine.contracts import WorkflowContext

        ctx = WorkflowContext(
            tenant_id="hal",
            scope=None,
            db_path=None,
            org_config={"scratchpad_dir": str(no_git_scratch)},  # no git_cwd, no worktree
            question="Fix the thing",
            session_id="test-session-gh449-ac8",
            persona="hal",
            framework=None,
            domain=None,
        )
        prev = _make_prev(
            pre_fix_sha=pre_sha,
            cycle=1,
            worker_written_paths=[],
            manifest_source="harness_tool_record",
        )

        pre_call_head = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=repo, check=True
        ).stdout.strip()

        result = _commit_fix_code(ctx, prev)

        assert result.status == "ok", (
            f"Expected ok (must keep the skip on cwd-default source), got "
            f"{result.status!r}: {getattr(result, 'error', '')}"
        )
        assert (result.data or {}).get("fix_commit_sha") is None, (
            "fix_commit_sha must remain None — must NOT commit the ambient "
            "cwd-resolved repo's dirty file"
        )

        post_call_head = subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=repo, check=True
        ).stdout.strip()
        assert post_call_head == pre_call_head == pre_sha, (
            f"HEAD must be unchanged (no new commit) when git_cwd defaulted to "
            f"cwd(): before={pre_call_head!r} after={post_call_head!r} pre_sha={pre_sha!r}"
        )

        skip_events = [e for e in captured if e["type"] == "fix_commit_skipped"]
        assert len(skip_events) == 1, (
            f"Expected exactly 1 fix_commit_skipped event. All events: "
            f"{[e['type'] for e in captured]}"
        )
        assert skip_events[0]["payload"].get("reason") == "empty_manifest_cwd_default", (
            f"Expected reason='empty_manifest_cwd_default', got "
            f"{skip_events[0]['payload'].get('reason')!r}"
        )

        fallback_events = [e for e in captured if e["type"] == "fix_commit_manifest_fallback"]
        assert not fallback_events, (
            f"Must NOT emit fix_commit_manifest_fallback when git_cwd resolution "
            f"defaulted to cwd() (GH381 hazard). Got: {fallback_events!r}"
        )


# ─── Amendment 1: lib.git_cwd.resolve_git_cwd_with_source unit test ────────


class TestResolveGitCwdWithSource:
    """New lib API (Amendment 1): resolve_git_cwd_with_source(cfg, prev_data)
    returns (path, source) so callers can gate behavior on the resolution
    source (e.g. never dirty-tree-fallback-commit an ambient Path.cwd())."""

    def test_resolve_git_cwd_with_source_cfg_git_cwd_and_cwd_default(
        self, tmp_path, monkeypatch
    ) -> None:
        from bytedigger_engine.lib.git_cwd import resolve_git_cwd_with_source  # type: ignore[import]

        # Case 1: explicit cfg["git_cwd"] -> source "cfg_git_cwd".
        explicit_path = str(tmp_path / "explicit_repo")
        path, source = resolve_git_cwd_with_source({"git_cwd": explicit_path}, None)
        assert path == explicit_path, f"expected {explicit_path!r}, got {path!r}"
        assert source == "cfg_git_cwd", f"expected source 'cfg_git_cwd', got {source!r}"

        # Case 2: nothing set anywhere -> falls through to Path.cwd(), source "cwd".
        cwd_target = tmp_path / "ambient_cwd"
        cwd_target.mkdir()
        monkeypatch.chdir(cwd_target)
        path2, source2 = resolve_git_cwd_with_source({}, None)
        assert source2 == "cwd", f"expected source 'cwd', got {source2!r}"
        assert Path(path2) == Path.cwd(), (
            f"expected cwd-default path to equal Path.cwd() ({Path.cwd()!r}), got {path2!r}"
        )
