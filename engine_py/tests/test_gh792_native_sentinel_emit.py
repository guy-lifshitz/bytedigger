"""RED tests for GH792 — native durable-seam telemetry (SERVE/STORE emit directly
to the event log, bypassing the run-context-only `telemetry_ctx.emit_safe`).

Spec: SHARED/memory/Decisions/2026-07-14_7C3E9A11_gh792_native_sentinel_emit_spec.md
(FROZEN). §1c Class: PATCH — 2 emit sites in `lib/phase_sentinel.py`, parent
SYSTEMATIC #612.

§1l forcing function: every AC anchors on the REAL event-log JSONL file on disk,
written by the REAL production path (`lib.dbos_setup.execute_durable_workflow` for
AC2/AC3/AC4/AC5/AC8, `lib.phase_sentinel.*` directly for AC6/AC7/AC9). No
`_set_run` / `telemetry_ctx.set_current_run` is ever called (AC1 is the guard that
enforces this) and `telemetry_ctx.emit_safe` is never monkeypatched. The ONE infra
seam patched is `event_sink.get_event_sink` in AC7 (§1n OWN-all guard) — never the
UUT (`store_success` / `load_cached_success` / `execute_native_workflow` /
`_emit_sentinel_event`).

§1q / D1CF5FDF: `lib.phase_sentinel._emit_sentinel_event` does not exist yet (the
new helper this ship introduces), so it is never referenced at module level —
every reference lives inside a test body. The module COLLECTS cleanly; each test
fails at call/assert time, never at collection time. No `spec_from_file_location`
/ `exec_module`, no module-level `sys.path` mutation (tests/conftest.py already
exposes engine_py root + `workflows/` on sys.path).
"""
from __future__ import annotations

import json
import os
from os.path import realpath
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("dbos")

import lib.dbos_setup as dbos_setup  # type: ignore[import]  # attr access only — never patched
import telemetry_ctx  # type: ignore[import]

_ENV = "HAL_ENGINE_DURABLE_BACKEND"

_MINIMAL_CTX_DICT: dict[str, Any] = {
    "tenant_id": "hal-test",
    "scope": None,
    "db_path": None,
    "org_config": {"complexity": "SIMPLE", "phase": "spec"},
    "question": "gh792 native sentinel emit test",
    "session_id": "gh792-test",
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
# Autouse isolation (no global state leaks between tests) — mirrors GH612 seam
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _env_isolation(monkeypatch):
    monkeypatch.delenv(_ENV, raising=False)
    monkeypatch.delenv("HAL_DBOS_DB_PATH", raising=False)
    monkeypatch.delenv("HAL_DIR", raising=False)
    yield


@pytest.fixture(autouse=True)
def _telemetry_reset():
    # NEVER call set_current_run anywhere in this file (AC1's whole point) —
    # this fixture only guarantees no run-context leaks IN from a sibling test.
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


def _register_counter_workflow(
    monkeypatch, name: str, counter_path: Path, *, mode: str = "ok"
) -> None:
    """Register a REAL engine workflow whose step increments an on-disk counter.

    Wraps production `workflows.register_all` — the exact hook `execute_engine`
    calls — so the step only ever runs when the production engine really
    executes it (§1l: a stub cannot move this counter).
    """
    import workflows as workflows_pkg  # type: ignore
    from contracts import StepContract, StepResult, WorkflowDefinition  # type: ignore

    def _step(_ctx, _prev):
        _bump_counter(counter_path)
        if mode == "error":
            return StepResult(
                status="error", data=None, duration_ms=0, step_name=name,
                error="gh792 boom", error_code="E_BOOM", recoverable=False,
            )
        if mode == "escalate":
            return StepResult(
                status="escalate", data=None, duration_ms=0, step_name=name,
                error=None, error_code=None, recoverable=True,
            )
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


def _read_events(event_log_path: str) -> list[dict[str, Any]]:
    """Read the REAL event-log JSONL file from disk and parse every line."""
    log_file = Path(event_log_path)
    if not log_file.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in log_file.read_text().splitlines():
        if not line.strip():
            continue
        rows.append(json.loads(line))
    return rows


def _dump_event_types(rows: list[dict[str, Any]]) -> str:
    return repr(sorted({r.get("event_type") for r in rows}))


# ===========================================================================
# AC1 — Integration guard (must stay PASS)
# ===========================================================================

def test_ac1_no_run_context_bound_before_prod_call_gh792(tmp_path, monkeypatch):
    """AC1: immediately before invoking the prod entry point,
    telemetry_ctx.get_current_run() is None — this test never binds a
    run-context (no `_set_run` helper exists in this file; this is what ac19 in
    test_gh612_phase_sentinel_seam.py got wrong per §0/P4 of the spec).

    Pre-GREEN: PASS (guard).
    """
    monkeypatch.setenv(_ENV, "native")
    real_tmp = Path(realpath(str(tmp_path)))
    event_log_path = _event_log_path(tmp_path)
    counter = real_tmp / "counter-ac1.txt"
    _register_counter_workflow(monkeypatch, "gh792_ac1_counter", counter)

    ctx = dict(_MINIMAL_CTX_DICT)
    run_id = "gh792-ac1-run"

    assert telemetry_ctx.get_current_run() is None, (
        "AC1 (GH792): a run-context leaked in from a sibling test/fixture before the "
        "prod call — this test must never bind one via set_current_run."
    )

    dbos_setup.execute_durable_workflow(
        "gh792_ac1_counter", ctx, run_id, event_log_path=event_log_path
    )

    assert telemetry_ctx.get_current_run() is None, (
        "AC1 (GH792): no run-context may be bound by this test, before OR after the "
        "prod call — production never binds one at these emit sites (§0/P2, P3)."
    )


# ===========================================================================
# AC2 — STORE (fresh)
# ===========================================================================

def test_ac2_store_fresh_appends_phase_sentinel_stored_gh792(tmp_path, monkeypatch):
    """AC2: dbos_setup.execute_durable_workflow(wf, ctx, run_id, event_log_path=...)
    under HAL_ENGINE_DURABLE_BACKEND=native, with NO run-context bound, appends a
    `phase_sentinel_stored` line to the REAL event-log file, payload
    phase_key == phase_key(run_id, wf, ctx) and status == "ok".

    Pre-GREEN: FAIL — 0 lines written; `store_success` still calls
    `telemetry_ctx.emit_safe`, which no-ops without a bound run-context
    (spec §0/P1, `telemetry_ctx.py:84`).
    """
    from lib.phase_sentinel import phase_key  # type: ignore[import]

    monkeypatch.setenv(_ENV, "native")
    real_tmp = Path(realpath(str(tmp_path)))
    event_log_path = _event_log_path(tmp_path)
    counter = real_tmp / "counter-ac2.txt"
    _register_counter_workflow(monkeypatch, "gh792_ac2_counter", counter)

    ctx = dict(_MINIMAL_CTX_DICT)
    run_id = "gh792-ac2-run"
    key = phase_key(run_id, "gh792_ac2_counter", ctx)

    assert telemetry_ctx.get_current_run() is None

    dbos_setup.execute_durable_workflow(
        "gh792_ac2_counter", ctx, run_id, event_log_path=event_log_path
    )

    rows = _read_events(event_log_path)
    stored = [r for r in rows if r.get("event_type") == "phase_sentinel_stored"]
    assert stored, (
        f"AC2 (GH792): expected a 'phase_sentinel_stored' line in the event log at "
        f"{event_log_path!r}; observed event types = {_dump_event_types(rows)}"
    )
    assert stored[0]["payload"].get("phase_key") == key, (
        f"AC2 (GH792): expected payload.phase_key == {key!r}; got {stored[0]!r}"
    )
    assert stored[0]["payload"].get("status") == "ok", (
        f"AC2 (GH792): expected payload.status == 'ok'; got {stored[0]!r}"
    )


# ===========================================================================
# AC3 — SERVE (auto-resume, same run-id)
# ===========================================================================

def test_ac3_serve_auto_resume_appends_phase_sentinel_resumed_gh792(tmp_path, monkeypatch):
    """AC3: a second identical execute_durable_workflow call appends
    `phase_sentinel_resumed` with the same phase_key, status == "ok", and the
    engine is NOT re-executed (real counter file stays 1).

    Pre-GREEN: FAIL — 0 lines written (same emit_safe no-op as AC2, plus
    `execute_native_workflow`'s SERVE branch calls emit_safe too).
    """
    from lib.phase_sentinel import phase_key  # type: ignore[import]

    monkeypatch.setenv(_ENV, "native")
    real_tmp = Path(realpath(str(tmp_path)))
    event_log_path = _event_log_path(tmp_path)
    counter = real_tmp / "counter-ac3.txt"
    _register_counter_workflow(monkeypatch, "gh792_ac3_counter", counter)

    ctx = dict(_MINIMAL_CTX_DICT)
    run_id = "gh792-ac3-run"
    key = phase_key(run_id, "gh792_ac3_counter", ctx)

    assert telemetry_ctx.get_current_run() is None

    dbos_setup.execute_durable_workflow(
        "gh792_ac3_counter", ctx, run_id, event_log_path=event_log_path
    )
    assert _read_counter(counter) == 1, (
        f"AC3 (GH792) pre-stage: the first call must really execute the engine once; "
        f"real counter={_read_counter(counter)!r}"
    )

    dbos_setup.execute_durable_workflow(
        "gh792_ac3_counter", ctx, run_id, event_log_path=event_log_path
    )

    rows = _read_events(event_log_path)
    resumed = [r for r in rows if r.get("event_type") == "phase_sentinel_resumed"]
    assert resumed, (
        f"AC3 (GH792): expected a 'phase_sentinel_resumed' line in the event log at "
        f"{event_log_path!r}; observed event types = {_dump_event_types(rows)}"
    )
    assert resumed[0]["payload"].get("phase_key") == key, (
        f"AC3 (GH792): expected payload.phase_key == {key!r}; got {resumed[0]!r}"
    )
    assert resumed[0]["payload"].get("status") == "ok", (
        f"AC3 (GH792): expected payload.status == 'ok'; got {resumed[0]!r}"
    )
    assert _read_counter(counter) == 1, (
        f"AC3 (GH792): the SERVE must NOT re-execute the engine; real counter="
        f"{_read_counter(counter)!r}, expected 1."
    )


# ===========================================================================
# AC4 — run_id in the event envelope
# ===========================================================================

def test_ac4_emitted_lines_carry_real_run_id_gh792(tmp_path, monkeypatch):
    """AC4: the emitted lines carry `run_id` in the event envelope
    (row["run_id"] == run_id, not "ad-hoc" — event_log.py:109 falls back to
    "ad-hoc" only when run_id is falsy/None).

    Pre-GREEN: FAIL — no lines are emitted at all (same root cause as AC2/AC3).
    """
    from lib.phase_sentinel import phase_key  # type: ignore[import]

    monkeypatch.setenv(_ENV, "native")
    real_tmp = Path(realpath(str(tmp_path)))
    event_log_path = _event_log_path(tmp_path)
    counter = real_tmp / "counter-ac4.txt"
    _register_counter_workflow(monkeypatch, "gh792_ac4_counter", counter)

    ctx = dict(_MINIMAL_CTX_DICT)
    run_id = "gh792-ac4-distinctive-run-id"
    phase_key(run_id, "gh792_ac4_counter", ctx)

    assert telemetry_ctx.get_current_run() is None

    dbos_setup.execute_durable_workflow(
        "gh792_ac4_counter", ctx, run_id, event_log_path=event_log_path
    )

    rows = _read_events(event_log_path)
    stored = [r for r in rows if r.get("event_type") == "phase_sentinel_stored"]
    assert stored, (
        f"AC4 (GH792): expected at least one 'phase_sentinel_stored' line to check "
        f"run_id on; observed event types = {_dump_event_types(rows)}"
    )
    assert stored[0].get("run_id") == run_id, (
        f"AC4 (GH792): expected row['run_id'] == {run_id!r} (not the 'ad-hoc' fallback "
        f"used when run_id is falsy — event_log.py:109); got {stored[0].get('run_id')!r} "
        f"full row={stored[0]!r}"
    )
    assert stored[0].get("run_id") != "ad-hoc", (
        f"AC4 (GH792): run_id must NOT be the 'ad-hoc' placeholder; got row={stored[0]!r}"
    )


# ===========================================================================
# AC5 — no spurious store event on error (must stay PASS)
# ===========================================================================

@pytest.mark.parametrize("mode", ["error", "escalate"])
def test_ac5_no_store_event_on_non_cacheable_status_gh792(tmp_path, monkeypatch, mode):
    """AC5: a workflow returning status="error" (and, parametrized, status=
    "escalate" — both are non-cacheable per phase_sentinel.py:49) writes NO
    sentinel and emits NO 'phase_sentinel_stored'.

    Pre-GREEN: PASS — store_success already early-returns on non-cacheable
    statuses (phase_sentinel.py:232) BEFORE any emit is attempted, unchanged by
    this ship; and since nothing is emitted today at all, there is trivially no
    'phase_sentinel_stored' line either way. This guard must stay green through
    GREEN.
    """
    from lib.phase_sentinel import phase_key  # type: ignore[import]

    monkeypatch.setenv(_ENV, "native")
    real_tmp = Path(realpath(str(tmp_path)))
    event_log_path = _event_log_path(tmp_path)
    counter = real_tmp / f"counter-ac5-{mode}.txt"
    _register_counter_workflow(monkeypatch, f"gh792_ac5_{mode}_counter", counter, mode=mode)

    ctx = dict(_MINIMAL_CTX_DICT)
    run_id = f"gh792-ac5-{mode}-run"
    key = phase_key(run_id, f"gh792_ac5_{mode}_counter", ctx)

    assert telemetry_ctx.get_current_run() is None

    result = dbos_setup.execute_durable_workflow(
        f"gh792_ac5_{mode}_counter", ctx, run_id, event_log_path=event_log_path
    )
    assert result["status"] == mode, (
        f"AC5 (GH792) pre-stage: the workflow must return status=={mode!r}; got {result!r}"
    )

    sentinel = Path(event_log_path).parent / "phase-sentinels" / f"{key}.json"
    assert not sentinel.exists(), (
        f"AC5 (GH792): a {mode!r} result must never be cached; found a sentinel at {sentinel}."
    )

    rows = _read_events(event_log_path)
    stored = [r for r in rows if r.get("event_type") == "phase_sentinel_stored"]
    assert not stored, (
        f"AC5 (GH792): no 'phase_sentinel_stored' line may be emitted for a {mode!r} result; "
        f"found {stored!r}"
    )


# ===========================================================================
# AC6 — no event_log_path ⇒ no emit, no raise (store_success's new run_id arg)
# ===========================================================================

def test_ac6_store_success_no_event_log_path_no_emit_no_raise_gh792(tmp_path):
    """AC6(a): store_success(key, ok_result, None, "rid") returns None, raises
    nothing, writes nothing — the 4th positional `run_id` argument this ship
    adds to `store_success`'s signature (spec §2.2).

    AC6(b): the §2.1 defense-in-depth guard inside `_emit_sentinel_event`
    (`if not event_log_path: return`) is a declared-dead branch from
    production (store_success returns before it; SERVE requires a cached hit
    which requires a path) — pinned DIRECTLY here rather than left unexercised
    (§1aa / GH723-F1 class).

    Pre-GREEN: FAIL — today's store_success(key, result, event_log_path) takes
    only 3 params (TypeError on the 4th positional arg), and
    `lib.phase_sentinel._emit_sentinel_event` does not exist yet (ImportError).
    """
    from lib.phase_sentinel import store_success  # type: ignore[import]

    ok_result = {
        "status": "ok", "data": {"x": 1}, "duration_ms": 1, "step_name": "y",
        "error": None, "error_code": None, "suggestion": None, "recoverable": True,
    }

    ret = store_success("gh792-ac6-key", ok_result, None, "rid")

    assert ret is None, f"AC6a (GH792): store_success must return None; got {ret!r}"

    stray = list(tmp_path.rglob("phase-sentinels"))
    assert stray == [], (
        f"AC6a (GH792): no 'phase-sentinels' dir may be created anywhere when "
        f"event_log_path is None; found {stray!r}"
    )

    from lib.phase_sentinel import _emit_sentinel_event  # type: ignore[import]

    direct_ret = _emit_sentinel_event("x", {}, "rid", None)
    assert direct_ret is None, (
        f"AC6b (GH792): _emit_sentinel_event must return None when event_log_path is "
        f"falsy (the declared dead-branch guard, §2.1); got {direct_ret!r}"
    )
    stray2 = list(tmp_path.rglob("phase-sentinels"))
    assert stray2 == [], (
        f"AC6b (GH792): direct call must write nothing anywhere; found {stray2!r}"
    )


# ===========================================================================
# AC7a/AC7b — emit never breaks a green phase (§1n OWN-all); INFRA seam only.
#
# BLOCKER-1 fix: fault the sink's `.append`, NEVER the `get_event_sink` factory
# itself. `execute_engine` performs the SAME local
# `from event_sink import get_event_sink` to build the engine's own sink
# (phase_sentinel.py:156-159) and lets exceptions propagate (:145) — a raising
# FACTORY blows up inside execute_engine on any cache-MISS path, before any
# emit is attempted, and no spec-compliant GREEN could ever pass that. The
# factory here stays a working factory; only the returned sink's `.append`
# raises. Discriminating assert: the sink records the emit was ATTEMPTED
# (`event_type in sink.attempts`) — without it a no-op GREEN (that never calls
# the emit seam at all) would satisfy a weaker "status==ok + sentinel exists"
# assertion, since both already hold today with zero telemetry.
# ===========================================================================

class _RecordingBrokenSink:
    """A working factory hands this back; its `.append` always raises, but it
    records every event_type it was asked to append BEFORE raising."""

    def __init__(self) -> None:
        self.attempts: list[str] = []

    def append(self, event_type: str, payload: dict[str, Any], run_id: str | None = None) -> Any:
        self.attempts.append(event_type)
        raise RuntimeError("gh792-ac7-append-boom")


def test_ac7a_store_emit_failure_does_not_break_green_phase_gh792(tmp_path, monkeypatch):
    """AC7a: FRESH key. `event_sink.get_event_sink` is patched to return a
    recording sink whose `.append` RAISES (INFRA seam, not the factory, not
    the UUT). On the STORE path: the sink records that 'phase_sentinel_stored'
    was ATTEMPTED, `execute_native_workflow` still returns status == "ok"
    (§1n OWN-all), and the sentinel is still committed to disk.

    Pre-GREEN: FAIL — 0 attempts recorded (`_emit_sentinel_event` does not
    exist yet, so nothing calls `get_event_sink` from the STORE path today).
    """
    from lib.phase_sentinel import execute_native_workflow, phase_key  # type: ignore[import]
    import event_sink  # type: ignore[import]

    monkeypatch.setenv(_ENV, "native")
    real_tmp = Path(realpath(str(tmp_path)))
    event_log_path = _event_log_path(tmp_path)
    counter = real_tmp / "counter-ac7a.txt"
    _register_counter_workflow(monkeypatch, "gh792_ac7a_counter", counter)

    sink = _RecordingBrokenSink()
    monkeypatch.setattr(event_sink, "get_event_sink", lambda path=None: sink)

    ctx = dict(_MINIMAL_CTX_DICT)
    run_id = "gh792-ac7a-run"
    key = phase_key(run_id, "gh792_ac7a_counter", ctx)

    assert telemetry_ctx.get_current_run() is None

    result = execute_native_workflow("gh792_ac7a_counter", ctx, run_id, event_log_path, key)

    assert "phase_sentinel_stored" in sink.attempts, (
        f"AC7a (GH792): expected a 'phase_sentinel_stored' emit ATTEMPT (discriminating "
        f"assert — a no-op GREEN would otherwise pass); recorded attempts = {sink.attempts!r}"
    )
    assert result["status"] == "ok", (
        f"AC7a (GH792): a raising sink.append must NOT break the green phase (§1n OWN-all); "
        f"got {result!r}"
    )
    sentinel = Path(event_log_path).parent / "phase-sentinels" / f"{key}.json"
    assert sentinel.exists(), (
        f"AC7a (GH792): the sentinel must still be committed to disk even when the emit "
        f"attempt fails; expected {sentinel} to exist."
    )


def test_ac7b_serve_emit_failure_does_not_break_served_phase_gh792(tmp_path, monkeypatch):
    """AC7b: sentinel PRE-STAGED via a REAL store (execute_native_workflow with
    the real sink) so the second call is a cache HIT and `execute_engine` is
    never entered. THEN the recording-raising sink is patched in: the SERVE
    still records a 'phase_sentinel_resumed' attempt, and the call still
    returns the cached status == "ok".

    Pre-GREEN: FAIL — 0 attempts recorded (`_emit_sentinel_event` does not
    exist yet, so nothing calls `get_event_sink` from the SERVE path today).
    """
    from lib.phase_sentinel import execute_native_workflow, phase_key  # type: ignore[import]
    import event_sink  # type: ignore[import]

    monkeypatch.setenv(_ENV, "native")
    real_tmp = Path(realpath(str(tmp_path)))
    event_log_path = _event_log_path(tmp_path)
    counter = real_tmp / "counter-ac7b.txt"
    _register_counter_workflow(monkeypatch, "gh792_ac7b_counter", counter)

    ctx = dict(_MINIMAL_CTX_DICT)
    run_id = "gh792-ac7b-run"
    key = phase_key(run_id, "gh792_ac7b_counter", ctx)

    assert telemetry_ctx.get_current_run() is None

    # Pre-stage: a REAL store, with the real sink (no fault injected yet).
    first = execute_native_workflow("gh792_ac7b_counter", ctx, run_id, event_log_path, key)
    assert first["status"] == "ok", (
        f"AC7b (GH792) pre-stage: the first call must really store a success; got {first!r}"
    )
    assert _read_counter(counter) == 1, (
        f"AC7b (GH792) pre-stage: the first call must execute the engine once; "
        f"counter={_read_counter(counter)!r}"
    )

    # NOW patch in the broken sink — the second call must be a cache HIT.
    sink = _RecordingBrokenSink()
    monkeypatch.setattr(event_sink, "get_event_sink", lambda path=None: sink)

    served = execute_native_workflow("gh792_ac7b_counter", ctx, run_id, event_log_path, key)

    assert "phase_sentinel_resumed" in sink.attempts, (
        f"AC7b (GH792): expected a 'phase_sentinel_resumed' emit ATTEMPT (discriminating "
        f"assert); recorded attempts = {sink.attempts!r}"
    )
    assert served["status"] == "ok", (
        f"AC7b (GH792): a raising sink.append must NOT break the served phase — the cached "
        f"result must still be returned; got {served!r}"
    )
    assert _read_counter(counter) == 1, (
        f"AC7b (GH792): the SERVE must still be a cache hit (engine not re-executed); "
        f"real counter={_read_counter(counter)!r}, expected 1."
    )


# ===========================================================================
# AC8 — occurrence-counter semantics (§1ab): serves are not deduped
# ===========================================================================

def test_ac8_third_call_emits_second_phase_sentinel_resumed_gh792(tmp_path, monkeypatch):
    """AC8: a third identical call emits a SECOND `phase_sentinel_resumed`
    (total 2 across the two SERVE calls) — serves are not deduped (§1ab
    idempotency declaration: these are occurrence counters, not idempotent
    state; the tripwire counts hits per window).

    Pre-GREEN: FAIL — 0 lines emitted today at all.
    """
    from lib.phase_sentinel import phase_key  # type: ignore[import]

    monkeypatch.setenv(_ENV, "native")
    real_tmp = Path(realpath(str(tmp_path)))
    event_log_path = _event_log_path(tmp_path)
    counter = real_tmp / "counter-ac8.txt"
    _register_counter_workflow(monkeypatch, "gh792_ac8_counter", counter)

    ctx = dict(_MINIMAL_CTX_DICT)
    run_id = "gh792-ac8-run"
    key = phase_key(run_id, "gh792_ac8_counter", ctx)

    assert telemetry_ctx.get_current_run() is None

    # call 1: fresh/STORE
    dbos_setup.execute_durable_workflow(
        "gh792_ac8_counter", ctx, run_id, event_log_path=event_log_path
    )
    # call 2: SERVE #1
    dbos_setup.execute_durable_workflow(
        "gh792_ac8_counter", ctx, run_id, event_log_path=event_log_path
    )
    # call 3: SERVE #2
    dbos_setup.execute_durable_workflow(
        "gh792_ac8_counter", ctx, run_id, event_log_path=event_log_path
    )

    rows = _read_events(event_log_path)
    resumed = [
        r for r in rows
        if r.get("event_type") == "phase_sentinel_resumed" and r.get("payload", {}).get("phase_key") == key
    ]
    assert len(resumed) == 2, (
        f"AC8 (GH792): expected exactly 2 'phase_sentinel_resumed' lines for phase_key "
        f"{key!r} after 2 SERVE calls (occurrence counter, no dedup); found "
        f"{len(resumed)} — full observed event types = {_dump_event_types(rows)}, "
        f"resumed rows = {resumed!r}"
    )
    assert _read_counter(counter) == 1, (
        f"AC8 (GH792) sanity: neither SERVE may re-execute the engine; real counter="
        f"{_read_counter(counter)!r}, expected 1."
    )


# ===========================================================================
# AC9 — legacy call-shape preserved (§1o)
# ===========================================================================

def test_ac9_legacy_three_arg_store_success_still_emits_gh792(tmp_path):
    """AC9: store_success(key, result, event_log_path) — 3 positional args, no
    run_id — still stores AND still emits (envelope run_id == "ad-hoc").

    Pre-GREEN: FAIL — today's store_success never emits to the event-log file
    (only `telemetry_ctx.emit_safe`, a no-op without a bound run-context), so
    no line lands at all.
    """
    from lib.phase_sentinel import store_success  # type: ignore[import]

    real_tmp = Path(realpath(str(tmp_path)))
    event_log_path = str(real_tmp / ".hal-build" / "events.jsonl")
    os.makedirs(os.path.dirname(event_log_path), exist_ok=True)

    key = "gh792-ac9-key"
    ok_result = {
        "status": "ok", "data": {"x": 1}, "duration_ms": 1, "step_name": "y",
        "error": None, "error_code": None, "suggestion": None, "recoverable": True,
    }

    store_success(key, ok_result, event_log_path)  # 3 positional args — legacy shape

    sentinel = Path(event_log_path).parent / "phase-sentinels" / f"{key}.json"
    assert sentinel.exists(), (
        f"AC9 (GH792) pre-stage: the legacy 3-arg call must still write the sentinel; "
        f"expected {sentinel} to exist."
    )

    rows = _read_events(event_log_path)
    stored = [r for r in rows if r.get("event_type") == "phase_sentinel_stored"]
    assert stored, (
        f"AC9 (GH792): the legacy 3-arg store_success call must still emit "
        f"'phase_sentinel_stored'; observed event types = {_dump_event_types(rows)}"
    )
    assert stored[0].get("run_id") == "ad-hoc", (
        f"AC9 (GH792): with no run_id supplied, the envelope must fall back to 'ad-hoc' "
        f"(event_log.py:109); got {stored[0].get('run_id')!r}, full row={stored[0]!r}"
    )
