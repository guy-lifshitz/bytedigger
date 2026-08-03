"""RED tests for GH947 — phase_6 fix-worker surface guard + ignored-path commit
resilience.

Contract (spec: SHARED/memory/Decisions/2026-07-17_GH947_fix_surface_guard_spec.md):
  - new helper _partition_fix_surface(prod_paths, pre_fix_sha, review_text, git_cwd)
    -> (allowed, violations); a path is a violation iff it did NOT exist at the
    pre-fix boundary AND its literal string does not occur in review_text.
  - new helper _drop_add_ignored_paths(stderr, prod_paths) -> (retained, dropped);
    drops paths matching a stderr ignored-paths line (exact or dir-prefix match).
  - _commit_fix_code wires both: (a) surface guard right after prod_paths is
    finalized — unlinks untracked violations, emits fix_surface_violation /
    fix_surface_guard_skipped / fix_commit_skipped(all_paths_off_surface);
    (b) on git-add non_lock_error containing the gitignore marker, drops ignored
    paths and retries add once, emitting commit_gitignored_paths_skipped /
    fix_commit_skipped(all_paths_gitignored); non-ignored non_lock_error is
    unchanged (E_FIX_COMMIT_FAILED regression pin).

None of this exists yet in production. All tests MUST FAIL until GREEN
implements _partition_fix_surface, _drop_add_ignored_paths, and wires them
into _commit_fix_code in workflows/phase_6_review.py.

Do NOT implement any production behavior here — RED-only file.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

HERE = Path(__file__).parent
ENGINE_ROOT = HERE.parent
sys.path.insert(0, str(ENGINE_ROOT))

from bytedigger_engine.workflows import phase_6_review as _p6  # noqa: E402
from bytedigger_engine.workflows.phase_6_review import _commit_fix_code  # noqa: E402
from bytedigger_engine.contracts import WorkflowContext  # noqa: E402


# ─── helpers (mirrors tests/test_phase_6_commit_fix_tests_8FE3D757.py) ────────


def _make_ctx(tmp_path: Path, **cfg_extra) -> WorkflowContext:
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    git_cwd = tmp_path / "repo"
    git_cwd.mkdir(parents=True, exist_ok=True)
    org = {
        "scratchpad_dir": str(scratchpad),
        "git_cwd": str(git_cwd),
        **cfg_extra,
    }
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question="gh947 fix surface guard test",
        session_id="test-session-gh947",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_prev(pre_fix_sha: str, cycle: int = 1, **extra) -> MagicMock:
    prev = MagicMock()
    data: dict = {"cycle": cycle, "pre_fix_sha": pre_fix_sha, **extra}
    prev.data = data
    return prev


def _git(repo_dir: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args], cwd=repo_dir, check=check, capture_output=True, text=True,
    )


def _init_git_repo(repo_dir: Path) -> str:
    """Init a real repo, commit a baseline file, return the boundary HEAD sha."""
    _git(repo_dir, "init", "-q", "--initial-branch=main")
    _git(repo_dir, "-c", "user.email=test@hal.test", "-c", "user.name=HAL Test",
         "config", "user.email", "test@hal.test")
    _git(repo_dir, "config", "user.name", "HAL Test")
    baseline = repo_dir / "baseline.py"
    baseline.write_text("# baseline\n", encoding="utf-8")
    _git(repo_dir, "add", "baseline.py")
    _git(repo_dir, "-c", "user.email=test@hal.test", "-c", "user.name=HAL Test",
         "commit", "-q", "-m", "initial commit")
    return _git(repo_dir, "rev-parse", "HEAD").stdout.strip()


def _commit(repo_dir: Path, *paths: str, msg: str = "extra") -> None:
    _git(repo_dir, "add", *paths)
    _git(repo_dir, "-c", "user.email=test@hal.test", "-c", "user.name=HAL Test",
         "commit", "-q", "-m", msg)


def _captured_emits(monkeypatch) -> list:
    emitted: list = []

    def _fake_emit(event_type: str, payload: dict) -> None:
        emitted.append((event_type, payload))

    monkeypatch.setattr(_p6, "_emit_safe", _fake_emit)
    return emitted


def _write_review_doc(scratchpad: Path, text: str) -> str:
    reviews = scratchpad / "reviews"
    reviews.mkdir(parents=True, exist_ok=True)
    doc = reviews / "review.md"
    doc.write_text(text, encoding="utf-8")
    return str(doc)


# ═══════════════════════════════════════════════════════════════════════════
# AC1 — stray NEW file not cited in review doc: reverted (unlinked), not committed
# ═══════════════════════════════════════════════════════════════════════════


def test_gh947_ac1_stray_uncited_new_file_reverted_and_not_committed(tmp_path, monkeypatch) -> None:
    partition = getattr(_p6, "_partition_fix_surface", None)
    assert partition is not None, "_partition_fix_surface not implemented yet"

    emitted = _captured_emits(monkeypatch)
    ctx = _make_ctx(tmp_path)
    repo = Path(ctx.org_config["git_cwd"])
    pre_fix_sha = _init_git_repo(repo)

    # cited.py: NEW file, path cited in review doc.
    (repo / "cited.py").write_text("x = 1\n", encoding="utf-8")
    # stray.json: NEW file, NOT cited anywhere in review doc.
    (repo / "stray.json").write_text("{}\n", encoding="utf-8")

    review_doc = _write_review_doc(
        Path(ctx.org_config["scratchpad_dir"]), "Finding: fix cited.py per the report.\n"
    )
    prev = _make_prev(
        pre_fix_sha, cycle=1,
        worker_written_paths=["cited.py", "stray.json"],
        manifest_source="harness_tool_record",
        review_doc_path=review_doc,
    )

    result = _commit_fix_code(ctx, prev)

    assert result.status == "ok", f"expected ok; got {result.status!r} ({result.error!r})"
    assert not (repo / "stray.json").exists(), "stray.json should have been unlinked"

    fix_sha = result.data.get("fix_commit_sha")
    assert fix_sha is not None
    tree = _git(repo, "show", "--name-only", "--format=", fix_sha).stdout.splitlines()
    assert any("cited.py" in f for f in tree)
    assert not any("stray.json" in f for f in tree), f"stray.json must not be committed; tree={tree!r}"

    violations = [(et, p) for et, p in emitted if et == "fix_surface_violation"]
    assert violations, f"expected fix_surface_violation event; got {emitted!r}"
    assert violations[0][1].get("path") == "stray.json"
    assert violations[0][1].get("deleted") is True


# ═══════════════════════════════════════════════════════════════════════════
# AC2 — NEW file whose path IS cited in review text: committed, no violation
# ═══════════════════════════════════════════════════════════════════════════


def test_gh947_ac2_new_file_cited_in_review_text_committed_no_violation(tmp_path, monkeypatch) -> None:
    emitted = _captured_emits(monkeypatch)
    ctx = _make_ctx(tmp_path)
    repo = Path(ctx.org_config["git_cwd"])
    pre_fix_sha = _init_git_repo(repo)

    (repo / "newfile.py").write_text("y = 2\n", encoding="utf-8")
    review_doc = _write_review_doc(
        Path(ctx.org_config["scratchpad_dir"]), "Please add newfile.py to address the gap.\n"
    )
    prev = _make_prev(
        pre_fix_sha, cycle=1,
        worker_written_paths=["newfile.py"],
        manifest_source="harness_tool_record",
        review_doc_path=review_doc,
    )

    result = _commit_fix_code(ctx, prev)

    assert result.status == "ok", f"expected ok; got {result.status!r} ({result.error!r})"
    fix_sha = result.data.get("fix_commit_sha")
    assert fix_sha is not None, "newfile.py should have been committed"
    tree = _git(repo, "show", "--name-only", "--format=", fix_sha).stdout.splitlines()
    assert any("newfile.py" in f for f in tree)

    violations = [(et, p) for et, p in emitted if et == "fix_surface_violation"]
    assert not violations, f"expected no fix_surface_violation; got {emitted!r}"


# ═══════════════════════════════════════════════════════════════════════════
# AC3 — pre-existing tracked file, NOT cited: collateral edit allowed
# ═══════════════════════════════════════════════════════════════════════════


def test_gh947_ac3_pre_existing_tracked_uncited_collateral_edit_allowed(tmp_path, monkeypatch) -> None:
    emitted = _captured_emits(monkeypatch)
    ctx = _make_ctx(tmp_path)
    repo = Path(ctx.org_config["git_cwd"])
    pre_fix_sha = _init_git_repo(repo)

    # baseline.py already exists at pre_fix_sha (tracked); edit it, no citation.
    (repo / "baseline.py").write_text("# baseline\nx = 1\n", encoding="utf-8")
    review_doc = _write_review_doc(
        Path(ctx.org_config["scratchpad_dir"]), "Finding: unrelated topic.\n"
    )
    prev = _make_prev(
        pre_fix_sha, cycle=1,
        worker_written_paths=["baseline.py"],
        manifest_source="harness_tool_record",
        review_doc_path=review_doc,
    )

    result = _commit_fix_code(ctx, prev)

    assert result.status == "ok", f"expected ok; got {result.status!r} ({result.error!r})"
    fix_sha = result.data.get("fix_commit_sha")
    assert fix_sha is not None, "baseline.py collateral edit should have been committed"
    tree = _git(repo, "show", "--name-only", "--format=", fix_sha).stdout.splitlines()
    assert any("baseline.py" in f for f in tree)

    violations = [(et, p) for et, p in emitted if et == "fix_surface_violation"]
    assert not violations, f"expected no fix_surface_violation for collateral edit; got {emitted!r}"


# ═══════════════════════════════════════════════════════════════════════════
# AC4 — violation path IS git-tracked at HEAD (not at pre_fix_sha): dropped
#        but NOT unlinked; deleted:false
# ═══════════════════════════════════════════════════════════════════════════


def test_gh947_ac4_violation_tracked_at_head_dropped_not_unlinked(tmp_path, monkeypatch) -> None:
    emitted = _captured_emits(monkeypatch)
    ctx = _make_ctx(tmp_path)
    repo = Path(ctx.org_config["git_cwd"])
    pre_fix_sha = _init_git_repo(repo)

    # Create + commit stray.json AFTER pre_fix_sha boundary (so it's tracked at
    # HEAD but did not exist at the boundary) — simulates a prior partial commit.
    (repo / "stray.json").write_text("{}\n", encoding="utf-8")
    _commit(repo, "stray.json", msg="prior partial commit")

    (repo / "cited.py").write_text("x = 1\n", encoding="utf-8")
    review_doc = _write_review_doc(
        Path(ctx.org_config["scratchpad_dir"]), "Finding: fix cited.py.\n"
    )
    prev = _make_prev(
        pre_fix_sha, cycle=1,
        worker_written_paths=["cited.py", "stray.json"],
        manifest_source="harness_tool_record",
        review_doc_path=review_doc,
    )

    result = _commit_fix_code(ctx, prev)

    assert result.status == "ok", f"expected ok; got {result.status!r} ({result.error!r})"
    assert (repo / "stray.json").exists(), "tracked violation must NOT be unlinked"

    violations = [(et, p) for et, p in emitted if et == "fix_surface_violation"]
    assert violations, f"expected fix_surface_violation for stray.json; got {emitted!r}"
    assert violations[0][1].get("path") == "stray.json"
    assert violations[0][1].get("deleted") is False


# ═══════════════════════════════════════════════════════════════════════════
# AC5 — ALL prod paths are violations: skip commit entirely, HEAD unchanged
# ═══════════════════════════════════════════════════════════════════════════


def test_gh947_ac5_all_paths_off_surface_skips_commit_head_unchanged(tmp_path, monkeypatch) -> None:
    emitted = _captured_emits(monkeypatch)
    ctx = _make_ctx(tmp_path)
    repo = Path(ctx.org_config["git_cwd"])
    pre_fix_sha = _init_git_repo(repo)

    (repo / "stray_a.json").write_text("{}\n", encoding="utf-8")
    (repo / "stray_b.json").write_text("{}\n", encoding="utf-8")
    review_doc = _write_review_doc(
        Path(ctx.org_config["scratchpad_dir"]), "Finding: totally unrelated topic.\n"
    )
    prev = _make_prev(
        pre_fix_sha, cycle=1,
        worker_written_paths=["stray_a.json", "stray_b.json"],
        manifest_source="harness_tool_record",
        review_doc_path=review_doc,
    )

    head_before = _git(repo, "rev-parse", "HEAD").stdout.strip()
    result = _commit_fix_code(ctx, prev)
    head_after = _git(repo, "rev-parse", "HEAD").stdout.strip()

    assert result.status == "ok", f"expected ok; got {result.status!r} ({result.error!r})"
    assert result.data.get("fix_commit_sha") is None
    assert head_before == head_after, "HEAD must not change when the whole surface is skipped"

    skipped = [
        (et, p) for et, p in emitted
        if et == "fix_commit_skipped" and p.get("reason") == "all_paths_off_surface"
    ]
    assert skipped, f"expected fix_commit_skipped(reason='all_paths_off_surface'); got {emitted!r}"


# ═══════════════════════════════════════════════════════════════════════════
# AC6 — review_doc_path missing/unreadable: guard skipped (fail-open, today's
#        behavior: stray committed as-is)
# ═══════════════════════════════════════════════════════════════════════════


def test_gh947_ac6_missing_review_doc_skips_guard_fail_open(tmp_path, monkeypatch) -> None:
    emitted = _captured_emits(monkeypatch)
    ctx = _make_ctx(tmp_path)
    repo = Path(ctx.org_config["git_cwd"])
    pre_fix_sha = _init_git_repo(repo)

    (repo / "stray.json").write_text("{}\n", encoding="utf-8")
    prev = _make_prev(
        pre_fix_sha, cycle=1,
        worker_written_paths=["stray.json"],
        manifest_source="harness_tool_record",
        review_doc_path=str(Path(ctx.org_config["scratchpad_dir"]) / "reviews" / "does_not_exist.md"),
    )

    result = _commit_fix_code(ctx, prev)

    assert result.status == "ok", f"expected ok; got {result.status!r} ({result.error!r})"
    fix_sha = result.data.get("fix_commit_sha")
    assert fix_sha is not None, "guard-skipped path must commit stray.json exactly as today"
    tree = _git(repo, "show", "--name-only", "--format=", fix_sha).stdout.splitlines()
    assert any("stray.json" in f for f in tree)

    skipped = [
        (et, p) for et, p in emitted
        if et == "fix_surface_guard_skipped" and p.get("reason") == "no_review_doc"
    ]
    assert skipped, f"expected fix_surface_guard_skipped(reason='no_review_doc'); got {emitted!r}"


# ═══════════════════════════════════════════════════════════════════════════
# AC7 — git-add ignored-path failure: dropped-by-prefix, retried once, ok
# ═══════════════════════════════════════════════════════════════════════════


def test_gh947_ac7_ignored_path_dropped_by_prefix_retry_succeeds(tmp_path, monkeypatch) -> None:
    emitted = _captured_emits(monkeypatch)
    ctx = _make_ctx(tmp_path)
    repo = Path(ctx.org_config["git_cwd"])
    pre_fix_sha = _init_git_repo(repo)

    (repo / "good.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "pipeline-output").mkdir()
    (repo / "pipeline-output" / "control_posture.json").write_text("{}\n", encoding="utf-8")
    review_doc = _write_review_doc(
        Path(ctx.org_config["scratchpad_dir"]),
        "Finding: fix good.py; also pipeline-output/control_posture.json is fine.\n",
    )
    prev = _make_prev(
        pre_fix_sha, cycle=1,
        worker_written_paths=["good.py", "pipeline-output/control_posture.json"],
        manifest_source="harness_tool_record",
        review_doc_path=review_doc,
    )

    real_op = _p6._git_op_with_lock_retry
    calls: list = []
    ignored_stderr = (
        "The following paths are ignored by one of your .gitignore files:\n"
        "pipeline-output\n"
        "hint: Use -f if you really want to add them.\n"
    )

    def _scripted(cmd, cwd, timeout):
        calls.append(list(cmd))
        if cmd[:2] == ["git", "add"] and len(calls) == 1:
            return (SimpleNamespace(returncode=1, stdout="", stderr=ignored_stderr), "non_lock_error")
        return real_op(cmd, cwd=cwd, timeout=timeout)

    monkeypatch.setattr(_p6, "_git_op_with_lock_retry", _scripted)

    result = _commit_fix_code(ctx, prev)

    assert result.status == "ok", f"expected ok; got {result.status!r} ({result.error!r})"
    fix_sha = result.data.get("fix_commit_sha")
    assert fix_sha is not None
    tree = _git(repo, "show", "--name-only", "--format=", fix_sha).stdout.splitlines()
    assert any("good.py" in f for f in tree)
    assert not any("control_posture.json" in f for f in tree)

    dropped_events = [(et, p) for et, p in emitted if et == "commit_gitignored_paths_skipped"]
    assert dropped_events, f"expected commit_gitignored_paths_skipped; got {emitted!r}"
    assert "pipeline-output/control_posture.json" in dropped_events[0][1].get("paths", [])
    assert dropped_events[0][1].get("step") == "commit_fix_code"

    add_calls = [c for c in calls if c[:2] == ["git", "add"]]
    assert len(add_calls) == 2, f"expected exactly one retry (2 add attempts); got {add_calls!r}"


# ═══════════════════════════════════════════════════════════════════════════
# AC8 — ALL paths dropped as ignored: ok, sha None, all_paths_gitignored
# ═══════════════════════════════════════════════════════════════════════════


def test_gh947_ac8_all_paths_gitignored_skips_commit(tmp_path, monkeypatch) -> None:
    emitted = _captured_emits(monkeypatch)
    ctx = _make_ctx(tmp_path)
    repo = Path(ctx.org_config["git_cwd"])
    pre_fix_sha = _init_git_repo(repo)

    (repo / "pipeline-output").mkdir()
    (repo / "pipeline-output" / "control_posture.json").write_text("{}\n", encoding="utf-8")
    review_doc = _write_review_doc(
        Path(ctx.org_config["scratchpad_dir"]),
        "Finding: pipeline-output/control_posture.json needs updating.\n",
    )
    prev = _make_prev(
        pre_fix_sha, cycle=1,
        worker_written_paths=["pipeline-output/control_posture.json"],
        manifest_source="harness_tool_record",
        review_doc_path=review_doc,
    )

    ignored_stderr = (
        "The following paths are ignored by one of your .gitignore files:\n"
        "pipeline-output\n"
    )

    def _scripted(cmd, cwd, timeout):
        if cmd[:2] == ["git", "add"]:
            return (SimpleNamespace(returncode=1, stdout="", stderr=ignored_stderr), "non_lock_error")
        raise AssertionError(f"unexpected non-add call: {cmd!r}")

    monkeypatch.setattr(_p6, "_git_op_with_lock_retry", _scripted)

    head_before = _git(repo, "rev-parse", "HEAD").stdout.strip()
    result = _commit_fix_code(ctx, prev)
    head_after = _git(repo, "rev-parse", "HEAD").stdout.strip()

    assert result.status == "ok", f"expected ok; got {result.status!r} ({result.error!r})"
    assert result.data.get("fix_commit_sha") is None
    assert head_before == head_after

    skipped = [
        (et, p) for et, p in emitted
        if et == "fix_commit_skipped" and p.get("reason") == "all_paths_gitignored"
    ]
    assert skipped, f"expected fix_commit_skipped(reason='all_paths_gitignored'); got {emitted!r}"


# ═══════════════════════════════════════════════════════════════════════════
# AC9 — non_lock_error WITHOUT ignored marker: unchanged E_FIX_COMMIT_FAILED
# ═══════════════════════════════════════════════════════════════════════════


def test_gh947_ac9_non_lock_error_without_ignore_marker_unchanged(tmp_path, monkeypatch) -> None:
    _captured_emits(monkeypatch)
    ctx = _make_ctx(tmp_path)
    repo = Path(ctx.org_config["git_cwd"])
    pre_fix_sha = _init_git_repo(repo)

    (repo / "good.py").write_text("x = 1\n", encoding="utf-8")
    review_doc = _write_review_doc(
        Path(ctx.org_config["scratchpad_dir"]), "Finding: fix good.py.\n"
    )
    prev = _make_prev(
        pre_fix_sha, cycle=1,
        worker_written_paths=["good.py"],
        manifest_source="harness_tool_record",
        review_doc_path=review_doc,
    )

    calls: list = []

    def _scripted(cmd, cwd, timeout):
        calls.append(list(cmd))
        return (SimpleNamespace(returncode=1, stdout="", stderr="fatal: some unrelated error"), "non_lock_error")

    monkeypatch.setattr(_p6, "_git_op_with_lock_retry", _scripted)

    result = _commit_fix_code(ctx, prev)

    assert result.status == "error", f"expected error; got {result.status!r}"
    assert result.error_code == "E_FIX_COMMIT_FAILED"
    add_calls = [c for c in calls if c[:2] == ["git", "add"]]
    assert len(add_calls) == 1, f"non-ignored failure must NOT retry; got {add_calls!r}"


# ═══════════════════════════════════════════════════════════════════════════
# AC10 — ignored-marker retry ALSO fails non_lock_error: E_FIX_COMMIT_FAILED
# ═══════════════════════════════════════════════════════════════════════════


def test_gh947_ac10_ignored_marker_retry_also_fails(tmp_path, monkeypatch) -> None:
    _captured_emits(monkeypatch)
    ctx = _make_ctx(tmp_path)
    repo = Path(ctx.org_config["git_cwd"])
    pre_fix_sha = _init_git_repo(repo)

    (repo / "good.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "pipeline-output").mkdir()
    (repo / "pipeline-output" / "control_posture.json").write_text("{}\n", encoding="utf-8")
    review_doc = _write_review_doc(
        Path(ctx.org_config["scratchpad_dir"]),
        "Finding: fix good.py; pipeline-output/control_posture.json also referenced.\n",
    )
    prev = _make_prev(
        pre_fix_sha, cycle=1,
        worker_written_paths=["good.py", "pipeline-output/control_posture.json"],
        manifest_source="harness_tool_record",
        review_doc_path=review_doc,
    )

    ignored_stderr = (
        "The following paths are ignored by one of your .gitignore files:\n"
        "pipeline-output\n"
    )
    calls: list = []

    def _scripted(cmd, cwd, timeout):
        calls.append(list(cmd))
        if cmd[:2] == ["git", "add"]:
            return (SimpleNamespace(returncode=1, stdout="", stderr=ignored_stderr), "non_lock_error")
        raise AssertionError(f"unexpected non-add call: {cmd!r}")

    monkeypatch.setattr(_p6, "_git_op_with_lock_retry", _scripted)

    result = _commit_fix_code(ctx, prev)

    assert result.status == "error", f"expected error; got {result.status!r}"
    assert result.error_code == "E_FIX_COMMIT_FAILED"
    add_calls = [c for c in calls if c[:2] == ["git", "add"]]
    assert len(add_calls) == 2, f"expected exactly one retry attempt (2 add calls); got {add_calls!r}"


# ═══════════════════════════════════════════════════════════════════════════
# AC11 — re-entry: second call is a no-crash no-op, no duplicate commit
# ═══════════════════════════════════════════════════════════════════════════


def test_gh947_ac11_reentry_no_duplicate_commit_no_crash(tmp_path, monkeypatch) -> None:
    emitted = _captured_emits(monkeypatch)
    ctx = _make_ctx(tmp_path)
    repo = Path(ctx.org_config["git_cwd"])
    pre_fix_sha = _init_git_repo(repo)

    (repo / "cited.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "stray.json").write_text("{}\n", encoding="utf-8")
    review_doc = _write_review_doc(
        Path(ctx.org_config["scratchpad_dir"]), "Finding: fix cited.py.\n"
    )
    prev = _make_prev(
        pre_fix_sha, cycle=1,
        worker_written_paths=["cited.py", "stray.json"],
        manifest_source="harness_tool_record",
        review_doc_path=review_doc,
    )

    first = _commit_fix_code(ctx, prev)
    assert first.status == "ok", f"first call must succeed; got {first.status!r} ({first.error!r})"
    head_after_first = _git(repo, "rev-parse", "HEAD").stdout.strip()

    emitted.clear()
    # Re-entry: same ctx/prev (stray already unlinked, commit already made).
    second = _commit_fix_code(ctx, prev)

    assert second.status == "ok", f"re-entry must not crash; got {second.status!r} ({second.error!r})"
    head_after_second = _git(repo, "rev-parse", "HEAD").stdout.strip()
    assert head_after_first == head_after_second, "re-entry must not create a second commit"

    violations = [(et, p) for et, p in emitted if et == "fix_surface_violation"]
    if violations:
        assert violations[0][1].get("deleted") is False, (
            "re-emitted violation on already-unlinked path must report deleted:false"
        )


# ═══════════════════════════════════════════════════════════════════════════
# AC12 — _partition_fix_surface with git_cwd not a repo: fail-open, no raise
# ═══════════════════════════════════════════════════════════════════════════


def test_gh947_ac12_partition_fix_surface_non_repo_fails_open(tmp_path) -> None:
    partition = getattr(_p6, "_partition_fix_surface", None)
    assert partition is not None, "_partition_fix_surface not implemented yet"

    non_repo = tmp_path / "not_a_repo"
    non_repo.mkdir()

    allowed, violations = partition(
        ["a.py", "b.json"],
        "a" * 40,
        "irrelevant review text",
        str(non_repo),
    )

    assert set(allowed) == {"a.py", "b.json"}, (
        f"expected fail-open: all paths allowed when git_cwd is not a repo; got {allowed!r}"
    )
    assert violations == [], f"expected no violations on repo-level failure; got {violations!r}"


# ═══════════════════════════════════════════════════════════════════════════
# §4 E2E smoke — incident repro (ppba#1274 sdk twin)
# ═══════════════════════════════════════════════════════════════════════════


def test_gh947_e2e_incident_repro_stray_reverted_and_ignored_path_skipped(tmp_path, monkeypatch) -> None:
    """Reproduces the exact incident: worker edits a cited test path (excluded
    from prod_paths by _is_test_path — untouched by the guard), writes an
    uncited root-level stray file, and the manifest also lists an ignored
    pipeline-output path. Expect: step ok, root stray reverted, no commit of
    the stray, ignored path dropped+retried, repo tree clean of both."""
    emitted = _captured_emits(monkeypatch)
    ctx = _make_ctx(tmp_path)
    repo = Path(ctx.org_config["git_cwd"])
    pre_fix_sha = _init_git_repo(repo)

    (repo / ".gitignore").write_text("pipeline-output\n", encoding="utf-8")
    _commit(repo, ".gitignore", msg="add gitignore")

    (repo / "tests").mkdir()
    (repo / "tests" / "test_x.py").write_text("def test_x(): pass\n", encoding="utf-8")
    _commit(repo, "tests/test_x.py", msg="add tracked test")

    # Worker's actual edit: cosmetic tweak to the cited test file.
    (repo / "tests" / "test_x.py").write_text("def test_x():\n    pass\n", encoding="utf-8")
    # Worker's stray write: root-level file NOT cited anywhere.
    (repo / "control_posture.json").write_text("{}\n", encoding="utf-8")
    (repo / "pipeline-output").mkdir(exist_ok=True)
    (repo / "pipeline-output" / "control_posture.json").write_text("{}\n", encoding="utf-8")

    review_doc = _write_review_doc(
        Path(ctx.org_config["scratchpad_dir"]),
        "Findings: adjust tests/test_x.py formatting only.\n",
    )
    prev = _make_prev(
        pre_fix_sha, cycle=1,
        worker_written_paths=[
            "tests/test_x.py",
            "control_posture.json",
            "pipeline-output/control_posture.json",
        ],
        manifest_source="harness_tool_record",
        review_doc_path=review_doc,
    )

    result = _commit_fix_code(ctx, prev)

    assert result.status == "ok", f"expected ok (incident must not hard-fail); got {result.status!r} ({result.error!r})"
    assert not (repo / "control_posture.json").exists(), "root stray must be reverted"

    porcelain = _git(repo, "status", "--porcelain").stdout
    assert "control_posture.json" not in porcelain.split("pipeline-output")[0], (
        f"root stray must not linger dirty either: {porcelain!r}"
    )

    violation_events = [p for et, p in emitted if et == "fix_surface_violation"]
    assert any(v.get("path") == "control_posture.json" for v in violation_events), (
        f"expected fix_surface_violation for root stray; got {emitted!r}"
    )
