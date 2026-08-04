"""RED tests for 3E6EBDF4 — engine_py conftest env-pin HAL_RUNNER_BACKEND.

Spec: SHARED/memory/Decisions/2026-05-25_3E6EBDF4_conftest_envpin_spec.md
Agreement: 3E6EBDF4 (PROCESS — conftest autouse fixture prerequisite for DA1174AE)

AC coverage:
  AC1: FAIL pre-GREEN (env unset -> KeyError)
  AC2: PASS pre-GREEN (env already absent; monkeypatch.delenv is a no-op)
  AC3: PASS pre-GREEN (monkeypatch.setenv wins; no conftest interference)
  AC4a: PASS pre-GREEN (monkeypatch.setenv works in isolation)
  AC4b: FAIL pre-GREEN (no pin to revert to; env unset after AC4a teardown)
  AC5: FAIL pre-GREEN (_resolve_backend returns ("claude-subprocess","default") not "env")
  AC7: PASS pre-GREEN (collection-safe placeholder)

Expected pre-GREEN: 4 PASS (AC2, AC3, AC4a, AC7) / 3 FAIL (AC1, AC4b, AC5)

§1q: NO spec_from_file_location / module_from_spec / exec_module used.
§1i: No singleton-resource / timing fixtures.
§1i N/A: no contested singleton resource.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# AC1 — pin present by default (no monkeypatch in body)
# ---------------------------------------------------------------------------

def test_ac1_env_pinned_default():
    """AC1: Autouse conftest fixture sets HAL_RUNNER_BACKEND=claude-subprocess.

    Pre-GREEN: FAIL — no conftest exists, env unset -> KeyError.
    Post-GREEN: PASS — conftest autouse fixture sets the env before this runs.
    """
    assert os.environ["HAL_RUNNER_BACKEND"] == "claude-subprocess", (
        "HAL_RUNNER_BACKEND must be pinned to 'claude-subprocess' by the "
        "autouse conftest fixture. Got: "
        + os.environ.get("HAL_RUNNER_BACKEND", "<KEY NOT SET>")
    )


# ---------------------------------------------------------------------------
# AC2 — override by delete (monkeypatch.delenv unwinds the pin)
# ---------------------------------------------------------------------------

def test_ac2_delenv_override(monkeypatch):
    """AC2: monkeypatch.delenv removes the pin; env var becomes absent.

    Pre-GREEN: PASS (no conftest set; already absent; raising=False suppresses error).
    Post-GREEN: PASS (delenv unwinds the autouse pin; env var absent).
    """
    monkeypatch.delenv("HAL_RUNNER_BACKEND", raising=False)
    assert "HAL_RUNNER_BACKEND" not in os.environ, (
        "After monkeypatch.delenv the env var must be absent."
    )


# ---------------------------------------------------------------------------
# AC3 — override by set (monkeypatch.setenv wins over autouse pin)
# ---------------------------------------------------------------------------

def test_ac3_setenv_override(monkeypatch):
    """AC3: monkeypatch.setenv to a different value overrides the autouse pin.

    Pre-GREEN: PASS (no conftest interference; setenv wins unconditionally).
    Post-GREEN: PASS (latest monkeypatch call wins; override takes effect).
    """
    monkeypatch.setenv("HAL_RUNNER_BACKEND", "claude-in-session")
    assert os.environ["HAL_RUNNER_BACKEND"] == "claude-in-session", (
        "monkeypatch.setenv('HAL_RUNNER_BACKEND', 'claude-in-session') must "
        "override the autouse pin."
    )


# ---------------------------------------------------------------------------
# AC4 — no leak between tests (two ordered functions)
# ---------------------------------------------------------------------------

def test_ac4a_set_in_session(monkeypatch):
    """AC4 part A: Override set to claude-in-session within this test's scope.

    Pre-GREEN: PASS (monkeypatch.setenv always works).
    Post-GREEN: PASS (same).
    """
    monkeypatch.setenv("HAL_RUNNER_BACKEND", "claude-in-session")
    assert os.environ["HAL_RUNNER_BACKEND"] == "claude-in-session", (
        "AC4a: env var must reflect the setenv override."
    )


def test_ac4b_pin_restored():
    """AC4 part B: After AC4a's monkeypatch teardown, pin is back to claude-subprocess.

    Alphabetically after test_ac4a (b > a), so pytest collects it second.
    No monkeypatch arg here — relies solely on the autouse conftest pin.

    Pre-GREEN: FAIL — no conftest; env is unset after AC4a teardown -> KeyError.
    Post-GREEN: PASS — autouse pin restores 'claude-subprocess' on each test entry.
    """
    assert os.environ["HAL_RUNNER_BACKEND"] == "claude-subprocess", (
        "AC4b: After AC4a teardown the autouse pin must restore 'claude-subprocess'. "
        "Got: " + os.environ.get("HAL_RUNNER_BACKEND", "<KEY NOT SET>")
    )


# ---------------------------------------------------------------------------
# AC5 — _resolve_backend reads from env (source == "env", not "default")
# ---------------------------------------------------------------------------

def test_ac5_resolve_backend_source_env():
    """AC5: _resolve_backend(None, os.environ) returns ("claude-subprocess", "env").

    The autouse pin sets HAL_RUNNER_BACKEND in os.environ, so _resolve_backend
    must pick it up via the env-read path -> source="env", not source="default".

    Pre-GREEN: FAIL — no conftest; env unset; returns ("claude-subprocess","default").
    Post-GREEN: PASS — autouse pin sets env; returns ("claude-subprocess","env").

    Import is inside function body to avoid module-level sys.path mutation
    (anti-pattern 81F97F3D / §1q). ENGINE_ROOT is on sys.path via the existing
    grandfathered conftest-import-time pattern that GREEN's conftest.py will
    establish; if not available pre-GREEN this test will ImportError (also a FAIL).
    """
    _ENGINE_ROOT = str(Path(__file__).parent.parent)
    if _ENGINE_ROOT not in sys.path:
        sys.path.insert(0, _ENGINE_ROOT)

    from bytedigger_engine.llm_subprocess import _resolve_backend  # type: ignore[import]

    result = _resolve_backend(None, os.environ)
    assert result == ("claude-subprocess", "env"), (
        f"AC5: _resolve_backend(None, os.environ) must return "
        f"('claude-subprocess', 'env') when env pin is active. Got: {result!r}"
    )


# ---------------------------------------------------------------------------
# AC7 — collection safety (trivial placeholder)
# ---------------------------------------------------------------------------

def test_ac7_collection_safe():
    """AC7: Module imports and collects without errors.

    Pre-GREEN: PASS. Post-GREEN: PASS.
    Proves the conftest landing does not break collection.
    """
    assert True
