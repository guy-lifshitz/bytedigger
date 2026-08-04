"""RED tests for GH631 — spec_cite.py learns declared-created files (net-new
allowlist).

Spec: SHARED/memory/Decisions/2026-07-12_GH631_cite_lint_declared_creates_spec.md
§3 AC1-AC12.

The module `spec_cite.py` ALREADY EXISTS (conftest.py inserts engine_py/ onto
sys.path), so a plain top-level `import bytedigger_engine.spec_cite` is safe (§1q only forbids
importing symbols that do not exist yet). The NEW public symbols under test
(`declared_created_files`, `_norm_path`, `check_citation(..., declared=...)`)
are fetched lazily via `getattr` inside each test body with an explicit
presence-assert, so the file COLLECTS and FAILS at assert time, never at
import/collection time (§1q / D1CF5FDF).

Expected pre-GREEN: AC1, AC2, AC3, AC4, AC6, AC7, AC9, AC11, AC12 FAIL.
AC5, AC8, AC10 are regression pins over EXISTING behavior and are expected to
PASS already (annotated per-test below).
"""
from __future__ import annotations

from pathlib import Path

from bytedigger_engine import spec_cite


def _get_declared_created_files():
    fn = getattr(spec_cite, "declared_created_files", None)
    assert fn is not None, "declared_created_files not implemented in spec_cite"
    return fn


# ── AC1: CREATE line, MODIFY line excluded ──────────────────────────────────


def test_ac1_create_line_extracted_modify_excluded() -> None:
    fn = _get_declared_created_files()
    spec_text = "## Files\nCREATE: scripts/foo.py\nMODIFY: bar.py (x)"
    result = fn(spec_text)
    assert result == {"scripts/foo.py"}, (
        f"AC1: expected {{'scripts/foo.py'}}, got {result!r}"
    )


# ── AC2: bullet/backtick/./comment forms normalize ──────────────────────────


def test_ac2_bullet_backtick_dotslash_comment_forms_normalize() -> None:
    fn = _get_declared_created_files()
    spec_text = "- CREATE: `scripts/foo.py`"
    result1 = fn(spec_text)
    assert result1 == {"scripts/foo.py"}, f"AC2a: got {result1!r}"

    spec_text2 = "CREATE: ./scripts/foo.py (new CLI)"
    result2 = fn(spec_text2)
    assert result2 == {"scripts/foo.py"}, f"AC2b: got {result2!r}"


# ── AC3: "Files this spec CREATES" section, bounded by next heading ─────────


def test_ac3_creates_heading_section_bounded_by_next_heading() -> None:
    fn = _get_declared_created_files()
    spec_text = (
        "## Files this spec CREATES\n"
        "- `a/b.py`\n"
        "- c.sh\n"
        "## Next Section\n"
        "d.py\n"
    )
    result = fn(spec_text)
    assert result == {"a/b.py", "c.sh"}, (
        f"AC3: expected {{'a/b.py', 'c.sh'}}, got {result!r} (d.py must be excluded)"
    )


# ── AC4: check_citation with declared kwarg, file absent → planned_file ─────


def test_ac4_check_citation_declared_file_absent_planned_file(tmp_path: Path) -> None:
    check_citation = spec_cite.check_citation
    cit = spec_cite.Citation(file="scripts/foo.py", symbol="retired_tech", line_no=1)
    try:
        finding = check_citation(cit, tmp_path, declared={"scripts/foo.py"})
    except TypeError as exc:
        raise AssertionError(
            f"AC4: check_citation does not accept 'declared' kwarg yet: {exc}"
        )
    assert finding.status == "planned_file", (
        f"AC4: expected status='planned_file', got {finding.status!r}"
    )


# ── AC5 (regression): check_citation WITHOUT declared kwarg, file absent ────
# Expected to PASS already pre-GREEN — declared has a default of frozenset().


def test_ac5_check_citation_without_declared_kwarg_missing_file(tmp_path: Path) -> None:
    check_citation = spec_cite.check_citation
    cit = spec_cite.Citation(file="scripts/foo.py", symbol="retired_tech", line_no=1)
    finding = check_citation(cit, tmp_path)
    assert finding.status == "missing_file", (
        f"AC5: expected status='missing_file', got {finding.status!r}"
    )


# ── AC6: lint_spec greenfield — declared file, exit 0, planned_file present ─


def test_ac6_lint_spec_greenfield_declared_file_exit_0_planned_file(tmp_path: Path) -> None:
    spec_text = (
        "## Files\nCREATE: scripts/foo.py\n\n"
        "Uses `retired_tech` in `scripts/foo.py` to migrate.\n"
    )
    spec_path = tmp_path / "spec.md"
    spec_path.write_text(spec_text)

    exit_code, findings = spec_cite.lint_spec(spec_path, tmp_path)
    assert exit_code == 0, (
        f"AC6: expected exit 0, got {exit_code}; findings={findings!r}"
    )
    assert any(f.status == "planned_file" for f in findings), (
        f"AC6: expected a planned_file finding, got {findings!r}"
    )
    assert not any(f.status == "unresolved_symbol" for f in findings), (
        f"AC6: expected NO unresolved_symbol finding, got {findings!r}"
    )


# ── AC7: cross-product repro — declared+cited symbol also cited on existing
#         file without NEW context → downgrade to new_symbol, exit 0 ─────────


def test_ac7_cross_product_downgrade_to_new_symbol_exit_0(tmp_path: Path) -> None:
    existing_file = tmp_path / "bar.py"
    existing_file.write_text("def unrelated():\n    pass\n")

    spec_text = (
        "## Files\nCREATE: scripts/foo.py\n\n"
        "Uses `shared_symbol` in `scripts/foo.py` to migrate.\n"
        "Calls `shared_symbol` from `bar.py` during setup.\n"
    )
    spec_path = tmp_path / "spec.md"
    spec_path.write_text(spec_text)

    exit_code, findings = spec_cite.lint_spec(spec_path, tmp_path)
    assert exit_code == 0, (
        f"AC7: expected exit 0, got {exit_code}; findings={findings!r}"
    )
    bar_findings = [f for f in findings if f.file == "bar.py" and f.symbol == "shared_symbol"]
    assert any(f.status == "new_symbol" for f in bar_findings), (
        f"AC7: expected 'shared_symbol' on bar.py downgraded to new_symbol, got {findings!r}"
    )


# ── AC8 (regression): undeclared+unresolved on existing file → exit 1 ───────
# Expected to PASS already pre-GREEN.


def test_ac8_undeclared_unresolved_existing_file_exit_1(tmp_path: Path) -> None:
    existing_file = tmp_path / "bar.py"
    existing_file.write_text("def correct_name():\n    pass\n")

    spec_text = "Call `typo_sym` from `bar.py` to process the request.\n"
    spec_path = tmp_path / "spec.md"
    spec_path.write_text(spec_text)

    exit_code, findings = spec_cite.lint_spec(spec_path, tmp_path)
    assert exit_code == 1, (
        f"AC8: expected exit 1, got {exit_code}; findings={findings!r}"
    )
    assert any(f.status == "unresolved_symbol" for f in findings), (
        f"AC8: expected an unresolved_symbol finding, got {findings!r}"
    )


# ── AC9: declared file exists — symbol present → resolved; absent → new_symbol


def test_ac9_declared_file_exists_symbol_present_resolved_absent_new_symbol(
    tmp_path: Path,
) -> None:
    check_citation = spec_cite.check_citation
    code_file = tmp_path / "scripts" / "foo.py"
    code_file.parent.mkdir(parents=True)
    code_file.write_text("def present_sym():\n    pass\n")

    cit_present = spec_cite.Citation(file="scripts/foo.py", symbol="present_sym", line_no=1)
    finding_present = check_citation(cit_present, tmp_path, declared={"scripts/foo.py"})
    assert finding_present.status == "resolved", (
        f"AC9a: expected status='resolved', got {finding_present.status!r}"
    )

    cit_absent = spec_cite.Citation(file="scripts/foo.py", symbol="missing_sym", line_no=2)
    finding_absent = check_citation(cit_absent, tmp_path, declared={"scripts/foo.py"})
    assert finding_absent.status == "new_symbol", (
        f"AC9b: expected status='new_symbol', got {finding_absent.status!r}"
    )


# ── AC10 (regression): undeclared missing file → missing_file, not planned_file
# Expected to PASS already pre-GREEN.


def test_ac10_undeclared_missing_file_stays_missing_file(tmp_path: Path) -> None:
    spec_text = "Uses `retired_tech` in `scripts/foo.py` to migrate.\n"
    spec_path = tmp_path / "spec.md"
    spec_path.write_text(spec_text)

    exit_code, findings = spec_cite.lint_spec(spec_path, tmp_path)
    assert any(f.status == "missing_file" for f in findings), (
        f"AC10: expected a missing_file finding, got {findings!r}"
    )
    assert not any(f.status == "planned_file" for f in findings), (
        f"AC10: expected NO planned_file finding (undeclared), got {findings!r}"
    )


# ── AC11: normalization — CREATE: ./scripts/foo.py matches citation on
#          scripts/foo.py (no leading ./) ────────────────────────────────────


def test_ac11_normalization_dotslash_create_matches_bare_citation(tmp_path: Path) -> None:
    spec_text = (
        "## Files\nCREATE: ./scripts/foo.py\n\n"
        "Uses `retired_tech` in `scripts/foo.py` to migrate.\n"
    )
    spec_path = tmp_path / "spec.md"
    spec_path.write_text(spec_text)

    exit_code, findings = spec_cite.lint_spec(spec_path, tmp_path)
    assert any(f.status == "planned_file" for f in findings), (
        f"AC11: expected a planned_file finding after ./-normalization, got {findings!r}"
    )


# ── AC12: planned_symbols adds symbol from planned_file-status finding ──────


def test_ac12_planned_symbols_includes_planned_file_status_symbol() -> None:
    citations = [
        spec_cite.Citation(file="scripts/foo.py", symbol="future_sym", line_no=1),
    ]
    findings = [
        spec_cite.Finding(file="scripts/foo.py", symbol="future_sym", status="planned_file"),
    ]
    planned = spec_cite.planned_symbols(citations, findings)
    assert "future_sym" in planned, (
        f"AC12: expected 'future_sym' in planned set, got {planned!r}"
    )
