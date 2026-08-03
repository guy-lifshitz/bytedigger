"""Agreement B1AAACFB-sub1 — _commit_red_tests message enrichment.

Pre-fix message: ``build: red cycle N tests`` (carries no test path / branch
context — operator scanning git log can't tell which tests landed without
rerunning ``git show``).

Post-fix contract:
  - Subject line still starts with ``build: red cycle N tests`` (regression
    guard for any tooling that pattern-matches on it).
  - Subject includes branch name in parentheses: ``... [<branch>]``.
  - Body lists test path basenames; first 3 + ``...+M more`` if >5.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ENGINE_PY = Path(__file__).resolve().parent.parent
if str(ENGINE_PY) not in sys.path:
    sys.path.insert(0, str(ENGINE_PY))
WORKFLOWS = ENGINE_PY / "bytedigger_engine" / "workflows"
if str(WORKFLOWS) not in sys.path:
    sys.path.insert(0, str(WORKFLOWS))

from bytedigger_engine.contracts import StepResult, WorkflowContext  # type: ignore


def _make_ctx(scratchpad: Path, *, git_cwd: str) -> WorkflowContext:
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={"scratchpad_dir": str(scratchpad), "git_cwd": git_cwd},
        question="task",
        session_id="s",
        persona="hal",
        framework=None,
        domain=None,
    )


def _init_repo(repo: Path, branch: str = "main") -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", branch], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)
    # initial commit so HEAD exists
    (repo / "README.md").write_text("init")
    subprocess.run(["git", "add", "README.md"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)


def _last_commit_msg(repo: Path) -> str:
    return subprocess.check_output(
        ["git", "log", "-1", "--format=%B"], cwd=repo, text=True
    ).strip()


def test_commit_message_includes_branch_name(tmp_path):
    from bytedigger_engine.workflows.phase_5_implement import _commit_red_tests  # type: ignore

    repo = tmp_path / "repo"
    _init_repo(repo, branch="feat-xyz")

    test_file = repo / "tests" / "test_foo.py"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text("def test_foo(): assert False\n")

    scratch = tmp_path / "scratch"
    ctx = _make_ctx(scratch, git_cwd=str(repo))
    prev = StepResult(
        status="ok",
        data={"cycle": 1, "red_test_paths": ["tests/test_foo.py"]},
        duration_ms=0,
        step_name="write_red_artifact",
    )
    result = _commit_red_tests(ctx, prev)
    assert result.status == "ok", f"commit failed: {result.error}"
    msg = _last_commit_msg(repo)
    # Subject still leads with the canonical phrase
    assert msg.startswith("build: red cycle 1 tests"), msg
    # Branch name present (in subject or body)
    assert "feat-xyz" in msg, f"branch name missing in commit message: {msg!r}"


def test_commit_message_lists_test_basenames(tmp_path):
    from bytedigger_engine.workflows.phase_5_implement import _commit_red_tests  # type: ignore

    repo = tmp_path / "repo"
    _init_repo(repo)

    paths = ["tests/test_alpha.py", "tests/test_beta.py", "tests/test_gamma.py"]
    for p in paths:
        f = repo / p
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("def test_x(): pass\n")

    scratch = tmp_path / "scratch"
    ctx = _make_ctx(scratch, git_cwd=str(repo))
    prev = StepResult(
        status="ok",
        data={"cycle": 1, "red_test_paths": paths},
        duration_ms=0,
        step_name="write_red_artifact",
    )
    result = _commit_red_tests(ctx, prev)
    assert result.status == "ok", f"commit failed: {result.error}"
    msg = _last_commit_msg(repo)
    # Basenames present
    assert "test_alpha.py" in msg
    assert "test_beta.py" in msg
    assert "test_gamma.py" in msg


def test_commit_message_truncates_long_path_lists(tmp_path):
    """6+ paths → keep first 3 basenames + '...+N more'. Operators scan, not read."""
    from bytedigger_engine.workflows.phase_5_implement import _commit_red_tests  # type: ignore

    repo = tmp_path / "repo"
    _init_repo(repo)

    paths = [f"tests/test_{i}.py" for i in range(6)]  # 6 paths
    for p in paths:
        f = repo / p
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("def test_x(): pass\n")

    scratch = tmp_path / "scratch"
    ctx = _make_ctx(scratch, git_cwd=str(repo))
    prev = StepResult(
        status="ok",
        data={"cycle": 1, "red_test_paths": paths},
        duration_ms=0,
        step_name="write_red_artifact",
    )
    result = _commit_red_tests(ctx, prev)
    assert result.status == "ok", f"commit failed: {result.error}"
    msg = _last_commit_msg(repo)
    # First 3 basenames present
    assert "test_0.py" in msg
    assert "test_1.py" in msg
    assert "test_2.py" in msg
    # 4th+ should NOT be present (truncated)
    assert "test_5.py" not in msg, "expected 6th path to be truncated, but it appears"
    # Truncation marker present
    assert "+3 more" in msg, f"expected '+3 more' truncation marker; msg={msg!r}"


def test_commit_message_canonical_subject_unchanged_5_or_fewer_paths(tmp_path):
    """Regression guard: tooling that grep's the canonical subject line must
    still match. Subject line = first line. Must start with
    'build: red cycle N tests'.
    """
    from bytedigger_engine.workflows.phase_5_implement import _commit_red_tests  # type: ignore

    repo = tmp_path / "repo"
    _init_repo(repo)
    paths = ["tests/test_only.py"]
    for p in paths:
        f = repo / p
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("def test_x(): pass\n")

    scratch = tmp_path / "scratch"
    ctx = _make_ctx(scratch, git_cwd=str(repo))
    prev = StepResult(
        status="ok",
        data={"cycle": 2, "red_test_paths": paths},
        duration_ms=0,
        step_name="write_red_artifact",
    )
    result = _commit_red_tests(ctx, prev)
    assert result.status == "ok", f"commit failed: {result.error}"
    msg = _last_commit_msg(repo)
    subject = msg.splitlines()[0]
    assert subject.startswith("build: red cycle 2 tests"), subject
