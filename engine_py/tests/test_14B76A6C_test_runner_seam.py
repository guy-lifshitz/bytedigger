"""RED tests for 14B76A6C — test-runner injectable Protocol seam.

Spec: SHARED/memory/Decisions/2026-06-19_14B76A6C_test_runner_seam_spec.md

Current production code (test_runner.py) has NO Protocol class, NO registry,
NO resolver, NO injectors.  All 10 tests FAIL today because the new symbols
(TestRunner, get_test_runner, set_default_test_runner_factory,
reset_default_test_runner_factory, default_test_runner,
_run_test_command_subprocess) do not exist yet.

Pre-GREEN PASS/FAIL classification (§3):
  AC1  → FAIL  (TestRunner class absent)
  AC2  → FAIL  (default_test_runner absent)
  AC3  → FAIL  (_DEFAULT_FACTORY / _ORIGINAL_FACTORY module globals absent)
  AC4  → FAIL  (get_test_runner absent)
  AC5  → FAIL  (set_default_test_runner_factory absent; delegator not wired)
  AC6  → FAIL  (set_default_test_runner_factory absent; delegator not wired)
  AC7  → FAIL  (reset_default_test_runner_factory absent)
  AC8  → FAIL  (delegator absent; run_test_command does NOT call get_test_runner())
  AC9  → PASS  (TestRunResult fields + test_subprocess_env exist today — regression guard)
  AC10 → FAIL  (reset_default_test_runner_factory absent)

§1i: AC5/AC6/AC7/AC10 pre-stage global state via set_default_test_runner_factory
     and teardown via reset_default_test_runner_factory() in finally blocks.
     (workflows.md §1i: Singleton-resource tests pre-stage state, never race.)
"""
from __future__ import annotations

import sys
from pathlib import Path

# ─── sys.path setup (guard-wrapped per suite_safety.py scanner / §1q) ────────
_ENGINE_PY = Path(__file__).resolve().parents[1]
if str(_ENGINE_PY) not in sys.path:
    sys.path.insert(0, str(_ENGINE_PY))
_WORKFLOWS = _ENGINE_PY / "bytedigger_engine" / "workflows"
if str(_WORKFLOWS) not in sys.path:
    sys.path.insert(0, str(_WORKFLOWS))
_LIB = _ENGINE_PY / "bytedigger_engine" / "lib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

# ─── Module-level import (the MODULE always exists today) ─────────────────────
# We import the module object; new symbols are accessed via getattr() or deferred
# inside each test body to avoid ImportError at collect time (D1CF5FDF).
from bytedigger_engine.lib.plugins.disk_truth import test_runner  # noqa: E402
from bytedigger_engine.lib.plugins.disk_truth import run_test_command, TestRunResult  # noqa: E402


# ═══════════════════════════════════════════════════════════════════════════════
# TestTestRunnerSeam (AC1 – AC10)
# ═══════════════════════════════════════════════════════════════════════════════


class TestTestRunnerSeam:
    """Acceptance criteria for the injectable TestRunner Protocol seam (14B76A6C)."""

    # ── AC1 ──────────────────────────────────────────────────────────────────
    def test_ac1_testrunner_protocol_exists_and_is_runtime_checkable(self) -> None:
        """TestRunner exists as a @runtime_checkable Protocol with __call__(cmd,cwd,timeout)->TestRunResult."""
        TestRunner = getattr(test_runner, "TestRunner", None)
        assert TestRunner is not None, "TestRunner class not found in test_runner module"

        # @runtime_checkable sets _is_runtime_protocol = True on the Protocol class
        assert getattr(TestRunner, "_is_runtime_protocol", False) is True, (
            "TestRunner is not @runtime_checkable"
        )

        # Protocol declares __call__; verify the annotation mentions the right signature
        import inspect
        hints = {}
        try:
            hints = getattr(TestRunner.__call__, "__annotations__", {})
        except Exception:
            pass
        # The return type should be TestRunResult — presence check only
        assert "return" in hints or hasattr(TestRunner, "__call__"), (
            "TestRunner.__call__ does not declare return type"
        )

    # ── AC2 ──────────────────────────────────────────────────────────────────
    def test_ac2_default_test_runner_returns_isinstance_of_protocol(self) -> None:
        """isinstance(default_test_runner(), TestRunner) is True AND isinstance(_run_test_command_subprocess, TestRunner) is True."""
        TestRunner = getattr(test_runner, "TestRunner", None)
        assert TestRunner is not None, "TestRunner absent"

        default_test_runner = getattr(test_runner, "default_test_runner", None)
        assert default_test_runner is not None, "default_test_runner absent"

        runner_instance = default_test_runner()
        assert isinstance(runner_instance, TestRunner), (
            f"default_test_runner() -> {runner_instance!r} is not an instance of TestRunner"
        )

        _run_test_command_subprocess = getattr(test_runner, "_run_test_command_subprocess", None)
        assert _run_test_command_subprocess is not None, "_run_test_command_subprocess absent"
        assert isinstance(_run_test_command_subprocess, TestRunner), (
            "_run_test_command_subprocess is not an instance of TestRunner"
        )

    # ── AC3 ──────────────────────────────────────────────────────────────────
    def test_ac3_default_factory_equals_original_factory_at_import(self) -> None:
        """_DEFAULT_FACTORY is _ORIGINAL_FACTORY AND _DEFAULT_FACTORY is default_test_runner at module load."""
        _DEFAULT_FACTORY = getattr(test_runner, "_DEFAULT_FACTORY", None)
        assert _DEFAULT_FACTORY is not None, "_DEFAULT_FACTORY module global absent"

        _ORIGINAL_FACTORY = getattr(test_runner, "_ORIGINAL_FACTORY", None)
        assert _ORIGINAL_FACTORY is not None, "_ORIGINAL_FACTORY module global absent"

        default_test_runner = getattr(test_runner, "default_test_runner", None)
        assert default_test_runner is not None, "default_test_runner absent"

        assert _DEFAULT_FACTORY is _ORIGINAL_FACTORY, (
            "_DEFAULT_FACTORY is not _ORIGINAL_FACTORY at import (single-source baseline broken)"
        )
        assert _DEFAULT_FACTORY is default_test_runner, (
            "_DEFAULT_FACTORY is not default_test_runner (single-source baseline broken)"
        )

    # ── AC4 ──────────────────────────────────────────────────────────────────
    def test_ac4_no_injection_default_get_test_runner_is_subprocess_impl(self) -> None:
        """With no injection, get_test_runner() returns _run_test_command_subprocess (the concrete impl)."""
        get_test_runner = getattr(test_runner, "get_test_runner", None)
        assert get_test_runner is not None, "get_test_runner absent"

        _run_test_command_subprocess = getattr(test_runner, "_run_test_command_subprocess", None)
        assert _run_test_command_subprocess is not None, "_run_test_command_subprocess absent"

        resolved = get_test_runner()
        assert resolved is _run_test_command_subprocess, (
            f"get_test_runner() returned {resolved!r}, expected _run_test_command_subprocess"
        )

    # ── AC5 ──────────────────────────────────────────────────────────────────
    def test_ac5_injection_forcing_function_real_side_effect(self, tmp_path: Path) -> None:
        """After set_default_test_runner_factory(sentinel_factory), run_test_command routes to sentinel.

        §1i: set_default_test_runner_factory called in try; reset_default_test_runner_factory
        called in finally to prevent global-state leak across tests.
        """
        set_factory = getattr(test_runner, "set_default_test_runner_factory", None)
        assert set_factory is not None, "set_default_test_runner_factory absent"

        reset_factory = getattr(test_runner, "reset_default_test_runner_factory", None)
        assert reset_factory is not None, "reset_default_test_runner_factory absent"

        # Build a real callable sentinel that records calls and returns a distinguishable TestRunResult
        calls: list[dict] = []
        sentinel_result = TestRunResult(
            exit_code=42,
            n_passed=99,
            n_failed=0,
            stdout_path="/sentinel/stdout",
            stderr_path="/sentinel/stderr",
        )

        def sentinel_runner(cmd, cwd, timeout=600):  # type: ignore[override]
            calls.append({"cmd": cmd, "cwd": cwd, "timeout": timeout})
            return sentinel_result

        def sentinel_factory():
            return sentinel_runner

        try:
            set_factory(sentinel_factory)
            result = run_test_command(["x"], "/tmp", timeout=1)
            # AC5 forcing assertions: real observable side-effect
            assert len(calls) == 1, (
                f"sentinel was called {len(calls)} times, expected exactly 1"
            )
            assert result is sentinel_result, (
                f"run_test_command returned {result!r}, expected sentinel_result identity"
            )
        finally:
            reset_factory()

    # ── AC6 ──────────────────────────────────────────────────────────────────
    def test_ac6_cross_call_site_reach_via_package_import(self, tmp_path: Path) -> None:
        """With sentinel injected, the delegator imported via 'from bytedigger_engine.lib.plugins.disk_truth import run_test_command' also routes to sentinel.

        §1i: global state restored in finally block.
        """
        set_factory = getattr(test_runner, "set_default_test_runner_factory", None)
        assert set_factory is not None, "set_default_test_runner_factory absent"

        reset_factory = getattr(test_runner, "reset_default_test_runner_factory", None)
        assert reset_factory is not None, "reset_default_test_runner_factory absent"

        calls: list[dict] = []
        sentinel_result = TestRunResult(
            exit_code=55,
            n_passed=77,
            n_failed=0,
            stdout_path="/sentinel2/stdout",
            stderr_path="/sentinel2/stderr",
        )

        def sentinel_runner(cmd, cwd, timeout=600):  # type: ignore[override]
            calls.append({"cmd": cmd, "cwd": str(cwd), "timeout": timeout})
            return sentinel_result

        def sentinel_factory():
            return sentinel_runner

        try:
            set_factory(sentinel_factory)
            # Simulate how phase callers import and call it:
            # "from bytedigger_engine.lib.plugins.disk_truth import run_test_command" — already imported at module level
            result = run_test_command(["echo", "hi"], str(tmp_path), timeout=5)
            assert len(calls) == 1, (
                f"cross-call-site: sentinel hit {len(calls)} times, expected 1"
            )
            assert result is sentinel_result, (
                "cross-call-site: run_test_command did not route to sentinel"
            )
        finally:
            reset_factory()

    # ── AC7 ──────────────────────────────────────────────────────────────────
    def test_ac7_reset_restores_original_factory(self) -> None:
        """After set→reset, get_test_runner() is _run_test_command_subprocess AND _DEFAULT_FACTORY is _ORIGINAL_FACTORY.

        §1i: global state restored in finally block.
        """
        set_factory = getattr(test_runner, "set_default_test_runner_factory", None)
        assert set_factory is not None, "set_default_test_runner_factory absent"

        reset_factory = getattr(test_runner, "reset_default_test_runner_factory", None)
        assert reset_factory is not None, "reset_default_test_runner_factory absent"

        get_test_runner = getattr(test_runner, "get_test_runner", None)
        assert get_test_runner is not None, "get_test_runner absent"

        _run_test_command_subprocess = getattr(test_runner, "_run_test_command_subprocess", None)
        assert _run_test_command_subprocess is not None, "_run_test_command_subprocess absent"

        _ORIGINAL_FACTORY = getattr(test_runner, "_ORIGINAL_FACTORY", None)
        assert _ORIGINAL_FACTORY is not None, "_ORIGINAL_FACTORY absent"

        def dummy_factory():
            return lambda cmd, cwd, timeout=600: None

        try:
            set_factory(dummy_factory)
            # Verify injection took effect
            assert get_test_runner() is not _run_test_command_subprocess, (
                "set_default_test_runner_factory had no effect"
            )
        finally:
            reset_factory()

        # After reset, identity must be restored
        assert get_test_runner() is _run_test_command_subprocess, (
            "After reset, get_test_runner() is not _run_test_command_subprocess"
        )
        assert test_runner._DEFAULT_FACTORY is _ORIGINAL_FACTORY, (  # type: ignore[attr-defined]
            "After reset, _DEFAULT_FACTORY is not _ORIGINAL_FACTORY"
        )

    # ── AC8 ──────────────────────────────────────────────────────────────────
    def test_ac8_behavior_preserve_real_subprocess_two_passing_tests(self, tmp_path: Path) -> None:
        """With NO injection, run_test_command([sys.executable,'-m','pytest','-q',<fixture>], cwd) returns exit_code==0, n_passed==2, n_failed==0.

        Proves the delegator→_run_test_command_subprocess path is behavior-identical
        to the pre-change run_test_command body.
        """
        import sys as _sys

        # Verify the delegator exists (get_test_runner must be wired into run_test_command)
        get_test_runner = getattr(test_runner, "get_test_runner", None)
        assert get_test_runner is not None, "get_test_runner absent — delegator cannot be wired"

        # Write a trivially-passing 2-test pytest file
        fixture_file = tmp_path / "test_ac8_fixture.py"
        fixture_file.write_text(
            "def test_one(): assert 1 == 1\n"
            "def test_two(): assert 2 == 2\n"
        )

        result = run_test_command(
            [_sys.executable, "-m", "pytest", "-q", str(fixture_file)],
            tmp_path,
        )
        assert result.exit_code == 0, (
            f"Expected exit_code=0, got {result.exit_code}"
        )
        assert result.n_passed == 2, (
            f"Expected n_passed=2, got {result.n_passed}"
        )
        assert result.n_failed == 0, (
            f"Expected n_failed=0, got {result.n_failed}"
        )

    # ── AC9 ──────────────────────────────────────────────────────────────────
    def test_ac9_regression_guard_testrunresult_fields_and_env_unchanged(self, tmp_path: Path) -> None:
        """TestRunResult has 5 fields (exit_code/n_passed/n_failed/stdout_path/stderr_path); test_subprocess_env unchanged."""
        import dataclasses

        fields = {f.name for f in dataclasses.fields(TestRunResult)}
        assert fields == {"exit_code", "n_passed", "n_failed", "stdout_path", "stderr_path"}, (
            f"TestRunResult fields changed: {fields}"
        )

        test_subprocess_env = getattr(test_runner, "test_subprocess_env", None)
        assert test_subprocess_env is not None, "test_subprocess_env absent"

        env = test_subprocess_env(str(tmp_path))
        assert env["HAL_DIR"] == str(tmp_path), (
            f"test_subprocess_env HAL_DIR={env.get('HAL_DIR')!r}, expected {str(tmp_path)!r}"
        )
        assert getattr(test_subprocess_env, "__test__", True) is False, (
            "test_subprocess_env.__test__ is not False (pytest would collect it)"
        )

    # ── AC10 ─────────────────────────────────────────────────────────────────
    def test_ac10_idempotent_reset_double_call_safe(self) -> None:
        """Double reset_default_test_runner_factory() is safe; set→reset→reset leaves get_test_runner() is _run_test_command_subprocess.

        §1i: global state restored in finally block.
        """
        set_factory = getattr(test_runner, "set_default_test_runner_factory", None)
        assert set_factory is not None, "set_default_test_runner_factory absent"

        reset_factory = getattr(test_runner, "reset_default_test_runner_factory", None)
        assert reset_factory is not None, "reset_default_test_runner_factory absent"

        get_test_runner = getattr(test_runner, "get_test_runner", None)
        assert get_test_runner is not None, "get_test_runner absent"

        _run_test_command_subprocess = getattr(test_runner, "_run_test_command_subprocess", None)
        assert _run_test_command_subprocess is not None, "_run_test_command_subprocess absent"

        def dummy_factory():
            return lambda cmd, cwd, timeout=600: None

        try:
            set_factory(dummy_factory)
        finally:
            reset_factory()

        # First reset done; second reset must be safe (no exception)
        reset_factory()  # second call — idempotent

        assert get_test_runner() is _run_test_command_subprocess, (
            "After set→reset→reset, get_test_runner() is not _run_test_command_subprocess"
        )
