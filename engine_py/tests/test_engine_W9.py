"""W9 RED tests for engine.py git-diff handling bugs.

Bug A (findings #6+#7, HIGH): _git_changes_vs_head splits rename/copy lines
with maxsplit=1, producing tab-embedded path keys for `R100\told\tnew` and
`C90\told\tnew` lines from `git diff --name-status HEAD`.

Bug B (finding #18, MED): _diff_changes only iterates post.items(), silently
dropping paths that were in pre but absent from post (deleted/reverted).

§1a sibling-repoint (DEA3305C): The 7 _git_changes_vs_head tests formerly
used `monkeypatch.setattr(engine.subprocess, "run", ...)`. Once GREEN routes
those calls through git_port.git_read, the old subprocess.run patch is bypassed
and the fake never fires (real git runs instead). Repointed here to inject via
set_default_git_read_factory + reset_default_git_read_factory (§1i).

Pre-GREEN status for the 7 repointed tests:
  Each test injects a spy via set_default_git_read_factory but _git_changes_vs_head
  still calls subprocess.run directly — the spy is never reached; real git output
  is used instead of the test fixture. All 7 FAIL pre-GREEN (wrong dict contents
  or test assertion fails against real repo state). PASS post-GREEN.

Bug B tests (_diff_changes) are NOT affected by the seam change and continue to
exercise the pure-Python _diff_changes function directly. Their pre-GREEN
PASS/FAIL status depends only on whether Bug B is already fixed in engine.py.
"""

import pytest

from bytedigger_engine import engine
from bytedigger_engine.engine import _git_changes_vs_head, _diff_changes
from bytedigger_engine.lib.git_port import (
    GitResult,
    set_default_git_read_factory,
    reset_default_git_read_factory,
)


# ---------------------------------------------------------------------------
# Spy factory — injects via git_port seam (replaces old subprocess.run patch)
# ---------------------------------------------------------------------------


class _SpyGitRead:
    """Recording spy matching GitReadPort.__call__ signature.

    Dispatches by argv:
      "ls-files" in args  → GitResult(0, others_stdout, "", False)
      else                → GitResult(0, diff_stdout,   "", False)

    Used to inject controlled diff/ls-files output through the git_port seam
    (set_default_git_read_factory) so _git_changes_vs_head uses fixture data.
    """

    def __init__(self, diff_stdout: str, others_stdout: str = "") -> None:
        self.diff_stdout = diff_stdout
        self.others_stdout = others_stdout

    def __call__(
        self,
        args: list[str],
        *,
        cwd: str | None = None,
        timeout: float | None = None,
        dir_: str | None = None,
    ) -> GitResult:
        if "ls-files" in args:
            return GitResult(returncode=0, stdout=self.others_stdout, stderr="", timed_out=False)
        return GitResult(returncode=0, stdout=self.diff_stdout, stderr="", timed_out=False)


def _make_git_read_spy(diff_stdout: str, others_stdout: str = "") -> _SpyGitRead:
    """Return a spy pre-loaded with the given fixture outputs."""
    return _SpyGitRead(diff_stdout=diff_stdout, others_stdout=others_stdout)


# ---------------------------------------------------------------------------
# Bug A: _git_changes_vs_head — rename/copy parsing
# ---------------------------------------------------------------------------


def test_W9_rename_line_produces_clean_new_path_key():
    """R100\told\tnew → result['src/new.py'] == 'R', no tab-embedded key."""
    spy = _make_git_read_spy("R100\tsrc/old.py\tsrc/new.py\n")
    try:
        set_default_git_read_factory(lambda: spy)
        result = _git_changes_vs_head()
    finally:
        reset_default_git_read_factory()

    assert result is not None
    assert result.get("src/new.py") == "R", (
        f"expected new path keyed under 'src/new.py' with status 'R', got {result!r}"
    )
    # No tab-embedded corruption
    for key in result.keys():
        assert "\t" not in key, f"tab-embedded path key leaked: {key!r}"
    assert "src/old.py\tsrc/new.py" not in result


def test_W9_copy_line_produces_clean_new_path_key():
    """C90\told\tnew → result['lib/b.py'] == 'C', no tab-embedded key."""
    spy = _make_git_read_spy("C90\tlib/a.py\tlib/b.py\n")
    try:
        set_default_git_read_factory(lambda: spy)
        result = _git_changes_vs_head()
    finally:
        reset_default_git_read_factory()

    assert result is not None
    assert result.get("lib/b.py") == "C", (
        f"expected copy target keyed under 'lib/b.py' with status 'C', got {result!r}"
    )
    for key in result.keys():
        assert "\t" not in key, f"tab-embedded path key leaked: {key!r}"
    assert "lib/a.py\tlib/b.py" not in result


def test_W9_plain_modify_still_works_regression():
    """Regression guard: M\tx.py must remain {'foo.py': 'M'}."""
    spy = _make_git_read_spy("M\tfoo.py\n")
    try:
        set_default_git_read_factory(lambda: spy)
        result = _git_changes_vs_head()
    finally:
        reset_default_git_read_factory()
    assert result == {"foo.py": "M"}, result


def test_W9_plain_add_still_works_regression():
    """Regression guard: A\tnew.py from tracked diff."""
    spy = _make_git_read_spy("A\tnew.py\n")
    try:
        set_default_git_read_factory(lambda: spy)
        result = _git_changes_vs_head()
    finally:
        reset_default_git_read_factory()
    assert result == {"new.py": "A"}, result


def test_W9_plain_delete_still_works_regression():
    """Regression guard: D\tgone.py."""
    spy = _make_git_read_spy("D\tgone.py\n")
    try:
        set_default_git_read_factory(lambda: spy)
        result = _git_changes_vs_head()
    finally:
        reset_default_git_read_factory()
    assert result == {"gone.py": "D"}, result


def test_W9_untracked_files_become_A_regression():
    """Regression guard: untracked from ls-files --others get status 'A'."""
    spy = _make_git_read_spy(
        diff_stdout="",
        others_stdout="brand_new.py\nanother.txt\n",
    )
    try:
        set_default_git_read_factory(lambda: spy)
        result = _git_changes_vs_head()
    finally:
        reset_default_git_read_factory()
    assert result == {"brand_new.py": "A", "another.txt": "A"}, result


def test_W9_mixed_rename_modify_and_untracked():
    """Combined: rename + plain modify + untracked all coexist cleanly."""
    spy = _make_git_read_spy(
        diff_stdout=(
            "R100\tsrc/old.py\tsrc/new.py\n"
            "M\tkeep.py\n"
        ),
        others_stdout="untracked.py\n",
    )
    try:
        set_default_git_read_factory(lambda: spy)
        result = _git_changes_vs_head()
    finally:
        reset_default_git_read_factory()

    assert result is not None
    assert result.get("src/new.py") == "R"
    assert result.get("keep.py") == "M"
    assert result.get("untracked.py") == "A"
    for key in result.keys():
        assert "\t" not in key


# ---------------------------------------------------------------------------
# Bug B: _diff_changes — pre-only paths must surface as deletions
# ---------------------------------------------------------------------------


def test_W9_pre_only_path_surfaces_as_deletion():
    """pre={'x.py':'M'}, post={} → 'x.py' must appear in result['D']."""
    result = _diff_changes({"x.py": "M"}, {})
    assert "x.py" in result.get("D", []), (
        f"expected 'x.py' in 'D' bucket (deleted between steps), got {result!r}"
    )


def test_W9_partial_pre_drop_surfaces_as_deletion():
    """pre has a, b; post has only a → 'b.py' must appear in result['D']."""
    result = _diff_changes(
        {"a.py": "M", "b.py": "A"},
        {"a.py": "M"},
    )
    assert "b.py" in result.get("D", []), (
        f"expected 'b.py' in 'D' bucket, got {result!r}"
    )
    # a.py was unchanged → must NOT appear in any bucket
    for bucket, paths in result.items():
        if bucket == "D":
            continue
        assert "a.py" not in paths, f"a.py leaked into bucket {bucket!r}: {result!r}"


def test_W9_new_addition_still_works_regression():
    """Regression guard: pre={}, post={'x.py':'A'} → 'x.py' in result['A']."""
    result = _diff_changes({}, {"x.py": "A"})
    assert "x.py" in result.get("A", []), result


def test_W9_unchanged_path_excluded_regression():
    """Regression guard: same status in pre and post → not in any bucket."""
    result = _diff_changes({"x.py": "M"}, {"x.py": "M"})
    for bucket, paths in result.items():
        assert "x.py" not in paths, (
            f"x.py leaked into bucket {bucket!r} despite unchanged status: {result!r}"
        )


def test_W9_status_change_still_works_regression():
    """Regression guard: status changed M→A → appears in new bucket."""
    result = _diff_changes({"x.py": "M"}, {"x.py": "A"})
    assert "x.py" in result.get("A", []), result
