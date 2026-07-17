"""RED tests for GH923 Lot 3 (ported from hal) — dispatcher report auto-generation.

lib/dispatcher_report.py and config_provider.dispatcher_reports_relpath() do
NOT exist yet (§1q / D1CF5FDF): ALL references to these NEW symbols are
deferred to inside test bodies so this file COLLECTS cleanly and fails at
assert/import/call time, never at collection time.

§1l forcing function: every AC anchors on a real file side-effect (a real
md write or a real JSONL append) inside emit_dispatcher_report /
classify_error_code / collect_run_stats / render_report — none stub-passable.
These four functions ARE the UUT and are never mocked/patched here.

Seam adaptations from the hal source (PORT_SPEC.md):
  - HAL_DISPATCHER_REPORTS_LOG -> BD_DISPATCHER_REPORTS_LOG
  - HAL_DISPATCHER_REPORT_DIR -> BD_DISPATCHER_REPORT_DIR
  - hal_config_provider-specific override test dropped (no hal-host
    provider in bd)
  - HAL_PAUSE_LANE (AC15) kept as-is — not part of the new-in-bd seam list
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from contracts import StepContract, StepResult, WorkflowContext, WorkflowDefinition
from engine import WorkflowEngine


@pytest.fixture(autouse=True)
def _no_ambient_git(tmp_path, monkeypatch):
    """Mirrors test_incident_ledger.py: engine unit tests must not read ambient cwd git state."""
    monkeypatch.chdir(Path(tmp_path).resolve())  # §1j: resolve() — macOS /var/folders symlink


def _make_ctx() -> WorkflowContext:
    return WorkflowContext(
        tenant_id="t",
        scope=None,
        db_path=None,
        org_config={},
        question="q",
        session_id="s",
        persona="p",
        framework=None,
        domain=None,
    )


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


# ─── AC1: classify_error_code — ordered rule table + external-outage marker ──

def test_ac1_classify_error_code_ordered_rule_table():
    from lib.dispatcher_report import classify_error_code  # noqa: PLC0415

    assert classify_error_code("E_LLM_TIMEOUT") == "external", (
        "AC1 FAIL: E_LLM_TIMEOUT must classify external"
    )
    assert classify_error_code("E_LLM_CMD_MISSING") == "infra", (
        "AC1 FAIL: E_LLM_CMD_MISSING must classify infra (exact-infra rule wins over E_LLM_ prefix, order!)"
    )
    assert classify_error_code("E_RESTART_CAP") == "engine", "AC1 FAIL: E_RESTART_CAP must classify engine"
    assert classify_error_code("E_CANARY_NO_MATCH") == "engine", "AC1 FAIL: E_CANARY_NO_MATCH must classify engine"
    assert classify_error_code("E_VALIDATION_FAILED") == "quality-gate", (
        "AC1 FAIL: E_VALIDATION_FAILED must classify quality-gate"
    )
    assert classify_error_code("E_SPEC_LINT_FAIL") == "quality-gate", (
        "AC1 FAIL: E_SPEC_LINT_FAIL must classify quality-gate"
    )
    assert classify_error_code("E_GIT_LOCKED") == "infra", "AC1 FAIL: E_GIT_LOCKED must classify infra"
    assert classify_error_code("E_BAD_CTX") == "unknown", "AC1 FAIL: unrecognized code must classify unknown"
    assert classify_error_code(None) == "unknown", "AC1 FAIL: None error_code must classify unknown"
    assert classify_error_code(
        "E_VALIDATION_FAILED", error="x [external_outage: anthropic-status] y",
    ) == "external", (
        "AC1 FAIL: an embedded '[external_outage:' marker in error must win over any error_code rule"
    )


# ─── AC2: full fixture — timeline/retries/restart/rollup + md + index row ───

def test_ac2_emit_dispatcher_report_full_fixture(tmp_path):
    from lib.dispatcher_report import emit_dispatcher_report  # noqa: PLC0415

    events_path = Path(tmp_path).resolve() / "events.jsonl"
    rows = [
        {"ts": "2026-07-17T00:00:00Z", "run_id": "r1", "event_type": "step_finished",
         "payload": {"step_name": "step_a", "status": "ok", "duration_ms": 5, "error": None}},
        {"ts": "2026-07-17T00:00:01Z", "run_id": "r1", "event_type": "step_finished",
         "payload": {"step_name": "step_b", "status": "ok", "duration_ms": 7, "error": None}},
        {"ts": "2026-07-17T00:00:02Z", "run_id": "r1", "event_type": "step_finished",
         "payload": {"step_name": "step_c", "status": "error", "duration_ms": 9, "error": "boom"}},
        {"ts": "2026-07-17T00:00:03Z", "run_id": "r1", "event_type": "phase_retry_triggered",
         "payload": {"phase_name": "p", "step_name": "step_c", "cycle": 2, "error_code": "E_X", "findings_bytes": 10}},
        {"ts": "2026-07-17T00:00:04Z", "run_id": "r1", "event_type": "phase_retry_triggered",
         "payload": {"phase_name": "p", "step_name": "step_c", "cycle": 3, "error_code": "E_X", "findings_bytes": 12}},
        {"ts": "2026-07-17T00:00:05Z", "run_id": "r1", "event_type": "restart_governor_reason_reset",
         "payload": {"reason": "op restart"}},
        {"ts": "2026-07-17T00:00:06Z", "run_id": "r1", "event_type": "restart_governor_denied",
         "payload": {"deny_code": "E_RESTART_CAP"}},
        {"ts": "2026-07-17T00:00:07Z", "run_id": "r1", "event_type": "workflow_cost_rollup",
         "payload": {"calls": 3, "tokens_in": 100, "tokens_out": 50, "cost_usd": 1.25}},
        # different run — must be filtered out entirely
        {"ts": "2026-07-17T00:00:08Z", "run_id": "r2", "event_type": "step_finished",
         "payload": {"step_name": "other_run_step", "status": "error", "duration_ms": 1, "error": "nope"}},
    ]
    _write_jsonl(events_path, rows)

    report_dir = Path(tmp_path).resolve() / "reports"
    index_path = Path(tmp_path).resolve() / "index.jsonl"

    emit_dispatcher_report(
        run_id="r1", phase="p", status="error", error_code="E_X", error="boom",
        wall_ms=42, events_path=events_path, report_dir=report_dir, index_path=index_path,
    )

    md_files = list(report_dir.glob("*.md"))
    assert len(md_files) == 1, f"AC2 FAIL: expected exactly 1 md file, got {len(md_files)}"

    assert index_path.exists(), "AC2 FAIL: index row was not appended"
    lines = index_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1, f"AC2 FAIL: expected exactly 1 index row, got {len(lines)}"
    row = json.loads(lines[0])

    for key in (
        "ts", "run_id", "phase", "status", "error_code", "classification", "step_name",
        "wall_ms", "retries", "restart_reasons", "cost_usd", "tokens_in", "tokens_out",
        "calls", "report_path", "error",
    ):
        assert key in row, f"AC2 FAIL: schema key {key!r} missing from index row: {row!r}"

    assert row["retries"] == 2, f"AC2 FAIL: expected retries==2, got {row['retries']!r}"
    assert row["restart_reasons"] == ["op restart"], (
        f"AC2 FAIL: expected restart_reasons==['op restart'], got {row['restart_reasons']!r}"
    )
    assert row["cost_usd"] == pytest.approx(1.25), f"AC2 FAIL: expected cost_usd~=1.25, got {row['cost_usd']!r}"
    assert row["tokens_in"] == 100, f"AC2 FAIL: expected tokens_in==100, got {row['tokens_in']!r}"
    assert row["calls"] == 3, f"AC2 FAIL: expected calls==3, got {row['calls']!r}"
    assert row["step_name"] == "step_c", f"AC2 FAIL: expected step_name=='step_c' (the failing step), got {row['step_name']!r}"
    assert row["classification"] == "unknown", f"AC2 FAIL: E_X must classify unknown, got {row['classification']!r}"
    assert row["report_path"] is not None and str(row["report_path"]).endswith(".md"), (
        f"AC2 FAIL: report_path must end with .md, got {row['report_path']!r}"
    )
    assert Path(row["report_path"]).exists(), "AC2 FAIL: report_path must point at a file that exists"
    import os as _os  # noqa: PLC0415
    assert _os.path.isabs(row["report_path"]), (
        f"AC2 FAIL: report_path must be an absolute path, got {row['report_path']!r}"
    )

    md_text = md_files[0].read_text(encoding="utf-8")
    assert "p" in md_text, "AC2 FAIL: md must contain phase"
    assert "E_X" in md_text, "AC2 FAIL: md must contain error_code"
    for step_name in ("step_a", "step_b", "step_c"):
        assert step_name in md_text, f"AC2 FAIL: md must contain timeline step name {step_name!r}"
    assert "2" in md_text, "AC2 FAIL: md must contain retry count 2"
    assert "op restart" in md_text, "AC2 FAIL: md must contain restart reason 'op restart'"
    assert "1.25" in md_text, "AC2 FAIL: md must contain cost_usd 1.25"
    assert "other_run_step" not in md_text, (
        "AC2 FAIL: run-filter leak — the other run's step name must not appear in the rendered md"
    )


# ─── AC3: run_id falsy -> suppress entirely, no exception ───────────────────

def test_ac3_run_id_none_suppresses_write(tmp_path):
    from lib.dispatcher_report import emit_dispatcher_report  # noqa: PLC0415

    report_dir = Path(tmp_path).resolve() / "reports"
    index_path = Path(tmp_path).resolve() / "index.jsonl"

    emit_dispatcher_report(
        run_id=None, phase="p", status="error", error_code="E_X", error="boom",
        wall_ms=1, report_dir=report_dir, index_path=index_path,
    )
    assert not index_path.exists(), "AC3 FAIL: run_id=None must not create the index file"
    assert not report_dir.exists() or not list(report_dir.glob("*.md")), (
        "AC3 FAIL: run_id=None must not create any md report"
    )


def test_ac3_run_id_empty_string_suppresses_write(tmp_path):
    from lib.dispatcher_report import emit_dispatcher_report  # noqa: PLC0415

    report_dir = Path(tmp_path).resolve() / "reports"
    index_path = Path(tmp_path).resolve() / "index.jsonl"

    emit_dispatcher_report(
        run_id="", phase="p", status="error", error_code="E_X", error="boom",
        wall_ms=1, report_dir=report_dir, index_path=index_path,
    )
    assert not index_path.exists(), "AC3 FAIL: run_id='' must not create the index file"
    assert not report_dir.exists() or not list(report_dir.glob("*.md")), (
        "AC3 FAIL: run_id='' must not create any md report"
    )


# ─── AC4: index_path whose parent is an existing FILE -> swallow, no raise ──

def test_ac4_index_parent_is_file_swallows_no_raise(tmp_path):
    from lib.dispatcher_report import emit_dispatcher_report  # noqa: PLC0415

    blocker_file = Path(tmp_path).resolve() / "not_a_dir"
    blocker_file.write_text("i am a file, not a directory", encoding="utf-8")
    bad_index_path = blocker_file / "index.jsonl"  # parent is a FILE -> os.makedirs fails

    result = emit_dispatcher_report(
        run_id="r1", phase="p", status="error", error_code="E_X", error="boom",
        wall_ms=1, index_path=bad_index_path,
    )
    assert result is None, f"AC4 FAIL: emit_dispatcher_report must return None even on a bad index path, got {result!r}"
    assert not bad_index_path.exists(), "AC4 FAIL: no row should be written to an unwritable path"


# ─── AC5: env call-time seams (BD_DISPATCHER_REPORTS_LOG / BD_DISPATCHER_REPORT_DIR) ──

def test_ac5_resolve_dispatcher_index_path_honors_env_at_call_time(tmp_path, monkeypatch):
    from lib.dispatcher_report import resolve_dispatcher_index_path  # noqa: PLC0415

    seam_path = Path(tmp_path).resolve() / "seam" / "dispatcher-reports.jsonl"
    monkeypatch.setenv("BD_DISPATCHER_REPORTS_LOG", str(seam_path))
    result = resolve_dispatcher_index_path()
    assert result == seam_path, (
        f"AC5 FAIL: resolve_dispatcher_index_path() must honor BD_DISPATCHER_REPORTS_LOG at call "
        f"time, expected {seam_path!r}, got {result!r}"
    )


def test_ac5_emit_dispatcher_report_honors_env_index_log_at_call_time(tmp_path, monkeypatch):
    from lib.dispatcher_report import emit_dispatcher_report  # noqa: PLC0415

    seam_path = Path(tmp_path).resolve() / "seam" / "dispatcher-reports.jsonl"
    monkeypatch.setenv("BD_DISPATCHER_REPORTS_LOG", str(seam_path))

    emit_dispatcher_report(
        run_id="r1", phase="p", status="error", error_code="E_X", error="boom", wall_ms=1,
    )
    assert seam_path.exists(), (
        "AC5 FAIL: emit_dispatcher_report without an explicit index_path must resolve "
        "BD_DISPATCHER_REPORTS_LOG at call time and write the row there"
    )


def test_ac5_emit_dispatcher_report_honors_env_report_dir_at_call_time(tmp_path, monkeypatch):
    from lib.dispatcher_report import emit_dispatcher_report  # noqa: PLC0415

    events_path = Path(tmp_path).resolve() / "events.jsonl"
    _write_jsonl(events_path, [])
    env_report_dir = Path(tmp_path).resolve() / "env_reports"
    index_path = Path(tmp_path).resolve() / "index.jsonl"
    monkeypatch.setenv("BD_DISPATCHER_REPORT_DIR", str(env_report_dir))

    emit_dispatcher_report(
        run_id="r1", phase="p", status="error", error_code="E_X", error="boom",
        wall_ms=1, events_path=events_path, index_path=index_path,
    )
    assert env_report_dir.exists(), "AC5 FAIL: BD_DISPATCHER_REPORT_DIR must be honored without an explicit report_dir"
    md_files = list(env_report_dir.glob("*.md"))
    assert len(md_files) == 1, f"AC5 FAIL: expected exactly 1 md file under the env report dir, got {len(md_files)}"


# ─── AC6: config_provider accessor default (neutral, .hal-build seam) ───────

def test_ac6_config_provider_default_dispatcher_reports_relpath():
    import config_provider  # noqa: PLC0415

    try:
        config_provider.reset_default_config_provider_factory()
        fn = getattr(config_provider, "dispatcher_reports_relpath", None)
        assert callable(fn), (
            "AC6 FAIL: config_provider.dispatcher_reports_relpath free function does not "
            "exist yet — GREEN must add it (§2.2)"
        )
        result = fn()
    finally:
        config_provider.reset_default_config_provider_factory()

    assert result == f"{config_provider.foreign_state_dirname()}/dispatcher-reports.jsonl", (
        f"AC6 FAIL: neutral default expected "
        f"'{config_provider.foreign_state_dirname()}/dispatcher-reports.jsonl', got {result!r}"
    )


# not ported: test_ac6_hal_config_provider_override_dispatcher_reports_relpath
# — hal-host provider (hal_config_provider module) absent in bd (PORT_SPEC.md
# "Seam adaptations": drop hal_config_provider-specific override tests).


# ─── AC7: no events_path, no env dir, no report_dir -> index row, no md ─────

def test_ac7_no_events_path_no_dir_index_row_no_md(tmp_path):
    from lib.dispatcher_report import emit_dispatcher_report  # noqa: PLC0415

    index_path = Path(tmp_path).resolve() / "index.jsonl"
    emit_dispatcher_report(
        run_id="r1", phase="p", status="error", error_code="E_X", error="boom",
        wall_ms=1, index_path=index_path,
    )
    assert index_path.exists(), "AC7 FAIL: index row must still be appended with no events_path/report_dir"
    lines = index_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["report_path"] is None, f"AC7 FAIL: report_path must be None, got {row['report_path']!r}"
    assert row["retries"] == 0, f"AC7 FAIL: retries must be 0, got {row['retries']!r}"
    assert row["restart_reasons"] == [], f"AC7 FAIL: restart_reasons must be [], got {row['restart_reasons']!r}"
    assert row["cost_usd"] is None, f"AC7 FAIL: cost_usd must be None, got {row['cost_usd']!r}"


# ─── AC8-AC10: engine wire point (real WorkflowEngine.execute, real step) ───

def _err_step(name: str = "fail_step", error_code: str = "E_T"):
    def _run(_ctx, _prev):
        return StepResult(
            status="error", data=None, duration_ms=0, step_name=name,
            error="boom", error_code=error_code, recoverable=False,
        )
    return StepContract(name=name, execute=_run)


def _ok_step(name: str = "ok_step"):
    def _run(_ctx, _prev):
        return StepResult(status="ok", data="fine", duration_ms=0, step_name=name)
    return StepContract(name=name, execute=_run)


def _escalate_step(name: str = "escalate_step"):
    def _run(_ctx, _prev):
        return StepResult(
            status="escalate", data=None, duration_ms=0, step_name=name,
            error="degraded", error_code="E_ESCALATE",
        )
    return StepContract(name=name, execute=_run)


def test_ac8_engine_error_step_writes_exactly_one_dispatcher_report(tmp_path, monkeypatch):
    # §1l/§1y forcing function: emit_dispatcher_report is the REAL wire point in
    # engine.py §2.4 (post rework-summary, pre-return). This test drives
    # WorkflowEngine.execute end-to-end; it does not call emit_dispatcher_report directly.
    index_path = Path(tmp_path).resolve() / "index.jsonl"
    report_dir = Path(tmp_path).resolve() / "reports"
    monkeypatch.setenv("BD_DISPATCHER_REPORTS_LOG", str(index_path))
    monkeypatch.setenv("BD_DISPATCHER_REPORT_DIR", str(report_dir))

    eng = WorkflowEngine()
    eng.register("wf_err", WorkflowDefinition(name="wf_err", steps=[_err_step("s_err")]))
    result, _ctx = eng.execute("wf_err", _make_ctx(), run_id="run-ac8")

    assert result.status == "error"
    assert index_path.exists(), (
        "AC8 FAIL: WorkflowEngine.execute on a real failing step must have triggered "
        "engine.py's emit_dispatcher_report wire point — index file was not created"
    )
    lines = index_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1, f"AC8 FAIL: expected exactly 1 index row, got {len(lines)}"
    row = json.loads(lines[0])
    assert row["phase"] == "wf_err"
    assert row["error_code"] == "E_T"
    assert row["status"] == "error"
    assert row["wall_ms"] >= 0

    md_files = list(report_dir.glob("*.md")) if report_dir.exists() else []
    assert len(md_files) == 1, f"AC8 FAIL: expected exactly 1 dispatcher md report, got {len(md_files)}"


def test_ac9_engine_ok_step_writes_zero_dispatcher_reports(tmp_path, monkeypatch):
    import importlib  # noqa: PLC0415

    # Presence gate: this is an absence-assertion RED — without it the test
    # would pass vacuously before the wiring exists (nothing writes anything
    # yet either way). Force it to fail pre-GREEN by asserting the wire point
    # is actually present.
    mod = importlib.import_module("lib.dispatcher_report")
    assert hasattr(mod, "emit_dispatcher_report"), (
        "AC9 FAIL (presence gate): lib.dispatcher_report.emit_dispatcher_report does not exist yet"
    )
    import engine as engine_mod  # noqa: PLC0415
    assert getattr(engine_mod, "emit_dispatcher_report", None) is not None, (
        "AC9 FAIL (presence gate): engine.py has not wired in emit_dispatcher_report yet (§2.4)"
    )

    index_path = Path(tmp_path).resolve() / "index.jsonl"
    report_dir = Path(tmp_path).resolve() / "reports"
    monkeypatch.setenv("BD_DISPATCHER_REPORTS_LOG", str(index_path))
    monkeypatch.setenv("BD_DISPATCHER_REPORT_DIR", str(report_dir))

    eng = WorkflowEngine()
    eng.register("wf_ok", WorkflowDefinition(name="wf_ok", steps=[_ok_step("s_ok")]))
    result, _ctx = eng.execute("wf_ok", _make_ctx(), run_id="run-ac9")

    assert result.status == "ok"
    assert not index_path.exists(), "AC9 FAIL: an all-ok run must not produce any index row"
    assert not report_dir.exists() or not list(report_dir.glob("*.md")), (
        "AC9 FAIL: an all-ok run must not produce any dispatcher md"
    )


def test_ac10_engine_escalate_step_writes_one_row_status_escalate(tmp_path, monkeypatch):
    index_path = Path(tmp_path).resolve() / "index.jsonl"
    report_dir = Path(tmp_path).resolve() / "reports"
    monkeypatch.setenv("BD_DISPATCHER_REPORTS_LOG", str(index_path))
    monkeypatch.setenv("BD_DISPATCHER_REPORT_DIR", str(report_dir))

    eng = WorkflowEngine()
    eng.register(
        "wf_escalate",
        WorkflowDefinition(name="wf_escalate", steps=[_escalate_step("s_escalate")]),
    )
    result, _ctx = eng.execute("wf_escalate", _make_ctx(), run_id="run-ac10")

    assert result.status == "escalate"
    assert index_path.exists(), "AC10 FAIL: an escalate-status run must produce a dispatcher index row"
    lines = index_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1, f"AC10 FAIL: expected exactly 1 row, got {len(lines)}"
    row = json.loads(lines[0])
    assert row["status"] == "escalate", f"AC10 FAIL: expected status=='escalate', got {row['status']!r}"


# ─── AC11: malformed events file -> no raise, row written, cost_usd None ────

def test_ac11_malformed_events_file_no_raise_row_written_cost_none(tmp_path):
    from lib.dispatcher_report import emit_dispatcher_report  # noqa: PLC0415

    events_path = Path(tmp_path).resolve() / "events.jsonl"
    with open(events_path, "wb") as fh:
        fh.write(b"\x00\x01\xff not valid json at all\n")
        fh.write((json.dumps({
            "ts": "2026-07-17T00:00:00Z", "run_id": "r1", "event_type": "step_finished",
            "payload": {"step_name": "s", "status": "error", "duration_ms": 1, "error": "boom"},
        }) + "\n").encode("utf-8"))

    index_path = Path(tmp_path).resolve() / "index.jsonl"
    # Must not raise.
    emit_dispatcher_report(
        run_id="r1", phase="p", status="error", error_code="E_X", error="boom",
        wall_ms=1, events_path=events_path, index_path=index_path,
    )
    assert index_path.exists(), "AC11 FAIL: row must still be written despite malformed events file"
    lines = index_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1, f"AC11 FAIL: expected exactly 1 row, got {len(lines)}"
    row = json.loads(lines[0])
    assert row["cost_usd"] is None, f"AC11 FAIL: cost_usd must be None when no rollup row parses, got {row['cost_usd']!r}"

    # No report_dir param and no BD_DISPATCHER_REPORT_DIR env in this test ->
    # the md must default under events_path.parent / "dispatcher" (§2.1d).
    default_report_dir = events_path.parent / "dispatcher"
    md_files = list(default_report_dir.glob("*.md")) if default_report_dir.exists() else []
    assert len(md_files) == 1, (
        f"AC11 FAIL: expected exactly 1 md file under the default dir {default_report_dir!r}, "
        f"got {len(md_files)}"
    )
    assert row["report_path"] is not None, "AC11 FAIL: report_path must not be None when events_path is given"
    assert Path(row["report_path"]).exists(), "AC11 FAIL: report_path must point at a file that exists"
    assert Path(row["report_path"]).parent == default_report_dir, (
        f"AC11 FAIL: report_path must live under the default dispatcher dir, got {row['report_path']!r}"
    )


# ─── AC16: report_dir whose parent is a FILE -> md fails, index row unaffected ─

def test_ac16_report_dir_parent_is_file_index_row_still_written(tmp_path):
    from lib.dispatcher_report import emit_dispatcher_report  # noqa: PLC0415

    events_path = Path(tmp_path).resolve() / "events.jsonl"
    _write_jsonl(events_path, [])

    blocker_file = Path(tmp_path).resolve() / "not_a_dir"
    blocker_file.write_text("i am a file, not a directory", encoding="utf-8")
    bad_report_dir = blocker_file / "reports"  # parent is a FILE -> os.makedirs/write fails

    index_path = Path(tmp_path).resolve() / "index.jsonl"

    # Must not raise despite the unwritable report_dir.
    emit_dispatcher_report(
        run_id="r1", phase="p", status="error", error_code="E_X", error="boom",
        wall_ms=1, events_path=events_path, report_dir=bad_report_dir, index_path=index_path,
    )
    assert index_path.exists(), "AC16 FAIL: index row must still be appended when the md write fails"
    lines = index_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1, f"AC16 FAIL: expected exactly 1 row, got {len(lines)}"
    row = json.loads(lines[0])
    assert row["report_path"] is None, (
        f"AC16 FAIL: report_path must be None when the md write failed, got {row['report_path']!r}"
    )


# ─── AC17: 100k-char error -> truncated to <=500 chars, row still written ───

def test_ac17_very_long_error_truncated_row_written(tmp_path):
    from lib.dispatcher_report import emit_dispatcher_report  # noqa: PLC0415

    index_path = Path(tmp_path).resolve() / "index.jsonl"
    long_error = "x" * 100_000
    emit_dispatcher_report(
        run_id="r1", phase="p", status="error", error_code="E_X", error=long_error,
        wall_ms=1, index_path=index_path,
    )
    assert index_path.exists(), "AC17 FAIL: row must be written despite the very long error"
    lines = index_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1, f"AC17 FAIL: expected exactly 1 row, got {len(lines)}"
    row = json.loads(lines[0])
    assert row["error"] is not None
    assert len(row["error"]) <= 500, (
        f"AC17 FAIL: error must be truncated to <=500 chars, got {len(row['error'])}"
    )


# ─── AC12: filename sanitization + oversized row skip ───────────────────────

def test_ac12_filename_sanitized_no_slash_or_space(tmp_path):
    from lib.dispatcher_report import emit_dispatcher_report  # noqa: PLC0415

    report_dir = Path(tmp_path).resolve() / "reports"
    index_path = Path(tmp_path).resolve() / "index.jsonl"
    events_path = Path(tmp_path).resolve() / "events.jsonl"
    _write_jsonl(events_path, [])

    emit_dispatcher_report(
        run_id="r1", phase="p/x x", status="error", error_code=None, error=None,
        wall_ms=1, events_path=events_path, report_dir=report_dir, index_path=index_path,
    )
    md_files = list(report_dir.glob("*.md"))
    assert len(md_files) == 1, f"AC12 FAIL: expected exactly 1 md file, got {len(md_files)}"
    basename = md_files[0].name
    assert "/" not in basename, f"AC12 FAIL: sanitized filename must not contain '/', got {basename!r}"
    assert " " not in basename, f"AC12 FAIL: sanitized filename must not contain a space, got {basename!r}"
    assert "NONE" in basename, f"AC12 FAIL: a None error_code must slug to 'NONE' in the filename, got {basename!r}"


def test_ac12_oversized_row_skipped_index_file_unchanged(tmp_path):
    from lib.dispatcher_report import emit_dispatcher_report  # noqa: PLC0415

    index_path = Path(tmp_path).resolve() / "index.jsonl"
    preexisting = json.dumps({"ts": "pre", "run_id": "prior"}) + "\n"
    index_path.write_text(preexisting, encoding="utf-8")

    huge_phase = "p" * 20_000  # forces the serialized row well over 4096 bytes
    emit_dispatcher_report(
        run_id="r1", phase=huge_phase, status="error", error_code="E_X", error="boom",
        wall_ms=1, index_path=index_path,
    )
    assert index_path.read_text(encoding="utf-8") == preexisting, (
        "AC12 FAIL: an oversized row (>4096 bytes) must be skipped entirely — "
        "the pre-existing index file content must be unchanged"
    )


# ─── AC13: error=None must not be dropped ────────────────────────────────────

def test_ac13_error_none_still_writes_row(tmp_path):
    from lib.dispatcher_report import emit_dispatcher_report  # noqa: PLC0415

    index_path = Path(tmp_path).resolve() / "index.jsonl"
    emit_dispatcher_report(
        run_id="r1", phase="p", status="error", error_code="E_X", error=None,
        wall_ms=1, index_path=index_path,
    )
    assert index_path.exists(), "AC13 FAIL: a None-error dispatch must still write a row"
    lines = index_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["error"] is None, f"AC13 FAIL: expected error is None, got {row['error']!r}"


# ─── AC14: §1ab zombie guard — non-authoritative execution -> 0 rows ────────

def test_ac14_non_authoritative_execution_writes_zero_rows(tmp_path, monkeypatch):
    import engine as engine_module  # noqa: PLC0415

    # Presence gate: absence-assertion RED — force a pre-GREEN fail by
    # asserting the wire point is actually present (else this would pass
    # vacuously, since nothing is wired yet either way).
    assert getattr(engine_module, "emit_dispatcher_report", None) is not None, (
        "AC14 FAIL (presence gate): engine.py has not wired in emit_dispatcher_report yet (§2.4)"
    )

    index_path = Path(tmp_path).resolve() / "index.jsonl"
    monkeypatch.setenv("BD_DISPATCHER_REPORTS_LOG", str(index_path))
    monkeypatch.setattr(engine_module, "is_authoritative_execution", lambda: False)

    eng = engine_module.WorkflowEngine()
    eng.register("wf_zombie", WorkflowDefinition(name="wf_zombie", steps=[_err_step("s_zombie")]))
    result, _ctx = eng.execute("wf_zombie", _make_ctx(), run_id="run-ac14")

    assert result.status == "error"
    assert not index_path.exists(), (
        "AC14 FAIL: a non-authoritative (zombie) execution must not emit a dispatcher report row"
    )


# ─── AC15: paused lane — E_LLM_SPEND_LIMIT status remapped, no dispatch ─────

def test_ac15_paused_lane_spend_limit_writes_zero_rows(tmp_path, monkeypatch):
    import engine as engine_module  # noqa: PLC0415

    # Presence gate: absence-assertion RED — force a pre-GREEN fail by
    # asserting the wire point is actually present (else this would pass
    # vacuously, since nothing is wired yet either way).
    assert getattr(engine_module, "emit_dispatcher_report", None) is not None, (
        "AC15 FAIL (presence gate): engine.py has not wired in emit_dispatcher_report yet (§2.4)"
    )

    index_path = Path(tmp_path).resolve() / "index.jsonl"
    monkeypatch.setenv("BD_DISPATCHER_REPORTS_LOG", str(index_path))
    # HAL_PAUSE_LANE default ON — do not set it, matching spec.

    eng = WorkflowEngine()
    eng.register(
        "wf_paused",
        WorkflowDefinition(name="wf_paused", steps=[_err_step("s_paused", error_code="E_LLM_SPEND_LIMIT")]),
    )
    result, _ctx = eng.execute("wf_paused", _make_ctx(), run_id="run-ac15")

    assert result.status == "error"  # StepResult.status vocabulary unchanged (§1n)
    assert not index_path.exists(), (
        "AC15 FAIL: the paused-lane remap (status=='paused' at emit time) must exclude "
        "the run from the dispatcher-report trigger — 0 index rows expected"
    )
