"""RED tests for D5D6A364 — route 2 commit-HEAD rev-parse READs through git_port.git_read.

Spec: SHARED/memory/Decisions/2026-06-21_D5D6A364_git_read_routing_slice2_spec.md

Forcing function (§1l / §3):
  Each test installs a recording spy as the git_read implementation via
  set_default_git_read_factory, then calls the UUT and asserts the spy was
  invoked with the correct args.

  Pre-GREEN: _commit_fix_code / _commit_fix_tests still call bounded_run
  directly for the post-commit rev-parse — the spy is NEVER reached.
  All 4 tests FAIL at ASSERT time (spy.calls == [] at assertion).
  NOT vacuous (§1l): we patch the collaborator (git_port.git_read factory +
  phase_6_review._git_op_with_lock_retry), never the UUTs themselves.

  Post-GREEN: UUTs call git_port.git_read(…); the spy is invoked; assertions pass.

§1i (singleton-resource): factory swap is pre-staged and always reset in finally.
§1q: no sys.path manipulation here; conftest handles it at import time.
§1q/D1CF5FDF: all symbols imported at module level already exist today
(_commit_fix_code, _commit_fix_tests, GitResult, set/reset_default_git_read_factory)
— file COLLECTS cleanly; RED fires at assert time, not at collection time.
§1j: macOS-safe tmp via os.path.realpath(tempfile.mkdtemp(...)).

Pre-GREEN PASS/FAIL classification:
  AC1 → FAIL  (spy never called; bounded_run used instead; len(spy.calls)==0)
  AC2 → FAIL  (spy never called; bounded_run used instead; rc check is never via spy)
  AC3 → FAIL  (spy never called; bounded_run used instead; len(spy.calls)==0)
  AC4 → FAIL  (spy never called; bounded_run used instead; rc check is never via spy)

Expected pre-GREEN: 4 FAIL / 0 PASS.
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock

# conftest-import-time singleton handles sys.path (§1q / 81F97F3D gate).
# Do NOT add sys.path.insert here.

import lib.git_port as git_port
from lib.git_port import GitResult, set_default_git_read_factory, reset_default_git_read_factory

import phase_6_review
from phase_6_review import _commit_fix_code, _commit_fix_tests
from contracts import WorkflowContext


# ─────────────────────────────────────────────────────────────────────────────
# Recording spy factory (mirrors test_7FE193B7_git_read_routing.py pattern)
# ─────────────────────────────────────────────────────────────────────────────


class _SpyGitRead:
    """Recording spy that matches GitReadPort.__call__ signature.

    Records ALL calls in self.calls.  Args-aware dispatch (D5D6A364 Opus fix):
    check-ignore calls (from _filter_gitignored_paths in phase_workflows_common)
    share the same injected seam and must be handled separately — return
    GitResult(0,"","",False) (nothing ignored) so prod_paths stay intact and
    execution reaches the rev-parse Point.  All other args use self.result
    (the configured return for rev-parse calls).
    """

    def __init__(self, result: GitResult) -> None:
        self.result = result
        self.calls: list[tuple] = []

    def __call__(
        self,
        args: list[str],
        *,
        cwd: str | None = None,
        timeout: float | None = None,
        dir_: str | None = None,
    ) -> GitResult:
        self.calls.append((args, cwd, timeout))
        if args[:1] == ["check-ignore"]:
            # _filter_gitignored_paths: return empty stdout → nothing ignored
            return GitResult(returncode=0, stdout="", stderr="", timed_out=False)
        return self.result


# ─────────────────────────────────────────────────────────────────────────────
# Shared fixture helpers
# ─────────────────────────────────────────────────────────────────────────────

_VALID_SHA = "a" * 40   # valid 40-char hex SHA boundary fixture
_POST_SHA = "b" * 40    # post-commit HEAD SHA fixture for success tests


def _make_ctx(tmp_path: Path) -> WorkflowContext:
    """Build a minimal WorkflowContext with scratchpad + git_cwd inside tmp_path.

    Setting git_cwd in org_config ensures _is_synthetic_test_env() returns False
    (cfg.get('git_cwd') is truthy → real-project branch, no early-return skip).
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    git_cwd = tmp_path / "repo"
    git_cwd.mkdir(parents=True, exist_ok=True)
    org = {
        "scratchpad_dir": str(scratchpad),
        "git_cwd": str(git_cwd),
    }
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question="Apply fix",
        session_id="test-session-D5D6A364",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_prev_code(pre_fix_sha: str, cycle: int = 2, **extra) -> MagicMock:
    """Minimal prev for _commit_fix_code with non-empty manifest."""
    prev = MagicMock()
    prev.data = {
        "cycle": cycle,
        "pre_fix_sha": pre_fix_sha,
        **extra,
    }
    return prev


def _make_prev_tests(pre_fix_sha: str, cycle: int = 2, **extra) -> MagicMock:
    """Minimal prev for _commit_fix_tests with non-empty test manifest."""
    prev = MagicMock()
    prev.data = {
        "cycle": cycle,
        "pre_fix_sha": pre_fix_sha,
        **extra,
    }
    return prev


# ─────────────────────────────────────────────────────────────────────────────
# AC1 — _commit_fix_code: spy returns rc=0 → called once, args, fix_commit_sha
# ─────────────────────────────────────────────────────────────────────────────


def test_ac1_commit_fix_code_rev_parse_routes_through_git_read(
    tmp_path: Path, monkeypatch
) -> None:
    """AC1: spy returns GitResult(0,'<sha>\\n','',False) after successful commit.

    _filter_gitignored_paths (phase_workflows_common) also calls git_read via the
    same seam with ["check-ignore",...] — the spy handles those with rc=0/empty
    (nothing ignored) and records them too.  We filter to rev-parse-only calls.

    Assert: exactly 1 rev-parse["rev-parse","HEAD"] call routed through seam,
    cwd==str(git_cwd), result.data["fix_commit_sha"]==<sha>.

    Pre-GREEN FAIL: bounded_run used for rev-parse; revparse_calls==[] → assert fails.
    """
    # macOS-safe tmp dir (§1j)
    git_cwd_str = str(os.path.realpath(str(tmp_path / "repo")))
    (tmp_path / "repo").mkdir(parents=True, exist_ok=True)

    sha = "c" * 40

    spy = _SpyGitRead(GitResult(returncode=0, stdout=sha + "\n", stderr="", timed_out=False))

    # Silence emit noise
    monkeypatch.setattr(phase_6_review, "_emit_safe", lambda et, p, **kw: None)

    # Mock the WRITE git ops (git add + git commit) so execution reaches the rev-parse Point
    fake_proc = MagicMock()
    fake_proc.returncode = 0
    monkeypatch.setattr(
        phase_6_review,
        "_git_op_with_lock_retry",
        lambda cmd, cwd, timeout=30: (fake_proc, "ok"),
    )

    # Materialize the source file on disk so git add path validation passes
    src_dir = tmp_path / "repo" / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "foo.py").write_text("")

    ctx = _make_ctx(tmp_path)
    prev = _make_prev_code(
        pre_fix_sha=_VALID_SHA,
        cycle=2,
        worker_written_paths=["src/foo.py"],
        manifest_source="harness_tool_record",
    )

    try:
        set_default_git_read_factory(lambda: spy)
        result = _commit_fix_code(ctx, prev)
    finally:
        reset_default_git_read_factory()

    revparse_calls = [c for c in spy.calls if c[0] == ["rev-parse", "HEAD"]]
    assert len(revparse_calls) == 1, (
        f"Expected exactly 1 rev-parse call routed through git_read seam; "
        f"got {len(revparse_calls)} — pre-GREEN: bounded_run used instead "
        f"(all spy calls: {[c[0] for c in spy.calls]})"
    )
    _, recorded_cwd, _ = revparse_calls[0]
    assert recorded_cwd == git_cwd_str, (
        f"cwd not forwarded: expected {git_cwd_str!r}, got {recorded_cwd!r}"
    )
    assert result.data is not None
    assert result.data.get("fix_commit_sha") == sha, (
        f"Expected fix_commit_sha=={sha!r}, got {result.data.get('fix_commit_sha')!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# AC2 — _commit_fix_code: spy returns rc=128 → error result
# ─────────────────────────────────────────────────────────────────────────────


def test_ac2_commit_fix_code_rev_parse_failure_returns_error(
    tmp_path: Path, monkeypatch
) -> None:
    """AC2: spy returns GitResult(128,'','fatal:boom',False) for rev-parse.

    check-ignore calls (same seam) are handled by the args-aware dispatch
    (rc=0, empty stdout) so prod_paths pass through and execution reaches the
    rev-parse Point where the spy returns rc=128.

    Assert: exactly 1 rev-parse call through seam, result.status=='error',
    error_code=='E_FIX_COMMIT_FAILED', 'boom' in result.error.

    Pre-GREEN FAIL: bounded_run used; revparse_calls==[] → first assert fails.
    """
    spy = _SpyGitRead(
        GitResult(returncode=128, stdout="", stderr="fatal:boom", timed_out=False)
    )

    monkeypatch.setattr(phase_6_review, "_emit_safe", lambda et, p, **kw: None)

    fake_proc = MagicMock()
    fake_proc.returncode = 0
    monkeypatch.setattr(
        phase_6_review,
        "_git_op_with_lock_retry",
        lambda cmd, cwd, timeout=30: (fake_proc, "ok"),
    )

    # File on disk so we pass the manifest -> git add path
    src_dir = tmp_path / "repo" / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    (src_dir / "foo.py").write_text("")

    ctx = _make_ctx(tmp_path)
    prev = _make_prev_code(
        pre_fix_sha=_VALID_SHA,
        cycle=2,
        worker_written_paths=["src/foo.py"],
        manifest_source="harness_tool_record",
    )

    try:
        set_default_git_read_factory(lambda: spy)
        result = _commit_fix_code(ctx, prev)
    finally:
        reset_default_git_read_factory()

    revparse_calls = [c for c in spy.calls if c[0] == ["rev-parse", "HEAD"]]
    assert len(revparse_calls) == 1, (
        f"Expected exactly 1 rev-parse call through git_read seam; "
        f"got {len(revparse_calls)} — pre-GREEN: bounded_run used, spy stays empty "
        f"(all spy calls: {[c[0] for c in spy.calls]})"
    )
    assert result.status == "error", (
        f"Expected status='error' on rc=128 rev-parse, got {result.status!r}"
    )
    assert result.error_code == "E_FIX_COMMIT_FAILED", (
        f"Expected error_code='E_FIX_COMMIT_FAILED', got {result.error_code!r}"
    )
    assert "boom" in (result.error or ""), (
        f"Expected 'boom' in result.error, got {result.error!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# AC3 — _commit_fix_tests: spy returns rc=0 → called once, fix_test_commit_sha
# ─────────────────────────────────────────────────────────────────────────────


def test_ac3_commit_fix_tests_rev_parse_routes_through_git_read(
    tmp_path: Path, monkeypatch
) -> None:
    """AC3: spy returns GitResult(0,'<sha>\\n','',False) after successful test commit.

    Assert: len(spy.calls)==1, args==["rev-parse","HEAD"], cwd==str(git_cwd),
    result.data["fix_test_commit_sha"]==<sha>.

    Pre-GREEN FAIL: bounded_run used instead; spy uncalled; len(spy.calls)==0.
    """
    git_cwd_str = str(os.path.realpath(str(tmp_path / "repo")))
    (tmp_path / "repo").mkdir(parents=True, exist_ok=True)

    sha = "d" * 40

    spy = _SpyGitRead(GitResult(returncode=0, stdout=sha + "\n", stderr="", timed_out=False))

    monkeypatch.setattr(phase_6_review, "_emit_safe", lambda et, p, **kw: None)

    fake_proc = MagicMock()
    fake_proc.returncode = 0
    monkeypatch.setattr(
        phase_6_review,
        "_git_op_with_lock_retry",
        lambda cmd, cwd, timeout=30: (fake_proc, "ok"),
    )
    monkeypatch.setattr(phase_6_review, "_paths_have_staged_changes", lambda *a, **k: True)  # 3F5599A6 §2.6: keep single-git_read spy premise
    monkeypatch.setattr(phase_6_review, "_assert_clean_tree", lambda *a, **k: True)  # 3F5599A6 §2.6

    # Materialize the test file on disk
    tests_dir = tmp_path / "repo" / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    (tests_dir / "test_x.py").write_text("")

    ctx = _make_ctx(tmp_path)
    prev = _make_prev_tests(
        pre_fix_sha=_VALID_SHA,
        cycle=2,
        worker_written_paths=["tests/test_x.py"],
        manifest_source="harness_tool_record",
    )

    try:
        set_default_git_read_factory(lambda: spy)
        result = _commit_fix_tests(ctx, prev)
    finally:
        reset_default_git_read_factory()

    # GH886: tail-autocommit helper legitimately adds extra git_read calls
    # (porcelain probe etc.) — routing intent is "rev-parse present", not "exactly one".
    rev_parse_calls = [c for c in spy.calls if c[0] == ["rev-parse", "HEAD"]]
    assert rev_parse_calls, (
        f"Expected at least one spy call with args ['rev-parse','HEAD'] "
        f"(git_read routes rev-parse); got calls={spy.calls!r} — "
        f"pre-GREEN: bounded_run used instead"
    )
    recorded_args, recorded_cwd, _ = rev_parse_calls[0]
    assert recorded_args == ["rev-parse", "HEAD"], (
        f"Expected args ['rev-parse','HEAD'], got {recorded_args!r}"
    )
    assert recorded_cwd == git_cwd_str, (
        f"cwd not forwarded: expected {git_cwd_str!r}, got {recorded_cwd!r}"
    )
    assert result.data is not None
    assert result.data.get("fix_test_commit_sha") == sha, (
        f"Expected fix_test_commit_sha=={sha!r}, got {result.data.get('fix_test_commit_sha')!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# AC4 — _commit_fix_tests: spy returns rc=128 → error result
# ─────────────────────────────────────────────────────────────────────────────


def test_ac4_commit_fix_tests_rev_parse_failure_returns_error(
    tmp_path: Path, monkeypatch
) -> None:
    """AC4: spy returns GitResult(128,'','fatal:boom',False).

    Assert: result.status=='error', error_code=='E_FIX_TEST_COMMIT_FAILED',
    'boom' in result.error, len(spy.calls)==1.

    Pre-GREEN FAIL: bounded_run used instead; spy uncalled; len(spy.calls)==0.
    """
    spy = _SpyGitRead(
        GitResult(returncode=128, stdout="", stderr="fatal:boom", timed_out=False)
    )

    monkeypatch.setattr(phase_6_review, "_emit_safe", lambda et, p, **kw: None)

    fake_proc = MagicMock()
    fake_proc.returncode = 0
    monkeypatch.setattr(
        phase_6_review,
        "_git_op_with_lock_retry",
        lambda cmd, cwd, timeout=30: (fake_proc, "ok"),
    )
    monkeypatch.setattr(phase_6_review, "_paths_have_staged_changes", lambda *a, **k: True)  # 3F5599A6 §2.6: keep single-git_read spy premise
    monkeypatch.setattr(phase_6_review, "_assert_clean_tree", lambda *a, **k: True)  # 3F5599A6 §2.6

    # Materialize test file on disk
    tests_dir = tmp_path / "repo" / "tests"
    tests_dir.mkdir(parents=True, exist_ok=True)
    (tests_dir / "test_x.py").write_text("")

    ctx = _make_ctx(tmp_path)
    prev = _make_prev_tests(
        pre_fix_sha=_VALID_SHA,
        cycle=2,
        worker_written_paths=["tests/test_x.py"],
        manifest_source="harness_tool_record",
    )

    try:
        set_default_git_read_factory(lambda: spy)
        result = _commit_fix_tests(ctx, prev)
    finally:
        reset_default_git_read_factory()

    assert len(spy.calls) == 1, (
        f"Expected spy called once; got {len(spy.calls)} — "
        f"pre-GREEN: bounded_run used, spy stays empty"
    )
    assert result.status == "error", (
        f"Expected status='error' on rc=128 rev-parse, got {result.status!r}"
    )
    assert result.error_code == "E_FIX_TEST_COMMIT_FAILED", (
        f"Expected error_code='E_FIX_TEST_COMMIT_FAILED', got {result.error_code!r}"
    )
    assert "boom" in (result.error or ""), (
        f"Expected 'boom' in result.error, got {result.error!r}"
    )
