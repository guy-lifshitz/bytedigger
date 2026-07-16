"""RED tests for D6B8DF32: FP-refinement of scan_forbidden_dynamic_import.

Asserts that comments and string literals (including docstrings) are NOT flagged,
while genuine NAME-token occurrences ARE flagged, and that fallback fires on
untokenizable input (Principle A fail-loud).

Each test calls _import_lib() inside the body — deferred per §1q-extension /
D1CF5FDF — so the file collects cleanly and fails at assert time, not import time.
"""
from __future__ import annotations

import sys
from pathlib import Path

# ── path constants (no sys.path mutation at module level) ────────────────────
_HERE = Path(__file__).resolve().parent   # engine_py/tests/
_ENGINE_PY = _HERE.parent                 # engine_py/


# ── deferred import helper ───────────────────────────────────────────────────

def _import_lib():
    """Import scan_forbidden_dynamic_import; deferred so collection never fails."""
    sys.path.insert(0, str(_ENGINE_PY))
    from forbidden_import import scan_forbidden_dynamic_import
    return scan_forbidden_dynamic_import


# ── FP1: pure comment line is NOT flagged ───────────────────────────────────

def test_fp1_pure_comment_spec_from_file_location_not_flagged():
    """FP1: a line that is purely a comment mentioning spec_from_file_location → []."""
    scan = _import_lib()
    text = '# importlib.util.spec_from_file_location("m", p)\n'
    findings = scan(text)
    assert findings == [], (
        f"FP1: pure comment should yield no findings, got {findings!r}"
    )


# ── FP2: inline trailing comment is NOT flagged ─────────────────────────────

def test_fp2_inline_trailing_comment_exec_module_not_flagged():
    """FP2: code line whose comment mentions exec_module → []."""
    scan = _import_lib()
    text = 'x = 1  # exec_module note\n'
    findings = scan(text)
    assert findings == [], (
        f"FP2: trailing-comment exec_module should yield no findings, got {findings!r}"
    )


# ── FP3: string literal is NOT flagged ──────────────────────────────────────

def test_fp3_string_literal_spec_from_file_location_not_flagged():
    """FP3: pattern inside a string literal → []."""
    scan = _import_lib()
    text = 's = "uses spec_from_file_location at runtime"\n'
    findings = scan(text)
    assert findings == [], (
        f"FP3: string literal should yield no findings, got {findings!r}"
    )


# ── FP4: triple-quoted docstring mentioning exec_module is NOT flagged ───────

def test_fp4_triple_quoted_docstring_exec_module_not_flagged():
    """FP4: exec_module inside a triple-quoted docstring → []."""
    scan = _import_lib()
    text = '"""\ncall exec_module here\n"""\n'
    findings = scan(text)
    assert findings == [], (
        f"FP4: docstring mention of exec_module should yield no findings, got {findings!r}"
    )


# ── FP5: real call is flagged with correct metadata ─────────────────────────

def test_fp5_real_call_spec_from_file_location_flagged():
    """FP5: genuine spec_from_file_location call → exactly 1 finding, correct metadata."""
    scan = _import_lib()
    text = 'mod = importlib.util.spec_from_file_location("m", p)\n'
    findings = scan(text)
    assert len(findings) == 1, (
        f"FP5: expected exactly 1 finding for real call, got {findings!r}"
    )
    assert findings[0]["pattern"] == "spec_from_file_location", (
        f"FP5: wrong pattern: {findings[0]['pattern']!r}"
    )
    assert findings[0]["line"] == 1, (
        f"FP5: expected line==1, got {findings[0]['line']!r}"
    )
    assert "spec_from_file_location" in findings[0]["text"], (
        f"FP5: pattern not in finding text: {findings[0]['text']!r}"
    )


# ── FP6: real exec_module call, and a line with both real calls ──────────────

def test_fp6_real_exec_module_and_both_on_one_line():
    """FP6: single exec_module call → 1 finding; both patterns on one line → 2 findings."""
    scan = _import_lib()

    # sub-case A: only exec_module
    text_a = 'spec.loader.exec_module(mod)\n'
    findings_a = scan(text_a)
    assert len(findings_a) == 1, (
        f"FP6a: expected 1 finding for exec_module call, got {findings_a!r}"
    )
    assert findings_a[0]["pattern"] == "exec_module", (
        f"FP6a: wrong pattern: {findings_a[0]['pattern']!r}"
    )

    # sub-case B: both real patterns on one line, spec first in source
    text_b = 'x = spec_from_file_location(); spec.loader.exec_module(mod)\n'
    findings_b = scan(text_b)
    assert len(findings_b) == 2, (
        f"FP6b: expected 2 findings for both patterns on one line, got {findings_b!r}"
    )
    assert findings_b[0]["pattern"] == "spec_from_file_location", (
        f"FP6b: first finding should be spec_from_file_location, got {findings_b[0]['pattern']!r}"
    )
    assert findings_b[1]["pattern"] == "exec_module", (
        f"FP6b: second finding should be exec_module, got {findings_b[1]['pattern']!r}"
    )


# ── FP7: mixed — real call line + comment-only line ─────────────────────────

def test_fp7_real_call_plus_comment_only_exec_module():
    """FP7: one real spec_from_file_location call + one comment-only exec_module line → exactly 1 finding."""
    scan = _import_lib()
    text = 'mod = spec_from_file_location("m", p)\n# exec_module is mentioned only in this comment\n'
    findings = scan(text)
    assert len(findings) == 1, (
        f"FP7: expected exactly 1 finding (comment exec_module suppressed), got {findings!r}"
    )
    assert findings[0]["pattern"] == "spec_from_file_location", (
        f"FP7: the one finding should be spec_from_file_location, got {findings[0]['pattern']!r}"
    )


# ── FP8: fallback on untokenizable input still detects real call ─────────────

def test_fp8_fallback_on_untokenizable_input_detects_real_call():
    """FP8: unterminated triple-quote triggers fallback; real spec_from_file_location call
    on line 2 is still reported (Principle A fail-loud)."""
    scan = _import_lib()
    # Opening triple-quote on line 1 never closed → tokenize raises TokenError.
    # Line 2 contains a genuine (non-comment) call.
    text = '"""unterminated triple quote\nspec_from_file_location(x)\n'
    findings = scan(text)
    assert len(findings) >= 1, (
        f"FP8: fallback should still yield ≥1 finding for real call, got {findings!r}"
    )
    patterns = [f["pattern"] for f in findings]
    assert "spec_from_file_location" in patterns, (
        f"FP8: expected spec_from_file_location in findings, got {findings!r}"
    )
