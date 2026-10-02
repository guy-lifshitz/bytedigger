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

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from bytedigger_engine import telemetry_ctx  # noqa: E402
from bytedigger_engine.lib import llm_cost  # noqa: E402

_ROLLUP_EVENT_TYPES = ("subprocess_exited", "runner_result_consumed")
_OBSERVED_EVENT = "llm_cost_observed"  # bd#167
_DIVERGENCE_EVENT = "llm_cost_divergence"  # bd#167


def _empty_bucket() -> dict[str, Any]:
    return {"calls": 0, "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0}


def _num(value: Any) -> int | float:
    return value if value is not None else 0


def _add_row(bucket: dict[str, Any], tokens_in: Any, tokens_out: Any, cost_usd: Any) -> None:
    bucket["calls"] += 1
    bucket["tokens_in"] += _num(tokens_in)
    bucket["tokens_out"] += _num(tokens_out)
    bucket["cost_usd"] += _num(cost_usd)


def run_cost(rows: list[dict], run_id: str) -> tuple[float, int]:
    """Known spend of ``run_id`` over already-parsed event rows, and the number
    of cost-bearing calls that reported no ``cost_usd`` (bd#85). Same events
    and fields as ``compute_cost_rollup``."""
    known, unknown = 0.0, 0
    for row in rows:
        if not isinstance(row, dict) or row.get("run_id") != run_id:
            continue
        if row.get("event_type") not in _ROLLUP_EVENT_TYPES:
            continue
        usd = (row.get("payload") or {}).get("cost_usd")
        if isinstance(usd, (int, float)):
            known += usd
        else:
            unknown += 1
    return known, unknown


def _count_or_zero(value: Any) -> int | float:
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


def _new_mode_bucket(cost_kind: str) -> dict[str, Any]:
    return {
        "calls": 0, "tokens_in": 0, "tokens_out": 0, "cache_read_tokens": 0,
        "cache_write_tokens": 0, "cache_write_1h_tokens": 0, "cost_usd": 0.0,
        "unpriced_calls": 0, "no_usage_calls": 0, "cost_kind": cost_kind,
    }


def _mode_of(payload: dict) -> str:
    mode = payload.get("billing_mode")
    return mode if mode in llm_cost.BILLING_MODES else "unknown"


def _add_observation(by_mode: dict[str, Any], payload: dict) -> dict[str, Any]:
    """bd#167: fold one ``llm_cost_observed`` payload into its billing-mode bucket.

    ``no_usage_calls`` = the backend reported no usage; ``unpriced_calls`` = usage
    reported but no price (mirrors token-cost.ts). Returns the bucket."""
    mode = _mode_of(payload)
    bucket = by_mode.setdefault(mode, _new_mode_bucket(llm_cost.cost_kind(mode)))
    bucket["calls"] += 1
    for key in ("tokens_in", "tokens_out", "cache_read_tokens", "cache_write_tokens", "cache_write_1h_tokens"):
        bucket[key] += _count_or_zero(payload.get(key))
    cost = payload.get("cost_usd")
    priced = isinstance(cost, (int, float)) and not isinstance(cost, bool)
    if priced:
        bucket["cost_usd"] += cost
    if payload.get("usage_reported") is False:
        bucket["no_usage_calls"] += 1
    elif not priced:
        bucket["unpriced_calls"] += 1
    return bucket


def _add_cost_event(rollup: dict, row: dict) -> bool:
    """bd#167: handle the two new event types; True when *row* was one of them."""
    event_type = row.get("event_type")
    if event_type == _OBSERVED_EVENT:
        payload = row.get("payload")
        _add_observation(rollup["by_billing_mode"], payload if isinstance(payload, dict) else {})
        return True
    if event_type == _DIVERGENCE_EVENT:
        rollup["cost_divergences"] += 1
        return True
    return False


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
        # bd#167: built from llm_cost_observed only; the flat fields above ignore it.
        "by_billing_mode": {},
        "cost_divergences": 0,
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
        if _add_cost_event(rollup, row):
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


# ---------------------------------------------------------------------------
# bd#167: A/B ledger export (the document ab-bench.ts reads from its ledger command)
# ---------------------------------------------------------------------------

_FRACTION_RE = re.compile(r"\.(\d+)")


def _parse_instant(text: Any) -> datetime:
    """Parse an ISO-8601 instant to an aware UTC datetime; raises ValueError.

    Python 3.9/3.10 ``fromisoformat`` rejects a trailing ``Z`` and any fraction
    that is not 3 or 6 digits, so both are normalised here (requires-python
    >=3.9). A naive value (including a date-only bound) is read as UTC."""
    if not isinstance(text, str) or not text.strip():
        raise ValueError(f"not an ISO-8601 timestamp: {text!r}")
    norm = text.strip()
    if norm[-1] in "zZ":
        norm = norm[:-1] + "+00:00"
    norm = _FRACTION_RE.sub(lambda m: "." + (m.group(1) + "000000")[:6], norm, count=1)
    parsed = datetime.fromisoformat(norm)
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def _in_window(row: dict, since: "datetime | None", until: "datetime | None") -> bool:
    """Inclusive time filter on the row ``ts``; a missing/unparseable ts is out under any bound."""
    if since is None and until is None:
        return True
    try:
        ts = _parse_instant(row.get("ts"))
    except ValueError:
        return False
    return (since is None or ts >= since) and (until is None or ts <= until)


def _read_rows(path: Path) -> list[dict]:
    """Parsed JSONL rows (dicts only); a missing/unreadable file yields []."""
    try:
        lines = path.read_text().splitlines()
    except Exception:
        return []
    rows: list[dict] = []
    for line in lines:
        try:
            row = json.loads(line) if line.strip() else None
        except Exception:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


_USD_TOTAL_BY_KIND = {"metered": "metered_usd", "notional": "notional_usd", "unknown": "unknown_usd"}


def _ledger_totals(by_mode: dict[str, Any]) -> dict[str, Any]:
    """Sum the per-mode buckets into the document's ``totals``.

    ``usd`` = metered_usd + notional_usd + unknown_usd = the metered-equivalent
    cost of every priced call (subscription calls are notional)."""
    totals: dict[str, Any] = {
        "usd": 0.0, "metered_usd": 0.0, "notional_usd": 0.0, "unknown_usd": 0.0,
        "unpriced": 0, "no_usage": 0, "input": 0, "output": 0,
        "cache_read": 0, "cache_write": 0, "cache_write_1h": 0, "records": 0,
    }
    for bucket in by_mode.values():
        totals[_USD_TOTAL_BY_KIND[bucket["cost_kind"]]] += bucket["cost_usd"]
        totals["unpriced"] += bucket["unpriced_calls"]
        totals["no_usage"] += bucket["no_usage_calls"]
        totals["input"] += bucket["tokens_in"]
        totals["output"] += bucket["tokens_out"]
        totals["cache_read"] += bucket["cache_read_tokens"]
        totals["cache_write"] += bucket["cache_write_tokens"]
        totals["cache_write_1h"] += bucket["cache_write_1h_tokens"]
        totals["records"] += bucket["calls"]
    totals["usd"] = totals["metered_usd"] + totals["notional_usd"] + totals["unknown_usd"]
    return totals


def export_ab_ledger(
    events_path: Path | str,
    run_id: "str | None" = None,
    since: "str | None" = None,
    until: "str | None" = None,
) -> dict:
    """The A/B ledger document: ``{schema, marker, totals, by_billing_mode}``.

    Built from ``llm_cost_observed`` rows only. ``run_id=None`` takes every run;
    ``since``/``until`` (ISO, inclusive, compared as instants) filter by the row
    ``ts``. ``marker``: OK (>=1 record), EMPTY_CORPUS (file missing/empty),
    NO_RECORDS_FOR_RUN otherwise. An unparseable bound raises ValueError.
    """
    lo = _parse_instant(since) if since is not None else None
    hi = _parse_instant(until) if until is not None else None
    rows = _read_rows(Path(events_path))
    by_mode: dict[str, Any] = {}
    for row in rows:
        if row.get("event_type") != _OBSERVED_EVENT:
            continue
        if run_id is not None and row.get("run_id") != run_id:
            continue
        if not _in_window(row, lo, hi):
            continue
        payload = row.get("payload")
        _add_observation(by_mode, payload if isinstance(payload, dict) else {})
    totals = _ledger_totals(by_mode)
    if totals["records"] > 0:
        marker = "OK"
    else:
        marker = "NO_RECORDS_FOR_RUN" if rows else "EMPTY_CORPUS"
    return {"schema": 1, "marker": marker, "totals": totals, "by_billing_mode": by_mode}


def main(argv: "list[str] | None" = None) -> int:
    """CLI: print the A/B ledger document; exit 0 OK, 3 otherwise, 2 on a usage error.

    Unknown arguments (the harness also passes --branch ...) are ignored."""
    parser = argparse.ArgumentParser(prog="cost_rollup", allow_abbrev=False)
    parser.add_argument("--events", required=True)
    parser.add_argument("--run-id", dest="run_id", default=None)
    parser.add_argument("--since", default=None)
    parser.add_argument("--until", default=None)
    parser.add_argument("--json", action="store_true")
    args, _unknown = parser.parse_known_args(argv)
    try:
        doc = export_ab_ledger(args.events, args.run_id, args.since, args.until)
    except ValueError as exc:
        print(f"cost_rollup: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(doc, sort_keys=True))
    return 0 if doc["marker"] == "OK" else 3


if __name__ == "__main__":
    sys.exit(main())
