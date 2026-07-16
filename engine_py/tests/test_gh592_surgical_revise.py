"""RED tests for GH#592 — phase_45_spec surgical-revise (point-patch REVISE).

Spec: SHARED/memory/Decisions/2026-07-11_GH592_surgical_revise_spec.md

`lib.plugins.checklist_convergence.surgical_revise` (extract_surgical_patches,
apply_surgical_patches, build_surgical_revise_prompt) does not exist yet, and
phase_45_spec.py's `_build_spec_prompt` / `_write_spec_doc` do not have the
surgical-revise wiring yet. All new-symbol imports happen INSIDE test bodies
(never at module top level) so the file COLLECTS cleanly and fails at
assert/call/import time per test, not at collection time (§1q).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "workflows"))

from contracts import StepResult, WorkflowContext  # noqa: E402


# ─── shared fixtures / helpers ─────────────────────────────────────────────


def make_ctx(scratchpad: Path, *, question: str = "GH592 surgical revise") -> WorkflowContext:
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={"scratchpad_dir": str(scratchpad)},
        question=question,
        session_id="test-session-GH592",
        persona="hal",
        framework=None,
        domain=None,
    )


BASE_SPEC = (
    "## Context\n"
    "Section 1 body, must stay byte-identical before the patched span.\n"
    "\n"
    "## Findings\n"
    "OLD_FRAGMENT_TO_REPLACE_F1\n"
    "\n"
    "## Out of Scope\n"
    "Section 3 body, must stay byte-identical after the patched span.\n"
)

STRUCTURED_FINDINGS = [
    {
        "id": "F1",
        "type": "gap",
        "evidence": "spec §Findings has an unresolved placeholder fragment",
        "required_action": "replace OLD_FRAGMENT_TO_REPLACE_F1 with concrete text",
    },
    {
        "id": "F2",
        "type": "fabrication",
        "evidence": "spec cites an unread line",
        "required_action": "move claim to Open Questions",
    },
]


def _fenced_patches_json(patches: list[dict]) -> str:
    return "```json\n" + json.dumps(patches) + "\n```\n"


VALID_PATCH_SINGLE = [
    {"finding_id": "F1", "old": "OLD_FRAGMENT_TO_REPLACE_F1", "new": "NEW_FRAGMENT_REPLACED_F1"},
]


# ─── AC1: extract_surgical_patches + apply_surgical_patches purity ─────────


def test_ac1_extract_valid_fenced_array_returns_list():
    from lib.plugins.checklist_convergence.surgical_revise import extract_surgical_patches

    raw = _fenced_patches_json(VALID_PATCH_SINGLE)
    result = extract_surgical_patches(raw)
    assert result == VALID_PATCH_SINGLE


def test_ac1_extract_no_fenced_block_returns_none():
    from lib.plugins.checklist_convergence.surgical_revise import extract_surgical_patches

    assert extract_surgical_patches("no json fence here at all") is None


def test_ac1_extract_not_an_array_returns_none():
    from lib.plugins.checklist_convergence.surgical_revise import extract_surgical_patches

    raw = "```json\n" + json.dumps({"finding_id": "F1", "old": "x", "new": "y"}) + "\n```\n"
    assert extract_surgical_patches(raw) is None


def test_ac1_extract_bad_item_returns_none():
    from lib.plugins.checklist_convergence.surgical_revise import extract_surgical_patches

    # Missing required "old" key.
    raw = "```json\n" + json.dumps([{"finding_id": "F1", "new": "y"}]) + "\n```\n"
    assert extract_surgical_patches(raw) is None


def test_ac1_extract_last_fenced_block_wins():
    from lib.plugins.checklist_convergence.surgical_revise import extract_surgical_patches

    first = _fenced_patches_json([{"finding_id": "F1", "old": "a", "new": "b"}])
    second = _fenced_patches_json(VALID_PATCH_SINGLE)
    raw = "some prose\n" + first + "\nmore prose\n" + second
    assert extract_surgical_patches(raw) == VALID_PATCH_SINGLE


def test_ac1_apply_multi_patch_sequential_success():
    from lib.plugins.checklist_convergence.surgical_revise import apply_surgical_patches

    base = "AAA one\nBBB two\nCCC three\n"
    patches = [
        {"finding_id": "F1", "old": "AAA one", "new": "AAA ONE"},
        {"finding_id": "F2", "old": "CCC three", "new": "CCC THREE"},
    ]
    patched, meta = apply_surgical_patches(base, patches)
    assert patched == "AAA ONE\nBBB two\nCCC THREE\n"
    assert meta == {"n_patches": 2}


def test_ac1_apply_empty_patches_reason_no_patches():
    from lib.plugins.checklist_convergence.surgical_revise import apply_surgical_patches

    patched, meta = apply_surgical_patches("some text\n", [])
    assert patched is None
    assert meta["reason"] == "no_patches"
    assert meta["finding_id"] is None


def test_ac1_apply_empty_old_reason_empty_old():
    from lib.plugins.checklist_convergence.surgical_revise import apply_surgical_patches

    patched, meta = apply_surgical_patches(
        "some text\n", [{"finding_id": "F9", "old": "", "new": "x"}]
    )
    assert patched is None
    assert meta["reason"] == "empty_old"
    assert meta["finding_id"] == "F9"


def test_ac1_apply_old_not_found_reason():
    from lib.plugins.checklist_convergence.surgical_revise import apply_surgical_patches

    patched, meta = apply_surgical_patches(
        "some text\n", [{"finding_id": "F3", "old": "NOT_PRESENT_ANYWHERE", "new": "x"}]
    )
    assert patched is None
    assert meta["reason"] == "old_not_found"
    assert meta["finding_id"] == "F3"


def test_ac1_apply_old_ambiguous_reason():
    from lib.plugins.checklist_convergence.surgical_revise import apply_surgical_patches

    patched, meta = apply_surgical_patches(
        "DUP DUP\n", [{"finding_id": "F4", "old": "DUP", "new": "x"}]
    )
    assert patched is None
    assert meta["reason"] == "old_ambiguous"
    assert meta["finding_id"] == "F4"


# ─── AC2: byte-stability of untouched sections ─────────────────────────────


def test_ac2_byte_stability_untouched_prefix_and_suffix():
    from lib.plugins.checklist_convergence.surgical_revise import apply_surgical_patches

    patches = [
        {"finding_id": "F1", "old": "OLD_FRAGMENT_TO_REPLACE_F1", "new": "NEW_FRAGMENT_REPLACED_F1"}
    ]
    patched, meta = apply_surgical_patches(BASE_SPEC, patches)
    assert patched is not None
    assert meta == {"n_patches": 1}

    old = "OLD_FRAGMENT_TO_REPLACE_F1"
    idx = BASE_SPEC.index(old)
    pre = BASE_SPEC[:idx]
    post = BASE_SPEC[idx + len(old):]

    assert patched.startswith(pre), "untouched prefix must be byte-identical"
    assert patched.endswith(post), "untouched suffix must be byte-identical"
    assert patched == pre + "NEW_FRAGMENT_REPLACED_F1" + post


# ─── AC3: prompt contract ───────────────────────────────────────────────────


def test_ac3_prompt_contains_fenced_json_instruction_findings_and_spec_no_full_rewrite():
    from lib.plugins.checklist_convergence.surgical_revise import build_surgical_revise_prompt

    spec_path = "/tmp/fake/build-spec.md"
    prompt = build_surgical_revise_prompt(spec_path, BASE_SPEC, STRUCTURED_FINDINGS)

    assert "```json" in prompt
    assert "FINDING_F1" in prompt
    assert "FINDING_F2" in prompt
    assert BASE_SPEC in prompt
    assert "Write the FULL" not in prompt


# ─── AC4: _build_spec_prompt wiring ─────────────────────────────────────────


def _write_prev_spec(scratchpad: Path, text: str = BASE_SPEC) -> Path:
    from phase_45_spec import SPEC_DOC_RELPATH

    spec_path = scratchpad / SPEC_DOC_RELPATH
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text(text, encoding="utf-8")
    return spec_path


def test_ac4_surgical_path_taken_when_both_gates_on(tmp_path, monkeypatch):
    monkeypatch.delenv("HAL_SPEC_DELTA_RETRY", raising=False)
    monkeypatch.delenv("HAL_SURGICAL_REVISE", raising=False)
    from phase_45_spec import _build_spec_prompt

    scratchpad = tmp_path / "scratch"
    spec_path = _write_prev_spec(scratchpad)
    ctx = make_ctx(scratchpad)
    prev = StepResult(
        status="ok",
        data={"cycle": 2, "structured_findings": STRUCTURED_FINDINGS},
        duration_ms=0,
        step_name="gate_on_review",
    )
    result = _build_spec_prompt(ctx, prev)
    assert isinstance(result.data, dict)
    assert result.data.get("surgical_revise") is True
    assert result.data.get("surgical_base_spec") == spec_path.read_text(encoding="utf-8")


def test_ac4_legacy_delta_path_when_surgical_gate_off(tmp_path, monkeypatch):
    monkeypatch.delenv("HAL_SPEC_DELTA_RETRY", raising=False)
    monkeypatch.setenv("HAL_SURGICAL_REVISE", "0")
    from phase_45_spec import _build_spec_prompt

    scratchpad = tmp_path / "scratch"
    _write_prev_spec(scratchpad)
    ctx = make_ctx(scratchpad)
    prev = StepResult(
        status="ok",
        data={"cycle": 2, "structured_findings": STRUCTURED_FINDINGS},
        duration_ms=0,
        step_name="gate_on_review",
    )
    result = _build_spec_prompt(ctx, prev)
    assert result.data.get("delta_retry") is True
    assert not result.data.get("surgical_revise")


def test_ac4_legacy_delta_path_when_prev_surgical_fallback(tmp_path, monkeypatch):
    monkeypatch.delenv("HAL_SPEC_DELTA_RETRY", raising=False)
    monkeypatch.delenv("HAL_SURGICAL_REVISE", raising=False)
    from phase_45_spec import _build_spec_prompt

    scratchpad = tmp_path / "scratch"
    _write_prev_spec(scratchpad)
    ctx = make_ctx(scratchpad)
    prev = StepResult(
        status="ok",
        data={
            "cycle": 2,
            "structured_findings": STRUCTURED_FINDINGS,
            "surgical_fallback": True,
        },
        duration_ms=0,
        step_name="gate_on_review",
    )
    result = _build_spec_prompt(ctx, prev)
    assert result.data.get("delta_retry") is True
    assert not result.data.get("surgical_revise")


# ─── AC5 / AC6: _write_spec_doc surgical apply-success / fallback ──────────


def _make_write_prev(
    scratchpad: Path, *, doc_path: Path, base: str, raw_response: str, cycle: int = 2
) -> StepResult:
    return StepResult(
        status="ok",
        data={
            "surgical_revise": True,
            "surgical_base_spec": base,
            "raw_response": raw_response,
            "cycle": cycle,
            "doc_path": str(doc_path),
        },
        duration_ms=0,
        step_name="invoke_spec_llm",
    )


def test_ac5_surgical_apply_success_writes_canonical_and_versioned_and_emits(tmp_path, monkeypatch):
    import phase_45_spec
    from lib.plugins.checklist_convergence.surgical_revise import apply_surgical_patches

    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(phase_45_spec, "_emit_safe", lambda et, payload: events.append((et, payload)))

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    doc_path = scratchpad / "specs" / "build-spec.md"
    doc_path.parent.mkdir(parents=True, exist_ok=True)

    raw_response = _fenced_patches_json(VALID_PATCH_SINGLE)
    ctx = make_ctx(scratchpad)
    prev = _make_write_prev(
        scratchpad, doc_path=doc_path, base=BASE_SPEC, raw_response=raw_response, cycle=2
    )

    result = phase_45_spec._write_spec_doc(ctx, prev)

    expected_patched, expected_meta = apply_surgical_patches(BASE_SPEC, VALID_PATCH_SINGLE)
    assert expected_patched is not None

    canonical_bytes = doc_path.read_bytes()
    assert canonical_bytes == expected_patched.encode("utf-8")

    versioned = doc_path.parent / "build-spec-cycle-2.md"
    assert versioned.is_file()
    assert versioned.read_bytes() == expected_patched.encode("utf-8")

    applied_events = [p for et, p in events if et == "surgical_revise_applied"]
    assert applied_events, f"expected surgical_revise_applied emit, got events={events!r}"
    assert applied_events[0]["n_patches"] == expected_meta["n_patches"]

    assert result.status == "ok"
    assert result.data.get("surgical_revise") is True


def test_ac6_surgical_fallback_old_not_found_no_write_and_recoverable_retry(tmp_path, monkeypatch):
    import phase_45_spec

    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(phase_45_spec, "_emit_safe", lambda et, payload: events.append((et, payload)))

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    doc_path = scratchpad / "specs" / "build-spec.md"
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    # Canonical file does not exist yet — assert this branch never creates it.

    bad_patch = [{"finding_id": "F1", "old": "TEXT_NOT_IN_BASE_SPEC", "new": "irrelevant"}]
    raw_response = _fenced_patches_json(bad_patch)
    ctx = make_ctx(scratchpad)
    prev = _make_write_prev(
        scratchpad, doc_path=doc_path, base=BASE_SPEC, raw_response=raw_response, cycle=2
    )
    prev.data["structured_findings"] = STRUCTURED_FINDINGS

    result = phase_45_spec._write_spec_doc(ctx, prev)

    assert not doc_path.exists(), "fallback branch must NOT write canonical file"

    fallback_events = [p for et, p in events if et == "surgical_revise_fallback"]
    assert fallback_events, f"expected surgical_revise_fallback emit, got events={events!r}"
    assert fallback_events[0]["reason"] == "old_not_found"
    assert fallback_events[0]["finding_id"] == "F1"

    assert result.status == "error"
    assert result.error_code == "E_VALIDATION_RETRY"
    assert result.recoverable is True
    assert result.data["retry_from_step"] == 0
    assert result.data["cycle_count"] == 1  # cycle(2) - 1
    assert result.data["surgical_fallback"] is True
    assert "structured_findings" in result.data


def test_ac8_fallback_invalidates_invoke_sentinel(tmp_path, monkeypatch):
    """AC8 (amendment 1): fallback branch must unlink the poisoned
    invoke_spec_llm_done_c{cycle}_* resume sentinel(s), leaving sibling
    (other-step, other-cycle) sentinels untouched."""
    import phase_45_spec

    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(phase_45_spec, "_emit_safe", lambda et, payload: events.append((et, payload)))

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    doc_path = scratchpad / "specs" / "build-spec.md"
    doc_path.parent.mkdir(parents=True, exist_ok=True)

    resume_dir = scratchpad / "resume"
    resume_dir.mkdir(parents=True, exist_ok=True)
    c2_invoke_sentinel = resume_dir / "invoke_spec_llm_done_c2_rTESTRUN.json"
    c2_invoke_sentinel.write_text("{}")
    review_sentinel = resume_dir / "invoke_review_llm_done_c2_rTESTRUN.json"
    review_sentinel.write_text("{}")
    c1_invoke_sentinel = resume_dir / "invoke_spec_llm_done_c1_rTESTRUN.json"
    c1_invoke_sentinel.write_text("{}")

    bad_patch = [{"finding_id": "F1", "old": "TEXT_NOT_IN_BASE_SPEC", "new": "irrelevant"}]
    raw_response = _fenced_patches_json(bad_patch)
    ctx = make_ctx(scratchpad)
    prev = _make_write_prev(
        scratchpad, doc_path=doc_path, base=BASE_SPEC, raw_response=raw_response, cycle=2
    )
    prev.data["structured_findings"] = STRUCTURED_FINDINGS

    result = phase_45_spec._write_spec_doc(ctx, prev)

    assert result.status == "error"
    assert result.error_code == "E_VALIDATION_RETRY"

    assert not c2_invoke_sentinel.exists(), (
        "poisoned cycle-2 invoke_spec_llm sentinel must be unlinked on fallback"
    )
    assert review_sentinel.exists(), "review-step sentinel must NOT be touched"
    assert c1_invoke_sentinel.exists(), "other-cycle sentinel must NOT be touched"


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
