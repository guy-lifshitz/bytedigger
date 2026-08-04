"""RED tests for 39AF5B04 — deterministic DBOS teardown on run.py exit (issue #275).

Spec: SHARED/memory/Decisions/2026-07-04_39AF5B04_dbos_teardown_on_exit_spec.md

Problem: run.py runs a DBOS durable workflow via init_dbos()+execute_durable_workflow()
then exits without ever calling DBOS.destroy(). On Python 3.14 the non-daemon
`dbos-executor-_0` thread lingers and blocks interpreter shutdown.

Fix (not yet implemented):
  - New helper lib.dbos_setup.teardown_dbos() — idempotent, fail-soft DBOS.destroy()
  - run.py wires a `finally: teardown_dbos()` around the init_dbos/execute_durable_workflow
    try block so it fires on every post-init exit path (success + every error path),
    but NOT on the early-return paths (--list, --list-runs, --status, --derive-state).

COLLECTABILITY NOTE (D1CF5FDF / §1q):
  - NO top-level import of dbos_setup.teardown_dbos — it does not exist yet.
  - sys.path is set by conftest.py (conftest-import-time singleton, §1q / 81F97F3D).
  - `import lib.dbos_setup as dbos_setup` / `import run` are deferred to inside each
    test body so missing attrs raise AttributeError/AssertionError at ASSERT time,
    never at collection time.

Stub-passability (§1l/7AD3D393): UUTs are `dbos_setup.teardown_dbos` (AC1-5, called
directly — never patched) and `run.main` (AC6-8, patched collaborators are
`run.teardown_dbos` (create=True, doesn't exist yet) and `run.execute_durable_workflow`
(existing collaborator) — never the UUT itself).
"""
from __future__ import annotations

import json
import sys
import threading
import time
from unittest.mock import patch


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _nondaemon_dbos_threads() -> list[threading.Thread]:
    """Live non-daemon dbos-executor threads (excludes main thread)."""
    return [
        t
        for t in threading.enumerate()
        if not t.daemon
        and t is not threading.main_thread()
        and t.name.startswith("dbos-executor")
    ]


def _cleanup_real_dbos() -> None:
    """Best-effort real teardown so a real-DBOS-launch test doesn't leak into siblings.

    MUST destroy the real DBOS singleton directly (NOT via the possibly-unbuilt
    `dbos_setup.teardown_dbos` helper) — during RED that helper is absent, so
    gating cleanup on it leaks the real non-daemon dbos-executor thread (the
    #275 bug itself) and poisons the DBOS singleton for the next test.
    """
    try:
        from dbos import DBOS  # noqa: PLC0415

        DBOS.destroy()
    except Exception:
        pass
    try:
        from bytedigger_engine.lib import dbos_setup as _dbos_setup_cleanup  # noqa: PLC0415

        _dbos_setup_cleanup._dbos_initialized = False
    except Exception:
        pass


# ---------------------------------------------------------------------------
# AC1 — teardown_dbos exists and is callable
# ---------------------------------------------------------------------------


def test_ac1_teardown_dbos_exists_and_is_callable():
    """AC1: dbos_setup.teardown_dbos exists and is callable."""
    from bytedigger_engine.lib import dbos_setup as dbos_setup  # noqa: PLC0415

    assert hasattr(dbos_setup, "teardown_dbos"), (
        "dbos_setup.teardown_dbos does not exist — GREEN must add this helper (spec §2.1)"
    )
    assert callable(dbos_setup.teardown_dbos), "dbos_setup.teardown_dbos exists but is not callable"


# ---------------------------------------------------------------------------
# AC2 — no-op (no raise, no DBOS.destroy call) when _dbos_initialized is False
# ---------------------------------------------------------------------------


def test_ac2_teardown_dbos_noops_when_not_initialized():
    """AC2: teardown_dbos() is a no-op when _dbos_initialized is False."""
    from bytedigger_engine.lib import dbos_setup as dbos_setup  # noqa: PLC0415
    from dbos import DBOS  # noqa: PLC0415

    assert hasattr(dbos_setup, "teardown_dbos"), (
        "dbos_setup.teardown_dbos does not exist — GREEN must add this helper (spec §2.1)"
    )

    original_flag = dbos_setup._dbos_initialized
    dbos_setup._dbos_initialized = False
    try:
        with patch.object(DBOS, "destroy") as mock_destroy:
            dbos_setup.teardown_dbos()
        mock_destroy.assert_not_called()
    finally:
        dbos_setup._dbos_initialized = original_flag


# ---------------------------------------------------------------------------
# AC3 — with _dbos_initialized=True, calls DBOS.destroy() once and resets flag
# ---------------------------------------------------------------------------


def test_ac3_teardown_dbos_calls_destroy_once_and_resets_flag():
    """AC3: teardown_dbos() calls DBOS.destroy() exactly once and resets the flag to False."""
    from bytedigger_engine.lib import dbos_setup as dbos_setup  # noqa: PLC0415
    from dbos import DBOS  # noqa: PLC0415

    assert hasattr(dbos_setup, "teardown_dbos"), (
        "dbos_setup.teardown_dbos does not exist — GREEN must add this helper (spec §2.1)"
    )

    original_flag = dbos_setup._dbos_initialized
    dbos_setup._dbos_initialized = True
    try:
        with patch.object(DBOS, "destroy") as mock_destroy:
            dbos_setup.teardown_dbos()
        mock_destroy.assert_called_once()
        assert dbos_setup._dbos_initialized is False, (
            "teardown_dbos() must reset _dbos_initialized to False after DBOS.destroy()"
        )
    finally:
        dbos_setup._dbos_initialized = original_flag


# ---------------------------------------------------------------------------
# AC4 — fail-soft: DBOS.destroy raising does NOT propagate, flag still reset
# ---------------------------------------------------------------------------


def test_ac4_teardown_dbos_fail_soft_on_destroy_raise():
    """AC4: teardown_dbos() swallows a raising DBOS.destroy() and still resets the flag."""
    from bytedigger_engine.lib import dbos_setup as dbos_setup  # noqa: PLC0415
    from dbos import DBOS  # noqa: PLC0415

    assert hasattr(dbos_setup, "teardown_dbos"), (
        "dbos_setup.teardown_dbos does not exist — GREEN must add this helper (spec §2.1)"
    )

    original_flag = dbos_setup._dbos_initialized
    dbos_setup._dbos_initialized = True
    try:
        with patch.object(DBOS, "destroy", side_effect=RuntimeError("boom")):
            try:
                dbos_setup.teardown_dbos()
            except Exception as exc:  # noqa: BLE001
                raise AssertionError(
                    f"teardown_dbos() propagated {type(exc).__name__}: {exc!r}; must be fail-soft"
                ) from exc
        assert dbos_setup._dbos_initialized is False, (
            "teardown_dbos() must reset _dbos_initialized to False even when DBOS.destroy() raises"
        )
    finally:
        dbos_setup._dbos_initialized = original_flag


# ---------------------------------------------------------------------------
# AC5 — (forcing fn, behavioral) real DBOS launch: non-daemon executor thread
#        exists after init+execute, gone after teardown_dbos()
# ---------------------------------------------------------------------------


def test_ac5_teardown_dbos_stops_real_nondaemon_executor_thread(tmp_path, monkeypatch):
    """AC5: real DBOS launch produces a non-daemon dbos-executor thread; teardown_dbos() clears it."""
    import argparse
    import dataclasses

    from bytedigger_engine.lib import dbos_setup as dbos_setup  # noqa: PLC0415

    assert hasattr(dbos_setup, "teardown_dbos"), (
        "dbos_setup.teardown_dbos does not exist — GREEN must add this helper (spec §2.1)"
    )

    from bytedigger_engine import run  # noqa: PLC0415 — only used to build ctx the same way run.py does

    db_path = tmp_path / "dbos.sqlite"
    monkeypatch.setenv("HAL_DBOS_DB_PATH", str(db_path))
    # GH839 B1 env-pin: this test exercises the real DBOS launch/teardown path,
    # which must keep working after HAL_ENGINE_DURABLE_BACKEND's default flips
    # to "native" (§2.3.5).
    monkeypatch.setenv("HAL_ENGINE_DURABLE_BACKEND", "dbos")

    try:
        ns = argparse.Namespace(
            ctx=None,
            ctx_json=json.dumps({"org_config": {}, "question": "ac5"}),
        )
        ctx_obj = run.parse_ctx(ns)
        ctx_dict = dataclasses.asdict(ctx_obj)

        dbos_setup.init_dbos()
        dbos_setup.execute_durable_workflow("echo", ctx_dict, "red275ac5")

        before = _nondaemon_dbos_threads()
        assert len(before) >= 1, (
            "expected >=1 non-daemon dbos-executor thread after real DBOS launch+execute; "
            f"got {before!r} (all threads={[t.name for t in threading.enumerate()]!r})"
        )

        dbos_setup.teardown_dbos()
        time.sleep(1.5)  # let the executor thread join

        after = _nondaemon_dbos_threads()
        assert after == [], (
            f"expected zero non-daemon dbos-executor threads after teardown_dbos(); got {after!r}"
        )
    finally:
        _cleanup_real_dbos()


# ---------------------------------------------------------------------------
# AC6 — (wiring, forcing fn) run.main() invokes teardown_dbos on the success path
# ---------------------------------------------------------------------------


def test_ac6_run_main_invokes_teardown_dbos_on_success_path(tmp_path, monkeypatch):
    """AC6: run.main() --workflow echo path invokes teardown_dbos. Fails on current code (no call)."""
    from bytedigger_engine import run  # noqa: PLC0415 — UUT

    db_path = tmp_path / "dbos.sqlite"
    monkeypatch.setenv("HAL_DBOS_DB_PATH", str(db_path))
    # GH839 B1 env-pin: this test exercises the real DBOS launch/teardown path,
    # which must keep working after HAL_ENGINE_DURABLE_BACKEND's default flips
    # to "native" (§2.3.5).
    monkeypatch.setenv("HAL_ENGINE_DURABLE_BACKEND", "dbos")

    argv = [
        "run.py",
        "--workflow", "echo",
        "--ctx-json", json.dumps({"org_config": {}, "question": "ac6"}),
        "--run-id", "red275ac6",
    ]
    monkeypatch.setattr(sys, "argv", argv)

    try:
        with patch.object(run, "teardown_dbos", create=True) as mock_teardown:
            rc = run.main()
        assert rc == 0, f"expected rc=0 for a healthy echo workflow, got {rc}"
        mock_teardown.assert_called_once()
    finally:
        _cleanup_real_dbos()


# ---------------------------------------------------------------------------
# AC7 — run.main() invokes teardown_dbos even when execute_durable_workflow raises
# ---------------------------------------------------------------------------


def test_ac7_run_main_invokes_teardown_dbos_when_workflow_raises(tmp_path, monkeypatch):
    """AC7: run.main() still invokes teardown_dbos on the error path (proves the `finally`)."""
    from bytedigger_engine import run  # noqa: PLC0415 — UUT

    db_path = tmp_path / "dbos.sqlite"
    monkeypatch.setenv("HAL_DBOS_DB_PATH", str(db_path))
    # GH839 B1 env-pin: this test exercises the real DBOS launch/teardown path,
    # which must keep working after HAL_ENGINE_DURABLE_BACKEND's default flips
    # to "native" (§2.3.5).
    monkeypatch.setenv("HAL_ENGINE_DURABLE_BACKEND", "dbos")

    argv = [
        "run.py",
        "--workflow", "echo",
        "--ctx-json", json.dumps({"org_config": {}, "question": "ac7"}),
        "--run-id", "red275ac7",
    ]
    monkeypatch.setattr(sys, "argv", argv)

    try:
        with patch.object(run, "teardown_dbos", create=True) as mock_teardown, patch.object(
            run, "execute_durable_workflow", side_effect=RuntimeError("boom")
        ):
            rc = run.main()
        assert rc == 2, f"expected rc=2 (E_RUNNER last-resort) when execute_durable_workflow raises, got {rc}"
        mock_teardown.assert_called_once()
    finally:
        _cleanup_real_dbos()


# ---------------------------------------------------------------------------
# AC8 — non-DBOS early path (--list) returns 0 without calling teardown_dbos
# ---------------------------------------------------------------------------


def test_ac8_run_main_list_path_does_not_call_teardown_dbos(monkeypatch):
    """AC8: --list never launches the executor, so teardown_dbos is not invoked."""
    from bytedigger_engine import run  # noqa: PLC0415 — UUT

    argv = ["run.py", "--list"]
    monkeypatch.setattr(sys, "argv", argv)

    with patch.object(run, "teardown_dbos", create=True) as mock_teardown:
        rc = run.main()

    assert rc == 0, f"expected rc=0 for --list, got {rc}"
    mock_teardown.assert_not_called()
