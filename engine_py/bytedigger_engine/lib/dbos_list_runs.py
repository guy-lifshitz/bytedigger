"""DBOS run-list reporter for engine_py/run.py --list-runs (Ship #2a).

Read-only over the workflow_status table; no DBOS framework launch.
Schema source: dbos_setup.py:14 _DBOS_DB_PATH constant.
"""
from __future__ import annotations
import json
import sqlite3
from pathlib import Path
from typing import Any

from bytedigger_engine.lib.dbos_setup import _resolve_db_path          # defined here
from bytedigger_engine.lib.phase_sentinel import _PHASE_UUID_SEP       # §1g canonical source (GH788)


def _query_rows(db_path: Path) -> list[tuple[str, str, int]]:
    """Return (workflow_uuid, status, updated_at_ms) rows. Empty if DB or table missing."""
    if not db_path.exists():
        return []
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5.0) as conn:
            cur = conn.execute(
                "SELECT workflow_uuid, status, updated_at FROM workflow_status"
            )
            return [(r[0], r[1], r[2]) for r in cur.fetchall()]
    except sqlite3.OperationalError:
        # OWN: table missing → treat as empty. DEFER: any other error → propagate.
        return []


def _group_by_run(rows: list[tuple[str, str, int]]) -> list[dict[str, Any]]:
    """Group rows by run_id (workflow_uuid prefix before __). Skip legacy rows w/o __."""
    by_run: dict[str, dict[str, Any]] = {}
    for uuid, status, updated_at in rows:
        if _PHASE_UUID_SEP not in uuid:
            continue  # §1l L5 legacy-row exclusion
        run_id, _, phase = uuid.partition(_PHASE_UUID_SEP)
        rec = by_run.setdefault(run_id, {
            "run_id": run_id,
            "phases": 0, "success": 0, "error": 0, "pending": 0,
            "last_updated_ms": 0, "workflows": [],
        })
        rec["phases"] += 1
        if status == "SUCCESS":
            rec["success"] += 1
        elif status == "ERROR":
            rec["error"] += 1
        elif status == "PENDING":
            rec["pending"] += 1
        if updated_at > rec["last_updated_ms"]:
            rec["last_updated_ms"] = updated_at
        rec["workflows"].append(phase)
    # Sort workflows alphabetically within each run for determinism
    for rec in by_run.values():
        rec["workflows"].sort()
    # Sort runs by last_updated_ms DESC
    return sorted(by_run.values(), key=lambda r: r["last_updated_ms"], reverse=True)


def _format_tsv(records: list[dict[str, Any]]) -> str:
    out = ["RUN_ID\tPHASES\tSUCCESS\tERROR\tPENDING\tLAST_UPDATED_MS\tWORKFLOWS"]
    for r in records:
        out.append(
            f"{r['run_id']}\t{r['phases']}\t{r['success']}\t{r['error']}\t"
            f"{r['pending']}\t{r['last_updated_ms']}\t{','.join(r['workflows'])}"
        )
    return "\n".join(out) + "\n"


def _format_json(records: list[dict[str, Any]]) -> str:
    return json.dumps(records, separators=(",", ":")) + "\n"


def list_runs(db_path: Path | None = None, *, as_json: bool = False, limit: int = 100) -> str:
    """Public entrypoint. Returns formatted stdout (NOT printed)."""
    target = db_path or _resolve_db_path()
    rows = _query_rows(target)
    records = _group_by_run(rows)
    if limit > 0:
        records = records[:limit]
    return _format_json(records) if as_json else _format_tsv(records)
