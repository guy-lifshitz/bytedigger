"""GH612 Ship A — native (dbos-free) durable phase seam.

**No `import dbos` anywhere in this module** and no import of `lib.dbos_setup`:
the dependency is one-way (`dbos_setup` imports FROM here), so Ship B can delete
`dbos_setup` without touching this file.

Single source for:
  * the phase key (`phase_key`, byte-identical to the historical inline f-string
    at `dbos_setup.py:426`),
  * the engine invoke (`execute_engine` — the body extracted verbatim from
    `_dbos_execute_wrapper`, now shared by BOTH backends),
  * the durable backend resolver (`resolve_durable_backend`),
  * the native success-only sentinel store (`load_cached_success` /
    `store_success`) and the resume-or-execute entry point
    (`execute_native_workflow`),
  * the STORE/SERVE telemetry emit seam (`_emit_sentinel_event`, GH792) —
    appends directly to the event log, independent of any bound
    `telemetry_ctx` run-context.

Success-only caching is the load-bearing decision: a store that physically cannot
hold a failure makes the sticky-ERROR class (#299, #603, #611) impossible by
construction — no evict-on-retry, no evict-orphan, no nonce-bump.

NOTE (spec §2.1 / gate MINOR-6): this module holds NO module-level cache or
singleton — the store IS the file on disk.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

# D3F9A975: workflow_uuid separator for per-phase segregation (MOVED from dbos_setup:21)
_PHASE_UUID_SEP = "__"

# 941B33FC R1: truncated SHA-256 hex length for ctx-hash suffix (MOVED from dbos_setup:24)
_CTX_HASH_LEN = 8

# §2.4 durable-backend seam
_DURABLE_BACKEND_ENV = "HAL_ENGINE_DURABLE_BACKEND"
_DURABLE_BACKENDS = ("dbos", "native")
_DEFAULT_DURABLE_BACKEND = "native"

# Statuses the native store may cache. `error` and `escalate` are NEVER cached
# (§2.2 escalate divergence) — the whole point of the ship.
_CACHEABLE_STATUSES = ("ok", "skip")

_SENTINEL_DIR_NAME = "phase-sentinels"


class E_DURABLE_BACKEND_UNKNOWN(RuntimeError):
    """§2.4 fail-closed: HAL_ENGINE_DURABLE_BACKEND holds an unknown value.

    Subclasses RuntimeError so run.py's generic `except Exception` handler DEFERs
    it to E_RUNNER (with exception_type "E_DURABLE_BACKEND_UNKNOWN"). No silent
    fallback to `dbos` — a misconfigured backend must not run the wrong durable
    layer silently.
    """


def _ctx_cfg_sha8(context_dict: dict[str, Any]) -> str:
    """Canonical 8-hex-char SHA over org_config slice for workflow_uuid keying.

    MOVED verbatim from dbos_setup.py:50 (re-exported there for its 6 consumers).

    Includes both org_config (user-input slice) and task_description if present
    (free-text drives spec content). Excludes session-id / timestamps that
    would defeat the cache.

    Per spec OQ2 resolution: org_config + task_description, hardcoded 8 chars.
    A7: use explicit None-check rather than .get(key, {}) to avoid silently
    treating None as the stored value.
    """
    cfg_raw = context_dict.get("org_config")
    cfg = cfg_raw if cfg_raw is not None else {}  # A7 — None-clarity
    task = context_dict.get("task_description", "")
    blob = json.dumps(
        {"org_config": cfg, "task_description": task},
        sort_keys=True, separators=(",", ":")  # A2 — canonicalize verbatim
    ).encode()
    return hashlib.sha256(blob).hexdigest()[:_CTX_HASH_LEN]


def phase_key(run_id: str, workflow_name: str, context_dict: dict[str, Any]) -> str:
    """§1g single-source phase-key producer.

    Byte-identical to the inline f-string `execute_durable_workflow` used to build
    (dbos_setup.py:426) — `run-phase.ts:57` correlation, `dbos_list_runs` /
    `dbos_status_run` `partition(_PHASE_UUID_SEP)`, and the 941B33FC uuid-format
    regression test all depend on the exact shape.
    """
    return (
        f"{run_id}{_PHASE_UUID_SEP}{workflow_name}"
        f"{_PHASE_UUID_SEP}{_ctx_cfg_sha8(context_dict)}"
    )


def resolve_durable_backend(
    *, event_log_path: str | None = None, run_id: str | None = None
) -> str:
    """§1g resolver: which durable layer executes a phase.

    unset / "" → "native" (default since GH839 B1; dbos remains the explicit fallback).
    "dbos" | "native" → that backend.
    anything else → fail-CLOSED with E_DURABLE_BACKEND_UNKNOWN (§2.4).

    Matching is EXACT and case-sensitive by design; normalizing (.strip().lower())
    would weaken the fail-closed guarantee.
    """
    from config_provider import env_opt  # local import — keeps this module import-light
    from lib.observability.emit_resolver import emit_resolver_resolved

    raw = env_opt("HAL_ENGINE_DURABLE_BACKEND")
    if not raw:
        backend, source = _DEFAULT_DURABLE_BACKEND, "default"
    elif raw in _DURABLE_BACKENDS:
        backend, source = raw, _DURABLE_BACKEND_ENV
    else:
        raise E_DURABLE_BACKEND_UNKNOWN(
            f"{_DURABLE_BACKEND_ENV}={raw!r} is not a known durable backend. "
            f"Legal values: 'native' (default) | 'dbos'. "
            f"Fix the env var — no silent fallback (§2.4 fail-closed)."
        )
    emit_resolver_resolved(
        "durable_backend",
        source,
        backend,
        event_log_path=event_log_path,
        run_id=run_id,
    )
    return backend


def execute_engine(
    workflow_name: str,
    context_dict: dict[str, Any],
    run_id: str,
    event_log_path: str | None = None,
) -> dict[str, Any]:
    """The engine invoke — ONE body, both backends.

    Extracted verbatim from `_dbos_execute_wrapper` (dbos_setup.py:196-243), which
    is now a thin `@DBOS.workflow()` shell around this function.

    Returns a pickle-clean dict (status, data, duration_ms, step_name, error,
    error_code, suggestion, recoverable). 'metadata' EXCLUDED — restored as `{}`
    by run.py via StepResult's `default_factory=dict`.

    Exceptions propagate unchanged (run.py owns the taxonomy).
    """
    # Path setup so contracts, engine, workflows resolve from within this wrapper
    HERE = Path(__file__).resolve().parent.parent  # engine_py/
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    if str(HERE / "workflows") not in sys.path:
        sys.path.insert(0, str(HERE / "workflows"))

    from contracts import WorkflowContext
    from engine import WorkflowEngine
    from event_log import EventLog  # noqa: F401 — kept; used elsewhere in module
    from event_sink import get_event_sink
    import workflows

    log = get_event_sink(event_log_path) if event_log_path else None
    eng = WorkflowEngine(event_log=log)
    workflows.register_all(eng)

    ctx = WorkflowContext(**context_dict)
    result, _ = eng.execute(workflow_name, ctx, run_id=run_id)

    return {
        "status": result.status,
        "data": result.data,
        "duration_ms": result.duration_ms,
        "step_name": result.step_name,
        "error": result.error,
        "error_code": result.error_code,
        "suggestion": result.suggestion,
        "recoverable": result.recoverable,
        # metadata intentionally excluded — restored as {} by run.py default_factory
    }


def _emit_sentinel_event(
    event_type: str,
    payload: dict[str, Any],
    run_id: str | None,
    event_log_path: str | None,
) -> None:
    """§2.1 single emit seam for both sentinel telemetry sites.

    `if not event_log_path: return` is a declared defense-in-depth branch
    (pinned by AC6b) — production never reaches it because both call sites
    already require a truthy `event_log_path` to get here.

    The broad `except Exception: return` is LOAD-BEARING (§1n OWN-all): the
    SERVE/STORE call sites have no enclosing try of their own at this seam,
    and `EventLog.append` can raise — a telemetry failure must never turn a
    green phase into E_RUNNER.
    """
    if not event_log_path:
        return
    try:
        from event_sink import get_event_sink

        get_event_sink(event_log_path).append(event_type, payload, run_id)
    except Exception:
        return


def _sentinel_path(key: str, event_log_path: str | None) -> Path | None:
    """`<event_log_dir>/phase-sentinels/<key>.json`, or None when there is no
    durable location (falsy event_log_path ⇒ no cache; mirrors the governor,
    inactive without --event-log)."""
    if not event_log_path:
        return None
    return Path(event_log_path).parent / _SENTINEL_DIR_NAME / f"{key}.json"


def load_cached_success(key: str, event_log_path: str | None) -> dict[str, Any] | None:
    """Read the sentinel for `key`; return the cached result dict, else None.

    MISS (None, never a raise) on: no path, missing file, unreadable, malformed
    JSON, NON-DICT payload (`null`, `[1,2,3]`, `"x"`), or a payload whose `status`
    is not in ("ok","skip") — defence-in-depth against a legacy/hostile file that
    plants an `error`/`escalate` result.

    The guard is a BROAD `except Exception` by contract (§2.4 / gate F2): a narrow
    (OSError, ValueError) tuple lets AttributeError escape on a non-dict payload.
    The non-dict gate precedes any `payload.get(...)` for the same reason.
    """
    try:
        sentinel = _sentinel_path(key, event_log_path)
        if sentinel is None or not sentinel.exists():
            return None
        payload = json.loads(sentinel.read_text())
        if not isinstance(payload, dict):
            return None
        if payload.get("status") not in _CACHEABLE_STATUSES:
            return None
        return payload
    except Exception:
        return None


def store_success(
    key: str,
    result: dict[str, Any],
    event_log_path: str | None,
    run_id: str | None = None,
) -> None:
    """Cache ONLY a success — no-op unless result["status"] in ("ok","skip").

    `error` AND `escalate` are never written: a store that cannot hold a failure
    has no sticky-failure class (#299/#603/#611), by construction.

    Serialization is `json.dumps(payload, default=str)` — StepResult.data is `Any`
    and the pickle store we replace was strictly more permissive than JSON.
    Write is atomic: tempfile in the sentinel's OWN directory + os.replace, with a
    try/finally that unlinks the temp on EVERY failure path (no .tmp residue, no
    cross-filesystem replace).

    NEVER raises — the guard is a BROAD `except Exception` by contract (§2.4 /
    gate F1). The cache is an optimization: losing it costs a re-execute, never a
    build. A narrow `except OSError` would turn a SUCCESSFUL phase into E_RUNNER
    (e.g. a TypeError from a non-str dict KEY, which `default=` does not cover).
    """
    try:
        if not isinstance(result, dict) or result.get("status") not in _CACHEABLE_STATUSES:
            return
        sentinel = _sentinel_path(key, event_log_path)
        if sentinel is None:
            return
        sentinel.parent.mkdir(parents=True, exist_ok=True)
        blob = json.dumps(result, default=str)

        fd, tmp_name = tempfile.mkstemp(dir=str(sentinel.parent), suffix=".tmp")
        pending: str | None = tmp_name
        try:
            with os.fdopen(fd, "w") as fh:
                fh.write(blob)
            os.replace(tmp_name, str(sentinel))
            pending = None  # committed — nothing left to unlink
        finally:
            if pending is not None:
                try:
                    os.unlink(pending)
                except OSError:
                    pass

        _emit_sentinel_event("phase_sentinel_stored", {
            "phase_key": key,
            "status": result.get("status"),
        }, run_id, event_log_path)
    except Exception:
        return


def execute_native_workflow(
    workflow_name: str,
    context_dict: dict[str, Any],
    run_id: str,
    event_log_path: str | None,
    key: str,
) -> dict[str, Any]:
    """§2.2 resume-or-execute: serve the success sentinel, else run the engine.

    A cached hit short-circuits the engine entirely (the DBOS startup-recovery
    analogue, minus the sqlite store / executor thread / exception memoization).
    Engine exceptions propagate unchanged; nothing is cached on failure.
    """
    cached = load_cached_success(key, event_log_path)
    if cached is not None:
        _emit_sentinel_event("phase_sentinel_resumed", {
            "phase_key": key,
            "status": cached["status"],
        }, run_id, event_log_path)
        return cached

    result = execute_engine(workflow_name, context_dict, run_id, event_log_path)
    store_success(key, result, event_log_path, run_id)
    return result
