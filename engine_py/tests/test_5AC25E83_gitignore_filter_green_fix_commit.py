"""RED tests for 5AC25E83 — gitignore-filter missing in GREEN+FIX commit paths.

All 6 tests MUST FAIL at RED time because _filter_gitignored_paths does not yet
exist in phase_5_implement or phase_6_review.

Fail mechanism: each test body begins with
    from phase_X import _filter_gitignored_paths
which raises ImportError at RED → pytest reports ERROR (counts as FAIL).

Do NOT implement the contract here — RED-only file.

§1i note: no singleton/time-dependent resources used; N/A for §1i pre-staging.
D1CF5FDF rule: all imports of _filter_gitignored_paths are deferred (inside
test bodies), ensuring collection succeeds even when the symbol is absent.
sys.path setup is provided by conftest.py (§1q / 81F97F3D gate); no module-level
sys.path.insert here.
"""
from __future__ import annotations

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

# WorkflowContext is importable at collection time (symbol already exists).
from contracts import WorkflowContext  # noqa: E402

# ─── shared fixtures ──────────────────────────────────────────────────────────

_VALID_SHA = "a" * 40  # valid 40-char hex SHA for boundary fields
_POST_COMMIT_SHA = "b" * 40  # fake HEAD SHA returned after commit


def _make_ctx(tmp_path: Path, **org_extra) -> WorkflowContext:
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    git_cwd = tmp_path / "repo"
    git_cwd.mkdir(parents=True, exist_ok=True)
    org = {
        "scratchpad_dir": str(scratchpad),
        "git_cwd": str(git_cwd),
        **org_extra,
    }
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question="Fix the thing",
        session_id="test-5ac25e83",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_prev_green(red_commit_sha=None, cycle: int = 1, paths=None, **extra) -> MagicMock:
    prev = MagicMock()
    data: dict = {"cycle": cycle, **extra}
    if red_commit_sha is not None:
        data["red_commit_sha"] = red_commit_sha
    if paths is not None:
        data["worker_written_paths"] = paths
        data["manifest_source"] = "harness_tool_record"
    prev.data = data
    return prev


def _make_prev_fix(pre_fix_sha=None, cycle: int = 2, paths=None, **extra) -> MagicMock:
    prev = MagicMock()
    data: dict = {"cycle": cycle, **extra}
    if pre_fix_sha is not None:
        data["pre_fix_sha"] = pre_fix_sha
    if paths is not None:
        data["worker_written_paths"] = paths
        data["manifest_source"] = "harness_tool_record"
    prev.data = data
    return prev


def _ok_git_outcome() -> tuple:
    """A fake successful _git_op_with_lock_retry return value."""
    proc = MagicMock()
    proc.returncode = 0
    proc.stdout = ""
    proc.stderr = ""
    return proc, "ok"


# ─── AC1 ──────────────────────────────────────────────────────────────────────


def test_ac1_helper_importable_from_phase5() -> None:
    """_filter_gitignored_paths must be importable from phase_5_implement.

    At RED time: ImportError → test FAILS (ERROR in pytest).
    After GREEN: import succeeds, callable assert passes.
    """
    from phase_5_implement import _filter_gitignored_paths  # ImportError at RED → FAIL

    assert callable(_filter_gitignored_paths), (
        "_filter_gitignored_paths must be callable"
    )


# ─── AC2 ──────────────────────────────────────────────────────────────────────


def test_ac2_helper_importable_from_phase6() -> None:
    """_filter_gitignored_paths must be importable from phase_6_review.

    At RED time: ImportError → test FAILS (ERROR in pytest).
    After GREEN: import succeeds, callable assert passes.
    """
    from phase_6_review import _filter_gitignored_paths  # ImportError at RED → FAIL

    assert callable(_filter_gitignored_paths), (
        "_filter_gitignored_paths must be callable"
    )


# ─── AC3 ──────────────────────────────────────────────────────────────────────


def test_ac3_green_filters_gitignored_path_from_git_add(tmp_path: Path) -> None:
    """GREEN: manifest with a gitignored path → git add called with only non-ignored paths;
    commit_gitignored_paths_skipped event emitted.

    At RED time: ImportError from 'from phase_5_implement import _filter_gitignored_paths'
    → pytest ERROR → counts as FAIL.
    After GREEN: the function exists; patch.object succeeds; behavior asserted.
    """
    # Deferred import — raises ImportError at RED (D1CF5FDF rule)
    from phase_5_implement import _filter_gitignored_paths  # noqa: F401 — ImportError at RED → FAIL

    import phase_5_implement

    ctx = _make_ctx(tmp_path)
    prev = _make_prev_green(
        red_commit_sha=_VALID_SHA,
        cycle=1,
        paths=["src/foo.py", ".hal-build/manifest.md"],
    )

    captured_events: list[tuple] = []

    def _fake_emit(event_type, payload, severity="info"):
        captured_events.append((event_type, payload))

    def _fake_manifest(p):
        return (["src/foo.py", ".hal-build/manifest.md"], "harness_tool_record")

    # _filter_gitignored_paths correctly filters the .hal-build path
    def _fake_filter(paths, git_cwd):
        filtered = [p for p in paths if not p.startswith(".hal-build")]
        if len(filtered) < len(paths):
            phase_5_implement._emit_safe(
                "commit_gitignored_paths_skipped",
                {"paths": sorted(set(paths) - set(filtered)), "step": "commit_green_code", "phase": 5},
            )
        return filtered

    add_calls: list = []

    def _fake_git_op(cmd, cwd, timeout=30):
        if "add" in cmd:
            add_calls.append(list(cmd))
            return _ok_git_outcome()
        return _ok_git_outcome()

    with (
        patch.object(phase_5_implement, "_emit_safe", side_effect=_fake_emit),
        patch.object(phase_5_implement, "manifest_from_result", side_effect=_fake_manifest),
        patch.object(phase_5_implement, "_filter_gitignored_paths", side_effect=_fake_filter),
        patch.object(phase_5_implement, "_git_op_with_lock_retry", side_effect=_fake_git_op),
        patch.object(phase_5_implement, "_paths_have_staged_changes", return_value=False),
    ):
        phase_5_implement._commit_green_code(ctx, prev)

    # git add must be called with only the non-ignored path
    assert len(add_calls) == 1, f"Expected 1 git add call, got {len(add_calls)}: {add_calls}"
    added_paths = add_calls[0]
    assert "src/foo.py" in added_paths, (
        f"src/foo.py must be in git add args: {added_paths}"
    )
    assert ".hal-build/manifest.md" not in added_paths, (
        f".hal-build/manifest.md must be filtered from git add args: {added_paths}"
    )

    # commit_gitignored_paths_skipped event must be emitted
    skipped = [e for e in captured_events if e[0] == "commit_gitignored_paths_skipped"]
    assert len(skipped) >= 1, (
        f"Expected commit_gitignored_paths_skipped event; got: {[e[0] for e in captured_events]}"
    )


# ─── AC4 ──────────────────────────────────────────────────────────────────────


def test_ac4_green_degrades_on_check_ignore_failure(tmp_path: Path) -> None:
    """GREEN: when git check-ignore returns rc=2 (unexpected error), _commit_green_code
    proceeds with ALL paths (degraded mode, no crash).

    At RED time: ImportError from 'from phase_5_implement import _filter_gitignored_paths'
    → pytest ERROR → counts as FAIL.
    After GREEN: degraded path verified — all paths reach git add.
    """
    from phase_5_implement import _filter_gitignored_paths  # noqa: F401 — ImportError at RED → FAIL

    import phase_5_implement

    ctx = _make_ctx(tmp_path)
    prev = _make_prev_green(
        red_commit_sha=_VALID_SHA,
        cycle=1,
        paths=["src/foo.py", ".hal-build/manifest.md"],
    )

    def _fake_manifest(p):
        return (["src/foo.py", ".hal-build/manifest.md"], "harness_tool_record")

    # Degraded: check-ignore rc=2 → return all paths unchanged
    def _degraded_filter(paths, git_cwd):
        return list(paths)

    add_calls: list = []

    def _fake_git_op(cmd, cwd, timeout=30):
        if "add" in cmd:
            add_calls.append(list(cmd))
            return _ok_git_outcome()
        return _ok_git_outcome()

    with (
        patch.object(phase_5_implement, "_emit_safe", lambda et, p, **kw: None),
        patch.object(phase_5_implement, "manifest_from_result", side_effect=_fake_manifest),
        patch.object(phase_5_implement, "_filter_gitignored_paths", side_effect=_degraded_filter),
        patch.object(phase_5_implement, "_git_op_with_lock_retry", side_effect=_fake_git_op),
        patch.object(phase_5_implement, "_paths_have_staged_changes", return_value=False),
    ):
        result = phase_5_implement._commit_green_code(ctx, prev)

    # Must not crash (no error from filter)
    assert result.status in ("ok", "error"), f"Unexpected status: {result.status}"
    if result.status == "error":
        # Only acceptable errors are unrelated to gitignore filtering
        assert getattr(result, "error_code", None) not in ("E_FILTER_CRASH",), (
            f"Degraded filter must not crash _commit_green_code: {result.error_code}"
        )

    # git add must be called with ALL paths (both included in degraded mode)
    assert len(add_calls) == 1, f"Expected 1 git add call, got {len(add_calls)}"
    added_paths = add_calls[0]
    assert "src/foo.py" in added_paths, (
        f"src/foo.py must be in git add args: {added_paths}"
    )
    assert ".hal-build/manifest.md" in added_paths, (
        f".hal-build/manifest.md must be present (degraded mode): {added_paths}"
    )


# ─── AC5 ──────────────────────────────────────────────────────────────────────


def test_ac5_fix_filters_gitignored_path_from_git_add(tmp_path: Path) -> None:
    """FIX: manifest with a gitignored path → git add called with only non-ignored paths;
    commit_gitignored_paths_skipped event emitted.

    At RED time: ImportError from 'from phase_6_review import _filter_gitignored_paths'
    → pytest ERROR → counts as FAIL.
    After GREEN: behavior asserted.
    """
    from phase_6_review import _filter_gitignored_paths  # noqa: F401 — ImportError at RED → FAIL

    import phase_6_review

    ctx = _make_ctx(tmp_path)
    prev = _make_prev_fix(
        pre_fix_sha=_VALID_SHA,
        cycle=2,
        paths=["src/foo.py", ".hal-build/manifest.md"],
    )

    captured_events: list[tuple] = []

    def _fake_emit(event_type, payload, **kw):
        captured_events.append((event_type, payload))

    def _fake_manifest(p):
        return (["src/foo.py", ".hal-build/manifest.md"], "harness_tool_record")

    def _fake_filter(paths, git_cwd):
        filtered = [p for p in paths if not p.startswith(".hal-build")]
        if len(filtered) < len(paths):
            phase_6_review._emit_safe(
                "commit_gitignored_paths_skipped",
                {"paths": sorted(set(paths) - set(filtered)), "step": "commit_fix_code", "phase": 6},
            )
        return filtered

    add_calls: list = []

    def _fake_git_op(cmd, cwd, timeout=30):
        if "add" in cmd:
            add_calls.append(list(cmd))
            return _ok_git_outcome()
        # git commit
        proc = MagicMock()
        proc.returncode = 0
        proc.stdout = ""
        proc.stderr = ""
        return proc, "ok"

    # phase_6 uses subprocess.run directly for git rev-parse HEAD after commit
    def _fake_subprocess_run(cmd, **kwargs):
        proc = MagicMock(spec=subprocess.CompletedProcess)
        proc.returncode = 0
        proc.stdout = _POST_COMMIT_SHA + "\n"
        proc.stderr = ""
        return proc

    with (
        patch.object(phase_6_review, "_emit_safe", side_effect=_fake_emit),
        patch.object(phase_6_review, "manifest_from_result", side_effect=_fake_manifest),
        patch.object(phase_6_review, "_filter_gitignored_paths", side_effect=_fake_filter),
        patch.object(phase_6_review, "_git_op_with_lock_retry", side_effect=_fake_git_op),
        patch.object(phase_6_review, "subprocess", create=False) as mock_sp,
    ):
        mock_sp.run.side_effect = _fake_subprocess_run
        phase_6_review._commit_fix_code(ctx, prev)

    # git add must be called with only the non-ignored path
    assert len(add_calls) == 1, f"Expected 1 git add call, got {len(add_calls)}: {add_calls}"
    added_paths = add_calls[0]
    assert "src/foo.py" in added_paths, (
        f"src/foo.py must be in git add args: {added_paths}"
    )
    assert ".hal-build/manifest.md" not in added_paths, (
        f".hal-build/manifest.md must be filtered from git add args: {added_paths}"
    )

    # commit_gitignored_paths_skipped event must be emitted
    skipped = [e for e in captured_events if e[0] == "commit_gitignored_paths_skipped"]
    assert len(skipped) >= 1, (
        f"Expected commit_gitignored_paths_skipped event; got: {[e[0] for e in captured_events]}"
    )


# ─── AC6 ──────────────────────────────────────────────────────────────────────


def test_ac6_fix_degrades_on_check_ignore_failure(tmp_path: Path) -> None:
    """FIX: when git check-ignore returns rc=2, _commit_fix_code proceeds with ALL
    paths (degraded mode, no crash).

    At RED time: ImportError from 'from phase_6_review import _filter_gitignored_paths'
    → pytest ERROR → counts as FAIL.
    After GREEN: degraded path verified — all paths reach git add.
    """
    from phase_6_review import _filter_gitignored_paths  # noqa: F401 — ImportError at RED → FAIL

    import phase_6_review

    ctx = _make_ctx(tmp_path)
    prev = _make_prev_fix(
        pre_fix_sha=_VALID_SHA,
        cycle=2,
        paths=["src/foo.py", ".hal-build/manifest.md"],
    )

    def _fake_manifest(p):
        return (["src/foo.py", ".hal-build/manifest.md"], "harness_tool_record")

    def _degraded_filter(paths, git_cwd):
        # rc=2 path: return all paths unchanged
        return list(paths)

    add_calls: list = []

    def _fake_git_op(cmd, cwd, timeout=30):
        if "add" in cmd:
            add_calls.append(list(cmd))
            return _ok_git_outcome()
        return _ok_git_outcome()

    def _fake_subprocess_run(cmd, **kwargs):
        proc = MagicMock(spec=subprocess.CompletedProcess)
        proc.returncode = 0
        proc.stdout = _POST_COMMIT_SHA + "\n"
        proc.stderr = ""
        return proc

    with (
        patch.object(phase_6_review, "_emit_safe", lambda et, p, **kw: None),
        patch.object(phase_6_review, "manifest_from_result", side_effect=_fake_manifest),
        patch.object(phase_6_review, "_filter_gitignored_paths", side_effect=_degraded_filter),
        patch.object(phase_6_review, "_git_op_with_lock_retry", side_effect=_fake_git_op),
        patch.object(phase_6_review, "subprocess", create=False) as mock_sp,
    ):
        mock_sp.run.side_effect = _fake_subprocess_run
        result = phase_6_review._commit_fix_code(ctx, prev)

    # Must not crash
    assert result.status in ("ok", "error"), f"Unexpected status: {result.status}"
    if result.status == "error":
        assert getattr(result, "error_code", None) not in ("E_FILTER_CRASH",), (
            f"Degraded filter must not crash _commit_fix_code: {result.error_code}"
        )

    # git add must be called with ALL paths
    assert len(add_calls) == 1, f"Expected 1 git add call, got {len(add_calls)}"
    added_paths = add_calls[0]
    assert "src/foo.py" in added_paths, (
        f"src/foo.py must be in git add args: {added_paths}"
    )
    assert ".hal-build/manifest.md" in added_paths, (
        f".hal-build/manifest.md must be present in degraded git add: {added_paths}"
    )


# ─── AC-integration: real git repo, no patch.object on _filter_gitignored_paths ──────


def _init_git_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)


def test_ac_integration_real_gitignore(tmp_path: Path) -> None:
    """Integration: real _filter_gitignored_paths against a real git repo + .gitignore.

    No patch.object on _filter_gitignored_paths — git check-ignore subprocess
    runs for real. Asserts:
    - gitignored path (.hal-build/manifest.md) excluded from result
    - trackable path (src/foo.py) retained
    - commit_gitignored_paths_skipped event emitted (captured via _emit_safe patch)
    """
    import phase_5_implement
    import phase_workflows_common  # noqa: PLC0415
    from phase_5_implement import _filter_gitignored_paths

    repo = tmp_path / "repo"
    repo.mkdir()
    _init_git_repo(repo)
    (repo / ".gitignore").write_text(".hal-build/\n")
    subprocess.run(["git", "add", ".gitignore"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)

    captured: list[tuple] = []

    with patch.object(
        phase_workflows_common,
        "_emit_safe",
        side_effect=lambda et, payload, **kw: captured.append((et, payload)),
    ):
        result = _filter_gitignored_paths(
            [".hal-build/manifest.md", "src/foo.py"],
            str(repo),
        )

    assert result == ["src/foo.py"], (
        f"gitignored path must be filtered; got {result!r}"
    )
    skipped = [e for e in captured if e[0] == "commit_gitignored_paths_skipped"]
    assert skipped, (
        f"commit_gitignored_paths_skipped must be emitted; got events: {[e[0] for e in captured]}"
    )
    assert ".hal-build/manifest.md" in skipped[0][1].get("paths", []), (
        f"skipped paths payload must include the gitignored path; got {skipped[0][1]}"
    )
