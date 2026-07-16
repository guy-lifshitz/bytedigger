"""review_schema.canonical — canonical schema constants for phase_6 review.

These constants are the single source of truth for the review schema used by
phase_6_review._build_review_prompt (PER_ROLE template, STRUCTURED FINDINGS
directive, and the role-findings-count marker regex).

Ship B (812D2503): moved from inline literals in workflows/phase_6_review.py.
"""
from __future__ import annotations

import re

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
