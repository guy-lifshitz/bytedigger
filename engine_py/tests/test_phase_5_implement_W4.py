"""RED tests for Wave 4 — 5-fix batch for phase_5_implement.

AC1 (#2 / HIGH-4): _parse_verdict last-marker-wins (port from phase_6).
AC2 (#5 / MED-2): module docstring says "Steps (12)".
AC3 (#6 / MED-9): module docstring says "1200s RED" + mentions FEATURE/COMPLEX.
AC4 (HIGH-6): _commit_red_tests checks MERGE/REBASE state before git ops.
AC5 (HIGH-8): _parse_red_test_paths returns None (no marker) vs [] (empty marker) vs list.
             _commit_red_tests distinguishes None→E_RED_NO_MARKER, []→E_RED_EMPTY_FILES.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

from bytedigger_engine.contracts import StepResult, WorkflowContext  # noqa: E402


# ─── helpers ──────────────────────────────────────────────────────────────────


def make_ctx(scratchpad: Path, **org_extra) -> WorkflowContext:
    org = {"scratchpad_dir": str(scratchpad), **org_extra}
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question="Wave 4 test",
        session_id="test-w4",
        persona="hal",
        framework=None,
        domain=None,
    )


def make_commit_prev(scratchpad: Path, red_test_paths) -> StepResult:
    """Build a prev StepResult for _commit_red_tests with given red_test_paths value."""
    return StepResult(
        status="ok",
        data={
            "red_log_path": str(scratchpad / "tests/build-red-output.log"),
            "spec_path": str(scratchpad / "specs/build-spec.md"),
            "red_bytes_written": 100,
            "cycle": 1,
            "red_test_paths": red_test_paths,
        },
        duration_ms=0,
        step_name="write_red_artifact",
    )


def init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)


def commit_file(repo: Path, relpath: str, body: str, msg: str = "c") -> None:
    p = repo / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    subprocess.run(["git", "add", relpath], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", msg], cwd=repo, check=True)


def minimal_repo(tmp_path: Path) -> Path:
    """Create a minimal git repo with one commit so commit_red_tests has a valid HEAD."""
    repo = tmp_path / "repo"
    init_repo(repo)
    commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
    return repo


# ─── AC1: _parse_verdict last-marker-wins ─────────────────────────────────────


def test_ac1_bypass_not_pass():
    """AC1: 'BYPASS' must NOT match 'PASS' — VERDICT_UNKNOWN expected."""
    from bytedigger_engine.workflows.phase_5_implement import _parse_verdict, VERDICT_UNKNOWN
    result = _parse_verdict("Note: BYPASS the test")
    assert result == VERDICT_UNKNOWN, (
        f"Expected VERDICT_UNKNOWN for 'BYPASS the test', got {result!r}. "
        "endswith('PASS') heuristic matches 'BYPASS' — last-marker-wins fix needed."
    )


def test_ac1_bypass_before_real_pass():
    """AC1: BYPASS appears first but real VERDICT: PASS appears last — should be PASS."""
    from bytedigger_engine.workflows.phase_5_implement import _parse_verdict, VERDICT_PASS
    result = _parse_verdict("Note: BYPASS this\nVERDICT: PASS")
    assert result == VERDICT_PASS, (
        f"Expected VERDICT_PASS for 'BYPASS this\\nVERDICT: PASS', got {result!r}."
    )


def test_ac1_last_wins_pass():
    """AC1: VERDICT: FAIL first, VERDICT: PASS last → VERDICT_PASS."""
    from bytedigger_engine.workflows.phase_5_implement import _parse_verdict, VERDICT_PASS
    raw = "VERDICT: FAIL\n... fixed;\nVERDICT: PASS"
    result = _parse_verdict(raw)
    assert result == VERDICT_PASS, (
        f"Expected VERDICT_PASS (last marker wins), got {result!r}. "
        "Current impl: FAIL-wins heuristic blocks last-marker-wins."
    )


def test_ac1_last_wins_fail():
    """AC1: VERDICT: PASS first, VERDICT: FAIL last → VERDICT_FAIL."""
    from bytedigger_engine.workflows.phase_5_implement import _parse_verdict, VERDICT_FAIL
    raw = "VERDICT: PASS\n... wait actually\nVERDICT: FAIL"
    result = _parse_verdict(raw)
    assert result == VERDICT_FAIL, (
        f"Expected VERDICT_FAIL (last marker wins), got {result!r}."
    )


# ─── AC2: docstring step count ────────────────────────────────────────────────


def test_ac2_docstring_steps_12():
    """AC2: module docstring must contain 'Steps (12)'."""
    from bytedigger_engine.workflows import phase_5_implement
    doc = phase_5_implement.__doc__ or ""
    assert "Steps (12)" in doc, (
        f"Module docstring does not contain 'Steps (12)'. "
        f"Current docstring snippet: {doc[50:150]!r}. "
        "Fix: change 'Steps (11):' → 'Steps (12):'."
    )


# ─── AC3: timeout comment in docstring ────────────────────────────────────────


def test_ac3_docstring_timeout_1200s_red():
    """AC3: module docstring must mention '1200s RED' and 'FEATURE/COMPLEX'."""
    from bytedigger_engine.workflows import phase_5_implement
    doc = phase_5_implement.__doc__ or ""
    assert "1200s RED" in doc, (
        f"Module docstring does not contain '1200s RED'. "
        f"Current snippet: {doc[0:200]!r}. "
        "Fix: '600s RED' → '1200s RED (2400s for FEATURE/COMPLEX, see EDBDCDB2)'."
    )
    assert "FEATURE/COMPLEX" in doc, (
        f"Module docstring does not mention 'FEATURE/COMPLEX'. "
        f"Current snippet: {doc[0:200]!r}."
    )


# ─── AC4: _commit_red_tests checks MERGE/REBASE state ────────────────────────


@pytest.mark.parametrize(
    "relpath,is_dir",
    [
        ("MERGE_HEAD", False),          # `git merge` in progress
        ("REBASE_HEAD", False),         # generic rebase head pointer
        ("rebase-merge", True),         # `git rebase -i` (interactive)
        ("rebase-apply", True),         # `git rebase` (non-interactive / am)
        ("CHERRY_PICK_HEAD", False),    # `git cherry-pick` in progress
    ],
    ids=["MERGE_HEAD", "REBASE_HEAD", "rebase-merge_dir", "rebase-apply_dir", "CHERRY_PICK_HEAD"],
)
def test_ac4_bad_git_state_detected(tmp_path, relpath, is_dir):
    """AC4: any of MERGE/REBASE/CHERRY_PICK state markers → E_GIT_BAD_STATE.

    A naive MERGE_HEAD-only check leaves rebase/cherry-pick failure modes
    unguarded. All 5 in-progress operation markers must trigger the gate.
    """
    from bytedigger_engine.workflows.phase_5_implement import _commit_red_tests

    repo = minimal_repo(tmp_path)
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()
    # Create a test file so red_test_paths is non-empty (post-AC5 happy path).
    test_file = repo / "tests/test_foo.py"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text("def test_fail(): assert False\n")

    # Plant the in-progress marker in the git dir
    git_dir = repo / ".git"
    target = git_dir / relpath
    if is_dir:
        target.mkdir(parents=True, exist_ok=True)
        # rebase-merge / rebase-apply normally contain files; create a sentinel
        # so the dir is non-empty and unambiguous.
        (target / "head-name").write_text("refs/heads/main\n")
    else:
        target.write_text("aabbccdd" * 5)

    ctx = make_ctx(scratchpad, git_cwd=str(repo))
    prev = make_commit_prev(scratchpad, ["tests/test_foo.py"])

    result = _commit_red_tests(ctx, prev)
    assert result.status == "error", (
        f"Expected error status when .git/{relpath} exists, got {result.status!r}."
    )
    assert result.error_code == "E_GIT_BAD_STATE", (
        f"Expected E_GIT_BAD_STATE for .git/{relpath} ({'dir' if is_dir else 'file'}), "
        f"got {result.error_code!r}. "
        "Fix: check for MERGE_HEAD / REBASE_HEAD / rebase-merge/ / rebase-apply/ / "
        "CHERRY_PICK_HEAD before git operations."
    )


def test_ac4_clean_state_no_bad_state_error(tmp_path):
    """AC4: clean git state → no E_GIT_BAD_STATE (existing happy path intact)."""
    from bytedigger_engine.workflows.phase_5_implement import _commit_red_tests

    repo = minimal_repo(tmp_path)
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()

    # Write a test file the RED worker "produced"
    test_file = repo / "tests/test_foo.py"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text("def test_fail(): assert False\n")

    ctx = make_ctx(scratchpad, git_cwd=str(repo))
    prev = make_commit_prev(scratchpad, ["tests/test_foo.py"])

    result = _commit_red_tests(ctx, prev)
    # Clean state → should NOT return E_GIT_BAD_STATE (may succeed or fail for other reasons)
    assert result.error_code != "E_GIT_BAD_STATE", (
        f"Clean git state should not produce E_GIT_BAD_STATE, got {result.error_code!r}."
    )


# AC5 (_parse_red_test_paths) tests deleted — γ cleanup 8.5 (A4461B8F) removed the symbol.
