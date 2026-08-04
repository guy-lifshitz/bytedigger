"""RED tests for GH795 — resolver emit-seam: wrapper-layer telemetry direct-sink.

Spec: SHARED/memory/Decisions/2026-07-14_C8016A42_gh795_resolver_emit_seam_spec.md
(FROZEN). §1c Class: SYSTEMATIC — the shared §1g helper
`lib/observability/emit_resolver.py::emit_resolver_resolved` gets new
keyword-only `event_log_path`/`run_id` params, threaded through
`lib/phase_sentinel.py::resolve_durable_backend` and the one call site with
both values in scope, `lib/dbos_setup.py:407`.

§1l forcing function: every AC that can be driven through the new kwargs
directly exercises them (today's `emit_resolver_resolved`/`resolve_durable_backend`
signatures do not accept `event_log_path`/`run_id` -> TypeError at call time).
AC4/AC8/AC9 additionally drive the REAL production entry point
`lib.dbos_setup.execute_durable_workflow` end-to-end with
HAL_ENGINE_DURABLE_BACKEND=native and NO bound run-context (mirrors
test_gh792_native_sentinel_emit.py's proof that this is possible without
`telemetry_ctx.set_current_run`), and fail today because the internal
`resolve_durable_backend()` call at dbos_setup.py:407 is arg-less -> the emit
is dropped into the no-op `telemetry_ctx.emit_safe` path, so 0 records land
in the real event-log file. AC10/AC11 are §1g/back-compat correctness GUARDS
that are expected to already PASS pre-GREEN (no production behavior changes
under test there) and must stay green through GREEN.

§1q / D1CF5FDF: every symbol imported at module level
(`emit_resolver_resolved`, `resolve_durable_backend`, `E_DURABLE_BACKEND_UNKNOWN`,
`dbos_setup`, `telemetry_ctx`) already exists on the CURRENT tree — only the
new *keyword-only params* are missing. The module COLLECTS cleanly; every
test fails at call/assert time, never at collection time. No
`spec_from_file_location` / `exec_module`, no module-level `sys.path`
mutation (tests/conftest.py already exposes engine_py root on sys.path).

No UUT (`emit_resolver_resolved`, `resolve_durable_backend`,
`execute_durable_workflow`) is ever mocked or patched (7AD3D393). AC2/AC2b/AC13
patch `telemetry_ctx.emit_safe` / `event_sink.get_event_sink` — both
COLLABORATORS/INFRA, never the UUT.

v2 (re-frozen after gate REJECT #1, 4 MAJOR): AC4's source assertion corrected
to the env-var NAME (`HAL_ENGINE_DURABLE_BACKEND`); AC6 rewritten to a
genuinely-unwritable parent-is-a-file case; AC10 made whitespace-insensitive
(regex over the whole file, not a per-line scan); AC2 strengthened with
emit_safe/get_event_sink spies plus the falsy-"" case (AC2b); AC13 added
(discriminating broken-sink guard, mirrors GH792 AC7a/AC7b); AC14 added
(exclusivity — the central "not both channels" contract). AC14 is the ONE
test in this file that binds a run-context via `telemetry_ctx.set_current_run`
— a scoped, documented exception, cleared in try/finally.
"""
from __future__ import annotations

import json
import os
import re
from os.path import realpath
from pathlib import Path
from typing import Any

import pytest

from bytedigger_engine import event_sink  # type: ignore[import]  # AC2 spy target — INFRA, never the UUT

# Skip the whole module if dbos is absent (lib.dbos_setup imports it at module level).
try:
    from bytedigger_engine.lib import dbos_setup as dbos_setup  # type: ignore[import]  # attr access only — never patched
except ImportError:  # pragma: no cover
    pytest.skip("dbos not installed", allow_module_level=True)

from bytedigger_engine import telemetry_ctx  # type: ignore[import]
from bytedigger_engine.lib.observability.emit_resolver import emit_resolver_resolved  # type: ignore[import]
from bytedigger_engine.lib import phase_sentinel  # type: ignore[import]
# §1q / D1CF5FDF: resolve `resolve_durable_backend` / `E_DURABLE_BACKEND_UNKNOWN`
# as MODULE ATTRIBUTES at call time (never module-level `from ... import`) —
# tests/test_gh612_phase_sentinel_seam.py calls `importlib.reload(lib.phase_sentinel)`
# in the same suite run, which rebinds a NEW `E_DURABLE_BACKEND_UNKNOWN` class
# object in the module namespace. A name bound at import time would freeze
# the OLD (pre-reload) class object, so `pytest.raises(E_DURABLE_BACKEND_UNKNOWN)`
# would silently stop matching the exception the (also stale) old function
# raises, whenever this file collects after test_gh612_phase_sentinel_seam.py.

_ENV = "HAL_ENGINE_DURABLE_BACKEND"

_MINIMAL_CTX_DICT: dict[str, Any] = {
    "tenant_id": "hal-test",
    "scope": None,
    "db_path": None,
    "org_config": {"complexity": "SIMPLE", "phase": "spec"},
    "question": "gh795 resolver emit seam test",
    "session_id": "gh795-test",
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
# Autouse isolation (no global state leaks between tests) — mirrors GH792/GH612
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _env_isolation(monkeypatch):
    monkeypatch.delenv(_ENV, raising=False)
    monkeypatch.delenv("HAL_DBOS_DB_PATH", raising=False)
    monkeypatch.delenv("HAL_DIR", raising=False)
    yield


@pytest.fixture(autouse=True)
def _telemetry_reset():
    # Only AC14 binds a run-context (scoped exception, documented there,
    # cleared in try/finally) — every other AC lands its event through the
    # direct-sink seam, exactly the blindness this ship fixes. This fixture
    # is a belt-and-suspenders backstop against any leak between tests.
    telemetry_ctx.clear_current_run()
    yield
    telemetry_ctx.clear_current_run()


# ---------------------------------------------------------------------------
# Real event-log helpers (§1l forcing function) — mirror GH792's helpers
# ---------------------------------------------------------------------------

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


def _register_noop_workflow(monkeypatch, name: str) -> None:
    """Register a REAL engine workflow with a trivial one-step body so
    `execute_durable_workflow` runs a full real invocation (§1l — the
    counter-file discipline from GH792 is not needed here since no AC in
    this file asserts on step-execution counts; the resolver's own emit is
    the sole thing under test)."""
    from bytedigger_engine import workflows as workflows_pkg  # type: ignore
    from bytedigger_engine.contracts import StepContract, StepResult, WorkflowDefinition  # type: ignore

    def _step(_ctx, _prev):
        return StepResult(status="ok", data={"echoed": name}, duration_ms=0, step_name=name)

    orig_register_all = workflows_pkg.register_all

    def _patched_register_all(engine):
        orig_register_all(engine)
        engine.register(name, WorkflowDefinition(name=name, steps=[StepContract(name=name, execute=_step)]))

    monkeypatch.setattr(workflows_pkg, "register_all", _patched_register_all)


# ===========================================================================
# AC1 — direct-sink write, exactly 1 record
# ===========================================================================

def test_ac1_direct_sink_appends_exactly_one_record_gh795(tmp_path):
    """AC1: emit_resolver_resolved("durable_backend","default","dbos",
    event_log_path=<tmp>, run_id="R1") appends exactly 1 record to <tmp> with
    event_type=="resolver_durable_backend_resolved", run_id=="R1",
    payload.helper/source/value as given.

    Pre-GREEN: FAIL — today's emit_resolver_resolved(helper, source, value,
    extra=None) accepts no `event_log_path`/`run_id` keyword -> TypeError.
    """
    event_log_path = _event_log_path(tmp_path)

    emit_resolver_resolved(
        "durable_backend", "default", "dbos",
        event_log_path=event_log_path, run_id="R1",
    )

    rows = _read_events(event_log_path)
    matching = [r for r in rows if r.get("event_type") == "resolver_durable_backend_resolved"]
    assert len(matching) == 1, (
        f"AC1 (GH795): expected exactly 1 'resolver_durable_backend_resolved' record "
        f"at {event_log_path!r}; observed = {_dump_event_types(rows)} full rows={rows!r}"
    )
    row = matching[0]
    assert row.get("run_id") == "R1", f"AC1 (GH795): expected run_id=='R1'; got {row!r}"
    payload = row.get("payload", {})
    assert payload.get("helper") == "durable_backend", f"AC1 (GH795): {row!r}"
    assert payload.get("source") == "default", f"AC1 (GH795): {row!r}"
    assert payload.get("value") == "dbos", f"AC1 (GH795): {row!r}"


# ===========================================================================
# AC2 — no event_log_path, no run-context -> emit_safe branch taken, no raise
# ===========================================================================

def test_ac2_none_path_takes_emit_safe_branch_never_calls_get_event_sink_gh795(tmp_path, monkeypatch):
    """AC2 (v2 MINOR-2 strengthened): explicit event_log_path=None, run_id=None
    (no bound run-context) -> the emit_safe branch is TAKEN: `telemetry_ctx.
    emit_safe` (spied — a COLLABORATOR, not the UUT) is called exactly once,
    `event_sink.get_event_sink` is NEVER called, 0 bytes at <tmp>, no raise.

    Pre-GREEN: FAIL — today's signature does not accept these keywords at
    all (even as None) -> TypeError on the call itself.
    """
    event_log_path = _event_log_path(tmp_path)
    os.remove(event_log_path) if os.path.exists(event_log_path) else None

    emit_safe_calls: list[tuple[str, dict]] = []
    orig_emit_safe = telemetry_ctx.emit_safe

    def _spy_emit_safe(event_type: str, payload: dict) -> None:
        emit_safe_calls.append((event_type, payload))
        orig_emit_safe(event_type, payload)

    monkeypatch.setattr(telemetry_ctx, "emit_safe", _spy_emit_safe)

    sink_calls: list[Any] = []

    class _InertStubSink:
        def append(self, event_type: str, payload: dict, run_id: str | None = None) -> dict:
            return {}

    def _spy_get_event_sink(path=None):
        # AC2 safety net: if the guard under test is ever non-conforming,
        # NEVER delegate to the real factory (that would resolve
        # get_event_sink(None) -> EventLog(None) -> default_log_path() ->
        # HAL's CENTRAL events.jsonl, appending a real
        # resolver_durable_backend_resolved row into production telemetry).
        sink_calls.append(path)
        return _InertStubSink()

    monkeypatch.setattr(event_sink, "get_event_sink", _spy_get_event_sink)

    assert telemetry_ctx.get_current_run() is None

    emit_resolver_resolved(
        "durable_backend", "default", "dbos",
        event_log_path=None, run_id=None,
    )

    assert len(emit_safe_calls) == 1, (
        f"AC2 (GH795): expected telemetry_ctx.emit_safe to be called exactly once "
        f"when event_log_path=None; got {emit_safe_calls!r}"
    )
    assert sink_calls == [], (
        f"AC2 (GH795): event_sink.get_event_sink must NEVER be called when "
        f"event_log_path=None; got {sink_calls!r}"
    )
    assert not os.path.exists(event_log_path) or os.path.getsize(event_log_path) == 0, (
        f"AC2 (GH795): expected 0 bytes written to {event_log_path!r} when "
        f"event_log_path=None and no run-context is bound."
    )


def test_ac2b_falsy_empty_string_path_also_takes_emit_safe_branch_gh795(tmp_path, monkeypatch):
    """AC2b (v2 MINOR-2): event_log_path="" (falsy-but-not-None) must ALSO
    take the emit_safe branch, proving the guard is truthiness (`if
    event_log_path:`), never `is not None`. An `is not None` guard would
    call `get_event_sink("")` which silently resolves to
    `default_log_path()` — HAL's CENTRAL events.jsonl — a serious
    misroute this test discriminates against.

    Pre-GREEN: FAIL — today's signature does not accept `event_log_path` at
    all -> TypeError on the call itself.
    """
    sink_calls: list[Any] = []

    class _InertStubSink:
        def append(self, event_type: str, payload: dict, run_id: str | None = None) -> dict:
            return {}

    def _spy_get_event_sink(path=None):
        # AC2b safety net — see AC2 above: never delegate to the real
        # factory, which under a non-conforming GREEN would misroute a
        # falsy path into HAL's central events.jsonl.
        sink_calls.append(path)
        return _InertStubSink()

    monkeypatch.setattr(event_sink, "get_event_sink", _spy_get_event_sink)

    assert telemetry_ctx.get_current_run() is None

    emit_resolver_resolved(
        "durable_backend", "default", "dbos",
        event_log_path="", run_id=None,
    )

    assert sink_calls == [], (
        f"AC2b (GH795): event_sink.get_event_sink must NEVER be called when "
        f"event_log_path=='' (falsy) — an `is not None` guard would call it "
        f"with a falsy path and misroute to HAL's central log; got {sink_calls!r}"
    )


# ===========================================================================
# AC3 — legacy positional 4-arg form unchanged
# ===========================================================================

def test_ac3_legacy_positional_extra_shape_unchanged_gh795(tmp_path):
    """AC3: emit_resolver_resolved(h, s, v, {"k":"v"}) — legacy positional
    4-arg call — still shapes payload.extra == {"k":"v"}. Observed via the
    direct-sink form (pass event_log_path/run_id too) so it is observable
    without a bound run-context.

    Pre-GREEN: FAIL — today's call accepts the 4 positionals fine, but adding
    the event_log_path/run_id keywords (required to observe the result
    without a bound telemetry context) raises TypeError, since those
    keywords do not exist yet.
    """
    event_log_path = _event_log_path(tmp_path)

    emit_resolver_resolved(
        "some_helper", "some_source", "some_value", {"k": "v"},
        event_log_path=event_log_path, run_id="R-ac3",
    )

    rows = _read_events(event_log_path)
    matching = [r for r in rows if r.get("event_type") == "resolver_some_helper_resolved"]
    assert matching, (
        f"AC3 (GH795): expected a 'resolver_some_helper_resolved' record; "
        f"observed = {_dump_event_types(rows)}"
    )
    assert matching[0]["payload"].get("extra") == {"k": "v"}, (
        f"AC3 (GH795): expected payload.extra == {{'k':'v'}}; got {matching[0]!r}"
    )


# ===========================================================================
# AC4 — real execute_durable_workflow, native, no bound run-context (§1y)
# ===========================================================================

def test_ac4_real_execute_durable_workflow_native_no_run_context_gh795(tmp_path, monkeypatch):
    """AC4 (§1y load-bearing): the REAL production entry point
    `dbos_setup.execute_durable_workflow(wf, ctx, run_id, event_log_path)`
    under HAL_ENGINE_DURABLE_BACKEND=native and NO bound run-context lands a
    'resolver_durable_backend_resolved' record with payload.value=="native",
    payload.source=="HAL_ENGINE_DURABLE_BACKEND" (the env-var NAME is the
    source token, phase_sentinel.py:44/:119), row['run_id'] == the real run id.

    Pre-GREEN: FAIL — dbos_setup.py:407 calls resolve_durable_backend() with
    no arguments; the emit inside it goes to the no-op telemetry_ctx.emit_safe
    (no bound run-context) -> 0 matching records land in the real event log.
    """
    monkeypatch.setenv(_ENV, "native")
    event_log_path = _event_log_path(tmp_path)
    _register_noop_workflow(monkeypatch, "gh795_ac4_wf")

    ctx = dict(_MINIMAL_CTX_DICT)
    run_id = "gh795-ac4-run"

    assert telemetry_ctx.get_current_run() is None

    dbos_setup.execute_durable_workflow("gh795_ac4_wf", ctx, run_id, event_log_path=event_log_path)

    rows = _read_events(event_log_path)
    matching = [r for r in rows if r.get("event_type") == "resolver_durable_backend_resolved"]
    assert matching, (
        f"AC4 (GH795): expected a 'resolver_durable_backend_resolved' record from the "
        f"real execute_durable_workflow call; observed = {_dump_event_types(rows)}"
    )
    row = matching[0]
    assert row.get("payload", {}).get("value") == "native", f"AC4 (GH795): {row!r}"
    # v2 (MAJOR-2): prod emits the env-var NAME as the source token
    # (phase_sentinel.py:44/:119 -> _DURABLE_BACKEND_ENV = "HAL_ENGINE_DURABLE_BACKEND"),
    # not the literal string "env". GREEN must NOT be made to change this shipped field.
    assert row.get("payload", {}).get("source") == "HAL_ENGINE_DURABLE_BACKEND", (
        f"AC4 (GH795): {row!r}"
    )
    assert row.get("run_id") == run_id, f"AC4 (GH795): expected run_id=={run_id!r}; got {row!r}"


# ===========================================================================
# AC5 — resolve_durable_backend direct-sink, env unset -> "dbos"
# ===========================================================================

def test_ac5_resolve_durable_backend_direct_sink_default_gh795(tmp_path):
    """AC5 (GH839 B1 flip): resolve_durable_backend(event_log_path=<tmp>, run_id="R1")
    with HAL_ENGINE_DURABLE_BACKEND unset -> returns "native" AND emits
    value=="native", source=="default".

    Pre-GREEN: FAIL — today's resolve_durable_backend() accepts no keyword
    arguments at all -> TypeError.
    """
    event_log_path = _event_log_path(tmp_path)

    backend = phase_sentinel.resolve_durable_backend(event_log_path=event_log_path, run_id="R1")

    assert backend == "native", f"AC5 (GH795/GH839 B1): expected 'native'; got {backend!r}"
    rows = _read_events(event_log_path)
    matching = [r for r in rows if r.get("event_type") == "resolver_durable_backend_resolved"]
    assert matching, f"AC5 (GH795/GH839 B1): observed = {_dump_event_types(rows)}"
    assert matching[0]["payload"].get("value") == "native", f"AC5 (GH795/GH839 B1): {matching[0]!r}"
    assert matching[0]["payload"].get("source") == "default", f"AC5 (GH795/GH839 B1): {matching[0]!r}"


# ===========================================================================
# AC6 — §1n OWN-all: bad directory never raises
# ===========================================================================

def test_ac6_unwritable_path_parent_is_a_file_never_raises_gh795(tmp_path):
    """AC6 (§1n OWN-all, v2 MAJOR-3): a merely *absent* directory does NOT
    reach the OWN-all branch — `event_log.py:93` `mkdir(parents=True,
    exist_ok=True)` creates it and the write succeeds. The genuinely
    unwritable case is when the parent path component is an existing
    REGULAR FILE: `<tmp>/afile` is a file, so `event_log_path =
    <tmp>/afile/events.jsonl` makes `EventLog.__init__`'s mkdir raise
    `FileExistsError` (root-safe; no chmod needed).
    `resolve_durable_backend(...)` must still RETURN the backend and raise
    NOTHING.

    Pre-GREEN: FAIL — today's resolve_durable_backend() accepts no keyword
    arguments -> calling with event_log_path=/run_id= raises TypeError
    (a real, unswallowed exception — the opposite of the AC6 guarantee).
    """
    a_file = tmp_path / "afile"
    a_file.write_text("not a directory")
    bogus_path = str(a_file / "events.jsonl")

    backend = phase_sentinel.resolve_durable_backend(event_log_path=bogus_path, run_id="R1")

    assert backend in ("dbos", "native"), (
        f"AC6 (GH795): expected a known backend string; got {backend!r}"
    )


# ===========================================================================
# AC7 — fail-closed preserved: bogus env still raises, 0 records written
# ===========================================================================

def test_ac7_fail_closed_bogus_env_raises_and_writes_nothing_gh795(tmp_path, monkeypatch):
    """AC7: HAL_ENGINE_DURABLE_BACKEND=bogus -> E_DURABLE_BACKEND_UNKNOWN
    raised AND 0 records land in <tmp>.

    Pre-GREEN: FAIL — the call itself raises TypeError (unexpected keyword
    argument), not E_DURABLE_BACKEND_UNKNOWN, so pytest.raises(
    E_DURABLE_BACKEND_UNKNOWN) does not match -> test fails.
    """
    monkeypatch.setenv(_ENV, "bogus")
    event_log_path = _event_log_path(tmp_path)

    with pytest.raises(phase_sentinel.E_DURABLE_BACKEND_UNKNOWN):
        phase_sentinel.resolve_durable_backend(event_log_path=event_log_path, run_id="R1")

    rows = _read_events(event_log_path)
    assert rows == [], (
        f"AC7 (GH795): expected 0 records written on the fail-closed path; got {rows!r}"
    )


# ===========================================================================
# AC8 — §1ab re-entry (c): same run_id twice -> 2 records, same run_id
# ===========================================================================

def test_ac8_same_run_id_twice_yields_two_records_gh795(tmp_path, monkeypatch):
    """AC8 (§1ab re-entry (c)): two real execute_durable_workflow calls with
    the SAME run_id (native) -> exactly 2 'resolver_durable_backend_resolved'
    records, BOTH carrying that same run_id (duplicates by design — an
    occurrence counter, not deduped state).

    Pre-GREEN: FAIL — 0 records land at all today (same root cause as AC4).
    """
    monkeypatch.setenv(_ENV, "native")
    event_log_path = _event_log_path(tmp_path)
    _register_noop_workflow(monkeypatch, "gh795_ac8_wf")

    ctx = dict(_MINIMAL_CTX_DICT)
    run_id = "gh795-ac8-run"

    dbos_setup.execute_durable_workflow("gh795_ac8_wf", ctx, run_id, event_log_path=event_log_path)
    dbos_setup.execute_durable_workflow("gh795_ac8_wf", ctx, run_id, event_log_path=event_log_path)

    rows = _read_events(event_log_path)
    matching = [r for r in rows if r.get("event_type") == "resolver_durable_backend_resolved"]
    assert len(matching) == 2, (
        f"AC8 (GH795): expected exactly 2 'resolver_durable_backend_resolved' records "
        f"after 2 calls with the same run_id; found {len(matching)} — "
        f"observed = {_dump_event_types(rows)}, matching={matching!r}"
    )
    for row in matching:
        assert row.get("run_id") == run_id, (
            f"AC8 (GH795): expected every record's run_id=={run_id!r}; got {row!r}"
        )


# ===========================================================================
# AC9 — §1o consumer: every record carries top-level event_type + ts
# ===========================================================================

def test_ac9_records_carry_event_type_and_ts_keys_gh795(tmp_path, monkeypatch):
    """AC9 (§1o consumer cite-verify): every emitted record has top-level
    'event_type' AND 'ts' keys — exactly the pair `weekly-tripwire.sh:161`
    selects on (`select(.event_type==$ev and .ts>=$cut)`).

    Pre-GREEN: FAIL — 0 records land at all today (same root cause as AC4),
    so there is nothing to check keys on.
    """
    monkeypatch.setenv(_ENV, "native")
    event_log_path = _event_log_path(tmp_path)
    _register_noop_workflow(monkeypatch, "gh795_ac9_wf")

    ctx = dict(_MINIMAL_CTX_DICT)
    run_id = "gh795-ac9-run"

    dbos_setup.execute_durable_workflow("gh795_ac9_wf", ctx, run_id, event_log_path=event_log_path)

    rows = _read_events(event_log_path)
    matching = [r for r in rows if r.get("event_type") == "resolver_durable_backend_resolved"]
    assert matching, (
        f"AC9 (GH795): expected at least one 'resolver_durable_backend_resolved' record; "
        f"observed = {_dump_event_types(rows)}"
    )
    for row in matching:
        assert "event_type" in row, f"AC9 (GH795): missing 'event_type' key in {row!r}"
        assert "ts" in row, f"AC9 (GH795): missing 'ts' key in {row!r}"


# ===========================================================================
# AC10 — §1g single-source-of-truth (guard — expected PASS pre- and post-GREEN)
# ===========================================================================

def test_ac10_single_construction_and_emit_site_gh795():
    """AC10 (§1g): `f"resolver_{helper}_resolved"` is constructed in EXACTLY
    ONE prod module (`lib/observability/emit_resolver.py`), and
    'resolver_durable_backend_resolved' has EXACTLY ONE prod emit site
    (`lib/phase_sentinel.py` — the `emit_resolver_resolved("durable_backend",
    ...)` call).

    This is a §1g correctness GUARD: the single-source invariant already
    holds on the current tree (only emit_resolver.py builds the f-string;
    only phase_sentinel.py calls it with helper=="durable_backend") and must
    continue to hold after GREEN wires the new kwargs through. Expected PASS
    both pre- and post-GREEN.

    v2 (MINOR-1): the scan is whitespace/line-break INSENSITIVE — a
    per-physical-line match would false-FAIL a correct GREEN once the
    post-GREEN call (~105 chars with the new kwargs) wraps across lines,
    which §1s forbids GREEN from "fixing" (RED is read-only for GREEN).
    """
    engine_py_root = Path(__file__).resolve().parent.parent
    py_files = [
        p for p in engine_py_root.rglob("*.py")
        if "tests" not in p.relative_to(engine_py_root).parts
    ]

    construction_sites = []
    emit_call_sites = []
    call_re = re.compile(r'emit_resolver_resolved\(\s*"durable_backend"', re.S)
    for p in py_files:
        try:
            text = p.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        if 'f"resolver_{helper}_resolved"' in text or "f'resolver_{helper}_resolved'" in text:
            construction_sites.append(str(p.relative_to(engine_py_root)))
        if call_re.search(text):
            emit_call_sites.append(str(p.relative_to(engine_py_root)))

    assert construction_sites == ["bytedigger_engine/lib/observability/emit_resolver.py"], (  # bd#44: engine_py-relative, and the engine moved into the package
        f"AC10 (GH795): expected exactly 1 construction site for the resolver "
        f"event-type f-string; found {construction_sites!r}"
    )
    assert emit_call_sites == ["bytedigger_engine/lib/phase_sentinel.py"], (  # bd#44: engine_py-relative
        f"AC10 (GH795): expected exactly 1 prod emit site for "
        f"'resolver_durable_backend_resolved'; found {emit_call_sites!r}"
    )


# ===========================================================================
# AC11 — back-compat: arg-less resolve_durable_backend() still works
# ===========================================================================

def test_ac11_arg_less_call_still_works_gh795():
    """AC11 (back-compat): the arg-less resolve_durable_backend() call shape
    (the `init_dbos:115` call site, §2.3 — stays blind by design) STILL
    returns the backend and does not raise.

    This is a back-compat correctness GUARD: this exact call shape already
    works on the current tree and must continue to work after GREEN makes
    both new params keyword-only-and-defaulted. Expected PASS both pre- and
    post-GREEN.
    """
    backend = phase_sentinel.resolve_durable_backend()
    assert backend in ("dbos", "native"), (
        f"AC11 (GH795): expected a known backend string; got {backend!r}"
    )


# ===========================================================================
# AC13 — §1n OWN-all, discriminating (mirrors GH792 AC7a/AC7b)
# ===========================================================================

class _RecordingBrokenSink:
    """A working factory hands this back; its `.append` always raises, but it
    records every event_type it was asked to append BEFORE raising."""

    def __init__(self) -> None:
        self.attempts: list[str] = []

    def append(self, event_type: str, payload: dict, run_id: str | None = None):
        self.attempts.append(event_type)
        raise RuntimeError("gh795-ac13-append-boom")


def test_ac13_broken_sink_attempt_recorded_backend_still_returned_no_raise_gh795(tmp_path, monkeypatch):
    """AC13 (§1n OWN-all, discriminating): `event_sink.get_event_sink` is
    patched (INFRA seam — NEVER the UUT) to return a recording sink whose
    `.append` records the event_type then RAISES. (i) the emit was
    ATTEMPTED (sink recorded 'resolver_durable_backend_resolved' — a no-op
    GREEN would fail this discriminating assert), (ii)
    resolve_durable_backend still RETURNS the backend, (iii) nothing
    propagates out of the call.

    Pre-GREEN: FAIL — today's resolve_durable_backend() accepts no keyword
    arguments -> calling with event_log_path=/run_id= raises TypeError
    before get_event_sink is ever reached, so 0 attempts are recorded.
    """
    event_log_path = _event_log_path(tmp_path)
    sink = _RecordingBrokenSink()
    monkeypatch.setattr(event_sink, "get_event_sink", lambda path=None: sink)

    backend = phase_sentinel.resolve_durable_backend(event_log_path=event_log_path, run_id="R-ac13")

    assert "resolver_durable_backend_resolved" in sink.attempts, (
        f"AC13 (GH795): expected a 'resolver_durable_backend_resolved' emit ATTEMPT "
        f"(discriminating assert — a no-op GREEN would otherwise pass); recorded "
        f"attempts = {sink.attempts!r}"
    )
    assert backend in ("dbos", "native"), (
        f"AC13 (GH795): a raising sink.append must NOT break the caller (§1n OWN-all); "
        f"got backend={backend!r}"
    )


# ===========================================================================
# AC14 — exclusivity: direct-sink and bound run-context are mutually exclusive
# ===========================================================================

def test_ac14_direct_sink_and_bound_run_context_are_exclusive_gh795(tmp_path):
    """AC14 (exclusivity — the seam's central contract, v2 MAJOR-4): with a
    run-context bound over EventLog(<pathB>), calling emit_resolver_resolved(
    "durable_backend","default","dbos", event_log_path=<pathA>, run_id="R1")
    must write to EXACTLY ONE channel: exactly 1 record at <pathA>, exactly
    0 records at <pathB>. A GREEN that writes to BOTH channels (double-write,
    inflating the tripwire count) must fail this AC.

    NOTE (scoped exception, documented per the coordinator's instruction):
    this is the ONE test in this file that binds a run-context via
    `telemetry_ctx.set_current_run` — required to construct the "a run-context
    IS bound, but a direct event_log_path is ALSO given" scenario AC14 pins.
    The context is cleared in a try/finally so it never leaks to sibling
    tests (the module `_telemetry_reset` autouse fixture is an extra
    belt-and-suspenders backstop).

    Pre-GREEN: FAIL — today's emit_resolver_resolved has no event_log_path
    keyword -> TypeError before either channel is touched.
    """
    from bytedigger_engine.event_log import EventLog  # type: ignore[import]  # local — only this test binds a run-context

    real_tmp = Path(realpath(str(tmp_path)))
    path_a = str(real_tmp / "channel-a-events.jsonl")
    path_b = str(real_tmp / "channel-b-events.jsonl")

    telemetry_ctx.set_current_run(
        event_log=EventLog(path_b), run_id="bound-run-b", step_name="gh795_ac14",
    )
    try:
        emit_resolver_resolved(
            "durable_backend", "default", "dbos",
            event_log_path=path_a, run_id="R1",
        )
    finally:
        telemetry_ctx.clear_current_run()

    rows_a = _read_events(path_a)
    rows_b = _read_events(path_b)
    matching_a = [r for r in rows_a if r.get("event_type") == "resolver_durable_backend_resolved"]
    matching_b = [r for r in rows_b if r.get("event_type") == "resolver_durable_backend_resolved"]

    assert len(matching_a) == 1, (
        f"AC14 (GH795): expected exactly 1 record at the direct-sink path {path_a!r}; "
        f"found {len(matching_a)} — rows={rows_a!r}"
    )
    assert len(matching_b) == 0, (
        f"AC14 (GH795): expected exactly 0 records at the bound-run-context path "
        f"{path_b!r} (exclusive dispatch must not double-write); found "
        f"{len(matching_b)} — rows={rows_b!r}"
    )
