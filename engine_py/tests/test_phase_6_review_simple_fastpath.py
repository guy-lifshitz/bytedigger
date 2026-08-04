"""RED tests for 03EDD39E — SIMPLE pipeline fast-path.

AC1: phase_6_review_simple_fastpath_workflow() returns WorkflowDefinition with name + 5 steps in order.
AC2: register_all registers workflow under name "phase_6_review_simple_fastpath".
AC3: _build_fastpath_stubs writes reviews/build-review.md with required substrings.
AC4: _build_fastpath_stubs writes reviews/build-fix.md with required substrings.
AC5: _build_fastpath_stubs returns spec_path even when file missing.
AC8: phase_6_review_workflow step count snapshot — must remain 16 (was 15 pre-7A940850).

ALL Python tests except AC8 fail because phase_6_review_simple_fastpath module does not exist yet.
AC8 passes today (snapshot guard against accidental drift in phase_6_review).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from bytedigger_engine.contracts import StepResult, WorkflowContext  # noqa: E402


# ─── helpers ──────────────────────────────────────────────────────────────────


def make_ctx(scratchpad: Path, **org_extra) -> WorkflowContext:
    org = {"scratchpad_dir": str(scratchpad), **org_extra}
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question="03EDD39E fast-path test",
        session_id="test-03edd39e",
        persona="hal",
        framework=None,
        domain=None,
    )


# ─── AC1 ──────────────────────────────────────────────────────────────────────


def test_ac1_workflow_definition_shape():
    """AC1: phase_6_review_simple_fastpath_workflow returns WorkflowDefinition with name + 5 steps in order."""
    from bytedigger_engine.workflows.phase_6_review_simple_fastpath import phase_6_review_simple_fastpath_workflow  # noqa: E402

    wf = phase_6_review_simple_fastpath_workflow()
    assert wf.name == "phase_6_review_simple_fastpath", f"name={wf.name!r}"
    step_names = [s.name for s in wf.steps]
    expected = [
        "build_fastpath_stubs",
        "build_satisfaction_prompt",
        "invoke_satisfaction_llm",
        "write_satisfaction_doc",
        "detect_mass_unverified",
    ]
    assert step_names == expected, f"step order mismatch: got {step_names}"


# ─── AC2 ──────────────────────────────────────────────────────────────────────


def test_ac2_register_all_includes_fastpath():
    """AC2: register_all registers phase_6_review_simple_fastpath in the engine."""
    from bytedigger_engine import workflows  # noqa: E402
    from bytedigger_engine.engine import WorkflowEngine  # noqa: E402

    eng = WorkflowEngine()
    workflows.register_all(eng)
    assert "phase_6_review_simple_fastpath" in eng.registered(), (
        "phase_6_review_simple_fastpath must appear in eng.registered() after register_all"
    )


# ─── AC3 ──────────────────────────────────────────────────────────────────────


def test_ac3_build_fastpath_stubs_writes_review_md(tmp_path):
    """AC3: _build_fastpath_stubs writes reviews/build-review.md with required substrings."""
    from bytedigger_engine.workflows.phase_6_review_simple_fastpath import _build_fastpath_stubs  # noqa: E402

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)

    result = _build_fastpath_stubs(make_ctx(scratchpad), None)
    assert result.status == "ok", f"status={result.status!r}"

    review_path = scratchpad / "reviews" / "build-review.md"
    assert review_path.is_file(), f"reviews/build-review.md missing at {review_path}"
    content = review_path.read_text(encoding="utf-8")
    assert "# Composite Review" in content, "missing '# Composite Review' header"
    assert "FAST PATH: composite review skipped" in content, "missing fast-path notice"
    assert "VERDICT: PENDING" in content, "missing VERDICT: PENDING"
    assert "Findings total: 0" in content, "missing Findings total: 0"

    # data shape
    assert "review_doc_path" in result.data, "data missing review_doc_path"
    assert "fix_doc_path" in result.data, "data missing fix_doc_path"
    assert "spec_path" in result.data, "data missing spec_path"


# ─── AC4 ──────────────────────────────────────────────────────────────────────


def test_ac4_build_fastpath_stubs_writes_fix_md(tmp_path):
    """AC4: _build_fastpath_stubs writes reviews/build-fix.md with required substrings."""
    from bytedigger_engine.workflows.phase_6_review_simple_fastpath import _build_fastpath_stubs  # noqa: E402

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)

    _build_fastpath_stubs(make_ctx(scratchpad), None)

    fix_path = scratchpad / "reviews" / "build-fix.md"
    assert fix_path.is_file(), f"reviews/build-fix.md missing at {fix_path}"
    content = fix_path.read_text(encoding="utf-8")
    assert "# Build Fix" in content, "missing '# Build Fix' header"
    assert "FAST PATH: no fix loop" in content, "missing fast-path notice in fix.md"


# ─── AC5 ──────────────────────────────────────────────────────────────────────


def test_ac5_spec_path_returned_even_when_missing(tmp_path):
    """AC5: data['spec_path'] points to expected path regardless of file existence."""
    from bytedigger_engine.workflows.phase_6_review_simple_fastpath import _build_fastpath_stubs  # noqa: E402

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    # Intentionally do NOT create specs/build-spec.md

    result = _build_fastpath_stubs(make_ctx(scratchpad), None)
    spec_path = Path(result.data["spec_path"])
    assert spec_path == scratchpad / "specs" / "build-spec.md", (
        f"expected <scratchpad>/specs/build-spec.md, got {spec_path}"
    )
    # No assertion on file existence — downstream satisfaction prompt handles missing.


# ─── AC8 (snapshot guard — passes today) ─────────────────────────────────────


def test_ac8_phase_6_review_workflow_step_count_unchanged():
    """AC8: existing phase_6_review_workflow step count must be exactly 18.

    Snapshot guard. Any change to phase_6_review.py that adds or removes steps
    must intentionally update this value. History: 15 → 16 (7A940850 post-fix
    pytest gate), 16 → 17 (8FE3D757 commit_fix_tests sibling step),
    17 → 18 (GH316 verify_fix_typecheck post-fix mypy delta gate).
    """
    from bytedigger_engine.workflows.phase_6_review import phase_6_review_workflow  # noqa: E402

    wf = phase_6_review_workflow()
    assert len(wf.steps) == 21, (
        f"phase_6_review_workflow step count drifted: expected 21, got {len(wf.steps)}. "
        f"Update snapshot intentionally if this is deliberate."
    )
