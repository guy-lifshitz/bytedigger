"""RED tests for 7F283A2B: _runner_for_path does NOT recognize .sh files.

Today `_runner_for_path("foo.sh")` returns None, causing:
  - `_infer_test_command_for_paths` to return `{"skipped": True, ...}`.
  - `_verify_red_fails_mechanically` to skip mechanical verification entirely.
  - Validation-gate bypass: bash RED tests can be all-passing (fake-RED) and
    still proceed to Opus without `red_test_outcome` telemetry.

Fix (pending GREEN):
    if p.endswith(".sh"):
        return {"kind": "sh", "argv_prefix": ["bash"]}

These five tests must ALL FAIL on the current (unfixed) code, and ALL PASS
after the one-line fix is added to `_runner_for_path`.

Scope: pure registry-level behavior. No subprocess execution, no integration
tests. Out of scope: _verify_red_fails_mechanically integration, .bash/.zsh
variants (HAL convention is .sh only).
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).parent

from bytedigger_engine.workflows.phase_5_implement import _runner_for_path, _infer_test_command_for_paths  # noqa: E402


# ─── Test 1: lowercase .sh ───────────────────────────────────────────────────


def test_runner_for_path_recognizes_sh_lowercase():
    result = _runner_for_path("foo.sh")
    assert result is not None, (
        "_runner_for_path('foo.sh') returned None; expected {'kind': 'sh', 'argv_prefix': ['bash']}"
    )
    assert result == {"kind": "sh", "argv_prefix": ["bash"]}, (
        f"wrong runner dict for foo.sh: {result!r}"
    )


# ─── Test 2: uppercase .SH (function lowercases) ─────────────────────────────


def test_runner_for_path_recognizes_sh_uppercase():
    result = _runner_for_path("BAR.SH")
    assert result is not None, (
        "_runner_for_path('BAR.SH') returned None; expected kind=sh (function lowercases path)"
    )
    assert result["kind"] == "sh", f"expected kind='sh', got kind={result.get('kind')!r}"
    assert result["argv_prefix"] == ["bash"], (
        f"expected argv_prefix=['bash'], got {result.get('argv_prefix')!r}"
    )


# ─── Test 3: full path with directories ──────────────────────────────────────


def test_runner_for_path_path_with_dirs():
    result = _runner_for_path("SYSTEM/cli/build/tests/test-resume-cli.sh")
    assert result is not None, (
        "_runner_for_path('SYSTEM/cli/build/tests/test-resume-cli.sh') returned None; "
        "expected kind=sh"
    )
    assert result["kind"] == "sh", (
        f"expected kind='sh' for directory-prefixed .sh path, got {result.get('kind')!r}"
    )


# ─── Test 4: _infer_test_command groups .sh paths correctly ──────────────────


def test_infer_test_command_groups_sh_paths():
    result = _infer_test_command_for_paths(["a.sh", "b.sh"])
    assert "groups" in result, (
        f"expected 'groups' key (not skipped), got {result!r}; "
        "_runner_for_path must recognize .sh before _infer_test_command can group them"
    )
    groups = result["groups"]
    assert len(groups) == 1, f"expected exactly 1 group for all-.sh input, got {len(groups)}: {groups!r}"
    g = groups[0]
    assert g["kind"] == "sh", f"expected group kind='sh', got {g.get('kind')!r}"
    assert g["argv"] == ["bash", "a.sh", "b.sh"], (
        f"expected argv=['bash', 'a.sh', 'b.sh'], got {g.get('argv')!r}"
    )
    assert g["paths"] == ["a.sh", "b.sh"], (
        f"expected paths=['a.sh', 'b.sh'], got {g.get('paths')!r}"
    )


# ─── Test 5: .sh and .py paths produce separate groups ───────────────────────


def test_infer_test_command_separates_sh_from_py():
    result = _infer_test_command_for_paths(["a.sh", "b.py"])
    assert "groups" in result, (
        f"expected 'groups' key for mixed sh+py, got {result!r}"
    )
    groups = result["groups"]
    assert len(groups) == 2, (
        f"expected 2 groups (sh + py), got {len(groups)}: {groups!r}"
    )
    kinds = {g["kind"] for g in groups}
    assert kinds == {"sh", "py"}, f"expected kinds={{'sh', 'py'}}, got {kinds!r}"

    sh_group = next(g for g in groups if g["kind"] == "sh")
    py_group = next(g for g in groups if g["kind"] == "py")

    assert sh_group["argv"][:1] == ["bash"], (
        f"sh group argv must start with ['bash'], got {sh_group['argv']!r}"
    )
    assert py_group["argv"][:3] == ["python3", "-m", "pytest"], (
        f"py group argv must start with ['python3', '-m', 'pytest'], got {py_group['argv']!r}"
    )
