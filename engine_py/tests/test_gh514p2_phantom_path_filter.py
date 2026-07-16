"""RED tests for GH514(2) — phantom-deleted-path filter before `git add`.

Spec: SHARED/memory/Decisions/2026-07-10_GH514P2_phantom_deleted_path_filter_spec.md

`_filter_phantom_deleted_paths` does not exist yet in `phase_workflows_common`.
Unit-level tests (AC1-5) import it INSIDE the test body — ImportError there is
a clean assert-time FAIL, never a collection-time hang (D1CF5FDF). E2E tests
(AC6-8) exercise the *current* wiring of `_commit_red_tests`/`_commit_green_code`,
which does not yet call the new filter — they FAIL today because a phantom path
in the manifest makes `git add` error atomically (E_GIT_COMMIT_FAILED).

Do NOT implement the contract here — RED-only file. UUT is never mocked (§1l);
only `_emit_safe` / `git_port.git_read` MAY be patched to capture/spy calls.

§1i: no singleton/time-dependent resources; N/A.
§1q: sys.path is provided by conftest.py; no module-level sys.path.insert here.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import phase_workflows_common  # noqa: E402  — plain import, symbol exists at collection time
import phase_5_implement  # noqa: E402


# ─── shared git-repo fixtures ─────────────────────────────────────────────────


def _init_git_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)


def _commit_file(repo: Path, relpath: str, body: str = "x\n") -> None:
    p = repo / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    subprocess.run(["git", "add", relpath], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", f"add {relpath}"], cwd=repo, check=True)


def _commit_deletion(repo: Path, relpath: str) -> None:
    """Delete an already-committed file and commit the deletion (phantom path)."""
    (repo / relpath).unlink()
    subprocess.run(["git", "add", relpath], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", f"delete {relpath}"], cwd=repo, check=True)


def _uncommitted_delete(repo: Path, relpath: str) -> None:
    """Delete a committed file WITHOUT staging/committing (still tracked)."""
    (repo / relpath).unlink()


def _real_repo(tmp_path: Path) -> Path:
    repo = Path(tmp_path).resolve() / "repo"  # §1j: resolve() avoids /var/folders symlink mismatch
    repo.mkdir()
    _init_git_repo(repo)
    return repo


# ─── AC1 — committed deletion is dropped; event emitted once ────────────────


def test_ac1_committed_deletion_dropped_and_event_emitted(tmp_path: Path) -> None:
    """AC1: absent+untracked path is filtered out; _emit_safe called once with
    commit_phantom_paths_skipped, payload paths==[<p>]."""
    from phase_workflows_common import _filter_phantom_deleted_paths  # ImportError at RED → FAIL

    repo = _real_repo(tmp_path)
    _commit_file(repo, "src/gone.py")
    _commit_deletion(repo, "src/gone.py")

    captured: list[tuple] = []
    with patch.object(
        phase_workflows_common,
        "_emit_safe",
        side_effect=lambda et, payload, **kw: captured.append((et, payload)),
    ):
        result = _filter_phantom_deleted_paths(["src/gone.py"], str(repo))

    assert result == [], f"phantom path must be dropped; got {result!r}"
    events = [e for e in captured if e[0] == "commit_phantom_paths_skipped"]
    assert len(events) == 1, f"expected exactly 1 event, got {captured}"
    assert events[0][1].get("paths") == ["src/gone.py"], (
        f"payload paths must equal ['src/gone.py']; got {events[0][1]}"
    )


# ─── AC2 — present-on-disk path kept, no event ──────────────────────────────


def test_ac2_present_on_disk_path_kept_no_event(tmp_path: Path) -> None:
    """AC2: path existing on disk (even untracked-new) is kept; no event emitted."""
    from phase_workflows_common import _filter_phantom_deleted_paths  # ImportError at RED → FAIL

    repo = _real_repo(tmp_path)
    (repo / "new_untracked.py").write_text("new\n")

    captured: list[tuple] = []
    with patch.object(
        phase_workflows_common,
        "_emit_safe",
        side_effect=lambda et, payload, **kw: captured.append((et, payload)),
    ):
        result = _filter_phantom_deleted_paths(["new_untracked.py"], str(repo))

    assert result == ["new_untracked.py"], f"present path must be kept; got {result!r}"
    assert not captured, f"no event expected; got {captured}"


# ─── AC3 — absent-but-tracked (uncommitted deletion) kept, no event ─────────


def test_ac3_uncommitted_deletion_kept_no_event(tmp_path: Path) -> None:
    """AC3: absent-but-tracked path (deletion NOT yet committed) is kept; no event."""
    from phase_workflows_common import _filter_phantom_deleted_paths  # ImportError at RED → FAIL

    repo = _real_repo(tmp_path)
    _commit_file(repo, "src/staged_del.py")
    _uncommitted_delete(repo, "src/staged_del.py")

    captured: list[tuple] = []
    with patch.object(
        phase_workflows_common,
        "_emit_safe",
        side_effect=lambda et, payload, **kw: captured.append((et, payload)),
    ):
        result = _filter_phantom_deleted_paths(["src/staged_del.py"], str(repo))

    assert result == ["src/staged_del.py"], (
        f"tracked-but-absent path must be kept; got {result!r}"
    )
    assert not captured, f"no event expected; got {captured}"


# ─── AC4 — empty input → [] and zero git_port.git_read calls ────────────────


def test_ac4_empty_input_returns_empty_zero_git_read_calls(tmp_path: Path) -> None:
    """AC4: empty input list → [] returned, zero git_port.git_read calls."""
    from phase_workflows_common import _filter_phantom_deleted_paths  # ImportError at RED → FAIL

    repo = _real_repo(tmp_path)

    with patch.object(phase_workflows_common.git_port, "git_read") as spy:
        result = _filter_phantom_deleted_paths([], str(repo))

    assert result == [], f"empty input must return []; got {result!r}"
    assert spy.call_count == 0, f"expected 0 git_read calls, got {spy.call_count}"


# ─── AC5 — ls-files failure → all paths returned, no event (degraded) ───────


def test_ac5_ls_files_failure_returns_all_paths_no_event(tmp_path: Path) -> None:
    """AC5: ls-files failure (rc!=0, e.g. git_cwd not a repo) → all paths
    returned unchanged, no event."""
    from phase_workflows_common import _filter_phantom_deleted_paths  # ImportError at RED → FAIL

    non_repo = Path(tmp_path).resolve() / "not_a_repo"
    non_repo.mkdir()
    # absent path so `missing` is non-empty and ls-files is actually invoked
    missing_path = "some/missing/path.py"

    captured: list[tuple] = []
    with patch.object(
        phase_workflows_common,
        "_emit_safe",
        side_effect=lambda et, payload, **kw: captured.append((et, payload)),
    ):
        result = _filter_phantom_deleted_paths([missing_path], str(non_repo))

    assert result == [missing_path], (
        f"degraded branch must return all paths unchanged; got {result!r}"
    )
    assert not captured, f"no event expected on ls-files failure; got {captured}"


# ─── AC6 — E2E _commit_red_tests: phantom + real new test → status ok ──────


def test_ac6_commit_red_tests_e2e_phantom_plus_real_path_status_ok(
    tmp_path: Path, monkeypatch
) -> None:
    """AC6: _commit_red_tests with red_test_paths=[phantom, real_new_test] in a
    repo carrying a committed deletion → status=='ok', no E_GIT_COMMIT_FAILED,
    real file committed (visible in `git show --name-only HEAD`), phantom
    absent from the commit.

    Today: current wiring calls only `_filter_gitignored_paths` (no phantom
    filter) → `git add -- <phantom> <real>` fails atomically →
    status=='error', error_code=='E_GIT_COMMIT_FAILED'. FAILS at RED.
    """
    from contracts import StepResult, WorkflowContext  # type: ignore

    repo = _real_repo(tmp_path)
    _commit_file(repo, "src/phantom.py")
    _commit_deletion(repo, "src/phantom.py")

    real_new_test = "tests/test_real_new_gh514p2.py"
    (repo / real_new_test).parent.mkdir(parents=True, exist_ok=True)
    (repo / real_new_test).write_text("def test_x(): assert True\n")

    ctx = WorkflowContext(
        tenant_id="hal", scope=None, db_path=None,
        org_config={"scratchpad_dir": str(repo / ".scratch"), "git_cwd": str(repo)},
        question="q", session_id="s", persona="hal", framework=None, domain=None,
    )
    prev = StepResult(
        status="ok",
        data={"cycle": 1, "spec_path": None},
        duration_ms=0,
        step_name="write_red_tests",
    )

    with patch.object(
        phase_5_implement,
        "_derive_red_paths_via_git_diff",
        return_value=["src/phantom.py", real_new_test],
    ):
        result = phase_5_implement._commit_red_tests(ctx, prev)

    assert result.status == "ok", (
        f"expected status='ok', got {result.status!r} "
        f"error_code={getattr(result, 'error_code', None)!r} error={getattr(result, 'error', None)!r}"
    )
    assert getattr(result, "error_code", None) != "E_GIT_COMMIT_FAILED"

    show = subprocess.run(
        ["git", "show", "--name-only", "--pretty=format:", "HEAD"],
        capture_output=True, text=True, cwd=repo, check=True,
    )
    committed_paths = {ln.strip() for ln in show.stdout.splitlines() if ln.strip()}
    assert real_new_test in committed_paths, (
        f"real new test must be committed; got {committed_paths}"
    )
    assert "src/phantom.py" not in committed_paths, (
        f"phantom path must not appear in the new commit; got {committed_paths}"
    )


# ─── AC7 — E2E _commit_red_tests: all paths phantom → degraded skip ────────


def test_ac7_commit_red_tests_e2e_all_phantom_degraded_skip(
    tmp_path: Path, monkeypatch
) -> None:
    """AC7: all red_test_paths phantom → status=='ok',
    red_commit_sha == pre_red_sha (degraded skip, no new commit).

    Today: `git add -- <phantom>` fails atomically → status=='error',
    error_code=='E_GIT_COMMIT_FAILED'. FAILS at RED.
    """
    from contracts import StepResult, WorkflowContext  # type: ignore

    repo = _real_repo(tmp_path)
    _commit_file(repo, "src/phantom2.py")
    _commit_deletion(repo, "src/phantom2.py")
    # Unrelated untracked dirt so `git status --porcelain` is non-empty and
    # _commit_red_tests reaches the git-add branch instead of skipping early.
    (repo / "dirt.txt").write_text("dirt\n")

    pre_red_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=repo, check=True,
    ).stdout.strip()

    ctx = WorkflowContext(
        tenant_id="hal", scope=None, db_path=None,
        org_config={"scratchpad_dir": str(repo / ".scratch"), "git_cwd": str(repo)},
        question="q", session_id="s", persona="hal", framework=None, domain=None,
    )
    prev = StepResult(
        status="ok",
        data={"cycle": 1, "spec_path": None},
        duration_ms=0,
        step_name="write_red_tests",
    )

    with patch.object(
        phase_5_implement,
        "_derive_red_paths_via_git_diff",
        return_value=["src/phantom2.py"],
    ):
        result = phase_5_implement._commit_red_tests(ctx, prev)

    assert result.status == "ok", (
        f"expected status='ok', got {result.status!r} "
        f"error_code={getattr(result, 'error_code', None)!r} error={getattr(result, 'error', None)!r}"
    )
    assert result.data.get("red_commit_sha") == pre_red_sha, (
        f"degraded skip must leave red_commit_sha==pre_red_sha={pre_red_sha!r}; "
        f"got {result.data.get('red_commit_sha')!r}"
    )


# ─── AC8 — E2E _commit_green_code: phantom + real prod path → status ok ────


def test_ac8_commit_green_code_e2e_phantom_plus_real_prod_path_status_ok(
    tmp_path: Path, monkeypatch
) -> None:
    """AC8: manifest contains phantom prod path + real prod file → status=='ok',
    real file committed, phantom skipped.

    Today: `_filter_gitignored_paths` alone doesn't drop the phantom → `git add`
    fails atomically → status=='error', error_code=='E_GIT_COMMIT_FAILED'.
    FAILS at RED.
    """
    monkeypatch.setenv("HAL_AUTHORED_BOUNDARY_GATE", "0")
    from contracts import StepResult, WorkflowContext  # type: ignore

    repo = _real_repo(tmp_path)
    _commit_file(repo, "src/phantom_prod.py")
    red_commit_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=repo, check=True,
    ).stdout.strip()
    _commit_deletion(repo, "src/phantom_prod.py")

    real_prod_path = "src/real_impl_gh514p2.py"
    (repo / real_prod_path).parent.mkdir(parents=True, exist_ok=True)
    (repo / real_prod_path).write_text("x = 1\n")

    ctx = WorkflowContext(
        tenant_id="hal", scope=None, db_path=None,
        org_config={"scratchpad_dir": str(repo / ".scratch"), "git_cwd": str(repo)},
        question="q", session_id="s", persona="hal", framework=None, domain=None,
    )
    prev = StepResult(
        status="ok",
        data={
            "cycle": 1,
            "red_commit_sha": red_commit_sha,
            "worker_written_paths": ["src/phantom_prod.py", real_prod_path],
            "manifest_source": "harness_tool_record",
        },
        duration_ms=0,
        step_name="commit_red_tests",
    )

    result = phase_5_implement._commit_green_code(ctx, prev)

    assert result.status == "ok", (
        f"expected status='ok', got {result.status!r} "
        f"error_code={getattr(result, 'error_code', None)!r} error={getattr(result, 'error', None)!r}"
    )

    show = subprocess.run(
        ["git", "show", "--name-only", "--pretty=format:", "HEAD"],
        capture_output=True, text=True, cwd=repo, check=True,
    )
    committed_paths = {ln.strip() for ln in show.stdout.splitlines() if ln.strip()}
    assert real_prod_path in committed_paths, (
        f"real prod path must be committed; got {committed_paths}"
    )
    assert "src/phantom_prod.py" not in committed_paths, (
        f"phantom prod path must not appear in the new commit; got {committed_paths}"
    )
