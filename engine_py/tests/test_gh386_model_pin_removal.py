"""GH386 — RED tests: remove Fable-5 model pins, role chains + availability fallback.

Spec: SHARED/memory/Decisions/2026-07-12_GH386_model_pin_removal_spec.md

Pre-GREEN expected outcome (today):
  AC1  FAIL — `model_config._as_chain` does not exist yet (presence gate).
  AC2  PASS — behavior-unchanged case (fable available -> "fable"); already true today.
  AC3  FAIL — no `unavailable` support in get_role_model yet -> returns "fable", not "opus".
  AC4  FAIL — no env seam (HAL_MODEL_UNAVAILABLE) consulted yet.
  AC5  FAIL — no family-match unavailable logic yet.
  AC6  FAIL — no chain/list role-value resolution yet.
  AC7  FAIL — no chain-exhausted degrade-and-warn branch/log yet.
  AC8  FAIL — `_DEFAULT_REPAIR_MODEL` still present in lib/directed_repair.py today.
  AC9  FAIL — `_resolve_directed_repair_model({})` today returns "claude-fable-5", not "sonnet".
  AC10 FAIL — literal "claude-fable-5" is present in lib/directed_repair.py today.
  AC11 PASS — cfg_override branch is unchanged; behavior already matches spec.
  AC12 FAIL — no chain skip-ahead support in _resolve_directed_repair_model yet.

Any new-symbol reference (`_as_chain`, `is_model_unavailable`) is imported INSIDE
the test body via getattr/hasattr presence gates, per §1q ext (D1CF5FDF) — the
file must COLLECT under pytest today, not raise ImportError at collection time.

UUT symbols (not mocked, per spec §6): get_role_model, is_model_unavailable,
_as_chain, _resolve_directed_repair_model.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from unittest.mock import patch

import pytest

import model_config


@pytest.fixture
def model_config_fixture(monkeypatch, tmp_path):
    """Point model_config at a tmp models.json fixture; restore prior state after."""
    prev_path = model_config._CONFIG_PATH

    def _write(data: dict) -> Path:
        fixture_path = tmp_path / "models.json"
        fixture_path.write_text(json.dumps(data), encoding="utf-8")
        model_config._CONFIG_PATH = fixture_path
        model_config.reset_cache()
        return fixture_path

    yield _write

    model_config._CONFIG_PATH = prev_path
    model_config.reset_cache()


def _load_directed_repair_module():
    import importlib
    try:
        return importlib.import_module("lib.directed_repair")
    except ImportError as exc:
        pytest.fail(f"lib/directed_repair.py failed to import: {exc}")


class TestGH386ModelPinRemoval:
    def test_ac1_as_chain_maps_input_shapes(self):
        """AC1: _as_chain(None)->[] ; "x"->["x"] ; ["a","b"]->["a","b"] ; ""->[]"""
        if not hasattr(model_config, "_as_chain"):
            pytest.fail("model_config._as_chain does not exist yet (GH386 AC1 not implemented)")
        as_chain = model_config._as_chain
        assert as_chain(None) == []
        assert as_chain("x") == ["x"]
        assert as_chain(["a", "b"]) == ["a", "b"]
        assert as_chain("") == []

    def test_ac2_decorrelated_verifier_fable_available_unchanged(self, model_config_fixture):
        """AC2: fable available -> "fable" (behavior with fable available MUST NOT change)."""
        model_config_fixture({"claude": {"decorrelated_verifier": "fable"}})
        assert model_config.get_claude_decorrelated_verifier() == "fable"

    def test_ac3_decorrelated_verifier_unavailable_via_models_json(self, model_config_fixture, monkeypatch):
        """AC3: models.json unavailable=["fable"] -> resolves to "opus", no exception."""
        monkeypatch.delenv("HAL_MODEL_UNAVAILABLE", raising=False)
        model_config_fixture({
            "claude": {"decorrelated_verifier": "fable", "unavailable": ["fable"]},
        })
        assert model_config.get_claude_decorrelated_verifier() == "opus"

    def test_ac4_decorrelated_verifier_unavailable_via_env(self, model_config_fixture, monkeypatch):
        """AC4: HAL_MODEL_UNAVAILABLE="fable" env, no unavailable key -> resolves to "opus"."""
        model_config_fixture({"claude": {"decorrelated_verifier": "fable"}})
        monkeypatch.setenv("HAL_MODEL_UNAVAILABLE", "fable")
        assert model_config.get_claude_decorrelated_verifier() == "opus"

    def test_ac5_family_match_full_model_name_unavailable(self, model_config_fixture, monkeypatch):
        """AC5: unavailable=["fable"] matches full model name "claude-fable-5" via family."""
        monkeypatch.delenv("HAL_MODEL_UNAVAILABLE", raising=False)
        model_config_fixture({
            "claude": {"decorrelated_verifier": "claude-fable-5", "unavailable": ["fable"]},
        })
        assert model_config.get_claude_decorrelated_verifier() == "opus"

    def test_ac6_list_role_value_successor_configurable(self, model_config_fixture, monkeypatch):
        """AC6: role value is a list; first-unavailable entry skipped, successor returned."""
        monkeypatch.delenv("HAL_MODEL_UNAVAILABLE", raising=False)
        model_config_fixture({
            "claude": {
                "decorrelated_verifier": ["fable", "test-successor"],
                "unavailable": ["fable"],
            },
        })
        assert model_config.get_claude_decorrelated_verifier() == "test-successor"

    def test_ac7_chain_exhausted_degrades_and_warns(self, model_config_fixture, monkeypatch, caplog):
        """AC7: all chain entries unavailable -> returns chain head, logs model_config_all_unavailable."""
        monkeypatch.delenv("HAL_MODEL_UNAVAILABLE", raising=False)
        model_config_fixture({
            "claude": {
                "decorrelated_verifier": ["fable"],
                "unavailable": ["fable", "opus"],
            },
        })
        with caplog.at_level(logging.WARNING, logger="model_config"):
            result = model_config.get_claude_decorrelated_verifier()
        assert result == "fable"
        assert any("model_config_all_unavailable" in rec.message for rec in caplog.records)

    def test_ac8_default_repair_model_literal_removed(self):
        """AC8: _DEFAULT_REPAIR_MODEL absent from directed_repair module; resolver fn present."""
        dr = _load_directed_repair_module()
        assert hasattr(dr, "_resolve_directed_repair_model"), "resolver function missing"
        assert not hasattr(dr, "_DEFAULT_REPAIR_MODEL"), (
            "GH386 AC8: _DEFAULT_REPAIR_MODEL const still present on lib.directed_repair "
            "(pin removal not yet done)"
        )

    def test_ac9_resolve_directed_repair_model_default_is_sonnet(self, model_config_fixture, monkeypatch):
        """AC9: no directed_repair key in models.json -> resolves "sonnet" via models.json role chain, never opus."""
        monkeypatch.delenv("HAL_MODEL_UNAVAILABLE", raising=False)
        model_config_fixture({"claude": {}})
        dr = _load_directed_repair_module()
        with patch("lib.observability.emit_resolver.emit_resolver_resolved") as mock_emit:
            result = dr._resolve_directed_repair_model({})
        assert result == "sonnet"
        assert "opus" not in result
        mock_emit.assert_called_once_with(
            "directed_repair_model", "models_directed_repair_role", "sonnet"
        )

    def test_ac10_no_fable5_literal_in_directed_repair_source(self):
        """AC10: source text of lib/directed_repair.py no longer contains "claude-fable-5"."""
        src_path = Path(__file__).parent.parent / "lib" / "directed_repair.py"
        text = src_path.read_text(encoding="utf-8")
        assert "claude-fable-5" not in text, (
            "GH386 AC10: claude-fable-5 literal still present in lib/directed_repair.py"
        )

    def test_ac11_cfg_override_unchanged(self):
        """AC11: org_config override still wins, reason "cfg_override" (behavior unchanged)."""
        dr = _load_directed_repair_module()
        with patch("lib.observability.emit_resolver.emit_resolver_resolved") as mock_emit:
            result = dr._resolve_directed_repair_model({"directed_repair_model": "custom-x"})
        assert result == "custom-x"
        mock_emit.assert_called_once_with(
            "directed_repair_model", "cfg_override", "custom-x"
        )

    def test_ac12_directed_repair_chain_skip_ahead(self, model_config_fixture, monkeypatch):
        """AC12: models.json directed_repair chain (sonnet unavailable) -> skips to "haiku"."""
        monkeypatch.delenv("HAL_MODEL_UNAVAILABLE", raising=False)
        model_config_fixture({"claude": {"unavailable": ["sonnet"]}})
        dr = _load_directed_repair_module()
        with patch("lib.observability.emit_resolver.emit_resolver_resolved"):
            result = dr._resolve_directed_repair_model({})
        assert result == "haiku"
