"""RED tests for Step 6 of the 95D3E5F6 sprint — W1 port into phase_45_spec.py.

Sibling phase_45_spec_lite.py already wires the checklist_convergence (W1)
pattern (C094A1E1, commit 702ea109). Step 6 ports the same pattern into
phase_45_spec.py — the FEATURE/COMPLEX path. These tests pin the contract:

  1. Cycle-1 reviewer output schema declares `## Findings (structured)` JSON block.
  2. Module imports the four W1 helpers from plugins.checklist_convergence.
  3. Cycle-2 spec writer uses the restricted writer prompt when prev cycle-1
     review carries a structured findings block — falls back to the legacy
     `## REVISION` free-rewrite block when not.
  4. Cycle-2 reviewer prompt uses the restricted reviewer when prev cycle-1
     review has structured findings.
  5. write_review_doc on cycle-2 prefers `parse_per_finding_verdicts(raw)`;
     falls back to `_parse_verdict` when no FINDING_ lines are present.

Tests 1, 2, 3, 5, 6, 7 are EXPECTED TO FAIL today (RED). Tests 4 and 8 are
backward-compat guards that should already PASS today (and still PASS after
the GREEN ship).

Do NOT modify phase_45_spec.py, phase_45_spec_lite.py, or the
plugins/checklist_convergence/ package from this file.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent           # engine_py/tests/
ENGINE_ROOT = HERE.parent              # engine_py/

from bytedigger_engine.contracts import StepResult  # noqa: E402


# ── Shared FakeCtx (mirrors test_phase_45_spec_lite_C094A1E1) ─────────────────


class FakeCtx:
    """Minimal WorkflowContext stand-in."""

    def __init__(self, scratchpad_dir: str, question: str = "test request") -> None:
        self.org_config: dict = {
            "scratchpad_dir": scratchpad_dir,
            "hal_root": str(Path.home() / ".claude"),
        }
        self.question = question


def _make_step_result(data: dict) -> StepResult:
    return StepResult(status="ok", data=data, duration_ms=0, step_name="_test_")


# Canonical structured-findings markdown block — used in several fixtures.
_STRUCTURED_REVIEW_TEXT = (
    "## Verdict\nREVISE\n\n"
    "## Concerns Checked\n- exit codes invented\n- no harness\n\n"
    "## Findings\n- some free-text issue\n\n"
    "## Findings (structured)\n"
    "```json\n"
    "[\n"
    '  {"id": "1", "type": "fabrication", "evidence": "exit codes invented",'
    ' "required_action": "move to Open Questions"},\n'
    '  {"id": "2", "type": "untestable", "evidence": "no harness",'
    ' "required_action": "pin stub strategy"}\n'
    "]\n"
    "```\n\n"
    "## Rationale\nTwo findings.\n"
)


# ── 1. Reviewer output schema declares structured-findings block ─────────────


def test_review_output_schema_includes_structured_findings_block() -> None:
    """RED: _review_output_schema() must instruct the reviewer to emit
    a `## Findings (structured)` JSON block with the W1-shape keys
    (id, type, evidence, required_action).

    Today the schema only lists ## Verdict / ## Concerns Checked / ## Findings
    / ## Rationale — no structured block. Sibling phase_45_spec_lite already
    has it via the W1 lib; this test pins that the FEATURE/COMPLEX path mirrors.
    """
    from bytedigger_engine.workflows.phase_45_spec import _review_output_schema  # noqa: F401

    schema = _review_output_schema()

    assert "## Findings (structured)" in schema, (
        "FEATURE/COMPLEX reviewer schema must instruct the reviewer to emit "
        "'## Findings (structured)' JSON block — RED until W1 port lands"
    )
    # JSON template keys — match the W1 schema used in checklist_convergence.
    for key in ("id", "type", "evidence", "required_action"):
        assert key in schema, (
            f"structured-findings JSON template must reference key {key!r} — "
            "missing today (RED)"
        )


# ── 2. Module binds the four checklist_convergence symbols ───────────────────


def test_imports_checklist_convergence_symbols() -> None:
    """RED: phase_45_spec module must bind the four W1 helpers from
    plugins.checklist_convergence (mirrors phase_45_spec_lite.py:88-94).
    """
    from bytedigger_engine.workflows import phase_45_spec  # noqa: F401

    expected = (
        "_restricted_writer_prompt",
        "_restricted_reviewer_prompt",
        "extract_structured_findings",
        "parse_per_finding_verdicts",
    )
    missing: list[str] = []
    for name in expected:
        if not hasattr(phase_45_spec, name):
            missing.append(name)
            continue
        obj = getattr(phase_45_spec, name)
        if not callable(obj):
            missing.append(f"{name} (not callable)")

    assert not missing, (
        f"phase_45_spec must import checklist_convergence helpers — missing: "
        f"{missing} (RED until Step 6 GREEN ship)"
    )


# ── 3. Cycle-2 writer prompt uses restricted mode when structured findings ───


def test_build_spec_prompt_cycle2_uses_restricted_writer_when_structured_findings_present(
    tmp_path: Path, monkeypatch,
) -> None:
    """RED: when cycle == 2 and prev cycle-1 review on disk has a
    `## Findings (structured)` JSON block, _build_spec_prompt must build
    a restricted writer prompt (markers `FINDING_<n>:` from
    plugins.checklist_convergence.restricted_writer_prompt) and skip the
    legacy `## REVISION (cycle 2 — address reviewer findings)` header.
    """
    # GH592: pin surgical OFF — this test covers the legacy restricted-writer lane
    monkeypatch.setenv("HAL_SURGICAL_REVISE", "0")
    from bytedigger_engine.workflows.phase_45_spec import _build_spec_prompt  # noqa: F401

    # Mirror the on-disk layout that phase_45_spec produces during a cycle-1 run.
    specs_dir = tmp_path / "specs"
    specs_dir.mkdir(parents=True)
    spec_path = specs_dir / "build-spec.md"
    spec_path.write_text(
        "## Context\nBuild a feature.\n\n"
        "## Acceptance Criteria\n1. AC1: do the thing.\n"
    )

    # Prev cycle-1 review with the W1 structured-findings JSON block.
    cycle1_review = specs_dir / "build-plan-review.md"
    cycle1_review.write_text(_STRUCTURED_REVIEW_TEXT)

    # Architecture doc (referenced by build_spec_prompt; presence harmless).
    arch_dir = tmp_path / "architecture"
    arch_dir.mkdir(parents=True)
    (arch_dir / "architecture.md").write_text("## Architecture\nstub\n")

    ctx = FakeCtx(scratchpad_dir=str(tmp_path))
    # _build_spec_prompt receives engine retry initial_data as a dict for cycle ≥ 2.
    prev: dict = {
        "cycle": 2,
        "findings": _STRUCTURED_REVIEW_TEXT,  # raw review text incl. structured block
    }

    result = _build_spec_prompt(ctx, prev)
    assert result.status == "ok", f"_build_spec_prompt failed: {result.error!r}"

    prompt = result.data["prompt"]
    assert "FINDING_1:" in prompt, (
        "cycle-2 writer prompt must use restricted-mode markers (FINDING_<n>:) "
        "when prev review has structured findings — RED until W1 port lands"
    )
    assert "## REVISION (cycle 2 — address reviewer findings)" not in prompt, (
        "cycle-2 writer prompt must NOT also append the legacy free-rewrite "
        "REVISION header when restricted writer is active (RED)"
    )


# ── 4. Cycle-2 writer falls back to legacy free-rewrite (backward-compat) ────


def test_build_spec_prompt_cycle2_falls_back_to_free_rewrite_without_structured_block(
    tmp_path: Path,
) -> None:
    """GREEN already: when prev review has NO structured findings JSON block,
    _build_spec_prompt(cycle=2) must keep the existing legacy `## REVISION`
    block (backward-compat). This guard must hold today AND after Step 6.
    """
    from bytedigger_engine.workflows.phase_45_spec import _build_spec_prompt  # noqa: F401

    specs_dir = tmp_path / "specs"
    specs_dir.mkdir(parents=True)

    # Prev review with NO structured block (free-form findings only).
    cycle1_review = specs_dir / "build-plan-review.md"
    cycle1_review.write_text(
        "## Verdict\nREVISE\n\n"
        "## Findings\n- some free-text issue\n\n"
        "## Rationale\nNo JSON block here.\n"
    )

    arch_dir = tmp_path / "architecture"
    arch_dir.mkdir(parents=True)
    (arch_dir / "architecture.md").write_text("## Architecture\nstub\n")

    ctx = FakeCtx(scratchpad_dir=str(tmp_path))
    prev: dict = {
        "cycle": 2,
        "findings": "1. exit codes invented\n2. no harness",
    }

    result = _build_spec_prompt(ctx, prev)
    assert result.status == "ok", f"_build_spec_prompt failed: {result.error!r}"

    prompt = result.data["prompt"]
    assert "## REVISION (cycle 2 — address reviewer findings)" in prompt, (
        "without structured findings, cycle-2 writer prompt must still use the "
        "legacy free-rewrite REVISION header — backward-compat guard"
    )


# ── 5. Cycle-2 reviewer uses restricted reviewer when prev review structured ─


def test_build_review_prompt_cycle2_uses_restricted_reviewer_when_prev_review_has_structured_findings(
    tmp_path: Path,
) -> None:
    """RED: cycle-2 _build_review_prompt must produce a restricted reviewer
    prompt (asks per-finding `FINDING_<id>: RESOLVED|UNRESOLVED` lines) when
    the prev cycle-1 review on disk carries a structured findings block.

    Today _build_review_prompt is free-form regardless of cycle.
    """
    from bytedigger_engine.workflows.phase_45_spec import _build_review_prompt  # noqa: F401

    specs_dir = tmp_path / "specs"
    specs_dir.mkdir(parents=True)

    # Cycle-2 spec to review (the one a cycle-2 writer just produced).
    spec_path = specs_dir / "build-spec-cycle-2.md"
    spec_path.write_text(
        "## Context\nFeature with revisions.\n\n"
        "## Open Questions\n1. Exit codes?\n\n"
        "## Acceptance Criteria\n1. AC1\n"
    )

    # Prev cycle-1 review on disk with structured findings (read by restricted reviewer builder).
    cycle1_review = specs_dir / "build-plan-review.md"
    cycle1_review.write_text(_STRUCTURED_REVIEW_TEXT)

    ctx = FakeCtx(scratchpad_dir=str(tmp_path))
    prev = _make_step_result({
        "cycle": 2,
        "spec_path": str(spec_path),
    })

    result = _build_review_prompt(ctx, prev)
    assert result.status == "ok", f"_build_review_prompt failed: {result.error!r}"

    prompt = result.data["prompt"]
    # Restricted reviewer asks for per-finding RESOLVED|UNRESOLVED lines.
    for token in ("FINDING_", "RESOLVED", "UNRESOLVED"):
        assert token in prompt, (
            f"cycle-2 reviewer prompt must contain {token!r} (per-finding "
            "checklist verdict format) — RED until W1 port lands"
        )


# ── 6. write_review_doc cycle-2 maps PASS+all-RESOLVED → VERDICT_SHIP ────────


def test_write_review_doc_cycle2_uses_per_finding_verdicts_when_lines_present(
    tmp_path: Path,
) -> None:
    """RED: cycle-2 _write_review_doc must call parse_per_finding_verdicts(raw)
    and, when final_verdict != UNPARSED, map PASS → VERDICT_SHIP and include
    per_finding/n_resolved/n_total in StepResult.data.
    """
    from bytedigger_engine.workflows.phase_45_spec import VERDICT_SHIP, _write_review_doc  # noqa: F401

    specs_dir = tmp_path / "specs"
    specs_dir.mkdir(parents=True)
    review_path = specs_dir / "build-plan-review-cycle-2.md"
    spec_path = specs_dir / "build-spec-cycle-2.md"
    spec_path.write_text("## Context\nDone.\n")

    raw = (
        "FINDING_1: RESOLVED - exit codes moved to Open Questions\n"
        "FINDING_2: RESOLVED - stub strategy pinned in AC5\n"
        "VERDICT: PASS\n"
    )

    prev = _make_step_result({
        "raw_response": raw,
        "doc_path": str(review_path),
        "spec_path": str(spec_path),
        "cycle": 2,
    })

    ctx = FakeCtx(scratchpad_dir=str(tmp_path))
    result = _write_review_doc(ctx, prev)

    assert result.status == "ok", f"_write_review_doc failed: {result.error!r}"
    assert result.data["verdict"] == VERDICT_SHIP, (
        f"all-RESOLVED + VERDICT:PASS must map to {VERDICT_SHIP!r}, got "
        f"{result.data['verdict']!r} — RED until W1 verdict_parser is wired"
    )
    for key in ("per_finding", "n_resolved", "n_total"):
        assert key in result.data, (
            f"write_review_doc cycle-2 must include {key!r} in StepResult.data "
            "when per-finding verdicts are parsed (RED)"
        )


# ── 7. write_review_doc cycle-2 PASS-with-UNRESOLVED → coerced to REVISE ─────


def test_write_review_doc_cycle2_per_finding_pass_with_unresolved_coerced_to_revise(
    tmp_path: Path,
) -> None:
    """RED: parse_per_finding_verdicts already coerces VERDICT:PASS down to
    REVISE when ANY finding is UNRESOLVED. _write_review_doc must surface
    that as VERDICT_REVISE (sanity guard against reviewer-fabricated PASS).
    """
    from bytedigger_engine.workflows.phase_45_spec import VERDICT_REVISE, _write_review_doc  # noqa: F401

    specs_dir = tmp_path / "specs"
    specs_dir.mkdir(parents=True)
    review_path = specs_dir / "build-plan-review-cycle-2.md"
    spec_path = specs_dir / "build-spec-cycle-2.md"
    spec_path.write_text("## Context\nDone.\n")

    raw = (
        "FINDING_1: RESOLVED - exit codes moved to Open Questions\n"
        "FINDING_2: UNRESOLVED - regex still too loose, no count validation\n"
        "VERDICT: PASS\n"
    )

    prev = _make_step_result({
        "raw_response": raw,
        "doc_path": str(review_path),
        "spec_path": str(spec_path),
        "cycle": 2,
    })

    ctx = FakeCtx(scratchpad_dir=str(tmp_path))
    result = _write_review_doc(ctx, prev)

    assert result.status == "ok", f"_write_review_doc failed: {result.error!r}"
    assert result.data["verdict"] == VERDICT_REVISE, (
        f"PASS with any UNRESOLVED finding must coerce to {VERDICT_REVISE!r}, "
        f"got {result.data['verdict']!r} — RED until W1 verdict_parser is wired"
    )


# ── 8. write_review_doc cycle-2 falls back to legacy parser (backward-compat)─


def test_write_review_doc_cycle2_falls_back_to_legacy_parse_without_per_finding_lines(
    tmp_path: Path,
) -> None:
    """GREEN already: when reviewer raw output has NO FINDING_ lines (free-form
    cycle-2, e.g. when cycle-1 produced no structured findings), cycle-2
    write_review_doc must fall back to the existing `## Verdict` header parser
    (`_parse_verdict`). VERDICT_SHIP must come through.

    Note: today phase_45_spec.write_review_doc *always* uses _parse_verdict,
    so this guard passes today AND still passes after Step 6 (which adds
    a per-finding-first branch with a legacy fallback, mirroring lite).
    """
    from bytedigger_engine.workflows.phase_45_spec import VERDICT_SHIP, _write_review_doc  # noqa: F401

    specs_dir = tmp_path / "specs"
    specs_dir.mkdir(parents=True)
    review_path = specs_dir / "build-plan-review-cycle-2.md"
    spec_path = specs_dir / "build-spec-cycle-2.md"
    spec_path.write_text("## Context\nDone.\n")

    # Free-form reviewer output — only `## Verdict\nSHIP`, no FINDING_ lines.
    raw = "## Verdict\nSHIP\n\n## Rationale\nLooks good.\n"

    prev = _make_step_result({
        "raw_response": raw,
        "doc_path": str(review_path),
        "spec_path": str(spec_path),
        "cycle": 2,
    })

    ctx = FakeCtx(scratchpad_dir=str(tmp_path))
    result = _write_review_doc(ctx, prev)

    assert result.status == "ok", f"_write_review_doc failed: {result.error!r}"
    assert result.data["verdict"] == VERDICT_SHIP, (
        f"legacy ## Verdict\\nSHIP without FINDING_ lines must map to "
        f"{VERDICT_SHIP!r} (backward-compat), got {result.data['verdict']!r}"
    )
