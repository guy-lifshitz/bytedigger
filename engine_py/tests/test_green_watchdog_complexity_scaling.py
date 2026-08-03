"""RED tests for 9356C4D1 — green_watchdog token multiplier complexity scaling.

Run forge-1778009357-5D4ED78F W1 build wrote 266 LOC across 4 batch files at
37,170 tokens (7.4x budget) and was killed despite ALL 48 RED tests passing
structurally. 5x is too tight for COMPLEX with substantial impl needs.

Required behavior:
1. New cfg key `green_watchdog_token_multiplier` overrides everything (escape hatch).
2. Else: complexity-aware default. cfg["complexity"] == "COMPLEX" (case-insensitive)
   → multiplier 10. Otherwise (SIMPLE / FEATURE / unset) → 5x baseline.

Engine_py self-modification per delegation rule 35F1A4EB (Option D manual flow).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

from bytedigger_engine.contracts import StepResult, WorkflowContext  # noqa: E402


# ─── helpers ──────────────────────────────────────────────────────────────────


def make_ctx(scratchpad: Path, **org_extra) -> WorkflowContext:
    """Build a WorkflowContext with optional org_config overrides."""
    org = {"scratchpad_dir": str(scratchpad), **org_extra}
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question="9356C4D1 test",
        session_id="test-9356C4D1",
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


# ─── Test 1: default 5x multiplier (regression guard) ────────────────────────


def test_default_multiplier_is_5x():
    """No cfg / empty cfg / no complexity / no override → 5x baseline.

    Regression guard — preserves current behavior for non-COMPLEX builds.
    """
    from bytedigger_engine.workflows.phase_5_implement import _resolve_green_watchdog_token_multiplier  # noqa: PLC0415

    assert _resolve_green_watchdog_token_multiplier(None) == 5
    assert _resolve_green_watchdog_token_multiplier({}) == 5


# ─── Test 2: COMPLEX uppercase returns 10x ───────────────────────────────────


def test_complex_complexity_returns_10x():
    """cfg["complexity"] == "COMPLEX" → 10x multiplier (RED today)."""
    from bytedigger_engine.workflows.phase_5_implement import _resolve_green_watchdog_token_multiplier  # noqa: PLC0415

    assert _resolve_green_watchdog_token_multiplier({"complexity": "COMPLEX"}) == 10


# ─── Test 3: complex lowercase returns 10x (case-insensitive) ────────────────


def test_complex_lowercase_also_returns_10x():
    """Case-insensitive — "complex" / "Complex" / "cOmPlEx" all → 10x (RED today)."""
    from bytedigger_engine.workflows.phase_5_implement import _resolve_green_watchdog_token_multiplier  # noqa: PLC0415

    assert _resolve_green_watchdog_token_multiplier({"complexity": "complex"}) == 10
    assert _resolve_green_watchdog_token_multiplier({"complexity": "Complex"}) == 10
    assert _resolve_green_watchdog_token_multiplier({"complexity": "cOmPlEx"}) == 10


# ─── Test 4: non-COMPLEX returns 5x (regression guard) ───────────────────────


def test_simple_complexity_returns_5x():
    """SIMPLE / FEATURE / unknown / missing → 5x (preserves baseline)."""
    from bytedigger_engine.workflows.phase_5_implement import _resolve_green_watchdog_token_multiplier  # noqa: PLC0415

    assert _resolve_green_watchdog_token_multiplier({"complexity": "SIMPLE"}) == 5
    assert _resolve_green_watchdog_token_multiplier({"complexity": "FEATURE"}) == 5
    assert _resolve_green_watchdog_token_multiplier({"complexity": "unknown"}) == 5
    assert _resolve_green_watchdog_token_multiplier({"complexity": ""}) == 5
    assert _resolve_green_watchdog_token_multiplier({"complexity": None}) == 5
    # No complexity key at all
    assert _resolve_green_watchdog_token_multiplier({"other": "stuff"}) == 5


# ─── Test 5: explicit override wins over complexity ──────────────────────────


def test_explicit_override_wins_over_complexity():
    """green_watchdog_token_multiplier overrides complexity-aware default (RED today).

    Orchestrator escape hatch: even on COMPLEX, explicit override is authoritative.
    """
    from bytedigger_engine.workflows.phase_5_implement import _resolve_green_watchdog_token_multiplier  # noqa: PLC0415

    cfg = {"complexity": "COMPLEX", "green_watchdog_token_multiplier": 7}
    assert _resolve_green_watchdog_token_multiplier(cfg) == 7

    # Override also wins on non-COMPLEX
    cfg2 = {"complexity": "SIMPLE", "green_watchdog_token_multiplier": 15}
    assert _resolve_green_watchdog_token_multiplier(cfg2) == 15


# ─── Test 6: explicit override clamps to ≥1 ──────────────────────────────────


def test_explicit_override_clamps_to_minimum_1():
    """Reject 0 / negative — clamp to 1 (RED today).

    Otherwise watchdog would fire immediately on any token output.
    """
    from bytedigger_engine.workflows.phase_5_implement import _resolve_green_watchdog_token_multiplier  # noqa: PLC0415

    assert _resolve_green_watchdog_token_multiplier({"green_watchdog_token_multiplier": 0}) == 1
    assert _resolve_green_watchdog_token_multiplier({"green_watchdog_token_multiplier": -5}) == 1
    assert _resolve_green_watchdog_token_multiplier({"green_watchdog_token_multiplier": -100}) == 1


# ─── Test 7: invalid override falls back to default ──────────────────────────


def test_explicit_override_invalid_falls_back_to_default():
    """Non-numeric / unparseable override → fall through to complexity-aware default (RED today)."""
    from bytedigger_engine.workflows.phase_5_implement import _resolve_green_watchdog_token_multiplier  # noqa: PLC0415

    # No complexity → 5x baseline
    assert _resolve_green_watchdog_token_multiplier(
        {"green_watchdog_token_multiplier": "not-a-number"}
    ) == 5
    assert _resolve_green_watchdog_token_multiplier(
        {"green_watchdog_token_multiplier": [1, 2, 3]}
    ) == 5

    # COMPLEX with invalid override → 10x (complexity default)
    assert _resolve_green_watchdog_token_multiplier(
        {"complexity": "COMPLEX", "green_watchdog_token_multiplier": "bogus"}
    ) == 10


# ─── Test 8: integration — _green_watchdog uses resolved multiplier ──────────


def test_green_watchdog_uses_resolved_multiplier(tmp_path: Path):
    """End-to-end: drive _green_watchdog with COMPLEX cfg + 37170 tokens_out.

    Under 5x baseline (limit=25000), 37170 trips → E_GREEN_WATCHDOG.
    Under 10x COMPLEX scaling (limit=50000), 37170 passes → status="ok".

    Mirrors W1 batch-build forge-1778009357-5D4ED78F failure mode (RED today).
    """
    from bytedigger_engine.workflows.phase_5_implement import _green_watchdog  # noqa: PLC0415

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    ctx = make_ctx(scratchpad, complexity="COMPLEX", green_llm_timeout_sec=600)

    prev = make_prev_green_result(duration_ms=300_000, tokens_out=37_170)
    result = _green_watchdog(ctx, prev)

    assert result.status == "ok", (
        f"COMPLEX should accommodate 37170 tokens (10x=50000); "
        f"got {result.status} {result.error_code}: {result.error}"
    )
    # Data must propagate downstream
    assert result.data["tokens_out"] == 37_170
    assert result.data["duration_ms"] == 300_000


# ─── Test 9 (bonus regression): non-COMPLEX still aborts at 5x ───────────────


def test_green_watchdog_simple_token_overrun_is_nonfatal(tmp_path: Path):
    """SIMPLE token overrun is now a non-fatal ALERT (32C49788), not an abort.

    tokens_out=25_001 exceeds SIMPLE limit (5 * 5000 = 25_000) but duration_ms=1000
    is well under the wall-clock limit (1_200_000 ms for green_llm_timeout_sec=600).
    Post-32C49788: _green_watchdog must return status="ok", error_code=None.
    Token volume is a verbosity metric, not a correctness signal.
    """
    from bytedigger_engine.workflows.phase_5_implement import _green_watchdog  # noqa: PLC0415

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    ctx = make_ctx(scratchpad, complexity="SIMPLE", green_llm_timeout_sec=600)

    # 25001 = just over 5*5000 baseline
    prev = make_prev_green_result(duration_ms=1000, tokens_out=25_001)
    result = _green_watchdog(ctx, prev)

    assert result.status == "ok", (
        f"32C49788: SIMPLE token overrun must be non-fatal (status='ok'), got {result.status!r}"
    )
    assert result.error_code is None, (
        f"32C49788: error_code must be None for non-fatal token alert, got {result.error_code!r}"
    )
