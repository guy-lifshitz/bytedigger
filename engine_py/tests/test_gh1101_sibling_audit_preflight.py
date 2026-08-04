"""RED tests for GH1101 — §1a sibling-shape-audit preflight axis in phase_45_spec.

Spec: SHARED/memory/Decisions/2026-07-21_GH1101_sibling_audit_preflight_spec.md

GREEN will (a) append a numbered item 6 "§1a sibling-shape-audit" to
`_spec_preflight_block()` in workflows/phase_45_spec.py, and (b) add the
token string "§1a sibling-shape-audit" to the `SPEC_HIGH_BINDING_AXES`
tuple (growing it from 11 to 12 entries), and (c) the orchestrator raises
the byte-cap in the gh729 test (test_ac10, out of scope for this file).

On the CURRENT base neither the axis prose nor the registered token exist
yet, so most assertions below MUST fail today.

Expected RED status on current base (per orchestrator instructions):
  - AC-1 FAILS  — axis text + token absent from _spec_preflight_block()/
                  SPEC_HIGH_BINDING_AXES; len() == 11, not 13 (the cardinality
                  assertion was raised 12 -> 13 by the 4197B484 ship, which adds
                  the "MANDATORY NAMED SECTIONS" axis).
  - AC-2 FAILS  — shape-semantics substrings absent from _spec_preflight_block().
  - AC-3 FAILS  — empty-grep-is-not-clean substrings absent.
  - AC-4 FAILS  — "§1a sibling-shape-audit" not yet present in the assembled
                  cycle>=2 prompt (token not registered/inserted).
  - AC-5 FAILS  — token not yet in SPEC_HIGH_BINDING_AXES, so removing the
                  (not-yet-existing) item-6 text from the block never causes
                  missing_high_binding_axes() to report the GH1101 token.
  - AC-6 is a regression/economics SHIELD and MAY ALREADY PASS on base (the
    existing 11-axis token set is untouched by this change until GREEN lands).
  - AC-7 is an economics SHIELD and MAY ALREADY PASS on base (current block
    is well under 4096 bytes; this test intentionally does not assert the
    exact raised cap, which is orchestrator-owned in the gh729 test file).

So the RED drivers that MUST fail now are AC-1 through AC-5. AC-6/AC-7 are
shields, written per instruction, and are not required to fail today.

Per §1q (conftest-import-time singleton, 81F97F3D): sys.path is NOT touched
here — tests/conftest.py already installs the singleton (engine_py root +
workflows/ dir). All UUT symbols are imported INSIDE each test body, never
at module top level, so this file collects cleanly and fails at
assert/call time, not at collection time. Nothing here mocks/patches
`_spec_preflight_block`, `_spec_high_binding_block`, `missing_high_binding_axes`,
or `_build_spec_prompt` — every assertion exercises the real prompt-assembly
path (§1l).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from bytedigger_engine.contracts import WorkflowContext


def make_ctx(scratchpad: Path, *, question: str = "GH1101 sibling-shape-audit axis") -> WorkflowContext:
    return WorkflowContext(
        tenant_id="hal", scope=None, db_path=None,
        org_config={"scratchpad_dir": str(scratchpad)},
        question=question, session_id="test-session-GH1101",
        persona="hal", framework=None, domain=None,
    )


def _write_prev_spec(scratchpad: Path) -> Path:
    from bytedigger_engine.workflows.phase_45_spec import SPEC_DOC_RELPATH  # noqa: PLC0415

    spec_path = scratchpad / SPEC_DOC_RELPATH
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text("## Context\nPrevious cycle-1 spec body.\n", encoding="utf-8")
    return spec_path


STRUCTURED_FINDINGS = [
    {"id": "F1", "type": "gap", "evidence": "spec §3 AC2 missing validation",
     "required_action": "add explicit forcing-function to AC2"},
]


def build_prompt(scratchpad: Path, *, cycle: int, findings=None, structured_findings=None):
    from bytedigger_engine.workflows.phase_45_spec import _build_spec_prompt  # noqa: PLC0415

    ctx = make_ctx(scratchpad)
    prev = {"cycle": cycle}
    if findings is not None:
        prev["findings"] = findings
    if structured_findings is not None:
        prev["structured_findings"] = structured_findings
    result = _build_spec_prompt(ctx, prev)
    assert isinstance(result.data, dict)
    return result.data["prompt"], result.data


# Verbatim item-6 text block GREEN will insert into _spec_preflight_block().
# Used ONLY in the AC-5 mutation test to build the removal target.
ITEM6 = (
    "6. §1a sibling-shape-audit — for any changed/renamed/removed PUBLIC\n"
    "   symbol (function, constant, event name, StepResult data key, numbered\n"
    "   prompt-block axis) enumerate sibling-test coverage BY ARTIFACT/SHAPE,\n"
    "   not by identifier-grep alone: name every consumer that couples on the\n"
    "   symbol's SHAPE (dict keys, tuple/list cardinality, ordering/sequence,\n"
    "   numeric byte/growth caps, enum membership) EVEN WHEN it never spells the\n"
    "   symbol. An empty symbol grep means the audit is INAPPLICABLE (wrong\n"
    "   query) — NOT that the change is sibling-clean. Each such consumer gets a\n"
    "   covering AC or an authorized-test-edits entry.\n"
)


# ─── AC-1 ───────────────────────────────────────────────────────────────────


def test_ac1_axis_prose_and_token_registered(tmp_path):
    from bytedigger_engine.workflows.phase_45_spec import _spec_preflight_block, SPEC_HIGH_BINDING_AXES

    assert "§1a sibling-shape-audit" in _spec_preflight_block()
    assert "§1a sibling-shape-audit" in SPEC_HIGH_BINDING_AXES
    assert len(SPEC_HIGH_BINDING_AXES) == 13


# ─── AC-2 ───────────────────────────────────────────────────────────────────


def test_ac2_axis_carries_shape_semantics(tmp_path):
    from bytedigger_engine.workflows.phase_45_spec import _spec_preflight_block

    blk = _spec_preflight_block()
    assert "BY ARTIFACT/SHAPE" in blk
    assert "cardinality" in blk
    assert "ordering/sequence" in blk
    assert "numeric byte/growth caps" in blk
    assert "enum membership" in blk


# ─── AC-3 ───────────────────────────────────────────────────────────────────


def test_ac3_empty_grep_is_not_sibling_clean(tmp_path):
    from bytedigger_engine.workflows.phase_45_spec import _spec_preflight_block

    blk = _spec_preflight_block()
    assert "INAPPROPRIATE" not in blk  # sanity: guard against a near-miss token typo
    assert "INAPPLICABLE" in blk
    assert "NOT that the change is sibling-clean" in blk
    assert "empty symbol grep" in blk.lower()


# ─── AC-4 ───────────────────────────────────────────────────────────────────


def test_ac4_cycle2_prompt_carries_axis_and_full_parity(tmp_path, monkeypatch):
    monkeypatch.delenv("HAL_SPEC_DELTA_RETRY", raising=False)
    monkeypatch.delenv("HAL_SURGICAL_REVISE", raising=False)
    from bytedigger_engine.workflows.phase_45_spec import missing_high_binding_axes  # noqa: PLC0415

    scratchpad = tmp_path / "scratch"
    _write_prev_spec(scratchpad)
    prompt, data = build_prompt(
        scratchpad, cycle=2, findings="reviewer prose", structured_findings=STRUCTURED_FINDINGS,
    )

    assert data.get("surgical_revise") is True
    assert "§1a sibling-shape-audit" in prompt
    assert missing_high_binding_axes(prompt) == []
    assert data.get("high_binding_missing") == []


# ─── AC-5 ───────────────────────────────────────────────────────────────────


def test_ac5_mutation_removing_item6_reports_axis_missing(tmp_path):
    from bytedigger_engine.workflows.phase_45_spec import _spec_high_binding_block, missing_high_binding_axes

    # Non-vacuous baseline: unmutated block must already report zero missing
    # axes (else a check that always returns non-empty would also "pass" the
    # mutation assertion below for the wrong reason).
    assert missing_high_binding_axes(_spec_high_binding_block()) == []

    mutated = _spec_high_binding_block().replace(ITEM6, "")
    # This fails today because "§1a sibling-shape-audit" is not yet a
    # registered token in SPEC_HIGH_BINDING_AXES: missing_high_binding_axes()
    # can only report a token's absence if the token is in the tuple to begin
    # with. It also fails if GREEN adds the axis as prose but forgets to
    # register the token in SPEC_HIGH_BINDING_AXES — the Principle-C
    # deterministic-wire regression this ship forbids.
    assert "§1a sibling-shape-audit" in missing_high_binding_axes(mutated)


# ─── AC-6 (regression shield — may already pass on base) ──────────────────


def test_ac6_gh729_prior_11_axes_unbroken(tmp_path):
    from bytedigger_engine.workflows.phase_45_spec import SPEC_HIGH_BINDING_AXES, missing_high_binding_axes, _spec_high_binding_block

    PREV_11 = (
        "§1o consumer cite-verify",
        "§1l/§1y forcing-function + reachability",
        "§1aa helper-extraction",
        "VALIDATION-LINE GUARD",
        "SPEC-LINT PARITY RULES",
        "TOKEN-CONSISTENCY",
        "PRESENCE-TRIAD (§1y)",
        "§1v FILES-NOT-IN-SCOPE",
        "§1w OP↔AC CROSS-LINK",
        "§2-vs-§3 finalize coverage",
        "DATA-MODEL GROUND TRUTH (§1ae)",
    )

    assert set(PREV_11).issubset(set(SPEC_HIGH_BINDING_AXES))
    assert missing_high_binding_axes(_spec_high_binding_block()) == []


# ─── AC-7 (economics shield — may already pass on base) ───────────────────


def test_ac7_high_binding_block_byte_cap_sanity(tmp_path):
    from bytedigger_engine.workflows.phase_45_spec import _spec_high_binding_block

    assert len(_spec_high_binding_block().encode("utf-8")) < 4096
