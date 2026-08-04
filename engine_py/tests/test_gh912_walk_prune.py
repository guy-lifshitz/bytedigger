"""GH912 RED: spec_cite._iter_code_files must prune nested-checkout and
skip-dirs during a topdown os.walk, not visit them first via rglob then
filter afterward.

Spec: SHARED/memory/Decisions/2026-07-16_GH912_spec_cite_walk_prune_spec.md
Unit under test: spec_cite._iter_code_files (existing symbol; body rewritten
by GREEN from rglob to os.walk topdown-prune). No not-yet-existing symbol is
imported at module level, so this file always COLLECTS.
"""

import os
from pathlib import Path

import pytest

from bytedigger_engine.spec_cite import _iter_code_files


def _scandir_visit_recorder(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Wrap os.scandir to record every directory path passed to it, then
    delegate to the real scandir (never alters walk results)."""
    visited: list[str] = []
    real_scandir = os.scandir

    def wrapper(path: str = "."):  # type: ignore[no-untyped-def]
        visited.append(os.path.normpath(str(path)))
        return real_scandir(path)

    monkeypatch.setattr(os, "scandir", wrapper)
    return visited


def test_ac1_parity_composition_gh912(tmp_path: Path) -> None:
    """AC1: normal .py files included; node_modules, nested checkout (file
    and dir .git forms), non-code suffix, oversized file all excluded."""
    root = tmp_path / "ac1"
    root.mkdir()
    (root / "real.py").write_text("def anchor_fn(): pass\n")
    nm = root / "node_modules"
    nm.mkdir()
    (nm / "x.py").write_text("x = 1\n")
    sub = root / "sub"
    sub.mkdir()
    (sub / ".git").write_text("gitdir: /x\n")
    (sub / "a.py").write_text("a = 1\n")
    rep = root / "rep"
    rep.mkdir()
    (rep / ".git").mkdir()
    (rep / "b.py").write_text("b = 1\n")
    (root / "notes.txt").write_text("hello\n")
    big = root / "big.py"
    from bytedigger_engine.spec_cite import _MAX_INDEX_FILE_BYTES

    big.write_text("x" * (_MAX_INDEX_FILE_BYTES + 10))

    files = _iter_code_files(root)
    assert files == [root / "real.py"]


def test_ac2_prune_skips_nested_checkout_subdirs_gh912(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC2 (forcing): a topdown-pruning walk never calls scandir on a
    directory below a nested-checkout's `.git` marker. FAILS pre-GREEN:
    the rglob implementation materializes the whole tree first."""
    root = tmp_path / "ac2"
    root.mkdir()
    (root / "real.py").write_text("def anchor_fn(): pass\n")
    sub = root / "sub"
    sub.mkdir()
    (sub / ".git").write_text("gitdir: /x\n")
    deep = sub / "deep"
    deep.mkdir()
    (deep / "c.py").write_text("c = 1\n")

    visited = _scandir_visit_recorder(monkeypatch)
    _iter_code_files(root)

    assert os.path.normpath(str(deep)) not in visited


def test_ac3_prune_skips_skip_dirs_subdirs_gh912(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC3 (forcing): node_modules/inner is never scandir'd. FAILS pre-GREEN
    for the same reason as AC2."""
    root = tmp_path / "ac3"
    root.mkdir()
    (root / "real.py").write_text("def anchor_fn(): pass\n")
    nm = root / "node_modules"
    nm.mkdir()
    inner = nm / "inner"
    inner.mkdir()
    (inner / "d.py").write_text("d = 1\n")

    visited = _scandir_visit_recorder(monkeypatch)
    _iter_code_files(root)

    assert os.path.normpath(str(inner)) not in visited


def test_ac4_deterministic_sorted_repeat_calls_gh912(tmp_path: Path) -> None:
    """AC4: two consecutive calls return identical, sorted lists."""
    root = tmp_path / "ac4"
    root.mkdir()
    (root / "b.py").write_text("b = 1\n")
    (root / "a.py").write_text("a = 1\n")
    sub = root / "sub"
    sub.mkdir()
    (sub / "c.py").write_text("c = 1\n")

    first = _iter_code_files(root)
    second = _iter_code_files(root)
    assert first == second
    assert first == sorted(first)


def test_ac5_unreadable_dir_never_raises_gh912(tmp_path: Path) -> None:
    """AC5: a directory with mode 000 does not raise; it is simply skipped,
    other files still returned. Permissions restored in finally."""
    root = tmp_path / "ac5"
    root.mkdir()
    (root / "real.py").write_text("def anchor_fn(): pass\n")
    locked = root / "locked"
    locked.mkdir()
    (locked / "e.py").write_text("e = 1\n")

    old_mode = locked.stat().st_mode
    try:
        locked.chmod(0o000)
        files = _iter_code_files(root)
        assert root / "real.py" in files
    finally:
        locked.chmod(old_mode)


def test_ac6_cap_limits_file_count_gh912(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC6: with _MAX_INDEX_FILES patched to 3, exactly 3 files are
    returned even though 10 exist."""
    root = tmp_path / "ac6"
    root.mkdir()
    for i in range(10):
        (root / f"f{i}.py").write_text(f"v{i} = 1\n")

    monkeypatch.setattr("bytedigger_engine.spec_cite._MAX_INDEX_FILES", 3)
    files = _iter_code_files(root)
    assert len(files) == 3


def test_ac7_prune_bounds_visited_dirs_and_is_fast_gh912(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """AC7 (forcing, loose perf proxy): a nested checkout containing 200
    subdirectories x 5 files is pruned before descent, so scandir visits
    fewer than 50 directories and wall stays well under 5s. FAILS
    pre-GREEN: rglob visits every one of the 200 subdirectories."""
    import time

    root = tmp_path / "ac7"
    root.mkdir()
    (root / "real.py").write_text("def anchor_fn(): pass\n")
    nested = root / "nested"
    nested.mkdir()
    (nested / ".git").write_text("gitdir: /x\n")
    for i in range(200):
        d = nested / f"d{i}"
        d.mkdir()
        for j in range(5):
            (d / f"f{j}.py").write_text(f"v{i}_{j} = 1\n")

    visited = _scandir_visit_recorder(monkeypatch)
    start = time.monotonic()
    _iter_code_files(root)
    elapsed = time.monotonic() - start

    assert len(visited) < 50
    assert elapsed < 5.0
