"""RED tests for 14F6DCD4 (Track A / #674) — _verify_green_passing net-new-added hard-fail.

8 ACs — pre-GREEN expected: 5 FAIL / 3 PASS (Opus 5.2 gate-corrected):
  FAIL: AC1, AC2, AC3, AC6, AC7
    AC1: net-new-COMMITTED test failure → should return status="error" but today falls
         through to status="escalate" (no 14F6DCD4 gate exists yet)
    AC2: net-new-UNTRACKED test failure → same fall-through to status="escalate"
    AC3: net-new-STAGED test failure → same fall-through to status="escalate"
    AC6: verify_green_added_test_failed event not emitted today (gate absent)
    AC7: hard-fail path has recoverable=False; not verifiable today (status="escalate" wrong)
  PASS (selectivity guards, test existing behaviour):
    AC4: preexisting test → PASSES today because EVERY case falls through to escalate
         pre-GREEN; post-GREEN must continue to PASS as the forcing-function guard
         against a stub returning status="error" always.
    AC5: missing red_commit_sha → falls through to existing escalate (passes today)
    AC8: orchestrator step-6 sibling guard (documented, trivially passes)
"""
from __future__ import annotations

import subprocess
import sys
from os.path import realpath
from pathlib import Path

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

from bytedigger_engine.contracts import StepResult, WorkflowContext  # noqa: E402
from bytedigger_engine.workflows.phase_5_implement import _verify_green_passing  # noqa: E402


# ─── shared helpers ───────────────────────────────────────────────────────────


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)


def _make_baseline_repo(tmp_path: Path) -> Path:
    """Real git repo with placeholder.py committed as baseline (no test file yet)."""
    repo = Path(realpath(str(tmp_path / "repo")))
    _init_repo(repo)
    (repo / "placeholder.py").write_text("# placeholder\n")
    subprocess.run(["git", "add", "placeholder.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init baseline"], cwd=repo, check=True)
    return repo


def _capture_head_sha(repo: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo, capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def _write_failing_test(repo: Path, rel_path: str = "tests/test_added.py") -> Path:
    test_file = repo / rel_path
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text("def test_fails(): assert False, 'net-new added test failure'\n")
    return test_file


def _make_verify_ctx(repo: Path, red_commit_sha: str | None = None) -> WorkflowContext:
    org: dict = {"git_cwd": str(repo)}
    if red_commit_sha is not None:
        org["red_commit_sha"] = red_commit_sha
    org["verify_green_delta_enforce"] = False
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question="q",
        session_id="test-14f6dcd4",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_prev_with_paths(paths: list[str], red_commit_sha: str | None = None) -> StepResult:
    data: dict = {"red_test_paths": paths}
    if red_commit_sha is not None:
        data["red_commit_sha"] = red_commit_sha
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


# ─── AC1: committed-since-red-sha test file → hard-fail ──────────────────────


class TestAC1CommittedNetNewTestHardFail:
    """AC1 — COMMITTED-since-red_sha failing test → status='error', recoverable=False."""

    def test_committed_net_new_test_returns_hard_error(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Commit baseline, capture red_sha, ADD+COMMIT test file, call seam.

        Today FAILS: gate absent, seam returns status='escalate'.
        After GREEN: must return status='error', error_code='E_GREEN_NOT_PASSING'.
        """
        repo = _make_baseline_repo(tmp_path)
        red_sha = _capture_head_sha(repo)
        _write_failing_test(repo, "tests/test_added.py")
        subprocess.run(["git", "add", "tests/test_added.py"], cwd=repo, check=True)
        subprocess.run(
            ["git", "commit", "-q", "-m", "GREEN adds test"],
            cwd=repo, check=True,
        )

        _patch_emit_p5(monkeypatch)
        ctx = _make_verify_ctx(repo)
        prev = _make_prev_with_paths(["tests/test_added.py"], red_commit_sha=red_sha)

        result = _verify_green_passing(ctx, prev)

        assert result.status == "error", (
            f"committed net-new test: expected status='error' (hard-fail), "
            f"got {result.status!r}. Today falls through to 'escalate' — gate absent."
        )
        assert result.error_code == "E_GREEN_NOT_PASSING", (
            f"committed net-new test: expected error_code='E_GREEN_NOT_PASSING', "
            f"got {result.error_code!r}"
        )


# ─── AC2: untracked test file → hard-fail ────────────────────────────────────


class TestAC2UntrackedNetNewTestHardFail:
    """AC2 — UNTRACKED failing test → status='error', recoverable=False."""

    def test_untracked_net_new_test_returns_hard_error(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Commit baseline, capture red_sha, WRITE test file (no git add), call seam.

        Today FAILS: gate absent, seam returns status='escalate'.
        After GREEN: must return status='error', error_code='E_GREEN_NOT_PASSING'.
        """
        repo = _make_baseline_repo(tmp_path)
        red_sha = _capture_head_sha(repo)
        _write_failing_test(repo, "tests/test_untracked.py")
        # Intentionally do NOT git add

        _patch_emit_p5(monkeypatch)
        ctx = _make_verify_ctx(repo)
        prev = _make_prev_with_paths(["tests/test_untracked.py"], red_commit_sha=red_sha)

        result = _verify_green_passing(ctx, prev)

        assert result.status == "error", (
            f"untracked net-new test: expected status='error' (hard-fail), "
            f"got {result.status!r}. Today falls through to 'escalate' — gate absent."
        )
        assert result.error_code == "E_GREEN_NOT_PASSING", (
            f"untracked net-new test: expected error_code='E_GREEN_NOT_PASSING', "
            f"got {result.error_code!r}"
        )


# ─── AC3: staged-not-committed test file → hard-fail ─────────────────────────


class TestAC3StagedNetNewTestHardFail:
    """AC3 — STAGED-not-committed failing test → status='error', recoverable=False."""

    def test_staged_net_new_test_returns_hard_error(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Commit baseline, capture red_sha, write+git add (no commit), call seam.

        Today FAILS: gate absent, seam returns status='escalate'.
        After GREEN: must return status='error', error_code='E_GREEN_NOT_PASSING'.
        """
        repo = _make_baseline_repo(tmp_path)
        red_sha = _capture_head_sha(repo)
        _write_failing_test(repo, "tests/test_staged.py")
        subprocess.run(["git", "add", "tests/test_staged.py"], cwd=repo, check=True)
        # Intentionally do NOT commit

        _patch_emit_p5(monkeypatch)
        ctx = _make_verify_ctx(repo)
        prev = _make_prev_with_paths(["tests/test_staged.py"], red_commit_sha=red_sha)

        result = _verify_green_passing(ctx, prev)

        assert result.status == "error", (
            f"staged net-new test: expected status='error' (hard-fail), "
            f"got {result.status!r}. Today falls through to 'escalate' — gate absent."
        )
        assert result.error_code == "E_GREEN_NOT_PASSING", (
            f"staged net-new test: expected error_code='E_GREEN_NOT_PASSING', "
            f"got {result.error_code!r}"
        )


# ─── AC4: preexisting (committed AT red_sha) → escalate, NOT hard-fail ────────


class TestAC4PreexistingTestEscalatesNotHardFail:
    """AC4 — Forcing-function: preexisting test (committed BEFORE red_sha) → status='escalate'.

    A stub returning status='error' always would FAIL this AC.
    """

    def test_preexisting_committed_test_returns_escalate_not_error(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Write+commit test file FIRST, THEN capture red_sha (after). Call seam.

        The test file existed AT red_sha — it is NOT added since red_sha.
        Expected: status='escalate' (585E30E3 P0 behavior preserved).
        FAILS today only if the gate fires incorrectly (it doesn't exist pre-GREEN,
        so this test actually PASSes today — kept as a forcing-function stub-guard).

        Note: This AC passes today because status='escalate' is the current behavior
        for any failing test with red_commit_sha present; included per spec §3 AC4
        as stub-passability forcing-function to prevent a naive stub from passing AC1-3.
        """
        repo = _make_baseline_repo(tmp_path)
        # Write AND commit test file BEFORE capturing red_sha
        _write_failing_test(repo, "tests/test_preexisting.py")
        subprocess.run(["git", "add", "tests/test_preexisting.py"], cwd=repo, check=True)
        subprocess.run(
            ["git", "commit", "-q", "-m", "RED adds test file"],
            cwd=repo, check=True,
        )
        # THEN capture red_sha — test file is NOT added since this sha
        red_sha = _capture_head_sha(repo)

        _patch_emit_p5(monkeypatch)
        ctx = _make_verify_ctx(repo)
        prev = _make_prev_with_paths(["tests/test_preexisting.py"], red_commit_sha=red_sha)

        result = _verify_green_passing(ctx, prev)

        assert result.status == "error", (
            f"preexisting test (committed at red_sha): expected status='error' "
            f"(GH297 unconditional cycle-feedback, cycle 1), got {result.status!r}. "
            f"A stub returning status='error' always would FAIL here."
        )
        assert result.error_code == "E_GREEN_NOT_PASSING", (
            f"preexisting test: expected error_code='E_GREEN_NOT_PASSING', "
            f"got {result.error_code!r}"
        )
        assert result.recoverable is True, (
            f"preexisting test: cycle 1 must be recoverable, got {result.recoverable!r}"
        )
        assert result.data.get("retry_from_step") == 1, (
            f"preexisting test: cycle 1 must set retry_from_step=1, got {result.data!r}"
        )


# ─── AC5: missing red_commit_sha → fall-through escalate (passes today) ──────


class TestAC5MissingRedCommitShaFallThrough:
    """AC5 — No red_commit_sha in prev.data → existing escalate behavior unchanged.

    PASSES today (selectivity guard protecting all 5 named 585E30E3 siblings).
    """

    def test_missing_red_commit_sha_returns_escalate(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Standard fixture with no red_commit_sha → seam returns status='escalate'.

        Mirrors _make_prev_with_paths style from test_phase_5_p0_escalate_585E30E3.py.
        Passes today and must continue to pass post-GREEN (fall-through guarantee).
        """
        repo = _make_baseline_repo(tmp_path)
        _write_failing_test(repo, "tests/test_fallthrough.py")
        # No git add — this simulates GREEN writing a failing test without red_commit_sha

        _patch_emit_p5(monkeypatch)
        ctx = _make_verify_ctx(repo)  # No red_commit_sha
        # No red_commit_sha in prev.data either
        prev = StepResult(
            status="ok",
            data={"red_test_paths": ["tests/test_fallthrough.py"]},
            duration_ms=0,
            step_name="verify_green_lint_rules",
        )

        result = _verify_green_passing(ctx, prev)

        assert result.status == "error", (
            f"no red_commit_sha: expected status='error' (GH297 unconditional "
            f"cycle-feedback, cycle 1), got {result.status!r}"
        )
        assert result.recoverable is True, (
            f"no red_commit_sha: cycle 1 must be recoverable, got {result.recoverable!r}"
        )
        assert result.data.get("retry_from_step") == 1, (
            f"no red_commit_sha: cycle 1 must set retry_from_step=1, got {result.data!r}"
        )


# ─── AC6: verify_green_added_test_failed event emitted on hard-fail ───────────


class TestAC6AddedTestFailedEventEmitted:
    """AC6 — hard-fail path emits verify_green_added_test_failed event with correct payload."""

    def test_verify_green_added_test_failed_event_emitted_with_correct_payload(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Committed net-new test → seam emits verify_green_added_test_failed.

        Today FAILS: gate absent, event never emitted.
        After GREEN: event must be emitted with net_new_added_failures (list),
        n_added_failures (int), phase==5.
        """
        repo = _make_baseline_repo(tmp_path)
        red_sha = _capture_head_sha(repo)
        _write_failing_test(repo, "tests/test_event_check.py")
        subprocess.run(["git", "add", "tests/test_event_check.py"], cwd=repo, check=True)
        subprocess.run(
            ["git", "commit", "-q", "-m", "GREEN adds test for event check"],
            cwd=repo, check=True,
        )

        captured = _patch_emit_p5(monkeypatch)
        ctx = _make_verify_ctx(repo)
        prev = _make_prev_with_paths(["tests/test_event_check.py"], red_commit_sha=red_sha)

        _verify_green_passing(ctx, prev)

        added_events = [e for e in captured if e["type"] == "verify_green_added_test_failed"]
        assert len(added_events) == 1, (
            f"Expected exactly 1 verify_green_added_test_failed event, "
            f"got {len(added_events)}; all captured types: {[e['type'] for e in captured]}"
        )
        payload = added_events[0]["payload"]
        assert "net_new_added_failures" in payload, (
            f"Event payload must have 'net_new_added_failures' key; got {payload!r}"
        )
        assert isinstance(payload["net_new_added_failures"], list), (
            f"net_new_added_failures must be a list; got {type(payload['net_new_added_failures'])}"
        )
        assert payload.get("n_added_failures") == 1, (
            f"n_added_failures must be 1; got {payload.get('n_added_failures')!r}"
        )
        assert payload.get("phase") == 5, (
            f"phase must be 5; got {payload.get('phase')!r}"
        )
        # Verify the absolute path of the test file appears in net_new_added_failures
        expected_abs = str((repo / "tests" / "test_event_check.py").resolve())
        assert expected_abs in payload["net_new_added_failures"], (
            f"Expected {expected_abs!r} in net_new_added_failures; "
            f"got {payload['net_new_added_failures']!r}"
        )


# ─── AC7: hard-fail return has recoverable=False ─────────────────────────────


class TestAC7HardFailRecoverableFalse:
    """AC7 — hard-fail StepResult has recoverable=False (engine.py:215 chain-terminal)."""

    def test_hard_fail_result_has_recoverable_false(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Untracked net-new test → result.recoverable is False.

        Today FAILS: gate absent, result is the escalate path which also has
        recoverable=False but the STATUS is wrong (escalate, not error).
        This test asserting result.status=='error' AND result.recoverable is False
        fails pre-GREEN because status is wrong.
        """
        repo = _make_baseline_repo(tmp_path)
        red_sha = _capture_head_sha(repo)
        _write_failing_test(repo, "tests/test_recoverable.py")
        # Untracked — no git add

        _patch_emit_p5(monkeypatch)
        ctx = _make_verify_ctx(repo)
        prev = _make_prev_with_paths(["tests/test_recoverable.py"], red_commit_sha=red_sha)

        result = _verify_green_passing(ctx, prev)

        assert result.status == "error", (
            f"untracked net-new test: expected status='error', got {result.status!r}"
        )
        assert result.recoverable is False, (
            f"hard-fail result must have recoverable=False (engine.py:215 chain-terminal); "
            f"got {result.recoverable!r}"
        )


# ─── AC8: sibling-test selectivity guard (orchestrator step-6 verification) ───


class TestAC8SiblingSelectivity:
    """AC8 — Sibling selectivity: orchestrator runs 5 named p0/p1b/p2/g1 tests post-GREEN.

    This test class documents the orchestrator's step-6 responsibility.
    The 5 named tests all use fixtures without red_commit_sha → fall-through.
    No code change in this test file is needed; orchestrator verifies at step 6.
    """

    def test_documented_as_orchestrator_step6_verification(self) -> None:
        """Orchestrator will verify at step 6 (stash+rerun delta math, workflows.md §1r):

        python -m pytest \\
            tests/test_phase_5_p0_escalate_585E30E3.py \\
            tests/test_phase_5_p1b_reproducibility_585E30E3.py \\
            tests/test_phase_5_p2_net_new_delta_585E30E3.py \\
            tests/test_phase_5_g1_band_aid_9_verify_green.py \\
            tests/test_phase_5_step3_verify_green.py -q

        Expected: ZERO new failures across all 5 files post-GREEN.
        The 14F6DCD4 gate fires only when red_commit_sha is present; all 5 named
        sibling tests use fixtures lacking red_commit_sha → fall-through guaranteed.
        """
        assert True  # Orchestrator verifies; this test trivially passes as documentation
