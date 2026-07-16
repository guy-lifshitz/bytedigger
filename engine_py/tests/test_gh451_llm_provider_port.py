"""Tests for GH451: LLM Provider port — provider-agnostic invocation + configurable gate floor.

Frozen spec: SHARED/memory/Decisions/2026-07-09_GH451_llm_provider_port_spec.md

`lib.llm_provider` does not exist yet; `_resolve_gate_floor`/`_meets_gate_floor` (llm_subprocess)
and `get_role_model`/`get_gate_floor` (model_config) do not exist yet either. Per §1q/D1CF5FDF,
those imports are deferred to inside each test body so collection succeeds cleanly against
current prod and only individual tests FAIL.
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
    _build_claude_argv,
    _find_last_result_event,
    _model_family,
)

import lib.model_config as model_config  # noqa: E402


# AC1 -------------------------------------------------------------------------

def test_ac1_get_provider_default_is_claude():
    from lib.llm_provider import get_provider

    spec = get_provider()
    assert spec.name == "claude"


# AC2 -------------------------------------------------------------------------

def test_ac2_get_provider_honors_env_override_and_unknown_raises_keyerror(monkeypatch):
    from lib.llm_provider import ProviderSpec, get_provider, register_provider, reset_providers

    stub = ProviderSpec(
        name="stub",
        build_argv=lambda model: ["stub-cli", model],
        stream_flags=(),
        parse_result=lambda events: None,
        model_rank={"stub-model": 0},
        model_family=lambda model: "stub-model" if model else None,
        default_gate_floor="stub-model",
    )
    try:
        register_provider(stub)
        monkeypatch.setenv("HAL_LLM_PROVIDER", "stub")
        assert get_provider().name == "stub"

        monkeypatch.setenv("HAL_LLM_PROVIDER", "does-not-exist")
        with pytest.raises(KeyError):
            get_provider()
    finally:
        reset_providers()


# AC3 -------------------------------------------------------------------------

def test_ac3_claude_provider_build_argv_and_stream_flags():
    from lib.llm_provider import CLAUDE_PROVIDER

    assert CLAUDE_PROVIDER.build_argv("opus") == ["claude", "-p", "--model", "opus"]
    assert CLAUDE_PROVIDER.stream_flags == ("--output-format", "stream-json", "--verbose")


# AC4 -------------------------------------------------------------------------

def test_ac4_build_claude_argv_delegates_to_registered_provider(monkeypatch):
    from lib.llm_provider import ProviderSpec, register_provider, reset_providers

    stub = ProviderSpec(
        name="stub",
        build_argv=lambda model: ["STUB-ARGV", model],
        stream_flags=(),
        parse_result=lambda events: None,
        model_rank={"x": 0},
        model_family=lambda model: None,
        default_gate_floor="x",
    )
    try:
        register_provider(stub)
        monkeypatch.setenv("HAL_LLM_PROVIDER", "stub")
        assert _build_claude_argv("m") == ["STUB-ARGV", "m"]
    finally:
        reset_providers()


# AC5 -------------------------------------------------------------------------

def test_ac5_find_last_result_event_delegates_to_provider(monkeypatch):
    from lib.llm_provider import ProviderSpec, register_provider, reset_providers

    stub = ProviderSpec(
        name="stub",
        build_argv=lambda model: [],
        stream_flags=(),
        parse_result=lambda events: {"marker": "STUB-RESULT"},
        model_rank={"x": 0},
        model_family=lambda model: None,
        default_gate_floor="x",
    )
    try:
        register_provider(stub)
        monkeypatch.setenv("HAL_LLM_PROVIDER", "stub")
        assert _find_last_result_event([{"type": "other"}]) == {"marker": "STUB-RESULT"}
    finally:
        reset_providers()


def test_ac5_find_last_result_event_claude_behavior_unchanged():
    events = [{"type": "result", "id": 1}, {"type": "assistant"}, {"type": "result", "id": 2}]
    assert _find_last_result_event(events) == {"type": "result", "id": 2}
    assert _find_last_result_event([{"type": "assistant"}]) is None


# AC6 -------------------------------------------------------------------------

def test_ac6_taxonomy_names_importable_and_regression_pin():
    from llm_subprocess import _is_fable_model, _is_opus_model, _meets_opus_floor

    assert _MODEL_RANK["fable"] == 3
    assert _model_family("claude-fable-5") == "fable"
    assert _meets_opus_floor("fable") is True
    assert _meets_opus_floor("sonnet") is False
    assert _is_fable_model("fable") is True
    assert _is_opus_model("opus") is True


# AC7 -------------------------------------------------------------------------

def test_ac7_resolve_gate_floor_precedence_matrix(tmp_path, monkeypatch):
    from llm_subprocess import _resolve_gate_floor

    monkeypatch.delenv("HAL_GATE_MODEL_FLOOR", raising=False)
    cfg_path = tmp_path / "models.json"
    old_path = model_config._CONFIG_PATH
    try:
        model_config._CONFIG_PATH = None
        model_config.reset_cache()
        assert _resolve_gate_floor() == "opus"

        monkeypatch.setenv("HAL_GATE_MODEL_FLOOR", "sonnet")
        assert _resolve_gate_floor() == "sonnet"
        monkeypatch.delenv("HAL_GATE_MODEL_FLOOR", raising=False)

        cfg_path.write_text('{"claude": {"gate_floor": "sonnet"}}', encoding="utf-8")
        model_config._CONFIG_PATH = cfg_path
        model_config.reset_cache()
        assert _resolve_gate_floor() == "sonnet"

        monkeypatch.setenv("HAL_GATE_MODEL_FLOOR", "haiku")
        assert _resolve_gate_floor() == "haiku", "env must win over config"
    finally:
        model_config._CONFIG_PATH = old_path
        model_config.reset_cache()


# AC8 -------------------------------------------------------------------------

def test_ac8_gate_floor_resolution_emits_resolver_resolved_env_source(monkeypatch):
    import llm_subprocess as llm_subprocess_mod

    captured = []

    def _fake_emit(helper, source, value, extra=None):
        captured.append((helper, source, value))

    monkeypatch.setattr(llm_subprocess_mod, "emit_resolver_resolved", _fake_emit)
    monkeypatch.setenv("HAL_GATE_MODEL_FLOOR", "sonnet")
    old_path = model_config._CONFIG_PATH
    try:
        model_config._CONFIG_PATH = None
        model_config.reset_cache()
        llm_subprocess_mod._resolve_gate_floor()
    finally:
        monkeypatch.delenv("HAL_GATE_MODEL_FLOOR", raising=False)
        model_config._CONFIG_PATH = old_path
        model_config.reset_cache()

    assert any(c[0] == "gate_floor" and c[1] == "env" for c in captured)


def test_ac8_gate_floor_resolution_emits_resolver_resolved_provider_default_source(monkeypatch):
    import llm_subprocess as llm_subprocess_mod

    captured = []

    def _fake_emit(helper, source, value, extra=None):
        captured.append((helper, source, value))

    monkeypatch.setattr(llm_subprocess_mod, "emit_resolver_resolved", _fake_emit)
    monkeypatch.delenv("HAL_GATE_MODEL_FLOOR", raising=False)
    old_path = model_config._CONFIG_PATH
    try:
        model_config._CONFIG_PATH = None
        model_config.reset_cache()
        llm_subprocess_mod._resolve_gate_floor()
    finally:
        model_config._CONFIG_PATH = old_path
        model_config.reset_cache()

    assert any(c[0] == "gate_floor" and c[1] == "provider_default" for c in captured)


# AC9 -------------------------------------------------------------------------

def test_ac9_hard_gate_passes_sonnet_when_floor_lowered(monkeypatch):
    monkeypatch.setenv("HAL_GATE_MODEL_FLOOR", "sonnet")
    try:
        result = _assert_hard_gate_opus(["claude", "-p", "--model", "sonnet"], "s", "g")
    finally:
        monkeypatch.delenv("HAL_GATE_MODEL_FLOOR", raising=False)
    assert result is None, "deliberately-lowered floor must let sonnet pass the hard gate"


# AC10 ------------------------------------------------------------------------

def test_ac10_hard_gate_still_rejects_sonnet_at_default_floor(monkeypatch):
    monkeypatch.delenv("HAL_GATE_MODEL_FLOOR", raising=False)
    result = _assert_hard_gate_opus(["claude", "-p", "--model", "sonnet"], "s", "g")
    assert result is not None
    assert result.error_code == "E_HARD_GATE_MODEL_DOWNGRADE"


# AC11 ------------------------------------------------------------------------

def test_ac11_get_role_model_matches_getters_and_get_gate_floor_none_without_key():
    from lib.model_config import get_gate_floor, get_role_model

    role_map = {
        "primary": model_config.get_claude_primary,
        "fallback": model_config.get_claude_fallback,
        "critical": model_config.get_claude_critical,
        "spec_writer": model_config.get_claude_spec_writer,
        "discovery": model_config.get_claude_discovery,
        "explore": model_config.get_claude_explore,
        "decorrelated_verifier": model_config.get_claude_decorrelated_verifier,
    }
    for role, getter in role_map.items():
        assert get_role_model(role) == getter()

    assert get_gate_floor() is None


# AC12 ------------------------------------------------------------------------

def test_ac12_invalid_env_floor_token_falls_through_to_default(monkeypatch):
    from llm_subprocess import _resolve_gate_floor

    monkeypatch.setenv("HAL_GATE_MODEL_FLOOR", "bogus")
    old_path = model_config._CONFIG_PATH
    try:
        model_config._CONFIG_PATH = None
        model_config.reset_cache()
        assert _resolve_gate_floor() == "opus"
        result = _assert_hard_gate_opus(["claude", "-p", "--model", "sonnet"], "s", "g")
    finally:
        monkeypatch.delenv("HAL_GATE_MODEL_FLOOR", raising=False)
        model_config._CONFIG_PATH = old_path
        model_config.reset_cache()

    assert result is not None
    assert result.error_code == "E_HARD_GATE_MODEL_DOWNGRADE"
