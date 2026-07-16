"""RED tests for 9dd6ae54 — `integration_class_detected` false-positive on generic vocab.

AC1: _detect_integration_triggers("optimize fail-open runtime behavior") == []
AC5: phase_05_inject_workflow with task "optimize fail-open runtime retrieval"
     emits NO `integration_class_detected` event.

Both FAIL today because _INTEGRATION_TRIGGERS_CI = ("hook", "runtime") — "runtime"
still matches the generic phrase.  GREEN fix: drop "runtime" from the tuple.
"""
from __future__ import annotations

import json
from pathlib import Path

# sys.path management is handled by conftest.py (conftest-import-time singleton,
# §1q / 81F97F3D gate) — do NOT add sys.path.insert here.
from contracts import WorkflowContext
from engine import WorkflowEngine
from event_log import EventLog
from phase_05_inject import (
    _detect_integration_triggers,
    phase_05_inject_workflow,
)


def _write_minimal_checklist(scratchpad: Path) -> None:
    scratchpad.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema_version": "1.0.0",
        "session_id": "test-session-9dd6",
        "complexity": "SIMPLE",
        "mode": "AUTONOMOUS",
        "cwd": str(scratchpad.parent),
        "pre_build_gate_version": "1.0.0",
        "written_at_ts": 1777421657,
    }
    path = scratchpad / ".orchestrator-checklist.json"
    path.write_text(json.dumps(payload), encoding="utf-8")


def _make_ctx(scratchpad: Path, **org_extra) -> WorkflowContext:
    org = {"scratchpad_dir": str(scratchpad), **org_extra}
    _write_minimal_checklist(scratchpad)
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question="task",
        session_id="test-session-9dd6",
        persona="hal",
        framework=None,
        domain=None,
    )


def _integration_events(events: list[dict]) -> list[dict]:
    return [e for e in events if e["event_type"] == "integration_class_detected"]


# ─── AC1 ──────────────────────────────────────────────────────────────────────

def test_ac1_generic_runtime_vocab_no_triggers():
    """AC1: generic phrase containing 'runtime' must NOT match any trigger.

    FAILS today: _INTEGRATION_TRIGGERS_CI includes "runtime" → returns ["runtime"].
    GREEN fix: remove "runtime" from the tuple → returns [].
    """
    result = _detect_integration_triggers("optimize fail-open runtime behavior")
    assert result == [], (
        f"expected no triggers for generic-vocab input, got {result!r}; "
        "'runtime' should not be an integration trigger"
    )


# ─── AC5 ──────────────────────────────────────────────────────────────────────

def test_ac5_generic_vocab_emits_no_integration_event(tmp_path):
    """AC5: phase_05_inject_workflow with a generic-vocab task emits NO
    `integration_class_detected` event.

    FAILS today: "runtime" in task_description triggers a non-empty trigger list
    → _emit_safe fires.  GREEN fix: drop "runtime" → triggers empty → no emit.
    """
    log_path = tmp_path / "events.jsonl"
    log = EventLog(log_path)
    eng = WorkflowEngine(event_log=log)
    eng.register("phase_05_inject", phase_05_inject_workflow())

    eng.execute(
        "phase_05_inject",
        _make_ctx(
            tmp_path / "scratch",
            task_description="optimize fail-open runtime retrieval",
        ),
        run_id="rid-9dd6-ac5",
    )

    matches = _integration_events(EventLog(log_path).read_all())
    assert len(matches) == 0, (
        f"expected 0 integration_class_detected events for generic-vocab task, "
        f"got {len(matches)}: {matches!r}"
    )
