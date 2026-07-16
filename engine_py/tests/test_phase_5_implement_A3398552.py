"""RED tests for A3398552 — per-tier model pinning in phase_5_implement.py.

AC1: complexity=SIMPLE  → RED step resolved command contains haiku.
AC2: complexity=SIMPLE  → GREEN step resolved command contains haiku.
AC3: complexity=FEATURE → RED + GREEN stay Sonnet (regression guard, must PASS today).
AC4: complexity=SIMPLE + red_llm_command override → override wins (must PASS today).

Tests AC1 + AC2 FAIL today (resolved command is sonnet, not haiku).
Tests AC3 + AC4 PASS today (regression guards).
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "workflows"))

from contracts import StepResult, WorkflowContext  # noqa: E402


# ─── helpers ──────────────────────────────────────────────────────────────────


def make_ctx(scratchpad: Path, **org_extra) -> WorkflowContext:
    org = {"scratchpad_dir": str(scratchpad), **org_extra}
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question="Add A3398552 feature",
        session_id="test-a3398552",
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


def make_green_prev(scratchpad: Path) -> StepResult:
    return StepResult(
        status="ok",
        data={
            "prompt": "Implement the feature to make tests pass.",
            "log_path": str(scratchpad / "tests/build-green-output.log"),
            "spec_path": str(scratchpad / "specs/build-spec.md"),
            "red_log_path": str(scratchpad / "tests/build-red-output.log"),
            "validation_doc_path": str(scratchpad / "reviews/build-opus-validation.md"),
            "verdict": "PASS",
        },
        duration_ms=0,
        step_name="gate_on_validation",
    )


def ok_stub(step_name: str) -> StepResult:
    return StepResult(
        status="ok",
        data={"raw_response": "RED COMPLETE — 1 tests written, all failing. Files: [x.py]"},
        duration_ms=0,
        step_name=step_name,
    )


# ─── AC1: SIMPLE tier pins RED to Haiku ───────────────────────────────────────


def test_simple_tier_pins_red_to_haiku(tmp_path):
    """AC1: complexity=SIMPLE → _invoke_red_llm must call invoke_llm_subprocess with haiku model.

    25e75663 migration (R2): fake_invoke now receives model= kwarg, not command= list.
    """
    from phase_5_implement import _invoke_red_llm  # noqa: PLC0415

    scratchpad = tmp_path / "scratch"
    ctx = make_ctx(scratchpad, complexity="SIMPLE")
    prev = make_prev(scratchpad)

    captured_models = []

    def fake_invoke(*, prompt, model, timeout_sec, step_name, extra_data=None, **kwargs):
        captured_models.append(model)
        return ok_stub(step_name)

    with patch("phase_5_implement.invoke_llm_subprocess", side_effect=fake_invoke):
        _invoke_red_llm(ctx, prev)

    assert captured_models, "invoke_llm_subprocess was not called"
    model_value = captured_models[0]
    assert "haiku" in model_value.lower(), (
        f"Expected model containing 'haiku' for complexity=SIMPLE, got model={model_value!r}."
    )


# ─── AC2: SIMPLE tier pins GREEN to Haiku ─────────────────────────────────────


def test_simple_tier_pins_green_to_haiku(tmp_path):
    """AC2: complexity=SIMPLE → _invoke_green_llm must call invoke_llm_subprocess with haiku model.

    25e75663 migration (R2): fake_invoke now receives model= kwarg, not command= list.
    """
    from phase_5_implement import _invoke_green_llm  # noqa: PLC0415

    scratchpad = tmp_path / "scratch"
    ctx = make_ctx(scratchpad, complexity="SIMPLE")
    prev = make_green_prev(scratchpad)

    captured_models = []

    def fake_invoke(*, prompt, model, timeout_sec, step_name, extra_data=None, **kwargs):
        captured_models.append(model)
        return StepResult(
            status="ok",
            data={"raw_response": "GREEN COMPLETE — all 1 tests passing. Files modified: [x.py]"},
            duration_ms=0,
            step_name=step_name,
        )

    with patch("phase_5_implement.invoke_llm_subprocess", side_effect=fake_invoke):
        _invoke_green_llm(ctx, prev)

    assert captured_models, "invoke_llm_subprocess was not called"
    model_value = captured_models[0]
    assert "haiku" in model_value.lower(), (
        f"Expected model containing 'haiku' for complexity=SIMPLE (GREEN), got model={model_value!r}."
    )


# ─── AC3: FEATURE tier keeps Sonnet (regression guard) ────────────────────────


def test_feature_tier_keeps_sonnet(tmp_path):
    """AC3: complexity=FEATURE → RED + GREEN both stay Sonnet (status quo regression guard).

    25e75663 migration (R2): fake_invoke now receives model= kwarg, not command= list.
    """
    from phase_5_implement import _invoke_green_llm, _invoke_red_llm  # noqa: PLC0415

    scratchpad = tmp_path / "scratch"

    # RED step
    ctx_red = make_ctx(scratchpad, complexity="FEATURE")
    prev_red = make_prev(scratchpad)
    captured_red = []

    def fake_red(*, prompt, model, timeout_sec, step_name, extra_data=None, **kwargs):
        captured_red.append(model)
        return ok_stub(step_name)

    with patch("phase_5_implement.invoke_llm_subprocess", side_effect=fake_red):
        _invoke_red_llm(ctx_red, prev_red)

    assert captured_red, "RED invoke_llm_subprocess was not called"
    red_model = captured_red[0]
    assert "sonnet" in red_model.lower(), (
        f"REGRESSION: complexity=FEATURE RED must stay sonnet, got {red_model!r}"
    )

    # GREEN step
    ctx_green = make_ctx(scratchpad, complexity="FEATURE")
    prev_green = make_green_prev(scratchpad)
    captured_green = []

    def fake_green(*, prompt, model, timeout_sec, step_name, extra_data=None, **kwargs):
        captured_green.append(model)
        return StepResult(
            status="ok",
            data={"raw_response": "GREEN COMPLETE — all 1 tests passing. Files modified: [x.py]"},
            duration_ms=0,
            step_name=step_name,
        )

    with patch("phase_5_implement.invoke_llm_subprocess", side_effect=fake_green):
        _invoke_green_llm(ctx_green, prev_green)

    assert captured_green, "GREEN invoke_llm_subprocess was not called"
    green_model = captured_green[0]
    assert "sonnet" in green_model.lower(), (
        f"REGRESSION: complexity=FEATURE GREEN must stay sonnet, got {green_model!r}"
    )


# ─── AC4: per-step override beats tier (regression guard) ────────────────────


def test_explicit_red_command_overrides_tier(tmp_path):
    """AC4: complexity=SIMPLE + red_model override → override wins.

    Per-step override > tier default.
    25e75663 migration (R4): red_llm_command → red_model (model string).
    """
    from phase_5_implement import _invoke_red_llm  # noqa: PLC0415

    scratchpad = tmp_path / "scratch"
    ctx = make_ctx(
        scratchpad,
        complexity="SIMPLE",
        red_model="custom-model-override",
    )
    prev = make_prev(scratchpad)

    captured_models = []

    def fake_invoke(*, prompt, model, timeout_sec, step_name, extra_data=None, **kwargs):
        captured_models.append(model)
        return ok_stub(step_name)

    with patch("phase_5_implement.invoke_llm_subprocess", side_effect=fake_invoke):
        _invoke_red_llm(ctx, prev)

    assert captured_models, "invoke_llm_subprocess was not called"
    model_value = captured_models[0]
    assert model_value == "custom-model-override", (
        f"Expected per-step override 'custom-model-override' to win over tier default, "
        f"got {model_value!r}"
    )


# ─── AC5: SIMPLE tier does NOT downgrade validation (Opus hard gate) ──────────


def test_simple_tier_does_not_downgrade_validation(tmp_path):
    """A3398552 safety: tier-aware Haiku pinning for RED/GREEN must NOT leak to
    the validation step. Validation is hard-gated to Opus per
    never_skip_opus_validation_gate (99% confidence). When complexity=SIMPLE,
    validation MUST still resolve to an opus-class model.

    25e75663 migration (R2): fake_invoke now receives model= kwarg, not command= list.
    """
    from phase_5_implement import _invoke_validation_llm  # noqa: PLC0415

    scratchpad = tmp_path / "scratch"
    ctx = make_ctx(scratchpad, complexity="SIMPLE")
    prev = StepResult(
        status="ok",
        data={
            "prompt": "Validate the implementation.",
            "doc_path": str(scratchpad / "reviews/build-opus-validation.md"),
            "spec_path": str(scratchpad / "specs/build-spec.md"),
            "red_log_path": str(scratchpad / "tests/build-red-output.log"),
        },
        duration_ms=0,
        step_name="build_validation_prompt",
    )

    captured_models = []

    def fake_invoke(*, prompt, model, timeout_sec, step_name, extra_data=None, hard_gate=False, gate_label=None, **kwargs):
        captured_models.append(model)
        return StepResult(
            status="ok",
            data={"raw_response": "PASS — implementation validated."},
            duration_ms=0,
            step_name=step_name,
        )

    with patch("phase_5_implement.invoke_llm_subprocess", side_effect=fake_invoke):
        _invoke_validation_llm(ctx, prev)

    assert captured_models, "invoke_llm_subprocess was not called"
    model_value = captured_models[0]
    assert "opus" in model_value.lower(), (
        f"SAFETY VIOLATION: complexity=SIMPLE must NOT downgrade validation from opus, "
        f"got model={model_value!r}."
    )


# ─── AC6: global llm_command override beats tier default ──────────────────────


def test_global_llm_command_overrides_tier_default(tmp_path):
    """A3398552 resolution-order contract: explicit global cfg['model']
    override wins over the tier-aware Haiku default. Documents the
    composition rule: per-step config > global model > tier default > step default.

    25e75663 migration (R4): llm_command → model (model string); R2: fake signature.
    """
    from phase_5_implement import _invoke_red_llm  # noqa: PLC0415

    scratchpad = tmp_path / "scratch"
    ctx = make_ctx(
        scratchpad,
        complexity="SIMPLE",
        model="global-model-override",
    )
    prev = make_prev(scratchpad)

    captured_models = []

    def fake_invoke(*, prompt, model, timeout_sec, step_name, extra_data=None, **kwargs):
        captured_models.append(model)
        return ok_stub(step_name)

    with patch("phase_5_implement.invoke_llm_subprocess", side_effect=fake_invoke):
        _invoke_red_llm(ctx, prev)

    assert captured_models, "invoke_llm_subprocess was not called"
    model_value = captured_models[0]
    assert model_value == "global-model-override", (
        f"Expected global model override 'global-model-override' to win "
        f"over tier-aware Haiku default, got {model_value!r}. "
        f"Resolution order: per-step > global model > tier default > step default."
    )
