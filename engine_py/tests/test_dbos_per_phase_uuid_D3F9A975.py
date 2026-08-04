"""RED tests for D3F9A975 — DBOS per-phase workflow_uuid segregation.

Forcing-function AC: execute_durable_workflow("echo", ..., run_id="d3f9a975-ac5-run")
must produce a row whose workflow_uuid has PREFIX 'd3f9a975-ac5-run__echo' (per-phase segment)
and must NOT produce a row at workflow_uuid='d3f9a975-ac5-run' (OLD bare-run_id format).

941B33FC R1 extended the workflow_uuid format to {run_id}__{name}__{sha8}. Assertions
updated to prefix-match (LIKE) to accommodate the ctx-hash suffix while preserving the
original per-phase segregation intent.

Pre-GREEN: SetWorkflowID(run_id) stores OLD format only → prefix assert FAILS.
Post-GREEN: SetWorkflowID(f"{run_id}__{workflow_name}__{sha8}") stores NEW format
          with prefix 'd3f9a975-ac5-run__echo' → both asserts PASS.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from typing import Any

import pytest

# engine_py path setup so contracts, engine, workflows resolve
HERE = Path(__file__).resolve().parent.parent  # engine_py/

# Skip entire module if dbos is not installed (mirrors F7BE40B9 pattern)
try:
    from dbos import DBOS  # type: ignore
except ImportError:
    pytest.skip("dbos not installed", allow_module_level=True)

# Import the production seam under test
from bytedigger_engine.lib.dbos_setup import (  # type: ignore[import]
    init_dbos,
    execute_durable_workflow,
)

# ---------------------------------------------------------------------------
# Minimal WorkflowContext dict (duplicated from F7BE40B9 to keep file standalone)
# ---------------------------------------------------------------------------

_MINIMAL_CTX_DICT: dict[str, Any] = {
    "tenant_id": "hal-test",
    "scope": None,
    "db_path": None,
    "org_config": None,
    "question": "smoke test question",
    "session_id": "d3f9a975-test",
    "persona": "hal",
    "framework": None,
    "domain": None,
    "enable_rag": False,
    "enable_reranker": False,
    "enable_domain_scoped_reranker": False,
    "message_history": None,
    "llm_provider": "azure",
    "llm_model": "gpt-4.1-datazone",
    "domains": [],
}


def _init_with_tmp_db(tmp_path: Path) -> Path:
    """Call init_dbos() with a per-test SQLite path and return that path."""
    db_path = tmp_path / "dbos.sqlite"
    init_dbos(db_path)
    return db_path


# ---------------------------------------------------------------------------
# Autouse fixture: destroy DBOS singleton + reset _dbos_initialized between tests
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _dbos_cleanup():
    """Reset DBOS singleton state and lib.dbos_setup._dbos_initialized after each test."""
    yield
    try:
        DBOS.destroy()
    except Exception:
        pass
    try:
        from bytedigger_engine.lib import dbos_setup as _mod  # type: ignore
        _mod._dbos_initialized = False
    except ImportError:
        pass


# ===========================================================================
# AC5 (D3F9A975 spec AC5) — per-phase segregation forcing function
# ===========================================================================

def test_ac5_per_phase_segregation_D3F9A975(tmp_path, monkeypatch):
    """AC5 of D3F9A975 spec — per-phase workflow_uuid forcing function.

    After execute_durable_workflow("echo", ..., run_id="d3f9a975-ac5-run"):
      (a) workflow_status must contain a row whose workflow_uuid has prefix
          'd3f9a975-ac5-run__echo' (per-phase segment present; 941B33FC R1
          extended format to {run_id}__{name}__{sha8}, so prefix-match is used).
      (b) workflow_status row at workflow_uuid='d3f9a975-ac5-run' (OLD bare-run_id
          format) MUST NOT exist.

    RED predict: FAIL. Pre-GREEN code uses SetWorkflowID(run_id) → row at
    workflow_uuid='d3f9a975-ac5-run' exists (assertion (a) fails because OLD format
    does not start with the expected prefix) and assertion (b) also implicitly proven.

    Post-GREEN: SetWorkflowID(f"{run_id}__{workflow_name}__{sha8}") → row with
    prefix 'd3f9a975-ac5-run__echo' exists, OLD bare-run_id row does not exist
    → both (a) and (b) PASS.

    Stub-passability note (§1l forcing function): a stub that returns either constant
    would fail one of the two halves — (a) requires per-phase-prefixed row in real
    DBOS sqlite, (b) requires OLD bare-run_id row absent. No coordinated fake can satisfy both.
    """
    # GH839 B1 env-pin: this test exercises the DBOS fallback explicitly, which
    # must keep working after HAL_ENGINE_DURABLE_BACKEND's default flips to
    # "native" (§2.3.5).
    monkeypatch.setenv("HAL_ENGINE_DURABLE_BACKEND", "dbos")
    db_path = _init_with_tmp_db(tmp_path)
    event_log_path = str(tmp_path / "events.jsonl")
    run_id = "d3f9a975-ac5-run"
    expected_uuid_prefix = f"{run_id}__echo"

    execute_durable_workflow(
        "echo",
        dict(_MINIMAL_CTX_DICT),
        run_id,
        event_log_path=event_log_path,
    )

    conn = sqlite3.connect(str(db_path))
    try:
        # (a) Per-phase-prefixed row MUST exist (prefix match accommodates R1 sha8 suffix)
        new_row = conn.execute(
            "SELECT workflow_uuid FROM workflow_status WHERE workflow_uuid LIKE ?",
            (f"{expected_uuid_prefix}%",),
        ).fetchone()
        assert new_row is not None, (
            f"workflow_status must contain a row whose workflow_uuid starts with "
            f"{expected_uuid_prefix!r} "
            f"(D3F9A975 per-phase segregation; 941B33FC R1 adds sha8 suffix so prefix-match used)"
        )
        assert new_row[0].startswith(expected_uuid_prefix), (
            f"stored workflow_uuid {new_row[0]!r} must start with {expected_uuid_prefix!r} "
            f"(D3F9A975 per-phase segregation)"
        )

        # (b) OLD format row (bare run_id) MUST NOT exist
        old_row = conn.execute(
            "SELECT workflow_uuid FROM workflow_status WHERE workflow_uuid = ?",
            (run_id,),
        ).fetchone()
        assert old_row is None, (
            f"workflow_status must NOT contain a row for OLD-format workflow_uuid={run_id!r}; "
            f"found: {old_row!r}. D3F9A975 requires the per-phase format, not bare run_id."
        )
    finally:
        conn.close()
