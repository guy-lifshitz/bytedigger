"""RED tests for 7B6A9AD1 — _commit_fix_code must write pre-fix-ref.txt BEFORE skip paths.

Contract change: hoist `pre-fix-ref.txt` write to immediately after SHA validation,
so it fires on ALL exit paths (including no_production_paths skip), not just success.

AC1 (test_pre_ref_written_on_no_production_paths_skip) MUST FAIL until GREEN hoists the write.
AC2-AC5 are expected to PASS against current code — they protect existing behavior.

Do NOT implement the fix here — RED-only file (7B6A9AD1).
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

HERE = Path(__file__).parent
ENGINE_ROOT = HERE.parent
sys.path.insert(0, str(ENGINE_ROOT))
sys.path.insert(0, str(ENGINE_ROOT / "lib"))
sys.path.insert(0, str(ENGINE_ROOT / "workflows"))

from phase_6_review import _commit_fix_code  # noqa: E402
from contracts import WorkflowContext  # noqa: E402
from lib.git_port import GitResult, set_default_git_read_factory, reset_default_git_read_factory  # noqa: E402


# ─── shared fixtures ──────────────────────────────────────────────────────────

_VALID_SHA = "a" * 40  # valid 40-char lowercase hex SHA


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
        question="Apply fix",
        session_id="test-7B6A9AD1",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_prev(pre_fix_sha=None, cycle: int = 2, **extra) -> MagicMock:
    prev = MagicMock()
    data: dict = {"cycle": cycle, **extra}
    if pre_fix_sha is not None:
        data["pre_fix_sha"] = pre_fix_sha
    prev.data = data
    return prev


# ═══════════════════════════════════════════════════════════════════════════════
# AC1 — EXPECTED TO FAIL against current code (RED signal for 7B6A9AD1)
# ═══════════════════════════════════════════════════════════════════════════════


def test_pre_ref_written_on_no_production_paths_skip(tmp_path: Path, monkeypatch) -> None:
    """AC1 (7B6A9AD1): pre-fix-ref.txt must exist even when no_production_paths skip fires.

    Current bug: the write lives after the no_production_paths guard (line 2297-2300),
    so this test FAILS until GREEN hoists it before the guard.
    """
    import phase_6_review

    monkeypatch.setattr(
        phase_6_review,
        "_emit_safe",
        lambda et, p, **kw: None,
    )
    # Mock git_diff_files to return empty list → triggers no_production_paths skip
    monkeypatch.setattr(
        phase_6_review,
        "git_diff_files",
        lambda sha, root, untracked=True, segment_filter=None: [],
    )

    ctx = _make_ctx(tmp_path)
    prev = _make_prev(pre_fix_sha=_VALID_SHA, cycle=2,
                      worker_written_paths=[], manifest_source="harness_tool_record")  # 4C03CCED Ship 1C

    result = _commit_fix_code(ctx, prev)

    # Skip path must still return ok with fix_commit_sha=None
    assert result.status == "ok", (
        f"AC1 FAIL: expected ok status on no_production_paths skip, got {result.status!r}"
    )
    assert result.data is not None and result.data.get("fix_commit_sha") is None, (
        f"AC1 FAIL: fix_commit_sha must be None on no_production_paths skip; "
        f"got {result.data.get('fix_commit_sha') if result.data else 'no data'!r}"
    )

    scratchpad = tmp_path / "scratch"
    ref_file = scratchpad / "integrity" / "pre-fix-ref.txt"

    assert ref_file.is_file(), (
        f"AC1 FAIL: pre-fix-ref.txt missing after no_production_paths skip path — "
        f"see spec 7B6A9AD1. The write must be hoisted before the skip guard. "
        f"scratchpad contents: {list(scratchpad.rglob('*'))}"
    )
    assert ref_file.read_text() == _VALID_SHA, (
        f"AC1 FAIL: pre-fix-ref.txt must contain the validated pre_fix_sha {_VALID_SHA!r} "
        f"(no trailing newline); got {ref_file.read_text()!r} — spec 7B6A9AD1"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC2 — Expected to PASS (protects existing behavior)
# ═══════════════════════════════════════════════════════════════════════════════


def test_pre_ref_written_on_success_path(tmp_path: Path, monkeypatch) -> None:
    """AC2 (7B6A9AD1): pre-fix-ref.txt still written on the full success path after a git commit.

    Existing behavior — must continue to pass both before and after GREEN lands.
    4961254A update: worker_written_paths drives commit path selection.
    """
    import phase_6_review

    post_sha = "b" * 40

    monkeypatch.setattr(
        phase_6_review,
        "_emit_safe",
        lambda et, p, **kw: None,
    )

    fake_proc = MagicMock()
    fake_proc.returncode = 0
    monkeypatch.setattr(
        phase_6_review,
        "_git_op_with_lock_retry",
        lambda cmd, cwd, timeout=30: (fake_proc, "ok"),
    )

    # D5D6A364 repoint: rev-parse now routes through git_port.git_read, not bounded_run.
    # Args-aware stub: check-ignore (from _filter_gitignored_paths) → rc=0/empty;
    # rev-parse → rc=0/post_sha so fix_commit_sha is populated and assertions hold.
    class _ArgAwareStub:
        def __call__(self, args, *, cwd=None, timeout=None, dir_=None):
            if args[:1] == ["check-ignore"]:
                return GitResult(returncode=0, stdout="", stderr="", timed_out=False)
            return GitResult(returncode=0, stdout=post_sha + "\n", stderr="", timed_out=False)

    try:
        set_default_git_read_factory(_ArgAwareStub)

        ctx = _make_ctx(tmp_path)
        # 4961254A: manifest drives commit path selection
        import os as _os
        _full = _os.path.join(ctx.org_config["git_cwd"], "src", "impl.py")
        _os.makedirs(_os.path.dirname(_full), exist_ok=True)
        open(_full, "w").close()
        prev = _make_prev(pre_fix_sha=_VALID_SHA, cycle=2, worker_written_paths=["src/impl.py"],
                          manifest_source="harness_tool_record")  # 4C03CCED Ship 1C

        result = _commit_fix_code(ctx, prev)
    finally:
        reset_default_git_read_factory()

    assert result.status == "ok", (
        f"AC2 FAIL: expected ok on success path, got {result.status!r}"
    )

    scratchpad = tmp_path / "scratch"
    ref_file = scratchpad / "integrity" / "pre-fix-ref.txt"

    assert ref_file.is_file(), (
        f"AC2 FAIL: pre-fix-ref.txt must exist after successful commit — spec 7B6A9AD1. "
        f"scratchpad contents: {list(scratchpad.rglob('*'))}"
    )
    assert ref_file.read_text() == _VALID_SHA, (
        f"AC2 FAIL: pre-fix-ref.txt must contain {_VALID_SHA!r}, got {ref_file.read_text()!r}"
    )

    sha_file = scratchpad / "integrity" / "fix-commit-sha.txt"
    assert sha_file.is_file(), (
        f"AC2 FAIL: fix-commit-sha.txt must also exist after successful commit"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC3 — Expected to PASS (protects existing behavior)
# ═══════════════════════════════════════════════════════════════════════════════


def test_pre_ref_not_written_without_scratchpad(tmp_path: Path, monkeypatch) -> None:
    """AC3 (7B6A9AD1): when cfg has no scratchpad_dir, function does not raise and no file is written.

    No scratchpad_dir → no write attempt → no FileNotFoundError.
    """
    import phase_6_review

    monkeypatch.setattr(
        phase_6_review,
        "_emit_safe",
        lambda et, p, **kw: None,
    )
    # Return empty list → skip path (simplest path that exercises the scratchpad guard)
    monkeypatch.setattr(
        phase_6_review,
        "git_diff_files",
        lambda sha, root, untracked=True, segment_filter=None: [],
    )

    # Build ctx WITHOUT scratchpad_dir
    git_cwd = tmp_path / "repo"
    git_cwd.mkdir(parents=True, exist_ok=True)
    org = {"git_cwd": str(git_cwd)}  # no scratchpad_dir key
    ctx = WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question="Apply fix",
        session_id="test-7B6A9AD1-no-scratch",
        persona="hal",
        framework=None,
        domain=None,
    )
    prev = _make_prev(pre_fix_sha=_VALID_SHA, cycle=1)

    # Must not raise regardless of which exit path fires
    try:
        result = _commit_fix_code(ctx, prev)
    except Exception as exc:
        raise AssertionError(
            f"AC3 FAIL: _commit_fix_code raised unexpectedly when scratchpad_dir absent: {exc!r}"
        ) from exc

    assert result.status in ("ok", "error"), (
        f"AC3 FAIL: unexpected result.status {result.status!r}"
    )
    # No file to assert on — just confirm no crash


# ═══════════════════════════════════════════════════════════════════════════════
# AC4 — Expected to PASS (protects existing behavior)
# ═══════════════════════════════════════════════════════════════════════════════


def test_pre_ref_not_written_on_missing_boundary(tmp_path: Path, monkeypatch) -> None:
    """AC4 (7B6A9AD1): when SHA validation fails and fallback returns empty, function returns
    E_MISSING_FIX_BOUNDARY and pre-fix-ref.txt is NOT written.
    """
    import phase_6_review

    monkeypatch.setattr(
        phase_6_review,
        "resolve_pre_phase_sha",
        lambda worktree_root: "",
    )
    monkeypatch.setattr(
        phase_6_review,
        "_emit_safe",
        lambda et, p, **kw: None,
    )

    ctx = _make_ctx(tmp_path)
    prev = _make_prev(cycle=1)  # no pre_fix_sha at all

    result = _commit_fix_code(ctx, prev)

    assert result.status == "error", (
        f"AC4 FAIL: expected error status when SHA boundary missing, got {result.status!r}"
    )
    assert result.error_code == "E_MISSING_FIX_BOUNDARY", (
        f"AC4 FAIL: expected E_MISSING_FIX_BOUNDARY, got {result.error_code!r} — spec 7B6A9AD1"
    )

    scratchpad = tmp_path / "scratch"
    ref_file = scratchpad / "integrity" / "pre-fix-ref.txt"
    assert not ref_file.exists(), (
        f"AC4 FAIL: pre-fix-ref.txt must NOT exist when SHA validation fails "
        f"(no valid SHA to write) — spec 7B6A9AD1. Found at {ref_file}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC5 — Expected to PASS (protects existing behavior)
# ═══════════════════════════════════════════════════════════════════════════════


def test_fix_commit_sha_only_on_success(tmp_path: Path, monkeypatch) -> None:
    """AC5 (7B6A9AD1): fix-commit-sha.txt must NOT be written on the no_production_paths skip path.

    The post-commit write (lines 2375-2378) must remain in place and only fire
    after a real git commit. Reuses the same no_production_paths fixture as AC1.
    """
    import phase_6_review

    monkeypatch.setattr(
        phase_6_review,
        "_emit_safe",
        lambda et, p, **kw: None,
    )
    # Empty diff → no_production_paths skip fires → no git commit
    monkeypatch.setattr(
        phase_6_review,
        "git_diff_files",
        lambda sha, root, untracked=True, segment_filter=None: [],
    )

    ctx = _make_ctx(tmp_path)
    prev = _make_prev(pre_fix_sha=_VALID_SHA, cycle=2,
                      worker_written_paths=[], manifest_source="harness_tool_record")  # 4C03CCED Ship 1C

    result = _commit_fix_code(ctx, prev)

    assert result.status == "ok", (
        f"AC5 FAIL: expected ok on no_production_paths skip, got {result.status!r}"
    )
    assert result.data is not None and result.data.get("fix_commit_sha") is None, (
        f"AC5 FAIL: fix_commit_sha must be None on skip path; "
        f"got {result.data.get('fix_commit_sha') if result.data else 'no data'!r}"
    )

    scratchpad = tmp_path / "scratch"
    sha_file = scratchpad / "integrity" / "fix-commit-sha.txt"
    assert not sha_file.exists(), (
        f"AC5 FAIL: fix-commit-sha.txt must NOT be written on no_production_paths skip "
        f"(no commit happened, no SHA to record) — spec 7B6A9AD1. Found at {sha_file}"
    )
