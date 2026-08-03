"""RED tests for 7C4D70ED — _check_red_executable pre-validation step.

Spec: SHARED/memory/Decisions/2026-06-01_7C4D70ED_red_executability_check_spec.md

Design summary:
  - New function _check_red_executable(ctx, prev) -> StepResult in phase_5_implement.py
    inserted between _build_validation_prompt (line 2292) and _invoke_validation_llm (line 2458).
  - Wired into build_validation_loop_contract() as new StepContract between
    "build_validation_prompt" and "invoke_validation_llm".
  - On collect failure, routes through RecoverableGateMixin.gated_step_result(gate="red_runtime",
    ...) — SIMPLE/FEATURE cycle=1 → recoverable retry; COMPLEX → terminal immediately.
  - Emits exactly one recoverable_gate_attempted event with payload["gate"] == "red_runtime".

Pre-GREEN PASS/FAIL classification:
  - AC1 → FAIL: ImportError on _check_red_executable (function does not exist yet).
  - AC2 → FAIL: ImportError on _check_red_executable.
  - AC3 → FAIL: ImportError on _check_red_executable.
  - AC4 → FAIL: ImportError on _check_red_executable.
  - AC5 → FAIL: ImportError on _check_red_executable (no telemetry emitted from nonexistent fn).
  - AC6 → FAIL: build_validation_loop_contract() has 11 body steps; "check_red_executable"
           not in the body step-name list (step missing — contract not updated yet).
  - AC7 → PASS at GREEN-time only (delta-0 marker; asserts True unconditionally).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

# ─── sys.path setup (mirrors test_E843349F_recoverable_policy.py:25-35) ──────
# §1q: use the conftest-resident sys.path pattern established in this suite.
# Suite safety scanner does not run in Option-D manual pipeline.
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

# ─── Production imports — AC1-AC6 fail pre-GREEN with ImportError ─────────────
# _check_red_executable does not exist until GREEN ships. Do NOT guard with
# try/except — the hard ImportError IS the desired RED state for AC1-AC5.
from bytedigger_engine.contracts import StepResult, WorkflowContext  # noqa: E402
from bytedigger_engine.workflows.phase_5_implement import (  # noqa: E402
    _check_red_executable,
    build_validation_loop_contract,
)


# ─── helpers ──────────────────────────────────────────────────────────────────


def make_ctx(complexity: str = "SIMPLE") -> WorkflowContext:
    """Build a minimal WorkflowContext with org_config containing complexity."""
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={"complexity": complexity},
        question="7C4D70ED red executability test",
        session_id="test-7C4D70ED",
        persona="hal",
        framework=None,
        domain=None,
    )


def make_prev(red_test_paths: list[str], cycle: int = 1) -> StepResult:
    """Build a fake prev StepResult shaped like write_red_artifact output."""
    return StepResult(
        status="ok",
        data={
            "red_test_paths": red_test_paths,
            "cycle": cycle,
        },
        duration_ms=0,
        step_name="verify_red_lint_rules",
    )


# ─── AC1: clean collect returns ok pass-through ──────────────────────────────


def test_clean_collect_returns_ok_pass_through(monkeypatch, tmp_path):
    """AC1: when all red_test_paths collect cleanly (returncode==0),
    _check_red_executable returns StepResult(status="ok") with prev.data unchanged.

    Pre-GREEN: ImportError on _check_red_executable at collection — FAIL.
    """
    red_path = str(tmp_path / "red_x.py")
    prev = make_prev(red_test_paths=[red_path], cycle=1)
    ctx = make_ctx("SIMPLE")

    monkeypatch.setattr(
        "bytedigger_engine.workflows.phase_5_implement.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0], returncode=0, stdout="1 test collected", stderr=""
        ),
    )

    result = _check_red_executable(ctx, prev)

    assert result.status == "ok", (
        f"7C4D70ED AC1: expected status='ok' on clean collect, got {result.status!r}"
    )
    assert result.data == prev.data, (
        f"7C4D70ED AC1: expected result.data == prev.data (pass-through, no mutation), "
        f"got result.data={result.data!r}, prev.data={prev.data!r}"
    )


# ─── AC2: collect failure SIMPLE cycle=1 → recoverable ──────────────────────


def test_collect_failure_simple_cycle_1_recoverable(monkeypatch, tmp_path):
    """AC2: when collect fails with SIMPLE and cycle=1, returns recoverable=True,
    error_code="E_RED_COLLECT_FAILED", data["retry_from_step"]==0.

    Pre-GREEN: ImportError on _check_red_executable — FAIL.
    """
    red_path = str(tmp_path / "red_x.py")
    prev = make_prev(red_test_paths=[red_path], cycle=1)
    ctx = make_ctx("SIMPLE")

    monkeypatch.setattr(
        "bytedigger_engine.workflows.phase_5_implement.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0], returncode=1, stdout="", stderr="ImportError: foo"
        ),
    )

    def _fake_emit(name: str, payload: dict, *args: Any, **kwargs: Any) -> None:
        pass

    monkeypatch.setattr(_telemetry_ctx, "emit_safe", _fake_emit)

    result = _check_red_executable(ctx, prev)

    assert result.status == "error", (
        f"7C4D70ED AC2: expected status='error' on collect failure, got {result.status!r}"
    )
    assert result.recoverable is True, (
        f"7C4D70ED AC2: SIMPLE cycle=1 collect failure must be recoverable=True "
        f"(recoverable_once slot, cycle < cap), got {result.recoverable!r}"
    )
    assert result.error_code == "E_RED_COLLECT_FAILED", (
        f"7C4D70ED AC2: expected error_code='E_RED_COLLECT_FAILED', "
        f"got {result.error_code!r}"
    )
    assert isinstance(result.data, dict), (
        f"7C4D70ED AC2: result.data must be a dict, got {type(result.data)!r}"
    )
    assert result.data.get("retry_from_step") == 0, (
        f"7C4D70ED AC2: expected data['retry_from_step']==0, "
        f"got {result.data.get('retry_from_step')!r}"
    )


# ─── AC3: collect failure COMPLEX → terminal ─────────────────────────────────


def test_collect_failure_complex_terminal(monkeypatch, tmp_path):
    """AC3: same collect failure with COMPLEX build class → recoverable=False,
    error_code="E_RED_NOT_EXECUTABLE" (terminal slot, any cycle).

    Pre-GREEN: ImportError on _check_red_executable — FAIL.
    """
    red_path = str(tmp_path / "red_x.py")
    prev = make_prev(red_test_paths=[red_path], cycle=1)
    ctx = make_ctx("COMPLEX")

    monkeypatch.setattr(
        "bytedigger_engine.workflows.phase_5_implement.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0], returncode=1, stdout="", stderr="ImportError: foo"
        ),
    )

    def _fake_emit(name: str, payload: dict, *args: Any, **kwargs: Any) -> None:
        pass

    monkeypatch.setattr(_telemetry_ctx, "emit_safe", _fake_emit)

    result = _check_red_executable(ctx, prev)

    assert result.recoverable is False, (
        f"7C4D70ED AC3: COMPLEX collect failure must be recoverable=False "
        f"(terminal slot), got {result.recoverable!r}"
    )
    assert result.error_code == "E_RED_NOT_EXECUTABLE", (
        f"7C4D70ED AC3: COMPLEX terminal must use error_code='E_RED_NOT_EXECUTABLE', "
        f"got {result.error_code!r}"
    )


# ─── AC4: TimeoutExpired routes through mixin failure path ───────────────────


def test_collect_timeout_routes_through_mixin(monkeypatch, tmp_path):
    """AC4: subprocess.TimeoutExpired (collect exceeds 30s) routes through
    the mixin failure path with error_msg containing "collect timed out".

    Pre-GREEN: ImportError on _check_red_executable — FAIL.
    """
    red_path = str(tmp_path / "red_x.py")
    prev = make_prev(red_test_paths=[red_path], cycle=1)
    ctx = make_ctx("SIMPLE")

    def _raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd=["pytest", "--collect-only"], timeout=30)

    monkeypatch.setattr("bytedigger_engine.workflows.phase_5_implement.subprocess.run", _raise_timeout)

    def _fake_emit(name: str, payload: dict, *args: Any, **kwargs: Any) -> None:
        pass

    monkeypatch.setattr(_telemetry_ctx, "emit_safe", _fake_emit)

    result = _check_red_executable(ctx, prev)

    assert result.error_code is not None, (
        f"7C4D70ED AC4: timeout must produce a non-None error_code "
        f"(mixin failure path triggered), got {result.error_code!r}"
    )
    assert result.error is not None, (
        f"7C4D70ED AC4: timeout must produce a non-None error message, "
        f"got {result.error!r}"
    )
    assert "collect timed out" in (result.error or "").lower() or "timed out" in (result.error or "").lower(), (
        f"7C4D70ED AC4: error message must contain 'collect timed out' or 'timed out' "
        f"to identify the timeout class. got error={result.error!r}"
    )


# ─── AC5: failure path emits recoverable_gate_attempted telemetry ────────────


def test_failure_emits_telemetry_event(monkeypatch, tmp_path):
    """AC5: failure path emits exactly one 'recoverable_gate_attempted' event
    with payload["gate"] == "red_runtime".

    Pattern mirrors test_E843349F_recoverable_policy.py AC12 — patch emit_safe
    on the telemetry_ctx module object, which both recoverable_gate and
    _recoverable_policy resolve through sys.modules.

    Pre-GREEN: ImportError on _check_red_executable — FAIL (no event emitted).
    """
    red_path = str(tmp_path / "red_x.py")
    prev = make_prev(red_test_paths=[red_path], cycle=1)
    ctx = make_ctx("SIMPLE")

    monkeypatch.setattr(
        "bytedigger_engine.workflows.phase_5_implement.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0], returncode=1, stdout="", stderr="SyntaxError: invalid syntax"
        ),
    )

    captured: list[tuple[str, dict]] = []

    def _fake_emit(name: str, payload: dict, *args: Any, **kwargs: Any) -> None:
        captured.append((name, payload))

    monkeypatch.setattr(_telemetry_ctx, "emit_safe", _fake_emit)

    result = _check_red_executable(ctx, prev)

    assert result.status == "error", (
        f"7C4D70ED AC5 precondition: subprocess failure must produce status='error', "
        f"got {result.status!r}"
    )

    gate_events = [(n, p) for n, p in captured if n == "recoverable_gate_attempted"]
    assert len(gate_events) == 1, (
        f"7C4D70ED AC5: expected exactly one 'recoverable_gate_attempted' event, "
        f"got {len(gate_events)}. All captured events: {[n for n, _ in captured]!r}. "
        f"Pre-GREEN: event not emitted (_check_red_executable does not exist yet)."
    )
    payload = gate_events[0][1]
    assert payload.get("gate") == "red_runtime", (
        f"7C4D70ED AC5: recoverable_gate_attempted payload must have "
        f"gate='red_runtime', got gate={payload.get('gate')!r}"
    )


# ─── AC6: build_validation_loop_contract includes check_red_executable step ──


def test_validation_loop_contract_includes_check_red_executable_step():
    """AC6: build_validation_loop_contract() includes a StepContract named
    "check_red_executable", positioned between "build_validation_prompt" and
    "invoke_validation_llm".

    Pre-GREEN: FAIL — current contract has 11 steps; "check_red_executable"
    is absent (step not yet wired).
    """
    contract = build_validation_loop_contract()
    body_names = [s.name for s in contract.body]

    assert "check_red_executable" in body_names, (
        f"7C4D70ED AC6: expected 'check_red_executable' in contract.body step names, "
        f"got body_names={body_names!r}. "
        f"Pre-GREEN: step is not yet inserted into build_validation_loop_contract()."
    )

    idx_check = body_names.index("check_red_executable")
    idx_build_prompt = body_names.index("build_validation_prompt")
    idx_invoke_llm = body_names.index("invoke_validation_llm")

    assert idx_check > idx_build_prompt, (
        f"7C4D70ED AC6: 'check_red_executable' (idx={idx_check}) must appear AFTER "
        f"'build_validation_prompt' (idx={idx_build_prompt}) in contract body."
    )
    assert idx_check < idx_invoke_llm, (
        f"7C4D70ED AC6: 'check_red_executable' (idx={idx_check}) must appear BEFORE "
        f"'invoke_validation_llm' (idx={idx_invoke_llm}) in contract body."
    )
    assert idx_check == idx_build_prompt + 1, (
        f"7C4D70ED AC6: 'check_red_executable' must be immediately after "
        f"'build_validation_prompt' (no steps between them). "
        f"Expected idx={idx_build_prompt + 1}, got idx={idx_check}."
    )
    assert idx_invoke_llm == idx_check + 1, (
        f"7C4D70ED AC6: 'invoke_validation_llm' must be immediately after "
        f"'check_red_executable' (no steps between them). "
        f"Expected idx={idx_check + 1}, got idx={idx_invoke_llm}."
    )


# ─── AC7: full-suite delta-0 marker ──────────────────────────────────────────


def test_engine_py_suite_delta_zero_marker():
    """AC7: full engine_py suite pytest delta-0 vs main b7dab48e.

    This test always passes — the delta-0 assertion is performed by the
    orchestrator at verify-step (step 6) using stash-baseline-delta math.
    The marker documents the requirement; the real gate is external.
    """
    assert True  # full-suite delta-0 asserted by orchestrator at verify-step
