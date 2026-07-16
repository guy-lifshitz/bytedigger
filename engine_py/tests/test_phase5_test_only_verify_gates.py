"""#268 RED tests — phase_5 verify-gate test_only_mode bypass.

When prev.data contains commit_red_tests_skipped=="test_only_mode" (set by the
already-shipped _commit_red_tests skip branch), BOTH _verify_red_fails_mechanically
AND _verify_red_lint_rules must short-circuit to status="ok" without invoking any
test subprocess.

AC1, AC2, AC4, AC6, AC7: FAIL against current prod (guard not yet inserted).
AC3, AC5: PASS against current prod (regression floors, must stay green).

Do NOT implement the contract here — RED-only file.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

HERE = Path(__file__).parent
ENGINE_ROOT = HERE.parent
sys.path.insert(0, str(ENGINE_ROOT))
sys.path.insert(0, str(ENGINE_ROOT / "lib"))
sys.path.insert(0, str(ENGINE_ROOT / "workflows"))

from contracts import StepResult, WorkflowContext  # noqa: E402
import phase_5_implement as _p5  # noqa: E402
from phase_5_implement import (  # noqa: E402
    _verify_red_fails_mechanically,
    _verify_red_lint_rules,
)


# ─── helpers ──────────────────────────────────────────────────────────────────


def _make_ctx() -> WorkflowContext:
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={},
        question="test question",
        session_id="test-session",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_prev(data: dict) -> StepResult:
    return StepResult(
        status="ok",
        data=data,
        duration_ms=0,
        step_name="commit_red_tests",
    )


def _patch_emit(monkeypatch) -> list[dict]:
    """Capture _emit_safe calls in phase_5_implement."""
    captured: list[dict] = []
    monkeypatch.setattr(
        _p5,
        "_emit_safe",
        lambda et, p, severity="warning": captured.append(
            {"type": et, "payload": p, "severity": severity}
        ),
    )
    return captured


# ─── AC1: flag + empty paths → _verify_red_fails_mechanically returns ok ──────


def test_ac1_verify_red_fails_mechanically_skips_on_flag(monkeypatch, tmp_path):
    """AC1: flag set + red_test_paths=[] → status==ok, step_name correct, NOT E_RED_NO_PATHS."""
    _patch_emit(monkeypatch)
    # Stub _resolve_git_cwd to avoid touching filesystem
    monkeypatch.setattr(_p5, "_resolve_git_cwd", lambda ctx, prev: str(tmp_path))

    ctx = _make_ctx()
    prev = _make_prev({
        "commit_red_tests_skipped": "test_only_mode",
        "red_test_paths": [],
    })

    result = _verify_red_fails_mechanically(ctx, prev)

    assert result.status == "ok", (
        f"AC1: expected status='ok', got {result.status!r}, "
        f"error_code={result.error_code!r}"
    )
    assert result.step_name == "verify_red_fails_mechanically", (
        f"AC1: wrong step_name: {result.step_name!r}"
    )
    assert result.error_code is None, (
        f"AC1: expected error_code=None (NOT E_RED_NO_PATHS), got {result.error_code!r}"
    )


# ─── AC2: flag set + non-empty paths → no subprocess spawned ─────────────────


def test_ac2_no_subprocess_spawned_when_flag_set(monkeypatch, tmp_path):
    """AC2: flag set + red_test_paths non-empty → ok, bounded_run never called."""
    _patch_emit(monkeypatch)

    subprocess_called = []

    def _recording_bounded_run(*a, **kw):
        subprocess_called.append(a)
        raise AssertionError("bounded_run must NOT be called when test_only_mode is set")

    def _recording_run_test_command(*a, **kw):
        subprocess_called.append(a)
        raise AssertionError("run_test_command must NOT be called when test_only_mode is set")

    monkeypatch.setattr(_p5, "bounded_run", _recording_bounded_run)
    monkeypatch.setattr(_p5, "run_test_command", _recording_run_test_command)
    monkeypatch.setattr(_p5, "_resolve_git_cwd", lambda ctx, prev: str(tmp_path))

    ctx = _make_ctx()
    prev = _make_prev({
        "commit_red_tests_skipped": "test_only_mode",
        "red_test_paths": ["t_x.py"],
    })

    result = _verify_red_fails_mechanically(ctx, prev)

    assert result.status == "ok", (
        f"AC2: expected status='ok', got {result.status!r}, "
        f"error_code={result.error_code!r}"
    )
    assert not subprocess_called, (
        "AC2: bounded_run / run_test_command was called despite test_only_mode flag"
    )


# ─── AC3 (regression floor): no flag + all-passing → E_RED_NOT_FAILING ───────


def test_ac3_regression_floor_no_flag_all_passing_gives_error(monkeypatch, tmp_path):
    """AC3 regression floor: NO flag, tests all pass → E_RED_NOT_FAILING still fires."""
    _patch_emit(monkeypatch)

    # Stub bounded_run: returncode=0 → all tests passing
    fake_proc = SimpleNamespace(returncode=0, stdout="1 passed")
    monkeypatch.setattr(_p5, "bounded_run", lambda *a, **kw: fake_proc)

    # Stub run_test_command: telemetry call
    fake_dt = SimpleNamespace(exit_code=0, n_passed=1, n_failed=0)
    monkeypatch.setattr(_p5, "run_test_command", lambda *a, **kw: fake_dt)

    # Stub _infer_test_command_for_paths to return a simple py group
    monkeypatch.setattr(
        _p5,
        "_infer_test_command_for_paths",
        lambda paths, git_cwd=None: {
            "groups": [{"kind": "py", "argv": ["pytest", "t_x.py"], "paths": ["t_x.py"]}]
        },
    )
    monkeypatch.setattr(_p5, "_resolve_git_cwd", lambda ctx, prev: str(tmp_path))

    ctx = _make_ctx()
    # NO commit_red_tests_skipped flag
    prev = _make_prev({"red_test_paths": ["t_x.py"]})

    result = _verify_red_fails_mechanically(ctx, prev)

    assert result.error_code == "E_RED_NOT_FAILING", (
        f"AC3: expected E_RED_NOT_FAILING (normal regression floor), "
        f"got status={result.status!r}, error_code={result.error_code!r}"
    )


# ─── AC4: flag + empty paths → _verify_red_lint_rules returns ok ─────────────


def test_ac4_verify_red_lint_rules_skips_on_flag(monkeypatch, tmp_path):
    """AC4: flag set + red_test_paths=[] → status==ok, step_name correct, NOT E_RED_EMPTY_FILES."""
    _patch_emit(monkeypatch)
    monkeypatch.setattr(_p5, "_resolve_git_cwd", lambda ctx, prev: str(tmp_path))

    ctx = _make_ctx()
    prev = _make_prev({
        "commit_red_tests_skipped": "test_only_mode",
        "red_test_paths": [],
    })

    result = _verify_red_lint_rules(ctx, prev)

    assert result.status == "ok", (
        f"AC4: expected status='ok', got {result.status!r}, "
        f"error_code={result.error_code!r}"
    )
    assert result.step_name == "verify_red_lint_rules", (
        f"AC4: wrong step_name: {result.step_name!r}"
    )
    assert result.error_code is None, (
        f"AC4: expected error_code=None (NOT E_RED_EMPTY_FILES), got {result.error_code!r}"
    )


# ─── AC5 (regression floor): no flag + empty paths → E_RED_EMPTY_FILES ───────


def test_ac5_regression_floor_no_flag_empty_paths_gives_error(monkeypatch):
    """AC5 regression floor: NO flag, red_test_paths=[] → E_RED_EMPTY_FILES still fires."""
    _patch_emit(monkeypatch)

    ctx = _make_ctx()
    # NO commit_red_tests_skipped flag
    prev = _make_prev({"red_test_paths": []})

    result = _verify_red_lint_rules(ctx, prev)

    assert result.error_code == "E_RED_EMPTY_FILES", (
        f"AC5: expected E_RED_EMPTY_FILES (normal regression floor), "
        f"got status={result.status!r}, error_code={result.error_code!r}"
    )


# ─── AC6: data pass-through — both functions forward prev.data unchanged ──────


def test_ac6_data_passthrough_verify_red_fails_mechanically(monkeypatch, tmp_path):
    """AC6a: _verify_red_fails_mechanically with flag passes prev.data through."""
    _patch_emit(monkeypatch)
    monkeypatch.setattr(_p5, "_resolve_git_cwd", lambda ctx, prev: str(tmp_path))

    ctx = _make_ctx()
    prev = _make_prev({
        "commit_red_tests_skipped": "test_only_mode",
        "red_test_paths": [],
        "spec_path": "/x",
    })

    result = _verify_red_fails_mechanically(ctx, prev)

    assert result.status == "ok", f"AC6a: expected ok, got {result.status!r}"
    assert isinstance(result.data, dict), (
        f"AC6a: result.data must be a dict, got {type(result.data)}"
    )
    assert "commit_red_tests_skipped" in result.data, (
        "AC6a: returned data must contain 'commit_red_tests_skipped'"
    )
    assert "spec_path" in result.data, (
        "AC6a: returned data must contain 'spec_path' (pass-through)"
    )
    assert result.data["spec_path"] == "/x", (
        f"AC6a: spec_path pass-through wrong: {result.data['spec_path']!r}"
    )


def test_ac6_data_passthrough_verify_red_lint_rules(monkeypatch, tmp_path):
    """AC6b: _verify_red_lint_rules with flag passes prev.data through."""
    _patch_emit(monkeypatch)
    monkeypatch.setattr(_p5, "_resolve_git_cwd", lambda ctx, prev: str(tmp_path))

    ctx = _make_ctx()
    prev = _make_prev({
        "commit_red_tests_skipped": "test_only_mode",
        "red_test_paths": [],
        "spec_path": "/x",
    })

    result = _verify_red_lint_rules(ctx, prev)

    assert result.status == "ok", f"AC6b: expected ok, got {result.status!r}"
    assert isinstance(result.data, dict), (
        f"AC6b: result.data must be a dict, got {type(result.data)}"
    )
    assert "commit_red_tests_skipped" in result.data, (
        "AC6b: returned data must contain 'commit_red_tests_skipped'"
    )
    assert "spec_path" in result.data, (
        "AC6b: returned data must contain 'spec_path' (pass-through)"
    )
    assert result.data["spec_path"] == "/x", (
        f"AC6b: spec_path pass-through wrong: {result.data['spec_path']!r}"
    )


# ─── AC7: _emit_safe called with correct event name + reason ──────────────────


def test_ac7_emit_safe_called_verify_red_fails_mechanically(monkeypatch, tmp_path):
    """AC7a: _verify_red_fails_mechanically emits red_verify_skipped_test_only with reason."""
    captured = _patch_emit(monkeypatch)
    monkeypatch.setattr(_p5, "_resolve_git_cwd", lambda ctx, prev: str(tmp_path))

    ctx = _make_ctx()
    prev = _make_prev({
        "commit_red_tests_skipped": "test_only_mode",
        "red_test_paths": [],
    })

    _verify_red_fails_mechanically(ctx, prev)

    skip_events = [e for e in captured if e["type"] == "red_verify_skipped_test_only"]
    assert len(skip_events) >= 1, (
        f"AC7a: expected at least 1 'red_verify_skipped_test_only' event, "
        f"got {len(skip_events)}. All events: {[e['type'] for e in captured]}"
    )
    payload = skip_events[0]["payload"]
    assert payload.get("reason") == "test_only_mode", (
        f"AC7a: expected reason='test_only_mode', got payload={payload!r}"
    )


def test_ac7_emit_safe_called_verify_red_lint_rules(monkeypatch, tmp_path):
    """AC7b: _verify_red_lint_rules emits red_verify_skipped_test_only with reason."""
    captured = _patch_emit(monkeypatch)
    monkeypatch.setattr(_p5, "_resolve_git_cwd", lambda ctx, prev: str(tmp_path))

    ctx = _make_ctx()
    prev = _make_prev({
        "commit_red_tests_skipped": "test_only_mode",
        "red_test_paths": [],
    })

    _verify_red_lint_rules(ctx, prev)

    skip_events = [e for e in captured if e["type"] == "red_verify_skipped_test_only"]
    assert len(skip_events) >= 1, (
        f"AC7b: expected at least 1 'red_verify_skipped_test_only' event, "
        f"got {len(skip_events)}. All events: {[e['type'] for e in captured]}"
    )
    payload = skip_events[0]["payload"]
    assert payload.get("reason") == "test_only_mode", (
        f"AC7b: expected reason='test_only_mode', got payload={payload!r}"
    )
