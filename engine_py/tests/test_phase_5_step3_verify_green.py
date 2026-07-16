"""RED tests for 95D3E5F6 Step 3 — phase_5 `_verify_green_passing` new step.

Contract: GREEN will add `_verify_green_passing` as the LAST step in the
phase_5 workflow (AFTER `verify_green_lint_rules`), registered at step index 8
(0-based), making total step count == 9.

Behaviour contract:
- Reads `red_test_paths` from prev.data.
- Uses `_infer_test_command_for_paths` to derive per-language command groups.
- For each group calls `disk_truth.run_test_command(argv, git_cwd, timeout=120)`.
- exit_code==0 AND n_failed==0 → group passing.
- Any group failing → StepResult(error_code='E_GREEN_NOT_PASSING', recoverable=False).
- Skip case (no inferable command) → StepResult(status='ok') + `verify_green_skipped` event.
- Emits `green_test_outcome` telemetry per group with {group, exit_code, n_passed, n_failed, phase: 5}.
- FileNotFoundError → StepResult(error_code='E_GREEN_TEST_RUNNER_MISSING', recoverable=False).
- Timeout → StepResult(error_code='E_GREEN_TEST_TIMEOUT').
- prev is None → StepResult(error_code='E_MISSING_PREV_DATA').

All tests MUST FAIL until GREEN implements _verify_green_passing.
Do NOT implement any contract here — RED-only file.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
ENGINE_ROOT = HERE.parent
sys.path.insert(0, str(ENGINE_ROOT))
sys.path.insert(0, str(ENGINE_ROOT / "lib"))
sys.path.insert(0, str(ENGINE_ROOT / "workflows"))

from phase_5_implement import _verify_green_passing, phase_5_implement_workflow  # noqa: E402
from contracts import StepResult, WorkflowContext  # noqa: E402


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


def _make_ctx(repo: Path) -> WorkflowContext:
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={"git_cwd": str(repo), "verify_green_passing": True},
        question="q",
        session_id="test-session",
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
        step_name="verify_green_lint_rules",
    )


def _write_passing_test(repo: Path, relpath: str) -> None:
    p = repo / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("def test_ok(): assert True\n")


def _write_failing_test(repo: Path, relpath: str) -> None:
    p = repo / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text('def test_fails(): assert False, "intentional RED test failure"\n')


def _patch_emit(monkeypatch) -> list[dict]:
    import phase_5_implement
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
# TestVerifyGreenPassingFunction
# ═══════════════════════════════════════════════════════════════════════════════


class TestVerifyGreenPassingFunction:

    def test_returns_ok_when_all_tests_pass(self, tmp_path: Path, monkeypatch) -> None:
        """tmp_path repo with a passing pytest → step returns status='ok'."""
        repo = _make_repo(tmp_path)
        _write_passing_test(repo, "tests/foo_test.py")

        _patch_emit(monkeypatch)
        ctx = _make_ctx(repo)
        prev = _make_prev(["tests/foo_test.py"])

        result = _verify_green_passing(ctx, prev)

        assert result.status == "ok", (
            f"expected status='ok' for passing tests, got status={result.status!r} "
            f"error_code={result.error_code!r}"
        )

    def test_returns_error_when_tests_fail(self, tmp_path: Path, monkeypatch) -> None:
        """tmp_path repo with failing pytest → returns error_code='E_GREEN_NOT_PASSING', recoverable=True (under cap; AEC7E800 feedback loop)."""
        repo = _make_repo(tmp_path)
        _write_failing_test(repo, "tests/foo_test.py")

        _patch_emit(monkeypatch)
        ctx = _make_ctx(repo)
        prev = _make_prev(["tests/foo_test.py"])

        result = _verify_green_passing(ctx, prev)

        assert result.error_code == "E_GREEN_NOT_PASSING", (
            f"expected error_code='E_GREEN_NOT_PASSING', got {result.error_code!r}"
        )
        # AEC7E800: genuine test-fail under cycle cap flips to recoverable=True (was terminal). error_code/status unchanged.
        assert result.recoverable is True, (
            "AEC7E800: under-cap genuine test-fail is now recoverable (feedback retry); terminal only at cap"
        )

    def test_emits_green_test_outcome_event_per_group(self, tmp_path: Path, monkeypatch) -> None:
        """red_test_paths=['tests/py_test.py', 'tests/js_test.test.ts'] → 2 events, one per group."""
        repo = _make_repo(tmp_path)
        _write_passing_test(repo, "tests/py_test.py")
        ts_test = repo / "tests" / "js_test.test.ts"
        ts_test.parent.mkdir(parents=True, exist_ok=True)
        ts_test.write_text(
            'import { test, expect } from "bun:test";\n'
            'test("ok", () => { expect(true).toBe(true); });\n'
        )

        captured = _patch_emit(monkeypatch)
        ctx = _make_ctx(repo)
        prev = _make_prev(["tests/py_test.py", "tests/js_test.test.ts"])

        _verify_green_passing(ctx, prev)

        outcome_events = [e for e in captured if e["type"] == "green_test_outcome"]
        assert len(outcome_events) == 2, (
            f"expected 2 green_test_outcome events (one per group), got {len(outcome_events)}: "
            f"{[e['payload'] for e in outcome_events]}"
        )
        for ev in outcome_events:
            for key in ("group", "exit_code", "n_passed", "n_failed", "phase"):
                assert key in ev["payload"], (
                    f"event payload missing key {key!r}: {ev['payload']}"
                )

    def test_emits_event_even_on_failure_path(self, tmp_path: Path, monkeypatch) -> None:
        """Failing test → BOTH green_test_outcome event fires AND error_code='E_GREEN_NOT_PASSING'."""
        repo = _make_repo(tmp_path)
        _write_failing_test(repo, "tests/failing_test.py")

        captured = _patch_emit(monkeypatch)
        ctx = _make_ctx(repo)
        prev = _make_prev(["tests/failing_test.py"])

        result = _verify_green_passing(ctx, prev)

        assert result.error_code == "E_GREEN_NOT_PASSING", (
            f"expected E_GREEN_NOT_PASSING, got {result.error_code!r}"
        )
        outcome_events = [e for e in captured if e["type"] == "green_test_outcome"]
        assert len(outcome_events) >= 1, (
            f"green_test_outcome event must fire even on failure; captured={captured}"
        )

    def test_skipped_when_no_inferable_command(self, tmp_path: Path, monkeypatch) -> None:
        """red_test_paths=['random.json'] → no language matched → status='ok' + verify_green_skipped event."""
        repo = _make_repo(tmp_path)
        # Create the file so it exists on disk (though command is still un-inferable)
        (repo / "random.json").write_text('{"x": 1}\n')

        captured = _patch_emit(monkeypatch)
        ctx = _make_ctx(repo)
        prev = _make_prev(["random.json"])

        result = _verify_green_passing(ctx, prev)

        assert result.status == "ok", (
            f"expected status='ok' for skipped case, got {result.status!r} error_code={result.error_code!r}"
        )
        skip_events = [e for e in captured if e["type"] == "verify_green_skipped"]
        assert len(skip_events) >= 1, (
            f"expected at least 1 verify_green_skipped event; captured={captured}"
        )

    def test_e_missing_prev_data_when_prev_invalid(self, tmp_path: Path, monkeypatch) -> None:
        """prev=None → returns error_code='E_MISSING_PREV_DATA'."""
        repo = _make_repo(tmp_path)
        _patch_emit(monkeypatch)
        ctx = _make_ctx(repo)

        result = _verify_green_passing(ctx, None)

        assert result.error_code == "E_MISSING_PREV_DATA", (
            f"expected E_MISSING_PREV_DATA for None prev, got {result.error_code!r}"
        )

    def test_e_green_test_runner_missing_when_binary_absent(self, tmp_path: Path, monkeypatch) -> None:
        """subprocess.run raises FileNotFoundError → returns error_code='E_GREEN_TEST_RUNNER_MISSING'."""
        repo = _make_repo(tmp_path)
        _write_passing_test(repo, "tests/foo_test.py")

        _patch_emit(monkeypatch)

        import phase_5_implement

        def _raise_fnf(*args, **kwargs):
            raise FileNotFoundError("binary not found")

        monkeypatch.setattr(phase_5_implement.subprocess, "run", _raise_fnf)

        ctx = _make_ctx(repo)
        prev = _make_prev(["tests/foo_test.py"])

        result = _verify_green_passing(ctx, prev)

        assert result.error_code == "E_GREEN_TEST_RUNNER_MISSING", (
            f"expected E_GREEN_TEST_RUNNER_MISSING, got {result.error_code!r}"
        )
        assert result.recoverable is False, (
            f"expected recoverable=False, got {result.recoverable!r}"
        )

    def test_phase_in_payload_is_5(self, tmp_path: Path, monkeypatch) -> None:
        """Any successful run → captured green_test_outcome event payload has phase==5."""
        repo = _make_repo(tmp_path)
        _write_passing_test(repo, "tests/phase_check_test.py")

        captured = _patch_emit(monkeypatch)
        ctx = _make_ctx(repo)
        prev = _make_prev(["tests/phase_check_test.py"])

        _verify_green_passing(ctx, prev)

        outcome_events = [e for e in captured if e["type"] == "green_test_outcome"]
        assert len(outcome_events) >= 1, f"no green_test_outcome events captured: {captured}"
        assert outcome_events[0]["payload"]["phase"] == 5, (
            f"expected phase==5, got {outcome_events[0]['payload']!r}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# TestVerifyGreenPassingRegistration
# ═══════════════════════════════════════════════════════════════════════════════


class TestVerifyGreenPassingRegistration:

    def test_registered_after_verify_green_lint_rules(self) -> None:
        """verify_green_passing immediately follows verify_green_lint_rules.

        4C0056FA added commit_green_code after verify_green_passing.
        8FFC0E05 moved green_watchdog to the last position (after commit_green_code).
        gh341 inserts verify_security_lint between verify_green_lint_rules and
        verify_green_passing; the contiguous triple must hold in that order.
        """
        wf = phase_5_implement_workflow()
        step_names = [s.name for s in wf.steps]

        vgp_idx = next(
            (i for i, s in enumerate(wf.steps) if s.name == "verify_green_passing"), None
        )
        assert vgp_idx is not None, (
            f"'verify_green_passing' not found in workflow steps: {step_names}"
        )
        assert step_names[-1] == "green_watchdog", (
            f"expected 'green_watchdog' as last step (8FFC0E05), got {step_names[-1]!r}; "
            f"all steps: {step_names}"
        )
        lint_idx = next(
            (i for i, s in enumerate(wf.steps) if s.name == "verify_green_lint_rules"), None
        )
        assert lint_idx is not None, (
            f"'verify_green_lint_rules' not found in workflow steps: {step_names}"
        )
        assert step_names[lint_idx:lint_idx + 3] == [
            "verify_green_lint_rules", "verify_security_lint", "verify_green_passing"
        ], (f"expected verify_security_lint between verify_green_lint_rules and verify_green_passing (gh341), got: {step_names}")

    def test_step_count_matches_expected(self) -> None:
        """Total workflow step count == 12 (was 11 pre-gh341, 10 pre-34AEB235, 9 pre-4C0056FA, 8 pre-Step-3)."""
        wf = phase_5_implement_workflow()
        assert len(wf.steps) == 12, (
            f"expected 12 steps after gh341 adds verify_security_lint, "
            f"got {len(wf.steps)}; steps: {[s.name for s in wf.steps]}"
        )
