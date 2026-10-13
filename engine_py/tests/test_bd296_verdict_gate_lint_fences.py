"""Fence tests for bd#296: verdict-gate lint behaviour that must not change.

These pass before and after the flag HAL_VERDICT_GATE_LINT_ENFORCE is retired;
they are regression fences, not RED tests. The RED tests live in
test_bd296_verdict_gate_lint_permanent_warn.py.

Spec: docs/decisions/2026-10-13-bd296-verdict-gate-lint-enforce-retired.md

AC2  test_ac2_unanchored_approved_doc_warns_and_continues
AC4  test_ac4_kill_switch_emits_no_warn
AC5  test_ac5_agreeing_doc_emits_no_warn
AC6  test_ac6_driver_error_is_warn_and_fail_open_enforce_unset  (unset case)

Harness: the real `_gate_on_validation` step with a recording event log in the
telemetry slot (pre-staged and cleared, no timing, workflows.md 1i), cwd = a
tiny tmp tree so the lint can verify anchors. Only AC6 replaces a collaborator
(`_run_verdict_gate`), to force the driver-error branch. Units under test are
imported lazily inside bodies.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

RETIRED_FLAG = "HAL_VERDICT_GATE_LINT_ENFORCE"
EVENT = "verdict_gate_lint_warn"

UNANCHORED_DOC = "## Verdict\nPASS\n\nVERDICT: APPROVED\n"


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------

class _FakeEventLog:
    def __init__(self) -> None:
        self.events: list = []

    def append(self, event_type: str, payload: dict, run_id: str = "ad-hoc") -> None:
        self.events.append((event_type, dict(payload), run_id))

    def of(self, event_type: str) -> list:
        return [p for (t, p, _) in self.events if t == event_type]


@pytest.fixture
def harness(monkeypatch, tmp_path):
    from bytedigger_engine import telemetry_ctx

    monkeypatch.delenv("HAL_VERDICT_GATE_LINT", raising=False)
    monkeypatch.delenv(RETIRED_FLAG, raising=False)
    monkeypatch.chdir(tmp_path)

    telemetry_ctx.clear_current_run()
    log = _FakeEventLog()
    telemetry_ctx.set_current_run(
        event_log=log, run_id="bd296", step_name="gate_on_validation", phase="5", cycle=1,
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
        question="bd296", session_id="test-bd296", persona="hal", framework=None, domain=None,
    )


def _prev(scratch: Path, raw: str, cycle: int = 1):
    from bytedigger_engine.contracts import StepResult
    from bytedigger_engine.workflows import phase_5_implement as p5

    scratch.mkdir(parents=True, exist_ok=True)
    doc = scratch / "validation.md"
    doc.write_text(raw, encoding="utf-8")
    return StepResult(
        status="ok",
        data={
            "verdict": p5.VERDICT_PASS,
            "cycle": cycle,
            "validation_doc_path": str(doc),
            "validation_raw": raw,
            "spec_path": str(scratch / "build-spec.md"),
            "red_log_path": str(scratch / "build-red-output-cycle-1.log"),
        },
        duration_ms=0,
        step_name="run_validator",
    )


def _run_step(h: dict, raw: str, cycle: int = 1):
    from bytedigger_engine.workflows import phase_5_implement as p5

    prev = _prev(h["scratch"], raw, cycle)
    return p5._gate_on_validation(_ctx(h["scratch"]), prev), prev


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _agreeing_doc_text(tmp: Path) -> str:
    """Anchored spec + RED files (sha256 match), checklist without FAIL rows,
    AC ids equal to the spec's: the lint returns GATE_PASS on this."""
    spec = tmp / "docs" / "decisions" / "frozen-spec.md"
    red = tmp / "tests" / "test_red.py"
    spec.parent.mkdir(parents=True, exist_ok=True)
    red.parent.mkdir(parents=True, exist_ok=True)
    spec.write_text(
        "# Spec\n\n## Acceptance criteria\n\n"
        "| AC1 | first behaviour |\n| AC2 | second behaviour |\n",
        encoding="utf-8",
    )
    red.write_text("def test_x():\n    assert False\n", encoding="utf-8")
    return (
        "# gate r1\n\n"
        "## Checklist\n\n- AC1: PASS\n- AC2: PASS\n\n"
        "VERDICT: APPROVED\n\n"
        "<!-- verdict-anchor\n"
        f"spec: docs/decisions/frozen-spec.md sha256:{_sha(spec)}\n"
        f"red: tests/test_red.py sha256:{_sha(red)}\n"
        "-->\n"
    )


def _assert_ok_single_warn(res, log, prev, *, result: str, rc: int) -> None:
    assert res.status == "ok", (
        f"status={res.status!r} error_code={res.error_code!r} error={res.error!r}"
    )
    assert res.error_code is None, res.error_code
    warns = log.of(EVENT)
    assert len(warns) == 1, f"expected exactly one {EVENT}, got {log.events!r}"
    w = warns[0]
    assert w["result"] == result, w
    assert w["rc"] == rc, w
    assert w["doc"] == prev.data["validation_doc_path"], w
    assert w["phase"] == 5, w
    assert w["cycle"] == prev.data["cycle"], w


# ---------------------------------------------------------------------------
# AC2, AC4 - AC6 (real step)
# ---------------------------------------------------------------------------

def test_ac2_unanchored_approved_doc_warns_and_continues(harness) -> None:
    """Fence: passes before the change (the warn branch already emits)."""
    res, prev = _run_step(harness, UNANCHORED_DOC)
    _assert_ok_single_warn(res, harness["log"], prev, result="UNANCHORED", rc=1)


def test_ac4_kill_switch_emits_no_warn(harness, monkeypatch) -> None:
    """Fence: passes before the change."""
    monkeypatch.setenv("HAL_VERDICT_GATE_LINT", "0")
    res, _prev_ = _run_step(harness, UNANCHORED_DOC)
    assert res.status == "ok", (res.status, res.error_code)
    assert harness["log"].of(EVENT) == [], harness["log"].events


def test_ac5_agreeing_doc_emits_no_warn(harness) -> None:
    """Fence: passes before the change. Instrument check first: the doc really
    is GATE_PASS under the unchanged lint, so silence means agreement."""
    from bytedigger_engine.verdict_gate import run_gate

    raw = _agreeing_doc_text(harness["tmp"])
    probe = harness["tmp"] / "probe.md"
    probe.write_text(raw, encoding="utf-8")
    rc, report = run_gate(str(probe), str(harness["tmp"]), {})
    assert (rc, report.get("result")) == (0, "GATE_PASS"), (rc, report)

    res, _prev_ = _run_step(harness, raw)
    assert res.status == "ok", (res.status, res.error_code)
    assert harness["log"].of(EVENT) == [], harness["log"].events


def test_ac6_driver_error_is_warn_and_fail_open_enforce_unset(harness, monkeypatch) -> None:
    """Fence (the `unset` case of AC6; passes before the change)."""
    from bytedigger_engine.workflows import phase_5_implement as p5

    def _boom(*_a, **_kw):
        raise RuntimeError("driver exploded")

    monkeypatch.setattr(p5, "_run_verdict_gate", _boom)
    res, prev = _run_step(harness, UNANCHORED_DOC)
    _assert_ok_single_warn(res, harness["log"], prev, result="DRIVER_ERROR", rc=2)
