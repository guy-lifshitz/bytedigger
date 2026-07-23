"""DBOS durable-workflow init + wrapper for HAL engine_py (F7BE40B9 R-prime Ship #1)."""
from __future__ import annotations
import logging
import os
import re
import sqlite3
import sys
import uuid
from pathlib import Path
from typing import Any

# GH612 Ship A: the phase key, the ctx-hash, the engine invoke and the backend
# resolver live in the dbos-free `lib.phase_sentinel`. The dependency is one-way
# (phase_sentinel NEVER imports dbos_setup) so Ship B deletes this module without
# touching that one. `_PHASE_UUID_SEP` / `_CTX_HASH_LEN` / `_ctx_cfg_sha8` are
# RE-EXPORTED here unchanged for their 6 existing consumers (lib/dbos_list_runs.py:12,
# lib/dbos_status_run.py:12 + 4 test modules).
from lib.phase_sentinel import (  # noqa: F401 — re-export contract (GH612 AC5)
    _CTX_HASH_LEN,
    _PHASE_UUID_SEP,
    _ctx_cfg_sha8,
    execute_engine,
    execute_native_workflow,
    phase_key,
    resolve_durable_backend,
)

# Module-level constants ─────────────────────────────────────────────────────
_DBOS_APP_NAME = "hal-engine"   # DBOSConfig name validation: ^[a-z0-9_-]{3,30}$

# Pre-flight primitive types accepted in context_dict (AC9 contract)
_PRIMITIVE_TYPES = (str, int, float, bool, type(None), list, dict, tuple)

# 941B33FC R3 / GH299: eviction CLASSIFICATION set — no longer gates eviction.
# Since GH299, ANY truthy error_code on a failed-terminal row is evicted; this
# set now only classifies the emitted evict_reason ("evict_code" if a member,
# else "failed_terminal"). Renamed from _RECOVERABLE_ERROR_CODES (5F097D10) to
# _EVICT_ON_RETRY_CODES (941B33FC). No back-compat alias per workflows.md §1g
# single-source + agreement 941B33FC Principle B.
_EVICT_ON_RETRY_CODES = frozenset({
    # infra-transient (unchanged from 5F097D10)
    "E_LLM_TIMEOUT",
    "E_LLM_NO_PROGRESS",
    "E_LLM_EXIT",
    "E_LLM_SPEND_LIMIT",
    "E_LLM_RESULT_ERROR",
    "E_LLM_NO_RESULT_EVENT",
    "E_STEP_TIMEOUT",
    "E_VALIDATION_RETRY",
    # caller-error transient (new in 941B33FC R3)
    "E_LLM_CMD_MISSING",    # 33369eba — string-vs-list ctx footgun
    "E_CONFIG_INVALID",     # generic config-error retry pattern
    "E_CTX_MALFORMED",      # generic ctx-shape retry pattern
    "E_SPEC_LINT_FAIL",     # ab60a7cb — spec citation/format fix + retry
})


def _module_default_db_path() -> Path:
    """§1g canonical resolver fallback when HAL_DBOS_DB_PATH unset.

    Routes through the config_provider seam: HAL provider → the state dir's dbos.sqlite;
    neutral provider → cwd/<foreign_state_dirname()>/dbos.sqlite (standalone resumable,
    zero infra).
    """
    from config_provider import get_config, dbos_db_relpath
    return get_config().hal_root() / dbos_db_relpath()


def _resolve_db_path() -> Path:
    """§1g canonical resolver: HAL_DBOS_DB_PATH env > HAL_DIR-derived > error.

    Emits emit_resolver_resolved("dbos_db_path", source, value) on each call
    per workflows.md §1g observability extension (966E571B).
    """
    from lib.observability.emit_resolver import emit_resolver_resolved
    from config_provider import env_opt
    env_path = env_opt("HAL_DBOS_DB_PATH")
    if env_path:
        result = Path(env_path)
        emit_resolver_resolved("dbos_db_path", "HAL_DBOS_DB_PATH", str(result))
        return result
    result = _module_default_db_path()
    source = "HAL_DIR" if env_opt("HAL_DIR") else "local_default"
    emit_resolver_resolved("dbos_db_path", source, str(result))
    return result


def _generate_executor_id() -> str:
    """Unique-per-process DBOS executor id (GH748). A distinct executor_id per
    phase process makes get_pending_workflows(executor_id, app_version) return
    zero rows for any OTHER process's PENDING workflows, so DBOS startup-recovery
    never re-executes a prior phase's orphan (the zombie class). Traceable (pid)
    + collision-free (uuid4)."""
    return f"hal-{os.getpid()}-{uuid.uuid4().hex[:12]}"


class E_DBOS_SCHEMA_INIT_FAILED(RuntimeError):
    """Raised on DBOS init/launch failure. Bare except is forbidden (BARK-asymmetry-closer)."""


_dbos_initialized = False


def init_dbos(db_path: Path | None = None) -> None:
    """Idempotent DBOS init. Sets up SQLite system_database_url + WAL/busy_timeout pragmas.

    Env var `HAL_DBOS_DB_PATH` overrides the default path when `db_path` is None
    (pytest-xdist worker isolation, R-MEDIUM #2 mitigation).

    GH612 §2.3 HARD CONSTRAINT: the backend gate is the FIRST statement of the body
    and sits ABOVE the `try:` below. Inside the try, `except Exception` would swallow
    E_DURABLE_BACKEND_UNKNOWN and re-raise it as E_DBOS_SCHEMA_INIT_FAILED, corrupting
    the §2.4 error taxonomy. Under `native`: no sqlite store, no DBOS(), no
    DBOS.launch(), no non-daemon executor thread, no startup-recovery replay.
    """
    if resolve_durable_backend() != "dbos":
        return
    global _dbos_initialized
    if _dbos_initialized:
        return
    target = db_path or _resolve_db_path()
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        # A2: silence DBOS console URL noise BEFORE launch
        logging.getLogger("dbos").setLevel(logging.WARNING)
        # Apply WAL mode BEFORE DBOS.launch() — WAL is a persistent SQLite property,
        # so DBOS picks up the existing WAL DB. Cannot apply post-launch because DBOS
        # holds an open writer connection that blocks raw sqlite3 PRAGMA changes.
        with sqlite3.connect(str(target), timeout=30.0) as conn:
            conn.execute("PRAGMA journal_mode=wal")
            conn.execute("PRAGMA busy_timeout=30000")
        from dbos import DBOS  # local import — keeps module import-clean if dbos absent
        # R-BLOCKER #1 fix: use system_database_url, NOT DBOS_DATABASE_URL env var
        DBOS(config={
            "name": _DBOS_APP_NAME,
            "system_database_url": f"sqlite:///{target}",
            "executor_id": _generate_executor_id(),   # GH748
        })
        DBOS.launch()
        _dbos_initialized = True
    except Exception as e:
        raise E_DBOS_SCHEMA_INIT_FAILED(
            f"DBOS init failed for {target}: {e}. "
            f"Suggested: check write permissions on parent dir, "
            f"verify dbos==2.22.0 installed."
        ) from e


def teardown_dbos() -> None:
    """Deterministic DBOS shutdown (issue #275, py3.14).

    Stops the non-daemon `dbos-executor` thread that otherwise blocks CPython
    interpreter shutdown after workflow_finished. Idempotent + fail-soft: no-ops
    when DBOS was never launched, never raises, resets `_dbos_initialized` so a
    later init_dbos() re-launches cleanly (same-process safety / test isolation).
    """
    global _dbos_initialized
    if not _dbos_initialized:
        return
    try:
        from dbos import DBOS
        DBOS.destroy()
    except Exception:
        pass
    finally:
        _dbos_initialized = False


def _assert_pickle_clean(context_dict: dict[str, Any]) -> None:
    """R-BLOCKER #3 contract: walk context_dict recursively; raise TypeError on non-primitive.

    The literal substring 'non-primitive' is part of the wrapper contract — AC9(b) asserts on it.
    Recursively checks nested list/dict/tuple values.
    """
    def _walk(value: Any, path: str) -> None:
        if not isinstance(value, _PRIMITIVE_TYPES):
            raise TypeError(
                f"non-primitive value in context_dict at key {path!r}: {type(value).__name__}"
            )
        if isinstance(value, dict):
            for k, v in value.items():
                _walk(v, f"{path}.{k}")
        elif isinstance(value, (list, tuple)):
            for i, v in enumerate(value):
                _walk(v, f"{path}[{i}]")
    for k, v in context_dict.items():
        _walk(v, k)


try:
    from dbos import DBOS, SetWorkflowID  # noqa: E402
    _DBOS_IMPORT_ERROR: Exception | None = None
except ImportError as _e:  # dbos is an optional extra (pip install <pkg>[dbos])
    DBOS = None  # type: ignore[assignment]
    SetWorkflowID = None  # type: ignore[assignment]
    _DBOS_IMPORT_ERROR = _e


if DBOS is not None:
    @DBOS.workflow()
    def _dbos_execute_wrapper(
        workflow_name: str,
        context_dict: dict[str, Any],
        run_id: str,
        event_log_path: str | None = None,
    ) -> dict[str, Any]:
        """@DBOS.workflow-decorated boundary around WorkflowEngine.execute().

        INTERNAL — callers MUST use `execute_durable_workflow()` instead so SetWorkflowID
        is applied at the right scope. Direct invocation generates a random workflow_uuid.

        Returns a pickle-clean dict (status, data, duration_ms, step_name, error,
        error_code, suggestion, recoverable). 'metadata' EXCLUDED — restored as `{}`
        by run.py via StepResult's `default_factory=dict`.

        GH612 Ship A: the body is now `lib.phase_sentinel.execute_engine` — ONE engine
        invoke shared by both backends. This function keeps its @DBOS.workflow()
        decorator, its 4-arg signature and its 8-key return (direct callers exist).
        """
        return execute_engine(workflow_name, context_dict, run_id, event_log_path)


def _evict_orphan_pending(workflow_uuid: str) -> None:
    """GH748: DELETE a PENDING orphan row for workflow_uuid left by a dead prior
    process, so a durable-resume re-invoke re-executes cleanly instead of polling
    await_workflow_result forever. HAL phases are sequential per workflow_uuid, so
    a PENDING row for THIS uuid is always a dead prior attempt, never a live owner.
    Mirrors _maybe_evict_on_retry's DELETE + error taxonomy (§4). HAL step resume
    uses its own step_sentinel store, not DBOS operation_outputs, so clearing
    operation_outputs is safe.
    """
    target = _resolve_db_path()
    if not target.exists():
        return
    try:
        with sqlite3.connect(str(target), timeout=30.0) as conn:
            row = conn.execute(
                "SELECT status FROM workflow_status WHERE workflow_uuid=? AND status='PENDING'",
                (workflow_uuid,),
            ).fetchone()
            if not row:
                return
            conn.execute("DELETE FROM operation_outputs WHERE workflow_uuid=?", (workflow_uuid,))
            conn.execute(
                "DELETE FROM workflow_status WHERE workflow_uuid=? AND status='PENDING'",
                (workflow_uuid,),
            )
            conn.commit()
    except sqlite3.OperationalError:
        return  # OWN: locked / busy → silent, leave orphan for DBOS to handle
    except sqlite3.DatabaseError:
        raise  # DEFER: schema corruption → run.py:132 → E_RUNNER


def _maybe_evict_on_retry(workflow_uuid: str) -> None:
    """Pre-empt DBOS cache replay for prior failed-terminal workflow outputs.

    Inspects workflow_status[workflow_uuid] for status=SUCCESS rows whose
    deserialized output carries a truthy error_code (GH299: failed-terminal
    gate — ANY error_code evicts, not just _EVICT_ON_RETRY_CODES membership).
    On match: emit dbos_cache_evicted telemetry BEFORE DELETE (AC4 ordering).
    FK ON DELETE CASCADE handles operation_outputs automatically.

    Error taxonomy per §4 (5F097D10):
    - sqlite3.OperationalError (locked/busy): OWN → silent return, let DBOS replay.
    - sqlite3.DatabaseError (schema corruption): DEFER → re-raise → run.py:132 E_RUNNER.
    - DBOSDefaultSerializer.deserialize raises: OWN → silent return, leave row alone.
    - Output non-dict: OWN → silent return.
    """
    from dbos._serialization import DBOSDefaultSerializer  # local import — DBOS upstream decoder
    from telemetry_ctx import emit_safe  # CORRECTED §1d cite-verify miss: emit_safe lives in engine_py/telemetry_ctx.py:44

    target = _resolve_db_path()
    if not target.exists():
        return
    try:
        with sqlite3.connect(str(target), timeout=30.0) as conn:
            row = conn.execute(
                "SELECT status, output FROM workflow_status WHERE workflow_uuid=? "
                "AND status IN ('SUCCESS','ERROR')",
                (workflow_uuid,),
            ).fetchone()
            if not row:
                return
            status, output = row

            if status == "ERROR":
                # GH603 §2.2: ERROR-row output is a serialized exception, NOT a
                # dict — do not deserialize it. Evict unconditionally.
                emit_safe("dbos_cache_evicted", {
                    "workflow_uuid": workflow_uuid,
                    "error_code": None,
                    "prior_recoverable": None,
                    "evict_reason": "error_row",
                })
                conn.execute("DELETE FROM operation_outputs WHERE workflow_uuid=?", (workflow_uuid,))
                conn.execute("DELETE FROM workflow_status WHERE workflow_uuid=?", (workflow_uuid,))
                conn.commit()
                return

            if output is None:
                return
            try:
                output_obj = DBOSDefaultSerializer.deserialize(output)
            except Exception:
                return  # OWN: malformed output → leave row alone, let DBOS replay
            if not isinstance(output_obj, dict):
                return
            error_code = output_obj.get("error_code")
            if not error_code:
                return
            evict_reason = "evict_code" if error_code in _EVICT_ON_RETRY_CODES else "failed_terminal"
            emit_safe("dbos_cache_evicted", {
                "workflow_uuid": workflow_uuid,
                "error_code": error_code,
                "prior_recoverable": output_obj.get("recoverable"),
                "evict_reason": evict_reason,
            })
            # FK CASCADE does not fire without PRAGMA foreign_keys=ON — explicit child DELETE first.
            conn.execute("DELETE FROM operation_outputs WHERE workflow_uuid=?", (workflow_uuid,))
            conn.execute("DELETE FROM workflow_status WHERE workflow_uuid=?", (workflow_uuid,))
            conn.commit()
    except sqlite3.OperationalError:
        return  # OWN: locked / busy → silent, let DBOS replay
    except sqlite3.DatabaseError:
        raise  # DEFER: schema corruption → run.py:132 → E_RUNNER


def _maybe_emit_durable_resume(workflow_uuid: str) -> None:
    """GH299: telemetry-only helper — emits dbos_durable_resume when a prior
    workflow_status row survives for workflow_uuid (durable-resume signal).

    §1n taxonomy: telemetry-only helper — ALL exceptions OWN → silent return.
    Event emission must never break execution; no DEFER branch.
    """
    try:
        from telemetry_ctx import emit_safe

        target = _resolve_db_path()
        if not target.exists():
            return
        with sqlite3.connect(str(target), timeout=30.0) as conn:
            row = conn.execute(
                "SELECT status FROM workflow_status WHERE workflow_uuid=?",
                (workflow_uuid,),
            ).fetchone()
            if not row:
                return
            prior_status = row[0]
            op_outputs_count = conn.execute(
                "SELECT COUNT(*) FROM operation_outputs WHERE workflow_uuid=?",
                (workflow_uuid,),
            ).fetchone()[0]
        emit_safe("dbos_durable_resume", {
            "workflow_uuid": workflow_uuid,
            "prior_status": prior_status,
            "op_outputs_count": op_outputs_count,
        })
    except Exception:
        return  # OWN-all: telemetry-only helper must never raise (§1n)


def hard_exit(rc: int) -> None:
    """GH299 / OFI 403d6484: hard-exit run.py's __main__ block, skipping the
    non-daemon dbos-executor thread join at interpreter shutdown.

    Env `HAL_RUNPY_HARD_EXIT=0` escape → sys.exit(rc) (used by in-process test
    callers that must not kill the pytest process). Default → flush
    stdout/stderr (best-effort; a flush failure must not block exit), then
    os._exit(rc), which skips Py_Finalize's non-daemon-thread join.
    """
    import config_provider
    if not config_provider.get_config().gate_enabled("HAL_RUNPY_HARD_EXIT"):
        sys.exit(rc)
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    os._exit(rc)


def execute_durable_workflow(
    workflow_name: str,
    context_dict: dict[str, Any],
    run_id: str,
    event_log_path: str | None = None,
) -> dict[str, Any]:
    """Public entry point: pre-flight pickle check + SetWorkflowID + invoke wrapper.

    R-BLOCKER #2: SetWorkflowID MUST be in caller frame, not wrapper body.
    R-BLOCKER #3: _assert_pickle_clean fires BEFORE DBOS opens workflow state
                  → no spurious workflow_status row on adversarial input.
    D3F9A975: per-phase segregation via workflow_uuid = f"{run_id}__{workflow_name}"
    941B33FC R1: ctx-hash suffix added: workflow_uuid = f"{run_id}__{workflow_name}__{sha8}"
              so retry with corrected input gets a fresh key (846bd1e2 regression fix).
    """
    from lib.observability.emit_resolver import emit_resolver_resolved
    # A5: _assert_pickle_clean fires BEFORE _ctx_cfg_sha8 so malformed ctx
    # fails fast on pickle, not on json.dumps.
    _assert_pickle_clean(context_dict)
    # 941B33FC R1: extend workflow_uuid with 8-hex ctx-hash suffix.
    # GH612: produced by the §1g single-source `phase_key` — byte-identical string.
    workflow_uuid = phase_key(run_id, workflow_name, context_dict)
    emit_resolver_resolved(
        "execute_durable_workflow",
        "phase_segregated_uuid",
        workflow_uuid,
        {"run_id": run_id, "workflow_name": workflow_name},
    )
    # GH612 ORDERING INVARIANT (load-bearing): the native return MUST precede the
    # three DBOS pre-flight helpers below — each opens the sqlite store (and
    # _evict_orphan_pending DELETEs PENDING rows). Branching after them would
    # re-couple `native` to the DBOS store and re-open #275/#484/#748.
    if resolve_durable_backend(event_log_path=event_log_path, run_id=run_id) == "native":
        return execute_native_workflow(
            workflow_name, context_dict, run_id, event_log_path, workflow_uuid
        )
    if DBOS is None:
        raise E_DBOS_SCHEMA_INIT_FAILED(
            f"durable backend 'dbos' selected but the dbos package is not installed: "
            f"{_DBOS_IMPORT_ERROR}. Suggested: pip install with the [dbos] extra, "
            f"or unset HAL_ENGINE_DURABLE_BACKEND."
        )
    _evict_orphan_pending(workflow_uuid)  # GH748 (B): clear mid-kill orphan
    _maybe_evict_on_retry(workflow_uuid)  # GH299: evict on any failed-terminal error_code
    _maybe_emit_durable_resume(workflow_uuid)  # GH299: durable-resume telemetry
    with SetWorkflowID(workflow_uuid):
        return _dbos_execute_wrapper(workflow_name, context_dict, run_id, event_log_path)
