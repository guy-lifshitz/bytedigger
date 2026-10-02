"""Tests for the phase_45_spec reviewer schema (originally E94AFD31).

bd#89 P2b: the cycle-1 reviewer cross-check directives (AC1-AC4: ctx-json keys,
override keys, org_config wrapper, invocation-example self-contradiction)
existed ONLY in the dropped SIMPLE-only spec workflow's schema. They are NOT in
phase_45_spec._review_output_schema() (GAP-5, measured at P2b RED), so AC1-AC4
are retired here and filed as a follow-up. AC5 (schema section headers) is the
kept-behavior guard and is re-pointed to phase_45_spec.
"""
from __future__ import annotations

from bytedigger_engine.workflows.phase_45_spec import _review_output_schema


# ── AC5: existing schema section headers preserved ───────────────────────────

def test_existing_schema_preserved() -> None:
    """AC5: _review_output_schema() must still return all four required section
    headers so downstream parsers don't break.

    EXPECTED TO PASS on current code (these headers already exist).
    """
    s = _review_output_schema()

    required_headers = (
        "## Verdict",
        "## Findings (structured)",
        "## Concerns Checked",
        "## Rationale",
    )

    for header in required_headers:
        assert header in s, (
            f"AC5 FAIL: _review_output_schema() is missing required section "
            f"header {header!r} — downstream parsers depend on this header."
        )
