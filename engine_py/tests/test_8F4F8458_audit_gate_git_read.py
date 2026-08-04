"""RED tests for 8F4F8458 — route audit_gate staged-path git diffs through git_port.git_read.

Agreement: 8F4F8458-6F7C-4269-825B-116DBF1330A4
Spec: SHARED/memory/Decisions/2026-06-21_8F4F8458_audit_gate_git_read_spec.md

All tests MUST FAIL until GREEN applies §1.1 edits to audit_gate.py:
  - drops `runner=subprocess.run` from both _git_staged_modified_paths and _git_all_staged_paths
  - adds `from bytedigger_engine.lib.git_port import git_read`
  - removes `import subprocess`
  - rewrites both bodies to call git_read(...)

Expected RED (pre-GREEN FAIL reasons):
  AC1 (test_ac1_staged_modified_routes_through_git_port_seam):
    FAIL — fn uses raw subprocess.run; set_default_git_read_factory ignored;
    captured stays empty → assertion on --diff-filter=MR fails.
  AC3 (test_ac3_both_fns_use_git_read):
    FAIL — source contains `runner(`, `subprocess.run`, `["git"` and NOT `git_read(`.
  AC4 (test_ac4_import_subprocess_removed):
    FAIL — `import subprocess` is present at module top level.
  AC5 (test_ac5_blank_lines_stripped):
    FAIL — fn uses raw subprocess.run; factory ignored; real git runs (not fake),
    returns unexpected output → assert == ["a.py","b.py"] fails.

§1i (singleton-resource): set_default_git_read_factory is a global seam; each test
resets it in a `finally` block — no race possible (tests run serially; no time-based
resource). §1i cited as required by workflows.md §1i (pre-stage global state, never race).

§1q (81F97F3D): no sys.path mutation at module level; conftest provides engine_py root.
§1q extension (D1CF5FDF): all audit_gate imports deferred to inside test fn bodies so
the file COLLECTS cleanly and FAILs at assert time.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


# ─── AC1: _git_staged_modified_paths routes through git_port seam ────────────


def test_ac1_staged_modified_routes_through_git_port_seam() -> None:
    """AC1 (§1l forcing-fn): inject fake reader via set_default_git_read_factory;
    call _git_staged_modified_paths(); assert captured args contain
    '--diff-filter=MR' and '--cached', and return value is ['x.py'].

    Pre-GREEN FAIL: fn uses raw subprocess.run default; injected factory ignored;
    captured stays empty → '--diff-filter=MR' assertion fails.
    """
    from bytedigger_engine import audit_gate
    from bytedigger_engine.lib.git_port import (
        GitResult,
        reset_default_git_read_factory,
        set_default_git_read_factory,
    )

    captured: list = []

    def fake_reader(args, *, cwd=None, timeout=None, dir_=None):
        captured.extend(args)
        return GitResult(returncode=0, stdout="x.py\n", stderr="", timed_out=False)

    set_default_git_read_factory(lambda: fake_reader)
    try:
        result = audit_gate._git_staged_modified_paths()
    finally:
        reset_default_git_read_factory()

    arg_str = " ".join(str(a) for a in captured)

    assert "--diff-filter=MR" in arg_str or (
        "--diff-filter" in arg_str and "MR" in arg_str
    ), (
        f"Expected '--diff-filter=MR' in captured git_read args. "
        f"Got: {captured!r}. "
        f"Pre-GREEN: subprocess.run used; factory never consulted; captured is empty."
    )
    assert "--cached" in arg_str, (
        f"Expected '--cached' in captured git_read args. Got: {captured!r}."
    )
    assert result == ["x.py"], (
        f"Expected return value ['x.py'] from fake reader stdout. Got: {result!r}."
    )


# ─── AC3: both fn source-slices contain git_read( and not runner(/subprocess.run ─


def test_ac3_both_fns_use_git_read() -> None:
    """AC3: read audit_gate.py source; slice each of the two fn bodies; assert each
    slice contains 'git_read(', its respective --diff-filter literal, and does NOT
    contain 'runner(' or 'subprocess.run' or the literal '[\"git\"'.

    Pre-GREEN FAIL: both slices contain 'runner(' and 'subprocess.run' and '["git"'
    and do NOT contain 'git_read('. All four negative assertions fire.
    """
    from bytedigger_engine import audit_gate

    source = Path(audit_gate.__file__).read_text(encoding="utf-8")
    lines = source.splitlines()

    def _slice_fn(name: str) -> str:
        """Extract from 'def <name>' line up to (not including) the next top-level def/class."""
        start: int | None = None
        end: int | None = None
        for i, ln in enumerate(lines):
            if ln.startswith(f"def {name}(") or ln.startswith(f"def {name} ("):
                start = i
            elif start is not None and i > start and (
                ln.startswith("def ") or ln.startswith("class ")
            ):
                end = i
                break
        assert start is not None, f"Could not find 'def {name}' in audit_gate.py"
        body_lines = lines[start:end] if end is not None else lines[start:]
        return "\n".join(body_lines)

    mr_body = _slice_fn("_git_staged_modified_paths")
    acmr_body = _slice_fn("_git_all_staged_paths")

    # _git_staged_modified_paths assertions
    assert "git_read(" in mr_body, (
        f"_git_staged_modified_paths body must contain 'git_read(' after GREEN. "
        f"Got body:\n{mr_body}"
    )
    assert "--diff-filter=MR" in mr_body, (
        f"_git_staged_modified_paths body must contain '--diff-filter=MR'."
    )
    assert "runner(" not in mr_body, (
        f"_git_staged_modified_paths body must NOT contain 'runner(' after GREEN."
    )
    assert "subprocess.run" not in mr_body, (
        f"_git_staged_modified_paths body must NOT contain 'subprocess.run' after GREEN."
    )
    assert '["git"' not in mr_body, (
        f"_git_staged_modified_paths body must NOT contain '[\"git\"' after GREEN."
    )

    # _git_all_staged_paths assertions
    assert "git_read(" in acmr_body, (
        f"_git_all_staged_paths body must contain 'git_read(' after GREEN. "
        f"Got body:\n{acmr_body}"
    )
    assert "--diff-filter=ACMR" in acmr_body, (
        f"_git_all_staged_paths body must contain '--diff-filter=ACMR'."
    )
    assert "runner(" not in acmr_body, (
        f"_git_all_staged_paths body must NOT contain 'runner(' after GREEN."
    )
    assert "subprocess.run" not in acmr_body, (
        f"_git_all_staged_paths body must NOT contain 'subprocess.run' after GREEN."
    )
    assert '["git"' not in acmr_body, (
        f"_git_all_staged_paths body must NOT contain '[\"git\"' after GREEN."
    )


# ─── AC4: import subprocess removed ──────────────────────────────────────────


def test_ac4_import_subprocess_removed() -> None:
    """AC4: audit_gate.py must NOT have a top-level 'import subprocess' line.

    Pre-GREEN FAIL: line 18 is 'import subprocess' → regex matches → assert fails.
    """
    from bytedigger_engine import audit_gate

    source = Path(audit_gate.__file__).read_text(encoding="utf-8")
    matches = re.findall(r"^import subprocess$", source, re.MULTILINE)
    assert not matches, (
        f"Expected no top-level 'import subprocess' in audit_gate.py after GREEN "
        f"(subprocess used only by the dropped runner defaults). "
        f"Found {len(matches)} match(es). "
        f"Pre-GREEN: line 18 is 'import subprocess' — GREEN must remove it."
    )


# ─── AC5: blank-line parse guard ─────────────────────────────────────────────


def test_ac5_blank_lines_stripped() -> None:
    """AC5: inject fake reader returning 'a.py\\n\\nb.py\\n'; assert
    _git_staged_modified_paths() returns ['a.py', 'b.py'] (blank lines stripped).

    Pre-GREEN FAIL: fn uses raw subprocess.run; injected factory ignored; real git
    runs and returns something other than ['a.py','b.py'] → assert fails.
    """
    from bytedigger_engine import audit_gate
    from bytedigger_engine.lib.git_port import (
        GitResult,
        reset_default_git_read_factory,
        set_default_git_read_factory,
    )

    def fake_reader(args, *, cwd=None, timeout=None, dir_=None):
        return GitResult(returncode=0, stdout="a.py\n\nb.py\n", stderr="", timed_out=False)

    set_default_git_read_factory(lambda: fake_reader)
    try:
        result = audit_gate._git_staged_modified_paths()
    finally:
        reset_default_git_read_factory()

    assert result == ["a.py", "b.py"], (
        f"Expected ['a.py','b.py'] (blank lines stripped) from fake reader stdout. "
        f"Got: {result!r}. "
        f"Pre-GREEN: factory ignored; real git runs; output unpredictable."
    )
