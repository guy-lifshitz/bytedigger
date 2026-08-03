"""RED tests for D1F51D7A — phase_45_spec FEATURE-tier + phase_45_spec_lite full complexity resolver.

Scope:
  - phase_45_spec._resolve_review_timeout_sec: adds FEATURE (600) branch between COMPLEX and Opus-floor.
  - phase_45_spec_lite: new _resolve_review_timeout_sec helper (COMPLEX=900/FEATURE=600/baseline=300)
    + new constants DEFAULT_REVIEW_TIMEOUT_SEC_FEATURE/DEFAULT_REVIEW_TIMEOUT_SEC_COMPLEX
    + call-site rewire at line 596.

Parent SYSTEMATIC: 97B6CF02 (timeout policy unification across phase_45_spec, phase_45_spec_lite,
phase_6_review).  Sibling: 920C6935 (phase_6_review size-scaled timeouts, 2026-05-20).

All 12 ACs must FAIL before GREEN ships the changes:
  AC1-AC3: phase_45_spec FEATURE tier (AssertionError / AttributeError today).
  AC4-AC5: phase_45_spec regression guards (PASS today — correctness guards).
  AC6-AC11: phase_45_spec_lite helper absent (ImportError / AttributeError today).
  AC12: phase_45_spec_lite call-site not yet rewired (substring-miss today).
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))


# ---------------------------------------------------------------------------
# AC1 — phase_45_spec FEATURE complexity → 600
# ---------------------------------------------------------------------------


def test_ac1_phase45_spec_feature_returns_600() -> None:
    """AC1: phase_45_spec._resolve_review_timeout_sec({"complexity": "FEATURE"}) → 600."""
    from bytedigger_engine.workflows.phase_45_spec import _resolve_review_timeout_sec  # noqa: PLC0415

    result = _resolve_review_timeout_sec({"complexity": "FEATURE"})
    assert result == 600, f"Expected 600 for FEATURE complexity, got {result!r}"


# ---------------------------------------------------------------------------
# AC2 — phase_45_spec FEATURE case-insensitive ("feature", "Feature") → 600
# ---------------------------------------------------------------------------


def test_ac2_phase45_spec_feature_case_insensitive() -> None:
    """AC2: case-insensitive FEATURE matching — 'feature' and 'Feature' both → 600."""
    from bytedigger_engine.workflows.phase_45_spec import _resolve_review_timeout_sec  # noqa: PLC0415

    result_lower = _resolve_review_timeout_sec({"complexity": "feature"})
    assert result_lower == 600, f"Expected 600 for 'feature' (lowercase), got {result_lower!r}"

    result_mixed = _resolve_review_timeout_sec({"complexity": "Feature"})
    assert result_mixed == 600, f"Expected 600 for 'Feature' (mixed-case), got {result_mixed!r}"


# ---------------------------------------------------------------------------
# AC3 — phase_45_spec DEFAULT_REVIEW_TIMEOUT_SEC_FEATURE constant == 600
# ---------------------------------------------------------------------------


def test_ac3_phase45_spec_feature_constant_present_and_600() -> None:
    """AC3: DEFAULT_REVIEW_TIMEOUT_SEC_FEATURE exists in phase_45_spec and equals 600."""
    from bytedigger_engine.workflows.phase_45_spec import DEFAULT_REVIEW_TIMEOUT_SEC_FEATURE  # noqa: PLC0415

    assert DEFAULT_REVIEW_TIMEOUT_SEC_FEATURE == 600, (
        f"DEFAULT_REVIEW_TIMEOUT_SEC_FEATURE should be 600, got {DEFAULT_REVIEW_TIMEOUT_SEC_FEATURE!r}"
    )


# ---------------------------------------------------------------------------
# AC4 — phase_45_spec regression guard (SIMPLE/COMPLEX/None/{} values unchanged)
# ---------------------------------------------------------------------------


def test_ac4_phase45_spec_regression_guard_existing_tiers() -> None:
    """AC4: regression guard — existing tier values unchanged post-FEATURE insertion.

    SIMPLE → 300, COMPLEX → 900, None → 300, {} → 300.
    This test PASSES today (forcing-function for non-regression on existing surface).
    """
    from bytedigger_engine.workflows.phase_45_spec import _resolve_review_timeout_sec  # noqa: PLC0415

    result_simple = _resolve_review_timeout_sec({"complexity": "SIMPLE"})
    assert result_simple == 300, f"SIMPLE should return 300, got {result_simple!r}"

    result_complex = _resolve_review_timeout_sec({"complexity": "COMPLEX"})
    assert result_complex == 900, f"COMPLEX should return 900, got {result_complex!r}"

    result_none = _resolve_review_timeout_sec(None)
    assert result_none == 300, f"None cfg should return 300, got {result_none!r}"

    result_empty = _resolve_review_timeout_sec({})
    assert result_empty == 300, f"Empty cfg should return 300, got {result_empty!r}"


# ---------------------------------------------------------------------------
# AC5 — phase_45_spec Opus floor preserved for SIMPLE+opus
# ---------------------------------------------------------------------------


def test_ac5_phase45_spec_opus_floor_preserved() -> None:
    """AC5: Opus floor preserved when complexity is SIMPLE with opus reviewer command.

    PASSES today (correctness guard — Opus floor must not be disturbed by FEATURE insertion).
    """
    from bytedigger_engine.workflows.phase_45_spec import _resolve_review_timeout_sec  # noqa: PLC0415

    result = _resolve_review_timeout_sec(
        {"complexity": "SIMPLE", "review_llm_command": ["claude", "-p", "--model", "opus"]}
    )
    assert result == 600, (
        f"Opus-floor for SIMPLE+opus reviewer should return 600, got {result!r}"
    )


# ---------------------------------------------------------------------------
# AC6 — phase_45_spec_lite _resolve_review_timeout_sec FEATURE → 600
# ---------------------------------------------------------------------------


def test_ac6_phase45_spec_lite_feature_returns_600() -> None:
    """AC6: phase_45_spec_lite._resolve_review_timeout_sec({"complexity": "FEATURE"}) → 600."""
    from bytedigger_engine.workflows.phase_45_spec_lite import _resolve_review_timeout_sec  # noqa: PLC0415

    result = _resolve_review_timeout_sec({"complexity": "FEATURE"})
    assert result == 600, f"Expected 600 for FEATURE complexity in lite, got {result!r}"


# ---------------------------------------------------------------------------
# AC7 — phase_45_spec_lite _resolve_review_timeout_sec COMPLEX → 900
# ---------------------------------------------------------------------------


def test_ac7_phase45_spec_lite_complex_returns_900() -> None:
    """AC7: phase_45_spec_lite._resolve_review_timeout_sec({"complexity": "COMPLEX"}) → 900."""
    from bytedigger_engine.workflows.phase_45_spec_lite import _resolve_review_timeout_sec  # noqa: PLC0415

    result = _resolve_review_timeout_sec({"complexity": "COMPLEX"})
    assert result == 900, f"Expected 900 for COMPLEX complexity in lite, got {result!r}"


# ---------------------------------------------------------------------------
# AC8 — phase_45_spec_lite None / {} / SIMPLE → 300
# ---------------------------------------------------------------------------


def test_ac8_phase45_spec_lite_baseline_returns_300() -> None:
    """AC8: phase_45_spec_lite._resolve_review_timeout_sec for None, {}, SIMPLE → 300."""
    from bytedigger_engine.workflows.phase_45_spec_lite import _resolve_review_timeout_sec  # noqa: PLC0415

    result_none = _resolve_review_timeout_sec(None)
    assert result_none == 300, f"None cfg should return 300 in lite, got {result_none!r}"

    result_empty = _resolve_review_timeout_sec({})
    assert result_empty == 300, f"Empty cfg should return 300 in lite, got {result_empty!r}"

    result_simple = _resolve_review_timeout_sec({"complexity": "SIMPLE"})
    assert result_simple == 300, f"SIMPLE should return 300 in lite, got {result_simple!r}"


# ---------------------------------------------------------------------------
# AC9 — phase_45_spec_lite explicit override wins
# ---------------------------------------------------------------------------


def test_ac9_phase45_spec_lite_explicit_override_wins() -> None:
    """AC9: explicit review_llm_timeout_sec beats complexity default → 777."""
    from bytedigger_engine.workflows.phase_45_spec_lite import _resolve_review_timeout_sec  # noqa: PLC0415

    result = _resolve_review_timeout_sec({"complexity": "COMPLEX", "review_llm_timeout_sec": 777})
    assert result == 777, f"Expected explicit override 777 in lite, got {result!r}"


# ---------------------------------------------------------------------------
# AC10 — phase_45_spec_lite invalid override + clamp behaviour
# ---------------------------------------------------------------------------


def test_ac10_phase45_spec_lite_invalid_override_and_clamp() -> None:
    """AC10: invalid/clamp behaviour in lite resolver.

    - "abc" override → 300 (falls through, no complexity set)
    - -5 override → 1 (clamp to max(1, ...))
    - 0 override + FEATURE complexity → 600 (0 is falsy, falls through to FEATURE)
    """
    from bytedigger_engine.workflows.phase_45_spec_lite import _resolve_review_timeout_sec  # noqa: PLC0415

    result_abc = _resolve_review_timeout_sec({"review_llm_timeout_sec": "abc"})
    assert result_abc == 300, (
        f"Invalid string override with no complexity should return 300, got {result_abc!r}"
    )

    result_neg = _resolve_review_timeout_sec({"review_llm_timeout_sec": -5})
    assert result_neg == 1, (
        f"Negative override -5 should be clamped to 1, got {result_neg!r}"
    )

    result_zero_feature = _resolve_review_timeout_sec(
        {"review_llm_timeout_sec": 0, "complexity": "FEATURE"}
    )
    assert result_zero_feature == 600, (
        f"Zero override (falsy) with FEATURE complexity should fall through to 600, "
        f"got {result_zero_feature!r}"
    )


# ---------------------------------------------------------------------------
# AC11 — phase_45_spec_lite new constants exported with correct values
# ---------------------------------------------------------------------------


def test_ac11_phase45_spec_lite_constants_present_and_correct() -> None:
    """AC11: new constants present in phase_45_spec_lite with correct values.

    DEFAULT_REVIEW_TIMEOUT_SEC_FEATURE == 600
    DEFAULT_REVIEW_TIMEOUT_SEC_COMPLEX == 900
    DEFAULT_REVIEW_TIMEOUT_SEC == 300 (baseline unchanged)
    """
    from bytedigger_engine.workflows.phase_45_spec_lite import (  # noqa: PLC0415
        DEFAULT_REVIEW_TIMEOUT_SEC,
        DEFAULT_REVIEW_TIMEOUT_SEC_COMPLEX,
        DEFAULT_REVIEW_TIMEOUT_SEC_FEATURE,
    )

    assert DEFAULT_REVIEW_TIMEOUT_SEC == 300, (
        f"DEFAULT_REVIEW_TIMEOUT_SEC (baseline) should remain 300, got {DEFAULT_REVIEW_TIMEOUT_SEC!r}"
    )
    assert DEFAULT_REVIEW_TIMEOUT_SEC_FEATURE == 600, (
        f"DEFAULT_REVIEW_TIMEOUT_SEC_FEATURE should be 600, got {DEFAULT_REVIEW_TIMEOUT_SEC_FEATURE!r}"
    )
    assert DEFAULT_REVIEW_TIMEOUT_SEC_COMPLEX == 900, (
        f"DEFAULT_REVIEW_TIMEOUT_SEC_COMPLEX should be 900, got {DEFAULT_REVIEW_TIMEOUT_SEC_COMPLEX!r}"
    )


# ---------------------------------------------------------------------------
# AC12 — phase_45_spec_lite call-site forcing-function (source-grep)
# ---------------------------------------------------------------------------


def test_ac12_phase45_spec_lite_call_site_rewired() -> None:
    """AC12: phase_45_spec_lite.py call-site must be rewired.

    New pattern present:  _resolve_review_timeout_sec(cfg)
    Old pattern absent:   int(cfg.get("review_llm_timeout_sec") or DEFAULT_REVIEW_TIMEOUT_SEC)
    """
    prod_path = Path(__file__).parent.parent / "bytedigger_engine" / "workflows" / "phase_45_spec_lite.py"
    src = prod_path.read_text(encoding="utf-8")

    new_pattern = '_resolve_review_timeout_sec(cfg)'
    assert new_pattern in src, (
        f"Expected '_resolve_review_timeout_sec(cfg)' to appear in phase_45_spec_lite.py "
        "but it was not found. GREEN has not rewired the call-site yet."
    )

    old_pattern = 'int(cfg.get("review_llm_timeout_sec") or DEFAULT_REVIEW_TIMEOUT_SEC)'
    assert old_pattern not in src, (
        "Old inline pattern 'int(cfg.get(\"review_llm_timeout_sec\") or DEFAULT_REVIEW_TIMEOUT_SEC)' "
        "still present in phase_45_spec_lite.py — call-site not yet rewired."
    )
