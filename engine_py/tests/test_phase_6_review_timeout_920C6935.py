"""RED tests for 920C6935 — size-scaled phase-6 review timeout helper.

Target: `_resolve_review_timeout_sec` + new constants in `phase_6_review.py`.
12 ACs matching spec §3 table.  All must FAIL today (ImportError / AttributeError /
assertion error) and PASS after GREEN ships the helper + constants + call-site rewiring.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

# ---------------------------------------------------------------------------
# AC1 — None cfg → 600 (regression guard / baseline)
# ---------------------------------------------------------------------------


def test_ac1_none_cfg_returns_600() -> None:
    """AC1: _resolve_review_timeout_sec(None) → 600."""
    from bytedigger_engine.workflows.phase_6_review import _resolve_review_timeout_sec, DEFAULT_REVIEW_TIMEOUT_SEC  # noqa: PLC0415

    result = _resolve_review_timeout_sec(None)
    assert result == 600, f"Expected 600 for None cfg, got {result!r}"
    assert result == DEFAULT_REVIEW_TIMEOUT_SEC


# ---------------------------------------------------------------------------
# AC2 — empty dict → 600
# ---------------------------------------------------------------------------


def test_ac2_empty_dict_returns_600() -> None:
    """AC2: _resolve_review_timeout_sec({}) → 600."""
    from bytedigger_engine.workflows.phase_6_review import _resolve_review_timeout_sec  # noqa: PLC0415

    result = _resolve_review_timeout_sec({})
    assert result == 600, f"Expected 600 for empty cfg, got {result!r}"


# ---------------------------------------------------------------------------
# AC3 — SIMPLE complexity → 600
# ---------------------------------------------------------------------------


def test_ac3_simple_complexity_returns_600() -> None:
    """AC3: _resolve_review_timeout_sec({'complexity': 'SIMPLE'}) → 600."""
    from bytedigger_engine.workflows.phase_6_review import _resolve_review_timeout_sec  # noqa: PLC0415

    result = _resolve_review_timeout_sec({"complexity": "SIMPLE"})
    assert result == 600, f"Expected 600 for SIMPLE, got {result!r}"


# ---------------------------------------------------------------------------
# AC4 — FEATURE complexity → 1000
# ---------------------------------------------------------------------------


def test_ac4_feature_complexity_returns_1000() -> None:
    """AC4: _resolve_review_timeout_sec({'complexity': 'FEATURE'}) → 1000."""
    from bytedigger_engine.workflows.phase_6_review import _resolve_review_timeout_sec, DEFAULT_REVIEW_TIMEOUT_SEC_FEATURE  # noqa: PLC0415

    result = _resolve_review_timeout_sec({"complexity": "FEATURE"})
    assert result == 1000, f"Expected 1000 for FEATURE, got {result!r}"
    assert result == DEFAULT_REVIEW_TIMEOUT_SEC_FEATURE


# ---------------------------------------------------------------------------
# AC5 — COMPLEX complexity → 1500
# ---------------------------------------------------------------------------


def test_ac5_complex_complexity_returns_1500() -> None:
    """AC5: _resolve_review_timeout_sec({'complexity': 'COMPLEX'}) → 1500."""
    from bytedigger_engine.workflows.phase_6_review import _resolve_review_timeout_sec, DEFAULT_REVIEW_TIMEOUT_SEC_COMPLEX  # noqa: PLC0415

    result = _resolve_review_timeout_sec({"complexity": "COMPLEX"})
    assert result == 1500, f"Expected 1500 for COMPLEX, got {result!r}"
    assert result == DEFAULT_REVIEW_TIMEOUT_SEC_COMPLEX


# ---------------------------------------------------------------------------
# AC6 — case-insensitive complexity matching
# ---------------------------------------------------------------------------


def test_ac6_case_insensitive_complexity() -> None:
    """AC6: lowercase/mixed-case complexity values map correctly."""
    from bytedigger_engine.workflows.phase_6_review import _resolve_review_timeout_sec  # noqa: PLC0415

    result_complex_lower = _resolve_review_timeout_sec({"complexity": "complex"})
    assert result_complex_lower == 1500, (
        f"Expected 1500 for 'complex' (lowercase), got {result_complex_lower!r}"
    )

    result_feature_mixed = _resolve_review_timeout_sec({"complexity": "Feature"})
    assert result_feature_mixed == 1000, (
        f"Expected 1000 for 'Feature' (mixed-case), got {result_feature_mixed!r}"
    )


# ---------------------------------------------------------------------------
# AC7 — unknown / empty / None complexity → 600
# ---------------------------------------------------------------------------


def test_ac7_unknown_complexity_falls_back_to_600() -> None:
    """AC7: unknown, empty string, or None complexity value → 600."""
    from bytedigger_engine.workflows.phase_6_review import _resolve_review_timeout_sec  # noqa: PLC0415

    assert _resolve_review_timeout_sec({"complexity": "UNKNOWN"}) == 600, (
        "Unknown complexity should return 600"
    )
    assert _resolve_review_timeout_sec({"complexity": ""}) == 600, (
        "Empty string complexity should return 600"
    )
    assert _resolve_review_timeout_sec({"complexity": None}) == 600, (
        "None complexity should return 600"
    )


# ---------------------------------------------------------------------------
# AC8 — explicit override wins over complexity
# ---------------------------------------------------------------------------


def test_ac8_explicit_override_wins() -> None:
    """AC8: review_llm_timeout_sec override beats complexity default."""
    from bytedigger_engine.workflows.phase_6_review import _resolve_review_timeout_sec  # noqa: PLC0415

    result = _resolve_review_timeout_sec(
        {"complexity": "COMPLEX", "review_llm_timeout_sec": 777}
    )
    assert result == 777, f"Expected explicit override 777, got {result!r}"


# ---------------------------------------------------------------------------
# AC9 — invalid override string falls through to complexity default
# ---------------------------------------------------------------------------


def test_ac9_invalid_override_falls_through_to_complexity() -> None:
    """AC9: non-numeric review_llm_timeout_sec falls through to complexity default."""
    from bytedigger_engine.workflows.phase_6_review import _resolve_review_timeout_sec  # noqa: PLC0415

    # With COMPLEX complexity: invalid override → 1500
    result_complex = _resolve_review_timeout_sec(
        {"complexity": "COMPLEX", "review_llm_timeout_sec": "abc"}
    )
    assert result_complex == 1500, (
        f"Expected 1500 (COMPLEX fallback) after invalid override, got {result_complex!r}"
    )

    # Without complexity set: invalid override → 600
    result_no_complexity = _resolve_review_timeout_sec(
        {"review_llm_timeout_sec": "abc"}
    )
    assert result_no_complexity == 600, (
        f"Expected 600 (no-complexity fallback) after invalid override, got {result_no_complexity!r}"
    )


# ---------------------------------------------------------------------------
# AC10 — negative/zero override behaviour
# ---------------------------------------------------------------------------


def test_ac10_negative_override_clamped_zero_is_falsy() -> None:
    """AC10: negative override clamped to 1; zero is falsy so falls through."""
    from bytedigger_engine.workflows.phase_6_review import _resolve_review_timeout_sec  # noqa: PLC0415

    # Negative override → clamped to max(1, int(-5)) = 1
    result_negative = _resolve_review_timeout_sec({"review_llm_timeout_sec": -5})
    assert result_negative == 1, (
        f"Expected 1 for negative override -5, got {result_negative!r}"
    )

    # Zero override: `if raw:` treats 0 as falsy → falls through to complexity default (600)
    result_zero = _resolve_review_timeout_sec({"review_llm_timeout_sec": 0})
    assert result_zero == 600, (
        f"Expected 600 for zero override (falsy fall-through), got {result_zero!r}"
    )


# ---------------------------------------------------------------------------
# AC11 — new constants present and have correct values
# ---------------------------------------------------------------------------


def test_ac11_new_constants_present_and_correct() -> None:
    """AC11: DEFAULT_REVIEW_TIMEOUT_SEC_FEATURE == 1000, DEFAULT_REVIEW_TIMEOUT_SEC_COMPLEX == 1500,
    DEFAULT_REVIEW_TIMEOUT_SEC == 600 (sibling guard — unchanged)."""
    from bytedigger_engine.workflows.phase_6_review import (  # noqa: PLC0415
        DEFAULT_REVIEW_TIMEOUT_SEC,
        DEFAULT_REVIEW_TIMEOUT_SEC_COMPLEX,
        DEFAULT_REVIEW_TIMEOUT_SEC_FEATURE,
    )

    assert DEFAULT_REVIEW_TIMEOUT_SEC == 600, (
        f"DEFAULT_REVIEW_TIMEOUT_SEC should remain 600, got {DEFAULT_REVIEW_TIMEOUT_SEC!r}"
    )
    assert DEFAULT_REVIEW_TIMEOUT_SEC_FEATURE == 1000, (
        f"DEFAULT_REVIEW_TIMEOUT_SEC_FEATURE should be 1000, got {DEFAULT_REVIEW_TIMEOUT_SEC_FEATURE!r}"
    )
    assert DEFAULT_REVIEW_TIMEOUT_SEC_COMPLEX == 1500, (
        f"DEFAULT_REVIEW_TIMEOUT_SEC_COMPLEX should be 1500, got {DEFAULT_REVIEW_TIMEOUT_SEC_COMPLEX!r}"
    )


# ---------------------------------------------------------------------------
# AC12 — call-site wiring forcing-function (per spec §3.2)
# ---------------------------------------------------------------------------


def test_ac12_call_site_wiring_in_phase_6_review() -> None:
    """AC12: both call-sites (:1085 primary, :1719 retry) must be rewired to
    _resolve_review_timeout_sec(cfg), and the old int(cfg.get(...) or ...) pattern
    must not appear anywhere in phase_6_review.py."""
    prod_path = HERE.parent / "bytedigger_engine" / "workflows" / "phase_6_review.py"
    source = prod_path.read_text(encoding="utf-8")

    # Both call-sites must carry the new helper (with trailing comma)
    new_pattern = '_resolve_review_timeout_sec(cfg),'
    count = source.count(new_pattern)
    assert count >= 2, (
        f"Expected '_resolve_review_timeout_sec(cfg),' to appear at least 2 times "
        f"in phase_6_review.py, found {count} occurrence(s). "
        "GREEN has not wired both call-sites yet."
    )

    # The old inline expression must be gone
    old_pattern = 'int(cfg.get("review_llm_timeout_sec") or DEFAULT_REVIEW_TIMEOUT_SEC)'
    assert old_pattern not in source, (
        "Old pattern 'int(cfg.get(\"review_llm_timeout_sec\") or DEFAULT_REVIEW_TIMEOUT_SEC)' "
        "still present in phase_6_review.py — call-sites not yet rewired."
    )
