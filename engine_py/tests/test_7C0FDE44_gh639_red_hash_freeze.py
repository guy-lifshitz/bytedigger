"""RED tests for GH639 (7C0FDE44) — frozen-hash manifest for committed RED tests.

Spec: SHARED/memory/Decisions/2026-07-12_7C0FDE44_gh639_red_hash_freeze_spec.md
ACs 1-12 (spec §3).

Conventions (§1q / D1CF5FDF collectability guard, 81F97F3D no module-level
sys.path mutation): ``lib.authored_boundary``, ``lib.run_allowlist``, and
``workflows.phase_5_implement`` (imported here as ``phase_5_implement`` —
conftest.py's import-time singleton already puts the engine_py root +
``workflows`` on sys.path) all exist TODAY, so they are imported at module
top level (collectable). The NEW symbols this ship adds —
``authored_boundary.compute_red_test_hashes``, ``authored_boundary.verify_red_test_hashes``,
``run_allowlist.parse_authorized_test_edits_with_reasons``,
``phase_5_implement.RED_TEST_HASHES_RELPATH``,
``phase_5_implement._persist_red_test_hashes``,
``phase_5_implement._read_red_test_hashes`` — do NOT exist yet, so each is
probed via ``getattr(...)`` INSIDE the test body. The file COLLECTS and each
test FAILS at assert time, never at collection time.

Stub-passability (§1l/7AD3D393): UUT symbols are called directly (never
patched/mocked). Only ``phase_5_implement._emit_safe`` is monkeypatched, for
event-capture (matching the sibling test_gh373/test_gh436 pattern).

AC10/AC12 forcing function (coordinator refinement): the pre-existing
``git diff --name-only`` tamper leg in ``authored_boundary.scan_boundary``
already rejects an edited *tracked* committed RED test — so a naive AC10
fixture using a normally-tracked test would already PASS today (vacuous).
AC10/AC12 instead use a **git-diff-BLIND** fixture: the RED test path is
placed under a ``.gitignore``d directory, so ``git diff --name-only`` cannot
see it change; only the NEW frozen-hash manifest leg can detect the tamper.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

import phase_5_implement  # noqa: E402
import lib.authored_boundary as boundary_mod  # noqa: E402
import lib.run_allowlist as run_allowlist_mod  # noqa: E402
from contracts import StepResult, WorkflowContext  # noqa: E402


# ─── shared helpers (copied minimal skeleton from test_gh373/test_gh436) ──────


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


def _rev_count(repo: Path) -> int:
    r = subprocess.run(
        ["git", "rev-list", "--count", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    )
    return int(r.stdout.strip())


def _make_green_ctx(scratchpad: Path, git_cwd: str) -> WorkflowContext:
    org = {"scratchpad_dir": str(scratchpad), "git_cwd": git_cwd}
    return WorkflowContext(
        tenant_id="hal", scope=None, db_path=None, org_config=org,
        question="Fix the thing", session_id="gh639-green", persona="hal",
        framework=None, domain=None,
    )


def _make_red_ctx(scratchpad: Path, git_cwd: str) -> WorkflowContext:
    org = {"scratchpad_dir": str(scratchpad), "git_cwd": git_cwd}
    return WorkflowContext(
        tenant_id="hal", scope=None, db_path=None, org_config=org,
        question="Write RED tests", session_id="gh639-red", persona="hal",
        framework=None, domain=None,
    )


def _make_prev(cycle: int = 1, **extra) -> StepResult:
    return StepResult(
        status="ok",
        data={"cycle": cycle, "spec_path": None, **extra},
        duration_ms=0,
        step_name="prev",
    )


def _require(mod, name: str):
    """Presence-gate for a not-yet-existing symbol (D1CF5FDF collectability)."""
    fn = getattr(mod, name, None)
    assert fn is not None, (
        f"{mod.__name__}.{name} does not exist yet (GH639 not implemented) — expected RED fail"
    )
    return fn


# ═══════════════════════════════════════════════════════════════════════════════
# TestGH639RedHashFreeze
# ═══════════════════════════════════════════════════════════════════════════════


class TestGH639RedHashFreeze:
    """GH639 — frozen-hash manifest for committed RED tests (ACs 1-12)."""

    # ── AC1: compute_red_test_hashes SHA value ─────────────────────────────

    def test_ac1_compute_red_test_hashes_produces_sha256_hexdigest(self, tmp_path: Path) -> None:
        fn = _require(boundary_mod, "compute_red_test_hashes")
        (tmp_path / "a.py").write_bytes(b"x")
        result = fn(["a.py"], str(tmp_path))
        assert result["a.py"] == hashlib.sha256(b"x").hexdigest()

    # ── AC2: missing file sentinel ──────────────────────────────────────────

    def test_ac2_compute_red_test_hashes_missing_file_sentinel(self, tmp_path: Path) -> None:
        fn = _require(boundary_mod, "compute_red_test_hashes")
        result = fn(["gone.py"], str(tmp_path))
        assert result["gone.py"] == "__MISSING__"

    # ── AC3: unchanged file -> clean verify ────────────────────────────────

    def test_ac3_verify_red_test_hashes_unchanged_is_clean(self, tmp_path: Path) -> None:
        compute_fn = _require(boundary_mod, "compute_red_test_hashes")
        verify_fn = _require(boundary_mod, "verify_red_test_hashes")
        (tmp_path / "a.py").write_bytes(b"x")
        frozen = compute_fn(["a.py"], str(tmp_path))
        assert verify_fn(frozen, str(tmp_path)) == []

    # ── AC4: edited file detected ──────────────────────────────────────────

    def test_ac4_verify_red_test_hashes_detects_content_edit(self, tmp_path: Path) -> None:
        compute_fn = _require(boundary_mod, "compute_red_test_hashes")
        verify_fn = _require(boundary_mod, "verify_red_test_hashes")
        (tmp_path / "a.py").write_bytes(b"x")
        frozen = compute_fn(["a.py"], str(tmp_path))
        (tmp_path / "a.py").write_bytes(b"y")
        assert verify_fn(frozen, str(tmp_path)) == ["a.py"]

    # ── AC5: deleted file detected ─────────────────────────────────────────

    def test_ac5_verify_red_test_hashes_detects_deletion(self, tmp_path: Path) -> None:
        compute_fn = _require(boundary_mod, "compute_red_test_hashes")
        verify_fn = _require(boundary_mod, "verify_red_test_hashes")
        (tmp_path / "a.py").write_bytes(b"x")
        frozen = compute_fn(["a.py"], str(tmp_path))
        (tmp_path / "a.py").unlink()
        assert "a.py" in verify_fn(frozen, str(tmp_path))

    # ── AC6: exact-match escape narrows the edit away ──────────────────────

    def test_ac6_verify_red_test_hashes_exact_match_escape(self, tmp_path: Path) -> None:
        compute_fn = _require(boundary_mod, "compute_red_test_hashes")
        verify_fn = _require(boundary_mod, "verify_red_test_hashes")
        (tmp_path / "a.py").write_bytes(b"x")
        frozen = compute_fn(["a.py"], str(tmp_path))
        (tmp_path / "a.py").write_bytes(b"y")
        assert verify_fn(frozen, str(tmp_path), ["a.py"]) == []

    # ── AC7: write-once idempotency (§1ab) ──────────────────────────────────

    def test_ac7_persist_red_test_hashes_write_once_idempotent(self, tmp_path: Path) -> None:
        persist_fn = _require(phase_5_implement, "_persist_red_test_hashes")
        read_fn = _require(phase_5_implement, "_read_red_test_hashes")
        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()
        m1 = {"tests/test_x.py": "aaa"}
        m2 = {"tests/test_x.py": "bbb"}
        first = persist_fn(scratchpad, m1)
        second = persist_fn(scratchpad, m2)
        assert first is True
        assert second is False
        assert read_fn(scratchpad) == m1

    # ── AC8: fail-safe read ─────────────────────────────────────────────────

    def test_ac8_read_red_test_hashes_absent_and_malformed_returns_none(self, tmp_path: Path) -> None:
        read_fn = _require(phase_5_implement, "_read_red_test_hashes")
        scratchpad_absent = tmp_path / "scratch_absent"
        scratchpad_absent.mkdir()
        assert read_fn(scratchpad_absent) is None

        relpath = getattr(phase_5_implement, "RED_TEST_HASHES_RELPATH", "integrity/red-test-hashes.json")
        scratchpad_malformed = tmp_path / "scratch_malformed"
        malformed_path = scratchpad_malformed / relpath
        malformed_path.parent.mkdir(parents=True, exist_ok=True)
        malformed_path.write_text("not valid json {{{")
        assert read_fn(scratchpad_malformed) is None

    # ── AC9: _commit_red_tests freezes the manifest as a prod side-effect ──

    def test_ac9_commit_red_tests_freezes_manifest_and_emits_event(
        self, tmp_path: Path, monkeypatch
    ) -> None:
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
            phase_5_implement,
            "_emit_safe",
            lambda et, p, severity="info": captured.append({"type": et, "payload": p}),
        )

        ctx = _make_red_ctx(scratchpad, str(repo))
        prev = _make_prev(cycle=1, red_test_paths=["tests/test_foo.py"])

        result = phase_5_implement._commit_red_tests(ctx, prev)
        assert result.status == "ok", f"Expected ok, got {result.status!r}: {getattr(result, 'error', '')}"

        relpath = getattr(phase_5_implement, "RED_TEST_HASHES_RELPATH", "integrity/red-test-hashes.json")
        manifest_path = scratchpad / relpath
        assert manifest_path.is_file(), (
            f"Expected frozen manifest at {manifest_path} after _commit_red_tests"
        )
        manifest = json.loads(manifest_path.read_text())
        assert "tests/test_foo.py" in manifest

        freeze_events = [e for e in captured if e["type"] == "red_test_hashes_frozen"]
        assert len(freeze_events) == 1, f"Expected 1 red_test_hashes_frozen event, got {captured!r}"
        assert freeze_events[0]["payload"].get("n", 0) >= 1
        assert freeze_events[0]["payload"].get("persisted") is True

    # ── AC9b (BLOCKING, §1l): freeze source is red_test_paths, NOT trackable_paths ──

    def test_ac9b_commit_red_tests_freezes_degraded_gitignored_path(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Degraded/gitignored mode: trackable_paths == [] but red_test_paths still
        holds the gitignored test path. Freezing over trackable_paths would produce
        an EMPTY manifest here — exactly the git-diff-blind case this ship covers.
        Forces GREEN to freeze over red_test_paths."""
        repo = (tmp_path / "repo").resolve()
        scratchpad = tmp_path / "scratch"
        _init_repo(repo)
        _commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
        _commit_file(repo, ".gitignore", "tests/\n", "add gitignore")

        test_file = repo / "tests" / "test_x.py"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        test_file.write_text("def test_x(): assert True\n")
        # NOT git-added — gitignored, so trackable_paths (gitignore-filtered) == [].

        captured: list[dict] = []
        monkeypatch.setattr(
            phase_5_implement,
            "_emit_safe",
            lambda et, p, severity="info": captured.append({"type": et, "payload": p}),
        )

        ctx = _make_red_ctx(scratchpad, str(repo))
        prev = _make_prev(cycle=1, red_test_paths=["tests/test_x.py"])

        result = phase_5_implement._commit_red_tests(ctx, prev)
        assert result.status == "ok", f"Expected ok (degraded no-op-commit path), got {result.status!r}: {getattr(result, 'error', '')}"

        relpath = getattr(phase_5_implement, "RED_TEST_HASHES_RELPATH", "integrity/red-test-hashes.json")
        manifest_path = scratchpad / relpath
        assert manifest_path.is_file(), (
            f"Expected frozen manifest at {manifest_path} even in degraded/gitignored mode"
        )
        manifest = json.loads(manifest_path.read_text())
        assert "tests/test_x.py" in manifest, (
            f"Freeze source must be red_test_paths (non-empty here), not trackable_paths "
            f"(empty for a gitignored path); got manifest keys {list(manifest.keys())!r}"
        )

        freeze_events = [e for e in captured if e["type"] == "red_test_hashes_frozen"]
        assert len(freeze_events) == 1, f"Expected 1 red_test_hashes_frozen event, got {captured!r}"
        assert freeze_events[0]["payload"].get("n", 0) >= 1
        assert freeze_events[0]["payload"].get("persisted") is True

    # ── AC10 (e2e, git-diff-blind): hash leg wired into _commit_green_code ──

    def test_ac10_commit_green_code_rejects_hash_tamper_on_gitignored_test(
        self, tmp_path: Path
    ) -> None:
        compute_fn = _require(boundary_mod, "compute_red_test_hashes")
        persist_fn = _require(phase_5_implement, "_persist_red_test_hashes")

        repo = (tmp_path / "repo").resolve()
        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()
        _init_repo(repo)
        _commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
        _commit_file(repo, ".gitignore", "tests/\n", "add gitignore")

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
        prev = _make_prev(
            cycle=1, red_commit_sha=red_sha,
            worker_written_paths=["src/module.py"], manifest_source="harness_tool_record",
            spec_path=str(spec_path),
        )

        commits_before = _rev_count(repo)
        result = phase_5_implement._commit_green_code(ctx, prev)
        commits_after = _rev_count(repo)

        assert result.error_code == "E_RED_TESTS_TAMPERED", (
            f"git-diff leg is blind to this gitignored test; only the frozen-hash "
            f"leg can catch this tamper. Got status={result.status!r} "
            f"error_code={getattr(result, 'error_code', None)!r} data={result.data!r}"
        )
        assert result.data is None
        assert commits_after == commits_before

    # ── AC11: reason-aware allowlist parse ─────────────────────────────────

    def test_ac11_parse_authorized_test_edits_with_reasons_grammar(self, tmp_path: Path) -> None:
        fn = _require(run_allowlist_mod, "parse_authorized_test_edits_with_reasons")
        spec_path = tmp_path / "spec.md"
        spec_path.write_text(
            "## Files\n\n- MODIFY `lib/foo.py`\n\n"
            "authorized-test-edits:\n"
            "- tests/foo.py — intentional fixture bump\n"
            "- tests/bar.py\n"
        )
        result = fn(str(spec_path))
        assert result == {
            "tests/foo.py": "intentional fixture bump",
            "tests/bar.py": "",
        }

    # ── AC12: authorized escape with reason, event carries reason ─────────

    def test_ac12_commit_green_code_honors_authorized_hash_edit_with_reason_event(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        compute_fn = _require(boundary_mod, "compute_red_test_hashes")
        persist_fn = _require(phase_5_implement, "_persist_red_test_hashes")

        repo = (tmp_path / "repo").resolve()
        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()
        _init_repo(repo)
        _commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
        _commit_file(repo, ".gitignore", "tests/\n", "add gitignore")

        _write_file(repo, "tests/test_x.py", "def test_x(): assert True\n")
        red_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
        ).stdout.strip()

        manifest = compute_fn(["tests/test_x.py"], str(repo))
        persist_fn(scratchpad, manifest)

        _write_file(repo, "tests/test_x.py", "def test_x(): assert False  # inverted contract\n")

        spec_path = scratchpad / "spec.md"
        spec_path.write_text(
            "## Files\n\n- MODIFY `src/module.py`\n\n"
            "authorized-test-edits:\n"
            "- tests/test_x.py — inverted contract, intentional\n"
        )
        _write_file(repo, "src/module.py", "def bar(): return 2\n")

        captured: list[dict] = []
        monkeypatch.setattr(
            phase_5_implement,
            "_emit_safe",
            lambda et, p, severity="info": captured.append({"type": et, "payload": p}),
        )

        ctx = _make_green_ctx(scratchpad, str(repo))
        prev = _make_prev(
            cycle=1, red_commit_sha=red_sha,
            worker_written_paths=["src/module.py"], manifest_source="harness_tool_record",
            spec_path=str(spec_path),
        )

        result = phase_5_implement._commit_green_code(ctx, prev)

        assert result.error_code != "E_RED_TESTS_TAMPERED", (
            f"authorized-test-edits with a reason must escape the hash-tamper leg; "
            f"got error_code={getattr(result, 'error_code', None)!r}"
        )

        escape_events = [e for e in captured if e["type"] == "red_authorized_test_edit"]
        assert len(escape_events) == 1, f"Expected 1 red_authorized_test_edit event, got {captured!r}"
        assert escape_events[0]["payload"].get("path") == "tests/test_x.py"
        assert escape_events[0]["payload"].get("reason") == "inverted contract, intentional"

    # ── AC13 (advisory): no frozen manifest -> hash leg is a clean no-op ──────

    def test_ac13_commit_green_code_frozen_none_is_noop(self, tmp_path: Path) -> None:
        """No manifest ever frozen (frozen is None) -> _commit_green_code proceeds
        normally, unaffected by the new hash-tamper leg (backward-compat)."""
        # Presence-gate the new read symbol so this stays collectable/relevant
        # even though the assertions below exercise _commit_green_code directly.
        _require(phase_5_implement, "_read_red_test_hashes")

        repo = (tmp_path / "repo").resolve()
        scratchpad = tmp_path / "scratch"
        scratchpad.mkdir()
        _init_repo(repo)
        _commit_file(repo, "src/placeholder.py", "# placeholder\n", "init")
        red_sha = _commit_file(repo, "tests/test_x.py", "def test_x(): assert True\n", "RED: add test_x")
        # Test file left untouched — no tamper, no manifest ever persisted.
        _write_file(repo, "src/module.py", "def bar(): return 2\n")

        spec_path = scratchpad / "spec.md"
        spec_path.write_text("## Files\n\n- MODIFY `src/module.py`\n")

        # Confirm no manifest exists in this scratchpad (frozen is None case).
        relpath = getattr(phase_5_implement, "RED_TEST_HASHES_RELPATH", "integrity/red-test-hashes.json")
        assert not (scratchpad / relpath).exists()

        ctx = _make_green_ctx(scratchpad, str(repo))
        prev = _make_prev(
            cycle=1, red_commit_sha=red_sha,
            worker_written_paths=["src/module.py"], manifest_source="harness_tool_record",
            spec_path=str(spec_path),
        )

        commits_before = _rev_count(repo)
        result = phase_5_implement._commit_green_code(ctx, prev)
        commits_after = _rev_count(repo)

        assert result.status == "ok", (
            f"Expected ok when no manifest was ever frozen, got {result.status!r} "
            f"error_code={getattr(result, 'error_code', None)!r} data={result.data!r}"
        )
        assert getattr(result, "error_code", None) != "E_RED_TESTS_TAMPERED"
        assert commits_after == commits_before + 1
