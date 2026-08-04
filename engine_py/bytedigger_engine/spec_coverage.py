"""spec_coverage.py — lib for spec-coverage-lint (§1w enumerated-op ↔ AC coverage).

API:
    scan_spec_coverage(spec_text: str) -> list[dict]
        Returns findings; each {"op": <id>, "line": <int>, "reason": "no covering AC"}.
        Returns [] when no AC table is present (nothing to check).

    check_ac_table(spec_text: str) -> str
        Extracts and returns the AC blob. Raises NoACTableError when no table found.
        Used by the CLI to gate exit-2 before calling scan_spec_coverage.

Algorithm:
1. Extract AC table blob (first table with "AC" in header, or under acceptance heading).
2. Extract bold ops in §2 sections.
3. Cross-check each op against ac_blob (case-insensitive, whitespace-collapsed).
4. Return findings.

Part of 36034660 — spec-coverage-lint.
"""
from __future__ import annotations

import re
from collections.abc import Iterable


class NoACTableError(Exception):
    """Raised when no acceptance criteria table can be found in the spec."""


def _collapse_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", text).lower()


def _extract_ac_blob(lines: list[str]) -> str:
    """Return concatenated cell text from the first qualifying AC table.

    Qualifying table: header row contains token 'AC' (case-insensitive),
    OR the table appears under a heading matching (?i)^#{1,4}.*acceptance.
    Raises NoACTableError if no such table found.
    """
    _ACCEPT_HEADING = re.compile(r"^#{1,4}.*acceptance", re.IGNORECASE)
    _TABLE_ROW = re.compile(r"^\s*\|")
    _SEPARATOR_ROW = re.compile(r"^\s*\|[\s|:-]+\|\s*$")

    under_acceptance_heading = False
    i = 0
    while i < len(lines):
        line = lines[i]

        # Track whether we're under an acceptance heading
        if re.match(r"^#{1,4}\s", line):
            under_acceptance_heading = bool(_ACCEPT_HEADING.match(line))

        # Check if this line starts a table
        if _TABLE_ROW.match(line):
            # Collect all consecutive table rows
            table_lines: list[str] = []
            while i < len(lines) and _TABLE_ROW.match(lines[i]):
                table_lines.append(lines[i])
                i += 1

            if len(table_lines) < 2:
                continue

            header_row = table_lines[0]
            header_has_ac = bool(re.search(r"\bAC\b", header_row, re.IGNORECASE))

            if header_has_ac or under_acceptance_heading:
                # Extract all cell text from all rows (skip separator rows)
                cells: list[str] = []
                for row in table_lines:
                    if _SEPARATOR_ROW.match(row):
                        continue
                    parts = row.split("|")
                    for part in parts:
                        stripped = part.strip()
                        if stripped:
                            cells.append(stripped)
                return " ".join(cells)
            continue

        i += 1

    raise NoACTableError("no AC table found")


def _extract_ops(lines: list[str]) -> list[dict]:
    """Extract bold ops from §2 sections.

    Sections whose heading matches (?i)§?\\s*2\\b.
    Items: numbered+bold or bullet+bold.
    Op name = trimmed first ≤6 words of bold content.
    """
    _SEC2_HEADING = re.compile(r"^#{1,6}\s.*?§?\s*2\b", re.IGNORECASE)
    _HEADING = re.compile(r"^#{1,6}\s")
    _NUMBERED_BOLD = re.compile(r"^\s*\d+\.\s+\*\*(?P<name>[^*]+)\*\*")
    _BULLET_BOLD = re.compile(r"^\s*[-*]\s+\*\*(?P<name>[^*]+)\*\*")

    ops: list[dict] = []
    in_sec2 = False

    for lineno, line in enumerate(lines, start=1):
        if _HEADING.match(line):
            in_sec2 = bool(_SEC2_HEADING.match(line))
            continue

        if not in_sec2:
            continue

        m = _NUMBERED_BOLD.match(line) or _BULLET_BOLD.match(line)
        if m:
            full_name = m.group("name").strip()
            # Skip sub-step labels (e.g. "**Phase:**") — they end with ":" and
            # are structural headings, not distinct ops.
            if full_name.endswith(":"):
                continue
            words = full_name.split()
            op_id = " ".join(words[:6])
            ops.append({"op": op_id, "line": lineno, "full_name": full_name})

    return ops


def _backtick_literals(full_name: str) -> list[str]:
    """Extract backtick-quoted literals from the bold name."""
    return re.findall(r"`([^`]+)`", full_name)


def check_ac_table(spec_text: str) -> str:
    """Extract and return the AC blob from spec_text.

    Raises NoACTableError when no qualifying table is found.
    Intended for CLI use to gate exit-2 before scanning.
    """
    lines = spec_text.splitlines()
    return _extract_ac_blob(lines)


def scan_spec_coverage(spec_text: str) -> list[dict]:
    """Scan spec_text and return findings for uncovered ops.

    Each finding: {"op": <id>, "line": <int>, "reason": "no covering AC"}
    Returns [] when no AC table is present (cannot check coverage).
    Never raises — callers that need the no-AC-table signal should use
    check_ac_table() first.
    """
    lines = spec_text.splitlines()

    try:
        ac_blob = _extract_ac_blob(lines)
    except NoACTableError:
        return []

    ac_collapsed = _collapse_whitespace(ac_blob)
    ops = _extract_ops(lines)

    findings: list[dict] = []
    for op_entry in ops:
        op_id = op_entry["op"]
        full_name = op_entry["full_name"]

        if _collapse_whitespace(op_id) in ac_collapsed:
            continue

        literals = _backtick_literals(full_name)
        if any(_collapse_whitespace(lit) in ac_collapsed for lit in literals):
            continue

        findings.append({
            "op": op_id,
            "line": op_entry["line"],
            "reason": "no covering AC",
        })

    return findings


# ─── GH824 §2.1/§2.2: §1n taxonomy + §2-vs-§3 finalize cross-check ──────────

_E_CODE_RE = re.compile(r"\bE_[A-Z][A-Z0-9_]{2,}\b")
_DISPOSITION_RE = re.compile(r"(?i)\b(own|defer)\b")

_FINALIZE_GROUP_PATTERNS: dict[str, re.Pattern[str]] = {
    "cleanup": re.compile(r"(?i)\bcleanup\b"),
    "finalize": re.compile(r"(?i)\bfinali[sz]e[rd]?\b"),
    "on_failure": re.compile(r"(?i)\bon[-_ ]?failure\b"),
    "on_terminal": re.compile(r"(?i)\bon[-_ ]?terminal\b"),
    "on_exit": re.compile(r"(?i)\bon[-_ ]?exit\b"),
    "rollback": re.compile(r"(?i)\brollback\b"),
    "merged_only": re.compile(r"(?i)\bmerged-only\b"),
}


def scan_error_taxonomy(spec_text: str, known_codes: "Iterable[str]") -> list[dict[str, object]]:
    """Scan spec_text for new (unknown) E_-code tokens missing an OWN/DEFER
    disposition line (§1n). Each finding:
    {"line": <int>, "reason": ..., "rule": "spec_taxonomy_missing_disposition", "token": <code>}
    """
    known = set(known_codes)
    lines = spec_text.splitlines()

    first_line: dict[str, int] = {}
    for i, line in enumerate(lines, start=1):
        for m in _E_CODE_RE.finditer(line):
            token = m.group(0)
            if token not in first_line:
                first_line[token] = i

    findings: list[dict[str, object]] = []
    for token, lineno in first_line.items():
        if token in known:
            continue
        disposed = any(
            token in line and _DISPOSITION_RE.search(line) for line in lines
        )
        if not disposed:
            findings.append({
                "line": lineno,
                "reason": f"new error code '{token}' has no OWN/DEFER disposition (§1n)",
                "rule": "spec_taxonomy_missing_disposition",
                "token": token,
            })
    return findings


def scan_finalize_coverage(spec_text: str) -> list[dict[str, object]]:
    """Scan spec_text §2-region vs AC table for finalize/cleanup keyword-group
    cross-coverage (§2.2). Returns [] when no AC table found (NoACTableError
    swallowed, consistent with scan_spec_coverage).
    """
    from bytedigger_engine.reentry_ac import _section2_slice  # §1g reuse

    lines_all = spec_text.splitlines()
    try:
        ac_blob = _extract_ac_blob(lines_all)
    except NoACTableError:
        return []
    ac_collapsed = _collapse_whitespace(ac_blob)

    sec2_text, hline = _section2_slice(spec_text)
    sec2_lines = sec2_text.splitlines()

    findings: list[dict[str, object]] = []
    for group, pattern in _FINALIZE_GROUP_PATTERNS.items():
        sec2_line = None
        for i, line in enumerate(sec2_lines):
            if pattern.search(line):
                sec2_line = hline + i
                break
        in_sec2 = sec2_line is not None
        in_ac = bool(pattern.search(ac_collapsed))

        if in_sec2 and not in_ac:
            findings.append({
                "line": sec2_line,
                "reason": f"§2 branch '{group}' has no covering AC (§2-vs-§3 finalize xcheck)",
                "rule": "spec_finalize_branch_uncovered",
                "group": group,
            })
        elif in_ac and not in_sec2:
            full_line = None
            for i, line in enumerate(lines_all, start=1):
                if pattern.search(line):
                    full_line = i
                    break
            findings.append({
                "line": full_line,
                "reason": f"AC references '{group}' with no §2 anchor (§2-vs-§3 finalize xcheck)",
                "rule": "spec_finalize_ac_unanchored",
                "group": group,
            })
    return findings
