"""Category B — files_touched telemetry (decree 2026-04-26).

Verifies engine emits files_touched after each step, with bucketed
A/M/D paths from `git diff --name-status`. Degrades gracefully when
cwd is not a git repo (no event, no error).
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

import pytest

from bytedigger_engine.contracts import (
    WorkflowContext,
    StepResult,
    StepContract,
    WorkflowDefinition,
)
from bytedigger_engine.engine import WorkflowEngine


class _FakeEventLog:
    def __init__(self):
        self.events: list[tuple[str, dict, str]] = []

    def append(self, event_type: str, payload: dict, run_id: str | None = None) -> None:
        self.events.append((event_type, payload, run_id or "ad-hoc"))


def make_ctx(git_cwd: str | None = None) -> WorkflowContext:
    """GH1082 Part B: callers now pin the tree explicitly via org_config
    instead of depending on the ambient-cwd scan this ship removes."""
    return WorkflowContext(
        tenant_id="t", scope=None, db_path=None,
        org_config={"git_cwd": git_cwd} if git_cwd else None,
        question="q", session_id="s", persona="p", framework=None, domain=None,
    )


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


@pytest.fixture
def git_repo(tmp_path, monkeypatch):
    """Initialise a git repo in tmp_path with one committed file, chdir into it."""
    _git(tmp_path, "init", "-q", "-b", "main")
    _git(tmp_path, "config", "user.email", "t@t")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "seed.txt").write_text("seed\n")
    _git(tmp_path, "add", "seed.txt")
    _git(tmp_path, "commit", "-q", "-m", "init")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def make_step_that_modifies(name: str, fn):
    def _run(_ctx, _prev):
        fn()
        return StepResult(status="ok", data=None, duration_ms=0, step_name=name)
    return StepContract(name=name, execute=_run)


def test_modified_file_emits_files_touched_with_M_status(git_repo):
    log = _FakeEventLog()
    eng = WorkflowEngine(event_log=log)

    def modify():
        (git_repo / "seed.txt").write_text("changed\n")

    eng.register("wf", WorkflowDefinition(
        name="wf", steps=[make_step_that_modifies("modify_step", modify)],
    ))
    eng.execute("wf", make_ctx(str(git_repo)), run_id="r1")

    touched = [e for e in log.events if e[0] == "files_touched"]
    assert len(touched) == 1
    payload = touched[0][1]
    assert payload["step_name"] == "modify_step"
    assert "seed.txt" in payload["paths"]
    assert "seed.txt" in payload["by_status"].get("M", [])


def test_added_file_bucketed_under_A(git_repo):
    log = _FakeEventLog()
    eng = WorkflowEngine(event_log=log)

    def add():
        (git_repo / "newfile.txt").write_text("new\n")

    eng.register("wf", WorkflowDefinition(
        name="wf", steps=[make_step_that_modifies("add_step", add)],
    ))
    eng.execute("wf", make_ctx(str(git_repo)), run_id="r1")

    touched = [e for e in log.events if e[0] == "files_touched"][0]
    payload = touched[1]
    assert "newfile.txt" in payload["paths"]
    # Untracked files show up as ?? in --name-status; we use diff against HEAD
    # which doesn't show untracked. Need to git-add first OR use ls-files.
    # We choose --name-status HEAD + ls-files --others to capture both.
    assert "newfile.txt" in payload["by_status"].get("A", [])


def test_deleted_file_bucketed_under_D(git_repo):
    log = _FakeEventLog()
    eng = WorkflowEngine(event_log=log)

    def delete():
        (git_repo / "seed.txt").unlink()

    eng.register("wf", WorkflowDefinition(
        name="wf", steps=[make_step_that_modifies("del_step", delete)],
    ))
    eng.execute("wf", make_ctx(str(git_repo)), run_id="r1")

    touched = [e for e in log.events if e[0] == "files_touched"][0]
    payload = touched[1]
    assert "seed.txt" in payload["paths"]
    assert "seed.txt" in payload["by_status"].get("D", [])


def test_no_changes_emits_no_event(git_repo):
    """Decree choice: omit event when no files touched (simpler than empty payload)."""
    log = _FakeEventLog()
    eng = WorkflowEngine(event_log=log)

    def noop():
        pass

    eng.register("wf", WorkflowDefinition(
        name="wf", steps=[make_step_that_modifies("noop_step", noop)],
    ))
    eng.execute("wf", make_ctx(str(git_repo)), run_id="r1")

    touched = [e for e in log.events if e[0] == "files_touched"]
    assert touched == []  # no event when no delta


def test_non_git_cwd_no_event_emitted(tmp_path, monkeypatch):
    """Degraded mode: not a git repo → no files_touched event."""
    monkeypatch.chdir(tmp_path)  # tmp_path is NOT a git repo
    log = _FakeEventLog()
    eng = WorkflowEngine(event_log=log)

    def _run(_ctx, _prev):
        return StepResult(status="ok", data=None, duration_ms=0, step_name="s")

    eng.register("wf", WorkflowDefinition(
        name="wf", steps=[StepContract(name="s", execute=_run)],
    ))
    eng.execute("wf", make_ctx(str(tmp_path)), run_id="r1")

    touched = [e for e in log.events if e[0] == "files_touched"]
    assert touched == []  # no event in non-git cwd


def test_files_touched_per_step_in_multistep_workflow(git_repo):
    log = _FakeEventLog()
    eng = WorkflowEngine(event_log=log)

    def step1():
        (git_repo / "f1.txt").write_text("a\n")

    def step2():
        (git_repo / "f2.txt").write_text("b\n")

    eng.register("wf", WorkflowDefinition(
        name="wf",
        steps=[
            make_step_that_modifies("s1", step1),
            make_step_that_modifies("s2", step2),
        ],
    ))
    eng.execute("wf", make_ctx(str(git_repo)), run_id="r1")

    touched = [e for e in log.events if e[0] == "files_touched"]
    assert len(touched) == 2
    s1 = next(t for t in touched if t[1]["step_name"] == "s1")
    s2 = next(t for t in touched if t[1]["step_name"] == "s2")
    assert "f1.txt" in s1[1]["paths"]
    assert "f1.txt" not in s2[1]["paths"]  # step 2 only sees its own delta
    assert "f2.txt" in s2[1]["paths"]
