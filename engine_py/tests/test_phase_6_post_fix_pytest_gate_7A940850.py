"""RED tests for 7A940850 — phase_6 _run_pytest_post_fix (post-fix sibling-regression gate).

Contract: after _commit_fix_code, a new step `run_pytest_post_fix`:
  - is registered in phase_6_review_workflow() immediately between
    commit_fix_code and build_satisfaction_prompt
  - uses prev.data["red_test_paths"] (filtered to *.py) when present
  - falls back to git_diff_files filtered to test-py predicate when absent
  - passes ok + emits post_fix_pytest_pass when pytest exits 0
  - fails closed with E_POST_FIX_PYTEST_FAILED + artifact when n_failed > 0
  - skips (ok pass-through) when test scope is empty, emitting
    post_fix_pytest_skipped with reason="no_test_scope"
  - returns E_POST_FIX_PYTEST_INFRA on FileNotFoundError / TimeoutExpired /
    exit_no_failures (exit_code!=0 but n_failed==0)
  - skips (ok) when neither pre_fix_sha source yields a valid SHA
  - never mutates prev.data in-place

All 12 tests MUST FAIL until GREEN agent implements _run_pytest_post_fix in
phase_6_review.py and registers it in phase_6_review_workflow().

Do NOT implement the contract here — RED-only file.
"""
from __future__ import annotations

import copy
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

HERE = Path(__file__).parent
ENGINE_ROOT = HERE.parent
sys.path.insert(0, str(ENGINE_ROOT))

from bytedigger_engine.workflows import phase_6_review  # noqa: E402  — imported as module so getattr works cleanly
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
        question="Post-fix gate test",
        session_id="test-session-7A940850",
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


@dataclass
class _FakeTestRunResult:
    """Stand-in for plugins.disk_truth.TestRunResult — duck-typed for run_test_command mock."""
    exit_code: int
    n_passed: int
    n_failed: int
    stdout_path: str = "/tmp/fake-stdout.txt"
    stderr_path: str = "/tmp/fake-stderr.txt"


def _write_fake_stdout(stdout_path: str, content: str) -> None:
    """Helper: write content to the stdout file so the step can read it."""
    Path(stdout_path).parent.mkdir(parents=True, exist_ok=True)
    Path(stdout_path).write_text(content)


# ═══════════════════════════════════════════════════════════════════════════════
# Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestPostFixPytestGate:
    """_run_pytest_post_fix: deterministic post-fix sibling-regression gate (7A940850)."""

    # ── AC1: step registration ─────────────────────────────────────────────────

    def test_step_registered_between_commit_fix_and_satisfaction(self) -> None:
        """run_pytest_post_fix must appear immediately after commit_fix_tests
        (which itself follows commit_fix_code — 8FE3D757) and immediately before
        build_satisfaction_prompt in phase_6_review_workflow().

        AC1: step registration order invariant.
        """
        workflow = phase_6_review_workflow()
        step_names = [s.name for s in workflow.steps]

        assert "run_pytest_post_fix" in step_names, (
            f"'run_pytest_post_fix' not found in workflow steps: {step_names!r}"
        )

        idx_commit = step_names.index("commit_fix_code")
        idx_commit_tests = step_names.index("commit_fix_tests")  # 8FE3D757
        idx_post_fix = step_names.index("run_pytest_post_fix")
        idx_satisfaction = step_names.index("build_satisfaction_prompt")

        assert idx_commit_tests == idx_commit + 1, (
            f"Expected 'commit_fix_tests' immediately after 'commit_fix_code' "
            f"(positions {idx_commit+1} vs {idx_commit_tests}). All steps: {step_names!r}"
        )
        assert idx_post_fix == idx_commit_tests + 1, (
            f"Expected 'run_pytest_post_fix' immediately after 'commit_fix_tests' "
            f"(positions {idx_commit_tests+1} vs {idx_post_fix}). All steps: {step_names!r}"
        )
        # GH316: verify_fix_typecheck now sits between run_pytest_post_fix and
        # build_satisfaction_prompt, so the pytest gate is 2 before satisfaction.
        idx_typecheck = step_names.index("verify_fix_typecheck")
        assert idx_typecheck == idx_post_fix + 1, (
            f"Expected 'verify_fix_typecheck' immediately after 'run_pytest_post_fix' "
            f"(positions {idx_post_fix+1} vs {idx_typecheck}). All steps: {step_names!r}"
        )
        idx_build_decorr = step_names.index("build_decorr_prompt")
        assert idx_typecheck == idx_build_decorr - 1, (
            f"Expected 'verify_fix_typecheck' immediately before 'build_decorr_prompt' "
            f"(positions {idx_build_decorr-1} vs {idx_typecheck}). All steps: {step_names!r}"
        )

    # ── AC2: red_test_paths channel ────────────────────────────────────────────

    def test_uses_red_test_paths_when_present(self, tmp_path: Path, monkeypatch) -> None:
        """When prev.data["red_test_paths"] contains py paths, step passes those
        (filtered to *.py) as argv to run_test_command — git_diff_files NOT called.

        AC2: red_test_paths is the preferred scope channel.
        """
        _run_pytest_post_fix = getattr(phase_6_review, "_run_pytest_post_fix", None)
        assert _run_pytest_post_fix is not None, (
            "_run_pytest_post_fix not found in phase_6_review — GREEN has not implemented it yet"
        )

        diff_calls: list = []
        run_test_calls: list = []

        monkeypatch.setattr(phase_6_review, "git_diff_files",
                            lambda *a, **kw: diff_calls.append(a) or [])
        monkeypatch.setattr(phase_6_review, "_emit_safe", lambda *a, **kw: None)

        fake_stdout = tmp_path / "stdout.txt"
        fake_stdout.write_text("3 passed in 0.1s")

        def _fake_run(argv, cwd, timeout=180):
            run_test_calls.append(argv)
            return _FakeTestRunResult(exit_code=0, n_passed=3, n_failed=0,
                                      stdout_path=str(fake_stdout))
        monkeypatch.setattr(phase_6_review, "run_test_command", _fake_run)

        ctx = _make_ctx(tmp_path)
        prev = _make_prev(
            pre_fix_sha=_VALID_SHA,
            red_test_paths=["tests/test_foo.py", "tests/test_bar.py", "src/not_a_test.py"],
        )

        result = _run_pytest_post_fix(ctx, prev)

        assert result.status == "ok", f"Expected ok, got {result.status!r}"
        assert len(diff_calls) == 0, (
            f"git_diff_files must NOT be called when red_test_paths is present; "
            f"was called {len(diff_calls)} time(s)"
        )
        assert len(run_test_calls) == 1, (
            f"run_test_command must be called exactly once; called {len(run_test_calls)}"
        )
        used_argv = run_test_calls[0]
        # Must include the py test paths (non-py filtered out)
        assert "tests/test_foo.py" in used_argv, f"test_foo.py missing from argv: {used_argv!r}"
        assert "tests/test_bar.py" in used_argv, f"test_bar.py missing from argv: {used_argv!r}"
        # Non-test py file may or may not be included depending on predicate implementation,
        # but src/not_a_test.py is NOT a test file — assert it's excluded
        assert "src/not_a_test.py" not in used_argv, (
            f"src/not_a_test.py (not a test file) must be filtered from argv: {used_argv!r}"
        )
        # Argv must include the canonical flags
        assert "--tb=short" in used_argv, f"--tb=short missing from argv: {used_argv!r}"
        assert "-q" in used_argv, f"-q missing from argv: {used_argv!r}"
        assert "-x" not in used_argv, f"-x must NOT appear (we want all failures): {used_argv!r}"

    # ── AC3: manifest fallback ─────────────────────────────────────────────────

    def test_falls_back_to_manifest_when_no_red_test_paths(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """When red_test_paths is absent, step uses worker_written_paths (manifest)
        filtered to test-py predicate as the scope source.

        AC3 (4961254A update): manifest fallback replaces git_diff_files fallback.
        git_diff_files is NOT called when red_test_paths is absent.
        """
        _run_pytest_post_fix = getattr(phase_6_review, "_run_pytest_post_fix", None)
        assert _run_pytest_post_fix is not None, (
            "_run_pytest_post_fix not found in phase_6_review — GREEN has not implemented it yet"
        )

        diff_calls: list = []
        run_test_calls: list = []

        # 4961254A: git_diff_files is NOT called — assert via spy
        monkeypatch.setattr(phase_6_review, "git_diff_files",
                            lambda *a, **kw: diff_calls.append(a) or [])
        monkeypatch.setattr(phase_6_review, "_emit_safe", lambda *a, **kw: None)

        fake_stdout = tmp_path / "stdout.txt"
        fake_stdout.write_text("1 passed in 0.1s")

        def _fake_run(argv, cwd, timeout=180):
            run_test_calls.append(argv)
            return _FakeTestRunResult(exit_code=0, n_passed=1, n_failed=0,
                                      stdout_path=str(fake_stdout))
        monkeypatch.setattr(phase_6_review, "run_test_command", _fake_run)

        ctx = _make_ctx(tmp_path)
        # Manifest includes a test file and a prod file; only test file expected in argv
        prev = _make_prev(
            pre_fix_sha=_VALID_SHA,
            worker_written_paths=["tests/test_alpha.py", "src/alpha.py"],
            manifest_source="harness_tool_record",  # 4C03CCED Ship 1C
        )  # no red_test_paths

        result = _run_pytest_post_fix(ctx, prev)

        assert result.status == "ok", f"Expected ok, got {result.status!r}"
        assert len(diff_calls) == 0, (
            f"git_diff_files must NOT be called (4961254A manifest path); "
            f"was called {len(diff_calls)} time(s)"
        )
        assert len(run_test_calls) == 1, (
            f"run_test_command must be called once; called {len(run_test_calls)}"
        )
        used_argv = run_test_calls[0]
        assert "tests/test_alpha.py" in used_argv, (
            f"test file from manifest must be in argv: {used_argv!r}"
        )
        assert "src/alpha.py" not in used_argv, (
            f"production file must be filtered out of argv: {used_argv!r}"
        )

    # ── AC4: pass branch ───────────────────────────────────────────────────────

    def test_pass_branch_emits_event_and_returns_ok(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """When pytest exits 0: status=ok, post_fix_pytest_pass emitted,
        prev.data passed through with post_fix_pytest.passed=True added.

        AC4: pass branch contract.
        4961254A update: uses worker_written_paths as test scope fallback.
        """
        _run_pytest_post_fix = getattr(phase_6_review, "_run_pytest_post_fix", None)
        assert _run_pytest_post_fix is not None, (
            "_run_pytest_post_fix not found in phase_6_review — GREEN has not implemented it yet"
        )

        captured: list[dict] = []
        monkeypatch.setattr(
            phase_6_review,
            "_emit_safe",
            lambda et, p, **kw: captured.append({"type": et, "payload": p}),
        )

        fake_stdout = tmp_path / "stdout.txt"
        fake_stdout.write_text("5 passed in 0.2s")

        monkeypatch.setattr(
            phase_6_review, "run_test_command",
            lambda argv, cwd, timeout=180: _FakeTestRunResult(
                exit_code=0, n_passed=5, n_failed=0, stdout_path=str(fake_stdout)
            ),
        )

        ctx = _make_ctx(tmp_path)
        # 4961254A: manifest fallback; test file in worker_written_paths
        prev = _make_prev(
            pre_fix_sha=_VALID_SHA, cycle=3, spec_path="/tmp/spec.md",
            worker_written_paths=["tests/test_x.py"],
            manifest_source="harness_tool_record",  # 4C03CCED Ship 1C
        )

        result = _run_pytest_post_fix(ctx, prev)

        assert result.status == "ok", f"Expected ok, got {result.status!r}"

        pass_events = [e for e in captured if e["type"] == "post_fix_pytest_pass"]
        assert len(pass_events) == 1, (
            f"Expected 1 post_fix_pytest_pass event; got {len(pass_events)}. "
            f"All events: {[e['type'] for e in captured]}"
        )

        pfix = (result.data or {}).get("post_fix_pytest")
        assert pfix is not None, "result.data must contain 'post_fix_pytest' key"
        assert pfix.get("passed") is True, (
            f"post_fix_pytest.passed must be True; got {pfix!r}"
        )
        assert pfix.get("n_passed") == 5, (
            f"post_fix_pytest.n_passed must be 5; got {pfix.get('n_passed')!r}"
        )
        assert pfix.get("n_failed") == 0, (
            f"post_fix_pytest.n_failed must be 0; got {pfix.get('n_failed')!r}"
        )

        # prev.data keys passed through
        assert (result.data or {}).get("spec_path") == "/tmp/spec.md", (
            "spec_path from prev.data must be preserved in result.data"
        )

    # ── AC5: regression branch — fail closed ──────────────────────────────────

    def test_regression_branch_fails_closed_with_event(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """When pytest exits non-zero with n_failed > 0: status=error,
        error_code=E_POST_FIX_PYTEST_FAILED, recoverable=False,
        post_fix_pytest_fail_with_regressions emitted with failing_tests list.

        AC5: regression branch fail-closed contract.
        4961254A update: worker_written_paths drives test scope.
        """
        _run_pytest_post_fix = getattr(phase_6_review, "_run_pytest_post_fix", None)
        assert _run_pytest_post_fix is not None, (
            "_run_pytest_post_fix not found in phase_6_review — GREEN has not implemented it yet"
        )

        captured: list[dict] = []
        monkeypatch.setattr(
            phase_6_review,
            "_emit_safe",
            lambda et, p, **kw: captured.append({"type": et, "payload": p}),
        )

        fake_stdout = tmp_path / "stdout.txt"
        fake_stdout.write_text(
            "FAILED tests/test_x.py::test_beta\nFAILED tests/test_x.py::test_gamma\n"
            "1 passed, 2 failed in 0.3s\n"
        )

        monkeypatch.setattr(
            phase_6_review, "run_test_command",
            lambda argv, cwd, timeout=180: _FakeTestRunResult(
                exit_code=1, n_passed=1, n_failed=2, stdout_path=str(fake_stdout)
            ),
        )

        ctx = _make_ctx(tmp_path)
        # 4961254A: manifest drives test scope
        prev = _make_prev(pre_fix_sha=_VALID_SHA, worker_written_paths=["tests/test_x.py"],
                          manifest_source="harness_tool_record")  # 4C03CCED Ship 1C

        result = _run_pytest_post_fix(ctx, prev)

        assert result.status == "error", f"Expected error, got {result.status!r}"
        assert result.error_code == "E_POST_FIX_PYTEST_FAILED", (
            f"Expected E_POST_FIX_PYTEST_FAILED, got {result.error_code!r}"
        )
        assert result.recoverable is False, (
            f"Expected recoverable=False; got {result.recoverable!r}"
        )

        reg_events = [e for e in captured if e["type"] == "post_fix_pytest_fail_with_regressions"]
        assert len(reg_events) == 1, (
            f"Expected 1 post_fix_pytest_fail_with_regressions event; got {len(reg_events)}. "
            f"All events: {[e['type'] for e in captured]}"
        )
        payload = reg_events[0]["payload"]
        assert payload.get("n_failed") == 2, (
            f"Event must carry n_failed=2; got {payload.get('n_failed')!r}"
        )
        assert "failing_tests" in payload, (
            f"Event must include failing_tests list; payload: {payload!r}"
        )

    # ── AC6: regression artifact ───────────────────────────────────────────────

    def test_regression_branch_writes_artifact(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """On regression, <scratchpad>/reviews/post-fix-pytest.md is written
        containing failing nodeids and stdout tail.

        AC6: artifact written on regression.
        4961254A update: worker_written_paths drives test scope.
        """
        _run_pytest_post_fix = getattr(phase_6_review, "_run_pytest_post_fix", None)
        assert _run_pytest_post_fix is not None, (
            "_run_pytest_post_fix not found in phase_6_review — GREEN has not implemented it yet"
        )

        monkeypatch.setattr(phase_6_review, "_emit_safe", lambda *a, **kw: None)

        fake_stdout = tmp_path / "stdout.txt"
        fake_stdout.write_text(
            "FAILED tests/test_x.py::test_beta\nFAILED tests/test_x.py::test_gamma\n"
            "0 passed, 2 failed in 0.3s\n"
        )

        monkeypatch.setattr(
            phase_6_review, "run_test_command",
            lambda argv, cwd, timeout=180: _FakeTestRunResult(
                exit_code=1, n_passed=0, n_failed=2, stdout_path=str(fake_stdout)
            ),
        )

        ctx = _make_ctx(tmp_path)
        # 4961254A: manifest drives test scope
        prev = _make_prev(pre_fix_sha=_VALID_SHA, worker_written_paths=["tests/test_x.py"],
                          manifest_source="harness_tool_record")  # 4C03CCED Ship 1C
        scratchpad = tmp_path / "scratch"

        result = _run_pytest_post_fix(ctx, prev)

        assert result.status == "error", f"Expected error on regression; got {result.status!r}"

        artifact = scratchpad / "reviews" / "post-fix-pytest.md"
        assert artifact.is_file(), (
            f"<scratchpad>/reviews/post-fix-pytest.md must exist after regression; "
            f"scratchpad contents: {list(scratchpad.rglob('*'))}"
        )
        content = artifact.read_text()
        # Must reference failing tests
        assert "test_beta" in content or "test_gamma" in content, (
            f"Artifact must list failing test nodeids; content preview: {content[:500]!r}"
        )

    # ── AC7: empty scope skip ──────────────────────────────────────────────────

    def test_empty_scope_skips_without_blocking(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """When no red_test_paths AND manifest has no test files:
        status=ok (pass-through), post_fix_pytest_skipped emitted with
        reason="no_test_scope". Must NOT block the build.

        AC7 (4961254A update): manifest replaces git_diff_files fallback.
        """
        _run_pytest_post_fix = getattr(phase_6_review, "_run_pytest_post_fix", None)
        assert _run_pytest_post_fix is not None, (
            "_run_pytest_post_fix not found in phase_6_review — GREEN has not implemented it yet"
        )

        captured: list[dict] = []
        monkeypatch.setattr(
            phase_6_review,
            "_emit_safe",
            lambda et, p, **kw: captured.append({"type": et, "payload": p}),
        )

        ctx = _make_ctx(tmp_path)
        # Manifest has only non-test files → no test scope
        prev = _make_prev(
            pre_fix_sha=_VALID_SHA,
            worker_written_paths=["src/impl.py", "docs/README.md"],
            manifest_source="harness_tool_record",  # 4C03CCED Ship 1C
        )  # no red_test_paths

        result = _run_pytest_post_fix(ctx, prev)

        assert result.status == "ok", (
            f"Empty-scope must return ok (pass-through); got {result.status!r}"
        )

        skip_events = [e for e in captured if e["type"] == "post_fix_pytest_skipped"]
        assert len(skip_events) == 1, (
            f"Expected 1 post_fix_pytest_skipped event; got {len(skip_events)}. "
            f"All events: {[e['type'] for e in captured]}"
        )
        payload = skip_events[0]["payload"]
        assert payload.get("reason") == "no_test_scope", (
            f"Expected reason='no_test_scope'; got {payload.get('reason')!r}"
        )

    # ── AC8: infra error — pytest missing ─────────────────────────────────────

    def test_infra_error_pytest_missing(self, tmp_path: Path, monkeypatch) -> None:
        """When run_test_command raises FileNotFoundError: status=error,
        error_code=E_POST_FIX_PYTEST_INFRA, recoverable=False,
        post_fix_pytest_infra_error emitted with reason='pytest_missing'.

        AC8: pytest binary missing branch.
        """
        _run_pytest_post_fix = getattr(phase_6_review, "_run_pytest_post_fix", None)
        assert _run_pytest_post_fix is not None, (
            "_run_pytest_post_fix not found in phase_6_review — GREEN has not implemented it yet"
        )

        captured: list[dict] = []
        monkeypatch.setattr(
            phase_6_review,
            "_emit_safe",
            lambda et, p, **kw: captured.append({"type": et, "payload": p}),
        )
        monkeypatch.setattr(
            phase_6_review, "run_test_command",
            lambda argv, cwd, timeout=180: (_ for _ in ()).throw(
                FileNotFoundError("No such file: python3")
            ),
        )

        ctx = _make_ctx(tmp_path)
        # 4961254A: manifest drives test scope
        prev = _make_prev(pre_fix_sha=_VALID_SHA, worker_written_paths=["tests/test_x.py"],
                          manifest_source="harness_tool_record")  # 4C03CCED Ship 1C

        result = _run_pytest_post_fix(ctx, prev)

        assert result.status == "error", f"Expected error, got {result.status!r}"
        assert result.error_code == "E_POST_FIX_PYTEST_INFRA", (
            f"Expected E_POST_FIX_PYTEST_INFRA, got {result.error_code!r}"
        )
        assert result.recoverable is False, f"Expected recoverable=False; got {result.recoverable!r}"

        infra_events = [e for e in captured if e["type"] == "post_fix_pytest_infra_error"]
        assert len(infra_events) == 1, (
            f"Expected 1 post_fix_pytest_infra_error event; got {len(infra_events)}. "
            f"All events: {[e['type'] for e in captured]}"
        )
        assert infra_events[0]["payload"].get("reason") == "pytest_missing", (
            f"Expected reason='pytest_missing'; got {infra_events[0]['payload']!r}"
        )

    # ── AC9: infra error — timeout ─────────────────────────────────────────────

    def test_infra_error_pytest_timeout(self, tmp_path: Path, monkeypatch) -> None:
        """When run_test_command raises subprocess.TimeoutExpired: status=error,
        error_code=E_POST_FIX_PYTEST_INFRA, post_fix_pytest_infra_error with
        reason='timeout'.

        AC9: timeout branch.
        """
        _run_pytest_post_fix = getattr(phase_6_review, "_run_pytest_post_fix", None)
        assert _run_pytest_post_fix is not None, (
            "_run_pytest_post_fix not found in phase_6_review — GREEN has not implemented it yet"
        )

        captured: list[dict] = []
        monkeypatch.setattr(
            phase_6_review,
            "_emit_safe",
            lambda et, p, **kw: captured.append({"type": et, "payload": p}),
        )
        monkeypatch.setattr(
            phase_6_review, "run_test_command",
            lambda argv, cwd, timeout=180: (_ for _ in ()).throw(
                subprocess.TimeoutExpired(cmd=argv, timeout=timeout)
            ),
        )

        ctx = _make_ctx(tmp_path)
        # 4961254A: manifest drives test scope
        prev = _make_prev(pre_fix_sha=_VALID_SHA, worker_written_paths=["tests/test_slow.py"],
                          manifest_source="harness_tool_record")  # 4C03CCED Ship 1C

        result = _run_pytest_post_fix(ctx, prev)

        assert result.status == "error", f"Expected error, got {result.status!r}"
        assert result.error_code == "E_POST_FIX_PYTEST_INFRA", (
            f"Expected E_POST_FIX_PYTEST_INFRA, got {result.error_code!r}"
        )

        infra_events = [e for e in captured if e["type"] == "post_fix_pytest_infra_error"]
        assert len(infra_events) == 1, (
            f"Expected 1 post_fix_pytest_infra_error event; got {len(infra_events)}."
        )
        assert infra_events[0]["payload"].get("reason") == "timeout", (
            f"Expected reason='timeout'; got {infra_events[0]['payload']!r}"
        )

    # ── AC10: infra error — collection failure / exit_no_failures ─────────────

    def test_infra_error_collection_failure(self, tmp_path: Path, monkeypatch) -> None:
        """When pytest exits non-zero but n_failed == 0 (collection error /
        plugin crash): status=error, error_code=E_POST_FIX_PYTEST_INFRA,
        post_fix_pytest_infra_error with reason='exit_no_failures'.

        AC10: collection error / plugin crash branch.
        """
        _run_pytest_post_fix = getattr(phase_6_review, "_run_pytest_post_fix", None)
        assert _run_pytest_post_fix is not None, (
            "_run_pytest_post_fix not found in phase_6_review — GREEN has not implemented it yet"
        )

        captured: list[dict] = []
        monkeypatch.setattr(
            phase_6_review,
            "_emit_safe",
            lambda et, p, **kw: captured.append({"type": et, "payload": p}),
        )

        fake_stdout = tmp_path / "stdout.txt"
        fake_stdout.write_text("ERROR collecting tests/test_x.py\n2 errors in 0.1s\n")

        monkeypatch.setattr(
            phase_6_review, "run_test_command",
            lambda argv, cwd, timeout=180: _FakeTestRunResult(
                exit_code=2, n_passed=0, n_failed=0, stdout_path=str(fake_stdout)
            ),
        )

        ctx = _make_ctx(tmp_path)
        # 4961254A: manifest drives test scope
        prev = _make_prev(pre_fix_sha=_VALID_SHA, worker_written_paths=["tests/test_x.py"],
                          manifest_source="harness_tool_record")  # 4C03CCED Ship 1C

        result = _run_pytest_post_fix(ctx, prev)

        assert result.status == "error", f"Expected error, got {result.status!r}"
        assert result.error_code == "E_POST_FIX_PYTEST_INFRA", (
            f"Expected E_POST_FIX_PYTEST_INFRA, got {result.error_code!r}"
        )

        infra_events = [e for e in captured if e["type"] == "post_fix_pytest_infra_error"]
        assert len(infra_events) == 1, (
            f"Expected 1 post_fix_pytest_infra_error event; got {len(infra_events)}."
        )
        assert infra_events[0]["payload"].get("reason") == "exit_no_failures", (
            f"Expected reason='exit_no_failures'; got {infra_events[0]['payload']!r}"
        )

    # ── AC11: no SHA boundary — skip without blocking ─────────────────────────

    def test_no_sha_boundary_skips_without_blocking(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """When pre_fix_sha absent from prev.data AND resolve_pre_phase_sha raises:
        status=ok (pass-through), post_fix_pytest_skipped with reason='no_sha_boundary'.

        AC11: missing SHA boundary graceful skip.
        """
        _run_pytest_post_fix = getattr(phase_6_review, "_run_pytest_post_fix", None)
        assert _run_pytest_post_fix is not None, (
            "_run_pytest_post_fix not found in phase_6_review — GREEN has not implemented it yet"
        )

        captured: list[dict] = []
        monkeypatch.setattr(
            phase_6_review,
            "_emit_safe",
            lambda et, p, **kw: captured.append({"type": et, "payload": p}),
        )
        monkeypatch.setattr(
            phase_6_review,
            "resolve_pre_phase_sha",
            lambda worktree_root: (_ for _ in ()).throw(
                RuntimeError("not a git repo")
            ),
        )

        ctx = _make_ctx(tmp_path)
        prev = MagicMock()
        prev.data = {"cycle": 1}  # no pre_fix_sha key at all

        result = _run_pytest_post_fix(ctx, prev)

        assert result.status == "ok", (
            f"No-SHA boundary must return ok (pass-through); got {result.status!r}"
        )

        skip_events = [e for e in captured if e["type"] == "post_fix_pytest_skipped"]
        assert len(skip_events) == 1, (
            f"Expected 1 post_fix_pytest_skipped; got {len(skip_events)}. "
            f"All events: {[e['type'] for e in captured]}"
        )
        assert skip_events[0]["payload"].get("reason") == "no_sha_boundary", (
            f"Expected reason='no_sha_boundary'; got {skip_events[0]['payload']!r}"
        )

    # ── AC12: prev.data not mutated ────────────────────────────────────────────

    def test_prev_data_not_mutated(self, tmp_path: Path, monkeypatch) -> None:
        """Step must not mutate prev.data in-place — result.data is a fresh dict
        with existing keys preserved plus the new 'post_fix_pytest' key.

        AC12: immutability of prev.data contract.
        """
        _run_pytest_post_fix = getattr(phase_6_review, "_run_pytest_post_fix", None)
        assert _run_pytest_post_fix is not None, (
            "_run_pytest_post_fix not found in phase_6_review — GREEN has not implemented it yet"
        )

        monkeypatch.setattr(phase_6_review, "_emit_safe", lambda *a, **kw: None)

        fake_stdout = tmp_path / "stdout.txt"
        fake_stdout.write_text("2 passed in 0.1s")

        monkeypatch.setattr(
            phase_6_review, "run_test_command",
            lambda argv, cwd, timeout=180: _FakeTestRunResult(
                exit_code=0, n_passed=2, n_failed=0, stdout_path=str(fake_stdout)
            ),
        )

        ctx = _make_ctx(tmp_path)
        # 4961254A: manifest drives test scope
        prev = _make_prev(
            pre_fix_sha=_VALID_SHA, cycle=4, spec_path="/tmp/spec.md",
            worker_written_paths=["tests/test_x.py"],
            manifest_source="harness_tool_record",  # 4C03CCED Ship 1C
        )

        # Take a deep copy of prev.data before the call
        original_data = copy.deepcopy(prev.data)

        result = _run_pytest_post_fix(ctx, prev)

        assert result.status == "ok", f"Expected ok, got {result.status!r}"

        # prev.data must not have been mutated
        assert prev.data == original_data, (
            f"prev.data was mutated in-place by _run_pytest_post_fix. "
            f"Before: {original_data!r} — After: {prev.data!r}"
        )

        # result.data must be a DIFFERENT dict object
        assert result.data is not prev.data, (
            "result.data must be a fresh dict, not the same object as prev.data"
        )

        # result.data must contain original keys
        assert result.data.get("pre_fix_sha") == _VALID_SHA, "pre_fix_sha must be in result.data"
        assert result.data.get("cycle") == 4, "cycle must be in result.data"
        assert result.data.get("spec_path") == "/tmp/spec.md", "spec_path must be in result.data"

        # result.data must contain the NEW key
        assert "post_fix_pytest" in result.data, (
            "result.data must contain 'post_fix_pytest' key"
        )

        # original must NOT have the new key
        assert "post_fix_pytest" not in original_data, (
            "post_fix_pytest must not appear in the original prev.data snapshot"
        )
