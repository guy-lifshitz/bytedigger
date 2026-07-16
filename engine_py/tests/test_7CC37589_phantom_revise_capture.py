"""RED tests for 7CC37589 — phantom-REVISE capture fix.

Spec: SHARED/memory/Decisions/2026-06-17_7CC37589_phantom_revise_capture_spec.md
Agreement: 7CC37589  Date: 2026-06-17

All 8 tests MUST FAIL until GREEN ships the _resolve_unresolved_count helper in
phase_45_spec.py and the findings_source kwarg in reject_log.record_plan_review_reject.

DEFERRED-IMPORT RATIONALE (D1CF5FDF / §1q-ext):
_resolve_unresolved_count does NOT exist yet in phase_45_spec.py, and
record_plan_review_reject does not yet accept findings_source=. A top-level
import of a missing symbol would make this file non-collectable, causing a
~30-min engine hang. All imports of these UUT symbols are placed INSIDE
each test function body so collection succeeds and tests fail at assert/call time.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

# Engine root on sys.path so phase modules are importable.
_ENGINE_ROOT = Path(__file__).resolve().parent.parent
if str(_ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(_ENGINE_ROOT))
if str(_ENGINE_ROOT / "workflows") not in sys.path:
    sys.path.insert(0, str(_ENGINE_ROOT / "workflows"))


# ---------------------------------------------------------------------------
# Fixtures — exact format cross-verified against parsers
# ---------------------------------------------------------------------------

# AC1 fixture: structured block with 3 items (matches _STRUCTURED_SECTION_RE)
_RAW_STRUCTURED_3 = """\
## REVISE — spec has open issues

## Findings (structured)
```json
[
  {"id": "F1", "type": "missing", "evidence": "line 10", "required_action": "add check"},
  {"id": "F2", "type": "incorrect", "evidence": "line 20", "required_action": "fix it"},
  {"id": "F3", "type": "missing", "evidence": "line 30", "required_action": "add guard"}
]
```

## Verdict
REVISE
"""

# AC2 / AC3 / AC4 fixture: NO structured block, numbered ## Findings with 4 items
# (matches _FREETEXT_LIST_ITEM_RE = r"^\d+\." MULTILINE in phase_45_spec.py)
_RAW_FREETEXT_4 = """\
## REVISE — spec has open issues

## Findings
1. The helper _resolve_unresolved_count is missing from the module.
2. record_plan_review_reject does not accept findings_source kwarg.
3. The wiring at the REVISE block still passes truncated text.
4. No fallback chain exists for free-text numbered findings.

## Verdict
REVISE
"""

# AC4 fixture: realistic free-form REVISE with real findings text, no JSON block,
# 3 numbered items. With n_total=3,n_resolved=0 per_finding path should give >0.
_RAW_REALISTIC_REVISE = """\
## Review

The spec is missing several required elements.

## Findings
1. §1aa helper-extraction mandate: _resolve_unresolved_count is absent from the design.
2. §1y reachability: no test path reaches the REVISE block counter logic.
3. §1l forcing-function: the detail dict shape is under-specified.

## Verdict
REVISE
"""

# AC7 fixture: >4096 bytes of filler THEN a "§1w" token near the very end
_RAW_LONG_WITH_S1W = "x" * 4100 + "\n\n§1w requirement not satisfied\n"


# ---------------------------------------------------------------------------
# AC1 — _resolve_unresolved_count with structured block → (3, "structured")
# ---------------------------------------------------------------------------

def test_ac1_resolve_unresolved_count_structured() -> None:
    """AC1: _resolve_unresolved_count(raw_with_3_item_structured_block, None, None)
    returns (3, 'structured').
    """
    from phase_45_spec import _resolve_unresolved_count  # deferred: symbol absent pre-GREEN

    count, source = _resolve_unresolved_count(_RAW_STRUCTURED_3, None, None)
    assert count == 3, f"expected count=3 (structured), got {count}"
    assert source == "structured", f"expected source='structured', got {source!r}"


# ---------------------------------------------------------------------------
# AC2 — per_finding fallback when structured absent but n_total/n_resolved given
# ---------------------------------------------------------------------------

def test_ac2_resolve_unresolved_count_per_finding() -> None:
    """AC2: _resolve_unresolved_count(raw_no_structured, n_total=5, n_resolved=2)
    returns (3, 'per_finding').
    """
    from phase_45_spec import _resolve_unresolved_count  # deferred

    count, source = _resolve_unresolved_count(_RAW_FREETEXT_4, 5, 2)
    assert count == 3, f"expected count=3 (per_finding: 5-2), got {count}"
    assert source == "per_finding", f"expected source='per_finding', got {source!r}"


# ---------------------------------------------------------------------------
# AC3 — freetext fallback when structured absent and no per-finding tally
# ---------------------------------------------------------------------------

def test_ac3_resolve_unresolved_count_freetext() -> None:
    """AC3: _resolve_unresolved_count(raw_no_structured_4_freetext_items, None, None)
    returns (4, 'freetext').
    """
    from phase_45_spec import _resolve_unresolved_count  # deferred

    count, source = _resolve_unresolved_count(_RAW_FREETEXT_4, None, None)
    assert count == 4, f"expected count=4 (freetext numbered items), got {count}"
    assert source == "freetext", f"expected source='freetext', got {source!r}"


# ---------------------------------------------------------------------------
# AC4 — phantom-killer: realistic free-form REVISE yields count > 0, not structured
# ---------------------------------------------------------------------------

def test_ac4_phantom_killer_freetext_revise_nonzero() -> None:
    """AC4: free-form REVISE with 3 real findings (no JSON block), n_total=3,
    n_resolved=0 → count > 0 AND source != 'structured'.

    This is the regression-killer for the 99.1% phantom. Pre-GREEN the old path
    returned n_unresolved=0 because extract_structured_findings returns None for
    free-text reviews. Post-GREEN _resolve_unresolved_count falls back to
    per_finding (n_total=3-n_resolved=0 → 3).
    """
    from phase_45_spec import _resolve_unresolved_count  # deferred

    count, source = _resolve_unresolved_count(_RAW_REALISTIC_REVISE, 3, 0)
    assert count > 0, (
        f"phantom-killer FAILED: count={count!r} (expected >0 for realistic free-form REVISE)"
    )
    assert source != "structured", (
        f"source={source!r} — should not be 'structured' for a plain free-text review"
    )


# ---------------------------------------------------------------------------
# AC5 — record_plan_review_reject with findings_source= writes correct detail
# ---------------------------------------------------------------------------

def test_ac5_record_plan_review_reject_with_findings_source(tmp_path: Path) -> None:
    """AC5: record_plan_review_reject(raw, 2, 3, findings_source='freetext') writes
    one line with detail == {'cycle':2,'n_unresolved':3,'findings_source':'freetext'}.
    """
    from reject_log import record_plan_review_reject  # deferred: kwarg absent pre-GREEN
    import telemetry_ctx

    p = tmp_path / "reject-reasons.jsonl"
    telemetry_ctx.set_current_run(event_log=None, run_id="ac5-run", step_name="test")
    try:
        record_plan_review_reject(
            "spec must satisfy §1w requirement",
            2,
            3,
            findings_source="freetext",
            path=p,
        )
    finally:
        telemetry_ctx.clear_current_run()

    assert p.exists(), "reject-reasons.jsonl was not created"
    lines = [ln for ln in p.read_text().splitlines() if ln.strip()]
    assert len(lines) == 1, f"expected 1 line, got {len(lines)}"

    record = json.loads(lines[0])
    expected_detail = {"cycle": 2, "n_unresolved": 3, "findings_source": "freetext"}
    assert record["detail"] == expected_detail, (
        f"detail mismatch: expected {expected_detail!r}, got {record['detail']!r}"
    )


# ---------------------------------------------------------------------------
# AC6 — record_plan_review_reject WITHOUT findings_source → default "unknown"
# ---------------------------------------------------------------------------

def test_ac6_record_plan_review_reject_default_findings_source(tmp_path: Path) -> None:
    """AC6: record_plan_review_reject called without findings_source →
    detail['findings_source'] == 'unknown' (back-compat default).
    """
    from reject_log import record_plan_review_reject  # deferred
    import telemetry_ctx

    p = tmp_path / "reject-reasons.jsonl"
    telemetry_ctx.set_current_run(event_log=None, run_id="ac6-run", step_name="test")
    try:
        record_plan_review_reject(
            "spec must satisfy §1w requirement",
            1,
            2,
            path=p,
        )
    finally:
        telemetry_ctx.clear_current_run()

    assert p.exists(), "reject-reasons.jsonl was not created"
    lines = [ln for ln in p.read_text().splitlines() if ln.strip()]
    assert len(lines) == 1, f"expected 1 line, got {len(lines)}"

    record = json.loads(lines[0])
    assert "findings_source" in record["detail"], (
        f"'findings_source' key missing from detail: {record['detail']!r}"
    )
    assert record["detail"]["findings_source"] == "unknown", (
        f"expected findings_source='unknown', got {record['detail']['findings_source']!r}"
    )


# ---------------------------------------------------------------------------
# AC7 — axes captured over untruncated text (§1w token after >4096 bytes)
# ---------------------------------------------------------------------------

def test_ac7_axes_captured_over_untruncated_text() -> None:
    """AC7 (§1y source-grep): phase_45_spec.py REVISE block calls
    record_plan_review_reject( with raw_review as its FIRST argument —
    tolerant of whitespace/newline between '(' and 'raw_review'.

    The current code passes truncated `findings` instead of `raw_review`, so
    §-tokens after the truncation boundary are silently dropped. This grep
    asserts the call-site fix (caller must pass untruncated raw_review).

    Fails pre-GREEN (current line 1920 reads:
        record_plan_review_reject(findings, cycle, ...)
    Passes post-GREEN once the caller is changed to:
        record_plan_review_reject(raw_review, cycle, ...)
    """
    import re

    phase_45_path = _ENGINE_ROOT / "workflows" / "phase_45_spec.py"
    assert phase_45_path.exists(), f"phase_45_spec.py not found at {phase_45_path}"

    source = phase_45_path.read_text()
    lines = source.splitlines()

    # Find the _emit_safe("phase_45_spec_revise" anchor (may span two lines)
    anchor_idx = None
    for i, line in enumerate(lines):
        if '_emit_safe(' in line and 'phase_45_spec_revise' in line:
            anchor_idx = i
            break
        # anchor may be split: _emit_safe( on one line, token on the next
        if '_emit_safe(' in line:
            # peek at next line
            if i + 1 < len(lines) and 'phase_45_spec_revise' in lines[i + 1]:
                anchor_idx = i
                break
    assert anchor_idx is not None, (
        "Could not find _emit_safe(\"phase_45_spec_revise\") anchor in phase_45_spec.py"
    )

    # Window = anchor line + 15 following lines (16 total)
    window = lines[anchor_idx: anchor_idx + 16]
    window_text = "\n".join(window)

    assert re.search(r"record_plan_review_reject\(\s*raw_review\b", window_text), (
        f"record_plan_review_reject( not called with raw_review as first arg "
        f"within 15 lines after _emit_safe('phase_45_spec_revise') anchor "
        f"(line {anchor_idx + 1}).\n"
        f"Current window passes truncated 'findings' — fix: pass raw_review instead.\n"
        f"Window:\n{window_text}"
    )


# ---------------------------------------------------------------------------
# AC8 — wiring source-grep: _resolve_unresolved_count and findings_source=
#        both appear within 15 lines after _emit_safe("phase_45_spec_revise"
# ---------------------------------------------------------------------------

def test_ac8_phase_45_spec_revise_block_wired() -> None:
    """AC8 (§1y source-grep): phase_45_spec.py REVISE block calls
    _resolve_unresolved_count( AND passes findings_source= to
    record_plan_review_reject(, both within 15 lines after the
    _emit_safe('phase_45_spec_revise' anchor.

    Fails pre-GREEN (neither symbol is wired yet). Passes post-GREEN.
    """
    phase_45_path = _ENGINE_ROOT / "workflows" / "phase_45_spec.py"
    assert phase_45_path.exists(), f"phase_45_spec.py not found at {phase_45_path}"

    source = phase_45_path.read_text()
    lines = source.splitlines()

    # Find the _emit_safe("phase_45_spec_revise" anchor
    anchor_idx = None
    for i, line in enumerate(lines):
        if '_emit_safe("phase_45_spec_revise"' in line or "_emit_safe('phase_45_spec_revise'" in line:
            anchor_idx = i
            break
    assert anchor_idx is not None, (
        "Could not find _emit_safe(\"phase_45_spec_revise\") anchor in phase_45_spec.py"
    )

    # Window = anchor line + 15 following lines (16 total)
    window = lines[anchor_idx: anchor_idx + 16]
    window_text = "\n".join(window)

    assert "_resolve_unresolved_count(" in window_text, (
        f"_resolve_unresolved_count( not found within 15 lines after "
        f"_emit_safe('phase_45_spec_revise') anchor (line {anchor_idx + 1}):\n"
        f"{window_text}"
    )
    assert "findings_source=" in window_text, (
        f"findings_source= not found within 15 lines after "
        f"_emit_safe('phase_45_spec_revise') anchor (line {anchor_idx + 1}):\n"
        f"{window_text}"
    )
