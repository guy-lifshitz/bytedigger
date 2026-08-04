"""RED wiring tests for C094A1E1 W1 — checklist-gated convergence on phase_45_spec_lite.

Tests T14-T18. All MUST FAIL until GREEN agent:
  (a) creates lib/plugins/checklist_convergence/ package, AND
  (b) modifies phase_45_spec_lite.py to use the restricted prompts + verdict_parser.

Do NOT modify phase_45_spec_lite.py or create the lib package here.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent          # engine_py/tests/
ENGINE_ROOT = HERE.parent             # engine_py/

from bytedigger_engine.contracts import StepResult  # noqa: E402


# ── Shared FakeCtx ────────────────────────────────────────────────────────────


class FakeCtx:
    """Minimal WorkflowContext stand-in matching FakeCtx in test_spec_lint_wiring."""

    def __init__(self, scratchpad_dir: str, question: str = "test request") -> None:
        self.org_config: dict = {
            "scratchpad_dir": scratchpad_dir,
            "hal_root": str(Path.home() / ".claude"),
        }
        self.question = question


# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_step_result(data: dict) -> StepResult:
    return StepResult(status="ok", data=data, duration_ms=0, step_name="_test_")


# ── T14: cycle-1 reviewer prompt contains structured-findings instruction ─────


def test_t14_cycle1_review_prompt_contains_structured_findings_instruction(
    tmp_path: Path,
) -> None:
    """T14: _build_review_prompt with cycle=1 must instruct reviewer to emit
    a '## Findings (structured)' JSON block in its output.

    This will FAIL in RED because phase_45_spec_lite._review_output_schema()
    does not yet include the structured-findings section.
    """
    from bytedigger_engine.workflows.phase_45_spec_lite import _build_review_prompt  # noqa: F401

    # Prepare minimal spec file
    specs_dir = tmp_path / "specs"
    specs_dir.mkdir(parents=True)
    spec_path = specs_dir / "build-spec.md"
    spec_path.write_text(
        "## Context\nBuild something.\n\n## Acceptance Criteria\n1. AC1: done.\n"
    )

    ctx = FakeCtx(scratchpad_dir=str(tmp_path))
    prev = _make_step_result({
        "cycle": 1,
        "spec_path": str(spec_path),
        "rewrite": False,
    })

    result = _build_review_prompt(ctx, prev)
    assert result.status == "ok", f"_build_review_prompt failed: {result.error!r}"

    prompt = result.data["prompt"]
    assert "## Findings (structured)" in prompt, (
        "cycle-1 reviewer prompt must instruct reviewer to emit "
        "'## Findings (structured)' JSON block — not yet implemented (RED)"
    )


# ── T15: cycle-2 writer prompt uses restricted mode ──────────────────────────


def test_t15_cycle2_writer_prompt_is_restricted(tmp_path: Path) -> None:
    """T15: when cycle == 2 and prev cycle-1 review has a ## Findings (structured)
    JSON block on disk, _maybe_rewrite_simple_spec_prompt must return a prompt
    containing 'ONLY modify lines' (restricted writer active).

    Will FAIL in RED because the step still builds a free-rewrite prompt.
    """
    from bytedigger_engine.workflows.phase_45_spec_lite import _maybe_rewrite_simple_spec_prompt  # noqa: F401

    specs_dir = tmp_path / "specs"
    specs_dir.mkdir(parents=True)

    # Cycle-1 spec (canonical)
    spec_path = specs_dir / "build-spec.md"
    spec_path.write_text(
        "## Context\nBuild a batch-resume feature.\n\n## Acceptance Criteria\n1. AC1\n"
    )

    # Cycle-1 review with structured findings block on disk
    cycle1_review = specs_dir / "build-plan-review.md"
    cycle1_review.write_text(
        "## Verdict\nREVISE\n\n"
        "## Findings\n- some free-text issue\n\n"
        "## Findings (structured)\n"
        "```json\n"
        '[\n'
        '  {"id": "1", "type": "fabrication", "evidence": "exit codes invented",'
        ' "required_action": "move to Open Questions"},\n'
        '  {"id": "2", "type": "untestable", "evidence": "no harness",'
        ' "required_action": "pin stub strategy"}\n'
        ']\n'
        "```\n\n"
        "## Rationale\nTwo findings.\n"
    )

    ctx = FakeCtx(scratchpad_dir=str(tmp_path))
    # Simulate engine retry: prev is gate_on_review result (dict-form initial_data)
    prev = _make_step_result({
        "cycle": 2,
        "findings": "1. exit codes invented\n2. no harness",
        "spec_path": str(spec_path),
    })

    result = _maybe_rewrite_simple_spec_prompt(ctx, prev)
    assert result.status == "ok", f"step failed: {result.error!r}"

    prompt = result.data.get("prompt", "")
    assert "ONLY modify lines" in prompt, (
        "cycle-2 writer prompt must use restricted mode ('ONLY modify lines') "
        "when structured findings are present — not yet implemented (RED)"
    )


# ── T16: cycle-2 reviewer prompt uses restricted mode ────────────────────────


def test_t16_cycle2_review_prompt_is_restricted(tmp_path: Path) -> None:
    """T16: when cycle == 2, _build_review_prompt must return a prompt containing
    'may NOT introduce new findings' (restricted reviewer active).

    Will FAIL in RED because the step still uses the free-form reviewer prompt.
    """
    from bytedigger_engine.workflows.phase_45_spec_lite import _build_review_prompt  # noqa: F401

    specs_dir = tmp_path / "specs"
    specs_dir.mkdir(parents=True)

    # Cycle-2 spec
    spec_path = specs_dir / "build-spec.md"
    spec_path.write_text(
        "## Context\nBuild a batch-resume feature.\n\n"
        "## Open Questions\n1. Exit codes?\n\n"
        "## Acceptance Criteria\n1. AC1\n"
    )

    # Cycle-1 review with structured findings on disk (read by restricted reviewer builder)
    cycle1_review = specs_dir / "build-plan-review.md"
    cycle1_review.write_text(
        "## Verdict\nREVISE\n\n"
        "## Findings (structured)\n"
        "```json\n"
        '[\n'
        '  {"id": "1", "type": "fabrication", "evidence": "exit codes invented",'
        ' "required_action": "move to Open Questions"}\n'
        ']\n'
        "```\n\n"
        "## Rationale\nOne finding.\n"
    )

    ctx = FakeCtx(scratchpad_dir=str(tmp_path))
    prev = _make_step_result({
        "cycle": 2,
        "spec_path": str(spec_path),
        "rewrite": True,
    })

    result = _build_review_prompt(ctx, prev)
    assert result.status == "ok", f"_build_review_prompt failed: {result.error!r}"

    prompt = result.data["prompt"]
    assert "may NOT introduce new findings" in prompt, (
        "cycle-2 reviewer prompt must restrict to cycle-1 findings only "
        "('may NOT introduce new findings') — not yet implemented (RED)"
    )


# ── T17: cycle-2 verdict 3/3 RESOLVED + VERDICT:PASS → SHIP ─────────────────


def test_t17_cycle2_verdict_all_resolved_maps_to_ship(tmp_path: Path) -> None:
    """T17: _write_review_doc on cycle 2 with a synthetic reviewer output of
    3/3 RESOLVED + VERDICT: PASS must produce StepResult.data['verdict'] == 'SHIP'.

    Will FAIL in RED because _write_review_doc still uses _parse_verdict()
    which looks for '## Verdict\\nSHIP' header, not per-finding lines.
    """
    from bytedigger_engine.workflows.phase_45_spec_lite import _write_review_doc, VERDICT_SHIP  # noqa: F401

    specs_dir = tmp_path / "specs"
    specs_dir.mkdir(parents=True)

    review_path = specs_dir / "build-plan-review-cycle-2.md"
    spec_path = specs_dir / "build-spec.md"
    spec_path.write_text("## Context\nDone.\n")

    # Synthetic restricted-reviewer output (no ## Verdict header — only per-finding lines)
    raw_review = (
        "FINDING_1: RESOLVED - exit codes moved to Open Questions\n"
        "FINDING_2: RESOLVED - stub strategy pinned in AC5\n"
        "FINDING_3: RESOLVED - regex tightened to include counts\n"
        "VERDICT: PASS\n"
    )

    # Fake prev mimicking _invoke_review_llm output
    prev = _make_step_result({
        "raw_response": raw_review,
        "doc_path": str(review_path),
        "spec_path": str(spec_path),
        "cycle": 2,
    })

    ctx = FakeCtx(scratchpad_dir=str(tmp_path))
    result = _write_review_doc(ctx, prev)

    assert result.status == "ok", f"_write_review_doc failed: {result.error!r}"
    assert result.data["verdict"] == VERDICT_SHIP, (
        f"cycle-2 all-resolved review must map to VERDICT_SHIP='{VERDICT_SHIP}', "
        f"got {result.data['verdict']!r} — verdict_parser not wired yet (RED)"
    )


# ── T18: cycle-2 verdict 2/3 RESOLVED + VERDICT:REVISE → REVISE ──────────────


def test_t18_cycle2_verdict_partial_resolved_maps_to_revise(tmp_path: Path) -> None:
    """T18: _write_review_doc on cycle 2 with 2/3 RESOLVED + VERDICT: REVISE
    must produce StepResult.data['verdict'] == 'REVISE'.

    Will FAIL in RED for the same reason as T17.
    """
    from bytedigger_engine.workflows.phase_45_spec_lite import _write_review_doc, VERDICT_REVISE  # noqa: F401

    specs_dir = tmp_path / "specs"
    specs_dir.mkdir(parents=True)

    review_path = specs_dir / "build-plan-review-cycle-2.md"
    spec_path = specs_dir / "build-spec.md"
    spec_path.write_text("## Context\nDone.\n")

    raw_review = (
        "FINDING_1: RESOLVED - exit codes moved to Open Questions\n"
        "FINDING_2: RESOLVED - stub strategy pinned\n"
        "FINDING_3: UNRESOLVED - regex still too loose, no count validation\n"
        "VERDICT: REVISE\n"
    )

    prev = _make_step_result({
        "raw_response": raw_review,
        "doc_path": str(review_path),
        "spec_path": str(spec_path),
        "cycle": 2,
    })

    ctx = FakeCtx(scratchpad_dir=str(tmp_path))
    result = _write_review_doc(ctx, prev)

    assert result.status == "ok", f"_write_review_doc failed: {result.error!r}"
    assert result.data["verdict"] == VERDICT_REVISE, (
        f"cycle-2 partial-resolved review must map to VERDICT_REVISE='{VERDICT_REVISE}', "
        f"got {result.data['verdict']!r} — verdict_parser not wired yet (RED)"
    )
