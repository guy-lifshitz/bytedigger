"""verdict_parser.py — parse per-finding verdicts from cycle-2 reviewer output.

Public API:
    ReviewerVerdict  — TypedDict with per_finding, n_resolved, n_total, final_verdict.
    parse_per_finding_verdicts(reviewer_output) -> ReviewerVerdict
"""
from __future__ import annotations

import re

try:
    from typing import TypedDict
except ImportError:  # pragma: no cover
    from typing_extensions import TypedDict  # type: ignore[assignment]


class ReviewerVerdict(TypedDict):
    per_finding: dict[str, bool]   # id -> True if RESOLVED
    n_resolved: int
    n_total: int
    final_verdict: str             # "PASS" | "REVISE" | "UNPARSED"


# FINDING_<id>: RESOLVED|UNRESOLVED (multiline, case-insensitive)
_FINDING_RE = re.compile(
    r"^\s*FINDING_(?P<id>\S+?):\s*(?P<verdict>RESOLVED|UNRESOLVED)\b",
    re.MULTILINE | re.IGNORECASE,
)

# VERDICT: PASS|REVISE (whole line, multiline, case-insensitive)
_VERDICT_RE = re.compile(
    r"^\s*VERDICT:\s*(?P<verdict>PASS|REVISE)\s*$",
    re.MULTILINE | re.IGNORECASE,
)

# Trailing punctuation to strip from finding id
_TRAILING_PUNCT_RE = re.compile(r"[:.,']+$")


def parse_per_finding_verdicts(reviewer_output: str) -> ReviewerVerdict:
    """Parse structured per-finding verdicts from restricted reviewer output.

    - Empty input → UNPARSED.
    - Sanity override: explicit VERDICT=PASS with n_resolved < n_total → coerce to REVISE.
    - No VERDICT line: infer PASS if all resolved, REVISE if any unresolved, UNPARSED if none.
    """
    if not reviewer_output or not reviewer_output.strip():
        return ReviewerVerdict(
            per_finding={}, n_resolved=0, n_total=0, final_verdict="UNPARSED"
        )

    per_finding: dict[str, bool] = {}
    for m in _FINDING_RE.finditer(reviewer_output):
        raw_id = m.group("id")
        fid = _TRAILING_PUNCT_RE.sub("", raw_id)
        resolved = m.group("verdict").upper() == "RESOLVED"
        per_finding[fid] = resolved

    n_total = len(per_finding)
    n_resolved = sum(1 for v in per_finding.values() if v)

    # Look for explicit VERDICT line
    vm = _VERDICT_RE.search(reviewer_output)
    if vm:
        explicit = vm.group("verdict").upper()
        # Sanity override: PASS with unresolved findings → REVISE
        if explicit == "PASS" and n_total > 0 and n_resolved < n_total:
            final_verdict = "REVISE"
        else:
            final_verdict = explicit
    else:
        # Infer from per-finding counts
        if n_total > 0 and n_resolved == n_total:
            final_verdict = "PASS"
        elif n_total > 0:
            final_verdict = "REVISE"
        else:
            final_verdict = "UNPARSED"

    return ReviewerVerdict(
        per_finding=per_finding,
        n_resolved=n_resolved,
        n_total=n_total,
        final_verdict=final_verdict,
    )
