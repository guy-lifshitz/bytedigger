"""RED-phase tests for HAL agreement 04E3B874 S1 — engine hard-fail on missing required ctx fields.

These tests MUST fail on current main and pass after GREEN implements:
1. `StepContract.required_ctx_fields: list[str] = field(default_factory=list)` in contracts.py
2. Engine pre-execute validation around engine.py:130 — for each key in
   step.required_ctx_fields, check `getattr(context, key, None)` is truthy. If
   missing, return StepResult(status="error", error_code="E_REQUIRED_CTX_MISSING")
   and HALT before executing the step (and before subsequent steps).
3. run.py parse_ctx (line 57): stop defaulting session_id to "unknown"; leave None.
4. phase_05_inject's `read_orchestrator_checklist` step gets
   required_ctx_fields=["org_config"].
5. phase_8_post_deploy's `deregister_session` step gets
   required_ctx_fields=["session_id"].

Pattern observed (5 instances in 2 builds): phases silently no-op or default-skip
when required ctx fields are missing/None. Anchors:
- phase_05_inject.py:348 — `cfg = ctx.org_config or {}` → silent no-op
- phase_8_post_deploy.py:604 — `if session_id == "unknown": skipped=True, status="ok"`
- run.py:57 — `data.get("session_id", "unknown")` is the source of bug above
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# Match existing test convention (test_engine.py:6) — sys.path bootstrap
HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "workflows"))

import pytest

from contracts import (
    StepContract,
    StepResult,
    WorkflowContext,
    WorkflowDefinition,
)
from engine import WorkflowEngine


# ─── helpers ─────────────────────────────────────────────────────────────────


def _ctx(**overrides) -> WorkflowContext:
    """Build a minimal WorkflowContext, with field overrides for the test."""
    base = dict(
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
    base.update(overrides)
    return WorkflowContext(**base)


class _RecordingStep:
    """Step whose execute() raises if called — used to spy that the engine
    SKIPPED execution because required_ctx validation failed."""

    def __init__(self, name: str = "spy"):
        self.name = name
        self.called = False

    def execute(self, _ctx, _prev) -> StepResult:
        self.called = True
        # If the engine ever calls us when ctx is missing required fields,
        # we want a noisy failure, not a silent ok.
        raise AssertionError(
            f"step {self.name!r} should NOT have been executed — "
            f"engine must hard-fail BEFORE step.execute() when "
            f"required_ctx_fields are missing"
        )


def _spy_contract(spy: _RecordingStep, *, required_ctx_fields=None) -> StepContract:
    """Build a StepContract that holds the spy's execute and the required_ctx_fields.

    Constructed via kwargs so the test fails LOUDLY on current main with
    TypeError: __init__() got an unexpected keyword argument 'required_ctx_fields'
    — exactly the failure GREEN must fix.
    """
    return StepContract(
        name=spy.name,
        execute=spy.execute,
        required_ctx_fields=required_ctx_fields or [],
    )


# ─── (a) hard-fail when ctx field is None ────────────────────────────────────


def test_engine_hardfails_when_required_ctx_missing():
    """Missing org_config (None) must cause E_REQUIRED_CTX_MISSING and
    HALT the workflow — step.execute() must NOT run, and no later step
    must run either."""
    eng = WorkflowEngine()
    spy = _RecordingStep("inject_step")
    later = _RecordingStep("should_never_run")

    eng.register(
        "wf_missing_org",
        WorkflowDefinition(
            name="wf_missing_org",
            steps=[
                _spy_contract(spy, required_ctx_fields=["org_config"]),
                _spy_contract(later, required_ctx_fields=[]),
            ],
        ),
    )

    ctx = _ctx(org_config=None)
    result, _ = eng.execute("wf_missing_org", ctx)

    assert result.status == "error", f"expected error, got {result.status!r}"
    assert result.error_code == "E_REQUIRED_CTX_MISSING", (
        f"expected E_REQUIRED_CTX_MISSING, got {result.error_code!r}"
    )
    assert spy.called is False, "step.execute() must not run when required ctx missing"
    assert later.called is False, "subsequent steps must not run when workflow halts"


# ─── (b) empty-string is falsy → still hard-fail ─────────────────────────────


def test_engine_hardfails_when_required_ctx_empty_string():
    """Empty string is falsy and must trigger the same hard-fail path
    as None — `getattr(ctx, key, None)` truthy-check semantics."""
    eng = WorkflowEngine()
    spy = _RecordingStep("deregister_step")

    eng.register(
        "wf_empty_session",
        WorkflowDefinition(
            name="wf_empty_session",
            steps=[_spy_contract(spy, required_ctx_fields=["session_id"])],
        ),
    )

    ctx = _ctx(session_id="")
    result, _ = eng.execute("wf_empty_session", ctx)

    assert result.status == "error"
    assert result.error_code == "E_REQUIRED_CTX_MISSING"
    assert spy.called is False


# ─── (c) "unknown" sentinel — design path: normalize at run.py boundary ──────


def test_engine_hardfails_when_session_id_is_unknown_sentinel():
    """`"unknown"` is the silent-skip trigger at phase_8_post_deploy.py:604.

    GREEN path per S1 design: run.py parse_ctx stops defaulting to "unknown"
    (test_run_py_parse_ctx_does_not_default_session_id_to_unknown covers that
    boundary). At the engine layer, the sentinel itself is truthy, so unless
    callers explicitly normalize → None, hard-fail does NOT trigger. We assert
    the design contract: when callers DO pass `session_id=None`, the engine
    hard-fails. (The "unknown" string flowing through is the bug to be fixed
    upstream, not at the engine — engine sees None after run.py change.)
    """
    eng = WorkflowEngine()
    spy = _RecordingStep("deregister_session")

    eng.register(
        "wf_session_none",
        WorkflowDefinition(
            name="wf_session_none",
            steps=[_spy_contract(spy, required_ctx_fields=["session_id"])],
        ),
    )

    # Simulate the post-GREEN run.py behavior: session_id arrives as None,
    # not "unknown". Engine must hard-fail.
    ctx = _ctx(session_id=None)  # type: ignore[arg-type]
    result, _ = eng.execute("wf_session_none", ctx)

    assert result.status == "error"
    assert result.error_code == "E_REQUIRED_CTX_MISSING"
    assert spy.called is False


# ─── (d) sanity: hard-fail does NOT trigger when required ctx is present ─────


def test_engine_passes_when_required_ctx_present():
    """Required ctx field is set → step executes normally, no hard-fail."""
    eng = WorkflowEngine()
    executed = {"called": False}

    def _ok(_ctx, _prev):
        executed["called"] = True
        return StepResult(status="ok", data="went-through", duration_ms=0, step_name="s")

    eng.register(
        "wf_ok",
        WorkflowDefinition(
            name="wf_ok",
            steps=[
                StepContract(
                    name="s",
                    execute=_ok,
                    required_ctx_fields=["org_config"],
                )
            ],
        ),
    )

    ctx = _ctx(org_config={"scratchpad_dir": "/tmp/x"})
    result, _ = eng.execute("wf_ok", ctx)

    assert result.status == "ok"
    assert result.error_code != "E_REQUIRED_CTX_MISSING"
    assert executed["called"] is True
    assert result.data == "went-through"


# ─── (e) run.py parse_ctx must NOT default session_id to "unknown" ───────────


def test_run_py_parse_ctx_does_not_default_session_id_to_unknown():
    """parse_ctx must leave session_id as None when caller omitted it,
    NOT default to the literal string "unknown" (which slips past truthy
    checks and triggers silent-skip downstream — phase_8_post_deploy.py:604).
    """
    import argparse
    import run

    # Simulate the CLI args namespace passed to parse_ctx
    args = argparse.Namespace(
        ctx=None,
        ctx_json=json.dumps({"tenant_id": "t", "question": "q"}),
    )
    ctx = run.parse_ctx(args)

    assert ctx.session_id != "unknown", (
        "run.py parse_ctx must not default session_id to literal 'unknown' — "
        "it is the source of phase_8 silent-skip (S1 design: leave as None, "
        "let downstream engine hard-fail)"
    )
    # Acceptable: None (preferred), or the truly-empty default. Not "unknown".
    assert ctx.session_id is None or ctx.session_id == "", (
        f"expected session_id None or '', got {ctx.session_id!r}"
    )


# ─── (f) phase_05_inject inject step declares required_ctx_fields ────────────


def test_phase_05_inject_step_has_required_ctx_org_config():
    """The step that reads ctx.org_config (read_orchestrator_checklist —
    phase_05_inject.py:404 calls _resolve_scratchpad which reads org_config)
    must declare required_ctx_fields=['org_config'] so the engine hard-fails
    BEFORE silently treating None as `{}` at line 348."""
    from phase_05_inject import phase_05_inject_workflow

    wf = phase_05_inject_workflow()
    target = next(
        (s for s in wf.steps if s.name == "read_orchestrator_checklist"),
        None,
    )
    assert target is not None, (
        "phase_05_inject must have a read_orchestrator_checklist step"
    )
    assert hasattr(target, "required_ctx_fields"), (
        "StepContract.required_ctx_fields field missing — S1 GREEN must "
        "add this to contracts.StepContract"
    )
    assert "org_config" in target.required_ctx_fields, (
        f"read_orchestrator_checklist must declare required_ctx_fields "
        f"contains 'org_config'; got {target.required_ctx_fields!r}"
    )


# ─── (g) phase_8_post_deploy deregister step declares required_ctx_fields ────


def test_phase_8_deregister_step_has_required_ctx_session_id():
    """The deregister_session step at phase_8_post_deploy.py:596 silently
    skips when session_id is None/empty/'unknown' (line 604, returns
    status='ok' with skipped=True). It must declare
    required_ctx_fields=['session_id'] so the engine hard-fails instead."""
    from phase_8_post_deploy import phase_8_post_deploy_workflow

    wf = phase_8_post_deploy_workflow()
    target = next(
        (s for s in wf.steps if s.name == "deregister_session"),
        None,
    )
    assert target is not None, (
        "phase_8_post_deploy must have a deregister_session step"
    )
    assert hasattr(target, "required_ctx_fields"), (
        "StepContract.required_ctx_fields field missing — S1 GREEN must "
        "add this to contracts.StepContract"
    )
    assert "session_id" in target.required_ctx_fields, (
        f"deregister_session must declare required_ctx_fields "
        f"contains 'session_id'; got {target.required_ctx_fields!r}"
    )


# ─── (R1) empty required_ctx_fields list does NOT block execution ────────────


def test_engine_passes_when_required_ctx_fields_is_empty_list():
    """Empty required_ctx_fields list must bypass validation cleanly — even
    when the WorkflowContext has most fields set to None / "" (i.e. the
    engine must NOT halt purely because ctx has Nones; halt only when a
    *declared* required field is missing).

    Guards against a GREEN bug where the truthy gate `if step.required_ctx_fields:`
    is missing or where empty-list iteration somehow triggers halt.
    """
    eng = WorkflowEngine()
    executed = {"called": False}

    def _ok(_ctx, _prev):
        executed["called"] = True
        return StepResult(
            status="ok", data="ran-with-empty-required", duration_ms=0, step_name="s_empty"
        )

    eng.register(
        "wf_empty_required",
        WorkflowDefinition(
            name="wf_empty_required",
            steps=[
                StepContract(
                    name="s_empty",
                    execute=_ok,
                    required_ctx_fields=[],
                )
            ],
        ),
    )

    # Most ctx fields None / "" — none of them are *declared* required, so
    # the engine must still execute cleanly.
    ctx = _ctx(org_config=None, session_id="", framework=None, domain=None)
    result, _ = eng.execute("wf_empty_required", ctx)

    assert executed["called"] is True, (
        "step.execute() must run when required_ctx_fields is empty list, "
        "regardless of which other ctx fields happen to be None/empty"
    )
    assert result.status == "ok", f"expected ok, got {result.status!r}"
    assert result.error_code != "E_REQUIRED_CTX_MISSING", (
        "empty required_ctx_fields must not produce E_REQUIRED_CTX_MISSING"
    )


# ─── (R2) getattr default-None guards against schema drift ───────────────────


def test_engine_hardfails_when_ctx_attribute_does_not_exist():
    """If a step declares a required_ctx_fields key that is NOT a real
    attribute on WorkflowContext (typo or rename drift), the engine must
    hard-fail cleanly with E_REQUIRED_CTX_MISSING — NOT raise AttributeError
    to the caller.

    Guards against GREEN writing `getattr(context, key)` (no default), which
    would surface AttributeError instead of the hard-fail signal.
    """
    eng = WorkflowEngine()
    spy = _RecordingStep("schema_drift_step")

    eng.register(
        "wf_schema_drift",
        WorkflowDefinition(
            name="wf_schema_drift",
            steps=[
                _spy_contract(
                    spy,
                    required_ctx_fields=["definitely_not_a_field_on_context"],
                )
            ],
        ),
    )

    ctx = _ctx()  # well-formed ctx; the missing field is the *typo*, not absent data

    # Must not raise — engine must catch via getattr(..., None) and hard-fail.
    try:
        result, _ = eng.execute("wf_schema_drift", ctx)
    except AttributeError as exc:  # pragma: no cover — failure path documents the bug
        pytest.fail(
            f"engine leaked AttributeError to caller for missing ctx attr: {exc!r} "
            f"— GREEN must use getattr(context, key, None), not getattr(context, key)"
        )

    assert result.status == "error", f"expected error, got {result.status!r}"
    assert result.error_code == "E_REQUIRED_CTX_MISSING", (
        f"schema-drift typo must produce E_REQUIRED_CTX_MISSING (same signal "
        f"as a None value); got {result.error_code!r}"
    )
    assert spy.called is False, (
        "step.execute() must not run when a declared required field is "
        "absent from the context schema"
    )


# ─── (R3) run.py parse_ctx --ctx (file) branch must not default to "unknown" ─


def test_run_py_parse_ctx_file_branch_does_not_default_session_id_to_unknown(tmp_path):
    """parse_ctx has TWO branches: --ctx-json (covered by test (e)) AND
    --ctx <file>. Both must avoid the literal "unknown" default for
    session_id, otherwise GREEN may fix only one branch and the silent-skip
    bug returns through the file branch.
    """
    import argparse
    import run

    ctx_path = tmp_path / "ctx.json"
    ctx_path.write_text(json.dumps({"tenant_id": "t", "question": "q"}))

    args = argparse.Namespace(
        ctx=str(ctx_path),
        ctx_json=None,
    )
    ctx = run.parse_ctx(args)

    assert ctx.session_id != "unknown", (
        "run.py parse_ctx --ctx (file) branch must not default session_id "
        "to literal 'unknown' — same invariant as the --ctx-json branch; "
        "it is the source of phase_8 silent-skip"
    )
    assert ctx.session_id is None or ctx.session_id == "", (
        f"expected session_id None or '' (file branch), got {ctx.session_id!r}"
    )


# ─── (RED-wire) error string includes boundary_error with phase/field/producer/where/schema ───


def test_engine_error_string_includes_boundary_error_format():
    """RED test: error message must include boundary_error format per agreement 03192214.

    When E_REQUIRED_CTX_MISSING fires, the error string must contain:
    - "boundary_error" substring
    - phase={workflow.name}
    - field={missing_key}
    - producer=WorkflowContext.__init__
    - where={file location}
    - schema=WorkflowContext.{key}

    This test MUST FAIL on current main (error string is plain f-string) and
    PASS after GREEN wires format_boundary_error into engine.py.
    """
    eng = WorkflowEngine()
    spy = _RecordingStep("test_step")

    eng.register(
        "test_workflow_phase",
        WorkflowDefinition(
            name="test_workflow_phase",
            steps=[_spy_contract(spy, required_ctx_fields=["scope"])],
        ),
    )

    ctx = _ctx(scope=None)
    result, _ = eng.execute("test_workflow_phase", ctx)

    assert result.status == "error"
    assert result.error_code == "E_REQUIRED_CTX_MISSING"

    # RED assertion: error string must contain boundary_error format
    error_str = result.error
    assert "boundary_error" in error_str, (
        f"error string must contain 'boundary_error' substring; "
        f"got: {error_str!r}"
    )
    assert "phase=test_workflow_phase" in error_str, (
        f"error string must contain phase=test_workflow_phase; got: {error_str!r}"
    )
    assert "field=scope" in error_str, (
        f"error string must contain field=scope; got: {error_str!r}"
    )
    assert "producer=orchestrator" in error_str, (
        f"error string must contain producer=orchestrator; "
        f"got: {error_str!r}"
    )
    assert "where=engine.py" in error_str, (
        f"error string must contain where=engine.py; got: {error_str!r}"
    )
    assert "schema=WorkflowContext.scope" in error_str, (
        f"error string must contain schema=WorkflowContext.scope; "
        f"got: {error_str!r}"
    )
