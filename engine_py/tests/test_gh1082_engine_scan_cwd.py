"""RED tests — GH1082 v3 Part B: explicit git-scan cwd (§2.3), AC10-AC15, AC24, AC25a-AC25b.

`_git_changes_vs_head` and its two call sites in `WorkflowEngine._execute_steps`
(engine.py:384, :435) today always read the AMBIENT process cwd. v2 pins the
scan to `context.org_config["git_cwd"]` ONLY, via a new module-level helper
`_resolve_scan_cwd(context)` (single argument — no `prev`, no ladder, no
`resolve_git_cwd_with_source` — that resolver emits `emit_resolver_resolved`
on every branch and would inject a new event into the active telemetry run;
see spec §1.4/§2.3 and gate BLOCKER 2 — pinned here by AC24).

§1q / D1CF5FDF: `_resolve_scan_cwd` and the new `cwd` parameter of
`_git_changes_vs_head` do not exist pre-GREEN, so every reference to them is
deferred INSIDE the test body (not at module import time) to fail at
assert-time, never collect-time. `engine` itself already exists and imports
cleanly, so it is imported at module level like the sibling `test_engine_W9.py`.

conftest.py already inserts the engine_py root onto sys.path at import time
(the canonical §1q seam) — no module-level sys.path manipulation here.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import engine
from contracts import StepContract, StepResult, WorkflowContext, WorkflowDefinition
from engine import WorkflowEngine
from lib.git_port import (
    GitResult,
    reset_default_git_read_factory,
    set_default_git_read_factory,
)


class _FakeEventLog:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict, str]] = []

    def append(self, event_type: str, payload: dict, run_id: str | None = None) -> None:
        self.events.append((event_type, payload, run_id or "ad-hoc"))


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def _init_git_repo(path: Path, seed_name: str) -> None:
    path.mkdir(parents=True, exist_ok=True)
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "t@t")
    _git(path, "config", "user.name", "t")
    (path / seed_name).write_text("seed\n")
    _git(path, "add", seed_name)
    _git(path, "commit", "-q", "-m", "init")


def _make_step_that_calls(name: str, fn) -> StepContract:
    def _run(_ctx, _prev):
        fn()
        return StepResult(status="ok", data=None, duration_ms=0, step_name=name)
    return StepContract(name=name, execute=_run)


def _make_ctx(org_config: dict | None) -> WorkflowContext:
    return WorkflowContext(
        tenant_id="t", scope=None, db_path=None, org_config=org_config,
        question="q", session_id="s", persona="p", framework=None, domain=None,
    )


# ─── AC10 ─────────────────────────────────────────────────────────────────────


def test_ac10_git_changes_vs_head_forwards_cwd_to_both_calls():
    """AC10: _git_changes_vs_head("/x") forwards cwd="/x" into BOTH git_read calls."""
    calls: list[dict] = []

    class _Spy:
        def __call__(self, args, *, cwd=None, timeout=None, dir_=None):
            calls.append({"args": args, "cwd": cwd})
            return GitResult(returncode=0, stdout="", stderr="", timed_out=False)

    spy = _Spy()
    try:
        set_default_git_read_factory(lambda: spy)
        engine._git_changes_vs_head("/x")
    finally:
        reset_default_git_read_factory()

    assert len(calls) == 2, f"expected 2 git_read calls, got {len(calls)}: {calls!r}"
    assert all(c["cwd"] == "/x" for c in calls), (
        f"expected cwd='/x' forwarded to both git_read calls; got {calls!r}"
    )


# ─── AC11 ─────────────────────────────────────────────────────────────────────


def test_ac11_resolve_scan_cwd_exists_and_returns_str_for_explicit_tier():
    """AC11: _resolve_scan_cwd(context) — single argument, no `prev` — exists
    as a module-level symbol and returns a str for org_config={"git_cwd": T}."""
    from engine import _resolve_scan_cwd  # deferred: doesn't exist pre-GREEN

    ctx = _make_ctx(org_config={"git_cwd": "/some/tree"})
    result = _resolve_scan_cwd(ctx)
    assert isinstance(result, str), f"expected str, got {result!r}"
    assert result == "/some/tree", f"expected '/some/tree', got {result!r}"


# ─── AC12 ─────────────────────────────────────────────────────────────────────


def test_ac12_resolve_scan_cwd_returns_none_when_no_explicit_tree():
    """AC12: _resolve_scan_cwd(context) returns None when org_config is None,
    empty, or lacks "git_cwd" — no "source label" concept in v2; single
    source is cfg["git_cwd"] and nothing else."""
    from engine import _resolve_scan_cwd  # deferred: doesn't exist pre-GREEN

    assert _resolve_scan_cwd(_make_ctx(org_config=None)) is None
    assert _resolve_scan_cwd(_make_ctx(org_config={})) is None
    assert _resolve_scan_cwd(_make_ctx(org_config={"other_key": "x"})) is None


# ─── AC13 — production side-effect anchor ────────────────────────────────────


def test_ac13_files_touched_reports_explicit_tier_not_ambient_cwd_repo(tmp_path, monkeypatch):
    """AC13 (§1l anchor): a real workflow run with org_config={"git_cwd": T}
    reports T's dirty file in files_touched — and NOT the differently-named
    dirty file in the process's ambient-cwd repo. Exercises the real emit at
    engine.py:435-440 through WorkflowEngine.execute; does not monkeypatch
    _git_changes_vs_head. The two repos' dirty files carry distinct
    filenames so the positive/negative assertions cannot coincide."""
    repo_a = tmp_path / "repo_a"
    repo_b = tmp_path / "repo_b"
    _init_git_repo(repo_a, "alpha_seed.txt")
    _init_git_repo(repo_b, "bravo_seed.txt")

    monkeypatch.chdir(repo_b)  # ambient process cwd points at repo_b, NOT repo_a

    log = _FakeEventLog()
    eng = WorkflowEngine(event_log=log)

    def _dirty_both():
        (repo_a / "alpha_dirty.txt").write_text("changed-a\n")
        (repo_b / "bravo_dirty.txt").write_text("changed-b\n")

    eng.register("wf", WorkflowDefinition(
        name="wf", steps=[_make_step_that_calls("s1", _dirty_both)],
    ))
    ctx = _make_ctx(org_config={"git_cwd": str(repo_a)})
    eng.execute("wf", ctx, run_id="r1")

    touched = [e for e in log.events if e[0] == "files_touched"]
    assert len(touched) == 1, f"expected exactly 1 files_touched event, got {touched!r}"
    payload = touched[0][1]
    assert "alpha_dirty.txt" in payload["paths"], (
        f"expected repo_a's dirty file 'alpha_dirty.txt' in payload paths; got {payload!r}"
    )
    assert "bravo_dirty.txt" not in payload["paths"], (
        f"payload must NOT name the ambient-cwd repo's dirty file 'bravo_dirty.txt'; got {payload!r}"
    )


# ─── AC14 ─────────────────────────────────────────────────────────────────────


def test_ac14_no_explicit_tier_suppresses_scan_even_if_ambient_cwd_dirty(tmp_path, monkeypatch):
    """AC14: with org_config=None (no explicit tier), a real workflow run
    emits NO files_touched event even when the process cwd is a dirty git repo."""
    repo = tmp_path / "repo"
    _init_git_repo(repo, "seed.txt")
    monkeypatch.chdir(repo)

    log = _FakeEventLog()
    eng = WorkflowEngine(event_log=log)

    def _dirty():
        (repo / "seed.txt").write_text("changed\n")

    eng.register("wf", WorkflowDefinition(
        name="wf", steps=[_make_step_that_calls("s1", _dirty)],
    ))
    ctx = _make_ctx(org_config=None)
    eng.execute("wf", ctx, run_id="r1")

    touched = [e for e in log.events if e[0] == "files_touched"]
    assert touched == [], (
        f"expected no files_touched event with no explicit tier; got {touched!r}"
    )


# ─── AC15 — regression, must stay PASS pre- and post-GREEN ───────────────────


def test_ac15_zero_arg_regression_still_parses_status_letters():
    """AC15 regression: zero-arg _git_changes_vs_head() still parses
    R/C/A/M status letters as today (pins the 17 existing zero-arg call sites)."""

    class _Spy:
        def __init__(self, diff_stdout: str, others_stdout: str = "") -> None:
            self.diff_stdout = diff_stdout
            self.others_stdout = others_stdout

        def __call__(self, args, *, cwd=None, timeout=None, dir_=None):
            if "ls-files" in args:
                return GitResult(returncode=0, stdout=self.others_stdout, stderr="", timed_out=False)
            return GitResult(returncode=0, stdout=self.diff_stdout, stderr="", timed_out=False)

    spy = _Spy(
        diff_stdout=(
            "R100\told.py\tnew.py\n"
            "C90\ta.py\tb.py\n"
            "M\tmod.py\n"
            "A\tadded.py\n"
        ),
        others_stdout="untracked.py\n",
    )
    try:
        set_default_git_read_factory(lambda: spy)
        result = engine._git_changes_vs_head()
    finally:
        reset_default_git_read_factory()

    assert result is not None
    assert result.get("new.py") == "R", result
    assert result.get("b.py") == "C", result
    assert result.get("mod.py") == "M", result
    assert result.get("added.py") == "A", result
    assert result.get("untracked.py") == "A", result


# ─── AC24 — BLOCKER regression pin: no new event type ────────────────────────


def test_ac24_no_resolver_git_cwd_resolved_event_leaks_into_log(tmp_path, monkeypatch):
    """AC24 (gate BLOCKER 2 regression pin): a 2-step workflow with
    org_config={"git_cwd": T} supplied must emit ZERO
    resolver_git_cwd_resolved events. _resolve_scan_cwd must read ONLY
    cfg["git_cwd"] and must NOT call lib.git_cwd.resolve_git_cwd_with_source
    (which emits on every branch via emit_resolver_resolved ->
    telemetry_ctx.emit_safe) — or the exact-sequence assert in
    tests/test_event_log_replay_e2e.py:59-67 breaks. This must FAIL if GREEN
    wires the emitting resolver instead of the frozen v2 design."""
    monkeypatch.chdir(tmp_path)  # hermeticity parity: no ambient reads against the live HAL worktree
    repo = tmp_path / "repo"
    _init_git_repo(repo, "seed.txt")

    log = _FakeEventLog()
    eng = WorkflowEngine(event_log=log)

    def _step1() -> None:
        return None

    def _step2() -> None:
        (repo / "seed.txt").write_text("changed\n")

    eng.register("wf", WorkflowDefinition(
        name="wf", steps=[
            _make_step_that_calls("s1", _step1),
            _make_step_that_calls("s2", _step2),
        ],
    ))
    ctx = _make_ctx(org_config={"git_cwd": str(repo)})
    eng.execute("wf", ctx, run_id="r1")

    resolver_events = [e for e in log.events if e[0] == "resolver_git_cwd_resolved"]
    assert resolver_events == [], (
        f"expected zero resolver_git_cwd_resolved events; got {resolver_events!r}"
    )

    # v3 MINOR fix: the event-log assertion alone is weak — emit_resolver.py:23
    # no-ops with no active run context, and v3 hoists _resolve_scan_cwd ABOVE
    # engine.py:373's set_current_run, so a hoisted emitting-resolver call
    # would emit nothing and the assertion above would still pass. Guard
    # directly against the emitting resolver via source-slice inspection of
    # _resolve_scan_cwd's own body (no spec_from_file_location/exec_module).
    # Skipped (vacuously true) pre-GREEN when the symbol does not exist yet —
    # AC24 is a PASS-today guard, both halves must stay PASS post-GREEN.
    resolve_scan_cwd = getattr(engine, "_resolve_scan_cwd", None)
    if resolve_scan_cwd is not None:
        import inspect

        fn_src = inspect.getsource(resolve_scan_cwd)
        module_src = inspect.getsource(engine)

        assert "resolve_git_cwd_with_source" not in fn_src, (
            f"_resolve_scan_cwd must not call resolve_git_cwd_with_source; source:\n{fn_src}"
        )
        assert "resolve_git_cwd(" not in fn_src, (
            f"_resolve_scan_cwd must not call resolve_git_cwd; source:\n{fn_src}"
        )
        forbidden_imports = [
            line for line in module_src.splitlines()
            if "import" in line and (
                "lib.git_cwd" in line or "lib import git_cwd" in line
            )
        ]
        assert not forbidden_imports, (
            f"engine.py must not import lib.git_cwd's emitting resolver; found: {forbidden_imports!r}"
        )


# ─── AC25a/AC25b — retry/tier stability across same-cycle retry re-entry ─────


def test_ac25a_prev_tier_excluded_no_files_touched_when_org_config_lacks_git_cwd(tmp_path, monkeypatch):
    """AC25a (the real MAJOR-3 pin): org_config LACKS git_cwd, while the
    retry/prev step data CARRIES a git_cwd pointing at repoA (also dirty). A
    prev-participating implementation would emit files_touched by picking up
    the prev-carried tree; the frozen v3 design reads ONLY cfg["git_cwd"], so
    no event fires on either attempt.

    Not vacuous (previous round's gate finding): the ambient process cwd is
    repoB, a REAL git repo that is ALSO made dirty during each attempt. Today
    production scans the ambient cwd unconditionally, so the buggy
    implementation DOES find something to emit here (repoB's dirty file) —
    only the correct implementation (org_config has no git_cwd -> skip
    entirely; prev never consulted) emits nothing. FAILS pre-GREEN."""
    repo_a = tmp_path / "repo_a"  # dirty repo carried via prev/retry data only
    repo_b = tmp_path / "repo_b"  # ambient process cwd — also dirty
    _init_git_repo(repo_a, "alpha_seed.txt")
    _init_git_repo(repo_b, "bravo_seed.txt")
    monkeypatch.chdir(repo_b)  # ambient cwd = repo_b (dirty)

    log = _FakeEventLog()
    eng = WorkflowEngine(event_log=log)

    attempt = {"n": 0}

    def _retry_step(_ctx, _prev):
        attempt["n"] += 1
        if attempt["n"] == 1:
            (repo_a / "alpha_dirty_1.txt").write_text("changed-attempt-1\n")
            (repo_b / "bravo_dirty_1.txt").write_text("changed-attempt-1\n")
            return StepResult(
                status="error",
                data={
                    "retry_from_step": 0,
                    "cycle_count": 1,
                    "git_cwd": str(repo_a),  # prev-carried tree; must NOT be adopted
                },
                duration_ms=0,
                step_name="s0",
                recoverable=True,
            )
        (repo_a / "alpha_dirty_2.txt").write_text("changed-attempt-2\n")
        (repo_b / "bravo_dirty_2.txt").write_text("changed-attempt-2\n")
        return StepResult(status="ok", data={"verdict": "PASS"}, duration_ms=0, step_name="s0")

    eng.register("wf", WorkflowDefinition(
        name="wf", steps=[StepContract(name="s0", execute=_retry_step)],
    ))
    ctx = _make_ctx(org_config={})  # explicitly lacks git_cwd
    eng.execute("wf", ctx, run_id="r1")

    touched = [e for e in log.events if e[0] == "files_touched"]
    assert touched == [], (
        f"expected zero files_touched events across both attempts (org_config "
        f"lacks git_cwd; prev must not be consulted; ambient-cwd repoB must "
        f"not be scanned either); got {touched!r}"
    )


def test_ac25b_scan_root_stable_across_retry_with_fresh_distinct_filenames(tmp_path, monkeypatch):
    """AC25b: org_config pins repoA. Each retry attempt dirties a NEWLY
    NAMED file in both repoA and the decoy repo so the post-retry delta is
    non-empty and discriminating (mirrors, but is not confused by, a
    same-path rewrite). The post-retry files_touched payload names repoA's
    second file and NOT the decoy repo's second file — driving the real
    same-cycle retry re-entry at engine.py:638."""
    repo_a = tmp_path / "repo_a"          # the org_config-pinned tree (correct)
    repo_other = tmp_path / "repo_other"  # decoy tree carried in retry data
    _init_git_repo(repo_a, "alpha_seed.txt")
    _init_git_repo(repo_other, "other_seed.txt")
    monkeypatch.chdir(tmp_path)  # ambient cwd is neither repo (belt-and-braces)

    log = _FakeEventLog()
    eng = WorkflowEngine(event_log=log)

    attempt = {"n": 0}

    def _retry_step(_ctx, _prev):
        attempt["n"] += 1
        if attempt["n"] == 1:
            (repo_a / "alpha_1.txt").write_text("changed-attempt-1\n")
            (repo_other / "other_1.txt").write_text("changed-attempt-1\n")
            return StepResult(
                status="error",
                data={
                    "retry_from_step": 0,
                    "cycle_count": 1,
                    "git_cwd": str(repo_other),  # decoy: must NOT be adopted
                },
                duration_ms=0,
                step_name="s0",
                recoverable=True,
            )
        (repo_a / "alpha_2.txt").write_text("changed-attempt-2\n")
        (repo_other / "other_2.txt").write_text("changed-attempt-2\n")
        return StepResult(status="ok", data={"verdict": "PASS"}, duration_ms=0, step_name="s0")

    eng.register("wf", WorkflowDefinition(
        name="wf", steps=[StepContract(name="s0", execute=_retry_step)],
    ))
    ctx = _make_ctx(org_config={"git_cwd": str(repo_a)})
    eng.execute("wf", ctx, run_id="r1")

    touched = [e for e in log.events if e[0] == "files_touched"]
    assert touched, f"expected at least one files_touched event across the retry; log={log.events!r}"
    last_payload = touched[-1][1]
    assert "alpha_2.txt" in last_payload["paths"], (
        f"scan root must stay anchored to repo_a on the retried attempt; got {last_payload!r}"
    )
    assert "other_2.txt" not in last_payload["paths"], (
        f"scan root must not adopt the decoy git_cwd carried in retry data; got {last_payload!r}"
    )
