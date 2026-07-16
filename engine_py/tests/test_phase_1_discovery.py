"""Tests for phase_1_discovery workflow — Stage 2.2 port.

LLM is stubbed via registered backend (25e75663 migration: llm_command→model seam).
Real ``claude -p`` is never invoked.
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
from phase_1_discovery import (  # noqa: E402
    DEFAULT_LLM_COMMAND,
    FEATURE_DOC_RELPATH,
    SIMPLE_DOC_RELPATH,
    phase_1_discovery_workflow,
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

    def __init__(self, payload: str = "MOCK_DISCOVERY_OUTPUT\n## Requirements\nfoo\n"):
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


class _TimeoutBackend:
    """Simulates E_LLM_TIMEOUT."""

    def __call__(self, **kw) -> StepResult:
        return StepResult(
            status="error",
            data=None,
            duration_ms=0,
            step_name=kw.get("step_name", "stub"),
            error="timed out waiting for LLM response",
            error_code="E_LLM_TIMEOUT",
            recoverable=True,
        )


class _CmdMissingBackend:
    """Simulates E_LLM_CMD_MISSING (binary not found)."""

    def __call__(self, **kw) -> StepResult:
        return StepResult(
            status="error",
            data=None,
            duration_ms=0,
            step_name=kw.get("step_name", "stub"),
            error="binary not found: /no/such/binary-zzzqq",
            error_code="E_LLM_CMD_MISSING",
            recoverable=False,
        )


def _reg_echo(payload: str = "MOCK_DISCOVERY_OUTPUT\n## Requirements\nfoo\n") -> None:
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
    wf = phase_1_discovery_workflow()
    assert wf.name == "phase_1_discovery"
    assert [s.name for s in wf.steps] == [
        "check_frozen_spec_skip",
        "build_discovery_prompt",
        "invoke_discovery_llm",
        "write_discovery_doc",
    ]


def test_default_llm_command_is_claude_headless():
    """Sanity check the production default (tests always override).

    Phase 1 discovery uses the discovery model (sonnet; F3A8F4FC §10b —
    supersedes spec_writer pin from sprint 95D3E5F6 Step -1, 2026-05-08).
    """
    assert DEFAULT_LLM_COMMAND == ["claude", "-p", "--model", "sonnet"]


# ─── prompt builder ───────────────────────────────────────────────────────────


def test_prompt_lists_read_first_paths_not_inlined(tmp_path):
    _reg_echo()
    eng = WorkflowEngine()
    eng.register("p1", phase_1_discovery_workflow())
    ctx = make_ctx(tmp_path / "scratch", model="sonnet")
    eng.execute("p1", ctx)

    # Step result for build_discovery_prompt is on the event log; we verify
    # the prompt structure by re-running the builder via the engine and
    # asserting the doc was written + the prompt contained the pointer paths
    # (covered by the next test). Here just sanity-check the doc exists.
    assert (tmp_path / "scratch" / FEATURE_DOC_RELPATH).is_file()


def test_prompt_contents_via_passthrough(tmp_path):
    """Use a passthrough LLM stub to verify exact prompt content."""
    _reg_passthrough()
    eng = WorkflowEngine()
    eng.register("p1", phase_1_discovery_workflow())
    ctx = make_ctx(
        tmp_path / "scratch",
        question="Implement widget X",
        complexity="FEATURE",
        model="sonnet",
    )
    eng.execute("p1", ctx)

    doc = (tmp_path / "scratch" / FEATURE_DOC_RELPATH).read_text()
    inj = tmp_path / "scratch" / "injection"

    assert "READ_FIRST" in doc
    assert f"{inj}/hal-memory.md" in doc
    assert f"{inj}/constitution.md" in doc
    assert f"{inj}/quality-gate.md" in doc
    assert f"{inj}/active-work.md" in doc
    assert "Implement widget X" in doc
    assert "COMPLEXITY: FEATURE" in doc
    # Token-spend guard: do NOT inline injection file contents
    assert "## HAL Memory" not in doc
    assert "## Quality Gate" not in doc


def test_prompt_includes_role_template_when_provided(tmp_path):
    role = tmp_path / "role.md"
    role.write_text("# Read-only role\n- no destructive ops\n")

    _reg_passthrough()
    eng = WorkflowEngine()
    eng.register("p1", phase_1_discovery_workflow())
    ctx = make_ctx(
        tmp_path / "scratch",
        model="sonnet",
        role_template_path=str(role),
    )
    eng.execute("p1", ctx)

    doc = (tmp_path / "scratch" / FEATURE_DOC_RELPATH).read_text()
    assert "# Read-only role" in doc
    assert "no destructive ops" in doc


def test_simple_complexity_writes_build_spec(tmp_path):
    _reg_echo("## Files\nfoo.py\n## Tests\ntest_foo\n")
    eng = WorkflowEngine()
    eng.register("p1", phase_1_discovery_workflow())
    ctx = make_ctx(
        tmp_path / "scratch",
        complexity="SIMPLE",
        model="sonnet",
    )
    eng.execute("p1", ctx)

    spec = tmp_path / "scratch" / SIMPLE_DOC_RELPATH
    assert spec.is_file()
    body = spec.read_text()
    assert "foo.py" in body
    assert not (tmp_path / "scratch" / FEATURE_DOC_RELPATH).exists()


def test_simple_prompt_uses_spec_writer_role_not_discovery(tmp_path):
    """SIMPLE Phase 1 IS the spec writer (no Phase 4.5 review). Role must reflect that."""
    _reg_passthrough()
    eng = WorkflowEngine()
    eng.register("p1", phase_1_discovery_workflow())
    ctx = make_ctx(
        tmp_path / "scratch",
        complexity="SIMPLE",
        model="sonnet",
    )
    eng.execute("p1", ctx)
    spec = (tmp_path / "scratch" / SIMPLE_DOC_RELPATH).read_text()
    assert "spec-spec writer" not in spec  # not the typo
    assert "build-spec writer" in spec
    assert "WRITE the spec file" in spec
    assert "discovery agent" not in spec


def test_simple_prompt_includes_anti_fabrication_rules(tmp_path):
    """SIMPLE prompt must explicitly forbid invented features (--dry-run, etc)."""
    _reg_passthrough()
    eng = WorkflowEngine()
    eng.register("p1", phase_1_discovery_workflow())
    ctx = make_ctx(
        tmp_path / "scratch",
        complexity="SIMPLE",
        model="sonnet",
    )
    eng.execute("p1", ctx)
    spec = (tmp_path / "scratch" / SIMPLE_DOC_RELPATH).read_text()
    # Q4 refactor (commit pending): universal ANTI-FAB lives in
    # injection/quality-gate.md; per-phase block keeps surface-specifics +
    # references the universal doc.
    assert "ANTI-FABRICATION" in spec
    assert "quality-gate.md" in spec  # universal-rules pointer present
    assert "--dry-run" in spec  # explicit example of forbidden invention
    assert "Length budget" in spec
    assert "Start the spec DIRECTLY with `## Context`" in spec
    assert "STATUS:" in spec  # listed as a forbidden marker example


def test_simple_prompt_includes_path_grounding_rule(tmp_path):
    """SIMPLE prompt must instruct LLM to verify file paths/imports/attributes
    by reading the actual target file (BUG-C fix: prevent hallucinated paths
    like 'EventLog (already imported)' or 'SHARED/state/build-events.jsonl')."""
    _reg_passthrough()
    eng = WorkflowEngine()
    eng.register("p1", phase_1_discovery_workflow())
    ctx = make_ctx(
        tmp_path / "scratch",
        complexity="SIMPLE",
        model="sonnet",
    )
    eng.execute("p1", ctx)
    spec = (tmp_path / "scratch" / SIMPLE_DOC_RELPATH).read_text()
    # Soft check that the path-grounding rule is present.
    assert "verify" in spec
    assert "Read" in spec
    assert ("path" in spec) or ("import" in spec)


def test_simple_prompt_uses_speckit_style_sections(tmp_path):
    """SIMPLE schema mirrors SpecKit-style spec structure (Out of Scope, Constraints,
    Acceptance Criteria with Validation, Open Questions) — anti-fabrication via
    structure, not just rules."""
    _reg_passthrough()
    eng = WorkflowEngine()
    eng.register("p1", phase_1_discovery_workflow())
    ctx = make_ctx(
        tmp_path / "scratch",
        complexity="SIMPLE",
        model="sonnet",
    )
    eng.execute("p1", ctx)
    spec = (tmp_path / "scratch" / SIMPLE_DOC_RELPATH).read_text()
    # SpecKit-style sections that make anti-fabrication structural
    assert "## Context" in spec
    assert "## Files" in spec
    assert "## Behavior" in spec
    assert "## Constraints" in spec
    assert "## Out of Scope" in spec
    assert "## Acceptance Criteria" in spec
    assert "## Open Questions" in spec
    # Validation line forces testable criteria
    assert "Validation:" in spec


def test_feature_prompt_keeps_discovery_role(tmp_path):
    """FEATURE/COMPLEX still use 'discovery agent' role — Phase 4.5 writes the actual spec."""
    _reg_passthrough()
    eng = WorkflowEngine()
    eng.register("p1", phase_1_discovery_workflow())
    ctx = make_ctx(
        tmp_path / "scratch",
        complexity="FEATURE",
        model="sonnet",
    )
    eng.execute("p1", ctx)
    doc = (tmp_path / "scratch" / FEATURE_DOC_RELPATH).read_text()
    assert "discovery agent" in doc
    assert "build-spec writer" not in doc


def test_complex_writes_discovery_doc(tmp_path):
    _reg_echo("## Requirements\nbig thing\n")
    eng = WorkflowEngine()
    eng.register("p1", phase_1_discovery_workflow())
    ctx = make_ctx(
        tmp_path / "scratch",
        complexity="COMPLEX",
        model="sonnet",
    )
    eng.execute("p1", ctx)
    assert (tmp_path / "scratch" / FEATURE_DOC_RELPATH).is_file()


def test_feature_discovery_includes_anti_fabrication_and_speckit_sections(tmp_path):
    """FEATURE/COMPLEX discovery doc also gets the SpecKit-style anti-fabrication
    structure: explicit Out of Scope + Open Questions + ANTI-FABRICATION block,
    plus Write-to-path wording (3AAB77FA). Discovery is input to Phase 2/4 —
    fabrication here propagates downstream."""
    _reg_passthrough()
    eng = WorkflowEngine()
    eng.register("p1", phase_1_discovery_workflow())
    ctx = make_ctx(
        tmp_path / "scratch",
        complexity="FEATURE",
        model="sonnet",
    )
    eng.execute("p1", ctx)
    doc = (tmp_path / "scratch" / FEATURE_DOC_RELPATH).read_text()
    assert "Start the discovery doc DIRECTLY with `## Context`" in doc
    assert "## Context" in doc
    assert "## Requirements" in doc
    assert "## In Scope" in doc
    assert "## Out of Scope" in doc
    assert "## Open Questions" in doc
    # Q4 refactor: universal ANTI-FAB in quality-gate.md; FEATURE/COMPLEX
    # discovery has no surface-specific addenda (job is to scope, not invent),
    # so the per-phase block is just the pointer.
    assert "ANTI-FABRICATION" in doc
    assert "quality-gate.md" in doc
    assert "STATUS:" in doc  # forbidden marker example


def test_complexity_default_is_feature(tmp_path):
    _reg_echo()
    eng = WorkflowEngine()
    eng.register("p1", phase_1_discovery_workflow())
    ctx = make_ctx(tmp_path / "scratch", model="sonnet")
    eng.execute("p1", ctx)
    assert (tmp_path / "scratch" / FEATURE_DOC_RELPATH).is_file()


def test_invalid_complexity_raises(tmp_path):
    _reg_echo()
    eng = WorkflowEngine()
    eng.register("p1", phase_1_discovery_workflow())
    ctx = make_ctx(tmp_path / "scratch", complexity="WHATEVER", model="sonnet")
    try:
        eng.execute("p1", ctx)
    except ValueError as e:
        assert "complexity" in str(e)
    else:
        raise AssertionError("expected ValueError for invalid complexity")


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
    eng.register("p1", phase_1_discovery_workflow())
    try:
        eng.execute("p1", ctx)
    except ValueError as e:
        assert "scratchpad_dir" in str(e)
    else:
        raise AssertionError("expected ValueError for missing scratchpad_dir")


# ─── LLM invocation error paths ───────────────────────────────────────────────


def test_llm_nonzero_exit_returns_error_step(tmp_path):
    register_backend(
        "claude-subprocess",
        _FailBackend(7),
        manifest_source="harness_tool_record",
        overwrite=True,
    )
    eng = WorkflowEngine()
    eng.register("p1", phase_1_discovery_workflow())
    ctx = make_ctx(tmp_path / "scratch", model="sonnet")
    result, _ = eng.execute("p1", ctx)
    assert result.status == "error"
    assert result.error_code == "E_LLM_EXIT"
    assert "7" in (result.error or "")
    # No doc on failure
    assert not (tmp_path / "scratch" / FEATURE_DOC_RELPATH).exists()


def test_llm_command_missing_returns_error_step(tmp_path):
    register_backend(
        "claude-subprocess",
        _CmdMissingBackend(),
        manifest_source="harness_tool_record",
        overwrite=True,
    )
    eng = WorkflowEngine()
    eng.register("p1", phase_1_discovery_workflow())
    ctx = make_ctx(
        tmp_path / "scratch",
        model="sonnet",
    )
    result, _ = eng.execute("p1", ctx)
    assert result.status == "error"
    assert result.error_code == "E_LLM_CMD_MISSING"
    assert result.recoverable is False


def test_llm_timeout_returns_error_step(tmp_path):
    register_backend(
        "claude-subprocess",
        _TimeoutBackend(),
        manifest_source="harness_tool_record",
        overwrite=True,
    )
    eng = WorkflowEngine()
    eng.register("p1", phase_1_discovery_workflow())
    ctx = make_ctx(
        tmp_path / "scratch",
        model="sonnet",
    )
    result, _ = eng.execute("p1", ctx)
    assert result.status == "error"
    assert result.error_code == "E_LLM_TIMEOUT"


# ─── verification: events emitted, derived state matches expectation ──────────


def test_events_emitted_and_derived_state_matches_spec(tmp_path):
    """Stage 2.2 acceptance:
    (a) discovery doc emitted,
    (b) three `step_finished` events with status='ok',
    (c) derived state shows workflow_name=phase_1_discovery, status=ok.
    """
    _reg_echo()
    log_path = tmp_path / "events.jsonl"
    log = EventLog(log_path)
    eng = WorkflowEngine(event_log=log)
    eng.register("phase_1_discovery", phase_1_discovery_workflow())

    eng.execute(
        "phase_1_discovery",
        make_ctx(tmp_path / "scratch", model="sonnet"),
        run_id="rid-p1",
    )

    assert (tmp_path / "scratch" / FEATURE_DOC_RELPATH).is_file()

    events = EventLog(log_path).read_all()
    finished_steps = [e for e in events if e["event_type"] == "step_finished"]

    assert len(finished_steps) == 4
    assert [e["payload"]["status"] for e in finished_steps] == ["ok", "ok", "ok", "ok"]
    assert [e["payload"]["step_name"] for e in finished_steps] == [
        "check_frozen_spec_skip",
        "build_discovery_prompt",
        "invoke_discovery_llm",
        "write_discovery_doc",
    ]

    state = replay(events)
    run = state["runs"]["rid-p1"]
    assert run["workflow_name"] == "phase_1_discovery"
    assert run["status"] == "ok"
    assert len(run["steps"]) == 4
    assert all(s["status"] == "ok" for s in run["steps"])


def test_registry_includes_phase_1_discovery():
    eng = WorkflowEngine()
    workflows.register_all(eng)
    assert "phase_1_discovery" in eng.registered()


# ─── dispatch/coordination mechanisms enrichment (RED for 16FD9C1C) ──────────
# Post-mortem: build forge-1777578916 missed UnifiedHookOrchestrator.HOOK_CONFIGS.
# Prompts must force the LLM to enumerate registries / orchestrators / native
# event types before proposing architecture.


def test_prompt_includes_dispatch_mechanisms_section_simple(tmp_path):
    _reg_passthrough()
    eng = WorkflowEngine()
    eng.register("p1", phase_1_discovery_workflow())
    ctx = make_ctx(
        tmp_path / "scratch",
        complexity="SIMPLE",
        model="sonnet",
    )
    eng.execute("p1", ctx)
    spec = (tmp_path / "scratch" / SIMPLE_DOC_RELPATH).read_text()
    assert "## Existing Dispatch/Coordination Mechanisms" in spec


def test_prompt_includes_dispatch_mechanisms_section_feature(tmp_path):
    _reg_passthrough()
    eng = WorkflowEngine()
    eng.register("p1", phase_1_discovery_workflow())
    ctx = make_ctx(
        tmp_path / "scratch",
        complexity="FEATURE",
        model="sonnet",
    )
    eng.execute("p1", ctx)
    doc = (tmp_path / "scratch" / FEATURE_DOC_RELPATH).read_text()
    assert "## Existing Dispatch/Coordination Mechanisms" in doc


def test_prompt_dispatch_mechanisms_lists_three_categories(tmp_path):
    _reg_passthrough()
    eng = WorkflowEngine()
    eng.register("p1", phase_1_discovery_workflow())
    ctx = make_ctx(
        tmp_path / "scratch",
        complexity="FEATURE",
        model="sonnet",
    )
    eng.execute("p1", ctx)
    doc = (tmp_path / "scratch" / FEATURE_DOC_RELPATH).read_text().lower()
    assert "registries" in doc
    assert "orchestrators" in doc
    assert "native event types" in doc


# ─── Cat A: structural integration into REQUIRED-sections enumeration ─────────
# The new heading must live inside the schema's "REQUIRED sections" enumeration
# (not appended as an orphan stub after the schema block). Asserted by index:
# "REQUIRED sections" must appear in the prompt BEFORE the new heading.

_HEADING = "## Existing Dispatch/Coordination Mechanisms"


def test_prompt_dispatch_mechanisms_listed_in_required_sections_phase1_simple(tmp_path):
    _reg_passthrough()
    eng = WorkflowEngine()
    eng.register("p1", phase_1_discovery_workflow())
    ctx = make_ctx(
        tmp_path / "scratch",
        complexity="SIMPLE",
        model="sonnet",
    )
    eng.execute("p1", ctx)
    spec = (tmp_path / "scratch" / SIMPLE_DOC_RELPATH).read_text()
    assert "REQUIRED sections" in spec
    idx_required = spec.index("REQUIRED sections")
    assert _HEADING in spec
    idx_heading = spec.index(_HEADING)
    # Upper-bound: terminator that closes the schema enumeration block in
    # phase_1_discovery — `ANTI-FABRICATION` appears after the REQUIRED-sections
    # list (see _output_schema_block(complexity, doc_path) in workflows/phase_1_discovery.py).
    # Without this bound, GREEN could orphan-append the heading after the schema and
    # still pass the lower-bound-only check.
    assert "ANTI-FABRICATION" in spec
    idx_terminator = spec.index("ANTI-FABRICATION")
    assert idx_required < idx_heading < idx_terminator, (
        "heading must appear INSIDE the REQUIRED sections enumeration "
        "(after 'REQUIRED sections' and before the ANTI-FABRICATION terminator), "
        "not as an orphan stub appended after the schema block"
    )


def test_prompt_dispatch_mechanisms_listed_in_required_sections_phase1_feature(tmp_path):
    _reg_passthrough()
    eng = WorkflowEngine()
    eng.register("p1", phase_1_discovery_workflow())
    ctx = make_ctx(
        tmp_path / "scratch",
        complexity="FEATURE",
        model="sonnet",
    )
    eng.execute("p1", ctx)
    doc = (tmp_path / "scratch" / FEATURE_DOC_RELPATH).read_text()
    assert "REQUIRED sections" in doc
    idx_required = doc.index("REQUIRED sections")
    assert _HEADING in doc
    idx_heading = doc.index(_HEADING)
    # Upper-bound: terminator that closes the schema enumeration block.
    # `ANTI-FABRICATION` appears after the REQUIRED-sections list in
    # phase_1_discovery's FEATURE schema — see workflows/phase_1_discovery.py.
    assert "ANTI-FABRICATION" in doc
    idx_terminator = doc.index("ANTI-FABRICATION")
    assert idx_required < idx_heading < idx_terminator, (
        "heading must appear INSIDE the REQUIRED sections enumeration "
        "(after 'REQUIRED sections' and before the ANTI-FABRICATION terminator), "
        "not as an orphan stub appended after the schema block"
    )


# ─── Cat B: grounding requirement near the new heading ────────────────────────
# Within ~500 chars after the heading, the prompt must demand concrete file/symbol
# citations. Any one of the canonical phrases below is acceptable so GREEN has
# flexibility in how it phrases the demand.

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


def test_prompt_dispatch_mechanisms_requires_concrete_citations_phase1_simple(tmp_path):
    _reg_passthrough()
    eng = WorkflowEngine()
    eng.register("p1", phase_1_discovery_workflow())
    ctx = make_ctx(
        tmp_path / "scratch",
        complexity="SIMPLE",
        model="sonnet",
    )
    eng.execute("p1", ctx)
    spec = (tmp_path / "scratch" / SIMPLE_DOC_RELPATH).read_text()
    assert _has_grounding_within(spec, _HEADING), (
        f"within 500 chars after {_HEADING!r} the prompt must demand concrete "
        f"citations (one of {_GROUNDING_PHRASES})"
    )


def test_prompt_dispatch_mechanisms_requires_concrete_citations_phase1_feature(tmp_path):
    _reg_passthrough()
    eng = WorkflowEngine()
    eng.register("p1", phase_1_discovery_workflow())
    ctx = make_ctx(
        tmp_path / "scratch",
        complexity="FEATURE",
        model="sonnet",
    )
    eng.execute("p1", ctx)
    doc = (tmp_path / "scratch" / FEATURE_DOC_RELPATH).read_text()
    assert _has_grounding_within(doc, _HEADING)


# ─── Cat C: ordering — new heading before "## Out of Scope" ──────────────────
# Dispatch mechanisms shape scope decisions; documenting them after Out-of-Scope
# is too late. SIMPLE has different scope shape (per task spec), so phase_1 Cat C
# only covers FEATURE.


def test_prompt_dispatch_mechanisms_before_out_of_scope_phase1_feature(tmp_path):
    _reg_passthrough()
    eng = WorkflowEngine()
    eng.register("p1", phase_1_discovery_workflow())
    ctx = make_ctx(
        tmp_path / "scratch",
        complexity="FEATURE",
        model="sonnet",
    )
    eng.execute("p1", ctx)
    doc = (tmp_path / "scratch" / FEATURE_DOC_RELPATH).read_text()
    assert _HEADING in doc
    assert "## Out of Scope" in doc
    assert doc.index(_HEADING) < doc.index("## Out of Scope"), (
        "dispatch mechanisms must precede Out of Scope — they shape scope decisions"
    )


# ─── F2D1E592: task_description from org_config must reach prompt body ────────
# Bug 2026-05-01: orchestrator passed `task_description: "S3 of HAL agreement..."`
# in ctx-json; LLM emitted STATUS=BLOCK "(no feature request provided)" because
# _build_prompt at workflows/phase_1_discovery.py:277 reads only ctx.question
# and ignores ctx.org_config["task_description"]. phase_05_inject.py:526 reads
# task_description but uses it only for memory FTS — never propagates to
# downstream discovery prompt. Different class than S1+S3 boundary hardening
# (silent dropping of valid input due to single-source-only prompt assembly).
#
# Tests use the existing passthrough pattern: LLM stub echoes prompt
# body to stdout, which is then written to FEATURE_DOC_RELPATH/SIMPLE_DOC_RELPATH
# — assertions inspect the doc to verify prompt contents.


def test_prompt_includes_task_description_from_org_config_when_question_absent(tmp_path):
    """When ctx.question is empty/absent, org_config['task_description'] must
    populate the FEATURE REQUEST section of the prompt — not silently fall back
    to '(no feature request provided)'."""
    _reg_passthrough()
    eng = WorkflowEngine()
    eng.register("p1", phase_1_discovery_workflow())
    ctx = make_ctx(
        tmp_path / "scratch",
        question="",  # explicitly absent
        complexity="FEATURE",
        model="sonnet",
        task_description="build the X feature",
    )
    eng.execute("p1", ctx)
    doc = (tmp_path / "scratch" / FEATURE_DOC_RELPATH).read_text()
    assert "build the X feature" in doc, (
        "task_description from org_config must reach the prompt body when "
        "ctx.question is absent — currently silently dropped (F2D1E592)"
    )
    assert "(no feature request provided)" not in doc, (
        "fallback string must NOT appear when task_description is available"
    )


def test_prompt_includes_task_description_when_both_question_and_task_description_present(tmp_path):
    """When both ctx.question AND org_config['task_description'] are present,
    BOTH must reach the prompt under DISTINCT labelled headings, with the
    task_description heading appearing AFTER `FEATURE REQUEST:`. GREEN may
    pick the heading name (`TASK CONTEXT:` or `ADDITIONAL CONTEXT:`), but
    it MUST be on its own line and AFTER FEATURE REQUEST. Strengthened
    post-Opus-gate (R3) — guards against raw concat / wrong-order assembly."""
    import re

    _reg_passthrough()
    eng = WorkflowEngine()
    eng.register("p1", phase_1_discovery_workflow())
    ctx = make_ctx(
        tmp_path / "scratch",
        question="rename the foo function",
        complexity="FEATURE",
        model="sonnet",
        task_description="background context: this is part of bigger refactor",
    )
    eng.execute("p1", ctx)
    doc = (tmp_path / "scratch" / FEATURE_DOC_RELPATH).read_text()
    assert "rename the foo function" in doc, (
        "ctx.question must reach the prompt (regression guard)"
    )
    assert "background context: this is part of bigger refactor" in doc, (
        "task_description from org_config must also reach the prompt — "
        "currently silently dropped when ctx.question is set (F2D1E592)"
    )
    # R3: distinct heading on its own line — accept either "TASK CONTEXT:" or
    # "ADDITIONAL CONTEXT:" (GREEN's call). Multiline anchored.
    heading_pat = re.compile(r"^(TASK|ADDITIONAL) CONTEXT:\s*$", re.MULTILINE)
    heading_match = heading_pat.search(doc)
    assert heading_match is not None, (
        "task_description must appear under a distinct labelled heading "
        "(`TASK CONTEXT:` or `ADDITIONAL CONTEXT:`) on its own line — "
        "raw concatenation into FEATURE REQUEST is not acceptable (F2D1E592 R3)"
    )
    # R3: heading INDEX must be AFTER `FEATURE REQUEST:` index (ordering).
    assert "FEATURE REQUEST:" in doc, (
        "FEATURE REQUEST: heading must be present (regression guard)"
    )
    assert doc.index("FEATURE REQUEST:") < heading_match.start(), (
        "task_description heading must appear AFTER FEATURE REQUEST: heading — "
        "the request is primary, the context is supplementary (F2D1E592 R3)"
    )


# ─── R1/R2: empty / whitespace-only task_description must NOT emit section ────
# Post-Opus-gate revisions. Guards GREEN against emitting empty or
# whitespace-only section headings (clean output, no trailing blank sections).


def test_prompt_does_not_emit_extra_section_when_task_description_empty_string(tmp_path):
    """When ctx.question is set and org_config['task_description'] is the empty
    string, the prompt MUST contain ctx.question content but MUST NOT emit a
    `TASK CONTEXT:` or `ADDITIONAL CONTEXT:` heading. Empty-string td is treated
    as absent — no ugly empty section. (F2D1E592 R1)"""
    import re

    _reg_passthrough()
    eng = WorkflowEngine()
    eng.register("p1", phase_1_discovery_workflow())
    ctx = make_ctx(
        tmp_path / "scratch",
        question="rename foo function",
        complexity="FEATURE",
        model="sonnet",
        task_description="",
    )
    eng.execute("p1", ctx)
    doc = (tmp_path / "scratch" / FEATURE_DOC_RELPATH).read_text()
    assert "rename foo function" in doc, (
        "ctx.question content must reach the prompt (regression guard)"
    )
    heading_pat = re.compile(r"^(TASK|ADDITIONAL) CONTEXT:\s*$", re.MULTILINE)
    assert heading_pat.search(doc) is None, (
        "empty-string task_description MUST NOT produce a `TASK CONTEXT:` / "
        "`ADDITIONAL CONTEXT:` heading — clean output, no empty sections "
        "(F2D1E592 R1)"
    )


def test_prompt_does_not_emit_extra_section_when_task_description_whitespace_only(tmp_path):
    """Whitespace-only task_description (spaces, tabs, newlines) MUST be treated
    as absent — no `TASK CONTEXT:` / `ADDITIONAL CONTEXT:` heading in the
    prompt. Guards GREEN against `if td:` truthy-check that admits whitespace.
    (F2D1E592 R2)"""
    import re

    _reg_passthrough()
    eng = WorkflowEngine()
    eng.register("p1", phase_1_discovery_workflow())
    ctx = make_ctx(
        tmp_path / "scratch",
        question="rename foo function",
        complexity="FEATURE",
        model="sonnet",
        task_description="   \n  ",
    )
    eng.execute("p1", ctx)
    doc = (tmp_path / "scratch" / FEATURE_DOC_RELPATH).read_text()
    assert "rename foo function" in doc, (
        "ctx.question content must reach the prompt (regression guard)"
    )
    heading_pat = re.compile(r"^(TASK|ADDITIONAL) CONTEXT:\s*$", re.MULTILINE)
    assert heading_pat.search(doc) is None, (
        "whitespace-only task_description MUST NOT produce a `TASK CONTEXT:` / "
        "`ADDITIONAL CONTEXT:` heading — treat as absent, clean output "
        "(F2D1E592 R2)"
    )


def test_prompt_falls_back_to_no_request_when_neither_present(tmp_path):
    """Pin existing behavior: when neither ctx.question nor org_config
    ['task_description'] is present, prompt contains the '(no feature request
    provided)' fallback string. This test PASSES today and must keep passing
    after GREEN — pre-condition for not breaking the no-input case."""
    _reg_passthrough()
    eng = WorkflowEngine()
    eng.register("p1", phase_1_discovery_workflow())
    ctx = make_ctx(
        tmp_path / "scratch",
        question="",  # explicitly absent
        complexity="FEATURE",
        model="sonnet",
        # no task_description in org_config
    )
    eng.execute("p1", ctx)
    doc = (tmp_path / "scratch" / FEATURE_DOC_RELPATH).read_text()
    assert "(no feature request provided)" in doc, (
        "fallback string must appear when neither ctx.question nor "
        "org_config['task_description'] is available"
    )
