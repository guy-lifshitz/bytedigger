"""RED tests for GH375: tier-aware model dispatch (Class 4 — SIMPLE off Opus).

Spec: SHARED/memory/Decisions/2026-07-07_GH375_tier_model_dispatch_spec.md

§1q / D1CF5FDF compliance: `_load_tier_model` (llm_subprocess) and
`set_current_run_from` (telemetry_ctx) do NOT exist yet. All access to these
not-yet-existing symbols is DEFERRED inside test bodies via
`getattr(module, "name", None)` so this file COLLECTS cleanly and FAILS at
assert-time, never at collection time (D1CF5FDF).

§1l (stub-passability): no test patches `invoke_llm_subprocess`,
`_load_tier_model`, `set_current_run`, or `set_current_run_from` (the UUTs).
The only patched surfaces are `llm_subprocess._BACKENDS` (infra registry,
established seam per test_68E964FB_llm_backend_registry.py) and
`config_provider.models_config_path` (config seam, established seam per
test_32ED59E2_effort_lever.py). AC4-AC7 exercise the REAL
`invoke_llm_subprocess` dispatch path end-to-end with a real list-backed
event_log.

§1i: no singleton/timing races — thread-local run-ctx is pushed/popped
per-test with `finally: telemetry_ctx.clear_current_run()`, never asserted
against a race window.

Expected pre-GREEN (per spec §3/§4):
  AC1  — FAIL: _load_tier_model absent
  AC2  — FAIL: _load_tier_model absent
  AC3  — FAIL: _load_tier_model absent
  AC4  — FAIL: no tier-aware dispatch/emit in invoke_llm_subprocess yet
  AC5  — PASS: hard_gate=True already leaves model untouched (regression guard)
  AC6  — PASS: tier=None path is already byte-identical to legacy (regression guard)
  AC7  — FAIL: no caller_pinned-source emit yet
  AC8  — FAIL: set_current_run has no `tier` kwarg / _RunCtx has no `tier` field
  AC9  — FAIL: set_current_run_from absent
  AC10 — FAIL: engine.py does not thread tier= into set_current_run yet
  AC11 — FAIL: phase_5_implement.py / phase_6_review.py retry sites still use
               the field-by-field telemetry_ctx.set_current_run(...) direct
               call form (revised: asserts direct-call regex count == 0 AND
               set_current_run_from(' count >= 2 per file)
  AC12 — FAIL (or skip): SHARED/config/models.json has no claude.model_by_tier
               key yet (orchestrator-staged config edit, not yet landed)
  AC13 — FAIL: _tier_model_is_downgrade absent; no downgrade-only guard in
               invoke_llm_subprocess yet (haiku pin would be upgraded to
               sonnet without the floor)
"""
from __future__ import annotations

import inspect
import json
import re
from pathlib import Path

import pytest

import config_provider
import llm_subprocess
import telemetry_ctx
from contracts import StepContract, StepResult, WorkflowContext, WorkflowDefinition
from engine import WorkflowEngine
from tree_root import resolve_tree_root


# ─── shared helpers ─────────────────────────────────────────────────────────

class _FakeEventLog:
    """Real list-backed event_log — `.append(event_type, payload, run_id)`.

    Matches the duck-typed contract WorkflowEngine/telemetry_ctx.emit_safe
    expect (see engine.py:147, telemetry_ctx.emit_safe).
    """

    def __init__(self) -> None:
        self.events: list[tuple[str, dict, str]] = []

    def append(self, event_type: str, payload: dict, run_id: str) -> None:
        self.events.append((event_type, payload, run_id))

    def of_type(self, event_type: str) -> list[tuple[str, dict, str]]:
        return [e for e in self.events if e[0] == event_type]


def _set_run_with_tier(event_log, run_id, step_name, phase=None, tier=None):
    """Push telemetry_ctx run context, threading *tier* through when supported.

    Pre-GREEN, `telemetry_ctx.set_current_run()` does not accept a `tier`
    kwarg — TypeError is caught and the context is pushed WITHOUT tier
    (byte-identical to today's ctx). Post-GREEN the kwarg is accepted and
    threaded through. This is NOT a stub of the UUT: it calls the REAL
    `set_current_run` in both branches; it only tolerates the pre-GREEN
    signature so AC4-AC7 can exercise the real `invoke_llm_subprocess`
    dispatch path in both worlds without an uninformative setup-time crash.
    """
    try:
        telemetry_ctx.set_current_run(
            event_log=event_log, run_id=run_id, step_name=step_name,
            phase=phase, tier=tier,
        )
    except TypeError:
        telemetry_ctx.set_current_run(
            event_log=event_log, run_id=run_id, step_name=step_name, phase=phase,
        )


def _patched_backends_with_recorder(backend_name: str):
    """Return (patched_dict, recorder) — patched_dict clones the REAL
    llm_subprocess._BACKENDS registry with one handler replaced by a
    recorder that captures kwargs and returns a StepResult(status="ok").
    Infra-patch (registry entry), NOT the invoke_llm_subprocess UUT.
    """
    registry = llm_subprocess._BACKENDS
    recorder_calls: list[dict] = []

    def _recorder(**kwargs):
        recorder_calls.append(kwargs)
        return StepResult(
            status="ok", data=None, duration_ms=0,
            step_name=kwargs.get("step_name", "recorded"),
        )

    patched = dict(registry)
    patched[backend_name] = _recorder
    return patched, recorder_calls


# ─── AC1: _load_tier_model reads model_by_tier[TIER] ───────────────────────

def test_ac1_load_tier_model_reads_simple_sonnet(monkeypatch, tmp_path):
    """AC1: _load_tier_model("SIMPLE") == "sonnet" given
    {"claude":{"model_by_tier":{"SIMPLE":"sonnet"}}}.
    """
    models_json = tmp_path / "models.json"
    models_json.write_text(json.dumps({"claude": {"model_by_tier": {"SIMPLE": "sonnet"}}}))
    monkeypatch.setattr(config_provider, "models_config_path", lambda: models_json)

    fn = getattr(llm_subprocess, "_load_tier_model", None)
    assert callable(fn), (
        "_load_tier_model does not exist in llm_subprocess yet — "
        "GREEN must add it (AC1 FAILS RED)"
    )

    try:
        result = fn("SIMPLE")
    finally:
        config_provider.reset_default_config_provider_factory()

    assert result == "sonnet", f"expected 'sonnet', got {result!r}"


# ─── AC2: robustness — never raises, best-effort None on every bad path ────

def test_ac2_load_tier_model_none_on_none_tier():
    """AC2a: _load_tier_model(None) == None (no lookup attempted)."""
    fn = getattr(llm_subprocess, "_load_tier_model", None)
    assert callable(fn), "_load_tier_model does not exist yet (AC2 FAILS RED)"
    assert fn(None) is None


def test_ac2_load_tier_model_none_on_unknown_tier(monkeypatch, tmp_path):
    """AC2b: unknown tier key (e.g. 'COMPLEX' absent from model_by_tier) -> None."""
    models_json = tmp_path / "models.json"
    models_json.write_text(json.dumps({"claude": {"model_by_tier": {"SIMPLE": "sonnet"}}}))
    monkeypatch.setattr(config_provider, "models_config_path", lambda: models_json)

    fn = getattr(llm_subprocess, "_load_tier_model", None)
    assert callable(fn), "_load_tier_model does not exist yet (AC2 FAILS RED)"
    try:
        result = fn("COMPLEX")
    finally:
        config_provider.reset_default_config_provider_factory()
    assert result is None, f"expected None for unknown tier, got {result!r}"


def test_ac2_load_tier_model_none_on_non_dict_model_by_tier(monkeypatch, tmp_path):
    """AC2c: model_by_tier is not a dict -> None (best-effort)."""
    models_json = tmp_path / "models.json"
    models_json.write_text(json.dumps({"claude": {"model_by_tier": "oops-a-string"}}))
    monkeypatch.setattr(config_provider, "models_config_path", lambda: models_json)

    fn = getattr(llm_subprocess, "_load_tier_model", None)
    assert callable(fn), "_load_tier_model does not exist yet (AC2 FAILS RED)"
    try:
        result = fn("SIMPLE")
    finally:
        config_provider.reset_default_config_provider_factory()
    assert result is None, f"expected None for non-dict model_by_tier, got {result!r}"


def test_ac2_load_tier_model_none_on_empty_or_nonstr_value(monkeypatch, tmp_path):
    """AC2d: value '' or non-str under the tier key -> None."""
    fn = getattr(llm_subprocess, "_load_tier_model", None)
    assert callable(fn), "_load_tier_model does not exist yet (AC2 FAILS RED)"

    models_empty = tmp_path / "models_empty.json"
    models_empty.write_text(json.dumps({"claude": {"model_by_tier": {"SIMPLE": ""}}}))
    monkeypatch.setattr(config_provider, "models_config_path", lambda: models_empty)
    try:
        result_empty = fn("SIMPLE")
    finally:
        config_provider.reset_default_config_provider_factory()
    assert result_empty is None, f"expected None for empty-string value, got {result_empty!r}"

    models_int = tmp_path / "models_int.json"
    models_int.write_text(json.dumps({"claude": {"model_by_tier": {"SIMPLE": 123}}}))
    monkeypatch.setattr(config_provider, "models_config_path", lambda: models_int)
    try:
        result_int = fn("SIMPLE")
    finally:
        config_provider.reset_default_config_provider_factory()
    assert result_int is None, f"expected None for non-str (int) value, got {result_int!r}"


def test_ac2_load_tier_model_none_on_missing_config_file(monkeypatch, tmp_path):
    """AC2e: unreadable/missing config file -> None, no raise."""
    fn = getattr(llm_subprocess, "_load_tier_model", None)
    assert callable(fn), "_load_tier_model does not exist yet (AC2 FAILS RED)"

    missing = tmp_path / "does_not_exist.json"
    monkeypatch.setattr(config_provider, "models_config_path", lambda: missing)
    try:
        result = fn("SIMPLE")
    finally:
        config_provider.reset_default_config_provider_factory()
    assert result is None, f"expected None for missing config file, got {result!r}"


# ─── AC3: tier key is uppercase-normalized ──────────────────────────────────

def test_ac3_load_tier_model_uppercases_tier(monkeypatch, tmp_path):
    """AC3: _load_tier_model("simple") (lowercase input) == "sonnet"
    (config keys stored uppercase, lookup normalizes)."""
    models_json = tmp_path / "models.json"
    models_json.write_text(json.dumps({"claude": {"model_by_tier": {"SIMPLE": "sonnet"}}}))
    monkeypatch.setattr(config_provider, "models_config_path", lambda: models_json)

    fn = getattr(llm_subprocess, "_load_tier_model", None)
    assert callable(fn), "_load_tier_model does not exist yet (AC3 FAILS RED)"
    try:
        result = fn("simple")
    finally:
        config_provider.reset_default_config_provider_factory()
    assert result == "sonnet", f"expected uppercase-normalized lookup to hit, got {result!r}"


# ─── AC4: chokepoint dispatch + resolver-event fires under SIMPLE tier ─────

def test_ac4_invoke_llm_subprocess_dispatches_tier_model_and_emits_event(monkeypatch, tmp_path):
    """AC4: with run_ctx.tier="SIMPLE" + fixture config + hard_gate=False, the
    stubbed backend receives model="sonnet" AND the real event_log gets a
    resolver_invoke_llm_subprocess_tier_model_resolved event with
    source="model_by_tier", value="sonnet", extra.pinned_model="opus",
    extra.tier="SIMPLE".
    """
    models_json = tmp_path / "models.json"
    models_json.write_text(json.dumps({"claude": {"model_by_tier": {"SIMPLE": "sonnet"}}}))
    monkeypatch.setattr(config_provider, "models_config_path", lambda: models_json)

    event_log = _FakeEventLog()
    patched, recorder_calls = _patched_backends_with_recorder("claude-subprocess")

    try:
        _set_run_with_tier(event_log, run_id="r-ac4", step_name="s-ac4", phase="wf", tier="SIMPLE")
        with monkeypatch.context() as m:
            m.setattr(llm_subprocess, "_BACKENDS", patched)
            llm_subprocess.invoke_llm_subprocess(
                prompt="x", model="opus", timeout_sec=5, step_name="s-ac4",
                hard_gate=False, backend="claude-subprocess",
            )
    finally:
        telemetry_ctx.clear_current_run()
        config_provider.reset_default_config_provider_factory()

    assert recorder_calls, "stubbed backend was never called"
    assert recorder_calls[0].get("model") == "sonnet", (
        f"expected backend to receive model='sonnet' (tier override), "
        f"got {recorder_calls[0].get('model')!r}"
    )

    resolved = event_log.of_type("resolver_invoke_llm_subprocess_tier_model_resolved")
    assert resolved, (
        "no resolver_invoke_llm_subprocess_tier_model_resolved event was appended "
        "to the event_log — tier-dispatch chokepoint not implemented (AC4 FAILS RED)"
    )
    _, payload, _ = resolved[0]
    assert payload.get("source") == "model_by_tier", payload
    assert payload.get("value") == "sonnet", payload
    extra = payload.get("extra") or {}
    assert extra.get("pinned_model") == "opus", extra
    assert extra.get("tier") == "SIMPLE", extra
    assert extra.get("tier_model") == "sonnet", (
        f"expected extra.tier_model=='sonnet', got {extra!r} (AC4 FAILS RED)"
    )


# ─── AC5: hard_gate=True is EXEMPT from tier override (PASS pre-GREEN) ─────

def test_ac5_hard_gate_true_exempts_tier_dispatch(monkeypatch, tmp_path):
    """AC5: hard_gate=True + tier="SIMPLE" -> backend still receives
    model="opus" (unchanged), and NO tier-resolved event fires.

    Paired regression guard with AC4/AC7: PASSES pre-GREEN trivially (today's
    invoke_llm_subprocess never touches tier at all), and MUST continue to
    pass post-GREEN (structural hard_gate exemption, not conventional).
    """
    models_json = tmp_path / "models.json"
    models_json.write_text(json.dumps({"claude": {"model_by_tier": {"SIMPLE": "sonnet"}}}))
    monkeypatch.setattr(config_provider, "models_config_path", lambda: models_json)

    event_log = _FakeEventLog()
    patched, recorder_calls = _patched_backends_with_recorder("claude-subprocess")

    try:
        _set_run_with_tier(event_log, run_id="r-ac5", step_name="s-ac5", phase="wf", tier="SIMPLE")
        with monkeypatch.context() as m:
            m.setattr(llm_subprocess, "_BACKENDS", patched)
            llm_subprocess.invoke_llm_subprocess(
                prompt="x", model="opus", timeout_sec=5, step_name="s-ac5",
                hard_gate=True, gate_label="test-ac5", backend="claude-subprocess",
            )
    finally:
        telemetry_ctx.clear_current_run()
        config_provider.reset_default_config_provider_factory()

    assert recorder_calls, "stubbed backend was never called"
    assert recorder_calls[0].get("model") == "opus", (
        f"hard_gate=True must leave model unchanged, got {recorder_calls[0].get('model')!r}"
    )
    resolved = event_log.of_type("resolver_invoke_llm_subprocess_tier_model_resolved")
    assert not resolved, (
        f"hard_gate=True must NOT emit a tier-resolved event, got {resolved!r}"
    )


# ─── AC6: tier=None -> byte-identical legacy path (PASS pre-GREEN) ─────────

def test_ac6_no_tier_no_override_no_event(monkeypatch, tmp_path):
    """AC6: active run ctx with tier=None (default) + fixture config -> backend
    receives model="opus" unchanged, NO tier-resolved event.
    Regression guard paired with AC4: PASSES pre-GREEN (today's behavior) and
    must continue to pass post-GREEN (non-build/legacy invocations untouched).
    """
    models_json = tmp_path / "models.json"
    models_json.write_text(json.dumps({"claude": {"model_by_tier": {"SIMPLE": "sonnet"}}}))
    monkeypatch.setattr(config_provider, "models_config_path", lambda: models_json)

    event_log = _FakeEventLog()
    patched, recorder_calls = _patched_backends_with_recorder("claude-subprocess")

    try:
        telemetry_ctx.set_current_run(
            event_log=event_log, run_id="r-ac6", step_name="s-ac6", phase="wf",
        )
        with monkeypatch.context() as m:
            m.setattr(llm_subprocess, "_BACKENDS", patched)
            llm_subprocess.invoke_llm_subprocess(
                prompt="x", model="opus", timeout_sec=5, step_name="s-ac6",
                hard_gate=False, backend="claude-subprocess",
            )
    finally:
        telemetry_ctx.clear_current_run()
        config_provider.reset_default_config_provider_factory()

    assert recorder_calls, "stubbed backend was never called"
    assert recorder_calls[0].get("model") == "opus", (
        f"tier=None must leave model unchanged, got {recorder_calls[0].get('model')!r}"
    )
    resolved = event_log.of_type("resolver_invoke_llm_subprocess_tier_model_resolved")
    assert not resolved, f"tier=None must NOT emit a tier-resolved event, got {resolved!r}"


# ─── AC7: config WITHOUT model_by_tier -> caller_pinned source event ───────

def test_ac7_missing_model_by_tier_emits_caller_pinned(monkeypatch, tmp_path):
    """AC7: run ctx tier="SIMPLE", config has no model_by_tier key -> backend
    receives model="opus" (pinned) AND a tier-resolved event fires with
    source="caller_pinned", value="opus".
    """
    models_json = tmp_path / "models.json"
    models_json.write_text(json.dumps({"claude": {}}))
    monkeypatch.setattr(config_provider, "models_config_path", lambda: models_json)

    event_log = _FakeEventLog()
    patched, recorder_calls = _patched_backends_with_recorder("claude-subprocess")

    try:
        _set_run_with_tier(event_log, run_id="r-ac7", step_name="s-ac7", phase="wf", tier="SIMPLE")
        with monkeypatch.context() as m:
            m.setattr(llm_subprocess, "_BACKENDS", patched)
            llm_subprocess.invoke_llm_subprocess(
                prompt="x", model="opus", timeout_sec=5, step_name="s-ac7",
                hard_gate=False, backend="claude-subprocess",
            )
    finally:
        telemetry_ctx.clear_current_run()
        config_provider.reset_default_config_provider_factory()

    assert recorder_calls, "stubbed backend was never called"
    assert recorder_calls[0].get("model") == "opus", (
        f"missing model_by_tier must leave model unchanged (pinned), "
        f"got {recorder_calls[0].get('model')!r}"
    )
    resolved = event_log.of_type("resolver_invoke_llm_subprocess_tier_model_resolved")
    assert resolved, (
        "no resolver_invoke_llm_subprocess_tier_model_resolved event fired for the "
        "caller_pinned fallback path (AC7 FAILS RED)"
    )
    _, payload, _ = resolved[0]
    assert payload.get("source") == "caller_pinned", payload
    assert payload.get("value") == "opus", payload


# ─── AC8: set_current_run(tier=...) + _RunCtx back-compat ──────────────────

def test_ac8_set_current_run_tier_kwarg_and_4positional_backcompat():
    """AC8: set_current_run(..., tier="SIMPLE") -> get_current_run().tier ==
    "SIMPLE"; omitting tier -> .tier is None; 4-positional _RunCtx
    construction (no tier) still valid with .tier is None.
    """
    try:
        telemetry_ctx.set_current_run(
            event_log=None, run_id="r1", step_name="s1", tier="SIMPLE",
        )
        run = telemetry_ctx.get_current_run()
        assert run is not None
        assert run.tier == "SIMPLE", (
            "set_current_run does not thread a tier kwarg yet (AC8 FAILS RED)"
        )
        telemetry_ctx.clear_current_run()

        telemetry_ctx.set_current_run(event_log=None, run_id="r2", step_name="s2")
        run2 = telemetry_ctx.get_current_run()
        assert run2 is not None
        assert getattr(run2, "tier", "MISSING_ATTR") is None, (
            "omitting tier must default to None"
        )
        telemetry_ctx.clear_current_run()

        ctx4 = telemetry_ctx._RunCtx("el", "rid", "sn", "ph")
        assert getattr(ctx4, "tier", "MISSING_ATTR") is None, (
            "4-positional _RunCtx construction must remain valid with tier defaulting to None"
        )
    finally:
        telemetry_ctx.clear_current_run()


# ─── AC9: set_current_run_from preserves all prev fields (incl. tier) ──────

def test_ac9_set_current_run_from_preserves_all_fields():
    """AC9: set_current_run_from(prev, step_name="retry_x") re-pushes prev's
    ctx under the new step_name, preserving event_log/run_id/phase/tier.
    Tier preservation is the load-bearing assert (§3 AC9).
    """
    fn = getattr(telemetry_ctx, "set_current_run_from", None)
    assert callable(fn), (
        "set_current_run_from does not exist in telemetry_ctx yet — "
        "GREEN must add it (AC9 FAILS RED)"
    )
    assert inspect.isfunction(fn), f"expected a plain function, got {fn!r}"

    try:
        fake_log = _FakeEventLog()
        telemetry_ctx.set_current_run(
            event_log=fake_log, run_id="rid-ac9", step_name="orig_step", phase="wf-ac9",
        )
        prev = telemetry_ctx.get_current_run()
        assert prev is not None

        fn(prev, step_name="retry_x")
        cur = telemetry_ctx.get_current_run()
        assert cur is not None
        assert cur.step_name == "retry_x", (
            f"expected step_name updated to 'retry_x', got {cur.step_name!r}"
        )
        assert cur.event_log is prev.event_log
        assert cur.run_id == prev.run_id
        assert cur.phase == prev.phase
        assert getattr(cur, "tier", "MISSING_ATTR") == getattr(prev, "tier", "MISSING_ATTR"), (
            "set_current_run_from must preserve tier (the load-bearing GH375 assert)"
        )
    finally:
        telemetry_ctx.clear_current_run()


# ─── AC10: engine threads org_config.complexity into run ctx tier ─────────

def _ok_step_capturing_tier(name: str, sink: dict) -> StepContract:
    def _run(ctx: WorkflowContext, prev):
        run = telemetry_ctx.get_current_run()
        sink["tier"] = (
            getattr(run, "tier", "MISSING_ATTR") if run is not None else "NO_RUN_CTX"
        )
        return StepResult(status="ok", data=None, duration_ms=0, step_name=name)
    return StepContract(name=name, execute=_run)


def _make_ctx(org_config):
    return WorkflowContext(
        tenant_id="t", scope=None, db_path=None, org_config=org_config,
        question="q", session_id="s", persona="p", framework=None, domain=None,
    )


def test_ac10_engine_threads_complexity_into_run_ctx_tier():
    """AC10a: WorkflowContext.org_config={"complexity":"SIMPLE"} -> the step's
    telemetry_ctx.get_current_run().tier == "SIMPLE"."""
    sink: dict = {}
    step = _ok_step_capturing_tier("s1", sink)
    workflow = WorkflowDefinition(name="w_ac10a", steps=[step])
    eng = WorkflowEngine()
    eng.register("w_ac10a", workflow)

    try:
        eng.execute("w_ac10a", _make_ctx({"complexity": "SIMPLE"}), run_id="r-ac10a")
    finally:
        telemetry_ctx.clear_current_run()

    assert sink.get("tier") == "SIMPLE", (
        f"expected engine to thread org_config.complexity='SIMPLE' into run ctx tier, "
        f"got {sink.get('tier')!r} (AC10 FAILS RED)"
    )


def test_ac10_engine_none_org_config_gives_none_tier():
    """AC10b: WorkflowContext.org_config=None -> captured tier is None
    (dispatch stays inert; not a MISSING_ATTR failure once GREEN lands)."""
    sink: dict = {}
    step = _ok_step_capturing_tier("s1", sink)
    workflow = WorkflowDefinition(name="w_ac10b", steps=[step])
    eng = WorkflowEngine()
    eng.register("w_ac10b", workflow)

    try:
        eng.execute("w_ac10b", _make_ctx(None), run_id="r-ac10b")
    finally:
        telemetry_ctx.clear_current_run()

    assert sink.get("tier") is None, (
        f"expected None tier when org_config is None, got {sink.get('tier')!r} (AC10 FAILS RED)"
    )


# ─── AC11: retry re-set sites use the set_current_run_from helper ──────────

_DIRECT_CALL_RE = re.compile(r"telemetry_ctx\.set_current_run\(")


def test_ac11_phase5_and_phase6_retry_sites_use_set_current_run_from():
    """AC11 (revised, grep-class, deterministic): in EACH of
    phase_5_implement.py and phase_6_review.py:
      (a) regex count of 'telemetry_ctx.set_current_run(' (direct call form)
          == 0 -- this regex requires the literal '(' immediately after
          'run', so it structurally CANNOT match 'set_current_run_from(';
          the 4 retry spans were the ONLY set_current_run(...) call-sites in
          these two files (verified pre-GREEN), so a zero count proves full
          migration to the helper and closes the tier-drop hole (spec §2.4).
      (b) count of 'set_current_run_from(' >= 2 (both retry re-set spans per
          file).
    """
    engine_root = Path(__file__).resolve().parents[1]
    phase5 = engine_root / "workflows" / "phase_5_implement.py"
    phase6 = engine_root / "workflows" / "phase_6_review.py"
    assert phase5.is_file(), f"expected {phase5} to exist"
    assert phase6.is_file(), f"expected {phase6} to exist"

    src5 = phase5.read_text(encoding="utf-8")
    src6 = phase6.read_text(encoding="utf-8")

    direct5 = _DIRECT_CALL_RE.findall(src5)
    direct6 = _DIRECT_CALL_RE.findall(src6)
    from_count5 = src5.count("set_current_run_from(")
    from_count6 = src6.count("set_current_run_from(")

    assert len(direct5) == 0, (
        f"phase_5_implement.py still has {len(direct5)} direct "
        "'telemetry_ctx.set_current_run(' call(s) -- retry sites not fully "
        "migrated to set_current_run_from (AC11 FAILS RED)"
    )
    assert len(direct6) == 0, (
        f"phase_6_review.py still has {len(direct6)} direct "
        "'telemetry_ctx.set_current_run(' call(s) -- retry sites not fully "
        "migrated to set_current_run_from (AC11 FAILS RED)"
    )
    assert from_count5 >= 2, (
        f"phase_5_implement.py has {from_count5} 'set_current_run_from(' "
        "occurrences, expected >= 2 (both retry re-set spans, §2.4) "
        "(AC11 FAILS RED)"
    )
    assert from_count6 >= 2, (
        f"phase_6_review.py has {from_count6} 'set_current_run_from(' "
        "occurrences, expected >= 2 (both retry re-set spans, §2.4) "
        "(AC11 FAILS RED)"
    )


# ─── AC13: downgrade-only floor (gate cycle-1 F1) ──────────────────────────

def test_ac13_haiku_pin_not_upgraded(monkeypatch, tmp_path):
    """AC13a: caller pins model="haiku" (already the SIMPLE-tier haiku
    override at some call-sites, e.g. phase_5 RED/GREEN, spec §2.3 gate
    cycle-1 F1) with run ctx tier="SIMPLE" + fixture config SIMPLE->sonnet.
    The tier override must NEVER upgrade a cheaper caller-pinned model:
    backend must still receive model="haiku", and the resolver event must
    fire with source="caller_pinned", value="haiku", extra.tier_model
    =="sonnet" (the tier target was computed but rejected as an upgrade).
    """
    models_json = tmp_path / "models.json"
    models_json.write_text(json.dumps({"claude": {"model_by_tier": {"SIMPLE": "sonnet"}}}))
    monkeypatch.setattr(config_provider, "models_config_path", lambda: models_json)

    event_log = _FakeEventLog()
    patched, recorder_calls = _patched_backends_with_recorder("claude-subprocess")

    try:
        _set_run_with_tier(event_log, run_id="r-ac13", step_name="s-ac13", phase="wf", tier="SIMPLE")
        with monkeypatch.context() as m:
            m.setattr(llm_subprocess, "_BACKENDS", patched)
            llm_subprocess.invoke_llm_subprocess(
                prompt="x", model="haiku", timeout_sec=5, step_name="s-ac13",
                hard_gate=False, backend="claude-subprocess",
            )
    finally:
        telemetry_ctx.clear_current_run()
        config_provider.reset_default_config_provider_factory()

    assert recorder_calls, "stubbed backend was never called"
    assert recorder_calls[0].get("model") == "haiku", (
        "tier override must NOT upgrade a cheaper caller-pinned model, got "
        f"{recorder_calls[0].get('model')!r} (AC13 FAILS RED)"
    )

    resolved = event_log.of_type("resolver_invoke_llm_subprocess_tier_model_resolved")
    assert resolved, (
        "no resolver_invoke_llm_subprocess_tier_model_resolved event fired for "
        "the downgrade-rejected haiku-pin path (AC13 FAILS RED)"
    )
    _, payload, _ = resolved[0]
    assert payload.get("source") == "caller_pinned", payload
    assert payload.get("value") == "haiku", payload
    extra = payload.get("extra") or {}
    assert extra.get("tier_model") == "sonnet", extra


def test_ac13_tier_model_is_downgrade_unit():
    """AC13b: unit coverage of _tier_model_is_downgrade (llm_subprocess),
    deferred-import per D1CF5FDF -- the symbol does not exist pre-GREEN.
    """
    fn = getattr(llm_subprocess, "_tier_model_is_downgrade", None)
    assert callable(fn), (
        "_tier_model_is_downgrade does not exist in llm_subprocess yet -- "
        "GREEN must add it (AC13 FAILS RED)"
    )
    assert fn("sonnet", "opus") is True, "sonnet is a downgrade from opus"
    assert fn("sonnet", "haiku") is False, "sonnet is an upgrade from haiku"
    assert fn("haiku", "sonnet") is True, "haiku is a downgrade from sonnet"
    assert fn("gpt-x", "opus") is False, "unknown tier_model family -> no override"
    assert fn("sonnet", "gpt-x") is False, "unknown pinned_model family -> no override"


# ─── AC12: live HAL config sanity ───────────────────────────────────────────

def test_ac12_hal_models_json_has_model_by_tier_simple_sonnet():
    """AC12: SHARED/config/models.json claude.model_by_tier.SIMPLE == "sonnet".
    pytest.skip if the file is absent (OSS/neutral checkout — orchestrator-
    staged HAL config edit, not GREEN's responsibility per spec §5).
    """
    # bd#97: resolved from tree content. A fixed parents[5] raised IndexError on
    # a shallow clone BEFORE the skip below could fire, so this test errored
    # instead of skipping — the guard was worthless where it mattered most.
    repo_root = resolve_tree_root(Path(__file__).resolve())
    models_json = repo_root / "SHARED" / "config" / "models.json"
    if not models_json.is_file():
        pytest.skip(f"{models_json} not present — neutral/OSS checkout")

    raw = json.loads(models_json.read_text(encoding="utf-8"))
    by_tier = raw.get("claude", {}).get("model_by_tier")
    assert isinstance(by_tier, dict), (
        f"expected claude.model_by_tier to be a dict, got {by_tier!r} (AC12 FAILS RED)"
    )
    assert by_tier.get("SIMPLE") == "sonnet", (
        f"expected claude.model_by_tier.SIMPLE == 'sonnet', got {by_tier.get('SIMPLE')!r} "
        "(AC12 FAILS RED — orchestrator must land the config edit)"
    )
