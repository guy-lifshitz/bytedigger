from __future__ import annotations

import re


def has_files_in_scope(spec_text: str) -> bool:
    """True iff any line matches the files-in-scope header regex (case-insensitive)."""
    pattern = re.compile(r"^#+.*files in scope", re.IGNORECASE)
    for line in spec_text.splitlines():
        if pattern.search(line):
            return True
    return False


def has_not_in_scope(spec_text: str) -> bool:
    """True iff the phrase 'not in scope' appears anywhere in the text."""
    return "not in scope" in spec_text.lower()


def scan_scope_inverse(spec_text: str) -> list[dict]:
    """Return findings if spec has a files-in-scope header but no inverse block."""
    if has_files_in_scope(spec_text) and not has_not_in_scope(spec_text):
        pattern = re.compile(r"^#+.*files in scope", re.IGNORECASE)
        for i, line in enumerate(spec_text.splitlines(), start=1):
            if pattern.search(line):
                return [
                    {
                        "line": i,
                        "reason": (
                            "spec has a Files-in-scope section but no "
                            "'Files NOT in scope' allowlist-inverse block (§1v)"
                        ),
                    }
                ]
    return []
