"""bd#82 PR4 — backend per role; effort as a capability.

Spec: `docs/decisions/2026-09-26-bd82-pr4-role-backend-effort.md`.

Role routing: two spy backends under different names, selected by the role env
vars. Effort: real side effects — the claude-subprocess argv (Popen captured),
the real agent-sdk options turned into CLI argv by the real SDK transport, the
real anthropic-api request body, and events in a real EventLog.
"""
from __future__ import annotations

import io
import json
import subprocess
import types
from unittest.mock import MagicMock, patch

import pytest

from bytedigger_engine import config_provider, llm_subprocess, telemetry_ctx
from bytedigger_engine.contracts import StepResult
from bytedigger_engine.event_log import EventLog
from bytedigger_engine.llm_subprocess import (
    _resolve_backend,
    invoke_llm_subprocess,
    register_backend,
    reset_backends,
)


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(llm_subprocess, "emit_resolver_resolved", lambda *a, **kw: None)
    for var in ("HAL_RUNNER_BACKEND_JUDGE", "HAL_RUNNER_BACKEND_WORKER"):
        monkeypatch.delenv(var, raising=False)
    telemetry_ctx.clear_current_run()
    llm_subprocess._WARNED_EFFORT_NOT_APPLIED.clear()
    yield
    telemetry_ctx.clear_current_run()
    reset_backends()


def _spy(name, capabilities=("tool_allowlist",)):
    calls: list[dict] = []

    def _impl(**kwargs):
        calls.append(kwargs)
        return StepResult(status="ok", data={"raw_response": "ok"}, duration_ms=1,
                          step_name=kwargs["step_name"])

    register_backend(name, _impl, manifest_source="git_diff", capabilities=set(capabilities),
                     overwrite=True)
    return calls


def _invoke(*, hard_gate=False, role=None, backend=None, model="opus", allowed_tools=("Read",)):
    kwargs = {}
    if role is not None:
        kwargs["role"] = role
    if backend is not None:
        kwargs["backend"] = backend
    return invoke_llm_subprocess(
        prompt="p", model=model, timeout_sec=5, step_name="bd82", hard_gate=hard_gate,
        gate_label="g", allowed_tools=list(allowed_tools), idle_timeout_sec=0, **kwargs,
    )


def _event_log(tmp_path):
    log = EventLog(tmp_path / "e.jsonl")
    telemetry_ctx.set_current_run(event_log=log, run_id="run-r", step_name="s", phase="p")
    return log


# ─── roles ────────────────────────────────────────────────────────────────────


@pytest.fixture
def routed(monkeypatch):
    judge = _spy("bd82-judge")
    worker = _spy("bd82-worker")
    monkeypatch.setenv("HAL_RUNNER_BACKEND_JUDGE", "bd82-judge")
    monkeypatch.setenv("HAL_RUNNER_BACKEND_WORKER", "bd82-worker")
    return judge, worker


def test_r1_gate_is_a_judge_and_a_worker_call_is_a_worker(routed):
    judge, worker = routed
    _invoke(hard_gate=True)
    _invoke()
    assert len(judge) == 1 and judge[0]["hard_gate"] is True
    assert len(worker) == 1 and worker[0]["hard_gate"] is False


def test_r2_explicit_judge_role_on_a_non_gate(routed):
    judge, worker = routed
    _invoke(role="judge")
    assert len(judge) == 1 and worker == []


def test_r3_unknown_role_is_refused():
    with pytest.raises(ValueError):
        _invoke(role="reviewer")


def test_r4_resolver_precedence():
    env = {"HAL_RUNNER_BACKEND": "g", "HAL_RUNNER_BACKEND_JUDGE": "j"}
    assert _resolve_backend("k", env, role="judge") == ("k", "kwarg")
    assert _resolve_backend(None, env, role="judge") == ("j", "env-role")
    assert _resolve_backend(None, env, role="worker") == ("g", "env")
    assert _resolve_backend(None, {}, role="worker") == (llm_subprocess._DEFAULT_BACKEND, "default")


def test_r3b_role_validated_even_with_an_explicit_backend():
    _spy("bd82-x")
    with pytest.raises(ValueError):
        _invoke(role="boss", backend="bd82-x")


def test_r4b_blank_role_variable_falls_through():
    env = {"HAL_RUNNER_BACKEND": "g", "HAL_RUNNER_BACKEND_JUDGE": "  "}
    assert _resolve_backend(None, env, role="judge") == ("g", "env")


def test_r5_no_role_keeps_todays_resolution():
    """NEGATIVE LEG: callers without a role (engine adapter identity) are unchanged."""
    env = {"HAL_RUNNER_BACKEND": "g", "HAL_RUNNER_BACKEND_JUDGE": "j"}
    assert _resolve_backend(None, env) == ("g", "env")


def test_r6_resolution_event_carries_the_role(routed, tmp_path):
    log = _event_log(tmp_path)
    _invoke(hard_gate=True)
    resolved = [e["payload"] for e in log.read_all() if e["event_type"] == "runner_backend_resolved"]
    assert resolved and resolved[0]["role"] == "judge" and resolved[0]["source"] == "env-role"


def test_r7_non_gate_judges_declare_the_judge_role():
    """The phase 6 reviewer and the semantic verifier."""
    from bytedigger_engine.lib.plugins.anti_hallucination import semantic_verifier as sv  # noqa: PLC0415
    from bytedigger_engine.workflows import phase_6_review as p6  # noqa: PLC0415

    seen: list[dict] = []

    def _capture(**kwargs):
        seen.append(kwargs)
        return StepResult(status="ok", data={"raw_response": "REFUTED:\nreason: r\nrationale: x\n",
                                             "text": "x"}, duration_ms=1,
                          step_name=kwargs["step_name"])

    ctx = types.SimpleNamespace(org_config={})
    review_prev = StepResult(status="ok", duration_ms=0, step_name="build_review_prompt", data={
        "doc_path": "d", "spec_path": "s", "red_log_path": "r", "green_log_path": "g",
        "prompt": "review",
    })
    with patch.object(p6, "invoke_llm_subprocess", _capture), \
         patch.object(sv.llm_subprocess, "invoke_llm_subprocess", _capture):
        p6._invoke_review_llm(ctx, review_prev)
        sv._invoke_verifier_agent({"file": "f", "line": 1, "quote": "q", "claim": "c"})
    assert [c.get("role") for c in seen] == ["judge", "judge"]


def test_r8_phase6_straggler_check_resolves_the_reviewers_backend(monkeypatch, tmp_path):
    """The reviewer is a judge: with judges in-session the review call carries no
    straggler_cfg (bd#89 P3b1: phase 6 never arms one)."""
    from bytedigger_engine.workflows import phase_6_review as p6  # noqa: PLC0415

    monkeypatch.setenv("HAL_RUNNER_BACKEND_JUDGE", "claude-in-session")
    seen: list[dict] = []

    def _capture(**kwargs):
        seen.append(kwargs)
        return StepResult(status="ok", data={"text": "x"}, duration_ms=1, step_name="s")

    prev = StepResult(status="ok", duration_ms=0, step_name="build_review_prompt", data={
        "doc_path": "d", "spec_path": "s", "red_log_path": "r", "green_log_path": "g",
        "prompt": "review",
    })
    ctx = types.SimpleNamespace(org_config={"scratchpad_dir": str(tmp_path)})
    with patch.object(p6, "invoke_llm_subprocess", _capture):
        p6._invoke_review_llm(ctx, prev)
    assert seen and "straggler_cfg" not in seen[0]


# r8b (subprocess judge keeps straggler_cfg) retired by bd#89 P3b1: phase 6 never arms it.


def test_r1b_gate_with_worker_role_routes_as_a_worker(routed):
    judge, worker = routed
    _invoke(hard_gate=True, role="worker")
    assert judge == [] and len(worker) == 1 and worker[0]["hard_gate"] is True


def test_r9_flags_catalogued():
    from bytedigger_engine.flags_catalog import FLAGS  # noqa: PLC0415

    for name in ("HAL_RUNNER_BACKEND_JUDGE", "HAL_RUNNER_BACKEND_WORKER",
                 "HAL_IN_SESSION_APPLIES_EFFORT"):
        assert name in FLAGS, name


# ─── effort ───────────────────────────────────────────────────────────────────


def _models(monkeypatch, tmp_path, effort):
    path = tmp_path / "models.json"
    path.write_text(json.dumps({"claude": {"effort": effort}}))
    monkeypatch.setattr(config_provider, "models_config_path", lambda: path)


def _subprocess_argv(**invoke_kwargs):
    event = ('{"type":"result","subtype":"success","result":"OK","usage":{"input_tokens":1,'
             '"output_tokens":1},"total_cost_usd":0.0,"duration_ms":1}\n')
    captured: list[list[str]] = []

    def _popen(argv, **kwargs):
        captured.append(list(argv))
        proc = MagicMock()
        proc.pid = 1
        proc.returncode = 0
        proc.stdout = io.StringIO(event)
        proc.stderr = io.StringIO("")
        proc.stdin = MagicMock()
        proc.wait = MagicMock(return_value=0)
        proc.communicate = MagicMock(return_value=(event, ""))
        return proc

    with patch("bytedigger_engine.llm_subprocess.subprocess.Popen", side_effect=_popen):
        res = _invoke(backend="claude-subprocess", **invoke_kwargs)
    assert res.status == "ok", res.error
    return captured[0]


def _flag(argv, name):
    return argv[argv.index(name) + 1] if name in argv else None


def test_e1_capabilities_declared():
    from bytedigger_engine.lib.reference_backends import agent_sdk, anthropic_api  # noqa: PLC0415

    agent_sdk.register()
    anthropic_api.register()
    for name in ("claude-subprocess", "agent-sdk"):
        assert "effort" in llm_subprocess._BACKEND_CAPABILITIES[name], name
    # anthropic-api applies only the levels it has a thinking budget for
    assert {"effort:low", "effort:medium", "effort:high"} <= llm_subprocess._BACKEND_CAPABILITIES["anthropic-api"]
    assert "effort" not in llm_subprocess._BACKEND_CAPABILITIES["anthropic-api"]
    assert "effort" not in llm_subprocess._BACKEND_CAPABILITIES["claude-in-session"]


def test_e2_chokepoint_passes_the_resolved_effort(monkeypatch, tmp_path):
    """Resolved once in the chokepoint: worker from by_phase/global, gate from by_model."""
    _models(monkeypatch, tmp_path, {"by_phase": {"bd82": "low"}, "by_model": {"opus": "high"}})
    calls = _spy("bd82-effort", capabilities=("tool_allowlist", "effort"))
    _invoke(backend="bd82-effort")
    _invoke(backend="bd82-effort", hard_gate=True)
    assert [c["effort"] for c in calls] == ["low", "high"]


def test_e2b_backend_without_the_capability_never_receives_the_kwarg(monkeypatch, tmp_path):
    _models(monkeypatch, tmp_path, "medium")
    calls = _spy("bd82-plain")
    _invoke(backend="bd82-plain")
    assert "effort" not in calls[0]


def test_e3_subprocess_argv_unchanged_by_the_hoist(monkeypatch, tmp_path):
    _models(monkeypatch, tmp_path, "medium")
    assert _flag(_subprocess_argv(), "--effort") == "medium"


def test_e3b_no_effort_configured_is_byte_identical(monkeypatch, tmp_path):
    """NEGATIVE LEG."""
    _models(monkeypatch, tmp_path, None)
    assert "--effort" not in _subprocess_argv()


def _agent_sdk_effort(monkeypatch, tmp_path, *, hard_gate=False):
    """Run the real agent-sdk backend through the chokepoint; return the --effort
    the real SDK transport would put on the CLI argv (None when absent)."""
    import dataclasses  # noqa: PLC0415

    import claude_agent_sdk  # noqa: PLC0415
    from claude_agent_sdk._internal.transport.subprocess_cli import (  # noqa: PLC0415
        SubprocessCLITransport,
    )

    from bytedigger_engine.lib.reference_backends import agent_sdk  # noqa: PLC0415

    seen: list[object] = []

    async def _query(*, prompt, options):
        seen.append(options)
        yield claude_agent_sdk.ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1, is_error=False, num_turns=1,
            session_id="s", result="ok")

    monkeypatch.setattr(claude_agent_sdk, "query", _query)
    agent_sdk.register()
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q",
                    "--allow-empty", "-m", "s"], cwd=repo, check=True)
    monkeypatch.chdir(repo)
    res = _invoke(backend="agent-sdk", hard_gate=hard_gate)
    assert res.status == "ok", res.error
    opts = dataclasses.replace(seen[-1], cli_path="/nonexistent/claude")
    return _flag(SubprocessCLITransport(prompt="p", options=opts)._build_command(), "--effort")


def test_e2c_fallback_dispatch_carries_effort(monkeypatch, tmp_path):
    _models(monkeypatch, tmp_path, "medium")

    def _hung(**kwargs):
        return StepResult(status="error", data={"hang_attempts": 1}, duration_ms=1,
                          step_name=kwargs["step_name"], error="hung",
                          error_code="E_LLM_API_TIMEOUT")

    register_backend("agent-sdk", _hung, manifest_source="harness_tool_record",
                     capabilities={"tool_allowlist", "effort"}, overwrite=True)
    calls: list[dict] = []

    def _target(**kwargs):
        calls.append(kwargs)
        return StepResult(status="ok", data={"raw_response": "ok"}, duration_ms=1,
                          step_name=kwargs["step_name"])

    register_backend("claude-subprocess", _target, manifest_source="harness_tool_record",
                     capabilities={"manifest", "tool_allowlist", "effort"}, overwrite=True)
    res = _invoke(backend="agent-sdk")
    assert res.status == "ok", res.error
    assert calls[0]["effort"] == "medium"


def test_e3c_passed_effort_wins_over_self_resolution(monkeypatch, tmp_path):
    _models(monkeypatch, tmp_path, "medium")
    event = ('{"type":"result","subtype":"success","result":"OK","usage":{"input_tokens":1,'
             '"output_tokens":1},"total_cost_usd":0.0,"duration_ms":1}\n')
    captured: list[list[str]] = []

    def _popen(argv, **kwargs):
        captured.append(list(argv))
        proc = MagicMock()
        proc.pid = 1
        proc.returncode = 0
        proc.stdout = io.StringIO(event)
        proc.stderr = io.StringIO("")
        proc.stdin = MagicMock()
        proc.wait = MagicMock(return_value=0)
        proc.communicate = MagicMock(return_value=(event, ""))
        return proc

    with patch("bytedigger_engine.llm_subprocess.subprocess.Popen", side_effect=_popen):
        llm_subprocess._invoke_subprocess(
            prompt="p", model="opus", timeout_sec=5, step_name="bd82", extra_data=None,
            allowed_tools=None, run_ctx=None, effort="max",
        )
    assert _flag(captured[0], "--effort") == "max"


def test_e3d_effort_follows_the_tier_rebound_model(monkeypatch, tmp_path):
    _models(monkeypatch, tmp_path, {"by_model": {"opus": "high", "sonnet": "medium"}})
    calls = _spy("bd82-effort", capabilities=("tool_allowlist", "effort"))
    telemetry_ctx.set_current_run(event_log=None, run_id="r", step_name="s", phase="p",
                                  tier="SIMPLE")
    monkeypatch.setattr(llm_subprocess, "_load_tier_model", lambda tier: "sonnet")
    _invoke(backend="bd82-effort")
    assert calls[0]["model"] == "sonnet" and calls[0]["effort"] == "medium"


def test_e4c_agent_sdk_gate_uses_the_gate_pin_only(monkeypatch, tmp_path):
    _models(monkeypatch, tmp_path, {"by_phase": {"bd82": "low"}})
    assert _agent_sdk_effort(monkeypatch, tmp_path, hard_gate=True) is None
    _models(monkeypatch, tmp_path, {"by_model": {"opus": "high"}})
    assert _agent_sdk_effort(monkeypatch, tmp_path, hard_gate=True) == "high"


def test_e4d_agent_sdk_nothing_configured_no_effort(monkeypatch, tmp_path):
    """NEGATIVE LEG."""
    _models(monkeypatch, tmp_path, None)
    assert _agent_sdk_effort(monkeypatch, tmp_path) is None


def test_e4_agent_sdk_applies_effort(monkeypatch, tmp_path):
    import claude_agent_sdk  # noqa: PLC0415
    from claude_agent_sdk._internal.transport.subprocess_cli import (  # noqa: PLC0415
        SubprocessCLITransport,
    )

    from bytedigger_engine.lib.reference_backends import agent_sdk  # noqa: PLC0415

    _models(monkeypatch, tmp_path, "high")
    seen: list[object] = []

    async def _query(*, prompt, options):
        seen.append(options)
        yield claude_agent_sdk.ResultMessage(
            subtype="success", duration_ms=1, duration_api_ms=1, is_error=False, num_turns=1,
            session_id="s", result="ok")

    monkeypatch.setattr(claude_agent_sdk, "query", _query)
    agent_sdk.register()
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q",
                    "--allow-empty", "-m", "s"], cwd=repo, check=True)
    monkeypatch.chdir(repo)

    res = _invoke(backend="agent-sdk")

    assert res.status == "ok", res.error
    import dataclasses  # noqa: PLC0415

    opts = dataclasses.replace(seen[0], cli_path="/nonexistent/claude")
    argv = SubprocessCLITransport(prompt="p", options=opts)._build_command()
    assert _flag(argv, "--effort") == "high"


def test_e4b_anthropic_api_applies_a_gate_pin(monkeypatch, tmp_path):
    from bytedigger_engine.lib.reference_backends import anthropic_api  # noqa: PLC0415

    _models(monkeypatch, tmp_path, {"by_model": {"opus": "high"}})
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    anthropic_api.register()
    bodies: list[dict] = []

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _urlopen(req, timeout=None):
        bodies.append(json.loads(req.data))
        return _Resp(json.dumps({"content": [{"type": "text", "text": "ok"}],
                                 "usage": {"input_tokens": 1, "output_tokens": 1},
                                 "model": "claude-opus"}).encode())

    monkeypatch.setattr(anthropic_api.urllib.request, "urlopen", _urlopen)

    _invoke(backend="anthropic-api", hard_gate=True, allowed_tools=())

    assert bodies and bodies[0].get("thinking", {}).get("type") == "enabled"


def test_e5_gate_with_pinned_effort_refused_on_a_backend_that_cannot_apply_it(
        monkeypatch, tmp_path):
    log = _event_log(tmp_path)
    _models(monkeypatch, tmp_path, {"by_model": {"opus": "high"}})
    calls = _spy("bd82-plain")

    res = _invoke(backend="bd82-plain", hard_gate=True)

    assert res.error_code == "E_GATE_EFFORT_UNSUPPORTED"
    assert res.recoverable is False
    assert calls == []
    refused = [e["payload"] for e in log.read_all() if e["event_type"] == "gate_effort_refused"]
    assert refused == [{"backend": "bd82-plain", "step_name": "bd82", "effort": "high"}]


@pytest.mark.parametrize(("capabilities", "model", "code"), [
    ((), "opus", "E_TOOL_RESTRICTION_UNSUPPORTED"),  # tool restriction before effort
    (("tool_allowlist",), "sonnet", "E_HARD_GATE_MODEL_DOWNGRADE"),  # floor first
])
def test_e5b_refusal_order(capabilities, model, code, monkeypatch, tmp_path):
    log = _event_log(tmp_path)
    _models(monkeypatch, tmp_path, {"by_model": {"opus": "high", "sonnet": "high"}})
    calls = _spy("bd82-order", capabilities=capabilities)
    res = _invoke(backend="bd82-order", hard_gate=True, model=model)
    assert res.error_code == code
    assert calls == []
    assert "gate_effort_refused" not in [e["event_type"] for e in log.read_all()]


def test_e6_worker_effort_not_applied_is_visible(monkeypatch, tmp_path, caplog):
    log = _event_log(tmp_path)
    _models(monkeypatch, tmp_path, "medium")
    calls = _spy("bd82-plain")

    with caplog.at_level("WARNING", logger="bytedigger_engine.llm_subprocess"):
        res = _invoke(backend="bd82-plain")

    assert res.status == "ok" and len(calls) == 1
    events = [e["payload"] for e in log.read_all() if e["event_type"] == "effort_not_applied"]
    assert events == [{"backend": "bd82-plain", "step_name": "bd82", "effort": "medium"}]
    assert any("effort_not_applied" in r.getMessage() for r in caplog.records)


def test_e7_nothing_configured_nothing_refused(monkeypatch, tmp_path):
    """NEGATIVE LEG: no effort configured → a gate on a no-effort backend runs."""
    log = _event_log(tmp_path)
    _models(monkeypatch, tmp_path, None)
    calls = _spy("bd82-plain")
    res = _invoke(backend="bd82-plain", hard_gate=True)
    assert res.status == "ok" and len(calls) == 1
    types_ = [e["event_type"] for e in log.read_all()]
    assert "effort_not_applied" not in types_ and "gate_effort_refused" not in types_


@pytest.mark.parametrize("effort", ["medium", {"by_phase": {"bd82": "low"}}], ids=["legacy", "by_phase"])
def test_e7b_non_pin_config_never_refuses_a_gate(effort, monkeypatch, tmp_path):
    _models(monkeypatch, tmp_path, effort)
    calls = _spy("bd82-plain")
    res = _invoke(backend="bd82-plain", hard_gate=True)
    assert res.status == "ok" and len(calls) == 1


def _req_files(tmp_path):
    return sorted((tmp_path / "req").rglob("*.req.json"))


def test_e9_in_session_gate_pin_refused_without_the_declaration(monkeypatch, tmp_path):
    monkeypatch.setenv("HAL_RUNNER_REQUEST_DIR", str(tmp_path / "req"))
    monkeypatch.setenv("HAL_IN_SESSION_ENFORCES_TOOLS", "1")
    _event_log(tmp_path)
    _models(monkeypatch, tmp_path, {"by_model": {"opus": "high"}})
    res = _invoke(backend="claude-in-session", hard_gate=True)
    assert res.error_code == "E_GATE_EFFORT_UNSUPPORTED"
    assert _req_files(tmp_path) == []


def test_e10_in_session_declaration_carries_effort_in_the_request(monkeypatch, tmp_path):
    monkeypatch.setenv("HAL_RUNNER_REQUEST_DIR", str(tmp_path / "req"))
    monkeypatch.setenv("HAL_IN_SESSION_ENFORCES_TOOLS", "1")
    monkeypatch.setenv("HAL_IN_SESSION_APPLIES_EFFORT", "1")
    _event_log(tmp_path)
    _models(monkeypatch, tmp_path, {"by_model": {"opus": "high"}})

    assert "effort" in llm_subprocess._backend_capabilities("claude-in-session")
    invoke_llm_subprocess(
        prompt="p", model="opus", timeout_sec=1, step_name="bd82", hard_gate=True,
        gate_label="g", allowed_tools=["Read"], backend="claude-in-session", idle_timeout_sec=0,
    )
    reqs = _req_files(tmp_path)
    assert len(reqs) == 1
    assert json.loads(reqs[0].read_text())["effort"] == "high"


@pytest.mark.parametrize("value", ["0", "true", ""])
def test_e10b_in_session_effort_declaration_only_exact_one(value, monkeypatch):
    monkeypatch.setenv("HAL_IN_SESSION_APPLIES_EFFORT", value)
    assert "effort" not in llm_subprocess._backend_capabilities("claude-in-session")


def test_e8_error_code_registered():
    from pathlib import Path  # noqa: PLC0415

    from bytedigger_engine import error_codes  # noqa: PLC0415

    assert "E_GATE_EFFORT_UNSUPPORTED" in error_codes.ERROR_CODES
    pkg = Path(error_codes.__file__).parent
    for doc in (pkg / "ERROR_CODES.md", pkg.parent / "ERROR_CODES.md"):
        assert "`E_GATE_EFFORT_UNSUPPORTED`" in doc.read_text(), doc


@pytest.mark.parametrize(("hard_gate", "event"), [(True, "gate_effort_refused"),
                                                (False, "effort_not_applied")])
def test_e11_level_the_backend_cannot_apply_is_decided_before_dispatch(
        hard_gate, event, monkeypatch, tmp_path):
    """anthropic-api has no thinking budget for `max`: the chokepoint refuses the
    gate (no request, no attestation) and records a worker's gap."""
    from bytedigger_engine.lib.reference_backends import anthropic_api  # noqa: PLC0415

    log = _event_log(tmp_path)
    _models(monkeypatch, tmp_path, {"by_model": {"opus": "max"}} if hard_gate else "max")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "k")
    anthropic_api.register()
    posted: list[dict] = []

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _urlopen(req, timeout=None):
        posted.append(json.loads(req.data))
        return _Resp(json.dumps({"content": [{"type": "text", "text": "ok"}],
                                 "usage": {"input_tokens": 1, "output_tokens": 1},
                                 "model": "claude-opus"}).encode())

    monkeypatch.setattr(anthropic_api.urllib.request, "urlopen", _urlopen)

    res = _invoke(backend="anthropic-api", hard_gate=hard_gate, allowed_tools=())

    types_ = [e["event_type"] for e in log.read_all()]
    assert event in types_
    if hard_gate:
        assert res.error_code == "E_GATE_EFFORT_UNSUPPORTED"
        assert posted == [] and "model_invocation_attested" not in types_
    else:
        assert len(posted) == 1 and "thinking" not in posted[0]
