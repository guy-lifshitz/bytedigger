"""RED tests for DA48BEAC pass 2: graph-first wiring into phase_5 validation.

AC coverage:
  AC1 — test_validation_prompt_contains_graph_first_protocol_and_query
  AC2 — test_validation_prompt_contains_graphify_path_and_affected
  AC3 — test_validation_prompt_contains_graph_first_evidence
  AC4 — test_invoke_validation_llm_allowed_tools_contains_bash_graphify_shim
  AC5 — test_invoke_validation_llm_allowed_tools_contains_read
  AC6 — test_invoke_validation_llm_allowed_tools_does_not_contain_write
  AC7 — test_invoke_validation_llm_hard_gate_is_true

All tests FAIL before GREEN (DA48BEAC pass 2) because:
  AC1-3: the directive/evidence text is absent from _build_validation_prompt output.
  AC4-7: allowed_tools=["Read"] (prod current) does not include Bash(graphify-shim.sh:*)
         and the test asserts on the actual call-kwargs captured by patching
         invoke_llm_subprocess at the phase_5_implement import boundary.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# sys.path is managed by conftest.py (conftest-import-time singleton, §1q/81F97F3D).
# Do NOT add sys.path manipulation here.

from contracts import StepResult, WorkflowContext  # noqa: E402


# ─── Fixtures ────────────────────────────────────────────────────────────────


def _make_ctx(tmp_path: Path) -> WorkflowContext:
    (tmp_path / "scratchpad").mkdir(parents=True, exist_ok=True)
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={"scratchpad_dir": str(tmp_path / "scratchpad")},
        question="test question for DA48BEAC",
        session_id="test-DA48BEAC-pass2",
        persona="hal",
        framework=None,
        domain=None,
    )


def _prev_for_build_validation_prompt(tmp_path: Path) -> StepResult:
    """Minimal prev StepResult that _build_validation_prompt will accept.

    Reads: prev.data["red_log_path"], prev.data["spec_path"],
           prev.data.get("cycle", 1).
    """
    red_log = tmp_path / "red.log"
    spec = tmp_path / "spec.md"
    red_log.write_text("RED LOG CONTENT\n", encoding="utf-8")
    spec.write_text("SPEC CONTENT\n", encoding="utf-8")
    return StepResult(
        status="ok",
        data={
            "red_log_path": str(red_log),
            "spec_path": str(spec),
            "cycle": 1,
        },
        duration_ms=0,
        step_name="prev_for_build_validation_prompt",
    )


def _prev_for_invoke_validation_llm(tmp_path: Path) -> StepResult:
    """Minimal prev StepResult that _invoke_validation_llm will accept.

    Reads: prev.data["prompt"], prev.data["doc_path"], prev.data["spec_path"],
           prev.data["red_log_path"], prev.data.get("red_test_paths", []),
           prev.data.get("cycle", 1), prev.data.get("red_commit_sha").
    """
    return StepResult(
        status="ok",
        data={
            "prompt": "validation prompt content",
            "doc_path": str(tmp_path / "validation.md"),
            "spec_path": str(tmp_path / "spec.md"),
            "red_log_path": str(tmp_path / "red.log"),
            "red_test_paths": [],
            "cycle": 1,
        },
        duration_ms=0,
        step_name="prev_for_invoke_validation_llm",
    )


def _mock_invoke_ok() -> MagicMock:
    """Mock that returns a minimal ok StepResult and records call args."""
    return MagicMock(return_value=StepResult(
        status="ok",
        data={"raw_response": "OK", "response_bytes": 2, "command": ["x"]},
        duration_ms=0,
        step_name="mock_invoke",
    ))


# ─── AC1-AC3: prompt content ─────────────────────────────────────────────────


def test_validation_prompt_contains_graph_first_protocol_and_query(tmp_path):
    """AC1: _build_validation_prompt returns prompt containing the graph-first
    directive header 'GRAPH-FIRST PROTOCOL (DA48BEAC)' AND 'graphify query'.

    FAILS RED: the directive is not yet present in _build_validation_prompt.
    """
    import phase_5_implement

    ctx = _make_ctx(tmp_path)
    prev = _prev_for_build_validation_prompt(tmp_path)

    result = phase_5_implement._build_validation_prompt(ctx, prev)

    assert result.status == "ok", (
        f"_build_validation_prompt returned error: {result.error!r}"
    )
    prompt = result.data["prompt"]
    assert "GRAPH-FIRST PROTOCOL (DA48BEAC)" in prompt, (
        "AC1 FAIL: 'GRAPH-FIRST PROTOCOL (DA48BEAC)' not found in "
        "_build_validation_prompt output.\n"
        "This is the RED state — GREEN must add the directive."
    )
    assert "graphify query" in prompt, (
        "AC1 FAIL: 'graphify query' not found in _build_validation_prompt output.\n"
        "This is the RED state — GREEN must add the directive."
    )


def test_validation_prompt_contains_graphify_path_and_affected(tmp_path):
    """AC2: _build_validation_prompt prompt contains 'graphify path' AND
    'graphify affected' (§1y reachability + §1o consumer verbs).

    FAILS RED: these verbs are absent in the current prompt.
    """
    import phase_5_implement

    ctx = _make_ctx(tmp_path)
    prev = _prev_for_build_validation_prompt(tmp_path)

    result = phase_5_implement._build_validation_prompt(ctx, prev)

    assert result.status == "ok", (
        f"_build_validation_prompt returned error: {result.error!r}"
    )
    prompt = result.data["prompt"]
    assert "graphify path" in prompt, (
        "AC2 FAIL: 'graphify path' not found in _build_validation_prompt output.\n"
        "GREEN must add this to the graph-first directive."
    )
    assert "graphify affected" in prompt, (
        "AC2 FAIL: 'graphify affected' not found in _build_validation_prompt output.\n"
        "GREEN must add this to the graph-first directive."
    )


def test_validation_prompt_contains_graph_first_evidence(tmp_path):
    """AC3: _build_validation_prompt prompt contains 'GRAPH-FIRST EVIDENCE'
    in the OUTPUT / Reachability section.

    FAILS RED: the evidence reporting sub-line is absent.
    """
    import phase_5_implement

    ctx = _make_ctx(tmp_path)
    prev = _prev_for_build_validation_prompt(tmp_path)

    result = phase_5_implement._build_validation_prompt(ctx, prev)

    assert result.status == "ok", (
        f"_build_validation_prompt returned error: {result.error!r}"
    )
    prompt = result.data["prompt"]
    assert "GRAPH-FIRST EVIDENCE" in prompt, (
        "AC3 FAIL: 'GRAPH-FIRST EVIDENCE' not found in _build_validation_prompt output.\n"
        "GREEN must add the evidence sub-line to the Reachability & Cross-Check OUTPUT."
    )


# ─── AC4-AC7: invoke_llm_subprocess call kwargs ──────────────────────────────


def test_invoke_validation_llm_allowed_tools_contains_bash_graphify_shim(tmp_path):
    """AC4: _invoke_validation_llm passes allowed_tools containing
    'Bash(graphify-shim.sh:*)' to invoke_llm_subprocess.

    FAILS RED: current prod passes allowed_tools=["Read"] only.
    Patch target: phase_5_implement.invoke_llm_subprocess (import boundary).
    """
    import phase_5_implement

    ctx = _make_ctx(tmp_path)
    prev = _prev_for_invoke_validation_llm(tmp_path)
    mock_invoke = _mock_invoke_ok()

    with patch("phase_5_implement.invoke_llm_subprocess", mock_invoke):
        phase_5_implement._invoke_validation_llm(ctx, prev)

    assert mock_invoke.call_count >= 1, (
        "_invoke_validation_llm never called invoke_llm_subprocess — "
        "check that prev.data has all required keys."
    )
    _, kwargs = mock_invoke.call_args_list[0]
    allowed_tools = kwargs.get("allowed_tools", "__MISSING__")
    assert allowed_tools != "__MISSING__", (
        "AC4 FAIL: invoke_llm_subprocess called without allowed_tools kwarg."
    )
    assert "Bash(graphify-shim.sh:*)" in allowed_tools, (
        f"AC4 FAIL: 'Bash(graphify-shim.sh:*)' not in allowed_tools.\n"
        f"Got: {allowed_tools!r}\n"
        "GREEN must add 'Bash(graphify-shim.sh:*)' to the validation allowed_tools list."
    )


def test_invoke_validation_llm_allowed_tools_contains_read(tmp_path):
    """AC5 (negative-regression): _invoke_validation_llm allowed_tools STILL
    contains 'Read' after the graph-first extension.

    FAILS RED only if 'Read' is somehow absent. Since current prod has
    allowed_tools=["Read"], this test passes today — it is a GREEN regression
    guard, not a forcing AC. Kept here to anchor the negative-regression contract.

    NOTE: This test is expected to PASS today (pre-GREEN). It protects the
    'Read' tool from being accidentally dropped during GREEN.
    """
    import phase_5_implement

    ctx = _make_ctx(tmp_path)
    prev = _prev_for_invoke_validation_llm(tmp_path)
    mock_invoke = _mock_invoke_ok()

    with patch("phase_5_implement.invoke_llm_subprocess", mock_invoke):
        phase_5_implement._invoke_validation_llm(ctx, prev)

    assert mock_invoke.call_count >= 1, (
        "_invoke_validation_llm never called invoke_llm_subprocess."
    )
    _, kwargs = mock_invoke.call_args_list[0]
    allowed_tools = kwargs.get("allowed_tools", "__MISSING__")
    assert allowed_tools != "__MISSING__", (
        "AC5 FAIL: invoke_llm_subprocess called without allowed_tools kwarg."
    )
    assert "Read" in allowed_tools, (
        f"AC5 FAIL (regression): 'Read' was dropped from allowed_tools.\n"
        f"Got: {allowed_tools!r}\n"
        "'Read' must be preserved — it was present in the pre-GREEN baseline."
    )


def test_invoke_validation_llm_allowed_tools_does_not_contain_write(tmp_path):
    """AC6 (validation-only invariant): allowed_tools must NOT contain 'Write'.

    FAILS RED if 'Write' is present. Since current prod has allowed_tools=["Read"],
    this test passes today — it is a GREEN regression guard for the validation-only
    invariant (the validator must never be given Write access).

    NOTE: This test is expected to PASS today (pre-GREEN). It guards against
    'Write' being inadvertently added during GREEN.
    """
    import phase_5_implement

    ctx = _make_ctx(tmp_path)
    prev = _prev_for_invoke_validation_llm(tmp_path)
    mock_invoke = _mock_invoke_ok()

    with patch("phase_5_implement.invoke_llm_subprocess", mock_invoke):
        phase_5_implement._invoke_validation_llm(ctx, prev)

    assert mock_invoke.call_count >= 1, (
        "_invoke_validation_llm never called invoke_llm_subprocess."
    )
    _, kwargs = mock_invoke.call_args_list[0]
    allowed_tools = kwargs.get("allowed_tools", "__MISSING__")
    assert allowed_tools != "__MISSING__", (
        "AC6 FAIL: invoke_llm_subprocess called without allowed_tools kwarg."
    )
    assert "Write" not in allowed_tools, (
        f"AC6 FAIL (invariant): 'Write' must NOT appear in validation allowed_tools.\n"
        f"Got: {allowed_tools!r}\n"
        "The validator is VALIDATION-ONLY — 'Write' is forbidden."
    )


def test_invoke_validation_llm_hard_gate_is_true(tmp_path):
    """AC7 (negative-regression): hard_gate=True must be preserved on the
    invoke_llm_subprocess call in _invoke_validation_llm.

    This test passes today (pre-GREEN) since hard_gate=True is already present.
    It guards against the GREEN change accidentally removing it.

    NOTE: This test is expected to PASS today (pre-GREEN). It anchors the
    Opus gate invariant.
    """
    import phase_5_implement

    ctx = _make_ctx(tmp_path)
    prev = _prev_for_invoke_validation_llm(tmp_path)
    mock_invoke = _mock_invoke_ok()

    with patch("phase_5_implement.invoke_llm_subprocess", mock_invoke):
        phase_5_implement._invoke_validation_llm(ctx, prev)

    assert mock_invoke.call_count >= 1, (
        "_invoke_validation_llm never called invoke_llm_subprocess."
    )
    _, kwargs = mock_invoke.call_args_list[0]
    hard_gate = kwargs.get("hard_gate", "__MISSING__")
    assert hard_gate != "__MISSING__", (
        "AC7 FAIL: invoke_llm_subprocess called without hard_gate kwarg."
    )
    assert hard_gate is True, (
        f"AC7 FAIL (regression): hard_gate must be True; got {hard_gate!r}.\n"
        "The Opus gate must remain untouched by the GREEN change."
    )
