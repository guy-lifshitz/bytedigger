"""findings_extractor.py — parse cycle-1 reviewer output into structured findings.

Public API:
    Finding       — TypedDict with id, type, evidence, required_action.
    extract_findings_section(review_text) -> str
    extract_structured_findings_raw(review_text) -> list[dict] | None
    extract_structured_findings(review_text) -> list[Finding] | None
    extract_findings_for_writer(review_text) -> list[Finding]
"""
from __future__ import annotations

import json
import re
from typing import Sequence

try:
    from typing import TypedDict
except ImportError:  # pragma: no cover
    from typing_extensions import TypedDict  # type: ignore[assignment]


class Finding(TypedDict):
    id: str
    type: str
    evidence: str
    required_action: str


# ── section regexes ───────────────────────────────────────────────────────────

# Matches "## Findings" but NOT "## Findings (structured)"
_FREE_TEXT_SECTION_RE = re.compile(
    r"^##\s+Findings(?!\s*\()\s*\n(.*?)(?=\n##\s|\Z)",
    re.MULTILINE | re.DOTALL,
)

# Matches "## Findings (structured)" + ```json fence
_STRUCTURED_SECTION_RE = re.compile(
    r"^##\s*Findings\s*\(\s*structured\s*\)\s*\n"
    r"```(?:json)?\s*\n(.*?)```",
    re.MULTILINE | re.DOTALL,
)

# Numbered / bulleted list items anchored to start-of-line (no leading whitespace)
_LIST_ITEM_RE = re.compile(
    r"^(?:\d+[.)]|[-*])\s+(.+?)(?=\n(?:\d+[.)]|[-*])\s+|\Z)",
    re.MULTILINE | re.DOTALL,
)

_NONE_BODIES = {"none", "(none)", "n/a", "-"}


def extract_findings_section(review_text: str) -> str:
    """Return raw body of the free-text ## Findings section.

    Returns "" if section absent or body is a 'none' placeholder.
    Does NOT match ## Findings (structured).
    """
    if not review_text:
        return ""
    m = _FREE_TEXT_SECTION_RE.search(review_text)
    if not m:
        return ""
    body = m.group(1).strip()
    if body.lower() in _NONE_BODIES:
        return ""
    return body


def extract_structured_findings_raw(review_text: str) -> list[dict] | None:
    """Schema-agnostic parser core for the ``## Findings (structured)`` JSON block.

    Returns ``None`` if the block is absent, ``review_text`` is falsy, the JSON
    is malformed, or the root is not a list.  Otherwise returns every dict entry
    in the list completely unmodified (no key coercion, no missing-key defaults,
    extra keys preserved, int/list values stay as-is); non-dict entries are
    filtered out.  Callers apply their own schema projection.
    """
    if not review_text:
        return None
    m = _STRUCTURED_SECTION_RE.search(review_text)
    if not m:
        return None
    raw_json = m.group(1).strip()
    try:
        data = json.loads(raw_json)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, list):
        return None
    return [item for item in data if isinstance(item, dict)]


def extract_structured_findings(review_text: str) -> list[Finding] | None:
    """Parse the ## Findings (structured) ```json``` fenced block.

    Returns None if absent, unparseable, or root is not a list.
    Each item is coerced to a Finding TypedDict (missing fields default to "").
    Delegates parsing to ``extract_structured_findings_raw``; applies the
    spec-review schema projection here.
    """
    raw = extract_structured_findings_raw(review_text)
    if raw is None:
        return None
    return [
        Finding(
            id=str(i.get("id", "")),
            type=str(i.get("type", "")),
            evidence=str(i.get("evidence", "")),
            required_action=str(i.get("required_action", "")),
        )
        for i in raw
    ]


def extract_findings_for_writer(review_text: str) -> list[Finding]:
    """Primary entry point.

    Prefer structured block (## Findings (structured)) if present and non-empty.
    Fallback: parse numbered/bulleted list from the free-text ## Findings section.
    Returns [] if section absent or body is a 'none' placeholder.
    """
    if not review_text:
        return []

    # Try structured first
    structured = extract_structured_findings(review_text)
    if structured is not None and len(structured) > 0:
        return structured

    # Fallback: free-text section
    body = extract_findings_section(review_text)
    if not body:
        return []

    items = _LIST_ITEM_RE.findall(body)
    if not items:
        # No list items found — treat whole section as single finding
        text = body.strip()
        return [
            Finding(id="0", type="issue", evidence=text, required_action=text)
        ]

    findings: list[Finding] = []
    for idx, raw in enumerate(items):
        text = raw.strip()
        findings.append(
            Finding(id=str(idx), type="issue", evidence=text, required_action=text)
        )
    return findings
