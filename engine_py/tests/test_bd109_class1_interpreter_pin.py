"""Pin tests for bd#109 class 1 (venv-less fresh worktree), GREEN today, split from the RED file.

Spec: docs/decisions/2026-10-16-bd109-build-failure-classes.md (section 3, AC1-AC2).

  AC1 test_ac1_*  venv-less linked worktree resolves to the main checkout's .venv
  AC2 test_ac2_*  no venv anywhere: ("", "none"), no raise, interpreter_unresolved warning

Class 1 needs no production change; these pin the existing behaviour on a real `git worktree add`.
The RED tests for classes 2 and 3 (AC3-AC9) live in test_bd109_build_failure_classes.py.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from bytedigger_engine import telemetry_ctx


@pytest.fixture(autouse=True)
def _isolated(monkeypatch):
    for k in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE", "GIT_COMMON_DIR"):
        monkeypatch.delenv(k, raising=False)
    telemetry_ctx.clear_current_run()
    yield
    telemetry_ctx.clear_current_run()


# --- helpers -------------------------------------------------------------------


def _git(cwd: Path, *args: str) -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update({"GIT_CONFIG_NOSYSTEM": "1", "HOME": str(cwd)})
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t", "-c", "commit.gpgsign=false", *args],
        cwd=str(cwd), env=env, check=True, capture_output=True, timeout=30,
    )


def _real_linked_worktree(tmp_path: Path, *, main_venv: bool) -> tuple[Path, Path]:
    main = tmp_path.resolve() / "main"
    main.mkdir(parents=True)
    _git(main, "init", "-q")
    (main / "mod.py").write_text("VALUE = 1\n")
    _git(main, "add", "mod.py")
    _git(main, "commit", "-q", "-m", "init")
    if main_venv:
        bindir = main / ".venv" / "bin"
        bindir.mkdir(parents=True)
        for name in ("python", "pytest"):
            exe = bindir / name
            exe.write_text("#!/bin/sh\nexit 0\n")
            exe.chmod(0o755)
    wt = tmp_path.resolve() / "wt"
    _git(main, "worktree", "add", "-q", "-b", "wt-branch", str(wt))
    assert (wt / ".git").is_file(), "fixture: wt must be a linked worktree"
    assert not os.path.lexists(wt / ".venv"), "fixture: wt must be venv-less"
    return main, wt


class _FakeEventLog:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict, str]] = []

    def append(self, event_type: str, payload: dict, run_id: str | None = None) -> None:
        self.events.append((event_type, payload, run_id or "ad-hoc"))


# --- AC1 / AC2: class 1 pins ----------------------------------------------------


def test_ac1_venvless_linked_worktree_resolves_to_main_checkout_venv(tmp_path):
    from bytedigger_engine.lib.interpreter import resolve_project_python

    main, wt = _real_linked_worktree(tmp_path, main_venv=True)
    path, source = resolve_project_python(str(wt))
    assert source != "none", (path, source)
    assert os.path.dirname(os.path.realpath(path)) == os.path.realpath(str(main / ".venv" / "bin")), path


def test_ac2_no_venv_anywhere_returns_none_and_warns_without_raising(tmp_path):
    from bytedigger_engine.lib.interpreter import resolve_project_python

    _main, wt = _real_linked_worktree(tmp_path, main_venv=False)
    log = _FakeEventLog()
    telemetry_ctx.set_current_run(
        event_log=log, run_id="bd109-ac2", step_name="resolve_project_python", phase="lib.interpreter",
    )
    path, source = resolve_project_python(str(wt))
    assert (path, source) == ("", "none")
    hits = [p for t, p, _ in log.events if t == "interpreter_unresolved"]
    assert len(hits) == 1, [t for t, _, _ in log.events]
    assert hits[0].get("severity") == "warning", hits[0]
