"""GH636 — findings-thread sidecar: survives DBOS operation_outputs ERROR-row evict.

Persists `structured_findings` alongside the scratchpad so a cycle>=2
`_build_spec_prompt` can recover the thread even when the engine's retry hook
no longer carries it in `_prev` (the prior step's DBOS operation_outputs row
was DELETEd on ERROR-retry).

bd#92: the sidecar is keyed by (run_id, producing cycle); a reader only
accepts it for the same run and the immediately preceding cycle.

See issue GH636 (spec id 0C39F486) for the frozen design.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

SIDECAR_RELNAME = ".findings-thread.json"


def persist_findings_thread(
    scratchpad: Path, structured_findings: list, *, cycle: int, run_id: "str | None" = None,
) -> Path | None:
    if not structured_findings or not isinstance(structured_findings, list):
        return None
    try:
        scratchpad = Path(scratchpad)
        dest = scratchpad / SIDECAR_RELNAME
        payload = json.dumps({"structured_findings": structured_findings, "cycle": cycle, "run_id": run_id})
        tmp = scratchpad / f"{SIDECAR_RELNAME}.tmp"
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, dest)
        return dest
    except (OSError, TypeError, ValueError):
        return None


def findings_thread_status(
    scratchpad: Path, *, run_id: "str | None", for_cycle: int,
) -> "tuple[list | None, str | None]":
    """Single reader. Returns ``(list, None)`` or ``(None, reason)``; never raises."""
    try:
        src = Path(scratchpad) / SIDECAR_RELNAME
        data = json.loads(src.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, "absent"
    except Exception:
        return None, "absent"
    if not isinstance(data, dict):
        return None, "malformed"
    sf = data.get("structured_findings")
    cyc = data.get("cycle")
    if not isinstance(sf, list) or not sf or not isinstance(cyc, int) or isinstance(cyc, bool):
        return None, "malformed"
    if "run_id" not in data:
        return None, "legacy"
    if data["run_id"] != run_id:
        return None, "run_mismatch"
    if cyc != for_cycle - 1:
        return None, "cycle_mismatch"
    return sf, None


def load_findings_thread(scratchpad: Path, *, run_id: "str | None", for_cycle: int) -> list | None:
    try:
        return findings_thread_status(scratchpad, run_id=run_id, for_cycle=for_cycle)[0]
    except Exception:
        return None
