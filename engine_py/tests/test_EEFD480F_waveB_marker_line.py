"""RED tests for EEFD480F Wave B — last_line_anchored_marker (P2) + routing.

New primitive `last_line_anchored_marker` does NOT exist yet (GREEN creates it
in lib/verdict_parse.py).  Per D1CF5FDF (non-collectable-hang rule): this
symbol is imported INSIDE each test body that exercises it — never at module
top level — so the file COLLECTS cleanly and tests FAIL at assertion time, not
at collection-time ImportError.

The phase parse functions (_parse_review_verdict, _parse_fix_status,
_parse_synthesizer_status, _parse_status_marker) DO exist today, so they are
imported normally (top-level from their modules).  Their NEW prose-ignoring
behaviour only activates after GREEN routes them through P2.

conftest singleton already adds engine_root + workflows/ to sys.path.
lib/ is inserted inside test bodies that need verdict_parse (same pattern as
Wave A: _ensure_lib_path() helper called inside body).

Agreement: EEFD480F · Wave B ACs: ACB1, ACB2, ACB-P2, ACB-P3, ACB-P4.
"""
from __future__ import annotations

from pathlib import Path

# Top-level imports: phase modules that already exist (GREEN changes their
# internal routing but keeps public signatures and module paths identical).
from bytedigger_engine.workflows.phase_6_review import _parse_review_verdict, _parse_fix_status  # noqa: E402
from bytedigger_engine.workflows.phase_7_synthesize import _parse_synthesizer_status  # noqa: E402
from bytedigger_engine.workflows.phase_2_explore import _parse_status_marker as _p2_parse_status_marker  # noqa: E402
from bytedigger_engine.workflows.phase_3_clarify import _parse_status_marker as _p3_parse_status_marker  # noqa: E402

HERE = Path(__file__).parent
ENGINE_ROOT = HERE.parent
_LIB_PATH = str(ENGINE_ROOT / "bytedigger_engine" / "lib")


# ─── helpers ─────────────────────────────────────────────────────────────────

def _ensure_lib_path() -> None:
    """Insert lib/ into sys.path inside a test body (not at module level)."""
    import sys  # noqa: PLC0415
    if _LIB_PATH not in sys.path:
        sys.path.insert(0, _LIB_PATH)


# ─── ACB1: P2 — line-start match vs prose-embedded non-match ─────────────────


def test_acb1_p2_line_start_verdict_pass_matches():
    """ACB1: `VERDICT: PASS` at line start is matched and returns mapped value."""
    _ensure_lib_path()
    from bytedigger_engine.lib.verdict_parse import last_line_anchored_marker  # noqa: PLC0415

    raw = "Some analysis.\nVERDICT: PASS\n"
    result = last_line_anchored_marker(
        raw,
        [
            ("VERDICT: PASS", "PASS"),
            ("VERDICT: PARTIAL", "PARTIAL"),
            ("VERDICT: FAIL", "FAIL"),
        ],
        fallback="UNKNOWN",
    )
    assert result == "PASS", (
        f"standalone line-start VERDICT: PASS should return 'PASS'; got {result!r}"
    )


def test_acb1_p2_prose_embedded_marker_does_not_match():
    """ACB1: mid-line `I considered VERDICT: FAIL` does NOT match; fallback returned."""
    _ensure_lib_path()
    from bytedigger_engine.lib.verdict_parse import last_line_anchored_marker  # noqa: PLC0415

    raw = "Review: I considered VERDICT: FAIL but rejected it.\n"
    result = last_line_anchored_marker(
        raw,
        [
            ("VERDICT: PASS", "PASS"),
            ("VERDICT: PARTIAL", "PARTIAL"),
            ("VERDICT: FAIL", "FAIL"),
        ],
        fallback="UNKNOWN",
    )
    assert result == "UNKNOWN", (
        f"prose-embedded VERDICT: FAIL must not match; got {result!r}"
    )


def test_acb1_p2_last_line_anchored_wins_over_earlier():
    """ACB1: two standalone markers — LAST one (highest offset) wins."""
    _ensure_lib_path()
    from bytedigger_engine.lib.verdict_parse import last_line_anchored_marker  # noqa: PLC0415

    raw = "VERDICT: PARTIAL\nMore discussion.\nVERDICT: PASS\n"
    result = last_line_anchored_marker(
        raw,
        [
            ("VERDICT: PASS", "PASS"),
            ("VERDICT: PARTIAL", "PARTIAL"),
            ("VERDICT: FAIL", "FAIL"),
        ],
        fallback="UNKNOWN",
    )
    assert result == "PASS", (
        f"last line-anchored occurrence should win; got {result!r}"
    )


def test_acb1_p2_prose_does_not_override_standalone():
    """ACB1: standalone PASS comes first; prose-embedded FAIL appears later —
    standalone PASS must still win (prose-embedded FAIL must not match at all)."""
    _ensure_lib_path()
    from bytedigger_engine.lib.verdict_parse import last_line_anchored_marker  # noqa: PLC0415

    raw = (
        "Final answer:\n"
        "VERDICT: PASS\n"
        "Note: an earlier reviewer wrote 'VERDICT: FAIL' mid-sentence.\n"
    )
    result = last_line_anchored_marker(
        raw,
        [
            ("VERDICT: PASS", "PASS"),
            ("VERDICT: PARTIAL", "PARTIAL"),
            ("VERDICT: FAIL", "FAIL"),
        ],
        fallback="UNKNOWN",
    )
    assert result == "PASS", (
        f"prose-embedded FAIL after standalone PASS must not override; got {result!r}"
    )


def test_acb1_p2_no_marker_returns_fallback():
    """ACB1: no marker present → fallback value returned."""
    _ensure_lib_path()
    from bytedigger_engine.lib.verdict_parse import last_line_anchored_marker  # noqa: PLC0415

    result = last_line_anchored_marker(
        "Nothing to see here.\n",
        [("VERDICT: PASS", "PASS"), ("VERDICT: FAIL", "FAIL")],
        fallback="UNKNOWN",
    )
    assert result == "UNKNOWN"


def test_acb1_p2_leading_whitespace_tolerated():
    """ACB1: marker with optional leading whitespace still line-anchored → matches."""
    _ensure_lib_path()
    from bytedigger_engine.lib.verdict_parse import last_line_anchored_marker  # noqa: PLC0415

    raw = "Analysis done.\n  VERDICT: FAIL\n"
    result = last_line_anchored_marker(
        raw,
        [("VERDICT: PASS", "PASS"), ("VERDICT: FAIL", "FAIL")],
        fallback="UNKNOWN",
    )
    assert result == "FAIL", (
        f"leading-whitespace line-start marker should match; got {result!r}"
    )


# ─── ACB2: P2 — payload-style markers + None fallback ────────────────────────


def test_acb2_p2_status_done_payload_extracted():
    """ACB2: STATUS: DONE at line start → mapped value 'DONE' via payload tuple."""
    _ensure_lib_path()
    from bytedigger_engine.lib.verdict_parse import last_line_anchored_marker  # noqa: PLC0415

    _STATUS_MARKERS = (
        "STATUS: DONE_WITH_CONCERNS",
        "STATUS: DONE",
        "STATUS: NEEDS_CONTEXT",
        "STATUS: BLOCKED",
    )
    raw = "Work complete.\nSTATUS: DONE\n"
    result = last_line_anchored_marker(
        raw,
        [(m, m.split(": ", 1)[1]) for m in _STATUS_MARKERS],
        fallback=None,
    )
    assert result == "DONE", (
        f"STATUS: DONE should return payload 'DONE'; got {result!r}"
    )


def test_acb2_p2_absent_marker_returns_none_fallback():
    """ACB2: no STATUS marker → fallback=None returned (not a string)."""
    _ensure_lib_path()
    from bytedigger_engine.lib.verdict_parse import last_line_anchored_marker  # noqa: PLC0415

    _STATUS_MARKERS = (
        "STATUS: DONE_WITH_CONCERNS",
        "STATUS: DONE",
        "STATUS: NEEDS_CONTEXT",
        "STATUS: BLOCKED",
    )
    result = last_line_anchored_marker(
        "No status marker here.",
        [(m, m.split(": ", 1)[1]) for m in _STATUS_MARKERS],
        fallback=None,
    )
    assert result is None, (
        f"absent marker should return None fallback; got {result!r}"
    )


def test_acb2_p2_last_status_marker_wins():
    """ACB2: DONE_WITH_CONCERNS then DONE at line start → DONE (last) wins."""
    _ensure_lib_path()
    from bytedigger_engine.lib.verdict_parse import last_line_anchored_marker  # noqa: PLC0415

    _STATUS_MARKERS = (
        "STATUS: DONE_WITH_CONCERNS",
        "STATUS: DONE",
        "STATUS: NEEDS_CONTEXT",
        "STATUS: BLOCKED",
    )
    raw = "STATUS: DONE_WITH_CONCERNS\nRevision after review.\nSTATUS: DONE\n"
    result = last_line_anchored_marker(
        raw,
        [(m, m.split(": ", 1)[1]) for m in _STATUS_MARKERS],
        fallback=None,
    )
    assert result == "DONE", (
        f"last line-anchored STATUS should win; got {result!r}"
    )


# ─── ACB-P2: phase_6_review tightening — prose-quoted verdict does not win ───
#
# Current behaviour (rfind): the last substring occurrence wins regardless of
# position in a line.  A prose sentence "reviewer flagged `VERDICT: FAIL`"
# after a standalone `VERDICT: PASS` causes _parse_review_verdict to return
# FAIL (BUG).  After GREEN routes through P2, only line-anchored occurrences
# count, so PASS wins.
#
# §1y: Point = _parse_review_verdict body (phase_6_review.py), Host = function
# itself, Test-path = direct call with crafted raw string.


def test_acb_p2_review_verdict_standalone_pass_wins_over_prose_fail():
    """ACB-P2: standalone VERDICT: PASS followed by prose-embedded VERDICT: FAIL
    must return PASS; rfind bug would return FAIL."""
    raw = (
        "Code review complete.\n"
        "VERDICT: PASS\n"
        "Note: originally the reviewer quoted `VERDICT: FAIL` in discussion.\n"
    )
    result = _parse_review_verdict(raw)
    assert result == "PASS", (
        f"prose-embedded VERDICT: FAIL after standalone VERDICT: PASS must not "
        f"override; got {result!r} (rfind bug still present)"
    )


def test_acb_p2_fix_status_standalone_complete_wins_over_prose_blocked():
    """ACB-P2: standalone FIX COMPLETE followed by prose `FIX BLOCKED` must
    return FIX_COMPLETE; rfind bug would return FIX_BLOCKED."""
    raw = (
        "Attempting fix.\n"
        "FIX COMPLETE\n"
        "Observation: a prior attempt ended with `FIX BLOCKED` mid-paragraph.\n"
    )
    result = _parse_fix_status(raw)
    # FIX_COMPLETE = "COMPLETE"
    assert result == "COMPLETE", (
        f"prose-embedded FIX BLOCKED after standalone FIX COMPLETE must not "
        f"override; got {result!r} (rfind bug still present)"
    )


# ─── ACB-P3: phase_7_synthesize tightening — prose-quoted status does not win ─
#
# §1y: Point = _parse_synthesizer_status body (phase_7_synthesize.py),
#       Host = function itself,
#       Test-path = direct call with crafted raw string.


def test_acb_p3_synthesizer_standalone_done_wins_over_prose_blocked():
    """ACB-P3: standalone STATUS: DONE followed by prose-embedded STATUS: BLOCKED
    must return STATUS_DONE; rfind bug would return STATUS_BLOCKED."""
    raw = (
        "Synthesis complete.\n"
        "STATUS: DONE\n"
        "Note: an earlier draft quoted `STATUS: BLOCKED` in a cautionary note.\n"
    )
    result = _parse_synthesizer_status(raw)
    # STATUS_DONE = "DONE"
    assert result == "DONE", (
        f"prose-embedded STATUS: BLOCKED after standalone STATUS: DONE must not "
        f"override; got {result!r} (rfind bug still present)"
    )


def test_acb_p3_synthesizer_no_marker_returns_no_marker_sentinel():
    """ACB-P3 parity: no status marker → STATUS_NO_MARKER ('NO_MARKER')."""
    result = _parse_synthesizer_status("No status here.\n")
    # STATUS_NO_MARKER = "NO_MARKER"
    assert result == "NO_MARKER", (
        f"absent marker should return NO_MARKER; got {result!r}"
    )


# ─── ACB-P4: phase_2_explore + phase_3_clarify tightening ────────────────────
#
# _parse_status_marker uses rfind today; prose-embedded marker appearing AFTER
# a standalone marker (later in string) overrides it (BUG).  After GREEN routes
# through P2, only line-anchored matches count.
#
# Contract: returns payload string (e.g. "DONE") or None on no match.
#
# §1y: Point = _parse_status_marker bodies (phase_2_explore.py L349,
#              phase_3_clarify.py L243),
#       Host = those functions directly,
#       Test-path = direct import + call with crafted raw string.


def test_acb_p4_phase2_standalone_done_wins_over_prose_blocked():
    """ACB-P4 phase_2: standalone STATUS: DONE before prose-embedded
    STATUS: BLOCKED → returns 'DONE'; rfind bug returns 'BLOCKED'."""
    raw = (
        "Exploration finished.\n"
        "STATUS: DONE\n"
        "Note: prior iteration said `STATUS: BLOCKED` in the middle of a sentence.\n"
    )
    result = _p2_parse_status_marker(raw)
    assert result == "DONE", (
        f"prose-embedded BLOCKED after standalone DONE must not override "
        f"(phase_2_explore); got {result!r} (rfind bug still present)"
    )


def test_acb_p4_phase2_no_marker_returns_none():
    """ACB-P4 phase_2 payload+None contract: no marker → None."""
    result = _p2_parse_status_marker("No status marker present.\n")
    assert result is None, (
        f"absent marker must return None (phase_2_explore); got {result!r}"
    )


def test_acb_p4_phase2_standalone_done_with_concerns_payload():
    """ACB-P4 phase_2: standalone STATUS: DONE_WITH_CONCERNS → payload 'DONE_WITH_CONCERNS'."""
    raw = "STATUS: DONE_WITH_CONCERNS\n"
    result = _p2_parse_status_marker(raw)
    assert result == "DONE_WITH_CONCERNS", (
        f"payload should be 'DONE_WITH_CONCERNS'; got {result!r}"
    )


def test_acb_p4_phase3_standalone_done_wins_over_prose_blocked():
    """ACB-P4 phase_3: standalone STATUS: DONE before prose-embedded
    STATUS: BLOCKED → returns 'DONE'; rfind bug returns 'BLOCKED'."""
    raw = (
        "Clarification complete.\n"
        "STATUS: DONE\n"
        "Earlier draft wrote `STATUS: BLOCKED` in prose.\n"
    )
    result = _p3_parse_status_marker(raw)
    assert result == "DONE", (
        f"prose-embedded BLOCKED after standalone DONE must not override "
        f"(phase_3_clarify); got {result!r} (rfind bug still present)"
    )


def test_acb_p4_phase3_no_marker_returns_none():
    """ACB-P4 phase_3 payload+None contract: no marker → None."""
    result = _p3_parse_status_marker("Nothing here.\n")
    assert result is None, (
        f"absent marker must return None (phase_3_clarify); got {result!r}"
    )


def test_acb_p4_phase3_standalone_needs_context_payload():
    """ACB-P4 phase_3: standalone STATUS: NEEDS_CONTEXT → payload 'NEEDS_CONTEXT'."""
    raw = "STATUS: NEEDS_CONTEXT\n"
    result = _p3_parse_status_marker(raw)
    assert result == "NEEDS_CONTEXT", (
        f"payload should be 'NEEDS_CONTEXT'; got {result!r}"
    )
