"""RED tests for CA50885D — phase_6 suspect findings fail-OPEN at fix-feed.

Spec: SHARED/memory/Decisions/2026-06-16_CA50885D_bug2_suspect_fail_open_spec.md

Design summary (CA50885D):
  - _render_fix_doc gains a 3rd param `suspect_findings: list | None = None`.
  - When non-empty, it appends a '## Suspect Findings (LOW CONFIDENCE)' section
    with one instruction line and each suspect finding's block text.
  - _aggregate_review_findings return data gains key "suspect_findings".
  - Verdict/severity computation stays verified-only (spec Q1 frozen NO).
  - Back-compat: 2-arg callers / empty suspect list → no suspect section emitted.

Expected pre-GREEN status:
  FAIL today:
    test_ac1_suspect_section_header_emitted         — 3rd param not accepted → TypeError (caught)
    test_ac2_suspect_instruction_line               — 3rd param not accepted → TypeError (caught)
    test_ac3_suspect_blocks_present                 — 3rd param not accepted → TypeError (caught)
    test_ac6_verified_section_still_present_with_suspect — 3rd param not accepted → TypeError (caught)
    test_ac7_verified_before_suspect                — 3rd param not accepted → TypeError (caught)
    test_ac8_signature_has_suspect_param            — param absent from inspect.signature today
    test_ac9_aggregate_forwards_suspect_findings    — key absent from _aggregate_review_findings source

  PASS today (back-compat / regression guards):
    test_ac4_legacy_2arg_no_suspect_header          — 2-arg call works; header absent (correct)
    test_ac5_empty_suspect_no_header                — guarded: skips 3rd-arg call if param absent
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

HERE = Path(__file__).parent
ENGINE_ROOT = HERE.parent
WORKFLOWS = ENGINE_ROOT / "workflows"

# Match sibling test import pattern exactly
# (test_phase_6_verified_only_gate_65695203.py, test_phase_6_1F39FB1A_soft_tag.py)
if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))
if str(ENGINE_ROOT / "lib") not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT / "lib"))
if str(WORKFLOWS) not in sys.path:
    sys.path.insert(0, str(WORKFLOWS))

# Top-level import — module already exists → collects fine.
# NOT-YET-EXISTING behavior (3rd param, suspect_findings key) accessed inside test
# bodies only, per §1q / D1CF5FDF.
import phase_6_review as _p6  # noqa: E402


# ─── fixture helpers ────────────────────────────────────────────────────────────


def _make_verified_finding(title: str, severity: str = "HIGH") -> dict:
    """Minimal verified finding dict matching the shape _aggregate_review_findings produces."""
    return {
        "title": title,
        "severity": severity,
        "verify_status": "verified-exact",
        "block": (
            f"### SEVERITY: {severity} — {title}\n"
            "> /tmp/fake_file.py:1: def some_func():\n"
            "Confidence: HIGH\n"
            "Description: a verified finding\n"
        ),
    }


def _make_suspect_finding(
    title: str,
    severity: str = "MEDIUM",
    status: str = "suspect-no-match",
) -> dict:
    """Minimal suspect finding dict matching the shape _aggregate_review_findings produces."""
    return {
        "title": title,
        "severity": severity,
        "verify_status": status,
        "block": (
            f"### SEVERITY: {severity} — {title}\n"
            "> /tmp/fake_file.py:1: `def some_func():`\n"
            "Confidence: HIGH\n"
            "Description: a suspect finding\n"
        ),
    }


# ─── AC8: signature guard (fastest/purest signal) ─────────────────────────────


def test_ac8_signature_has_suspect_param():
    """AC8: _render_fix_doc signature accepts a 3rd param named 'suspect_findings'
    with default None.

    FAILS today: current signature is (verified_findings, verdict="") — 2 params only;
    inspect.signature will not find 'suspect_findings'.
    """
    sig = inspect.signature(_p6._render_fix_doc)
    params = sig.parameters

    assert "suspect_findings" in params, (
        f"AC8: _render_fix_doc signature must have param 'suspect_findings'. "
        f"Current params: {list(params.keys())}. "
        f"FAILS today: signature is 2-arg (verified_findings, verdict) — 3rd param not yet added."
    )

    default = params["suspect_findings"].default
    assert default is None, (
        f"AC8: 'suspect_findings' param must default to None, got {default!r}."
    )


# ─── AC1: suspect section header emitted ──────────────────────────────────────


def test_ac1_suspect_section_header_emitted():
    """AC1: _render_fix_doc(verified, "FAIL", suspect_findings=suspect) with non-empty
    suspect list emits '## Suspect Findings (LOW CONFIDENCE)' header in body.

    FAILS today: the function does not accept a 3rd kwarg → TypeError. The try/except
    converts that to a failing assertion so the test fails at assert-time (not collect-time),
    per §1q / D1CF5FDF.
    """
    verified = [_make_verified_finding("VerifiedAC1-Alpha", "HIGH")]
    suspect = [_make_suspect_finding("SuspectAC1-TDA-01", "MEDIUM")]

    try:
        body = _p6._render_fix_doc(verified, "FAIL", suspect_findings=suspect)
    except TypeError as exc:
        assert False, (
            f"AC1: _render_fix_doc raised TypeError on 3rd kwarg 'suspect_findings': {exc}. "
            f"FAILS today: 3rd param not yet in signature."
        )

    assert "## Suspect Findings (LOW CONFIDENCE)" in body, (
        f"AC1: '## Suspect Findings (LOW CONFIDENCE)' header absent from body. "
        f"Body returned:\n{body}"
    )


# ─── AC2: exact instruction line emitted ──────────────────────────────────────


def test_ac2_suspect_instruction_line():
    """AC2: the suspect section contains the exact instruction line:
    'These findings did not pass quote-verification. Read the cited file yourself
    and fix ONLY if the issue is real.'

    FAILS today: TypeError on 3rd kwarg (same as AC1 path).
    """
    _EXPECTED_INSTRUCTION = (
        "These findings did not pass quote-verification. "
        "Read the cited file yourself and fix ONLY if the issue is real."
    )

    verified = [_make_verified_finding("VerifiedAC2-Alpha", "HIGH")]
    suspect = [_make_suspect_finding("SuspectAC2-TDA-01", "MEDIUM")]

    try:
        body = _p6._render_fix_doc(verified, "FAIL", suspect_findings=suspect)
    except TypeError as exc:
        assert False, (
            f"AC2: _render_fix_doc raised TypeError on 3rd kwarg 'suspect_findings': {exc}. "
            f"FAILS today: 3rd param not yet in signature."
        )

    assert _EXPECTED_INSTRUCTION in body, (
        f"AC2: exact instruction line not found in body.\n"
        f"Expected substring: {_EXPECTED_INSTRUCTION!r}\n"
        f"Body returned:\n{body}"
    )


# ─── AC3: each suspect finding's block appears in body ────────────────────────


def test_ac3_suspect_blocks_present():
    """AC3: each suspect finding's 'block' text appears in the output body.

    FAILS today: TypeError on 3rd kwarg.
    """
    verified = [_make_verified_finding("VerifiedAC3-Alpha", "HIGH")]
    suspect = [
        _make_suspect_finding("SuspectAC3-TDA-01", "MEDIUM", "suspect-no-match"),
        _make_suspect_finding("SuspectAC3-TDA-02", "LOW", "suspect-no-quote"),
    ]

    try:
        body = _p6._render_fix_doc(verified, "FAIL", suspect_findings=suspect)
    except TypeError as exc:
        assert False, (
            f"AC3: _render_fix_doc raised TypeError on 3rd kwarg 'suspect_findings': {exc}. "
            f"FAILS today: 3rd param not yet in signature."
        )

    for f in suspect:
        assert f["block"] in body, (
            f"AC3: suspect finding block not found in body.\n"
            f"Missing block for {f['title']!r}:\n{f['block']!r}\n"
            f"Body returned:\n{body}"
        )


# ─── AC4 (PASS today — back-compat regression guard) ──────────────────────────


def test_ac4_legacy_2arg_no_suspect_header():
    """AC4 (PASS today): _render_fix_doc(verified, "FAIL") (2-arg, legacy call) emits
    NO '## Suspect Findings' header. Back-compat byte-identical verified-only path.

    This test guards that the GREEN change does NOT break 2-arg callers.
    Must PASS today (2-arg works, no suspect section) AND after GREEN (default None → no section).
    """
    verified = [_make_verified_finding("VerifiedAC4-Alpha", "HIGH")]

    body = _p6._render_fix_doc(verified, "FAIL")

    assert isinstance(body, str), (
        f"AC4: _render_fix_doc must return str, got {type(body)}"
    )
    assert "## Suspect Findings" not in body, (
        f"AC4 (regression guard): '## Suspect Findings' MUST NOT appear in 2-arg call body. "
        f"Back-compat: legacy callers must get verified-only output unchanged.\n"
        f"Body returned:\n{body}"
    )


# ─── AC5 (PASS today and after GREEN — empty suspect list guard) ───────────────


def test_ac5_empty_suspect_no_header():
    """AC5 (PASS today): _render_fix_doc with empty suspect_findings list emits NO
    '## Suspect Findings' header.

    Guard: if the 3rd param doesn't exist yet (today), falls back to 2-arg call so
    this test passes both pre- and post-GREEN.
    """
    verified = [_make_verified_finding("VerifiedAC5-Alpha", "HIGH")]

    sig = inspect.signature(_p6._render_fix_doc)
    if "suspect_findings" in sig.parameters:
        # Post-GREEN path: call with explicit empty list
        body = _p6._render_fix_doc(verified, "FAIL", suspect_findings=[])
    else:
        # Pre-GREEN path: 3rd param not yet present; fall back to 2-arg (still tests the contract)
        body = _p6._render_fix_doc(verified, "FAIL")

    assert isinstance(body, str), (
        f"AC5: _render_fix_doc must return str, got {type(body)}"
    )
    assert "## Suspect Findings" not in body, (
        f"AC5: '## Suspect Findings' MUST NOT appear when suspect_findings is empty or absent.\n"
        f"Body returned:\n{body}"
    )


# ─── AC6: verified section still present with suspect non-empty ────────────────


def test_ac6_verified_section_still_present_with_suspect():
    """AC6: when suspect_findings is non-empty, body STILL contains '## Aggregated Findings'
    header and every verified finding's block (regression alongside new behavior).

    FAILS today: TypeError on 3rd kwarg.
    """
    verified = [
        _make_verified_finding("VerifiedAC6-Alpha", "HIGH"),
        _make_verified_finding("VerifiedAC6-Beta", "MEDIUM"),
    ]
    suspect = [_make_suspect_finding("SuspectAC6-TDA-01", "LOW")]

    try:
        body = _p6._render_fix_doc(verified, "FAIL", suspect_findings=suspect)
    except TypeError as exc:
        assert False, (
            f"AC6: _render_fix_doc raised TypeError on 3rd kwarg 'suspect_findings': {exc}. "
            f"FAILS today: 3rd param not yet in signature."
        )

    assert "## Aggregated Findings" in body, (
        f"AC6: '## Aggregated Findings' header must still appear when suspect is non-empty.\n"
        f"Body returned:\n{body}"
    )
    for f in verified:
        assert f["block"] in body, (
            f"AC6: verified finding block missing from body when suspect is non-empty.\n"
            f"Missing block for {f['title']!r}:\n{f['block']!r}\n"
            f"Body returned:\n{body}"
        )


# ─── AC7: verified section appears BEFORE suspect section ─────────────────────


def test_ac7_verified_before_suspect():
    """AC7: body.index('## Aggregated Findings') < body.index('## Suspect Findings (LOW CONFIDENCE)').
    Verified-first ordering is required.

    FAILS today: TypeError on 3rd kwarg.
    """
    verified = [_make_verified_finding("VerifiedAC7-Alpha", "HIGH")]
    suspect = [_make_suspect_finding("SuspectAC7-TDA-01", "MEDIUM")]

    try:
        body = _p6._render_fix_doc(verified, "FAIL", suspect_findings=suspect)
    except TypeError as exc:
        assert False, (
            f"AC7: _render_fix_doc raised TypeError on 3rd kwarg 'suspect_findings': {exc}. "
            f"FAILS today: 3rd param not yet in signature."
        )

    _VERIFIED_HEADER = "## Aggregated Findings"
    _SUSPECT_HEADER = "## Suspect Findings (LOW CONFIDENCE)"

    assert _VERIFIED_HEADER in body, (
        f"AC7: '## Aggregated Findings' must be present in body.\nBody:\n{body}"
    )
    assert _SUSPECT_HEADER in body, (
        f"AC7: '## Suspect Findings (LOW CONFIDENCE)' must be present in body.\nBody:\n{body}"
    )
    assert body.index(_VERIFIED_HEADER) < body.index(_SUSPECT_HEADER), (
        f"AC7: '## Aggregated Findings' (verified) must appear BEFORE "
        f"'## Suspect Findings (LOW CONFIDENCE)' (suspect).\n"
        f"verified at index {body.index(_VERIFIED_HEADER)}, "
        f"suspect at index {body.index(_SUSPECT_HEADER)}.\n"
        f"Body:\n{body}"
    )


# ─── AC9: _aggregate_review_findings source contains suspect_findings key ─────


def test_ac9_aggregate_forwards_suspect_findings():
    """AC9: _aggregate_review_findings return data dict must contain the key
    'suspect_findings' (forwarded for fail-OPEN fix-feed).

    Asserted via inspect.getsource — looks for the literal substring
    '"suspect_findings": suspect_findings' in the function body.

    FAILS today: the return dict in _aggregate_review_findings has 'verified_findings'
    (added by 65695203) but NOT 'suspect_findings' — key not yet forwarded (CA50885D adds it).
    """
    source = inspect.getsource(_p6._aggregate_review_findings)

    _EXPECTED_FRAGMENT = '"suspect_findings": suspect_findings'

    assert _EXPECTED_FRAGMENT in source, (
        f"AC9: literal substring {_EXPECTED_FRAGMENT!r} not found in "
        f"_aggregate_review_findings source. "
        f"FAILS today: suspect_findings local exists (L1699) but is not forwarded "
        f"in the return data dict. CA50885D adds this key."
    )
