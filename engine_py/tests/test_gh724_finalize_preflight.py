"""RED tests for GH724 Change A — §2-vs-§3 finalize coverage preflight item 5.

Spec: SHARED/memory/Decisions/2026-07-15_GH724_generator_rule_injection_spec.md

UUTs: `_spec_preflight_block()`, `SPEC_HIGH_BINDING_AXES`,
`missing_high_binding_axes()`, `_spec_high_binding_block()`,
`_build_spec_prompt` (all `phase_45_spec.py`) and
`spec_coverage._FINALIZE_GROUP_PATTERNS` — never mocked/patched (§1l).

Item 5 ("5. §2-vs-§3 finalize coverage ...") does not exist yet in
`_spec_preflight_block()` on this worktree base, so every assertion below
FAILS against current production code — that is the RED.

Symbols are imported INSIDE test bodies (§1q ext / D1CF5FDF) so the file
COLLECTS even though the emitted TEXT under test does not exist yet; the
symbols themselves already exist (only their content changes), matching the
sibling convention in test_GH723_producer_guard_reachability.py.

sys.path is NOT touched — conftest.py installs the conftest-import-time
singleton (engine_py root + workflows dir) per §1q/81F97F3D.
"""
from __future__ import annotations

from pathlib import Path

from bytedigger_engine.contracts import WorkflowContext  # noqa: E402


def make_ctx(scratchpad: Path, *, question: str = "GH724 finalize preflight") -> WorkflowContext:
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={"scratchpad_dir": str(scratchpad)},
        question=question,
        session_id="test-session-GH724",
        persona="hal",
        framework=None,
        domain=None,
    )


def build_prompt(scratchpad: Path, *, cycle: int, findings=None):
    from bytedigger_engine.workflows.phase_45_spec import _build_spec_prompt  # noqa: PLC0415

    ctx = make_ctx(scratchpad)
    prev = {"cycle": cycle}
    if findings is not None:
        prev["findings"] = findings
    result = _build_spec_prompt(ctx, prev)
    assert isinstance(result.data, dict), "expected dict data on _build_spec_prompt"
    return result.data["prompt"], result.data


def _write_prev_spec(scratchpad: Path) -> Path:
    from bytedigger_engine.workflows.phase_45_spec import SPEC_DOC_RELPATH  # noqa: PLC0415

    spec_path = scratchpad / SPEC_DOC_RELPATH
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text(
        "## Context\nPrevious cycle-1 spec body used as the cycle>=2 revise input.\n",
        encoding="utf-8",
    )
    return spec_path


STRUCTURED_FINDINGS_FENCE_TEXT = (
    "## Verdict\nREVISE\n\n"
    "## Findings (structured)\n"
    "```json\n"
    "[\n"
    '  {"id": "F1", "type": "gap", "evidence": "spec §3 AC2 missing validation",\n'
    '   "required_action": "add explicit forcing-function to AC2"}\n'
    "]\n"
    "```\n\n"
    "## Findings\n"
    "- reviewer prose sentinel line\n"
)


# ─── AC-A1 — item 5 literal present in _spec_preflight_block() ─────────────


def test_ac_a1_item5_literal_present():
    from bytedigger_engine.workflows.phase_45_spec import _spec_preflight_block  # noqa: PLC0415

    block = _spec_preflight_block()
    assert "5. §2-vs-§3 finalize coverage" in block


# ─── AC-A2 — token-consistency: all 7 finalize groups match the block ──────


def test_ac_a2_all_seven_finalize_groups_match_preflight_block():
    from bytedigger_engine.workflows.phase_45_spec import _spec_preflight_block  # noqa: PLC0415
    from bytedigger_engine.spec_coverage import _FINALIZE_GROUP_PATTERNS  # noqa: PLC0415

    block = _spec_preflight_block()
    missing = [
        group
        for group, pattern in _FINALIZE_GROUP_PATTERNS.items()
        if not pattern.search(block)
    ]
    assert missing == [], f"finalize groups not matched in preflight block: {missing}"


# ─── AC-A3 — axis literal registered + substring of preflight block ────────


def test_ac_a3_axis_registered_and_substring_of_block():
    from bytedigger_engine.workflows.phase_45_spec import SPEC_HIGH_BINDING_AXES, _spec_preflight_block  # noqa: PLC0415

    axis = "§2-vs-§3 finalize coverage"
    assert axis in SPEC_HIGH_BINDING_AXES
    assert axis in _spec_preflight_block()


# ─── AC-A4 — missing_high_binding_axes parity for the new axis ────────────


def test_ac_a4_missing_high_binding_axes_parity():
    from bytedigger_engine.workflows.phase_45_spec import missing_high_binding_axes, _spec_high_binding_block  # noqa: PLC0415

    axis = "§2-vs-§3 finalize coverage"
    assert axis in missing_high_binding_axes("")
    assert missing_high_binding_axes(_spec_high_binding_block()) == []


# ─── AC-A5 (§1ab) — cycle-1 AND cycle>=2 each carry item 5 exactly once ────


def test_ac_a5_cycle1_prompt_contains_item5_once(tmp_path):
    scratchpad = tmp_path / "scratch"
    prompt, _ = build_prompt(scratchpad, cycle=1)
    assert prompt.count("5. §2-vs-§3 finalize coverage") == 1


def test_ac_a5_cycle2_prompt_contains_item5_once(tmp_path, monkeypatch):
    monkeypatch.setenv("HAL_SPEC_DELTA_RETRY", "0")
    scratchpad = tmp_path / "scratch"
    _write_prev_spec(scratchpad)
    prompt, _ = build_prompt(scratchpad, cycle=2, findings=STRUCTURED_FINDINGS_FENCE_TEXT)

    # branch-identity guard: must land on the restricted-writer scaffold
    assert "ADDRESS EACH FINDING (by id):" in prompt
    assert prompt.count("5. §2-vs-§3 finalize coverage") == 1
