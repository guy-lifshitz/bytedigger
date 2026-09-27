"""bd#88 (P7) — baseline once on the RED commit; GREEN delta by test ID, no ``-x``.

S1  pytest runner prefix carries no ``-x`` and asks for the short summary.
S2  pure ID helpers: parse fail ids, id-based delta verdict.
S3  ``_red_commit_baseline_fail_ids``: detached worktree at red_sha, cached,
    never stashes, refuses an ambient cwd.
S4  ``_verify_green_passing``: sibling delta by ID (a swap blocks), fail-closed
    on a missing sibling baseline, a red RED test is never released, no stash.
S5  ``run_baseline_delta_gate``: base sha / cache dir threaded, stderr in the event.
S6  ``baseline_delta_gate.py``: a missing ledger is an empty ledger.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from bytedigger_engine.contracts import StepResult, WorkflowContext
from bytedigger_engine.lib.plugins.disk_truth.test_runner import TestRunResult
from bytedigger_engine.workflows import phase_5_implement as _p5
from bytedigger_engine.workflows import _baseline_delta as _bd

_REPO_ROOT = Path(__file__).resolve().parents[2]
_GATE_SCRIPT = _REPO_ROOT / "baseline_delta_gate.py"


# ─── helpers ──────────────────────────────────────────────────────────────────

def _ctx(tmp_path: Path, **extra) -> WorkflowContext:
    return WorkflowContext(
        tenant_id="bd88", scope=None, db_path=None,
        org_config={"git_cwd": str(tmp_path), "verify_green_delta_enforce": True, **extra},
        question="bd88", session_id="bd88", persona="bd88", framework=None, domain=None,
    )


def _prev(paths: list[str], red_sha: str | None = "redsha") -> StepResult:
    data: dict[str, Any] = {"red_test_paths": paths}
    if red_sha:
        data["red_commit_sha"] = red_sha
    return StepResult(status="ok", data=data, duration_ms=0, step_name="verify_security_lint")


def _result(tmp_path: Path, fail_ids: list[str], n_passed: int = 3) -> TestRunResult:
    out = tmp_path / f"out_{len(list(tmp_path.glob('out_*')))}.txt"
    lines = [f"FAILED {i} - AssertionError" for i in fail_ids]
    lines.append(f"{len(fail_ids)} failed, {n_passed} passed in 0.1s" if fail_ids else f"{n_passed} passed in 0.1s")
    out.write_text("\n".join(lines) + "\n")
    return TestRunResult(
        exit_code=1 if fail_ids else 0, n_passed=n_passed, n_failed=len(fail_ids),
        stdout_path=str(out), stderr_path=str(out),
    )


def _events(monkeypatch) -> list[dict]:
    captured: list[dict] = []
    monkeypatch.setattr(
        _p5, "_emit_safe",
        lambda t, p, severity="warning": captured.append({"type": t, "payload": p}),
    )
    return captured


def _wire_verify_green(monkeypatch, tmp_path, *, red_fails, sib_current, baseline_for):
    """Drive _verify_green_passing with one py RED group and one py sibling."""
    red = tmp_path / "tests" / "test_red.py"
    red.parent.mkdir(parents=True, exist_ok=True)
    red.write_text("# red\n")
    sib = str(tmp_path / "tests" / "test_foo.py")
    monkeypatch.setattr(_p5, "verify_count_reproducible", lambda _r, _a, _c: (True, [(3, 0)]))
    monkeypatch.setattr(
        _p5, "run_test_command",
        lambda argv, cwd, timeout=120: _result(tmp_path, red_fails),
    )
    monkeypatch.setattr(_p5, "_sibling_test_paths", lambda paths, sha, cwd: sorted({str(red), sib}))
    monkeypatch.setattr(_p5, "_run_plan_fail_ids", lambda plan, cwd: sib_current)
    baseline_calls: list[list[str]] = []

    def _baseline(paths, red_sha, git_cwd, git_cwd_source, cache_dir):
        baseline_calls.append(list(paths))
        return baseline_for(paths)

    monkeypatch.setattr(_p5, "_red_commit_baseline_fail_ids", _baseline)
    monkeypatch.setattr(_p5, "_get_diff_added_files", lambda sha, cwd: set())
    monkeypatch.setattr(_p5, "_terminal_green_result",
                        lambda step, data, msg, *a, **k: StepResult(
                            status="error", data=data, duration_ms=0, step_name=step,
                            error=msg, error_code="E_GREEN_NOT_PASSING", recoverable=False))
    monkeypatch.setattr(_p5, "run_baseline_delta_gate", lambda *a, **k: {"skipped": "stub"})
    return str(red), sib, baseline_calls


# ─── S1 ───────────────────────────────────────────────────────────────────────

class TestS1NoFailFast:
    def test_fallback_prefix_has_no_x(self, monkeypatch, tmp_path):
        monkeypatch.setattr(_p5, "_venv_pytest", lambda cwd: None)
        monkeypatch.setattr(_p5, "_main_checkout_root", lambda cwd: None)
        prefix = _p5._runner_for_path("tests/test_a.py", git_cwd=str(tmp_path))["argv_prefix"]
        assert "-x" not in prefix
        assert prefix == ["python3", "-m", "pytest", "--tb=no", "-q", "-rfE"]

    def test_venv_prefix_has_no_x(self, monkeypatch, tmp_path):
        monkeypatch.setattr(_p5, "_venv_pytest", lambda cwd: "/v/bin/pytest")
        prefix = _p5._runner_for_path("tests/test_a.py", git_cwd=str(tmp_path))["argv_prefix"]
        assert prefix == ["/v/bin/pytest", "--tb=no", "-q", "-rfE"]


# ─── S2 ───────────────────────────────────────────────────────────────────────

class TestS2IdHelpers:
    def test_parse_fail_ids(self):
        from bytedigger_engine.net_new_delta import parse_pytest_fail_ids
        text = (
            "..F\n"
            "FAILED tests/test_a.py::test_x - assert 1 == 2\n"
            "ERROR tests/test_b.py - ImportError\n"
            "FAILED tests/test_a.py::test_x - dup\n"
            "1 failed, 1 error in 0.1s\n"
        )
        assert parse_pytest_fail_ids(text) == frozenset({"tests/test_a.py::test_x", "tests/test_b.py"})

    def test_swap_is_a_regression(self):
        from bytedigger_engine.net_new_delta import id_delta_verdict
        v = id_delta_verdict(frozenset({"t::a"}), frozenset({"t::b"}), enforce=True, fail_closed=True)
        assert v.new_ids == ("t::b",)
        assert v.classification == "net_new_regression"
        assert v.would_block is True

    def test_preexisting_only(self):
        from bytedigger_engine.net_new_delta import id_delta_verdict
        v = id_delta_verdict(frozenset({"t::a", "t::b"}), frozenset({"t::a"}), enforce=True, fail_closed=True)
        assert v.new_ids == ()
        assert v.classification == "preexisting_only"
        assert v.would_block is False

    def test_unavailable_fail_closed_vs_open(self):
        from bytedigger_engine.net_new_delta import id_delta_verdict
        closed = id_delta_verdict(None, frozenset({"t::b", "t::a"}), enforce=True, fail_closed=True)
        assert closed.classification == "baseline_unavailable"
        assert closed.new_ids == ("t::a", "t::b")
        assert closed.would_block is True
        open_ = id_delta_verdict(None, frozenset({"t::a"}), enforce=True, fail_closed=False)
        assert open_.new_ids == ()
        assert open_.would_block is False

    def test_clean(self):
        from bytedigger_engine.net_new_delta import id_delta_verdict
        v = id_delta_verdict(frozenset({"t::a"}), frozenset(), enforce=True, fail_closed=True)
        assert v.classification == "clean"
        assert v.would_block is False


# ─── S3 — real git repo ──────────────────────────────────────────────────────

def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(cwd), check=True, capture_output=True, text=True,
    ).stdout


@pytest.fixture
def red_repo(tmp_path, monkeypatch):
    """RED commit: test_a fails, test_b passes. Working tree (GREEN): flipped."""
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "pytest.ini").write_text("[pytest]\n")
    (repo / "mod.py").write_text("A = 0\nB = 1\n")
    (repo / "tests" / "test_s.py").write_text(
        "import sys, pathlib\n"
        "sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))\n"
        "import mod\n"
        "def test_a():\n    assert mod.A == 1\n"
        "def test_b():\n    assert mod.B == 1\n"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "red")
    red_sha = _git(repo, "rev-parse", "HEAD").strip()
    (repo / "mod.py").write_text("A = 1\nB = 0\n")
    (repo / "untracked.txt").write_text("keep me\n")
    monkeypatch.setattr(_p5, "_venv_pytest", lambda cwd: None)
    monkeypatch.setattr(_p5, "_main_checkout_root", lambda cwd: None)
    monkeypatch.setattr(
        _p5, "_runner_for_path",
        lambda p, git_cwd=None: {"kind": "py", "argv_prefix": [sys.executable, "-m", "pytest", "--tb=no", "-q", "-rfE", "-p", "no:cacheprovider"]}
        if str(p).endswith(".py") else None,
    )
    return repo, red_sha


class TestS3RedCommitBaseline:
    def test_ids_come_from_red_commit_not_working_tree(self, red_repo, tmp_path):
        repo, red_sha = red_repo
        status_before = _git(repo, "status", "--porcelain")
        ids = _p5._red_commit_baseline_fail_ids(
            ["tests/test_s.py"], red_sha, str(repo), "org_config", str(tmp_path / "cache"),
        )
        assert ids == frozenset({"tests/test_s.py::test_a"})
        assert _git(repo, "status", "--porcelain") == status_before
        assert _git(repo, "stash", "list") == ""
        assert (repo / "untracked.txt").read_text() == "keep me\n"
        assert len(_git(repo, "worktree", "list").splitlines()) == 1

    def test_second_call_hits_cache(self, red_repo, tmp_path, monkeypatch):
        repo, red_sha = red_repo
        cache = str(tmp_path / "cache")
        first = _p5._red_commit_baseline_fail_ids(["tests/test_s.py"], red_sha, str(repo), "org_config", cache)
        runs: list = []
        monkeypatch.setattr(_p5, "run_test_command", lambda *a, **k: runs.append(a))
        second = _p5._red_commit_baseline_fail_ids(
            [str(repo / "tests" / "test_s.py")], red_sha, str(repo), "org_config", cache,
        )
        assert second == first
        assert runs == []
        fails_file = Path(cache) / f"{red_sha}.pytest.fails"
        assert fails_file.read_text().split() == ["tests/test_s.py::test_a"]

    def test_path_absent_at_red_sha_contributes_nothing(self, red_repo, tmp_path):
        repo, red_sha = red_repo
        (repo / "tests" / "test_new.py").write_text("def test_n():\n    assert False\n")
        ids = _p5._red_commit_baseline_fail_ids(
            ["tests/test_new.py"], red_sha, str(repo), "org_config", str(tmp_path / "cache"),
        )
        assert ids == frozenset()

    def test_ambient_cwd_refused(self, red_repo, tmp_path, monkeypatch):
        repo, red_sha = red_repo
        events = _events(monkeypatch)
        ids = _p5._red_commit_baseline_fail_ids(
            ["tests/test_s.py"], red_sha, str(repo), "cwd", str(tmp_path / "cache"),
        )
        assert ids is None
        assert any(e["type"] == "baseline_skipped_ambient_cwd" for e in events)
        assert len(_git(repo, "worktree", "list").splitlines()) == 1

    def test_stash_baseline_removed(self):
        assert not hasattr(_p5, "_compute_baseline_failed")
        assert not hasattr(_p5, "_run_plan_failed_total")


# ─── S4 — verify_green ───────────────────────────────────────────────────────

class TestS4VerifyGreen:
    def test_sibling_swap_blocks(self, monkeypatch, tmp_path):
        red, sib, _ = _wire_verify_green(
            monkeypatch, tmp_path, red_fails=[],
            sib_current=frozenset({"tests/test_foo.py::test_b"}),
            baseline_for=lambda paths: frozenset({"tests/test_foo.py::test_a"}),
        )
        events = _events(monkeypatch)
        r = _p5._verify_green_passing(_ctx(tmp_path), _prev([red]))
        assert r.status == "error"
        ev = [e for e in events if e["type"] == "verify_green_sibling_delta_verdict"]
        assert ev and ev[0]["payload"]["new_ids"] == ["tests/test_foo.py::test_b"]

    def test_sibling_preexisting_passes(self, monkeypatch, tmp_path):
        red, sib, _ = _wire_verify_green(
            monkeypatch, tmp_path, red_fails=[],
            sib_current=frozenset({"tests/test_foo.py::test_a"}),
            baseline_for=lambda paths: frozenset({"tests/test_foo.py::test_a"}),
        )
        r = _p5._verify_green_passing(_ctx(tmp_path), _prev([red]))
        assert r.status == "ok"

    def test_sibling_baseline_unavailable_fails_closed(self, monkeypatch, tmp_path):
        red, sib, _ = _wire_verify_green(
            monkeypatch, tmp_path, red_fails=[],
            sib_current=frozenset({"tests/test_foo.py::test_a"}),
            baseline_for=lambda paths: None,
        )
        events = _events(monkeypatch)
        r = _p5._verify_green_passing(_ctx(tmp_path), _prev([red]))
        assert r.status == "error"
        ev = [e for e in events if e["type"] == "verify_green_sibling_delta_verdict"]
        assert ev and ev[0]["payload"]["baseline_available"] is False

    def test_remaining_red_red_test_is_never_released(self, monkeypatch, tmp_path):
        """Acceptance: GREEN with one red RED test that was already red at the
        RED commit (preexisting by ID) still FAILS the step."""
        red, sib, _ = _wire_verify_green(
            monkeypatch, tmp_path, red_fails=["tests/test_red.py::test_one"],
            sib_current=frozenset(),
            baseline_for=lambda paths: frozenset({"tests/test_red.py::test_one"}),
        )
        r = _p5._verify_green_passing(_ctx(tmp_path), _prev([red]))
        assert r.status == "error"
        assert r.error_code == "E_GREEN_NOT_PASSING"

    def test_red_group_new_id_is_terminal_net_new(self, monkeypatch, tmp_path):
        red, sib, _ = _wire_verify_green(
            monkeypatch, tmp_path, red_fails=["tests/test_red.py::test_two"],
            sib_current=frozenset(),
            baseline_for=lambda paths: frozenset({"tests/test_red.py::test_one"}),
        )
        events = _events(monkeypatch)
        r = _p5._verify_green_passing(_ctx(tmp_path), _prev([red]))
        assert r.status == "error"
        assert r.recoverable is False
        ev = [e for e in events if e["type"] == "verify_green_delta_verdict"]
        assert ev and ev[0]["payload"]["new_ids"] == ["tests/test_red.py::test_two"]

    def test_no_git_stash_during_verify_green(self, monkeypatch, tmp_path):
        red, sib, _ = _wire_verify_green(
            monkeypatch, tmp_path, red_fails=["tests/test_red.py::test_one"],
            sib_current=frozenset({"tests/test_foo.py::test_a"}),
            baseline_for=lambda paths: frozenset(),
        )
        cmds: list = []
        monkeypatch.setattr(_p5.git_write_port, "git_op_capture",
                            lambda cmd, cwd, timeout=30: cmds.append(cmd))
        _p5._verify_green_passing(_ctx(tmp_path), _prev([red]))
        assert not any("stash" in c for c in cmds)

    def test_baseline_gate_gets_red_sha(self, monkeypatch, tmp_path):
        red, sib, _ = _wire_verify_green(
            monkeypatch, tmp_path, red_fails=["tests/test_red.py::test_one"],
            sib_current=frozenset(),
            baseline_for=lambda paths: frozenset({"tests/test_red.py::test_one"}),
        )
        calls: list = []
        monkeypatch.setattr(_p5, "run_baseline_delta_gate",
                            lambda *a, **k: calls.append(k) or {"skipped": "stub"})
        _p5._verify_green_passing(_ctx(tmp_path), _prev([red]))
        assert calls and calls[0]["base_sha"] == "redsha"


# ─── S5 — _baseline_delta wiring ─────────────────────────────────────────────

class _Cfg:
    def gate_enabled(self, _n):
        return True

    def flag(self, _n):
        return False

    def path(self, _n, default):
        return default


class TestS5BaselineDeltaWiring:
    def test_driver_error_carries_stderr(self, monkeypatch, tmp_path):
        monkeypatch.setattr(_bd.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(
            a[0], 2, stdout="", stderr="ERROR baseline-delta-gate: ledger unreadable: boom\n"))
        events: list = []
        out = _bd.run_baseline_delta_gate(
            str(tmp_path / "r.txt"), "pytest", str(tmp_path), 5, "s",
            lambda t, p, **k: events.append((t, p)), cfg=_Cfg(),
        )
        assert out["skipped"] == "driver_error"
        skipped = [p for t, p in events if t == "baseline_delta_gate_skipped"]
        assert skipped and "ledger unreadable: boom" in skipped[0]["stderr"]

    def test_base_sha_and_cache_dir_threaded(self, monkeypatch, tmp_path):
        seen: list = []

        def _run(argv, **k):
            seen.append(argv)
            return subprocess.CompletedProcess(argv, 0, stdout=json.dumps({"verdict": "PASS"}), stderr="")

        monkeypatch.setattr(_bd.subprocess, "run", _run)
        _bd.run_baseline_delta_gate(
            str(tmp_path / "r.txt"), "pytest", str(tmp_path), 5, "s",
            lambda *a, **k: None, cfg=_Cfg(), base_sha="abc", cache_dir="/c",
        )
        argv = seen[0]
        assert argv[argv.index("--base-sha") + 1] == "abc"
        assert argv[argv.index("--cache-dir") + 1] == "/c"


# ─── S6 — missing ledger = empty ledger ──────────────────────────────────────

class TestS6MissingLedger:
    def test_missing_ledger_yields_verdict(self, tmp_path):
        results = tmp_path / "results.txt"
        results.write_text("FAILED tests/test_x.py::test_new - boom\n1 failed in 0.1s\n")
        baseline = tmp_path / "baseline.txt"
        baseline.write_text("")
        proc = subprocess.run(
            [sys.executable, str(_GATE_SCRIPT), "--results", str(results), "--suite", "pytest",
             "--baseline", str(baseline), "--ledger", str(tmp_path / "absent-known-reds.md")],
            cwd=str(_REPO_ROOT), capture_output=True, text=True,
        )
        assert proc.returncode != 2, proc.stderr
        out = json.loads(proc.stdout.strip().splitlines()[-1])
        assert out["ledger_source"] == "missing"
        assert out["delta_verdict"] == "FAIL"
        assert out["new_fails"] == ["tests/test_x.py::test_new"]
