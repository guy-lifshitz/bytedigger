"""RED tests for 9D7A44A4 — engine_py/run.py --list-runs CLI (Ship #2a).

Pre-GREEN: ALL 8 tests FAIL because --list-runs argparse flag does not exist
in run.py and lib/dbos_list_runs.py has not been created. AC1 fails with exit
code 2 (unknown argument). AC2-AC8 fail because the CLI produces no TSV/JSON
output.

All tests invoke the CLI as a subprocess via sys.executable + run.py — never
import bytedigger_engine.lib.dbos_list_runs directly (per spec §5 / §5.1 anti-fabrication rule).

Forcing-function ACs (spec §3 §1l):
  - AC4 and AC6 anchor on orchestrator-frozen SHA-256 literals.
  - AC7 anchors on an independent sqlite3 row-set equality check vs CLI output.
  - AC8 anchors on two frozen SHAs for header-only TSV and empty JSON array.
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess

from helpers.engine_subprocess import engine_env
import sys
from pathlib import Path

import pytest

# ── Path constants ──────────────────────────────────────────────────────────
HERE = Path(__file__).resolve().parent      # engine_py/tests/
ENGINE_ROOT = HERE.parent                   # engine_py/
RUN_PY = ENGINE_ROOT / "bytedigger_engine" / "run.py"


# ── Helpers ─────────────────────────────────────────────────────────────────

def _make_env(tmp_db: Path) -> dict[str, str]:
    """Merge with real PATH/PYTHONPATH so the subprocess resolves lib imports."""
    base = dict(os.environ)
    base["HAL_DBOS_DB_PATH"] = str(tmp_db)
    return base


def _seed_db(db_path: Path, rows: list[tuple[str, str, int]]) -> None:
    """Create minimal workflow_status table and insert given rows."""
    with sqlite3.connect(str(db_path)) as conn:
        conn.execute(
            "CREATE TABLE workflow_status "
            "(workflow_uuid TEXT PRIMARY KEY, status TEXT, name TEXT, updated_at INTEGER)"
        )
        conn.executemany(
            "INSERT INTO workflow_status (workflow_uuid, status, name, updated_at) VALUES (?,?,?,?)",
            [(uuid, status, None, updated_at) for uuid, status, updated_at in rows],
        )


# ── Full fixture rows (spec §3 Fixture) ────────────────────────────────────
_FULL_FIXTURE_ROWS: list[tuple[str, str, int]] = [
    ("forge-a000__phase_1_discovery",   "SUCCESS", 1000),
    ("forge-a000__phase_5_implement",   "SUCCESS", 2000),
    ("forge-a000__phase_8_post_deploy", "SUCCESS", 3000),
    ("int-b000__phase_05_inject",       "ERROR",   4000),
    ("bare-uuid-noseparator",           "SUCCESS", 5000),  # legacy — must be SKIPPED
]

# ── Frozen SHA-256 literals (orchestrator-frozen at spec-freeze 2026-05-24) ─
_SHA_AC4_FULL_TSV  = "bc06bc1846ca5b9dd26e95653c31de9e0f9afb6074c17ce2792dfadd41309cee"
_SHA_AC6_FULL_JSON = "331d0f024e81f81fe6ee194a012054783c7c25174aa21aafd81b58d8447e1c79"
_SHA_AC8_EMPTY_TSV = "eb32356d8d58a02d1d86d39584bc8ff4ca31915934d800a8a745bf5321570596"
_SHA_AC8_EMPTY_JSON = "37517e5f3dc66819f61f5a7bb8ace1921282415f10551d2defa5c3eb0985b570"


# ════════════════════════════════════════════════════════════════════════════
# AC1 — Argparse plumbing
# ════════════════════════════════════════════════════════════════════════════

def test_ac1_argparse_plumbing(tmp_path: Path) -> None:
    """AC1: python3 run.py --list-runs --help exits 0 and mentions --list-runs.

    Pre-GREEN: argparse exits 2 with 'unrecognized arguments: --list-runs' →
    returncode == 2, not 0 → assertion fails.
    """
    tmp_db = tmp_path / "ac1.sqlite"
    _seed_db(tmp_db, [])
    result = subprocess.run(
        [sys.executable, str(RUN_PY), "--list-runs", "--help"],
        env=engine_env(_make_env(tmp_db)),
        capture_output=True,
        timeout=30,
    )
    stdout = result.stdout.decode("utf-8", errors="replace")
    assert result.returncode == 0, (
        f"Expected exit 0, got {result.returncode}. stderr: {result.stderr.decode()}"
    )
    assert "--list-runs" in stdout, (
        f"'--list-runs' not found in help output: {stdout[:400]}"
    )


# ════════════════════════════════════════════════════════════════════════════
# AC2 — Single-run TSV output
# ════════════════════════════════════════════════════════════════════════════

def test_ac2_single_run_tsv(tmp_path: Path) -> None:
    """AC2: single-run fixture (forge-a000 3 rows) produces deterministic TSV.

    Fixture: only the 3 forge-a000 rows (no int-b000, no legacy).
    Asserts: 2-line stdout (header + one data row), exit 0,
    data row == expected verbatim.

    Pre-GREEN: CLI produces no output → stdout is empty → assertions fail.
    """
    tmp_db = tmp_path / "ac2.sqlite"
    _seed_db(tmp_db, [
        ("forge-a000__phase_1_discovery",   "SUCCESS", 1000),
        ("forge-a000__phase_5_implement",   "SUCCESS", 2000),
        ("forge-a000__phase_8_post_deploy", "SUCCESS", 3000),
    ])
    result = subprocess.run(
        [sys.executable, str(RUN_PY), "--list-runs"],
        env=engine_env(_make_env(tmp_db)),
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"Exit {result.returncode}. stderr: {result.stderr.decode()}"
    )
    lines = result.stdout.decode("utf-8").splitlines()
    # Header line
    assert lines[0] == "RUN_ID\tPHASES\tSUCCESS\tERROR\tPENDING\tLAST_UPDATED_MS\tWORKFLOWS", (
        f"Unexpected header: {lines[0]!r}"
    )
    # Exactly one data row
    assert len(lines) == 2, f"Expected 2 lines (header + 1 row), got {len(lines)}: {lines}"
    expected_row = "forge-a000\t3\t3\t0\t0\t3000\tphase_1_discovery,phase_5_implement,phase_8_post_deploy"
    assert lines[1] == expected_row, f"Data row mismatch.\nGot:      {lines[1]!r}\nExpected: {expected_row!r}"


# ════════════════════════════════════════════════════════════════════════════
# AC3 — Multi-status aggregation
# ════════════════════════════════════════════════════════════════════════════

def test_ac3_multi_status_aggregation(tmp_path: Path) -> None:
    """AC3: one run with SUCCESS/ERROR/PENDING rows → correct per-status counts.

    Fixture: mixed-r000 with p1(SUCCESS/100), p2(ERROR/200), p3(PENDING/300).
    Expected data row: mixed-r000<TAB>3<TAB>1<TAB>1<TAB>1<TAB>300<TAB>p1,p2,p3

    Pre-GREEN: CLI produces no output → assertions fail.
    """
    tmp_db = tmp_path / "ac3.sqlite"
    _seed_db(tmp_db, [
        ("mixed-r000__p1", "SUCCESS", 100),
        ("mixed-r000__p2", "ERROR",   200),
        ("mixed-r000__p3", "PENDING", 300),
    ])
    result = subprocess.run(
        [sys.executable, str(RUN_PY), "--list-runs"],
        env=engine_env(_make_env(tmp_db)),
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"Exit {result.returncode}. stderr: {result.stderr.decode()}"
    )
    lines = result.stdout.decode("utf-8").splitlines()
    assert len(lines) == 2, f"Expected header + 1 row, got {len(lines)}"
    expected = "mixed-r000\t3\t1\t1\t1\t300\tp1,p2,p3"
    assert lines[1] == expected, f"Got {lines[1]!r}, expected {expected!r}"


# ════════════════════════════════════════════════════════════════════════════
# AC4 — N>1 sort-by-max-updated-DESC + frozen SHA (full fixture)
# ════════════════════════════════════════════════════════════════════════════

def test_ac4_n2_sort_by_max_updated_desc(tmp_path: Path) -> None:
    """AC4: full fixture (2 runs + legacy) → stdout SHA == orchestrator-frozen value.

    Verifies sort order (int-b000 first, last_updated_ms=4000 > forge-a000 3000)
    and the N>1 multi-run path (6AD2F3F8 forcing function).
    SHA literal: bc06bc1846ca5b9dd26e95653c31de9e0f9afb6074c17ce2792dfadd41309cee

    Pre-GREEN: CLI produces no output → sha256('') != frozen SHA → assertion fails.
    """
    tmp_db = tmp_path / "ac4.sqlite"
    _seed_db(tmp_db, _FULL_FIXTURE_ROWS)
    result = subprocess.run(
        [sys.executable, str(RUN_PY), "--list-runs"],
        env=engine_env(_make_env(tmp_db)),
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"Exit {result.returncode}. stderr: {result.stderr.decode()}"
    )
    raw = result.stdout  # bytes
    actual_sha = hashlib.sha256(raw).hexdigest()
    assert actual_sha == _SHA_AC4_FULL_TSV, (
        f"TSV SHA mismatch.\n"
        f"  Expected: {_SHA_AC4_FULL_TSV}\n"
        f"  Actual:   {actual_sha}\n"
        f"  Stdout: {raw.decode()!r}"
    )


# ════════════════════════════════════════════════════════════════════════════
# AC5 — Legacy-row exclusion
# ════════════════════════════════════════════════════════════════════════════

def test_ac5_legacy_row_exclusion(tmp_path: Path) -> None:
    """AC5: bare-uuid-noseparator row must NOT appear in CLI output.

    Pre-GREEN: CLI produces no output → returncode != 0 or 'bare-uuid' check is
    vacuous (empty stdout passes the 'not in' check but returncode fails first).
    We assert both returncode==0 AND 'bare-uuid' not present to force the test
    to fail for the right reason post-GREEN (not just absence-of-output).
    """
    tmp_db = tmp_path / "ac5.sqlite"
    _seed_db(tmp_db, _FULL_FIXTURE_ROWS)
    result = subprocess.run(
        [sys.executable, str(RUN_PY), "--list-runs"],
        env=engine_env(_make_env(tmp_db)),
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"Exit {result.returncode}. stderr: {result.stderr.decode()}"
    )
    stdout = result.stdout.decode("utf-8")
    assert "bare-uuid" not in stdout, (
        f"Legacy row 'bare-uuid-noseparator' appeared in output: {stdout!r}"
    )
    # Additionally verify the output has exactly 2 runs (int-b000 + forge-a000),
    # confirming the 4-phase rows ARE present — not merely an empty output.
    lines = [l for l in stdout.splitlines() if l and not l.startswith("RUN_ID")]
    assert len(lines) == 2, (
        f"Expected 2 run data rows, got {len(lines)}: {lines}"
    )


# ════════════════════════════════════════════════════════════════════════════
# AC6 — JSON mode + frozen SHA
# ════════════════════════════════════════════════════════════════════════════

def test_ac6_json_mode(tmp_path: Path) -> None:
    """AC6: --list-runs --json → stdout SHA == orchestrator-frozen JSON SHA.

    SHA literal: 331d0f024e81f81fe6ee194a012054783c7c25174aa21aafd81b58d8447e1c79

    Pre-GREEN: --json flag unknown → argparse error exit 2 → assertion fails.
    """
    tmp_db = tmp_path / "ac6.sqlite"
    _seed_db(tmp_db, _FULL_FIXTURE_ROWS)
    result = subprocess.run(
        [sys.executable, str(RUN_PY), "--list-runs", "--json"],
        env=engine_env(_make_env(tmp_db)),
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"Exit {result.returncode}. stderr: {result.stderr.decode()}"
    )
    raw = result.stdout
    actual_sha = hashlib.sha256(raw).hexdigest()
    assert actual_sha == _SHA_AC6_FULL_JSON, (
        f"JSON SHA mismatch.\n"
        f"  Expected: {_SHA_AC6_FULL_JSON}\n"
        f"  Actual:   {actual_sha}\n"
        f"  Stdout: {raw.decode()!r}"
    )
    # Structural sanity: parse as JSON list with expected run_id order
    parsed = json.loads(raw.decode("utf-8"))
    assert isinstance(parsed, list), f"Expected JSON array, got {type(parsed)}"
    assert len(parsed) == 2, f"Expected 2 runs, got {len(parsed)}"
    assert parsed[0]["run_id"] == "int-b000",   f"First run should be int-b000: {parsed[0]}"
    assert parsed[1]["run_id"] == "forge-a000", f"Second run should be forge-a000: {parsed[1]}"


# ════════════════════════════════════════════════════════════════════════════
# AC7 — Independent sqlite Layer-5 inspection
# ════════════════════════════════════════════════════════════════════════════

def test_ac7_independent_sqlite_layer5(tmp_path: Path) -> None:
    """AC7: independently open DB in RO mode; workflow_uuid set matches CLI stdout.

    Layer-5 forcing function (§1l): the test opens the same sqlite file that
    HAL_DBOS_DB_PATH points to, queries rows with '__' separator independently,
    then asserts the run_ids surfaced by --list-runs match.

    Pre-GREEN: CLI returns no output; parsed run_ids set is empty while DB
    query returns {forge-a000, int-b000} → sets don't match → FAIL.
    """
    tmp_db = tmp_path / "ac7.sqlite"
    _seed_db(tmp_db, _FULL_FIXTURE_ROWS)

    # Step 1: run CLI, parse run_ids from TSV output
    result = subprocess.run(
        [sys.executable, str(RUN_PY), "--list-runs"],
        env=engine_env(_make_env(tmp_db)),
        capture_output=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"Exit {result.returncode}. stderr: {result.stderr.decode()}"
    )
    stdout_lines = result.stdout.decode("utf-8").splitlines()
    # Skip header row; parse first column of each data row
    cli_run_ids = {
        line.split("\t")[0]
        for line in stdout_lines[1:]
        if line.strip()
    }

    # Step 2: independently query DB in read-only mode
    with sqlite3.connect(f"file:{tmp_db}?mode=ro", uri=True) as conn:
        rows = conn.execute(
            "SELECT workflow_uuid FROM workflow_status "
            "WHERE workflow_uuid LIKE '%__%' ESCAPE '\\'"
        ).fetchall()
    db_run_ids = {uuid.split("__")[0] for (uuid,) in rows if "__" in uuid}

    assert cli_run_ids == db_run_ids, (
        f"CLI run_ids {cli_run_ids!r} != DB-derived run_ids {db_run_ids!r}"
    )
    # Sanity: both sides should contain exactly forge-a000 and int-b000
    assert cli_run_ids == {"forge-a000", "int-b000"}, (
        f"Expected {{'forge-a000','int-b000'}}, got {cli_run_ids!r}"
    )


# ════════════════════════════════════════════════════════════════════════════
# AC8 — Empty-DB graceful exit + frozen SHAs
# ════════════════════════════════════════════════════════════════════════════

def test_ac8_empty_db_graceful_exit(tmp_path: Path) -> None:
    """AC8: empty DB → header-only TSV (sha == frozen) + [] JSON (sha == frozen). Exit 0.

    Frozen TSV SHA:  eb32356d8d58a02d1d86d39584bc8ff4ca31915934d800a8a745bf5321570596
    Frozen JSON SHA: 37517e5f3dc66819f61f5a7bb8ace1921282415f10551d2defa5c3eb0985b570

    Pre-GREEN: CLI not implemented → exit 2 or empty stdout → SHA mismatch.
    """
    tmp_db = tmp_path / "ac8.sqlite"
    _seed_db(tmp_db, [])  # schema present, no rows

    # TSV mode
    result_tsv = subprocess.run(
        [sys.executable, str(RUN_PY), "--list-runs"],
        env=engine_env(_make_env(tmp_db)),
        capture_output=True,
        timeout=30,
    )
    assert result_tsv.returncode == 0, (
        f"TSV exit {result_tsv.returncode}. stderr: {result_tsv.stderr.decode()}"
    )
    tsv_sha = hashlib.sha256(result_tsv.stdout).hexdigest()
    assert tsv_sha == _SHA_AC8_EMPTY_TSV, (
        f"Empty-TSV SHA mismatch.\n"
        f"  Expected: {_SHA_AC8_EMPTY_TSV}\n"
        f"  Actual:   {tsv_sha}\n"
        f"  Stdout: {result_tsv.stdout!r}"
    )

    # JSON mode
    result_json = subprocess.run(
        [sys.executable, str(RUN_PY), "--list-runs", "--json"],
        env=engine_env(_make_env(tmp_db)),
        capture_output=True,
        timeout=30,
    )
    assert result_json.returncode == 0, (
        f"JSON exit {result_json.returncode}. stderr: {result_json.stderr.decode()}"
    )
    json_sha = hashlib.sha256(result_json.stdout).hexdigest()
    assert json_sha == _SHA_AC8_EMPTY_JSON, (
        f"Empty-JSON SHA mismatch.\n"
        f"  Expected: {_SHA_AC8_EMPTY_JSON}\n"
        f"  Actual:   {json_sha}\n"
        f"  Stdout: {result_json.stdout!r}"
    )
