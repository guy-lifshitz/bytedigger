"""Helper-extraction verifier for HAL engine_py spec_lint (7099566a, §1aa gate).

Detects when spec §2 contains inline bash arithmetic ($((…))) or bare emit_event/emit_signal
calls but omits a `declare -f <helper>` forcing-function AC. Lib-tier: no engine_py deps,
stdlib only.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# §2 heading (matches ## §2, ### §2.1, ### §2-AMEND, etc.)
_SEC2_RE = re.compile(r'^\s*#+\s*§\s*2\b', re.MULTILINE)

# §3 / Acceptance heading
_SEC3_RE = re.compile(r'^\s*#+\s*(?:§\s*3\b|acceptance\s+criteria)', re.IGNORECASE | re.MULTILINE)

# Bash inline-math + bare emit calls
_TRIGGER_RE = re.compile(r'\$\(\(|\bemit_event\b|\bemit_signal\b')

# Helper-mandate forcing-function
_DECLARE_F_RE = re.compile(r'declare\s+-f\b')


@dataclass(frozen=True)
class HelperFinding:
    """A single §1aa helper-extraction finding from a spec."""

    offset: int    # byte offset in spec text of the first triggering inline site
    rule_id: str   # always "helper-extraction-missing"
    evidence: str  # "<n>-inline-sites-no-declare-f-AC"


def find_missing_helpers(spec_text: str, hal_root: Path) -> list[HelperFinding]:
    """Return helper-extraction findings for *spec_text*.

    Algorithm:
    1. Find §2 heading; if absent → [] (cannot assess, no false-positive).
    2. Compute §2 span: from §2 heading to first §3/acceptance heading or EOF.
    3. Find all triggers in §2 span (absolute offsets). If none → [].
    4. Escape: if declare -f present anywhere in full spec → [] (helper mandated).
    5. Else → one HelperFinding at triggers[0].start() with count evidence.
    """
    # Step 1
    m2 = _SEC2_RE.search(spec_text)
    if m2 is None:
        return []

    # Step 2
    sec2_start = m2.start()
    m3 = _SEC3_RE.search(spec_text, m2.end())
    sec2_end = m3.start() if m3 else len(spec_text)

    # Step 3 — finditer with pos/endpos gives absolute offsets in spec_text
    triggers = list(_TRIGGER_RE.finditer(spec_text, sec2_start, sec2_end))
    if not triggers:
        return []

    # Step 4 — whole-spec escape (declare -f may live in §3 AC table)
    if _DECLARE_F_RE.search(spec_text):
        return []

    # Step 5
    return [
        HelperFinding(
            offset=triggers[0].start(),
            rule_id="helper-extraction-missing",
            evidence=f"{len(triggers)}-inline-sites-no-declare-f-AC",
        )
    ]
