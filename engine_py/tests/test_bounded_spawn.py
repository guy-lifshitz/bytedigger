"""RED tests — AC1–AC6 for A375DD88 bounded_spawn wrapper.

lib/bounded_spawn.py does NOT exist yet.  Imports are deferred to inside each
test function body per the D1CF5FDF non-collectable-hang rule so that this file
COLLECTS cleanly and each test FAILS at call time, never at collection time.
"""
from __future__ import annotations

import pathlib
import re


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _engine_py_root() -> pathlib.Path:
    """Return the engine_py root (<repo>/SYSTEM/cli/build/engine_py).

    This file lives at engine_py/tests/test_bounded_spawn.py, so parent of
    parent is engine_py.
    """
    return pathlib.Path(__file__).resolve().parents[1]


def _read_function_slice(filepath: pathlib.Path, fn_name: str) -> str:
    """Extract the source lines from 'def fn_name' to the next top-level def/class.

    Returns the raw string slice so callers can assert substrings.
    """
    src = filepath.read_text(encoding="utf-8")
    lines = src.splitlines()
    start = None
    for i, line in enumerate(lines):
        if re.match(rf"^def {re.escape(fn_name)}\b", line):
            start = i
            break
    if start is None:
        raise AssertionError(f"Function {fn_name!r} not found in {filepath}")

    end = len(lines)
    for i in range(start + 1, len(lines)):
        if re.match(r"^(def |class )\S", lines[i]):
            end = i
            break

    return "\n".join(lines[start:end])


# ---------------------------------------------------------------------------
# AC1 — bounded_run returns CompletedProcess, returncode == 0
# ---------------------------------------------------------------------------

def test_ac1_bounded_run_returns_completed_process_returncode_0():
    from lib.bounded_spawn import bounded_run  # deferred per D1CF5FDF
    import subprocess
    r = bounded_run(["true"], timeout=5)
    assert isinstance(r, subprocess.CompletedProcess), (
        f"expected CompletedProcess, got {type(r)}"
    )
    assert r.returncode == 0, f"expected returncode 0, got {r.returncode}"


# ---------------------------------------------------------------------------
# AC2 — calling without timeout raises TypeError
# ---------------------------------------------------------------------------

def test_ac2_bounded_run_without_timeout_raises_type_error():
    import pytest
    from lib.bounded_spawn import bounded_run  # deferred per D1CF5FDF
    with pytest.raises(TypeError):
        bounded_run(["true"])


# ---------------------------------------------------------------------------
# AC3 — timeout returns rc=124, stdout="", does not raise (text mode)
# ---------------------------------------------------------------------------

def test_ac3_timeout_returns_sentinel_rc124_stdout_empty_str_text_mode():
    from lib.bounded_spawn import bounded_run  # deferred per D1CF5FDF
    r = bounded_run(["sleep", "5"], timeout=0.3, capture_output=True, text=True)
    assert r.returncode == 124, (
        f"expected returncode 124 on timeout, got {r.returncode}"
    )
    assert r.stdout == "", (
        f"expected stdout=='' (str) on timeout in text mode, got {r.stdout!r}"
    )


# ---------------------------------------------------------------------------
# AC4 — timeout in bytes mode, sentinel stdout == b""
# ---------------------------------------------------------------------------

def test_ac4_timeout_sentinel_stdout_bytes_when_no_text_mode():
    from lib.bounded_spawn import bounded_run  # deferred per D1CF5FDF
    r = bounded_run(["sleep", "5"], timeout=0.3)
    assert r.returncode == 124, (
        f"expected returncode 124 on timeout, got {r.returncode}"
    )
    assert r.stdout == b"", (
        f"expected stdout==b'' (bytes) on timeout in bytes mode, got {r.stdout!r}"
    )


# ---------------------------------------------------------------------------
# AC5 — TIMEOUT_RETURNCODE == 124
# ---------------------------------------------------------------------------

def test_ac5_timeout_returncode_constant_equals_124():
    from lib.bounded_spawn import TIMEOUT_RETURNCODE  # deferred per D1CF5FDF
    assert TIMEOUT_RETURNCODE == 124, (
        f"expected TIMEOUT_RETURNCODE==124, got {TIMEOUT_RETURNCODE}"
    )


# ---------------------------------------------------------------------------
# AC6 — each of the 6 migrated prod function bodies contains bounded_run( and timeout=
# ---------------------------------------------------------------------------

def test_ac6_git_toplevel_in_project_root_uses_git_read():
    engine_py = _engine_py_root()
    filepath = engine_py / "lib" / "project_root.py"
    body = _read_function_slice(filepath, "_git_toplevel")
    assert "git_read(" in body, (
        f"_git_toplevel in {filepath} does not call git_read( — seam migration incomplete"
    )
    assert "timeout=" in body, (
        f"_git_toplevel in {filepath} does not pass timeout="
    )
    assert "bounded_run(" not in body, (
        f"_git_toplevel in {filepath} still calls bounded_run( directly — raw primitive must be removed"
    )


def test_ac6_run_git_in_git_diff_uses_git_read():
    engine_py = _engine_py_root()
    filepath = engine_py / "lib" / "plugins" / "disk_truth" / "git_diff.py"
    body = _read_function_slice(filepath, "_run_git")
    assert "git_read(" in body, (
        f"_run_git in {filepath} does not call git_read( — seam migration incomplete"
    )
    assert "timeout=" in body, (
        f"_run_git in {filepath} does not pass timeout="
    )
    assert "bounded_run(" not in body, (
        f"_run_git in {filepath} still calls bounded_run( directly — raw primitive must be removed"
    )


def test_ac6_check_update_needs_update_in_graph_source_uses_bounded_run():
    engine_py = _engine_py_root()
    filepath = engine_py / "workflows" / "graph_source.py"
    body = _read_function_slice(filepath, "_check_update_needs_update")
    assert "bounded_run(" in body, (
        f"_check_update_needs_update in {filepath} does not call bounded_run("
    )
    assert "timeout=" in body, (
        f"_check_update_needs_update in {filepath} does not pass timeout="
    )


def test_ac6_compute_baseline_failed_in_phase5_uses_bounded_run():
    engine_py = _engine_py_root()
    filepath = engine_py / "workflows" / "phase_5_implement.py"
    body = _read_function_slice(filepath, "_compute_baseline_failed")
    # Both stash push (try-body) AND stash pop (finally block) must go through
    # git_op_capture seam (git_write_port), not bounded_run directly.
    count = body.count("git_op_capture(")
    assert count >= 2, (
        f"_compute_baseline_failed in {filepath} must contain at least 2 "
        f"git_op_capture( calls (stash push + stash pop finally), found {count}"
    )
    assert "timeout=" in body, (
        f"_compute_baseline_failed in {filepath} does not pass timeout="
    )


def test_ac6_compute_baseline_typecheck_count_stash_push_uses_bounded_run():
    engine_py = _engine_py_root()
    filepath = engine_py / "workflows" / "phase_5_implement.py"
    body = _read_function_slice(filepath, "_compute_baseline_typecheck_count")
    # The function body must route the stash push through the git_op_capture seam.
    assert "git_op_capture(" in body, (
        f"_compute_baseline_typecheck_count stash push in {filepath} does not call git_op_capture("
    )
    assert "timeout=" in body, (
        f"_compute_baseline_typecheck_count in {filepath} does not pass timeout="
    )


def test_ac6_compute_baseline_typecheck_count_finally_stash_pop_uses_bounded_run():
    engine_py = _engine_py_root()
    filepath = engine_py / "workflows" / "phase_5_implement.py"
    body = _read_function_slice(filepath, "_compute_baseline_typecheck_count")
    # Both stash push AND stash pop must go through the git_op_capture seam.
    # Count occurrences: need >= 2 git_op_capture( calls (push + pop).
    count = body.count("git_op_capture(")
    assert count >= 2, (
        f"_compute_baseline_typecheck_count in {filepath} must contain at least 2 "
        f"git_op_capture( calls (stash push + stash pop finally), found {count}"
    )
