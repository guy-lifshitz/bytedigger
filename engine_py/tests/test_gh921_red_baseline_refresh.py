"""RED tests for GH921 — sanctioned RED-baseline refresh + early check + classified payload.

Spec: GH921 red-baseline-refresh spec (host repo Decisions memory).
ACs 1-11 (spec §3). AC12 is a sibling-suite invariant (run
test_7C0FDE44_gh639_red_hash_freeze.py in scoped verify) — not tested here,
and that file is READ-ONLY (not touched by this ship).

Conventions (§1q / D1CF5FDF collectability guard): ``phase_5_implement`` and
``lib.authored_boundary`` already exist today, so they are imported at module
top level (collectable, mirrors test_gh639's import header exactly — no
sys.path mutation, no spec_from_file_location/exec_module). The NEW symbols
this ship adds — ``authored_boundary.classify_red_hash_mismatches``,
``phase_5_implement._red_baseline_precheck``,
``phase_5_implement._refreeze_red_test_hashes`` — do NOT exist yet, so each is
probed via ``getattr(...)`` INSIDE the test body via ``_require``. The file
COLLECTS and each test FAILS at assert time, never at collection time.

Stub-passability (§1l/7AD3D393): UUT symbols are called directly (never
patched/mocked). Only ``phase_5_implement._emit_safe`` is monkeypatched for
event-capture, and env var HAL_RED_BASELINE_REFRESH via monkeypatch.setenv
(matching the sibling test_gh639 pattern).
"""
from __future__ import annotations

import json
import subprocess
import time
from pathlib import Path

import phase_5_implement  # noqa: E402
import lib.authored_boundary as boundary_mod  # noqa: E402
from contracts import StepResult, WorkflowContext  # noqa: E402


# ─── shared helpers (mirrors test_7C0FDE44_gh639_red_hash_freeze.py) ──────────


def _init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)


def _commit_file(repo: Path, relpath: str, body: str, msg: str) -> str:
    p = repo / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)
    subprocess.run(["git", "add", relpath], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", msg], cwd=repo, check=True)
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=repo, check=True
    )
    return result.stdout.strip()


def _write_file(repo: Path, relpath: str, body: str) -> None:
    p = repo / relpath
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body)


def _make_green_ctx(scratchpad: Path, git_cwd: str) -> WorkflowContext:
    org = {"scratchpad_dir": str(scratchpad), "git_cwd": git_cwd}
    return WorkflowContext(
        tenant_id="hal", scope=None, db_path=None, org_config=org,
        question="Fix the thing", session_id="gh921-green", persona="hal",
        framework=None, domain=None,
    )


def _make_prev(spec_path: str | None, red_log_path: str, validation_doc_path: str) -> StepResult:
    return StepResult(
        status="ok",
        data={
            "spec_path": spec_path,
            "red_log_path": red_log_path,
            "validation_doc_path": validation_doc_path,
            "red_test_paths": ["tests/test_x.py"],
        },
        duration_ms=0,
        step_name="prev",
    )


def _require(mod, name: str):
    """Presence-gate for a not-yet-existing symbol (D1CF5FDF collectability)."""
    fn = getattr(mod, name, None)
    assert fn is not None, (
        f"{mod.__name__}.{name} does not exist yet (GH921 not implemented) — expected RED fail"
    )
    return fn


def _stub_scratch_files(scratchpad: Path) -> tuple[str, str, str]:
    """Create the red_log / validation_doc / spec files _build_green_prompt reads."""
    red_log = scratchpad / "red-log.md"
    red_log.write_text("RED report\n")
    validation_doc = scratchpad / "validation.md"
    validation_doc.write_text("VERDICT: APPROVED\n")
    return str(red_log), str(validation_doc)


def _make_minimal_spec(scratchpad: Path) -> str:
    """A real minimal spec file (no authorized-test-edits block) — spec_path
    is Path()'d unconditionally at L5154 of _build_green_prompt, so a fixture
    expecting the ok path must supply a real file, never None."""
    spec_path = scratchpad / "minimal-spec.md"
    spec_path.write_text("## Files\n\n- MODIFY `src/module.py`\n")
    return str(spec_path)


# ═══════════════════════════════════════════════════════════════════════════
# TestGH921ClassifyRedHashMismatches (AC1-AC3)
# ═══════════════════════════════════════════════════════════════════════════


class TestGH921ClassifyRedHashMismatches:
    def test_ac1_head_moved_classification(self, tmp_path: Path) -> None:
        classify_fn = _require(boundary_mod, "classify_red_hash_mismatches")
        repo = (tmp_path / "repo").resolve()
        _init_repo(repo)
        _commit_file(repo, "tests/test_x.py", "def test_x(): assert True\n", "init")
        # Committed content differs from the stale frozen digest.
        frozen = {"tests/test_x.py": "old-stale-digest"}
        result = classify_fn(frozen, str(repo))
        assert result == {"tests/test_x.py": "head_moved"}

    def test_ac2_worktree_dirty_classification_both_variants(self, tmp_path: Path) -> None:
        classify_fn = _require(boundary_mod, "classify_red_hash_mismatches")
        compute_fn = boundary_mod.compute_red_test_hashes
        repo = (tmp_path / "repo").resolve()
        _init_repo(repo)
        _commit_file(repo, "tests/test_x.py", "def test_x(): assert True\n", "init")
        head_digest = compute_fn(["tests/test_x.py"], str(repo))["tests/test_x.py"]

        # Variant A: worktree != HEAD == frozen (edited after freeze-at-commit).
        frozen_a = {"tests/test_x.py": head_digest}
        _write_file(repo, "tests/test_x.py", "def test_x(): assert False  # dirty\n")
        result_a = classify_fn(frozen_a, str(repo))
        assert result_a == {"tests/test_x.py": "worktree_dirty"}

        # Variant B: worktree != HEAD != frozen (all three distinct).
        frozen_b = {"tests/test_x.py": "some-other-stale-digest"}
        result_b = classify_fn(frozen_b, str(repo))
        assert result_b == {"tests/test_x.py": "worktree_dirty"}

    def test_ac3_missing_and_head_unreadable_classification(self, tmp_path: Path) -> None:
        classify_fn = _require(boundary_mod, "classify_red_hash_mismatches")
        repo = (tmp_path / "repo").resolve()
        _init_repo(repo)
        _commit_file(repo, "README.md", "init\n", "init")

        # missing: file was frozen but no longer on disk.
        frozen_missing = {"tests/gone.py": "some-digest"}
        result_missing = classify_fn(frozen_missing, str(repo))
        assert result_missing == {"tests/gone.py": "missing"}

        # head_unreadable: file exists on disk but was never committed to HEAD.
        _write_file(repo, "tests/never_committed.py", "def test_y(): assert True\n")
        frozen_unreadable = {"tests/never_committed.py": "some-digest"}
        result_unreadable = classify_fn(frozen_unreadable, str(repo))
        assert result_unreadable == {"tests/never_committed.py": "head_unreadable"}


# ═══════════════════════════════════════════════════════════════════════════
# TestGH921RedBaselinePrecheck (AC4-AC8, AC11) — via _build_green_prompt
# ═══════════════════════════════════════════════════════════════════════════


class TestGH921RedBaselinePrecheck:
    def _setup_head_moved_repo(self, tmp_path: Path):
        """Commit a NEW version of tests/test_x.py at HEAD (operator-committed
        RED strengthening), freeze a STALE digest in the manifest — worktree
        == HEAD, both != frozen. Classic head_moved fixture."""
        repo = (tmp_path / "repo").resolve()
        scratchpad = tmp_path / "scratch"
        _init_repo(repo)
        _commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
        _commit_file(
            repo, "tests/test_x.py",
            "def test_x(): assert True  # strengthened cycle 4\n",
            "RED: strengthen test_x",
        )
        scratchpad.mkdir()
        stale_manifest = {"tests/test_x.py": "stale-digest-from-cycle-3"}
        persist_fn = phase_5_implement._persist_red_test_hashes
        persist_fn(scratchpad, stale_manifest)
        return repo, scratchpad

    def test_ac4_flag_on_head_moved_refreshes_manifest_and_emits_once(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        precheck_fn = _require(phase_5_implement, "_red_baseline_precheck")
        repo, scratchpad = self._setup_head_moved_repo(tmp_path)
        monkeypatch.setenv("HAL_RED_BASELINE_REFRESH", "1")

        captured: list[dict] = []
        monkeypatch.setattr(
            phase_5_implement, "_emit_safe",
            lambda et, p, severity="info": captured.append({"type": et, "payload": p}),
        )

        red_log, validation_doc = _stub_scratch_files(scratchpad)
        spec_path = _make_minimal_spec(scratchpad)
        ctx = _make_green_ctx(scratchpad, str(repo))
        prev = _make_prev(spec_path=spec_path, red_log_path=red_log, validation_doc_path=validation_doc)

        result = phase_5_implement._build_green_prompt(ctx, prev)
        assert result.status == "ok", f"expected ok, got {result.status!r}: {getattr(result, 'error', '')}"

        relpath = getattr(phase_5_implement, "RED_TEST_HASHES_RELPATH", "integrity/red-test-hashes.json")
        manifest = json.loads((scratchpad / relpath).read_text())
        current_digest = boundary_mod.compute_red_test_hashes(["tests/test_x.py"], str(repo))["tests/test_x.py"]
        assert manifest["tests/test_x.py"] == current_digest, "manifest must hold the NEW digest after refresh"

        refresh_events = [e for e in captured if e["type"] == "red_baseline_refreshed"]
        assert len(refresh_events) == 1, f"expected exactly 1 red_baseline_refreshed, got {captured!r}"

    def test_ac5_flag_off_head_moved_errors_and_manifest_unchanged(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        precheck_fn = _require(phase_5_implement, "_red_baseline_precheck")
        repo, scratchpad = self._setup_head_moved_repo(tmp_path)
        monkeypatch.delenv("HAL_RED_BASELINE_REFRESH", raising=False)

        relpath = getattr(phase_5_implement, "RED_TEST_HASHES_RELPATH", "integrity/red-test-hashes.json")
        manifest_path = scratchpad / relpath
        before_bytes = manifest_path.read_bytes()

        red_log, validation_doc = _stub_scratch_files(scratchpad)
        spec_path = _make_minimal_spec(scratchpad)
        ctx = _make_green_ctx(scratchpad, str(repo))
        prev = _make_prev(spec_path=spec_path, red_log_path=red_log, validation_doc_path=validation_doc)

        result = phase_5_implement._build_green_prompt(ctx, prev)
        assert result.status == "error"
        assert result.error_code == "E_RED_TESTS_TAMPERED"
        assert result.step_name == "build_green_prompt"
        assert result.recoverable is False
        assert "head_moved" in result.error
        assert "HAL_RED_BASELINE_REFRESH=1" in result.error
        assert manifest_path.read_bytes() == before_bytes, "manifest must NOT be rewritten on the error path"

    def test_ac6_flag_on_worktree_dirty_still_errors(self, tmp_path: Path, monkeypatch) -> None:
        precheck_fn = _require(phase_5_implement, "_red_baseline_precheck")
        repo = (tmp_path / "repo").resolve()
        scratchpad = tmp_path / "scratch"
        _init_repo(repo)
        _commit_file(repo, "tests/test_x.py", "def test_x(): assert True\n", "init")
        head_digest = boundary_mod.compute_red_test_hashes(["tests/test_x.py"], str(repo))["tests/test_x.py"]
        scratchpad.mkdir()
        phase_5_implement._persist_red_test_hashes(scratchpad, {"tests/test_x.py": head_digest})
        # Uncommitted worktree edit AFTER the freeze == real tamper.
        _write_file(repo, "tests/test_x.py", "def test_x(): assert False  # dirty edit\n")

        monkeypatch.setenv("HAL_RED_BASELINE_REFRESH", "1")
        captured: list[dict] = []
        monkeypatch.setattr(
            phase_5_implement, "_emit_safe",
            lambda et, p, severity="info": captured.append({"type": et, "payload": p}),
        )

        relpath = getattr(phase_5_implement, "RED_TEST_HASHES_RELPATH", "integrity/red-test-hashes.json")
        manifest_path = scratchpad / relpath
        before_bytes = manifest_path.read_bytes()

        red_log, validation_doc = _stub_scratch_files(scratchpad)
        spec_path = _make_minimal_spec(scratchpad)
        ctx = _make_green_ctx(scratchpad, str(repo))
        prev = _make_prev(spec_path=spec_path, red_log_path=red_log, validation_doc_path=validation_doc)

        result = phase_5_implement._build_green_prompt(ctx, prev)
        assert result.status == "error", "the refresh flag must NOT bless a dirty worktree"
        assert result.error_code == "E_RED_TESTS_TAMPERED"
        assert "worktree_dirty" in result.error
        assert manifest_path.read_bytes() == before_bytes
        refresh_events = [e for e in captured if e["type"] == "red_baseline_refreshed"]
        assert len(refresh_events) == 0

    def test_ac6_mixed_head_moved_and_worktree_dirty_suppresses_refresh_hint(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Amended AC6: one head_moved path + one worktree_dirty path, flag=1.
        Refresh must NOT fire (not ALL mismatches are head_moved) and the
        HAL_RED_BASELINE_REFRESH=1 hint must be suppressed entirely."""
        precheck_fn = _require(phase_5_implement, "_red_baseline_precheck")
        repo = (tmp_path / "repo").resolve()
        scratchpad = tmp_path / "scratch"
        _init_repo(repo)
        _commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
        # path A: operator committed a strengthened version at HEAD (head_moved).
        _commit_file(
            repo, "tests/test_a.py",
            "def test_a(): assert True  # strengthened\n",
            "RED: strengthen test_a",
        )
        # path B: committed once, then dirtied in the worktree (worktree_dirty).
        _commit_file(repo, "tests/test_b.py", "def test_b(): assert True\n", "RED: add test_b")
        head_digest_b = boundary_mod.compute_red_test_hashes(["tests/test_b.py"], str(repo))["tests/test_b.py"]
        _write_file(repo, "tests/test_b.py", "def test_b(): assert False  # dirty edit\n")

        scratchpad.mkdir()
        stale_manifest = {
            "tests/test_a.py": "stale-digest-from-cycle-3",
            "tests/test_b.py": head_digest_b,
        }
        phase_5_implement._persist_red_test_hashes(scratchpad, stale_manifest)
        relpath = getattr(phase_5_implement, "RED_TEST_HASHES_RELPATH", "integrity/red-test-hashes.json")
        manifest_path = scratchpad / relpath
        before_bytes = manifest_path.read_bytes()

        monkeypatch.setenv("HAL_RED_BASELINE_REFRESH", "1")
        captured: list[dict] = []
        monkeypatch.setattr(
            phase_5_implement, "_emit_safe",
            lambda et, p, severity="info": captured.append({"type": et, "payload": p}),
        )

        red_log, validation_doc = _stub_scratch_files(scratchpad)
        spec_path = _make_minimal_spec(scratchpad)
        ctx = _make_green_ctx(scratchpad, str(repo))
        prev = _make_prev(spec_path=spec_path, red_log_path=red_log, validation_doc_path=validation_doc)

        result = phase_5_implement._build_green_prompt(ctx, prev)
        assert result.status == "error", "mixed classification must NOT be blessed by the refresh flag"
        assert result.error_code == "E_RED_TESTS_TAMPERED"
        assert "head_moved" in result.error
        assert "worktree_dirty" in result.error
        assert "HAL_RED_BASELINE_REFRESH=1" not in result.error, (
            "the refresh hint is suppressed unless ALL mismatches are head_moved"
        )
        assert manifest_path.read_bytes() == before_bytes
        refresh_events = [e for e in captured if e["type"] == "red_baseline_refreshed"]
        assert len(refresh_events) == 0

    def test_ac7_no_manifest_is_fresh_run_noop(self, tmp_path: Path, monkeypatch) -> None:
        precheck_fn = _require(phase_5_implement, "_red_baseline_precheck")
        repo = (tmp_path / "repo").resolve()
        scratchpad = tmp_path / "scratch"
        _init_repo(repo)
        _commit_file(repo, "tests/test_x.py", "def test_x(): assert True\n", "init")
        scratchpad.mkdir()
        relpath = getattr(phase_5_implement, "RED_TEST_HASHES_RELPATH", "integrity/red-test-hashes.json")
        assert not (scratchpad / relpath).exists()

        captured: list[dict] = []
        monkeypatch.setattr(
            phase_5_implement, "_emit_safe",
            lambda et, p, severity="info": captured.append({"type": et, "payload": p}),
        )

        red_log, validation_doc = _stub_scratch_files(scratchpad)
        spec_path = _make_minimal_spec(scratchpad)
        ctx = _make_green_ctx(scratchpad, str(repo))
        prev = _make_prev(spec_path=spec_path, red_log_path=red_log, validation_doc_path=validation_doc)

        result = phase_5_implement._build_green_prompt(ctx, prev)
        assert result.status == "ok", f"fresh scratchpad must not block prompt build, got {result.status!r}"
        refresh_events = [e for e in captured if e["type"] == "red_baseline_refreshed"]
        assert len(refresh_events) == 0

    def test_ac8_second_call_after_refresh_is_idempotent_single_emit(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        _require(phase_5_implement, "_red_baseline_precheck")
        repo, scratchpad = self._setup_head_moved_repo(tmp_path)
        monkeypatch.setenv("HAL_RED_BASELINE_REFRESH", "1")

        captured: list[dict] = []
        monkeypatch.setattr(
            phase_5_implement, "_emit_safe",
            lambda et, p, severity="info": captured.append({"type": et, "payload": p}),
        )

        red_log, validation_doc = _stub_scratch_files(scratchpad)
        spec_path = _make_minimal_spec(scratchpad)
        ctx = _make_green_ctx(scratchpad, str(repo))
        prev = _make_prev(spec_path=spec_path, red_log_path=red_log, validation_doc_path=validation_doc)

        first = phase_5_implement._build_green_prompt(ctx, prev)
        assert first.status == "ok"
        second = phase_5_implement._build_green_prompt(ctx, prev)
        assert second.status == "ok", f"re-entry must stay ok, got {second.status!r}: {getattr(second, 'error', '')}"

        refresh_events = [e for e in captured if e["type"] == "red_baseline_refreshed"]
        assert len(refresh_events) == 1, (
            f"total red_baseline_refreshed across both calls must be exactly 1 "
            f"(re-entry idempotency, §1ab-c/d), got {len(refresh_events)}: {captured!r}"
        )

    def test_ac11_authorized_test_edit_path_is_excluded_from_precheck(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        _require(phase_5_implement, "_red_baseline_precheck")
        repo, scratchpad = self._setup_head_moved_repo(tmp_path)
        monkeypatch.delenv("HAL_RED_BASELINE_REFRESH", raising=False)

        spec_path = scratchpad / "spec.md"
        spec_path.write_text(
            "## Files\n\n- MODIFY `src/module.py`\n\n"
            "authorized-test-edits:\n"
            "- tests/test_x.py — intentional strengthening\n"
        )

        captured: list[dict] = []
        monkeypatch.setattr(
            phase_5_implement, "_emit_safe",
            lambda et, p, severity="info": captured.append({"type": et, "payload": p}),
        )

        red_log, validation_doc = _stub_scratch_files(scratchpad)
        ctx = _make_green_ctx(scratchpad, str(repo))
        prev = _make_prev(spec_path=str(spec_path), red_log_path=red_log, validation_doc_path=validation_doc)

        result = phase_5_implement._build_green_prompt(ctx, prev)
        assert result.status == "ok", (
            f"authorized path must be excluded from tamper classification, "
            f"got {result.status!r}: {getattr(result, 'error', '')}"
        )
        refresh_events = [e for e in captured if e["type"] == "red_baseline_refreshed"]
        assert len(refresh_events) == 0, "no refresh should fire for an authorized-edit path"


# ═══════════════════════════════════════════════════════════════════════════
# TestGH921RefreezeRedTestHashes (AC9) — via _commit_red_tests re-entry
# ═══════════════════════════════════════════════════════════════════════════


class TestGH921RefreezeRedTestHashes:
    def test_ac9_changed_content_reentry_overwrites_and_emits_refrozen(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        _require(phase_5_implement, "_refreeze_red_test_hashes")
        repo = (tmp_path / "repo").resolve()
        scratchpad = tmp_path / "scratch"
        _init_repo(repo)
        _commit_file(repo, "README.md", "init\n", "init")
        sha_0 = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
        ).stdout.strip()
        ref_path = scratchpad / "integrity" / "pre-red-ref.txt"
        ref_path.parent.mkdir(parents=True, exist_ok=True)
        ref_path.write_text(sha_0)

        test_file = repo / "tests" / "test_foo.py"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.write_text("def test_fail(): assert False\n")
        subprocess.run(["git", "add", "tests/test_foo.py"], cwd=repo, check=True)

        captured: list[dict] = []
        monkeypatch.setattr(
            phase_5_implement, "_emit_safe",
            lambda et, p, severity="info": captured.append({"type": et, "payload": p}),
        )

        org = {"scratchpad_dir": str(scratchpad), "git_cwd": str(repo)}
        ctx = WorkflowContext(
            tenant_id="hal", scope=None, db_path=None, org_config=org,
            question="Write RED tests", session_id="gh921-red", persona="hal",
            framework=None, domain=None,
        )
        prev = StepResult(
            status="ok", data={"cycle": 1, "spec_path": None, "red_test_paths": ["tests/test_foo.py"]},
            duration_ms=0, step_name="prev",
        )

        # First entry: fresh manifest freeze (baseline behavior, unchanged).
        result1 = phase_5_implement._commit_red_tests(ctx, prev)
        assert result1.status == "ok"

        relpath = getattr(phase_5_implement, "RED_TEST_HASHES_RELPATH", "integrity/red-test-hashes.json")
        manifest_path = scratchpad / relpath
        manifest_after_first = json.loads(manifest_path.read_text())

        # Simulate engine-cycle re-entry with CHANGED committed content: amend
        # the RED commit with different test content, then re-run the same
        # commit_red_tests step (as an engine-cycle re-entry would).
        test_file.write_text("def test_fail(): assert False  # cycle 2 strengthened\n")
        subprocess.run(["git", "add", "tests/test_foo.py"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "--amend", "-q", "-m", "RED cycle 2"], cwd=repo, check=True)

        captured.clear()
        result2 = phase_5_implement._commit_red_tests(ctx, prev)
        assert result2.status == "ok"

        manifest_after_second = json.loads(manifest_path.read_text())
        assert manifest_after_second["tests/test_foo.py"] != manifest_after_first["tests/test_foo.py"], (
            "changed-content re-entry must overwrite the manifest with the new digest"
        )
        refrozen_events = [e for e in captured if e["type"] == "red_test_hashes_refrozen"]
        assert len(refrozen_events) == 1, f"expected exactly 1 red_test_hashes_refrozen, got {captured!r}"

    def test_ac9_same_content_reentry_is_noop_no_write_no_emit(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        _require(phase_5_implement, "_refreeze_red_test_hashes")
        repo = (tmp_path / "repo").resolve()
        scratchpad = tmp_path / "scratch"
        _init_repo(repo)
        _commit_file(repo, "README.md", "init\n", "init")
        sha_0 = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
        ).stdout.strip()
        ref_path = scratchpad / "integrity" / "pre-red-ref.txt"
        ref_path.parent.mkdir(parents=True, exist_ok=True)
        ref_path.write_text(sha_0)

        test_file = repo / "tests" / "test_foo.py"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.write_text("def test_fail(): assert False\n")
        subprocess.run(["git", "add", "tests/test_foo.py"], cwd=repo, check=True)

        monkeypatch.setattr(
            phase_5_implement, "_emit_safe",
            lambda et, p, severity="info": None,
        )

        org = {"scratchpad_dir": str(scratchpad), "git_cwd": str(repo)}
        ctx = WorkflowContext(
            tenant_id="hal", scope=None, db_path=None, org_config=org,
            question="Write RED tests", session_id="gh921-red", persona="hal",
            framework=None, domain=None,
        )
        prev = StepResult(
            status="ok", data={"cycle": 1, "spec_path": None, "red_test_paths": ["tests/test_foo.py"]},
            duration_ms=0, step_name="prev",
        )

        result1 = phase_5_implement._commit_red_tests(ctx, prev)
        assert result1.status == "ok"

        relpath = getattr(phase_5_implement, "RED_TEST_HASHES_RELPATH", "integrity/red-test-hashes.json")
        manifest_path = scratchpad / relpath
        bytes_after_first = manifest_path.read_bytes()
        mtime_after_first = manifest_path.stat().st_mtime_ns
        time.sleep(0.01)  # ensure a subsequent write (if any) would bump mtime

        captured: list[dict] = []
        monkeypatch.setattr(
            phase_5_implement, "_emit_safe",
            lambda et, p, severity="info": captured.append({"type": et, "payload": p}),
        )

        # Re-entry with UNCHANGED content — nothing committed differently.
        result2 = phase_5_implement._commit_red_tests(ctx, prev)
        assert result2.status == "ok"

        bytes_after_second = manifest_path.read_bytes()
        mtime_after_second = manifest_path.stat().st_mtime_ns
        assert bytes_after_second == bytes_after_first, "same-content re-entry must not rewrite the manifest"
        assert mtime_after_second == mtime_after_first, "same-content re-entry must not touch the manifest file (no write at all)"
        refrozen_events = [e for e in captured if e["type"] == "red_test_hashes_refrozen"]
        assert len(refrozen_events) == 0, f"same-content re-entry must emit 0 refrozen events, got {captured!r}"


# ═══════════════════════════════════════════════════════════════════════════
# TestGH921CommitGreenCodeClassifiedPayload (AC10)
# ═══════════════════════════════════════════════════════════════════════════


class TestGH921CommitGreenCodeClassifiedPayload:
    def test_ac10_frozen_hash_tamper_error_contains_classification_token(
        self, tmp_path: Path
    ) -> None:
        compute_fn = boundary_mod.compute_red_test_hashes
        persist_fn = phase_5_implement._persist_red_test_hashes

        repo = (tmp_path / "repo").resolve()
        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()
        _init_repo(repo)
        _commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
        _commit_file(repo, ".gitignore", "tests/\n", "add gitignore")

        # tests/test_x.py is gitignored and NEVER committed to HEAD — this is
        # the git-diff-blind case. `git show HEAD:tests/test_x.py` returns
        # rc!=0, so the correct classification is "head_unreadable" (fail-CLOSED
        # treated as tamper), NOT "worktree_dirty" (amended AC10).
        _write_file(repo, "tests/test_x.py", "def test_x(): assert True\n")
        red_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
        ).stdout.strip()

        manifest = compute_fn(["tests/test_x.py"], str(repo))
        persist_fn(scratchpad, manifest)

        # Tamper the gitignored, git-diff-blind RED test.
        _write_file(repo, "tests/test_x.py", "def test_x(): assert False  # tampered\n")

        spec_path = scratchpad / "spec.md"
        spec_path.write_text("## Files\n\n- MODIFY `src/module.py`\n")
        _write_file(repo, "src/module.py", "def bar(): return 2\n")

        ctx = _make_green_ctx(scratchpad, str(repo))
        prev = StepResult(
            status="ok",
            data={
                "cycle": 1, "red_commit_sha": red_sha,
                "worker_written_paths": ["src/module.py"], "manifest_source": "harness_tool_record",
                "spec_path": str(spec_path),
            },
            duration_ms=0, step_name="prev",
        )

        result = phase_5_implement._commit_green_code(ctx, prev)
        assert result.error_code == "E_RED_TESTS_TAMPERED"
        assert "head_unreadable" in result.error, (
            f"error string must carry the classification token for the tampered path, "
            f"got: {result.error!r}"
        )
