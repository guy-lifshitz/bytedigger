"""Correctness guards for bd#83 (spec docs/decisions/2026-10-10-bd83-openai-compat-provider.md).

These pass before and after the change: they pin behaviour the change must not
disturb (cost payload shape for backends without wall time, invalid wall_ms values
never forwarded, the unchanged review prompt for tool-capable judges).
"""
from __future__ import annotations

import pytest

from bytedigger_engine import llm_subprocess, telemetry_ctx
from bytedigger_engine.contracts import StepResult, WorkflowContext
from bytedigger_engine.event_log import EventLog
from bytedigger_engine.llm_subprocess import invoke_llm_subprocess, register_backend, reset_backends
from bytedigger_engine.workflows import phase_6_review


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    monkeypatch.setattr(llm_subprocess, "emit_resolver_resolved", lambda *a, **kw: None)
    for var in ("HAL_RUNNER_BACKEND_JUDGE", "HAL_RUNNER_BACKEND_WORKER"):
        monkeypatch.delenv(var, raising=False)
    telemetry_ctx.clear_current_run()
    yield
    telemetry_ctx.clear_current_run()
    reset_backends()


def _log(tmp_path):
    log = EventLog(tmp_path / "events.jsonl")
    telemetry_ctx.set_current_run(event_log=log, run_id="run-bd83", step_name="s", phase="phase_6", cycle=1)
    return log


def _events(log, event_type="llm_cost_observed"):
    return [e["payload"] for e in log.read_all() if e["event_type"] == event_type]


def _fake_backend(name, data, *, caps=()):
    def _impl(**kwargs):
        merged = {**data, **(kwargs.get("extra_data") or {})}
        return StepResult(status="ok", data=merged, duration_ms=1, step_name=kwargs["step_name"])

    register_backend(name, _impl, manifest_source="git_diff", capabilities=set(caps), overwrite=True)


def _wf_ctx(tmp_path) -> WorkflowContext:
    scratch = tmp_path / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    worktree = tmp_path / "fake_worktree"
    worktree.mkdir(parents=True, exist_ok=True)
    org = {
        "scratchpad_dir": str(scratch),
        "current_worktree_path": str(worktree),
        "complexity": "FEATURE",
    }
    return WorkflowContext(
        tenant_id="t", scope=None, db_path=None, org_config=org,
        question="add feature X", session_id="test-bd83", persona="p",
        framework=None, domain=None,
    )


@pytest.mark.parametrize("bad", [True, False, -1, "12", 1.5, None])
def test_ac7_invalid_wall_ms_is_not_forwarded(tmp_path, bad):
    log = _log(tmp_path)
    _fake_backend("walled", {"raw_response": "x", "wall_ms": bad})
    invoke_llm_subprocess(prompt="p", model="m", timeout_sec=5, step_name="bd83", backend="walled", idle_timeout_sec=0)
    (payload,) = _events(log)
    assert "wall_ms" not in payload


@pytest.mark.parametrize("name", ["claude-subprocess", "anthropic-api"])
def test_ac7_backends_without_wall_ms_keep_their_payload_unchanged(tmp_path, name):
    log = _log(tmp_path)
    _fake_backend(name, {"raw_response": "x", "usage": {"input_tokens": 3, "output_tokens": 4}})
    invoke_llm_subprocess(prompt="p", model="m", timeout_sec=5, step_name="bd83", backend=name, idle_timeout_sec=0)
    (payload,) = _events(log)
    assert "wall_ms" not in payload
    assert payload["tokens_in"] == 3 and payload["tokens_out"] == 4


def test_ac11_default_prompt_pins_the_three_write_instructions_verbatim(tmp_path):
    result = phase_6_review._build_review_prompt(_wf_ctx(tmp_path), None)
    assert result.status == "ok", f"{result.error_code}: {result.error}"
    prompt = result.data["prompt"]
    scratchpad = tmp_path / "scratch"
    assert (
        "ROLE: You are the sole reviewer. Review the work yourself, write your findings "
        f"to {scratchpad / 'reviews'}/role-composite.md, then write the aggregated review into "
        f"{scratchpad / phase_6_review.REVIEW_DOC_RELPATH}. VERIFICATION-ONLY — do NOT edit code or test files."
    ) in prompt
    assert (
        "Use the Write tool to write your findings to ONE file:\n"
        f"    {scratchpad / 'reviews'}/role-composite.md\n"
        "Schema:\n"
    ) in prompt
    assert (
        "OUTPUT — Use the Write tool to write the full aggregated review to the review file named above. "
        "Your final response is a status summary under 200 tokens, ending with one line: "
        "VERDICT: <PASS|PARTIAL|FAIL>. Do NOT echo the review body — it is already on disk. "
        "Your status summary MUST begin with a line exactly `## Aggregated Findings`."
    ) in prompt
    assert "Reply with the complete aggregated review as your final answer" not in prompt


_CANONICAL_SHA256 = {
    "SINGLE_REVIEW_FRAMING_TEMPLATE": "f42cf190c77b4392d942c22a55e0b81cb8fdec723fe1fbe36665ca9527356fec",
    "PER_ROLE_SCHEMA_TEMPLATE": "b99ae1ececcc4b956d3c9db16d8843ca51b1cfe3cbdc71bc7574d0646f0cb16a",
}


@pytest.mark.parametrize("name", sorted(_CANONICAL_SHA256))
def test_ac11_canonical_review_constants_stay_byte_identical(name):
    import hashlib

    from bytedigger_engine.lib.plugins.review_schema import canonical

    value = getattr(canonical, name)
    assert hashlib.sha256(value.encode()).hexdigest() == _CANONICAL_SHA256[name]


def test_ac11_tool_capable_judge_keeps_the_write_instructions(monkeypatch, tmp_path):
    def _impl(**kwargs):
        return StepResult(status="ok", data={"raw_response": "x"}, duration_ms=1, step_name=kwargs["step_name"])

    register_backend(
        "tools-too", _impl, manifest_source="api_text_response",
        capabilities={"no_tools", "tool_allowlist"}, overwrite=True,
    )
    monkeypatch.setenv("HAL_RUNNER_BACKEND_JUDGE", "tools-too")
    result = phase_6_review._build_review_prompt(_wf_ctx(tmp_path), None)
    assert result.status == "ok", f"{result.error_code}: {result.error}"
    prompt = result.data["prompt"]
    for marker in ("Use the Write tool", "write your findings", "write the aggregated review into"):
        assert marker in prompt
