"""RED tests for GH625 — split restart budgets (crash vs gate-retry) + per-gate
cycle accounting.

Spec: SHARED/memory/Decisions/2026-07-12_GH625_restart_budget_split_spec.md
(§3 AC1-AC16).

Governor ACs (AC1-AC7) exercise lib.restart_governor against real tmp_path
state files (§1l anchor on the JSON state file the production code writes).
Gate ACs (AC8-AC12) exercise lib.recoverable_gate.RecoverableGateMixin
directly. Engine ACs (AC13-AC16) drive a real WorkflowEngine over a minimal
fake 2-step workflow with a real event-log capture (sibling idiom:
tests/test_engine_retry_data_forwarding.py, tests/test_phase_5_retry_cycle_gh348.py).

No UUT mocking (stub-passability lint, §1l): governor_check/record_start/
record_result/record_pause/gate, gated_step_result, and the engine retry path
are all exercised directly against real state, never patched. Only
telemetry_ctx.emit_safe is monkeypatched for payload capture (sibling idiom
from test_E843349F_recoverable_policy.py).

Import pattern mirrors test_GH374_restart_governor.py: `lib.restart_governor`
symbols are imported INSIDE each test body (module-level shape is changing
under this ship — D1CF5FDF collectability guard applies to the *behavior*,
not existence, since the module already exists; this keeps the file robust
to any refactor GREEN makes to internal helpers).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

# ─── sys.path setup (mirrors test_phase_5_retry_cycle_gh348.py:64-73) ─────────
ENGINE_PY = Path(__file__).resolve().parents[1]
if str(ENGINE_PY) not in sys.path:
    sys.path.insert(0, str(ENGINE_PY))
WORKFLOWS = ENGINE_PY / "workflows"
if str(WORKFLOWS) not in sys.path:
    sys.path.insert(0, str(WORKFLOWS))
LIB = ENGINE_PY / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

import telemetry_ctx as _telemetry_ctx  # noqa: E402

from contracts import (  # noqa: E402
    StepContract,
    StepResult,
    WorkflowContext,
    WorkflowDefinition,
)
from engine import WorkflowEngine  # noqa: E402
from lib.recoverable_gate import RecoverableGateMixin  # noqa: E402


# ═══════════════════════════════════════════════════════════════════════════
# Helpers
# ═══════════════════════════════════════════════════════════════════════════


class _FakeEventLog:
    """Minimal duck-typed event log (sibling idiom, test_engine_retry_data_forwarding.py)."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict, str]] = []

    def append(self, event_type: str, payload: dict, run_id: str) -> None:
        self.events.append((event_type, payload, run_id))

    def of_type(self, event_type: str) -> list[dict]:
        return [p for et, p, _ in self.events if et == event_type]


def _make_engine_ctx(tmp_path: Path) -> WorkflowContext:
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={"git_cwd": str(tmp_path)},
        question="gh625",
        session_id="test-gh625",
        persona="hal",
        framework=None,
        domain=None,
    )


def _build_gate_budget_workflow(
    step0_retry_data: dict, captured_prev: list[Any]
) -> WorkflowDefinition:
    """Step 0 returns a recoverable retry (carrying step0_retry_data) on its
    first call; step 1 probe captures prev. Mirrors
    test_engine_retry_data_forwarding.py's _build_two_step_workflow."""
    call_counts = {"step0": 0}

    def step0_execute(ctx, prev):
        call_counts["step0"] += 1
        return StepResult(
            status="error",
            data=step0_retry_data,
            duration_ms=0,
            step_name="step0",
            error_code="E_GH625_GATE_RETRY",
            recoverable=True,
        )

    def step1_execute(ctx, prev):
        captured_prev.append(prev)
        return StepResult(status="ok", data={"probe": True}, duration_ms=0, step_name="step1")

    return WorkflowDefinition(
        name="test_gh625_gate_budget",
        steps=[
            StepContract(name="step0", execute=step0_execute),
            StepContract(name="step1_probe", execute=step1_execute),
        ],
    )


# ═══════════════════════════════════════════════════════════════════════════
# AC1-AC7 — lib.restart_governor split-counter contract
# ═══════════════════════════════════════════════════════════════════════════


def test_ac1_crash_loop_denies_and_state_shows_split_counters(tmp_path):
    """AC1: 3x governor_record_start with no results (crash loop):
    governor_check(cap=3) denies E_RESTART_CAP; state file shows
    crash_starts==2, pending==true (2 closed-as-crash + 1 open)."""
    from lib.restart_governor import governor_check, governor_record_start

    run_id, workflow = "gh625-ac1", "echo"
    for _ in range(3):
        governor_record_start(tmp_path, run_id, workflow)

    allow, deny_code = governor_check(tmp_path, run_id, workflow, cap=3)
    assert allow is False, "AC1 FAIL: 3 unclosed starts at cap=3 must deny"
    assert deny_code == "E_RESTART_CAP", f"AC1 FAIL: expected E_RESTART_CAP, got {deny_code!r}"

    state_file = tmp_path / f"restart-governor-{run_id}-{workflow}.json"
    data = json.loads(state_file.read_text(encoding="utf-8"))
    assert data.get("crash_starts") == 2, (
        f"AC1 FAIL: expected crash_starts==2 (schema v2 split counter), got {data!r}. "
        "Today's state file has no 'crash_starts' key at all (legacy 'starts' shape)."
    )
    assert data.get("pending") is True, (
        f"AC1 FAIL: expected pending==True (last start still open), got {data!r}"
    )


def test_ac2_gate_retries_dont_touch_crash_budget(tmp_path):
    """AC2: 3x (record_start + record_result(distinct code)) alternating:
    with gate_cap=3 check denies; with gate_cap=10, cap=3 check ALLOWS
    (crash budget untouched: crash_starts==0)."""
    from lib.restart_governor import governor_check, governor_record_result, governor_record_start

    run_id, workflow = "gh625-ac2", "echo"
    for i in range(3):
        governor_record_start(tmp_path, run_id, workflow)
        governor_record_result(tmp_path, run_id, workflow, error_code=f"E_GATE_{i}")

    allow_deny, deny_code = governor_check(tmp_path, run_id, workflow, cap=3, gate_cap=3)
    assert allow_deny is False, "AC2 FAIL: gate_cap=3 with 3 gate starts must deny"
    assert deny_code == "E_RESTART_CAP", f"AC2 FAIL: expected E_RESTART_CAP, got {deny_code!r}"

    allow_ok, deny_ok = governor_check(tmp_path, run_id, workflow, cap=3, gate_cap=10)
    assert allow_ok is True, (
        f"AC2 FAIL: gate_cap=10 must allow (only 3 gate starts spent); got deny_code={deny_ok!r}. "
        "Today's governor_check has no gate_cap kwarg at all (TypeError expected pre-GREEN)."
    )

    state_file = tmp_path / f"restart-governor-{run_id}-{workflow}.json"
    data = json.loads(state_file.read_text(encoding="utf-8"))
    assert data.get("crash_starts") == 0, (
        f"AC2 FAIL: crash budget must be untouched by gate retries; got crash_starts="
        f"{data.get('crash_starts')!r} in {data!r}"
    )


def test_ac3_interleaved_crash_and_gate_starts_independent_budgets(tmp_path):
    """AC3: 2 crashes then 1 gated result: state crash_starts==2, gate_starts==1,
    pending==false; cap=3, gate_cap=3 allows (crash_eff 2<3, gate 1<3)."""
    from lib.restart_governor import governor_check, governor_record_result, governor_record_start

    run_id, workflow = "gh625-ac3", "echo"
    governor_record_start(tmp_path, run_id, workflow)  # open #1
    governor_record_start(tmp_path, run_id, workflow)  # closes #1 as crash -> crash_starts=1, open #2
    governor_record_result(tmp_path, run_id, workflow, error_code="E_GATED")  # closes #2 as gate

    state_file = tmp_path / f"restart-governor-{run_id}-{workflow}.json"
    data = json.loads(state_file.read_text(encoding="utf-8"))
    assert data.get("crash_starts") == 1, (
        f"AC3 FAIL: expected crash_starts==1 after 2 opens (1 closed-as-crash), got {data!r}"
    )
    assert data.get("gate_starts") == 1, (
        f"AC3 FAIL: expected gate_starts==1 after the gated result, got {data!r}"
    )
    assert data.get("pending") is False, (
        f"AC3 FAIL: expected pending==False after a gated close, got {data!r}"
    )

    allow, deny_code = governor_check(tmp_path, run_id, workflow, cap=3, gate_cap=3)
    assert allow is True, f"AC3 FAIL: expected allow, got deny_code={deny_code!r}"


def test_ac4_legacy_state_migrates_to_schema_v2(tmp_path):
    """AC4: legacy state file {"starts": 3, "error_codes": []} -> check(cap=3)
    denies E_RESTART_CAP (migrates to crash_starts=3); after
    record_result("E_Y") file is schema-2 with gate_starts==1."""
    from lib.restart_governor import governor_check, governor_record_result

    run_id, workflow = "gh625-ac4", "echo"
    state_file = tmp_path / f"restart-governor-{run_id}-{workflow}.json"
    state_file.write_text(json.dumps({"starts": 3, "error_codes": []}), encoding="utf-8")

    allow, deny_code = governor_check(tmp_path, run_id, workflow, cap=3)
    assert allow is False, "AC4 FAIL: migrated legacy starts=3 must deny at cap=3"
    assert deny_code == "E_RESTART_CAP", f"AC4 FAIL: expected E_RESTART_CAP, got {deny_code!r}"

    governor_record_result(tmp_path, run_id, workflow, error_code="E_Y")
    data = json.loads(state_file.read_text(encoding="utf-8"))
    assert data.get("schema") == 2, f"AC4 FAIL: expected schema==2 after migration write, got {data!r}"
    assert data.get("gate_starts") == 1, (
        f"AC4 FAIL: expected gate_starts==1 after record_result, got {data!r}"
    )


def test_ac5_pause_refunds_without_decrementing_split_counters(tmp_path):
    """AC5: record_start then governor_record_pause: pending==false,
    crash_starts==0, gate_starts==0; subsequent check allows (GH576 refund
    preserved)."""
    from lib.restart_governor import governor_check, governor_record_pause, governor_record_start

    run_id, workflow = "gh625-ac5", "echo"
    governor_record_start(tmp_path, run_id, workflow)
    governor_record_pause(tmp_path, run_id, workflow)

    state_file = tmp_path / f"restart-governor-{run_id}-{workflow}.json"
    data = json.loads(state_file.read_text(encoding="utf-8"))
    assert data.get("pending") is False, f"AC5 FAIL: expected pending==False after pause, got {data!r}"
    assert data.get("crash_starts", 0) == 0, f"AC5 FAIL: expected crash_starts==0, got {data!r}"
    assert data.get("gate_starts", 0) == 0, f"AC5 FAIL: expected gate_starts==0, got {data!r}"

    allow, deny_code = governor_check(tmp_path, run_id, workflow, cap=3)
    assert allow is True, f"AC5 FAIL: expected allow after pause-refund, got deny_code={deny_code!r}"


def test_ac6_success_result_unlinks_state_file(tmp_path):
    """AC6: governor_record_result(None) unlinks state file (success reset
    unchanged, cite restart_governor.py:84-90)."""
    from lib.restart_governor import governor_record_result, governor_record_start

    run_id, workflow = "gh625-ac6", "echo"
    governor_record_start(tmp_path, run_id, workflow)
    state_file = tmp_path / f"restart-governor-{run_id}-{workflow}.json"
    assert state_file.exists()

    governor_record_result(tmp_path, run_id, workflow, error_code=None)
    assert not state_file.exists(), "AC6 FAIL: success result must unlink the state file"


def test_ac7_governor_gate_denied_payload_has_split_keys(tmp_path):
    """AC7: governor_gate with crash_cap=1, gate_retry_cap=5 and a pre-seeded
    pending state denies with a StepResult-shaped dict, error_code==
    'E_RESTART_CAP'; emitted restart_governor_denied event payload contains
    deny_class=='crash', crash_starts, gate_starts, and legacy starts."""
    from lib.restart_governor import governor_gate

    run_id, workflow = "gh625-ac7", "echo"
    state_file = tmp_path / f"restart-governor-{run_id}-{workflow}.json"
    # Pre-seed pending schema-v2 state (a crashed prior start).
    state_file.write_text(
        json.dumps(
            {"schema": 2, "crash_starts": 0, "gate_starts": 0, "pending": True, "error_codes": []}
        ),
        encoding="utf-8",
    )
    event_log_path = str(tmp_path / "events.jsonl")

    org_config = {"restart_governor": {"crash_cap": 1, "gate_retry_cap": 5}}
    result = governor_gate(event_log_path, run_id, workflow, org_config)

    assert isinstance(result, dict), f"AC7 FAIL: expected deny dict, got {result!r}"
    assert result.get("error_code") == "E_RESTART_CAP", (
        f"AC7 FAIL: expected error_code=='E_RESTART_CAP', got {result!r}"
    )

    events_text = Path(event_log_path).read_text(encoding="utf-8")
    assert "restart_governor_denied" in events_text, "AC7 FAIL: expected deny event to be emitted"
    denied_line = next(
        line for line in events_text.strip().splitlines() if "restart_governor_denied" in line
    )
    payload = json.loads(denied_line).get("payload", json.loads(denied_line))
    assert payload.get("deny_class") == "crash", (
        f"AC7 FAIL: expected deny_class=='crash', got payload={payload!r}. "
        "Today's payload has no 'deny_class' key at all."
    )
    assert "crash_starts" in payload, f"AC7 FAIL: expected 'crash_starts' key, got {payload!r}"
    assert "gate_starts" in payload, f"AC7 FAIL: expected 'gate_starts' key, got {payload!r}"
    assert "starts" in payload, f"AC7 FAIL: expected legacy 'starts' key preserved, got {payload!r}"


# ═══════════════════════════════════════════════════════════════════════════
# AC8-AC12 — lib.recoverable_gate per-gate attempt accounting
# ═══════════════════════════════════════════════════════════════════════════


def test_ac8_fresh_gate_attempts_grants_retry_even_at_inherited_cycle2(monkeypatch):
    """AC8 (#625 datapoint 2 regression): gated_step_result(gate='green_typecheck',
    build_class='FEATURE', cycle=2, forwarded_data={}) -> outcome retry:
    status='error', recoverable is True, data['gate_attempts']['green_typecheck']==1,
    data['gate_budget_ok'] is True."""
    captured: list[tuple[str, dict]] = []

    def _fake_emit(name: str, payload: dict, *args: Any, **kwargs: Any) -> None:
        captured.append((name, payload))

    monkeypatch.setattr(_telemetry_ctx, "emit_safe", _fake_emit)

    result = RecoverableGateMixin.gated_step_result(
        build_class="FEATURE",
        gate="green_typecheck",
        cycle=2,
        retry_from_step_idx=1,
        error_code="E_GREEN_TYPECHECK_RETRY",
        error_msg="typecheck failed",
        step_name="verify_green_typecheck",
        forwarded_data={},
        terminal_error_code="E_GREEN_TYPECHECK_FAIL_CAP2",
    )
    assert result.status == "error", f"AC8 FAIL: expected status='error', got {result.status!r}"
    assert result.recoverable is True, (
        f"AC8 FAIL: expected recoverable=True (fresh gate_attempts, not cycle-capped); "
        f"got {result.recoverable!r}. Today's cap check uses `cycle >= pol.cycle_cap` "
        "(2>=1 for FEATURE) which caps immediately, ignoring gate_attempts."
    )
    assert isinstance(result.data, dict), "AC8 FAIL: result.data must be a dict"
    ga = result.data.get("gate_attempts")
    assert isinstance(ga, dict) and ga.get("green_typecheck") == 1, (
        f"AC8 FAIL: expected data['gate_attempts']['green_typecheck']==1, got {ga!r}"
    )
    assert result.data.get("gate_budget_ok") is True, (
        f"AC8 FAIL: expected data['gate_budget_ok'] is True, got {result.data.get('gate_budget_ok')!r}"
    )


def test_ac9_spent_gate_attempts_caps_regardless_of_cycle(monkeypatch):
    """AC9: same call with forwarded_data={'gate_attempts': {'green_typecheck': 1}}
    -> outcome=cap: recoverable is False, error_code == terminal code, no
    retry_from_step in data."""
    captured: list[tuple[str, dict]] = []

    def _fake_emit(name: str, payload: dict, *args: Any, **kwargs: Any) -> None:
        captured.append((name, payload))

    monkeypatch.setattr(_telemetry_ctx, "emit_safe", _fake_emit)

    result = RecoverableGateMixin.gated_step_result(
        build_class="FEATURE",
        gate="green_typecheck",
        cycle=2,
        retry_from_step_idx=1,
        error_code="E_GREEN_TYPECHECK_RETRY",
        error_msg="typecheck failed",
        step_name="verify_green_typecheck",
        forwarded_data={"gate_attempts": {"green_typecheck": 1}},
        terminal_error_code="E_GREEN_TYPECHECK_FAIL_CAP2",
    )
    assert result.recoverable is False, (
        f"AC9 FAIL: expected recoverable=False (1 attempt already spent, cap=1 FEATURE); "
        f"got {result.recoverable!r}"
    )
    assert result.error_code == "E_GREEN_TYPECHECK_FAIL_CAP2", (
        f"AC9 FAIL: expected terminal error_code, got {result.error_code!r}"
    )
    assert isinstance(result.data, dict)
    assert "retry_from_step" not in result.data, (
        f"AC9 FAIL: cap outcome must not carry retry_from_step, got {result.data!r}"
    )


def test_ac10_attempts_are_per_gate_not_shared(monkeypatch):
    """AC10: forwarded_data={'gate_attempts': {'green_lint': 5}}, gate
    'green_typecheck' -> still retry (other gate's spend does not starve)."""
    captured: list[tuple[str, dict]] = []

    def _fake_emit(name: str, payload: dict, *args: Any, **kwargs: Any) -> None:
        captured.append((name, payload))

    monkeypatch.setattr(_telemetry_ctx, "emit_safe", _fake_emit)

    result = RecoverableGateMixin.gated_step_result(
        build_class="FEATURE",
        gate="green_typecheck",
        cycle=2,
        retry_from_step_idx=1,
        error_code="E_GREEN_TYPECHECK_RETRY",
        error_msg="typecheck failed",
        step_name="verify_green_typecheck",
        forwarded_data={"gate_attempts": {"green_lint": 5}},
        terminal_error_code="E_GREEN_TYPECHECK_FAIL_CAP2",
    )
    assert result.recoverable is True, (
        f"AC10 FAIL: expected recoverable=True (green_lint's spend must not starve "
        f"green_typecheck's fresh budget); got {result.recoverable!r}"
    )


def test_ac11_telemetry_payload_has_exactly_seven_keys_incl_attempts(monkeypatch):
    """AC11: recoverable_gate_attempted telemetry payload has exactly 7 keys
    incl. 'attempts' (supersedes E843349F 6-key freeze)."""
    captured: list[tuple[str, dict]] = []

    def _fake_emit(name: str, payload: dict, *args: Any, **kwargs: Any) -> None:
        captured.append((name, payload))

    monkeypatch.setattr(_telemetry_ctx, "emit_safe", _fake_emit)

    RecoverableGateMixin.gated_step_result(
        build_class="SIMPLE",
        gate="green_typecheck",
        cycle=1,
        retry_from_step_idx=1,
        error_code="E_GREEN_TYPECHECK_RETRY",
        error_msg="typecheck failed",
        step_name="verify_green_typecheck",
        forwarded_data={},
    )
    events = [(n, p) for n, p in captured if n == "recoverable_gate_attempted"]
    assert len(events) == 1, f"AC11 FAIL: expected 1 event, got {len(events)}"
    payload = events[0][1]
    assert "attempts" in payload, f"AC11 FAIL: expected 'attempts' key, got {set(payload.keys())!r}"
    assert len(payload) == 7, (
        f"AC11 FAIL: expected exactly 7 keys (6 existing + attempts), got {len(payload)}: "
        f"{sorted(payload.keys())!r}"
    )


def test_ac17_spec_retry_revise_cap_pin(monkeypatch):
    """AC17 (Opus cycle 3 pin, spec §2.3a/§2.5): phase_45 spec_retry revise
    gate still terminates at its policy cap under attempts-based semantics.
    GH625 §2.5 recalibration: spec_retry is now recoverable_once/cap=1 for
    all build classes.

    At attempts=0 (no seed / fresh gate), the retry branch grants a retry
    and records gate_attempts["spec_retry"]==1 in data. At attempts=1
    (cap reached), outcome=cap: status='error', recoverable False, terminal
    error_code, no retry_from_step in data."""
    captured: list[tuple[str, dict]] = []

    def _fake_emit(name: str, payload: dict, *args: Any, **kwargs: Any) -> None:
        captured.append((name, payload))

    monkeypatch.setattr(_telemetry_ctx, "emit_safe", _fake_emit)

    # attempts=0 (no seed) -> grants retry, records gate_attempts["spec_retry"]==1
    retry_result = RecoverableGateMixin.gated_step_result(
        build_class="SIMPLE",
        gate="spec_retry",
        cycle=1,
        retry_from_step_idx=0,
        error_code="E_SPEC_RETRY",
        error_msg="spec needs revision",
        step_name="revise_spec",
        forwarded_data={},
        terminal_error_code="E_SPEC_RETRY_FAIL_CAP2",
    )
    assert retry_result.recoverable is True, (
        f"AC17 FAIL: expected recoverable=True at attempts=0 (cap=1), "
        f"got {retry_result.recoverable!r}"
    )
    assert isinstance(retry_result.data, dict)
    assert retry_result.data.get("gate_attempts", {}).get("spec_retry") == 1, (
        f"AC17 FAIL: expected gate_attempts['spec_retry']==1 after retry, "
        f"got {retry_result.data.get('gate_attempts')!r}"
    )

    # attempts=1 (seeded, == cap) -> cap reached: terminal, no retry_from_step
    cap_result = RecoverableGateMixin.gated_step_result(
        build_class="SIMPLE",
        gate="spec_retry",
        cycle=2,
        retry_from_step_idx=0,
        error_code="E_SPEC_RETRY",
        error_msg="spec needs revision",
        step_name="revise_spec",
        forwarded_data={"gate_attempts": {"spec_retry": 1}},
        terminal_error_code="E_SPEC_RETRY_FAIL_CAP2",
    )
    assert cap_result.status == "error", f"AC17 FAIL: expected status='error', got {cap_result.status!r}"
    assert cap_result.recoverable is False, (
        f"AC17 FAIL: expected recoverable=False at cap (attempts=1>=1), got {cap_result.recoverable!r}"
    )
    assert cap_result.error_code == "E_SPEC_RETRY_FAIL_CAP2", (
        f"AC17 FAIL: expected terminal error_code, got {cap_result.error_code!r}"
    )
    assert isinstance(cap_result.data, dict)
    assert "retry_from_step" not in cap_result.data, (
        f"AC17 FAIL: cap outcome must not carry retry_from_step, got {cap_result.data!r}"
    )


def test_ac12_terminal_slot_unaffected_by_attempts(monkeypatch):
    """AC12: terminal slot (('FEATURE','integrity')) unaffected: still
    recoverable False regardless of attempts."""
    captured: list[tuple[str, dict]] = []

    def _fake_emit(name: str, payload: dict, *args: Any, **kwargs: Any) -> None:
        captured.append((name, payload))

    monkeypatch.setattr(_telemetry_ctx, "emit_safe", _fake_emit)

    result = RecoverableGateMixin.gated_step_result(
        build_class="FEATURE",
        gate="integrity",
        cycle=1,
        retry_from_step_idx=0,
        error_code="E_INTEGRITY_ASSERTION_GAMING",
        error_msg="integrity violated",
        step_name="integrity_check",
        forwarded_data={"gate_attempts": {}},
    )
    assert result.recoverable is False, (
        f"AC12 FAIL: terminal slot must stay recoverable=False, got {result.recoverable!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# AC13-AC16 — engine.py retry allowance + hard backstop
# ═══════════════════════════════════════════════════════════════════════════


def test_ac13_engine_retries_past_max_validation_cycles_when_gate_budget_ok(tmp_path):
    """AC13: 2-step workflow where step B returns recoverable=True,
    data={'retry_from_step': 0, 'cycle_count': 2, 'gate_budget_ok': True,
    'gate_attempts': {...}} -> engine RETRIES (emits phase_retry_triggered
    with cycle 3) despite cycle_count >= _MAX_VALIDATION_CYCLES."""
    captured: list[Any] = []
    retry_data = {
        "retry_from_step": 1,
        "cycle_count": 2,
        "gate_budget_ok": True,
        "gate_attempts": {"green_typecheck": 1},
    }
    workflow = _build_gate_budget_workflow(retry_data, captured)

    event_log = _FakeEventLog()
    engine = WorkflowEngine(event_log=event_log)
    engine.register("test_gh625_gate_budget", workflow)

    ctx = _make_engine_ctx(tmp_path)
    result, _ = engine.execute("test_gh625_gate_budget", ctx)

    retry_events = event_log.of_type("phase_retry_triggered")
    assert len(retry_events) == 1, (
        f"AC13 FAIL: expected engine to retry (1 phase_retry_triggered event) despite "
        f"cycle_count=2 >= _MAX_VALIDATION_CYCLES=2; got {len(retry_events)}. "
        f"Today's engine.py:431 `if cycle_count < self._MAX_VALIDATION_CYCLES:` has no "
        "gate_budget_ok escape hatch, so it stops at the cap. Final result: "
        f"status={result.status!r} error_code={result.error_code!r}"
    )
    assert retry_events[0].get("cycle") == 3, (
        f"AC13 FAIL: expected retry cycle==3, got {retry_events[0]!r}"
    )
    assert result.status == "ok", f"AC13 FAIL: expected retry to reach step1 (ok), got {result.status!r}"


def test_ac14_hard_backstop_denies_retry_at_cycle_six(tmp_path):
    """AC14: same but cycle_count=6 (>= _GATE_BUDGET_HARD_BACKSTOP) -> NO
    retry, terminal error returned (backstop)."""
    captured: list[Any] = []
    retry_data = {
        "retry_from_step": 1,
        "cycle_count": 6,
        "gate_budget_ok": True,
        "gate_attempts": {"green_typecheck": 5},
    }
    workflow = _build_gate_budget_workflow(retry_data, captured)

    event_log = _FakeEventLog()
    engine = WorkflowEngine(event_log=event_log)
    engine.register("test_gh625_gate_budget", workflow)

    ctx = _make_engine_ctx(tmp_path)
    result, _ = engine.execute("test_gh625_gate_budget", ctx)

    retry_events = event_log.of_type("phase_retry_triggered")
    assert len(retry_events) == 0, (
        f"AC14 FAIL: expected the hard backstop (_GATE_BUDGET_HARD_BACKSTOP=6) to deny "
        f"retry at cycle_count=6; got {len(retry_events)} phase_retry_triggered events. "
        "Today there is no hard-backstop concept at all — but today's plain cap check "
        "(6 < 2) also denies, so this AC currently PASSES as a coincidental pin; the "
        "forcing function is AC13 (retry must succeed at cycle_count=2)."
    )
    assert result.status == "error", f"AC14 FAIL: expected terminal error, got {result.status!r}"
    assert result.recoverable is True, (
        "AC14 sanity: the returned StepResult itself is still the recoverable=True one "
        "from the producer (engine does not mutate it); the ENGINE simply refuses to "
        f"drive another cycle. got recoverable={result.recoverable!r}"
    )


def test_ac15_gate_budget_ok_absent_preserves_pre_gh625_behavior(tmp_path):
    """AC15: same but gate_budget_ok absent and cycle_count=2 -> NO retry
    (pre-GH625 behavior preserved for non-gate producers)."""
    captured: list[Any] = []
    retry_data = {
        "retry_from_step": 1,
        "cycle_count": 2,
    }
    workflow = _build_gate_budget_workflow(retry_data, captured)

    event_log = _FakeEventLog()
    engine = WorkflowEngine(event_log=event_log)
    engine.register("test_gh625_gate_budget", workflow)

    ctx = _make_engine_ctx(tmp_path)
    result, _ = engine.execute("test_gh625_gate_budget", ctx)

    retry_events = event_log.of_type("phase_retry_triggered")
    assert len(retry_events) == 0, (
        f"AC15 FAIL: expected no retry when gate_budget_ok is absent (pre-GH625 "
        f"non-gate producer path unchanged); got {len(retry_events)} events."
    )
    assert result.status == "error", f"AC15 FAIL: expected terminal error, got {result.status!r}"


def test_ac16_gate_budget_ok_consumed_not_forwarded_gate_attempts_is(tmp_path):
    """AC16: gate_budget_ok is consumed: in the AC13 retry, the
    retry_data_forwarded event's forwarded_keys does NOT contain
    gate_budget_ok but DOES contain gate_attempts."""
    captured: list[Any] = []
    retry_data = {
        "retry_from_step": 1,
        "cycle_count": 2,
        "gate_budget_ok": True,
        "gate_attempts": {"green_typecheck": 1},
    }
    workflow = _build_gate_budget_workflow(retry_data, captured)

    event_log = _FakeEventLog()
    engine = WorkflowEngine(event_log=event_log)
    engine.register("test_gh625_gate_budget", workflow)

    ctx = _make_engine_ctx(tmp_path)
    engine.execute("test_gh625_gate_budget", ctx)

    forwarded_events = event_log.of_type("retry_data_forwarded")
    assert len(forwarded_events) == 1, (
        f"AC16 precondition FAIL: expected the retry to happen at all (1 "
        f"retry_data_forwarded event), got {len(forwarded_events)}. This AC depends on "
        "AC13's forcing fix landing first."
    )
    fk = forwarded_events[0].get("forwarded_keys", [])
    assert "gate_budget_ok" not in fk, (
        f"AC16 FAIL: expected 'gate_budget_ok' to be consumed (added to "
        f"_RETRY_CONTROL_KEYS), not forwarded; got forwarded_keys={fk!r}"
    )
    assert "gate_attempts" in fk, (
        f"AC16 FAIL: expected 'gate_attempts' to be forwarded across the retry boundary; "
        f"got forwarded_keys={fk!r}"
    )
