"""RED tests for F3A8F4FC — Phase 1/2 Opus→Sonnet downgrade.

Adds dedicated `discovery` and `explore` role keys to models.json + model_config.py,
and repoints phase_1_discovery / phase_2_explore to the new accessors.

Agreement: F3A8F4FC-7191-4138-BB2B-7DCF36BE6CA8
Date: 2026-05-20

These tests MUST FAIL until GREEN implements:
  - SHARED/config/models.json: add "discovery"/"explore" keys
  - engine_py/lib/model_config.py: add FALLBACK_CONFIG entries + 2 new accessors
  - engine_py/workflows/phase_1_discovery.py: repoint import + call
  - engine_py/workflows/phase_2_explore.py: repoint import + call

Expected pre-GREEN failure summary:
  AC1  FAIL — "discovery"/"explore" keys absent from models.json
  AC2  FAIL — FALLBACK_CONFIG lacks "discovery"/"explore" entries
  AC3  FAIL — AttributeError: module 'model_config' has no attribute 'get_claude_discovery'
  AC4  FAIL — AttributeError: module 'model_config' has no attribute 'get_claude_explore'
  AC5  FAIL — same AttributeError path (fallback test of get_claude_discovery)
  AC6  FAIL — same AttributeError path (fallback test of get_claude_explore)
  AC7  FAIL — phase_1_discovery still imports get_claude_spec_writer, not get_claude_discovery
  AC8  FAIL — phase_2_explore still imports get_claude_critical, not get_claude_explore
  AC9  # REGRESSION GUARD — get_claude_critical() == "opus" likely passes today; import
       # assertions also pass (files haven't changed). Expected to PASS pre- and post-GREEN.
  AC10 # REGRESSION GUARD — get_claude_spec_writer() == "opus" likely passes today; import
       # assertions also pass. Expected to PASS pre- and post-GREEN.
"""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "lib"))

import model_config  # noqa: E402
from tree_root import resolve_tree_root  # noqa: E402

# bd#97: resolved from tree content, not an ancestor count. A fixed parents[5]
# is correct only in the upstream HAL layout and IndexErrors at import time on a
# shallower checkout, which took the whole suite down at collection.
REAL_CONFIG_PATH = (
    resolve_tree_root(Path(__file__).resolve()) / "SHARED" / "config" / "models.json"
)
WORKFLOWS_DIR = HERE.parent / "workflows"


@pytest.fixture(autouse=True)
def _reset_cache_between_tests():
    """Each test starts with a clean model_config cache."""
    model_config.reset_cache()
    yield
    model_config.reset_cache()


# ─── AC1: models.json contains "discovery" and "explore" keys ─────────────────

def test_ac1_models_json_has_discovery_and_explore_keys():
    """AC1: SHARED/config/models.json 'claude' object contains
    'discovery': 'sonnet' AND 'explore': 'sonnet'. Other 4 keys unchanged.

    PRE-GREEN FAILURE: keys do not yet exist in models.json.
    """
    if not REAL_CONFIG_PATH.exists():
        pytest.skip("env-precondition #489: SHARED/config/models.json absent at repo root")
    raw = json.loads(REAL_CONFIG_PATH.read_text())
    claude_cfg = raw["claude"]

    # New keys — these fail today
    assert claude_cfg.get("discovery") == "sonnet", (
        f"Expected claude.discovery='sonnet', got {claude_cfg.get('discovery')!r}"
    )
    assert claude_cfg.get("explore") == "sonnet", (
        f"Expected claude.explore='sonnet', got {claude_cfg.get('explore')!r}"
    )

    # Existing 4 keys unchanged (regression guard within same assertion)
    assert claude_cfg.get("primary") == "sonnet"
    assert claude_cfg.get("fallback") == "haiku"
    assert claude_cfg.get("critical") == "opus"
    assert claude_cfg.get("spec_writer") == "opus"


# ─── AC2: FALLBACK_CONFIG has "discovery" and "explore" entries ───────────────

def test_ac2_fallback_config_has_discovery_and_explore():
    """AC2: model_config.FALLBACK_CONFIG['claude'] contains
    'discovery': 'sonnet' AND 'explore': 'sonnet'. Other 4 unchanged.

    PRE-GREEN FAILURE: FALLBACK_CONFIG does not yet have these keys.
    """
    fallback_claude = model_config.FALLBACK_CONFIG["claude"]

    assert fallback_claude.get("discovery") == "sonnet", (
        f"Expected FALLBACK_CONFIG['claude']['discovery']='sonnet', "
        f"got {fallback_claude.get('discovery')!r}"
    )
    assert fallback_claude.get("explore") == "sonnet", (
        f"Expected FALLBACK_CONFIG['claude']['explore']='sonnet', "
        f"got {fallback_claude.get('explore')!r}"
    )

    # Unchanged entries
    assert fallback_claude.get("primary") == "sonnet"
    assert fallback_claude.get("fallback") == "haiku"
    assert fallback_claude.get("critical") == "opus"
    assert fallback_claude.get("spec_writer") == "opus"


# ─── AC3: get_claude_discovery() returns "sonnet" (FORCING FUNCTION) ──────────

def test_ac3_get_claude_discovery_returns_sonnet(monkeypatch, tmp_path):
    """AC3: get_claude_discovery() returns 'sonnet' on stock models.json.
    Forcing function per §1l — a stub returning '' or 'opus' MUST fail.

    PRE-GREEN FAILURE: AttributeError — function does not exist yet.
    """
    # Point at a canonical stock config with the new keys
    cfg = {
        "claude": {
            "primary": "sonnet",
            "fallback": "haiku",
            "critical": "opus",
            "spec_writer": "opus",
            "discovery": "sonnet",
            "explore": "sonnet",
        }
    }
    cfg_path = tmp_path / "models.json"
    cfg_path.write_text(json.dumps(cfg))
    monkeypatch.setattr(model_config, "_CONFIG_PATH", cfg_path)
    model_config.reset_cache()

    result = model_config.get_claude_discovery()
    assert result == "sonnet", (
        f"get_claude_discovery() must return exactly 'sonnet', got {result!r}"
    )


# ─── AC4: get_claude_explore() returns "sonnet" (FORCING FUNCTION) ────────────

def test_ac4_get_claude_explore_returns_sonnet(monkeypatch, tmp_path):
    """AC4: get_claude_explore() returns 'sonnet' on stock models.json.
    Forcing function per §1l — a stub returning '' or 'opus' MUST fail.

    PRE-GREEN FAILURE: AttributeError — function does not exist yet.
    """
    cfg = {
        "claude": {
            "primary": "sonnet",
            "fallback": "haiku",
            "critical": "opus",
            "spec_writer": "opus",
            "discovery": "sonnet",
            "explore": "sonnet",
        }
    }
    cfg_path = tmp_path / "models.json"
    cfg_path.write_text(json.dumps(cfg))
    monkeypatch.setattr(model_config, "_CONFIG_PATH", cfg_path)
    model_config.reset_cache()

    result = model_config.get_claude_explore()
    assert result == "sonnet", (
        f"get_claude_explore() must return exactly 'sonnet', got {result!r}"
    )


# ─── AC5: get_claude_discovery() fallback on missing file ─────────────────────

def test_ac5_get_claude_discovery_fallback_on_missing_file(monkeypatch, tmp_path):
    """AC5: get_claude_discovery() falls back to 'sonnet' on missing models.json.
    Mirrors test_fallback_on_missing_file for existing 4 accessors.

    PRE-GREEN FAILURE: AttributeError — function does not exist yet.
    """
    nonexistent = tmp_path / "does_not_exist.json"
    assert not nonexistent.exists()
    monkeypatch.setattr(model_config, "_CONFIG_PATH", nonexistent)
    model_config.reset_cache()

    result = model_config.get_claude_discovery()
    assert result == "sonnet", (
        f"get_claude_discovery() must fall back to 'sonnet', got {result!r}"
    )


# ─── AC6: get_claude_explore() fallback on missing file ───────────────────────

def test_ac6_get_claude_explore_fallback_on_missing_file(monkeypatch, tmp_path):
    """AC6: get_claude_explore() falls back to 'sonnet' on missing models.json.

    PRE-GREEN FAILURE: AttributeError — function does not exist yet.
    """
    nonexistent = tmp_path / "does_not_exist.json"
    assert not nonexistent.exists()
    monkeypatch.setattr(model_config, "_CONFIG_PATH", nonexistent)
    model_config.reset_cache()

    result = model_config.get_claude_explore()
    assert result == "sonnet", (
        f"get_claude_explore() must fall back to 'sonnet', got {result!r}"
    )


# ─── AC7: phase_1_discovery uses get_claude_discovery (FORCING FUNCTION) ──────

def test_ac7_phase1_discovery_uses_get_claude_discovery(monkeypatch, tmp_path):
    """AC7: phase_1_discovery._default_llm_command() returns exactly
    ['claude', '-p', '--model', 'sonnet'] AND the module imports get_claude_discovery
    (not get_claude_spec_writer).

    Forcing function: a stub that changes only the return value but leaves the
    import as get_claude_spec_writer must still fail the import-path assertion.

    PRE-GREEN FAILURE (two sub-checks):
      1. Import-path check: phase_1_discovery.py still imports get_claude_spec_writer
      2. Return-value check: _default_llm_command() may currently return
         ['claude', '-p', '--model', 'opus'] (via get_claude_spec_writer) so the
         exact argv assertion also fails.
    """
    # --- Import-path check: inspect module source for the new import ---
    phase1_path = WORKFLOWS_DIR / "phase_1_discovery.py"
    source = phase1_path.read_text()

    # Must import get_claude_discovery (not get_claude_spec_writer)
    assert "get_claude_discovery" in source, (
        "phase_1_discovery.py must import 'get_claude_discovery' (F3A8F4FC). "
        "Currently imports 'get_claude_spec_writer'."
    )
    assert "get_claude_spec_writer" not in source, (
        "phase_1_discovery.py must NOT import 'get_claude_spec_writer' after F3A8F4FC. "
        "The old import is still present."
    )

    # --- Return-value check: wire a stock models.json with discovery=sonnet ---
    cfg = {
        "claude": {
            "primary": "sonnet",
            "fallback": "haiku",
            "critical": "opus",
            "spec_writer": "opus",
            "discovery": "sonnet",
            "explore": "sonnet",
        }
    }
    cfg_path = tmp_path / "models.json"
    cfg_path.write_text(json.dumps(cfg))
    monkeypatch.setattr(model_config, "_CONFIG_PATH", cfg_path)
    model_config.reset_cache()

    # Import phase_1_discovery lazily inside the test to avoid module-level side-effects
    # contaminating other tests (per §1q / FBB5EB25).
    if "phase_1_discovery" in sys.modules:
        del sys.modules["phase_1_discovery"]
    sys.path.insert(0, str(WORKFLOWS_DIR))
    import phase_1_discovery  # noqa: E402

    result = phase_1_discovery._default_llm_command()
    assert result == ["claude", "-p", "--model", "sonnet"], (
        f"phase_1_discovery._default_llm_command() must return "
        f"['claude', '-p', '--model', 'sonnet'], got {result!r}"
    )


# ─── AC8: phase_2_explore uses get_claude_explore (FORCING FUNCTION) ──────────

def test_ac8_phase2_explore_uses_get_claude_explore(monkeypatch, tmp_path):
    """AC8: phase_2_explore._default_llm_command() returns exactly
    ['claude', '-p', '--model', 'sonnet'] AND the module imports get_claude_explore
    (not get_claude_critical).

    Forcing function: a stub that only changes the return value but leaves the
    import as get_claude_critical must still fail the import-path assertion.

    PRE-GREEN FAILURE (two sub-checks):
      1. Import-path check: phase_2_explore.py still imports get_claude_critical
      2. Return-value check: _default_llm_command() currently returns opus via critical
    """
    # --- Import-path check: inspect module source for the new import ---
    phase2_path = WORKFLOWS_DIR / "phase_2_explore.py"
    source = phase2_path.read_text()

    # Must import get_claude_explore (not get_claude_critical)
    assert "get_claude_explore" in source, (
        "phase_2_explore.py must import 'get_claude_explore' (F3A8F4FC). "
        "Currently imports 'get_claude_critical'."
    )
    assert "get_claude_critical" not in source, (
        "phase_2_explore.py must NOT import 'get_claude_critical' after F3A8F4FC. "
        "The old import is still present."
    )

    # --- Return-value check: wire a stock models.json with explore=sonnet ---
    cfg = {
        "claude": {
            "primary": "sonnet",
            "fallback": "haiku",
            "critical": "opus",
            "spec_writer": "opus",
            "discovery": "sonnet",
            "explore": "sonnet",
        }
    }
    cfg_path = tmp_path / "models.json"
    cfg_path.write_text(json.dumps(cfg))
    monkeypatch.setattr(model_config, "_CONFIG_PATH", cfg_path)
    model_config.reset_cache()

    # Import lazily to avoid module-level side-effects (§1q / FBB5EB25).
    if "phase_2_explore" in sys.modules:
        del sys.modules["phase_2_explore"]
    sys.path.insert(0, str(WORKFLOWS_DIR))
    import phase_2_explore  # noqa: E402

    result = phase_2_explore._default_llm_command()
    assert result == ["claude", "-p", "--model", "sonnet"], (
        f"phase_2_explore._default_llm_command() must return "
        f"['claude', '-p', '--model', 'sonnet'], got {result!r}"
    )


# ─── AC9: REGRESSION GUARD — critical phases still use opus ───────────────────

def test_ac9_critical_phases_still_use_opus():
    """AC9: REGRESSION GUARD — get_claude_critical() resolves to 'opus' AND
    phase_5_implement.py, phase_5_integrity.py, phase_6_review.py,
    phase_6_fix_integrity.py, phase_45_spec.py, phase_45_spec_lite.py
    all still import get_claude_critical (never downgraded in this ship).

    # REGRESSION GUARD: these assertions are expected to PASS today (pre-GREEN)
    # because the production files have NOT changed yet. They remain as a hard
    # guard to ensure GREEN does not accidentally touch these files.
    # Both pre-GREEN and post-GREEN: PASS.
    """
    # Accessor returns "opus"
    assert model_config.get_claude_critical() == "opus", (
        f"get_claude_critical() must return 'opus', got {model_config.get_claude_critical()!r}"
    )

    # Files that MUST retain the get_claude_critical import
    critical_files = [
        WORKFLOWS_DIR / "phase_5_implement.py",
        WORKFLOWS_DIR / "phase_5_integrity.py",
        WORKFLOWS_DIR / "phase_6_review.py",
        WORKFLOWS_DIR / "phase_6_fix_integrity.py",
    ]
    # NOTE: phase_45_spec.py and phase_45_spec_lite.py also use get_claude_critical
    # for their self-review paths (AC9 spec). Verified they appear in the grep above.
    # Adding them here for completeness.
    critical_files += [
        WORKFLOWS_DIR / "phase_45_spec.py",
        WORKFLOWS_DIR / "phase_45_spec_lite.py",
    ]

    for fpath in critical_files:
        source = fpath.read_text()
        assert "from model_config import get_claude_critical" in source, (
            f"{fpath.name} must still contain "
            f"'from model_config import get_claude_critical' (F3A8F4FC out-of-scope guard)"
        )


# ─── AC10: REGRESSION GUARD — spec writer phases still use opus ───────────────

def test_ac10_spec_writer_phases_still_use_opus():
    """AC10: REGRESSION GUARD — get_claude_spec_writer() resolves to 'opus' AND
    phase_45_spec.py and phase_45_spec_lite.py still import get_claude_spec_writer.

    # REGRESSION GUARD: expected to PASS today (pre-GREEN) and post-GREEN.
    # This guard ensures the spec_writer key is not altered by this ship.
    """
    # Accessor returns "opus"
    assert model_config.get_claude_spec_writer() == "opus", (
        f"get_claude_spec_writer() must return 'opus', "
        f"got {model_config.get_claude_spec_writer()!r}"
    )

    # Files that MUST retain the get_claude_spec_writer import
    spec_writer_files = [
        WORKFLOWS_DIR / "phase_45_spec.py",
        WORKFLOWS_DIR / "phase_45_spec_lite.py",
    ]
    for fpath in spec_writer_files:
        source = fpath.read_text()
        assert "get_claude_spec_writer" in source, (
            f"{fpath.name} must still contain "
            f"'get_claude_spec_writer' import (F3A8F4FC out-of-scope guard)"
        )
