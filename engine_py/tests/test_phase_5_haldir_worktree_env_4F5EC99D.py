"""RED tests for 4F5EC99D — phase_5 test-runner passes HAL_DIR=<worktree> to bash tests.

Agreement:  4F5EC99D
Spec:       SHARED/memory/Decisions/2026-06-03_4F5EC99D_phase5_haldir_worktree_env_spec.md
ACs covered: AC1, AC2, AC3, AC4, AC5, AC6

§1q compliance: no spec_from_file_location / exec_module used.
Import idiom copied from: tests/test_phase_5_step2_verify_red.py (ENGINE_ROOT / workflows path).

Pre-GREEN expected failures:
  FAIL: AC1 — test_subprocess_env absent → ImportError (lazy import inside test)
  FAIL: AC2 — test_subprocess_env absent → ImportError (lazy import inside test)
  FAIL: AC3 — test_subprocess_env absent → ImportError (lazy import inside test)
  FAIL: AC4 — run_test_command does not inject HAL_DIR today → stdout == "" not str(tmp_path)
  PASS: AC5 — env-preservation of existing env vars is already correct (regression guard)
  FAIL: AC6 — _verify_red_fails_mechanically passes no env= kwarg today → env is None
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

HERE = Path(__file__).parent
ENGINE_ROOT = HERE.parent
sys.path.insert(0, str(ENGINE_ROOT))                      # contracts, etc.

from bytedigger_engine.lib.plugins.disk_truth import run_test_command, TestRunResult  # noqa: E402
from bytedigger_engine.contracts import StepResult, WorkflowContext               # noqa: E402


# ─── shared helpers ────────────────────────────────────────────────────────────


def _make_ctx(tmp_path: Path) -> WorkflowContext:
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={"git_cwd": str(tmp_path)},
        question="q",
        session_id="test-4F5EC99D",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_prev(red_test_paths: list[str]) -> StepResult:
    return StepResult(
        status="ok",
        data={
            "red_test_paths": red_test_paths,
            "red_log_path": "tests/build-red-output.log",
            "spec_path": "scratchpad/spec.md",
            "cycle": 1,
        },
        duration_ms=0,
        step_name="commit_red_tests",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC1 — test_subprocess_env returns os.environ + HAL_DIR=<cwd>
# ═══════════════════════════════════════════════════════════════════════════════


def test_ac1_test_subprocess_env_returns_env_with_hal_dir(tmp_path: Path) -> None:
    """AC1: test_subprocess_env(p) == {**os.environ, 'HAL_DIR': str(p)}.
    FAILS today: helper absent → ImportError."""
    from bytedigger_engine.lib.plugins.disk_truth import test_subprocess_env  # lazy — absent pre-GREEN

    expected = {**os.environ, "HAL_DIR": str(tmp_path)}
    result = test_subprocess_env(tmp_path)
    assert result == expected, (
        f"test_subprocess_env must return os.environ overlaid with HAL_DIR=<cwd>; "
        f"got HAL_DIR={result.get('HAL_DIR')!r}, expected {str(tmp_path)!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC2 — test_subprocess_env does NOT mutate os.environ
# ═══════════════════════════════════════════════════════════════════════════════


def test_ac2_test_subprocess_env_does_not_mutate_os_environ(tmp_path: Path) -> None:
    """AC2: test_subprocess_env returns a fresh dict; os.environ is unchanged.
    FAILS today: helper absent → ImportError."""
    from bytedigger_engine.lib.plugins.disk_truth import test_subprocess_env  # lazy — absent pre-GREEN

    before = dict(os.environ)
    result = test_subprocess_env(tmp_path)

    assert result is not os.environ, "must return a new dict, not os.environ itself"
    assert dict(os.environ) == before, (
        "test_subprocess_env must not mutate os.environ"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC3 — worktree path wins over pre-existing HAL_DIR in os.environ
# ═══════════════════════════════════════════════════════════════════════════════


def test_ac3_worktree_wins_over_preexisting_hal_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC3: with os.environ['HAL_DIR']=='/main', test_subprocess_env('/wt')['HAL_DIR']=='/wt'.
    FAILS today: helper absent → ImportError."""
    from bytedigger_engine.lib.plugins.disk_truth import test_subprocess_env  # lazy — absent pre-GREEN

    worktree = tmp_path / "worktree"
    worktree.mkdir()

    monkeypatch.setenv("HAL_DIR", "/main")
    result = test_subprocess_env(str(worktree))

    assert result["HAL_DIR"] == str(worktree), (
        f"worktree path must override pre-existing HAL_DIR; "
        f"got {result['HAL_DIR']!r}, expected {str(worktree)!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC4 — CHOKEPOINT: run_test_command injects HAL_DIR into child process env
# ═══════════════════════════════════════════════════════════════════════════════


def test_ac4_run_test_command_injects_hal_dir(tmp_path: Path) -> None:
    """AC4: run_test_command with cwd=tmp_path → child $HAL_DIR == str(tmp_path).
    FAILS today: run_test_command does not pass env= so HAL_DIR is inherited/empty."""
    result = run_test_command(
        ["sh", "-c", 'printf %s "$HAL_DIR"'],
        cwd=tmp_path,
    )
    actual = Path(result.stdout_path).read_text()
    assert actual == str(tmp_path), (
        f"run_test_command must inject HAL_DIR=<cwd> into subprocess env; "
        f"child saw HAL_DIR={actual!r}, expected {str(tmp_path)!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC5 — regression guard: existing env vars are preserved in child process
# ═══════════════════════════════════════════════════════════════════════════════


def test_ac5_run_test_command_preserves_existing_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC5: env-preservation — sentinel env var is visible to child after HAL_DIR injection.
    May PASS at RED (regression guard); must stay GREEN after the fix."""
    monkeypatch.setenv("HAL_SENTINEL_4F5", "z")
    result = run_test_command(
        ["sh", "-c", 'printf %s "$HAL_SENTINEL_4F5"'],
        cwd=tmp_path,
    )
    actual = Path(result.stdout_path).read_text()
    assert actual == "z", (
        f"run_test_command must preserve existing env vars; "
        f"child saw HAL_SENTINEL_4F5={actual!r}, expected 'z'"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC6 — _verify_red_fails_mechanically passes env with HAL_DIR to direct subprocess
# ═══════════════════════════════════════════════════════════════════════════════


def test_ac6_verify_red_fails_mechanically_passes_hal_dir_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC6: _verify_red_fails_mechanically passes env={'HAL_DIR': git_cwd, ...}
    to its direct subprocess.run call. FAILS today: env kwarg is absent (None)."""
    from bytedigger_engine.workflows import phase_5_implement
    from bytedigger_engine.lib.plugins.disk_truth import test_subprocess_env  # lazy — absent pre-GREEN

    captured_kwargs: list[dict] = []

    class _FakeProc:
        returncode = 1
        stdout = ""
        stderr = ""

    def _fake_subprocess_run(*args, **kwargs):
        captured_kwargs.append(kwargs)
        return _FakeProc()

    monkeypatch.setattr(phase_5_implement.subprocess, "run", _fake_subprocess_run)

    ctx = _make_ctx(tmp_path)
    prev = _make_prev(["x.sh"])

    phase_5_implement._verify_red_fails_mechanically(ctx, prev)

    assert captured_kwargs, "_verify_red_fails_mechanically must call subprocess.run"
    first_call_env = captured_kwargs[0].get("env")
    assert first_call_env is not None, (
        "_verify_red_fails_mechanically must pass env= kwarg to subprocess.run; got None"
    )
    assert first_call_env.get("HAL_DIR") == str(tmp_path), (
        f"env['HAL_DIR'] must equal git_cwd={str(tmp_path)!r}; "
        f"got {first_call_env.get('HAL_DIR')!r}"
    )
