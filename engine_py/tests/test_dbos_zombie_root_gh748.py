"""RED tests for GH748 — DBOS zombie-root: unique per-process executor_id +
orphan-PENDING eviction.

Spec: SHARED/memory/Decisions/2026-07-13_GH748_dbos_zombie_root_spec.md

Pre-GREEN expected profile (per spec section 3):
  FAIL: AC1, AC2, AC3, AC5, AC6
  PASS (pin, guards GH299 cache-hit contract is not broken by GH748): AC4

1q: no spec_from_file_location / exec_module -- module-level sys.path insert
mirrors the established sibling pattern (test_gh299_dbos_rekey_resume.py etc).
The not-yet-existing GREEN symbols (_generate_executor_id, _evict_orphan_pending)
are referenced INSIDE test function bodies via getattr, never imported at module
level, so the module COLLECTS cleanly even though they don't exist yet (D1CF5FDF).

1i: AC5's dead-owner PENDING orphan is pre-staged deterministically (real
invoke, then a single UPDATE that also sets a FOREIGN executor_id so
startup-recovery's get_pending_workflows(<current executor_id>, app_version)
is blind to the orphan in BOTH pre- and post-GREEN states -- otherwise the
shared-executor recovery thread races in and masks the deadlock). The bounded
timeout wrapper (thread + join(timeout=15)) is the forcing function that turns
the resulting deterministic hang into a fast, deterministic FAIL instead of
racing wall-clock.
"""
from __future__ import annotations

import sqlite3
import sys
import threading
from os.path import realpath
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Path setup -- mirrors test_gh299_dbos_rekey_resume.py pattern.
# ---------------------------------------------------------------------------
HERE = Path(__file__).resolve().parent          # engine_py/tests/
ENGINE_ROOT = HERE.parent                        # engine_py/


# Skip entire module if dbos is not installed (mirrors sibling pattern).
try:
    import dbos as dbos_module  # type: ignore
    from dbos import DBOS  # type: ignore
    from dbos._utils import GlobalParams  # type: ignore
except ImportError:
    pytest.skip("dbos not installed", allow_module_level=True)

# Existing (already-shipped) symbols -- safe to import at module level.
from bytedigger_engine.lib.dbos_setup import (  # type: ignore[import]
    _ctx_cfg_sha8,
    _PHASE_UUID_SEP,
    execute_durable_workflow,
    init_dbos,
)

from bytedigger_engine import telemetry_ctx  # type: ignore[import]
from bytedigger_engine.event_log import EventLog  # type: ignore[import]

# DBOS serialization helper (mirrors 941B33FC / 5F097D10 / gh299 pattern).
from dbos._serialization import serialize_value_as, DBOSDefaultSerializer  # type: ignore[import]


def _encode_output(obj: Any) -> str:
    blob, _ = serialize_value_as(obj, "py_pickle", DBOSDefaultSerializer)
    return blob


# ---------------------------------------------------------------------------
# Minimal context dict (mirrors sibling pattern).
# ---------------------------------------------------------------------------

_MINIMAL_CTX_DICT: dict[str, Any] = {
    "tenant_id": "hal-test",
    "scope": None,
    "db_path": None,
    "org_config": {"complexity": "SIMPLE", "phase": "spec"},
    "question": "gh748 smoke test",
    "session_id": "gh748-test",
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
# Row seeding helpers (1i: pre-stage deterministically).
# ---------------------------------------------------------------------------

def _seed_workflow_status(
    conn: sqlite3.Connection,
    workflow_uuid: str,
    output_obj: Any | None,
    *,
    status: str = "SUCCESS",
    executor_id: str | None = None,
    application_version: str | None = None,
    owner_xid: str | None = None,
) -> None:
    output_blob: str | None = _encode_output(output_obj) if output_obj is not None else None
    conn.execute(
        """
        INSERT INTO workflow_status
            (workflow_uuid, status, name, output, executor_id, application_version,
             owner_xid, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0)
        """,
        (workflow_uuid, status, workflow_uuid, output_blob, executor_id, application_version, owner_xid),
    )
    conn.commit()


def _seed_operation_output(
    conn: sqlite3.Connection,
    workflow_uuid: str,
    function_id: int,
    function_name: str,
    output_obj: Any,
) -> None:
    output_blob = _encode_output(output_obj)
    conn.execute(
        """
        INSERT INTO operation_outputs
            (workflow_uuid, function_id, function_name, output)
        VALUES (?, ?, ?, ?)
        """,
        (workflow_uuid, function_id, function_name, output_blob),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# Autouse fixtures (mirrors sibling pattern).
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


def _spy_dbos_config(monkeypatch) -> dict[str, Any]:
    """Patch dbos.DBOS.__init__ to capture the config kwarg without disturbing
    class-level methods (launch/workflow/etc stay on the real class).

    NOT a mock of the UUT (init_dbos) -- this spies on the third-party DBOS
    constructor, exactly the "infra, not UUT" carve-out in the RED brief.
    """
    captured: dict[str, Any] = {}
    orig_init = dbos_module.DBOS.__init__

    def _spy_init(self, *args, **kwargs):
        captured["config"] = kwargs.get("config")
        return orig_init(self, *args, **kwargs)

    monkeypatch.setattr(dbos_module.DBOS, "__init__", _spy_init)
    return captured


# ===========================================================================
# AC1 -- config passed to DBOS(...) carries a non-empty, non-"local" executor_id
# ===========================================================================

def test_ac1_config_carries_unique_executor_id_gh748(tmp_path, monkeypatch):
    """AC1: after init_dbos(), the config dict passed to the DBOS(...)
    constructor must contain key 'executor_id' with a non-empty value != 'local'.

    Pre-GREEN: FAIL -- init_dbos's config dict today is
    {"name": ..., "system_database_url": ...} with no executor_id key at all,
    so DBOS defaults to executor_id='local' (dbos._utils.GlobalParams).
    """
    captured = _spy_dbos_config(monkeypatch)
    _init_with_tmp_db(tmp_path, monkeypatch)

    cfg = captured.get("config")
    assert cfg is not None, "AC1 (GH748): DBOS(...) constructor must be invoked with a config dict"
    executor_id = cfg.get("executor_id")
    assert executor_id, (
        f"AC1 (GH748): config['executor_id'] must be present and non-empty; got {executor_id!r}. "
        f"init_dbos does not set executor_id in the DBOS config today."
    )
    assert executor_id != "local", (
        f"AC1 (GH748): config['executor_id'] must not be the DBOS default 'local'; "
        f"got {executor_id!r}."
    )


# ===========================================================================
# AC2 -- two init_dbos() cycles yield different executor_id values
# ===========================================================================

def test_ac2_two_init_cycles_yield_different_executor_ids_gh748(tmp_path, monkeypatch):
    """AC2: two init_dbos() cycles in separate simulated processes (reset
    _dbos_initialized + fresh DBOS singleton between) must yield DIFFERENT
    executor_id values.

    Pre-GREEN: FAIL -- init_dbos never sets executor_id, so both cycles
    resolve to the same DBOS default ('local' == 'local').
    """
    from bytedigger_engine.lib import dbos_setup as _dbos_mod  # type: ignore

    def _one_cycle(sub: Path) -> Any:
        captured = _spy_dbos_config(monkeypatch)
        sub.mkdir(parents=True, exist_ok=True)
        db_path = sub / "dbos.sqlite"
        monkeypatch.setenv("HAL_DBOS_DB_PATH", str(db_path))
        init_dbos(db_path)
        cfg = captured.get("config") or {}
        return cfg.get("executor_id")

    id_a = _one_cycle(tmp_path / "cycle_a")

    # Reset -- simulate a fresh process's init_dbos() call.
    try:
        DBOS.destroy()
    except Exception:
        pass
    _dbos_mod._dbos_initialized = False

    id_b = _one_cycle(tmp_path / "cycle_b")

    assert id_a != id_b, (
        f"AC2 (GH748): two init_dbos() cycles (simulated separate processes) must yield "
        f"DIFFERENT executor_id values (cross-process zombie isolation); got id_a={id_a!r} "
        f"id_b={id_b!r}."
    )


# ===========================================================================
# AC3 -- prior PENDING row under executor_id='local' invisible to THIS process's recovery
# ===========================================================================

def test_ac3_prior_local_pending_row_invisible_to_recovery_gh748(tmp_path, monkeypatch):
    """AC3: a system DB with a PENDING workflow_status row written under
    executor_id='local' for the current app_version must be INVISIBLE to
    the current process's get_pending_workflows(<current executor_id>, app_version)
    call -- i.e. the zombie's recovery input for this uuid is empty.

    Pre-GREEN: FAIL -- init_dbos never overrides GlobalParams.executor_id, so
    the current process ALSO resolves to executor_id='local', matching the
    seeded orphan row exactly -- get_pending_workflows returns it (non-empty).
    """
    db_path = _init_with_tmp_db(tmp_path, monkeypatch)

    app_version = GlobalParams.app_version
    current_executor_id = GlobalParams.executor_id
    orphan_uuid = "gh748-ac3-orphan__phase_x__deadbeef"

    conn = sqlite3.connect(str(db_path))
    try:
        _seed_workflow_status(
            conn, orphan_uuid, None,
            status="PENDING", executor_id="local", application_version=app_version,
        )
    finally:
        conn.close()

    from dbos._dbos import _dbos_global_instance  # already-launched singleton (init_dbos ran above)
    assert _dbos_global_instance is not None, (
        "AC3 (GH748): expected an already-launched DBOS singleton after init_dbos(); "
        "_dbos_global_instance is None."
    )
    pending = _dbos_global_instance._sys_db.get_pending_workflows(current_executor_id, app_version)
    pending_ids = [p.workflow_id for p in pending]

    assert orphan_uuid not in pending_ids, (
        f"AC3 (GH748): a PENDING row seeded under executor_id='local' must be invisible to "
        f"THIS process's get_pending_workflows(current_executor_id={current_executor_id!r}, "
        f"app_version={app_version!r}); found it in pending_ids={pending_ids!r}. Pre-GREEN, "
        f"the current process ALSO boots with executor_id='local' -- the orphan is NOT "
        f"filtered out (zombie recovery input is non-empty)."
    )


# ===========================================================================
# AC4 (pin) -- SUCCESS row written under a DIFFERENT executor_id still cache-hits
# ===========================================================================

def test_ac4_success_row_across_executor_boundary_cache_hits_gh748(tmp_path, monkeypatch):
    """AC4 (regression pin, GH299 preservation): a SUCCESS row for W written by
    a REAL first invoke must still be returned as a cache-hit when
    execute_durable_workflow re-invokes W from a fresh simulated
    process/executor: the registered workflow body executes 0 MORE times and
    the cached output is returned.

    Uses the real double-invoke pattern (mirrors test_gh299_dbos_rekey_resume.py)
    rather than raw-SQL-seeding a SUCCESS row: DBOS validates the existing
    row's name/class_name/config_name against the re-invoke BEFORE the
    cache-hit path (dbos _sys_db.py:696 DBOSConflictingWorkflowError) -- a raw
    seed with a fabricated name value raises a conflict instead of exercising
    the cache-hit. A real first invoke guarantees matching metadata.

    Expected: PASS both pre- and post-GREEN -- DBOS's check_workflow_result
    cache-hit lookup is keyed on workflow_uuid alone (executor_id-independent),
    per spec cite _sys_db.py:1319/642. GH748 must NOT regress this.
    """
    db_path = _init_with_tmp_db(tmp_path, monkeypatch)
    event_log_path = str(tmp_path / "events-ac4.jsonl")
    run_id = "gh748-ac4-run"
    ctx = dict(_MINIMAL_CTX_DICT)

    from bytedigger_engine import workflows as workflows_pkg  # type: ignore
    from bytedigger_engine.contracts import StepContract, StepResult, WorkflowDefinition  # type: ignore
    from bytedigger_engine.lib import dbos_setup as _dbos_mod  # type: ignore

    counter = {"n": 0}
    cached_data = {"echoed": "gh748-ac4-fixed-body-output"}

    def _counting_step(ctx_, _prev):
        counter["n"] += 1
        return StepResult(status="ok", data=cached_data, duration_ms=0, step_name="gh748_ac4_counter")

    def _counting_workflow():
        return WorkflowDefinition(
            name="gh748_ac4_counter",
            steps=[StepContract(name="gh748_ac4_counter", execute=_counting_step)],
        )

    orig_register_all = workflows_pkg.register_all

    def _patched_register_all(engine):
        orig_register_all(engine)
        engine.register("gh748_ac4_counter", _counting_workflow())

    monkeypatch.setattr(workflows_pkg, "register_all", _patched_register_all)

    log = EventLog(event_log_path)
    telemetry_ctx.set_current_run(event_log=log, run_id=run_id, step_name="cache_hit_test")

    # 1) Real first invoke -> body runs, DBOS writes a correct SUCCESS row.
    result_1 = execute_durable_workflow("gh748_ac4_counter", ctx, run_id, event_log_path=event_log_path)
    assert counter["n"] == 1 and result_1.get("data") == cached_data, (
        f"pre-stage sanity: first real invoke must run the body exactly once; "
        f"counter={counter['n']!r} result_1={result_1!r}"
    )

    # 2) Simulate a fresh process/executor: reset DBOS singleton + _dbos_initialized, re-init.
    try:
        DBOS.destroy()
    except Exception:
        pass
    _dbos_mod._dbos_initialized = False
    monkeypatch.setenv("HAL_DBOS_DB_PATH", str(db_path))
    init_dbos(db_path)

    telemetry_ctx.set_current_run(event_log=log, run_id=run_id, step_name="cache_hit_test")

    # 3) Second invoke from the "new process" -- must cache-hit, not re-run the body.
    result_2 = execute_durable_workflow("gh748_ac4_counter", ctx, run_id, event_log_path=event_log_path)

    assert result_2.get("data") == cached_data, (
        f"AC4 (GH748): re-invoking W from a fresh simulated executor must return the cached "
        f"SUCCESS output written by the first invoke; got {result_2.get('data')!r}"
    )
    assert counter["n"] == 1, (
        f"AC4 (GH748): the registered workflow body must NOT re-run on a cache-hit across "
        f"the executor_id boundary; expected counter==1 (only the first real invoke), got "
        f"{counter['n']!r}. A GREEN that breaks the executor-independent cache-hit contract "
        f"re-executes the body."
    )


# ===========================================================================
# AC5 -- dead-owner PENDING orphan re-executes exactly once within bounded timeout
# ===========================================================================

def test_ac5_dead_owner_pending_orphan_reexecutes_within_timeout_gh748(tmp_path, monkeypatch):
    """AC5: a PENDING workflow_status row for workflow_uuid=W from a dead prior
    owner_xid must be evicted before re-invoke, so execute_durable_workflow(W)
    re-executes the body EXACTLY ONCE, returns a FRESH result, row -> SUCCESS,
    and COMPLETES within a bounded timeout (no indefinite await_workflow_result
    poll).

    Reproduces the orphan by REAL-invoking W once (DBOS writes a correct
    SUCCESS row with matching name/class_name/config_name), then flipping that
    SAME row to PENDING w/ a dead owner_xid AND a FOREIGN executor_id via
    UPDATE (simulating a mid-kill from a DIFFERENT process), rather than
    INSERTing a fabricated row -- a raw-INSERT with name=<uuid> trips DBOS's
    pre-cache-hit name/class/config conflict check (dbos _sys_db.py:696
    DBOSConflictingWorkflowError) before should_execute is ever evaluated,
    which would mask the real hang this AC targets.

    The executor_id is deliberately set to a FOREIGN value
    ('gh748-foreign-exec-DIFFERENT'), never the current process's own
    executor_id ('local' pre-GREEN). This makes startup-recovery's
    get_pending_workflows(<current executor_id>, app_version) BLIND to the
    orphan in BOTH pre-GREEN (current process is 'local' != the foreign value)
    AND post-GREEN (unique per-process executor_id) -- so the shared-executor
    recovery thread can never race in and complete the orphan out from under
    the test. Without this, pre-GREEN's shared 'local' executor_id lets the
    startup-recovery thread find + re-execute the orphan itself, masking the
    deadlock this AC targets (racy, non-deterministic pre-GREEN).

    Pre-GREEN: DETERMINISTIC HANG (empirically confirmed) -- _evict_orphan_pending
    does not exist / is not wired into execute_durable_workflow, and the orphan
    is invisible to this process's own recovery (foreign executor_id), so
    nothing evicts or re-executes it. DBOS sees a PENDING row owned by a
    mismatched owner_xid ('gh748-dead-owner-xid' can never equal a
    freshly-generated per-call owner id) -> should_execute=False -> the sync
    path polls await_workflow_result indefinitely (dbos/_core.py:1203) -> the
    invoking thread never finishes within the 15s bound -> thread.is_alive()
    is True -> AC5 FAILS deterministically, not by luck of the recovery race.

    Post-GREEN: (B) _evict_orphan_pending deletes the orphan before re-invoke
    -> body runs exactly once -> SUCCESS within the bound -> PASS.

    1i: the PENDING orphan is pre-staged deterministically (real invoke, then
    a single UPDATE) BEFORE the second execute_durable_workflow call -- no
    race, no polling loop in the test itself; the bounded join(timeout) is the
    sole forcing function.
    """
    db_path = _init_with_tmp_db(tmp_path, monkeypatch)
    event_log_path = str(tmp_path / "events-ac5.jsonl")
    run_id = "gh748-ac5-run"
    ctx = dict(_MINIMAL_CTX_DICT)
    sha8 = _ctx_cfg_sha8(ctx)
    workflow_uuid = f"{run_id}{_PHASE_UUID_SEP}gh748_ac5_counter{_PHASE_UUID_SEP}{sha8}"

    from bytedigger_engine import workflows as workflows_pkg  # type: ignore
    from bytedigger_engine.contracts import StepContract, StepResult, WorkflowDefinition  # type: ignore
    from bytedigger_engine.lib import dbos_setup as _dbos_mod  # type: ignore

    counter = {"n": 0}

    def _counting_step(ctx_, _prev):
        counter["n"] += 1
        return StepResult(status="ok", data={"echoed": "gh748-ac5-fresh"}, duration_ms=0, step_name="gh748_ac5_counter")

    def _counting_workflow():
        return WorkflowDefinition(
            name="gh748_ac5_counter",
            steps=[StepContract(name="gh748_ac5_counter", execute=_counting_step)],
        )

    orig_register_all = workflows_pkg.register_all

    def _patched_register_all(engine):
        orig_register_all(engine)
        engine.register("gh748_ac5_counter", _counting_workflow())

    monkeypatch.setattr(workflows_pkg, "register_all", _patched_register_all)

    log = EventLog(event_log_path)
    telemetry_ctx.set_current_run(event_log=log, run_id=run_id, step_name="orphan_test")

    # 1) Real first invoke -> correct SUCCESS row written with matching name/class/config metadata.
    first_result = execute_durable_workflow(
        "gh748_ac5_counter", ctx, run_id, event_log_path=event_log_path
    )
    assert counter["n"] == 1 and first_result.get("status") == "ok", (
        f"pre-stage sanity: first real invoke must run the body exactly once; "
        f"counter={counter['n']!r} first_result={first_result!r}"
    )

    # 2) Simulate a mid-kill orphan: flip the SAME row to PENDING w/ a dead owner_xid,
    #    clear its output, drop its operation_outputs -- name/class/config metadata intact.
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "UPDATE workflow_status SET status='PENDING', owner_xid=?, output=NULL, "
            "executor_id=? WHERE workflow_uuid=?",
            ("gh748-dead-owner-xid", "gh748-foreign-exec-DIFFERENT", workflow_uuid),
        )
        conn.execute("DELETE FROM operation_outputs WHERE workflow_uuid=?", (workflow_uuid,))
        conn.commit()
        pre_status = conn.execute(
            "SELECT status FROM workflow_status WHERE workflow_uuid=?", (workflow_uuid,)
        ).fetchone()
        assert pre_status == ("PENDING",), (
            f"pre-stage sanity: expected the row flipped to PENDING, got {pre_status!r}"
        )
    finally:
        conn.close()

    # 3) Simulate a fresh process/executor + a fresh body-counter for THIS re-invoke's assertion.
    try:
        DBOS.destroy()
    except Exception:
        pass
    _dbos_mod._dbos_initialized = False
    monkeypatch.setenv("HAL_DBOS_DB_PATH", str(db_path))
    init_dbos(db_path)
    counter["n"] = 0

    telemetry_ctx.set_current_run(event_log=log, run_id=run_id, step_name="orphan_test")

    result_holder: dict[str, Any] = {}
    error_holder: dict[str, BaseException] = {}

    def _invoke() -> None:
        try:
            result_holder["result"] = execute_durable_workflow(
                "gh748_ac5_counter", ctx, run_id, event_log_path=event_log_path
            )
        except BaseException as e:  # pragma: no cover - failure path documented in assertion
            error_holder["error"] = e

    thread = threading.Thread(target=_invoke, daemon=True)
    thread.start()
    thread.join(timeout=15.0)

    assert not thread.is_alive(), (
        "AC5 (GH748): execute_durable_workflow(W) over a dead-owner PENDING orphan must "
        "COMPLETE within a bounded timeout (15s); the invoking thread is still alive, "
        "indicating an indefinite await_workflow_result poll (missing orphan eviction)."
    )
    if "error" in error_holder:
        raise error_holder["error"]

    result = result_holder.get("result")
    assert result is not None and result.get("status") == "ok", (
        f"AC5 (GH748): after eviction, execute_durable_workflow must return a FRESH ok "
        f"result; got {result!r}"
    )

    assert counter["n"] == 1, (
        f"AC5 (GH748): the workflow body must execute EXACTLY once during the re-invoke "
        f"after evicting the dead-owner orphan; got counter={counter['n']!r}."
    )

    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT status FROM workflow_status WHERE workflow_uuid=?", (workflow_uuid,)
        ).fetchone()
    finally:
        conn.close()
    assert row is not None and row[0] == "SUCCESS", (
        f"AC5 (GH748): workflow_status row for {workflow_uuid!r} must be SUCCESS after "
        f"fresh re-execution; got {row!r}"
    )


# ===========================================================================
# AC6 -- _evict_orphan_pending(W) deletes only W's PENDING row (+ its operation_outputs)
# ===========================================================================

def test_ac6_evict_orphan_pending_deletes_only_target_uuid_gh748(tmp_path, monkeypatch):
    """AC6: _evict_orphan_pending(W) must DELETE the PENDING row (+ its
    operation_outputs) for W only. A PENDING row for a different uuid W2,
    and a SUCCESS row for W3, must be untouched.

    Pre-GREEN: FAIL -- _evict_orphan_pending does not exist in lib.dbos_setup.
    Import deferred into this test body (getattr) per D1CF5FDF so the module
    collects cleanly.
    """
    db_path = _init_with_tmp_db(tmp_path, monkeypatch)

    from bytedigger_engine.lib import dbos_setup as _dbos_mod  # type: ignore
    helper = getattr(_dbos_mod, "_evict_orphan_pending", None)
    assert helper is not None, (
        "AC6 (GH748): lib.dbos_setup._evict_orphan_pending must exist as a named helper "
        "(§1aa); not found on production module today."
    )

    w1 = "gh748-ac6-w1__phase_x__deadbeef"
    w2 = "gh748-ac6-w2__phase_x__cafebabe"
    w3 = "gh748-ac6-w3__phase_x__f00dbeef"

    conn = sqlite3.connect(str(db_path))
    try:
        _seed_workflow_status(conn, w1, None, status="PENDING", owner_xid="dead-owner-1")
        _seed_operation_output(conn, w1, 0, "step_0", {"seeded": True})
        _seed_workflow_status(conn, w2, None, status="PENDING", owner_xid="dead-owner-2")
        _seed_workflow_status(conn, w3, {"status": "ok", "data": {}}, status="SUCCESS")

        pre_w1 = conn.execute(
            "SELECT COUNT(*) FROM workflow_status WHERE workflow_uuid=?", (w1,)
        ).fetchone()[0]
        pre_w1_ops = conn.execute(
            "SELECT COUNT(*) FROM operation_outputs WHERE workflow_uuid=?", (w1,)
        ).fetchone()[0]
        assert pre_w1 == 1 and pre_w1_ops == 1, (
            f"pre-seed sanity: expected 1 status row + 1 op row for w1, got {pre_w1}+{pre_w1_ops}"
        )
    finally:
        conn.close()

    helper(w1)

    conn = sqlite3.connect(str(db_path))
    try:
        post_w1_status = conn.execute(
            "SELECT COUNT(*) FROM workflow_status WHERE workflow_uuid=?", (w1,)
        ).fetchone()[0]
        post_w1_ops = conn.execute(
            "SELECT COUNT(*) FROM operation_outputs WHERE workflow_uuid=?", (w1,)
        ).fetchone()[0]
        post_w2_pending = conn.execute(
            "SELECT COUNT(*) FROM workflow_status WHERE workflow_uuid=? AND status='PENDING'", (w2,)
        ).fetchone()[0]
        post_w3_success = conn.execute(
            "SELECT COUNT(*) FROM workflow_status WHERE workflow_uuid=? AND status='SUCCESS'", (w3,)
        ).fetchone()[0]
    finally:
        conn.close()

    assert post_w1_status == 0, (
        f"AC6 (GH748): _evict_orphan_pending(w1) must DELETE w1's PENDING workflow_status "
        f"row; found {post_w1_status} row(s)."
    )
    assert post_w1_ops == 0, (
        f"AC6 (GH748): _evict_orphan_pending(w1) must DELETE w1's operation_outputs rows; "
        f"found {post_w1_ops} row(s)."
    )
    assert post_w2_pending == 1, (
        f"AC6 (GH748): w2's PENDING row (a DIFFERENT uuid) must be untouched; "
        f"found {post_w2_pending} row(s), expected 1."
    )
    assert post_w3_success == 1, (
        f"AC6 (GH748): w3's SUCCESS row must be untouched; found {post_w3_success} row(s), "
        f"expected 1."
    )
