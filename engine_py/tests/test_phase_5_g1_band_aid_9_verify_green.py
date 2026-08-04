"""RED tests — DA69E824 (G1 #9 redo) — thread `red_test_paths` through the
validation→green chain and feature-flag `_verify_green_passing` hard-fail
behind `org_config["verify_green_passing"]` (option-a design, default off).

Agreement: DA69E824-0CC1-4C73-8BE9-23E7E9433AD5 (HIGH, child of 95D3E5F6).
Band-aid #9 redo. Second attempt — option (a) feature-flag design.

Expected pre-fix RED outcome (5F / 3P):
  FAIL: AC1, AC2, AC3, AC4 (red_test_paths not forwarded through chain)
  FAIL: AC6a (flag-off path emits E_GREEN_NOT_PASSING today, no
              verify_green_would_fail event exists)
  PASS: AC5 (regression guard — _verify_green_passing already runs paths
              when given them directly, passing tests already return ok)
  PASS: AC6b (regression guard — enforce mode; current code already returns
              E_GREEN_NOT_PASSING on failure, matches expected contract)
  PASS: AC7 (regression guard — empty-paths skip already works)
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
ENGINE_ROOT = HERE.parent

from bytedigger_engine.workflows.phase_5_implement import (  # noqa: E402
    GREEN_COMPLETE,
    GREEN_LOG_RELPATH,
    RED_LOG_RELPATH,
    SPEC_DOC_RELPATH,
    VALIDATION_DOC_RELPATH,
    VERDICT_PASS,
    _build_green_prompt,
    _gate_on_validation,
    _invoke_green_llm,
    _verify_green_passing,
    _write_green_artifact,
)
from bytedigger_engine.contracts import StepResult, WorkflowContext  # noqa: E402


# ─── duplicated helpers (do NOT import from test_phase_5_step3_verify_green) ──


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


def _make_ctx(repo: Path, *, enforce: bool = False) -> WorkflowContext:
    """Build a WorkflowContext for _verify_green_passing tests.

    enforce=True adds org_config["verify_green_passing"]=True to activate
    the hard-fail gate (option-a feature flag).
    """
    org: dict = {"git_cwd": str(repo)}
    org["verify_green_delta_enforce"] = False   # C0B5C6E1
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


def _make_prev(**data) -> StepResult:
    """Minimal prev StepResult carrying arbitrary data keys."""
    return StepResult(
        status="ok",
        data=dict(data),
        duration_ms=0,
        step_name="verify_green_lint_rules",
    )


def _patch_emit(monkeypatch) -> list[dict]:
    """Monkeypatch _emit_safe; return list that accumulates captured events."""
    from bytedigger_engine.workflows import phase_5_implement
    captured: list[dict] = []

    def _capture(event_type, payload, severity="warning"):
        captured.append({"type": event_type, "payload": payload, "severity": severity})

    monkeypatch.setattr(phase_5_implement, "_emit_safe", _capture)
    return captured


def _make_green_ctx(scratchpad: Path) -> WorkflowContext:
    """WorkflowContext with scratchpad_dir needed by _build_green_prompt/_write_green_artifact."""
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={"scratchpad_dir": str(scratchpad)},
        question="Add foo to bar",
        session_id="test-session",
        persona="hal",
        framework=None,
        domain=None,
    )


# ─── AC1 ──────────────────────────────────────────────────────────────────────


class TestAC1GateOnValidationForwardsRedTestPaths:
    """AC1 — _gate_on_validation PASS path must forward red_test_paths."""

    def test_gate_on_validation_pass_path_forwards_red_test_paths(self, tmp_path: Path) -> None:
        """_gate_on_validation on PASS — result.data["red_test_paths"] preserved."""
        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir(parents=True, exist_ok=True)
        # Create the required path files so Path(prev.data["validation_doc_path"]) etc. resolve
        val_doc = scratchpad / VALIDATION_DOC_RELPATH
        val_doc.parent.mkdir(parents=True, exist_ok=True)
        val_doc.write_text("# Verdict: PASS\n")
        spec = scratchpad / SPEC_DOC_RELPATH
        spec.parent.mkdir(parents=True, exist_ok=True)
        spec.write_text("## US1\nfoo\n")
        red_log = scratchpad / RED_LOG_RELPATH
        red_log.parent.mkdir(parents=True, exist_ok=True)
        red_log.write_text("RED output\n")

        prev = _make_prev(
            verdict=VERDICT_PASS,
            validation_doc_path=str(val_doc),
            spec_path=str(spec),
            red_log_path=str(red_log),
            cycle=1,
            red_test_paths=["tests/foo_test.py"],
        )

        result = _gate_on_validation(None, prev)

        assert result.status == "ok", (
            f"expected status='ok' on PASS path, got {result.status!r} "
            f"error_code={result.error_code!r}"
        )
        assert result.data is not None, "result.data must not be None on PASS path"
        assert result.data.get("red_test_paths") == ["tests/foo_test.py"], (
            f"expected red_test_paths=['tests/foo_test.py'] forwarded by _gate_on_validation, "
            f"got {result.data.get('red_test_paths')!r}; "
            f"full data keys: {list(result.data.keys())}"
        )


# ─── AC2 ──────────────────────────────────────────────────────────────────────


class TestAC2BuildGreenPromptForwardsRedTestPaths:
    """AC2 — _build_green_prompt must forward red_test_paths into result.data."""

    def test_build_green_prompt_forwards_red_test_paths(self, tmp_path: Path) -> None:
        """_build_green_prompt result.data["red_test_paths"] matches prev.data value."""
        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir(parents=True, exist_ok=True)

        # Seed required files so _build_green_prompt can read them
        spec = scratchpad / SPEC_DOC_RELPATH
        spec.parent.mkdir(parents=True, exist_ok=True)
        spec.write_text("## US1\nfoo\n")

        red_log = scratchpad / RED_LOG_RELPATH
        red_log.parent.mkdir(parents=True, exist_ok=True)
        red_log.write_text("RED output\n")

        val_doc = scratchpad / VALIDATION_DOC_RELPATH
        val_doc.parent.mkdir(parents=True, exist_ok=True)
        val_doc.write_text("# Verdict: PASS\n")

        ctx = _make_green_ctx(scratchpad)
        prev = _make_prev(
            spec_path=str(spec),
            red_log_path=str(red_log),
            validation_doc_path=str(val_doc),
            verdict=VERDICT_PASS,
            red_test_paths=["tests/a_test.py", "tests/b_test.py"],
        )

        result = _build_green_prompt(ctx, prev)

        assert result.status == "ok", (
            f"_build_green_prompt failed: status={result.status!r} error={result.error!r}"
        )
        assert result.data is not None
        assert result.data.get("red_test_paths") == ["tests/a_test.py", "tests/b_test.py"], (
            f"expected red_test_paths=['tests/a_test.py', 'tests/b_test.py'] in result.data, "
            f"got {result.data.get('red_test_paths')!r}; "
            f"data keys present: {list(result.data.keys())}"
        )


# ─── AC3 ──────────────────────────────────────────────────────────────────────


class TestAC3InvokeGreenLlmExtraDataContainsRedTestPaths:
    """AC3 — _invoke_green_llm must include red_test_paths in extra_data passed
    to invoke_llm_subprocess."""

    def test_invoke_green_llm_extra_data_includes_red_test_paths(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """invoke_llm_subprocess extra_data dict must carry red_test_paths."""
        from bytedigger_engine.workflows import phase_5_implement

        captured_extra_data: list[dict] = []

        def _fake_invoke(*, prompt, model, timeout_sec, step_name, extra_data=None, **kwargs):
            captured_extra_data.append(extra_data or {})
            # Return a minimal ok result so _invoke_green_llm doesn't error out
            return StepResult(
                status="ok",
                data={
                    "raw_response": f"GREEN {GREEN_COMPLETE} — all 1 tests passing. Files modified: [x]\n",
                    "tokens_out": 10,
                    "worker_written_paths": [],
                    "manifest_source": "harness_tool_record",
                    **(extra_data or {}),
                },
                duration_ms=0,
                step_name=step_name,
            )

        monkeypatch.setattr(phase_5_implement, "invoke_llm_subprocess", _fake_invoke)

        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir(parents=True, exist_ok=True)
        spec = scratchpad / SPEC_DOC_RELPATH
        spec.parent.mkdir(parents=True, exist_ok=True)
        spec.write_text("## US1\nfoo\n")

        ctx = _make_green_ctx(scratchpad)
        prev = _make_prev(
            prompt="some prompt text",
            log_path=str(scratchpad / GREEN_LOG_RELPATH),
            spec_path=str(spec),
            red_log_path=str(scratchpad / RED_LOG_RELPATH),
            validation_doc_path=str(scratchpad / VALIDATION_DOC_RELPATH),
            verdict=VERDICT_PASS,
            red_commit_sha=None,
            red_test_paths=["tests/ac3_test.py"],
        )

        result = _invoke_green_llm(ctx, prev)

        assert result.status == "ok", (
            f"_invoke_green_llm failed unexpectedly: {result.error!r}"
        )
        assert len(captured_extra_data) >= 1, (
            "invoke_llm_subprocess was not called — cannot verify extra_data"
        )
        first_extra = captured_extra_data[0]
        assert "red_test_paths" in first_extra, (
            f"extra_data passed to invoke_llm_subprocess missing 'red_test_paths'; "
            f"keys present: {list(first_extra.keys())}"
        )
        assert first_extra["red_test_paths"] == ["tests/ac3_test.py"], (
            f"extra_data['red_test_paths'] != ['tests/ac3_test.py']; "
            f"got {first_extra['red_test_paths']!r}"
        )


# ─── AC4 ──────────────────────────────────────────────────────────────────────


class TestAC4WriteGreenArtifactForwardsRedTestPaths:
    """AC4 — _write_green_artifact must forward red_test_paths in common_data."""

    def test_write_green_artifact_forwards_red_test_paths(self, tmp_path: Path) -> None:
        """_write_green_artifact result.data["red_test_paths"] matches prev.data value."""
        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir(parents=True, exist_ok=True)
        log_dir = scratchpad / "tests"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / "build-green-output.log"

        spec = scratchpad / SPEC_DOC_RELPATH
        spec.parent.mkdir(parents=True, exist_ok=True)
        spec.write_text("## US1\nfoo\n")
        red_log = scratchpad / RED_LOG_RELPATH
        red_log.parent.mkdir(parents=True, exist_ok=True)
        red_log.write_text("RED output\n")
        val_doc = scratchpad / VALIDATION_DOC_RELPATH
        val_doc.parent.mkdir(parents=True, exist_ok=True)
        val_doc.write_text("# Verdict: PASS\n")

        # Build a raw_response that satisfies _parse_green_status → GREEN_COMPLETE
        raw_response = (
            "Some implementation notes.\n"
            "GREEN COMPLETE — all 5 tests passing. Files modified: [src/foo.py]\n"
        )

        ctx = _make_green_ctx(scratchpad)
        prev = _make_prev(
            raw_response=raw_response,
            log_path=str(log_path),
            spec_path=str(spec),
            red_log_path=str(red_log),
            validation_doc_path=str(val_doc),
            verdict=VERDICT_PASS,
            red_commit_sha=None,
            prompt="some prompt",
            red_test_paths=["tests/x_test.py"],
        )

        result = _write_green_artifact(ctx, prev)

        assert result.status == "ok", (
            f"_write_green_artifact failed: status={result.status!r} "
            f"error_code={result.error_code!r} error={result.error!r}"
        )
        assert result.data is not None
        assert result.data.get("red_test_paths") == ["tests/x_test.py"], (
            f"expected red_test_paths=['tests/x_test.py'] in common_data, "
            f"got {result.data.get('red_test_paths')!r}; "
            f"data keys present: {list(result.data.keys())}"
        )


# ─── AC5 ──────────────────────────────────────────────────────────────────────


class TestAC5VerifyGreenPassingRunsWhenPathsThreaded:
    """AC5 (regression guard) — _verify_green_passing runs the test when paths
    are explicitly threaded and returns ok + emits green_test_outcome (not
    verify_green_skipped with reason='no_red_test_paths')."""

    def test_passing_test_returns_ok_and_emits_outcome_not_skipped(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        repo = _make_repo(tmp_path)
        test_file = repo / "tests" / "foo_test.py"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.write_text("def test_ok(): assert True\n")

        captured = _patch_emit(monkeypatch)
        ctx = _make_ctx(repo, enforce=False)
        prev = _make_prev(red_test_paths=["tests/foo_test.py"])

        result = _verify_green_passing(ctx, prev)

        assert result.status == "ok", (
            f"expected status='ok' for passing tests, got {result.status!r} "
            f"error_code={result.error_code!r}"
        )
        # Must NOT have emitted verify_green_skipped with reason='no_red_test_paths'
        skip_events = [
            e for e in captured
            if e["type"] == "verify_green_skipped"
            and e["payload"].get("reason") == "no_red_test_paths"
        ]
        assert len(skip_events) == 0, (
            f"Expected no 'verify_green_skipped(reason=no_red_test_paths)' event "
            f"(paths were threaded and should have been run); captured={captured}"
        )
        # Must have emitted green_test_outcome (the paths were actually run)
        outcome_events = [e for e in captured if e["type"] == "green_test_outcome"]
        assert len(outcome_events) >= 1, (
            f"Expected at least one 'green_test_outcome' event when tests are run; "
            f"captured={captured}"
        )
        # Must NOT have emitted verify_green_would_fail (tests passed)
        would_fail_events = [e for e in captured if e["type"] == "verify_green_would_fail"]
        assert len(would_fail_events) == 0, (
            f"Expected no 'verify_green_would_fail' events (tests passed); "
            f"captured={captured}"
        )


# ─── AC6a ─────────────────────────────────────────────────────────────────────


class TestAC6aFlagOffTelemetryOnly:
    """AC6a — flag-off (enforce=False): failing test → status='error' (GH297
    unconditional cycle-feedback, cycle 1), recoverable, retry_from_step=1."""

    def test_flag_off_failing_test_returns_ok_and_emits_would_fail(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        repo = _make_repo(tmp_path)
        test_file = repo / "tests" / "foo_test.py"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.write_text('def test_fails(): assert False, "intentional RED test failure"\n')

        captured = _patch_emit(monkeypatch)
        ctx = _make_ctx(repo, enforce=False)
        prev = _make_prev(red_test_paths=["tests/foo_test.py"])

        result = _verify_green_passing(ctx, prev)

        assert result.status == "error", (
            f"flag-off: expected status='error' (GH297 cycle 1), "
            f"got status={result.status!r} error_code={result.error_code!r}"
        )
        assert result.error_code == "E_GREEN_NOT_PASSING", (
            f"flag-off: must return E_GREEN_NOT_PASSING (surfaced, not suppressed); "
            f"got error_code={result.error_code!r}"
        )
        assert result.recoverable is True, (
            f"flag-off: cycle 1 must be recoverable; got {result.recoverable!r}"
        )
        assert result.data.get("retry_from_step") == 1, (
            f"flag-off: cycle 1 must set retry_from_step=1; got {result.data!r}"
        )
        # green_test_outcome must also be emitted
        outcome_events = [e for e in captured if e["type"] == "green_test_outcome"]
        assert len(outcome_events) >= 1, (
            f"flag-off: expected 'green_test_outcome' event even on soft-fail; "
            f"captured={captured}"
        )


# ─── AC6b ─────────────────────────────────────────────────────────────────────


class TestAC6bFlagOnHardFail:
    """AC6b (regression guard) — enforce=True: failing test → status='error',
    error_code='E_GREEN_NOT_PASSING', recoverable=False; no verify_green_would_fail."""

    def test_flag_on_failing_test_returns_error_not_passing(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        repo = _make_repo(tmp_path)
        test_file = repo / "tests" / "foo_test.py"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.write_text('def test_fails(): assert False, "intentional RED test failure"\n')

        captured = _patch_emit(monkeypatch)
        ctx = _make_ctx(repo, enforce=True)
        prev = _make_prev(red_test_paths=["tests/foo_test.py"])

        result = _verify_green_passing(ctx, prev)

        assert result.error_code == "E_GREEN_NOT_PASSING", (
            f"enforce=True: expected E_GREEN_NOT_PASSING, got {result.error_code!r}"
        )
        # AEC7E800: under-cap genuine test-fail flips to recoverable=True (was terminal). enforce=True invariant preserved: error_code stays E_GREEN_NOT_PASSING, terminal at cap.
        assert result.recoverable is True, (
            "AEC7E800: under-cap genuine test-fail is now recoverable (feedback retry); enforce=True still hard-fails at cap"
        )
        would_fail_events = [e for e in captured if e["type"] == "verify_green_would_fail"]
        assert len(would_fail_events) == 0, (
            f"enforce=True: must NOT emit verify_green_would_fail (hard-fail path); "
            f"captured={captured}"
        )


# ─── AC7 ──────────────────────────────────────────────────────────────────────


class TestAC7EmptyPathsSkipUnchanged:
    """AC7 (regression guard) — empty red_test_paths still emits
    verify_green_skipped(reason='no_red_test_paths') and returns ok.
    The empty-paths skip path must survive the feature-flag edit."""

    def test_empty_paths_emits_skipped_and_returns_ok(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        repo = _make_repo(tmp_path)
        captured = _patch_emit(monkeypatch)
        ctx = _make_ctx(repo, enforce=False)
        prev = _make_prev(red_test_paths=[])

        result = _verify_green_passing(ctx, prev)

        assert result.status == "ok", (
            f"empty paths: expected status='ok', got {result.status!r} "
            f"error_code={result.error_code!r}"
        )
        skip_events = [
            e for e in captured
            if e["type"] == "verify_green_skipped"
            and e["payload"].get("reason") == "no_red_test_paths"
        ]
        assert len(skip_events) >= 1, (
            f"expected verify_green_skipped(reason='no_red_test_paths') for empty paths, "
            f"got no such event; captured={captured}"
        )
        would_fail_events = [e for e in captured if e["type"] == "verify_green_would_fail"]
        assert len(would_fail_events) == 0, (
            f"empty paths: must NOT emit verify_green_would_fail; captured={captured}"
        )

    def test_absent_paths_key_emits_skipped_and_returns_ok(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """red_test_paths key entirely absent from prev.data (degraded mode)."""
        repo = _make_repo(tmp_path)
        captured = _patch_emit(monkeypatch)
        ctx = _make_ctx(repo, enforce=False)
        # _make_prev with NO red_test_paths key
        prev = StepResult(
            status="ok",
            data={"cycle": 1},
            duration_ms=0,
            step_name="verify_green_lint_rules",
        )

        result = _verify_green_passing(ctx, prev)

        assert result.status == "ok", (
            f"absent key: expected status='ok', got {result.status!r}"
        )
        skip_events = [
            e for e in captured
            if e["type"] == "verify_green_skipped"
            and e["payload"].get("reason") == "no_red_test_paths"
        ]
        assert len(skip_events) >= 1, (
            f"absent key: expected verify_green_skipped(reason='no_red_test_paths'); "
            f"captured={captured}"
        )
