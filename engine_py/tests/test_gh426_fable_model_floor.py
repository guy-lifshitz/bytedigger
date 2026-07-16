"""Tests for GH426: hard-gate model floor must recognize fable (rank above opus).

Frozen spec: SHARED/memory/Decisions/2026-07-08_GH426_fable_model_floor_spec.md

_is_fable_model / _meets_opus_floor do not exist yet in llm_subprocess.py — those
imports are deferred to inside each test body (D1CF5FDF collect-safety: the file
must COLLECT cleanly against current prod, only individual tests may FAIL).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

from llm_subprocess import (  # noqa: E402
    _MODEL_RANK,
    _assert_hard_gate_opus,
    _assert_in_session_model_or_downgrade,
    _model_family,
    _price_for_model,
    _tier_model_is_downgrade,
)


def _import_is_fable_model():
    try:
        from llm_subprocess import _is_fable_model
    except ImportError:
        pytest.fail("_is_fable_model not implemented yet (GH426)")
    return _is_fable_model


def _import_meets_opus_floor():
    try:
        from llm_subprocess import _meets_opus_floor
    except ImportError:
        pytest.fail("_meets_opus_floor not implemented yet (GH426)")
    return _meets_opus_floor


# AC1 -----------------------------------------------------------------------

def test_ac1_is_fable_model_recognizes_anchored_forms():
    is_fable = _import_is_fable_model()
    assert is_fable("fable") is True
    assert is_fable("claude-fable-5") is True
    assert is_fable("us.anthropic/fable") is True


def test_ac1_is_fable_model_rejects_non_anchored_lookalikes():
    is_fable = _import_is_fable_model()
    assert is_fable("fablegrande") is False
    assert is_fable("affable") is False
    assert is_fable("xfable") is False


# AC2 -------------------------------------------------------------------------

def test_ac2_model_family_recognizes_fable():
    assert _model_family("fable") == "fable"
    assert _model_family("claude-fable-5") == "fable"


def test_ac2_model_family_unchanged_for_opus_sonnet_haiku():
    assert _model_family("opus") == "opus"
    assert _model_family("claude-opus-4-8") == "opus"
    assert _model_family("sonnet") == "sonnet"
    assert _model_family("haiku") == "haiku"


# AC3 -------------------------------------------------------------------------

def test_ac3_model_rank_fable_above_opus():
    assert _MODEL_RANK["fable"] == 3
    assert _MODEL_RANK["fable"] > _MODEL_RANK["opus"]


# AC4 -------------------------------------------------------------------------

def test_ac4_meets_opus_floor_true_for_opus_and_fable():
    meets = _import_meets_opus_floor()
    assert meets("opus") is True
    assert meets("claude-opus-4-8") is True
    assert meets("fable") is True
    assert meets("claude-fable-5") is True


def test_ac4_meets_opus_floor_false_below_floor_and_unknown():
    meets = _import_meets_opus_floor()
    assert meets("sonnet") is False
    assert meets("haiku") is False
    assert meets("") is False
    assert meets("gpt-4.1") is False


# AC5 -------------------------------------------------------------------------

def test_ac5_in_session_assertor_passes_fable_dispatched_model():
    result = _assert_in_session_model_or_downgrade(
        result_obj={"dispatched_model": "claude-fable-5"},
        pinned_model="fable",
        hard_gate=True,
        step_name="step",
        nonce="nonce",
    )
    assert result is None, "fable dispatch must pass the hard gate (rank above opus)"


# AC6 -------------------------------------------------------------------------

def test_ac6_in_session_assertor_still_rejects_sonnet():
    result = _assert_in_session_model_or_downgrade(
        result_obj={"dispatched_model": "sonnet"},
        pinned_model="fable",
        hard_gate=True,
        step_name="step",
        nonce="nonce",
    )
    assert result is not None
    assert result.error_code == "E_HARD_GATE_MODEL_DOWNGRADE"


# AC7 -------------------------------------------------------------------------

def test_ac7_subprocess_assertor_passes_fable():
    result = _assert_hard_gate_opus(
        ["claude", "-p", "--model", "claude-fable-5"], "step", "validation"
    )
    assert result is None, "fable dispatch must pass the subprocess hard gate"


def test_ac7_subprocess_assertor_still_rejects_sonnet():
    result = _assert_hard_gate_opus(
        ["claude", "-p", "--model", "sonnet"], "step", "validation"
    )
    assert result is not None
    assert result.error_code == "E_HARD_GATE_MODEL_DOWNGRADE"


# AC8 -------------------------------------------------------------------------

def test_ac8_tier_model_is_downgrade_sonnet_to_fable_is_true():
    assert _tier_model_is_downgrade("sonnet", "fable") is True


def test_ac8_tier_model_is_downgrade_fable_to_opus_is_false():
    assert _tier_model_is_downgrade("fable", "opus") is False


# AC9 -------------------------------------------------------------------------

def test_ac9_price_for_model_resolves_fable_full_id_via_alias():
    result = _price_for_model("claude-fable-5", table={"fable": (10.0, 50.0)})
    assert result == (10.0, 50.0)
