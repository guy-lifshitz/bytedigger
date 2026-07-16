"""RED tests for 1C24581F — evaluator-side satisfaction-prompt binding (SCORE ↔ structured.satisfied).

Agreement: 1C24581F (Track B Step 4)

Contract (GREEN will implement):
_build_satisfaction_prompt must insert an INVARIANT block parameterised with the runtime
threshold, positioned after "## Composite" and before "End your response with EXACTLY".

All tests that capture NEW behaviour MUST FAIL until GREEN implements the contract.
Tests for pre-existing text (AC8, AC9, AC12) pass today as regression guards.
Do NOT implement any production change here — RED-only file (1C24581F).
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).parent
ENGINE_ROOT = HERE.parent
sys.path.insert(0, str(ENGINE_ROOT))
sys.path.insert(0, str(ENGINE_ROOT / "lib"))
sys.path.insert(0, str(ENGINE_ROOT / "workflows"))

import pytest  # noqa: E402

from contracts import StepResult, WorkflowContext  # noqa: E402
from phase_6_review import (  # noqa: E402
    _build_satisfaction_prompt,
    DEFAULT_SATISFACTION_THRESHOLD,
)


# ─── fixture helpers ──────────────────────────────────────────────────────────

_SENTINEL = object()  # sentinel for "caller did not pass org_config_override"


def _make_ctx(tmp_path: Path, org_config_override: object = _SENTINEL) -> WorkflowContext:
    """Build a minimal WorkflowContext with a scratchpad directory.

    When org_config_override is provided (including None), it replaces the default dict.
    """
    scratch = tmp_path / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    if org_config_override is _SENTINEL:
        org_cfg: dict | None = {
            "scratchpad_dir": str(scratch),
            "satisfaction_threshold": DEFAULT_SATISFACTION_THRESHOLD,
        }
    elif org_config_override is None:
        org_cfg = None
    else:
        # Caller supplied a custom dict; ensure scratchpad_dir is present.
        assert isinstance(org_config_override, dict)
        org_cfg = {"scratchpad_dir": str(scratch), **org_config_override}

    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org_cfg,
        question="add feature X",
        session_id="test-1C24581F",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_prev(tmp_path: Path) -> StepResult:
    """Minimal prev StepResult that _build_satisfaction_prompt expects."""
    scratch = tmp_path / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)

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
    result = _build_satisfaction_prompt(ctx, prev)
    assert result.status == "ok", (
        f"_build_satisfaction_prompt returned error: "
        f"{getattr(result, 'error_code', None)}: {getattr(result, 'error', None)}"
    )
    return result.data["prompt"]


# ─── AC1 ──────────────────────────────────────────────────────────────────────


def test_prompt_contains_invariant_token(tmp_path: Path) -> None:
    """AC1: Built prompt contains the literal token 'INVARIANT' exactly once.

    FAILS pre-impl: current prompt has no 'INVARIANT' token.
    """
    prompt = _extract_prompt(tmp_path)
    count = prompt.count("INVARIANT")
    assert count == 1, (
        f"AC1 FAIL: expected 'INVARIANT' to appear exactly once; "
        f"found {count} times — 1C24581F invariant block not yet added"
    )


# ─── AC2 ──────────────────────────────────────────────────────────────────────


def test_prompt_contains_threshold_value(tmp_path: Path) -> None:
    """AC2: Built prompt contains the threshold value as a decimal literal ('80').

    May coincidentally pass today (80 appears in SCORE example), but AC3 will
    fail (no binding clause), confirming the feature is absent.
    """
    prompt = _extract_prompt(tmp_path)
    assert "80" in prompt, (
        "AC2 FAIL: expected '80' (default threshold) in prompt; not found — 1C24581F"
    )


# ─── AC3 ──────────────────────────────────────────────────────────────────────


def test_prompt_contains_binding_clause(tmp_path: Path) -> None:
    """AC3: Built prompt contains 'SCORE >= 80' AND 'structured.satisfied=true'.

    FAILS pre-impl: no binding clause in current prompt.
    """
    prompt = _extract_prompt(tmp_path)
    assert "SCORE >= 80" in prompt, (
        "AC3 FAIL: expected 'SCORE >= 80' binding clause in prompt; "
        "not found — 1C24581F invariant block not yet added"
    )
    assert "structured.satisfied=true" in prompt, (
        "AC3 FAIL: expected 'structured.satisfied=true' in binding clause; "
        "not found — 1C24581F invariant block not yet added"
    )


# ─── AC4 ──────────────────────────────────────────────────────────────────────


def test_prompt_contains_drift_warning(tmp_path: Path) -> None:
    """AC4: Built prompt contains case-insensitive 'drift' AND 'fail'.

    FAILS pre-impl: no drift-warning text in current prompt.
    Note: 'fail' appears today in 'VERDICT: FAIL' context — but 'drift' does not.
    """
    prompt = _extract_prompt(tmp_path)
    assert "drift" in prompt.lower(), (
        "AC4 FAIL: expected case-insensitive 'drift' in prompt; "
        "not found — 1C24581F invariant block not yet added"
    )
    assert "fail" in prompt.lower(), (
        "AC4 FAIL: expected case-insensitive 'fail' in prompt; not found — 1C24581F"
    )


# ─── AC5 ──────────────────────────────────────────────────────────────────────


def test_prompt_threshold_override_85(tmp_path: Path) -> None:
    """AC5: org_config={"satisfaction_threshold": 85} → prompt contains '85';
    standalone '80' does NOT appear inside the invariant block region.

    FAILS pre-impl: threshold override is not wired into prompt builder.
    """
    prompt = _extract_prompt(tmp_path, {"satisfaction_threshold": 85})
    assert "85" in prompt, (
        "AC5 FAIL: expected '85' in prompt when threshold=85; "
        "not found — threshold not yet wired into _build_satisfaction_prompt"
    )
    # The invariant block is between 'INVARIANT' and 'End your response with EXACTLY'.
    # Assert that within that region, standalone '80' is absent.
    inv_start = prompt.index("INVARIANT")
    end_marker = "End your response with EXACTLY"
    end_pos = prompt.index(end_marker)
    invariant_block = prompt[inv_start:end_pos]
    assert " 80" not in invariant_block and "=80" not in invariant_block, (
        "AC5 FAIL: standalone '80' appears inside invariant block when threshold=85; "
        f"invariant_block snippet: {invariant_block[:200]!r}"
    )


# ─── AC6 ──────────────────────────────────────────────────────────────────────


def test_prompt_threshold_default_80(tmp_path: Path) -> None:
    """AC6: org_config={} (no key) → prompt contains '80' (default threshold).

    May already pass today if '80' appears elsewhere in prompt; becomes a
    meaningful invariant-block guard post-impl.
    """
    prompt = _extract_prompt(tmp_path, {})
    assert "80" in prompt, (
        "AC6 FAIL: expected default threshold '80' in prompt when org_config={}; "
        "not found — 1C24581F"
    )


# ─── AC7 ──────────────────────────────────────────────────────────────────────


def test_prompt_threshold_string_coerce(tmp_path: Path) -> None:
    """AC7: org_config={"satisfaction_threshold": "85"} (string) → prompt contains '85'.

    FAILS pre-impl: threshold is not read from org_config in prompt builder.
    """
    prompt = _extract_prompt(tmp_path, {"satisfaction_threshold": "85"})
    assert "85" in prompt, (
        "AC7 FAIL: expected '85' in prompt when threshold passed as string '85'; "
        "not found — threshold coercion not yet wired into _build_satisfaction_prompt"
    )


# ─── AC8 ──────────────────────────────────────────────────────────────────────


def test_existing_rubric_preserved(tmp_path: Path) -> None:
    """AC8: existing rubric '## Composite' and 'SCORE: <0-100>' preserved verbatim.

    PASSES today — regression guard ensuring invariant block is an ADDITION.
    """
    prompt = _extract_prompt(tmp_path)
    assert "## Composite" in prompt, (
        "AC8 FAIL: expected '## Composite' rubric section in prompt; "
        "not found — regression in _build_satisfaction_prompt"
    )
    assert "SCORE: <0-100>" in prompt, (
        "AC8 FAIL: expected 'SCORE: <0-100>' in prompt; "
        "not found — regression in _build_satisfaction_prompt"
    )


# ─── AC9 ──────────────────────────────────────────────────────────────────────


def test_existing_structured_block_preserved(tmp_path: Path) -> None:
    """AC9: existing structured-block instructions preserved verbatim.

    PASSES today — regression guard.
    """
    prompt = _extract_prompt(tmp_path)
    assert "satisfaction-output (structured)" in prompt, (
        "AC9 FAIL: expected 'satisfaction-output (structured)' in prompt; "
        "not found — regression in _build_satisfaction_prompt"
    )
    assert "Set satisfied=true iff VERDICT=PASS" in prompt, (
        "AC9 FAIL: expected 'Set satisfied=true iff VERDICT=PASS' in prompt; "
        "not found — regression in _build_satisfaction_prompt"
    )


# ─── AC10 ─────────────────────────────────────────────────────────────────────


def test_invariant_position_between_rubric_and_verdict(tmp_path: Path) -> None:
    """AC10: invariant block appears AFTER '## Composite' AND BEFORE
    'End your response with EXACTLY one of'.

    FAILS pre-impl: 'INVARIANT' not present → prompt.index raises ValueError.
    """
    prompt = _extract_prompt(tmp_path)
    composite_pos = prompt.index("## Composite")
    invariant_pos = prompt.index("INVARIANT")
    end_marker_pos = prompt.index("End your response with EXACTLY")
    assert composite_pos < invariant_pos, (
        f"AC10 FAIL: 'INVARIANT' ({invariant_pos}) must appear after "
        f"'## Composite' ({composite_pos})"
    )
    assert invariant_pos < end_marker_pos, (
        f"AC10 FAIL: 'INVARIANT' ({invariant_pos}) must appear before "
        f"'End your response with EXACTLY' ({end_marker_pos})"
    )


# ─── AC11 ─────────────────────────────────────────────────────────────────────


class _CtxNoOrgConfig:
    """Minimal duck-typed ctx with NO org_config attribute at all.

    Crucially, it DOES have org_config accessible via _resolve_scratchpad by
    providing a separate _scratchpad_dir that a patched _resolve_scratchpad
    could use — but we cannot monkeypatch here without Bash.

    Instead this class is used via SimpleNamespace where we supply
    org_config={"scratchpad_dir": ...} to pass _resolve_scratchpad, but the
    class has no `org_config` attribute so that `getattr(ctx, "org_config", None)`
    is what GREEN must use.
    """

    def __init__(self, scratchpad_dir: str) -> None:
        self.tenant_id = "hal"
        self.scope = None
        self.db_path = None
        self.question = "add feature X"
        self.session_id = "test-1C24581F-missing"
        self.persona = "hal"
        self.framework = None
        self.domain = None
        # org_config intentionally absent — do NOT set self.org_config
        # Store scratchpad separately; _resolve_scratchpad(ctx) does ctx.org_config
        # which raises AttributeError today (RED) and succeeds with getattr post-GREEN.
        self._scratchpad_dir = scratchpad_dir


@pytest.mark.parametrize("case", ["none_value", "missing_attr"])
def test_prompt_ctx_org_config_none_or_missing(tmp_path: Path, case: str) -> None:
    """AC11: ctx.org_config=None OR ctx lacks org_config entirely → defaults to 80 without raising.

    For 'none_value': org_config=None is passed via a valid WorkflowContext.  The scratchpad
    path is smuggled in via a hybrid ctx so _resolve_scratchpad can succeed.
    For 'missing_attr': a custom class with NO org_config attribute (NOT MagicMock —
    MagicMock auto-fabricates attrs and would hide the bug).

    Both cases: must not raise AttributeError. After GREEN adds getattr guard, both
    sub-cases survive. Pre-GREEN the missing_attr sub-case raises AttributeError in
    _resolve_scratchpad; that is the expected RED failure for this sub-case.
    """
    scratch = tmp_path / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    spec_file = tmp_path / "spec.md"
    review_file = tmp_path / "review.md"
    fix_file = tmp_path / "fix.md"
    spec_file.write_text("# Spec\n", encoding="utf-8")
    review_file.write_text("# Review\n", encoding="utf-8")
    fix_file.write_text("# Fix\n", encoding="utf-8")

    prev = StepResult(
        status="ok",
        data={
            "spec_path": str(spec_file),
            "review_doc_path": str(review_file),
            "fix_doc_path": str(fix_file),
        },
        duration_ms=0,
        step_name="invoke_fix_llm",
    )

    if case == "none_value":
        # WorkflowContext with org_config=None.
        # _resolve_scratchpad does ctx.org_config or {} → {} → raises ValueError
        # (scratchpad_dir missing). That is acceptable — the test only guards that
        # org_config=None does NOT cause AttributeError in threshold resolution.
        ctx = WorkflowContext(
            tenant_id="hal",
            scope=None,
            db_path=None,
            org_config=None,
            question="add feature X",
            session_id="test-1C24581F-none",
            persona="hal",
            framework=None,
            domain=None,
        )
    else:
        # missing_attr: a plain SimpleNamespace with NO org_config attribute.
        # _resolve_scratchpad(ctx) does ctx.org_config → AttributeError today.
        # After GREEN adds getattr guard to _build_satisfaction_prompt AND
        # _resolve_scratchpad is also guarded (or GREEN puts threshold extraction
        # before _resolve_scratchpad), this sub-case must not raise AttributeError.
        # NOT a MagicMock — MagicMock.__getattr__ fabricates any attribute, hiding the bug.
        ctx = _CtxNoOrgConfig(scratchpad_dir=str(scratch))

    # Contract: must NOT propagate an AttributeError from org_config access in
    # the threshold-resolution code added by GREEN. ValueError/KeyError from
    # unrelated path resolution is acceptable.
    try:
        result = _build_satisfaction_prompt(ctx, prev)
    except AttributeError as exc:
        pytest.fail(
            f"AC11 FAIL [{case}]: _build_satisfaction_prompt raised AttributeError "
            f"when org_config is {case}; expected getattr guard fallback to default=80. "
            f"Error: {exc} — 1C24581F getattr guard not yet implemented"
        )
    except (ValueError, KeyError, TypeError):
        # Acceptable path-resolution / dict-access failure; org_config access was safe.
        return


# ─── AC12 ─────────────────────────────────────────────────────────────────────


def test_prompt_deterministic_across_calls(tmp_path: Path) -> None:
    """AC12: calling _build_satisfaction_prompt twice with identical inputs → byte-identical.

    PASSES today (deterministic by construction) — regression guard.
    """
    ctx = _make_ctx(tmp_path)
    prev = _make_prev(tmp_path)

    result_a = _build_satisfaction_prompt(ctx, prev)
    result_b = _build_satisfaction_prompt(ctx, prev)

    assert result_a.status == "ok", (
        f"AC12 FAIL: first call returned error: {getattr(result_a, 'error_code', None)}"
    )
    assert result_b.status == "ok", (
        f"AC12 FAIL: second call returned error: {getattr(result_b, 'error_code', None)}"
    )
    prompt_a: str = result_a.data["prompt"]
    prompt_b: str = result_b.data["prompt"]
    assert prompt_a == prompt_b, (
        "AC12 FAIL: two calls with identical inputs produced different prompt strings; "
        "nondeterministic ordering detected — 1C24581F"
    )
