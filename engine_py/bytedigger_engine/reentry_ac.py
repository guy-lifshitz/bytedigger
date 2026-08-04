from __future__ import annotations

import re

# GH823 §1ab/§1ac — re-entry AC mandate lint (mirrors scope_inverse.py
# conventions). Rationale for the compound "cycle-cap"/"cycle cap" form (not
# bare "cap"): bare "cap" is FP-prone against "capture"/"escape" — the
# compound form keeps detection deterministic while still word-bounded.
STATEFUL_TOKENS_RE = re.compile(
    r"(?i)\b(sentinel|cache[sd]?|counter|resume[sd]?|dbos|governor|frozen[- ]ref|"
    r"findings[- ]thread|durable|cycle[- ]cap)\b"
)

_SECTION2_START_RE = re.compile(r"^#+.*((§|\b)2\b|implementation|design)", re.IGNORECASE)
_SECTION3_START_RE = re.compile(r"^#+.*((§|\b)3\b|acceptance)", re.IGNORECASE)
_REENTRY_HEADER_RE = re.compile(r"^#+.*re-?entry", re.IGNORECASE)

REQUIRED_PATH_TOKENS = ("fresh", "retry", "resume", "replay")


def stateful_probe(text: str) -> bool:
    """True iff `text` mentions any durable-state token (§1ab probe)."""
    return bool(STATEFUL_TOKENS_RE.search(text or ""))


def _section2_slice(spec_text: str) -> tuple[str, int]:
    """Return (text of the §2/implementation region, 1-based header line).

    Falls back to the whole text at line 1 when no §2-style header is found
    (deterministic fail-closed toward detection, §2.1).
    """
    lines = spec_text.splitlines()
    start_idx = None
    for i, line in enumerate(lines):
        if _SECTION2_START_RE.search(line):
            start_idx = i
            break
    if start_idx is None:
        return spec_text, 1
    end_idx = len(lines)
    for j in range(start_idx + 1, len(lines)):
        if _SECTION3_START_RE.search(lines[j]):
            end_idx = j
            break
    return "\n".join(lines[start_idx:end_idx]), start_idx + 1


def has_reentry_section(spec_text: str) -> bool:
    """True iff a `## Re-entry ACs`-style header or literal phrase is present."""
    if "Re-entry AC" in spec_text:
        return True
    for line in spec_text.splitlines():
        if _REENTRY_HEADER_RE.search(line):
            return True
    return False


def scan_reentry_ac(spec_text: str) -> list[dict[str, object]]:
    """Return findings shaped like scope_inverse.scan_scope_inverse.

    Findings: `{"line": int, "reason": str, "rule": str}`.
    """
    sec2, hline = _section2_slice(spec_text)
    if not stateful_probe(sec2):
        return []
    if not has_reentry_section(spec_text):
        return [
            {
                "line": hline,
                "reason": (
                    "spec touches durable state (§1ab) but has no '## Re-entry ACs' "
                    "section covering (a) fresh, (b) in-phase retry, (c) auto-resume, "
                    "(d) DBOS-replay entry paths"
                ),
                "rule": "spec_reentry_missing_section",
            }
        ]
    findings: list[dict[str, object]] = []
    lower = spec_text.lower()
    for token in REQUIRED_PATH_TOKENS:
        if token not in lower:
            findings.append(
                {
                    "line": hline,
                    "reason": f"'## Re-entry ACs' section present but missing required token '{token}' (§1ab)",
                    "rule": "spec_reentry_missing_token",
                }
            )
    if "idempoten" not in lower:
        findings.append(
            {
                "line": hline,
                "reason": "'## Re-entry ACs' section present but missing required token 'idempoten*' (§1ab counter idempotency)",
                "rule": "spec_reentry_missing_token",
            }
        )
    return findings
