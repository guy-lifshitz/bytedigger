"""Regression: tier_gate._REPO_ROOT must resolve on ANY checkout layout.

A colleague on Windows (`C:\\dev\\bytedigger\\engine_py`, only 3 dirs deep)
hit `IndexError: 4` at import time because `_REPO_ROOT` was a fixed
`Path(__file__).resolve().parents[4]` — a HAL-layout depth assumption. The
CLI entry-point (`run.py` -> workflows -> verdict_gate -> audit_gate ->
tier_gate) crashed before doing any work. This locks the layout-agnostic
resolver so the fixed-depth assumption cannot come back on a re-port.
"""
from __future__ import annotations

import os
from pathlib import Path

import tier_gate


def test_import_does_not_crash_and_root_is_set():
    # Importing the module (done above) must not raise, and _REPO_ROOT must
    # be a real Path — this is exactly what failed at import time on Windows.
    assert isinstance(tier_gate._REPO_ROOT, Path)


def test_shallow_layout_does_not_indexerror():
    # Emulate C:\dev\bytedigger\engine_py\tier_gate.py: 3 dirs deep, no .git
    # up-tree -> the fixed parents[4] would IndexError. The resolver must
    # fall back instead of raising.
    shallow = Path(os.sep, "dev", "bytedigger", "engine_py", "tier_gate.py")
    root = tier_gate._resolve_repo_root(shallow)  # must not raise
    assert isinstance(root, Path)


def test_prefers_enclosing_git_dir(tmp_path):
    repo = tmp_path / "somerepo"
    (repo / ".git").mkdir(parents=True)
    nested = repo / "engine_py" / "tier_gate.py"
    nested.parent.mkdir(parents=True)
    nested.write_text("")
    assert tier_gate._resolve_repo_root(nested.resolve()) == repo.resolve()
