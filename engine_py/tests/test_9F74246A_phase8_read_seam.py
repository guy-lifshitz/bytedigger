"""9F74246A: RED tests — phase_8_post_deploy._git_read routes through git_port seam.

Spec AC summary:
  AC1 — _git_read routes calls through the git_read seam (spy records args)
  AC2 — FileNotFoundError from port -> (127, "", "git: command not found")
  AC3 — timed_out=True -> (124, "", "git <argv>: timeout after 30s")
  AC4 — normal GitResult passthrough -> (rc, stdout, stderr)
  AC5 — cwd is passed as str to the spy
  AC6 — _cleanup_worktrees reaches git_read seam (["worktree","list","--porcelain"])

Forcing function (§1l / §3):
  `_git_read` does NOT exist in phase_8_post_deploy yet.  Accessing it inside a
  test function body raises AttributeError — that is the RED forcing signal.
  For AC6 the spy is never invoked because _cleanup_worktrees still calls `_git`
  (subprocess path), so spy.calls remains [].

§1i (singleton-resource, workflows.md §1i):
  autouse fixture `_reset_git_read_factory` calls reset_default_git_read_factory()
  in both setup and teardown so injected factories never leak across tests.

§1q/D1CF5FDF (collectability):
  `_git_read` does NOT exist yet — it is the GREEN deliverable.  It is accessed
  ONLY inside test-function bodies via `getattr(phase_8_post_deploy, "_git_read")`
  so collection never trips an ImportError/AttributeError at module level.
  All symbols imported at module top level exist today:
    - phase_8_post_deploy (workflows/phase_8_post_deploy.py) ✓
    - GitResult, set_default_git_read_factory, reset_default_git_read_factory
      (lib/git_port.py) ✓
  The file COLLECTS cleanly; RED fires at assert / getattr time, never at
  collection time.

Pre-GREEN FAIL classification (expected: 6 FAIL / 0 PASS):
  AC1 → FAIL  — AttributeError: module has no attribute '_git_read' (not added yet)
  AC2 → FAIL  — AttributeError: module has no attribute '_git_read'
  AC3 → FAIL  — AttributeError: module has no attribute '_git_read'
  AC4 → FAIL  — AttributeError: module has no attribute '_git_read'
  AC5 → FAIL  — AttributeError: module has no attribute '_git_read'
  AC6 → FAIL  — spy.calls empty; _cleanup_worktrees still calls _git (not _git_read)
"""
from __future__ import annotations

from pathlib import Path

import pytest

import phase_8_post_deploy
from lib.git_port import (
    GitResult,
    reset_default_git_read_factory,
    set_default_git_read_factory,
)


# ── Spy ──────────────────────────────────────────────────────────────────────


class _SpyGitRead:
    """Recording spy matching GitReadPort.__call__ signature.

    Returns the supplied GitResult for every call, records (args, cwd, timeout)
    tuples in self.calls.  Optionally raises FileNotFoundError on first call if
    `raises_fnf` is True.
    """

    def __init__(
        self,
        result: GitResult | None = None,
        raises_fnf: bool = False,
    ) -> None:
        self.result = result or GitResult(returncode=0, stdout="", stderr="", timed_out=False)
        self.raises_fnf = raises_fnf
        self.calls: list[tuple] = []

    def __call__(
        self,
        args: list[str],
        *,
        cwd: str | None = None,
        timeout: float | None = None,
        dir_: str | None = None,
    ) -> GitResult:
        self.calls.append((args, cwd, timeout))
        if self.raises_fnf:
            raise FileNotFoundError("git: command not found")
        return self.result


# ── §1i singleton-reset fixture ───────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_git_read_factory():
    """Pre-stage: reset factory to default before AND after each test.

    Cited per workflows.md §1i (Singleton-resource tests pre-stage state,
    never race).  Ensures factory injection from one test never bleeds into
    the next.
    """
    reset_default_git_read_factory()
    yield
    reset_default_git_read_factory()


# ── Helper ────────────────────────────────────────────────────────────────────


def _make_ctx(tmp_path: Path):
    """Build a minimal WorkflowContext (mirrors test_phase_8_post_deploy_6CBC19FA.py)."""
    from contracts import WorkflowContext  # noqa: PLC0415 — deferred to body per §1q

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


# ── AC1 — routes through seam ─────────────────────────────────────────────────


def test_ac1_git_read_routes_through_seam(tmp_path: Path) -> None:
    """AC1: _git_read(["status","--porcelain"], path) routes through git_read seam.

    Pre-GREEN FAIL: AttributeError — phase_8_post_deploy._git_read does not
    exist yet (GREEN will add it).
    """
    _git_read = getattr(phase_8_post_deploy, "_git_read", None)
    assert _git_read is not None, (
        "phase_8_post_deploy._git_read does not exist yet — "
        "GREEN must add the module-local helper routing through git_port.git_read."
    )

    spy = _SpyGitRead(result=GitResult(returncode=0, stdout="", stderr="", timed_out=False))
    set_default_git_read_factory(lambda: spy)

    _git_read(["status", "--porcelain"], tmp_path)

    assert spy.calls, (
        "Spy recorded zero calls — _git_read did not route through git_read seam. "
        "Pre-GREEN: _git_read calls _git / bounded_run, bypassing the injectable factory."
    )
    recorded_args = [c[0] for c in spy.calls]
    assert ["status", "--porcelain"] in recorded_args, (
        f"Expected spy to receive args ['status','--porcelain']; "
        f"got {recorded_args!r}."
    )


# ── AC2 — FileNotFoundError maps to (127, "", "git: command not found") ────────


def test_ac2_filenotfound_maps_to_127(tmp_path: Path) -> None:
    """AC2: port raises FileNotFoundError -> _git_read returns (127, "", "git: command not found").

    Pre-GREEN FAIL: AttributeError — _git_read does not exist yet.
    """
    _git_read = getattr(phase_8_post_deploy, "_git_read", None)
    assert _git_read is not None, (
        "phase_8_post_deploy._git_read does not exist yet — "
        "GREEN must add the module-local helper."
    )

    spy = _SpyGitRead(raises_fnf=True)
    set_default_git_read_factory(lambda: spy)

    result = _git_read(["status"], tmp_path)

    assert result == (127, "", "git: command not found"), (
        f"Expected (127, '', 'git: command not found') on FileNotFoundError; "
        f"got {result!r}."
    )


# ── AC3 — timed_out maps to (124, "", "git <argv>: timeout after 30s") ────────


def test_ac3_timeout_maps_to_124_with_msg(tmp_path: Path) -> None:
    """AC3: GitResult.timed_out=True -> (124, "", "git log -1: timeout after 30s").

    Pre-GREEN FAIL: AttributeError — _git_read does not exist yet.
    """
    _git_read = getattr(phase_8_post_deploy, "_git_read", None)
    assert _git_read is not None, (
        "phase_8_post_deploy._git_read does not exist yet — "
        "GREEN must add the module-local helper."
    )

    spy = _SpyGitRead(result=GitResult(returncode=124, stdout="", stderr="", timed_out=True))
    set_default_git_read_factory(lambda: spy)

    result = _git_read(["log", "-1"], tmp_path)

    assert result == (124, "", "git log -1: timeout after 30s"), (
        f"Expected (124, '', 'git log -1: timeout after 30s') on timed_out; "
        f"got {result!r}."
    )


# ── AC4 — normal passthrough ───────────────────────────────────────────────────


def test_ac4_normal_passthrough(tmp_path: Path) -> None:
    """AC4: normal GitResult(0, "OUT", "ERR", False) -> (0, "OUT", "ERR").

    Pre-GREEN FAIL: AttributeError — _git_read does not exist yet.
    """
    _git_read = getattr(phase_8_post_deploy, "_git_read", None)
    assert _git_read is not None, (
        "phase_8_post_deploy._git_read does not exist yet — "
        "GREEN must add the module-local helper."
    )

    spy = _SpyGitRead(result=GitResult(returncode=0, stdout="OUT", stderr="ERR", timed_out=False))
    set_default_git_read_factory(lambda: spy)

    result = _git_read(["rev-parse"], tmp_path)

    assert result == (0, "OUT", "ERR"), (
        f"Expected (0, 'OUT', 'ERR') passthrough; got {result!r}."
    )


# ── AC5 — cwd is threaded as str to the spy ───────────────────────────────────


def test_ac5_cwd_threaded(tmp_path: Path) -> None:
    """AC5: _git_read(["status"], Path("/some/dir")) passes cwd="/some/dir" to spy.

    Pre-GREEN FAIL: AttributeError — _git_read does not exist yet.
    """
    _git_read = getattr(phase_8_post_deploy, "_git_read", None)
    assert _git_read is not None, (
        "phase_8_post_deploy._git_read does not exist yet — "
        "GREEN must add the module-local helper."
    )

    target = Path("/some/dir")
    spy = _SpyGitRead(result=GitResult(returncode=0, stdout="", stderr="", timed_out=False))
    set_default_git_read_factory(lambda: spy)

    _git_read(["status"], target)

    assert spy.calls, "Spy recorded zero calls — _git_read did not invoke the seam."
    recorded_cwd = spy.calls[0][1]
    assert recorded_cwd == str(target), (
        f"Expected cwd='{target!s}' threaded to spy; got {recorded_cwd!r}."
    )


# ── AC6 — _cleanup_worktrees reaches git_read seam ───────────────────────────


def test_ac6_cleanup_worktrees_reaches_git_read(tmp_path: Path) -> None:
    """AC6: _cleanup_worktrees issues ["worktree","list","--porcelain"] via git_read seam.

    Drive the production Host _cleanup_worktrees (mirrors invocation in
    test_phase_8_post_deploy_6CBC19FA.py).  The spy must record
    ["worktree","list","--porcelain"] among its calls.

    Pre-GREEN FAIL: spy.calls remains [] because _cleanup_worktrees still calls
    _git (bounded_run path), bypassing the injectable git_read factory.
    After GREEN: _cleanup_worktrees calls _git_read which routes through the
    factory, so the spy is invoked and records the argv.
    """
    ctx = _make_ctx(tmp_path)

    # Spy returns rc=0 with empty porcelain output (no worktrees to remove).
    # Also covers the "branch --merged" call that follows.
    spy = _SpyGitRead(result=GitResult(returncode=0, stdout="", stderr="", timed_out=False))
    set_default_git_read_factory(lambda: spy)

    result = phase_8_post_deploy._cleanup_worktrees(ctx, None)

    # Fail-open contract: status must be "ok" regardless.
    assert result.status == "ok", (
        f"_cleanup_worktrees fail-open invariant violated; got status={result.status!r}."
    )

    recorded_argvs = [c[0] for c in spy.calls]
    assert ["worktree", "list", "--porcelain"] in recorded_argvs, (
        f"Expected ['worktree','list','--porcelain'] among spy calls; "
        f"got {recorded_argvs!r}.  "
        f"Pre-GREEN: _cleanup_worktrees calls _git (bounded_run), "
        f"not the injectable git_read seam — spy never reached."
    )
