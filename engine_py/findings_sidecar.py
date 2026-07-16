"""GH636 — findings-thread sidecar: survives DBOS operation_outputs ERROR-row evict.

Persists `structured_findings` alongside the scratchpad so a cycle>=2
`_build_spec_prompt` can recover the thread even when the engine's retry hook
no longer carries it in `_prev` (the prior step's DBOS operation_outputs row
was DELETEd on ERROR-retry).

See issue GH636 (spec id 0C39F486) for the frozen design.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

SIDECAR_RELNAME = ".findings-thread.json"


def persist_findings_thread(scratchpad: Path, structured_findings: list, *, cycle: int) -> Path | None:
    if not structured_findings or not isinstance(structured_findings, list):
        return None
    try:
        scratchpad = Path(scratchpad)
        dest = scratchpad / SIDECAR_RELNAME
        payload = json.dumps({"structured_findings": structured_findings, "cycle": cycle})
        tmp = scratchpad / f"{SIDECAR_RELNAME}.tmp"
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, dest)
        return dest
    except (OSError, TypeError, ValueError):
        return None


def load_findings_thread(scratchpad: Path) -> list | None:
    try:
        src = Path(scratchpad) / SIDECAR_RELNAME
        data = json.loads(src.read_text(encoding="utf-8"))
        sf = data["structured_findings"]
        if isinstance(sf, list) and sf:
            return sf
        return None
    except (OSError, TypeError, ValueError, KeyError, json.JSONDecodeError):
        return None
