"""GH637 RED tests — persist frozen pre-red boundary BEFORE the RED commit.

Closes agreement D5D74333-E846-447B-B0EB-9FD9B345BB00 (GH637). Parent
SYSTEMATIC 8D162396 (frozen pre-red boundary family) + §1ab re-entry-AC
mandate.

Bug: `_persist_pre_red_ref` in `phase_5_implement._commit_red_tests` is
called AFTER the RED `git add`/`git commit`. A crash in the window between
the commit landing (HEAD -> R) and the persist leaves the ref file absent;
on resume, the boundary falls back to R itself -> vacuous GREEN (RED set
diffs against itself).

Fix (GREEN, not this file): relocate the `_persist_pre_red_ref` call to
BEFORE the git add/commit block, immediately after `pre_red_sha` validation.

These tests FAIL pre-GREEN (persist still runs after commit) and PASS
post-GREEN (persist runs before commit; crash-in-window resume recovers
the true parent P, not the RED commit R).
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import pytest

# Import the module at top level — conftest-import-time singleton already put
# engine_py root + workflows on sys.path (§1q / 81F97F3D gate). Both
# `_commit_red_tests` and `_persist_pre_red_ref` already exist today (this is
# a reorder bug, not a missing-symbol bug), so this import is safe at
# collection time (D1CF5FDF).
from bytedigger_engine.workflows import phase_5_implement as mod


# ─── shared git-repo helpers (copied verbatim from
#     test_8D162396_red_loop_reentry_frozen_sha.py for self-containment) ─────


def _make_repo(base: Path) -> Path:
    """Create a minimal git repo under base with one initial commit.

    Uses os.path.realpath so /var/folders symlinks don't break subprocess
    cwd equality on macOS (§1j).
    """
    repo = Path(os.path.realpath(str(base / "repo")))
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "tester"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)
    # Initial commit so HEAD is valid
    placeholder = repo / "src" / "placeholder.py"
    placeholder.parent.mkdir(parents=True, exist_ok=True)
    placeholder.write_text("# placeholder\n")
    subprocess.run(["git", "add", "src/placeholder.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return repo


def _head_sha(repo: Path) -> str:
    r = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=repo, check=True
    )
    return r.stdout.strip()


def _commit_file(repo: Path, relpath: str, body: str, msg: str = "c") -> str:
    """Write a file, commit it, return new HEAD sha."""
    p = repo / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    subprocess.run(["git", "add", relpath], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", msg], cwd=repo, check=True)
    return _head_sha(repo)


def _make_scratchpad() -> Path:
    """Return a real-path'd tmp scratchpad dir (§1j)."""
    return Path(os.path.realpath(tempfile.mkdtemp()))


def _make_ctx(scratchpad: Path, git_cwd: str | None = None):
    from bytedigger_engine.contracts import WorkflowContext  # noqa: PLC0415

    org: dict = {"scratchpad_dir": str(scratchpad)}
    if git_cwd is not None:
        org["git_cwd"] = git_cwd
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question="add a feature",
        session_id="test-session",
        persona="hal",
        framework=None,
        domain=None,
    )


def _prev_for_commit(scratchpad: Path, cycle: int = 1, red_test_paths=None):
    """Minimal prev StepResult accepted by _commit_red_tests."""
    from bytedigger_engine.contracts import StepResult  # noqa: PLC0415

    return StepResult(
        status="ok",
        data={
            "red_log_path": str(scratchpad / "tests/build-red-output.log"),
            "spec_path": str(scratchpad / "specs/build-spec.md"),
            "cycle": cycle,
            "red_test_paths": red_test_paths,
        },
        duration_ms=0,
        step_name="write_red_artifact",
    )


def _write_untracked_red_test(repo: Path, relpath: str) -> None:
    """Write a RED test file into repo's working tree WITHOUT committing it."""
    p = repo / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("def test_x(): assert False\n")


# ─── AC1: persist-before-commit call order (forcing function) ────────────────


def test_ac1_persist_ref_called_before_git_commit(tmp_path):
    """AC1: in a fresh _commit_red_tests run that commits a RED test,
    _persist_pre_red_ref must be invoked BEFORE the git commit op.

    Pre-fix: persist happens after the commit block -> order.index("persist")
    > order.index("commit") -> FAILS.
    """
    repo = _make_repo(tmp_path)
    scratchpad = _make_scratchpad()
    _write_untracked_red_test(repo, "tests/test_red_x.py")

    ctx = _make_ctx(scratchpad, git_cwd=str(repo))
    prev = _prev_for_commit(scratchpad, cycle=1, red_test_paths=["tests/test_red_x.py"])

    order: list[str] = []

    real_persist = mod._persist_pre_red_ref
    real_git_op = mod._git_op_with_lock_retry

    def _spy_persist(scratchpad_arg, sha_arg):
        order.append("persist")
        return real_persist(scratchpad_arg, sha_arg)

    def _spy_git_op(argv, *args, **kwargs):
        if "commit" in argv:
            order.append("commit")
        return real_git_op(argv, *args, **kwargs)

    mp = pytest.MonkeyPatch()
    try:
        mp.setattr(mod, "_persist_pre_red_ref", _spy_persist)
        mp.setattr(mod, "_git_op_with_lock_retry", _spy_git_op)

        result = mod._commit_red_tests(ctx, prev)
    finally:
        mp.undo()

    assert result.status == "ok", f"expected ok, got {result.status}: {getattr(result, 'error', None)}"
    assert "persist" in order, f"spy never recorded a persist call: {order!r}"
    assert "commit" in order, f"spy never recorded a commit call: {order!r}"
    assert order.index("persist") < order.index("commit"), (
        "GH637: _persist_pre_red_ref must run BEFORE git commit so a crash "
        f"after the commit still leaves the boundary frozen at the true parent. "
        f"Observed call order: {order!r}"
    )


# ─── AC2/AC3 shared setup + crash injection ───────────────────────────────────


def _crash_after_commit_and_resume(tmp_path):
    """Shared fixture: fresh repo+scratchpad, crash-inject after RED commit,
    then resume with a fresh _commit_red_tests call.

    Returns (repo, scratchpad, parent_sha, red_commit_sha_after_crash).
    """
    repo = _make_repo(tmp_path)
    parent = _head_sha(repo)
    scratchpad = _make_scratchpad()
    _write_untracked_red_test(repo, "tests/test_red_y.py")

    ctx = _make_ctx(scratchpad, git_cwd=str(repo))
    prev_fresh = _prev_for_commit(scratchpad, cycle=1, red_test_paths=["tests/test_red_y.py"])

    real_git_op = mod._git_op_with_lock_retry

    def _crashing_git_op(argv, *args, **kwargs):
        result = real_git_op(argv, *args, **kwargs)
        if "commit" in argv:
            raise RuntimeError("GH637 injected crash: after RED commit, before step return")
        return result

    mp = pytest.MonkeyPatch()
    try:
        mp.setattr(mod, "_git_op_with_lock_retry", _crashing_git_op)
        with pytest.raises(RuntimeError, match="GH637 injected crash"):
            mod._commit_red_tests(ctx, prev_fresh)
    finally:
        mp.undo()

    red_commit_sha = _head_sha(repo)
    assert red_commit_sha != parent, (
        "the RED commit must have actually landed before the injected crash fired "
        f"(parent={parent!r}, head={red_commit_sha!r})"
    )

    # RESUME: same repo, same scratchpad, real (un-patched) git op.
    prev_resume = _prev_for_commit(scratchpad, cycle=1, red_test_paths=["tests/test_red_y.py"])
    resume_result = mod._commit_red_tests(ctx, prev_resume)
    assert resume_result.status == "ok", (
        f"resume after crash-in-window must succeed, got {resume_result.status}: "
        f"{getattr(resume_result, 'error', None)}"
    )

    ref_path = Path(scratchpad) / mod.PRE_RED_REF_RELPATH
    assert ref_path.exists(), "pre-red-ref file must exist after resume"
    ref_value = ref_path.read_text().strip()
    return repo, scratchpad, parent, red_commit_sha, ref_value


def test_ac2_crash_in_window_resume_boundary_is_parent_not_red_commit(tmp_path):
    """AC2 (§1ab (c)): crash between RED commit landing and persist; on resume,
    integrity/pre-red-ref.txt must equal the true parent P, NOT the RED
    commit R.

    Pre-fix: persist runs after commit, so the crash fires before any persist
    ever happens; resume's first-ever persist writes R (the RED commit
    itself) -> vacuous GREEN. FAILS.
    """
    repo, scratchpad, parent, red_commit_sha, ref_value = _crash_after_commit_and_resume(tmp_path)

    assert ref_value == parent, (
        "GH637: pre-red-ref must freeze at the true parent P after a "
        "crash-in-window + resume, not at the RED commit R. "
        f"parent(P)={parent!r}, red_commit(R)={red_commit_sha!r}, ref written={ref_value!r}. "
        "Pre-fix persists after the commit, so the crash prevents any persist "
        "until resume, whose idempotent-skip path stamps the ref with "
        "red_commit_sha (=R) instead of the true parent."
    )


def test_ac3_crash_in_window_resume_does_not_clobber_frozen_boundary(tmp_path):
    """AC3 (§1ab (c) idempotency): in the same crash+resume scenario, the
    written ref must differ from the RED commit R even though resume-time
    HEAD == R — re-entry write-once must not re-stamp the boundary with the
    now-advanced HEAD.
    """
    repo, scratchpad, parent, red_commit_sha, ref_value = _crash_after_commit_and_resume(tmp_path)

    assert _head_sha(repo) == red_commit_sha, (
        "sanity: resume-time HEAD should equal the already-landed RED commit "
        f"(no new commit expected on idempotent resume); got {_head_sha(repo)!r} "
        f"vs {red_commit_sha!r}"
    )
    assert ref_value != red_commit_sha, (
        "GH637: write-once re-entry must NOT clobber/stamp the frozen boundary "
        f"with resume-time HEAD (={red_commit_sha!r}); ref must stay at the "
        f"true parent instead. Got ref={ref_value!r}."
    )


# ─── AC4: fresh clean regression (§1ab (a)) ───────────────────────────────────


def test_ac4_fresh_clean_run_ref_equals_parent(tmp_path):
    """AC4: a single clean _commit_red_tests run (no crash) writes ref ==
    pre-commit HEAD (the true parent) and returns ok, with HEAD advancing
    past it for the RED commit.
    """
    repo = _make_repo(tmp_path)
    parent = _head_sha(repo)
    scratchpad = _make_scratchpad()
    _write_untracked_red_test(repo, "tests/test_red_z.py")

    ctx = _make_ctx(scratchpad, git_cwd=str(repo))
    prev = _prev_for_commit(scratchpad, cycle=1, red_test_paths=["tests/test_red_z.py"])

    result = mod._commit_red_tests(ctx, prev)

    assert result.status == "ok", f"expected ok, got {result.status}: {getattr(result, 'error', None)}"
    new_head = _head_sha(repo)
    assert new_head != parent, "RED test must have been committed, advancing HEAD"
    assert result.data.get("red_commit_sha") == new_head, (
        f"red_commit_sha must equal the advanced HEAD; got "
        f"{result.data.get('red_commit_sha')!r} vs {new_head!r}"
    )

    ref_path = Path(scratchpad) / mod.PRE_RED_REF_RELPATH
    assert ref_path.exists(), "pre-red-ref file must be written on a clean fresh run"
    assert ref_path.read_text().strip() == parent, (
        f"pre-red-ref must equal the true parent {parent!r} on a clean fresh run, "
        f"got {ref_path.read_text().strip()!r}"
    )


# ─── AC5: no E_RED_NO_MARKER regression, persist must not run early ──────────


def test_ac5_no_red_paths_returns_e_red_no_marker_without_persisting(tmp_path):
    """AC5: an empty repo (no RED test files anywhere) still returns
    E_RED_NO_MARKER, and the pre-red-ref file must NOT exist — the early
    return must precede any persist call.
    """
    repo = _make_repo(tmp_path)
    scratchpad = _make_scratchpad()

    ctx = _make_ctx(scratchpad, git_cwd=str(repo))
    prev = _prev_for_commit(scratchpad, cycle=1, red_test_paths=None)

    result = mod._commit_red_tests(ctx, prev)

    assert getattr(result, "error_code", None) == "E_RED_NO_MARKER", (
        f"expected E_RED_NO_MARKER, got {getattr(result, 'error_code', None)!r}: "
        f"{getattr(result, 'error', None)!r}"
    )
    ref_path = Path(scratchpad) / mod.PRE_RED_REF_RELPATH
    assert not ref_path.exists(), (
        "pre-red-ref must NOT be written when _commit_red_tests early-returns "
        "E_RED_NO_MARKER (persist must not run before the RED-paths check)."
    )
