"""RED tests for EEFD480F — lib/verdict_parse.py Wave A.

New module `lib/verdict_parse.py` does NOT exist yet (GREEN creates it).
Per §4 (D1CF5FDF): `verdict_parse` imported INSIDE each test body so the
file COLLECTS cleanly and test functions FAIL on assertions, never on
collection-time ImportError.

The conftest-singleton already adds engine_root + workflows to sys.path;
`lib/` is added inside each test body that needs verdict_parse (no
module-level sys.path.insert per §1q / 81F97F3D).

Agreement: EEFD480F · Wave A ACs: AC1, AC2, AC3, AC4, AC-P2 (bug fix).
"""
from __future__ import annotations

from pathlib import Path

HERE = Path(__file__).parent
ENGINE_ROOT = HERE.parent
_LIB_PATH = str(ENGINE_ROOT / "bytedigger_engine" / "lib")


# ─── helpers ─────────────────────────────────────────────────────────────────

def _ensure_lib_path() -> None:
    """Insert lib/ into sys.path inside a test body (not at module level)."""
    import sys
    if _LIB_PATH not in sys.path:
        sys.path.insert(0, _LIB_PATH)


# ─── AC1: P1 — standalone vs inline discrimination ───────────────────────────


def test_ac1_p1_standalone_line_returns_token():
    """AC1: P1 standalone `VERDICT: SPEC_CHANGE` → SPEC_CHANGE."""
    _ensure_lib_path()
    from bytedigger_engine.lib.verdict_parse import last_standalone_line_verdict  # noqa: PLC0415

    raw = "Final classification:\nVERDICT: SPEC_CHANGE\n"
    result = last_standalone_line_verdict(
        raw,
        ("ASSERTION_GAMING", "LEGITIMATE_REFACTOR", "SPEC_CHANGE", "NO_CHANGES"),
        fallback="UNKNOWN",
    )
    assert result == "SPEC_CHANGE"


def test_ac1_p1_inline_only_returns_fallback():
    """AC1: inline-only `...VERDICT: ASSERTION_GAMING...` in prose → UNKNOWN."""
    _ensure_lib_path()
    from bytedigger_engine.lib.verdict_parse import last_standalone_line_verdict  # noqa: PLC0415

    raw = "The reviewer noted VERDICT: ASSERTION_GAMING inline only.\n"
    result = last_standalone_line_verdict(
        raw,
        ("ASSERTION_GAMING", "LEGITIMATE_REFACTOR", "SPEC_CHANGE", "NO_CHANGES"),
        fallback="UNKNOWN",
    )
    assert result == "UNKNOWN", (
        f"inline-only verdict must not be recognised; got {result!r}"
    )


# ─── AC2: P1 — last-wins, bold+lowercase tolerated ───────────────────────────


def test_ac2_p1_first_inline_second_standalone_returns_second():
    """AC2: first line has trailing `.` (inline), only second standalone → ASSERTION_GAMING."""
    _ensure_lib_path()
    from bytedigger_engine.lib.verdict_parse import last_standalone_line_verdict  # noqa: PLC0415

    # "VERDICT: SPEC_CHANGE." — trailing period makes it NOT a standalone line
    # because the regex requires end-of-line after token (no trailing chars).
    raw = "First pass: VERDICT: SPEC_CHANGE.\nOn second look:\nVERDICT: ASSERTION_GAMING\n"
    result = last_standalone_line_verdict(
        raw,
        ("ASSERTION_GAMING", "LEGITIMATE_REFACTOR", "SPEC_CHANGE", "NO_CHANGES"),
        fallback="UNKNOWN",
    )
    assert result == "ASSERTION_GAMING", (
        f"only standalone line should win; got {result!r}"
    )


def test_ac2_p1_two_standalone_last_wins():
    """AC2: two genuine standalone VERDICT lines → LAST wins."""
    _ensure_lib_path()
    from bytedigger_engine.lib.verdict_parse import last_standalone_line_verdict  # noqa: PLC0415

    raw = "VERDICT: SPEC_CHANGE\nsome prose\nVERDICT: LEGITIMATE_REFACTOR\n"
    result = last_standalone_line_verdict(
        raw,
        ("ASSERTION_GAMING", "LEGITIMATE_REFACTOR", "SPEC_CHANGE", "NO_CHANGES"),
        fallback="UNKNOWN",
    )
    assert result == "LEGITIMATE_REFACTOR", (
        f"last standalone line should win; got {result!r}"
    )


def test_ac2_p1_bold_lowercase_tolerated():
    """AC2: `**Verdict: spec_change**` (bold + lowercase) → SPEC_CHANGE."""
    _ensure_lib_path()
    from bytedigger_engine.lib.verdict_parse import last_standalone_line_verdict  # noqa: PLC0415

    raw = "Reviewed.\n**Verdict: spec_change**\n"
    result = last_standalone_line_verdict(
        raw,
        ("ASSERTION_GAMING", "LEGITIMATE_REFACTOR", "SPEC_CHANGE", "NO_CHANGES"),
        fallback="UNKNOWN",
    )
    assert result == "SPEC_CHANGE", (
        f"bold+lowercase variant should be normalised to SPEC_CHANGE; got {result!r}"
    )


# ─── AC3: P3 — heading-anchored verdict ──────────────────────────────────────


def test_ac3_p3_token_under_heading():
    """AC3: token directly under `## Verdict` → that token."""
    _ensure_lib_path()
    from bytedigger_engine.lib.verdict_parse import verdict_under_heading  # noqa: PLC0415

    raw = "## Verdict\nSHIP"
    result = verdict_under_heading(
        raw,
        ("SHIP", "PASS", "APPROVED", "REVISE"),
        aliases={"PASS": "SHIP", "APPROVED": "SHIP"},
        fallback="UNKNOWN",
    )
    assert result == "SHIP"


def test_ac3_p3_prose_token_before_heading_ignored():
    """AC3: prose REVISE before `## Verdict` does not pollute result."""
    _ensure_lib_path()
    from bytedigger_engine.lib.verdict_parse import verdict_under_heading  # noqa: PLC0415

    raw = "Discussion: avoid REVISE pattern.\n## Verdict\nSHIP\n"
    result = verdict_under_heading(
        raw,
        ("SHIP", "PASS", "APPROVED", "REVISE"),
        aliases={"PASS": "SHIP", "APPROVED": "SHIP"},
        fallback="UNKNOWN",
    )
    assert result == "SHIP", f"prose REVISE should be ignored; got {result!r}"


def test_ac3_p3_codeblock_token_before_heading_ignored():
    """AC3: token in code block before `## Verdict` heading is ignored."""
    _ensure_lib_path()
    from bytedigger_engine.lib.verdict_parse import verdict_under_heading  # noqa: PLC0415

    raw = "```\nREVISE this\n```\n## Verdict\nSHIP\n"
    result = verdict_under_heading(
        raw,
        ("SHIP", "PASS", "APPROVED", "REVISE"),
        aliases={"PASS": "SHIP", "APPROVED": "SHIP"},
        fallback="UNKNOWN",
    )
    assert result == "SHIP", f"codeblock REVISE should be ignored; got {result!r}"


def test_ac3_p3_pass_alias_maps_to_ship():
    """AC3: PASS under ## Verdict → SHIP via alias."""
    _ensure_lib_path()
    from bytedigger_engine.lib.verdict_parse import verdict_under_heading  # noqa: PLC0415

    raw = "## Verdict\nPASS"
    result = verdict_under_heading(
        raw,
        ("SHIP", "PASS", "APPROVED", "REVISE"),
        aliases={"PASS": "SHIP", "APPROVED": "SHIP"},
        fallback="UNKNOWN",
    )
    assert result == "SHIP", f"PASS alias should resolve to SHIP; got {result!r}"


def test_ac3_p3_mixed_case_header_and_token():
    """AC3: `## verdict\nship` (mixed-case header + token) → SHIP."""
    _ensure_lib_path()
    from bytedigger_engine.lib.verdict_parse import verdict_under_heading  # noqa: PLC0415

    raw = "## verdict\nship"
    result = verdict_under_heading(
        raw,
        ("SHIP", "PASS", "APPROVED", "REVISE"),
        aliases={"PASS": "SHIP", "APPROVED": "SHIP"},
        fallback="UNKNOWN",
    )
    assert result == "SHIP", f"mixed-case header/token should resolve; got {result!r}"


def test_ac3_p3_empty_input_returns_fallback():
    """AC3: empty string → fallback UNKNOWN."""
    _ensure_lib_path()
    from bytedigger_engine.lib.verdict_parse import verdict_under_heading  # noqa: PLC0415

    result = verdict_under_heading(
        "",
        ("SHIP", "PASS", "APPROVED", "REVISE"),
        aliases={"PASS": "SHIP", "APPROVED": "SHIP"},
        fallback="UNKNOWN",
    )
    assert result == "UNKNOWN"


def test_ac3_p3_no_heading_returns_fallback():
    """AC3: no `## Verdict` heading → fallback UNKNOWN."""
    _ensure_lib_path()
    from bytedigger_engine.lib.verdict_parse import verdict_under_heading  # noqa: PLC0415

    result = verdict_under_heading(
        "no header here",
        ("SHIP", "PASS", "APPROVED", "REVISE"),
        aliases={"PASS": "SHIP", "APPROVED": "SHIP"},
        fallback="UNKNOWN",
    )
    assert result == "UNKNOWN"


# ─── AC4: P4 — find_last_standalone_marker ───────────────────────────────────


def test_ac4_p4_returns_last_match_with_usable_end():
    """AC4: REPRODUCED: then REFUTED: then REPRODUCED: → last is REPRODUCED, .end() usable."""
    _ensure_lib_path()
    from bytedigger_engine.lib.verdict_parse import find_last_standalone_marker  # noqa: PLC0415

    raw = "REFUTED:\nreason: x\n\nREPRODUCED:\nfile: a\n"
    match = find_last_standalone_marker(raw, ("REPRODUCED", "REFUTED", "UNVERIFIED"))
    assert match is not None, "should find a marker"
    token = match.group(0).rstrip(":").strip()
    assert token == "REPRODUCED", f"last marker should be REPRODUCED; got {token!r}"
    # .end() must be usable (not raise, points into string)
    tail = raw[match.end():]
    assert isinstance(tail, str)


def test_ac4_p4_no_marker_returns_none():
    """AC4: no marker in text → None."""
    _ensure_lib_path()
    from bytedigger_engine.lib.verdict_parse import find_last_standalone_marker  # noqa: PLC0415

    result = find_last_standalone_marker("no marker\n", ("REPRODUCED", "REFUTED", "UNVERIFIED"))
    assert result is None


# ─── AC-P2: phase_6_fix_integrity bug — prose-quoted inline verdict ───────────
#
# On current main, _parse_verdict uses rfind which picks the LAST substring
# position in a case-folded copy. A prose-quoted "VERDICT: ASSERTION_GAMING"
# appearing AFTER a standalone "VERDICT: SPEC_CHANGE" flips the result.
# After GREEN routes through P1 (last_standalone_line_verdict), only standalone
# lines count, so SPEC_CHANGE wins.
#
# §1i / singleton-resource: no singleton contention here — pure string parse.
# §1y: Point = _parse_verdict (phase_6_fix_integrity.py L255),
#       Host = classify_fix_diff_verdict step body,
#       Test-path = direct call to _parse_verdict with probe string.


def test_ac_p2_prose_quoted_assertion_gaming_does_not_flip_standalone_spec_change():
    """AC-P2 bug fix: prose-quoted VERDICT: ASSERTION_GAMING after standalone
    VERDICT: SPEC_CHANGE must NOT override the standalone verdict.

    On current main _parse_verdict uses rfind → returns ASSERTION_GAMING (BUG).
    After GREEN routes through P1 → returns SPEC_CHANGE (FIXED).
    """
    import sys  # noqa: PLC0415
    # workflows/ is already on sys.path via conftest singleton
    from bytedigger_engine.workflows.phase_6_fix_integrity import _parse_verdict  # noqa: PLC0415

    # The standalone line comes FIRST; then prose text quotes
    # VERDICT: ASSERTION_GAMING mid-line (NOT a standalone verdict line).
    raw = (
        "Final classification:\n"
        "VERDICT: SPEC_CHANGE\n"
        "Note: an earlier pass quoted `VERDICT: ASSERTION_GAMING` in prose.\n"
    )
    result = _parse_verdict(raw)
    assert result == "SPEC_CHANGE", (
        f"prose-quoted ASSERTION_GAMING must not override standalone SPEC_CHANGE; "
        f"got {result!r} (rfind BUG still present)"
    )


# ─── 7C80A9CE: CRLF normalization ────────────────────────────────────────────
#
# TRUE RED ACs (fail on current main, pass after GREEN adds _normalize):
#   AC1 — P1 plain CRLF line
#   AC2 — P1 bold-emphasis + CRLF
#   AC3 — P1 mixed endings (CRLF body + LF verdict, and vice-versa)
#
# Invariant-pin ACs (pass pre-fix AND post-fix — lock the contract):
#   AC4 — P2 CRLF-terminated marker
#   AC5 — P3 CRLF after heading
#   AC6 — P4 CRLF marker + offset-contract guard (raw[m.end():] correctness)
#   AC8 — LF-only regression guard (no regression for existing behaviour)
#
# §1i (singleton-resource): no singleton contention — pure string parsing.
# §1y reachability for AC1–AC3:
#   Point = last_standalone_line_verdict line 42 (rx.finditer with [ \t]*$)
#   Host  = last_standalone_line_verdict fn body
#   Test-path = direct call with CRLF fixture; fixture makes \r\n reachable.


def test_7c80a9ce_ac1_p1_plain_crlf_returns_token():
    """AC1 (TRUE RED): P1 with plain CRLF-terminated verdict line returns token.

    Current: [ \\t]*$ does not match \\r before \\n → returns fallback UNKNOWN.
    After GREEN (_normalize): \\r\\n → \\n → match succeeds → SPEC_CHANGE.
    """
    _ensure_lib_path()
    from bytedigger_engine.lib.verdict_parse import last_standalone_line_verdict  # noqa: PLC0415

    raw = "VERDICT: SPEC_CHANGE\r\n"
    result = last_standalone_line_verdict(
        raw,
        ("SPEC_CHANGE", "APPROVED"),
        fallback="UNKNOWN",
    )
    assert result == "SPEC_CHANGE", (
        f"CRLF-terminated plain verdict should return SPEC_CHANGE; got {result!r}"
    )


def test_7c80a9ce_ac2_p1_bold_crlf_returns_token():
    """AC2 (TRUE RED): P1 with bold-emphasis + CRLF verdict line returns token.

    **VERDICT: SPEC_CHANGE**\\r\\n — existing test_ac2_p1_bold_lowercase_tolerated
    passes with LF; this covers the same emphasis pattern with CRLF ending.
    """
    _ensure_lib_path()
    from bytedigger_engine.lib.verdict_parse import last_standalone_line_verdict  # noqa: PLC0415

    raw = "**VERDICT: SPEC_CHANGE**\r\n"
    result = last_standalone_line_verdict(
        raw,
        ("SPEC_CHANGE", "APPROVED"),
        fallback="UNKNOWN",
    )
    assert result == "SPEC_CHANGE", (
        f"bold+CRLF verdict should return SPEC_CHANGE; got {result!r}"
    )


def test_7c80a9ce_ac3_p1_mixed_endings_last_crlf_wins():
    """AC3 (TRUE RED): P1 with mixed line endings — CRLF body preamble + LF verdict.

    Two sub-cases:
    a) CRLF preamble line then LF-terminated APPROVED → APPROVED
    b) LF APPROVED first then CRLF SPEC_CHANGE last → last-wins = SPEC_CHANGE
    """
    _ensure_lib_path()
    from bytedigger_engine.lib.verdict_parse import last_standalone_line_verdict  # noqa: PLC0415

    # sub-case a: CRLF preamble, LF verdict
    raw_a = "some preamble line\r\nVERDICT: APPROVED\n"
    result_a = last_standalone_line_verdict(
        raw_a,
        ("SPEC_CHANGE", "APPROVED"),
        fallback="UNKNOWN",
    )
    assert result_a == "APPROVED", (
        f"sub-case a: CRLF preamble + LF verdict should yield APPROVED; got {result_a!r}"
    )

    # sub-case b: LF verdict first, CRLF verdict last → last wins
    raw_b = "VERDICT: APPROVED\nVERDICT: SPEC_CHANGE\r\n"
    result_b = last_standalone_line_verdict(
        raw_b,
        ("SPEC_CHANGE", "APPROVED"),
        fallback="UNKNOWN",
    )
    assert result_b == "SPEC_CHANGE", (
        f"sub-case b: last CRLF-terminated verdict should win; got {result_b!r}"
    )


def test_7c80a9ce_ac4_p2_crlf_marker_invariant():
    """AC4 (invariant pin): P2 with CRLF-terminated marker line returns its value.

    P2 already uses ^\\s* (\\s includes \\r) so this passes pre-fix and
    post-fix — locks the contract against regression.
    """
    _ensure_lib_path()
    from bytedigger_engine.lib.verdict_parse import last_line_anchored_marker  # noqa: PLC0415

    raw = "STATUS: DONE\r\n"
    result = last_line_anchored_marker(raw, [("STATUS: DONE", "done")], None)
    assert result == "done", (
        f"P2 CRLF-terminated marker should return 'done'; got {result!r}"
    )


def test_7c80a9ce_ac5_p3_crlf_heading_invariant():
    """AC5 (invariant pin): P3 with CRLF after heading and before token returns token.

    P3 already uses \\s* (which includes \\r) so this passes pre-fix — locks
    the heading-anchored contract against CRLF regression.
    """
    _ensure_lib_path()
    from bytedigger_engine.lib.verdict_parse import verdict_under_heading  # noqa: PLC0415

    raw = "## Verdict\r\n\r\nAPPROVED\r\n"
    result = verdict_under_heading(
        raw,
        ("APPROVED", "SPEC_CHANGE"),
        fallback="UNKNOWN",
    )
    assert result == "APPROVED", (
        f"P3 CRLF heading+body should return APPROVED; got {result!r}"
    )


def test_7c80a9ce_ac6_p4_crlf_marker_offset_contract():
    """AC6 (invariant pin + offset-contract guard): P4 with CRLF-terminated marker.

    Guards TWO things:
    1. A CRLF-terminated marker is found (Match is not None).
    2. raw[m.end():] on the ORIGINAL raw correctly slices the body after
       the marker line (body contains the expected next text).

    P4 is intentionally NOT normalized (§2.1 offset-contract exemption) — its
    regex uses \\s*$ which includes \\r, so it matches CRLF. The body slice
    must remain coherent on the unnormalized raw.
    """
    _ensure_lib_path()
    from bytedigger_engine.lib.verdict_parse import find_last_standalone_marker  # noqa: PLC0415

    raw = "REPRODUCED:\r\nrepro: did the thing\r\n"
    m = find_last_standalone_marker(raw, ("REPRODUCED", "REFUTED"))
    assert m is not None, "P4 must find REPRODUCED: in CRLF-terminated raw"
    body = raw[m.end():]
    assert "repro:" in body, (
        f"raw[m.end():] should contain the body text 'repro:'; got {body!r}"
    )


def test_7c80a9ce_ac8_p1_lf_only_regression():
    """AC8 (regression pin): LF-only inputs still return expected values for P1.

    Ensures _normalize (when added) does not break plain-LF inputs.
    Passes before AND after the fix.
    """
    _ensure_lib_path()
    from bytedigger_engine.lib.verdict_parse import last_standalone_line_verdict  # noqa: PLC0415

    raw = "VERDICT: APPROVED\n"
    result = last_standalone_line_verdict(
        raw,
        ("SPEC_CHANGE", "APPROVED"),
        fallback="UNKNOWN",
    )
    assert result == "APPROVED", (
        f"LF-only input should still return APPROVED; got {result!r}"
    )
