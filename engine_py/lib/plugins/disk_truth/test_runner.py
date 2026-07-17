"""test_runner.py — subprocess + framework-output parsers.

Public API:
    TestRunResult   — dataclass with exit_code, n_passed, n_failed, stdout_path, stderr_path
    TestRunner      — @runtime_checkable Protocol for injectable test runners
    run_test_command(cmd, cwd, timeout=600) -> TestRunResult
    test_subprocess_env(cwd) -> dict[str, str]
    default_test_runner() -> TestRunner
    get_test_runner() -> TestRunner
    set_default_test_runner_factory(factory) -> None
    reset_default_test_runner_factory() -> None
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Union, runtime_checkable

import config_provider


@dataclass
class TestRunResult:
    exit_code: int
    n_passed: int
    n_failed: int
    stdout_path: str
    stderr_path: str


# ── Protocol (§2.1) ──────────────────────────────────────────────────────────

@runtime_checkable
class TestRunner(Protocol):
    """Structural protocol matching run_test_command's callable contract.
    Any callable (cmd, cwd, timeout=600) -> TestRunResult satisfies it."""

    def __call__(self, cmd: Union[list[str], str], cwd: Union[str, Path],
                 timeout: int = 600) -> TestRunResult: ...


# ── framework output parsers ──────────────────────────────────────────────────

# pytest: "3 passed, 1 failed in 0.5s" / "2 passed" / "1 error"
_PYTEST_PASSED_RE = re.compile(r"(\d+)\s+passed")
_PYTEST_FAILED_RE = re.compile(r"(\d+)\s+failed")
_PYTEST_ERROR_RE = re.compile(r"(\d+)\s+error[s]?")

# bun test: "2 pass" / "0 fail"
_BUN_PASSED_RE = re.compile(r"(\d+)\s+pass(?:ed)?\b")
_BUN_FAILED_RE = re.compile(r"(\d+)\s+fail(?:ed)?\b")

# jest: "Tests: 3 passed, 1 failed, 4 total"
_JEST_PASSED_RE = re.compile(r"Tests:.*?(\d+)\s+passed")
_JEST_FAILED_RE = re.compile(r"Tests:.*?(\d+)\s+failed")


def _parse_pytest(stdout: str) -> tuple[int, int] | None:
    """Return (n_passed, n_failed) if pytest summary found, else None."""
    passed_m = _PYTEST_PASSED_RE.search(stdout)
    failed_m = _PYTEST_FAILED_RE.search(stdout)
    error_m = _PYTEST_ERROR_RE.search(stdout)
    if passed_m or failed_m or error_m:
        n_passed = int(passed_m.group(1)) if passed_m else 0
        n_failed = int(failed_m.group(1)) if failed_m else 0
        n_errors = int(error_m.group(1)) if error_m else 0
        return n_passed, n_failed + n_errors
    return None


def _parse_bun(stdout: str) -> tuple[int, int] | None:
    """Return (n_passed, n_failed) if bun test summary found, else None."""
    passed_m = _BUN_PASSED_RE.search(stdout)
    failed_m = _BUN_FAILED_RE.search(stdout)
    if passed_m or failed_m:
        n_passed = int(passed_m.group(1)) if passed_m else 0
        n_failed = int(failed_m.group(1)) if failed_m else 0
        return n_passed, n_failed
    return None


def _parse_jest(stdout: str) -> tuple[int, int] | None:
    """Return (n_passed, n_failed) if jest summary found, else None."""
    passed_m = _JEST_PASSED_RE.search(stdout)
    failed_m = _JEST_FAILED_RE.search(stdout)
    if passed_m or failed_m:
        n_passed = int(passed_m.group(1)) if passed_m else 0
        n_failed = int(failed_m.group(1)) if failed_m else 0
        return n_passed, n_failed
    return None


def _parse_output(stdout: str, exit_code: int) -> tuple[int, int]:
    """Try parsers in order; first match wins. Fallback on exit code."""
    for parser in (_parse_pytest, _parse_bun, _parse_jest):
        result = parser(stdout)
        if result is not None:
            return result
    # Fallback: bash/generic
    if exit_code == 0:
        return 0, 0
    return 0, sys.maxsize


def test_subprocess_env(cwd: Union[str, Path]) -> dict[str, str]:
    """os.environ overlaid with HAL_DIR=<cwd> so bash tests sourcing
    ${HAL_DIR:-$HOME/.claude}/... resolve to the checkout under test (the worktree),
    not the main checkout. Closes V3-052 / 04DDFA12 RCA-2 recurrence."""
    env = dict(config_provider.env_mapping())
    env["HAL_DIR"] = str(cwd)
    return env


setattr(test_subprocess_env, "__test__", False)  # not a test: pytest must not collect this test_*-named helper


# ── §2.2: concrete subprocess implementation (renamed from run_test_command) ──

def _run_test_command_subprocess(
    cmd: Union[list[str], str],
    cwd: Union[str, Path],
    timeout: int = 600,
) -> TestRunResult:
    """Run a test command and return structured results.

    Args:
        cmd: Command as list (preferred) or shell string.
        cwd: Working directory.
        timeout: Seconds before killing the process (default 600).

    Returns:
        TestRunResult with exit code, pass/fail counts, and paths to output files.
    """
    cwd_str = str(cwd)
    use_shell = isinstance(cmd, str)

    # Create temp files for stdout/stderr
    tmp_dir = tempfile.mkdtemp(prefix="disk_truth_")
    stdout_file = open(f"{tmp_dir}/stdout.txt", "w")
    stderr_file = open(f"{tmp_dir}/stderr.txt", "w")
    stdout_path = stdout_file.name
    stderr_path = stderr_file.name

    try:
        proc = subprocess.run(
            cmd,
            cwd=cwd_str,
            shell=use_shell,
            stdout=stdout_file,
            stderr=stderr_file,
            timeout=timeout,
            env=test_subprocess_env(cwd_str),
        )
        exit_code = proc.returncode
    except subprocess.TimeoutExpired:
        return TestRunResult(
            exit_code=124,
            n_passed=0,
            n_failed=sys.maxsize,
            stdout_path=stdout_path,
            stderr_path=stderr_path,
        )
    finally:
        stdout_file.close()
        stderr_file.close()

    # Read stdout for parsing
    try:
        stdout_content = open(stdout_path).read()
    except Exception:
        stdout_content = ""

    n_passed, n_failed = _parse_output(stdout_content, exit_code)

    return TestRunResult(
        exit_code=exit_code,
        n_passed=n_passed,
        n_failed=n_failed,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
    )


# ── §2.3: default factory ─────────────────────────────────────────────────────

def default_test_runner() -> TestRunner:
    """Return the default concrete runner (_run_test_command_subprocess).

    This is the default factory implementation — the HAL concrete runner.
    OSS consumers may replace the factory via `set_default_test_runner_factory`.
    """
    return _run_test_command_subprocess


# ── §2.4: Module-global registry (§1g single-source-of-truth) ────────────────

# The single mutable registry. Holds a callable () -> TestRunner.
_DEFAULT_FACTORY = default_test_runner

# Snapshot of the original factory for restoration in reset.
_ORIGINAL_FACTORY = default_test_runner


# ── §2.5: single resolver ─────────────────────────────────────────────────────

def get_test_runner() -> TestRunner:
    """Return a runner instance from the registered factory (the single resolver).

    With no override registered this returns `_run_test_command_subprocess` —
    behavior identical to calling it directly. After `set_default_test_runner_factory`
    is called, returns whatever the injected factory produces.
    """
    return _DEFAULT_FACTORY()


# ── §2.6: injectors ──────────────────────────────────────────────────────────

def set_default_test_runner_factory(factory) -> None:
    """Override the module-global factory used by `get_test_runner`.

    *factory* must be a callable taking no arguments and returning an object
    satisfying the TestRunner protocol.

    This is the OSS injection point — call `reset_default_test_runner_factory`
    in test teardown to avoid global-state pollution between tests (§1i).
    """
    global _DEFAULT_FACTORY
    _DEFAULT_FACTORY = factory


def reset_default_test_runner_factory() -> None:
    """Restore `_DEFAULT_FACTORY` to the original subprocess-based default.

    Must be called in test teardown after any `set_default_test_runner_factory`
    call to prevent global-state leakage between tests (§1i singleton-reset).
    """
    global _DEFAULT_FACTORY
    _DEFAULT_FACTORY = _ORIGINAL_FACTORY


# ── §2.7: public delegator (preserves module-level patchable name + signature) -

def run_test_command(
    cmd: Union[list[str], str],
    cwd: Union[str, Path],
    timeout: int = 600,
) -> TestRunResult:
    """Thin delegator — routes to the registered test runner via get_test_runner().

    Preserves the original module-level callable contract so existing callers
    (phase_5, phase_6, phase_8) and monkeypatch sites are unaffected.
    """
    return get_test_runner()(cmd, cwd, timeout)
