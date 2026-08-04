"""RED tests for AE0F261A — _verify_spec_lint hal_root resolution chain.

Agreement: AE0F261A — Class A: repo_root single source of truth (Option D)

Acceptance criteria tested:
  AC9  — _verify_spec_lint with cfg.git_cwd=/repo, hal_root absent, checklist absent
          → invokes lint_spec.py with --hal-root /repo.
  AC10 — Precedence: cfg.hal_root (explicit) > cfg.git_cwd > checklist.hal_root
          > Path.home()/.claude — verified by 4 parameterized cases. (789CDF56: git_cwd promoted above checklist)
  AC11 — HAL self-build regression: no cfg.hal_root, no cfg.git_cwd, no checklist
          → falls back to Path.home()/.claude.

RED prediction:
  AC9  FAIL — current code: `hal_root = cfg.get("hal_root") or Path.home()/".claude"`;
              cfg.git_cwd is not consulted, so --hal-root will be ~/. claude, NOT /repo.
  AC10 FAIL — checklist.hal_root slot missing from resolution chain; 3 of 4 cases will fail.
  AC11 PASS — fallback to Path.home()/.claude already works in current code.

All subprocess calls are mocked to return returncode=0, so _verify_spec_lint
returns status="ok" and we can inspect the captured subprocess.run args.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

HERE = Path(__file__).parent

from bytedigger_engine.contracts import StepResult, WorkflowContext  # noqa: E402
from bytedigger_engine.workflows.phase_45_spec import _verify_spec_lint  # noqa: E402


# ─── helpers ──────────────────────────────────────────────────────────────────


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


# ─── AC9 ──────────────────────────────────────────────────────────────────────


def test_ac9_git_cwd_used_as_hal_root_when_hal_root_absent(tmp_path):
    """AC9: _verify_spec_lint uses cfg.git_cwd as --hal-root when cfg.hal_root and checklist absent.

    RED: current code ignores cfg.git_cwd; it uses Path.home()/.claude as default,
    so subprocess is called with --hal-root ~/.claude, NOT /repo.
    """
    spec_file = tmp_path / "build-spec.md"
    spec_file.write_text("# spec\n", encoding="utf-8")

    fake_repo = str(tmp_path / "myrepo")

    ctx = make_ctx(org_config={"git_cwd": fake_repo})
    prev = make_prev(str(spec_file))

    # Patch lint_path.is_file() to return True so the driver-missing guard doesn't fire
    # AND patch subprocess.run to capture the invocation args.
    with patch("bytedigger_engine.workflows.phase_45_spec.subprocess.run", side_effect=_fake_run_ok) as mock_run:
        result = _verify_spec_lint(ctx, prev)

    assert result.status == "ok", f"unexpected status: {result.status!r} / {result.error!r}"
    assert mock_run.called, "subprocess.run must have been called"

    hal_root_used = _captured_hal_root(mock_run.call_args)
    # FAILS in RED: current code uses ~/.claude, not fake_repo
    assert hal_root_used == fake_repo, (
        f"expected --hal-root {fake_repo!r}, got {hal_root_used!r}"
    )


# ─── AC10 — parameterized 4-case precedence ────────────────────────────────────


@pytest.mark.parametrize(
    "case_label, org_config_extra, expected_hal_root_suffix",
    [
        (
            "explicit_cfg_hal_root_wins",
            {
                "hal_root": "/explicit/hal",
                "checklist": {"hal_root": "/checklist/hal"},
                "git_cwd": "/gitcwd/hal",
            },
            "/explicit/hal",
        ),
        (
            "git_cwd_wins_over_checklist_hal_root",
            {
                "checklist": {"hal_root": "/checklist/hal"},
                "git_cwd": "/gitcwd/hal",
            },
            "/gitcwd/hal",
        ),
        (
            "git_cwd_wins_over_home_default",
            {
                "git_cwd": "/gitcwd/hal",
            },
            "/gitcwd/hal",
        ),
    ],
)
def test_ac10_hal_root_precedence(
    tmp_path, case_label, org_config_extra, expected_hal_root_suffix
):
    """AC10: hal_root resolution precedence: explicit > git_cwd > checklist > home/.claude.

    4 parameterized cases. 789CDF56: case 2 flipped to git_cwd_wins_over_checklist_hal_root.
    RED (post-789CDF56 edit): case 2 FAILS pre-GREEN (old code still returns /checklist/hal).
    """
    spec_file = tmp_path / "build-spec.md"
    spec_file.write_text("# spec\n", encoding="utf-8")

    org_config = {"scratchpad_dir": str(tmp_path), **org_config_extra}
    ctx = make_ctx(org_config=org_config)
    prev = make_prev(str(spec_file))

    with patch("bytedigger_engine.workflows.phase_45_spec.subprocess.run", side_effect=_fake_run_ok) as mock_run:
        result = _verify_spec_lint(ctx, prev)

    assert result.status == "ok", (
        f"[{case_label}] unexpected status: {result.status!r} / {result.error!r}"
    )
    assert mock_run.called, f"[{case_label}] subprocess.run must have been called"

    hal_root_used = _captured_hal_root(mock_run.call_args)
    assert hal_root_used is not None, (
        f"[{case_label}] --hal-root not found in subprocess args: {mock_run.call_args}"
    )
    # FAILS in RED for cases 1, 2, 3: wrong fallback used
    assert hal_root_used.endswith(expected_hal_root_suffix), (
        f"[{case_label}] expected --hal-root ending with {expected_hal_root_suffix!r}, "
        f"got {hal_root_used!r}"
    )


# ─── AC11 ─────────────────────────────────────────────────────────────────────


def test_ac11_resolver_result_threaded_to_hal_root(tmp_path):
    """phase_45 threads resolve_project_root()[0] into --hal-root (wiring contract).
    Resolver branch behavior covered by ship 043A9BF3 AC1-AC10.
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
        f"expected --hal-root /sentinel/root (resolver result threaded), got {hal_root_used!r}"
    )
