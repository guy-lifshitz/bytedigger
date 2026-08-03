"""RED tests for F96D3539 — GREEN watchdog + cwd_preflight.

2026-04-27 incident: $87 GREEN cycle, 967s wall, 189 messages. After worktree
was destroyed mid-run, every Bash returned "directory does not exist" but LLM
never abandoned. Quadratic cost growth.

Design: two new steps in phase_5_implement workflow:

1. **cwd_preflight** (before invoke_green_llm) — checks ctx.git_cwd exists on disk.
   If not, returns E_GREEN_CWD_GONE (recoverable=False) to short-circuit GREEN.

2. **green_watchdog** (after invoke_green_llm, before check_green_token_budget) —
   examines prev.data["duration_ms"] and prev.data["tokens_out"]. Aborts with
   E_GREEN_WATCHDOG (recoverable=False) when EITHER:
   - duration_ms >= 2 * timeout_sec * 1000 (wall-clock 2x SLA)
   - tokens_out >= 5 * GREEN_OUTPUT_TOKEN_BUDGET (token blow-up 5x)

Tests (RED-first per 35F1A4EB Option D):
1. test_cwd_preflight_aborts_when_git_cwd_missing
2. test_green_watchdog_aborts_on_2x_wall_clock
3. test_green_watchdog_aborts_on_5x_token_blowup
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

from bytedigger_engine.contracts import StepResult, WorkflowContext  # noqa: E402


# ─── helpers ──────────────────────────────────────────────────────────────────


def make_ctx(scratchpad: Path, **org_extra) -> WorkflowContext:
    """Build a WorkflowContext with optional git_cwd override."""
    org = {"scratchpad_dir": str(scratchpad), **org_extra}
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question="F96D3539 test",
        session_id="test-f96d3539",
        persona="hal",
        framework=None,
        domain=None,
    )


def make_prev_green_result(duration_ms: int = 1000, tokens_out: int = 100) -> StepResult:
    """Build a fake prev StepResult shaped like invoke_green_llm output."""
    return StepResult(
        status="ok",
        data={
            "duration_ms": duration_ms,
            "tokens_out": tokens_out,
            "log_path": "/tmp/test/tests/build-green-output.log",
            "spec_path": "/tmp/test/specs/build-spec.md",
            "red_log_path": "/tmp/test/tests/build-red-output.log",
            "validation_doc_path": "/tmp/test/reviews/build-opus-validation.md",
            "verdict": "PASS",
        },
        duration_ms=0,
        step_name="invoke_green_llm",
    )


# ─── Test 1: cwd_preflight aborts when git_cwd missing ─────────────────────────


def test_cwd_preflight_aborts_when_git_cwd_missing(tmp_path: Path):
    """cwd_preflight step must check ctx.git_cwd exists on disk.

    If not, returns StepResult with error_code="E_GREEN_CWD_GONE",
    status="error", recoverable=False.

    RED PHASE: This test FAILs because cwd_preflight step doesn't exist yet.
    """
    from bytedigger_engine.workflows.phase_5_implement import phase_5_implement_workflow  # noqa: PLC0415

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)

    # Set git_cwd to a path that does NOT exist
    ctx = make_ctx(scratchpad, git_cwd="/tmp/this-does-not-exist-F96D3539")

    # Get workflow and find cwd_preflight step
    wf = phase_5_implement_workflow()
    steps_by_name = {s.name: s for s in wf.steps}

    # Step should exist — RED signals failure when it doesn't
    step = steps_by_name["cwd_preflight"]  # KeyError here = test FAILS (expected in RED)
    prev_result = StepResult(
        status="ok", data={}, duration_ms=0, step_name="build_green_prompt"
    )
    result = step.execute(ctx, prev_result)
    assert result.error_code == "E_GREEN_CWD_GONE"
    assert result.status == "error"
    assert result.recoverable is False


# ─── Test 2: green_watchdog aborts on 2x wall-clock ────────────────────────────


def test_green_watchdog_aborts_on_2x_wall_clock(tmp_path: Path):
    """green_watchdog step must abort when duration_ms >= 2 * timeout_sec * 1000.

    Typical timeout: 900s (DEFAULT_GREEN_TIMEOUT_SEC). 2x = 1800s.
    Test duration: 2400s (well over 2x).

    Returns StepResult with error_code="E_GREEN_WATCHDOG_ESCALATE", status="escalate",
    recoverable=False, and error message mentioning "wall" or "duration".

    RED PHASE: This test FAILs because green_watchdog step doesn't exist yet.
    """
    from bytedigger_engine.workflows.phase_5_implement import phase_5_implement_workflow  # noqa: PLC0415

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    ctx = make_ctx(scratchpad, green_llm_timeout_sec=900)

    # Get workflow and find green_watchdog step
    wf = phase_5_implement_workflow()
    steps_by_name = {s.name: s for s in wf.steps}

    # Step should exist — RED signals failure when it doesn't
    step = steps_by_name["green_watchdog"]  # KeyError here = test FAILS (expected in RED)
    prev_result = make_prev_green_result(duration_ms=2_400_000, tokens_out=100)
    result = step.execute(ctx, prev_result)
    assert result.error_code == "E_GREEN_WATCHDOG_ESCALATE"
    assert result.status == "escalate"
    assert result.recoverable is False
    assert "wall" in result.error.lower() or "duration" in result.error.lower()


# ─── Test 3: green_watchdog token overrun is non-fatal ALERT (32C49788) ──────────


def test_green_watchdog_token_overrun_is_nonfatal_alert(tmp_path: Path):
    """post-commit token overrun is a non-fatal ALERT (not abort), per 32C49788.

    GREEN_OUTPUT_TOKEN_BUDGET = 5000; SIMPLE multiplier = 5x → token_limit = 25_000.
    tokens_out=25_001 trips the threshold but duration_ms=1000 is healthy (wall_limit
    = 1_200_000 ms for green_llm_timeout_sec=600).

    Post-32C49788: _green_watchdog must return status="ok", error_code=None,
    and pass through prev.data (tokens_out==25_001 preserved).
    Token volume is a verbosity metric, not a correctness signal — the wall-clock
    branch already covers genuine runaway.
    """
    from bytedigger_engine.workflows.phase_5_implement import phase_5_implement_workflow  # noqa: PLC0415

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    ctx = make_ctx(scratchpad, green_llm_timeout_sec=600)

    # Get workflow and find green_watchdog step
    wf = phase_5_implement_workflow()
    steps_by_name = {s.name: s for s in wf.steps}

    step = steps_by_name["green_watchdog"]
    prev_result = make_prev_green_result(duration_ms=1000, tokens_out=25_001)
    result = step.execute(ctx, prev_result)
    assert result.status == "ok", (
        f"32C49788: token overrun must be non-fatal (status='ok'), got {result.status!r}"
    )
    assert result.error_code is None, (
        f"32C49788: error_code must be None for non-fatal token alert, got {result.error_code!r}"
    )
    assert result.data is not None and result.data.get("tokens_out") == 25_001, (
        f"32C49788: prev.data must pass through; tokens_out expected 25_001, "
        f"got {result.data.get('tokens_out') if result.data else None!r}"
    )


# ─── Test 4: green_watchdog passes through when both metrics under threshold ──


def test_green_watchdog_passes_when_under_thresholds(tmp_path: Path):
    """green_watchdog must NOT abort when duration_ms and tokens_out are healthy.

    Prevents a trivially-passing GREEN that returns E_GREEN_WATCHDOG unconditionally.
    Also pins data forwarding contract (downstream check_green_token_budget reads
    prev.data — watchdog must propagate, not swallow).

    RED PHASE: This test FAILs because green_watchdog step doesn't exist yet.
    """
    from bytedigger_engine.workflows.phase_5_implement import phase_5_implement_workflow  # noqa: PLC0415

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    ctx = make_ctx(scratchpad, green_llm_timeout_sec=900)

    wf = phase_5_implement_workflow()
    steps_by_name = {s.name: s for s in wf.steps}

    step = steps_by_name["green_watchdog"]  # KeyError here = test FAILS (expected in RED)
    prev_result = make_prev_green_result(duration_ms=1000, tokens_out=100)
    result = step.execute(ctx, prev_result)
    assert result.status == "ok"
    assert result.error_code is None
    # Data must propagate so downstream steps see invoke_green_llm output
    assert result.data["duration_ms"] == 1000
    assert result.data["tokens_out"] == 100
