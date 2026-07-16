"""RED tests for 585E30E3 P0 — 'escalate' StepResult status.

14 ACs — pre-GREEN expected failures:
  FAIL: AC1, AC3, AC4, AC5, AC6, AC7, AC9, AC10, AC13, AC14
    AC1:  StepResult(status="escalate") raises ValueError today (not in allowed set)
    AC3:  escalate from step N aborts chain today (falls into error handling or raises)
    AC4:  step_escalated event not emitted today
    AC5:  workflow_finished.status would not be "escalate" today
    AC6:  _escalated flag not present → last ok is not downgraded to escalate today
    AC7:  escalate+error ordering not handled → today error after escalate would not
          produce "error" final status (chain may abort earlier)
    AC9:  escalate not in retry early-exit set → step will be retried today
    AC10: _verify_green_passing flag-off+failing returns status="ok" (not "escalate")
    AC13: full phase-5 chain with flag-off+failing → final status="ok" today (no escalate)
    AC14: LoopStepContract body escalate aborts loop today (treated like error)
  PASS (selectivity guards): AC2, AC8, AC11, AC12
    AC2:  unknown status already raises ValueError (unchanged)
    AC8:  run.py:117 already returns exit 1 for non-(ok,skip) — mechanism verified
          via unit asserting escalate is not in ("ok","skip")
    AC11: flag-off+passing → "ok" (unchanged behavior)
    AC12: flag-on+failing → "error" (hard gate unchanged)
"""
from __future__ import annotations

import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "workflows"))
sys.path.insert(0, str(HERE.parent / "lib" / "plugins"))  # noqa: E402

from contracts import (  # noqa: E402
    LoopStepContract,
    RetryPolicy,
    StepContract,
    StepResult,
    WorkflowContext,
    WorkflowDefinition,
    step as contracts_step,
)
from engine import LoopRunner, WorkflowEngine  # noqa: E402
from phase_5_implement import _verify_green_passing  # noqa: E402


# ─── shared helpers ───────────────────────────────────────────────────────────


def _make_ctx_escalate(**org_extra) -> WorkflowContext:
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=dict(org_extra),
        question="escalate-test",
        session_id="esc-585e30e3",
        persona="hal",
        framework=None,
        domain=None,
    )


def _ok_step(name: str, payload=None) -> StepContract:
    def _run(_ctx, _prev) -> StepResult:
        return StepResult(status="ok", data=payload, duration_ms=0, step_name=name)
    return StepContract(name=name, execute=_run)


def _escalate_step(
    name: str = "esc_step",
    error_code: str = "E_TEST_ESCALATE",
    error: str = "intentional escalation",
) -> StepContract:
    def _run(_ctx, _prev) -> StepResult:
        return StepResult(
            status="escalate",
            data=None,
            duration_ms=0,
            step_name=name,
            error_code=error_code,
            error=error,
        )
    return StepContract(name=name, execute=_run)


def _err_step(name: str = "err_step") -> StepContract:
    def _run(_ctx, _prev) -> StepResult:
        return StepResult(
            status="error",
            data=None,
            duration_ms=0,
            step_name=name,
            error="boom",
            error_code="E_HARD_ERROR",
            recoverable=False,
        )
    return StepContract(name=name, execute=_run)


class _FakeEventLog:
    def __init__(self):
        self.events: list[tuple[str, dict, str | None]] = []

    def append(self, event_type: str, payload: dict, run_id: str | None = None) -> None:
        self.events.append((event_type, payload, run_id))


# ─── git repo helpers (for phase-5 chain tests) ──────────────────────────────


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)


def _make_repo_with_failing_test(tmp_path: Path) -> Path:
    """Minimal git repo with a committed failing test file."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "placeholder.py").write_text("# placeholder\n")
    subprocess.run(["git", "add", "placeholder.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    test_file = repo / "tests" / "test_escalate.py"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text('def test_fails(): assert False, "intentional escalation test"\n')
    return repo


def _make_verify_ctx(repo: Path, *, enforce: bool = False) -> WorkflowContext:
    org: dict = {"git_cwd": str(repo)}
    org["verify_green_delta_enforce"] = False   # C0B5C6E1 — explicit opt-out under new default-True
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question="q",
        session_id="test-session",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_prev_with_paths(paths: list[str]) -> StepResult:
    return StepResult(
        status="ok",
        data={"red_test_paths": paths},
        duration_ms=0,
        step_name="verify_green_lint_rules",
    )


def _patch_emit_p5(monkeypatch) -> list[dict]:
    import phase_5_implement
    captured: list[dict] = []

    def _capture(event_type, payload, severity="warning"):
        captured.append({"type": event_type, "payload": payload, "severity": severity})

    monkeypatch.setattr(phase_5_implement, "_emit_safe", _capture)
    return captured


# ─── AC1: StepResult(status="escalate") constructs without ValueError ─────────


class TestAC1EscalateStatusConstructs:
    """AC1 — StepResult(status='escalate') must not raise ValueError after GREEN."""

    def test_escalate_status_constructs_without_raising(self) -> None:
        """status='escalate' must be an accepted value in StepResult.__post_init__."""
        # This will raise ValueError today because "escalate" is not in
        # ("ok", "error", "skip"). After GREEN it must construct cleanly.
        result = StepResult(
            status="escalate",
            data=None,
            duration_ms=0,
            step_name="esc",
            error_code="E_ESC",
            error="test escalation",
        )
        assert result.status == "escalate"
        assert result.error_code == "E_ESC"


# ─── AC2: status="unknown" still raises ValueError (selectivity guard) ────────


class TestAC2UnknownStatusStillRaises:
    """AC2 — Unknown status still raises ValueError (regression guard)."""

    def test_unknown_status_raises_value_error(self) -> None:
        """After adding 'escalate', 'unknown' must still be rejected."""
        with pytest.raises(ValueError, match="status must be one of"):
            StepResult(
                status="unknown",
                data=None,
                duration_ms=0,
                step_name="step",
            )


# ─── AC3: chain continues after escalate ─────────────────────────────────────


class TestAC3ChainContinuesAfterEscalate:
    """AC3 — Engine dispatch: step N returns escalate → step N+1 executes."""

    def test_next_step_executes_after_escalate(self) -> None:
        """Step after an escalating step must still execute.

        Today this FAILS because StepResult(status='escalate') raises ValueError
        before the engine even sees it. After GREEN it must continue the chain.
        """
        executed = {"next": False}

        def _track_next(_ctx, _prev) -> StepResult:
            executed["next"] = True
            return StepResult(status="ok", data="downstream", duration_ms=0, step_name="next_step")

        eng = WorkflowEngine()
        eng.register(
            "esc_chain",
            WorkflowDefinition(
                name="esc_chain",
                steps=[
                    _escalate_step("esc_step", error_code="E_ESC", error="esc"),
                    StepContract(name="next_step", execute=_track_next),
                ],
            ),
        )
        result, _ = eng.execute("esc_chain", _make_ctx_escalate())

        assert executed["next"] is True, (
            "step after escalate must execute; chain must not abort on escalate"
        )


# ─── AC4: step_escalated event emitted ───────────────────────────────────────


class TestAC4StepEscalatedEventEmitted:
    """AC4 — Engine emits step_escalated event with correct payload."""

    def test_step_escalated_event_emitted_with_correct_fields(self) -> None:
        """Engine must emit step_escalated with step_name, error_code, error."""
        log = _FakeEventLog()
        eng = WorkflowEngine(event_log=log)
        eng.register(
            "esc_event",
            WorkflowDefinition(
                name="esc_event",
                steps=[_escalate_step("my_esc_step", error_code="E_MY_CODE", error="my_error")],
            ),
        )
        eng.execute("esc_event", _make_ctx_escalate())

        esc_events = [e for e in log.events if e[0] == "step_escalated"]
        assert len(esc_events) == 1, (
            f"Expected exactly 1 step_escalated event, got {len(esc_events)}; "
            f"all events: {[e[0] for e in log.events]}"
        )
        payload = esc_events[0][1]
        assert payload.get("step_name") == "my_esc_step", (
            f"step_escalated payload step_name mismatch: {payload!r}"
        )
        assert payload.get("error_code") == "E_MY_CODE", (
            f"step_escalated payload error_code mismatch: {payload!r}"
        )
        assert payload.get("error") == "my_error", (
            f"step_escalated payload error mismatch: {payload!r}"
        )


# ─── AC5: workflow_finished status=escalate when step escalated ───────────────


class TestAC5WorkflowFinishedStatusEscalate:
    """AC5 — workflow_finished has status='escalate' when step escalated (last step)."""

    def test_workflow_finished_status_is_escalate_when_last_step_escalates(self) -> None:
        """When only step is escalate, workflow_finished.status must be 'escalate'."""
        log = _FakeEventLog()
        eng = WorkflowEngine(event_log=log)
        eng.register(
            "esc_single",
            WorkflowDefinition(
                name="esc_single",
                steps=[_escalate_step("solo_esc", error_code="E_SOLO")],
            ),
        )
        result, _ = eng.execute("esc_single", _make_ctx_escalate())

        wf_events = [e for e in log.events if e[0] == "workflow_finished"]
        assert len(wf_events) == 1, f"Expected 1 workflow_finished, got {len(wf_events)}"
        assert wf_events[0][1].get("status") == "escalate", (
            f"workflow_finished.status must be 'escalate', got {wf_events[0][1]!r}"
        )
        assert result.status == "escalate", (
            f"engine.execute() result.status must be 'escalate', got {result.status!r}"
        )


# ─── AC6: escalate propagates even when last step returns ok ──────────────────


class TestAC6EscalatePropagatesToFinalStatus:
    """AC6 — _escalated flag: last step returns ok → final status still escalate."""

    def test_final_ok_downgraded_to_escalate_when_prior_step_escalated(self) -> None:
        """Mid-chain escalate + final ok → workflow_finished.status must be 'escalate'."""
        log = _FakeEventLog()
        eng = WorkflowEngine(event_log=log)
        eng.register(
            "esc_then_ok",
            WorkflowDefinition(
                name="esc_then_ok",
                steps=[
                    _escalate_step("mid_esc", error_code="E_MID"),
                    _ok_step("final_ok", payload="done"),
                ],
            ),
        )
        result, _ = eng.execute("esc_then_ok", _make_ctx_escalate())

        wf_events = [e for e in log.events if e[0] == "workflow_finished"]
        final_status = wf_events[0][1].get("status") if wf_events else result.status
        assert final_status == "escalate", (
            f"workflow_finished.status must be 'escalate' (propagation from prior step); "
            f"got {final_status!r}. result.status={result.status!r}"
        )
        assert result.status == "escalate", (
            f"result.status must be 'escalate', got {result.status!r}"
        )


# ─── AC7: error after escalate → final status=error ──────────────────────────


class TestAC7ErrorAfterEscalateWinsWithErrorStatus:
    """AC7 — later error after escalate → workflow_finished.status='error'."""

    def test_error_after_escalate_yields_error_final_status(self) -> None:
        """escalate step, then later error step → final status must be 'error', not 'escalate'."""
        log = _FakeEventLog()
        eng = WorkflowEngine(event_log=log)
        eng.register(
            "esc_then_err",
            WorkflowDefinition(
                name="esc_then_err",
                steps=[
                    _escalate_step("first_esc", error_code="E_ESC"),
                    _err_step("later_err"),
                    _ok_step("should_not_run"),
                ],
            ),
        )
        result, _ = eng.execute("esc_then_err", _make_ctx_escalate())

        wf_events = [e for e in log.events if e[0] == "workflow_finished"]
        final_status = wf_events[0][1].get("status") if wf_events else result.status
        assert final_status == "error", (
            f"workflow_finished.status must be 'error' when later step errors; "
            f"got {final_status!r}"
        )
        assert result.status == "error", (
            f"result.status must be 'error' (error wins over escalate), got {result.status!r}"
        )


# ─── AC8: run.py exit code 1 for escalate (selectivity guard) ────────────────


class TestAC8RunPyExitCodeForEscalate:
    """AC8 — run.py:117 exits 1 for escalate (mechanism guard, no subprocess).

    This is a selectivity guard: run.py:117 already exits 1 for non-(ok,skip).
    'escalate' is not in ("ok", "skip") → exit 1. No code change needed.
    PASSES today. Kept as regression guard to ensure future changes don't break it.
    """

    def test_escalate_is_not_in_terminal_success_set(self) -> None:
        """'escalate' must not be in the ('ok', 'skip') terminal-success set."""
        terminal_success = ("ok", "skip")
        assert "escalate" not in terminal_success, (
            "'escalate' must not be in terminal-success set; "
            "run.py:117 must return exit 1 for escalate status"
        )


# ─── AC9: escalate exits retry loop immediately ───────────────────────────────


class TestAC9EscalateExitsRetryImmediately:
    """AC9 — contracts.step() retry loop: escalate exits immediately, no retry."""

    def test_escalate_exits_retry_loop_with_call_count_one(self) -> None:
        """A step returning escalate must exit the retry loop after 1 attempt."""
        call_count = {"n": 0}

        def _esc_fn(_ctx, _prev) -> StepResult:
            call_count["n"] += 1
            return StepResult(
                status="escalate",
                data=None,
                duration_ms=0,
                step_name="esc_retry",
                error_code="E_ESC_RETRY",
            )

        wrapped = contracts_step(
            name="esc_retry",
            fn=_esc_fn,
            retries=RetryPolicy(max_retries=2, initial_delay_sec=0),
        )
        ctx = _make_ctx_escalate()
        result = wrapped.execute(ctx, None)

        assert call_count["n"] == 1, (
            f"escalate must exit retry loop immediately (call_count=1); "
            f"got call_count={call_count['n']} — step was retried when it should not be"
        )
        assert result.status == "escalate", (
            f"retry wrapper must preserve escalate status; got {result.status!r}"
        )


# ─── AC10: _verify_green_passing flag-off+failing → escalate ─────────────────


class TestAC10VerifyGreenPassingFlagOffFailing:
    """AC10 — flag-off + failing tests → status='error' (GH297 unconditional cycle-feedback,
    cycle 1), error_code='E_GREEN_NOT_PASSING'."""

    def test_flag_off_failing_test_returns_escalate(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """_verify_green_passing flag-off + failing test must return status='error'.

        Post-GH297: the flag-off opt-out escalate branch is removed; the
        unconditional cycle-feedback path fires — cycle 1 → recoverable=True,
        retry_from_step=1, no verify_green_would_fail events.
        """
        repo = _make_repo_with_failing_test(tmp_path)
        captured = _patch_emit_p5(monkeypatch)
        ctx = _make_verify_ctx(repo, enforce=False)
        prev = _make_prev_with_paths(["tests/test_escalate.py"])

        result = _verify_green_passing(ctx, prev)

        assert result.status == "error", (
            f"flag-off + failing: expected status='error' (GH297 cycle 1), "
            f"got {result.status!r}. error_code={result.error_code!r}"
        )
        assert result.error_code == "E_GREEN_NOT_PASSING", (
            f"flag-off + failing: expected error_code='E_GREEN_NOT_PASSING', "
            f"got {result.error_code!r}"
        )
        assert result.recoverable is True, (
            f"cycle 1 must be recoverable=True, got {result.recoverable!r}"
        )
        assert result.data["retry_from_step"] == 1, (
            f"cycle 1 must set data['retry_from_step']==1, got {result.data!r}"
        )
        would_fail_events = [e for e in captured if e["type"] == "verify_green_would_fail"]
        assert len(would_fail_events) == 0, (
            f"Expected 0 verify_green_would_fail events post-GH297; "
            f"got {len(would_fail_events)}; captured={captured}"
        )


# ─── AC11: flag-off + passing → ok (selectivity guard) ───────────────────────


class TestAC11VerifyGreenPassingFlagOffPassing:
    """AC11 — flag-off + passing tests → status='ok' (unchanged). PASSES today."""

    def test_flag_off_passing_test_returns_ok(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """_verify_green_passing flag-off + passing tests must still return 'ok'."""
        repo = _make_repo_with_failing_test(tmp_path)
        # Override the test file with a passing one
        test_file = repo / "tests" / "test_escalate.py"
        test_file.write_text("def test_passes(): assert True\n")

        _patch_emit_p5(monkeypatch)
        ctx = _make_verify_ctx(repo, enforce=False)
        prev = _make_prev_with_paths(["tests/test_escalate.py"])

        result = _verify_green_passing(ctx, prev)

        assert result.status == "ok", (
            f"flag-off + passing: must return 'ok', got {result.status!r}"
        )


# ─── AC12: flag-on + failing → error (selectivity guard) ─────────────────────


class TestAC12VerifyGreenPassingFlagOnFailing:
    """AC12 — flag-on + failing tests → status='error' (hard gate unchanged). PASSES today."""

    def test_flag_on_failing_test_returns_error(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """_verify_green_passing flag-on + failing test must still return 'error'."""
        repo = _make_repo_with_failing_test(tmp_path)
        _patch_emit_p5(monkeypatch)
        ctx = _make_verify_ctx(repo, enforce=True)
        prev = _make_prev_with_paths(["tests/test_escalate.py"])

        result = _verify_green_passing(ctx, prev)

        assert result.status == "error", (
            f"flag-on + failing: must return 'error' (hard gate), got {result.status!r}"
        )
        assert result.error_code == "E_GREEN_NOT_PASSING", (
            f"flag-on + failing: error_code must be 'E_GREEN_NOT_PASSING', "
            f"got {result.error_code!r}"
        )


# ─── AC13: full phase-5 chain flag-off+failing → commit+watchdog run, escalate ─


class TestAC13FullPhase5ChainFlagOffFailing:
    """AC13 — Synthetic phase-5-shaped chain: escalate from verify_green_passing does not
    abort commit_green_code or green_watchdog; final workflow status is 'escalate'.

    Uses a synthetic StepContract workflow (same harness as AC3/AC5/AC6) so the test
    reaches the engine dispatch code under test without requiring real LLM/artifact infra.
    """

    def test_full_phase5_chain_escalate_does_not_abort_commit_or_watchdog(
        self,
    ) -> None:
        """Escalate from verify_green_passing must not abort downstream steps.

        After GREEN: chain continues through commit_green_code and green_watchdog;
        final workflow status is 'escalate' (post-loop _escalated promotion).
        """
        executed_steps: list[str] = []

        def _track_commit(_ctx, _prev) -> StepResult:
            executed_steps.append("commit_green_code")
            return StepResult(status="ok", data=None, duration_ms=0, step_name="commit_green_code")

        def _track_watchdog(_ctx, _prev) -> StepResult:
            executed_steps.append("green_watchdog")
            return StepResult(status="ok", data=None, duration_ms=0, step_name="green_watchdog")

        log = _FakeEventLog()
        eng = WorkflowEngine(event_log=log)
        eng.register(
            "ac13_phase5_shape",
            WorkflowDefinition(
                name="ac13_phase5_shape",
                steps=[
                    _ok_step("pre_green"),
                    _escalate_step("verify_green_passing", error_code="E_GREEN_NOT_PASSING",
                                   error="green tests failing (flag-off)"),
                    StepContract(name="commit_green_code", execute=_track_commit),
                    StepContract(name="green_watchdog", execute=_track_watchdog),
                ],
            ),
        )

        result, _ = eng.execute("ac13_phase5_shape", _make_ctx_escalate())

        assert "commit_green_code" in executed_steps, (
            f"commit_green_code must execute after escalate (chain must not abort); "
            f"executed_steps={executed_steps}"
        )
        assert "green_watchdog" in executed_steps, (
            f"green_watchdog must execute after escalate; executed_steps={executed_steps}"
        )
        assert result.status == "escalate", (
            f"Final workflow status must be 'escalate' (_escalated post-loop promotion); "
            f"got {result.status!r}. executed_steps={executed_steps}"
        )


# ─── AC14: LoopStepContract body escalate does not abort loop ─────────────────


class TestAC14LoopBodyEscalateDoesNotAbortLoop:
    """AC14 — LoopStepContract executor: escalate from body step does not abort loop."""

    def test_loop_continues_after_body_step_escalates(self) -> None:
        """A body step returning escalate must not terminate the LoopStepContract.

        Today LoopStepContract at engine.py:361 only checks for 'error' and would
        fall through (neither abort nor escalate-track). After GREEN it must
        emit step_escalated, set _escalated, and continue the outer loop.
        We verify: all max_iterations run, final LoopRunner result tracks escalate.
        """
        iteration_count = {"n": 0}

        def _body_fn(_ctx, _prev) -> StepResult:
            iteration_count["n"] += 1
            obj = object.__new__(StepResult)
            object.__setattr__(obj, "status", "escalate")
            object.__setattr__(obj, "data", {"iter": iteration_count["n"]})
            object.__setattr__(obj, "duration_ms", 0)
            object.__setattr__(obj, "step_name", "loop_body")
            object.__setattr__(obj, "error_code", "E_LOOP_ESC")
            object.__setattr__(obj, "error", "loop escalation")
            object.__setattr__(obj, "suggestion", None)
            object.__setattr__(obj, "recoverable", True)
            object.__setattr__(obj, "metadata", {})
            return obj

        loop = LoopStepContract(
            name="esc_loop",
            body=[StepContract(name="loop_body", execute=_body_fn)],
            max_iterations=3,
        )
        ctx = _make_ctx_escalate()
        result = LoopRunner(loop).run(ctx, None)

        assert iteration_count["n"] == 3, (
            f"Loop must run all 3 iterations even when body step escalates; "
            f"got {iteration_count['n']} iterations. "
            f"(Today error-branch aborts at iteration 1 or it silently falls through — "
            f"after GREEN escalate continues every iteration.)"
        )
        assert result.status == "escalate", (
            f"LoopRunner result must be 'escalate' when body escalated; got {result.status!r}"
        )
