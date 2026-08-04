"""RED tests for 761D8C4F — deterministic MICRO-scope eligibility kernel.

Covers: AC1..AC12 from spec §3 (2026-05-30_761D8C4F_micro_scope_heuristic_spec.md).

Pre-GREEN predict: ALL 12 tests FAIL/ERROR at collection time.
  AC1-AC12: ImportError — lib/micro_scope.py does not exist yet.
"""
from __future__ import annotations

import pytest

# MODULE-LEVEL IMPORT: causes ImportError (all tests FAIL) pre-GREEN.
from bytedigger_engine.lib.micro_scope import classify_scope, MICRO_MAX_LOGIC_LOC, MICRO_MAX_AC  # type: ignore[import]


# ---------------------------------------------------------------------------
# Shared fixture helper — AC1 base dict; tests override single keys.
# ---------------------------------------------------------------------------

def _base() -> dict:
    return {
        "new_files": 1,
        "modified_files": 0,
        "logic_loc": 50,
        "ac_count": 8,
        "blocking_deps": False,
        "spec_fits_one_page": True,
    }


# ---------------------------------------------------------------------------
# AC1 — all gates pass → MICRO, eligible, no reasons
# ---------------------------------------------------------------------------

def test_ac1_all_gates_pass_returns_micro():
    """AC1: base dict → tier=="MICRO", eligible is True, reasons==[]"""
    result = classify_scope(_base())
    assert result["tier"] == "MICRO"
    assert result["eligible"] is True
    assert result["reasons"] == []


# ---------------------------------------------------------------------------
# AC2 — modified_files:1 → modifies-existing-production
# ---------------------------------------------------------------------------

def test_ac2_modified_files_nonzero_escalates():
    """AC2: AC1 but modified_files:1 → ESCALATE, "modifies-existing-production" in reasons"""
    scope = _base()
    scope["modified_files"] = 1
    result = classify_scope(scope)
    assert result["tier"] == "ESCALATE"
    assert "modifies-existing-production" in result["reasons"]


# ---------------------------------------------------------------------------
# AC3 — new_files:2 → not-single-new-file
# ---------------------------------------------------------------------------

def test_ac3_new_files_two_escalates():
    """AC3: AC1 but new_files:2 → "not-single-new-file" in reasons, ESCALATE"""
    scope = _base()
    scope["new_files"] = 2
    result = classify_scope(scope)
    assert result["tier"] == "ESCALATE"
    assert "not-single-new-file" in result["reasons"]


# ---------------------------------------------------------------------------
# AC4 — new_files:0 → not-single-new-file
# ---------------------------------------------------------------------------

def test_ac4_new_files_zero_escalates():
    """AC4: AC1 but new_files:0 → "not-single-new-file" in reasons, ESCALATE"""
    scope = _base()
    scope["new_files"] = 0
    result = classify_scope(scope)
    assert result["tier"] == "ESCALATE"
    assert "not-single-new-file" in result["reasons"]


# ---------------------------------------------------------------------------
# AC5 — logic_loc:81 → loc-over-cap
# ---------------------------------------------------------------------------

def test_ac5_logic_loc_over_cap_escalates():
    """AC5: AC1 but logic_loc:81 → "loc-over-cap" in reasons, ESCALATE"""
    scope = _base()
    scope["logic_loc"] = 81
    result = classify_scope(scope)
    assert result["tier"] == "ESCALATE"
    assert "loc-over-cap" in result["reasons"]


# ---------------------------------------------------------------------------
# AC6 — logic_loc:80 (boundary) → MICRO, reasons==[]
# ---------------------------------------------------------------------------

def test_ac6_logic_loc_boundary_is_micro():
    """AC6: AC1 but logic_loc:80 (boundary) → tier=="MICRO", reasons==[]"""
    scope = _base()
    scope["logic_loc"] = 80
    result = classify_scope(scope)
    assert result["tier"] == "MICRO"
    assert result["reasons"] == []


# ---------------------------------------------------------------------------
# AC7 — ac_count:13 → ac-over-cap
# ---------------------------------------------------------------------------

def test_ac7_ac_count_over_cap_escalates():
    """AC7: AC1 but ac_count:13 → "ac-over-cap" in reasons, ESCALATE"""
    scope = _base()
    scope["ac_count"] = 13
    result = classify_scope(scope)
    assert result["tier"] == "ESCALATE"
    assert "ac-over-cap" in result["reasons"]


# ---------------------------------------------------------------------------
# AC8 — ac_count:12 (boundary) → MICRO
# ---------------------------------------------------------------------------

def test_ac8_ac_count_boundary_is_micro():
    """AC8: AC1 but ac_count:12 (boundary) → tier=="MICRO", reasons==[]"""
    scope = _base()
    scope["ac_count"] = 12
    result = classify_scope(scope)
    assert result["tier"] == "MICRO"
    assert result["reasons"] == []


# ---------------------------------------------------------------------------
# AC9 — blocking_deps:True → blocking-deps
# ---------------------------------------------------------------------------

def test_ac9_blocking_deps_escalates():
    """AC9: AC1 but blocking_deps:True → "blocking-deps" in reasons, ESCALATE"""
    scope = _base()
    scope["blocking_deps"] = True
    result = classify_scope(scope)
    assert result["tier"] == "ESCALATE"
    assert "blocking-deps" in result["reasons"]


# ---------------------------------------------------------------------------
# AC10 — spec_fits_one_page:False → spec-over-one-page
# ---------------------------------------------------------------------------

def test_ac10_spec_over_one_page_escalates():
    """AC10: AC1 but spec_fits_one_page:False → "spec-over-one-page" in reasons, ESCALATE"""
    scope = _base()
    scope["spec_fits_one_page"] = False
    result = classify_scope(scope)
    assert result["tier"] == "ESCALATE"
    assert "spec-over-one-page" in result["reasons"]


# ---------------------------------------------------------------------------
# AC11 — logic_loc:200, ac_count:99 → BOTH loc-over-cap AND ac-over-cap in reasons
# ---------------------------------------------------------------------------

def test_ac11_multiple_violations_both_reasons_present():
    """AC11: AC1 but logic_loc:200, ac_count:99 → reasons contains BOTH "loc-over-cap" and "ac-over-cap" """
    scope = _base()
    scope["logic_loc"] = 200
    scope["ac_count"] = 99
    result = classify_scope(scope)
    assert "loc-over-cap" in result["reasons"]
    assert "ac-over-cap" in result["reasons"]


# ---------------------------------------------------------------------------
# AC12 — missing ac_count key raises ValueError with "ac_count" in message;
#         AND module constants MICRO_MAX_LOGIC_LOC==80, MICRO_MAX_AC==12
# ---------------------------------------------------------------------------

def test_ac12_missing_key_raises_valueerror_and_constants_correct():
    """AC12: AC1 missing ac_count key → raises ValueError with "ac_count" in message;
    AND MICRO_MAX_LOGIC_LOC==80 and MICRO_MAX_AC==12."""
    scope = _base()
    del scope["ac_count"]
    with pytest.raises(ValueError) as exc_info:
        classify_scope(scope)
    assert "ac_count" in str(exc_info.value), (
        f"ValueError message {str(exc_info.value)!r} does not contain 'ac_count'"
    )
    assert MICRO_MAX_LOGIC_LOC == 80, f"MICRO_MAX_LOGIC_LOC expected 80, got {MICRO_MAX_LOGIC_LOC}"
    assert MICRO_MAX_AC == 12, f"MICRO_MAX_AC expected 12, got {MICRO_MAX_AC}"
