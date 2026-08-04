"""fixture_schema.py — deterministic fixture-DDL ⊆ reference-DDL RED-lint (GH891).

Closes the fixture-fiction class (battle-818: a RED test hand-invents a
CREATE TABLE column that does not exist in the real prod DB schema; every
existing gate checks tests<->code, none checks fixtures<->reality).

GH1350: a silently-incomplete reference derivation is the same class one level
up — an unrecognised element or an unbalanced/unparseable body must never be
treated as a no-op. Three outcomes only: agree (no finding), drift (a real
extra column), not-comparable (the reference itself could not be derived in
full, so it has no right to take part in a comparison). A reference table that
cannot be derived is represented explicitly via `_NOT_COMPARABLE`, never
omitted — "absent" and "derived as empty" must not be indistinguishable.

Stdlib-only, sibling of stub_passability.py.
"""
from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

GROUND_TRUTH_HEADER_RE = re.compile(r"^#{2,4}\s+Data-Model Ground Truth\b", re.MULTILINE | re.IGNORECASE)

# GH1350 §15: anchored strictly zero-width (lookahead), so `tm.end()` stays
# byte-identical to the unanchored form and `_find_paren_body(text, tm.end())`
# keeps reading the same body. The seam `[\s`"'+\\]*` tolerates Python implicit
# string concatenation between the table name and the paren ("CREATE TABLE t "
# "(a TEXT)") while admitting no word characters, so prose that merely mentions
# "CREATE TABLE <word>" without an immediate paren-body seam never matches.
_TABLE_NAME_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?[`\"'\[]?(\w+)"
    r"(?=[`\"'\]]?[\s`\"'+\\]*\()",
    re.IGNORECASE,
)

_CONSTRAINT_KEYWORDS = {"PRIMARY", "FOREIGN", "UNIQUE", "CHECK", "CONSTRAINT"}

_ANY_HEADING_RE = re.compile(r"^(#{1,4})\s", re.MULTILINE)

_COLUMN_TOKEN_RE = re.compile(r"[`\"'\[]?(\w+)")


class _NotComparable:
    """Sentinel: this reference table's DDL could not be derived in full (a
    parse anomaly, not a no-op) and must not take part in a set comparison
    (GH1350 §8/§12/§17). Present-and-marked, never absent — distinguishable
    from any real `set[str]`, and never `None` (a dropped table is skipped
    silently downstream, the other half of the same class)."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "<NOT_COMPARABLE>"


_NOT_COMPARABLE = _NotComparable()


@dataclass(frozen=True)
class FixtureSchemaFinding:
    table: str
    extra_columns: tuple[str, ...]
    line: int
    kind: str  # "drift" | "unparseable" | "not-comparable" | "unrecognised-element"


def _strip_sql_comments(body: str) -> str:
    """Remove `--` (to end of line) and `/* ... */` comments, honouring `'`
    and `"` string literals so a comment marker inside a quoted default does
    not corrupt the DDL.

    Called from the derivation path (`_find_paren_body`) BEFORE paren
    balancing and BEFORE splitting (GH1350 §3/§6 B3): a paren or comma inside
    a comment must never reach either step. GH1350 §17 scope delta: this is a
    named module-level hook, and callers must dispatch through the module
    attribute at call time (not a captured local/default) so the AC12
    mutation can retarget it without editing this file.
    """
    out: list[str] = []
    i, n, quote = 0, len(body), None
    while i < n:
        c = body[i]
        if quote:
            out.append(c)
            if c == quote:
                quote = None
            i += 1
            continue
        if c in "'\"":
            quote = c
            out.append(c)
            i += 1
            continue
        if body.startswith("--", i):
            j = body.find("\n", i)
            if j == -1:
                i = n
            else:
                out.append("\n")
                i = j + 1
            continue
        if body.startswith("/*", i):
            j = body.find("*/", i + 2)
            i = n if j == -1 else j + 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def _find_paren_body(text: str, start: int) -> str | None:
    """Given start index just after table name match, strip comments (GH1350
    §3 item 1 — before anything else so a paren inside a comment cannot affect
    balancing) and return the paren-balanced body of the first '(' found from
    `start` onward (excluding outer parens). None if unbalanced/not found.
    """
    remainder = _strip_sql_comments(text[start:])
    idx = remainder.find("(")
    if idx == -1:
        return None
    depth = 0
    for i in range(idx, len(remainder)):
        c = remainder[i]
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return remainder[idx + 1:i]
    return None  # unbalanced


def _split_top_level_commas(body: str) -> list[str]:
    """GH1350 AC5: literal-aware — a comma (or a `--`) inside a `'`/`"`
    string literal is not a top-level separator."""
    parts: list[str] = []
    depth = 0
    quote: str | None = None
    cur: list[str] = []
    for c in body:
        if quote:
            cur.append(c)
            if c == quote:
                quote = None
            continue
        if c in "'\"":
            quote = c
            cur.append(c)
            continue
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
        m = _COLUMN_TOKEN_RE.match(item)
        if not m:
            continue
        token = m.group(1)
        if token.upper() in _CONSTRAINT_KEYWORDS:
            continue
        columns.add(token.lower())
    return columns


def _unrecognised_items(body: str) -> list[str]:
    """Top-level items that fail the same recogniser `_extract_columns` uses.
    GH1350 class 2: an unrecognised element is a parse anomaly, never a silent
    no-op — the caller decides what that means for its side (fixture: counted
    event, no entry; reference: not-comparable, §17)."""
    bad: list[str] = []
    for item in _split_top_level_commas(body):
        item = item.strip()
        if item and not _COLUMN_TOKEN_RE.match(item):
            bad.append(item)
    return bad


def _count_columns_via_sqlite(body: str) -> int | None:
    """Independent second source (GH1350 §3 item 4): sqlite3's own parser,
    executing a throwaway `_probe` table in `:memory:` and reading
    `PRAGMA table_info`. Returns None when sqlite3 itself rejects the body —
    treated the same as a disagreement by the derivation path (§18 AC20:
    consulted, not merely present).

    Both the docstring above and the statement below deliberately avoid writing
    the probe DDL as one adjacent literal. `_TABLE_NAME_RE` would match it, and
    this module's own probe would enter the corpus the instrument measures —
    the same circularity for which the instrument already excludes
    `tests/tools/` and `test_gh1350_*`. Measured: as an adjacent literal it
    added two `unrecognised_element` rows for a table `_probe` that has no
    clean sibling, reddening AC8. Split, not suppressed with
    `# fixture-schema: allow`: a marker would hide a real match, while the
    honest fact is that this text was never fixture DDL."""
    probe = "_probe"
    try:
        conn = sqlite3.connect(":memory:")
        try:
            conn.execute("CREATE TABLE " + probe + " (" + body + ")")
            return len(conn.execute(f"PRAGMA table_info({probe})").fetchall())
        finally:
            conn.close()
    except Exception:
        return None


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


def parse_reference_ddl(spec_text: str) -> dict[str, object]:
    """Returns table -> set[str] of columns, or table -> `_NOT_COMPARABLE`
    when the table's body could not be derived in full: unbalanced parens, an
    unrecognised element, or the recogniser's count disagreeing with the
    independent sqlite3 count (GH1350 §8/§17/§18). Never raises; a table that
    cannot be derived is present-and-marked, never absent or silently dropped.
    """
    header_match = GROUND_TRUTH_HEADER_RE.search(spec_text)
    if header_match is None:
        return {}

    section = _section_slice(spec_text, header_match)

    result: dict[str, object] = {}
    for fence_match in re.finditer(r"```(?:sql)?\n(.*?)```", section, re.DOTALL):
        fence_body = fence_match.group(1)
        for tm in _TABLE_NAME_RE.finditer(fence_body):
            table = tm.group(1).lower()
            body = _find_paren_body(fence_body, tm.end())
            if body is None:
                result[table] = _NOT_COMPARABLE
                continue
            if _unrecognised_items(body):
                # §17: an unrecognised element on the REFERENCE side is not
                # class 2's "derive what you can, stay silent" — a partial
                # reference has no right to take part in a comparison.
                result[table] = _NOT_COMPARABLE
                continue
            recognised = _extract_columns(body)
            second_count = _count_columns_via_sqlite(body)
            if second_count is None or second_count != len(recognised):
                # §18 AC20: the independent second count is CONSULTED, not
                # merely present — a disagreement (including sqlite3 itself
                # rejecting the body) makes the reference not-comparable.
                result[table] = _NOT_COMPARABLE
                continue
            result[table] = recognised
    return result


def scan_fixture_schema(source: str, reference: dict[str, object]) -> list[FixtureSchemaFinding]:
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

        marker = reference[table]
        if not isinstance(marker, set):
            # The reference itself was never fully derived — never compute a
            # drift against it, and never stay silent either (GH1350 §8).
            findings.append(FixtureSchemaFinding(
                table=table, extra_columns=(), line=line, kind="not-comparable",
            ))
            continue

        body = _find_paren_body(source, tm.end())
        if body is None:
            findings.append(FixtureSchemaFinding(
                table=table, extra_columns=(), line=line, kind="unparseable",
            ))
            continue

        if _unrecognised_items(body):
            # GH1350 class 2, fixture side: columns are still derived from the
            # recognised items, but this must not block — it is not drift.
            findings.append(FixtureSchemaFinding(
                table=table, extra_columns=(), line=line, kind="unrecognised-element",
            ))
            continue

        fixture_cols = _extract_columns(body)
        extra = fixture_cols - marker
        if extra:
            findings.append(FixtureSchemaFinding(
                table=table, extra_columns=tuple(sorted(extra)), line=line, kind="drift",
            ))

    return findings


def lint_red_file(path: str, reference: dict[str, object]) -> list[FixtureSchemaFinding]:
    with open(path, encoding="utf-8", errors="replace") as fh:
        source = fh.read()
    return scan_fixture_schema(source, reference)
