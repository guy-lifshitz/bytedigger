"""RED tests for DC6BD331 — _resolve_git_cwd single-source resolver.

Spec: SHARED/memory/Decisions/2026-06-08_DC6BD331_git_cwd_single_source_spec.md

Design summary:
  - New helper _resolve_git_cwd(ctx, prev=None) -> str in phase_5_implement.py.
    Precedence: org_config['git_cwd'] -> prev.data['git_cwd'] -> Path.cwd().
    Never returns None.
  - All 9 existing git_cwd resolution sites migrate to _resolve_git_cwd.
  - _check_red_executable collect-probe subprocess.run gains cwd=git_cwd kwarg.

§1q collectability mandate: _resolve_git_cwd does NOT exist pre-GREEN.
  Import it INSIDE each test body (deferred import) — never at module top level.
  _check_red_executable, WorkflowContext, StepResult already exist → imported here.

Pre-GREEN PASS/FAIL:
  - AC1 → FAIL: ImportError on _resolve_git_cwd inside test body.
  - AC2 → FAIL: ImportError on _resolve_git_cwd inside test body.
  - AC3 → FAIL: ImportError on _resolve_git_cwd inside test body.
  - AC4 → FAIL: bug at line 2699 reads prev.data (no git_cwd key) → None;
           subprocess.run called with no cwd kwarg → recorded cwd is None.
  - AC5 → FAIL: git_cwd=None → venv branch in _runner_for_path skipped →
           argv[0]=="python3" not ".venv/bin/pytest".
  - AC6 → PASS (regression guard): rc=0 already returns status=="ok" today.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

# ─── sys.path setup (mirrors test_7C4D70ED_red_executability_check.py:34-42) ─
# §1q: use the conftest-resident sys.path pattern established in this suite.
# Suite safety scanner does not run in Option-D manual pipeline.
ENGINE_PY = Path(__file__).resolve().parents[1]
if str(ENGINE_PY) not in sys.path:
    sys.path.insert(0, str(ENGINE_PY))
WORKFLOWS = ENGINE_PY / "workflows"
if str(WORKFLOWS) not in sys.path:
    sys.path.insert(0, str(WORKFLOWS))
LIB = ENGINE_PY / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

# ─── Production imports — symbols that exist pre-GREEN ────────────────────────
from contracts import StepResult, WorkflowContext  # noqa: E402
from phase_5_implement import _check_red_executable  # noqa: E402


# ─── helpers ──────────────────────────────────────────────────────────────────


def make_ctx(complexity: str = "SIMPLE", extra_cfg: dict | None = None) -> WorkflowContext:
    """Build a minimal WorkflowContext with org_config containing complexity."""
    cfg: dict = {"complexity": complexity}
    if extra_cfg:
        cfg.update(extra_cfg)
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=cfg,
        question="DC6BD331 git_cwd single-source test",
        session_id="test-DC6BD331",
        persona="hal",
        framework=None,
        domain=None,
    )


def make_prev(red_test_paths: list[str], cycle: int = 1, extra_data: dict | None = None) -> StepResult:
    """Build a fake prev StepResult shaped like write_red_artifact output."""
    data: dict = {
        "red_test_paths": red_test_paths,
        "cycle": cycle,
    }
    if extra_data:
        data.update(extra_data)
    return StepResult(
        status="ok",
        data=data,
        duration_ms=0,
        step_name="verify_red_lint_rules",
    )


def _build_venv_pytest(tmp_path: Path) -> Path:
    """Create <tmp>/.venv/bin/pytest as a chmod-0o755 executable. Returns tmp_path."""
    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True, exist_ok=True)
    pytest_exe = venv_bin / "pytest"
    pytest_exe.write_text("#!/usr/bin/env python3\n# stub\n")
    os.chmod(pytest_exe, 0o755)
    return tmp_path


# ─── AC1: _resolve_git_cwd returns org_config['git_cwd'] when present ────────


def test_resolve_git_cwd_prefers_org_config(tmp_path):
    """AC1: _resolve_git_cwd(ctx, prev) returns cfg['git_cwd'] when org_config has it.
    Even when prev.data also has a different git_cwd, cfg takes precedence.

    Pre-GREEN: FAIL — ImportError on _resolve_git_cwd (symbol does not exist yet).
    """
    # §1q deferred import: _resolve_git_cwd does not exist pre-GREEN
    from phase_5_implement import _resolve_git_cwd  # noqa: PLC0415

    ctx = make_ctx(extra_cfg={"git_cwd": "/cfg/dir"})
    prev = make_prev(
        red_test_paths=[str(tmp_path / "tests" / "red_x.py")],
        extra_data={"git_cwd": "/prev/dir"},
    )

    result = _resolve_git_cwd(ctx, prev)

    assert result == "/cfg/dir", (
        f"DC6BD331 AC1: expected '/cfg/dir' (org_config precedence), got {result!r}. "
        f"Resolver must prefer org_config['git_cwd'] over prev.data['git_cwd']."
    )


# ─── AC2: _resolve_git_cwd falls back to prev.data when org_config lacks it ──


def test_resolve_git_cwd_falls_back_to_prev_data(tmp_path):
    """AC2: when org_config lacks git_cwd but prev.data has it → returns prev value.

    Pre-GREEN: FAIL — ImportError on _resolve_git_cwd (symbol does not exist yet).
    """
    # §1q deferred import
    from phase_5_implement import _resolve_git_cwd  # noqa: PLC0415

    # org_config has only complexity — no git_cwd key
    ctx = make_ctx(complexity="SIMPLE")
    prev = make_prev(
        red_test_paths=[str(tmp_path / "tests" / "red_x.py")],
        extra_data={"git_cwd": "/prev/dir"},
    )

    result = _resolve_git_cwd(ctx, prev)

    assert result == "/prev/dir", (
        f"DC6BD331 AC2: expected '/prev/dir' (prev.data fallback), got {result!r}. "
        f"When org_config lacks git_cwd, resolver must use prev.data['git_cwd']."
    )


# ─── AC3: _resolve_git_cwd never returns None — falls back to Path.cwd() ─────


def test_resolve_git_cwd_never_none(tmp_path):
    """AC3: when neither org_config nor prev.data has git_cwd → returns str(Path.cwd()).
    Must never return None (anti-#2 invariant).

    Pre-GREEN: FAIL — ImportError on _resolve_git_cwd (symbol does not exist yet).
    """
    # §1q deferred import
    from phase_5_implement import _resolve_git_cwd  # noqa: PLC0415

    # Neither side has git_cwd
    ctx = make_ctx(complexity="SIMPLE")
    prev = make_prev(red_test_paths=[str(tmp_path / "tests" / "red_x.py")])
    # prev.data has only red_test_paths + cycle — no git_cwd

    result = _resolve_git_cwd(ctx, prev)

    assert result is not None, (
        "DC6BD331 AC3: _resolve_git_cwd must never return None. "
        "Expected str(Path.cwd()) as last-resort fallback."
    )
    assert result == str(Path.cwd()), (
        f"DC6BD331 AC3: expected str(Path.cwd())={str(Path.cwd())!r} as fallback, "
        f"got {result!r}."
    )
    assert isinstance(result, str), (
        f"DC6BD331 AC3: return type must be str, not {type(result)!r}."
    )


# ─── AC4: _check_red_executable passes cwd=git_cwd to subprocess.run ─────────


def test_check_red_executable_threads_git_cwd_to_probe_cwd(monkeypatch, tmp_path):
    """AC4 (#2 root): _check_red_executable with org_config containing git_cwd
    and prev.data WITHOUT git_cwd key: the collect-probe subprocess.run call
    receives cwd == str(tmp_path).

    Pre-GREEN FAIL: line 2699 reads git_cwd from prev.data (key absent → None).
    subprocess.run has no cwd kwarg → recorded cwd is None.
    """
    tmp = _build_venv_pytest(tmp_path)

    ctx = make_ctx(extra_cfg={"git_cwd": str(tmp)})
    # prev.data deliberately has NO git_cwd key — this is the bug trigger
    prev = make_prev(red_test_paths=[str(tmp / "tests" / "red_x.py")])
    assert "git_cwd" not in prev.data, "fixture must not carry git_cwd in prev.data"

    recorded: list[dict] = []

    def _fake_run(argv, *args: Any, **kwargs: Any):
        # Capture only the first .py probe call
        if not recorded and any(str(a).endswith(".py") for a in argv):
            recorded.append({"argv": list(argv), "cwd": kwargs.get("cwd")})
        return subprocess.CompletedProcess(
            args=argv, returncode=0, stdout="1 collected", stderr=""
        )

    monkeypatch.setattr("phase_5_implement.subprocess.run", _fake_run)

    _check_red_executable(ctx, prev)

    assert recorded, (
        "DC6BD331 AC4: subprocess.run was never called with a .py path. "
        "Expected at least one collect-probe invocation."
    )
    recorded_cwd = recorded[0]["cwd"]
    assert recorded_cwd == str(tmp), (
        f"DC6BD331 AC4: expected cwd={str(tmp)!r} passed to subprocess.run, "
        f"got {recorded_cwd!r}. "
        f"Pre-GREEN: git_cwd read from prev.data (absent→None), no cwd kwarg → None."
    )


# ─── AC5: _check_red_executable uses venv pytest when git_cwd is threaded ────


def test_check_red_executable_uses_venv_pytest_when_git_cwd_threaded(monkeypatch, tmp_path):
    """AC5 (#2 venv reach): same setup — captured probe argv[0] ends with
    '.venv/bin/pytest', NOT 'python3'. Proves git_cwd reached _runner_for_path.

    Pre-GREEN FAIL: git_cwd=None → venv branch in _runner_for_path skipped →
    argv[0] == 'python3' (bare runner), not the venv binary.
    """
    tmp = _build_venv_pytest(tmp_path)

    ctx = make_ctx(extra_cfg={"git_cwd": str(tmp)})
    prev = make_prev(red_test_paths=[str(tmp / "tests" / "red_x.py")])
    assert "git_cwd" not in prev.data, "fixture must not carry git_cwd in prev.data"

    recorded: list[dict] = []

    def _fake_run(argv, *args: Any, **kwargs: Any):
        if not recorded and any(str(a).endswith(".py") for a in argv):
            recorded.append({"argv": list(argv), "cwd": kwargs.get("cwd")})
        return subprocess.CompletedProcess(
            args=argv, returncode=0, stdout="1 collected", stderr=""
        )

    monkeypatch.setattr("phase_5_implement.subprocess.run", _fake_run)

    _check_red_executable(ctx, prev)

    assert recorded, (
        "DC6BD331 AC5: subprocess.run was never called with a .py path."
    )
    argv0 = recorded[0]["argv"][0]
    assert argv0.endswith(".venv/bin/pytest"), (
        f"DC6BD331 AC5: expected argv[0] ending with '.venv/bin/pytest', got {argv0!r}. "
        f"Pre-GREEN: None git_cwd → venv branch skipped → argv[0]=='python3'."
    )


# ─── AC6: valid collect (rc=0) does not false-fail ───────────────────────────


def test_valid_red_collect_does_not_false_fail(monkeypatch, tmp_path):
    """AC6 (#3 regression guard): when git_cwd is resolved and probe returns rc=0,
    _check_red_executable returns status='ok' with no E_RED_COLLECT_FAILED.

    This is a regression guard — may PASS pre-GREEN (the ok path already works
    when subprocess.run is reached). Prevents the false-fail→retry recurrence.
    """
    tmp = _build_venv_pytest(tmp_path)

    ctx = make_ctx(extra_cfg={"git_cwd": str(tmp)})
    prev = make_prev(red_test_paths=[str(tmp / "tests" / "red_x.py")])

    monkeypatch.setattr(
        "phase_5_implement.subprocess.run",
        lambda argv, *args, **kwargs: subprocess.CompletedProcess(
            args=argv, returncode=0, stdout="1 collected", stderr=""
        ),
    )

    result = _check_red_executable(ctx, prev)

    assert result.status == "ok", (
        f"DC6BD331 AC6: expected status='ok' on clean collect (rc=0), "
        f"got {result.status!r}. Regression guard: no false E_RED_COLLECT_FAILED."
    )
    error_code = getattr(result, "error_code", None)
    assert not error_code, (
        f"DC6BD331 AC6: expected no error_code on clean collect, got {error_code!r}."
    )
    error_msg = getattr(result, "error", None) or ""
    assert "E_RED_COLLECT_FAILED" not in error_msg, (
        f"DC6BD331 AC6: 'E_RED_COLLECT_FAILED' must not appear in error message "
        f"on rc=0 collect. got error={error_msg!r}."
    )
