"""RED tests for 184717A7 — _extract_ops: skip numbered/bullet-bold LABELS (trailing colon).

Agreement: 184717A7 (OPTION-D PATCH — funnels into 1C22838E build-error corpus prevention).
Class: PATCH (single-instance FP fix — _extract_ops colon-trailing label guard).

Tests MUST FAIL until GREEN adds the trailing-colon guard in _extract_ops:
  - SYSTEM/cli/build/engine_py/spec_coverage.py  (_extract_ops only)

Fail mechanism: module-level import of spec_coverage resolves cleanly (conftest
puts engine_py root on sys.path at collection time per §1q / 81F97F3D gate).
Tests FAIL at assert time, not collect time.

§1i: no singleton/time-dependent resources; N/A.
§1l: each AC anchors on the real return value of _extract_ops / scan_spec_coverage
     over concrete inline fixtures — no UUT mock, no patching.
§1q-ext (D1CF5FDF): imports resolve at collection time (module exists); tests fail
     at assert time — no collection-blocking ImportError.

Expected RED failures (current prod has no colon-guard):
  AC1 FAIL — label extracted as op (bug)
  AC4 FAIL — bullet label extracted as op (bug)
  AC5 FAIL — count is 3 not 2 (label not filtered)
  AC6 FAIL — scan_spec_coverage returns spurious finding for colon-label

Expected PASS in RED (regression guards, already correct):
  AC2 PASS — real numbered-bold op extracted
  AC3 PASS — real bullet-bold op extracted
  AC7 PASS — real uncovered op still flagged
  AC8 PASS — mid-name colon op NOT skipped (correct behavior already)
"""
from __future__ import annotations

from pathlib import Path

import pytest

# conftest.py injects engine_py root into sys.path at collection time (§1q /
# 81F97F3D gate) — safe to import at module level, no sys.path manipulation here.
from spec_coverage import _extract_ops, scan_spec_coverage


# ─── helpers ─────────────────────────────────────────────────────────────────

def _lines_with_sec2(*items: str) -> list[str]:
    """Return a minimal §2-section line list: heading then item lines."""
    return ["## §2 Design"] + list(items)


# ─── AC1: numbered-bold label (trailing colon) is skipped ────────────────────

def test_ac1_numbered_label_trailing_colon_skipped() -> None:
    """AC1: '3. **Frozen fast-path:**' under §2 → _extract_ops returns NO entry
    whose line corresponds to that label line (label must be skipped)."""
    lines = _lines_with_sec2("3. **Frozen fast-path:**")
    # Line 1 = heading; line 2 = the label line → label is at lineno 2
    label_lineno = 2

    ops = _extract_ops(lines)

    line_numbers = [o["line"] for o in ops]
    assert label_lineno not in line_numbers, (
        f"Expected label '3. **Frozen fast-path:**' at line {label_lineno} to be "
        f"SKIPPED (trailing colon = sub-step label), but _extract_ops returned "
        f"entries at lines {line_numbers!r}: {ops!r}"
    )


# ─── AC2: real numbered-bold op (no colon) is extracted ──────────────────────

def test_ac2_real_numbered_bold_op_extracted() -> None:
    """AC2: '1. **claim**' under §2 → _extract_ops returns op with op=='claim'."""
    lines = _lines_with_sec2("1. **claim**")

    ops = _extract_ops(lines)

    op_names = [o["op"] for o in ops]
    assert any(op == "claim" for op in op_names), (
        f"Expected op 'claim' to be extracted from '1. **claim**', "
        f"got ops={op_names!r}: {ops!r}"
    )


# ─── AC3: real bullet-bold op (no colon) is extracted ────────────────────────

def test_ac3_real_bullet_bold_op_extracted() -> None:
    """AC3: '- **release**' under §2 → _extract_ops returns op with op=='release'."""
    lines = _lines_with_sec2("- **release**")

    ops = _extract_ops(lines)

    op_names = [o["op"] for o in ops]
    assert any(op == "release" for op in op_names), (
        f"Expected op 'release' to be extracted from '- **release**', "
        f"got ops={op_names!r}: {ops!r}"
    )


# ─── AC4: bullet-bold label (trailing colon) is skipped ──────────────────────

def test_ac4_bullet_label_trailing_colon_skipped() -> None:
    """AC4: '- **Setup phase:**' under §2 → _extract_ops returns NO entry for
    that line (bullet label with trailing colon skipped)."""
    lines = _lines_with_sec2("- **Setup phase:**")
    label_lineno = 2  # heading=line1, label=line2

    ops = _extract_ops(lines)

    line_numbers = [o["line"] for o in ops]
    assert label_lineno not in line_numbers, (
        f"Expected bullet label '- **Setup phase:**' at line {label_lineno} to be "
        f"SKIPPED (trailing colon = sub-step label), but _extract_ops returned "
        f"entries at lines {line_numbers!r}: {ops!r}"
    )


# ─── AC5: mixed section — labels filtered, real ops counted exactly ───────────

def test_ac5_mixed_section_labels_filtered_exact_count() -> None:
    """AC5: §2 with claim, 'Frozen fast-path:' label, refresh → exactly 2 ops:
    claim and refresh (label filtered, count is exact)."""
    lines = _lines_with_sec2(
        "1. **claim**",
        "2. **Frozen fast-path:**",
        "3. **refresh**",
    )

    ops = _extract_ops(lines)

    op_names = [o["op"] for o in ops]
    assert len(ops) == 2, (
        f"Expected exactly 2 ops (claim, refresh) after filtering label, "
        f"got {len(ops)} ops={op_names!r}: {ops!r}"
    )
    assert "claim" in op_names, (
        f"Expected 'claim' in ops, got op_names={op_names!r}"
    )
    assert "refresh" in op_names, (
        f"Expected 'refresh' in ops, got op_names={op_names!r}"
    )


# ─── AC6: scan_spec_coverage returns [] when §2 has ONLY colon-labels ─────────

# Full spec text: §2 has ONLY colon-labels; §3 AC table covers nothing real.
# Bug: current code treats labels as ops → spurious E_SPEC_COVERAGE findings.
# After GREEN fix: scan returns [] (labels are not ops → nothing uncovered).
_FIXTURE_ONLY_LABELS = """\
# Spec with sub-step labels only

## §2 Design

1. **Frozen fast-path:** description
2. **Setup phase:** description
3. **Cleanup step:** description

## §3 Acceptance Criteria

| # | AC | Behavior |
|---|----|----|
| AC1 | some test | verifies something |
"""


def test_ac6_only_colon_labels_no_spurious_findings() -> None:
    """AC6: spec whose §2 has ONLY trailing-colon labels + a valid AC table
    → scan_spec_coverage returns [] (no spurious UNCOVERED-OP findings)."""
    findings = scan_spec_coverage(_FIXTURE_ONLY_LABELS)

    assert findings == [], (
        f"Expected [] (no real ops — all labels should be skipped), "
        f"got {len(findings)} spurious finding(s): {findings!r}"
    )


# ─── AC7: real uncovered op still flagged (regression guard) ─────────────────

_FIXTURE_REAL_OP_UNCOVERED = """\
# Spec with a real uncovered op

## §2 Design

- **purge** — removes stale entries

## §3 Acceptance Criteria

| # | AC | Behavior |
|---|----|----|
| AC1 | claim test | verifies claim |
"""


def test_ac7_real_uncovered_op_still_flagged() -> None:
    """AC7: §2 has real op '- **purge**' (no colon, not in AC table)
    → scan_spec_coverage returns ≥1 finding with op=='purge' (lint still works)."""
    findings = scan_spec_coverage(_FIXTURE_REAL_OP_UNCOVERED)

    assert len(findings) >= 1, (
        f"Expected ≥1 finding for uncovered op 'purge', got findings={findings!r}"
    )
    purge_findings = [f for f in findings if "purge" in f["op"].lower()]
    assert purge_findings, (
        f"Expected a finding citing op 'purge', got findings={findings!r}"
    )


# ─── AC8: mid-name colon (NOT trailing) — op is NOT skipped ──────────────────

def test_ac8_mid_name_colon_not_skipped() -> None:
    """AC8: '- **claim: do the thing**' under §2 — mid-name colon, name does NOT
    end with ':' → _extract_ops returns an op whose 'op' startswith 'claim:'."""
    lines = _lines_with_sec2("- **claim: do the thing**")

    ops = _extract_ops(lines)

    assert ops, (
        f"Expected _extract_ops to extract an op from '- **claim: do the thing**' "
        f"(mid-name colon must NOT be skipped), but got empty list"
    )
    op_names = [o["op"] for o in ops]
    assert any(name.startswith("claim:") for name in op_names), (
        f"Expected at least one op starting with 'claim:' (mid-colon not trimmed), "
        f"got op_names={op_names!r}: {ops!r}"
    )
