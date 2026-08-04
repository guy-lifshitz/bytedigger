"""Tests for phase_8_post_deploy workflow — Stage 2.8 port (deterministic v0).

v0 scope:
    - All steps deterministic (no LLM calls)
    - Best-effort cleanup: per-step ``status="ok"`` even when sub-actions fail
    - HARD GATE only on report writer (E_CLEANUP_REPORT_WRITE_FAILED)
    - Worktree cleanup uses local `git branch --merged` (no `gh pr view`)

Tests use a real git repo + worktree fixture in tmp_path so we exercise the
actual git plumbing rather than mocking subprocess.
"""
from __future__ import annotations

import dataclasses
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

HERE = Path(__file__).parent

from bytedigger_engine.contracts import WorkflowContext, StepResult  # noqa: E402
from bytedigger_engine.derive_state import replay  # noqa: E402
from bytedigger_engine.engine import WorkflowEngine  # noqa: E402
from bytedigger_engine.event_log import EventLog  # noqa: E402
from bytedigger_engine.workflows.phase_8_post_deploy import (  # noqa: E402
    CLEANUP_REPORT_RELPATH,
    DEFAULT_EVAL_KEEP_COUNT,
    DEFAULT_MAIN_BRANCH,
    SCRATCHPAD_SUBDIRS_TO_CLEAN,
    _parse_worktree_list,
    _write_cleanup_report,
    phase_8_post_deploy_workflow,
)
from bytedigger_engine import workflows  # noqa: E402


# ─── helpers ──────────────────────────────────────────────────────────────────


def make_ctx(scratchpad: Path, *, working_dir: Path | None = None, **org_extra) -> WorkflowContext:
    org = {"scratchpad_dir": str(scratchpad), **org_extra}
    if working_dir is not None:
        org["working_dir"] = str(working_dir)
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question="cleanup",
        session_id="test-session",
        persona="hal",
        framework=None,
        domain=None,
    )


def init_git_repo(path: Path) -> None:
    """Init a real git repo with one commit on main, isolated config."""
    path.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    subprocess.run(
        ["git", "init", "-q", "-b", "main"], cwd=str(path), check=True, env=env
    )
    subprocess.run(
        ["git", "config", "commit.gpgsign", "false"], cwd=str(path), check=True, env=env
    )
    (path / "README").write_text("init\n")
    subprocess.run(["git", "add", "."], cwd=str(path), check=True, env=env)
    subprocess.run(
        ["git", "commit", "-q", "-m", "init"], cwd=str(path), check=True, env=env
    )


def git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
           "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"}
    return subprocess.run(
        ["git", *args], cwd=str(cwd), check=True, env=env,
        capture_output=True, text=True,
    )


# ─── shape ────────────────────────────────────────────────────────────────────


def test_workflow_definition_shape():
    # AC13 (8C9F758C): suite_boyscout_gate inserted after full_suite_delta_gate,
    # before ship_to_pr. 1DA29C33: cleanup_run_allowlist inserted after
    # cleanup_eval_rotation, before write_cleanup_report — total 13 steps.
    wf = phase_8_post_deploy_workflow()
    assert wf.name == "phase_8_post_deploy"
    assert [s.name for s in wf.steps] == [
        "preserve_events_log",
        "persist_run_artifacts",
        "persist_learnings_to_db",
        "cleanup_worktrees",
        "cleanup_gone_branches",
        "cleanup_scratchpad_subdirs",
        "cleanup_eval_rotation",
        "cleanup_run_allowlist",
        "write_cleanup_report",
        "full_suite_delta_gate",
        "suite_boyscout_gate",
        "ship_to_pr",
        "deregister_session",
    ]


def test_defaults_are_sensible():
    assert DEFAULT_MAIN_BRANCH == "main"
    assert DEFAULT_EVAL_KEEP_COUNT == 10
    assert SCRATCHPAD_SUBDIRS_TO_CLEAN == (
        "research", "architecture", "specs", "tests", "reviews", "injection",
    )


def test_canonical_report_path():
    assert CLEANUP_REPORT_RELPATH == "post-deploy/cleanup-report.md"


# ─── parser unit ──────────────────────────────────────────────────────────────


def test_parse_worktree_list_basic():
    porcelain = (
        "worktree /repo/main\n"
        "HEAD abc123\n"
        "branch refs/heads/main\n"
        "\n"
        "worktree /repo/.worktrees/feat-x\n"
        "HEAD def456\n"
        "branch refs/heads/feat-x\n"
        "\n"
    )
    entries = _parse_worktree_list(porcelain)
    assert entries == [
        {"path": "/repo/main", "branch": "main"},
        {"path": "/repo/.worktrees/feat-x", "branch": "feat-x"},
    ]


def test_parse_worktree_list_detached():
    porcelain = (
        "worktree /repo/main\n"
        "HEAD abc123\n"
        "branch refs/heads/main\n"
        "\n"
        "worktree /repo/.worktrees/detached\n"
        "HEAD def456\n"
        "detached\n"
        "\n"
    )
    entries = _parse_worktree_list(porcelain)
    assert entries[1]["branch"] == ""


def test_parse_worktree_list_no_trailing_blank():
    porcelain = (
        "worktree /repo/main\n"
        "HEAD abc\n"
        "branch refs/heads/main\n"
    )
    entries = _parse_worktree_list(porcelain)
    assert entries == [{"path": "/repo/main", "branch": "main"}]


# ─── worktree cleanup ────────────────────────────────────────────────────────


def test_cleanup_worktrees_removes_merged_keeps_unmerged_skips_current(tmp_path):
    repo = tmp_path / "repo"
    init_git_repo(repo)

    # Merged worktree: branch from main, immediately merge back
    git(["worktree", "add", "-b", "merged-feat", str(tmp_path / "wt-merged")], repo)
    # Branch is a no-op vs main so it's already "merged" by reachability
    git(["worktree", "add", "-b", "unmerged-feat", str(tmp_path / "wt-unmerged")], repo)
    # Add a divergent commit on unmerged-feat so it's NOT merged into main
    git(["commit", "-q", "--allow-empty", "-m", "div"], tmp_path / "wt-unmerged")

    scratchpad = tmp_path / "scratch"
    eng = WorkflowEngine()
    eng.register("p8", phase_8_post_deploy_workflow())
    result, _ = eng.execute(
        "p8",
        make_ctx(
            scratchpad,
            working_dir=repo,
            current_worktree_path=str(tmp_path / "wt-merged"),  # protected
        ),
    )

    wt_data = result.data["step_summary"]["cleanup_worktrees"]
    # current_worktree_path is skipped, even though branch is merged
    assert any(str(tmp_path / "wt-merged") in p for p in wt_data["skipped"])
    # Unmerged worktree kept
    assert any(str(tmp_path / "wt-unmerged") in p for p in wt_data["kept_unmerged"])
    # Main worktree itself (cwd) is in skipped (matches working_dir)
    assert str(repo) in wt_data["skipped"]


def test_cleanup_worktrees_skips_when_cwd_is_subdir_of_worktree(tmp_path, monkeypatch):
    """E6041CDA regression: cwd inside (not equal to) a worktree must skip it.

    Prior bug: skip-current guard used exact string equality, so pytest run from
    `<wt>/SYSTEM/cli/build/` made cwd != worktree_path → guard failed → fresh
    forge-branches share main HEAD ⇒ classified merged ⇒ host worktree removed
    mid-pytest. 2026-04-27 incident wiped a live worktree this way.
    """
    repo = tmp_path / "repo"
    init_git_repo(repo)
    git(["worktree", "add", "-b", "host-feat", str(tmp_path / "wt-host")], repo)

    sub = tmp_path / "wt-host" / "deep" / "nested"
    sub.mkdir(parents=True)
    monkeypatch.chdir(sub)

    scratchpad = tmp_path / "scratch"
    eng = WorkflowEngine()
    eng.register("p8", phase_8_post_deploy_workflow())
    result, _ = eng.execute(
        "p8",
        make_ctx(scratchpad),  # no working_dir → falls back to Path.cwd() = sub (inside wt-host)
    )

    wt_data = result.data["step_summary"]["cleanup_worktrees"]
    assert str(tmp_path / "wt-host") in wt_data["skipped"], (
        "host worktree must be skipped because Path.cwd() resolves inside it"
    )
    assert str(tmp_path / "wt-host") not in wt_data["removed"], (
        "host worktree must NEVER be removed when cwd lives inside it"
    )


def test_cleanup_worktrees_skips_when_cwd_is_subdir_via_current_path(tmp_path):
    """E6041CDA defense for current_worktree_path: subdir relationship counts."""
    repo = tmp_path / "repo"
    init_git_repo(repo)
    git(["worktree", "add", "-b", "host-feat", str(tmp_path / "wt-host")], repo)

    scratchpad = tmp_path / "scratch"
    eng = WorkflowEngine()
    eng.register("p8", phase_8_post_deploy_workflow())
    result, _ = eng.execute(
        "p8",
        make_ctx(
            scratchpad,
            working_dir=repo,
            current_worktree_path=str(tmp_path / "wt-host" / "subdir"),
        ),
    )

    wt_data = result.data["step_summary"]["cleanup_worktrees"]
    assert str(tmp_path / "wt-host") in wt_data["skipped"]


def test_cleanup_worktrees_skips_when_anchor_canonicalizes_differently(tmp_path, monkeypatch):
    """E6041CDA: anchor and worktree may reach the same place via different prefixes.

    Real incident: macOS `/var/folders/...` symlinks to `/private/var/folders/...`.
    `git worktree list` output and `Path.cwd()` may surface different prefixes.
    `_path_contains` MUST .resolve() both sides so the protection holds regardless
    of which prefix each path was captured from.
    """
    repo = tmp_path / "repo"
    init_git_repo(repo)
    git(["worktree", "add", "-b", "host-feat", str(tmp_path / "wt-host")], repo)

    # Build an alias that reaches the worktree via a different absolute string.
    alias = tmp_path / "alias_link"
    alias.symlink_to(tmp_path / "wt-host")
    monkeypatch.chdir(alias / "deep" / "nested" if (alias / "deep" / "nested").exists() else alias)

    scratchpad = tmp_path / "scratch"
    eng = WorkflowEngine()
    eng.register("p8", phase_8_post_deploy_workflow())
    result, _ = eng.execute("p8", make_ctx(scratchpad))

    wt_data = result.data["step_summary"]["cleanup_worktrees"]
    # The worktree is reported by git via its real path; cwd reaches it via the alias symlink.
    # After .resolve() on both, they MUST collapse to the same real path.
    assert str(tmp_path / "wt-host") in wt_data["skipped"]
    assert str(tmp_path / "wt-host") not in wt_data["removed"]


def test_cleanup_worktrees_does_not_skip_sibling_with_shared_prefix(tmp_path, monkeypatch):
    """E6041CDA regression: `wt-host` must NOT be confused with `wt-host-2`.

    Guards against a future refactor that swaps `is_relative_to` for raw
    string-prefix matching without anchoring on a path separator —
    `'wt-host'.startswith('wt-host')` would erroneously trigger.
    """
    repo = tmp_path / "repo"
    init_git_repo(repo)
    git(["worktree", "add", "-b", "host-feat", str(tmp_path / "wt-host")], repo)
    git(["worktree", "add", "-b", "neighbor-feat", str(tmp_path / "wt-host-2")], repo)

    sub = tmp_path / "wt-host-2" / "inside"
    sub.mkdir()
    monkeypatch.chdir(sub)

    scratchpad = tmp_path / "scratch"
    eng = WorkflowEngine()
    eng.register("p8", phase_8_post_deploy_workflow())
    result, _ = eng.execute("p8", make_ctx(scratchpad))

    wt_data = result.data["step_summary"]["cleanup_worktrees"]
    assert str(tmp_path / "wt-host-2") in wt_data["skipped"]
    # wt-host shares a string prefix with wt-host-2 but is a SEPARATE worktree.
    # It is merged (no divergent commits) so without the path-separator anchor
    # it would be wrongly skipped, OR (worse) wrongly conflated and removed.
    assert str(tmp_path / "wt-host") in wt_data["removed"], (
        "sibling worktree with shared prefix must be eligible for removal"
    )
    assert str(tmp_path / "wt-host") not in wt_data["skipped"]


def test_cleanup_worktrees_removes_merged_when_not_current(tmp_path):
    """Merged worktree IS removed when not protected as current."""
    repo = tmp_path / "repo"
    init_git_repo(repo)
    wt_path = tmp_path / "wt-merged"
    git(["worktree", "add", "-b", "merged-feat", str(wt_path)], repo)

    scratchpad = tmp_path / "scratch"
    eng = WorkflowEngine()
    eng.register("p8", phase_8_post_deploy_workflow())
    result, _ = eng.execute("p8", make_ctx(scratchpad, working_dir=repo))

    wt_data = result.data["step_summary"]["cleanup_worktrees"]
    assert str(wt_path) in wt_data["removed"]
    assert not wt_path.exists()


def test_cleanup_worktrees_handles_non_git_dir(tmp_path):
    """Best-effort: non-git working_dir doesn't crash, surfaces error in data."""
    not_a_repo = tmp_path / "plain"
    not_a_repo.mkdir()
    scratchpad = tmp_path / "scratch"

    eng = WorkflowEngine()
    eng.register("p8", phase_8_post_deploy_workflow())
    result, _ = eng.execute("p8", make_ctx(scratchpad, working_dir=not_a_repo))

    assert result.status == "ok"  # best-effort, never blocks pipeline close
    wt_data = result.data["step_summary"]["cleanup_worktrees"]
    assert wt_data["removed"] == []
    assert len(wt_data["errors"]) >= 1


# ─── gone branches ───────────────────────────────────────────────────────────


def test_cleanup_gone_branches_handles_no_remote(tmp_path):
    """No remote → fetch errors in data, no branches deleted, step still ok."""
    repo = tmp_path / "repo"
    init_git_repo(repo)
    scratchpad = tmp_path / "scratch"

    eng = WorkflowEngine()
    eng.register("p8", phase_8_post_deploy_workflow())
    result, _ = eng.execute("p8", make_ctx(scratchpad, working_dir=repo))

    assert result.status == "ok"
    gb_data = result.data["step_summary"]["cleanup_gone_branches"]
    assert gb_data["deleted"] == []


# ─── scratchpad cleanup ──────────────────────────────────────────────────────


def test_cleanup_scratchpad_removes_all_six_subdirs(tmp_path):
    scratchpad = tmp_path / "scratch"
    for sub in SCRATCHPAD_SUBDIRS_TO_CLEAN:
        d = scratchpad / sub
        d.mkdir(parents=True)
        (d / "stale.md").write_text("old\n")
    # post-deploy/ MUST survive
    pd = scratchpad / "post-deploy"
    pd.mkdir(parents=True)
    (pd / "report.md").write_text("kept\n")

    eng = WorkflowEngine()
    eng.register("p8", phase_8_post_deploy_workflow())
    # GH1086 6604CC4B: owned-scratchpad case
    ctx = dataclasses.replace(
        make_ctx(scratchpad, working_dir=tmp_path), session_id=scratchpad.name
    )
    eng.execute("p8", ctx)

    for sub in SCRATCHPAD_SUBDIRS_TO_CLEAN:
        assert not (scratchpad / sub).exists(), f"{sub} should be removed"
    assert (pd / "report.md").read_text() == "kept\n"


def test_cleanup_scratchpad_skips_missing_subdirs(tmp_path):
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    # No subdirs at all → no errors
    (scratchpad / "specs").mkdir()
    (scratchpad / "specs" / "x.md").write_text("x")

    eng = WorkflowEngine()
    eng.register("p8", phase_8_post_deploy_workflow())
    # GH1086 6604CC4B: owned-scratchpad case
    ctx = dataclasses.replace(
        make_ctx(scratchpad, working_dir=tmp_path), session_id=scratchpad.name
    )
    result, _ = eng.execute("p8", ctx)

    sp_data = result.data["step_summary"]["cleanup_scratchpad_subdirs"]
    assert any(str(scratchpad / "specs") in p for p in sp_data["removed"])
    assert sp_data["errors"] == []


def test_cleanup_scratchpad_preserves_post_deploy_dir(tmp_path):
    """Even if post-deploy/ has many files, none are touched."""
    scratchpad = tmp_path / "scratch"
    pd = scratchpad / "post-deploy"
    pd.mkdir(parents=True)
    (pd / "a.md").write_text("a")
    (pd / "b.md").write_text("b")
    (scratchpad / "specs").mkdir()

    eng = WorkflowEngine()
    eng.register("p8", phase_8_post_deploy_workflow())
    eng.execute("p8", make_ctx(scratchpad, working_dir=tmp_path))

    assert (pd / "a.md").read_text() == "a"
    assert (pd / "b.md").read_text() == "b"


# ─── eval rotation ───────────────────────────────────────────────────────────


def test_cleanup_eval_rotation_keeps_newest_n(tmp_path):
    scratchpad = tmp_path / "scratch"
    eval_dir = scratchpad / "evals"
    eval_dir.mkdir(parents=True)
    # Create 12 files with increasing mtimes
    for i in range(12):
        f = eval_dir / f"run-{i:02d}"
        f.write_text(str(i))
        os.utime(f, (1_700_000_000 + i, 1_700_000_000 + i))

    eng = WorkflowEngine()
    eng.register("p8", phase_8_post_deploy_workflow())
    eng.execute("p8", make_ctx(scratchpad, working_dir=tmp_path, eval_keep_count=5))

    remaining = sorted(eval_dir.iterdir())
    # 5 newest (largest mtimes) → 07..11
    assert [p.name for p in remaining] == [f"run-{i:02d}" for i in range(7, 12)]


def test_cleanup_eval_rotation_no_evals_dir(tmp_path):
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()

    eng = WorkflowEngine()
    eng.register("p8", phase_8_post_deploy_workflow())
    result, _ = eng.execute("p8", make_ctx(scratchpad, working_dir=tmp_path))

    ev_data = result.data["step_summary"]["cleanup_eval_rotation"]
    assert ev_data["kept"] == 0
    assert ev_data["removed_count"] == 0
    assert ev_data["skipped_reason"] == "no evals/ dir"


def test_cleanup_eval_rotation_under_keep_count_keeps_all(tmp_path):
    scratchpad = tmp_path / "scratch"
    eval_dir = scratchpad / "evals"
    eval_dir.mkdir(parents=True)
    for i in range(3):
        (eval_dir / f"run-{i}").write_text(str(i))

    eng = WorkflowEngine()
    eng.register("p8", phase_8_post_deploy_workflow())
    eng.execute("p8", make_ctx(scratchpad, working_dir=tmp_path, eval_keep_count=10))

    assert len(list(eval_dir.iterdir())) == 3


def test_cleanup_eval_rotation_handles_subdirs(tmp_path):
    scratchpad = tmp_path / "scratch"
    eval_dir = scratchpad / "evals"
    eval_dir.mkdir(parents=True)
    # 3 subdirs with files inside
    for i in range(3):
        d = eval_dir / f"run-{i}"
        d.mkdir()
        (d / "result.json").write_text("{}")
        os.utime(d, (1_700_000_000 + i, 1_700_000_000 + i))

    eng = WorkflowEngine()
    eng.register("p8", phase_8_post_deploy_workflow())
    eng.execute("p8", make_ctx(scratchpad, working_dir=tmp_path, eval_keep_count=1))

    remaining = sorted(eval_dir.iterdir())
    assert [p.name for p in remaining] == ["run-2"]
    assert (remaining[0] / "result.json").exists()


# ─── report writer ───────────────────────────────────────────────────────────


def test_cleanup_report_aggregates_all_step_data(tmp_path):
    scratchpad = tmp_path / "scratch"
    (scratchpad / "specs").mkdir(parents=True)
    eval_dir = scratchpad / "evals"
    eval_dir.mkdir()
    (eval_dir / "run-1").write_text("1")

    eng = WorkflowEngine()
    eng.register("p8", phase_8_post_deploy_workflow())
    result, _ = eng.execute("p8", make_ctx(scratchpad, working_dir=tmp_path))

    report = (scratchpad / CLEANUP_REPORT_RELPATH).read_text()
    assert "Phase 8 Post-Deploy Cleanup Report" in report
    assert "## Worktrees" in report
    assert "## Gone Branches" in report
    assert "## Scratchpad Subdirs" in report
    assert "## Eval Rotation" in report
    assert "## Learnings" in report
    assert "phase_8_post_deploy: complete" in report
    # report is in step_summary too
    assert "cleanup_worktrees" in result.data["step_summary"]


def test_cleanup_report_write_failure_is_hard_gate(tmp_path):
    """Cannot write report → E_CLEANUP_REPORT_WRITE_FAILED.

    Simulate by making the report's parent path a file (not a dir),
    so mkdir + write_text both fail.
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    # Block post-deploy/ from being a directory by creating a file there
    blocker = scratchpad / "post-deploy"
    blocker.write_text("not-a-dir")

    eng = WorkflowEngine()
    eng.register("p8", phase_8_post_deploy_workflow())
    result, _ = eng.execute("p8", make_ctx(scratchpad, working_dir=tmp_path))

    assert result.status == "error"
    assert result.error_code == "E_CLEANUP_REPORT_WRITE_FAILED"


# ─── error paths ──────────────────────────────────────────────────────────────


def test_missing_scratchpad_dir_raises(tmp_path):
    ctx = WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={},
        question="cleanup",
        session_id="s",
        persona="hal",
        framework=None,
        domain=None,
    )
    eng = WorkflowEngine()
    eng.register("p8", phase_8_post_deploy_workflow())
    with pytest.raises(ValueError, match="scratchpad_dir"):
        eng.execute("p8", ctx)


# ─── verification: events emitted, derived state matches expectation ──────────


def test_events_emitted_five_steps_happy_path(tmp_path):
    scratchpad = tmp_path / "scratch"
    log_path = tmp_path / "events.jsonl"
    log = EventLog(log_path)
    eng = WorkflowEngine(event_log=log)
    eng.register("phase_8_post_deploy", phase_8_post_deploy_workflow())

    eng.execute(
        "phase_8_post_deploy",
        make_ctx(scratchpad, working_dir=tmp_path),
        run_id="rid-p8",
    )

    events = EventLog(log_path).read_all()
    finished = [e for e in events if e["event_type"] == "step_finished"]
    # AC13 (8C9F758C): suite_boyscout_gate inserted. 1DA29C33: cleanup_run_allowlist
    # inserted after cleanup_eval_rotation → 13 total steps.
    assert len(finished) == 13
    assert [e["payload"]["status"] for e in finished] == ["ok"] * 13
    assert [e["payload"]["step_name"] for e in finished] == [
        "preserve_events_log",
        "persist_run_artifacts",
        "persist_learnings_to_db",
        "cleanup_worktrees",
        "cleanup_gone_branches",
        "cleanup_scratchpad_subdirs",
        "cleanup_eval_rotation",
        "cleanup_run_allowlist",
        "write_cleanup_report",
        "full_suite_delta_gate",
        "suite_boyscout_gate",
        "ship_to_pr",
        "deregister_session",
    ]

    state = replay(events)
    run = state["runs"]["rid-p8"]
    assert run["workflow_name"] == "phase_8_post_deploy"
    assert run["status"] == "ok"
    assert len(run["steps"]) == 13


def test_registry_includes_phase_8_post_deploy():
    eng = WorkflowEngine()
    workflows.register_all(eng)
    assert "phase_8_post_deploy" in eng.registered()


# ─── deregister_session ──────────────────────────────────────────────────────


def test_run_py_normalizes_unknown_sentinel_to_none(tmp_path):
    """Boundary normalization: parse_ctx maps legacy 'unknown' → None.

    Single source of truth for the contract — once parse_ctx maps 'unknown'
    to None, the engine boundary (required_ctx_fields=["session_id"]) hard-fails
    via E_REQUIRED_CTX_MISSING. The in-step guard in phase_8 only matters for
    direct workflow callers that bypass run.py; covered by
    test_ctx_boundary_hardfail.test_engine_hardfails_when_session_id_is_unknown_sentinel
    (engine-side) and below for the run.py side.
    """
    import argparse
    from bytedigger_engine import run as run_mod  # type: ignore[import-not-found]

    args = argparse.Namespace(
        ctx=None,
        ctx_json='{"session_id": "unknown", "tenant_id": "hal"}',
    )
    ctx = run_mod.parse_ctx(args)
    assert ctx.session_id is None, (
        f"parse_ctx must normalize legacy 'unknown' sentinel to None; got {ctx.session_id!r}"
    )


def test_deregister_session_calls_cli_with_session_id(tmp_path, monkeypatch):
    """Patch subprocess.run; assert cmd includes --session-id <id> and parses removed."""
    scratchpad = tmp_path / "scratch"
    ctx = WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={"scratchpad_dir": str(scratchpad), "working_dir": str(tmp_path)},
        question="cleanup",
        session_id="abc",
        persona="hal",
        framework=None,
        domain=None,
    )

    captured: dict = {}
    real_run = subprocess.run

    def fake_run(cmd, *args, **kwargs):
        # Only intercept the deregister-session call; let _git etc. through.
        if isinstance(cmd, list) and len(cmd) >= 2 and "deregister-session.ts" in str(cmd[1]):
            captured["cmd"] = cmd
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=0,
                stdout='{"removed": true, "session_id": "abc"}',
                stderr="",
            )
        return real_run(cmd, *args, **kwargs)

    from bytedigger_engine.workflows import phase_8_post_deploy as p8mod
    monkeypatch.setattr(p8mod.subprocess, "run", fake_run)

    eng = WorkflowEngine()
    eng.register("p8", phase_8_post_deploy_workflow())
    result, _ = eng.execute("p8", ctx)

    assert "--session-id" in captured["cmd"]
    assert "abc" in captured["cmd"]
    assert result.data["removed"] is True


def test_deregister_session_failure_is_graceful(tmp_path, monkeypatch):
    """subprocess.run raises FileNotFoundError → step still ok, deregistered=False."""
    scratchpad = tmp_path / "scratch"
    ctx = WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={"scratchpad_dir": str(scratchpad), "working_dir": str(tmp_path)},
        question="cleanup",
        session_id="abc",
        persona="hal",
        framework=None,
        domain=None,
    )

    real_run = subprocess.run

    def fake_run(cmd, *args, **kwargs):
        if isinstance(cmd, list) and len(cmd) >= 2 and "deregister-session.ts" in str(cmd[1]):
            raise FileNotFoundError("bun")
        return real_run(cmd, *args, **kwargs)

    from bytedigger_engine.workflows import phase_8_post_deploy as p8mod
    monkeypatch.setattr(p8mod.subprocess, "run", fake_run)

    eng = WorkflowEngine()
    eng.register("p8", phase_8_post_deploy_workflow())
    result, _ = eng.execute("p8", ctx)

    assert result.status == "ok"
    assert result.data["deregistered"] is False


# ─── db7e5a25: ## Learnings section in cleanup report ────────────────────────


def test_cleanup_report_learnings_skipped(tmp_path):
    """AC1 + AC2: skipped step dict renders '## Learnings' and '- Skipped: disabled'."""
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    prev = StepResult(
        status="ok",
        data={"step_summary": {"persist_learnings_to_db": {"skipped": True, "reason": "disabled"}}},
        duration_ms=0,
        step_name="dummy_prior",
    )
    _write_cleanup_report(make_ctx(scratchpad), prev)
    report = (scratchpad / CLEANUP_REPORT_RELPATH).read_text()
    assert "## Learnings" in report
    assert "- Skipped: disabled" in report


def test_cleanup_report_learnings_persistence_failed(tmp_path):
    """AC1 + AC3: failed persist dict renders '## Learnings' and '- Persistence failed: boom'."""
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    prev = StepResult(
        status="ok",
        data={"step_summary": {"persist_learnings_to_db": {
            "persisted": False, "error": "boom", "inserted": 0, "updated": 0, "skipped": 0,
        }}},
        duration_ms=0,
        step_name="dummy_prior",
    )
    _write_cleanup_report(make_ctx(scratchpad), prev)
    report = (scratchpad / CLEANUP_REPORT_RELPATH).read_text()
    assert "## Learnings" in report
    assert "- Persistence failed: boom" in report


def test_cleanup_report_learnings_persisted_success(tmp_path):
    """AC1 + AC4 + AC5: success dict renders counts and event; int skipped=5 does NOT trigger skip branch."""
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    prev = StepResult(
        status="ok",
        data={"step_summary": {"persist_learnings_to_db": {
            "persisted": True, "inserted": 3, "updated": 2, "skipped": 5,
            "event": "learnings_persisted",
        }}},
        duration_ms=0,
        step_name="dummy_prior",
    )
    _write_cleanup_report(make_ctx(scratchpad), prev)
    report = (scratchpad / CLEANUP_REPORT_RELPATH).read_text()
    # AC1
    assert "## Learnings" in report
    # AC4: row-counts rendered
    assert "- Persisted: inserted=3, updated=2, skipped=5" in report
    # AC4: event rendered
    assert "- Event: learnings_persisted" in report
    # AC5 dual-shape guard: int skipped=5 must NOT mis-route into "- Skipped:" branch
    assert "\n- Skipped:" not in report


def test_cleanup_report_learnings_not_recorded(tmp_path):
    """AC1 + AC6: empty/absent dict renders '## Learnings' and '- Not recorded'."""
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    prev = StepResult(
        status="ok",
        data={"step_summary": {"persist_learnings_to_db": {}}},
        duration_ms=0,
        step_name="dummy_prior",
    )
    _write_cleanup_report(make_ctx(scratchpad), prev)
    report = (scratchpad / CLEANUP_REPORT_RELPATH).read_text()
    assert "## Learnings" in report
    assert "- Not recorded" in report
