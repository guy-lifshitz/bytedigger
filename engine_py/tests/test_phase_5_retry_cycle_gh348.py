"""RED tests for GH348 — single-owner cycle threading for red_runtime retry
(ENG-1) + honest cap semantics (ENG-10).

Spec: SHARED/memory/Decisions/2026-07-05_gh348_retry_cycle_spec.md

Design summary (frozen §2):
  1. phase_5_implement.py:2997 — remove the `-1` compensation in
     _check_red_executable's gated_step_result call: cycle=int(prev.data.get(
     "cycle", 1)) (was cycle=max(int(prev.data.get("cycle", 1)) - 1, 0)).
  2. _recoverable_policy.py:56-57 — red_runtime SIMPLE + FEATURE:
     RecoverablePolicy("recoverable_once", 1) -> RecoverablePolicy(
     "recoverable_twice", 2). COMPLEX row (:58, terminal/0) UNCHANGED.
  3. _recoverable_policy.py:31 — cycle_cap field comment gains the literal
     "retries granted = cap - 1".

Pre-GREEN PASS/FAIL classification (per spec §3):
  AC1 -> FAIL (today cycle_count==0, not 1 — the -1 compensation + cap=1
        policy combo)
  AC2 -> FAIL (same as AC1, FEATURE build class)
  AC3 -> PASS (deliberate pin — today's -1 compensation + cap=1 policy
        happen to cap at cycle=2 the same way post-GREEN's raw-cycle + cap=2
        policy caps at cycle=2; guards the frozen `>=` comparison against an
        over-fix flip to `>`, R-BLOCKER #2)
  AC4a -> FAIL (today SIMPLE/red_runtime resolves to recoverable_once/1)
  AC4b -> FAIL (today FEATURE/red_runtime resolves to recoverable_once/1)
  AC4c -> PASS (deliberate pin — COMPLEX/red_runtime terminal/0 unchanged
        by this ship)
  AC5 -> FAIL (today telemetry payload carries cycle==0, cycle_cap==1;
        AC asserts cycle==1, cycle_cap==2)
  AC6 -> FAIL (today SIMPLE/red_runtime cap=1 caps on the FIRST call
        (cycle defaults to 1, 1>=1), so the stub step executes exactly
        once and the engine never re-drives the retry path; counter==2
        assertion fails against an actual count of 1. Post-GREEN cap=2
        lets the first call retry (1<2), the engine threads cycle=2 into
        a second drive, and the second call hits the cap (2>=2) ->
        terminates cleanly at counter==2. No RecursionError guard is used
        here — whatever the real cycle-threading defect produces (short-
        circuit termination OR runaway recursion) is left to surface as a
        natural pytest failure/error, never caught.)
  AC7 -> FAIL (today's cycle_cap comment does not contain the literal
        "retries granted = cap - 1")

Anchor classification (§1l): AC1/2/5/6 anchor on production side-effects of
REAL prod bodies (_check_red_executable, RecoverableGateMixin.gated_step_result,
WorkflowEngine._execute_steps — no UUT mocks). AC6 is the multi-call N>1 AC
(engine drives the stub step twice post-GREEN). AC4/AC7 anchor on the real
policy matrix / source text. No AC is empty-shape.

RED discipline (§5): all imported symbols exist today (no D1CF5FDF collect
risk). monkeypatch used ONLY for phase_5_implement.subprocess.run (collect-
probe infra) and telemetry_ctx.emit_safe (capture) — never for
_check_red_executable / gated_step_result / resolve_policy / _execute_steps
(the UUTs under change).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

# ─── sys.path setup (mirrors test_7C4D70ED_red_executability_check.py:31-42) ──
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

# ─── Production imports — all symbols exist today; no ImportError risk ────────
from bytedigger_engine.contracts import (  # noqa: E402
    StepContract,
    StepResult,
    WorkflowContext,
    WorkflowDefinition,
)
from bytedigger_engine.workflows.phase_5_implement import _check_red_executable  # noqa: E402
from bytedigger_engine.workflows._recoverable_policy import resolve_policy, RecoverablePolicy  # noqa: E402
from bytedigger_engine.lib.recoverable_gate import RecoverableGateMixin  # noqa: E402
from bytedigger_engine.engine import WorkflowEngine  # noqa: E402


# ─── helpers (mirrors test_7C4D70ED_red_executability_check.py:60-85) ─────────


def make_ctx(complexity: str = "SIMPLE") -> WorkflowContext:
    """Build a minimal WorkflowContext with org_config containing complexity."""
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={"complexity": complexity},
        question="GH348 retry cycle test",
        session_id="test-gh348",
        persona="hal",
        framework=None,
        domain=None,
    )


def make_prev(
    red_test_paths: list[str], cycle: int = 1, gate_attempts: dict | None = None
) -> StepResult:
    """Build a fake prev StepResult shaped like write_red_artifact output.

    GH625: `gate_attempts` optionally seeds per-gate attempt accounting —
    `_check_red_executable` passes `forwarded_data=prev.data` verbatim
    (phase_5_implement.py:4128), so this key flows straight into
    gated_step_result's cap decision.
    """
    data: dict = {
        "red_test_paths": red_test_paths,
        "cycle": cycle,
    }
    if gate_attempts is not None:
        data["gate_attempts"] = gate_attempts
    return StepResult(
        status="ok",
        data=data,
        duration_ms=0,
        step_name="verify_red_lint_rules",
    )


def _mock_collect_failure(monkeypatch) -> None:
    """Force the collect-probe branch: subprocess.run returns rc=1 with a
    synthetic SyntaxError stderr, mirroring test_7C4D70ED AC2/AC5.
    """
    monkeypatch.setattr(
        "bytedigger_engine.workflows.phase_5_implement.subprocess.run",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args=args[0], returncode=1, stdout="", stderr="SyntaxError: invalid syntax"
        ),
    )


def _silence_telemetry(monkeypatch) -> None:
    def _fake_emit(name: str, payload: dict, *args: Any, **kwargs: Any) -> None:
        pass
    monkeypatch.setattr(_telemetry_ctx, "emit_safe", _fake_emit)


# ─── AC1: SIMPLE collect-fail at cycle=1 → cycle_count==1, retry_from_step==0 ─


def test_ac1_simple_collect_fail_cycle1_yields_cycle_count_1(monkeypatch, tmp_path):
    """AC1: SIMPLE collect-fail at cycle=1 -> status='error', recoverable=True,
    error_code='E_RED_COLLECT_FAILED', data['cycle_count']==1, data['retry_from_step']==0.

    Pre-GREEN FAIL: the -1 compensation at phase_5_implement.py:2997 sends
    cycle=0 into gated_step_result, so retry_data['cycle_count'] is 0, not 1.
    """
    red_path = str(tmp_path / "red_x.py")
    prev = make_prev(red_test_paths=[red_path], cycle=1)
    ctx = make_ctx("SIMPLE")

    _mock_collect_failure(monkeypatch)
    _silence_telemetry(monkeypatch)

    result = _check_red_executable(ctx, prev)

    assert result.status == "error", (
        "GH348 AC1: expected status='error' on collect failure, "
        f"got {result.status!r}"
    )
    assert result.recoverable is True, (
        "GH348 AC1: SIMPLE cycle=1 collect failure must be recoverable=True "
        f"(cycle < cap), got {result.recoverable!r}"
    )
    assert result.error_code == "E_RED_COLLECT_FAILED", (
        f"GH348 AC1: expected error_code='E_RED_COLLECT_FAILED', got {result.error_code!r}"
    )
    assert isinstance(result.data, dict), (
        f"GH348 AC1: result.data must be a dict, got {type(result.data)!r}"
    )
    assert result.data.get("cycle_count") == 1, (
        "GH348 AC1 (forcing): expected data['cycle_count']==1 (single-owner raw-cycle "
        "threading, no -1 compensation), got "
        f"{result.data.get('cycle_count')!r}. Pre-GREEN: today's -1 compensation "
        "sends cycle=0 into gated_step_result, producing cycle_count=0."
    )
    assert result.data.get("retry_from_step") == 0, (
        f"GH348 AC1: expected data['retry_from_step']==0, got {result.data.get('retry_from_step')!r}"
    )


# ─── AC2: FEATURE collect-fail at cycle=1 → same contract as AC1 ─────────────


def test_ac2_feature_collect_fail_cycle1_yields_cycle_count_1(monkeypatch, tmp_path):
    """AC2: FEATURE collect-fail at cycle=1 -> same contract as AC1.

    Pre-GREEN FAIL: same -1 compensation defect, cycle_count==0 not 1.
    """
    red_path = str(tmp_path / "red_x.py")
    prev = make_prev(red_test_paths=[red_path], cycle=1)
    ctx = make_ctx("FEATURE")

    _mock_collect_failure(monkeypatch)
    _silence_telemetry(monkeypatch)

    result = _check_red_executable(ctx, prev)

    assert result.status == "error", (
        f"GH348 AC2: expected status='error' on collect failure, got {result.status!r}"
    )
    assert result.recoverable is True, (
        "GH348 AC2: FEATURE cycle=1 collect failure must be recoverable=True "
        f"(cycle < cap), got {result.recoverable!r}"
    )
    assert result.error_code == "E_RED_COLLECT_FAILED", (
        f"GH348 AC2: expected error_code='E_RED_COLLECT_FAILED', got {result.error_code!r}"
    )
    assert isinstance(result.data, dict), (
        f"GH348 AC2: result.data must be a dict, got {type(result.data)!r}"
    )
    assert result.data.get("cycle_count") == 1, (
        "GH348 AC2 (forcing): expected data['cycle_count']==1, got "
        f"{result.data.get('cycle_count')!r}. Pre-GREEN: today's -1 compensation "
        "sends cycle=0 into gated_step_result, producing cycle_count=0."
    )
    assert result.data.get("retry_from_step") == 0, (
        f"GH348 AC2: expected data['retry_from_step']==0, got {result.data.get('retry_from_step')!r}"
    )


# ─── AC3: SIMPLE collect-fail at cycle=2 → terminal (deliberate PASS pin) ────


def test_ac3_simple_collect_fail_cycle2_is_terminal(monkeypatch, tmp_path):
    """AC3: SIMPLE collect-fail at cycle=2 -> recoverable=False,
    error_code='E_RED_NOT_EXECUTABLE'.

    Deliberate pre-GREEN PASS pin: today's `-1` compensation (cycle=max(2-1,0)=1)
    against today's cap=1 policy caps identically (1>=1) to post-GREEN's raw
    cycle=2 against cap=2 policy (2>=2). This guards the frozen `>=` comparison
    in recoverable_gate.py:83 against an over-fix flip to `>` (R-BLOCKER #2) —
    this AC must stay green across the GREEN commit, not flip to red.
    """
    red_path = str(tmp_path / "red_x.py")
    # GH625 §2.5: cap decision now keyed on per-gate gate_attempts, not raw
    # cycle, and SIMPLE red_runtime cap is now 1 (recoverable_once). Seed 1
    # prior attempt (== cap) so this stays terminal (2>=1 would also cap, but
    # 1>=1 is the exact boundary).
    prev = make_prev(red_test_paths=[red_path], cycle=2, gate_attempts={"red_runtime": 1})
    ctx = make_ctx("SIMPLE")

    _mock_collect_failure(monkeypatch)
    _silence_telemetry(monkeypatch)

    result = _check_red_executable(ctx, prev)

    assert result.recoverable is False, (
        "GH348 AC3 (pin): SIMPLE cycle=2 collect failure must be recoverable=False "
        f"(cap reached both pre- and post-GREEN), got {result.recoverable!r}"
    )
    assert result.error_code == "E_RED_NOT_EXECUTABLE", (
        "GH348 AC3 (pin): cap must use terminal error_code='E_RED_NOT_EXECUTABLE', "
        f"got {result.error_code!r}"
    )


# ─── AC4a/b/c: resolve_policy red_runtime matrix rows ────────────────────────


def test_ac4a_resolve_policy_simple_red_runtime_recoverable_once(monkeypatch):
    """AC4a (GH625 §2.5 recalibration): resolve_policy("SIMPLE", "red_runtime")
    == RecoverablePolicy("recoverable_once", 1).

    GH625: matrix recalibrated back to recoverable_once/cap=1 for SIMPLE
    red_runtime under attempts-based accounting (supersedes the earlier
    GH348 recoverable_twice/2 pin).
    """
    _silence_telemetry(monkeypatch)
    result = resolve_policy("SIMPLE", "red_runtime")
    assert result == RecoverablePolicy("recoverable_once", 1), (
        "GH348/GH625 AC4a (forcing): expected RecoverablePolicy('recoverable_once', 1), "
        f"got {result!r}."
    )


def test_ac4b_resolve_policy_feature_red_runtime_recoverable_once(monkeypatch):
    """AC4b (GH625 §2.5 recalibration): resolve_policy("FEATURE", "red_runtime")
    == RecoverablePolicy("recoverable_once", 1).

    GH625: matrix recalibrated back to recoverable_once/cap=1 for FEATURE
    red_runtime under attempts-based accounting (supersedes the earlier
    GH348 recoverable_twice/2 pin).
    """
    _silence_telemetry(monkeypatch)
    result = resolve_policy("FEATURE", "red_runtime")
    assert result == RecoverablePolicy("recoverable_once", 1), (
        "GH348/GH625 AC4b (forcing): expected RecoverablePolicy('recoverable_once', 1), "
        f"got {result!r}."
    )


def test_ac4c_resolve_policy_complex_red_runtime_stays_terminal(monkeypatch):
    """AC4c: resolve_policy("COMPLEX", "red_runtime") ==
    RecoverablePolicy("terminal", 0) — UNCHANGED by this ship.

    Deliberate pre-GREEN PASS pin — the COMPLEX row is out of scope (§2 item 2:
    only SIMPLE/FEATURE rows flip).
    """
    _silence_telemetry(monkeypatch)
    result = resolve_policy("COMPLEX", "red_runtime")
    assert result == RecoverablePolicy("terminal", 0), (
        "GH348 AC4c (pin): COMPLEX/red_runtime must stay RecoverablePolicy('terminal', 0), "
        f"got {result!r}"
    )


# ─── AC5: telemetry payload cycle==1, cycle_cap==2, outcome=='retry' ─────────


def test_ac5_telemetry_payload_carries_raw_cycle_and_new_cap(monkeypatch, tmp_path):
    """AC5 (GH625 §2.5 recalibration): AC1 fixture (SIMPLE, cycle=1, collect
    failure) emits exactly one 'recoverable_gate_attempted' event with
    payload gate=='red_runtime', cycle==1, cycle_cap==1 (recoverable_once
    per §2.5), outcome=='retry', and 'attempts' key present (GH625 7-key
    payload).

    GH625: cycle_cap is now 1 (SIMPLE red_runtime recalibrated to
    recoverable_once), not the earlier GH348 recoverable_twice/2 pin.
    """
    red_path = str(tmp_path / "red_x.py")
    prev = make_prev(red_test_paths=[red_path], cycle=1)
    ctx = make_ctx("SIMPLE")

    _mock_collect_failure(monkeypatch)

    captured: list[tuple[str, dict]] = []

    def _fake_emit(name: str, payload: dict, *args: Any, **kwargs: Any) -> None:
        captured.append((name, payload))

    monkeypatch.setattr(_telemetry_ctx, "emit_safe", _fake_emit)

    result = _check_red_executable(ctx, prev)

    assert result.status == "error", (
        f"GH348 AC5 precondition: expected status='error', got {result.status!r}"
    )

    gate_events = [(n, p) for n, p in captured if n == "recoverable_gate_attempted"]
    assert len(gate_events) == 1, (
        "GH348 AC5: expected exactly one 'recoverable_gate_attempted' event, "
        f"got {len(gate_events)}. All captured: {[n for n, _ in captured]!r}"
    )
    payload = gate_events[0][1]
    assert payload.get("gate") == "red_runtime", (
        f"GH348 AC5: expected gate='red_runtime', got {payload.get('gate')!r}"
    )
    assert payload.get("cycle") == 1, (
        "GH348 AC5 (forcing): expected payload['cycle']==1 (raw cycle, no -1 "
        f"compensation), got {payload.get('cycle')!r}. Pre-GREEN: today's -1 "
        "compensation puts cycle=0 into the telemetry payload."
    )
    assert payload.get("cycle_cap") == 1, (
        "GH348/GH625 AC5 (forcing): expected payload['cycle_cap']==1 (§2.5 "
        f"recoverable_once recalibration), got {payload.get('cycle_cap')!r}."
    )
    assert payload.get("outcome") == "retry", (
        f"GH348 AC5: expected payload['outcome']=='retry', got {payload.get('outcome')!r}"
    )
    assert "attempts" in payload, (
        f"GH348/GH625 AC5: expected 'attempts' key in the 7-key GH625 payload, "
        f"got keys={sorted(payload.keys())!r}"
    )


# ─── AC6: engine + real gate retry terminates cleanly, exactly 2 executions ──
# GH625 §2.5 recalibration: cap is keyed on per-gate gate_attempts, not raw
# cycle, AND SIMPLE/FEATURE red_runtime is recoverable_once/cap=1 (not
# recoverable_twice/2). One real repair attempt before capping: call1
# attempts=0 -> retry, call2 attempts=1 -> cap. Two executions.


def test_ac6_engine_drives_real_gate_to_terminal_in_exactly_two_calls():
    """AC6 (ENG-1 kill, integration): WorkflowEngine().execute() (register +
    execute, mirroring test_pipeline_recovery.py:174-178 — NOT the private
    _execute_steps, whose retry branch touches self._rework_cycle_high which
    is only initialized inside execute()) over a 1-step workflow whose step
    is a counting wrapper around the REAL RecoverableGateMixin.gated_step_result(
    gate="red_runtime", build_class="SIMPLE", cycle=<read from prev "cycle",
    default 1>) terminates without ever needing to be caught; final
    error_code=='E_RED_NOT_EXECUTABLE', recoverable is False, and the step
    executed exactly 2 times (GH625 §2.5: per-gate gate_attempts, SIMPLE
    red_runtime cap=1, grants one real retry before capping on the second call).

    No RecursionError guard is used — whatever cycle-threading defect exists
    today is left to surface as a natural pytest failure/error; the assertions
    below are not wrapped in try/except.

    The stub's forwarded_data starts with an empty gate_attempts (natural
    unseeded drive) and accumulates it via gated_step_result's own retry
    branch across the engine's real retry recursion — no pre-seed needed.
    """
    counter = {"n": 0}

    def _stub_red_gate(_ctx: WorkflowContext, prev: Any) -> StepResult:
        # GH625: cap decision is now keyed on per-gate gate_attempts, carried
        # in forwarded_data (mirrors the real _check_red_executable call site,
        # phase_5_implement.py:4128, which passes forwarded_data=prev.data
        # verbatim) — so the stub must forward it across retries too, or
        # attempts would stay 0 forever and the cap would never be reached.
        if isinstance(prev, StepResult) and isinstance(prev.data, dict):
            cycle = int(prev.data.get("cycle", 1))
            prior_attempts = prev.data.get("gate_attempts") or {}
        elif isinstance(prev, dict):
            cycle = int(prev.get("cycle", 1))
            prior_attempts = prev.get("gate_attempts") or {}
        else:
            cycle = 1
            prior_attempts = {}
        counter["n"] += 1
        return RecoverableGateMixin.gated_step_result(
            build_class="SIMPLE",
            gate="red_runtime",
            cycle=cycle,
            retry_from_step_idx=0,
            error_code="E_RED_COLLECT_FAILED",
            error_msg="synthetic",
            step_name="stub_red_gate",
            forwarded_data={"gate_attempts": prior_attempts},
            terminal_error_code="E_RED_NOT_EXECUTABLE",
        )

    workflow = WorkflowDefinition(
        name="gh348_ac6",
        steps=[StepContract(name="stub_red_gate", execute=_stub_red_gate)],
    )
    ctx = WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={"complexity": "SIMPLE"},
        question="gh348 ac6 integration",
        session_id="test-gh348-ac6",
        persona="hal",
        framework=None,
        domain=None,
    )
    eng = WorkflowEngine()
    eng.register("gh348_ac6", workflow)

    result, _ = eng.execute("gh348_ac6", ctx, run_id="gh348")

    assert result.error_code == "E_RED_NOT_EXECUTABLE", (
        f"GH348 AC6: expected final error_code='E_RED_NOT_EXECUTABLE', got {result.error_code!r}"
    )
    assert result.recoverable is False, (
        f"GH348 AC6: expected final recoverable is False, got {result.recoverable!r}"
    )
    assert counter["n"] == 2, (
        "GH348/GH625 AC6 (forcing): expected the stub step to execute exactly 2 "
        f"times (one real retry then cap on the second call), got {counter['n']}. "
        "GH625 §2.5: cap is keyed on per-gate gate_attempts, and SIMPLE/red_runtime "
        "is recoverable_once/cap=1 — call1 attempts=0 retries, call2 attempts=1 caps."
    )


# ─── AC7: honest cap-semantics literal present in source ─────────────────────


def test_ac7_cycle_cap_comment_contains_retries_granted_literal():
    """AC7 (GH625 §2.5 supersession): _recoverable_policy.py source contains
    the current honest cap-semantics comment describing cycle_cap as the real
    retry count under attempts-based accounting.

    GH625 superseded the earlier GH348 literal "retries granted = cap - 1"
    (that framing assumed shared-cycle cap math); the shipped comment now
    reads "cycle_cap = real retries granted under GH625 attempts-based" —
    pinned here as a stable, distinctive substring.
    """
    policy_path = WORKFLOWS / "_recoverable_policy.py"
    source = policy_path.read_text(encoding="utf-8")

    assert "cycle_cap = real retries granted under GH625 attempts-based" in source, (
        "GH348/GH625 AC7 (forcing): expected the current honest cap-semantics "
        f"comment substring in {policy_path}."
    )
