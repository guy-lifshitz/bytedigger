"""forbidden_import.py — lib for detecting forbidden dynamic-import patterns.

Detects spec_from_file_location and exec_module as Python NAME tokens in code,
excluding comments and string literals (including docstrings). Falls back to a
deterministic line-based scan (comment lines skipped) on untokenizable input.
Source-order output. Return-dict keys: line (1-based int), pattern (str), text (str).
"""
from __future__ import annotations

import io
import os
import tokenize


def is_test_file(filename: str) -> bool:
    """Return True iff filename is a test file by naming convention."""
    base = os.path.basename(filename)
    if base.startswith("test_") and base.endswith(".py"):
        return True
    if base.endswith("_test.py"):
        return True
    if base == "conftest.py":
        return True
    return False


def _scan_via_tokenize(text: str) -> list[dict]:
    """Primary scan: matches only Python NAME tokens (comments + string literals excluded).

    Iterates tokenize.generate_tokens over the source text. Only tokens with
    tok.type == tokenize.NAME and tok.string in PATTERNS produce findings.
    Returns findings sorted by (line, col) in source order.
    """
    PATTERNS = ("spec_from_file_location", "exec_module")
    lines = text.splitlines()
    collected: list[tuple[int, int, str]] = []
    for tok in tokenize.generate_tokens(io.StringIO(text).readline):
        if tok.type == tokenize.NAME and tok.string in PATTERNS:
            row, col = tok.start
            collected.append((row, col, tok.string))
    collected.sort(key=lambda t: (t[0], t[1]))
    findings: list[dict] = []
    for line, col, pattern in collected:
        findings.append({
            "line": line,
            "pattern": pattern,
            "text": lines[line - 1].strip() if line - 1 < len(lines) else "",
        })
    return findings


def _scan_via_lines(text: str) -> list[dict]:
    """Fallback scan: per-line substring check (spec_from_file_location before exec_module).

    Pure comment lines (strip().startswith('#')) are skipped. Cannot exclude
    string-literal mentions — degraded but fail-loud on malformed input.
    """
    findings: list[dict] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if line.strip().startswith("#"):
            continue
        if "spec_from_file_location" in line:
            findings.append({
                "line": lineno,
                "pattern": "spec_from_file_location",
                "text": line.strip(),
            })
        if "exec_module" in line:
            findings.append({
                "line": lineno,
                "pattern": "exec_module",
                "text": line.strip(),
            })
    return findings


def scan_forbidden_dynamic_import(text: str) -> list[dict]:
    """Scan text for forbidden dynamic-import patterns.

    Primary path: tokenize-based scan — matches spec_from_file_location and
    exec_module only as Python NAME tokens (comments and string literals excluded),
    results in source order, deterministic by (line, col).

    Fallback (tokenize.TokenError / IndentationError / SyntaxError): line-based
    scan with pure-comment-line skip — fail-loud, never drops a real call on
    malformed input.

    Returns a list of dicts with keys: line (1-based int), pattern (str), text (str).
    """
    try:
        return _scan_via_tokenize(text)
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return _scan_via_lines(text)
