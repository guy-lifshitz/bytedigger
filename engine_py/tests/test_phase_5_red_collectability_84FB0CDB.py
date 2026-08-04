"""RED tests for 84FB0CDB — Wire COLLECTABILITY (D1CF5FDF) discipline into _build_red_prompt.

AC1: cycle-1 prompt contains `COLLECTABILITY (D1CF5FDF)` label.
AC2: cycle-1 prompt contains remedy tokens: defer, inside the test function, collection.
AC3: cycle-1 prompt contains terminal failure code `E_RED_COLLECT_FAILED`.
AC4: cycle-2 prompt STILL contains `COLLECTABILITY (D1CF5FDF)` (rule is in base RULES, not REVISION-only).

All 4 tests FAIL pre-GREEN: the bullet has not been added to the RULES block yet.
"""
from __future__ import annotations

from pathlib import Path

from bytedigger_engine.contracts import StepResult, WorkflowContext


# ─── helpers (mirrored from test_phase_5_implement_EDBDCDB2.py) ──────────────


def make_ctx(scratchpad: Path, **org_extra) -> WorkflowContext:
    org = {"scratchpad_dir": str(scratchpad), **org_extra}
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question="Add RED collectability discipline to _build_red_prompt",
        session_id="test-84fb0cdb",
        persona="hal",
        framework=None,
        domain=None,
    )


# ─── AC1: COLLECTABILITY label present in cycle-1 prompt ─────────────────────


def test_collectability_rule_cycle1(tmp_path):
    """AC1: cycle-1 _build_red_prompt must contain 'COLLECTABILITY (D1CF5FDF)'."""
    from bytedigger_engine.workflows.phase_5_implement import _build_red_prompt  # noqa: PLC0415

    scratchpad = tmp_path / "scratch"
    ctx = make_ctx(scratchpad)
    prev = {"cycle": 1}
    result = _build_red_prompt(ctx, prev)

    assert result.status == "ok"
    assert "COLLECTABILITY (D1CF5FDF)" in result.data["prompt"], (
        "cycle-1 RED prompt must contain 'COLLECTABILITY (D1CF5FDF)' rule bullet. "
        "GREEN must add this bullet to the base RULES block in _build_red_prompt."
    )


# ─── AC2: remedy tokens present (case-insensitive) ───────────────────────────


def test_collectability_rule_remedy(tmp_path):
    """AC2: cycle-1 prompt contains remedy tokens: 'defer', 'inside the test function', 'collection'."""
    from bytedigger_engine.workflows.phase_5_implement import _build_red_prompt  # noqa: PLC0415

    scratchpad = tmp_path / "scratch"
    ctx = make_ctx(scratchpad)
    prev = {"cycle": 1}
    result = _build_red_prompt(ctx, prev)

    assert result.status == "ok"
    prompt_lower = result.data["prompt"].lower()

    assert "defer" in prompt_lower, (
        "COLLECTABILITY rule must contain 'defer' (the remedy action)."
    )
    assert "inside the test function" in prompt_lower, (
        "COLLECTABILITY rule must contain 'inside the test function' (where to defer the import)."
    )
    assert "collection" in prompt_lower, (
        "COLLECTABILITY rule must mention 'collection' (the failure mode: pytest COLLECTION)."
    )


# ─── AC3: terminal error code named in the rule ──────────────────────────────


def test_collectability_rule_names_error_code(tmp_path):
    """AC3: cycle-1 prompt contains 'E_RED_COLLECT_FAILED' (the terminal failure code)."""
    from bytedigger_engine.workflows.phase_5_implement import _build_red_prompt  # noqa: PLC0415

    scratchpad = tmp_path / "scratch"
    ctx = make_ctx(scratchpad)
    prev = {"cycle": 1}
    result = _build_red_prompt(ctx, prev)

    assert result.status == "ok"
    assert "E_RED_COLLECT_FAILED" in result.data["prompt"], (
        "COLLECTABILITY rule must name 'E_RED_COLLECT_FAILED' so the RED-writer "
        "understands the terminal consequence of a module-top import of a missing symbol."
    )


# ─── AC4: rule present in cycle-2 (base RULES, not REVISION-gated) ───────────


def test_collectability_rule_cycle2(tmp_path):
    """AC4: cycle-2 prompt STILL contains 'COLLECTABILITY (D1CF5FDF)' — rule is in base RULES."""
    from bytedigger_engine.workflows.phase_5_implement import _build_red_prompt  # noqa: PLC0415

    scratchpad = tmp_path / "scratch"
    ctx = make_ctx(scratchpad)
    prev = {"cycle": 2, "findings": "some validator finding"}
    result = _build_red_prompt(ctx, prev)

    assert result.status == "ok"
    assert result.data["cycle"] == 2
    assert "COLLECTABILITY (D1CF5FDF)" in result.data["prompt"], (
        "cycle-2 RED prompt must STILL contain 'COLLECTABILITY (D1CF5FDF)'. "
        "The rule must live in the base RULES block, not gated behind the REVISION block. "
        "A REVISION-only placement would silently skip this discipline on cycle-1 RED."
    )
