"""RED: GH834-fix — absent models.json must not emit a WARNING (DEBUG only).

Spec: SHARED/memory/Decisions/2026-07-15_GH834fix_model_config_absent_silent_spec.md
"""
import json
import logging

import pytest

from lib import model_config


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    model_config.reset_cache()
    yield
    model_config.reset_cache()


def test_ac1_absent_config_no_warning_but_debug_absent_record(monkeypatch, tmp_path, caplog):
    missing = tmp_path / "does_not_exist" / "models.json"
    monkeypatch.setattr(model_config, "_CONFIG_PATH", missing)

    with caplog.at_level(logging.DEBUG, logger="lib.model_config"):
        result = model_config._load()

    assert result == {}
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings == [], f"expected no WARNING records, got {warnings}"
    debug_msgs = [r.getMessage() for r in caplog.records if r.levelno == logging.DEBUG]
    assert any("model_config_absent" in m for m in debug_msgs), (
        f"expected a DEBUG record containing 'model_config_absent', got {debug_msgs}"
    )


def test_ac2_corrupt_json_emits_warning_unreadable(monkeypatch, tmp_path, caplog):
    bad = tmp_path / "models.json"
    bad.write_text("{ not valid json ", encoding="utf-8")
    monkeypatch.setattr(model_config, "_CONFIG_PATH", bad)

    with caplog.at_level(logging.DEBUG, logger="lib.model_config"):
        result = model_config._load()

    assert result == {}
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any("model_config_unreadable" in m for m in warnings), (
        f"expected WARNING 'model_config_unreadable', got {warnings}"
    )


def test_ac3_valid_json_parsed_no_warning(monkeypatch, tmp_path, caplog):
    good = tmp_path / "models.json"
    good.write_text(json.dumps({"claude": {"primary": "sonnet"}}), encoding="utf-8")
    monkeypatch.setattr(model_config, "_CONFIG_PATH", good)

    with caplog.at_level(logging.DEBUG, logger="lib.model_config"):
        result = model_config._load()

    assert result == {"claude": {"primary": "sonnet"}}
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings == []


def test_ac4_second_load_call_emits_no_new_records(monkeypatch, tmp_path, caplog):
    missing = tmp_path / "still_missing" / "models.json"
    monkeypatch.setattr(model_config, "_CONFIG_PATH", missing)

    with caplog.at_level(logging.DEBUG, logger="lib.model_config"):
        model_config._load()
        caplog.clear()
        result = model_config._load()

    assert result == {}
    assert caplog.records == [], f"expected zero new records on cache-hit, got {caplog.records}"
