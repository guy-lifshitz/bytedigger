"""RED tests for 9397B99B — route phase_45_spec._build_suffix_map ls-files through git_read.

Spec: SHARED/memory/Decisions/2026-06-21_9397B99B_slice3bii_suffixmap_git_read_spec.md

Forcing function (§1l / §3):
  Each test installs a recording spy as the git_read implementation via
  set_default_git_read_factory, then calls _build_suffix_map and asserts the spy
  was invoked with the correct argv.

  Pre-GREEN: _build_suffix_map still calls bounded_run directly (subprocess path).
  The spy installed via the git_port factory is NEVER reached.
  All 4 tests FAIL at ASSERT time (spy.calls == [] at assertion, or wrong result).
  NOT vacuous (§1l): we patch the COLLABORATOR (git_port factory), never _build_suffix_map.

  Post-GREEN: _build_suffix_map calls git_read(...); the spy is invoked; assertions pass.

§1i (singleton-resource, workflows.md §1i): factory swap is pre-staged and ALWAYS
  reset in finally — deterministic, never racing.
§1q: no sys.path manipulation here; conftest.py handles it at import time.
§1q/D1CF5FDF: ALL symbols imported at module level already exist today —
  phase_45_spec._build_suffix_map, GitResult, set_default_git_read_factory,
  reset_default_git_read_factory — file COLLECTS cleanly; RED fires at assert time.

Pre-GREEN PASS/FAIL classification:
  AC1 → FAIL  (spy never called; bounded_run used directly; spy.calls == [])
  AC2 → FAIL  (spy never called; real bounded_run used; result may differ from {})
  AC3 → FAIL  (spy never called; real bounded_run used; result may differ from {})
  AC4 → FAIL  (spy never called; real repo output returned instead of fixture data)

Expected pre-GREEN: 4 FAIL / 0 PASS.
"""
from __future__ import annotations

from pathlib import Path

# conftest-import-time singleton handles sys.path (§1q / 81F97F3D gate).
# Do NOT add sys.path.insert here.

import lib.git_port as git_port_mod
from lib.git_port import (
    GitResult,
    set_default_git_read_factory,
    reset_default_git_read_factory,
)

import phase_45_spec


# ─────────────────────────────────────────────────────────────────────────────
# Recording spy (matches GitReadPort.__call__ signature)
# ─────────────────────────────────────────────────────────────────────────────


class _SpyGitRead:
    """Recording spy that matches GitReadPort.__call__ signature.

    Records ALL calls in self.calls as (args, cwd, timeout, dir_) tuples.
    Returns self.result for every call.
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
        self.calls.append((args, cwd, timeout, dir_))
        return self.result


# ─────────────────────────────────────────────────────────────────────────────
# Expected argv constant
# ─────────────────────────────────────────────────────────────────────────────

_EXPECTED_ARGV = ["-c", "core.quotepath=false", "ls-files", "-z"]


# ─────────────────────────────────────────────────────────────────────────────
# AC1 — spy receives correct argv when _build_suffix_map is called
# ─────────────────────────────────────────────────────────────────────────────


def test_ac1_build_suffix_map_routes_through_git_read_argv() -> None:
    """AC1: _build_suffix_map routes ls-files through git_read with correct argv.

    Spy returns GitResult(0, "", "", False) — no files, empty result.
    Assert: spy was called once with argv == ["-c","core.quotepath=false","ls-files","-z"].

    Pre-GREEN FAIL: bounded_run called directly; spy.calls == [] at assertion.
    Spec §1i: factory swap pre-staged and always reset in finally.
    """
    spy = _SpyGitRead(GitResult(returncode=0, stdout="", stderr="", timed_out=False))

    try:
        set_default_git_read_factory(lambda: spy)
        phase_45_spec._build_suffix_map(Path("/any"))
    finally:
        reset_default_git_read_factory()

    assert len(spy.calls) == 1, (
        f"Expected spy called once; got {len(spy.calls)} calls — "
        f"pre-GREEN: bounded_run used directly, spy never reached"
    )
    recorded_argv, _cwd, _timeout, _dir = spy.calls[0]
    assert recorded_argv == _EXPECTED_ARGV, (
        f"Expected argv {_EXPECTED_ARGV!r}, got {recorded_argv!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# AC2 — rc=124 (timed_out) → returns {}
# ─────────────────────────────────────────────────────────────────────────────


def test_ac2_build_suffix_map_timeout_returns_empty() -> None:
    """AC2: spy returns GitResult(rc=124, timed_out=True) → result == {}.

    Pre-GREEN FAIL: spy not reached; real bounded_run used against "/any"
    (likely raises OSError/FileNotFoundError → {} anyway, but spy.calls==[] proves
    the seam was never used, so the AC is technically FAIL at the argv assertion —
    we assert spy was called and result is {}).
    Spec §1i: always reset in finally.
    """
    spy = _SpyGitRead(
        GitResult(returncode=124, stdout="", stderr="", timed_out=True)
    )

    result: dict = {}
    try:
        set_default_git_read_factory(lambda: spy)
        result = phase_45_spec._build_suffix_map(Path("/any"))
    finally:
        reset_default_git_read_factory()

    assert len(spy.calls) == 1, (
        f"Expected spy called once (timeout branch via seam); "
        f"got {len(spy.calls)} — pre-GREEN: bounded_run used, spy never reached"
    )
    assert result == {}, (
        f"Expected {{}} on rc=124 timeout; got {result!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# AC3 — rc=128 (non-zero, non-timeout) → returns {}
# ─────────────────────────────────────────────────────────────────────────────


def test_ac3_build_suffix_map_nonzero_rc_returns_empty() -> None:
    """AC3: spy returns GitResult(rc=128, timed_out=False) → result == {}.

    Collapses the old CalledProcessError arm (check=True on rc!=0) into the
    new `if result.returncode != 0: return {}` branch.

    Pre-GREEN FAIL: spy not reached; bounded_run called directly with check=True
    which raises CalledProcessError (or OSError for /any) → {} anyway, but spy
    was never invoked; asserted via spy.calls == [].
    Spec §1i: always reset in finally.
    """
    spy = _SpyGitRead(
        GitResult(returncode=128, stdout="", stderr="fatal: not a git repository", timed_out=False)
    )

    result: dict = {}
    try:
        set_default_git_read_factory(lambda: spy)
        result = phase_45_spec._build_suffix_map(Path("/any"))
    finally:
        reset_default_git_read_factory()

    assert len(spy.calls) == 1, (
        f"Expected spy called once (rc=128 nonzero branch via seam); "
        f"got {len(spy.calls)} — pre-GREEN: bounded_run used, spy never reached"
    )
    assert result == {}, (
        f"Expected {{}} on rc=128; got {result!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# AC4 — rc=0 + NUL-delimited stdout → correct suffix map, ambiguous dropped
# ─────────────────────────────────────────────────────────────────────────────


def test_ac4_build_suffix_map_parses_stdout_and_drops_ambiguous() -> None:
    """AC4: spy returns rc=0 + stdout "a/b/foo.py\\x00c/foo.py\\x00".

    Expected result:
      "b/foo.py"   -> "a/b/foo.py"   (unique 2-segment suffix)
      "a/b/foo.py" -> "a/b/foo.py"   (unique full path)
      "c/foo.py"   -> "c/foo.py"     (unique 2-segment suffix)
      "foo.py"     NOT in result     (ambiguous: matches both files → dropped)

    Pre-GREEN FAIL: spy not reached; real git runs against "/any" → either raises
    OSError/returns {} or returns live repo data, neither matching the fixture dict.
    The spy.calls==[] assertion alone proves the seam was bypassed.
    Spec §1i: always reset in finally.
    """
    fixture_stdout = "a/b/foo.py\x00c/foo.py\x00"
    spy = _SpyGitRead(
        GitResult(returncode=0, stdout=fixture_stdout, stderr="", timed_out=False)
    )

    result: dict = {}
    try:
        set_default_git_read_factory(lambda: spy)
        result = phase_45_spec._build_suffix_map(Path("/any"))
    finally:
        reset_default_git_read_factory()

    # Seam-routing assertion (forcing function — spy must have been called)
    assert len(spy.calls) == 1, (
        f"Expected spy called once (rc=0 parse path via seam); "
        f"got {len(spy.calls)} — pre-GREEN: bounded_run used, spy never reached"
    )

    # Parse-correctness assertions
    assert result.get("b/foo.py") == "a/b/foo.py", (
        f"Expected 'b/foo.py' -> 'a/b/foo.py'; got {result!r}"
    )
    assert result.get("a/b/foo.py") == "a/b/foo.py", (
        f"Expected 'a/b/foo.py' -> 'a/b/foo.py'; got {result!r}"
    )
    assert result.get("c/foo.py") == "c/foo.py", (
        f"Expected 'c/foo.py' -> 'c/foo.py'; got {result!r}"
    )
    assert "foo.py" not in result, (
        f"'foo.py' must be absent (ambiguous — matches both paths); got {result!r}"
    )
