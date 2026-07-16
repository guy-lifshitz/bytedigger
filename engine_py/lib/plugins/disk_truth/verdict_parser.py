"""verdict_parser.py — single-token + structured-block parsers.

Public API:
    parse_single_token_verdict(stdout, allowed) -> str | None
    parse_structured_block(markdown, name) -> dict | list | None

Composes existing checklist_convergence.verdict_parser patterns.
"""
from __future__ import annotations

import json
import re
from typing import Optional, Set, Union


def parse_single_token_verdict(
    stdout: str,
    allowed: Set[str],
) -> Optional[str]:
    """Scan lines top-to-bottom; return first line that matches an allowed token.

    Comparison is case-insensitive (input line is uppercased before comparison).
    allowed set may be lowercase or uppercase — all tokens are uppercased for comparison.
    Returns None if no match found or stdout is empty/whitespace.
    """
    if not stdout or not stdout.strip():
        return None

    allowed_upper = {tok.upper() for tok in allowed}

    for line in stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        upper = stripped.upper()
        if upper in allowed_upper:
            return upper

    return None


def parse_structured_block(
    markdown: str,
    name: str,
) -> Optional[Union[dict, list]]:
    """Parse a named structured JSON block from markdown.

    Matches heading: ## <name> (structured)
    Followed by a ```json ... ``` fenced block.

    Returns parsed JSON (dict or list) if found and valid.
    Returns None if heading absent, fence missing, or JSON unparseable.
    """
    if not markdown:
        return None

    escaped_name = re.escape(name)
    pattern = re.compile(
        r"^##\s+" + escaped_name + r"\s*\(\s*structured\s*\)\s*\n"
        r"```(?:json)?\s*\n(.*?)```",
        re.MULTILINE | re.DOTALL,
    )

    m = pattern.search(markdown)
    if not m:
        return None

    raw_json = m.group(1).strip()
    try:
        data = json.loads(raw_json)
    except (json.JSONDecodeError, ValueError):
        return None

    if isinstance(data, (dict, list)):
        return data

    return None
