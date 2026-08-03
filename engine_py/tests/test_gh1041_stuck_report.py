"""RED tests for GH1041 lot A+B — stuck-agent root-cause halt report (engine_py).

Spec: SHARED/memory/Decisions/2026-07-19_GH1041_stuck_report_spec.md
§2.1-§2.4 (design), §3 AC table (ACs 1-11 — this lot; ACs 12-13 are the bash
lot C, not here). Audit notes D2 (is_authoritative_execution guard on the
same-cycle-cap site) and D4 (safe `(state.get("error_codes") or [None])[-1]`
access — never bare `state["error_codes"][-1]`).

UUT symbols (never mocked/patched — stub-passability §1l):
  lib.stuck_report.build_stuck_report / emit_stuck_report / clear_stuck_report
  lib.restart_governor.governor_gate / governor_record_result
  engine.WorkflowEngine._execute_steps / execute (same-cycle-cap + terminal sites)

§1q / D1CF5FDF: `lib.stuck_report` does not exist yet at RED time. Every
import of it is deferred INSIDE the relevant test function body so this file
COLLECTS cleanly and each test FAILs at assert/import time, not at collect
time. `lib.restart_governor`, `engine`, `contracts`, `event_log` already
exist today (only new call-sites/behavior are added by GREEN) so they are
imported at module level, matching the precedent in
test_GH374_restart_governor.py / test_gh750_same_cycle_invalidation.py.
Conftest already puts ENGINE_ROOT (+lib, +workflows) on sys.path (§1q
singleton) — no module-level sys.path manipulation here.

§1i: AC1/AC2/AC5 pre-stage deterministic governor/report state on disk
BEFORE invoking the real `run.py` subprocess — no timing races.
"""
from __future__ import annotations

import json
import os
import subprocess

from helpers.engine_subprocess import engine_env
import sys
from pathlib import Path

import pytest

from bytedigger_engine.contracts import StepContract, StepResult, WorkflowContext, WorkflowDefinition
from bytedigger_engine.engine import WorkflowEngine
from bytedigger_engine.event_log import EventLog
from bytedigger_engine.lib.restart_governor import governor_gate, governor_record_result

ENGINE_ROOT = Path(__file__).resolve().parents[1]
RUN_PY = ENGINE_ROOT / "bytedigger_engine" / "run.py"


def _make_ctx(scratch: Path) -> WorkflowContext:
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={"scratchpad_dir": str(scratch)},
        question="GH1041 stuck report test",
        session_id="s",
        persona="hal",
        framework=None,
        domain=None,
    )


def _run_subprocess(args, timeout=60):
    return subprocess.run(
        [sys.executable, str(RUN_PY), *args], env=engine_env(),
        cwd=str(ENGINE_ROOT),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


# ─── AC1 ────────────────────────────────────────────────────────────────────


def test_ac1_governor_cap_deny_writes_stuck_report_with_null_last_error_when_error_codes_absent(tmp_path):
    """AC1 (D4 adversarial edge): pre-staged over-cap state with NO
    'error_codes' key -> subprocess deny (rc 1, E_RESTART_CAP) AND
    <tmp>/stuck-report.json exists with schema=1, breaker='restart_cap',
    next_step='operator_reset_restart_reason', run_id=<run>, last_error=null.

    At RED: lib.stuck_report doesn't exist and governor_gate never emits a
    report -> stuck-report.json is never written -> file-existence assertion
    fails.
    """
    run_id, workflow = "gh1041-ac1", "echo"
    state_file = tmp_path / f"restart-governor-{run_id}-{workflow}.json"
    state_file.write_text(
        json.dumps({"schema": 2, "crash_starts": 3, "gate_starts": 0, "pending": False, "sc_codes": []}),
        encoding="utf-8",
    )
    event_log_path = tmp_path / "events.jsonl"

    proc = _run_subprocess(
        ["--workflow", workflow, "--run-id", run_id, "--event-log", str(event_log_path), "--ctx-json", "{}"]
    )
    assert proc.returncode == 1, f"AC1 FAIL: expected rc 1, got {proc.returncode}; stderr={proc.stderr!r}"
    stdout_lines = [ln for ln in proc.stdout.strip().splitlines() if ln.strip()]
    payload = json.loads(stdout_lines[-1])
    assert payload.get("error_code") == "E_RESTART_CAP", f"AC1 FAIL: {payload!r}"

    report_path = tmp_path / "stuck-report.json"
    assert report_path.exists(), f"AC1 FAIL: expected {report_path} to exist"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report.get("schema") == 1
    assert report.get("breaker") == "restart_cap"
    assert report.get("next_step") == "operator_reset_restart_reason"
    assert report.get("run_id") == run_id
    assert report.get("last_error") is None, (
        f"AC1 FAIL: expected last_error=None (safe access, no error_codes key), got {report.get('last_error')!r}"
    )


# ─── AC2 ────────────────────────────────────────────────────────────────────


def test_ac2_governor_short_circuit_deny_writes_stuck_report_with_truncated_last_error(tmp_path):
    """AC2: pre-staged sc_codes=[X,X] non-transient -> deny E_RESTART_SHORT_CIRCUIT,
    report breaker='short_circuit', next_step='fix_root_cause_then_reset',
    last_error <= 500 chars.
    """
    run_id, workflow = "gh1041-ac2", "echo"
    state_file = tmp_path / f"restart-governor-{run_id}-{workflow}.json"
    state_file.write_text(
        json.dumps(
            {
                "schema": 2,
                "crash_starts": 0,
                "gate_starts": 0,
                "pending": False,
                "error_codes": ["E_CUSTOM_SC", "E_CUSTOM_SC"],
                "sc_codes": ["E_CUSTOM_SC", "E_CUSTOM_SC"],
            }
        ),
        encoding="utf-8",
    )
    event_log_path = tmp_path / "events.jsonl"

    proc = _run_subprocess(
        ["--workflow", workflow, "--run-id", run_id, "--event-log", str(event_log_path), "--ctx-json", "{}"]
    )
    assert proc.returncode == 1, f"AC2 FAIL: expected rc 1, got {proc.returncode}; stderr={proc.stderr!r}"
    stdout_lines = [ln for ln in proc.stdout.strip().splitlines() if ln.strip()]
    payload = json.loads(stdout_lines[-1])
    assert payload.get("error_code") == "E_RESTART_SHORT_CIRCUIT", f"AC2 FAIL: {payload!r}"

    report_path = tmp_path / "stuck-report.json"
    assert report_path.exists(), f"AC2 FAIL: expected {report_path} to exist"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report.get("breaker") == "short_circuit"
    assert report.get("next_step") == "fix_root_cause_then_reset"
    last_error = report.get("last_error")
    assert last_error is not None and len(last_error) <= 500


# ─── AC3 ────────────────────────────────────────────────────────────────────


def test_ac3_governor_allow_start_clears_a_preexisting_stale_stuck_report(tmp_path):
    """AC3: fresh run (no prior governor state) -> allow, and a pre-planted
    stale stuck-report.json IS unlinked by the allow path (§2.4 clear-on-start).

    Direct import of lib.restart_governor.governor_gate — real function call,
    no mocking (§1l).
    """
    event_log_path = str(tmp_path / "events.jsonl")
    stale_report = tmp_path / "stuck-report.json"
    stale_report.write_text(json.dumps({"schema": 1, "stale": True}), encoding="utf-8")

    result = governor_gate(event_log_path, "gh1041-ac3", "echo", None)
    assert result is None, f"AC3 FAIL: fresh state must allow, got {result!r}"
    assert not stale_report.exists(), (
        "AC3 FAIL: a pre-existing stuck-report.json must be cleared on an allowed start"
    )


# ─── AC4 ────────────────────────────────────────────────────────────────────


def test_ac4_governor_success_result_unlinks_both_state_and_stuck_report(tmp_path):
    """AC4: governor_record_result(error_code=None) unlinks BOTH the governor
    state file AND stuck-report.json.
    """
    run_id, workflow = "gh1041-ac4", "echo"
    state_file = tmp_path / f"restart-governor-{run_id}-{workflow}.json"
    state_file.write_text(
        json.dumps({"schema": 2, "crash_starts": 1, "gate_starts": 0, "pending": False, "error_codes": [], "sc_codes": []}),
        encoding="utf-8",
    )
    report_file = tmp_path / "stuck-report.json"
    report_file.write_text(json.dumps({"schema": 1}), encoding="utf-8")

    governor_record_result(tmp_path, run_id, workflow, error_code=None)

    assert not state_file.exists(), "AC4 FAIL: governor state file must be unlinked on success"
    assert not report_file.exists(), "AC4 FAIL: stuck-report.json must ALSO be unlinked on success"


# ─── AC5 ────────────────────────────────────────────────────────────────────


def test_ac5_restart_reason_subprocess_clears_stuck_report_and_emits_cleared_event(tmp_path):
    """AC5: `run.py --restart-reason ...` with a planted report -> report
    removed AND a stuck_report_cleared event row is appended to events.jsonl
    (extends-restart-reason contract; operator-reset entry path, §2.4).
    """
    run_id, workflow = "gh1041-ac5", "echo"
    event_log_path = tmp_path / "events.jsonl"
    event_log_path.write_text(
        json.dumps({"event": "workflow_started", "run_id": "seed", "workflow": workflow}) + "\n",
        encoding="utf-8",
    )
    report_path = tmp_path / "stuck-report.json"
    report_path.write_text(json.dumps({"schema": 1, "stale": True}), encoding="utf-8")

    proc = _run_subprocess(
        [
            "--workflow", workflow,
            "--run-id", run_id,
            "--restart-reason", "operator escape",
            "--event-log", str(event_log_path),
            "--ctx-json", "{}",
        ]
    )
    assert proc.returncode in (0, 1), (
        f"AC5 setup sanity: unexpected rc {proc.returncode}; stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    assert not report_path.exists(), (
        f"AC5 FAIL: stuck-report.json must be cleared by --restart-reason; "
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    events_text = event_log_path.read_text(encoding="utf-8")
    assert "stuck_report_cleared" in events_text, (
        f"AC5 FAIL: expected stuck_report_cleared event in events.jsonl, got {events_text!r}"
    )


# ─── AC6 ────────────────────────────────────────────────────────────────────


def test_ac6_build_stuck_report_is_pure_with_frozen_breaker_next_step_table(tmp_path):
    """AC6: build_stuck_report is pure — same inputs -> identical dict modulo
    'ts'; all 4 breaker->next_step mappings exact; no file I/O.
    """
    from bytedigger_engine.lib.stuck_report import build_stuck_report  # noqa: PLC0415 — deferred, §1q

    before = set(tmp_path.iterdir())
    kwargs = dict(
        run_id="r6", workflow="echo", step_name="step0", breaker="restart_cap",
        error_code="E_RESTART_CAP", retry_count=3,
        counters={"crash_starts": 3, "gate_starts": 0, "pending": False},
        last_error="boom",
    )
    report1 = build_stuck_report(**kwargs)
    report2 = build_stuck_report(**kwargs)
    assert set(tmp_path.iterdir()) == before, "AC6 FAIL: build_stuck_report must perform no file I/O"

    r1 = dict(report1)
    r2 = dict(report2)
    r1.pop("ts", None)
    r2.pop("ts", None)
    assert r1 == r2, f"AC6 FAIL: expected identical dicts modulo ts, got {r1!r} vs {r2!r}"

    table = {
        "restart_cap": "operator_reset_restart_reason",
        "short_circuit": "fix_root_cause_then_reset",
        "same_cycle_retry_cap": "inspect_validation_cycle",
        "terminal_failure": "check_provider_or_config",
    }
    for breaker, expected_next in table.items():
        kw = dict(kwargs)
        kw["breaker"] = breaker
        rep = build_stuck_report(**kw)
        assert rep.get("schema") == 1
        assert rep.get("next_step") == expected_next, (
            f"AC6 FAIL: breaker={breaker!r} expected next_step={expected_next!r}, got {rep.get('next_step')!r}"
        )


# ─── AC7 ────────────────────────────────────────────────────────────────────


def test_ac7_emit_stuck_report_byte_cap_truncates_last_error_never_silent_skip(tmp_path):
    """AC7: a 10KB last_error -> emitted file <= 4096 bytes, still valid
    JSON, last_error truncated (never silent-skip the write)."""
    from bytedigger_engine.lib.stuck_report import build_stuck_report, emit_stuck_report  # noqa: PLC0415

    report = build_stuck_report(
        run_id="r7", workflow="echo", step_name="s", breaker="terminal_failure",
        error_code="E_CUSTOM", retry_count=0, counters=None, last_error="x" * 10000,
    )
    emit_stuck_report(tmp_path, report, "r7")

    out = tmp_path / "stuck-report.json"
    assert out.exists(), "AC7 FAIL: emit_stuck_report must write the file even when over-cap (never silent-skip)"
    raw = out.read_bytes()
    assert len(raw) <= 4096, f"AC7 FAIL: expected <=4096 bytes, got {len(raw)}"
    data = json.loads(raw.decode("utf-8"))
    assert len(data.get("last_error") or "") < 10000, "AC7 FAIL: last_error must be truncated"


# ─── AC8 ────────────────────────────────────────────────────────────────────


def test_ac8_emit_stuck_report_double_emit_overwrites_atomically_not_accumulates(tmp_path):
    """AC8: double emit (retry-after-deny replay) overwrites atomically;
    retry_count reflects the LATEST governor counters, not emit count."""
    from bytedigger_engine.lib.stuck_report import build_stuck_report, emit_stuck_report  # noqa: PLC0415

    report1 = build_stuck_report(
        run_id="r8", workflow="echo", step_name="s", breaker="restart_cap",
        error_code="E_RESTART_CAP", retry_count=1,
        counters={"crash_starts": 1, "gate_starts": 0, "pending": False}, last_error=None,
    )
    emit_stuck_report(tmp_path, report1, "r8")

    report2 = build_stuck_report(
        run_id="r8", workflow="echo", step_name="s", breaker="restart_cap",
        error_code="E_RESTART_CAP", retry_count=2,
        counters={"crash_starts": 2, "gate_starts": 0, "pending": False}, last_error=None,
    )
    emit_stuck_report(tmp_path, report2, "r8")

    out = tmp_path / "stuck-report.json"
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data.get("retry_count") == 2, f"AC8 FAIL: expected overwritten retry_count=2, got {data.get('retry_count')!r}"
    matches = list(tmp_path.glob("stuck-report*.json"))
    assert len(matches) == 1, f"AC8 FAIL: expected exactly one file (atomic overwrite), got {matches}"


# ─── AC9 ────────────────────────────────────────────────────────────────────


def test_ac9_engine_same_cycle_cap_emits_stuck_report_zombie_guard_suppresses(tmp_path):
    """AC9: engine same-cycle cap (attempts > _MAX_SAME_CYCLE_RETRIES) emits
    same_cycle_retry_capped AND a stuck-report.json (breaker=same_cycle_retry_cap,
    retry_count=3, step_name=<step>); with is_authoritative_execution() patched
    to return False (guard helper patched, NOT the UUT), NO report is written
    (D2 zombie-replay guard).
    """
    from bytedigger_engine import engine as engine_mod  # noqa: PLC0415 — module exists; patched attr only

    def _make_always_same_cycle_workflow(name, step_name):
        calls = []

        def _exec(ctx, prev):
            calls.append(1)
            return StepResult(
                status="error", data={"retry_from_step": 0, "cycle_count": 0},
                duration_ms=0, step_name=step_name, error="always retry",
                error_code="E_VALIDATION_RETRY", recoverable=True,
            )

        return WorkflowDefinition(name=name, steps=[StepContract(name=step_name, execute=_exec)]), calls

    scratch = tmp_path / "scratch"
    scratch.mkdir(parents=True)

    workflow, calls = _make_always_same_cycle_workflow("wf_gh1041_ac9", "loopy")
    log = EventLog(tmp_path / "events.jsonl")
    eng = WorkflowEngine(event_log=log)
    eng.register("wf_gh1041_ac9", workflow)
    eng.execute("wf_gh1041_ac9", _make_ctx(scratch), run_id="run-ac9")

    events = log.read_all()
    capped = [e for e in events if e["event_type"] == "same_cycle_retry_capped"]
    assert capped, "AC9 FAIL: expected same_cycle_retry_capped event"

    report_path = tmp_path / "stuck-report.json"
    assert report_path.exists(), "AC9 FAIL: expected stuck-report.json written on same-cycle cap"
    data = json.loads(report_path.read_text(encoding="utf-8"))
    assert data.get("breaker") == "same_cycle_retry_cap"
    assert data.get("retry_count") == 3, f"AC9 FAIL: expected retry_count=3, got {data.get('retry_count')!r}"
    assert data.get("step_name") == "loopy"

    # ── zombie guard: patch the helper, not the UUT (emit_stuck_report) ──
    report_path.unlink()
    workflow2, calls2 = _make_always_same_cycle_workflow("wf_gh1041_ac9b", "loopy2")
    log2 = EventLog(tmp_path / "events2.jsonl")
    eng2 = WorkflowEngine(event_log=log2)
    eng2.register("wf_gh1041_ac9b", workflow2)

    orig = engine_mod.is_authoritative_execution
    engine_mod.is_authoritative_execution = lambda: False
    try:
        eng2.execute("wf_gh1041_ac9b", _make_ctx(scratch), run_id="run-ac9b")
    finally:
        engine_mod.is_authoritative_execution = orig

    assert not (tmp_path / "stuck-report.json").exists(), (
        "AC9 FAIL: non-authoritative (zombie/DBOS-replay) execution must NOT write stuck-report.json"
    )


# ─── AC10 ───────────────────────────────────────────────────────────────────


def test_ac10_engine_terminal_nonrecoverable_emits_report_recoverable_does_not(tmp_path):
    """AC10: a terminal non-recoverable finish (recoverable=False) emits
    breaker='terminal_failure' alongside the dispatcher report; a terminal
    RECOVERABLE error must NOT emit a stuck-report (the governor owns that
    breaker class instead)."""
    scratch = tmp_path / "scratch"
    scratch.mkdir(parents=True)

    def _exec_nonrecoverable(ctx, prev):
        return StepResult(
            status="error", data=None, duration_ms=0, step_name="term",
            error="boom", error_code="E_CUSTOM_TERMINAL", recoverable=False,
        )

    workflow = WorkflowDefinition(name="wf_gh1041_ac10", steps=[StepContract(name="term", execute=_exec_nonrecoverable)])
    log = EventLog(tmp_path / "events.jsonl")
    eng = WorkflowEngine(event_log=log)
    eng.register("wf_gh1041_ac10", workflow)
    eng.execute("wf_gh1041_ac10", _make_ctx(scratch), run_id="run-ac10")

    report_path = tmp_path / "stuck-report.json"
    assert report_path.exists(), "AC10 FAIL: expected stuck-report.json on terminal non-recoverable finish"
    data = json.loads(report_path.read_text(encoding="utf-8"))
    assert data.get("breaker") == "terminal_failure"

    # recoverable terminal error -> NO stuck-report
    report_path.unlink()

    def _exec_recoverable(ctx, prev):
        return StepResult(
            status="error", data=None, duration_ms=0, step_name="term2",
            error="boom2", error_code="E_CUSTOM_TERMINAL2", recoverable=True,
        )

    workflow2 = WorkflowDefinition(name="wf_gh1041_ac10b", steps=[StepContract(name="term2", execute=_exec_recoverable)])
    log2 = EventLog(tmp_path / "events2.jsonl")
    eng2 = WorkflowEngine(event_log=log2)
    eng2.register("wf_gh1041_ac10b", workflow2)
    eng2.execute("wf_gh1041_ac10b", _make_ctx(scratch), run_id="run-ac10b")

    assert not (tmp_path / "stuck-report.json").exists(), (
        "AC10 FAIL: recoverable terminal error must NOT emit a stuck-report (governor owns it)"
    )


# ─── AC11 ───────────────────────────────────────────────────────────────────


def test_ac11_emit_stuck_report_never_raises_on_unwritable_target_dir(tmp_path):
    """AC11: fail-safety — an unwritable target dir must never raise into the
    caller. Real permission denial (§1l anchor), no mocking."""
    from bytedigger_engine.lib.stuck_report import build_stuck_report, emit_stuck_report  # noqa: PLC0415

    bad_dir = tmp_path / "unwritable"
    bad_dir.mkdir()
    os.chmod(bad_dir, 0o500)
    try:
        report = build_stuck_report(
            run_id="r11", workflow="echo", step_name="s", breaker="restart_cap",
            error_code="E_RESTART_CAP", retry_count=1, counters=None, last_error=None,
        )
        try:
            emit_stuck_report(bad_dir, report, "r11")
        except Exception as exc:  # noqa: BLE001
            pytest.fail(f"AC11 FAIL: emit_stuck_report must never raise, got {exc!r}")
    finally:
        os.chmod(bad_dir, 0o700)
