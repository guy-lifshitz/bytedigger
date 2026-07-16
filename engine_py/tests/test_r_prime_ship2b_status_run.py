"""RED tests for Ship #2b — engine_py/run.py --status <run-id> CLI (agreement 7F698A10).

8 ACs per spec §3. All tests FAIL pre-GREEN because --status flag does not exist
in the pre-GREEN argparse block of run.py (confirmed by grep: 0 hits for --status
in run.py:70-79).

§1i compliance: every fixture pre-stages sqlite state deterministically via
pytest tmp_path fixture. No race conditions.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

# RUN_PY = engine_py/run.py (parent of tests/ dir, then up one more to engine_py/)
RUN_PY = Path(__file__).resolve().parent.parent / "run.py"

# Canonical fixture schema — 5 columns per spec §3
_CREATE_SQL = """
CREATE TABLE workflow_status (
    workflow_uuid TEXT PRIMARY KEY,
    status TEXT,
    name TEXT,
    created_at INTEGER,
    updated_at INTEGER
)
"""

# 5 fixture rows per spec §3 Fixture (order: forge-x100 x3, forge-y200, bare-uuid)
_FIXTURE_ROWS = [
    ("forge-x100__phase_1_discovery", "SUCCESS", "phase_1_discovery", 1000, 1100),
    ("forge-x100__phase_5_implement",  "ERROR",   "phase_5_implement",  1100, 1500),
    ("forge-x100__phase_8_post_deploy","PENDING", "phase_8_post_deploy", 1500, 1500),
    ("forge-y200__phase_1_discovery",  "SUCCESS", "phase_1_discovery",  2000, 2100),
    ("bare-uuid-noseparator",           "SUCCESS", "bare-uuid-noseparator", 500, 500),
]

# Golden SHAs — orchestrator-frozen 2026-05-25 per spec §3.1/§3.2/§3.3
_SHA_AC2_TSV     = "b2b47f463d688bec87ae0297c767fc8f517d15fe5ee29ce734ff4c31d6ce0ae0"
_SHA_AC3_JSON    = "c57f441a01044f1989ff45b0328bcbf7bffb1a8ba145e2bc7d143bddbca72658"
_SHA_AC4_STDERR  = "5600a73f61694a9639b03eb5270cb1da189ba094edd3ecb094c6ad9e14874296"


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _run_status(run_id: str, tmp_db: Path, extra_args: list[str] | None = None) -> subprocess.CompletedProcess:
    """Invoke run.py --status <run_id> via subprocess (§1i pre-staged DB)."""
    cmd = [sys.executable, str(RUN_PY), "--status", run_id] + (extra_args or [])
    return subprocess.run(
        cmd,
        env={"HAL_DBOS_DB_PATH": str(tmp_db), "PATH": os.environ["PATH"]},
        capture_output=True,
        text=True,
        timeout=30,
    )


@pytest.fixture
def seeded_db(tmp_path: Path) -> Path:
    """§1i pre-staged: sqlite file created and populated BEFORE CLI is invoked."""
    db = tmp_path / "workflow_status.sqlite"
    with sqlite3.connect(str(db)) as conn:
        conn.execute(_CREATE_SQL)
        conn.executemany(
            "INSERT INTO workflow_status VALUES (?,?,?,?,?)",
            _FIXTURE_ROWS,
        )
        conn.commit()
    return db


# ---------------------------------------------------------------------------
# AC1 — Argparse plumbing: --status flag exists
# ---------------------------------------------------------------------------

def test_ac1_argparse_status_flag_exists(tmp_path: Path) -> None:
    """AC1: python3 run.py --status RUN --help exits 0 and contains '--status'.

    Uses --help so argparse prints usage and exits before any DBOS init.
    FAIL pre-GREEN: --status does not exist in argparse block; --help exits 0
    but stdout will NOT contain '--status'.
    """
    dummy_db = tmp_path / "dummy.sqlite"
    result = subprocess.run(
        [sys.executable, str(RUN_PY), "--status", "RUN", "--help"],
        env={"HAL_DBOS_DB_PATH": str(dummy_db), "PATH": os.environ["PATH"]},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, f"Expected exit 0, got {result.returncode}\nstderr: {result.stderr}"
    assert "--status" in result.stdout, (
        f"'--status' not found in --help output:\n{result.stdout}"
    )


# ---------------------------------------------------------------------------
# AC2 — Default TSV happy path (§1l L6 SHA anchor)
# ---------------------------------------------------------------------------

def test_ac2_tsv_happy_path_sha(seeded_db: Path) -> None:
    """AC2: --status forge-x100 exits 0, empty stderr, stdout SHA matches frozen golden.

    FAIL pre-GREEN: --status not recognized, argparse will fail or emit no TSV.
    Frozen SHA: b2b47f463d688bec87ae0297c767fc8f517d15fe5ee29ce734ff4c31d6ce0ae0
    """
    result = _run_status("forge-x100", seeded_db)
    assert result.returncode == 0, f"Expected exit 0, got {result.returncode}\nstderr: {result.stderr}"
    assert result.stderr == "", f"Expected empty stderr, got: {result.stderr!r}"
    actual_sha = _sha256(result.stdout)
    assert actual_sha == _SHA_AC2_TSV, (
        f"TSV stdout SHA mismatch.\n"
        f"Expected: {_SHA_AC2_TSV}\n"
        f"Actual:   {actual_sha}\n"
        f"stdout:   {result.stdout!r}"
    )


# ---------------------------------------------------------------------------
# AC3 — JSON happy path (§1l L6 SHA anchor)
# ---------------------------------------------------------------------------

def test_ac3_json_happy_path_sha(seeded_db: Path) -> None:
    """AC3: --status forge-x100 --json exits 0, empty stderr, stdout SHA matches frozen golden.

    FAIL pre-GREEN: --status + --json flags absent; no JSON output produced.
    Frozen SHA: c57f441a01044f1989ff45b0328bcbf7bffb1a8ba145e2bc7d143bddbca72658
    """
    result = _run_status("forge-x100", seeded_db, extra_args=["--json"])
    assert result.returncode == 0, f"Expected exit 0, got {result.returncode}\nstderr: {result.stderr}"
    assert result.stderr == "", f"Expected empty stderr, got: {result.stderr!r}"
    actual_sha = _sha256(result.stdout)
    assert actual_sha == _SHA_AC3_JSON, (
        f"JSON stdout SHA mismatch.\n"
        f"Expected: {_SHA_AC3_JSON}\n"
        f"Actual:   {actual_sha}\n"
        f"stdout:   {result.stdout!r}"
    )


# ---------------------------------------------------------------------------
# AC4 — Unknown run_id → exit 2 + E_RUN_NOT_FOUND (§1l L6 SHA anchor)
# ---------------------------------------------------------------------------

def test_ac4_unknown_run_id_exit2_sha(seeded_db: Path) -> None:
    """AC4: --status nonexistent exits 2, empty stdout, stderr SHA matches frozen golden.

    FAIL pre-GREEN: no E_RUN_NOT_FOUND error path exists yet.
    Frozen SHA: 5600a73f61694a9639b03eb5270cb1da189ba094edd3ecb094c6ad9e14874296
    """
    result = _run_status("nonexistent", seeded_db)
    assert result.returncode == 2, f"Expected exit 2, got {result.returncode}\nstdout: {result.stdout}"
    assert result.stdout == "", f"Expected empty stdout, got: {result.stdout!r}"
    actual_sha = _sha256(result.stderr)
    assert actual_sha == _SHA_AC4_STDERR, (
        f"stderr SHA mismatch.\n"
        f"Expected: {_SHA_AC4_STDERR}\n"
        f"Actual:   {actual_sha}\n"
        f"stderr:   {result.stderr!r}"
    )


# ---------------------------------------------------------------------------
# AC5 — Independent Layer-5 sqlite backing-store inspection
# ---------------------------------------------------------------------------

def test_ac5_independent_sqlite_inspection(seeded_db: Path) -> None:
    """AC5 (§1l L5): independently query backing store; set of phases must match CLI output.

    The test independently reads the sqlite and compares against CLI TSV output.
    FAIL pre-GREEN: CLI produces no output (--status absent), so phase set from
    CLI will be empty, and will not match the DB rows.
    §1i: seeded_db fixture pre-stages state before CLI invocation.
    """
    # Independent query — same LIKE pattern the production code must use
    with sqlite3.connect(f"file:{seeded_db}?mode=ro", uri=True) as conn:
        rows = conn.execute(
            "SELECT workflow_uuid FROM workflow_status "
            "WHERE workflow_uuid LIKE 'forge-x100__%' "
            "ORDER BY created_at ASC"
        ).fetchall()
    expected_phases = {r[0].partition("__")[2] for r in rows if "__" in r[0]}

    # CLI invocation
    result = _run_status("forge-x100", seeded_db)
    # Parse phases from TSV output (skip header line)
    lines = result.stdout.strip().splitlines()
    actual_phases: set[str] = set()
    if len(lines) > 1:
        for line in lines[1:]:
            parts = line.split("\t")
            if parts:
                actual_phases.add(parts[0])

    assert actual_phases == expected_phases, (
        f"Phases from CLI do not match backing store.\n"
        f"DB expected phases: {expected_phases}\n"
        f"CLI actual phases:  {actual_phases}\n"
        f"CLI stdout:         {result.stdout!r}"
    )


# ---------------------------------------------------------------------------
# AC6 — Legacy row exclusion (bare-uuid-noseparator → exit 2)
# ---------------------------------------------------------------------------

def test_ac6_legacy_row_excluded(seeded_db: Path) -> None:
    """AC6: --status bare-uuid-noseparator exits 2 (LIKE prefix finds no __ rows).

    The legacy row has no '__' separator, so LIKE 'bare-uuid-noseparator__%' finds
    nothing → treated as unknown run_id → E_RUN_NOT_FOUND.
    FAIL pre-GREEN: no --status flag; no filtering logic exists.
    """
    result = _run_status("bare-uuid-noseparator", seeded_db)
    assert result.returncode == 2, (
        f"Expected exit 2 for legacy bare-uuid run_id, got {result.returncode}\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    assert result.stdout == "", f"Expected empty stdout, got: {result.stdout!r}"
    assert "E_RUN_NOT_FOUND" in result.stderr, (
        f"Expected 'E_RUN_NOT_FOUND' in stderr, got: {result.stderr!r}"
    )


# ---------------------------------------------------------------------------
# AC7 — Empty DB / missing file → exit 2 graceful
# ---------------------------------------------------------------------------

def test_ac7_missing_db_file_exit2(tmp_path: Path) -> None:
    """AC7: HAL_DBOS_DB_PATH pointing to non-existent file → exit 2 + E_RUN_NOT_FOUND.

    §1i: no fixture file is pre-staged — absence is the explicit pre-staged state.
    File must not exist at invocation time (tmp_path guarantees fresh empty dir).
    FAIL pre-GREEN: no --status path exists.
    """
    missing_db = tmp_path / "does-not-exist-ac7.sqlite"
    assert not missing_db.exists(), "tmp_path must not pre-create the file"

    cmd = [sys.executable, str(RUN_PY), "--status", "any-run"]
    result = subprocess.run(
        cmd,
        env={"HAL_DBOS_DB_PATH": str(missing_db), "PATH": os.environ["PATH"]},
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 2, (
        f"Expected exit 2 for missing DB, got {result.returncode}\n"
        f"stdout: {result.stdout!r}\nstderr: {result.stderr!r}"
    )
    assert result.stdout == "", f"Expected empty stdout, got: {result.stdout!r}"
    assert "E_RUN_NOT_FOUND: no rows found for run_id=any-run" in result.stderr, (
        f"Expected E_RUN_NOT_FOUND message in stderr, got: {result.stderr!r}"
    )


# ---------------------------------------------------------------------------
# AC8 — Sort phases by created_at ASC (not insertion order, not alpha)
# ---------------------------------------------------------------------------

def test_ac8_sort_by_created_at_asc(tmp_path: Path) -> None:
    """AC8: rows inserted in reverse chronological order must appear ASC in output.

    Fixture: phase_C (3000) → phase_A (1000) → phase_B (2000) — insertion reverse order.
    Expected TSV data rows: phase_A, phase_B, phase_C (by created_at ASC).
    FAIL pre-GREEN: no --status path; no ORDER BY created_at ASC logic exists.
    §1i: sqlite pre-staged before CLI invocation.
    """
    db = tmp_path / "sort_fixture.sqlite"
    # Insert in REVERSE chronological order to prove ORDER BY is enforced
    rows_reversed = [
        ("forge-z300__phase_C", "SUCCESS", "phase_C", 3000, 3000),
        ("forge-z300__phase_A", "SUCCESS", "phase_A", 1000, 1000),
        ("forge-z300__phase_B", "SUCCESS", "phase_B", 2000, 2000),
    ]
    with sqlite3.connect(str(db)) as conn:
        conn.execute(_CREATE_SQL)
        conn.executemany("INSERT INTO workflow_status VALUES (?,?,?,?,?)", rows_reversed)
        conn.commit()

    result = _run_status("forge-z300", db)
    assert result.returncode == 0, (
        f"Expected exit 0, got {result.returncode}\nstderr: {result.stderr}"
    )

    lines = result.stdout.strip().splitlines()
    # lines[0] is the header; lines[1..] are data rows
    assert len(lines) >= 4, f"Expected header + 3 data rows, got: {lines}"
    data_lines = lines[1:]  # skip header
    phases = [line.split("\t")[0] for line in data_lines]

    assert phases.index("phase_A") < phases.index("phase_B"), (
        f"phase_A should appear before phase_B in output. Phases: {phases}"
    )
    assert phases.index("phase_B") < phases.index("phase_C"), (
        f"phase_B should appear before phase_C in output. Phases: {phases}"
    )
