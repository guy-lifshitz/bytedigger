"""RED tests for 7FE193B7 — route 3 rev-parse READ callsites through git_port.git_read.

Spec: SHARED/memory/Decisions/2026-06-21_7FE193B7_git_read_routing_spec.md

Forcing function (§1l / §3):
  Each test installs a recording spy as the git_read implementation via
  set_default_git_read_factory, calls the Host fn, and asserts the spy was
  invoked with the correct args.

  Pre-GREEN: the Host fns still call bounded_run directly — the spy is NEVER
  reached.  All 8 tests FAIL at ASSERT time (spy.calls == [] at assertion).
  This is the forcing function — a stub that left bounded_run in place cannot
  make the spy non-empty.  NOT vacuous (§1l): we patch the collaborator
  (git_port.git_read) via its public factory, not the UUTs themselves.

  Post-GREEN: Host fns call git_port.git_read(…); the spy is invoked;
  assertions pass.

§1i (singleton-resource): factory swap is pre-staged in the fixture teardown
(finally: git_port.reset_default_git_read_factory()) — never raced.

§1q: no sys.path manipulation here; conftest handles it at import time.
§1q/D1CF5FDF: all symbols imported at module level already exist today
(git_port, GitResult, set_default_git_read_factory, reset_default_git_read_factory,
_resolve_fix_commit_sha, _resolve_pre_fix_sha, _is_synthetic_test_env) — file
COLLECTS cleanly; RED fires at assert time, not at collection.

Pre-GREEN PASS/FAIL classification (§3 + §4):
  AC1  → FAIL  (spy never called; calls == [] ; bounded_run runs instead)
  AC2  → FAIL  (spy never called; actual path calls bounded_run in non-git dir)
  AC3  → FAIL  (spy never called; bounded_run path returns real rc, not timed_out)
  AC4  → FAIL  (spy never called; bounded_run runs instead)
  AC5  → FAIL  (spy never called; bounded_run path in non-git dir)
  AC6  → FAIL  (spy never called; bounded_run runs instead)
  AC7  → FAIL  (spy never called; bounded_run in non-git tmp → rc != 0 → True,
                but spy.calls assert fails first)
  AC8  → FAIL  (spy never called; bounded_run in non-git tmp → rc != 0 → True,
                but spy.calls assert fails first)

Expected pre-GREEN: 8 FAIL / 0 PASS.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest

# conftest-import-time singleton handles sys.path (§1q / 81F97F3D gate).
# Do NOT add sys.path.insert here.

import lib.git_port as git_port
from lib.git_port import GitResult

import phase_6_fix_integrity as _p6fi
import phase_6_review as _p6r


# ─────────────────────────────────────────────────────────────────────────────
# Recording spy factory
# ─────────────────────────────────────────────────────────────────────────────


class _SpyGitRead:
    """Recording spy that matches GitReadPort.__call__ signature.

    Returns the configured GitResult for every call and records
    (args, cwd, timeout) tuples in self.calls.
    """

    def __init__(self, result: GitResult) -> None:
        self.result = result
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
        return self.result


# ─────────────────────────────────────────────────────────────────────────────
# Fixture — installs a spy factory, always resets in finally (§1i)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture()
def tmp_git_cwd(tmp_path: Path) -> Path:
    """macOS-safe tmp dir (§1j realpathSync equivalent)."""
    real = Path(os.path.realpath(tempfile.mkdtemp(dir=tmp_path)))
    return real


# ─────────────────────────────────────────────────────────────────────────────
# AC1 — _resolve_fix_commit_sha: spy returns rc=0 → called once, args, value
# ─────────────────────────────────────────────────────────────────────────────


def test_ac1_resolve_fix_commit_sha_routes_through_git_read(tmp_git_cwd: Path) -> None:
    """AC1: spy returns GitResult(0,'abc123\\n','',False) → spy called once,
    recorded args == ['rev-parse','HEAD'] (no leading 'git'), fn returns 'abc123'.

    Fails pre-GREEN because bounded_run is called instead of git_port.git_read,
    so spy.calls == [] and the first assert fails at assert time.
    """
    spy = _SpyGitRead(GitResult(returncode=0, stdout="abc123\n", stderr="", timed_out=False))
    try:
        git_port.set_default_git_read_factory(lambda: spy)
        result = _p6fi._resolve_fix_commit_sha(
            cfg={},          # no "fix_commit_sha" key → reaches git path
            scratchpad=None,  # no scratchpad → reaches git path
            git_cwd=tmp_git_cwd,
        )
        assert len(spy.calls) == 1, (
            f"Expected spy called once; got {len(spy.calls)} calls "
            f"(pre-GREEN: bounded_run used instead of git_port.git_read)"
        )
        recorded_args, recorded_cwd, _ = spy.calls[0]
        assert recorded_args == ["rev-parse", "HEAD"], (
            f"Expected args ['rev-parse','HEAD'], got {recorded_args!r} "
            f"(leading 'git' must be stripped — git_port adds it internally)"
        )
        assert recorded_cwd == str(tmp_git_cwd), (
            f"cwd not forwarded: expected {str(tmp_git_cwd)!r}, got {recorded_cwd!r}"
        )
        assert result == "abc123", (
            f"Expected return value 'abc123' (stripped), got {result!r}"
        )
    finally:
        git_port.reset_default_git_read_factory()


# ─────────────────────────────────────────────────────────────────────────────
# AC2 — _resolve_fix_commit_sha: spy returns rc=128 → fn returns None
# ─────────────────────────────────────────────────────────────────────────────


def test_ac2_resolve_fix_commit_sha_rc128_returns_none(tmp_git_cwd: Path) -> None:
    """AC2: spy returns GitResult(128,'','fatal',False) → fn returns None
    (rc != 0 raises CalledProcessError which is caught by the outer try/except).

    Fails pre-GREEN: spy not called (bounded_run used); assert len(spy.calls)==1 fails.
    """
    spy = _SpyGitRead(GitResult(returncode=128, stdout="", stderr="fatal", timed_out=False))
    try:
        git_port.set_default_git_read_factory(lambda: spy)
        result = _p6fi._resolve_fix_commit_sha(
            cfg={},
            scratchpad=None,
            git_cwd=tmp_git_cwd,
        )
        assert len(spy.calls) == 1, (
            f"Expected spy called once; got {len(spy.calls)} calls "
            f"(pre-GREEN: bounded_run used instead of git_port.git_read)"
        )
        assert result is None, (
            f"Expected None when git returns rc=128 (error caught), got {result!r}"
        )
    finally:
        git_port.reset_default_git_read_factory()


# ─────────────────────────────────────────────────────────────────────────────
# AC3 — _resolve_fix_commit_sha: spy returns timed_out=True → fn returns None
# ─────────────────────────────────────────────────────────────────────────────


def test_ac3_resolve_fix_commit_sha_timed_out_returns_none(tmp_git_cwd: Path) -> None:
    """AC3: spy returns GitResult(124,'','',True) → fn returns None (timeout path).

    Fails pre-GREEN: spy not called; assert len(spy.calls)==1 fails.
    """
    spy = _SpyGitRead(GitResult(returncode=124, stdout="", stderr="", timed_out=True))
    try:
        git_port.set_default_git_read_factory(lambda: spy)
        result = _p6fi._resolve_fix_commit_sha(
            cfg={},
            scratchpad=None,
            git_cwd=tmp_git_cwd,
        )
        assert len(spy.calls) == 1, (
            f"Expected spy called once; got {len(spy.calls)} calls "
            f"(pre-GREEN: bounded_run used instead of git_port.git_read)"
        )
        assert result is None, (
            f"Expected None when git times out (timed_out=True), got {result!r}"
        )
    finally:
        git_port.reset_default_git_read_factory()


# ─────────────────────────────────────────────────────────────────────────────
# AC4 — _resolve_pre_fix_sha: spy returns rc=0 → called with DEAD~1, value BEEF
# ─────────────────────────────────────────────────────────────────────────────


def test_ac4_resolve_pre_fix_sha_routes_through_git_read(tmp_git_cwd: Path) -> None:
    """AC4: fix_commit_sha='DEAD'; spy returns GitResult(0,'BEEF\\n','',False).
    Assert spy args == ['rev-parse','DEAD~1'], fn returns 'BEEF'.

    Fails pre-GREEN: spy not called; assert len(spy.calls)==1 fails.
    """
    spy = _SpyGitRead(GitResult(returncode=0, stdout="BEEF\n", stderr="", timed_out=False))
    try:
        git_port.set_default_git_read_factory(lambda: spy)
        result = _p6fi._resolve_pre_fix_sha(
            cfg={},             # no "pre_fix_sha" → reaches git path
            scratchpad=None,     # no scratchpad → reaches git path
            fix_commit_sha="DEAD",  # truthy → reaches bounded_run/git_read
            git_cwd=tmp_git_cwd,
        )
        assert len(spy.calls) == 1, (
            f"Expected spy called once; got {len(spy.calls)} calls "
            f"(pre-GREEN: bounded_run used instead of git_port.git_read)"
        )
        recorded_args, recorded_cwd, _ = spy.calls[0]
        assert recorded_args == ["rev-parse", "DEAD~1"], (
            f"Expected args ['rev-parse','DEAD~1'], got {recorded_args!r}"
        )
        assert recorded_cwd == str(tmp_git_cwd), (
            f"cwd not forwarded: expected {str(tmp_git_cwd)!r}, got {recorded_cwd!r}"
        )
        assert result == "BEEF", (
            f"Expected 'BEEF' (stripped stdout), got {result!r}"
        )
    finally:
        git_port.reset_default_git_read_factory()


# ─────────────────────────────────────────────────────────────────────────────
# AC5 — _resolve_pre_fix_sha: spy returns timed_out=True → fn returns None
# ─────────────────────────────────────────────────────────────────────────────


def test_ac5_resolve_pre_fix_sha_timed_out_returns_none(tmp_git_cwd: Path) -> None:
    """AC5: fix_commit_sha='DEAD'; spy returns GitResult(124,'','',True) → fn returns None.

    Fails pre-GREEN: spy not called; assert len(spy.calls)==1 fails.
    """
    spy = _SpyGitRead(GitResult(returncode=124, stdout="", stderr="", timed_out=True))
    try:
        git_port.set_default_git_read_factory(lambda: spy)
        result = _p6fi._resolve_pre_fix_sha(
            cfg={},
            scratchpad=None,
            fix_commit_sha="DEAD",
            git_cwd=tmp_git_cwd,
        )
        assert len(spy.calls) == 1, (
            f"Expected spy called once; got {len(spy.calls)} calls "
            f"(pre-GREEN: bounded_run used instead of git_port.git_read)"
        )
        assert result is None, (
            f"Expected None on timeout, got {result!r}"
        )
    finally:
        git_port.reset_default_git_read_factory()


# ─────────────────────────────────────────────────────────────────────────────
# AC6 — _is_synthetic_test_env: spy returns rc=0 → called, args, returns False
# ─────────────────────────────────────────────────────────────────────────────


def test_ac6_is_synthetic_test_env_routes_through_git_read(tmp_git_cwd: Path) -> None:
    """AC6: cfg without 'git_cwd' key; spy returns GitResult(0,'/x/.git','',False).
    Assert spy called, args == ['rev-parse','--git-dir'], fn returns False
    (git found → not synthetic → False).

    cfg must NOT have 'git_cwd' key — else function early-returns False without
    calling git at all (§1y precondition).

    Fails pre-GREEN: spy not called; assert len(spy.calls)==1 fails.
    """
    spy = _SpyGitRead(GitResult(returncode=0, stdout="/x/.git", stderr="", timed_out=False))
    try:
        git_port.set_default_git_read_factory(lambda: spy)
        result = _p6r._is_synthetic_test_env(
            cfg={},              # NO 'git_cwd' key → does not short-circuit to False
            git_cwd=str(tmp_git_cwd),
        )
        assert len(spy.calls) == 1, (
            f"Expected spy called once; got {len(spy.calls)} calls "
            f"(pre-GREEN: bounded_run used instead of git_port.git_read)"
        )
        recorded_args, recorded_cwd, _ = spy.calls[0]
        assert recorded_args == ["rev-parse", "--git-dir"], (
            f"Expected args ['rev-parse','--git-dir'], got {recorded_args!r}"
        )
        assert result is False, (
            f"Expected False (rc=0 means real git repo → not synthetic), got {result!r}"
        )
    finally:
        git_port.reset_default_git_read_factory()


# ─────────────────────────────────────────────────────────────────────────────
# AC7 — _is_synthetic_test_env: spy returns rc=128 → fn returns True
# ─────────────────────────────────────────────────────────────────────────────


def test_ac7_is_synthetic_test_env_rc128_returns_true(tmp_git_cwd: Path) -> None:
    """AC7: cfg without 'git_cwd'; spy returns GitResult(128,'','',False) → fn returns True
    (rc != 0 means no git repo → synthetic).

    Fails pre-GREEN: spy not called; assert len(spy.calls)==1 fails.
    """
    spy = _SpyGitRead(GitResult(returncode=128, stdout="", stderr="", timed_out=False))
    try:
        git_port.set_default_git_read_factory(lambda: spy)
        result = _p6r._is_synthetic_test_env(
            cfg={},
            git_cwd=str(tmp_git_cwd),
        )
        assert len(spy.calls) == 1, (
            f"Expected spy called once; got {len(spy.calls)} calls "
            f"(pre-GREEN: bounded_run used instead of git_port.git_read)"
        )
        assert result is True, (
            f"Expected True (rc=128 → no git repo → synthetic), got {result!r}"
        )
    finally:
        git_port.reset_default_git_read_factory()


# ─────────────────────────────────────────────────────────────────────────────
# AC8 — _is_synthetic_test_env: spy returns timed_out=True → fn returns True
# ─────────────────────────────────────────────────────────────────────────────


def test_ac8_is_synthetic_test_env_timed_out_returns_true(tmp_git_cwd: Path) -> None:
    """AC8: cfg without 'git_cwd'; spy returns GitResult(124,'','',True) → fn returns True
    (timeout → treat as synthetic).

    Fails pre-GREEN: spy not called; assert len(spy.calls)==1 fails.
    """
    spy = _SpyGitRead(GitResult(returncode=124, stdout="", stderr="", timed_out=True))
    try:
        git_port.set_default_git_read_factory(lambda: spy)
        result = _p6r._is_synthetic_test_env(
            cfg={},
            git_cwd=str(tmp_git_cwd),
        )
        assert len(spy.calls) == 1, (
            f"Expected spy called once; got {len(spy.calls)} calls "
            f"(pre-GREEN: bounded_run used instead of git_port.git_read)"
        )
        assert result is True, (
            f"Expected True (timeout → synthetic), got {result!r}"
        )
    finally:
        git_port.reset_default_git_read_factory()
