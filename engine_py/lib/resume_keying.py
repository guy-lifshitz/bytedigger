"""Single-source resume-sentinel key builder (chokepoint 7E274B85 / parent 4C03CCED).

Folds run_id into the durable-resume sentinel filename so a NEW build run cannot
replay a prior run's cached step result (stale-replay-on-run_id class). run_id is
STABLE across durable-resume re-entry (DBOS keys on it too — see spec §1.5), so
keying on it is resume-safe. None/empty run_id degrades to a constant sentinel.
"""
from __future__ import annotations

_NO_RUN = "norun"


def resume_sentinel_name(
    step_name: str,
    cycle: int,
    run_id: "str | None",
    ctx_hash: "str | None" = None,
    workflow_name: "str | None" = None,
) -> str:
    """Build the durable-resume sentinel filename. run_id partitions runs.

    ``ctx_hash`` (GH443 part 3 §2.1), when a non-empty str, appends a truncated
    ``_h<hash[:12]>`` suffix so a rerun with mutated ctx (task/decision_doc)
    cannot serve a stale cached artifact. ``workflow_name`` (GH752), when
    truthy, prefixes the filename to prevent cross-workflow sentinel key
    collisions. ``None`` (default, for both) preserves the exact legacy
    filename (backward compat).
    """
    rid = run_id or _NO_RUN
    prefix = f"{workflow_name}__" if workflow_name else ""
    if ctx_hash:
        return f"{prefix}{step_name}_done_c{cycle}_r{rid}_h{ctx_hash[:12]}.json"
    return f"{prefix}{step_name}_done_c{cycle}_r{rid}.json"
