"""RED tests for 5325B280 — phase_5_implement.py 3 false-positive bugs.

Spec: SHARED/memory/Decisions/2026-06-12_5325B280_phase5_3bugs_spec.md

BUG1: _check_red_executable treats pytest rc==5 (NOTESTSCOLLECTED) as a real
      collect failure → infinite retry loop on fixture-only paths.
BUG2: _derive_red_paths_via_git_diff includes conftest.py/__init__.py via
      _is_test_path; _is_fixture_only_path helper missing.
BUG3: _commit_green_code on re-entry: "nothing to commit" rc=1 → cm_outcome==
      "non_lock_error" → E_GREEN_COMMIT_FAILED on an already-succeeded GREEN.

Pre-GREEN FAIL/PASS classification:
  AC1 → FAIL: rc==5 falls through to E_RED_COLLECT_FAILED today (no rc==5 guard).
  AC2 → PASS: rc==2 + "ImportError" already routes to E_RED_COLLECT_FAILED.
  AC3 → FAIL: _is_fixture_only_path does not exist until GREEN.
  AC4 → FAIL: _derive_red_paths_via_git_diff includes conftest.py today.
  AC5 → PASS: _is_test_path unchanged; "tests/foo_spec.py" → True, "bin/test_helpers.py" → True.
  AC6 → FAIL: "nothing to commit" rc=1 → E_GREEN_COMMIT_FAILED today.
  AC7 → PASS: staged-change path still fires commit correctly (regression guard).
  AC8 → PASS: derive_state.replay dispatches unknown events to `unknown` counter; no raise.

§1q / D1CF5FDF: _is_fixture_only_path is guarded inside each test body (getattr +
pytest.fail) — never imported at module level so file COLLECTS even pre-GREEN.
"""
from __future__ import annotations

import subprocess
import re
from pathlib import Path
from unittest.mock import MagicMock

import pytest

# conftest-import-time singleton (§1q / 81F97F3D) inserts engine_py root
# and engine_py/workflows into sys.path — no sys.path mutation here.
import phase_5_implement
from derive_state import replay


# ─── shared repo helpers (mirror test_phase_5_step1_disk_truth + step5 idioms) ──

def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)


def _commit_file(repo: Path, relpath: str, body: str = "# x\n", msg: str = "c") -> str:
    """Write, stage, commit a file. Returns the resulting HEAD SHA."""
    p = repo / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    subprocess.run(["git", "add", relpath], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", msg], cwd=repo, check=True)
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=repo, check=True
    )
    return result.stdout.strip()


def _make_repo(tmp_path: Path) -> Path:
    """Minimal git repo with one committed placeholder so HEAD is valid."""
    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
    return repo


def _make_repo_with_red_commit(tmp_path: Path) -> tuple[Path, str]:
    """Seed repo: init + placeholder (seed) + a test-file RED commit.

    Returns (repo_path, red_commit_sha).
    """
    repo = tmp_path / "repo"
    _init_repo(repo)
    _commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
    red_sha = _commit_file(
        repo, "tests/test_red.py", "def test_fail(): assert False\n", "RED: add failing test"
    )
    return repo, red_sha


def _add_untracked(repo: Path, relpath: str, body: str = "# test\n") -> None:
    """Write a file to the working tree without staging/committing."""
    p = repo / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)


def _write_file(repo: Path, relpath: str, body: str = "# impl\n") -> None:
    """Write a file to the working tree without staging/committing."""
    p = repo / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)


def _make_ctx(scratchpad: Path, git_cwd: str, **org_extra):
    from contracts import WorkflowContext
    org = {"scratchpad_dir": str(scratchpad), "git_cwd": git_cwd, **org_extra}
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question="Fix the thing",
        session_id="test-5325B280",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_prev_for_red_check(red_test_paths: list[str], cycle: int = 1) -> MagicMock:
    """Minimal prev StepResult-like shaped like the output of verify_red_lint_rules."""
    prev = MagicMock()
    prev.data = {
        "red_test_paths": red_test_paths,
        "cycle": cycle,
    }
    return prev


def _make_prev_for_commit(
    red_commit_sha: str,
    worker_written_paths: list[str],
    cycle: int = 1,
    **extra,
) -> MagicMock:
    """Minimal prev StepResult-like shaped for _commit_green_code."""
    prev = MagicMock()
    prev.data = {
        "red_commit_sha": red_commit_sha,
        "worker_written_paths": worker_written_paths,
        "manifest_source": "harness_tool_record",
        "cycle": cycle,
        **extra,
    }
    return prev


def _capture_emit(monkeypatch) -> list[dict]:
    """Monkeypatch _emit_safe on the phase_5_implement module; return capture list."""
    captured: list[dict] = []
    monkeypatch.setattr(
        phase_5_implement,
        "_emit_safe",
        lambda et, p, severity="warning": captured.append(
            {"type": et, "payload": p, "severity": severity}
        ),
    )
    return captured


def _head_sha(repo: Path) -> str:
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=repo, check=True
    ).stdout.strip()


# ─────────────────────────────────────────────────────────────────────────────
# BUG1 — rc==5 guard in _check_red_executable
# ─────────────────────────────────────────────────────────────────────────────

class TestBug1RcFiveGuard:
    """BUG1: pytest rc==5 (NOTESTSCOLLECTED) must be treated as clean skip, not
    a collect failure."""

    def test_ac1_rc5_treated_as_clean_skip(self, tmp_path, monkeypatch):
        """AC1 (FAIL pre-GREEN): rc==5 → result.status=="ok", result.data==prev.data,
        NO E_RED_COLLECT_FAILED.

        Today rc==5 falls through the rc==0 guard, skips the stderr-substring
        guard, and hits the "Real collect failure" block → E_RED_COLLECT_FAILED.
        Pre-GREEN this assertion fails on result.status or result.error_code.
        """
        prev = _make_prev_for_red_check(red_test_paths=["tests/conftest.py"], cycle=1)

        # Monkeypatch subprocess.run on the module (mirrors test_7C4D70ED idiom).
        monkeypatch.setattr(
            "phase_5_implement.subprocess.run",
            lambda *args, **kwargs: subprocess.CompletedProcess(
                args=args[0], returncode=5, stdout="", stderr=""
            ),
        )
        # Silence _emit_safe so telemetry side-effects don't raise.
        monkeypatch.setattr(
            phase_5_implement, "_emit_safe", lambda et, p, severity="warning": None
        )

        ctx = _make_ctx(tmp_path / "scratch", str(tmp_path))
        result = phase_5_implement._check_red_executable(ctx, prev)

        assert result.status == "ok", (
            f"5325B280 AC1: rc==5 (NOTESTSCOLLECTED) must yield status='ok' "
            f"(clean-skip passthrough), got {result.status!r}. "
            f"error_code={getattr(result, 'error_code', None)!r}"
        )
        assert result.data == prev.data, (
            f"5325B280 AC1: result.data must equal prev.data (transparent passthrough). "
            f"result.data={result.data!r}, prev.data={prev.data!r}"
        )
        # Belt-and-suspenders: confirm no E_RED_COLLECT_FAILED in error_code.
        assert getattr(result, "error_code", None) != "E_RED_COLLECT_FAILED", (
            f"5325B280 AC1: result must NOT have error_code='E_RED_COLLECT_FAILED' "
            f"when rc==5. Got error_code={getattr(result, 'error_code', None)!r}"
        )

    def test_ac2_rc2_import_error_still_caught(self, tmp_path, monkeypatch):
        """AC2 (PASS pre+post, regression guard): rc==2 + stderr "ImportError: x" →
        routes to E_RED_COLLECT_FAILED. The real-failure path must remain intact.

        This test verifies the unchanged branch is not broken by BUG1 fix.
        """
        prev = _make_prev_for_red_check(red_test_paths=["tests/test_bad.py"], cycle=1)

        monkeypatch.setattr(
            "phase_5_implement.subprocess.run",
            lambda *args, **kwargs: subprocess.CompletedProcess(
                args=args[0], returncode=2, stdout="", stderr="ImportError: x"
            ),
        )
        monkeypatch.setattr(
            phase_5_implement, "_emit_safe", lambda et, p, severity="warning": None
        )

        ctx = _make_ctx(tmp_path / "scratch", str(tmp_path))
        result = phase_5_implement._check_red_executable(ctx, prev)

        assert result.status == "error", (
            f"5325B280 AC2: rc==2 + ImportError must produce status='error', "
            f"got {result.status!r}"
        )
        assert getattr(result, "error_code", None) == "E_RED_COLLECT_FAILED", (
            f"5325B280 AC2: real collect failures must still produce "
            f"error_code='E_RED_COLLECT_FAILED', got {getattr(result, 'error_code', None)!r}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# BUG2 — _is_fixture_only_path helper + _derive_red_paths_via_git_diff filter
# ─────────────────────────────────────────────────────────────────────────────

class TestBug2FixtureOnlyPath:
    """BUG2: _is_fixture_only_path helper must exist and _derive_red_paths_via_git_diff
    must exclude conftest.py / __init__.py from red_test_paths."""

    def test_ac3_is_fixture_only_path_behavior(self):
        """AC3 (FAIL pre-GREEN): _is_fixture_only_path does not exist until GREEN.

        Uses getattr+pytest.fail pattern (§1q / D1CF5FDF guard) so the file
        COLLECTS and fails at assertion time, not import time.

        Expected True: tests/conftest.py, pkg/__init__.py, conftest.py
        Expected False: tests/test_x.py
        """
        helper = getattr(phase_5_implement, "_is_fixture_only_path", None)
        if helper is None:
            pytest.fail(
                "5325B280 AC3: _is_fixture_only_path not shipped yet "
                "(absent from phase_5_implement). GREEN must add this named helper."
            )

        assert helper("tests/conftest.py") is True, (
            "5325B280 AC3: _is_fixture_only_path('tests/conftest.py') must be True"
        )
        assert helper("pkg/__init__.py") is True, (
            "5325B280 AC3: _is_fixture_only_path('pkg/__init__.py') must be True"
        )
        assert helper("conftest.py") is True, (
            "5325B280 AC3: _is_fixture_only_path('conftest.py') must be True"
        )
        assert helper("tests/test_x.py") is False, (
            "5325B280 AC3: _is_fixture_only_path('tests/test_x.py') must be False"
        )

    def test_ac4_derive_excludes_conftest_includes_test(self, tmp_path):
        """AC4 (FAIL pre-GREEN): _derive_red_paths_via_git_diff over a repo with
        untracked tests/conftest.py AND tests/test_real.py must:
          - EXCLUDE any path containing 'conftest.py'
          - INCLUDE at least one path containing 'test_real.py'

        Today conftest.py IS included (no _is_fixture_only_path filter at L705).
        """
        repo = _make_repo(tmp_path)
        _add_untracked(
            repo,
            "tests/conftest.py",
            "import pytest\n@pytest.fixture\ndef f(): return 1\n",
        )
        _add_untracked(
            repo,
            "tests/test_real.py",
            "def test_x(): assert False\n",
        )

        result = phase_5_implement._derive_red_paths_via_git_diff(str(repo))

        assert isinstance(result, list), (
            f"5325B280 AC4: _derive_red_paths_via_git_diff must return list, got {type(result)}"
        )
        assert not any("conftest.py" in p for p in result), (
            f"5325B280 AC4: result must NOT contain conftest.py paths. "
            f"Got: {result!r}. (BUG2: conftest.py matches _is_test_path today.)"
        )
        assert any("test_real.py" in p for p in result), (
            f"5325B280 AC4: result must contain at least one path with 'test_real.py'. "
            f"Got: {result!r}"
        )

    def test_ac5_is_test_path_unchanged(self):
        """AC5 (PASS pre+post, regression guard): _is_test_path is not modified by BUG2 fix.

        tests/foo_spec.py → True; bin/test_helpers.py → True.
        (BUG2 adds _is_fixture_only_path, does NOT touch _is_test_path.)
        """
        it = getattr(phase_5_implement, "_is_test_path", None)
        if it is None:
            pytest.fail(
                "5325B280 AC5: _is_test_path not found in phase_5_implement — "
                "BUG2 fix must NOT remove or rename _is_test_path."
            )

        assert it("tests/foo_spec.py") is True, (
            "5325B280 AC5: _is_test_path('tests/foo_spec.py') must remain True"
        )
        assert it("bin/test_helpers.py") is True, (
            "5325B280 AC5: _is_test_path('bin/test_helpers.py') must remain True"
        )


# ─────────────────────────────────────────────────────────────────────────────
# BUG3 — idempotent green-commit guard
# ─────────────────────────────────────────────────────────────────────────────

class TestBug3IdempotentGreenCommit:
    """BUG3: _commit_green_code on re-entry (prod file already committed, git add
    stages nothing) must skip commit and return status="ok" with green_commit_sha
    == current HEAD, emitting commit_green_code_idempotent_skip."""

    def test_ac6_no_staged_changes_skips_commit(self, tmp_path, monkeypatch):
        """AC6 (FAIL pre-GREEN): prod file already committed — git add stages nothing.

        Strategy (§1i pre-stage, never race): the prod file is committed in the
        red commit itself so git add <prod_path> is a no-op (working tree ==
        index == HEAD for that path). HEAD does NOT advance. Emits
        commit_green_code_idempotent_skip with reason=="no_staged_diff", phase==5.

        Today: "nothing to commit" rc=1 → cm_outcome=="non_lock_error" →
        E_GREEN_COMMIT_FAILED. This test FAILS pre-GREEN on result.status or
        error_code.
        """
        # Build repo: init + placeholder + RED commit (test file)
        repo, red_sha = _make_repo_with_red_commit(tmp_path)
        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()

        # Commit the prod file so the working tree is already clean for that path.
        # git add <prod_path> will stage nothing → no-op commit → BUG3 path.
        prod_relpath = "src/module.py"
        _commit_file(repo, prod_relpath, "def foo(): return 42\n", "GREEN: impl")

        # Capture HEAD before the call — must equal HEAD after (no advance).
        head_before = _head_sha(repo)

        captured = _capture_emit(monkeypatch)

        ctx = _make_ctx(scratchpad, str(repo))
        prev = _make_prev_for_commit(
            red_commit_sha=red_sha,
            worker_written_paths=[prod_relpath],
            cycle=1,
        )

        result = phase_5_implement._commit_green_code(ctx, prev)

        # Must return ok (not error) — BUG3: today returns E_GREEN_COMMIT_FAILED.
        assert result.status == "ok", (
            f"5325B280 AC6: no-staged-changes path must return status='ok', "
            f"got {result.status!r}. error_code={getattr(result, 'error_code', None)!r}. "
            f"error={getattr(result, 'error', None)!r}"
        )
        assert getattr(result, "error_code", None) != "E_GREEN_COMMIT_FAILED", (
            f"5325B280 AC6: must NOT produce E_GREEN_COMMIT_FAILED on idempotent re-entry. "
            f"Got error_code={getattr(result, 'error_code', None)!r}"
        )

        # HEAD must NOT have advanced.
        head_after = _head_sha(repo)
        assert head_after == head_before, (
            f"5325B280 AC6: HEAD must NOT advance on idempotent skip. "
            f"head_before={head_before!r}, head_after={head_after!r}"
        )

        # green_commit_sha must equal the (unchanged) HEAD.
        green_sha = (result.data or {}).get("green_commit_sha")
        assert green_sha == head_before, (
            f"5325B280 AC6: result.data['green_commit_sha'] must equal HEAD "
            f"(the prior green commit). Expected {head_before!r}, got {green_sha!r}"
        )

        # Must emit commit_green_code_idempotent_skip with correct payload.
        skip_events = [e for e in captured if e["type"] == "commit_green_code_idempotent_skip"]
        assert len(skip_events) == 1, (
            f"5325B280 AC6: expected exactly 1 'commit_green_code_idempotent_skip' event, "
            f"got {len(skip_events)}. All events: {[e['type'] for e in captured]!r}"
        )
        payload = skip_events[0]["payload"]
        assert payload.get("reason") == "no_staged_diff", (
            f"5325B280 AC6: skip event payload must have reason='no_staged_diff', "
            f"got reason={payload.get('reason')!r}"
        )
        assert payload.get("phase") == 5, (
            f"5325B280 AC6: skip event payload must have phase==5, "
            f"got phase={payload.get('phase')!r}"
        )

    def test_ac7_staged_change_still_commits(self, tmp_path, monkeypatch):
        """AC7 (PASS pre+post, regression guard): when prod file is new/modified on disk
        (not yet committed), git add stages it → commit fires → HEAD advances.

        The commit-branch must remain intact after BUG3 fix.
        """
        repo, red_sha = _make_repo_with_red_commit(tmp_path)
        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()

        # Write a NEW production file to the working tree (untracked = new GREEN file).
        prod_relpath = "src/module.py"
        _write_file(repo, prod_relpath, "def foo(): return 42\n")

        head_before = _head_sha(repo)

        captured = _capture_emit(monkeypatch)

        ctx = _make_ctx(scratchpad, str(repo))
        prev = _make_prev_for_commit(
            red_commit_sha=red_sha,
            worker_written_paths=[prod_relpath],
            cycle=1,
        )

        result = phase_5_implement._commit_green_code(ctx, prev)

        assert result.status == "ok", (
            f"5325B280 AC7: staged-change path must return status='ok', "
            f"got {result.status!r}. error={getattr(result, 'error', None)!r}"
        )

        head_after = _head_sha(repo)
        assert head_after != head_before, (
            "5325B280 AC7: HEAD must advance after green commit (new file staged)."
        )

        green_sha = (result.data or {}).get("green_commit_sha")
        assert green_sha is not None, "5325B280 AC7: green_commit_sha must be set."
        assert isinstance(green_sha, str) and re.fullmatch(r"[0-9a-f]{40}", green_sha), (
            f"5325B280 AC7: green_commit_sha must be a 40-char hex SHA, got {green_sha!r}"
        )
        assert green_sha == head_after, (
            f"5325B280 AC7: green_commit_sha must equal new HEAD. "
            f"green_sha={green_sha!r}, head_after={head_after!r}"
        )

        commit_events = [e for e in captured if e["type"] == "green_commit"]
        assert len(commit_events) == 1, (
            f"5325B280 AC7: expected 1 'green_commit' event, got {len(commit_events)}. "
            f"All events: {[e['type'] for e in captured]!r}"
        )
        payload = commit_events[0]["payload"]
        assert payload.get("commit_sha") == green_sha, (
            f"5325B280 AC7: green_commit event commit_sha must match result. "
            f"event={payload.get('commit_sha')!r}, result={green_sha!r}"
        )

    def test_ac8_idempotent_skip_event_replay_tolerant(self):
        """AC8 (PASS pre+post): derive_state.replay tolerates commit_green_code_idempotent_skip
        event in the event stream — no raise, unknown_events counter increments (or
        is recognized, both OK per spec §2 dispatch-on-known guarantee).

        §2 states replay tolerates unknown event types by incrementing unknown counter.
        The new event type is unrecognized pre-GREEN (no special dispatch added),
        so it lands in the unknown bucket. Post-GREEN the event type may be known;
        either way no exception is raised.
        """
        events = [
            {
                "event_type": "workflow_started",
                "ts": "2026-06-12T00:00:00Z",
                "run_id": "run-5325B280",
                "payload": {"workflow_name": "phase_5"},
            },
            {
                "event_type": "commit_green_code_idempotent_skip",
                "ts": "2026-06-12T00:00:01Z",
                "run_id": "run-5325B280",
                "payload": {"phase": 5, "step": "commit_green_code", "reason": "no_staged_diff", "cycle": 1},
            },
        ]

        # Must not raise regardless of whether the event type is known or unknown.
        try:
            state = replay(events)
        except Exception as exc:
            pytest.fail(
                f"5325B280 AC8: derive_state.replay raised unexpectedly on "
                f"'commit_green_code_idempotent_skip' event: {exc!r}. "
                f"Spec §2 guarantees dispatch-on-known / unknown-counter for unrecognized types."
            )

        # The replay must return a valid DerivedState (dict-like).
        assert isinstance(state, dict), (
            f"5325B280 AC8: replay must return a dict (DerivedState), got {type(state)}"
        )
