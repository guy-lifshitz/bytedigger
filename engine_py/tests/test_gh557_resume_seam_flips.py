"""RED tests for GH557 — opt-in remaining expensive LLM steps to the resume seam.

Spec: SHARED/memory/Decisions/2026-07-11_GH557_resume_seam_remaining_steps_spec.md

§1q: production changes are three ``resume_sentinel=True`` flips (phase_45_spec
``invoke_review_llm``; phase_5_implement ``invoke_red_llm`` /
``invoke_validation_llm``) plus a ``LoopRunner`` seam extension in engine.py so
the LoopStepContract body actually consults the sentinel. None of these exist
yet — every AC1/AC2/AC4/AC6/AC7 test below FAILS at assert/call time against
today's code, never at collection time. AC3/AC5/AC8 pin already-true behavior
(regression guards) and may legitimately PASS today.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "workflows"))
sys.path.insert(0, str(HERE.parent / "lib"))

from contracts import LoopStepContract, StepContract, StepResult, WorkflowContext  # noqa: E402
from phase_45_spec import phase_45_spec_workflow  # noqa: E402
from phase_5_implement import build_validation_loop_contract  # noqa: E402
from phase_6_review import phase_6_review_workflow  # noqa: E402


def make_ctx(scratchpad: Path, *, task_description: str = "t", **org_extra) -> WorkflowContext:
    org = {"scratchpad_dir": str(scratchpad), "task_description": task_description, **org_extra}
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question="GH557 resume seam flips",
        session_id="test-gh557",
        persona="hal",
        framework=None,
        domain=None,
    )


# ─── AC1 ────────────────────────────────────────────────────────────────────


def test_ac1_phase45_invoke_review_llm_flagged_neighbours_unflagged():
    wf = phase_45_spec_workflow()
    names = [s.name for s in wf.steps]
    idx = names.index("invoke_review_llm")
    by_name = {s.name: s for s in wf.steps}
    assert by_name["invoke_review_llm"].resume_sentinel is True
    before_name = names[idx - 1]
    after_name = names[idx + 1]
    assert by_name[before_name].resume_sentinel is False
    assert by_name[after_name].resume_sentinel is False


# ─── AC2 ────────────────────────────────────────────────────────────────────


def test_ac2_phase5_loop_body_invoke_red_and_validation_flagged():
    contract = build_validation_loop_contract()
    body_names = [s.name for s in contract.body]
    by_name = {s.name: s for s in contract.body}

    idx_red = body_names.index("invoke_red_llm")
    idx_val = body_names.index("invoke_validation_llm")

    assert by_name["invoke_red_llm"].resume_sentinel is True
    assert by_name["invoke_validation_llm"].resume_sentinel is True
    assert by_name[body_names[idx_red - 1]].resume_sentinel is False
    assert by_name[body_names[idx_val - 1]].resume_sentinel is False


# ─── AC3 (pinned decision — regression guard, may PASS today) ───────────────


def test_ac3_phase6_invoke_satisfaction_llm_stays_unflagged():
    """Pinned decision (Change item 4): satisfaction never gets resume_sentinel
    because ctx_hash cannot see code-state changes between crash and resume.
    A future flip must consciously break this test and read the rationale."""
    wf = phase_6_review_workflow()
    by_name = {s.name: s for s in wf.steps}
    assert by_name["invoke_satisfaction_llm"].resume_sentinel is False


# ─── AC4 ────────────────────────────────────────────────────────────────────


def test_ac4_phase45_invoke_review_llm_resumes_from_sentinel(tmp_path):
    from step_sentinel import compute_ctx_hash, maybe_read_sentinel, write_step_sentinel
    from resume_keying import resume_sentinel_name

    ctx = make_ctx(tmp_path)
    run_id = "run-ac4"
    ctx_hash = compute_ctx_hash(ctx)

    write_step_sentinel(tmp_path, "invoke_review_llm", 1, {"raw_response": "cached"}, run_id, ctx_hash)
    sentinel_path = tmp_path / "resume" / resume_sentinel_name("invoke_review_llm", 1, run_id, ctx_hash)
    assert sentinel_path.exists()

    wf = phase_45_spec_workflow()
    step = next(s for s in wf.steps if s.name == "invoke_review_llm")

    events: list = []

    def emit(event_type, payload, rid):
        events.append((event_type, payload, rid))

    result = maybe_read_sentinel(ctx, step, 1, run_id, emit)

    assert result is not None, "flip must make invoke_review_llm eligible for sentinel resume"
    assert result.status == "ok"
    assert result.data["raw_response"] == "cached"
    assert result.step_name == "invoke_review_llm"


# ─── AC5 (negative slice — regression guard, may PASS today) ────────────────


def test_ac5_phase6_invoke_satisfaction_llm_never_resumes_from_sentinel(tmp_path):
    from step_sentinel import compute_ctx_hash, maybe_read_sentinel, write_step_sentinel

    ctx = make_ctx(tmp_path)
    run_id = "run-ac5"
    ctx_hash = compute_ctx_hash(ctx)

    write_step_sentinel(tmp_path, "invoke_satisfaction_llm", 1, {"raw_response": "cached"}, run_id, ctx_hash)

    wf = phase_6_review_workflow()
    step = next(s for s in wf.steps if s.name == "invoke_satisfaction_llm")

    def emit(event_type, payload, rid):
        raise AssertionError("emit must not be called — unflagged step never resumes")

    result = maybe_read_sentinel(ctx, step, 1, run_id, emit)
    assert result is None


# ─── AC6/AC7/AC8 helpers ─────────────────────────────────────────────────────


def _make_counter_body(counts: dict) -> StepContract:
    def _counter(ctx, prev):
        counts["calls"] = counts.get("calls", 0) + 1
        return StepResult(status="ok", data={"raw_response": "live"}, duration_ms=0, step_name="invoke_red_llm")

    return StepContract(name="invoke_red_llm", execute=_counter, resume_sentinel=True)


def _make_unflagged_body(counts: dict) -> StepContract:
    def _counter(ctx, prev):
        counts["other_calls"] = counts.get("other_calls", 0) + 1
        return StepResult(status="ok", data={"raw_response": "other"}, duration_ms=0, step_name="other_step")

    return StepContract(name="other_step", execute=_counter, resume_sentinel=False)


# ─── AC6 ────────────────────────────────────────────────────────────────────


def test_ac6_loop_runner_resumes_body_step_from_sentinel_no_call(tmp_path):
    import telemetry_ctx
    from engine import LoopRunner
    from event_log import EventLog
    from step_sentinel import compute_ctx_hash, write_step_sentinel

    ctx = make_ctx(tmp_path)
    log_path = tmp_path / "events.jsonl"
    event_log = EventLog(log_path)
    run_id = "runX"
    ctx_hash = compute_ctx_hash(ctx)

    write_step_sentinel(tmp_path, "invoke_red_llm", 1, {"raw_response": "cached"}, run_id, ctx_hash, workflow_name="test_loop")

    counts: dict = {}
    body_step = _make_counter_body(counts)
    contract = LoopStepContract(name="test_loop", body=[body_step], max_iterations=1)

    telemetry_ctx.set_current_run(
        event_log=event_log, run_id=run_id, step_name="validation_cycle_loop", cycle=1,
    )
    try:
        LoopRunner(contract).run(ctx, None)
    finally:
        telemetry_ctx.clear_current_run()

    assert counts.get("calls", 0) == 0, "cached body step must NOT invoke the counter fn"

    events = event_log.read_all()
    resumed_events = [e for e in events if e.get("event_type") == "step_sentinel_resumed"]
    assert resumed_events, "expected a step_sentinel_resumed event for the loop body step"


# ─── AC7 ────────────────────────────────────────────────────────────────────


def test_ac7_loop_runner_writes_sentinel_for_body_step_ok_result(tmp_path):
    import telemetry_ctx
    from engine import LoopRunner
    from event_log import EventLog
    from step_sentinel import compute_ctx_hash
    from resume_keying import resume_sentinel_name

    ctx = make_ctx(tmp_path)
    log_path = tmp_path / "events.jsonl"
    event_log = EventLog(log_path)
    run_id = "runX"
    ctx_hash = compute_ctx_hash(ctx)

    counts: dict = {}
    body_step = _make_counter_body(counts)
    contract = LoopStepContract(name="test_loop", body=[body_step], max_iterations=1)

    telemetry_ctx.set_current_run(
        event_log=event_log, run_id=run_id, step_name="validation_cycle_loop", cycle=1,
    )
    try:
        LoopRunner(contract).run(ctx, None)
    finally:
        telemetry_ctx.clear_current_run()

    sentinel_path = tmp_path / "resume" / resume_sentinel_name("invoke_red_llm", 1, run_id, ctx_hash, workflow_name="test_loop")
    assert sentinel_path.exists(), "loop-body ok result must be persisted for future resume"
    import json
    persisted = json.loads(sentinel_path.read_text())
    assert persisted.get("raw_response") == "live"


# ─── AC8 (degrade — regression guard, may PASS today) ───────────────────────


def test_ac8_loop_runner_degrades_safely_with_no_run_ctx(tmp_path):
    import telemetry_ctx
    from engine import LoopRunner
    from step_sentinel import compute_ctx_hash, write_step_sentinel

    ctx = make_ctx(tmp_path)
    run_id = "runX"
    ctx_hash = compute_ctx_hash(ctx)

    # pre-write sentinels for both a flagged and an unflagged body step
    write_step_sentinel(tmp_path, "invoke_red_llm", 1, {"raw_response": "cached"}, run_id, ctx_hash)
    write_step_sentinel(tmp_path, "other_step", 1, {"raw_response": "cached_other"}, run_id, ctx_hash)

    counts: dict = {}
    flagged_step = _make_counter_body(counts)
    unflagged_step = _make_unflagged_body(counts)
    contract = LoopStepContract(name="test_loop", body=[flagged_step, unflagged_step], max_iterations=1)

    telemetry_ctx.clear_current_run()  # explicit: no active run ctx
    try:
        LoopRunner(contract).run(ctx, None)
    finally:
        telemetry_ctx.clear_current_run()

    assert counts.get("calls", 0) == 1, "no run_id -> seam off -> counter fn called exactly once"
    assert counts.get("other_calls", 0) == 1, "unflagged body step executes normally regardless"


# ─── AC9 ────────────────────────────────────────────────────────────────────


def test_ac9_loop_runner_keys_sentinel_by_iteration_not_run_ctx_cycle(tmp_path):
    import telemetry_ctx
    from engine import LoopRunner
    from event_log import EventLog
    from step_sentinel import compute_ctx_hash, write_step_sentinel
    from resume_keying import resume_sentinel_name

    ctx = make_ctx(tmp_path)
    log_path = tmp_path / "events.jsonl"
    event_log = EventLog(log_path)
    run_id = "runX"
    ctx_hash = compute_ctx_hash(ctx)

    # sentinel present ONLY for iteration 1 — iteration 2 must run live and be
    # keyed by its own iteration number, not by the outer run ctx's cycle=1.
    write_step_sentinel(tmp_path, "invoke_red_llm", 1, {"raw_response": "cached"}, run_id, ctx_hash, workflow_name="test_loop")

    counts: dict = {}
    body_step = _make_counter_body(counts)
    contract = LoopStepContract(name="test_loop", body=[body_step], max_iterations=2)

    telemetry_ctx.set_current_run(
        event_log=event_log, run_id=run_id, step_name="validation_cycle_loop", cycle=1,
    )
    try:
        LoopRunner(contract).run(ctx, None)
    finally:
        telemetry_ctx.clear_current_run()

    assert counts.get("calls", 0) == 1, (
        "iteration 1 must resume from cache (no call); iteration 2 must execute live (one call)"
    )

    sentinel_path_iter2 = tmp_path / "resume" / resume_sentinel_name("invoke_red_llm", 2, run_id, ctx_hash, workflow_name="test_loop")
    assert sentinel_path_iter2.exists(), (
        "iteration 2's live ok result must be persisted keyed by iteration=2, not run ctx cycle=1"
    )
