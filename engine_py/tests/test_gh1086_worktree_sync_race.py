"""GH1086 RED tests — worktree-sync race hardening (agreement 6604CC4B).

Covers AC1-AC9 from spec §3
(SHARED/memory/Decisions/2026-07-20_6604CC4B_gh1086_worktree_sync_race_spec.md):

M1 — new helper `_detect_head_drift(git_cwd, frozen_sha, git_port) -> dict` in
     phase_5_implement.py, wired into the existing terminal branch at :1716-1726.
M2 — ownership guard in `_cleanup_scratchpad_subdirs` (phase_8_post_deploy.py).

Per §1q-extension (D1CF5FDF): `_detect_head_drift` does not exist yet, so all
references to it are deferred into test bodies via getattr() — the module
imports cleanly at collection time, and each test FAILS at assert-time.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import uuid
from pathlib import Path

import pytest

# Conftest singleton (§1q) already put engine_py root + workflows on sys.path.
import phase_5_implement as p5_mod
import phase_8_post_deploy as p8_mod
from contracts import StepResult, WorkflowContext


# ─── shared git-repo helpers (mirrors test_8D162396 idiom) ────────────────────


def _make_repo(base: Path) -> Path:
    """Create a minimal git repo under base with one initial commit.

    os.path.realpath avoids /var/folders symlink cwd mismatches (§1j).
    """
    repo = Path(os.path.realpath(str(base / "repo")))
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "tester"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)
    placeholder = repo / "src" / "placeholder.py"
    placeholder.parent.mkdir(parents=True, exist_ok=True)
    unique = uuid.uuid4().hex
    placeholder.write_text(f"# placeholder {repo} {unique}\n")
    subprocess.run(["git", "add", "src/placeholder.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", f"init {repo} {unique}"], cwd=repo, check=True)
    return repo


def _head_sha(repo: Path) -> str:
    r = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=repo, check=True
    )
    return r.stdout.strip()


def _commit_file(repo: Path, relpath: str, body: str, msg: str = "c") -> str:
    p = repo / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    subprocess.run(["git", "add", relpath], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", msg], cwd=repo, check=True)
    return _head_sha(repo)


def _make_scratchpad() -> Path:
    return Path(os.path.realpath(tempfile.mkdtemp()))


def _make_ctx(scratchpad: Path, git_cwd: str | None = None, session_id: str = "test-session"):
    org: dict = {"scratchpad_dir": str(scratchpad)}
    if git_cwd is not None:
        org["git_cwd"] = git_cwd
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question="worktree sync race",
        session_id=session_id,
        persona="hal",
        framework=None,
        domain=None,
    )


def _prev_for_commit(scratchpad: Path, cycle: int = 1):
    return StepResult(
        status="ok",
        data={
            "red_log_path": str(scratchpad / "tests/build-red-output.log"),
            "spec_path": str(scratchpad / "specs/build-spec.md"),
            "cycle": cycle,
            "red_test_paths": None,
        },
        duration_ms=0,
        step_name="write_red_artifact",
    )


class _FakeGitResult:
    """Minimal stand-in matching git_port.GitResult's shape."""

    def __init__(self, returncode: int, stdout: str = "", stderr: str = ""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        self.timed_out = returncode == 124


class _TimeoutGitPort:
    """Fake git_port whose git_read always times out (rc=124) — AC4."""

    def git_read(self, args, *, cwd=None, timeout=None, dir_=None):
        return _FakeGitResult(124, "", "")


# ─── AC1: no drift — frozen sha is HEAD~1 reachable ────────────────────────────


def test_ac1_no_drift_when_frozen_sha_is_ancestor(tmp_path):
    """AC1: frozen sha ancestor of HEAD, RED files absent, diff empty →
    _detect_head_drift returns drift=False; E_RED_NO_MARKER (unchanged) fires.

    Fails pre-GREEN: _detect_head_drift does not exist on phase_5_implement.
    """
    _detect_head_drift = getattr(p5_mod, "_detect_head_drift", None)
    assert _detect_head_drift is not None, (
        "_detect_head_drift not found on phase_5_implement — M1 helper missing"
    )

    from lib import git_port  # noqa: PLC0415

    repo = _make_repo(tmp_path)
    frozen_sha = _head_sha(repo)
    _commit_file(repo, "src/other.py", "# more\n", "second commit")

    result = _detect_head_drift(str(repo), frozen_sha, git_port)

    assert result["drift"] is False, f"Expected no drift (ancestor holds), got {result!r}"
    assert result["reason"] == "", f"Expected empty reason on no-drift, got {result!r}"


# ─── AC2: drift — frozen sha unreachable (unrelated root) ─────────────────────


def test_ac2_drift_when_frozen_sha_missing(tmp_path):
    """AC2: frozen sha not present as an object at all → drift=True, reason
    'missing', message references both SHAs (via the wired terminal branch).

    Fails pre-GREEN: _detect_head_drift missing AND terminal branch at :1716
    still always returns E_RED_NO_MARKER regardless of drift.
    """
    _detect_head_drift = getattr(p5_mod, "_detect_head_drift", None)
    assert _detect_head_drift is not None, (
        "_detect_head_drift not found on phase_5_implement — M1 helper missing"
    )

    from lib import git_port  # noqa: PLC0415

    # Unrelated repo root — frozen sha comes from a *different* repo entirely,
    # so it is not an object in this repo (missing, not just non-ancestor).
    other_repo = _make_repo(tmp_path / "other")
    frozen_sha = _head_sha(other_repo)
    repo = _make_repo(tmp_path / "main")

    result = _detect_head_drift(str(repo), frozen_sha, git_port)

    assert result["drift"] is True, f"Expected drift=True (frozen sha unreachable), got {result!r}"
    assert result["reason"] == "missing", f"Expected reason='missing', got {result!r}"

    # Integration: the terminal branch at :1716-1726 must surface the NEW code.
    scratchpad = _make_scratchpad()
    ref_path = scratchpad / p5_mod.PRE_RED_REF_RELPATH
    ref_path.parent.mkdir(parents=True, exist_ok=True)
    ref_path.write_text(frozen_sha)
    ctx = _make_ctx(scratchpad, git_cwd=str(repo))
    prev = _prev_for_commit(scratchpad, cycle=1)

    step_result = p5_mod._commit_red_tests(ctx, prev)

    assert step_result.error_code == "E_WORKTREE_HEAD_MOVED", (
        f"Expected E_WORKTREE_HEAD_MOVED on drift, got {step_result.error_code!r} "
        f"(error={step_result.error!r})"
    )
    err_msg = step_result.error or ""
    assert frozen_sha in err_msg and _head_sha(repo) in err_msg, (
        f"E_WORKTREE_HEAD_MOVED message must contain both SHAs, got: {err_msg!r}"
    )


# ─── AC3: drift — frozen sha object present but not ancestor (branch swap) ────


def test_ac3_drift_when_frozen_sha_not_ancestor(tmp_path):
    """AC3: frozen sha exists as a commit object but HEAD is on a diverged
    branch (not a descendant) → drift=True, reason 'not_ancestor'.

    Fails pre-GREEN: _detect_head_drift missing.
    """
    _detect_head_drift = getattr(p5_mod, "_detect_head_drift", None)
    assert _detect_head_drift is not None, (
        "_detect_head_drift not found on phase_5_implement — M1 helper missing"
    )

    from lib import git_port  # noqa: PLC0415

    repo = _make_repo(tmp_path)
    frozen_sha = _head_sha(repo)  # base commit, becomes the diverged-away sha

    # Branch off, commit, then swap HEAD to a sibling branch that also
    # descends from the SAME base but diverges — frozen_sha itself as
    # "not_ancestor" requires frozen_sha to be a *later* commit no longer on
    # the current branch's ancestry line. Build: base -> A (frozen) -> B(main)
    # then checkout a fresh orphan-ish branch from base only, so frozen (A)
    # is a real object but not an ancestor of new HEAD.
    a_sha = _commit_file(repo, "src/a.py", "# a\n", "commit A")
    frozen_sha = a_sha
    subprocess.run(["git", "checkout", "-q", "-b", "swap", "main~1"], cwd=repo, check=True)
    _commit_file(repo, "src/c.py", "# c\n", "commit C on swap branch")

    result = _detect_head_drift(str(repo), frozen_sha, git_port)

    assert result["drift"] is True, f"Expected drift=True (not ancestor), got {result!r}"
    assert result["reason"] == "not_ancestor", f"Expected reason='not_ancestor', got {result!r}"


# ─── AC4: git_read timeout → fail-safe drift=False ─────────────────────────────


def test_ac4_timeout_fails_safe_to_no_drift(tmp_path):
    """AC4: git_read returns rc=124 (timeout) → drift=False (fail-safe to old
    behavior), NOT drift=True.

    Fails pre-GREEN: _detect_head_drift missing.
    """
    _detect_head_drift = getattr(p5_mod, "_detect_head_drift", None)
    assert _detect_head_drift is not None, (
        "_detect_head_drift not found on phase_5_implement — M1 helper missing"
    )

    repo = _make_repo(tmp_path)
    frozen_sha = _head_sha(repo)
    fake_port = _TimeoutGitPort()

    result = _detect_head_drift(str(repo), frozen_sha, fake_port)

    assert result["drift"] is False, (
        f"git_read timeout (rc=124) must fail-safe to drift=False, got {result!r}"
    )


# ─── AC5: §1ab same-cycle retry re-entry — idempotent outcome ─────────────────


def test_ac5_same_cycle_retry_reentry_idempotent_drift_outcome(tmp_path):
    """AC5: same-cycle retry re-enters commit_red_tests with the SAME drift
    state pre-staged (write-once pre-red-ref untouched) → 2nd entry produces
    the identical E_WORKTREE_HEAD_MOVED outcome (idempotent, no new sentinel).

    Fails pre-GREEN: terminal branch never returns E_WORKTREE_HEAD_MOVED.
    """
    from lib import git_port  # noqa: PLC0415 (imported for parity; not directly asserted)

    other_repo = _make_repo(tmp_path / "other")
    frozen_sha = _head_sha(other_repo)
    repo = _make_repo(tmp_path / "main")

    scratchpad = _make_scratchpad()
    ref_path = scratchpad / p5_mod.PRE_RED_REF_RELPATH
    ref_path.parent.mkdir(parents=True, exist_ok=True)
    ref_path.write_text(frozen_sha)

    ctx = _make_ctx(scratchpad, git_cwd=str(repo))
    prev = _prev_for_commit(scratchpad, cycle=1)

    first = p5_mod._commit_red_tests(ctx, prev)
    # Write-once ref must be unchanged before re-entry.
    assert ref_path.read_text().strip() == frozen_sha, "pre-red-ref must remain write-once"

    prev_retry = _prev_for_commit(scratchpad, cycle=1)  # same-cycle retry, not cycle+1
    second = p5_mod._commit_red_tests(ctx, prev_retry)

    assert first.error_code == "E_WORKTREE_HEAD_MOVED", (
        f"1st entry expected E_WORKTREE_HEAD_MOVED, got {first.error_code!r}"
    )
    assert second.error_code == first.error_code, (
        f"2nd (same-cycle retry) entry must reproduce identical outcome; "
        f"got {second.error_code!r} vs 1st {first.error_code!r}"
    )
    assert ref_path.read_text().strip() == frozen_sha, (
        "pre-red-ref must remain untouched after same-cycle retry re-entry"
    )


# ─── AC6: registry membership + dbos_setup non-membership ─────────────────────


def test_ac6_error_code_registered_and_not_evict_on_retry(tmp_path):
    """AC6: E_WORKTREE_HEAD_MOVED registered in error_codes.ERROR_CODES, and
    explicitly NOT a member of dbos_setup._EVICT_ON_RETRY_CODES (external
    interference classifies as failed_terminal, not auto-evict-retry).

    Fails pre-GREEN: code not yet registered.
    """
    import error_codes  # noqa: PLC0415
    import lib.dbos_setup as dbos_mod  # noqa: PLC0415

    assert "E_WORKTREE_HEAD_MOVED" in error_codes.ERROR_CODES, (
        "E_WORKTREE_HEAD_MOVED must be registered in error_codes.ERROR_CODES"
    )
    evict_codes = getattr(dbos_mod, "_EVICT_ON_RETRY_CODES", frozenset())
    assert "E_WORKTREE_HEAD_MOVED" not in evict_codes, (
        "E_WORKTREE_HEAD_MOVED must NOT be in dbos_setup._EVICT_ON_RETRY_CODES "
        "(external HEAD interference needs re-base, not auto-evict-retry)"
    )


# ─── AC7: phase_8 cleanup — foreign session skips rmtree ──────────────────────


def test_ac7_cleanup_skips_foreign_session_scratchpad(tmp_path):
    """AC7: scratchpad dir named 'forge-A', ctx.session_id='forge-B' →
    rmtree must be skipped entirely; dirs remain on disk; result.data carries
    skipped_foreign=True + a scratchpad_ownership_mismatch event is emitted.

    Fails pre-GREEN: _cleanup_scratchpad_subdirs has no ownership check and
    unconditionally rmtrees every populated subdir.
    """
    scratchpad = Path(os.path.realpath(str(tmp_path / "forge-A")))
    scratchpad.mkdir(parents=True)
    populated = []
    for sub in p8_mod.SCRATCHPAD_SUBDIRS_TO_CLEAN:
        d = scratchpad / sub
        d.mkdir(parents=True)
        marker = d / "keepme.txt"
        marker.write_text("keep")
        populated.append(marker)

    ctx = _make_ctx(scratchpad, session_id="forge-B")
    prev = StepResult(status="ok", data={}, duration_ms=0, step_name="prior_step")

    result = p8_mod._cleanup_scratchpad_subdirs(ctx, prev)

    for marker in populated:
        assert marker.exists(), (
            f"{marker} was deleted despite foreign session_id mismatch (forge-A vs forge-B) — "
            "M2 ownership guard missing"
        )
    assert result.data.get("skipped_foreign") is True, (
        f"Expected skipped_foreign=True in result.data, got {result.data!r}"
    )
    assert result.data.get("removed") == [], (
        f"Expected removed=[] when cleanup is skipped, got {result.data.get('removed')!r}"
    )


# ─── AC8: matching session — existing removal behavior preserved ──────────────


def test_ac8_cleanup_removes_when_session_matches(tmp_path):
    """AC8: scratchpad name == ctx.session_id → existing rmtree behavior
    proceeds; post-deploy/ preserved (unaffected by M2).

    This is a correctness guard — the CURRENT code already removes matching
    dirs (no ownership check exists yet, so it always removes). It should
    remain green after GREEN adds the M2 guard IF the guard is name-aware.
    """
    sid = "forge-match"
    scratchpad = Path(os.path.realpath(str(tmp_path / sid)))
    scratchpad.mkdir(parents=True)
    for sub in p8_mod.SCRATCHPAD_SUBDIRS_TO_CLEAN:
        d = scratchpad / sub
        d.mkdir(parents=True)
        (d / "f.txt").write_text("x")
    post_deploy = scratchpad / "post-deploy"
    post_deploy.mkdir(parents=True)
    (post_deploy / "report.md").write_text("report")

    ctx = _make_ctx(scratchpad, session_id=sid)
    prev = StepResult(status="ok", data={}, duration_ms=0, step_name="prior_step")

    result = p8_mod._cleanup_scratchpad_subdirs(ctx, prev)

    for sub in p8_mod.SCRATCHPAD_SUBDIRS_TO_CLEAN:
        assert not (scratchpad / sub).exists(), (
            f"{sub} should have been removed when session_id matches scratchpad name"
        )
    assert post_deploy.exists(), "post-deploy/ must be preserved"
    assert not result.data.get("skipped_foreign"), (
        f"skipped_foreign must be falsy on a matching-session cleanup, got {result.data!r}"
    )


# ─── AC9: session_id=None — current behavior (defensive parity) ───────────────


def test_ac9_cleanup_proceeds_when_session_id_is_none(tmp_path):
    """AC9: ctx.session_id=None → current rmtree behavior proceeds (defensive
    parity with the engine boundary requiring session_id); no mismatch event.

    This is a correctness guard for the "sid falsy" branch of M2 — should
    stay green both pre- and post-GREEN since it exercises the fallback path
    to unguarded behavior explicitly carved out by the design.
    """
    scratchpad = Path(os.path.realpath(str(tmp_path / "no-sid-scratch")))
    scratchpad.mkdir(parents=True)
    for sub in p8_mod.SCRATCHPAD_SUBDIRS_TO_CLEAN:
        d = scratchpad / sub
        d.mkdir(parents=True)
        (d / "f.txt").write_text("x")

    ctx = _make_ctx(scratchpad, session_id=None)
    prev = StepResult(status="ok", data={}, duration_ms=0, step_name="prior_step")

    result = p8_mod._cleanup_scratchpad_subdirs(ctx, prev)

    for sub in p8_mod.SCRATCHPAD_SUBDIRS_TO_CLEAN:
        assert not (scratchpad / sub).exists(), (
            f"{sub} should have been removed when ctx.session_id is None (defensive parity)"
        )
    assert not result.data.get("skipped_foreign"), (
        f"skipped_foreign must be falsy when session_id is None, got {result.data!r}"
    )


# ─── AC14: empty/whitespace frozen ref → fail-safe to E_RED_NO_MARKER ─────────


def test_ac14_empty_frozen_ref_fails_safe_to_no_marker(tmp_path):
    """AC14: pre-red-ref.txt contains only whitespace, RED files absent, diff
    empty → must yield E_RED_NO_MARKER, NOT E_WORKTREE_HEAD_MOVED (§2 M1:
    frozen sha empty/whitespace after strip -> drift=False fail-safe; no
    spurious 'missing' reason).

    Robustness note (per orchestrator instructions): this is a terminal-branch
    integration assertion, not a direct _detect_head_drift unit call. Today,
    pre-GREEN, _detect_head_drift is not wired into the terminal branch at all
    (only the git-diff-empty-and-no-disk-files path exists), so the branch
    already always returns E_RED_NO_MARKER regardless of the empty ref content
    -- this test currently documents CURRENT behavior as a guard, and stays
    green through GREEN only if M1 correctly fail-safes on empty/whitespace
    refs (empty ref -> _resolve_frozen_pre_red_sha falls back to git rev-parse
    HEAD of git_cwd, so frozen_sha == current HEAD -> no drift by construction
    once M1 is wired). It is NOT a false-red: it pins the required fail-safe
    outcome that GREEN's M1 wiring must preserve, and a naive M1 wiring that
    treats an empty frozen_sha as "missing" (spurious drift) would flip this
    red once _detect_head_drift lands wired to the terminal branch.
    """
    repo = _make_repo(tmp_path)

    scratchpad = _make_scratchpad()
    ref_path = scratchpad / p5_mod.PRE_RED_REF_RELPATH
    ref_path.parent.mkdir(parents=True, exist_ok=True)
    ref_path.write_text("   \n\t  \n")  # whitespace-only

    ctx = _make_ctx(scratchpad, git_cwd=str(repo))
    prev = _prev_for_commit(scratchpad, cycle=1)

    step_result = p5_mod._commit_red_tests(ctx, prev)

    assert step_result.error_code == "E_RED_NO_MARKER", (
        f"Expected E_RED_NO_MARKER (fail-safe on empty/whitespace frozen ref), "
        f"got {step_result.error_code!r} (error={step_result.error!r})"
    )
    assert step_result.error_code != "E_WORKTREE_HEAD_MOVED", (
        "Empty/whitespace frozen ref must NOT be classified as head-moved drift"
    )
