"""RED tests for 8FE3D757 — phase_6 _commit_fix_tests (fix-worker test-patch commit gap).

Contract: after _commit_fix_code, a new sibling step `commit_fix_tests`:
  - is registered in phase_6_review_workflow() between commit_fix_code
    and run_pytest_post_fix
  - applies the same test-env guard as _commit_fix_code (skip outside scratchpad)
  - resolves SHA boundary from prev.data["pre_fix_sha"] or resolve_pre_phase_sha fallback
  - enumerates test paths via git_diff_files filtered by _is_test_path
  - no-ops cleanly when no test paths exist
  - commits ONLY test paths (prod paths left untouched) in a single commit
  - uses _build_fix_test_commit_message for the commit message
  - persists fix_test_commit_sha to <scratchpad>/integrity/fix-test-commit-sha.txt
  - emits fix_test_commit telemetry with n_files, paths, commit_sha, cycle, phase
  - applies the same git add / commit error ladder as _commit_fix_code
    (E_GIT_LOCKED, E_FIX_TEST_COMMIT_FAILED, E_GIT_TIMEOUT, E_GIT_OS_ERROR)
  - gracefully skips when git_diff_files raises RuntimeError / OSError

All tests MUST FAIL until GREEN agent implements _commit_fix_tests and
_build_fix_test_commit_message in phase_6_review.py and registers commit_fix_tests
in phase_6_review_workflow().

Do NOT implement the contract here — RED-only file.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

HERE = Path(__file__).parent
ENGINE_ROOT = HERE.parent
sys.path.insert(0, str(ENGINE_ROOT))

from bytedigger_engine.workflows import phase_6_review as _p6  # noqa: E402  — top-level alias; bound functions resolve names here
from bytedigger_engine.workflows.phase_6_review import (  # noqa: E402
    _commit_fix_tests,
    _build_fix_test_commit_message,
    phase_6_review_workflow,
)
from bytedigger_engine.contracts import WorkflowContext, StepResult  # noqa: E402


# ─── helpers ──────────────────────────────────────────────────────────────────

_VALID_SHA = "a" * 40


def _make_ctx(tmp_path: Path, **cfg_extra) -> WorkflowContext:
    """Build a minimal WorkflowContext with scratchpad + git_cwd inside tmp_path."""
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    (scratchpad / "reviews").mkdir(parents=True, exist_ok=True)
    git_cwd = tmp_path / "repo"
    git_cwd.mkdir(parents=True, exist_ok=True)
    org = {
        "scratchpad_dir": str(scratchpad),
        "git_cwd": str(git_cwd),
        **cfg_extra,
    }
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question="commit_fix_tests test",
        session_id="test-session-8FE3D757",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_prev(pre_fix_sha=_VALID_SHA, cycle: int = 1, **extra) -> MagicMock:
    """Minimal prev StepResult-like object with data dict."""
    prev = MagicMock()
    data: dict = {"cycle": cycle, **extra}
    if pre_fix_sha is not None:
        data["pre_fix_sha"] = pre_fix_sha
    prev.data = data
    return prev


def _init_git_repo(repo_dir: Path) -> str:
    """Init a git repo, add an initial commit, and return the initial HEAD SHA."""
    subprocess.run(
        ["git", "init", "-q", "--initial-branch=main"],
        cwd=repo_dir, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "test@hal.test"],
        cwd=repo_dir, check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "HAL Test"],
        cwd=repo_dir, check=True, capture_output=True,
    )
    baseline = repo_dir / "baseline.py"
    baseline.write_text("# baseline\n", encoding="utf-8")
    subprocess.run(["git", "add", "baseline.py"], cwd=repo_dir, check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-q", "-m", "initial commit"],
        cwd=repo_dir, check=True, capture_output=True,
    )
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_dir, check=True, capture_output=True, text=True,
    )
    return result.stdout.strip()


def _captured_emits(monkeypatch) -> list:
    """Attach a spy to _p6._emit_safe and return the captured list."""
    emitted: list = []

    def _fake_emit(event_type: str, payload: dict) -> None:
        emitted.append((event_type, payload))

    monkeypatch.setattr(_p6, "_emit_safe", _fake_emit)
    return emitted


# ═══════════════════════════════════════════════════════════════════════════════
# AC1 — step registered between commit_fix_code and run_pytest_post_fix
# ═══════════════════════════════════════════════════════════════════════════════


def test_8fe3d757_ac1_step_registered_between_commit_fix_code_and_post_fix_pytest() -> None:
    """commit_fix_tests must appear in workflow immediately after commit_fix_code
    and immediately before run_pytest_post_fix. AC1: step ordering invariant."""
    workflow = phase_6_review_workflow()
    step_names = [s.name for s in workflow.steps]

    assert "commit_fix_tests" in step_names, (
        f"'commit_fix_tests' not found in workflow steps: {step_names!r}"
    )
    assert "commit_fix_code" in step_names
    assert "run_pytest_post_fix" in step_names

    idx_code = step_names.index("commit_fix_code")
    idx_tests = step_names.index("commit_fix_tests")
    idx_pytest = step_names.index("run_pytest_post_fix")

    assert idx_tests == idx_code + 1, (
        f"commit_fix_tests ({idx_tests}) must immediately follow "
        f"commit_fix_code ({idx_code}); got order: {step_names}"
    )
    assert idx_pytest == idx_tests + 1, (
        f"run_pytest_post_fix ({idx_pytest}) must immediately follow "
        f"commit_fix_tests ({idx_tests}); got order: {step_names}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC2 — test-env guard: git_cwd outside scratchpad → skip
# ═══════════════════════════════════════════════════════════════════════════════


def test_8fe3d757_ac2_test_env_guard_outside_scratchpad(tmp_path, monkeypatch) -> None:
    """When cfg['git_cwd'] is absent AND cwd resolves to a non-repo dir →
    emit fix_test_commit_skipped(reason='no_git_repo') and return ok with
    fix_test_commit_sha=None. AC2: test-env guard (3CE7007C R5 contract update).

    R5 change: reason was 'git_cwd_outside_scratchpad'; now 'no_git_repo' because
    _is_synthetic_test_env uses git rev-parse probe instead of scratchpad-path check.
    monkeypatch.chdir(nonrepo) ensures the defaulted git_cwd is a non-repo path so
    _is_synthetic_test_env returns True regardless of where pytest itself runs.
    """
    emitted = _captured_emits(monkeypatch)

    # Pre-stage a non-repo dir and chdir to it so git_cwd defaults to a non-repo path.
    # §1i: resource pre-staged deterministically, no race.
    nonrepo = tmp_path / "nonrepo_ac2"
    nonrepo.mkdir(parents=True, exist_ok=True)
    monkeypatch.chdir(nonrepo)

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    ctx = WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={"scratchpad_dir": str(scratchpad)},  # no git_cwd key
        question="guard test",
        session_id="test-session-8FE3D757-ac2",
        persona="hal",
        framework=None,
        domain=None,
    )
    prev = _make_prev(pre_fix_sha=_VALID_SHA, cycle=1)

    result = _commit_fix_tests(ctx, prev)

    assert result.status == "ok", f"expected ok, got {result.status!r} ({result.error!r})"
    assert result.data is not None
    assert result.data.get("fix_test_commit_sha") is None, (
        f"fix_test_commit_sha should be None; got {result.data.get('fix_test_commit_sha')!r}"
    )

    guard_events = [
        (et, p) for et, p in emitted
        if et == "fix_test_commit_skipped"
        and p.get("reason") == "no_git_repo"
    ]
    assert guard_events, (
        f"Expected fix_test_commit_skipped(reason='no_git_repo') event; "
        f"got: {emitted!r}"
    )
    assert guard_events[0][1].get("phase") == 6


# ═══════════════════════════════════════════════════════════════════════════════
# AC3 — SHA boundary resolution (three sub-tests)
# ═══════════════════════════════════════════════════════════════════════════════


def test_8fe3d757_ac3a_pre_fix_sha_from_prev(tmp_path, monkeypatch) -> None:
    """AC3a: prev.data['pre_fix_sha'] is valid 40-char hex → used directly;
    resolve_pre_phase_sha is NOT called.
    4961254A: git_diff_files is no longer called; empty manifest → no-test-paths skip → ok."""
    _captured_emits(monkeypatch)

    resolve_called = []
    monkeypatch.setattr(_p6, "resolve_pre_phase_sha", lambda cwd: resolve_called.append(cwd) or (_ for _ in ()).throw(AssertionError("should not be called")))

    ctx = _make_ctx(tmp_path)
    prev = _make_prev(pre_fix_sha=_VALID_SHA, cycle=1,
                      worker_written_paths=[], manifest_source="harness_tool_record")  # 4C03CCED Ship 1C
    result = _commit_fix_tests(ctx, prev)

    assert result.status == "ok", f"expected ok; got {result.status!r} ({result.error!r})"
    assert not resolve_called, "resolve_pre_phase_sha should not be called when prev has valid SHA"


def test_8fe3d757_ac3b_pre_fix_sha_fallback_resolve(tmp_path, monkeypatch) -> None:
    """AC3b: prev.data['pre_fix_sha'] is absent / invalid → resolve_pre_phase_sha
    is called and its return value is used.
    4961254A: git_diff_files is no longer called; empty manifest → no-test-paths skip → ok."""
    _captured_emits(monkeypatch)

    fallback_sha = "b" * 40
    resolve_called = []

    def _fake_resolve(cwd):
        resolve_called.append(cwd)
        return fallback_sha

    monkeypatch.setattr(_p6, "resolve_pre_phase_sha", _fake_resolve)

    ctx = _make_ctx(tmp_path)
    prev = _make_prev(pre_fix_sha=None, cycle=1,
                      worker_written_paths=[], manifest_source="harness_tool_record")  # 4C03CCED Ship 1C
    result = _commit_fix_tests(ctx, prev)

    assert result.status == "ok", f"expected ok; got {result.status!r} ({result.error!r})"
    assert resolve_called, "resolve_pre_phase_sha should have been called"


def test_8fe3d757_ac3c_both_missing_returns_error(tmp_path, monkeypatch) -> None:
    """AC3c: prev.data['pre_fix_sha'] invalid AND resolve_pre_phase_sha raises
    → result.status == 'error' with error_code E_MISSING_FIX_BOUNDARY."""
    _captured_emits(monkeypatch)

    def _raise(cwd):
        raise RuntimeError("no git repo")

    monkeypatch.setattr(_p6, "resolve_pre_phase_sha", _raise)

    ctx = _make_ctx(tmp_path)
    prev = _make_prev(pre_fix_sha="not-a-valid-sha", cycle=1)
    result = _commit_fix_tests(ctx, prev)

    assert result.status == "error", f"expected error; got {result.status!r}"
    assert result.error_code == "E_MISSING_FIX_BOUNDARY", (
        f"expected E_MISSING_FIX_BOUNDARY; got {result.error_code!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC4 — no-op when no test paths (only prod paths in diff)
# ═══════════════════════════════════════════════════════════════════════════════


def test_8fe3d757_ac4_noop_when_no_test_paths(tmp_path, monkeypatch) -> None:
    """AC4: git_diff_files returns only prod paths → filter leaves empty test_paths
    → emit fix_test_commit_skipped(reason='no_test_paths') + ok + fix_test_commit_sha=None.
    Real-git fixture; assert HEAD unchanged after call."""
    emitted = _captured_emits(monkeypatch)

    repo = tmp_path / "repo"
    repo.mkdir()
    pre_fix_sha = _init_git_repo(repo)

    # Write a prod-only change (not a test file)
    (repo / "src").mkdir()
    (repo / "src" / "demo.py").write_text("x = 1\n", encoding="utf-8")

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    ctx = WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={
            "scratchpad_dir": str(scratchpad),
            "git_cwd": str(repo),
        },
        question="no-test-paths test",
        session_id="test-session-8FE3D757-ac4",
        persona="hal",
        framework=None,
        domain=None,
    )
    prev = _make_prev(pre_fix_sha=pre_fix_sha, cycle=1,
                      worker_written_paths=[], manifest_source="harness_tool_record")  # 4C03CCED Ship 1C

    head_before = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True
    ).stdout.strip()

    result = _commit_fix_tests(ctx, prev)

    head_after = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True
    ).stdout.strip()

    assert result.status == "ok", f"expected ok; got {result.status!r} ({result.error!r})"
    assert result.data.get("fix_test_commit_sha") is None, (
        f"fix_test_commit_sha should be None; got {result.data.get('fix_test_commit_sha')!r}"
    )
    assert head_before == head_after, "HEAD should not change when no test paths committed"

    noop_events = [
        (et, p) for et, p in emitted
        if et == "fix_test_commit_skipped" and p.get("reason") == "no_test_paths"
    ]
    assert noop_events, (
        f"Expected fix_test_commit_skipped(reason='no_test_paths'); got {emitted!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC5 — happy path: test file committed, prod file NOT committed
# ═══════════════════════════════════════════════════════════════════════════════


def test_8fe3d757_ac5_happy_path_test_committed_prod_not(tmp_path, monkeypatch) -> None:
    """AC5: manifest has test file + prod file → only test file staged + committed
    → fix_test_commit_sha populated → telemetry emitted with n_files==1, correct paths.
    4961254A: manifest drives selection (not git_diff_files).

    GH886 tail-autocommit contract: after _commit_fix_tests makes the fix-test
    commit, the remaining dirty tail (src/demo.py here) is auto-committed as a
    SECOND commit so the integrity gate never sees a dirty tree. So HEAD ends up
    ONE commit ahead of fix_test_commit_sha: fix_test_commit_sha == HEAD~1 (the
    fix-test commit); HEAD == the tail commit containing src/demo.py.
    """
    emitted = _captured_emits(monkeypatch)

    repo = tmp_path / "repo"
    repo.mkdir()
    pre_fix_sha = _init_git_repo(repo)

    # Create test file and prod file AFTER the baseline commit
    (repo / "tests").mkdir()
    test_file = repo / "tests" / "test_demo.py"
    test_file.write_text("def test_x(): pass\n", encoding="utf-8")
    (repo / "src").mkdir()
    prod_file = repo / "src" / "demo.py"
    prod_file.write_text("x = 1\n", encoding="utf-8")

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    ctx = WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={
            "scratchpad_dir": str(scratchpad),
            "git_cwd": str(repo),
        },
        question="happy path test",
        session_id="test-session-8FE3D757-ac5",
        persona="hal",
        framework=None,
        domain=None,
    )
    # 4961254A: manifest includes both; _is_test_path filters to only test file
    prev = _make_prev(
        pre_fix_sha=pre_fix_sha, cycle=1,
        worker_written_paths=["tests/test_demo.py", "src/demo.py"],
        manifest_source="harness_tool_record",  # 4C03CCED Ship 1C
    )

    head_before = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True
    ).stdout.strip()

    result = _commit_fix_tests(ctx, prev)

    head_after = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True
    ).stdout.strip()

    assert result.status == "ok", f"expected ok; got {result.status!r} ({result.error!r})"
    new_sha = result.data.get("fix_test_commit_sha")
    assert new_sha is not None, "fix_test_commit_sha should be populated after successful commit"
    assert head_before != head_after, "HEAD should have advanced after test-file commit"

    # GH886: fix_test_commit_sha is the fix-test commit, HEAD~1 relative to the
    # auto-committed tail commit.
    head_parent = subprocess.run(
        ["git", "rev-parse", "HEAD~1"], cwd=repo, capture_output=True, text=True,
    ).stdout.strip()
    assert new_sha == head_parent, (
        f"fix_test_commit_sha {new_sha!r} != HEAD~1 {head_parent!r} (GH886 tail-autocommit contract)"
    )

    # Test file must be in the fix-test commit's tree
    tree_files = subprocess.run(
        ["git", "diff-tree", "--no-commit-id", "-r", "--name-only", new_sha],
        cwd=repo, capture_output=True, text=True,
    ).stdout.strip().splitlines()
    assert any("test_demo.py" in f for f in tree_files), (
        f"test_demo.py not found in commit tree; tree: {tree_files!r}"
    )

    # Prod file must NOT be in the fix-test commit
    assert not any("demo.py" in f and "test" not in f for f in tree_files), (
        f"prod file should NOT be in test commit; tree: {tree_files!r}"
    )

    # GH886: the tail commit (HEAD) must contain the auto-committed prod file.
    tail_files = subprocess.run(
        ["git", "diff-tree", "--no-commit-id", "-r", "--name-only", head_after],
        cwd=repo, capture_output=True, text=True,
    ).stdout.strip().splitlines()
    assert any("src/demo.py" in f for f in tail_files), (
        f"src/demo.py not found in tail commit tree; tree: {tail_files!r}"
    )

    # Telemetry
    commit_events = [(et, p) for et, p in emitted if et == "fix_test_commit"]
    assert commit_events, f"Expected fix_test_commit event; got {emitted!r}"
    payload = commit_events[0][1]
    assert payload.get("n_files") == 1, f"n_files should be 1; got {payload.get('n_files')!r}"
    assert payload.get("commit_sha") == new_sha
    assert payload.get("cycle") == 1
    assert payload.get("phase") == 6
    path_list = payload.get("paths", [])
    assert any("test_demo.py" in str(p) for p in path_list), (
        f"test_demo.py not in paths; got {path_list!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC6 — two test files committed in ONE commit
# ═══════════════════════════════════════════════════════════════════════════════


def test_8fe3d757_ac6_two_test_files_single_commit(tmp_path, monkeypatch) -> None:
    """AC6: two test files in manifest → both committed in ONE commit; n_files==2;
    commit message body lists both.
    4961254A: manifest drives selection."""
    emitted = _captured_emits(monkeypatch)

    repo = tmp_path / "repo"
    repo.mkdir()
    pre_fix_sha = _init_git_repo(repo)

    (repo / "tests").mkdir()
    (repo / "tests" / "test_a.py").write_text("def test_a(): pass\n", encoding="utf-8")
    (repo / "tests" / "test_b.py").write_text("def test_b(): pass\n", encoding="utf-8")

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    ctx = WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={
            "scratchpad_dir": str(scratchpad),
            "git_cwd": str(repo),
        },
        question="two files test",
        session_id="test-session-8FE3D757-ac6",
        persona="hal",
        framework=None,
        domain=None,
    )
    # 4961254A: manifest includes both test files
    prev = _make_prev(
        pre_fix_sha=pre_fix_sha, cycle=2,
        worker_written_paths=["tests/test_a.py", "tests/test_b.py"],
        manifest_source="harness_tool_record",  # 4C03CCED Ship 1C
    )
    result = _commit_fix_tests(ctx, prev)

    assert result.status == "ok", f"expected ok; got {result.status!r} ({result.error!r})"

    commit_events = [(et, p) for et, p in emitted if et == "fix_test_commit"]
    assert commit_events, f"Expected fix_test_commit event; got {emitted!r}"
    payload = commit_events[0][1]
    assert payload.get("n_files") == 2, f"n_files should be 2; got {payload.get('n_files')!r}"

    # Verify single new commit contains both files
    new_sha = result.data.get("fix_test_commit_sha")
    tree_files = subprocess.run(
        ["git", "diff-tree", "--no-commit-id", "-r", "--name-only", new_sha],
        cwd=repo, capture_output=True, text=True,
    ).stdout.strip().splitlines()
    assert any("test_a.py" in f for f in tree_files), f"test_a.py missing; tree: {tree_files!r}"
    assert any("test_b.py" in f for f in tree_files), f"test_b.py missing; tree: {tree_files!r}"

    # Commit message body lists both paths
    commit_msg = subprocess.run(
        ["git", "log", "--format=%B", "-1", new_sha],
        cwd=repo, capture_output=True, text=True,
    ).stdout
    assert "test_a.py" in commit_msg, f"test_a.py missing from commit message: {commit_msg!r}"
    assert "test_b.py" in commit_msg, f"test_b.py missing from commit message: {commit_msg!r}"


# ═══════════════════════════════════════════════════════════════════════════════
# AC7 — commit message helper
# ═══════════════════════════════════════════════════════════════════════════════


def test_8fe3d757_ac7_commit_message_helper_format() -> None:
    """AC7: _build_fix_test_commit_message(cycle, test_paths) produces the expected format.
    First line: 'fix(test): cycle N — phase_6 fix-worker test patches'.
    Body lists each path."""
    msg = _build_fix_test_commit_message(
        cycle=2,
        test_paths=["tests/test_x.py", "tests/test_y.py"],
    )
    lines = msg.splitlines()
    assert lines[0] == "fix(test): cycle 2 — phase_6 fix-worker test patches", (
        f"First line mismatch: {lines[0]!r}"
    )
    assert "tests/test_x.py" in msg, f"test_x.py not in message body: {msg!r}"
    assert "tests/test_y.py" in msg, f"test_y.py not in message body: {msg!r}"


# ═══════════════════════════════════════════════════════════════════════════════
# AC8 — round-trip prev.data: fix_commit_sha + other_key preserved
# ═══════════════════════════════════════════════════════════════════════════════


def test_8fe3d757_ac8_prev_data_round_trip(tmp_path, monkeypatch) -> None:
    """AC8: prev.data keys (fix_commit_sha, other_key, cycle) are preserved in
    result.data; the spread {**prev.data, 'fix_test_commit_sha': ...} contract."""
    emitted = _captured_emits(monkeypatch)  # noqa: F841

    repo = tmp_path / "repo"
    repo.mkdir()
    pre_fix_sha = _init_git_repo(repo)

    # Only a prod file → no test paths → no-op path, but data should still round-trip
    (repo / "src").mkdir()
    (repo / "src" / "prod.py").write_text("x = 1\n", encoding="utf-8")

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    ctx = WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={
            "scratchpad_dir": str(scratchpad),
            "git_cwd": str(repo),
        },
        question="round-trip test",
        session_id="test-session-8FE3D757-ac8",
        persona="hal",
        framework=None,
        domain=None,
    )
    prev = MagicMock()
    prev.data = {
        "pre_fix_sha": pre_fix_sha,
        "fix_commit_sha": "abc" + "0" * 37,
        "cycle": 3,
        "other_key": "preserved",
        "worker_written_paths": [],           # 4C03CCED Ship 1C
        "manifest_source": "harness_tool_record",  # 4C03CCED Ship 1C
    }

    result = _commit_fix_tests(ctx, prev)

    assert result.status == "ok", f"expected ok; got {result.status!r} ({result.error!r})"
    assert result.data.get("fix_commit_sha") == "abc" + "0" * 37, (
        f"fix_commit_sha not preserved; got {result.data.get('fix_commit_sha')!r}"
    )
    assert result.data.get("other_key") == "preserved", (
        f"other_key not preserved; got {result.data.get('other_key')!r}"
    )
    assert result.data.get("cycle") == 3, (
        f"cycle not preserved; got {result.data.get('cycle')!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC9 — SHA written to integrity file
# ═══════════════════════════════════════════════════════════════════════════════


def test_8fe3d757_ac9_sha_persisted_to_integrity_file(tmp_path, monkeypatch) -> None:
    """AC9: after successful commit, fix_test_commit_sha is written to
    <scratchpad>/integrity/fix-test-commit-sha.txt.
    4961254A: manifest drives path selection."""
    emitted = _captured_emits(monkeypatch)  # noqa: F841

    repo = tmp_path / "repo"
    repo.mkdir()
    pre_fix_sha = _init_git_repo(repo)

    (repo / "tests").mkdir()
    (repo / "tests" / "test_persist.py").write_text("def test_p(): pass\n", encoding="utf-8")

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    ctx = WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={
            "scratchpad_dir": str(scratchpad),
            "git_cwd": str(repo),
        },
        question="persist sha test",
        session_id="test-session-8FE3D757-ac9",
        persona="hal",
        framework=None,
        domain=None,
    )
    # 4961254A: manifest drives selection
    prev = _make_prev(pre_fix_sha=pre_fix_sha, cycle=1, worker_written_paths=["tests/test_persist.py"],
                      manifest_source="harness_tool_record")  # 4C03CCED Ship 1C
    result = _commit_fix_tests(ctx, prev)

    assert result.status == "ok", f"expected ok; got {result.status!r} ({result.error!r})"
    new_sha = result.data.get("fix_test_commit_sha")
    assert new_sha is not None, "fix_test_commit_sha should be set after commit"

    integrity_file = scratchpad / "integrity" / "fix-test-commit-sha.txt"
    assert integrity_file.exists(), f"integrity file not created at {integrity_file}"
    persisted = integrity_file.read_text(encoding="utf-8").strip()
    assert persisted == new_sha, (
        f"Persisted SHA {persisted!r} != result SHA {new_sha!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC10 — git add error ladder
# ═══════════════════════════════════════════════════════════════════════════════

def _make_git_op_stub(add_outcome: str):
    """Return a _git_op_with_lock_retry replacement that returns add_outcome on
    the first call (git add) and success on any subsequent call (git commit)."""
    call_count = [0]
    fake_proc = SimpleNamespace(returncode=1, stdout="", stderr="fake-error")
    success_proc = SimpleNamespace(returncode=0, stdout="", stderr="")

    def _stub(cmd, cwd, timeout):
        call_count[0] += 1
        if call_count[0] == 1:
            return (fake_proc, add_outcome)
        return (success_proc, "success")

    return _stub


def _make_git_op_stub_commit(commit_outcome: str):
    """Return a _git_op_with_lock_retry replacement that returns success on the
    first call (git add) and commit_outcome on the second call (git commit)."""
    call_count = [0]
    fake_proc = SimpleNamespace(returncode=1, stdout="", stderr="fake-error")
    success_proc = SimpleNamespace(returncode=0, stdout="", stderr="")

    def _stub(cmd, cwd, timeout):
        call_count[0] += 1
        if call_count[0] == 1:
            return (success_proc, "success")
        return (fake_proc, commit_outcome)

    return _stub


def _real_git_ctx_with_test_file(tmp_path):
    """Helper: real-git repo with one untracked test file; returns (ctx, prev, pre_sha).
    4961254A: worker_written_paths includes the test file so the manifest-based
    commit step reaches the git-add path instead of skipping on empty manifest."""
    repo = tmp_path / "repo"
    repo.mkdir()
    pre_fix_sha = _init_git_repo(repo)
    (repo / "tests").mkdir()
    (repo / "tests" / "test_err.py").write_text("def test_e(): pass\n", encoding="utf-8")
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    ctx = WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={
            "scratchpad_dir": str(scratchpad),
            "git_cwd": str(repo),
        },
        question="error ladder test",
        session_id="test-session-8FE3D757-ac10",
        persona="hal",
        framework=None,
        domain=None,
    )
    prev = _make_prev(pre_fix_sha=pre_fix_sha, cycle=1, worker_written_paths=["tests/test_err.py"],
                      manifest_source="harness_tool_record")  # 4C03CCED Ship 1C
    return ctx, prev, pre_fix_sha


def test_8fe3d757_ac10a_git_add_lock_persisted(tmp_path, monkeypatch) -> None:
    """AC10a: git add → lock_persisted → E_GIT_LOCKED."""
    _captured_emits(monkeypatch)
    ctx, prev, _ = _real_git_ctx_with_test_file(tmp_path)
    monkeypatch.setattr(_p6, "_git_op_with_lock_retry", _make_git_op_stub("lock_persisted"))
    result = _commit_fix_tests(ctx, prev)
    assert result.status == "error", f"expected error; got {result.status!r}"
    assert result.error_code == "E_GIT_LOCKED", (
        f"expected E_GIT_LOCKED; got {result.error_code!r}"
    )


def test_8fe3d757_ac10b_git_add_non_lock_error(tmp_path, monkeypatch) -> None:
    """AC10b: git add → non_lock_error → E_FIX_TEST_COMMIT_FAILED."""
    _captured_emits(monkeypatch)
    ctx, prev, _ = _real_git_ctx_with_test_file(tmp_path)
    monkeypatch.setattr(_p6, "_git_op_with_lock_retry", _make_git_op_stub("non_lock_error"))
    result = _commit_fix_tests(ctx, prev)
    assert result.status == "error", f"expected error; got {result.status!r}"
    assert result.error_code == "E_FIX_TEST_COMMIT_FAILED", (
        f"expected E_FIX_TEST_COMMIT_FAILED; got {result.error_code!r}"
    )


def test_8fe3d757_ac10c_git_add_timeout(tmp_path, monkeypatch) -> None:
    """AC10c: git add → timeout → E_GIT_TIMEOUT."""
    _captured_emits(monkeypatch)
    ctx, prev, _ = _real_git_ctx_with_test_file(tmp_path)
    monkeypatch.setattr(_p6, "_git_op_with_lock_retry", _make_git_op_stub("timeout"))
    result = _commit_fix_tests(ctx, prev)
    assert result.status == "error", f"expected error; got {result.status!r}"
    assert result.error_code == "E_GIT_TIMEOUT", (
        f"expected E_GIT_TIMEOUT; got {result.error_code!r}"
    )


def test_8fe3d757_ac10d_git_add_os_error(tmp_path, monkeypatch) -> None:
    """AC10d: git add → os_error → E_GIT_OS_ERROR."""
    _captured_emits(monkeypatch)
    ctx, prev, _ = _real_git_ctx_with_test_file(tmp_path)
    monkeypatch.setattr(_p6, "_git_op_with_lock_retry", _make_git_op_stub("os_error"))
    result = _commit_fix_tests(ctx, prev)
    assert result.status == "error", f"expected error; got {result.status!r}"
    assert result.error_code == "E_GIT_OS_ERROR", (
        f"expected E_GIT_OS_ERROR; got {result.error_code!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC11 — git commit error ladder
# ═══════════════════════════════════════════════════════════════════════════════


def test_8fe3d757_ac11a_git_commit_lock_persisted(tmp_path, monkeypatch) -> None:
    """AC11a: git add success, git commit → lock_persisted → E_GIT_LOCKED."""
    _captured_emits(monkeypatch)
    ctx, prev, _ = _real_git_ctx_with_test_file(tmp_path)
    monkeypatch.setattr(_p6, "_git_op_with_lock_retry", _make_git_op_stub_commit("lock_persisted"))
    monkeypatch.setattr(_p6, "_paths_have_staged_changes", lambda *a, **k: True)  # 3F5599A6 §2.6: fixture stubs add without staging; force guard open
    result = _commit_fix_tests(ctx, prev)
    assert result.status == "error", f"expected error; got {result.status!r}"
    assert result.error_code == "E_GIT_LOCKED", (
        f"expected E_GIT_LOCKED; got {result.error_code!r}"
    )


def test_8fe3d757_ac11b_git_commit_non_lock_error(tmp_path, monkeypatch) -> None:
    """AC11b: git add success, git commit → non_lock_error → E_FIX_TEST_COMMIT_FAILED."""
    _captured_emits(monkeypatch)
    ctx, prev, _ = _real_git_ctx_with_test_file(tmp_path)
    monkeypatch.setattr(_p6, "_git_op_with_lock_retry", _make_git_op_stub_commit("non_lock_error"))
    monkeypatch.setattr(_p6, "_paths_have_staged_changes", lambda *a, **k: True)  # 3F5599A6 §2.6: fixture stubs add without staging; force guard open
    result = _commit_fix_tests(ctx, prev)
    assert result.status == "error", f"expected error; got {result.status!r}"
    assert result.error_code == "E_FIX_TEST_COMMIT_FAILED", (
        f"expected E_FIX_TEST_COMMIT_FAILED; got {result.error_code!r}"
    )


def test_8fe3d757_ac11c_git_commit_timeout(tmp_path, monkeypatch) -> None:
    """AC11c: git add success, git commit → timeout → E_GIT_TIMEOUT."""
    _captured_emits(monkeypatch)
    ctx, prev, _ = _real_git_ctx_with_test_file(tmp_path)
    monkeypatch.setattr(_p6, "_git_op_with_lock_retry", _make_git_op_stub_commit("timeout"))
    monkeypatch.setattr(_p6, "_paths_have_staged_changes", lambda *a, **k: True)  # 3F5599A6 §2.6: fixture stubs add without staging; force guard open
    result = _commit_fix_tests(ctx, prev)
    assert result.status == "error", f"expected error; got {result.status!r}"
    assert result.error_code == "E_GIT_TIMEOUT", (
        f"expected E_GIT_TIMEOUT; got {result.error_code!r}"
    )


def test_8fe3d757_ac11d_git_commit_os_error(tmp_path, monkeypatch) -> None:
    """AC11d: git add success, git commit → os_error → E_GIT_OS_ERROR."""
    _captured_emits(monkeypatch)
    ctx, prev, _ = _real_git_ctx_with_test_file(tmp_path)
    monkeypatch.setattr(_p6, "_git_op_with_lock_retry", _make_git_op_stub_commit("os_error"))
    monkeypatch.setattr(_p6, "_paths_have_staged_changes", lambda *a, **k: True)  # 3F5599A6 §2.6: fixture stubs add without staging; force guard open
    result = _commit_fix_tests(ctx, prev)
    assert result.status == "error", f"expected error; got {result.status!r}"
    assert result.error_code == "E_GIT_OS_ERROR", (
        f"expected E_GIT_OS_ERROR; got {result.error_code!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC12 — git_diff_files raises RuntimeError → graceful skip
# ═══════════════════════════════════════════════════════════════════════════════


def test_8fe3d757_ac12_empty_manifest_graceful_skip(tmp_path, monkeypatch) -> None:
    """AC12 (4961254A update): empty worker_written_paths (no test paths in manifest) →
    emit fix_test_commit_skipped(reason='no_test_paths') → return ok with
    fix_test_commit_sha=None. Does NOT propagate as error.

    4961254A: git_diff_files is no longer called — manifest drives selection.
    The old 'git_diff_failed' reason is superseded by the manifest path."""
    emitted = _captured_emits(monkeypatch)

    repo = tmp_path / "repo"
    repo.mkdir()
    pre_fix_sha = _init_git_repo(repo)

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    ctx = WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={
            "scratchpad_dir": str(scratchpad),
            "git_cwd": str(repo),
        },
        question="empty manifest skip test",
        session_id="test-session-8FE3D757-ac12",
        persona="hal",
        framework=None,
        domain=None,
    )
    # No worker_written_paths → empty manifest → no test paths → skip
    prev = _make_prev(pre_fix_sha=pre_fix_sha, cycle=1, worker_written_paths=[], manifest_source="harness_tool_record")

    result = _commit_fix_tests(ctx, prev)

    assert result.status == "ok", (
        f"Empty manifest should be a graceful skip (ok), got {result.status!r} ({result.error!r})"
    )
    assert result.data.get("fix_test_commit_sha") is None, (
        f"fix_test_commit_sha should be None; got {result.data.get('fix_test_commit_sha')!r}"
    )

    noop_events = [
        (et, p) for et, p in emitted
        if et == "fix_test_commit_skipped" and p.get("reason") == "no_test_paths"
    ]
    assert noop_events, (
        f"Expected fix_test_commit_skipped(reason='no_test_paths') on empty manifest; got {emitted!r}"
    )
    payload = noop_events[0][1]
    assert payload.get("phase") == 6
