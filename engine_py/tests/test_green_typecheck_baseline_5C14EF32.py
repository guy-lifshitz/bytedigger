"""RED tests for 5C14EF32 — green typecheck baseline delta (net-new, not absolute).

9 ACs (AC1–AC9).  Pre-GREEN expected FAIL: AC1, AC2, AC3, AC4, AC6, AC7, AC9.
Pre-GREEN expected PASS: AC5, AC8 (selectivity guards).

The new symbol `_compute_baseline_typecheck_count` does NOT yet exist in
phase_5_implement.py.  Every test that references it reaches it via
`getattr(phase_5_implement, "_compute_baseline_typecheck_count", None)`
INSIDE the test body — the file COLLECTS cleanly today and each test
FAILs at assert time (§1q / D1CF5FDF non-collectable RED guard).

Import pattern: modules imported at top level (both collect fine today);
not-yet-existing attrs accessed inside test bodies.  Mirrors the
conftest-path-singleton pattern (conftest.py inserts engine_py root +
workflows on sys.path at import time) — no module-level sys.path
manipulation here (§1q / 81F97F3D).
"""
from __future__ import annotations

import os
import subprocess
import shutil
from pathlib import Path
from typing import Any

import pytest

# Both modules import cleanly today (conftest inserts sys.path).
# Not-yet-existing attrs (_compute_baseline_typecheck_count) referenced
# inside test bodies.
import phase_5_implement  # noqa: E402

from contracts import StepResult, WorkflowContext  # noqa: E402
from net_new_delta import delta_verdict  # noqa: E402  (exists today — used in AC9)


# ─── shared helpers ───────────────────────────────────────────────────────────


def _make_ctx(git_cwd: str, build_class: str = "SIMPLE",
              org_config_extra: dict | None = None) -> WorkflowContext:
    cfg: dict[str, Any] = {"git_cwd": git_cwd, "build_class": build_class}
    if org_config_extra:
        cfg.update(org_config_extra)
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=cfg,
        question="q",
        session_id="test-5c14ef32",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_prev(
    git_cwd: str,
    red_commit_sha: str | None = None,
    cycle: int = 1,
    build_class: str = "SIMPLE",
    extra: dict | None = None,
) -> StepResult:
    data: dict[str, Any] = {
        "git_cwd": git_cwd,
        "build_class": build_class,
        "cycle": cycle,
        "cycle_count": cycle,
    }
    if red_commit_sha is not None:
        data["red_commit_sha"] = red_commit_sha
    if extra:
        data.update(extra)
    return StepResult(
        status="ok",
        data=data,
        duration_ms=0,
        step_name="verify_green_passing",
    )


def _init_git_repo(repo: Path) -> None:
    """Create a minimal real git repo (§1j: realpath for macOS /var/folders)."""
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)


def _make_repo_with_committed_file(tmp_path: Path, filename: str,
                                   content: str) -> Path:
    """Real git repo: baseline commit containing filename with content.
    Returns repo path (realpath, §1j macOS symlink fix).
    """
    repo = Path(os.path.realpath(str(tmp_path / "repo")))
    _init_git_repo(repo)
    (repo / filename).write_text(content)
    subprocess.run(["git", "add", filename], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "baseline"], cwd=repo, check=True)
    return repo


def _make_repo_with_red_commit(tmp_path: Path) -> tuple[Path, str]:
    """Real git repo: baseline commit, then a RED commit; returns (repo, red_sha).
    Mirrors the template pattern in test_34AEB235_green_typecheck_gate.py.
    """
    repo = Path(os.path.realpath(str(tmp_path / "repo")))
    _init_git_repo(repo)
    (repo / "placeholder.py").write_text("# baseline\n")
    subprocess.run(["git", "add", "placeholder.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "baseline"], cwd=repo, check=True)
    tests_dir = repo / "tests"
    tests_dir.mkdir(exist_ok=True)
    (tests_dir / "test_stub.py").write_text("def test_placeholder(): pass\n")
    subprocess.run(["git", "add", "tests/test_stub.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "RED commit"], cwd=repo, check=True)
    red_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo, capture_output=True, text=True, check=True,
    ).stdout.strip()
    return repo, red_sha


# ─── AC1: helper returns N baseline findings for a changed committed file ─────


@pytest.mark.skipif(not shutil.which("git"), reason="git not available")
class TestAC1BaselineCountReturnsN:
    """AC1 — _compute_baseline_typecheck_count with a changed committed file
    carrying N baseline mypy findings → returns N."""

    def test_ac1_returns_baseline_finding_count(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        # Presence gate: file COLLECTS today; test FAILs at assert if helper absent
        fn = getattr(phase_5_implement, "_compute_baseline_typecheck_count", None)
        assert fn is not None, (
            "_compute_baseline_typecheck_count not found in phase_5_implement — "
            "not yet implemented (expected RED fail for AC1)"
        )

        # Real git repo: commit a file with 2 type errors at baseline
        repo = _make_repo_with_committed_file(
            tmp_path,
            "module_with_errors.py",
            "x: int = 'wrong'\ny: str = 42\n",
        )
        baseline_py = str((repo / "module_with_errors.py").resolve())

        # Modify the working tree so stash will have something to save
        (repo / "module_with_errors.py").write_text(
            "x: int = 'wrong'\ny: str = 42\nz: int = 'also_wrong'\n"
        )

        # Canned mypy output for the baseline run: exactly 2 findings
        canned_baseline_stdout = (
            "module_with_errors.py:1: error: Incompatible types in assignment  [assignment]\n"
            "module_with_errors.py:2: error: Incompatible types in assignment  [assignment]\n"
        )

        _real_run = subprocess.run

        def _fake_run(argv, **kwargs):
            is_mypy = bool(argv) and any("mypy" in str(a) for a in argv[:2])
            if not is_mypy:
                return _real_run(argv, **kwargs)
            return subprocess.CompletedProcess(
                args=argv, returncode=1,
                stdout=canned_baseline_stdout, stderr="",
            )

        monkeypatch.setattr(phase_5_implement.subprocess, "run", _fake_run)

        result = fn([baseline_py], str(repo), "cfg_git_cwd")

        assert result == 2, (
            f"Expected baseline count=2 from canned mypy output; got {result!r}. "
            "AC1 FAIL — helper not yet implemented."
        )


# ─── AC2: clean tree → git stash says "No local changes" → returns None ───────


@pytest.mark.skipif(not shutil.which("git"), reason="git not available")
class TestAC2CleanTreeReturnsNone:
    """AC2 — helper: git stash reports 'No local changes to save' → returns None."""

    def test_ac2_clean_tree_returns_none(self, tmp_path: Path, monkeypatch) -> None:
        fn = getattr(phase_5_implement, "_compute_baseline_typecheck_count", None)
        assert fn is not None, (
            "_compute_baseline_typecheck_count not found in phase_5_implement — "
            "not yet implemented (expected RED fail for AC2)"
        )

        # Real git repo with a committed file — working tree is clean (no modifications)
        repo = _make_repo_with_committed_file(
            tmp_path, "clean.py", "x: int = 1\n"
        )
        clean_py = str((repo / "clean.py").resolve())

        # Do NOT modify working tree — stash should say "No local changes to save"
        result = fn([clean_py], str(repo), "cfg_git_cwd")

        assert result is None, (
            f"Expected None for clean tree (no local changes to stash); got {result!r}. "
            "AC2 FAIL — helper not yet implemented."
        )


# ─── AC3: stash always popped (D4 invariant) even if mypy subprocess raises ───


@pytest.mark.skipif(not shutil.which("git"), reason="git not available")
class TestAC3StashAlwaysPopped:
    """AC3 — helper: stash succeeds, ALWAYS pops even if mypy subprocess raises
    (D4 invariant — tree NEVER left stashed)."""

    def test_ac3_stash_popped_even_on_mypy_raise(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        fn = getattr(phase_5_implement, "_compute_baseline_typecheck_count", None)
        assert fn is not None, (
            "_compute_baseline_typecheck_count not found in phase_5_implement — "
            "not yet implemented (expected RED fail for AC3)"
        )

        # Real git repo: commit a file, then modify it so stash saves something
        repo = _make_repo_with_committed_file(
            tmp_path, "target.py", "x: int = 1\n"
        )
        target_py = str((repo / "target.py").resolve())
        (repo / "target.py").write_text("x: int = 'modified'\n")  # dirty working tree

        _real_run = subprocess.run

        def _fake_run(argv, **kwargs):
            is_mypy = bool(argv) and any("mypy" in str(a) for a in argv[:2])
            if not is_mypy:
                return _real_run(argv, **kwargs)
            # Force mypy to raise — simulates a crash mid-run
            raise RuntimeError("Simulated mypy crash for AC3")

        monkeypatch.setattr(phase_5_implement.subprocess, "run", _fake_run)

        # Call the helper — should NOT raise despite mypy crashing
        try:
            result = fn([target_py], str(repo), "cfg_git_cwd")
        except Exception as exc:
            pytest.fail(
                f"AC3 FAIL: _compute_baseline_typecheck_count raised {exc!r}; "
                "D4 invariant requires it swallow errors and always return."
            )

        # After the call, verify the stash is empty (stash was popped)
        stash_list = subprocess.run(
            ["git", "stash", "list"], cwd=repo, capture_output=True, text=True,
        ).stdout.strip()
        assert stash_list == "", (
            f"AC3 FAIL: git stash list is non-empty after helper returned: "
            f"{stash_list!r}. D4 invariant violated — tree was left stashed. "
            "Not yet implemented."
        )

        # Also verify the working tree is restored (modified content is back)
        content_after = (repo / "target.py").read_text()
        assert "modified" in content_after, (
            f"AC3 FAIL: working tree not restored after helper; content={content_after!r}. "
            "git stash pop was not called."
        )

        # result should be None (mypy exception → baseline unavailable)
        assert result is None, (
            f"AC3 FAIL: expected None (mypy raised → baseline unavailable); got {result!r}."
        )


# ─── AC4: newly-ADDED path (absent at baseline) excluded from baseline run ────


@pytest.mark.skipif(not shutil.which("git"), reason="git not available")
class TestAC4AddedFileExcludedFromBaseline:
    """AC4 — helper: a resolved path that is newly-ADDED (absent at baseline after
    stash) is excluded from the baseline run; returns 0 when that is the only path."""

    def test_ac4_added_only_file_returns_zero(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        fn = getattr(phase_5_implement, "_compute_baseline_typecheck_count", None)
        assert fn is not None, (
            "_compute_baseline_typecheck_count not found in phase_5_implement — "
            "not yet implemented (expected RED fail for AC4)"
        )

        # Real git repo: baseline commit with a placeholder, then add a NEW file
        # (not yet committed — it will vanish when stashed)
        repo = _make_repo_with_committed_file(
            tmp_path, "placeholder.py", "# baseline\n"
        )
        new_file = repo / "brand_new_module.py"
        new_file.write_text("x: int = 'this would be a type error'\n")
        # Stage the new file (untracked + -u in stash push will also catch untracked)
        subprocess.run(["git", "add", "brand_new_module.py"], cwd=repo, check=True)

        new_file_path = str(new_file.resolve())

        # Mypy should NOT be called at all (no surviving paths after stash)
        mypy_called = []
        _real_run = subprocess.run

        def _fake_run(argv, **kwargs):
            is_mypy = bool(argv) and any("mypy" in str(a) for a in argv[:2])
            if not is_mypy:
                return _real_run(argv, **kwargs)
            mypy_called.append(list(argv))
            return subprocess.CompletedProcess(
                args=argv, returncode=0, stdout="Success: no issues found\n", stderr="",
            )

        monkeypatch.setattr(phase_5_implement.subprocess, "run", _fake_run)

        result = fn([new_file_path], str(repo), "cfg_git_cwd")

        assert result == 0, (
            f"AC4 FAIL: expected 0 (added file excluded → no baseline findings); "
            f"got {result!r}. Not yet implemented."
        )
        # Verify the new file was not passed to mypy (it didn't exist at baseline)
        # mypy_called may be empty (early-return=0) or called with no target paths
        for call_argv in mypy_called:
            assert new_file_path not in call_argv, (
                f"AC4 FAIL: newly-added file path was passed to mypy at baseline; "
                f"mypy argv={call_argv!r}. Newly-added files must be excluded."
            )


# ─── AC5: gate: current=15, baseline=14 (1 net-new) → still blocks ───────────


@pytest.mark.skipif(not shutil.which("git"), reason="git not available")
class TestAC5NetNewStillBlocks:
    """AC5 [PASS selectivity] — gate: current=15, baseline=14 (1 net-new) →
    would_block → gated_step_result retry path (E_GREEN_TYPECHECK_RETRY).
    Pre-GREEN this passes because the absolute gate also blocks on 15 findings."""

    def test_ac5_net_new_one_still_blocks(self, tmp_path: Path, monkeypatch) -> None:
        repo, red_sha = _make_repo_with_red_commit(tmp_path)

        # Write a changed .py (staged) so gate has a file to typecheck
        changed = repo / "prod_module.py"
        changed.write_text("x: int = 1\n")
        subprocess.run(["git", "add", "prod_module.py"], cwd=repo, check=True)

        # Monkeypatch _compute_baseline_typecheck_count → 14 (raising=False so pre-GREEN OK)
        monkeypatch.setattr(
            phase_5_implement,
            "_compute_baseline_typecheck_count",
            lambda paths, cwd, source: 14,
            raising=False,
        )

        # 15 findings from mypy (current > baseline → net_new=1 → blocks)
        canned_stdout = "\n".join(
            f"prod_module.py:{i}: error: Type error [assignment]"
            for i in range(1, 16)
        ) + "\n"

        _real_run = subprocess.run

        def _fake_run(argv, **kwargs):
            is_mypy = bool(argv) and any("mypy" in str(a) for a in argv[:2])
            if not is_mypy:
                return _real_run(argv, **kwargs)
            return subprocess.CompletedProcess(
                args=argv, returncode=1, stdout=canned_stdout, stderr="",
            )

        monkeypatch.setattr(phase_5_implement.subprocess, "run", _fake_run)

        fn = getattr(phase_5_implement, "_verify_green_typecheck", None)
        assert fn is not None, (
            "_verify_green_typecheck not found — unexpected; gate should exist"
        )

        ctx = _make_ctx(str(repo), build_class="SIMPLE")
        prev = _make_prev(str(repo), red_commit_sha=red_sha, cycle=1)

        result = fn(ctx, prev)

        assert result.error_code in (
            "E_GREEN_TYPECHECK_RETRY",
            "E_GREEN_TYPECHECK_FAIL_CAP2",
        ), (
            f"AC5: net_new=1 must still block; expected retry/cap error_code, "
            f"got {result.error_code!r} (status={result.status!r})"
        )
        assert result.status in ("error",), (
            f"AC5: net_new=1 must block; expected status='error', got {result.status!r}"
        )


# ─── AC6: gate: current=14, baseline=14 (0 net-new) → status=ok, warnings ────


@pytest.mark.skipif(not shutil.which("git"), reason="git not available")
class TestAC6PreExistingOnlyNotBlocked:
    """AC6 [FAIL pre-GREEN] — gate: current=14, baseline=14 (0 net-new, all
    pre-existing) → status='ok', findings surfaced as green_typecheck_warnings,
    NOT blocked.  Pre-GREEN the absolute gate blocks on 14 findings → FAIL."""

    def test_ac6_preexisting_only_returns_ok(self, tmp_path: Path, monkeypatch) -> None:
        repo, red_sha = _make_repo_with_red_commit(tmp_path)

        changed = repo / "prod_module.py"
        changed.write_text("x: int = 1\n")
        subprocess.run(["git", "add", "prod_module.py"], cwd=repo, check=True)

        # Monkeypatch helper → 14 (pre-existing count equals current)
        monkeypatch.setattr(
            phase_5_implement,
            "_compute_baseline_typecheck_count",
            lambda paths, cwd, source: 14,
            raising=False,
        )

        # 14 findings from mypy (same as baseline → net_new=0 → must NOT block)
        canned_stdout = "\n".join(
            f"prod_module.py:{i}: error: Type error [assignment]"
            for i in range(1, 15)
        ) + "\n"

        _real_run = subprocess.run

        def _fake_run(argv, **kwargs):
            is_mypy = bool(argv) and any("mypy" in str(a) for a in argv[:2])
            if not is_mypy:
                return _real_run(argv, **kwargs)
            return subprocess.CompletedProcess(
                args=argv, returncode=1, stdout=canned_stdout, stderr="",
            )

        monkeypatch.setattr(phase_5_implement.subprocess, "run", _fake_run)

        fn = getattr(phase_5_implement, "_verify_green_typecheck", None)
        assert fn is not None

        ctx = _make_ctx(str(repo), build_class="SIMPLE")
        prev = _make_prev(str(repo), red_commit_sha=red_sha, cycle=1)

        result = fn(ctx, prev)

        # Presence gate: verify the new field exists (not vacuously true from early-return)
        assert result.status == "ok", (
            f"AC6 FAIL: expected status='ok' (0 net-new, all pre-existing); "
            f"got status={result.status!r}, error_code={result.error_code!r}. "
            "Pre-GREEN absolute gate blocks on 14 findings — not yet implemented."
        )
        assert isinstance(result.data, dict), "result.data must be a dict"
        warnings = result.data.get("green_typecheck_warnings")
        assert warnings is not None and len(warnings) == 14, (
            f"AC6 FAIL: expected green_typecheck_warnings with 14 findings; "
            f"got {warnings!r}. Not yet implemented."
        )


# ─── AC7: gate: verify_green_typecheck_delta_enforce=False → not enforced ─────


@pytest.mark.skipif(not shutil.which("git"), reason="git not available")
class TestAC7DeltaEnforceFalseNotBlocked:
    """AC7 [FAIL pre-GREEN] — gate: findings present AND
    verify_green_typecheck_delta_enforce=False in org_config → not enforced →
    status='ok'.  Pre-GREEN no flag is read → blocks → FAIL."""

    def test_ac7_enforce_false_returns_ok(self, tmp_path: Path, monkeypatch) -> None:
        # Presence gate: flag must be read by the gate (not vacuously absent)
        # We verify this by checking the gate does NOT block when flag=False
        repo, red_sha = _make_repo_with_red_commit(tmp_path)

        changed = repo / "prod_module.py"
        changed.write_text("x: int = 1\n")
        subprocess.run(["git", "add", "prod_module.py"], cwd=repo, check=True)

        # Monkeypatch helper → 10 (current=12 → net_new=2, but enforce=False)
        monkeypatch.setattr(
            phase_5_implement,
            "_compute_baseline_typecheck_count",
            lambda paths, cwd, source: 10,
            raising=False,
        )

        # 12 findings from mypy (net_new=2, but enforce=False → must not block)
        canned_stdout = "\n".join(
            f"prod_module.py:{i}: error: Type error [assignment]"
            for i in range(1, 13)
        ) + "\n"

        _real_run = subprocess.run

        def _fake_run(argv, **kwargs):
            is_mypy = bool(argv) and any("mypy" in str(a) for a in argv[:2])
            if not is_mypy:
                return _real_run(argv, **kwargs)
            return subprocess.CompletedProcess(
                args=argv, returncode=1, stdout=canned_stdout, stderr="",
            )

        monkeypatch.setattr(phase_5_implement.subprocess, "run", _fake_run)

        fn = getattr(phase_5_implement, "_verify_green_typecheck", None)
        assert fn is not None

        # Pass enforce=False via org_config
        ctx = _make_ctx(
            str(repo),
            build_class="SIMPLE",
            org_config_extra={"verify_green_typecheck_delta_enforce": False},
        )
        prev = _make_prev(str(repo), red_commit_sha=red_sha, cycle=1)

        result = fn(ctx, prev)

        # Presence gate: confirm the flag is being read (not just a coincidental pass)
        # If gate returned ok for a reason OTHER than enforce=False, we need to catch that.
        # We also verify findings are surfaced as warnings (not silently dropped).
        assert result.status == "ok", (
            f"AC7 FAIL: verify_green_typecheck_delta_enforce=False must make gate "
            f"non-blocking; got status={result.status!r}, error_code={result.error_code!r}. "
            "Pre-GREEN cfg flag not read → absolute gate blocks → FAIL."
        )
        assert isinstance(result.data, dict), "result.data must be a dict"
        # Findings must be surfaced as warnings (not silently swallowed)
        warnings = result.data.get("green_typecheck_warnings")
        assert warnings is not None and len(warnings) > 0, (
            f"AC7 FAIL: expected green_typecheck_warnings to be surfaced on enforce=False; "
            f"got {warnings!r}. Not yet implemented."
        )


# ─── AC8: gate: no findings → status=ok, no baseline run [PASS selectivity] ───


@pytest.mark.skipif(not shutil.which("git"), reason="git not available")
class TestAC8CleanMypyOk:
    """AC8 [PASS selectivity] — gate: no findings → status='ok', no baseline run.
    Early-return unchanged — pre-GREEN this already passes."""

    def test_ac8_clean_mypy_returns_ok_no_baseline(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        repo, red_sha = _make_repo_with_red_commit(tmp_path)

        changed = repo / "clean_module.py"
        changed.write_text("x: int = 1\n")
        subprocess.run(["git", "add", "clean_module.py"], cwd=repo, check=True)

        baseline_called = []
        monkeypatch.setattr(
            phase_5_implement,
            "_compute_baseline_typecheck_count",
            lambda paths, cwd, source: baseline_called.append(1) or 0,
            raising=False,
        )

        canned_stdout = "Success: no issues found in 1 source file\n"

        _real_run = subprocess.run

        def _fake_run(argv, **kwargs):
            is_mypy = bool(argv) and any("mypy" in str(a) for a in argv[:2])
            if not is_mypy:
                return _real_run(argv, **kwargs)
            return subprocess.CompletedProcess(
                args=argv, returncode=0, stdout=canned_stdout, stderr="",
            )

        monkeypatch.setattr(phase_5_implement.subprocess, "run", _fake_run)

        fn = getattr(phase_5_implement, "_verify_green_typecheck", None)
        assert fn is not None

        ctx = _make_ctx(str(repo), build_class="SIMPLE")
        prev = _make_prev(str(repo), red_commit_sha=red_sha, cycle=1)

        result = fn(ctx, prev)

        assert result.status == "ok", (
            f"AC8: clean mypy must return status='ok'; got {result.status!r}"
        )
        assert baseline_called == [], (
            f"AC8: baseline helper must NOT be called when no findings; "
            f"called {len(baseline_called)} time(s). (Early-return should precede baseline.)"
        )


# ─── AC9: baseline=None → delta_verdict(None, current) → fail-open, status=ok ─


@pytest.mark.skipif(not shutil.which("git"), reason="git not available")
class TestAC9BaselineUnavailableFailsOpen:
    """AC9 [FAIL pre-GREEN] — baseline unavailable (None) with current>0 findings →
    must match delta_verdict(None, current) semantics = would_block=False →
    expect status='ok' + findings as green_typecheck_warnings, NOT blocked.
    Pre-GREEN absolute gate blocks on any finding → FAIL."""

    def test_ac9_baseline_none_fails_open(self, tmp_path: Path, monkeypatch) -> None:
        repo, red_sha = _make_repo_with_red_commit(tmp_path)

        changed = repo / "prod_module.py"
        changed.write_text("x: int = 'wrong'\n")
        subprocess.run(["git", "add", "prod_module.py"], cwd=repo, check=True)

        # Monkeypatch helper → None (baseline unavailable)
        monkeypatch.setattr(
            phase_5_implement,
            "_compute_baseline_typecheck_count",
            lambda paths, cwd, source: None,
            raising=False,
        )

        # 3 findings from mypy (baseline=None → net_new=0 per delta_verdict → no block)
        current_count = 3
        canned_stdout = "\n".join(
            f"prod_module.py:{i}: error: Type error [assignment]"
            for i in range(1, current_count + 1)
        ) + "\n"

        _real_run = subprocess.run

        def _fake_run(argv, **kwargs):
            is_mypy = bool(argv) and any("mypy" in str(a) for a in argv[:2])
            if not is_mypy:
                return _real_run(argv, **kwargs)
            return subprocess.CompletedProcess(
                args=argv, returncode=1, stdout=canned_stdout, stderr="",
            )

        monkeypatch.setattr(phase_5_implement.subprocess, "run", _fake_run)

        fn = getattr(phase_5_implement, "_verify_green_typecheck", None)
        assert fn is not None

        ctx = _make_ctx(str(repo), build_class="SIMPLE")
        prev = _make_prev(str(repo), red_commit_sha=red_sha, cycle=1)

        result = fn(ctx, prev)

        # Assert against the ACTUAL delta_verdict(None, current_count) semantics —
        # pinned to real net_new_delta.delta_verdict, not a hardcoded guess (§ anti-anchor-bias)
        expected_verdict = delta_verdict(None, current_count, enforce=True)
        assert not expected_verdict.would_block, (
            f"AC9 pre-condition: delta_verdict(None, {current_count}, enforce=True) "
            f"should have would_block=False; got {expected_verdict!r}. "
            "Check net_new_delta.py logic."
        )

        assert result.status == "ok", (
            f"AC9 FAIL: baseline=None with {current_count} findings must fail-open "
            f"(delta_verdict would_block=False); got status={result.status!r}, "
            f"error_code={result.error_code!r}. "
            "Pre-GREEN absolute gate blocks on any finding → FAIL. Not yet implemented."
        )
        assert isinstance(result.data, dict), "result.data must be a dict"
        warnings = result.data.get("green_typecheck_warnings")
        assert warnings is not None and len(warnings) == current_count, (
            f"AC9 FAIL: expected green_typecheck_warnings with {current_count} findings "
            f"(surfaced, not blocked); got {warnings!r}. Not yet implemented."
        )
