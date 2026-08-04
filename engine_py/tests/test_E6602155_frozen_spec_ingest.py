"""RED tests for E6602155 — phase_45 frozen-spec ingest (validate-only fast-path).

New helpers (not yet implemented):
  - engine_py/bytedigger_engine/skip_logic.py::is_frozen_spec_text(text:str)->bool
  - engine_py/bytedigger_engine/skip_logic.py::detect_frozen_spec(decision_doc_raw:str|None)->tuple[bool,Path|None]

New workflow step (not yet implemented in either workflow):
  - step-0 "detect_frozen_spec" prepended to phase_45_spec_workflow() +
    phase_45_spec_lite_workflow() (or their LoopStepContract body)
  - writer steps return status="skip" when prev.data["is_frozen"] is True
  - _gate_on_review emits frozen_spec_fallback_to_full + returns
    E_VALIDATION_RETRY with data["frozen_fallback"]=True when REVISE on frozen

ALL 10 test functions MUST FAIL until GREEN implements the production change.

§1q / D1CF5FDF compliance:  new symbols (is_frozen_spec_text, detect_frozen_spec)
are NOT imported at module top-level.  Each test imports them inside the
function body so the file COLLECTS cleanly and tests FAIL at assert/ImportError
time, never at collection time.

STEP-LEVEL PATTERN (no engine.execute, no subprocess, no 124-hang risk):
  wf = phase_45_spec_workflow()
  step = next(s for s in wf.steps if s.name == "<name>")
  result = step.execute(ctx, prev)
  assert result.status / result.data / captured events

sys.path is owned by the conftest-import-time singleton (§1q / 81F97F3D gate).
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Any

# Module-level imports: ONLY symbols that already exist in production.
# conftest.py handles sys.path; no sys.path manipulation here.
from bytedigger_engine.contracts import StepResult, WorkflowContext  # noqa: E402
from bytedigger_engine.workflows import phase_45_spec as _p45  # noqa: E402
from bytedigger_engine.workflows import phase_45_spec_lite as _p45l  # noqa: E402
from bytedigger_engine.workflows.phase_45_spec import (  # noqa: E402
    SPEC_DOC_RELPATH,
    VERDICT_REVISE,
    phase_45_spec_workflow,
)
from bytedigger_engine.workflows.phase_45_spec_lite import phase_45_spec_lite_workflow  # noqa: E402


# ─── Shared fixtures ──────────────────────────────────────────────────────────

# A frozen spec must contain BOTH: the AC-table heading (matches the regex)
# AND the preflight marker.  No detection tokens in fixture comments (§1m).
_FROZEN_DOC_BODY = (
    "# My Feature Spec\n"
    "\n"
    "## Context\n"
    "This spec has been ratified and frozen before the build.\n"
    "\n"
    "## Behavior\n"
    "- Add a new command.\n"
    "\n"
    "## Files\n"
    "MODIFY: foo/bar.py\n"
    "\n"
    "## Constraints\n"
    "- No breaking changes.\n"
    "\n"
    "## Out of Scope\n"
    "- Unrelated cleanup.\n"
    "\n"
    "## §3 Acceptance Criteria\n"
    "| # | AC | Forcing-function |\n"
    "|---|----|------------------|\n"
    "| 1 | foo works | unit test |\n"
    "\n"
    "§1-PREFLIGHT self-audit passed.\n"
    "\n"
    "## Open Questions\n"
    "none\n"
)

# A non-frozen doc: no AC table, no preflight marker.
_NON_FROZEN_DOC_BODY = (
    "# Plain decision doc\n"
    "\n"
    "## Context\n"
    "A plain doc with no criteria table and no preflight.\n"
    "\n"
    "## Approach\n"
    "Just a decision description.\n"
)


def _make_tmp_frozen_doc(tmp_path: Path) -> Path:
    """Write a frozen spec to a realpath tmp dir (§1j)."""
    real_dir = Path(os.path.realpath(tempfile.mkdtemp(dir=str(tmp_path))))
    doc = real_dir / "frozen_spec.md"
    doc.write_text(_FROZEN_DOC_BODY, encoding="utf-8")
    return doc


def _make_tmp_non_frozen_doc(tmp_path: Path) -> Path:
    """Write a non-frozen doc to a realpath tmp dir (§1j)."""
    real_dir = Path(os.path.realpath(tempfile.mkdtemp(dir=str(tmp_path))))
    doc = real_dir / "plain_doc.md"
    doc.write_text(_NON_FROZEN_DOC_BODY, encoding="utf-8")
    return doc


def _make_ctx(
    scratchpad: Path,
    *,
    question: str = "Add feature X",
    **org_extra: Any,
) -> WorkflowContext:
    org: dict[str, Any] = {
        "scratchpad_dir": str(scratchpad),
        "spec_lint_skip": True,
        **org_extra,
    }
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question=question,
        session_id="test-e6602155",
        persona="hal",
        framework=None,
        domain=None,
    )


def _patch_emit(monkeypatch, module) -> list[tuple[str, dict]]:
    """Monkeypatch module._emit_safe; return captured (event_type, payload) list.
    Mirrors pattern from test_phase_45_spec_telemetry_D7B5BFB3.py."""
    captured: list[tuple[str, dict]] = []
    monkeypatch.setattr(module, "_emit_safe", lambda et, p: captured.append((et, p)))
    return captured


def _events_of(captured: list[tuple[str, dict]], event_type: str) -> list[dict]:
    return [p for et, p in captured if et == event_type]


def _make_gate_prev(
    tmp_path: Path,
    verdict: str,
    cycle: int = 1,
    *,
    is_frozen: bool = False,
    raw_review: str = "",
) -> StepResult:
    """Build a StepResult simulating what _write_review_doc returns,
    suitable for passing directly to _gate_on_review.
    Mirrors _make_gate_prev in test_phase_45_spec_telemetry_D7B5BFB3.py."""
    review_path = tmp_path / "build-plan-review.md"
    review_path.write_text(raw_review or f"## Verdict\n{verdict}\n")
    spec_path = tmp_path / "build-spec.md"
    spec_path.write_text(_FROZEN_DOC_BODY if is_frozen else "## Context\nstub\n")
    return StepResult(
        status="ok",
        data={
            "verdict": verdict,
            "cycle": cycle,
            "review_path": str(review_path),
            "spec_path": str(spec_path),
            "review_raw": raw_review or f"## Verdict\n{verdict}\n",
            "is_frozen": is_frozen,
        },
        duration_ms=0,
        step_name="write_review_doc",
    )


# ─── AC1: is_frozen_spec_text — 4-case unit test ─────────────────────────────


def test_ac1_is_frozen_spec_text_four_cases():
    """AC1: is_frozen_spec_text True only when BOTH AC-table heading AND
    preflight marker present; False for every other combination (4 cases)."""
    # Deferred import: symbol does not exist in production yet (§1q / D1CF5FDF).
    from bytedigger_engine.skip_logic import is_frozen_spec_text  # noqa: PLC0415

    both_present = (
        "## §3 Acceptance Criteria\n| 1 | foo | bar |\n\n§1-PREFLIGHT done.\n"
    )
    ac_only = "## Acceptance Criteria\n| 1 | foo | bar |\n"
    marker_only = "§1-PREFLIGHT done.\n"
    neither = "## Context\nJust a plain doc.\n"

    assert is_frozen_spec_text(both_present) is True, (
        "BOTH AC heading + preflight marker → must return True"
    )
    assert is_frozen_spec_text(ac_only) is False, (
        "AC heading WITHOUT preflight marker → must return False"
    )
    assert is_frozen_spec_text(marker_only) is False, (
        "Preflight marker WITHOUT AC heading → must return False"
    )
    assert is_frozen_spec_text(neither) is False, (
        "NEITHER present → must return False"
    )


# ─── AC2: detect_frozen_spec graceful on unset/missing ───────────────────────


def test_ac2_detect_frozen_spec_graceful(tmp_path):
    """AC2: detect_frozen_spec returns (False, None) on None, empty, missing path."""
    # Deferred import: symbol does not exist in production yet (§1q / D1CF5FDF).
    from bytedigger_engine.skip_logic import detect_frozen_spec  # noqa: PLC0415

    result_none = detect_frozen_spec(None)
    assert result_none == (False, None), (
        f"detect_frozen_spec(None) should be (False, None), got {result_none}"
    )

    result_empty = detect_frozen_spec("")
    assert result_empty == (False, None), (
        f"detect_frozen_spec('') should be (False, None), got {result_empty}"
    )

    missing = Path(os.path.realpath(str(tmp_path))) / "does_not_exist.md"
    result_missing = detect_frozen_spec(str(missing))
    assert result_missing == (False, None), (
        f"detect_frozen_spec(missing_path) should be (False, None), got {result_missing}"
    )


# ─── AC3: step-0 with frozen doc → writer step returns status="skip" ─────────


def test_ac3_frozen_doc_writer_step_skipped(tmp_path, monkeypatch):
    """AC3 (step-level): after step-0 sets is_frozen=True in prev.data, the
    invoke_spec_llm StepContract returns status='skip' without calling the LLM.

    Approach: find the 'invoke_spec_llm' step in the workflow, build a prev
    StepResult as if step-0 returned is_frozen=True, call step.execute directly.
    """
    scratchpad = Path(os.path.realpath(tempfile.mkdtemp(dir=str(tmp_path)))) / "scratch"
    frozen_doc = _make_tmp_frozen_doc(tmp_path)

    wf = phase_45_spec_workflow()
    # Step-0 ("detect_frozen_spec") will be the first step post-GREEN;
    # we locate it and also the writer step.
    step0 = next((s for s in wf.steps if s.name == "detect_frozen_spec"), None)
    assert step0 is not None, (
        "AC3 FAIL: step 'detect_frozen_spec' not found in phase_45_spec_workflow(). "
        f"Actual steps: {[s.name for s in wf.steps]}"
    )

    # Call step-0 with decision_doc set to a frozen file.
    ctx = _make_ctx(scratchpad, decision_doc=str(frozen_doc))
    # prev for step-0 is initial_data (empty dict-like); use empty StepResult.
    initial_prev = StepResult(
        status="ok", data={}, duration_ms=0, step_name="__initial__"
    )
    result0 = step0.execute(ctx, initial_prev)
    assert isinstance(result0.data, dict) and result0.data.get("is_frozen") is True, (
        f"AC3 FAIL: step-0 should return is_frozen=True for a frozen doc. "
        f"Got data={result0.data!r}, status={result0.status!r}"
    )

    # Now call the writer step with that prev — it should skip.
    invoke_step = next((s for s in wf.steps if s.name == "invoke_spec_llm"), None)
    assert invoke_step is not None, (
        f"AC3 FAIL: 'invoke_spec_llm' step not found. Steps: {[s.name for s in wf.steps]}"
    )
    writer_result = invoke_step.execute(ctx, result0)
    assert writer_result.status == "skip", (
        f"AC3 FAIL: invoke_spec_llm should return status='skip' when is_frozen=True, "
        f"got status={writer_result.status!r}, data={writer_result.data!r}"
    )


# ─── AC4: step-0 with frozen doc → frozen_spec_ingested emitted ──────────────


def test_ac4_frozen_spec_ingested_event_emitted(tmp_path, monkeypatch):
    """AC4 (step-level): step-0 emits 'frozen_spec_ingested' with doc_path,
    ac_table=True, preflight=True when decision_doc is frozen.

    We patch _emit_safe on BOTH skip_logic AND phase_45_spec because GREEN
    may place the emit call in either module's step-0 wrapper."""
    scratchpad = Path(os.path.realpath(tempfile.mkdtemp(dir=str(tmp_path)))) / "scratch"
    frozen_doc = _make_tmp_frozen_doc(tmp_path)

    # Capture emits from both candidate emit sites.
    from bytedigger_engine import skip_logic as _sl  # noqa: PLC0415  — already exists, safe to import
    captured: list[tuple[str, dict]] = []
    monkeypatch.setattr(_sl, "_emit_safe", lambda et, p: captured.append((et, p)))
    monkeypatch.setattr(_p45, "_emit_safe", lambda et, p: captured.append((et, p)))

    wf = phase_45_spec_workflow()
    step0 = next((s for s in wf.steps if s.name == "detect_frozen_spec"), None)
    assert step0 is not None, (
        "AC4 FAIL: step 'detect_frozen_spec' not found in phase_45_spec_workflow(). "
        f"Actual steps: {[s.name for s in wf.steps]}"
    )

    ctx = _make_ctx(scratchpad, decision_doc=str(frozen_doc))
    initial_prev = StepResult(status="ok", data={}, duration_ms=0, step_name="__initial__")
    step0.execute(ctx, initial_prev)

    ingest_events = _events_of(captured, "frozen_spec_ingested")
    assert len(ingest_events) == 1, (
        f"AC4 FAIL: expected 1 'frozen_spec_ingested' event from step-0, "
        f"got {len(ingest_events)}. Captured: {[(et, p) for et, p in captured]}"
    )
    payload = ingest_events[0]
    assert "doc_path" in payload, (
        f"AC4 FAIL: frozen_spec_ingested payload missing 'doc_path': {payload}"
    )
    assert payload.get("ac_table") is True, (
        f"AC4 FAIL: frozen_spec_ingested payload missing ac_table=True: {payload}"
    )
    assert payload.get("preflight") is True, (
        f"AC4 FAIL: frozen_spec_ingested payload missing preflight=True: {payload}"
    )


# ─── AC5: step-0 copies frozen doc verbatim to specs/build-spec.md ────────────


def test_ac5_canonical_spec_byte_equals_frozen_doc(tmp_path, monkeypatch):
    """AC5 (step-level): step-0 writes the frozen doc verbatim to
    specs/build-spec.md (SPEC_DOC_RELPATH) in the scratchpad."""
    from bytedigger_engine import skip_logic as _sl  # noqa: PLC0415
    # Swallow emits from both candidate modules so we focus on the file write.
    _patch_emit(monkeypatch, _sl)
    _patch_emit(monkeypatch, _p45)

    scratchpad = Path(os.path.realpath(tempfile.mkdtemp(dir=str(tmp_path)))) / "scratch"
    frozen_doc = _make_tmp_frozen_doc(tmp_path)

    wf = phase_45_spec_workflow()
    step0 = next((s for s in wf.steps if s.name == "detect_frozen_spec"), None)
    assert step0 is not None, (
        "AC5 FAIL: step 'detect_frozen_spec' not found in phase_45_spec_workflow(). "
        f"Actual steps: {[s.name for s in wf.steps]}"
    )

    ctx = _make_ctx(scratchpad, decision_doc=str(frozen_doc))
    initial_prev = StepResult(status="ok", data={}, duration_ms=0, step_name="__initial__")
    step0.execute(ctx, initial_prev)

    spec_path = scratchpad / SPEC_DOC_RELPATH
    assert spec_path.exists(), (
        f"AC5 FAIL: {SPEC_DOC_RELPATH} not written at {spec_path} by step-0"
    )
    written_bytes = spec_path.read_bytes()
    frozen_bytes = frozen_doc.read_bytes()
    assert written_bytes == frozen_bytes, (
        f"AC5 FAIL: specs/build-spec.md does not byte-equal the frozen doc. "
        f"written={len(written_bytes)}B frozen={len(frozen_bytes)}B"
    )


# ─── AC6: frozen ingest → reviewer step still executes ───────────────────────


def test_ac6_reviewer_not_skipped_on_frozen_ingest(tmp_path, monkeypatch):
    """AC6 (step-level): frozen ingest → reviewer step is NOT skipped.

    Forcing-function: the detect_frozen_spec step must exist (fails today) AND
    the invoke_review_llm step called with prev carrying is_frozen=True must
    return status != 'skip' (proves the Plan-Review Opus turn still runs).

    Fails today because step 'detect_frozen_spec' is absent from the workflow.
    """
    scratchpad = Path(os.path.realpath(tempfile.mkdtemp(dir=str(tmp_path)))) / "scratch"
    frozen_doc = _make_tmp_frozen_doc(tmp_path)

    wf = phase_45_spec_workflow()

    # Precondition: detect_frozen_spec step must exist (fails today — no such step).
    step0 = next((s for s in wf.steps if s.name == "detect_frozen_spec"), None)
    assert step0 is not None, (
        "AC6 FAIL: step 'detect_frozen_spec' not found in phase_45_spec_workflow(). "
        f"Actual steps: {[s.name for s in wf.steps]}"
    )

    # Meaningful part: call step-0 with frozen doc to get is_frozen=True prev,
    # then call the reviewer step and assert it does NOT skip.
    from bytedigger_engine import skip_logic as _sl  # noqa: PLC0415
    monkeypatch.setattr(_sl, "_emit_safe", lambda et, p: None)
    monkeypatch.setattr(_p45, "_emit_safe", lambda et, p: None)

    # Write the canonical spec so invoke_review_llm can reference it.
    spec_dir = scratchpad / "specs"
    spec_dir.mkdir(parents=True, exist_ok=True)
    spec_file = scratchpad / SPEC_DOC_RELPATH
    spec_file.write_text(_FROZEN_DOC_BODY)

    ctx = _make_ctx(scratchpad, decision_doc=str(frozen_doc))
    initial_prev = StepResult(status="ok", data={}, duration_ms=0, step_name="__initial__")
    result0 = step0.execute(ctx, initial_prev)
    assert result0.data.get("is_frozen") is True  # step-0 must set this

    # Now call invoke_review_llm with the post-step-0 prev threaded through.
    # Build a prompt prev as if build_review_prompt ran after frozen ingest.
    ship_verdict = "## Verdict\nSHIP\n## Concerns Checked\n- none\n## Findings\nnone\n"
    review_doc_path = str(scratchpad / "specs/build-plan-review.md")
    prompt_prev = StepResult(
        status="ok",
        data={
            "prompt": ship_verdict,
            "doc_path": review_doc_path,
            "spec_path": str(spec_file),
            "cycle": 1,
            "is_frozen": True,
        },
        duration_ms=0,
        step_name="build_review_prompt",
    )
    ship_escaped = ship_verdict.replace("'", "\\'").replace("\n", "\\n")
    echo_cmd = ["python3", "-c", f"import sys; sys.stdin.read(); sys.stdout.write('{ship_escaped}')"]
    ctx_with_cmd = _make_ctx(scratchpad, decision_doc=str(frozen_doc), review_llm_command=echo_cmd)

    review_step = next(s for s in wf.steps if s.name == "invoke_review_llm")
    result = review_step.execute(ctx_with_cmd, prompt_prev)

    assert result.status != "skip", (
        f"AC6 FAIL: invoke_review_llm must NOT be skipped on frozen ingest "
        f"(the Plan-Review Opus turn must still run). Got status={result.status!r}"
    )


# ─── AC7: gate with is_frozen=True + REVISE → fallback event + frozen_fallback


def test_ac7_revise_on_frozen_emits_fallback_event(tmp_path, monkeypatch):
    """AC7 (step-level): _gate_on_review, when called with is_frozen=True and
    verdict=REVISE, emits 'frozen_spec_fallback_to_full' and returns
    E_VALIDATION_RETRY with data carrying frozen_fallback=True."""
    captured = _patch_emit(monkeypatch, _p45)

    wf = phase_45_spec_workflow()
    gate_step = next((s for s in wf.steps if s.name == "gate_on_review"), None)
    assert gate_step is not None, (
        f"AC7 FAIL: 'gate_on_review' step not found. Steps: {[s.name for s in wf.steps]}"
    )

    prev = _make_gate_prev(
        tmp_path,
        VERDICT_REVISE,
        cycle=1,
        is_frozen=True,
        raw_review="## Verdict\nREVISE\n## Findings\n1. needs more context\n",
    )
    ctx = _make_ctx(tmp_path / "scratch")
    result = gate_step.execute(ctx, prev)

    # Must emit frozen_spec_fallback_to_full (not just the normal phase_45_spec_revise).
    fallback_events = _events_of(captured, "frozen_spec_fallback_to_full")
    assert len(fallback_events) >= 1, (
        f"AC7 FAIL: expected 'frozen_spec_fallback_to_full' event when "
        f"REVISE on a frozen-ingested spec. "
        f"Captured: {[(et, p) for et, p in captured]}"
    )

    # Must return a recoverable retry so the engine re-runs from step-0.
    assert result.error_code == "E_VALIDATION_RETRY", (
        f"AC7 FAIL: gate should return E_VALIDATION_RETRY on frozen-REVISE, "
        f"got error_code={result.error_code!r}"
    )
    assert result.recoverable is True, (
        f"AC7 FAIL: E_VALIDATION_RETRY must be recoverable=True"
    )

    # The retry initial_data must carry frozen_fallback=True.
    retry_data = result.data or {}
    assert retry_data.get("frozen_fallback") is True, (
        f"AC7 FAIL: gate result.data should carry frozen_fallback=True so "
        f"step-0 detector skips re-ingest on retry. "
        f"result.data={retry_data!r}"
    )


# ─── AC8: step-0 with frozen_fallback=True → is_frozen=False (writer will run)


def test_ac8_fallback_prev_clears_is_frozen(tmp_path, monkeypatch):
    """AC8 (step-level, 2-part contract):
    (a) _gate_on_review REVISE+frozen → result.data carries frozen_fallback=True (AC7 sub).
    (b) step-0 called with prev={'frozen_fallback': True} → data['is_frozen'] is False,
        so the normal writer cycle runs on retry (no re-ingest).
    """
    from bytedigger_engine import skip_logic as _sl  # noqa: PLC0415
    _patch_emit(monkeypatch, _sl)  # swallow emits from step-0

    # Part (a): confirm gate produces frozen_fallback=True (quick assertion).
    captured_gate = _patch_emit(monkeypatch, _p45)
    wf = phase_45_spec_workflow()
    gate_step = next((s for s in wf.steps if s.name == "gate_on_review"), None)
    assert gate_step is not None, (
        f"AC8 FAIL: 'gate_on_review' not found. Steps: {[s.name for s in wf.steps]}"
    )
    gate_prev = _make_gate_prev(
        tmp_path, VERDICT_REVISE, cycle=1, is_frozen=True,
        raw_review="## Verdict\nREVISE\n## Findings\n1. needs rework\n",
    )
    gate_result = gate_step.execute(_make_ctx(tmp_path / "scratch"), gate_prev)
    gate_data = gate_result.data or {}
    assert gate_data.get("frozen_fallback") is True, (
        f"AC8(a) FAIL: gate result.data should carry frozen_fallback=True. "
        f"result.data={gate_data!r}"
    )

    # Part (b): step-0 on retry (prev = forwarded initial_data with frozen_fallback=True)
    # should return is_frozen=False so the writer step is NOT skipped.
    scratchpad = Path(os.path.realpath(tempfile.mkdtemp(dir=str(tmp_path)))) / "scratch_retry"
    frozen_doc = _make_tmp_frozen_doc(tmp_path)

    step0 = next((s for s in wf.steps if s.name == "detect_frozen_spec"), None)
    assert step0 is not None, (
        "AC8(b) FAIL: step 'detect_frozen_spec' not found in phase_45_spec_workflow(). "
        f"Actual steps: {[s.name for s in wf.steps]}"
    )

    # Simulate what engine threads as prev on retry: the gate's data dict.
    retry_prev = StepResult(
        status="ok",
        data={"frozen_fallback": True, "cycle": 2, "findings": "1. needs rework"},
        duration_ms=0,
        step_name="__retry_initial__",
    )
    ctx_retry = _make_ctx(scratchpad, decision_doc=str(frozen_doc))
    result0_retry = step0.execute(ctx_retry, retry_prev)

    assert result0_retry.data.get("is_frozen") is False, (
        f"AC8(b) FAIL: step-0 with frozen_fallback=True in prev should produce "
        f"is_frozen=False (writer runs normally on retry). "
        f"Got data={result0_retry.data!r}"
    )


# ─── AC9: step-0 with non-frozen doc → is_frozen=False, no frozen_* events ───


def test_ac9_non_frozen_doc_no_frozen_events(tmp_path, monkeypatch):
    """AC9 (step-level): step-0 with a non-frozen decision_doc returns
    is_frozen=False and emits NO frozen_* events."""
    from bytedigger_engine import skip_logic as _sl  # noqa: PLC0415
    # Capture from both candidate emit modules (GREEN may emit from either).
    captured: list[tuple[str, dict]] = []
    monkeypatch.setattr(_sl, "_emit_safe", lambda et, p: captured.append((et, p)))
    monkeypatch.setattr(_p45, "_emit_safe", lambda et, p: captured.append((et, p)))

    non_frozen_doc = _make_tmp_non_frozen_doc(tmp_path)
    scratchpad = Path(os.path.realpath(tempfile.mkdtemp(dir=str(tmp_path)))) / "scratch"

    wf = phase_45_spec_workflow()
    step0 = next((s for s in wf.steps if s.name == "detect_frozen_spec"), None)
    assert step0 is not None, (
        "AC9 FAIL: step 'detect_frozen_spec' not found in phase_45_spec_workflow(). "
        f"Actual steps: {[s.name for s in wf.steps]}"
    )

    ctx = _make_ctx(scratchpad, decision_doc=str(non_frozen_doc))
    initial_prev = StepResult(status="ok", data={}, duration_ms=0, step_name="__initial__")
    result = step0.execute(ctx, initial_prev)

    assert result.data.get("is_frozen") is False, (
        f"AC9 FAIL: step-0 with non-frozen doc should return is_frozen=False. "
        f"Got data={result.data!r}"
    )

    frozen_events = [(et, p) for et, p in captured if et.startswith("frozen_spec_")]
    assert frozen_events == [], (
        f"AC9 FAIL: non-frozen doc must produce zero frozen_* events, "
        f"got: {frozen_events}"
    )


# ─── AC10: phase_45_spec_lite also has step-0 and honors detector ─────────────


def test_ac10_lite_workflow_honors_detector(tmp_path, monkeypatch):
    """AC10 (step-level): phase_45_spec_lite_workflow() also contains a
    'detect_frozen_spec' step-0 that (a) emits frozen_spec_ingested and
    (b) returns is_frozen=True for a frozen doc, and (c) writer step skips.

    The lite workflow wraps its steps in a LoopStepContract body; the new
    step-0 is prepended to that body.  We locate it via
    build_review_loop_contract().body or the workflow's outer step-0
    (depending on GREEN's implementation choice)."""
    from bytedigger_engine import skip_logic as _sl  # noqa: PLC0415
    # Capture from all three candidate emit modules (step-0 may call _emit_safe
    # from skip_logic, phase_45_spec, or phase_45_spec_lite depending on impl).
    captured: list[tuple[str, dict]] = []
    monkeypatch.setattr(_sl, "_emit_safe", lambda et, p: captured.append((et, p)))
    monkeypatch.setattr(_p45, "_emit_safe", lambda et, p: captured.append((et, p)))
    monkeypatch.setattr(_p45l, "_emit_safe", lambda et, p: captured.append((et, p)))

    frozen_doc = _make_tmp_frozen_doc(tmp_path)
    scratchpad = Path(os.path.realpath(tempfile.mkdtemp(dir=str(tmp_path)))) / "scratch_lite"

    wf_lite = phase_45_spec_lite_workflow()

    # GREEN may place step-0 as a top-level step OR inside the loop body.
    # Try top-level first, then fall back to the loop body.
    step0 = next((s for s in wf_lite.steps if s.name == "detect_frozen_spec"), None)
    if step0 is None:
        # Try the loop body (LoopStepContract): the first step in wf_lite is
        # "review_cycle_loop"; its contract body is accessible via build_review_loop_contract.
        from bytedigger_engine.workflows.phase_45_spec_lite import build_review_loop_contract  # noqa: PLC0415
        loop_contract = build_review_loop_contract()
        step0 = next(
            (s for s in loop_contract.body if s.name == "detect_frozen_spec"), None
        )

    assert step0 is not None, (
        "AC10 FAIL: step 'detect_frozen_spec' not found in "
        "phase_45_spec_lite_workflow() (checked top-level AND loop body). "
        f"Top-level steps: {[s.name for s in wf_lite.steps]}"
    )

    ctx = _make_ctx(scratchpad, complexity="SIMPLE", decision_doc=str(frozen_doc))
    initial_prev = StepResult(status="ok", data={}, duration_ms=0, step_name="__initial__")
    result0 = step0.execute(ctx, initial_prev)

    # (a) is_frozen=True returned
    assert result0.data.get("is_frozen") is True, (
        f"AC10 FAIL: lite step-0 should return is_frozen=True for frozen doc. "
        f"Got data={result0.data!r}"
    )

    # (b) frozen_spec_ingested emitted
    ingest_events = _events_of(captured, "frozen_spec_ingested")
    assert len(ingest_events) >= 1, (
        f"AC10 FAIL: lite step-0 should emit 'frozen_spec_ingested'. "
        f"Captured: {[(et, p) for et, p in captured]}"
    )

    # (c) writer step (maybe_invoke_spec_rewrite) skips when is_frozen=True
    # Find the rewrite step in the lite loop body.
    from bytedigger_engine.workflows.phase_45_spec_lite import build_review_loop_contract  # noqa: PLC0415
    loop_contract = build_review_loop_contract()
    rewrite_step = next(
        (s for s in loop_contract.body if s.name == "maybe_invoke_spec_rewrite"), None
    )
    assert rewrite_step is not None, (
        f"AC10 FAIL: 'maybe_invoke_spec_rewrite' not found in lite loop body. "
        f"Body steps: {[s.name for s in loop_contract.body]}"
    )
    writer_result = rewrite_step.execute(ctx, result0)
    assert writer_result.status == "skip", (
        f"AC10 FAIL: lite maybe_invoke_spec_rewrite should return status='skip' "
        f"when is_frozen=True. Got status={writer_result.status!r}"
    )


# ─── AC11: is_frozen + frozen_doc_path survive the full real step chain ───────


def test_ac11_is_frozen_survives_real_step_chain(tmp_path, monkeypatch):
    """AC11 (§1y threading): running the REAL step chain step-by-step (no engine,
    no real LLM), is_frozen and frozen_doc_path survive from step-0 through every
    intermediate step into the gate's preceding prev.

    This catches the bug Opus found: an intermediate step rebuilding `data` without
    forwarding is_frozen → gate never sees it → fallback dead.

    Fails today at `assert step0 is not None` (detect_frozen_spec absent).
    After GREEN it passes ONLY if every intermediate step threads the flags.
    """
    from bytedigger_engine import skip_logic as _sl  # noqa: PLC0415
    # Swallow telemetry emits from all candidate modules.
    monkeypatch.setattr(_sl, "_emit_safe", lambda et, p: None)
    monkeypatch.setattr(_p45, "_emit_safe", lambda et, p: None)

    scratchpad = Path(os.path.realpath(tempfile.mkdtemp(dir=str(tmp_path)))) / "scratch"
    frozen_doc = _make_tmp_frozen_doc(tmp_path)

    wf = phase_45_spec_workflow()

    # Precondition: detect_frozen_spec step must exist (fails today — no such step).
    step0 = next((s for s in wf.steps if s.name == "detect_frozen_spec"), None)
    assert step0 is not None, (
        "AC11 FAIL: step 'detect_frozen_spec' not found in phase_45_spec_workflow(). "
        f"Actual steps: {[s.name for s in wf.steps]}"
    )

    # Steps to run in order from step-0 up to (but not including) gate_on_review.
    # After GREEN prepends detect_frozen_spec, the order is:
    #   detect_frozen_spec → build_spec_prompt → invoke_spec_llm → write_spec_doc →
    #   verify_spec_completeness → verify_spec_citations → verify_spec_lint →
    #   build_review_prompt → invoke_review_llm → write_review_doc → gate_on_review
    # We stop just before gate_on_review and assert the prev it would receive.
    stop_before = "gate_on_review"

    # Patch the two LLM subprocess steps so they return fast without spawning.
    ship_verdict = "## Verdict\nSHIP\n## Concerns Checked\n- none\n## Findings\nnone\n"

    def _fake_invoke_spec_llm(ctx, prev):
        # Frozen path means this step returns skip; forward keys.
        fwd = prev.data if isinstance(prev.data, dict) else {}
        return StepResult(
            status="skip",
            data={**fwd},
            duration_ms=0,
            step_name="invoke_spec_llm",
        )

    def _fake_invoke_review_llm(ctx, prev):
        fwd = prev.data if isinstance(prev.data, dict) else {}
        review_path = scratchpad / "specs" / "build-plan-review.md"
        review_path.parent.mkdir(parents=True, exist_ok=True)
        review_path.write_text(ship_verdict)
        return StepResult(
            status="ok",
            data={**fwd, "raw_response": ship_verdict, "doc_path": str(review_path)},
            duration_ms=0,
            step_name="invoke_review_llm",
        )

    monkeypatch.setattr(_p45, "_invoke_spec_llm", _fake_invoke_spec_llm)
    monkeypatch.setattr(_p45, "_invoke_review_llm", _fake_invoke_review_llm)

    ctx = _make_ctx(scratchpad, decision_doc=str(frozen_doc))
    initial_prev = StepResult(status="ok", data={}, duration_ms=0, step_name="__initial__")

    # Run step-0 first.
    prev = step0.execute(ctx, initial_prev)
    assert prev.data.get("is_frozen") is True, (
        f"AC11 FAIL: step-0 must return is_frozen=True. data={prev.data!r}"
    )

    # Thread through every subsequent step up to (not including) gate_on_review.
    for step in wf.steps:
        if step.name == "detect_frozen_spec":
            continue  # already ran
        if step.name == stop_before:
            break
        prev = step.execute(ctx, prev)
        # Each step must not drop status to error in a way that loses the data dict.
        # (skip is fine — frozen writer returns skip but must still forward data.)

    # Assert the flags survived the full chain into gate's incoming prev.
    assert isinstance(prev.data, dict), (
        f"AC11 FAIL: gate's incoming prev.data is not a dict: {prev.data!r}"
    )
    assert prev.data.get("is_frozen") is True, (
        f"AC11 FAIL: is_frozen was lost traversing the step chain. "
        f"gate prev.data={prev.data!r} (step chain did not forward the flag)"
    )
    assert "frozen_doc_path" in prev.data, (
        f"AC11 FAIL: frozen_doc_path was lost traversing the step chain. "
        f"gate prev.data={prev.data!r}"
    )


# ─── AC12: deterministic gate steps skip when is_frozen=True ─────────────────


def test_ac12_deterministic_gates_skip_on_frozen(tmp_path, monkeypatch):
    """AC12 (step-level): verify_spec_completeness, verify_spec_citations, and
    verify_spec_lint each return status='skip' when prev.data carries is_frozen=True.

    Fails today because none of these steps honor is_frozen (not yet implemented).
    """
    monkeypatch.setattr(_p45, "_emit_safe", lambda et, p: None)

    scratchpad = Path(os.path.realpath(tempfile.mkdtemp(dir=str(tmp_path)))) / "scratch"
    spec_dir = scratchpad / "specs"
    spec_dir.mkdir(parents=True, exist_ok=True)
    spec_file = scratchpad / SPEC_DOC_RELPATH
    spec_file.write_text(_FROZEN_DOC_BODY)

    wf = phase_45_spec_workflow()
    gate_names = ("verify_spec_completeness", "verify_spec_citations", "verify_spec_lint")

    # Build a prev StepResult that carries is_frozen=True and the keys these
    # steps expect (spec_path, cycle) so they can run without KeyError.
    frozen_prev = StepResult(
        status="ok",
        data={
            "is_frozen": True,
            "frozen_doc_path": str(spec_file),
            "spec_path": str(spec_file),
            "cycle": 1,
        },
        duration_ms=0,
        step_name="write_spec_doc",
    )
    ctx = _make_ctx(scratchpad)

    for gate_name in gate_names:
        gate_step = next((s for s in wf.steps if s.name == gate_name), None)
        assert gate_step is not None, (
            f"AC12 FAIL: step '{gate_name}' not found in phase_45_spec_workflow(). "
            f"Actual steps: {[s.name for s in wf.steps]}"
        )
        result = gate_step.execute(ctx, frozen_prev)
        assert result.status == "skip", (
            f"AC12 FAIL: {gate_name} should return status='skip' when is_frozen=True, "
            f"got status={result.status!r}, error_code={result.error_code!r}"
        )
