"""RED tests for bd#203 - the semantic verifier writes its events to the run's own event log.

Spec: docs/decisions/2026-10-10-bd203-semantic-verifier-run-scoped-log.md (AC1-AC10, r1).

Unit under test: `verify_findings_semantic` in
lib/plugins/anti_hallucination/semantic_verifier.py. New names (ABSENT pre-GREEN):
`_run_event_log`, flag `HAL_SEMANTIC_VERIFY_RUN_LOG`. They are touched only inside
test bodies, so every test fails individually at assert time (never at collection).

The model seam is the chokepoint backend (`register_backend("claude-subprocess")`)
returning a scripted StepResult. `sv.EventLog` is NOT patched: events must land
in a real file, and pre-GREEN they land under the process cwd (an empty dir
`elsewhere`). Determinism (workflows.md section 1i): every backend answer is
pre-scripted, every filesystem location is pre-staged under tmp_path, no
sleeps, no network, no real `claude`, no sys.path mutation.
AC10 (existing suites stay green) is verified by running those suites; no test here.
"""
from __future__ import annotations

import ast
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from bytedigger_engine import event_log, llm_subprocess, telemetry_ctx
from bytedigger_engine.contracts import StepResult
from bytedigger_engine.event_log import EventLog
from bytedigger_engine.lib.plugins.anti_hallucination import semantic_verifier as sv
from bytedigger_engine.llm_subprocess import register_backend, reset_backends

REPO_ROOT = Path(__file__).resolve().parents[2]
FLAG = "HAL_SEMANTIC_VERIFY_RUN_LOG"
STEP = "verify_findings_semantic"
RUN_ID = "R203"
SKIPPED = "phase_6_semantic_verify_skipped"
STOPPED = "phase_6_semantic_verify_stopped"
COMPLETE = "phase_6_semantic_verify_complete"

REPRODUCED = (
    "REPRODUCED:\n"
    "file: src/core.py:10\n"
    "repro: pytest tests/test_core.py -v\n"
    "evidence: line 10 accesses obj.value without a None check.\n"
)
SPEND = StepResult(status="error", data={"pausable": True}, duration_ms=1, step_name=STEP,
                   error="spend limit reached: weekly cap", error_code="E_LLM_SPEND_LIMIT",
                   recoverable=True)

_BAD_RUN_IDS = ["../x", "a/b", ".h", "a" * 129]


def _ok(text) -> StepResult:
    return StepResult(status="ok", data={"raw_response": text}, duration_ms=1, step_name=STEP)


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch):
    monkeypatch.setattr(llm_subprocess, "emit_resolver_resolved", lambda *a, **kw: None)
    for prefix in ("HAL_", "BD_", "BYTEDIGGER_"):
        monkeypatch.delenv(prefix + "EVENT_LOG_PATH", raising=False)
        if prefix != "HAL_":
            monkeypatch.delenv(prefix + "RUN_LOG_ROOT", raising=False)
    monkeypatch.delenv(FLAG, raising=False)
    telemetry_ctx.clear_current_run()
    telemetry_ctx.set_invocation_run_id(None)
    yield
    telemetry_ctx.clear_current_run()
    telemetry_ctx.set_invocation_run_id(None)
    reset_backends()


@pytest.fixture
def env(monkeypatch, tmp_path):
    """Own run root, empty cwd `elsewhere`, review docs dir; returns (root, elsewhere)."""
    root = tmp_path / "root"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (tmp_path / "docs").mkdir()
    monkeypatch.setenv("HAL_RUN_LOG_ROOT", str(root))
    monkeypatch.chdir(elsewhere)
    return root, elsewhere


def _backend(default):
    register_backend("claude-subprocess", lambda **kw: default, manifest_source="harness_tool_record",
                     capabilities={"manifest", "tool_allowlist", "warm_resume"}, overwrite=True)


def _doc(tmp_path: Path, severity: str = "HIGH") -> Path:
    body = ("# Build Review\n\n## Aggregated Findings\n\n"
            f"### SEVERITY: {severity} — finding 0\n> src/f0.py:1: quote0\nClaim: claim 0.\n\n")
    path = tmp_path / "docs" / "review.md"
    path.write_text(body, encoding="utf-8")
    return path


def _run(tmp_path: Path, session_id, verdict: str = "PASS", severity: str = "HIGH"):
    doc = _doc(tmp_path, severity)
    prev = StepResult(status="ok", data={"verdict": verdict, "review_doc_path": str(doc)},
                      duration_ms=0, step_name="verify_findings")
    return sv.verify_findings_semantic(ctx=SimpleNamespace(session_id=session_id), prev=prev)


def _rows(path: Path, event_type: str) -> list[dict]:
    assert path.is_file(), f"no event log at {path}"
    return [e for e in EventLog(path).read_all() if e["event_type"] == event_type]


def _snapshot(base: Path) -> set[Path]:
    return set(base.rglob("*"))


# ---------------------------------------------------------------- AC1

def test_ac1_skip_path_goes_to_run_log(tmp_path, env):
    root, elsewhere = env
    _backend(_ok(REPRODUCED))
    result = _run(tmp_path, RUN_ID, verdict="FAIL", severity="MEDIUM")
    assert result.status == "ok"
    assert len(_rows(root / RUN_ID / "events.jsonl", SKIPPED)) == 1
    assert list(elsewhere.iterdir()) == []


# ---------------------------------------------------------------- AC2

def test_ac2_complete_path_goes_to_run_log(tmp_path, env):
    root, elsewhere = env
    _backend(_ok(REPRODUCED))
    result = _run(tmp_path, RUN_ID)
    assert result.status == "ok"
    assert len(_rows(root / RUN_ID / "events.jsonl", COMPLETE)) == 1
    assert list(elsewhere.iterdir()) == []


# ---------------------------------------------------------------- AC3

def test_ac3_stop_path_goes_to_run_log(tmp_path, env):
    root, elsewhere = env
    _backend(SPEND)
    result = _run(tmp_path, RUN_ID)
    assert result.status == "error" and result.error_code == "E_LLM_SPEND_LIMIT"
    rows = _rows(root / RUN_ID / "events.jsonl", STOPPED)
    assert len(rows) == 1
    assert rows[0]["payload"]["error_code"] == "E_LLM_SPEND_LIMIT"
    assert list(elsewhere.iterdir()) == []


# ---------------------------------------------------------------- AC4

def test_ac4_active_run_sink_wins_over_run_scoped_path(tmp_path, env):
    root, elsewhere = env
    sink = tmp_path / "sink.jsonl"
    telemetry_ctx.set_current_run(event_log=EventLog(sink), run_id=RUN_ID, step_name=STEP)
    _backend(_ok(REPRODUCED))
    result = _run(tmp_path, RUN_ID)
    assert result.status == "ok"
    assert len(_rows(sink, COMPLETE)) == 1
    assert not (root / RUN_ID).exists()
    assert list(elsewhere.iterdir()) == []


# ---------------------------------------------------------------- AC5

def test_ac5_standalone_caller_keeps_default_location(tmp_path, env):
    root, elsewhere = env
    assert hasattr(sv, "_run_event_log"), "bd#203 helper _run_event_log must exist"
    expected = event_log.default_log_path()
    assert elsewhere in expected.parents
    _backend(_ok(REPRODUCED))
    result = _run(tmp_path, None)
    assert result.status == "ok"
    assert len(_rows(expected, COMPLETE)) == 1
    assert not root.exists()


# ---------------------------------------------------------------- AC6

@pytest.mark.parametrize("bad_id", _BAD_RUN_IDS)
def test_ac6_unsafe_run_id_degrades_to_default_location(tmp_path, env, bad_id):
    root, elsewhere = env
    assert hasattr(sv, "_run_event_log"), "bd#203 helper _run_event_log must exist"
    expected = event_log.default_log_path()
    _backend(_ok(REPRODUCED))
    doc = _doc(tmp_path)
    before = _snapshot(tmp_path)
    prev = StepResult(status="ok", data={"verdict": "PASS", "review_doc_path": str(doc)},
                      duration_ms=0, step_name="verify_findings")
    result = sv.verify_findings_semantic(ctx=SimpleNamespace(session_id=bad_id), prev=prev)
    assert result.status == "ok" and result.data["semantic_reproduced"] == 1
    assert len(_rows(expected, COMPLETE)) == 1
    created = _snapshot(tmp_path) - before
    assert created <= {expected.parent, expected}, f"unexpected paths created: {created}"
    assert not root.exists()


# ---------------------------------------------------------------- AC7

def test_ac7_kill_switch_restores_cwd_relative_writer(tmp_path, env, monkeypatch):
    from bytedigger_engine import flags_catalog  # noqa: PLC0415

    root, elsewhere = env
    assert FLAG in flags_catalog.FLAGS, "kill switch must be catalogued"
    monkeypatch.setenv(FLAG, "0")
    expected = event_log.default_log_path()
    _backend(_ok(REPRODUCED))
    result = _run(tmp_path, RUN_ID)
    assert result.status == "ok"
    assert len(_rows(expected, COMPLETE)) == 1
    assert not (root / RUN_ID).exists()


@pytest.mark.parametrize("value", [None, "1"], ids=["unset", "one"])
def test_ac7_kill_switch_on_keeps_run_log(tmp_path, env, monkeypatch, value):
    from bytedigger_engine import flags_catalog  # noqa: PLC0415

    root, elsewhere = env
    assert FLAG in flags_catalog.FLAGS, "kill switch must be catalogued"
    if value is not None:
        monkeypatch.setenv(FLAG, value)
    _backend(_ok(REPRODUCED))
    result = _run(tmp_path, RUN_ID)
    assert result.status == "ok"
    assert len(_rows(root / RUN_ID / "events.jsonl", COMPLETE)) == 1
    assert list(elsewhere.iterdir()) == []


# ---------------------------------------------------------------- AC8 (guard)

def test_ac8_eventlog_constructed_only_in_helper_and_sites_use_it():
    tree = ast.parse(Path(sv.__file__).read_text(encoding="utf-8"))
    parents: dict[ast.AST, ast.AST] = {}
    for node in ast.walk(tree):
        for child in ast.iter_child_nodes(node):
            parents[child] = node

    def _enclosing_func(node: ast.AST) -> str | None:
        while node in parents:
            node = parents[node]
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return node.name
        return None

    def _is_eventlog(func: ast.AST) -> bool:
        return (isinstance(func, ast.Name) and func.id == "EventLog") or \
               (isinstance(func, ast.Attribute) and func.attr == "EventLog")

    ctor_calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and _is_eventlog(n.func)]
    assert ctor_calls, "_run_event_log must construct an EventLog"
    for call in ctor_calls:
        assert call.args or call.keywords, f"argument-less EventLog() at line {call.lineno}"
        assert _enclosing_func(call) == "_run_event_log", \
            f"EventLog(...) outside _run_event_log at line {call.lineno}"

    emitted: set[str] = set()
    for n in ast.walk(tree):
        if (isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr == "append"
                and isinstance(n.func.value, ast.Call)
                and isinstance(n.func.value.func, ast.Name) and n.func.value.func.id == "_run_event_log"
                and n.args and isinstance(n.args[0], ast.Constant)):
            emitted.add(n.args[0].value)
    assert {SKIPPED, STOPPED, COMPLETE} <= emitted


# ---------------------------------------------------------------- AC9 (guard)

def test_ac9_flag_catalogued_with_owner_and_provenance():
    from bytedigger_engine import flags_catalog  # noqa: PLC0415

    assert FLAG in flags_catalog.FLAGS, "kill switch must be catalogued"
    entry = flags_catalog.FLAGS[FLAG]
    assert entry.get("kind") == "gate"
    assert entry.get("default") == "1"
    assert isinstance(entry.get("owner"), str) and entry["owner"].strip()
    assert str(entry.get("provenance", "")).startswith("introduced:")


def test_ac9_owner_lint_passes_with_flag_catalogued():
    from bytedigger_engine import flags_catalog  # noqa: PLC0415

    assert FLAG in flags_catalog.FLAGS, "kill switch must be catalogued"
    lint = REPO_ROOT / "scripts" / "flag_owner_lint.py"
    result = subprocess.run([sys.executable, str(lint)], capture_output=True, text=True,
                            cwd=str(REPO_ROOT))
    assert result.returncode == 0, f"{result.stdout!r} {result.stderr!r}"


def test_ac9_changelog_fixed_block_mentions_flag():
    from helpers.changelog import read_changelog, require_entry  # noqa: PLC0415

    require_entry(read_changelog(), FLAG, block="Fixed")
