"""9ECBF1F5 RED tests — git_write_port capture-primitive + phase_5 stash seam.

Agreement 9ECBF1F5 (OSS-seam #304 slice 3a): `phase_5_implement.py`'s two
baseline helpers call raw `bounded_run(["git","stash",…])` — bypassing the
`git_write_port` seam.  GREEN will:
  1. Add `git_op_capture` + `op_capture` to `lib/git_write_port.py`.
  2. Route the 4 stash callsites through `git_write_port.git_op_capture`.

Acceptance Criteria tested (§3):
  AC1 — git_op_capture on clean repo → GitResult(rc=0, stdout="", timed_out=False)
  AC2 — git_op_capture(stash pop) on empty-stash repo → rc!=0, does NOT raise
  AC3 — default_git_write() is GitWritePort instance AND has op_capture method
  AC4 — spy records ["git","stash","push","-u","-m","p2-baseline-585e30e3"] via _compute_baseline_failed
  AC5 — spy records ["git","stash","pop"] via _compute_baseline_failed finally block
  AC6 — spy records ["git","stash","push","-u","-m","p2-typecheck-baseline-5c14ef32"] via _compute_baseline_typecheck_count
  AC7 — spy records ["git","stash","pop"] via _compute_baseline_typecheck_count finally block
  AC8 — clean-tree sentinel: spy records push but NOT pop; helper returns None
  AC9 — phase_5_implement.py source has 0 occurrences of bounded_run wrapping ["git","stash"

All AC1–AC9 FAIL today:
  AC1/AC2 — git_op_capture does not exist on the module → AttributeError at call time
  AC3 — op_capture method not present on default_git_write() → hasattr returns False
  AC4–AC8 — git_op_capture absent; set_default_git_write_factory wiring not hooked;
             _compute_baseline_failed/typecheck still call bounded_run directly →
             recorded cmd-vectors don't match expected seam calls
  AC9 — phase_5_implement.py still contains the raw bounded_run(["git","stash"…) calls

§1q / D1CF5FDF anti-hang rule: `from bytedigger_engine.lib import git_write_port` at module top is safe
(module exists).  `git_write_port.git_op_capture` is referenced INSIDE test bodies only
so the file COLLECTS even though git_op_capture is absent; tests FAIL at assert/call time.

§1i cite (singleton-resource): spy factory is reset via `reset_default_git_write_factory()`
in a try/finally teardown for every AC that injects via `set_default_git_write_factory`.
See workflows.md §1i (Singleton-resource tests pre-stage state, never race).
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

# Module-level imports of EXISTING modules — safe at collect time (§1q / D1CF5FDF).
# conftest.py already wired engine_py root + workflows/ into sys.path at import time.
from bytedigger_engine.lib import git_write_port
from bytedigger_engine.lib.git_port import GitResult
from bytedigger_engine.workflows import phase_5_implement as p5


# ─── Shared real-git-repo fixture (AC1, AC2) ─────────────────────────────────


def _make_real_git_repo(base: Path) -> Path:
    """Init a bare-minimum git repo with one commit so stash/pop work.

    Mirrors the fixture convention in the sibling test 5F06E98D.
    """
    repo = base / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@hal.local"],
        cwd=str(repo), check=True, capture_output=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "HAL Test"],
        cwd=str(repo), check=True, capture_output=True,
    )
    # Need at least one commit for stash to work
    (repo / "init.txt").write_text("initial\n")
    subprocess.run(["git", "add", "."], cwd=str(repo), check=True, capture_output=True)
    subprocess.run(
        ["git", "commit", "-m", "init"],
        cwd=str(repo), check=True, capture_output=True,
    )
    return repo


# ─── AC1 — git_op_capture: clean repo → rc=0, stdout="", timed_out=False ────


def test_9ecbf1f5_ac1_git_op_capture_stash_list_clean_repo(tmp_path):
    """git_op_capture(["git","stash","list"], cwd=<clean repo>) → GitResult(rc=0,
    stdout=="", timed_out is False).

    FAILS today: git_write_port.git_op_capture does not exist → AttributeError.
    """
    repo = _make_real_git_repo(tmp_path)

    # git_op_capture not yet on module — AttributeError proves RED before GREEN
    result = git_write_port.git_op_capture(
        ["git", "stash", "list"], cwd=str(repo)
    )

    assert isinstance(result, GitResult), (
        f"git_op_capture must return a GitResult; got {type(result)!r}"
    )
    assert result.returncode == 0, (
        f"Expected rc=0 from 'git stash list' on clean repo; got {result.returncode}"
    )
    assert result.stdout == "", (
        f"Expected empty stdout from 'git stash list' on clean repo; got {result.stdout!r}"
    )
    assert result.timed_out is False, (
        f"Expected timed_out=False; got {result.timed_out!r}"
    )


# ─── AC2 — git_op_capture: stash pop on empty stash → rc!=0, no raise ────────


def test_9ecbf1f5_ac2_git_op_capture_stash_pop_empty_no_raise(tmp_path):
    """git_op_capture(["git","stash","pop"], cwd=<repo with empty stash>)
    → returns GitResult with rc != 0 and does NOT raise.

    FAILS today: git_write_port.git_op_capture does not exist → AttributeError.
    """
    repo = _make_real_git_repo(tmp_path)

    # Must not raise — non-zero rc is returned, not propagated
    result = git_write_port.git_op_capture(
        ["git", "stash", "pop"], cwd=str(repo)
    )

    assert isinstance(result, GitResult), (
        f"git_op_capture must return a GitResult; got {type(result)!r}"
    )
    assert result.returncode != 0, (
        f"Expected rc != 0 from 'git stash pop' on empty stash; got {result.returncode}"
    )


# ─── AC3 — Protocol conformance: op_capture method on default_git_write() ────


def test_9ecbf1f5_ac3_default_git_write_is_protocol_and_has_op_capture():
    """isinstance(default_git_write(), GitWritePort) is True AND
    hasattr(default_git_write(), "op_capture") is True.

    FAILS today: GitWritePort does not declare op_capture → hasattr returns False.
    """
    from bytedigger_engine.lib.git_write_port import GitWritePort, default_git_write

    instance = default_git_write()

    assert isinstance(instance, GitWritePort), (
        f"default_git_write() must satisfy GitWritePort Protocol; "
        f"isinstance returned False.  Is GitWritePort @runtime_checkable?"
    )
    assert hasattr(instance, "op_capture"), (
        f"default_git_write() instance must have an 'op_capture' method after GREEN; "
        f"got: {[a for a in dir(instance) if not a.startswith('__')]!r}"
    )


# ─── Spy fixture (AC4–AC8) ───────────────────────────────────────────────────


class _CaptureSpy:
    """Records cmd-vectors passed to op_capture; returns a caller-configured GitResult."""

    def __init__(self, push_result: GitResult):
        self.calls: list[list] = []
        self._push_result = push_result

    def op_capture(self, cmd: list, *, cwd: str, timeout: int = 30) -> GitResult:
        self.calls.append(list(cmd))
        # Return push_result for push; rc=0 GitResult for pop (unused by helpers)
        if "pop" in cmd:
            return GitResult(returncode=0, stdout="", stderr="", timed_out=False)
        return self._push_result


# ─── AC4 — _compute_baseline_failed records stash push via seam ──────────────


def test_9ecbf1f5_ac4_baseline_failed_records_stash_push(tmp_path):
    """Inject spy; _compute_baseline_failed({"groups":[]}, "/tmp") → spy recorded
    cmd ["git","stash","push","-u","-m","p2-baseline-585e30e3"].

    FAILS today: _compute_baseline_failed calls bounded_run directly, not
    git_write_port.git_op_capture → spy is never called.

    §1i: spy factory reset in finally (singleton-resource teardown; workflows.md §1i).
    """
    push_result = GitResult(returncode=0, stdout="", stderr="", timed_out=False)
    spy = _CaptureSpy(push_result=push_result)
    git_write_port.set_default_git_write_factory(lambda: spy)
    try:
        p5._compute_baseline_failed({"groups": []}, str(tmp_path), "cfg_git_cwd")
    finally:
        git_write_port.reset_default_git_write_factory()

    push_cmd = ["git", "stash", "push", "-u", "-m", "p2-baseline-585e30e3"]
    assert push_cmd in spy.calls, (
        f"Expected spy to record {push_cmd!r}; "
        f"actual recorded calls: {spy.calls!r}.\n"
        f"GREEN must route the push through git_write_port.git_op_capture."
    )


# ─── AC5 — _compute_baseline_failed records stash pop (finally block) ─────────


def test_9ecbf1f5_ac5_baseline_failed_records_stash_pop(tmp_path):
    """Inject spy; _compute_baseline_failed({"groups":[]}, "/tmp") → spy recorded
    cmd ["git","stash","pop"] (finally-block pop reached because push succeeded).

    FAILS today: _compute_baseline_failed calls bounded_run directly →
    spy never called for pop.

    §1i: spy factory reset in finally (workflows.md §1i).
    """
    push_result = GitResult(returncode=0, stdout="", stderr="", timed_out=False)
    spy = _CaptureSpy(push_result=push_result)
    git_write_port.set_default_git_write_factory(lambda: spy)
    try:
        p5._compute_baseline_failed({"groups": []}, str(tmp_path), "cfg_git_cwd")
    finally:
        git_write_port.reset_default_git_write_factory()

    pop_cmd = ["git", "stash", "pop"]
    assert pop_cmd in spy.calls, (
        f"Expected spy to record {pop_cmd!r}; "
        f"actual recorded calls: {spy.calls!r}.\n"
        f"GREEN must route the finally-block pop through git_write_port.git_op_capture."
    )


# ─── AC6 — _compute_baseline_typecheck_count records typecheck push ───────────


def test_9ecbf1f5_ac6_baseline_typecheck_records_stash_push(tmp_path):
    """Inject spy; _compute_baseline_typecheck_count([], "/tmp") → spy recorded
    cmd ["git","stash","push","-u","-m","p2-typecheck-baseline-5c14ef32"].

    Reachability: resolved_paths=[] → existing_paths=[] → returns 0 in try block
    before mypy is called → finally-block pop fires.

    FAILS today: _compute_baseline_typecheck_count calls bounded_run directly →
    spy never called.

    §1i: spy factory reset in finally (workflows.md §1i).
    """
    push_result = GitResult(returncode=0, stdout="", stderr="", timed_out=False)
    spy = _CaptureSpy(push_result=push_result)
    git_write_port.set_default_git_write_factory(lambda: spy)
    try:
        p5._compute_baseline_typecheck_count([], str(tmp_path), "cfg_git_cwd")
    finally:
        git_write_port.reset_default_git_write_factory()

    push_cmd = ["git", "stash", "push", "-u", "-m", "p2-typecheck-baseline-5c14ef32"]
    assert push_cmd in spy.calls, (
        f"Expected spy to record {push_cmd!r}; "
        f"actual recorded calls: {spy.calls!r}.\n"
        f"GREEN must route the typecheck push through git_write_port.git_op_capture."
    )


# ─── AC7 — _compute_baseline_typecheck_count records stash pop (finally) ──────


def test_9ecbf1f5_ac7_baseline_typecheck_records_stash_pop(tmp_path):
    """Inject spy; _compute_baseline_typecheck_count([], "/tmp") → spy recorded
    cmd ["git","stash","pop"] (finally-block pop reached).

    FAILS today: _compute_baseline_typecheck_count calls bounded_run directly →
    spy never called for pop.

    §1i: spy factory reset in finally (workflows.md §1i).
    """
    push_result = GitResult(returncode=0, stdout="", stderr="", timed_out=False)
    spy = _CaptureSpy(push_result=push_result)
    git_write_port.set_default_git_write_factory(lambda: spy)
    try:
        p5._compute_baseline_typecheck_count([], str(tmp_path), "cfg_git_cwd")
    finally:
        git_write_port.reset_default_git_write_factory()

    pop_cmd = ["git", "stash", "pop"]
    assert pop_cmd in spy.calls, (
        f"Expected spy to record {pop_cmd!r}; "
        f"actual recorded calls: {spy.calls!r}.\n"
        f"GREEN must route the finally-block typecheck pop through git_write_port.git_op_capture."
    )


# ─── AC8 — clean-tree sentinel: push recorded, pop NOT recorded ──────────────


def test_9ecbf1f5_ac8_clean_tree_sentinel_no_pop(tmp_path):
    """Inject spy whose op_capture returns GitResult(0,"No local changes to save","",False)
    for push; call _compute_baseline_failed({"groups":[]}, "/tmp") →
      - helper returns None (sentinel branch)
      - spy recorded ["git","stash","push",...] (push was called)
      - spy did NOT record any ["git","stash","pop"] (stashed=False branch, pop skipped)

    FAILS today: _compute_baseline_failed calls bounded_run directly →
    spy never called (push not recorded, behaviour assertion trivially wrong).

    §1i: spy factory reset in finally (workflows.md §1i).
    """
    # Sentinel result: rc=0 with "No local changes to save" on stdout
    sentinel_result = GitResult(
        returncode=0,
        stdout="No local changes to save",
        stderr="",
        timed_out=False,
    )
    spy = _CaptureSpy(push_result=sentinel_result)
    git_write_port.set_default_git_write_factory(lambda: spy)
    try:
        result = p5._compute_baseline_failed({"groups": []}, str(tmp_path), "cfg_git_cwd")
    finally:
        git_write_port.reset_default_git_write_factory()

    assert result is None, (
        f"Helper must return None on clean-tree sentinel; got {result!r}"
    )

    push_cmd = ["git", "stash", "push", "-u", "-m", "p2-baseline-585e30e3"]
    assert push_cmd in spy.calls, (
        f"Push must be recorded even on sentinel branch; calls: {spy.calls!r}"
    )

    pop_cmd = ["git", "stash", "pop"]
    assert pop_cmd not in spy.calls, (
        f"Pop must NOT be recorded when stashed=False (clean-tree branch); "
        f"calls: {spy.calls!r}.\n"
        f"GREEN must preserve the 'if stashed:' guard in the finally block."
    )


# ─── AC9 — source grep: no raw bounded_run(["git","stash") in phase_5 ─────────


def test_9ecbf1f5_ac9_no_raw_bounded_run_stash_in_phase5_source():
    """phase_5_implement.py must contain 0 occurrences of
    `bounded_run(` directly wrapping `["git", "stash"` (after GREEN reroutes
    all 4 callsites through git_write_port.git_op_capture).

    FAILS today: the raw calls still exist (GREEN hasn't run yet).
    """
    phase5_path = Path(p5.__file__)
    src = phase5_path.read_text()

    # Pattern mirrors the spec's grep: bounded_run(\s*["git"\s*,\s*"stash"
    # re.DOTALL so \s* spans newlines (actual source has a newline between
    # bounded_run( and ["git", — multiline indentation style).
    pattern = r'bounded_run\(\s*\[\s*"git"\s*,\s*"stash"'
    matches = re.findall(pattern, src, re.DOTALL)

    assert len(matches) == 0, (
        f"phase_5_implement.py still contains {len(matches)} raw "
        f"bounded_run([\"git\",\"stash\"...) call(s) — GREEN must reroute "
        f"all 4 stash callsites through git_write_port.git_op_capture.\n"
        f"File: {phase5_path}\n"
        f"Matches found at: {[m for m in re.finditer(pattern, src)]!r}"
    )
