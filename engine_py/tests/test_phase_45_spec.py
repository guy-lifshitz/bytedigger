"""Tests for phase_45_spec workflow — Stage 2.4 port (single-pass v0).

Retry loop on REVISE verdict (max 2 cycles per phase-45-spec.md) is
deliberately deferred to the same design pass as fanout. v0 produces ONE
spec + ONE review; caller decides what to do with REVISE.

Both LLM calls (spec + review) are stubbed via register_backend (25e75663
migration: command:list[str] → model:str seam).

Surface-15 tests (05F83B1B — engine-side REVISE retry loop) appended below
the existing suite. Uses _CycleAwareBackend — mirrors cycle_aware_lite_stub
from test_phase_45_spec_lite but detects the FEATURE/COMPLEX reviewer role
string ("spec reviewer (separate agent") and the spec-writer role.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pytest

HERE = Path(__file__).parent

from bytedigger_engine.contracts import WorkflowContext, StepResult  # noqa: E402
from bytedigger_engine.derive_state import replay  # noqa: E402
from bytedigger_engine.engine import WorkflowEngine  # noqa: E402
from bytedigger_engine.event_log import EventLog  # noqa: E402
from bytedigger_engine.workflows.phase_45_spec import (  # noqa: E402
    ARCHITECTURE_DOC_RELPATH,
    DEFAULT_REVIEW_LLM_COMMAND,
    DEFAULT_REVIEW_TIMEOUT_SEC,
    DEFAULT_SPEC_LLM_COMMAND,
    DEFAULT_SPEC_TIMEOUT_SEC,
    MAX_REVIEW_CYCLES,
    REVIEW_DOC_RELPATH,
    SPEC_DOC_RELPATH,
    _FINDINGS_MAX_BYTES,
    _parse_verdict,
    _truncate_findings,
    VERDICT_SHIP,
    VERDICT_REVISE,
    VERDICT_UNKNOWN,
    phase_45_spec_workflow,
)
from bytedigger_engine import workflows  # noqa: E402
from bytedigger_engine import llm_subprocess  # noqa: E402
from bytedigger_engine.llm_subprocess import register_backend, reset_backends  # noqa: E402
from bytedigger_engine import telemetry_ctx  # noqa: E402


# ─── §1i autouse teardown ─────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset_backends_and_telemetry(monkeypatch):
    """§1i: restore _BACKENDS singleton + clear telemetry between tests.

    Also neutralise emit_resolver_resolved to prevent disk writes into
    SHARED/state during invoke_llm_subprocess calls.
    25e75663 migration: tests register backends; autouse resets on teardown.
    """
    monkeypatch.setattr(llm_subprocess, "emit_resolver_resolved", lambda *a, **kw: None)
    # Ensure claude-subprocess is the default backend (no env override leak)
    monkeypatch.setenv("HAL_RUNNER_BACKEND", "claude-subprocess")
    telemetry_ctx.clear_current_run()
    yield
    telemetry_ctx.clear_current_run()
    reset_backends()


# ─── Stub backend base + helpers ──────────────────────────────────────────────

_VALID_SPEC_LINES = (
    "## Context\n"
    "Stub spec used in test infrastructure — satisfies SpecKit structural check.\n"
    "This is not a real feature spec; it exists to allow review-path tests to\n"
    "exercise steps after verify_spec_completeness without triggering E_SPEC_INCOMPLETE.\n"
    "\n"
    "## User Stories\n"
    "### US1 - Stub story (P1 — MVP)\n"
    "  **Why P1**: needed to unblock review step in tests.\n"
    "  **Acceptance**: Given stub spec, When completeness checked, Then passes.\n"
    "\n"
    "## Files\n"
    "MODIFY: SYSTEM/cli/build/engine_py/workflows/phase_45_spec.py\n"
    "\n"
    "## Interfaces\n"
    "stub_interface() -> None\n"
    "\n"
    "## Data Model\n"
    "No new data model.\n"
    "\n"
    "## Behavior\n"
    "- Stub spec body passes line-count and header checks.\n"
    "- Does not represent real feature logic.\n"
    "\n"
    "## Constraints\n"
    "- Must not break existing tests.\n"
    "- Line count must remain >= 80.\n"
    "\n"
    "## Out of Scope\n"
    "- Real feature implementation.\n"
    "- Any behavior beyond passing the completeness gate.\n"
    "\n"
    "## Acceptance Criteria\n"
    "1. Spec has >= 80 lines.\n"
    "   Validation: len(spec.splitlines()) >= 80.\n"
    "2. Spec starts with ## Context.\n"
    "   Validation: spec.startswith('## Context').\n"
    "\n"
    "## Open Questions\n"
    "- (none)\n"
    + "".join(f"# padding line {i}\n" for i in range(50))
)

_REWRITE_SPEC = (
    "## Context\nrewritten by stub on retry.\n\n"
    "## User Stories\n### US1 - Quiet (P1)\n  **Why P1**: mvp.\n  **Acceptance**: Given x, When y, Then z.\n\n"
    "## Files\nMODIFY: scripts/notify.sh (add flag)\n\n"
    "## Interfaces\nnotify(quiet: bool)\n\n"
    "## Data Model\nnone\n\n"
    "## Behavior\n- Quiet suppresses output.\n\n"
    "## Constraints\n- No exit-code change.\n\n"
    "## Out of Scope\n- --verbose.\n\n"
    "## Acceptance Criteria\n1. Quiet path.\n   Validation: empty stdout.\n\n"
    "## Open Questions\nnone\n"
    + ("# padding\n" * 50)
)


def _make_ok_result(step_name: str, raw_response: str, extra: dict | None = None,
                    extra_data: dict | None = None) -> StepResult:
    data: dict[str, Any] = {
        "raw_response": raw_response,
        "worker_written_paths": [],
        "manifest_source": "harness_tool_record",
    }
    if extra:
        data.update(extra)
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


def _make_error_result(step_name: str, error: str, error_code: str,
                       recoverable: bool = False) -> StepResult:
    return StepResult(
        status="error",
        data=None,
        duration_ms=0,
        step_name=step_name,
        error=error,
        error_code=error_code,
        recoverable=recoverable,
    )


def _opus_in(model: str | None) -> bool:
    """True if model string contains 'opus' (case-insensitive)."""
    return bool(model) and "opus" in model.lower()


class _PassthroughBackend:
    """Echo prompt back as raw_response (replaces passthrough_stub)."""
    def __call__(self, **kw) -> StepResult:
        return _make_ok_result(kw.get("step_name", "stub"), kw.get("prompt", ""),
                               extra_data=kw.get("extra_data"))


class _EchoBackend:
    """Always return a fixed payload as raw_response (replaces echo_stub)."""
    def __init__(self, payload: str):
        self.payload = payload

    def __call__(self, **kw) -> StepResult:
        return _make_ok_result(kw.get("step_name", "stub"), self.payload,
                               extra_data=kw.get("extra_data"))


class _FailBackend:
    """Return error E_LLM_EXIT (replaces fail_stub)."""
    def __call__(self, **kw) -> StepResult:
        return _make_error_result(
            kw.get("step_name", "stub"),
            error="boom",
            error_code="E_LLM_EXIT",
            recoverable=False,
        )


class _GateAwareBackend:
    """Wraps an inner backend and applies hard-gate Opus check.

    25e75663 migration: gate tests need the gate to fire for non-opus models.
    The real gate lives in _invoke_subprocess; we replicate the semantics here
    so tests that override claude-subprocess still get gate behaviour.
    """
    def __init__(self, inner: Any):
        self.inner = inner

    def __call__(self, **kw) -> StepResult:
        model = kw.get("model") or ""
        hard_gate = kw.get("hard_gate", False)
        gate_label = kw.get("gate_label") or kw.get("step_name") or "gate"
        step_name = kw.get("step_name", "stub")
        if hard_gate and not _opus_in(model):
            if not model:
                msg = (
                    f"{gate_label} gate requires opus model; missing model — "
                    "refusing (not guaranteed to be opus)"
                )
            else:
                msg = f"{gate_label} gate requires opus model, got {model!r}; refusing to invoke"
            return StepResult(
                status="error",
                data=None,
                duration_ms=0,
                step_name=step_name,
                error=msg,
                error_code="E_HARD_GATE_MODEL_DOWNGRADE",
                recoverable=False,
            )
        return self.inner(**kw)


class _EchoValidSpecBackend:
    """Replaces _echo_valid_spec_stub: valid spec for spec-writer, verdict for reviewer.

    'spec reviewer' in prompt → return verdict string.
    Otherwise → return _VALID_SPEC_LINES.
    """
    def __init__(self, verdict: str = "## Verdict\nSHIP\n"):
        self.verdict = verdict

    def __call__(self, **kw) -> StepResult:
        prompt = kw.get("prompt", "")
        if "spec reviewer" in prompt:
            raw = self.verdict
        else:
            raw = _VALID_SPEC_LINES
        return _make_ok_result(kw.get("step_name", "stub"), raw,
                               extra_data=kw.get("extra_data"))


class _CycleAwareBackend:
    """Replaces cycle_aware_full_stub: steps through verdicts per reviewer call.

    - 'spec reviewer (separate agent' in prompt → use next verdict from list
    - '## REVISION (cycle' in prompt → emit REWRITE_SPEC (for cycle-2 spec writer)
    - default → passthrough (echo prompt back; cycle-1 spec writer)
    """
    def __init__(self, verdicts: list[str], counter_path: Path):
        self.verdicts = verdicts
        self.counter_path = counter_path

    def __call__(self, **kw) -> StepResult:
        prompt = kw.get("prompt", "")
        step_name = kw.get("step_name", "stub")
        extra_data = kw.get("extra_data")
        if "spec reviewer (separate agent" in prompt:
            cycle_idx = 0
            if self.counter_path.exists():
                try:
                    cycle_idx = int(self.counter_path.read_text().strip() or "0")
                except (ValueError, OSError):
                    cycle_idx = 0
            self.counter_path.write_text(str(cycle_idx + 1))
            idx = min(cycle_idx, len(self.verdicts) - 1)
            verdict = self.verdicts[idx]
            raw = (
                "## Verdict\n" + verdict
                + "\n\n## Concerns Checked\n- stubbed\n\n## Findings\nstub finding\n\n## Rationale\nstubbed\n"
            )
            return _make_ok_result(step_name, raw, extra_data=extra_data)
        elif "## REVISION (cycle" in prompt:
            return _make_ok_result(step_name, _REWRITE_SPEC, extra_data=extra_data)
        else:
            return _make_ok_result(step_name, prompt, extra_data=extra_data)


def _register_stub(backend: Any, manifest_source: str = "harness_tool_record") -> None:
    """Register backend as claude-subprocess (overwrite). The default backend
    for phase_45_spec calls (no backend= kwarg, no HAL_RUNNER_BACKEND env)."""
    register_backend(
        "claude-subprocess",
        backend,
        manifest_source=manifest_source,
        capabilities=frozenset({"manifest", "progress_since", "abort"}),
        overwrite=True,
    )


def _passthrough_spec_stub() -> Any:
    """Gate-aware backend: spec step returns prompt (passthrough), review step SHIPs.

    Used by prompt-inspection tests that need the spec-writer prompt echoed into
    the spec doc but must not retry (which would overwrite SPEC_DOC_RELPATH with
    a cycle-2 REVISION prompt and corrupt the assertions).
    The review step is dispatched to an echo-SHIP backend so gate_on_review passes.
    """
    # _StepAwareBackend is defined below; accessed at call-time (after module load).
    class _SpecPassReviewShip:
        def __call__(self, **kw) -> StepResult:
            step_name = kw.get("step_name", "stub")
            extra_data = kw.get("extra_data")
            if step_name == "invoke_spec_llm":
                return _make_ok_result(step_name, kw.get("prompt", ""),
                                       extra_data=extra_data)
            else:
                # review step (invoke_review_llm): echo prompt so review doc
                # contains the review prompt text (assertions check its content);
                # append a top-level ## Verdict\nSHIP\n so gate_on_review parses
                # SHIP and does not trigger a retry cycle.
                review_raw = kw.get("prompt", "") + "\n## Verdict\nSHIP\n"
                return _make_ok_result(step_name, review_raw,
                                       extra_data=extra_data)

    return _GateAwareBackend(_SpecPassReviewShip())


def make_ctx(scratchpad: Path, *, question: str = "Add foo to bar", **org_extra) -> WorkflowContext:
    # spec_lint_skip=True: flow tests exercise verdict/retry paths, not the lint gate.
    # (spec_cite_lint_skip too — same rationale for the citation gate).
    # AC3/AC6 use their own _FakeCtx to exercise the real gate without this opt-out.
    # 25e75663 migration: removed llm_command/spec_llm_command/review_llm_command keys;
    # use model/spec_model/review_model (str) instead.
    # GH541: legacy flow tests exercise cycle/retry mechanics with review re-poll disabled
    org = {"scratchpad_dir": str(scratchpad), "spec_lint_skip": True, "spec_cite_lint_skip": True, "spec_review_repolls": 0, **org_extra}
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


def seed_arch(scratchpad: Path, body: str = "## Approach\nbuild it\n") -> Path:
    arch = scratchpad / ARCHITECTURE_DOC_RELPATH
    arch.parent.mkdir(parents=True, exist_ok=True)
    arch.write_text(body)
    return arch


# ─── shape ────────────────────────────────────────────────────────────────────


def test_workflow_definition_shape():
    wf = phase_45_spec_workflow()
    assert wf.name == "phase_45_spec"
    assert [s.name for s in wf.steps] == [
        "detect_frozen_spec",
        "build_spec_prompt",
        "invoke_spec_llm",
        "write_spec_doc",
        "verify_spec_completeness",
        "verify_spec_cite_prelint",
        "verify_spec_citations",
        "verify_spec_preflight_batch",
        "verify_spec_lint",
        "verify_spec_cite_lint",
        "verify_spec_scope_inverse",
        "verify_spec_reentry",
        "verify_spec_helper_extraction",
        "verify_spec_coverage",
        "verify_spec_lint_batch",
        "verify_spec_ac_dsl",
        "build_review_prompt",
        "invoke_review_llm",
        "write_review_doc",
        "gate_on_review",
    ]


def test_defaults_are_sensible():
    # Spec writer pinned to Opus (literal-mechanical AC framing caused REVISE cycles
    # on 027FB8B9 build; Opus reasoning prevents narrow-test propagation to RED).
    # DEFAULT_*_LLM_COMMAND are back-compat constants that still exist in prod.
    assert DEFAULT_SPEC_LLM_COMMAND == ["claude", "-p", "--model", "opus"]
    assert DEFAULT_REVIEW_LLM_COMMAND == ["claude", "-p", "--model", "opus"]
    assert DEFAULT_SPEC_TIMEOUT_SEC == 600
    assert DEFAULT_REVIEW_TIMEOUT_SEC == 300  # review is read-only, lighter


def test_canonical_doc_paths():
    assert SPEC_DOC_RELPATH == "specs/build-spec.md"
    assert REVIEW_DOC_RELPATH == "specs/build-plan-review.md"


# ─── prompt builders ──────────────────────────────────────────────────────────


def test_spec_prompt_references_arch_doc_by_path_not_inlined(tmp_path):
    """Token-spend guard: spec prompt must point at architecture.md by path."""
    scratchpad = tmp_path / "scratch"
    seed_arch(scratchpad, "ARCH_BODY_DO_NOT_INLINE\n")

    # Passthrough: spec writer echoes prompt → spec doc carries prompt content.
    # Review writer also echoes prompt → review doc carries review prompt.
    _register_stub(_passthrough_spec_stub())

    eng = WorkflowEngine()
    eng.register("p45", phase_45_spec_workflow())
    eng.execute("p45", make_ctx(scratchpad))

    spec = (scratchpad / SPEC_DOC_RELPATH).read_text()
    arch_path = scratchpad / ARCHITECTURE_DOC_RELPATH
    assert str(arch_path) in spec
    # Token-spend guard: arch contents NOT inlined
    assert "ARCH_BODY_DO_NOT_INLINE" not in spec


def test_spec_schema_is_speckit_style_with_anti_fabrication(tmp_path):
    """Phase 4.5 spec writer schema mirrors SpecKit-style spec sections plus an
    explicit ANTI-FABRICATION block. Constraints + Out of Scope + Open Questions
    make anti-fabrication structural; Acceptance Criteria with `Validation:`
    line forces testable invariants."""
    scratchpad = tmp_path / "scratch"
    _register_stub(_passthrough_spec_stub())
    eng = WorkflowEngine()
    eng.register("p45", phase_45_spec_workflow())
    eng.execute("p45", make_ctx(scratchpad))
    spec = (scratchpad / SPEC_DOC_RELPATH).read_text()
    # SpecKit-style required sections
    assert "## Context" in spec
    assert "## User Stories" in spec
    assert "## Files" in spec
    assert "## Interfaces" in spec
    assert "## Data Model" in spec
    assert "## Behavior" in spec
    assert "## Constraints" in spec
    assert "## Out of Scope" in spec
    assert "## Acceptance Criteria" in spec
    assert "## Open Questions" in spec
    # Each Acceptance Criterion must carry a Validation: line
    assert "Validation:" in spec
    # Q4 refactor: universal ANTI-FAB lives in injection/quality-gate.md;
    # FEATURE/COMPLEX spec keeps a short surface-specific addendum naming
    # the authoritative sources (FEATURE REQUEST + ARCHITECTURE DECISION).
    assert "ANTI-FABRICATION" in spec
    assert "quality-gate.md" in spec
    assert "ARCHITECTURE DECISION" in spec  # surface-specific source-of-truth
    assert "Write" in spec and "## Context" in spec  # write-to-file output discipline (FD2592D9)
    assert "STATUS:" in spec  # forbidden marker example


def test_review_schema_calls_out_fabrication_and_validation_gaps(tmp_path):
    """Reviewer schema explicitly tells Opus to scrutinize: (1) features in spec
    not in request (fabrication), (2) criteria without Validation lines
    (untestable), (3) missing Out of Scope (no anti-scope discipline)."""
    scratchpad = tmp_path / "scratch"
    _register_stub(_passthrough_spec_stub())
    eng = WorkflowEngine()
    eng.register("p45", phase_45_spec_workflow())
    eng.execute("p45", make_ctx(scratchpad))
    review = (scratchpad / REVIEW_DOC_RELPATH).read_text()
    assert "fabricated features" in review.lower() or "fabrication" in review.lower()
    assert "Validation:" in review
    assert "Out of Scope" in review
    assert "Do NOT approve by default" in review


def test_spec_prompt_handles_missing_arch_gracefully(tmp_path):
    scratchpad = tmp_path / "scratch"
    _register_stub(_passthrough_spec_stub())
    eng = WorkflowEngine()
    eng.register("p45", phase_45_spec_workflow())
    eng.execute("p45", make_ctx(scratchpad))

    spec = (scratchpad / SPEC_DOC_RELPATH).read_text()
    assert "ARCHITECTURE DECISION: (none" in spec


def test_review_prompt_references_just_written_spec_by_path(tmp_path):
    """Reviewer must point at build-spec.md, not receive its contents inline."""
    scratchpad = tmp_path / "scratch"
    _register_stub(_passthrough_spec_stub())
    eng = WorkflowEngine()
    eng.register("p45", phase_45_spec_workflow())
    eng.execute("p45", make_ctx(scratchpad))

    review = (scratchpad / REVIEW_DOC_RELPATH).read_text()
    spec_path = scratchpad / SPEC_DOC_RELPATH
    assert str(spec_path) in review
    assert "spec reviewer" in review.lower()


def test_review_prompt_includes_anti_hallucination_fragment(tmp_path):
    """Agreement 3B0E1323 Step 4: phase_45_spec reviewer is the first non-HARD-GATE
    rollout — flagged after 3E8E3A2A REVISE-finding fabrication. Inject the plugin
    fragment so spec-review findings carry verbatim evidence quotes.
    """
    scratchpad = tmp_path / "scratch"
    _register_stub(_passthrough_spec_stub())
    eng = WorkflowEngine()
    eng.register("p45", phase_45_spec_workflow())
    eng.execute("p45", make_ctx(scratchpad))

    review = (scratchpad / REVIEW_DOC_RELPATH).read_text()
    assert "ANTI-FABRICATION" in review
    assert "EVIDENCE QUOTE" in review
    assert "build 3E8E3A2A" in review


def test_review_prompt_lists_research_files_when_present(tmp_path):
    scratchpad = tmp_path / "scratch"
    research = scratchpad / "research"
    research.mkdir(parents=True, exist_ok=True)
    (research / "explore.md").write_text("EXPLORE_BODY")
    (research / "clarify.md").write_text("CLARIFY_BODY")

    _register_stub(_passthrough_spec_stub())
    eng = WorkflowEngine()
    eng.register("p45", phase_45_spec_workflow())
    eng.execute("p45", make_ctx(scratchpad))

    review = (scratchpad / REVIEW_DOC_RELPATH).read_text()
    assert str(research / "clarify.md") in review
    assert str(research / "explore.md") in review
    # Token-spend guard
    assert "EXPLORE_BODY" not in review
    assert "CLARIFY_BODY" not in review


def test_both_prompts_list_read_first_paths(tmp_path):
    scratchpad = tmp_path / "scratch"
    _register_stub(_passthrough_spec_stub())
    eng = WorkflowEngine()
    eng.register("p45", phase_45_spec_workflow())
    eng.execute("p45", make_ctx(scratchpad))

    spec = (scratchpad / SPEC_DOC_RELPATH).read_text()
    review = (scratchpad / REVIEW_DOC_RELPATH).read_text()
    inj = scratchpad / "injection"
    for doc in (spec, review):
        for name in ("hal-memory", "constitution", "quality-gate", "producer-rules", "active-work"):
            assert f"{inj}/{name}.md" in doc
    # Don't inline injection contents
    assert "## Org Memory" not in spec
    assert "## Org Memory" not in review


def test_role_template_prepended_to_both_prompts(tmp_path):
    scratchpad = tmp_path / "scratch"
    role = tmp_path / "role.md"
    role.write_text("# Read-only role\n- no destructive ops\n")

    _register_stub(_passthrough_spec_stub())
    eng = WorkflowEngine()
    eng.register("p45", phase_45_spec_workflow())
    eng.execute(
        "p45",
        make_ctx(scratchpad, role_template_path=str(role)),
    )

    spec = (scratchpad / SPEC_DOC_RELPATH).read_text()
    review = (scratchpad / REVIEW_DOC_RELPATH).read_text()
    assert "# Read-only role" in spec
    assert "# Read-only role" in review


# ─── verdict parsing ──────────────────────────────────────────────────────────


def test_verdict_ship_detected(tmp_path):
    scratchpad = tmp_path / "scratch"

    _register_stub(_GateAwareBackend(_EchoValidSpecBackend("## Verdict\nSHIP\nAll good.\n")))
    eng = WorkflowEngine()
    eng.register("p45", phase_45_spec_workflow())
    # HIGH #1: canonical ## Verdict header required for regex.
    result, _ = eng.execute("p45", make_ctx(scratchpad))
    assert result.status == "ok"
    assert result.data["verdict"] == "SHIP"


def test_verdict_revise_detected(tmp_path):
    scratchpad = tmp_path / "scratch"
    _register_stub(_GateAwareBackend(_CycleAwareBackend(["REVISE", "SHIP"], tmp_path / "ctr")))
    eng = WorkflowEngine()
    eng.register("p45", phase_45_spec_workflow())
    # HIGH #1: canonical ## Verdict header; cycle-1 REVISE triggers retry; cycle-2 SHIPs.
    result, _ = eng.execute("p45", make_ctx(scratchpad))
    assert result.status == "ok"
    assert result.data["verdict"] == "SHIP"


def test_verdict_unknown_when_neither_keyword_present(tmp_path):
    scratchpad = tmp_path / "scratch"
    _register_stub(_GateAwareBackend(_EchoValidSpecBackend("Some response without keyword.\n")))
    eng = WorkflowEngine()
    eng.register("p45", phase_45_spec_workflow())
    result, _ = eng.execute("p45", make_ctx(scratchpad))
    assert result.data["verdict"] == "UNKNOWN"


def test_verdict_revise_wins_when_both_words_present(tmp_path):
    """Stricter signal: ## Verdict REVISE overrides any SHIP mention elsewhere.

    HIGH #1: anchored regex means only the token under ## Verdict header counts.
    """
    scratchpad = tmp_path / "scratch"
    _register_stub(_GateAwareBackend(_CycleAwareBackend(["REVISE", "SHIP"], tmp_path / "ctr2")))
    eng = WorkflowEngine()
    eng.register("p45", phase_45_spec_workflow())
    # cycle-1 returns ## Verdict\nREVISE → triggers retry; cycle-2 SHIPs.
    result, _ = eng.execute("p45", make_ctx(scratchpad))
    assert result.status == "ok"
    assert result.data["verdict"] == "SHIP"


# ─── per-step command overrides (improvement A — retrofit) ──────────────────


def test_review_llm_command_pin_isolates_reviewer(tmp_path):
    """review_model can pin Opus while spec writer uses cheaper model.

    Lock-in for "separate reviewer agent" — harness cannot silently downgrade
    the Plan-Review by setting a single global model.
    25e75663 migration: R4 review_llm_command → review_model (str).
    Class-C judgment: the old test used a subprocess side-effect to detect
    which stub ran. The new test uses a spy backend for the review step and
    detects via spy.calls.
    """
    scratchpad = tmp_path / "scratch"

    # Spy tracks whether the review backend was called with the opus model.
    class _ReviewSpy:
        def __init__(self):
            self.calls: list[dict] = []
        def __call__(self, **kw) -> StepResult:
            self.calls.append(dict(kw))
            return _make_ok_result(kw.get("step_name", "stub"), "## Verdict\nSHIP\n",
                                   extra_data=kw.get("extra_data"))

    spy = _ReviewSpy()
    # We wrap with gate-awareness (review hard_gate=True).
    _register_stub(_GateAwareBackend(
        # For spec step (hard_gate=False, model=default opus): passthrough valid spec.
        # For review step (hard_gate=True, model=review_model): spy.
        # A single gate-aware backend that delegates based on step_name:
        _StepAwareBackend({
            "invoke_spec_llm": _EchoValidSpecBackend("## Verdict\nSHIP\n"),
            "invoke_review_llm": spy,
        })
    ))
    eng = WorkflowEngine()
    eng.register("p45", phase_45_spec_workflow())
    eng.execute(
        "p45",
        make_ctx(scratchpad, review_model="claude-opus-4-5"),
    )
    # Spy was called for the review step with opus model.
    assert len(spy.calls) >= 1, "review spy backend must have been called"
    assert spy.calls[0].get("model") == "claude-opus-4-5"


class _StepAwareBackend:
    """Route calls to per-step-name backend instances."""
    def __init__(self, routes: dict[str, Any], default: Any | None = None):
        self.routes = routes
        self.default = default or _PassthroughBackend()

    def __call__(self, **kw) -> StepResult:
        step_name = kw.get("step_name", "")
        handler = self.routes.get(step_name, self.default)
        return handler(**kw)


def test_spec_llm_command_only(tmp_path):
    """spec_model only: spec step uses custom model, review uses global default.
    25e75663 migration: R4 spec_llm_command → spec_model (str).
    Class-C judgment: the old test wrote a file from a subprocess side-effect
    to confirm the spec stub was called. New test: spy backend for spec step.
    """
    scratchpad = tmp_path / "scratch"

    class _SpecSpy:
        def __init__(self):
            self.was_called = False
        def __call__(self, **kw) -> StepResult:
            self.was_called = True
            return _make_ok_result(kw.get("step_name", "stub"), _VALID_SPEC_LINES,
                                   extra_data=kw.get("extra_data"))

    spec_spy = _SpecSpy()
    _register_stub(_GateAwareBackend(
        _StepAwareBackend({
            "invoke_spec_llm": spec_spy,
            "invoke_review_llm": _EchoBackend("## Verdict\nSHIP\n"),
        })
    ))
    eng = WorkflowEngine()
    eng.register("p45", phase_45_spec_workflow())
    eng.execute(
        "p45",
        make_ctx(scratchpad, spec_model="claude-opus-4-5"),
    )
    assert spec_spy.was_called


def test_per_step_command_falls_back_to_global(tmp_path):
    """No overrides → both steps use default model (backward compatible)."""
    scratchpad = tmp_path / "scratch"
    _register_stub(_GateAwareBackend(_EchoValidSpecBackend("## Verdict\nSHIP\n")))
    eng = WorkflowEngine()
    eng.register("p45", phase_45_spec_workflow())
    # HIGH #1: canonical ## Verdict header required for anchored regex.
    result, _ = eng.execute("p45", make_ctx(scratchpad))
    assert result.status == "ok"
    assert (scratchpad / SPEC_DOC_RELPATH).exists()
    assert (scratchpad / REVIEW_DOC_RELPATH).exists()


# ─── error paths ──────────────────────────────────────────────────────────────


def test_missing_scratchpad_dir_raises(tmp_path):
    # 25e75663 migration: llm_command key was ignored anyway (ValueError fires
    # before any LLM call). model key is also unused here (ValueError fires first).
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
    eng.register("p45", phase_45_spec_workflow())
    try:
        eng.execute("p45", ctx)
    except ValueError as e:
        assert "scratchpad_dir" in str(e)
    else:
        raise AssertionError("expected ValueError")


def test_spec_llm_failure_blocks_review(tmp_path):
    """If spec LLM fails, downstream steps must not run."""
    scratchpad = tmp_path / "scratch"
    _register_stub(_FailBackend())
    eng = WorkflowEngine()
    eng.register("p45", phase_45_spec_workflow())
    result, _ = eng.execute("p45", make_ctx(scratchpad))

    assert result.status == "error"
    assert result.error_code == "E_LLM_EXIT"
    # spec doc not written (write_spec_doc never ran)
    assert not (scratchpad / SPEC_DOC_RELPATH).exists()
    # review doc not written either (downstream blocked)
    assert not (scratchpad / REVIEW_DOC_RELPATH).exists()


# ─── verification: events emitted, derived state matches expectation ──────────


def test_events_emitted_six_steps(tmp_path):
    log_path = tmp_path / "events.jsonl"
    log = EventLog(log_path)
    eng = WorkflowEngine(event_log=log)
    eng.register("phase_45_spec", phase_45_spec_workflow())

    _register_stub(_GateAwareBackend(_EchoValidSpecBackend("## Verdict\nSHIP\n")))
    eng.execute(
        "phase_45_spec",
        # HIGH #1: canonical ## Verdict header required for anchored regex.
        make_ctx(tmp_path / "scratch"),
        run_id="rid-p45",
    )

    scratchpad = tmp_path / "scratch"
    assert (scratchpad / SPEC_DOC_RELPATH).is_file()
    assert (scratchpad / REVIEW_DOC_RELPATH).is_file()

    events = EventLog(log_path).read_all()
    finished = [e for e in events if e["event_type"] == "step_finished"]
    # 18 + 1 verify_spec_reentry GH823 + 1 verify_spec_helper_extraction GH863 = 20
    assert len(finished) == 20
    assert [e["payload"]["status"] for e in finished] == ["ok"] * 20
    assert [e["payload"]["step_name"] for e in finished] == [
        "detect_frozen_spec",
        "build_spec_prompt",
        "invoke_spec_llm",
        "write_spec_doc",
        "verify_spec_completeness",
        "verify_spec_cite_prelint",
        "verify_spec_citations",
        "verify_spec_preflight_batch",
        "verify_spec_lint",
        "verify_spec_cite_lint",
        "verify_spec_scope_inverse",
        "verify_spec_reentry",
        "verify_spec_helper_extraction",
        "verify_spec_coverage",
        "verify_spec_lint_batch",
        "verify_spec_ac_dsl",
        "build_review_prompt",
        "invoke_review_llm",
        "write_review_doc",
        "gate_on_review",
    ]

    state = replay(events)
    run = state["runs"]["rid-p45"]
    assert run["workflow_name"] == "phase_45_spec"
    assert run["status"] == "ok"
    # 18 + 1 verify_spec_reentry GH823 + 1 verify_spec_helper_extraction GH863 = 20
    assert len(run["steps"]) == 20


def test_registry_includes_phase_45_spec():
    eng = WorkflowEngine()
    workflows.register_all(eng)
    assert "phase_45_spec" in eng.registered()


# ─── HARD-GATE LLM pinning validation (05F83B1B HIGH #3 — phase_45_spec) ─────


def test_plan_review_gate_refuses_non_opus_downgrade(tmp_path):
    """RED — invoke_review_llm must reject a non-Opus review model.

    Phase-45-spec.md: "invoke_review_llm | **Opus** (HARD GATE — review is the
    whole point; harness must not silently downgrade)". Gate must fire and return
    E_HARD_GATE_MODEL_DOWNGRADE before the review backend runs.
    25e75663 migration: R4 review_llm_command=["python3","-c","..","--model","sonnet"]
    → review_model="sonnet". Gate-aware backend checks hard_gate=True + model.
    """
    scratchpad = tmp_path / "scratch"
    _register_stub(_GateAwareBackend(_EchoValidSpecBackend("## Verdict\nSHIP\n")))
    eng = WorkflowEngine()
    eng.register("p45", phase_45_spec_workflow())
    result, _ = eng.execute(
        "p45",
        make_ctx(scratchpad, review_model="sonnet"),
    )
    assert result.status == "error", (
        f"non-opus review_model must be refused, got {result.status!r}"
    )
    assert result.error_code == "E_HARD_GATE_MODEL_DOWNGRADE", (
        f"expected E_HARD_GATE_MODEL_DOWNGRADE, got {result.error_code!r}"
    )
    assert "plan-review" in (result.error or "").lower() or "review" in (result.error or "").lower(), (
        f"error should mention plan-review gate, got: {result.error!r}"
    )
    assert result.recoverable is False, "hard gate must be non-recoverable"


def test_plan_review_gate_refuses_claude_p_without_model(tmp_path):
    """CRITICAL #1 extension — non-opus review model must be refused for plan-review.

    25e75663 migration: The old test passed review_llm_command=["claude","-p"]
    (no --model) to test the "no model" case. In the new str seam, the equivalent
    is a non-opus model string. We use review_model="haiku" to exercise the same
    gate-fires path.
    """
    scratchpad = tmp_path / "scratch"
    _register_stub(_GateAwareBackend(_EchoValidSpecBackend("## Verdict\nSHIP\n")))
    eng = WorkflowEngine()
    eng.register("p45", phase_45_spec_workflow())
    result, _ = eng.execute(
        "p45",
        make_ctx(scratchpad, review_model="haiku"),
    )
    assert result.status == "error", (
        f"non-opus review_model must be refused, got {result.status!r}"
    )
    assert result.error_code == "E_HARD_GATE_MODEL_DOWNGRADE"
    assert result.recoverable is False


def test_plan_review_gate_accepts_opus_command(tmp_path):
    """Invariant — Opus review model must pass the gate and complete the workflow.
    25e75663 migration: R4 review_llm_command → review_model="claude-opus-4-5".
    """
    scratchpad = tmp_path / "scratch"
    _register_stub(_GateAwareBackend(_EchoValidSpecBackend("## Verdict\nSHIP\n")))
    eng = WorkflowEngine()
    eng.register("p45", phase_45_spec_workflow())
    result, _ = eng.execute(
        "p45",
        make_ctx(scratchpad, review_model="claude-opus-4-5"),
    )
    assert result.error_code != "E_HARD_GATE_MODEL_DOWNGRADE", (
        f"opus review_model must not trigger hard gate, got {result.error_code!r}"
    )


# ─── Surface-15: engine-side REVISE retry loop (05F83B1B) ────────────────────
#
# _CycleAwareBackend mirrors cycle_aware_full_stub but uses Python classes
# instead of shell subprocess.
# Reviewer role tag: "spec reviewer (separate agent"
# Spec-rewrite tag: "## REVISION (cycle"


def seed_spec(scratchpad: Path) -> Path:
    """Write a minimal build-spec.md so cycle-1 spec prompt has something to
    review. Uses _VALID_SPEC_LINES to pass _verify_spec_completeness (8A9C0F24)."""
    spec = scratchpad / SPEC_DOC_RELPATH
    spec.parent.mkdir(parents=True, exist_ok=True)
    spec.write_text(_VALID_SPEC_LINES)
    return spec


# ─── Test 1: REVISE cycle 1 → SHIP cycle 2 (RED) ─────────────────────────────


def test_full_phase45_revise_cycle1_then_ship_cycle2(tmp_path, monkeypatch):
    """RED — engine retries on cycle-1 REVISE, SHIPs on cycle 2.

    Asserts: result.status='ok', verdict='SHIP', cycle=2 in result.data,
    cycle-2 versioned spec + review artifacts exist, iteration events emitted.
    """
    monkeypatch.setenv("HAL_SPEC_DELTA_RETRY", "0")  # GH530: pin legacy lane — delta path covered by test_gh530_delta_revise_populate.py
    scratchpad = tmp_path / "scratch"
    seed_spec(scratchpad)
    log = EventLog(tmp_path / "events.jsonl")
    eng = WorkflowEngine(event_log=log)
    eng.register("p45", phase_45_spec_workflow())

    _register_stub(_GateAwareBackend(
        _CycleAwareBackend(["REVISE", "SHIP"], tmp_path / "counter")
    ))
    result, _ = eng.execute("p45", make_ctx(scratchpad), run_id="r-s15-1")
    assert result.status == "ok", f"expected ok, got {result.status!r}: {result.error!r}"
    assert result.data["verdict"] == "SHIP"
    assert result.data["cycle"] == 2

    # Cycle-2 versioned spec exists and canonical is overwritten.
    assert (scratchpad / "specs/build-spec-cycle-2.md").exists()
    assert (scratchpad / SPEC_DOC_RELPATH).exists()
    canonical = (scratchpad / SPEC_DOC_RELPATH).read_text()
    assert "rewritten by stub on retry" in canonical

    # Cycle-2 versioned review doc exists.
    assert (scratchpad / "specs/build-plan-review-cycle-2.md").exists()

    # iteration_started + iteration_finished events emitted.
    events = log.read_all()
    started = [e for e in events if e["event_type"] == "iteration_started"]
    finished = [e for e in events if e["event_type"] == "iteration_finished"]
    assert len(started) == 1
    assert started[0]["payload"]["cycle_number"] == 2
    assert len(finished) == 1
    assert finished[0]["payload"]["cycle_number"] == 2


# ─── Test 2: REVISE both cycles → terminal abort (RED) ───────────────────────


def test_full_phase45_revise_both_cycles_fails_terminally(tmp_path):
    """RED — both cycles REVISE → E_REVIEW_FAILED, recoverable=False."""
    scratchpad = tmp_path / "scratch"
    seed_spec(scratchpad)
    _register_stub(_GateAwareBackend(
        _CycleAwareBackend(["REVISE", "REVISE"], tmp_path / "counter")
    ))
    eng = WorkflowEngine()
    eng.register("p45", phase_45_spec_workflow())

    result, _ = eng.execute("p45", make_ctx(scratchpad), run_id="r-s15-2")
    assert result.status == "error", f"expected error, got {result.status!r}"
    assert result.error_code == "E_REVIEW_FAILED", f"got {result.error_code!r}"
    assert result.recoverable is False
    assert result.data["cycle_count"] == MAX_REVIEW_CYCLES


# ─── Test 3: SHIP cycle 1 — no retry (Invariant) ─────────────────────────────


def test_full_phase45_ship_cycle1_no_retry(tmp_path):
    """Invariant — SHIP on cycle 1: no cycle-2 artifacts, single iteration."""
    scratchpad = tmp_path / "scratch"
    seed_spec(scratchpad)
    log = EventLog(tmp_path / "events.jsonl")
    eng = WorkflowEngine(event_log=log)
    eng.register("p45", phase_45_spec_workflow())

    _register_stub(_GateAwareBackend(
        _CycleAwareBackend(["SHIP"], tmp_path / "counter")
    ))
    result, _ = eng.execute("p45", make_ctx(scratchpad), run_id="r-s15-3")
    assert result.status == "ok", f"expected ok, got {result.status!r}: {result.error!r}"
    assert result.data["verdict"] == "SHIP"

    # No cycle-2 artifacts.
    assert not (scratchpad / "specs/build-spec-cycle-2.md").exists()
    assert not (scratchpad / "specs/build-plan-review-cycle-2.md").exists()

    # No iteration events on single-cycle SHIP.
    events = log.read_all()
    assert [e for e in events if e["event_type"].startswith("iteration_")] == []


# ─── Test 4: UNKNOWN verdict treated as REVISE (Invariant) ───────────────────


def test_full_phase45_unknown_verdict_treated_as_revise(tmp_path):
    """Invariant — UNKNOWN on cycle 1 triggers retry (fail-closed); SHIP on cycle 2
    results in ok."""
    scratchpad = tmp_path / "scratch"
    seed_spec(scratchpad)
    _register_stub(_GateAwareBackend(
        _CycleAwareBackend(["UNDECIDED", "SHIP"], tmp_path / "counter")
    ))
    eng = WorkflowEngine()
    eng.register("p45", phase_45_spec_workflow())

    result, _ = eng.execute("p45", make_ctx(scratchpad), run_id="r-s15-4")
    # Retry must have fired (cycle 2 = SHIP → ok).
    assert result.status == "ok", f"expected ok after retry, got {result.status!r}: {result.error!r}"
    assert result.data["verdict"] == "SHIP"
    assert result.data["cycle"] == 2


# ─── Test 5: cycle-2 prompt contains REVISION block (Invariant boundary) ─────


def test_full_phase45_cycle2_prompt_contains_revision_block(tmp_path, monkeypatch):
    """Invariant boundary — cycle-2 spec-writer prompt carries '## REVISION (cycle 2'
    block with reviewer findings.

    Verified via iteration_started event's findings_summary (canonical signal),
    which must contain the REVISE verdict from cycle-1 reviewer output.
    """
    monkeypatch.setenv("HAL_SPEC_DELTA_RETRY", "0")  # GH530: pin legacy lane — delta path covered by test_gh530_delta_revise_populate.py
    scratchpad = tmp_path / "scratch"
    seed_spec(scratchpad)
    log = EventLog(tmp_path / "events.jsonl")
    eng = WorkflowEngine(event_log=log)
    eng.register("p45", phase_45_spec_workflow())

    _register_stub(_GateAwareBackend(
        _CycleAwareBackend(["REVISE", "SHIP"], tmp_path / "counter")
    ))
    eng.execute("p45", make_ctx(scratchpad), run_id="r-s15-5")
    events = log.read_all()
    started = [e for e in events if e["event_type"] == "iteration_started"]
    assert len(started) == 1
    summary = started[0]["payload"]["findings_summary"]
    assert "REVISE" in summary
    assert "## Findings" in summary

    # Also verify cycle-2 spec body signals the stub's REVISION branch was hit
    # (stub writes REWRITE_SPEC when prompt contains '## REVISION (cycle').
    cycle2_spec = scratchpad / "specs/build-spec-cycle-2.md"
    assert cycle2_spec.exists()
    assert "rewritten by stub on retry" in cycle2_spec.read_text()


# ─── Test 6: cap does not exceed MAX_REVIEW_CYCLES (Invariant) ───────────────


def test_full_phase45_cap_does_not_exceed_max_cycles(tmp_path):
    """Invariant — engine stops at MAX_REVIEW_CYCLES even if verdicts list has more.

    Verdicts list has 3 entries; engine must stop at cycle 2 (E_REVIEW_FAILED)
    and never invoke the reviewer a third time.
    """
    scratchpad = tmp_path / "scratch"
    seed_spec(scratchpad)
    counter_path = tmp_path / "counter"
    _register_stub(_GateAwareBackend(
        _CycleAwareBackend(["REVISE", "REVISE", "SHIP"], counter_path)
    ))
    eng = WorkflowEngine()
    eng.register("p45", phase_45_spec_workflow())

    result, _ = eng.execute("p45", make_ctx(scratchpad), run_id="r-s15-6")
    assert result.status == "error"
    assert result.error_code == "E_REVIEW_FAILED"
    assert result.recoverable is False

    # Counter file records how many times the reviewer stub was called.
    # Must be exactly MAX_REVIEW_CYCLES (2), never 3.
    calls = int(counter_path.read_text().strip())
    assert calls == MAX_REVIEW_CYCLES, (
        f"reviewer called {calls} times; expected exactly {MAX_REVIEW_CYCLES} (cap)"
    )

    # No third-cycle artifacts.
    assert not (scratchpad / "specs/build-spec-cycle-3.md").exists()
    assert not (scratchpad / "specs/build-plan-review-cycle-3.md").exists()


# ─── HIGH #1: anchored _parse_verdict regex (unit tests) ─────────────────────


def test_parse_verdict_ship_canonical():
    """'## Verdict\nSHIP' → SHIP"""
    assert _parse_verdict("## Verdict\nSHIP") == VERDICT_SHIP


def test_parse_verdict_revise_canonical():
    """'## Verdict\nREVISE\n...' → REVISE"""
    assert _parse_verdict("## Verdict\nREVISE\n\n## Findings\n- missing tests\n") == VERDICT_REVISE


def test_parse_verdict_revise_in_prose_ignored():
    """REVISE in prose before ## Verdict header must NOT match — only the header counts."""
    raw = "Discussion: avoid REVISE pattern in specs.\n## Verdict\nSHIP\n"
    assert _parse_verdict(raw) == VERDICT_SHIP


def test_parse_verdict_revise_in_codeblock_ignored():
    """REVISE inside a code block must NOT match."""
    raw = "```\nREVISE this code\n```\n## Verdict\nSHIP\n"
    assert _parse_verdict(raw) == VERDICT_SHIP


def test_parse_verdict_mixed_case_verdict_header():
    """'## verdict\nship' (all lower) → SHIP (case-insensitive match)."""
    assert _parse_verdict("## verdict\nship\n") == VERDICT_SHIP


def test_parse_verdict_empty_string():
    """Empty string → UNKNOWN."""
    assert _parse_verdict("") == VERDICT_UNKNOWN


def test_parse_verdict_no_verdict_header():
    """No ## Verdict header → UNKNOWN."""
    assert _parse_verdict("Some random text with no header.") == VERDICT_UNKNOWN


# ─── HIGH #2: _truncate_findings unit tests ───────────────────────────────────


def test_truncate_findings_short_text_not_truncated():
    """Short text under cap is returned as-is, was_truncated=False."""
    text = "short finding"
    result, truncated = _truncate_findings(text)
    assert result == text
    assert truncated is False


def test_truncate_findings_long_text_truncated():
    """50KB synthetic review → truncated to ≤4KB + suffix appended, was_truncated=True."""
    big = "x" * 50_000
    result, truncated = _truncate_findings(big)
    assert truncated is True
    assert len(result.encode("utf-8")) > _FINDINGS_MAX_BYTES  # suffix pushes over, but original 4KB cap
    assert "[... findings truncated at" in result
    # Core cap: original bytes up to _FINDINGS_MAX_BYTES were kept
    assert result.startswith("x" * 100)


def test_truncate_findings_exactly_at_cap_not_truncated():
    """Text exactly at cap boundary → not truncated."""
    text = "a" * _FINDINGS_MAX_BYTES
    result, truncated = _truncate_findings(text)
    assert truncated is False
    assert result == text


def test_findings_truncated_event_emitted_on_oversized_review(tmp_path):
    """HIGH #2: 50KB synthetic review triggers findings_truncated event with payload.

    When the reviewer output exceeds _FINDINGS_MAX_BYTES, the engine must emit a
    dedicated 'findings_truncated' event with {phase, original_bytes, truncated_to}.
    """
    scratchpad = tmp_path / "scratch"
    seed_spec(scratchpad)
    log = EventLog(tmp_path / "trunc-events.jsonl")
    eng = WorkflowEngine(event_log=log)
    eng.register("p45", phase_45_spec_workflow())

    # Cycle-1: reviewer returns 50KB REVISE review (triggers truncation + retry).
    # Cycle-2: reviewer returns canonical SHIP.
    big_review = "x" * 50_000 + "\n## Verdict\nREVISE\n"
    responses = [big_review, "## Verdict\nSHIP\n"]
    counter_path = tmp_path / "trunc_ctr"

    class _TruncCycleBackend:
        def __init__(self):
            self.counter_path = counter_path
            self.responses = responses

        def __call__(self, **kw) -> StepResult:
            prompt = kw.get("prompt", "")
            step_name = kw.get("step_name", "stub")
            extra_data = kw.get("extra_data")
            if "spec reviewer (separate agent" in prompt:
                cycle_idx = 0
                if self.counter_path.exists():
                    try:
                        cycle_idx = int(self.counter_path.read_text().strip() or "0")
                    except (ValueError, OSError):
                        cycle_idx = 0
                self.counter_path.write_text(str(cycle_idx + 1))
                idx = min(cycle_idx, len(self.responses) - 1)
                return _make_ok_result(step_name, self.responses[idx], extra_data=extra_data)
            elif "## REVISION (cycle" in prompt:
                # Cycle-2 rewrite must pass _verify_spec_completeness (8A9C0F24).
                body = "## Context\nrewritten spec stub.\n\n" + ("# padding\n" * 80)
                return _make_ok_result(step_name, body, extra_data=extra_data)
            else:
                return _make_ok_result(step_name, prompt, extra_data=extra_data)

    _register_stub(_GateAwareBackend(_TruncCycleBackend()))
    eng.execute("p45", make_ctx(scratchpad), run_id="r-trunc")

    events = log.read_all()
    trunc_events = [e for e in events if e["event_type"] == "findings_truncated"]
    assert len(trunc_events) == 1, f"expected 1 findings_truncated event, got {len(trunc_events)}"
    payload = trunc_events[0]["payload"]
    assert payload["phase"] == "phase_45_spec"
    assert payload["original_bytes"] > 50_000, (
        f"original_bytes should reflect big review size, got {payload['original_bytes']}"
    )
    assert payload["truncated_to"] <= _FINDINGS_MAX_BYTES + 200, (
        f"truncated_to should be near cap, got {payload['truncated_to']}"
    )


# ─── HIGH #3: UNKNOWN + empty review → E_REVIEW_UNPARSEABLE (terminal) ───────


def test_unknown_empty_review_yields_unparseable_error(tmp_path):
    """UNKNOWN verdict + empty review_raw → E_REVIEW_UNPARSEABLE (not retry)."""
    scratchpad = tmp_path / "scratch"
    seed_spec(scratchpad)
    _register_stub(_GateAwareBackend(_EchoValidSpecBackend("")))
    eng = WorkflowEngine()
    eng.register("p45", phase_45_spec_workflow())
    result, _ = eng.execute("p45", make_ctx(scratchpad))
    assert result.error_code == "E_REVIEW_UNPARSEABLE", (
        f"expected E_REVIEW_UNPARSEABLE, got {result.error_code!r}"
    )
    assert result.recoverable is False, "must be terminal — not retryable"


def test_unknown_nonempty_review_triggers_retry(tmp_path):
    """UNKNOWN verdict + non-empty review_raw → retry as REVISE (fail-closed)."""
    scratchpad = tmp_path / "scratch"
    seed_spec(scratchpad)
    # Cycle 1: reviewer outputs text but no ## Verdict header → UNKNOWN.
    # Cycle 2: reviewer outputs ## Verdict\nSHIP.
    counter_path = tmp_path / "counter"
    responses = ["no verdict header here — just some concerns", "## Verdict\nSHIP\n"]

    class _UnknownRetryBackend:
        def __init__(self):
            self.counter_path = counter_path
            self.responses = responses

        def __call__(self, **kw) -> StepResult:
            prompt = kw.get("prompt", "")
            step_name = kw.get("step_name", "stub")
            extra_data = kw.get("extra_data")
            if "spec reviewer (separate agent" in prompt:
                cycle_idx = 0
                if self.counter_path.exists():
                    try:
                        cycle_idx = int(self.counter_path.read_text().strip() or "0")
                    except (ValueError, OSError):
                        cycle_idx = 0
                self.counter_path.write_text(str(cycle_idx + 1))
                idx = min(cycle_idx, len(self.responses) - 1)
                return _make_ok_result(step_name, self.responses[idx], extra_data=extra_data)
            elif "## REVISION (cycle" in prompt:
                body = "## Context\nrewritten spec stub.\n\n" + ("# padding\n" * 80)
                return _make_ok_result(step_name, body, extra_data=extra_data)
            else:
                return _make_ok_result(step_name, prompt, extra_data=extra_data)

    _register_stub(_GateAwareBackend(_UnknownRetryBackend()))
    eng = WorkflowEngine()
    eng.register("p45", phase_45_spec_workflow())
    result, _ = eng.execute("p45", make_ctx(scratchpad))
    # Cycle 2 should SHIP → ok
    assert result.status == "ok", (
        f"UNKNOWN+non-empty retry should eventually SHIP, got {result.status!r}: {result.error!r}"
    )
    assert result.data["verdict"] == "SHIP"


# ─── MED #5: phase_retry_triggered event ─────────────────────────────────────


def test_phase_retry_triggered_event_emitted(tmp_path, monkeypatch):
    """On REVISE cycle 1, engine emits phase_retry_triggered with expected fields."""
    # GH592: pin surgical OFF — asserts exactly 1 gate-retry event (surgical adds a fallback retry hop)
    monkeypatch.setenv("HAL_SURGICAL_REVISE", "0")
    scratchpad = tmp_path / "scratch"
    seed_spec(scratchpad)
    log = EventLog(tmp_path / "events.jsonl")
    eng = WorkflowEngine(event_log=log)
    eng.register("p45", phase_45_spec_workflow())

    _register_stub(_GateAwareBackend(
        _CycleAwareBackend(["REVISE", "SHIP"], tmp_path / "counter")
    ))
    eng.execute("p45", make_ctx(scratchpad), run_id="r-med5")
    events = log.read_all()
    retry_events = [e for e in events if e["event_type"] == "phase_retry_triggered"]
    assert len(retry_events) == 1, f"expected 1 phase_retry_triggered, got {len(retry_events)}"
    payload = retry_events[0]["payload"]
    assert payload["phase_name"] == "phase_45_spec"
    assert payload["cycle"] == 2
    assert payload["error_code"] == "E_VALIDATION_RETRY"
    assert "findings_bytes" in payload


# ─── MED #6: engine cap consistency ──────────────────────────────────────────


def test_engine_cap_consistency():
    """MED #6: engine._MAX_VALIDATION_CYCLES must equal workflow MAX_REVIEW_CYCLES.

    Both constants must stay in sync — drift causes silent retry-cap disagreement.
    """
    from bytedigger_engine.engine import WorkflowEngine as _Engine
    assert _Engine._MAX_VALIDATION_CYCLES == MAX_REVIEW_CYCLES, (
        f"engine._MAX_VALIDATION_CYCLES={_Engine._MAX_VALIDATION_CYCLES} != "
        f"MAX_REVIEW_CYCLES={MAX_REVIEW_CYCLES} — update one to match"
    )


# ─── MED #7: no UNKNOWN→PASS coercion in iteration_finished ──────────────────


def test_iteration_finished_does_not_coerce_unknown_to_pass(tmp_path):
    """MED #7: iteration_finished verdict must be raw UNKNOWN, never silently PASS."""
    scratchpad = tmp_path / "scratch"
    seed_spec(scratchpad)
    # Cycle 1: reviewer returns no header → UNKNOWN. Cycle 2: SHIP.
    counter_path = tmp_path / "counter2"
    responses = ["no verdict header here", "## Verdict\nSHIP\n"]

    class _Med7Backend:
        def __init__(self):
            self.counter_path = counter_path
            self.responses = responses

        def __call__(self, **kw) -> StepResult:
            prompt = kw.get("prompt", "")
            step_name = kw.get("step_name", "stub")
            extra_data = kw.get("extra_data")
            if "spec reviewer (separate agent" in prompt:
                cycle_idx = 0
                if self.counter_path.exists():
                    try:
                        cycle_idx = int(self.counter_path.read_text().strip() or "0")
                    except (ValueError, OSError):
                        cycle_idx = 0
                self.counter_path.write_text(str(cycle_idx + 1))
                idx = min(cycle_idx, len(self.responses) - 1)
                return _make_ok_result(step_name, self.responses[idx], extra_data=extra_data)
            elif "## REVISION (cycle" in prompt:
                body = "## Context\nrewritten spec stub.\n\n" + ("# padding\n" * 80)
                return _make_ok_result(step_name, body, extra_data=extra_data)
            else:
                return _make_ok_result(step_name, prompt, extra_data=extra_data)

    log = EventLog(tmp_path / "events-med7.jsonl")
    eng = WorkflowEngine(event_log=log)
    eng.register("p45", phase_45_spec_workflow())
    _register_stub(_GateAwareBackend(_Med7Backend()))
    eng.execute("p45", make_ctx(scratchpad), run_id="r-med7")

    events = log.read_all()
    finished_events = [e for e in events if e["event_type"] == "iteration_finished"]
    assert len(finished_events) == 1
    verdict_emitted = finished_events[0]["payload"]["verdict"]
    # iteration_finished captures cycle-2 retry result — SHIP (not UNKNOWN, not
    # coerced PASS). Key invariant: engine never maps UNKNOWN→PASS in telemetry.
    assert verdict_emitted != "PASS", (
        f"UNKNOWN must not be coerced to PASS in telemetry, got {verdict_emitted!r}"
    )
    # Cycle-2 reviewer returned "## Verdict\nSHIP" → SHIP propagated raw.
    assert verdict_emitted == "SHIP"


# ─── HIGH #4: atomic write invariant ─────────────────────────────────────────


def test_atomic_write_uses_os_replace(tmp_path):
    """HIGH #4: atomic_write uses os.replace (atomic temp+rename), not write_text direct.
    After 3ECCFF8E, atomic_write lives in io_utils as the shared utility."""
    from bytedigger_engine.io_utils import atomic_write
    target = tmp_path / "test.md"
    content = "atomic content"
    atomic_write(target, content)
    assert target.read_text() == content
    # No stale .tmp file left behind
    assert not (tmp_path / "test.md.tmp").exists()


# A1969546: pre-submission checklist injection


def test_spec_schema_includes_pre_submission_checklist(tmp_path):
    """A1969546: spec-writer prompt embeds reviewer's rubric as a self-check
    block so cycle-1 pass rate goes up. F1 dead-code + F2 path-divergence
    grep markers preserved for fix-history traceability."""
    scratchpad = tmp_path / "scratch"
    _register_stub(_passthrough_spec_stub())
    eng = WorkflowEngine()
    eng.register("p45", phase_45_spec_workflow())
    eng.execute("p45", make_ctx(scratchpad))
    spec = (scratchpad / SPEC_DOC_RELPATH).read_text()
    assert "PRE-SUBMISSION CHECKLIST" in spec
    assert "F2 path-divergence" in spec
    assert "F1 dead-code-paths" in spec


def test_spec_schema_pre_submission_lists_validation_must_exercise_behavior(tmp_path):
    """A1969546: checklist forces Validation: lines to name file/command, and
    explicitly rejects 'Manual review' / 'code inspection' as non-validations."""
    scratchpad = tmp_path / "scratch"
    _register_stub(_passthrough_spec_stub())
    eng = WorkflowEngine()
    eng.register("p45", phase_45_spec_workflow())
    eng.execute("p45", make_ctx(scratchpad))
    spec = (scratchpad / SPEC_DOC_RELPATH).read_text()
    # Substring check tolerant of line wrap in prompt body
    flat = " ".join(spec.split())
    assert "Validation: line that names the file or command" in flat
    assert "Manual review" in spec
    assert "code inspection" in spec


def test_spec_schema_pre_submission_warns_about_cycle_2_retry(tmp_path):
    """A1969546: checklist must surface the cost of failing it (REVISE +
    cycle-2 retry) so spec-writer treats it as gating, not advisory."""
    scratchpad = tmp_path / "scratch"
    _register_stub(_passthrough_spec_stub())
    eng = WorkflowEngine()
    eng.register("p45", phase_45_spec_workflow())
    eng.execute("p45", make_ctx(scratchpad))
    spec = (scratchpad / SPEC_DOC_RELPATH).read_text()
    assert "REVISE" in spec
    assert "cycle-2 retry" in spec


# ─── 03192214 PARTIAL: boundary_error wiring on E_REVIEW_FAILED (RED) ────────
#
# Site: phase_45_spec.py:620 — terminal abort (cycle 2 REVISE cap reached) raises
# E_REVIEW_FAILED with a bare f-string error today. Once GREEN wires
# `format_boundary_error(...)` into that raise site, the result.error string MUST
# carry boundary_error fields so downstream LLM fix-agents have the schema/where
# context (S1+S3 error-as-feed-forward pattern, agreement 03192214).
#
# These tests fail RED today: helper not yet wired into `_gate_on_review`.


def test_full_phase45_revise_cap_error_carries_boundary_error_format(tmp_path):
    """RED — terminal E_REVIEW_FAILED on cycle-2 REVISE cap MUST contain the
    'boundary_error' marker token in result.error (emitted by
    format_boundary_error helper). Helper not wired today."""
    scratchpad = tmp_path / "scratch"
    seed_spec(scratchpad)
    _register_stub(_GateAwareBackend(
        _CycleAwareBackend(["REVISE", "REVISE"], tmp_path / "counter")
    ))
    eng = WorkflowEngine()
    eng.register("p45", phase_45_spec_workflow())

    result, _ = eng.execute("p45", make_ctx(scratchpad), run_id="r-boundary-1")
    assert result.status == "error"
    assert result.error_code == "E_REVIEW_FAILED"
    assert "boundary_error" in (result.error or ""), (
        f"expected 'boundary_error' marker in result.error, got: {result.error!r}"
    )
    # Fix 5: lock human-readable message preservation. GREEN must APPEND
    # boundary_error to existing message, not replace it.
    assert "cap reached" in (result.error or ""), (
        f"expected 'cap reached' (existing human message) preserved alongside "
        f"boundary_error, got: {result.error!r}"
    )


def test_full_phase45_revise_cap_error_carries_boundary_phase_field(tmp_path):
    """RED — boundary_error MUST carry phase=phase_45_spec."""
    scratchpad = tmp_path / "scratch"
    seed_spec(scratchpad)
    _register_stub(_GateAwareBackend(
        _CycleAwareBackend(["REVISE", "REVISE"], tmp_path / "counter")
    ))
    eng = WorkflowEngine()
    eng.register("p45", phase_45_spec_workflow())

    result, _ = eng.execute("p45", make_ctx(scratchpad), run_id="r-boundary-2")
    assert "phase=phase_45_spec" in (result.error or ""), (
        f"expected 'phase=phase_45_spec' in result.error, got: {result.error!r}"
    )


def test_full_phase45_revise_cap_error_carries_boundary_field_review_verdict(tmp_path):
    """RED — boundary_error MUST carry field=review_verdict."""
    scratchpad = tmp_path / "scratch"
    seed_spec(scratchpad)
    _register_stub(_GateAwareBackend(
        _CycleAwareBackend(["REVISE", "REVISE"], tmp_path / "counter")
    ))
    eng = WorkflowEngine()
    eng.register("p45", phase_45_spec_workflow())

    result, _ = eng.execute("p45", make_ctx(scratchpad), run_id="r-boundary-3")
    assert "field=review_verdict" in (result.error or ""), (
        f"expected 'field=review_verdict' in result.error, got: {result.error!r}"
    )


def test_full_phase45_revise_cap_error_carries_boundary_producer(tmp_path):
    """RED — boundary_error MUST carry producer=phase_45_spec.review_cycle."""
    scratchpad = tmp_path / "scratch"
    seed_spec(scratchpad)
    _register_stub(_GateAwareBackend(
        _CycleAwareBackend(["REVISE", "REVISE"], tmp_path / "counter")
    ))
    eng = WorkflowEngine()
    eng.register("p45", phase_45_spec_workflow())

    result, _ = eng.execute("p45", make_ctx(scratchpad), run_id="r-boundary-4")
    assert "producer=phase_45_spec.review_cycle" in (result.error or ""), (
        f"expected 'producer=phase_45_spec.review_cycle' in result.error, got: {result.error!r}"
    )


def test_full_phase45_revise_cap_error_carries_boundary_where_and_schema(tmp_path):
    """RED — boundary_error MUST carry where=phase_45_spec.py (filename only,
    matching codebase precedent at engine.py:142-151) and
    schema=StepResult.data.verdict (the field downstream consumers look at)."""
    scratchpad = tmp_path / "scratch"
    seed_spec(scratchpad)
    _register_stub(_GateAwareBackend(
        _CycleAwareBackend(["REVISE", "REVISE"], tmp_path / "counter")
    ))
    eng = WorkflowEngine()
    eng.register("p45", phase_45_spec_workflow())

    result, _ = eng.execute("p45", make_ctx(scratchpad), run_id="r-boundary-5")
    err = result.error or ""
    # Fix 1: filename-only (no :620). Substring match still passes if GREEN
    # appends a line number later.
    assert "where=phase_45_spec.py" in err, (
        f"expected 'where=phase_45_spec.py' in result.error, got: {err!r}"
    )
    assert "schema=StepResult.data.verdict" in err, (
        f"expected 'schema=StepResult.data.verdict' in result.error, got: {err!r}"
    )


# Fix 4a: negative test — SHIP path must NOT emit boundary_error.
def test_full_phase45_ship_does_not_emit_boundary_error(tmp_path):
    """RED-discipline negative — cycle-1 SHIP (happy path) must NOT emit
    boundary_error in result.error. Without this, GREEN could pass by emitting
    boundary_error on every code path. PASSES today (helper not wired)."""
    scratchpad = tmp_path / "scratch"
    seed_spec(scratchpad)
    _register_stub(_GateAwareBackend(
        _CycleAwareBackend(["SHIP"], tmp_path / "counter")
    ))
    eng = WorkflowEngine()
    eng.register("p45", phase_45_spec_workflow())

    result, _ = eng.execute("p45", make_ctx(scratchpad), run_id="r-boundary-ship-neg")
    assert result.status == "ok", (
        f"expected ok on cycle-1 SHIP, got {result.status!r}: {result.error!r}"
    )
    assert result.data["verdict"] == "SHIP"
    err = result.error or ""
    assert "boundary_error" not in err, (
        f"happy SHIP path must NOT emit boundary_error, got: {err!r}"
    )


# ─── 4196B1A2: complexity-aware min_lines default (AC1-AC4) ──────────────────


def test_verify_spec_completeness_simple_default_zero(tmp_path):
    """AC1 (4196B1A2): SIMPLE complexity → min_lines defaults to 0 (short spec OK).

    FAILS today: _verify_spec_completeness reads MIN_SPEC_LINES (80) as default
    regardless of complexity, so a 2-line spec returns error.  After GREEN ships
    the read-site complexity-aware default, SIMPLE complexity must yield status=ok
    for a short but structurally valid spec.
    """
    from bytedigger_engine.workflows.phase_45_spec import _verify_spec_completeness
    from bytedigger_engine.contracts import StepResult

    spec = tmp_path / "build-spec.md"
    # 2 lines only — has the required header, but far below MIN_SPEC_LINES (80).
    spec.write_text("## Context\nshort spec body\n")

    ctx = make_ctx(tmp_path / "scratch", complexity="SIMPLE")
    prev = StepResult(
        status="ok",
        data={"spec_path": str(spec), "cycle": 1},
        duration_ms=0,
        step_name="prev",
    )
    result = _verify_spec_completeness(ctx, prev)
    assert result.status == "ok", (
        f"AC1 FAIL: SIMPLE complexity must yield ok for short valid spec; "
        f"got status={result.status!r} error_code={result.error_code!r}"
    )


def test_verify_spec_completeness_feature_default_min_spec_lines(tmp_path):
    """AC2 (4196B1A2): FEATURE complexity → min_lines defaults to MIN_SPEC_LINES.

    A short spec (2 lines) with required header should return error for FEATURE.
    PASSES today (current code already returns error for FEATURE — same MIN_SPEC_LINES default).
    This is a regression guard: GREEN must not accidentally break FEATURE behavior.
    """
    from bytedigger_engine.workflows.phase_45_spec import _verify_spec_completeness
    from bytedigger_engine.contracts import StepResult

    spec = tmp_path / "build-spec.md"
    spec.write_text("## Context\nshort spec body\n")

    ctx = make_ctx(tmp_path / "scratch", complexity="FEATURE")
    prev = StepResult(
        status="ok",
        data={"spec_path": str(spec), "cycle": 1},
        duration_ms=0,
        step_name="prev",
    )
    result = _verify_spec_completeness(ctx, prev)
    assert result.status == "error", (
        f"AC2 FAIL: FEATURE complexity must reject short spec; got status={result.status!r}"
    )
    assert result.error_code in ("E_SPEC_INCOMPLETE", "E_SPEC_INCOMPLETE_FATAL"), (
        f"AC2 FAIL: unexpected error_code={result.error_code!r}"
    )


def test_verify_spec_completeness_complex_default_min_spec_lines(tmp_path):
    """AC3 (4196B1A2): COMPLEX complexity → min_lines defaults to MIN_SPEC_LINES.

    Mirror of AC2 with COMPLEX complexity.  Short spec must be rejected.
    PASSES today (same reason as AC2).
    """
    from bytedigger_engine.workflows.phase_45_spec import _verify_spec_completeness
    from bytedigger_engine.contracts import StepResult

    spec = tmp_path / "build-spec.md"
    spec.write_text("## Context\nshort spec body\n")

    ctx = make_ctx(tmp_path / "scratch", complexity="COMPLEX")
    prev = StepResult(
        status="ok",
        data={"spec_path": str(spec), "cycle": 1},
        duration_ms=0,
        step_name="prev",
    )
    result = _verify_spec_completeness(ctx, prev)
    assert result.status == "error", (
        f"AC3 FAIL: COMPLEX complexity must reject short spec; got status={result.status!r}"
    )
    assert result.error_code in ("E_SPEC_INCOMPLETE", "E_SPEC_INCOMPLETE_FATAL"), (
        f"AC3 FAIL: unexpected error_code={result.error_code!r}"
    )


def test_verify_spec_completeness_explicit_override_wins(tmp_path):
    """AC4 (4196B1A2): explicit spec_min_lines override wins over complexity default.

    org_config = {"complexity": "FEATURE", "spec_min_lines": 3} with a 4-line spec
    must yield status=ok (explicit 3 beats FEATURE's MIN_SPEC_LINES default of 80).
    PASSES today (explicit spec_min_lines is already respected).
    Regression guard: GREEN must not break the override path.
    """
    from bytedigger_engine.workflows.phase_45_spec import _verify_spec_completeness
    from bytedigger_engine.contracts import StepResult

    spec = tmp_path / "build-spec.md"
    # 4 lines, has required header, above the explicit override of 3.
    spec.write_text("## Context\nline1\nline2\nline3\n")

    ctx = make_ctx(tmp_path / "scratch", complexity="FEATURE", spec_min_lines=3)
    prev = StepResult(
        status="ok",
        data={"spec_path": str(spec), "cycle": 1},
        duration_ms=0,
        step_name="prev",
    )
    result = _verify_spec_completeness(ctx, prev)
    assert result.status == "ok", (
        f"AC4 FAIL: explicit spec_min_lines=3 must override FEATURE default; "
        f"got status={result.status!r} error_code={result.error_code!r}"
    )
