"""RED tests for GH597 — model-mix phase_45: review-voices + directed_repair
config-driven roles (spec_reviewer / directed_repair).

Spec: SHARED/memory/Decisions/2026-07-11_GH597_model_mix_phase45_spec.md

UUT symbols (never patched): resolve_review_model, _resolve_directed_repair_model,
get_role_model, get_claude_spec_reviewer, _default_review_model.
Collaborators allowed to be mocked: emit_resolver_resolved, invoke_llm_subprocess.

§1q-extension: not-yet-existing symbols are imported INSIDE test bodies so the
file COLLECTS cleanly and fails at assert time, not collect time.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "lib"))
sys.path.insert(0, str(HERE.parent / "workflows"))

import model_config  # noqa: E402


FIXTURE_WITH_ROLES = {
    "claude": {
        "primary": "sonnet",
        "fallback": "haiku",
        "critical": "critfix",
        "spec_writer": "writefix",
        "spec_reviewer": "sonnet",
        "directed_repair": "sonnet",
    }
}

FIXTURE_WITHOUT_ROLES = {
    "claude": {
        "primary": "sonnet",
        "fallback": "haiku",
        "critical": "critfix",
        "spec_writer": "writefix",
    }
}


def _write_fixture(tmp_path, data) -> Path:
    p = tmp_path / "models.json"
    p.write_text(json.dumps(data))
    return p


@pytest.fixture
def with_roles_cfg(tmp_path, monkeypatch):
    cfg_path = _write_fixture(tmp_path, FIXTURE_WITH_ROLES)
    monkeypatch.setattr(model_config, "_CONFIG_PATH", cfg_path)
    model_config.reset_cache()
    yield cfg_path
    model_config.reset_cache()


@pytest.fixture
def without_roles_cfg(tmp_path, monkeypatch):
    cfg_path = _write_fixture(tmp_path, FIXTURE_WITHOUT_ROLES)
    monkeypatch.setattr(model_config, "_CONFIG_PATH", cfg_path)
    model_config.reset_cache()
    yield cfg_path
    model_config.reset_cache()


def _ctx(**cfg):
    class _Ctx:
        org_config = cfg
    return _Ctx()


def _prev():
    from contracts import StepResult
    return StepResult(
        status="ok",
        data={
            "prompt": "review this spec",
            "doc_path": "/tmp/doc.md",
            "spec_path": "/tmp/spec.md",
        },
        duration_ms=0,
        step_name="build_review_prompt",
    )


# ---- AC1 ----

def test_ac1_get_role_model_and_get_claude_spec_reviewer_delegate(with_roles_cfg):
    assert model_config.get_role_model("spec_reviewer") == "sonnet"
    assert hasattr(model_config, "get_claude_spec_reviewer"), (
        "GH597: model_config.get_claude_spec_reviewer not yet implemented"
    )
    assert model_config.get_claude_spec_reviewer() == "sonnet"


# ---- AC2 / AC3 ----

def test_ac2_resolve_review_model_cfg_override_wins(with_roles_cfg):
    from lib.spec_retry_cycle import resolve_review_model
    assert resolve_review_model({"review_model": "X"}) == "X"


def test_ac3_resolve_review_model_cfg_model_wins_over_role(with_roles_cfg):
    from lib.spec_retry_cycle import resolve_review_model
    assert resolve_review_model({"model": "Y"}) == "Y"


# ---- AC4 ----

def test_ac4_resolve_review_model_falls_back_to_role_and_emits(with_roles_cfg, monkeypatch):
    monkeypatch.setenv("HAL_GATE_MODEL_FLOOR", "sonnet")
    from lib.spec_retry_cycle import resolve_review_model
    with patch("lib.observability.emit_resolver.emit_resolver_resolved") as mock_emit:
        result = resolve_review_model(None)
    assert result == "sonnet"
    assert mock_emit.called, "GH597: resolve_review_model must emit resolver_review_model_resolved"
    args = mock_emit.call_args.args
    assert args[0] == "review_model"
    assert args[1] == "models_spec_reviewer_role"
    assert args[2] == "sonnet"


# ---- AC5 ----

def test_ac5_resolve_review_model_legacy_falls_back_to_critical(without_roles_cfg):
    from lib.spec_retry_cycle import resolve_review_model
    expected_critical = model_config.get_claude_critical()
    result = resolve_review_model(None)
    assert result == expected_critical


# ---- AC6 ----

def test_ac6_phase_45_spec_default_review_model_uses_role(with_roles_cfg):
    import workflows.phase_45_spec as p45
    assert p45._default_review_model() == "sonnet"


def test_ac6b_phase_45_spec_default_review_model_legacy(without_roles_cfg):
    import workflows.phase_45_spec as p45
    expected_critical = model_config.get_claude_critical()
    assert p45._default_review_model() == expected_critical


# ---- AC7 ----

def test_ac7_phase_45_spec_lite_default_review_model_uses_role(with_roles_cfg):
    import workflows.phase_45_spec_lite as p45_lite
    assert p45_lite._default_review_model() == "sonnet"


def test_ac7b_phase_45_spec_lite_default_review_model_legacy(without_roles_cfg):
    import workflows.phase_45_spec_lite as p45_lite
    expected_critical = model_config.get_claude_critical()
    assert p45_lite._default_review_model() == expected_critical


# ---- AC8 ----

def test_ac8_invoke_review_llm_passes_role_model(with_roles_cfg, monkeypatch):
    monkeypatch.setenv("HAL_GATE_MODEL_FLOOR", "sonnet")
    import workflows.phase_45_spec as p45
    captured = {}

    def _fake_invoke(**kwargs):
        captured.update(kwargs)
        from contracts import StepResult
        return StepResult(status="ok", data={"raw_response": "## Verdict\nSHIP\n"},
                           duration_ms=0, step_name="invoke_review_llm")

    with patch.object(p45, "invoke_llm_subprocess", side_effect=_fake_invoke):
        p45._invoke_review_llm(_ctx(), _prev())

    assert captured.get("model") == "sonnet", (
        f"GH597: expected review model 'sonnet' from role, got {captured.get('model')!r}"
    )


# ---- AC9 ----

def test_ac9a_directed_repair_cfg_override_wins(with_roles_cfg):
    from directed_repair import _resolve_directed_repair_model
    assert _resolve_directed_repair_model({"directed_repair_model": "Z"}) == "Z"


def test_ac9b_directed_repair_uses_role_and_emits(with_roles_cfg):
    from directed_repair import _resolve_directed_repair_model
    with patch("lib.observability.emit_resolver.emit_resolver_resolved") as mock_emit:
        result = _resolve_directed_repair_model(None)
    assert result == "sonnet", (
        f"GH597: expected directed_repair model 'sonnet' from role, got {result!r}"
    )
    assert mock_emit.called
    args = mock_emit.call_args.args
    assert args[0] == "directed_repair_model"
    assert args[1] == "models_directed_repair_role"
    assert args[2] == "sonnet"


def test_ac9c_directed_repair_legacy_default(without_roles_cfg):
    """GH386: no directed_repair key in models.json -> FALLBACK_CONFIG chain resolves 'sonnet'."""
    from directed_repair import _resolve_directed_repair_model
    with patch("lib.observability.emit_resolver.emit_resolver_resolved") as mock_emit:
        result = _resolve_directed_repair_model(None)
    assert result == "sonnet"
    assert mock_emit.called
    args = mock_emit.call_args.args
    assert args[1] == "models_directed_repair_role"


# ---- AC10 ----

def test_ac10_default_spec_model_unaffected(with_roles_cfg):
    import workflows.phase_45_spec as p45
    assert p45._default_spec_model() == "writefix"
