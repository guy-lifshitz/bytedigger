"""Engine-scoped telemetry context (decree 2026-04-26, category A).

Module-level slot the engine pushes/pops around each step so
``invoke_llm_subprocess`` can emit subprocess_spawned/exited events
without changing every phase call site signature.

Constraint (decree HARD): WorkflowContext stays frozen and unchanged.
This is the only escape hatch — a thread-local single slot — concurrent
threads each see their own context.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any


@dataclass
class _RunCtx:
    event_log: Any
    run_id: str
    step_name: str
    phase: str | None
    tier: str | None = None
    cycle: int = 1


_local = threading.local()

# GH497 D3: module-level (NOT thread-local) slot for the freshly-minted
# invocation run_id. Must survive into DBOS durable-resume replay, which
# happens in a worker thread distinct from the thread that minted the id —
# a threading.local slot would not be visible there (Opus advisory A2).
_invocation_run_id: str | None = None


def set_invocation_run_id(run_id: str | None) -> None:
    """Engine call: record the freshly-minted invocation run_id (module-global)."""
    global _invocation_run_id
    _invocation_run_id = run_id


def get_invocation_run_id() -> str | None:
    """Reader: consult the process-local invocation run_id slot."""
    return _invocation_run_id


def set_current_run(
    *, event_log: Any, run_id: str, step_name: str, phase: str | None = None, tier: str | None = None,
    cycle: int = 1,
) -> None:
    """Engine call: set the active run context for downstream telemetry."""
    _local.current = _RunCtx(
        event_log=event_log, run_id=run_id, step_name=step_name, phase=phase, tier=tier, cycle=cycle,
    )


def set_current_run_from(prev: _RunCtx, *, step_name: str) -> None:
    """Re-push *prev*'s run context under a new step_name, preserving ALL other
    fields (event_log, run_id, phase, tier, cycle). Single seam for retry re-sets so a
    future _RunCtx field cannot be silently dropped at copy sites (GH375)."""
    _local.current = _RunCtx(
        event_log=prev.event_log, run_id=prev.run_id,
        step_name=step_name, phase=prev.phase, tier=prev.tier, cycle=prev.cycle,
    )


def clear_current_run() -> None:
    """Engine call: clear active run context (after step completes)."""
    _local.current = None


def get_current_run() -> _RunCtx | None:
    """Reader: helpers consult this to decide whether to emit telemetry."""
    return getattr(_local, "current", None)


def emit_safe(event_type: str, payload: dict) -> None:
    """Public module-level telemetry helper (E843349F).
    Appends event to the active run context's event_log; no-op if no run is set.
    Consumers import this via attribute lookup (import telemetry_ctx;
    telemetry_ctx.emit_safe(...)) so monkeypatching the module attribute works."""
    run_ctx = get_current_run()
    if run_ctx is None or run_ctx.event_log is None:
        return
    try:
        run_ctx.event_log.append(event_type, payload, run_ctx.run_id)
    except Exception:  # noqa: BLE001
        pass
