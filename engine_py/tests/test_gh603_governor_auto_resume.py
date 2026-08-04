"""RED tests for GH603 -- governor `--restart-reason` escape + DBOS ERROR-row
auto-evict on retry.

Spec: SHARED/memory/Decisions/2026-07-11_GH603_governor_dbos_auto_resume_spec.md
AC1-AC12 (spec section 3).

Pre-GREEN expected profile:
  FAIL: AC1, AC2, AC3, AC4, AC7, AC10, AC11
  PASS (regression pins, invariant 9 unchanged): AC5, AC6, AC8, AC9, AC12

D1CF5FDF / 1q: `governor_reset` does not exist yet -- every reference to it is
INSIDE the relevant test function body (via getattr presence-gate or deferred
import) so this module COLLECTS cleanly. `_maybe_evict_on_retry` and other
already-shipped symbols are imported at module level.

1l / stub-passability: no mocking of governor_reset or _maybe_evict_on_retry --
all anchors are real sqlite rows (tmp HAL_DBOS_DB_PATH), real jsonl event-log
lines, and a real state file on disk.

Patterns copied (not modified) from:
  tests/test_gh299_dbos_rekey_resume.py -- sqlite seed helpers, tmp-db init,
    autouse cleanup fixtures.
  tests/test_GH374_restart_governor.py -- governor unit-test style, state-file
    path convention, subprocess e2e pattern.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from os.path import realpath
from pathlib import Path
from typing import Any

import pytest

HERE = Path(__file__).resolve().parent          # engine_py/tests/
ENGINE_ROOT = HERE.parent                        # engine_py/


try:
    from dbos import DBOS  # type: ignore
except ImportError:
    pytest.skip("dbos not installed", allow_module_level=True)

# Already-shipped symbols -- safe to import at module level.
from bytedigger_engine.lib.dbos_setup import (  # type: ignore[import]
    _ctx_cfg_sha8,
    _PHASE_UUID_SEP,
    _maybe_evict_on_retry,
    execute_durable_workflow,
    init_dbos,
)

from bytedigger_engine import telemetry_ctx  # type: ignore[import]
from bytedigger_engine.event_log import EventLog  # type: ignore[import]

_MINIMAL_CTX_DICT: dict[str, Any] = {
    "tenant_id": "hal-test",
    "scope": None,
    "db_path": None,
    "org_config": {"complexity": "SIMPLE", "phase": "spec"},
    "question": "gh603 test",
    "session_id": "gh603-test",
    "persona": "hal",
    "framework": None,
    "domain": None,
    "enable_rag": False,
    "enable_reranker": False,
    "enable_domain_scoped_reranker": False,
    "message_history": None,
    "llm_provider": "azure",
    "llm_model": "gpt-4.1-datazone",
    "domains": [],
}


# ---------------------------------------------------------------------------
# Autouse fixtures -- mirrors test_gh299_dbos_rekey_resume.py pattern.
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _dbos_cleanup():
    yield
    try:
        DBOS.destroy()
    except Exception:
        pass
    try:
        from bytedigger_engine.lib import dbos_setup as _mod  # type: ignore
        _mod._dbos_initialized = False
    except ImportError:
        pass


@pytest.fixture(autouse=True)
def _reset_telemetry_ctx():
    telemetry_ctx.clear_current_run()
    yield
    telemetry_ctx.clear_current_run()


@pytest.fixture(autouse=True)
def _hal_env_isolation(monkeypatch):
    monkeypatch.delenv("HAL_DBOS_DB_PATH", raising=False)
    monkeypatch.delenv("HAL_DIR", raising=False)
    monkeypatch.delenv("HAL_RUNPY_HARD_EXIT", raising=False)
    # GH839 B1 env-pin: this file exercises the DBOS fallback explicitly, which
    # must keep working after HAL_ENGINE_DURABLE_BACKEND's default flips to
    # "native" (§2.3.5).
    monkeypatch.setenv("HAL_ENGINE_DURABLE_BACKEND", "dbos")
    yield


def _init_with_tmp_db(tmp_path: Path, monkeypatch) -> Path:
    """1j macOS realpath wrapper applied (804157CD)."""
    real_tmp = Path(realpath(str(tmp_path)))
    db_path = real_tmp / "dbos.sqlite"
    monkeypatch.setenv("HAL_DBOS_DB_PATH", str(db_path))
    init_dbos(db_path)
    return db_path


def _seed_error_row(conn: sqlite3.Connection, workflow_uuid: str) -> None:
    """Seed a terminal ERROR row -- output column carries opaque serialized
    junk bytes (per spec §2.2: NOT deserialized on the ERROR branch)."""
    conn.execute(
        """
        INSERT INTO workflow_status
            (workflow_uuid, status, name, output, created_at, updated_at)
        VALUES (?, 'ERROR', ?, ?, 0, 0)
        """,
        (workflow_uuid, workflow_uuid, b"not-a-valid-pickle-blob-junk"),
    )
    conn.commit()


def _seed_pending_row(conn: sqlite3.Connection, workflow_uuid: str) -> None:
    conn.execute(
        """
        INSERT INTO workflow_status
            (workflow_uuid, status, name, output, created_at, updated_at)
        VALUES (?, 'PENDING', ?, NULL, 0, 0)
        """,
        (workflow_uuid, workflow_uuid),
    )
    conn.commit()


# ===========================================================================
# AC1 -- governor_reset deletes state file, returns prior counters
# ===========================================================================

def test_ac1_governor_reset_deletes_state_and_returns_prior_counters(tmp_path):
    """AC1: governor_reset() unlinks the state file and returns prior_starts /
    prior_error_codes. Pre-GREEN: FAIL -- governor_reset does not exist yet."""
    from bytedigger_engine.lib import restart_governor as _gov  # type: ignore  # noqa: PLC0415
    governor_reset = getattr(_gov, "governor_reset", None)
    if governor_reset is None:
        pytest.fail(
            "AC1 (GH603): lib.restart_governor.governor_reset must exist as a "
            "named helper (§1aa); not found on production module today."
        )

    run_id, workflow = "gh603-ac1-run", "echo"
    state_file = tmp_path / f"restart-governor-{run_id}-{workflow}.json"
    state_file.write_text(
        json.dumps({"starts": 2, "error_codes": ["E_X", "E_X"]}), encoding="utf-8"
    )

    result = governor_reset(tmp_path, run_id, workflow, "input-changed: spec v2")

    assert not state_file.exists(), (
        "AC1 (GH603): governor_reset must unlink the restart-governor state file."
    )
    assert result == {"prior_starts": 2, "prior_error_codes": ["E_X", "E_X"]}, (
        f"AC1 (GH603): expected prior counters returned; got {result!r}"
    )


# ===========================================================================
# AC2 -- governor_reset emits restart_governor_reason_reset to event log
# ===========================================================================

def test_ac2_governor_reset_emits_reason_reset_event(tmp_path):
    """AC2: with event_log_path set, governor_reset emits
    restart_governor_reason_reset with reason + prior_starts.
    Pre-GREEN: FAIL -- governor_reset does not exist yet."""
    from bytedigger_engine.lib import restart_governor as _gov  # type: ignore  # noqa: PLC0415
    governor_reset = getattr(_gov, "governor_reset", None)
    if governor_reset is None:
        pytest.fail(
            "AC2 (GH603): lib.restart_governor.governor_reset must exist "
            "(same helper as AC1)."
        )

    run_id, workflow = "gh603-ac2-run", "echo"
    state_file = tmp_path / f"restart-governor-{run_id}-{workflow}.json"
    state_file.write_text(
        json.dumps({"starts": 2, "error_codes": ["E_X", "E_X"]}), encoding="utf-8"
    )
    event_log_path = str(tmp_path / "events-ac2.jsonl")

    governor_reset(tmp_path, run_id, workflow, "input-changed: spec v2", event_log_path)

    log = EventLog(event_log_path)
    events = log.read_all()
    reset_events = [e for e in events if e.get("event_type") == "restart_governor_reason_reset"]
    assert len(reset_events) == 1, (
        f"AC2 (GH603): expected exactly 1 restart_governor_reason_reset event; "
        f"got {len(reset_events)} (all types: {[e.get('event_type') for e in events]!r})."
    )
    payload = reset_events[0].get("payload", {})
    assert payload.get("reason") == "input-changed: spec v2", (
        f"AC2 (GH603): payload.reason mismatch; got {payload.get('reason')!r}"
    )
    assert payload.get("prior_starts") == 2, (
        f"AC2 (GH603): payload.prior_starts must be 2; got {payload.get('prior_starts')!r}"
    )


# ===========================================================================
# AC3 -- empty/whitespace reason -> ValueError
# ===========================================================================

def test_ac3_governor_reset_blank_reason_raises_value_error(tmp_path):
    """AC3: governor_reset(..., reason='  ') must raise ValueError.
    Pre-GREEN: FAIL -- governor_reset does not exist (no AttributeError caught
    as ValueError; presence-gated first so the failure is legible)."""
    from bytedigger_engine.lib import restart_governor as _gov  # type: ignore  # noqa: PLC0415
    governor_reset = getattr(_gov, "governor_reset", None)
    if governor_reset is None:
        pytest.fail(
            "AC3 (GH603): lib.restart_governor.governor_reset must exist "
            "before its validation behavior can be exercised."
        )

    run_id, workflow = "gh603-ac3-run", "echo"
    with pytest.raises(ValueError):
        governor_reset(tmp_path, run_id, workflow, "   ")


# ===========================================================================
# AC4 -- after governor_reset, governor_check allows retry (no manual surgery)
# ===========================================================================

def test_ac4_governor_check_allows_after_reset(tmp_path):
    """AC4: after a governor_reset unlinks the short-circuited state file,
    governor_check must return (True, None) -- retry proceeds without manual
    file deletion. Pre-GREEN: FAIL -- governor_reset does not exist yet."""
    from bytedigger_engine.lib.restart_governor import governor_check  # already-shipped symbol
    from bytedigger_engine.lib import restart_governor as _gov  # type: ignore  # noqa: PLC0415
    governor_reset = getattr(_gov, "governor_reset", None)
    if governor_reset is None:
        pytest.fail(
            "AC4 (GH603): lib.restart_governor.governor_reset must exist "
            "for the retry-allow path to be reachable."
        )

    run_id, workflow = "gh603-ac4-run", "echo"
    state_file = tmp_path / f"restart-governor-{run_id}-{workflow}.json"
    state_file.write_text(
        json.dumps({"starts": 3, "error_codes": ["E_TERM", "E_TERM"]}), encoding="utf-8"
    )

    # Sanity: short-circuited/at-cap before reset.
    allow_before, deny_before = governor_check(tmp_path, run_id, workflow, cap=3)
    assert allow_before is False, "AC4 (GH603) pre-condition: state must deny before reset"

    governor_reset(tmp_path, run_id, workflow, "input-changed: spec v2")

    allow_after, deny_after = governor_check(tmp_path, run_id, workflow, cap=3)
    assert allow_after is True, (
        f"AC4 (GH603): governor_check must allow after governor_reset; "
        f"got deny_code={deny_after!r}"
    )
    assert deny_after is None


# ===========================================================================
# AC5 (regression pin, invariant 9) -- E_RESTART_SHORT_CIRCUIT unweakened
# ===========================================================================

def test_ac5_short_circuit_still_denies_regression_pin(tmp_path):
    """AC5 pin: two identical non-transient error codes still short-circuit.
    Expected: PASS both pre- and post-GREEN (invariant 9)."""
    from bytedigger_engine.lib.restart_governor import governor_check, governor_record_result

    run_id, workflow = "gh603-ac5-run", "echo"
    governor_record_result(tmp_path, run_id, workflow, error_code="E_TERM")
    governor_record_result(tmp_path, run_id, workflow, error_code="E_TERM")

    allow, deny_code = governor_check(tmp_path, run_id, workflow)
    assert allow is False, "AC5 (GH603): short-circuit must NOT be weakened"
    assert deny_code == "E_RESTART_SHORT_CIRCUIT", (
        f"AC5 (GH603): expected E_RESTART_SHORT_CIRCUIT; got {deny_code!r}"
    )


# ===========================================================================
# AC6 (regression pin, invariant 9) -- E_RESTART_CAP unweakened
# ===========================================================================

def test_ac6_cap_still_denies_regression_pin(tmp_path):
    """AC6 pin: starts==cap still denies with E_RESTART_CAP.
    Expected: PASS both pre- and post-GREEN (invariant 9)."""
    from bytedigger_engine.lib.restart_governor import governor_check, governor_record_start

    run_id, workflow = "gh603-ac6-run", "echo"
    for _ in range(3):
        governor_record_start(tmp_path, run_id, workflow)

    allow, deny_code = governor_check(tmp_path, run_id, workflow, cap=3)
    assert allow is False, "AC6 (GH603): cap must NOT be weakened"
    assert deny_code == "E_RESTART_CAP", f"AC6 (GH603): expected E_RESTART_CAP; got {deny_code!r}"


# ===========================================================================
# AC7 -- ERROR-row eviction (real forcing function -- fails today)
# ===========================================================================

def test_ac7_error_row_evicted_on_retry(tmp_path, monkeypatch):
    """AC7: a seeded status='ERROR' workflow_status row + child operation_outputs
    row must both be deleted by _maybe_evict_on_retry, with a dbos_cache_evicted
    telemetry event carrying evict_reason=='error_row' emitted BEFORE deletion.

    Pre-GREEN: FAIL -- current query filters `AND status='SUCCESS'`, so an
    ERROR row is never even selected; both rows survive today."""
    db_path = _init_with_tmp_db(tmp_path, monkeypatch)
    workflow_uuid = "gh603-ac7-run__phase_x__deadbeef"

    conn = sqlite3.connect(str(db_path))
    try:
        _seed_error_row(conn, workflow_uuid)
        cols_info = conn.execute("PRAGMA table_info(operation_outputs)").fetchall()
        col_names = [c[1] for c in cols_info]
        col_values: dict[str, Any] = {
            "workflow_uuid": workflow_uuid,
            "function_id": 0,
            "function_name": "gh603_ac7_seed",
            "output": b"junk-child-output",
        }
        insert_cols = [c for c in col_names if c in col_values]
        placeholders = ",".join(["?"] * len(insert_cols))
        conn.execute(
            f"INSERT INTO operation_outputs ({','.join(insert_cols)}) VALUES ({placeholders})",
            tuple(col_values[c] for c in insert_cols),
        )
        conn.commit()

        pre_status = conn.execute(
            "SELECT COUNT(*) FROM workflow_status WHERE workflow_uuid=?", (workflow_uuid,)
        ).fetchone()[0]
        pre_ops = conn.execute(
            "SELECT COUNT(*) FROM operation_outputs WHERE workflow_uuid=?", (workflow_uuid,)
        ).fetchone()[0]
        assert pre_status == 1 and pre_ops == 1, "AC7 (GH603) pre-seed sanity failed"
    finally:
        conn.close()

    event_log_path = str(tmp_path / "events-ac7.jsonl")
    log = EventLog(event_log_path)
    telemetry_ctx.set_current_run(event_log=log, run_id="gh603-ac7-run", step_name="evict_test")

    _maybe_evict_on_retry(workflow_uuid)

    conn = sqlite3.connect(str(db_path))
    try:
        post_status = conn.execute(
            "SELECT COUNT(*) FROM workflow_status WHERE workflow_uuid=?", (workflow_uuid,)
        ).fetchone()[0]
        post_ops = conn.execute(
            "SELECT COUNT(*) FROM operation_outputs WHERE workflow_uuid=?", (workflow_uuid,)
        ).fetchone()[0]
    finally:
        conn.close()

    assert post_status == 0, (
        f"AC7 (GH603): ERROR row must be evicted; found {post_status} row(s). "
        f"Current code filters status='SUCCESS' only."
    )
    assert post_ops == 0, (
        f"AC7 (GH603): child operation_outputs row must also be evicted; found {post_ops} row(s)."
    )

    events = log.read_all()
    evict_events = [e for e in events if e.get("event_type") == "dbos_cache_evicted"]
    assert len(evict_events) == 1, (
        f"AC7 (GH603): expected exactly 1 dbos_cache_evicted event; got {len(evict_events)}."
    )
    payload = evict_events[0].get("payload", {})
    assert payload.get("evict_reason") == "error_row", (
        f"AC7 (GH603): evict_reason must be 'error_row'; got {payload.get('evict_reason')!r}"
    )


# ===========================================================================
# AC8 (regression pin) -- PENDING row untouched (durable resume preserved)
# ===========================================================================

def test_ac8_pending_row_preserved_regression_pin(tmp_path, monkeypatch):
    """AC8 pin: a seeded PENDING row must survive _maybe_evict_on_retry.
    Expected: PASS both pre- and post-GREEN."""
    db_path = _init_with_tmp_db(tmp_path, monkeypatch)
    workflow_uuid = "gh603-ac8-run__phase_x__cafebabe"

    conn = sqlite3.connect(str(db_path))
    try:
        _seed_pending_row(conn, workflow_uuid)
    finally:
        conn.close()

    event_log_path = str(tmp_path / "events-ac8.jsonl")
    log = EventLog(event_log_path)
    telemetry_ctx.set_current_run(event_log=log, run_id="gh603-ac8-run", step_name="evict_test")

    _maybe_evict_on_retry(workflow_uuid)

    conn = sqlite3.connect(str(db_path))
    try:
        post_count = conn.execute(
            "SELECT COUNT(*) FROM workflow_status WHERE workflow_uuid=?", (workflow_uuid,)
        ).fetchone()[0]
    finally:
        conn.close()
    assert post_count == 1, (
        f"AC8 (GH603): PENDING row must be preserved (durable resume); found {post_count} row(s)."
    )


# ===========================================================================
# AC9 (regression pin) -- SUCCESS ok-output (no error_code) preserved
# ===========================================================================

def test_ac9_success_ok_output_preserved_regression_pin(tmp_path, monkeypatch):
    """AC9 pin: a SUCCESS row without an error_code key must survive.
    Expected: PASS both pre- and post-GREEN."""
    from dbos._serialization import serialize_value_as, DBOSDefaultSerializer  # type: ignore

    db_path = _init_with_tmp_db(tmp_path, monkeypatch)
    workflow_uuid = "gh603-ac9-run__phase_x__f00dbeef"

    ok_output = {
        "status": "ok",
        "data": {"echoed": "gh603 ok output"},
        "duration_ms": 0,
        "step_name": "echo",
    }
    blob, _ = serialize_value_as(ok_output, "py_pickle", DBOSDefaultSerializer)

    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """
            INSERT INTO workflow_status
                (workflow_uuid, status, name, output, created_at, updated_at)
            VALUES (?, 'SUCCESS', ?, ?, 0, 0)
            """,
            (workflow_uuid, workflow_uuid, blob),
        )
        conn.commit()
    finally:
        conn.close()

    event_log_path = str(tmp_path / "events-ac9.jsonl")
    log = EventLog(event_log_path)
    telemetry_ctx.set_current_run(event_log=log, run_id="gh603-ac9-run", step_name="evict_test")

    _maybe_evict_on_retry(workflow_uuid)

    conn = sqlite3.connect(str(db_path))
    try:
        post_count = conn.execute(
            "SELECT COUNT(*) FROM workflow_status WHERE workflow_uuid=?", (workflow_uuid,)
        ).fetchone()[0]
    finally:
        conn.close()
    assert post_count == 1, (
        f"AC9 (GH603): ok-output SUCCESS row must be preserved; found {post_count} row(s)."
    )


# ===========================================================================
# AC10 -- run.py --restart-reason resets short-circuit state, exit 0
# ===========================================================================

def test_ac10_run_py_restart_reason_resets_and_exits_zero(tmp_path, monkeypatch):
    """AC10: `run.py --workflow echo --restart-reason X --event-log L --run-id R`
    with a pre-existing short-circuited state file for (R, echo) must reset the
    governor state (fresh governor_record_start, starts==1), emit
    restart_governor_reason_reset, and exit 0.

    Pre-GREEN: FAIL -- `--restart-reason` is not a recognized argparse flag
    today; argparse raises SystemExit(2) (unrecognized arguments)."""
    from bytedigger_engine import run  # noqa: PLC0415 -- UUT

    db_path = tmp_path / "dbos.sqlite"
    monkeypatch.setenv("HAL_DBOS_DB_PATH", str(db_path))

    run_id, workflow = "gh603-ac10-run", "echo"
    event_log_path = tmp_path / "events-ac10.jsonl"

    state_file = tmp_path / f"restart-governor-{run_id}-{workflow}.json"
    state_file.write_text(
        json.dumps({"starts": 2, "error_codes": ["E_TERM", "E_TERM"]}), encoding="utf-8"
    )

    argv = [
        "run.py",
        "--workflow", workflow,
        "--restart-reason", "input-changed: gh603 test",
        "--event-log", str(event_log_path),
        "--run-id", run_id,
        "--ctx-json", json.dumps({"org_config": {}, "question": "ac10"}),
    ]
    monkeypatch.setattr(sys, "argv", argv)

    try:
        rc = run.main()
    except SystemExit as e:
        pytest.fail(
            f"AC10 (GH603): run.py must accept --restart-reason without a usage "
            f"SystemExit; got SystemExit({e.code!r}). Flag not wired yet today."
        )

    assert rc == 0, f"AC10 (GH603): expected exit 0; got {rc}"

    log = EventLog(str(event_log_path))
    events = log.read_all()
    reset_events = [e for e in events if e.get("event_type") == "restart_governor_reason_reset"]
    assert len(reset_events) == 1, (
        f"AC10 (GH603): expected 1 restart_governor_reason_reset event; "
        f"got {len(reset_events)} (types: {[e.get('event_type') for e in events]!r})."
    )
    denied_events = [e for e in events if e.get("event_type") == "restart_governor_denied"]
    assert len(denied_events) == 0, (
        f"AC10 (GH603): the reset run must NOT also emit restart_governor_denied; "
        f"got {len(denied_events)} such events."
    )

    assert not state_file.exists(), (
        "AC10 (GH603): governor state file must be ABSENT after a successful run "
        "(governor_record_result(error_code=None) unlinks it on success, "
        "restart_governor.py:84-90) -- state persistence is not the AC10 anchor."
    )


# ===========================================================================
# AC11 -- e2e: 2x execute_durable_workflow(same uuid) auto-resumes on ERROR
# ===========================================================================

def test_ac11_execute_durable_workflow_auto_resumes_after_error(tmp_path, monkeypatch):
    """AC11 (e2e autonomy, exit-criteria (d)): a workflow step that raises on
    its 1st invocation and succeeds on its 2nd is invoked twice via
    execute_durable_workflow with an IDENTICAL run_id/ctx (same workflow_uuid).
    The 2nd call must be a genuine fresh re-execution (status=='ok'), not a
    re-raised cached ERROR -- no manual nonce/state-row surgery.

    Pre-GREEN: FAIL -- today's ERROR row is never evicted, so DBOS re-raises
    the cached exception on the 2nd call instead of re-executing the step."""
    from bytedigger_engine.workflows import echo as _echo_mod  # noqa: PLC0415
    from bytedigger_engine.contracts import StepResult  # noqa: PLC0415

    counter_file = tmp_path / "call-counter.txt"
    counter_file.write_text("0", encoding="utf-8")

    def _flaky_echo(ctx, _prev):
        n = int(counter_file.read_text(encoding="utf-8"))
        n += 1
        counter_file.write_text(str(n), encoding="utf-8")
        if n == 1:
            raise RuntimeError("gh603 ac11 deliberate first-call failure")
        return StepResult(status="ok", data={"echoed": ctx.question}, duration_ms=0, step_name="echo")

    monkeypatch.setattr(_echo_mod, "_echo", _flaky_echo)

    db_path = _init_with_tmp_db(tmp_path, monkeypatch)
    event_log_path = str(tmp_path / "events-ac11.jsonl")
    run_id = "gh603-ac11-run"
    ctx = dict(_MINIMAL_CTX_DICT)

    log = EventLog(event_log_path)
    telemetry_ctx.set_current_run(event_log=log, run_id=run_id, step_name="ac11_test")

    with pytest.raises(Exception):
        execute_durable_workflow("echo", ctx, run_id, event_log_path=event_log_path)

    telemetry_ctx.set_current_run(event_log=log, run_id=run_id, step_name="ac11_test")
    result = execute_durable_workflow("echo", ctx, run_id, event_log_path=event_log_path)

    assert result.get("status") == "ok", (
        f"AC11 (GH603): 2nd call with identical run_id/ctx must genuinely re-execute "
        f"and return status=='ok'; got status={result.get('status')!r} "
        f"error_code={result.get('error_code')!r}. Manual nonce-bump should not be required."
    )

    calls = int(counter_file.read_text(encoding="utf-8"))
    assert calls >= 2, (
        f"AC11 (GH603): the flaky step function must have been genuinely invoked "
        f"at least twice (real re-execution, not a cached replay); counter={calls}"
    )


# ===========================================================================
# AC12 -- --restart-reason without --event-log is a no-op (warn on stderr)
# ===========================================================================

def test_ac12_restart_reason_without_event_log_is_noop(tmp_path, monkeypatch, capsys):
    """AC12: `run.py --restart-reason X` without --event-log must warn on
    stderr and continue execution as a no-op (governor is inactive without
    --event-log per governor_gate's existing behavior).

    Expected: PASS both pre- and post-GREEN in one sense (no --event-log means
    governor_gate already no-ops) -- but pre-GREEN this actually FAILS at the
    argparse layer (unrecognized --restart-reason -> SystemExit(2)), so it is
    a genuine forcing function for the flag's existence, not just its no-op
    semantics."""
    from bytedigger_engine import run  # noqa: PLC0415 -- UUT

    db_path = tmp_path / "dbos.sqlite"
    monkeypatch.setenv("HAL_DBOS_DB_PATH", str(db_path))

    argv = [
        "run.py",
        "--workflow", "echo",
        "--restart-reason", "input-changed: gh603 test",
        "--ctx-json", json.dumps({"org_config": {}, "question": "ac12"}),
    ]
    monkeypatch.setattr(sys, "argv", argv)

    try:
        rc = run.main()
    except SystemExit as e:
        pytest.fail(
            f"AC12 (GH603): --restart-reason without --event-log must still be "
            f"a recognized flag (no-op path); got SystemExit({e.code!r})."
        )

    assert rc == 0, f"AC12 (GH603): expected exit 0 (echo succeeds, flag no-op); got {rc}"


# ===========================================================================
# AC13 -- blank --restart-reason -> usage exit 2 with stderr message
# ===========================================================================

def test_ac13_run_py_blank_restart_reason_usage_exit(monkeypatch, capsys):
    """AC13: `run.py --workflow echo --restart-reason "   "` (whitespace-only)
    must return/exit 2 AND write
    "--restart-reason requires a non-empty reason" to stderr (spec §2.3:
    validated immediately after argparse, before the try-block).

    Pre-GREEN: FAIL -- --restart-reason is not yet a recognized argparse flag,
    so argparse itself raises SystemExit(2) for the unrecognized argument --
    but WITHOUT our required stderr message, so the message-substring
    assertion is the real forcing function (a mere pre-existing SystemExit(2)
    from argparse would NOT satisfy this test)."""
    from bytedigger_engine import run  # noqa: PLC0415 -- UUT

    argv = [
        "run.py",
        "--workflow", "echo",
        "--restart-reason", "   ",
        "--ctx-json", json.dumps({"org_config": {}, "question": "ac13"}),
    ]
    monkeypatch.setattr(sys, "argv", argv)

    rc: int | None = None
    try:
        rc = run.main()
    except SystemExit as e:
        rc = e.code

    captured = capsys.readouterr()
    assert rc == 2, f"AC13 (GH603): expected return/exit code 2 for blank --restart-reason; got {rc!r}"
    assert "--restart-reason requires a non-empty reason" in captured.err, (
        f"AC13 (GH603): stderr must contain the exact usage message; got stderr={captured.err!r}"
    )
