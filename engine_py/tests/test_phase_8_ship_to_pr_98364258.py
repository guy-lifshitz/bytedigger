"""RED-phase tests — 98364258: durable idempotent ship step (phase_8 ship_to_pr).

13 ACs from spec §3.  All tests are expected to FAIL today because:
- `_ship_to_pr`, `_gh`, `_parse_pr_url` don't exist in phase_8_post_deploy.py
- `ship_to_pr` is absent from the phase_8_post_deploy_workflow() step list
- AC1 asserts a step-list that doesn't match current production

Fake-gh mechanism: a tiny shell script written into tmp_path per-test that records
its argv to gh-argv.log and returns canned JSON / exit codes.  All tests point the
SUT at it via monkeypatch.setenv("HAL_GH_BIN", ...).

No event-assertion ACs: spec §2.y confirmed phase_8 steps carry state only in
step_summary (no emit_event seam — grep of phase_8_post_deploy.py shows zero
emit_event calls).
"""
from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

# Imports rely on conftest-import-time sys.path setup (§1q / 81F97F3D).
from bytedigger_engine.contracts import WorkflowContext  # noqa: E402
from bytedigger_engine.engine import WorkflowEngine  # noqa: E402
from bytedigger_engine.workflows.phase_8_post_deploy import (  # noqa: E402
    _gh,
    _parse_pr_url,
    _resolve_working_dir,
    _ship_to_pr,
    phase_8_post_deploy_workflow,
)


# ─── helpers ─────────────────────────────────────────────────────────────────


def make_ctx(scratchpad: Path, *, working_dir: Path | None = None, **org_extra) -> WorkflowContext:
    org = {"scratchpad_dir": str(scratchpad), **org_extra}
    if working_dir is not None:
        org["working_dir"] = str(working_dir)
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question="ship",
        session_id="test-session",
        persona="hal",
        framework=None,
        domain=None,
    )


def init_git_repo(path: Path) -> None:
    """Init a real git repo with one commit on main, isolated config."""
    path.mkdir(parents=True, exist_ok=True)
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=str(path), check=True, env=env)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=str(path), check=True, env=env)
    (path / "README").write_text("init\n")
    subprocess.run(["git", "add", "."], cwd=str(path), check=True, env=env)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=str(path), check=True, env=env)


def git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    env = {
        **os.environ,
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )


def make_feature_branch_with_commit(repo: Path, branch: str = "feat/my-feature") -> None:
    """Create a feature branch with one commit ahead of main."""
    git(["checkout", "-b", branch], repo)
    (repo / "feature.txt").write_text("feature\n")
    git(["add", "."], repo)
    git(["commit", "-q", "-m", "feat: add feature"], repo)


def write_fake_gh(
    tmp_path: Path,
    *,
    pr_list_json: str = "[]",
    pr_create_rc: int = 0,
    pr_create_output: str = "https://github.com/o/r/pull/7",
) -> Path:
    """Write a fake `gh` shell script to tmp_path/fake-gh.

    The script:
    - Records all argv to gh-argv.log (one invocation per line, tab-separated).
    - On `pr list …` → prints pr_list_json and exits 0.
    - On `pr create …` → prints pr_create_output and exits pr_create_rc.
    - Any other subcommand → exits 0 with empty stdout.
    """
    log = tmp_path / "gh-argv.log"
    script = tmp_path / "fake-gh"
    # Use printf to append tab-separated args to log, then dispatch.
    script_body = f"""\
#!/bin/sh
printf '%s\\t' "$@" >> "{log}"
printf '\\n' >> "{log}"
case "$1 $2" in
  "pr list")
    printf '%s\\n' '{pr_list_json}'
    exit 0
    ;;
  "pr create")
    printf '%s\\n' '{pr_create_output}'
    exit {pr_create_rc}
    ;;
  *)
    exit 0
    ;;
esac
"""
    script.write_text(script_body)
    script.chmod(script.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return script


def gh_argv_log(tmp_path: Path) -> list[list[str]]:
    """Parse gh-argv.log → list of invocations, each a list of args."""
    log = tmp_path / "gh-argv.log"
    if not log.exists():
        return []
    lines = log.read_text().splitlines()
    result = []
    for line in lines:
        if line.strip():
            result.append([a for a in line.split("\t") if a])
    return result


# ─── AC1: step list shape ──────────────────────────────────────────────────────


def test_ac1_ship_to_pr_present_at_index_9_full_step_list():
    """AC1: ship_to_pr is at index 11 (suite_boyscout_gate 8C9F758C took index 10,
    after full_suite_delta_gate idx 9, before deregister_session idx 12; 1DA29C33's
    cleanup_run_allowlist inserted at idx 7 after cleanup_eval_rotation shifted
    everything from write_cleanup_report onward by +1 — the index-pinned 0..3
    block is undisplaced), full step-name list asserted."""
    wf = phase_8_post_deploy_workflow()
    names = [s.name for s in wf.steps]
    assert names[11] == "ship_to_pr", (
        f"Expected ship_to_pr at index 11, got {names[11]!r}. Full list: {names}"
    )
    assert names == [
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
    ], f"Full step list mismatch: {names}"


# ─── AC2: no-op default (no ship_pr in org_config) ───────────────────────────


def test_ac2_no_ship_pr_config_skips_with_no_gh_invocation(tmp_path, monkeypatch):
    """AC2: no ship_pr → ok, skipped msg, fake-gh argv log EMPTY."""
    monkeypatch.delenv("HAL_BUILD_SHIP_PR", raising=False)
    repo = tmp_path / "repo"
    init_git_repo(repo)
    fake_gh = write_fake_gh(tmp_path)
    monkeypatch.setenv("HAL_GH_BIN", str(fake_gh))

    scratchpad = tmp_path / "scratch"
    ctx = make_ctx(scratchpad, working_dir=repo)
    # No ship_pr in org_config — default-off
    assert not (ctx.org_config or {}).get("ship_pr")

    result = _ship_to_pr(ctx, None)

    assert result.status == "ok"
    summary = result.data.get("step_summary", {}).get("ship_to_pr", result.data)
    skipped = summary.get("skipped") or result.data.get("skipped")
    assert skipped == "ship_pr disabled", f"Expected skipped='ship_pr disabled', got {skipped!r}"
    # No gh or git push should have been called
    assert gh_argv_log(tmp_path) == [], "fake-gh must NOT be invoked when ship_pr is disabled"


# ─── AC3: ship_pr=true but branch == main ─────────────────────────────────────


def test_ac3_on_main_branch_skips(tmp_path, monkeypatch):
    """AC3: ship_pr=true, currently on main → ok, skipped mentions main; no push/gh."""
    repo = tmp_path / "repo"
    init_git_repo(repo)
    fake_gh = write_fake_gh(tmp_path)
    monkeypatch.setenv("HAL_GH_BIN", str(fake_gh))

    scratchpad = tmp_path / "scratch"
    ctx = make_ctx(scratchpad, working_dir=repo, ship_pr=True)

    result = _ship_to_pr(ctx, None)

    assert result.status == "ok"
    skipped = result.data.get("skipped") or (
        result.data.get("step_summary", {}).get("ship_to_pr", {}).get("skipped")
    )
    assert skipped is not None, "Expected a 'skipped' key in result data"
    assert "main" in skipped.lower() or "detached" in skipped.lower(), (
        f"Expected skip reason to mention main/detached, got: {skipped!r}"
    )
    assert gh_argv_log(tmp_path) == [], "No gh invocations expected when on main"


# ─── AC4: ship_pr=true, feature branch, 0 commits ahead ──────────────────────


def test_ac4_no_commits_ahead_skips(tmp_path, monkeypatch):
    """AC4: feature branch with 0 commits ahead of main → ok, skipped='no commits ahead'."""
    repo = tmp_path / "repo"
    init_git_repo(repo)
    # Create empty feature branch (no new commits)
    git(["checkout", "-b", "feat/empty"], repo)
    fake_gh = write_fake_gh(tmp_path)
    monkeypatch.setenv("HAL_GH_BIN", str(fake_gh))

    scratchpad = tmp_path / "scratch"
    ctx = make_ctx(scratchpad, working_dir=repo, ship_pr=True)

    result = _ship_to_pr(ctx, None)

    assert result.status == "ok"
    skipped = result.data.get("skipped") or (
        result.data.get("step_summary", {}).get("ship_to_pr", {}).get("skipped")
    )
    assert skipped == "no commits ahead", (
        f"Expected skipped='no commits ahead', got {skipped!r}"
    )
    assert gh_argv_log(tmp_path) == [], "No gh invocations expected when no commits ahead"


# ─── AC2–AC5: _strip_untracked unit tests ─────────────────────────────────────


def test_strip_untracked_ac2_untracked_dropped():
    """AC2: _strip_untracked('?? dirty.txt') returns '' (untracked line dropped)."""
    from bytedigger_engine.workflows.phase_8_post_deploy import _strip_untracked  # deferred: symbol absent pre-GREEN
    assert _strip_untracked("?? dirty.txt") == ""


def test_strip_untracked_ac3_tracked_kept():
    """AC3: _strip_untracked(' M tracked.py') returns ' M tracked.py' (tracked line kept)."""
    from bytedigger_engine.workflows.phase_8_post_deploy import _strip_untracked  # deferred: symbol absent pre-GREEN
    assert _strip_untracked(" M tracked.py") == " M tracked.py"


def test_strip_untracked_ac4_mixed_keep_tracked_drop_untracked():
    """AC4: mixed input — tracked kept, untracked dropped."""
    from bytedigger_engine.workflows.phase_8_post_deploy import _strip_untracked  # deferred: symbol absent pre-GREEN
    assert _strip_untracked("M  staged.py\n?? new.txt") == "M  staged.py"


def test_strip_untracked_ac5_empty_and_whitespace():
    """AC5: empty string and whitespace-only input both return ''."""
    from bytedigger_engine.workflows.phase_8_post_deploy import _strip_untracked  # deferred: symbol absent pre-GREEN
    assert _strip_untracked("") == ""
    assert _strip_untracked("\n  \n") == ""


# ─── AC6: untracked file does NOT block ship ──────────────────────────────────


def test_ac6_untracked_file_does_not_block_ship(tmp_path, monkeypatch):
    """AC6: ship_pr=true, feature branch, ONLY an untracked file dirty → NOT E_SHIP_DIRTY_TREE.

    After the dirty-check passes, the SUT proceeds to push; there is no origin remote so
    push fails with E_SHIP_PUSH_FAILED — that is expected. We only assert the guard does
    NOT fire on an untracked file.
    """
    repo = tmp_path / "repo"
    init_git_repo(repo)
    make_feature_branch_with_commit(repo)
    # Dirty the tree with ONLY an untracked file (not staged, not tracked)
    (repo / "dirty.txt").write_text("dirty\n")

    fake_gh = write_fake_gh(tmp_path)
    monkeypatch.setenv("HAL_GH_BIN", str(fake_gh))

    scratchpad = tmp_path / "scratch"
    ctx = make_ctx(scratchpad, working_dir=repo, ship_pr=True)

    result = _ship_to_pr(ctx, None)

    # The guard MUST NOT fire on an untracked file
    assert result.error_code != "E_SHIP_DIRTY_TREE", (
        f"Untracked file must NOT trigger E_SHIP_DIRTY_TREE; got error_code={result.error_code!r}"
    )


# ─── AC7: tracked modification still blocks ship ──────────────────────────────


def test_ac7_tracked_modification_still_blocks(tmp_path, monkeypatch):
    """AC7: ship_pr=true, feature branch, TRACKED committed file modified (unstaged) → E_SHIP_DIRTY_TREE.

    Proves the guard still fires on genuine uncommitted tracked work after the fix.
    README is committed by init_git_repo; modifying it without staging = ' M README'.
    """
    repo = tmp_path / "repo"
    init_git_repo(repo)
    make_feature_branch_with_commit(repo)
    # Modify a TRACKED committed file without staging (unstaged tracked modification)
    (repo / "README").write_text("modified\n")

    fake_gh = write_fake_gh(tmp_path)
    monkeypatch.setenv("HAL_GH_BIN", str(fake_gh))

    scratchpad = tmp_path / "scratch"
    ctx = make_ctx(scratchpad, working_dir=repo, ship_pr=True)

    result = _ship_to_pr(ctx, None)

    assert result.status == "error", f"Expected error, got {result.status!r}"
    assert result.error_code == "E_SHIP_DIRTY_TREE", (
        f"Expected E_SHIP_DIRTY_TREE for tracked modification, got {result.error_code!r}"
    )
    assert result.recoverable is False, "Dirty tree (tracked mod) must be non-recoverable"


# ─── AC6 (old numbering): create path (pr list → [], pr create → rc0+URL) ────


def test_ac6_creates_pr_when_none_exists(tmp_path, monkeypatch):
    """AC6 ★: pr list → [], pr create → rc0 + URL → ok, pr_url set, idempotent_hit=False."""
    repo = tmp_path / "repo"
    init_git_repo(repo)
    make_feature_branch_with_commit(repo, "feat/my-feature")

    # Set up origin as a bare clone so push succeeds
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "clone", "--bare", str(repo), str(origin)], check=True,
                   capture_output=True)
    git(["remote", "add", "origin", str(origin)], repo)
    git(["push", "-u", "origin", "feat/my-feature"], repo)

    fake_gh = write_fake_gh(
        tmp_path,
        pr_list_json="[]",
        pr_create_rc=0,
        pr_create_output="https://github.com/o/r/pull/7",
    )
    monkeypatch.setenv("HAL_GH_BIN", str(fake_gh))

    scratchpad = tmp_path / "scratch"
    ctx = make_ctx(scratchpad, working_dir=repo, ship_pr=True)

    result = _ship_to_pr(ctx, None)

    assert result.status == "ok", f"Expected ok, got {result.status!r} / {result.error_code!r}"

    # Check step_summary or top-level data for pr_url
    data = result.data
    summary_entry = data.get("step_summary", {}).get("ship_to_pr", data)
    pr_url = summary_entry.get("pr_url") or data.get("pr_url")
    assert pr_url == "https://github.com/o/r/pull/7", (
        f"Expected pr_url='https://github.com/o/r/pull/7', got {pr_url!r}"
    )
    idempotent_hit = summary_entry.get("idempotent_hit")
    if idempotent_hit is None:
        idempotent_hit = data.get("idempotent_hit")
    assert idempotent_hit is False, f"Expected idempotent_hit=False, got {idempotent_hit!r}"

    # Assert fake-gh was called with pr create
    invocations = gh_argv_log(tmp_path)
    create_calls = [inv for inv in invocations if "create" in inv]
    assert len(create_calls) >= 1, f"Expected pr create in argv log, got: {invocations}"
    create_call = create_calls[0]
    assert "pr" in create_call, f"Expected 'pr' in create argv: {create_call}"
    assert "--head" in create_call, f"Expected --head in create argv: {create_call}"
    assert "feat/my-feature" in create_call, (
        f"Expected branch 'feat/my-feature' in create argv: {create_call}"
    )
    assert "--base" in create_call, f"Expected --base in create argv: {create_call}"
    assert "main" in create_call, f"Expected 'main' (base) in create argv: {create_call}"


# ─── AC7: pr already exists (529-resume idempotency) ─────────────────────────


def test_ac7_pr_already_exists_no_duplicate_create(tmp_path, monkeypatch):
    """AC7 ★: pr list → existing PR → ok, idempotent_hit=True, no pr create (DBOS-replay-safe)."""
    repo = tmp_path / "repo"
    init_git_repo(repo)
    make_feature_branch_with_commit(repo, "feat/my-feature")

    origin = tmp_path / "origin.git"
    subprocess.run(["git", "clone", "--bare", str(repo), str(origin)], check=True,
                   capture_output=True)
    git(["remote", "add", "origin", str(origin)], repo)
    git(["push", "-u", "origin", "feat/my-feature"], repo)

    existing_pr = [{"url": "https://github.com/o/r/pull/42", "number": 42}]
    fake_gh = write_fake_gh(
        tmp_path,
        pr_list_json=json.dumps(existing_pr),
        pr_create_rc=0,
        pr_create_output="https://github.com/o/r/pull/99",  # should NOT be reached
    )
    monkeypatch.setenv("HAL_GH_BIN", str(fake_gh))

    scratchpad = tmp_path / "scratch"
    ctx = make_ctx(scratchpad, working_dir=repo, ship_pr=True)

    result = _ship_to_pr(ctx, None)

    assert result.status == "ok", f"Expected ok, got {result.status!r}"

    data = result.data
    summary_entry = data.get("step_summary", {}).get("ship_to_pr", data)
    pr_url = summary_entry.get("pr_url") or data.get("pr_url")
    assert pr_url == "https://github.com/o/r/pull/42", (
        f"Expected existing pr_url 'https://github.com/o/r/pull/42', got {pr_url!r}"
    )
    idempotent_hit = summary_entry.get("idempotent_hit")
    if idempotent_hit is None:
        idempotent_hit = data.get("idempotent_hit")
    assert idempotent_hit is True, f"Expected idempotent_hit=True, got {idempotent_hit!r}"

    # pr list MUST have been called; pr create must NOT
    invocations = gh_argv_log(tmp_path)
    list_calls = [inv for inv in invocations if "list" in inv]
    create_calls = [inv for inv in invocations if "create" in inv]
    assert len(list_calls) >= 1, f"Expected pr list in argv log, got: {invocations}"
    assert create_calls == [], (
        f"pr create must NOT be called when PR already exists (DBOS-replay-safe); got: {create_calls}"
    )


# ─── AC8: push fails ──────────────────────────────────────────────────────────


def test_ac8_push_failure_returns_recoverable_error(tmp_path, monkeypatch):
    """AC8: push rc≠0 → error, E_SHIP_PUSH_FAILED, recoverable=True."""
    repo = tmp_path / "repo"
    init_git_repo(repo)
    make_feature_branch_with_commit(repo, "feat/push-fail")
    # Point origin to a non-existent path so push fails
    git(["remote", "add", "origin", "/nonexistent/path/to/nowhere.git"], repo)

    fake_gh = write_fake_gh(tmp_path)
    monkeypatch.setenv("HAL_GH_BIN", str(fake_gh))

    scratchpad = tmp_path / "scratch"
    ctx = make_ctx(scratchpad, working_dir=repo, ship_pr=True)

    result = _ship_to_pr(ctx, None)

    assert result.status == "error", f"Expected error, got {result.status!r}"
    assert result.error_code == "E_SHIP_PUSH_FAILED", (
        f"Expected E_SHIP_PUSH_FAILED, got {result.error_code!r}"
    )
    assert result.recoverable is True, "Push failure must be recoverable=True (transient)"


# ─── AC9: pr create fails ─────────────────────────────────────────────────────


def test_ac9_pr_create_failure_returns_recoverable_error(tmp_path, monkeypatch):
    """AC9: pr list → [], pr create → rc≠0 → error, E_SHIP_PR_FAILED, recoverable=True."""
    repo = tmp_path / "repo"
    init_git_repo(repo)
    make_feature_branch_with_commit(repo, "feat/pr-fail")

    origin = tmp_path / "origin.git"
    subprocess.run(["git", "clone", "--bare", str(repo), str(origin)], check=True,
                   capture_output=True)
    git(["remote", "add", "origin", str(origin)], repo)
    git(["push", "-u", "origin", "feat/pr-fail"], repo)

    fake_gh = write_fake_gh(
        tmp_path,
        pr_list_json="[]",
        pr_create_rc=1,
        pr_create_output="gh: error creating PR",
    )
    monkeypatch.setenv("HAL_GH_BIN", str(fake_gh))

    scratchpad = tmp_path / "scratch"
    ctx = make_ctx(scratchpad, working_dir=repo, ship_pr=True)

    result = _ship_to_pr(ctx, None)

    assert result.status == "error", f"Expected error, got {result.status!r}"
    assert result.error_code == "E_SHIP_PR_FAILED", (
        f"Expected E_SHIP_PR_FAILED, got {result.error_code!r}"
    )
    assert result.recoverable is True, "PR create failure must be recoverable=True"


# ─── AC10: org_config overrides for title + body ──────────────────────────────


def test_ac10_org_config_title_body_passed_to_pr_create(tmp_path, monkeypatch):
    """AC10: ship_pr_title/ship_pr_body from org_config appear verbatim in pr create argv."""
    repo = tmp_path / "repo"
    init_git_repo(repo)
    make_feature_branch_with_commit(repo, "feat/config-override")

    origin = tmp_path / "origin.git"
    subprocess.run(["git", "clone", "--bare", str(repo), str(origin)], check=True,
                   capture_output=True)
    git(["remote", "add", "origin", str(origin)], repo)
    git(["push", "-u", "origin", "feat/config-override"], repo)

    fake_gh = write_fake_gh(
        tmp_path,
        pr_list_json="[]",
        pr_create_rc=0,
        pr_create_output="https://github.com/o/r/pull/10",
    )
    monkeypatch.setenv("HAL_GH_BIN", str(fake_gh))

    scratchpad = tmp_path / "scratch"
    ctx = make_ctx(
        scratchpad,
        working_dir=repo,
        ship_pr=True,
        ship_pr_title="My Custom Title",
        ship_pr_body="My Custom Body",
    )

    result = _ship_to_pr(ctx, None)

    assert result.status == "ok", f"Expected ok, got {result.status!r}"

    invocations = gh_argv_log(tmp_path)
    create_calls = [inv for inv in invocations if "create" in inv]
    assert len(create_calls) >= 1, f"Expected pr create in argv log: {invocations}"
    create_argv = create_calls[0]
    # The title and body must appear verbatim in the argv
    assert "My Custom Title" in create_argv, (
        f"Custom title must appear in pr create argv: {create_argv}"
    )
    assert "My Custom Body" in create_argv, (
        f"Custom body must appear in pr create argv: {create_argv}"
    )


# ─── AC11: _parse_pr_url helper ───────────────────────────────────────────────


def test_ac11_parse_pr_url_extracts_last_url_line():
    """AC11 ★: _parse_pr_url returns the URL from last non-empty line; non-URL input → None."""
    url = "https://github.com/o/r/pull/7"
    assert _parse_pr_url(f"Creating pull request...\n{url}\n") == url
    assert _parse_pr_url(f"\n{url}\n") == url
    assert _parse_pr_url(url) == url  # single line, no trailing newline
    assert _parse_pr_url("no url here") is None
    assert _parse_pr_url("") is None
    assert _parse_pr_url("   \n  \n") is None


# ─── AC12: _gh helper ──────────────────────────────────────────────────────────


def test_ac12_gh_helper_returns_rc_stdout_stderr_tuple(tmp_path, monkeypatch):
    """AC12: _gh(["--version"], cwd, HAL_GH_BIN=fake) → (rc, stdout, stderr) tuple; never raises."""
    fake_gh = write_fake_gh(tmp_path)
    monkeypatch.setenv("HAL_GH_BIN", str(fake_gh))

    rc, stdout, stderr = _gh(["--version"], tmp_path)
    assert isinstance(rc, int), f"rc must be int, got {type(rc)}"
    assert isinstance(stdout, str), f"stdout must be str, got {type(stdout)}"
    assert isinstance(stderr, str), f"stderr must be str, got {type(stderr)}"
    assert rc == 0


def test_ac12_gh_helper_returns_127_on_missing_binary(tmp_path, monkeypatch):
    """AC12: FileNotFoundError (bad HAL_GH_BIN) → rc=127, never raises."""
    monkeypatch.setenv("HAL_GH_BIN", str(tmp_path / "nonexistent-gh"))

    rc, stdout, stderr = _gh(["--version"], tmp_path)
    assert rc == 127, f"Expected rc=127 for missing binary, got {rc}"


# ─── AC13: full engine path (WorkflowEngine.execute) ─────────────────────────


def test_ac13_engine_execute_reaches_ship_to_pr_step(tmp_path, monkeypatch):
    """AC13 ★: engine.execute('p8', ctx) with ship_pr=True + fake-gh create-path → pr_url in step_summary.

    Proves §1y host=phase_8_post_deploy_workflow, test-path=WorkflowEngine.execute reaches
    ship_to_pr, not just direct _ship_to_pr call.
    """
    repo = tmp_path / "repo"
    init_git_repo(repo)
    make_feature_branch_with_commit(repo, "feat/engine-path")

    origin = tmp_path / "origin.git"
    subprocess.run(["git", "clone", "--bare", str(repo), str(origin)], check=True,
                   capture_output=True)
    git(["remote", "add", "origin", str(origin)], repo)
    git(["push", "-u", "origin", "feat/engine-path"], repo)

    fake_gh = write_fake_gh(
        tmp_path,
        pr_list_json="[]",
        pr_create_rc=0,
        pr_create_output="https://github.com/o/r/pull/42",
    )
    monkeypatch.setenv("HAL_GH_BIN", str(fake_gh))

    scratchpad = tmp_path / "scratch"
    eng = WorkflowEngine()
    eng.register("p8", phase_8_post_deploy_workflow())

    result, _ = eng.execute(
        "p8",
        make_ctx(scratchpad, working_dir=repo, ship_pr=True),
    )

    # Assert pr_url is accessible via the canonical step_summary path
    step_data = result.data.get("step_summary", {}).get("ship_to_pr")
    assert step_data is not None, (
        "step_summary['ship_to_pr'] must be populated after engine.execute"
    )
    pr_url = step_data.get("pr_url")
    assert pr_url is not None, (
        f"step_summary['ship_to_pr']['pr_url'] must be set; got step_data={step_data!r}"
    )
    assert pr_url == "https://github.com/o/r/pull/42", (
        f"Expected pr_url='https://github.com/o/r/pull/42', got {pr_url!r}"
    )


# ─── ENV1–ENV4: 2754DD92 HAL_BUILD_SHIP_PR env-gate ─────────────────────────


def test_env1_env_var_activates_ship_without_org_config(tmp_path, monkeypatch):
    """ENV1 (2754DD92): HAL_BUILD_SHIP_PR=1, no org ship_pr, feature branch+commit → ships.

    Fails today because _ship_to_pr only checks org_config.get("ship_pr");
    the HAL_BUILD_SHIP_PR env-gate doesn't exist yet.
    """
    monkeypatch.setenv("HAL_BUILD_SHIP_PR", "1")

    repo = tmp_path / "repo"
    init_git_repo(repo)
    make_feature_branch_with_commit(repo, "feat/env-gate")

    origin = tmp_path / "origin.git"
    subprocess.run(["git", "clone", "--bare", str(repo), str(origin)], check=True,
                   capture_output=True)
    git(["remote", "add", "origin", str(origin)], repo)
    git(["push", "-u", "origin", "feat/env-gate"], repo)

    fake_gh = write_fake_gh(
        tmp_path,
        pr_list_json="[]",
        pr_create_rc=0,
        pr_create_output="https://github.com/o/r/pull/7",
    )
    monkeypatch.setenv("HAL_GH_BIN", str(fake_gh))

    scratchpad = tmp_path / "scratch"
    # Deliberately NO ship_pr in org_config — env var must activate the step
    ctx = make_ctx(scratchpad, working_dir=repo)
    assert not (ctx.org_config or {}).get("ship_pr"), "Pre-condition: org_config must NOT have ship_pr"

    result = _ship_to_pr(ctx, None)

    assert result.status == "ok", f"Expected ok, got {result.status!r} / {result.error_code!r}"

    data = result.data
    summary_entry = data.get("step_summary", {}).get("ship_to_pr", data)
    skipped = summary_entry.get("skipped") or data.get("skipped")
    assert skipped != "ship_pr disabled", (
        f"ENV1: step must NOT be skipped with 'ship_pr disabled' when HAL_BUILD_SHIP_PR=1; "
        f"got skipped={skipped!r}"
    )

    invocations = gh_argv_log(tmp_path)
    create_calls = [inv for inv in invocations if "pr" in inv and "create" in inv]
    assert len(create_calls) >= 1, (
        f"ENV1: expected a 'pr create' invocation in gh argv log, got: {invocations!r}"
    )

    pr_url = summary_entry.get("pr_url") or data.get("pr_url")
    assert pr_url == "https://github.com/o/r/pull/7", (
        f"ENV1: expected pr_url='https://github.com/o/r/pull/7', got {pr_url!r}"
    )


def test_env2_unset_env_no_org_skips(tmp_path, monkeypatch):
    """ENV2 (2754DD92): HAL_BUILD_SHIP_PR deleted + no org ship_pr → skip 'ship_pr disabled'.

    Regression guard — must still skip cleanly when neither gate is set.
    Passes today (skip already works); asserts the skip stays correct post-GREEN.
    """
    monkeypatch.delenv("HAL_BUILD_SHIP_PR", raising=False)

    repo = tmp_path / "repo"
    init_git_repo(repo)
    fake_gh = write_fake_gh(tmp_path)
    monkeypatch.setenv("HAL_GH_BIN", str(fake_gh))

    scratchpad = tmp_path / "scratch"
    ctx = make_ctx(scratchpad, working_dir=repo)

    result = _ship_to_pr(ctx, None)

    assert result.status == "ok", f"Expected ok, got {result.status!r}"
    summary_entry = result.data.get("step_summary", {}).get("ship_to_pr", result.data)
    skipped = summary_entry.get("skipped") or result.data.get("skipped")
    assert skipped == "ship_pr disabled", (
        f"ENV2: expected skipped='ship_pr disabled', got {skipped!r}"
    )
    assert gh_argv_log(tmp_path) == [], (
        "ENV2: gh must NOT be invoked when neither env nor org_config enables ship_pr"
    )


def test_env3_env_zero_no_org_skips(tmp_path, monkeypatch):
    """ENV3 (2754DD92): HAL_BUILD_SHIP_PR=0 + no org ship_pr → skip 'ship_pr disabled'.

    Exact-"1"-only rule: any value other than "1" must be treated as unset.
    Fails today only if production accidentally accepted non-"1" values; asserts
    the correct strict-match semantics are preserved post-GREEN.
    """
    monkeypatch.setenv("HAL_BUILD_SHIP_PR", "0")

    repo = tmp_path / "repo"
    init_git_repo(repo)
    fake_gh = write_fake_gh(tmp_path)
    monkeypatch.setenv("HAL_GH_BIN", str(fake_gh))

    scratchpad = tmp_path / "scratch"
    ctx = make_ctx(scratchpad, working_dir=repo)

    result = _ship_to_pr(ctx, None)

    assert result.status == "ok", f"Expected ok, got {result.status!r}"
    summary_entry = result.data.get("step_summary", {}).get("ship_to_pr", result.data)
    skipped = summary_entry.get("skipped") or result.data.get("skipped")
    assert skipped == "ship_pr disabled", (
        f"ENV3: HAL_BUILD_SHIP_PR=0 must NOT activate ship; expected skipped='ship_pr disabled', got {skipped!r}"
    )
    assert gh_argv_log(tmp_path) == [], (
        "ENV3: gh must NOT be invoked when HAL_BUILD_SHIP_PR=0"
    )


def test_env4_env_var_passes_gate_then_skips_on_main(tmp_path, monkeypatch):
    """ENV4 (2754DD92): HAL_BUILD_SHIP_PR=1, no org ship_pr, HEAD on main → skip 'on main / detached'.

    Proves the env-gate is checked first (passes it), then the branch check fires.
    Fails today: without the env gate, the function returns 'ship_pr disabled' before
    ever reaching the branch check — so the skip reason would be wrong.
    """
    monkeypatch.setenv("HAL_BUILD_SHIP_PR", "1")

    repo = tmp_path / "repo"
    init_git_repo(repo)
    # Repo is already on 'main' after init — do NOT create a feature branch
    fake_gh = write_fake_gh(tmp_path)
    monkeypatch.setenv("HAL_GH_BIN", str(fake_gh))

    scratchpad = tmp_path / "scratch"
    # No ship_pr in org_config
    ctx = make_ctx(scratchpad, working_dir=repo)

    result = _ship_to_pr(ctx, None)

    assert result.status == "ok", f"Expected ok, got {result.status!r}"
    summary_entry = result.data.get("step_summary", {}).get("ship_to_pr", result.data)
    skipped = summary_entry.get("skipped") or result.data.get("skipped")
    assert skipped is not None, "ENV4: expected a 'skipped' key in result data"
    assert "main" in (skipped or "").lower() or "detached" in (skipped or "").lower(), (
        f"ENV4: expected skip reason mentioning 'main' or 'detached', got {skipped!r}"
    )
    # Critically: the skip must NOT be the gate-level reason
    assert skipped != "ship_pr disabled", (
        f"ENV4: skip reason must NOT be 'ship_pr disabled' — env gate should have passed; got {skipped!r}"
    )
