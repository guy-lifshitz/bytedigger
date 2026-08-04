"""RED tests for EDBDCDB2 — Phase 5 RED timeout extension + anti-nested-subagent directive.

AC1: DEFAULT_RED_TIMEOUT_SEC_FEATURE = 2400 constant exists.
AC2: complexity FEATURE/COMPLEX → timeout_sec=2400 (no override).
AC3: complexity SIMPLE/TRIVIAL/missing → timeout_sec=1200 (DEFAULT_RED_TIMEOUT_SEC).
AC4: explicit red_llm_timeout_sec override always wins.
AC5: _build_red_prompt contains RED_DISALLOW_SUBAGENTS + forbid language in both cycle-1 and cycle-2.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

HERE = Path(__file__).parent

from bytedigger_engine.contracts import StepResult, WorkflowContext  # noqa: E402


# ─── helpers ──────────────────────────────────────────────────────────────────


def make_ctx(scratchpad: Path, **org_extra) -> WorkflowContext:
    org = {"scratchpad_dir": str(scratchpad), **org_extra}
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question="Add EDBDCDB2 feature",
        session_id="test-edbdcdb2",
        persona="hal",
        framework=None,
        domain=None,
    )


def make_prev(scratchpad: Path, cycle: int = 1) -> StepResult:
    return StepResult(
        status="ok",
        data={
            "prompt": "Write failing tests for the feature.",
            "log_path": str(scratchpad / "tests/build-red-output.log"),
            "spec_path": str(scratchpad / "specs/build-spec.md"),
            "cycle": cycle,
        },
        duration_ms=0,
        step_name="build_red_prompt",
    )


# ─── AC1: DEFAULT_RED_TIMEOUT_SEC_FEATURE constant ───────────────────────────


def test_ac1_feature_timeout_constant_exists():
    """AC1: module-level DEFAULT_RED_TIMEOUT_SEC_FEATURE = 2400 must exist."""
    from bytedigger_engine.workflows import phase_5_implement  # noqa: PLC0415

    assert hasattr(phase_5_implement, "DEFAULT_RED_TIMEOUT_SEC_FEATURE"), (
        "phase_5_implement must define DEFAULT_RED_TIMEOUT_SEC_FEATURE"
    )
    assert phase_5_implement.DEFAULT_RED_TIMEOUT_SEC_FEATURE == 2400, (
        f"DEFAULT_RED_TIMEOUT_SEC_FEATURE must equal 2400, got {phase_5_implement.DEFAULT_RED_TIMEOUT_SEC_FEATURE!r}"
    )


# ─── AC2: FEATURE/COMPLEX complexity → 2400s timeout ────────────────────────


def test_ac2_feature_complexity_uses_2400_timeout(tmp_path):
    """AC2: complexity=FEATURE → subprocess called with timeout_sec=2400."""
    from bytedigger_engine.workflows.phase_5_implement import _invoke_red_llm  # noqa: PLC0415

    scratchpad = tmp_path / "scratch"
    ctx = make_ctx(scratchpad, complexity="FEATURE")
    prev = make_prev(scratchpad)

    captured_timeout = []

    def fake_invoke(*, prompt, model, timeout_sec, step_name, extra_data=None, **kwargs):
        captured_timeout.append(timeout_sec)
        return StepResult(
            status="ok",
            data={"raw_response": "RED COMPLETE — 1 tests written, all failing. Files: [x.py]"},
            duration_ms=0,
            step_name=step_name,
        )

    with patch("bytedigger_engine.workflows.phase_5_implement.invoke_llm_subprocess", side_effect=fake_invoke):
        _invoke_red_llm(ctx, prev)

    assert captured_timeout, "invoke_llm_subprocess was not called"
    assert captured_timeout[0] == 2400, (
        f"Expected timeout_sec=2400 for complexity=FEATURE, got {captured_timeout[0]}"
    )


def test_ac2_complex_complexity_uses_2400_timeout(tmp_path):
    """AC2: complexity=COMPLEX → subprocess called with timeout_sec=2400."""
    from bytedigger_engine.workflows.phase_5_implement import _invoke_red_llm  # noqa: PLC0415

    scratchpad = tmp_path / "scratch"
    ctx = make_ctx(scratchpad, complexity="complex")  # lowercase — case-insensitive
    prev = make_prev(scratchpad)

    captured_timeout = []

    def fake_invoke(*, prompt, model, timeout_sec, step_name, extra_data=None, **kwargs):
        captured_timeout.append(timeout_sec)
        return StepResult(
            status="ok",
            data={"raw_response": "RED COMPLETE — 1 tests written, all failing. Files: [x.py]"},
            duration_ms=0,
            step_name=step_name,
        )

    with patch("bytedigger_engine.workflows.phase_5_implement.invoke_llm_subprocess", side_effect=fake_invoke):
        _invoke_red_llm(ctx, prev)

    assert captured_timeout, "invoke_llm_subprocess was not called"
    assert captured_timeout[0] == 2400, (
        f"Expected timeout_sec=2400 for complexity=complex, got {captured_timeout[0]}"
    )


# ─── AC3: SIMPLE/TRIVIAL/missing → 1200s timeout (unchanged) ─────────────────


def test_ac3_simple_and_trivial_complexity_keeps_1200_timeout(tmp_path):
    """AC3: complexity=SIMPLE/TRIVIAL/empty/None/missing → timeout_sec=1200.

    AC3 enumerates **five distinct input classes**: SIMPLE, TRIVIAL, empty
    string (""), explicit None, missing key. An implementation using truthy
    `if cfg.get("complexity") and ...` would silently mishandle ""; the
    canonical `.upper()-then-membership` pattern (per skip_logic.py:78)
    handles all uniformly.
    """
    from bytedigger_engine.workflows.phase_5_implement import (  # noqa: PLC0415
        DEFAULT_RED_TIMEOUT_SEC_FEATURE,
        _invoke_red_llm,
    )

    assert DEFAULT_RED_TIMEOUT_SEC_FEATURE == 2400  # new constant must exist and be 2400

    scratchpad = tmp_path / "scratch"
    captured = {}

    def fake_invoke(*, prompt, model, timeout_sec, step_name, extra_data=None, **kwargs):
        captured[step_name] = timeout_sec
        return StepResult(
            status="ok",
            data={"raw_response": "RED COMPLETE — 1 tests written, all failing. Files: [x.py]"},
            duration_ms=0,
            step_name=step_name,
        )

    # Sentinel distinguishes "key absent" from "key present with None/empty value".
    _MISSING = object()
    cases = [
        ("SIMPLE", "SIMPLE"),
        ("TRIVIAL", "TRIVIAL"),
        ("", "empty-string"),
        (None, "explicit-None"),
        (_MISSING, "missing-key"),
    ]
    for complexity, label in cases:
        captured.clear()
        if complexity is _MISSING:
            ctx = make_ctx(scratchpad)
        else:
            ctx = make_ctx(scratchpad, complexity=complexity)
        prev = make_prev(scratchpad)
        with patch("bytedigger_engine.workflows.phase_5_implement.invoke_llm_subprocess", side_effect=fake_invoke):
            _invoke_red_llm(ctx, prev)
        assert "invoke_red_llm" in captured, f"invoke_llm_subprocess not called for {label}"
        assert captured["invoke_red_llm"] == 1200, (
            f"Expected 1200 for complexity={label}, got {captured['invoke_red_llm']}"
        )


# ─── AC4: explicit override always wins ──────────────────────────────────────


def test_ac4_explicit_override_wins_over_feature_complexity(tmp_path):
    """AC4: red_llm_timeout_sec=999 + complexity=FEATURE → timeout_sec=999 (override beats new constant)."""
    # Import the new constant to verify it exists — override must beat it.
    from bytedigger_engine.workflows.phase_5_implement import (  # noqa: PLC0415
        DEFAULT_RED_TIMEOUT_SEC_FEATURE,
        _invoke_red_llm,
    )

    assert DEFAULT_RED_TIMEOUT_SEC_FEATURE == 2400  # prerequisite: new constant at 2400

    scratchpad = tmp_path / "scratch"
    ctx = make_ctx(scratchpad, complexity="FEATURE", red_llm_timeout_sec=999)
    prev = make_prev(scratchpad)

    captured_timeout = []

    def fake_invoke(*, prompt, model, timeout_sec, step_name, extra_data=None, **kwargs):
        captured_timeout.append(timeout_sec)
        return StepResult(
            status="ok",
            data={"raw_response": "RED COMPLETE — 1 tests written, all failing. Files: [x.py]"},
            duration_ms=0,
            step_name=step_name,
        )

    with patch("bytedigger_engine.workflows.phase_5_implement.invoke_llm_subprocess", side_effect=fake_invoke):
        _invoke_red_llm(ctx, prev)

    assert captured_timeout, "invoke_llm_subprocess was not called"
    assert captured_timeout[0] == 999, (
        f"Expected timeout_sec=999 (explicit override beats FEATURE complexity), got {captured_timeout[0]}"
    )


# ─── AC5: RED_DISALLOW_SUBAGENTS in prompt (cycle-1 and cycle-2) ─────────────


def test_ac5_red_prompt_contains_disallow_subagents_directive_cycle1(tmp_path):
    """AC5: cycle-1 RED prompt must contain RED_DISALLOW_SUBAGENTS + forbid language."""
    from bytedigger_engine.workflows.phase_5_implement import _build_red_prompt  # noqa: PLC0415

    scratchpad = tmp_path / "scratch"
    ctx = make_ctx(scratchpad)
    result = _build_red_prompt(ctx, None)

    assert result.status == "ok"
    prompt = result.data["prompt"]

    assert "RED_DISALLOW_SUBAGENTS" in prompt, (
        "cycle-1 RED prompt must contain 'RED_DISALLOW_SUBAGENTS'"
    )
    forbid_keywords = ["do not spawn", "no Task tool", "no nested Agent", "no nested agent"]
    found = any(kw.lower() in prompt.lower() for kw in forbid_keywords)
    assert found, (
        f"cycle-1 RED prompt must contain forbid language (one of {forbid_keywords}). "
        f"Prompt excerpt: {prompt[:500]!r}"
    )


def test_ac5_red_prompt_contains_disallow_subagents_directive_cycle2(tmp_path):
    """AC5: cycle-2 RED prompt (with findings) must also contain RED_DISALLOW_SUBAGENTS."""
    from bytedigger_engine.workflows.phase_5_implement import _build_red_prompt  # noqa: PLC0415

    scratchpad = tmp_path / "scratch"
    ctx = make_ctx(scratchpad)
    prev = {"cycle": 2, "findings": "Test X was not failing; fix assertion."}
    result = _build_red_prompt(ctx, prev)

    assert result.status == "ok"
    assert result.data["cycle"] == 2
    prompt = result.data["prompt"]

    assert "RED_DISALLOW_SUBAGENTS" in prompt, (
        "cycle-2 RED prompt must contain 'RED_DISALLOW_SUBAGENTS'"
    )
    forbid_keywords = ["do not spawn", "no Task tool", "no nested Agent", "no nested agent"]
    found = any(kw.lower() in prompt.lower() for kw in forbid_keywords)
    assert found, (
        f"cycle-2 RED prompt must contain forbid language (one of {forbid_keywords}). "
        f"Prompt excerpt: {prompt[:500]!r}"
    )
