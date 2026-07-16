"""RED tests for GH443 parts 1+2 — delta-retry REVISE prompt + §1-PREFLIGHT
injection.

Spec: SHARED/memory/Decisions/2026-07-09_GH443_delta_retry_spec.md

`lib.plugins.checklist_convergence.delta_retry_prompt.build_delta_retry_prompt`
and `phase_45_spec._spec_preflight_block` do not exist yet — both are imported
INSIDE test bodies (never at module top level) so the file COLLECTS cleanly
and fails at assert/call time, not at collection time (§1q ext, D1CF5FDF).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "workflows"))

from contracts import WorkflowContext  # noqa: E402


# ─── shared fixtures / helpers ─────────────────────────────────────────────

REVIEWER_SENTINEL_LINE = "Reviewer prose sentinel line for AC3 verbatim-context check."

STRUCTURED_FINDINGS_TEXT = (
    "## Verdict\nREVISE\n\n"
    "## Findings (structured)\n"
    "```json\n"
    "[\n"
    '  {"id": "F1", "type": "gap", "evidence": "spec §3 AC2 missing validation",\n'
    '   "required_action": "add explicit forcing-function to AC2"},\n'
    '  {"id": "F2", "type": "fabrication", "evidence": "spec cites a line not read",\n'
    '   "required_action": "move claim to Open Questions"}\n'
    "]\n"
    "```\n\n"
    "## Findings\n"
    f"- {REVIEWER_SENTINEL_LINE}\n"
)

UNSTRUCTURED_FINDINGS_TEXT = (
    "## Verdict\nREVISE\n\n"
    "## Findings\n"
    "- spec is missing an Out of Scope section\n"
)


def make_ctx(scratchpad: Path, *, question: str = "Add delta-retry prompt path") -> WorkflowContext:
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={"scratchpad_dir": str(scratchpad)},
        question=question,
        session_id="test-session-GH443",
        persona="hal",
        framework=None,
        domain=None,
    )


def _write_prev_spec(scratchpad: Path) -> Path:
    from phase_45_spec import SPEC_DOC_RELPATH

    spec_path = scratchpad / SPEC_DOC_RELPATH
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text(
        "## Context\nPrevious cycle-1 spec body used as the delta-retry input.\n",
        encoding="utf-8",
    )
    return spec_path


def get_prompt_and_data(scratchpad: Path, *, cycle: int, findings: str | None):
    from phase_45_spec import _build_spec_prompt

    ctx = make_ctx(scratchpad)
    prev = {"cycle": cycle, "findings": findings}
    result = _build_spec_prompt(ctx, prev)
    assert isinstance(result.data, dict), "expected dict data on _build_spec_prompt"
    return result.data["prompt"], result.data


# ─── AC1 ────────────────────────────────────────────────────────────────────


def test_ac1_delta_path_drops_the_scaffold(tmp_path, monkeypatch):
    monkeypatch.delenv("HAL_SPEC_DELTA_RETRY", raising=False)
    from phase_45_spec import _spec_output_schema

    scratchpad = tmp_path / "scratch"
    _write_prev_spec(scratchpad)
    prompt, data = get_prompt_and_data(scratchpad, cycle=2, findings=STRUCTURED_FINDINGS_TEXT)

    schema_first_line = next(
        line for line in _spec_output_schema("x").splitlines() if line.strip()
    )

    assert "FEATURE REQUEST:" not in prompt
    assert "ARCHITECTURE DECISION" not in prompt
    # GH729: VALIDATION-LINE GUARD is now deliberately restored on the cycle>=2
    # delta path via the bounded high-binding block (2KB cap, AC-10) — the
    # remaining three assertions still prove the ~10-15KB scaffold is dropped,
    # so GH443's token-economy contract survives intact.
    assert "VALIDATION-LINE GUARD" in prompt
    assert schema_first_line not in prompt


# ─── AC2 ────────────────────────────────────────────────────────────────────


def test_ac2_delta_prompt_is_self_sufficient(tmp_path, monkeypatch):
    monkeypatch.delenv("HAL_SPEC_DELTA_RETRY", raising=False)
    # GH592: pin surgical OFF — this test covers the delta-prompt lane
    monkeypatch.setenv("HAL_SURGICAL_REVISE", "0")
    from phase_45_spec import _get_out_of_role_block

    scratchpad = tmp_path / "scratch"
    spec_path = _write_prev_spec(scratchpad)
    prompt, data = get_prompt_and_data(scratchpad, cycle=2, findings=STRUCTURED_FINDINGS_TEXT)

    out_of_role_first_line = next(
        line for line in _get_out_of_role_block().splitlines() if line.strip()
    )

    assert "Previous cycle-1 spec body used as the delta-retry input." in prompt
    assert "FINDING_F1" in prompt
    assert "FINDING_F2" in prompt
    assert str(spec_path) in prompt
    assert "full body, not a diff" in prompt
    assert out_of_role_first_line in prompt


# ─── AC3 ────────────────────────────────────────────────────────────────────


def test_ac3_verbatim_reviewer_context_preserved(tmp_path, monkeypatch):
    monkeypatch.delenv("HAL_SPEC_DELTA_RETRY", raising=False)
    scratchpad = tmp_path / "scratch"
    _write_prev_spec(scratchpad)
    prompt, _data = get_prompt_and_data(scratchpad, cycle=2, findings=STRUCTURED_FINDINGS_TEXT)

    assert "REVIEWER VERDICT (verbatim, for context):" in prompt
    assert REVIEWER_SENTINEL_LINE in prompt
    # Tie the assertion to the delta path specifically (not just to the
    # legacy restricted-writer path, which already carries a verbatim
    # reviewer block today) — the scaffold must be gone too.
    assert "FEATURE REQUEST:" not in prompt


# ─── AC4 ────────────────────────────────────────────────────────────────────


def test_ac4_kill_switch_restores_legacy(tmp_path, monkeypatch):
    monkeypatch.setenv("HAL_SPEC_DELTA_RETRY", "0")
    scratchpad = tmp_path / "scratch"
    _write_prev_spec(scratchpad)
    prompt, data = get_prompt_and_data(scratchpad, cycle=2, findings=STRUCTURED_FINDINGS_TEXT)

    assert "FEATURE REQUEST:" in prompt
    assert "CONSTRAINTS (HARD" in prompt
    assert data["delta_retry"] is False


# ─── AC5 ────────────────────────────────────────────────────────────────────


def test_ac5_legacy_no_structured_findings_path_unchanged(tmp_path, monkeypatch):
    monkeypatch.delenv("HAL_SPEC_DELTA_RETRY", raising=False)
    scratchpad = tmp_path / "scratch"
    _write_prev_spec(scratchpad)
    prompt, data = get_prompt_and_data(scratchpad, cycle=2, findings=UNSTRUCTURED_FINDINGS_TEXT)

    assert "## REVISION (cycle 2" in prompt
    assert "FEATURE REQUEST:" in prompt
    assert data["delta_retry"] is False


# ─── AC6 ────────────────────────────────────────────────────────────────────


def test_ac6_delta_flag_surfaced_in_step_data(tmp_path, monkeypatch):
    monkeypatch.delenv("HAL_SPEC_DELTA_RETRY", raising=False)
    scratchpad = tmp_path / "scratch"
    _write_prev_spec(scratchpad)
    prompt, data = get_prompt_and_data(scratchpad, cycle=2, findings=STRUCTURED_FINDINGS_TEXT)

    assert data["delta_retry"] is True
    assert data["prompt_bytes"] == len(prompt.encode("utf-8"))
    assert data["cycle"] == 2


# ─── AC7 ────────────────────────────────────────────────────────────────────


def test_ac7_token_cut_is_real_relative(tmp_path, monkeypatch):
    monkeypatch.delenv("HAL_SPEC_DELTA_RETRY", raising=False)
    # Equal-byte-length dir names so the embedded scratchpad path cannot
    # itself account for any prompt-byte delta between the two calls.
    scratchpad_a = tmp_path / "scratch_aa"
    _write_prev_spec(scratchpad_a)
    delta_prompt, _ = get_prompt_and_data(scratchpad_a, cycle=2, findings=STRUCTURED_FINDINGS_TEXT)

    monkeypatch.setenv("HAL_SPEC_DELTA_RETRY", "0")
    scratchpad_b = tmp_path / "scratch_bb"
    _write_prev_spec(scratchpad_b)
    legacy_prompt, _ = get_prompt_and_data(scratchpad_b, cycle=2, findings=STRUCTURED_FINDINGS_TEXT)

    delta_bytes = len(delta_prompt.encode("utf-8"))
    legacy_bytes = len(legacy_prompt.encode("utf-8"))
    # Margin (1KB) well under the ~10KB scaffold being dropped, but large
    # enough that no incidental path/whitespace noise can satisfy it (§1b).
    assert delta_bytes < legacy_bytes - 1024


# ─── AC8 ────────────────────────────────────────────────────────────────────


def test_ac8_preflight_block_on_cycle1(tmp_path, monkeypatch):
    monkeypatch.delenv("HAL_SPEC_DELTA_RETRY", raising=False)
    try:
        from phase_45_spec import _spec_preflight_block
    except ImportError:
        pytest.fail(
            "phase_45_spec._spec_preflight_block does not exist yet "
            "(GH443 part 2 not implemented)"
        )

    scratchpad = tmp_path / "scratch"
    prompt, _data = get_prompt_and_data(scratchpad, cycle=1, findings=None)

    assert "SPEC PREFLIGHT" in prompt
    for token in ("§1o", "§1y", "§1aa"):
        assert token in prompt, f"missing preflight axis token {token!r}"

    preflight_idx = prompt.find("SPEC PREFLIGHT")
    guard_idx = prompt.find("VALIDATION-LINE GUARD")
    assert preflight_idx >= 0 and guard_idx >= 0
    assert preflight_idx < guard_idx


# ─── AC9 ────────────────────────────────────────────────────────────────────


# GH729 supersedes GH443 AC-9: preflight is now deliberately restored on
# cycle>=2 paths via the bounded high-binding block — both assertions below
# are flipped from `not in` to `in` accordingly.
def test_ac9_preflight_present_on_cycle_ge2_GH729(tmp_path, monkeypatch):
    monkeypatch.delenv("HAL_SPEC_DELTA_RETRY", raising=False)
    scratchpad_a = tmp_path / "scratch_delta"
    _write_prev_spec(scratchpad_a)
    delta_prompt, _ = get_prompt_and_data(scratchpad_a, cycle=2, findings=STRUCTURED_FINDINGS_TEXT)
    assert "SPEC PREFLIGHT" in delta_prompt

    monkeypatch.setenv("HAL_SPEC_DELTA_RETRY", "0")
    scratchpad_b = tmp_path / "scratch_legacy"
    _write_prev_spec(scratchpad_b)
    legacy_prompt, _ = get_prompt_and_data(scratchpad_b, cycle=2, findings=STRUCTURED_FINDINGS_TEXT)
    assert "SPEC PREFLIGHT" in legacy_prompt


# ─── AC10 ───────────────────────────────────────────────────────────────────


def test_ac10_helper_is_lib_importable(tmp_path):
    try:
        from lib.plugins.checklist_convergence.delta_retry_prompt import (
            build_delta_retry_prompt,
        )
    except ImportError:
        pytest.fail(
            "lib.plugins.checklist_convergence.delta_retry_prompt."
            "build_delta_retry_prompt does not exist yet (GH443 part 1 not implemented)"
        )

    spec_path = str(tmp_path / "specs" / "build-spec.md")
    finding = {
        "id": "F1",
        "type": "gap",
        "evidence": "spec §3 AC2 missing validation",
        "required_action": "add explicit forcing-function to AC2",
    }
    result = build_delta_retry_prompt(
        spec_path, "## Context\nstub spec body.\n", [finding],
    )

    assert isinstance(result, str)
    assert "FINDING_" in result
    assert spec_path in result


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
