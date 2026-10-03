"""RED tests for bd#163 -- gate round cap per tier/complexity.

Spec: docs/decisions/2026-10-03-bd163-gate-round-cap.md (5 ACs + design
constraints).

The unit under test is real throughout: ``bytedigger_engine.lib.gate_round_cap``
(new, does not exist yet), ``config_provider`` and the real
``WorkflowEngine._execute_steps`` retry path. Nothing of the UUT is mocked; the
only patch is the LLM subprocess entry point, which must NOT be reached
(design constraint: no LLM call on the cap path).

Harness idiom: sibling tests/test_gh625_restart_budget_split.py (fake 2-step
workflow, duck-typed event log capture). The retry step derives its
``cycle_count`` from the ``cycle`` the engine forwards in ``prev`` so the real
retry loop walks cycle 1, 2, 3 ... until the cap denies.

Singleton state (workflows.md 1i): the config-provider factory and the
BD_GATE_* / HAL_RUNNER_BACKEND env vars are restored by the autouse fixture.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from bytedigger_engine import config_provider
from bytedigger_engine.config_provider import _DefaultConfigProvider
from bytedigger_engine.contracts import (
    StepContract,
    StepResult,
    WorkflowContext,
    WorkflowDefinition,
)
from bytedigger_engine.engine import WorkflowEngine

HAL_TABLE = {"MICRO": 1, "TIER2": 3, "TIER3": 3, "OPTION_D": 3}
ENGINE_PKG = Path(config_provider.__file__).resolve().parent
_ENV_VARS = ("BD_GATE_ROUND_CAPS", "BD_GATE_TIER", "HAL_GATE_ROUND_CAPS", "HAL_GATE_TIER", "HAL_RUNNER_BACKEND")


# --------------------------------------------------------------------------
# Fixtures / helpers
# --------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolated_state(monkeypatch):
    for v in _ENV_VARS:
        monkeypatch.delenv(v, raising=False)
    config_provider.reset_default_config_provider_factory()
    yield
    config_provider.reset_default_config_provider_factory()


@pytest.fixture
def no_llm(monkeypatch):
    """Design constraint: the cap decision never calls an LLM."""
    from bytedigger_engine import llm_subprocess

    calls: list[Any] = []

    def _boom(*a, **k):
        calls.append((a, k))
        raise AssertionError("LLM subprocess must not be invoked by the gate round cap")

    monkeypatch.setattr(llm_subprocess, "invoke_llm_subprocess", _boom)
    return calls


class _FakeEventLog:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict, str]] = []

    def append(self, event_type: str, payload: dict, run_id: str) -> None:
        self.events.append((event_type, payload, run_id))

    def of_type(self, event_type: str) -> list[dict]:
        return [p for et, p, _ in self.events if et == event_type]


def _ctx(tmp_path: Path, **org: Any) -> WorkflowContext:
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={"git_cwd": str(tmp_path), **org},
        question="bd163",
        session_id="test-bd163",
        persona="hal",
        framework=None,
        domain=None,
    )


def _workflow(gate_budget_ok: bool = False) -> WorkflowDefinition:
    """step0 always asks for a retry; its cycle_count is the cycle the engine
    forwarded (prev["cycle"]), so the real loop advances 1, 2, 3 ..."""

    def step0(ctx, prev):
        cc = int(prev.get("cycle", 1)) if isinstance(prev, dict) else 1
        data = {"retry_from_step": 0, "cycle_count": cc}
        if gate_budget_ok:
            data["gate_budget_ok"] = True
        return StepResult(
            status="error",
            data=data,
            duration_ms=0,
            step_name="step0",
            error_code="E_BD163_LEGACY_RETRY",
            recoverable=True,
        )

    def step1(ctx, prev):
        return StepResult(status="ok", data={}, duration_ms=0, step_name="step1")

    return WorkflowDefinition(
        name="bd163_wf",
        steps=[StepContract(name="step0", execute=step0), StepContract(name="step1", execute=step1)],
    )


def _run(tmp_path, *, engine=None, run_id="bd163-run", cycle=1, initial=None, gate_budget_ok=False, **org):
    log = engine._event_log if engine is not None else _FakeEventLog()
    eng = engine or WorkflowEngine(event_log=log)
    # execute() sets these per run (engine.py:300); direct seam must seed them.
    eng._rework_cycle_high = 1
    eng._rework_last_step = None
    result = eng._execute_steps(
        _workflow(gate_budget_ok), _ctx(tmp_path, **org), run_id,
        initial_data=initial, start_step=0, cycle=cycle,
    )
    return eng, log, result


def _retry_cycles(log: _FakeEventLog) -> list[int]:
    return [p["cycle"] for p in log.of_type("phase_retry_triggered")]


# --------------------------------------------------------------------------
# AC1 -- default behaviour unchanged
# --------------------------------------------------------------------------


def test_ac1_default_cap_is_two_legacy_error_code(tmp_path, no_llm):
    from bytedigger_engine.lib.gate_round_cap import DEFAULT_CAP, HARD_MAX, resolve_gate_round_cap

    assert (DEFAULT_CAP, HARD_MAX) == (2, 6)
    for label in (None, "MICRO", "whatever"):
        for table in (None, "", {}):
            cap = resolve_gate_round_cap(label, table)
            assert (cap.cap, cap.source, cap.warnings) == (2, "default", [])

    # Real engine: no table anywhere, with a label present.
    _, log, result = _run(tmp_path, gate_tier="MICRO")
    assert _retry_cycles(log) == [2], "retry at cycle_count 1, stop at 2"
    assert result.error_code == "E_BD163_LEGACY_RETRY"
    assert result.error_code != "E_GATE_ROUND_CAP"
    assert log.of_type("gate_round_cap_exceeded") == [] or all(
        e["source"] == "default" for e in log.of_type("gate_round_cap_exceeded")
    )
    resolved = log.of_type("gate_round_cap_resolved")
    assert resolved and resolved[0]["cap"] == 2 and resolved[0]["source"] == "default"
    assert no_llm == []


# --------------------------------------------------------------------------
# AC2 -- table caps enforced, E_GATE_ROUND_CAP registered
# --------------------------------------------------------------------------


def test_ac2_micro_cap_one_denies_at_cycle_one(tmp_path, no_llm):
    _, log, result = _run(
        tmp_path, gate_tier="MICRO", gate_round_caps={"MICRO": 1, "TIER2": 3}
    )
    assert _retry_cycles(log) == []
    assert result.error_code == "E_GATE_ROUND_CAP"
    assert result.recoverable is False
    assert result.data["gate_round_cap"] == 1
    assert result.data["escalation"] == "host_decision_required"
    ev = log.of_type("gate_round_cap_exceeded")
    assert len(ev) == 1
    assert ev[0]["cap"] == 1 and ev[0]["cycle"] == 1
    assert ev[0]["source"] == "table" and ev[0]["label"] == "MICRO"
    assert ev[0]["exit"] == "host_escalation"
    assert ev[0]["step_name"] == "step0"


def test_ac2_tier2_cap_three_allows_cycle_two_denies_three(tmp_path, no_llm):
    _, log, result = _run(
        tmp_path, gate_tier="TIER2", gate_round_caps={"MICRO": 1, "TIER2": 3}
    )
    assert _retry_cycles(log) == [2, 3]
    assert result.error_code == "E_GATE_ROUND_CAP"
    assert result.recoverable is False
    ev = log.of_type("gate_round_cap_exceeded")
    assert len(ev) == 1 and ev[0]["cycle"] == 3 and ev[0]["cap"] == 3


def test_ac2_complexity_is_label_fallback(tmp_path, no_llm):
    _, log, result = _run(tmp_path, complexity="micro", gate_round_caps={"MICRO": 1})
    assert result.error_code == "E_GATE_ROUND_CAP"
    assert log.of_type("gate_round_cap_resolved")[0]["label"].upper() == "MICRO"


def test_ac2_error_code_registered_everywhere_and_resume_stops(tmp_path):
    from bytedigger_engine.error_codes import ERROR_CODES
    from bytedigger_engine.event_log import EventLog
    from bytedigger_engine.lib.task_resume import _STOP_CODES, plan_resume

    assert "E_GATE_ROUND_CAP" in ERROR_CODES
    assert "E_GATE_ROUND_CAP" in _STOP_CODES
    from bytedigger_engine.error_codes import render_markdown

    rendered = render_markdown()
    for md_path in (ENGINE_PKG / "ERROR_CODES.md", ENGINE_PKG.parent / "ERROR_CODES.md"):
        md = md_path.read_text(encoding="utf-8")
        assert "`E_GATE_ROUND_CAP`" in md, md_path
        assert md == rendered, f"{md_path} not byte-identical to render_markdown()"

    path = tmp_path / "events.jsonl"
    EventLog(path).append(
        "workflow_finished",
        {"workflow_name": "phase_x", "status": "error", "error_code": "E_GATE_ROUND_CAP"},
        "rid-bd163",
    )
    plan = plan_resume(path, "rid-bd163", ["phase_x"])
    assert plan.action == "stop"
    assert plan.error_code == "E_GATE_ROUND_CAP"


# --------------------------------------------------------------------------
# AC3 -- idempotence
# --------------------------------------------------------------------------


def test_ac3_fresh_engine_same_run_id_identical_decision_and_one_warning(tmp_path, no_llm):
    org = dict(gate_tier="MICRO", gate_round_caps={"MICRO": "x"})  # malformed -> warning
    eng1, log1, r1 = _run(tmp_path, run_id="same-rid", **org)
    eng2, log2, r2 = _run(tmp_path, run_id="same-rid", **org)
    assert (r1.error_code, r1.recoverable) == (r2.error_code, r2.recoverable)
    assert _retry_cycles(log1) == _retry_cycles(log2) == [2]
    # one warning per run_id per engine, even though two cap checks ran in eng1
    assert len(log1.of_type("gate_round_cap_table_invalid")) == 1
    assert len(log2.of_type("gate_round_cap_table_invalid")) == 1
    # re-running through the SAME engine and run_id does not re-emit
    _run(tmp_path, engine=eng1, run_id="same-rid", **org)
    assert len(log1.of_type("gate_round_cap_table_invalid")) == 1


def test_ac3_replayed_cycle_count_gives_same_decision(tmp_path, no_llm):
    org = dict(gate_tier="TIER2", gate_round_caps={"TIER2": 3})
    # Full run from cycle 1 ...
    _, log_a, ra = _run(tmp_path, run_id="rid-replay", **org)
    # ... and a replay entering at cycle 3 (the carried cycle_count).
    _, log_b, rb = _run(tmp_path, run_id="rid-replay", cycle=3, initial={"cycle": 3}, **org)
    assert ra.error_code == rb.error_code == "E_GATE_ROUND_CAP"
    assert ra.recoverable is rb.recoverable is False
    assert _retry_cycles(log_b) == []
    assert log_b.of_type("gate_round_cap_exceeded")[0]["cycle"] == 3


# --------------------------------------------------------------------------
# AC4 -- malformed table / provider down: safe default, never raises
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    ["not json", {"MICRO": "x"}, {"MICRO": 0}, {"MICRO": 99}, {"MICRO": True}],
    ids=["notjson", "string", "zero", "over_hard_max", "bool"],
)
def test_ac4_malformed_table_falls_back_to_default(bad, tmp_path, no_llm):
    from bytedigger_engine.lib.gate_round_cap import resolve_gate_round_cap

    cap = resolve_gate_round_cap("MICRO", bad)
    assert cap.cap == 2 and cap.source == "fallback"
    assert len(cap.warnings) == 1
    assert cap.warnings[0]["event"] == "gate_round_cap_table_invalid"
    assert cap.warnings[0].get("reason")

    # Through the real engine: legacy behaviour + exactly one event.
    _, log, result = _run(tmp_path, gate_tier="MICRO", gate_round_caps=bad)
    assert result.error_code == "E_BD163_LEGACY_RETRY"
    assert _retry_cycles(log) == [2]
    assert len(log.of_type("gate_round_cap_table_invalid")) == 1
    assert log.of_type("gate_round_cap_resolved")[0]["source"] == "fallback"


def test_ac4_provider_raising_degrades_to_default_with_warning(tmp_path, no_llm):
    class _Down(_DefaultConfigProvider):
        def gate_round_caps(self):
            raise RuntimeError("provider down")

    config_provider.set_default_config_provider_factory(_Down)
    _, log, result = _run(tmp_path, gate_tier="MICRO")
    assert result.error_code == "E_BD163_LEGACY_RETRY"
    assert _retry_cycles(log) == [2]
    assert len(log.of_type("gate_round_cap_provider_unavailable")) == 1
    assert log.of_type("gate_round_cap_resolved")[0]["source"] == "default"


def test_ac4_provider_without_method_is_no_table_no_warning(tmp_path, no_llm):
    class _Minimal:  # minimal-Protocol provider: no gate_round_caps()
        def gate_enabled(self, env_var):
            return env_var != "HAL_ENGINE_SHADOW_EMITS_OFF"

        def flag(self, env_var):
            return False

        def timeout_ms(self, env_var, default):
            return default

        def binary(self, env_var, default):
            return default

        def hal_root(self):
            return tmp_path

        def path(self, env_var, default):
            return default

    config_provider.set_default_config_provider_factory(_Minimal)
    _, log, result = _run(tmp_path, gate_tier="MICRO")
    assert result.error_code == "E_BD163_LEGACY_RETRY"
    assert log.of_type("gate_round_cap_resolved")[0]["source"] == "default"
    assert log.of_type("gate_round_cap_provider_unavailable") == []
    assert log.of_type("gate_round_cap_table_invalid") == []


def test_ac4_provider_raising_falls_through_to_env_table(tmp_path, monkeypatch, no_llm):
    class _Down(_DefaultConfigProvider):
        def gate_round_caps(self):
            raise RuntimeError("provider down")

    config_provider.set_default_config_provider_factory(_Down)
    monkeypatch.setenv("BD_GATE_ROUND_CAPS", json.dumps({"MICRO": 1}))
    _, log, result = _run(tmp_path, gate_tier="MICRO")
    assert len(log.of_type("gate_round_cap_provider_unavailable")) == 1
    r = log.of_type("gate_round_cap_resolved")[0]
    assert (r["cap"], r["source"]) == (1, "table")
    assert result.error_code == "E_GATE_ROUND_CAP"


def test_malformed_first_source_does_not_fall_through(tmp_path, monkeypatch, no_llm):
    monkeypatch.setenv("BD_GATE_ROUND_CAPS", json.dumps({"MICRO": 1}))
    _, log, result = _run(tmp_path, gate_tier="MICRO", gate_round_caps="not json")
    r = log.of_type("gate_round_cap_resolved")[0]
    assert (r["cap"], r["source"]) == (2, "fallback")
    assert len(log.of_type("gate_round_cap_table_invalid")) == 1
    assert result.error_code == "E_BD163_LEGACY_RETRY"


def test_empty_table_is_valid_default_no_warning(tmp_path, no_llm):
    from bytedigger_engine.lib.gate_round_cap import resolve_gate_round_cap

    cap = resolve_gate_round_cap("MICRO", {})
    assert (cap.cap, cap.source, cap.warnings) == (2, "default", [])
    _, log, _ = _run(tmp_path, gate_tier="MICRO", gate_round_caps={})
    assert log.of_type("gate_round_cap_table_invalid") == []
    assert log.of_type("gate_round_cap_resolved")[0]["source"] == "default"


def test_unmatched_invalid_entry_is_ignored(no_llm):
    from bytedigger_engine.lib.gate_round_cap import resolve_gate_round_cap

    cap = resolve_gate_round_cap("MICRO", {"MICRO": 1, "TIER2": "x", "TIER3": 99})
    assert (cap.cap, cap.source, cap.warnings) == (1, "table", [])
    nolabel = resolve_gate_round_cap(None, {"MICRO": 1})
    assert (nolabel.cap, nolabel.source) == (2, "default")


# --------------------------------------------------------------------------
# gate_budget_ok pin (amendment r1): bounded by HARD_MAX only, not tier cap
# --------------------------------------------------------------------------


def test_gate_budget_ok_retries_bounded_by_hard_max_not_tier_cap(tmp_path, no_llm):
    _, log, result = _run(
        tmp_path, gate_budget_ok=True, gate_tier="MICRO", gate_round_caps={"MICRO": 1}
    )
    # allowed at cycle_count 1 despite cap 1; denied only at cycle_count 6
    assert _retry_cycles(log) == [2, 3, 4, 5, 6]
    assert result.error_code == "E_BD163_LEGACY_RETRY"
    assert log.of_type("gate_round_cap_exceeded") == []


# --------------------------------------------------------------------------
# AC5 -- HAL parity table via all three sources
# --------------------------------------------------------------------------

_EXPECTED = {"MICRO": 1, "TIER2": 3, "TIER3": 3, "OPTION_D": 3}


def test_ac5_hal_table_resolves_via_pure_function_dict_and_json():
    from bytedigger_engine.lib.gate_round_cap import resolve_gate_round_cap

    for raw in (HAL_TABLE, json.dumps(HAL_TABLE)):
        for label, want in _EXPECTED.items():
            cap = resolve_gate_round_cap(label, raw)
            assert (cap.cap, cap.source, cap.warnings) == (want, "table", [])
            assert resolve_gate_round_cap(label.lower(), raw).cap == want


@pytest.mark.parametrize("label", sorted(_EXPECTED))
def test_ac5_hal_table_via_org_config(label, tmp_path, no_llm):
    _, log, _ = _run(tmp_path, gate_tier=label, gate_round_caps=HAL_TABLE)
    r = log.of_type("gate_round_cap_resolved")[0]
    assert (r["cap"], r["source"]) == (_EXPECTED[label], "table")


@pytest.mark.parametrize("label", sorted(_EXPECTED))
def test_ac5_hal_table_via_provider(label, tmp_path, no_llm):
    class _Host(_DefaultConfigProvider):
        def gate_round_caps(self):
            return dict(HAL_TABLE)

    config_provider.set_default_config_provider_factory(_Host)
    _, log, _ = _run(tmp_path, gate_tier=label)
    r = log.of_type("gate_round_cap_resolved")[0]
    assert (r["cap"], r["source"]) == (_EXPECTED[label], "table")


@pytest.mark.parametrize("label", sorted(_EXPECTED))
def test_ac5_hal_table_via_env(label, tmp_path, monkeypatch, no_llm):
    monkeypatch.setenv("BD_GATE_ROUND_CAPS", json.dumps(HAL_TABLE))
    monkeypatch.setenv("BD_GATE_TIER", label)
    assert _DefaultConfigProvider().gate_round_caps() is not None
    _, log, _ = _run(tmp_path)  # label from env BD_GATE_TIER
    r = log.of_type("gate_round_cap_resolved")[0]
    assert (r["cap"], r["source"]) == (_EXPECTED[label], "table")


def test_default_provider_gate_round_caps_none_when_env_unset():
    assert _DefaultConfigProvider().gate_round_caps() is None


def test_org_config_beats_provider_beats_env(tmp_path, monkeypatch, no_llm):
    class _Host(_DefaultConfigProvider):
        def gate_round_caps(self):
            return {"MICRO": 3}

    config_provider.set_default_config_provider_factory(_Host)
    monkeypatch.setenv("BD_GATE_ROUND_CAPS", json.dumps({"MICRO": 4}))
    _, log, _ = _run(tmp_path, gate_tier="MICRO", gate_round_caps={"MICRO": 1})
    assert log.of_type("gate_round_cap_resolved")[0]["cap"] == 1
    _, log2, _ = _run(tmp_path, run_id="r2", gate_tier="MICRO")
    assert log2.of_type("gate_round_cap_resolved")[0]["cap"] == 3


# --------------------------------------------------------------------------
# Design constraints -- no LLM, backend-independent
# --------------------------------------------------------------------------


@pytest.mark.parametrize("backend", ["claude-subprocess", "anthropic-api"])
def test_cap_decision_is_backend_independent_and_llm_free(backend, tmp_path, monkeypatch, no_llm):
    monkeypatch.setenv("HAL_RUNNER_BACKEND", backend)
    _, log, result = _run(
        tmp_path, gate_tier="TIER2", gate_round_caps={"MICRO": 1, "TIER2": 3}
    )
    assert _retry_cycles(log) == [2, 3]
    assert result.error_code == "E_GATE_ROUND_CAP"
    assert log.of_type("gate_round_cap_resolved")[0]["cap"] == 3
    assert no_llm == []
