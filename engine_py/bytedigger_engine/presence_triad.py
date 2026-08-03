"""presence_triad.py — §1y presence-triad lint: every side-effect/emit AC in a
spec's §3 Acceptance Criteria table must carry a Point:/Host:/Test-path: block.

Part of GH540 — spec-lint batch.
"""
from __future__ import annotations

import re

# Regex: start of a §3 header
_RE_SEC3_START = re.compile(
    r"^#+\s*(§?\s*3\b|section\s+3\b)", re.IGNORECASE
)

# Regex: any markdown heading (ends the §3 span)
_RE_HEADING = re.compile(r"^#+\s")

_TRIAD_TOKENS = ["Point:", "Host:", "Test-path:"]

_SIDE_EFFECT_TOKENS = [
    "emit_",
    "emit ",
    "event",
    "jsonl",
    "append",
    "side-effect",
    "side effect",
]


def _is_ac_line(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("|") and "AC" in line


def _is_side_effect_ac(line: str) -> bool:
    lowered = line.lower()
    return any(token.lower() in lowered for token in _SIDE_EFFECT_TOKENS)


def scan_presence_triad(spec_text: str) -> list[dict]:
    """Scan spec_text §3 section for side-effect ACs missing the Point/Host/
    Test-path presence triad.

    Returns a list of finding dicts:
      {"line": <1-based int>, "rule_id": "presence-triad-missing",
       "missing": [<subset of Point:/Host:/Test-path: in order>],
       "evidence": <str>}
    """
    lines = spec_text.splitlines()

    start_idx = None
    for i, line in enumerate(lines):
        if _RE_SEC3_START.match(line):
            start_idx = i
            break

    if start_idx is None:
        return []

    end_idx = len(lines)
    for i in range(start_idx + 1, len(lines)):
        if _RE_HEADING.match(lines[i]):
            end_idx = i
            break

    section_lines = lines[start_idx + 1:end_idx]
    section_offset = start_idx + 1  # 0-based index of section_lines[0] in `lines`

    ac_indices = [
        idx for idx, line in enumerate(section_lines) if _is_ac_line(line)
    ]

    findings: list[dict] = []
    for pos, ac_idx in enumerate(ac_indices):
        ac_line = section_lines[ac_idx]
        if not _is_side_effect_ac(ac_line):
            continue

        span_end = ac_indices[pos + 1] if pos + 1 < len(ac_indices) else len(section_lines)
        span_lines = section_lines[ac_idx:span_end]
        span_text = "\n".join(span_lines)

        missing = [tok for tok in _TRIAD_TOKENS if tok not in span_text]
        if missing:
            lineno = section_offset + ac_idx + 1  # 1-based
            findings.append({
                "line": lineno,
                "rule_id": "presence-triad-missing",
                "missing": missing,
                "evidence": "missing " + ",".join(missing),
            })

    return findings
