"""helper_extraction.py — §1aa mandate lint: detect inline-math and inline-emit
constructs inside a spec's §2 design section.

Part of 5F4912B3 — helper-extraction lint.
"""
from __future__ import annotations

import re

# Regex: start of a §2 header
_RE_SEC2_START = re.compile(
    r"^#+\s*(§?\s*2\b|section\s+2\b)", re.IGNORECASE | re.MULTILINE
)

# Regex: start of §3 or higher (ends the §2 span)
_RE_SEC_AFTER2 = re.compile(
    r"^#+\s*(§?\s*[3-9]\b|§?\s*1\d\b|section\s+[3-9]\b)", re.IGNORECASE | re.MULTILINE
)

# Regex: inline-emit construct
_RE_EMIT = re.compile(r"\bemit_(event|signal)\b")


def extract_section_2(spec_text: str) -> str:
    """Return the §2 design section text (body lines, not including the header).

    Returns '' if no §2 header is found.
    """
    lines = spec_text.splitlines()
    in_sec2 = False
    body: list[str] = []

    for line in lines:
        if not in_sec2:
            if _RE_SEC2_START.match(line):
                in_sec2 = True
        else:
            if _RE_SEC_AFTER2.match(line):
                break
            body.append(line)

    if not in_sec2:
        return ""
    return "\n".join(body)


def scan_helper_extraction(spec_text: str) -> list[dict]:
    """Scan spec_text for unguarded inline-math / inline-emit in the §2 section.

    Returns [] if:
      - No §2 header found, OR
      - The full spec_text contains the literal substring 'declare -f' (mandate present).

    Otherwise returns a list of finding dicts:
      {"line": <1-based int>, "construct": <"inline-math"|"inline-emit">, "text": <stripped line>}
    """
    # Mandate check: if 'declare -f' appears anywhere in the spec, no findings.
    if "declare -f" in spec_text:
        return []

    lines = spec_text.splitlines()
    findings: list[dict] = []
    in_sec2 = False
    found_sec2 = False

    for i, line in enumerate(lines):
        lineno = i + 1  # 1-based

        if not in_sec2:
            if _RE_SEC2_START.match(line):
                in_sec2 = True
                found_sec2 = True
        else:
            if _RE_SEC_AFTER2.match(line):
                in_sec2 = False
            else:
                # Check for violations within §2 only
                if "$((" in line:
                    findings.append({
                        "line": lineno,
                        "construct": "inline-math",
                        "text": line.strip(),
                    })
                elif _RE_EMIT.search(line):
                    findings.append({
                        "line": lineno,
                        "construct": "inline-emit",
                        "text": line.strip(),
                    })

    if not found_sec2:
        return []

    return findings
