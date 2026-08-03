"""RED-phase tests for GH700: false terminal workflow_finished from a
DBOS-recovery zombie re-execution.

Covers AC1-AC10 from SHARED/memory/Decisions/2026-07-13_GH700_nested_workflow_finished_race_spec.md §3.

§1q / D1CF5FDF: the production module ``execution_provenance`` does not exist
yet. Every import of it is deferred to INSIDE the test function body so the
file COLLECTS cleanly and fails at assert time, never at collection time.

conftest.py already adds engine_py root to sys.path at import time -- no
module-level sys.path manipulation needed (§1q / 81F97F3D gate). conftest.py
also autouse-isolates HAL_REWORK_LOG to ``tmp_path / "build-rework-log.jsonl"``
for every test (GH497 D1), which AC8 relies on directly.

§1i: the non-authoritative ("zombie") execution path is forced deterministically
by running ``eng.execute(...)`` inside a ``threading.Thread`` that is joined
before any assertion runs -- no sleep, no barrier, no polling. Any exception
raised inside the thread is captured and re-raised on the main thread via
``_run_on_thread`` so a thread-side error can never silently pass a test.

§1l: no test mocks/patches WorkflowEngine._emit, WorkflowEngine.execute,
derive_state.replay, or any execution_provenance symbol (the UUTs). All
assertions read back a REAL EventLog-backed tmp_path jsonl file (or, for AC6,
directly-appended real EventLog records) -- the production side-effect is the
forcing function.

AC1 and AC5 are regression guards over the existing foreground path and are
EXPECTED TO PASS today (byte-identical pre-fix behaviour, per spec Non-Goals).
All other tests (AC2, AC3, AC4, AC6, AC7, AC8, AC9, AC10) are EXPECTED TO FAIL
today because the shadow-completion guard does not exist yet.
"""

from __future__ import annotations

import threading

from bytedigger_engine.contracts import (
    StepContract,
    StepResult,
    WorkflowContext,
    WorkflowDefinition,
)
from bytedigger_engine.engine import WorkflowEngine
from bytedigger_engine import derive_state


# ─── Shared helpers ──────────────────────────────────────────────────────────

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


def ok_step(name: str, payload=None):
    def _run(_ctx, _prev):
        return StepResult(status="ok", data=payload, duration_ms=0, step_name=name)
    return StepContract(name=name, execute=_run)


def err_step(name: str):
    def _run(_ctx, _prev):
        return StepResult(
            status="error", data=None, duration_ms=0, step_name=name,
            error="boom", error_code="E_TEST",
        )
    return StepContract(name=name, execute=_run)


def _run_on_thread(fn) -> None:
    """Run fn() on a spawned (non-main) thread, join deterministically, and
    re-raise any exception that occurred inside the thread on the main thread
    (§1i: pre-staged join, never a race -- no sleep/barrier/poll)."""
    errors: list[BaseException] = []

    def _wrap():
        try:
            fn()
        except BaseException as e:  # noqa: BLE001 - must propagate everything
            errors.append(e)

    t = threading.Thread(target=_wrap)
    t.start()
    t.join()
    if errors:
        raise errors[0]


# ─── AC1: foreground path byte-identical to pre-fix (regression guard; PASSES today) ──

def test_ac1_foreground_execute_unaffected_single_workflow_finished(tmp_path):
    from bytedigger_engine.event_log import EventLog

    log = EventLog(tmp_path / "events.jsonl")
    eng = WorkflowEngine(event_log=log)
    eng.register("wf", WorkflowDefinition(name="wf", steps=[err_step("s1")]))
    ctx = make_ctx()

    result, _ = eng.execute("wf", ctx, run_id="rid-ac1")
    assert result.status == "error"

    events = log.read_all()
    finished = [e for e in events if e["event_type"] == "workflow_finished" and e["run_id"] == "rid-ac1"]
    assert len(finished) == 1
    assert finished[0]["payload"]["status"] == "error"
    shadow = [e for e in events if e["event_type"] == "engine_shadow_emit"]
    assert len(shadow) == 0


# ─── AC2: zombie (spawned thread) → 0 real terminal events, 1 shadow event ──

def test_ac2_zombie_execute_emits_zero_terminal_events_and_one_shadow(tmp_path):
    from bytedigger_engine.event_log import EventLog

    log = EventLog(tmp_path / "events.jsonl")
    eng = WorkflowEngine(event_log=log)
    eng.register("wf", WorkflowDefinition(name="wf", steps=[err_step("s1")]))
    ctx = make_ctx()

    _run_on_thread(lambda: eng.execute("wf", ctx, run_id="rid-ac2"))

    events = log.read_all()
    finished = [e for e in events if e["event_type"] == "workflow_finished" and e["run_id"] == "rid-ac2"]
    started = [e for e in events if e["event_type"] == "workflow_started" and e["run_id"] == "rid-ac2"]
    assert len(finished) == 0
    assert len(started) == 0

    shadow_finished = [
        e for e in events
        if e["event_type"] == "engine_shadow_emit"
        and e["run_id"] == "rid-ac2"
        and e["payload"].get("shadowed_event") == "wf_finished"
    ]
    assert len(shadow_finished) == 1
    payload = shadow_finished[0]["payload"]
    assert payload["status"] == "error"
    assert payload["provenance"] == "dbos_recovery"


# ─── AC3: foreground + zombie, same run_id → exactly 1 workflow_finished total ──

def test_ac3_foreground_and_zombie_same_run_id_yields_one_workflow_finished(tmp_path):
    from bytedigger_engine.event_log import EventLog

    log = EventLog(tmp_path / "events.jsonl")
    eng = WorkflowEngine(event_log=log)
    eng.register("wf", WorkflowDefinition(name="wf", steps=[err_step("s1")]))
    ctx = make_ctx()
    rid = "rid-ac3-shared"

    # Zombie first (spawned thread, joined) -- pre-staged, deterministic.
    _run_on_thread(lambda: eng.execute("wf", ctx, run_id=rid))

    # Foreground on the main thread, same run_id, same event-log path.
    fg_result, _ = eng.execute("wf", ctx, run_id=rid)

    events = log.read_all()
    finished = [e for e in events if e["event_type"] == "workflow_finished" and e["run_id"] == rid]
    assert len(finished) == 1
    assert finished[0]["payload"]["status"] == fg_result.status


# ─── AC4: zombie run 3x sequential → 0 workflow_finished, 3 shadow events ──

def test_ac4_repeated_zombie_recovery_stays_idempotent_never_terminal(tmp_path):
    from bytedigger_engine.event_log import EventLog

    log = EventLog(tmp_path / "events.jsonl")
    eng = WorkflowEngine(event_log=log)
    eng.register("wf", WorkflowDefinition(name="wf", steps=[err_step("s1")]))
    ctx = make_ctx()

    for i in range(3):
        _run_on_thread(lambda i=i: eng.execute("wf", ctx, run_id=f"rid-ac4-{i}"))

    events = log.read_all()
    finished = [e for e in events if e["event_type"] == "workflow_finished" and e["run_id"].startswith("rid-ac4-")]
    assert len(finished) == 0

    shadow_finished = [
        e for e in events
        if e["event_type"] == "engine_shadow_emit"
        and e["run_id"].startswith("rid-ac4-")
        and e["payload"].get("shadowed_event") == "wf_finished"
    ]
    assert len(shadow_finished) == 3


# ─── AC5: internal validation-retry (main thread) still 1 workflow_finished (regression guard; PASSES today) ──

def test_ac5_internal_validation_retry_still_single_workflow_finished(tmp_path):
    """Drives engine.py's real _MAX_VALIDATION_CYCLES retry recursion
    (engine.py ~line 260/437-522): the step signals
    StepResult(recoverable=True, data={"retry_from_step": 0, "cycle_count": 1})
    on its first call, which the engine recurses on internally before the
    step succeeds on its second call. Guards the existing invariant that
    in-phase retry cycles never multiply the terminal emit."""
    from bytedigger_engine.event_log import EventLog

    log = EventLog(tmp_path / "events.jsonl")
    eng = WorkflowEngine(event_log=log)

    call_count = {"n": 0}

    def _run(_ctx, _prev):
        call_count["n"] += 1
        if call_count["n"] == 1:
            return StepResult(
                status="error",
                data={"retry_from_step": 0, "cycle_count": 1, "findings": "needs fix"},
                duration_ms=0, step_name="validate",
                error="retry needed", error_code="E_RETRY_TEST",
                recoverable=True,
            )
        return StepResult(status="ok", data={"cycle": 2}, duration_ms=0, step_name="validate")

    eng.register(
        "retry_wf",
        WorkflowDefinition(name="retry_wf", steps=[StepContract(name="validate", execute=_run)]),
    )
    ctx = make_ctx()
    result, _ = eng.execute("retry_wf", ctx, run_id="rid-ac5")
    assert result.status == "ok"
    assert call_count["n"] == 2  # confirms the retry recursion actually engaged

    events = log.read_all()
    finished = [e for e in events if e["event_type"] == "workflow_finished" and e["run_id"] == "rid-ac5"]
    assert len(finished) == 1


# ─── AC6: derive_state.replay() must not treat a shadow event as terminal ──

def test_ac6_derive_state_shadow_event_stays_non_terminal(tmp_path):
    """This is the #700 bug expressed as an assertion (spec §3 AC6): a
    foreground run still "running" (only workflow_started seen) must not be
    flipped to status="error" by a zombie's shadow completion event."""
    from bytedigger_engine.event_log import EventLog

    log = EventLog(tmp_path / "events.jsonl")
    rid = "rid-ac6"
    log.append("workflow_started", {"workflow_name": "wf"}, rid)
    log.append(
        "engine_shadow_emit",
        {
            "workflow_name": "wf",
            "status": "error",
            "wall_ms": 10,
            "shadowed_event": "wf_finished",
            "provenance": "dbos_recovery",
        },
        rid,
    )

    state = derive_state.replay(log.read_all())
    run = state["runs"][rid]
    assert run["status"] != "error"
    assert run["status"] == "running"
    assert state["unknown_events"] == 0
    assert state.get("shadow_events") == 1


# ─── AC7: grep-safety -- shadow lines never carry the quoted terminal literals ──

def test_ac7_shadow_lines_never_contain_quoted_terminal_literals(tmp_path):
    from bytedigger_engine.event_log import EventLog

    log = EventLog(tmp_path / "events.jsonl")
    eng = WorkflowEngine(event_log=log)
    eng.register("wf", WorkflowDefinition(name="wf", steps=[err_step("s1")]))
    ctx = make_ctx()

    for i in range(2):
        _run_on_thread(lambda i=i: eng.execute("wf", ctx, run_id=f"rid-ac7-{i}"))

    raw_lines = [l for l in log.path.read_text(encoding="utf-8").splitlines() if l.strip()]
    shadow_lines = [l for l in raw_lines if '"engine_shadow_emit"' in l]
    assert shadow_lines, "expected at least one shadow line written by the two zombie runs"
    for line in shadow_lines:
        assert '"workflow_finished"' not in line, f"shadow line leaks quoted terminal literal: {line}"
        assert '"workflow_started"' not in line, f"shadow line leaks quoted terminal literal: {line}"


# ─── AC8: rework-summary guard -- zombie appends 0, foreground appends 1 ──

def test_ac8_zombie_execute_appends_zero_rework_records_foreground_appends_one(tmp_path):
    """conftest.py's autouse _telemetry_log_isolation fixture already points
    HAL_REWORK_LOG at tmp_path / "build-rework-log.jsonl" for this test."""
    from bytedigger_engine.event_log import EventLog

    log = EventLog(tmp_path / "events.jsonl")
    eng = WorkflowEngine(event_log=log)
    eng.register("wf", WorkflowDefinition(name="wf", steps=[ok_step("s1", "x")]))
    ctx = make_ctx()

    _run_on_thread(lambda: eng.execute("wf", ctx, run_id="rid-ac8-zombie"))

    rework_path = tmp_path / "build-rework-log.jsonl"
    zombie_count = 0
    if rework_path.exists():
        zombie_count = sum(1 for l in rework_path.read_text(encoding="utf-8").splitlines() if l.strip())
    assert zombie_count == 0, "zombie execute() must not append to the rework log"

    fg_result, _ = eng.execute("wf", ctx, run_id="rid-ac8-fg")
    assert fg_result.status == "ok"
    fg_lines = [l for l in rework_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(fg_lines) == 1, "foreground execute() must append exactly one rework record"


# ─── AC9: kill-switch is reversible in one env flip ──

def test_ac9_kill_switch_disables_shadowing_reversibly(tmp_path, monkeypatch):
    from bytedigger_engine.event_log import EventLog

    ctx = make_ctx()

    # Gate ON (default): zombie run must shadow workflow_finished -- no real
    # terminal event on the shared log.
    log_on = EventLog(tmp_path / "events-on.jsonl")
    eng_on = WorkflowEngine(event_log=log_on)
    eng_on.register("wf", WorkflowDefinition(name="wf", steps=[err_step("s1")]))
    _run_on_thread(lambda: eng_on.execute("wf", ctx, run_id="rid-ac9-on"))
    events_on = log_on.read_all()
    finished_on = [e for e in events_on if e["event_type"] == "workflow_finished"]
    assert len(finished_on) == 0, "gate ON (default) must shadow workflow_finished for a zombie execution"

    # Flip OFF: the same zombie shape must emit a real workflow_finished again
    # (pre-fix behaviour), proving the guard is reversible in one env flip.
    monkeypatch.setenv("HAL_ENGINE_SHADOW_EMITS", "0")
    log_off = EventLog(tmp_path / "events-off.jsonl")
    eng_off = WorkflowEngine(event_log=log_off)
    eng_off.register("wf", WorkflowDefinition(name="wf", steps=[err_step("s1")]))
    _run_on_thread(lambda: eng_off.execute("wf", ctx, run_id="rid-ac9-off"))
    events_off = log_off.read_all()
    finished_off = [e for e in events_off if e["event_type"] == "workflow_finished"]
    assert len(finished_off) == 1
    assert finished_off[0]["payload"]["status"] == "error"


# ─── AC10: execution_provenance() / is_authoritative_execution() / shadow_event_name() ──

def test_ac10_execution_provenance_and_shadow_event_name():
    """execution_provenance module does not exist yet -- import deferred
    inside the test body (§1q / D1CF5FDF collect-safety)."""
    from bytedigger_engine import execution_provenance as ep

    assert ep.execution_provenance() == "foreground"
    assert ep.is_authoritative_execution() is True

    results: dict[str, object] = {}

    def _check():
        results["prov"] = ep.execution_provenance()
        results["auth"] = ep.is_authoritative_execution()

    _run_on_thread(_check)
    assert results["prov"] == "dbos_recovery"
    assert results["auth"] is False

    assert ep.shadow_event_name("workflow_finished") == "wf_finished"
    assert ep.shadow_event_name("step_finished") == "step_finished"
