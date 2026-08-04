"""RED tests for DA48BEAC graphify plugin — phase wiring (AC6, AC7, AC8, AC9, AC10).

AC coverage:
    test_ac6_discovery_allowed_tools_contains_graphify_shim  → AC6
    test_ac7_explore_allowed_tools_contains_graphify_shim    → AC7
    test_ac10_grep_fallback_tools_preserved_discovery        → AC10 (discovery half)
    test_ac10_grep_fallback_tools_preserved_explore          → AC10 (explore half)
    test_ac8_discovery_prompt_contains_graph_first_directive → AC8
    test_ac9_explore_prompt_contains_graph_first_directive   → AC9

AC6, AC7, AC8, AC9 FAIL today (shim token and graph-first directive absent).
AC10 PASSES today (Read/Grep/Glob already present) — correctness guard.

Conftest-import-time singleton provides sys.path (§1q / 81F97F3D).
No module-level sys.path manipulation here.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# sys.path is set by conftest.py (conftest-import-time singleton).
# Do NOT add sys.path.insert here — violates 81F97F3D gate.
from bytedigger_engine.contracts import StepResult, WorkflowContext  # noqa: E402

_SHIM_TOKEN = "Bash(graphify-shim.sh:*)"
_GRAPH_QUERY_DIRECTIVE = "graphify query"
_GRAPH_EVIDENCE_HEADER = "### GRAPH-FIRST EVIDENCE"


# ─── Shared helpers ───────────────────────────────────────────────────────────


def _make_ctx(tmp_path: Path) -> WorkflowContext:
    (tmp_path / "injection").mkdir(parents=True, exist_ok=True)
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={"scratchpad_dir": str(tmp_path)},
        question="test question for graphify wiring",
        session_id="test-DA48BEAC",
        persona="hal",
        framework=None,
        domain=None,
    )


def _ok_result(**data) -> StepResult:
    return StepResult(status="ok", data=data, duration_ms=0, step_name="prev")


def _mock_invoke_ok() -> MagicMock:
    return MagicMock(return_value=StepResult(
        status="ok",
        data={"raw_response": "OK", "response_bytes": 2, "command": ["x"]},
        duration_ms=0,
        step_name="mock",
    ))


# ─── AC6: discovery allowed_tools contains Bash(graphify-shim.sh:*) ──────────


def test_ac6_discovery_allowed_tools_contains_graphify_shim(tmp_path):
    """AC6: phase_1_discovery._invoke_discovery_llm passes Bash(graphify-shim.sh:*)
    in allowed_tools to invoke_llm_subprocess.

    Fails today: allowed_tools=["Read","Grep","Glob","Write"] — shim token absent.
    """
    import importlib
    phase_1 = importlib.import_module("bytedigger_engine.workflows.phase_1_discovery")

    ctx = _make_ctx(tmp_path)
    prev = _ok_result(
        prompt="x",
        doc_path=str(tmp_path / "discovery.md"),
        complexity="SIMPLE",
    )

    mock_invoke = _mock_invoke_ok()
    with patch("bytedigger_engine.workflows.phase_1_discovery.invoke_llm_subprocess", mock_invoke):
        phase_1._invoke_discovery_llm(ctx, prev)

    assert mock_invoke.call_count >= 1, (
        "phase_1_discovery._invoke_discovery_llm never called invoke_llm_subprocess"
    )
    _, kwargs = mock_invoke.call_args_list[0]
    actual = kwargs.get("allowed_tools", "__MISSING__")
    assert actual != "__MISSING__", (
        f"allowed_tools kwarg not passed. kwargs={list(kwargs.keys())}"
    )
    assert _SHIM_TOKEN in actual, (
        f"AC6 FAIL: '{_SHIM_TOKEN}' not in discovery allowed_tools.\n"
        f"Current value: {actual!r}"
    )


# ─── AC7: explore allowed_tools contains Bash(graphify-shim.sh:*) ────────────


def test_ac7_explore_allowed_tools_contains_graphify_shim(tmp_path):
    """AC7: phase_2_explore._invoke_explore_llm passes Bash(graphify-shim.sh:*)
    in allowed_tools to invoke_llm_subprocess.

    Fails today: allowed_tools=[..., "Write"] — shim token absent.
    """
    import importlib
    phase_2 = importlib.import_module("bytedigger_engine.workflows.phase_2_explore")

    ctx = _make_ctx(tmp_path)
    # prev must not be skipped — passthrough_if_skipped checks for "skipped" key
    prev = _ok_result(
        prompt="x",
        doc_path=str(tmp_path / "exploration.md"),
        complexity="FEATURE",
    )

    mock_invoke = _mock_invoke_ok()
    with patch("bytedigger_engine.workflows.phase_2_explore.invoke_llm_subprocess", mock_invoke):
        phase_2._invoke_explore_llm(ctx, prev)

    assert mock_invoke.call_count >= 1, (
        "phase_2_explore._invoke_explore_llm never called invoke_llm_subprocess"
    )
    _, kwargs = mock_invoke.call_args_list[0]
    actual = kwargs.get("allowed_tools", "__MISSING__")
    assert actual != "__MISSING__", (
        f"allowed_tools kwarg not passed. kwargs={list(kwargs.keys())}"
    )
    assert _SHIM_TOKEN in actual, (
        f"AC7 FAIL: '{_SHIM_TOKEN}' not in explore allowed_tools.\n"
        f"Current value: {actual!r}"
    )


# ─── AC10: grep-fallback tools preserved (negative-regression) ───────────────


def test_ac10_grep_fallback_tools_preserved_discovery(tmp_path):
    """AC10 (discovery half): Read, Grep, Glob still in discovery allowed_tools after
    the graphify-shim token is added.

    Passes today (Read/Grep/Glob present) — correctness guard that must keep passing
    post-GREEN. Fails if GREEN accidentally drops the fallback chain.
    (feedback_cleanup_must_preserve_grounding_fallback_chain)
    """
    import importlib
    phase_1 = importlib.import_module("bytedigger_engine.workflows.phase_1_discovery")

    ctx = _make_ctx(tmp_path)
    prev = _ok_result(
        prompt="x",
        doc_path=str(tmp_path / "discovery.md"),
        complexity="SIMPLE",
    )

    mock_invoke = _mock_invoke_ok()
    with patch("bytedigger_engine.workflows.phase_1_discovery.invoke_llm_subprocess", mock_invoke):
        phase_1._invoke_discovery_llm(ctx, prev)

    assert mock_invoke.call_count >= 1
    _, kwargs = mock_invoke.call_args_list[0]
    actual = kwargs.get("allowed_tools", [])
    for tool in ("Read", "Grep", "Glob"):
        assert tool in actual, (
            f"AC10 FAIL: '{tool}' was removed from discovery allowed_tools — "
            f"grep-fallback chain broken.\nCurrent value: {actual!r}"
        )


def test_ac10_grep_fallback_tools_preserved_explore(tmp_path):
    """AC10 (explore half): Read, Grep, Glob still in explore allowed_tools after
    the graphify-shim token is added.

    Passes today — correctness guard that must keep passing post-GREEN.
    """
    import importlib
    phase_2 = importlib.import_module("bytedigger_engine.workflows.phase_2_explore")

    ctx = _make_ctx(tmp_path)
    prev = _ok_result(
        prompt="x",
        doc_path=str(tmp_path / "exploration.md"),
        complexity="FEATURE",
    )

    mock_invoke = _mock_invoke_ok()
    with patch("bytedigger_engine.workflows.phase_2_explore.invoke_llm_subprocess", mock_invoke):
        phase_2._invoke_explore_llm(ctx, prev)

    assert mock_invoke.call_count >= 1
    _, kwargs = mock_invoke.call_args_list[0]
    actual = kwargs.get("allowed_tools", [])
    for tool in ("Read", "Grep", "Glob"):
        assert tool in actual, (
            f"AC10 FAIL: '{tool}' was removed from explore allowed_tools — "
            f"grep-fallback chain broken.\nCurrent value: {actual!r}"
        )


# ─── AC8: discovery prompt body contains graph-first directive ────────────────


def test_ac8_discovery_prompt_contains_graph_first_directive(tmp_path):
    """AC8: phase_1_discovery._build_discovery_prompt assembles a prompt body
    containing both 'graphify query' AND '### GRAPH-FIRST EVIDENCE'.

    Fails today: neither token is present in the current prompt body.
    §2.3/DF-7: verbatim token check (Principle C engine-prompt forcing function).
    """
    import importlib
    phase_1 = importlib.import_module("bytedigger_engine.workflows.phase_1_discovery")

    ctx = _make_ctx(tmp_path)
    result = phase_1._build_discovery_prompt(ctx, None)

    assert result.status == "ok", (
        f"_build_discovery_prompt returned status={result.status!r}"
    )
    prompt = result.data["prompt"]

    assert _GRAPH_QUERY_DIRECTIVE in prompt, (
        f"AC8 FAIL: '{_GRAPH_QUERY_DIRECTIVE}' not found in discovery prompt body.\n"
        f"Graph-first directive (§2.3/DF-7) not yet wired into phase_1_discovery.py."
    )
    assert _GRAPH_EVIDENCE_HEADER in prompt, (
        f"AC8 FAIL: '{_GRAPH_EVIDENCE_HEADER}' not found in discovery prompt body.\n"
        f"OUTPUT-section header (§2.3/DF-7) not yet wired into phase_1_discovery.py."
    )


# ─── AC9: explore prompt body contains graph-first directive ──────────────────


def test_ac9_explore_prompt_contains_graph_first_directive(tmp_path):
    """AC9: phase_2_explore._build_explore_prompt assembles a prompt body
    containing both 'graphify query' AND '### GRAPH-FIRST EVIDENCE'.

    Fails today: neither token is present in the current explore prompt body.
    §2.3/DF-7: verbatim token check (Principle C engine-prompt forcing function).
    """
    import importlib
    phase_2 = importlib.import_module("bytedigger_engine.workflows.phase_2_explore")

    ctx = _make_ctx(tmp_path)
    # _build_explore_prompt calls passthrough_if_skipped on prev; pass a non-skipped prev
    prev = _ok_result(skipped=False)
    result = phase_2._build_explore_prompt(ctx, prev)

    assert result.status == "ok", (
        f"_build_explore_prompt returned status={result.status!r}"
    )
    prompt = result.data["prompt"]

    assert _GRAPH_QUERY_DIRECTIVE in prompt, (
        f"AC9 FAIL: '{_GRAPH_QUERY_DIRECTIVE}' not found in explore prompt body.\n"
        f"Graph-first directive (§2.3/DF-7) not yet wired into phase_2_explore.py."
    )
    assert _GRAPH_EVIDENCE_HEADER in prompt, (
        f"AC9 FAIL: '{_GRAPH_EVIDENCE_HEADER}' not found in explore prompt body.\n"
        f"OUTPUT-section header (§2.3/DF-7) not yet wired into phase_2_explore.py."
    )
