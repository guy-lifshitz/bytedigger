"""RED tests for GH714 — _collect_probe_argv must pin --rootdir to the file's
own directory for the .py branch (kill the 30s collect-cap flake).

Spec: SHARED/memory/Decisions/2026-07-14_GH714_collect_probe_rootdir_pin_spec.md
Issue: #714

Problem (per spec §"Problem"): _collect_probe_argv's .py branch builds
`<py-prefix> --collect-only -q <red_path>` with no `--rootdir`. When red_path's
directory diverges from cwd, pytest's auto-discovered rootdir walk explodes
(measured 82s / ~296k collection nodes / 1.19M stat calls on one trivial file).
Fix (frozen design, §"Design"): insert `["--rootdir", str(Path(red_path).parent)]`
immediately before `--collect-only` in the py branch only.

Pre-GREEN PASS/FAIL classification (per spec §3):
  - AC1 (CORE) → FAIL: no "--rootdir" token present in current argv.
  - AC2 (CORE) → FAIL: same reason, deep path.
  - AC3         → PASS (regression guard, pre & post): argv[-1]==red_path and
                  "--collect-only" in argv already hold today.
  - AC4         → PASS (regression guard, pre & post): sh branch already exact.
  - AC5         → PASS (regression guard, pre & post): unsupported ext already None.
  - AC6 (CORE) → FAIL: no "--rootdir" token present for bare filename either.

Expected pre-GREEN: AC1, AC2, AC6 FAIL. AC3, AC4, AC5 PASS. >=1 CORE FAIL satisfied.
"""
from __future__ import annotations

import sys
from pathlib import Path

# ─── sys.path setup (mirrors test_4238DD14_phase5_validation_bashtest_enum.py:37-48) ──
# §1q / D1CF5FDF: use the conftest-import-time singleton pattern established in
# this suite. Suite safety scanner does not run in the Option-D manual pipeline.
ENGINE_PY = Path(__file__).resolve().parents[1]
if str(ENGINE_PY) not in sys.path:
    sys.path.insert(0, str(ENGINE_PY))
WORKFLOWS = ENGINE_PY / "workflows"
if str(WORKFLOWS) not in sys.path:
    sys.path.insert(0, str(WORKFLOWS))
LIB = ENGINE_PY / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

# ─── telemetry_ctx — must be importable before production imports ──────────────
import telemetry_ctx as _telemetry_ctx  # noqa: E402

# ─── Production module import (exists today — collectable). Access the symbol
# via the module attribute (not a direct `from ... import`) so this file stays
# collectable even if the function signature ever changes shape. §1q discipline. ──
import phase_5_implement as p5mod  # noqa: E402


# ─── AC1 (CORE): absolute path → --rootdir immediately followed by its parent ──


def test_collect_probe_argv_pins_rootdir_to_file_parent_abs_path():
    """AC1 (CORE): _collect_probe_argv("/abs/dir/test_x.py") contains "--rootdir"
    immediately followed by str(Path(red_path).parent) == "/abs/dir".

    Pre-GREEN: FAIL — current .py branch never emits a "--rootdir" token, so
    "--rootdir" is absent from argv entirely (ValueError on .index()).
    Isolation: fails standalone — the argv the pure function returns today
    genuinely has no --rootdir, independent of test order.
    Victim: the .py branch of _collect_probe_argv (phase_5_implement.py) —
    a stub/monkeypatch of some unrelated symbol cannot satisfy this; only the
    real GREEN edit (inserting the two rootdir tokens) does.
    Not-yet-shipped: confirmed by direct read of phase_5_implement.py — the
    py branch is `runner["argv_prefix"] + ["--collect-only", "-q", red_path]`.
    """
    red_path = "/abs/dir/test_x.py"
    argv = p5mod._collect_probe_argv(red_path)
    assert argv is not None, "GH714 AC1: expected a list for .py path, got None"
    assert "--rootdir" in argv, (
        f"GH714 AC1 (CORE): '--rootdir' must be present in .py probe argv, got {argv!r}"
    )
    idx = argv.index("--rootdir")
    expected_rootdir = str(Path(red_path).parent)
    assert argv[idx + 1] == expected_rootdir, (
        f"GH714 AC1 (CORE): token after '--rootdir' must be {expected_rootdir!r}, "
        f"got {argv[idx + 1]!r} (full argv={argv!r})"
    )


# ─── AC2 (CORE): deep path → rootdir == its own parent, not a high ancestor ────


def test_collect_probe_argv_rootdir_scoped_to_deep_parent_not_ancestor():
    """AC2 (CORE): for a deep .py path whose parent dir diverges from any
    plausible cwd, the pinned rootdir equals the file's immediate parent
    (not cwd, not a high ancestor like "/private" or "/private/var").

    Pre-GREEN: FAIL — no "--rootdir" token exists in current argv at all.
    Isolation: fails standalone for the same forcing reason as AC1.
    Victim: same .py branch of _collect_probe_argv; a trivial stub cannot
    satisfy the exact-parent equality without the real fix.
    Not-yet-shipped: confirmed — see AC1 rationale.
    """
    red_path = "/private/var/folders/xx/yy/T/pytest-of-guy/probe-Z/test_deep.py"
    argv = p5mod._collect_probe_argv(red_path)
    assert argv is not None, "GH714 AC2: expected a list for .py path, got None"
    assert "--rootdir" in argv, (
        f"GH714 AC2 (CORE): '--rootdir' must be present in .py probe argv, got {argv!r}"
    )
    idx = argv.index("--rootdir")
    expected_rootdir = str(Path(red_path).parent)
    assert expected_rootdir == "/private/var/folders/xx/yy/T/pytest-of-guy/probe-Z"
    assert argv[idx + 1] == expected_rootdir, (
        f"GH714 AC2 (CORE): rootdir must be the file's immediate parent "
        f"{expected_rootdir!r} (not a high ancestor), got {argv[idx + 1]!r} "
        f"(full argv={argv!r})"
    )


# ─── AC3: py argv still ends with red_path and still contains --collect-only ──


def test_collect_probe_argv_py_branch_preserves_4238dd14_contract():
    """AC3: py argv still ends with red_path and still contains "--collect-only"
    (the pre-existing 4238DD14 AC2 contract must survive the rootdir insertion).

    Pre-GREEN: PASS (regression guard) — this already holds today; it must
    continue to hold post-GREEN too (the rootdir tokens are inserted before
    "--collect-only", not appended after red_path).
    """
    red_path = "/abs/dir/test_x.py"
    argv = p5mod._collect_probe_argv(red_path)
    assert argv is not None, "GH714 AC3: expected a list for .py path, got None"
    assert "--collect-only" in argv, (
        f"GH714 AC3: '--collect-only' must remain in .py probe argv, got {argv!r}"
    )
    assert argv[-1] == red_path, (
        f"GH714 AC3: last argv element must remain red_path {red_path!r}, got {argv[-1]!r}"
    )


# ─── AC4: sh branch argv unchanged, no --rootdir leak ─────────────────────────


def test_collect_probe_argv_sh_branch_unchanged_no_rootdir_leak():
    """AC4: sh branch argv is exactly ["bash", "-n", path] — no "--rootdir"
    injected (the fix must be scoped strictly to the .py branch).

    Pre-GREEN: PASS (regression guard) — sh branch is untouched by the fix
    and already returns this exact list today; must continue to do so.
    """
    result = p5mod._collect_probe_argv("foo.test.sh")
    assert result == ["bash", "-n", "foo.test.sh"], (
        f"GH714 AC4: sh branch must remain exactly ['bash', '-n', 'foo.test.sh'], "
        f"got {result!r}"
    )


# ─── AC5: unsupported extension → None (unchanged) ────────────────────────────


def test_collect_probe_argv_unsupported_extension_still_none():
    """AC5: _collect_probe_argv("foo.rb") returns None (unsupported extension,
    unchanged by the fix).

    Pre-GREEN: PASS (regression guard) — already returns None today.
    """
    result = p5mod._collect_probe_argv("foo.rb")
    assert result is None, f"GH714 AC5: expected None for unsupported extension .rb, got {result!r}"


# ─── AC6 (CORE): bare filename → rootdir token == "." ─────────────────────────


def test_collect_probe_argv_bare_filename_rootdir_is_dot():
    """AC6 (CORE): _collect_probe_argv("foo_test.py") (bare filename, no dir
    component) pins rootdir to "." (str(Path("foo_test.py").parent) == ".")
    and argv still ends with "foo_test.py".

    Pre-GREEN: FAIL — no "--rootdir" token exists in current argv at all, so
    even the bare-filename case has nothing to assert on the rootdir value.
    Isolation: fails standalone for the same forcing reason as AC1/AC2.
    Victim: same .py branch of _collect_probe_argv; only the real GREEN edit
    (Path(red_path).parent stringified) produces "." here.
    Not-yet-shipped: confirmed — see AC1 rationale.
    """
    red_path = "foo_test.py"
    argv = p5mod._collect_probe_argv(red_path)
    assert argv is not None, "GH714 AC6: expected a list for .py path, got None"
    assert "--rootdir" in argv, (
        f"GH714 AC6 (CORE): '--rootdir' must be present in .py probe argv, got {argv!r}"
    )
    idx = argv.index("--rootdir")
    expected_rootdir = str(Path(red_path).parent)
    assert expected_rootdir == "."
    assert argv[idx + 1] == expected_rootdir, (
        f"GH714 AC6 (CORE): rootdir token for bare filename must be '.', "
        f"got {argv[idx + 1]!r} (full argv={argv!r})"
    )
    assert argv[-1] == red_path, (
        f"GH714 AC6: last argv element must remain {red_path!r}, got {argv[-1]!r}"
    )
