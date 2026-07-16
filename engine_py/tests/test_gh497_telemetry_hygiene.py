"""RED tests for GH497 — build-telemetry hygiene (fixture pollution, empty
axes, resume rollup attribution).

Spec: SHARED/memory/Decisions/2026-07-10_GH497_telemetry_hygiene_spec.md
Class: TOOLING. Tier: Option-D (engine_py prod modules).

Not-yet-existing symbols (`resolve_reject_log_path`, `annotate_invocation`,
`set_invocation_run_id`, `get_invocation_run_id`, the `axes_text` kwarg) are
imported/used INSIDE test bodies only — never at module top level — so this
file COLLECTS cleanly and fails at assert/call time (§1q ext, D1CF5FDF).

DO NOT modify any non-test file from this file.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest

import telemetry_ctx
import reject_log
from contracts import StepContract, StepResult, WorkflowContext, WorkflowDefinition


def make_ctx() -> WorkflowContext:
    return WorkflowContext(
        tenant_id="t",
        scope=None,
        db_path=None,
        org_config=None,
        question="q",
        session_id="s",
        persona="p",
        framework=None,
        domain=None,
    )


# ─── AC1 ────────────────────────────────────────────────────────────────────


def test_ac1_emit_reject_reason_writes_to_env_path_not_default(monkeypatch, tmp_path):
    """AC1: with HAL_REJECT_LOG=<tmp>/rl.jsonl and a run ctx set,
    record_plan_review_reject appends 1 line to the tmp file; the default
    prod REJECT_LOG_PATH constant is left untouched (no line appended there
    when using the env-seam resolver)."""
    rl_path = tmp_path / "rl.jsonl"
    monkeypatch.setenv("HAL_REJECT_LOG", str(rl_path))

    telemetry_ctx.set_current_run(
        event_log=None, run_id="rid1", step_name="s1", phase="phase_45_spec",
    )
    try:
        # resolve_reject_log_path is the new call-time resolver — not present
        # yet, so this import raises AttributeError until GREEN lands it.
        resolved = reject_log.resolve_reject_log_path()
        reject_log.emit_reject_reason(
            "phase_45_spec", "PLAN_REVIEW_REVISE", ["§1o"], {"cycle": 1},
            path=resolved,
        )
    finally:
        telemetry_ctx.clear_current_run()

    assert rl_path.exists(), "expected env-seam path to receive the record"
    lines = rl_path.read_text().splitlines()
    assert len(lines) == 1


# ─── AC2 ────────────────────────────────────────────────────────────────────


def test_ac2_emit_reject_reason_suppresses_write_when_build_id_none(monkeypatch, tmp_path):
    """AC2: with no run ctx and no explicit run_id, the tmp file is not
    created (or has 0 lines) — build_id-null suppression."""
    rl_path = tmp_path / "rl.jsonl"
    monkeypatch.setenv("HAL_REJECT_LOG", str(rl_path))

    telemetry_ctx.clear_current_run()
    resolved = reject_log.resolve_reject_log_path()
    reject_log.emit_reject_reason(
        "phase_45_spec", "PLAN_REVIEW_REVISE", [], {"cycle": 1}, path=resolved,
    )

    if rl_path.exists():
        assert rl_path.read_text().strip() == ""
    # else: file was never created — also acceptable per AC2


# ─── AC3 ────────────────────────────────────────────────────────────────────


def test_ac3_written_record_has_build_id_from_run_ctx(monkeypatch, tmp_path):
    """AC3: with run ctx set (run_id='rid1'), the written record has
    "build_id":"rid1"."""
    rl_path = tmp_path / "rl.jsonl"
    monkeypatch.setenv("HAL_REJECT_LOG", str(rl_path))

    telemetry_ctx.set_current_run(
        event_log=None, run_id="rid1", step_name="s1", phase="phase_45_spec",
    )
    try:
        resolved = reject_log.resolve_reject_log_path()
        reject_log.emit_reject_reason(
            "phase_45_spec", "PLAN_REVIEW_REVISE", ["§1o"], {"cycle": 1},
            path=resolved,
        )
    finally:
        telemetry_ctx.clear_current_run()

    lines = rl_path.read_text().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["build_id"] == "rid1"


# ─── AC4 ────────────────────────────────────────────────────────────────────


def test_ac4_engine_test_sees_reject_and_rework_log_env_under_tmp():
    """AC4: any engine test sees HAL_REJECT_LOG and HAL_REWORK_LOG set to
    paths under pytest tmp via the autouse conftest fixture (D1)."""
    reject_env = os.environ.get("HAL_REJECT_LOG")
    rework_env = os.environ.get("HAL_REWORK_LOG")
    assert reject_env is not None, "expected autouse fixture to set HAL_REJECT_LOG"
    assert rework_env is not None, "expected autouse fixture to set HAL_REWORK_LOG"
    # pytest tmp dirs live under a system temp root — assert NOT pointed at
    # the real HAL install/cwd-relative default path.
    assert "pytest" in reject_env or "tmp" in reject_env.lower()
    assert "pytest" in rework_env or "tmp" in rework_env.lower()


# ─── AC5 ────────────────────────────────────────────────────────────────────


def test_ac5_build_review_prompt_requires_rule_axes(tmp_path):
    """AC5: _build_review_prompt (phase_45) output prompt contains the
    literal RULE-AXES: forcing-function instruction."""
    sys.path.insert(0, str(Path(__file__).parent.parent / "workflows"))
    from phase_45_spec import _build_review_prompt, SPEC_DOC_RELPATH

    scratch = tmp_path / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    spec_path = scratch / SPEC_DOC_RELPATH
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text("## Context\nstub spec body.\n", encoding="utf-8")

    ctx = WorkflowContext(
        tenant_id="hal", scope=None, db_path=None,
        org_config={"scratchpad_dir": str(scratch)},
        question="Add foo", session_id="test-GH497", persona="hal",
        framework=None, domain=None,
    )
    prev = StepResult(
        status="ok",
        data={"spec_path": str(spec_path), "spec_bytes_written": spec_path.stat().st_size, "cycle": 1},
        duration_ms=0, step_name="write_spec_doc",
    )
    result = _build_review_prompt(ctx, prev)
    prompt = result.data["prompt"]
    assert "RULE-AXES:" in prompt


# ─── AC6 ────────────────────────────────────────────────────────────────────


def test_ac6_build_satisfaction_prompt_requires_rule_axes(tmp_path):
    """AC6: _build_satisfaction_prompt (phase_6) output prompt contains the
    literal RULE-AXES: forcing-function instruction."""
    sys.path.insert(0, str(Path(__file__).parent.parent / "workflows"))
    from phase_6_review import _build_satisfaction_prompt, DEFAULT_SATISFACTION_THRESHOLD

    scratch = tmp_path / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    spec_file = tmp_path / "spec.md"
    review_file = tmp_path / "review.md"
    fix_file = tmp_path / "fix.md"
    spec_file.write_text("# Spec\n", encoding="utf-8")
    review_file.write_text("# Review\n", encoding="utf-8")
    fix_file.write_text("# Fix\n", encoding="utf-8")

    ctx = WorkflowContext(
        tenant_id="hal", scope=None, db_path=None,
        org_config={"scratchpad_dir": str(scratch), "satisfaction_threshold": DEFAULT_SATISFACTION_THRESHOLD},
        question="add feature X", session_id="test-GH497-satisfaction", persona="hal",
        framework=None, domain=None,
    )
    prev = StepResult(
        status="ok",
        data={
            "spec_path": str(spec_file),
            "review_doc_path": str(review_file),
            "fix_doc_path": str(fix_file),
        },
        duration_ms=0, step_name="invoke_fix_llm",
    )
    result = _build_satisfaction_prompt(ctx, prev)
    prompt = result.data["prompt"]
    assert "RULE-AXES:" in prompt


# ─── AC7 ────────────────────────────────────────────────────────────────────


def test_ac7_extract_axes_parses_rule_axes_line():
    """AC7: _extract_axes("RULE-AXES: §1o, §1y") == ["§1o", "§1y"]."""
    result = reject_log._extract_axes("RULE-AXES: §1o, §1y")
    assert result == ["§1o", "§1y"]


# ─── AC8 ────────────────────────────────────────────────────────────────────


def test_ac8_record_satisfaction_reject_uses_axes_text_seam(monkeypatch, tmp_path):
    """AC8: record_satisfaction_reject(..., axes_text="RULE-AXES: §1w") writes
    a record with axes==["§1w"] even when error_msg has no §-tokens."""
    rl_path = tmp_path / "rl.jsonl"
    monkeypatch.setenv("HAL_REJECT_LOG", str(rl_path))

    telemetry_ctx.set_current_run(
        event_log=None, run_id="rid8", step_name="s1", phase="phase_6_review",
    )
    try:
        resolved = reject_log.resolve_reject_log_path()
        reject_log.record_satisfaction_reject(
            "SATISFACTION_FAIL", "no axis tokens here", 3, 5,
            axes_text="RULE-AXES: §1w",
            path=resolved,
        )
    finally:
        telemetry_ctx.clear_current_run()

    lines = rl_path.read_text().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["axes"] == ["§1w"]


# ─── AC9 ────────────────────────────────────────────────────────────────────


def test_ac9_annotate_invocation_uses_slot_when_set_else_falls_back_to_rid():
    """AC9: annotate_invocation({}, "ridA") with set_invocation_run_id("ridB")
    -> {"invocation_run_id":"ridB","resumed":True}; with slot cleared ->
    {"invocation_run_id":"ridA","resumed":False}."""
    from lib.cost_rollup import annotate_invocation

    telemetry_ctx.set_invocation_run_id("ridB")
    try:
        rollup = annotate_invocation({}, "ridA")
        assert rollup["invocation_run_id"] == "ridB"
        assert rollup["resumed"] is True
    finally:
        telemetry_ctx.set_invocation_run_id(None)

    rollup2 = annotate_invocation({}, "ridA")
    assert rollup2["invocation_run_id"] == "ridA"
    assert rollup2["resumed"] is False


# ─── AC10 ───────────────────────────────────────────────────────────────────


def _emitting_echo_step(ctx, _prev):
    run_ctx = telemetry_ctx.get_current_run()
    if run_ctx is not None and run_ctx.event_log is not None:
        run_ctx.event_log.append(
            "subprocess_exited",
            {"phase": run_ctx.phase, "tokens_in": 5, "tokens_out": 5, "cost_usd": 0.25},
            run_ctx.run_id,
        )
    return StepResult(status="ok", data={"echoed": ctx.question}, duration_ms=0, step_name="echo")


def test_ac10_workflow_cost_rollup_and_rework_summary_carry_invocation_run_id(monkeypatch, tmp_path):
    """AC10: 1-step echo workflow via WorkflowEngine.execute with a
    file-backed event log, after set_invocation_run_id("ridZ") and
    run_id="ridQ" -> emitted workflow_cost_rollup payload has
    invocation_run_id=="ridZ", resumed==True; rework-summary line (via
    HAL_REWORK_LOG -> tmp) has the same fields."""
    from event_log import EventLog
    from engine import WorkflowEngine

    rework_path = tmp_path / "rework.jsonl"
    monkeypatch.setenv("HAL_REWORK_LOG", str(rework_path))

    log_path = tmp_path / "events.jsonl"
    log = EventLog(log_path)
    eng = WorkflowEngine(event_log=log)
    eng.register(
        "echo_gh497",
        WorkflowDefinition(name="echo_gh497", steps=[StepContract(name="echo", execute=_emitting_echo_step)]),
    )

    telemetry_ctx.set_invocation_run_id("ridZ")
    try:
        eng.execute("echo_gh497", make_ctx(), run_id="ridQ")
    finally:
        telemetry_ctx.set_invocation_run_id(None)

    rows = EventLog(log_path).read_all()
    rollup_rows = [r for r in rows if r["event_type"] == "workflow_cost_rollup"]
    assert len(rollup_rows) == 1
    payload = rollup_rows[0]["payload"]
    assert payload.get("invocation_run_id") == "ridZ"
    assert payload.get("resumed") is True

    assert rework_path.exists(), "expected rework-summary line under HAL_REWORK_LOG tmp path"
    rework_lines = [ln for ln in rework_path.read_text().splitlines() if ln.strip()]
    assert len(rework_lines) >= 1
    summary_record = json.loads(rework_lines[-1])
    assert summary_record.get("invocation_run_id") == "ridZ"
    assert summary_record.get("resumed") is True


# ─── AC11 ───────────────────────────────────────────────────────────────────


def test_ac11_run_py_calls_set_invocation_run_id_after_resolved_run_id():
    """AC11: run.py source calls set_invocation_run_id( after the
    _resolved_run_id assignment (deterministic source-grep)."""
    run_py = Path(__file__).parent.parent / "run.py"
    source = run_py.read_text(encoding="utf-8")

    resolved_idx = source.find("_resolved_run_id =")
    assert resolved_idx != -1, "expected `_resolved_run_id =` assignment in run.py"

    call_idx = source.find("set_invocation_run_id(", resolved_idx)
    assert call_idx != -1, "expected set_invocation_run_id( call after _resolved_run_id assignment"


# ─── AC12 ───────────────────────────────────────────────────────────────────


def test_ac12_flags_catalog_includes_hal_reject_log():
    """AC12: flags_catalog includes a HAL_REJECT_LOG entry."""
    import flags_catalog

    assert "HAL_REJECT_LOG" in flags_catalog.FLAGS


# ─── AC13 ───────────────────────────────────────────────────────────────────


def test_ac13_satisfaction_reject_call_sites_pass_axes_text():
    """AC13: both record_satisfaction_reject( call sites in phase_6_review.py
    pass axes_text= — the call inside _write_satisfaction_doc uses
    axes_text=body, the call inside _write_satisfaction_doc_multi uses
    axes_text=composite_text (deterministic source-grep)."""
    phase6_py = Path(__file__).parent.parent / "workflows" / "phase_6_review.py"
    source = phase6_py.read_text(encoding="utf-8")

    single_start = source.find("def _write_satisfaction_doc(")
    multi_start = source.find("def _write_satisfaction_doc_multi(")
    assert single_start != -1, "expected _write_satisfaction_doc definition"
    assert multi_start != -1, "expected _write_satisfaction_doc_multi definition"
    assert single_start < multi_start

    single_body = source[single_start:multi_start]
    multi_body = source[multi_start:]

    single_call_idx = single_body.find("record_satisfaction_reject(")
    assert single_call_idx != -1, "expected a record_satisfaction_reject( call in _write_satisfaction_doc"
    single_call_end = single_body.find(")", single_call_idx)
    single_call_text = single_body[single_call_idx:single_call_end]
    assert "axes_text=body" in single_call_text, (
        "expected _write_satisfaction_doc's record_satisfaction_reject call "
        "to pass axes_text=body"
    )

    multi_call_idx = multi_body.find("record_satisfaction_reject(")
    assert multi_call_idx != -1, "expected a record_satisfaction_reject( call in _write_satisfaction_doc_multi"
    multi_call_end = multi_body.find(")", multi_call_idx)
    multi_call_text = multi_body[multi_call_idx:multi_call_end]
    assert "axes_text=composite_text" in multi_call_text, (
        "expected _write_satisfaction_doc_multi's record_satisfaction_reject "
        "call to pass axes_text=composite_text"
    )
