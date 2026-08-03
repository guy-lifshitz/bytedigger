"""GH892 — schema-smoke: dry-run a fixture artifact against a real schema snapshot.

Battle-818 (ppba #818 / PR #1284): a RED-invented column (`source_doc_id`)
made it all the way to a shipped artifact that fails on first contact with a
real DB, and the failure surfaces as `{"error": "..."}` printed to **stdout**
with exit code 0 — no phase ever executes the artifact against real schema.
This module is the deterministic (no-LLM) fix: materialize a schema-only
temp DB from a real `.schema` snapshot, run the target artifact against it,
and classify the merged stdout+stderr for the `no such (column|table)`
signature regardless of exit code.

Named helpers per spec §2.2 (§1aa) — no inline math/dispatch in the caller.
"""
from __future__ import annotations

import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
from pathlib import Path

_SIGNATURE_RE = re.compile(r"no such (column|table)(: [\w.]+)?", re.IGNORECASE)


def restore_schema_to_tempdb(schema_sql: str, dest_path: Path) -> None:
    """Materialize a schema-only sqlite DB at `dest_path` from `schema_sql`.

    Cross-process dry-run artifacts cannot see a parent's `:memory:` DB, so
    we build a real (empty-tables) temp `.db` file — semantically equivalent
    to a `:memory:` schema for the purpose of a schema-mismatch smoke test.
    """
    conn = sqlite3.connect(str(dest_path))
    try:
        conn.executescript(schema_sql)
        conn.commit()
    finally:
        conn.close()


def classify_dryrun_output(exit_code: int, merged_output: str) -> tuple[str, str | None]:
    """Classify a single dry-run invocation's outcome.

    Returns (status, signature):
      - signature found (ANY exit code, including 0) -> ("mismatch", <match>)
      - no signature, exit 0                          -> ("pass", None)
      - no signature, exit != 0                        -> ("dryrun_error", None)
    """
    match = _SIGNATURE_RE.search(merged_output)
    if match:
        return "mismatch", match.group(0)
    if exit_code == 0:
        return "pass", None
    return "dryrun_error", None


_OVERALL_PRIORITY = ("mismatch", "unavailable", "dryrun_error", "pass")


def _fold_overall(statuses: list[str]) -> str:
    for candidate in _OVERALL_PRIORITY:
        if candidate in statuses:
            return candidate
    return "pass"


def run_schema_smoke(targets: list[dict[str, str]], cwd: Path, timeout_sec: int) -> dict[str, object]:
    """Run schema-smoke dry-runs for every target, return a report dict.

    Report shape: {"targets": [{"name", "status", "signature", "exit_code"}...],
                   "overall": "pass"|"mismatch"|"dryrun_error"|"unavailable"}
    """
    results: list[dict[str, object]] = []
    for target in targets:
        name = target.get("name", "?")
        schema_path = Path(target["schema_path"])
        cmd_template = target["cmd"]

        if not schema_path.is_file():
            results.append({
                "name": name, "status": "unavailable", "signature": None,
                "exit_code": None, "reason": "missing_schema",
            })
            continue

        tempdir = Path(os.path.realpath(tempfile.mkdtemp(prefix="schema_smoke_")))
        try:
            db_path = tempdir / "smoke.db"
            try:
                restore_schema_to_tempdb(schema_path.read_text(encoding="utf-8"), db_path)
            except Exception:
                results.append({
                    "name": name, "status": "unavailable", "signature": None,
                    "exit_code": None, "reason": "restore_failed",
                })
                continue

            cmd = [str(db_path) if part == "{db}" else part for part in cmd_template]
            try:
                proc = subprocess.run(
                    cmd,
                    cwd=str(cwd),
                    capture_output=True,
                    text=True,
                    timeout=timeout_sec,
                )
            except subprocess.TimeoutExpired:
                results.append({
                    "name": name, "status": "unavailable", "signature": None,
                    "exit_code": None, "reason": "timeout",
                })
                continue

            merged = (proc.stdout or "") + (proc.stderr or "")
            status, signature = classify_dryrun_output(proc.returncode, merged)
            results.append({
                "name": name, "status": status, "signature": signature,
                "exit_code": proc.returncode,
            })
        finally:
            shutil.rmtree(tempdir, ignore_errors=True)

    overall = _fold_overall([str(r["status"]) for r in results])
    return {"targets": results, "overall": overall}
