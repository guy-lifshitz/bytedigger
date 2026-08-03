"""D7B4D83C — RED test: fix prompt PRIOR-COMMIT ANTI-REVERT cleanup
(24E29725 followup).

Four acceptance criteria, single test:
  AC1: 'git log -p --since' phrase absent (LLM can't execute Bash).
  AC2: 'with the conflicting commit SHA' phrase absent (no git access).
  AC3: 'PRIOR-COMMIT ANTI-REVERT' rule name retained.
  AC4: 'current test assertions as' intent-preserved replacement present.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

from bytedigger_engine.contracts import WorkflowContext, StepResult  # noqa: E402
from bytedigger_engine.workflows.phase_6_review import _build_fix_prompt  # noqa: E402


def test_fix_prompt_no_git_log_shell_command(tmp_path):
    """D7B4D83C AC1-AC4: stale git log shell command removed; intent preserved."""
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
        question="Cleanup fix prompt git log instruction",
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

    assert "git log -p --since" not in prompt, (
        "Stale shell command must be removed (24E29725 dropped Bash from "
        "fix LLM allowed_tools; LLM cannot execute git)"
    )
    assert "with the conflicting commit SHA" not in prompt, (
        "Conflicting-commit-SHA citation must be removed (LLM has no git "
        "access to obtain SHAs)"
    )
    assert "PRIOR-COMMIT ANTI-REVERT" in prompt, (
        "Rule name must be retained — anti-revert intent is preserved"
    )
    assert "current test assertions as" in prompt, (
        "Replacement language clarifying intent must be present"
    )
