"""RED tests for agreement 5F9817F6 (MEDIUM, 2026-05-01).

CONTROL gate (companion to 467E0B3B ROOT CAUSE): emit
``review_aggregator_mass_unverified`` event when phase_6 aggregator
output contains N >= 3 verify_unverified findings AND satisfaction
verdict == PASS.

The "satisfaction PASS" condition is implicit: the new
``detect_mass_unverified`` step is the LAST step in
``phase_6_review_workflow``, after ``write_satisfaction_doc``. A failed
satisfaction step aborts the workflow with E_SATISFACTION_BELOW_THRESHOLD
(``recoverable=False``) — so if the detector runs at all, satisfaction
passed.

Source-of-truth: ``_verify_findings`` wrapper emits a
``phase_6_unverified_count`` event after each verify_findings cycle. The
detector reads events.jsonl for the current run_id, takes the LAST count
(final retry's), and re-emits ``review_aggregator_mass_unverified`` if
count >= 3.

Visibility-only — never blocks. Always returns status=ok.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "lib" / "plugins"))
sys.path.insert(0, str(HERE.parent / "workflows"))

from contracts import StepResult, WorkflowContext  # noqa: E402
from event_log import EventLog  # noqa: E402


def _ctx(question: str = "test") -> WorkflowContext:
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={},
        question=question,
        session_id="test-5F9817F6",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_prev(data):
    return StepResult(
        status="ok",
        data=data,
        duration_ms=0,
        step_name="prev",
    )


def _read_events(log_path: Path) -> list[dict]:
    if not log_path.exists():
        return []
    lines = log_path.read_text(encoding="utf-8").strip().splitlines()
    return [json.loads(line) for line in lines if line.strip()]


def _make_run_ctx(event_log: EventLog, run_id: str = "run-5F9817F6"):
    rc = MagicMock()
    rc.event_log = event_log
    rc.run_id = run_id
    return rc


# ─── verify_findings wrapper emits phase_6_unverified_count ────────────────


def test_verify_findings_emits_unverified_count_event(tmp_path):
    """``_verify_findings`` wrapper must emit ``phase_6_unverified_count``
    after the impl runs, with the count of UNVERIFIED findings as payload.
    """
    import phase_6_review as m
    import telemetry_ctx

    log_path = tmp_path / "events.jsonl"
    event_log = EventLog(path=log_path)
    run_ctx_mock = _make_run_ctx(event_log)

    review_doc = tmp_path / "build-review.md"
    review_doc.write_text(
        "# Composite Review\n\n"
        "## Aggregated Findings\n\n"
        "### SEVERITY: HIGH — Test finding A\n"
        "> nonexistent/file_a.py:1: fake quote\n"
        "Description.\n\n"
        "### SEVERITY: HIGH — Test finding B\n"
        "> nonexistent/file_b.py:1: fake quote\n"
        "Description.\n\n"
        "### SEVERITY: HIGH — Test finding C\n"
        "> nonexistent/file_c.py:1: fake quote\n"
        "Description.\n\n"
        "## Summary\n"
        "Findings total: 3 (CRITICAL: 0 / HIGH: 3 / MEDIUM: 0)\n",
        encoding="utf-8",
    )

    prev = _make_prev({"review_doc_path": str(review_doc), "verdict": "FAIL"})

    with patch.object(telemetry_ctx, "get_current_run", return_value=run_ctx_mock):
        result = m._verify_findings(_ctx(), prev)

    assert result.status == "ok", f"unexpected status {result.status!r}"
    assert result.data.get("verify_unverified") == 3, (
        f"expected 3 unverified, got {result.data!r}"
    )

    events = _read_events(log_path)
    matching = [
        e for e in events if e.get("event_type") == "phase_6_unverified_count"
    ]
    assert len(matching) == 1, (
        f"expected 1 phase_6_unverified_count event, got {len(matching)}; "
        f"all events: {[e.get('event_type') for e in events]}"
    )
    assert matching[0]["payload"]["count"] == 3, (
        f"expected count=3, got {matching[0]['payload']!r}"
    )


def test_verify_findings_zero_unverified_still_emits_count_event(tmp_path):
    """Source-of-truth event MUST fire even when count=0 — downstream
    detector relies on presence to confirm verify_findings ran.
    """
    import phase_6_review as m
    import telemetry_ctx

    log_path = tmp_path / "events.jsonl"
    event_log = EventLog(path=log_path)
    run_ctx_mock = _make_run_ctx(event_log)

    # Aggregated Findings section absent → counters stay 0 (per impl).
    review_doc = tmp_path / "build-review.md"
    review_doc.write_text(
        "# Composite Review\n\n"
        "VERDICT: PASS\n",
        encoding="utf-8",
    )
    prev = _make_prev({"review_doc_path": str(review_doc), "verdict": "PASS"})

    with patch.object(telemetry_ctx, "get_current_run", return_value=run_ctx_mock):
        m._verify_findings(_ctx(), prev)

    events = _read_events(log_path)
    matching = [
        e for e in events if e.get("event_type") == "phase_6_unverified_count"
    ]
    assert len(matching) == 1, (
        f"expected exactly 1 count event even at zero, got {len(matching)}"
    )
    assert matching[0]["payload"]["count"] == 0


# ─── detect_mass_unverified step behavior ─────────────────────────────────


def test_detect_mass_unverified_emits_alert_when_threshold_met(tmp_path):
    """When events.jsonl has phase_6_unverified_count with count >= 3
    for current run_id, ``_detect_mass_unverified`` must emit
    ``review_aggregator_mass_unverified`` event.
    """
    import phase_6_review as m
    import telemetry_ctx

    log_path = tmp_path / "events.jsonl"
    event_log = EventLog(path=log_path)
    run_id = "run-detect-1"
    # Seed with a prior unverified-count event for this run.
    event_log.append("phase_6_unverified_count", {"count": 5, "total": 5}, run_id=run_id)

    run_ctx_mock = _make_run_ctx(event_log, run_id=run_id)
    prev = _make_prev({"satisfaction_doc_path": "/tmp/anything.md"})

    with patch.object(telemetry_ctx, "get_current_run", return_value=run_ctx_mock):
        result = m._detect_mass_unverified(_ctx(), prev)

    assert result.status == "ok", f"detector must always return ok; got {result.status!r}"

    events = _read_events(log_path)
    alerts = [
        e for e in events
        if e.get("event_type") == "review_aggregator_mass_unverified"
    ]
    assert len(alerts) == 1, (
        f"expected 1 review_aggregator_mass_unverified alert, got {len(alerts)}; "
        f"all events: {[e.get('event_type') for e in events]}"
    )
    assert alerts[0]["payload"]["count"] == 5, (
        f"alert payload count mismatch: {alerts[0]['payload']!r}"
    )


def test_detect_mass_unverified_no_alert_below_threshold(tmp_path):
    """count < 3 must NOT emit the alert event."""
    import phase_6_review as m
    import telemetry_ctx

    log_path = tmp_path / "events.jsonl"
    event_log = EventLog(path=log_path)
    run_id = "run-detect-2"
    event_log.append("phase_6_unverified_count", {"count": 2, "total": 5}, run_id=run_id)

    run_ctx_mock = _make_run_ctx(event_log, run_id=run_id)
    prev = _make_prev({})

    with patch.object(telemetry_ctx, "get_current_run", return_value=run_ctx_mock):
        result = m._detect_mass_unverified(_ctx(), prev)

    assert result.status == "ok"
    events = _read_events(log_path)
    alerts = [
        e for e in events
        if e.get("event_type") == "review_aggregator_mass_unverified"
    ]
    assert len(alerts) == 0, (
        f"must NOT emit alert below threshold; got {len(alerts)} alerts"
    )


def test_detect_mass_unverified_uses_last_count_for_retry_cycles(tmp_path):
    """When phase_6 retries, multiple phase_6_unverified_count events fire
    for the same run. Detector must use the LAST one (final cycle's).
    """
    import phase_6_review as m
    import telemetry_ctx

    log_path = tmp_path / "events.jsonl"
    event_log = EventLog(path=log_path)
    run_id = "run-detect-3"
    # Cycle 1: high count (review found mass-UNVERIFIED, retry triggered)
    event_log.append("phase_6_unverified_count", {"count": 5, "total": 5}, run_id=run_id)
    # Cycle 2: count went down (paths fixed) → no alert.
    event_log.append("phase_6_unverified_count", {"count": 1, "total": 5}, run_id=run_id)

    run_ctx_mock = _make_run_ctx(event_log, run_id=run_id)
    prev = _make_prev({})

    with patch.object(telemetry_ctx, "get_current_run", return_value=run_ctx_mock):
        m._detect_mass_unverified(_ctx(), prev)

    events = _read_events(log_path)
    alerts = [
        e for e in events
        if e.get("event_type") == "review_aggregator_mass_unverified"
    ]
    assert len(alerts) == 0, (
        f"final-cycle count was 1 — must NOT alert; got {len(alerts)} alerts"
    )


def test_detect_mass_unverified_ignores_other_runs(tmp_path):
    """Counts from a different run_id must be ignored."""
    import phase_6_review as m
    import telemetry_ctx

    log_path = tmp_path / "events.jsonl"
    event_log = EventLog(path=log_path)
    event_log.append(
        "phase_6_unverified_count", {"count": 99, "total": 99}, run_id="other-run"
    )

    run_ctx_mock = _make_run_ctx(event_log, run_id="run-detect-4")
    prev = _make_prev({})

    with patch.object(telemetry_ctx, "get_current_run", return_value=run_ctx_mock):
        m._detect_mass_unverified(_ctx(), prev)

    events = _read_events(log_path)
    alerts = [
        e for e in events
        if e.get("event_type") == "review_aggregator_mass_unverified"
    ]
    assert len(alerts) == 0, (
        f"must not cross-pollute alert from other run_id; got {len(alerts)}"
    )


def test_detect_mass_unverified_robust_to_missing_event_log(tmp_path):
    """If telemetry_ctx has no run_ctx, detector must still return ok."""
    import phase_6_review as m
    import telemetry_ctx

    prev = _make_prev({})

    with patch.object(telemetry_ctx, "get_current_run", return_value=None):
        result = m._detect_mass_unverified(_ctx(), prev)

    assert result.status == "ok"
    assert result.step_name == "detect_mass_unverified"


# ─── workflow integration ─────────────────────────────────────────────────


def test_phase_6_workflow_includes_detect_mass_unverified_step():
    """detect_mass_unverified must be the LAST step in the workflow,
    so it only runs when satisfaction passed (failed satisfaction aborts).
    """
    import phase_6_review as m

    wf = m.phase_6_review_workflow()
    step_names = [s.name for s in wf.steps]
    assert step_names[-1] == "detect_mass_unverified", (
        f"detect_mass_unverified must be the LAST step; got order {step_names!r}"
    )
    # Must come AFTER write_satisfaction_doc.
    sat_idx = step_names.index("write_satisfaction_doc")
    detect_idx = step_names.index("detect_mass_unverified")
    assert detect_idx > sat_idx, (
        f"detect_mass_unverified must come after write_satisfaction_doc"
    )
