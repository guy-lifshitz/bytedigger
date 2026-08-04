"""Data-Model Ground Truth verifier for HAL engine_py spec_lint (GH891, §1ae).

Detects specs that mention an on-disk DB path (`.db`) without a Ground-Truth
DDL section, or carry an empty/opt-out-less Ground-Truth section.

GH962/GH952: `.db`-suffixed tokens are keyed on real DB *artifacts* only
(`classify_db_token`) — placeholder paths (`d.db`) and attribute access
(`args.db`) no longer trigger `missing-ground-truth-ddl`, killing the FP
class that contradicted cite-lint's verbatim-citation requirement.

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

_IDENTIFIER_SHAPED_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*\.db$")

_OPEN_SIGNAL_RE = re.compile(
    r"sqlite3\.connect|connect\(|CREATE\s+TABLE|Path\(|\bopen\(|\btouch\b",
    re.IGNORECASE,
)

_QUOTE_CHARS = "\"'"


def _pragma_exempts(spec_text: str, token: str) -> bool:
    pragma_re = re.compile(
        r"ground-truth:\s*never-opened\s+" + re.escape(token), re.IGNORECASE,
    )
    return bool(pragma_re.search(spec_text))


def classify_db_token(spec_text: str, m: "re.Match[str]") -> bool:
    """True iff the matched .db token denotes a real DB artifact requiring ground-truth DDL."""
    token = m.group(0)

    # 1. Pragma exemption (per-token, #952 ask).
    if _pragma_exempts(spec_text, token):
        return False

    # 2. Path-like: contains "/" or "~" -> artifact.
    if "/" in token or "~" in token:
        return True

    # 3. Identifier-shaped & unquoted -> not artifact (attribute access,
    #    bare placeholder).
    if _IDENTIFIER_SHAPED_RE.match(token):
        preceded_by_quote = (
            m.start() > 0 and spec_text[m.start() - 1] in _QUOTE_CHARS
        )
        followed_by_quote = (
            m.end() < len(spec_text) and spec_text[m.end()] in _QUOTE_CHARS
        )
        if not (preceded_by_quote and followed_by_quote):
            return False

    # 4. Otherwise: artifact iff an open-signal appears within a ±160-char
    #    window around the token.
    window = spec_text[max(0, m.start() - 160):m.end() + 160]
    return bool(_OPEN_SIGNAL_RE.search(window))


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

    # Check 1: .db artifact mention with no header at all — independent of
    # check 2. Iterate ALL matches; fire on the first token that classifies
    # as a real DB artifact (GH962/GH952).
    if header_match is None:
        for db_match in DB_REF_RE.finditer(spec_text):
            if classify_db_token(spec_text, db_match):
                findings.append(GroundTruthFinding(
                    offset=db_match.start(),
                    rule_id="missing-ground-truth-ddl",
                    evidence=db_match.group(0),
                ))
                break

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
