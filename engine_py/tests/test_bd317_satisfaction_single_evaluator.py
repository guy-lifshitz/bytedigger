"""RED tests for bd#317 -- COMPLEX satisfaction uses the single evaluator path.

Spec: docs/decisions/2026-10-16-bd317-satisfaction-single-evaluator.md (section 3).

AC1  COMPLEX: one LLM call, allowed_tools == [Read, Write], no multi keys
AC2  COMPLEX SCORE 90 @ threshold 90 passes, no `multi_evaluator` key
AC3  COMPLEX SCORE 89 @ threshold 90 fails with E_SATISFACTION_BELOW_THRESHOLD
AC4  GUARD: SIMPLE / FEATURE / unset -> one call, [Read, Write]
AC5  registry: multi machinery gone, single machinery kept
AC6  phase_6_review.py source has none of the multi markers
AC7  E_REVIEW_DEGRADED removed from error_codes + both ERROR_CODES.md; --check exits 0
AC8  phase-6 md / dynamic-context template / bytedigger.json thresholds
AC9  class_i_lint exits 0 and the inventory has no `_write_satisfaction_doc_multi` key

Only the LLM subprocess boundary (`invoke_llm_subprocess`) and the telemetry sink are
stubbed (workflows.md 1l). No singleton/time-dependent resource (1i). No sys.path
mutation, no conftest import (1q).
"""
from __future__ import annotations

import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

from bytedigger_engine.contracts import StepResult, WorkflowContext
from bytedigger_engine.workflows import phase_6_review as p6

ENGINE_ROOT = Path(__file__).resolve().parent.parent
REPO_ROOT = ENGINE_ROOT.parent


# --- helpers ----------------------------------------------------------------


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


def _run_complex(tmp_path: Path, monkeypatch, score: int):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    calls = _install_stub(monkeypatch, score)
    ctx = _ctx(scratch, "COMPLEX", 90)
    invoked = p6._invoke_satisfaction_llm(ctx, _prev(scratch))
    written = p6._write_satisfaction_doc(ctx, invoked)
    return calls, invoked, written


# --- AC1 --------------------------------------------------------------------


def test_ac1_complex_single_llm_call_read_write_no_multi_keys(tmp_path, monkeypatch):
    calls, invoked, _ = _run_complex(tmp_path, monkeypatch, 90)
    assert len(calls) == 1, f"COMPLEX must make exactly one LLM call, got {len(calls)}"
    assert calls[0]["allowed_tools"] == ["Read", "Write"]
    assert "is_multi_evaluator" not in invoked.data
    assert "evaluator_responses" not in invoked.data


# --- AC2 --------------------------------------------------------------------


def test_ac2_complex_score_at_threshold_passes_without_multi_evaluator_key(tmp_path, monkeypatch):
    calls, _, written = _run_complex(tmp_path, monkeypatch, 90)
    assert len(calls) == 1
    assert written.status == "ok", f"{written.error_code!r} {written.error!r}"
    assert written.data["threshold"] == 90
    assert written.data["score"] == 90
    assert "multi_evaluator" not in written.data


# --- AC3 --------------------------------------------------------------------


def test_ac3_complex_score_below_threshold_fails(tmp_path, monkeypatch):
    calls, _, written = _run_complex(tmp_path, monkeypatch, 89)
    assert len(calls) == 1, f"COMPLEX must make exactly one LLM call, got {len(calls)}"
    assert written.status != "ok"
    assert written.error_code == "E_SATISFACTION_BELOW_THRESHOLD"
    assert written.data["threshold"] == 90


# --- AC4 (GUARD) ------------------------------------------------------------


@pytest.mark.parametrize("complexity", ["SIMPLE", "FEATURE", None])
def test_ac4_guard_non_complex_single_call_read_write(tmp_path, monkeypatch, complexity):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    calls = _install_stub(monkeypatch, 95)
    p6._invoke_satisfaction_llm(_ctx(scratch, complexity, None), _prev(scratch))
    assert len(calls) == 1
    assert calls[0]["allowed_tools"] == ["Read", "Write"]


# --- AC5 --------------------------------------------------------------------

_REMOVED = (
    "_run_satisfaction_evaluators_parallel", "_aggregate_satisfaction",
    "_write_satisfaction_doc_multi", "_single_eval_passed", "_multi_findings_groups",
)
_KEPT = (
    "_decide_satisfaction_passed", "_deterministic_satisfaction_override",
    "_satisfaction_findings_blocks", "_persist_satisfaction_last_findings",
    "_write_satisfaction_doc", "_invoke_satisfaction_llm",
)


@pytest.mark.parametrize("name", _REMOVED)
def test_ac5_multi_machinery_removed(name):
    assert not hasattr(p6, name), f"phase_6_review.{name} must be removed"


@pytest.mark.parametrize("name", _KEPT)
def test_ac5_single_machinery_kept(name):
    assert callable(getattr(p6, name, None)), f"phase_6_review.{name} must survive"


# --- AC6 --------------------------------------------------------------------


def test_ac6_phase_6_source_has_no_multi_markers():
    src = (ENGINE_ROOT / "bytedigger_engine" / "workflows" / "phase_6_review.py").read_text(
        encoding="utf-8")
    present = [m for m in ("is_multi_evaluator", "evaluator_responses",
                           "satisfaction_multi_evaluator", "ThreadPoolExecutor",
                           "E_REVIEW_DEGRADED") if m in src]
    assert present == [], f"markers still in phase_6_review.py: {present}"


# --- AC7 --------------------------------------------------------------------


def test_ac7_review_degraded_not_in_error_codes_registry():
    ec = importlib.import_module("bytedigger_engine.error_codes")
    assert "E_REVIEW_DEGRADED" not in ec.ERROR_CODES


@pytest.mark.parametrize("rel", ["engine_py/ERROR_CODES.md",
                                 "engine_py/bytedigger_engine/ERROR_CODES.md"])
def test_ac7_review_degraded_not_in_error_codes_md(rel):
    assert "E_REVIEW_DEGRADED" not in (REPO_ROOT / rel).read_text(encoding="utf-8")


def test_ac7_error_codes_check_exits_zero():
    proc = subprocess.run(
        [sys.executable, "-m", "bytedigger_engine.error_codes", "--check"],
        cwd=str(ENGINE_ROOT), capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


# --- AC8 --------------------------------------------------------------------


def test_ac8_phase_6_md_is_single_evaluator():
    md = (REPO_ROOT / "phases" / "phase-6-review.md").read_text(encoding="utf-8")
    present = [s for s in ("3-Agent Majority Vote", "Launch 3 Opus agents", "Evaluator A",
                           "Evaluator B", "Evaluator C", "3x Opus") if s in md]
    assert present == [], f"stale multi-evaluator text in phase-6-review.md: {present}"
    assert any(ln.lstrip().startswith("#") and "COMPLEX" in ln and "Single Opus Evaluator" in ln
               for ln in md.splitlines()), "no heading with COMPLEX + Single Opus Evaluator"


def test_ac8_dynamic_context_template_has_no_3x_opus():
    tpl = (REPO_ROOT / "templates" / "dynamic-context.md").read_text(encoding="utf-8")
    assert "3x Opus" not in tpl


def test_ac8_phase_0_classify_has_no_3x_opus():
    md = (REPO_ROOT / "phases" / "phase-0-classify.md").read_text(encoding="utf-8")
    assert "3x Opus" not in md


def test_ac8_guard_satisfaction_thresholds_unchanged():
    cfg = json.loads((REPO_ROOT / "bytedigger.json").read_text(encoding="utf-8"))
    assert cfg["satisfaction_thresholds"] == {"SIMPLE": 80, "FEATURE": 85, "COMPLEX": 90}


# --- AC9 --------------------------------------------------------------------


def test_ac9_guard_class_i_lint_exits_zero():
    proc = subprocess.run(
        [sys.executable, "-m", "bytedigger_engine.conformance.class_i_lint"],
        cwd=str(ENGINE_ROOT), capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_ac9_inventory_has_no_write_satisfaction_doc_multi_key():
    lint = importlib.import_module("bytedigger_engine.conformance.class_i_lint")
    sites = lint.load_inventory()["sites"]
    stale = [k for k in sites if "_write_satisfaction_doc_multi" in k]
    assert stale == [], f"stale inventory keys: {stale}"
