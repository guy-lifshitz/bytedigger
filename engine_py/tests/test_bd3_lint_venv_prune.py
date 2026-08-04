"""RED tests — bd#3: the mutating-git lint must not scan installed dependencies.

`_iter_prod_py_files` prunes `tests` / `__tests__` / `__pycache__` and nothing
else, so a virtualenv inside the tree — which is what the README's install flow
produces — puts every installed third-party package under the lint. In the
clean room it lands on `semgrep/git.py::_remove_worktree_with_check`, a correct
hit for the pattern and a meaningless one for the purpose: it is not engine
code, it can never be registered in `GUARDED_WRITE_SITES`, and no edit makes it
pass. `run_lint` is fail-closed and part of the frozen public API, so the
anti-rot layer becomes unusable for exactly the person it should protect.

AC1  a `.py` under a directory holding `pyvenv.cfg` is not scanned
AC2  `site-packages` / `dist-packages` / `node_modules` are not scanned, at any depth
AC3  a venv under a non-conventional name is still pruned (the marker is
     `pyvenv.cfg`, not the directory name)
AC4  find_unclassified_sites returns [] for a tree whose only hazard is inside a venv
AC5  negative control — a genuine unclassified site in real prod code is STILL
     reported (the prune must not become a blanket "find nothing")
AC6  find_read_port_misuse inherits the same prune (it shares the helper)
AC7  the real engine tree with a venv installed into it reports zero
     unclassified sites — the clean room's actual failure, reproduced
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

from bytedigger_engine.lib import mutating_git_lint  # noqa: E402

# A mutating-git call whose enclosing function is in neither registry — the
# shape `find_unclassified_sites` is built to report. Mirrors semgrep's
# `_remove_worktree_with_check`, the real-world hit from the clean room.
_HAZARD_SOURCE = '''
import subprocess


def _remove_worktree_with_check(path, git_cwd):
    subprocess.run(["git", "worktree", "remove", "--force", path], cwd=git_cwd)
'''


def _write(path: Path, text: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _make_venv(root: Path, name: str = ".venv") -> Path:
    """A virtualenv as `python -m venv` lays one out: pyvenv.cfg at its root."""
    venv = root / name
    _write(venv / "pyvenv.cfg", "home = /usr/local/bin\nversion = 3.11.9\n")
    _write(
        venv / "lib" / "python3.11" / "site-packages" / "semgrep" / "git.py",
        _HAZARD_SOURCE,
    )
    return venv


def _scanned(root: Path) -> list[str]:
    return [str(p) for p in mutating_git_lint._iter_prod_py_files(root)]


# ── AC1 ──────────────────────────────────────────────────────────────────────

def test_ac1_pyvenv_cfg_marks_a_tree_as_not_prod(tmp_path):
    """AC1. Pre-GREEN FAIL: the venv's .py files are yielded."""
    _make_venv(tmp_path)
    _write(tmp_path / "engine.py", "x = 1\n")

    scanned = _scanned(tmp_path)

    assert not any("site-packages" in p for p in scanned), (
        f"expected nothing under a pyvenv.cfg-marked tree; actual {scanned!r}"
    )
    assert any(p.endswith("engine.py") for p in scanned), (
        f"the project's own code must still be scanned; actual {scanned!r}"
    )


# ── AC2 ──────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("vendor_dir", ["site-packages", "dist-packages", "node_modules"])
def test_ac2_vendored_directory_names_are_pruned(tmp_path, vendor_dir):
    """AC2: pruned by name too, at any depth, and with no pyvenv.cfg present —
    a `pip install --target` tree and a Debian `dist-packages` have no venv
    marker at all.

    Pre-GREEN FAIL: yielded.
    """
    _write(tmp_path / "a" / "b" / vendor_dir / "dep" / "git.py", _HAZARD_SOURCE)
    _write(tmp_path / "engine.py", "x = 1\n")

    scanned = _scanned(tmp_path)

    assert not any(vendor_dir in p for p in scanned), (
        f"expected {vendor_dir!r} pruned; actual {scanned!r}"
    )
    assert any(p.endswith("engine.py") for p in scanned)


# ── AC3 ──────────────────────────────────────────────────────────────────────

def test_ac3_venv_under_an_unconventional_name_is_still_pruned(tmp_path):
    """AC3: `.venv` is a convention, not a rule. `python -m venv env311`
    is just as valid, and a name-only prune would miss it — which is how a
    fix that passes AC1 can still fail for the next user.

    Pre-GREEN FAIL: yielded.
    """
    _make_venv(tmp_path, name="env311")
    _write(tmp_path / "engine.py", "x = 1\n")

    scanned = _scanned(tmp_path)

    assert not any("env311" in p for p in scanned), (
        f"expected the pyvenv.cfg marker to prune regardless of directory "
        f"name; actual {scanned!r}"
    )


# ── AC4 ──────────────────────────────────────────────────────────────────────

def test_ac4_hazard_inside_a_venv_is_not_reported(tmp_path):
    """AC4 (the user-visible property, not the helper's): the lint's own
    output is clean for a tree whose only hazard is an installed dependency.

    Pre-GREEN FAIL: one unclassified site, in semgrep.
    """
    _make_venv(tmp_path)
    _write(tmp_path / "engine.py", "x = 1\n")

    unclassified = mutating_git_lint.find_unclassified_sites(tmp_path)

    assert unclassified == [], (
        f"expected no findings from installed dependencies; actual "
        f"{unclassified!r}"
    )


# ── AC5: negative control ────────────────────────────────────────────────────

def test_ac5_a_real_unclassified_site_is_still_reported(tmp_path):
    """AC5: the prune must not degrade into "find nothing".

    Without this, `_iter_prod_py_files` returning an empty iterator satisfies
    AC1-AC4 and AC7, the lint reports zero forever, and the anti-rot layer is
    dead while its own test suite is green — the same false-green class as
    bd#1, one layer down.

    Pre-GREEN FAIL: two sites — the real one and the vendored one. Post-GREEN
    it must be exactly the real one, which is what pins the prune's precision
    in both directions.
    """
    _make_venv(tmp_path)
    _write(tmp_path / "lib" / "real_prod.py", _HAZARD_SOURCE)

    unclassified = mutating_git_lint.find_unclassified_sites(tmp_path)

    assert len(unclassified) == 1, (
        f"expected the project's own unclassified site still reported; actual "
        f"{unclassified!r}"
    )
    assert unclassified[0]["function"] == "_remove_worktree_with_check"
    assert "real_prod.py" in unclassified[0]["file"]
    assert "site-packages" not in unclassified[0]["file"]


# ── AC6 ──────────────────────────────────────────────────────────────────────

def test_ac6_read_port_misuse_inherits_the_prune(tmp_path):
    """AC6 (§1u): `find_read_port_misuse` is the second public entry point over
    the same helper. Fixing one call path and not the other would leave half
    the API scanning dependencies.

    Pre-GREEN FAIL: reports the vendored misuse.
    """
    venv = _make_venv(tmp_path)
    _write(
        venv / "lib" / "python3.11" / "site-packages" / "dep" / "bad.py",
        'def f(git_cwd):\n    git_port.git_read(["commit", "-m", "x"], cwd=git_cwd)\n',
    )

    misuse = mutating_git_lint.find_read_port_misuse(tmp_path)

    assert misuse == [], (
        f"expected no read-port findings from installed dependencies; actual "
        f"{misuse!r}"
    )


# ── AC7: the clean room's actual failure ─────────────────────────────────────

def test_ac7_real_engine_tree_with_a_venv_reports_zero(tmp_path, monkeypatch):
    """AC7 (§1l — reproduces the reported failure against the REAL tree, not a
    fixture): the clean room installs into `engine_py/.venv`, and that is the
    only difference between the machine where AC32 passes and the machine
    where it fails. This stages a real copy of the prod tree (2.5 MB of `.py`
    across 164 files, tests excluded) and plants a venv in it — a copy rather
    than symlinks, because `Path.rglob` does not descend through directory
    symlinks and the test would pass vacuously.

    Pre-GREEN FAIL: one unclassified site, in the planted venv.
    """
    import shutil  # noqa: PLC0415

    engine_root = HERE.parent
    staged = tmp_path / "engine_py"
    shutil.copytree(
        engine_root,
        staged,
        ignore=shutil.ignore_patterns("tests", "__pycache__", ".*"),
        symlinks=True,
        ignore_dangling_symlinks=True,
    )

    assert mutating_git_lint.find_unclassified_sites(staged) == [], (
        "fixture precondition: the staged copy must be clean BEFORE the venv "
        "is planted, otherwise this test proves nothing about the venv"
    )

    _make_venv(staged)

    unclassified = mutating_git_lint.find_unclassified_sites(staged)

    assert unclassified == [], (
        f"expected zero unclassified sites for the real engine tree with a "
        f"venv installed into it; actual {unclassified!r}"
    )
