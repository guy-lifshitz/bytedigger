"""RED tests for Pillar 3: mechanical pytest+exit-code gate in phase_5_implement.

Inserted between commit_red_tests and build_validation_prompt. The new
helper `_verify_red_fails_mechanically(ctx, prev)` runs pytest on the RED
test paths and gates on exit code:
    rc == 1                       -> ok (RED is real)
    rc == 0                       -> error E_RED_NOT_FAILING (fake RED)
    FileNotFoundError             -> error E_PYTEST_MISSING
    subprocess.TimeoutExpired     -> error E_RED_PYTEST_TIMEOUT  (covered elsewhere)

These tests fake `subprocess.run` via monkeypatch — no real pytest is spawned.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

from bytedigger_engine.contracts import StepResult, WorkflowContext  # noqa: E402
from bytedigger_engine.workflows import phase_5_implement  # noqa: E402
from bytedigger_engine.workflows.phase_5_implement import _verify_red_fails_mechanically  # noqa: E402


def _make_ctx(tmp_path: Path) -> WorkflowContext:
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={"git_cwd": str(tmp_path)},
        question="q",
        session_id="test-session",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_prev() -> StepResult:
    return StepResult(
        status="ok",
        data={
            "red_test_paths": ["tests/test_x.py"],
            "red_log_path": "tests/build-red-output.log",
            "red_bytes_written": 42,
            "cycle": 1,
            "spec_path": "scratchpad/spec.md",
        },
        duration_ms=0,
        step_name="commit_red_tests",
    )


def _fake_completed(returncode: int) -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["python3", "-m", "pytest"], returncode=returncode, stdout="", stderr=""
    )


def test_verify_red_fails_mechanically_passes_on_exit_code_1(tmp_path, monkeypatch):
    monkeypatch.setattr(
        phase_5_implement.subprocess, "run", lambda *a, **kw: _fake_completed(1)
    )
    result = _verify_red_fails_mechanically(_make_ctx(tmp_path), _make_prev())
    assert result.status == "ok"
    assert result.data["red_test_paths"] == ["tests/test_x.py"]


def test_verify_red_fails_mechanically_fails_on_exit_code_0(tmp_path, monkeypatch):
    monkeypatch.setattr(
        phase_5_implement.subprocess, "run", lambda *a, **kw: _fake_completed(0)
    )
    result = _verify_red_fails_mechanically(_make_ctx(tmp_path), _make_prev())
    assert result.status == "error"
    assert result.error_code == "E_RED_NOT_FAILING"


def test_verify_red_fails_mechanically_fails_on_pytest_missing(tmp_path, monkeypatch):
    def _raise(*_a, **_kw):
        raise FileNotFoundError("python3 not found")

    monkeypatch.setattr(phase_5_implement.subprocess, "run", _raise)
    result = _verify_red_fails_mechanically(_make_ctx(tmp_path), _make_prev())
    assert result.status == "error"
    assert result.error_code == "E_PYTEST_MISSING"
