"""Phase 6 review SIMPLE fast-path workflow (03EDD39E).

For SIMPLE complexity + diff <= 10 lines, skip composite review + fix loop;
run only Opus satisfaction (now with GREP-VERIFY MANDATE per DFB5B0CB).

Architecture: sibling workflow to phase_6_review. Orchestrator chooses by
invoking either workflow name on run-phase. This file holds the new workflow
definition + the only new step (build_fastpath_stubs); all other steps are
REUSED unchanged from phase_6_review.
"""
from __future__ import annotations

from pathlib import Path

from bytedigger_engine.contracts import StepContract, StepResult, WorkflowDefinition

# Reuse existing satisfaction steps + constants from phase_6_review.
try:
    from .phase_6_review import (
        REVIEW_DOC_RELPATH,
        FIX_DOC_RELPATH,
        SPEC_DOC_RELPATH,
        _build_satisfaction_prompt,
        _invoke_satisfaction_llm,
        _write_satisfaction_doc,
        _detect_mass_unverified,
    )
except ImportError:  # pragma: no cover — bare fallback for sys.path-rooted test imports (GH881)
    from bytedigger_engine.workflows.phase_6_review import (  # type: ignore[no-redef]
        REVIEW_DOC_RELPATH,
        FIX_DOC_RELPATH,
        SPEC_DOC_RELPATH,
        _build_satisfaction_prompt,
        _invoke_satisfaction_llm,
        _write_satisfaction_doc,
        _detect_mass_unverified,
    )

try:
    from .phase_workflows_common import _resolve_scratchpad  # noqa: E402
except ImportError:  # pragma: no cover — bare fallback for sys.path-rooted test imports (GH881)
    from bytedigger_engine.workflows.phase_workflows_common import _resolve_scratchpad  # type: ignore[no-redef]  # noqa: E402


def _build_fastpath_stubs(ctx, _prev) -> StepResult:
    """Write minimal review.md + fix.md stubs so downstream satisfaction prompt
    can read them by path.

    Returns data with review_doc_path, fix_doc_path, spec_path keys (str).
    """
    scratchpad = _resolve_scratchpad(ctx)
    review_path = scratchpad / REVIEW_DOC_RELPATH
    fix_path = scratchpad / FIX_DOC_RELPATH
    spec_path = scratchpad / SPEC_DOC_RELPATH

    review_path.parent.mkdir(parents=True, exist_ok=True)
    review_path.write_text(
        "# Composite Review\n\n"
        "FAST PATH: composite review skipped (SIMPLE complexity + diff <= 10 lines).\n\n"
        "Findings total: 0 (CRITICAL: 0 / HIGH: 0 / MEDIUM: 0)\n\n"
        "VERDICT: PENDING\n",
        encoding="utf-8",
    )
    fix_path.write_text(
        "# Build Fix\n\n"
        "FAST PATH: no fix loop (composite skipped).\n",
        encoding="utf-8",
    )
    # NB: do NOT create specs/build-spec.md — downstream satisfaction prompt
    # handles missing per existing convention (Path() construction, file
    # existence check at consume time). Just return the path.

    return StepResult(
        status="ok",
        data={
            "review_doc_path": str(review_path),
            "fix_doc_path": str(fix_path),
            "spec_path": str(spec_path),
        },
        duration_ms=0,
        step_name="build_fastpath_stubs",
    )


def phase_6_review_simple_fastpath_workflow() -> WorkflowDefinition:
    return WorkflowDefinition(
        name="phase_6_review_simple_fastpath",
        steps=[
            StepContract(name="build_fastpath_stubs", execute=_build_fastpath_stubs),
            StepContract(name="build_satisfaction_prompt", execute=_build_satisfaction_prompt),
            StepContract(name="invoke_satisfaction_llm", execute=_invoke_satisfaction_llm),
            StepContract(name="write_satisfaction_doc", execute=_write_satisfaction_doc),
            StepContract(name="detect_mass_unverified", execute=_detect_mass_unverified),
        ],
    )
