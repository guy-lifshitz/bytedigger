"""3B410663: RED tests — phase_8_post_deploy destructive WRITE calls route through git_write_port seam.

Spec AC summary:
  AC1 — _cleanup_worktrees routes worktree-remove via write-spy
  AC2 — _cleanup_gone_branches routes fetch --prune via write-spy
  AC3 — _cleanup_gone_branches routes branch -d via write-spy (gone branch)
  AC4 — _compute_full_suite_baseline routes worktree add via write-spy
  AC5 — _compute_full_suite_baseline (finally) routes worktree remove via write-spy
  AC6 — _git_write helper behavior-preservation: timeout→124, FileNotFoundError→127, rc passthrough
  AC7 — deferred-push boundary: _git still owns push; _git_write does not

Forcing function (§1l / §3):
  `_git_write` does NOT exist in phase_8_post_deploy yet.  The 5 write callsites
  (AC1–AC5) still call `_git` (bounded_run path), bypassing the injectable factory —
  the write-spy records zero calls, so the assertions FAIL.
  For AC6, accessing `phase_8_post_deploy._git_write` inside the test body raises
  AttributeError — that is the RED forcing signal.
  For AC7 the assertion holds today (push uses _git) and will hold post-GREEN too
  — this AC is a boundary-preservation guard.

§1i (singleton-resource, workflows.md §1i):
  autouse fixture `_reset_factories` calls reset_default_git_write_factory() AND
  reset_default_git_read_factory() in both setup and teardown so injected factories
  never leak across tests.

§1q/D1CF5FDF (collectability):
  `_git_write` does NOT exist yet — it is the GREEN deliverable.  It is accessed
  ONLY inside test-function bodies (via `getattr`) so collection never trips an
  AttributeError at module level.
  All symbols imported at module top level exist today:
    - phase_8_post_deploy (workflows/phase_8_post_deploy.py) ✓
    - GitResult, set_default_git_read_factory, reset_default_git_read_factory
      (lib/git_port.py) ✓
    - set_default_git_write_factory, reset_default_git_write_factory
      (lib/git_write_port.py) ✓
  The file COLLECTS cleanly; RED fires at assert / getattr time, never at
  collection time.

Pre-GREEN FAIL classification (expected: 7 FAIL / 0 PASS):
  AC1 → FAIL  — write-spy.calls is []; _cleanup_worktrees still calls _git
  AC2 → FAIL  — write-spy.calls is []; _cleanup_gone_branches still calls _git for fetch
  AC3 → FAIL  — write-spy.calls is []; _cleanup_gone_branches still calls _git for branch -d
  AC4 → FAIL  — write-spy.calls is []; _compute_full_suite_baseline still calls _git for worktree add
  AC5 → FAIL  — write-spy.calls is []; _compute_full_suite_baseline still calls _git for worktree remove
  AC6 → FAIL  — AttributeError: module 'phase_8_post_deploy' has no attribute '_git_write'
  AC7 → PASS  — boundary guard: _git still owns push (correctness guard for post-GREEN)
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from bytedigger_engine.workflows import phase_8_post_deploy
from bytedigger_engine.lib.git_port import (
    GitResult,
    reset_default_git_read_factory,
    set_default_git_read_factory,
)
from bytedigger_engine.lib.git_write_port import (
    reset_default_git_write_factory,
    set_default_git_write_factory,
)


# ── Recording write-spy ───────────────────────────────────────────────────────


class _SpyGitWrite:
    """Recording spy matching GitWritePort.op_capture / op_with_lock_retry surface.

    op_capture appends the full cmd to self.calls and returns a configurable
    GitResult (default rc=0, timed_out=False).  May be configured to raise
    FileNotFoundError on every call.

    op_with_lock_retry is kept to satisfy the Protocol surface but is not
    exercised on the reachability paths under test.
    """

    def __init__(
        self,
        result: GitResult | None = None,
        raises_fnf: bool = False,
    ) -> None:
        self.result = result or GitResult(returncode=0, stdout="", stderr="", timed_out=False)
        self.raises_fnf = raises_fnf
        self.calls: list[list[str]] = []

    def op_capture(self, cmd: list[str], *, cwd: str, timeout: int = 30) -> GitResult:
        self.calls.append(list(cmd))
        if self.raises_fnf:
            raise FileNotFoundError("git: command not found")
        return self.result

    def op_with_lock_retry(self, cmd: list[str], *, cwd: str, timeout: int = 30):
        # Not exercised on the reachability paths; return a benign value.
        return (None, "ok")


# ── Recording read-spy (feeds canned porcelain so write paths are reachable) ──


class _SpyGitRead:
    """Minimal read spy — returns a fixed GitResult per call; records (args, cwd)."""

    def __init__(self, result: GitResult | None = None) -> None:
        self.result = result or GitResult(returncode=0, stdout="", stderr="", timed_out=False)
        self.calls: list[tuple] = []

    def __call__(
        self,
        args: list[str],
        *,
        cwd: str | None = None,
        timeout: float | None = None,
        dir_: str | None = None,
    ) -> GitResult:
        self.calls.append((list(args), cwd))
        return self.result


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


# ── WorkflowContext builder ───────────────────────────────────────────────────


def _make_ctx(tmp_path: Path):
    """Build a minimal WorkflowContext (deferred import per §1q / D1CF5FDF)."""
    from bytedigger_engine.contracts import WorkflowContext  # noqa: PLC0415 — deferred to body per §1q

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


# ── AC1: _cleanup_worktrees routes worktree-remove through write-spy ──────────


def test_ac1_cleanup_worktrees_routes_remove_through_write_seam(
    tmp_path: Path, monkeypatch
) -> None:
    """AC1: _cleanup_worktrees records ["git","worktree","remove","--force",<path>] in write-spy.

    §1y Point L829 → Host _cleanup_worktrees → Test-path:
      read-spy returns porcelain with one worktree (path outside cwd anchor,
      branch≠main, branch in merged list) so the loop reaches L829.

    Pre-GREEN FAIL: write-spy.calls is [] because _cleanup_worktrees still calls
    _git (bounded_run), bypassing the injectable write factory.
    """
    ctx = _make_ctx(tmp_path)

    wt_path = str(tmp_path / "extra-wt")
    branch_name = "feature-x"

    # Porcelain output for "worktree list --porcelain": one extra worktree
    # at wt_path on branch feature-x; main worktree matches working_dir.
    working_dir = str(tmp_path / "work")
    porcelain_output = (
        f"worktree {working_dir}\n"
        f"HEAD 1111111111111111111111111111111111111111\n"
        f"branch refs/heads/main\n"
        f"\n"
        f"worktree {wt_path}\n"
        f"HEAD 2222222222222222222222222222222222222222\n"
        f"branch refs/heads/{branch_name}\n"
        f"\n"
    )
    # "branch --merged main" lists the feature branch (eligible for removal)
    merged_output = f"  main\n  {branch_name}\n"

    def fake_read_dispatch(args, cwd, *, timeout=30):
        if args[:2] == ["worktree", "list"]:
            return 0, porcelain_output, ""
        if args[:2] == ["branch", "--merged"]:
            return 0, merged_output, ""
        return 0, "", ""

    monkeypatch.setattr(phase_8_post_deploy, "_git_read", fake_read_dispatch, raising=True)

    write_spy = _SpyGitWrite()
    set_default_git_write_factory(lambda: write_spy)

    phase_8_post_deploy._cleanup_worktrees(ctx, None)

    expected = ["git", "worktree", "remove", "--force", wt_path]
    assert expected in write_spy.calls, (
        f"Expected {expected!r} in write-spy.calls; got {write_spy.calls!r}. "
        f"Pre-GREEN: _cleanup_worktrees still calls _git (raw subprocess), "
        f"bypassing the injectable write factory — spy never reached."
    )


# ── AC2: _cleanup_gone_branches routes fetch --prune through write-spy ─────────


def test_ac2_cleanup_gone_branches_routes_fetch_through_write_seam(
    tmp_path: Path, monkeypatch
) -> None:
    """AC2: _cleanup_gone_branches records ["git","fetch","--prune"] in write-spy.

    §1y Point L856 → Host _cleanup_gone_branches → Test-path:
      fetch is the first, unconditional call in the function.

    Pre-GREEN FAIL: write-spy.calls is [] because the fetch still calls _git.
    """
    ctx = _make_ctx(tmp_path)

    monkeypatch.setattr(phase_8_post_deploy, "_git_read", lambda *a, **k: (0, "", ""), raising=True)

    write_spy = _SpyGitWrite()
    set_default_git_write_factory(lambda: write_spy)

    phase_8_post_deploy._cleanup_gone_branches(ctx, None)

    expected = ["git", "fetch", "--prune"]
    assert expected in write_spy.calls, (
        f"Expected {expected!r} in write-spy.calls; got {write_spy.calls!r}. "
        f"Pre-GREEN: _cleanup_gone_branches calls _git for fetch (raw subprocess), "
        f"bypassing the injectable write factory."
    )


# ── AC3: _cleanup_gone_branches routes branch -d through write-spy ─────────────


def test_ac3_cleanup_gone_branches_routes_branch_delete_through_write_seam(
    tmp_path: Path, monkeypatch
) -> None:
    """AC3: _cleanup_gone_branches records ["git","branch","-d",<name>] in write-spy.

    §1y Point L892 → Host _cleanup_gone_branches → Test-path:
      read-spy `branch -vv` returns a line with `: gone]` + branch name
      so the loop reaches L892.

    Pre-GREEN FAIL: write-spy.calls is [] because branch -d still calls _git.
    """
    ctx = _make_ctx(tmp_path)
    gone_branch = "stale-feature"

    branch_vv_output = (
        "* main                       abcdef [origin/main] init\n"
        f"  {gone_branch}              123456 [origin/{gone_branch}: gone] wip\n"
    )

    def fake_read_dispatch(args, cwd, *, timeout=30):
        if args[:2] == ["branch", "-vv"]:
            return 0, branch_vv_output, ""
        return 0, "", ""

    monkeypatch.setattr(phase_8_post_deploy, "_git_read", fake_read_dispatch, raising=True)

    write_spy = _SpyGitWrite()
    set_default_git_write_factory(lambda: write_spy)

    phase_8_post_deploy._cleanup_gone_branches(ctx, None)

    expected = ["git", "branch", "-d", gone_branch]
    assert expected in write_spy.calls, (
        f"Expected {expected!r} in write-spy.calls; got {write_spy.calls!r}. "
        f"Pre-GREEN: _cleanup_gone_branches calls _git for branch -d (raw subprocess), "
        f"bypassing the injectable write factory."
    )


# ── AC4: _compute_full_suite_baseline routes worktree add through write-spy ────


def test_ac4_compute_full_suite_baseline_routes_worktree_add_through_write_seam(
    tmp_path: Path, monkeypatch
) -> None:
    """AC4: _compute_full_suite_baseline records ["git","worktree","add","--detach",<wt>,<ref>] in write-spy.

    §1y Point L1210 → Host _compute_full_suite_baseline → Test-path:
      call with non-empty cmds + git_cwd; worktree add is first.

    Pre-GREEN FAIL: write-spy.calls is [] because worktree add still calls _git.
    """
    git_cwd = str(tmp_path)
    main_ref = "main"
    cmds = [["pytest", "--tb=no", "-q"]]

    # Stub _run_full_suite so we don't need a real suite; return (0,) to avoid None result.
    monkeypatch.setattr(
        phase_8_post_deploy,
        "_run_full_suite",
        lambda *a, **k: (0,),
        raising=True,
    )

    write_spy = _SpyGitWrite()
    set_default_git_write_factory(lambda: write_spy)

    phase_8_post_deploy._compute_full_suite_baseline(cmds, main_ref, git_cwd, 30)

    # Post-GREEN: write-spy.calls contains the add argv (full argv incl. "git").
    add_calls = [c for c in write_spy.calls if len(c) >= 4 and c[1:4] == ["worktree", "add", "--detach"]]
    assert add_calls, (
        f"Expected a call with argv [...,'worktree','add','--detach',<wt>,'{main_ref}'] "
        f"in write-spy.calls; got {write_spy.calls!r}. "
        f"Pre-GREEN: _compute_full_suite_baseline calls _git for worktree add (raw subprocess), "
        f"bypassing the injectable write factory."
    )
    # Verify main_ref appears in the add call
    assert any(main_ref in c for c in add_calls), (
        f"Expected main_ref={main_ref!r} in add call argv; got {add_calls!r}."
    )


# ── AC5: _compute_full_suite_baseline (finally) routes worktree remove through write-spy ──


def test_ac5_compute_full_suite_baseline_routes_worktree_remove_through_write_seam(
    tmp_path: Path, monkeypatch
) -> None:
    """AC5: _compute_full_suite_baseline finally-block records ["git","worktree","remove","--force",<wt>] in write-spy.

    §1y Point L1219 (finally) → Host _compute_full_suite_baseline → Test-path:
      write-spy add rc=0 (worktree_added=True); _run_full_suite patched to dummy
      return so finally fires the remove.

    Pre-GREEN FAIL: write-spy.calls is [] because worktree remove still calls _git.
    """
    git_cwd = str(tmp_path)
    main_ref = "main"
    cmds = [["pytest", "--tb=no", "-q"]]

    monkeypatch.setattr(
        phase_8_post_deploy,
        "_run_full_suite",
        lambda *a, **k: (0,),
        raising=True,
    )

    write_spy = _SpyGitWrite(result=GitResult(returncode=0, stdout="", stderr="", timed_out=False))
    set_default_git_write_factory(lambda: write_spy)

    phase_8_post_deploy._compute_full_suite_baseline(cmds, main_ref, git_cwd, 30)

    remove_calls = [
        c for c in write_spy.calls
        if len(c) >= 4 and c[1:4] == ["worktree", "remove", "--force"]
    ]
    assert remove_calls, (
        f"Expected a call with argv [...,'worktree','remove','--force',<wt>] "
        f"in write-spy.calls; got {write_spy.calls!r}. "
        f"Pre-GREEN: _compute_full_suite_baseline calls _git for worktree remove "
        f"(raw subprocess), bypassing the injectable write factory."
    )


# ── AC6: _git_write behavior-preservation ─────────────────────────────────────


def test_ac6a_git_write_timed_out_returns_124(tmp_path: Path) -> None:
    """AC6a: _git_write with timed_out GitResult returns (124, "", msg containing 'timeout').

    Pre-GREEN FAIL: AttributeError — phase_8_post_deploy._git_write does not exist yet.
    """
    _git_write = getattr(phase_8_post_deploy, "_git_write", None)
    assert _git_write is not None, (
        "phase_8_post_deploy._git_write does not exist yet — "
        "GREEN must add the module-local helper routing through git_write_port.git_op_capture."
    )

    timed_out_spy = _SpyGitWrite(
        result=GitResult(returncode=124, stdout="", stderr="", timed_out=True)
    )
    set_default_git_write_factory(lambda: timed_out_spy)

    rc, out, err = _git_write(["status"], tmp_path)

    assert rc == 124, f"Expected rc=124 on timed_out; got rc={rc!r}."
    assert out == "", f"Expected stdout='' on timed_out; got {out!r}."
    assert "timeout" in err.lower(), (
        f"Expected 'timeout' in stderr on timed_out; got {err!r}."
    )


def test_ac6b_git_write_filenotfound_returns_127(tmp_path: Path) -> None:
    """AC6b: _git_write with FileNotFoundError from port returns (127, "", "git: command not found").

    Pre-GREEN FAIL: AttributeError — phase_8_post_deploy._git_write does not exist yet.
    """
    _git_write = getattr(phase_8_post_deploy, "_git_write", None)
    assert _git_write is not None, (
        "phase_8_post_deploy._git_write does not exist yet — "
        "GREEN must add the module-local helper."
    )

    fnf_spy = _SpyGitWrite(raises_fnf=True)
    set_default_git_write_factory(lambda: fnf_spy)

    rc, out, err = _git_write(["status"], tmp_path)

    assert (rc, out, err) == (127, "", "git: command not found"), (
        f"Expected (127, '', 'git: command not found') on FileNotFoundError; got {(rc, out, err)!r}."
    )


def test_ac6c_git_write_normal_passthrough(tmp_path: Path) -> None:
    """AC6c: _git_write with normal GitResult(rc=2, stdout='o', stderr='e') returns (2, 'o', 'e').

    Pre-GREEN FAIL: AttributeError — phase_8_post_deploy._git_write does not exist yet.
    """
    _git_write = getattr(phase_8_post_deploy, "_git_write", None)
    assert _git_write is not None, (
        "phase_8_post_deploy._git_write does not exist yet — "
        "GREEN must add the module-local helper."
    )

    passthrough_spy = _SpyGitWrite(
        result=GitResult(returncode=2, stdout="o", stderr="e", timed_out=False)
    )
    set_default_git_write_factory(lambda: passthrough_spy)

    rc, out, err = _git_write(["diff"], tmp_path)

    assert (rc, out, err) == (2, "o", "e"), (
        f"Expected (2, 'o', 'e') passthrough; got {(rc, out, err)!r}."
    )


# ── AC7: deferred-push boundary — push stays on _git, not _git_write ──────────


def test_ac7_push_callsite_migrated_to_git_write() -> None:
    """AC7: inspect.getsource shows _git_write(["push" present AND _git(["push" absent.

    Migration guard: the push -u origin callsite (C27F057B slice 4b) must have
    migrated to _git_write post-GREEN. The legacy _git call for push must be gone.

    This AC was inverted by the §1a sweep for C27F057B — it now asserts the
    completed migration rather than the deferred boundary.
    """
    src = inspect.getsource(phase_8_post_deploy)

    assert '_git_write(["push"' in src, (
        "Expected '_git_write([\"push\"' in phase_8_post_deploy source — "
        "the push callsite must have migrated to _git_write (C27F057B slice 4b)."
    )
    assert '_git(["push"' not in src, (
        "Found '_git([\"push\"' in phase_8_post_deploy source — "
        "push must have migrated away from legacy _git (C27F057B slice 4b)."
    )
