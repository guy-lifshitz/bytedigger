"""B6247E87 RED tests — index.lock retry logic for _commit_red_tests.

Closes agreement B6247E87: when `git add` or `git commit` fails with
"Unable to create '.git/index.lock': File exists" stderr, engine should
retry with exponential backoff (3 attempts: 0s, 1s, 2s) instead of
failing immediately as E_GIT_COMMIT_FAILED.

Desired behavior:
1. Lock removed mid-retry → status="ok" (retry succeeded).
2. Lock never removed → status="error", error_code="E_GIT_LOCKED".
3. E_GIT_LOCKED is distinct from E_GIT_COMMIT_FAILED.
4. Non-lock git failures → no retry, immediate E_GIT_COMMIT_FAILED.

These tests FAIL against current phase_5_implement.py (no retry logic).
They PASS once GREEN implements the retry/backoff + E_GIT_LOCKED code.
"""
from __future__ import annotations

import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest import mock

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))


def _retry_backoff_sleeps(all_sleeps: "list[float]") -> "list[float]":
    """GH669: isolate the git index.lock retry backoff from noise sleeps.

    These tests patch the process-global ``time.sleep`` and assert on the
    captured sequence to verify the retry backoff. The backoff is
    ``time.sleep(attempt)`` for ``attempt`` in ``{1, 2}`` — always WHOLE SECONDS
    (>= 1). Every sub-second sleep that the global patch also captures is NOT
    the backoff and must be filtered out:

    * ``bounded_run``'s subprocess-wait poll — ``time.sleep(0.05)`` on the
      calling (main) thread whenever a real ``git`` subprocess runs slowly,
      which happens under pytest-xdist CPU contention (``-n auto``);
    * a foreign ``llm-stream-reader`` daemon thread abandoned by a sibling
      timeout test, whose fake ``readline()`` loops on ``time.sleep(0.05)``.

    Both are strictly sub-second, so restricting to whole-second sleeps yields
    exactly the retry backoff — an ordering-, worker-count-, and load-independent
    isolation. This is what lets #669 drop the #651 serial quarantine (a
    thread-ident filter is insufficient: bounded_run's poll runs on the main
    thread too).
    """
    return [s for s in all_sleeps if s >= 1]


# ─── helpers (mirror test_phase_5_implement.py style) ────────────────────────


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)


def _commit_file(repo: Path, relpath: str, body: str, msg: str = "c") -> None:
    p = repo / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    subprocess.run(["git", "add", relpath], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", msg], cwd=repo, check=True)


def _minimal_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
    return repo


def _make_ctx(scratchpad: Path, *, question: str = "Add foo to bar", **org_extra):
    from bytedigger_engine.contracts import WorkflowContext  # noqa: PLC0415

    org = {"scratchpad_dir": str(scratchpad), **org_extra}
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question=question,
        session_id="test-session",
        persona="hal",
        framework=None,
        domain=None,
    )


def _prev_ok(scratchpad: Path, repo: Path, test_relpath: str = "tests/test_red.py"):
    from bytedigger_engine.contracts import StepResult  # noqa: PLC0415

    return StepResult(
        status="ok",
        data={
            "red_log_path": str(scratchpad / "tests/build-red-output.log"),
            "spec_path": str(scratchpad / "specs/build-spec.md"),
            "cycle": 1,
            "red_test_paths": [test_relpath],
        },
        duration_ms=0,
        step_name="write_red_artifact",
    )


# ─── Test 1: lock removed mid-retry → ok ─────────────────────────────────────


def test_commit_red_tests_retries_on_external_index_lock_then_succeeds(tmp_path):
    """Lock present at call time; background thread removes it after ~1.5s.

    Asserts status="ok" — retry logic picks it up on attempt 2 or 3.
    FAILS today because _commit_red_tests has no retry logic; it returns
    E_GIT_COMMIT_FAILED on the first git add failure.
    """
    from bytedigger_engine.workflows.phase_5_implement import _commit_red_tests  # noqa: PLC0415

    repo = _minimal_repo(tmp_path)
    scratchpad = tmp_path / "scratch"

    # Place uncommitted RED test file in working tree
    test_file = repo / "tests" / "test_red.py"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text("def test_fails(): assert False\n")

    # Simulate external lock
    lock_file = repo / ".git" / "index.lock"
    lock_file.write_text("lock")

    # Background thread removes lock after ~1.5s (within retry window)
    def _remove_lock():
        time.sleep(1.5)
        lock_file.unlink(missing_ok=True)

    t = threading.Thread(target=_remove_lock, daemon=True)
    t.start()

    ctx = _make_ctx(scratchpad, git_cwd=str(repo))
    prev = _prev_ok(scratchpad, repo)

    result = _commit_red_tests(ctx, prev)
    t.join(timeout=10)

    # RED: currently fails with E_GIT_COMMIT_FAILED; after GREEN should be "ok"
    assert result.status == "ok", (
        f"Expected status='ok' after lock released mid-retry, "
        f"got status={result.status!r} error_code={getattr(result, 'error_code', None)!r} "
        f"error={getattr(result, 'error', None)!r}"
    )


# ─── Test 2: lock never removed → E_GIT_LOCKED ───────────────────────────────


def test_commit_red_tests_returns_e_git_locked_when_lock_persists(tmp_path):
    """Lock is never removed; all 3 attempts fail.

    Uses mock.patch to replace the sleep calls with no-ops so the test runs
    in <0.5s instead of waiting the full 3s (0+1+2).

    Asserts status="error", error_code="E_GIT_LOCKED", message mentions "index.lock".
    FAILS today because _commit_red_tests has no retry logic and returns
    E_GIT_COMMIT_FAILED (not E_GIT_LOCKED).
    """
    from bytedigger_engine.workflows.phase_5_implement import _commit_red_tests  # noqa: PLC0415

    repo = _minimal_repo(tmp_path)
    scratchpad = tmp_path / "scratch"

    test_file = repo / "tests" / "test_red.py"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text("def test_fails(): assert False\n")

    # Lock that is never removed
    lock_file = repo / ".git" / "index.lock"
    lock_file.write_text("lock")

    ctx = _make_ctx(scratchpad, git_cwd=str(repo))
    prev = _prev_ok(scratchpad, repo)

    # Patch time.sleep to capture backoff sequence without waiting.
    sleep_calls: list[float] = []
    with mock.patch("time.sleep", side_effect=lambda s: sleep_calls.append(s)):
        result = _commit_red_tests(ctx, prev)

    # RED: currently returns E_GIT_COMMIT_FAILED; after GREEN must be E_GIT_LOCKED
    assert result.status == "error", f"Expected status='error', got {result.status!r}"
    assert getattr(result, "error_code", None) == "E_GIT_LOCKED", (
        f"Expected error_code='E_GIT_LOCKED', got {getattr(result, 'error_code', None)!r}. "
        "E_GIT_LOCKED is a new distinct code for persistent index.lock failures."
    )
    err_msg = getattr(result, "error", "") or ""
    assert "index.lock" in err_msg.lower(), (
        f"Expected error message to mention 'index.lock', got: {err_msg!r}"
    )
    # AC1: backoff sequence is 0s before attempt 1, 1s before attempt 2, 2s before attempt 3.
    # sleep() is called BETWEEN attempts: once with 1 before attempt 2, once with 2 before
    # attempt 3 → two sleep calls total: [1, 2].
    # GH669: filter to whole-second sleeps — the retry backoff — excluding
    # bounded_run's sub-second subprocess-wait poll (fires under xdist load on
    # the real `git add` here) and any foreign daemon-thread sleep.
    assert _retry_backoff_sleeps(sleep_calls) == [1, 2], (
        f"Expected backoff sleep sequence [1, 2] (seconds before attempts 2 and 3), "
        f"got {_retry_backoff_sleeps(sleep_calls)!r} (all captured: {sleep_calls!r}). "
        f"Spec AC1: backoff is 0s, 1s, 2s across 3 attempts."
    )


# ─── Test 3: E_GIT_LOCKED distinct from E_GIT_COMMIT_FAILED ─────────────────


def test_commit_red_tests_e_git_locked_distinct_from_e_git_commit_failed(tmp_path):
    """E_GIT_LOCKED and E_GIT_COMMIT_FAILED must be different strings.

    Triggers both in one test:
    - Lock scenario  → expects E_GIT_LOCKED
    - MERGE_HEAD bad-state scenario → expects E_GIT_BAD_STATE (existing code)

    Primary assertion: the two error codes are not equal — they must be distinct
    constants so callers can branch on them separately.

    FAILS today because E_GIT_LOCKED does not exist yet (no retry logic).
    """
    from bytedigger_engine.workflows.phase_5_implement import _commit_red_tests  # noqa: PLC0415

    # ── scenario A: persistent lock → E_GIT_LOCKED ──
    repo_a = _minimal_repo(tmp_path / "a")
    scratchpad_a = tmp_path / "scratch_a"

    test_file_a = repo_a / "tests" / "test_red.py"
    test_file_a.parent.mkdir(parents=True, exist_ok=True)
    test_file_a.write_text("def test_fails(): assert False\n")

    lock_a = repo_a / ".git" / "index.lock"
    lock_a.write_text("lock")

    ctx_a = _make_ctx(scratchpad_a, git_cwd=str(repo_a))
    prev_a = _prev_ok(scratchpad_a, repo_a)

    with mock.patch("time.sleep", return_value=None):
        result_lock = _commit_red_tests(ctx_a, prev_a)

    code_lock = getattr(result_lock, "error_code", None)

    # ── scenario B: non-lock bad git state → E_GIT_BAD_STATE (already exists) ──
    repo_b = _minimal_repo(tmp_path / "b")
    scratchpad_b = tmp_path / "scratch_b"

    test_file_b = repo_b / "tests" / "test_red.py"
    test_file_b.parent.mkdir(parents=True, exist_ok=True)
    test_file_b.write_text("def test_fails(): assert False\n")

    # Simulate MERGE_HEAD bad state (existing E_GIT_BAD_STATE path)
    merge_head = repo_b / ".git" / "MERGE_HEAD"
    merge_head.write_text("deadbeef" * 5)

    ctx_b = _make_ctx(scratchpad_b, git_cwd=str(repo_b))
    prev_b = _prev_ok(scratchpad_b, repo_b)
    result_bad_state = _commit_red_tests(ctx_b, prev_b)
    code_bad_state = getattr(result_bad_state, "error_code", None)

    # Both must be error status
    assert result_lock.status == "error"
    assert result_bad_state.status == "error"

    # E_GIT_LOCKED must exist (not None) and must differ from the other code
    assert code_lock == "E_GIT_LOCKED", (
        f"Lock scenario should return E_GIT_LOCKED, got {code_lock!r}. "
        "E_GIT_LOCKED is a new code introduced by B6247E87."
    )
    assert code_lock != code_bad_state, (
        f"E_GIT_LOCKED ({code_lock!r}) must be distinct from {code_bad_state!r}"
    )


# ─── Test 4: non-lock failure → no retry, immediate E_GIT_COMMIT_FAILED ──────


def test_commit_red_tests_does_not_retry_on_non_lock_git_failure(tmp_path):
    """Non-lock git failure must NOT trigger retry; only 1 git add attempt.

    Asserts error_code="E_GIT_COMMIT_FAILED" (NOT "E_GIT_LOCKED").
    Asserts time.sleep is NOT called (no retry = no backoff sleep).
    FAILS today because E_GIT_LOCKED does not exist and _commit_red_tests has
    no retry logic — so the error_code assertion will fail (returns
    E_GIT_COMMIT_FAILED already, but git add call count is 1 only by accident;
    once retry is added the guard here must still hold for non-lock errors).
    """
    from bytedigger_engine.workflows.phase_5_implement import _commit_red_tests  # noqa: PLC0415

    repo = _minimal_repo(tmp_path)
    scratchpad = tmp_path / "scratch"

    test_file = repo / "tests" / "test_red.py"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text("def test_fails(): assert False\n")

    original_run = subprocess.run
    call_count = 0

    def patched_run(cmd, **kwargs):
        nonlocal call_count
        if isinstance(cmd, list) and cmd[:2] == ["git", "add"]:
            call_count += 1
            class FakeResult:
                returncode = 128
                stderr = "fatal: unable to open index file: Permission denied"
                stdout = ""
            return FakeResult()
        return original_run(cmd, **kwargs)

    sleep_calls: list[float] = []

    ctx = _make_ctx(scratchpad, git_cwd=str(repo))
    prev = _prev_ok(scratchpad, repo)

    with mock.patch("subprocess.run", side_effect=patched_run), \
         mock.patch("time.sleep", side_effect=lambda s: sleep_calls.append(s)):
        result = _commit_red_tests(ctx, prev)

    # Must fail as E_GIT_COMMIT_FAILED, not E_GIT_LOCKED
    assert result.status == "error"
    assert getattr(result, "error_code", None) == "E_GIT_COMMIT_FAILED", (
        f"Non-lock failure should return E_GIT_COMMIT_FAILED, got "
        f"{getattr(result, 'error_code', None)!r}"
    )

    # No retry backoff should occur — single attempt only (no retry for non-lock).
    # GH669: filter to whole-second sleeps; sub-second sleeps captured here are
    # bounded_run's subprocess-wait poll (real git rev-parse/status run under
    # xdist load) or a foreign daemon-thread sleep, NOT the retry backoff.
    assert _retry_backoff_sleeps(sleep_calls) == [], (
        f"retry backoff (whole-second time.sleep) should NOT occur for non-lock "
        f"failures (retry guard). Got backoff sleeps: {_retry_backoff_sleeps(sleep_calls)} "
        f"(all captured: {sleep_calls})"
    )

    # git add should be called exactly once (no retry)
    assert call_count == 1, (
        f"git add should be called exactly once for non-lock failure, called {call_count} times"
    )


# ─── Test 5: lock during git commit (not git add) → retry → ok ───────────────


def test_commit_red_tests_retries_on_index_lock_during_git_commit(tmp_path):
    """Lock appears AFTER git add succeeds, on first git commit attempt.

    Spec says "git add OR git commit" can encounter the lock.
    Mock: git add always succeeds; git commit emits lock-error stderr on first
    call, succeeds on retry.

    Asserts status="ok" — retry logic recovers from lock mid-sequence.
    FAILS today because _commit_red_tests has no retry logic.
    """
    from bytedigger_engine.workflows.phase_5_implement import _commit_red_tests  # noqa: PLC0415

    repo = _minimal_repo(tmp_path)
    scratchpad = tmp_path / "scratch"

    test_file = repo / "tests" / "test_red.py"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text("def test_fails(): assert False\n")

    original_run = subprocess.run
    commit_call_count = 0

    LOCK_STDERR = "fatal: Unable to create '.git/index.lock': File exists"

    def patched_run(cmd, **kwargs):
        nonlocal commit_call_count
        if isinstance(cmd, list) and cmd[:2] == ["git", "commit"]:
            commit_call_count += 1
            if commit_call_count == 1:
                # First git commit attempt: simulate lock error
                class LockResult:
                    returncode = 128
                    stderr = LOCK_STDERR
                    stdout = ""
                return LockResult()
            # Second attempt: succeed
            return original_run(cmd, **kwargs)
        return original_run(cmd, **kwargs)

    ctx = _make_ctx(scratchpad, git_cwd=str(repo))
    prev = _prev_ok(scratchpad, repo)

    with mock.patch("subprocess.run", side_effect=patched_run), \
         mock.patch("time.sleep", return_value=None):
        result = _commit_red_tests(ctx, prev)

    assert result.status == "ok", (
        f"Expected status='ok' after git commit lock on attempt 1 + retry succeeds, "
        f"got status={result.status!r} error_code={getattr(result, 'error_code', None)!r} "
        f"error={getattr(result, 'error', None)!r}"
    )
    assert commit_call_count >= 2, (
        f"Expected git commit to be called at least twice (retry after lock), "
        f"called {commit_call_count} time(s)"
    )
