"""8D162396 RED tests — frozen pre-phase SHA boundary for RED-loop re-entry.

Closes agreement 8D162396: on cycle-2 re-entry the diff boundary used to
recover RED test paths drifts to live HEAD (which already contains cycle-1's
committed tests) → empty diff → E_RED_NO_MARKER.

Fix: two new helpers + three wiring edits (spec §2).

  H1 _resolve_frozen_pre_red_sha(scratchpad, git_cwd) -> str
  H2 _persist_pre_red_ref(scratchpad, sha) -> bool
  W3 _derive_red_paths_via_git_diff(cwd, frozen_sha=None) optional param
  W1/W2/E1 wiring in _commit_red_tests

These tests FAIL pre-GREEN (helpers absent, W3 signature missing, E1 not yet
reworded). They PASS once GREEN implements the helpers and wiring edits.
"""
from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

import pytest

# Import the module at top level — conftest-import-time singleton already put
# engine_py root + workflows on sys.path (§1q / 81F97F3D gate).
from bytedigger_engine.workflows import phase_5_implement as mod

# NOTE: _resolve_frozen_pre_red_sha and _persist_pre_red_ref do NOT exist yet.
# We resolve them via getattr() inside each test body (D1CF5FDF: defer
# not-yet-existing imports to assert-time so the file COLLECTS cleanly).


# ─── shared git-repo helpers ──────────────────────────────────────────────────


def _make_repo(base: Path) -> Path:
    """Create a minimal git repo under base with one initial commit.

    Uses os.path.realpath so /var/folders symlinks don't break subprocess
    cwd equality on macOS (§1j).
    """
    repo = Path(os.path.realpath(str(base / "repo")))
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "tester"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)
    # Initial commit so HEAD is valid
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
    """Write a file, commit it, return new HEAD sha."""
    p = repo / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    subprocess.run(["git", "add", relpath], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", msg], cwd=repo, check=True)
    return _head_sha(repo)


def _make_scratchpad() -> Path:
    """Return a real-path'd tmp scratchpad dir (§1j)."""
    return Path(os.path.realpath(tempfile.mkdtemp()))


def _make_ctx(scratchpad: Path, git_cwd: str | None = None):
    from bytedigger_engine.contracts import WorkflowContext  # noqa: PLC0415

    org: dict = {"scratchpad_dir": str(scratchpad)}
    if git_cwd is not None:
        org["git_cwd"] = git_cwd
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question="add a feature",
        session_id="test-session",
        persona="hal",
        framework=None,
        domain=None,
    )


def _prev_for_commit(scratchpad: Path, cycle: int = 1):
    """Minimal prev StepResult accepted by _commit_red_tests."""
    from bytedigger_engine.contracts import StepResult  # noqa: PLC0415

    return StepResult(
        status="ok",
        data={
            "red_log_path": str(scratchpad / "tests/build-red-output.log"),
            "spec_path": str(scratchpad / "specs/build-spec.md"),
            "cycle": cycle,
            "red_test_paths": None,
        },
        duration_ms=0,
        step_name="write_red_artifact",
    )


# ─── AC1: resolve frozen SHA — ref file present ───────────────────────────────


def test_ac1_resolve_frozen_reads_ref_file(tmp_path):
    """AC1: scratchpad has integrity/pre-red-ref.txt with 40-hex SHA X.

    _resolve_frozen_pre_red_sha(scratchpad, cwd) must return X, NOT live HEAD.
    Fails pre-GREEN: symbol does not exist yet.
    """
    _resolve_frozen_pre_red_sha = getattr(mod, "_resolve_frozen_pre_red_sha", None)
    assert _resolve_frozen_pre_red_sha is not None, (
        "_resolve_frozen_pre_red_sha not found in phase_5_implement — GREEN symbol missing"
    )

    scratchpad = _make_scratchpad()
    repo = _make_repo(tmp_path)

    frozen_sha = "a" * 40  # 40-hex boundary written by a prior cycle
    ref_path = Path(scratchpad) / mod.PRE_RED_REF_RELPATH
    ref_path.parent.mkdir(parents=True, exist_ok=True)
    ref_path.write_text(frozen_sha)

    result = _resolve_frozen_pre_red_sha(scratchpad, str(repo))

    assert result == frozen_sha, (
        f"Expected frozen SHA {frozen_sha!r} from ref file, got {result!r}. "
        "H1 must return the persisted ref when the file exists."
    )


# ─── AC2: resolve frozen SHA — no ref file, falls back to HEAD ───────────────


def test_ac2_resolve_frozen_falls_back_to_head(tmp_path):
    """AC2: no ref file in scratchpad → _resolve_frozen_pre_red_sha returns HEAD.

    Fails pre-GREEN: symbol does not exist yet.
    """
    _resolve_frozen_pre_red_sha = getattr(mod, "_resolve_frozen_pre_red_sha", None)
    assert _resolve_frozen_pre_red_sha is not None, (
        "_resolve_frozen_pre_red_sha not found in phase_5_implement — GREEN symbol missing"
    )

    scratchpad = _make_scratchpad()
    repo = _make_repo(tmp_path)
    expected_head = _head_sha(repo)

    # Scratchpad deliberately has no integrity/ sub-dir
    result = _resolve_frozen_pre_red_sha(scratchpad, str(repo))

    assert result == expected_head, (
        f"Expected HEAD fallback {expected_head!r} when ref file absent, got {result!r}. "
        "H1 must return git rev-parse HEAD when the ref file does not exist."
    )


# ─── AC3: persist ref — write-once first call ─────────────────────────────────


def test_ac3_persist_ref_writes_and_returns_true():
    """AC3: scratchpad without ref file → _persist_pre_red_ref(scratchpad, sha)
    writes the sha and returns True.

    Fails pre-GREEN: symbol does not exist yet.
    """
    _persist_pre_red_ref = getattr(mod, "_persist_pre_red_ref", None)
    assert _persist_pre_red_ref is not None, (
        "_persist_pre_red_ref not found in phase_5_implement — GREEN symbol missing"
    )

    scratchpad = _make_scratchpad()
    sha = "b" * 40

    wrote = _persist_pre_red_ref(scratchpad, sha)

    ref_path = Path(scratchpad) / mod.PRE_RED_REF_RELPATH
    assert wrote is True, f"Expected True (first write), got {wrote!r}"
    assert ref_path.exists(), "ref file must be created by _persist_pre_red_ref"
    assert ref_path.read_text().strip() == sha, (
        f"ref file content must be {sha!r}, got {ref_path.read_text()!r}"
    )


# ─── AC4: persist ref — write-once, second call preserves existing ───────────


def test_ac4_persist_ref_write_once_preserves_existing():
    """AC4: ref file already holds S1 → _persist_pre_red_ref(scratchpad, S2)
    does NOT overwrite; file still holds S1; returns False.

    Fails pre-GREEN: symbol does not exist yet.
    """
    _persist_pre_red_ref = getattr(mod, "_persist_pre_red_ref", None)
    assert _persist_pre_red_ref is not None, (
        "_persist_pre_red_ref not found in phase_5_implement — GREEN symbol missing"
    )

    scratchpad = _make_scratchpad()
    sha1 = "c" * 40
    sha2 = "d" * 40

    # Pre-stage the file as if cycle-1 already wrote it
    ref_path = Path(scratchpad) / mod.PRE_RED_REF_RELPATH
    ref_path.parent.mkdir(parents=True, exist_ok=True)
    ref_path.write_text(sha1)

    wrote = _persist_pre_red_ref(scratchpad, sha2)

    assert wrote is False, f"Expected False (existing file preserved), got {wrote!r}"
    assert ref_path.read_text().strip() == sha1, (
        f"ref file must still hold {sha1!r} after write-once guard, got {ref_path.read_text()!r}"
    )


# ─── AC5: derive with frozen_sha returns committed test path ──────────────────


def test_ac5_derive_with_frozen_sha_finds_committed_test(tmp_path):
    """AC5: _derive_red_paths_via_git_diff(cwd, frozen_sha=<pre-commit-sha>)
    returns the test path committed after frozen_sha.

    Fails pre-GREEN: W3 signature (frozen_sha kwarg) not yet added.
    The current signature has no frozen_sha param → TypeError → FAIL.
    """
    repo = _make_repo(tmp_path)

    # Capture the frozen boundary — before the RED test is committed
    frozen_sha = _head_sha(repo)

    # Commit a RED test file after the frozen boundary
    _commit_file(repo, "tests/test_feature_x.py", "def test_x(): assert False\n", "add RED test")

    result = mod._derive_red_paths_via_git_diff(str(repo), frozen_sha=frozen_sha)

    assert result, (
        "Expected non-empty list from _derive_red_paths_via_git_diff with frozen_sha; "
        "got empty. W3 must diff against frozen_sha when provided."
    )
    relpath_result = [os.path.basename(p) for p in result]
    assert "test_feature_x.py" in relpath_result or any(
        "test_feature_x.py" in p for p in result
    ), (
        f"Expected 'test_feature_x.py' in derived paths, got {result!r}"
    )


# ─── AC6: derive back-compat — no frozen_sha kwarg does not raise ─────────────


def test_ac6_derive_no_frozen_sha_back_compat(tmp_path):
    """AC6: _derive_red_paths_via_git_diff(cwd) with NO frozen_sha kwarg must
    not raise TypeError (back-compat: param is optional with default None).

    Pre-GREEN this already passes because the current signature has no frozen_sha
    and therefore no TypeError (calling without it works today). This test is a
    back-compat correctness guard: after GREEN adds frozen_sha=None, this STILL
    passes. It does not fail pre-GREEN (correctness guard, not forcing fn).
    """
    repo = _make_repo(tmp_path)

    # Should not raise regardless of repo state
    try:
        mod._derive_red_paths_via_git_diff(str(repo))
    except TypeError as exc:
        pytest.fail(
            f"_derive_red_paths_via_git_diff raised TypeError when called without "
            f"frozen_sha (back-compat broken): {exc}"
        )


# ─── AC7: E_RED_NO_MARKER error_code unchanged; message references boundary ───


def test_ac7_e_red_no_marker_code_unchanged_message_updated(tmp_path):
    """AC7: E_RED_NO_MARKER error_code must stay identical; after GREEN the
    error= message must reference the frozen pre-red boundary, not only the
    old 'Files: [...]' marker text.

    Pre-GREEN: error_code passes (already correct) but the message substring
    assert on 'frozen' / 'pre-red' FAILS (old message says 'Files: [...]').
    This FAILS pre-GREEN on the message assertion.
    """
    from bytedigger_engine.contracts import StepResult  # noqa: PLC0415
    from bytedigger_engine.workflows.phase_5_implement import _commit_red_tests  # noqa: PLC0415

    repo = _make_repo(tmp_path)
    scratchpad = _make_scratchpad()

    # Empty repo — no untracked test files → _derive_red_paths_via_git_diff returns []
    ctx = _make_ctx(scratchpad, git_cwd=str(repo))
    prev = _prev_for_commit(scratchpad, cycle=1)

    result = _commit_red_tests(ctx, prev)

    assert getattr(result, "error_code", None) == "E_RED_NO_MARKER", (
        f"E_RED_NO_MARKER error_code must remain unchanged after E1 reword; "
        f"got {getattr(result, 'error_code', None)!r}"
    )

    err_msg = (getattr(result, "error", "") or "").lower()
    # After GREEN E1: message must mention the frozen pre-red boundary concept.
    # Pre-GREEN: this assertion FAILS because the old message only says "Files: [...]".
    boundary_terms = ["frozen", "pre-red", "pre_red", "boundary", "pre-phase"]
    has_boundary_ref = any(t in err_msg for t in boundary_terms)
    assert has_boundary_ref, (
        "E_RED_NO_MARKER error= message must reference the frozen pre-red boundary "
        "(e.g. 'frozen pre-red SHA', 'pre-red boundary', 'frozen ref'). "
        f"Got: {getattr(result, 'error', '')!r}. "
        "E1 wiring in spec §2 not yet applied."
    )


# ─── AC8: cycle-2 integration — no E_RED_NO_MARKER when ref present ──────────


def test_ac8_cycle2_reentry_no_e_red_no_marker(tmp_path):
    """AC8: simulated cycle-2 re-entry with pre-red-ref.txt present and a
    committed RED test → _commit_red_tests must NOT return E_RED_NO_MARKER.

    Scenario:
    - Repo has an initial commit (frozen boundary)
    - A RED test was committed in cycle-1 (so live HEAD diff is empty)
    - integrity/pre-red-ref.txt holds the pre-phase SHA (frozen boundary)
    - prev.data has cycle=2

    Pre-GREEN: W1 calls _derive_red_paths_via_git_diff(_git_cwd_early) with NO
    frozen_sha → diffs against live HEAD → empty → E_RED_NO_MARKER returned.
    Post-GREEN: W1 calls with frozen_sha=_resolve_frozen_pre_red_sha(...) →
    diffs against the frozen boundary → test path found → no error.

    This test FAILS pre-GREEN.
    """
    from bytedigger_engine.workflows.phase_5_implement import _commit_red_tests  # noqa: PLC0415

    repo = _make_repo(tmp_path)

    # frozen boundary = HEAD after init commit
    frozen_sha = _head_sha(repo)

    # cycle-1 committed a RED test (so live HEAD is now 1 commit ahead of frozen_sha)
    _commit_file(repo, "tests/test_cycle1_red.py", "def test_fail(): assert False\n", "RED: cycle-1 tests")

    # Pre-stage: scratchpad with the frozen pre-red ref (as write-once H2 would produce)
    scratchpad = _make_scratchpad()
    ref_path = Path(scratchpad) / mod.PRE_RED_REF_RELPATH
    ref_path.parent.mkdir(parents=True, exist_ok=True)
    ref_path.write_text(frozen_sha)

    # cycle=2 prev (re-entry after validation failed)
    ctx = _make_ctx(scratchpad, git_cwd=str(repo))
    prev = _prev_for_commit(scratchpad, cycle=2)

    result = _commit_red_tests(ctx, prev)

    assert getattr(result, "error_code", None) != "E_RED_NO_MARKER", (
        "cycle-2 re-entry with frozen pre-red ref MUST NOT return E_RED_NO_MARKER. "
        "W1+W2+W3 wiring not yet applied: _commit_red_tests still diffs against "
        f"live HEAD (empty on cycle-2). Got error_code={getattr(result, 'error_code', None)!r}, "
        f"error={getattr(result, 'error', None)!r}"
    )
