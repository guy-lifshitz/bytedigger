"""Agreement: 7DDD9529 — phase_5_implement.py git commit argv must include
`-o -m MSG -- *paths` at both call-sites so commits are scoped to the paths
that were just `git add`'d (AP4 accidental-bundling sub-class fix).

RED-phase tests.  All tests in this file MUST FAIL against the current
production code (lines 1251 + 2970 still use `["git", "commit", "-m", ...]`
without `-o` / `"--"` / path unpacking).

Spec: SHARED/memory/Decisions/2026-05-15_7DDD9529_phase_5_commit_only_paths_spec.md
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

ENGINE_PY_ROOT = Path(__file__).resolve().parent.parent
PHASE_5 = ENGINE_PY_ROOT / "workflows" / "phase_5_implement.py"

# Ensure the engine root is importable so we can import functions for AC6.
if str(ENGINE_PY_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_PY_ROOT))
_WORKFLOWS = ENGINE_PY_ROOT / "workflows"
if str(_WORKFLOWS) not in sys.path:
    sys.path.insert(0, str(_WORKFLOWS))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _source() -> str:
    """Return full source text of phase_5_implement.py (cached per process)."""
    return PHASE_5.read_text()


def _function_source(name: str) -> str:
    """Return the source block of a top-level function, read directly from
    the canonical PHASE_5 file (GH684: NOT via `import` + `inspect.getsource`,
    which order-flakes under the full parallel suite when another test leaves
    a shadowed/stale `phase_5_implement` in sys.modules/linecache)."""
    src = _source()
    m = re.search(rf"^def {re.escape(name)}\(", src, re.MULTILINE)
    assert m, f"function {name} not found in {PHASE_5}"
    tail = src[m.start():]
    nxt = re.search(r"\n(?=def |class |@)", tail[1:])
    return tail[: nxt.start() + 1] if nxt else tail


# ---------------------------------------------------------------------------
# AC1 — Every `git commit` argv MUST include `"--"` within 4 array elements
#        after `-m`.  Bare `"git", "commit", "-m"` without a subsequent `"--"`
#        must NOT exist.
# ---------------------------------------------------------------------------

def test_ac1_no_bare_git_commit_m_without_double_dash_separator():
    """AC1 — no `["git", "commit", "-m", ...]` without `"--"` within ~4 elements.

    Strategy: find every line containing the commit argv construction, then
    verify each occurrence either already contains `"-o"` (new shape) OR has
    `"--"` within 4 lines after it (meaning the separator is present).

    Current production code at lines 1251 + 2970 has NEITHER `-o` NOR `"--"`
    so this test FAILs today.
    """
    src = _source()

    # Regex: find the position of every `"git", "commit", "-m"` fragment.
    pattern = re.compile(r'"git",\s*"commit",\s*"-m"')
    matches = list(pattern.finditer(src))

    violations = []
    for m in matches:
        # Extract a ~200-char window around the match to check for "--" and "-o"
        window = src[m.start(): m.start() + 200]
        has_double_dash = '"--"' in window
        has_dash_o = '"-o"' in window
        if not has_double_dash and not has_dash_o:
            # Find the line number for reporting
            lineno = src[: m.start()].count("\n") + 1
            violations.append(f"line {lineno}: {window[:80]!r}")

    assert not violations, (
        "Found git commit call-sites WITHOUT '-o' or '--' path separator:\n"
        + "\n".join(violations)
    )

    # Post-GREEN invariant: at least one -o-flagged commit call-site must exist.
    assert '"git", "commit", "-o"' in src, (
        "post-GREEN: expected -o-flagged commit call-site in phase_5_implement.py"
    )


# ---------------------------------------------------------------------------
# AC2 — Exactly 2 occurrences of `"-o", "-m"` consecutive pair (one per call-site).
# ---------------------------------------------------------------------------

def test_ac2_two_commit_call_sites_use_dash_o_dash_m():
    """AC2 — exactly 2 matches of the `"-o", "-m"` consecutive pair.

    Current production code has 0 matches (no `-o` at either call-site),
    so this test FAILs today.
    """
    src = _source()
    pattern = re.compile(r'"-o",\s*"-m"')
    matches = list(pattern.finditer(src))
    assert len(matches) == 2, (
        f"Expected exactly 2 occurrences of '\"-o\", \"-m\"' in phase_5_implement.py, "
        f"found {len(matches)}"
    )


# ---------------------------------------------------------------------------
# AC3 — `_commit_red_tests` function body contains `*trackable_paths` after `"--"`.
# ---------------------------------------------------------------------------

def test_ac3_commit_red_tests_argv_unpacks_trackable_paths():
    """AC3 — `_commit_red_tests` argv at the commit call-site contains
    `*trackable_paths` (or equivalent unpacking) AFTER `"--",`.

    Current production code: `["git", "commit", "-m", commit_message]` — no `"--"`
    and no `*trackable_paths` unpacking — so this test FAILs today.
    """
    body = _function_source("_commit_red_tests")

    # Must have "--", separator
    assert '"--",' in body or '"--"' in body, (
        '_commit_red_tests body must contain the "--" pathspec separator'
    )

    # Must unpack trackable_paths AFTER the "--" separator
    # Accept: `"--", *trackable_paths` or `"--",\n    *trackable_paths`
    pattern = re.compile(r'"--",\s*\*trackable_paths')
    assert pattern.search(body), (
        '_commit_red_tests git commit argv must unpack `*trackable_paths` '
        'immediately after `"--",`'
    )


# ---------------------------------------------------------------------------
# AC4 — `_commit_green_code` function body contains `*prod_paths` (or similar)
#        unpacking after `"--"`.
# ---------------------------------------------------------------------------

def test_ac4_commit_green_code_argv_unpacks_prod_paths():
    """AC4 — `_commit_green_code` argv at the commit call-site contains
    `*<paths-var>` unpacking after `"--",`.

    The variable name may be `prod_paths` or similar — the regex accepts any
    `*<word>_paths` pattern after `"--",`.

    Current production code: `["git", "commit", "-m", commit_message]` — no
    `"--"` and no unpacking — so this test FAILs today.
    """
    body = _function_source("_commit_green_code")

    # Must have "--", separator
    assert '"--",' in body or '"--"' in body, (
        '_commit_green_code body must contain the "--" pathspec separator'
    )

    # Accept any `"--", *<word>_paths` unpacking
    pattern = re.compile(r'"--",\s*\*\w+_paths')
    assert pattern.search(body), (
        '_commit_green_code git commit argv must contain `"--", *<name>_paths` '
        'path unpacking; current code has no such pattern'
    )


# ---------------------------------------------------------------------------
# AC5 — Existing phase_5 tests still pass (regression baseline).
#        Orchestrator runs the full suite independently; this is a doc-only guard.
# ---------------------------------------------------------------------------

@pytest.mark.skip(reason="orchestrator-side regression check — run pytest engine_py/tests/test_phase_5_implement_B1AAACFB_sub1.py engine_py/tests/test_phase_5_integrity.py independently")
def test_ac5_existing_phase_5_implement_tests_still_pass():
    """AC5 — regression baseline.

    The orchestrator runs the existing phase_5 test files independently
    to verify zero regressions.  This test is marked skip to avoid
    re-running pytest-from-pytest (brittle) while still documenting the AC.
    """
    assert True  # orchestrator gate, not executed in this file


# ---------------------------------------------------------------------------
# AC6a — monkeypatch `_git_op_with_lock_retry` in `_commit_red_tests`,
#         capture argv, assert `-o`, `--`, and `trackable_paths` present.
# ---------------------------------------------------------------------------

def test_ac6_subprocess_argv_via_monkeypatch_commit_red(tmp_path):
    """AC6a — call `_commit_red_tests` with a minimal fixture + monkeypatched
    `_git_op_with_lock_retry`; assert captured commit argv contains
    `-o`, `"--"`, and the expected path.

    We monkeypatch at the module level (`phase_5_implement._git_op_with_lock_retry`)
    so the function under test calls our spy instead of real git.

    Additionally, `subprocess.run` is patched for the non-lock-retry calls
    (git status, rev-parse, check-ignore) to return minimal fixture responses.

    FAILs today because the real argv is `["git", "commit", "-m", msg]` with
    no `-o` / `"--"` / paths.
    """
    import phase_5_implement as p5  # type: ignore
    from contracts import StepResult, WorkflowContext  # type: ignore

    # Fake test path — must appear in captured commit argv
    fake_test_path = "SYSTEM/cli/build/engine_py/tests/test_fake_7DDD9529.py"

    # Build a minimal WorkflowContext
    ctx = WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={"scratchpad_dir": str(tmp_path), "git_cwd": str(tmp_path)},
        question="task",
        session_id="s",
        persona="hal",
        framework=None,
        domain=None,
    )

    prev_data = {
        "red_test_paths": [fake_test_path],
        "cycle": 1,
        "spec_path": None,
    }
    prev = StepResult(status="ok", data=prev_data, duration_ms=0, step_name="write_red_tests")

    # Captured calls to _git_op_with_lock_retry
    captured_argvs: list[list[str]] = []

    def fake_git_op(argv, *, cwd, timeout=30):
        captured_argvs.append(list(argv))
        # Return a successful CompletedProcess-like object + outcome string
        result = MagicMock()
        result.returncode = 0
        result.stdout = ""
        result.stderr = ""
        return result, "ok"

    # Patch subprocess.run for the non-lock-retry calls
    fake_sha = "a" * 40

    def fake_subprocess_run(cmd, **kwargs):
        r = MagicMock()
        r.returncode = 0
        r.stdout = ""
        r.stderr = ""
        if "rev-parse" in cmd and "--git-dir" in cmd:
            r.stdout = str(tmp_path / ".git")
        elif "rev-parse" in cmd and "HEAD" in cmd:
            r.stdout = fake_sha
        elif "status" in cmd:
            r.stdout = f"?? {fake_test_path}\n"
        elif "check-ignore" in cmd:
            r.returncode = 1  # nothing is ignored
            r.stdout = ""
        elif "ls-files" in cmd:
            r.returncode = 0  # GH514(2): treat fake_test_path as tracked, not phantom
            r.stdout = f"{fake_test_path}\n"
        elif "diff" in cmd and "--cached" in cmd:
            r.returncode = 1  # 9EDB7588: staged changes present post-add → commit proceeds
        return r

    # Patch _derive_red_paths_via_git_diff to return our fake path
    with (
        patch.object(p5, "_git_op_with_lock_retry", side_effect=fake_git_op),
        patch("phase_5_implement.subprocess.run", side_effect=fake_subprocess_run),
        patch.object(p5, "_derive_red_paths_via_git_diff", return_value=[fake_test_path]),
        patch.object(p5, "_emit_safe"),
    ):
        p5._commit_red_tests(ctx, prev)

    # Find the commit argv (not the add argv)
    commit_argvs = [a for a in captured_argvs if "commit" in a]
    assert commit_argvs, (
        f"_git_op_with_lock_retry was never called with a 'commit' argv. "
        f"All captured argvs: {captured_argvs}"
    )

    commit_argv = commit_argvs[0]

    assert "-o" in commit_argv, (
        f"Expected '-o' in commit argv but got: {commit_argv}"
    )
    assert "--" in commit_argv, (
        f"Expected '--' path separator in commit argv but got: {commit_argv}"
    )
    assert fake_test_path in commit_argv, (
        f"Expected the test path {fake_test_path!r} in commit argv but got: {commit_argv}"
    )

    # Verify ordering: -o before -m before -- before paths
    idx_o = commit_argv.index("-o")
    idx_m = commit_argv.index("-m")
    idx_sep = commit_argv.index("--")
    idx_path = commit_argv.index(fake_test_path)
    assert idx_o < idx_m < idx_sep < idx_path, (
        f"Expected argv order -o < -m < -- < path, got indices "
        f"-o={idx_o} -m={idx_m} --={idx_sep} path={idx_path} in {commit_argv}"
    )


# ---------------------------------------------------------------------------
# AC6b — monkeypatch `_git_op_with_lock_retry` in `_commit_green_code`,
#         capture argv, assert `-o`, `--`, and `prod_paths` present.
# ---------------------------------------------------------------------------

def test_ac6_subprocess_argv_via_monkeypatch_commit_green(tmp_path):
    """AC6b — call `_commit_green_code` with a minimal fixture + monkeypatched
    `_git_op_with_lock_retry`; assert captured commit argv contains
    `-o`, `"--"`, and the expected production path.

    FAILs today because the real argv is `["git", "commit", "-m", msg]` with
    no `-o` / `"--"` / paths.
    """
    import phase_5_implement as p5  # type: ignore
    from contracts import StepResult, WorkflowContext  # type: ignore

    fake_prod_path = "SYSTEM/cli/build/engine_py/workflows/fake_prod_7DDD9529.py"
    fake_sha = "b" * 40

    ctx = WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={"scratchpad_dir": str(tmp_path), "git_cwd": str(tmp_path)},
        question="task",
        session_id="s",
        persona="hal",
        framework=None,
        domain=None,
    )

    # 4961254A: manifest drives commit; include the prod path in worker_written_paths
    prev_data = {
        "red_commit_sha": fake_sha,
        "cycle": 1,
        "worker_written_paths": [fake_prod_path],
        "manifest_source": "harness_tool_record",  # 4C03CCED Ship 1C
    }
    prev = StepResult(status="ok", data=prev_data, duration_ms=0, step_name="commit_red_tests")

    captured_argvs: list[list[str]] = []

    def fake_git_op(argv, *, cwd, timeout=30):
        captured_argvs.append(list(argv))
        result = MagicMock()
        result.returncode = 0
        result.stdout = fake_sha
        result.stderr = ""
        return result, "ok"

    # Materialize the prod path so git add succeeds
    import os as _os
    _full = _os.path.join(str(tmp_path), fake_prod_path)
    _os.makedirs(_os.path.dirname(_full), exist_ok=True)
    open(_full, "w").close()

    with (
        patch.object(p5, "_git_op_with_lock_retry", side_effect=fake_git_op),
        patch.object(p5, "_emit_safe"),
    ):
        p5._commit_green_code(ctx, prev)

    commit_argvs = [a for a in captured_argvs if "commit" in a]
    assert commit_argvs, (
        f"_git_op_with_lock_retry was never called with a 'commit' argv. "
        f"All captured argvs: {captured_argvs}"
    )

    commit_argv = commit_argvs[0]

    assert "-o" in commit_argv, (
        f"Expected '-o' in commit argv but got: {commit_argv}"
    )
    assert "--" in commit_argv, (
        f"Expected '--' path separator in commit argv but got: {commit_argv}"
    )
    assert fake_prod_path in commit_argv, (
        f"Expected production path {fake_prod_path!r} in commit argv but got: {commit_argv}"
    )

    idx_o = commit_argv.index("-o")
    idx_m = commit_argv.index("-m")
    idx_sep = commit_argv.index("--")
    idx_path = commit_argv.index(fake_prod_path)
    assert idx_o < idx_m < idx_sep < idx_path, (
        f"Expected argv order -o < -m < -- < path, got indices "
        f"-o={idx_o} -m={idx_m} --={idx_sep} path={idx_path} in {commit_argv}"
    )
