"""Kept phase-7 helpers: ``_telemetry_digest``, ``_satisfaction_state``, the doc-path constants.

Moved here by bd#89 P3c from the retired ``test_phase_7_synthesize.py`` (which drove
the synthesizer LLM path). The helpers are unchanged; the old tests read the digest
through the synthesizer prompt, these call the helper directly. The completed-phase
helpers keep their tests in ``test_phase_7_resume_context.py``.

No ``sys.path`` manipulation; no mocks of the helpers under test.
"""
from __future__ import annotations

import json
from pathlib import Path

from bytedigger_engine import derive_state as ds
from bytedigger_engine.contracts import WorkflowContext
from bytedigger_engine.workflows import phase_7_synthesize as p7


def _ctx(scratchpad: Path, **org_extra) -> WorkflowContext:
    return WorkflowContext(
        tenant_id="hal", scope=None, db_path=None,
        org_config={"scratchpad_dir": str(scratchpad), **org_extra},
        question="Add foo to bar", session_id="test-session", persona="hal",
        framework=None, domain=None,
    )


def _point_log_at(monkeypatch, path: Path, events: list[dict]) -> None:
    path.write_text("".join(json.dumps(e) + "\n" for e in events))
    monkeypatch.setattr(ds, "default_log_path", lambda: path, raising=False)


_TWO_PHASE_EVENTS = [
    {"ts": "2026-05-02T20:00:00.000Z", "run_id": "rA", "event_type": "workflow_started",
     "payload": {"workflow_name": "phase_5_implement"}},
    {"ts": "2026-05-02T20:00:10.000Z", "run_id": "rA", "event_type": "subprocess_exited",
     "payload": {"phase": "phase_5_implement", "model": "sonnet", "cost_usd": 0.12, "pid": 1}},
    {"ts": "2026-05-02T20:00:20.000Z", "run_id": "rA", "event_type": "workflow_finished",
     "payload": {"workflow_name": "phase_5_implement", "status": "ok", "wall_ms": 20000}},
    {"ts": "2026-05-02T20:00:25.000Z", "run_id": "rB", "event_type": "workflow_started",
     "payload": {"workflow_name": "phase_6_review"}},
    {"ts": "2026-05-02T20:00:35.000Z", "run_id": "rB", "event_type": "subprocess_exited",
     "payload": {"phase": "phase_6_review", "model": "haiku", "cost_usd": 0.03, "pid": 2}},
    {"ts": "2026-05-02T20:00:40.000Z", "run_id": "rB", "event_type": "workflow_finished",
     "payload": {"workflow_name": "phase_6_review", "status": "ok", "wall_ms": 15000}},
]


def test_canonical_doc_paths_and_deleted_docs_constants():
    assert p7.REPORT_DOC_RELPATH == "post-deploy/post-deploy-report.md"
    assert p7.SPEC_DOC_RELPATH == "specs/build-spec.md"
    assert p7.REVIEW_DOC_RELPATH == "reviews/build-review.md"
    assert p7.FIX_DOC_RELPATH == "reviews/build-fix.md"
    assert p7.SATISFACTION_DOC_RELPATH == "reviews/build-satisfaction.md"
    for deleted in (
        "DOCS_DOC_RELPATH", "DEFAULT_DOCS_TIMEOUT_SEC", "DOCS_COMPLETE",
        "DOCS_SKIPPED", "DOCS_BLOCKED", "DOCS_NO_MARKER",
    ):
        assert not hasattr(p7, deleted), f"{deleted} should have been deleted from phase_7_synthesize"


def test_telemetry_digest_disabled_by_default(tmp_path, monkeypatch):
    _point_log_at(monkeypatch, tmp_path / "build-events.jsonl", _TWO_PHASE_EVENTS)
    assert p7._telemetry_digest(_ctx(tmp_path / "scratch")) == ""


def test_telemetry_digest_enabled_includes_phase_summary(tmp_path, monkeypatch):
    _point_log_at(monkeypatch, tmp_path / "build-events.jsonl", _TWO_PHASE_EVENTS)
    digest = p7._telemetry_digest(_ctx(tmp_path / "scratch", include_telemetry_digest=True))
    assert digest.startswith("TELEMETRY")
    assert "phase_5_implement" in digest
    assert "phase_6_review" in digest
    assert "0.12" in digest or "0.1200" in digest
    assert "20.0s" in digest or "20s" in digest


def test_telemetry_digest_excludes_phase_7_self(tmp_path, monkeypatch):
    events = [
        {"ts": "2026-05-02T20:00:00.000Z", "run_id": "r1", "event_type": "workflow_finished",
         "payload": {"workflow_name": "phase_5_implement", "status": "ok", "wall_ms": 1000}},
        {"ts": "2026-05-02T20:00:01.000Z", "run_id": "r2", "event_type": "workflow_finished",
         "payload": {"workflow_name": "phase_7_synthesize", "status": "ok", "wall_ms": 500}},
    ]
    _point_log_at(monkeypatch, tmp_path / "build-events.jsonl", events)
    digest = p7._telemetry_digest(_ctx(tmp_path / "scratch", include_telemetry_digest=True))
    assert "phase_5_implement" in digest
    assert "phase_7_synthesize" not in digest


def test_telemetry_digest_empty_log_is_empty_string(tmp_path, monkeypatch):
    _point_log_at(monkeypatch, tmp_path / "build-events.jsonl", [])
    assert p7._telemetry_digest(_ctx(tmp_path / "scratch", include_telemetry_digest=True)) == ""


def test_satisfaction_state_three_states(tmp_path):
    missing = tmp_path / "missing.md"
    real = tmp_path / "real.md"
    real.write_text("SCORE: 95\nVERDICT: PASS\n")
    stub = tmp_path / "stub.md"
    stub.write_text("# Build satisfaction\n\nSATISFACTION: NOT_ASSESSED\n")
    assert p7._satisfaction_state(missing) == p7.SATISFACTION_MISSING == "MISSING"
    assert p7._satisfaction_state(real) == p7.SATISFACTION_PRESENT == "PRESENT"
    assert p7._satisfaction_state(stub) == p7.SATISFACTION_NOT_ASSESSED == "NOT_ASSESSED"
