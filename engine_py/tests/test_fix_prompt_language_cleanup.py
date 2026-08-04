"""004206FA — RED test: fix prompt language cleanup (24E29725 followup).

Two acceptance criteria, single test:
  AC1: 'Run the verification command (test suite or grep)' phrase absent.
  AC2: 'Engine runs the test suite' phrase present.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

from bytedigger_engine.contracts import WorkflowContext, StepResult  # noqa: E402
from bytedigger_engine.workflows.phase_6_review import _build_fix_prompt  # noqa: E402


def test_fix_prompt_no_run_verification_command_phrase(tmp_path):
    """004206FA AC1+AC2: stale verification-command phrase removed; engine-runs-tests added."""
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    (scratchpad / "build-first-block.md").write_text("# build context\n")

    spec = tmp_path / "spec.md"
    spec.write_text("# spec\n")
    review = tmp_path / "review.md"
    review.write_text("## Aggregated Findings\n\nFAIL — 1 finding.\n")

    ctx = WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={"scratchpad_dir": str(scratchpad)},
        question="Cleanup fix prompt language",
        session_id="test-session",
        persona="hal",
        framework=None,
        domain=None,
    )

    prev = StepResult(
        status="ok",
        data={
            "spec_path": str(spec),
            "review_doc_path": str(review),
            "verdict": "FAIL",
        },
        duration_ms=0,
        step_name="invoke_review_llm",
    )

    result = _build_fix_prompt(ctx, prev)
    assert result.status == "ok", f"_build_fix_prompt error: {result.error!r}"
    prompt = result.data["prompt"]

    assert "Run the verification command (test suite or grep)" not in prompt, (
        "Stale phrase must be removed from fix prompt (24E29725 dropped Bash, "
        "LLM cannot run test suite)"
    )
    assert "Engine runs the test suite" in prompt, (
        "Replacement language clarifying engine-side verification must be present"
    )
