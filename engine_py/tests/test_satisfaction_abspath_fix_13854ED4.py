"""RED tests for 13854ED4 — satisfaction prompt must name absolute sat_doc_path.

Agreement: 13854ED4 · Parent SYSTEMATIC: FD2592D9 · Class: PATCH
Spec: SHARED/memory/Decisions/2026-06-12_13854ED4_satisfaction_abspath_fix_spec.md

Contract (GREEN will implement):
_build_satisfaction_prompt non-COMPLEX OUTPUT branch must replace the bare
relpath `reviews/build-satisfaction.md` with the absolute `sat_doc_path` and
add the "EXACTLY this file path" family phrasing.

AC1, AC2 FAIL today — no absolute path or EXACTLY-phrase in non-COMPLEX branch.
AC3, AC4 PASS today as regression guards and continue to pass post-GREEN.

Do NOT implement any production change here — RED-only file (13854ED4).
sys.path is managed by conftest.py at import time (§1q / 81F97F3D gate).
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

# conftest.py inserts engine root + workflows dir into sys.path at collection time.
from bytedigger_engine.contracts import StepResult, WorkflowContext  # noqa: E402
from bytedigger_engine.workflows import phase_6_review  # noqa: E402 — import module, not top-level symbol (§D1CF5FDF)


# ─── fixture helpers (mirrored from test_phase_6_satisfaction_prompt_binding_1C24581F) ──

_SENTINEL = object()


def _make_ctx(tmp_path: Path, org_config_override: object = _SENTINEL) -> WorkflowContext:
    """Build a minimal WorkflowContext with a known scratchpad directory."""
    scratch = tmp_path / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    if org_config_override is _SENTINEL:
        org_cfg: dict | None = {"scratchpad_dir": str(scratch)}
    elif org_config_override is None:
        org_cfg = None
    else:
        assert isinstance(org_config_override, dict)
        org_cfg = {"scratchpad_dir": str(scratch), **org_config_override}

    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org_cfg,
        question="add feature X",
        session_id="test-13854ED4",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_prev(tmp_path: Path) -> StepResult:
    """Minimal prev StepResult that _build_satisfaction_prompt expects."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    spec_file = tmp_path / "spec.md"
    review_file = tmp_path / "review.md"
    fix_file = tmp_path / "fix.md"
    spec_file.write_text("# Spec\n", encoding="utf-8")
    review_file.write_text("# Review\n", encoding="utf-8")
    fix_file.write_text("# Fix\n", encoding="utf-8")

    return StepResult(
        status="ok",
        data={
            "spec_path": str(spec_file),
            "review_doc_path": str(review_file),
            "fix_doc_path": str(fix_file),
        },
        duration_ms=0,
        step_name="invoke_fix_llm",
    )


def _extract_prompt(tmp_path: Path, org_config_override: object = _SENTINEL) -> str:
    """Build a prompt and return the text string, asserting no error."""
    ctx = _make_ctx(tmp_path, org_config_override)
    prev = _make_prev(tmp_path)
    result = phase_6_review._build_satisfaction_prompt(ctx, prev)
    assert result.status == "ok", (
        f"_build_satisfaction_prompt returned error: "
        f"{getattr(result, 'error_code', None)}: {getattr(result, 'error', None)}"
    )
    return result.data["prompt"]


# ─── AC1 — absolute sat_doc_path present in non-COMPLEX prompt ────────────────

def test_non_complex_prompt_contains_absolute_sat_doc_path(tmp_path: Path) -> None:
    """AC1: non-COMPLEX prompt contains the absolute sat_doc_path string.

    Compute expected = str(Path(scratchpad_dir) / "reviews/build-satisfaction.md").
    Assert it is present in the prompt AND os.path.isabs(expected) is True.

    FAILS today: current non-COMPLEX OUTPUT branch has only the bare relpath
    `reviews/build-satisfaction.md`, not the absolute path.
    """
    scratch = tmp_path / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    org_cfg = {"scratchpad_dir": str(scratch)}
    ctx = _make_ctx(tmp_path, org_cfg)
    prev = _make_prev(tmp_path)

    result = phase_6_review._build_satisfaction_prompt(ctx, prev)
    assert result.status == "ok", (
        f"AC1 FAIL: _build_satisfaction_prompt returned error: "
        f"{getattr(result, 'error_code', None)}: {getattr(result, 'error', None)}"
    )
    prompt = result.data["prompt"]

    expected = str(Path(str(scratch)) / "reviews/build-satisfaction.md")
    assert os.path.isabs(expected), (
        f"AC1 SETUP-ERROR: expected path is not absolute: {expected!r}"
    )
    assert expected in prompt, (
        f"AC1 FAIL: absolute sat_doc_path {expected!r} not found in non-COMPLEX prompt. "
        "GREEN must replace bare relpath with absolute sat_doc_path. "
        "(13854ED4 fix not yet implemented)"
    )


# ─── AC2 — "EXACTLY this file path" phrase present in non-COMPLEX prompt ──────

def test_non_complex_prompt_contains_exactly_this_file_path(tmp_path: Path) -> None:
    """AC2: non-COMPLEX prompt contains the family phrasing "EXACTLY this file path".

    FAILS today: no such phrase appears in the non-COMPLEX OUTPUT branch.
    The only "EXACTLY" in the current prompt is "EXACTLY one of: VERDICT: PASS/FAIL"
    which does not match the required phrase.
    """
    prompt = _extract_prompt(tmp_path)
    assert "EXACTLY this file path" in prompt, (
        "AC2 FAIL: phrase 'EXACTLY this file path' not found in non-COMPLEX prompt. "
        "GREEN must add this family phrasing to the OUTPUT directive. "
        "(13854ED4 fix not yet implemented)"
    )


# ─── AC3 — regression guard: required landmarks still present in non-COMPLEX ──

def test_non_complex_prompt_required_landmarks_preserved(tmp_path: Path) -> None:
    """AC3: non-COMPLEX prompt still contains all 1A07C325/1C24581F contract landmarks.

    PASSES today (regression guard) and must continue to pass post-GREEN.
    Tokens checked: OUTPUT —, Do NOT echo, ## Composite, SCORE: <0-100>,
    ## satisfaction-output.
    """
    prompt = _extract_prompt(tmp_path)

    assert "OUTPUT —" in prompt, (
        "AC3 FAIL: 'OUTPUT —' landmark missing from non-COMPLEX prompt — regression"
    )
    assert "Do NOT echo" in prompt, (
        "AC3 FAIL: 'Do NOT echo' missing from non-COMPLEX prompt — regression"
    )
    assert "## Composite" in prompt, (
        "AC3 FAIL: '## Composite' section missing from non-COMPLEX prompt — regression"
    )
    assert "SCORE: <0-100>" in prompt, (
        "AC3 FAIL: 'SCORE: <0-100>' missing from non-COMPLEX prompt — regression"
    )
    assert "## satisfaction-output" in prompt, (
        "AC3 FAIL: '## satisfaction-output' missing from non-COMPLEX prompt — regression"
    )


# ─── AC4 — COMPLEX branch unchanged: echo directive present, no EXACTLY phrase ─

def test_complex_prompt_unchanged(tmp_path: Path) -> None:
    """AC4: COMPLEX prompt still has echo directive and does NOT gain "EXACTLY this file path".

    PASSES today (regression guard) and must continue to pass post-GREEN.
    COMPLEX branch is out of scope for 13854ED4 (deferred to slice-6d).
    """
    prompt = _extract_prompt(tmp_path, {"complexity": "COMPLEX"})

    assert "your response IS" in prompt, (
        "AC4 FAIL: COMPLEX echo directive 'your response IS' missing — regression"
    )
    assert "EXACTLY this file path" not in prompt, (
        "AC4 FAIL: 'EXACTLY this file path' must NOT appear in COMPLEX prompt "
        "— COMPLEX branch is out of scope for 13854ED4 (slice-6d)"
    )
