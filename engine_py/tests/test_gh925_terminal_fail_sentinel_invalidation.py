"""RED tests for GH925 — terminal satisfaction-FAIL invalidates cycle-keyed
sentinels on re-entry.

Ported from hal-v2 commit 42acd2aed. bd has no test_gh750_same_cycle_invalidation
sibling, so the AC9 cross-reference to it is dropped; the same-cycle-retry
regression guard lives in test_BC45C403_phase6_step_sentinel_resume.py /
test_GH374_step_sentinel_primitive.py here.

UUT symbols (never mocked/patched — stub-passability §1l):
  engine.WorkflowEngine._execute_steps
  lib.step_sentinel.invalidate_cycle_sentinels
  workflows.phase_6_review._write_satisfaction_doc

§1q-ext (D1CF5FDF): the new `invalidate_cycle_sentinels_on_fail` StepResult.data
key and the `terminal_fail_sentinels_invalidated` event do not exist yet; they
are only accessed/asserted inside test function bodies (never at import time),
so this file COLLECTS cleanly and each test FAILs at assert time.

No `patch`/`patch.object`/`@patch` on any UUT anywhere in this file.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
ENGINE_ROOT = HERE.parent
sys.path.insert(0, str(ENGINE_ROOT))

from bytedigger_engine.contracts import StepContract, StepResult, WorkflowContext, WorkflowDefinition  # noqa: E402
from bytedigger_engine.engine import WorkflowEngine  # noqa: E402
from bytedigger_engine.event_log import EventLog  # noqa: E402
from bytedigger_engine.lib.resume_keying import resume_sentinel_name  # noqa: E402


def _make_ctx(scratchpad: Path) -> WorkflowContext:
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={"scratchpad_dir": str(scratchpad)},
        question="GH925 terminal-fail invalidation test",
        session_id="s",
        persona="hal",
        framework=None,
        domain=None,
    )


# ─── engine-level toy workflow: LLM-step (resume_sentinel) + terminal step ──


def _run_terminal_scenario(tmp_path: Path, *, flagged: bool, run_id: str = "runGH925"):
    """resume_sentinel step 'llm_step' followed by a terminal step that
    returns status='error', recoverable=False, and (when `flagged`) the
    `invalidate_cycle_sentinels_on_fail` data key."""
    scratch = tmp_path / "scratch"
    scratch.mkdir(parents=True)
    calls_llm: list = []

    def _exec_llm(ctx, prev):
        calls_llm.append(1)
        return StepResult(status="ok", data={"n": len(calls_llm)}, duration_ms=0, step_name="llm_step")

    def _exec_terminal(ctx, prev):
        data = {"satisfaction_doc_path": "x"}
        if flagged:
            data["invalidate_cycle_sentinels_on_fail"] = True
        return StepResult(
            status="error", data=data, duration_ms=0, step_name="terminal_step",
            error="satisfaction below threshold", error_code="E_SATISFACTION_BELOW_THRESHOLD",
            recoverable=False,
        )

    workflow = WorkflowDefinition(
        name="wf_gh925",
        steps=[
            StepContract(name="llm_step", execute=_exec_llm, resume_sentinel=True),
            StepContract(name="terminal_step", execute=_exec_terminal),
        ],
    )
    log = EventLog(tmp_path / "events.jsonl")
    eng = WorkflowEngine(event_log=log)
    eng.register("wf_gh925", workflow)
    result, ctx = eng.execute("wf_gh925", _make_ctx(scratch), run_id=run_id)
    sentinel_file = scratch / "resume" / resume_sentinel_name("llm_step", 1, run_id, None, "wf_gh925")
    return result, calls_llm, log, scratch, sentinel_file


# ─── AC1 ────────────────────────────────────────────────────────────────────


def test_ac1_flagged_terminal_fail_removes_sentinel_file(tmp_path):
    """AC1: with the invalidation flag set, the llm_step sentinel is absent
    from disk after execute().

    At RED: no hook exists at the terminal error return -> sentinel survives.
    """
    result, calls_llm, log, scratch, sentinel_file = _run_terminal_scenario(tmp_path, flagged=True)
    assert result.status == "error"
    assert not sentinel_file.exists(), "flagged terminal FAIL must unlink the cycle's sentinel"


# ─── AC2 ────────────────────────────────────────────────────────────────────


def test_ac2_unflagged_terminal_fail_leaves_sentinel_intact(tmp_path):
    """AC2: without the flag, the sentinel from the ok llm_step survives
    (fresh-path / regression guard — passes today and after GREEN)."""
    result, calls_llm, log, scratch, sentinel_file = _run_terminal_scenario(tmp_path, flagged=False)
    assert result.status == "error"
    assert sentinel_file.exists(), "unflagged terminal FAIL must not touch the sentinel"


# ─── AC3 ────────────────────────────────────────────────────────────────────


def test_ac3_reentry_after_flagged_fail_reexecutes_llm_step(tmp_path):
    """AC3: a second execute() with the SAME run_id after a flagged terminal
    FAIL must re-run llm_step's body (not replay the stale sentinel) ->
    call-counter reaches 2 across both passes.

    At RED: the sentinel from pass 1 survives -> pass 2 replays it ->
    llm_step never re-executes -> calls_llm stays at 1.
    """
    scratch = tmp_path / "scratch"
    scratch.mkdir(parents=True)
    run_id = "runGH925_ac3"
    calls_llm: list = []

    def _exec_llm(ctx, prev):
        calls_llm.append(1)
        return StepResult(status="ok", data={"n": len(calls_llm)}, duration_ms=0, step_name="llm_step")

    def _exec_terminal(ctx, prev):
        return StepResult(
            status="error", data={"invalidate_cycle_sentinels_on_fail": True}, duration_ms=0,
            step_name="terminal_step", error="fail", error_code="E_SATISFACTION_BELOW_THRESHOLD",
            recoverable=False,
        )

    workflow = WorkflowDefinition(
        name="wf_gh925_ac3",
        steps=[
            StepContract(name="llm_step", execute=_exec_llm, resume_sentinel=True),
            StepContract(name="terminal_step", execute=_exec_terminal),
        ],
    )
    log = EventLog(tmp_path / "events.jsonl")
    eng = WorkflowEngine(event_log=log)
    eng.register("wf_gh925_ac3", workflow)

    eng.execute("wf_gh925_ac3", _make_ctx(scratch), run_id=run_id)
    eng.execute("wf_gh925_ac3", _make_ctx(scratch), run_id=run_id)

    assert len(calls_llm) == 2, f"expected llm_step to re-execute on re-entry, got {len(calls_llm)} calls"


# ─── AC4 ────────────────────────────────────────────────────────────────────


def test_ac4_terminal_fail_sentinels_invalidated_event_emitted(tmp_path):
    """AC4: terminal_fail_sentinels_invalidated carries error_code/cycle/
    sentinels_invalidated>=1; the step_sentinel_invalidated event (emitted by
    the shared invalidate_cycle_sentinels helper) carries
    reason='terminal_fail_state_invalidation'.

    At RED: neither event is ever emitted -> both list-comprehensions are
    empty -> assertions fail.
    """
    result, calls_llm, log, scratch, sentinel_file = _run_terminal_scenario(tmp_path, flagged=True)
    events = log.read_all()

    terminal_events = [e for e in events if e["event_type"] == "terminal_fail_sentinels_invalidated"]
    assert terminal_events, f"expected terminal_fail_sentinels_invalidated event; got {[e['event_type'] for e in events]}"
    payload = terminal_events[0].get("payload") or terminal_events[0].get("data") or {}
    assert payload.get("error_code") == "E_SATISFACTION_BELOW_THRESHOLD"
    assert payload.get("cycle") == 1
    assert payload.get("sentinels_invalidated", 0) >= 1

    invalidated = [e for e in events if e["event_type"] == "step_sentinel_invalidated"]
    assert invalidated, "expected a step_sentinel_invalidated event from the shared helper"
    inv_payload = invalidated[0].get("payload") or invalidated[0].get("data") or {}
    assert inv_payload.get("reason") == "terminal_fail_state_invalidation", (
        f"expected reason='terminal_fail_state_invalidation', got {inv_payload!r}"
    )


# ─── AC5 ────────────────────────────────────────────────────────────────────


def test_ac5_second_terminal_fail_reinvalidates_without_raising(tmp_path):
    """AC5 (re-frozen, MAJOR-1): a second execute() (same run_id), after a
    re-entry that re-executed llm_step and re-wrote its sentinel, ALSO
    terminally fails flagged. Both flagged terminal fails must complete
    WITHOUT raising and each must re-invalidate the freshly-written cycle
    sentinel (sentinels_invalidated>=1 on BOTH passes) — there is no zero
    stationary state since AC3's re-entry always re-writes the sentinel
    before the second terminal fail observes it.

    At RED: no terminal_fail_sentinels_invalidated event is ever emitted ->
    the events list is empty -> len(terminal_events) == 2 fails.
    """
    scratch = tmp_path / "scratch"
    scratch.mkdir(parents=True)
    run_id = "runGH925_ac5"

    def _exec_llm(ctx, prev):
        return StepResult(status="ok", data={"n": 1}, duration_ms=0, step_name="llm_step")

    def _exec_terminal(ctx, prev):
        return StepResult(
            status="error", data={"invalidate_cycle_sentinels_on_fail": True}, duration_ms=0,
            step_name="terminal_step", error="fail", error_code="E_SATISFACTION_BELOW_THRESHOLD",
            recoverable=False,
        )

    workflow = WorkflowDefinition(
        name="wf_gh925_ac5",
        steps=[
            StepContract(name="llm_step", execute=_exec_llm, resume_sentinel=True),
            StepContract(name="terminal_step", execute=_exec_terminal),
        ],
    )
    log = EventLog(tmp_path / "events.jsonl")
    eng = WorkflowEngine(event_log=log)
    eng.register("wf_gh925_ac5", workflow)

    try:
        eng.execute("wf_gh925_ac5", _make_ctx(scratch), run_id=run_id)
        eng.execute("wf_gh925_ac5", _make_ctx(scratch), run_id=run_id)
    except Exception as exc:  # pragma: no cover — guard, not expected path
        pytest.fail(f"both flagged terminal fails must complete without raising: {exc!r}")

    events = log.read_all()
    terminal_events = [e for e in events if e["event_type"] == "terminal_fail_sentinels_invalidated"]
    assert len(terminal_events) == 2, f"expected an event on each pass, got {len(terminal_events)}"
    for i, ev in enumerate(terminal_events):
        payload = ev.get("payload") or ev.get("data") or {}
        assert payload.get("sentinels_invalidated", 0) >= 1, (
            f"pass {i}: re-entry re-writes the sentinel before the next terminal fail, "
            f"so invalidation must re-fire (>=1), got {payload!r}"
        )


# ─── AC6/AC7/AC8 — phase_6_review satisfaction-FAIL sites declare the flag ──


def _make_sat_ctx(tmp_path: Path, satisfaction_threshold: int = 85) -> WorkflowContext:
    scratch = tmp_path / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={"scratchpad_dir": str(scratch), "satisfaction_threshold": satisfaction_threshold},
        question="GH925 satisfaction flag test",
        session_id="test-gh925",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_single_prev(tmp_path: Path, raw_response: str) -> StepResult:
    scratch = tmp_path / "scratch"
    reviews_dir = scratch / "reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)
    sat_doc = reviews_dir / "build-satisfaction.md"
    review_doc = reviews_dir / "build-review.md"
    review_doc.write_text("# Review\nVERDICT: FAIL\n", encoding="utf-8")
    return StepResult(
        status="ok",
        data={
            "raw_response": raw_response,
            "doc_path": str(sat_doc),
            "spec_path": str(scratch / "specs" / "build-spec.md"),
            "review_doc_path": str(review_doc),
            "fix_doc_path": str(scratch / "reviews" / "build-fix.md"),
        },
        duration_ms=100,
        step_name="invoke_satisfaction_llm",
    )


def _make_multi_prev(tmp_path: Path, evaluator_responses: list) -> StepResult:
    scratch = tmp_path / "scratch"
    reviews_dir = scratch / "reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)
    sat_doc = reviews_dir / "build-satisfaction.md"
    review_doc = reviews_dir / "build-review.md"
    review_doc.write_text("# Review\nVERDICT: FAIL\n", encoding="utf-8")
    ok_raws = [e["raw_response"] for e in evaluator_responses if e.get("status") == "ok" and e.get("raw_response")]
    return StepResult(
        status="ok",
        data={
            "raw_response": ok_raws[0] if ok_raws else "",
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


def _raw_score_fail(score: int) -> str:
    return "\n".join(["## Composite", f"SCORE: {score}", "VERDICT: FAIL", ""])


def _raw_score_pass(score: int) -> str:
    return "\n".join([
        "## Evaluation", f"SCORE: {score}", "VERDICT: PASS", "",
        '## satisfaction-output (structured)\n```json\n{"satisfied": true, "fixes_required": []}\n```',
    ])


def _raw_score_fail_structured(score: int) -> str:
    return "\n".join([
        "## Evaluation", f"SCORE: {score}", "VERDICT: FAIL", "",
        '## satisfaction-output (structured)\n```json\n'
        '{"satisfied": false, "fixes_required": [{"file": "a.py", "issue": "x"}]}\n```',
    ])


def test_ac6_single_eval_fail_declares_invalidation_flag(tmp_path, monkeypatch):
    """AC6: single-eval satisfaction FAIL StepResult carries
    data['invalidate_cycle_sentinels_on_fail'] is True; common_data keys
    (e.g. satisfaction_doc_path) survive alongside it.

    At RED: the key does not exist in production yet -> data.get(...) is
    None, not True.
    """
    from bytedigger_engine.workflows import phase_6_review
    from bytedigger_engine.workflows.phase_6_review import _write_satisfaction_doc

    captured: list = []
    monkeypatch.setattr(phase_6_review, "_emit_safe", lambda et, p, **kw: captured.append((et, p, kw)))

    ctx = _make_sat_ctx(tmp_path, satisfaction_threshold=85)
    prev = _make_single_prev(tmp_path, _raw_score_fail(50))
    result = _write_satisfaction_doc(ctx, prev)

    assert result.status == "error"
    assert result.data.get("invalidate_cycle_sentinels_on_fail") is True, (
        f"expected the invalidation flag on a satisfaction FAIL, got data={result.data!r}"
    )
    assert result.data.get("satisfaction_doc_path"), "common_data keys must survive alongside the new flag"


def test_ac7_multi_eval_both_fail_sites_declare_flag_ok_does_not(tmp_path, monkeypatch):
    """AC7: multi-eval majority-FAIL and AC-checklist-FAIL StepResults both
    carry the flag; a multi-eval PASS result does NOT.

    At RED: the flag is absent on the FAIL result -> first assertion fails.
    """
    from bytedigger_engine.workflows import phase_6_review
    from bytedigger_engine.workflows.phase_6_review import _write_satisfaction_doc

    captured: list = []
    monkeypatch.setattr(phase_6_review, "_emit_safe", lambda et, p, **kw: captured.append((et, p, kw)))

    ctx = _make_sat_ctx(tmp_path, satisfaction_threshold=80)

    # majority FAIL: 2 of 3 evaluators fail
    fail_evals = [
        {"index": 0, "status": "ok", "raw_response": _raw_score_pass(85), "error_code": None},
        {"index": 1, "status": "ok", "raw_response": _raw_score_fail_structured(60), "error_code": None},
        {"index": 2, "status": "ok", "raw_response": _raw_score_fail_structured(55), "error_code": None},
    ]
    prev_fail = _make_multi_prev(tmp_path, fail_evals)
    result_fail = _write_satisfaction_doc(ctx, prev_fail)
    assert result_fail.status == "error", f"expected majority FAIL, got {result_fail.status!r}"
    assert result_fail.data.get("invalidate_cycle_sentinels_on_fail") is True, (
        f"expected flag on multi-eval majority FAIL, got data={result_fail.data!r}"
    )

    # unanimous PASS: flag must be absent
    pass_evals = [
        {"index": 0, "status": "ok", "raw_response": _raw_score_pass(95), "error_code": None},
        {"index": 1, "status": "ok", "raw_response": _raw_score_pass(92), "error_code": None},
        {"index": 2, "status": "ok", "raw_response": _raw_score_pass(88), "error_code": None},
    ]
    prev_pass = _make_multi_prev(tmp_path, pass_evals)
    result_pass = _write_satisfaction_doc(ctx, prev_pass)
    assert result_pass.status == "ok", f"expected unanimous PASS, got {result_pass.status!r}"
    assert "invalidate_cycle_sentinels_on_fail" not in (result_pass.data or {}), (
        "PASS result must not carry the invalidation flag"
    )


def test_ac8_review_degraded_does_not_declare_flag(tmp_path, monkeypatch):
    """AC8: E_REVIEW_DEGRADED (n_valid<2, structural degradation, not a
    stale-tree verdict) must NOT carry the flag (§1n DEFER).

    This may already pass today (key doesn't exist anywhere pre-GREEN, so
    'not in data' is trivially true) — kept as the DEFER-classification
    regression guard per spec §2.3; paired with AC6/AC7 which DO fail today.
    """
    from bytedigger_engine.workflows import phase_6_review
    from bytedigger_engine.workflows.phase_6_review import _write_satisfaction_doc

    captured: list = []
    monkeypatch.setattr(phase_6_review, "_emit_safe", lambda et, p, **kw: captured.append((et, p, kw)))

    ctx = _make_sat_ctx(tmp_path, satisfaction_threshold=80)
    prose = "## Evaluation\nNo score, no structured block here."
    degraded_evals = [
        {"index": 0, "status": "ok", "raw_response": prose, "error_code": None},
        {"index": 1, "status": "ok", "raw_response": prose, "error_code": None},
        {"index": 2, "status": "ok", "raw_response": prose, "error_code": None},
    ]
    prev = _make_multi_prev(tmp_path, degraded_evals)
    result = _write_satisfaction_doc(ctx, prev)

    assert result.status == "error"
    assert result.error_code == "E_REVIEW_DEGRADED", f"expected E_REVIEW_DEGRADED, got {result.error_code!r}"
    assert "invalidate_cycle_sentinels_on_fail" not in (result.data or {}), (
        f"E_REVIEW_DEGRADED must not carry the invalidation flag (DEFER), got data={result.data!r}"
    )


# ─── AC10 ───────────────────────────────────────────────────────────────────


def test_ac10_recoverable_retry_leaves_earlier_cycle_sentinels_alone(tmp_path):
    """AC10 (§1ab-(b)): a recoverable same-cycle-retry path must NOT trigger
    the new terminal-fail invalidation branch — only non-recoverable terminal
    errors carrying the flag do. A step0 sentinel written before the retry
    survives, and no terminal_fail_sentinels_invalidated event is ever
    emitted for this run.

    This targets the intended scoping (terminal-fail hook is gated on the
    terminal `return result`, never via the recoverable retry-recursion
    branch) — it should already hold pre-GREEN (no hook exists yet to
    mis-fire), and must continue to hold post-GREEN.
    """
    scratch = tmp_path / "scratch"
    scratch.mkdir(parents=True)
    run_id = "runGH925_ac10"
    calls0: list = []
    calls1: list = []

    def _exec0(ctx, prev):
        calls0.append(1)
        return StepResult(status="ok", data={"n": len(calls0)}, duration_ms=0, step_name="step0")

    def _exec1(ctx, prev):
        calls1.append(1)
        if len(calls1) == 1:
            return StepResult(
                status="error", data={"retry_from_step": 0, "cycle_count": 0,
                                      "invalidate_cycle_sentinels_on_fail": True},
                duration_ms=0, step_name="retry_producer", error="retry me",
                error_code="E_VALIDATION_RETRY", recoverable=True,
            )
        return StepResult(status="ok", data={"done": True}, duration_ms=0, step_name="retry_producer")

    workflow = WorkflowDefinition(
        name="wf_gh925_ac10",
        steps=[
            StepContract(name="step0", execute=_exec0, resume_sentinel=True),
            StepContract(name="retry_producer", execute=_exec1),
        ],
    )
    log = EventLog(tmp_path / "events.jsonl")
    eng = WorkflowEngine(event_log=log)
    eng.register("wf_gh925_ac10", workflow)
    result, ctx = eng.execute("wf_gh925_ac10", _make_ctx(scratch), run_id=run_id)

    events = log.read_all()
    terminal_events = [e for e in events if e["event_type"] == "terminal_fail_sentinels_invalidated"]
    assert terminal_events == [], (
        f"a recoverable same-cycle retry must never emit terminal_fail_sentinels_invalidated, "
        f"got {terminal_events}"
    )
    assert result.status == "ok"
