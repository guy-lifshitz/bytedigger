"""Execution-provenance discriminator (GH700).

Pure, host-decoupled: stdlib ``threading`` only. No env access, no
filesystem paths, no I/O, no host-repo string references, no raw ``HAL_``
env reads, no HAL imports.

INVARIANT (F6 — read before touching this file): the main-thread
discriminator below is sound ONLY while HAL invokes
``_dbos_execute_wrapper`` SYNCHRONOUSLY, on the process main thread
(lib/dbos_setup.py:392-393). DBOS.launch() submits recovered
re-executions to a background thread pool (dbos/_dbos.py:618,
``self._executor.submit(startup_recovery_thread, self, workflow_ids)``),
so a DBOS-recovery zombie always runs off-main-thread while the real
invocation always runs on it. If HAL ever dispatches the engine wrapper
via ``DBOS.start_workflow`` or a DBOS ``Queue.enqueue`` — both of which
run the AUTHORITATIVE workflow execution on ``dbos._executor`` (i.e. also
off the process main thread) — this guard would silently misclassify a
REAL terminal error as a shadow/non-authoritative event. This is the same
main-thread/DBOS-recovery-thread reality that a pre-existing sibling
predicate in ``contracts.py:584`` already relies on. Do not repurpose this
module for an async/queued dispatch path without revisiting that
assumption.
"""
from __future__ import annotations

import threading

PROVENANCE_FOREGROUND = "foreground"
PROVENANCE_DBOS_RECOVERY = "dbos_recovery"
SHADOW_EVENT_TYPE = "engine_shadow_emit"


def execution_provenance() -> str:
    """foreground iff on the process main thread; else dbos_recovery.

    DBOS.launch() submits startup_recovery_thread to a thread pool
    (dbos/_dbos.py:618), so every recovered re-execution runs off-main-thread,
    while run.py's real invocation always runs on the main thread.
    """
    return PROVENANCE_FOREGROUND if threading.current_thread() is threading.main_thread() else PROVENANCE_DBOS_RECOVERY


def is_authoritative_execution() -> bool:
    return execution_provenance() == PROVENANCE_FOREGROUND


def shadow_event_name(original_event_type: str) -> str:
    """Mangle so a shadow line can NEVER contain the quoted literal
    "workflow_finished" / "workflow_started" that shell consumers grep for."""
    return original_event_type.replace("workflow_", "wf_")
