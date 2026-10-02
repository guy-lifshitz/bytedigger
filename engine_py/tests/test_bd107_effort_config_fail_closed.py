"""bd#107 — an unreadable or malformed effort config refuses a hard gate; an
unresolved family under a gate pin (and any worker-side problem) surfaces as an
event and dispatches at effort None.

Spec: `docs/decisions/2026-10-02-bd107-effort-config-fail-closed.md`.

Real models-config files in tmp_path behind the `config_provider.models_config_path`
seam, a real EventLog, backends registered through `register_backend` with a call
spy (the backend is the boundary, not the unit under test).
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from bytedigger_engine import config_provider, llm_subprocess, telemetry_ctx
from bytedigger_engine.contracts import StepResult
from bytedigger_engine.event_log import EventLog
from bytedigger_engine.lib import llm_provider
from bytedigger_engine.llm_subprocess import (
    invoke_llm_subprocess,
    register_backend,
    reset_backends,
)

CODE = "E_GATE_EFFORT_CONFIG_INVALID"
EVENT = "effort_config_invalid"
ATTESTED = "model_invocation_attested"


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(llm_subprocess, "emit_resolver_resolved", lambda *a, **kw: None)
    for var in ("HAL_RUNNER_BACKEND_JUDGE", "HAL_RUNNER_BACKEND_WORKER", "HAL_RUNNER_BACKEND",
                "HAL_LLM_PROVIDER"):
        monkeypatch.delenv(var, raising=False)
    telemetry_ctx.clear_current_run()
    llm_subprocess._WARNED_EFFORT_NOT_APPLIED.clear()
    getattr(llm_subprocess, "_WARNED_EFFORT_CONFIG_INVALID", set()).clear()
    yield
    telemetry_ctx.clear_current_run()
    reset_backends()
    llm_provider.reset_providers()


def _spy(name, capabilities=("tool_allowlist",), manifest_source="git_diff"):
    calls: list[dict] = []

    def _impl(**kwargs):
        calls.append(kwargs)
        return StepResult(status="ok", data={"raw_response": "ok"}, duration_ms=1,
                          step_name=kwargs["step_name"])

    register_backend(name, _impl, manifest_source=manifest_source,
                     capabilities=set(capabilities), overwrite=True)
    return calls


def _invoke(*, backend, hard_gate=False, model="opus", allowed_tools=("Read",)):
    return invoke_llm_subprocess(
        prompt="p", model=model, timeout_sec=5, step_name="bd107", hard_gate=hard_gate,
        gate_label="g", allowed_tools=list(allowed_tools), idle_timeout_sec=0, backend=backend,
    )


def _event_log(tmp_path):
    log = EventLog(tmp_path / "e.jsonl")
    telemetry_ctx.set_current_run(event_log=log, run_id="run-r", step_name="s", phase="p")
    return log


def _events(log, name):
    return [e["payload"] for e in log.read_all() if e["event_type"] == name]


def _types(log):
    return [e["event_type"] for e in log.read_all()]


def _write(monkeypatch, tmp_path, kind, value=None):
    """Install a models config of the given shape behind the seam; return its path."""
    path = tmp_path / "models.json"
    if kind == "json":
        path.write_text(json.dumps(value))
    elif kind == "text":
        path.write_text(value)
    elif kind == "bytes":
        path.write_bytes(value)
    elif kind == "dir":
        path = tmp_path / "models-dir"
        path.mkdir()
    elif kind == "missing":
        path = tmp_path / "no-such-models.json"
    monkeypatch.setattr(config_provider, "models_config_path", lambda: path)
    return path


# (id, kind, value, expected problem)
_UNREADABLE = [
    ("a_invalid_json", "text", "{not json", "unreadable"),
    ("b_directory", "dir", None, "unreadable"),
    ("c_invalid_utf8", "bytes", b'{"claude": {"effort": "\xff\xfe"}}', "unreadable"),
]
_MALFORMED_COMMON = [
    ("d_top_level_list", "json", [], "malformed"),
    ("e_claude_int", "json", {"claude": 3}, "malformed"),
    ("f_effort_int", "json", {"claude": {"effort": 5}}, "malformed"),
    ("g_by_model_str", "json", {"claude": {"effort": {"by_model": "opus"}}}, "malformed"),
]
_MALFORMED_GATE_ONLY = [
    ("h_pin_int", "json", {"claude": {"effort": {"by_model": {"opus": 7}}}}, "malformed"),
    ("i_pin_empty", "json", {"claude": {"effort": {"by_model": {"opus": ""}}}}, "malformed"),
]
_GATE_CASES = _UNREADABLE + _MALFORMED_COMMON + _MALFORMED_GATE_ONLY
_WORKER_CASES = _GATE_CASES  # h/i: a worker with no by_phase entry consults by_model[family]


def _ids(cases):
    return [c[0] for c in cases]


# ─── AC1 ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("kind", "value", "problem"), [c[1:] for c in _GATE_CASES],
                         ids=_ids(_GATE_CASES))
def test_ac1_gate_refused_on_an_unusable_effort_config(kind, value, problem, monkeypatch,
                                                       tmp_path):
    log = _event_log(tmp_path)
    _write(monkeypatch, tmp_path, kind, value)
    calls = _spy("bd107-eff", capabilities=("tool_allowlist", "effort"))

    res = _invoke(backend="bd107-eff", hard_gate=True)

    assert res.error_code == CODE
    assert res.recoverable is False
    assert calls == []
    events = _events(log, EVENT)
    assert len(events) == 1
    assert events[0]["problem"] == problem
    assert events[0]["hard_gate"] is True
    assert events[0]["backend"] == "bd107-eff" and events[0]["step_name"] == "bd107"
    assert set(events[0]) == {"backend", "step_name", "hard_gate", "problem", "detail"}
    assert ATTESTED not in _types(log)


# ─── AC2 ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("pin", [{"opus": "high"}, {"haiku": "low"}], ids=["opus_pin", "haiku_pin"])
def test_ac2_gate_family_unresolved_signals_and_still_dispatches(pin, monkeypatch, tmp_path):
    """A provider whose argv carries no --model (non-claude_p command: floor passes) and
    whose model_family recognizes nothing: the by_model pin cannot be matched. Signal
    only, never refuse: the gate runs at default effort."""
    log = _event_log(tmp_path)
    _write(monkeypatch, tmp_path, "json", {"claude": {"effort": {"by_model": pin}}})
    stub = llm_provider.ProviderSpec(
        name="bd107-stub",
        build_argv=lambda model: ["bd107-stub-cli"],
        stream_flags=(),
        parse_result=lambda events: None,
        model_rank={"opus": 2},
        model_family=lambda model: None,
        default_gate_floor="opus",
    )
    llm_provider.register_provider(stub)
    monkeypatch.setenv("HAL_LLM_PROVIDER", "bd107-stub")
    capable = _spy("bd107-eff", capabilities=("tool_allowlist", "effort"))
    plain = _spy("bd107-plain")

    res = _invoke(backend="bd107-eff", hard_gate=True, model="mystery-model")
    res_plain = _invoke(backend="bd107-plain", hard_gate=True, model="mystery-model")

    assert res.status == "ok" and res_plain.status == "ok"
    assert len(capable) == 1 and capable[0]["effort"] is None
    assert len(plain) == 1 and "effort" not in plain[0]
    events = _events(log, EVENT)
    assert len(events) == 2
    assert all(e["problem"] == "family_unresolved" and e["hard_gate"] is True for e in events)


# ─── AC2b ─────────────────────────────────────────────────────────────────────


def test_ac2b_i_gate_ignores_a_malformed_by_phase_it_never_reads(monkeypatch, tmp_path):
    log = _event_log(tmp_path)
    _write(monkeypatch, tmp_path, "json", {"claude": {"effort": {
        "by_phase": "x", "by_model": {"opus": "high"}}}})
    calls = _spy("bd107-eff", capabilities=("tool_allowlist", "effort"))

    res = _invoke(backend="bd107-eff", hard_gate=True)

    assert res.status == "ok" and len(calls) == 1 and calls[0]["effort"] == "high"
    assert EVENT not in _types(log)


def test_ac2b_ii_worker_ignores_a_malformed_by_model_when_by_phase_resolves(monkeypatch,
                                                                          tmp_path):
    log = _event_log(tmp_path)
    _write(monkeypatch, tmp_path, "json", {"claude": {"effort": {
        "by_phase": {"bd107": "low"}, "by_model": "x"}}})
    calls = _spy("bd107-eff", capabilities=("tool_allowlist", "effort"))

    res = _invoke(backend="bd107-eff")

    assert res.status == "ok" and len(calls) == 1 and calls[0]["effort"] == "low"
    assert EVENT not in _types(log)


def test_ac2b_iii_worker_with_a_malformed_by_phase_degrades_and_signals(monkeypatch, tmp_path):
    log = _event_log(tmp_path)
    _write(monkeypatch, tmp_path, "json", {"claude": {"effort": {"by_phase": "x"}}})
    calls = _spy("bd107-eff", capabilities=("tool_allowlist", "effort"))

    res = _invoke(backend="bd107-eff")

    assert res.status == "ok" and len(calls) == 1 and calls[0]["effort"] is None
    events = _events(log, EVENT)
    assert len(events) == 1
    assert events[0]["problem"] == "malformed" and events[0]["hard_gate"] is False


# ─── AC3 ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("kind", "value", "problem"), [c[1:] for c in _WORKER_CASES],
                         ids=_ids(_WORKER_CASES))
def test_ac3_worker_degrades_to_default_effort_and_signals(kind, value, problem, monkeypatch,
                                                           tmp_path):
    log = _event_log(tmp_path)
    _write(monkeypatch, tmp_path, kind, value)
    capable = _spy("bd107-eff", capabilities=("tool_allowlist", "effort"))
    plain = _spy("bd107-plain")

    res_capable = _invoke(backend="bd107-eff")
    res_plain = _invoke(backend="bd107-plain")

    assert res_capable.status == "ok" and res_plain.status == "ok"
    assert len(capable) == 1 and capable[0]["effort"] is None
    assert len(plain) == 1 and "effort" not in plain[0]
    events = _events(log, EVENT)
    assert len(events) == 2
    assert all(e["hard_gate"] is False and e["problem"] == problem for e in events)
    assert {e["backend"] for e in events} == {"bd107-eff", "bd107-plain"}


# ─── AC4 ──────────────────────────────────────────────────────────────────────


def test_ac4_worker_on_an_unrecognized_family_stays_silent(monkeypatch, tmp_path):
    log = _event_log(tmp_path)
    _write(monkeypatch, tmp_path, "json", {"claude": {"effort": {"by_model": {"opus": "high"}}}})
    calls = _spy("bd107-eff", capabilities=("tool_allowlist", "effort"))

    res = _invoke(backend="bd107-eff", model="some-other-provider-model")

    assert res.status == "ok" and len(calls) == 1
    assert calls[0]["effort"] is None
    assert EVENT not in _types(log)


# ─── AC5 ──────────────────────────────────────────────────────────────────────

_NOT_CONFIGURED = [
    ("missing_file", "missing", None),
    ("empty_object", "json", {}),
    ("claude_empty", "json", {"claude": {}}),
    ("effort_null", "json", {"claude": {"effort": None}}),
    ("effort_empty_str", "json", {"claude": {"effort": ""}}),
]


@pytest.mark.parametrize(("kind", "value"), [c[1:] for c in _NOT_CONFIGURED],
                         ids=_ids(_NOT_CONFIGURED))
@pytest.mark.parametrize("hard_gate", [True, False], ids=["gate", "worker"])
def test_ac5_not_configured_never_refuses_or_signals(kind, value, hard_gate, monkeypatch,
                                                     tmp_path):
    log = _event_log(tmp_path)
    _write(monkeypatch, tmp_path, kind, value)
    calls = _spy("bd107-plain")

    res = _invoke(backend="bd107-plain", hard_gate=hard_gate)

    assert res.status == "ok" and len(calls) == 1
    assert EVENT not in _types(log)


# ─── AC6 ──────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(("kind", "value", "problem"), [c[1:] for c in _GATE_CASES],
                         ids=_ids(_GATE_CASES))
def test_ac6_loaders_return_none_and_do_not_raise_on_a_broken_config(kind, value, problem,
                                                                      monkeypatch, tmp_path):
    _write(monkeypatch, tmp_path, kind, value)
    assert llm_subprocess._load_effort("opus", "bd107") is None
    assert llm_subprocess._load_effort_gate("opus") is None


def test_ac6_loaders_unchanged_on_a_valid_config(monkeypatch, tmp_path):
    _write(monkeypatch, tmp_path, "json", {"claude": {"effort": {
        "by_phase": {"bd107": "low"}, "by_model": {"opus": "high"}}}})
    assert llm_subprocess._load_effort("opus", "bd107") == "low"
    assert llm_subprocess._load_effort("opus", "other") == "high"
    assert llm_subprocess._load_effort("opus", None) == "high"
    assert llm_subprocess._load_effort_gate("opus") == "high"
    assert llm_subprocess._load_effort_gate("sonnet") is None

    _write(monkeypatch, tmp_path, "json", {"claude": {"effort": "medium"}})
    assert llm_subprocess._load_effort("opus", "bd107") == "medium"
    assert llm_subprocess._load_effort_gate("opus") is None


# ─── AC7 ──────────────────────────────────────────────────────────────────────

_AC7_CONFIGS = [_GATE_CASES[0], _GATE_CASES[-2]]  # (a) invalid JSON, (h) non-str pin
_AC7_BACKENDS = [
    ("subscription", ("tool_allowlist", "effort"), "harness_tool_record"),
    ("api", ("tool_allowlist", "effort"), "git_diff"),
    ("no_effort_capability", ("tool_allowlist",), "git_diff"),
]


@pytest.mark.parametrize(("kind", "value", "problem"), [c[1:] for c in _AC7_CONFIGS],
                         ids=_ids(_AC7_CONFIGS))
@pytest.mark.parametrize(("style", "capabilities", "source"), _AC7_BACKENDS,
                         ids=[b[0] for b in _AC7_BACKENDS])
def test_ac7_config_refusal_holds_for_every_backend_style(style, capabilities, source, kind,
                                                          value, problem, monkeypatch,
                                                          tmp_path):
    log = _event_log(tmp_path)
    _write(monkeypatch, tmp_path, kind, value)
    calls = _spy(f"bd107-{style}", capabilities=capabilities, manifest_source=source)

    res = _invoke(backend=f"bd107-{style}", hard_gate=True)

    assert res.error_code == CODE  # not E_GATE_EFFORT_UNSUPPORTED: config refusal first
    assert calls == []
    assert [e["problem"] for e in _events(log, EVENT)] == [problem]
    assert "gate_effort_refused" not in _types(log)
    assert ATTESTED not in _types(log)


# ─── AC8 ──────────────────────────────────────────────────────────────────────


def test_ac8_error_code_registered_and_documented():
    from bytedigger_engine import error_codes  # noqa: PLC0415

    assert CODE in error_codes.ERROR_CODES
    pkg = Path(error_codes.__file__).parent
    for doc in (pkg / "ERROR_CODES.md", pkg.parent / "ERROR_CODES.md"):
        assert f"`{CODE}`" in doc.read_text(), doc
    unregistered, _dead = error_codes.check(Path(__file__).resolve().parent.parent)
    assert CODE not in unregistered


# ─── AC9 ──────────────────────────────────────────────────────────────────────


def test_ac9_i_one_warning_per_problem_and_detail(monkeypatch, tmp_path, caplog):
    log = _event_log(tmp_path)
    _write(monkeypatch, tmp_path, "text", "{not json")
    _spy("bd107-eff", capabilities=("tool_allowlist", "effort"))

    with caplog.at_level("WARNING", logger="bytedigger_engine.llm_subprocess"):
        _invoke(backend="bd107-eff", hard_gate=True)
        _invoke(backend="bd107-eff", hard_gate=True)

    assert len(_events(log, EVENT)) == 2
    assert len([r for r in caplog.records if EVENT in r.getMessage()]) == 1


def test_ac9_ii_refusal_message_names_the_problem_and_the_config_path(monkeypatch, tmp_path):
    _event_log(tmp_path)
    path = _write(monkeypatch, tmp_path, "text", "{not json")
    _spy("bd107-eff", capabilities=("tool_allowlist", "effort"))

    res = _invoke(backend="bd107-eff", hard_gate=True)

    assert res.error_code == CODE
    assert "unreadable" in res.error and str(path) in res.error


def test_ac9_iii_tool_restriction_precedes_the_effort_config_refusal(monkeypatch, tmp_path):
    log = _event_log(tmp_path)
    _write(monkeypatch, tmp_path, "text", "{not json")
    calls = _spy("bd107-notools", capabilities=())

    res = _invoke(backend="bd107-notools", hard_gate=True)

    assert res.error_code == "E_TOOL_RESTRICTION_UNSUPPORTED"
    assert calls == []
    assert EVENT not in _types(log)
