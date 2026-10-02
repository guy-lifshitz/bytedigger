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


def resume_sentinel_glob(
    step_name: str,
    cycle: "int | str",
    run_id: "str | None",
    workflow_name: "str | None" = None,
    *,
    hashed: bool,
) -> str:
    """bd#92: glob matching names built by ``resume_sentinel_name`` for the same
    arguments. ``cycle`` is an int or ``"*"``; ``hashed`` selects the
    ``_h<hash>`` variant (any hash). Built from the name builder with
    placeholders (cycle ``-1``, 12-char hash) so there is one name source.
    Step/workflow names containing the anchored placeholder tokens are out of
    contract."""
    name = resume_sentinel_name(step_name, -1, run_id, "h" * 12 if hashed else None, workflow_name)
    if hashed:
        tail = "_h" + "h" * 12 + ".json"
        if name.endswith(tail):
            name = name[: -len(tail)] + "_h*.json"
    token = "_c-1_r"
    idx = name.rfind(token)
    if idx >= 0:
        name = name[:idx] + "_c" + str(cycle) + "_r" + name[idx + len(token):]
    return name
