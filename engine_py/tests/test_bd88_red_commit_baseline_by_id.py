"""bd#88 (P7) — baseline once on the RED commit; GREEN delta by test ID, no ``-x``.

S1  pytest runner prefix: no ``-x``, short summary, collection errors do not abort.
S2  pure ID helpers: parse, availability, id delta with file-level coverage.
S3  the RED run records the RED-path baseline (no extra run) into the cache.
S4  ``_red_commit_baseline_fail_ids``: sibling baseline from a detached worktree
    at red_sha, cached, never stashes, refuses an ambient cwd, never caches an
    unavailable run, cleans up on exceptions.
S5  ``_verify_green_passing``: sibling delta by ID over the siblings only (a swap
    blocks, fail-closed), RED-group baseline from the cache, a red RED test is
    never released, no stash.
S6  ``run_baseline_delta_gate``: base sha / cache dir threaded, stderr in the event.
S7  ``baseline_delta_gate.py``: a missing ledger is an empty ledger.
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
_REAL_BASELINE = getattr(_p5, "_red_commit_baseline_fail_ids", None)
_GATE_SCRIPT = _REPO_ROOT / "baseline_delta_gate.py"
_SRC = "cfg_git_cwd"
_PY_PREFIX = [sys.executable, "-m", "pytest", "--tb=no", "-q", "-rfE",
              "--continue-on-collection-errors", "-p", "no:cacheprovider"]


# ─── helpers ──────────────────────────────────────────────────────────────────

def _ctx(tmp_path: Path, **extra) -> WorkflowContext:
    return WorkflowContext(
        tenant_id="bd88", scope=None, db_path=None,
        org_config={
            "git_cwd": str(tmp_path), "scratchpad_dir": str(tmp_path / "sp"),
            "verify_green_delta_enforce": True, **extra,
        },
        question="bd88", session_id="bd88", persona="bd88", framework=None, domain=None,
    )


def _prev(paths: list[str], red_sha: str | None = "redsha") -> StepResult:
    data: dict[str, Any] = {"red_test_paths": paths}
    if red_sha:
        data["red_commit_sha"] = red_sha
    return StepResult(status="ok", data=data, duration_ms=0, step_name="verify_security_lint")


def _result(tmp_path: Path, fail_ids: list[str], n_passed: int = 3, exit_code: int | None = None) -> TestRunResult:
    out = tmp_path / f"out_{len(list(tmp_path.glob('out_*')))}.txt"
    lines = [f"FAILED {i} - AssertionError" for i in fail_ids]
    lines.append(f"{len(fail_ids)} failed, {n_passed} passed in 0.1s" if fail_ids else f"{n_passed} passed in 0.1s")
    out.write_text("\n".join(lines) + "\n")
    rc = exit_code if exit_code is not None else (1 if fail_ids else 0)
    return TestRunResult(exit_code=rc, n_passed=n_passed, n_failed=len(fail_ids),
                         stdout_path=str(out), stderr_path=str(out))


def _events(monkeypatch) -> list[dict]:
    captured: list[dict] = []
    monkeypatch.setattr(
        _p5, "_emit_safe",
        lambda t, p, severity="warning": captured.append({"type": t, "payload": p}),
    )
    return captured


def _seed_cache(tmp_path: Path, red_sha: str, covered: list[str], fail_ids: list[str]) -> Path:
    d = tmp_path / "sp" / "baseline"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{red_sha}.pytest.json").write_text(json.dumps({
        "covered": [str(Path(p).resolve()) for p in covered], "fail_ids": fail_ids,
    }))
    (d / f"{red_sha}.pytest.fails").write_text("".join(f"{i}\n" for i in fail_ids))
    return d


def _wire_verify_green(monkeypatch, tmp_path, *, red_fails, sib_current, sib_baseline,
                       red_baseline=None, red_sha="redsha"):
    """One py RED group + one py sibling. RED-group baseline seeded in the cache."""
    red = tmp_path / "tests" / "test_red.py"
    red.parent.mkdir(parents=True, exist_ok=True)
    red.write_text("# red\n")
    sib = str((tmp_path / "tests" / "test_foo.py").resolve())
    if red_baseline is not None:
        _seed_cache(tmp_path, red_sha, [str(red)], red_baseline)
    monkeypatch.setattr(_p5, "verify_count_reproducible", lambda _r, _a, _c: (True, [(3, 0)]))
    monkeypatch.setattr(_p5, "run_test_command",
                        lambda argv, cwd, timeout=120: _result(tmp_path, red_fails))
    monkeypatch.setattr(_p5, "_sibling_test_paths",
                        lambda paths, sha, cwd: sorted({str(red.resolve()), sib}))
    plans: list[list[str]] = []

    def _current(plan, cwd):
        plans.append([p for g in plan["groups"] for p in g["paths"]])
        return sib_current

    monkeypatch.setattr(_p5, "_run_plan_fail_ids", _current)
    baseline_calls: list[list[str]] = []
    sources: list[str] = []

    def _baseline(paths, sha, git_cwd, git_cwd_source, cache_dir):
        baseline_calls.append(sorted(paths))
        sources.append(git_cwd_source)
        return sib_baseline

    monkeypatch.setattr(_p5, "_red_commit_baseline_fail_ids", _baseline)
    monkeypatch.setattr(_p5, "_get_diff_added_files", lambda sha, cwd: set())
    monkeypatch.setattr(_p5, "_terminal_green_result",
                        lambda step, data, msg, *a, **k: StepResult(
                            status="error", data=data, duration_ms=0, step_name=step,
                            error=msg, error_code="E_GREEN_NOT_PASSING", recoverable=False))
    monkeypatch.setattr(_p5, "run_baseline_delta_gate", lambda *a, **k: {"skipped": "stub"})
    return str(red), sib, plans, baseline_calls, sources


# ─── S1 ───────────────────────────────────────────────────────────────────────

class TestS1RunnerPrefix:
    def test_fallback_prefix(self, monkeypatch, tmp_path):
        monkeypatch.setattr(_p5, "_venv_pytest", lambda cwd: None)
        monkeypatch.setattr(_p5, "_main_checkout_root", lambda cwd: None)
        prefix = _p5._runner_for_path("tests/test_a.py", git_cwd=str(tmp_path))["argv_prefix"]
        assert "-x" not in prefix
        assert prefix == ["python3", "-m", "pytest", "--tb=no", "-q", "-rfE",
                          "--continue-on-collection-errors"]

    def test_venv_prefix(self, monkeypatch, tmp_path):
        monkeypatch.setattr(_p5, "_venv_pytest", lambda cwd: "/v/bin/pytest")
        prefix = _p5._runner_for_path("tests/test_a.py", git_cwd=str(tmp_path))["argv_prefix"]
        assert prefix == ["/v/bin/pytest", "--tb=no", "-q", "-rfE", "--continue-on-collection-errors"]


# ─── S2 ───────────────────────────────────────────────────────────────────────

class TestS2IdHelpers:
    def test_parse_fail_ids(self):
        from bytedigger_engine.net_new_delta import parse_pytest_fail_ids
        text = (
            "..F\n"
            "FAILED tests/test_a.py::test_x - assert 1 == 2\n"
            "\x1b[31mERROR\x1b[0m tests/test_b.py - ImportError\n"
            "FAILED tests/test_a.py::test_p[a b] - boom\n"
            "FAILED tests/test_a.py::test_bare\n"
            "FAILED tests/test_a.py::test_x - dup\n"
            "1 failed, 1 error in 0.1s\n"
        )
        assert parse_pytest_fail_ids(text) == frozenset({
            "tests/test_a.py::test_x", "tests/test_b.py",
            "tests/test_a.py::test_p[a b]", "tests/test_a.py::test_bare",
        })

    @pytest.mark.parametrize("rc,text,expected", [
        (0, "3 passed in 0.1s\n", frozenset()),
        (5, "no tests ran in 0.01s\n", frozenset()),
        (1, "FAILED t.py::a - x\n1 failed\n", frozenset({"t.py::a"})),
        (1, "1 failed\n", None),                       # failure without an id
        (2, "ERROR t.py - ImportError\nInterrupted\n", None),
        (3, "INTERNALERROR>\n", None),
        (4, "usage: pytest\n", None),
        (124, "", None),
    ])
    def test_run_fail_ids_availability(self, rc, text, expected):
        from bytedigger_engine.net_new_delta import run_fail_ids
        assert run_fail_ids(rc, text) == expected

    def test_swap_is_a_regression(self):
        from bytedigger_engine.net_new_delta import id_delta_verdict
        v = id_delta_verdict(frozenset({"t::a"}), frozenset({"t::b"}), enforce=True, fail_closed=True)
        assert v.new_ids == ("t::b",)
        assert v.classification == "net_new_regression"
        assert v.baseline_available is True
        assert v.would_block is True

    def test_preexisting_only(self):
        from bytedigger_engine.net_new_delta import id_delta_verdict
        v = id_delta_verdict(frozenset({"t::a", "t::b"}), frozenset({"t::a"}), enforce=True, fail_closed=True)
        assert v.new_ids == ()
        assert v.classification == "preexisting_only"
        assert v.would_block is False

    def test_file_level_baseline_covers_test_level_ids(self):
        from bytedigger_engine.net_new_delta import id_delta_verdict
        v = id_delta_verdict(frozenset({"tests/test_red.py"}),
                             frozenset({"tests/test_red.py::test_x", "tests/test_red.pyc::t"}),
                             enforce=True, fail_closed=True)
        assert v.new_ids == ("tests/test_red.pyc::t",)

    def test_unavailable_fail_closed_vs_open(self):
        from bytedigger_engine.net_new_delta import id_delta_verdict
        closed = id_delta_verdict(None, frozenset({"t::b", "t::a"}), enforce=True, fail_closed=True)
        assert closed.classification == "baseline_unavailable"
        assert closed.baseline_available is False
        assert closed.new_ids == ("t::a", "t::b")
        assert closed.would_block is True
        open_ = id_delta_verdict(None, frozenset({"t::a"}), enforce=True, fail_closed=False)
        assert open_.new_ids == ()
        assert open_.would_block is False

    def test_clean_and_not_enforced(self):
        from bytedigger_engine.net_new_delta import id_delta_verdict
        assert id_delta_verdict(frozenset({"t::a"}), frozenset(), enforce=True, fail_closed=True).classification == "clean"
        assert id_delta_verdict(frozenset(), frozenset({"t::a"}), enforce=False, fail_closed=True).would_block is False


# ─── S3 — RED run records the RED-path baseline ──────────────────────────────

class TestS3RedRunRecordsBaseline:
    def _drive(self, monkeypatch, tmp_path, stdout, verdict_status="ok", red_sha="redsha",
               passing_kind=None, verdict_sha=None, red_rc=None):
        red = tmp_path / "tests" / "test_red.py"
        red.parent.mkdir(parents=True, exist_ok=True)
        red.write_text("# red\n")
        monkeypatch.setattr(_p5, "_resolve_git_cwd", lambda ctx, prev=None: str(tmp_path))
        monkeypatch.setattr(_p5, "_verify_red_dirty_tree_guard", lambda *a, **k: None)
        monkeypatch.setattr(_p5, "_infer_test_command_for_paths", lambda paths, git_cwd=None: {
            "groups": [{"kind": "py", "argv": ["pytest", str(red)], "paths": [str(red)]}]})
        rc = red_rc if red_rc is not None else (1 if "failed" in stdout else 0)
        monkeypatch.setattr(_p5, "_run_red_group", lambda g, c, p, cwd: (None, passing_kind, stdout, rc))
        monkeypatch.setattr(_p5, "_decide_red_verdict", lambda ctx, prev, *a: StepResult(
            status=verdict_status,
            data={**prev.data, **({"red_commit_sha": verdict_sha} if verdict_sha else {})},
            duration_ms=0, step_name="verify_red_fails_mechanically"))
        prev = _prev([str(red)], red_sha=red_sha)
        return _p5._verify_red_fails_mechanically(_ctx(tmp_path), prev), red

    def test_records_ids_for_red_sha(self, monkeypatch, tmp_path):
        r, red = self._drive(monkeypatch, tmp_path,
                             "FAILED tests/test_red.py::test_one - x\n1 failed in 0.1s\n")
        assert r.status == "ok"
        cache = tmp_path / "sp" / "baseline"
        data = json.loads((cache / "redsha.pytest.json").read_text())
        assert data["fail_ids"] == ["tests/test_red.py::test_one"]
        assert data["covered"] == [str(red.resolve())]
        assert (cache / "redsha.pytest.fails").read_text().split() == ["tests/test_red.py::test_one"]
        assert _p5._cached_baseline_fail_ids(str(cache), "redsha", [str(red)]) == frozenset(
            {"tests/test_red.py::test_one"})

    def test_uncovered_path_is_a_miss(self, monkeypatch, tmp_path):
        self._drive(monkeypatch, tmp_path, "FAILED tests/test_red.py::test_one - x\n1 failed\n")
        cache = str(tmp_path / "sp" / "baseline")
        assert _p5._cached_baseline_fail_ids(cache, "redsha", [str(tmp_path / "tests" / "other.py")]) is None
        assert _p5._cached_baseline_fail_ids(cache, "othersha", [str(tmp_path / "tests" / "test_red.py")]) is None
        assert _p5._cached_baseline_fail_ids(None, "redsha", [str(tmp_path / "tests" / "test_red.py")]) is None

    def test_not_recorded_when_verdict_not_ok(self, monkeypatch, tmp_path):
        self._drive(monkeypatch, tmp_path, "5 passed\n", verdict_status="error")
        assert not (tmp_path / "sp" / "baseline" / "redsha.pytest.json").exists()

    def test_not_recorded_on_restore_or_resume(self, monkeypatch, tmp_path):
        """GH1034 restore / GH483 resume: verdict ok, a group passed, the verdict
        may carry a new sha — the degenerate stdout must not become a baseline."""
        r, _ = self._drive(monkeypatch, tmp_path, "3 passed in 0.1s\n",
                           passing_kind="py", verdict_sha="restoredsha")
        assert r.status == "ok"
        base = tmp_path / "sp" / "baseline"
        assert not (base / "redsha.pytest.json").exists()
        assert not (base / "restoredsha.pytest.json").exists()

    def test_not_recorded_without_ids(self, monkeypatch, tmp_path):
        events = _events(monkeypatch)
        r, _ = self._drive(monkeypatch, tmp_path, "1 failed\n")
        assert r.status == "ok"
        assert not (tmp_path / "sp" / "baseline" / "redsha.pytest.json").exists()
        assert any(e["type"] == "red_commit_baseline_unavailable" for e in events)

    def test_not_recorded_from_interrupted_run(self, monkeypatch, tmp_path):
        """rc 2 (interrupted / pytest.exit) after some failures: a partial run
        must not claim coverage of tests that never ran."""
        events = _events(monkeypatch)
        self._drive(monkeypatch, tmp_path,
                    "FAILED tests/test_red.py::test_one - x\n!!! Interrupted !!!\n1 failed\n", red_rc=2)
        assert not (tmp_path / "sp" / "baseline" / "redsha.pytest.json").exists()
        assert any(e["payload"].get("reason") == "unreliable_run"
                   for e in events if e["type"] == "red_commit_baseline_unavailable")

    def test_corrupt_cache_is_reported(self, monkeypatch, tmp_path):
        events = _events(monkeypatch)
        d = tmp_path / "sp" / "baseline"
        d.mkdir(parents=True)
        (d / "redsha.pytest.json").write_text("{not json")
        assert _p5._cached_baseline_fail_ids(str(d), "redsha", [str(tmp_path / "x.py")]) is None
        assert any(e["type"] == "red_commit_baseline_cache_corrupt" for e in events)

    def test_not_recorded_without_red_sha(self, monkeypatch, tmp_path):
        r, _ = self._drive(monkeypatch, tmp_path, "FAILED tests/test_red.py::a - x\n1 failed\n", red_sha=None)
        assert r.status == "ok"
        assert not (tmp_path / "sp" / "baseline").exists() or not any((tmp_path / "sp" / "baseline").iterdir())


# ─── S4 — sibling baseline at red_sha (real git repo) ────────────────────────

def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=str(cwd), check=True,
                          capture_output=True, text=True).stdout


@pytest.fixture
def red_repo(tmp_path, monkeypatch):
    """No pytest ini, no sys.path hack: a root conftest (standard layout).
    RED commit: test_a fails, test_b passes, test_c always fails.
    Working tree (GREEN, uncommitted): A/B flipped."""
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "conftest.py").write_text("")
    (repo / "mod.py").write_text("A = 0\nB = 1\n")
    (repo / "tests" / "test_s.py").write_text(
        "import mod\n"
        "def test_a():\n    assert mod.A == 1\n"
        "def test_b():\n    assert mod.B == 1\n"
        "def test_c():\n    assert False\n"
    )
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "red")
    red_sha = _git(repo, "rev-parse", "HEAD").strip()
    (repo / "mod.py").write_text("A = 1\nB = 0\n")
    (repo / "untracked.txt").write_text("keep me\n")
    runner_calls: list = []

    def _runner(p, git_cwd=None):
        runner_calls.append((str(p), git_cwd))
        return {"kind": "py", "argv_prefix": list(_PY_PREFIX)} if str(p).endswith(".py") else None

    monkeypatch.setattr(_p5, "_runner_for_path", _runner)
    return repo, red_sha, runner_calls


def _wt_count(repo: Path) -> int:
    return len(_git(repo, "worktree", "list").splitlines())


class TestS4SiblingBaseline:
    def test_ids_come_from_red_commit_not_working_tree(self, red_repo, tmp_path):
        repo, red_sha, runner_calls = red_repo
        status_before = _git(repo, "status", "--porcelain")
        ids = _p5._red_commit_baseline_fail_ids(
            ["tests/test_s.py"], red_sha, str(repo), _SRC, str(tmp_path / "cache"))
        assert ids == frozenset({"tests/test_s.py::test_a", "tests/test_s.py::test_c"})
        assert _git(repo, "status", "--porcelain") == status_before
        assert _git(repo, "stash", "list") == ""
        assert (repo / "untracked.txt").read_text() == "keep me\n"
        assert _wt_count(repo) == 1
        # runner (venv) resolved from git_cwd, never from the temp worktree
        assert runner_calls and all(cwd == str(repo) for _, cwd in runner_calls)
        assert all(p.startswith(str(repo.resolve())) or p.startswith(str(repo)) for p, _ in runner_calls)

    def test_ids_match_current_side_ids(self, red_repo, tmp_path):
        repo, red_sha, _ = red_repo
        base = _p5._red_commit_baseline_fail_ids(
            [str(repo / "tests" / "test_s.py")], red_sha, str(repo), _SRC, str(tmp_path / "cache"))
        plan = _p5._infer_test_command_for_paths([str(repo / "tests" / "test_s.py")], git_cwd=str(repo))
        current = _p5._run_plan_fail_ids(plan, str(repo))
        assert current == frozenset({"tests/test_s.py::test_b", "tests/test_s.py::test_c"})
        assert "tests/test_s.py::test_c" in base

    def test_second_call_hits_cache(self, red_repo, tmp_path, monkeypatch):
        repo, red_sha, _ = red_repo
        cache = tmp_path / "cache"
        first = _p5._red_commit_baseline_fail_ids(["tests/test_s.py"], red_sha, str(repo), _SRC, str(cache))
        data = json.loads((cache / f"{red_sha}.pytest.json").read_text())
        assert data["covered"] == [str((repo / "tests" / "test_s.py").resolve())]
        runs: list = []
        monkeypatch.setattr(_p5, "run_test_command", lambda *a, **k: runs.append(a))
        worktree_adds: list = []
        real_capture = _p5.git_write_port.git_op_capture

        def _spy(cmd, cwd, timeout=30):
            worktree_adds.append(cmd)
            return real_capture(cmd, cwd=cwd, timeout=timeout)

        monkeypatch.setattr(_p5.git_write_port, "git_op_capture", _spy)
        second = _p5._red_commit_baseline_fail_ids(
            [str(repo / "tests" / "test_s.py")], red_sha, str(repo), _SRC, str(cache))
        assert second == first
        assert runs == [] and worktree_adds == []
        assert sorted((cache / f"{red_sha}.pytest.fails").read_text().split()) == [
            "tests/test_s.py::test_a", "tests/test_s.py::test_c"]

    def test_path_absent_at_red_sha_contributes_nothing(self, red_repo, tmp_path):
        repo, red_sha, _ = red_repo
        (repo / "tests" / "test_new.py").write_text("def test_n():\n    assert False\n")
        ids = _p5._red_commit_baseline_fail_ids(
            ["tests/test_new.py"], red_sha, str(repo), _SRC, str(tmp_path / "cache"))
        assert ids == frozenset()

    def test_ambient_cwd_refused(self, red_repo, tmp_path, monkeypatch):
        repo, red_sha, _ = red_repo
        events = _events(monkeypatch)
        ids = _p5._red_commit_baseline_fail_ids(
            ["tests/test_s.py"], red_sha, str(repo), "cwd", str(tmp_path / "cache"))
        assert ids is None
        assert any(e["type"] == "baseline_skipped_ambient_cwd" for e in events)
        assert _wt_count(repo) == 1

    def test_exception_cleans_worktree_and_reports(self, red_repo, tmp_path, monkeypatch):
        repo, red_sha, _ = red_repo
        events = _events(monkeypatch)

        def _boom(*a, **k):
            raise RuntimeError("runner exploded")

        monkeypatch.setattr(_p5, "run_test_command", _boom)
        ids = _p5._red_commit_baseline_fail_ids(
            ["tests/test_s.py"], red_sha, str(repo), _SRC, str(tmp_path / "cache"))
        assert ids is None
        assert _wt_count(repo) == 1
        assert any(e["type"] == "red_commit_baseline_unavailable" for e in events)
        assert not (tmp_path / "cache" / f"{red_sha}.pytest.json").exists()

    def test_unavailable_run_is_not_cached(self, red_repo, tmp_path, monkeypatch):
        repo, red_sha, _ = red_repo
        real = _p5.run_test_command
        broken = {"on": True}

        def _maybe_broken(argv, cwd, timeout=120):
            if broken["on"]:
                return _result(tmp_path, [], n_passed=0, exit_code=3)
            return real(argv, cwd, timeout)

        monkeypatch.setattr(_p5, "run_test_command", _maybe_broken)
        cache = str(tmp_path / "cache")
        assert _p5._red_commit_baseline_fail_ids(["tests/test_s.py"], red_sha, str(repo), _SRC, cache) is None
        broken["on"] = False
        assert _p5._red_commit_baseline_fail_ids(["tests/test_s.py"], red_sha, str(repo), _SRC, cache) == \
            frozenset({"tests/test_s.py::test_a", "tests/test_s.py::test_c"})

    def test_no_cache_dir_means_no_cache(self, red_repo, tmp_path, monkeypatch):
        repo, red_sha, _ = red_repo
        real = _p5.run_test_command
        runs: list = []

        def _count(argv, cwd, timeout=120):
            runs.append(argv)
            return real(argv, cwd, timeout)

        monkeypatch.setattr(_p5, "run_test_command", _count)
        for _ in range(2):
            _p5._red_commit_baseline_fail_ids(["tests/test_s.py"], red_sha, str(repo), _SRC, None)
        assert len(runs) == 2

    def test_worktree_imports_red_sha_code_not_pythonpath_tree(self, red_repo, tmp_path, monkeypatch):
        """No root conftest; the working tree is on PYTHONPATH. Without the
        worktree-first PYTHONPATH prefix the baseline would import GREEN code
        and report {test_b, test_c}."""
        repo, red_sha, _ = red_repo
        (repo / "conftest.py").unlink()
        _git(repo, "rm", "-q", "--cached", "conftest.py")
        _git(repo, "commit", "-q", "-m", "no conftest")
        red_sha = _git(repo, "rev-parse", "HEAD").strip()
        monkeypatch.setenv("PYTHONPATH", str(repo))
        # A console-script-like entry (as a venv `pytest` binary): unlike
        # `python -m`, it does not put the cwd (the worktree) on sys.path.
        entry = tmp_path / "pytest_entry.py"
        entry.write_text("import sys, pytest\nsys.exit(pytest.main())\n")
        prefix = [sys.executable, str(entry), "--tb=no", "-q", "-rfE",
                  "--continue-on-collection-errors", "-p", "no:cacheprovider"]
        monkeypatch.setattr(_p5, "_runner_for_path",
                            lambda p, git_cwd=None: {"kind": "py", "argv_prefix": list(prefix)})
        ids = _p5._red_commit_baseline_fail_ids(
            ["tests/test_s.py"], red_sha, str(repo), _SRC, str(tmp_path / "cache"))
        assert ids == frozenset({"tests/test_s.py::test_a", "tests/test_s.py::test_c"})

    def test_bad_red_sha_is_unavailable_not_absent(self, red_repo, tmp_path, monkeypatch):
        repo, _, _ = red_repo
        events = _events(monkeypatch)
        cache = tmp_path / "cache"
        ids = _p5._red_commit_baseline_fail_ids(
            ["tests/test_s.py"], "0" * 40, str(repo), _SRC, str(cache))
        assert ids is None
        assert not (cache / f"{'0' * 40}.pytest.json").exists()
        assert any(e["type"] == "red_commit_baseline_unavailable" for e in events)

    def test_package_in_subdirectory(self, tmp_path, monkeypatch):
        """git_cwd below the repo top (engine_py/-style layout), no conftest:
        the worktree run imports the red_sha package from git_cwd's counterpart."""
        repo = tmp_path / "mono"
        pkg = repo / "pkg"
        (pkg / "tests").mkdir(parents=True)
        _git(repo, "init", "-q")
        _git(repo, "config", "user.email", "t@example.com")
        _git(repo, "config", "user.name", "t")
        (pkg / "mod.py").write_text("A = 0\n")
        (pkg / "tests" / "test_p.py").write_text(
            "import mod\ndef test_a():\n    assert mod.A == 1\ndef test_c():\n    assert False\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", "red")
        red_sha = _git(repo, "rev-parse", "HEAD").strip()
        (pkg / "mod.py").write_text("A = 1\n")
        monkeypatch.setenv("PYTHONPATH", str(pkg))
        entry = tmp_path / "pytest_entry.py"
        entry.write_text("import sys, pytest\nsys.exit(pytest.main())\n")
        prefix = [sys.executable, str(entry), "--tb=no", "-q", "-rfE",
                  "--continue-on-collection-errors", "-p", "no:cacheprovider"]
        monkeypatch.setattr(_p5, "_runner_for_path",
                            lambda p, git_cwd=None: {"kind": "py", "argv_prefix": list(prefix)})
        ids = _p5._red_commit_baseline_fail_ids(
            ["tests/test_p.py"], red_sha, str(pkg), _SRC, str(tmp_path / "cache"))
        plan = {"groups": [{"kind": "py", "argv": prefix + [str(pkg / "tests" / "test_p.py")],
                            "paths": [str(pkg / "tests" / "test_p.py")]}]}
        current = _p5._run_plan_fail_ids(plan, str(pkg))
        # red_sha code (A == 0) was imported: test_a fails at the baseline only;
        # test_c fails on both sides under the identical id.
        assert len(current) == 1
        (c_id,) = current
        assert c_id.endswith("test_p.py::test_c")
        assert ids == frozenset({c_id, c_id.replace("test_c", "test_a")})

    def test_stash_baseline_removed(self):
        assert not hasattr(_p5, "_compute_baseline_failed")
        assert not hasattr(_p5, "_run_plan_failed_total")


# ─── S5 — verify_green ───────────────────────────────────────────────────────

class TestS5VerifyGreen:
    def test_sibling_swap_blocks_and_scopes_to_siblings(self, monkeypatch, tmp_path):
        red, sib, plans, baseline_calls, sources = _wire_verify_green(
            monkeypatch, tmp_path, red_fails=[],
            sib_current=frozenset({"tests/test_foo.py::test_b"}),
            sib_baseline=frozenset({"tests/test_foo.py::test_a"}),
        )
        events = _events(monkeypatch)
        r = _p5._verify_green_passing(_ctx(tmp_path), _prev([red]))
        assert r.status == "error"
        assert plans == [[sib]]
        assert baseline_calls == [[sib]]
        assert sources == ["cfg_git_cwd"]  # the resolver's label, forwarded
        ev = [e for e in events if e["type"] == "verify_green_sibling_delta_verdict"]
        assert ev and ev[0]["payload"]["new_ids"] == ["tests/test_foo.py::test_b"]

    def test_sibling_preexisting_passes(self, monkeypatch, tmp_path):
        red, *_ = _wire_verify_green(
            monkeypatch, tmp_path, red_fails=[],
            sib_current=frozenset({"tests/test_foo.py::test_a"}),
            sib_baseline=frozenset({"tests/test_foo.py::test_a"}),
        )
        assert _p5._verify_green_passing(_ctx(tmp_path), _prev([red])).status == "ok"

    def test_clean_siblings_skip_the_baseline(self, monkeypatch, tmp_path):
        red, sib, plans, baseline_calls, _ = _wire_verify_green(
            monkeypatch, tmp_path, red_fails=[],
            sib_current=frozenset(), sib_baseline=frozenset({"x"}),
        )
        assert _p5._verify_green_passing(_ctx(tmp_path), _prev([red])).status == "ok"
        assert plans == [[sib]] and baseline_calls == []

    def test_sibling_baseline_unavailable_fails_closed(self, monkeypatch, tmp_path):
        red, *_ = _wire_verify_green(
            monkeypatch, tmp_path, red_fails=[],
            sib_current=frozenset({"tests/test_foo.py::test_a"}), sib_baseline=None,
        )
        events = _events(monkeypatch)
        assert _p5._verify_green_passing(_ctx(tmp_path), _prev([red])).status == "error"
        ev = [e for e in events if e["type"] == "verify_green_sibling_delta_verdict"]
        assert ev and ev[0]["payload"]["baseline_available"] is False

    def test_sibling_current_unavailable_fails_closed(self, monkeypatch, tmp_path):
        red, *_ = _wire_verify_green(
            monkeypatch, tmp_path, red_fails=[], sib_current=None, sib_baseline=frozenset(),
        )
        events = _events(monkeypatch)
        assert _p5._verify_green_passing(_ctx(tmp_path), _prev([red])).status == "error"
        ev = [e for e in events if e["type"] == "verify_green_sibling_delta_verdict"]
        assert ev and ev[0]["payload"]["current_available"] is False

    def test_remaining_red_red_test_is_never_released(self, monkeypatch, tmp_path):
        """Acceptance: GREEN with one red RED test that was already red at the
        RED commit (preexisting by ID) still FAILS the step — recoverably."""
        red, *_ = _wire_verify_green(
            monkeypatch, tmp_path, red_fails=["tests/test_red.py::test_one"],
            sib_current=frozenset(), sib_baseline=frozenset(),
            red_baseline=["tests/test_red.py::test_one"],
        )
        r = _p5._verify_green_passing(_ctx(tmp_path), _prev([red]))
        assert r.status == "error"
        assert r.error_code == "E_GREEN_NOT_PASSING"
        assert r.recoverable is True
        assert r.data["retry_from_step"] == 1

    def test_file_level_red_baseline_keeps_retry_recoverable(self, monkeypatch, tmp_path):
        red, *_ = _wire_verify_green(
            monkeypatch, tmp_path, red_fails=["tests/test_red.py::test_x"],
            sib_current=frozenset(), sib_baseline=frozenset(),
            red_baseline=["tests/test_red.py"],
        )
        r = _p5._verify_green_passing(_ctx(tmp_path), _prev([red]))
        assert r.status == "error"
        assert r.recoverable is True
        assert r.data["retry_from_step"] == 1

    def test_red_group_new_id_is_terminal_net_new(self, monkeypatch, tmp_path):
        red, *_ = _wire_verify_green(
            monkeypatch, tmp_path, red_fails=["tests/test_red.py::test_two"],
            sib_current=frozenset(), sib_baseline=frozenset(),
            red_baseline=["tests/test_red.py::test_one"],
        )
        events = _events(monkeypatch)
        r = _p5._verify_green_passing(_ctx(tmp_path), _prev([red]))
        assert r.status == "error"
        assert r.recoverable is False
        ev = [e for e in events if e["type"] == "verify_green_delta_verdict"]
        assert ev and ev[0]["payload"]["new_ids"] == ["tests/test_red.py::test_two"]

    def test_red_baseline_missing_stays_recoverable(self, monkeypatch, tmp_path):
        red, *_ = _wire_verify_green(
            monkeypatch, tmp_path, red_fails=["tests/test_red.py::test_two"],
            sib_current=frozenset(), sib_baseline=frozenset(),
        )
        events = _events(monkeypatch)
        r = _p5._verify_green_passing(_ctx(tmp_path), _prev([red]))
        assert r.recoverable is True
        ev = [e for e in events if e["type"] == "verify_green_delta_verdict"]
        assert ev and ev[0]["payload"]["classification"] == "baseline_unavailable"

    def test_no_red_sha(self, monkeypatch, tmp_path):
        red, sib, plans, baseline_calls, _ = _wire_verify_green(
            monkeypatch, tmp_path, red_fails=["tests/test_red.py::test_one"],
            sib_current=frozenset(), sib_baseline=frozenset(), red_sha=None,
        )
        r = _p5._verify_green_passing(_ctx(tmp_path), _prev([red], red_sha=None))
        assert r.status == "error" and r.recoverable is True
        assert plans == [] and baseline_calls == []

    def test_ambient_real_caller_sibling_site_never_writes(self, monkeypatch, tmp_path):
        """Real caller + real helper at the sibling site with an ambient
        (relative) git_cwd: guard fires, no worktree, no stash, untracked intact."""
        red, sib, *_ = _wire_verify_green(
            monkeypatch, tmp_path, red_fails=[],
            sib_current=frozenset({"tests/test_foo.py::test_a"}), sib_baseline=frozenset(),
        )
        monkeypatch.setattr(_p5, "_red_commit_baseline_fail_ids", _REAL_BASELINE)
        _git(tmp_path, "init", "-q")
        _git(tmp_path, "config", "user.email", "t@example.com")
        _git(tmp_path, "config", "user.name", "t")
        _git(tmp_path, "add", "-A")
        _git(tmp_path, "commit", "-q", "-m", "base")
        (tmp_path / "untracked.txt").write_text("precious\n")
        wt_before = _git(tmp_path, "worktree", "list")
        monkeypatch.chdir(tmp_path)
        events = _events(monkeypatch)
        ctx = _ctx(tmp_path, git_cwd=".")
        _p5._verify_green_passing(ctx, _prev([red]))
        assert any(e["type"] == "baseline_skipped_ambient_cwd" for e in events)
        assert _git(tmp_path, "worktree", "list") == wt_before
        assert _git(tmp_path, "stash", "list") == ""
        assert (tmp_path / "untracked.txt").read_text() == "precious\n"

    def test_no_git_stash_during_verify_green(self, monkeypatch, tmp_path):
        red, *_ = _wire_verify_green(
            monkeypatch, tmp_path, red_fails=["tests/test_red.py::test_one"],
            sib_current=frozenset(), sib_baseline=frozenset(),
            red_baseline=["tests/test_red.py::test_one"],
        )
        events = _events(monkeypatch)
        cmds: list = []
        monkeypatch.setattr(_p5.git_write_port, "git_op_capture",
                            lambda cmd, cwd, timeout=30: cmds.append(cmd))
        _p5._verify_green_passing(_ctx(tmp_path), _prev([red]))
        assert any(e["type"] == "verify_green_delta_verdict" for e in events)  # reached the RED branch
        assert not any("stash" in c for c in cmds)

    def test_baseline_gate_gets_red_sha_and_cache_dir(self, monkeypatch, tmp_path):
        red, *_ = _wire_verify_green(
            monkeypatch, tmp_path, red_fails=["tests/test_red.py::test_one"],
            sib_current=frozenset(), sib_baseline=frozenset(),
            red_baseline=["tests/test_red.py::test_one"],
        )
        calls: list = []
        monkeypatch.setattr(_p5, "run_baseline_delta_gate",
                            lambda *a, **k: calls.append(k) or {"skipped": "stub"})
        _p5._verify_green_passing(_ctx(tmp_path), _prev([red]))
        assert calls and calls[0]["base_sha"] == "redsha"
        assert calls[0]["cache_dir"] == str((tmp_path / "sp" / "baseline").resolve())


# ─── S6 — _baseline_delta wiring ─────────────────────────────────────────────

class _Cfg:
    def gate_enabled(self, _n):
        return True

    def flag(self, _n):
        return False

    def path(self, _n, default):
        return default


class TestS6BaselineDeltaWiring:
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


# ─── S7 — missing ledger = empty ledger ──────────────────────────────────────

def _run_gate(tmp_path: Path, ledger: Path) -> subprocess.CompletedProcess:
    results = tmp_path / "results.txt"
    results.write_text("FAILED tests/test_x.py::test_new - boom\n1 failed in 0.1s\n")
    baseline = tmp_path / "baseline.txt"
    baseline.write_text("")
    return subprocess.run(
        [sys.executable, str(_GATE_SCRIPT), "--results", str(results), "--suite", "pytest",
         "--baseline", str(baseline), "--ledger", str(ledger)],
        cwd=str(_REPO_ROOT), capture_output=True, text=True,
    )


class TestS7MissingLedger:
    def test_missing_ledger_yields_verdict(self, tmp_path):
        proc = _run_gate(tmp_path, tmp_path / "absent-known-reds.md")
        assert proc.returncode != 2, proc.stderr
        out = json.loads(proc.stdout.strip().splitlines()[-1])
        assert out["ledger_source"] == "missing"
        assert out["delta_verdict"] == "FAIL"
        assert out["new_fails"] == ["tests/test_x.py::test_new"]

    def test_present_ledger_is_file_source(self, tmp_path):
        ledger = tmp_path / "known-reds.md"
        ledger.write_text("# known reds\n")
        proc = _run_gate(tmp_path, ledger)
        out = json.loads(proc.stdout.strip().splitlines()[-1])
        assert out["ledger_source"] == "file"

    def test_unreadable_ledger_stays_driver_error(self, tmp_path):
        ledger = tmp_path / "known-reds.md"
        ledger.mkdir()
        assert _run_gate(tmp_path, ledger).returncode == 2
