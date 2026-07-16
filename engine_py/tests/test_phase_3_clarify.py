"""Tests for phase_3_clarify workflow — Stage 2.9 port (AUTONOMOUS-only v0).

Single LLM call, single output doc (specs/assumptions.md). STATUS marker
parsed last-marker-wins (rfind), same vocabulary as phase_2_explore.

25e75663 migration: shell-stub helpers (echo_stub/passthrough_stub/fail_stub)
replaced by register_backend spy pattern (R2). org_config key llm_command
is silently ignored by _resolve_model; tests now use model= routing via
HAL_RUNNER_BACKEND env pin + registered stub backend.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "workflows"))

from contracts import WorkflowContext, StepResult  # noqa: E402
from derive_state import replay  # noqa: E402
from engine import WorkflowEngine  # noqa: E402
from event_log import EventLog  # noqa: E402
from phase_3_clarify import (  # noqa: E402
    CLARIFY_DOC_RELPATH,
    DEFAULT_CLARIFY_TIMEOUT_SEC,
    DEFAULT_LLM_COMMAND,
    EXPLORATION_DOC_RELPATH,
    phase_3_clarify_workflow,
)
import llm_subprocess  # noqa: E402
from llm_subprocess import register_backend, reset_backends  # noqa: E402
import workflows  # noqa: E402


# ─── stub backend helpers ─────────────────────────────────────────────────────


class _EchoBackend:
    """Returns a fixed raw_response string, merges extra_data (R2 echo_stub replacement)."""

    def __init__(self, payload: str):
        self._payload = payload
        self.calls: list[dict] = []

    def __call__(self, *, prompt, model, timeout_sec, step_name, extra_data,
                 allowed_tools, run_ctx, hard_gate, gate_label, straggler_cfg,
                 idle_timeout_sec) -> StepResult:
        self.calls.append({"model": model, "step_name": step_name})
        data: dict = {
            "raw_response": self._payload,
            "response_bytes": len(self._payload.encode()),
            "worker_written_paths": [],
            "manifest_source": "harness_tool_record",
        }
        if extra_data:
            data.update(extra_data)
        data["worker_written_paths"] = []
        data["manifest_source"] = "harness_tool_record"
        return StepResult(status="ok", data=data, duration_ms=0, step_name=step_name)


class _PassthroughBackend:
    """Returns the prompt as raw_response (R2 passthrough_stub replacement)."""

    def __init__(self):
        self.calls: list[dict] = []

    def __call__(self, *, prompt, model, timeout_sec, step_name, extra_data,
                 allowed_tools, run_ctx, hard_gate, gate_label, straggler_cfg,
                 idle_timeout_sec) -> StepResult:
        self.calls.append({"model": model, "step_name": step_name})
        data: dict = {
            "raw_response": prompt,
            "response_bytes": len(prompt.encode()),
            "worker_written_paths": [],
            "manifest_source": "harness_tool_record",
        }
        if extra_data:
            data.update(extra_data)
        data["worker_written_paths"] = []
        data["manifest_source"] = "harness_tool_record"
        return StepResult(status="ok", data=data, duration_ms=0, step_name=step_name)


class _FailBackend:
    """Returns error with E_LLM_EXIT (R2 fail_stub replacement)."""

    def __init__(self, error_code: str = "E_LLM_EXIT"):
        self._error_code = error_code
        self.calls: list[dict] = []

    def __call__(self, *, prompt, model, timeout_sec, step_name, extra_data,
                 allowed_tools, run_ctx, hard_gate, gate_label, straggler_cfg,
                 idle_timeout_sec) -> StepResult:
        self.calls.append({"model": model, "step_name": step_name})
        return StepResult(
            status="error",
            data=None,
            duration_ms=0,
            step_name=step_name,
            error="stub error",
            error_code=self._error_code,
            recoverable=False,
        )


# ─── autouse fixture: reset backend registry + neutralise telemetry ───────────


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_backends_fixture(monkeypatch):
    """§1i: restore _BACKENDS singleton between tests; neutralise resolver telemetry."""
    monkeypatch.setattr(llm_subprocess, "emit_resolver_resolved", lambda *a, **kw: None)
    yield
    reset_backends()


def _register_echo(payload: str, monkeypatch) -> _EchoBackend:
    """Register an echo backend as claude-subprocess and pin env."""
    b = _EchoBackend(payload)
    register_backend("claude-subprocess", b, manifest_source="harness_tool_record", overwrite=True)
    monkeypatch.setenv("HAL_RUNNER_BACKEND", "claude-subprocess")
    return b


def _register_passthrough(monkeypatch) -> _PassthroughBackend:
    """Register a passthrough backend as claude-subprocess and pin env."""
    b = _PassthroughBackend()
    register_backend("claude-subprocess", b, manifest_source="harness_tool_record", overwrite=True)
    monkeypatch.setenv("HAL_RUNNER_BACKEND", "claude-subprocess")
    return b


def _register_fail(monkeypatch, error_code: str = "E_LLM_EXIT") -> _FailBackend:
    """Register a fail backend as claude-subprocess and pin env."""
    b = _FailBackend(error_code)
    register_backend("claude-subprocess", b, manifest_source="harness_tool_record", overwrite=True)
    monkeypatch.setenv("HAL_RUNNER_BACKEND", "claude-subprocess")
    return b


# ─── helpers ──────────────────────────────────────────────────────────────────


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


def seed_exploration(scratchpad: Path, body: str = "## Findings\nfoo.py:10\n") -> Path:
    p = scratchpad / EXPLORATION_DOC_RELPATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    return p


# ─── shape ────────────────────────────────────────────────────────────────────


def test_workflow_definition_shape():
    wf = phase_3_clarify_workflow()
    assert wf.name == "phase_3_clarify"
    assert [s.name for s in wf.steps] == [
        "check_decision_doc_skip",
        "build_clarify_prompt",
        "invoke_clarify_llm",
        "write_clarify_doc",
    ]


def test_defaults_are_sensible():
    # Phase 3 clarify is pinned to Haiku (function-based model framework).
    assert DEFAULT_LLM_COMMAND == ["claude", "-p", "--model", "haiku"]
    assert DEFAULT_CLARIFY_TIMEOUT_SEC == 300


def test_canonical_doc_paths():
    assert CLARIFY_DOC_RELPATH == "specs/assumptions.md"
    assert EXPLORATION_DOC_RELPATH == "research/exploration.md"


# ─── prompt builder ───────────────────────────────────────────────────────────


def test_prompt_references_exploration_by_path_not_inlined(tmp_path, monkeypatch):
    """Token-spend guard: exploration findings referenced by path."""
    scratchpad = tmp_path / "scratch"
    seed_exploration(scratchpad, "EXPLORATION_BODY_DO_NOT_INLINE\n")

    _register_passthrough(monkeypatch)

    eng = WorkflowEngine()
    eng.register("p3", phase_3_clarify_workflow())
    eng.execute("p3", make_ctx(scratchpad))

    doc = (scratchpad / CLARIFY_DOC_RELPATH).read_text()
    explore_path = scratchpad / EXPLORATION_DOC_RELPATH
    assert str(explore_path) in doc
    assert "EXPLORATION_BODY_DO_NOT_INLINE" not in doc


def test_prompt_handles_missing_exploration_gracefully(tmp_path, monkeypatch):
    scratchpad = tmp_path / "scratch"
    _register_passthrough(monkeypatch)

    eng = WorkflowEngine()
    eng.register("p3", phase_3_clarify_workflow())
    eng.execute("p3", make_ctx(scratchpad))

    doc = (scratchpad / CLARIFY_DOC_RELPATH).read_text()
    assert "EXPLORATION FINDINGS: (none" in doc


def test_prompt_lists_read_first_paths(tmp_path, monkeypatch):
    scratchpad = tmp_path / "scratch"
    _register_passthrough(monkeypatch)

    eng = WorkflowEngine()
    eng.register("p3", phase_3_clarify_workflow())
    eng.execute("p3", make_ctx(scratchpad))

    doc = (scratchpad / CLARIFY_DOC_RELPATH).read_text()
    inj = scratchpad / "injection"
    for name in ("hal-memory", "constitution", "quality-gate", "producer-rules", "active-work"):
        assert f"{inj}/{name}.md" in doc


def test_prompt_contains_feature_request(tmp_path, monkeypatch):
    scratchpad = tmp_path / "scratch"
    _register_passthrough(monkeypatch)

    eng = WorkflowEngine()
    eng.register("p3", phase_3_clarify_workflow())
    eng.execute(
        "p3",
        make_ctx(scratchpad, question="UNIQUE_REQUEST_TOKEN_3"),
    )

    doc = (scratchpad / CLARIFY_DOC_RELPATH).read_text()
    assert "UNIQUE_REQUEST_TOKEN_3" in doc


def test_prompt_declares_autonomous_mode(tmp_path, monkeypatch):
    """v0 is AUTONOMOUS-only; prompt must say so explicitly."""
    scratchpad = tmp_path / "scratch"
    _register_passthrough(monkeypatch)

    eng = WorkflowEngine()
    eng.register("p3", phase_3_clarify_workflow())
    eng.execute("p3", make_ctx(scratchpad))

    doc = (scratchpad / CLARIFY_DOC_RELPATH).read_text()
    assert "AUTONOMOUS" in doc


def test_role_template_prepended(tmp_path, monkeypatch):
    scratchpad = tmp_path / "scratch"
    role = tmp_path / "role.md"
    role.write_text("# Read-only role\n")

    _register_passthrough(monkeypatch)

    eng = WorkflowEngine()
    eng.register("p3", phase_3_clarify_workflow())
    eng.execute(
        "p3",
        make_ctx(scratchpad, role_template_path=str(role)),
    )

    doc = (scratchpad / CLARIFY_DOC_RELPATH).read_text()
    assert "# Read-only role" in doc


# ─── status marker parsing ────────────────────────────────────────────────────


def test_marker_done(tmp_path, monkeypatch):
    scratchpad = tmp_path / "scratch"
    _register_echo("body\nSTATUS: DONE\n", monkeypatch)

    eng = WorkflowEngine()
    eng.register("p3", phase_3_clarify_workflow())
    result, _ = eng.execute("p3", make_ctx(scratchpad))
    assert result.status == "ok"
    assert result.data["marker"] == "DONE"


def test_marker_done_with_concerns(tmp_path, monkeypatch):
    scratchpad = tmp_path / "scratch"
    _register_echo("body\nSTATUS: DONE_WITH_CONCERNS\n", monkeypatch)

    eng = WorkflowEngine()
    eng.register("p3", phase_3_clarify_workflow())
    result, _ = eng.execute("p3", make_ctx(scratchpad))
    assert result.status == "ok"
    assert result.data["marker"] == "DONE_WITH_CONCERNS"


def test_marker_blocked(tmp_path, monkeypatch):
    scratchpad = tmp_path / "scratch"
    _register_echo("STATUS: BLOCKED\n", monkeypatch)

    eng = WorkflowEngine()
    eng.register("p3", phase_3_clarify_workflow())
    result, _ = eng.execute("p3", make_ctx(scratchpad))
    assert result.error_code == "E_CLARIFY_BLOCKED"
    assert (scratchpad / CLARIFY_DOC_RELPATH).is_file()


def test_marker_needs_context(tmp_path, monkeypatch):
    scratchpad = tmp_path / "scratch"
    _register_echo("STATUS: NEEDS_CONTEXT\n", monkeypatch)

    eng = WorkflowEngine()
    eng.register("p3", phase_3_clarify_workflow())
    result, _ = eng.execute("p3", make_ctx(scratchpad))
    assert result.error_code == "E_CLARIFY_NEEDS_CONTEXT"


def test_marker_missing(tmp_path, monkeypatch):
    scratchpad = tmp_path / "scratch"
    _register_echo("nothing useful here\n", monkeypatch)

    eng = WorkflowEngine()
    eng.register("p3", phase_3_clarify_workflow())
    result, _ = eng.execute("p3", make_ctx(scratchpad))
    assert result.error_code == "E_CLARIFY_NO_MARKER"
    assert result.data["marker"] is None


def test_last_marker_wins(tmp_path, monkeypatch):
    """STATUS: BLOCKED quoted earlier, then resolved to DONE."""
    scratchpad = tmp_path / "scratch"
    body = "Considered STATUS: BLOCKED but resolved.\nSTATUS: DONE\n"
    _register_echo(body, monkeypatch)

    eng = WorkflowEngine()
    eng.register("p3", phase_3_clarify_workflow())
    result, _ = eng.execute("p3", make_ctx(scratchpad))
    assert result.status == "ok"
    assert result.data["marker"] == "DONE"


# ─── per-step model override ──────────────────────────────────────────────────


def test_clarify_model_overrides_global(tmp_path, monkeypatch):
    """25e75663 R4: clarify_model key overrides global model key.

    Register a spy backend; verify that the model kwarg passed to the backend
    equals the clarify_model value (not the global model default).
    """
    scratchpad = tmp_path / "scratch"

    # Use a spy that records the model kwarg; always returns STATUS: DONE
    spy = _EchoBackend("STATUS: DONE\n")
    register_backend("claude-subprocess", spy, manifest_source="harness_tool_record", overwrite=True)
    monkeypatch.setenv("HAL_RUNNER_BACKEND", "claude-subprocess")

    eng = WorkflowEngine()
    eng.register("p3", phase_3_clarify_workflow())
    eng.execute(
        "p3",
        make_ctx(
            scratchpad,
            clarify_model="claude-haiku-4-pinned",
            model="claude-opus-4-should-not-be-used",
        ),
    )

    # The spy should have been called with clarify_model, not the global model
    llm_calls = [c for c in spy.calls if c["step_name"] == "invoke_clarify_llm"]
    assert len(llm_calls) == 1, f"Expected 1 LLM call, got {len(llm_calls)}: {spy.calls}"
    assert llm_calls[0]["model"] == "claude-haiku-4-pinned", (
        f"clarify_model override not applied; got model={llm_calls[0]['model']!r}"
    )


def test_per_step_command_falls_back_to_global(tmp_path, monkeypatch):
    scratchpad = tmp_path / "scratch"
    _register_echo("STATUS: DONE\n", monkeypatch)

    eng = WorkflowEngine()
    eng.register("p3", phase_3_clarify_workflow())
    result, _ = eng.execute("p3", make_ctx(scratchpad))
    assert result.status == "ok"


# ─── error paths ──────────────────────────────────────────────────────────────


def test_missing_scratchpad_dir_raises(tmp_path):
    ctx = WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={},
        question="task",
        session_id="s",
        persona="hal",
        framework=None,
        domain=None,
    )
    eng = WorkflowEngine()
    eng.register("p3", phase_3_clarify_workflow())
    try:
        eng.execute("p3", ctx)
    except ValueError as e:
        assert "scratchpad_dir" in str(e)
    else:
        raise AssertionError("expected ValueError")


def test_llm_failure_blocks_doc_write(tmp_path, monkeypatch):
    scratchpad = tmp_path / "scratch"
    _register_fail(monkeypatch, "E_LLM_EXIT")

    eng = WorkflowEngine()
    eng.register("p3", phase_3_clarify_workflow())
    result, _ = eng.execute("p3", make_ctx(scratchpad))

    assert result.status == "error"
    assert result.error_code == "E_LLM_EXIT"
    assert not (scratchpad / CLARIFY_DOC_RELPATH).exists()


# ─── events + registry ────────────────────────────────────────────────────────


def test_events_emitted_three_steps(tmp_path, monkeypatch):
    log_path = tmp_path / "events.jsonl"
    log = EventLog(log_path)
    eng = WorkflowEngine(event_log=log)
    eng.register("phase_3_clarify", phase_3_clarify_workflow())

    _register_echo("STATUS: DONE\n", monkeypatch)

    eng.execute(
        "phase_3_clarify",
        make_ctx(tmp_path / "scratch"),
        run_id="rid-p3",
    )

    events = EventLog(log_path).read_all()
    finished = [e for e in events if e["event_type"] == "step_finished"]
    assert len(finished) == 4
    assert [e["payload"]["status"] for e in finished] == ["ok"] * 4
    assert [e["payload"]["step_name"] for e in finished] == [
        "check_decision_doc_skip",
        "build_clarify_prompt",
        "invoke_clarify_llm",
        "write_clarify_doc",
    ]

    state = replay(events)
    run = state["runs"]["rid-p3"]
    assert run["workflow_name"] == "phase_3_clarify"
    assert run["status"] == "ok"


def test_registry_includes_phase_3_clarify():
    eng = WorkflowEngine()
    workflows.register_all(eng)
    assert "phase_3_clarify" in eng.registered()


# ─── decision_doc self-skip (05F83B1B surface 6) ─────────────────────────────


def test_phase_3_skips_when_decision_doc_present_and_feature(tmp_path, monkeypatch):
    """RED: decision_doc exists + FEATURE → entire phase skips with skipped=True."""
    monkeypatch.chdir(tmp_path)
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    ddoc = tmp_path / "decision.md"
    ddoc.write_text("# Decision\nsome content\n")

    # Register fail backend — must NOT be called if skip works correctly
    _register_fail(monkeypatch)

    eng = WorkflowEngine()
    eng.register("p3", phase_3_clarify_workflow())
    result, _ = eng.execute(
        "p3",
        make_ctx(
            scratchpad,
            decision_doc=str(ddoc),
            complexity="FEATURE",
        ),
    )

    assert result.status == "ok"
    assert result.data["skipped"] is True
    assert "decision_doc" in result.data
    assert result.data["decision_doc"] == str(ddoc)
    # No output file written
    assert not (scratchpad / CLARIFY_DOC_RELPATH).exists()


def test_phase_3_runs_normally_when_no_decision_doc(tmp_path, monkeypatch):
    """Invariant: no decision_doc → phase runs (current behavior preserved)."""
    scratchpad = tmp_path / "scratch"
    _register_echo("STATUS: DONE\n", monkeypatch)

    eng = WorkflowEngine()
    eng.register("p3", phase_3_clarify_workflow())
    result, _ = eng.execute(
        "p3",
        make_ctx(scratchpad, complexity="FEATURE"),
    )
    assert result.status == "ok"
    assert not result.data.get("skipped")
    assert (scratchpad / CLARIFY_DOC_RELPATH).exists()


def test_phase_3_runs_normally_when_complexity_not_feature(tmp_path, monkeypatch):
    """Invariant: decision_doc present but complexity=COMPLEX → phase runs."""
    scratchpad = tmp_path / "scratch"
    ddoc = tmp_path / "decision.md"
    ddoc.write_text("# Decision\n")

    _register_echo("STATUS: DONE\n", monkeypatch)

    eng = WorkflowEngine()
    eng.register("p3", phase_3_clarify_workflow())
    result, _ = eng.execute(
        "p3",
        make_ctx(
            scratchpad,
            decision_doc=str(ddoc),
            complexity="COMPLEX",
        ),
    )
    assert result.status == "ok"
    assert not result.data.get("skipped")
    assert (scratchpad / CLARIFY_DOC_RELPATH).exists()


def test_phase_3_runs_normally_when_decision_doc_path_missing(tmp_path, monkeypatch):
    """Invariant guard: decision_doc path doesn't exist → phase runs."""
    scratchpad = tmp_path / "scratch"
    _register_echo("STATUS: DONE\n", monkeypatch)

    eng = WorkflowEngine()
    eng.register("p3", phase_3_clarify_workflow())
    result, _ = eng.execute(
        "p3",
        make_ctx(
            scratchpad,
            decision_doc="/nonexistent/path.md",
            complexity="FEATURE",
        ),
    )
    assert result.status == "ok"
    assert not result.data.get("skipped")
    assert (scratchpad / CLARIFY_DOC_RELPATH).exists()


# ─── D57F354F: phase_status_marker telemetry ─────────────────────────────────


def test_phase_3_emits_status_marker_event_on_needs_context(tmp_path, monkeypatch):
    """AC4: phase_3_clarify emits phase_status_marker even when final step returns error."""
    log_path = tmp_path / "events.jsonl"
    log = EventLog(log_path)
    eng = WorkflowEngine(event_log=log)
    eng.register("phase_3_clarify", phase_3_clarify_workflow())

    _register_echo("STATUS: NEEDS_CONTEXT\n", monkeypatch)

    eng.execute(
        "phase_3_clarify",
        make_ctx(tmp_path / "scratch"),
        run_id="rid-p3-ncx",
    )
    events = EventLog(log_path).read_all()
    marker_events = [e for e in events if e["event_type"] == "phase_status_marker"]
    assert len(marker_events) == 1
    payload = marker_events[0]["payload"]
    assert payload["phase"] == 3
    assert payload["marker"] == "NEEDS_CONTEXT"


def test_phase_3_emits_status_marker_event_on_blocked(tmp_path, monkeypatch):
    """AC6: phase_3_clarify emits phase_status_marker with marker=BLOCKED."""
    log_path = tmp_path / "events.jsonl"
    log = EventLog(log_path)
    eng = WorkflowEngine(event_log=log)
    eng.register("phase_3_clarify", phase_3_clarify_workflow())

    _register_echo("STATUS: BLOCKED\n", monkeypatch)

    eng.execute(
        "phase_3_clarify",
        make_ctx(tmp_path / "scratch"),
        run_id="rid-p3-blkd",
    )
    events = EventLog(log_path).read_all()
    marker_events = [e for e in events if e["event_type"] == "phase_status_marker"]
    assert len(marker_events) == 1
    payload = marker_events[0]["payload"]
    assert payload["phase"] == 3
    assert payload["marker"] == "BLOCKED"
