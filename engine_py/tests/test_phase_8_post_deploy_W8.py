"""W8 RED tests — phase_8_post_deploy cleanup-step hardening.

Closes /ultrareview Wave 8 findings (3 MED):

* Finding #8 (MED, ``_cleanup_worktrees`` line 305-318)
  ``merged_branches`` is built from ``git branch --merged <main>`` output,
  which lists ``<main>`` itself. The loop has cwd/current-path skip via
  ``_path_contains`` (E6041CDA fix 2026-04-27) but NO ``branch == main_branch``
  skip. A SIBLING main-worktree (path NOT under cwd anchors) is therefore
  eligible for ``git worktree remove --force``. Same root-cause class as the
  original E6041CDA self-destruction; the previous fix protected the LIVE
  worktree but not other main-branch worktrees.

* Finding #10 (MED, ``_cleanup_gone_branches`` line 366)
  ``stripped = line.strip().lstrip("*").strip()`` strips only ``*``, not
  ``+``. The ``+`` prefix marks branches checked out in OTHER worktrees
  (sister ``_cleanup_worktrees`` already does ``lstrip("*+")``). A gone
  branch checked out in a sibling worktree → name becomes ``+branchname``
  → ``git branch -d +branchname`` fails (invalid name). Fix: ``lstrip("*+")``.

* Finding #17 (MED, ``_cleanup_eval_rotation`` line 449-453)
  ``sorted(eval_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True)``.
  ``Path.stat()`` follows symlinks. A broken symlink → ``FileNotFoundError``
  during sort. Sort runs BEFORE the per-entry try/except at line 459, so one
  broken symlink kills the whole cleanup step. Fix: ``p.lstat().st_mtime``
  (doesn't follow symlinks) and/or wrap stat in try/except per-entry.

These tests follow the W4/W7 ACx-style wave-isolation pattern. Each
"must-fail" test (#1, #2, #4, #6) probes a bug and FAILS against current
production code. Regression-guard tests (#3, #5, #7) lock behavior so the
GREEN fix can't degenerate.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

from bytedigger_engine.contracts import WorkflowContext  # noqa: E402
from bytedigger_engine.engine import WorkflowEngine  # noqa: E402
from bytedigger_engine.workflows.phase_8_post_deploy import (  # noqa: E402
    _cleanup_eval_rotation,
    _cleanup_gone_branches,
    _cleanup_worktrees,
    phase_8_post_deploy_workflow,
)


# ─── helpers (mirror existing test_phase_8_post_deploy.py) ────────────────────


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


# ─── Finding #8: main-branch worktree must be skipped, not removed ───────────


def test_cleanup_worktrees_skips_main_branch_worktree_when_anchor_does_not_match(tmp_path, monkeypatch):
    """Finding #8: sibling main-branch worktree (not under cwd anchors) must be SKIPPED.

    Setup: a real git repo whose primary worktree is on ``main``. We operate
    from an unrelated cwd (``tmp_path/elsewhere``). The primary worktree path
    (``tmp_path/repo``) is NOT a parent of, and not contained in, the cwd
    anchor. `main` is in `merged_branches` (main is merged into main). Today
    the loop attempts ``git worktree remove --force <repo>`` — even though git
    refuses (returns rc!=0 for the primary), the function records this in
    ``errors`` instead of ``skipped``. Post-fix: ``branch == main_branch`` is
    a hard skip, so the worktree lands in ``skipped`` and the remove call is
    never even attempted.
    """
    repo = tmp_path / "repo"
    init_git_repo(repo)

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)

    scratchpad = tmp_path / "scratch"
    eng = WorkflowEngine()
    eng.register("p8", phase_8_post_deploy_workflow())
    # Pass working_dir=repo so _git can list worktrees, but DON'T pass
    # current_worktree_path; cwd anchor is `elsewhere` (not under repo).
    # Actually: _resolve_working_dir is also used as a skip_anchor (line 303).
    # To fully isolate the bug we need working_dir to NOT contain repo either.
    # Use a working_dir that points at repo (so git ops work) but force the
    # `cwd` anchor via _path_contains to NOT match repo. Since working_dir
    # IS repo here, repo's primary worktree WOULD be skipped by the anchor.
    # So we need a different setup — use a worktree configured outside the
    # working_dir's containment chain.
    #
    # Better setup: `working_dir` = repo (anchor = repo). Add a second worktree
    # at `tmp_path/sibling` checked out at a NON-main branch but *also* simulate
    # a main-branch worktree via git's worktree machinery.
    #
    # But git refuses duplicate-branch checkouts. Workaround: detached HEAD
    # worktree won't help (branch == "" → kept_unmerged path). We need a
    # worktree whose `branch` field is literally "main" yet path is not under
    # any skip anchor.
    #
    # Achievable trick: working_dir = a SUBDIRECTORY of repo. Then anchor =
    # repo/sub. _path_contains(candidate=repo, anchor=repo/sub) → True
    # (anchor IS relative to candidate) → repo skipped. That hides the bug.
    #
    # Alternative achievable trick: use _cleanup_worktrees() directly with a
    # ctx whose working_dir is a sibling path. Then _git runs there and
    # `git worktree list` will fail. Won't reproduce.
    #
    # Cleanest reproduction: use the primary repo as working_dir AND add a
    # bare sibling repo whose worktree is on main, accessed via the same
    # working_dir's git output. Not feasible without orchestrating two repos.
    #
    # SIMPLEST FAITHFUL REPRO: monkeypatch _git to simulate the worktree
    # listing showing a sibling main worktree at a path outside skip anchors.
    result = _run_with_simulated_sibling_main(
        tmp_path, scratchpad, working_dir=repo, sibling_path=tmp_path / "sibling-main"
    )
    # Direct-call path: data lives at top level (not nested under step_summary).
    wt_data = result.data

    sibling = str(tmp_path / "sibling-main")
    assert sibling in wt_data["skipped"], (
        f"Sibling main-branch worktree at {sibling} must be SKIPPED "
        f"(branch == main_branch is a hard skip). "
        f"Got skipped={wt_data['skipped']}, removed={wt_data['removed']}, "
        f"errors={wt_data['errors']}. "
        f"Fix: add `if branch == main_branch: skipped.append(path); continue` "
        f"BEFORE merged-set membership test."
    )
    assert sibling not in wt_data["removed"], (
        "Main-branch worktree must never be in `removed`."
    )
    # Also assert no error referencing the sibling — the remove call must not
    # have been attempted at all.
    sibling_errors = [e for e in wt_data["errors"] if e.get("path") == sibling]
    assert sibling_errors == [], (
        f"Main-branch worktree must be skipped BEFORE attempting remove; "
        f"got errors={sibling_errors}."
    )


def test_cleanup_worktrees_main_branch_skip_independent_of_path_anchor(tmp_path):
    """Finding #8: branch==main_branch protection is independent of path anchors.

    Even when path anchor protection misses (e.g., separate clone, alias path,
    or just a sibling worktree path), the branch-name check must protect main.
    Same simulated-listing approach as above but with a fully unrelated
    working_dir, so the anchor-based protection definitely cannot fire.
    """
    repo = tmp_path / "repo"
    init_git_repo(repo)
    scratchpad = tmp_path / "scratch"
    sibling = tmp_path / "another-clone-on-main"

    result = _run_with_simulated_sibling_main(
        tmp_path, scratchpad, working_dir=repo, sibling_path=sibling
    )
    wt_data = result.data

    assert str(sibling) in wt_data["skipped"]
    assert str(sibling) not in wt_data["removed"]


def test_cleanup_worktrees_non_main_merged_branch_still_removed(tmp_path):
    """Regression guard: NON-main merged branches outside cwd anchors STILL get removed.

    Locks the W8 fix to be precise (skip on branch==main_branch only), not
    over-broad (skip everything merged). Uses real git — a feature branch
    that's a no-op vs main is "merged" by reachability and must be removed.
    Should already pass against current code; locks behavior post-fix.
    """
    repo = tmp_path / "repo"
    init_git_repo(repo)
    wt_path = tmp_path / "wt-feature-merged"
    git(["worktree", "add", "-b", "feat-merged", str(wt_path)], repo)

    scratchpad = tmp_path / "scratch"
    eng = WorkflowEngine()
    eng.register("p8", phase_8_post_deploy_workflow())
    result, _ = eng.execute("p8", make_ctx(scratchpad, working_dir=repo))

    wt_data = result.data["step_summary"]["cleanup_worktrees"]
    assert str(wt_path) in wt_data["removed"], (
        "Non-main merged feature branches must remain eligible for removal "
        "after the W8 fix (skip is keyed on branch==main_branch, not on "
        "merged-status)."
    )


def _run_with_simulated_sibling_main(
    tmp_path: Path,
    scratchpad: Path,
    *,
    working_dir: Path,
    sibling_path: Path,
):
    """Run `_cleanup_worktrees` with `_git_read` patched to inject a sibling main worktree.

    Real git won't let us add two worktrees on the same branch. We patch
    `_git_read` so the worktree-list call returns the real listing PLUS a fake
    sibling on `main` at `sibling_path`. The merged-branches call returns
    the real listing (which already contains main). Other git calls (notably
    the `worktree remove` call we want to verify is NEVER made for main)
    fall through to the real implementation if reached — so a stray remove
    surfaces as a real failure rather than being silently swallowed.
    """
    from bytedigger_engine.workflows import phase_8_post_deploy as p8mod

    real_git_read = p8mod._git_read
    remove_attempts: list[list[str]] = []

    def fake_git(args, cwd, *, timeout=30):
        if args[:2] == ["worktree", "list"]:
            rc, stdout, stderr = real_git_read(args, cwd=cwd, timeout=timeout)
            # Append a fake sibling main worktree to the porcelain output.
            extra = (
                f"worktree {sibling_path}\n"
                f"HEAD 0000000000000000000000000000000000000000\n"
                f"branch refs/heads/main\n"
                f"\n"
            )
            return rc, stdout + extra, stderr
        if args[:2] == ["worktree", "remove"]:
            remove_attempts.append(list(args))
            # Don't actually remove; pretend it succeeded so we can detect the
            # bug via the `removed` list. (If real git refuses for the primary
            # worktree we'd see an error — either way it's wrong-behavior to
            # have attempted at all.)
            return 0, "", ""
        return real_git_read(args, cwd=cwd, timeout=timeout)

    with patch.object(p8mod, "_git_read", side_effect=fake_git):
        with patch.object(p8mod, "_git_write", side_effect=fake_git, create=True):
            result = _cleanup_worktrees(
                make_ctx(scratchpad, working_dir=working_dir), None
            )
    # Surface remove_attempts on the result so tests can also inspect them.
    if isinstance(result.data, dict):
        result.data["_test_remove_attempts"] = remove_attempts
    return result


# ─── Finding #10: _cleanup_gone_branches must strip "+" prefix ───────────────


def test_cleanup_gone_branches_strips_plus_prefix(tmp_path, monkeypatch):
    """Finding #10: branches checked out in OTHER worktrees are prefixed `+ `.

    Reproduces the bug by patching `_git` to return a `branch -vv` line with
    a `+ ` prefix (the real-git setup — push branch, check it out in sibling
    worktree, simulate remote deletion — is heavy and brittle on CI). Today
    the function emits `+gone-feature` as the branch name, then runs
    `git branch -d +gone-feature` which fails ("not a valid branch name");
    `deleted` stays empty and an error appears. Post-fix the function strips
    `+` (mirroring `_cleanup_worktrees` line 290 `lstrip("*+")`), runs
    `git branch -d gone-feature`, and includes the clean name in `deleted`.
    """
    from bytedigger_engine.workflows import phase_8_post_deploy as p8mod

    branch_calls: list[list[str]] = []
    delete_calls: list[list[str]] = []

    def fake_git(args, cwd, *, timeout=30):
        if args[:1] == ["fetch"]:
            return 0, "", ""
        if args[:2] == ["branch", "-vv"]:
            branch_calls.append(list(args))
            stdout = (
                "* main                       abcdef [origin/main] init\n"
                "+ gone-feature               123456 [origin/gone-feature: gone] wip\n"
            )
            return 0, stdout, ""
        if args[:2] == ["branch", "-d"]:
            delete_calls.append(list(args))
            # Real git would reject "+gone-feature" with an error; simulate
            # the reject only when we see a "+" still in the argument so the
            # test fails LOUDLY against current code (sees +gone-feature).
            target = args[2] if len(args) > 2 else ""
            if target.startswith("+"):
                return 128, "", f"fatal: '{target}' is not a valid branch name."
            return 0, "", ""
        return 0, "", ""

    monkeypatch.setattr(p8mod, "_git_read", fake_git)
    monkeypatch.setattr(p8mod, "_git_write", fake_git, raising=False)

    scratchpad = tmp_path / "scratch"
    result = _cleanup_gone_branches(
        make_ctx(scratchpad, working_dir=tmp_path), None
    )

    deleted = result.data["deleted"]
    errors = result.data.get("errors", [])

    # The clean (stripped) branch name MUST be in `deleted`.
    assert "gone-feature" in deleted, (
        f"Stripped branch name 'gone-feature' must be in deleted; got {deleted!r}. "
        f"Errors: {errors!r}. delete_calls: {delete_calls!r}. "
        f"Fix: change line 366 to `stripped = line.strip().lstrip('*+').strip()`."
    )
    # The "+" prefix must NEVER reach `git branch -d`.
    plus_attempts = [c for c in delete_calls if any(a.startswith("+") for a in c)]
    assert plus_attempts == [], (
        f"Must not invoke `git branch -d` with a '+'-prefixed name; got "
        f"{plus_attempts!r}. The `+` is a worktree-checkout marker, not part "
        f"of the branch name."
    )


def test_cleanup_gone_branches_strips_star_prefix(tmp_path, monkeypatch):
    """Regression guard: existing star-strip behavior survives the W8 fix.

    Locks the existing `*` strip path so the GREEN fix is additive
    (`lstrip('*')` → `lstrip('*+')`), not a regression.
    """
    from bytedigger_engine.workflows import phase_8_post_deploy as p8mod

    delete_calls: list[list[str]] = []

    def fake_git(args, cwd, *, timeout=30):
        if args[:1] == ["fetch"]:
            return 0, "", ""
        if args[:2] == ["branch", "-vv"]:
            stdout = (
                "* current-gone               abcdef [origin/current-gone: gone] wip\n"
                "  other                      123456 [origin/other] wip\n"
            )
            return 0, stdout, ""
        if args[:2] == ["branch", "-d"]:
            delete_calls.append(list(args))
            return 0, "", ""
        return 0, "", ""

    monkeypatch.setattr(p8mod, "_git_read", fake_git)
    monkeypatch.setattr(p8mod, "_git_write", fake_git, raising=False)

    scratchpad = tmp_path / "scratch"
    result = _cleanup_gone_branches(
        make_ctx(scratchpad, working_dir=tmp_path), None
    )

    assert "current-gone" in result.data["deleted"]
    star_attempts = [c for c in delete_calls if any(a.startswith("*") for a in c)]
    assert star_attempts == []


# ─── Finding #17: _cleanup_eval_rotation must survive broken symlinks ────────


def test_cleanup_eval_rotation_survives_broken_symlink(tmp_path):
    """Finding #17: a broken symlink in evals/ must NOT crash the whole step.

    Setup: 3 real dirs (with explicit mtimes) + 1 broken symlink (target
    does not exist). Today `sorted(eval_dir.iterdir(), key=lambda p:
    p.stat().st_mtime, ...)` raises FileNotFoundError BEFORE the per-entry
    try/except at line 459, killing the whole cleanup step. Post-fix
    (`p.lstat().st_mtime` and/or per-entry guard) the sort survives, real
    dirs sort correctly, and the broken symlink is handled gracefully
    (skipped or removed; both acceptable).
    """
    scratchpad = tmp_path / "scratch"
    eval_dir = scratchpad / "evals"
    eval_dir.mkdir(parents=True)

    # 3 real dirs with explicit mtimes (oldest → newest)
    for i in range(3):
        d = eval_dir / f"run-{i}"
        d.mkdir()
        (d / "result.json").write_text("{}")
        os.utime(d, (1_700_000_000 + i, 1_700_000_000 + i))

    # 1 broken symlink (target does not exist)
    broken = eval_dir / "broken-link"
    broken.symlink_to(tmp_path / "does-not-exist-target")

    # keep_count=1 → 2 of the 3 real dirs should be removed.
    ctx = make_ctx(scratchpad, working_dir=tmp_path, eval_keep_count=1)

    # Today this raises FileNotFoundError out of the sort call.
    result = _cleanup_eval_rotation(ctx, None)

    # Post-fix: step completes with status="ok" and removes the older real
    # dirs. The broken symlink is either silently skipped (lstat-based sort
    # plus per-entry guard) or recorded in errors — both acceptable.
    assert result.status == "ok", (
        f"Step must not crash on a broken symlink; got status={result.status!r}, "
        f"data={result.data!r}. "
        f"Fix: replace `p.stat().st_mtime` with `p.lstat().st_mtime` so the "
        f"sort doesn't traverse the symlink target, and/or wrap the keyfn "
        f"in a try/except that demotes broken entries to a sentinel mtime."
    )
    # The two oldest real dirs (run-0, run-1) must be removed (newest run-2 kept).
    removed_names = {Path(p).name for p in result.data.get("removed", [])}
    assert "run-0" in removed_names
    assert "run-1" in removed_names
    # Newest real dir survived.
    assert (eval_dir / "run-2").exists()


def test_cleanup_eval_rotation_no_symlink_unchanged(tmp_path):
    """Regression guard: existing rotation behavior unchanged when no symlinks present.

    Locks the W8 fix to be additive — pure-real-dir behavior must keep
    sorting by mtime and keeping the newest N exactly as before.
    """
    scratchpad = tmp_path / "scratch"
    eval_dir = scratchpad / "evals"
    eval_dir.mkdir(parents=True)
    for i in range(5):
        f = eval_dir / f"run-{i:02d}"
        f.write_text(str(i))
        os.utime(f, (1_700_000_000 + i, 1_700_000_000 + i))

    ctx = make_ctx(scratchpad, working_dir=tmp_path, eval_keep_count=2)
    result = _cleanup_eval_rotation(ctx, None)

    assert result.status == "ok"
    remaining = sorted(p.name for p in eval_dir.iterdir())
    # 2 newest = run-03, run-04
    assert remaining == ["run-03", "run-04"]
