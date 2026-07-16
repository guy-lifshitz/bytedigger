"""RED tests for FFEA1913: poison-PATH burn-guard installed via pytest_configure.

Spec: SHARED/memory/Decisions/2026-06-20_FFEA1913_llm_burn_guard_spec.md

All five tests FAIL today (pre-GREEN) because conftest.py does not yet install
the burn-guard: HAL_LLM_BURN_GUARD is unset, PATH has no poison entry, and there
is no $GUARD/claude executable.

FAIL today:
  AC1 — HAL_LLM_BURN_GUARD unset → assertion fails before any file access
  AC2 — HAL_LLM_BURN_GUARD unset → assertion fails; shutil.which resolves real or None
  AC3 — HAL_LLM_BURN_GUARD unset/guard path check fails → assert stops before subprocess
  AC4 — HAL_LLM_BURN_GUARD unset/guard path check fails → assert stops before subprocess
  AC5 — HAL_LLM_BURN_GUARD unset → assertion fails before any file access

PASS today: none — all ACs target new behavior.

§1l (stub-passability): the UUT is the conftest.py pytest_configure hook. None of
the five tests mock, patch, or stub any symbol from conftest.py. They observe real
side-effects: env var presence, file existence, exit code, log file growth, basename.

§1i (singleton/time): no time-dependent or singleton-resource race. HAL_LLM_BURN_GUARD
is set deterministically by pytest_configure before any test body runs. AC3/AC4 guard
the invocation behind an assert — in RED state the assert fails BEFORE any subprocess
spawn, so no real binary is ever invoked.

§1q / D1CF5FDF: no not-yet-existing symbols are imported at module level. All
assertions operate on stdlib (os, shutil, subprocess, os.path) and environment
variables — no new production imports at collection time.

RED safety: tests NEVER call subprocess.run(["claude", ...]).
AC3/AC4 resolve the path via shutil.which + guard-dir prefix-check first;
only if that guard assertion passes (GREEN state) is the absolute path invoked.
In RED state, the guard assertion fails → zero subprocess spawns → $0.
"""
from __future__ import annotations

import os
import shutil
import subprocess

import pytest


# ---------------------------------------------------------------------------
# AC1: HAL_LLM_BURN_GUARD is set, names an existing dir, and <dir>/claude
#      exists and is executable.
# ---------------------------------------------------------------------------

def test_ac1_burn_guard_env_and_executable_exist():
    """AC1: pytest_configure sets HAL_LLM_BURN_GUARD to an existing dir
    containing an executable file named 'claude'.

    FAILS today: HAL_LLM_BURN_GUARD is not set (conftest.py lacks the guard).
    After GREEN: env var is set, dir exists, claude file is executable.
    """
    guard_dir = os.environ.get("HAL_LLM_BURN_GUARD", "")
    assert guard_dir, (
        "HAL_LLM_BURN_GUARD is not set. "
        "GREEN must add pytest_configure to conftest.py that creates the burn-guard dir "
        "and sets this env marker (FFEA1913 §2.1 step 4)."
    )
    assert os.path.isdir(guard_dir), (
        f"HAL_LLM_BURN_GUARD={guard_dir!r} is not an existing directory. "
        "GREEN must create the guard dir in pytest_configure."
    )
    guard_exe = os.path.join(guard_dir, "claude")
    assert os.path.isfile(guard_exe), (
        f"Guard executable not found at {guard_exe!r}. "
        "GREEN must write the poison 'claude' script inside the guard dir "
        "(FFEA1913 §2.1 step 2)."
    )
    assert os.access(guard_exe, os.X_OK), (
        f"Guard executable at {guard_exe!r} exists but is not executable (X_OK). "
        "GREEN must write it with mode 0o755 (FFEA1913 §2.1 step 2)."
    )


# ---------------------------------------------------------------------------
# AC2: shutil.which("claude") resolves inside HAL_LLM_BURN_GUARD (poison
#      shadows any real claude on PATH).
# ---------------------------------------------------------------------------

def test_ac2_which_claude_resolves_inside_guard_dir():
    """AC2: shutil.which('claude') returns a path starting with HAL_LLM_BURN_GUARD.

    FAILS today: HAL_LLM_BURN_GUARD is unset; which() resolves the real claude
    (or None if not installed) — neither starts with the guard dir.
    After GREEN: GUARD_DIR is prepended to PATH so shutil.which('claude') resolves
    the poison first (FFEA1913 §2.1 step 3).
    """
    guard_dir = os.environ.get("HAL_LLM_BURN_GUARD", "")
    assert guard_dir, (
        "HAL_LLM_BURN_GUARD is not set. "
        "Cannot verify PATH poisoning: guard not installed (FFEA1913 §2.1 step 3)."
    )
    resolved = shutil.which("claude")
    assert resolved is not None, (
        "shutil.which('claude') returned None — guard dir not on PATH or no 'claude' "
        "file in guard dir."
    )
    assert resolved.startswith(guard_dir), (
        f"shutil.which('claude')={resolved!r} does not start with guard dir "
        f"{guard_dir!r}. PATH is not correctly poisoned: GUARD_DIR must be prepended "
        "to os.environ['PATH'] in pytest_configure (FFEA1913 §2.1 step 3)."
    )


# ---------------------------------------------------------------------------
# AC3: invoking the resolved poison path exits 99 and prints BURN-GUARD to stderr.
#
# SAFETY: assert guard_dir and resolved.startswith(guard_dir) BEFORE any
# subprocess.run call. In RED state these asserts fail → zero spawns.
# ---------------------------------------------------------------------------

def test_ac3_poison_exits_99_and_emits_burn_guard_on_stderr():
    """AC3: invoking the resolved poison script returns exit code 99 and
    includes b'BURN-GUARD' in stderr.

    FAILS today: HAL_LLM_BURN_GUARD unset → guard assertion fails before spawn.
    After GREEN: resolved path is the poison script (FFEA1913 §2.1 steps 2-3);
    it exits 99 and writes 'BURN-GUARD' to stderr.

    §RED safety: subprocess.run is only reached if guard_dir is set AND the
    resolved path is inside guard_dir. In RED state the assert fails first.
    """
    guard_dir = os.environ.get("HAL_LLM_BURN_GUARD", "")
    assert guard_dir, (
        "HAL_LLM_BURN_GUARD is not set — cannot safely resolve poison path. "
        "RED state: zero spawns (FFEA1913 §3 RED-safety)."
    )
    resolved = shutil.which("claude")
    assert resolved is not None and resolved.startswith(guard_dir), (
        f"shutil.which('claude')={resolved!r} is not inside guard dir {guard_dir!r}. "
        "Cannot safely invoke — aborting to prevent real-claude spawn (FFEA1913 §3)."
    )
    # Guard assertion passed: safe to invoke the absolute resolved path.
    result = subprocess.run([resolved], capture_output=True)  # noqa: S603
    assert result.returncode == 99, (
        f"Poison script must exit with code 99; got {result.returncode}. "
        f"stderr={result.stderr!r} (FFEA1913 §2.1 step 2)."
    )
    assert b"BURN-GUARD" in result.stderr, (
        f"Poison script must write 'BURN-GUARD' to stderr; "
        f"stderr={result.stderr!r} (FFEA1913 §2.1 step 2)."
    )


# ---------------------------------------------------------------------------
# AC4: invoking the poison appends a line to <guard_dir>/burn-guard.log.
#
# SAFETY: same guard assertion as AC3 before any subprocess.run call.
# ---------------------------------------------------------------------------

def test_ac4_poison_appends_to_burn_guard_log():
    """AC4: invoking the poison script appends a line to <guard_dir>/burn-guard.log;
    log file size / line count grows across one invocation.

    FAILS today: HAL_LLM_BURN_GUARD unset → guard assertion fails before spawn.
    After GREEN: poison script appends timestamp+argv+PATH to the log file
    (FFEA1913 §2.1 step 2).

    §1i: log file state is pre-staged by reading line count before invocation;
    no timing race — growth is deterministic per subprocess call.
    §RED safety: subprocess.run only reached if guard assertions pass.
    """
    guard_dir = os.environ.get("HAL_LLM_BURN_GUARD", "")
    assert guard_dir, (
        "HAL_LLM_BURN_GUARD is not set — cannot safely resolve poison path. "
        "RED state: zero spawns (FFEA1913 §3 RED-safety)."
    )
    resolved = shutil.which("claude")
    assert resolved is not None and resolved.startswith(guard_dir), (
        f"shutil.which('claude')={resolved!r} is not inside guard dir {guard_dir!r}. "
        "Cannot safely invoke — aborting to prevent real-claude spawn (FFEA1913 §3)."
    )
    log_path = os.path.join(guard_dir, "burn-guard.log")
    # Pre-stage: capture current log size (0 if log not yet created).
    size_before = os.path.getsize(log_path) if os.path.isfile(log_path) else 0
    # Guard assertions passed: safe to invoke the absolute resolved path.
    subprocess.run([resolved], capture_output=True)  # noqa: S603
    assert os.path.isfile(log_path), (
        f"burn-guard.log not found at {log_path!r} after invoking poison script. "
        "GREEN must make the script append to the log (FFEA1913 §2.1 step 2)."
    )
    size_after = os.path.getsize(log_path)
    assert size_after > size_before, (
        f"burn-guard.log did not grow after invocation: "
        f"size_before={size_before}, size_after={size_after}. "
        "Poison script must append a line on each invocation (FFEA1913 §2.1 step 2)."
    )


# ---------------------------------------------------------------------------
# AC5: the guard binary basename is exactly "claude".
# ---------------------------------------------------------------------------

def test_ac5_guard_binary_basename_is_claude():
    """AC5: os.path.basename of the guard executable is exactly 'claude'.

    FAILS today: HAL_LLM_BURN_GUARD unset → first assertion fails.
    After GREEN: guard file written as <GUARD_DIR>/claude; basename == 'claude'
    (FFEA1913 §2.1 step 2).
    """
    guard_dir = os.environ.get("HAL_LLM_BURN_GUARD", "")
    assert guard_dir, (
        "HAL_LLM_BURN_GUARD is not set. "
        "GREEN must install the burn-guard in pytest_configure (FFEA1913 §2.1)."
    )
    guard_exe = os.path.join(guard_dir, "claude")
    assert os.path.isfile(guard_exe), (
        f"Guard executable not found at {guard_exe!r}. "
        "Cannot check basename (FFEA1913 §2.1 step 2)."
    )
    assert os.path.basename(guard_exe) == "claude", (
        f"Guard binary basename must be exactly 'claude'; "
        f"got {os.path.basename(guard_exe)!r}. "
        "The file must be named 'claude' to match argv[0] spawn patterns (FFEA1913 §2.1)."
    )
