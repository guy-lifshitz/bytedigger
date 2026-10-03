"""RED tests for bd#102 - the semantic verifier stops on pause-lane and fatal codes.

Spec: docs/decisions/2026-10-03-bd102-semantic-verifier-stop-codes.md (AC1-AC14, r1).

Unit under test: `verify_findings_semantic` / `_invoke_verifier_agent` in
lib/plugins/anti_hallucination/semantic_verifier.py. New names (ABSENT pre-GREEN):
`_VerifierStop`, `_STOP_ERROR_CODES`, flag `HAL_SEMANTIC_VERIFY_STOP_ON_FATAL`,
result key `semantic_unverified_reasons`, event `phase_6_semantic_verify_stopped`.

The model seam is the chokepoint backend (`register_backend("claude-subprocess")`)
returning a scripted StepResult per call; `_invoke_verifier_agent` is never patched.
The event log is redirected to tmp_path by replacing the module's `EventLog`
constructor. Pre-GREEN every test fails at assert time: new names are touched only
after an explicit existence assertion. No sleeps, no network, no model calls, no
singleton or timing-contended resource (workflows.md section 1i): every backend
answer is pre-scripted. No sys.path mutation.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from bytedigger_engine import telemetry_ctx
from bytedigger_engine import llm_subprocess
from bytedigger_engine.contracts import StepResult
from bytedigger_engine.event_log import EventLog
from bytedigger_engine.lib import env_limit
from bytedigger_engine.lib.plugins.anti_hallucination import semantic_verifier as sv
from bytedigger_engine.llm_subprocess import register_backend, reset_backends

REPO_ROOT = Path(__file__).resolve().parents[2]
KILL = "HAL_SEMANTIC_VERIFY_STOP_ON_FATAL"
STEP = "verify_findings_semantic"

REPRODUCED = (
    "REPRODUCED:\n"
    "file: src/core.py:10\n"
    "repro: pytest tests/test_core.py -v\n"
    "evidence: line 10 accesses obj.value without a None check.\n"
)
REFUTED = "REFUTED:\nreason: fine\nrationale: the loop is bounded\n"
CANNOT_DECIDE = "UNVERIFIED:\nreason: cannot_decide\n"

SPEND = StepResult(status="error", data={"pausable": True}, duration_ms=1, step_name=STEP,
                   error="spend limit reached: weekly cap", error_code="E_LLM_SPEND_LIMIT",
                   recoverable=True)
STOP_RESULTS = {
    "E_LLM_SPEND_LIMIT": SPEND,
    "E_CAPABILITY_ESCAPE": StepResult(
        status="error", data={"capability_escapes": ["Write"]}, duration_ms=1, step_name=STEP,
        error="read-only verifier used Write", error_code="E_CAPABILITY_ESCAPE", recoverable=False),
    "E_LLM_BACKEND_UNKNOWN": StepResult(
        status="error", data={"backend": "nope"}, duration_ms=1, step_name=STEP,
        error="unknown backend nope", error_code="E_LLM_BACKEND_UNKNOWN", recoverable=False),
    "E_LLM_RUN_ID_MISSING": StepResult(
        status="error", data={"why": "no run"}, duration_ms=1, step_name=STEP,
        error="run id missing", error_code="E_LLM_RUN_ID_MISSING", recoverable=False),
}


def _ok(text) -> StepResult:
    return StepResult(status="ok", data={"raw_response": text}, duration_ms=1, step_name=STEP)


def _err(code: str, error: str = "boom") -> StepResult:
    return StepResult(status="error", data=None, duration_ms=1, step_name=STEP,
                      error=error, error_code=code)


@pytest.fixture(autouse=True)
def _reset(monkeypatch, tmp_path):
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


def _backend(script, default=None):
    """Backend whose n-th call returns script[n]; later calls return `default`
    (REPRODUCED text when None). Returns the list of call kwargs."""
    calls: list[dict] = []
    fallback = default if default is not None else _ok(REPRODUCED)

    def _impl(**kwargs):
        n = len(calls)
        calls.append(kwargs)
        return script[n] if n < len(script) else fallback

    register_backend("claude-subprocess", _impl, manifest_source="harness_tool_record",
                     capabilities={"manifest", "tool_allowlist", "warm_resume"}, overwrite=True)
    return calls


def _doc(tmp_path: Path, n: int, severity: str = "HIGH") -> Path:
    body = "# Build Review\n\n## Aggregated Findings\n\n"
    for i in range(n):
        body += (f"### SEVERITY: {severity} — finding {i}\n"
                 f"> src/f{i}.py:{i + 1}: quote{i}\n"
                 f"Claim: claim {i}.\n\n")
    path = tmp_path / "review.md"
    path.write_text(body, encoding="utf-8")
    return path


def _prev(doc: Path, verdict: str = "PASS") -> StepResult:
    return StepResult(status="ok", data={"verdict": verdict, "review_doc_path": str(doc)},
                      duration_ms=0, step_name="verify_findings")


def _run(doc: Path, verdict: str = "PASS"):
    return sv.verify_findings_semantic(ctx=None, prev=_prev(doc, verdict))


def _rows(log: EventLog, event_type: str) -> list[dict]:
    return [e for e in log.read_all() if e["event_type"] == event_type]


# ---------------------------------------------------------------- AC1

@pytest.mark.parametrize("env", [None, "1"], ids=["unset", "one"])
def test_ac1_spend_limit_stops_the_step_after_one_call(tmp_path, log, monkeypatch, env):
    if env is not None:
        monkeypatch.setenv(KILL, env)
    calls = _backend([SPEND], default=SPEND)
    result = _run(_doc(tmp_path, 20))
    assert result.status == "error"
    assert result.error_code == "E_LLM_SPEND_LIMIT"
    assert result.data == {"pausable": True}
    assert len(calls) == 1


# ---------------------------------------------------------------- AC2

@pytest.mark.parametrize("code", ["E_CAPABILITY_ESCAPE", "E_LLM_BACKEND_UNKNOWN", "E_LLM_RUN_ID_MISSING"])
def test_ac2_fatal_codes_return_the_backend_result_unchanged(tmp_path, log, code):
    sent = STOP_RESULTS[code]
    calls = _backend([sent], default=sent)
    result = _run(_doc(tmp_path, 20))
    assert result.status == "error"
    assert result.error_code == code
    assert result.data == sent.data
    assert result.recoverable == sent.recoverable
    assert result.error == sent.error
    assert len(calls) == 1


# ---------------------------------------------------------------- AC3

def test_ac3_stop_leaves_doc_untouched_and_emits_stopped_event(tmp_path, log):
    doc = _doc(tmp_path, 20)
    before = doc.read_bytes()
    _backend([SPEND], default=SPEND)
    _run(doc)
    assert doc.read_bytes() == before
    stopped = _rows(log, "phase_6_semantic_verify_stopped")
    assert len(stopped) == 1
    assert stopped[0]["payload"]["error_code"] == "E_LLM_SPEND_LIMIT"
    assert stopped[0]["payload"]["findings_total"] == 20
    assert _rows(log, "phase_6_semantic_verify_complete") == []


# ---------------------------------------------------------------- AC4

def test_ac4_mid_loop_stop_does_not_partially_rewrite(tmp_path, log):
    doc = _doc(tmp_path, 5)
    before = doc.read_bytes()
    calls = _backend([_ok(REFUTED), _ok(REFUTED), SPEND], default=SPEND)
    result = _run(doc)
    assert result.status == "error" and result.error_code == "E_LLM_SPEND_LIMIT"
    assert len(calls) == 3
    assert doc.read_bytes() == before
    stopped = _rows(log, "phase_6_semantic_verify_stopped")
    assert len(stopped) == 1 and stopped[0]["payload"]["model_haiku_calls"] == 3


# ---------------------------------------------------------------- AC5

def test_ac5_stop_fires_on_the_opus_escalation(tmp_path, log):
    doc = _doc(tmp_path, 3)
    before = doc.read_bytes()
    calls = _backend([_ok(CANNOT_DECIDE), SPEND], default=SPEND)
    result = _run(doc)
    assert result.status == "error" and result.error_code == "E_LLM_SPEND_LIMIT"
    assert len(calls) == 2
    assert doc.read_bytes() == before


# ---------------------------------------------------------------- AC6

def test_ac6_non_stop_error_stays_tag_and_continue(tmp_path, log):
    doc = _doc(tmp_path, 3)
    calls = _backend([], default=_err("E_LLM_EXIT"))
    result = _run(doc)
    assert result.status == "ok"
    assert result.data["semantic_unverified"] == 3
    assert len(calls) == 3
    assert result.data.get("semantic_unverified_reasons") == {"E_LLM_EXIT": 3}
    assert doc.read_text(encoding="utf-8").count("### SEVERITY: [UNVERIFIED]") == 3


# ---------------------------------------------------------------- AC7

def test_ac7_kill_switch_restores_tag_and_continue(tmp_path, log, monkeypatch):
    monkeypatch.setenv(KILL, "0")
    doc = _doc(tmp_path, 3)
    calls = _backend([], default=SPEND)
    result = _run(doc)
    assert result.status == "ok"
    assert len(calls) == 3
    assert result.data["semantic_unverified"] == 3
    assert result.data.get("semantic_unverified_reasons") == {"E_LLM_SPEND_LIMIT": 3}


# ---------------------------------------------------------------- AC8

def test_ac8_tally_by_reason(tmp_path, log):
    doc = _doc(tmp_path, 21)
    calls = _backend([_err("E_LLM_TIMEOUT", "timed out"), _ok(""), _ok("I think this is fine.\n")])
    result = _run(doc)
    assert result.status == "ok"
    assert len(calls) == 20
    expected = {"agent_timeout": 1, "empty_response": 1, "no_verdict_marker": 1, "overflow": 1}
    got = result.data.get("semantic_unverified_reasons")
    assert got == expected
    assert sum(got.values()) == result.data["semantic_unverified"]
    complete = _rows(log, "phase_6_semantic_verify_complete")
    assert len(complete) == 1
    assert complete[0]["payload"].get("semantic_unverified_reasons") == expected


# ---------------------------------------------------------------- AC9

def test_ac9_all_reproduced_gives_empty_tally(tmp_path, log):
    _backend([])
    result = _run(_doc(tmp_path, 3))
    assert result.status == "ok" and result.data["semantic_unverified"] == 0
    assert "semantic_unverified_reasons" in result.data
    assert result.data["semantic_unverified_reasons"] == {}
    complete = _rows(log, "phase_6_semantic_verify_complete")
    assert len(complete) == 1
    assert complete[0]["payload"]["semantic_unverified_reasons"] == {}


# ---------------------------------------------------------------- AC10

FINDING = {"file": "x.py", "line": 3, "quote": "q", "claim": "off by one", "severity": "HIGH"}


def test_ac10_invoke_raises_on_stop_codes_only(monkeypatch):
    assert hasattr(sv, "_VerifierStop"), "_VerifierStop must exist"
    for code, sent in STOP_RESULTS.items():
        _backend([sent], default=sent)
        with pytest.raises(sv._VerifierStop) as info:
            sv._invoke_verifier_agent(FINDING)
        assert info.value.result.error_code == code

    _backend([], default=_err("E_LLM_TIMEOUT", "timed out"))
    assert sv._invoke_verifier_agent(FINDING) == "UNVERIFIED:\nreason: agent_timeout\n"

    monkeypatch.setenv(KILL, "0")
    _backend([], default=SPEND)
    raw = sv._invoke_verifier_agent(FINDING)
    assert raw.startswith("UNVERIFIED:\nreason: agent_error E_LLM_SPEND_LIMIT")


# ---------------------------------------------------------------- AC11

def test_ac11_stop_code_set():
    assert hasattr(sv, "_STOP_ERROR_CODES"), "_STOP_ERROR_CODES must exist"
    assert set(sv._STOP_ERROR_CODES) == {
        "E_LLM_SPEND_LIMIT", "E_CAPABILITY_ESCAPE", "E_LLM_BACKEND_UNKNOWN", "E_LLM_RUN_ID_MISSING"}
    assert set(env_limit.PAUSE_LANE_ERROR_CODES) <= set(sv._STOP_ERROR_CODES)


# ---------------------------------------------------------------- AC12

def test_ac12_flag_catalogued_and_owner_lint_passes():
    from bytedigger_engine import flags_catalog  # noqa: PLC0415

    assert KILL in flags_catalog.FLAGS, "kill switch must be catalogued"
    entry = flags_catalog.FLAGS[KILL]
    assert entry.get("kind") == "gate"
    assert entry.get("default") == "1"
    assert isinstance(entry.get("owner"), str) and entry["owner"].strip()
    assert str(entry.get("provenance", "")).startswith("introduced:")

    lint = REPO_ROOT / "scripts" / "flag_owner_lint.py"
    result = subprocess.run([sys.executable, str(lint)], capture_output=True, text=True,
                            cwd=str(REPO_ROOT))
    assert result.returncode == 0, f"{result.stdout!r} {result.stderr!r}"


# ---------------------------------------------------------------- AC13 (guard)

def test_ac13_fail_skip_and_no_section_paths_have_no_tally_key(tmp_path, log):
    from bytedigger_engine import flags_catalog  # noqa: PLC0415

    assert hasattr(sv, "_STOP_ERROR_CODES"), "bd#102 surface must exist"
    assert KILL in flags_catalog.FLAGS, "bd#102 kill switch must be catalogued"
    calls = _backend([])
    medium = _doc(tmp_path, 2, severity="MEDIUM")
    skipped = _run(medium, verdict="FAIL")
    assert skipped.status == "ok"
    assert "semantic_unverified_reasons" not in skipped.data

    nosec = tmp_path / "nosec.md"
    nosec.write_text("# Build Review\n\nnothing here\n", encoding="utf-8")
    plain = _run(nosec)
    assert plain.status == "ok"
    assert "semantic_unverified_reasons" not in plain.data
    assert calls == []


# ---------------------------------------------------------------- AC14 (guard)

def test_ac14_changelog_fixed_block_mentions_flag():
    from helpers.changelog import read_changelog, require_entry  # noqa: PLC0415

    require_entry(read_changelog(), KILL, block="Fixed")


# ---------------------------------------------------------------- AC15

def test_ac15_stopped_row_carries_tally_and_counts_overflow(tmp_path, log):
    doc = _doc(tmp_path, 21)
    before = doc.read_bytes()
    calls = _backend([_err("E_LLM_EXIT"), SPEND], default=SPEND)
    result = _run(doc)
    assert result.status == "error" and result.error_code == "E_LLM_SPEND_LIMIT"
    assert len(calls) == 2
    assert doc.read_bytes() == before
    stopped = _rows(log, "phase_6_semantic_verify_stopped")
    assert len(stopped) == 1
    payload = stopped[0]["payload"]
    assert payload.get("findings_total") == 21
    assert payload.get("semantic_unverified_reasons") == {"E_LLM_EXIT": 1}
    assert payload.get("model_opus_calls") == 0
    assert payload.get("model_haiku_calls") == 2
    assert isinstance(payload.get("duration_ms"), int)
    assert "unverified_reasons" not in payload, "one key name across both terminal events"


def test_ac15_stopped_row_counts_opus_calls(tmp_path, log):
    _backend([_ok(CANNOT_DECIDE), SPEND], default=SPEND)
    _run(_doc(tmp_path, 3))
    stopped = _rows(log, "phase_6_semantic_verify_stopped")
    assert len(stopped) == 1
    assert stopped[0]["payload"].get("model_opus_calls") == 1
    assert stopped[0]["payload"].get("semantic_unverified_reasons") == {}


def test_ac15_zero_findings_gives_empty_tally(tmp_path, log):
    calls = _backend([])
    result = _run(_doc(tmp_path, 0))
    assert result.status == "ok" and calls == []
    assert result.data.get("semantic_unverified_reasons") == {}
    complete = _rows(log, "phase_6_semantic_verify_complete")
    assert len(complete) == 1
    assert complete[0]["payload"].get("semantic_unverified_reasons") == {}
