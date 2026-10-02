"""Tests for D1F51D7A — phase_45_spec FEATURE-tier review-timeout resolver.

Scope:
  - phase_45_spec._resolve_review_timeout_sec: adds FEATURE (600) branch between COMPLEX and Opus-floor.

Parent SYSTEMATIC: 97B6CF02 (timeout policy unification across the spec phase and
phase_6_review).  Sibling: 920C6935 (phase_6_review size-scaled timeouts, 2026-05-20).

AC1-AC3: phase_45_spec FEATURE tier.
AC4-AC5: phase_45_spec regression guards.
(AC6-AC12 covered the SIMPLE-only twin workflow dropped by bd#89 P2b; retired.)
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


# AC6-AC12 (the dropped SIMPLE-only spec workflow's own resolver, constants and
# call-site rewire) retired by bd#89 P2b; AC1-AC5 above are the twin for the
# remaining phase_45_spec._resolve_review_timeout_sec.
