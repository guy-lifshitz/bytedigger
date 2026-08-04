"""RED tests for GH612 Ship A — durable-backend seam + native `phase_sentinel`.

Spec: SHARED/memory/Decisions/2026-07-14_GH612_dbos_backend_seam_spec.md (FROZEN)

Pre-GREEN expected profile (gate cycle 3 — AC23 replaced by AC23a/AC23b; AC25-28 added):
  FAIL: AC1, AC2, AC3, AC5, AC6, AC8-AC28 except AC4  (lib/phase_sentinel.py does not
        exist; init_dbos has no backend gate; execute_durable_workflow has no native
        branch)
  PASS (parity pin — guards the default DBOS path against regression): AC4

§1q / D1CF5FDF: `lib.phase_sentinel` DOES NOT EXIST yet, so it is NEVER imported at
module level. Every reference lives INSIDE a test body — the module COLLECTS cleanly
and each test FAILS at ImportError-inside-test / assert time, never at collect time.
No `spec_from_file_location` / `exec_module`. No module-level `sys.path` mutation —
`tests/conftest.py` already exposes engine_py root + `workflows/` on sys.path
(conftest-import-time singleton, §1q / 81F97F3D).

§1l forcing function: ACs 8/10/12/13/14/17 anchor on a REAL on-disk counter file
incremented by a REAL engine step running through `WorkflowEngine.execute()`. A stub
`execute_native_workflow` that fabricates a result dict physically cannot move the
counter, so it cannot satisfy "counter == 1 after two calls" / "counter == 2 after a
failure retry".

Spy carve-out (mirrors test_dbos_zombie_root_gh748.py:190): AC4 spies on
`dbos_setup._dbos_execute_wrapper` (the internal @DBOS.workflow shell) and AC6 spies on
the third-party `DBOS` constructor. Both are INFRA, not UUTs of this ship. No UUT symbol
(`resolve_durable_backend`, `phase_key`, `execute_engine`, `_sentinel_path`,
`load_cached_success`, `store_success`, `execute_native_workflow`,
`execute_durable_workflow`, `init_dbos`) is ever mocked or patched.
"""
from __future__ import annotations

import importlib
import json
import os
import sqlite3
from os.path import realpath
from pathlib import Path
from typing import Any

import pytest

# Skip the whole module if dbos is absent (lib.dbos_setup imports it at module level).
try:
    import dbos as dbos_module  # type: ignore
    from dbos import DBOS  # type: ignore
except ImportError:  # pragma: no cover
    pytest.skip("dbos not installed", allow_module_level=True)

from bytedigger_engine.lib import dbos_setup as dbos_setup  # type: ignore[import]  # attr access only — never patched
from bytedigger_engine import telemetry_ctx  # type: ignore[import]
from bytedigger_engine.event_log import EventLog  # type: ignore[import]

_ENV = "HAL_ENGINE_DURABLE_BACKEND"

_MINIMAL_CTX_DICT: dict[str, Any] = {
    "tenant_id": "hal-test",
    "scope": None,
    "db_path": None,
    "org_config": {"complexity": "SIMPLE", "phase": "spec"},
    "question": "gh612 seam test",
    "session_id": "gh612-test",
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


# ---------------------------------------------------------------------------
# Autouse isolation (no global state leaks between tests)
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _env_isolation(monkeypatch):
    monkeypatch.delenv(_ENV, raising=False)
    monkeypatch.delenv("HAL_DBOS_DB_PATH", raising=False)
    monkeypatch.delenv("HAL_DIR", raising=False)
    yield


@pytest.fixture(autouse=True)
def _dbos_cleanup():
    yield
    try:
        DBOS.destroy()
    except Exception:
        pass
    dbos_setup._dbos_initialized = False


@pytest.fixture(autouse=True)
def _telemetry_reset():
    telemetry_ctx.clear_current_run()
    yield
    telemetry_ctx.clear_current_run()


# ---------------------------------------------------------------------------
# On-disk counter (§1l forcing function) + real counter-workflow registration
# ---------------------------------------------------------------------------

def _read_counter(counter_path: Path) -> int:
    if not counter_path.exists():
        return 0
    return int(counter_path.read_text().strip())


def _bump_counter(counter_path: Path) -> None:
    counter_path.write_text(str(_read_counter(counter_path) + 1))


def _register_counter_workflow(monkeypatch, name: str, counter_path: Path, *, mode: str = "ok") -> None:
    """Register a REAL engine workflow whose step increments an on-disk counter.

    Wraps the production `workflows.register_all` (the exact hook both
    `_dbos_execute_wrapper` and the new `execute_engine` call), so the step only
    ever runs when the production engine really executes it.
    """
    from bytedigger_engine import workflows as workflows_pkg  # type: ignore
    from bytedigger_engine.contracts import StepContract, StepResult, WorkflowDefinition  # type: ignore

    def _step(_ctx, _prev):
        _bump_counter(counter_path)
        if mode == "raise":
            raise RuntimeError("gh612-boom")
        if mode == "error":
            return StepResult(
                status="error", data=None, duration_ms=0, step_name=name,
                error="gh612 boom", error_code="E_BOOM", recoverable=False,
            )
        if mode == "escalate":
            return StepResult(
                status="escalate", data=None, duration_ms=0, step_name=name,
                error=None, error_code=None, recoverable=True,
            )
        if mode == "skip":
            return StepResult(status="skip", data={"echoed": name}, duration_ms=0, step_name=name)
        return StepResult(status="ok", data={"echoed": name}, duration_ms=0, step_name=name)

    orig_register_all = workflows_pkg.register_all

    def _patched_register_all(engine):
        orig_register_all(engine)
        engine.register(name, WorkflowDefinition(name=name, steps=[StepContract(name=name, execute=_step)]))

    monkeypatch.setattr(workflows_pkg, "register_all", _patched_register_all)


def _event_log_path(tmp_path: Path) -> str:
    real_tmp = Path(realpath(str(tmp_path)))  # §1j macOS /var-symlink guard
    d = real_tmp / ".hal-build"
    d.mkdir(parents=True, exist_ok=True)
    return str(d / "events.jsonl")


def _set_run(event_log_path: str, run_id: str) -> None:
    telemetry_ctx.set_current_run(
        event_log=EventLog(event_log_path), run_id=run_id, step_name="gh612_seam"
    )


# ===========================================================================
# Seam / default-path parity
# ===========================================================================

def test_ac1_backend_defaults_to_native_when_env_unset_or_empty_gh612(monkeypatch):
    """AC1 (GH839 B1 flip): resolve_durable_backend() returns "native" when
    HAL_ENGINE_DURABLE_BACKEND is unset AND when it is set to "".

    Pre-GREEN: FAIL — lib.phase_sentinel does not exist (ImportError).
    """
    from bytedigger_engine.lib.phase_sentinel import resolve_durable_backend  # type: ignore[import]

    monkeypatch.delenv(_ENV, raising=False)
    assert resolve_durable_backend() == "native", (
        "AC1 (GH612/GH839 B1): unset HAL_ENGINE_DURABLE_BACKEND must resolve to the "
        "'native' default post-flip."
    )

    monkeypatch.setenv(_ENV, "")
    assert resolve_durable_backend() == "native", (
        "AC1 (GH612/GH839 B1): empty HAL_ENGINE_DURABLE_BACKEND must resolve to the "
        "'native' default post-flip."
    )


def test_ac2_backend_resolves_explicit_native_and_dbos_gh612(monkeypatch):
    """AC2: resolve_durable_backend() returns "native" for "native" and "dbos" for "dbos".

    Pre-GREEN: FAIL — lib.phase_sentinel does not exist (ImportError).
    """
    from bytedigger_engine.lib.phase_sentinel import resolve_durable_backend  # type: ignore[import]

    monkeypatch.setenv(_ENV, "native")
    assert resolve_durable_backend() == "native", "AC2 (GH612): 'native' must resolve to 'native'."

    monkeypatch.setenv(_ENV, "dbos")
    assert resolve_durable_backend() == "dbos", "AC2 (GH612): 'dbos' must resolve to 'dbos'."


def test_ac3_unknown_backend_fails_closed_gh612(monkeypatch):
    """AC3: an unknown value ("sqlite") raises E_DURABLE_BACKEND_UNKNOWN whose message
    names the offending value AND both legal values (§2.4 fail-closed — no silent
    fallback to dbos).

    Pre-GREEN: FAIL — lib.phase_sentinel (and E_DURABLE_BACKEND_UNKNOWN) does not exist.
    """
    from bytedigger_engine.lib.phase_sentinel import (  # type: ignore[import]
        E_DURABLE_BACKEND_UNKNOWN,
        resolve_durable_backend,
    )

    monkeypatch.setenv(_ENV, "sqlite")
    with pytest.raises(E_DURABLE_BACKEND_UNKNOWN) as exc:
        resolve_durable_backend()

    msg = str(exc.value)
    assert "sqlite" in msg, f"AC3 (GH612): message must name the offending value; got {msg!r}"
    assert "dbos" in msg and "native" in msg, (
        f"AC3 (GH612): message must name BOTH legal values ('dbos', 'native'); got {msg!r}"
    )
    assert isinstance(exc.value, RuntimeError), (
        "AC3 (GH612): E_DURABLE_BACKEND_UNKNOWN must subclass RuntimeError so run.py:207 "
        "DEFERs it to E_RUNNER."
    )


def test_ac4_default_path_no_longer_invokes_dbos_wrapper_gh612(tmp_path, monkeypatch):
    """AC4 (GH839 B1 flip — inverted pin): with the env UNSET, execute_durable_workflow
    now takes the NATIVE path — `_dbos_execute_wrapper` is NEVER invoked, and the real
    counter workflow DOES run (counter == 1).

    Spy target is `dbos_setup._dbos_execute_wrapper` — the internal @DBOS.workflow shell,
    i.e. INFRA, not a UUT of this ship (GH748 precedent, that file's line ~190 comment).

    Pre-GREEN: FAIL — the default still resolves to 'dbos' (phase_sentinel.py not yet
    flipped), so _dbos_execute_wrapper is still invoked and the counter stays 0.
    """
    real_tmp = Path(realpath(str(tmp_path)))
    db_path = real_tmp / "dbos.sqlite"
    monkeypatch.setenv("HAL_DBOS_DB_PATH", str(db_path))
    monkeypatch.delenv(_ENV, raising=False)

    event_log_path = _event_log_path(tmp_path)
    counter = real_tmp / "counter-ac4.txt"
    _register_counter_workflow(monkeypatch, "gh612_ac4_counter", counter)

    calls: list[tuple] = []

    def _spy_wrapper(workflow_name, context_dict, run_id, event_log_path=None):
        calls.append((workflow_name, run_id))
        return {
            "status": "ok", "data": {"spied": True}, "duration_ms": 0,
            "step_name": workflow_name, "error": None, "error_code": None,
            "suggestion": None, "recoverable": True,
        }

    monkeypatch.setattr(dbos_setup, "_dbos_execute_wrapper", _spy_wrapper)

    dbos_setup.init_dbos(db_path)
    _set_run(event_log_path, "gh612-ac4-run")

    result = dbos_setup.execute_durable_workflow(
        "gh612_ac4_counter", dict(_MINIMAL_CTX_DICT), "gh612-ac4-run", event_log_path=event_log_path
    )

    assert calls == [], (
        f"AC4 (GH612/GH839 B1): with HAL_ENGINE_DURABLE_BACKEND unset, execute_durable_workflow "
        f"must NOT invoke _dbos_execute_wrapper post-flip (default = native); got {calls!r}"
    )
    assert result.get("status") == "ok", (
        f"AC4 (GH612/GH839 B1): the native-path result must report status 'ok'; got {result!r}"
    )
    assert _read_counter(counter) == 1, (
        f"AC4 (GH612/GH839 B1): the native engine-invoke MUST run on the default path — the real "
        f"counter workflow executed {_read_counter(counter)} time(s) (expected 1)."
    )


def test_ac4b_explicit_dbos_still_invokes_dbos_wrapper_gh612(tmp_path, monkeypatch):
    """AC4b (GH839 B1 fallback-reachability sibling — B1 deletes nothing): with
    HAL_ENGINE_DURABLE_BACKEND explicitly set to 'dbos', execute_durable_workflow still
    invokes `_dbos_execute_wrapper` exactly once, and the native engine invoke never
    happens (the real counter workflow never runs -> counter == 0).

    Pre-GREEN: PASS — dbos is today's default, so setting it explicitly already exercises
    this exact path unchanged.
    """
    real_tmp = Path(realpath(str(tmp_path)))
    db_path = real_tmp / "dbos.sqlite"
    monkeypatch.setenv("HAL_DBOS_DB_PATH", str(db_path))
    monkeypatch.setenv(_ENV, "dbos")

    event_log_path = _event_log_path(tmp_path)
    counter = real_tmp / "counter-ac4b.txt"
    _register_counter_workflow(monkeypatch, "gh612_ac4b_counter", counter)

    calls: list[tuple] = []

    def _spy_wrapper(workflow_name, context_dict, run_id, event_log_path=None):
        calls.append((workflow_name, run_id))
        return {
            "status": "ok", "data": {"spied": True}, "duration_ms": 0,
            "step_name": workflow_name, "error": None, "error_code": None,
            "suggestion": None, "recoverable": True,
        }

    monkeypatch.setattr(dbos_setup, "_dbos_execute_wrapper", _spy_wrapper)

    dbos_setup.init_dbos(db_path)
    _set_run(event_log_path, "gh612-ac4b-run")

    result = dbos_setup.execute_durable_workflow(
        "gh612_ac4b_counter", dict(_MINIMAL_CTX_DICT), "gh612-ac4b-run", event_log_path=event_log_path
    )

    assert len(calls) == 1, (
        f"AC4b (GH839 B1): explicit HAL_ENGINE_DURABLE_BACKEND=dbos must still invoke "
        f"_dbos_execute_wrapper EXACTLY once; got {len(calls)} call(s)."
    )
    assert result.get("data") == {"spied": True}, (
        f"AC4b (GH839 B1): the DBOS-path result must be returned unchanged; got {result!r}"
    )
    assert _read_counter(counter) == 0, (
        f"AC4b (GH839 B1): the native engine-invoke must NOT run under explicit dbos — real "
        f"counter workflow executed {_read_counter(counter)} time(s) (expected 0)."
    )


def test_ac5_phase_key_byte_exact_and_reexports_intact_gh612():
    """AC5: phase_key(run_id, "echo", ctx) == f"{run_id}__echo__{_ctx_cfg_sha8(ctx)}"
    byte-for-byte, and _PHASE_UUID_SEP / _CTX_HASH_LEN / _ctx_cfg_sha8 stay importable
    from lib.dbos_setup (re-export intact ⇒ dbos_list_runs / dbos_status_run + 4 test
    modules unbroken).

    Pre-GREEN: FAIL — lib.phase_sentinel.phase_key does not exist (ImportError).
    """
    from bytedigger_engine.lib.phase_sentinel import phase_key  # type: ignore[import]
    from bytedigger_engine.lib.dbos_setup import _CTX_HASH_LEN, _PHASE_UUID_SEP, _ctx_cfg_sha8  # re-export contract

    ctx = dict(_MINIMAL_CTX_DICT)
    run_id = "gh612-ac5-run"
    expected = f"{run_id}{_PHASE_UUID_SEP}echo{_PHASE_UUID_SEP}{_ctx_cfg_sha8(ctx)}"

    assert phase_key(run_id, "echo", ctx) == expected, (
        f"AC5 (GH612): phase_key must be byte-identical to today's inline f-string "
        f"(dbos_setup.py:426) — run-phase.ts:57 correlation + the 941B33FC uuid-regex test "
        f"depend on it. Expected {expected!r}."
    )
    assert _PHASE_UUID_SEP == "__" and _CTX_HASH_LEN == 8, (
        "AC5 (GH612): the re-exported constants must keep their values "
        f"(_PHASE_UUID_SEP={_PHASE_UUID_SEP!r}, _CTX_HASH_LEN={_CTX_HASH_LEN!r})."
    )


def test_ac6_native_init_dbos_does_not_launch_dbos_gh612(tmp_path, monkeypatch):
    """AC6: under `native`, init_dbos() early-returns — dbos_setup._dbos_initialized stays
    False and the DBOS constructor is NEVER called (no sqlite store, no launch, no
    non-daemon executor thread, no startup-recovery replay).

    Spy target is the THIRD-PARTY dbos.DBOS class — infra, not the UUT (GH748 precedent).

    Pre-GREEN: FAIL — init_dbos has no backend gate today; it constructs + launches DBOS
    unconditionally, so the constructor fires and _dbos_initialized flips True.
    """
    real_tmp = Path(realpath(str(tmp_path)))
    db_path = real_tmp / "dbos.sqlite"
    monkeypatch.setenv("HAL_DBOS_DB_PATH", str(db_path))
    monkeypatch.setenv(_ENV, "native")

    ctor_calls: list[dict] = []
    orig_init = dbos_module.DBOS.__init__

    def _spy_init(self, *args, **kwargs):
        ctor_calls.append(kwargs.get("config") or {})
        return orig_init(self, *args, **kwargs)

    monkeypatch.setattr(dbos_module.DBOS, "__init__", _spy_init)

    dbos_setup._dbos_initialized = False
    dbos_setup.init_dbos(db_path)

    assert ctor_calls == [], (
        f"AC6 (GH612): under HAL_ENGINE_DURABLE_BACKEND=native, init_dbos() must early-return "
        f"BEFORE constructing DBOS; the DBOS constructor was called {len(ctor_calls)} time(s)."
    )
    assert dbos_setup._dbos_initialized is False, (
        "AC6 (GH612): under `native`, init_dbos() must not set _dbos_initialized "
        "(nothing was launched)."
    )


def test_ac7_native_teardown_dbos_is_silent_noop_gh612(tmp_path, monkeypatch):
    """AC7: under `native`, teardown_dbos() after init_dbos() is a silent no-op — it does
    not raise and _dbos_initialized is still False (the unchanged :161 guard holds; the
    AC asserts this rather than editing teardown_dbos).

    Pre-GREEN: FAIL — init_dbos has no native gate, so it really launches DBOS and sets
    _dbos_initialized=True; the post-teardown assertion that it was never True fails.
    """
    real_tmp = Path(realpath(str(tmp_path)))
    db_path = real_tmp / "dbos.sqlite"
    monkeypatch.setenv("HAL_DBOS_DB_PATH", str(db_path))
    monkeypatch.setenv(_ENV, "native")

    dbos_setup._dbos_initialized = False
    dbos_setup.init_dbos(db_path)

    assert dbos_setup._dbos_initialized is False, (
        "AC7 (GH612): under `native`, init_dbos() must leave _dbos_initialized False."
    )

    dbos_setup.teardown_dbos()  # must not raise

    assert dbos_setup._dbos_initialized is False, (
        "AC7 (GH612): teardown_dbos() under `native` must be a silent no-op and leave "
        "_dbos_initialized False."
    )


# ===========================================================================
# Native execute + §1l forcing function (real on-disk counter)
# ===========================================================================

def test_ac8_native_fresh_execute_runs_engine_once_gh612(tmp_path, monkeypatch):
    """AC8 (§1ab-a FRESH): under `native`, the FIRST execute_native_workflow for a key
    really executes the engine — the REAL counter workflow leaves counter == 1 — and the
    returned dict has status == "ok", exactly the 8 contract keys, and NO 'metadata'.

    §1l: the counter is incremented only by a real StepContract running inside
    WorkflowEngine.execute(); a stub cannot move it.

    Pre-GREEN: FAIL — lib.phase_sentinel does not exist (ImportError).
    """
    from bytedigger_engine.lib.phase_sentinel import execute_native_workflow, phase_key  # type: ignore[import]

    monkeypatch.setenv(_ENV, "native")
    real_tmp = Path(realpath(str(tmp_path)))
    event_log_path = _event_log_path(tmp_path)
    counter = real_tmp / "counter-ac8.txt"
    _register_counter_workflow(monkeypatch, "gh612_ac8_counter", counter)

    ctx = dict(_MINIMAL_CTX_DICT)
    run_id = "gh612-ac8-run"
    _set_run(event_log_path, run_id)
    key = phase_key(run_id, "gh612_ac8_counter", ctx)

    result = execute_native_workflow("gh612_ac8_counter", ctx, run_id, event_log_path, key)

    assert _read_counter(counter) == 1, (
        f"AC8 (GH612): the native path must really execute the engine once; the real "
        f"on-disk counter is {_read_counter(counter)!r}, expected 1."
    )
    assert result["status"] == "ok", f"AC8 (GH612): expected status 'ok'; got {result!r}"
    assert set(result.keys()) == {
        "status", "data", "duration_ms", "step_name", "error",
        "error_code", "suggestion", "recoverable",
    }, (
        f"AC8 (GH612): the native result must carry exactly the 8 pickle-clean contract keys "
        f"and NO 'metadata' (run.py restores it via default_factory); got {sorted(result)!r}"
    )


def test_ac9_sentinel_written_on_success_gh612(tmp_path, monkeypatch):
    """AC9: after a successful native execute, a sentinel exists at
    <event_log_dir>/phase-sentinels/<key>.json and its JSON 'status' is "ok".

    Pre-GREEN: FAIL — lib.phase_sentinel does not exist (ImportError).
    """
    from bytedigger_engine.lib.phase_sentinel import execute_native_workflow, phase_key  # type: ignore[import]

    monkeypatch.setenv(_ENV, "native")
    real_tmp = Path(realpath(str(tmp_path)))
    event_log_path = _event_log_path(tmp_path)
    counter = real_tmp / "counter-ac9.txt"
    _register_counter_workflow(monkeypatch, "gh612_ac9_counter", counter)

    ctx = dict(_MINIMAL_CTX_DICT)
    run_id = "gh612-ac9-run"
    _set_run(event_log_path, run_id)
    key = phase_key(run_id, "gh612_ac9_counter", ctx)

    execute_native_workflow("gh612_ac9_counter", ctx, run_id, event_log_path, key)

    sentinel = Path(event_log_path).parent / "phase-sentinels" / f"{key}.json"
    assert sentinel.exists(), (
        f"AC9 (GH612): store_success must write the sentinel at {sentinel}; it does not exist."
    )
    payload = json.loads(sentinel.read_text())
    assert payload.get("status") == "ok", (
        f"AC9 (GH612): the sentinel payload's status must be 'ok'; got {payload!r}"
    )


def test_ac10_second_call_serves_cache_engine_not_reexecuted_gh612(tmp_path, monkeypatch):
    """AC10 (§1ab-c AUTO-RESUME, same run-id + idempotency): a SECOND native call with the
    same run_id/workflow/ctx SERVES THE CACHE — the on-disk counter stays 1 (the engine is
    NOT re-executed) and the returned dict equals the first one.

    §1l: only a real cache-serve keeps the real counter at 1; a stub that re-executes (or
    that never executed) cannot produce 1-then-still-1.

    Pre-GREEN: FAIL — lib.phase_sentinel does not exist (ImportError).
    """
    from bytedigger_engine.lib.phase_sentinel import execute_native_workflow, phase_key  # type: ignore[import]

    monkeypatch.setenv(_ENV, "native")
    real_tmp = Path(realpath(str(tmp_path)))
    event_log_path = _event_log_path(tmp_path)
    counter = real_tmp / "counter-ac10.txt"
    _register_counter_workflow(monkeypatch, "gh612_ac10_counter", counter)

    ctx = dict(_MINIMAL_CTX_DICT)
    run_id = "gh612-ac10-run"
    _set_run(event_log_path, run_id)
    key = phase_key(run_id, "gh612_ac10_counter", ctx)

    first = execute_native_workflow("gh612_ac10_counter", ctx, run_id, event_log_path, key)
    assert _read_counter(counter) == 1, (
        f"AC10 (GH612) pre-stage: the first call must execute the engine exactly once; "
        f"counter={_read_counter(counter)!r}"
    )

    second = execute_native_workflow("gh612_ac10_counter", ctx, run_id, event_log_path, key)

    assert _read_counter(counter) == 1, (
        f"AC10 (GH612): the second call for the same phase_key must SERVE THE CACHE — the "
        f"engine must NOT re-execute. Real on-disk counter is {_read_counter(counter)!r}, "
        f"expected 1."
    )
    assert second == first, (
        f"AC10 (GH612): the cached result must equal the first result; first={first!r} "
        f"second={second!r}"
    )


def test_ac11_cache_survives_module_reload_gh612(tmp_path, monkeypatch):
    """AC11 (§1ab-d CROSS-PROCESS replay analogue): the sentinel cache survives a module
    state reset — after importlib.reload(lib.phase_sentinel) (fresh module globals, fresh
    WorkflowEngine on the next invoke), a call with the same key STILL serves the cache and
    the real counter stays 1. This is the file store's stand-in for DBOS startup-recovery.

    Pre-GREEN: FAIL — lib.phase_sentinel does not exist (ImportError).
    """
    from bytedigger_engine.lib import phase_sentinel as ps  # type: ignore[import]

    monkeypatch.setenv(_ENV, "native")
    real_tmp = Path(realpath(str(tmp_path)))
    event_log_path = _event_log_path(tmp_path)
    counter = real_tmp / "counter-ac11.txt"
    _register_counter_workflow(monkeypatch, "gh612_ac11_counter", counter)

    ctx = dict(_MINIMAL_CTX_DICT)
    run_id = "gh612-ac11-run"
    _set_run(event_log_path, run_id)
    key = ps.phase_key(run_id, "gh612_ac11_counter", ctx)

    first = ps.execute_native_workflow("gh612_ac11_counter", ctx, run_id, event_log_path, key)
    assert _read_counter(counter) == 1, (
        f"AC11 (GH612) pre-stage: first call must execute the engine once; "
        f"counter={_read_counter(counter)!r}"
    )

    ps_reloaded = importlib.reload(ps)  # wipe every in-memory module global
    _set_run(event_log_path, run_id)

    replayed = ps_reloaded.execute_native_workflow(
        "gh612_ac11_counter", ctx, run_id, event_log_path, key
    )

    assert _read_counter(counter) == 1, (
        f"AC11 (GH612): after a module-state reset the FILE store must still serve the cache "
        f"(no in-memory state may be load-bearing); real counter={_read_counter(counter)!r}, "
        f"expected 1."
    )
    assert replayed == first, (
        f"AC11 (GH612): the post-reload replay must return the cached result; got {replayed!r}"
    )


# ===========================================================================
# The class this ship closes — sticky ERROR
# ===========================================================================

def test_ac12_error_result_not_cached_and_retry_reexecutes_gh612(tmp_path, monkeypatch):
    """AC12 (§1ab-b IN-PHASE RETRY after failure): a workflow whose step returns
    status="error" (error_code="E_BOOM") writes NO sentinel file, and a second call with
    the SAME key RE-EXECUTES the engine (real counter == 2) rather than replaying the
    failure. This is the #299/#603/#611 sticky-ERROR class, dead by construction.

    §1l: a stub cannot produce "counter == 2" — only a real second engine execution does.

    Pre-GREEN: FAIL — lib.phase_sentinel does not exist (ImportError).
    """
    from bytedigger_engine.lib.phase_sentinel import execute_native_workflow, phase_key  # type: ignore[import]

    monkeypatch.setenv(_ENV, "native")
    real_tmp = Path(realpath(str(tmp_path)))
    event_log_path = _event_log_path(tmp_path)
    counter = real_tmp / "counter-ac12.txt"
    _register_counter_workflow(monkeypatch, "gh612_ac12_counter", counter, mode="error")

    ctx = dict(_MINIMAL_CTX_DICT)
    run_id = "gh612-ac12-run"
    _set_run(event_log_path, run_id)
    key = phase_key(run_id, "gh612_ac12_counter", ctx)

    first = execute_native_workflow("gh612_ac12_counter", ctx, run_id, event_log_path, key)
    assert first["status"] == "error" and first["error_code"] == "E_BOOM", (
        f"AC12 (GH612) pre-stage: the failing workflow must return the error result; got {first!r}"
    )

    sentinel = Path(event_log_path).parent / "phase-sentinels" / f"{key}.json"
    assert not sentinel.exists(), (
        f"AC12 (GH612): a FAILED result must NEVER be cached — store_success is success-only. "
        f"Found a sentinel at {sentinel}."
    )

    execute_native_workflow("gh612_ac12_counter", ctx, run_id, event_log_path, key)

    assert _read_counter(counter) == 2, (
        f"AC12 (GH612): the retry after a failure must RE-EXECUTE the engine (the sticky-ERROR "
        f"class is impossible when the store cannot hold a failure); real counter="
        f"{_read_counter(counter)!r}, expected 2."
    )


def test_ac13_raising_step_propagates_and_caches_nothing_gh612(tmp_path, monkeypatch):
    """AC13: a step that RAISES propagates the exception unchanged (execute_engine has no
    try/except; store_success is never reached), writes no sentinel, and the next call
    re-executes the engine (real counter == 2).

    Pre-GREEN: FAIL — lib.phase_sentinel does not exist (ImportError).
    """
    from bytedigger_engine.lib.phase_sentinel import execute_native_workflow, phase_key  # type: ignore[import]

    monkeypatch.setenv(_ENV, "native")
    real_tmp = Path(realpath(str(tmp_path)))
    event_log_path = _event_log_path(tmp_path)
    counter = real_tmp / "counter-ac13.txt"
    _register_counter_workflow(monkeypatch, "gh612_ac13_counter", counter, mode="raise")

    ctx = dict(_MINIMAL_CTX_DICT)
    run_id = "gh612-ac13-run"
    _set_run(event_log_path, run_id)
    key = phase_key(run_id, "gh612_ac13_counter", ctx)

    with pytest.raises(RuntimeError, match="gh612-boom"):
        execute_native_workflow("gh612_ac13_counter", ctx, run_id, event_log_path, key)

    sentinel = Path(event_log_path).parent / "phase-sentinels" / f"{key}.json"
    assert not sentinel.exists(), (
        f"AC13 (GH612): a raising step must leave NO sentinel behind; found {sentinel}."
    )

    with pytest.raises(RuntimeError, match="gh612-boom"):
        execute_native_workflow("gh612_ac13_counter", ctx, run_id, event_log_path, key)

    assert _read_counter(counter) == 2, (
        f"AC13 (GH612): after a raise, the next call must RE-EXECUTE the engine; real counter="
        f"{_read_counter(counter)!r}, expected 2."
    )


# ===========================================================================
# Stale-serve / corruption guards
# ===========================================================================

def test_ac14_corrected_ctx_does_not_serve_stale_gh612(tmp_path, monkeypatch):
    """AC14: same run_id + workflow but a MUTATED org_config yields a DIFFERENT phase_key
    ⇒ cache MISS ⇒ the engine really re-executes (real counter == 2). Driven end-to-end
    through the production entry point `execute_durable_workflow` under `native` (proving
    the §2.3 seam branch is wired), mirroring
    test_ac12_corrected_ctx_does_not_replay_failure_941B33FC on the native path.

    Pre-GREEN: FAIL — execute_durable_workflow has no native branch, so it takes the DBOS
    path (uninitialized DBOS) instead of the phase_sentinel path; the counter does not
    reach 2 through the native seam.

    §1l: only two REAL engine executions can leave counter == 2.
    """
    from bytedigger_engine.lib.phase_sentinel import phase_key  # type: ignore[import]

    monkeypatch.setenv(_ENV, "native")
    real_tmp = Path(realpath(str(tmp_path)))
    # Test-hygiene fix (gate cycle-3): SET HAL_DBOS_DB_PATH to a tmp path rather than
    # deleting it — a delenv here lets the DBOS pre-flight helpers fall back to
    # _module_default_db_path() (the developer's REAL dbos.sqlite) and issue DELETEs
    # against it if a misordered GREEN reaches them.
    monkeypatch.setenv("HAL_DBOS_DB_PATH", str(real_tmp / "ac14-dbos.sqlite"))
    event_log_path = _event_log_path(tmp_path)
    counter = real_tmp / "counter-ac14.txt"
    _register_counter_workflow(monkeypatch, "gh612_ac14_counter", counter)

    run_id = "gh612-ac14-run"
    _set_run(event_log_path, run_id)

    ctx_a = dict(_MINIMAL_CTX_DICT)
    ctx_a["org_config"] = {"complexity": "SIMPLE", "phase": "spec"}
    ctx_b = dict(_MINIMAL_CTX_DICT)
    ctx_b["org_config"] = {"complexity": "SIMPLE", "phase": "spec", "corrected": True}

    key_a = phase_key(run_id, "gh612_ac14_counter", ctx_a)
    key_b = phase_key(run_id, "gh612_ac14_counter", ctx_b)
    assert key_a != key_b, (
        "AC14 (GH612): a mutated org_config must change the phase_key sha8 suffix; "
        f"both resolved to {key_a!r}."
    )

    dbos_setup.execute_durable_workflow(
        "gh612_ac14_counter", ctx_a, run_id, event_log_path=event_log_path
    )
    assert _read_counter(counter) == 1, (
        f"AC14 (GH612) pre-stage: the first native execute_durable_workflow must run the engine "
        f"once; real counter={_read_counter(counter)!r}"
    )

    dbos_setup.execute_durable_workflow(
        "gh612_ac14_counter", ctx_b, run_id, event_log_path=event_log_path
    )

    assert _read_counter(counter) == 2, (
        f"AC14 (GH612): the corrected-ctx retry must MISS the cache (different key) and really "
        f"re-execute the engine; real counter={_read_counter(counter)!r}, expected 2. A stale "
        f"serve would leave it at 1."
    )


def test_ac15_corrupt_sentinel_is_a_miss_gh612(tmp_path, monkeypatch):
    """AC15 (crash-window / corruption): a sentinel file holding truncated JSON
    ('{"status": "ok"') is treated as a MISS — load_cached_success returns None, does not
    raise, and the engine re-executes (real counter == 1 after the call).

    §1i: the contested sentinel is PRE-STAGED deterministically (hand-written before the
    call); nothing races.

    Pre-GREEN: FAIL — lib.phase_sentinel does not exist (ImportError).
    """
    from bytedigger_engine.lib.phase_sentinel import (  # type: ignore[import]
        _sentinel_path,
        execute_native_workflow,
        load_cached_success,
        phase_key,
    )

    monkeypatch.setenv(_ENV, "native")
    real_tmp = Path(realpath(str(tmp_path)))
    event_log_path = _event_log_path(tmp_path)
    counter = real_tmp / "counter-ac15.txt"
    _register_counter_workflow(monkeypatch, "gh612_ac15_counter", counter)

    ctx = dict(_MINIMAL_CTX_DICT)
    run_id = "gh612-ac15-run"
    _set_run(event_log_path, run_id)
    key = phase_key(run_id, "gh612_ac15_counter", ctx)

    # Pre-stage a truncated (crash-mid-write) sentinel.
    sentinel = _sentinel_path(key, event_log_path)
    assert sentinel is not None, "AC15 (GH612): _sentinel_path must resolve for a real event_log_path."
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_text('{"status": "ok"')

    assert load_cached_success(key, event_log_path) is None, (
        "AC15 (GH612): a malformed/truncated sentinel must be a MISS (None), never a raise."
    )

    result = execute_native_workflow("gh612_ac15_counter", ctx, run_id, event_log_path, key)

    assert result["status"] == "ok" and _read_counter(counter) == 1, (
        f"AC15 (GH612): after a corrupt-sentinel MISS the engine must really re-execute; "
        f"real counter={_read_counter(counter)!r} result={result!r}"
    )


def test_ac16_error_status_sentinel_is_refused_gh612(tmp_path, monkeypatch):
    """AC16: a hand-planted (legacy/hostile) sentinel whose payload status is "error" is
    REFUSED by the reader — load_cached_success returns None (defence-in-depth on the
    reader, not just the success-only writer).

    §1i: the sentinel is PRE-STAGED deterministically; no race.

    Pre-GREEN: FAIL — lib.phase_sentinel does not exist (ImportError).
    """
    from bytedigger_engine.lib.phase_sentinel import _sentinel_path, load_cached_success, phase_key  # type: ignore[import]

    monkeypatch.setenv(_ENV, "native")
    event_log_path = _event_log_path(tmp_path)
    ctx = dict(_MINIMAL_CTX_DICT)
    key = phase_key("gh612-ac16-run", "gh612_ac16_counter", ctx)

    sentinel = _sentinel_path(key, event_log_path)
    assert sentinel is not None, "AC16 (GH612): _sentinel_path must resolve for a real event_log_path."
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_text(json.dumps({
        "status": "error", "data": None, "duration_ms": 1, "step_name": "x",
        "error": "stale failure", "error_code": "E_BOOM", "suggestion": None, "recoverable": False,
    }))

    assert load_cached_success(key, event_log_path) is None, (
        "AC16 (GH612): a sentinel whose payload status is 'error' must be REFUSED (None) — "
        "the reader must not replay a failure even if a hostile/legacy file plants one."
    )


def test_ac17_no_event_log_path_means_no_cache_gh612(tmp_path, monkeypatch):
    """AC17: event_log_path=None ⇒ _sentinel_path is None ⇒ no cache and no crash: the
    engine executes on EVERY call (real counter == 2 after two calls) and no
    'phase-sentinels' directory is created anywhere.

    §1l: only two real engine executions can leave counter == 2.

    Pre-GREEN: FAIL — lib.phase_sentinel does not exist (ImportError).
    """
    from bytedigger_engine.lib.phase_sentinel import _sentinel_path, execute_native_workflow, phase_key  # type: ignore[import]

    monkeypatch.setenv(_ENV, "native")
    real_tmp = Path(realpath(str(tmp_path)))
    counter = real_tmp / "counter-ac17.txt"
    _register_counter_workflow(monkeypatch, "gh612_ac17_counter", counter)

    ctx = dict(_MINIMAL_CTX_DICT)
    run_id = "gh612-ac17-run"
    key = phase_key(run_id, "gh612_ac17_counter", ctx)

    assert _sentinel_path(key, None) is None, (
        "AC17 (GH612): _sentinel_path must be None when event_log_path is falsy (no durable "
        "location ⇒ no cache)."
    )

    execute_native_workflow("gh612_ac17_counter", ctx, run_id, None, key)
    execute_native_workflow("gh612_ac17_counter", ctx, run_id, None, key)

    assert _read_counter(counter) == 2, (
        f"AC17 (GH612): with no event_log_path the engine must execute on EVERY call; real "
        f"counter={_read_counter(counter)!r}, expected 2."
    )
    stray = list(real_tmp.rglob("phase-sentinels"))
    assert stray == [], (
        f"AC17 (GH612): no 'phase-sentinels' directory may be created when event_log_path is "
        f"None; found {stray!r}."
    )


def test_ac18_atomic_write_leaves_no_tmp_residue_gh612(tmp_path, monkeypatch):
    """AC18: the atomic write (tempfile + os.replace) leaves no residue — after
    store_success, the sentinel dir contains EXACTLY the one <key>.json (no .tmp* leftovers).

    Pre-GREEN: FAIL — lib.phase_sentinel does not exist (ImportError).
    """
    from bytedigger_engine.lib.phase_sentinel import (  # type: ignore[import]
        _sentinel_path,
        execute_native_workflow,
        phase_key,
    )

    monkeypatch.setenv(_ENV, "native")
    real_tmp = Path(realpath(str(tmp_path)))
    event_log_path = _event_log_path(tmp_path)
    counter = real_tmp / "counter-ac18.txt"
    _register_counter_workflow(monkeypatch, "gh612_ac18_counter", counter)

    ctx = dict(_MINIMAL_CTX_DICT)
    run_id = "gh612-ac18-run"
    _set_run(event_log_path, run_id)
    key = phase_key(run_id, "gh612_ac18_counter", ctx)

    execute_native_workflow("gh612_ac18_counter", ctx, run_id, event_log_path, key)

    sentinel = _sentinel_path(key, event_log_path)
    assert sentinel is not None and sentinel.exists(), (
        f"AC18 (GH612) pre-stage: the sentinel must exist after a success; got {sentinel!r}"
    )
    entries = sorted(p.name for p in sentinel.parent.iterdir())
    assert entries == [f"{key}.json"], (
        f"AC18 (GH612): the sentinel dir must contain exactly the one committed file "
        f"(os.replace, no temp residue); found {entries!r}."
    )


# ===========================================================================
# Observability (eval §6.2)
# ===========================================================================

def test_ac19_serve_and_store_emit_phase_sentinel_events_gh612(tmp_path, monkeypatch):
    """AC19: the cache WRITE emits `phase_sentinel_stored` and the cache SERVE emits
    `phase_sentinel_resumed` carrying `phase_key` — the serve-counter Ship B's go/no-go
    depends on. Asserted on telemetry_ctx.emit_safe calls (a recording pass-through spy on
    the telemetry INFRA seam, not on a UUT) unioned with what actually lands in the event log.

    Pre-GREEN: FAIL — lib.phase_sentinel does not exist (ImportError); no code emits
    phase_sentinel_stored / phase_sentinel_resumed anywhere today.
    """
    from bytedigger_engine.lib.phase_sentinel import execute_native_workflow, phase_key  # type: ignore[import]

    monkeypatch.setenv(_ENV, "native")
    real_tmp = Path(realpath(str(tmp_path)))
    event_log_path = _event_log_path(tmp_path)
    counter = real_tmp / "counter-ac19.txt"
    _register_counter_workflow(monkeypatch, "gh612_ac19_counter", counter)

    seen: list[tuple[str, dict]] = []
    orig_emit_safe = telemetry_ctx.emit_safe

    def _spy_emit_safe(event_type: str, payload: dict) -> None:
        seen.append((event_type, payload))
        orig_emit_safe(event_type, payload)

    monkeypatch.setattr(telemetry_ctx, "emit_safe", _spy_emit_safe)

    ctx = dict(_MINIMAL_CTX_DICT)
    run_id = "gh612-ac19-run"
    _set_run(event_log_path, run_id)
    key = phase_key(run_id, "gh612_ac19_counter", ctx)

    execute_native_workflow("gh612_ac19_counter", ctx, run_id, event_log_path, key)   # WRITE
    _set_run(event_log_path, run_id)
    execute_native_workflow("gh612_ac19_counter", ctx, run_id, event_log_path, key)   # SERVE

    logged: list[tuple[str, dict]] = []
    log_file = Path(event_log_path)
    if log_file.exists():
        for line in log_file.read_text().splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            logged.append((row.get("event_type") or row.get("type"), row.get("payload") or {}))

    events = seen + logged
    stored = [p for (t, p) in events if t == "phase_sentinel_stored"]
    resumed = [p for (t, p) in events if t == "phase_sentinel_resumed"]

    assert stored, (
        f"AC19 (GH612): the cache WRITE must emit 'phase_sentinel_stored'; observed event types="
        f"{sorted({t for (t, _) in events})!r}"
    )
    assert resumed, (
        f"AC19 (GH612): the cache SERVE must emit 'phase_sentinel_resumed'; observed event types="
        f"{sorted({t for (t, _) in events})!r}"
    )
    assert any(p.get("phase_key") == key for p in resumed), (
        f"AC19 (GH612): 'phase_sentinel_resumed' must carry phase_key={key!r}; got {resumed!r}"
    )
    assert _read_counter(counter) == 1, (
        f"AC19 (GH612) sanity: the SERVE must not re-execute the engine; real counter="
        f"{_read_counter(counter)!r}, expected 1."
    )


# ===========================================================================
# Gate cycle-1 additions (F1/F2/F3 + M2/M3) — appended per Opus coverage reject.
# All 6 tests below are NEW; the 19 above are UNCHANGED per orchestrator instruction.
# ===========================================================================

def _register_nonjson_counter_workflow(monkeypatch, name: str, counter_path: Path) -> None:
    """Register a REAL engine workflow whose SUCCESSFUL step returns non-JSON-native
    `data` (a Path and a tuple) — the AC20/AC21 forcing fixture. A bare
    `json.dumps(result)` (no `default=str`) raises TypeError on this payload."""
    from bytedigger_engine import workflows as workflows_pkg  # type: ignore
    from bytedigger_engine.contracts import StepContract, StepResult, WorkflowDefinition  # type: ignore

    def _step(_ctx, _prev):
        _bump_counter(counter_path)
        return StepResult(
            status="ok",
            data={"p": Path("/x"), "t": (1, 2)},
            duration_ms=0,
            step_name=name,
        )

    orig_register_all = workflows_pkg.register_all

    def _patched_register_all(engine):
        orig_register_all(engine)
        engine.register(name, WorkflowDefinition(name=name, steps=[StepContract(name=name, execute=_step)]))

    monkeypatch.setattr(workflows_pkg, "register_all", _patched_register_all)


def test_ac20_nonjson_native_success_does_not_raise_gh612(tmp_path, monkeypatch):
    """AC20 (F1 — serialization): a native SUCCESS whose `data` carries non-JSON-native
    values (Path, tuple) returns status=="ok" and does NOT raise; a second call with the
    same key also returns "ok" without raising.

    A GREEN using bare `json.dumps(result)` + `except OSError` raises TypeError out of
    store_success on this green phase — that TypeError is exactly what this test forbids.

    Pre-GREEN: FAIL — lib.phase_sentinel does not exist (ImportError).
    """
    from bytedigger_engine.lib.phase_sentinel import execute_native_workflow, phase_key  # type: ignore[import]

    monkeypatch.setenv(_ENV, "native")
    event_log_path = _event_log_path(tmp_path)
    real_tmp = Path(realpath(str(tmp_path)))
    counter = real_tmp / "counter-ac20.txt"
    _register_nonjson_counter_workflow(monkeypatch, "gh612_ac20_counter", counter)

    ctx = dict(_MINIMAL_CTX_DICT)
    run_id = "gh612-ac20-run"
    _set_run(event_log_path, run_id)
    key = phase_key(run_id, "gh612_ac20_counter", ctx)

    first = execute_native_workflow("gh612_ac20_counter", ctx, run_id, event_log_path, key)
    assert first["status"] == "ok", (
        f"AC20 (GH612): a non-JSON-native SUCCESS must still return status=='ok'; got {first!r}"
    )

    second = execute_native_workflow("gh612_ac20_counter", ctx, run_id, event_log_path, key)
    assert second["status"] == "ok", (
        f"AC20 (GH612): the second call over the same non-JSON-native cached result must also "
        f"return status=='ok' without raising; got {second!r}"
    )


def test_ac20b_readonly_sentinel_dir_store_fails_soft_gh612(tmp_path, monkeypatch):
    """AC20b (F1 — writer fail-soft): with the phase-sentinels dir pre-created and made
    read-only (chmod 0o500), a native success still returns status=="ok" — store_success
    silently no-ops, no exception escapes. Mode is restored in a finally so tmp cleanup
    works.

    Pre-GREEN: FAIL — lib.phase_sentinel does not exist (ImportError).
    """
    if os.geteuid() == 0:
        pytest.skip("chmod 0o500 does not stop root; AC20b cannot force the guard in this container")

    from bytedigger_engine.lib.phase_sentinel import execute_native_workflow, phase_key  # type: ignore[import]

    monkeypatch.setenv(_ENV, "native")
    event_log_path = _event_log_path(tmp_path)
    real_tmp = Path(realpath(str(tmp_path)))
    counter = real_tmp / "counter-ac20b.txt"
    _register_counter_workflow(monkeypatch, "gh612_ac20b_counter", counter)

    ctx = dict(_MINIMAL_CTX_DICT)
    run_id = "gh612-ac20b-run"
    _set_run(event_log_path, run_id)
    key = phase_key(run_id, "gh612_ac20b_counter", ctx)

    sentinel_dir = Path(event_log_path).parent / "phase-sentinels"
    sentinel_dir.mkdir(parents=True, exist_ok=True)
    sentinel_dir.chmod(0o500)
    try:
        result = execute_native_workflow("gh612_ac20b_counter", ctx, run_id, event_log_path, key)
        assert result["status"] == "ok", (
            f"AC20b (GH612): a native success must still return status=='ok' even when the "
            f"sentinel dir is read-only; got {result!r}"
        )
    finally:
        sentinel_dir.chmod(0o700)


def test_ac21_served_result_equals_declared_coercion_image_gh612(tmp_path, monkeypatch):
    """AC21 (F1 — serve fidelity, declared §2.2): the SERVED dict on call 2 equals
    `json.loads(json.dumps(first_result, default=str))` — the declared coercion image,
    not necessarily the pickle-exact original (Path/tuple are not JSON-native).

    Pre-GREEN: FAIL — lib.phase_sentinel does not exist (ImportError).
    """
    from bytedigger_engine.lib.phase_sentinel import execute_native_workflow, phase_key  # type: ignore[import]

    monkeypatch.setenv(_ENV, "native")
    event_log_path = _event_log_path(tmp_path)
    real_tmp = Path(realpath(str(tmp_path)))
    counter = real_tmp / "counter-ac21.txt"
    _register_nonjson_counter_workflow(monkeypatch, "gh612_ac21_counter", counter)

    ctx = dict(_MINIMAL_CTX_DICT)
    run_id = "gh612-ac21-run"
    _set_run(event_log_path, run_id)
    key = phase_key(run_id, "gh612_ac21_counter", ctx)

    first = execute_native_workflow("gh612_ac21_counter", ctx, run_id, event_log_path, key)
    expected = json.loads(json.dumps(first, default=str))

    served = execute_native_workflow("gh612_ac21_counter", ctx, run_id, event_log_path, key)

    assert served == expected, (
        f"AC21 (GH612): the served (cached) result must equal the declared coercion image "
        f"json.loads(json.dumps(first_result, default=str)); expected {expected!r}, got {served!r}"
    )
    assert _read_counter(counter) == 1, (
        f"AC21 (GH612) sanity: serving from cache must not re-execute the engine; real counter="
        f"{_read_counter(counter)!r}, expected 1."
    )


def test_ac22_non_dict_sentinel_payloads_are_a_miss_without_raising_gh612(tmp_path, monkeypatch):
    """AC22 (F2 — reader non-dict): sentinels containing `null` and `[1,2,3]` each make
    load_cached_success return None WITHOUT raising, and the engine re-executes.

    A GREEN catching only (OSError, ValueError) and then calling `payload.get(...)` raises
    AttributeError on these payloads — that AttributeError is exactly what this test forbids.

    §1i: both sentinels are PRE-STAGED deterministically (hand-written before the call);
    nothing races.

    Pre-GREEN: FAIL — lib.phase_sentinel does not exist (ImportError).
    """
    from bytedigger_engine.lib.phase_sentinel import (  # type: ignore[import]
        _sentinel_path,
        execute_native_workflow,
        load_cached_success,
        phase_key,
    )

    monkeypatch.setenv(_ENV, "native")
    event_log_path = _event_log_path(tmp_path)
    real_tmp = Path(realpath(str(tmp_path)))
    counter = real_tmp / "counter-ac22.txt"
    _register_counter_workflow(monkeypatch, "gh612_ac22_counter", counter)

    ctx = dict(_MINIMAL_CTX_DICT)
    run_id = "gh612-ac22-run"
    _set_run(event_log_path, run_id)

    key_null = phase_key(run_id, "gh612_ac22_counter", {**ctx, "org_config": {"variant": "null"}})
    key_list = phase_key(run_id, "gh612_ac22_counter", {**ctx, "org_config": {"variant": "list"}})

    sentinel_null = _sentinel_path(key_null, event_log_path)
    sentinel_list = _sentinel_path(key_list, event_log_path)
    assert sentinel_null is not None and sentinel_list is not None
    sentinel_null.parent.mkdir(parents=True, exist_ok=True)
    sentinel_null.write_text("null")
    sentinel_list.parent.mkdir(parents=True, exist_ok=True)
    sentinel_list.write_text("[1,2,3]")

    assert load_cached_success(key_null, event_log_path) is None, (
        "AC22 (GH612): a sentinel containing 'null' must be a MISS (None) WITHOUT raising."
    )
    assert load_cached_success(key_list, event_log_path) is None, (
        "AC22 (GH612): a sentinel containing '[1,2,3]' must be a MISS (None) WITHOUT raising."
    )

    ctx_null = dict(ctx)
    ctx_null["org_config"] = {"variant": "null"}
    result = execute_native_workflow("gh612_ac22_counter", ctx_null, run_id, event_log_path, key_null)

    assert result["status"] == "ok" and _read_counter(counter) == 1, (
        f"AC22 (GH612): after a non-dict-payload MISS, the engine must really re-execute; "
        f"real counter={_read_counter(counter)!r}, result={result!r}"
    )


def _seed_pending_workflow_status_row(db_path: Path, workflow_uuid: str) -> None:
    """Hand-build a minimal DBOS-shaped sqlite DB holding one PENDING workflow_status row
    for workflow_uuid. Mirrors the schema/seed idiom of
    test_dbos_zombie_root_gh748.py's _seed_workflow_status (AC5) — a bare-bones
    workflow_status table, sufficient for _evict_orphan_pending's own
    `SELECT status FROM workflow_status WHERE workflow_uuid=? AND status='PENDING'` /
    `DELETE ... WHERE workflow_uuid=? AND status='PENDING'` queries (dbos_setup.py:262-266).
    We do NOT boot real DBOS/DBOS schema here — _evict_orphan_pending only ever touches
    workflow_status + operation_outputs directly via raw sqlite3, never through the DBOS
    ORM, so a minimal hand-built schema is sufficient and faster than a real DBOS.launch().
    """
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS workflow_status (
                workflow_uuid TEXT PRIMARY KEY,
                status TEXT,
                name TEXT,
                output TEXT,
                executor_id TEXT,
                application_version TEXT,
                owner_xid TEXT,
                created_at INTEGER,
                updated_at INTEGER
            )
            """
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS operation_outputs "
            "(workflow_uuid TEXT, function_id INTEGER, function_name TEXT, output TEXT)"
        )
        conn.execute(
            "INSERT INTO workflow_status (workflow_uuid, status, name, created_at, updated_at) "
            "VALUES (?, 'PENDING', ?, 0, 0)",
            (workflow_uuid, workflow_uuid),
        )
        conn.commit()
    finally:
        conn.close()


def test_ac23a_native_leaves_preexisting_pending_row_intact_gh612(tmp_path, monkeypatch):
    """AC23a (F3 — ordering invariant, REAL side-effect — the strong one). Under `native`,
    with an EXISTING sqlite DB pre-staged at HAL_DBOS_DB_PATH holding a `workflow_status`
    row (workflow_uuid=<the exact phase_key>, status='PENDING'), a full
    execute_durable_workflow completes (real counter == 1) AND that PENDING row STILL
    EXISTS. A misordered GREEN's _evict_orphan_pending (dbos_setup.py:262-266) DELETEs it.

    The cycle-2 version of this AC pointed HAL_DBOS_DB_PATH at a NON-existent file — all
    3 DBOS pre-flight helpers `return` at their `if not target.exists()` guard regardless
    of branch ordering, so it was VACUOUS (could not discriminate). This version pre-stages
    an EXISTING DB with a real row so the DELETE (if reached) is observable.

    §1i: the PENDING row is PRE-STAGED deterministically via raw sqlite3 BEFORE the call —
    no race.

    Pre-GREEN: FAIL — execute_durable_workflow has no native branch, so it falls through to
    the DBOS pre-flight helpers; _evict_orphan_pending finds the seeded PENDING row and
    DELETEs it, so "still exists" fails.
    """
    from bytedigger_engine.lib.phase_sentinel import phase_key  # type: ignore[import]

    real_tmp = Path(realpath(str(tmp_path)))
    db_path = real_tmp / "ac23a-dbos.sqlite"
    monkeypatch.setenv("HAL_DBOS_DB_PATH", str(db_path))
    monkeypatch.setenv(_ENV, "native")

    event_log_path = _event_log_path(tmp_path)
    counter = real_tmp / "counter-ac23a.txt"
    _register_counter_workflow(monkeypatch, "gh612_ac23a_counter", counter)

    ctx = dict(_MINIMAL_CTX_DICT)
    run_id = "gh612-ac23a-run"
    key = phase_key(run_id, "gh612_ac23a_counter", ctx)
    _seed_pending_workflow_status_row(db_path, key)

    _set_run(event_log_path, run_id)
    result = dbos_setup.execute_durable_workflow(
        "gh612_ac23a_counter", ctx, run_id, event_log_path=event_log_path
    )

    assert result["status"] == "ok" and _read_counter(counter) == 1, (
        f"AC23a (GH612): the native path must really execute the engine; real counter="
        f"{_read_counter(counter)!r}, result={result!r}"
    )

    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT status FROM workflow_status WHERE workflow_uuid=?", (key,)
        ).fetchone()
    finally:
        conn.close()
    assert row == ("PENDING",), (
        f"AC23a (GH612): the pre-staged PENDING row for {key!r} must STILL EXIST after a "
        f"correctly-ordered native run (the native `return` precedes _evict_orphan_pending); "
        f"got row={row!r}. A misordered GREEN's _evict_orphan_pending DELETEs it."
    )


def test_ac23b_native_never_emits_dbos_db_path_resolver_event_gh612(tmp_path, monkeypatch):
    """AC23b (F3 — ordering invariant, emit trace). On the same kind of native run as
    AC23a, `resolver_dbos_db_path_resolved` is emitted ZERO times. `_resolve_db_path`
    emits at dbos_setup.py:92/96 BEFORE the `exists()` guard inside each of the 3 DBOS
    pre-flight helpers, so a misordered GREEN emits it 3 times (once per helper)
    regardless of whether the target sqlite file exists.

    Pre-GREEN: FAIL — execute_durable_workflow has no native branch, so it falls through
    to _evict_orphan_pending / _maybe_evict_on_retry / _maybe_emit_durable_resume, each of
    which calls _resolve_db_path() and emits resolver_dbos_db_path_resolved — 3 events,
    not 0.
    """
    from bytedigger_engine.lib.phase_sentinel import phase_key  # type: ignore[import]

    real_tmp = Path(realpath(str(tmp_path)))
    db_path = real_tmp / "ac23b-dbos.sqlite"
    monkeypatch.setenv("HAL_DBOS_DB_PATH", str(db_path))
    monkeypatch.setenv(_ENV, "native")

    event_log_path = _event_log_path(tmp_path)
    counter = real_tmp / "counter-ac23b.txt"
    _register_counter_workflow(monkeypatch, "gh612_ac23b_counter", counter)

    ctx = dict(_MINIMAL_CTX_DICT)
    run_id = "gh612-ac23b-run"
    key = phase_key(run_id, "gh612_ac23b_counter", ctx)
    _seed_pending_workflow_status_row(db_path, key)

    seen: list[tuple[str, dict]] = []
    orig_emit_safe = telemetry_ctx.emit_safe

    def _spy_emit_safe(event_type: str, payload: dict) -> None:
        seen.append((event_type, payload))
        orig_emit_safe(event_type, payload)

    monkeypatch.setattr(telemetry_ctx, "emit_safe", _spy_emit_safe)

    _set_run(event_log_path, run_id)
    dbos_setup.execute_durable_workflow(
        "gh612_ac23b_counter", ctx, run_id, event_log_path=event_log_path
    )

    resolver_events = [p for (t, p) in seen if t == "resolver_dbos_db_path_resolved"]
    assert resolver_events == [], (
        f"AC23b (GH612): a correctly-ordered native run must emit "
        f"'resolver_dbos_db_path_resolved' ZERO times (the native return precedes "
        f"_resolve_db_path's callers); got {len(resolver_events)} event(s): {resolver_events!r}"
    )


def test_ac24_native_still_emits_resolver_events_gh612(tmp_path, monkeypatch):
    """AC24 (M2/M3 — resolver observability): under `native`, execute_durable_workflow
    still emits emit_resolver_resolved("execute_durable_workflow", "phase_segregated_uuid",
    <key>) (it sits above the branch), AND resolve_durable_backend emits
    emit_resolver_resolved("durable_backend", ..., "native").

    GH795 AC12 repair (§1a sibling repair, RED-owned, pre-authorized in the GH795 spec
    §5): GH795's §2.2 "exclusive, not both" dispatch means a run-context bound over the
    SAME event_log_path starves the `emit_safe` spy for the "durable_backend" resolver
    event once GREEN lands (the emit goes straight to the event-log file on disk
    instead). The `resolver_execute_durable_workflow_resolved` half stays on the
    emit_safe spy unchanged — that emit passes no event_log_path, so it still routes
    through emit_safe. The "durable_backend" half is re-sourced from the event-log file
    on disk (same read-back shape GH792 uses).

    Pre-GREEN: FAIL — lib.phase_sentinel (and its resolve_durable_backend) does not exist,
    so no "durable_backend" resolver event is ever emitted; ImportError on the first import.

    GH795 AC12 state: GREEN both before and after GH795's GREEN — by design. The
    disk assertion is channel-agnostic: today the bound run-context makes emit_safe
    write the event into EventLog(event_log_path); post-GREEN the exclusive
    direct-sink dispatch writes it to that same file. So this is an invariant guard
    (like GH795 AC10/AC11), not a RED — it fails only if GREEN drops the emit or
    routes it somewhere else.
    """
    from bytedigger_engine.lib.phase_sentinel import phase_key  # type: ignore[import]

    real_tmp = Path(realpath(str(tmp_path)))
    monkeypatch.setenv("HAL_DBOS_DB_PATH", str(real_tmp / "never2.sqlite"))
    monkeypatch.setenv(_ENV, "native")

    event_log_path = _event_log_path(tmp_path)
    counter = real_tmp / "counter-ac24.txt"
    _register_counter_workflow(monkeypatch, "gh612_ac24_counter", counter)

    seen: list[tuple[str, dict]] = []
    orig_emit_safe = telemetry_ctx.emit_safe

    def _spy_emit_safe(event_type: str, payload: dict) -> None:
        seen.append((event_type, payload))
        orig_emit_safe(event_type, payload)

    monkeypatch.setattr(telemetry_ctx, "emit_safe", _spy_emit_safe)

    ctx = dict(_MINIMAL_CTX_DICT)
    run_id = "gh612-ac24-run"
    _set_run(event_log_path, run_id)
    expected_key = phase_key(run_id, "gh612_ac24_counter", ctx)

    dbos_setup.execute_durable_workflow(
        "gh612_ac24_counter", ctx, run_id, event_log_path=event_log_path
    )

    uuid_events = [p for (t, p) in seen if t == "resolver_execute_durable_workflow_resolved"]

    assert any(p.get("value") == expected_key for p in uuid_events), (
        f"AC24 (GH612): execute_durable_workflow must still emit "
        f"resolver_execute_durable_workflow_resolved with value={expected_key!r} under native; "
        f"got {uuid_events!r}"
    )

    # GH795 AC12: the "durable_backend" resolver event now dispatches EXCLUSIVELY to the
    # direct-sink event-log file (§2.2) when event_log_path is given — even though a
    # run-context is ALSO bound over the same path — so it must be read back from disk,
    # not from the emit_safe spy (which the exclusive dispatch starves).
    backend_rows: list[dict] = []
    log_file = Path(event_log_path)
    if log_file.exists():
        for line in log_file.read_text().splitlines():
            if not line.strip():
                continue
            backend_rows.append(json.loads(line))
    backend_events = [
        row.get("payload", {}) for row in backend_rows
        if row.get("event_type") == "resolver_durable_backend_resolved"
    ]
    assert any(p.get("value") == "native" for p in backend_events), (
        f"AC24 (GH612): resolve_durable_backend must emit resolver_durable_backend_resolved "
        f"with value='native' to the event-log file on disk at {event_log_path!r}; "
        f"got {backend_events!r}"
    )


# ===========================================================================
# Gate cycle-3 additions (AC23 replacement + AC25/26a/26b/27/28).
# AC23a/AC23b above REPLACE the vacuous cycle-2 AC23. All other tests unchanged.
# ===========================================================================

def test_ac25_unknown_backend_gate_sits_above_dbos_init_try_gh612(tmp_path, monkeypatch):
    """AC25 (gate MAJOR-2 — the §2.3 HARD CONSTRAINT): with
    HAL_ENGINE_DURABLE_BACKEND="sqlite", init_dbos(tmp_db) raises
    E_DURABLE_BACKEND_UNKNOWN and NOT E_DBOS_SCHEMA_INIT_FAILED.

    Both are RuntimeErrors, so the NEGATIVE assertion (`not isinstance(...,
    E_DBOS_SCHEMA_INIT_FAILED)`) is what pins the gate ABOVE dbos_setup.py:126's `try:` —
    inside it, `except Exception` (:145) would re-wrap the typo'd env var as
    E_DBOS_SCHEMA_INIT_FAILED ("check write permissions… verify dbos==2.22.0"),
    corrupting the §2.4 taxonomy.

    Pre-GREEN: FAIL — lib.phase_sentinel.E_DURABLE_BACKEND_UNKNOWN does not exist yet
    (ImportError); today init_dbos has no backend gate at all and constructs DBOS
    unconditionally.
    """
    from bytedigger_engine.lib.phase_sentinel import E_DURABLE_BACKEND_UNKNOWN  # type: ignore[import]

    real_tmp = Path(realpath(str(tmp_path)))
    db_path = real_tmp / "ac25-dbos.sqlite"
    monkeypatch.setenv("HAL_DBOS_DB_PATH", str(db_path))
    monkeypatch.setenv(_ENV, "sqlite")

    dbos_setup._dbos_initialized = False
    with pytest.raises(E_DURABLE_BACKEND_UNKNOWN) as exc:
        dbos_setup.init_dbos(db_path)

    assert not isinstance(exc.value, dbos_setup.E_DBOS_SCHEMA_INIT_FAILED), (
        "AC25 (GH612): the unknown-backend gate must sit ABOVE the try: at dbos_setup.py:126 — "
        "raising E_DBOS_SCHEMA_INIT_FAILED here means the typo'd env var fell into the DBOS "
        "init try/except and got re-wrapped, corrupting the §2.4 error taxonomy."
    )


def test_ac26a_escalate_result_not_cached_and_retry_reexecutes_gh612(tmp_path, monkeypatch):
    """AC26a (gate MAJOR-3 — escalate writer): a workflow returning status="escalate"
    writes NO sentinel, and a second call under the same key RE-EXECUTES (real counter
    == 2). A GREEN gating on `if status == "error": return` would cache+serve escalate —
    a sticky non-success, the #299/#603/#611 class reborn on the native path. AC12 only
    covers "error".

    §1l: only a real second engine execution can leave counter == 2.

    Pre-GREEN: FAIL — lib.phase_sentinel does not exist (ImportError).
    """
    from bytedigger_engine.lib.phase_sentinel import execute_native_workflow, phase_key  # type: ignore[import]

    monkeypatch.setenv(_ENV, "native")
    real_tmp = Path(realpath(str(tmp_path)))
    event_log_path = _event_log_path(tmp_path)
    counter = real_tmp / "counter-ac26a.txt"
    _register_counter_workflow(monkeypatch, "gh612_ac26a_counter", counter, mode="escalate")

    ctx = dict(_MINIMAL_CTX_DICT)
    run_id = "gh612-ac26a-run"
    _set_run(event_log_path, run_id)
    key = phase_key(run_id, "gh612_ac26a_counter", ctx)

    first = execute_native_workflow("gh612_ac26a_counter", ctx, run_id, event_log_path, key)
    assert first["status"] == "escalate", (
        f"AC26a (GH612) pre-stage: the escalate workflow must return status=='escalate'; "
        f"got {first!r}"
    )

    sentinel = Path(event_log_path).parent / "phase-sentinels" / f"{key}.json"
    assert not sentinel.exists(), (
        f"AC26a (GH612): an ESCALATE result must NEVER be cached; found a sentinel at {sentinel}."
    )

    execute_native_workflow("gh612_ac26a_counter", ctx, run_id, event_log_path, key)

    assert _read_counter(counter) == 2, (
        f"AC26a (GH612): the retry after an escalate must RE-EXECUTE the engine; real counter="
        f"{_read_counter(counter)!r}, expected 2."
    )


def test_ac26b_escalate_status_sentinel_is_refused_gh612(tmp_path, monkeypatch):
    """AC26b (gate MAJOR-3 — escalate reader): a hand-planted sentinel with
    {"status": "escalate", ...} makes load_cached_success return None
    (defence-in-depth, mirroring AC16).

    §1i: the sentinel is PRE-STAGED deterministically; no race.

    Pre-GREEN: FAIL — lib.phase_sentinel does not exist (ImportError).
    """
    from bytedigger_engine.lib.phase_sentinel import _sentinel_path, load_cached_success, phase_key  # type: ignore[import]

    monkeypatch.setenv(_ENV, "native")
    event_log_path = _event_log_path(tmp_path)
    ctx = dict(_MINIMAL_CTX_DICT)
    key = phase_key("gh612-ac26b-run", "gh612_ac26b_counter", ctx)

    sentinel = _sentinel_path(key, event_log_path)
    assert sentinel is not None, "AC26b (GH612): _sentinel_path must resolve for a real event_log_path."
    sentinel.parent.mkdir(parents=True, exist_ok=True)
    sentinel.write_text(json.dumps({
        "status": "escalate", "data": None, "duration_ms": 1, "step_name": "x",
        "error": None, "error_code": None, "suggestion": None, "recoverable": True,
    }))

    assert load_cached_success(key, event_log_path) is None, (
        "AC26b (GH612): a sentinel whose payload status is 'escalate' must be REFUSED (None) — "
        "the reader must not replay an escalate result even if hand-planted."
    )


def test_ac27_skip_result_is_cached_and_served_gh612(tmp_path, monkeypatch):
    """AC27 (gate MINOR-3 — skip IS cached): a workflow returning status="skip" WRITES a
    sentinel, and the second call SERVES it (real counter stays 1). Pins the other half of
    `in ("ok","skip")` — a GREEN gating on `== "ok"` alone would silently never cache a
    skipped phase.

    §1l: only a real cache-serve keeps the real counter at 1 across two calls.

    Pre-GREEN: FAIL — lib.phase_sentinel does not exist (ImportError).
    """
    from bytedigger_engine.lib.phase_sentinel import execute_native_workflow, phase_key  # type: ignore[import]

    monkeypatch.setenv(_ENV, "native")
    real_tmp = Path(realpath(str(tmp_path)))
    event_log_path = _event_log_path(tmp_path)
    counter = real_tmp / "counter-ac27.txt"
    _register_counter_workflow(monkeypatch, "gh612_ac27_counter", counter, mode="skip")

    ctx = dict(_MINIMAL_CTX_DICT)
    run_id = "gh612-ac27-run"
    _set_run(event_log_path, run_id)
    key = phase_key(run_id, "gh612_ac27_counter", ctx)

    first = execute_native_workflow("gh612_ac27_counter", ctx, run_id, event_log_path, key)
    assert first["status"] == "skip", (
        f"AC27 (GH612) pre-stage: the skip workflow must return status=='skip'; got {first!r}"
    )
    sentinel = Path(event_log_path).parent / "phase-sentinels" / f"{key}.json"
    assert sentinel.exists(), (
        f"AC27 (GH612): a SKIP result must be cached (store_success gate is "
        f"`in (\"ok\",\"skip\")`); no sentinel found at {sentinel}."
    )

    second = execute_native_workflow("gh612_ac27_counter", ctx, run_id, event_log_path, key)

    assert _read_counter(counter) == 1, (
        f"AC27 (GH612): the second call must SERVE THE CACHE for a skip result — the engine "
        f"must NOT re-execute; real counter={_read_counter(counter)!r}, expected 1."
    )
    assert second["status"] == "skip", (
        f"AC27 (GH612): the served result must preserve status=='skip'; got {second!r}"
    )


def test_ac28_failed_atomic_write_still_ok_and_cleans_tmp_residue_gh612(tmp_path, monkeypatch):
    """AC28 (gate MINOR-4 — tmp cleanup on a FAILED write): with <key>.json pre-created as
    a DIRECTORY (so os.replace fails AFTER the tmp file is written), a native success
    still returns status=="ok" AND the sentinel dir contains NO stray .tmp* entry — the
    try/finally unlink fired.

    Pre-GREEN: FAIL — lib.phase_sentinel does not exist (ImportError).
    """
    from bytedigger_engine.lib.phase_sentinel import execute_native_workflow, phase_key  # type: ignore[import]

    monkeypatch.setenv(_ENV, "native")
    event_log_path = _event_log_path(tmp_path)
    counter = Path(realpath(str(tmp_path))) / "counter-ac28.txt"
    _register_counter_workflow(monkeypatch, "gh612_ac28_counter", counter)

    ctx = dict(_MINIMAL_CTX_DICT)
    run_id = "gh612-ac28-run"
    _set_run(event_log_path, run_id)
    key = phase_key(run_id, "gh612_ac28_counter", ctx)

    sentinel_dir = Path(event_log_path).parent / "phase-sentinels"
    sentinel_dir.mkdir(parents=True, exist_ok=True)
    (sentinel_dir / f"{key}.json").mkdir()  # pre-stage AS A DIRECTORY -> os.replace target conflict

    result = execute_native_workflow("gh612_ac28_counter", ctx, run_id, event_log_path, key)

    assert result["status"] == "ok", (
        f"AC28 (GH612): a native success must still return status=='ok' even when the atomic "
        f"write's final os.replace fails (target is a pre-existing directory); got {result!r}"
    )

    residue = [p.name for p in sentinel_dir.iterdir() if p.name != f"{key}.json"]
    assert residue == [], (
        f"AC28 (GH612): no stray temp file may remain in the sentinel dir after a failed "
        f"os.replace; found {residue!r} (the try/finally unlink must fire)."
    )
