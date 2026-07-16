"""6CBC19FA: assert logger.warning fires on git rc!=0 in cleanup steps,
behavior remains fail-open (status=ok).

Surfaces silent failure modes in `_cleanup_worktrees` and `_cleanup_gone_branches`
without changing fail-open semantics (matches "hooks = observability not
enforcement" pattern). See agreement 6CBC19FA.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "workflows"))

import phase_8_post_deploy  # noqa: E402
from contracts import WorkflowContext  # noqa: E402


def make_ctx(tmp_path: Path) -> WorkflowContext:
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    working = tmp_path / "work"
    working.mkdir(parents=True, exist_ok=True)
    org = {
        "scratchpad_dir": str(scratchpad),
        "working_dir": str(working),
    }
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


def test_cleanup_worktrees_logs_warning_on_git_rc_nonzero(caplog, monkeypatch, tmp_path):
    """When 'git worktree list' fails, _cleanup_worktrees logs a warning
    that includes '6CBC19FA' and the rc; still returns status=ok (fail-open)."""
    ctx = make_ctx(tmp_path)

    def fake_git(args, cwd, *, timeout=30):
        # Only the worktree list call is expected here; if anything else, fail loudly.
        assert args[:2] == ["worktree", "list"], f"unexpected git call: {args}"
        return (1, "", "stderr msg")

    monkeypatch.setattr(phase_8_post_deploy, "_git_read", fake_git)
    monkeypatch.setattr(phase_8_post_deploy, "_git_write", fake_git, raising=False)
    caplog.set_level(logging.WARNING, logger=phase_8_post_deploy.__name__)

    result = phase_8_post_deploy._cleanup_worktrees(ctx, None)

    assert result.status == "ok", "fail-open invariant must be preserved"

    matching = [
        r for r in caplog.records
        if r.levelno == logging.WARNING
        and "6CBC19FA" in r.getMessage()
        and "cleanup_worktrees" in r.getMessage()
    ]
    assert matching, (
        f"expected WARNING with '6CBC19FA' and 'cleanup_worktrees' in message; "
        f"got: {[(r.levelname, r.getMessage()) for r in caplog.records]}"
    )


def test_cleanup_gone_branches_logs_warning_on_branch_list_rc_nonzero(caplog, monkeypatch, tmp_path):
    """When 'git branch -vv' fails, _cleanup_gone_branches logs a warning
    including '6CBC19FA'; status=ok preserved."""
    ctx = make_ctx(tmp_path)

    def fake_git(args, cwd, *, timeout=30):
        if args[:1] == ["fetch"]:
            return (0, "", "")
        if args[:2] == ["branch", "-vv"]:
            return (1, "", "")
        raise AssertionError(f"unexpected git call: {args}")

    monkeypatch.setattr(phase_8_post_deploy, "_git_read", fake_git)
    monkeypatch.setattr(phase_8_post_deploy, "_git_write", fake_git, raising=False)
    caplog.set_level(logging.WARNING, logger=phase_8_post_deploy.__name__)

    result = phase_8_post_deploy._cleanup_gone_branches(ctx, None)

    assert result.status == "ok", "fail-open invariant must be preserved"

    matching = [
        r for r in caplog.records
        if r.levelno == logging.WARNING
        and "6CBC19FA" in r.getMessage()
        and "cleanup_gone_branches" in r.getMessage()
    ]
    assert matching, (
        f"expected WARNING with '6CBC19FA' and 'cleanup_gone_branches' in message; "
        f"got: {[(r.levelname, r.getMessage()) for r in caplog.records]}"
    )


def test_cleanup_gone_branches_logs_warning_on_fetch_rc_nonzero(caplog, monkeypatch, tmp_path):
    """When 'git fetch --prune' fails but branch listing succeeds,
    _cleanup_gone_branches logs warning for fetch + continues (status=ok)."""
    ctx = make_ctx(tmp_path)

    def fake_git(args, cwd, *, timeout=30):
        if args[:1] == ["fetch"]:
            return (1, "", "fetch stderr")
        if args[:2] == ["branch", "-vv"]:
            return (0, "", "")
        raise AssertionError(f"unexpected git call: {args}")

    monkeypatch.setattr(phase_8_post_deploy, "_git_read", fake_git)
    monkeypatch.setattr(phase_8_post_deploy, "_git_write", fake_git, raising=False)
    caplog.set_level(logging.WARNING, logger=phase_8_post_deploy.__name__)

    result = phase_8_post_deploy._cleanup_gone_branches(ctx, None)

    assert result.status == "ok", "fail-open invariant must be preserved"

    matching = [
        r for r in caplog.records
        if r.levelno == logging.WARNING
        and "6CBC19FA" in r.getMessage()
        and "fetch" in r.getMessage()
    ]
    assert matching, (
        f"expected WARNING with '6CBC19FA' and 'fetch' in message; "
        f"got: {[(r.levelname, r.getMessage()) for r in caplog.records]}"
    )
