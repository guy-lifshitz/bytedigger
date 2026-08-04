"""RED tests for 80CC602D — phase_45_spec timeout complexity scaling.

DEFAULT_REVIEW_TIMEOUT_SEC=300 too short for COMPLEX with large specs. Hit
exact 300s SIGKILL on forge-1777986051-b6619743 cycle 2 (Opus reviewer on
27KB+193-line spec with 773-line decision_doc context).

DEFAULT_SPEC_TIMEOUT_SEC=600 likewise too tight for COMPLEX writer.

Required behavior (mirrors 9356C4D1 watchdog scaling pattern):
1. New cfg key `spec_llm_timeout_sec` / `review_llm_timeout_sec` overrides everything.
2. Else: complexity-aware default. cfg["complexity"] == "COMPLEX" (case-insensitive)
   → spec=1800, review=900. Otherwise (SIMPLE / FEATURE / unset) → 600/300 baseline.

Engine_py self-modification per delegation rule 35F1A4EB (Option D manual flow).
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))


# ─── SPEC TIMEOUT ─────────────────────────────────────────────────────────────


def test_default_spec_timeout_is_600():
    """No cfg / empty cfg → 600s baseline (regression guard)."""
    from bytedigger_engine.workflows.phase_45_spec import _resolve_spec_timeout_sec  # noqa: PLC0415

    assert _resolve_spec_timeout_sec(None) == 600
    assert _resolve_spec_timeout_sec({}) == 600


def test_complex_complexity_returns_1800_spec():
    """cfg["complexity"] == "COMPLEX" → 1800s (RED today)."""
    from bytedigger_engine.workflows.phase_45_spec import _resolve_spec_timeout_sec  # noqa: PLC0415

    assert _resolve_spec_timeout_sec({"complexity": "COMPLEX"}) == 1800


def test_simple_complexity_returns_600_spec():
    """SIMPLE / FEATURE → 600s baseline (regression guard)."""
    from bytedigger_engine.workflows.phase_45_spec import _resolve_spec_timeout_sec  # noqa: PLC0415

    assert _resolve_spec_timeout_sec({"complexity": "SIMPLE"}) == 600
    assert _resolve_spec_timeout_sec({"complexity": "FEATURE"}) == 600
    assert _resolve_spec_timeout_sec({"complexity": "unknown"}) == 600
    assert _resolve_spec_timeout_sec({"complexity": ""}) == 600
    assert _resolve_spec_timeout_sec({"complexity": None}) == 600
    assert _resolve_spec_timeout_sec({"other": "stuff"}) == 600


def test_explicit_spec_override_wins():
    """spec_llm_timeout_sec overrides complexity-aware default (RED today)."""
    from bytedigger_engine.workflows.phase_45_spec import _resolve_spec_timeout_sec  # noqa: PLC0415

    cfg = {"complexity": "COMPLEX", "spec_llm_timeout_sec": 1234}
    assert _resolve_spec_timeout_sec(cfg) == 1234

    # Override also wins on non-COMPLEX
    cfg2 = {"complexity": "SIMPLE", "spec_llm_timeout_sec": 999}
    assert _resolve_spec_timeout_sec(cfg2) == 999


def test_invalid_spec_override_falls_through_to_complexity_default():
    """Non-numeric override → fall through to complexity-aware default (RED today)."""
    from bytedigger_engine.workflows.phase_45_spec import _resolve_spec_timeout_sec  # noqa: PLC0415

    # COMPLEX with invalid override → 1800
    assert _resolve_spec_timeout_sec(
        {"complexity": "COMPLEX", "spec_llm_timeout_sec": "abc"}
    ) == 1800
    # No complexity → 600 baseline
    assert _resolve_spec_timeout_sec(
        {"spec_llm_timeout_sec": "not-a-number"}
    ) == 600


def test_spec_override_clamps_to_min_1():
    """Zero is falsy in `if raw:` so falls to default; negative clamps to 1."""
    from bytedigger_engine.workflows.phase_45_spec import _resolve_spec_timeout_sec  # noqa: PLC0415

    # 0 is falsy → falls through to default (600)
    assert _resolve_spec_timeout_sec({"spec_llm_timeout_sec": 0}) == 600
    # negative → max(1, -5) == 1
    assert _resolve_spec_timeout_sec({"spec_llm_timeout_sec": -5}) == 1
    assert _resolve_spec_timeout_sec({"spec_llm_timeout_sec": -100}) == 1


# ─── REVIEW TIMEOUT ───────────────────────────────────────────────────────────


def test_default_review_timeout_is_300():
    """No cfg / empty cfg → 300s baseline (regression guard)."""
    from bytedigger_engine.workflows.phase_45_spec import _resolve_review_timeout_sec  # noqa: PLC0415

    assert _resolve_review_timeout_sec(None) == 300
    assert _resolve_review_timeout_sec({}) == 300


def test_complex_complexity_returns_900_review():
    """cfg["complexity"] == "COMPLEX" → 900s (RED today)."""
    from bytedigger_engine.workflows.phase_45_spec import _resolve_review_timeout_sec  # noqa: PLC0415

    assert _resolve_review_timeout_sec({"complexity": "COMPLEX"}) == 900


def test_simple_complexity_returns_300_review():
    """SIMPLE / FEATURE → 300s baseline (regression guard)."""
    from bytedigger_engine.workflows.phase_45_spec import _resolve_review_timeout_sec  # noqa: PLC0415

    assert _resolve_review_timeout_sec({"complexity": "SIMPLE"}) == 300
    assert _resolve_review_timeout_sec({"complexity": "FEATURE"}) == 600  # D1F51D7A: FEATURE now scaled to 600s
    assert _resolve_review_timeout_sec({"complexity": "unknown"}) == 300
    assert _resolve_review_timeout_sec({"complexity": ""}) == 300
    assert _resolve_review_timeout_sec({"complexity": None}) == 300
    assert _resolve_review_timeout_sec({"other": "stuff"}) == 300


def test_explicit_review_override_wins():
    """review_llm_timeout_sec overrides complexity-aware default (RED today)."""
    from bytedigger_engine.workflows.phase_45_spec import _resolve_review_timeout_sec  # noqa: PLC0415

    cfg = {"complexity": "COMPLEX", "review_llm_timeout_sec": 777}
    assert _resolve_review_timeout_sec(cfg) == 777

    # Override also wins on non-COMPLEX
    cfg2 = {"complexity": "SIMPLE", "review_llm_timeout_sec": 450}
    assert _resolve_review_timeout_sec(cfg2) == 450


def test_invalid_review_override_falls_through_to_complexity_default():
    """Non-numeric override → fall through to complexity-aware default (RED today)."""
    from bytedigger_engine.workflows.phase_45_spec import _resolve_review_timeout_sec  # noqa: PLC0415

    # COMPLEX with invalid override → 900
    assert _resolve_review_timeout_sec(
        {"complexity": "COMPLEX", "review_llm_timeout_sec": "abc"}
    ) == 900
    # No complexity → 300 baseline
    assert _resolve_review_timeout_sec(
        {"review_llm_timeout_sec": [1, 2, 3]}
    ) == 300


def test_review_override_clamps_to_min_1():
    """Zero is falsy in `if raw:` so falls to default; negative clamps to 1."""
    from bytedigger_engine.workflows.phase_45_spec import _resolve_review_timeout_sec  # noqa: PLC0415

    # 0 is falsy → falls through to default (300)
    assert _resolve_review_timeout_sec({"review_llm_timeout_sec": 0}) == 300
    # negative → max(1, -5) == 1
    assert _resolve_review_timeout_sec({"review_llm_timeout_sec": -5}) == 1
    assert _resolve_review_timeout_sec({"review_llm_timeout_sec": -100}) == 1


# ─── BONUS: case-insensitive complexity ───────────────────────────────────────


def test_complexity_lowercase_also_recognized():
    """Case-insensitive — "complex" / "Complex" → 1800 spec, 900 review (RED today)."""
    from bytedigger_engine.workflows.phase_45_spec import (  # noqa: PLC0415
        _resolve_review_timeout_sec,
        _resolve_spec_timeout_sec,
    )

    assert _resolve_spec_timeout_sec({"complexity": "complex"}) == 1800
    assert _resolve_spec_timeout_sec({"complexity": "Complex"}) == 1800
    assert _resolve_spec_timeout_sec({"complexity": "cOmPlEx"}) == 1800

    assert _resolve_review_timeout_sec({"complexity": "complex"}) == 900
    assert _resolve_review_timeout_sec({"complexity": "Complex"}) == 900
    assert _resolve_review_timeout_sec({"complexity": "cOmPlEx"}) == 900
