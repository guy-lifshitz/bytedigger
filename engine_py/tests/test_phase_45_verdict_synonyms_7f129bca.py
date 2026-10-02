"""Tests for 7f129bca — Phase 4.5 verdict parser PASS/APPROVED synonym widening.

AC1–AC6: phase_45_spec._parse_verdict
(AC7–AC10 covered the sibling SIMPLE-only parser; retired by bd#89 P2b.)
"""

from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).parent
ENGINE_ROOT = HERE.parent
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))

from bytedigger_engine.workflows.phase_45_spec import (  # noqa: E402
    _parse_verdict as _parse_verdict_spec,
    VERDICT_SHIP,
    VERDICT_REVISE,
    VERDICT_UNKNOWN,
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

