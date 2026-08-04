"""RED tests for 3F4B71D4 — RED-path fallback chain (persisted-paths tier).

Closes the "engine can't find RED tests when git-diff is empty" category.

AC1  RED_TEST_PATHS_RELPATH constant defined at module scope.
AC2  _persist_red_test_paths writes non-empty list and returns True.
AC3  _persist_red_test_paths no-ops on empty input, returns False.
AC4  _read_red_test_paths round-trips persisted list; missing file → [].
AC5  _write_red_artifact persists derived working-tree RED paths to scratchpad.
AC6  _write_red_artifact threads derived paths into returned StepResult.data.
AC7  _commit_red_tests recovers via persisted-paths when git-diff is empty.
AC8  Recovery emits red_files_recovered_via_persisted_paths (not E_RED_NO_MARKER).
AC9  Happy path still emits red_files_drift(chosen="git"); recovery event absent.
AC10 All fallbacks absent AND git empty → E_RED_NO_MARKER still returned.

Import idiom: plain `import bytedigger_engine.workflows.phase_5_implement as p5` (conftest-import-time
singleton adds engine_py root + workflows to sys.path — §1q / 81F97F3D gate).
New symbols accessed via getattr() INSIDE test bodies so the file COLLECTS
cleanly even before GREEN ships them (D1CF5FDF §1q-ext).
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import pytest

# conftest-import-time singleton provides sys.path seam (§1q / 81F97F3D gate).
from bytedigger_engine.workflows import phase_5_implement as p5
from bytedigger_engine.contracts import StepResult, WorkflowContext


# ─── shared git-repo helpers ──────────────────────────────────────────────────

def _make_repo(base: Path) -> Path:
    """Create a minimal git repo with one initial commit.

    Uses os.path.realpath so /var/folders symlinks don't break subprocess
    cwd equality on macOS (§1j).
    """
    repo = Path(os.path.realpath(str(base / "repo")))
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "tester"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)
    placeholder = repo / "src" / "placeholder.py"
    placeholder.parent.mkdir(parents=True, exist_ok=True)
    placeholder.write_text("# placeholder\n")
    subprocess.run(["git", "add", "src/placeholder.py"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=repo, check=True)
    return repo


def _head_sha(repo: Path) -> str:
    r = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=repo, check=True
    )
    return r.stdout.strip()


def _commit_file(repo: Path, relpath: str, body: str, msg: str = "c") -> str:
    """Write, stage, commit a file; return new HEAD sha."""
    p = repo / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    subprocess.run(["git", "add", relpath], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", msg], cwd=repo, check=True)
    return _head_sha(repo)


def _add_untracked(repo: Path, relpath: str, body: str) -> None:
    """Write a file into the working tree WITHOUT staging/committing (untracked)."""
    p = repo / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)


def _make_scratchpad() -> Path:
    """Return a real-path'd tmpdir (§1j)."""
    return Path(os.path.realpath(tempfile.mkdtemp()))


def _write_pre_red_ref(scratchpad: Path, sha: str) -> None:
    """Pre-seed the frozen pre-red-ref.txt so git-diff vs HEAD is empty."""
    ref = scratchpad / p5.PRE_RED_REF_RELPATH
    ref.parent.mkdir(parents=True, exist_ok=True)
    ref.write_text(sha)


def _write_red_paths_file(scratchpad: Path, paths: list[str]) -> None:
    """Manually pre-seed the red-test-paths.txt scratchpad file."""
    relpath = "integrity/red-test-paths.txt"
    f = scratchpad / relpath
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text("\n".join(paths) + "\n")


# ─── shared ctx/prev constructors ─────────────────────────────────────────────

def _make_ctx(scratchpad: Path, git_cwd: str | None = None) -> WorkflowContext:
    org: dict = {"scratchpad_dir": str(scratchpad)}
    if git_cwd is not None:
        org["git_cwd"] = git_cwd
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question="3F4B71D4 fallback chain test",
        session_id="test-3f4b71d4",
        persona="hal",
        framework=None,
        domain=None,
    )


def _prev_for_write_red(scratchpad: Path, log_path: str | None = None, cycle: int = 1) -> StepResult:
    """Minimal prev StepResult accepted by _write_red_artifact."""
    lp = log_path or str(scratchpad / "tests/build-red-output.log")
    return StepResult(
        status="ok",
        data={
            "raw_response": "# stub RED LLM output\n",
            "log_path": lp,
            "spec_path": str(scratchpad / "specs/build-spec.md"),
            "cycle": cycle,
            "red_test_paths": None,
        },
        duration_ms=0,
        step_name="invoke_red_llm",
    )


def _prev_for_commit(scratchpad: Path, red_test_paths, cycle: int = 1) -> StepResult:
    """Minimal prev StepResult accepted by _commit_red_tests."""
    return StepResult(
        status="ok",
        data={
            "red_log_path": str(scratchpad / "tests/build-red-output.log"),
            "spec_path": str(scratchpad / "specs/build-spec.md"),
            "cycle": cycle,
            "red_test_paths": red_test_paths,
        },
        duration_ms=0,
        step_name="write_red_artifact",
    )


def _patch_emit_p5(monkeypatch) -> list[dict]:
    """Monkeypatch phase_5_implement._emit_safe and capture all calls.

    Matches the sibling pattern in test_phase_5_red_disk_fallback_9F2A50E8.py.
    """
    captured: list[dict] = []
    monkeypatch.setattr(
        p5,
        "_emit_safe",
        lambda et, payload, **kw: captured.append({"type": et, "payload": payload}),
    )
    return captured


# ─── AC1: RED_TEST_PATHS_RELPATH constant ─────────────────────────────────────


def test_ac1_red_test_paths_relpath_constant():
    """AC1: RED_TEST_PATHS_RELPATH == 'integrity/red-test-paths.txt' at module scope.

    Fails pre-GREEN: attribute does not exist.
    """
    value = getattr(p5, "RED_TEST_PATHS_RELPATH", None)
    assert value == "integrity/red-test-paths.txt", (
        f"Expected p5.RED_TEST_PATHS_RELPATH == 'integrity/red-test-paths.txt', "
        f"got {value!r}. Pre-GREEN: attribute absent — FAIL expected."
    )


# ─── AC2: _persist_red_test_paths writes non-empty list ──────────────────────


def test_ac2_persist_writes_nonempty_list():
    """AC2: _persist_red_test_paths(sp, ["a/test_x.py","b/test_y.py"]) writes both
    lines to sp/integrity/red-test-paths.txt and returns True.

    Fails pre-GREEN: function does not exist.
    """
    sp = _make_scratchpad()
    fn = getattr(p5, "_persist_red_test_paths", None)
    assert fn is not None, (
        "_persist_red_test_paths not found in phase_5_implement — symbol absent pre-GREEN (AC2)."
    )

    paths = ["a/test_x.py", "b/test_y.py"]
    ret = fn(str(sp), paths)

    assert ret is True, (
        f"_persist_red_test_paths should return True when given non-empty list, got {ret!r} (AC2)."
    )
    target = sp / "integrity" / "red-test-paths.txt"
    assert target.exists(), (
        f"Expected {target} to exist after persist call (AC2)."
    )
    written = [line for line in target.read_text().splitlines() if line.strip()]
    assert written == paths, (
        f"Expected lines {paths!r}, got {written!r} (AC2)."
    )


# ─── AC3: _persist_red_test_paths no-ops on empty input ──────────────────────


def test_ac3_persist_noop_on_empty():
    """AC3: _persist_red_test_paths(sp, []) returns False and does NOT create/overwrite.

    Pre-seeds the file with known content to verify it is unchanged.
    Fails pre-GREEN: function does not exist.
    """
    sp = _make_scratchpad()
    fn = getattr(p5, "_persist_red_test_paths", None)
    assert fn is not None, (
        "_persist_red_test_paths not found in phase_5_implement — symbol absent pre-GREEN (AC3)."
    )

    # Pre-seed with known content
    target = sp / "integrity" / "red-test-paths.txt"
    target.parent.mkdir(parents=True, exist_ok=True)
    original_content = "tests/existing_test.py\n"
    target.write_text(original_content)

    ret = fn(str(sp), [])

    assert ret is False, (
        f"_persist_red_test_paths([]) should return False (no-op on empty), got {ret!r} (AC3)."
    )
    assert target.exists(), (
        f"Pre-seeded file should still exist after no-op call (AC3)."
    )
    assert target.read_text() == original_content, (
        f"Pre-seeded file content must be unchanged after empty-list call. "
        f"Expected {original_content!r}, got {target.read_text()!r} (AC3)."
    )


# ─── AC4: _read_red_test_paths round-trip + missing file → [] ─────────────────


def test_ac4_read_round_trips_and_missing_returns_empty():
    """AC4: _read_red_test_paths round-trips _persist_red_test_paths; missing file → [].

    Fails pre-GREEN: functions do not exist.
    """
    persist_fn = getattr(p5, "_persist_red_test_paths", None)
    read_fn = getattr(p5, "_read_red_test_paths", None)
    assert persist_fn is not None, (
        "_persist_red_test_paths absent in phase_5_implement — pre-GREEN (AC4)."
    )
    assert read_fn is not None, (
        "_read_red_test_paths absent in phase_5_implement — pre-GREEN (AC4)."
    )

    sp = _make_scratchpad()
    paths = ["tests/test_alpha.py", "tests/test_beta.py", "tests/test_gamma.py"]
    persist_fn(str(sp), paths)
    recovered = read_fn(str(sp))
    assert recovered == paths, (
        f"Round-trip failed: persisted {paths!r}, read back {recovered!r} (AC4)."
    )

    # Missing file → []
    sp2 = _make_scratchpad()
    empty = read_fn(str(sp2))
    assert empty == [], (
        f"Expected [] for missing red-test-paths.txt, got {empty!r} (AC4)."
    )


# ─── AC5: _write_red_artifact persists derived working-tree paths ──────────────


def test_ac5_write_red_artifact_persists_paths(tmp_path):
    """AC5: after _write_red_artifact runs against a repo with an uncommitted
    tests/test_new.py, sp/integrity/red-test-paths.txt exists and contains
    'tests/test_new.py'.

    Fails pre-GREEN: _write_red_artifact does not call _persist_red_test_paths.
    """
    from bytedigger_engine.workflows.phase_5_implement import _write_red_artifact  # noqa: PLC0415

    repo = _make_repo(tmp_path)
    sp = _make_scratchpad()

    # Add uncommitted test file to working tree (untracked vs HEAD)
    _add_untracked(repo, "tests/test_new.py", "def test_placeholder():\n    assert False\n")

    ctx = _make_ctx(sp, git_cwd=str(repo))
    prev = _prev_for_write_red(sp)

    result = _write_red_artifact(ctx, prev)

    # Must not error
    assert result.status in ("ok",), (
        f"_write_red_artifact returned status={result.status!r}, "
        f"error_code={getattr(result, 'error_code', None)!r} (AC5)."
    )

    red_paths_file = sp / "integrity" / "red-test-paths.txt"
    assert red_paths_file.exists(), (
        f"Expected {red_paths_file} to exist after _write_red_artifact with "
        f"uncommitted tests/test_new.py in working tree. "
        "Pre-GREEN: persist call absent — FAIL expected (AC5)."
    )
    lines = [l for l in red_paths_file.read_text().splitlines() if l.strip()]
    assert any("test_new.py" in line for line in lines), (
        f"Expected 'tests/test_new.py' in persisted red-test-paths.txt lines, "
        f"got {lines!r} (AC5)."
    )


# ─── AC6: _write_red_artifact threads derived paths into StepResult.data ──────


def test_ac6_write_red_artifact_threads_paths_into_result(tmp_path):
    """AC6: returned StepResult.data['red_test_paths'] is ['tests/test_new.py']
    (not None) when the working tree has an uncommitted test file.

    Fails pre-GREEN: _write_red_artifact ignores ctx/git and threads prev.data's
    red_test_paths (which is None post-γ-cleanup).
    """
    from bytedigger_engine.workflows.phase_5_implement import _write_red_artifact  # noqa: PLC0415

    repo = _make_repo(tmp_path)
    sp = _make_scratchpad()

    _add_untracked(repo, "tests/test_new.py", "def test_placeholder():\n    assert False\n")

    ctx = _make_ctx(sp, git_cwd=str(repo))
    prev = _prev_for_write_red(sp)

    result = _write_red_artifact(ctx, prev)

    assert result.status in ("ok",), (
        f"_write_red_artifact status={result.status!r} (AC6)."
    )
    assert result.data is not None, "StepResult.data must not be None (AC6)."
    rtp = result.data.get("red_test_paths")
    assert rtp is not None and len(rtp) > 0, (
        f"Expected data['red_test_paths'] to be a non-empty list, got {rtp!r}. "
        "Pre-GREEN: red_test_paths remains None (γ cleanup) — FAIL expected (AC6)."
    )
    assert any("test_new.py" in p for p in rtp), (
        f"Expected 'test_new.py' among threaded red_test_paths={rtp!r} (AC6)."
    )


# ─── AC7: _commit_red_tests recovers via persisted-paths (the bug fix) ────────


def test_ac7_commit_recovers_via_persisted_paths(tmp_path):
    """AC7: git-diff empty (frozen SHA == HEAD) but red-test-paths.txt contains a
    committed test that exists on disk → _commit_red_tests returns status='ok'
    with red_test_paths==['tests/test_committed.py'], NOT E_RED_NO_MARKER.

    This is THE core regression test for #270 / 3F4B71D4.
    Fails pre-GREEN: else-branch does NOT read persisted file → E_RED_NO_MARKER.
    """
    from bytedigger_engine.workflows.phase_5_implement import _commit_red_tests  # noqa: PLC0415

    repo = _make_repo(tmp_path)
    sp = _make_scratchpad()

    # Commit the test file so it exists on disk (the "resume" scenario)
    _commit_file(repo, "tests/test_committed.py", "def test_x():\n    assert False\n", "red")

    # Force empty git diff: write current HEAD as the frozen pre-red SHA
    head = _head_sha(repo)
    _write_pre_red_ref(sp, head)

    # Pre-write the persisted paths file (simulating _write_red_artifact having run earlier)
    _write_red_paths_file(sp, ["tests/test_committed.py"])

    ctx = _make_ctx(sp, git_cwd=str(repo))
    # prev.data has no red_test_paths (simulates γ-cleanup None)
    prev = _prev_for_commit(sp, None)

    result = _commit_red_tests(ctx, prev)

    assert result.status == "ok", (
        f"Expected status='ok' via persisted-paths recovery, got {result.status!r} "
        f"error_code={getattr(result, 'error_code', None)!r}. "
        "Pre-GREEN: persisted-paths tier absent → E_RED_NO_MARKER — FAIL expected (AC7)."
    )
    assert result.data is not None, "StepResult.data must not be None (AC7)."
    rtp = result.data.get("red_test_paths")
    assert rtp == ["tests/test_committed.py"], (
        f"Expected red_test_paths==['tests/test_committed.py'], got {rtp!r} (AC7)."
    )


# ─── AC8: recovery emits red_files_recovered_via_persisted_paths ──────────────


def test_ac8_commit_recovery_emits_persisted_paths_event(tmp_path, monkeypatch):
    """AC8: the persisted-paths recovery branch emits 'red_files_recovered_via_persisted_paths'
    (NOT 'red_files_recovered_via_disk_fallback', NOT E_RED_NO_MARKER).

    Fails pre-GREEN: the branch and its emit do not exist.
    """
    from bytedigger_engine.workflows.phase_5_implement import _commit_red_tests  # noqa: PLC0415

    captured = _patch_emit_p5(monkeypatch)

    repo = _make_repo(tmp_path)
    sp = _make_scratchpad()

    _commit_file(repo, "tests/test_committed.py", "def test_x():\n    assert False\n", "red")
    head = _head_sha(repo)
    _write_pre_red_ref(sp, head)
    _write_red_paths_file(sp, ["tests/test_committed.py"])

    ctx = _make_ctx(sp, git_cwd=str(repo))
    prev = _prev_for_commit(sp, None)

    _commit_red_tests(ctx, prev)

    event_names = [e["type"] for e in captured]
    assert "red_files_recovered_via_persisted_paths" in event_names, (
        f"Expected 'red_files_recovered_via_persisted_paths' event but saw: {event_names!r}. "
        "Pre-GREEN: persisted-paths recovery branch absent — FAIL expected (AC8)."
    )
    assert "red_files_recovered_via_disk_fallback" not in event_names, (
        f"Persisted-paths tier must emit its own event, not the disk-fallback one. "
        f"Saw: {event_names!r} (AC8)."
    )
    # Verify payload shape
    evt = next(e for e in captured if e["type"] == "red_files_recovered_via_persisted_paths")
    assert evt["payload"].get("chosen") == "persisted_paths", (
        f"Expected chosen=='persisted_paths' in event payload, got {evt['payload']!r} (AC8)."
    )


# ─── AC9: happy path — git-diff non-empty, no recovery event ─────────────────


def test_ac9_happy_path_git_diff_nonempty_no_recovery_event(tmp_path, monkeypatch):
    """AC9: when git-diff is non-empty, red_files_drift(chosen='git') is emitted
    and 'red_files_recovered_via_persisted_paths' is NOT emitted.

    Fails pre-GREEN: if recovery is incorrectly triggered on the git-diff non-empty
    path, the recovery event appears — regression guard.
    Also the happy-path already works today, so drift event assertion is a correctness guard.
    """
    from bytedigger_engine.workflows.phase_5_implement import _commit_red_tests  # noqa: PLC0415

    captured = _patch_emit_p5(monkeypatch)

    repo = _make_repo(tmp_path)
    sp = _make_scratchpad()

    # Freeze SHA BEFORE the test file is added → diff vs frozen will be non-empty
    initial_sha = _head_sha(repo)
    _write_pre_red_ref(sp, initial_sha)

    # Now add an uncommitted test file to the working tree (untracked)
    _add_untracked(repo, "tests/test_new.py", "def test_placeholder():\n    assert False\n")

    ctx = _make_ctx(sp, git_cwd=str(repo))
    prev = _prev_for_commit(sp, None)

    result = _commit_red_tests(ctx, prev)

    event_names = [e["type"] for e in captured]

    # red_files_drift must be emitted (happy-path correctness guard)
    drift_evts = [e for e in captured if e["type"] == "red_files_drift"]
    assert drift_evts, (
        f"Expected 'red_files_drift' event on happy path (git-diff non-empty), "
        f"got events: {event_names!r} (AC9)."
    )
    drift_chosen = drift_evts[0]["payload"].get("chosen")
    assert drift_chosen == "git", (
        f"Expected drift event chosen=='git', got {drift_chosen!r} (AC9)."
    )

    # Recovery event must NOT appear on the happy path
    assert "red_files_recovered_via_persisted_paths" not in event_names, (
        f"'red_files_recovered_via_persisted_paths' must NOT be emitted when git-diff "
        f"is non-empty (happy path). Saw events: {event_names!r} (AC9)."
    )


# ─── AC10: all fallbacks absent → E_RED_NO_MARKER preserved ──────────────────


def test_ac10_no_fallbacks_returns_e_red_no_marker(tmp_path):
    """AC10: git-diff empty + no persisted-paths file + prev.data red_test_paths None
    → _commit_red_tests still returns E_RED_NO_MARKER (terminal preserved).

    This ensures we haven't broken the existing error terminal when there is
    genuinely nothing recoverable.
    Pre-GREEN: already returns E_RED_NO_MARKER (correctness guard — may PASS today).
    Post-GREEN: persisted-paths tier is consulted first, but with absent scratchpad
    file and None prev.data, all tiers empty → must still error.
    """
    from bytedigger_engine.workflows.phase_5_implement import _commit_red_tests  # noqa: PLC0415

    repo = _make_repo(tmp_path)
    sp = _make_scratchpad()

    # Force empty diff: frozen-ref == HEAD, nothing uncommitted
    head = _head_sha(repo)
    _write_pre_red_ref(sp, head)
    # Do NOT write red-test-paths.txt (absent scratchpad)
    # prev.data has no red_test_paths

    ctx = _make_ctx(sp, git_cwd=str(repo))
    prev = _prev_for_commit(sp, None)

    result = _commit_red_tests(ctx, prev)

    assert result.error_code == "E_RED_NO_MARKER", (
        f"Expected E_RED_NO_MARKER when all fallbacks are absent and git-diff is empty, "
        f"got error_code={getattr(result, 'error_code', None)!r}, status={result.status!r} (AC10)."
    )
