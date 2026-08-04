"""RED tests for resume_key_lint 00321AB4 — lint_text / lint_file / main.

ALL tests MUST FAIL until GREEN ships:
  - SYSTEM/cli/build/engine_py/resume_key_lint.py

Lib import is DEFERRED INSIDE each test function so the file COLLECTS cleanly
(per §1q/D1CF5FDF).  Module top-level has NO bare `from resume_key_lint import ...`.

conftest.py already puts _ENGINE_ROOT on sys.path at import time, so the
deferred `import resume_key_lint` resolves once the module exists.
"""
from __future__ import annotations

from pathlib import Path

import pytest

# Real golden files — must exist NOW (pre-GREEN).
_ENGINE_PY_ROOT = Path(__file__).resolve().parents[1]  # engine_py/
_RESUME_KEYING_PATH = _ENGINE_PY_ROOT / "bytedigger_engine/lib" / "resume_keying.py"
_PHASE_6_REVIEW_PATH = _ENGINE_PY_ROOT / "bytedigger_engine/workflows" / "phase_6_review.py"


def _import_resume_key_lint():
    """Deferred import — raises ImportError until GREEN ships resume_key_lint.py."""
    from bytedigger_engine import resume_key_lint as _rkl
    return _rkl


# ── AC1: real resume_keying.py lints clean ──────────────────────────────────


def test_ac1_golden_resume_keying_lint_clean() -> None:
    """AC1: lint_text on real engine_py/bytedigger_engine/lib/resume_keying.py source → []."""
    rkl = _import_resume_key_lint()
    assert _RESUME_KEYING_PATH.exists(), (
        f"Prerequisite: real resume_keying.py must exist at {_RESUME_KEYING_PATH}"
    )
    source = _RESUME_KEYING_PATH.read_text(encoding="utf-8")
    violations = rkl.lint_text(source, "resume_keying.py")
    assert violations == [], (
        f"resume_keying.py must lint clean (golden invariant); got {violations!r}"
    )


# ── AC2: real phase_6_review.py lints clean ─────────────────────────────────


def test_ac2_golden_phase_6_review_lint_clean() -> None:
    """AC2: lint_text on real engine_py/bytedigger_engine/workflows/phase_6_review.py source → []."""
    rkl = _import_resume_key_lint()
    assert _PHASE_6_REVIEW_PATH.exists(), (
        f"Prerequisite: real phase_6_review.py must exist at {_PHASE_6_REVIEW_PATH}"
    )
    source = _PHASE_6_REVIEW_PATH.read_text(encoding="utf-8")
    violations = rkl.lint_text(source, "phase_6_review.py")
    assert violations == [], (
        f"phase_6_review.py must lint clean (golden invariant); got {violations!r}"
    )


# ── AC3: inline f-string (no run_id) in non-chokepoint fn → R1 present ──────


def test_ac3_inline_sentinel_no_helper_r1_present() -> None:
    """AC3: inline f-string f"{step}_done_c{cycle}.json" in non-chokepoint fn → R1 present."""
    rkl = _import_resume_key_lint()
    source = '''\
def build_key(step, cycle):
    return f"{step}_done_c{cycle}.json"
'''
    violations = rkl.lint_text(source, "test_input.py")
    r1 = [v for v in violations if v.rule == "inline_sentinel_no_helper"]
    assert len(r1) >= 1, (
        f"expected >=1 inline_sentinel_no_helper violation; got {violations!r}"
    )


# ── AC4: same missing-run_id inline literal → R2 also present ───────────────


def test_ac4_inline_sentinel_missing_run_id_r2_present() -> None:
    """AC4: f"{step}_done_c{cycle}.json" in non-chokepoint fn → R2 (sentinel_missing_run_id) present."""
    rkl = _import_resume_key_lint()
    source = '''\
def build_key(step, cycle):
    return f"{step}_done_c{cycle}.json"
'''
    violations = rkl.lint_text(source, "test_input.py")
    r2 = [v for v in violations if v.rule == "sentinel_missing_run_id"]
    assert len(r2) >= 1, (
        f"expected >=1 sentinel_missing_run_id violation; got {violations!r}"
    )


# ── AC5: inline f-string WITH run_id → R1 present, R2 ABSENT ────────────────


def test_ac5_inline_sentinel_with_run_id_r1_present_r2_absent() -> None:
    """AC5: f"{step}_done_c{cycle}_r{run_id}.json" in non-chokepoint fn → R1 present, R2 absent."""
    rkl = _import_resume_key_lint()
    source = '''\
def build_key(step, cycle, run_id):
    return f"{step}_done_c{cycle}_r{run_id}.json"
'''
    violations = rkl.lint_text(source, "test_input.py")
    r1 = [v for v in violations if v.rule == "inline_sentinel_no_helper"]
    r2 = [v for v in violations if v.rule == "sentinel_missing_run_id"]
    assert len(r1) >= 1, (
        f"expected >=1 inline_sentinel_no_helper (not using helper); got {violations!r}"
    )
    assert r2 == [], (
        f"expected no sentinel_missing_run_id (has run_id); got {violations!r}"
    )


# ── AC6: sentinel WITH run_id inside chokepoint fn → no violation ────────────


def test_ac6_sentinel_inside_chokepoint_with_run_id_clean() -> None:
    """AC6: f"{s}_done_c{c}_r{rid}.json" inside def resume_sentinel_name → []."""
    rkl = _import_resume_key_lint()
    source = '''\
def resume_sentinel_name(step_name, cycle, run_id):
    rid = run_id or "norun"
    return f"{step_name}_done_c{cycle}_r{rid}.json"
'''
    violations = rkl.lint_text(source, "test_input.py")
    assert violations == [], (
        f"chokepoint fn with run_id must lint clean; got {violations!r}"
    )


# ── AC7: sentinel WITHOUT run_id inside chokepoint → R2 present, R1 ABSENT ──


def test_ac7_sentinel_inside_chokepoint_missing_run_id_r2_only() -> None:
    """AC7: f"{s}_done_c{c}.json" (no run_id) inside resume_sentinel_name → R2 present, R1 absent."""
    rkl = _import_resume_key_lint()
    source = '''\
def resume_sentinel_name(step_name, cycle):
    return f"{step_name}_done_c{cycle}.json"
'''
    violations = rkl.lint_text(source, "test_input.py")
    r1 = [v for v in violations if v.rule == "inline_sentinel_no_helper"]
    r2 = [v for v in violations if v.rule == "sentinel_missing_run_id"]
    assert r2 != [], (
        f"expected sentinel_missing_run_id inside chokepoint with no run_id; got {violations!r}"
    )
    assert r1 == [], (
        f"expected NO inline_sentinel_no_helper inside chokepoint fn; got {violations!r}"
    )


# ── AC8: non-sentinel f-string → no violation ────────────────────────────────


def test_ac8_non_sentinel_fstring_not_flagged() -> None:
    """AC8: f"{step}_result.json" → no violations."""
    rkl = _import_resume_key_lint()
    source = '''\
def get_result_key(step):
    return f"{step}_result.json"
'''
    violations = rkl.lint_text(source, "test_input.py")
    assert violations == [], (
        f"non-sentinel f-string must not be flagged; got {violations!r}"
    )


# ── AC9: pragma resume-key: allow suppresses both rules ──────────────────────


def test_ac9_pragma_suppresses_both_rules() -> None:
    """AC9: fn containing '# resume-key: allow' with inline sentinel → [] (pragma suppresses both)."""
    rkl = _import_resume_key_lint()
    source = '''\
def build_key_legacy(step, cycle):
    # resume-key: allow
    return f"{step}_done_c{cycle}.json"
'''
    violations = rkl.lint_text(source, "test_input.py")
    assert violations == [], (
        f"fn with '# resume-key: allow' must suppress all violations; got {violations!r}"
    )


# ── AC10: plain string constant (not f-string) → R1 + R2 ────────────────────


def test_ac10_plain_string_constant_yields_r1_and_r2() -> None:
    """AC10: plain ast.Constant string 'foo_done_c1.json' inline in non-chokepoint fn → R1 + R2."""
    rkl = _import_resume_key_lint()
    source = '''\
def get_sentinel():
    return "foo_done_c1.json"
'''
    violations = rkl.lint_text(source, "test_input.py")
    r1 = [v for v in violations if v.rule == "inline_sentinel_no_helper"]
    r2 = [v for v in violations if v.rule == "sentinel_missing_run_id"]
    assert len(r1) >= 1, (
        f"expected inline_sentinel_no_helper for plain string; got {violations!r}"
    )
    assert len(r2) >= 1, (
        f"expected sentinel_missing_run_id for plain string; got {violations!r}"
    )


# ── AC11: main([real_resume_keying_path]) == 0 ──────────────────────────────


def test_ac11_main_golden_file_returns_0() -> None:
    """AC11: main([real resume_keying.py path]) == 0."""
    rkl = _import_resume_key_lint()
    assert _RESUME_KEYING_PATH.exists(), (
        f"Prerequisite: real resume_keying.py must exist at {_RESUME_KEYING_PATH}"
    )
    exit_code = rkl.main([str(_RESUME_KEYING_PATH)])
    assert exit_code == 0, (
        f"main([real resume_keying.py]) must return 0; got {exit_code!r}"
    )


# ── AC12: main nonexistent → 2; main violating → 1 ──────────────────────────


def test_ac12_main_nonexistent_returns_2_and_violating_returns_1(tmp_path: Path) -> None:
    """AC12: main([nonexistent]) == 2 AND main([tmp_violating_file]) == 1."""
    rkl = _import_resume_key_lint()

    # usage error: unreadable/nonexistent file
    exit_code_missing = rkl.main(["/nonexistent_resume_xyz.py"])
    assert exit_code_missing == 2, (
        f"main with nonexistent file must return 2; got {exit_code_missing!r}"
    )

    # violating file: inline sentinel without helper or run_id
    violating_source = '''\
def make_key(step, cycle):
    return f"{step}_done_c{cycle}.json"
'''
    tmp_file = tmp_path / "violating_resume.py"
    tmp_file.write_text(violating_source, encoding="utf-8")
    exit_code_viol = rkl.main([str(tmp_file)])
    assert exit_code_viol == 1, (
        f"main with violating file must return 1; got {exit_code_viol!r}"
    )
