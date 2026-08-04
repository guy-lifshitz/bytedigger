"""format_conversion_cases.py — §1x format-conversion lint: any spec discussing
format-conversion must have a §2.3 writer-behavior section covering the four
writer cases (append/migrate/refuse/create).

Part of GH540 — spec-lint batch.
"""
from __future__ import annotations

import re

_RE_TRIGGER = re.compile(r"format[-\s]conversion", re.IGNORECASE)

# Regex: start of a §2.3 header
_RE_SEC23_START = re.compile(r"^#+\s*§?\s*2\.3\b")

# Regex: any markdown heading (ends the §2.3 span)
_RE_HEADING = re.compile(r"^#+\s")

_CASE_KEYWORDS = ["append", "migrate", "refuse", "create"]


def scan_format_conversion(spec_text: str) -> list[dict]:
    """Scan spec_text for a missing/incomplete §2.3 format-conversion writer
    behavior section.

    Returns a list of finding dicts:
      {"line": <1-based int>, "rule_id": <str>, "missing": [...], "evidence": <str>}
    """
    if not _RE_TRIGGER.search(spec_text):
        return []

    lines = spec_text.splitlines()

    start_idx = None
    for i, line in enumerate(lines):
        if _RE_SEC23_START.match(line):
            start_idx = i
            break

    if start_idx is None:
        return [{
            "line": 1,
            "rule_id": "format-conversion-missing-section",
            "missing": ["§2.3"],
            "evidence": "no §2.3 writer-behavior section",
        }]

    end_idx = len(lines)
    for i in range(start_idx + 1, len(lines)):
        if _RE_HEADING.match(lines[i]):
            end_idx = i
            break

    body = "\n".join(lines[start_idx + 1:end_idx]).lower()

    missing = [kw for kw in _CASE_KEYWORDS if kw not in body]
    if missing:
        return [{
            "line": start_idx + 1,  # 1-based
            "rule_id": "format-conversion-missing-cases",
            "missing": missing,
            "evidence": "missing cases: " + ",".join(missing),
        }]

    return []
