"""RED tests for C0B5C6E1 Phase 1b: flip verify_green_passing + verify_green_delta_enforce defaults to True.

AC1: no-flag-keys + failing groups → status="error" (default True since C0B5C6E1 shipped)
AC2: no-flag-keys + failing → verify_green_delta_verdict event has enforced=True (default True since C0B5C6E1 shipped)
AC4: delta_enforce=False only (no verify_green_passing key) + failing → status="error" (default True since C0B5C6E1 shipped)
AC6: passing groups unaffected by flip → status="ok" (selectivity guard)
AC8: Track A net-new-added test → status="error" + verify_green_added_test_failed event (selectivity guard)

GH297: AC3/AC5/AC7 (explicit verify_green_passing=False opt-out escalate pins) removed —
the opt-out escalate branch no longer exists in production after GH297.

C0B5C6E1's default-True flip already shipped, so all 5 surviving ACs (AC1/2/4/6/8)
are regression pins that PASS both pre- and post-GH297-GREEN: 5/5 PASS pre-GREEN,
5/5 PASS post-GREEN.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "workflows"))
sys.path.insert(0, str(HERE.parent / "lib" / "plugins"))

from contracts import StepResult, WorkflowContext  # noqa: E402
import phase_5_implement  # noqa: E402


# ─── ctx builders ────────────────────────────────────────────────────────────


def _make_ctx_with_cwd(git_cwd: str, **org_extra) -> WorkflowContext:
    org = {"git_cwd": git_cwd}
    org.update(org_extra)
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question="Add foo to bar",
        session_id="test-C0B5C6E1",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_prev(red_test_paths: list, *, red_commit_sha: str | None = None) -> StepResult:
    data: dict = {"red_test_paths": red_test_paths}
    if red_commit_sha is not None:
        data["red_commit_sha"] = red_commit_sha
    return StepResult(
        status="ok",
        data=data,
        duration_ms=0,
        step_name="prev",
    )


# ─── monkeypatch helpers ──────────────────────────────────────────────────────


class _FakeResult:
    def __init__(self, n_passed: int = 5, n_failed: int = 3, exit_code: int = 1):
        self.n_passed = n_passed
        self.n_failed = n_failed
        self.exit_code = exit_code


def _fake_infer(paths, git_cwd=None):
    return {
        "groups": [
            {
                "kind": "py",
                "argv": ["python", "-m", "pytest"] + list(paths),
                "paths": list(paths),
            }
        ]
    }


# ─── AC1: no-flag-keys + failing → status="error" ────────────────────────────


def test_ac1_no_flag_keys_failing_returns_error(tmp_path, monkeypatch):
    """_verify_green_passing with no flag keys and failing tests must return status='error'.

    Regression pin: cfg.get("verify_green_passing", True) defaults True since
    C0B5C6E1 shipped — this already PASSES pre-GH297-GREEN and must keep passing.
    """
    emitted: list[tuple] = []

    fake_result = _FakeResult(n_passed=5, n_failed=3, exit_code=1)

    def _fake_run_test_command(argv, git_cwd, *, timeout=120):
        return fake_result

    def _fake_emit(event_name, payload, *, severity="info"):
        emitted.append((event_name, payload))

    monkeypatch.setattr(phase_5_implement, "run_test_command", _fake_run_test_command)
    monkeypatch.setattr(phase_5_implement, "_infer_test_command_for_paths", _fake_infer)
    monkeypatch.setattr(phase_5_implement, "_emit_safe", _fake_emit)

    ctx = _make_ctx_with_cwd(str(tmp_path))
    prev = _make_prev(["tests/test_x.py"])

    result = phase_5_implement._verify_green_passing(ctx, prev)

    assert result.status == "error", (
        f"Expected status='error' (post-flip default), got '{result.status}'"
    )
    assert result.error_code == "E_GREEN_NOT_PASSING"


# ─── AC2: no-flag-keys + failing → verify_green_delta_verdict enforced=True ──


def test_ac2_no_flag_keys_delta_verdict_enforced_true(tmp_path, monkeypatch):
    """verify_green_delta_verdict event must carry enforced=True when no flag keys present.

    FAIL pre-GREEN: bool(cfg.get("verify_green_delta_enforce")) == False → event has enforced=False.
    PASS post-GREEN: default True → enforced=True in event payload.
    """
    emitted: list[tuple] = []

    fake_result = _FakeResult(n_passed=5, n_failed=3, exit_code=1)

    def _fake_run_test_command(argv, git_cwd, *, timeout=120):
        return fake_result

    def _fake_emit(event_name, payload, *, severity="info"):
        emitted.append((event_name, payload))

    monkeypatch.setattr(phase_5_implement, "run_test_command", _fake_run_test_command)
    monkeypatch.setattr(phase_5_implement, "_infer_test_command_for_paths", _fake_infer)
    monkeypatch.setattr(phase_5_implement, "_emit_safe", _fake_emit)

    ctx = _make_ctx_with_cwd(str(tmp_path))
    prev = _make_prev(["tests/test_x.py"])

    phase_5_implement._verify_green_passing(ctx, prev)

    delta_events = [e for e in emitted if e[0] == "verify_green_delta_verdict"]
    assert len(delta_events) == 1, (
        f"Expected exactly 1 verify_green_delta_verdict event, got {len(delta_events)}"
    )
    assert delta_events[0][1]["enforced"] is True, (
        f"Expected enforced=True in delta_verdict payload, got {delta_events[0][1]['enforced']}"
    )


# ─── AC4: delta_enforce=False only, no verify_green_passing key → error ───────


def test_ac4_delta_enforce_false_only_no_passing_key_returns_error(tmp_path, monkeypatch):
    """Only verify_green_delta_enforce=False set; verify_green_passing absent → default True → error.

    FAIL pre-GREEN: missing key → bool(None)=False → escalate.
    PASS post-GREEN: missing key → default True → error.
    """
    emitted: list[tuple] = []

    fake_result = _FakeResult(n_passed=5, n_failed=3, exit_code=1)

    def _fake_run_test_command(argv, git_cwd, *, timeout=120):
        return fake_result

    def _fake_emit(event_name, payload, *, severity="info"):
        emitted.append((event_name, payload))

    monkeypatch.setattr(phase_5_implement, "run_test_command", _fake_run_test_command)
    monkeypatch.setattr(phase_5_implement, "_infer_test_command_for_paths", _fake_infer)
    monkeypatch.setattr(phase_5_implement, "_emit_safe", _fake_emit)

    # Only delta_enforce=False; NO verify_green_passing key
    ctx = _make_ctx_with_cwd(str(tmp_path), verify_green_delta_enforce=False)
    prev = _make_prev(["tests/test_x.py"])

    result = phase_5_implement._verify_green_passing(ctx, prev)

    assert result.status == "error", (
        f"Expected status='error' (missing key → default True), got '{result.status}'"
    )
    assert result.error_code == "E_GREEN_NOT_PASSING"


# ─── AC6: passing groups unaffected by flag flip → status="ok" ────────────────


def test_ac6_passing_groups_return_ok_unaffected_by_flip(tmp_path, monkeypatch):
    """No failing groups → status='ok' regardless of flag default.

    PASS today (selectivity guard): passing path must not be disturbed by the flag flip.
    """
    emitted: list[tuple] = []

    fake_result = _FakeResult(n_passed=7, n_failed=0, exit_code=0)

    def _fake_run_test_command(argv, git_cwd, *, timeout=120):
        return fake_result

    def _fake_emit(event_name, payload, *, severity="info"):
        emitted.append((event_name, payload))

    monkeypatch.setattr(phase_5_implement, "run_test_command", _fake_run_test_command)
    monkeypatch.setattr(phase_5_implement, "_infer_test_command_for_paths", _fake_infer)
    monkeypatch.setattr(phase_5_implement, "_emit_safe", _fake_emit)

    ctx = _make_ctx_with_cwd(str(tmp_path))
    prev = _make_prev(["tests/test_x.py"])

    result = phase_5_implement._verify_green_passing(ctx, prev)

    assert result.status == "ok", (
        f"Expected status='ok' for passing groups, got '{result.status}'"
    )


# ─── AC8: Track A net-new-added test fires → error + event ───────────────────


def test_ac8_track_a_net_new_added_test_fires_before_flag_check(tmp_path, monkeypatch):
    """Track A (14F6DCD4) hard-fail fires before flag check; flag flip must not disturb it.

    PASS today (selectivity guard): Track A already returns 'error' before reaching flag check.
    PASS post-GREEN: Track A path unchanged.
    """
    emitted: list[tuple] = []

    fake_result = _FakeResult(n_passed=5, n_failed=3, exit_code=1)
    test_rel_path = "tests/test_x.py"
    test_abs_path = str((Path(str(tmp_path)) / test_rel_path).resolve())
    fake_sha = "abc123def456"

    def _fake_run_test_command(argv, git_cwd, *, timeout=120):
        return fake_result

    def _fake_emit(event_name, payload, *, severity="info"):
        emitted.append((event_name, payload))

    def _fake_get_diff_added_files(red_sha, git_cwd):
        return {test_abs_path}

    monkeypatch.setattr(phase_5_implement, "run_test_command", _fake_run_test_command)
    monkeypatch.setattr(phase_5_implement, "_infer_test_command_for_paths", _fake_infer)
    monkeypatch.setattr(phase_5_implement, "_emit_safe", _fake_emit)
    monkeypatch.setattr(phase_5_implement, "_get_diff_added_files", _fake_get_diff_added_files)

    ctx = _make_ctx_with_cwd(str(tmp_path))
    prev = _make_prev([test_rel_path], red_commit_sha=fake_sha)

    result = phase_5_implement._verify_green_passing(ctx, prev)

    assert result.status == "error", (
        f"Expected status='error' from Track A gate, got '{result.status}'"
    )
    assert result.error_code == "E_GREEN_NOT_PASSING"
    added_events = [e for e in emitted if e[0] == "verify_green_added_test_failed"]
    assert len(added_events) >= 1, (
        "Expected at least 1 verify_green_added_test_failed event (Track A path fired)"
    )
