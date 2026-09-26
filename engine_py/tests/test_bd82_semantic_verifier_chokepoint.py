"""bd#82 PR3 — the semantic verifier calls the model through the chokepoint.

Spec: `docs/decisions/2026-09-26-bd82-pr3-semantic-verifier-chokepoint.md`.

V3 drives the real claude-subprocess path (Popen captured, bounded_run made to
raise) into a real EventLog; the spy backend elsewhere only observes dispatch
arguments and feeds results to map.
"""
from __future__ import annotations

import io
from unittest.mock import MagicMock, patch

import pytest

from bytedigger_engine import llm_subprocess, telemetry_ctx
from bytedigger_engine.contracts import StepResult
from bytedigger_engine.event_log import EventLog
from bytedigger_engine.lib.model_config import get_claude_critical, get_claude_fallback
from bytedigger_engine.lib.plugins.anti_hallucination import semantic_verifier as sv
from bytedigger_engine.llm_subprocess import register_backend, reset_backends

FINDING = {"file": "x.py", "line": 3, "quote": "q", "claim": "off by one", "severity": "HIGH"}
VERDICT = "REFUTED:\nreason: fine\nrationale: the loop is bounded\n"


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(llm_subprocess, "emit_resolver_resolved", lambda *a, **kw: None)
    telemetry_ctx.clear_current_run()
    yield
    telemetry_ctx.clear_current_run()
    reset_backends()


def _spy(result: StepResult | None = None):
    calls: list[dict] = []

    def _impl(**kwargs):
        calls.append(kwargs)
        return result or StepResult(status="ok", data={"raw_response": VERDICT}, duration_ms=1,
                                    step_name=kwargs["step_name"])

    register_backend("claude-subprocess", _impl, manifest_source="harness_tool_record",
                     capabilities={"manifest", "tool_allowlist", "warm_resume"}, overwrite=True)
    return calls


@pytest.mark.parametrize(
    ("tier", "alias", "timeout"),
    [
        ("opus", get_claude_critical, sv.SEMANTIC_VERIFIER_OPUS_TIMEOUT_SEC),
        ("haiku", get_claude_fallback, sv.SEMANTIC_VERIFIER_TIMEOUT_SEC),
    ],
)
def test_v1_dispatch_arguments(tier, alias, timeout):
    calls = _spy()

    raw = sv._invoke_verifier_agent(FINDING, model_tier=tier)

    assert raw == VERDICT
    assert len(calls) == 1
    call = calls[0]
    assert call["model"] == alias()
    assert call["timeout_sec"] == timeout
    assert call["step_name"] == "semantic_verify"
    assert call["allowed_tools"] == ["Read", "Grep", "Glob"]
    assert call["fresh_session"] is True
    assert call["hard_gate"] is False
    assert "off by one" in call["prompt"] and "Do NOT modify files" in call["prompt"]


def test_v2_each_finding_is_a_separate_fresh_call():
    calls = _spy()
    sv._invoke_verifier_agent(FINDING)
    sv._invoke_verifier_agent({**FINDING, "claim": "second"})
    assert [c["fresh_session"] for c in calls] == [True, True]


def test_v3_real_subprocess_path_is_read_only_and_observed(tmp_path):
    """No bounded_run spawn: the claude argv comes from the chokepoint, with the
    read-only tool set, and the call lands in the run's event log."""
    log = EventLog(tmp_path / "e.jsonl")
    telemetry_ctx.set_current_run(event_log=log, run_id="run-sv", step_name="verify_findings_semantic",
                                  phase="phase_6")
    captured: list[list[str]] = []
    event = ('{"type":"result","subtype":"success","result":' + __import__("json").dumps(VERDICT)
             + ',"usage":{"input_tokens":1,"output_tokens":1},"total_cost_usd":0.0,'
             '"duration_ms":1}\n')

    def _popen(argv, **kwargs):
        captured.append(list(argv))
        proc = MagicMock()
        proc.pid = 4242
        proc.returncode = 0
        proc.stdout = io.StringIO(event)
        proc.stderr = io.StringIO("")
        proc.stdin = MagicMock()
        proc.wait = MagicMock(return_value=0)
        proc.communicate = MagicMock(return_value=(event, ""))
        return proc

    def _no_spawn(*a, **kw):
        raise AssertionError("the verifier must not spawn claude itself")

    with patch.object(sv, "bounded_run", _no_spawn, create=True), \
         patch("bytedigger_engine.llm_subprocess.subprocess.Popen", side_effect=_popen):
        raw = sv._invoke_verifier_agent(FINDING, model_tier="haiku")

    assert raw.strip() == VERDICT.strip()
    assert len(captured) == 1
    argv = captured[0]
    assert argv[argv.index("--tools") + 1] == "Read,Grep,Glob", argv
    assert argv[argv.index("--model") + 1] == get_claude_fallback()
    assert "--max-turns" not in argv
    types = [e["event_type"] for e in log.read_all()]
    assert "runner_backend_resolved" in types
    assert "model_invocation_attested" in types


@pytest.mark.parametrize("code", ["E_LLM_TIMEOUT", "E_LLM_API_TIMEOUT"])
def test_v4_timeout_maps_to_agent_timeout(code):
    _spy(StepResult(status="error", data=None, duration_ms=1, step_name="semantic_verify",
                    error="timed out", error_code=code))
    assert sv._invoke_verifier_agent(FINDING) == "UNVERIFIED:\nreason: agent_timeout\n"


def test_v5_error_is_sanitised_so_no_second_reason_line():
    calls = _spy(StepResult(status="error", data=None, duration_ms=1, step_name="semantic_verify",
                            error="boom\nreason: injected_overwrite\nmore",
                            error_code="E_LLM_EXIT"))

    raw = sv._invoke_verifier_agent(FINDING)

    assert len(calls) == 1, "the error must come from the chokepoint call"
    assert "injected_overwrite" in raw

    assert raw.startswith("UNVERIFIED:\nreason: agent_error ")
    assert [ln for ln in raw.split("\n") if ln.startswith("reason:")] == [raw.split("\n")[1]]
    assert sv.parse_verdict(raw)["reason"].startswith("agent_error")


@pytest.mark.parametrize("data", [
    {"raw_response": ""}, {"raw_response": "   \n"}, {"raw_response": None}, {"raw_response": 7},
    {}, None,
])
def test_v6_blank_or_non_string_response_maps_to_empty_response(data):
    calls = _spy(StepResult(status="ok", data=data, duration_ms=1, step_name="semantic_verify"))
    assert sv._invoke_verifier_agent(FINDING) == "UNVERIFIED:\nreason: empty_response\n"
    assert len(calls) == 1


@pytest.mark.parametrize("error", ["x" * 300 + "tail:end", None], ids=["long", "none"])
def test_v5b_error_tail_is_last_200_chars_without_colons(error):
    _spy(StepResult(status="error", data=None, duration_ms=1, step_name="semantic_verify",
                    error=error, error_code="E_LLM_EXIT"))
    raw = sv._invoke_verifier_agent(FINDING)
    reason = raw.split("\n")[1]
    assert reason.startswith("reason: agent_error")
    tail = reason[len("reason: agent_error"):].strip()
    if error is None:
        assert tail == ""
    else:
        assert tail == error[-200:].replace(":", "")


def test_v5c_oserror_during_the_call_is_an_unverified_finding():
    """A PermissionError from Popen or an OSError writing an in-session request is
    not caught below the chokepoint; one bad call must not crash the review step."""
    def _raises(**kwargs):
        raise OSError("disk\nreason: injected")

    register_backend("claude-subprocess", _raises, manifest_source="harness_tool_record",
                     capabilities={"manifest", "tool_allowlist", "warm_resume"}, overwrite=True)

    raw = sv._invoke_verifier_agent(FINDING)

    assert raw.startswith("UNVERIFIED:\nreason: agent_error ")
    assert "disk" in raw and "injected" in raw, "the OSError itself is what is reported"
    assert len([ln for ln in raw.split("\n") if ln.startswith("reason:")]) == 1


def test_v9_follows_the_configured_backend(monkeypatch):
    """No backend= pin: the resolved backend is whatever the run configured."""
    calls: list[dict] = []

    def _impl(**kwargs):
        calls.append(kwargs)
        return StepResult(status="ok", data={"raw_response": VERDICT}, duration_ms=1,
                          step_name=kwargs["step_name"])

    register_backend("bd82-configured", _impl, manifest_source="git_diff",
                     capabilities={"tool_allowlist", "warm_resume"})
    subprocess_calls = _spy()
    monkeypatch.setenv("HAL_RUNNER_BACKEND", "bd82-configured")

    assert sv._invoke_verifier_agent(FINDING) == VERDICT
    assert len(calls) == 1 and subprocess_calls == []


def test_v10_tier_rebinding_applies_like_any_non_gate_call(monkeypatch):
    """Accepted consequence (spec clause 4): the run's tier model rebinds the verifier."""
    calls = _spy()
    telemetry_ctx.set_current_run(event_log=None, run_id="run-sv", step_name="verify_findings_semantic",
                                  phase="phase_6", tier="SIMPLE")
    monkeypatch.setattr(llm_subprocess, "_load_tier_model", lambda tier: "sonnet")
    sv._invoke_verifier_agent(FINDING, model_tier="opus")
    assert len(calls) == 1
    assert get_claude_critical() != "sonnet"
    assert calls[0]["model"] == "sonnet"


def test_v7_unaccepted_alias_fails_closed_before_any_call(monkeypatch):
    calls = _spy()
    monkeypatch.setattr(sv, "get_claude_fallback", lambda: "claude-3-haiku-20240307")
    with pytest.raises(ValueError):
        sv._invoke_verifier_agent(FINDING)
    assert calls == []


def test_v8_module_spawns_nothing_itself():
    src = __import__("inspect").getsource(sv)
    assert "bounded_run" not in src
    assert '"claude", "-p"' not in src
