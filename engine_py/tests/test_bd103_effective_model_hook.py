"""bd#103: the hard-gate floor checks the model a backend actually runs.

A backend may declare an `effective_model` hook at registration. The chokepoint
(`_dispatch_backend`) resolves it once, checks the floor on the effective model,
still calls the backend with the requested model, and records the effective model
in the attestation and the pin check. A hook that fails closes a hard gate and
degrades a worker. pydantic-openai declares its deployment override through the
hook, and its adapter-local refusal moves to the chokepoint.

Spec: `docs/decisions/2026-10-02-bd103-effective-model-hook.md`.

Real `EventLog` on disk; backends are registered through `register_backend` with a
call spy (the backend is the boundary, not the unit under test); dispatch goes
through the public `invoke_llm_subprocess`. `reset_backends()` runs in teardown.
Everything new in production is reached inside test bodies, never at import.
"""
from __future__ import annotations

import json
import subprocess
import sys
import types

import pytest

from bytedigger_engine import llm_subprocess, telemetry_ctx
from bytedigger_engine.contracts import StepResult
from bytedigger_engine.event_log import EventLog
from bytedigger_engine.llm_subprocess import (
    invoke_llm_subprocess,
    register_backend,
    reset_backends,
)

_REFUSED = "E_HARD_GATE_MODEL_DOWNGRADE"


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(llm_subprocess, "emit_resolver_resolved", lambda *a, **kw: None)
    # Effort comes from the operator's models config; keep it out of these tests.
    monkeypatch.setattr(llm_subprocess, "_load_effort_gate", lambda *a, **kw: None)
    monkeypatch.setattr(llm_subprocess, "_load_effort", lambda *a, **kw: None)
    monkeypatch.setattr(llm_subprocess, "_resolve_effort",
                        lambda *a, **kw: llm_subprocess._EffortResolution(None, None, None))
    telemetry_ctx.clear_current_run()
    yield
    telemetry_ctx.clear_current_run()
    reset_backends()
    for name in list(sys.modules):
        root = name.split(".")[0]
        if root in ("pydantic_ai", "anthropic") and getattr(sys.modules[name], "__file__", None) is None:
            sys.modules.pop(name, None)  # only the fakes this file installed


def _event_log(tmp_path):
    log = EventLog(tmp_path / "events.jsonl")
    telemetry_ctx.set_current_run(event_log=log, run_id="run-bd103", step_name="s", phase="p")
    return log


def _events(log, event_type):
    return [e["payload"] for e in log.read_all() if e["event_type"] == event_type]


def _types(log):
    return [e["event_type"] for e in log.read_all()]


def _register_spy(name="bd103-be", *, observed_model=None, **register_kwargs):
    calls: list[dict] = []

    def _impl(**kwargs):
        calls.append(kwargs)
        data = {"text": "ok"}
        if observed_model is not None:
            data["observed_model"] = observed_model
        return StepResult(status="ok", data=data, duration_ms=1, step_name=kwargs["step_name"])

    register_backend(name, _impl, manifest_source="git_diff", **register_kwargs)
    return calls


def _invoke(backend="bd103-be", *, hard_gate, model="opus", **kw):
    return invoke_llm_subprocess(
        prompt="p", model=model, timeout_sec=5, step_name="bd103",
        hard_gate=hard_gate, gate_label="g", backend=backend,
        idle_timeout_sec=0, tier_rebind=False, **kw,
    )


def _attested(log):
    events = _events(log, "model_invocation_attested")
    assert len(events) == 1, events
    return events[0]


# --- AC1 -------------------------------------------------------------------


def test_ac1_non_callable_effective_model_is_type_error_and_nothing_registered():
    # A callable hook is accepted (fails today: the kwarg does not exist).
    register_backend("bd103-ok", lambda **kw: None, manifest_source="git_diff",
                     effective_model=lambda m: m)
    assert "bd103-ok" in llm_subprocess._BACKENDS

    with pytest.raises(TypeError, match=r"effective_model.*callable"):
        register_backend("bd103-bad", lambda **kw: None, manifest_source="git_diff",
                         effective_model="sonnet")
    assert "bd103-bad" not in llm_subprocess._BACKENDS
    assert "bd103-bad" not in llm_subprocess._BACKEND_MANIFEST_SOURCE
    assert "bd103-bad" not in llm_subprocess._BACKEND_CAPABILITIES


# --- AC2 -------------------------------------------------------------------


def test_ac2_no_hook_hard_gate_dispatches_requested_model_and_attests_it(tmp_path):
    log = _event_log(tmp_path)
    calls = _register_spy()

    res = _invoke(hard_gate=True, model="opus")

    assert res.status == "ok", res.error
    assert len(calls) == 1 and calls[0]["model"] == "opus"
    assert _attested(log)["model_requested"] == "opus"


# --- AC3 -------------------------------------------------------------------


def test_ac3_hook_below_floor_refuses_hard_gate_before_dispatch(tmp_path):
    log = _event_log(tmp_path)
    calls = _register_spy(effective_model=lambda m: "sonnet")

    res = _invoke(hard_gate=True, model="opus")

    assert res.error_code == _REFUSED
    assert res.recoverable is False
    assert calls == []
    refused = _events(log, "hard_gate_refused")
    assert len(refused) == 1 and refused[0]["observed_model"] == "sonnet"
    assert "model_invocation_attested" not in _types(log)


# --- AC4 -------------------------------------------------------------------


def test_ac4_hook_meeting_floor_dispatches_requested_model_attests_effective(tmp_path):
    log = _event_log(tmp_path)
    calls = _register_spy(effective_model=lambda m: "fable")

    res = _invoke(hard_gate=True, model="opus")

    assert res.status == "ok", res.error
    assert len(calls) == 1 and calls[0]["model"] == "opus"
    assert _attested(log)["model_requested"] == "fable"


# --- AC5 -------------------------------------------------------------------


def test_ac5_worker_dispatched_attests_effective_and_no_pin_mismatch(tmp_path):
    log = _event_log(tmp_path)
    calls = _register_spy(observed_model="haiku", effective_model=lambda m: "haiku")

    res = _invoke(hard_gate=False, model="opus")

    assert res.status == "ok", res.error
    assert res.error_code != "E_MODEL_PIN_MISMATCH"
    assert len(calls) == 1
    assert _attested(log)["model_requested"] == "haiku"
    assert "model_pin_mismatch" not in _types(log)


# --- AC6 / AC7 -------------------------------------------------------------


def _boom(model):
    raise RuntimeError("boom")


def _assert_unresolved_event(log, *, hard_gate, detail_has):
    events = _events(log, "effective_model_unresolved")
    assert len(events) == 1, events
    ev = events[0]
    assert set(ev) == {"backend", "step_name", "hard_gate", "model", "detail"}
    assert ev["backend"] == "bd103-be"
    assert ev["step_name"] == "bd103"
    assert ev["model"] == "opus"
    assert ev["hard_gate"] is hard_gate
    assert detail_has in ev["detail"]


def test_ac6a_hook_raises_hard_gate_fails_closed(tmp_path):
    log = _event_log(tmp_path)
    calls = _register_spy(effective_model=_boom)

    res = _invoke(hard_gate=True, model="opus")

    assert res.error_code == _REFUSED
    assert res.recoverable is False
    assert calls == []
    _assert_unresolved_event(log, hard_gate=True, detail_has="RuntimeError")
    assert "bd103-be" in (res.error or "")
    assert "effective model" in (res.error or "").lower()
    assert "model_invocation_attested" not in _types(log)


def test_ac6b_hook_raises_worker_degrades_to_requested_model(tmp_path):
    log = _event_log(tmp_path)
    calls = _register_spy(effective_model=_boom)

    res = _invoke(hard_gate=False, model="opus")

    assert res.status == "ok", res.error
    assert len(calls) == 1
    _assert_unresolved_event(log, hard_gate=False, detail_has="RuntimeError")
    assert _attested(log)["model_requested"] == "opus"


_BAD_RETURNS = ["", "   ", None, 42]
_BAD_IDS = ["empty", "whitespace", "none", "int"]


@pytest.mark.parametrize("bad", _BAD_RETURNS, ids=_BAD_IDS)
def test_ac7a_hook_bad_return_hard_gate_fails_closed(bad, tmp_path):
    log = _event_log(tmp_path)
    calls = _register_spy(effective_model=lambda m: bad)

    res = _invoke(hard_gate=True, model="opus")

    assert res.error_code == _REFUSED
    assert calls == []
    _assert_unresolved_event(log, hard_gate=True, detail_has="returned")
    assert "model_invocation_attested" not in _types(log)


@pytest.mark.parametrize("bad", _BAD_RETURNS, ids=_BAD_IDS)
def test_ac7b_hook_bad_return_worker_degrades(bad, tmp_path):
    log = _event_log(tmp_path)
    calls = _register_spy(effective_model=lambda m: bad)

    res = _invoke(hard_gate=False, model="opus")

    assert res.status == "ok", res.error
    assert len(calls) == 1
    _assert_unresolved_event(log, hard_gate=False, detail_has="returned")
    assert _attested(log)["model_requested"] == "opus"


# --- AC8 -------------------------------------------------------------------


def test_ac8a_hook_called_exactly_once_with_requested_model(tmp_path):
    _event_log(tmp_path)
    seen: list[str] = []

    def _hook(model):
        seen.append(model)
        return "opus"

    calls = _register_spy(effective_model=_hook)

    res = _invoke(hard_gate=True, model="opus")  # tier_rebind=False

    assert res.status == "ok", res.error
    assert len(calls) == 1
    assert seen == ["opus"]


def test_ac8b_hook_receives_post_rebind_tier_model(tmp_path, monkeypatch):
    """Same fixture shape as AUTHORSHIP_SPEC AC-M3: tier SIMPLE -> haiku."""
    from bytedigger_engine import config_provider  # noqa: PLC0415

    models_json = tmp_path / "models.json"
    models_json.write_text(json.dumps({"claude": {"model_by_tier": {"SIMPLE": "haiku"}}}))
    monkeypatch.setattr(config_provider, "models_config_path", lambda: models_json)
    log = EventLog(tmp_path / "events.jsonl")
    telemetry_ctx.set_current_run(
        event_log=log, run_id="run-bd103", step_name="s", phase="p", tier="SIMPLE",
    )
    seen: list[str] = []

    def _hook(model):
        seen.append(model)
        return model

    calls = _register_spy(effective_model=_hook)

    res = invoke_llm_subprocess(
        prompt="p", model="opus", timeout_sec=5, step_name="bd103",
        hard_gate=False, gate_label="g", backend="bd103-be", idle_timeout_sec=0,
    )

    assert res.status == "ok", res.error
    assert len(calls) == 1
    assert calls[0]["model"] == "haiku", "precondition: tier dispatch must be active"
    assert seen == ["haiku"]


# --- AC9 -------------------------------------------------------------------


def test_ac9a_overwrite_without_hook_clears_earlier_hook(tmp_path):
    _event_log(tmp_path)
    first = _register_spy(effective_model=lambda m: "sonnet")
    assert _invoke(hard_gate=True, model="opus").error_code == _REFUSED
    assert first == []

    second = _register_spy(overwrite=True)  # same name, no hook

    res = _invoke(hard_gate=True, model="opus")
    assert res.status == "ok", res.error
    assert len(second) == 1


def test_ac9b_reset_backends_clears_runtime_hooks(tmp_path):
    _event_log(tmp_path)
    _register_spy(effective_model=lambda m: "sonnet")
    assert _invoke(hard_gate=True, model="opus").error_code == _REFUSED

    reset_backends()
    assert "bd103-be" not in llm_subprocess._BACKEND_EFFECTIVE_MODEL

    calls = _register_spy()  # same name again, no hook, no overwrite needed
    res = _invoke(hard_gate=True, model="opus")
    assert res.status == "ok", res.error
    assert len(calls) == 1


# --- pydantic-openai through the chokepoint --------------------------------


class _RecordingAgent:
    """Fake pydantic_ai.Agent that records construction."""

    constructed: list["_RecordingAgent"] = []

    def __init__(self, model, tools=None, **kwargs):
        self.model = model
        _RecordingAgent.constructed.append(self)

    def tool_plain(self, fn=None, **kwargs):
        return fn if fn is not None else (lambda f: f)

    def run_sync(self, prompt, usage_limits=None, **kwargs):
        usage = types.SimpleNamespace(input_tokens=1, output_tokens=1)
        return types.SimpleNamespace(output="done", data="done", usage=lambda: usage)


def _install_fake_pydantic_openai(monkeypatch):
    _RecordingAgent.constructed = []
    mod = types.ModuleType("pydantic_ai")
    mod.Agent = _RecordingAgent
    mod.UsageLimits = lambda request_limit=None, **kw: types.SimpleNamespace(request_limit=request_limit)
    models = types.ModuleType("pydantic_ai.models")
    models_openai = types.ModuleType("pydantic_ai.models.openai")
    models_openai.OpenAIChatModel = lambda name, provider=None, **kw: types.SimpleNamespace(name=name)
    providers = types.ModuleType("pydantic_ai.providers")
    providers_azure = types.ModuleType("pydantic_ai.providers.azure")
    providers_azure.AzureProvider = lambda **kw: types.SimpleNamespace(**kw)
    for name, m in (
        ("pydantic_ai", mod), ("pydantic_ai.models", models),
        ("pydantic_ai.models.openai", models_openai),
        ("pydantic_ai.providers", providers),
        ("pydantic_ai.providers.azure", providers_azure),
    ):
        monkeypatch.setitem(sys.modules, name, m)
    monkeypatch.setenv("AZURE_OPENAI_KEY", "k")
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://fake.example")


def _repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    for argv in (
        ["git", "init", "-q", "-b", "main"],
        ["git", "config", "user.email", "t@t"],
        ["git", "config", "user.name", "t"],
        ["git", "commit", "--allow-empty", "-qm", "seed"],
    ):
        subprocess.run(argv, cwd=root, check=True, capture_output=True)
    return root


def _dispatch_pydantic(tmp_path):
    return _invoke(
        "pydantic-openai", hard_gate=True, model="opus",
        extra_data={"workspace_root": str(_repo(tmp_path))},
    )


def test_ac10a_deployment_below_floor_refused_at_chokepoint(monkeypatch, tmp_path):
    monkeypatch.setenv("PYDANTIC_BACKEND_DEPLOYMENT", "gpt-4o-mini")
    _install_fake_pydantic_openai(monkeypatch)
    log = _event_log(tmp_path)
    from bytedigger_engine.lib.reference_backends import pydantic_openai  # noqa: PLC0415

    pydantic_openai.register()
    res = _dispatch_pydantic(tmp_path)

    assert res.error_code == _REFUSED
    assert _RecordingAgent.constructed == []
    # The refusal is the chokepoint's: it names the model that would have run.
    refused = _events(log, "hard_gate_refused")
    assert len(refused) == 1 and refused[0]["observed_model"] == "gpt-4o-mini"


def test_ac10b_no_override_gate_proceeds_past_floor(monkeypatch, tmp_path):
    monkeypatch.delenv("PYDANTIC_BACKEND_DEPLOYMENT", raising=False)
    _install_fake_pydantic_openai(monkeypatch)
    log = _event_log(tmp_path)
    from bytedigger_engine.lib.reference_backends import pydantic_openai  # noqa: PLC0415

    pydantic_openai.register()
    assert pydantic_openai._effective_deployment("opus") == "opus"
    res = _dispatch_pydantic(tmp_path)

    assert res.error_code != _REFUSED, res.error
    assert len(_RecordingAgent.constructed) == 1
    assert _attested(log)["model_requested"] == "opus"


def test_ac10c_effective_deployment_reads_env_else_model(monkeypatch):
    from bytedigger_engine.lib.reference_backends import pydantic_openai  # noqa: PLC0415

    monkeypatch.setenv("PYDANTIC_BACKEND_DEPLOYMENT", "my-deploy")
    assert pydantic_openai._effective_deployment("opus") == "my-deploy"
    monkeypatch.delenv("PYDANTIC_BACKEND_DEPLOYMENT")
    assert pydantic_openai._effective_deployment("opus") == "opus"


def test_ac10d_deployment_meeting_floor_passes_the_chokepoint(monkeypatch, tmp_path):
    """The adapter-local check was stricter than the floor (spec s1.4)."""
    monkeypatch.setenv("PYDANTIC_BACKEND_DEPLOYMENT", "fable")
    _install_fake_pydantic_openai(monkeypatch)
    log = _event_log(tmp_path)
    from bytedigger_engine.lib.reference_backends import pydantic_openai  # noqa: PLC0415

    pydantic_openai.register()
    res = _dispatch_pydantic(tmp_path)

    assert res.error_code != _REFUSED, res.error
    assert len(_RecordingAgent.constructed) == 1
    assert _attested(log)["model_requested"] == "fable"


# --- AC12 / AC13 / AC14 ----------------------------------------------------


def test_ac12_remap_emits_effective_model_remapped_with_exact_keys(tmp_path):
    log = _event_log(tmp_path)
    _register_spy(effective_model=lambda m: "fable")

    res = _invoke(hard_gate=True, model="opus")

    assert res.status == "ok", res.error
    events = _events(log, "effective_model_remapped")
    assert len(events) == 1, events
    assert set(events[0]) == {"backend", "step_name", "hard_gate", "model", "effective_model"}
    assert events[0]["model"] == "opus" and events[0]["effective_model"] == "fable"
    assert events[0]["backend"] == "bd103-be" and events[0]["hard_gate"] is True


def test_ac12_remap_event_is_also_written_on_refused_sonnet_gate(tmp_path):
    log = _event_log(tmp_path)
    calls = _register_spy(effective_model=lambda m: "sonnet")

    res = _invoke(hard_gate=True, model="opus")

    assert res.error_code == _REFUSED and calls == []
    events = _events(log, "effective_model_remapped")
    assert len(events) == 1, events
    assert set(events[0]) == {"backend", "step_name", "hard_gate", "model", "effective_model"}
    assert events[0]["model"] == "opus" and events[0]["effective_model"] == "sonnet"


def test_ac12_no_remap_event_for_same_model_hook_or_no_hook(tmp_path):
    log = _event_log(tmp_path)
    _register_spy("bd103-same", effective_model=lambda m: m)
    _register_spy("bd103-none")

    assert _invoke("bd103-same", hard_gate=True, model="opus").status == "ok"
    assert _invoke("bd103-none", hard_gate=True, model="opus").status == "ok"

    assert _events(log, "effective_model_remapped") == []
    # Positive control: the hook path really ran (fails today: no hook support).
    assert "bd103-same" in llm_subprocess._BACKEND_EFFECTIVE_MODEL


def test_ac13_no_run_context_hard_gate_hook_raises_refuses_without_raising():
    telemetry_ctx.clear_current_run()
    calls = _register_spy(effective_model=_boom)

    res = _invoke(hard_gate=True, model="opus")

    assert res.error_code == _REFUSED
    assert calls == []


def test_ac14a_gate_effort_keyed_on_requested_model(tmp_path, monkeypatch):
    _event_log(tmp_path)
    seen: list = []

    def _rec(model, step_name=None, *a, hard_gate=None, **kw):
        seen.append((model, hard_gate))
        return llm_subprocess._EffortResolution(None, None, None)

    monkeypatch.setattr(llm_subprocess, "_resolve_effort", _rec)
    _register_spy(effective_model=lambda m: "fable")

    res = _invoke(hard_gate=True, model="opus")

    assert res.status == "ok", res.error
    assert seen == [("opus", True)]


def test_ac14b_worker_effort_keyed_on_requested_model(tmp_path, monkeypatch):
    _event_log(tmp_path)
    seen: list = []

    def _rec(model, step_name=None, *a, hard_gate=None, **kw):
        seen.append((model, hard_gate))
        return llm_subprocess._EffortResolution(None, None, None)

    monkeypatch.setattr(llm_subprocess, "_resolve_effort", _rec)
    _register_spy(effective_model=lambda m: "haiku")

    res = _invoke(hard_gate=False, model="opus")

    assert res.status == "ok", res.error
    assert seen == [("opus", False)]


# --- AC11 ------------------------------------------------------------------


def test_ac11_direct_backend_call_no_longer_refuses_locally(monkeypatch, tmp_path):
    monkeypatch.setenv("PYDANTIC_BACKEND_DEPLOYMENT", "gpt-4o-mini")
    _install_fake_pydantic_openai(monkeypatch)
    from bytedigger_engine.lib.reference_backends import pydantic_openai  # noqa: PLC0415

    res = pydantic_openai.pydantic_openai_backend(
        prompt="p", model="opus", timeout_sec=30, step_name="bd103",
        extra_data={"workspace_root": str(_repo(tmp_path))}, allowed_tools=["Read"],
        hard_gate=True,
    )

    assert res.error_code != _REFUSED, res.error
    assert len(_RecordingAgent.constructed) == 1
