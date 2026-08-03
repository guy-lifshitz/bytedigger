"""RED tests for D3F7B377 — phase_5_implement validation + GREEN timeout complexity scaling.

DEFAULT_VALIDATION_TIMEOUT_SEC=600 too tight for COMPLEX validation (Opus auditing
large RED tests at scale).

DEFAULT_GREEN_TIMEOUT_SEC=900 too tight for COMPLEX GREEN (200+ line impls).

Required behavior (mirrors 80CC602D phase_45_spec scaling pattern):
1. New cfg key `validation_llm_timeout_sec` / `green_llm_timeout_sec` overrides everything.
2. Else: complexity-aware default. cfg["complexity"] == "COMPLEX" (case-insensitive)
   → validation=1200, green=1800. Otherwise (SIMPLE / FEATURE / unset) → 600/900 baseline.

Engine_py self-modification per delegation rule 35F1A4EB (Option D manual flow).
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))


# ─── VALIDATION TIMEOUT ───────────────────────────────────────────────────────


def test_default_validation_timeout_is_600():
    """No cfg / empty cfg → 600s baseline (regression guard)."""
    from bytedigger_engine.workflows.phase_5_implement import _resolve_validation_timeout_sec  # noqa: PLC0415

    assert _resolve_validation_timeout_sec(None) == 600
    assert _resolve_validation_timeout_sec({}) == 600


def test_complex_complexity_returns_1200_validation():
    """cfg["complexity"] == "COMPLEX" → 1200s (RED today)."""
    from bytedigger_engine.workflows.phase_5_implement import _resolve_validation_timeout_sec  # noqa: PLC0415

    assert _resolve_validation_timeout_sec({"complexity": "COMPLEX"}) == 1200


def test_complex_lowercase_returns_1200_validation():
    """Case-insensitive — "complex" → 1200 (RED today)."""
    from bytedigger_engine.workflows.phase_5_implement import _resolve_validation_timeout_sec  # noqa: PLC0415

    assert _resolve_validation_timeout_sec({"complexity": "complex"}) == 1200


def test_simple_complexity_returns_600_validation():
    """SIMPLE / FEATURE → 600s baseline (regression guard)."""
    from bytedigger_engine.workflows.phase_5_implement import _resolve_validation_timeout_sec  # noqa: PLC0415

    assert _resolve_validation_timeout_sec({"complexity": "SIMPLE"}) == 600
    assert _resolve_validation_timeout_sec({"complexity": "FEATURE"}) == 600


def test_explicit_validation_override_wins():
    """validation_llm_timeout_sec overrides complexity-aware default (RED today)."""
    from bytedigger_engine.workflows.phase_5_implement import _resolve_validation_timeout_sec  # noqa: PLC0415

    assert _resolve_validation_timeout_sec({"validation_llm_timeout_sec": 1234}) == 1234


def test_invalid_validation_override_falls_through_to_complexity_default():
    """Non-numeric override → fall through to complexity-aware default (RED today)."""
    from bytedigger_engine.workflows.phase_5_implement import _resolve_validation_timeout_sec  # noqa: PLC0415

    assert _resolve_validation_timeout_sec(
        {"validation_llm_timeout_sec": "abc", "complexity": "COMPLEX"}
    ) == 1200


def test_validation_override_clamps_negative_to_one():
    """Negative override clamps to 1 (RED today)."""
    from bytedigger_engine.workflows.phase_5_implement import _resolve_validation_timeout_sec  # noqa: PLC0415

    assert _resolve_validation_timeout_sec({"validation_llm_timeout_sec": -5}) == 1


def test_validation_override_zero_falls_through_to_default():
    """0 is falsy in `if raw:` so falls to default 600 (RED today)."""
    from bytedigger_engine.workflows.phase_5_implement import _resolve_validation_timeout_sec  # noqa: PLC0415

    assert _resolve_validation_timeout_sec({"validation_llm_timeout_sec": 0}) == 600


def test_validation_override_zero_with_complex_falls_through_to_complex_default():
    """0 falsy → fall through; with COMPLEX → 1200 (RED today)."""
    from bytedigger_engine.workflows.phase_5_implement import _resolve_validation_timeout_sec  # noqa: PLC0415

    assert _resolve_validation_timeout_sec(
        {"validation_llm_timeout_sec": 0, "complexity": "COMPLEX"}
    ) == 1200


# ─── GREEN TIMEOUT ────────────────────────────────────────────────────────────


def test_default_green_timeout_is_900():
    """No cfg / empty cfg → 900s baseline (regression guard)."""
    from bytedigger_engine.workflows.phase_5_implement import _resolve_green_timeout_sec  # noqa: PLC0415

    assert _resolve_green_timeout_sec(None) == 900
    assert _resolve_green_timeout_sec({}) == 900


def test_complex_complexity_returns_1800_green():
    """cfg["complexity"] == "COMPLEX" → 1800s (RED today)."""
    from bytedigger_engine.workflows.phase_5_implement import _resolve_green_timeout_sec  # noqa: PLC0415

    assert _resolve_green_timeout_sec({"complexity": "COMPLEX"}) == 1800


def test_simple_complexity_returns_900_green():
    """SIMPLE → 900s baseline (regression guard). FEATURE → 1500 covered in test_phase_5_implement_477B3143.py."""
    from bytedigger_engine.workflows.phase_5_implement import _resolve_green_timeout_sec  # noqa: PLC0415

    assert _resolve_green_timeout_sec({"complexity": "SIMPLE"}) == 900


def test_explicit_green_override_wins():
    """green_llm_timeout_sec overrides complexity-aware default (RED today)."""
    from bytedigger_engine.workflows.phase_5_implement import _resolve_green_timeout_sec  # noqa: PLC0415

    assert _resolve_green_timeout_sec({"green_llm_timeout_sec": 7200}) == 7200
