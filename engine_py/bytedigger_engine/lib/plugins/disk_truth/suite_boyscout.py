"""suite_boyscout.py — pure suite-level boy-scout gate logic (8C9F758C).

Pure, dependency-light module. No subprocess calls. No I/O.

Public API:
    parse_failing_nodeids(stdout, framework) -> list[str]
    parse_allowlist(text) -> dict[str, AllowEntry]
    evaluate(failing, allowlist, today, enforce) -> BoyscoutVerdict
    AllowEntry   — frozen dataclass: pattern, agreement_id, kill_by
    BoyscoutVerdict — frozen dataclass: failing, covered, expired, uncovered, would_block
    _today() -> date   — indirection for test injection (real code uses today param)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Dict, List


# ── data types ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class AllowEntry:
    """One allowlist entry — covers node-ids that contain `pattern` as a substring."""
    pattern: str
    agreement_id: str
    kill_by: date


@dataclass(frozen=True)
class BoyscoutVerdict:
    """Result of evaluate()."""
    failing: List[str]
    covered: List[str]
    expired: List[str]
    uncovered: List[str]
    would_block: bool


# ── framework regex patterns ──────────────────────────────────────────────────

# pytest: lines starting with "FAILED <nodeid>" or "ERROR <nodeid>" (anchored at line start)
_PYTEST_FAILED_RE = re.compile(r"^FAILED (\S+::\S+)", re.MULTILINE)
_PYTEST_ERROR_RE = re.compile(r"^ERROR (\S+::\S+)", re.MULTILINE)

# bun: lines like "(fail) <label> [<timing>]"
_BUN_FAIL_RE = re.compile(r"^\(fail\)\s+(.+?)(?:\s+\[[\d.]+m?s\])?$", re.MULTILINE)

# allowlist: agreement-id is 8 hex chars (case-insensitive)
_AGREEMENT_ID_RE = re.compile(r"^[0-9A-Fa-f]{8}$")


# ── public functions ──────────────────────────────────────────────────────────


def _today() -> date:
    """Indirection so tests can monkeypatch date.today; evaluate takes today as param."""
    return date.today()


def parse_failing_nodeids(stdout: str, framework: str) -> List[str]:
    """Extract failing node-ids from test runner stdout.

    Args:
        stdout:    Raw combined stdout from the test runner.
        framework: One of "pytest", "bun". Unknown → returns [].

    Returns:
        List of node-id strings. Anchored patterns prevent mid-traceback
        FAILED words from being captured.
    """
    if framework == "pytest":
        failed = _PYTEST_FAILED_RE.findall(stdout)
        errors = _PYTEST_ERROR_RE.findall(stdout)
        return failed + errors
    elif framework == "bun":
        return _BUN_FAIL_RE.findall(stdout)
    else:
        return []


def parse_allowlist(text: str) -> Dict[str, AllowEntry]:
    """Parse allowlist text into a dict keyed by pattern.

    Format (one entry per line):
        <node-id-substring> :: <AGREEMENT_ID_8HEX> :: kill-by:YYYY-MM-DD

    Lines starting with '#' or blank lines are ignored.
    Raises ValueError (naming the bad line) on:
        - not exactly 3 '::'-delimited fields
        - agreement-id not matching ^[0-9A-Fa-f]{8}$
        - kill-by missing, not prefixed with 'kill-by:', or unparseable date
    """
    result: Dict[str, AllowEntry] = {}
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue

        parts = [p.strip() for p in line.split(" :: ")]
        if len(parts) != 3:
            raise ValueError(
                f"Allowlist line must have exactly 3 ' :: '-delimited fields; "
                f"got {len(parts)} in: {line!r}"
            )

        pattern, agreement_id, kill_by_raw = parts

        if not _AGREEMENT_ID_RE.match(agreement_id):
            raise ValueError(
                f"Allowlist agreement-id must be 8 hex chars (^[0-9A-Fa-f]{{8}}$); "
                f"got {agreement_id!r} in line: {line!r}"
            )

        if not kill_by_raw.startswith("kill-by:"):
            raise ValueError(
                f"Allowlist kill-by field must start with 'kill-by:'; "
                f"got {kill_by_raw!r} in line: {line!r}"
            )

        date_str = kill_by_raw[len("kill-by:"):]
        try:
            kill_by = datetime.strptime(date_str, "%Y-%m-%d").date()
        except ValueError:
            raise ValueError(
                f"Allowlist kill-by date {date_str!r} is not a valid YYYY-MM-DD date "
                f"in line: {line!r}"
            )

        entry = AllowEntry(pattern=pattern, agreement_id=agreement_id, kill_by=kill_by)
        result[pattern] = entry

    return result


def evaluate(
    failing: List[str],
    allowlist: Dict[str, AllowEntry],
    *,
    today: date,
    enforce: bool,
) -> BoyscoutVerdict:
    """Classify each failing node-id against the allowlist.

    Classification (per node-id):
        covered  — some entry.pattern is a substring of node-id AND kill_by >= today
        expired  — matched by a pattern but kill_by < today
        uncovered — no pattern matches at all

    would_block (SINGLE SOURCE OF TRUTH):
        enforce and bool(expired or uncovered)

    When enforce=False: would_block is always False, but covered/expired/uncovered
    are still populated for telemetry.
    """
    covered: List[str] = []
    expired: List[str] = []
    uncovered: List[str] = []

    for node_id in failing:
        # Find the best matching entry (prefer non-expired over expired)
        matching_entries = [
            entry for entry in allowlist.values()
            if entry.pattern in node_id
        ]

        if not matching_entries:
            uncovered.append(node_id)
            continue

        # Among matching entries, check if any is non-expired (valid)
        valid_entries = [e for e in matching_entries if e.kill_by >= today]
        if valid_entries:
            covered.append(node_id)
        else:
            # All matching entries are expired
            expired.append(node_id)

    would_block = enforce and bool(expired or uncovered)

    return BoyscoutVerdict(
        failing=list(failing),
        covered=covered,
        expired=expired,
        uncovered=uncovered,
        would_block=would_block,
    )
