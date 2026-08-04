"""RED tests for 789CDF56 — spec_lint hal_root resolution: promote git_cwd above checklist.hal_root.

Agreement: 789CDF56-FC4E-46BF-B052-0E5D5F111C95
Spec: SHARED/memory/Decisions/2026-05-21_789CDF56_spec_lint_hal_root_promotion_spec.md

Acceptance criteria tested:
  AC1 — explicit cfg.hal_root wins over both git_cwd and checklist (PASS pre-GREEN selectivity).
  AC2 — git_cwd wins over checklist.hal_root when cfg.hal_root absent (THE FLIP; FAIL pre-GREEN).
  AC3 — git_cwd wins over home default when no checklist, no cfg.hal_root (PASS pre-GREEN selectivity).
  AC4 — checklist.hal_root wins over home default when no cfg.hal_root, no git_cwd (PASS pre-GREEN selectivity).
  AC5 — home fallback when org_config={} (PASS pre-GREEN selectivity).
  AC6 — subprocess receives exactly one --hal-root flag with a non-empty value (PASS pre-GREEN selectivity).

RED prediction (per spec §3 + §8):
  AC1  PASS pre-GREEN — explicit cfg.hal_root always wins; unchanged by the flip.
  AC2  FAIL pre-GREEN — OLD code: checklist.get("hal_root") is consulted BEFORE cfg.get("git_cwd");
        /checklist/hal is returned instead of /gitcwd/hal.  This is the forcing-function test.
  AC3  PASS pre-GREEN — no checklist in cfg, so git_cwd already wins under old code (AE0F261A AC10 case 3).
  AC4  PASS pre-GREEN — no git_cwd in cfg; checklist.get("hal_root") returns /checklist/hal under
        both old and new code (only the mutual-presence case changes in the flip).
  AC5  PASS pre-GREEN — empty org_config; home fallback unchanged.
  AC6  PASS pre-GREEN — subprocess invocation shape unchanged; flag is always emitted once.

All subprocess calls are mocked to return returncode=0 so _verify_spec_lint returns status="ok"
and we can inspect the captured subprocess.run args.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).parent

from bytedigger_engine.contracts import StepResult, WorkflowContext  # noqa: E402
from bytedigger_engine.workflows.phase_45_spec import _verify_spec_lint  # noqa: E402


# ─── helpers (same idiom as test_phase_45_spec_hal_root.py) ───────────────────


def make_ctx(*, org_config: dict | None = None) -> WorkflowContext:
    """Minimal WorkflowContext for _verify_spec_lint."""
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org_config or {},
        question="test task",
        session_id="test-session",
        persona="hal",
        framework=None,
        domain=None,
    )


def make_prev(spec_path: str) -> StepResult:
    """StepResult shaped like _write_spec_doc output with a spec_path."""
    return StepResult(
        status="ok",
        data={"spec_path": spec_path, "cycle": 1},
        duration_ms=0,
        step_name="write_spec_doc",
    )


def _fake_run_ok(*args, **kwargs) -> subprocess.CompletedProcess:
    """Mock subprocess.run returning rc=0 (no lint findings)."""
    return subprocess.CompletedProcess(args=args[0], returncode=0, stdout="", stderr="")


def _captured_hal_root(call_args) -> str | None:
    """Extract the --hal-root value from a captured subprocess.run call_args."""
    cmd = call_args[0][0]  # positional arg 0 = the command list
    try:
        idx = cmd.index("--hal-root")
        return cmd[idx + 1]
    except (ValueError, IndexError):
        return None


# ─── AC1 ──────────────────────────────────────────────────────────────────────


def test_ac1_explicit_cfg_hal_root_wins(tmp_path):
    """AC1: explicit cfg.hal_root wins over both git_cwd and checklist.hal_root.

    RED predict: PASS (unchanged under both old and new precedence).
    """
    spec_file = tmp_path / "build-spec.md"
    spec_file.write_text("# spec\n", encoding="utf-8")

    ctx = make_ctx(org_config={
        "hal_root": "/explicit/hal",
        "git_cwd": "/gitcwd/hal",
        "checklist": {"hal_root": "/checklist/hal"},
    })
    prev = make_prev(str(spec_file))

    with patch("bytedigger_engine.workflows.phase_45_spec.subprocess.run", side_effect=_fake_run_ok) as mock_run:
        result = _verify_spec_lint(ctx, prev)

    assert result.status == "ok", f"unexpected status: {result.status!r} / {result.error!r}"
    assert mock_run.called, "subprocess.run must have been called"

    hal_root_used = _captured_hal_root(mock_run.call_args)
    assert hal_root_used == "/explicit/hal", (
        f"AC1: expected --hal-root /explicit/hal, got {hal_root_used!r}"
    )


# ─── AC2 ──────────────────────────────────────────────────────────────────────


def test_ac2_git_cwd_wins_over_checklist_when_no_explicit(tmp_path):
    """AC2: git_cwd wins over checklist.hal_root when cfg.hal_root is absent.

    This is the forcing-function test — THE FLIP (789CDF56 core change).

    RED predict: FAIL.
      Old code: checklist.get("hal_root") is consulted before cfg.get("git_cwd"),
      so pre-GREEN this returns /checklist/hal instead of /gitcwd/hal.
      New code (post-GREEN): cfg.get("git_cwd") promoted to #2, so /gitcwd/hal wins.
    """
    spec_file = tmp_path / "build-spec.md"
    spec_file.write_text("# spec\n", encoding="utf-8")

    ctx = make_ctx(org_config={
        "git_cwd": "/gitcwd/hal",
        "checklist": {"hal_root": "/checklist/hal"},
        # NOTE: no hal_root key in org_config
    })
    prev = make_prev(str(spec_file))

    with patch("bytedigger_engine.workflows.phase_45_spec.subprocess.run", side_effect=_fake_run_ok) as mock_run:
        result = _verify_spec_lint(ctx, prev)

    assert result.status == "ok", f"unexpected status: {result.status!r} / {result.error!r}"
    assert mock_run.called, "subprocess.run must have been called"

    hal_root_used = _captured_hal_root(mock_run.call_args)
    # FAILS pre-GREEN: old code returns /checklist/hal (checklist #2, git_cwd #3)
    assert hal_root_used == "/gitcwd/hal", (
        f"AC2: expected --hal-root /gitcwd/hal (git_cwd wins over checklist), "
        f"got {hal_root_used!r}"
    )


# ─── AC3 ──────────────────────────────────────────────────────────────────────


def test_ac3_git_cwd_wins_over_home_default(tmp_path):
    """AC3: git_cwd wins over home default when no cfg.hal_root and no checklist.

    RED predict: PASS (same under both old and new precedence — no checklist present).
    """
    spec_file = tmp_path / "build-spec.md"
    spec_file.write_text("# spec\n", encoding="utf-8")

    ctx = make_ctx(org_config={
        "git_cwd": "/gitcwd/hal",
        # no hal_root, no checklist
    })
    prev = make_prev(str(spec_file))

    with patch("bytedigger_engine.workflows.phase_45_spec.subprocess.run", side_effect=_fake_run_ok) as mock_run:
        result = _verify_spec_lint(ctx, prev)

    assert result.status == "ok", f"unexpected status: {result.status!r} / {result.error!r}"
    assert mock_run.called, "subprocess.run must have been called"

    hal_root_used = _captured_hal_root(mock_run.call_args)
    assert hal_root_used == "/gitcwd/hal", (
        f"AC3: expected --hal-root /gitcwd/hal, got {hal_root_used!r}"
    )


# ─── AC4 ──────────────────────────────────────────────────────────────────────


def test_ac4_checklist_wins_over_home_when_no_git_cwd(tmp_path):
    """AC4: checklist.hal_root wins over home default when no cfg.hal_root and no git_cwd.

    Selectivity guard: confirms checklist remains in the resolution chain after demotion —
    it is NOT removed entirely, just ranked below git_cwd.

    RED predict: PASS (checklist is the only non-empty non-home candidate; both old and new
    code return /checklist/hal when git_cwd is absent).
    """
    spec_file = tmp_path / "build-spec.md"
    spec_file.write_text("# spec\n", encoding="utf-8")

    ctx = make_ctx(org_config={
        "checklist": {"hal_root": "/checklist/hal"},
        # no hal_root, no git_cwd
    })
    prev = make_prev(str(spec_file))

    with patch("bytedigger_engine.workflows.phase_45_spec.subprocess.run", side_effect=_fake_run_ok) as mock_run:
        result = _verify_spec_lint(ctx, prev)

    assert result.status == "ok", f"unexpected status: {result.status!r} / {result.error!r}"
    assert mock_run.called, "subprocess.run must have been called"

    hal_root_used = _captured_hal_root(mock_run.call_args)
    assert hal_root_used == "/checklist/hal", (
        f"AC4: expected --hal-root /checklist/hal (checklist still in chain, just demoted), "
        f"got {hal_root_used!r}"
    )


# ─── AC5 ──────────────────────────────────────────────────────────────────────


def test_ac5_resolver_result_threaded_to_hal_root(tmp_path):
    """empty cfg → phase_45 threads resolver result to --hal-root; no hardcoded ~/.claude.
    Resolver derive covered by 043A9BF3 AC3/AC4.
    """
    spec_file = tmp_path / "build-spec.md"
    spec_file.write_text("# spec\n", encoding="utf-8")

    ctx = make_ctx(org_config={})
    prev = make_prev(str(spec_file))

    with patch("bytedigger_engine.workflows.phase_45_spec.subprocess.run", side_effect=_fake_run_ok) as mock_run, \
         patch("bytedigger_engine.workflows.phase_45_spec.resolve_project_root",
               return_value=(Path("/sentinel/root"), "git_toplevel")):
        result = _verify_spec_lint(ctx, prev)

    assert result.status == "ok", f"unexpected status: {result.status!r} / {result.error!r}"
    assert mock_run.called, "subprocess.run must have been called"

    hal_root_used = _captured_hal_root(mock_run.call_args)
    assert hal_root_used == "/sentinel/root", (
        f"AC5: expected --hal-root /sentinel/root (resolver result threaded), "
        f"got {hal_root_used!r}"
    )


# ─── AC6 ──────────────────────────────────────────────────────────────────────


def test_ac6_subprocess_argv_exactly_one_hal_root_flag(tmp_path):
    """AC6: subprocess receives exactly one --hal-root flag and its value is non-empty.

    Sentinel against malformed argv (e.g. a refactor that drops or duplicates the flag).

    RED predict: PASS (subprocess invocation shape unchanged pre- and post-GREEN).
    """
    spec_file = tmp_path / "build-spec.md"
    spec_file.write_text("# spec\n", encoding="utf-8")

    ctx = make_ctx(org_config={"git_cwd": "/gitcwd/hal"})
    prev = make_prev(str(spec_file))

    with patch("bytedigger_engine.workflows.phase_45_spec.subprocess.run", side_effect=_fake_run_ok) as mock_run:
        result = _verify_spec_lint(ctx, prev)

    assert result.status == "ok", f"unexpected status: {result.status!r} / {result.error!r}"
    assert mock_run.called, "subprocess.run must have been called"

    cmd = mock_run.call_args[0][0]  # the command list (first positional arg)
    hal_root_positions = [i for i, tok in enumerate(cmd) if tok == "--hal-root"]

    assert len(hal_root_positions) == 1, (
        f"AC6: expected exactly 1 --hal-root flag in argv, found {len(hal_root_positions)}: {cmd}"
    )
    flag_idx = hal_root_positions[0]
    assert flag_idx + 1 < len(cmd), (
        f"AC6: --hal-root flag is the last token — no value follows: {cmd}"
    )
    hal_root_value = cmd[flag_idx + 1]
    assert hal_root_value, (
        f"AC6: --hal-root value is empty string in argv: {cmd}"
    )
