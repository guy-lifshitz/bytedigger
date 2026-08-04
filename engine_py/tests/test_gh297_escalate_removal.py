"""RED tests for GH297 — remove production escalate branch from _verify_green_passing.

Agreement 55EB335C-7504-4451-8E87-35204C2422CB. Spec:
SHARED/memory/Decisions/2026-07-05_gh297_escalate_removal_spec.md

6 ACs — pre-GREEN expected:
  FAIL: AC1, AC2, AC3, AC4
    AC1: flag-off + failing, cycle 1 → today returns status='escalate' (opt-out branch),
         not the unconditional cycle-feedback status='error'/recoverable=True/retry_from_step=1.
    AC2: today the opt-out branch emits exactly 1 verify_green_would_fail event; after
         GREEN removal there must be ZERO such events.
    AC3: phase_5_implement.py source today STILL contains 'verify_green_would_fail',
         'status="escalate"', and 'cfg.get("verify_green_passing"' — all three must be
         absent after GREEN (the StepContract name 'verify_green_passing' stays).
    AC4: flag-off + failing + cycle_count staged at cap → today returns status='escalate'
         (opt-out branch fires unconditionally regardless of cycle_count), not the
         terminal cycle-cap status='error'/recoverable=False.
  PASS (selectivity guards, unaffected by GH297):
    AC5: default cfg (no flag key) + failing, cycle 1 → already status='error'/recoverable=True
         (AEC7E800 unconditional-by-default path — flag defaults True today per C0B5C6E1).
    AC6: flag-off + all tests passing → already status='ok' (unaffected code path).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

from bytedigger_engine.contracts import StepResult, WorkflowContext  # noqa: E402
from bytedigger_engine.workflows.phase_5_implement import _verify_green_passing, GREEN_TEST_CYCLE_CAP  # noqa: E402

_PROD_FILE = HERE.parent / "bytedigger_engine" / "workflows" / "phase_5_implement.py"


# ─── shared helpers (copied shape from test_phase_5_p0_escalate_585E30E3.py) ──


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)


def _make_repo_with_failing_test(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    _init_repo(repo)
    (repo / "placeholder.py").write_text("# placeholder\n")
    subprocess.run(["git", "add", "placeholder.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    test_file = repo / "tests" / "test_gh297.py"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text('def test_fails(): assert False, "intentional gh297 test failure"\n')
    return repo


def _make_verify_ctx(repo: Path, *, enforce: bool | None = False) -> WorkflowContext:
    org: dict = {"git_cwd": str(repo)}
    if enforce is not None:
        org["verify_green_passing"] = enforce
    org["verify_green_delta_enforce"] = False
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question="q",
        session_id="test-gh297",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_prev_with_paths(paths: list[str], *, cycle_count: int | None = None) -> StepResult:
    data: dict = {"red_test_paths": paths}
    if cycle_count is not None:
        data["cycle_count"] = cycle_count
    return StepResult(
        status="ok",
        data=data,
        duration_ms=0,
        step_name="verify_green_lint_rules",
    )


def _patch_emit_p5(monkeypatch) -> list[dict]:
    from bytedigger_engine.workflows import phase_5_implement
    captured: list[dict] = []

    def _capture(event_type, payload, severity="warning"):
        captured.append({"type": event_type, "payload": payload, "severity": severity})

    monkeypatch.setattr(phase_5_implement, "_emit_safe", _capture)
    return captured


# ─── AC1 ───────────────────────────────────────────────────────────────────────


class TestAC1FlagOffFailingCycle1ReturnsError:
    """AC1 — flag-off + failing test, cycle 1 → status='error'/recoverable=True/
    retry_from_step=1 (unconditional cycle-feedback), NOT 'escalate'."""

    def test_flag_off_failing_cycle1_returns_error_not_escalate(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        repo = _make_repo_with_failing_test(tmp_path)
        _patch_emit_p5(monkeypatch)
        ctx = _make_verify_ctx(repo, enforce=False)
        prev = _make_prev_with_paths(["tests/test_gh297.py"])

        result = _verify_green_passing(ctx, prev)

        assert result.status == "error", (
            f"flag-off + failing (cycle 1): expected status='error' (GH297 removal), "
            f"got {result.status!r}"
        )
        assert result.error_code == "E_GREEN_NOT_PASSING", (
            f"expected error_code='E_GREEN_NOT_PASSING', got {result.error_code!r}"
        )
        assert result.recoverable is True, (
            f"cycle 1 must be recoverable=True, got {result.recoverable!r}"
        )
        assert result.data.get("retry_from_step") == 1, (
            f"cycle 1 must set data['retry_from_step']==1, got {result.data!r}"
        )


# ─── AC2 ───────────────────────────────────────────────────────────────────────


class TestAC2NoWouldFailEventEmitted:
    """AC2 — same fixture as AC1 → zero verify_green_would_fail events emitted."""

    def test_no_would_fail_events_emitted(self, tmp_path: Path, monkeypatch) -> None:
        repo = _make_repo_with_failing_test(tmp_path)
        captured = _patch_emit_p5(monkeypatch)
        ctx = _make_verify_ctx(repo, enforce=False)
        prev = _make_prev_with_paths(["tests/test_gh297.py"])

        _verify_green_passing(ctx, prev)

        would_fail_events = [e for e in captured if e["type"] == "verify_green_would_fail"]
        assert len(would_fail_events) == 0, (
            f"expected 0 verify_green_would_fail events post-GH297; "
            f"got {len(would_fail_events)}; captured={captured}"
        )


# ─── AC3 ───────────────────────────────────────────────────────────────────────


class TestAC3SourceTextRemoved:
    """AC3 — phase_5_implement.py source contains NO 'verify_green_would_fail',
    NO 'status="escalate"', NO 'cfg.get("verify_green_passing"' substring; the
    StepContract step name 'verify_green_passing' MUST remain present."""

    def test_source_has_no_would_fail_no_escalate_status_no_flag_read(self) -> None:
        source = _PROD_FILE.read_text()

        assert "verify_green_would_fail" not in source, (
            "phase_5_implement.py must not contain the 'verify_green_would_fail' "
            "token after GH297 removal"
        )
        assert 'status="escalate"' not in source, (
            "phase_5_implement.py must not contain a 'status=\"escalate\"' return "
            "after GH297 removal"
        )
        assert 'cfg.get("verify_green_passing"' not in source, (
            "phase_5_implement.py must not read the verify_green_passing cfg flag "
            "after GH297 removal (gate deleted, body unconditional)"
        )
        # Step name at StepContract registration must survive.
        assert 'name="verify_green_passing"' in source, (
            "the StepContract step name 'verify_green_passing' must remain present "
            "(only the cfg-flag read dies, not the step identity)"
        )


# ─── AC4 ───────────────────────────────────────────────────────────────────────


class TestAC4FlagOffFailingAtCapReturnsTerminalError:
    """AC4 — flag-off + failing test + cycle_count staged at GREEN_TEST_CYCLE_CAP
    → status='error', recoverable=False (terminal), NOT 'escalate'."""

    def test_flag_off_failing_at_cap_returns_terminal_error(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        repo = _make_repo_with_failing_test(tmp_path)
        _patch_emit_p5(monkeypatch)
        ctx = _make_verify_ctx(repo, enforce=False)
        prev = _make_prev_with_paths(
            ["tests/test_gh297.py"], cycle_count=GREEN_TEST_CYCLE_CAP
        )

        result = _verify_green_passing(ctx, prev)

        assert result.status == "error", (
            f"flag-off + failing at cap: expected status='error' (terminal), "
            f"got {result.status!r}"
        )
        assert result.error_code == "E_GREEN_NOT_PASSING", (
            f"expected error_code='E_GREEN_NOT_PASSING', got {result.error_code!r}"
        )
        assert result.recoverable is False, (
            f"at cap: expected recoverable=False (terminal abort), "
            f"got {result.recoverable!r}"
        )


# ─── AC5 (pin) ─────────────────────────────────────────────────────────────────


class TestAC5DefaultFlagFailingCycle1Pin:
    """AC5 (pin) — default cfg (no verify_green_passing key) + failing, cycle 1
    → status='error'/recoverable=True. Behavior identical pre/post GH297."""

    def test_default_cfg_failing_cycle1_returns_error(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        repo = _make_repo_with_failing_test(tmp_path)
        _patch_emit_p5(monkeypatch)
        ctx = _make_verify_ctx(repo, enforce=None)  # no verify_green_passing key at all
        prev = _make_prev_with_paths(["tests/test_gh297.py"])

        result = _verify_green_passing(ctx, prev)

        assert result.status == "error", (
            f"default cfg + failing (cycle 1): expected status='error', "
            f"got {result.status!r}"
        )
        assert result.recoverable is True, (
            f"cycle 1 must be recoverable=True, got {result.recoverable!r}"
        )


# ─── AC6 (pin) ─────────────────────────────────────────────────────────────────


class TestAC6FlagOffAllPassingPin:
    """AC6 (pin) — flag-off + all tests passing → status='ok'. Unaffected code path."""

    def test_flag_off_all_passing_returns_ok(self, tmp_path: Path, monkeypatch) -> None:
        repo = _make_repo_with_failing_test(tmp_path)
        test_file = repo / "tests" / "test_gh297.py"
        test_file.write_text("def test_passes(): assert True\n")

        _patch_emit_p5(monkeypatch)
        ctx = _make_verify_ctx(repo, enforce=False)
        prev = _make_prev_with_paths(["tests/test_gh297.py"])

        result = _verify_green_passing(ctx, prev)

        assert result.status == "ok", (
            f"flag-off + all passing: expected status='ok', got {result.status!r}"
        )
