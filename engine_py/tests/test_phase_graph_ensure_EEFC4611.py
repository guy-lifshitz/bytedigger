"""RED tests for EEFC4611 — phase wiring (AC8, AC9).

UUTs: _invoke_discovery_llm (phase_1_discovery), _invoke_explore_llm (phase_2_explore).
Collaborator under test: ensure_graph is PATCHED here (not a UUT in this file).

Pre-GREEN predict: BOTH tests FAIL.
  AC8: patch("phase_1_discovery.ensure_graph") raises AttributeError — ensure_graph not
       yet imported into phase_1_discovery namespace; extra_data["graph_source"] absent.
  AC9: same for phase_2_explore.

§1q: conftest-import-time singleton provides sys.path. No sys.path.insert here.
D1CF5FDF: phase module imports via importlib.import_module inside test bodies so any
  collection-time issues (missing attribute) become assert-time failures.
§1l stub-passability: patching ensure_graph (collaborator) is valid here; the UUT is
  the wiring logic inside _invoke_discovery_llm/_invoke_explore_llm that CALLS ensure_graph
  and threads result into extra_data — that wiring does NOT exist yet (verified by grep).
  A trivial stub of ensure_graph does NOT satisfy these tests alone; the UUT must actually
  call ensure_graph and forward its return into extra_data["graph_source"].
§1i: no singleton resources; all patches are deterministic.
"""
from __future__ import annotations

import importlib
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# conftest.py inserts ENGINE_ROOT + ENGINE_ROOT/workflows onto sys.path at import time.
# No sys.path.insert here (§1q / 81F97F3D gate).

from contracts import StepResult, WorkflowContext  # noqa: E402  # available via conftest path


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

_SENTINEL = "EEFC4611_SENTINEL"  # returned by patched ensure_graph


def _make_ctx(tmp_path: Path) -> WorkflowContext:
    (tmp_path / "injection").mkdir(parents=True, exist_ok=True)
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={"scratchpad_dir": str(tmp_path)},
        question="test question for EEFC4611 phase wiring",
        session_id="test-EEFC4611",
        persona="hal",
        framework=None,
        domain=None,
    )


def _ok_prev_discovery(tmp_path: Path) -> StepResult:
    """Build a non-skipped prev StepResult that passes _invoke_discovery_llm validation."""
    return StepResult(
        status="ok",
        data={
            "prompt": "test discovery prompt",
            "doc_path": str(tmp_path / "discovery.md"),
            "complexity": "SIMPLE",
        },
        duration_ms=0,
        step_name="build_discovery_prompt",
    )


def _ok_prev_explore(tmp_path: Path) -> StepResult:
    """Build a non-skipped prev StepResult that passes _invoke_explore_llm validation.
    Must NOT have data["skipped"] truthy (passthrough_if_skipped guard).
    """
    return StepResult(
        status="ok",
        data={
            "prompt": "test explore prompt",
            "doc_path": str(tmp_path / "exploration.md"),
            "complexity": "FEATURE",
        },
        duration_ms=0,
        step_name="build_explore_prompt",
    )


def _mock_invoke_ok() -> MagicMock:
    """Return a mock invoke_llm_subprocess that records kwargs and returns a valid StepResult."""
    return MagicMock(return_value=StepResult(
        status="ok",
        data={"raw_response": "OK", "response_bytes": 2, "command": ["x"]},
        duration_ms=0,
        step_name="mock",
    ))


# ---------------------------------------------------------------------------
# AC8: _invoke_discovery_llm calls ensure_graph(str(HAL_DIR)) and threads result
#      into extra_data["graph_source"]
#
# §1y Point→Host→Test:
#   Point  = the `graph_src = ensure_graph(str(HAL_DIR))` line (to be added in GREEN)
#            + `extra_data["graph_source": graph_src]` threading
#   Host   = _invoke_discovery_llm in phase_1_discovery (runs at test time)
#   Test   = patch phase_1_discovery.ensure_graph→sentinel,
#            patch phase_1_discovery.invoke_llm_subprocess→capture kwargs,
#            assert ensure_graph called once AND extra_data["graph_source"]==sentinel
#
# Pre-GREEN FAIL: patch("phase_1_discovery.ensure_graph") raises AttributeError
#   because ensure_graph is NOT yet imported/bound in phase_1_discovery namespace.
#   Even if patch somehow succeeded, extra_data has no "graph_source" key today
#   (current prod: extra_data={"doc_path":..., "complexity":...}).
# ---------------------------------------------------------------------------

def test_ac8_discovery_llm_calls_ensure_graph_and_threads_result_EEFC4611(tmp_path):
    """AC8: _invoke_discovery_llm calls ensure_graph(str(HAL_DIR)) exactly once before
    invoke_llm_subprocess, and extra_data['graph_source'] == the value ensure_graph returned.

    Pre-GREEN FAIL: AttributeError on patch (ensure_graph not in phase_1_discovery namespace)
    OR assert on extra_data["graph_source"] fails (key absent in current prod code).
    """
    phase_1 = importlib.import_module("phase_1_discovery")

    ctx = _make_ctx(tmp_path)
    prev = _ok_prev_discovery(tmp_path)

    mock_invoke = _mock_invoke_ok()
    ensure_graph_calls = []

    def fake_ensure_graph(repo_root: str) -> str:
        ensure_graph_calls.append(repo_root)
        return _SENTINEL

    # Patch ensure_graph as it will be imported in phase_1_discovery namespace.
    # Pre-GREEN: this patch raises AttributeError → test FAILs at assert time (patch target absent).
    with patch("phase_1_discovery.ensure_graph", side_effect=fake_ensure_graph), \
         patch("phase_1_discovery.invoke_llm_subprocess", mock_invoke):
        phase_1._invoke_discovery_llm(ctx, prev)

    # Assert ensure_graph was called exactly once (any repo_root str is acceptable —
    # the spec says str(HAL_DIR) but we can't import HAL_DIR here without risk;
    # we assert call-count + return threading, not exact arg value).
    assert len(ensure_graph_calls) == 1, (
        f"AC8 FAIL: ensure_graph called {len(ensure_graph_calls)} time(s), expected 1.\n"
        "phase_1_discovery._invoke_discovery_llm does not call ensure_graph yet."
    )

    # Assert the sentinel landed in extra_data["graph_source"].
    assert mock_invoke.call_count >= 1, (
        "AC8 FAIL: invoke_llm_subprocess was never called"
    )
    _, kwargs = mock_invoke.call_args_list[0]
    extra_data = kwargs.get("extra_data", {})
    assert "graph_source" in extra_data, (
        f"AC8 FAIL: extra_data missing 'graph_source' key.\n"
        f"Current extra_data keys: {list(extra_data.keys())}\n"
        "GREEN must add: extra_data['graph_source'] = ensure_graph(str(HAL_DIR))"
    )
    assert extra_data["graph_source"] == _SENTINEL, (
        f"AC8 FAIL: extra_data['graph_source'] == {extra_data['graph_source']!r}, "
        f"expected sentinel {_SENTINEL!r}"
    )


# ---------------------------------------------------------------------------
# AC9: _invoke_explore_llm calls ensure_graph(str(HAL_DIR)) and threads result
#      into extra_data["graph_source"]
#
# §1y Point→Host→Test:
#   Point  = the `graph_src = ensure_graph(str(HAL_DIR))` line (to be added in GREEN)
#            + `extra_data["graph_source": graph_src]` threading
#   Host   = _invoke_explore_llm in phase_2_explore (runs at test time)
#   Test   = patch phase_2_explore.ensure_graph→sentinel,
#            patch phase_2_explore.invoke_llm_subprocess→capture kwargs,
#            assert ensure_graph called once AND extra_data["graph_source"]==sentinel
#
# Pre-GREEN FAIL: AttributeError on patch (ensure_graph not yet in phase_2_explore namespace)
#   OR assert on extra_data["graph_source"] fails (key absent in current prod code).
# passthrough_if_skipped guard: prev has no "skipped" key → guard returns None → proceeds.
# ---------------------------------------------------------------------------

def test_ac9_explore_llm_calls_ensure_graph_and_threads_result_EEFC4611(tmp_path):
    """AC9: _invoke_explore_llm calls ensure_graph(str(HAL_DIR)) exactly once before
    invoke_llm_subprocess, and extra_data['graph_source'] == the value ensure_graph returned.

    Pre-GREEN FAIL: AttributeError on patch (ensure_graph not in phase_2_explore namespace)
    OR assert on extra_data["graph_source"] fails (key absent in current prod code).
    """
    phase_2 = importlib.import_module("phase_2_explore")

    ctx = _make_ctx(tmp_path)
    prev = _ok_prev_explore(tmp_path)

    mock_invoke = _mock_invoke_ok()
    ensure_graph_calls = []

    def fake_ensure_graph(repo_root: str) -> str:
        ensure_graph_calls.append(repo_root)
        return _SENTINEL

    # Patch ensure_graph as it will be imported in phase_2_explore namespace.
    # Pre-GREEN: raises AttributeError → test FAILs at assert time.
    with patch("phase_2_explore.ensure_graph", side_effect=fake_ensure_graph), \
         patch("phase_2_explore.invoke_llm_subprocess", mock_invoke):
        phase_2._invoke_explore_llm(ctx, prev)

    # Assert ensure_graph was called exactly once.
    assert len(ensure_graph_calls) == 1, (
        f"AC9 FAIL: ensure_graph called {len(ensure_graph_calls)} time(s), expected 1.\n"
        "phase_2_explore._invoke_explore_llm does not call ensure_graph yet."
    )

    # Assert the sentinel landed in extra_data["graph_source"].
    assert mock_invoke.call_count >= 1, (
        "AC9 FAIL: invoke_llm_subprocess was never called"
    )
    _, kwargs = mock_invoke.call_args_list[0]
    extra_data = kwargs.get("extra_data", {})
    assert "graph_source" in extra_data, (
        f"AC9 FAIL: extra_data missing 'graph_source' key.\n"
        f"Current extra_data keys: {list(extra_data.keys())}\n"
        "GREEN must add: extra_data['graph_source'] = ensure_graph(str(HAL_DIR))"
    )
    assert extra_data["graph_source"] == _SENTINEL, (
        f"AC9 FAIL: extra_data['graph_source'] == {extra_data['graph_source']!r}, "
        f"expected sentinel {_SENTINEL!r}"
    )
