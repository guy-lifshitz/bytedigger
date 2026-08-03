"""RED tests for 4238DD14 — phase_5 collect-probe must branch on test group.

Spec: SHARED/memory/Decisions/2026-06-06_4238DD14_phase5_validation_bashtest_enum_spec.md

Problem (§0): _check_red_executable unconditionally runs pytest --collect-only on
EVERY red_test_path, including bash .test.sh files which pytest cannot collect.
Fix: new helper _collect_probe_argv dispatches ["bash","-n",path] for .sh and
the pytest collect argv for .py, and None for unsupported extensions.

Pre-GREEN PASS/FAIL classification:
  - AC1 → FAIL: AttributeError (_collect_probe_argv absent in module).
  - AC2 → FAIL: AttributeError (_collect_probe_argv absent in module).
  - AC3 → FAIL: AttributeError (_collect_probe_argv absent in module).
  - AC4 → FAIL (CORE): current code runs pytest --collect-only on .sh → non-zero exit
           → E_RED_COLLECT_FAILED. Fix must use bash -n → exits 0 → status="ok".
  - AC5 → PASS (incidental): current code also runs pytest on broken .sh → non-zero exit
           → E_RED_COLLECT_FAILED. Passes vacuously for the wrong reason (pytest, not bash -n).
           Still exercises the failure path; post-GREEN exercises the bash -n path correctly.
  - AC6 → PASS: .py valid file, real subprocess pytest --collect-only succeeds → status="ok".
           (Current code already handles .py correctly.)
  - AC7 → FAIL: current code runs pytest on the .py → pytest collects the file, sees the
           import error → non-zero → E_RED_COLLECT_FAILED. Actually this PASSES pre-GREEN
           if pytest correctly fails on import. May vary by env — conservatively treated as PASS.
  - AC8 → FAIL (CORE): mixed paths. Current code runs pytest on the .sh → non-zero →
           E_RED_COLLECT_FAILED before ever reaching the .py. Post-GREEN: bash -n passes +
           pytest passes → status="ok".

Expected pre-GREEN: AC1 FAIL, AC2 FAIL, AC3 FAIL, AC4 FAIL, AC8 FAIL (>= 5 FAIL). AC5/AC6/AC7 PASS.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

# ─── sys.path setup (mirrors test_7C4D70ED_red_executability_check.py:34-42) ──
# §1q / D1CF5FDF: use the conftest-import-time singleton pattern established in
# this suite. Suite safety scanner does not run in the Option-D manual pipeline.
ENGINE_PY = Path(__file__).resolve().parents[1]
if str(ENGINE_PY) not in sys.path:
    sys.path.insert(0, str(ENGINE_PY))
WORKFLOWS = ENGINE_PY / "bytedigger_engine" / "workflows"
if str(WORKFLOWS) not in sys.path:
    sys.path.insert(0, str(WORKFLOWS))
LIB = ENGINE_PY / "bytedigger_engine" / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

# ─── telemetry_ctx — must be importable before production imports ──────────────
from bytedigger_engine import telemetry_ctx as _telemetry_ctx  # noqa: E402

# ─── Production module import (exists today — collectable) ────────────────────
# _collect_probe_argv does NOT exist yet. We import the MODULE (not the symbol)
# so the file is collectable. Each AC1-3 test body accesses the attribute via
# getattr() — AttributeError surfaces as a test FAIL at assert time, not at
# collect time. §1q / D1CF5FDF discipline.
from bytedigger_engine.workflows import phase_5_implement as p5mod  # noqa: E402
from bytedigger_engine.contracts import StepResult, WorkflowContext  # noqa: E402
from bytedigger_engine.workflows.phase_5_implement import _check_red_executable  # noqa: E402


# ─── helpers ──────────────────────────────────────────────────────────────────


def make_ctx(complexity: str = "SIMPLE") -> WorkflowContext:
    """Minimal WorkflowContext with org_config containing complexity."""
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={"complexity": complexity},
        question="4238DD14 bashtest enum test",
        session_id="test-4238DD14",
        persona="hal",
        framework=None,
        domain=None,
    )


def make_prev(red_test_paths: list[str], cycle: int = 1) -> StepResult:
    """Fake prev StepResult shaped like write_red_artifact output."""
    return StepResult(
        status="ok",
        data={
            "red_test_paths": red_test_paths,
            "cycle": cycle,
        },
        duration_ms=0,
        step_name="verify_red_lint_rules",
    )


# ─── AC1: _collect_probe_argv("foo.test.sh") == ["bash", "-n", "foo.test.sh"] ─


def test_collect_probe_argv_sh_branch():
    """AC1: _collect_probe_argv("foo.test.sh") returns ["bash", "-n", "foo.test.sh"].

    Pre-GREEN: FAIL — AttributeError because _collect_probe_argv does not exist.
    Isolation: fails in isolation — AttributeError is deterministic on the absent symbol.
    Victim: the new _collect_probe_argv helper that GREEN must add.
    Not-yet-shipped: confirmed — grep engine found zero occurrences of _collect_probe_argv.
    """
    fn = getattr(p5mod, "_collect_probe_argv", None)
    assert fn is not None, (
        "4238DD14 AC1: _collect_probe_argv is absent from phase_5_implement "
        "(pre-GREEN: AttributeError expected as test failure)"
    )
    result = fn("foo.test.sh")
    assert result == ["bash", "-n", "foo.test.sh"], (
        f"4238DD14 AC1: expected ['bash', '-n', 'foo.test.sh'] for .sh path, got {result!r}"
    )


# ─── AC2: _collect_probe_argv("foo_test.py") contains --collect-only + pytest ─


def test_collect_probe_argv_py_branch():
    """AC2: _collect_probe_argv("foo_test.py") returns a list containing
    "--collect-only", ending with "foo_test.py", and with a pytest token.

    Pre-GREEN: FAIL — AttributeError because _collect_probe_argv does not exist.
    """
    fn = getattr(p5mod, "_collect_probe_argv", None)
    assert fn is not None, (
        "4238DD14 AC2: _collect_probe_argv is absent from phase_5_implement "
        "(pre-GREEN: AttributeError expected as test failure)"
    )
    result = fn("foo_test.py")
    assert isinstance(result, list), (
        f"4238DD14 AC2: expected a list for .py path, got {type(result)!r}"
    )
    assert "--collect-only" in result, (
        f"4238DD14 AC2: '--collect-only' must be in argv for .py path, got {result!r}"
    )
    assert result[-1] == "foo_test.py", (
        f"4238DD14 AC2: last element must be 'foo_test.py', got {result[-1]!r}"
    )
    pytest_present = any("pytest" in tok for tok in result)
    assert pytest_present, (
        f"4238DD14 AC2: a 'pytest' token must appear in argv for .py path, got {result!r}"
    )


# ─── AC3: _collect_probe_argv("foo.rb") is None ───────────────────────────────


def test_collect_probe_argv_unsupported_returns_none():
    """AC3: _collect_probe_argv("foo.rb") returns None (unsupported extension).

    Pre-GREEN: FAIL — AttributeError because _collect_probe_argv does not exist.
    """
    fn = getattr(p5mod, "_collect_probe_argv", None)
    assert fn is not None, (
        "4238DD14 AC3: _collect_probe_argv is absent from phase_5_implement "
        "(pre-GREEN: AttributeError expected as test failure)"
    )
    result = fn("foo.rb")
    assert result is None, (
        f"4238DD14 AC3: expected None for unsupported extension .rb, got {result!r}"
    )


# ─── AC4 (CORE): valid bash .test.sh → status == "ok" ────────────────────────


def test_check_red_executable_valid_bash_ok(tmp_path):
    """AC4 (CORE): _check_red_executable with a syntactically-valid bash .test.sh
    returns status="ok", NOT E_RED_COLLECT_FAILED.

    This is the primary forcing function. Pre-GREEN the function unconditionally
    runs pytest --collect-only on the .sh file, which exits non-zero → E_RED_COLLECT_FAILED.
    Post-GREEN it must dispatch bash -n → exits 0 → status="ok".

    Uses a real subprocess (no monkeypatch) so the test validates end-to-end behavior
    including the subprocess dispatch. bash must be available in PATH.

    Pre-GREEN: FAIL — pytest --collect-only fails on .sh → E_RED_COLLECT_FAILED.
    §1i (singleton-resource): no singleton resource; temp file is path-unique under tmp_path.
    """
    sh_file = tmp_path / "valid_test.test.sh"
    sh_file.write_text("#!/usr/bin/env bash\n: ;\n")

    prev = make_prev(red_test_paths=[str(sh_file)], cycle=1)
    ctx = make_ctx("SIMPLE")

    result = _check_red_executable(ctx, prev)

    assert result.status == "ok", (
        f"4238DD14 AC4 (CORE): valid bash .test.sh must return status='ok', "
        f"got status={result.status!r}, error_code={getattr(result, 'error_code', None)!r}. "
        f"Pre-GREEN: pytest --collect-only fails on .sh → E_RED_COLLECT_FAILED. "
        f"Post-GREEN: bash -n exits 0 → status='ok'."
    )


# ─── AC5: syntactically-broken bash .test.sh → E_RED_COLLECT_FAILED ──────────


def test_check_red_executable_broken_bash_collect_failed(tmp_path):
    """AC5: _check_red_executable with a syntactically-broken bash .test.sh
    returns error_code E_RED_COLLECT_FAILED.

    Pre-GREEN: PASS incidentally — pytest --collect-only also exits non-zero on .sh.
    Post-GREEN: bash -n exits non-zero on broken syntax → E_RED_COLLECT_FAILED.
    Either way the behavior is E_RED_COLLECT_FAILED; the path that produces it changes.
    """
    sh_file = tmp_path / "broken_test.test.sh"
    # 'if then fi' is syntactically invalid bash (missing condition between if and then)
    sh_file.write_text("#!/usr/bin/env bash\nif then fi\n")

    prev = make_prev(red_test_paths=[str(sh_file)], cycle=1)
    ctx = make_ctx("SIMPLE")

    result = _check_red_executable(ctx, prev)

    assert result.status == "error", (
        f"4238DD14 AC5: broken bash .test.sh must return status='error', "
        f"got status={result.status!r}"
    )
    assert getattr(result, "error_code", None) == "E_RED_COLLECT_FAILED", (
        f"4238DD14 AC5: broken bash .test.sh must return error_code='E_RED_COLLECT_FAILED', "
        f"got error_code={getattr(result, 'error_code', None)!r}"
    )


# ─── AC6: valid collectable .py → status == "ok" ─────────────────────────────


def test_check_red_executable_valid_py_ok(tmp_path):
    """AC6: _check_red_executable with a valid collectable .py test file
    returns status="ok".

    Pre-GREEN: PASS — current code already runs pytest --collect-only on .py files
    and this succeeds for a simple test file.
    """
    py_file = tmp_path / "test_valid_4238dd14.py"
    py_file.write_text("def test_x():\n    assert True\n")

    prev = make_prev(red_test_paths=[str(py_file)], cycle=1)
    ctx = make_ctx("SIMPLE")

    result = _check_red_executable(ctx, prev)

    assert result.status == "ok", (
        f"4238DD14 AC6: valid collectable .py must return status='ok', "
        f"got status={result.status!r}, error_code={getattr(result, 'error_code', None)!r}"
    )


# ─── AC7: uncollectable .py (import error) → E_RED_COLLECT_FAILED ────────────


def test_check_red_executable_uncollectable_py_collect_failed(tmp_path):
    """AC7: _check_red_executable with a .py file that fails pytest collect
    (module-top ImportError on a nonexistent package) returns E_RED_COLLECT_FAILED.

    Pre-GREEN: PASS — current code runs pytest --collect-only, which surfaces the
    ImportError → non-zero exit → E_RED_COLLECT_FAILED.
    Post-GREEN: same behavior (py path unchanged).
    """
    py_file = tmp_path / "test_uncollectable_4238dd14.py"
    py_file.write_text("import does_not_exist_xyz_4238dd14\ndef test_x():\n    pass\n")

    prev = make_prev(red_test_paths=[str(py_file)], cycle=1)
    ctx = make_ctx("SIMPLE")

    result = _check_red_executable(ctx, prev)

    assert result.status == "error", (
        f"4238DD14 AC7: uncollectable .py (import error) must return status='error', "
        f"got status={result.status!r}"
    )
    assert getattr(result, "error_code", None) == "E_RED_COLLECT_FAILED", (
        f"4238DD14 AC7: uncollectable .py must return error_code='E_RED_COLLECT_FAILED', "
        f"got error_code={getattr(result, 'error_code', None)!r}"
    )


# ─── AC8 (CORE): mixed valid .sh + valid .py → status == "ok" ────────────────


def test_check_red_executable_mixed_valid_sh_and_py_ok(tmp_path):
    """AC8 (CORE): _check_red_executable with two paths (one valid .sh + one valid .py)
    returns status="ok".

    Pre-GREEN: FAIL — current code runs pytest --collect-only on the .sh first,
    fails → E_RED_COLLECT_FAILED, never reaches the .py.
    Post-GREEN: bash -n passes for .sh, pytest --collect-only passes for .py → "ok".

    Uses real subprocesses (no monkeypatch) to exercise the full dispatch path.
    §1i: no singleton resource; both temp files are path-unique under tmp_path.
    """
    sh_file = tmp_path / "valid_test.test.sh"
    sh_file.write_text("#!/usr/bin/env bash\n: ;\n")

    py_file = tmp_path / "test_valid_4238dd14_ac8.py"
    py_file.write_text("def test_y():\n    assert True\n")

    prev = make_prev(red_test_paths=[str(sh_file), str(py_file)], cycle=1)
    ctx = make_ctx("SIMPLE")

    result = _check_red_executable(ctx, prev)

    assert result.status == "ok", (
        f"4238DD14 AC8 (CORE): valid .sh + valid .py must return status='ok', "
        f"got status={result.status!r}, error_code={getattr(result, 'error_code', None)!r}. "
        f"Pre-GREEN: pytest --collect-only on .sh fails → E_RED_COLLECT_FAILED. "
        f"Post-GREEN: bash -n + pytest --collect-only both pass → status='ok'."
    )
