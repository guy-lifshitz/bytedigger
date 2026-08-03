"""RED tests for 6CAFEB9B — size-scaled phase-6 satisfaction timeout helper.

Target: `_resolve_satisfaction_timeout_sec` + new constants
`DEFAULT_SATISFACTION_TIMEOUT_SEC_FEATURE` (1000) and
`DEFAULT_SATISFACTION_TIMEOUT_SEC_COMPLEX` (1500) in `phase_6_review.py`.
8 ACs matching spec §3 table.  All must FAIL today (ImportError / AttributeError /
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
    """AC1: _resolve_satisfaction_timeout_sec(None) → 600 == DEFAULT_SATISFACTION_TIMEOUT_SEC."""
    from bytedigger_engine.workflows.phase_6_review import _resolve_satisfaction_timeout_sec, DEFAULT_SATISFACTION_TIMEOUT_SEC  # noqa: PLC0415

    result = _resolve_satisfaction_timeout_sec(None)
    assert result == 600, f"Expected 600 for None cfg, got {result!r}"
    assert result == DEFAULT_SATISFACTION_TIMEOUT_SEC


# ---------------------------------------------------------------------------
# AC2 — empty / SIMPLE / unknown / empty-string / None complexity → 600
# ---------------------------------------------------------------------------


def test_ac2_non_scaling_complexities_return_600() -> None:
    """AC2: {}, SIMPLE, UNKNOWN, empty string, None complexity → 600."""
    from bytedigger_engine.workflows.phase_6_review import _resolve_satisfaction_timeout_sec  # noqa: PLC0415

    for cfg in [
        {},
        {"complexity": "SIMPLE"},
        {"complexity": "UNKNOWN"},
        {"complexity": ""},
        {"complexity": None},
    ]:
        result = _resolve_satisfaction_timeout_sec(cfg)
        assert result == 600, (
            f"Expected 600 for cfg={cfg!r}, got {result!r}"
        )


# ---------------------------------------------------------------------------
# AC3 — FEATURE complexity → 1000 (case-insensitive)
# ---------------------------------------------------------------------------


def test_ac3_feature_complexity_returns_1000() -> None:
    """AC3: FEATURE → 1000 == DEFAULT_SATISFACTION_TIMEOUT_SEC_FEATURE; 'Feature' also → 1000."""
    from bytedigger_engine.workflows.phase_6_review import (  # noqa: PLC0415
        _resolve_satisfaction_timeout_sec,
        DEFAULT_SATISFACTION_TIMEOUT_SEC_FEATURE,
    )

    result = _resolve_satisfaction_timeout_sec({"complexity": "FEATURE"})
    assert result == 1000, f"Expected 1000 for FEATURE, got {result!r}"
    assert result == DEFAULT_SATISFACTION_TIMEOUT_SEC_FEATURE

    result_mixed = _resolve_satisfaction_timeout_sec({"complexity": "Feature"})
    assert result_mixed == 1000, (
        f"Expected 1000 for 'Feature' (mixed-case), got {result_mixed!r}"
    )


# ---------------------------------------------------------------------------
# AC4 — COMPLEX complexity → 1500 (case-insensitive)
# ---------------------------------------------------------------------------


def test_ac4_complex_complexity_returns_1500() -> None:
    """AC4: COMPLEX → 1500 == DEFAULT_SATISFACTION_TIMEOUT_SEC_COMPLEX; 'complex' also → 1500."""
    from bytedigger_engine.workflows.phase_6_review import (  # noqa: PLC0415
        _resolve_satisfaction_timeout_sec,
        DEFAULT_SATISFACTION_TIMEOUT_SEC_COMPLEX,
    )

    result = _resolve_satisfaction_timeout_sec({"complexity": "COMPLEX"})
    assert result == 1500, f"Expected 1500 for COMPLEX, got {result!r}"
    assert result == DEFAULT_SATISFACTION_TIMEOUT_SEC_COMPLEX

    result_lower = _resolve_satisfaction_timeout_sec({"complexity": "complex"})
    assert result_lower == 1500, (
        f"Expected 1500 for 'complex' (lowercase), got {result_lower!r}"
    )


# ---------------------------------------------------------------------------
# AC5 — explicit override wins over complexity default
# ---------------------------------------------------------------------------


def test_ac5_explicit_override_wins() -> None:
    """AC5: satisfaction_llm_timeout_sec override beats complexity default."""
    from bytedigger_engine.workflows.phase_6_review import _resolve_satisfaction_timeout_sec  # noqa: PLC0415

    result = _resolve_satisfaction_timeout_sec(
        {"complexity": "COMPLEX", "satisfaction_llm_timeout_sec": 777}
    )
    assert result == 777, f"Expected explicit override 777, got {result!r}"


# ---------------------------------------------------------------------------
# AC6 — invalid / negative / zero override edge cases
# ---------------------------------------------------------------------------


def test_ac6_invalid_override_edge_cases() -> None:
    """AC6: non-numeric override falls through; negative clamped to 1; zero is falsy → default."""
    from bytedigger_engine.workflows.phase_6_review import _resolve_satisfaction_timeout_sec  # noqa: PLC0415

    # Non-numeric string with COMPLEX complexity → falls through to 1500
    result_complex_invalid = _resolve_satisfaction_timeout_sec(
        {"complexity": "COMPLEX", "satisfaction_llm_timeout_sec": "abc"}
    )
    assert result_complex_invalid == 1500, (
        f"Expected 1500 (COMPLEX fallback) after invalid string override, "
        f"got {result_complex_invalid!r}"
    )

    # Non-numeric string without complexity set → falls through to 600
    result_no_complexity_invalid = _resolve_satisfaction_timeout_sec(
        {"satisfaction_llm_timeout_sec": "abc"}
    )
    assert result_no_complexity_invalid == 600, (
        f"Expected 600 (no-complexity fallback) after invalid string override, "
        f"got {result_no_complexity_invalid!r}"
    )

    # Negative override → clamped to max(1, int(-5)) = 1
    result_negative = _resolve_satisfaction_timeout_sec(
        {"satisfaction_llm_timeout_sec": -5}
    )
    assert result_negative == 1, (
        f"Expected 1 for negative override -5, got {result_negative!r}"
    )

    # Zero override: falsy → falls through to complexity default (600)
    result_zero = _resolve_satisfaction_timeout_sec(
        {"satisfaction_llm_timeout_sec": 0}
    )
    assert result_zero == 600, (
        f"Expected 600 for zero override (falsy fall-through), got {result_zero!r}"
    )


# ---------------------------------------------------------------------------
# AC7 — new constants present and have correct values
# ---------------------------------------------------------------------------


def test_ac7_new_constants_present_and_correct() -> None:
    """AC7: DEFAULT_SATISFACTION_TIMEOUT_SEC==600, _FEATURE==1000, _COMPLEX==1500."""
    from bytedigger_engine.workflows.phase_6_review import (  # noqa: PLC0415
        DEFAULT_SATISFACTION_TIMEOUT_SEC,
        DEFAULT_SATISFACTION_TIMEOUT_SEC_FEATURE,
        DEFAULT_SATISFACTION_TIMEOUT_SEC_COMPLEX,
    )

    assert DEFAULT_SATISFACTION_TIMEOUT_SEC == 600, (
        f"DEFAULT_SATISFACTION_TIMEOUT_SEC should remain 600, "
        f"got {DEFAULT_SATISFACTION_TIMEOUT_SEC!r}"
    )
    assert DEFAULT_SATISFACTION_TIMEOUT_SEC_FEATURE == 1000, (
        f"DEFAULT_SATISFACTION_TIMEOUT_SEC_FEATURE should be 1000, "
        f"got {DEFAULT_SATISFACTION_TIMEOUT_SEC_FEATURE!r}"
    )
    assert DEFAULT_SATISFACTION_TIMEOUT_SEC_COMPLEX == 1500, (
        f"DEFAULT_SATISFACTION_TIMEOUT_SEC_COMPLEX should be 1500, "
        f"got {DEFAULT_SATISFACTION_TIMEOUT_SEC_COMPLEX!r}"
    )


# ---------------------------------------------------------------------------
# AC8 — call-site wiring forcing-function (per spec §3.2)
# ---------------------------------------------------------------------------


def test_ac8_call_site_wiring_in_phase_6_review() -> None:
    """AC8: call-site must be rewired to _resolve_satisfaction_timeout_sec(cfg);
    old int(cfg.get('satisfaction_llm_timeout_sec') or DEFAULT_SATISFACTION_TIMEOUT_SEC)
    must not appear anywhere in phase_6_review.py."""
    prod_path = HERE.parent / "bytedigger_engine" / "workflows" / "phase_6_review.py"
    source = prod_path.read_text(encoding="utf-8")

    # Call-site must carry the new helper
    new_pattern = '_resolve_satisfaction_timeout_sec(cfg)'
    assert new_pattern in source, (
        f"Expected '_resolve_satisfaction_timeout_sec(cfg)' to appear "
        f"in phase_6_review.py — GREEN has not wired the call-site yet."
    )

    # The old inline expression must be gone
    old_pattern = 'int(cfg.get("satisfaction_llm_timeout_sec") or DEFAULT_SATISFACTION_TIMEOUT_SEC)'
    assert old_pattern not in source, (
        "Old pattern 'int(cfg.get(\"satisfaction_llm_timeout_sec\") or "
        "DEFAULT_SATISFACTION_TIMEOUT_SEC)' still present in phase_6_review.py "
        "— call-site not yet rewired."
    )
