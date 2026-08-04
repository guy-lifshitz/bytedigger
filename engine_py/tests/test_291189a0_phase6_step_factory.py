"""RED tests for 291189A0 — phase_6_review step()+RetryPolicy migration.

Mirror C37015E8 pilot: write_review_artifact + write_fix_artifact registered
via step() factory with RetryPolicy(max_retries=1). Behavioral no-op via
recoverable=False on terminal post-inline-retry failures.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from bytedigger_engine.contracts import RetryPolicy, StepContract, StepResult  # noqa: E402
from bytedigger_engine.workflows.phase_6_review import phase_6_review_workflow  # noqa: E402


def _find_step(workflow, name: str) -> StepContract:
    for s in workflow.steps:
        if s.name == name:
            return s
    raise AssertionError(f"step {name!r} not in workflow")


def test_write_review_artifact_registered_via_step_factory_with_retry_policy_1():
    """write_review_artifact must be a step()-factory contract with RetryPolicy(max_retries=1).

    Detection heuristic: step()-factory contracts have an _execute closure (not the bare _write_review_artifact
    function reference). If the registration is StepContract(execute=_write_review_artifact) directly, the
    contract.execute is _write_review_artifact and __name__=='_write_review_artifact'. After migration,
    contract.execute is the inner _execute closure of step() and __name__=='_execute'.
    """
    wf = phase_6_review_workflow()
    contract = _find_step(wf, "write_review_artifact")
    assert callable(contract.execute)
    # step() factory wraps fn with an inner _execute closure
    assert contract.execute.__name__ == "_execute", (
        "write_review_artifact still uses bare StepContract registration; "
        "expected step()+RetryPolicy(max_retries=1) factory wrapping"
    )


def test_write_fix_artifact_registered_via_step_factory_with_retry_policy_1():
    wf = phase_6_review_workflow()
    contract = _find_step(wf, "write_fix_artifact")
    assert callable(contract.execute)
    assert contract.execute.__name__ == "_execute", (
        "write_fix_artifact still uses bare StepContract registration; "
        "expected step()+RetryPolicy(max_retries=1) factory wrapping"
    )


# СНЯТО GH1399 (§1c-ОТМЕНА): test_write_review_artifact_terminal_drift_is_recoverable_false
# 291189A0 — терминальность пост-ретрайного дрейфа: ретрай-ветка удалена GH1399,
# у решения исчез предмет. Свойство «двойного ретрая нет» сохраняется в СИЛЬНОЙ форме
# и ассертится GH1399 AC8 (отсутствие retry_from_step).


def test_write_fix_artifact_terminal_no_marker_is_recoverable_false(tmp_path, monkeypatch):
    """Terminal E_FIX_NO_MARKER must return recoverable=False (behavioral no-op for type-level pilot)."""
    from bytedigger_engine.workflows import phase_6_review as p6r

    monkeypatch.setattr(p6r, "_parse_fix_status", lambda raw: p6r.FIX_NO_MARKER)
    def fake_invoke(**kwargs):
        return StepResult(
            status="ok",
            data={"raw_response": "still no marker"},
            duration_ms=0,
            step_name=kwargs.get("step_name", "invoke_fix_llm_retry"),
        )
    monkeypatch.setattr(p6r, "invoke_llm_subprocess", fake_invoke)

    log_path = tmp_path / "build-fix.md"
    prev = StepResult(
        status="ok",
        data={
            "raw_response": "no marker raw",
            "log_path": str(log_path),
            "spec_path": str(tmp_path / "spec.md"),
            "review_doc_path": str(tmp_path / "review.md"),
            "verdict": "REVIEW_FAIL",
            "prompt": "fix prompt",
        },
        duration_ms=0,
        step_name="invoke_fix_llm",
    )

    class FakeCtx:
        org_config = {"fix_llm_command": ["echo"]}

    result = p6r._write_fix_artifact(FakeCtx(), prev)
    assert result.status == "error"
    assert result.error_code == "E_FIX_NO_MARKER"
    assert result.recoverable is False, (
        "terminal E_FIX_NO_MARKER must be recoverable=False to prevent "
        "step()+RetryPolicy outer-loop double-retry"
    )
