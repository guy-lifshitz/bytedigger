"""RED tests for bd#247 - one fatal/transient classification of chokepoint error codes.

Spec: docs/decisions/2026-10-10-bd247-fatal-error-classification.md (AC1-AC9, AC11).

Unit under test: `llm_subprocess.FATAL_ERROR_CODES` / `is_fatal_error_code` /
`classify_os_error` (ABSENT pre-GREEN) and their reader `verify_findings_semantic` in
lib/plugins/anti_hallucination/semantic_verifier.py. Pre-GREEN every test fails at assert
time: new names are touched only after an explicit hasattr assertion.

Real path: a real review doc on tmp_path, the real `verify_findings_semantic`, the real
chokepoint, the real WorkflowEngine. The only scripted seam is the registered backend result.
Missing-binary cases register nothing: HAL_RUNNER_BACKEND=claude-subprocess with PATH pointing
at an empty dir (chokepoint returns E_LLM_CMD_MISSING) or at a dir holding a chmod 644 `claude`
(chokepoint raises PermissionError). Pre-staged state only, no sleeps, no network, no model
calls, no racing (workflows.md section 1i). No sys.path mutation.
"""
from __future__ import annotations

import ast
import errno
import inspect
import os
from pathlib import Path

import pytest

from bytedigger_engine import llm_subprocess, telemetry_ctx
from bytedigger_engine.contracts import StepContract, StepResult, WorkflowContext, WorkflowDefinition
from bytedigger_engine.engine import WorkflowEngine
from bytedigger_engine.event_log import EventLog
from bytedigger_engine.lib import env_limit
from bytedigger_engine.lib.plugins.anti_hallucination import semantic_verifier as sv
from bytedigger_engine.llm_subprocess import register_backend, reset_backends

KILL = "HAL_SEMANTIC_VERIFY_STOP_ON_FATAL"
STEP = "verify_findings_semantic"

REPRODUCED = (
    "REPRODUCED:\n"
    "file: src/core.py:10\n"
    "repro: pytest tests/test_core.py -v\n"
    "evidence: line 10 accesses obj.value without a None check.\n"
)

NINE = {
    "E_LLM_SPEND_LIMIT", "E_CAPABILITY_ESCAPE", "E_LLM_BACKEND_UNKNOWN", "E_LLM_RUN_ID_MISSING",
    "E_LLM_CMD_MISSING", "E_LLM_BACKEND_NO_MANIFEST", "E_MODEL_PIN_MISMATCH",
    "E_INJECT_UNATTRIBUTED", "E_TOOL_RESTRICTION_UNSUPPORTED",
}
TRANSIENT = ["E_LLM_TIMEOUT", "E_LLM_API_TIMEOUT", "E_LLM_NO_PROGRESS", "E_STEP_TIMEOUT",
             "E_LLM_EXIT", "E_LLM_RESULT_ERROR", "E_LLM_NO_RESULT_EVENT", None, "", "E_SOMETHING_NEW"]
ADDED_SCRIPTED = ["E_LLM_BACKEND_NO_MANIFEST", "E_MODEL_PIN_MISMATCH", "E_INJECT_UNATTRIBUTED",
                  "E_TOOL_RESTRICTION_UNSUPPORTED"]


def _err(code: str, error: str = "boom", data=None, recoverable: bool = False) -> StepResult:
    return StepResult(status="error", data=data, duration_ms=1, step_name=STEP,
                      error=error, error_code=code, recoverable=recoverable)


def _ok(text) -> StepResult:
    return StepResult(status="ok", data={"raw_response": text}, duration_ms=1, step_name=STEP)


SPEND = _err("E_LLM_SPEND_LIMIT", "spend limit reached: weekly cap", {"pausable": True}, True)


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setattr(llm_subprocess, "emit_resolver_resolved", lambda *a, **kw: None)
    monkeypatch.delenv(KILL, raising=False)
    telemetry_ctx.clear_current_run()
    yield
    telemetry_ctx.clear_current_run()
    reset_backends()


@pytest.fixture
def log(monkeypatch, tmp_path) -> EventLog:
    event_log = EventLog(tmp_path / "events" / "e.jsonl")
    monkeypatch.setattr(sv, "EventLog", lambda *a, **kw: event_log)
    return event_log


def _backend(script, default=None, name: str = "claude-subprocess"):
    """Backend whose n-th call returns script[n]; later calls `default` (REPRODUCED text when None)."""
    calls: list[dict] = []
    fallback = default if default is not None else _ok(REPRODUCED)

    def _impl(**kwargs):
        n = len(calls)
        calls.append(kwargs)
        return script[n] if n < len(script) else fallback

    register_backend(name, _impl, manifest_source="harness_tool_record",
                     capabilities={"manifest", "tool_allowlist", "warm_resume"}, overwrite=True)
    return calls


def _raising_backend(make_exc):
    calls: list[dict] = []

    def _impl(**kwargs):
        calls.append(kwargs)
        raise make_exc()

    register_backend("claude-subprocess", _impl, manifest_source="harness_tool_record",
                     capabilities={"manifest", "tool_allowlist", "warm_resume"}, overwrite=True)
    return calls


def _doc(tmp_path: Path, n: int) -> Path:
    body = "# Build Review\n\n## Aggregated Findings\n\n"
    for i in range(n):
        body += (f"### SEVERITY: HIGH — finding {i}\n"
                 f"> src/f{i}.py:{i + 1}: quote{i}\n"
                 f"Claim: claim {i}.\n\n")
    path = tmp_path / "review.md"
    path.write_text(body, encoding="utf-8")
    return path


def _prev(doc: Path) -> StepResult:
    return StepResult(status="ok", data={"verdict": "PASS", "review_doc_path": str(doc)},
                      duration_ms=0, step_name="verify_findings")


def _run(doc: Path):
    return sv.verify_findings_semantic(ctx=None, prev=_prev(doc))


def _rows(log: EventLog, event_type: str) -> list[dict]:
    return [e for e in log.read_all() if e["event_type"] == event_type]


def _path_empty(monkeypatch, tmp_path) -> None:
    d = tmp_path / "emptybin"
    d.mkdir()
    monkeypatch.setenv("HAL_RUNNER_BACKEND", "claude-subprocess")
    monkeypatch.setenv("PATH", str(d))


def _path_nonexec(monkeypatch, tmp_path) -> None:
    d = tmp_path / "noexecbin"
    d.mkdir()
    f = d / "claude"
    f.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    os.chmod(f, 0o644)
    monkeypatch.setenv("HAL_RUNNER_BACKEND", "claude-subprocess")
    monkeypatch.setenv("PATH", str(d))


def _need(name: str):
    assert hasattr(llm_subprocess, name), f"llm_subprocess.{name} must exist (bd#247)"
    return getattr(llm_subprocess, name)


# ---------------------------------------------------------------- AC1

def test_ac1_fatal_error_codes_are_the_nine_and_classify():
    fatal = _need("FATAL_ERROR_CODES")
    is_fatal = _need("is_fatal_error_code")
    assert set(fatal) == NINE
    assert env_limit.PAUSE_LANE_ERROR_CODES <= set(fatal)
    for code in NINE:
        assert is_fatal(code) is True, code
    for code in TRANSIENT:
        assert is_fatal(code) is False, repr(code)


# ---------------------------------------------------------------- AC2

def test_ac2_classify_os_error_is_narrow():
    classify = _need("classify_os_error")
    assert classify(FileNotFoundError(errno.ENOENT, "no such file")) == "E_LLM_CMD_MISSING"
    assert classify(PermissionError(errno.EACCES, "denied")) == "E_LLM_CMD_MISSING"
    for exc in (OSError(errno.EAGAIN, "try again"), OSError(errno.ENOMEM, "no memory"),
                TimeoutError("t"), ConnectionResetError("r"), BrokenPipeError("p")):
        assert classify(exc) is None, type(exc).__name__


# ---------------------------------------------------------------- AC3

@pytest.mark.parametrize("env", [None, "1"], ids=["unset", "one"])
@pytest.mark.parametrize("code", ADDED_SCRIPTED + ["E_LLM_CMD_MISSING"])
def test_ac3_added_fatal_code_stops_the_step(tmp_path, log, monkeypatch, code, env):
    if env is not None:
        monkeypatch.setenv(KILL, env)
    doc = _doc(tmp_path, 20)
    before = doc.read_bytes()
    calls = None
    sent = None
    if code == "E_LLM_CMD_MISSING":
        _path_empty(monkeypatch, tmp_path)
    else:
        sent = _err(code, f"scripted {code}", {"why": code}, recoverable=False)
        calls = _backend([sent], default=sent)
    result = _run(doc)
    assert result.status == "error"
    assert result.error_code == code
    assert doc.read_bytes() == before
    stopped = _rows(log, "phase_6_semantic_verify_stopped")
    assert len(stopped) == 1 and stopped[0]["payload"]["error_code"] == code
    assert _rows(log, "phase_6_semantic_verify_complete") == []
    if sent is not None:
        assert result.data == sent.data
        assert result.recoverable == sent.recoverable
        assert result.error == sent.error
        assert len(calls) == 1


# ---------------------------------------------------------------- AC4

@pytest.mark.parametrize("env", [None, "1"], ids=["unset", "one"])
def test_ac4_permission_error_from_chokepoint_stops_the_step(tmp_path, log, monkeypatch, env):
    if env is not None:
        monkeypatch.setenv(KILL, env)
    _path_nonexec(monkeypatch, tmp_path)
    doc = _doc(tmp_path, 20)
    before = doc.read_bytes()
    result = _run(doc)
    assert result.status == "error"
    assert result.error_code == "E_LLM_CMD_MISSING"
    assert result.recoverable is False
    assert "ermission" in (result.error or ""), result.error  # the OS message, not a generic text
    assert doc.read_bytes() == before
    stopped = _rows(log, "phase_6_semantic_verify_stopped")
    assert len(stopped) == 1 and stopped[0]["payload"]["error_code"] == "E_LLM_CMD_MISSING"
    assert stopped[0]["payload"].get("model_haiku_calls") == 1  # one attempt


# ---------------------------------------------------------------- AC5

@pytest.mark.parametrize("make_exc", [lambda: OSError(errno.EAGAIN, "try again"), lambda: TimeoutError("slow")],
                         ids=["EAGAIN", "TimeoutError"])
def test_ac5_transient_os_error_stays_tag_and_continue(tmp_path, log, make_exc):
    assert hasattr(llm_subprocess, "classify_os_error"), "bd#247 classification must exist"
    doc = _doc(tmp_path, 3)
    calls = _raising_backend(make_exc)
    result = _run(doc)
    assert result.status == "ok"
    assert len(calls) == 3
    assert result.data["semantic_unverified"] == 3
    assert doc.read_text(encoding="utf-8").count("### SEVERITY: [UNVERIFIED]") == 3


def test_ac5_transient_timeout_result_is_tallied_as_agent_timeout(tmp_path, log):
    assert hasattr(llm_subprocess, "is_fatal_error_code"), "bd#247 classification must exist"
    doc = _doc(tmp_path, 3)
    calls = _backend([], default=_err("E_LLM_TIMEOUT", "timed out"))
    result = _run(doc)
    assert result.status == "ok"
    assert len(calls) == 3
    assert result.data.get("semantic_unverified_reasons") == {"agent_timeout": 3}


# ---------------------------------------------------------------- AC6

def _assert_tag_and_continue(doc: Path, result, n: int) -> None:
    assert result.status == "ok"
    assert result.data["semantic_unverified"] == n
    assert doc.read_text(encoding="utf-8").count("### SEVERITY: [UNVERIFIED]") == n


def test_ac6_kill_switch_scripted_added_code(tmp_path, log, monkeypatch):
    assert hasattr(llm_subprocess, "FATAL_ERROR_CODES"), "bd#247 classification must exist"
    monkeypatch.setenv(KILL, "0")
    doc = _doc(tmp_path, 3)
    calls = _backend([], default=_err("E_MODEL_PIN_MISMATCH", "pin"))
    result = _run(doc)
    assert len(calls) == 3
    _assert_tag_and_continue(doc, result, 3)


def test_ac6_kill_switch_missing_binary_code(tmp_path, log, monkeypatch):
    assert hasattr(llm_subprocess, "FATAL_ERROR_CODES"), "bd#247 classification must exist"
    monkeypatch.setenv(KILL, "0")
    _path_empty(monkeypatch, tmp_path)
    doc = _doc(tmp_path, 3)
    _assert_tag_and_continue(doc, _run(doc), 3)


def test_ac6_kill_switch_permission_error_path(tmp_path, log, monkeypatch):
    assert hasattr(llm_subprocess, "classify_os_error"), "bd#247 classification must exist"
    monkeypatch.setenv(KILL, "0")
    _path_nonexec(monkeypatch, tmp_path)
    doc = _doc(tmp_path, 3)
    _assert_tag_and_continue(doc, _run(doc), 3)


# ---------------------------------------------------------------- AC7

def test_ac7_verifier_reads_the_chokepoint_list_at_call_time(tmp_path, log, monkeypatch):
    fatal = _need("FATAL_ERROR_CODES")
    # original value: E_LLM_EXIT is transient
    doc = _doc(tmp_path, 3)
    calls = _backend([], default=_err("E_LLM_EXIT"))
    result = _run(doc)
    assert result.status == "ok" and len(calls) == 3
    # extended value: the same code now stops the step after one call
    monkeypatch.setattr(llm_subprocess, "FATAL_ERROR_CODES", frozenset(fatal) | {"E_LLM_EXIT"})
    doc2 = _doc(tmp_path, 3)
    before = doc2.read_bytes()
    calls2 = _backend([], default=_err("E_LLM_EXIT"))
    result2 = _run(doc2)
    assert result2.status == "error" and result2.error_code == "E_LLM_EXIT"
    assert len(calls2) == 1
    assert doc2.read_bytes() == before


def test_ac7_verifier_holds_no_second_list_of_codes():
    assert hasattr(llm_subprocess, "FATAL_ERROR_CODES"), "bd#247 classification must exist"
    assert not hasattr(sv, "_STOP_ERROR_CODES")
    tree = ast.parse(inspect.getsource(sv))
    literals = {n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)}
    assert "E_CAPABILITY_ESCAPE" not in literals
    assert "E_LLM_BACKEND_UNKNOWN" not in literals


# ---------------------------------------------------------------- AC8 / AC9 (engine level)

class _FakeEventLog:
    def __init__(self):
        self.events: list[tuple[str, dict, str]] = []

    def append(self, event_type: str, payload: dict, run_id: str | None = None) -> None:
        self.events.append((event_type, payload, run_id))


def _ctx() -> WorkflowContext:
    return WorkflowContext(tenant_id="t", scope=None, db_path=None, org_config=None, question="q",
                           session_id="s", persona="p", framework=None, domain=None)


def _engine_run(doc: Path):
    sink = _FakeEventLog()
    eng = WorkflowEngine(event_log=sink)

    def _step(ctx, _prev_unused):
        return sv.verify_findings_semantic(ctx=ctx, prev=_prev(doc))

    eng.register("solo", WorkflowDefinition(name="solo", steps=[StepContract(name=STEP, execute=_step)]))
    eng.execute("solo", _ctx(), run_id="rid-bd247")
    finished = [e[1] for e in sink.events if e[0] == "workflow_finished"]
    assert len(finished) == 1
    return finished[0]


@pytest.mark.parametrize("backend_name", ["claude-subprocess", "openai-compat"])
def test_ac8_spend_limit_pauses_the_workflow(tmp_path, log, monkeypatch, backend_name):
    assert hasattr(llm_subprocess, "FATAL_ERROR_CODES"), "bd#247 classification must exist"
    monkeypatch.setenv("HAL_RUNNER_BACKEND", backend_name)
    calls = _backend([SPEND], default=SPEND, name=backend_name)
    row = _engine_run(_doc(tmp_path, 20))
    assert row["status"] == "paused", row
    assert len(calls) == 1


def test_ac8_pause_lane_off_gives_error_with_code(tmp_path, log, monkeypatch):
    assert hasattr(llm_subprocess, "FATAL_ERROR_CODES"), "bd#247 classification must exist"
    monkeypatch.setenv("HAL_PAUSE_LANE", "0")
    calls = _backend([SPEND], default=SPEND)
    row = _engine_run(_doc(tmp_path, 20))
    assert row["status"] == "error", row
    assert row.get("error_code") == "E_LLM_SPEND_LIMIT"
    assert len(calls) == 1


def test_ac9_missing_binary_ends_the_workflow_as_error(tmp_path, log, monkeypatch):
    _path_empty(monkeypatch, tmp_path)
    row = _engine_run(_doc(tmp_path, 20))
    assert row["status"] == "error", row
    assert row.get("error_code") == "E_LLM_CMD_MISSING"


# ---------------------------------------------------------------- AC11 (guard)

def test_ac11_changelog_fixed_block_mentions_flag_and_bd247():
    from helpers.changelog import read_changelog, require_entry  # noqa: PLC0415

    text = read_changelog()
    sec = require_entry(text, "bd#247", block="Fixed")
    assert sec in [require_entry(text, KILL, block="Fixed")] or KILL in sec.body
