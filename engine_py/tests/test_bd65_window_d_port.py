"""RED tests for bd#65 window D — hal->bytedigger port.

Part 1: port of hal's TestGH921ClassifyRedHashMismatches (AC1-AC3) targeting
``lib.authored_boundary.classify_red_hash_mismatches``, which exists in hal
but not yet in this bd repo's ``lib/authored_boundary.py`` (which already
carries ``scan_boundary``/``compute_red_test_hashes`` from an earlier port).

Part 2: catalog-parity asserts for ``ERROR_CODES`` (error_codes.py) and
``FLAGS`` (flags_catalog.py) — the bd catalogs are missing the GH891/GH892
schema-smoke/fixture-drift error codes and the corresponding flag entries,
plus the GH807-style flip-by retarget on HAL_VERDICT_GATE_LINT_ENFORCE.

Conventions (§1q / D1CF5FDF collectability guard, 81F97F3D no module-level
sys.path mutation): ``lib.authored_boundary`` already exists in this repo so
it is imported at module top level (collectable). The NOT-yet-existing
symbol ``classify_red_hash_mismatches`` is probed via ``_require`` INSIDE
each test body — the file COLLECTS and each test FAILS at assert time, never
at collection time.

Stub-passability (§1l/7AD3D393): UUT symbols are called directly, never
patched/mocked.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from bytedigger_engine.lib import authored_boundary as boundary_mod  # noqa: E402
from bytedigger_engine.error_codes import ERROR_CODES  # noqa: E402
from bytedigger_engine.flags_catalog import FLAGS  # noqa: E402


# ─── shared helpers (mirrors hal's test_gh921_red_baseline_refresh.py) ────────


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


def _require(mod, name: str):
    """Presence-gate for a not-yet-existing symbol (D1CF5FDF collectability)."""
    fn = getattr(mod, name, None)
    assert fn is not None, (
        f"{mod.__name__}.{name} does not exist yet (bd#65 window D not implemented) — expected RED fail"
    )
    return fn


# ═══════════════════════════════════════════════════════════════════════════
# TestBD65ClassifyRedHashMismatches (AC1-AC3, port of GH921)
# ═══════════════════════════════════════════════════════════════════════════


class TestBD65ClassifyRedHashMismatches:
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
# TestBD65ErrorCodesPort — catalog parity (GH891/GH892)
# ═══════════════════════════════════════════════════════════════════════════


class TestBD65ErrorCodesPort:
    def test_e_schema_smoke_dryrun_error_present(self) -> None:
        assert ERROR_CODES.get("E_SCHEMA_SMOKE_DRYRUN_ERROR") == (
            "phase_5_integrity: schema-smoke dry-run artifact failed for a reason "
            "unrelated to schema mismatch (category FIXTURE_SCHEMA_MISMATCH n/a)"
        )

    def test_e_schema_smoke_mismatch_present(self) -> None:
        assert ERROR_CODES.get("E_SCHEMA_SMOKE_MISMATCH") == (
            "phase_5_integrity: schema-smoke dry-run detected a real schema mismatch "
            "(category FIXTURE_SCHEMA_MISMATCH)"
        )

    def test_e_schema_smoke_unavailable_present(self) -> None:
        assert ERROR_CODES.get("E_SCHEMA_SMOKE_UNAVAILABLE") == (
            "phase_5_integrity: schema-smoke target snapshot unavailable "
            "(missing schema, restore failure, or timeout)"
        )

    def test_e_red_fixture_schema_drift_present(self) -> None:
        assert ERROR_CODES.get("E_RED_FIXTURE_SCHEMA_DRIFT") == (
            "phase_5_implement: RED fixture CREATE TABLE columns are not a subset "
            "of the spec's Data-Model Ground Truth reference DDL (GH891 "
            "fixture-fiction class)"
        )


# ═══════════════════════════════════════════════════════════════════════════
# TestBD65FlagsCatalogPort — flag-catalog parity (GH891/GH892 + GH807 retarget)
# ═══════════════════════════════════════════════════════════════════════════


class TestBD65FlagsCatalogPort:
    def test_hal_agent_sdk_max_resumes_present(self) -> None:
        entry = FLAGS.get("HAL_AGENT_SDK_MAX_RESUMES")
        assert entry is not None, "HAL_AGENT_SDK_MAX_RESUMES missing from FLAGS"
        assert entry.get("kind") == "int"
        assert entry.get("default") == "8"
        assert entry.get("module") == "lib/reference_backends/agent_sdk.py"

    def test_hal_schema_smoke_gate_present(self) -> None:
        entry = FLAGS.get("HAL_SCHEMA_SMOKE_GATE")
        assert entry is not None, "HAL_SCHEMA_SMOKE_GATE missing from FLAGS"
        assert entry.get("kind") == "gate"
        assert entry.get("default") == "1"
        assert entry.get("module") == "workflows/phase_5_integrity.py"

    def test_hal_fixture_schema_gate_present(self) -> None:
        entry = FLAGS.get("HAL_FIXTURE_SCHEMA_GATE")
        assert entry is not None, "HAL_FIXTURE_SCHEMA_GATE missing from FLAGS"
        assert entry.get("kind") == "gate"
        assert entry.get("default") == "1"
        assert entry.get("module") == "workflows/phase_5_implement.py"

    def test_hal_red_baseline_refresh_present(self) -> None:
        entry = FLAGS.get("HAL_RED_BASELINE_REFRESH")
        assert entry is not None, "HAL_RED_BASELINE_REFRESH missing from FLAGS"
        assert entry.get("kind") == "flag"
        assert entry.get("default") == "0"
        assert entry.get("module") == "workflows/phase_5_implement.py"

    def test_hal_verdict_gate_lint_enforce_flip_by_retargeted(self) -> None:
        entry = FLAGS.get("HAL_VERDICT_GATE_LINT_ENFORCE")
        assert entry is not None, "HAL_VERDICT_GATE_LINT_ENFORCE missing from FLAGS"
        desc = entry.get("description", "")
        assert "flip-by:2026-08-01" in desc, (
            f"Expected retargeted flip-by:2026-08-01 in description, got: {desc!r}"
        )
