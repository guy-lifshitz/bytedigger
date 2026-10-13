"""Fence tests for bd#297: behaviour that must not change when the dead
verdict-gate lint seam is deleted from `_gate_on_validation`.

All of these pass before and after the change; they are regression fences,
not RED tests. RED tests: test_bd297_verdict_seam_removed.py.

Spec: docs/decisions/2026-10-13-bd297-verdict-gate-seam-removed.md

AC5  test_ac5_verdict_pass_doc_proceeds_without_gate_event
AC6  test_ac6_verdict_fail_doc_rejected_at_cap_without_gate_event
     test_ac6_verdict_fail_doc_below_cap_loops_without_gate_event
AC7  test_ac7_run_gate_unanchored / _pass_doc_not_approved / _kill_switch_skip

Harness: the real `_gate_on_validation` step with a recording event log in the
telemetry slot (pre-staged and cleared, no timing, workflows.md 1i). No
collaborator is replaced. Units under test are imported lazily.
"""
from __future__ import annotations

from pathlib import Path

import pytest

UNANCHORED_DOC = "## Verdict\nPASS\n\nVERDICT: APPROVED\n"
PASS_DOC = "# validation\n\nAll acceptance criteria covered.\n\nVerdict: PASS\n"
FAIL_DOC = "# validation\n\nAC2 is not covered.\n\nVerdict: FAIL\n"


class _FakeEventLog:
    def __init__(self) -> None:
        self.events: list = []

    def append(self, event_type: str, payload: dict, run_id: str = "ad-hoc") -> None:
        self.events.append((event_type, dict(payload), run_id))

    def types(self) -> list:
        return [t for (t, _p, _r) in self.events]


@pytest.fixture
def harness(monkeypatch, tmp_path):
    from bytedigger_engine import telemetry_ctx

    monkeypatch.delenv("HAL_VERDICT_GATE_LINT", raising=False)
    monkeypatch.chdir(tmp_path)

    telemetry_ctx.clear_current_run()
    log = _FakeEventLog()
    telemetry_ctx.set_current_run(
        event_log=log, run_id="bd297f", step_name="gate_on_validation", phase="5", cycle=1,
    )
    try:
        yield {"tmp": tmp_path, "log": log, "scratch": tmp_path / "scratch"}
    finally:
        telemetry_ctx.clear_current_run()


def _ctx(scratch: Path):
    from bytedigger_engine.contracts import WorkflowContext

    return WorkflowContext(
        tenant_id="hal", scope=None, db_path=None,
        org_config={"scratchpad_dir": str(scratch)},
        question="bd297", session_id="test-bd297", persona="hal", framework=None, domain=None,
    )


def _prev(scratch: Path, raw: str, verdict: str, cycle: int = 1):
    from bytedigger_engine.contracts import StepResult

    scratch.mkdir(parents=True, exist_ok=True)
    doc = scratch / "validation.md"
    doc.write_text(raw, encoding="utf-8")
    return StepResult(
        status="ok",
        data={
            "verdict": verdict,
            "cycle": cycle,
            "validation_doc_path": str(doc),
            "validation_raw": raw,
            "spec_path": str(scratch / "build-spec.md"),
            "red_log_path": str(scratch / "build-red-output-cycle-1.log"),
        },
        duration_ms=0,
        step_name="run_validator",
    )


def _run_step(h: dict, raw: str, verdict: str, cycle: int = 1):
    from bytedigger_engine.workflows import phase_5_implement as p5

    prev = _prev(h["scratch"], raw, verdict, cycle)
    return p5._gate_on_validation(_ctx(h["scratch"]), prev)


def _no_gate_events(log) -> None:
    gate_events = [t for t in log.types() if t.startswith("verdict_gate")]
    assert gate_events == [], log.events


# ---------------------------------------------------------------------------
# AC5, AC6 (real step)
# ---------------------------------------------------------------------------

def test_ac5_verdict_pass_doc_proceeds_without_gate_event(harness) -> None:
    from bytedigger_engine.workflows import phase_5_implement as p5

    res = _run_step(harness, PASS_DOC, p5.VERDICT_PASS)
    assert res.status == "ok", (res.status, res.error_code, res.error)
    assert res.error_code is None
    assert res.data["verdict"] == p5.VERDICT_PASS, res.data
    _no_gate_events(harness["log"])


def test_ac6_verdict_fail_doc_rejected_at_cap_without_gate_event(harness) -> None:
    """cycle == cap (default cap 2): terminal rejection, E_VALIDATION_FAILED."""
    from bytedigger_engine.workflows import phase_5_implement as p5

    res = _run_step(harness, FAIL_DOC, p5.VERDICT_FAIL, cycle=p5.MAX_VALIDATION_CYCLES)
    assert res.status == "error", (res.status, res.error_code, res.error)
    assert res.error_code == "E_VALIDATION_FAILED", res.error_code
    assert res.data["verdict"] == p5.VERDICT_FAIL, res.data
    _no_gate_events(harness["log"])


def test_ac6_verdict_fail_doc_below_cap_loops_without_gate_event(harness) -> None:
    """cycle < cap: loop-continue result (ok, non-PASS verdict, cycle + 1)."""
    from bytedigger_engine.workflows import phase_5_implement as p5

    res = _run_step(harness, FAIL_DOC, p5.VERDICT_FAIL, cycle=1)
    assert res.status == "ok", (res.status, res.error_code, res.error)
    assert res.data["verdict"] == p5.VERDICT_FAIL, res.data
    assert res.data["cycle"] == 2, res.data
    _no_gate_events(harness["log"])


# ---------------------------------------------------------------------------
# AC7 (the lint tool itself is unchanged)
# ---------------------------------------------------------------------------

def _doc(tmp: Path, raw: str) -> str:
    p = tmp / "doc.md"
    p.write_text(raw, encoding="utf-8")
    return str(p)


def test_ac7_run_gate_unanchored(tmp_path) -> None:
    from bytedigger_engine.verdict_gate import run_gate

    rc, report = run_gate(_doc(tmp_path, UNANCHORED_DOC), str(tmp_path), {})
    assert rc == 1, (rc, report)
    assert report["result"] == "UNANCHORED", report


def test_ac7_run_gate_pass_doc_not_approved(tmp_path) -> None:
    from bytedigger_engine.verdict_gate import run_gate

    rc, report = run_gate(_doc(tmp_path, PASS_DOC), str(tmp_path), {})
    assert rc == 0, (rc, report)
    assert report["result"] == "NOT_APPROVED", report


def test_ac7_run_gate_kill_switch_skip(tmp_path) -> None:
    from bytedigger_engine.verdict_gate import run_gate

    rc, report = run_gate(
        _doc(tmp_path, UNANCHORED_DOC), str(tmp_path), {"HAL_VERDICT_GATE_LINT": "0"}
    )
    assert rc == 0, (rc, report)
    assert report == {"result": "SKIP"}, report
