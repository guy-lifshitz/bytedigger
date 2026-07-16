"""RED tests for GH379 — Class 5 decorrelated verifier role in phase-6.

Spec: SHARED/memory/Decisions/2026-07-07_GH379_decorrelated_verifier_spec.md

§1q: the new production symbols (``_build_decorr_prompt``, ``_invoke_decorr_llm``,
``_write_decorr_artifact``, ``get_claude_decorrelated_verifier``) do NOT exist
yet. They are imported INSIDE each test function body so this module COLLECTS
cleanly and every test FAILS at assert/call time (ImportError/AttributeError
inside the test body, or a plain AssertionError against today's 18-step
workflow) — never at collection time (§1q / D1CF5FDF).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "workflows"))
sys.path.insert(0, str(HERE.parent / "lib"))

from contracts import StepResult, WorkflowContext  # noqa: E402
from phase_6_review import phase_6_review_workflow  # noqa: E402


def make_ctx(scratchpad: Path, *, question: str = "Add foo to bar", **org_extra) -> WorkflowContext:
    org = {"scratchpad_dir": str(scratchpad), **org_extra}
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question=question,
        session_id="test-session",
        persona="hal",
        framework=None,
        domain=None,
    )


def _prev_ok(data: dict, step_name: str = "verify_fix_typecheck") -> StepResult:
    return StepResult(status="ok", data=data, duration_ms=0, step_name=step_name)


# ─── AC1 ────────────────────────────────────────────────────────────────────


def test_ac1_three_new_steps_inserted_in_order_between_typecheck_and_satisfaction():
    wf = phase_6_review_workflow()
    names = [s.name for s in wf.steps]
    assert len(names) == 21, f"expected 21 steps, got {len(names)}: {names}"
    idx = names.index("verify_fix_typecheck")
    assert names[idx + 1 : idx + 4] == [
        "build_decorr_prompt",
        "invoke_decorr_llm",
        "write_decorr_artifact",
    ]
    assert names[idx + 4] == "build_satisfaction_prompt"


# ─── AC2 ────────────────────────────────────────────────────────────────────


def test_ac2_only_invoke_decorr_llm_has_resume_sentinel():
    wf = phase_6_review_workflow()
    by_name = {s.name: s for s in wf.steps}
    assert by_name["build_decorr_prompt"].resume_sentinel is False
    assert by_name["invoke_decorr_llm"].resume_sentinel is True
    assert by_name["write_decorr_artifact"].resume_sentinel is False


# ─── AC3 ────────────────────────────────────────────────────────────────────


def test_ac3_simple_complexity_skips_all_three_decorr_steps(tmp_path):
    from phase_6_review import _build_decorr_prompt, _invoke_decorr_llm, _write_decorr_artifact
    import telemetry_ctx
    from event_log import EventLog

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    ctx = make_ctx(scratchpad, complexity="SIMPLE")

    log_path = tmp_path / "events.jsonl"
    log = EventLog(log_path)
    telemetry_ctx.set_current_run(event_log=log, run_id="rid-ac3", step_name="build_decorr_prompt")
    try:
        r1 = _build_decorr_prompt(ctx, _prev_ok({}))
        assert r1.status == "ok"
        assert r1.data["decorr_skipped"] == "simple_tier"

        r2 = _invoke_decorr_llm(ctx, r1)
        assert r2.status == "ok"
        assert r2.data.get("decorr_skipped") == "simple_tier"

        r3 = _write_decorr_artifact(ctx, r2)
        assert r3.status == "ok"
    finally:
        telemetry_ctx.clear_current_run()

    events = EventLog(log_path).read_all()
    skipped = [e for e in events if e["event_type"] == "decorr_verify_skipped"]
    assert len(skipped) == 1
    verdicts = [e for e in events if e["event_type"] == "decorr_verify_verdict"]
    assert len(verdicts) == 0
    artifact = scratchpad / "reviews" / "build-decorr-verify.md"
    assert not artifact.exists()


# ─── AC4 ────────────────────────────────────────────────────────────────────


def test_ac4_feature_prompt_references_artifact_paths_not_full_bodies(tmp_path):
    from phase_6_review import _build_decorr_prompt

    scratchpad = tmp_path / "scratch"
    (scratchpad / "reviews").mkdir(parents=True)
    (scratchpad / "specs").mkdir(parents=True)
    spec_path = scratchpad / "specs" / "build-spec.md"
    spec_path.write_text("SECRET_SPEC_BODY_MARKER_TOKEN\n")
    review_path = scratchpad / "reviews" / "build-review.md"
    review_path.write_text("REVIEW_BODY_MARKER_TOKEN\n")
    fix_path = scratchpad / "reviews" / "build-fix.md"
    fix_path.write_text("FIX_BODY_MARKER_TOKEN\n")

    ctx = make_ctx(scratchpad, complexity="FEATURE")
    result = _build_decorr_prompt(ctx, _prev_ok({}))
    assert result.status == "ok"
    prompt = result.data["prompt"]

    assert str(spec_path) in prompt
    assert str(review_path) in prompt
    assert "DECORR VERDICT:" in prompt
    assert "SECRET_SPEC_BODY_MARKER_TOKEN" not in prompt
    assert "REVIEW_BODY_MARKER_TOKEN" not in prompt
    assert "FIX_BODY_MARKER_TOKEN" not in prompt


# ─── AC5 ────────────────────────────────────────────────────────────────────


def test_ac5_get_claude_decorrelated_verifier_config_driven_with_fallback(tmp_path, monkeypatch):
    import model_config
    from model_config import get_claude_decorrelated_verifier

    cfg_path = tmp_path / "models.json"
    cfg_path.write_text(json.dumps({"claude": {"decorrelated_verifier": "test-decorr-m"}}))
    monkeypatch.setattr(model_config, "_CONFIG_PATH", cfg_path)
    model_config.reset_cache()
    assert get_claude_decorrelated_verifier() == "test-decorr-m"

    absent_path = tmp_path / "models_absent.json"
    absent_path.write_text(json.dumps({"claude": {"primary": "sonnet"}}))
    monkeypatch.setattr(model_config, "_CONFIG_PATH", absent_path)
    model_config.reset_cache()
    assert get_claude_decorrelated_verifier() == "fable"
    model_config.reset_cache()


# ─── AC6 (§1l: patch the infra seam, not the UUT) ──────────────────────────


def test_ac6_invoke_decorr_llm_dispatches_config_model_and_hard_gate_false(tmp_path, monkeypatch):
    import phase_6_review
    from phase_6_review import _invoke_decorr_llm
    from model_config import get_claude_decorrelated_verifier

    calls: list[dict] = []

    def _fake_invoke(**kw):
        calls.append(kw)
        return StepResult(
            status="ok",
            data={"raw_response": "DECORR VERDICT: CLEAR"},
            duration_ms=0,
            step_name=kw.get("step_name", "invoke_decorr_llm"),
        )

    # §1l stub-passability: patch the infra seam (invoke_llm_subprocess), never
    # the UUT (_invoke_decorr_llm) itself.
    monkeypatch.setattr(phase_6_review, "invoke_llm_subprocess", _fake_invoke)

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    ctx = make_ctx(scratchpad, complexity="FEATURE")
    prev = _prev_ok({"prompt": "refute this ship"}, step_name="build_decorr_prompt")

    result = _invoke_decorr_llm(ctx, prev)

    assert len(calls) == 1, f"expected exactly 1 invoke_llm_subprocess call, got {len(calls)}"
    assert calls[0]["model"] == get_claude_decorrelated_verifier()
    assert calls[0]["hard_gate"] is False
    assert result.status == "ok"
    # stdout-handoff pin (gate must-fix #2): result.data["stdout"] must equal
    # the invoke result's raw_response verbatim.
    assert result.data["stdout"] == "DECORR VERDICT: CLEAR"


# ─── AC7 ────────────────────────────────────────────────────────────────────


def test_ac7_verdict_parser_last_match_wins(tmp_path):
    from phase_6_review import _write_decorr_artifact
    import telemetry_ctx
    from event_log import EventLog

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    ctx = make_ctx(scratchpad)
    prev = _prev_ok(
        {"stdout": "…DECORR VERDICT: SUSPECT…\n…\nDECORR VERDICT: CLEAR"},
        step_name="invoke_decorr_llm",
    )

    log_path = tmp_path / "events.jsonl"
    log = EventLog(log_path)
    telemetry_ctx.set_current_run(event_log=log, run_id="rid-ac7", step_name="write_decorr_artifact")
    try:
        result = _write_decorr_artifact(ctx, prev)
    finally:
        telemetry_ctx.clear_current_run()

    assert result.status == "ok"
    events = EventLog(log_path).read_all()
    verdicts = [e for e in events if e["event_type"] == "decorr_verify_verdict"]
    assert len(verdicts) == 1
    assert verdicts[0]["payload"]["verdict"] == "CLEAR"


# ─── AC8 ────────────────────────────────────────────────────────────────────


def test_ac8_absent_unparseable_or_invoke_error_defaults_to_conservative_suspect(tmp_path):
    from phase_6_review import _write_decorr_artifact
    import telemetry_ctx
    from event_log import EventLog

    def _run(prev_data: dict, run_id: str):
        scratchpad = tmp_path / f"scratch-{run_id}"
        scratchpad.mkdir()
        ctx = make_ctx(scratchpad)
        prev = _prev_ok(prev_data, step_name="invoke_decorr_llm")
        log_path = tmp_path / f"events-{run_id}.jsonl"
        log = EventLog(log_path)
        telemetry_ctx.set_current_run(event_log=log, run_id=run_id, step_name="write_decorr_artifact")
        try:
            result = _write_decorr_artifact(ctx, prev)
        finally:
            telemetry_ctx.clear_current_run()
        events = EventLog(log_path).read_all()
        verdicts = [e for e in events if e["event_type"] == "decorr_verify_verdict"]
        assert len(verdicts) == 1, f"expected exactly 1 verdict event, got {len(verdicts)}"
        return result, verdicts[0]["payload"]

    r1, p1 = _run({"stdout": "no marker anywhere in this output"}, "unparseable")
    assert r1.status == "ok"
    assert p1["verdict"] == "SUSPECT"
    assert p1["parse_source"] == "conservative_default"

    r2, p2 = _run({"decorr_error": "E_LLM_TIMEOUT", "stdout": ""}, "invoke-error")
    assert r2.status == "ok"
    assert p2["verdict"] == "SUSPECT"
    assert p2["parse_source"] == "conservative_default"


# ─── AC9 ────────────────────────────────────────────────────────────────────


def test_ac9_advisory_suspect_ok_with_exactly_one_verdict_event(tmp_path):
    from phase_6_review import _write_decorr_artifact
    import telemetry_ctx
    from event_log import EventLog

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    ctx = make_ctx(scratchpad)  # no decorrelated_verify_enforce key -> falsy default
    prev = _prev_ok({"stdout": "garbage, no marker"}, step_name="invoke_decorr_llm")

    log_path = tmp_path / "events.jsonl"
    log = EventLog(log_path)
    telemetry_ctx.set_current_run(event_log=log, run_id="rid-ac9", step_name="write_decorr_artifact")
    try:
        result = _write_decorr_artifact(ctx, prev)
    finally:
        telemetry_ctx.clear_current_run()

    assert result.status == "ok"
    events = EventLog(log_path).read_all()
    verdicts = [e for e in events if e["event_type"] == "decorr_verify_verdict"]
    assert len(verdicts) == 1
    assert verdicts[0]["payload"]["enforce"] is False


# ─── AC10 ───────────────────────────────────────────────────────────────────


def test_ac10_enforce_suspect_returns_error(tmp_path):
    from phase_6_review import _write_decorr_artifact
    import telemetry_ctx
    from event_log import EventLog

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    ctx = make_ctx(scratchpad, decorrelated_verify_enforce=True)
    prev = _prev_ok({"stdout": "garbage, no marker"}, step_name="invoke_decorr_llm")

    log_path = tmp_path / "events.jsonl"
    log = EventLog(log_path)
    telemetry_ctx.set_current_run(event_log=log, run_id="rid-ac10", step_name="write_decorr_artifact")
    try:
        result = _write_decorr_artifact(ctx, prev)
    finally:
        telemetry_ctx.clear_current_run()

    assert result.status == "error"
    assert result.error_code == "E_DECORR_VERIFY_SUSPECT"

    # emit-before-error pin (gate must-fix #3): exactly ONE verdict event with
    # enforce=True, even on the error-terminal path.
    events = EventLog(log_path).read_all()
    verdicts = [e for e in events if e["event_type"] == "decorr_verify_verdict"]
    assert len(verdicts) == 1, f"expected exactly 1 verdict event, got {len(verdicts)}"
    assert verdicts[0]["payload"]["enforce"] is True


# ─── AC11 ───────────────────────────────────────────────────────────────────


def test_ac11_enforce_clear_returns_ok():
    import tempfile

    from phase_6_review import _write_decorr_artifact

    with tempfile.TemporaryDirectory() as tmp:
        scratchpad = Path(tmp) / "scratch"
        scratchpad.mkdir()
        ctx = make_ctx(scratchpad, decorrelated_verify_enforce=True)
        prev = _prev_ok({"stdout": "findings ok\nDECORR VERDICT: CLEAR\n"}, step_name="invoke_decorr_llm")

        result = _write_decorr_artifact(ctx, prev)

        assert result.status == "ok"


# ─── AC12 ───────────────────────────────────────────────────────────────────


def test_ac12_artifact_written_verbatim_at_scratchpad_configured_path(tmp_path):
    from phase_6_review import _write_decorr_artifact

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    ctx = make_ctx(scratchpad)
    stdout = "Findings: none of note.\n\nDECORR VERDICT: CLEAR\n"
    prev = _prev_ok({"stdout": stdout}, step_name="invoke_decorr_llm")

    result = _write_decorr_artifact(ctx, prev)

    assert result.status == "ok"
    artifact = scratchpad / "reviews" / "build-decorr-verify.md"
    assert artifact.is_file(), f"expected artifact at {artifact}"
    assert artifact.read_text(encoding="utf-8") == stdout


# ─── AC13 (gate must-fix #1: §1n OWN path tested end-to-end) ───────────────


def test_ac13_invoke_failure_own_path(tmp_path, monkeypatch):
    import phase_6_review
    from phase_6_review import _invoke_decorr_llm, _write_decorr_artifact
    import telemetry_ctx
    from event_log import EventLog

    def _fake_invoke_failure(**kw):
        return StepResult(
            status="error",
            data=None,
            duration_ms=0,
            step_name=kw.get("step_name", "invoke_decorr_llm"),
            error="subprocess timed out",
            error_code="E_LLM_TIMEOUT",
            recoverable=True,
        )

    # §1l stub-passability: patch the infra seam, never the UUT itself.
    monkeypatch.setattr(phase_6_review, "invoke_llm_subprocess", _fake_invoke_failure)

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    ctx = make_ctx(scratchpad, complexity="FEATURE")
    prev = _prev_ok({"prompt": "refute this ship"}, step_name="build_decorr_prompt")

    log_path = tmp_path / "events.jsonl"
    log = EventLog(log_path)
    telemetry_ctx.set_current_run(event_log=log, run_id="rid-ac13", step_name="invoke_decorr_llm")
    try:
        invoke_result = _invoke_decorr_llm(ctx, prev)
    finally:
        telemetry_ctx.clear_current_run()

    # §1n OWN: subprocess failure never becomes status="error" at this step.
    assert invoke_result.status == "ok"
    assert invoke_result.data.get("decorr_error")
    assert invoke_result.data.get("stdout") == ""

    events = EventLog(log_path).read_all()
    failed_events = [e for e in events if e["event_type"] == "decorr_verify_invoke_failed"]
    assert len(failed_events) == 1, f"expected exactly 1 decorr_verify_invoke_failed event, got {len(failed_events)}"

    # Chain the ok-with-decorr_error result into the write step: conservative
    # SUSPECT verdict must still be resolved downstream.
    log_path2 = tmp_path / "events2.jsonl"
    log2 = EventLog(log_path2)
    telemetry_ctx.set_current_run(event_log=log2, run_id="rid-ac13-write", step_name="write_decorr_artifact")
    try:
        write_result = _write_decorr_artifact(ctx, invoke_result)
    finally:
        telemetry_ctx.clear_current_run()

    assert write_result.status == "ok"
    events2 = EventLog(log_path2).read_all()
    verdicts = [e for e in events2 if e["event_type"] == "decorr_verify_verdict"]
    assert len(verdicts) == 1
    assert verdicts[0]["payload"]["verdict"] == "SUSPECT"
