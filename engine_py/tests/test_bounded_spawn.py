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
    from bytedigger_engine.lib.bounded_spawn import bounded_run  # deferred per D1CF5FDF
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
    from bytedigger_engine.lib.bounded_spawn import bounded_run  # deferred per D1CF5FDF
    with pytest.raises(TypeError):
        bounded_run(["true"])


# ---------------------------------------------------------------------------
# AC3 — timeout returns rc=124, stdout="", does not raise (text mode)
# ---------------------------------------------------------------------------

def test_ac3_timeout_returns_sentinel_rc124_stdout_empty_str_text_mode():
    from bytedigger_engine.lib.bounded_spawn import bounded_run  # deferred per D1CF5FDF
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
    from bytedigger_engine.lib.bounded_spawn import bounded_run  # deferred per D1CF5FDF
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
    from bytedigger_engine.lib.bounded_spawn import TIMEOUT_RETURNCODE  # deferred per D1CF5FDF
    assert TIMEOUT_RETURNCODE == 124, (
        f"expected TIMEOUT_RETURNCODE==124, got {TIMEOUT_RETURNCODE}"
    )


# ---------------------------------------------------------------------------
# AC6 — each of the 6 migrated prod function bodies contains bounded_run( and timeout=
# ---------------------------------------------------------------------------

def test_ac6_git_toplevel_in_project_root_uses_git_read():
    engine_py = _engine_py_root()
    filepath = engine_py / "bytedigger_engine" / "lib" / "project_root.py"
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
    filepath = engine_py / "bytedigger_engine" / "lib" / "plugins" / "disk_truth" / "git_diff.py"
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


# graph_source._check_update_needs_update test retired by bd#89 P2a (graph_source.py deleted).


def test_ac6_red_commit_baseline_in_phase5_uses_git_write_seam():
    engine_py = _engine_py_root()
    filepath = engine_py / "bytedigger_engine" / "workflows" / "phase_5_implement.py"
    # bd#88: the stash re-run (_compute_baseline_failed) is gone; its successor
    # adds and removes a detached worktree. Both writes (add in the try body,
    # remove in the finally block) go through the git_op_capture seam.
    body = _read_function_slice(filepath, "_red_commit_baseline_fail_ids")
    count = body.count("git_op_capture(")
    assert count >= 2, (
        f"_red_commit_baseline_fail_ids in {filepath} must contain at least 2 "
        f"git_op_capture( calls (worktree add + worktree remove finally), found {count}"
    )
    assert "timeout=" in body, (
        f"_red_commit_baseline_fail_ids in {filepath} does not pass timeout="
    )


def _assert_no_raw_spawn(body: str, where: str) -> None:
    """No unbounded primitive may appear in *body* — the whole point of the seam."""
    for raw in ("subprocess.run(", "subprocess.Popen(", "os.system(", "bounded_run("):
        assert raw not in body, (
            f"{where} calls {raw} directly — every git op must go through the "
            f"injected write port, never a raw/unbounded spawn"
        )


def _assert_injected_port_is_bounded() -> None:
    """The port `lib/baseline_tree.py` is handed carries the explicit `timeout=`.

    The provider takes its write port as a mandatory keyword and calls it with
    the port's own bound; the bound itself lives at the port definition
    (`phase_workflows_common._git_write`, the single source phase_5_implement
    imports and injects), which must keep a `timeout` in its signature and
    thread it into the `git_op_capture` seam.
    """
    filepath = _engine_py_root() / "bytedigger_engine" / "workflows" / "phase_workflows_common.py"
    body = _read_function_slice(filepath, "_git_write")
    assert "timeout" in body.splitlines()[0], (
        f"_git_write in {filepath} lost its timeout parameter — the baseline "
        f"provider's git ops would become unbounded"
    )
    assert "timeout=timeout" in body, (
        f"_git_write in {filepath} does not thread timeout= into git_op_capture("
    )


def test_ac6_baseline_tree_worktree_add_uses_bounded_git_write_port():
    """Baseline worktree CREATION is a bounded write-port call, not a raw spawn.

    GH1612-B moved this mechanism: `_compute_baseline_typecheck_count` no
    longer performs any git operation itself — it delegates to the canonical
    provider `lib/baseline_tree.py`, which runs `git worktree add --detach`
    through the caller-supplied `_git_write` port (bounded via `timeout=`,
    see `_assert_injected_port_is_bounded`). The pytest baseline
    `_compute_baseline_failed` still stashes and is guarded by its own
    sibling assertions in this file — left untouched.
    """
    filepath = _engine_py_root() / "bytedigger_engine" / "lib" / "baseline_tree.py"
    body = _read_function_slice(filepath, "baseline_tree")
    assert '_git_write(["worktree", "add", "--detach"' in body, (
        f"baseline_tree in {filepath} does not create the worktree through the "
        f"injected _git_write port"
    )
    _assert_no_raw_spawn(body, f"baseline_tree in {filepath}")
    _assert_injected_port_is_bounded()


def test_ac6_baseline_tree_finally_worktree_remove_uses_bounded_git_write_port():
    """Baseline worktree REMOVAL is bounded too, and always runs.

    Same GH1612-B move as the sibling above: the removal leg that used to be
    `_compute_baseline_typecheck_count`'s `finally` stash pop now lives in
    `lib/baseline_tree.py` as a `git worktree remove --force` through the same
    injected port, inside the provider's `finally` block. Both legs must go
    through the port, so the provider body carries >= 2 `_git_write(` calls.
    `_compute_baseline_failed` (the pytest baseline) still stashes and keeps
    its own sibling assertions in this file — untouched.
    """
    filepath = _engine_py_root() / "bytedigger_engine" / "lib" / "baseline_tree.py"
    body = _read_function_slice(filepath, "baseline_tree")
    assert '_git_write(["worktree", "remove", "--force"' in body, (
        f"baseline_tree in {filepath} does not remove the worktree through the "
        f"injected _git_write port"
    )
    assert "finally:" in body, (
        f"baseline_tree in {filepath} has no finally block — removal is no "
        f"longer unconditional"
    )
    count = body.count("_git_write(")
    assert count >= 2, (
        f"baseline_tree in {filepath} must contain at least 2 _git_write( calls "
        f"(worktree add + worktree remove finally), found {count}"
    )
    _assert_no_raw_spawn(body, f"baseline_tree in {filepath}")
    _assert_injected_port_is_bounded()
