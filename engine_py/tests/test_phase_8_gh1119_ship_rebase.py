"""RED-phase tests — GH1119 (7C02AA88): `_ship_to_pr` rebases onto origin/main before push.

Spec: SHARED/memory/Decisions/2026-07-22_gh1119_ship_pr_rebase_spec.md (AC1–AC12).

House pattern of this suite (§1.7 of the spec): a REAL `git init -b main` tmp repo, a REAL
`git clone --bare` origin, drift staged by committing to the bare origin's `main` from a
second clone, and a fake `gh` shell script on `HAL_GH_BIN` (§1h).  Git is NOT mocked (§1l):
every AC anchors a real production side-effect on a real repository.

§1i (singleton/ordering): all contested state (origin refs, drift commits, remote branch tips)
is PRE-STAGED deterministically before the unit under test is invoked; nothing races on time.

§1q / D1CF5FDF: the two new helpers `_rebase_onto_origin_main` / `_detect_phantom_deletions`
do not exist yet, so they are imported INSIDE the test bodies — the file stays collectable and
fails at runtime (assert / ImportError), never at collect time.
"""
from __future__ import annotations

import os
import stat
import subprocess
from pathlib import Path

# Imports rely on conftest-import-time sys.path setup (§1q / 81F97F3D).
from bytedigger_engine.contracts import WorkflowContext  # noqa: E402
from bytedigger_engine.workflows.phase_8_post_deploy import _ship_to_pr  # noqa: E402

GIT_ENV = {
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
}


# ─── helpers (adapted from test_phase_8_ship_to_pr_98364258.py) ───────────────


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


def git(args: list[str], cwd: Path, *, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        check=check,
        env={**os.environ, **GIT_ENV},
        capture_output=True,
        text=True,
    )


def _isolate(path: Path) -> None:
    git(["config", "commit.gpgsign", "false"], path)
    git(["config", "user.name", "t"], path)
    git(["config", "user.email", "t@t"], path)


def init_git_repo(path: Path) -> None:
    """Init a real git repo with one commit on main, isolated config."""
    path.mkdir(parents=True, exist_ok=True)
    git(["init", "-q", "-b", "main"], path)
    _isolate(path)
    (path / "README").write_text("init\n")
    git(["add", "."], path)
    git(["commit", "-q", "-m", "init"], path)


def commit_file(repo: Path, relpath: str, content: str, msg: str) -> None:
    target = repo / relpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content)
    git(["add", "-A"], repo)
    git(["commit", "-q", "-m", msg], repo)


def make_feature_branch_with_commit(repo: Path, branch: str = "feat/my-feature") -> None:
    """Create a feature branch with one commit ahead of main."""
    git(["checkout", "-q", "-b", branch], repo)
    commit_file(repo, "feature.txt", "feature\n", "feat: add feature")


def clone_bare_origin(tmp_path: Path, repo: Path) -> Path:
    """Real bare origin + wired remote + an initial fetch (so origin/main exists, stale)."""
    origin = tmp_path / "origin.git"
    subprocess.run(
        ["git", "clone", "--bare", str(repo), str(origin)],
        check=True,
        capture_output=True,
        env={**os.environ, **GIT_ENV},
    )
    git(["remote", "add", "origin", str(origin)], repo)
    git(["fetch", "-q", "origin"], repo)
    return origin


def drift_origin_main(
    tmp_path: Path,
    origin: Path,
    *,
    relpath: str = "docs/landed.md",
    content: str = "landed on main\n",
    tag: str = "drift",
) -> str:
    """Land a real commit on the bare origin's `main` (the mid-run merge the worktree missed).

    Returns the new origin/main SHA.
    """
    clone = tmp_path / f"{tag}-clone"
    subprocess.run(
        ["git", "clone", "-q", str(origin), str(clone)],
        check=True,
        capture_output=True,
        env={**os.environ, **GIT_ENV},
    )
    _isolate(clone)
    git(["checkout", "-q", "main"], clone)
    commit_file(clone, relpath, content, f"land: {relpath}")
    git(["push", "-q", "origin", "main"], clone)
    return git(["rev-parse", "HEAD"], clone).stdout.strip()


def write_fake_gh(
    tmp_path: Path,
    *,
    pr_list_json: str = "[]",
    pr_create_rc: int = 0,
    pr_create_output: str = "https://github.com/o/r/pull/7",
) -> Path:
    """Fake `gh`: records argv to gh-argv.log, canned `pr list` / `pr create` responses."""
    log = tmp_path / "gh-argv.log"
    script = tmp_path / "fake-gh"
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


def write_stateful_fake_gh(tmp_path: Path, *, pr_url: str = "https://github.com/o/r/pull/7") -> Path:
    """Fake `gh` that models GitHub state: `pr list` is empty until a `pr create` happened.

    Pre-staged, not raced (§1i): the state transition is driven by the SUT's own `pr create`,
    recorded in a marker file, so the 2nd `_ship_to_pr` call deterministically sees the open PR.
    """
    log = tmp_path / "gh-argv.log"
    marker = tmp_path / "gh-created.marker"
    script = tmp_path / "fake-gh"
    script_body = f"""\
#!/bin/sh
printf '%s\\t' "$@" >> "{log}"
printf '\\n' >> "{log}"
case "$1 $2" in
  "pr list")
    if [ -f "{marker}" ]; then
      printf '%s\\n' '[{{"url": "{pr_url}", "number": 7}}]'
    else
      printf '%s\\n' '[]'
    fi
    exit 0
    ;;
  "pr create")
    : > "{marker}"
    printf '%s\\n' '{pr_url}'
    exit 0
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
    log = tmp_path / "gh-argv.log"
    if not log.exists():
        return []
    return [[a for a in ln.split("\t") if a] for ln in log.read_text().splitlines() if ln.strip()]


def gh_create_calls(tmp_path: Path) -> list[list[str]]:
    return [inv for inv in gh_argv_log(tmp_path) if "pr" in inv and "create" in inv]


def spy_on_git_writes(monkeypatch) -> list[list[str]]:
    """Record every write-git argv while PROXYING to the real implementation.

    Recording only — behaviour is untouched, git is still really executed (§1l).
    """
    from bytedigger_engine.workflows import phase_8_post_deploy as p8

    real = p8._git_write
    calls: list[list[str]] = []

    def spy(argv, *a, **kw):
        calls.append(list(argv))
        return real(argv, *a, **kw)

    monkeypatch.setattr(p8, "_git_write", spy)
    return calls


def push_argvs(calls: list[list[str]]) -> list[list[str]]:
    return [c for c in calls if "push" in c]


def head_sha(repo: Path) -> str:
    return git(["rev-parse", "HEAD"], repo).stdout.strip()


def origin_branch_sha(origin: Path, branch: str) -> str:
    return git(["rev-parse", f"refs/heads/{branch}"], origin).stdout.strip()


def blob_in_head(repo: Path, relpath: str) -> bool:
    return git(["cat-file", "-e", f"HEAD:{relpath}"], repo, check=False).returncode == 0


# ─── AC1 ─────────────────────────────────────────────────────────────────────


def test_ac1_ship_rebases_onto_drifted_origin_main(tmp_path, monkeypatch):
    """AC1: status=="ok"; the worktree HEAD's history contains the drift commit's tree change
    (rebase happened); `git rev-list --count origin/main..HEAD` == the branch's own commit count.
    """
    repo = tmp_path / "repo"
    init_git_repo(repo)
    origin = clone_bare_origin(tmp_path, repo)
    make_feature_branch_with_commit(repo, "feat/rebase-me")
    drift_origin_main(tmp_path, origin)

    monkeypatch.setenv("HAL_GH_BIN", str(write_fake_gh(tmp_path)))
    ctx = make_ctx(tmp_path / "scratch", working_dir=repo, ship_pr=True)

    result = _ship_to_pr(ctx, None)

    assert result.status == "ok", f"Expected ok, got {result.status!r} / {result.error_code!r}"
    assert blob_in_head(repo, "docs/landed.md"), (
        "AC1: after ship, HEAD's history must contain the drift commit's tree change "
        "(docs/landed.md landed on origin/main mid-run) — the branch was never rebased"
    )
    git(["fetch", "-q", "origin"], repo)
    count = git(["rev-list", "--count", "origin/main..HEAD"], repo).stdout.strip()
    assert count == "1", (
        f"AC1: branch must be exactly its own 1 commit ahead of origin/main, got {count!r}"
    )


# ─── AC2 ─────────────────────────────────────────────────────────────────────


def _conflicting_repo(tmp_path: Path) -> tuple[Path, Path]:
    """Branch and origin/main both rewrite README ⇒ the rebase must conflict."""
    repo = tmp_path / "repo"
    init_git_repo(repo)
    origin = clone_bare_origin(tmp_path, repo)
    git(["checkout", "-q", "-b", "feat/conflict"], repo)
    commit_file(repo, "README", "branch side\n", "feat: branch rewrites README")
    drift_origin_main(tmp_path, origin, relpath="README", content="main side\n")
    return repo, origin


def test_ac2_rebase_conflict_returns_non_recoverable_error(tmp_path, monkeypatch):
    """AC2: status=="error", error_code=="E_SHIP_REBASE_CONFLICT", recoverable is False;
    no `gh pr create` in gh-argv.log; `error` non-empty."""
    repo, _origin = _conflicting_repo(tmp_path)
    monkeypatch.setenv("HAL_GH_BIN", str(write_fake_gh(tmp_path)))
    ctx = make_ctx(tmp_path / "scratch", working_dir=repo, ship_pr=True)

    result = _ship_to_pr(ctx, None)

    assert result.status == "error", f"AC2: expected error, got {result.status!r}"
    assert result.error_code == "E_SHIP_REBASE_CONFLICT", (
        f"AC2: expected E_SHIP_REBASE_CONFLICT, got {result.error_code!r}"
    )
    assert result.recoverable is False, "AC2: a rebase conflict must be non-recoverable"
    assert (result.error or "").strip(), "AC2: `error` must be a non-empty human-readable message"
    assert gh_create_calls(tmp_path) == [], (
        f"AC2: no `pr create` may happen on a conflict; got {gh_argv_log(tmp_path)!r}"
    )


# ─── AC3 ─────────────────────────────────────────────────────────────────────


def test_ac3_conflict_leaves_rebase_in_progress(tmp_path, monkeypatch):
    """AC3: a rebase is still in progress (`.git/rebase-merge` or `.git/rebase-apply` exists)
    — the step did NOT silently `--abort`; HEAD is not reset to the pre-rebase branch tip as
    if nothing happened."""
    repo, _origin = _conflicting_repo(tmp_path)
    monkeypatch.setenv("HAL_GH_BIN", str(write_fake_gh(tmp_path)))
    ctx = make_ctx(tmp_path / "scratch", working_dir=repo, ship_pr=True)
    pre_tip = head_sha(repo)

    _ship_to_pr(ctx, None)

    in_progress = (repo / ".git" / "rebase-merge").exists() or (
        repo / ".git" / "rebase-apply"
    ).exists()
    assert in_progress, (
        "AC3: the worktree must be left mid-rebase for a human to finish by hand — "
        "no `rebase --abort`, no silent cleanup"
    )
    # And the mid-rebase state is real: HEAD has moved off the pre-rebase branch tip
    # (rebase replays onto origin/main), i.e. nothing was rolled back as if untouched.
    assert head_sha(repo) != pre_tip, (
        "AC3: HEAD must not be reset back to the pre-rebase branch tip as if nothing happened"
    )


# ─── AC4 ─────────────────────────────────────────────────────────────────────


def test_ac4_landed_file_is_not_phantom_deleted_in_pr_diff(tmp_path, monkeypatch):
    """AC4: status=="ok" and `git diff --stat origin/main..HEAD` (two-dot) contains no deletion
    of docs/landed.md (regression on the literal ppba #1385 defect)."""
    repo = tmp_path / "repo"
    init_git_repo(repo)
    origin = clone_bare_origin(tmp_path, repo)
    make_feature_branch_with_commit(repo, "feat/ppba-regression")
    drift_origin_main(tmp_path, origin, relpath="docs/landed.md", content="163 lines\n")

    monkeypatch.setenv("HAL_GH_BIN", str(write_fake_gh(tmp_path)))
    ctx = make_ctx(tmp_path / "scratch", working_dir=repo, ship_pr=True)

    result = _ship_to_pr(ctx, None)

    assert result.status == "ok", f"AC4: expected ok, got {result.status!r} / {result.error_code!r}"
    git(["fetch", "-q", "origin"], repo)
    deletions = git(
        ["diff", "--diff-filter=D", "--name-only", "origin/main..HEAD"], repo
    ).stdout.split()
    assert "docs/landed.md" not in deletions, (
        f"AC4: the PR diff must not delete a file that landed on main mid-run; got {deletions!r}"
    )
    stat_out = git(["diff", "--stat", "origin/main..HEAD"], repo).stdout
    assert "docs/landed.md" not in stat_out, (
        f"AC4: docs/landed.md must not appear in the PR diffstat at all; got:\n{stat_out}"
    )


# ─── AC5 / AC6 — phantom deletion ────────────────────────────────────────────


def _stale_branch_vs_drifted_origin(tmp_path: Path, branch: str) -> Path:
    """The literal ppba #1385 shape — no rename contrivance, plain merged-mid-build drift.

    main@T0 (README) → bare origin → branch off T0 with one own commit (feature.txt);
    `docs/landed.md` then lands on the origin's `main`.  The test fetches so that the local
    `origin/main` ref really points at the drifted tip, and the branch is left **un-rebased**.

    ⇒ two-dot `origin/main..HEAD` reports `docs/landed.md` as deleted, while the branch's own
      commits deleted nothing ⇒ phantom == {"docs/landed.md"} (spec §1.2).
    """
    repo = tmp_path / "repo"
    init_git_repo(repo)
    origin = clone_bare_origin(tmp_path, repo)
    make_feature_branch_with_commit(repo, branch)
    drift_origin_main(tmp_path, origin, relpath="docs/landed.md", content="landed mid-build\n")
    # Pre-stage the contested ref deterministically (§1i): local origin/main == drifted tip,
    # HEAD still stale — exactly the state a build sits in when the rebase did not happen.
    git(["fetch", "-q", "origin"], repo)
    assert not blob_in_head(repo, "docs/landed.md"), "pre-condition: HEAD must be stale"
    return repo


def test_ac5_detect_phantom_deletions_returns_the_drifted_path(tmp_path):
    """AC5: `_detect_phantom_deletions(cwd, main)` returns exactly ["<that drifted path>"]."""
    from bytedigger_engine.workflows.phase_8_post_deploy import _detect_phantom_deletions  # deferred: absent pre-GREEN

    repo = _stale_branch_vs_drifted_origin(tmp_path, "feat/phantom")

    assert _detect_phantom_deletions(repo, "main") == ["docs/landed.md"]


def test_ac6_phantom_deletion_blocks_the_ship(tmp_path, monkeypatch):
    """AC6: status=="error", error_code=="E_SHIP_PHANTOM_DELETION", recoverable is False,
    `error` names the path; no `pr create`."""
    repo = _stale_branch_vs_drifted_origin(tmp_path, "feat/phantom-step")
    # Suppress the rebase without discarding the already-fetched origin/main ref: the remote
    # URL is repointed at a dead path, so §1.1 step 3's fetch fails ⇒ outcome "skipped", while
    # the local origin/main still carries the drift ⇒ the phantom belt is the second line of
    # defence and must fire BEFORE the push (§1.3 call-site order).
    git(["remote", "set-url", "origin", "/nonexistent/path/to/nowhere.git"], repo)
    monkeypatch.setenv("HAL_GH_BIN", str(write_fake_gh(tmp_path)))
    ctx = make_ctx(tmp_path / "scratch", working_dir=repo, ship_pr=True)

    result = _ship_to_pr(ctx, None)

    assert result.status == "error", f"AC6: expected error, got {result.status!r}"
    assert result.error_code == "E_SHIP_PHANTOM_DELETION", (
        f"AC6: expected E_SHIP_PHANTOM_DELETION, got {result.error_code!r}"
    )
    assert result.recoverable is False, "AC6: a phantom deletion must be non-recoverable"
    assert "docs/landed.md" in (result.error or ""), (
        f"AC6: `error` must name the phantom-deleted path, got {result.error!r}"
    )
    assert gh_create_calls(tmp_path) == [], (
        f"AC6: no `pr create` may happen on a phantom deletion; got {gh_argv_log(tmp_path)!r}"
    )


# ─── AC7 ─────────────────────────────────────────────────────────────────────


def test_ac7_legitimate_own_deletion_is_not_a_false_positive(tmp_path, monkeypatch):
    """AC7: `_detect_phantom_deletions` returns [] (no false positive) and the step ships ok."""
    from bytedigger_engine.workflows.phase_8_post_deploy import _detect_phantom_deletions  # deferred: absent pre-GREEN

    repo = tmp_path / "repo"
    init_git_repo(repo)
    commit_file(repo, "docs/doomed.md", "doomed\n", "add doomed doc")
    origin = clone_bare_origin(tmp_path, repo)
    assert origin_branch_sha(origin, "main"), "pre-staged bare origin must carry refs/heads/main"
    git(["checkout", "-q", "-b", "feat/own-deletion"], repo)
    git(["rm", "-q", "docs/doomed.md"], repo)
    git(["commit", "-q", "-m", "chore: delete doomed doc"], repo)

    assert _detect_phantom_deletions(repo, "main") == [], (
        "AC7: a deletion made by the branch's own commit is not phantom"
    )

    monkeypatch.setenv("HAL_GH_BIN", str(write_fake_gh(tmp_path)))
    ctx = make_ctx(tmp_path / "scratch", working_dir=repo, ship_pr=True)
    result = _ship_to_pr(ctx, None)
    assert result.status == "ok", (
        f"AC7: legitimate deletion must still ship, got {result.status!r} / {result.error_code!r}"
    )


# ─── AC8 ─────────────────────────────────────────────────────────────────────


def test_ac8_second_call_is_idempotent_and_rebase_is_noop(tmp_path, monkeypatch):
    """AC8: status=="ok", idempotent_hit is True, exactly one `pr create` across both calls in
    gh-argv.log; the 2nd call's rebase is a no-op (origin branch SHA unchanged between calls)."""
    repo = tmp_path / "repo"
    init_git_repo(repo)
    origin = clone_bare_origin(tmp_path, repo)
    make_feature_branch_with_commit(repo, "feat/idempotent")
    drift_origin_main(tmp_path, origin)

    monkeypatch.setenv("HAL_GH_BIN", str(write_stateful_fake_gh(tmp_path)))
    ctx = make_ctx(tmp_path / "scratch", working_dir=repo, ship_pr=True)

    first = _ship_to_pr(ctx, None)
    assert first.status == "ok", f"AC8: 1st call must ship ok, got {first.error_code!r}"
    assert blob_in_head(repo, "docs/landed.md"), (
        "AC8 pre-condition: the 1st call must have rebased the drift into HEAD"
    )
    sha_after_first = origin_branch_sha(origin, "feat/idempotent")

    second = _ship_to_pr(ctx, None)

    assert second.status == "ok", f"AC8: 2nd call must be ok, got {second.error_code!r}"
    entry = second.data.get("step_summary", {}).get("ship_to_pr", second.data)
    hit = entry.get("idempotent_hit")
    if hit is None:
        hit = second.data.get("idempotent_hit")
    assert hit is True, f"AC8: 2nd call must be an idempotent hit, got {hit!r}"
    assert len(gh_create_calls(tmp_path)) == 1, (
        f"AC8: exactly one `pr create` across both calls, got {gh_create_calls(tmp_path)!r}"
    )
    assert origin_branch_sha(origin, "feat/idempotent") == sha_after_first, (
        "AC8: the 2nd call's rebase must be a no-op — origin branch SHA must not move"
    )


# ─── AC9 ─────────────────────────────────────────────────────────────────────


def test_ac9_no_drift_pushes_without_force_with_lease(tmp_path, monkeypatch):
    """AC9: status=="ok", and the push argv recorded is exactly `push -u origin <branch>`
    — no `--force-with-lease`."""
    repo = tmp_path / "repo"
    init_git_repo(repo)
    clone_bare_origin(tmp_path, repo)  # origin/main == local main, no drift staged
    make_feature_branch_with_commit(repo, "feat/no-drift")

    monkeypatch.setenv("HAL_GH_BIN", str(write_fake_gh(tmp_path)))
    calls = spy_on_git_writes(monkeypatch)
    ctx = make_ctx(tmp_path / "scratch", working_dir=repo, ship_pr=True)

    result = _ship_to_pr(ctx, None)

    assert result.status == "ok", f"AC9: expected ok, got {result.status!r} / {result.error_code!r}"
    pushes = push_argvs(calls)
    assert pushes == [["push", "-u", "origin", "feat/no-drift"]], (
        f"AC9: on the no-drift path the push argv must stay byte-identical, got {pushes!r}"
    )


# ─── AC10 ────────────────────────────────────────────────────────────────────


def test_ac10_drifted_and_already_pushed_uses_force_with_lease(tmp_path, monkeypatch):
    """AC10: push argv is `push --force-with-lease -u origin <branch>`; origin's branch ref
    ends at the post-rebase HEAD."""
    repo = tmp_path / "repo"
    init_git_repo(repo)
    origin = clone_bare_origin(tmp_path, repo)
    make_feature_branch_with_commit(repo, "feat/already-pushed")
    # Pre-stage the contested remote ref (§1i): the branch already exists on origin.
    git(["push", "-q", "-u", "origin", "feat/already-pushed"], repo)
    drift_origin_main(tmp_path, origin)
    pre_rebase_head = head_sha(repo)

    monkeypatch.setenv("HAL_GH_BIN", str(write_fake_gh(tmp_path)))
    calls = spy_on_git_writes(monkeypatch)
    ctx = make_ctx(tmp_path / "scratch", working_dir=repo, ship_pr=True)

    result = _ship_to_pr(ctx, None)

    assert result.status == "ok", f"AC10: expected ok, got {result.status!r} / {result.error_code!r}"
    pushes = push_argvs(calls)
    assert pushes == [
        ["push", "--force-with-lease", "-u", "origin", "feat/already-pushed"]
    ], f"AC10: a rebased, already-pushed branch needs --force-with-lease, got {pushes!r}"
    post_rebase_head = head_sha(repo)
    assert post_rebase_head != pre_rebase_head, "AC10: the rebase must have rewritten HEAD"
    assert origin_branch_sha(origin, "feat/already-pushed") == post_rebase_head, (
        "AC10: origin's branch ref must end at the post-rebase HEAD"
    )


# ─── AC11 ────────────────────────────────────────────────────────────────────


def test_ac11_no_origin_remote_skips_cleanly(tmp_path):
    """AC11: returns outcome == "skipped"; no exception; `_detect_phantom_deletions` returns []."""
    from bytedigger_engine.workflows.phase_8_post_deploy import (  # deferred: absent pre-GREEN
        _detect_phantom_deletions,
        _rebase_onto_origin_main,
    )

    repo = tmp_path / "repo"
    init_git_repo(repo)
    make_feature_branch_with_commit(repo, "feat/no-remote")

    outcome, _detail, _head = _rebase_onto_origin_main(repo, "main")

    assert outcome == "skipped", f"AC11: no origin remote ⇒ skipped, got {outcome!r}"
    assert _detect_phantom_deletions(repo, "main") == [], (
        "AC11: no-remote fixtures must stay untouched by the phantom belt"
    )


# ─── AC12 ────────────────────────────────────────────────────────────────────


def test_ac12_unreachable_origin_defers_to_push_failed(tmp_path, monkeypatch):
    """AC12: error_code == "E_SHIP_PUSH_FAILED" (fetch failure defers, §1.4) — not a
    rebase/phantom code."""
    repo = tmp_path / "repo"
    init_git_repo(repo)
    make_feature_branch_with_commit(repo, "feat/unreachable")
    git(["remote", "add", "origin", "/nonexistent/path/to/nowhere.git"], repo)

    monkeypatch.setenv("HAL_GH_BIN", str(write_fake_gh(tmp_path)))
    ctx = make_ctx(tmp_path / "scratch", working_dir=repo, ship_pr=True)

    result = _ship_to_pr(ctx, None)

    assert result.status == "error", f"AC12: expected error, got {result.status!r}"
    assert result.error_code == "E_SHIP_PUSH_FAILED", (
        f"AC12: an unreachable origin must defer to E_SHIP_PUSH_FAILED, got {result.error_code!r}"
    )
    assert result.recoverable is True, "AC12: push failure stays recoverable=True"
