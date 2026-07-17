"""GH895 RED: spec_cite repo-index must exclude nested git checkouts
(linked worktrees + nested clones) from _iter_code_files / _repo_symbol_index.

Spec: SHARED/memory/Decisions/2026-07-16_GH895_spec_cite_worktree_index_leak_spec.md
New symbol under test: spec_cite._in_nested_checkout (not yet defined pre-GREEN).
Per D1CF5FDF / spec Out-of-Role: the new symbol is accessed ONLY inside test
bodies via getattr, so this file always COLLECTS even pre-GREEN.
"""

from pathlib import Path

import spec_cite
from spec_cite import Citation, _iter_code_files, _repo_symbol_index, check_citation


def test_ac1_linked_worktree_git_file_excluded_gh895(tmp_path: Path) -> None:
    """AC1: nested `.git` FILE (linked worktree marker) must be excluded from
    both _iter_code_files and _repo_symbol_index. FAILS pre-GREEN: current
    code has no nested-checkout skip, so wt/leak.py is indexed."""
    root = tmp_path / "ac1"
    root.mkdir()
    (root / "real.py").write_text("def anchor_fn(): pass\n")
    wt = root / "wt"
    wt.mkdir()
    (wt / ".git").write_text("gitdir: /x\n")
    (wt / "leak.py").write_text("gh895_leak_tok_qq17 = 1\n")
    deep_dir = wt / "pkg" / "sub"
    deep_dir.mkdir(parents=True)
    (deep_dir / "deep.py").write_text("gh895_deep_tok_qq17 = 1\n")

    files = _iter_code_files(root)
    assert not any("wt" in p.parts for p in files)
    assert not any(p == (wt / "pkg" / "sub" / "deep.py") for p in files)

    index = _repo_symbol_index(root)
    assert "gh895_leak_tok_qq17" not in index
    assert "gh895_deep_tok_qq17" not in index


def test_ac2_nested_clone_git_dir_excluded_gh895(tmp_path: Path) -> None:
    """AC2: nested `.git` DIRECTORY (nested clone) must also be excluded.
    FAILS pre-GREEN for the same reason as AC1."""
    root = tmp_path / "ac2"
    root.mkdir()
    (root / "real.py").write_text("def anchor_fn(): pass\n")
    wt2 = root / "wt2"
    wt2.mkdir()
    (wt2 / ".git").mkdir()
    (wt2 / "leak2.py").write_text("gh895_leak2_tok_qq17 = 1\n")

    files = _iter_code_files(root)
    assert not any("wt2" in p.parts for p in files)

    index = _repo_symbol_index(root)
    assert "gh895_leak2_tok_qq17" not in index


def test_ac3_top_level_symbol_still_indexed_wrong_file_gh895(tmp_path: Path) -> None:
    """AC3: top-level (non-nested) symbols are NOT over-skipped; check_citation
    against a different existing file yields status "wrong_file". This is
    preserved (pre-existing) behavior and may already PASS pre-GREEN."""
    root = tmp_path / "ac3"
    root.mkdir()
    (root / "real.py").write_text("gh895_top_tok_qq17 = 1\n")
    (root / "other.py").write_text("x = 1\n")

    index = _repo_symbol_index(root)
    assert "gh895_top_tok_qq17" in index

    cit = Citation(file="other.py", symbol="gh895_top_tok_qq17", line_no=1)
    finding = check_citation(cit, root)
    assert finding.status == "wrong_file"


def test_ac4_repo_root_itself_git_dir_not_treated_nested_gh895(tmp_path: Path) -> None:
    """AC4: repo_root itself having a `.git` directory does NOT make its own
    files nested (only STRICT ancestors below repo_root count). Preserved
    behavior — may already PASS pre-GREEN since repo_root is excluded from
    the ancestor walk by construction."""
    root = tmp_path / "ac4"
    root.mkdir()
    (root / ".git").mkdir()
    (root / "real.py").write_text("def anchor_fn(): pass\n")

    index = _repo_symbol_index(root)
    assert "anchor_fn" in index


def test_ac5_check_citation_colocated_becomes_unresolved_gh895(tmp_path: Path) -> None:
    """AC5: with the AC1 fixture, a co-located citation (real.py claiming the
    leaked-nested symbol) must resolve to blocking "unresolved_symbol" (the
    leak previously downgraded this to advisory via wrong_file). FAILS
    pre-GREEN: today the nested token pollutes the index so check_citation
    returns "wrong_file" instead."""
    root = tmp_path / "ac5"
    root.mkdir()
    (root / "real.py").write_text("def anchor_fn(): pass\n")
    wt = root / "wt"
    wt.mkdir()
    (wt / ".git").write_text("gitdir: /x\n")
    (wt / "leak.py").write_text("gh895_leak_tok_qq17 = 1\n")

    cit = Citation(file="real.py", symbol="gh895_leak_tok_qq17", line_no=1)
    finding = check_citation(cit, root)
    assert finding.status == "unresolved_symbol"


def test_ac6_in_nested_checkout_helper_exists_and_classifies_gh895(tmp_path: Path) -> None:
    """AC6 (helper-existence forcing, §1aa): _in_nested_checkout must exist as
    a callable module attribute on spec_cite, and correctly classify a nested
    vs top-level path. FAILS pre-GREEN: attribute does not exist yet
    (getattr with no default raises AttributeError -> test fails)."""
    root = tmp_path / "ac6"
    root.mkdir()
    (root / "real.py").write_text("def anchor_fn(): pass\n")
    wt = root / "wt"
    wt.mkdir()
    (wt / ".git").write_text("gitdir: /x\n")
    (wt / "leak.py").write_text("x = 1\n")

    helper = getattr(spec_cite, "_in_nested_checkout")
    assert callable(helper)
    assert helper(wt / "leak.py", root, {}) is True
    assert helper(root / "real.py", root, {}) is False

    deep_dir = wt / "pkg" / "sub"
    deep_dir.mkdir(parents=True)
    (deep_dir / "deep.py").write_text("x = 1\n")
    assert helper(deep_dir / "deep.py", root, {}) is True


def test_ac7_repo_symbol_index_memoized_same_object_gh895(tmp_path: Path) -> None:
    """AC7 (§1ab): two consecutive calls to _repo_symbol_index(root) return
    the SAME cached object (identity), consistent with AC1 content
    expectations. Uses its own unique tmp root (never shared across tests).
    Behavior of memoization itself is pre-existing and may already PASS
    pre-GREEN; the nested-exclusion content check FAILS pre-GREEN."""
    root = tmp_path / "ac7"
    root.mkdir()
    (root / "real.py").write_text("def anchor_fn(): pass\n")
    wt = root / "wt"
    wt.mkdir()
    (wt / ".git").write_text("gitdir: /x\n")
    (wt / "leak.py").write_text("gh895_leak_tok_qq17 = 1\n")

    first = _repo_symbol_index(root)
    second = _repo_symbol_index(root)
    assert first is second
    assert "gh895_leak_tok_qq17" not in first
