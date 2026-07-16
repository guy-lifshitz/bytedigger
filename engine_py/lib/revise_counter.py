"""Durable cross-invocation REVISE counter (GH443 part 3 §2.3, GH633 §2 idempotency).

Core-clean: stdlib + io_utils.atomic_write only, no HAL_* env, no host paths.
Counter file is keyed by run_id so it survives re-invocation within the same
build run; a genuinely fresh run (new run_id) starts at 0.

When ``cycle`` is supplied (idempotent path, GH633), the counter also tracks
``counted_cycles`` — the set of cycles already counted for this run_id. A
replay of an already-counted ``(run_id, cycle)`` pair returns the current
total WITHOUT bumping or writing (auto-resume safe). ``cycle=None`` preserves
the original legacy behavior exactly (unconditional bump, no counted_cycles
key persisted).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

_LIB_DIR = str(Path(__file__).resolve().parent)
if _LIB_DIR not in sys.path:
    sys.path.insert(0, _LIB_DIR)

from io_utils import atomic_write  # noqa: E402

_NO_RUN = "norun"


def bump_revise_count(
    scratchpad: "Path", run_id: "str | None", cycle: "int | None" = None
) -> int:
    """Increment and persist the durable REVISE counter for ``run_id``.

    Missing/corrupt counter file is treated as 0 (returns 1). Never raises —
    any read or write failure still returns the best-effort incremented value.

    ``cycle=None`` (legacy): unconditional bump; persists ``{"count": N}``
    only. ``cycle=<int>`` (GH633 idempotent path): a replay of a cycle
    already present in ``counted_cycles`` returns the current count without
    bumping or writing; otherwise bumps and persists
    ``{"count": N, "counted_cycles": [...]}``.
    """
    rid = run_id or _NO_RUN
    counter_dir = scratchpad / "resume"
    counter_file = counter_dir / f"spec_revise_count_r{rid}.json"

    count = 0
    counted: list = []
    try:
        data = json.loads(counter_file.read_text())
        count = data.get("count", 0)
        counted = list(data.get("counted_cycles", []))
    except (FileNotFoundError, json.JSONDecodeError, OSError, AttributeError):
        count = 0
        counted = []

    if cycle is None:
        total = count + 1
        try:
            counter_dir.mkdir(parents=True, exist_ok=True)
            atomic_write(counter_file, json.dumps({"count": total}))
        except OSError:
            pass
        return total

    if cycle in counted:
        return count

    total = count + 1
    new_counted = sorted(set(counted) | {cycle})
    try:
        counter_dir.mkdir(parents=True, exist_ok=True)
        atomic_write(
            counter_file, json.dumps({"count": total, "counted_cycles": new_counted})
        )
    except OSError:
        pass
    return total
