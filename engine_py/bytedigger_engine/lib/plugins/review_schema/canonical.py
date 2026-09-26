"""review_schema.canonical — canonical schema constants for phase_6 review.

These constants are the single source of truth for the review schema used by
phase_6_review._build_review_prompt (PER_ROLE template, STRUCTURED FINDINGS
directive, and the role-findings-count marker regex).

Ship B (812D2503): moved from inline literals in workflows/phase_6_review.py.
"""
from __future__ import annotations

import re

from bytedigger_engine.lib.llm_output_normalize import fence_mask, strip_emphasis

# ── PER_ROLE_SCHEMA_TEMPLATE ──────────────────────────────────────────────────
# Verbatim move from workflows/phase_6_review.py lines 854-869 (pre-Ship-B).
# Do NOT compress — content must be byte-equivalent to what was inline.
PER_ROLE_SCHEMA_TEMPLATE: str = (
    "    # <role-name> Review\n"
    "    \n"
    "    ### SEVERITY: <CRITICAL|HIGH|MEDIUM|LOW> — <title>\n"
    "    > path:line: <verbatim code>\n"
    "    Confidence: <HIGH|MEDIUM|LOW>\n"
    "    Description: ...\n"
    "    \n"
    "    You MUST emit EVERY finding as its own ### SEVERITY: block in exactly this form.\n"
    "    Findings written only in prose / narrative are INVISIBLE to the aggregator and\n"
    "    will be treated as NOT REPORTED. Do not summarise 'I found N issues' without\n"
    "    the N blocks.\n"
    "    \n"
    "    VERDICT: <PASS|PARTIAL|FAIL>\n"
    "    <!-- role-findings-count: N --> where N is the exact number of ### SEVERITY:\n"
    "    blocks you wrote above. Required last line of the role file; the engine uses\n"
    "    it to verify nothing was dropped.\n"
)

# ── PARALLEL_DISPATCH_FRAMING_TEMPLATE ───────────────────────────────────────
# Verbatim move from `workflows/phase_6_review.py` lines 850-862 (pre-Ship-B3).
# Do NOT compress — content must be byte-equivalent to what was inline.
PARALLEL_DISPATCH_FRAMING_TEMPLATE: str = (
    "PARALLEL DISPATCH — invoke all {reviewer_count} Agent calls in a single message "
    "so they run in parallel. Pass SPEC, RED, and GREEN file paths by "
    "reference in each sub-agent prompt — do NOT inline file contents.\n"
    "\n"
    "Each dispatched Agent MUST use the Write tool to write its findings to:\n"
    "    {abs_reviews_dir}/role-<slug>.md\n"
    "where <slug> is the subagent_type after the colon (e.g. 'code-reviewer' →\n"
    "{abs_reviews_dir}/role-code-reviewer.md). Per-role schema:\n"
    "{per_role_schema}\n\n"
    "Dispatch table (subagent_type — model):\n"
    "{dispatch_table}"
)

# ── STRUCTURED_FINDINGS_JSON_TEMPLATE ────────────────────────────────────────
# Verbatim move from workflows/phase_6_review.py lines 992-1002 (pre-Ship-B).
# Do NOT compress — content must be byte-equivalent to what was inline.
STRUCTURED_FINDINGS_JSON_TEMPLATE: str = (
    "## Findings (structured)\n"
    "```json\n"
    "[\n"
    "  {\n"
    '    "id": "1",\n'
    '    "severity": "CRITICAL|HIGH|MEDIUM|LOW",\n'
    '    "path": "<file/path.py>",\n'
    '    "description": "<short single-sentence explanation>"\n'
    "  }\n"
    "]\n"
    "```\n"
)

# ── STRUCTURED_FINDINGS_DIRECTIVE_SHORT ───────────────────────────────────────
# NEW short directive (≤500B). Preserves the 5 LLM-load-bearing invariants:
#   1. section header  "## Findings (structured)"
#   2. all four keys   "id", "severity", "path", "description"
#   3. empty-list      "[]"
#   4. alongside clause "sits"
#   5. severity scope  "CRITICAL/HIGH/MEDIUM"
# Keys are quoted per R-B1 (Opus 5.2 advisory). 194B reference variant.
STRUCTURED_FINDINGS_DIRECTIVE_SHORT: str = (
    'After per-role findings, emit `## Findings (structured)` JSON with '
    'CRITICAL/HIGH/MEDIUM findings. Keys: "id", "severity", "path", '
    '"description". Use `[]` if PASS. Sits alongside per-role schema.'
)

# ── ROLE_FINDINGS_COUNT_MARKER_RE ─────────────────────────────────────────────
# Verbatim move from workflows/phase_6_review.py line 1153 (pre-Ship-B).
# Matches <!-- role-findings-count: N --> with whitespace tolerance.
# Bit-equivalent to the original _ROLE_SELFCOUNT_RE inline re.compile.
ROLE_FINDINGS_COUNT_MARKER_RE: re.Pattern[str] = re.compile(
    r"<!--\s*role-findings-count:\s*(\d+)\s*-->"
)

# ── SEVERITY_HDR_* — GH970 tolerant SEVERITY-header parse ───────────────────
# Single source of truth for the "### SEVERITY: <LEVEL> — <title>" header
# pattern, tolerant of a 2-4 hash prefix (the prescribed form stays ### per
# PER_ROLE_SCHEMA_TEMPLATE above; ## and #### are also accepted so a
# well-formed-but-off-prescription header is not structurally invisible to
# the aggregator). Group contract: group(1)=severity, group(2)=title.
SEVERITY_LEVELS: str = "CRITICAL|HIGH|MEDIUM|LOW"
SEVERITY_HDR_CORE: str = rf"#{{2,4}}\s+SEVERITY:\s*({SEVERITY_LEVELS})\s*[—-]\s*(.+?)\s*$"
SEVERITY_HDR_LINE_RE: re.Pattern[str] = re.compile(r"^" + SEVERITY_HDR_CORE, re.IGNORECASE)
SEVERITY_HDR_MULTILINE_RE: re.Pattern[str] = re.compile(r"^" + SEVERITY_HDR_CORE, re.IGNORECASE | re.MULTILINE)

# ── SEVERITY_MALFORMED_LINE_RE / lint_role_report — GH970 D2 ────────────────
# Deterministic lint (Principle A) for role reports: catches lines that LOOK
# like a severity header (SEVERITY: <LEVEL> — ..., with an optional #/*
# prefix outside the SEVERITY_HDR_LINE_RE-accepted 2-4 hash range) but do NOT
# parse under SEVERITY_HDR_LINE_RE — these findings are structurally invisible
# to the aggregator.
SEVERITY_MALFORMED_LINE_RE: re.Pattern[str] = re.compile(
    rf"^\s*(?:#{{1,6}}\s*|\*{{1,2}}\s*)?SEVERITY\s*:?\s*({SEVERITY_LEVELS})\s*[—-]",
    re.IGNORECASE,
)


def lint_role_report(content: str) -> list[str]:
    """Return lines that LOOK like severity headers but do NOT parse under
    SEVERITY_HDR_LINE_RE (rstrip each line before both matches)."""
    flagged: list[str] = []
    for line in content.splitlines():
        stripped = line.rstrip("\n\r")
        if SEVERITY_MALFORMED_LINE_RE.match(stripped) and not SEVERITY_HDR_LINE_RE.match(stripped):
            flagged.append(stripped)
    return flagged


# ── canonicalize_severity_headers — bd#84 ────────────────────────────────────
# Models drop the `SEVERITY:` word or wrap the header in bold; such findings
# were structurally invisible (counted as zero → a false clean review). The
# aggregator rewrites finding-shaped lines to the canonical header before
# parsing, with the model-output dressing rules of llm_output_normalize.
#
# Finding-shaped lines: a `##`-`####` heading (the GH970 range — `#` and
# `#####` stay invisible) or a line that opens with bold.
_SEVERITY_HEADING_RE = re.compile(r"^[ ]{0,3}#{2,4}[ \t]+(.*?)[ \t]*$")
_BOLD_LEAD_RE = re.compile(r"^[ ]{0,3}(\*\*.*?)[ \t]*$")
_SEVERITY_TEXT_RE = re.compile(
    r"^(?:(?P<word>SEVERITY)[ \t]*:?[ \t]*)?"
    rf"(?P<level>{SEVERITY_LEVELS})\b"
    r"(?:[ \t]*[—–][ \t]*|[ \t]+-[ \t]+|[ \t]*:[ \t]*)"
    r"(?P<title>\S.*?)[ \t]*$",
    re.IGNORECASE,
)
# The per-role schema's evidence line (`> path:line: <verbatim code>`). A
# heading counts as a finding only when its block carries one: tallies,
# summaries and "none found" headings never do.
_EVIDENCE_LINE_RE = re.compile(r"^>\s+(?:[A-Za-z]:)?[^:]+:\d+:")


def _canonical_severity_line(line: str) -> str | None:
    """The canonical header for a finding-shaped ``line``, else None."""
    m = _SEVERITY_HEADING_RE.match(line) or _BOLD_LEAD_RE.match(line)
    t = m and _SEVERITY_TEXT_RE.match(strip_emphasis(m.group(1)))
    if not t:
        return None
    title = t.group("title").strip().strip("*_").strip()
    return f"### SEVERITY: {t.group('level').upper()} — {title}" if title else None


def canonicalize_severity_headers(text: str) -> str:
    """Rewrite finding-shaped lines to ``### SEVERITY: <LEVEL> — <title>``.

    A finding-shaped line is a ``##``-``####`` heading (the GH970 tolerance
    of ``SEVERITY_HDR_CORE``; ``#`` and ``#####`` stay invisible) or a line
    that opens with bold, whose text starts with ``[SEVERITY[:]] <level>``,
    then a dash or colon and a title: ``### HIGH — t``, ``**SEVERITY: LOW** — t``,
    ``#### Severity: critical: t``. It is rewritten only when its block (up to
    the next heading or finding-shaped line) carries a ``> path:line:``
    evidence line, so tallies (``**HIGH:** 2``), summaries and "none found"
    headings stay as written. A bold line inside a block already opened by a
    finding header belongs to that finding and is not split off (it would
    take the finding's evidence with it). Lines that already parse under
    ``SEVERITY_HDR_LINE_RE``, fenced lines and body lines are untouched.
    """
    if not text:
        return text
    lines = text.split("\n")
    fenced = fence_mask(lines)
    candidate: list[str | None] = []
    in_finding = False  # inside a finding block opened by a canonical header
    for line, f in zip(lines, fenced):
        new = None if f or SEVERITY_HDR_LINE_RE.match(line) else _canonical_severity_line(line)
        is_heading = not f and line.lstrip().startswith("#")
        if new and not is_heading and in_finding:
            new = None  # a bold line inside a finding block is part of that finding
        candidate.append(new)
        if not f and SEVERITY_HDR_LINE_RE.match(line):
            in_finding = True
        elif is_heading:
            in_finding = new is not None

    def has_evidence(i: int) -> bool:
        for j in range(i + 1, len(lines)):
            if fenced[j]:
                continue
            if candidate[j] or lines[j].lstrip().startswith("#"):
                return False
            if _EVIDENCE_LINE_RE.match(lines[j]):
                return True
        return False

    return "\n".join(
        new if new and has_evidence(i) else line
        for i, (line, new) in enumerate(zip(lines, candidate))
    )
