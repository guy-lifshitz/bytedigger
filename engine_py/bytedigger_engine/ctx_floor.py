"""ctx_floor.py — GH1234 (agreement 7383BD57): startup-context floor helper
library. All logic bodies live here (§1f); the entry point at
SYSTEM/cli/build/ctx-floor-report.py is imports + argparse + call-sites only.

No import-time side effects.
"""
from __future__ import annotations

import json
import socket
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict


class ProfileStats(TypedDict):
    n_sessions: int
    median_tokens: int
    p90_tokens: int
    min_tokens: int
    max_tokens: int


def parse_ts(raw: object) -> datetime | None:
    """Parse a real transcript timestamp. Accepts fractional-second '...Z',
    whole-second '...Z', and explicit '+00:00' offsets. Returns None for
    anything non-string or unparseable."""
    if not isinstance(raw, str):
        return None
    text = raw
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _record_ctx(record: dict[str, object]) -> int | None:
    """Return the candidate ctx for a record, or None if it is not a
    candidate. Every field is read with .get(), never subscripted."""
    message = record.get("message")
    if not isinstance(message, dict):
        return None
    usage = message.get("usage")
    if not isinstance(usage, dict):
        return None
    is_sidechain = record.get("isSidechain")
    if is_sidechain:
        return None
    ts = parse_ts(record.get("timestamp"))
    if ts is None:
        return None
    ctx = (
        (usage.get("input_tokens") or 0)
        + (usage.get("cache_creation_input_tokens") or 0)
        + (usage.get("cache_read_input_tokens") or 0)
    )
    if ctx <= 0:
        return None
    return ctx


def session_floor(path: Path, cutoff: datetime) -> int | None:
    """Stream a session JSONL file and return the ctx of the candidate
    record with the globally earliest timestamp in the file, iff that
    timestamp is >= cutoff (inclusive); else None. Malformed lines and
    unparseable timestamps are skipped silently."""
    best_ts: datetime | None = None
    best_ctx: int | None = None

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    record = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(record, dict):
                    continue
                ts = parse_ts(record.get("timestamp"))
                if ts is None:
                    continue
                ctx = _record_ctx(record)
                if ctx is None:
                    continue
                if best_ts is None or ts < best_ts:
                    best_ts = ts
                    best_ctx = ctx
    except OSError:
        return None

    if best_ts is None or best_ctx is None:
        return None
    if best_ts >= cutoff:
        return best_ctx
    return None


def profile_floors(roots: list[Path], cutoff: datetime) -> dict[str, list[int]]:
    """Scan <root>/projects/*/*.jsonl for each root, keyed by profile-dir
    basename (merged/de-duplicated by resolved path). One value per session
    file that yields a floor."""
    result: dict[str, list[int]] = {}
    seen_resolved: set[Path] = set()

    for root in roots:
        try:
            resolved = root.resolve()
        except OSError:
            continue
        if resolved in seen_resolved:
            continue
        seen_resolved.add(resolved)

        if not resolved.exists() or not resolved.is_dir():
            continue
        projects_dir = resolved / "projects"
        if not projects_dir.is_dir():
            continue

        profile_key = resolved.name
        values = result.setdefault(profile_key, [])

        try:
            session_files = sorted(projects_dir.glob("*/*.jsonl"))
        except OSError:
            continue

        for session_path in session_files:
            try:
                mtime = session_path.stat().st_mtime
            except OSError:
                continue
            file_mtime = datetime.fromtimestamp(mtime, tz=timezone.utc)
            if file_mtime < cutoff:
                continue
            floor = session_floor(session_path, cutoff)
            if floor is not None:
                values.append(floor)

        if not values:
            del result[profile_key]

    return result


def percentile(values_sorted: list[int], q: float) -> int:
    """Nearest-rank percentile: idx = min(len-1, int(len * q))."""
    idx = min(len(values_sorted) - 1, int(len(values_sorted) * q))
    return values_sorted[idx]


def summarize(floors: dict[str, list[int]]) -> dict[str, ProfileStats]:
    """Sort every value list ascending before computing stats; also sort the
    full concatenation for the ALL key."""
    summary: dict[str, ProfileStats] = {}
    all_values: list[int] = []

    for profile, values in floors.items():
        if not values:
            continue
        values_sorted = sorted(values)
        all_values.extend(values_sorted)
        summary[profile] = {
            "n_sessions": len(values_sorted),
            "median_tokens": percentile(values_sorted, 0.5),
            "p90_tokens": percentile(values_sorted, 0.9),
            "min_tokens": values_sorted[0],
            "max_tokens": values_sorted[-1],
        }

    if all_values:
        all_sorted = sorted(all_values)
        summary["ALL"] = {
            "n_sessions": len(all_sorted),
            "median_tokens": percentile(all_sorted, 0.5),
            "p90_tokens": percentile(all_sorted, 0.9),
            "min_tokens": all_sorted[0],
            "max_tokens": all_sorted[-1],
        }

    return summary


def render_text(summary: dict[str, ProfileStats]) -> str:
    """Fixed-width table. Deliberately no AC (§2.1) — operator sugar."""
    lines = []
    header = f"{'profile':<20}{'n':>6}{'median':>10}{'p90':>10}"
    lines.append(header)
    for profile, stats in summary.items():
        lines.append(
            f"{profile:<20}{stats['n_sessions']:>6}"
            f"{stats['median_tokens']:>10}{stats['p90_tokens']:>10}"
        )
    return "\n".join(lines)


def emit_events(
    log_path: Path,
    summary: dict[str, ProfileStats],
    now: datetime,
    run_id: str = "ctx-floor-report",
) -> int:
    """Append one line per key in summary (profiles + ALL). Append-only,
    never truncates. Returns the number of lines appended."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    ts = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    host = socket.gethostname().split(".")[0]

    count = 0
    with open(log_path, "a", encoding="utf-8") as fh:
        for profile, stats in summary.items():
            payload = {
                "profile": profile,
                "n_sessions": stats["n_sessions"],
                "median_tokens": stats["median_tokens"],
                "p90_tokens": stats["p90_tokens"],
                "host": host,
            }
            record = {
                "event_type": "ctx_floor_measured",
                "ts": ts,
                "run_id": run_id,
                "payload": payload,
            }
            fh.write(json.dumps(record) + "\n")
            count += 1

    return count
