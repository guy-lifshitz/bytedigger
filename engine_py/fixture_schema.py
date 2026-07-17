"""fixture_schema.py — deterministic fixture-DDL ⊆ reference-DDL RED-lint (GH891).

Closes the fixture-fiction class (battle-818: a RED test hand-invents a
CREATE TABLE column that does not exist in the real prod DB schema; every
existing gate checks tests<->code, none checks fixtures<->reality).

Stdlib-only, sibling of stub_passability.py.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

GROUND_TRUTH_HEADER_RE = re.compile(r"^#{2,4}\s+Data-Model Ground Truth\b", re.MULTILINE | re.IGNORECASE)

_TABLE_NAME_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`\"'\[]?(\w+)",
    re.IGNORECASE,
)

_CONSTRAINT_KEYWORDS = {"PRIMARY", "FOREIGN", "UNIQUE", "CHECK", "CONSTRAINT"}

_ANY_HEADING_RE = re.compile(r"^(#{1,4})\s", re.MULTILINE)


@dataclass(frozen=True)
class FixtureSchemaFinding:
    table: str
    extra_columns: tuple[str, ...]
    line: int
    kind: str  # "drift" | "unparseable"


def _find_paren_body(text: str, start: int) -> str | None:
    """Given start index just after table name match, find the first
    '(' and return the paren-balanced body (excluding outer parens).
    None if unbalanced/not found."""
    idx = text.find("(", start)
    if idx == -1:
        return None
    depth = 0
    for i in range(idx, len(text)):
        c = text[i]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return text[idx + 1:i]
    return None  # unbalanced


def _split_top_level_commas(body: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    cur = []
    for c in body:
        if c == "(":
            depth += 1
            cur.append(c)
        elif c == ")":
            depth -= 1
            cur.append(c)
        elif c == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(c)
    if cur:
        parts.append("".join(cur))
    return parts


def _extract_columns(body: str) -> set[str]:
    columns: set[str] = set()
    for item in _split_top_level_commas(body):
        item = item.strip()
        if not item:
            continue
        m = re.match(r"[`\"'\[]?(\w+)", item)
        if not m:
            continue
        token = m.group(1)
        if token.upper() in _CONSTRAINT_KEYWORDS:
            continue
        columns.add(token.lower())
    return columns


def _section_slice(text: str, header_match: "re.Match[str]") -> str:
    """Return text from header_match.start() to the next heading of level
    <= the header's level, or EOF."""
    header_level = len(header_match.group(0).split()[0])
    start = header_match.start()
    search_from = header_match.end()
    for hm in _ANY_HEADING_RE.finditer(text, search_from):
        if len(hm.group(1)) <= header_level:
            return text[start:hm.start()]
    return text[start:]


def parse_reference_ddl(spec_text: str) -> dict[str, set[str]]:
    header_match = GROUND_TRUTH_HEADER_RE.search(spec_text)
    if header_match is None:
        return {}

    section = _section_slice(spec_text, header_match)

    result: dict[str, set[str]] = {}
    for fence_match in re.finditer(r"```(?:sql)?\n(.*?)```", section, re.DOTALL):
        fence_body = fence_match.group(1)
        for tm in _TABLE_NAME_RE.finditer(fence_body):
            table = tm.group(1).lower()
            body = _find_paren_body(fence_body, tm.end())
            if body is None:
                continue
            result[table] = _extract_columns(body)
    return result


def scan_fixture_schema(source: str, reference: dict[str, set[str]]) -> list[FixtureSchemaFinding]:
    findings: list[FixtureSchemaFinding] = []
    lines = source.splitlines()

    for tm in _TABLE_NAME_RE.finditer(source):
        table = tm.group(1).lower()
        if table not in reference:
            continue

        line = source.count("\n", 0, tm.start()) + 1
        line_text = lines[line - 1] if 1 <= line <= len(lines) else ""
        if "# fixture-schema: allow" in line_text:
            continue

        body = _find_paren_body(source, tm.end())
        if body is None:
            findings.append(FixtureSchemaFinding(
                table=table, extra_columns=(), line=line, kind="unparseable",
            ))
            continue

        fixture_cols = _extract_columns(body)
        extra = fixture_cols - reference[table]
        if extra:
            findings.append(FixtureSchemaFinding(
                table=table, extra_columns=tuple(sorted(extra)), line=line, kind="drift",
            ))

    return findings


def lint_red_file(path: str, reference: dict[str, set[str]]) -> list[FixtureSchemaFinding]:
    with open(path, encoding="utf-8", errors="replace") as fh:
        source = fh.read()
    return scan_fixture_schema(source, reference)
