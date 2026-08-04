"""RED tests for 9F2A50E8 — Guarded on-disk RED fallback in `_commit_red_tests`.

AC1: _red_paths_present_on_disk filters to on-disk-existing paths.
AC2: _red_paths_present_on_disk is order-preserving and deduplicates.
AC3: _red_paths_present_on_disk normalizes None / "<missing>" / bare str inputs.
AC4: core fix — git-diff empty + on-disk ctx path → status "ok", NOT E_RED_NO_MARKER.
AC5: git-diff empty + no ctx override → E_RED_NO_MARKER (preserved behavior).
AC6: git-diff empty + fabricated (non-existent) ctx path → E_RED_NO_MARKER (anti-fab).
AC7: the corrected error message contains "no ctx red_test_paths exist on disk".
AC8: git-diff NON-empty → primary git path still works (back-compat regression guard).
AC9: disk-fallback path emits red_files_recovered_via_disk_fallback with chosen=="disk_fallback".
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

# sys.path seam provided by conftest.py (§1q / 81F97F3D gate — no module-level sys.path here).
from bytedigger_engine.workflows import phase_5_implement  # module exists pre-GREEN; conftest adds engine_py/workflows to path
from bytedigger_engine.contracts import StepResult, WorkflowContext

# PRE_RED_REF_RELPATH constant — used to write frozen-ref file
PRE_RED_REF_RELPATH = "integrity/pre-red-ref.txt"


# ─── shared helpers (mirrored from test_phase_5_implement_W4.py) ──────────────


def make_ctx(scratchpad: Path, **org_extra) -> WorkflowContext:
    org = {"scratchpad_dir": str(scratchpad), **org_extra}
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question="9F2A50E8 disk fallback test",
        session_id="test-9f2a50e8",
        persona="hal",
        framework=None,
        domain=None,
    )


def make_commit_prev(scratchpad: Path, red_test_paths) -> StepResult:
    """Build a prev StepResult for _commit_red_tests with given red_test_paths value."""
    return StepResult(
        status="ok",
        data={
            "red_log_path": str(scratchpad / "tests/build-red-output.log"),
            "spec_path": str(scratchpad / "specs/build-spec.md"),
            "red_bytes_written": 100,
            "cycle": 1,
            "red_test_paths": red_test_paths,
        },
        duration_ms=0,
        step_name="write_red_artifact",
    )


def init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)


def commit_file(repo: Path, relpath: str, body: str, msg: str = "c") -> None:
    p = repo / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    subprocess.run(["git", "add", relpath], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", msg], cwd=repo, check=True)


def minimal_repo(tmp_path: Path) -> Path:
    """Create a minimal git repo with one commit so commit_red_tests has a valid HEAD."""
    repo = tmp_path / "repo"
    init_repo(repo)
    commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
    return repo


def _get_head_sha(repo: Path) -> str:
    """Return the current HEAD SHA of the repo."""
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def _write_frozen_ref_as_head(scratchpad: Path, repo: Path) -> str:
    """Write current HEAD into the pre-red-ref.txt to force empty git diff.

    When frozen_sha == HEAD, _derive_red_paths_via_git_diff returns [] because
    git diff HEAD..HEAD is empty — this is the bug scenario (9F2A50E8).
    Returns the SHA written.
    """
    sha = _get_head_sha(repo)
    ref_path = scratchpad / PRE_RED_REF_RELPATH
    ref_path.parent.mkdir(parents=True, exist_ok=True)
    ref_path.write_text(sha)
    return sha


def _patch_emit_p5(monkeypatch) -> list[dict]:
    """Monkeypatch phase_5_implement._emit_safe; capture all calls.

    _emit_safe signature: (event_type: str, payload: dict, severity: str = "warning")
    """
    captured: list[dict] = []
    monkeypatch.setattr(
        phase_5_implement,
        "_emit_safe",
        lambda et, p, **kw: captured.append({"type": et, "payload": p}),
    )
    return captured


# ─── AC1: _red_paths_present_on_disk filters to on-disk-existing paths ────────


def test_present_filters_to_existing(tmp_path):
    """AC1: only the on-disk-existing path is returned; absent path is dropped."""
    # Defer attribute access to INSIDE the test so ImportError at attribute
    # lookup is an AssertionError/AttributeError at ASSERT time, not collection.
    fn = getattr(phase_5_implement, "_red_paths_present_on_disk", None)
    assert fn is not None, (
        "_red_paths_present_on_disk not found on phase_5_implement — helper not yet added (AC1 pre-GREEN expected FAIL)"
    )

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "tests").mkdir()
    (repo / "tests" / "a.py").write_text("# a\n")
    # tests/b.py deliberately NOT created

    result = fn(["tests/a.py", "tests/b.py"], str(repo))
    assert result == ["tests/a.py"], (
        f"Expected ['tests/a.py'] (b.py absent), got {result!r}"
    )


# ─── AC2: order-preserving + dedup ────────────────────────────────────────────


def test_present_order_dedup(tmp_path):
    """AC2: duplicate entries removed; order of first occurrence preserved."""
    fn = getattr(phase_5_implement, "_red_paths_present_on_disk", None)
    assert fn is not None, (
        "_red_paths_present_on_disk not found on phase_5_implement (AC2 pre-GREEN expected FAIL)"
    )

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "t").mkdir()
    (repo / "t" / "a.py").write_text("# a\n")
    (repo / "t" / "c.py").write_text("# c\n")
    # t/b.py NOT created

    result = fn(["t/a.py", "t/a.py", "t/c.py"], str(repo))
    assert result == ["t/a.py", "t/c.py"], (
        f"Expected ['t/a.py', 't/c.py'] (dedup + order preserved), got {result!r}"
    )


# ─── AC3: normalizes None / "<missing>" / bare str ────────────────────────────


def test_present_normalizes_input(tmp_path):
    """AC3: None and '<missing>' return []; bare str treated as single-item list."""
    fn = getattr(phase_5_implement, "_red_paths_present_on_disk", None)
    assert fn is not None, (
        "_red_paths_present_on_disk not found on phase_5_implement (AC3 pre-GREEN expected FAIL)"
    )

    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "tests").mkdir()
    (repo / "tests" / "a.py").write_text("# a\n")

    assert fn(None, str(repo)) == [], (
        f"None should normalize to [] — got {fn(None, str(repo))!r}"
    )
    assert fn("<missing>", str(repo)) == [], (
        f"'<missing>' should normalize to [] — got {fn('<missing>', str(repo))!r}"
    )
    # str (not list) should be accepted as a single-item input
    result_str = fn("tests/a.py", str(repo))
    assert result_str == ["tests/a.py"], (
        f"Bare str 'tests/a.py' (exists on disk) should return ['tests/a.py'], got {result_str!r}"
    )


# ─── AC4: core fix — empty git diff + on-disk ctx path → ok ──────────────────


def test_commit_recovers_via_disk_fallback(tmp_path):
    """AC4: THE CORE FIX.

    git-diff empty (frozen-ref == HEAD) + ctx red_test_paths points at an
    on-disk committed test file → _commit_red_tests must return status='ok'
    with data['red_test_paths'] == the present path, NOT E_RED_NO_MARKER.

    Pre-GREEN: returns E_RED_NO_MARKER (hard error in else branch) → FAIL.
    """
    from bytedigger_engine.workflows.phase_5_implement import _commit_red_tests  # noqa: PLC0415

    repo = minimal_repo(tmp_path)
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()

    # Commit the test file so it exists on disk AND in git history
    test_body = "def test_placeholder():\n    assert False\n"
    commit_file(repo, "tests/foo_test.py", test_body, "red")

    # Force empty diff: write HEAD as the frozen pre-red SHA
    _write_frozen_ref_as_head(scratchpad, repo)

    ctx = make_ctx(scratchpad, git_cwd=str(repo))
    prev = make_commit_prev(scratchpad, ["tests/foo_test.py"])

    result = _commit_red_tests(ctx, prev)

    assert result.status == "ok", (
        f"Expected status='ok' with disk fallback active, got {result.status!r} "
        f"(error_code={result.error_code!r}, error={getattr(result, 'error', None)!r}). "
        "Pre-GREEN hard error in else-branch — fallback not yet implemented."
    )
    assert result.data is not None and result.data.get("red_test_paths") == ["tests/foo_test.py"], (
        f"Expected data['red_test_paths']==['tests/foo_test.py'], got {result.data!r}"
    )
    assert getattr(result, "error_code", None) is None, (
        f"Expected error_code=None on ok result, got {result.error_code!r}"
    )


# ─── AC5: no ctx override → E_RED_NO_MARKER preserved ────────────────────────


def test_commit_no_marker_when_no_candidates(tmp_path):
    """AC5: git-diff empty + no ctx red_test_paths → E_RED_NO_MARKER.

    Guards preserved behavior: when there genuinely is no RED, must still fail.
    Pre-GREEN: already returns E_RED_NO_MARKER → likely PASSES (regression guard).
    """
    from bytedigger_engine.workflows.phase_5_implement import _commit_red_tests  # noqa: PLC0415

    repo = minimal_repo(tmp_path)
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()

    # Force empty diff
    _write_frozen_ref_as_head(scratchpad, repo)

    ctx = make_ctx(scratchpad, git_cwd=str(repo))
    # red_test_paths absent (None value stored in data)
    prev = make_commit_prev(scratchpad, None)

    result = _commit_red_tests(ctx, prev)

    assert result.error_code == "E_RED_NO_MARKER", (
        f"Expected E_RED_NO_MARKER when no ctx candidates and git diff empty, "
        f"got error_code={result.error_code!r}, status={result.status!r}"
    )


# ─── AC6: fabricated (non-existent) ctx path → E_RED_NO_MARKER ───────────────


def test_commit_rejects_nonexistent_candidates(tmp_path):
    """AC6: git-diff empty + ctx paths that do NOT exist on disk → E_RED_NO_MARKER.

    Anti-fabrication: an LLM-fabricated path not present on disk is rejected.
    Pre-GREEN: may already return E_RED_NO_MARKER (regression guard).
    Post-GREEN: fallback filters to on-disk-present only; bogus path → empty → error.
    """
    from bytedigger_engine.workflows.phase_5_implement import _commit_red_tests  # noqa: PLC0415

    repo = minimal_repo(tmp_path)
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()

    # Force empty diff
    _write_frozen_ref_as_head(scratchpad, repo)

    ctx = make_ctx(scratchpad, git_cwd=str(repo))
    # Provide a path that does NOT exist on disk
    prev = make_commit_prev(scratchpad, ["tests/does_not_exist.py"])

    result = _commit_red_tests(ctx, prev)

    assert result.error_code == "E_RED_NO_MARKER", (
        f"Expected E_RED_NO_MARKER for fabricated non-existent path, "
        f"got error_code={result.error_code!r}, status={result.status!r}. "
        "Post-GREEN: _red_paths_present_on_disk must filter it out."
    )


# ─── AC7: corrected error message text ────────────────────────────────────────


def test_commit_error_msg_corrected(tmp_path):
    """AC7: E_RED_NO_MARKER error text contains the corrected message.

    Pre-GREEN message: 'tests may already be committed or the frozen ref is stale.'
    Post-GREEN message must contain: 'no ctx red_test_paths exist on disk'
    Pre-GREEN: old message → FAIL.
    """
    from bytedigger_engine.workflows.phase_5_implement import _commit_red_tests  # noqa: PLC0415

    repo = minimal_repo(tmp_path)
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()

    # Force empty diff; no on-disk candidates
    _write_frozen_ref_as_head(scratchpad, repo)

    ctx = make_ctx(scratchpad, git_cwd=str(repo))
    prev = make_commit_prev(scratchpad, None)

    result = _commit_red_tests(ctx, prev)

    assert result.error_code == "E_RED_NO_MARKER", (
        f"Expected E_RED_NO_MARKER but got {result.error_code!r}"
    )
    error_text = getattr(result, "error", "") or ""
    assert "no ctx red_test_paths exist on disk" in error_text, (
        f"Expected corrected message 'no ctx red_test_paths exist on disk' in error text, "
        f"got: {error_text!r}. Pre-GREEN has old message — FAIL expected."
    )


# ─── AC8: primary git path unchanged when diff is non-empty ──────────────────


def test_commit_primary_git_path_unchanged(tmp_path):
    """AC8: git-diff NON-empty → primary git path used; fallback NOT triggered.

    The test file is in the working tree but NOT committed, so git diff vs
    the frozen pre-red SHA (the initial commit HEAD) IS non-empty.
    Pre-GREEN: already works (back-compat regression guard) — likely PASSES.
    """
    from bytedigger_engine.workflows.phase_5_implement import _commit_red_tests  # noqa: PLC0415

    repo = minimal_repo(tmp_path)
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()

    # Do NOT write frozen-ref == HEAD; let _resolve_frozen_pre_red_sha fall back
    # to HEAD of the initial commit (before the test file was added).
    # Capture HEAD before adding the test file.
    frozen_sha = _get_head_sha(repo)
    # Write frozen SHA = the initial commit (so diff will show the new file)
    ref_path = scratchpad / PRE_RED_REF_RELPATH
    ref_path.parent.mkdir(parents=True, exist_ok=True)
    ref_path.write_text(frozen_sha)

    # Now commit the test file AFTER freezing — diff vs frozen will be non-empty
    test_body = "def test_placeholder():\n    assert False\n"
    commit_file(repo, "tests/bar_test.py", test_body, "red")

    ctx = make_ctx(scratchpad, git_cwd=str(repo))
    prev = make_commit_prev(scratchpad, None)  # ctx paths irrelevant; git wins

    result = _commit_red_tests(ctx, prev)

    # Primary git path must still work: should NOT produce E_RED_NO_MARKER
    assert result.error_code != "E_RED_NO_MARKER", (
        f"Primary git path broken: got E_RED_NO_MARKER when diff is non-empty. "
        f"status={result.status!r}, error={getattr(result, 'error', None)!r}"
    )
    # If status ok, red_test_paths must include the git-detected file
    if result.status == "ok":
        paths = result.data.get("red_test_paths", []) if result.data else []
        assert any("bar_test" in p for p in paths), (
            f"Expected 'bar_test.py' in git-derived red_test_paths, got {paths!r}"
        )


# ─── AC9: disk-fallback emits red_files_recovered_via_disk_fallback ──────────


def test_commit_emits_fallback_telemetry(tmp_path, monkeypatch):
    """AC9: disk-fallback path emits red_files_recovered_via_disk_fallback with chosen=='disk_fallback'.

    Pre-GREEN: fallback branch does not exist → event never emitted → FAIL.
    """
    from bytedigger_engine.workflows.phase_5_implement import _commit_red_tests  # noqa: PLC0415

    # Capture all _emit_safe calls from phase_5_implement
    captured = _patch_emit_p5(monkeypatch)

    repo = minimal_repo(tmp_path)
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()

    # Commit the test file so disk fallback finds it
    test_body = "def test_placeholder():\n    assert False\n"
    commit_file(repo, "tests/foo_test.py", test_body, "red")

    # Force empty diff: frozen-ref == HEAD
    _write_frozen_ref_as_head(scratchpad, repo)

    ctx = make_ctx(scratchpad, git_cwd=str(repo))
    prev = make_commit_prev(scratchpad, ["tests/foo_test.py"])

    result = _commit_red_tests(ctx, prev)

    # Find the fallback telemetry event
    fallback_events = [
        e for e in captured
        if e["type"] == "red_files_recovered_via_disk_fallback"
    ]
    assert fallback_events, (
        f"Expected 'red_files_recovered_via_disk_fallback' event to be emitted "
        f"during disk fallback, but no such event found. "
        f"Captured events: {[e['type'] for e in captured]!r}. "
        f"result.status={result.status!r}, result.error_code={getattr(result, 'error_code', None)!r}. "
        "Pre-GREEN: fallback branch not yet implemented → FAIL."
    )
    chosen = fallback_events[0]["payload"].get("chosen")
    assert chosen == "disk_fallback", (
        f"Expected chosen=='disk_fallback' in fallback event payload, got {chosen!r}. "
        f"Full payload: {fallback_events[0]['payload']!r}"
    )
