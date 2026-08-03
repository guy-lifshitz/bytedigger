"""Tests for LoopStepContract + LoopRunner (E2440DB5).

Declarative loop primitive replacing hand-rolled cycle logic across
phase_45_spec_lite (cycle 2 spec rewrite), phase_5_implement (RED→GREEN
retry), and phase_6_review (iteration). Schema mirrors Archon's loop
node: prompt + until_marker (LLM-judged) + until_bash (deterministic) +
max_iterations + fresh_context.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

from bytedigger_engine.contracts import (  # noqa: E402
    LoopStepContract,
    StepContract,
    StepResult,
    WorkflowContext,
)
from bytedigger_engine.engine import LoopRunner  # noqa: E402


def _ctx() -> WorkflowContext:
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={},
        question="loop test",
        session_id="t",
        persona="hal",
        framework=None,
        domain=None,
    )


def _step(name: str, fn) -> StepContract:
    return StepContract(name=name, execute=fn)


# ─── until_marker termination ────────────────────────────────────────────────


def test_loop_terminates_on_until_marker():
    """Body step emits marker on iteration 2 → loop stops at 2."""
    counter = {"n": 0}

    def body_fn(_ctx, _prev) -> StepResult:
        counter["n"] += 1
        text = "MARKER_DONE" if counter["n"] >= 2 else "still working"
        return StepResult(status="ok", data={"raw_response": text},
                          duration_ms=0, step_name="b")

    loop = LoopStepContract(
        name="L", body=[_step("b", body_fn)],
        until_marker="MARKER_DONE", max_iterations=10,
    )
    result = LoopRunner(loop).run(_ctx(), None)

    assert result.status == "ok"
    assert counter["n"] == 2
    assert result.metadata["iterations"] == 2
    assert result.metadata["terminated_by"] == "marker"


def test_loop_until_marker_scans_default_raw_response_field():
    """Default marker_field = raw_response."""
    def body_fn(_ctx, _prev) -> StepResult:
        return StepResult(status="ok", data={"raw_response": "PASS"},
                          duration_ms=0, step_name="b")

    loop = LoopStepContract(name="L", body=[_step("b", body_fn)],
                            until_marker="PASS", max_iterations=5)
    result = LoopRunner(loop).run(_ctx(), None)
    assert result.metadata["iterations"] == 1
    assert result.metadata["terminated_by"] == "marker"


def test_loop_until_marker_custom_field():
    """marker_field='verdict' scans data['verdict']."""
    def body_fn(_ctx, _prev) -> StepResult:
        return StepResult(status="ok", data={"verdict": "READY_TO_SHIP"},
                          duration_ms=0, step_name="b")

    loop = LoopStepContract(name="L", body=[_step("b", body_fn)],
                            until_marker="READY_TO_SHIP",
                            marker_field="verdict", max_iterations=5)
    result = LoopRunner(loop).run(_ctx(), None)
    assert result.metadata["terminated_by"] == "marker"


# ─── until_bash termination ──────────────────────────────────────────────────


def test_loop_terminates_on_until_bash_exit_0(tmp_path):
    """until_bash exits 0 once a sentinel file exists."""
    sentinel = tmp_path / "done.flag"
    counter = {"n": 0}

    def body_fn(_ctx, _prev) -> StepResult:
        counter["n"] += 1
        if counter["n"] == 3:
            sentinel.write_text("done")
        return StepResult(status="ok", data={"raw_response": "x"},
                          duration_ms=0, step_name="b")

    loop = LoopStepContract(
        name="L", body=[_step("b", body_fn)],
        until_bash=["test", "-f", str(sentinel)],
        max_iterations=10,
    )
    result = LoopRunner(loop).run(_ctx(), None)
    assert counter["n"] == 3
    assert result.metadata["terminated_by"] == "bash"


def test_loop_until_bash_nonzero_continues():
    """Bash returning non-zero does NOT terminate."""
    counter = {"n": 0}

    def body_fn(_ctx, _prev) -> StepResult:
        counter["n"] += 1
        return StepResult(status="ok", data={"raw_response": "x"},
                          duration_ms=0, step_name="b")

    loop = LoopStepContract(
        name="L", body=[_step("b", body_fn)],
        until_bash=["false"],  # always exits 1
        max_iterations=4,
    )
    result = LoopRunner(loop).run(_ctx(), None)
    assert counter["n"] == 4
    assert result.metadata["terminated_by"] == "max_iterations"


# ─── max_iterations cap ──────────────────────────────────────────────────────


def test_loop_caps_at_max_iterations():
    """No marker, no bash → run exactly max_iterations times."""
    counter = {"n": 0}

    def body_fn(_ctx, _prev) -> StepResult:
        counter["n"] += 1
        return StepResult(status="ok", data={"raw_response": "x"},
                          duration_ms=0, step_name="b")

    loop = LoopStepContract(name="L", body=[_step("b", body_fn)],
                            max_iterations=3)
    result = LoopRunner(loop).run(_ctx(), None)
    assert counter["n"] == 3
    assert result.metadata["iterations"] == 3
    assert result.metadata["terminated_by"] == "max_iterations"


# ─── fresh_context behaviour ─────────────────────────────────────────────────


def test_loop_fresh_context_passes_none_to_first_body_step_each_iter():
    """fresh_context=True → each iteration's first body step gets prev=None."""
    seen_prev = []

    def body_fn(_ctx, prev) -> StepResult:
        seen_prev.append(prev)
        return StepResult(status="ok", data={"raw_response": "x", "i": len(seen_prev)},
                          duration_ms=0, step_name="b")

    initial = StepResult(status="ok", data={"from_caller": True},
                         duration_ms=0, step_name="caller")
    loop = LoopStepContract(name="L", body=[_step("b", body_fn)],
                            max_iterations=3, fresh_context=True)
    LoopRunner(loop).run(_ctx(), initial)

    assert seen_prev == [None, None, None]


def test_loop_threaded_context_default_passes_prev_first_iter():
    """fresh_context=False (default) → first iteration gets caller's prev,
    subsequent iterations get the previous iteration's last body result."""
    seen_prev = []

    def body_fn(_ctx, prev) -> StepResult:
        seen_prev.append(prev)
        return StepResult(status="ok", data={"raw_response": "x", "tag": len(seen_prev)},
                          duration_ms=0, step_name="b")

    initial = StepResult(status="ok", data={"from_caller": True},
                         duration_ms=0, step_name="caller")
    loop = LoopStepContract(name="L", body=[_step("b", body_fn)],
                            max_iterations=2)
    LoopRunner(loop).run(_ctx(), initial)

    assert len(seen_prev) == 2
    assert seen_prev[0] is initial
    # 2nd iter gets previous iter's last body StepResult
    assert isinstance(seen_prev[1], StepResult)
    assert seen_prev[1].data["tag"] == 1


# ─── error propagation ──────────────────────────────────────────────────────


def test_loop_propagates_error_from_body_step():
    """Body step error (skip_on_error=False) terminates loop with error."""
    counter = {"n": 0}

    def body_fn(_ctx, _prev) -> StepResult:
        counter["n"] += 1
        if counter["n"] == 2:
            return StepResult(status="error", data=None, duration_ms=0,
                              step_name="b", error="boom",
                              error_code="E_TEST")
        return StepResult(status="ok", data={"raw_response": "x"},
                          duration_ms=0, step_name="b")

    loop = LoopStepContract(name="L", body=[_step("b", body_fn)],
                            max_iterations=10)
    result = LoopRunner(loop).run(_ctx(), None)
    assert result.status == "error"
    assert result.error_code == "E_TEST"
    assert counter["n"] == 2
    assert result.metadata["iterations"] == 2
    assert result.metadata["terminated_by"] == "error"


def test_loop_skip_on_error_continues():
    """skip_on_error=True → body error doesn't terminate loop."""
    counter = {"n": 0}

    def body_fn(_ctx, _prev) -> StepResult:
        counter["n"] += 1
        if counter["n"] == 1:
            return StepResult(status="error", data=None, duration_ms=0,
                              step_name="b", error="skip me",
                              error_code="E_SKIP")
        return StepResult(status="ok", data={"raw_response": "DONE"},
                          duration_ms=0, step_name="b")

    skip_step = StepContract(name="b", execute=body_fn, skip_on_error=True)
    loop = LoopStepContract(name="L", body=[skip_step],
                            until_marker="DONE", max_iterations=5)
    result = LoopRunner(loop).run(_ctx(), None)
    assert result.status == "ok"
    assert counter["n"] == 2
    assert result.metadata["terminated_by"] == "marker"


# ─── multi-step body ────────────────────────────────────────────────────────


def test_loop_multi_step_body_threads_within_iteration():
    """Within one iteration, step 2 receives step 1's result via prev."""
    captured = []

    def step_a(_ctx, _prev) -> StepResult:
        return StepResult(status="ok", data={"raw_response": "A"},
                          duration_ms=0, step_name="a")

    def step_b(_ctx, prev) -> StepResult:
        captured.append(prev.data["raw_response"])
        return StepResult(status="ok", data={"raw_response": "B-DONE"},
                          duration_ms=0, step_name="b")

    loop = LoopStepContract(
        name="L",
        body=[_step("a", step_a), _step("b", step_b)],
        until_marker="B-DONE", max_iterations=3,
    )
    LoopRunner(loop).run(_ctx(), None)
    assert captured == ["A"]


# ─── empty / pathological ───────────────────────────────────────────────────


def test_loop_max_iterations_zero_returns_skip():
    """max_iterations=0 → no body execution, returns skip with iterations=0."""
    def body_fn(_ctx, _prev) -> StepResult:
        raise AssertionError("body must not run")

    loop = LoopStepContract(name="L", body=[_step("b", body_fn)],
                            max_iterations=0)
    result = LoopRunner(loop).run(_ctx(), None)
    assert result.status == "skip"
    assert result.metadata["iterations"] == 0
    assert result.metadata["terminated_by"] == "max_iterations"


def test_loop_empty_body_raises_at_construction():
    """Body must be non-empty — caught by __post_init__."""
    import pytest
    with pytest.raises(ValueError):
        LoopStepContract(name="L", body=[], max_iterations=3)
