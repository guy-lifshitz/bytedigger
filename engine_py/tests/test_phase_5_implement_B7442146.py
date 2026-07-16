"""B7442146 RED tests — CONTROL axis: hard gate on GREEN output token budget.

Closes agreement B7442146 (control half of dual-axis pair with 228AB822
root-cause prompt rewrite). Adds a HARD gate that fails the pipeline if
GREEN's actual output exceeds 5000 tokens, regardless of prompt instructions.

Two changes pinned:

1. ``llm_subprocess.invoke_llm_subprocess`` returns ``tokens_out`` and
   ``tokens_in`` as REQUIRED keys in ``StepResult.data`` (None when the
   backend did not report tokens).

2. ``phase_5_implement`` workflow gains a new step ``check_green_token_budget``
   between ``invoke_green_llm`` and ``write_green_artifact``.

25e75663 migration (R2): shell-subprocess stubs replaced with register_backend
spy stubs that return canned StepResult directly. Behavioral contract unchanged.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "workflows"))


# ─── autouse fixture: reset _BACKENDS singleton between tests (§1i) ───────────

import llm_subprocess as _llm_subprocess_mod
from llm_subprocess import register_backend, reset_backends
from contracts import StepResult as _StepResult


@pytest.fixture(autouse=True)
def _reset_backends():
    """§1i: restore _BACKENDS singleton after every test."""
    yield
    reset_backends()


# ─── Test 1: tokens_out present (int) when backend reports token data ─────────


def test_b7442146_invoke_llm_subprocess_data_includes_tokens_out():
    """invoke_llm_subprocess must surface tokens_out in StepResult.data.

    25e75663 migration (R2): replaced python3 JSON-envelope subprocess stub
    with a register_backend stub returning canned token data. Contract unchanged:
    data['tokens_out'] == 1234, data['tokens_in'] == 42.
    """
    from llm_subprocess import invoke_llm_subprocess  # noqa: PLC0415

    class _TokenBackend:
        def __call__(self, **kw) -> _StepResult:
            return _StepResult(
                status="ok",
                data={
                    "raw_response": "ok",
                    "tokens_out": 1234,
                    "tokens_in": 42,
                    "worker_written_paths": [],
                    "manifest_source": "harness_tool_record",
                },
                duration_ms=0,
                step_name=kw.get("step_name", "step"),
                error=None,
                error_code=None,
                recoverable=True,
            )

    register_backend("b7442146-token-backend", _TokenBackend(),
                     manifest_source="harness_tool_record", overwrite=True)

    r = invoke_llm_subprocess(
        prompt="hi",
        model="sonnet",
        timeout_sec=10,
        step_name="step",
        backend="b7442146-token-backend",
    )
    assert r.status == "ok", f"stub should succeed, got {r.status!r}: {getattr(r, 'error', None)!r}"
    assert "tokens_out" in r.data, (
        "tokens_out missing from StepResult.data — required by B7442146 contract."
    )
    assert r.data["tokens_out"] == 1234, (
        f"Expected tokens_out=1234, got {r.data['tokens_out']!r}"
    )
    assert "tokens_in" in r.data, "tokens_in missing — required key alongside tokens_out"
    assert r.data["tokens_in"] == 42, (
        f"Expected tokens_in=42, got {r.data['tokens_in']!r}"
    )


# ─── Test 2: tokens_out is None when backend reports no token data ────────────


def test_b7442146_invoke_llm_subprocess_data_tokens_out_none_when_no_json():
    """Backend that does not report tokens must set tokens_out=None — key MUST
    be present so downstream code never KeyErrors.

    25e75663 migration (R2): plain-text subprocess stub replaced with a
    register_backend stub returning tokens_out=None. Contract unchanged.
    """
    from llm_subprocess import invoke_llm_subprocess  # noqa: PLC0415

    class _PlainBackend:
        def __call__(self, **kw) -> _StepResult:
            return _StepResult(
                status="ok",
                data={
                    "raw_response": "HELLO\n",
                    "tokens_out": None,
                    "tokens_in": None,
                    "worker_written_paths": [],
                    "manifest_source": "harness_tool_record",
                },
                duration_ms=0,
                step_name=kw.get("step_name", "step"),
                error=None,
                error_code=None,
                recoverable=True,
            )

    register_backend("b7442146-plain-backend", _PlainBackend(),
                     manifest_source="harness_tool_record", overwrite=True)

    r = invoke_llm_subprocess(
        prompt="hi",
        model="sonnet",
        timeout_sec=10,
        step_name="step",
        backend="b7442146-plain-backend",
    )
    assert r.status == "ok"
    assert "tokens_out" in r.data, (
        "tokens_out key MUST be present (None) even when backend reports no tokens."
    )
    assert r.data["tokens_out"] is None, (
        f"Expected tokens_out=None for plain backend, got {r.data['tokens_out']!r}"
    )
    assert "tokens_in" in r.data
    assert r.data["tokens_in"] is None


# ─── Test 3: gate passes when tokens_out < 5000 ──────────────────────────────


def test_b7442146_check_green_token_budget_passes_when_under_5000():
    """tokens_out=4999 → pass-through. prev.data preserved so write_green_artifact
    sees the same shape as today.

    FAILS today: function does not exist.
    """
    import phase_5_implement  # noqa: PLC0415
    from contracts import StepResult  # noqa: PLC0415

    prev_data = {
        "tokens_out": 4999,
        "tokens_in": 100,
        "raw_response": "GREEN COMPLETE — all 1 tests passing. Files modified: [x]\n",
        "log_path": "/tmp/x.log",
        "spec_path": "/tmp/spec.md",
        "red_log_path": "/tmp/red.log",
        "validation_doc_path": "/tmp/v.md",
        "verdict": "PASS",
        "prompt": "p",
    }
    prev = StepResult(
        status="ok",
        data=dict(prev_data),
        duration_ms=0,
        step_name="invoke_green_llm",
    )
    assert hasattr(phase_5_implement, "_check_green_token_budget"), (
        "_check_green_token_budget missing — see test 3."
    )
    result = phase_5_implement._check_green_token_budget(None, prev)
    assert result.status == "ok", (
        f"Expected status='ok' for tokens_out=4999 (<5000), got {result.status!r}"
    )
    # Pass-through preservation: the keys downstream needs (raw_response,
    # log_path, spec_path, red_log_path, validation_doc_path, verdict, prompt)
    # must remain on result.data so _write_green_artifact still works.
    for key in ("raw_response", "log_path", "spec_path", "red_log_path",
                "validation_doc_path", "verdict", "prompt"):
        assert key in result.data, (
            f"Pass-through must preserve {key!r}; missing from result.data keys "
            f"{sorted(result.data.keys())!r}"
        )
        assert result.data[key] == prev_data[key], (
            f"Pass-through changed value of {key!r}: "
            f"{prev_data[key]!r} -> {result.data[key]!r}"
        )


# ─── Test 5: gate passes when tokens_out is None ─────────────────────────────


def test_b7442146_check_green_token_budget_passes_when_tokens_out_none():
    """tokens_out=None (stub command without JSON envelope) → pass-through.
    Real claude invocations always have JSON; stubs in tests don't. Gate must
    not punish stubs.

    FAILS today: function does not exist.
    """
    import phase_5_implement  # noqa: PLC0415
    from contracts import StepResult  # noqa: PLC0415

    prev = StepResult(
        status="ok",
        data={
            "tokens_out": None,
            "tokens_in": None,
            "raw_response": "GREEN COMPLETE — all 1 tests passing. Files modified: [x]\n",
            "log_path": "/tmp/x.log",
            "spec_path": "/tmp/spec.md",
            "red_log_path": "/tmp/red.log",
            "validation_doc_path": "/tmp/v.md",
            "verdict": "PASS",
            "prompt": "p",
        },
        duration_ms=0,
        step_name="invoke_green_llm",
    )
    assert hasattr(phase_5_implement, "_check_green_token_budget"), (
        "_check_green_token_budget missing — see test 3."
    )
    result = phase_5_implement._check_green_token_budget(None, prev)
    assert result.status == "ok", (
        f"Expected status='ok' for tokens_out=None (stub case), got {result.status!r}"
    )


# ─── Test 6: boundary — exactly 5000 passes ──────────────────────────────────


def test_b7442146_check_green_token_budget_passes_when_exactly_5000():
    """Boundary: gate fails ONLY if tokens_out > 5000, NOT >= 5000.
    tokens_out=5000 → pass-through.

    FAILS today: function does not exist.
    """
    import phase_5_implement  # noqa: PLC0415
    from contracts import StepResult  # noqa: PLC0415

    prev = StepResult(
        status="ok",
        data={
            "tokens_out": 5000,
            "tokens_in": 100,
            "raw_response": "ok",
            "log_path": "/tmp/x.log",
            "spec_path": "/tmp/spec.md",
            "red_log_path": "/tmp/red.log",
            "validation_doc_path": "/tmp/v.md",
            "verdict": "PASS",
            "prompt": "p",
        },
        duration_ms=0,
        step_name="invoke_green_llm",
    )
    assert hasattr(phase_5_implement, "_check_green_token_budget"), (
        "_check_green_token_budget missing — see test 3."
    )
    result = phase_5_implement._check_green_token_budget(None, prev)
    assert result.status == "ok", (
        f"Boundary: tokens_out=5000 must PASS (gate fires only on >5000), "
        f"got status={result.status!r} error_code={getattr(result, 'error_code', None)!r}"
    )


# ─── Test 4: workflow registers gate between invoke and write ────────────────


def test_b7442146_workflow_includes_check_green_token_budget_between_invoke_and_write():
    """phase_5_implement_workflow() step list must include
    'check_green_token_budget' AFTER 'invoke_green_llm' and BEFORE
    'write_green_artifact'.

    FAILS today: step missing entirely.
    """
    from phase_5_implement import phase_5_implement_workflow  # noqa: PLC0415

    wf = phase_5_implement_workflow()
    names = [s.name for s in wf.steps]

    assert "check_green_token_budget" in names, (
        f"Workflow step list missing 'check_green_token_budget'. "
        f"Got steps: {names!r}. B7442146 must wire it between invoke_green_llm "
        f"and write_green_artifact."
    )
    invoke_idx = names.index("invoke_green_llm")
    gate_idx = names.index("check_green_token_budget")
    write_idx = names.index("write_green_artifact")
    assert invoke_idx < gate_idx < write_idx, (
        f"Step ordering wrong. Expected invoke_green_llm < check_green_token_budget "
        f"< write_green_artifact, got indices: invoke={invoke_idx} gate={gate_idx} "
        f"write={write_idx}. Full sequence: {names!r}"
    )


# ─── Test 5 (DOWNGRADE): gate is alert-only when over budget ────────────────


class _AlertEventLog:
    """Minimal event log capturing (event_type, payload, run_id) tuples."""

    def __init__(self):
        self.events: list[tuple[str, dict, str | None]] = []

    def append(self, event_type: str, payload: dict, run_id: str | None = None) -> None:
        self.events.append((event_type, payload, run_id))


def test_check_green_token_budget_emits_alert_event_when_exceeded():
    """B7442146 downgrade: gate is observability, not enforcement.

    When tokens_out > GREEN_OUTPUT_TOKEN_BUDGET (5000):
      - emits exactly one ``green_token_budget_alert`` event
      - payload == {tokens_out, threshold, exceeded_by}
      - returns StepResult(status='ok', ...)  (NOT 'error')

    No more E_GREEN_VERBOSE / recoverable=False. Tool-IO overhead from
    Write/Edit envelopes naturally inflates tokens_out — hard-gating on it
    blocks legitimate GREEN runs. Root-cause control lives in the GREEN
    prompt RESPONSE BUDGET (228AB822); this gate stays as an ALERT only.

    FAILS today: current code returns status='error' / error_code=E_GREEN_VERBOSE
    and emits no alert event.
    """
    import phase_5_implement  # noqa: PLC0415
    import telemetry_ctx  # noqa: PLC0415
    from contracts import StepResult  # noqa: PLC0415

    prev = StepResult(
        status="ok",
        data={
            "tokens_out": 13043,
            "tokens_in": 200,
            "raw_response": "GREEN COMPLETE — all 1 tests passing. Files modified: [x]\n",
            "log_path": "/tmp/x.log",
            "spec_path": "/tmp/spec.md",
            "red_log_path": "/tmp/red.log",
            "validation_doc_path": "/tmp/v.md",
            "verdict": "PASS",
            "prompt": "p",
        },
        duration_ms=0,
        step_name="invoke_green_llm",
    )

    log = _AlertEventLog()
    telemetry_ctx.set_current_run(
        event_log=log,
        run_id="b7442146-downgrade-test",
        step_name="check_green_token_budget",
        phase="phase_5_implement",
    )
    try:
        result = phase_5_implement._check_green_token_budget(None, prev)
    finally:
        telemetry_ctx.clear_current_run()

    # 1) Status MUST be ok — pipeline continues.
    assert result.status == "ok", (
        f"B7442146 downgrade: gate must NOT block pipeline. "
        f"Expected status='ok', got status={result.status!r} "
        f"error_code={getattr(result, 'error_code', None)!r} "
        f"error={getattr(result, 'error', None)!r}"
    )
    assert getattr(result, "error_code", None) in (None, ""), (
        f"Expected no error_code on alert-only gate; "
        f"got {getattr(result, 'error_code', None)!r}"
    )

    # 2) Exactly one green_token_budget_alert event was emitted.
    alerts = [e for e in log.events if e[0] == "green_token_budget_alert"]
    assert len(alerts) == 1, (
        f"Expected exactly 1 green_token_budget_alert event, got {len(alerts)}. "
        f"All events: {[e[0] for e in log.events]!r}"
    )

    # 3) Payload shape.
    _evt_type, payload, _run_id = alerts[0]
    assert payload == {
        "tokens_out": 13043,
        "threshold": 5000,
        "exceeded_by": 8043,
    }, (
        f"green_token_budget_alert payload mismatch. "
        f"Expected {{tokens_out: 13043, threshold: 5000, exceeded_by: 8043}}, "
        f"got {payload!r}"
    )
