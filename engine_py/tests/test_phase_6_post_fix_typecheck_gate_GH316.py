"""RED tests for GH316 — phase_6 post-fix mypy typecheck delta gate.

Contract: after run_pytest_post_fix, a new step `verify_fix_typecheck`:
  - is registered in phase_6_review_workflow() immediately after
    run_pytest_post_fix and before build_satisfaction_prompt
  - lib.mypy_baseline exports mypy_base_argv, parse_mypy_output, MYPY_LINE_RE
  - phase_5_implement re-aliases those symbols under private names (_mypy_base_argv
    etc.) preserving identity
  - skips (ok) on: no_python_scope, mypy_missing, no_git_repo (synthetic env),
    no_sha_boundary
  - fails CLOSED with E_POST_FIX_TYPECHECK_REGRESSION when net-new > 0 and enforce=True
  - passes ok when pre-existing debt unchanged (net_new <= 0), enforce=True
  - passes ok when net-new > 0 but enforce=False
  - emits verify_fix_typecheck_delta_verdict on any real verdict
  - always force-removes worktree + parent dir (D4-analog cleanup)

All 13 tests MUST FAIL until GREEN implements:
  - lib/mypy_baseline.py (new)
  - phase_5_implement.py re-aliases (3 defs replaced by re-exports)
  - phase_6_review.py _verify_fix_typecheck fn + StepContract registration

Do NOT implement the contract here — RED-only file.

§D1CF5FDF compliance: _verify_fix_typecheck and lib.mypy_baseline do NOT exist
yet.  ALL imports of those missing symbols are DEFERRED inside test function
bodies so this file COLLECTS cleanly and tests FAIL at attribute/assert time.
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# ── conftest-singleton path seam (§1q / 81F97F3D) ─────────────────────────────
# conftest.py inserts engine_py root + workflows at import time; we rely on that.
# Do NOT re-insert sys.path here.

# phase_6_review module itself already EXISTS — safe to import at module level.
from bytedigger_engine.workflows import phase_6_review  # noqa: E402
from bytedigger_engine.workflows.phase_6_review import phase_6_review_workflow  # noqa: E402
from bytedigger_engine.contracts import WorkflowContext, StepResult  # noqa: E402

# ─── helpers ──────────────────────────────────────────────────────────────────

_VALID_SHA = "a" * 40


def _make_ctx(tmp_path: Path, **org_extra) -> WorkflowContext:
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    (scratchpad / "reviews").mkdir(parents=True, exist_ok=True)
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
        question="Post-fix typecheck gate test GH316",
        session_id="test-session-GH316",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_prev(pre_fix_sha=_VALID_SHA, cycle: int = 2, **extra) -> MagicMock:
    """Minimal prev StepResult-like object with data dict."""
    prev = MagicMock()
    data: dict = {"cycle": cycle, **extra}
    if pre_fix_sha is not None:
        data["pre_fix_sha"] = pre_fix_sha
    prev.data = data
    return prev


def _init_git_repo(repo_path: Path) -> None:
    """Initialise a bare-minimum git repo for fixture use."""
    subprocess.run(["git", "init", str(repo_path)], check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@hal.test"],
        check=True, capture_output=True, cwd=str(repo_path),
    )
    subprocess.run(
        ["git", "config", "user.name", "HAL Test"],
        check=True, capture_output=True, cwd=str(repo_path),
    )


def _git_commit(repo_path: Path, message: str) -> str:
    """Stage all and commit; return the resulting SHA."""
    subprocess.run(
        ["git", "add", "-A"], check=True, capture_output=True, cwd=str(repo_path)
    )
    subprocess.run(
        ["git", "commit", "-m", message],
        check=True, capture_output=True, cwd=str(repo_path),
    )
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        check=True, capture_output=True, text=True, cwd=str(repo_path),
    )
    return result.stdout.strip()


def _is_mypy_available() -> bool:
    """True if mypy is reachable via uvx or system PATH."""
    return shutil.which("uvx") is not None or shutil.which("mypy") is not None


# ═══════════════════════════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestPostFixTypecheckGate:
    """_verify_fix_typecheck: deterministic post-fix mypy delta gate (GH316)."""

    # ── AC1: mypy_base_argv contract ───────────────────────────────────────────

    def test_ac1_mypy_base_argv_returns_correct_structure(self, tmp_path: Path) -> None:
        """lib.mypy_baseline.mypy_base_argv(cwd) returns a list starting with
        'uvx','mypy' if uvx on PATH else 'mypy'; appends --ignore-missing-imports
        iff no config file; always ends with --follow-imports=silent
        --no-error-summary --no-color-output.

        AC1: mypy_base_argv structure.
        """
        # §D1CF5FDF: deferred import — module does not exist yet in GREEN
        from bytedigger_engine.lib.mypy_baseline import mypy_base_argv  # type: ignore[import]  # noqa: PLC0415

        cwd = str(tmp_path)
        argv = mypy_base_argv(cwd)

        assert isinstance(argv, list), f"Expected list, got {type(argv)}"
        assert len(argv) >= 2, f"Too short: {argv!r}"

        # Binary: uvx+mypy if uvx present, else mypy
        if shutil.which("uvx"):
            assert argv[0] == "uvx", f"Expected 'uvx' first when uvx on PATH; got {argv!r}"
            assert argv[1] == "mypy", f"Expected 'mypy' second; got {argv!r}"
        else:
            assert argv[0] == "mypy", f"Expected 'mypy' when no uvx; got {argv!r}"

        # Mandatory trailing flags
        assert "--follow-imports=silent" in argv, f"Missing --follow-imports=silent in {argv!r}"
        assert "--no-error-summary" in argv, f"Missing --no-error-summary in {argv!r}"
        assert "--no-color-output" in argv, f"Missing --no-color-output in {argv!r}"

        # No config in tmp_path → --ignore-missing-imports must be appended
        assert "--ignore-missing-imports" in argv, (
            f"Expected --ignore-missing-imports when no config in {cwd!r}; argv={argv!r}"
        )

        # With mypy.ini present → --ignore-missing-imports must NOT appear
        (tmp_path / "mypy.ini").write_text("[mypy]\n")
        argv_with_config = mypy_base_argv(cwd)
        assert "--ignore-missing-imports" not in argv_with_config, (
            f"--ignore-missing-imports must be absent when mypy.ini present; "
            f"argv={argv_with_config!r}"
        )

    # ── AC2: parse_mypy_output contract ───────────────────────────────────────

    def test_ac2_parse_mypy_output_parses_correctly(self) -> None:
        """lib.mypy_baseline.parse_mypy_output(text) parses error/warning lines
        into finding dicts; ignores note:/Success:/unparsable; returns [] on empty.

        AC2: parse_mypy_output behavior.
        """
        # §D1CF5FDF: deferred import
        from bytedigger_engine.lib.mypy_baseline import parse_mypy_output  # type: ignore[import]  # noqa: PLC0415

        # Empty
        assert parse_mypy_output("") == [], "Empty input must return []"
        assert parse_mypy_output("Success: no issues found in 1 source file\n") == [], (
            "Success: line must be ignored"
        )

        # note: lines ignored
        note_text = "foo.py:10: note: Revealed type is 'int'"
        assert parse_mypy_output(note_text) == [], f"note: lines must be ignored; got {parse_mypy_output(note_text)!r}"

        # error line
        error_text = "src/foo.py:5:1: error: Incompatible return value type (got \"int\", expected \"str\")  [return-value]"
        findings = parse_mypy_output(error_text)
        assert len(findings) == 1, f"Expected 1 finding; got {findings!r}"
        f = findings[0]
        assert f.get("severity") == "error", f"Expected severity=error; got {f!r}"
        assert f.get("path") == "src/foo.py", f"Expected path=src/foo.py; got {f!r}"
        assert f.get("start") == 5, f"Expected start=5; got {f!r}"

        # warning line
        warn_text = "src/bar.py:12: warning: Some warning"
        wfindings = parse_mypy_output(warn_text)
        assert len(wfindings) == 1, f"Expected 1 warning finding; got {wfindings!r}"
        assert wfindings[0].get("severity") == "warning"

        # Mixed: 2 errors + 1 note → 2 findings
        mixed = (
            "src/a.py:1: error: Error A  [attr-defined]\n"
            "src/a.py:2: note: Hint\n"
            "src/b.py:3: error: Error B\n"
        )
        mixed_findings = parse_mypy_output(mixed)
        assert len(mixed_findings) == 2, (
            f"Expected 2 findings from mixed output; got {mixed_findings!r}"
        )

    # ── AC3: phase_5 re-alias identity ────────────────────────────────────────

    def test_ac3_phase5_realiases_are_same_objects_as_lib(self) -> None:
        """After GREEN: phase_5_implement._mypy_base_argv IS lib.mypy_baseline.mypy_base_argv
        (object identity — re-alias, not a copy).  Same for _parse_mypy_output and _MYPY_LINE_RE.

        AC3: re-alias identity (behavior-preserving refactor of phase_5).
        """
        # §D1CF5FDF: both imports deferred — lib does not exist yet
        from bytedigger_engine.lib.mypy_baseline import (  # type: ignore[import]  # noqa: PLC0415
            mypy_base_argv,
            parse_mypy_output,
            MYPY_LINE_RE,
        )
        from bytedigger_engine.workflows import phase_5_implement as p5  # noqa: PLC0415

        p5_argv_fn = getattr(p5, "_mypy_base_argv", None)
        p5_parse_fn = getattr(p5, "_parse_mypy_output", None)
        p5_re = getattr(p5, "_MYPY_LINE_RE", None)

        assert p5_argv_fn is not None, (
            "phase_5_implement._mypy_base_argv not found — GREEN has not re-aliased yet"
        )
        assert p5_parse_fn is not None, (
            "phase_5_implement._parse_mypy_output not found — GREEN has not re-aliased yet"
        )
        assert p5_re is not None, (
            "phase_5_implement._MYPY_LINE_RE not found — GREEN has not re-aliased yet"
        )

        assert p5_argv_fn is mypy_base_argv, (
            f"phase_5_implement._mypy_base_argv must IS lib.mypy_baseline.mypy_base_argv; "
            f"got {p5_argv_fn!r} vs {mypy_base_argv!r}"
        )
        assert p5_parse_fn is parse_mypy_output, (
            f"phase_5_implement._parse_mypy_output must IS lib.mypy_baseline.parse_mypy_output; "
            f"got {p5_parse_fn!r} vs {parse_mypy_output!r}"
        )
        assert p5_re is MYPY_LINE_RE, (
            f"phase_5_implement._MYPY_LINE_RE must IS lib.mypy_baseline.MYPY_LINE_RE; "
            f"got {p5_re!r} vs {MYPY_LINE_RE!r}"
        )

    # ── AC4: step registration order ──────────────────────────────────────────

    def test_ac4_verify_fix_typecheck_registered_after_pytest_before_satisfaction(
        self,
    ) -> None:
        """phase_6_review_workflow().steps must contain verify_fix_typecheck
        positioned immediately after run_pytest_post_fix and immediately before
        build_satisfaction_prompt.

        AC4: step registration order invariant.
        """
        workflow = phase_6_review_workflow()
        step_names = [s.name for s in workflow.steps]

        assert "verify_fix_typecheck" in step_names, (
            f"'verify_fix_typecheck' not found in workflow steps: {step_names!r}"
        )

        idx_pytest = step_names.index("run_pytest_post_fix")
        idx_typecheck = step_names.index("verify_fix_typecheck")
        idx_satisfaction = step_names.index("build_satisfaction_prompt")

        assert idx_typecheck == idx_pytest + 1, (
            f"Expected 'verify_fix_typecheck' immediately after 'run_pytest_post_fix' "
            f"(positions {idx_pytest + 1} vs {idx_typecheck}). All steps: {step_names!r}"
        )
        idx_build_decorr = step_names.index("build_decorr_prompt")
        assert idx_typecheck == idx_build_decorr - 1, (
            f"Expected 'verify_fix_typecheck' immediately before 'build_decorr_prompt' "
            f"(positions {idx_build_decorr - 1} vs {idx_typecheck}). All steps: {step_names!r}"
        )

    # ── AC5: net-new errors blocked (real git + real mypy) ────────────────────

    @pytest.mark.skipif(
        not _is_mypy_available(),
        reason="mypy/uvx not on PATH — cannot run real typecheck AC5",
    )
    def test_ac5_net_new_type_errors_block_with_error(self, tmp_path: Path) -> None:
        """Real git fixture: baseline commit has clean prod .py; fix commit introduces
        a NET-NEW type error.  With enforce=True, _verify_fix_typecheck returns
        status='error', error_code='E_POST_FIX_TYPECHECK_REGRESSION'.

        AC5: net-new regression blocks build.
        §1i (8CA8D54C): real git repo pre-staged before invoke, never racy.
        """
        # §D1CF5FDF: deferred import
        from bytedigger_engine.workflows.phase_6_review import _verify_fix_typecheck  # type: ignore[import]  # noqa: PLC0415  # §1q: same module object as top-level `import bytedigger_engine.workflows.phase_6_review` the tests monkeypatch

        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)

        # Baseline commit: clean Python file (no mypy errors)
        prod_py = repo / "src" / "mymodule.py"
        prod_py.parent.mkdir()
        prod_py.write_text(
            "def add(x: int, y: int) -> int:\n    return x + y\n"
        )
        pre_fix_sha = _git_commit(repo, "baseline: clean typing")

        # Fix commit: introduce NET-NEW type error (returns int where str declared)
        prod_py.write_text(
            "def add(x: int, y: int) -> int:\n    return x + y\n"
            "\ndef broken(x: int) -> str:\n    return x  # type error: int != str\n"
        )
        _git_commit(repo, "fix: introduces net-new type error")

        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()
        (scratchpad / "reviews").mkdir()
        ctx = WorkflowContext(
            tenant_id="hal",
            scope=None,
            db_path=None,
            org_config={
                "git_cwd": str(repo),
                "scratchpad_dir": str(scratchpad),
                # enforce=True is the default; make explicit
                "post_fix_typecheck_delta_enforce": True,
            },
            question="AC5 real git test",
            session_id="test-GH316-ac5",
            persona="hal",
            framework=None,
            domain=None,
        )
        prev = _make_prev(pre_fix_sha=pre_fix_sha)

        result = _verify_fix_typecheck(ctx, prev)

        assert result.status == "error", (
            f"Expected status='error' for net-new mypy error; got {result.status!r}"
        )
        assert result.error_code == "E_POST_FIX_TYPECHECK_REGRESSION", (
            f"Expected E_POST_FIX_TYPECHECK_REGRESSION; got {result.error_code!r}"
        )
        assert result.recoverable is True, (
            f"Regression must be recoverable=True (fix loop re-fixes); got {result.recoverable!r}"
        )

    # ── AC6: pre-existing debt not blocked (real git + real mypy) ─────────────

    @pytest.mark.skipif(
        not _is_mypy_available(),
        reason="mypy/uvx not on PATH — cannot run real typecheck AC6",
    )
    def test_ac6_preexisting_errors_not_blocked(self, tmp_path: Path) -> None:
        """Real git fixture: baseline already has N mypy errors in a prod .py.
        Fix commit adds no NEW errors (same count).  With enforce=True, step
        returns status='ok' (net_new <= 0 → not blocked).

        AC6: pre-existing debt does not trip the gate.
        §1i (8CA8D54C): git state pre-staged, never racy.
        """
        # §D1CF5FDF: deferred import
        from bytedigger_engine.workflows.phase_6_review import _verify_fix_typecheck  # type: ignore[import]  # noqa: PLC0415  # §1q: same module object as top-level `import bytedigger_engine.workflows.phase_6_review` the tests monkeypatch

        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)

        # Baseline: one pre-existing type error
        prod_py = repo / "src" / "mod.py"
        prod_py.parent.mkdir()
        prod_py.write_text(
            "def broken(x: int) -> str:\n    return x  # pre-existing error\n"
        )
        pre_fix_sha = _git_commit(repo, "baseline: one pre-existing type error")

        # Fix commit: keeps the same error (no net-new), adds an unrelated change
        prod_py.write_text(
            "def broken(x: int) -> str:\n    return x  # pre-existing error\n"
            "\nFOO = 1  # harmless addition\n"
        )
        _git_commit(repo, "fix: no new type errors introduced")

        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()
        (scratchpad / "reviews").mkdir()
        ctx = WorkflowContext(
            tenant_id="hal",
            scope=None,
            db_path=None,
            org_config={
                "git_cwd": str(repo),
                "scratchpad_dir": str(scratchpad),
                "post_fix_typecheck_delta_enforce": True,
            },
            question="AC6 preexisting debt",
            session_id="test-GH316-ac6",
            persona="hal",
            framework=None,
            domain=None,
        )
        prev = _make_prev(pre_fix_sha=pre_fix_sha)

        result = _verify_fix_typecheck(ctx, prev)

        assert result.status == "ok", (
            f"Pre-existing debt (net_new=0) must not block; got status={result.status!r}"
        )

    # ── AC7: enforce=False → ok even with net-new ─────────────────────────────

    def test_ac7_enforce_false_skips_block(self, tmp_path: Path, monkeypatch) -> None:
        """When cfg["post_fix_typecheck_delta_enforce"]=False, step returns ok
        even if verdict would have blocked; emitted verdict has enforced==False.

        AC7: enforce=False → no block.
        """
        # §D1CF5FDF: deferred import
        from bytedigger_engine.workflows.phase_6_review import _verify_fix_typecheck  # type: ignore[import]  # noqa: PLC0415  # §1q: same module object as top-level `import bytedigger_engine.workflows.phase_6_review` the tests monkeypatch

        captured: list[dict] = []
        monkeypatch.setattr(
            phase_6_review,
            "_emit_safe",
            lambda et, p, **kw: captured.append({"type": et, "payload": p}),
        )

        # Patch bounded_run to return a net-new error (current=1, baseline=0)
        def _fake_bounded_run(cmd, **kwargs):
            """Return 1 mypy error on any run."""
            return subprocess.CompletedProcess(
                args=cmd, returncode=1,
                stdout="src/foo.py:5: error: Bad type  [return-value]\n",
                stderr="",
            )

        monkeypatch.setattr(phase_6_review, "bounded_run", _fake_bounded_run)

        # Also patch git_diff_files to return one changed prod .py
        monkeypatch.setattr(
            phase_6_review, "git_diff_files",
            lambda *a, **kw: ["src/foo.py"],
        )

        # Patch worktree operations so baseline=0 (empty worktree run)
        def _fake_git_write(args, cwd, **kw):
            return (0, "", "")

        monkeypatch.setattr(phase_6_review, "_git_write", _fake_git_write, raising=False)

        repo = tmp_path / "repo"
        repo.mkdir()
        # Create src/foo.py so boundary check passes
        (repo / "src").mkdir()
        (repo / "src" / "foo.py").write_text("x: int = 1\n")

        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()
        (scratchpad / "reviews").mkdir()

        ctx = WorkflowContext(
            tenant_id="hal",
            scope=None,
            db_path=None,
            org_config={
                "git_cwd": str(repo),
                "scratchpad_dir": str(scratchpad),
                "post_fix_typecheck_delta_enforce": False,  # key: enforce off
            },
            question="AC7 enforce=False",
            session_id="test-GH316-ac7",
            persona="hal",
            framework=None,
            domain=None,
        )
        prev = _make_prev(pre_fix_sha=_VALID_SHA)

        result = _verify_fix_typecheck(ctx, prev)

        assert result.status == "ok", (
            f"enforce=False must not block even with net-new; got {result.status!r}"
        )

        # Verdict event (if emitted) must have enforced==False
        verdict_events = [e for e in captured if e["type"] == "verify_fix_typecheck_delta_verdict"]
        if verdict_events:
            assert verdict_events[0]["payload"].get("enforced") is False, (
                f"enforced must be False in verdict event; payload: {verdict_events[0]['payload']!r}"
            )

    # ── AC8: no changed prod .py → skip, reason=no_python_scope ──────────────

    def test_ac8_no_python_scope_skips(self, tmp_path: Path, monkeypatch) -> None:
        """When git_diff_files since pre_fix_sha has no changed production .py
        (only test files or none), step returns ok and emits
        post_fix_typecheck_skipped reason=no_python_scope; no worktree created.

        AC8: no_python_scope skip.
        """
        # §D1CF5FDF: deferred import
        from bytedigger_engine.workflows.phase_6_review import _verify_fix_typecheck  # type: ignore[import]  # noqa: PLC0415  # §1q: same module object as top-level `import bytedigger_engine.workflows.phase_6_review` the tests monkeypatch

        captured: list[dict] = []
        monkeypatch.setattr(
            phase_6_review,
            "_emit_safe",
            lambda et, p, **kw: captured.append({"type": et, "payload": p}),
        )

        worktree_calls: list = []

        def _spy_git_write(args, cwd, **kw):
            worktree_calls.append(args)
            return (0, "", "")

        monkeypatch.setattr(phase_6_review, "_git_write", _spy_git_write, raising=False)

        # Only test files in diff — no production .py scope
        monkeypatch.setattr(
            phase_6_review, "git_diff_files",
            lambda *a, **kw: ["tests/test_foo.py", "tests/__init__.py"],
        )

        ctx = _make_ctx(tmp_path)
        prev = _make_prev(pre_fix_sha=_VALID_SHA)

        result = _verify_fix_typecheck(ctx, prev)

        assert result.status == "ok", (
            f"no_python_scope must return ok; got {result.status!r}"
        )

        skip_events = [e for e in captured if e["type"] == "post_fix_typecheck_skipped"]
        assert len(skip_events) == 1, (
            f"Expected 1 post_fix_typecheck_skipped event; got {len(skip_events)}. "
            f"All events: {[e['type'] for e in captured]}"
        )
        assert skip_events[0]["payload"].get("reason") == "no_python_scope", (
            f"Expected reason='no_python_scope'; got {skip_events[0]['payload']!r}"
        )

        # No worktree should be created
        worktree_add_calls = [c for c in worktree_calls if "worktree" in c and "add" in c]
        assert len(worktree_add_calls) == 0, (
            f"No worktree must be created when scope is empty; worktree calls: {worktree_calls!r}"
        )

    # ── AC9: mypy binary missing → ok, reason=mypy_missing ───────────────────

    def test_ac9_mypy_missing_skips_not_fails(self, tmp_path: Path, monkeypatch) -> None:
        """When mypy invocation raises FileNotFoundError (binary missing),
        step returns ok (build not hard-failed) and emits
        post_fix_typecheck_skipped reason=mypy_missing.

        AC9: mypy_missing graceful skip.
        """
        # §D1CF5FDF: deferred import
        from bytedigger_engine.workflows.phase_6_review import _verify_fix_typecheck  # type: ignore[import]  # noqa: PLC0415  # §1q: same module object as top-level `import bytedigger_engine.workflows.phase_6_review` the tests monkeypatch

        captured: list[dict] = []
        monkeypatch.setattr(
            phase_6_review,
            "_emit_safe",
            lambda et, p, **kw: captured.append({"type": et, "payload": p}),
        )

        # Simulate mypy binary absent
        def _raise_fnf(cmd, **kwargs):
            raise FileNotFoundError(f"No such file or directory: {cmd[0]!r}")

        monkeypatch.setattr(phase_6_review, "bounded_run", _raise_fnf)

        # Provide one changed prod .py so we reach the mypy invocation
        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "src").mkdir()
        (repo / "src" / "mod.py").write_text("x = 1\n")

        monkeypatch.setattr(
            phase_6_review, "git_diff_files",
            lambda *a, **kw: ["src/mod.py"],
        )

        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()
        (scratchpad / "reviews").mkdir()

        ctx = WorkflowContext(
            tenant_id="hal",
            scope=None,
            db_path=None,
            org_config={
                "git_cwd": str(repo),
                "scratchpad_dir": str(scratchpad),
            },
            question="AC9 mypy missing",
            session_id="test-GH316-ac9",
            persona="hal",
            framework=None,
            domain=None,
        )
        prev = _make_prev(pre_fix_sha=_VALID_SHA)

        result = _verify_fix_typecheck(ctx, prev)

        assert result.status == "ok", (
            f"mypy_missing must not hard-fail; got {result.status!r}"
        )

        skip_events = [e for e in captured if e["type"] == "post_fix_typecheck_skipped"]
        assert len(skip_events) == 1, (
            f"Expected 1 post_fix_typecheck_skipped; got {len(skip_events)}. "
            f"All events: {[e['type'] for e in captured]}"
        )
        assert skip_events[0]["payload"].get("reason") == "mypy_missing", (
            f"Expected reason='mypy_missing'; got {skip_events[0]['payload']!r}"
        )

    # ── AC10: synthetic test env → ok, reason=no_git_repo ─────────────────────

    def test_ac10_synthetic_env_skips(self, tmp_path: Path, monkeypatch) -> None:
        """When _is_synthetic_test_env(cfg, git_cwd) returns True,
        step returns ok and emits post_fix_typecheck_skipped reason=no_git_repo.
        No mypy or worktree created.

        AC10: synthetic env skip.
        """
        # §D1CF5FDF: deferred import
        from bytedigger_engine.workflows.phase_6_review import _verify_fix_typecheck  # type: ignore[import]  # noqa: PLC0415  # §1q: same module object as top-level `import bytedigger_engine.workflows.phase_6_review` the tests monkeypatch

        captured: list[dict] = []
        mypy_calls: list = []
        monkeypatch.setattr(
            phase_6_review,
            "_emit_safe",
            lambda et, p, **kw: captured.append({"type": et, "payload": p}),
        )
        monkeypatch.setattr(
            phase_6_review, "bounded_run",
            lambda *a, **kw: mypy_calls.append(a) or None,
        )
        # Force synthetic: _is_synthetic_test_env returns True
        monkeypatch.setattr(
            phase_6_review, "_is_synthetic_test_env",
            lambda cfg, git_cwd: True,
        )

        # NOTE: git_cwd key NOT in org (so _is_synthetic_test_env would normally probe) —
        # but we've monkeypatched it to True directly.
        ctx = WorkflowContext(
            tenant_id="hal",
            scope=None,
            db_path=None,
            org_config={
                # no git_cwd key → synthetic path (but patched anyway)
                "scratchpad_dir": str(tmp_path / "scratch"),
            },
            question="AC10 synthetic env",
            session_id="test-GH316-ac10",
            persona="hal",
            framework=None,
            domain=None,
        )
        (tmp_path / "scratch").mkdir()
        ((tmp_path / "scratch") / "reviews").mkdir()
        prev = _make_prev(pre_fix_sha=_VALID_SHA)

        result = _verify_fix_typecheck(ctx, prev)

        assert result.status == "ok", (
            f"Synthetic env must return ok; got {result.status!r}"
        )

        skip_events = [e for e in captured if e["type"] == "post_fix_typecheck_skipped"]
        assert len(skip_events) == 1, (
            f"Expected 1 post_fix_typecheck_skipped; got {len(skip_events)}. "
            f"All events: {[e['type'] for e in captured]}"
        )
        assert skip_events[0]["payload"].get("reason") == "no_git_repo", (
            f"Expected reason='no_git_repo'; got {skip_events[0]['payload']!r}"
        )

        assert len(mypy_calls) == 0, (
            f"mypy must NOT be called in synthetic env; was called {len(mypy_calls)} time(s)"
        )

    # ── AC11: pre_fix_sha absent + resolve raises → ok, reason=no_sha_boundary

    def test_ac11_no_sha_boundary_skips(self, tmp_path: Path, monkeypatch) -> None:
        """When pre_fix_sha absent from prev.data AND resolve_pre_phase_sha raises,
        step returns ok and emits post_fix_typecheck_skipped reason=no_sha_boundary.

        AC11: missing SHA boundary graceful skip.
        """
        # §D1CF5FDF: deferred import
        from bytedigger_engine.workflows.phase_6_review import _verify_fix_typecheck  # type: ignore[import]  # noqa: PLC0415  # §1q: same module object as top-level `import bytedigger_engine.workflows.phase_6_review` the tests monkeypatch

        captured: list[dict] = []
        monkeypatch.setattr(
            phase_6_review,
            "_emit_safe",
            lambda et, p, **kw: captured.append({"type": et, "payload": p}),
        )
        monkeypatch.setattr(
            phase_6_review,
            "resolve_pre_phase_sha",
            lambda worktree_root: (_ for _ in ()).throw(RuntimeError("not a git repo")),
        )

        ctx = _make_ctx(tmp_path)
        prev = MagicMock()
        prev.data = {"cycle": 1}  # no pre_fix_sha key at all

        result = _verify_fix_typecheck(ctx, prev)

        assert result.status == "ok", (
            f"No-SHA boundary must return ok; got {result.status!r}"
        )

        skip_events = [e for e in captured if e["type"] == "post_fix_typecheck_skipped"]
        assert len(skip_events) == 1, (
            f"Expected 1 post_fix_typecheck_skipped; got {len(skip_events)}. "
            f"All events: {[e['type'] for e in captured]}"
        )
        assert skip_events[0]["payload"].get("reason") == "no_sha_boundary", (
            f"Expected reason='no_sha_boundary'; got {skip_events[0]['payload']!r}"
        )

    # ── AC12: verify_fix_typecheck_delta_verdict emitted ──────────────────────

    def test_ac12_verdict_event_emitted_with_required_keys(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Any run that reaches the verdict step (not skipped) emits
        verify_fix_typecheck_delta_verdict with keys:
        baseline_failed, current_failed, net_new, classification, enforced, phase==6.

        AC12: verdict event shape.
        """
        # §D1CF5FDF: deferred import
        from bytedigger_engine.workflows.phase_6_review import _verify_fix_typecheck  # type: ignore[import]  # noqa: PLC0415  # §1q: same module object as top-level `import bytedigger_engine.workflows.phase_6_review` the tests monkeypatch

        captured: list[dict] = []
        monkeypatch.setattr(
            phase_6_review,
            "_emit_safe",
            lambda et, p, **kw: captured.append({"type": et, "payload": p}),
        )

        # Patch bounded_run to return 0 errors (clean run → ok path)
        monkeypatch.setattr(
            phase_6_review, "bounded_run",
            lambda cmd, **kwargs: subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout="", stderr="",
            ),
        )
        monkeypatch.setattr(
            phase_6_review, "git_diff_files",
            lambda *a, **kw: ["src/clean.py"],
        )

        # Patch worktree add/remove to no-ops (baseline run returns 0 too)
        def _noop_git_write(args, cwd, **kw):
            return (0, "", "")

        monkeypatch.setattr(phase_6_review, "_git_write", _noop_git_write, raising=False)

        repo = tmp_path / "repo"
        repo.mkdir()
        (repo / "src").mkdir()
        (repo / "src" / "clean.py").write_text("x = 1\n")

        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()
        (scratchpad / "reviews").mkdir()

        ctx = WorkflowContext(
            tenant_id="hal",
            scope=None,
            db_path=None,
            org_config={
                "git_cwd": str(repo),
                "scratchpad_dir": str(scratchpad),
                "post_fix_typecheck_delta_enforce": True,
            },
            question="AC12 verdict event",
            session_id="test-GH316-ac12",
            persona="hal",
            framework=None,
            domain=None,
        )
        prev = _make_prev(pre_fix_sha=_VALID_SHA)

        result = _verify_fix_typecheck(ctx, prev)

        verdict_events = [
            e for e in captured if e["type"] == "verify_fix_typecheck_delta_verdict"
        ]
        assert len(verdict_events) >= 1, (
            f"Expected at least 1 verify_fix_typecheck_delta_verdict event; "
            f"all events: {[e['type'] for e in captured]}"
        )

        payload = verdict_events[0]["payload"]
        required_keys = {"baseline_failed", "current_failed", "net_new", "classification",
                         "enforced", "phase"}
        missing = required_keys - set(payload.keys())
        assert not missing, (
            f"verify_fix_typecheck_delta_verdict missing keys {missing!r}; "
            f"full payload: {payload!r}"
        )
        assert payload.get("phase") == 6, (
            f"Event must carry phase=6; got {payload.get('phase')!r}"
        )

    # ── AC13: worktree cleanup (real git + real mypy) ─────────────────────────

    @pytest.mark.skipif(
        not _is_mypy_available(),
        reason="mypy/uvx not on PATH — cannot run real worktree/typecheck AC13",
    )
    def test_ac13_worktree_always_cleaned_up(self, tmp_path: Path) -> None:
        """After _verify_fix_typecheck runs (even if mypy errors during baseline),
        no tc_baseline_* worktree dir remains and git worktree list shows no
        leftover detached worktrees for this repo.

        AC13: D4-analog worktree cleanup invariant.
        §1i (8CA8D54C): git state pre-staged, never racy.
        """
        # §D1CF5FDF: deferred import
        from bytedigger_engine.workflows.phase_6_review import _verify_fix_typecheck  # type: ignore[import]  # noqa: PLC0415  # §1q: same module object as top-level `import bytedigger_engine.workflows.phase_6_review` the tests monkeypatch

        repo = tmp_path / "repo"
        repo.mkdir()
        _init_git_repo(repo)

        # Baseline: a clean prod .py
        prod_py = repo / "src" / "mod.py"
        prod_py.parent.mkdir()
        prod_py.write_text("def f(x: int) -> int:\n    return x\n")
        pre_fix_sha = _git_commit(repo, "baseline")

        # Fix commit: add a type error to trigger worktree baseline path
        prod_py.write_text(
            "def f(x: int) -> int:\n    return x\n"
            "\ndef bad(x: int) -> str:\n    return x\n"
        )
        _git_commit(repo, "fix: adds net-new type error")

        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()
        (scratchpad / "reviews").mkdir()

        ctx = WorkflowContext(
            tenant_id="hal",
            scope=None,
            db_path=None,
            org_config={
                "git_cwd": str(repo),
                "scratchpad_dir": str(scratchpad),
                "post_fix_typecheck_delta_enforce": True,
            },
            question="AC13 worktree cleanup",
            session_id="test-GH316-ac13",
            persona="hal",
            framework=None,
            domain=None,
        )
        prev = _make_prev(pre_fix_sha=pre_fix_sha)

        # Run the step (error or ok, doesn't matter — cleanup must still occur)
        _verify_fix_typecheck(ctx, prev)

        # Assert no tc_baseline_* directories remain under tmp_path
        leftover_dirs = list(tmp_path.rglob("tc_baseline_*"))
        assert len(leftover_dirs) == 0, (
            f"tc_baseline_* dirs must be cleaned up; found: {leftover_dirs!r}"
        )

        # Assert git worktree list shows only the main worktree
        wt_result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=str(repo),
            capture_output=True,
            text=True,
        )
        wt_lines = wt_result.stdout.strip().splitlines()
        # The main worktree has a "worktree <path>" line; no extra worktrees
        worktree_entries = [l for l in wt_lines if l.startswith("worktree ")]
        assert len(worktree_entries) == 1, (
            f"Only the main worktree must remain; git worktree list:\n{wt_result.stdout}"
        )
