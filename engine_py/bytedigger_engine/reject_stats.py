from __future__ import annotations

from datetime import datetime, timedelta

LINTABLE_PATTERNS = ["CITE", "SCOPE", "COVERAGE", "MISSING", "FIELD", "GROUND", "IMPORT"]

_TS_FMT = "%Y-%m-%dT%H:%M:%SZ"


def classify_reason(reason_code: str) -> str:
    """Return 'lintable' if reason_code contains any LINTABLE_PATTERNS, else 'needs-prompt'."""
    upper = reason_code.upper()
    for pattern in LINTABLE_PATTERNS:
        if pattern in upper:
            return "lintable"
    return "needs-prompt"


def aggregate(rows: list[dict], window_days: int | None = None) -> list[dict]:
    """Aggregate reject-reason rows into ranked stats groups.

    Groups by (reason_code, phase, tuple(sorted(axes))).
    Ranks output by count DESC, tiebreak (reason_code, phase) ASC.
    """
    if not rows:
        return []

    # Window filter: anchored on MAX ts in rows (not wall-clock)
    if window_days is not None:
        max_ts = max(r["ts"] for r in rows)
        max_dt = datetime.strptime(max_ts, _TS_FMT)
        cutoff = max_dt - timedelta(days=window_days)
        rows = [r for r in rows if datetime.strptime(r["ts"], _TS_FMT) >= cutoff]
        if not rows:
            return []

    # Group preserving first-seen input order
    groups: dict[tuple, list[dict]] = {}
    order: list[tuple] = []
    for row in rows:
        key = (row["reason_code"], row["phase"], tuple(sorted(row.get("axes") or [])))
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(row)

    result: list[dict] = []
    for key in order:
        group_rows = groups[key]
        reason_code, phase, axes_tuple = key
        axes = list(axes_tuple)
        count = len(group_rows)
        build_spread = len({r["build_id"] for r in group_rows if r.get("build_id") is not None})
        example_detail = group_rows[0].get("detail")
        ts_values = [r["ts"] for r in group_rows]
        first_seen = min(ts_values)
        last_seen = max(ts_values)
        result.append(
            {
                "reason_code": reason_code,
                "phase": phase,
                "axes": axes,
                "count": count,
                "build_spread": build_spread,
                "example_detail": example_detail,
                "first_seen": first_seen,
                "last_seen": last_seen,
                "classify": classify_reason(reason_code),
            }
        )

    result.sort(key=lambda r: (-r["count"], r["reason_code"], r["phase"]))
    return result


_INFORMATIVE_PREFLIGHT = ("fresh", "red")
_LADDER_HEADS_MAX = 3


def ladder_table(rows: list) -> list[dict]:
    """bd#218 s1: phase-5 validation rejects grouped by detail.preflight (status, red_step).

    Only fresh and red groups are informative; every other status (and 'none' for rows
    without the key) is no evidence either way. Malformed rows are skipped; never raises.
    """
    groups: dict[tuple[str, str | None], dict] = {}
    for row in rows:
        try:
            if not isinstance(row, dict) or row.get("phase") != "phase_5_implement":
                continue
            reason = row.get("reason_code")
            if not isinstance(reason, str) or not reason.startswith("VALIDATION_"):
                continue
            detail = row.get("detail")
            if not isinstance(detail, dict):
                continue
            pf = detail.get("preflight")
            if pf is None:
                status: str = "none"
                red_step: str | None = None
            elif isinstance(pf, dict) and isinstance(pf.get("status"), str):
                status = pf["status"]
                step = pf.get("red_step")
                red_step = step if isinstance(step, str) else None
            else:
                continue
            heads = detail.get("findings_head")
            group = groups.setdefault((status, red_step), {"rejects": 0, "heads": []})
            group["rejects"] += 1
            if isinstance(heads, list):
                for head in heads:
                    if isinstance(head, str) and head not in group["heads"]:
                        group["heads"].append(head)
        except Exception:  # noqa: BLE001 -- malformed row: skipped
            continue
    ordered = sorted(groups.items(), key=lambda kv: (-kv[1]["rejects"], kv[0][0], kv[0][1] or ""))
    return [
        {
            "status": status,
            "red_step": red_step,
            "rejects": g["rejects"],
            "informative": status in _INFORMATIVE_PREFLIGHT,
            "findings_heads": g["heads"][:_LADDER_HEADS_MAX],
        }
        for (status, red_step), g in ordered
    ]
