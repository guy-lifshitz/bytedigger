"""RED tests for BFEC3E71: phase_5_implement.py subprocess.run → bounded_run migration.

Two test classes:
  TestSourceGrepBFEC3E71  — deterministic source-grep assertions (AC1/AC2/AC3/AC7/AC8)
  TestBehaviorBFEC3E71    — monkeypatch bounded_run → rc=124 behavior assertions (AC4/AC5/AC6)

These tests FAIL against the current (pre-migration) code and PASS after GREEN migrates
the 21 subprocess.run sites to bounded_run and rewrites the except-TimeoutExpired arms.

Spec: SHARED/memory/Decisions/2026-06-15_BFEC3E71_phase5_bounded_run_spec.md
§1i (workflows.md): no singleton/time races — bounded_run is monkeypatched deterministically.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any

import pytest

# conftest-import-time singleton handles sys.path (§1q / 81F97F3D gate).
# Do NOT add sys.path.insert here.

import phase_5_implement as _p5
from contracts import StepResult, WorkflowContext
from bounded_spawn import TIMEOUT_RETURNCODE
from lib.git_port import GitResult as _GitResult

# ─── path to the production file under migration ──────────────────────────────
_PROD_FILE = Path(_p5.__file__)


# ─── helpers ─────────────────────────────────────────────────────────────────


def _source() -> str:
    return _PROD_FILE.read_text(encoding="utf-8")


def _common_source() -> str:
    import phase_workflows_common as _pwc  # noqa: PLC0415
    return Path(_pwc.__file__).read_text(encoding="utf-8")


def _fn_body(source: str, fn_name: str) -> str:
    """Extract the source text from 'def <fn_name>' to the next top-level def/class.

    Returns the slice of `source` starting at the 'def <fn_name>' line and
    ending just before the next top-level symbol (def/class at column 0).
    Raises AssertionError if the function is not found.
    """
    pattern = re.compile(r"^def " + re.escape(fn_name) + r"\b", re.MULTILINE)
    m = pattern.search(source)
    assert m is not None, f"function {fn_name!r} not found in {_PROD_FILE}"
    start = m.start()
    # Find next top-level def/class after start
    next_top = re.compile(r"\ndef |\nclass ", re.MULTILINE)
    nm = next_top.search(source, start + 1)
    end = nm.start() if nm else len(source)
    return source[start:end]


def _make_ctx(git_cwd: str = "/tmp", **org_extra: Any) -> WorkflowContext:
    org = {"scratchpad_dir": "/tmp/scratch", **org_extra}
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question="test",
        session_id="test-bfec3e71",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_prev(data: dict) -> StepResult:
    return StepResult(
        status="ok",
        data=data,
        duration_ms=0,
        step_name="prev",
    )


def _rc124() -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(
        args=["fake"], returncode=TIMEOUT_RETURNCODE, stdout="", stderr=""
    )


# ═══════════════════════════════════════════════════════════════════════════════
# Class 1 — Deterministic source-grep tests (PRIMARY RED signal)
# ═══════════════════════════════════════════════════════════════════════════════


class TestSourceGrepBFEC3E71:
    """Grep the production source to assert migration shape.

    Each test reads phase_5_implement.py as text and asserts properties of
    specific function bodies.  These tests FAIL now (pre-migration) because the
    functions still call subprocess.run, not bounded_run.
    """

    # ── AC1/AC2: migrated functions call bounded_run, NOT subprocess.run ──────

    def test_git_op_with_lock_retry_uses_bounded_run(self):
        """AC1/AC2 Bucket C: op_with_lock_retry body in git_write_port must call
        bounded_run and must not contain subprocess.run(.

        Repointed per §2.5 (5F06E98D): the retry body moved from phase_5_implement
        to lib/git_write_port.py; coverage is preserved at the new home.
        FAILS today: git_write_port.py does not exist yet (ImportError inside body).
        """
        from lib import git_write_port  # noqa: PLC0415 — deferred per §1q/D1CF5FDF
        _gwp = Path(git_write_port.__file__).read_text(encoding="utf-8")
        assert "bounded_run(" in _gwp, (
            "git_write_port.py has no bounded_run( call — migration not done"
        )
        assert "subprocess.run(" not in _gwp, (
            "git_write_port.py must not contain subprocess.run( after migration"
        )

    def test_verify_no_cross_tree_edits_uses_git_port(self):
        """AC1/AC2 Bucket B: _verify_no_cross_tree_edits body must call git_port.git_read (both sites)."""
        body = _fn_body(_common_source(), "_verify_no_cross_tree_edits")
        count = body.count("git_port.git_read(")
        assert count >= 2, (
            f"expected >=2 git_port.git_read( calls in _verify_no_cross_tree_edits, got {count}"
        )
        assert "subprocess.run(" not in body

    def test_revert_cross_tree_modifications_uses_git_write_port(self):
        """Spec 8B9CAB8C (LSC-01 last bypass): _revert_cross_tree_modifications must
        route git checkout through git_write_port.op_capture, not bounded_run directly
        nor raw subprocess.run — closing the last raw-git write bypass (GH295)."""
        body = _fn_body(_common_source(), "_revert_cross_tree_modifications")
        assert "op_capture(" in body
        assert "bounded_run(" not in body
        assert "subprocess.run(" not in body

    def test_build_red_commit_message_uses_git_port(self):
        """AC1/AC2 Bucket B: _build_red_commit_message body must call git_port.git_read."""
        body = _fn_body(_source(), "_build_red_commit_message")
        assert "git_port.git_read(" in body, (
            "_build_red_commit_message git read not routed through git_port.git_read"
        )
        assert "subprocess.run(" not in body

    def test_main_checkout_root_uses_git_port(self):
        """AC1/AC2 Bucket B: _main_checkout_root body must call git_port.git_read."""
        body = _fn_body(_source(), "_main_checkout_root")
        assert "git_port.git_read(" in body, (
            "_main_checkout_root git read not routed through git_port.git_read"
        )
        assert "subprocess.run(" not in body

    def test_verify_red_fails_mechanically_uses_bounded_run(self):
        """AC1/AC2 Bucket B: the red-group run body must call bounded_run.

        Repointed per GH1114: the run/exception body moved from
        _verify_red_fails_mechanically to the _run_red_group helper (radon
        decompose); coverage preserved at the new home.
        """
        body = _fn_body(_source(), "_run_red_group")
        assert "bounded_run(" in body
        assert "subprocess.run(" not in body

    def test_verify_red_lint_rules_uses_bounded_run(self):
        """AC1/AC2 Bucket B: _verify_red_lint_rules body must call bounded_run."""
        body = _fn_body(_source(), "_verify_red_lint_rules")
        assert "bounded_run(" in body
        assert "subprocess.run(" not in body

    def test_compute_baseline_typecheck_count_uses_bounded_run(self):
        """AC1/AC2 Bucket B: _compute_baseline_typecheck_count body must call bounded_run."""
        body = _fn_body(_source(), "_compute_baseline_typecheck_count")
        assert "bounded_run(" in body
        assert "subprocess.run(" not in body

    def test_verify_green_lint_rules_uses_bounded_run(self):
        """AC1/AC2 Bucket B: _verify_green_lint_rules body must call bounded_run."""
        body = _fn_body(_source(), "_verify_green_lint_rules")
        assert "bounded_run(" in body
        assert "subprocess.run(" not in body

    def test_verify_green_typecheck_uses_bounded_run(self):
        """AC1/AC2 Bucket B: _verify_green_typecheck body must call bounded_run."""
        body = _fn_body(_source(), "_verify_green_typecheck")
        assert "bounded_run(" in body
        assert "subprocess.run(" not in body

    def test_check_red_executable_uses_bounded_run(self):
        """AC1/AC2 Bucket B: _check_red_executable body must call bounded_run."""
        body = _fn_body(_source(), "_check_red_executable")
        assert "bounded_run(" in body
        assert "subprocess.run(" not in body

    # ── AC3: no except clause retains subprocess.TimeoutExpired ───────────────

    def test_git_op_with_lock_retry_no_timeout_expired_except(self):
        """AC3 Bucket C: _git_op_with_lock_retry must not catch subprocess.TimeoutExpired."""
        body = _fn_body(_common_source(), "_git_op_with_lock_retry")
        assert "TimeoutExpired" not in body, (
            "_git_op_with_lock_retry still has except subprocess.TimeoutExpired arm"
        )

    def test_verify_no_cross_tree_edits_no_timeout_expired_except(self):
        """AC3 Bucket B: _verify_no_cross_tree_edits must not catch subprocess.TimeoutExpired
        but must still catch FileNotFoundError and OSError."""
        body = _fn_body(_common_source(), "_verify_no_cross_tree_edits")
        assert "TimeoutExpired" not in body
        assert "FileNotFoundError" in body
        assert "OSError" in body

    def test_revert_cross_tree_modifications_no_timeout_expired_except(self):
        """AC3 Bucket B: _revert_cross_tree_modifications must not catch TimeoutExpired
        but must still catch FileNotFoundError and OSError."""
        body = _fn_body(_common_source(), "_revert_cross_tree_modifications")
        assert "TimeoutExpired" not in body
        assert "FileNotFoundError" in body
        assert "OSError" in body

    def test_build_red_commit_message_no_timeout_expired_except(self):
        """AC3 Bucket B: _build_red_commit_message must not catch TimeoutExpired
        but must still catch OSError."""
        body = _fn_body(_source(), "_build_red_commit_message")
        assert "TimeoutExpired" not in body
        assert "OSError" in body

    def test_main_checkout_root_no_timeout_expired_except(self):
        """AC3 Bucket B: _main_checkout_root must not catch TimeoutExpired
        but must still catch CalledProcessError, FileNotFoundError, OSError, ValueError."""
        body = _fn_body(_source(), "_main_checkout_root")
        assert "TimeoutExpired" not in body
        assert "CalledProcessError" in body
        assert "FileNotFoundError" in body
        assert "OSError" in body

    def test_verify_red_fails_mechanically_no_timeout_expired_except(self):
        """AC3 Bucket B: the red-group run body must not catch TimeoutExpired
        but must still catch FileNotFoundError.

        Repointed per GH1114: the run/exception body moved from
        _verify_red_fails_mechanically to the _run_red_group helper (radon
        decompose); coverage preserved at the new home.
        """
        body = _fn_body(_source(), "_run_red_group")
        assert "TimeoutExpired" not in body
        assert "FileNotFoundError" in body

    def test_verify_red_lint_rules_no_timeout_expired_except(self):
        """AC3 Bucket B: _verify_red_lint_rules must not catch TimeoutExpired
        but must still catch FileNotFoundError."""
        body = _fn_body(_source(), "_verify_red_lint_rules")
        assert "TimeoutExpired" not in body
        assert "FileNotFoundError" in body

    def test_verify_green_lint_rules_no_timeout_expired_except(self):
        """AC3 Bucket B: _verify_green_lint_rules must not catch TimeoutExpired
        but must still catch FileNotFoundError."""
        body = _fn_body(_source(), "_verify_green_lint_rules")
        assert "TimeoutExpired" not in body
        assert "FileNotFoundError" in body

    def test_verify_green_typecheck_no_timeout_expired_except(self):
        """AC3 Bucket B: _verify_green_typecheck must not catch TimeoutExpired
        but must still catch FileNotFoundError."""
        body = _fn_body(_source(), "_verify_green_typecheck")
        assert "TimeoutExpired" not in body
        assert "FileNotFoundError" in body

    # ── AC7: Bucket A sites have downstream returncode guards ─────────────────

    def test_bucket_a_paths_have_staged_changes_has_returncode_guard(self):
        """AC7 Bucket A (site 757): _paths_have_staged_changes checks r.returncode."""
        body = _fn_body(_common_source(), "_paths_have_staged_changes")
        assert "returncode" in body

    def test_bucket_a_derive_green_paths_has_returncode_guard(self):
        """AC7 Bucket A (site 783): _derive_green_paths_from_git checks result.returncode."""
        body = _fn_body(_source(), "_derive_green_paths_from_git")
        assert "returncode" in body

    def test_bucket_a_filter_gitignored_has_returncode_guard(self):
        """AC7 Bucket A (site 1223): _filter_gitignored_paths checks ci.returncode."""
        body = _fn_body(_common_source(), "_filter_gitignored_paths")
        assert "returncode" in body

    def test_bucket_a_finding_in_diff_hunks_has_returncode_guard(self):
        """AC7 Bucket A (site 2562): _finding_in_diff_hunks checks result.returncode."""
        body = _fn_body(_source(), "_finding_in_diff_hunks")
        assert "returncode" in body

    def test_bucket_a_get_diff_added_files_has_returncode_guard(self):
        """AC7 Bucket A (sites 2509/2522/2535): _get_diff_added_files checks returncode."""
        body = _fn_body(_source(), "_get_diff_added_files")
        # All three result variables must have a returncode check
        assert body.count("returncode") >= 3, (
            "expected >=3 returncode checks in _get_diff_added_files (one per subprocess call)"
        )

    def test_bucket_a_reclassify_resolve_frozen_pre_red_sha_has_rc124_guard(self):
        """AC7 Bucket A→B reclassification (site 726): _resolve_frozen_pre_red_sha reads
        result.stdout with NO prior returncode guard — after migration it MUST have an
        explicit rc==124 guard (reads stdout without guard = rc=124 returns empty string
        silently; needs controlled failure path)."""
        body = _fn_body(_source(), "_resolve_frozen_pre_red_sha")
        # Post-migration: must have a returncode==124 or returncode != 0 guard
        assert "returncode" in body, (
            "_resolve_frozen_pre_red_sha has no returncode guard — "
            "site 726 reclassified to Bucket B: reads result.stdout with no rc check"
        )

    # ── AC8: no second magic timeout constant ─────────────────────────────────

    def test_no_new_module_level_timeout_constant(self):
        """AC8: no new module-level constant for the 124 sentinel — comparisons use literal 124."""
        src = _source()
        # Must not introduce a second named constant for timeout returncode
        assert "TIMEOUT_RC" not in src
        assert "TIMEOUT_CODE" not in src
        assert "TIMEOUT_RETURN_CODE" not in src
        # The wrapper constant name is TIMEOUT_RETURNCODE in bounded_spawn.py (already imported)
        # The migrated arms compare against literal 124 — assert at least one such comparison
        # exists in each of the Bucket B/C function bodies after migration.
        # Repointed per §2.5 (5F06E98D): retry body moved to git_write_port.py;
        # read op_with_lock_retry body from the port's new home.
        # FAILS today: git_write_port.py does not exist yet (ImportError inside body).
        from lib import git_write_port  # noqa: PLC0415 — deferred per §1q/D1CF5FDF
        _gwp_src = Path(git_write_port.__file__).read_text(encoding="utf-8")
        assert "== 124" in _gwp_src or "returncode == 124" in _gwp_src, (
            "git_write_port.py missing rc==124 comparison after migration"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Class 2 — Behavior tests via monkeypatch (§1y reachability)
# ═══════════════════════════════════════════════════════════════════════════════


class TestBehaviorBFEC3E71:
    """Drive each migrated host function with bounded_run monkeypatched to rc=124.

    Pre-migration: these tests FAIL because `bounded_run` is not called — the
    original `subprocess.run` is called instead, and the rc==124 branch never fires.

    Post-migration: `bounded_run` is called, returns rc=124, the new branch fires,
    and the assertion passes.

    §1l: bounded_run is the collaborator — we never patch the host functions themselves.
    §1i: no time-race; bounded_run is replaced deterministically.
    """

    # ── AC4: Bucket C — _git_op_with_lock_retry returns (None, "timeout") ────

    def test_git_op_with_lock_retry_returns_none_timeout_on_rc124(self, monkeypatch, tmp_path):
        """AC4 Bucket C: when bounded_run returns rc=124, _git_op_with_lock_retry
        (via phase_5 delegator → git_write_port port) returns (None, 'timeout').

        Repointed per §2.5 (5F06E98D): patch git_write_port.bounded_run (the new home
        of the retry logic); the call via _p5._git_op_with_lock_retry routes through
        the port's bounded_run and the (None,'timeout') branch fires there.
        FAILS today: git_write_port.py does not exist yet (ImportError inside body).
        """
        from lib import git_write_port  # noqa: PLC0415 — deferred per §1q/D1CF5FDF
        monkeypatch.setattr(git_write_port, "bounded_run", lambda *a, **kw: _rc124())
        result_tuple = _p5._git_op_with_lock_retry(
            ["git", "status"], cwd=str(tmp_path), timeout=30
        )
        assert result_tuple == (None, "timeout"), (
            f"expected (None, 'timeout'), got {result_tuple!r}"
        )

    # ── AC5: Bucket B StepResult error_code preservation ─────────────────────

    def test_verify_red_fails_mechanically_pytest_timeout(self, monkeypatch, tmp_path):
        """AC5 Bucket B (site 1627): when bounded_run returns rc=124 for a .py test path,
        _verify_red_fails_mechanically returns error_code='E_RED_PYTEST_TIMEOUT'."""
        # Patch subprocess.run so pre-migration code returns rc=0 quickly (deterministic).
        # Pre-migration: function sees rc=0 → passing_groups → returns E_RED_NOT_FAILING,
        #   NOT E_RED_PYTEST_TIMEOUT → assertion FAILS (RED).
        # Post-migration: function calls bounded_run (monkeypatched rc=124) → timeout branch →
        #   returns E_RED_PYTEST_TIMEOUT → assertion PASSES.
        ok_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        monkeypatch.setattr(_p5.subprocess, "run", lambda *a, **kw: ok_result)
        monkeypatch.setattr(_p5, "bounded_run", lambda *a, **kw: _rc124())

        # Stub out run_test_command (telemetry only) to avoid real subprocess in disk_truth.
        from dataclasses import make_dataclass
        FakeTestResult = make_dataclass("FakeTestResult", ["exit_code", "n_passed", "n_failed"])
        monkeypatch.setattr(_p5, "run_test_command", lambda *a, **kw: FakeTestResult(0, 1, 0))

        test_py = str(tmp_path / "test_example.py")
        ctx = _make_ctx(git_cwd=str(tmp_path))
        prev = _make_prev({"red_test_paths": [test_py]})
        result = _p5._verify_red_fails_mechanically(ctx, prev)
        assert result.error_code == "E_RED_PYTEST_TIMEOUT", (
            f"expected E_RED_PYTEST_TIMEOUT, got error_code={result.error_code!r}, "
            f"status={result.status!r}"
        )

    def test_verify_red_fails_mechanically_ts_runner_timeout(self, monkeypatch, tmp_path):
        """AC5 Bucket B (site 1627): when bounded_run returns rc=124 for a .ts test path,
        _verify_red_fails_mechanically returns error_code='E_RED_TEST_RUNNER_TIMEOUT'."""
        ok_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        monkeypatch.setattr(_p5.subprocess, "run", lambda *a, **kw: ok_result)
        monkeypatch.setattr(_p5, "bounded_run", lambda *a, **kw: _rc124())

        from dataclasses import make_dataclass
        FakeTestResult = make_dataclass("FakeTestResult", ["exit_code", "n_passed", "n_failed"])
        monkeypatch.setattr(_p5, "run_test_command", lambda *a, **kw: FakeTestResult(0, 1, 0))

        test_ts = str(tmp_path / "test_example.ts")
        ctx = _make_ctx(git_cwd=str(tmp_path))
        prev = _make_prev({"red_test_paths": [test_ts]})
        result = _p5._verify_red_fails_mechanically(ctx, prev)
        assert result.error_code == "E_RED_TEST_RUNNER_TIMEOUT", (
            f"expected E_RED_TEST_RUNNER_TIMEOUT, got error_code={result.error_code!r}"
        )

    def test_verify_red_lint_rules_timeout(self, monkeypatch, tmp_path):
        """AC5 Bucket B (site 1816): when bounded_run returns rc=124 for semgrep,
        _verify_red_lint_rules returns error_code='E_RED_LINT_TIMEOUT'."""
        import shutil as _shutil
        # Patch shutil.which so semgrep appears present (bypass E_RED_LINT_SEMGREP_MISSING)
        monkeypatch.setattr(_p5.shutil, "which", lambda name: "/usr/local/bin/semgrep" if name == "semgrep" else _shutil.which(name))
        # Create a dummy rules.yml so the rules_path.is_file() check passes
        rules_dir = Path(_p5.__file__).parent.parent / "scripts" / "red_lint"
        rules_dir.mkdir(parents=True, exist_ok=True)
        rules_file = rules_dir / "rules.yml"
        if not rules_file.exists():
            rules_file.write_text("rules: []\n")
            created_rules = True
        else:
            created_rules = False

        try:
            ok_result = subprocess.CompletedProcess(args=[], returncode=0, stdout='{"results":[]}', stderr="")
            monkeypatch.setattr(_p5.subprocess, "run", lambda *a, **kw: ok_result)
            monkeypatch.setattr(_p5, "bounded_run", lambda *a, **kw: _rc124())

            test_py = tmp_path / "test_example.py"
            test_py.write_text("def test_foo(): pass\n")
            ctx = _make_ctx(git_cwd=str(tmp_path))
            prev = _make_prev({"red_test_paths": ["test_example.py"]})
            result = _p5._verify_red_lint_rules(ctx, prev)
            assert result.error_code == "E_RED_LINT_TIMEOUT", (
                f"expected E_RED_LINT_TIMEOUT, got error_code={result.error_code!r}, "
                f"status={result.status!r}"
            )
        finally:
            if created_rules:
                rules_file.unlink(missing_ok=True)

    def test_verify_green_lint_rules_timeout(self, monkeypatch, tmp_path):
        """AC5 Bucket B (site 2681): when bounded_run returns rc=124 for semgrep,
        _verify_green_lint_rules returns error_code='E_GREEN_LINT_TIMEOUT'."""
        import shutil as _shutil
        monkeypatch.setattr(_p5.shutil, "which", lambda name: "/usr/local/bin/semgrep" if name == "semgrep" else _shutil.which(name))
        rules_dir = Path(_p5.__file__).parent.parent / "scripts" / "green_lint"
        rules_dir.mkdir(parents=True, exist_ok=True)
        rules_file = rules_dir / "rules.yml"
        if not rules_file.exists():
            rules_file.write_text("rules: []\n")
            created_rules = True
        else:
            created_rules = False

        try:
            ok_result = subprocess.CompletedProcess(args=[], returncode=0, stdout='{"results":[]}', stderr="")
            monkeypatch.setattr(_p5.subprocess, "run", lambda *a, **kw: ok_result)
            monkeypatch.setattr(_p5, "bounded_run", lambda *a, **kw: _rc124())
            # Ensure green_paths is non-empty by monkeypatching git_diff_files to return
            # a production .py file — otherwise the function returns early with "skipped".
            (tmp_path / "prod.py").write_text("x = 1\n")
            monkeypatch.setattr(_p5, "git_diff_files", lambda *a, **kw: ["prod.py"])

            fake_sha = "a" * 40
            ctx = _make_ctx(git_cwd=str(tmp_path))
            prev = _make_prev({
                "red_test_paths": [],
                "red_commit_sha": fake_sha,
                "git_cwd": str(tmp_path),
            })
            result = _p5._verify_green_lint_rules(ctx, prev)
            assert result.error_code == "E_GREEN_LINT_TIMEOUT", (
                f"expected E_GREEN_LINT_TIMEOUT, got error_code={result.error_code!r}, "
                f"status={result.status!r}"
            )
        finally:
            if created_rules:
                rules_file.unlink(missing_ok=True)

    def test_verify_green_typecheck_timeout(self, monkeypatch, tmp_path):
        """AC5 Bucket B (site 2966): when bounded_run returns rc=124 for mypy,
        _verify_green_typecheck returns error_code='E_GREEN_TYPECHECK_TIMEOUT'
        with recoverable=False."""
        ok_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        monkeypatch.setattr(_p5.subprocess, "run", lambda *a, **kw: ok_result)
        monkeypatch.setattr(_p5, "bounded_run", lambda *a, **kw: _rc124())
        # Ensure green_paths is non-empty to reach the mypy subprocess call.
        (tmp_path / "prod.py").write_text("x = 1\n")
        monkeypatch.setattr(_p5, "git_diff_files", lambda *a, **kw: ["prod.py"])

        fake_sha = "a" * 40
        ctx = _make_ctx(git_cwd=str(tmp_path))
        prev = _make_prev({
            "red_test_paths": [],
            "red_commit_sha": fake_sha,
            "git_cwd": str(tmp_path),
        })
        result = _p5._verify_green_typecheck(ctx, prev)
        assert result.error_code == "E_GREEN_TYPECHECK_TIMEOUT", (
            f"expected E_GREEN_TYPECHECK_TIMEOUT, got error_code={result.error_code!r}"
        )
        assert result.recoverable is False, (
            f"expected recoverable=False on green_typecheck timeout, got {result.recoverable!r}"
        )

    # ── AC6: Bucket B dict/None/break sites reproduce prior timeout return ────

    def test_verify_no_cross_tree_edits_no_main_repo_root_on_bounded_run_timeout(self, monkeypatch, tmp_path):
        """AC6 Bucket B (site 344/351): when git_port.git_read returns rc=124, the function
        must return the empty dict without a meaningful main_repo_root (pre-migration path
        via subprocess.run returns a real main_repo_root when git succeeds; post-migration
        path via git_port.git_read rc=124 returns empty with main_repo_root=None).

        Pre-migration: subprocess.run(["git", "worktree", ...]) runs real git.
        With a valid git repo as tmp_path worktree_root, git might return valid data
        — but git_port.git_read monkeypatch is inert so the git subprocess runs, populates
        main_root, and proceeds; `main_repo_root` in result is a string, not None.
        We assert `main_repo_root is None` — fails pre-migration, passes post.
        """
        # Patch subprocess.run to succeed with valid worktree data pointing to tmp_path
        # itself (so the function proceeds but main_repo_root gets set to str(tmp_path))
        worktree_output = f"worktree {tmp_path}\nHEAD abc123\nbranch refs/heads/main\n\n"
        monkeypatch.setattr(_p5.subprocess, "run", lambda *a, **kw: subprocess.CompletedProcess(
            args=a[0] if a else [], returncode=0, stdout=worktree_output, stderr=""
        ))
        monkeypatch.setattr(_p5.git_port, "git_read",
            lambda *a, **kw: _GitResult(returncode=124, stdout="", stderr="", timed_out=True))

        result = _p5._verify_no_cross_tree_edits(tmp_path)
        # Post-migration: git_port.git_read (first call) returns rc=124 → return empty
        # empty has main_repo_root=None.
        # Pre-migration: subprocess.run parses worktree list → main_root is set to
        # str(tmp_path) → main_repo_root is a string, NOT None.
        assert result.get("main_repo_root") is None, (
            f"expected main_repo_root=None (empty dict from rc=124 path), "
            f"got main_repo_root={result.get('main_repo_root')!r}. "
            "Pre-migration: subprocess.run returned valid data so main_repo_root was set."
        )

    def test_revert_cross_tree_modifications_uses_timeout_string(self, monkeypatch, tmp_path):
        """Spec 8B9CAB8C AC2: injected git_write_port returning rc=124 must produce
        error='timeout' for the file — proves runtime routing through the port seam,
        not a monkeypatch of the module-local `bounded_run` name (which the GREEN
        change removes from _revert_cross_tree_modifications entirely)."""
        from lib import git_write_port  # noqa: PLC0415 — deferred per §1q/D1CF5FDF

        class FakePort:
            def op_capture(self, cmd, *, cwd, timeout=30):
                return _GitResult(returncode=124, stdout="", stderr="", timed_out=True)

        git_write_port.set_default_git_write_factory(lambda: FakePort())
        try:
            result = _p5._revert_cross_tree_modifications(tmp_path, ["some/file.py"])
        finally:
            git_write_port.reset_default_git_write_factory()

        assert result["revert_attempted"] is True
        assert result["failed_count"] == 1
        assert len(result["results"]) == 1
        file_entry = result["results"][0]
        assert file_entry["reverted"] is False
        assert file_entry["error"] == "timeout", (
            f"expected error='timeout' for timed-out revert, got {file_entry['error']!r}"
        )

    def test_revert_cross_tree_modifications_happy_path_via_injected_port(self, tmp_path):
        """Spec 8B9CAB8C AC3: injected git_write_port returning rc=0 must mark the
        file reverted — proves the happy path also runs through the injected port,
        not real subprocess git (which would fail/non-zero against a non-repo tmp_path)."""
        from lib import git_write_port  # noqa: PLC0415 — deferred per §1q/D1CF5FDF

        class FakePort:
            def op_capture(self, cmd, *, cwd, timeout=30):
                return _GitResult(returncode=0, stdout="", stderr="", timed_out=False)

        git_write_port.set_default_git_write_factory(lambda: FakePort())
        try:
            result = _p5._revert_cross_tree_modifications(tmp_path, ["some/file.py"])
        finally:
            git_write_port.reset_default_git_write_factory()

        assert result["reverted_count"] == 1
        assert result["failed_count"] == 0
        assert result["results"][0]["reverted"] is True
        assert result["results"][0]["error"] is None

    def test_build_red_commit_message_branch_empty_on_timeout(self, monkeypatch, tmp_path):
        """AC6 Bucket B (site 1101/1110): when git_port.git_read returns rc=124,
        _build_red_commit_message uses branch='' (no branch in subject)."""
        ok_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="main\n", stderr="")
        monkeypatch.setattr(_p5.subprocess, "run", lambda *a, **kw: ok_result)
        monkeypatch.setattr(_p5.git_port, "git_read",
            lambda *a, **kw: _GitResult(returncode=124, stdout="", stderr="", timed_out=True))

        msg = _p5._build_red_commit_message(1, ["tests/test_foo.py"], str(tmp_path))
        assert "[" not in msg.splitlines()[0], (
            f"expected no branch in subject when timeout, got subject={msg.splitlines()[0]!r}"
        )
        assert msg.startswith("build: red cycle 1 tests\n"), (
            f"expected 'build: red cycle 1 tests' subject (no branch), got {msg!r}"
        )

    def test_main_checkout_root_returns_none_on_timeout(self, monkeypatch, tmp_path):
        """AC6 Bucket B (site 1493/1504): when git_port.git_read returns rc=124,
        _main_checkout_root returns None."""
        ok_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="/some/path/.git\n", stderr="")
        monkeypatch.setattr(_p5.subprocess, "run", lambda *a, **kw: ok_result)
        monkeypatch.setattr(_p5.git_port, "git_read",
            lambda *a, **kw: _GitResult(returncode=124, stdout="", stderr="", timed_out=True))

        result = _p5._main_checkout_root(str(tmp_path))
        assert result is None, (
            f"expected None when git_port.git_read times out (rc=124), got {result!r}"
        )

    def test_check_red_executable_sets_break_flag_on_timeout(self, monkeypatch, tmp_path):
        """AC6 Bucket B (site 3264/3267): when bounded_run returns rc=124 for the
        collectability probe, _check_red_executable sets _failure_stderr_tail to
        'collect timed out (30s)' and eventually returns an error StepResult
        (E_RED_COLLECT_FAILED or E_RED_NOT_EXECUTABLE)."""
        ok_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        monkeypatch.setattr(_p5.subprocess, "run", lambda *a, **kw: ok_result)
        monkeypatch.setattr(_p5, "bounded_run", lambda *a, **kw: _rc124())

        test_py = tmp_path / "test_foo.py"
        test_py.write_text("def test_x(): assert False\n")
        ctx = _make_ctx(git_cwd=str(tmp_path))
        prev = _make_prev({
            "red_test_paths": [str(test_py)],
            "cycle": 1,
        })
        result = _p5._check_red_executable(ctx, prev)
        assert result.status == "error", (
            f"expected error status on collect timeout, got {result.status!r}"
        )
        assert "collect timed out" in (result.error or ""), (
            f"expected 'collect timed out' in error message, got {result.error!r}"
        )

    def test_compute_baseline_typecheck_count_returns_none_on_timeout(self, monkeypatch, tmp_path):
        """AC6 Bucket B (site 2194/2204): when bounded_run returns rc=124 for the
        baseline mypy run, _compute_baseline_typecheck_count returns None.

        Repointed per §4 (9ECBF1F5): stash push/pop now go through
        git_write_port.git_op_capture seam. Inject a write-port factory that
        returns rc=0 for all stash ops; keep bounded_run monkeypatched to rc=124
        so the mypy baseline call times out → None.
        """

        class _StashOk:
            def op_capture(self, cmd: list, *, cwd: str, timeout: int = 30) -> _GitResult:
                # Return rc=0 "Saved" for stash push, rc=0 empty for stash pop —
                # any stash cmd gets a success result so stashed=True is set.
                return _GitResult(returncode=0, stdout="Saved working directory\n", stderr="", timed_out=False)

        _p5.git_write_port.set_default_git_write_factory(lambda: _StashOk())

        # bounded_run is now only called for the mypy baseline run → return rc=124.
        monkeypatch.setattr(_p5, "bounded_run", lambda *a, **kw: _rc124())
        # subprocess.run is used by _compute_baseline_typecheck_count — patch to safe return
        ok_result = subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr="")
        monkeypatch.setattr(_p5.subprocess, "run", lambda *a, **kw: ok_result)

        # prod.py must EXIST on disk: _compute_baseline_typecheck_count filters
        # resolved_paths to existing files after stashing; an empty list short-circuits
        # to `return 0` before the baseline mypy run, so the rc=124 timeout path
        # (the behavior under test) would never be reached.
        prod = tmp_path / "prod.py"
        prod.write_text("x: int = 1\n")

        try:
            result = _p5._compute_baseline_typecheck_count([str(prod)], str(tmp_path))
        finally:
            _p5.git_write_port.reset_default_git_write_factory()

        assert result is None, (
            f"expected None when mypy baseline run times out (rc=124), got {result!r}"
        )

    # ── AC6 Bucket A→B reclassification: _resolve_frozen_pre_red_sha ─────────

    def test_resolve_frozen_pre_red_sha_has_rc124_guard_behavior(self, monkeypatch, tmp_path):
        """AC7/AC6 Bucket A→B reclassification (site 726): _resolve_frozen_pre_red_sha
        reads result.stdout with no returncode guard. After migration it must have an
        explicit rc guard so rc=124 does not silently produce an empty/garbage SHA.
        Post-migration: function returns '' or None (not silently treating rc=124 as valid SHA).
        Pre-migration: subprocess.run returns a fake SHA → function returns it → assertion fails.
        """
        # Pre-migration: subprocess.run returns a fake 40-char SHA
        fake_sha = "a" * 40
        fake_result = subprocess.CompletedProcess(args=[], returncode=0, stdout=fake_sha + "\n", stderr="")
        monkeypatch.setattr(_p5.subprocess, "run", lambda *a, **kw: fake_result)
        monkeypatch.setattr(_p5.git_port, "git_read",
            lambda *a, **kw: _GitResult(returncode=124, stdout="", stderr="", timed_out=True))

        # Use a scratchpad without PRE_RED_REF_RELPATH so it falls through to git_port.git_read
        scratchpad = str(tmp_path / "scratch")
        result = _p5._resolve_frozen_pre_red_sha(scratchpad, str(tmp_path))

        # Post-migration: when git_port.git_read returns rc=124, the function must NOT return
        # a valid-looking SHA (it should return "" or raise a controlled error or return None).
        # This assertion captures the reclassification requirement: rc=124 must be handled.
        assert result != fake_sha, (
            f"_resolve_frozen_pre_red_sha returned a fake SHA '{result}' when git_port.git_read "
            "returns rc=124 — the rc==124 guard is missing (site 726 reclassification to Bucket B)"
        )
