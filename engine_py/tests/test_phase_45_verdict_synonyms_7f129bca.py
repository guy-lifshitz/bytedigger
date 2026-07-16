"""Tests for 7f129bca — Phase 4.5 verdict parser PASS/APPROVED synonym widening.

AC1–AC6: phase_45_spec._parse_verdict
AC7–AC10: phase_45_spec_lite._parse_verdict

Pre-GREEN expected state: AC1, AC2, AC3, AC7, AC8 FAIL (UNKNOWN returned instead of SHIP).
AC4, AC5, AC6, AC9, AC10 PASS (regression guards on legacy behaviour).
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).parent
ENGINE_ROOT = HERE.parent
for p in (str(ENGINE_ROOT), str(ENGINE_ROOT / "lib"), str(ENGINE_ROOT / "workflows")):
    if p not in sys.path:
        sys.path.insert(0, p)

from phase_45_spec import (  # noqa: E402
    _parse_verdict as _parse_verdict_spec,
    VERDICT_SHIP,
    VERDICT_REVISE,
    VERDICT_UNKNOWN,
)
from phase_45_spec_lite import (  # noqa: E402
    _parse_verdict as _parse_verdict_lite,
)


def test_ac1_phase45_spec_verdict_pass_normalizes_to_ship():
    """AC1: phase_45_spec._parse_verdict('## Verdict\nPASS\n') returns VERDICT_SHIP (exact equality)."""
    result = _parse_verdict_spec("## Verdict\nPASS\n")
    assert result == VERDICT_SHIP


def test_ac2_phase45_spec_verdict_approved_normalizes_to_ship():
    """AC2: phase_45_spec._parse_verdict('## Verdict\nAPPROVED\n') returns VERDICT_SHIP (exact equality)."""
    result = _parse_verdict_spec("## Verdict\nAPPROVED\n")
    assert result == VERDICT_SHIP


def test_ac3_phase45_spec_verdict_pass_lowercase_normalizes_to_ship():
    """AC3: phase_45_spec._parse_verdict('## Verdict\npass\n') returns VERDICT_SHIP (case-insensitive preserved)."""
    result = _parse_verdict_spec("## Verdict\npass\n")
    assert result == VERDICT_SHIP


def test_ac4_phase45_spec_verdict_ship_unchanged():
    """AC4: phase_45_spec._parse_verdict('## Verdict\nSHIP\n') returns VERDICT_SHIP (legacy regression guard)."""
    result = _parse_verdict_spec("## Verdict\nSHIP\n")
    assert result == VERDICT_SHIP


def test_ac5_phase45_spec_verdict_revise_unchanged():
    """AC5: phase_45_spec._parse_verdict('## Verdict\nREVISE\n') returns VERDICT_REVISE (legacy regression guard)."""
    result = _parse_verdict_spec("## Verdict\nREVISE\n")
    assert result == VERDICT_REVISE


def test_ac6_phase45_spec_verdict_pass_without_header_returns_unknown():
    """AC6: phase_45_spec._parse_verdict('PASS') (no ## Verdict header) returns VERDICT_UNKNOWN (anchoring preserved — false-positive guard)."""
    result = _parse_verdict_spec("PASS")
    assert result == VERDICT_UNKNOWN


def test_ac7_phase45_spec_lite_verdict_pass_normalizes_to_ship():
    """AC7: phase_45_spec_lite._parse_verdict('## Verdict\nPASS\n') returns VERDICT_SHIP (sibling module, exact equality)."""
    result = _parse_verdict_lite("## Verdict\nPASS\n")
    assert result == VERDICT_SHIP


def test_ac8_phase45_spec_lite_verdict_approved_normalizes_to_ship():
    """AC8: phase_45_spec_lite._parse_verdict('## Verdict\nAPPROVED\n') returns VERDICT_SHIP (sibling module, exact equality)."""
    result = _parse_verdict_lite("## Verdict\nAPPROVED\n")
    assert result == VERDICT_SHIP


def test_ac9_phase45_spec_lite_verdict_ship_unchanged():
    """AC9: phase_45_spec_lite._parse_verdict('## Verdict\nSHIP\n') returns VERDICT_SHIP (sibling regression guard)."""
    result = _parse_verdict_lite("## Verdict\nSHIP\n")
    assert result == VERDICT_SHIP


def test_ac10_phase45_spec_lite_verdict_revise_unchanged():
    """AC10: phase_45_spec_lite._parse_verdict('## Verdict\nREVISE\n') returns VERDICT_REVISE (sibling regression guard)."""
    result = _parse_verdict_lite("## Verdict\nREVISE\n")
    assert result == VERDICT_REVISE
