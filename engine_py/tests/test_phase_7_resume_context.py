"""GH450: phase_7 resumed-run context rehydration (digest behaviour kept).

Spec: SHARED/memory/Decisions/2026-07-09_GH450_synth_resume_context_spec.md

bd#89 P3c re-point: the phase-7 prompt is gone (the engine writes the report from
the event log), so the prompt-embedding asserts of AC1-AC3, AC5 and AC7 became
report-text asserts on ``post-deploy/post-deploy-report.md``, and the digest
helpers ``_collect_completed_phases`` / ``_completed_phase_digest`` keep their
direct tests. Retired: AC4 (the empty-diff / ``git log --stat -10`` prompt
guidance; the report has no prompt and no git call).

The report step is reached through the workflow definition by name; a missing
step fails that test only. No ``sys.path`` manipulation.
"""
from __future__ import annotations

import json
from pathlib import Path

from bytedigger_engine.contracts import WorkflowContext
from bytedigger_engine import derive_state as ds
from bytedigger_engine.workflows import phase_7_synthesize as p7
from bytedigger_engine.workflows.phase_7_synthesize import (
    FIX_DOC_RELPATH,
    REPORT_DOC_RELPATH,
    REVIEW_DOC_RELPATH,
    SATISFACTION_DOC_RELPATH,
    SPEC_DOC_RELPATH,
)

_STEP = "write_post_deploy_report"


def make_ctx(scratchpad: Path, *, question: str = "Add foo to bar", **org_extra) -> WorkflowContext:
    org = {"scratchpad_dir": str(scratchpad), **org_extra}
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question=question,
        session_id="test-session",
        persona="hal",
        framework=None,
        domain=None,
    )


def _seed_all_docs(scratchpad: Path) -> None:
    for relpath, body in (
        (SPEC_DOC_RELPATH, "## US1\nAdd foo\n"),
        (REVIEW_DOC_RELPATH, "Composite review.\nVERDICT: PASS\n"),
        (FIX_DOC_RELPATH, "FIX SKIPPED — no findings\n"),
        (SATISFACTION_DOC_RELPATH, "SCORE: 95\nVERDICT: PASS\n"),
    ):
        doc = scratchpad / relpath
        doc.parent.mkdir(parents=True, exist_ok=True)
        doc.write_text(body)


def _write_events(log_path: Path, events: list[dict]) -> None:
    log_path.write_text("\n".join(json.dumps(e) for e in events) + "\n")


def _finished_event(run_id: str, workflow_name: str, ts: str) -> dict:
    return {
        "ts": ts,
        "run_id": run_id,
        "event_type": "workflow_finished",
        "payload": {"workflow_name": workflow_name, "status": "ok", "wall_ms": 1000},
    }


_RESUMED_EVENTS = [
    _finished_event("r1", "phase_45_spec", "2026-07-09T10:00:00.000Z"),
    _finished_event("r2", "phase_5_implement", "2026-07-09T10:01:00.000Z"),
    _finished_event("r3", "phase_5_implement", "2026-07-09T10:02:00.000Z"),
    _finished_event("r4", "phase_6_review", "2026-07-09T10:03:00.000Z"),
    _finished_event("r5", "phase_7_synthesize", "2026-07-09T10:04:00.000Z"),
]


def _run_report(ctx: WorkflowContext):
    """Run the one deterministic phase-7 step; return (StepResult, report text)."""
    wf = p7.phase_7_synthesize_workflow()
    step = next((s for s in wf.steps if s.name == _STEP), None)
    assert step is not None, f"phase_7 has no step {_STEP!r}; steps: {[s.name for s in wf.steps]}"
    result = step.execute(ctx, None)
    scratchpad = Path(ctx.org_config["scratchpad_dir"])
    return result, (scratchpad / REPORT_DOC_RELPATH).read_text(encoding="utf-8")


def _completed_section(text: str) -> str:
    head = "## Completed Phases"
    assert head in text, text
    return text.split(head, 1)[1].split("\n## ", 1)[0]


def test_ac1_completed_phases_listed_in_report(tmp_path, monkeypatch):
    """AC1: resumed-run fixture -> report lists the completed phases."""
    log_path = tmp_path / "build-events.jsonl"
    _write_events(log_path, _RESUMED_EVENTS)
    monkeypatch.setattr(ds, "default_log_path", lambda: log_path, raising=False)

    scratchpad = tmp_path / "scratch"
    _seed_all_docs(scratchpad)
    _, report = _run_report(make_ctx(scratchpad))

    section = _completed_section(report)
    assert "- phase_5_implement" in section
    assert "- phase_45_spec" in section
    assert "- phase_6_review" in section


def test_ac2_report_excludes_phase_7_self(tmp_path, monkeypatch):
    """AC2: literal '- phase_7_synthesize' absent from the Completed Phases section."""
    log_path = tmp_path / "build-events.jsonl"
    _write_events(log_path, _RESUMED_EVENTS)
    monkeypatch.setattr(ds, "default_log_path", lambda: log_path, raising=False)

    scratchpad = tmp_path / "scratch"
    _seed_all_docs(scratchpad)
    _, report = _run_report(make_ctx(scratchpad))

    section = _completed_section(report)
    assert "- phase_5_implement" in section
    assert "- phase_7_synthesize" not in report


def test_ac3_artifacts_on_disk_all_present(tmp_path, monkeypatch):
    """AC3a: all 4 docs present -> Artifacts section shows all PRESENT."""
    log_path = tmp_path / "build-events.jsonl"
    _write_events(log_path, _RESUMED_EVENTS)
    monkeypatch.setattr(ds, "default_log_path", lambda: log_path, raising=False)

    scratchpad = tmp_path / "scratch"
    _seed_all_docs(scratchpad)
    _, report = _run_report(make_ctx(scratchpad))

    assert "## Artifacts" in report
    assert "spec: PRESENT" in report
    assert "review: PRESENT" in report
    assert "fix: PRESENT" in report
    assert "satisfaction: PRESENT" in report


def test_ac3_artifacts_on_disk_review_missing(tmp_path, monkeypatch):
    """AC3b: review doc deleted -> Artifacts section shows review: MISSING."""
    log_path = tmp_path / "build-events.jsonl"
    _write_events(log_path, _RESUMED_EVENTS)
    monkeypatch.setattr(ds, "default_log_path", lambda: log_path, raising=False)

    scratchpad = tmp_path / "scratch"
    _seed_all_docs(scratchpad)
    (scratchpad / REVIEW_DOC_RELPATH).unlink()
    _, report = _run_report(make_ctx(scratchpad))

    assert "review: MISSING" in report
    assert "spec: PRESENT" in report


def test_ac5_fresh_run_no_completed_phases_digest_and_empty_list(tmp_path, monkeypatch):
    """AC5: empty events.jsonl -> digest '', collected list [], report says none recorded."""
    log_path = tmp_path / "build-events.jsonl"
    log_path.write_text("")
    monkeypatch.setattr(ds, "default_log_path", lambda: log_path, raising=False)

    scratchpad = tmp_path / "scratch"
    _seed_all_docs(scratchpad)
    ctx = make_ctx(scratchpad)

    assert p7._completed_phase_digest(ctx) == ""
    assert p7._collect_completed_phases(ctx) == []

    result, report = _run_report(ctx)
    assert result.data["completed_phases"] == []
    assert "none recorded" in _completed_section(report)


def test_ac6_default_log_path_raises_best_effort_guard(tmp_path, monkeypatch):
    """AC6: default_log_path raising -> digest '' and the report step still status ok."""

    def _raise():
        raise RuntimeError("boom")

    monkeypatch.setattr(ds, "default_log_path", _raise, raising=False)

    scratchpad = tmp_path / "scratch"
    _seed_all_docs(scratchpad)
    ctx = make_ctx(scratchpad)

    assert p7._completed_phase_digest(ctx) == ""
    assert p7._collect_completed_phases(ctx) == []

    result, _ = _run_report(ctx)
    assert result.status == "ok"


def test_ac7_completed_phases_deduped_first_seen_order(tmp_path, monkeypatch):
    """AC7: collected list and result data are first-seen deduped; phase_5_implement once."""
    log_path = tmp_path / "build-events.jsonl"
    _write_events(log_path, _RESUMED_EVENTS)
    monkeypatch.setattr(ds, "default_log_path", lambda: log_path, raising=False)

    scratchpad = tmp_path / "scratch"
    _seed_all_docs(scratchpad)
    ctx = make_ctx(scratchpad)
    expected = ["phase_45_spec", "phase_5_implement", "phase_6_review"]

    assert p7._collect_completed_phases(ctx) == expected
    digest = p7._completed_phase_digest(ctx)
    assert digest.splitlines()[0].startswith("COMPLETED PHASES")
    assert [ln.strip()[2:] for ln in digest.splitlines()[1:]] == expected

    result, _ = _run_report(ctx)
    assert result.data["completed_phases"] == expected
