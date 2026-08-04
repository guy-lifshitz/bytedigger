"""RED tests for 9EDB7588 — _commit_red_tests idempotency guard.

Spec: SHARED/memory/Decisions/2026-06-11_9EDB7588_commit_red_idempotency_spec.md

Bug: on phase_5 re-entry (cycle>=2) _commit_red_tests re-runs
`git commit -o -- <test_paths>` on already-committed, unchanged test paths
→ empty commit → E_GIT_COMMIT_FAILED.

Fix: add `_paths_have_staged_changes(git_cwd, paths) -> bool` helper and a
guard that SKIPS the commit when there is no staged diff for the tracked paths,
keeps `red_commit_sha = HEAD`, emits `commit_red_tests_idempotent_skip`
with `reason="no_staged_diff"`.

Pre-GREEN PASS/FAIL classification (§3):
  AC1 → FAIL  (current code returns error/E_GIT_COMMIT_FAILED on re-entry)
  AC2 → FAIL  (current code returns error_code != None on re-entry)
  AC3 → FAIL  (current code creates empty commit attempt, git non-zero, HEAD may
               not advance cleanly — but guard absent means behavior wrong; also
               AC3+AC4 share same forcing fixture as AC1/AC2)
  AC4 → FAIL  (result.data["red_commit_sha"] wrong on re-entry)
  AC5 → FAIL  (no commit_red_tests_idempotent_skip event in current code)
  AC6 → FAIL  (_paths_have_staged_changes does not exist → ImportError inside test fn → FAIL)
  AC7 → PASS  (first-cycle real commit already works — regression guard)
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

# ─── sys.path setup (guard-wrapped per suite_safety.py scanner) ───────────────
_ENGINE_PY = Path(__file__).resolve().parents[1]
if str(_ENGINE_PY) not in sys.path:
    sys.path.insert(0, str(_ENGINE_PY))
_WORKFLOWS = _ENGINE_PY / "bytedigger_engine" / "workflows"
if str(_WORKFLOWS) not in sys.path:
    sys.path.insert(0, str(_WORKFLOWS))

# ─── Production imports (module-level — types only, no not-yet-existing symbols) ─
from bytedigger_engine.contracts import StepResult, WorkflowContext  # noqa: E402

# _commit_red_tests exists today — safe at module level
from bytedigger_engine.workflows.phase_5_implement import _commit_red_tests  # noqa: E402

# ─── Helpers ──────────────────────────────────────────────────────────────────

PRE_RED_REF_RELPATH = "integrity/pre-red-ref.txt"


def _make_ctx(scratchpad: Path, repo: Path) -> WorkflowContext:
    """Build a minimal WorkflowContext for _commit_red_tests tests."""
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={"scratchpad_dir": str(scratchpad), "git_cwd": str(repo)},
        question="9EDB7588 test",
        session_id="test-9EDB7588",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_prev(cycle: int, red_test_paths: list) -> StepResult:
    """Build a prev StepResult for _commit_red_tests."""
    return StepResult(
        status="ok",
        data={
            "cycle": cycle,
            "red_test_paths": red_test_paths,
            "spec_path": None,
        },
        duration_ms=0,
        step_name="write_red_artifact",
    )


def _git(repo: Path, *args: str) -> str:
    """Run a git command in repo and return stripped stdout."""
    r = subprocess.run(["git"] + list(args), cwd=repo, capture_output=True, text=True, check=True)
    return r.stdout.strip()


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)


def _seed_reentry_fixture(tmp_path: Path):
    """Build the re-entry fixture described in §5 of the spec.

    Returns (repo, scratchpad, sha_1) where:
      sha_1 = HEAD after committing tests/test_foo.py (cycle-1 RED commit)
    The scratchpad pre-red-ref.txt holds sha_0 (the pre-RED boundary).
    A dirty prod file (prod_mod.py) is left uncommitted so git status is non-empty.
    """
    repo = tmp_path / "repo"
    scratchpad = tmp_path / "scratch"

    _init_repo(repo)

    # Seed commit → SHA_0
    (repo / "README.md").write_text("init\n")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    sha_0 = _git(repo, "rev-parse", "HEAD")

    # Write pre-red-ref.txt = SHA_0 (frozen cycle-1 boundary)
    ref_path = scratchpad / PRE_RED_REF_RELPATH
    ref_path.parent.mkdir(parents=True, exist_ok=True)
    ref_path.write_text(sha_0)

    # Commit tests/test_foo.py → SHA_1 (simulates cycle-1 RED commit)
    test_file = repo / "tests" / "test_foo.py"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text("def test_stub(): assert False\n")
    subprocess.run(["git", "add", "tests/test_foo.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "build: red cycle 1 tests [main]"], cwd=repo, check=True)
    sha_1 = _git(repo, "rev-parse", "HEAD")

    # Dirty prod file — makes git status --porcelain non-empty so the
    # elif st.stdout.strip(): branch is entered (reproducing the bug path).
    (repo / "prod_mod.py").write_text("# GREEN prod change\n")

    return repo, scratchpad, sha_1


# ─── AC1 + AC2: re-entry returns status==ok, error_code==None ─────────────────


def test_reentry_unchanged_paths_returns_ok(tmp_path: Path) -> None:
    """AC1/AC2: re-entry fixture (committed RED test at frozen+1, dirty prod,
    pre-red-ref=SHA_0) — _commit_red_tests returns status=='ok' and error_code
    is None.

    Current code attempts `git commit -o -- tests/test_foo.py` on an already-
    committed, unmodified file → empty commit → git exits non-zero →
    E_GIT_COMMIT_FAILED.  After GREEN the idempotency guard detects no staged
    diff and skips the commit, returning status='ok'.

    Pre-GREEN: FAIL (status='error', error_code='E_GIT_COMMIT_FAILED').
    Isolation: the fixture builds a real temp repo; failure is caused by the
    absent guard, not by any shared state.
    """
    repo, scratchpad, _sha_1 = _seed_reentry_fixture(tmp_path)
    ctx = _make_ctx(scratchpad, repo)
    prev = _make_prev(cycle=2, red_test_paths=["tests/test_foo.py"])

    result = _commit_red_tests(ctx, prev)

    assert result.status == "ok", (
        f"AC1: expected status='ok' on re-entry (no staged diff), "
        f"got {result.status!r} (error={result.error!r}, code={result.error_code!r}).  "
        "Fix: add _paths_have_staged_changes guard before git commit -o."
    )
    assert result.error_code is None, (
        f"AC2: expected error_code=None on re-entry, got {result.error_code!r}.  "
        "Fix: idempotency guard must skip the commit and return ok."
    )


# ─── AC3 + AC4: HEAD unchanged, red_commit_sha == pre-call SHA ─────────────────


def test_reentry_head_unchanged_no_empty_commit(tmp_path: Path) -> None:
    """AC3/AC4: same re-entry fixture — HEAD is unchanged after the call and
    result.data['red_commit_sha'] equals the pre-call HEAD (SHA_1).

    Anchor: git side-effect — captures HEAD before and after call.

    Pre-GREEN: FAIL because the commit attempt either raises or advances HEAD
    unexpectedly (empty-commit branch), so neither the sha equality nor the
    HEAD-stable assertion holds.
    Isolation: real git repo per test; no shared state.
    """
    repo, scratchpad, sha_1 = _seed_reentry_fixture(tmp_path)
    ctx = _make_ctx(scratchpad, repo)
    prev = _make_prev(cycle=2, red_test_paths=["tests/test_foo.py"])

    # Capture HEAD before the call
    head_before = _git(repo, "rev-parse", "HEAD")
    assert head_before == sha_1, "fixture sanity: HEAD should be SHA_1 before call"

    result = _commit_red_tests(ctx, prev)

    head_after = _git(repo, "rev-parse", "HEAD")
    assert head_after == sha_1, (
        f"AC3: HEAD must not change on re-entry (was {sha_1!r}, "
        f"now {head_after!r}).  Fix: guard must skip the empty commit."
    )
    assert isinstance(result.data, dict), (
        f"AC4: result.data must be a dict, got {type(result.data)}"
    )
    assert result.data.get("red_commit_sha") == sha_1, (
        f"AC4: result.data['red_commit_sha'] must equal pre-call HEAD {sha_1!r}, "
        f"got {result.data.get('red_commit_sha')!r}.  "
        "Fix: guard sets red_commit_sha = pre_red_sha (existing HEAD)."
    )
    # Confirm it is a 40-hex SHA
    rcs = result.data.get("red_commit_sha", "")
    assert len(rcs) == 40 and all(c in "0123456789abcdef" for c in rcs), (
        f"AC4: red_commit_sha must be 40-hex, got {rcs!r}"
    )


# ─── AC5: idempotent-skip telemetry event emitted ─────────────────────────────


def test_reentry_emits_idempotent_skip_event(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """AC5: same re-entry fixture — a 'commit_red_tests_idempotent_skip' event is
    emitted with reason=='no_staged_diff'.

    Monkeypatches phase_5_implement._emit_safe to capture calls.

    Anti-stub forcing function (§1l): an impl that returns ok without emitting
    the event fails this test.  Cannot be stub-satisfied without the real emit
    call in the guard branch.

    Pre-GREEN: FAIL — current code has no idempotency guard, so no
    'commit_red_tests_idempotent_skip' event is emitted (0 events captured).
    Isolation: fresh repo + monkeypatch per test; no cross-test state.
    """
    from bytedigger_engine.workflows import phase_5_implement as _p5m  # noqa: PLC0415

    events: list[tuple[str, dict, dict]] = []

    def _capture_emit(name: str, payload: dict, **kw: Any) -> None:
        events.append((name, payload, kw))

    monkeypatch.setattr(_p5m, "_emit_safe", _capture_emit)

    repo, scratchpad, _sha_1 = _seed_reentry_fixture(tmp_path)
    ctx = _make_ctx(scratchpad, repo)
    prev = _make_prev(cycle=2, red_test_paths=["tests/test_foo.py"])

    _commit_red_tests(ctx, prev)

    skip_events = [
        (name, payload, kw)
        for name, payload, kw in events
        if name == "commit_red_tests_idempotent_skip"
    ]
    assert len(skip_events) >= 1, (
        f"AC5: expected at least 1 'commit_red_tests_idempotent_skip' event, "
        f"got {len(skip_events)}.  All events captured: {[e[0] for e in events]!r}.  "
        "Pre-GREEN: 0 events → FAIL expected."
    )
    _, payload, _ = skip_events[0]
    assert payload.get("reason") == "no_staged_diff", (
        f"AC5: event payload['reason'] must be 'no_staged_diff', "
        f"got {payload.get('reason')!r}.  "
        "Fix: emit with reason='no_staged_diff' per spec §2."
    )


# ─── AC6: _paths_have_staged_changes helper exists and is correct ──────────────


def test_paths_have_staged_changes_helper(tmp_path: Path) -> None:
    """AC6 (§1aa): _paths_have_staged_changes(git_cwd, paths) must exist and
    return False when path has no staged diff, True when path is git-added.

    Import is deferred to inside the test body per D1CF5FDF (avoids collection-
    time ImportError hanging the engine when the symbol doesn't exist yet).

    Pre-GREEN: FAIL — symbol doesn't exist → ImportError inside the test body
    → AssertionError / ImportError → test fails.  This is the §1aa forcing-fn:
    'declare -f'-equivalent: the helper must be importable AND correct.
    Isolation: fresh repo; no shared state.
    """
    # Deferred import — avoids collection-time ImportError when helper absent
    try:
        from bytedigger_engine.workflows.phase_5_implement import _paths_have_staged_changes  # noqa: PLC0415
    except ImportError as exc:
        pytest.fail(
            f"AC6: _paths_have_staged_changes not importable from phase_5_implement "
            f"({exc}).  Fix: add the helper per spec §2."
        )

    repo = tmp_path / "repo"
    _init_repo(repo)

    # Need at least one commit so HEAD exists
    (repo / "README.md").write_text("init\n")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)

    # Existing committed file — not staged, no changes
    committed = repo / "existing.py"
    committed.write_text("x = 1\n")
    subprocess.run(["git", "add", "existing.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "add existing"], cwd=repo, check=True)

    # No staged diff for existing committed path → must return False
    result_false = _paths_have_staged_changes(str(repo), ["existing.py"])
    assert result_false is False, (
        f"AC6: expected False for committed+unmodified 'existing.py', "
        f"got {result_false!r}.  "
        "Fix: helper must return False when git diff --cached --quiet exits 0."
    )

    # Create and git-add a NEW file → staged diff exists → must return True
    new_file = repo / "new_test.py"
    new_file.write_text("def test_new(): assert False\n")
    subprocess.run(["git", "add", "new_test.py"], cwd=repo, check=True)

    result_true = _paths_have_staged_changes(str(repo), ["new_test.py"])
    assert result_true is True, (
        f"AC6: expected True for newly git-added 'new_test.py', "
        f"got {result_true!r}.  "
        "Fix: helper must return True when git diff --cached --quiet exits 1."
    )


# ─── AC7: first-cycle real changes still commit (negative control / regression) ──


def test_first_cycle_real_changes_still_commits(tmp_path: Path) -> None:
    """AC7 (negative control): first-cycle fixture where tests/test_foo.py is
    NEW and git-added (real staged diff) — _commit_red_tests must still create
    a commit (status=='ok', HEAD advances, commit subject present).

    The idempotency guard must NOT skip a genuine RED commit.
    This test is expected to PASS pre-GREEN (regression anchor that protects the
    B1AAACFB siblings).  It must continue to pass post-GREEN.

    Isolation: fresh repo with a genuinely staged new test file.
    """
    repo = tmp_path / "repo"
    scratchpad = tmp_path / "scratch"

    _init_repo(repo)

    # Seed commit (SHA_0)
    (repo / "README.md").write_text("init\n")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    sha_0 = _git(repo, "rev-parse", "HEAD")

    # Write pre-red-ref.txt = SHA_0 (as if this is cycle-1 start)
    ref_path = scratchpad / PRE_RED_REF_RELPATH
    ref_path.parent.mkdir(parents=True, exist_ok=True)
    ref_path.write_text(sha_0)

    # Create + stage tests/test_foo.py — real staged diff (first cycle)
    test_file = repo / "tests" / "test_foo.py"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text("def test_fail(): assert False\n")
    subprocess.run(["git", "add", "tests/test_foo.py"], cwd=repo, check=True)

    head_before = _git(repo, "rev-parse", "HEAD")

    ctx = _make_ctx(scratchpad, repo)
    prev = _make_prev(cycle=1, red_test_paths=["tests/test_foo.py"])

    result = _commit_red_tests(ctx, prev)

    assert result.status == "ok", (
        f"AC7: expected status='ok' for first-cycle real staged commit, "
        f"got {result.status!r} (error={result.error!r}, code={result.error_code!r}).  "
        "Guard must NOT skip genuine RED commits."
    )

    head_after = _git(repo, "rev-parse", "HEAD")
    assert head_after != head_before, (
        f"AC7: HEAD must advance after a real commit (before={head_before!r}, "
        f"after={head_after!r}).  Guard must not suppress genuine commits."
    )

    # Commit subject must be non-empty and carry the canonical prefix
    subject = subprocess.check_output(
        ["git", "log", "-1", "--format=%s"], cwd=repo, text=True
    ).strip()
    assert subject, "AC7: commit subject must be non-empty"
    assert "red" in subject.lower() or "build" in subject.lower(), (
        f"AC7: commit subject should contain 'red' or 'build', got {subject!r}"
    )
