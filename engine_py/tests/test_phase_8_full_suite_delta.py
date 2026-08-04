"""Tests for 692E4583 Part B — phase_8 full-suite baseline-delta ship-gate.

AC → test-function mapping:
  AC1  test_ac1_workflow_step_order_and_index
  AC2  test_ac2_gate_disabled_default_zero_worktrees
  AC3  test_ac3_gate_disabled_explicit_false_noop
  AC4  test_ac4_full_suite_cmds_valid_and_empty
  AC5  test_ac5_run_full_suite_nonzero_exit_returns_int
  AC6  test_ac6_run_full_suite_timeout_returns_none
  AC7  test_ac7_run_full_suite_missing_binary_returns_none
  AC8  test_ac8_compute_baseline_returns_int_and_cleans_worktree
  AC9  test_ac9_compute_baseline_bad_main_ref_returns_none_no_leftover
  AC10 test_ac10_compute_baseline_empty_cmds_returns_none_no_worktree_add
  AC11 test_ac11_gate_enforce_true_regression_errors_with_code
  AC12 test_ac12_gate_enforce_true_no_regression_ok
  AC13 test_ac13_gate_enforce_true_no_cmds_baseline_unavailable_ok
  AC14 test_ac14_source_contains_rollout_tokens_and_error_code

All tests MUST FAIL until GREEN adds:
  - _full_suite_cmds, _run_full_suite, _compute_full_suite_baseline,
    _full_suite_delta_gate  (ImportError / AttributeError)
  - workflow step list updated to include "full_suite_delta_gate" at index 8
    (assertion failure in AC1)
  - source literals flip-by:2026-09-01, 692E4583, E_SHIP_FULL_SUITE_REGRESSION
    (assertion failure in AC14)
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

from bytedigger_engine.contracts import WorkflowContext, StepResult  # noqa: E402

# Import the workflow builder (always exists — pre-GREEN)
from bytedigger_engine.workflows.phase_8_post_deploy import (  # noqa: E402
    phase_8_post_deploy_workflow,
    _accumulate_summary,
    _resolve_working_dir,
)

# These helpers do NOT exist yet — imports will fail (expected RED).
# We use a deferred import pattern inside each test so collection succeeds
# and individual tests fail at the assertion / import-inside-test level.
# This way pytest -q reports 14 FAIL instead of 1 collection error.

from bytedigger_engine.workflows import phase_8_post_deploy as _p8mod  # noqa: E402


# ─── shared fixtures ─────────────────────────────────────────────────────────

_GIT_ENV = {
    **os.environ,
    "GIT_AUTHOR_NAME": "t",
    "GIT_AUTHOR_EMAIL": "t@t",
    "GIT_COMMITTER_NAME": "t",
    "GIT_COMMITTER_EMAIL": "t@t",
}


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), check=True,
        env=_GIT_ENV, capture_output=True, text=True,
    )


def _init_git_repo(path: Path) -> None:
    """Initialise a real git repo with one commit on main."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "-q", "-b", "main"], cwd=str(path), check=True, env=_GIT_ENV
    )
    subprocess.run(
        ["git", "config", "commit.gpgsign", "false"], cwd=str(path), check=True, env=_GIT_ENV
    )
    (path / "README").write_text("init\n")
    subprocess.run(["git", "add", "."], cwd=str(path), check=True, env=_GIT_ENV)
    subprocess.run(
        ["git", "commit", "-q", "-m", "init"], cwd=str(path), check=True, env=_GIT_ENV
    )


def _worktree_count(repo: Path) -> int:
    """Count entries in `git worktree list` (each entry == 1 worktree including main)."""
    proc = subprocess.run(
        ["git", "worktree", "list", "--porcelain"],
        cwd=str(repo), capture_output=True, text=True, env=_GIT_ENV,
    )
    if proc.returncode != 0:
        return 0
    return proc.stdout.count("\nHEAD ")


def _make_ctx(scratchpad: Path, *, working_dir: Path | None = None, **org_extra) -> WorkflowContext:
    org: dict = {"scratchpad_dir": str(scratchpad), **org_extra}
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


# ─── AC1 ─────────────────────────────────────────────────────────────────────


def test_ac1_workflow_step_order_and_index():
    """AC1: full_suite_delta_gate at index 9, immediately before suite_boyscout_gate.

    AC13 (8C9F758C): after insertion of suite_boyscout_gate at index 9 (pre-1DA29C33),
    full_suite_delta_gate's next neighbor is suite_boyscout_gate (not ship_to_pr).
    1DA29C33: cleanup_run_allowlist inserted after cleanup_eval_rotation, before
    write_cleanup_report — shifts full_suite_delta_gate and everything after it by +1.
    """
    wf = phase_8_post_deploy_workflow()
    names = [s.name for s in wf.steps]
    assert "full_suite_delta_gate" in names, (
        "full_suite_delta_gate step missing from workflow; not yet added by GREEN"
    )
    idx = names.index("full_suite_delta_gate")
    assert idx == 9, f"full_suite_delta_gate must be at index 9, got {idx}"
    # AC13 (8C9F758C): suite_boyscout_gate is now between delta_gate and ship_to_pr
    assert names[idx + 1] == "suite_boyscout_gate", (
        f"full_suite_delta_gate must be immediately before suite_boyscout_gate "
        f"(8C9F758C insertion); next step is {names[idx + 1]!r}"
    )
    assert names[idx - 1] == "write_cleanup_report", (
        f"full_suite_delta_gate must be immediately after write_cleanup_report; "
        f"prev step is {names[idx - 1]!r}"
    )
    # Deregister shifts to 12 (13 total steps)
    assert names[-1] == "deregister_session"
    assert len(names) == 13, (
        f"Total steps must be 13 after 1DA29C33 insertion; got {len(names)}: {names}"
    )


# ─── AC2 ─────────────────────────────────────────────────────────────────────


def test_ac2_gate_disabled_default_zero_worktrees(tmp_path):
    """AC2: no full_suite_delta_enforce key → status ok, ran False, zero worktrees created.

    §1i: pre-stage a real git repo so we can assert worktree count is unchanged.
    """
    repo = tmp_path / "repo"
    _init_git_repo(repo)

    _full_suite_delta_gate = getattr(_p8mod, "_full_suite_delta_gate", None)
    assert _full_suite_delta_gate is not None, (
        "_full_suite_delta_gate not found on phase_8_post_deploy module; "
        "GREEN has not added it yet"
    )

    ctx = _make_ctx(tmp_path / "scratch", working_dir=repo)
    wt_before = _worktree_count(repo)

    result = _full_suite_delta_gate(ctx, None)

    assert result.status == "ok", f"Expected status='ok', got {result.status!r}"
    assert result.duration_ms == 0, f"Expected duration_ms=0, got {result.duration_ms}"

    data = result.data if isinstance(result.data, dict) else {}
    # The gate's own data is either at top level or under step_summary key.
    # _accumulate_summary wraps our data in step_summary, so gate's own keys
    # are top-level minus step_summary.
    gate_data = {k: v for k, v in data.items() if k != "step_summary"}
    assert gate_data.get("ran") is False, (
        f"Expected ran=False when gate disabled (default); got {gate_data!r}"
    )

    wt_after = _worktree_count(repo)
    assert wt_after == wt_before, (
        f"Disabled gate must create zero worktrees; before={wt_before} after={wt_after}"
    )


# ─── AC3 ─────────────────────────────────────────────────────────────────────


def test_ac3_gate_disabled_explicit_false_noop(tmp_path):
    """AC3: full_suite_delta_enforce=False explicit → status ok, ran False."""
    _full_suite_delta_gate = getattr(_p8mod, "_full_suite_delta_gate", None)
    assert _full_suite_delta_gate is not None, (
        "_full_suite_delta_gate not found on phase_8_post_deploy module"
    )

    ctx = _make_ctx(
        tmp_path / "scratch",
        working_dir=tmp_path,
        full_suite_delta_enforce=False,
        full_suite_cmds=[["true"]],
    )
    result = _full_suite_delta_gate(ctx, None)

    assert result.status == "ok", f"Expected status='ok', got {result.status!r}"
    data = result.data if isinstance(result.data, dict) else {}
    gate_data = {k: v for k, v in data.items() if k != "step_summary"}
    assert gate_data.get("ran") is False, (
        f"enforce=False must be a no-op; expected ran=False, got {gate_data!r}"
    )


# ─── AC4 ─────────────────────────────────────────────────────────────────────


def test_ac4_full_suite_cmds_valid_and_empty():
    """AC4: _full_suite_cmds returns list of lists; empty/invalid → []."""
    _full_suite_cmds = getattr(_p8mod, "_full_suite_cmds", None)
    assert _full_suite_cmds is not None, (
        "_full_suite_cmds not found on phase_8_post_deploy module"
    )

    # Valid config
    cfg_valid = {"full_suite_cmds": [["true"]]}
    result = _full_suite_cmds(cfg_valid)
    assert result == [["true"]], f"Expected [['true']], got {result!r}"

    # Missing key → empty
    result_empty = _full_suite_cmds({})
    assert result_empty == [], f"Expected [] for missing key, got {result_empty!r}"

    # Non-list value → empty
    result_non_list = _full_suite_cmds({"full_suite_cmds": "not-a-list"})
    assert result_non_list == [], f"Expected [] for non-list value, got {result_non_list!r}"

    # Empty list → empty
    result_empty_list = _full_suite_cmds({"full_suite_cmds": []})
    assert result_empty_list == [], f"Expected [] for empty list, got {result_empty_list!r}"


# ─── AC5 ─────────────────────────────────────────────────────────────────────


def test_ac5_run_full_suite_nonzero_exit_returns_int(tmp_path):
    """AC5: _run_full_suite with cmd that exits non-zero → n_failed (result[0]) is int ≥ 0.

    AC14 (8C9F758C): return shape is now (n_failed, stdout_paths) tuple.
    Existing count-only callers read result[0] (unchanged semantics).
    """
    _run_full_suite = getattr(_p8mod, "_run_full_suite", None)
    assert _run_full_suite is not None, (
        "_run_full_suite not found on phase_8_post_deploy module"
    )

    # 'false' exits 1 with no parseable test count → n_failed from parser
    result = _run_full_suite([["false"]], str(tmp_path), 30)
    # AC14 (8C9F758C): additive tuple shape
    assert isinstance(result, tuple), (
        f"_run_full_suite must return a tuple (additive shape); got {type(result).__name__!r}"
    )
    n_failed, stdout_paths = result
    assert n_failed is not None, (
        "_run_full_suite(['false'],...) n_failed must not be None; got None"
    )
    assert isinstance(n_failed, int), (
        f"_run_full_suite result[0] (n_failed) must be int for non-zero exit; "
        f"got {type(n_failed).__name__}"
    )
    assert n_failed >= 0, f"n_failed must be >= 0; got {n_failed}"
    assert isinstance(stdout_paths, list), (
        f"_run_full_suite result[1] (stdout_paths) must be a list; "
        f"got {type(stdout_paths).__name__!r}"
    )


# ─── AC6 ─────────────────────────────────────────────────────────────────────


def test_ac6_run_full_suite_timeout_returns_none(tmp_path):
    """AC6: _run_full_suite with cmd that sleeps → timeout → n_failed is None (baseline_unavailable).

    §1i: uses a 1s timeout against 'sleep 999' — deterministic, no race.
    AC14 (8C9F758C): timeout case still signals unavailability; result[0] is None.
    """
    _run_full_suite = getattr(_p8mod, "_run_full_suite", None)
    assert _run_full_suite is not None, (
        "_run_full_suite not found on phase_8_post_deploy module"
    )

    result = _run_full_suite([["sleep", "999"]], str(tmp_path), 1)
    # AC14 additive shape: tuple where result[0] is None on timeout
    assert isinstance(result, tuple), (
        f"_run_full_suite must return a tuple (additive shape); got {type(result).__name__!r}"
    )
    n_failed, _stdout_paths = result
    assert n_failed is None, (
        f"_run_full_suite timeout sentinel must have n_failed=None; got {n_failed!r}"
    )


# ─── AC7 ─────────────────────────────────────────────────────────────────────


def test_ac7_run_full_suite_missing_binary_returns_none(tmp_path):
    """AC7: _run_full_suite with non-existent binary → FileNotFoundError swallowed → n_failed None.

    AC14 (8C9F758C): missing-binary case still signals unavailability; result[0] is None.
    """
    _run_full_suite = getattr(_p8mod, "_run_full_suite", None)
    assert _run_full_suite is not None, (
        "_run_full_suite not found on phase_8_post_deploy module"
    )

    result = _run_full_suite([["__no_such_bin_692E4583__"]], str(tmp_path), 30)
    # AC14 additive shape: tuple where result[0] is None on missing binary
    assert isinstance(result, tuple), (
        f"_run_full_suite must return a tuple (additive shape); got {type(result).__name__!r}"
    )
    n_failed, _stdout_paths = result
    assert n_failed is None, (
        f"_run_full_suite with missing binary must have n_failed=None "
        f"(FileNotFoundError swallowed); got {n_failed!r}"
    )


# ─── AC8 ─────────────────────────────────────────────────────────────────────


def test_ac8_compute_baseline_returns_int_and_cleans_worktree(tmp_path, monkeypatch):
    """AC8: _compute_full_suite_baseline returns int AND cleans up temp worktree (D4-analog).

    §1y: monkeypatch _run_full_suite so test doesn't run a real suite.
    §1i: real git fixture — worktree count before == after.
    """
    _compute_full_suite_baseline = getattr(_p8mod, "_compute_full_suite_baseline", None)
    assert _compute_full_suite_baseline is not None, (
        "_compute_full_suite_baseline not found on phase_8_post_deploy module"
    )

    repo = tmp_path / "repo"
    _init_git_repo(repo)

    # Stub _run_full_suite to return additive tuple (0 fails, empty stdout_paths)
    # AC14 (8C9F758C): new shape is (n_failed, [(framework, stdout_path), ...])
    monkeypatch.setattr(_p8mod, "_run_full_suite", lambda cmds, cwd, timeout: (0, []))

    wt_before = _worktree_count(repo)

    result = _compute_full_suite_baseline(
        [["python3", "-m", "pytest", "-q", "--tb=no"]],
        "main",
        str(os.path.realpath(repo)),
        30,
    )

    assert isinstance(result, int), (
        f"_compute_full_suite_baseline must return int when worktree-add succeeds; "
        f"got {result!r} (type={type(result).__name__})"
    )
    # _compute_full_suite_baseline still returns int | None (count-only; unchanged contract)
    # It reads result[0] from the additive _run_full_suite tuple
    assert result == 0, f"Expected 0 (stubbed _run_full_suite returns (0, [])); got {result}"

    wt_after = _worktree_count(repo)
    assert wt_after == wt_before, (
        f"D4-analog: worktree must be cleaned up after baseline run; "
        f"before={wt_before} after={wt_after}"
    )

    # Also assert no fsd_baseline_* temp dir lingers under /tmp or tmp_path
    import tempfile, glob as _glob
    leftover = _glob.glob(os.path.join(tempfile.gettempdir(), "fsd_baseline_*"))
    assert leftover == [], (
        f"Temp parent dir(s) must be removed after baseline; leftover: {leftover}"
    )


# ─── AC9 ─────────────────────────────────────────────────────────────────────


def test_ac9_compute_baseline_bad_main_ref_returns_none_no_leftover(tmp_path):
    """AC9: bad main_ref → worktree-add fails → returns None; no leftover worktree/tempdir."""
    _compute_full_suite_baseline = getattr(_p8mod, "_compute_full_suite_baseline", None)
    assert _compute_full_suite_baseline is not None, (
        "_compute_full_suite_baseline not found on phase_8_post_deploy module"
    )

    repo = tmp_path / "repo"
    _init_git_repo(repo)

    wt_before = _worktree_count(repo)

    result = _compute_full_suite_baseline(
        [["true"]],
        "refs/heads/__nonexistent_branch_692E4583__",  # bogus ref → worktree add fails
        str(os.path.realpath(repo)),
        30,
    )

    assert result is None, (
        f"Bad main_ref must return None (baseline_unavailable); got {result!r}"
    )

    wt_after = _worktree_count(repo)
    assert wt_after == wt_before, (
        f"No worktree must be left when worktree-add fails; "
        f"before={wt_before} after={wt_after}"
    )

    import tempfile, glob as _glob
    leftover = _glob.glob(os.path.join(tempfile.gettempdir(), "fsd_baseline_*"))
    assert leftover == [], f"Temp dir must be cleaned on worktree-add failure; leftover: {leftover}"


# ─── AC10 ────────────────────────────────────────────────────────────────────


def test_ac10_compute_baseline_empty_cmds_returns_none_no_worktree_add(tmp_path, monkeypatch):
    """AC10: cmds=[] → returns None; zero git worktree add invocations."""
    _compute_full_suite_baseline = getattr(_p8mod, "_compute_full_suite_baseline", None)
    assert _compute_full_suite_baseline is not None, (
        "_compute_full_suite_baseline not found on phase_8_post_deploy module"
    )

    repo = tmp_path / "repo"
    _init_git_repo(repo)

    # Track any worktree-add calls by shadowing _git_write
    worktree_add_calls: list = []
    original_git_write = getattr(_p8mod, "_git_write", None)

    def _spy_git_write(args, cwd, **kw):
        if args and args[0] == "worktree" and "add" in args:
            worktree_add_calls.append(args)
        if original_git_write is not None:
            return original_git_write(args, cwd, **kw)
        return (0, "", "")

    monkeypatch.setattr(_p8mod, "_git_write", _spy_git_write)

    result = _compute_full_suite_baseline(
        [],   # empty cmds → must return None immediately without worktree-add
        "main",
        str(os.path.realpath(repo)),
        30,
    )

    assert result is None, (
        f"cmds=[] must return None immediately; got {result!r}"
    )
    assert worktree_add_calls == [], (
        f"cmds=[] must invoke zero git worktree add calls; got {worktree_add_calls}"
    )


# ─── AC11 ────────────────────────────────────────────────────────────────────


def test_ac11_gate_enforce_true_regression_errors_with_code(tmp_path, monkeypatch):
    """AC11: enforce=True, baseline=0 current=1 → status error, E_SHIP_FULL_SUITE_REGRESSION,
    would_block=True, classification=net_new_regression, net_new>=1.

    §1y: monkeypatch _run_full_suite and _compute_full_suite_baseline.
    """
    _full_suite_delta_gate = getattr(_p8mod, "_full_suite_delta_gate", None)
    assert _full_suite_delta_gate is not None, (
        "_full_suite_delta_gate not found on phase_8_post_deploy module"
    )

    # Stub: current run → 1 failure; baseline → 0 failures
    # AC14 (8C9F758C): _run_full_suite now returns (n_failed, stdout_paths) tuple
    monkeypatch.setattr(_p8mod, "_run_full_suite", lambda cmds, cwd, timeout: (1, []))
    monkeypatch.setattr(_p8mod, "_compute_full_suite_baseline", lambda cmds, main_ref, git_cwd, timeout: 0)

    ctx = _make_ctx(
        tmp_path / "scratch",
        working_dir=tmp_path,
        full_suite_delta_enforce=True,
        full_suite_cmds=[["python3", "-m", "pytest", "-q", "--tb=no"]],
    )
    result = _full_suite_delta_gate(ctx, None)

    assert result.status == "error", (
        f"enforce=True + net-new regression must return status='error'; got {result.status!r}"
    )
    assert result.error_code == "E_SHIP_FULL_SUITE_REGRESSION", (
        f"error_code must be 'E_SHIP_FULL_SUITE_REGRESSION'; got {result.error_code!r}"
    )
    assert result.recoverable is False, (
        f"regression block must be non-recoverable; got recoverable={result.recoverable!r}"
    )

    data = result.data if isinstance(result.data, dict) else {}
    gate_data = {k: v for k, v in data.items() if k != "step_summary"}
    assert gate_data.get("would_block") is True, (
        f"would_block must be True; got {gate_data!r}"
    )
    assert gate_data.get("classification") == "net_new_regression", (
        f"classification must be 'net_new_regression'; got {gate_data.get('classification')!r}"
    )
    net_new = gate_data.get("net_new", 0)
    assert net_new >= 1, f"net_new must be >= 1; got {net_new}"


# ─── AC12 ────────────────────────────────────────────────────────────────────


def test_ac12_gate_enforce_true_no_regression_ok(tmp_path, monkeypatch):
    """AC12: enforce=True, baseline==current (no net-new) → status ok, would_block=False."""
    _full_suite_delta_gate = getattr(_p8mod, "_full_suite_delta_gate", None)
    assert _full_suite_delta_gate is not None, (
        "_full_suite_delta_gate not found on phase_8_post_deploy module"
    )

    # Stub: same count both sides → no regression
    # AC14 (8C9F758C): _run_full_suite now returns (n_failed, stdout_paths) tuple
    monkeypatch.setattr(_p8mod, "_run_full_suite", lambda cmds, cwd, timeout: (2, []))
    monkeypatch.setattr(_p8mod, "_compute_full_suite_baseline", lambda cmds, main_ref, git_cwd, timeout: 2)

    ctx = _make_ctx(
        tmp_path / "scratch",
        working_dir=tmp_path,
        full_suite_delta_enforce=True,
        full_suite_cmds=[["python3", "-m", "pytest", "-q", "--tb=no"]],
    )
    result = _full_suite_delta_gate(ctx, None)

    assert result.status == "ok", (
        f"enforce=True + no net-new → must return status='ok'; got {result.status!r}"
    )
    data = result.data if isinstance(result.data, dict) else {}
    gate_data = {k: v for k, v in data.items() if k != "step_summary"}
    assert gate_data.get("would_block") is False, (
        f"would_block must be False when no regression; got {gate_data!r}"
    )
    classification = gate_data.get("classification", "")
    assert classification in ("clean", "preexisting_only"), (
        f"classification must be 'clean' or 'preexisting_only'; got {classification!r}"
    )


# ─── AC13 ────────────────────────────────────────────────────────────────────


def test_ac13_gate_enforce_true_no_cmds_baseline_unavailable_ok(tmp_path):
    """AC13: enforce=True + full_suite_cmds missing → baseline_unavailable → status ok,
    would_block=False (non-blocking, best-effort shadow-safe).
    """
    _full_suite_delta_gate = getattr(_p8mod, "_full_suite_delta_gate", None)
    assert _full_suite_delta_gate is not None, (
        "_full_suite_delta_gate not found on phase_8_post_deploy module"
    )

    ctx = _make_ctx(
        tmp_path / "scratch",
        working_dir=tmp_path,
        full_suite_delta_enforce=True,
        # full_suite_cmds intentionally absent
    )
    result = _full_suite_delta_gate(ctx, None)

    assert result.status == "ok", (
        f"baseline_unavailable (no cmds) must NOT block; expected 'ok', got {result.status!r}"
    )
    data = result.data if isinstance(result.data, dict) else {}
    gate_data = {k: v for k, v in data.items() if k != "step_summary"}
    assert gate_data.get("would_block") is False, (
        f"would_block must be False when cmds absent; got {gate_data!r}"
    )


# ─── AC14 ────────────────────────────────────────────────────────────────────


def test_ac14_source_contains_rollout_tokens_and_error_code():
    """AC14: source of phase_8_post_deploy.py contains rollout-completion tokens
    AND the error code literal (§1l fixed-string grep, not regex).

    Asserts:
      - literal 'flip-by:2026-09-01' is present on/near the full_suite_delta_enforce line
      - literal '692E4583' is present in the source
      - literal 'E_SHIP_FULL_SUITE_REGRESSION' is present in the source
    """
    source_path = Path(_p8mod.__file__)
    source = source_path.read_text(encoding="utf-8")

    assert "flip-by:2026-09-01" in source, (
        "Source must contain 'flip-by:2026-09-01' token for rollout-completion-check gate "
        "(585E30E3 P3 discipline). GREEN must add this literal."
    )
    assert "692E4583" in source, (
        "Source must contain agreement-ID '692E4583' near the full_suite_delta_enforce flag line. "
        "GREEN must add this literal."
    )
    assert "E_SHIP_FULL_SUITE_REGRESSION" in source, (
        "Source must contain error_code literal 'E_SHIP_FULL_SUITE_REGRESSION'. "
        "GREEN must add this literal."
    )
