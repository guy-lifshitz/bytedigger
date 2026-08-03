"""RED tests for GH#638 — delta-re-review: content-vote, not patch-key-existence
(deletion-aware).

Spec: SHARED/memory/Decisions/2026-07-12_EE56C70D_gh638_delta_rereview_content_vote_spec.md

UUT: lib.plugins.checklist_convergence.delta_reviewer_prompt
  - extract_affected_sections(spec_text, patches)          [existing, edited]
  - build_delta_reviewer_prompt(findings, patches, sections) [existing, edited]
  - map_findings_to_patches(findings, patches)               [NEW, does not exist yet]

`map_findings_to_patches` does not exist yet at RED time, so its import is
deferred INSIDE each test function body (never at module top level) so the
file COLLECTS cleanly and fails at assert/call time, not at collection time
(§1q / D1CF5FDF). The two existing functions may be imported normally.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

from bytedigger_engine.lib.plugins.checklist_convergence.delta_reviewer_prompt import (  # noqa: E402
    build_delta_reviewer_prompt,
    extract_affected_sections,
)

SPEC_TEXT = (
    "## Context\n"
    "Section 1 body untouched.\n"
    "\n"
    "## Findings\n"
    "OLD_FRAGMENT_TO_REPLACE_F1\n"
    "\n"
    "## Out of Scope\n"
    "ZZUNTOUCHED_SECTION_TOKEN — this section is never patched.\n"
)

FINDINGS_TWO = [
    {"id": "1", "type": "gap", "evidence": "e1", "required_action": "a1"},
    {"id": "2", "type": "gap", "evidence": "e2", "required_action": "a2"},
]


# ─── AC1 ─────────────────────────────────────────────────────────────────────


def test_ac1_map_findings_to_patches_keys_every_finding_unkeyed_is_empty_list():
    from bytedigger_engine.lib.plugins.checklist_convergence.delta_reviewer_prompt import (
        map_findings_to_patches,
    )

    findings = [{"id": "1"}, {"id": "2"}]
    patches = [{"finding_id": "1", "old": "OLD_FRAGMENT_TO_REPLACE_F1", "new": "NEW1"}]

    mapping = map_findings_to_patches(findings, patches)

    assert "1" in mapping and "2" in mapping, (
        "AC1 FAIL: mapping must have a key for every finding id"
    )
    assert mapping["2"] == [], "AC1 FAIL: unkeyed finding must map to [] (present, not missing)"
    assert len(mapping["1"]) == 1


# ─── AC2 ─────────────────────────────────────────────────────────────────────


def test_ac2_bundled_patch_grouped_under_keyed_finding_and_appears_in_prompt():
    from bytedigger_engine.lib.plugins.checklist_convergence.delta_reviewer_prompt import (
        map_findings_to_patches,
    )

    patch = {"finding_id": "1", "old": "OLD_FRAGMENT_TO_REPLACE_F1", "new": "NEW1"}
    patches = [patch]

    mapping = map_findings_to_patches(FINDINGS_TWO, patches)
    assert mapping["1"] == [patch]
    assert mapping["2"] == []

    prompt = build_delta_reviewer_prompt(FINDINGS_TWO, patches, [])
    assert "OLD_FRAGMENT_TO_REPLACE_F1" in prompt, (
        "AC2 FAIL: bundled patch's old text must still appear in the full-diff block"
    )


# ─── AC3 ─────────────────────────────────────────────────────────────────────


def test_ac3_int_str_id_mismatch_still_matches_string_normalized():
    from bytedigger_engine.lib.plugins.checklist_convergence.delta_reviewer_prompt import (
        map_findings_to_patches,
    )

    findings = [{"id": 1}]
    patches = [{"finding_id": "1", "old": "OLD_FRAGMENT_TO_REPLACE_F1", "new": "NEW1"}]

    mapping = map_findings_to_patches(findings, patches)
    assert mapping[str(1)], "AC3 FAIL: int finding id vs str patch finding_id must match"


# ─── AC4 ─────────────────────────────────────────────────────────────────────


def test_ac4_extract_affected_sections_deletion_produces_removed_tagged_section():
    patches = [
        {"finding_id": "1", "old": "OLD_FRAGMENT_TO_REPLACE_F1", "new": ""},
    ]
    sections = extract_affected_sections(SPEC_TEXT, patches)
    assert any(
        "[REMOVED]" in s and "OLD_FRAGMENT_TO_REPLACE_F1" in s for s in sections
    ), "AC4 FAIL: deletion patch must produce a [REMOVED]-tagged section carrying old text"


# ─── AC5 ─────────────────────────────────────────────────────────────────────


def test_ac5_extract_affected_sections_still_skips_unfound_insertion():
    patches = [
        {"finding_id": "9", "old": "irrelevant", "new": "ZZZ not in spec"},
    ]
    sections = extract_affected_sections(SPEC_TEXT, patches)
    assert sections == [], "AC5 FAIL: unfound insertion must still be skipped (GH605 AC4)"


# ─── AC6 ─────────────────────────────────────────────────────────────────────


def test_ac6_mixed_batch_insertion_and_deletion_both_contribute():
    post_patch_spec = SPEC_TEXT + "\nNEW_INSERTED_FRAGMENT_XYZ\n"
    patches = [
        {"finding_id": "1", "old": "irrelevant_old_1", "new": "NEW_INSERTED_FRAGMENT_XYZ"},
        {"finding_id": "2", "old": "OLD_FRAGMENT_TO_REPLACE_F1", "new": ""},
    ]
    sections = extract_affected_sections(post_patch_spec, patches)
    assert len(sections) == 2, "AC6 FAIL: both insertion and deletion must contribute"
    assert "NEW_INSERTED_FRAGMENT_XYZ" in sections[0]
    assert "[REMOVED]" in sections[1]


# ─── AC7 ─────────────────────────────────────────────────────────────────────


def test_ac7_prompt_no_longer_hard_rules_unkeyed_as_unresolved():
    prompt = build_delta_reviewer_prompt(FINDINGS_TWO, [], [])
    assert "has NOT been addressed" not in prompt, (
        "AC7 FAIL: old hard UNRESOLVED-by-cardinality rule must be removed"
    )


# ─── AC8 ─────────────────────────────────────────────────────────────────────


def test_ac8_prompt_contains_content_vote_and_deletion_valid_tokens():
    prompt = build_delta_reviewer_prompt(FINDINGS_TWO, [], [])
    assert "CONTENT of the surgical diff" in prompt, (
        "AC8 FAIL: content-vote directive token missing"
    )
    assert "DELETED" in prompt, "AC8 FAIL: deletion-valid token 'DELETED' missing"
    assert "empty) is RESOLVED" in prompt, (
        "AC8 FAIL: deletion-valid frozen literal 'empty) is RESOLVED' missing"
    )


# ─── AC9 ─────────────────────────────────────────────────────────────────────


def test_ac9_prompt_preserves_verdict_parser_contract_tokens():
    prompt = build_delta_reviewer_prompt(FINDINGS_TWO, [], [])
    assert "FINDING_<id>: <RESOLVED|UNRESOLVED>" in prompt
    assert "VERDICT: <PASS|REVISE>" in prompt
    assert "VERDICT=PASS iff ALL findings RESOLVED" in prompt


# ─── AC10 ────────────────────────────────────────────────────────────────────


def test_ac10_bundling_end_to_end_no_hardcoded_unresolved_for_unkeyed_finding():
    patch = {"finding_id": "1", "old": "OLD_FRAGMENT_TO_REPLACE_F1", "new": "NEW1"}
    patches = [patch]
    prompt = build_delta_reviewer_prompt(FINDINGS_TWO, patches, [])

    assert "no patch labelled with this id" in prompt, (
        "AC10 FAIL: unkeyed finding must get the non-binding hint"
    )
    assert "FINDING_2: UNRESOLVED" not in prompt, (
        "AC10 FAIL: prompt must not hard-code an UNRESOLVED verdict for finding 2"
    )
    assert "OLD_FRAGMENT_TO_REPLACE_F1" in prompt, (
        "AC10 FAIL: full diff (patch old text) must still be present"
    )


# ─── AC11 (§1ab re-entry) ────────────────────────────────────────────────────


def test_ac11_entry_path_agnostic_idempotent_output():
    from bytedigger_engine.lib.plugins.checklist_convergence.delta_reviewer_prompt import (
        map_findings_to_patches,
    )

    patches = [{"finding_id": "1", "old": "OLD_FRAGMENT_TO_REPLACE_F1", "new": "NEW1"}]
    sections = extract_affected_sections(
        SPEC_TEXT.replace("OLD_FRAGMENT_TO_REPLACE_F1", "NEW1"), patches
    )

    prompt1 = build_delta_reviewer_prompt(FINDINGS_TWO, patches, sections)
    prompt2 = build_delta_reviewer_prompt(FINDINGS_TWO, patches, sections)
    assert prompt1 == prompt2, "AC11 FAIL: prompt build must be byte-identical across re-entry"

    m1 = map_findings_to_patches(FINDINGS_TWO, patches)
    m2 = map_findings_to_patches(FINDINGS_TWO, patches)
    assert m1 == m2, "AC11 FAIL: map_findings_to_patches must be idempotent"


# ─── AC12 ────────────────────────────────────────────────────────────────────


def test_ac12_deletion_only_batch_non_empty_sections_and_valid_tokens():
    patches = [
        {"finding_id": "1", "old": "OLD_FRAGMENT_TO_REPLACE_F1", "new": ""},
        {"finding_id": "2", "old": "ZZUNTOUCHED_SECTION_TOKEN", "new": ""},
    ]
    sections = extract_affected_sections(SPEC_TEXT, patches)
    assert sections != [], (
        "AC12 FAIL: deletion-only batch must not abandon the delta path (non-empty sections)"
    )

    prompt = build_delta_reviewer_prompt(FINDINGS_TWO, patches, sections)
    assert "DELETED" in prompt
    assert "empty) is RESOLVED" in prompt


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
