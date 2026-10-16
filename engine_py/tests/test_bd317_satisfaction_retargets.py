"""bd#317 RED: satisfaction single-evaluator retargets, moved out of their sibling files.

Spec: docs/decisions/2026-10-16-bd317-satisfaction-single-evaluator.md (section 5 items 3, 4, 6).

These four tests fail by design until bd#317 GREEN. They were moved here so the sibling test
files stay fully green pre-GREEN (lot-preflight --phase red requires green siblings).

Origin of each test:
  test_ac11_satisfaction_step_is_single_evaluator
      <- tests/test_bd89_p3b1c_single_reviewer_md_gates.py (AC11, bd#317 flip)
  test_ac2_complex_single_evaluator_passes_read_write_allowed_tools
      <- engine_py/tests/test_subagent_return_discipline_satisfaction_1A07C325.py (AC2)
  test_ac2_complex_declares_one_block_for_the_single_evaluator
      <- engine_py/tests/test_bd206_class_m_sites.py (AC2)
  test_ac2b_missing_evaluator_id_forwards_nothing_and_does_not_refuse
      <- engine_py/tests/test_bd206_class_m_sites.py (AC2b)

Helpers are reused from the original files via plain sibling-module imports (no
sys.path mutation here, no conftest import). Autouse fixtures do not carry over via import, so
the bd206 isolation fixture is redefined below.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

import test_bd206_class_m_sites as _b206
import test_subagent_return_discipline_satisfaction_1A07C325 as _1a07
from bytedigger_engine import llm_subprocess, telemetry_ctx
from bytedigger_engine.contracts import StepResult

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]


_drive_sat = _b206._drive_sat
_sat_raw = _b206._sat_raw
FIXES_A = _b206.FIXES_A
_sat_event_id = _b206._sat_event_id
_fix_from = _b206._fix_from
_attests = _b206._attests
_class_m = _b206._class_m
sha256_of = _b206.sha256_of
_lines = _b206._lines
_p6 = _b206._p6

phase_6_review = _1a07.phase_6_review
_make_ctx = _1a07._make_ctx
_make_invoke_sat_prev = _1a07._make_invoke_sat_prev
_valid_raw_response = _1a07._valid_raw_response


@pytest.fixture(autouse=True)
def _bd206_isolation(monkeypatch):
    monkeypatch.setattr(llm_subprocess, "emit_resolver_resolved", lambda *a, **kw: None)
    llm_subprocess.reset_backends()
    telemetry_ctx.clear_current_run()
    yield
    telemetry_ctx.clear_current_run()
    llm_subprocess.reset_backends()


# ---------------------------------------------------------------------------
# From test_bd89_p3b1c_single_reviewer_md_gates.py (AC11)
# ---------------------------------------------------------------------------

def test_ac11_satisfaction_step_is_single_evaluator():
    text = (REPO / "phases" / "phase-6-review.md").read_text(encoding="utf-8")
    assert "3-Agent Majority Vote" not in text
    assert "Launch 3 Opus agents in parallel" not in text
    assert any(
        line.startswith("#") and "COMPLEX" in line and "Single Opus Evaluator" in line
        for line in text.splitlines()
    )
    cfg = json.loads((REPO / "bytedigger.json").read_text(encoding="utf-8"))
    th = cfg["satisfaction_thresholds"]
    assert (th["SIMPLE"], th["FEATURE"], th["COMPLEX"]) == (80, 85, 90)


# ---------------------------------------------------------------------------
# From test_subagent_return_discipline_satisfaction_1A07C325.py (AC2)
# ---------------------------------------------------------------------------

def test_ac2_complex_single_evaluator_passes_read_write_allowed_tools(tmp_path, monkeypatch):
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    calls: list = []

    def fake_invoke(*args, **kwargs):
        calls.append(kwargs.get("allowed_tools"))
        return StepResult(
            status="ok",
            data={
                "raw_response": _valid_raw_response(95),
                "doc_path": "",
                "spec_path": "",
                "review_doc_path": "",
                "fix_doc_path": "",
            },
            duration_ms=0,
            step_name="invoke_satisfaction_llm",
        )

    monkeypatch.setattr(phase_6_review, "invoke_llm_subprocess", fake_invoke)
    monkeypatch.setattr(phase_6_review, "_emit_safe", lambda *a, **kw: None)
    ctx = _make_ctx(scratchpad, complexity="COMPLEX")
    prev = _make_invoke_sat_prev(scratchpad)
    phase_6_review._invoke_satisfaction_llm(ctx, prev)
    assert len(calls) == 1
    assert calls[0] == ["Read", "Write"]


# ---------------------------------------------------------------------------
# From test_bd206_class_m_sites.py (AC2, AC2b)
# ---------------------------------------------------------------------------

def test_ac2_complex_declares_one_block_for_the_single_evaluator(tmp_path) -> None:
    s = _drive_sat(tmp_path, [_sat_raw(FIXES_A, "evaluator A")], complex_=True)
    assert s.router.counts.get("invoke_satisfaction_llm") == 1
    assert "evaluator_responses" not in s.sat.data
    assert "is_multi_evaluator" not in s.sat.data
    sat_id = _sat_event_id(s)
    built, fixed = _fix_from(s, s.r.data)
    assert fixed.status == "ok"
    fix_ev = _attests(s.log, "invoke_fix_llm")
    assert len(fix_ev) == 1
    assert _class_m(fix_ev[0]) == [{
        "source_id": "invocation:invoke_satisfaction_llm:" + sat_id,
        "sha256": sha256_of(_lines(FIXES_A)),
    }]
    assert s.r.data["findings"] == _lines(FIXES_A)


def test_ac2b_missing_evaluator_id_forwards_nothing_and_does_not_refuse(tmp_path) -> None:
    s = _drive_sat(tmp_path, [_sat_raw(FIXES_A, "evaluator A")], complex_=True)
    control_blocks = s.r.data.get("findings_blocks")
    assert isinstance(control_blocks, list) and len(control_blocks) == 1
    _, fixed_control = _fix_from(s, s.r.data)
    assert fixed_control.status == "ok"
    assert len(_class_m(_attests(s.log, "invoke_fix_llm")[0])) == 1

    no_id = StepResult(
        status="ok", duration_ms=0, step_name="invoke_satisfaction_llm",
        data={**s.sat.data, "invocation_id": None},
    )
    r2 = _p6()._write_satisfaction_doc(s.ctx, no_id)
    assert r2.recoverable is True
    assert "findings_blocks" not in r2.data
    _, fixed = _fix_from(s, r2.data)
    assert fixed.status == "ok" and fixed.error_code != "E_INJECT_UNATTRIBUTED"
    second = _attests(s.log, "invoke_fix_llm")[1]
    assert _class_m(second) == []
