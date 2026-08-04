"""RED tests for F23A1FDF — sibling-expanded net-new-delta gate (Part A).

11 ACs — pre-GREEN expected failures:
  FAIL: AC1  _sibling_test_paths not importable (AttributeError on import)
  FAIL: AC2  _sibling_test_paths not importable
  FAIL: AC3  _sibling_test_paths not importable
  FAIL: AC4  _sibling_test_paths not importable
  FAIL: AC5  _run_plan_failed_total not importable
  FAIL: AC6  _run_plan_failed_total not importable
  FAIL: AC7  verify_green_sibling_delta_verdict never emitted + wrong status (block absent)
  FAIL: AC8  safety case: block absent → status="ok" already today → need to confirm
             the path that WOULD block (sib delta absent → AC7 FAIL proves gate absent)
  FAIL: AC9  verify_green_delta_sibling_expand flag not consulted today (block absent)
  FAIL: AC10 verify_green_sibling_delta_verdict event not emitted today (block absent)
  PASS: AC11 regression note — marker only, full phase_5 suite run delegated to orchestrator §1r
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

# sys.path is managed by conftest.py (§1q / 81F97F3D gate — no module-level sys.path here).
# conftest already inserts engine_py root + engine_py/bytedigger_engine/workflows so all imports below work.

from bytedigger_engine.contracts import StepResult, WorkflowContext  # noqa: E402
from bytedigger_engine.workflows.phase_5_implement import _verify_green_passing  # noqa: E402
from bytedigger_engine.workflows import phase_5_implement as _p5  # noqa: E402  (for monkeypatching)


# ─── shared helpers ───────────────────────────────────────────────────────────

def _make_ctx(**org_extra) -> WorkflowContext:
    """Minimal WorkflowContext for sibling-delta tests."""
    org_config: dict = {
        "verify_green_passing": False,
        "verify_green_delta_enforce": False,
        **org_extra,
    }
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org_config,
        question="F23A1FDF-sibling-delta-test",
        session_id="F23A1FDF-red",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_prev(paths: list[str], red_commit_sha: str | None = None) -> StepResult:
    """Minimal StepResult carrying red_test_paths (+ optional red_commit_sha)."""
    data: dict[str, Any] = {"red_test_paths": paths}
    if red_commit_sha is not None:
        data["red_commit_sha"] = red_commit_sha
    return StepResult(
        status="ok",
        data=data,
        duration_ms=0,
        step_name="verify_green_lint_rules",
    )


def _patch_emit(monkeypatch) -> list[dict]:
    """Spy on _emit_safe in phase_5_implement; return captured events list."""
    captured: list[dict] = []

    def _capture(event_type, payload, severity="warning"):
        captured.append({"type": event_type, "payload": payload, "severity": severity})

    monkeypatch.setattr(_p5, "_emit_safe", _capture)
    return captured


def _fake_run_result(n_failed: int, exit_code: int | None = None):
    """Return a minimal TestRunResult-like object (import deferred to call time)."""
    from bytedigger_engine.lib.plugins.disk_truth.test_runner import TestRunResult  # type: ignore[import]
    ec = exit_code if exit_code is not None else (0 if n_failed == 0 else 1)
    return TestRunResult(
        exit_code=ec,
        n_passed=max(0, 5 - n_failed),
        n_failed=n_failed,
        stdout_path="/tmp/fake_stdout.txt",
        stderr_path="/tmp/fake_stderr.txt",
    )


# ─── AC1-AC4: _sibling_test_paths helper ─────────────────────────────────────

class TestAC01SiblingTestPathsExistingFile:
    # AC1: _sibling_test_paths with red_sha + changed prod file foo.py whose test_foo.py
    #      sibling EXISTS on disk → result contains abs path of test_foo.py ∪ red paths
    def test_ac01_sibling_test_paths_includes_existing_sibling(self, tmp_path):
        from bytedigger_engine.workflows.phase_5_implement import _sibling_test_paths  # type: ignore[attr-defined]

        # Create a fake git_cwd with a prod file and its conventional sibling
        prod_file = tmp_path / "src" / "foo.py"
        prod_file.parent.mkdir(parents=True)
        prod_file.write_text("# prod\n")
        sibling = tmp_path / "src" / "test_foo.py"
        sibling.write_text("# test\n")

        # red_test_paths already contains some test
        red_path = str(tmp_path / "tests" / "test_existing.py")
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_existing.py").write_text("# existing red test\n")

        from bytedigger_engine.workflows import phase_5_implement as _p5m
        # Monkeypatch git_diff_files to return the prod file as a changed file
        _orig = getattr(_p5m, "git_diff_files", None)

        changed_rel = "src/foo.py"

        # We monkeypatch at module attribute level using object.__setattr__ is not possible;
        # use pytest monkeypatch via a temporary attribute swap
        import unittest.mock as mock
        with mock.patch.object(_p5m, "git_diff_files", return_value=[changed_rel]):
            result = _sibling_test_paths([red_path], "abc123", str(tmp_path))

        assert str(sibling.resolve()) in result, (
            f"AC1: expected sibling {sibling} in result; got {result}"
        )
        assert red_path in result, (
            f"AC1: expected red_path {red_path!r} preserved in result; got {result}"
        )


class TestAC02SiblingTestPathsNoSibling:
    # AC2: _sibling_test_paths with changed prod file that has NO existing sibling
    #      → result == red_test_paths (no fabricated/non-existent paths)
    def test_ac02_no_existing_sibling_returns_red_paths_unchanged(self, tmp_path):
        from bytedigger_engine.workflows.phase_5_implement import _sibling_test_paths  # type: ignore[attr-defined]

        prod_file = tmp_path / "bar.py"
        prod_file.write_text("# prod\n")
        # No test_bar.py created — sibling does NOT exist

        red_path = str(tmp_path / "tests" / "test_red.py")
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_red.py").write_text("# red\n")

        from bytedigger_engine.workflows import phase_5_implement as _p5m
        import unittest.mock as mock
        with mock.patch.object(_p5m, "git_diff_files", return_value=["bar.py"]):
            result = _sibling_test_paths([red_path], "abc123", str(tmp_path))

        assert result == sorted([red_path]), (
            f"AC2: no existing sibling → result must equal red_paths; got {result}"
        )


class TestAC03SiblingTestPathsExcludesTestFiles:
    # AC3: _sibling_test_paths excludes changed TEST files from the prod set
    #      (a changed test_x.py produces no sibling candidates)
    def test_ac03_changed_test_file_produces_no_sibling_candidates(self, tmp_path):
        from bytedigger_engine.workflows.phase_5_implement import _sibling_test_paths  # type: ignore[attr-defined]

        # A changed file that IS itself a test file
        test_file = tmp_path / "test_widget.py"
        test_file.write_text("# test\n")
        # A hypothetical non-existent sibling that must NOT be generated
        phantom = tmp_path / "test_test_widget.py"
        assert not phantom.exists()

        red_path = str(tmp_path / "test_widget.py")

        from bytedigger_engine.workflows import phase_5_implement as _p5m
        import unittest.mock as mock
        with mock.patch.object(_p5m, "git_diff_files", return_value=["test_widget.py"]):
            result = _sibling_test_paths([red_path], "abc123", str(tmp_path))

        # Changed test file contributes no new sibling candidates
        non_red = [p for p in result if p != red_path]
        assert non_red == [], (
            f"AC3: changed test file must produce no sibling candidates; extra paths: {non_red}"
        )


class TestAC04SiblingTestPathsNoRedSha:
    # AC4: _sibling_test_paths with red_sha=None → returns red_test_paths unchanged
    def test_ac04_no_red_sha_returns_red_paths_unchanged(self, tmp_path):
        from bytedigger_engine.workflows.phase_5_implement import _sibling_test_paths  # type: ignore[attr-defined]

        red_paths = ["/fake/tests/test_a.py", "/fake/tests/test_b.py"]

        result = _sibling_test_paths(red_paths, None, str(tmp_path))

        assert result == sorted(red_paths), (
            f"AC4: red_sha=None → result must equal sorted(red_paths); got {result}"
        )


# ─── AC5-AC6: _run_plan_failed_total helper ───────────────────────────────────

class TestAC05RunPlanFailedTotalSumsGroups:
    # AC5: _run_plan_failed_total sums n_failed across groups on HEAD → returns int
    def test_ac05_sums_n_failed_across_groups(self, monkeypatch, tmp_path):
        from bytedigger_engine.workflows.phase_5_implement import _run_plan_failed_total  # type: ignore[attr-defined]

        plan = {
            "groups": [
                {"argv": ["pytest", "tests/test_a.py"]},
                {"argv": ["pytest", "tests/test_b.py"]},
            ]
        }
        call_count = [0]

        def _fake_run(argv, cwd, timeout=120):
            call_count[0] += 1
            return _fake_run_result(n_failed=call_count[0])  # 1 then 2 → total 3

        monkeypatch.setattr(_p5, "run_test_command", _fake_run)

        result = _run_plan_failed_total(plan, str(tmp_path))

        assert isinstance(result, int), (
            f"AC5: _run_plan_failed_total must return int; got {type(result)!r}: {result!r}"
        )
        assert result == 3, (
            f"AC5: expected 1+2=3; got {result}"
        )


class TestAC06RunPlanFailedTotalNoneOnError:
    # AC6: _run_plan_failed_total returns None on FileNotFoundError AND on timeout sentinel
    def test_ac06a_returns_none_on_file_not_found(self, monkeypatch, tmp_path):
        from bytedigger_engine.workflows.phase_5_implement import _run_plan_failed_total  # type: ignore[attr-defined]

        plan = {"groups": [{"argv": ["pytest", "tests/test_x.py"]}]}

        def _raise_fnf(argv, cwd, timeout=120):
            raise FileNotFoundError("pytest not found")

        monkeypatch.setattr(_p5, "run_test_command", _raise_fnf)

        result = _run_plan_failed_total(plan, str(tmp_path))
        assert result is None, (
            f"AC6a: FileNotFoundError → must return None; got {result!r}"
        )

    def test_ac06b_returns_none_on_timeout_sentinel(self, monkeypatch, tmp_path):
        from bytedigger_engine.workflows.phase_5_implement import _run_plan_failed_total  # type: ignore[attr-defined]

        plan = {"groups": [{"argv": ["pytest", "tests/test_x.py"]}]}

        def _timeout_result(argv, cwd, timeout=120):
            return _fake_run_result(n_failed=sys.maxsize, exit_code=124)

        monkeypatch.setattr(_p5, "run_test_command", _timeout_result)

        result = _run_plan_failed_total(plan, str(tmp_path))
        assert result is None, (
            f"AC6b: timeout sentinel (exit_code=124 + n_failed=maxsize) → must return None; got {result!r}"
        )


# ─── AC7-AC10: integration via _verify_green_passing ─────────────────────────

def _common_integration_patches(monkeypatch, tmp_path, prod_file_rel: str,
                                 sib_current: int, sib_baseline: int,
                                 scoped_n_failed: int = 0):
    """Wire up all the monkeypatches needed for the sibling-expand integration ACs.

    - git_diff_files → [prod_file_rel] (one changed prod file)
    - _verify_green_passing scoped path → scoped_n_failed failures (default 0 = PASS)
    - _run_plan_failed_total → sib_current
    - _compute_baseline_failed → sib_baseline for the sibling plan
    - _infer_test_command_for_paths → minimal fake plan
    """
    # Scoped (red path) plan — used for the _verify_green_passing main loop
    scoped_fake_plan = {
        "groups": [{"kind": "py", "argv": ["pytest", "tests/test_red.py"]}],
        "skipped": False,
    }

    # Sibling plan — returned for the sibling paths
    sib_fake_plan = {
        "groups": [{"kind": "py", "argv": ["pytest", "tests/test_red.py", str(tmp_path / "src" / "test_foo.py")]}],
        "skipped": False,
    }

    # _infer_test_command_for_paths: return different plans based on whether sib paths were passed
    def _fake_infer(paths, git_cwd=None):
        return scoped_fake_plan

    monkeypatch.setattr(_p5, "_infer_test_command_for_paths", _fake_infer)

    # verify_count_reproducible: always ok
    monkeypatch.setattr(_p5, "verify_count_reproducible",
                        lambda _runner, _argv, _cwd: (True, [(5, 0), (5, 0)]))

    # run_test_command: scoped path returns scoped_n_failed
    monkeypatch.setattr(_p5, "run_test_command",
                        lambda argv, cwd, timeout=120: _fake_run_result(n_failed=scoped_n_failed))

    # git_diff_files → prod file (triggers sibling expand)
    monkeypatch.setattr(_p5, "git_diff_files", lambda sha, cwd, untracked=True, segment_filter=None: [prod_file_rel])

    # _run_plan_failed_total → sib_current
    monkeypatch.setattr(_p5, "_run_plan_failed_total", lambda plan, cwd: sib_current)

    # _compute_baseline_failed → sib_baseline for sibling plan, 0 for scoped
    baseline_calls = [0]
    def _fake_baseline(plan, cwd, source):
        baseline_calls[0] += 1
        return sib_baseline

    monkeypatch.setattr(_p5, "_compute_baseline_failed", _fake_baseline)

    return sib_fake_plan


class TestAC07SiblingDeltaBlocksWhenScopedPasses:
    # AC7 (#837 core): scoped red tests PASS (failing_groups empty) BUT sibling delta
    # net_new>0 → _verify_green_passing returns status="error", error_code="E_GREEN_NOT_PASSING"
    # proving the block runs INDEPENDENT of failing_groups
    def test_ac07_scoped_passes_but_sibling_regression_blocks(self, monkeypatch, tmp_path):
        # Create prod file and its sibling on disk (so _sibling_test_paths finds it)
        prod_dir = tmp_path / "src"
        prod_dir.mkdir()
        (prod_dir / "foo.py").write_text("# prod\n")
        sibling = prod_dir / "test_foo.py"
        sibling.write_text("# sibling test\n")

        red_path = str(tmp_path / "tests" / "test_red.py")
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_red.py").write_text("# red test\n")

        _common_integration_patches(
            monkeypatch, tmp_path,
            prod_file_rel="src/foo.py",
            sib_current=1,     # sibling FAILS at current
            sib_baseline=0,    # sibling PASSED at baseline → net_new=1
            scoped_n_failed=0, # scoped tests PASS → failing_groups empty
        )
        captured = _patch_emit(monkeypatch)

        ctx = _make_ctx(
            git_cwd=str(tmp_path),
            verify_green_delta_enforce=True,
        )
        prev = _make_prev([red_path], red_commit_sha="abc123sha")

        result = _verify_green_passing(ctx, prev)

        assert result.status == "error", (
            f"AC7: scoped PASS + sibling net_new>0 must return status='error'; got {result.status!r}"
        )
        assert result.error_code == "E_GREEN_NOT_PASSING", (
            f"AC7: error_code must be 'E_GREEN_NOT_PASSING'; got {result.error_code!r}"
        )


class TestAC08SiblingSafetyPreexistingNoBlock:
    # AC8 (safety): sibling fails at BOTH baseline AND current (sib_baseline==sib_current==2)
    # → net_new=0 → NO block; returns status="ok"
    def test_ac08_preexisting_sibling_failure_does_not_block(self, monkeypatch, tmp_path):
        prod_dir = tmp_path / "src"
        prod_dir.mkdir()
        (prod_dir / "foo.py").write_text("# prod\n")
        sibling = prod_dir / "test_foo.py"
        sibling.write_text("# sibling test\n")

        red_path = str(tmp_path / "tests" / "test_red.py")
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_red.py").write_text("# red test\n")

        _common_integration_patches(
            monkeypatch, tmp_path,
            prod_file_rel="src/foo.py",
            sib_current=2,     # sibling fails at current
            sib_baseline=2,    # sibling also failed at baseline → net_new=0
            scoped_n_failed=0, # scoped tests PASS
        )
        _patch_emit(monkeypatch)

        ctx = _make_ctx(
            git_cwd=str(tmp_path),
            verify_green_delta_enforce=True,
        )
        prev = _make_prev([red_path], red_commit_sha="abc123sha")

        result = _verify_green_passing(ctx, prev)

        assert result.status == "ok", (
            f"AC8: pre-existing sibling failures (net_new=0) must NOT block; got {result.status!r}"
        )


class TestAC09SiblingExpandFlagOff:
    # AC9: verify_green_delta_sibling_expand=False → sibling block fully skipped
    # (no verify_green_sibling_delta_verdict emit, no extra test run)
    def test_ac09_flag_off_skips_sibling_block(self, monkeypatch, tmp_path):
        prod_dir = tmp_path / "src"
        prod_dir.mkdir()
        (prod_dir / "foo.py").write_text("# prod\n")
        sibling = prod_dir / "test_foo.py"
        sibling.write_text("# sibling test\n")

        red_path = str(tmp_path / "tests" / "test_red.py")
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_red.py").write_text("# red test\n")

        run_plan_calls = [0]

        def _run_plan_spy(plan, cwd):
            run_plan_calls[0] += 1
            return 1

        _common_integration_patches(
            monkeypatch, tmp_path,
            prod_file_rel="src/foo.py",
            sib_current=1,
            sib_baseline=0,
            scoped_n_failed=0,
        )
        # Override _run_plan_failed_total with spy
        monkeypatch.setattr(_p5, "_run_plan_failed_total", _run_plan_spy)

        captured = _patch_emit(monkeypatch)

        ctx = _make_ctx(
            git_cwd=str(tmp_path),
            verify_green_delta_sibling_expand=False,  # FLAG OFF
            verify_green_delta_enforce=True,
        )
        prev = _make_prev([red_path], red_commit_sha="abc123sha")

        result = _verify_green_passing(ctx, prev)

        # Block skipped: no sibling verdict event
        sib_events = [e for e in captured if e["type"] == "verify_green_sibling_delta_verdict"]
        assert sib_events == [], (
            f"AC9: flag-off → no verify_green_sibling_delta_verdict emitted; got {sib_events}"
        )
        # Block skipped: _run_plan_failed_total not called (no sibling test run)
        assert run_plan_calls[0] == 0, (
            f"AC9: flag-off → _run_plan_failed_total must not be called; called {run_plan_calls[0]} times"
        )
        # Result must be ok (scoped tests pass, block skipped)
        assert result.status == "ok", (
            f"AC9: flag-off → must return status='ok'; got {result.status!r}"
        )


class TestAC10EmitsVerdictEvent:
    # AC10: emits verify_green_sibling_delta_verdict with net_new + n_sibling_paths keys
    # when the block runs and produces a verdict
    def test_ac10_emits_sibling_delta_verdict_event(self, monkeypatch, tmp_path):
        prod_dir = tmp_path / "src"
        prod_dir.mkdir()
        (prod_dir / "foo.py").write_text("# prod\n")
        sibling = prod_dir / "test_foo.py"
        sibling.write_text("# sibling test\n")

        red_path = str(tmp_path / "tests" / "test_red.py")
        (tmp_path / "tests").mkdir()
        (tmp_path / "tests" / "test_red.py").write_text("# red test\n")

        _common_integration_patches(
            monkeypatch, tmp_path,
            prod_file_rel="src/foo.py",
            sib_current=1,
            sib_baseline=0,
            scoped_n_failed=0,
        )
        captured = _patch_emit(monkeypatch)

        ctx = _make_ctx(
            git_cwd=str(tmp_path),
            verify_green_delta_enforce=False,  # don't block — just emit
        )
        prev = _make_prev([red_path], red_commit_sha="abc123sha")

        _verify_green_passing(ctx, prev)

        sib_events = [e for e in captured if e["type"] == "verify_green_sibling_delta_verdict"]
        assert len(sib_events) >= 1, (
            f"AC10: expected verify_green_sibling_delta_verdict event; "
            f"captured types={[e['type'] for e in captured]}"
        )
        payload = sib_events[0]["payload"]
        assert "net_new" in payload, f"AC10: payload missing 'net_new'; got {payload!r}"
        assert "n_sibling_paths" in payload, f"AC10: payload missing 'n_sibling_paths'; got {payload!r}"
        assert "baseline_failed" in payload, f"AC10: payload missing 'baseline_failed'; got {payload!r}"
        assert "current_failed" in payload, f"AC10: payload missing 'current_failed'; got {payload!r}"
        assert "classification" in payload, f"AC10: payload missing 'classification'; got {payload!r}"
        assert payload.get("phase") == 5, f"AC10: payload 'phase' must be 5; got {payload!r}"


# ─── AC11: regression note ─────────────────────────────────────────────────────

class TestAC11RegressionNote:
    # AC11 (§1r regression): full tests/test_phase_5_* suite stays GREEN — the additive
    # block is inert for all existing fixtures (new_sib empty there). Delegated to
    # orchestrator full-suite verify per §1r; not executable here without Bash.
    @pytest.mark.skip(
        reason=(
            "AC11 — §1r regression guard: full phase_5 suite delta must be 0 post-GREEN. "
            "Orchestrator verifies via `pytest tests/test_phase_5_*.py` before + after. "
            "The additive sibling-expand block is inert when new_sib is empty (all existing "
            "fixtures pass no red_sha or git_diff_files yields no prod file with an existing "
            "conventional sibling). This test is a deliberate marker — not a false-pass."
        )
    )
    def test_ac11_full_phase5_suite_stays_green_post_green(self):
        pass
