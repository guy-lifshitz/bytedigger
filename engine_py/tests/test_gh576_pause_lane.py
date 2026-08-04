"""RED for GH576 — PAUSED/resumable lane for E_LLM_SPEND_LIMIT.

Agreement: C474E073-40E5-4557-B990-66D358BDE842

`lib.env_limit.is_paused_result` and `lib.restart_governor.governor_record_pause`
do not exist yet — every import of them is deferred to inside the test body
(workflows.md §1q extension) so this file COLLECTS cleanly and FAILS at assert
time (an ImportError raised inside a test body is a test FAILURE, not a
collection error).

P2-P4 drive a real `WorkflowEngine` instance (unmocked UUT, §1l) with a
one-step workflow whose step returns the spend-limit `StepResult`; a fake
event log captures emits (pattern: test_engine.py `_FakeEventLog` /
`test_engine_emits_workflow_started_and_finished_when_log_attached`).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from bytedigger_engine.contracts import (  # noqa: E402
    WorkflowContext,
    StepResult,
    StepContract,
    WorkflowDefinition,
)
from bytedigger_engine.engine import WorkflowEngine  # noqa: E402


def make_ctx() -> WorkflowContext:
    return WorkflowContext(
        tenant_id="t",
        scope=None,
        db_path=None,
        org_config=None,
        question="q",
        session_id="s",
        persona="p",
        framework=None,
        domain=None,
    )


class _FakeEventLog:
    def __init__(self):
        self.events: list[tuple[str, dict, str]] = []

    def append(self, event_type: str, payload: dict, run_id: str | None = None) -> None:
        self.events.append((event_type, payload, run_id))


def _spend_limit_step(name: str = "call_llm"):
    def _run(_ctx, _prev):
        return StepResult(
            status="error",
            data={"pausable": True},
            duration_ms=0,
            step_name=name,
            error="spend limit hit",
            error_code="E_LLM_SPEND_LIMIT",
            recoverable=True,
        )
    return StepContract(name=name, execute=_run)


def _other_error_step(name: str = "call_llm", error_code: str = "E_LLM_EXIT"):
    def _run(_ctx, _prev):
        return StepResult(
            status="error",
            data=None,
            duration_ms=0,
            step_name=name,
            error="boom",
            error_code=error_code,
            recoverable=False,
        )
    return StepContract(name=name, execute=_run)


# ---------------------------------------------------------------------------
# P1 — lib.env_limit.is_paused_result predicate
# ---------------------------------------------------------------------------

def test_p1_is_paused_result_predicate():
    from bytedigger_engine.lib.env_limit import is_paused_result, PAUSE_LANE_ERROR_CODES

    assert is_paused_result("error", "E_LLM_SPEND_LIMIT") is True
    assert is_paused_result("error", "E_LLM_EXIT") is False
    assert is_paused_result("ok", "E_LLM_SPEND_LIMIT") is False
    assert is_paused_result("escalate", "E_LLM_SPEND_LIMIT") is False
    assert PAUSE_LANE_ERROR_CODES == frozenset({"E_LLM_SPEND_LIMIT"})


# ---------------------------------------------------------------------------
# P2 — engine emits workflow_finished status=="paused" for spend-limit result
# ---------------------------------------------------------------------------

def test_p2_engine_emits_paused_status_for_spend_limit():
    log = _FakeEventLog()
    eng = WorkflowEngine(event_log=log)
    eng.register("solo", WorkflowDefinition(name="solo", steps=[_spend_limit_step()]))
    result, _ = eng.execute("solo", make_ctx(), run_id="rid-p2")

    wf_finished = [e for e in log.events if e[0] == "workflow_finished"][0]
    payload = wf_finished[1]
    assert payload["status"] == "paused", (
        f"P2 FAIL: expected workflow_finished payload status=='paused', got {payload!r}"
    )
    assert set(payload.keys()) == {"workflow_name", "status", "wall_ms"}, (
        f"P2 FAIL: payload keys must stay exactly workflow_name/status/wall_ms, got {payload!r}"
    )
    assert result.status == "error", (
        f"P2 FAIL: returned StepResult.status must stay 'error' (unchanged), got {result.status!r}"
    )


# ---------------------------------------------------------------------------
# P3 — HAL_PAUSE_LANE=0 kill-switch: legacy status=="error"
# ---------------------------------------------------------------------------

def test_p3_kill_switch_disables_paused_status(monkeypatch):
    monkeypatch.setenv("HAL_PAUSE_LANE", "0")
    log = _FakeEventLog()
    eng = WorkflowEngine(event_log=log)
    eng.register("solo", WorkflowDefinition(name="solo", steps=[_spend_limit_step()]))
    eng.execute("solo", make_ctx(), run_id="rid-p3")

    wf_finished = [e for e in log.events if e[0] == "workflow_finished"][0]
    assert wf_finished[1]["status"] == "error", (
        f"P3 FAIL: HAL_PAUSE_LANE=0 must keep legacy status=='error', got {wf_finished[1]!r}"
    )


# ---------------------------------------------------------------------------
# P4 — regression pin: E_LLM_EXIT must NOT be treated as pausable
# ---------------------------------------------------------------------------

def test_p4_non_spend_limit_error_code_stays_error():
    log = _FakeEventLog()
    eng = WorkflowEngine(event_log=log)
    eng.register("solo", WorkflowDefinition(name="solo", steps=[_other_error_step()]))
    eng.execute("solo", make_ctx(), run_id="rid-p4")

    wf_finished = [e for e in log.events if e[0] == "workflow_finished"][0]
    assert wf_finished[1]["status"] == "error", (
        f"P4 FAIL: E_LLM_EXIT must not be over-widened into 'paused', got {wf_finished[1]!r}"
    )


# ---------------------------------------------------------------------------
# P5 — governor_record_pause refunds a start, never appends error_codes,
# floors at 0 (self-heals on absent state)
# ---------------------------------------------------------------------------

def test_p5_governor_record_pause_refunds_start_and_floors_at_zero(tmp_path):
    """GH625 §2.1: schema v2 split counters — a single open `record_start`
    is tracked via `pending`, not a flat `starts` counter (legacy shape).
    `governor_record_pause` clears `pending` only (no counter decrement):
    an unclosed-then-paused start was never counted toward either budget,
    so clearing `pending` IS the refund. A subsequent `governor_check`
    must still ALLOW (GH576 exemption semantics preserved)."""
    from bytedigger_engine.lib.restart_governor import (
        governor_check,
        governor_record_pause,
        governor_record_start,
    )
    import json

    run_id, workflow = "run-p5", "echo"
    governor_record_start(tmp_path, run_id, workflow)
    state_file = tmp_path / f"restart-governor-{run_id}-{workflow}.json"
    assert json.loads(state_file.read_text(encoding="utf-8"))["pending"] is True

    governor_record_pause(tmp_path, run_id, workflow)
    data = json.loads(state_file.read_text(encoding="utf-8"))
    assert data["pending"] is False, f"P5 FAIL: expected pending==False after pause, got {data!r}"
    assert data.get("crash_starts", 0) == 0, f"P5 FAIL: expected crash_starts==0, got {data!r}"
    assert data.get("gate_starts", 0) == 0, f"P5 FAIL: expected gate_starts==0, got {data!r}"
    assert data["error_codes"] == [], f"P5 FAIL: expected error_codes==[] (unappended), got {data!r}"

    allow, deny_code = governor_check(tmp_path, run_id, workflow)
    assert allow is True, f"P5 FAIL: expected allow after pause-refund, got deny_code={deny_code!r}"

    # Calling again (already pending==False) must stay floored, never negative
    governor_record_pause(tmp_path, run_id, workflow)
    data2 = json.loads(state_file.read_text(encoding="utf-8"))
    assert data2["pending"] is False, f"P5 FAIL: repeated pause must stay pending==False, got {data2!r}"
    assert data2.get("crash_starts", 0) == 0, f"P5 FAIL: repeated pause must floor at 0, got {data2!r}"

    # Absent state file must self-heal, not raise
    run_id2 = "run-p5-absent"
    governor_record_pause(tmp_path, run_id2, workflow)
    state_file2 = tmp_path / f"restart-governor-{run_id2}-{workflow}.json"
    data3 = json.loads(state_file2.read_text(encoding="utf-8"))
    assert data3["pending"] is False, (
        f"P5 FAIL: absent-state pause must self-heal to pending==False, got {data3!r}"
    )
    assert data3.get("crash_starts", 0) == 0, (
        f"P5 FAIL: absent-state pause must self-heal to crash_starts==0, got {data3!r}"
    )


# ---------------------------------------------------------------------------
# P6 — run.py source wires governor_record_pause + is_paused_result, and
# still retains the legacy governor_record_result call site
# ---------------------------------------------------------------------------

def test_p6_run_py_wires_pause_lane_and_keeps_legacy_call():
    run_py_path = Path(__file__).parent.parent / "bytedigger_engine" / "run.py"
    source = run_py_path.read_text(encoding="utf-8")

    assert "governor_record_pause(" in source, (
        "P6 FAIL: run.py must call governor_record_pause("
    )
    assert "is_paused_result(" in source, (
        "P6 FAIL: run.py must call is_paused_result("
    )
    assert "governor_record_result(" in source, (
        "P6 FAIL: run.py must still call governor_record_result( (legacy lane retained)"
    )


# ---------------------------------------------------------------------------
# P7 — flags_catalog.FLAGS declares HAL_PAUSE_LANE gate, default ON
# ---------------------------------------------------------------------------

def test_p7_flags_catalog_declares_hal_pause_lane():
    from bytedigger_engine import flags_catalog

    assert "HAL_PAUSE_LANE" in flags_catalog.FLAGS, (
        "P7 FAIL: flags_catalog.FLAGS must declare HAL_PAUSE_LANE"
    )
    entry = flags_catalog.FLAGS["HAL_PAUSE_LANE"]
    assert entry["kind"] == "gate", f"P7 FAIL: expected kind=='gate', got {entry!r}"
    assert entry["default"] == "1", f"P7 FAIL: expected default=='1', got {entry!r}"
