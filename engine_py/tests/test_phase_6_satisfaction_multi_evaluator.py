"""RED tests for 021D8FAE — phase_6 satisfaction 3-evaluator majority+median for COMPLEX builds.

Agreement: 021D8FAE-D00D-49DA-B5E3-06CB83F18EC8 (MEDIUM)

Contract (GREEN will implement):
1. _invoke_satisfaction_llm branches: complexity == "COMPLEX" → 3 parallel evaluators
   via _run_satisfaction_evaluators_parallel; SIMPLE/FEATURE/unset → single call (unchanged).
2. _run_satisfaction_evaluators_parallel: dispatches n=3 concurrent invoke_llm_subprocess calls
   via ThreadPoolExecutor; returns list[StepResult] in submission/index order.
3. _aggregate_satisfaction: new pure helper — median score, majority vote, agreement label,
   degraded flag.
4. _write_satisfaction_doc: if is_multi_evaluator+evaluator_responses → multi-eval branch;
   writes composite doc with ## Aggregation Summary + ## Evaluator N sections;
   emits satisfaction_multi_evaluator or satisfaction_multi_evaluator_degraded telemetry;
   gate decision via majority/median for n_valid>=2, single-eval semantics for n_valid==1,
   fail-closed for n_valid==0.
5. Non-COMPLEX path unchanged (AC1, AC3, AC15 guard this).

All tests MUST FAIL until GREEN implements the contract.
Do NOT implement any production change here — RED-only file (021D8FAE).
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).parent
ENGINE_ROOT = HERE.parent
sys.path.insert(0, str(ENGINE_ROOT))
sys.path.insert(0, str(ENGINE_ROOT / "lib"))
sys.path.insert(0, str(ENGINE_ROOT / "workflows"))

import phase_6_review as p6  # noqa: E402  (needed for monkeypatching module-level names)
from contracts import StepResult, WorkflowContext  # noqa: E402
from phase_6_review import (  # noqa: E402
    _invoke_satisfaction_llm,
    _write_satisfaction_doc,
    SatisfactionVerdict,  # re-exported from plugins.disk_truth via phase_6_review imports
)


# ─── raw LLM response builders ────────────────────────────────────────────────

def _structured_block(satisfied: bool, fixes: list | None = None) -> str:
    if fixes is None:
        fixes = []
    fix_str = ", ".join(
        f'{{"file": "{f["file"]}", "issue": "{f["issue"]}"}}'
        for f in fixes
    )
    return (
        "## satisfaction-output (structured)\n"
        "```json\n"
        f'{{"satisfied": {"true" if satisfied else "false"}, "fixes_required": [{fix_str}]}}\n'
        "```"
    )


def _raw_pass(score: int, satisfied: bool = True, fixes: list | None = None) -> str:
    return "\n".join([
        "## Evaluation",
        f"SCORE: {score}",
        "VERDICT: PASS",
        "",
        _structured_block(satisfied, fixes),
    ])


def _raw_fail(score: int, satisfied: bool = False, fixes: list | None = None) -> str:
    if fixes is None:
        fixes = [{"file": "x.py", "issue": "missing test"}]
    return "\n".join([
        "## Evaluation",
        f"SCORE: {score}",
        "VERDICT: FAIL",
        "",
        _structured_block(satisfied, fixes),
    ])


def _raw_no_score_no_structured() -> str:
    return "## Evaluation\nThis is a prose response with no score and no structured block."


# ─── fixture builders ─────────────────────────────────────────────────────────

def _make_ctx(tmp_path: Path, satisfaction_threshold: int = 80, complexity: str | None = None) -> WorkflowContext:
    scratch = tmp_path / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    cfg: dict = {
        "scratchpad_dir": str(scratch),
        "satisfaction_threshold": satisfaction_threshold,
    }
    if complexity is not None:
        cfg["complexity"] = complexity
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=cfg,
        question="add feature X",
        session_id="test-021D8FAE",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_invoke_prev(tmp_path: Path) -> StepResult:
    """A prev StepResult for _invoke_satisfaction_llm (output of build_satisfaction_prompt)."""
    scratch = tmp_path / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    reviews_dir = scratch / "reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)
    sat_doc = reviews_dir / "build-satisfaction.md"
    review_doc = reviews_dir / "build-review.md"
    review_doc.write_text("# Review\nVERDICT: FAIL\n", encoding="utf-8")
    return StepResult(
        status="ok",
        data={
            "prompt": "Please evaluate: spec says X, review says Y.",
            "doc_path": str(sat_doc),
            "spec_path": str(scratch / "specs" / "build-spec.md"),
            "review_doc_path": str(review_doc),
            "fix_doc_path": str(reviews_dir / "build-fix.md"),
        },
        duration_ms=0,
        step_name="build_satisfaction_prompt",
    )


def _make_write_prev_single(
    tmp_path: Path,
    raw_response: str,
) -> StepResult:
    """Construct a prev StepResult that _write_satisfaction_doc expects for single-evaluator path."""
    scratch = tmp_path / "scratch"
    reviews_dir = scratch / "reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)
    sat_doc = reviews_dir / "build-satisfaction.md"
    review_doc = reviews_dir / "build-review.md"
    if not review_doc.exists():
        review_doc.write_text("# Review\nVERDICT: FAIL\n", encoding="utf-8")
    return StepResult(
        status="ok",
        data={
            "raw_response": raw_response,
            "doc_path": str(sat_doc),
            "spec_path": str(scratch / "specs" / "build-spec.md"),
            "review_doc_path": str(review_doc),
            "fix_doc_path": str(reviews_dir / "build-fix.md"),
        },
        duration_ms=100,
        step_name="invoke_satisfaction_llm",
    )


def _make_write_prev_multi(
    tmp_path: Path,
    evaluator_responses: list[dict],
    first_ok_raw: str = "",
) -> StepResult:
    """Construct a prev StepResult for _write_satisfaction_doc multi-evaluator path."""
    scratch = tmp_path / "scratch"
    reviews_dir = scratch / "reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)
    sat_doc = reviews_dir / "build-satisfaction.md"
    review_doc = reviews_dir / "build-review.md"
    if not review_doc.exists():
        review_doc.write_text("# Review\nVERDICT: FAIL\n", encoding="utf-8")
    ok_raws = [e["raw_response"] for e in evaluator_responses if e.get("status") == "ok" and e.get("raw_response")]
    raw_response = ok_raws[0] if ok_raws else first_ok_raw
    return StepResult(
        status="ok",
        data={
            "raw_response": raw_response,
            "is_multi_evaluator": True,
            "evaluator_responses": evaluator_responses,
            "doc_path": str(sat_doc),
            "spec_path": str(scratch / "specs" / "build-spec.md"),
            "review_doc_path": str(review_doc),
            "fix_doc_path": str(reviews_dir / "build-fix.md"),
        },
        duration_ms=0,
        step_name="invoke_satisfaction_llm",
    )


def _capture_emit(monkeypatch) -> list[tuple]:
    """Capture _emit_safe calls; returns list of (event_type, payload, kwargs)."""
    captured: list[tuple] = []
    monkeypatch.setattr(
        p6,
        "_emit_safe",
        lambda et, payload, **kw: captured.append((et, payload, kw)),
    )
    return captured


def _events_of_type(captured: list[tuple], event_type: str) -> list[dict]:
    return [p for et, p, _ in captured if et == event_type]


# ═══════════════════════════════════════════════════════════════════════════════
# AC1 — non-COMPLEX: single invoke_llm_subprocess call, no multi-eval keys
# ═══════════════════════════════════════════════════════════════════════════════

def test_invoke_satisfaction_llm_non_complex_calls_subprocess_once(
    tmp_path: Path, monkeypatch
) -> None:
    """AC1: org_config has no complexity → exactly 1 invoke_llm_subprocess call;
    returned StepResult has no is_multi_evaluator / evaluator_responses keys.

    FAILS until GREEN: current code is single-call-only with no branching.
    This test will PASS against current code (it guards the non-COMPLEX unchanged path).
    """
    calls: list = []

    def _stub(*args, **kwargs) -> StepResult:
        calls.append(kwargs)
        return StepResult(
            status="ok",
            data={
                "raw_response": _raw_pass(90),
                "doc_path": kwargs.get("extra_data", {}).get("doc_path", ""),
                "spec_path": kwargs.get("extra_data", {}).get("spec_path", ""),
                "review_doc_path": kwargs.get("extra_data", {}).get("review_doc_path", ""),
                "fix_doc_path": kwargs.get("extra_data", {}).get("fix_doc_path", ""),
            },
            duration_ms=0,
            step_name="invoke_satisfaction_llm",
        )

    monkeypatch.setattr(p6, "invoke_llm_subprocess", _stub)

    ctx = _make_ctx(tmp_path)  # no complexity key
    prev = _make_invoke_prev(tmp_path)
    result = _invoke_satisfaction_llm(ctx, prev)

    assert len(calls) == 1, (
        f"AC1 FAIL: expected exactly 1 invoke_llm_subprocess call for non-COMPLEX; "
        f"got {len(calls)} — 021D8FAE"
    )
    assert result.status == "ok", f"AC1: expected ok; got {result.status!r}"
    assert "is_multi_evaluator" not in (result.data or {}), (
        "AC1 FAIL: is_multi_evaluator must NOT be present in non-COMPLEX result.data"
    )
    assert "evaluator_responses" not in (result.data or {}), (
        "AC1 FAIL: evaluator_responses must NOT be present in non-COMPLEX result.data"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC2 — COMPLEX: 3 concurrent invoke_llm_subprocess calls, multi-eval keys present
# ═══════════════════════════════════════════════════════════════════════════════

def test_invoke_satisfaction_llm_complex_calls_subprocess_three_times(
    tmp_path: Path, monkeypatch
) -> None:
    """AC2: complexity == "COMPLEX" → exactly 3 invoke_llm_subprocess calls;
    returns status="ok" with is_multi_evaluator=True, evaluator_responses len 3 in index order.

    FAILS until GREEN: _invoke_satisfaction_llm has no COMPLEX branch today.
    """
    calls: list = []
    raw = _raw_pass(90)

    def _stub(*args, **kwargs) -> StepResult:
        calls.append(kwargs)
        return StepResult(
            status="ok",
            data={
                "raw_response": raw,
                "doc_path": str(tmp_path / "sat.md"),
                "spec_path": "",
                "review_doc_path": "",
                "fix_doc_path": "",
            },
            duration_ms=0,
            step_name="invoke_satisfaction_llm",
        )

    monkeypatch.setattr(p6, "invoke_llm_subprocess", _stub)

    ctx = _make_ctx(tmp_path, complexity="COMPLEX")
    prev = _make_invoke_prev(tmp_path)
    result = _invoke_satisfaction_llm(ctx, prev)

    assert len(calls) == 3, (
        f"AC2 FAIL: expected exactly 3 invoke_llm_subprocess calls for COMPLEX; "
        f"got {len(calls)} — 021D8FAE _run_satisfaction_evaluators_parallel not implemented"
    )
    assert result.status == "ok", (
        f"AC2 FAIL: expected status='ok' when all 3 evaluators ok; got {result.status!r}"
    )
    assert (result.data or {}).get("is_multi_evaluator") is True, (
        "AC2 FAIL: is_multi_evaluator must be True in COMPLEX result.data"
    )
    evaluator_responses = (result.data or {}).get("evaluator_responses")
    assert isinstance(evaluator_responses, list), (
        f"AC2 FAIL: evaluator_responses must be a list; got {type(evaluator_responses)!r}"
    )
    assert len(evaluator_responses) == 3, (
        f"AC2 FAIL: evaluator_responses must have 3 entries; got {len(evaluator_responses)}"
    )
    indices = [e["index"] for e in evaluator_responses]
    assert indices == [0, 1, 2], (
        f"AC2 FAIL: evaluator_responses must be in index order [0,1,2]; got {indices}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC13 — COMPLEX, all 3 subprocesses error → return first error StepResult as-is
# ═══════════════════════════════════════════════════════════════════════════════

def test_invoke_satisfaction_llm_complex_all_errors_returns_first_error(
    tmp_path: Path, monkeypatch
) -> None:
    """AC13: COMPLEX, all 3 invoke_llm_subprocess return status="error" →
    _invoke_satisfaction_llm returns the first error StepResult;
    error_code is the LLM error (E_LLM_TIMEOUT), NOT E_SATISFACTION_BELOW_THRESHOLD.

    FAILS until GREEN: no COMPLEX branch today.
    """
    call_count = 0

    def _stub_error(*args, **kwargs) -> StepResult:
        nonlocal call_count
        call_count += 1
        return StepResult(
            status="error",
            data=None,
            duration_ms=0,
            step_name="invoke_satisfaction_llm",
            error="LLM timed out",
            error_code="E_LLM_TIMEOUT",
        )

    monkeypatch.setattr(p6, "invoke_llm_subprocess", _stub_error)

    ctx = _make_ctx(tmp_path, complexity="COMPLEX")
    prev = _make_invoke_prev(tmp_path)
    result = _invoke_satisfaction_llm(ctx, prev)

    assert call_count == 3, (
        f"AC13 FAIL: expected 3 evaluator calls even when all error; got {call_count}"
    )
    assert result.status == "error", (
        f"AC13 FAIL: expected status='error' when all 3 subprocess errors; got {result.status!r}"
    )
    assert result.error_code == "E_LLM_TIMEOUT", (
        f"AC13 FAIL: expected error_code='E_LLM_TIMEOUT' (LLM error, not gate error); "
        f"got {result.error_code!r} — 021D8FAE"
    )
    assert result.error_code != "E_SATISFACTION_BELOW_THRESHOLD", (
        "AC13 FAIL: must NOT return E_SATISFACTION_BELOW_THRESHOLD when subprocess errors — "
        "_write_satisfaction_doc was never reached"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC3 — non-COMPLEX _write_satisfaction_doc: no multi-eval telemetry emitted
# ═══════════════════════════════════════════════════════════════════════════════

def test_write_satisfaction_doc_non_complex_no_multi_evaluator_telemetry(
    tmp_path: Path, monkeypatch
) -> None:
    """AC3: non-COMPLEX prev (single raw_response, no is_multi_evaluator) → gate PASS,
    no satisfaction_multi_evaluator or satisfaction_multi_evaluator_degraded events emitted.

    Partly guards pre-existing behaviour (PASS path) and also guards no telemetry leak.
    The telemetry assertion is the new part — FAILS until GREEN if multi path accidentally
    emits these events for single-eval input.
    """
    captured = _capture_emit(monkeypatch)
    ctx = _make_ctx(tmp_path, satisfaction_threshold=80)
    raw = _raw_pass(90, satisfied=True)
    prev = _make_write_prev_single(tmp_path, raw)

    result = _write_satisfaction_doc(ctx, prev)

    assert result.status == "ok", (
        f"AC3 FAIL: expected status='ok' for single valid PASS; got {result.status!r}"
    )
    assert result.data is not None
    assert result.data.get("score") == 90, (
        f"AC3 FAIL: expected score=90; got {result.data.get('score')!r}"
    )
    multi_events = _events_of_type(captured, "satisfaction_multi_evaluator")
    degraded_events = _events_of_type(captured, "satisfaction_multi_evaluator_degraded")
    assert len(multi_events) == 0, (
        f"AC3 FAIL: no satisfaction_multi_evaluator event expected for non-COMPLEX; "
        f"got {multi_events} — 021D8FAE"
    )
    assert len(degraded_events) == 0, (
        f"AC3 FAIL: no satisfaction_multi_evaluator_degraded event expected for non-COMPLEX; "
        f"got {degraded_events}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC4 — COMPLEX, 3 valid all PASS (95/92/88): median=92, unanimous, event emitted, doc has sections
# ═══════════════════════════════════════════════════════════════════════════════

def test_write_satisfaction_doc_complex_three_pass_unanimous(
    tmp_path: Path, monkeypatch
) -> None:
    """AC4: COMPLEX, evaluators 0/1/2 all PASS with scores 95/92/88 (structured satisfied=True)
    → gate PASS; data["score"]==92 (median); agreement=="unanimous";
    satisfaction_multi_evaluator emitted with median_score=92, majority_passed=True, n_valid=3;
    written doc contains ## Aggregation Summary + ## Evaluator 1 + ## Evaluator 2 + ## Evaluator 3.

    FAILS until GREEN: _write_satisfaction_doc has no multi-evaluator branch.
    """
    captured = _capture_emit(monkeypatch)
    ctx = _make_ctx(tmp_path, satisfaction_threshold=80)

    evaluator_responses = [
        {"index": 0, "status": "ok", "raw_response": _raw_pass(95), "error_code": None},
        {"index": 1, "status": "ok", "raw_response": _raw_pass(92), "error_code": None},
        {"index": 2, "status": "ok", "raw_response": _raw_pass(88), "error_code": None},
    ]
    prev = _make_write_prev_multi(tmp_path, evaluator_responses)
    result = _write_satisfaction_doc(ctx, prev)

    assert result.status == "ok", (
        f"AC4 FAIL: expected gate PASS for unanimous 3-PASS; got {result.status!r}, "
        f"error={getattr(result, 'error', None)!r} — 021D8FAE"
    )
    assert (result.data or {}).get("score") == 92, (
        f"AC4 FAIL: expected median score=92 for [95,92,88]; got {(result.data or {}).get('score')!r}"
    )
    multi_ev = (result.data or {}).get("multi_evaluator", {})
    assert multi_ev.get("agreement") == "unanimous", (
        f"AC4 FAIL: expected agreement='unanimous'; got {multi_ev.get('agreement')!r}"
    )
    # telemetry
    events = _events_of_type(captured, "satisfaction_multi_evaluator")
    assert len(events) == 1, (
        f"AC4 FAIL: expected 1 satisfaction_multi_evaluator event; got {len(events)}"
    )
    ev = events[0]
    assert ev.get("median_score") == 92, (
        f"AC4 FAIL: telemetry median_score expected 92; got {ev.get('median_score')!r}"
    )
    assert ev.get("majority_passed") is True, (
        f"AC4 FAIL: telemetry majority_passed expected True; got {ev.get('majority_passed')!r}"
    )
    assert ev.get("n_valid") == 3, (
        f"AC4 FAIL: telemetry n_valid expected 3; got {ev.get('n_valid')!r}"
    )
    # doc content
    sat_doc = Path(prev.data["doc_path"])
    assert sat_doc.exists(), "AC4 FAIL: doc_path file not written"
    content = sat_doc.read_text(encoding="utf-8")
    assert "## Aggregation Summary" in content, (
        "AC4 FAIL: doc must contain '## Aggregation Summary'"
    )
    for i in (1, 2, 3):
        assert f"## Evaluator {i}" in content, (
            f"AC4 FAIL: doc must contain '## Evaluator {i}'"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# AC5 — COMPLEX, 2 PASS 1 FAIL: gate PASS, score=88 (median of 92/88/79), agreement="2-1"
# ═══════════════════════════════════════════════════════════════════════════════

def test_write_satisfaction_doc_complex_two_pass_one_fail(
    tmp_path: Path, monkeypatch
) -> None:
    """AC5: COMPLEX, scores 92 PASS / 88 PASS / 79 FAIL → gate PASS (majority);
    data["score"]==88 (median of 92/88/79); agreement=="2-1".

    FAILS until GREEN.
    """
    captured = _capture_emit(monkeypatch)
    ctx = _make_ctx(tmp_path, satisfaction_threshold=80)

    evaluator_responses = [
        {"index": 0, "status": "ok", "raw_response": _raw_pass(92), "error_code": None},
        {"index": 1, "status": "ok", "raw_response": _raw_pass(88), "error_code": None},
        {"index": 2, "status": "ok", "raw_response": _raw_fail(79), "error_code": None},
    ]
    prev = _make_write_prev_multi(tmp_path, evaluator_responses)
    result = _write_satisfaction_doc(ctx, prev)

    assert result.status == "ok", (
        f"AC5 FAIL: expected gate PASS (majority 2/3); got {result.status!r} — 021D8FAE"
    )
    assert (result.data or {}).get("score") == 88, (
        f"AC5 FAIL: expected median score=88 (median of 92,88,79); "
        f"got {(result.data or {}).get('score')!r}"
    )
    multi_ev = (result.data or {}).get("multi_evaluator", {})
    assert multi_ev.get("agreement") == "2-1", (
        f"AC5 FAIL: expected agreement='2-1'; got {multi_ev.get('agreement')!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC6 — COMPLEX, 1 PASS 2 FAIL: gate FAIL, score=70 (median of 92/70/65), agreement="2-1"
# ═══════════════════════════════════════════════════════════════════════════════

def test_write_satisfaction_doc_complex_one_pass_two_fail(
    tmp_path: Path, monkeypatch
) -> None:
    """AC6: COMPLEX, scores 92 PASS / 70 FAIL / 65 FAIL (threshold 80) →
    gate FAIL E_SATISFACTION_BELOW_THRESHOLD, recoverable=False;
    agreement=="2-1"; data["score"]==70 (median of 92/70/65).

    FAILS until GREEN.
    """
    captured = _capture_emit(monkeypatch)
    ctx = _make_ctx(tmp_path, satisfaction_threshold=80)

    evaluator_responses = [
        {"index": 0, "status": "ok", "raw_response": _raw_pass(92), "error_code": None},
        {"index": 1, "status": "ok", "raw_response": _raw_fail(70), "error_code": None},
        {"index": 2, "status": "ok", "raw_response": _raw_fail(65), "error_code": None},
    ]
    prev = _make_write_prev_multi(tmp_path, evaluator_responses)
    result = _write_satisfaction_doc(ctx, prev)

    assert result.status == "error", (
        f"AC6 FAIL: expected gate FAIL (minority 1/3); got {result.status!r} — 021D8FAE"
    )
    assert result.error_code == "E_SATISFACTION_BELOW_THRESHOLD", (
        f"AC6 FAIL: expected E_SATISFACTION_BELOW_THRESHOLD; got {result.error_code!r}"
    )
    assert getattr(result, "recoverable", True) is False, (
        "AC6 FAIL: recoverable must be False"
    )
    assert (result.data or {}).get("score") == 70, (
        f"AC6 FAIL: expected median score=70 (median of 92,70,65); "
        f"got {(result.data or {}).get('score')!r}"
    )
    multi_ev = (result.data or {}).get("multi_evaluator", {})
    assert multi_ev.get("agreement") == "2-1", (
        f"AC6 FAIL: expected agreement='2-1'; got {multi_ev.get('agreement')!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC7 — COMPLEX, all 3 FAIL: gate FAIL, agreement="unanimous"
# ═══════════════════════════════════════════════════════════════════════════════

def test_write_satisfaction_doc_complex_all_fail_unanimous(
    tmp_path: Path, monkeypatch
) -> None:
    """AC7: COMPLEX, all 3 valid FAIL (scores 60/55/40) →
    gate FAIL E_SATISFACTION_BELOW_THRESHOLD; agreement=="unanimous".

    FAILS until GREEN.
    """
    _capture_emit(monkeypatch)
    ctx = _make_ctx(tmp_path, satisfaction_threshold=80)

    evaluator_responses = [
        {"index": 0, "status": "ok", "raw_response": _raw_fail(60), "error_code": None},
        {"index": 1, "status": "ok", "raw_response": _raw_fail(55), "error_code": None},
        {"index": 2, "status": "ok", "raw_response": _raw_fail(40), "error_code": None},
    ]
    prev = _make_write_prev_multi(tmp_path, evaluator_responses)
    result = _write_satisfaction_doc(ctx, prev)

    assert result.status == "error", (
        f"AC7 FAIL: expected gate FAIL (all 3 fail); got {result.status!r} — 021D8FAE"
    )
    assert result.error_code == "E_SATISFACTION_BELOW_THRESHOLD", (
        f"AC7 FAIL: expected E_SATISFACTION_BELOW_THRESHOLD; got {result.error_code!r}"
    )
    multi_ev = (result.data or {}).get("multi_evaluator", {})
    assert multi_ev.get("agreement") == "unanimous", (
        f"AC7 FAIL: expected agreement='unanimous'; got {multi_ev.get('agreement')!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC8 — COMPLEX, evaluator 0 errored, 1&2 valid+PASS: gate PASS, n_valid=2
# ═══════════════════════════════════════════════════════════════════════════════

def test_write_satisfaction_doc_complex_one_error_two_pass(
    tmp_path: Path, monkeypatch
) -> None:
    """AC8: COMPLEX, evaluator 0 status="error", 1&2 valid+PASS →
    gate PASS over 2 survivors; agreement=="unanimous";
    satisfaction_multi_evaluator emitted with n_attempted=3, n_valid=2;
    doc table shows evaluator 1 errored + ## Evaluator 1 — ERRORED section.

    FAILS until GREEN.
    """
    captured = _capture_emit(monkeypatch)
    ctx = _make_ctx(tmp_path, satisfaction_threshold=80)

    evaluator_responses = [
        {"index": 0, "status": "error", "raw_response": "", "error_code": "E_LLM_TIMEOUT"},
        {"index": 1, "status": "ok", "raw_response": _raw_pass(90), "error_code": None},
        {"index": 2, "status": "ok", "raw_response": _raw_pass(85), "error_code": None},
    ]
    prev = _make_write_prev_multi(tmp_path, evaluator_responses)
    result = _write_satisfaction_doc(ctx, prev)

    assert result.status == "ok", (
        f"AC8 FAIL: expected gate PASS (2/2 valid ok); got {result.status!r} — 021D8FAE"
    )
    multi_ev = (result.data or {}).get("multi_evaluator", {})
    assert multi_ev.get("agreement") == "unanimous", (
        f"AC8 FAIL: expected agreement='unanimous' for 2/2 valid both PASS; "
        f"got {multi_ev.get('agreement')!r}"
    )
    events = _events_of_type(captured, "satisfaction_multi_evaluator")
    assert len(events) == 1, f"AC8 FAIL: expected 1 satisfaction_multi_evaluator event"
    ev = events[0]
    assert ev.get("n_attempted") == 3, (
        f"AC8 FAIL: n_attempted expected 3; got {ev.get('n_attempted')!r}"
    )
    assert ev.get("n_valid") == 2, (
        f"AC8 FAIL: n_valid expected 2; got {ev.get('n_valid')!r}"
    )
    sat_doc = Path(prev.data["doc_path"])
    if sat_doc.exists():
        content = sat_doc.read_text(encoding="utf-8")
        assert "ERRORED" in content.upper(), (
            "AC8 FAIL: doc must mention ERRORED for evaluator 0"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# AC9 — COMPLEX, evaluators 0&1 error, evaluator 2 valid+PASS: degraded PASS
# ═══════════════════════════════════════════════════════════════════════════════

def test_write_satisfaction_doc_complex_two_error_one_pass_degraded(
    tmp_path: Path, monkeypatch
) -> None:
    """AC9: COMPLEX, evaluators 0&1 error, evaluator 2 valid+PASS (score >= threshold) →
    gate PASS (single-eval semantics on survivor);
    satisfaction_multi_evaluator_degraded emitted with reason="only_one_valid_evaluator".

    FAILS until GREEN.
    """
    captured = _capture_emit(monkeypatch)
    ctx = _make_ctx(tmp_path, satisfaction_threshold=80)

    evaluator_responses = [
        {"index": 0, "status": "error", "raw_response": "", "error_code": "E_LLM_TIMEOUT"},
        {"index": 1, "status": "error", "raw_response": "", "error_code": "E_LLM_TIMEOUT"},
        {"index": 2, "status": "ok", "raw_response": _raw_pass(90), "error_code": None},
    ]
    prev = _make_write_prev_multi(tmp_path, evaluator_responses)
    result = _write_satisfaction_doc(ctx, prev)

    assert result.status == "error", (
        f"AC9 FAIL: expected status='error' (F81D5EF7 degrade-fail-loud); "
        f"got {result.status!r} — pre-GREEN single-eval semantics allowed ok here"
    )
    assert result.error_code == "E_REVIEW_DEGRADED", (
        f"AC9 FAIL: expected error_code='E_REVIEW_DEGRADED' (F81D5EF7); "
        f"got {result.error_code!r}"
    )
    degraded_events = _events_of_type(captured, "satisfaction_multi_evaluator_degraded")
    assert len(degraded_events) == 1, (
        f"AC9 FAIL: expected 1 satisfaction_multi_evaluator_degraded event; "
        f"got {len(degraded_events)}"
    )
    assert degraded_events[0].get("reason") == "only_one_valid_evaluator", (
        f"AC9 FAIL: degraded reason expected 'only_one_valid_evaluator'; "
        f"got {degraded_events[0].get('reason')!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC10 — COMPLEX, evaluators 0&1 error, evaluator 2 valid+FAIL: degraded FAIL
# ═══════════════════════════════════════════════════════════════════════════════

def test_write_satisfaction_doc_complex_two_error_one_fail_degraded(
    tmp_path: Path, monkeypatch
) -> None:
    """AC10: COMPLEX, evaluators 0&1 error, evaluator 2 valid but score 50 < threshold 80 →
    gate FAIL E_SATISFACTION_BELOW_THRESHOLD (single-eval semantics);
    satisfaction_multi_evaluator_degraded emitted with reason="only_one_valid_evaluator".

    FAILS until GREEN.
    """
    captured = _capture_emit(monkeypatch)
    ctx = _make_ctx(tmp_path, satisfaction_threshold=80)

    evaluator_responses = [
        {"index": 0, "status": "error", "raw_response": "", "error_code": "E_LLM_TIMEOUT"},
        {"index": 1, "status": "error", "raw_response": "", "error_code": "E_LLM_TIMEOUT"},
        {"index": 2, "status": "ok", "raw_response": _raw_fail(50), "error_code": None},
    ]
    prev = _make_write_prev_multi(tmp_path, evaluator_responses)
    result = _write_satisfaction_doc(ctx, prev)

    assert result.status == "error", (
        f"AC10 FAIL: expected gate FAIL (single-eval survivor score 50 < 80); "
        f"got {result.status!r} — 021D8FAE"
    )
    assert result.error_code == "E_REVIEW_DEGRADED", (
        f"AC10 FAIL: expected E_REVIEW_DEGRADED; got {result.error_code!r}"
    )
    degraded_events = _events_of_type(captured, "satisfaction_multi_evaluator_degraded")
    assert len(degraded_events) == 1, (
        f"AC10 FAIL: expected 1 degraded event; got {len(degraded_events)}"
    )
    assert degraded_events[0].get("reason") == "only_one_valid_evaluator", (
        f"AC10 FAIL: degraded reason expected 'only_one_valid_evaluator'; "
        f"got {degraded_events[0].get('reason')!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC11 — COMPLEX, all 3 ok but no SCORE and no structured: n_valid=0, FAIL, degraded
# ═══════════════════════════════════════════════════════════════════════════════

def test_write_satisfaction_doc_complex_no_valid_signal_fail_closed(
    tmp_path: Path, monkeypatch
) -> None:
    """AC11: COMPLEX, all 3 status="ok" but raw is plain prose (no SCORE, no structured) →
    n_valid==0 → gate FAIL E_SATISFACTION_BELOW_THRESHOLD;
    satisfaction_multi_evaluator_degraded reason="no_valid_evaluators"; data["score"] is None.

    FAILS until GREEN.
    """
    captured = _capture_emit(monkeypatch)
    ctx = _make_ctx(tmp_path, satisfaction_threshold=80)

    prose = _raw_no_score_no_structured()
    evaluator_responses = [
        {"index": 0, "status": "ok", "raw_response": prose, "error_code": None},
        {"index": 1, "status": "ok", "raw_response": prose, "error_code": None},
        {"index": 2, "status": "ok", "raw_response": prose, "error_code": None},
    ]
    prev = _make_write_prev_multi(tmp_path, evaluator_responses, first_ok_raw=prose)
    result = _write_satisfaction_doc(ctx, prev)

    assert result.status == "error", (
        f"AC11 FAIL: expected gate FAIL (n_valid=0, fail-closed); "
        f"got {result.status!r} — 021D8FAE"
    )
    assert result.error_code == "E_REVIEW_DEGRADED", (
        f"AC11 FAIL: expected E_REVIEW_DEGRADED; got {result.error_code!r}"
    )
    assert (result.data or {}).get("score") is None, (
        f"AC11 FAIL: expected data['score'] is None; got {(result.data or {}).get('score')!r}"
    )
    degraded_events = _events_of_type(captured, "satisfaction_multi_evaluator_degraded")
    assert len(degraded_events) == 1, (
        f"AC11 FAIL: expected 1 degraded event; got {len(degraded_events)}"
    )
    assert degraded_events[0].get("reason") == "no_valid_evaluators", (
        f"AC11 FAIL: degraded reason expected 'no_valid_evaluators'; "
        f"got {degraded_events[0].get('reason')!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC12 — COMPLEX, evaluator 0 errored, 1 PASS 2 FAIL (split 1-1): FAIL, agreement="split"
# ═══════════════════════════════════════════════════════════════════════════════

def test_write_satisfaction_doc_complex_split_one_error_one_pass_one_fail(
    tmp_path: Path, monkeypatch
) -> None:
    """AC12: COMPLEX, evaluator 0 errored, evaluator 1 PASS, evaluator 2 FAIL →
    n_valid==2, passed_count==1 → majority_passed=False → gate FAIL E_SATISFACTION_BELOW_THRESHOLD;
    agreement=="split".

    FAILS until GREEN.
    """
    _capture_emit(monkeypatch)
    ctx = _make_ctx(tmp_path, satisfaction_threshold=80)

    evaluator_responses = [
        {"index": 0, "status": "error", "raw_response": "", "error_code": "E_LLM_TIMEOUT"},
        {"index": 1, "status": "ok", "raw_response": _raw_pass(90), "error_code": None},
        {"index": 2, "status": "ok", "raw_response": _raw_fail(65), "error_code": None},
    ]
    prev = _make_write_prev_multi(tmp_path, evaluator_responses)
    result = _write_satisfaction_doc(ctx, prev)

    assert result.status == "error", (
        f"AC12 FAIL: expected gate FAIL (1-1 split → fail-closed); "
        f"got {result.status!r} — 021D8FAE"
    )
    assert result.error_code == "E_SATISFACTION_BELOW_THRESHOLD", (
        f"AC12 FAIL: expected E_SATISFACTION_BELOW_THRESHOLD; got {result.error_code!r}"
    )
    multi_ev = (result.data or {}).get("multi_evaluator", {})
    assert multi_ev.get("agreement") == "split", (
        f"AC12 FAIL: expected agreement='split'; got {multi_ev.get('agreement')!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC14 — _aggregate_satisfaction: pure helper, called directly
# ═══════════════════════════════════════════════════════════════════════════════

def test_aggregate_satisfaction_mixed_structured_and_score_only(
    tmp_path: Path,
) -> None:
    """AC14 case 1 (rewritten for 9702B73F strict-AND policy):
    Input: [{index:0, score:90, structured:None}, {index:1, score:None, structured:satisfied=True},
            {index:2, score:70, structured:None}], threshold=80.
    Under strict-AND: evaluator 1 (score=None, struct=T) → structured_only_no_score → passed=False.
    eff = [90, 70] (score=None excluded); median=80 (int(round((90+70)/2)=80));
    passed_count=1 (only evaluator 0: score=90>=80, no struct → concurring_pass);
    majority_passed=False (1*2=2 ≤ 3); agreement="2-1"; degraded=False; n_valid=3.
    """
    _aggregate_satisfaction = getattr(p6, "_aggregate_satisfaction", None)
    assert _aggregate_satisfaction is not None, (
        "AC14 FAIL: _aggregate_satisfaction not found in phase_6_review — not implemented yet (021D8FAE)"
    )

    sv = SatisfactionVerdict(satisfied=True, fixes_required=[])
    evals = [
        {"index": 0, "score": 90, "structured": None, "status": "ok", "error_code": None},
        {"index": 1, "score": None, "structured": sv, "status": "ok", "error_code": None},
        {"index": 2, "score": 70, "structured": None, "status": "ok", "error_code": None},
    ]
    result = _aggregate_satisfaction(evals, threshold=80)

    assert result["n_valid"] == 3, f"AC14 FAIL: n_valid expected 3; got {result['n_valid']}"
    assert result["median_score"] == 80, (
        f"AC14 FAIL: eff=[90,70] (score=None excluded) → median=80; got {result['median_score']}"
    )
    assert result["passed_count"] == 1, (
        f"AC14 FAIL: passed_count expected 1 (only evaluator 0 passes); got {result['passed_count']}"
    )
    assert result["majority_passed"] is False, (
        f"AC14 FAIL: majority_passed expected False (1*2=2 ≤ 3); got {result['majority_passed']!r}"
    )
    assert result["agreement"] == "2-1", (
        f"AC14 FAIL: agreement expected '2-1'; got {result['agreement']!r}"
    )
    assert result["degraded"] is False, (
        f"AC14 FAIL: degraded expected False (n_valid=3>=2); got {result['degraded']!r}"
    )


def test_aggregate_satisfaction_two_errors_one_valid_degraded(tmp_path: Path) -> None:
    """AC14 case 2: two entries errored + one valid → n_valid=1, agreement="single", degraded=True.

    FAILS until GREEN: _aggregate_satisfaction does not exist.
    """
    _aggregate_satisfaction = getattr(p6, "_aggregate_satisfaction", None)
    assert _aggregate_satisfaction is not None, (
        "AC14 FAIL: _aggregate_satisfaction not found — not implemented yet (021D8FAE)"
    )

    evals = [
        {"index": 0, "score": None, "structured": None, "status": "error", "error_code": "E_LLM_TIMEOUT"},
        {"index": 1, "score": None, "structured": None, "status": "error", "error_code": "E_LLM_TIMEOUT"},
        {"index": 2, "score": 85, "structured": None, "status": "ok", "error_code": None},
    ]
    result = _aggregate_satisfaction(evals, threshold=80)

    assert result["n_valid"] == 1, (
        f"AC14 FAIL: n_valid expected 1; got {result['n_valid']}"
    )
    assert result["agreement"] == "single", (
        f"AC14 FAIL: agreement expected 'single'; got {result['agreement']!r}"
    )
    assert result["degraded"] is True, (
        f"AC14 FAIL: degraded expected True (n_valid=1<2); got {result['degraded']!r}"
    )


def test_aggregate_satisfaction_two_valid_both_pass_unanimous(tmp_path: Path) -> None:
    """AC14 case 3: two valid scores 85/90, both pass → median=88 (round((85+90)/2)=88);
    agreement="unanimous".

    FAILS until GREEN.
    """
    _aggregate_satisfaction = getattr(p6, "_aggregate_satisfaction", None)
    assert _aggregate_satisfaction is not None, (
        "AC14 FAIL: _aggregate_satisfaction not found — not implemented yet (021D8FAE)"
    )

    evals = [
        {"index": 0, "score": 85, "structured": None, "status": "ok", "error_code": None},
        {"index": 1, "score": 90, "structured": None, "status": "ok", "error_code": None},
    ]
    result = _aggregate_satisfaction(evals, threshold=80)

    assert result["median_score"] == 88, (
        f"AC14 FAIL: median of [85,90] expected 88 (int(round(87.5))); "
        f"got {result['median_score']!r}"
    )
    assert result["agreement"] == "unanimous", (
        f"AC14 FAIL: agreement expected 'unanimous' (both pass); got {result['agreement']!r}"
    )
    assert result["majority_passed"] is True, (
        f"AC14 FAIL: majority_passed expected True; got {result['majority_passed']!r}"
    )


def test_aggregate_satisfaction_one_one_split(tmp_path: Path) -> None:
    """AC14 case 4: 2 valid, 1 PASS (95) / 1 FAIL (70), threshold=80 →
    passed_count=1; 2*1>2 is False → majority_passed=False; agreement="split".

    FAILS until GREEN.
    """
    _aggregate_satisfaction = getattr(p6, "_aggregate_satisfaction", None)
    assert _aggregate_satisfaction is not None, (
        "AC14 FAIL: _aggregate_satisfaction not found — not implemented yet (021D8FAE)"
    )

    evals = [
        {"index": 0, "score": 95, "structured": None, "status": "ok", "error_code": None},
        {"index": 1, "score": 70, "structured": None, "status": "ok", "error_code": None},
    ]
    result = _aggregate_satisfaction(evals, threshold=80)

    assert result["passed_count"] == 1, (
        f"AC14 FAIL: passed_count expected 1; got {result['passed_count']}"
    )
    assert result["majority_passed"] is False, (
        f"AC14 FAIL: majority_passed expected False (1-1 tie, fail-closed); "
        f"got {result['majority_passed']!r}"
    )
    assert result["agreement"] == "split", (
        f"AC14 FAIL: agreement expected 'split'; got {result['agreement']!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC15 — non-COMPLEX path does NOT emit complexity_default_used from satisfaction code
# ═══════════════════════════════════════════════════════════════════════════════

def test_invoke_satisfaction_llm_non_complex_no_complexity_default_used_event(
    tmp_path: Path, monkeypatch
) -> None:
    """AC15: _invoke_satisfaction_llm for non-COMPLEX reads cfg.get("complexity") directly
    (never calls _resolve_complexity) → no complexity_default_used event emitted.

    FAILS today only if GREEN accidentally calls _resolve_complexity in the satisfaction path.
    Guards the invariant: the satisfaction path must not double-emit this event.
    """
    captured = _capture_emit(monkeypatch)

    def _stub(*args, **kwargs) -> StepResult:
        return StepResult(
            status="ok",
            data={
                "raw_response": _raw_pass(90),
                "doc_path": "",
                "spec_path": "",
                "review_doc_path": "",
                "fix_doc_path": "",
            },
            duration_ms=0,
            step_name="invoke_satisfaction_llm",
        )

    monkeypatch.setattr(p6, "invoke_llm_subprocess", _stub)

    ctx = _make_ctx(tmp_path)  # no complexity key
    prev = _make_invoke_prev(tmp_path)
    _invoke_satisfaction_llm(ctx, prev)

    default_used = _events_of_type(captured, "complexity_default_used")
    assert len(default_used) == 0, (
        f"AC15 FAIL: satisfaction step must NOT emit 'complexity_default_used' — "
        f"it reads cfg.get('complexity') directly; got {default_used} — 021D8FAE"
    )
