"""53BB3810 RED tests — subprocess hardening for _git_op_with_lock_retry.

Closes agreement 53BB3810: subprocess.run() inside _git_op_with_lock_retry can
raise subprocess.TimeoutExpired or OSError (e.g. FileNotFoundError when git
binary missing). Currently uncaught — they propagate out of phase_5, crashing
the workflow instead of returning a clean StepResult with an error_code.

Desired behaviour after GREEN:
  - subprocess.TimeoutExpired raised → function returns (None, "timeout").
    Caller _commit_red_tests recognises "timeout" → StepResult(error_code="E_GIT_TIMEOUT").
  - OSError (incl. FileNotFoundError) raised → function returns (None, "os_error").
    Caller _commit_red_tests recognises "os_error" → StepResult(error_code="E_GIT_OS_ERROR").
  - Existing outcomes ("ok", "non_lock_error", "lock_persisted") preserved.
  - Retry logic for lock contention preserved.
  - timeout / OSError do NOT trigger retry (stuck or missing binary won't recover).

These tests FAIL against current phase_5_implement.py (no try/except around
subprocess.run inside _git_op_with_lock_retry, no E_GIT_TIMEOUT/E_GIT_OS_ERROR
codes in callers). They PASS once GREEN adds the hardening.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest import mock

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "workflows"))


# ─── helpers (mirror test_phase_5_implement_B6247E87.py style) ──────────────


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
    from contracts import WorkflowContext  # noqa: PLC0415

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
    from contracts import StepResult  # noqa: PLC0415

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


# ─── Test 1: TimeoutExpired → (None, "timeout") ─────────────────────────────


def test_53bb3810_subprocess_timeout_returns_clean_outcome(tmp_path):
    """subprocess.TimeoutExpired raised by subprocess.run must be caught and
    converted to (None, "timeout") instead of propagating out of the function.

    FAILS today: TimeoutExpired propagates uncaught.
    """
    import phase_5_implement  # noqa: PLC0415

    repo = _minimal_repo(tmp_path)

    def _raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=["git", "add", "."], timeout=30)

    with mock.patch.object(phase_5_implement.subprocess, "run", side_effect=_raise_timeout):
        result = phase_5_implement._git_op_with_lock_retry(
            ["git", "add", "."], cwd=str(repo), timeout=30
        )

    assert isinstance(result, tuple) and len(result) == 2, (
        f"Expected 2-tuple (result, outcome), got {result!r}"
    )
    last_result, outcome = result
    assert last_result is None, f"Expected None for last_result on timeout, got {last_result!r}"
    assert outcome == "timeout", (
        f"Expected outcome='timeout' on TimeoutExpired, got {outcome!r}. "
        "GREEN must catch TimeoutExpired and return (None, 'timeout')."
    )


# ─── Test 2: OSError / FileNotFoundError → (None, "os_error") ───────────────


def test_53bb3810_subprocess_os_error_returns_clean_outcome(tmp_path):
    """OSError (incl. FileNotFoundError when git binary missing) must be caught
    and converted to (None, "os_error") instead of propagating.

    FAILS today: FileNotFoundError propagates uncaught.
    """
    import phase_5_implement  # noqa: PLC0415

    repo = _minimal_repo(tmp_path)

    def _raise_fnfe(*args, **kwargs):
        raise FileNotFoundError("git not found")

    with mock.patch.object(phase_5_implement.subprocess, "run", side_effect=_raise_fnfe):
        result = phase_5_implement._git_op_with_lock_retry(
            ["git", "add", "."], cwd=str(repo), timeout=30
        )

    assert isinstance(result, tuple) and len(result) == 2, (
        f"Expected 2-tuple (result, outcome), got {result!r}"
    )
    last_result, outcome = result
    assert last_result is None, f"Expected None for last_result on os_error, got {last_result!r}"
    assert outcome == "os_error", (
        f"Expected outcome='os_error' on FileNotFoundError/OSError, got {outcome!r}. "
        "GREEN must catch OSError and return (None, 'os_error')."
    )


# ─── Test 3: timeout does NOT retry ─────────────────────────────────────────


def test_53bb3810_timeout_does_not_retry(tmp_path):
    """A stuck git binary won't unstick on retry — TimeoutExpired must abort
    immediately with a single subprocess.run call (no retry sleep, no extra
    invocations).

    FAILS today: TimeoutExpired propagates on the first call (call_count == 1
    by accident), but once GREEN adds try/except it MUST still return early.
    """
    import phase_5_implement  # noqa: PLC0415

    repo = _minimal_repo(tmp_path)

    run_mock = mock.Mock(
        side_effect=subprocess.TimeoutExpired(cmd=["git", "add", "."], timeout=30)
    )
    sleep_calls: list[float] = []

    with mock.patch.object(phase_5_implement.subprocess, "run", run_mock), \
         mock.patch.object(phase_5_implement.time, "sleep",
                           side_effect=lambda s: sleep_calls.append(s)):
        last_result, outcome = phase_5_implement._git_op_with_lock_retry(
            ["git", "add", "."], cwd=str(repo), timeout=30
        )

    assert outcome == "timeout", f"Expected outcome='timeout', got {outcome!r}"
    assert run_mock.call_count == 1, (
        f"TimeoutExpired must NOT retry (stuck binary won't recover). "
        f"Expected 1 subprocess.run call, got {run_mock.call_count}."
    )
    assert sleep_calls == [], (
        f"No backoff sleep should occur on timeout (no retry). Got {sleep_calls!r}."
    )


# ─── Test 4: OSError does NOT retry ─────────────────────────────────────────


def test_53bb3810_os_error_does_not_retry(tmp_path):
    """A missing git binary won't appear on retry — FileNotFoundError must
    abort immediately with a single subprocess.run call.
    """
    import phase_5_implement  # noqa: PLC0415

    repo = _minimal_repo(tmp_path)

    run_mock = mock.Mock(side_effect=FileNotFoundError("git not found"))
    sleep_calls: list[float] = []

    with mock.patch.object(phase_5_implement.subprocess, "run", run_mock), \
         mock.patch.object(phase_5_implement.time, "sleep",
                           side_effect=lambda s: sleep_calls.append(s)):
        last_result, outcome = phase_5_implement._git_op_with_lock_retry(
            ["git", "add", "."], cwd=str(repo), timeout=30
        )

    assert outcome == "os_error", f"Expected outcome='os_error', got {outcome!r}"
    assert run_mock.call_count == 1, (
        f"OSError must NOT retry (missing binary won't reappear). "
        f"Expected 1 subprocess.run call, got {run_mock.call_count}."
    )
    assert sleep_calls == [], (
        f"No backoff sleep should occur on OSError. Got {sleep_calls!r}."
    )


# ─── Test 5: _commit_red_tests → E_GIT_TIMEOUT on TimeoutExpired ────────────


def test_53bb3810_commit_red_tests_returns_e_git_timeout_on_subprocess_timeout(tmp_path):
    """End-to-end: TimeoutExpired during the `git add` call inside
    _commit_red_tests must produce StepResult(status='error',
    error_code='E_GIT_TIMEOUT') — not propagate the exception.

    FAILS today: TimeoutExpired propagates out of phase_5.
    """
    import phase_5_implement  # noqa: PLC0415
    from phase_5_implement import _commit_red_tests  # noqa: PLC0415

    repo = _minimal_repo(tmp_path)
    scratchpad = tmp_path / "scratch"

    test_file = repo / "tests" / "test_red.py"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text("def test_fails(): assert False\n")

    original_run = subprocess.run

    def patched_run(cmd, **kwargs):
        if isinstance(cmd, list) and cmd[:2] == ["git", "add"]:
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=30)
        return original_run(cmd, **kwargs)

    ctx = _make_ctx(scratchpad, git_cwd=str(repo))
    prev = _prev_ok(scratchpad, repo)

    with mock.patch.object(phase_5_implement.subprocess, "run", side_effect=patched_run), \
         mock.patch.object(phase_5_implement.time, "sleep", return_value=None):
        result = _commit_red_tests(ctx, prev)

    assert result.status == "error", (
        f"Expected status='error' on git add timeout, got {result.status!r}"
    )
    assert getattr(result, "error_code", None) == "E_GIT_TIMEOUT", (
        f"Expected error_code='E_GIT_TIMEOUT' on TimeoutExpired, "
        f"got {getattr(result, 'error_code', None)!r}. "
        "GREEN must add E_GIT_TIMEOUT branch in _commit_red_tests for outcome=='timeout'."
    )


# ─── Test 6: _commit_red_tests → E_GIT_OS_ERROR on FileNotFoundError ────────


def test_53bb3810_commit_red_tests_returns_e_git_os_error_on_filenotfound(tmp_path):
    """End-to-end: FileNotFoundError during the `git add` call inside
    _commit_red_tests must produce StepResult(status='error',
    error_code='E_GIT_OS_ERROR') — not propagate.

    FAILS today: FileNotFoundError propagates out of phase_5.
    """
    import phase_5_implement  # noqa: PLC0415
    from phase_5_implement import _commit_red_tests  # noqa: PLC0415

    repo = _minimal_repo(tmp_path)
    scratchpad = tmp_path / "scratch"

    test_file = repo / "tests" / "test_red.py"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text("def test_fails(): assert False\n")

    original_run = subprocess.run

    def patched_run(cmd, **kwargs):
        if isinstance(cmd, list) and cmd[:2] == ["git", "add"]:
            raise FileNotFoundError("git not found")
        return original_run(cmd, **kwargs)

    ctx = _make_ctx(scratchpad, git_cwd=str(repo))
    prev = _prev_ok(scratchpad, repo)

    with mock.patch.object(phase_5_implement.subprocess, "run", side_effect=patched_run), \
         mock.patch.object(phase_5_implement.time, "sleep", return_value=None):
        result = _commit_red_tests(ctx, prev)

    assert result.status == "error", (
        f"Expected status='error' on FileNotFoundError, got {result.status!r}"
    )
    assert getattr(result, "error_code", None) == "E_GIT_OS_ERROR", (
        f"Expected error_code='E_GIT_OS_ERROR' on FileNotFoundError, "
        f"got {getattr(result, 'error_code', None)!r}. "
        "GREEN must add E_GIT_OS_ERROR branch in _commit_red_tests for outcome=='os_error'."
    )


# ─── Test 8: _commit_red_tests → E_GIT_TIMEOUT at git commit call site ──────


def test_53bb3810_commit_red_tests_returns_e_git_timeout_when_commit_step_times_out(tmp_path):
    """End-to-end: TimeoutExpired during the `git commit` call (NOT git add)
    inside _commit_red_tests must produce StepResult(status='error',
    error_code='E_GIT_TIMEOUT'). Pins the commit-site caller mapping at
    phase_5_implement.py lines 670-680, separately from the git-add site.

    A GREEN that only maps the add-site if-chain and leaves the commit-site
    if-chain unmapped would still propagate TimeoutExpired here.

    FAILS today: TimeoutExpired propagates out of phase_5.
    """
    import phase_5_implement  # noqa: PLC0415
    from phase_5_implement import _commit_red_tests  # noqa: PLC0415

    repo = _minimal_repo(tmp_path)
    scratchpad = tmp_path / "scratch"

    test_file = repo / "tests" / "test_red.py"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text("def test_fails(): assert False\n")

    original_run = subprocess.run

    def patched_run(cmd, **kwargs):
        # Only the git commit call site raises; all other calls (incl. git add)
        # pass through to the real subprocess.run against the real repo.
        if isinstance(cmd, list) and cmd[:2] == ["git", "commit"]:
            raise subprocess.TimeoutExpired(cmd=cmd, timeout=30)
        return original_run(cmd, **kwargs)

    ctx = _make_ctx(scratchpad, git_cwd=str(repo))
    prev = _prev_ok(scratchpad, repo)

    with mock.patch.object(phase_5_implement.subprocess, "run", side_effect=patched_run), \
         mock.patch.object(phase_5_implement.time, "sleep", return_value=None):
        result = _commit_red_tests(ctx, prev)

    assert result.status == "error", (
        f"Expected status='error' on git commit timeout, got {result.status!r}"
    )
    assert getattr(result, "error_code", None) == "E_GIT_TIMEOUT", (
        f"Expected error_code='E_GIT_TIMEOUT' at git commit call site, "
        f"got {getattr(result, 'error_code', None)!r}. "
        "GREEN must map outcome=='timeout' for the git commit call site (lines 670-680), "
        "not just the git add site."
    )
    err_msg = (getattr(result, "error", "") or "").lower()
    assert "commit" in err_msg, (
        f"Expected error message to mention 'commit' (so we know the commit-site "
        f"branch fired, not the add-site branch); got: {err_msg!r}"
    )


# ─── Test 9: _commit_red_tests → E_GIT_OS_ERROR at git commit call site ─────


def test_53bb3810_commit_red_tests_returns_e_git_os_error_when_commit_step_raises_oserror(tmp_path):
    """End-to-end: OSError during the `git commit` call (NOT git add) inside
    _commit_red_tests must produce StepResult(status='error',
    error_code='E_GIT_OS_ERROR'). Pins the commit-site caller mapping
    separately from the add-site.

    FAILS today: FileNotFoundError propagates out of phase_5.
    """
    import phase_5_implement  # noqa: PLC0415
    from phase_5_implement import _commit_red_tests  # noqa: PLC0415

    repo = _minimal_repo(tmp_path)
    scratchpad = tmp_path / "scratch"

    test_file = repo / "tests" / "test_red.py"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text("def test_fails(): assert False\n")

    original_run = subprocess.run

    def patched_run(cmd, **kwargs):
        if isinstance(cmd, list) and cmd[:2] == ["git", "commit"]:
            raise FileNotFoundError("git not found")
        return original_run(cmd, **kwargs)

    ctx = _make_ctx(scratchpad, git_cwd=str(repo))
    prev = _prev_ok(scratchpad, repo)

    with mock.patch.object(phase_5_implement.subprocess, "run", side_effect=patched_run), \
         mock.patch.object(phase_5_implement.time, "sleep", return_value=None):
        result = _commit_red_tests(ctx, prev)

    assert result.status == "error", (
        f"Expected status='error' on git commit FileNotFoundError, got {result.status!r}"
    )
    assert getattr(result, "error_code", None) == "E_GIT_OS_ERROR", (
        f"Expected error_code='E_GIT_OS_ERROR' at git commit call site, "
        f"got {getattr(result, 'error_code', None)!r}. "
        "GREEN must map outcome=='os_error' for the git commit call site (lines 670-680), "
        "not just the git add site."
    )
    err_msg = (getattr(result, "error", "") or "").lower()
    assert "commit" in err_msg, (
        f"Expected error message to mention 'commit' (so we know the commit-site "
        f"branch fired, not the add-site branch); got: {err_msg!r}"
    )


# ─── Test 7: preservation — lock contention path unchanged ──────────────────


def test_53bb3810_lock_contention_path_unchanged(tmp_path):
    """Existing retry path for index.lock contention MUST keep working.
    Patch subprocess.run so it returns rc=1 with index.lock stderr 3 times.
    Function must call subprocess.run 3 times and return
    (last_result, "lock_persisted").

    Should PASS today (preservation guard).
    """
    import phase_5_implement  # noqa: PLC0415

    repo = _minimal_repo(tmp_path)

    LOCK_STDERR = "fatal: Unable to create '.git/index.lock': File exists"

    class LockResult:
        returncode = 1
        stderr = LOCK_STDERR
        stdout = ""

    run_mock = mock.Mock(return_value=LockResult())

    with mock.patch.object(phase_5_implement.subprocess, "run", run_mock), \
         mock.patch.object(phase_5_implement.time, "sleep", return_value=None):
        last_result, outcome = phase_5_implement._git_op_with_lock_retry(
            ["git", "add", "."], cwd=str(repo), timeout=30
        )

    assert outcome == "lock_persisted", (
        f"Expected outcome='lock_persisted' after 3 lock-error returns, got {outcome!r}"
    )
    assert run_mock.call_count == 3, (
        f"Expected 3 subprocess.run attempts on lock contention, got {run_mock.call_count}"
    )
    assert last_result is not None and last_result.returncode == 1
