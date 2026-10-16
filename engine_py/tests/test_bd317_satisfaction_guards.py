"""bd#317 GUARD tests (green before and after GREEN) split from
test_bd317_satisfaction_single_evaluator.py so the RED file contains only red tests.

Spec: docs/decisions/2026-10-16-bd317-satisfaction-single-evaluator.md (section 3).

AC4  SIMPLE / FEATURE / unset -> one LLM call, allowed_tools == [Read, Write]
AC5  single-evaluator machinery in phase_6_review survives
AC7  `python3 -m bytedigger_engine.error_codes --check` exits 0
AC8  bytedigger.json satisfaction_thresholds == {SIMPLE:80, FEATURE:85, COMPLEX:90}
AC9  class_i_lint exits 0

Only the LLM subprocess boundary is stubbed (workflows.md 1l). No sys.path mutation (1q).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from bytedigger_engine.contracts import StepResult, WorkflowContext
from bytedigger_engine.workflows import phase_6_review as p6

ENGINE_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = ENGINE_ROOT.parent


def _ctx(scratch: Path, complexity: str | None, threshold: int | None = 90) -> WorkflowContext:
    org: dict = {"scratchpad_dir": str(scratch)}
    if complexity is not None:
        org["complexity"] = complexity
    if threshold is not None:
        org["satisfaction_threshold"] = threshold
    return WorkflowContext(
        tenant_id="hal", scope=None, db_path=None, org_config=org,
        question="Add foo to bar", session_id="test-bd317", persona="hal",
        framework=None, domain=None,
    )


def _prev(scratch: Path) -> StepResult:
    spec = scratch / p6.SPEC_DOC_RELPATH
    review = scratch / p6.REVIEW_DOC_RELPATH
    fix = scratch / p6.FIX_DOC_RELPATH
    doc = scratch / p6.SATISFACTION_DOC_RELPATH
    for p, body in ((spec, "## US1\nAdd foo\n"), (review, "# Review\n\nVERDICT: PASS\n"),
                    (fix, "FIX COMPLETE.\n")):
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    doc.parent.mkdir(parents=True, exist_ok=True)
    prompt = "test satisfaction prompt"
    return StepResult(
        status="ok",
        data={"prompt": prompt, "doc_path": str(doc), "spec_path": str(spec),
              "review_doc_path": str(review), "fix_doc_path": str(fix),
              "prompt_bytes": len(prompt.encode())},
        duration_ms=0, step_name="build_satisfaction_prompt",
    )


def _raw(score: int) -> str:
    verdict = "PASS" if score >= 80 else "FAIL"
    satisfied = "true" if score >= 80 else "false"
    return (
        "# Satisfaction Evaluation\n\n## Per-Dimension Scores\n- Spec compliance: 95\n"
        "- Test quality: 95\n- Code quality: 95\n- Completeness: 95\n- Boy Scout: 95\n\n"
        "## Evidence Per Dimension\n- All checks passed.\n\n## Concerns\nnone\n\n"
        f"## Composite\nSCORE: {score}\n\nVERDICT: {verdict}\n\n"
        "## satisfaction-output (structured)\n```json\n"
        f'{{"satisfied": {satisfied}, "fixes_required": []}}\n```\n'
    )


def _install_stub(monkeypatch, score: int) -> list[dict]:
    calls: list[dict] = []

    def _fake(**kwargs):
        calls.append({"allowed_tools": kwargs.get("allowed_tools")})
        extra = kwargs.get("extra_data") or {}
        return StepResult(
            status="ok",
            data={"raw_response": _raw(score), "doc_path": extra.get("doc_path", ""),
                  "spec_path": extra.get("spec_path", ""),
                  "review_doc_path": extra.get("review_doc_path", ""),
                  "fix_doc_path": extra.get("fix_doc_path", "")},
            duration_ms=0, step_name="invoke_satisfaction_llm",
        )

    monkeypatch.setattr(p6, "invoke_llm_subprocess", _fake)
    monkeypatch.setattr(p6, "_emit_safe", lambda *a, **kw: None)
    return calls


@pytest.mark.parametrize("complexity", ["SIMPLE", "FEATURE", None])
def test_ac4_guard_non_complex_single_call_read_write(tmp_path, monkeypatch, complexity):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    calls = _install_stub(monkeypatch, 95)
    p6._invoke_satisfaction_llm(_ctx(scratch, complexity, None), _prev(scratch))
    assert len(calls) == 1
    assert calls[0]["allowed_tools"] == ["Read", "Write"]


_KEPT = (
    "_decide_satisfaction_passed", "_deterministic_satisfaction_override",
    "_satisfaction_findings_blocks", "_persist_satisfaction_last_findings",
    "_write_satisfaction_doc", "_invoke_satisfaction_llm",
)


@pytest.mark.parametrize("name", _KEPT)
def test_ac5_single_machinery_kept(name):
    assert callable(getattr(p6, name, None)), f"phase_6_review.{name} must survive"


def test_ac7_error_codes_check_exits_zero():
    proc = subprocess.run(
        [sys.executable, "-m", "bytedigger_engine.error_codes", "--check"],
        cwd=str(ENGINE_ROOT), capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_ac8_guard_satisfaction_thresholds_unchanged():
    cfg = json.loads((REPO_ROOT / "bytedigger.json").read_text(encoding="utf-8"))
    assert cfg["satisfaction_thresholds"] == {"SIMPLE": 80, "FEATURE": 85, "COMPLEX": 90}


def test_ac9_guard_class_i_lint_exits_zero():
    proc = subprocess.run(
        [sys.executable, "-m", "bytedigger_engine.conformance.class_i_lint"],
        cwd=str(ENGINE_ROOT), capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
