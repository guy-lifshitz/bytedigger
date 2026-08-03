"""W11 RED tests — line-anchor markers in _last_marker_wins.

Closes W7-FOLLOWUP MED follow-up: ``_parse_verdict`` and ``_parse_green_status``
both delegate to ``_last_marker_wins`` which uses ``str.rfind`` — matches the
marker substring anywhere in the raw output, including inside prose-quoted
text. Bug:

    We were told: "NEVER REPORT VERDICT: PASS unless tests pass"

With no other VERDICT marker, ``rfind("VERDICT: PASS")`` matches the prose
quote → returns VERDICT_PASS. Same flaw for ``GREEN BLOCKED`` etc.

Fix encoded in tests: change ``_last_marker_wins`` to require markers at
LINE START (optionally preceded by whitespace) — e.g. regex
``re.finditer(r"^\\s*MARKER", raw, re.MULTILINE | re.IGNORECASE)`` and
keep the LAST match's position. Last-line-anchored match wins.

These tests are pure unit tests against the parser helpers (no fixtures
needed — _parse_verdict / _parse_green_status are functions over str).

Expected:
  * ``test_W11_parse_verdict_prose_quoted_pass_does_not_match`` —
    FAIL on current code (returns VERDICT_PASS via rfind); PASS once
    line-anchor lands.
  * ``test_W11_parse_green_status_prose_quoted_blocked_does_not_match`` —
    FAIL on current code (returns GREEN_BLOCKED via rfind); PASS once
    line-anchor lands.
  * Three regression guards (line-start, indented, last-wins) — PASS on
    current code; protect happy paths from over-correction.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))


# ─── W7-FOLLOWUP: prose-quoted markers must NOT match ───────────────────────


def test_W11_parse_verdict_prose_quoted_pass_does_not_match():
    """Prose-quoted VERDICT: PASS inside instructions must not be picked up.

    Reproduces W7-FOLLOWUP. ``rfind`` matches the substring anywhere; with
    no genuine line-start VERDICT marker, the prose quote wins. Fix: line-
    anchor markers (regex ``^\\s*VERDICT: PASS`` with re.MULTILINE).
    """
    from bytedigger_engine.workflows.phase_5_implement import _parse_verdict, VERDICT_UNKNOWN  # noqa: PLC0415

    raw = 'We were told: "NEVER REPORT VERDICT: PASS unless tests pass"\n'
    result = _parse_verdict(raw)
    assert result == VERDICT_UNKNOWN, (
        f"Expected VERDICT_UNKNOWN — only prose-quoted PASS present, no "
        f"line-start marker. Got {result!r}. Fix: line-anchor marker match "
        f"in _last_marker_wins (require ^\\s*MARKER via re.MULTILINE)."
    )


def test_W11_parse_green_status_prose_quoted_blocked_does_not_match():
    """Prose-quoted GREEN BLOCKED inside instructions must not be picked up."""
    from bytedigger_engine.workflows.phase_5_implement import _parse_green_status, GREEN_NO_MARKER  # noqa: PLC0415

    raw = "Avoid GREEN BLOCKED in your output\n"
    result = _parse_green_status(raw)
    assert result == GREEN_NO_MARKER, (
        f"Expected GREEN_NO_MARKER — only prose-quoted BLOCKED present, no "
        f"line-start marker. Got {result!r}. Fix: line-anchor marker match."
    )


# ─── Regression guards: legitimate line-anchored markers still match ────────


def test_W11_parse_verdict_line_start_pass_matches_regression():
    """VERDICT: PASS at line start (legit LLM output) must still match."""
    from bytedigger_engine.workflows.phase_5_implement import _parse_verdict, VERDICT_PASS  # noqa: PLC0415

    raw = "VERDICT: PASS\n"
    assert _parse_verdict(raw) == VERDICT_PASS


def test_W11_parse_verdict_indented_pass_matches_regression():
    """Indented VERDICT: PASS (whitespace before marker) must still match."""
    from bytedigger_engine.workflows.phase_5_implement import _parse_verdict, VERDICT_PASS  # noqa: PLC0415

    raw = "  VERDICT: PASS\n"
    assert _parse_verdict(raw) == VERDICT_PASS, (
        "Indented marker (^\\s*MARKER) must match — line-anchor regex "
        "should allow leading whitespace."
    )


def test_W11_parse_verdict_last_line_anchored_wins_regression():
    """Both markers at line start — last-line-wins picks the trailing one.

    Existing contract preserved: when multiple legitimate markers appear,
    the last one wins (LLM revising its verdict).
    """
    from bytedigger_engine.workflows.phase_5_implement import _parse_verdict, VERDICT_PASS  # noqa: PLC0415

    raw = "VERDICT: FAIL\nVERDICT: PASS\n"
    assert _parse_verdict(raw) == VERDICT_PASS


def test_W11_parse_green_status_line_start_blocked_matches_regression():
    """GREEN BLOCKED at line start (legit LLM output) must still match."""
    from bytedigger_engine.workflows.phase_5_implement import _parse_green_status, GREEN_BLOCKED  # noqa: PLC0415

    raw = "GREEN BLOCKED\n"
    assert _parse_green_status(raw) == GREEN_BLOCKED
