"""Tests for phase_0_research workflow — Stage 2 step 1."""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "workflows"))

from contracts import WorkflowContext  # noqa: E402
from derive_state import replay  # noqa: E402
from engine import WorkflowEngine  # noqa: E402
from event_log import EventLog  # noqa: E402
from phase_0_research import (  # noqa: E402
    SUBDIRS,
    RESEARCH_STUB_FILENAME,
    phase_0_research_workflow,
)
import workflows  # noqa: E402


def make_ctx(scratchpad: Path, task: str = "Add foo to bar") -> WorkflowContext:
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={"scratchpad_dir": str(scratchpad)},
        question=task,
        session_id="test-session",
        persona="hal",
        framework=None,
        domain=None,
    )


# ─── unit ─────────────────────────────────────────────────────────────────────


def test_workflow_definition_shape():
    wf = phase_0_research_workflow()
    assert wf.name == "phase_0_research"
    assert [s.name for s in wf.steps] == ["init_scratchpad", "seed_research_doc"]


def test_init_scratchpad_creates_all_subdirs(tmp_path):
    eng = WorkflowEngine()
    eng.register("p0", phase_0_research_workflow())
    eng.execute("p0", make_ctx(tmp_path / "scratch"))

    for sub in SUBDIRS:
        assert (tmp_path / "scratch" / sub).is_dir()


def test_seed_research_doc_writes_file_with_task_and_session(tmp_path):
    eng = WorkflowEngine()
    eng.register("p0", phase_0_research_workflow())
    eng.execute("p0", make_ctx(tmp_path / "scratch", task="Migrate widget"))

    doc = tmp_path / "scratch" / "research" / RESEARCH_STUB_FILENAME
    assert doc.is_file()
    body = doc.read_text()
    assert "Migrate widget" in body
    assert "test-session" in body
    assert body.startswith("# Phase 0")


def test_default_scratchpad_when_org_config_omitted(tmp_path, monkeypatch):
    """If no scratchpad_dir in org_config, default to <cwd>/.bytedigger."""
    monkeypatch.chdir(tmp_path)
    ctx = WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=None,
        question="task",
        session_id="s",
        persona="hal",
        framework=None,
        domain=None,
    )
    eng = WorkflowEngine()
    eng.register("p0", phase_0_research_workflow())
    eng.execute("p0", ctx)
    assert (tmp_path / ".bytedigger" / "research" / RESEARCH_STUB_FILENAME).is_file()


def test_idempotent_when_scratchpad_already_exists(tmp_path):
    """Re-running phase_0 over a populated scratchpad must not error or wipe state."""
    scratchpad = tmp_path / "scratch"
    (scratchpad / "research").mkdir(parents=True)
    sentinel = scratchpad / "research" / "do-not-touch.txt"
    sentinel.write_text("preexisting")

    eng = WorkflowEngine()
    eng.register("p0", phase_0_research_workflow())
    result, _ = eng.execute("p0", make_ctx(scratchpad))

    assert result.status == "ok"
    assert sentinel.read_text() == "preexisting"  # unrelated files preserved


# ─── verification: events emitted, derived state matches expectation ──────────


def test_events_emitted_and_derived_state_matches_spec(tmp_path):
    """Stage 2 step 1 acceptance:
    (a) research stub doc emitted,
    (b) two `step_finished` events with status='ok',
    (c) derived state shows workflow_name=phase_0_research, status=ok.
    """
    log_path = tmp_path / "events.jsonl"
    log = EventLog(log_path)
    eng = WorkflowEngine(event_log=log)
    eng.register("phase_0_research", phase_0_research_workflow())

    eng.execute("phase_0_research", make_ctx(tmp_path / "scratch"), run_id="rid-p0")

    # (a) doc emitted
    doc = tmp_path / "scratch" / "research" / RESEARCH_STUB_FILENAME
    assert doc.is_file()

    events = EventLog(log_path).read_all()
    finished_steps = [e for e in events if e["event_type"] == "step_finished"]

    # (b) two step_finished events, both ok
    assert len(finished_steps) == 2
    assert [e["payload"]["status"] for e in finished_steps] == ["ok", "ok"]
    assert [e["payload"]["step_name"] for e in finished_steps] == [
        "init_scratchpad",
        "seed_research_doc",
    ]

    # (c) derived state
    state = replay(events)
    run = state["runs"]["rid-p0"]
    assert run["workflow_name"] == "phase_0_research"
    assert run["status"] == "ok"
    assert len(run["steps"]) == 2
    assert all(s["status"] == "ok" for s in run["steps"])


def test_registry_includes_phase_0_research():
    eng = WorkflowEngine()
    workflows.register_all(eng)
    assert "phase_0_research" in eng.registered()
