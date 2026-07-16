"""Tests for phase_2_explore workflow — Stage 2.9 port.

FEATURE/COMPLEX-only phase. Single LLM call, single output doc, STATUS marker
parsed last-marker-wins (rfind). Stub LLM via registered backend (25e75663
migration: llm_command→model seam).

Note on passthrough stub: the prompt itself enumerates all 4 STATUS markers
(DONE / DONE_WITH_CONCERNS / NEEDS_CONTEXT / BLOCKED) in the output schema.
With rfind last-marker-wins, passthrough echoes back a response whose last
marker is BLOCKED — so passthrough tests assert prompt content but the
workflow returns status="error" with E_EXPLORE_BLOCKED. The doc IS written
to disk before the error return, so read_text() still works.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "workflows"))

from contracts import WorkflowContext, StepResult  # noqa: E402
from derive_state import replay  # noqa: E402
from engine import WorkflowEngine  # noqa: E402
from event_log import EventLog  # noqa: E402
from phase_2_explore import (  # noqa: E402
    DEFAULT_EXPLORE_TIMEOUT_SEC,
    DEFAULT_LLM_COMMAND,
    EXPLORE_DOC_RELPATH,
    phase_2_explore_workflow,
)
import workflows  # noqa: E402
import llm_subprocess  # noqa: E402
from llm_subprocess import register_backend, reset_backends  # noqa: E402
import telemetry_ctx  # noqa: E402


# ─── §1i autouse teardown: restore _BACKENDS singleton + clear telemetry ──────


@pytest.fixture(autouse=True)
def _reset_backends_and_telemetry(monkeypatch):
    """§1i: restore _BACKENDS singleton + clear telemetry context between tests.

    Also neutralise emit_resolver_resolved to prevent disk writes into
    SHARED/state during invoke_llm_subprocess calls.
    """
    monkeypatch.setattr(llm_subprocess, "emit_resolver_resolved", lambda *a, **kw: None)
    telemetry_ctx.clear_current_run()
    yield
    telemetry_ctx.clear_current_run()
    reset_backends()


# ─── stub backend helpers ─────────────────────────────────────────────────────


class _EchoBackend:
    """Returns a fixed payload as raw_response (ignores prompt)."""

    def __init__(self, payload: str):
        self._payload = payload

    def __call__(self, **kw) -> StepResult:
        data = {
            "raw_response": self._payload,
            "worker_written_paths": [],
            "manifest_source": "harness_tool_record",
        }
        data.update(kw.get("extra_data") or {})
        return StepResult(
            status="ok",
            data=data,
            duration_ms=0,
            step_name=kw.get("step_name", "stub"),
            error=None,
            error_code=None,
            recoverable=True,
        )


class _PassthroughBackend:
    """Returns the prompt as raw_response (echoes stdin)."""

    def __call__(self, **kw) -> StepResult:
        data = {
            "raw_response": kw.get("prompt", ""),
            "worker_written_paths": [],
            "manifest_source": "harness_tool_record",
        }
        data.update(kw.get("extra_data") or {})
        return StepResult(
            status="ok",
            data=data,
            duration_ms=0,
            step_name=kw.get("step_name", "stub"),
            error=None,
            error_code=None,
            recoverable=True,
        )


class _FailBackend:
    """Returns E_LLM_EXIT with the given exit code in the error message."""

    def __init__(self, exit_code: int = 7):
        self._code = exit_code

    def __call__(self, **kw) -> StepResult:
        return StepResult(
            status="error",
            data={
                "raw_response": "",
                "worker_written_paths": [],
                "manifest_source": "harness_tool_record",
            },
            duration_ms=0,
            step_name=kw.get("step_name", "stub"),
            error=f"process exited with non-zero code {self._code}",
            error_code="E_LLM_EXIT",
            recoverable=True,
        )


def _reg_echo(payload: str) -> None:
    """Register an echo stub as the claude-subprocess backend."""
    register_backend(
        "claude-subprocess",
        _EchoBackend(payload),
        manifest_source="harness_tool_record",
        overwrite=True,
    )


def _reg_passthrough() -> None:
    """Register a passthrough stub as the claude-subprocess backend."""
    register_backend(
        "claude-subprocess",
        _PassthroughBackend(),
        manifest_source="harness_tool_record",
        overwrite=True,
    )


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


# ─── shape ────────────────────────────────────────────────────────────────────


def test_workflow_definition_shape():
    wf = phase_2_explore_workflow()
    assert wf.name == "phase_2_explore"
    assert [s.name for s in wf.steps] == [
        "check_decision_doc_skip",
        "build_explore_prompt",
        "invoke_explore_llm",
        "write_explore_doc",
    ]


def test_defaults_are_sensible():
    # Phase 2 explore is pinned to Sonnet (F3A8F4FC §10b — breadth > per-agent depth).
    assert DEFAULT_LLM_COMMAND == ["claude", "-p", "--model", "sonnet"]
    assert DEFAULT_EXPLORE_TIMEOUT_SEC == 600


def test_canonical_doc_path():
    assert EXPLORE_DOC_RELPATH == "research/exploration.md"


# ─── prompt builder ───────────────────────────────────────────────────────────


def test_prompt_lists_read_first_paths_not_inlined(tmp_path):
    """Token-spend guard: injection files referenced by path, not inlined."""
    scratchpad = tmp_path / "scratch"
    inj = scratchpad / "injection"
    inj.mkdir(parents=True)
    (inj / "hal-memory.md").write_text("HAL_MEMORY_BODY_DO_NOT_INLINE")
    (inj / "constitution.md").write_text("CONSTITUTION_BODY_DO_NOT_INLINE")

    _reg_passthrough()
    eng = WorkflowEngine()
    eng.register("p2", phase_2_explore_workflow())
    eng.execute("p2", make_ctx(scratchpad, model="sonnet"))

    doc = (scratchpad / EXPLORE_DOC_RELPATH).read_text()
    for name in ("hal-memory", "constitution", "quality-gate", "producer-rules", "active-work"):
        assert f"{inj}/{name}.md" in doc
    assert "HAL_MEMORY_BODY_DO_NOT_INLINE" not in doc
    assert "CONSTITUTION_BODY_DO_NOT_INLINE" not in doc


def test_prompt_contains_feature_request(tmp_path):
    scratchpad = tmp_path / "scratch"
    _reg_passthrough()
    eng = WorkflowEngine()
    eng.register("p2", phase_2_explore_workflow())
    eng.execute(
        "p2",
        make_ctx(scratchpad, question="UNIQUE_REQUEST_TOKEN", model="sonnet"),
    )

    doc = (scratchpad / EXPLORE_DOC_RELPATH).read_text()
    assert "UNIQUE_REQUEST_TOKEN" in doc


def test_prompt_includes_producer_anti_fabrication_fragment(tmp_path):
    """Agreement 3B0E1323 Step 5: phase_2_explore is a producer (not a reviewer).
    First migration to the new producer fragment in the anti_hallucination plugin —
    centralizes evidence-quote + scope + invention rules previously duplicated inline.
    """
    scratchpad = tmp_path / "scratch"
    _reg_passthrough()
    eng = WorkflowEngine()
    eng.register("p2", phase_2_explore_workflow())
    eng.execute("p2", make_ctx(scratchpad, model="sonnet"))

    doc = (scratchpad / EXPLORE_DOC_RELPATH).read_text()
    # Producer-fragment anchors (distinct from reviewer fragment — no "EVIDENCE QUOTE" header here)
    assert "ANTI-FABRICATION — producer rules" in doc
    assert "SELF-VERIFY" in doc
    assert "FAILURE-MODE EXAMPLE (build 3E8E3A2A)" in doc
    # Phase-specific bullets must still be present
    assert "Surface-specific for EXPLORE" in doc


def test_complex_complexity_includes_security_perspective(tmp_path):
    scratchpad = tmp_path / "scratch"
    _reg_passthrough()
    eng = WorkflowEngine()
    eng.register("p2", phase_2_explore_workflow())
    eng.execute(
        "p2",
        make_ctx(scratchpad, complexity="COMPLEX", model="sonnet"),
    )

    doc = (scratchpad / EXPLORE_DOC_RELPATH).read_text()
    assert "Security" in doc


def test_feature_complexity_omits_security_perspective(tmp_path):
    scratchpad = tmp_path / "scratch"
    _reg_passthrough()
    eng = WorkflowEngine()
    eng.register("p2", phase_2_explore_workflow())
    eng.execute(
        "p2",
        make_ctx(scratchpad, complexity="FEATURE", model="sonnet"),
    )

    doc = (scratchpad / EXPLORE_DOC_RELPATH).read_text()
    assert "Security & Error Handling" not in doc


def test_invalid_complexity_raises(tmp_path):
    scratchpad = tmp_path / "scratch"
    _reg_passthrough()
    eng = WorkflowEngine()
    eng.register("p2", phase_2_explore_workflow())
    try:
        eng.execute(
            "p2",
            make_ctx(scratchpad, complexity="WHATEVER", model="sonnet"),
        )
    except ValueError as e:
        assert "complexity must be one of" in str(e)
    else:
        raise AssertionError("expected ValueError")


def test_role_template_prepended(tmp_path):
    scratchpad = tmp_path / "scratch"
    role = tmp_path / "role.md"
    role.write_text("# Read-only role\n- no destructive ops\n")

    _reg_passthrough()
    eng = WorkflowEngine()
    eng.register("p2", phase_2_explore_workflow())
    eng.execute(
        "p2",
        make_ctx(scratchpad, model="sonnet", role_template_path=str(role)),
    )

    doc = (scratchpad / EXPLORE_DOC_RELPATH).read_text()
    assert "# Read-only role" in doc


def test_role_template_missing_file_silently_skipped(tmp_path):
    scratchpad = tmp_path / "scratch"
    _reg_echo("body\nSTATUS: DONE\n")
    eng = WorkflowEngine()
    eng.register("p2", phase_2_explore_workflow())
    result, _ = eng.execute(
        "p2",
        make_ctx(
            scratchpad,
            model="sonnet",
            role_template_path=str(tmp_path / "nope.md"),
        ),
    )
    # No crash; workflow proceeds.
    assert result.data["marker"] == "DONE"


# ─── status marker parsing (last-marker-wins via rfind) ───────────────────────


def test_marker_done(tmp_path):
    scratchpad = tmp_path / "scratch"
    _reg_echo("findings\n\nSTATUS: DONE\n")
    eng = WorkflowEngine()
    eng.register("p2", phase_2_explore_workflow())
    result, _ = eng.execute(
        "p2",
        make_ctx(scratchpad, model="sonnet"),
    )
    assert result.status == "ok"
    assert result.data["marker"] == "DONE"


def test_marker_done_with_concerns(tmp_path):
    scratchpad = tmp_path / "scratch"
    _reg_echo("findings\nSTATUS: DONE_WITH_CONCERNS\n")
    eng = WorkflowEngine()
    eng.register("p2", phase_2_explore_workflow())
    result, _ = eng.execute(
        "p2",
        make_ctx(scratchpad, model="sonnet"),
    )
    assert result.status == "ok"
    assert result.data["marker"] == "DONE_WITH_CONCERNS"


def test_marker_blocked(tmp_path):
    scratchpad = tmp_path / "scratch"
    _reg_echo("STATUS: BLOCKED\n")
    eng = WorkflowEngine()
    eng.register("p2", phase_2_explore_workflow())
    result, _ = eng.execute(
        "p2",
        make_ctx(scratchpad, model="sonnet"),
    )
    assert result.status == "error"
    assert result.error_code == "E_EXPLORE_BLOCKED"
    assert result.data["marker"] == "BLOCKED"
    # Doc still written for inspection
    assert (scratchpad / EXPLORE_DOC_RELPATH).is_file()


def test_marker_needs_context(tmp_path):
    scratchpad = tmp_path / "scratch"
    _reg_echo("STATUS: NEEDS_CONTEXT\n")
    eng = WorkflowEngine()
    eng.register("p2", phase_2_explore_workflow())
    result, _ = eng.execute(
        "p2",
        make_ctx(scratchpad, model="sonnet"),
    )
    assert result.status == "error"
    assert result.error_code == "E_EXPLORE_NEEDS_CONTEXT"


def test_marker_missing(tmp_path):
    scratchpad = tmp_path / "scratch"
    _reg_echo("findings without any status\n")
    eng = WorkflowEngine()
    eng.register("p2", phase_2_explore_workflow())
    result, _ = eng.execute(
        "p2",
        make_ctx(scratchpad, model="sonnet"),
    )
    assert result.status == "error"
    assert result.error_code == "E_EXPLORE_NO_MARKER"
    assert result.data["marker"] is None


def test_last_marker_wins_via_rfind(tmp_path):
    """If multiple markers appear, the last one (highest rfind) wins.

    Critical for prompts that quote markers in their own output schema —
    a naive `in` check would mis-classify a DONE response that quotes the
    BLOCKED option earlier in scratch text.
    """
    scratchpad = tmp_path / "scratch"
    body = (
        "I considered STATUS: BLOCKED but ruled it out.\n"
        "Findings below.\n"
        "STATUS: DONE\n"
    )
    _reg_echo(body)
    eng = WorkflowEngine()
    eng.register("p2", phase_2_explore_workflow())
    result, _ = eng.execute("p2", make_ctx(scratchpad, model="sonnet"))
    assert result.status == "ok"
    assert result.data["marker"] == "DONE"


def test_last_marker_blocked_wins_over_earlier_done(tmp_path):
    scratchpad = tmp_path / "scratch"
    body = "Initial: STATUS: DONE\nWait — ran into blocker.\nSTATUS: BLOCKED\n"
    _reg_echo(body)
    eng = WorkflowEngine()
    eng.register("p2", phase_2_explore_workflow())
    result, _ = eng.execute("p2", make_ctx(scratchpad, model="sonnet"))
    assert result.error_code == "E_EXPLORE_BLOCKED"


def test_done_with_concerns_preferred_over_done_when_both_present(tmp_path):
    """DONE_WITH_CONCERNS contains DONE as substring — rfind on the longer
    marker must take precedence when DONE_WITH_CONCERNS appears later."""
    scratchpad = tmp_path / "scratch"
    body = "STATUS: DONE\n...later...\nSTATUS: DONE_WITH_CONCERNS\n"
    _reg_echo(body)
    eng = WorkflowEngine()
    eng.register("p2", phase_2_explore_workflow())
    result, _ = eng.execute("p2", make_ctx(scratchpad, model="sonnet"))
    assert result.data["marker"] == "DONE_WITH_CONCERNS"


# ─── per-step model override ──────────────────────────────────────────────────


def test_explore_llm_command_overrides_global(tmp_path):
    """explore_model overrides the global model in _resolve_model.

    Registers a single backend that branches on the model string:
    - model "stub-explore-pinned" → writes marker file, returns ok
    - any other model → returns error (proves global model was NOT used)
    """
    scratchpad = tmp_path / "scratch"
    marker_file = tmp_path / "explore-was-pinned"

    class _BranchingBackend:
        def __call__(self, **kw) -> StepResult:
            if kw.get("model") == "stub-explore-pinned":
                marker_file.write_text("1")
                data = {
                    "raw_response": "STATUS: DONE\n",
                    "worker_written_paths": [],
                    "manifest_source": "harness_tool_record",
                }
                data.update(kw.get("extra_data") or {})
                return StepResult(
                    status="ok",
                    data=data,
                    duration_ms=0,
                    step_name=kw.get("step_name", "stub"),
                    error=None,
                    error_code=None,
                    recoverable=True,
                )
            return StepResult(
                status="error",
                data=None,
                duration_ms=0,
                step_name=kw.get("step_name", "stub"),
                error=f"global model used — should have used explore_model; got {kw.get('model')!r}",
                error_code="E_LLM_EXIT",
                recoverable=True,
            )

    register_backend(
        "claude-subprocess",
        _BranchingBackend(),
        manifest_source="harness_tool_record",
        overwrite=True,
    )

    eng = WorkflowEngine()
    eng.register("p2", phase_2_explore_workflow())
    eng.execute(
        "p2",
        make_ctx(
            scratchpad,
            explore_model="stub-explore-pinned",  # per-step override
            model="stub-fail-global",  # global — must NOT be used
        ),
    )
    assert marker_file.read_text() == "1"


def test_per_step_command_falls_back_to_global(tmp_path):
    scratchpad = tmp_path / "scratch"
    _reg_echo("STATUS: DONE\n")
    eng = WorkflowEngine()
    eng.register("p2", phase_2_explore_workflow())
    result, _ = eng.execute(
        "p2",
        make_ctx(scratchpad, model="sonnet"),
    )
    assert result.status == "ok"


# ─── error paths ──────────────────────────────────────────────────────────────


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
    eng.register("p2", phase_2_explore_workflow())
    try:
        eng.execute("p2", ctx)
    except ValueError as e:
        assert "scratchpad_dir" in str(e)
    else:
        raise AssertionError("expected ValueError")


def test_llm_failure_blocks_doc_write(tmp_path):
    scratchpad = tmp_path / "scratch"
    register_backend(
        "claude-subprocess",
        _FailBackend(7),
        manifest_source="harness_tool_record",
        overwrite=True,
    )
    eng = WorkflowEngine()
    eng.register("p2", phase_2_explore_workflow())
    result, _ = eng.execute("p2", make_ctx(scratchpad, model="sonnet"))

    assert result.status == "error"
    assert result.error_code == "E_LLM_EXIT"
    assert not (scratchpad / EXPLORE_DOC_RELPATH).exists()


# ─── events + registry ────────────────────────────────────────────────────────


def test_events_emitted_three_steps(tmp_path):
    _reg_echo("STATUS: DONE\n")
    log_path = tmp_path / "events.jsonl"
    log = EventLog(log_path)
    eng = WorkflowEngine(event_log=log)
    eng.register("phase_2_explore", phase_2_explore_workflow())

    eng.execute(
        "phase_2_explore",
        make_ctx(tmp_path / "scratch", model="sonnet"),
        run_id="rid-p2",
    )

    events = EventLog(log_path).read_all()
    finished = [e for e in events if e["event_type"] == "step_finished"]
    assert len(finished) == 4
    assert [e["payload"]["status"] for e in finished] == ["ok"] * 4
    assert [e["payload"]["step_name"] for e in finished] == [
        "check_decision_doc_skip",
        "build_explore_prompt",
        "invoke_explore_llm",
        "write_explore_doc",
    ]

    state = replay(events)
    run = state["runs"]["rid-p2"]
    assert run["workflow_name"] == "phase_2_explore"
    assert run["status"] == "ok"
    assert len(run["steps"]) == 4


def test_registry_includes_phase_2_explore():
    eng = WorkflowEngine()
    workflows.register_all(eng)
    assert "phase_2_explore" in eng.registered()


# ─── decision_doc self-skip (05F83B1B surface 6) ─────────────────────────────


def test_phase_2_skips_when_decision_doc_present_and_feature(tmp_path, monkeypatch):
    """RED: decision_doc exists + FEATURE → entire phase skips with skipped=True."""
    monkeypatch.chdir(tmp_path)
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    ddoc = tmp_path / "decision.md"
    ddoc.write_text("# Decision\nsome content\n")

    # fail backend — must NOT be called (skip fires before LLM step)
    register_backend(
        "claude-subprocess",
        _FailBackend(99),
        manifest_source="harness_tool_record",
        overwrite=True,
    )
    eng = WorkflowEngine()
    eng.register("p2", phase_2_explore_workflow())
    result, _ = eng.execute(
        "p2",
        make_ctx(
            scratchpad,
            decision_doc=str(ddoc),
            complexity="FEATURE",
            model="sonnet",
        ),
    )

    assert result.status == "ok"
    assert result.data["skipped"] is True
    assert "decision_doc" in result.data
    assert result.data["decision_doc"] == str(ddoc)
    # No output file written
    assert not (scratchpad / EXPLORE_DOC_RELPATH).exists()


def test_phase_2_runs_normally_when_no_decision_doc(tmp_path):
    """Invariant: no decision_doc → phase runs (current behavior preserved)."""
    scratchpad = tmp_path / "scratch"
    _reg_echo("STATUS: DONE\n")
    eng = WorkflowEngine()
    eng.register("p2", phase_2_explore_workflow())
    result, _ = eng.execute(
        "p2",
        make_ctx(scratchpad, complexity="FEATURE", model="sonnet"),
    )
    assert result.status == "ok"
    assert not result.data.get("skipped")
    assert (scratchpad / EXPLORE_DOC_RELPATH).exists()


def test_phase_2_runs_normally_when_complexity_not_feature(tmp_path):
    """Invariant: decision_doc present but complexity=SIMPLE → phase runs."""
    scratchpad = tmp_path / "scratch"
    ddoc = tmp_path / "decision.md"
    ddoc.write_text("# Decision\n")

    _reg_echo("STATUS: DONE\n")
    eng = WorkflowEngine()
    eng.register("p2", phase_2_explore_workflow())
    result, _ = eng.execute(
        "p2",
        make_ctx(
            scratchpad,
            decision_doc=str(ddoc),
            complexity="COMPLEX",
            model="sonnet",
        ),
    )
    assert result.status == "ok"
    assert not result.data.get("skipped")
    assert (scratchpad / EXPLORE_DOC_RELPATH).exists()


def test_phase_2_runs_normally_when_decision_doc_path_missing(tmp_path):
    """Invariant guard: decision_doc path doesn't exist → phase runs."""
    scratchpad = tmp_path / "scratch"
    _reg_echo("STATUS: DONE\n")
    eng = WorkflowEngine()
    eng.register("p2", phase_2_explore_workflow())
    result, _ = eng.execute(
        "p2",
        make_ctx(
            scratchpad,
            decision_doc="/nonexistent/path.md",
            complexity="FEATURE",
            model="sonnet",
        ),
    )
    assert result.status == "ok"
    assert not result.data.get("skipped")
    assert (scratchpad / EXPLORE_DOC_RELPATH).exists()


# ─── dispatch/coordination mechanisms enrichment (RED for 16FD9C1C) ──────────
# Post-mortem: build forge-1777578916 missed UnifiedHookOrchestrator.HOOK_CONFIGS.
# Prompts must force the LLM to enumerate registries / orchestrators / native
# event types before proposing architecture.


def test_prompt_includes_dispatch_mechanisms_section_simple(tmp_path):
    # Note: phase_2 has no SIMPLE branch (FEATURE/COMPLEX only). Test name
    # kept for parity with phase_1 sibling; uses COMPLEX as the non-default
    # complexity to verify the section appears in both supported branches.
    scratchpad = tmp_path / "scratch"
    _reg_passthrough()
    eng = WorkflowEngine()
    eng.register("p2", phase_2_explore_workflow())
    eng.execute(
        "p2",
        make_ctx(scratchpad, complexity="COMPLEX", model="sonnet"),
    )
    doc = (scratchpad / EXPLORE_DOC_RELPATH).read_text()
    assert "## Existing Dispatch/Coordination Mechanisms" in doc


def test_prompt_includes_dispatch_mechanisms_section_feature(tmp_path):
    scratchpad = tmp_path / "scratch"
    _reg_passthrough()
    eng = WorkflowEngine()
    eng.register("p2", phase_2_explore_workflow())
    eng.execute(
        "p2",
        make_ctx(scratchpad, complexity="FEATURE", model="sonnet"),
    )
    doc = (scratchpad / EXPLORE_DOC_RELPATH).read_text()
    assert "## Existing Dispatch/Coordination Mechanisms" in doc


def test_prompt_dispatch_mechanisms_lists_three_categories(tmp_path):
    scratchpad = tmp_path / "scratch"
    _reg_passthrough()
    eng = WorkflowEngine()
    eng.register("p2", phase_2_explore_workflow())
    eng.execute(
        "p2",
        make_ctx(scratchpad, complexity="FEATURE", model="sonnet"),
    )
    doc = (scratchpad / EXPLORE_DOC_RELPATH).read_text().lower()
    assert "registries" in doc
    assert "orchestrators" in doc
    assert "native event types" in doc


# ─── Cat A: structural integration into REQUIRED-sections enumeration ─────────
# Heading must appear inside the schema's "REQUIRED sections" enumeration
# (not orphan-appended after schema). Asserted by index ordering.

_HEADING = "## Existing Dispatch/Coordination Mechanisms"


def test_prompt_dispatch_mechanisms_listed_in_required_sections_phase2_feature(tmp_path):
    scratchpad = tmp_path / "scratch"
    _reg_passthrough()
    eng = WorkflowEngine()
    eng.register("p2", phase_2_explore_workflow())
    eng.execute(
        "p2",
        make_ctx(scratchpad, complexity="FEATURE", model="sonnet"),
    )
    doc = (scratchpad / EXPLORE_DOC_RELPATH).read_text()
    assert "REQUIRED sections" in doc
    assert _HEADING in doc
    # Upper-bound: terminator that closes the schema enumeration block in
    # phase_2_explore. The literal "End your text response with EXACTLY ONE status line"
    # (see _explore_output_schema in workflows/phase_2_explore.py) reliably
    # appears AFTER the REQUIRED-sections list and the producer anti-fab block,
    # marking the trailing STATUS-line directive — chosen over `## Anti-Fabrication`
    # (which would land mid-schema via the producer fragment) for stable
    # end-of-schema positioning.
    assert "End your text response with EXACTLY ONE status line" in doc
    idx_required = doc.index("REQUIRED sections")
    idx_heading = doc.index(_HEADING)
    idx_terminator = doc.index("End your text response with EXACTLY ONE status line")
    assert idx_required < idx_heading < idx_terminator, (
        "heading must appear INSIDE the REQUIRED sections enumeration "
        "(after 'REQUIRED sections' and before the EXACTLY-ONE-status-line "
        "terminator), not as an orphan stub appended after the schema block"
    )


def test_prompt_dispatch_mechanisms_listed_in_required_sections_phase2_complex(tmp_path):
    scratchpad = tmp_path / "scratch"
    _reg_passthrough()
    eng = WorkflowEngine()
    eng.register("p2", phase_2_explore_workflow())
    eng.execute(
        "p2",
        make_ctx(scratchpad, complexity="COMPLEX", model="sonnet"),
    )
    doc = (scratchpad / EXPLORE_DOC_RELPATH).read_text()
    assert "REQUIRED sections" in doc
    assert _HEADING in doc
    # Same upper-bound terminator as the FEATURE variant — see comment above.
    assert "End your text response with EXACTLY ONE status line" in doc
    idx_required = doc.index("REQUIRED sections")
    idx_heading = doc.index(_HEADING)
    idx_terminator = doc.index("End your text response with EXACTLY ONE status line")
    assert idx_required < idx_heading < idx_terminator, (
        "heading must appear INSIDE the REQUIRED sections enumeration "
        "(after 'REQUIRED sections' and before the EXACTLY-ONE-status-line "
        "terminator), not as an orphan stub appended after the schema block"
    )


# ─── Cat B: grounding requirement near the new heading ────────────────────────
# Within ~500 chars after the heading, the prompt must demand concrete file/symbol
# citations. Accept any of these canonical phrases so GREEN has flexibility.

_GROUNDING_PHRASES = (
    "file:line",
    "file path",
    "name the symbol",
    "specific symbol",
    "with citations",
    "cite",
)


def _has_grounding_within(text: str, heading: str, window: int = 500) -> bool:
    """Section-bounded grounding check.

    Extracts the section text from `heading` to the next `\\n## ` heading (or
    end-of-prompt if none), then asserts at least one canonical grounding phrase
    appears inside. Replaces the older fixed-`window` slice — section bounding
    prevents false-positives from sibling sections (e.g. the next section's
    "file path" / "cite" demand bleeding in via a generous char window).

    `window` kept as a (now-unused) param for signature back-compat with the
    older test helper.
    """
    assert heading in text
    heading_idx = text.index(heading)
    start = heading_idx + len(heading)
    # Find the next ##-prefixed heading after the new heading. Schemas render
    # headings with leading whitespace (two-space indent in current builders);
    # use a regex so the bound works regardless of indent prefix.
    match = re.search(r"\n[ \t]*## ", text[start:])
    end = (start + match.start()) if match else len(text)
    section_text = text[start:end].lower()
    return any(phrase.lower() in section_text for phrase in _GROUNDING_PHRASES)


def test_prompt_dispatch_mechanisms_requires_concrete_citations_phase2_feature(tmp_path):
    scratchpad = tmp_path / "scratch"
    _reg_passthrough()
    eng = WorkflowEngine()
    eng.register("p2", phase_2_explore_workflow())
    eng.execute(
        "p2",
        make_ctx(scratchpad, complexity="FEATURE", model="sonnet"),
    )
    doc = (scratchpad / EXPLORE_DOC_RELPATH).read_text()
    assert _has_grounding_within(doc, _HEADING), (
        f"within 500 chars after {_HEADING!r} the prompt must demand concrete "
        f"citations (one of {_GROUNDING_PHRASES})"
    )


def test_prompt_dispatch_mechanisms_requires_concrete_citations_phase2_complex(tmp_path):
    scratchpad = tmp_path / "scratch"
    _reg_passthrough()
    eng = WorkflowEngine()
    eng.register("p2", phase_2_explore_workflow())
    eng.execute(
        "p2",
        make_ctx(scratchpad, complexity="COMPLEX", model="sonnet"),
    )
    doc = (scratchpad / EXPLORE_DOC_RELPATH).read_text()
    assert _has_grounding_within(doc, _HEADING)


# ─── Cat C: ordering — new heading before "## Out of Scope" ──────────────────
# Dispatch mechanisms shape scope decisions; documenting them after Out-of-Scope
# is too late. Phase 2 has both FEATURE and COMPLEX branches; cover both.


def test_prompt_dispatch_mechanisms_before_out_of_scope_phase2_feature(tmp_path):
    scratchpad = tmp_path / "scratch"
    _reg_passthrough()
    eng = WorkflowEngine()
    eng.register("p2", phase_2_explore_workflow())
    eng.execute(
        "p2",
        make_ctx(scratchpad, complexity="FEATURE", model="sonnet"),
    )
    doc = (scratchpad / EXPLORE_DOC_RELPATH).read_text()
    assert _HEADING in doc
    assert "## Out of Scope" in doc
    assert doc.index(_HEADING) < doc.index("## Out of Scope"), (
        "dispatch mechanisms must precede Out of Scope — they shape scope decisions"
    )


def test_prompt_dispatch_mechanisms_before_out_of_scope_phase2_complex(tmp_path):
    scratchpad = tmp_path / "scratch"
    _reg_passthrough()
    eng = WorkflowEngine()
    eng.register("p2", phase_2_explore_workflow())
    eng.execute(
        "p2",
        make_ctx(scratchpad, complexity="COMPLEX", model="sonnet"),
    )
    doc = (scratchpad / EXPLORE_DOC_RELPATH).read_text()
    assert _HEADING in doc
    assert "## Out of Scope" in doc
    assert doc.index(_HEADING) < doc.index("## Out of Scope")


# ─── D57F354F: phase_status_marker telemetry ─────────────────────────────────


def test_phase_2_emits_status_marker_event_on_done_with_concerns(tmp_path):
    """AC3: phase_2_explore emits phase_status_marker with marker=DONE_WITH_CONCERNS."""
    _reg_echo("STATUS: DONE_WITH_CONCERNS\n")
    log_path = tmp_path / "events.jsonl"
    log = EventLog(log_path)
    eng = WorkflowEngine(event_log=log)
    eng.register("phase_2_explore", phase_2_explore_workflow())
    eng.execute(
        "phase_2_explore",
        make_ctx(tmp_path / "scratch", model="sonnet"),
        run_id="rid-p2-dwc",
    )
    events = EventLog(log_path).read_all()
    marker_events = [e for e in events if e["event_type"] == "phase_status_marker"]
    assert len(marker_events) == 1
    payload = marker_events[0]["payload"]
    assert payload["phase"] == 2
    assert payload["marker"] == "DONE_WITH_CONCERNS"


def test_phase_2_emits_status_marker_event_on_no_marker(tmp_path):
    """AC5: phase_2_explore emits phase_status_marker with marker=NO_MARKER when LLM omits the line."""
    _reg_echo("just some text without the marker line\n")
    log_path = tmp_path / "events.jsonl"
    log = EventLog(log_path)
    eng = WorkflowEngine(event_log=log)
    eng.register("phase_2_explore", phase_2_explore_workflow())
    eng.execute(
        "phase_2_explore",
        make_ctx(tmp_path / "scratch", model="sonnet"),
        run_id="rid-p2-nomarker",
    )
    events = EventLog(log_path).read_all()
    marker_events = [e for e in events if e["event_type"] == "phase_status_marker"]
    assert len(marker_events) == 1
    payload = marker_events[0]["payload"]
    assert payload["phase"] == 2
    assert payload["marker"] == "NO_MARKER"
