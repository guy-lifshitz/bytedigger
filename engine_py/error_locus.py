from __future__ import annotations

from datetime import datetime, timedelta

LINTABLE_PATTERNS = ["CITE", "SCOPE", "COVERAGE", "MISSING", "FIELD", "GROUND", "IMPORT"]


def classify_error(error_code: str | None) -> str:
    """Return 'lintable' if error_code contains any LINTABLE_PATTERNS, else 'needs-prompt'."""
    if not error_code:
        return "needs-prompt"
    upper = error_code.upper()
    for pattern in LINTABLE_PATTERNS:
        if pattern in upper:
            return "lintable"
    return "needs-prompt"


def _parse_ts(ts: str) -> datetime:
    """Tolerant timestamp parser: strips trailing Z and fractional seconds."""
    ts = ts.rstrip("Z")
    ts = ts.split(".")[0]
    return datetime.strptime(ts, "%Y-%m-%dT%H:%M:%S")


def aggregate(events: list[dict], window_days: int | None = None) -> list[dict]:
    """Aggregate build events into per-file error locus stats."""
    if not events:
        return []

    # Window filter anchored on MAX ts across events
    if window_days is not None:
        max_dt = max(_parse_ts(e["ts"]) for e in events)
        cutoff = max_dt - timedelta(days=window_days)
        events = [e for e in events if _parse_ts(e["ts"]) >= cutoff]
        if not events:
            return []

    # PASS 1 — group by run_id
    builds: dict[str, dict] = {}
    for e in events:
        run_id = e["run_id"]
        if run_id not in builds:
            builds[run_id] = {
                "touched": {},        # path -> touch_count
                "statuses": {},       # path -> set(status)
                "retry_codes": [],    # list of error_code strings
                "finding_count": 0,
            }
        b = builds[run_id]
        etype = e.get("event_type")
        payload = e.get("payload") or {}

        if etype == "files_touched":
            for path in payload.get("paths") or []:
                b["touched"][path] = b["touched"].get(path, 0) + 1
            for status, paths in (payload.get("by_status") or {}).items():
                for path in paths or []:
                    if path not in b["statuses"]:
                        b["statuses"][path] = set()
                    b["statuses"][path].add(status)

        elif etype == "phase_retry_triggered":
            code = payload.get("error_code")
            if code:
                b["retry_codes"].append(code)

        elif etype == "review_findings_audit":
            agg = payload.get("aggregated", 0)
            if agg > b["finding_count"]:
                b["finding_count"] = agg

    # PASS 2 — per file across all builds
    file_data: dict[str, dict] = {}
    for run_id, b in builds.items():
        has_retries = bool(b["retry_codes"])
        has_findings = b["finding_count"] > 0
        for path, touch_count in b["touched"].items():
            if path not in file_data:
                file_data[path] = {
                    "touches": 0,
                    "builds": 0,
                    "retry_incidence": 0,
                    "finding_incidence": 0,
                    "all_retry_codes": [],
                    "status_counts": {},
                }
            fd = file_data[path]
            fd["touches"] += touch_count
            fd["builds"] += 1
            if has_retries:
                fd["retry_incidence"] += 1
                fd["all_retry_codes"].extend(b["retry_codes"])
            if has_findings:
                fd["finding_incidence"] += 1
            # status counts
            for status in (b["statuses"].get(path) or set()):
                fd["status_counts"][status] = fd["status_counts"].get(status, 0) + 1

    # Build result list
    result: list[dict] = []
    for path, fd in file_data.items():
        # dominant error class: most frequent code, tiebreak alphabetical
        dominant: str | None = None
        if fd["all_retry_codes"]:
            freq: dict[str, int] = {}
            for code in fd["all_retry_codes"]:
                freq[code] = freq.get(code, 0) + 1
            dominant = sorted(freq.keys(), key=lambda c: (-freq[c], c))[0]

        module = path.rsplit("/", 1)[0] if "/" in path else ""

        result.append({
            "file": path,
            "module": module,
            "touches": fd["touches"],
            "builds": fd["builds"],
            "retry_incidence": fd["retry_incidence"],
            "finding_incidence": fd["finding_incidence"],
            "dominant_error_class": dominant,
            "status_counts": fd["status_counts"],
            "classify": classify_error(dominant),
        })

    result.sort(key=lambda r: (-r["touches"], -r["retry_incidence"], r["file"]))
    return result
