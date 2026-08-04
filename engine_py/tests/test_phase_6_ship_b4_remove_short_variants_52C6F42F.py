"""RED tests for Ship B4 / agreement 52C6F42F — remove unused short variants.

Spec: SHARED/memory/Decisions/2026-05-15_52C6F42F_ship_b4_remove_short_variants_spec.md
"""
import importlib
import inspect
import sys
from pathlib import Path

import pytest

_ENGINE_PY_ROOT = Path(__file__).resolve().parents[1]  # engine_py/

_HELPER_PATH = _ENGINE_PY_ROOT / "bytedigger_engine/lib" / "plugins" / "anti_hallucination" / "helper.py"
_RUBRIC_TRIM_TEST_PATH = _ENGINE_PY_ROOT / "tests" / "test_phase_6_rubric_trim_5D0D3BD1.py"


def test_prompt_fragment_short_removed_from_helper_source():
    src = _HELPER_PATH.read_text(encoding="utf-8")
    assert "_PROMPT_FRAGMENT_SHORT" not in src, \
        "AC1: _PROMPT_FRAGMENT_SHORT must be deleted from helper.py source"


def test_behavioral_assertion_rubric_short_removed_from_helper_source():
    src = _HELPER_PATH.read_text(encoding="utf-8")
    assert "_BEHAVIORAL_ASSERTION_RUBRIC_SHORT" not in src, \
        "AC2: _BEHAVIORAL_ASSERTION_RUBRIC_SHORT must be deleted from helper.py source"


def test_prompt_fragment_short_not_importable():
    from bytedigger_engine.lib.plugins.anti_hallucination import helper as _h
    importlib.reload(_h)
    assert not hasattr(_h, "_PROMPT_FRAGMENT_SHORT"), \
        "AC3: helper module must no longer expose _PROMPT_FRAGMENT_SHORT"


def test_behavioral_assertion_rubric_short_not_importable():
    from bytedigger_engine.lib.plugins.anti_hallucination import helper as _h
    importlib.reload(_h)
    assert not hasattr(_h, "_BEHAVIORAL_ASSERTION_RUBRIC_SHORT"), \
        "AC4: helper module must no longer expose _BEHAVIORAL_ASSERTION_RUBRIC_SHORT"


def test_get_prompt_fragment_no_kwarg():
    from bytedigger_engine.lib.plugins.anti_hallucination import helper as _h
    importlib.reload(_h)
    sig = inspect.signature(_h.get_prompt_fragment)
    assert "verbose" not in sig.parameters, \
        f"AC5: get_prompt_fragment must not accept 'verbose' kwarg, got {list(sig.parameters)}"
    result = _h.get_prompt_fragment()
    assert isinstance(result, str) and len(result) > 0, \
        "AC5: get_prompt_fragment() must return non-empty str"


def test_get_behavioral_assertion_rubric_no_kwarg():
    from bytedigger_engine.lib.plugins.anti_hallucination import helper as _h
    importlib.reload(_h)
    sig = inspect.signature(_h.get_behavioral_assertion_rubric)
    assert "verbose" not in sig.parameters, \
        f"AC6: get_behavioral_assertion_rubric must not accept 'verbose' kwarg, got {list(sig.parameters)}"
    result = _h.get_behavioral_assertion_rubric()
    assert isinstance(result, str) and len(result) > 0, \
        "AC6: get_behavioral_assertion_rubric() must return non-empty str"


def test_helper_source_no_bridge_comment():
    src = _HELPER_PATH.read_text(encoding="utf-8")
    assert "# Bridge to A3 (9D520664)" not in src, \
        "AC7: bridge comment must be removed from helper.py (bridge complete)"


def test_rubric_trim_5D0D3BD1_no_short_references():
    src = _RUBRIC_TRIM_TEST_PATH.read_text(encoding="utf-8")
    assert "_PROMPT_FRAGMENT_SHORT" not in src, \
        "AC8: test_phase_6_rubric_trim_5D0D3BD1.py must no longer reference _PROMPT_FRAGMENT_SHORT"
    assert "_BEHAVIORAL_ASSERTION_RUBRIC_SHORT" not in src, \
        "AC8: test_phase_6_rubric_trim_5D0D3BD1.py must no longer reference _BEHAVIORAL_ASSERTION_RUBRIC_SHORT"
