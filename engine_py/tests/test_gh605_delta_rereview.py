"""RED tests for GH#605 — phase_45 delta-re-review (diff-only cycle-2 re-review).

Spec: SHARED/memory/Decisions/2026-07-11_GH605_delta_rereview_spec.md

`lib.plugins.checklist_convergence.delta_reviewer_prompt` (extract_affected_sections,
build_delta_reviewer_prompt) and phase_45_spec.py's `_load_surgical_delta` /
sidecar-write / delta-path wiring in `_build_review_prompt` do NOT exist yet.
All new-symbol imports happen INSIDE test bodies (never at module top level) so
the file COLLECTS cleanly and fails at assert/call time, not at collection time
(§1q / D1CF5FDF).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

from bytedigger_engine.contracts import StepResult, WorkflowContext  # noqa: E402


def make_ctx(scratchpad: Path, *, question: str = "GH605 delta rereview") -> WorkflowContext:
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={"scratchpad_dir": str(scratchpad)},
        question=question,
        session_id="test-session-GH605",
        persona="hal",
        framework=None,
        domain=None,
    )


BASE_SPEC = (
    "## Context\n"
    "Section 1 body untouched.\n"
    "\n"
    "## Findings\n"
    "OLD_FRAGMENT_TO_REPLACE_F1\n"
    "\n"
    "## Out of Scope\n"
    "ZZUNTOUCHED_SECTION_TOKEN — this section is never patched.\n"
)

STRUCTURED_FINDINGS = [
    {
        "id": "F1",
        "type": "gap",
        "evidence": "spec has unresolved placeholder",
        "required_action": "replace OLD_FRAGMENT_TO_REPLACE_F1",
    },
    {
        "id": "F2",
        "type": "fabrication",
        "evidence": "unread citation",
        "required_action": "move claim to Open Questions",
    },
]

VALID_PATCH_SINGLE = [
    {"finding_id": "F1", "old": "OLD_FRAGMENT_TO_REPLACE_F1", "new": "NEW_FRAGMENT_REPLACED_F1"},
]

# §2.1: extract_affected_sections matches patch["new"] against the ON-DISK
# spec, which at cycle-2 review time is already POST-patch. Any fixture that
# needs the delta branch to actually be reachable must write this variant,
# not BASE_SPEC (which still contains the pre-patch OLD_ fragment).
POST_PATCH_SPEC = BASE_SPEC.replace(
    "OLD_FRAGMENT_TO_REPLACE_F1", "NEW_FRAGMENT_REPLACED_F1"
)


def _write_prev_review_with_findings(scratchpad: Path, cycle: int, findings: list[dict]) -> Path:
    from bytedigger_engine.workflows.phase_45_spec import _review_cycle_relpath

    review_path = scratchpad / _review_cycle_relpath(cycle)
    review_path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["## Findings (structured)", "```json", json.dumps(findings), "```", "VERDICT: REVISE"]
    review_path.write_text("\n".join(lines), encoding="utf-8")
    return review_path


def _write_sidecar(scratchpad: Path, cycle: int, patches: list[dict]) -> Path:
    sidecar = scratchpad / "specs" / f"surgical-delta-cycle-{cycle}.json"
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    sidecar.write_text(json.dumps({"cycle": cycle, "patches": patches}), encoding="utf-8")
    return sidecar


def _write_spec_file(scratchpad: Path, text: str) -> Path:
    from bytedigger_engine.workflows.phase_45_spec import SPEC_DOC_RELPATH

    spec_path = scratchpad / SPEC_DOC_RELPATH
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text(text, encoding="utf-8")
    return spec_path


# ─── AC1: surgical apply writes sidecar ─────────────────────────────────────


def test_ac1_surgical_apply_success_writes_delta_sidecar(tmp_path, monkeypatch):
    from bytedigger_engine.workflows import phase_45_spec

    monkeypatch.setattr(phase_45_spec, "_emit_safe", lambda et, payload: None)

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    doc_path = scratchpad / "specs" / "build-spec.md"
    doc_path.parent.mkdir(parents=True, exist_ok=True)

    raw_response = "```json\n" + json.dumps(VALID_PATCH_SINGLE) + "\n```\n"
    ctx = make_ctx(scratchpad)
    prev = StepResult(
        status="ok",
        data={
            "surgical_revise": True,
            "surgical_base_spec": BASE_SPEC,
            "raw_response": raw_response,
            "cycle": 2,
            "doc_path": str(doc_path),
        },
        duration_ms=0,
        step_name="invoke_spec_llm",
    )

    phase_45_spec._write_spec_doc(ctx, prev)

    sidecar = scratchpad / "specs" / "surgical-delta-cycle-2.json"
    assert sidecar.is_file(), "AC1 FAIL: surgical-delta-cycle-2.json not written"
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    assert payload["cycle"] == 2
    assert payload["patches"] == VALID_PATCH_SINGLE


# ─── AC2: fallback unlinks same-cycle sidecar ───────────────────────────────


def test_ac2_surgical_fallback_unlinks_same_cycle_sidecar(tmp_path, monkeypatch):
    from bytedigger_engine.workflows import phase_45_spec

    monkeypatch.setattr(phase_45_spec, "_emit_safe", lambda et, payload: None)

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    doc_path = scratchpad / "specs" / "build-spec.md"
    doc_path.parent.mkdir(parents=True, exist_ok=True)

    pre_sidecar = _write_sidecar(scratchpad, 2, VALID_PATCH_SINGLE)
    assert pre_sidecar.exists()

    bad_patch = [{"finding_id": "F1", "old": "TEXT_NOT_IN_BASE_SPEC", "new": "irrelevant"}]
    raw_response = "```json\n" + json.dumps(bad_patch) + "\n```\n"
    ctx = make_ctx(scratchpad)
    prev = StepResult(
        status="ok",
        data={
            "surgical_revise": True,
            "surgical_base_spec": BASE_SPEC,
            "raw_response": raw_response,
            "cycle": 2,
            "doc_path": str(doc_path),
            "structured_findings": STRUCTURED_FINDINGS,
        },
        duration_ms=0,
        step_name="invoke_spec_llm",
    )

    phase_45_spec._write_spec_doc(ctx, prev)

    assert not pre_sidecar.exists(), (
        "AC2 FAIL: _surgical_fallback did not unlink the same-cycle delta sidecar"
    )


# ─── AC3/AC4: extract_affected_sections ─────────────────────────────────────


def test_ac3_extract_affected_sections_returns_enclosing_section_and_dedups():
    from bytedigger_engine.lib.plugins.checklist_convergence.delta_reviewer_prompt import extract_affected_sections

    # §2.1: extract_affected_sections matches patch["new"] against the ON-DISK
    # spec text, which at cycle-2 review time is the POST-patch spec — so the
    # fixture spec here must already contain the "new" fragment, not "old".
    post_patch_spec = BASE_SPEC.replace(
        "OLD_FRAGMENT_TO_REPLACE_F1", "NEW_FRAGMENT_REPLACED_F1"
    )
    patches = [
        {"finding_id": "F1", "old": "OLD_FRAGMENT_TO_REPLACE_F1", "new": "NEW_FRAGMENT_REPLACED_F1"},
    ]
    sections = extract_affected_sections(post_patch_spec, patches)
    assert len(sections) == 1
    assert "## Findings" in sections[0]
    assert "NEW_FRAGMENT_REPLACED_F1" in sections[0]
    assert "ZZUNTOUCHED_SECTION_TOKEN" not in sections[0]

    # two patches landing in the same section dedup to one element
    spec2 = (
        "## Findings\n"
        "AAA CHANGED\n"
        "BBB CHANGED\n"
        "\n"
        "## Other\n"
        "unrelated\n"
    )
    patches2 = [
        {"finding_id": "F1", "old": "AAA marker one", "new": "AAA CHANGED"},
        {"finding_id": "F2", "old": "BBB marker two", "new": "BBB CHANGED"},
    ]
    sections2 = extract_affected_sections(spec2, patches2)
    assert len(sections2) == 1


def test_ac4_extract_affected_sections_skips_unfound_patch_and_returns_empty():
    from bytedigger_engine.lib.plugins.checklist_convergence.delta_reviewer_prompt import extract_affected_sections

    patches = [
        {"finding_id": "F9", "old": "NOT_PRESENT_ANYWHERE_XYZ", "new": "irrelevant"},
    ]
    sections = extract_affected_sections(BASE_SPEC, patches)
    assert sections == [], "AC4 FAIL: unfound patch must be skipped, all-skipped -> []"


# ─── AC5: build_delta_reviewer_prompt contract ──────────────────────────────


def test_ac5_build_delta_reviewer_prompt_contract_and_no_untouched_marker():
    from bytedigger_engine.lib.plugins.checklist_convergence.delta_reviewer_prompt import (
        build_delta_reviewer_prompt,
        extract_affected_sections,
    )

    # post-patch spec on disk (§2.1: "new" is matched against post-patch text)
    post_patch_spec = BASE_SPEC.replace(
        "OLD_FRAGMENT_TO_REPLACE_F1", "NEW_FRAGMENT_REPLACED_F1"
    )
    patches = VALID_PATCH_SINGLE
    sections = extract_affected_sections(post_patch_spec, patches)
    prompt = build_delta_reviewer_prompt(STRUCTURED_FINDINGS, patches, sections)

    assert "FINDING_F1" in prompt
    assert "FINDING_F2" in prompt
    assert "--- old" in prompt
    assert "+++ new" in prompt
    assert "OLD_FRAGMENT_TO_REPLACE_F1" in prompt
    assert "NEW_FRAGMENT_REPLACED_F1" in prompt
    assert "VERDICT: <PASS|REVISE>" in prompt or "VERDICT:" in prompt
    assert "may not introduce" in prompt.lower() or "may NOT introduce" in prompt

    assert "ZZUNTOUCHED_SECTION_TOKEN" not in prompt, (
        "AC5 FAIL: delta prompt must NOT contain content from an untouched section"
    )


# ─── AC6/AC7/AC8/AC9/AC10/AC11/AC12: _build_review_prompt delta wiring ──────


def _cycle2_ctx_and_prev(scratchpad: Path, spec_text: str = BASE_SPEC):
    _write_spec_file(scratchpad, spec_text)
    _write_prev_review_with_findings(scratchpad, 1, STRUCTURED_FINDINGS)
    ctx = make_ctx(scratchpad)
    from bytedigger_engine.workflows.phase_45_spec import SPEC_DOC_RELPATH

    prev = StepResult(
        status="ok",
        data={"spec_path": str(scratchpad / SPEC_DOC_RELPATH), "cycle": 2},
        duration_ms=0,
        step_name="verify_spec_completeness",
    )
    return ctx, prev


def test_ac6_delta_path_used_when_gate_on_and_valid_sidecar(tmp_path, monkeypatch):
    from bytedigger_engine.workflows import phase_45_spec

    monkeypatch.delenv("HAL_DELTA_REREVIEW", raising=False)
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(phase_45_spec, "_emit_safe", lambda et, payload: events.append((et, payload)))

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    # Padded spec: the untouched "## Out of Scope" section carries ~9KB of
    # filler so a real delta prompt (findings + patches + affected sections
    # only) is provably smaller than the full spec embed.
    # §2.1: on-disk spec at cycle-2 review time is POST-patch (the "new"
    # fragment must be present for extract_affected_sections to find it).
    padded_spec = (
        "## Context\n"
        "Section 1 body untouched.\n"
        "\n"
        "## Findings\n"
        "NEW_FRAGMENT_REPLACED_F1\n"
        "\n"
        "## Out of Scope\n"
        "ZZUNTOUCHED_SECTION_TOKEN — this section is never patched.\n"
        + ("filler filler filler filler filler filler filler filler\n" * 160)
    )
    ctx, prev = _cycle2_ctx_and_prev(scratchpad, spec_text=padded_spec)
    _write_sidecar(scratchpad, 2, VALID_PATCH_SINGLE)

    result = phase_45_spec._build_review_prompt(ctx, prev)

    assert result.data.get("delta_rereview") is True, (
        "AC6 FAIL: data['delta_rereview'] must be True on the delta path"
    )
    used = [p for et, p in events if et == "delta_rereview_used"]
    assert used, f"AC6 FAIL: expected delta_rereview_used emit, got events={events!r}"
    assert used[0]["prompt_bytes"] < used[0]["full_spec_bytes"], (
        "AC6 FAIL: delta prompt_bytes must be smaller than full_spec_bytes"
    )


def test_ac7_gate_off_falls_back_to_full_embed(tmp_path, monkeypatch):
    from bytedigger_engine.workflows import phase_45_spec

    monkeypatch.setenv("HAL_DELTA_REREVIEW", "0")
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(phase_45_spec, "_emit_safe", lambda et, payload: events.append((et, payload)))

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    ctx, prev = _cycle2_ctx_and_prev(scratchpad)
    _write_sidecar(scratchpad, 2, VALID_PATCH_SINGLE)

    result = phase_45_spec._build_review_prompt(ctx, prev)

    assert "CYCLE-2 SPEC:" in result.data["prompt"]
    assert BASE_SPEC in result.data["prompt"]
    assert "delta_rereview" not in result.data
    delta_events = [et for et, _ in events if et.startswith("delta_rereview")]
    assert delta_events == [], f"AC7 FAIL: unexpected delta events with gate off: {delta_events!r}"


def test_ac8_gate_on_no_sidecar_falls_back_with_reason_no_sidecar(tmp_path, monkeypatch):
    from bytedigger_engine.workflows import phase_45_spec

    monkeypatch.delenv("HAL_DELTA_REREVIEW", raising=False)
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(phase_45_spec, "_emit_safe", lambda et, payload: events.append((et, payload)))

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    ctx, prev = _cycle2_ctx_and_prev(scratchpad)
    # deliberately no sidecar written

    result = phase_45_spec._build_review_prompt(ctx, prev)

    fallback = [p for et, p in events if et == "delta_rereview_fallback"]
    assert fallback, f"AC8 FAIL: expected delta_rereview_fallback emit, got {events!r}"
    assert fallback[0]["reason"] == "no_sidecar"
    assert "CYCLE-2 SPEC:" in result.data["prompt"]
    assert BASE_SPEC in result.data["prompt"]


@pytest.mark.parametrize(
    "setup_kind,expected_reason",
    [("mismatch", "cycle_mismatch"), ("bad_json", "parse_error"), ("no_sections", "no_sections")],
)
def test_ac9_load_surgical_delta_fallback_reasons(tmp_path, monkeypatch, setup_kind, expected_reason):
    from bytedigger_engine.workflows import phase_45_spec

    monkeypatch.delenv("HAL_DELTA_REREVIEW", raising=False)
    events: list[tuple[str, dict]] = []
    monkeypatch.setattr(phase_45_spec, "_emit_safe", lambda et, payload: events.append((et, payload)))

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    ctx, prev = _cycle2_ctx_and_prev(scratchpad)

    sidecar = scratchpad / "specs" / "surgical-delta-cycle-2.json"
    sidecar.parent.mkdir(parents=True, exist_ok=True)
    if setup_kind == "mismatch":
        sidecar.write_text(json.dumps({"cycle": 1, "patches": VALID_PATCH_SINGLE}), encoding="utf-8")
    elif setup_kind == "bad_json":
        sidecar.write_text("{not valid json", encoding="utf-8")
    elif setup_kind == "no_sections":
        unfindable = [
            {"finding_id": "F1", "old": "NOT_PRESENT_ANYWHERE_XYZ", "new": "QQZZ_NEVER_IN_SPEC_TOKEN"}
        ]
        sidecar.write_text(json.dumps({"cycle": 2, "patches": unfindable}), encoding="utf-8")

    result = phase_45_spec._build_review_prompt(ctx, prev)

    fallback = [p for et, p in events if et == "delta_rereview_fallback"]
    assert fallback, f"AC9[{setup_kind}] FAIL: expected delta_rereview_fallback emit, got {events!r}"
    assert fallback[0]["reason"] == expected_reason
    assert "CYCLE-2 SPEC:" in result.data["prompt"]


def test_ac10_delta_prompt_ends_with_rule_axes_suffix(tmp_path, monkeypatch):
    from bytedigger_engine.workflows import phase_45_spec

    monkeypatch.delenv("HAL_DELTA_REREVIEW", raising=False)
    monkeypatch.setattr(phase_45_spec, "_emit_safe", lambda et, payload: None)

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    ctx, prev = _cycle2_ctx_and_prev(scratchpad, spec_text=POST_PATCH_SPEC)
    _write_sidecar(scratchpad, 2, VALID_PATCH_SINGLE)

    result = phase_45_spec._build_review_prompt(ctx, prev)

    assert result.data.get("delta_rereview") is True, (
        "AC10 FAIL: delta path not taken (gate ON + valid sidecar) — "
        "delta_rereview must be True before checking the RULE-AXES suffix"
    )
    assert "CYCLE-2 SPEC:" not in result.data["prompt"], (
        "AC10 FAIL: prompt is the full embed, not the delta prompt"
    )
    assert "RULE-AXES REQUIREMENT (GH497)" in result.data["prompt"], (
        "AC10 FAIL: delta prompt missing RULE-AXES suffix"
    )


def test_ac11_delta_prompt_bytes_and_restricted_reviewer_flag(tmp_path, monkeypatch):
    from bytedigger_engine.workflows import phase_45_spec

    monkeypatch.delenv("HAL_DELTA_REREVIEW", raising=False)
    monkeypatch.setattr(phase_45_spec, "_emit_safe", lambda et, payload: None)

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    ctx, prev = _cycle2_ctx_and_prev(scratchpad, spec_text=POST_PATCH_SPEC)
    _write_sidecar(scratchpad, 2, VALID_PATCH_SINGLE)

    result = phase_45_spec._build_review_prompt(ctx, prev)

    assert result.data.get("delta_rereview") is True, (
        "AC11 FAIL: delta path not taken (gate ON + valid sidecar) — "
        "delta_rereview must be True before checking prompt_bytes/restricted_reviewer"
    )
    assert result.data.get("restricted_reviewer") is True, (
        "AC11 FAIL: restricted_reviewer flag must be preserved on delta path"
    )
    assert result.data["prompt_bytes"] == len(result.data["prompt"].encode("utf-8")), (
        "AC11 FAIL: prompt_bytes must equal len(prompt.encode('utf-8'))"
    )


def test_ac12_uncovered_finding_still_present_in_delta_prompt(tmp_path, monkeypatch):
    from bytedigger_engine.workflows import phase_45_spec

    monkeypatch.delenv("HAL_DELTA_REREVIEW", raising=False)
    monkeypatch.setattr(phase_45_spec, "_emit_safe", lambda et, payload: None)

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    ctx, prev = _cycle2_ctx_and_prev(scratchpad, spec_text=POST_PATCH_SPEC)
    # only F1 has a patch; F2 is uncovered
    _write_sidecar(scratchpad, 2, VALID_PATCH_SINGLE)

    result = phase_45_spec._build_review_prompt(ctx, prev)

    assert result.data.get("delta_rereview") is True, (
        "AC12 FAIL: delta path not taken (gate ON + valid sidecar) — "
        "delta_rereview must be True before checking finding coverage"
    )
    assert "CYCLE-2 SPEC:" not in result.data["prompt"], (
        "AC12 FAIL: prompt is the full embed, not the delta prompt"
    )
    assert "FINDING_F2" in result.data["prompt"], (
        "AC12 FAIL: uncovered finding F2 must still appear in the delta prompt"
    )


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
