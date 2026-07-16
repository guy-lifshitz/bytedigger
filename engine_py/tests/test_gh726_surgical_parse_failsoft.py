"""RED tests for GH#726 — surgical-revise parser fail-soft on format-variance.

Spec: SHARED/memory/Decisions/2026-07-13_GH726_surgical_parse_failsoft_spec.md

`extract_surgical_patches` on main is strict: it only accepts a lowercase
``` ```json\n<array>``` ``` fence where the array body starts on the NEXT
line. This file exercises the widened fail-soft precedence (uppercase tag,
same-line array, generic fence, bare array, last-json-fence-wins over decoy
brackets) while confirming existing strict-content-validation guards
(non-array, missing key, empty old, no-json, empty string) are preserved.
AC12/AC13 (Opus adversarial-guard additions) assert fail-soft `None` on a
decoy bracket preceding a bare array and on a non-JSON generic code fence —
these are safety regression guards expected to PASS before and after GREEN.

All new-symbol imports happen INSIDE test bodies (never at module top level)
so the file COLLECTS cleanly and fails at assert/call time per test, not at
collection time (§1q / D1CF5FDF).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "workflows"))


VALID_PATCH_SINGLE = [
    {"finding_id": "F1", "old": "SOME_UNIQUE_OLD", "new": "SOME_NEW"},
]


# ─── AC1: uppercase ```JSON fence tag ───────────────────────────────────────


def test_ac1_uppercase_json_fence_tag_returns_list():
    from lib.plugins.checklist_convergence.surgical_revise import extract_surgical_patches

    raw = "```JSON\n" + json.dumps(VALID_PATCH_SINGLE) + "\n```\n"
    result = extract_surgical_patches(raw)
    assert result == VALID_PATCH_SINGLE


# ─── AC2: array on same line as ```json fence (no newline before array) ────


def test_ac2_array_same_line_as_json_fence_returns_list():
    from lib.plugins.checklist_convergence.surgical_revise import extract_surgical_patches

    raw = "```json" + json.dumps(VALID_PATCH_SINGLE) + "```\n"
    result = extract_surgical_patches(raw)
    assert result == VALID_PATCH_SINGLE


# ─── AC3: generic fence, no json language tag ───────────────────────────────


def test_ac3_generic_fence_no_json_tag_returns_list():
    from lib.plugins.checklist_convergence.surgical_revise import extract_surgical_patches

    raw = "```\n" + json.dumps(VALID_PATCH_SINGLE) + "\n```\n"
    result = extract_surgical_patches(raw)
    assert result == VALID_PATCH_SINGLE


# ─── AC4: bare valid JSON array, no fence at all ────────────────────────────


def test_ac4_bare_array_no_fence_returns_list():
    from lib.plugins.checklist_convergence.surgical_revise import extract_surgical_patches

    raw = json.dumps(VALID_PATCH_SINGLE)
    result = extract_surgical_patches(raw)
    assert result == VALID_PATCH_SINGLE


# ─── AC5: two ```json fences, decoy first, valid second — last-fence-wins ──


def test_ac5_two_json_fences_last_fence_wins():
    from lib.plugins.checklist_convergence.surgical_revise import extract_surgical_patches

    decoy = [{"finding_id": "F1", "old": "a", "new": "b"}]
    first = "```json\n" + json.dumps(decoy) + "\n```\n"
    second = "```json\n" + json.dumps(VALID_PATCH_SINGLE) + "\n```\n"
    raw = "some prose\n" + first + "\nmore prose\n" + second
    result = extract_surgical_patches(raw)
    assert result == VALID_PATCH_SINGLE


# ─── AC6: ```json fence wrapping a JSON object (not array) → None ─────────


def test_ac6_json_object_not_array_returns_none():
    from lib.plugins.checklist_convergence.surgical_revise import extract_surgical_patches

    raw = "```json\n" + json.dumps({"finding_id": "F1", "old": "x", "new": "y"}) + "\n```\n"
    assert extract_surgical_patches(raw) is None


# ─── AC7: array item missing "old" key → None ───────────────────────────────


def test_ac7_array_item_missing_old_key_returns_none():
    from lib.plugins.checklist_convergence.surgical_revise import extract_surgical_patches

    raw = "```json\n" + json.dumps([{"finding_id": "F1", "new": "y"}]) + "\n```\n"
    assert extract_surgical_patches(raw) is None


# ─── AC8: prose with no JSON array anywhere → None ──────────────────────────


def test_ac8_prose_no_array_anywhere_returns_none():
    from lib.plugins.checklist_convergence.surgical_revise import extract_surgical_patches

    assert extract_surgical_patches("no patches here at all") is None


# ─── AC9: empty string → None ───────────────────────────────────────────────


def test_ac9_empty_string_returns_none():
    from lib.plugins.checklist_convergence.surgical_revise import extract_surgical_patches

    assert extract_surgical_patches("") is None


# ─── AC10: prose decoy bracket + valid ```json fence — fence beats bare ────


def test_ac10_prose_decoy_bracket_plus_fence_prefers_fenced_list():
    from lib.plugins.checklist_convergence.surgical_revise import extract_surgical_patches

    fenced = "```json\n" + json.dumps(VALID_PATCH_SINGLE) + "\n```\n"
    raw = "prose with a decoy [not, json] and then\n" + fenced
    result = extract_surgical_patches(raw)
    assert result == VALID_PATCH_SINGLE


# ─── AC11: array item with empty "old" → None ───────────────────────────────


def test_ac11_array_item_empty_old_returns_none():
    from lib.plugins.checklist_convergence.surgical_revise import extract_surgical_patches

    raw = "```json\n" + json.dumps(
        [{"finding_id": "F1", "old": "", "new": "y"}]
    ) + "\n```\n"
    assert extract_surgical_patches(raw) is None


# ─── AC12: decoy bracket before bare array → None (fail-soft, no silent apply) ─


def test_ac12_bare_array_with_decoy_prose_bracket_returns_none():
    from lib.plugins.checklist_convergence.surgical_revise import extract_surgical_patches

    raw = "note: see item [3] for context, then\n" + json.dumps(
        [{"finding_id": "F1", "old": "o", "new": "n"}]
    )
    assert extract_surgical_patches(raw) is None


# ─── AC13: non-json generic code fence with bare list body → None ─────────


def test_ac13_non_json_generic_code_fence_returns_none():
    from lib.plugins.checklist_convergence.surgical_revise import extract_surgical_patches

    raw = "```python\nprint([1, 2])\n```"
    assert extract_surgical_patches(raw) is None


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
