"""Tests for phase_4_architect workflow — Stage 2.3 port (single architect v0).

Multi-architect parallel fanout (A/B/C roles per phase-4-architect.md) is
deliberately deferred to Stage 2.4+ pending StepContract.fanout. v0 produces
ONE combined architecture doc.

25e75663 migration: shell-stub helpers (echo_stub/passthrough_stub/fail_stub/
slow_stub) replaced by register_backend spy pattern (R2). org_config key
llm_command is silently ignored by _resolve_model; tests now use registered
stub backends via HAL_RUNNER_BACKEND env pin.
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
from phase_4_architect import (  # noqa: E402
    ARCHITECTURE_DOC_RELPATH,
    DEFAULT_LLM_COMMAND,
    DEFAULT_LLM_TIMEOUT_SEC,
    phase_4_architect_workflow,
)
import llm_subprocess  # noqa: E402
from llm_subprocess import register_backend, reset_backends  # noqa: E402
import workflows  # noqa: E402


# ─── stub backend helpers ─────────────────────────────────────────────────────


class _EchoBackend:
    """Returns a fixed raw_response, merges extra_data (R2 echo_stub replacement)."""

    def __init__(self, payload: str = "## Approach\nbuild it\n## Files\nfoo.py\n"):
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
    """Returns prompt as raw_response (R2 passthrough_stub replacement)."""

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
    """Returns error result with specified error_code (R2 fail_stub/slow_stub/bogus-cmd replacement)."""

    def __init__(self, error_code: str = "E_LLM_EXIT", recoverable: bool = False):
        self._error_code = error_code
        self._recoverable = recoverable
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
            recoverable=self._recoverable,
        )


# ─── autouse fixture: reset backend registry + neutralise telemetry ───────────

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _reset_backends_fixture(monkeypatch):
    """§1i: restore _BACKENDS singleton between tests; neutralise resolver telemetry."""
    monkeypatch.setattr(llm_subprocess, "emit_resolver_resolved", lambda *a, **kw: None)
    yield
    reset_backends()


def _register_echo(monkeypatch, payload: str = "## Approach\nbuild it\n## Files\nfoo.py\n") -> _EchoBackend:
    b = _EchoBackend(payload)
    register_backend("claude-subprocess", b, manifest_source="harness_tool_record", overwrite=True)
    monkeypatch.setenv("HAL_RUNNER_BACKEND", "claude-subprocess")
    return b


def _register_passthrough(monkeypatch) -> _PassthroughBackend:
    b = _PassthroughBackend()
    register_backend("claude-subprocess", b, manifest_source="harness_tool_record", overwrite=True)
    monkeypatch.setenv("HAL_RUNNER_BACKEND", "claude-subprocess")
    return b


def _register_fail(monkeypatch, error_code: str = "E_LLM_EXIT", recoverable: bool = False) -> _FailBackend:
    b = _FailBackend(error_code, recoverable)
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


def seed_research(scratchpad: Path, files: dict[str, str]) -> None:
    research = scratchpad / "research"
    research.mkdir(parents=True, exist_ok=True)
    for name, body in files.items():
        (research / name).write_text(body)


# ─── shape ────────────────────────────────────────────────────────────────────


def test_workflow_definition_shape():
    wf = phase_4_architect_workflow()
    assert wf.name == "phase_4_architect"
    assert [s.name for s in wf.steps] == [
        "check_decision_doc_skip",
        "build_architect_prompt",
        "invoke_architect_llm",
        "write_architecture_doc",
    ]


def test_defaults_are_sensible():
    """Architect timeout is heavier than discovery (heavier work)."""
    # Phase 4 architect is pinned to Opus (serious reasoning + design).
    assert DEFAULT_LLM_COMMAND == ["claude", "-p", "--model", "opus"]
    assert DEFAULT_LLM_TIMEOUT_SEC == 600  # 2x discovery


# ─── prompt builder ───────────────────────────────────────────────────────────


def test_prompt_lists_research_files_not_inlined(tmp_path, monkeypatch):
    """Token-spend guard: prompt must reference research/*.md by path, not inline contents."""
    scratchpad = tmp_path / "scratch"
    seed_research(scratchpad, {"phase-2-explore.md": "EXPLORE_FINDINGS_BODY\n", "phase-3.md": "CLARIFY_BODY\n"})

    _register_passthrough(monkeypatch)

    eng = WorkflowEngine()
    eng.register("p4", phase_4_architect_workflow())
    eng.execute("p4", make_ctx(scratchpad))

    doc = (scratchpad / ARCHITECTURE_DOC_RELPATH).read_text()
    assert str(scratchpad / "research" / "phase-2-explore.md") in doc
    assert str(scratchpad / "research" / "phase-3.md") in doc
    # Token-spend guard: do NOT inline research file contents
    assert "EXPLORE_FINDINGS_BODY" not in doc
    assert "CLARIFY_BODY" not in doc


def test_prompt_includes_producer_anti_fabrication_fragment(tmp_path, monkeypatch):
    """Agreement 3B0E1323 Step 6: phase_4_architect migrated to centralized
    producer fragment. Verifies the fragment + ARCHITECT-specific bullets ship
    together in the prompt.
    """
    scratchpad = tmp_path / "scratch"
    seed_research(scratchpad, {"r.md": "x"})

    _register_passthrough(monkeypatch)

    eng = WorkflowEngine()
    eng.register("p4", phase_4_architect_workflow())
    eng.execute("p4", make_ctx(scratchpad))

    doc = (scratchpad / ARCHITECTURE_DOC_RELPATH).read_text()
    assert "ANTI-FABRICATION — producer rules" in doc
    assert "SELF-VERIFY" in doc
    assert "FAILURE-MODE EXAMPLE (build 3E8E3A2A)" in doc
    assert "Surface-specific for ARCHITECT" in doc


def test_prompt_lists_read_first_paths_not_inlined(tmp_path, monkeypatch):
    scratchpad = tmp_path / "scratch"
    seed_research(scratchpad, {"r.md": "x"})

    _register_passthrough(monkeypatch)

    eng = WorkflowEngine()
    eng.register("p4", phase_4_architect_workflow())
    eng.execute("p4", make_ctx(scratchpad))

    doc = (scratchpad / ARCHITECTURE_DOC_RELPATH).read_text()
    inj = scratchpad / "injection"
    assert "READ_FIRST" in doc
    for name in ("hal-memory", "constitution", "quality-gate", "producer-rules", "active-work"):
        assert f"{inj}/{name}.md" in doc
    # Don't inline injection file contents
    assert "## HAL Memory" not in doc
    assert "## Quality Gate" not in doc


def test_prompt_includes_role_template_when_provided(tmp_path, monkeypatch):
    scratchpad = tmp_path / "scratch"
    role = tmp_path / "role.md"
    role.write_text("# Read-only role\n- no destructive ops\n")

    _register_passthrough(monkeypatch)

    eng = WorkflowEngine()
    eng.register("p4", phase_4_architect_workflow())
    eng.execute(
        "p4",
        make_ctx(scratchpad, role_template_path=str(role)),
    )

    doc = (scratchpad / ARCHITECTURE_DOC_RELPATH).read_text()
    assert "# Read-only role" in doc


def test_security_classification_high_changes_prompt(tmp_path, monkeypatch):
    scratchpad = tmp_path / "scratch"

    _register_passthrough(monkeypatch)

    eng = WorkflowEngine()
    eng.register("p4", phase_4_architect_workflow())
    eng.execute(
        "p4",
        make_ctx(scratchpad, security_classification="HIGH"),
    )

    doc = (scratchpad / ARCHITECTURE_DOC_RELPATH).read_text()
    assert "SECURITY FOCUS (HIGH)" in doc
    assert "Threat model" in doc
    assert "Secret management" in doc


def test_security_classification_low_default(tmp_path, monkeypatch):
    scratchpad = tmp_path / "scratch"

    _register_passthrough(monkeypatch)

    eng = WorkflowEngine()
    eng.register("p4", phase_4_architect_workflow())
    eng.execute("p4", make_ctx(scratchpad))

    doc = (scratchpad / ARCHITECTURE_DOC_RELPATH).read_text()
    assert "SECURITY FOCUS (LOW)" in doc


def test_invalid_security_classification_raises(tmp_path, monkeypatch):
    _register_echo(monkeypatch)

    eng = WorkflowEngine()
    eng.register("p4", phase_4_architect_workflow())
    ctx = make_ctx(tmp_path / "scratch", security_classification="CRITICAL")
    try:
        eng.execute("p4", ctx)
    except ValueError as e:
        assert "security_classification" in str(e)
    else:
        raise AssertionError("expected ValueError")


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
    eng.register("p4", phase_4_architect_workflow())
    try:
        eng.execute("p4", ctx)
    except ValueError as e:
        assert "scratchpad_dir" in str(e)
    else:
        raise AssertionError("expected ValueError")


def test_research_dir_missing_is_ok(tmp_path, monkeypatch):
    """No research dir → prompt notes 'none', workflow still completes."""
    scratchpad = tmp_path / "scratch"
    _register_passthrough(monkeypatch)

    eng = WorkflowEngine()
    eng.register("p4", phase_4_architect_workflow())
    eng.execute("p4", make_ctx(scratchpad))

    doc = (scratchpad / ARCHITECTURE_DOC_RELPATH).read_text()
    assert "RESEARCH FINDINGS: (none — research dir empty)" in doc


def test_research_files_listed_alphabetically(tmp_path, monkeypatch):
    """Stable ordering — events should be deterministic."""
    scratchpad = tmp_path / "scratch"
    seed_research(scratchpad, {"c.md": "x", "a.md": "x", "b.md": "x"})

    _register_passthrough(monkeypatch)

    eng = WorkflowEngine()
    eng.register("p4", phase_4_architect_workflow())
    eng.execute("p4", make_ctx(scratchpad))

    doc = (scratchpad / ARCHITECTURE_DOC_RELPATH).read_text()
    a_pos = doc.index(str(scratchpad / "research" / "a.md"))
    b_pos = doc.index(str(scratchpad / "research" / "b.md"))
    c_pos = doc.index(str(scratchpad / "research" / "c.md"))
    assert a_pos < b_pos < c_pos


def test_writes_architecture_doc_at_canonical_path(tmp_path, monkeypatch):
    scratchpad = tmp_path / "scratch"
    _register_echo(monkeypatch)

    eng = WorkflowEngine()
    eng.register("p4", phase_4_architect_workflow())
    eng.execute("p4", make_ctx(scratchpad))

    assert (scratchpad / ARCHITECTURE_DOC_RELPATH).is_file()
    assert ARCHITECTURE_DOC_RELPATH == "architecture/architecture.md"


# ─── LLM invocation error paths ───────────────────────────────────────────────


def test_llm_nonzero_exit_returns_error_step(tmp_path, monkeypatch):
    _register_fail(monkeypatch, "E_LLM_EXIT")

    eng = WorkflowEngine()
    eng.register("p4", phase_4_architect_workflow())
    result, _ = eng.execute("p4", make_ctx(tmp_path / "scratch"))
    assert result.status == "error"
    assert result.error_code == "E_LLM_EXIT"
    assert not (tmp_path / "scratch" / ARCHITECTURE_DOC_RELPATH).exists()


def test_llm_command_missing_returns_error_step(tmp_path, monkeypatch):
    """25e75663 R2: stub returns E_LLM_CMD_MISSING (binary-not-found path).

    The bogus-binary injection via llm_command= is no longer possible via the
    seam; a stub backend returning E_LLM_CMD_MISSING (recoverable=False)
    preserves the behavioral contract.
    """
    _register_fail(monkeypatch, "E_LLM_CMD_MISSING", recoverable=False)

    eng = WorkflowEngine()
    eng.register("p4", phase_4_architect_workflow())
    result, _ = eng.execute("p4", make_ctx(tmp_path / "scratch"))
    assert result.status == "error"
    assert result.error_code == "E_LLM_CMD_MISSING"
    assert result.recoverable is False


def test_llm_timeout_returns_error_step(tmp_path, monkeypatch):
    """25e75663 R2: stub returns E_LLM_TIMEOUT (slow subprocess path).

    The slow-stub injection via llm_command= is no longer possible via the
    seam; a stub backend returning E_LLM_TIMEOUT preserves the behavioral
    contract.
    """
    _register_fail(monkeypatch, "E_LLM_TIMEOUT", recoverable=True)

    eng = WorkflowEngine()
    eng.register("p4", phase_4_architect_workflow())
    result, _ = eng.execute("p4", make_ctx(tmp_path / "scratch"))
    assert result.status == "error"
    assert result.error_code == "E_LLM_TIMEOUT"


# ─── verification: events emitted, derived state matches expectation ──────────


def test_events_emitted_and_derived_state_matches_spec(tmp_path, monkeypatch):
    log_path = tmp_path / "events.jsonl"
    log = EventLog(log_path)
    eng = WorkflowEngine(event_log=log)
    eng.register("phase_4_architect", phase_4_architect_workflow())

    _register_echo(monkeypatch)

    eng.execute(
        "phase_4_architect",
        make_ctx(tmp_path / "scratch"),
        run_id="rid-p4",
    )

    assert (tmp_path / "scratch" / ARCHITECTURE_DOC_RELPATH).is_file()

    events = EventLog(log_path).read_all()
    finished_steps = [e for e in events if e["event_type"] == "step_finished"]
    assert len(finished_steps) == 4
    assert [e["payload"]["status"] for e in finished_steps] == ["ok"] * 4
    assert [e["payload"]["step_name"] for e in finished_steps] == [
        "check_decision_doc_skip",
        "build_architect_prompt",
        "invoke_architect_llm",
        "write_architecture_doc",
    ]

    state = replay(events)
    run = state["runs"]["rid-p4"]
    assert run["workflow_name"] == "phase_4_architect"
    assert run["status"] == "ok"
    assert len(run["steps"]) == 4


def test_registry_includes_phase_4_architect():
    eng = WorkflowEngine()
    workflows.register_all(eng)
    assert "phase_4_architect" in eng.registered()


# ─── 03DD394B: phase_4 decision_doc self-skip ────────────────────────────────


def test_phase_4_skips_when_decision_doc_present_and_feature(tmp_path, monkeypatch):
    """AC2: decision_doc present + FEATURE → entire phase skips, llm NOT called."""
    monkeypatch.chdir(tmp_path)
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    ddoc = tmp_path / "decision.md"
    ddoc.write_text("# Decision\nsome content\n")

    # Register fail backend — must NOT be called if skip works correctly
    _register_fail(monkeypatch)

    eng = WorkflowEngine()
    eng.register("p4", phase_4_architect_workflow())
    result, _ = eng.execute(
        "p4",
        make_ctx(scratchpad, decision_doc=str(ddoc), complexity="FEATURE"),
    )
    assert result.status == "ok"
    assert result.data.get("skipped") is True
    assert result.data.get("decision_doc") == str(ddoc)
    assert not (scratchpad / ARCHITECTURE_DOC_RELPATH).exists()


def test_phase_4_skips_when_decision_doc_present_and_complex(tmp_path, monkeypatch):
    """AC3: decision_doc + COMPLEX → entire phase skips (post-B07DBB97 relax)."""
    monkeypatch.chdir(tmp_path)
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    ddoc = tmp_path / "decision.md"
    ddoc.write_text("# Decision\nsome content\n")

    # Register fail backend — must NOT be called if skip works correctly
    _register_fail(monkeypatch)

    eng = WorkflowEngine()
    eng.register("p4", phase_4_architect_workflow())
    result, _ = eng.execute(
        "p4",
        make_ctx(scratchpad, decision_doc=str(ddoc), complexity="COMPLEX"),
    )
    assert result.status == "ok"
    assert result.data.get("skipped") is True


def test_phase_4_runs_normally_when_no_decision_doc(tmp_path, monkeypatch):
    """AC4: no decision_doc → architecture doc IS written (existing behavior preserved)."""
    scratchpad = tmp_path / "scratch"
    _register_echo(monkeypatch)

    eng = WorkflowEngine()
    eng.register("p4", phase_4_architect_workflow())
    result, _ = eng.execute(
        "p4",
        make_ctx(scratchpad, complexity="FEATURE"),
    )
    assert result.status == "ok"
    assert not result.data.get("skipped")
    assert (scratchpad / ARCHITECTURE_DOC_RELPATH).is_file()


def test_phase_4_passthrough_when_decision_doc_path_missing(tmp_path, monkeypatch):
    """AC6 corollary: decision_doc set but file doesn't resolve → phase runs normally
    (does NOT skip; does NOT crash; does NOT call llm with passthrough-poisoned input)."""
    scratchpad = tmp_path / "scratch"
    _register_echo(monkeypatch)

    eng = WorkflowEngine()
    eng.register("p4", phase_4_architect_workflow())
    result, _ = eng.execute(
        "p4",
        make_ctx(
            scratchpad,
            decision_doc="/nonexistent/path/to/decision.md",
            complexity="FEATURE",
        ),
    )
    assert result.status == "ok"
    assert not result.data.get("skipped")
    assert (scratchpad / ARCHITECTURE_DOC_RELPATH).is_file()
