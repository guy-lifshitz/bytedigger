"""Mutation two-sidedness verifier for HAL engine_py spec_lint (GH1373, Rule M).

Rule
----
A spec that declares a mutation check must present BOTH observed outcomes —
red when the mutation is injected, green/unmutated when it is not. A
declaration naming only the red outcome is indistinguishable from a check
that was never run the other way: the "control" side of the experiment is
asserted by construction, not observed.

Class (§1c): a checking-mechanism claim is not distinguished from a claim
that was never checked. Rule P (`one_sided_predicate.py`) is the same class
applied to RED test-file predicates on process exit codes; this module is
the spec-side half.

§2.0 — frozen token sets (verbatim, gate rev2 blocker 3)
---------------------------------------------------------
`_MUT_DECL`/`_RED_OUT`/`_GREEN_OUT`/`_MUT_ESCAPE` are FROZEN by the spec —
copied character-for-character. The live-corpus baseline (spec §1b, pinned by
AC26) was measured with exactly these patterns; any edit here invalidates
that measurement.

Unit slicing (§2.1)
--------------------
An anchor line is one that matches `_MUT_DECL`. Its unit:
  - table row (`^\\s*\\|`)      -> unit = that line only;
  - heading (`^ {0,3}#{1,6}\\s`) -> unit = heading + lines to next heading;
  - otherwise                   -> unit = line + following lines until TWO
    consecutive blank lines or a heading.

Table-column special case: a table whose HEADER row names a "мутация"
column (matching `_MUT_DECL`) is a mutation table — every row of that
contiguous table block is its own single-line unit, even when a data row's
own text does not repeat the word "мутация" (the header names the column
once; per-row cells declare the mutation and its outcome). This is the
concrete reading of "table row -> unit = this line" for a real mutation
table (spec §1b corpus form; verified by AC5).

Two functions share this ONE slicer (§1g) — `find_one_sided_mutations`
(findings) and `summarize_mutation_units` (counts); no second copy of the
narezka lives anywhere else.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

# ─── §2.0 — frozen verbatim (do not edit without re-freezing the spec) ───────

_MUT_DECL = re.compile(
    r"(?i)"
    r"mutation[\s_-]*(?:check|sanity|guard|test|probe|proof|run)\w*"
    r"|(?:§1l\s*)?\bmutation\s+(?:on|of|for)\b"
    r"|\bmutation\d+\b"
    r"|\bмутационн\w*"
    r"|\bмутаци(?:я|и|ю|ей|е)\b"
)
_RED_OUT = re.compile(
    r"(?i)\bFAILs?\b|\bFAILED\b|\bmust\s+fail\b"
    r"|\bdies\b|\bdie\b|\bbreaks\b|\bflips?\b|\bredden\w*"
    r"|\bкрасне\w*|\bпокрасн\w*|\bкрасн(?:ый|ым|ое|ая|ые)\b"
    r"|\bпада\w+|\bумирает\b"
)
_GREEN_OUT = re.compile(
    r"(?i)\bPASSe?s?\b|\bPASSED\b|\bbaseline\b|\bholds\b|\bunmutated\b"
    r"|\blegitimate\b|\bboth\s+ways\b|\bnon-?vacuous\b"
    r"|\bзел[её]н\w*|\bбез\s+мутаци\w*|\bсо?\s*снят\w*|\bвыжива\w+"
    r"|\bобе\s+сторон\w*|\bне\s+вакуум\w*"
)
_MUT_ESCAPE = "mutation-one-sided-ok:"

# Slicing structure regexes (§2.1) — not part of the frozen Rule M vocabulary,
# but their forms are pinned by the spec text itself.
_TABLE_ROW_RE = re.compile(r"^\s*\|")
_HEADING_RE = re.compile(r"^ {0,3}#{1,6}\s")

RULE_ONE_SIDED = "one-sided-mutation-declaration"


@dataclass(frozen=True)
class Finding:
    offset: int
    rule_id: str
    evidence: str


def _iter_units(spec_text: str) -> list[tuple[int, str]]:
    """Single canonical slicer for Rule M units (§1g), shared by
    `find_one_sided_mutations` and `summarize_mutation_units`. Returns
    (anchor_offset, unit_text) pairs in document order."""
    # splitlines(), not split("\n"): the §1b baseline was measured with it, and
    # split("\n") appends a phantom trailing "" for newline-terminated files, which
    # ends a paragraph unit one blank early (measured: declarations 62 vs 60).
    raw_lines = spec_text.splitlines()
    n = len(raw_lines)

    offsets: list[int] = []
    pos = 0
    for ln in raw_lines:
        offsets.append(pos)
        pos += len(ln) + 1

    units: list[tuple[int, str]] = []

    # §2.1 frozen slicer: ONE forward pass, anchor test FIRST. A table row that
    # does not itself match _MUT_DECL is not a unit, whatever its header says —
    # the table-block/header-inheritance reading inflated `declarations` from 60
    # to 221 on the live corpus and invalidated the §1b measurement.
    i = 0
    while i < n:
        line = raw_lines[i]
        if not _MUT_DECL.search(line):
            i += 1
            continue
        if _TABLE_ROW_RE.match(line):
            units.append((offsets[i], line))
            i += 1
            continue
        if _HEADING_RE.match(line):
            j = i + 1
            while j < n and not _HEADING_RE.match(raw_lines[j]):
                j += 1
            units.append((offsets[i], "\n".join(raw_lines[i:j])))
            i = j
            continue
        # paragraph / list block: stops on a heading or on two consecutive
        # blanks — NOT on a table row (§2.1).
        j = i + 1
        blanks = 0
        while j < n:
            if _HEADING_RE.match(raw_lines[j]):
                break
            if not raw_lines[j].strip():
                blanks += 1
                if blanks >= 2:
                    break
            else:
                blanks = 0
            j += 1
        units.append((offsets[i], "\n".join(raw_lines[i:j])))
        i = j

    return units


# §1g: ONE canonical classifier. Both public functions route through it, so the
# red/escape/green decision cannot drift between "what the lint reports" and
# "what the summary counts" — and a mutation of the rule has exactly one site.
NOT_OUTCOME = "not_outcome"
ESCAPED = "escaped"
TWO_SIDED = "two_sided"
ONE_SIDED = "one_sided"


def _classify_unit(unit_text: str) -> str:
    """Classify one declaration unit. Order is load-bearing: the _RED_OUT filter
    runs FIRST (prose about mutations names no measured outcome and is excluded
    by construction), then the enumerable escape, then two-sidedness."""
    if not _RED_OUT.search(unit_text):
        return NOT_OUTCOME
    if _MUT_ESCAPE in unit_text:
        return ESCAPED
    if _GREEN_OUT.search(unit_text):
        return TWO_SIDED
    return ONE_SIDED


def find_one_sided_mutations(
    spec_text: str, hal_root: object = None,  # noqa: ARG001 - sibling parity w/ closure_evidence_verifier
) -> list[Finding]:
    """Return findings for mutation-check declarations naming only the red
    outcome (no green/unmutated control), unescaped."""
    findings: list[Finding] = []
    for offset, unit_text in _iter_units(spec_text):
        if _classify_unit(unit_text) != ONE_SIDED:
            continue
        first_line = unit_text.split("\n", 1)[0].strip()[:120]
        findings.append(Finding(
            offset=offset, rule_id=RULE_ONE_SIDED, evidence=first_line,
        ))
    findings.sort(key=lambda f: f.offset)
    return findings


def summarize_mutation_units(spec_text: str) -> dict:
    """Non-vacuity summary (gate blockers 2/4): distinguishes "clean" from
    "unit never inspected" and makes escapes enumerable. `escaped` is counted
    INSIDE `outcome_declarations` — a unit with a red outcome and an escape
    increments outcome_declarations AND escaped, and is neither one_sided nor
    two_sided."""
    declarations = 0
    outcome_declarations = 0
    one_sided = 0
    two_sided = 0
    escaped = 0
    for _offset, unit_text in _iter_units(spec_text):
        declarations += 1
        kind = _classify_unit(unit_text)
        if kind == NOT_OUTCOME:
            continue
        outcome_declarations += 1
        if kind == ESCAPED:
            escaped += 1
        elif kind == TWO_SIDED:
            two_sided += 1
        else:
            one_sided += 1
    return {
        "declarations": declarations,
        "outcome_declarations": outcome_declarations,
        "one_sided": one_sided,
        "two_sided": two_sided,
        "escaped": escaped,
    }


# GH1483 §3.6: the live-corpus band keeps only its NON-VACUITY floors; the shape of
# the partition is held by these identities instead of by absolute ceilings (class
# #1368). Comparison is EXACT: `<=` would accept a LOST unit, and the partition has
# to be complete in BOTH directions (double-counting AND loss).
_MUTATION_IDENTITIES = (
    "outcome_declarations == one_sided + two_sided + escaped",
    "outcome_declarations <= declarations",
    "two_sided <= outcome_declarations",
)


def check_mutation_identities(summary: Mapping[str, Any]) -> list[str]:
    """Names of the Rule M identities VIOLATED by `summary` ([] when clean)."""
    declarations = int(summary.get("declarations", 0))
    outcome_declarations = int(summary.get("outcome_declarations", 0))
    one_sided = int(summary.get("one_sided", 0))
    two_sided = int(summary.get("two_sided", 0))
    escaped = int(summary.get("escaped", 0))

    violated: list[str] = []
    if outcome_declarations != one_sided + two_sided + escaped:
        violated.append(_MUTATION_IDENTITIES[0])
    if outcome_declarations > declarations:
        violated.append(_MUTATION_IDENTITIES[1])
    if two_sided > outcome_declarations:
        violated.append(_MUTATION_IDENTITIES[2])
    return violated
