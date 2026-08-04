"""RED tests for F88217EB Prong 1 — model-aware spec/review timeouts in phase_45_spec.

Agreement F88217EB: Opus reviewer SIGKILLed at exactly 300s during FEATURE-class
forge-1778620958 run on agreement B628BBC9. Root cause: `_resolve_review_timeout_sec`
is complexity-aware (80CC602D) but NOT model-aware. Fix: add Opus-class detection
helper `_is_opus_class_model` + new branch (Opus AND non-COMPLEX → 600s floor for
review, 900s floor for spec).

Sibling shipping pattern: 80CC602D (complexity-aware timeouts, same file).
Engine_py self-modification per delegation rule 35F1A4EB (Option D).
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))


# ─── AC1 — _is_opus_class_model: None / empty / no-flag → False ───────────────


def test_ac1_is_opus_class_model_handles_none_empty_no_flag():
    """AC1: _is_opus_class_model(None) → False; [] → False; no --model flag → False."""
    from bytedigger_engine.workflows.phase_45_spec import _is_opus_class_model  # noqa: PLC0415

    assert _is_opus_class_model(None) is False
    assert _is_opus_class_model([]) is False
    assert _is_opus_class_model(["claude", "-p"]) is False
    assert _is_opus_class_model(["claude", "-p", "--model"]) is False  # no value follows


# ─── AC2 — _is_opus_class_model: known Opus strings → True (case-insensitive) ──


def test_ac2_opus_recognised_case_insensitive():
    """AC2: --model opus/OPUS/Opus/claude-opus-4-7 all return True."""
    from bytedigger_engine.workflows.phase_45_spec import _is_opus_class_model  # noqa: PLC0415

    assert _is_opus_class_model(["claude", "-p", "--model", "opus"]) is True
    assert _is_opus_class_model(["claude", "-p", "--model", "OPUS"]) is True
    assert _is_opus_class_model(["claude", "-p", "--model", "Opus"]) is True
    assert _is_opus_class_model(["claude", "-p", "--model", "claude-opus-4-7"]) is True
    assert _is_opus_class_model(["claude", "-p", "--model", "opus-4-6"]) is True


# ─── AC3 — _is_opus_class_model: non-Opus model strings → False ────────────────


def test_ac3_non_opus_model_returns_false():
    """AC3: sonnet, haiku, gemini-2.5-pro, empty string → False."""
    from bytedigger_engine.workflows.phase_45_spec import _is_opus_class_model  # noqa: PLC0415

    assert _is_opus_class_model(["claude", "-p", "--model", "sonnet"]) is False
    assert _is_opus_class_model(["claude", "-p", "--model", "haiku"]) is False
    assert _is_opus_class_model(["claude", "-p", "--model", "gemini-2.5-pro"]) is False
    assert _is_opus_class_model(["claude", "-p", "--model", ""]) is False


# ─── AC4 — _is_opus_class_model: --model=<value> equals form ──────────────────


def test_ac4_equals_form_model_flag():
    """AC4: --model=opus → True; --model=sonnet → False (equals-sign form)."""
    from bytedigger_engine.workflows.phase_45_spec import _is_opus_class_model  # noqa: PLC0415

    assert _is_opus_class_model(["claude", "--model=opus"]) is True
    assert _is_opus_class_model(["claude", "--model=claude-opus-4-7"]) is True
    assert _is_opus_class_model(["claude", "--model=sonnet"]) is False
    assert _is_opus_class_model(["claude", "--model=haiku"]) is False


# ─── AC5 — _is_opus_class_model: --model present but no follow-up value ────────


def test_ac5_model_flag_at_end_malformed():
    """AC5: ['claude', '-p', '--model'] (flag with no value) → False, not an error."""
    from bytedigger_engine.workflows.phase_45_spec import _is_opus_class_model  # noqa: PLC0415

    assert _is_opus_class_model(["claude", "-p", "--model"]) is False


# ─── AC6 — _resolve_review_timeout_sec: Opus, no complexity → 600 ──────────────


def test_ac6_review_timeout_opus_floor_600():
    """AC6: Opus reviewer and no complexity set → 600s floor (new branch)."""
    from bytedigger_engine.workflows.phase_45_spec import _resolve_review_timeout_sec  # noqa: PLC0415

    cfg = {"review_llm_command": ["claude", "-p", "--model", "opus"]}
    assert _resolve_review_timeout_sec(cfg) == 600


# ─── AC7 — _resolve_review_timeout_sec: COMPLEX wins over Opus floor → 900 ─────


def test_ac7_review_timeout_complex_beats_opus_floor():
    """AC7: COMPLEX complexity wins over Opus model floor → still 900s."""
    from bytedigger_engine.workflows.phase_45_spec import _resolve_review_timeout_sec  # noqa: PLC0415

    cfg = {
        "complexity": "COMPLEX",
        "review_llm_command": ["claude", "-p", "--model", "opus"],
    }
    assert _resolve_review_timeout_sec(cfg) == 900


# ─── AC8 — _resolve_review_timeout_sec: non-Opus model, non-COMPLEX → 300 ──────


def test_ac8_review_timeout_non_opus_baseline_300():
    """AC8: sonnet or haiku model, SIMPLE complexity → 300s (no floor)."""
    from bytedigger_engine.workflows.phase_45_spec import _resolve_review_timeout_sec  # noqa: PLC0415

    assert _resolve_review_timeout_sec(
        {"complexity": "SIMPLE", "review_llm_command": ["claude", "-p", "--model", "sonnet"]}
    ) == 300
    assert _resolve_review_timeout_sec(
        {"review_llm_command": ["claude", "-p", "--model", "haiku"]}
    ) == 300


# ─── AC9 — _resolve_review_timeout_sec: explicit override beats Opus floor ──────


def test_ac9_explicit_override_wins_over_opus_floor():
    """AC9: review_llm_timeout_sec explicit override wins even when Opus model set."""
    from bytedigger_engine.workflows.phase_45_spec import _resolve_review_timeout_sec  # noqa: PLC0415

    cfg = {
        "review_llm_timeout_sec": 123,
        "review_llm_command": ["claude", "-p", "--model", "opus"],
    }
    assert _resolve_review_timeout_sec(cfg) == 123


# ─── AC10 — _resolve_spec_timeout_sec: symmetric Opus/COMPLEX/baseline behaviour ─


def test_ac10_spec_timeout_symmetric_opus_and_complex():
    """AC10: Opus spec writer → 900s floor; COMPLEX wins → 1800s; sonnet → 600s."""
    from bytedigger_engine.workflows.phase_45_spec import _resolve_spec_timeout_sec  # noqa: PLC0415

    # Opus floor for spec writer (non-COMPLEX)
    assert _resolve_spec_timeout_sec(
        {"spec_llm_command": ["claude", "-p", "--model", "opus"]}
    ) == 900

    # COMPLEX wins over Opus floor
    assert _resolve_spec_timeout_sec(
        {
            "complexity": "COMPLEX",
            "spec_llm_command": ["claude", "-p", "--model", "opus"],
        }
    ) == 1800

    # Non-Opus model → 600s baseline (no floor)
    assert _resolve_spec_timeout_sec(
        {"spec_llm_command": ["claude", "-p", "--model", "sonnet"]}
    ) == 600


# ─── AC11 — new module constants exported ──────────────────────────────────────


def test_ac11_module_constants_exported_with_correct_values():
    """AC11: DEFAULT_REVIEW_TIMEOUT_SEC_OPUS == 600; DEFAULT_SPEC_TIMEOUT_SEC_OPUS == 900."""
    from bytedigger_engine.workflows.phase_45_spec import (  # noqa: PLC0415
        DEFAULT_REVIEW_TIMEOUT_SEC_OPUS,
        DEFAULT_SPEC_TIMEOUT_SEC_OPUS,
    )

    assert DEFAULT_REVIEW_TIMEOUT_SEC_OPUS == 600
    assert DEFAULT_SPEC_TIMEOUT_SEC_OPUS == 900


# ─── AC12 — regression-guard: 80CC602D baseline paths still resolve correctly ───


def test_ac12_regression_guard_baseline_cases_unchanged():
    """AC12: Pre-existing 80CC602D paths (no-cfg, COMPLEX, explicit override) unchanged."""
    from bytedigger_engine.workflows.phase_45_spec import (  # noqa: PLC0415
        _resolve_review_timeout_sec,
        _resolve_spec_timeout_sec,
    )

    # no-cfg baselines
    assert _resolve_review_timeout_sec(None) == 300
    assert _resolve_review_timeout_sec({}) == 300
    assert _resolve_spec_timeout_sec(None) == 600
    assert _resolve_spec_timeout_sec({}) == 600

    # COMPLEX → still 900 / 1800
    assert _resolve_review_timeout_sec({"complexity": "COMPLEX"}) == 900
    assert _resolve_spec_timeout_sec({"complexity": "COMPLEX"}) == 1800

    # explicit numeric override wins
    assert _resolve_review_timeout_sec({"review_llm_timeout_sec": 450}) == 450
    assert _resolve_spec_timeout_sec({"spec_llm_timeout_sec": 1234}) == 1234
