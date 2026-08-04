"""RED tests for DEA3305C — route engine.py:_git_changes_vs_head reads through git_port.git_read.

Spec: SHARED/memory/Decisions/2026-06-21_DEA3305C_slice3b_engine_git_read_spec.md

Forcing function (§1l / §3):
  Each test installs a recording spy as the git_read implementation via
  set_default_git_read_factory, then calls the UUT (_git_changes_vs_head) and
  asserts the spy was invoked with the correct args and the result is parsed correctly.

  Pre-GREEN: _git_changes_vs_head still calls subprocess.run directly for both
  ["diff","--name-status","HEAD"] and ["ls-files","--others","--exclude-standard"].
  The spy is NEVER reached. All 5 tests FAIL at ASSERT time (spy.calls==[] at
  assertion). NOT vacuous (§1l): we patch the COLLABORATOR (git_port.git_read
  factory via set_default_git_read_factory), NEVER the UUT _git_changes_vs_head
  itself.

  Post-GREEN: _git_changes_vs_head calls git_read(...) (through git_port seam);
  the spy is invoked; all 5 assertions pass.

§1i (singleton-resource, workflows.md §1i): factory swap is pre-staged and always
  reset in finally — no timing race possible.
§1q: no sys.path manipulation here; conftest handles sys.path at import time.
§1q/D1CF5FDF: ALL symbols imported at module level ALREADY EXIST today:
  engine._git_changes_vs_head — exists in engine.py L560.
  GitResult, set_default_git_read_factory, reset_default_git_read_factory — exist
  in lib/git_port.py. The file COLLECTS cleanly; RED fires at ASSERT time, never
  at collection time.

Pre-GREEN PASS/FAIL classification (expected: 5 FAIL / 0 PASS):
  AC1 → FAIL (spy never called; raw subprocess.run used; spy.calls==[] at assertion)
  AC2 → FAIL (spy never called; raw subprocess.run used; spy.calls==[] at assertion)
  AC3 → FAIL (spy never called; raw subprocess.run used; real git output used instead)
  AC4 → FAIL (rc-branch never driven by spy; real git process runs and may succeed)
  AC5 → FAIL (spy never called; real git output used instead of spy's untracked output)
"""
from __future__ import annotations

from bytedigger_engine import engine
from bytedigger_engine.engine import _git_changes_vs_head
from bytedigger_engine.lib.git_port import (
    GitResult,
    set_default_git_read_factory,
    reset_default_git_read_factory,
)


# ─────────────────────────────────────────────────────────────────────────────
# Recording spy — mirrors test_D5D6A364_git_read_routing_slice2.py pattern
# ─────────────────────────────────────────────────────────────────────────────


class _SpyGitRead:
    """Recording spy matching GitReadPort.__call__ signature.

    Dispatches by argv:
      "ls-files" in args  → returns GitResult(0, self.others_stdout, "", False)
      else                → returns GitResult(self.diff_rc, self.diff_stdout, "", False)

    Records ALL calls in self.calls as (args, cwd, timeout) tuples.
    """

    def __init__(
        self,
        diff_stdout: str = "",
        diff_rc: int = 0,
        others_stdout: str = "",
        others_rc: int = 0,
    ) -> None:
        self.diff_stdout = diff_stdout
        self.diff_rc = diff_rc
        self.others_stdout = others_stdout
        self.others_rc = others_rc
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
        if "ls-files" in args:
            return GitResult(
                returncode=self.others_rc,
                stdout=self.others_stdout,
                stderr="",
                timed_out=(self.others_rc == 124),
            )
        return GitResult(
            returncode=self.diff_rc,
            stdout=self.diff_stdout,
            stderr="",
            timed_out=(self.diff_rc == 124),
        )


# ─────────────────────────────────────────────────────────────────────────────
# AC1 — _git_changes_vs_head invokes the spy with argv ["diff","--name-status","HEAD"]
# ─────────────────────────────────────────────────────────────────────────────


def test_ac1_diff_call_routes_through_git_read_seam() -> None:
    """AC1: calling _git_changes_vs_head() routes ["diff","--name-status","HEAD"]
    through the git_read seam (spy records the argv).

    Pre-GREEN FAIL: _git_changes_vs_head calls subprocess.run directly; the spy
    is never reached; spy.calls remains [] at assertion time.
    """
    spy = _SpyGitRead(diff_stdout="M\tfoo.py\n", others_stdout="")

    try:
        set_default_git_read_factory(lambda: spy)
        _git_changes_vs_head()
    finally:
        reset_default_git_read_factory()

    diff_calls = [c for c in spy.calls if c[0] == ["diff", "--name-status", "HEAD"]]
    assert len(diff_calls) >= 1, (
        f"Expected at least 1 call with argv ['diff','--name-status','HEAD'] "
        f"routed through git_read seam, got {[c[0] for c in spy.calls]!r}. "
        f"Pre-GREEN: subprocess.run used directly; spy never invoked."
    )


# ─────────────────────────────────────────────────────────────────────────────
# AC2 — _git_changes_vs_head invokes the spy with argv ["ls-files","--others","--exclude-standard"]
# ─────────────────────────────────────────────────────────────────────────────


def test_ac2_ls_files_call_routes_through_git_read_seam() -> None:
    """AC2: calling _git_changes_vs_head() routes
    ["ls-files","--others","--exclude-standard"] through the git_read seam.

    Pre-GREEN FAIL: _git_changes_vs_head calls subprocess.run directly; the spy
    is never reached; spy.calls remains [] at assertion time.
    """
    spy = _SpyGitRead(diff_stdout="M\tfoo.py\n", others_stdout="")

    try:
        set_default_git_read_factory(lambda: spy)
        _git_changes_vs_head()
    finally:
        reset_default_git_read_factory()

    ls_calls = [
        c for c in spy.calls
        if c[0] == ["ls-files", "--others", "--exclude-standard"]
    ]
    assert len(ls_calls) >= 1, (
        f"Expected at least 1 call with argv "
        f"['ls-files','--others','--exclude-standard'] routed through git_read seam, "
        f"got {[c[0] for c in spy.calls]!r}. "
        f"Pre-GREEN: subprocess.run used directly; spy never invoked."
    )


# ─────────────────────────────────────────────────────────────────────────────
# AC3 — rename line parses correctly through the seam
# ─────────────────────────────────────────────────────────────────────────────


def test_ac3_rename_line_parsed_correctly_via_seam() -> None:
    """AC3: spy returns diff stdout 'R100\\tsrc/old.py\\tsrc/new.py\\n' (rc 0)
    + empty others (rc 0) → result == {"src/new.py": "R"}.

    Pre-GREEN FAIL: spy never reached; real git subprocess runs; real git output
    (from actual repo state) is parsed instead — not the fixture.
    """
    spy = _SpyGitRead(
        diff_stdout="R100\tsrc/old.py\tsrc/new.py\n",
        diff_rc=0,
        others_stdout="",
        others_rc=0,
    )

    try:
        set_default_git_read_factory(lambda: spy)
        result = _git_changes_vs_head()
    finally:
        reset_default_git_read_factory()

    assert result is not None, (
        "Expected a dict result from _git_changes_vs_head, got None. "
        "Pre-GREEN: spy not reached; real git diff may have failed."
    )
    assert result == {"src/new.py": "R"}, (
        f"Expected {{'src/new.py': 'R'}}, got {result!r}. "
        f"Pre-GREEN: spy not reached; real git output used instead of fixture."
    )
    for key in result.keys():
        assert "\t" not in key, f"Tab-embedded path key leaked: {key!r}"


# ─────────────────────────────────────────────────────────────────────────────
# AC4 — diff rc != 0 → returns None
# ─────────────────────────────────────────────────────────────────────────────


def test_ac4_diff_nonzero_rc_returns_none() -> None:
    """AC4: spy returns diff rc != 0 → _git_changes_vs_head() returns None.

    Pre-GREEN FAIL: spy never reached; real git subprocess runs; real git diff
    likely returns rc=0 in a repo, so result is a dict not None.
    """
    spy = _SpyGitRead(
        diff_stdout="",
        diff_rc=128,
        others_stdout="",
        others_rc=0,
    )

    try:
        set_default_git_read_factory(lambda: spy)
        result = _git_changes_vs_head()
    finally:
        reset_default_git_read_factory()

    assert result is None, (
        f"Expected None when diff rc=128 via seam, got {result!r}. "
        f"Pre-GREEN: spy not reached; real git runs and returns rc=0 with real output."
    )


# ─────────────────────────────────────────────────────────────────────────────
# AC5 — empty diff + untracked others → {"u1.py": "A", "u2.py": "A"}
# ─────────────────────────────────────────────────────────────────────────────


def test_ac5_untracked_others_become_A_via_seam() -> None:
    """AC5: spy returns empty diff (rc 0) + others stdout 'u1.py\\nu2.py\\n' (rc 0)
    → result == {"u1.py": "A", "u2.py": "A"}.

    Pre-GREEN FAIL: spy never reached; real git subprocess runs; real ls-files
    output from the repo is used instead of the fixture.
    """
    spy = _SpyGitRead(
        diff_stdout="",
        diff_rc=0,
        others_stdout="u1.py\nu2.py\n",
        others_rc=0,
    )

    try:
        set_default_git_read_factory(lambda: spy)
        result = _git_changes_vs_head()
    finally:
        reset_default_git_read_factory()

    assert result is not None, (
        "Expected a dict result from _git_changes_vs_head, got None. "
        "Pre-GREEN: spy not reached; real git diff may have failed."
    )
    assert result == {"u1.py": "A", "u2.py": "A"}, (
        f"Expected {{'u1.py': 'A', 'u2.py': 'A'}}, got {result!r}. "
        f"Pre-GREEN: spy not reached; real git ls-files output used instead."
    )
