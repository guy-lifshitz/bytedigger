"""GH1179 RED tests — deterministic post-RED write-boundary gate (spec v2).

Spec (frozen v2, post-gate-REJECT): SHARED/memory/Decisions/2026-07-25_6B28230E_gh1179_red_write_boundary_spec.md
Agreement: 6B28230E-E6B4-4308-A0C3-AF66795FC076

Covers AC1-AC24 from spec §4:
  UUT-A = lib/red_write_boundary.py         (AC1-AC10)
  UUT-B = workflows/phase_5_implement.py    (AC11-AC22, new _enforce_red_write_boundary)
  registries                                (AC23 error_codes + flags_catalog)
  consumer classification                   (AC24 error_locus / dispatcher_report)

Per §1q/D1CF5FDF: `lib/red_write_boundary.py` does not exist yet, and
`phase_5_implement._enforce_red_write_boundary` does not exist on the module
yet either. Every reference to either is deferred INTO the test bodies (via
the `_mod()` helper / `getattr(p5_mod, ...)`) so this file still imports
cleanly at pytest COLLECTION time — each test instead FAILS at ASSERT time.
A module-level `import red_write_boundary` would fail at collection and hang
red_runtime (the exact failure mode D1CF5FDF documents). `phase_5_implement`
and `contracts` ARE imported at module level below because both already
exist in production.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
import uuid
from pathlib import Path

# Conftest singleton (§1q, tests/conftest.py:24-37) already put engine_py
# root, workflows/ and lib/ on sys.path — no module-level sys.path edits here.
import phase_5_implement as p5_mod
from contracts import StepResult, WorkflowContext


def _mod():
    """Deferred import of the not-yet-existing lib module (§1q / D1CF5FDF)."""
    import red_write_boundary  # noqa: PLC0415

    return red_write_boundary


# ─── shared git-repo helpers (mirrors test_gh1086_worktree_sync_race idiom) ───


def _make_repo(base: Path) -> Path:
    """Create a minimal git repo under base with one initial commit.

    A `tests/.gitkeep` is committed too so the `tests/` directory itself is
    TRACKED — otherwise `git status --porcelain` (default showUntrackedFiles)
    collapses a wholly-new subdirectory into a single `?? tests/` line
    instead of listing individual new files, which would make AC3's exact
    per-file assertions non-deterministic. This mirrors every real HAL/ppba
    checkout, which always has a pre-existing tracked `tests/` directory.

    os.path.realpath avoids /var/folders symlink cwd mismatches (§1j).
    """
    repo = Path(os.path.realpath(str(base / "repo")))
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "tester"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)
    placeholder = repo / "src" / "placeholder.py"
    placeholder.parent.mkdir(parents=True, exist_ok=True)
    unique = uuid.uuid4().hex
    placeholder.write_text(f"# placeholder {repo} {unique}\n")
    tests_keep = repo / "tests" / ".gitkeep"
    tests_keep.parent.mkdir(parents=True, exist_ok=True)
    tests_keep.write_text("")
    subprocess.run(["git", "add", "src/placeholder.py", "tests/.gitkeep"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", f"init {repo} {unique}"], cwd=repo, check=True)
    return repo


def _add_worktree(main_repo: Path, dest: Path, branch: str) -> Path:
    """Create a REAL linked worktree via `git worktree add` (§1l — no mocking
    of the git layer for the end-to-end AC17-AC20 tests)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    wt = Path(os.path.realpath(str(dest.parent))) / dest.name
    subprocess.run(
        ["git", "worktree", "add", "-b", branch, str(wt)], cwd=main_repo, check=True
    )
    return wt


def _make_scratchpad() -> Path:
    return Path(os.path.realpath(tempfile.mkdtemp()))


def _make_ctx(scratchpad: Path, *, current_worktree_path: str | None = None) -> WorkflowContext:
    org: dict = {"scratchpad_dir": str(scratchpad)}
    if current_worktree_path is not None:
        org["current_worktree_path"] = current_worktree_path
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question="gh1179 red write boundary",
        session_id="test-session",
        persona="hal",
        framework=None,
        domain=None,
    )


def _prev_for_invoke(scratchpad: Path) -> StepResult:
    return StepResult(
        status="ok",
        data={
            "prompt": "noop-prompt",
            "log_path": str(scratchpad / "tests/build-red-output.log"),
            "spec_path": str(scratchpad / "specs/build-spec.md"),
            "cycle": 1,
            "stable_prefix": "",
        },
        duration_ms=0,
        step_name="build_red_prompt",
    )


def _capture_emit(events: list) -> "callable":
    """Collaborator-level telemetry spy (not the UUT): captures whatever
    positional/keyword call shape _emit_safe is invoked with, without
    assuming a fixed signature at the call site."""

    def _fn(*args, **kwargs):
        events.append((args, kwargs))

    return _fn


def _event_payload(args: tuple, kwargs: dict) -> dict:
    return args[1] if len(args) > 1 else kwargs.get("payload", {})


def _event_type(args: tuple, kwargs: dict) -> str | None:
    return args[0] if args else kwargs.get("event_type")


# ─── AC1: resolve_main_repo_root — str|Path, cwd, timeout=10 ─────────────────


def test_ac01_resolve_main_repo_root_uses_git_worktree_list_str_or_path(tmp_path, monkeypatch):
    """AC1: resolve_main_repo_root(root) runs `git worktree list --porcelain`
    via git_port.git_read with cwd=str(root) and timeout=10, returning the
    first `worktree ` entry as a resolved Path. Accepts root as str or Path
    (§1.6-1).
    """
    m = _mod()
    from lib import git_port  # noqa: PLC0415

    repo = _make_repo(tmp_path)

    calls: list[dict] = []
    real_git_read = git_port.git_read

    def _spy(args, *, cwd=None, timeout=None, dir_=None):
        calls.append({"args": list(args), "cwd": cwd, "timeout": timeout})
        return real_git_read(args, cwd=cwd, timeout=timeout, dir_=dir_)

    monkeypatch.setattr(git_port, "git_read", _spy)

    result_from_str = m.resolve_main_repo_root(str(repo))
    result_from_path = m.resolve_main_repo_root(repo)

    assert result_from_str == repo.resolve(), f"str input: expected {repo.resolve()!r}, got {result_from_str!r}"
    assert result_from_path == repo.resolve(), f"Path input: expected {repo.resolve()!r}, got {result_from_path!r}"
    assert any(
        c["args"][:2] == ["worktree", "list"] and c["cwd"] == str(repo) and c["timeout"] == 10
        for c in calls
    ), f"expected a call args=['worktree','list',...], cwd={str(repo)!r}, timeout=10; calls={calls!r}"


# ─── AC2: nesting — armed for a worktree nested under main (closes M5) ────────


def test_ac02_nested_worktree_stays_armed(tmp_path):
    """AC2: snapshot_boundary_state returns available=False ONLY when
    worktree_root resolves to the main repo root itself. For a worktree
    NESTED under the main root it returns available=True (§1.3 — the gate
    must stay armed for HAL self-builds).
    """
    m = _mod()
    main = _make_repo(tmp_path / "main")
    nested_wt = _add_worktree(main, main / ".worktrees" / "wt", "feat2")

    same_root = m.snapshot_boundary_state(main)
    assert same_root["available"] is False, (
        f"single-tree build (worktree_root == main) must be available=False, got {same_root!r}"
    )

    nested = m.snapshot_boundary_state(nested_wt)
    assert nested["available"] is True, (
        f"nested worktree (HAL's own layout) must stay ARMED (available=True), got {nested!r}"
    )


# ─── AC3: linked worktree snapshot — both trees, subtree exclusion ───────────


def test_ac03_snapshot_excludes_worktree_subtree_from_main_paths(tmp_path):
    """AC3: For a linked worktree, available=True, main_repo_root ==
    str(main.resolve()), and main_paths is the set of paths (not raw status
    lines) from `git -C <main> status --porcelain`, including `??`
    untracked entries; worktree_paths is the same for the worktree. Any
    MAIN path under the worktree's own subtree is excluded from
    main_paths (§1.3).
    """
    m = _mod()
    main = _make_repo(tmp_path / "main")
    nested_wt = _add_worktree(main, main / ".worktrees" / "wt", "feat3")

    stray = main / "tests" / "test_stray.py"
    stray.write_text("def test_x(): pass\n")

    inside = nested_wt / "tests" / "test_inside.py"
    inside.write_text("def test_y(): pass\n")

    result = m.snapshot_boundary_state(nested_wt)

    assert result["available"] is True
    assert result["main_repo_root"] == str(main.resolve())
    assert "tests/test_stray.py" in result["main_paths"], (
        f"untracked (??) MAIN path missing from main_paths: {result['main_paths']!r}"
    )
    assert not any(p.startswith(".worktrees") for p in result["main_paths"]), (
        f"a MAIN path under the worktree's own subtree must be EXCLUDED from main_paths: "
        f"{result['main_paths']!r}"
    )
    assert "tests/test_inside.py" in result["worktree_paths"], (
        f"worktree-side untracked path missing from worktree_paths: {result['worktree_paths']!r}"
    )


# ─── AC4: both git calls independently guarded — never raises ────────────────


def test_ac04_snapshot_unavailable_and_never_raises_on_either_git_call_failing(tmp_path, monkeypatch):
    """AC4: snapshot_boundary_state returns available=False and NEVER
    raises when EITHER git call fails: rc != 0, rc == 124,
    FileNotFoundError, OSError — each call independently guarded (§1.6-2).
    """
    m = _mod()
    from lib import git_port  # noqa: PLC0415

    main = _make_repo(tmp_path / "main")
    wt = _add_worktree(main, tmp_path / "wt", "feat4")
    real_git_read = git_port.git_read

    class _FakeResult:
        def __init__(self, returncode: int):
            self.returncode = returncode
            self.stdout = ""
            self.stderr = "boom"
            self.timed_out = returncode == 124

    # (a) the `worktree list` call itself fails.
    for rc in (1, 124):
        def _fail_worktree_list(args, *, cwd=None, timeout=None, dir_=None, _rc=rc):
            return _FakeResult(_rc)

        monkeypatch.setattr(git_port, "git_read", _fail_worktree_list)
        result = m.snapshot_boundary_state(wt)
        assert result["available"] is False, f"worktree-list rc={rc}: expected available=False, got {result!r}"

    def _raise_worktree_list(args, *, cwd=None, timeout=None, dir_=None):
        raise FileNotFoundError("git binary not found")

    monkeypatch.setattr(git_port, "git_read", _raise_worktree_list)
    result = m.snapshot_boundary_state(wt)  # must NOT raise
    assert result["available"] is False, f"expected available=False after FileNotFoundError, got {result!r}"

    # (b) `worktree list` succeeds, a `status --porcelain` call fails.
    for rc in (1, 124):
        def _fail_status_only(args, *, cwd=None, timeout=None, dir_=None, _rc=rc):
            if args[:2] == ["worktree", "list"]:
                return real_git_read(args, cwd=cwd, timeout=timeout, dir_=dir_)
            return _FakeResult(_rc)

        monkeypatch.setattr(git_port, "git_read", _fail_status_only)
        result = m.snapshot_boundary_state(wt)
        assert result["available"] is False, f"status rc={rc}: expected available=False, got {result!r}"

    def _raise_status_only(args, *, cwd=None, timeout=None, dir_=None):
        if args[:2] == ["worktree", "list"]:
            return real_git_read(args, cwd=cwd, timeout=timeout, dir_=dir_)
        raise OSError("status failed")

    monkeypatch.setattr(git_port, "git_read", _raise_status_only)
    result = m.snapshot_boundary_state(wt)  # must NOT raise
    assert result["available"] is False, f"expected available=False after status OSError, got {result!r}"


# ─── AC5: classify — unavailable cases ─────────────────────────────────────────


def test_ac05_classify_unavailable_cases():
    """AC5: classify_boundary_delta returns verdict == "unavailable" when
    either snapshot has available=False, or the two main_repo_root values
    differ.
    """
    m = _mod()
    unavailable = {
        "available": False, "main_repo_root": "/main", "worktree_root": "/wt",
        "main_paths": frozenset(), "worktree_paths": frozenset(),
    }
    ok_a = {
        "available": True, "main_repo_root": "/main", "worktree_root": "/wt",
        "main_paths": frozenset(), "worktree_paths": frozenset(),
    }
    ok_b_diff_root = {
        "available": True, "main_repo_root": "/other", "worktree_root": "/wt",
        "main_paths": frozenset({"tests/test_new.py"}), "worktree_paths": frozenset(),
    }

    assert m.classify_boundary_delta(unavailable, ok_a)["verdict"] == "unavailable"
    assert m.classify_boundary_delta(ok_a, unavailable)["verdict"] == "unavailable"
    assert m.classify_boundary_delta(ok_a, ok_b_diff_root)["verdict"] == "unavailable"


# ─── AC6: classify — violation, sorted stray_paths, test-shaped only ─────────


def test_ac06_classify_violation_when_only_main_gains_test_shaped():
    """AC6: verdict == "violation" with sorted stray_paths == exactly the
    paths new in after["main_paths"] and test-shaped, when the worktree
    gained no test-shaped path.
    """
    m = _mod()
    before = {
        "available": True, "main_repo_root": "/main", "worktree_root": "/wt",
        "main_paths": frozenset(), "worktree_paths": frozenset(),
    }
    after = {
        "available": True, "main_repo_root": "/main", "worktree_root": "/wt",
        "main_paths": frozenset({"tests/test_z.py", "tests/test_a.py", "src/leftover.py"}),
        "worktree_paths": frozenset(),
    }

    result = m.classify_boundary_delta(before, after)

    assert result["verdict"] == "violation", f"expected violation, got {result!r}"
    assert result["stray_paths"] == ["tests/test_a.py", "tests/test_z.py"], f"{result!r}"


# ─── AC7: is_test_shaped predicate ─────────────────────────────────────────────


def test_ac07_is_test_shaped_classifies_paths():
    """AC7: is_test_shaped returns real True for a tests/ or __tests__/
    segment, or a basename matching test_*.py / *_test.py / *.test.* /
    *_test.sh; real False for src/main.py, README.md, notes/testing.md
    (§1.6-5, asserted with `is`).
    """
    m = _mod()

    positives = [
        "tests/foo.py",
        "pkg/__tests__/bar.ts",
        "test_stray.py",
        "some/dir/frobnicate_test.py",
        "SYSTEM/cli/build/foo.test.ts",
        "scripts/run_test.sh",
    ]
    for p in positives:
        assert m.is_test_shaped(p) is True, f"expected test-shaped (is True): {p!r}"

    negatives = ["src/main.py", "README.md", "notes/testing.md"]
    for p in negatives:
        assert m.is_test_shaped(p) is False, f"expected NOT test-shaped (is False): {p!r}"


# ─── AC8: pre-existing / disappeared paths cancel out ─────────────────────────


def test_ac08_preexisting_and_disappeared_paths_never_reported():
    """AC8: Paths already present in before, and paths that disappeared, are
    never reported — pre-existing MAIN dirt from other sessions cancels out.
    """
    m = _mod()
    before = {
        "available": True, "main_repo_root": "/main", "worktree_root": "/wt",
        "main_paths": frozenset({"tests/test_preexisting.py", "tests/test_gone.py"}),
        "worktree_paths": frozenset(),
    }
    after = {
        "available": True, "main_repo_root": "/main", "worktree_root": "/wt",
        "main_paths": frozenset({"tests/test_preexisting.py", "tests/test_new.py"}),
        "worktree_paths": frozenset(),
    }

    result = m.classify_boundary_delta(before, after)

    assert result["verdict"] == "violation"
    assert result["stray_paths"] == ["tests/test_new.py"], (
        f"pre-existing (test_preexisting.py) and disappeared (test_gone.py) paths "
        f"must not be reported; got {result['stray_paths']!r}"
    )


# ─── AC9: ambiguous — both trees gain test-shaped paths (closes M4) ──────────


def test_ac09_classify_ambiguous_when_both_trees_gain_test_shaped():
    """AC9: (closes M4) When MAIN gained a test-shaped path AND the
    worktree also gained one, verdict == "ambiguous"; stray_paths and
    worktree_new_paths are both populated and the verdict is NOT
    "violation".
    """
    m = _mod()
    before = {
        "available": True, "main_repo_root": "/main", "worktree_root": "/wt",
        "main_paths": frozenset(), "worktree_paths": frozenset(),
    }
    after = {
        "available": True, "main_repo_root": "/main", "worktree_root": "/wt",
        "main_paths": frozenset({"tests/test_concurrent.py"}),
        "worktree_paths": frozenset({"tests/test_inside.py"}),
    }

    result = m.classify_boundary_delta(before, after)

    assert result["verdict"] == "ambiguous", f"expected ambiguous, got {result!r}"
    assert result["verdict"] != "violation"
    assert result["stray_paths"] == ["tests/test_concurrent.py"], f"{result!r}"
    assert result["worktree_new_paths"] == ["tests/test_inside.py"], f"{result!r}"


# ─── AC10: clean — neither tree gains a test-shaped path ─────────────────────


def test_ac10_classify_clean_when_neither_tree_gains():
    """AC10: Neither tree gained a test-shaped path -> verdict == "clean",
    stray_paths == [].
    """
    m = _mod()
    before = {
        "available": True, "main_repo_root": "/main", "worktree_root": "/wt",
        "main_paths": frozenset({"tests/test_a.py"}), "worktree_paths": frozenset({"tests/test_b.py"}),
    }
    after = {
        "available": True, "main_repo_root": "/main", "worktree_root": "/wt",
        "main_paths": frozenset({"tests/test_a.py"}), "worktree_paths": frozenset({"tests/test_b.py"}),
    }

    result = m.classify_boundary_delta(before, after)

    assert result["verdict"] == "clean", f"expected clean, got {result!r}"
    assert result["stray_paths"] == []


# ─── AC11: pass-through when result.status != "ok" ────────────────────────────


def test_ac11_enforce_noop_when_result_not_ok(monkeypatch):
    """AC11: _enforce_red_write_boundary(result, before, after) returns the
    SAME object and emits NO telemetry when result.status != "ok".
    """
    fn = getattr(p5_mod, "_enforce_red_write_boundary", None)
    assert fn is not None, (
        "_enforce_red_write_boundary not found on phase_5_implement — GH1179 helper missing"
    )

    events: list = []
    monkeypatch.setattr(p5_mod, "_emit_safe", _capture_emit(events))

    result = StepResult(
        status="error", data=None, duration_ms=0, step_name="invoke_red_llm",
        error="boom", error_code="E_LLM_CMD_MISSING",
    )
    before = {
        "available": True, "main_repo_root": "/main", "worktree_root": "/wt",
        "main_paths": frozenset(), "worktree_paths": frozenset(),
    }
    after = {
        "available": True, "main_repo_root": "/main", "worktree_root": "/wt",
        "main_paths": frozenset({"tests/test_new.py"}), "worktree_paths": frozenset(),
    }

    out = fn(result, before, after)

    assert out is result, "expected the SAME result object returned unchanged when status != 'ok'"
    assert events == [], f"expected no telemetry emitted when short-circuiting on non-ok status, got {events!r}"


# ─── AC12: kill-switch =0 disables enforcement + emits skip event ────────────


def test_ac12_gate_kill_switch_skips_enforcement(monkeypatch):
    """AC12: HAL_RED_WRITE_BOUNDARY_GATE=0 -> same object returned even for
    a violating pair, plus a red_write_boundary_skipped event.
    """
    fn = getattr(p5_mod, "_enforce_red_write_boundary", None)
    assert fn is not None, (
        "_enforce_red_write_boundary not found on phase_5_implement — GH1179 helper missing"
    )

    monkeypatch.setenv("HAL_RED_WRITE_BOUNDARY_GATE", "0")
    events: list = []
    monkeypatch.setattr(p5_mod, "_emit_safe", _capture_emit(events))

    result = StepResult(status="ok", data={}, duration_ms=0, step_name="invoke_red_llm")
    before = {
        "available": True, "main_repo_root": "/main", "worktree_root": "/wt",
        "main_paths": frozenset(), "worktree_paths": frozenset(),
    }
    after = {
        "available": True, "main_repo_root": "/main", "worktree_root": "/wt",
        "main_paths": frozenset({"tests/test_new.py"}), "worktree_paths": frozenset(),
    }

    out = fn(result, before, after)

    assert out is result, "gate disabled: result must be returned unchanged despite the violation"
    assert any(_event_type(a, k) == "red_write_boundary_skipped" for a, k in events), (
        f"expected red_write_boundary_skipped event when gate disabled, got {events!r}"
    )


# ─── AC13: clean -> checked event with stray_n=0 ─────────────────────────────


def test_ac13_clean_emits_checked_event_with_zero_stray(monkeypatch):
    """AC13: clean -> same object returned, red_write_boundary_checked
    event carries stray_n == 0.
    """
    fn = getattr(p5_mod, "_enforce_red_write_boundary", None)
    assert fn is not None, (
        "_enforce_red_write_boundary not found on phase_5_implement — GH1179 helper missing"
    )

    events: list = []
    monkeypatch.setattr(p5_mod, "_emit_safe", _capture_emit(events))

    result = StepResult(status="ok", data={}, duration_ms=0, step_name="invoke_red_llm")
    before = {
        "available": True, "main_repo_root": "/main", "worktree_root": "/wt",
        "main_paths": frozenset(), "worktree_paths": frozenset(),
    }
    after = {
        "available": True, "main_repo_root": "/main", "worktree_root": "/wt",
        "main_paths": frozenset(), "worktree_paths": frozenset(),
    }

    out = fn(result, before, after)

    assert out is result
    matches = [(a, k) for a, k in events if _event_type(a, k) == "red_write_boundary_checked"]
    assert matches, f"expected red_write_boundary_checked event, got {events!r}"
    payload = _event_payload(*matches[0])
    assert payload.get("stray_n") == 0, f"expected stray_n=0, got {payload!r}"


# ─── AC14: ambiguous -> pass-through, no failure, telemetry (closes M4) ──────


def test_ac14_ambiguous_verdict_passes_through_with_telemetry(monkeypatch):
    """AC14: (closes M4) ambiguous -> same object returned (NO build
    failure), plus a red_write_boundary_ambiguous event carrying
    stray_paths and worktree_new_paths.
    """
    fn = getattr(p5_mod, "_enforce_red_write_boundary", None)
    assert fn is not None, (
        "_enforce_red_write_boundary not found on phase_5_implement — GH1179 helper missing"
    )

    events: list = []
    monkeypatch.setattr(p5_mod, "_emit_safe", _capture_emit(events))

    result = StepResult(status="ok", data={}, duration_ms=0, step_name="invoke_red_llm")
    before = {
        "available": True, "main_repo_root": "/main", "worktree_root": "/wt",
        "main_paths": frozenset(), "worktree_paths": frozenset(),
    }
    after = {
        "available": True, "main_repo_root": "/main", "worktree_root": "/wt",
        "main_paths": frozenset({"tests/test_concurrent.py"}),
        "worktree_paths": frozenset({"tests/test_inside.py"}),
    }

    out = fn(result, before, after)

    assert out is result, "ambiguous verdict must NOT terminate the build — same object returned"
    matches = [(a, k) for a, k in events if _event_type(a, k) == "red_write_boundary_ambiguous"]
    assert matches, f"expected red_write_boundary_ambiguous event, got {events!r}"
    payload = _event_payload(*matches[0])
    assert payload.get("stray_paths") == ["tests/test_concurrent.py"], f"payload={payload!r}"
    assert payload.get("worktree_new_paths") == ["tests/test_inside.py"], f"payload={payload!r}"


# ─── AC15: violation -> terminal StepResult naming both roots + every path ────


def test_ac15_violation_returns_terminal_error_step_result(monkeypatch):
    """AC15: violation -> StepResult with status="error",
    step_name="invoke_red_llm", error_code="E_RED_WROTE_OUTSIDE_WORKTREE",
    and an error string naming the worktree root, the MAIN repo root, and
    EVERY stray path.
    """
    fn = getattr(p5_mod, "_enforce_red_write_boundary", None)
    assert fn is not None, (
        "_enforce_red_write_boundary not found on phase_5_implement — GH1179 helper missing"
    )

    monkeypatch.setattr(p5_mod, "_emit_safe", lambda *a, **k: None)

    result = StepResult(status="ok", data={}, duration_ms=0, step_name="invoke_red_llm")
    before = {
        "available": True, "main_repo_root": "/main-root", "worktree_root": "/wt-root",
        "main_paths": frozenset(), "worktree_paths": frozenset(),
    }
    after = {
        "available": True, "main_repo_root": "/main-root", "worktree_root": "/wt-root",
        "main_paths": frozenset({"tests/test_a.py", "tests/test_b.py"}),
        "worktree_paths": frozenset(),
    }

    out = fn(result, before, after)

    assert out.status == "error", f"expected status=error on violation, got {out.status!r}"
    assert out.step_name == "invoke_red_llm", f"expected step_name=invoke_red_llm, got {out.step_name!r}"
    assert out.error_code == "E_RED_WROTE_OUTSIDE_WORKTREE", f"got error_code={out.error_code!r}"
    msg = out.error or ""
    assert "/wt-root" in msg, f"error must name the worktree root: {msg!r}"
    assert "/main-root" in msg, f"error must name the MAIN repo root: {msg!r}"
    assert "tests/test_a.py" in msg and "tests/test_b.py" in msg, (
        f"error must name EVERY stray path: {msg!r}"
    )


# ─── AC16: violation -> warning event with expected keys ─────────────────────


def test_ac16_violation_emits_warning_event_with_expected_keys(monkeypatch):
    """AC16: violation emits red_write_boundary_violation with severity
    "warning" and payload keys main_repo_root, worktree_root, stray_paths.
    """
    fn = getattr(p5_mod, "_enforce_red_write_boundary", None)
    assert fn is not None, (
        "_enforce_red_write_boundary not found on phase_5_implement — GH1179 helper missing"
    )

    events: list = []
    monkeypatch.setattr(p5_mod, "_emit_safe", _capture_emit(events))

    result = StepResult(status="ok", data={}, duration_ms=0, step_name="invoke_red_llm")
    before = {
        "available": True, "main_repo_root": "/main-root", "worktree_root": "/wt-root",
        "main_paths": frozenset(), "worktree_paths": frozenset(),
    }
    after = {
        "available": True, "main_repo_root": "/main-root", "worktree_root": "/wt-root",
        "main_paths": frozenset({"tests/test_a.py"}), "worktree_paths": frozenset(),
    }

    fn(result, before, after)

    matches = [(a, k) for a, k in events if _event_type(a, k) == "red_write_boundary_violation"]
    assert matches, f"expected red_write_boundary_violation event, got {events!r}"
    args, kwargs = matches[0]
    payload = _event_payload(args, kwargs)
    # §1.6-7: _emit_safe(event_type, payload, severity="warning") — no 4th positional arg.
    severity = args[2] if len(args) > 2 else kwargs.get("severity", "warning")
    assert severity == "warning", f"expected severity='warning', got {severity!r}"
    assert payload.get("main_repo_root") == "/main-root", f"payload={payload!r}"
    assert payload.get("worktree_root") == "/wt-root", f"payload={payload!r}"
    assert payload.get("stray_paths") == ["tests/test_a.py"], f"payload={payload!r}"


# ─── AC17: E2E — stray write to MAIN yields the new error code ──────────────


def test_ac17_end_to_end_stray_write_to_main_yields_new_error_code(tmp_path, monkeypatch):
    """AC17: E2E (§1l): real repo + real git worktree add worktree;
    invoke_llm_subprocess stubbed to return ok AND ACTUALLY CREATE
    <main>/tests/test_stray.py -> _invoke_red_llm returns status="error",
    error_code="E_RED_WROTE_OUTSIDE_WORKTREE" (NOT ok, NOT E_RED_NO_MARKER).
    """
    main = _make_repo(tmp_path / "main")
    wt = _add_worktree(main, tmp_path / "wt", "feat17")
    scratchpad = wt / "scratchpad"
    scratchpad.mkdir(parents=True)

    def _stub_invoke(**kwargs):
        stray = main / "tests" / "test_stray.py"
        stray.write_text("def test_x(): pass\n")
        data = {"raw_response": "RED COMPLETE", "worker_written_paths": []}
        data.update(kwargs.get("extra_data") or {})
        return StepResult(status="ok", data=data, duration_ms=0, step_name="invoke_red_llm")

    monkeypatch.setattr(p5_mod, "invoke_llm_subprocess", _stub_invoke)

    ctx = _make_ctx(scratchpad, current_worktree_path=str(wt))
    prev = _prev_for_invoke(scratchpad)

    result = p5_mod._invoke_red_llm(ctx, prev)

    assert result.status == "error", (
        f"expected status=error when RED strayed into MAIN, got {result.status!r} data={result.data!r}"
    )
    assert result.error_code == "E_RED_WROTE_OUTSIDE_WORKTREE", (
        f"expected E_RED_WROTE_OUTSIDE_WORKTREE, got {result.error_code!r} "
        f"(must not be 'ok' and must not be E_RED_NO_MARKER — the ppba bug displaced)"
    )


# ─── AC18: E2E — write lands INSIDE worktree, no masking ─────────────────────


def test_ac18_end_to_end_write_inside_worktree_is_not_masked(tmp_path, monkeypatch):
    """AC18: E2E: identical fixture, stub writes <wt>/tests/test_inside.py
    -> status="ok", no red_write_boundary_violation event,
    result.data["red_test_paths"] is None (contract at
    phase_5_implement.py:1376).
    """
    main = _make_repo(tmp_path / "main")
    wt = _add_worktree(main, tmp_path / "wt", "feat18")
    scratchpad = wt / "scratchpad"
    scratchpad.mkdir(parents=True)

    def _stub_invoke(**kwargs):
        inside = wt / "tests" / "test_inside.py"
        inside.write_text("def test_y(): pass\n")
        data = {"raw_response": "RED COMPLETE", "worker_written_paths": []}
        data.update(kwargs.get("extra_data") or {})
        return StepResult(status="ok", data=data, duration_ms=0, step_name="invoke_red_llm")

    monkeypatch.setattr(p5_mod, "invoke_llm_subprocess", _stub_invoke)

    events: list = []
    monkeypatch.setattr(p5_mod, "_emit_safe", _capture_emit(events))

    ctx = _make_ctx(scratchpad, current_worktree_path=str(wt))
    prev = _prev_for_invoke(scratchpad)

    result = p5_mod._invoke_red_llm(ctx, prev)

    assert result.status == "ok", (
        f"expected status=ok for an in-worktree write, got {result.status!r} error={result.error!r}"
    )
    assert not any(_event_type(a, k) == "red_write_boundary_violation" for a, k in events), (
        f"unexpected violation event for a legitimate in-worktree write: {events!r}"
    )
    assert result.data.get("red_test_paths") is None, (
        f"existing contract (phase_5_implement.py:1376): red_test_paths must remain None on ok, "
        f"got {result.data!r}"
    )


# ─── AC19: E2E — concurrent writes yield ambiguous, not failure (closes M4) ──


def test_ac19_end_to_end_concurrent_writes_yield_ambiguous_not_failure(tmp_path, monkeypatch):
    """AC19: E2E (closes M4): identical fixture, stub writes
    <wt>/tests/test_inside.py AND <main>/tests/test_concurrent.py ->
    status="ok" and a red_write_boundary_ambiguous event — a concurrent
    third-party MAIN write never terminates a healthy build.
    """
    main = _make_repo(tmp_path / "main")
    wt = _add_worktree(main, tmp_path / "wt", "feat19")
    scratchpad = wt / "scratchpad"
    scratchpad.mkdir(parents=True)

    def _stub_invoke(**kwargs):
        inside = wt / "tests" / "test_inside.py"
        inside.write_text("def test_y(): pass\n")
        concurrent = main / "tests" / "test_concurrent.py"
        concurrent.write_text("def test_z(): pass\n")
        data = {"raw_response": "RED COMPLETE", "worker_written_paths": []}
        data.update(kwargs.get("extra_data") or {})
        return StepResult(status="ok", data=data, duration_ms=0, step_name="invoke_red_llm")

    monkeypatch.setattr(p5_mod, "invoke_llm_subprocess", _stub_invoke)
    events: list = []
    monkeypatch.setattr(p5_mod, "_emit_safe", _capture_emit(events))

    ctx = _make_ctx(scratchpad, current_worktree_path=str(wt))
    prev = _prev_for_invoke(scratchpad)

    result = p5_mod._invoke_red_llm(ctx, prev)

    assert result.status == "ok", (
        f"a concurrent third-party MAIN write must never terminate a healthy build, "
        f"got {result.status!r} error={result.error!r}"
    )
    assert any(_event_type(a, k) == "red_write_boundary_ambiguous" for a, k in events), (
        f"expected red_write_boundary_ambiguous event, got {events!r}"
    )


# ─── AC20: E2E displacement, not masking — E_RED_NO_MARKER survives ──────────


def test_ac20_end_to_end_displacement_not_masking_no_marker_survives(tmp_path, monkeypatch):
    """AC20: E2E displacement, not masking (closes M3): real repo +
    worktree; stub writes NOTHING anywhere -> _invoke_red_llm returns ok
    (verdict clean, gate silent), and feeding that result through
    write_red_artifact into _commit_red_tests still returns
    error_code == "E_RED_NO_MARKER".

    Scratchpad is deliberately OUTSIDE the worktree (via _make_scratchpad(),
    not `wt / "scratchpad"`): the RED log path carries a `tests/` segment
    (_prev_for_invoke), so a scratchpad nested inside the worktree would
    make `git ls-files --others` in `wt` see that log file as a test-shaped
    untracked path, taking the `red_files_drift`/git branch at :1729 and
    never reaching the E_RED_NO_MARKER cascade at :1788. current_worktree_path
    still points _resolve_git_cwd at the real worktree.
    """
    main = _make_repo(tmp_path / "main")
    wt = _add_worktree(main, tmp_path / "wt", "feat20")
    scratchpad = _make_scratchpad()

    def _stub_invoke(**kwargs):
        data = {"raw_response": "RED COMPLETE — nothing new on disk\n", "worker_written_paths": []}
        data.update(kwargs.get("extra_data") or {})
        return StepResult(status="ok", data=data, duration_ms=0, step_name="invoke_red_llm")

    monkeypatch.setattr(p5_mod, "invoke_llm_subprocess", _stub_invoke)

    ctx = _make_ctx(scratchpad, current_worktree_path=str(wt))
    prev = _prev_for_invoke(scratchpad)

    invoke_result = p5_mod._invoke_red_llm(ctx, prev)
    assert invoke_result.status == "ok", (
        f"expected ok (clean verdict, gate silent) when nothing was written anywhere, "
        f"got {invoke_result.status!r} error={invoke_result.error!r}"
    )

    write_result = p5_mod._write_red_artifact(ctx, invoke_result)
    assert write_result.status == "ok", f"write_red_artifact failed: {write_result.error!r}"

    commit_result = p5_mod._commit_red_tests(ctx, write_result)

    assert commit_result.error_code == "E_RED_NO_MARKER", (
        f"E_RED_NO_MARKER must survive as a strict displacement pin — the new gate must not "
        f"have fired (nothing test-shaped appeared anywhere) and the pre-existing empty-diff "
        f"cascade must still be reachable; got {commit_result.error_code!r} "
        f"(status={commit_result.status!r})"
    )


# ─── AC21: str worktree_root from a stub — no AttributeError/TypeError ───────


def test_ac21_str_worktree_root_from_resolve_worktree_root_does_not_raise(monkeypatch):
    """AC21: (closes M1) With _resolve_worktree_root stubbed to return the
    STRING "/tmp/fake-worktree" (as
    tests/test_gh1157_invoke_red_llm_failed_event.py:110 does) and an ok LLM
    result, _invoke_red_llm returns without raising AttributeError/TypeError
    and the ok result is unchanged.
    """
    sp = _make_scratchpad()
    monkeypatch.setattr(p5_mod, "_resolve_worktree_root", lambda ctx, scratchpad: "/tmp/fake-worktree")

    ok_result = StepResult(
        status="ok",
        data={"raw_response": "RED COMPLETE", "worker_written_paths": []},
        duration_ms=10,
        step_name="invoke_red_llm",
    )
    monkeypatch.setattr(p5_mod, "invoke_llm_subprocess", lambda **kw: ok_result)

    ctx = _make_ctx(sp)
    prev = _prev_for_invoke(sp)

    result = p5_mod._invoke_red_llm(ctx, prev)  # must not raise AttributeError/TypeError

    assert result.status == "ok", f"expected ok passthrough, got {result.status!r} error={result.error!r}"


# ─── AC22: missing scratchpad_dir must not break the error path ─────────────


def test_ac22_missing_scratchpad_dir_does_not_break_error_path(monkeypatch):
    """AC22: (m3) With a ctx whose org_config has NO scratchpad_dir (so
    _resolve_scratchpad raises ValueError) and a FAILING LLM result,
    _invoke_red_llm still returns that error result UNCHANGED — the
    pre-snapshot never breaks the error path.
    """
    ctx = WorkflowContext(
        tenant_id="hal", scope=None, db_path=None, org_config={},
        question="gh1179 ac22", session_id="test-session",
        persona="hal", framework=None, domain=None,
    )
    fake_error_result = StepResult(
        status="error", data={"stderr_tail": "boom", "exit_code": 1},
        duration_ms=5, step_name="invoke_red_llm",
        error="llm failed", error_code="E_LLM_TIMEOUT",
    )
    monkeypatch.setattr(p5_mod, "invoke_llm_subprocess", lambda **kw: fake_error_result)
    monkeypatch.setattr(p5_mod, "_emit_safe", lambda *a, **k: None)

    prev = _prev_for_invoke(_make_scratchpad())  # scratchpad path here is unrelated to ctx.org_config

    result = p5_mod._invoke_red_llm(ctx, prev)  # must not raise ValueError

    assert result is fake_error_result, (
        f"expected the SAME error StepResult returned unchanged despite missing scratchpad_dir, "
        f"got {result!r}"
    )
    assert result.error_code == "E_LLM_TIMEOUT"


# ─── AC23: registry + flags-catalog registration ─────────────────────────────


def test_ac23_error_code_and_flag_registered():
    """AC23: "E_RED_WROTE_OUTSIDE_WORKTREE" in error_codes.ERROR_CODES, and
    flags_catalog.FLAGS["HAL_RED_WRITE_BOUNDARY_GATE"] exists with
    kind == "gate" and default == "1"; flags_catalog.unregistered_tokens_in_files
    ([<touched prod files>]) omits the token.
    """
    import error_codes  # noqa: PLC0415
    import flags_catalog  # noqa: PLC0415

    assert "E_RED_WROTE_OUTSIDE_WORKTREE" in error_codes.ERROR_CODES, (
        "E_RED_WROTE_OUTSIDE_WORKTREE must be registered in error_codes.ERROR_CODES"
    )

    assert "HAL_RED_WRITE_BOUNDARY_GATE" in flags_catalog.FLAGS, (
        "HAL_RED_WRITE_BOUNDARY_GATE missing from flags_catalog.FLAGS"
    )
    entry = flags_catalog.FLAGS["HAL_RED_WRITE_BOUNDARY_GATE"]
    assert entry.get("kind") == "gate", f"expected kind='gate', got {entry!r}"
    assert str(entry.get("default")) == "1", f"expected default='1', got {entry!r}"

    engine_root = Path(flags_catalog.__file__).resolve().parent
    touched = [
        engine_root / "lib" / "red_write_boundary.py",
        engine_root / "workflows" / "phase_5_implement.py",
    ]
    existing = [str(p) for p in touched if p.is_file()]
    unregistered = flags_catalog.unregistered_tokens_in_files(existing)
    assert "HAL_RED_WRITE_BOUNDARY_GATE" not in unregistered, (
        f"HAL_RED_WRITE_BOUNDARY_GATE read but not registered: {unregistered!r}"
    )


# ─── AC24: consumer classification pins the naming constraint ───────────────


def test_ac24_error_code_classifies_needs_prompt_and_quality_gate():
    """AC24: error_locus.classify_error("E_RED_WROTE_OUTSIDE_WORKTREE") ==
    "needs-prompt" AND dispatcher_report.classify_error_code(
    "E_RED_WROTE_OUTSIDE_WORKTREE") == "quality-gate" — pins the naming
    constraint (§2/§1o-1) and the RED_ bucket.
    """
    import error_locus  # noqa: PLC0415
    from lib import dispatcher_report  # noqa: PLC0415

    assert error_locus.classify_error("E_RED_WROTE_OUTSIDE_WORKTREE") == "needs-prompt", (
        "E_RED_WROTE_OUTSIDE_WORKTREE must NOT contain a LINTABLE_PATTERNS token "
        "(CITE/SCOPE/COVERAGE/MISSING/FIELD/GROUND/IMPORT) — a name like "
        "E_RED_SCOPE_ESCAPE would wrongly route to 'lintable'"
    )
    assert dispatcher_report.classify_error_code("E_RED_WROTE_OUTSIDE_WORKTREE") == "quality-gate", (
        "E_RED_WROTE_OUTSIDE_WORKTREE must classify via the RED_ prefix bucket as 'quality-gate'"
    )
