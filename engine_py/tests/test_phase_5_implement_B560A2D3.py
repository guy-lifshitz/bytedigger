"""B560A2D3 RED tests — Files-marker fallback via git status --porcelain.

Closes agreement B560A2D3: when LLM omits the `Files: [path1, path2]` marker
line, `_commit_red_tests` should derive red_test_paths from working-tree state
(git status --porcelain) rather than immediately returning E_RED_NO_MARKER.

Desired behavior:
1. `_derive_red_paths_from_git(git_cwd, pre_red_sha)` returns test-file paths
   matching: *.test.ts, *.test.tsx, *.test.js, test_*.py, *_test.py, or any
   path under __tests__/ or tests/ directories.
2. Status flags considered: ?? (untracked), M (modified), A (added), MM, etc.
3. Marker absent + fallback non-empty → warning emitted, proceed normally.
4. Marker absent + fallback empty → E_RED_NO_MARKER (existing behaviour).
5. Marker present (even empty []) → fallback skipped (existing paths for []).
6. pre_red_sha param accepted but ignored in v1.

These tests FAIL against current phase_5_implement.py (_derive_red_paths_from_git
does not exist; _commit_red_tests returns E_RED_NO_MARKER immediately on None).
They PASS once GREEN implements the helper and fallback branch.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "workflows"))


# ─── helpers ─────────────────────────────────────────────────────────────────


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)


def _commit_file(repo: Path, relpath: str, body: str, msg: str = "c") -> None:
    p = repo / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    subprocess.run(["git", "add", relpath], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", msg], cwd=repo, check=True)


def _minimal_repo(tmp_path: Path) -> Path:
    """Create a minimal git repo with one commit so commit_red_tests has a valid HEAD."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
    return repo


def _make_ctx(scratchpad: Path, *, question: str = "Add foo to bar", **org_extra):
    from contracts import WorkflowContext  # noqa: PLC0415

    org = {"scratchpad_dir": str(scratchpad), **org_extra}
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question=question,
        session_id="test-session",
        persona="hal",
        framework=None,
        domain=None,
    )


def _prev_no_marker(scratchpad: Path, repo: Path):
    """Simulate write_red_artifact output where LLM omitted Files: marker."""
    from contracts import StepResult  # noqa: PLC0415

    return StepResult(
        status="ok",
        data={
            "red_log_path": str(scratchpad / "tests/build-red-output.log"),
            "spec_path": str(scratchpad / "specs/build-spec.md"),
            "cycle": 1,
            "red_test_paths": None,  # marker absent
        },
        duration_ms=0,
        step_name="write_red_artifact",
    )


def _prev_with_marker(scratchpad: Path, repo: Path, paths: list[str]):
    """Simulate write_red_artifact output where LLM included Files: marker."""
    from contracts import StepResult  # noqa: PLC0415

    return StepResult(
        status="ok",
        data={
            "red_log_path": str(scratchpad / "tests/build-red-output.log"),
            "spec_path": str(scratchpad / "specs/build-spec.md"),
            "cycle": 1,
            "red_test_paths": paths,
        },
        duration_ms=0,
        step_name="write_red_artifact",
    )


def _write_untracked(repo: Path, relpath: str, body: str = "def test_x(): assert False\n") -> None:
    p = repo / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)


# Tests 1-4 (_derive_red_paths_from_git) deleted — γ cleanup 8.5 (A4461B8F) removed the symbol.

# ─── Test 5: marker absent + fallback empty → E_RED_NO_MARKER ────────────────


def test_commit_red_tests_returns_e_red_no_marker_when_fallback_empty(tmp_path):
    """Marker absent AND no test files in working tree → E_RED_NO_MARKER preserved.

    FAILS today in a trivially-passing way (already returns E_RED_NO_MARKER),
    but the error message must also hint at "no test files found" — the existing
    message only mentions the missing marker. Once GREEN enriches the message,
    this test locks that in. Also ensures error_code is NOT changed.
    """
    from phase_5_implement import _commit_red_tests  # noqa: PLC0415

    repo = _minimal_repo(tmp_path)
    scratchpad = tmp_path / "scratch"

    # No test files in working tree — only non-test untracked file
    _write_untracked(repo, "src/impl.py", "x = 1\n")

    ctx = _make_ctx(scratchpad, git_cwd=str(repo))
    prev = _prev_no_marker(scratchpad, repo)

    result = _commit_red_tests(ctx, prev)

    assert result.status == "error", (
        f"Expected status='error', got {result.status!r}"
    )
    assert getattr(result, "error_code", None) == "E_RED_NO_MARKER", (
        f"Expected error_code='E_RED_NO_MARKER', got {getattr(result, 'error_code', None)!r}"
    )
    err_msg = (getattr(result, "error", "") or "").lower()
    has_hint = "no test" in err_msg or "test files" in err_msg or "fallback" in err_msg
    assert has_hint, (
        f"Expected error message to hint at 'no test files found' after fallback attempt, "
        f"got: {getattr(result, 'error', '')!r}"
    )


# ─── Test 6: marker present → fallback skipped, no warning ───────────────────


def test_commit_red_tests_marker_present_skips_fallback(tmp_path, capsys):
    """When Files: marker is present, git-status fallback must not be invoked.

    Asserts:
    - result.status == "ok" (marker path works as before)
    - stderr does NOT contain "fallback" or "derived"

    FAILS today: _commit_red_tests does not have the fallback path yet, but once
    GREEN adds it, this test ensures the marker-present branch bypasses fallback.
    (Today it passes the status="ok" assertion but the fallback-skip is vacuously
    true since the fallback doesn't exist. Once GREEN adds fallback, the guard
    prevents accidental fallback invocation on the happy path.)
    """
    from phase_5_implement import _commit_red_tests  # noqa: PLC0415

    repo = _minimal_repo(tmp_path)
    scratchpad = tmp_path / "scratch"

    # Untracked test file (would be picked up by fallback if erroneously called)
    _write_untracked(repo, "tests/test_foo.py")

    ctx = _make_ctx(scratchpad, git_cwd=str(repo))
    # Marker IS present with explicit path list
    prev = _prev_with_marker(scratchpad, repo, ["tests/test_foo.py"])

    result = _commit_red_tests(ctx, prev)

    assert result.status == "ok", (
        f"Expected status='ok' with explicit marker paths, got status={result.status!r} "
        f"error_code={getattr(result, 'error_code', None)!r} "
        f"error={getattr(result, 'error', None)!r}"
    )
    captured = capsys.readouterr()
    combined = (captured.err + captured.out).lower()
    assert "fallback" not in combined and "derived" not in combined, (
        "Expected no fallback/derived warning when Files: marker is present, "
        f"but found it in output: {combined!r}"
    )
