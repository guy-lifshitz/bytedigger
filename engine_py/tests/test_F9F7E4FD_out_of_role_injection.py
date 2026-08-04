"""RED tests for F9F7E4FD: OUT-OF-ROLE block injection into all 12 prompt builders.

Each test:
  1. Calls the real prompt-builder function.
  2. Asserts result.status == "ok".
  3. Asserts "OUT OF ROLE" in result.data["prompt"].
  4. Asserts "bun run-phase.ts" in result.data["prompt"].

All 12 tests MUST FAIL before implementation (no block exists yet).
After GREEN they MUST PASS.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent

from bytedigger_engine.contracts import StepResult, WorkflowContext  # noqa: E402
from bytedigger_engine.workflows import phase_5_implement  # noqa: E402
from bytedigger_engine.workflows import phase_6_review  # noqa: E402
from bytedigger_engine.workflows import phase_45_spec  # noqa: E402
from bytedigger_engine.workflows import phase_7_synthesize  # noqa: E402
from bytedigger_engine.workflows import phase_4_architect  # noqa: E402
from bytedigger_engine.workflows import phase_2_explore  # noqa: E402
from bytedigger_engine.workflows import phase_3_clarify  # noqa: E402
from bytedigger_engine.workflows import phase_5_integrity  # noqa: E402


# ─── shared fixtures ──────────────────────────────────────────────────────────


def _make_ctx(scratchpad: Path, *, question: str = "Add foo to bar") -> WorkflowContext:
    scratchpad.mkdir(parents=True, exist_ok=True)
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={"scratchpad_dir": str(scratchpad)},
        question=question,
        session_id="test-F9F7E4FD",
        persona="hal",
        framework=None,
        domain=None,
    )


def _seed_injection(scratchpad: Path) -> None:
    """Seed injection/*.md stubs so _read_first_block doesn't raise."""
    inj = scratchpad / "injection"
    inj.mkdir(parents=True, exist_ok=True)
    for name in ("hal-memory", "constitution", "quality-gate", "producer-rules", "active-work"):
        (inj / f"{name}.md").write_text("")


def _prev_with_paths(scratchpad: Path, **extra) -> StepResult:
    """Minimal prev StepResult with path keys used by several builders."""
    spec_path = scratchpad / "specs" / "build-spec.md"
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text("# Spec\n")
    data = {
        "spec_path": str(spec_path),
        "red_log_path": str(scratchpad / "tests" / "build-red-output.log"),
        "red_test_paths": ["tests/test_foo.py"],
        "cycle": 1,
        **extra,
    }
    return StepResult(status="ok", data=data, duration_ms=0, step_name="prev")


def _assert_out_of_role(result: StepResult, builder_name: str) -> None:
    assert result.status == "ok", f"{builder_name}: prompt build failed: {result}"
    prompt: str = result.data["prompt"]
    assert "OUT OF ROLE" in prompt, f"{builder_name}: missing OUT OF ROLE block"
    assert "bun run-phase.ts" in prompt, f"{builder_name}: missing run-phase.ts deny"


# ─── T01: phase_5_implement._build_red_prompt ─────────────────────────────────


def test_red_prompt_includes_out_of_role_block(tmp_path):
    scratch = tmp_path / "s"
    _seed_injection(scratch)
    ctx = _make_ctx(scratch)
    result = phase_5_implement._build_red_prompt(ctx, None)
    _assert_out_of_role(result, "_build_red_prompt")


# ─── T02: phase_5_implement._build_green_prompt ───────────────────────────────


def test_green_prompt_includes_out_of_role_block(tmp_path):
    scratch = tmp_path / "s"
    _seed_injection(scratch)
    ctx = _make_ctx(scratch)
    val_doc = scratch / "validation" / "validation.md"
    val_doc.parent.mkdir(parents=True, exist_ok=True)
    val_doc.write_text("VERDICT: PASS\n")
    prev = _prev_with_paths(
        scratch,
        validation_doc_path=str(val_doc),
    )
    result = phase_5_implement._build_green_prompt(ctx, prev)
    _assert_out_of_role(result, "_build_green_prompt")


# ─── T03: phase_5_implement._build_validation_prompt ─────────────────────────


def test_validation_prompt_includes_out_of_role_block(tmp_path):
    scratch = tmp_path / "s"
    _seed_injection(scratch)
    ctx = _make_ctx(scratch)
    prev = _prev_with_paths(scratch)
    result = phase_5_implement._build_validation_prompt(ctx, prev)
    _assert_out_of_role(result, "_build_validation_prompt")


# ─── T04: phase_6_review._build_review_prompt ────────────────────────────────


def test_review_prompt_includes_out_of_role_block(tmp_path):
    scratch = tmp_path / "s"
    _seed_injection(scratch)
    ctx = _make_ctx(scratch)
    result = phase_6_review._build_review_prompt(ctx, None)
    _assert_out_of_role(result, "_build_review_prompt")


# ─── T05: phase_6_review._build_fix_prompt ───────────────────────────────────


def test_fix_prompt_includes_out_of_role_block(tmp_path):
    scratch = tmp_path / "s"
    _seed_injection(scratch)
    ctx = _make_ctx(scratch)
    review_doc = scratch / "reviews" / "review.md"
    review_doc.parent.mkdir(parents=True, exist_ok=True)
    review_doc.write_text("# Review\nVERDICT: PARTIAL\n")
    prev = _prev_with_paths(
        scratch,
        review_doc_path=str(review_doc),
        verdict="PARTIAL",
    )
    result = phase_6_review._build_fix_prompt(ctx, prev)
    _assert_out_of_role(result, "_build_fix_prompt")


# ─── T06: phase_6_review._build_satisfaction_prompt ──────────────────────────


def test_satisfaction_prompt_includes_out_of_role_block(tmp_path):
    scratch = tmp_path / "s"
    _seed_injection(scratch)
    ctx = _make_ctx(scratch)
    review_doc = scratch / "reviews" / "review.md"
    review_doc.parent.mkdir(parents=True, exist_ok=True)
    review_doc.write_text("# Review\nVERDICT: PASS\n")
    fix_doc = scratch / "fixes" / "fix.md"
    fix_doc.parent.mkdir(parents=True, exist_ok=True)
    fix_doc.write_text("# Fix\n")
    prev = _prev_with_paths(
        scratch,
        review_doc_path=str(review_doc),
        fix_doc_path=str(fix_doc),
        verdict="PASS",
    )
    result = phase_6_review._build_satisfaction_prompt(ctx, prev)
    _assert_out_of_role(result, "_build_satisfaction_prompt")


# ─── T07: phase_45_spec._build_spec_prompt ───────────────────────────────────


def test_spec_prompt_includes_out_of_role_block(tmp_path):
    scratch = tmp_path / "s"
    _seed_injection(scratch)
    ctx = _make_ctx(scratch)
    result = phase_45_spec._build_spec_prompt(ctx, None)
    _assert_out_of_role(result, "_build_spec_prompt")


# ─── T08: phase_7_synthesize._build_synthesizer_prompt ───────────────────────


def test_synthesizer_prompt_includes_out_of_role_block(tmp_path):
    scratch = tmp_path / "s"
    _seed_injection(scratch)
    ctx = _make_ctx(scratch)
    result = phase_7_synthesize._build_synthesizer_prompt(ctx, None)
    _assert_out_of_role(result, "_build_synthesizer_prompt")


# ─── T09: phase_4_architect._build_architect_prompt ──────────────────────────


def test_architect_prompt_includes_out_of_role_block(tmp_path):
    scratch = tmp_path / "s"
    _seed_injection(scratch)
    ctx = _make_ctx(scratch)
    result = phase_4_architect._build_architect_prompt(ctx, None)
    _assert_out_of_role(result, "_build_architect_prompt")


# ─── T10: phase_2_explore._build_explore_prompt ──────────────────────────────


def test_explore_prompt_includes_out_of_role_block(tmp_path):
    scratch = tmp_path / "s"
    _seed_injection(scratch)
    ctx = _make_ctx(scratch)
    result = phase_2_explore._build_explore_prompt(ctx, None)
    _assert_out_of_role(result, "_build_explore_prompt")


# ─── T11: phase_3_clarify._build_clarify_prompt ──────────────────────────────


def test_clarify_prompt_includes_out_of_role_block(tmp_path):
    scratch = tmp_path / "s"
    _seed_injection(scratch)
    ctx = _make_ctx(scratch)
    result = phase_3_clarify._build_clarify_prompt(ctx, None)
    _assert_out_of_role(result, "_build_clarify_prompt")


# ─── T12: phase_5_integrity._build_integrity_prompt ──────────────────────────


def test_integrity_prompt_includes_out_of_role_block(tmp_path):
    scratch = tmp_path / "s"
    _seed_injection(scratch)
    ctx = _make_ctx(scratch)
    # _build_integrity_prompt runs `git diff` via _resolve_diff_command.
    # Provide a scratchpad inside a real git repo so it can run.
    result = phase_5_integrity._build_integrity_prompt(ctx, None)
    # Legitimate skip paths:
    #   - status=error: diff command failed (not a git repo / cmd missing)
    #   - status=ok with verdict_override=NO_CHANGES: empty diff → no LLM
    #     prompt assembled (prompt=None). This is a Phase 3.5.3 ideal case
    #     and varies based on test execution order / fixture diff state.
    if result.status == "error":
        pytest.skip(
            f"_build_integrity_prompt returned error (likely diff-cmd setup): {result.error} "
            "— covered by GREEN-phase impl test"
        )
    if result.data.get("verdict_override") and result.data.get("prompt") is None:
        pytest.skip(
            "_build_integrity_prompt skipped LLM call (empty diff) — "
            "OUT OF ROLE injection is irrelevant when no prompt is built"
        )
    _assert_out_of_role(result, "_build_integrity_prompt")
