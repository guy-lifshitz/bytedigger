"""Incident ledger — one structured record per step FAIL/escalate (GH923 lot 1).

Pattern-clone of reject_log.py (fail-safe JSONL append, call-time env seam,
hal-root/foreign split).

Schema (one JSON object per line):
    {
        "ts":            "<ISO-8601 UTC ending Z>",
        "run_id":        "...",
        "phase":         "<workflow.name>",
        "step_name":     "...",
        "cycle":         1,
        "status":        "error|escalate",
        "error_code":    "E_...|null",
        "error":         "<=500 chars|null>",
        "wall_ms":       1234,
        "tokens":        {"calls": N, "tokens_in": N, "tokens_out": N, "cost_usd": F} | null,
        "classification": "unknown",
        "workaround":    null
    }

classification is ALWAYS written as "unknown"; workaround ALWAYS null (a
later lot mutates them via a CLI). No consumer reads this file yet (§1o).

Fail-safe: the entire body of emit_incident is wrapped in try/except so this
helper can NEVER raise into the build pipeline.
"""
from __future__ import annotations

import datetime
import json
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent))
from config_provider import hal_root as _hal_root_fn, path as _path_fn, incident_log_relpath, foreign_state_dirname as _foreign_state_dirname_fn  # noqa: E402

_LINE_LIMIT_BYTES = 4096
_ERROR_TRUNC_LEN = 500


def default_incident_log_path() -> Path:
    """Resolve the default incident-ledger path lazily based on cwd.

    Dogfood (cwd inside install root) -> canonical central incident log;
    foreign project -> cwd-relative foreign_state_dirname()/incidents.jsonl
    (no state pollution). HOME/cwd unresolvable -> treat cwd as foreign
    (safe default).
    """
    try:
        hal_dir = _hal_root_fn()
        cwd = Path.cwd().resolve()
        if cwd == hal_dir or cwd.is_relative_to(hal_dir):
            return hal_dir / incident_log_relpath()
    except (OSError, ValueError, RuntimeError):
        pass
    return Path.cwd() / _foreign_state_dirname_fn() / "incidents.jsonl"


def resolve_incident_log_path() -> Path:
    """Call-time env-seam resolver for the incident-ledger path.

    Returns Path(BD_INCIDENT_LOG) if the env var is set and non-empty, else
    default_incident_log_path(). Resolved at CALL time (matches
    resolve_reject_log_path's GH497-D1 parity).
    """
    return _path_fn("BD_INCIDENT_LOG", default_incident_log_path())


def emit_incident(
    *,
    run_id: str | None,
    phase: str,
    step_name: str,
    cycle: int,
    status: str,
    error_code: str | None,
    error: str | None,
    wall_ms: int,
    events_path: Path | str | None = None,
    path: Path | None = None,
) -> None:
    """Append one incident record to the JSONL ledger.

    Fail-safe: any exception is silently swallowed — this helper must NEVER
    raise into the build pipeline.

    run_id falsy (None or "") -> the write is suppressed entirely, before
    any rollup work.
    """
    try:
        if not run_id:
            return

        tokens: dict[str, Any] | None = None
        if events_path is not None:
            try:
                from lib.cost_rollup import compute_cost_rollup  # noqa: PLC0415

                rollup = compute_cost_rollup(events_path, run_id)
                tokens = rollup["by_phase"].get(phase)
            except Exception:  # noqa: BLE001
                tokens = None

        truncated_error = error if error is None else error[:_ERROR_TRUNC_LEN]

        ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        record = {
            "ts": ts,
            "run_id": run_id,
            "phase": phase,
            "step_name": step_name,
            "cycle": cycle,
            "status": status,
            "error_code": error_code,
            "error": truncated_error,
            "wall_ms": wall_ms,
            "tokens": tokens,
            "classification": "unknown",
            "workaround": None,
        }
        line = json.dumps(record, separators=(",", ":"), default=str) + "\n"
        encoded = line.encode("utf-8")

        if len(encoded) > _LINE_LIMIT_BYTES:
            return

        resolved_path: Path = path if path is not None else resolve_incident_log_path()

        parent = Path(resolved_path).parent
        os.makedirs(parent, exist_ok=True)

        fd = os.open(str(resolved_path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        os.write(fd, encoded)
        os.close(fd)
    except Exception:  # noqa: BLE001
        return
