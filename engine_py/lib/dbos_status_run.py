"""DBOS per-run status reporter for engine_py/run.py --status <run-id> (Ship #2b).

Read-only over the workflow_status table; no DBOS framework launch.
Schema source: dbos_setup.py:14 _DBOS_DB_PATH constant.
"""
from __future__ import annotations
import json
import sqlite3
from pathlib import Path
from typing import Any

from lib.dbos_setup import _resolve_db_path          # defined here
from lib.phase_sentinel import _PHASE_UUID_SEP       # §1g canonical source (GH788)


def _query_status_rows(db_path: Path, run_id: str) -> list[tuple[str, str, int, int]]:
    """Return (workflow_uuid, status, created_at, updated_at) rows for run_id.

    Empty list if DB or table missing, or no rows match. Filters out legacy
    rows lacking the `__` separator (§1l L5/L6 boundary).
    """
    if not db_path.exists():
        return []
    try:
        with sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5.0) as conn:
            cur = conn.execute(
                "SELECT workflow_uuid, status, created_at, updated_at "
                "FROM workflow_status WHERE workflow_uuid LIKE ? "
                "ORDER BY created_at ASC",
                (f"{run_id}{_PHASE_UUID_SEP}%",),
            )
            return [(r[0], r[1], int(r[2]), int(r[3])) for r in cur.fetchall()
                    if _PHASE_UUID_SEP in r[0]]
    except sqlite3.OperationalError:
        # OWN: table missing → empty. DEFER: any other error → propagate.
        return []


def _format_tsv(rows: list[tuple[str, str, int, int]]) -> str:
    out = ["PHASE\tSTATUS\tCREATED_MS\tUPDATED_MS"]
    for uuid, status, created, updated in rows:
        _, _, phase = uuid.partition(_PHASE_UUID_SEP)
        out.append(f"{phase}\t{status}\t{created}\t{updated}")
    return "\n".join(out) + "\n"


def _format_json(run_id: str, rows: list[tuple[str, str, int, int]]) -> str:
    phases: list[dict[str, Any]] = []
    counts = {"success": 0, "error": 0, "pending": 0}
    for uuid, status, created, updated in rows:
        _, _, phase = uuid.partition(_PHASE_UUID_SEP)
        phases.append({
            "phase": phase, "status": status,
            "created_ms": created, "updated_ms": updated,
        })
        if status == "SUCCESS":
            counts["success"] += 1
        elif status == "ERROR":
            counts["error"] += 1
        elif status == "PENDING":
            counts["pending"] += 1
    payload = {
        "run_id": run_id, "phases": phases,
        "summary": {"total": len(phases), **counts},
    }
    return json.dumps(payload, separators=(",", ":")) + "\n"


def status_run(run_id: str, db_path: Path | None = None, *, as_json: bool = False) -> tuple[str, str, int]:
    """Public entrypoint. Returns (stdout, stderr, exit_code) tuple. Does NOT print."""
    target = db_path or _resolve_db_path()
    rows = _query_status_rows(target, run_id)
    if not rows:
        return ("", f"E_RUN_NOT_FOUND: no rows found for run_id={run_id}\n", 2)
    stdout = _format_json(run_id, rows) if as_json else _format_tsv(rows)
    return (stdout, "", 0)
