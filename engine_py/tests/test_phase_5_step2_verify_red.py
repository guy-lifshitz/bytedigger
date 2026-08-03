"""RED tests for 95D3E5F6 Step 2 — phase_5 _verify_red_fails_mechanically red_test_outcome telemetry.

Contract: after Step 2, _verify_red_fails_mechanically emits a `red_test_outcome` telemetry
event (via _emit_safe) once per test-group. Payload contains:
  group, exit_code, n_passed, n_failed, phase (==5).

This is purely additive — existing pass/fail decision logic is UNCHANGED.
All tests MUST FAIL until GREEN implements the secondary disk_truth.run_test_command
call and _emit_safe("red_test_outcome", ...) inside _verify_red_fails_mechanically.

Do NOT implement the contract here — RED-only file.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

HERE = Path(__file__).parent
ENGINE_ROOT = HERE.parent
sys.path.insert(0, str(ENGINE_ROOT))

from bytedigger_engine.workflows.phase_5_implement import _verify_red_fails_mechanically  # noqa: E402
from bytedigger_engine.contracts import StepResult, WorkflowContext  # noqa: E402


# ─── helpers ──────────────────────────────────────────────────────────────────


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)


def _commit_file(repo: Path, relpath: str, body: str = "# x\n", msg: str = "c") -> None:
    p = repo / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    subprocess.run(["git", "add", relpath], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", msg], cwd=repo, check=True)


def _make_repo(tmp_path: Path) -> Path:
    """Minimal git repo with one committed file so HEAD is valid."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
    return repo


def _make_ctx(tmp_path: Path) -> WorkflowContext:
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={"git_cwd": str(tmp_path)},
        question="q",
        session_id="test-session",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_prev(red_test_paths) -> StepResult:
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


def _write_failing_test(repo: Path, relpath: str) -> None:
    """Write a pytest test that intentionally fails (real RED)."""
    p = repo / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('def test_intentionally_fails(): assert False, "intentionally failing red test"\n')


def _write_passing_test(repo: Path, relpath: str) -> None:
    """Write a pytest test that intentionally passes (fake RED scenario)."""
    p = repo / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('def test_intentionally_passes(): assert True\n')


def _patch_emit(monkeypatch) -> list[dict]:
    """Monkeypatch _emit_safe and return captured events list."""
    from bytedigger_engine.workflows import phase_5_implement
    captured: list[dict] = []
    monkeypatch.setattr(
        phase_5_implement,
        "_emit_safe",
        lambda et, p, severity="warning": captured.append(
            {"type": et, "payload": p, "severity": severity}
        ),
    )
    return captured


# ═══════════════════════════════════════════════════════════════════════════════
# TestRedTestOutcomeTelemetry
# ═══════════════════════════════════════════════════════════════════════════════


class TestRedTestOutcomeTelemetry:
    """_verify_red_fails_mechanically must emit `red_test_outcome` once per group."""

    def test_emits_one_event_per_group_on_happy_path(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """red_test_paths=['tests/foo_test.py'] → 1 group ('py') → exactly 1 red_test_outcome event.
        Payload must have keys: group, exit_code, n_passed, n_failed, phase.
        """
        repo = _make_repo(tmp_path)
        _write_failing_test(repo, "tests/foo_test.py")

        captured = _patch_emit(monkeypatch)
        ctx = _make_ctx(repo)
        prev = _make_prev(["tests/foo_test.py"])

        _verify_red_fails_mechanically(ctx, prev)

        outcome_events = [e for e in captured if e["type"] == "red_test_outcome"]
        assert len(outcome_events) == 1, (
            f"expected exactly 1 red_test_outcome event, got {len(outcome_events)}: {outcome_events}"
        )
        payload = outcome_events[0]["payload"]
        for key in ("group", "exit_code", "n_passed", "n_failed", "phase"):
            assert key in payload, f"payload missing key {key!r}: {payload}"

    def test_emits_event_even_on_E_RED_NOT_FAILING(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Passing test (fake RED) → function returns E_RED_NOT_FAILING BUT red_test_outcome event still fires."""
        repo = _make_repo(tmp_path)
        _write_passing_test(repo, "tests/foo_test.py")

        captured = _patch_emit(monkeypatch)
        ctx = _make_ctx(repo)
        prev = _make_prev(["tests/foo_test.py"])

        result = _verify_red_fails_mechanically(ctx, prev)

        assert result.error_code == "E_RED_NOT_FAILING", (
            f"expected E_RED_NOT_FAILING for passing test, got error_code={result.error_code!r}"
        )
        outcome_events = [e for e in captured if e["type"] == "red_test_outcome"]
        assert len(outcome_events) >= 1, (
            f"red_test_outcome event must still fire on E_RED_NOT_FAILING; captured={captured}"
        )

    def test_event_payload_phase_is_5(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Any run → captured red_test_outcome event payload has phase == 5."""
        repo = _make_repo(tmp_path)
        _write_failing_test(repo, "tests/bar_test.py")

        captured = _patch_emit(monkeypatch)
        ctx = _make_ctx(repo)
        prev = _make_prev(["tests/bar_test.py"])

        _verify_red_fails_mechanically(ctx, prev)

        outcome_events = [e for e in captured if e["type"] == "red_test_outcome"]
        assert len(outcome_events) >= 1, f"no red_test_outcome events captured: {captured}"
        assert outcome_events[0]["payload"]["phase"] == 5, (
            f"expected phase==5 in payload, got {outcome_events[0]['payload']!r}"
        )

    def test_event_payload_n_failed_positive_when_red_real(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Real failing test → n_failed >= 1 in the red_test_outcome event payload."""
        repo = _make_repo(tmp_path)
        _write_failing_test(repo, "tests/real_fail_test.py")

        captured = _patch_emit(monkeypatch)
        ctx = _make_ctx(repo)
        prev = _make_prev(["tests/real_fail_test.py"])

        _verify_red_fails_mechanically(ctx, prev)

        outcome_events = [e for e in captured if e["type"] == "red_test_outcome"]
        assert len(outcome_events) >= 1, f"no red_test_outcome events: {captured}"
        n_failed = outcome_events[0]["payload"]["n_failed"]
        assert n_failed >= 1, (
            f"expected n_failed >= 1 for real failing test, got n_failed={n_failed}"
        )

    def test_event_payload_n_failed_zero_when_red_fake(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Passing test (fake RED) → n_failed == 0 AND n_passed >= 1 in event payload."""
        repo = _make_repo(tmp_path)
        _write_passing_test(repo, "tests/fake_fail_test.py")

        captured = _patch_emit(monkeypatch)
        ctx = _make_ctx(repo)
        prev = _make_prev(["tests/fake_fail_test.py"])

        _verify_red_fails_mechanically(ctx, prev)

        outcome_events = [e for e in captured if e["type"] == "red_test_outcome"]
        assert len(outcome_events) >= 1, f"no red_test_outcome events: {captured}"
        payload = outcome_events[0]["payload"]
        assert payload["n_failed"] == 0, (
            f"expected n_failed==0 for passing test, got n_failed={payload['n_failed']}"
        )
        assert payload["n_passed"] >= 1, (
            f"expected n_passed >= 1 for passing test, got n_passed={payload['n_passed']}"
        )

    def test_telemetry_failure_does_not_block_decision(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """run_test_command raising RuntimeError → decision still works, function does not crash.

        GREEN IMPLEMENTATION CONTRACT NOTE: this test assumes GREEN imports run_test_command
        at module level in phase_5_implement.py (e.g. `from bytedigger_engine.lib.plugins.disk_truth import run_test_command`).
        If GREEN imports inside the function body instead, this monkeypatch will not take effect
        and the test may need adjustment — flag this to GREEN agent.
        """
        repo = _make_repo(tmp_path)
        _write_failing_test(repo, "tests/crash_test.py")

        captured = _patch_emit(monkeypatch)

        from bytedigger_engine.workflows import phase_5_implement

        def _boom(*args, **kwargs):
            raise RuntimeError("boom — simulated disk_truth failure")

        monkeypatch.setattr(phase_5_implement, "run_test_command", _boom)

        ctx = _make_ctx(repo)
        prev = _make_prev(["tests/crash_test.py"])

        # Should not raise — telemetry failure must be swallowed.
        result = _verify_red_fails_mechanically(ctx, prev)

        # Decision path (subprocess.run) must still work.
        assert result is not None, "function must return a StepResult, not crash"
        # Telemetry event should NOT appear (or may appear with fallback values — either is OK).
        # Primary assertion: no exception was raised (tested implicitly above).

    def test_event_group_kind_matches_inferred_plan(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """red_test_paths with .py AND .test.ts → 2 red_test_outcome events, one per group.
        Each event's `group` field matches the inferred kind ('py' and 'bun'/'ts').
        """
        from helpers.host_tools import skip_without
        skip_without("bun")
        repo = _make_repo(tmp_path)
        _write_failing_test(repo, "tests/py_test.py")
        # Write a minimal bun/ts test file that will cause bun to fail (file exists but bun may not)
        ts_test = repo / "tests" / "js_test.test.ts"
        ts_test.write_text('import { test, expect } from "bun:test";\ntest("fail", () => { expect(false).toBe(true); });\n')

        captured = _patch_emit(monkeypatch)
        ctx = _make_ctx(repo)
        prev = _make_prev(["tests/py_test.py", "tests/js_test.test.ts"])

        _verify_red_fails_mechanically(ctx, prev)

        outcome_events = [e for e in captured if e["type"] == "red_test_outcome"]
        assert len(outcome_events) == 2, (
            f"expected exactly 2 red_test_outcome events (one per group), got {len(outcome_events)}: "
            f"{[e['payload'] for e in outcome_events]}"
        )
        group_kinds = {e["payload"]["group"] for e in outcome_events}
        assert "py" in group_kinds, f"expected 'py' group in events; got groups={group_kinds}"
        # bun test runner may be identified as 'bun', 'ts', or similar
        non_py = group_kinds - {"py"}
        assert len(non_py) == 1, f"expected exactly one non-py group; got {non_py}"
