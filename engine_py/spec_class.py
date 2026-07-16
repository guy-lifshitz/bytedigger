from __future__ import annotations

import re

_CLASS_RE = re.compile(r"class:.*\b(systematic|process|tooling|patch)\b", re.IGNORECASE)


def has_class_declaration(spec_text: str) -> bool:
    """True iff some line matches class: followed by a valid enum value (case-insensitive)."""
    for line in spec_text.splitlines():
        if _CLASS_RE.search(line):
            return True
    return False


def has_chokepoint_declaration(spec_text: str) -> bool:
    """True iff 'chokepoint' or 'parent' appears anywhere in the text (case-insensitive)."""
    lower = spec_text.lower()
    return "chokepoint" in lower or "parent" in lower


def _class_line(spec_text: str) -> int:
    """1-based line number of the first class-declaration line, else 1."""
    for i, line in enumerate(spec_text.splitlines(), start=1):
        if _CLASS_RE.search(line):
            return i
    return 1


def scan_spec_class(spec_text: str) -> list[dict]:
    """Return findings if spec front-matter is missing Class: and/or chokepoint/parent."""
    has_class = has_class_declaration(spec_text)
    has_choke = has_chokepoint_declaration(spec_text)
    if has_class and has_choke:
        return []
    if not has_class and not has_choke:
        reason = (
            "spec front-matter missing Class: declaration "
            "(one of SYSTEMATIC/PROCESS/TOOLING/PATCH) "
            "and chokepoint/parent candidate (§1c)"
        )
    elif not has_class:
        reason = (
            "spec front-matter missing Class: declaration "
            "(one of SYSTEMATIC/PROCESS/TOOLING/PATCH) (§1c)"
        )
    else:
        reason = "spec front-matter missing chokepoint/parent candidate (§1c)"
    return [{"line": _class_line(spec_text), "reason": reason}]
