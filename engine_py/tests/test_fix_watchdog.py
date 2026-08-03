"""RED tests for 775D6752 — _fix_watchdog post-invoke step in phase_6_review.

Mirror of _green_watchdog (phase_5_implement.py:2110) applied to the fix_llm
hang class: duration accumulated WITHOUT tokens — the opposite of _green_watchdog
which catches "you went too far" (wall-2x or tokens-5x). Here the signal is
"nothing happened at all" (0-token output after 60s).

Background: 2026-05-06 W1 redo abort — invoke_fix_llm produced 0 stdout for
15 min before SIGKILL, wasting ~14 min wall + $5.61. This watchdog is the
post-invoke gate that converts that hang class into a distinct, recoverable
error code instead of a silent eventual timeout.

All tests MUST FAIL against current codebase (RED contract):
- ImportError on `_fix_watchdog` / `FIX_WATCHDOG_NO_PROGRESS_MS` (do not exist yet)
- AssertionError on workflow step ordering test (step not registered yet)

Test inventory (9 tests):
  T1 test_fix_watchdog_trips_on_zero_tokens_after_60s              AC 1
  T2 test_fix_watchdog_trips_on_None_tokens_after_60s              AC 2
  T3 test_fix_watchdog_passthrough_when_tokens_present             AC 3
  T4 test_fix_watchdog_passthrough_when_duration_under_60s         AC 4
  T5 test_fix_watchdog_at_threshold_exactly                        AC 1 boundary
  T6 test_fix_watchdog_constant_exported                           AC 5
  T7 test_fix_watchdog_step_registered_in_workflow                 AC 6
  T8 test_fix_watchdog_emits_telemetry_event                       AC 7
  T9 test_fix_watchdog_missing_prev_data_returns_error             AC 8
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

from bytedigger_engine.contracts import StepResult, WorkflowContext  # noqa: E402
from bytedigger_engine import telemetry_ctx  # noqa: E402


# ─── helpers ──────────────────────────────────────────────────────────────────


def make_ctx(scratchpad: Path, **org_extra) -> WorkflowContext:
    """Build a minimal WorkflowContext for _fix_watchdog calls."""
    org = {"scratchpad_dir": str(scratchpad), **org_extra}
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question="775D6752 fix_watchdog test",
        session_id="test-775D6752",
        persona="hal",
        framework=None,
        domain=None,
    )


def make_prev_fix_result(tokens_out, duration_ms: int = 1000) -> StepResult:
    """Build a fake prev StepResult shaped like invoke_fix_llm output."""
    return StepResult(
        status="ok",
        data={
            "duration_ms": duration_ms,
            "tokens_out": tokens_out,
            "raw_response": "FIX COMPLETE\nsome fix content",
            "log_path": "/tmp/test/build-fix-output.log",
            "spec_path": "/tmp/test/specs/build-spec.md",
            "review_doc_path": "/tmp/test/reviews/build-review.md",
            "verdict": "FAIL",
        },
        duration_ms=0,
        step_name="invoke_fix_llm",
    )


class _FakeEventLog:
    """Captures (event_type, payload, run_id) tuples for assertion."""

    def __init__(self):
        self.events: list[tuple[str, dict, str]] = []

    def append(self, event_type: str, payload: dict, run_id: str | None = None) -> None:
        self.events.append((event_type, payload, run_id or "ad-hoc"))


# ─── T1: AC 1 — trips on zero tokens after >= 60s ────────────────────────────


def test_fix_watchdog_trips_on_zero_tokens_after_60s(tmp_path):
    """AC 1: duration_ms=60_001 AND tokens_out=0 → error, E_FIX_LLM_NO_PROGRESS,
    recoverable=True.

    This is the canonical hang class: clock advanced (duration_ms large)
    but zero tokens produced — model froze without producing output.
    Unlike _green_watchdog (recoverable=False), _fix_watchdog is
    recoverable=True because hangs are transient model hiccups.
    """
    from bytedigger_engine.workflows.phase_6_review import _fix_watchdog  # noqa: PLC0415

    ctx = make_ctx(tmp_path)
    prev = make_prev_fix_result(tokens_out=0, duration_ms=60_001)

    result = _fix_watchdog(ctx, prev)

    assert result.status == "error", (
        "775D6752: _fix_watchdog must return status='error' when "
        "duration_ms=60_001 AND tokens_out=0 (hang signature). "
        "got: " + repr(result.status)
    )
    assert result.error_code == "E_FIX_LLM_NO_PROGRESS", (
        "775D6752: hang-class error must yield error_code='E_FIX_LLM_NO_PROGRESS' "
        "(distinct from E_GREEN_WATCHDOG / E_LLM_TIMEOUT — this is post-invoke "
        "hang detection). got: " + repr(result.error_code)
    )
    assert result.recoverable is True, (
        "775D6752: _fix_watchdog hang is recoverable=True (transient hiccup — "
        "retry-loop can kick in). Differs from _green_watchdog which is "
        "recoverable=False (resource blow-up). got: " + repr(result.recoverable)
    )


# ─── T2: AC 2 — trips on None tokens after >= 60s ────────────────────────────


def test_fix_watchdog_trips_on_None_tokens_after_60s(tmp_path):
    """AC 2: tokens_out=None (JSON parse failure on legacy path) is treated
    identically to tokens_out=0. Both mean 'no progress observed'.

    None happens on _communicate_legacy path when token JSON cannot be parsed
    (llm_subprocess.py:163). Zero happens on stream-json with empty result.
    Both are the hang signature — _fix_watchdog must not distinguish.
    """
    from bytedigger_engine.workflows.phase_6_review import _fix_watchdog  # noqa: PLC0415

    ctx = make_ctx(tmp_path)
    prev = make_prev_fix_result(tokens_out=None, duration_ms=90_000)

    result = _fix_watchdog(ctx, prev)

    assert result.status == "error", (
        "775D6752: tokens_out=None after 90s must trip watchdog (parse-failure "
        "case — no tokens observed). got: " + repr(result.status)
    )
    assert result.error_code == "E_FIX_LLM_NO_PROGRESS", (
        "775D6752: tokens_out=None trip must yield E_FIX_LLM_NO_PROGRESS "
        "(same code as tokens_out=0 — both are no-progress). "
        "got: " + repr(result.error_code)
    )
    assert result.recoverable is True, (
        "775D6752: tokens_out=None trip must be recoverable=True. "
        "got: " + repr(result.recoverable)
    )


# ─── T3: AC 3 — pass-through when tokens present ─────────────────────────────


def test_fix_watchdog_passthrough_when_tokens_present(tmp_path):
    """AC 3: tokens_out=42 regardless of duration → status='ok', data preserved.

    When the fix_llm produced tokens, the call made progress. Even if it
    took 120s, that's the timeout ceiling's problem — not the hang watchdog.
    _fix_watchdog only catches the ZERO-token case.
    """
    from bytedigger_engine.workflows.phase_6_review import _fix_watchdog  # noqa: PLC0415

    ctx = make_ctx(tmp_path)
    prev = make_prev_fix_result(tokens_out=42, duration_ms=120_000)

    result = _fix_watchdog(ctx, prev)

    assert result.status == "ok", (
        "775D6752: tokens_out=42 with duration_ms=120_000 must pass through "
        "(model made progress — watchdog should not fire). "
        "got: " + repr(result.status)
    )
    assert result.data == prev.data, (
        "775D6752: pass-through must preserve prev.data exactly. "
        "got data keys: " + repr(sorted(result.data.keys()) if result.data else None)
    )


# ─── T4: AC 4 — pass-through when duration under 60s ────────────────────────


def test_fix_watchdog_passthrough_when_duration_under_60s(tmp_path):
    """AC 4: duration_ms=59_999 AND tokens_out=0 → status='ok', no trip.

    The watchdog only fires when BOTH conditions hold: duration >= 60_000
    AND no tokens. A fast call that produced zero tokens is not a hang —
    it might be a legitimate empty response handled by the fix artifact
    parser. Only prolonged silence (>= 60s) with zero tokens is the hang
    signature.
    """
    from bytedigger_engine.workflows.phase_6_review import _fix_watchdog  # noqa: PLC0415

    ctx = make_ctx(tmp_path)
    prev = make_prev_fix_result(tokens_out=0, duration_ms=59_999)

    result = _fix_watchdog(ctx, prev)

    assert result.status == "ok", (
        "775D6752: duration_ms=59_999 (just under threshold) with tokens_out=0 "
        "must NOT trip watchdog — threshold is strictly >= 60_000. "
        "got: " + repr(result.status)
    )
    assert result.error_code is None, (
        "775D6752: pass-through result must have no error_code. "
        "got: " + repr(result.error_code)
    )


# ─── T5: boundary — exactly at threshold (60_000 ms) ────────────────────────


def test_fix_watchdog_at_threshold_exactly(tmp_path):
    """AC 1 boundary: duration_ms=60_000 (exactly FIX_WATCHDOG_NO_PROGRESS_MS)
    AND tokens_out=0 → MUST TRIP. Spec uses >= semantics.

    This pins the boundary condition explicitly so GREEN cannot use
    strict > by mistake (which would let the exact-threshold case slip
    through as a pass-through).
    """
    from bytedigger_engine.workflows.phase_6_review import _fix_watchdog  # noqa: PLC0415

    ctx = make_ctx(tmp_path)
    prev = make_prev_fix_result(tokens_out=0, duration_ms=60_000)

    result = _fix_watchdog(ctx, prev)

    assert result.status == "error", (
        "775D6752: duration_ms=60_000 (exactly at threshold) AND tokens_out=0 "
        "MUST TRIP watchdog. Spec uses >= semantics (not strict >). "
        "got: " + repr(result.status)
    )
    assert result.error_code == "E_FIX_LLM_NO_PROGRESS", (
        "775D6752: boundary trip must yield E_FIX_LLM_NO_PROGRESS. "
        "got: " + repr(result.error_code)
    )


# ─── T6: AC 5 — constant exported at module level ────────────────────────────


def test_fix_watchdog_constant_exported():
    """AC 5: FIX_WATCHDOG_NO_PROGRESS_MS == 60_000 exported at module level.

    Module-level constant mirrors GREEN_WATCHDOG_WALL_MULTIPLIER pattern
    (phase_5_implement.py). Allows callers / tests to reference the threshold
    symbolically and makes future tuning a 1-line edit.
    """
    from bytedigger_engine.workflows.phase_6_review import FIX_WATCHDOG_NO_PROGRESS_MS  # noqa: PLC0415

    assert FIX_WATCHDOG_NO_PROGRESS_MS == 60_000, (
        "775D6752: FIX_WATCHDOG_NO_PROGRESS_MS must equal 60_000 (60s, "
        "matches existing idle_timeout_sec=60 for invoke_fix_llm). "
        "got: " + repr(FIX_WATCHDOG_NO_PROGRESS_MS)
    )


# ─── T7: AC 6 — step registered between invoke_fix_llm and write_fix_artifact ─


def test_fix_watchdog_step_registered_in_workflow():
    """AC 6: workflow registers fix_watchdog BETWEEN invoke_fix_llm and
    write_fix_artifact.

    Checks the step list of phase_6_review_workflow(). The ordering must be:
      ... invoke_fix_llm → fix_watchdog → write_fix_artifact ...

    Mirror of test_phase_5_implement.py's step-name list assertion for
    _green_watchdog (phase_5_implement.py:154 — assert [s.name for s in wf.steps]).
    """
    from bytedigger_engine.workflows.phase_6_review import phase_6_review_workflow  # noqa: PLC0415

    wf = phase_6_review_workflow()
    step_names = [s.name for s in wf.steps]

    assert "fix_watchdog" in step_names, (
        "775D6752: phase_6_review workflow must contain a 'fix_watchdog' step. "
        "current step names: " + repr(step_names)
    )

    idx_fix_llm = step_names.index("invoke_fix_llm")
    idx_watchdog = step_names.index("fix_watchdog")
    idx_write_fix = step_names.index("write_fix_artifact")

    assert idx_fix_llm < idx_watchdog, (
        "775D6752: fix_watchdog must come AFTER invoke_fix_llm in step list. "
        "invoke_fix_llm=" + repr(idx_fix_llm) + " fix_watchdog=" + repr(idx_watchdog)
    )
    assert idx_watchdog < idx_write_fix, (
        "775D6752: fix_watchdog must come BEFORE write_fix_artifact in step list. "
        "fix_watchdog=" + repr(idx_watchdog) + " write_fix_artifact=" + repr(idx_write_fix)
    )
    assert idx_watchdog == idx_fix_llm + 1, (
        "775D6752: fix_watchdog must be IMMEDIATELY after invoke_fix_llm "
        "(no steps in between). Expected index " + repr(idx_fix_llm + 1)
        + " got " + repr(idx_watchdog)
    )


# ─── T8: AC 7 — trip emits telemetry event with required keys ────────────────


def test_fix_watchdog_emits_telemetry_event(tmp_path):
    """AC 7: when watchdog trips, emits _emit_safe('fix_watchdog_no_progress', {...})
    with keys: duration_ms, tokens_out, phase.

    Mirrors _green_watchdog telemetry pattern (phase_5_implement.py:2128):
    _emit_safe('green_watchdog_tokens_unknown', {...}) — though the event name
    and trigger condition differ.

    Uses a _FakeEventLog wired to telemetry_ctx to capture the emitted event
    (same pattern as test_phase_6_review_W2.py:160-186 and
    test_llm_subprocess_775D6752.py:436-466).
    """
    from bytedigger_engine.workflows.phase_6_review import _fix_watchdog  # noqa: PLC0415

    ctx = make_ctx(tmp_path)
    prev = make_prev_fix_result(tokens_out=0, duration_ms=60_001)

    log = _FakeEventLog()
    telemetry_ctx.set_current_run(
        event_log=log, run_id="r-fw-test", step_name="fix_watchdog", phase="phase_6"
    )
    try:
        result = _fix_watchdog(ctx, prev)
    finally:
        telemetry_ctx.clear_current_run()

    assert result.status == "error", (
        "775D6752 T8 precondition: watchdog must trip (status=error) before "
        "we can check telemetry. got: " + repr(result.status)
    )

    emitted = [e for e in log.events if e[0] == "fix_watchdog_no_progress"]
    assert len(emitted) == 1, (
        "775D6752: watchdog trip must emit exactly one 'fix_watchdog_no_progress' "
        "telemetry event. got events: " + repr([e[0] for e in log.events])
    )

    payload = emitted[0][1]
    for required_key in ("duration_ms", "tokens_out", "phase"):
        assert required_key in payload, (
            "775D6752: fix_watchdog_no_progress payload must contain key "
            "'" + required_key + "'. "
            "got payload keys: " + repr(sorted(payload.keys()))
        )

    assert payload["duration_ms"] == 60_001, (
        "775D6752: payload duration_ms must echo prev.data value. "
        "expected 60_001, got: " + repr(payload["duration_ms"])
    )
    assert payload["tokens_out"] == 0, (
        "775D6752: payload tokens_out must echo prev.data value. "
        "expected 0, got: " + repr(payload["tokens_out"])
    )


# ─── T9: AC 8 — defensive: missing prev.data returns error ───────────────────


def test_fix_watchdog_missing_prev_data_returns_error(tmp_path):
    """AC 8: when prev.data is None or prev is not a StepResult, return
    error_code='E_MISSING_PREV_DATA'. Mirror of _green_watchdog:2113-2117
    defensive guard.

    Protects against pipeline bugs where invoke_fix_llm short-circuits
    before populating data (e.g. hard error upstream that skip_on_error
    doesn't catch, or engine routing bug).
    """
    from bytedigger_engine.workflows.phase_6_review import _fix_watchdog  # noqa: PLC0415

    ctx = make_ctx(tmp_path)

    # Case A: prev is None (pipeline routing bug)
    result_none = _fix_watchdog(ctx, None)
    assert result_none.status == "error", (
        "775D6752: _fix_watchdog(ctx, None) must return status='error'. "
        "got: " + repr(result_none.status)
    )
    assert result_none.error_code == "E_MISSING_PREV_DATA", (
        "775D6752: prev=None must yield error_code='E_MISSING_PREV_DATA'. "
        "got: " + repr(result_none.error_code)
    )

    # Case B: prev.data is None (invoke_fix_llm produced no data dict)
    prev_no_data = StepResult(
        status="ok",
        data=None,
        duration_ms=0,
        step_name="invoke_fix_llm",
    )
    result_no_data = _fix_watchdog(ctx, prev_no_data)
    assert result_no_data.status == "error", (
        "775D6752: _fix_watchdog with prev.data=None must return status='error'. "
        "got: " + repr(result_no_data.status)
    )
    assert result_no_data.error_code == "E_MISSING_PREV_DATA", (
        "775D6752: prev.data=None must yield error_code='E_MISSING_PREV_DATA'. "
        "got: " + repr(result_no_data.error_code)
    )

    # Case C: prev is a non-StepResult object (wrong type)
    result_wrong_type = _fix_watchdog(ctx, {"duration_ms": 100, "tokens_out": 5})
    assert result_wrong_type.status == "error", (
        "775D6752: _fix_watchdog with prev as plain dict (not StepResult) "
        "must return status='error'. got: " + repr(result_wrong_type.status)
    )
    assert result_wrong_type.error_code == "E_MISSING_PREV_DATA", (
        "775D6752: non-StepResult prev must yield error_code='E_MISSING_PREV_DATA'. "
        "got: " + repr(result_wrong_type.error_code)
    )
