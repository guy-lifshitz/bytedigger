"""Tests for phase_7_synthesize workflow — Stage 2.7 synthesizer-only (3-step).

v1 scope:
    - Single Haiku synthesizer (post-deploy report + structured LEARNINGS block)
    - Phase 7 ends after write_synthesizer_artifact; no doc-updater subprocess
    - Living Doc updates delegated to /ship cascade in orchestrator mode
    - HARD GATE on synthesizer BLOCKED/NO_MARKER markers only

25e75663 migration: LLM stubs converted from shell-subprocess (passthrough_stub /
echo_stub / role_aware_stub / fail_stub) to register_backend spy/stub pattern.
Backend registered as "claude-subprocess" (the default) with overwrite=True;
autouse fixture resets after each test (§1i singleton).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "workflows"))

from contracts import StepResult, WorkflowContext  # noqa: E402
from derive_state import replay  # noqa: E402
from engine import WorkflowEngine  # noqa: E402
from event_log import EventLog  # noqa: E402
from llm_subprocess import register_backend, reset_backends  # noqa: E402
import telemetry_ctx  # noqa: E402
from phase_7_synthesize import (  # noqa: E402
    DEFAULT_LLM_COMMAND,
    DEFAULT_SYNTHESIZER_TIMEOUT_SEC,
    FIX_DOC_RELPATH,
    REPORT_DOC_RELPATH,
    REVIEW_DOC_RELPATH,
    SATISFACTION_DOC_RELPATH,
    SPEC_DOC_RELPATH,
    STATUS_BLOCKED,
    STATUS_DONE,
    STATUS_DONE_WITH_CONCERNS,
    STATUS_NEEDS_CONTEXT,
    STATUS_NO_MARKER,
    phase_7_synthesize_workflow,
)
import workflows  # noqa: E402

# ---------------------------------------------------------------------------
# §1i autouse teardown: restore _BACKENDS singleton + clear telemetry context
# between tests (mirrors test_25e75663_llm_semantic_params pattern).
# ---------------------------------------------------------------------------

import llm_subprocess as _llm_mod  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_backends_and_telemetry(monkeypatch):
    """§1i: restore _BACKENDS singleton + clear telemetry between tests.

    Also neutralises emit_resolver_resolved to prevent disk writes into
    SHARED/state during invoke_llm_subprocess calls.
    Pins HAL_RUNNER_BACKEND="claude-subprocess" so backend resolution is
    deterministic regardless of whether reference backends (e.g. agent-sdk,
    the new default) are registered in the shared _BACKENDS registry by
    other test modules on the CI runner (GH1129 / agreement 157E627A).
    """
    monkeypatch.setattr(_llm_mod, "emit_resolver_resolved", lambda *a, **kw: None)
    monkeypatch.setenv("HAL_RUNNER_BACKEND", "claude-subprocess")
    telemetry_ctx.clear_current_run()
    yield
    telemetry_ctx.clear_current_run()
    reset_backends()


# ---------------------------------------------------------------------------
# Stub backend classes (R2: replace shell-subprocess stubs with backends)
# ---------------------------------------------------------------------------

_STUB_BACKEND_NAME = "claude-subprocess"  # overwrite the default backend


def _ok_result(raw_response: str, step_name: str, extra_data: dict | None = None) -> StepResult:
    data: dict = {
        "raw_response": raw_response,
        "worker_written_paths": [],
        "manifest_source": "harness_tool_record",
    }
    if extra_data:
        data.update(extra_data)
    return StepResult(
        status="ok",
        data=data,
        duration_ms=0,
        step_name=step_name,
        error=None,
        error_code=None,
        recoverable=True,
    )


class _EchoBackend:
    """Returns a fixed payload as raw_response (mirrors echo_stub)."""

    def __init__(self, payload: str):
        self._payload = payload

    def __call__(self, **kw) -> StepResult:
        return _ok_result(self._payload, kw.get("step_name", "invoke_synthesizer_llm"),
                          kw.get("extra_data"))


class _PassthroughBackend:
    """Returns the prompt as raw_response (mirrors passthrough_stub)."""

    def __call__(self, **kw) -> StepResult:
        return _ok_result(kw.get("prompt", ""), kw.get("step_name", "invoke_synthesizer_llm"),
                          kw.get("extra_data"))


class _RoleAwareBackend:
    """Echoes prompt with STATUS: DONE appended (mirrors role_aware_stub)."""

    def __call__(self, **kw) -> StepResult:
        payload = kw.get("prompt", "") + "\n\nSTATUS: DONE\n"
        return _ok_result(payload, kw.get("step_name", "invoke_synthesizer_llm"),
                          kw.get("extra_data"))


class _FailBackend:
    """Returns E_LLM_EXIT error (mirrors fail_stub)."""

    def __init__(self, exit_code: int = 9):
        self._exit_code = exit_code

    def __call__(self, **kw) -> StepResult:
        return StepResult(
            status="error",
            data=None,
            duration_ms=0,
            step_name=kw.get("step_name", "invoke_synthesizer_llm"),
            error=f"process exited with code {self._exit_code}",
            error_code="E_LLM_EXIT",
            recoverable=False,
        )


def _register_echo(payload: str) -> None:
    register_backend(_STUB_BACKEND_NAME, _EchoBackend(payload),
                     manifest_source="harness_tool_record", overwrite=True)


def _register_passthrough() -> None:
    register_backend(_STUB_BACKEND_NAME, _PassthroughBackend(),
                     manifest_source="harness_tool_record", overwrite=True)


def _register_role_aware() -> None:
    register_backend(_STUB_BACKEND_NAME, _RoleAwareBackend(),
                     manifest_source="harness_tool_record", overwrite=True)


def _register_fail(exit_code: int = 9) -> None:
    register_backend(_STUB_BACKEND_NAME, _FailBackend(exit_code),
                     manifest_source="harness_tool_record", overwrite=True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def seed_spec(scratchpad: Path, body: str = "## US1\nAdd foo\n") -> Path:
    spec = scratchpad / SPEC_DOC_RELPATH
    spec.parent.mkdir(parents=True, exist_ok=True)
    spec.write_text(body)
    return spec


def seed_review(scratchpad: Path, body: str = "Composite review.\nVERDICT: PASS\n") -> Path:
    doc = scratchpad / REVIEW_DOC_RELPATH
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text(body)
    return doc


def seed_fix(scratchpad: Path, body: str = "FIX SKIPPED — no findings\n") -> Path:
    doc = scratchpad / FIX_DOC_RELPATH
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text(body)
    return doc


def seed_satisfaction(scratchpad: Path, body: str = "SCORE: 95\nVERDICT: PASS\n") -> Path:
    doc = scratchpad / SATISFACTION_DOC_RELPATH
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text(body)
    return doc


# --- shape --------------------------------------------------------------------


def test_workflow_definition_shape():
    wf = phase_7_synthesize_workflow()
    assert wf.name == "phase_7_synthesize"
    assert [s.name for s in wf.steps] == [
        "build_synthesizer_prompt",
        "invoke_synthesizer_llm",
        "write_synthesizer_artifact",
    ]


def test_defaults_are_sensible():
    # Phase 7 synthesize is documentation generation; pinned to Haiku.
    assert DEFAULT_LLM_COMMAND == ["claude", "-p", "--model", "haiku"]
    assert DEFAULT_SYNTHESIZER_TIMEOUT_SEC == 600


def test_canonical_doc_paths():
    import phase_7_synthesize as m
    assert REPORT_DOC_RELPATH == "post-deploy/post-deploy-report.md"
    for deleted in (
        "DOCS_DOC_RELPATH",
        "DEFAULT_DOCS_TIMEOUT_SEC",
        "DOCS_COMPLETE",
        "DOCS_SKIPPED",
        "DOCS_BLOCKED",
        "DOCS_NO_MARKER",
    ):
        assert not hasattr(m, deleted), f"{deleted} should have been deleted from phase_7_synthesize"


# --- prompt builders — token-spend guards ------------------------------------


def test_synthesizer_prompt_references_inputs_by_path_not_inlined(tmp_path):
    scratchpad = tmp_path / "scratch"
    seed_spec(scratchpad, "SPEC_BODY_DO_NOT_INLINE\n")
    seed_review(scratchpad, "REVIEW_BODY_DO_NOT_INLINE\nVERDICT: PASS\n")
    seed_fix(scratchpad, "FIX_BODY_DO_NOT_INLINE\nFIX SKIPPED\n")
    seed_satisfaction(scratchpad, "SAT_BODY_DO_NOT_INLINE\nSCORE: 95\nVERDICT: PASS\n")

    _register_role_aware()
    eng = WorkflowEngine()
    eng.register("p7", phase_7_synthesize_workflow())
    eng.execute("p7", make_ctx(scratchpad))

    report = (scratchpad / REPORT_DOC_RELPATH).read_text()
    assert str(scratchpad / SPEC_DOC_RELPATH) in report
    assert str(scratchpad / REVIEW_DOC_RELPATH) in report
    assert str(scratchpad / FIX_DOC_RELPATH) in report
    assert str(scratchpad / SATISFACTION_DOC_RELPATH) in report
    for marker in (
        "SPEC_BODY_DO_NOT_INLINE",
        "REVIEW_BODY_DO_NOT_INLINE",
        "FIX_BODY_DO_NOT_INLINE",
        "SAT_BODY_DO_NOT_INLINE",
    ):
        assert marker not in report


def test_synthesizer_prompt_emits_learnings_block_schema(tmp_path):
    scratchpad = tmp_path / "scratch"
    _register_passthrough()
    eng = WorkflowEngine()
    eng.register("p7", phase_7_synthesize_workflow())
    eng.execute("p7", make_ctx(scratchpad))

    report = (scratchpad / REPORT_DOC_RELPATH).read_text()
    assert "LEARNINGS BLOCK" in report
    assert "pattern=" in report
    assert "domain=" in report
    assert "confidence=" in report
    assert "approach=" in report
    assert "Step 4 of phase 7" not in report
    assert "/ship cascade" in report


def test_synthesizer_prompt_includes_producer_anti_fabrication_fragment(tmp_path):
    """Agreement 3B0E1323 Step 6: phase_7_synthesize migrated to centralized
    producer fragment alongside phase_4_architect.
    """
    scratchpad = tmp_path / "scratch"
    _register_passthrough()
    eng = WorkflowEngine()
    eng.register("p7", phase_7_synthesize_workflow())
    eng.execute("p7", make_ctx(scratchpad))

    report = (scratchpad / REPORT_DOC_RELPATH).read_text()
    assert "ANTI-FABRICATION — producer rules" in report
    assert "SELF-VERIFY" in report
    assert "FAILURE-MODE EXAMPLE (build 3E8E3A2A)" in report
    assert "Surface-specific for SYNTHESIZER" in report


def test_synthesizer_prompt_lists_read_first_paths(tmp_path):
    scratchpad = tmp_path / "scratch"
    _register_role_aware()
    eng = WorkflowEngine()
    eng.register("p7", phase_7_synthesize_workflow())
    eng.execute("p7", make_ctx(scratchpad))

    report = (scratchpad / REPORT_DOC_RELPATH).read_text()
    inj = scratchpad / "injection"
    for name in ("hal-memory", "constitution", "quality-gate", "producer-rules", "active-work"):
        assert f"{inj}/{name}.md" in report
    assert "## HAL Memory" not in report  # no inlining


def test_role_template_prepended_to_synthesizer_prompt(tmp_path):
    scratchpad = tmp_path / "scratch"
    role = tmp_path / "role.md"
    role.write_text("# Read-only role\n- no destructive ops\n")

    _register_role_aware()
    eng = WorkflowEngine()
    eng.register("p7", phase_7_synthesize_workflow())
    eng.execute(
        "p7",
        make_ctx(scratchpad, role_template_path=str(role)),
    )

    report = (scratchpad / REPORT_DOC_RELPATH).read_text()
    assert "# Read-only role" in report


# --- status parsing -----------------------------------------------------------


def test_synthesizer_status_done_passes(tmp_path):
    scratchpad = tmp_path / "scratch"
    _register_echo("Final Checkpoint.\nSTATUS: DONE\n")
    eng = WorkflowEngine()
    eng.register("p7", phase_7_synthesize_workflow())
    result, _ = eng.execute("p7", make_ctx(scratchpad))
    assert result.status == "ok"
    assert result.data["synthesizer_status"] == STATUS_DONE


def test_synthesizer_status_done_with_concerns_proceeds(tmp_path):
    """DONE_WITH_CONCERNS still completes synthesis — not a hard gate."""
    scratchpad = tmp_path / "scratch"
    _register_echo("Concerns: minor.\nSTATUS: DONE_WITH_CONCERNS\n")
    eng = WorkflowEngine()
    eng.register("p7", phase_7_synthesize_workflow())
    result, _ = eng.execute("p7", make_ctx(scratchpad))
    assert result.status == "ok"
    assert result.data["synthesizer_status"] == STATUS_DONE_WITH_CONCERNS


def test_synthesizer_status_blocked_returns_error(tmp_path):
    scratchpad = tmp_path / "scratch"
    _register_echo("Cannot synthesize.\nSTATUS: BLOCKED\n")
    eng = WorkflowEngine()
    eng.register("p7", phase_7_synthesize_workflow())
    result, _ = eng.execute("p7", make_ctx(scratchpad))
    assert result.status == "error"
    assert result.error_code == "E_SYNTHESIZER_BLOCKED"
    assert result.data["synthesizer_status"] == STATUS_BLOCKED
    assert (scratchpad / REPORT_DOC_RELPATH).exists()


def test_synthesizer_no_marker_returns_error(tmp_path):
    """Truncated/malformed synthesizer response (no STATUS marker) -> error."""
    scratchpad = tmp_path / "scratch"
    _register_echo("Some text without any STATUS marker.\n")
    eng = WorkflowEngine()
    eng.register("p7", phase_7_synthesize_workflow())
    result, _ = eng.execute("p7", make_ctx(scratchpad))
    assert result.status == "error"
    assert result.error_code == "E_SYNTHESIZER_NO_MARKER"
    assert result.data["synthesizer_status"] == STATUS_NO_MARKER


def test_synthesizer_status_parser_unit():
    from phase_7_synthesize import _parse_synthesizer_status

    assert _parse_synthesizer_status("STATUS: DONE") == STATUS_DONE
    assert _parse_synthesizer_status("STATUS: DONE_WITH_CONCERNS") == STATUS_DONE_WITH_CONCERNS
    assert _parse_synthesizer_status("STATUS: NEEDS_CONTEXT") == STATUS_NEEDS_CONTEXT
    assert _parse_synthesizer_status("STATUS: BLOCKED") == STATUS_BLOCKED
    assert _parse_synthesizer_status("nothing") == STATUS_NO_MARKER
    # Line-anchored contract (EEFD480F prose-false-match kill): "... wait, STATUS: BLOCKED"
    # is mid-line prose — does NOT match. STATUS: DONE is the sole line-anchored hit, so it wins.
    assert (
        _parse_synthesizer_status("STATUS: DONE\n... wait, STATUS: BLOCKED\n")
        == STATUS_DONE
    )
    # Last marker wins — trailing DONE beats earlier BLOCKED (LLM recovered)
    assert (
        _parse_synthesizer_status("STATUS: BLOCKED\nretried...\nSTATUS: DONE\n")
        == STATUS_DONE
    )
    # DONE_WITH_CONCERNS contains "DONE" substring — must not collapse
    assert (
        _parse_synthesizer_status("STATUS: DONE_WITH_CONCERNS")
        == STATUS_DONE_WITH_CONCERNS
    )


# --- per-step model overrides -------------------------------------------------


def test_per_step_command_override_synthesizer_only(tmp_path):
    """synthesizer_model overrides global model for synthesizer step.

    25e75663 migration: old test used synthesizer_llm_command (shell-stub with
    marker file write). New test uses a backend that records the model it was
    called with; asserts it received the per-step synthesizer_model value,
    not the global model value.
    """
    scratchpad = tmp_path / "scratch"
    syn_marker = tmp_path / "synth-was-called"

    class _ModelCheckBackend:
        """Writes marker file when called with model='synth-model'."""
        def __call__(self, **kw) -> StepResult:
            if kw.get("model") == "synth-model":
                syn_marker.write_text("1")
            return _ok_result("STATUS: DONE\n", kw.get("step_name", "s"),
                              kw.get("extra_data"))

    register_backend(_STUB_BACKEND_NAME, _ModelCheckBackend(),
                     manifest_source="harness_tool_record", overwrite=True)

    eng = WorkflowEngine()
    eng.register("p7", phase_7_synthesize_workflow())
    eng.execute(
        "p7",
        make_ctx(
            scratchpad,
            synthesizer_model="synth-model",
            model="global-model",
        ),
    )
    assert syn_marker.exists()


def test_per_step_command_falls_back_to_global(tmp_path):
    scratchpad = tmp_path / "scratch"
    _register_echo("STATUS: DONE\n")
    eng = WorkflowEngine()
    eng.register("p7", phase_7_synthesize_workflow())
    result, _ = eng.execute("p7", make_ctx(scratchpad))
    assert result.status == "ok"
    assert (scratchpad / REPORT_DOC_RELPATH).exists()


# --- error paths --------------------------------------------------------------


def test_missing_scratchpad_dir_raises(tmp_path):
    ctx = WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={"model": "sonnet"},
        question="task",
        session_id="s",
        persona="hal",
        framework=None,
        domain=None,
    )
    eng = WorkflowEngine()
    eng.register("p7", phase_7_synthesize_workflow())
    try:
        eng.execute("p7", ctx)
    except ValueError as e:
        assert "scratchpad_dir" in str(e)
    else:
        raise AssertionError("expected ValueError")


def test_synthesizer_llm_failure_returns_error(tmp_path):
    scratchpad = tmp_path / "scratch"
    _register_fail(7)
    eng = WorkflowEngine()
    eng.register("p7", phase_7_synthesize_workflow())
    result, _ = eng.execute("p7", make_ctx(scratchpad))

    assert result.status == "error"
    assert result.error_code == "E_LLM_EXIT"
    assert not (scratchpad / REPORT_DOC_RELPATH).exists()


# --- verification: events emitted, derived state matches expectation ----------


def test_events_emitted_three_steps_happy_path(tmp_path):
    log_path = tmp_path / "events.jsonl"
    log = EventLog(log_path)
    eng = WorkflowEngine(event_log=log)
    eng.register("phase_7_synthesize", phase_7_synthesize_workflow())

    _register_echo("STATUS: DONE\n")
    eng.execute(
        "phase_7_synthesize",
        make_ctx(tmp_path / "scratch"),
        run_id="rid-p7",
    )

    events = EventLog(log_path).read_all()
    finished = [e for e in events if e["event_type"] == "step_finished"]
    assert len(finished) == 3
    assert [e["payload"]["status"] for e in finished] == ["ok"] * 3
    assert [e["payload"]["step_name"] for e in finished] == [
        "build_synthesizer_prompt",
        "invoke_synthesizer_llm",
        "write_synthesizer_artifact",
    ]

    state = replay(events)
    run = state["runs"]["rid-p7"]
    assert run["workflow_name"] == "phase_7_synthesize"
    assert run["status"] == "ok"
    assert len(run["steps"]) == 3


def test_events_stop_at_synthesizer_on_blocked(tmp_path):
    """SYNTHESIZER BLOCKED -> 3 step_finished events; no further steps fire."""
    log_path = tmp_path / "events.jsonl"
    log = EventLog(log_path)
    eng = WorkflowEngine(event_log=log)
    eng.register("phase_7_synthesize", phase_7_synthesize_workflow())

    _register_echo("STATUS: BLOCKED\n")
    eng.execute(
        "phase_7_synthesize",
        make_ctx(tmp_path / "scratch"),
        run_id="rid-p7-blocked",
    )

    events = EventLog(log_path).read_all()
    finished = [e for e in events if e["event_type"] == "step_finished"]
    assert len(finished) == 3
    step_names = [e["payload"]["step_name"] for e in finished]
    assert step_names == [
        "build_synthesizer_prompt",
        "invoke_synthesizer_llm",
        "write_synthesizer_artifact",
    ]
    assert finished[-1]["payload"]["status"] == "error"


def test_registry_includes_phase_7_synthesize():
    eng = WorkflowEngine()
    workflows.register_all(eng)
    assert "phase_7_synthesize" in eng.registered()


# --- new: synthesizer-only happy path (AC #2 + #3) ---------------------------


def test_phase_7_synthesizer_only_happy_path(tmp_path):
    """Phase 7 with 3-step synthesizer-only: status ok, report written, no build-docs.md."""
    scratchpad = tmp_path / "scratch"
    seed_spec(scratchpad)
    seed_review(scratchpad)
    seed_fix(scratchpad)
    seed_satisfaction(scratchpad)

    _register_echo("STATUS: DONE\n")
    eng = WorkflowEngine()
    eng.register("p7", phase_7_synthesize_workflow())
    result, _ = eng.execute("p7", make_ctx(scratchpad))

    assert result.status == "ok"
    assert (scratchpad / REPORT_DOC_RELPATH).exists()
    assert not (scratchpad / "post-deploy/build-docs.md").exists()


# ─── telemetry digest tests (9AB90CA0) ─────


def test_telemetry_digest_disabled_by_default(tmp_path):
    """Without org_config.include_telemetry_digest, prompt has no TELEMETRY block."""
    scratchpad = tmp_path / "scratch"
    _register_passthrough()
    eng = WorkflowEngine()
    eng.register("p7", phase_7_synthesize_workflow())
    eng.execute("p7", make_ctx(scratchpad))

    report = (scratchpad / REPORT_DOC_RELPATH).read_text()
    assert "TELEMETRY" not in report


# --- agreement 157E627A / GH1129: default-backend registry pollution --------


class _FakeAgentSdkBackend:
    """Mirrors _FailBackend shape but with the CI-observed pollution error_code."""

    def __call__(self, **kw) -> StepResult:
        return StepResult(
            status="error",
            data=None,
            duration_ms=0,
            step_name=kw.get("step_name", "invoke_synthesizer_llm"),
            error="agent-sdk deps missing",
            error_code="E_LLM_API_DEPS_MISSING",
            recoverable=False,
        )


def test_synthesizer_no_marker_deterministic_under_agent_sdk_pollution(tmp_path):
    """Agreement 157E627A / GH1129: locks deterministic backend routing against
    agent-sdk default-backend registry pollution.

    CI global registry can end up with a fake/broken "agent-sdk" backend
    registered (e.g. leaked across test order). Because _DEFAULT_BACKEND is
    "agent-sdk" and the autouse fixture only deletes HAL_RUNNER_BACKEND (does
    not pin it to "claude-subprocess"), invoke_llm_subprocess resolves to
    whatever is registered under "agent-sdk" rather than the test's intended
    claude-subprocess stub -- causing a flaky, machine-dependent error_code.
    This test pins the expected outcome to the no-marker echo stub registered
    under claude-subprocess, independent of what pollutes agent-sdk.
    """
    scratchpad = tmp_path / "scratch"

    register_backend(
        "agent-sdk",
        _FakeAgentSdkBackend(),
        manifest_source="harness_tool_record",
        overwrite=True,
    )
    _register_echo("Some text without any STATUS marker.\n")

    eng = WorkflowEngine()
    eng.register("p7", phase_7_synthesize_workflow())
    result, _ = eng.execute("p7", make_ctx(scratchpad))

    assert result.status == "error"
    assert result.error_code == "E_SYNTHESIZER_NO_MARKER"
    assert result.data["synthesizer_status"] == STATUS_NO_MARKER


def test_telemetry_digest_enabled_includes_phase_summary(tmp_path, monkeypatch):
    """With include_telemetry_digest=True, prompt embeds per-phase wall + cost from log."""
    import json
    log_path = tmp_path / "build-events.jsonl"
    events = [
        {"ts": "2026-05-02T20:00:00.000Z", "run_id": "rA", "event_type": "workflow_started",
         "payload": {"workflow_name": "phase_5_implement"}},
        {"ts": "2026-05-02T20:00:10.000Z", "run_id": "rA", "event_type": "subprocess_exited",
         "payload": {"phase": "phase_5_implement", "model": "sonnet", "cost_usd": 0.12, "pid": 1}},
        {"ts": "2026-05-02T20:00:20.000Z", "run_id": "rA", "event_type": "workflow_finished",
         "payload": {"workflow_name": "phase_5_implement", "status": "ok", "wall_ms": 20000}},
        {"ts": "2026-05-02T20:00:25.000Z", "run_id": "rB", "event_type": "workflow_started",
         "payload": {"workflow_name": "phase_6_review"}},
        {"ts": "2026-05-02T20:00:35.000Z", "run_id": "rB", "event_type": "subprocess_exited",
         "payload": {"phase": "phase_6_review", "model": "haiku", "cost_usd": 0.03, "pid": 2}},
        {"ts": "2026-05-02T20:00:40.000Z", "run_id": "rB", "event_type": "workflow_finished",
         "payload": {"workflow_name": "phase_6_review", "status": "ok", "wall_ms": 15000}},
    ]
    log_path.write_text("\n".join(json.dumps(e) for e in events) + "\n")

    import derive_state as ds
    monkeypatch.setattr(ds, "default_log_path", lambda: log_path, raising=False)

    scratchpad = tmp_path / "scratch"
    _register_passthrough()
    eng = WorkflowEngine()
    eng.register("p7", phase_7_synthesize_workflow())
    eng.execute("p7", make_ctx(scratchpad, include_telemetry_digest=True))

    report = (scratchpad / REPORT_DOC_RELPATH).read_text()
    assert "TELEMETRY" in report
    assert "phase_5_implement" in report
    assert "phase_6_review" in report
    # Cost rendered to 4 decimals
    assert "0.12" in report or "0.1200" in report
    # Wall clock rendered as seconds
    assert "20.0s" in report or "20s" in report


def test_telemetry_digest_excludes_phase_7_self(tmp_path, monkeypatch):
    """Digest must not list phase_7_synthesize itself (would be circular/empty)."""
    import json
    log_path = tmp_path / "build-events.jsonl"
    events = [
        {"ts": "2026-05-02T20:00:00.000Z", "run_id": "r1", "event_type": "workflow_finished",
         "payload": {"workflow_name": "phase_5_implement", "status": "ok", "wall_ms": 1000}},
        {"ts": "2026-05-02T20:00:01.000Z", "run_id": "r2", "event_type": "workflow_finished",
         "payload": {"workflow_name": "phase_7_synthesize", "status": "ok", "wall_ms": 500}},
    ]
    log_path.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    import derive_state as ds
    monkeypatch.setattr(ds, "default_log_path", lambda: log_path, raising=False)

    scratchpad = tmp_path / "scratch"
    _register_passthrough()
    eng = WorkflowEngine()
    eng.register("p7", phase_7_synthesize_workflow())
    eng.execute("p7", make_ctx(scratchpad, include_telemetry_digest=True))

    report = (scratchpad / REPORT_DOC_RELPATH).read_text()
    # phase_5 listed in digest (search after the TELEMETRY marker only)
    digest_start = report.find("TELEMETRY")
    assert digest_start != -1
    digest_section = report[digest_start:digest_start + 500]
    assert "phase_5_implement" in digest_section
    # phase_7_synthesize NOT listed in digest section
    assert "phase_7_synthesize" not in digest_section


def test_telemetry_digest_empty_log_no_block(tmp_path, monkeypatch):
    """Enabled but log empty → no TELEMETRY block (silence over noise)."""
    log_path = tmp_path / "build-events.jsonl"
    log_path.write_text("")
    import derive_state as ds
    monkeypatch.setattr(ds, "default_log_path", lambda: log_path, raising=False)

    scratchpad = tmp_path / "scratch"
    _register_passthrough()
    eng = WorkflowEngine()
    eng.register("p7", phase_7_synthesize_workflow())
    eng.execute("p7", make_ctx(scratchpad, include_telemetry_digest=True))

    report = (scratchpad / REPORT_DOC_RELPATH).read_text()
    assert "TELEMETRY" not in report
