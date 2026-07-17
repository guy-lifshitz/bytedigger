"""Data-Model Ground Truth verifier for HAL engine_py spec_lint (GH891, §1ae).

Detects specs that mention an on-disk DB path (`.db`) without a Ground-Truth
DDL section, or carry an empty/opt-out-less Ground-Truth section.

Standalone: scripts/lib is import-isolated from engine_py — the header/table
regexes are duplicated here rather than importing fixture_schema.py (the
canonical prod sibling that scans fixture DDL at phase-5 lint time).
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

DB_REF_RE = re.compile(r"[\w./~-]+\.db\b")

GROUND_TRUTH_HEADER_RE = re.compile(
    r"^#{2,4}\s+Data-Model Ground Truth\b", re.MULTILINE | re.IGNORECASE,
)

_ANY_HEADING_RE = re.compile(r"^(#{1,4})\s", re.MULTILINE)

_CREATE_TABLE_RE = re.compile(r"CREATE\s+TABLE", re.IGNORECASE)

_OPT_OUT_RE = re.compile(r"ground-truth:\s*n/a", re.IGNORECASE)


@dataclass(frozen=True)
class GroundTruthFinding:
    offset: int
    rule_id: str
    evidence: str


def _section_slice(text: str, header_match: "re.Match[str]") -> str:
    header_level = len(header_match.group(0).split()[0])
    start = header_match.start()
    search_from = header_match.end()
    for hm in _ANY_HEADING_RE.finditer(text, search_from):
        if len(hm.group(1)) <= header_level:
            return text[start:hm.start()]
    return text[start:]


def find_missing_ground_truth(spec_text: str, hal_root: Path) -> list[GroundTruthFinding]:
    findings: list[GroundTruthFinding] = []

    header_match = GROUND_TRUTH_HEADER_RE.search(spec_text)

    # Check 1: .db mention with no header at all — independent of check 2.
    db_match = DB_REF_RE.search(spec_text)
    if db_match is not None and header_match is None:
        findings.append(GroundTruthFinding(
            offset=db_match.start(),
            rule_id="missing-ground-truth-ddl",
            evidence=db_match.group(0),
        ))

    # Check 2: header present but section is empty (no fence, no opt-out).
    if header_match is not None:
        section = _section_slice(spec_text, header_match)
        has_fence_with_create_table = False
        for fence_match in re.finditer(r"```(?:sql)?\n(.*?)```", section, re.DOTALL):
            if _CREATE_TABLE_RE.search(fence_match.group(1)):
                has_fence_with_create_table = True
                break
        has_opt_out = bool(_OPT_OUT_RE.search(section))
        if not has_fence_with_create_table and not has_opt_out:
            findings.append(GroundTruthFinding(
                offset=header_match.start(),
                rule_id="empty-ground-truth-ddl",
                evidence="Data-Model Ground Truth",
            ))

    return sorted(findings, key=lambda f: f.offset)
