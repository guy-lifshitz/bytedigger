"""W7 RED tests — sister-parser hardening for phase_5_implement.

Closes /ultrareview Wave 7 findings:

* Finding #4 (HIGH, line 215, ``_parse_green_status``)
  Substring `in upper` checks + early-return BLOCKED-wins precedence.
  Sister parser ``_parse_verdict`` (line 206) was migrated to
  ``_last_marker_wins`` / ``rfind`` in Wave 4. ``_parse_green_status`` was
  missed. Two bugs:
    (a) substring not anchored / no rfind → BLOCKED then COMPLETE returns
        BLOCKED instead of last-wins COMPLETE
    (b) early-return ``if has_blocked: return GREEN_BLOCKED`` runs before
        checking ``has_complete`` → if the LLM internal loop produced
        ``GREEN BLOCKED`` then later ``GREEN COMPLETE`` on retry, returns
        BLOCKED instead of last-wins COMPLETE

* Finding #1 (HIGH, line 237, ``_parse_red_test_paths``)
  ``re.search(r"Files:\\s*\\[([^\\]]*)\\]", raw)`` returns FIRST match.
  If LLM output contains an example/template ``Files: [...]`` block
  followed by the real one (common with prompt schemas that repeat the
  format), picks the template. Sister to ``_last_marker_wins`` pattern —
  should pick LAST occurrence.

These tests follow the W4 ACx-style naming and are intended to FAIL against
the current ``phase_5_implement.py`` and PASS once GREEN replaces the
substring/first-match logic with last-marker-wins / last-files-wins.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))


# ─── Finding #4: _parse_green_status last-marker-wins ────────────────────────


def test_green_status_last_marker_wins_complete_after_blocked():
    """LLM internal loop: cycle-1 BLOCKED, cycle-3 COMPLETE → COMPLETE.

    Reproduces #4(b). Current early-return on ``has_blocked`` ignores the
    later COMPLETE marker. With last-marker-wins, COMPLETE wins because it
    appears later in the output.
    """
    from bytedigger_engine.workflows.phase_5_implement import _parse_green_status, GREEN_COMPLETE  # noqa: PLC0415

    raw = (
        "cycle 1 attempt:\n"
        "GREEN BLOCKED — pytest collection failure\n"
        "cycle 2: still failing\n"
        "cycle 3: fixed;\n"
        "GREEN COMPLETE\n"
        "Files: [tests/foo.py]\n"
    )
    result = _parse_green_status(raw)
    assert result == GREEN_COMPLETE, (
        f"Expected GREEN_COMPLETE (last-marker-wins: BLOCKED then COMPLETE), "
        f"got {result!r}. Fix: port to _last_marker_wins like _parse_verdict."
    )


def test_green_status_last_marker_wins_blocked_after_complete():
    """LLM walked back: cycle-2 COMPLETE, cycle-3 BLOCKED → BLOCKED.

    Locks last-marker-wins semantics in the opposite direction so the GREEN
    fix can't degenerate into "always-COMPLETE-wins". Currently this returns
    BLOCKED for the WRONG reason (early-return on first BLOCKED-substring
    hit anywhere). Post-fix it must still return BLOCKED, but because
    ``rfind('GREEN BLOCKED') > rfind('GREEN COMPLETE')``.
    """
    from bytedigger_engine.workflows.phase_5_implement import _parse_green_status, GREEN_BLOCKED  # noqa: PLC0415

    raw = (
        "cycle 2:\n"
        "GREEN COMPLETE — initial pass\n"
        "post-validator review found regressions\n"
        "cycle 3: walking back;\n"
        "GREEN BLOCKED — cannot reconcile\n"
    )
    result = _parse_green_status(raw)
    # Position of the marker matters: BLOCKED must be the LAST marker, not
    # just present anywhere. We assert by position to make this meaningful.
    assert result == GREEN_BLOCKED, (
        f"Expected GREEN_BLOCKED (last-marker-wins: COMPLETE then BLOCKED), "
        f"got {result!r}."
    )
    # Sanity: BLOCKED really is later than COMPLETE in the input.
    assert raw.upper().rfind("GREEN BLOCKED") > raw.upper().rfind("GREEN COMPLETE"), (
        "Test fixture assumption broken: BLOCKED must appear after COMPLETE."
    )


def test_green_status_substring_in_prose_does_not_trigger():
    """Prose mention of 'GREEN BLOCKED' before a real GREEN COMPLETE marker.

    Reproduces #4(a). Currently returns GREEN_BLOCKED because the substring
    ``"GREEN BLOCKED"`` appears in prose, and the early-return fires before
    we ever look at COMPLETE. Expected: GREEN_COMPLETE (the real terminal
    marker is at the end).

    NOTE on the substring-anchor decision (for GREEN):
      A pure rfind-based ``_last_marker_wins`` does NOT eliminate this bug
      class — it still substring-matches prose. If a future LLM emits
      ``"...not GREEN COMPLETE..."`` AFTER a real ``GREEN BLOCKED`` marker,
      rfind would still pick the prose. ``_parse_verdict`` (sister parser)
      currently has the same flaw (verified manually — see W7 RED report).

      Minimum-scope GREEN for THIS finding: port to ``_last_marker_wins`` —
      this fixes the early-return precedence (real terminal marker at end
      of output now wins over earlier prose). This test is constructed so
      that goal alone is sufficient to make it pass: the real
      ``GREEN COMPLETE`` is the LAST string occurrence in the input.

      Stricter line-anchoring (``^GREEN BLOCKED`` / standalone-token) would
      close the residual class but expands W7 scope to also touch
      ``_parse_verdict``. Defer that to a follow-up agreement; W7 keeps
      sister-parser parity.
    """
    from bytedigger_engine.workflows.phase_5_implement import _parse_green_status, GREEN_NO_MARKER  # noqa: PLC0415

    raw = (
        "The validator said this is not GREEN BLOCKED but something else "
        "entirely. The worker should not confuse the two.\n"
        "Worker reports: GREEN COMPLETE — embedded in prose\n"
        "Files: [tests/foo.py]\n"
    )
    # Sanity: both markers appear only as prose substrings, never line-anchored.
    assert "GREEN BLOCKED" in raw and "GREEN COMPLETE" in raw, (
        "Fixture broken: both marker substrings must be present (in prose only)."
    )

    result = _parse_green_status(raw)
    # W11 line-anchor: prose-embedded markers must NOT trigger. Fallback expected.
    assert result == GREEN_NO_MARKER, (
        f"Expected GREEN_NO_MARKER (line-anchor rejects prose-embedded markers), "
        f"got {result!r}. W11 line-anchored _last_marker_wins requires markers "
        "to start a line; substrings inside prose lines must not match."
    )


def test_green_status_no_marker_returns_no_marker():
    """Regression guard: empty / no-marker input → GREEN_NO_MARKER."""
    from bytedigger_engine.workflows.phase_5_implement import _parse_green_status, GREEN_NO_MARKER  # noqa: PLC0415

    assert _parse_green_status("") == GREEN_NO_MARKER
    assert _parse_green_status("worker output without any marker at all") == GREEN_NO_MARKER


# _parse_red_test_paths tests deleted — γ cleanup 8.5 (A4461B8F) removed the symbol.
