"""Tests for Wave 6 (CRIT #7 + CRIT #8 + HIGH #2): hard-gate chokepoint
inside ``invoke_llm_subprocess``.

Architectural fix being TESTED:
    Move ``_assert_hard_gate_opus`` enforcement from per-workflow opt-in into a
    chokepoint inside ``invoke_llm_subprocess`` via a new ``hard_gate=True`` /
    ``gate_label: str | None`` parameter. Currently 4 of 6 LLM-invoke sites opt
    in correctly; 2 silently skip enforcement. Move to chokepoint =
    N+1 sites become 1 site = pattern un-breakable.

Migration update (23680DDA, 2026-05-06): tests that exercise the gate-pass
path (Test 3 + Test 4) now use a stream-json shaped fixture so the
auto-injected reader can iterate a real result event. Tests that exercise
gate-refusal short-circuit Popen, so their fixtures don't matter.

Closes:
    CRIT #7  — phase_5_integrity haiku-default LLM command + missing gate.
    CRIT #8  — phase_45_spec_lite ``_invoke_review_llm`` missing gate.
    HIGH #2  — run_ctx not threaded → hard_gate_refused never emits.
"""
from __future__ import annotations

import io
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

from bytedigger_engine.llm_subprocess import invoke_llm_subprocess  # noqa: E402
from bytedigger_engine import telemetry_ctx  # noqa: E402


# Single-line stream-json result event — also a valid 1-event stream.
# Fed to the streaming reader on gate-pass paths so a result_event is
# extracted and status="ok" is returned.
_OPUS_OK_JSON = (
    '{"type":"result","subtype":"success","result":"ok","total_cost_usd":0,'
    '"usage":{"input_tokens":1,"output_tokens":1,'
    '"cache_read_input_tokens":0,"cache_creation_input_tokens":0}}\n'
)


class _FakeEventLog:
    def __init__(self):
        self.events: list[tuple[str, dict, str]] = []

    def append(self, event_type: str, payload: dict, run_id: str | None = None) -> None:
        self.events.append((event_type, payload, run_id or "ad-hoc"))


def _mock_proc(stdout: str = "ok", returncode: int = 0):
    """Stream-json shaped Popen mock — proc.stdout iterates the given text
    line-by-line so the streaming reader extracts events. (Despite the
    legacy name, this returns a stream-json fixture; renaming would require
    test churn elsewhere.)"""
    proc = MagicMock()
    proc.pid = 24601
    proc.returncode = returncode
    proc.stdout = io.StringIO(stdout)
    proc.stderr = io.StringIO("")
    proc.stdin = MagicMock()
    proc.wait = MagicMock(return_value=returncode)
    proc.communicate.return_value = (stdout, "")
    return proc


# ─── 1. claude -p without --model is refused under hard_gate ──────────────────


def test_hard_gate_param_refuses_claude_p_without_model():
    """hard_gate=True must refuse a non-Opus model with E_HARD_GATE_*
    AND must NOT spawn the subprocess.

    Migration (25e75663): original test used command=["claude","-p"] (no --model).
    New seam requires model= string. Use model="sonnet" — a non-Opus model —
    which is still refused by hard_gate, preserving the test's intent.
    """
    with patch("bytedigger_engine.llm_subprocess.subprocess.Popen") as mock_popen:
        result = invoke_llm_subprocess(
            prompt="x",
            model="sonnet",
            timeout_sec=1,
            step_name="s",
            hard_gate=True,
            gate_label="validation",
        )
    assert result.status == "error", f"expected error, got {result.status!r}"
    assert result.error_code == "E_HARD_GATE_MODEL_DOWNGRADE", (
        f"expected E_HARD_GATE_MODEL_DOWNGRADE, got {result.error_code!r}"
    )
    assert result.recoverable is False, (
        f"hard gate must be non-recoverable, got recoverable={result.recoverable!r}"
    )
    mock_popen.assert_not_called()


# ─── 2. explicit non-Opus model is refused under hard_gate ────────────────────


def test_hard_gate_param_refuses_explicit_non_opus():
    """hard_gate=True must refuse explicit non-Opus model (e.g. sonnet) without spawning."""
    # R1: command=["claude","-p","--model","sonnet"] -> model="sonnet"
    with patch("bytedigger_engine.llm_subprocess.subprocess.Popen") as mock_popen:
        result = invoke_llm_subprocess(
            prompt="x",
            model="sonnet",
            timeout_sec=1,
            step_name="s",
            hard_gate=True,
            gate_label="validation",
        )
    assert result.status == "error"
    assert result.error_code == "E_HARD_GATE_MODEL_DOWNGRADE"
    assert result.recoverable is False
    mock_popen.assert_not_called()


# ─── 3. explicit Opus passes hard_gate and spawns ─────────────────────────────


def test_hard_gate_param_passes_opus():
    """hard_gate=True with Opus model must pass the gate AND spawn subprocess."""
    # R1: command=["claude","-p","--model","claude-opus-4-7"] -> model="claude-opus-4-7"
    with patch("bytedigger_engine.llm_subprocess.subprocess.Popen") as mock_popen:
        mock_popen.return_value = _mock_proc(stdout=_OPUS_OK_JSON)
        result = invoke_llm_subprocess(
            prompt="x",
            model="claude-opus-4-7",
            timeout_sec=10,
            step_name="s",
            hard_gate=True,
            gate_label="validation",
        )
    assert result.status == "ok", f"expected ok, got status={result.status!r} err={result.error!r}"
    mock_popen.assert_called_once()


# ─── 4. default (no hard_gate kw) preserves existing behavior — backward compat


def test_hard_gate_default_off_preserves_existing_behavior():
    """Explicit hard_gate=False AND haiku command: subprocess must spawn,
    no E_HARD_GATE error.

    Why this is a RED test (must fail today): explicit ``hard_gate=False``
    is rejected by current ``invoke_llm_subprocess`` with TypeError because
    the keyword does not exist. Once the chokepoint adds ``hard_gate`` with
    default False, this test verifies the off-path stays a no-op for the
    haiku-default backward-compat path.
    """
    # R1: command=["claude","-p","--model","haiku"] -> model="haiku"
    with patch("bytedigger_engine.llm_subprocess.subprocess.Popen") as mock_popen:
        mock_popen.return_value = _mock_proc(stdout=_OPUS_OK_JSON)
        result = invoke_llm_subprocess(
            prompt="x",
            model="haiku",
            timeout_sec=10,
            step_name="s",
            hard_gate=False,
            gate_label=None,
        )
    assert result.status == "ok", (
        f"backward-compat broken: hard_gate=False errored with "
        f"{result.error_code!r} {result.error!r}"
    )
    mock_popen.assert_called_once()


# ─── 5. hard_gate emits hard_gate_refused with active run_ctx (HIGH #2) ───────


def test_hard_gate_emits_telemetry_with_active_run_ctx():
    """With telemetry_ctx active run AND hard_gate=True + non-Opus, the
    chokepoint must emit a `hard_gate_refused` event with run_ctx fields
    {phase, step_name, gate_label, observed_model, command_kind}.

    THIS IS HIGH #2 — currently impossible to fire because workflows pass
    run_ctx=None (and the chokepoint doesn't even gate yet).
    """
    log = _FakeEventLog()
    telemetry_ctx.set_current_run(
        event_log=log, run_id="r-w6", step_name="step-x", phase="phase_5_integrity"
    )
    try:
        with patch("bytedigger_engine.llm_subprocess.subprocess.Popen") as mock_popen:
            invoke_llm_subprocess(
                prompt="x",
                model="sonnet",
                timeout_sec=1,
                step_name="step-x",
                hard_gate=True,
                gate_label="validation",
            )
        mock_popen.assert_not_called()
    finally:
        telemetry_ctx.clear_current_run()

    refused = [e for e in log.events if e[0] == "hard_gate_refused"]
    assert len(refused) == 1, (
        f"expected exactly 1 hard_gate_refused event, got {len(refused)}: "
        f"{[e[0] for e in log.events]!r}"
    )
    payload = refused[0][1]
    for key in ("phase", "step_name", "gate_label", "observed_model", "command_kind"):
        assert key in payload, f"hard_gate_refused payload missing key {key!r}: {payload!r}"
    assert payload["gate_label"] == "validation"
    assert payload["observed_model"] == "sonnet"
    assert payload["command_kind"] == "claude_p"
    assert payload["phase"] == "phase_5_integrity"
    assert payload["step_name"] == "step-x"


# ─── 6. no telemetry_ctx → no emission, but still error (observability never breaks)


def test_hard_gate_no_telemetry_without_run_ctx():
    """Without an active telemetry_ctx run, hard_gate refusal must still
    return E_HARD_GATE_* but emission must be silently skipped (no crash).
    """
    telemetry_ctx.clear_current_run()  # belt + suspenders
    # R1: command=["claude","-p","--model","sonnet"] -> model="sonnet"
    with patch("bytedigger_engine.llm_subprocess.subprocess.Popen") as mock_popen:
        result = invoke_llm_subprocess(
            prompt="x",
            model="sonnet",
            timeout_sec=1,
            step_name="s",
            hard_gate=True,
            gate_label="validation",
        )
    assert result.status == "error"
    assert result.error_code == "E_HARD_GATE_MODEL_DOWNGRADE"
    mock_popen.assert_not_called()


# ─── 7. CRIT #7: phase_5_integrity default haiku is now refused via chokepoint


def test_hard_gate_chokepoint_phase_5_integrity_default_haiku_now_refused():
    """Forward-looking integration test — expected to fail today.

    Currently phase_5_integrity:
      - DEFAULT_LLM_COMMAND = ["claude", "-p", "--model", "haiku"]   (line ~100)
      - _invoke_integrity_llm calls invoke_llm_subprocess WITHOUT hard_gate=True
      - net effect: a haiku-tier model silently classifies test-integrity diffs

    After the chokepoint fix lands AND phase_5_integrity opts in
    (hard_gate=True), this default path MUST refuse with E_HARD_GATE_*.

    Test strategy: monkeypatch invoke_llm_subprocess to capture kwargs the
    workflow passes through, then verify (a) hard_gate=True is passed and
    (b) when run for real with default haiku command + hard_gate=True, the
    chokepoint refuses (no Popen).
    """
    from bytedigger_engine.contracts import StepResult, WorkflowContext
    from bytedigger_engine.workflows import phase_5_integrity as p5i

    # Build a minimal ctx with scratchpad_dir set; no real disk ops needed
    # because we stub the prev step.
    tmp = HERE / "_w6_tmp_p5i"
    tmp.mkdir(exist_ok=True)
    ctx = WorkflowContext(
        tenant_id="t",
        scope=None,
        db_path=None,
        org_config={"scratchpad_dir": str(tmp)},
        question="q",
        session_id="s",
        persona="analyst",
        framework=None,
        domain=None,
    )
    prev = StepResult(
        status="ok",
        data={
            "prompt": "p",
            "doc_path": str(tmp / "review.md"),
            "diff_path": str(tmp / "diff.patch"),
        },
        duration_ms=0,
        step_name="build_integrity_prompt",
    )

    captured: dict = {}

    def fake_invoke(**kw):
        captured.update(kw)
        # Simulate chokepoint refusal so caller sees the hard-gate behavior.
        return StepResult(
            status="error",
            data=None,
            duration_ms=0,
            step_name=kw.get("step_name", "?"),
            error="refused",
            error_code="E_HARD_GATE_MODEL_DOWNGRADE",
            recoverable=False,
        )

    with patch.object(p5i, "invoke_llm_subprocess", side_effect=fake_invoke):
        result = p5i._invoke_integrity_llm(ctx, prev)

    assert captured.get("hard_gate") is True, (
        "phase_5_integrity._invoke_integrity_llm must pass hard_gate=True to "
        f"invoke_llm_subprocess, got kw={list(captured.keys())!r}"
    )
    assert result.error_code == "E_HARD_GATE_MODEL_DOWNGRADE"


# ─── 8. CRIT #8: phase_45_spec_lite._invoke_review_llm missing gate


def test_hard_gate_chokepoint_phase_45_spec_lite_missing_gate_now_refused():
    """Forward-looking integration test — expected to fail today.

    phase_45_spec_lite._invoke_review_llm calls invoke_llm_subprocess for the
    SIMPLE-tier spec reviewer (Opus by default config, but no hard_gate
    enforcement → silent downgrade if config sets sonnet/haiku).

    After the chokepoint fix lands AND phase_45_spec_lite opts in
    (hard_gate=True), this path must pass the kwarg.
    """
    from bytedigger_engine.contracts import StepResult, WorkflowContext
    from bytedigger_engine.workflows import phase_45_spec_lite as p45

    tmp = HERE / "_w6_tmp_p45"
    tmp.mkdir(exist_ok=True)
    ctx = WorkflowContext(
        tenant_id="t",
        scope=None,
        db_path=None,
        org_config={"scratchpad_dir": str(tmp)},
        question="q",
        session_id="s",
        persona="analyst",
        framework=None,
        domain=None,
    )
    prev = StepResult(
        status="ok",
        data={
            "prompt": "p",
            "doc_path": str(tmp / "review.md"),
            "spec_path": str(tmp / "spec.md"),
            "cycle": 1,
        },
        duration_ms=0,
        step_name="build_review_prompt",
    )

    captured: dict = {}

    def fake_invoke(**kw):
        captured.update(kw)
        return StepResult(
            status="ok",
            data={"raw_response": "VERDICT: SHIP", "doc_path": kw.get("extra_data", {}).get("doc_path", "")},
            duration_ms=0,
            step_name=kw.get("step_name", "?"),
        )

    with patch.object(p45, "invoke_llm_subprocess", side_effect=fake_invoke):
        p45._invoke_review_llm(ctx, prev)

    assert captured.get("hard_gate") is True, (
        "phase_45_spec_lite._invoke_review_llm must pass hard_gate=True to "
        f"invoke_llm_subprocess, got kw={list(captured.keys())!r}"
    )
    # Reviewer is the gate's whole point — gate label must distinguish from
    # validation gate to keep telemetry separable.
    gl = captured.get("gate_label")
    assert gl, "phase_45_spec_lite must pass a non-empty gate_label"
