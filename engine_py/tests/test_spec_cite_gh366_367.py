"""RED tests for GH366/GH367 — spec_cite.py false-positive fixes (agreement 106C8D6F).

Spec: SHARED/memory/Decisions/2026-07-06_106C8D6F_gh366_368_spec_lint_fp_spec.md §3 T1-T10.

The module `spec_cite.py` ALREADY EXISTS (conftest.py inserts engine_py/ onto
sys.path — see tests/conftest.py "Conftest-import-time singleton"), so a plain
top-level `import bytedigger_engine.spec_cite` is safe (§1q only forbids importing symbols that
do not exist yet). The NEW symbols under test (Citation.is_new, the
"new_symbol" Finding.status value, the tightened _SYMBOL_TOKEN_RE charset)
are exercised via calls to the EXISTING public functions (scan_citations,
check_citation, lint_spec) — presence of new *behavior* is asserted directly;
presence of the new `is_new` dataclass field (T10) is checked via
`dataclasses.fields` so it fails at assert time, never at collection/import
time (§1q / D1CF5FDF).

Expected pre-GREEN result: T1, T2, T6, T8, T9, T10 FAIL. T3, T4, T5, T7 are
regression pins and are expected to PASS already (annotated per-test below).
"""
from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest

from bytedigger_engine import spec_cite


# ── T1 (#367): `result[0]["status"]=="not_remediated"` is NOT a symbol ──────


def test_t1_index_and_comparison_expression_not_a_citation(tmp_path: Path) -> None:
    """T1: inline expr `result[0]["status"]=="not_remediated"` next to a .py path
    must NOT be scanned as a symbol citation.

    Expected pre-GREEN: FAIL — current _is_valid_symbol only rejects spaces /
    file-suffix / non-letter-start; this token starts with 'r', has no space,
    doesn't end in .py — so it currently DOES scan as a (bogus) citation.
    """
    spec_text = (
        'The check asserts `result[0]["status"]=="not_remediated"` in `foo.py`.\n'
    )
    citations = spec_cite.scan_citations(spec_text)
    bogus = [c for c in citations if c.symbol == 'result[0]["status"]=="not_remediated"']
    assert bogus == [], (
        f"T1: inline index/comparison expression must NOT be emitted as a symbol "
        f"citation; got {bogus!r} in {citations!r}"
    )


# ── T2 (#367): `len(result)==4` is NOT a symbol ──────────────────────────────


def test_t2_call_and_comparison_expression_not_a_citation(tmp_path: Path) -> None:
    """T2: inline expr `len(result)==4` next to a .py path must NOT be a citation.

    Expected pre-GREEN: FAIL — same reasoning as T1; currently scans as valid.
    """
    spec_text = "Verify `len(result)==4` holds after the call in `foo.py`.\n"
    citations = spec_cite.scan_citations(spec_text)
    bogus = [c for c in citations if c.symbol == "len(result)==4"]
    assert bogus == [], (
        f"T2: inline call/comparison expression must NOT be emitted as a symbol "
        f"citation; got {bogus!r} in {citations!r}"
    )


# ── T3 (#367 regression): `check_citation()` legit literal suffix — resolved ─


def test_t3_legit_call_suffix_symbol_resolved(tmp_path: Path) -> None:
    """T3 (regression, expected to PASS pre-GREEN already): `check_citation()`
    with the symbol present in the target file → resolved.

    _is_valid_symbol already accepts this token today (no space, no .py
    suffix, letter-start) and check_citation's substring match already finds
    it — this regression pin must survive the §2.1 charset tightening.
    """
    code_file = tmp_path / "foo.py"
    code_file.write_text("def check_citation():\n    pass\n")

    spec_text = "Call `check_citation()` defined in `foo.py` to validate.\n"
    citations = spec_cite.scan_citations(spec_text)
    target = next((c for c in citations if c.symbol == "check_citation()"), None)
    assert target is not None, f"T3: expected a Citation for 'check_citation()', got {citations!r}"

    finding = spec_cite.check_citation(target, tmp_path)
    assert finding.status == "resolved", (
        f"T3: expected status='resolved', got {finding.status!r}"
    )


# ── T4 (#367 regression): `consumer-dispatch-missing` legit hyphen literal ──


def test_t4_legit_hyphenated_literal_resolved(tmp_path: Path) -> None:
    """T4 (regression, expected to PASS pre-GREEN already): `consumer-dispatch-missing`
    string literal present in the target file → resolved.
    """
    code_file = tmp_path / "foo.py"
    code_file.write_text('rule_id = "consumer-dispatch-missing"\n')

    spec_text = "The rule_id is `consumer-dispatch-missing` in `foo.py`.\n"
    citations = spec_cite.scan_citations(spec_text)
    target = next((c for c in citations if c.symbol == "consumer-dispatch-missing"), None)
    assert target is not None, (
        f"T4: expected a Citation for 'consumer-dispatch-missing', got {citations!r}"
    )

    finding = spec_cite.check_citation(target, tmp_path)
    assert finding.status == "resolved", (
        f"T4: expected status='resolved', got {finding.status!r}"
    )


# ── T5 (#366/#367 regression): plain unresolved symbol → unresolved_symbol ──


def test_t5_plain_unresolved_symbol_still_unresolved_exit_1(tmp_path: Path) -> None:
    """T5 (regression, expected to PASS pre-GREEN already): ordinary `typo_sym`
    citation, symbol absent, line has NO new-context marker → status stays
    'unresolved_symbol' and lint_spec exit code stays 1.

    §1l forcing: distinguishes the real fix from a stub that makes
    check_citation ALWAYS advisory ("new_symbol") regardless of new-context.
    """
    code_file = tmp_path / "foo.py"
    code_file.write_text("def correct_name():\n    pass\n")

    spec_text = "Call `typo_sym` from `foo.py` to process the request.\n"
    spec_path = tmp_path / "spec.md"
    spec_path.write_text(spec_text)

    exit_code, findings = spec_cite.lint_spec(spec_path, tmp_path)
    assert exit_code == 1, f"T5: expected exit code 1, got {exit_code}; findings={findings!r}"

    unresolved = [f for f in findings if f.status == "unresolved_symbol"]
    assert any(f.symbol == "typo_sym" for f in unresolved), (
        f"T5: expected an unresolved_symbol finding for 'typo_sym', got {findings!r}"
    )


# ── T6 (#366): ADD `new_helper` to `foo.py` → new_symbol, exit 0 ────────────


def test_t6_add_line_new_symbol_marks_advisory_exit_0(tmp_path: Path) -> None:
    """T6: line 'ADD `new_helper` to `foo.py`' with the symbol absent from the
    file must yield status='new_symbol' (not 'unresolved_symbol') and
    lint_spec exit code 0.

    Expected pre-GREEN: FAIL — Citation has no new-context concept today;
    check_citation would return status='unresolved_symbol', exit code 1.
    """
    code_file = tmp_path / "foo.py"
    code_file.write_text("def unrelated():\n    pass\n")

    spec_text = "ADD `new_helper` to `foo.py` for the pipeline.\n"
    citations = spec_cite.scan_citations(spec_text)
    target = next((c for c in citations if c.symbol == "new_helper"), None)
    assert target is not None, f"T6: expected a Citation for 'new_helper', got {citations!r}"

    finding = spec_cite.check_citation(target, tmp_path)
    assert finding.status == "new_symbol", (
        f"T6: expected status='new_symbol', got {finding.status!r}"
    )

    spec_path = tmp_path / "spec.md"
    spec_path.write_text(spec_text)
    exit_code, findings = spec_cite.lint_spec(spec_path, tmp_path)
    assert exit_code == 0, (
        f"T6: new_symbol is advisory only — expected exit 0, got {exit_code}; findings={findings!r}"
    )


# ── T7 (#366 regression): CREATE-line, symbol present → resolved ────────────


def test_t7_create_line_symbol_present_resolved(tmp_path: Path) -> None:
    """T7 (regression, expected to PASS pre-GREEN already): 'CREATE `helper_fn`
    in `foo.py`' with the symbol ALREADY present in the file → resolved (the
    new-context marker must not override an actually-resolved symbol).
    """
    code_file = tmp_path / "foo.py"
    code_file.write_text("def helper_fn():\n    pass\n")

    spec_text = "CREATE `helper_fn` in `foo.py` for validation.\n"
    citations = spec_cite.scan_citations(spec_text)
    target = next((c for c in citations if c.symbol == "helper_fn"), None)
    assert target is not None, f"T7: expected a Citation for 'helper_fn', got {citations!r}"

    finding = spec_cite.check_citation(target, tmp_path)
    assert finding.status == "resolved", (
        f"T7: expected status='resolved', got {finding.status!r}"
    )


# ── T8 (#366): inline `(new)` marker, symbol absent → new_symbol ────────────


def test_t8_inline_new_marker_advisory(tmp_path: Path) -> None:
    """T8: line with inline '(new)' marker, symbol absent from file → new_symbol.

    Expected pre-GREEN: FAIL — no new-context concept today; would be
    unresolved_symbol.
    """
    code_file = tmp_path / "foo.py"
    code_file.write_text("def unrelated():\n    pass\n")

    spec_text = "The (new) helper `fresh_sym` lands in `foo.py`.\n"
    citations = spec_cite.scan_citations(spec_text)
    target = next((c for c in citations if c.symbol == "fresh_sym"), None)
    assert target is not None, f"T8: expected a Citation for 'fresh_sym', got {citations!r}"

    finding = spec_cite.check_citation(target, tmp_path)
    assert finding.status == "new_symbol", (
        f"T8: expected status='new_symbol', got {finding.status!r}"
    )


# ── T9 (#366): spec with only new_symbol findings → exit 0, status present ──


def test_t9_lint_spec_all_new_symbol_findings_exit_0(tmp_path: Path) -> None:
    """T9: a spec whose only citation is a new-context symbol not yet in the
    file → lint_spec returns exit_code 0 and findings contain status='new_symbol'.

    Expected pre-GREEN: FAIL — current lint_spec would return exit 1 with
    status='unresolved_symbol'.
    """
    code_file = tmp_path / "foo.py"
    code_file.write_text("def unrelated():\n    pass\n")

    spec_text = "CREATE `brand_new_fn` in `foo.py` as the entry point.\n"
    spec_path = tmp_path / "spec.md"
    spec_path.write_text(spec_text)

    exit_code, findings = spec_cite.lint_spec(spec_path, tmp_path)
    assert exit_code == 0, f"T9: expected exit code 0, got {exit_code}; findings={findings!r}"
    assert any(f.status == "new_symbol" for f in findings), (
        f"T9: expected a finding with status='new_symbol', got {findings!r}"
    )


# ── T10 (#366): Citation.is_new field exists, defaults to False ─────────────


def test_t10_citation_is_new_field_exists_defaults_false() -> None:
    """T10: Citation dataclass has a field `is_new: bool` defaulting to False.

    Presence-gated via dataclasses.fields so the assertion fails cleanly at
    assert time (never at import/collection time, per §1q/D1CF5FDF).

    Expected pre-GREEN: FAIL — Citation has no such field today.
    """
    field_names = {f.name for f in dataclasses.fields(spec_cite.Citation)}
    assert "is_new" in field_names, (
        f"T10: Citation must have an 'is_new' field, got fields={field_names!r}"
    )

    cit = spec_cite.Citation(file="foo.py", symbol="bar", line_no=1)
    assert getattr(cit, "is_new", None) is False, (
        f"T10: Citation.is_new must default to False, got {getattr(cit, 'is_new', 'MISSING')!r}"
    )
