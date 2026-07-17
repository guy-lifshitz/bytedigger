"""RED tests for GH963 — validation-gate execution-failure detect + bounded
retry + sentinel hygiene.

Spec: SHARED/memory/Decisions/2026-07-17_GH963_validation_execution_failure_spec.md
§3 AC1-AC14.

UUT symbols (never mocked/patched — stub-passability §1l):
  workflows.phase_5_implement._detect_validation_non_execution
  workflows.phase_5_implement._invoke_validation_llm
  lib.step_sentinel.invalidate_validation_sentinels_for_run
  lib.restart_governor.governor_reset_full

§1q-ext (D1CF5FDF): `_detect_validation_non_execution`,
`invalidate_validation_sentinels_for_run`, `governor_reset_full`,
`VERDICT_CATEGORY_VALIDATION_EXECUTION_FAILURE`, and the
"validation_execution" policy-matrix rows do not exist yet. The *modules*
(`phase_5_implement`, `step_sentinel`, `restart_governor`, `_recoverable_policy`,
`error_codes`) already exist and are imported at module level; only the
not-yet-existing *attributes* are accessed, and only inside test function
bodies, so the file COLLECTS cleanly and each test FAILs at assert/attribute
time rather than at collection time.

Only collaborators are ever patched: `phase_5_implement.invoke_llm_subprocess`
and `phase_5_implement._emit_safe` (monkeypatch.setattr on the imported module
object). The UUT itself is never patched.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
ENGINE_ROOT = HERE.parent
sys.path.insert(0, str(ENGINE_ROOT))
sys.path.insert(0, str(ENGINE_ROOT / "lib"))
sys.path.insert(0, str(ENGINE_ROOT / "workflows"))

import phase_5_implement  # noqa: E402 — module exists; new attrs accessed lazily
import step_sentinel  # noqa: E402
import restart_governor  # noqa: E402
from contracts import StepResult, WorkflowContext  # noqa: E402
from resume_keying import resume_sentinel_name  # noqa: E402


def _make_ctx(**org_extra) -> WorkflowContext:
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={"complexity": "SIMPLE", **org_extra},
        question="GH963 validation execution failure test",
        session_id="s",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_prev(tmp_path, **data_extra) -> StepResult:
    data = {
        "prompt": "p",
        "doc_path": str(tmp_path / "d.md"),
        "spec_path": str(tmp_path / "s.md"),
        "red_log_path": str(tmp_path / "r.log"),
        "cycle": 1,
        "stable_prefix": "",
        **data_extra,
    }
    return StepResult(status="ok", data=data, duration_ms=0, step_name="check_red_executable")


# ─── AC1 ────────────────────────────────────────────────────────────────────


def test_ac1_detect_marker_and_none_on_clean_audit():
    """AC1: detects a known marker case-insensitively; a genuine-audit FAIL
    body with no markers returns None.

    At RED: getattr raises AttributeError (function doesn't exist yet).
    """
    fn = getattr(phase_5_implement, "_detect_validation_non_execution")
    assert fn("The validator report says: …Validation was NOT performed… today.") == (
        "validation was not performed"
    )
    clean = "VERDICT: FAIL\n\nforward map: line 12 -> AC3\nno issues with tooling execution here."
    assert fn(clean) is None


# ─── AC2 (parametrized over the 6 markers) ──────────────────────────────────


@pytest.mark.parametrize(
    "marker",
    [
        "validation was not performed",
        "validation not performed",
        "no tool call",
        "inputs not read",
        "tooling did not deliver",
        "re-run the validation gate",
    ],
)
def test_ac2_each_marker_triggers_detection(marker):
    fn = getattr(phase_5_implement, "_detect_validation_non_execution")
    raw = f"Some preamble text. {marker.upper()} Some trailer text."
    assert fn(raw) == marker


def test_ac2_empty_string_returns_none():
    fn = getattr(phase_5_implement, "_detect_validation_non_execution")
    assert fn("") is None


# ─── AC3 / AC4 ──────────────────────────────────────────────────────────────


def test_ac3_invoke_validation_llm_returns_recoverable_retry_on_marker(tmp_path, monkeypatch):
    """AC3: monkeypatched invoke_llm_subprocess returns ok+marker raw ->
    _invoke_validation_llm returns status='error', recoverable=True,
    data['retry_from_step']==0, error_code=='E_VALIDATION_EXEC_RETRY',
    data['gate_attempts']['validation_execution']==1.

    At RED: no detection wiring exists; the patched ok StepResult (with the
    poisoned raw_response) is returned unchanged -> status stays 'ok'.
    """
    poisoned = StepResult(
        status="ok",
        data={"raw_response": "The tooling did not deliver the report; re-run the validation gate."},
        duration_ms=0,
        step_name="invoke_validation_llm",
    )
    monkeypatch.setattr(phase_5_implement, "invoke_llm_subprocess", lambda **kw: poisoned)
    ctx = _make_ctx()
    prev = _make_prev(tmp_path)
    result = phase_5_implement._invoke_validation_llm(ctx, prev)
    assert result.status == "error"
    assert result.recoverable is True
    assert result.data["retry_from_step"] == 0
    assert result.error_code == "E_VALIDATION_EXEC_RETRY"
    assert result.data["gate_attempts"]["validation_execution"] == 1


def test_ac4_emits_validation_execution_failure_detected(tmp_path, monkeypatch):
    """AC4: same call emits validation_execution_failure_detected with
    payload['verdict_category']=='VALIDATION_EXECUTION_FAILURE' and a
    'marker' key.

    At RED: _emit_safe is never called with this event name -> emitted list
    stays empty, assertion fails.
    """
    poisoned = StepResult(
        status="ok",
        data={"raw_response": "no tool call was made during this validation pass."},
        duration_ms=0,
        step_name="invoke_validation_llm",
    )
    monkeypatch.setattr(phase_5_implement, "invoke_llm_subprocess", lambda **kw: poisoned)
    emitted: list = []

    def _fake_emit_safe(event_type, payload, severity="info"):
        emitted.append((event_type, payload, severity))

    monkeypatch.setattr(phase_5_implement, "_emit_safe", _fake_emit_safe)
    ctx = _make_ctx()
    prev = _make_prev(tmp_path)
    phase_5_implement._invoke_validation_llm(ctx, prev)

    matches = [e for e in emitted if e[0] == "validation_execution_failure_detected"]
    assert matches, f"expected validation_execution_failure_detected; got {[e[0] for e in emitted]}"
    payload = matches[0][1]
    assert payload.get("verdict_category") == "VALIDATION_EXECUTION_FAILURE"
    assert "marker" in payload


# ─── AC5 ────────────────────────────────────────────────────────────────────


def test_ac5_budget_spent_returns_terminal_execution_failure(tmp_path, monkeypatch):
    """AC5: with prev.data['gate_attempts']={'validation_execution': 1}
    (budget already spent for recoverable_once) -> terminal:
    status='error', recoverable=False, error_code=='E_VALIDATION_EXECUTION_FAILURE'.

    At RED: no gating exists -> status stays 'ok' (poisoned passthrough).
    """
    poisoned = StepResult(
        status="ok",
        data={"raw_response": "Validation was not performed due to a tooling error."},
        duration_ms=0,
        step_name="invoke_validation_llm",
    )
    monkeypatch.setattr(phase_5_implement, "invoke_llm_subprocess", lambda **kw: poisoned)
    ctx = _make_ctx()
    prev = _make_prev(tmp_path, gate_attempts={"validation_execution": 1})
    result = phase_5_implement._invoke_validation_llm(ctx, prev)
    assert result.status == "error"
    assert result.recoverable is False
    assert result.error_code == "E_VALIDATION_EXECUTION_FAILURE"


# ─── AC6 ────────────────────────────────────────────────────────────────────


def test_ac6_clean_response_passthrough_unchanged(tmp_path, monkeypatch):
    """AC6: a clean (marker-free) ok response is returned exactly as
    invoke_llm_subprocess produced it -- zero behavior change.

    This is a regression guard and may already pass today.
    """
    clean = StepResult(
        status="ok",
        data={"raw_response": "VERDICT: FAIL\n\nforward map: line 12 -> AC3\nno issues at all."},
        duration_ms=0,
        step_name="invoke_validation_llm",
    )
    monkeypatch.setattr(phase_5_implement, "invoke_llm_subprocess", lambda **kw: clean)
    ctx = _make_ctx()
    prev = _make_prev(tmp_path)
    result = phase_5_implement._invoke_validation_llm(ctx, prev)
    assert result.status == "ok"
    assert result.data == clean.data


# ─── AC7 ────────────────────────────────────────────────────────────────────


def test_ac7_non_ok_result_passes_through_even_with_marker_text(tmp_path, monkeypatch):
    """AC7: a non-ok result (error_code='E_LLM_TIMEOUT') passes through
    unchanged even when its data contains marker-like text (§1n DEFER).

    Regression guard — may already pass today (the ok-guard is a design
    property that a naive always-scan GREEN could violate).
    """
    timed_out = StepResult(
        status="error",
        data={"raw_response": "inputs not read yet"},
        duration_ms=0,
        step_name="invoke_validation_llm",
        error="timed out",
        error_code="E_LLM_TIMEOUT",
        recoverable=True,
    )
    monkeypatch.setattr(phase_5_implement, "invoke_llm_subprocess", lambda **kw: timed_out)
    ctx = _make_ctx()
    prev = _make_prev(tmp_path)
    result = phase_5_implement._invoke_validation_llm(ctx, prev)
    assert result.status == "error"
    assert result.error_code == "E_LLM_TIMEOUT"
    assert result.data == timed_out.data


# ─── AC8 ────────────────────────────────────────────────────────────────────


class _StepStub:
    def __init__(self, name, resume_sentinel=True, sentinel_input_field=None):
        self.name = name
        self.resume_sentinel = resume_sentinel
        self.sentinel_input_field = sentinel_input_field


def test_ac8_maybe_write_sentinel_skips_error_writes_ok(tmp_path):
    """AC8: maybe_write_sentinel with the AC3-detected (status='error')
    result writes NOTHING to scratchpad/resume/ (dir stays empty); with an
    ok result it writes the sentinel file.

    This exercises the EXISTING maybe_write_sentinel (status=='ok' guard) —
    it already behaves correctly for the error branch (regression guard),
    but the ok branch assertion pins the presence-pair shape the spec relies
    on for GH963 to hold end-to-end.
    """
    scratch = tmp_path / "scratch"
    ctx = _make_ctx(scratchpad_dir=str(scratch))
    step = _StepStub(name="invoke_validation_llm")

    error_result = StepResult(status="error", data={"x": 1}, duration_ms=0, step_name="invoke_validation_llm")
    step_sentinel.maybe_write_sentinel(ctx, step, 1, "R1", error_result)
    resume_dir = scratch / "resume"
    assert not resume_dir.exists() or list(resume_dir.iterdir()) == [], (
        "an error result must never be sentinel-cached"
    )

    ok_result = StepResult(status="ok", data={"x": 2}, duration_ms=0, step_name="invoke_validation_llm")
    step_sentinel.maybe_write_sentinel(ctx, step, 1, "R1", ok_result)
    assert resume_dir.exists() and list(resume_dir.iterdir()), "an ok result must be sentinel-cached"


# ─── AC9 ────────────────────────────────────────────────────────────────────


def test_ac9_policy_matrix_has_validation_execution_rows():
    """AC9: resolve_policy(bc, 'validation_execution') ==
    RecoverablePolicy('recoverable_once', 1) for SIMPLE/FEATURE/COMPLEX,
    and the explicit matrix rows exist (not the default fallback).

    At RED: ('SIMPLE', 'validation_execution') not in _POLICY_MATRIX.
    """
    import _recoverable_policy as rp

    assert ("SIMPLE", "validation_execution") in rp._POLICY_MATRIX
    for bc in ("SIMPLE", "FEATURE", "COMPLEX"):
        pol = rp.resolve_policy(bc, "validation_execution")
        assert pol.slot == "recoverable_once"
        assert pol.cycle_cap == 1


# ─── AC10 ───────────────────────────────────────────────────────────────────


def test_ac10_error_codes_registry_has_new_codes():
    """AC10: error_codes registry contains both new codes with non-empty
    descriptions.

    At RED: neither key is present in ERROR_CODES.
    """
    import error_codes

    for code in ("E_VALIDATION_EXEC_RETRY", "E_VALIDATION_EXECUTION_FAILURE"):
        assert code in error_codes.ERROR_CODES, f"missing {code}"
        assert error_codes.ERROR_CODES[code].strip(), f"{code} has empty description"


# ─── AC11 ───────────────────────────────────────────────────────────────────


def test_ac11_invalidate_validation_sentinels_scoped_and_idempotent(tmp_path):
    """AC11: invalidate_validation_sentinels_for_run(tmp, 'R1') unlinks the
    two named validation-cycle files, leaves the prefix-collision file
    ('...rR12.json') and the other-step file intact, returns the 2 removed
    names, and a second call returns [].

    At RED: getattr raises AttributeError (function doesn't exist yet).
    """
    resume_dir = tmp_path / "resume"
    resume_dir.mkdir(parents=True)

    f1 = resume_dir / "phase_5_validation_cycle__invoke_validation_llm_done_c1_rR1.json"
    f2 = resume_dir / "phase_5_validation_cycle__invoke_validation_llm_done_c2_rR1_habcdef123456.json"
    f_collision = resume_dir / "phase_5_validation_cycle__invoke_validation_llm_done_c1_rR12.json"
    f_other_step = resume_dir / "phase_5_validation_cycle__invoke_red_llm_done_c1_rR1.json"
    for f in (f1, f2, f_collision, f_other_step):
        f.write_text(json.dumps({"marker": f.name}))

    fn = getattr(step_sentinel, "invalidate_validation_sentinels_for_run")
    removed = fn(tmp_path, "R1")

    assert not f1.exists()
    assert not f2.exists()
    assert f_collision.exists(), "run_id prefix collision file must survive"
    assert f_other_step.exists(), "other-step file must survive"
    assert set(removed) == {f1.name, f2.name}

    removed_again = fn(tmp_path, "R1")
    assert removed_again == [], "second call must be idempotent (no files left to remove)"


# ─── AC12 ───────────────────────────────────────────────────────────────────


def test_ac12_governor_reset_full_unlinks_state_and_sentinel_and_emits_event(tmp_path):
    """AC12: governor_reset_full unlinks BOTH the governor state file AND
    the seeded validation sentinel, appends a
    governor_reset_sentinels_invalidated event to the event log, and returns
    dict with prior_starts + sentinels_removed (1 name).

    At RED: getattr raises AttributeError (function doesn't exist yet).
    """
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    scratchpad = tmp_path / "scratch"
    resume_dir = scratchpad / "resume"
    resume_dir.mkdir(parents=True)
    event_log = tmp_path / "events.jsonl"

    restart_governor.governor_record_start(state_dir, "R1", "phase_5_implement")
    state_file = restart_governor._state_file(state_dir, "R1", "phase_5_implement")
    assert state_file.exists()

    sentinel_file = resume_dir / "phase_5_validation_cycle__invoke_validation_llm_done_c1_rR1.json"
    sentinel_file.write_text(json.dumps({"x": 1}))

    fn = getattr(restart_governor, "governor_reset_full")
    result = fn(
        state_dir, "R1", "phase_5_implement", "op reset",
        event_log_path=str(event_log), scratchpad_dir=str(scratchpad),
    )

    assert not state_file.exists()
    assert not sentinel_file.exists()
    assert "prior_starts" in result
    assert result.get("sentinels_removed") == [sentinel_file.name]

    assert event_log.exists()
    lines = [json.loads(l) for l in event_log.read_text().splitlines() if l.strip()]
    types = [e.get("event_type") for e in lines]
    assert "governor_reset_sentinels_invalidated" in types


# ─── AC13 ───────────────────────────────────────────────────────────────────


def test_ac13_governor_reset_full_none_scratchpad_matches_legacy(tmp_path):
    """AC13: governor_reset_full with scratchpad_dir=None behaves exactly as
    governor_reset -- state unlinked, sentinels_removed==[], no invalidation
    event (run.py legacy path safe).

    At RED: getattr raises AttributeError (function doesn't exist yet).
    """
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    event_log = tmp_path / "events.jsonl"

    restart_governor.governor_record_start(state_dir, "R2", "phase_5_implement")
    state_file = restart_governor._state_file(state_dir, "R2", "phase_5_implement")
    assert state_file.exists()

    fn = getattr(restart_governor, "governor_reset_full")
    result = fn(
        state_dir, "R2", "phase_5_implement", "op reset",
        event_log_path=str(event_log), scratchpad_dir=None,
    )

    assert not state_file.exists()
    assert result.get("sentinels_removed") == []

    if event_log.exists():
        lines = [json.loads(l) for l in event_log.read_text().splitlines() if l.strip()]
        types = [e.get("event_type") for e in lines]
        assert "governor_reset_sentinels_invalidated" not in types


# ─── AC14 ───────────────────────────────────────────────────────────────────


def test_ac14_run_py_calls_governor_reset_full_with_scratchpad_dir():
    """AC14: run.py source calls governor_reset_full( in the restart_reason
    branch, passing scratchpad_dir=; bare governor_reset( is no longer
    called there.

    At RED: run.py still calls bare governor_reset(...) at L166.
    """
    src = (ENGINE_ROOT / "run.py").read_text()
    assert "governor_reset_full(" in src, "run.py must call governor_reset_full"
    assert "scratchpad_dir=" in src, "run.py call must pass scratchpad_dir="

    idx = src.rindex("if args.restart_reason:")
    branch = src[idx: idx + 600]
    assert "governor_reset_full(" in branch, (
        "the restart_reason branch must call governor_reset_full, not bare governor_reset"
    )
    assert "governor_reset(" not in branch.replace("governor_reset_full(", ""), (
        "bare governor_reset( must no longer be called in the restart_reason branch"
    )
