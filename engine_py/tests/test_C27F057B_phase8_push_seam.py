"""C27F057B: RED tests — phase_8_post_deploy push callsite migrated to git_write seam + _git deleted.

Spec AC summary:
  AC1 — _ship_to_pr routes push through write-spy (set_default_git_write_factory)
  AC2 — legacy _git helper is deleted from module surface

Forcing function (§1l / §3):
  AC1: Pre-GREEN push still calls _git (bounded_run path), bypassing the injectable
       write factory — the write-spy records zero calls, assertion FAILS.
       Post-GREEN: push routes through _git_write → factory spy → argv recorded → PASS.
  AC2: Pre-GREEN _git exists → hasattr returns True → assert not hasattr FAILS.
       Post-GREEN: _git deleted → hasattr returns False → PASS.

§1i (singleton-resource, workflows.md §1i):
  autouse fixture _reset_factories calls reset_default_git_write_factory() AND
  reset_default_git_read_factory() in both setup and teardown so injected factories
  never leak across tests.

§1q / D1CF5FDF (collectability):
  WorkflowContext is imported INSIDE the test-function body per §1q — deferred to
  assert time, not collection time.
  _git is referenced ONLY via the string in hasattr(), never imported at module level
  — file collects cleanly even pre-GREEN when _git still exists.

Pre-GREEN FAIL classification (expected: 2 FAIL / 0 PASS):
  AC1 → FAIL  — write-spy.calls is []; push still calls _git (bounded_run), not _git_write
  AC2 → FAIL  — _git exists on module → assert not hasattr fails
"""
from __future__ import annotations

import pytest

import phase_8_post_deploy
from lib.git_port import (
    GitResult,
    reset_default_git_read_factory,
    set_default_git_read_factory,
)
from lib.git_write_port import (
    reset_default_git_write_factory,
    set_default_git_write_factory,
)


# ── Recording write-spy ───────────────────────────────────────────────────────


class _SpyGitWrite:
    """Recording spy matching GitWritePort.op_capture / op_with_lock_retry surface."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def op_capture(self, cmd: list[str], *, cwd: str, timeout: int = 30) -> GitResult:
        self.calls.append(list(cmd))
        return GitResult(returncode=0, stdout="", stderr="", timed_out=False)

    def op_with_lock_retry(self, cmd: list[str], *, cwd: str, timeout: int = 30):
        return (None, "ok")


# ── §1i singleton-reset fixture ───────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_factories():
    """Pre-stage: reset both write and read factories before AND after each test.

    Cited per workflows.md §1i (Singleton-resource tests pre-stage state,
    never race).  Ensures factory injection from one test never bleeds into
    the next.
    """
    reset_default_git_write_factory()
    reset_default_git_read_factory()
    yield
    reset_default_git_write_factory()
    reset_default_git_read_factory()


# ── AC1: _ship_to_pr routes push through write-spy ────────────────────────────


def test_ac1_ship_to_pr_routes_push_through_write_seam(
    tmp_path, monkeypatch
) -> None:
    """AC1: _ship_to_pr records ["git","push","-u","origin","feat-x"] in the write-spy.

    §1y Point L407 → Host _ship_to_pr → Test-path:
      enable ship (org_config["ship_pr"]=True);
      read-spy returns rev-parse→"feat-x" (not main/HEAD/""), rev-list→"1" (>0),
      status --porcelain→"" (clean); stub _gh→(1,"","") so post-push gh path
      short-circuits (push argv already recorded).
      For pre-GREEN hermeticity, also monkeypatch _git→noop (raising=False)
      so the push still dispatches but never touches real git.

    Pre-GREEN: push→_git(noop)→write-spy empty→FAIL.
    Post-GREEN: push→_git_write→factory spy→argv recorded→PASS.
    """
    from contracts import WorkflowContext  # noqa: PLC0415 — deferred per §1q

    working = tmp_path / "work"
    working.mkdir(parents=True, exist_ok=True)

    ctx = WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={
            "ship_pr": True,
            "main_branch": "main",
            "scratchpad_dir": str(tmp_path),
            "working_dir": str(working),
        },
        question="ship",
        session_id="test-session-C27F057B",
        persona="hal",
        framework=None,
        domain=None,
    )

    # Read-spy: dispatch on argv to make _ship_to_pr reach the push callsite.
    def _fake_read(args, cwd, *, timeout=30):
        if args[:2] == ["rev-parse", "--abbrev-ref"]:
            return 0, "feat-x\n", ""
        if args[:2] == ["rev-list", "--count"]:
            return 0, "1\n", ""
        if args[:1] == ["status"]:
            return 0, "", ""
        return 0, "", ""

    monkeypatch.setattr(phase_8_post_deploy, "_git_read", _fake_read, raising=True)

    # Stub _gh so the post-push gh path short-circuits (push argv already recorded).
    monkeypatch.setattr(
        phase_8_post_deploy, "_gh", lambda *a, **k: (1, "", ""), raising=True
    )

    # Pre-GREEN hermeticity: _git is a noop so push via old path doesn't shell out.
    # raising=False because _git will be DELETED post-GREEN — phantom stub.
    monkeypatch.setattr(
        phase_8_post_deploy, "_git", lambda *a, **k: (0, "", ""), raising=False
    )

    write_spy = _SpyGitWrite()
    set_default_git_write_factory(lambda: write_spy)

    phase_8_post_deploy._ship_to_pr(ctx, None)

    expected = ["git", "push", "-u", "origin", "feat-x"]
    assert expected in write_spy.calls, (
        f"Expected {expected!r} in write-spy.calls; got {write_spy.calls!r}. "
        f"Pre-GREEN: _ship_to_pr still calls _git(['push',...]) (raw subprocess via "
        f"bounded_run), bypassing the injectable write factory — spy never reached."
    )


# ── AC2: legacy _git helper is deleted ────────────────────────────────────────


def test_ac2_legacy_git_helper_is_deleted() -> None:
    """AC2: phase_8_post_deploy no longer exposes the _git helper.

    _git must be deleted from the module surface (push was its sole remaining
    consumer after slice 4a; once push migrates to _git_write, _git is dead).

    Pre-GREEN: _git exists → assert not hasattr FAILS.
    Post-GREEN: _git deleted → PASS.

    Note: _git is referenced ONLY as the string "\"_git\"" in hasattr() — never
    imported or attribute-accessed at module top level — so this file collects
    cleanly both pre- and post-GREEN.
    """
    assert not hasattr(phase_8_post_deploy, "_git"), (
        "_git must be deleted from phase_8_post_deploy (push migrated to _git_write; "
        "GREEN deletes the now-dead helper per spec §2.2)."
    )
