"""RED tests for AB117AFA — preserve_events_log Step 0 in phase_8_post_deploy.

Covers all 5 Acceptance Criteria from the build-spec:
  AC1 happy path — source exists -> copied with identical content
  AC2 source missing -> graceful skip (skipped=True, reason="source_missing", no dest)
  AC3 copy failure -> graceful skip (copied=False, error populated, report still written)
  AC4 step ordering — preserve_events_log is steps[0]; cleanup_worktrees is steps[1]
  AC5 org_config override — events_log_path takes precedence over default location
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

from bytedigger_engine.contracts import WorkflowContext  # noqa: E402
from bytedigger_engine.engine import WorkflowEngine  # noqa: E402
from bytedigger_engine.workflows.phase_8_post_deploy import (  # noqa: E402
    CLEANUP_REPORT_RELPATH,
    phase_8_post_deploy_workflow,
)


def make_ctx(scratchpad: Path, *, working_dir: Path | None = None, **org_extra) -> WorkflowContext:
    org = {"scratchpad_dir": str(scratchpad), **org_extra}
    if working_dir is not None:
        org["working_dir"] = str(working_dir)
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question="cleanup",
        session_id="test-session",
        persona="hal",
        framework=None,
        domain=None,
    )


# --- AC4: step ordering -------------------------------------------------------


def test_preserve_events_log_is_first_step():
    """AC4: preserve_events_log must be steps[0]; persist_run_artifacts must be steps[1].
    (D21C846D inserted persist_learnings_to_db at steps[2]; cleanup_worktrees moved to steps[3].)"""
    wf = phase_8_post_deploy_workflow()
    assert wf.steps[0].name == "preserve_events_log"
    assert wf.steps[1].name == "persist_run_artifacts"
    assert wf.steps[2].name == "persist_learnings_to_db"
    assert wf.steps[3].name == "cleanup_worktrees"


# --- AC1: happy path ----------------------------------------------------------


def test_preserve_events_log_happy_path(tmp_path):
    """AC1: When source .bytedigger/events.jsonl exists, it is copied to
    <scratchpad>/post-deploy/events.jsonl with identical content."""
    working_dir = tmp_path / "repo"
    hal_build = working_dir / ".bytedigger"
    hal_build.mkdir(parents=True)
    known_content = '{"event":"start"}\n{"event":"end"}\n'
    (hal_build / "events.jsonl").write_text(known_content)

    scratchpad = tmp_path / "scratch"
    eng = WorkflowEngine()
    eng.register("p8", phase_8_post_deploy_workflow())
    result, _ = eng.execute("p8", make_ctx(scratchpad, working_dir=working_dir))

    dest = scratchpad / "post-deploy" / "events.jsonl"
    assert dest.exists(), "events.jsonl must be copied to <scratchpad>/post-deploy/events.jsonl"
    assert dest.read_text() == known_content, "copied content must match source exactly"
    step0_data = result.data["step_summary"]["preserve_events_log"]
    assert step0_data.get("copied") is True, "data['copied'] must be True on happy path"


# --- AC2: source missing ------------------------------------------------------


def test_preserve_events_log_source_missing_graceful_skip(tmp_path):
    """AC2: When source events.jsonl doesn't exist, Step 0 skips without raising.

    data["skipped"]==True, data["reason"]=="source_missing", dest file absent,
    downstream steps (cleanup_worktrees) still executed.
    """
    working_dir = tmp_path / "repo"
    working_dir.mkdir()
    # Deliberately no .bytedigger/events.jsonl at the default path

    scratchpad = tmp_path / "scratch"
    eng = WorkflowEngine()
    eng.register("p8", phase_8_post_deploy_workflow())
    result, _ = eng.execute("p8", make_ctx(scratchpad, working_dir=working_dir))

    dest = scratchpad / "post-deploy" / "events.jsonl"
    assert not dest.exists(), "dest file must NOT be created when source is missing"

    step0_data = result.data["step_summary"]["preserve_events_log"]
    assert step0_data.get("skipped") is True
    assert step0_data.get("reason") == "source_missing"

    assert "cleanup_worktrees" in result.data["step_summary"], (
        "downstream steps must still execute after graceful skip"
    )


# --- AC3: copy failure graceful -----------------------------------------------


def test_preserve_events_log_copy_failure_graceful(tmp_path):
    """AC3: When shutil.copy2 raises OSError, Step 0 returns ok with copied=False.

    data["error"] is populated. cleanup-report.md is still written (remaining steps ran).
    """
    working_dir = tmp_path / "repo"
    hal_build = working_dir / ".bytedigger"
    hal_build.mkdir(parents=True)
    (hal_build / "events.jsonl").write_text('{"event":"start"}\n')

    scratchpad = tmp_path / "scratch"
    eng = WorkflowEngine()
    eng.register("p8", phase_8_post_deploy_workflow())

    with patch("bytedigger_engine.workflows.phase_8_post_deploy.shutil.copy2", side_effect=OSError("simulated")):
        result, _ = eng.execute("p8", make_ctx(scratchpad, working_dir=working_dir))

    report = scratchpad / CLEANUP_REPORT_RELPATH
    assert report.exists(), "cleanup-report.md must still be written after copy failure"

    step0_data = result.data["step_summary"]["preserve_events_log"]
    assert step0_data.get("copied") is False
    assert step0_data.get("error"), "data['error'] must be populated with OSError repr"


# --- AC5: org_config override -------------------------------------------------


def test_preserve_events_log_org_config_override(tmp_path):
    """AC5: When org_config['events_log_path'] is set, Step 0 reads from that path
    instead of the default <working_dir>/.bytedigger/events.jsonl."""
    custom_src = tmp_path / "override" / "my-events.jsonl"
    custom_src.parent.mkdir(parents=True)
    custom_content = '{"event":"override"}\n'
    custom_src.write_text(custom_content)

    working_dir = tmp_path / "repo"
    working_dir.mkdir()
    # No .bytedigger/events.jsonl at the default location

    scratchpad = tmp_path / "scratch"
    eng = WorkflowEngine()
    eng.register("p8", phase_8_post_deploy_workflow())
    eng.execute(
        "p8",
        make_ctx(scratchpad, working_dir=working_dir, events_log_path=str(custom_src)),
    )

    dest = scratchpad / "post-deploy" / "events.jsonl"
    assert dest.exists(), "dest must exist when org_config override path points to an existing file"
    assert dest.read_text() == custom_content, "dest content must match the override source"
