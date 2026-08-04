"""Tests for A1D4A4C9 — centralize path-classifier into util.path_classifier.

Trimmed by 4961254A: dead-symbol cases (_is_engine_scratch, _is_session_state,
_ENGINE_SCRATCH_PREFIXES, _SESSION_STATE_PREFIXES, _resolve_engine_scratch_prefixes)
removed.  Remaining cases cover the surviving _is_test_path ROUTER.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).parent
ENGINE_ROOT = HERE.parent
sys.path.insert(0, str(ENGINE_ROOT))

import importlib
from bytedigger_engine.workflows import phase_5_implement as _p5  # noqa: E402
from bytedigger_engine.workflows import phase_6_review as _p6  # noqa: E402

_pc_mod = None

def _pc():
    """Return the util.path_classifier module, asserting it is importable."""
    global _pc_mod
    if _pc_mod is None:
        _pc_mod = importlib.import_module("bytedigger_engine.lib.util.path_classifier")
    return _pc_mod


# Resolve phase-file paths robustly from this test file's location
_WORKFLOWS = ENGINE_ROOT / "bytedigger_engine" / "workflows"
_P5_SRC = (_WORKFLOWS / "phase_5_implement.py").read_text()
_P6_SRC = (_WORKFLOWS / "phase_6_review.py").read_text()


# ──────────────────────────────────────────────────────────────────────────────
# AC1 (trimmed): util.path_classifier importable with surviving router names
# ──────────────────────────────────────────────────────────────────────────────

_EXPECTED_NAMES = (
    "_fn",
    "_TEST_FILENAME_PATTERNS",
    "_TEST_PATH_SEGMENTS",
    "_is_test_path",
)


def test_a1d4a4c9_ac1_module_importable_with_router_names():
    pc = _pc()
    missing = [name for name in _EXPECTED_NAMES if not hasattr(pc, name)]
    assert not missing, (
        f"util.path_classifier is missing attributes: {missing!r}."
    )


# ──────────────────────────────────────────────────────────────────────────────
# AC2: phase_5 re-exports _is_test_path as the same object from path_classifier
# ──────────────────────────────────────────────────────────────────────────────

def test_a1d4a4c9_ac2_p5_is_test_path_is_pc():
    pc = _pc()
    assert _p5._is_test_path is pc._is_test_path, (
        "phase_5_implement._is_test_path is not the same object as "
        "util.path_classifier._is_test_path."
    )


# ──────────────────────────────────────────────────────────────────────────────
# AC3: phase_6 re-exports _is_test_path as the same object from path_classifier
# ──────────────────────────────────────────────────────────────────────────────

def test_a1d4a4c9_ac3_p6_is_test_path_is_pc():
    pc = _pc()
    assert _p6._is_test_path is pc._is_test_path, (
        "phase_6_review._is_test_path is not the same object as "
        "util.path_classifier._is_test_path."
    )


# ──────────────────────────────────────────────────────────────────────────────
# AC7 (trimmed): surviving constant tuples are the same objects across modules
# ──────────────────────────────────────────────────────────────────────────────

def test_a1d4a4c9_ac7_surviving_constant_identity():
    pc = _pc()
    for const in (
        "_TEST_FILENAME_PATTERNS",
        "_TEST_PATH_SEGMENTS",
    ):
        pc_val = getattr(pc, const)
        p5_val = getattr(_p5, const)
        p6_val = getattr(_p6, const)
        assert p5_val is pc_val, (
            f"phase_5_implement.{const} is not the same object as "
            f"util.path_classifier.{const}"
        )
        assert p6_val is pc_val, (
            f"phase_6_review.{const} is not the same object as "
            f"util.path_classifier.{const}"
        )


# ──────────────────────────────────────────────────────────────────────────────
# AC8: _is_test_path behaviour
# ──────────────────────────────────────────────────────────────────────────────

def test_a1d4a4c9_ac8_is_test_path_behaviour():
    pc = _pc()
    fn = pc._is_test_path
    assert fn("a/__tests__/x.ts") is True, "segment '__tests__' must match"
    assert fn("foo.test.sh") is True, "filename pattern '*.test.sh' must match"
    assert fn("test_bar.py") is True, "filename pattern 'test_*.py' must match"
    assert fn("src/app.py") is False, "production source must not match"
    assert fn("") is False, "empty string must return False"


# ──────────────────────────────────────────────────────────────────────────────
# AC11: def _is_session_state must NOT appear in either phase-file source
#       (post-4961254A: deleted entirely from both phase files)
# ──────────────────────────────────────────────────────────────────────────────

def test_a1d4a4c9_ac11_no_def_is_session_state_in_phase_sources():
    assert "def _is_session_state" not in _P5_SRC, (
        "phase_5_implement.py still contains a local 'def _is_session_state'. "
        "After 4961254A it must be absent entirely."
    )
    assert "def _is_session_state" not in _P6_SRC, (
        "phase_6_review.py still contains a local 'def _is_session_state'. "
        "After 4961254A it must be absent entirely."
    )


# ──────────────────────────────────────────────────────────────────────────────
# AC12 (trimmed): no local def for surviving/dead symbols in phase sources
# ──────────────────────────────────────────────────────────────────────────────

def test_a1d4a4c9_ac12_no_local_def_bodies_in_phase_sources():
    for sym in (
        "def _is_test_path",
        "def _is_engine_scratch",
        "def _resolve_engine_scratch_prefixes",
    ):
        assert sym not in _P5_SRC, (
            f"phase_5_implement.py still contains a local '{sym}'."
        )
        assert sym not in _P6_SRC, (
            f"phase_6_review.py still contains a local '{sym}'."
        )
