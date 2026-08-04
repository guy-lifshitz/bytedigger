"""cost_rollup — per-run token-cost aggregation (GH452).

Public API:
    compute_cost_rollup(events_path, run_id) -> dict

Pure-deterministic, no LLM, no host coupling. Reads a JSONL events file,
filters rows for a given run_id whose event_type is subprocess_exited or
runner_result_consumed, and aggregates tokens_in/tokens_out/cost_usd flat
+ by_phase + by_cycle buckets. Never raises: malformed lines are skipped,
a missing file yields a zero rollup, missing/None fields count as 0.

Parent SYSTEMATIC: audit-protocol economics-first monitoring (#452).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from bytedigger_engine import telemetry_ctx  # noqa: E402

_ROLLUP_EVENT_TYPES = ("subprocess_exited", "runner_result_consumed")


def _empty_bucket() -> dict[str, Any]:
    return {"calls": 0, "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0}


def _num(value: Any) -> int | float:
    return value if value is not None else 0


def _add_row(bucket: dict[str, Any], tokens_in: Any, tokens_out: Any, cost_usd: Any) -> None:
    bucket["calls"] += 1
    bucket["tokens_in"] += _num(tokens_in)
    bucket["tokens_out"] += _num(tokens_out)
    bucket["cost_usd"] += _num(cost_usd)


def compute_cost_rollup(events_path: Path | str, run_id: str) -> dict:
    """Aggregate token-cost telemetry for one run_id from a JSONL events file."""
    rollup: dict[str, Any] = {
        "run_id": run_id,
        "calls": 0,
        "tokens_in": 0,
        "tokens_out": 0,
        "cost_usd": 0.0,
        "by_phase": {},
        "by_cycle": {},
    }

    try:
        path = Path(events_path)
        if not path.exists():
            return rollup
        lines = path.read_text().splitlines()
    except Exception:
        return rollup

    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        if not isinstance(row, dict):
            continue
        if row.get("run_id") != run_id:
            continue
        if row.get("event_type") not in _ROLLUP_EVENT_TYPES:
            continue

        payload = row.get("payload") or {}
        tokens_in = payload.get("tokens_in")
        tokens_out = payload.get("tokens_out")
        cost_usd = payload.get("cost_usd")

        _add_row(rollup, tokens_in, tokens_out, cost_usd)

        phase = payload.get("phase") or "unattributed"
        phase_bucket = rollup["by_phase"].setdefault(phase, _empty_bucket())
        _add_row(phase_bucket, tokens_in, tokens_out, cost_usd)

        cycle_key = "unattributed" if "cycle" not in payload else str(payload.get("cycle"))
        cycle_bucket = rollup["by_cycle"].setdefault(cycle_key, _empty_bucket())
        _add_row(cycle_bucket, tokens_in, tokens_out, cost_usd)

    return rollup


def annotate_invocation(rollup: dict, rid: str) -> dict:
    """GH497 D3: stamp *rollup* with the freshly-minted invocation run_id.

    inv = telemetry_ctx.get_invocation_run_id() or rid. Sets
    rollup["invocation_run_id"]=inv and rollup["resumed"]=(inv != rid).
    Returns rollup (mutated in place, also returned for call-site chaining).
    """
    inv = telemetry_ctx.get_invocation_run_id() or rid
    rollup["invocation_run_id"] = inv
    rollup["resumed"] = (inv != rid)
    return rollup
