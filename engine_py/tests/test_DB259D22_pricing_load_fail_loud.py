"""RED tests for DB259D22: _load_pricing_table() fail-loud on empty/missing pricing.

Spec: SHARED/memory/Decisions/2026-06-25_DB259D22_pricing_load_fail_loud_spec.md

§1l (stub-passability): tests call the real _load_pricing_table(). The UUT is
NOT patched/mocked. Only the NON-UUT seam (config_provider.models_config_path
and HAL_DIR env) is patched — exactly the pattern from AC5 in the sibling test
(test_llm_subprocess_decouple_B7E2F9A1.py, lines 282-290).

§1i: no singleton-resource or timing races — N/A (no file locks/ports).

§1q / D1CF5FDF compliance: all imports are at top level; the UUT symbol
(_load_pricing_table) already exists in production so there is no
not-yet-existing import. File COLLECTS cleanly.

§1y reachability:
  Point   = the two logger.warning lines GREEN will add to _load_pricing_table
  Host    = _load_pricing_table() (called directly from each test)
  Test-path = test invokes _load_pricing_table() under caplog.at_level(WARNING)
              with models_config_path seam pointed at controlled fixture

Pre-GREEN expectation:
  AC1 — FAIL: prod has no logger.warning in except handler → 0 WARNING records
  AC2 — FAIL: prod has no logger.warning after parse loop → 0 WARNING records
  AC3 — PASS (guard): prod already returns non-empty table on valid JSON, no warning emitted
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

import llm_subprocess
import config_provider


# ---------------------------------------------------------------------------
# AC1: missing file → WARNING emitted AND returns {}
# ---------------------------------------------------------------------------

def test_ac1_missing_models_json_emits_warning_and_returns_empty(
    monkeypatch, tmp_path, caplog
):
    """AC1: when models_config_path() points at a nonexistent file,
    _load_pricing_table() emits >=1 WARNING and returns {}.

    Pre-GREEN FAIL reason: the except handler does 'return {}' with no
    logger.warning(), so caplog captures 0 WARNING records → assert fails.

    Seam: monkeypatch both HAL_DIR env (post-GREEN path) AND
    config_provider.models_config_path (pre/post-GREEN path) so the function
    tries to open a file that does not exist → falls into the except branch.
    """
    nonexistent = tmp_path / "nonexistent_hal_root" / "SHARED" / "config" / "models.json"
    monkeypatch.setenv("HAL_DIR", str(tmp_path / "nonexistent_hal_root"))
    monkeypatch.setattr(config_provider, "models_config_path", lambda: nonexistent)

    with caplog.at_level(logging.WARNING, logger="llm_subprocess"):
        result = llm_subprocess._load_pricing_table()

    assert result == {}, (
        f"Expected _load_pricing_table() to return {{}} on missing file, got {result!r}"
    )
    warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warning_records) >= 1, (
        f"Expected >=1 WARNING log record when models.json is missing; "
        f"got {len(warning_records)} WARNING records. "
        f"All caplog records: {[(r.levelno, r.getMessage()) for r in caplog.records]!r}. "
        f"GREEN must add logger.warning() in the except handler (§2.1)."
    )


# ---------------------------------------------------------------------------
# AC2: valid JSON but empty claude.pricing → WARNING emitted AND returns {}
# ---------------------------------------------------------------------------

def test_ac2_empty_pricing_block_emits_warning_and_returns_empty(
    monkeypatch, tmp_path, caplog
):
    """AC2: when models.json exists but claude.pricing is {}, _load_pricing_table()
    emits >=1 WARNING and returns {}.

    Pre-GREEN FAIL reason: the file loads cleanly (no exception), the parse loop
    produces an empty table, but there is no post-loop logger.warning() call →
    0 WARNING records → assert fails.

    Seam: write a fixture models.json with empty pricing block; point both
    HAL_DIR env and config_provider.models_config_path at it.
    """
    shared_config = tmp_path / "SHARED" / "config"
    shared_config.mkdir(parents=True)
    fixture_path = shared_config / "models.json"
    fixture_path.write_text(json.dumps({"claude": {"pricing": {}}}))

    monkeypatch.setenv("HAL_DIR", str(tmp_path))
    monkeypatch.setattr(config_provider, "models_config_path", lambda: fixture_path)

    with caplog.at_level(logging.WARNING, logger="llm_subprocess"):
        result = llm_subprocess._load_pricing_table()

    assert result == {}, (
        f"Expected _load_pricing_table() to return {{}} on empty pricing block, "
        f"got {result!r}"
    )
    warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warning_records) >= 1, (
        f"Expected >=1 WARNING log record when claude.pricing is empty; "
        f"got {len(warning_records)} WARNING records. "
        f"All caplog records: {[(r.levelno, r.getMessage()) for r in caplog.records]!r}. "
        f"GREEN must add logger.warning() after the parse loop when table is empty (§2.2)."
    )


# ---------------------------------------------------------------------------
# AC3: valid pricing block → non-empty dict AND ZERO WARNING records (guard)
# ---------------------------------------------------------------------------

def test_ac3_valid_pricing_returns_nonempty_dict_with_zero_warnings(
    monkeypatch, tmp_path, caplog
):
    """AC3 (behavior GUARD — expected to PASS pre-GREEN): when models.json has a
    valid pricing block, _load_pricing_table() returns a non-empty {alias:(float,float)}
    dict and emits ZERO WARNING records.

    This guard ensures GREEN does not introduce over-warning on the happy path (§2.3).

    Seam: same as AC1/AC2 — monkeypatch HAL_DIR and config_provider.models_config_path.
    No UUT patching.
    """
    shared_config = tmp_path / "SHARED" / "config"
    shared_config.mkdir(parents=True)
    fixture_path = shared_config / "models.json"
    fixture_pricing = {
        "claude": {
            "pricing": {
                "opus":   {"in": 5.0,  "out": 25.0},
                "sonnet": {"in": 3.0,  "out": 15.0},
                "haiku":  {"in": 1.0,  "out":  5.0},
            }
        }
    }
    fixture_path.write_text(json.dumps(fixture_pricing))

    monkeypatch.setenv("HAL_DIR", str(tmp_path))
    monkeypatch.setattr(config_provider, "models_config_path", lambda: fixture_path)

    with caplog.at_level(logging.WARNING, logger="llm_subprocess"):
        result = llm_subprocess._load_pricing_table()

    assert isinstance(result, dict), (
        f"Expected _load_pricing_table() to return a dict, got {type(result)!r}"
    )
    assert len(result) > 0, (
        f"Expected non-empty pricing table from valid fixture models.json, got: {result!r}"
    )
    for alias, rates in result.items():
        assert isinstance(alias, str), f"Alias key must be str, got {alias!r}"
        assert isinstance(rates, tuple) and len(rates) == 2, (
            f"Rates for {alias!r} must be (in_rate, out_rate) tuple, got {rates!r}"
        )
        in_rate, out_rate = rates
        assert isinstance(in_rate, float) and isinstance(out_rate, float), (
            f"Rates must be floats: in={in_rate!r} out={out_rate!r}"
        )

    warning_records = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warning_records) == 0, (
        f"Expected ZERO WARNING records on valid pricing block (happy path must stay silent); "
        f"got {len(warning_records)}: "
        f"{[(r.levelno, r.getMessage()) for r in warning_records]!r}"
    )
