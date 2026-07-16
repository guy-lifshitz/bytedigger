"""RED tests for 3CE7007C R5 — _is_synthetic_test_env skip-guard consolidation
+ _resolve_pytest_argv venv probe (R4-secondary).

Contract (D8CB354F R5):
  - _is_synthetic_test_env(cfg, git_cwd) returns True iff git_cwd was NOT explicitly
    set in cfg AND git rev-parse --git-dir fails at that path (no real git repo).
  - Returns False (run) when cfg.get("git_cwd") is truthy (escape hatch).
  - Returns False (run) when git_cwd IS a real git repo (fixes the project-mode bug).
  - _commit_fix_code / _commit_fix_tests / _run_pytest_post_fix now emit BOTH
    project_mode_skipped(step=..., reason=no_git_repo) AND the per-step skip event
    with reason=no_git_repo (not git_cwd_outside_scratchpad).
  - _resolve_pytest_argv(git_cwd) prefers <git_cwd>/.venv/bin/pytest if executable,
    else falls back to ["python3", "-m", "pytest", "--tb=short", "-q"].

All 8 tests MUST FAIL until GREEN implements _is_synthetic_test_env and
_resolve_pytest_argv in phase_6_review.py and updates the three guard sites.

Do NOT implement the contract here — RED-only file.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).parent
ENGINE_ROOT = HERE.parent
sys.path.insert(0, str(ENGINE_ROOT))
sys.path.insert(0, str(ENGINE_ROOT / "lib"))
sys.path.insert(0, str(ENGINE_ROOT / "workflows"))

import phase_6_review as _p6  # noqa: E402
from phase_6_review import (  # noqa: E402
    _commit_fix_code,
    _commit_fix_tests,
    _run_pytest_post_fix,
)
from contracts import WorkflowContext  # noqa: E402


# ─── helpers ──────────────────────────────────────────────────────────────────


def _captured_emits(monkeypatch) -> list:
    """Attach a spy to _p6._emit_safe and return the captured list."""
    emitted: list = []

    def _fake_emit(event_type: str, payload: dict) -> None:
        emitted.append((event_type, payload))

    monkeypatch.setattr(_p6, "_emit_safe", _fake_emit)
    return emitted


def _make_skip_ctx(tmp_path: Path) -> WorkflowContext:
    """Build a minimal WorkflowContext with NO explicit git_cwd (triggers synthetic guard).

    scratchpad_dir is set so the old guard (scratchpad-existence check) would have
    fired; under R5 only the rev-parse probe matters.
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={"scratchpad_dir": str(scratchpad)},  # NO git_cwd key
        question="skip guard test",
        session_id="test-session-3CE7007C",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_prev(**extra) -> MagicMock:
    prev = MagicMock()
    prev.data = {"cycle": 1, "pre_fix_sha": "a" * 40, **extra}
    return prev


# ═══════════════════════════════════════════════════════════════════════════════
# AC1 — _is_synthetic_test_env returns False for a real git repo
# ═══════════════════════════════════════════════════════════════════════════════


def test_3ce7007c_ac1_real_git_repo_returns_false(tmp_path: Path) -> None:
    """AC1: _is_synthetic_test_env({}, str(repo)) where repo is git-init-ed
    must return exactly False (real repo present → run, not skip).

    Forcing function: exact `is False` check, input-derived from a real git init.
    pre-GREEN: FAIL — _is_synthetic_test_env does not exist yet (AttributeError).
    """
    repo = tmp_path / "real_repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=str(repo), check=True, capture_output=True)

    result = _p6._is_synthetic_test_env({}, str(repo))

    assert result is False, (
        f"Expected False for a real git repo; got {result!r}. "
        f"A real git repo must NOT be classified as synthetic."
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC2 — _is_synthetic_test_env returns True for a mkdir-only non-repo
# ═══════════════════════════════════════════════════════════════════════════════


def test_3ce7007c_ac2_nonrepo_dir_returns_true(tmp_path: Path) -> None:
    """AC2: _is_synthetic_test_env({}, str(nonrepo)) where nonrepo is mkdir-only
    (no git init) must return exactly True (no git repo → skip).

    Forcing function: exact `is True` check, input-derived.
    pre-GREEN: FAIL — _is_synthetic_test_env does not exist yet (AttributeError).
    """
    nonrepo = tmp_path / "not_a_repo"
    nonrepo.mkdir()

    result = _p6._is_synthetic_test_env({}, str(nonrepo))

    assert result is True, (
        f"Expected True for a non-repo dir; got {result!r}. "
        f"A dir with no git repo must be classified as synthetic."
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC3 — escape hatch: explicit git_cwd in cfg → False regardless of repo state
# ═══════════════════════════════════════════════════════════════════════════════


def test_3ce7007c_ac3_explicit_git_cwd_escape_hatch(tmp_path: Path) -> None:
    """AC3: _is_synthetic_test_env({"git_cwd": str(nonrepo)}, str(nonrepo)) must return
    False — explicit git_cwd in cfg is the escape hatch; rev-parse probe skipped.

    nonrepo is mkdir-only (would be True without the escape hatch). The fact that
    it's a non-repo dir proves the escape hatch fires before the probe.
    pre-GREEN: FAIL — _is_synthetic_test_env does not exist yet (AttributeError).
    """
    nonrepo = tmp_path / "explicit_but_nonrepo"
    nonrepo.mkdir()

    result = _p6._is_synthetic_test_env({"git_cwd": str(nonrepo)}, str(nonrepo))

    assert result is False, (
        f"Expected False when git_cwd is explicitly set (escape hatch); got {result!r}. "
        f"Explicit git_cwd must bypass the rev-parse probe."
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC4 — _commit_fix_code emits project_mode_skipped + fix_commit_skipped (no_git_repo)
# ═══════════════════════════════════════════════════════════════════════════════


def test_3ce7007c_ac4_commit_fix_code_skip_events(tmp_path: Path, monkeypatch) -> None:
    """AC4: _commit_fix_code with NO git_cwd in org_config and cwd=non-repo dir
    must emit project_mode_skipped(step=commit_fix_code, reason=no_git_repo) AND
    fix_commit_skipped(reason=no_git_repo); return status=ok, fix_commit_sha=None.

    §1i: non-repo dir pre-staged deterministically (mkdir, no git init).
    §1j: monkeypatch.chdir so defaulted git_cwd = non-repo path.
    pre-GREEN: FAIL — events not emitted / old reason string still in production.
    """
    emitted = _captured_emits(monkeypatch)

    nonrepo = tmp_path / "nonrepo_cwd"
    nonrepo.mkdir()
    monkeypatch.chdir(nonrepo)  # git_cwd defaults to Path.cwd() = nonrepo (non-repo)

    ctx = _make_skip_ctx(tmp_path)
    prev = _make_prev()

    result = _commit_fix_code(ctx, prev)

    assert result.status == "ok", (
        f"Expected status=ok on skip; got {result.status!r} ({getattr(result, 'error', None)!r})"
    )
    assert result.data is not None
    assert result.data.get("fix_commit_sha") is None, (
        f"fix_commit_sha must be None on skip; got {result.data.get('fix_commit_sha')!r}"
    )

    # Must emit the unified cycle-3 project_mode_skipped event
    pm_events = [
        (et, p) for et, p in emitted
        if et == "project_mode_skipped"
        and p.get("step") == "commit_fix_code"
        and p.get("reason") == "no_git_repo"
    ]
    assert pm_events, (
        f"Expected project_mode_skipped(step=commit_fix_code, reason=no_git_repo); "
        f"got: {emitted!r}"
    )

    # Must also emit the per-step skip event with updated reason
    step_events = [
        (et, p) for et, p in emitted
        if et == "fix_commit_skipped"
        and p.get("reason") == "no_git_repo"
    ]
    assert step_events, (
        f"Expected fix_commit_skipped(reason=no_git_repo); got: {emitted!r}. "
        f"Old reason 'git_cwd_outside_scratchpad' is no longer correct."
    )
    assert step_events[0][1].get("phase") == 6


# ═══════════════════════════════════════════════════════════════════════════════
# AC5 — _commit_fix_tests emits project_mode_skipped + fix_test_commit_skipped (no_git_repo)
# ═══════════════════════════════════════════════════════════════════════════════


def test_3ce7007c_ac5_commit_fix_tests_skip_events(tmp_path: Path, monkeypatch) -> None:
    """AC5: _commit_fix_tests with NO git_cwd and cwd=non-repo dir must emit
    project_mode_skipped(step=commit_fix_tests, reason=no_git_repo) AND
    fix_test_commit_skipped(reason=no_git_repo); return status=ok, fix_test_commit_sha=None.

    §1i: non-repo dir pre-staged deterministically.
    pre-GREEN: FAIL — events not emitted / old reason string still in production.
    """
    emitted = _captured_emits(monkeypatch)

    nonrepo = tmp_path / "nonrepo_cwd_tests"
    nonrepo.mkdir()
    monkeypatch.chdir(nonrepo)

    ctx = _make_skip_ctx(tmp_path)
    prev = _make_prev()

    result = _commit_fix_tests(ctx, prev)

    assert result.status == "ok", (
        f"Expected status=ok on skip; got {result.status!r} ({getattr(result, 'error', None)!r})"
    )
    assert result.data is not None
    assert result.data.get("fix_test_commit_sha") is None, (
        f"fix_test_commit_sha must be None on skip; got {result.data.get('fix_test_commit_sha')!r}"
    )

    pm_events = [
        (et, p) for et, p in emitted
        if et == "project_mode_skipped"
        and p.get("step") == "commit_fix_tests"
        and p.get("reason") == "no_git_repo"
    ]
    assert pm_events, (
        f"Expected project_mode_skipped(step=commit_fix_tests, reason=no_git_repo); "
        f"got: {emitted!r}"
    )

    step_events = [
        (et, p) for et, p in emitted
        if et == "fix_test_commit_skipped"
        and p.get("reason") == "no_git_repo"
    ]
    assert step_events, (
        f"Expected fix_test_commit_skipped(reason=no_git_repo); got: {emitted!r}. "
        f"Old reason 'git_cwd_outside_scratchpad' is no longer correct."
    )
    assert step_events[0][1].get("phase") == 6


# ═══════════════════════════════════════════════════════════════════════════════
# AC6 — _run_pytest_post_fix emits project_mode_skipped + post_fix_pytest_skipped (no_git_repo)
# ═══════════════════════════════════════════════════════════════════════════════


def test_3ce7007c_ac6_run_pytest_post_fix_skip_events(tmp_path: Path, monkeypatch) -> None:
    """AC6: _run_pytest_post_fix with NO git_cwd and cwd=non-repo dir must emit
    project_mode_skipped(step=run_pytest_post_fix, reason=no_git_repo) AND
    post_fix_pytest_skipped(reason=no_git_repo); return status=ok.

    §1i: non-repo dir pre-staged deterministically.
    pre-GREEN: FAIL — events not emitted / old reason string still in production.
    """
    emitted = _captured_emits(monkeypatch)

    nonrepo = tmp_path / "nonrepo_cwd_pytest"
    nonrepo.mkdir()
    monkeypatch.chdir(nonrepo)

    ctx = _make_skip_ctx(tmp_path)
    prev = _make_prev()

    result = _run_pytest_post_fix(ctx, prev)

    assert result.status == "ok", (
        f"Expected status=ok on skip; got {result.status!r} ({getattr(result, 'error', None)!r})"
    )

    pm_events = [
        (et, p) for et, p in emitted
        if et == "project_mode_skipped"
        and p.get("step") == "run_pytest_post_fix"
        and p.get("reason") == "no_git_repo"
    ]
    assert pm_events, (
        f"Expected project_mode_skipped(step=run_pytest_post_fix, reason=no_git_repo); "
        f"got: {emitted!r}"
    )

    step_events = [
        (et, p) for et, p in emitted
        if et == "post_fix_pytest_skipped"
        and p.get("reason") == "no_git_repo"
    ]
    assert step_events, (
        f"Expected post_fix_pytest_skipped(reason=no_git_repo); got: {emitted!r}. "
        f"Old reason 'git_cwd_outside_scratchpad' is no longer correct."
    )
    assert step_events[0][1].get("phase") == 6
    assert step_events[0][1].get("step") == "run_pytest_post_fix"


# ═══════════════════════════════════════════════════════════════════════════════
# AC7 — _resolve_pytest_argv falls back to python3 -m pytest when no venv
# ═══════════════════════════════════════════════════════════════════════════════


def test_3ce7007c_ac7_resolve_pytest_argv_no_venv_fallback(tmp_path: Path) -> None:
    """AC7: _resolve_pytest_argv(str(tmp)) with no .venv/bin/pytest present
    must return exactly ["python3", "-m", "pytest", "--tb=short", "-q"].

    tmp_path has no .venv or venv subdirectory.
    pre-GREEN: FAIL — _resolve_pytest_argv does not exist yet (AttributeError).
    """
    result = _p6._resolve_pytest_argv(str(tmp_path))

    assert result == ["python3", "-m", "pytest", "--tb=short", "-q"], (
        f"Expected fallback argv; got {result!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC8 — _resolve_pytest_argv prefers .venv/bin/pytest when executable
# ═══════════════════════════════════════════════════════════════════════════════


def test_3ce7007c_ac8_resolve_pytest_argv_venv_preferred(tmp_path: Path) -> None:
    """AC8: _resolve_pytest_argv(str(tmp)) with tmp/.venv/bin/pytest present and
    executable must return exactly [str(tmp/".venv"/"bin"/"pytest"), "--tb=short", "-q"].

    Forcing function: exact input-derived path check — anti-stub.
    §1j: stub written as a real file (not a signed system binary) → no macOS codesign issue.
    pre-GREEN: FAIL — _resolve_pytest_argv does not exist yet (AttributeError).
    """
    venv_bin = tmp_path / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    pytest_stub = venv_bin / "pytest"
    pytest_stub.write_text("#!/bin/sh\nexec python3 -m pytest \"$@\"\n", encoding="utf-8")
    os.chmod(pytest_stub, 0o755)

    expected_argv = [str(pytest_stub), "--tb=short", "-q"]
    result = _p6._resolve_pytest_argv(str(tmp_path))

    assert result == expected_argv, (
        f"Expected venv pytest argv {expected_argv!r}; got {result!r}. "
        f"Venv-local pytest should be preferred over system python3 -m pytest."
    )
