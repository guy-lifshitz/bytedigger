"""RED tests for verdict_anchor_lint 6B4AFA50 — lint_text / lint_file / main.

ALL tests MUST FAIL until GREEN ships:
  - SYSTEM/cli/build/engine_py/verdict_anchor.py

Lib import is DEFERRED INSIDE each test function so the file COLLECTS cleanly
(per §1q/D1CF5FDF).  Module top-level has NO bare `from verdict_anchor import ...`.
"""
from __future__ import annotations

from pathlib import Path

import pytest

# Engine root so deferred imports inside test bodies can find verdict_anchor.py.
_ENGINE_ROOT = Path(__file__).resolve().parents[1]  # engine_py/

# Real target file — canonical golden input; must exist NOW (pre-GREEN).
_VERDICT_PARSE_PATH = _ENGINE_ROOT / "bytedigger_engine/lib" / "verdict_parse.py"


def _import_verdict_anchor():
    """Deferred import — raises ImportError until GREEN ships verdict_anchor.py."""
    import importlib
    import sys
    from bytedigger_engine import verdict_anchor as _va
    return _va


# ── AC1: lint_text on real verdict_parse.py → [] (golden passes) ────────────


def test_ac1_golden_verdict_parse_lint_clean() -> None:
    """AC1: lint_text on the real current verdict_parse.py source → [] (all 4 parse fns pass)."""
    va = _import_verdict_anchor()
    source = _VERDICT_PARSE_PATH.read_text(encoding="utf-8")
    violations = va.lint_text(source, "verdict_parse.py")
    assert violations == [], (
        f"verdict_parse.py must lint clean (golden invariant); got {violations!r}"
    )


# ── AC2: [ \t ] anchor + NO _normalize → 1 R1 violation ────────────────────


def test_ac2_tab_anchor_no_normalize_yields_r1_violation() -> None:
    """AC2: fn with [ \\t] anchor + NO _normalize → exactly one tab_anchor_no_normalize violation."""
    va = _import_verdict_anchor()
    source = '''\
import re

def parse_verdict(raw, tokens, fallback):
    pattern = r"^[ \\t]*VERDICT:[ \\t]*(.+)[ \\t]*$"
    m = re.search(pattern, raw, re.MULTILINE)
    if not m:
        return fallback
    return m.group(1).upper()
'''
    violations = va.lint_text(source, "test_input.py")
    r1 = [v for v in violations if v.rule == "tab_anchor_no_normalize"]
    assert len(r1) == 1, (
        f"expected exactly 1 tab_anchor_no_normalize violation; got {violations!r}"
    )
    assert r1[0].fn == "parse_verdict", (
        f"violation .fn should be 'parse_verdict'; got {r1[0].fn!r}"
    )


# ── AC3: [ \t ] anchor + _normalize call → no R1 ────────────────────────────


def test_ac3_tab_anchor_with_normalize_no_r1_violation() -> None:
    """AC3: fn with [ \\t] anchor + _normalize call → no tab_anchor_no_normalize violation (P1 shape)."""
    va = _import_verdict_anchor()
    source = '''\
import re

def _normalize(raw):
    if not raw:
        return raw
    return raw.replace("\\r\\n", "\\n").replace("\\r", "\\n")

def parse_verdict(raw, tokens, fallback):
    raw = _normalize(raw)
    pattern = r"^[ \\t]*VERDICT:[ \\t]*(.+)[ \\t]*$"
    m = re.search(pattern, raw, re.MULTILINE)
    if not m:
        return fallback
    return m.group(1).upper()
'''
    violations = va.lint_text(source, "test_input.py")
    r1 = [v for v in violations if v.rule == "tab_anchor_no_normalize"]
    assert r1 == [], (
        f"fn with [ \\t] anchor + _normalize must NOT trigger R1; got {violations!r}"
    )


# ── AC4: parse fn (re.search) + NO _normalize, not exempt → 1 R2 ────────────


def test_ac4_parse_fn_no_normalize_not_exempt_yields_r2_violation() -> None:
    """AC4: parse fn using re.search + NO _normalize, not exempt → one entry_no_normalize violation."""
    va = _import_verdict_anchor()
    source = '''\
import re

def parse_status(raw, markers, fallback):
    for marker, value in markers:
        pattern = re.compile(rf"^\\s*{re.escape(marker)}", re.MULTILINE)
        if pattern.search(raw):
            return value
    return fallback
'''
    violations = va.lint_text(source, "test_input.py")
    r2 = [v for v in violations if v.rule == "entry_no_normalize"]
    assert len(r2) == 1, (
        f"expected exactly 1 entry_no_normalize violation; got {violations!r}"
    )
    assert r2[0].fn == "parse_status", (
        f"violation .fn should be 'parse_status'; got {r2[0].fn!r}"
    )


# ── AC5: parse fn, no _normalize, body comment "NOT normalized" → [] ─────────


def test_ac5_parse_fn_not_normalized_comment_exempt() -> None:
    """AC5: parse fn, no _normalize, body has 'NOT normalized' comment → no violation (P4 case)."""
    va = _import_verdict_anchor()
    source = '''\
import re

def find_last_standalone_marker(raw, tokens, suffix=":"):
    """P4 last standalone marker."""
    # NOT normalized: caller slices its OWN original raw via this Match .end()
    if not raw or not tokens:
        return None
    sorted_tokens = sorted(tokens, key=len, reverse=True)
    alt = "|".join(re.escape(t) for t in sorted_tokens)
    pattern = r"^(" + alt + r")" + re.escape(suffix) + r"\\s*$"
    rx = re.compile(pattern, re.MULTILINE)
    last = None
    for m in rx.finditer(raw):
        last = m
    return last
'''
    violations = va.lint_text(source, "test_input.py")
    assert violations == [], (
        f"fn with 'NOT normalized' comment must be R2-exempt; got {violations!r}"
    )


# ── AC6: parse fn, no _normalize, body has pragma → [] ──────────────────────


def test_ac6_parse_fn_verdict_anchor_exempt_pragma() -> None:
    """AC6: parse fn, no _normalize, body has '# verdict-anchor: exempt' pragma → no violation."""
    va = _import_verdict_anchor()
    source = '''\
import re

def parse_raw_marker(raw, tokens, fallback):
    # verdict-anchor: exempt
    # Caller normalises before passing in; we operate on pre-normalised text.
    pattern = re.compile(r"^\\s*(" + "|".join(re.escape(t) for t in tokens) + r")\\s*$", re.MULTILINE)
    m = pattern.search(raw)
    if not m:
        return fallback
    return m.group(1)
'''
    violations = va.lint_text(source, "test_input.py")
    r2 = [v for v in violations if v.rule == "entry_no_normalize"]
    assert r2 == [], (
        f"fn with 'verdict-anchor: exempt' pragma must be R2-exempt; got {violations!r}"
    )


# ── AC7: non-parse fn (no re. usage) without _normalize → never flagged ──────


def test_ac7_non_parse_fn_not_flagged() -> None:
    """AC7: non-parse fn (no re. usage) without _normalize → zero violations."""
    va = _import_verdict_anchor()
    source = '''\
def _normalize(raw):
    if not raw:
        return raw
    return raw.replace("\\r\\n", "\\n").replace("\\r", "\\n")
'''
    violations = va.lint_text(source, "test_input.py")
    assert violations == [], (
        f"non-parse fn (_normalize doing only .replace) must never be flagged; got {violations!r}"
    )


# ── AC8: Violation carries correct .fn and .line_no (def line) ───────────────


def test_ac8_violation_carries_fn_name_and_def_line_no() -> None:
    """AC8: a returned Violation has .fn == offending function name and .line_no == def line number."""
    va = _import_verdict_anchor()
    # parse_status starts at line 5 (1-indexed): blank line 1, import line 2,
    # blank line 3, blank line 4, def line 5
    source = '''\

import re


def parse_status(raw, markers, fallback):
    pattern = re.compile(r"^\\s*STATUS:", re.MULTILINE)
    return pattern.search(raw) is not None
'''
    violations = va.lint_text(source, "test_input.py")
    r2 = [v for v in violations if v.rule == "entry_no_normalize"]
    assert len(r2) >= 1, (
        f"expected at least 1 entry_no_normalize violation; got {violations!r}"
    )
    v = r2[0]
    assert v.fn == "parse_status", (
        f"Violation.fn must be 'parse_status'; got {v.fn!r}"
    )
    # def line is line 5 in this source (1-indexed)
    assert v.line_no == 5, (
        f"Violation.line_no must be 5 (the 'def parse_status' line); got {v.line_no!r}"
    )
    assert v.file == "test_input.py", (
        f"Violation.file must be 'test_input.py'; got {v.file!r}"
    )


# ── AC9: exempt-tagged fn with [ \t ] anchor STILL yields R1 ────────────────


def test_ac9_exempt_does_not_excuse_tab_anchor() -> None:
    """AC9: R1 fires even when fn IS exempt-tagged (exempt does not excuse [ \\t] anchor)."""
    va = _import_verdict_anchor()
    source = '''\
import re

def parse_verdict_exempt(raw, tokens, fallback):
    # verdict-anchor: exempt
    pattern = r"^[ \\t]*VERDICT:[ \\t]*(.+)$"
    m = re.search(pattern, raw, re.MULTILINE)
    if not m:
        return fallback
    return m.group(1).strip()
'''
    violations = va.lint_text(source, "test_input.py")
    r1 = [v for v in violations if v.rule == "tab_anchor_no_normalize"]
    assert len(r1) == 1, (
        f"R1 (tab_anchor_no_normalize) must fire even for exempt-tagged fn; got {violations!r}"
    )
    assert r1[0].fn == "parse_verdict_exempt", (
        f"violation .fn should be 'parse_verdict_exempt'; got {r1[0].fn!r}"
    )


# ── AC10: main([real_verdict_parse_path]) returns 0 ─────────────────────────


def test_ac10_main_real_verdict_parse_returns_0() -> None:
    """AC10: main([real_verdict_parse_path]) returns 0 (golden clean)."""
    va = _import_verdict_anchor()
    assert _VERDICT_PARSE_PATH.exists(), (
        f"Prerequisite: real verdict_parse.py must exist at {_VERDICT_PARSE_PATH}"
    )
    exit_code = va.main([str(_VERDICT_PARSE_PATH)])
    assert exit_code == 0, (
        f"main([real verdict_parse.py]) must return 0; got {exit_code!r}"
    )


# ── AC11: main([nonexistent_path]) returns 2 ─────────────────────────────────


def test_ac11_main_nonexistent_file_returns_2() -> None:
    """AC11: main([nonexistent_path]) returns 2 (usage error — unreadable file)."""
    va = _import_verdict_anchor()
    exit_code = va.main(["/nonexistent/nope_verdict.py"])
    assert exit_code == 2, (
        f"main with nonexistent file must return 2; got {exit_code!r}"
    )


# ── AC12: main([tmp_file_with_violation]) returns 1 ─────────────────────────


def test_ac12_main_violating_file_returns_1(tmp_path: Path) -> None:
    """AC12: main([tmp_file_with_violation]) returns 1 (≥1 violation found)."""
    va = _import_verdict_anchor()
    violating_source = '''\
import re

def parse_bad(raw, tokens, fallback):
    pattern = r"^[ \\t]*VERDICT:[ \\t]*(.+)$"
    m = re.search(pattern, raw, re.MULTILINE)
    if not m:
        return fallback
    return m.group(1).strip()
'''
    tmp_file = tmp_path / "bad_verdict.py"
    tmp_file.write_text(violating_source, encoding="utf-8")
    exit_code = va.main([str(tmp_file)])
    assert exit_code == 1, (
        f"main with violating file must return 1; got {exit_code!r}"
    )
