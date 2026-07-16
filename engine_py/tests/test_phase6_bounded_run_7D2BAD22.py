"""RED tests for 7D2BAD22: phase_6_review.py + phase_6_fix_integrity.py
subprocess.run → bounded_run migration.

Two test classes:
  TestSourceGrep7D2BAD22  — deterministic source-grep assertions (AC1–AC9)
  TestBehavior7D2BAD22    — monkeypatch bounded_run → rc=124 behavior assertions (AC10–AC12)

These tests FAIL against the current (pre-migration) code and PASS after GREEN migrates
the 11 subprocess.run sites to bounded_run and rewrites the except-TimeoutExpired arms.

Spec: SHARED/memory/Decisions/2026-06-16_7D2BAD22_phase6_bounded_run_spec.md
§1i (workflows.md): no singleton/time races — bounded_run is monkeypatched deterministically.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

# conftest-import-time singleton handles sys.path (§1q / 81F97F3D gate).
# Do NOT add sys.path.insert here.

import phase_6_review as _p6r
import phase_6_fix_integrity as _p6fi
from bounded_spawn import TIMEOUT_RETURNCODE
from lib import git_port
from lib.git_port import GitResult

# ─── paths to the production files under migration ───────────────────────────
_PROD_FILE_P6R = Path(_p6r.__file__)
_PROD_FILE_P6FI = Path(_p6fi.__file__)


# ─── helpers ─────────────────────────────────────────────────────────────────


def _source_p6r() -> str:
    return _PROD_FILE_P6R.read_text(encoding="utf-8")


def _common_source() -> str:
    import phase_workflows_common as _pwc  # noqa: PLC0415
    return Path(_pwc.__file__).read_text(encoding="utf-8")


def _source_p6fi() -> str:
    return _PROD_FILE_P6FI.read_text(encoding="utf-8")


def _fn_body(source: str, fn_name: str, prod_file: Path) -> str:
    """Extract the source text from 'def <fn_name>' to the next top-level def/class.

    Returns the slice of `source` starting at the 'def <fn_name>' line and
    ending just before the next top-level symbol (def/class at column 0).
    Raises AssertionError if the function is not found.
    """
    pattern = re.compile(r"^def " + re.escape(fn_name) + r"\b", re.MULTILINE)
    m = pattern.search(source)
    assert m is not None, f"function {fn_name!r} not found in {prod_file}"
    start = m.start()
    # Find next top-level def/class after start
    next_top = re.compile(r"\ndef |\nclass ", re.MULTILINE)
    nm = next_top.search(source, start + 1)
    end = nm.start() if nm else len(source)
    return source[start:end]


def _rc124() -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["fake"], returncode=TIMEOUT_RETURNCODE, stdout="", stderr=""
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Class 1 — Deterministic source-grep tests (PRIMARY RED signal)
# ═══════════════════════════════════════════════════════════════════════════════


class TestSourceGrep7D2BAD22:
    """Grep the production source to assert migration shape.

    Each test reads phase_6_review.py or phase_6_fix_integrity.py as text and
    asserts properties of specific function bodies.  These tests FAIL now
    (pre-migration) because the functions still call subprocess.run, not
    bounded_run.
    """

    # ── AC1: _verify_no_cross_tree_edits — 2 git_port.git_read calls, no subprocess.run ─

    def test_verify_no_cross_tree_edits_uses_git_port(self):
        """AC1: _verify_no_cross_tree_edits body (now in phase_workflows_common) must call
        git_port.git_read at least twice AND must not contain subprocess.run(.
        Reconciliation #261: phase_6→git_port routing (intentional flip, §5.2)."""
        import phase_workflows_common as _pwc  # noqa: PLC0415
        body = _fn_body(_common_source(), "_verify_no_cross_tree_edits", Path(_pwc.__file__))
        count = body.count("git_port.git_read(")
        assert count >= 2, (
            f"expected >=2 git_port.git_read( calls in _verify_no_cross_tree_edits, got {count}"
        )
        assert "subprocess.run(" not in body, (
            "_verify_no_cross_tree_edits still contains subprocess.run( — migration not done"
        )

    # ── AC2: _revert_cross_tree_modifications ────────────────────────────────

    def test_revert_cross_tree_modifications_uses_git_write_port(self):
        """Spec 8B9CAB8C (LSC-01 last bypass): _revert_cross_tree_modifications (now in
        phase_workflows_common) must route git checkout through git_write_port.op_capture,
        not bounded_run directly nor raw subprocess.run — closing the last raw-git write
        bypass (GH295).
        Reconciliation #261: repointed to common source; assertion updated for 8B9CAB8C."""
        import phase_workflows_common as _pwc  # noqa: PLC0415
        body = _fn_body(_common_source(), "_revert_cross_tree_modifications", Path(_pwc.__file__))
        assert "op_capture(" in body, (
            "_revert_cross_tree_modifications has no op_capture( call — migration not done"
        )
        assert "bounded_run(" not in body, (
            "_revert_cross_tree_modifications still contains bounded_run( — migration not done"
        )
        assert "subprocess.run(" not in body, (
            "_revert_cross_tree_modifications still contains subprocess.run( — migration not done"
        )

    # ── AC3: _git_op_with_lock_retry (phase_6_review) ────────────────────────

    def test_git_op_with_lock_retry_uses_bounded_run(self):
        """AC3: op_with_lock_retry body in git_write_port must call bounded_run, must
        not contain subprocess.run(, and must have returncode == 124 guard and 'timeout'.

        Repointed per §2.5 (5F06E98D): the retry body moved from phase_6_review to
        lib/git_write_port.py; coverage is preserved at the new home.
        FAILS today: git_write_port.py does not exist yet (ImportError inside body).
        """
        from lib import git_write_port  # noqa: PLC0415 — deferred per §1q/D1CF5FDF
        _gwp = Path(git_write_port.__file__).read_text(encoding="utf-8")
        assert "bounded_run(" in _gwp, (
            "git_write_port.py has no bounded_run( call — migration not done"
        )
        assert "subprocess.run(" not in _gwp, (
            "git_write_port.py still contains subprocess.run( — migration not done"
        )
        assert "returncode == 124" in _gwp or "== 124" in _gwp, (
            "git_write_port.py missing rc==124 guard after migration"
        )
        assert '"timeout"' in _gwp or "'timeout'" in _gwp, (
            "git_write_port.py missing 'timeout' return string"
        )

    # ── AC4: _filter_gitignored_paths ────────────────────────────────────────

    def test_filter_gitignored_paths_uses_git_port(self):
        """AC4: _filter_gitignored_paths body (now in phase_workflows_common) must call
        git_port.git_read AND must not contain subprocess.run(.
        Reconciliation #261: phase_6→git_port routing (intentional flip, §5.2)."""
        import phase_workflows_common as _pwc  # noqa: PLC0415
        body = _fn_body(_common_source(), "_filter_gitignored_paths", Path(_pwc.__file__))
        assert "git_port.git_read(" in body, (
            "_filter_gitignored_paths has no git_port.git_read( call — git_port routing not done"
        )
        assert "subprocess.run(" not in body, (
            "_filter_gitignored_paths still contains subprocess.run( — migration not done"
        )

    # ── AC5: _is_synthetic_test_env ──────────────────────────────────────────

    def test_is_synthetic_test_env_uses_bounded_run(self):
        """AC5: _is_synthetic_test_env body must call git_port.git_read, must not contain
        subprocess.run(. (Intentional flip §1s: migrated from bounded_run → git_port.git_read.)"""
        src = _source_p6r()
        body = _fn_body(src, "_is_synthetic_test_env", _PROD_FILE_P6R)
        assert "git_port.git_read(" in body, (
            "_is_synthetic_test_env has no git_port.git_read( call — git_port routing not done"
        )
        assert "subprocess.run(" not in body, (
            "_is_synthetic_test_env still contains subprocess.run( — migration not done"
        )

    # ── AC6: _commit_fix_code + _commit_fix_tests ────────────────────────────

    def test_commit_fix_code_uses_git_read(self):
        """AC6: _commit_fix_code body must call git_port.git_read AND must not contain
        subprocess.run(.

        Intentional flip §1s (slice 2 D5D6A364): rev-parse HEAD migrated
        bounded_run → git_port.git_read.  bounded_run( assertion inverted.
        """
        src = _source_p6r()
        body = _fn_body(src, "_commit_fix_code", _PROD_FILE_P6R)
        assert "git_port.git_read(" in body, (
            "_commit_fix_code has no git_port.git_read( call — migration to git_read not done"
        )
        assert "subprocess.run(" not in body, (
            "_commit_fix_code still has subprocess.run( — migration not done"
        )

    def test_commit_fix_tests_uses_git_read(self):
        """AC6: _commit_fix_tests body must call git_port.git_read AND must not contain
        subprocess.run(.

        Intentional flip §1s (slice 2 D5D6A364): rev-parse HEAD migrated
        bounded_run → git_port.git_read.  bounded_run( assertion inverted.
        """
        src = _source_p6r()
        body = _fn_body(src, "_commit_fix_tests", _PROD_FILE_P6R)
        assert "git_port.git_read(" in body, (
            "_commit_fix_tests has no git_port.git_read( call — migration to git_read not done"
        )
        assert "subprocess.run(" not in body, (
            "_commit_fix_tests still has subprocess.run( — migration not done"
        )

    # ── AC7: _resolve_fix_commit_sha + _resolve_pre_fix_sha (fix_integrity) ──

    def test_resolve_fix_commit_sha_uses_bounded_run(self):
        """AC7: _resolve_fix_commit_sha body must call git_port.git_read, must not contain
        subprocess.run(. (Intentional flip §1s: migrated from bounded_run → git_port.git_read.)"""
        src = _source_p6fi()
        body = _fn_body(src, "_resolve_fix_commit_sha", _PROD_FILE_P6FI)
        assert "git_port.git_read(" in body, (
            "_resolve_fix_commit_sha has no git_port.git_read( call — git_port routing not done"
        )
        assert "subprocess.run(" not in body, (
            "_resolve_fix_commit_sha still contains subprocess.run( — migration not done"
        )

    def test_resolve_pre_fix_sha_uses_bounded_run(self):
        """AC7: _resolve_pre_fix_sha body must call git_port.git_read, must not contain
        subprocess.run(. (Intentional flip §1s: migrated from bounded_run → git_port.git_read.)"""
        src = _source_p6fi()
        body = _fn_body(src, "_resolve_pre_fix_sha", _PROD_FILE_P6FI)
        assert "git_port.git_read(" in body, (
            "_resolve_pre_fix_sha has no git_port.git_read( call — git_port routing not done"
        )
        assert "subprocess.run(" not in body, (
            "_resolve_pre_fix_sha still contains subprocess.run( — migration not done"
        )

    # ── AC8: _build_fix_integrity_prompt ─────────────────────────────────────

    def test_build_fix_integrity_prompt_uses_git_read(self):
        """AC8: _build_fix_integrity_prompt body must call git_port.git_read, must not contain
        bounded_run( or subprocess.run(, must have timed_out guard, E_DIFF_TIMEOUT string,
        and must NOT contain 'except subprocess.TimeoutExpired' anywhere in the body.
        (Intentional flip 6E36AEB0 (slice 3b-iii): migrated from bounded_run → git_port.git_read.)"""
        src = _source_p6fi()
        body = _fn_body(src, "_build_fix_integrity_prompt", _PROD_FILE_P6FI)
        assert "git_port.git_read(" in body, (
            "_build_fix_integrity_prompt has no git_port.git_read( call — git_port routing not done"
        )
        assert "bounded_run(" not in body, (
            "_build_fix_integrity_prompt still contains bounded_run( — dead call not removed"
        )
        assert "subprocess.run(" not in body, (
            "_build_fix_integrity_prompt still contains subprocess.run( — migration not done"
        )
        assert "timed_out" in body, (
            "_build_fix_integrity_prompt missing timed_out guard after git_read migration"
        )
        assert "E_DIFF_TIMEOUT" in body, (
            "_build_fix_integrity_prompt missing E_DIFF_TIMEOUT error_code string"
        )
        assert "except subprocess.TimeoutExpired" not in body, (
            "_build_fix_integrity_prompt still has 'except subprocess.TimeoutExpired' — "
            "must be replaced by res.timed_out post-call check"
        )

    # ── AC9: both prod files have the import ─────────────────────────────────

    def test_phase6_review_has_bounded_run_import(self):
        """AC9: phase_6_review.py must contain 'from bounded_spawn import bounded_run'."""
        src = _source_p6r()
        assert "from bounded_spawn import bounded_run" in src, (
            "phase_6_review.py is missing 'from bounded_spawn import bounded_run' import"
        )

    def test_phase6_fix_integrity_has_git_port_import(self):
        """AC9: phase_6_fix_integrity.py must contain 'from lib import git_port' and must NOT
        contain the dead 'from bounded_spawn import bounded_run' import.
        (Intentional flip 6E36AEB0 dead-import removal.)"""
        src = _source_p6fi()
        assert "from lib import git_port" in src, (
            "phase_6_fix_integrity.py is missing 'from lib import git_port' import"
        )
        assert "from bounded_spawn import bounded_run" not in src, (
            "phase_6_fix_integrity.py still has dead 'from bounded_spawn import bounded_run' import"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Class 2 — Behavior tests via monkeypatch (§1y reachability)
# ═══════════════════════════════════════════════════════════════════════════════


class TestBehavior7D2BAD22:
    """Drive each migrated host function with bounded_run monkeypatched to rc=124.

    Pre-migration: these tests FAIL because `bounded_run` is not called — the
    original `subprocess.run` is called instead (or `bounded_run` doesn't exist
    as a module attribute, raising AttributeError during monkeypatch.setattr).

    Post-migration: `bounded_run` is called, returns rc=124, the new branch fires,
    and the assertion passes.

    §1l: bounded_run is the collaborator — we never patch the host functions themselves.
    §1i: no time-race; bounded_run is replaced deterministically.
    """

    # ── AC10: _is_synthetic_test_env returns True on rc=124 ──────────────────

    def test_is_synthetic_test_env_returns_true_on_rc124(self, tmp_path):
        """AC10: when git_read returns timed_out=True, _is_synthetic_test_env must return True
        (graceful — treats timeout same as git-missing / non-git-dir).

        Intentional flip (§1s): repointed from bounded_run monkeypatch → git_port factory swap.

        Fixture: cfg dict WITHOUT 'git_cwd' key (so the function doesn't early-return False),
        and tmp_path as git_cwd.
        Signature: _is_synthetic_test_env(cfg: dict, git_cwd: str) -> bool
        """
        try:
            git_port.set_default_git_read_factory(
                lambda: lambda *a, **k: GitResult(124, "", "", True)
            )
            cfg = {}  # no 'git_cwd' key → does not short-circuit to False
            result = _p6r._is_synthetic_test_env(cfg, str(tmp_path))
            assert result is True, (
                f"expected True when git_read returns timed_out=True (timeout → synthetic env), "
                f"got {result!r}"
            )
        finally:
            git_port.reset_default_git_read_factory()

    # ── AC11: _resolve_fix_commit_sha returns None on rc=124 ─────────────────

    def test_resolve_fix_commit_sha_returns_none_on_rc124(self, tmp_path):
        """AC11: when git_read returns timed_out=True, _resolve_fix_commit_sha must return None
        (no raise; graceful degradation).

        Intentional flip (§1s): repointed from bounded_run monkeypatch → git_port factory swap.

        Fixture: cfg without 'fix_commit_sha' (so no early-return from cfg cache),
        scratchpad=None (so no file cache path), git_cwd=tmp_path (non-git dir).
        Signature: _resolve_fix_commit_sha(cfg: dict, scratchpad: Path | None, git_cwd: Path | None) -> str | None
        """
        try:
            git_port.set_default_git_read_factory(
                lambda: lambda *a, **k: GitResult(124, "", "", True)
            )
            cfg = {}  # no fix_commit_sha cached
            result = _p6fi._resolve_fix_commit_sha(cfg, None, tmp_path)
            assert result is None, (
                f"expected None when git_read returns timed_out=True (timeout), got {result!r}"
            )
        finally:
            git_port.reset_default_git_read_factory()

    # ── AC12: _git_op_with_lock_retry returns (None, "timeout") on rc=124 ────

    def test_git_op_with_lock_retry_returns_none_timeout_on_rc124(self, monkeypatch, tmp_path):
        """AC12: when bounded_run returns rc=124, _git_op_with_lock_retry (via phase_6
        delegator → git_write_port port) must return (None, 'timeout').

        Repointed per §2.5 (5F06E98D): patch git_write_port.bounded_run (the new home
        of the retry logic); the call via _p6r._git_op_with_lock_retry routes through
        the port's bounded_run and the (None,'timeout') branch fires there.
        FAILS today: git_write_port.py does not exist yet (ImportError inside body).
        """
        from lib import git_write_port  # noqa: PLC0415 — deferred per §1q/D1CF5FDF
        monkeypatch.setattr(git_write_port, "bounded_run", lambda *a, **k: _rc124())
        result_tuple = _p6r._git_op_with_lock_retry(
            ["git", "status"], cwd=str(tmp_path), timeout=5
        )
        assert result_tuple == (None, "timeout"), (
            f"expected (None, 'timeout') when bounded_run returns rc=124, got {result_tuple!r}"
        )
