"""RED tests for 477B3143 — phase_5_implement GREEN timeout FEATURE-class bump.

Path (a) per Axis 2a: add DEFAULT_GREEN_TIMEOUT_SEC_FEATURE = 1500 constant and
update _resolve_green_timeout_sec to return 1500 for FEATURE complexity (was 900).

Engine_py self-modification per delegation rule 35F1A4EB (Option D manual flow).
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))


def test_default_green_timeout_sec_feature_constant_exists():
    """AC1 — DEFAULT_GREEN_TIMEOUT_SEC_FEATURE exists and equals 1500 (RED today: ImportError)."""
    from bytedigger_engine.workflows.phase_5_implement import DEFAULT_GREEN_TIMEOUT_SEC_FEATURE  # noqa: PLC0415

    assert DEFAULT_GREEN_TIMEOUT_SEC_FEATURE == 1500


def test_feature_complexity_returns_1500_green():
    """AC2 — _resolve_green_timeout_sec({"complexity": "FEATURE"}) == 1500 (RED today: returns 900)."""
    from bytedigger_engine.workflows.phase_5_implement import DEFAULT_GREEN_TIMEOUT_SEC_FEATURE  # noqa: PLC0415
    from bytedigger_engine.workflows.phase_5_implement import _resolve_green_timeout_sec  # noqa: PLC0415

    assert _resolve_green_timeout_sec({"complexity": "FEATURE"}) == 1500


def test_feature_lowercase_returns_1500_green():
    """AC3 — case-insensitive: "feature" → 1500 (RED today: returns 900)."""
    from bytedigger_engine.workflows.phase_5_implement import DEFAULT_GREEN_TIMEOUT_SEC_FEATURE  # noqa: PLC0415
    from bytedigger_engine.workflows.phase_5_implement import _resolve_green_timeout_sec  # noqa: PLC0415

    assert _resolve_green_timeout_sec({"complexity": "feature"}) == 1500


def test_explicit_green_override_wins_over_feature():
    """AC4 — explicit green_llm_timeout_sec beats FEATURE default (RED today: ImportError on constant)."""
    from bytedigger_engine.workflows.phase_5_implement import DEFAULT_GREEN_TIMEOUT_SEC_FEATURE  # noqa: PLC0415
    from bytedigger_engine.workflows.phase_5_implement import _resolve_green_timeout_sec  # noqa: PLC0415

    assert _resolve_green_timeout_sec({"green_llm_timeout_sec": 7200, "complexity": "FEATURE"}) == 7200
