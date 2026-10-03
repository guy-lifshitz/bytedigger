"""bd#89 P3c RED (spec FROZEN r1.2 + Errata r2, section 10).

Spec: docs/decisions/2026-10-03-bd89-p3c-deterministic-synthesize-report.md (section 3, AC1-AC16;
section 10 changes AC12/AC13 and adds AC17-AC22: the same step also derives
``reviews/learnings-raw.md`` from the review doc and the event log).

Phase 7 writes ``post-deploy/post-deploy-report.md`` from the event log with no LLM:
one step ``write_post_deploy_report``; the synthesizer steps, the ``E_SYNTHESIZER_*``
codes, the ``synthesize.llm`` timeout key, ``SynthesizerVerdict``, the synthesizer
agent and the ``learnings-raw.md`` deliverable gate are all gone.

AC mapping:
    test_ac1_one_step_workflow_registered                      -> AC1
    test_ac2_report_written_with_all_docs_present              -> AC2 (side effect)
    test_ac3_missing_docs_degrade_to_not_assessed              -> AC3
    test_ac4_not_assessed_satisfaction_reaches_report          -> AC4
    test_ac5_step_needs_no_model_and_no_tool                   -> AC5
    test_ac5b_module_imports_no_llm_or_subprocess              -> AC5
    test_ac6_completed_phases_from_event_log                   -> AC6
    test_ac6b_telemetry_section_only_when_opted_in             -> AC6
    test_ac7_report_feeds_phase8_summary_contract              -> AC7
    test_ac7b_empty_request_has_no_done_line                   -> AC7
    test_ac7c_none_request_has_no_done_line                    -> AC7
    test_ac8_no_scratchpad_degrades_to_skipped_event           -> AC8
    test_ac8b_write_failure_degrades_to_skipped_event          -> AC8
    test_ac8c_lone_surrogate_request_still_writes_report       -> AC8
    test_ac8d_render_failure_degrades_to_render_failed_event   -> AC8 (r1.2 F6)
    test_ac9_retired_org_keys_are_ignored_with_one_event       -> AC9
    test_ac9b_ignored_keys_event_also_fires_without_scratchpad -> AC9
    test_ac10_retired_error_codes_are_gone                     -> AC10
    test_ac11_retired_timeout_key_and_schema_are_gone          -> AC11
    test_ac11b_phase_7_module_has_no_retired_symbols           -> AC11
    test_ac12_synthesizer_agent_removed_extraction_step_kept   -> AC12 (r2)
    test_ac12b_guard_phase_7_md_keeps_cleanup_and_ship         -> AC12 (GUARD)
    test_ac13_guard_tsv_row_kept_and_bash_gate_still_demands_raw -> AC13 (r2 GUARD)
    test_ac17_derived_learnings_raw_parses_with_expected_entries -> AC17 (r2)
    test_ac18_review_finding_entries_capped_at_ten_in_file_order -> AC18 (r2)
    test_ac19_nothing_derivable_writes_not_derived_file        -> AC19 (r2)
    test_ac20_fix_process_and_acceptance_entries               -> AC20 (r2)
    test_ac21_learning_store_extract_stores_derived_entries    -> AC21 (r2)
    test_ac22_learnings_write_failure_degrades                 -> AC22 (r2)
    test_ac22b_learnings_render_failure_degrades               -> AC22 (r2)
    test_ac22c_report_write_failure_does_not_skip_learnings    -> AC22c (r2.1, op9 independence)
    test_ac23a_refuted_section_skipped_and_unverified_token_kept -> AC23a (r2.1)
    test_ac23b_latest_review_findings_audit_event_wins         -> AC23b (r2.1)
    test_ac23c_missing_review_doc_still_yields_event_derived_entries -> AC23c (r2.1)
    test_ac12c_phase_7_sections_do_not_mention_haiku           -> AC12 (r2.1 F8)
    test_ac14_guard_extract_without_raw_degrades_to_zero       -> AC14 (GUARD, both backends)
    test_ac15_guard_class_i_lint_compileall_config_parses      -> AC15 (GUARD)
    test_ac15_guard_tree_scan_lint_real_tree_clean             -> AC15 (GUARD)
    test_ac15b_anti_hallucination_config_drops_phase_7         -> AC15 (config key part, red)
    test_ac16_guard_gh1124_pr_title_suite_present              -> AC16 (GUARD)

The retired step is reached through the workflow definition by name, so a missing
step fails that test only (and, unlike an engine run, never reaches a real model).
Telemetry goes to a real ``EventLog`` on ``tmp_path``; ``derive_state.default_log_path``
is pointed at the same file so ``query_run_events`` reads what the test wrote.
No ``sys.path`` manipulation; no import from conftest.
"""
from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from bytedigger_engine import derive_state as ds
from bytedigger_engine import error_codes, telemetry_ctx, workflows
from bytedigger_engine.contracts import StepResult, WorkflowContext
from bytedigger_engine.engine import WorkflowEngine
from bytedigger_engine.event_log import EventLog
from bytedigger_engine.workflows import phase_7_synthesize as p7

ENGINE_PY = Path(__file__).resolve().parents[1]
ENGINE_PKG = ENGINE_PY / "bytedigger_engine"
REPO_ROOT = ENGINE_PY.parent

STEP = "write_post_deploy_report"
REPORT_REL = "post-deploy/post-deploy-report.md"
SPEC_REL = "specs/build-spec.md"
REVIEW_REL = "reviews/build-review.md"
FIX_REL = "reviews/build-fix.md"
SAT_REL = "reviews/build-satisfaction.md"


# --- fixtures / helpers --------------------------------------------------------

@pytest.fixture(autouse=True)
def _clean_telemetry_ctx():
    telemetry_ctx.clear_current_run()
    yield
    telemetry_ctx.clear_current_run()


def _ctx(scratch: Path | None, question: str = "Add retry to the sync job\nmore detail",
         **org_extra) -> WorkflowContext:
    org: dict = {}
    if scratch is not None:
        org["scratchpad_dir"] = str(scratch)
    org.update(org_extra)
    return WorkflowContext(
        tenant_id="hal", scope=None, db_path=None, org_config=org,
        question=question, session_id="test-bd89-p3c", persona="hal",
        framework=None, domain=None,
    )


def _write(scratch: Path, rel: str, body: str) -> None:
    p = scratch / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


def _seed_all(scratch: Path) -> None:
    _write(scratch, SPEC_REL, "## US1\nAdd retry\n")
    _write(scratch, REVIEW_REL, "Composite review.\nVERDICT: PASS\n")
    _write(scratch, FIX_REL, "FIX SKIPPED - no findings\n")
    _write(scratch, SAT_REL, "SCORE: 95\nVERDICT: PASS\n")


def _make_log(tmp_path: Path, monkeypatch) -> EventLog:
    path = tmp_path / "build-events.jsonl"
    log = EventLog(path)
    monkeypatch.setattr(ds, "default_log_path", lambda: path, raising=False)
    return log


def _finished(log: EventLog, phase: str, wall_ms: int = 1000) -> None:
    log.append("workflow_finished",
               {"workflow_name": phase, "status": "ok", "wall_ms": wall_ms}, f"r-{phase}")


def _events(log: EventLog, event_type: str) -> list[dict]:
    return [e for e in log.read_all() if e["event_type"] == event_type]


def _run_step(ctx: WorkflowContext, log: EventLog) -> StepResult:
    wf = p7.phase_7_synthesize_workflow()
    step = next((s for s in wf.steps if s.name == STEP), None)
    assert step is not None, (
        f"phase_7_synthesize has no step {STEP!r}; steps are {[s.name for s in wf.steps]}"
    )
    telemetry_ctx.set_current_run(
        event_log=log, run_id="rid-p7", step_name=STEP, phase="phase_7_synthesize",
    )
    try:
        return step.execute(ctx, None)
    finally:
        telemetry_ctx.clear_current_run()


def _section(text: str, heading: str) -> str:
    m = re.search(rf"^## {re.escape(heading)}[^\n]*\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    assert m, f"report has no '## {heading}' section:\n{text}"
    return m.group(1)


def _bullets(section: str) -> list[str]:
    return [ln for ln in section.splitlines() if ln.lstrip().startswith("- ")]


# --- AC1 -----------------------------------------------------------------------

def test_ac1_one_step_workflow_registered():
    wf = p7.phase_7_synthesize_workflow()
    assert wf.name == "phase_7_synthesize"
    assert [s.name for s in wf.steps] == [STEP]
    eng = WorkflowEngine()
    workflows.register_all(eng)
    assert "phase_7_synthesize" in eng.registered()


# --- AC2 -----------------------------------------------------------------------

def test_ac2_report_written_with_all_docs_present(tmp_path, monkeypatch):
    log = _make_log(tmp_path, monkeypatch)
    scratch = tmp_path / "scratch"
    _seed_all(scratch)
    result = _run_step(_ctx(scratch), log)

    report = scratch / REPORT_REL
    assert report.is_file()
    text = report.read_text(encoding="utf-8")
    assert text.startswith("# Post-Deploy Report")
    assert "## Final Checkpoint" in text
    assert "Done: Add retry to the sync job" in text
    assert "more detail" not in text.split("## Final Checkpoint")[1].split("##")[0]
    arts = _section(text, "Artifacts")
    for name in ("spec", "review", "fix", "satisfaction"):
        assert f"- {name}: PRESENT" in arts
    assert re.search(r"^## Concerns\s*\n+\s*(?:-\s*)?none\b", text, re.M), text
    assert result.status == "ok"
    assert result.error_code is None
    assert result.data["report_written"] is True
    assert result.data["report_bytes_written"] == len(report.read_bytes())
    assert result.data["artifact_states"] == {
        "spec": "PRESENT", "review": "PRESENT", "fix": "PRESENT", "satisfaction": "PRESENT"}
    written = _events(log, "synthesize_report_written")
    assert len(written) == 1
    assert written[0]["payload"]["report_bytes"] == len(report.read_bytes())
    assert written[0]["payload"]["gaps"] == []
    assert written[0]["payload"]["doc_path"] == str(report)


# --- AC3 -----------------------------------------------------------------------

def test_ac3_missing_docs_degrade_to_not_assessed(tmp_path, monkeypatch):
    log = _make_log(tmp_path, monkeypatch)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    result = _run_step(_ctx(scratch), log)

    assert result.status == "ok"
    assert result.error_code is None
    text = (scratch / REPORT_REL).read_text(encoding="utf-8")
    assert "not assessed" in text
    arts = _section(text, "Artifacts")
    for name in ("spec", "review", "fix", "satisfaction"):
        assert f"- {name}: MISSING" in arts
    concerns = _bullets(_section(text, "Concerns"))
    assert len(concerns) == 4, concerns
    assert all("not assessed" in c for c in concerns)
    assert result.data["artifact_states"] == {
        "spec": "MISSING", "review": "MISSING", "fix": "MISSING", "satisfaction": "MISSING"}
    written = _events(log, "synthesize_report_written")
    assert len(written) == 1
    assert sorted(written[0]["payload"]["gaps"]) == ["fix", "review", "satisfaction", "spec"]


# --- AC4 -----------------------------------------------------------------------

def test_ac4_not_assessed_satisfaction_reaches_report(tmp_path, monkeypatch):
    log = _make_log(tmp_path, monkeypatch)
    scratch = tmp_path / "scratch"
    _seed_all(scratch)
    _write(scratch, SAT_REL, "SATISFACTION: NOT_ASSESSED\nSCORE: 8731\n")
    _run_step(_ctx(scratch), log)

    text = (scratch / REPORT_REL).read_text(encoding="utf-8")
    assert "- satisfaction: NOT_ASSESSED" in _section(text, "Artifacts")
    assert "- satisfaction: PRESENT" not in text
    review_line = next(ln for ln in text.splitlines() if ln.startswith("Review:"))
    assert "satisfaction: NOT_ASSESSED" in review_line
    concern = [c for c in _bullets(_section(text, "Concerns")) if "build-satisfaction.md" in c]
    assert concern and "not assessed" in concern[0]
    assert "8731" not in text


# --- AC5 -----------------------------------------------------------------------

def test_ac5_step_needs_no_model_and_no_tool(tmp_path, monkeypatch):
    log = _make_log(tmp_path, monkeypatch)
    scratch = tmp_path / "scratch"
    _seed_all(scratch)

    calls: list[str] = []

    def _boom(*a, **kw):
        calls.append("called")
        raise AssertionError("model or tool invoked by the deterministic step")

    import bytedigger_engine.llm_subprocess as llm_mod
    monkeypatch.setattr(llm_mod, "invoke_llm_subprocess", _boom)
    monkeypatch.setattr(p7, "invoke_llm_subprocess", _boom, raising=False)
    monkeypatch.setattr(subprocess, "run", _boom)
    monkeypatch.setattr(subprocess, "Popen", _boom)
    monkeypatch.setattr(os, "system", _boom)

    result = _run_step(_ctx(scratch), log)
    assert result.status == "ok"
    assert (scratch / REPORT_REL).is_file()
    # A broad `except` in the step must not be able to hide a model or tool call.
    assert calls == []


def test_ac5b_module_imports_no_llm_or_subprocess():
    tree = ast.parse(Path(p7.__file__).read_text(encoding="utf-8"))
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported += [a.name for a in node.names]
        elif isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
            imported += [a.name for a in node.names]
    offenders = [n for n in imported if "llm_subprocess" in n or n.split(".")[0] == "subprocess"]
    assert offenders == []


# --- AC6 -----------------------------------------------------------------------

def test_ac6_completed_phases_from_event_log(tmp_path, monkeypatch):
    log = _make_log(tmp_path, monkeypatch)
    _finished(log, "phase_5_implement")
    _finished(log, "phase_6_review")
    _finished(log, "phase_6_review")
    _finished(log, "phase_7_synthesize")
    scratch = tmp_path / "scratch"
    _seed_all(scratch)
    _run_step(_ctx(scratch), log)

    text = (scratch / REPORT_REL).read_text(encoding="utf-8")
    phases = [b.strip()[2:].strip() for b in _bullets(_section(text, "Completed Phases"))]
    assert phases == ["phase_5_implement", "phase_6_review"]
    assert "## Telemetry" not in text


def test_ac6b_telemetry_section_only_when_opted_in(tmp_path, monkeypatch):
    log = _make_log(tmp_path, monkeypatch)
    _finished(log, "phase_5_implement", wall_ms=20000)
    log.append("subprocess_exited",
               {"phase": "phase_5_implement", "model": "sonnet", "cost_usd": 0.12, "pid": 1}, "r-sub")

    on = tmp_path / "scratch_on"
    _seed_all(on)
    _run_step(_ctx(on, include_telemetry_digest=True), log)
    text_on = (on / REPORT_REL).read_text(encoding="utf-8")
    tele = _section(text_on, "Telemetry")
    assert "phase_5_implement" in tele
    assert "0.12" in tele

    off = tmp_path / "scratch_off"
    _seed_all(off)
    _run_step(_ctx(off), log)
    assert "## Telemetry" not in (off / REPORT_REL).read_text(encoding="utf-8")


# --- AC7 -----------------------------------------------------------------------

def test_ac7_report_feeds_phase8_summary_contract(tmp_path, monkeypatch):
    from bytedigger_engine.workflows.phase_8_post_deploy import _parse_report_summary

    log = _make_log(tmp_path, monkeypatch)
    scratch = tmp_path / "scratch"
    _seed_all(scratch)
    long_line = "A" + "b" * 139
    assert len(long_line) == 140
    _run_step(_ctx(scratch, question=f"\n\n{long_line}\nsecond line"), log)
    text = (scratch / REPORT_REL).read_text(encoding="utf-8")
    assert _parse_report_summary(text) == long_line[:100]

    short = tmp_path / "scratch_short"
    _seed_all(short)
    _run_step(_ctx(short, question="Add retry to the sync job\nmore detail"), log)
    assert _parse_report_summary((short / REPORT_REL).read_text(encoding="utf-8")) == \
        "Add retry to the sync job"


def test_ac7b_empty_request_has_no_done_line(tmp_path, monkeypatch):
    from bytedigger_engine.workflows.phase_8_post_deploy import _parse_report_summary

    log = _make_log(tmp_path, monkeypatch)
    scratch = tmp_path / "scratch"
    _seed_all(scratch)
    _run_step(_ctx(scratch, question="  \n \n"), log)
    text = (scratch / REPORT_REL).read_text(encoding="utf-8")
    assert not re.search(r"^\s*Done:", text, re.M)
    assert _parse_report_summary(text) == ""


def test_ac7c_none_request_has_no_done_line(tmp_path, monkeypatch):
    from bytedigger_engine.workflows.phase_8_post_deploy import _parse_report_summary

    log = _make_log(tmp_path, monkeypatch)
    scratch = tmp_path / "scratch"
    _seed_all(scratch)
    result = _run_step(_ctx(scratch, question=None), log)
    assert result.status == "ok"
    assert result.data["report_written"] is True
    text = (scratch / REPORT_REL).read_text(encoding="utf-8")
    assert not re.search(r"^\s*Done:", text, re.M)
    assert _parse_report_summary(text) == ""


# --- AC8 -----------------------------------------------------------------------

def test_ac8c_lone_surrogate_request_still_writes_report(tmp_path, monkeypatch):
    log = _make_log(tmp_path, monkeypatch)
    scratch = tmp_path / "scratch"
    _seed_all(scratch)
    result = _run_step(_ctx(scratch, question="\ud800x second"), log)
    assert result.status == "ok"
    assert result.error_code is None
    assert result.data["report_written"] is True
    report = scratch / REPORT_REL
    text = report.read_bytes().decode("utf-8")  # strict: the file must be valid UTF-8
    assert text.startswith("# Post-Deploy Report")
    assert _events(log, "post_deploy_report_skipped") == []


def test_ac8_no_scratchpad_degrades_to_skipped_event(tmp_path, monkeypatch):
    log = _make_log(tmp_path, monkeypatch)
    result = _run_step(_ctx(None), log)
    assert result.status == "ok"
    assert result.error_code is None
    assert result.data["report_written"] is False
    assert "report_bytes_written" not in result.data
    skipped = _events(log, "post_deploy_report_skipped")
    assert len(skipped) == 1
    assert skipped[0]["payload"]["reason"] == "no_scratchpad"
    # r2.1 F1: op9 does not run without a scratchpad; the keys are present on every branch.
    assert result.data["learnings_written"] is False
    assert result.data["learnings_entries"] == 0
    assert result.data["learnings_raw_path"] is None
    assert [e for e in log.read_all() if e["event_type"].startswith("learnings_raw")] == []


def test_ac8b_write_failure_degrades_to_skipped_event(tmp_path, monkeypatch):
    log = _make_log(tmp_path, monkeypatch)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    (scratch / "post-deploy").write_text("i am a file, not a directory", encoding="utf-8")
    result = _run_step(_ctx(scratch), log)
    assert result.status == "ok"
    assert result.error_code is None
    assert result.data["report_written"] is False
    skipped = _events(log, "post_deploy_report_skipped")
    assert len(skipped) == 1
    assert skipped[0]["payload"]["reason"] == "write_failed"


def test_ac8d_render_failure_degrades_to_render_failed_event(tmp_path, monkeypatch):
    log = _make_log(tmp_path, monkeypatch)
    scratch = tmp_path / "scratch"
    _seed_all(scratch)

    def _raise(*a, **kw):
        raise RuntimeError("injected collaborator fault")

    # Collaborator fault injection (not a mock of the unit under test).
    monkeypatch.setattr(p7, "_collect_completed_phases", _raise)
    result = _run_step(_ctx(scratch), log)
    assert result.status == "ok"
    assert result.error_code is None
    assert result.data["report_written"] is False
    assert not (scratch / REPORT_REL).exists()
    skipped = _events(log, "post_deploy_report_skipped")
    assert len(skipped) == 1
    assert skipped[0]["payload"]["reason"] == "render_failed"


# --- AC9 -----------------------------------------------------------------------

def test_ac9_retired_org_keys_are_ignored_with_one_event(tmp_path, monkeypatch):
    log = _make_log(tmp_path, monkeypatch)
    scratch = tmp_path / "scratch"
    _seed_all(scratch)
    result = _run_step(
        _ctx(scratch, synthesizer_model="x", synthesizer_llm_command=["y"]), log)
    assert result.status == "ok"
    ignored = _events(log, "synthesizer_config_ignored")
    assert len(ignored) == 1
    assert ignored[0]["payload"]["keys"] == ["synthesizer_llm_command", "synthesizer_model"]

    scratch2 = tmp_path / "scratch2"
    _seed_all(scratch2)
    before = len(_events(log, "synthesizer_config_ignored"))
    _run_step(_ctx(scratch2), log)
    assert len(_events(log, "synthesizer_config_ignored")) == before


def test_ac9b_ignored_keys_event_also_fires_without_scratchpad(tmp_path, monkeypatch):
    log = _make_log(tmp_path, monkeypatch)
    result = _run_step(_ctx(None, synthesizer_model="x"), log)
    assert result.status == "ok"
    assert result.data["report_written"] is False
    ignored = _events(log, "synthesizer_config_ignored")
    assert len(ignored) == 1
    assert ignored[0]["payload"]["keys"] == ["synthesizer_model"]
    assert len(_events(log, "post_deploy_report_skipped")) == 1


# --- AC10 ----------------------------------------------------------------------

_RETIRED_CODES = ("E_SYNTHESIZER_BLOCKED", "E_SYNTHESIZER_NEEDS_CONTEXT", "E_SYNTHESIZER_NO_MARKER")


def test_ac10_retired_error_codes_are_gone():
    for code in _RETIRED_CODES:
        assert code not in error_codes.ERROR_CODES
    mds = (ENGINE_PY / "ERROR_CODES.md", ENGINE_PKG / "ERROR_CODES.md")
    for md in mds:
        body = md.read_text(encoding="utf-8")
        for code in _RETIRED_CODES:
            assert code not in body, f"{code} still in {md}"
    assert mds[0].read_bytes() == mds[1].read_bytes()
    proc = subprocess.run(
        [sys.executable, "-m", "bytedigger_engine.error_codes", "--check"],
        cwd=str(ENGINE_PY), capture_output=True, text=True, timeout=180,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


# --- AC11 ----------------------------------------------------------------------

def test_ac11_retired_timeout_key_and_schema_are_gone():
    from bytedigger_engine.lib.timeout_policy import DEFAULT_POLICY
    import bytedigger_engine.lib.plugins.disk_truth as dt

    assert "synthesize.llm" not in DEFAULT_POLICY
    assert not hasattr(dt, "SynthesizerVerdict")
    schema_src = (ENGINE_PKG / "lib" / "plugins" / "disk_truth" / "schema.py").read_text(encoding="utf-8")
    assert "SynthesizerVerdict" not in schema_src


def test_ac11b_phase_7_module_has_no_retired_symbols():
    src = Path(p7.__file__).read_text(encoding="utf-8")
    for token in ("_build_synthesizer_prompt", "_invoke_synthesizer_llm",
                  "_write_synthesizer_artifact", "E_SYNTHESIZER", "SYNTHESIZER_STABLE_PREFIX"):
        assert token not in src, token
    for attr in ("DEFAULT_LLM_COMMAND", "DEFAULT_SYNTHESIZER_TIMEOUT_SEC", "_parse_synthesizer_status",
                 "_parse_synthesizer_structured", "STATUS_DONE", "STATUS_BLOCKED"):
        assert not hasattr(p7, attr), attr


# --- AC12 ----------------------------------------------------------------------

def test_ac12_synthesizer_agent_removed_extraction_step_kept():
    # r2: the agent and the Haiku launch go; the extraction step stays (its producer is op9).
    assert not (REPO_ROOT / "agents" / "synthesizer.md").exists()
    for rel in ("phases/phase-7-synthesize.md", "commands/build.md"):
        body = (REPO_ROOT / rel).read_text(encoding="utf-8")
        for token in ("agents/synthesizer.md", "Launch Haiku"):
            assert token not in body, f"{token!r} still in {rel}"
        assert "learning-store.sh extract" in body, f"extraction step missing from {rel}"


def _phase_7_text(rel: str) -> str:
    body = (REPO_ROOT / rel).read_text(encoding="utf-8")
    if rel == "phases/phase-7-synthesize.md":
        return body  # the whole file is the phase 7 flow
    start = body.index("## PHASE 7: SYNTHESIZE")
    end = body.index("## PHASE 8: POST-DEPLOY")
    assert start < end
    return body[start:end]


@pytest.mark.parametrize("rel", ["phases/phase-7-synthesize.md", "commands/build.md"])
def test_ac12c_phase_7_sections_do_not_mention_haiku(rel):
    # r2.1 F8: the engine writes the report deterministically (no model).
    section = _phase_7_text(rel)
    assert "learning-store.sh extract" in section, f"phase 7 section of {rel} lost the extraction step"
    assert "Haiku" not in section, f"'Haiku' still in the phase 7 section of {rel}"


def test_ac12b_guard_phase_7_md_keeps_cleanup_and_ship():
    body = (REPO_ROOT / "phases" / "phase-7-synthesize.md").read_text(encoding="utf-8")
    assert "## State Cleanup" in body
    assert "## 7.5 SHIP Protocol" in body


# --- AC13 ----------------------------------------------------------------------

def _gate_fixture(tmp_path: Path, extra_state: str) -> Path:
    scratch = tmp_path / "scratch"
    for d in ("research", "architecture", "reviews"):
        (scratch / d).mkdir(parents=True, exist_ok=True)
    (tmp_path / "bytedigger.json").write_text(
        json.dumps({"gates_enabled": True, "tdd_mandatory": True}))
    (tmp_path / "build-state.yaml").write_text(
        'task: "t"\n'
        "complexity: FEATURE\n"
        "mode: AUTONOMOUS\n"
        'current_phase: "7"\n'
        'last_updated: "2026-10-03T00:00:00Z"\n'
        f'scratchpad_dir: "{scratch}"\n'
        f"{extra_state}"
    )
    return scratch


def test_ac13_guard_tsv_row_kept_and_bash_gate_still_demands_raw(tmp_path):
    # GUARD (r2): the gate, tsv and learning-store are unchanged; the deliverable now has
    # a producer (op9), so the soft gate keeps its meaning.
    tsv = (REPO_ROOT / "scripts" / "phase-deliverables.tsv").read_text(encoding="utf-8")
    rows = [ln for ln in tsv.splitlines() if ln.strip() and not ln.startswith("#")]
    assert any(re.match(r"^7\s+scratch_file\s+reviews/learnings-raw\.md\s*$", r) for r in rows), rows

    scratch = _gate_fixture(tmp_path, "review_complete: pass\nlearning_backend: file\n")
    assert not (scratch / "reviews" / "learnings-raw.md").exists()
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(tmp_path),
           "BYTEDIGGER_CONFIG": str(tmp_path / "bytedigger.json")}
    proc = subprocess.run(
        ["bash", str(REPO_ROOT / "scripts" / "build-gate.sh")], stdin=subprocess.DEVNULL,
        capture_output=True, text=True, env=env, cwd=str(tmp_path), timeout=60,
    )
    assert proc.returncode == 2, proc.stdout + proc.stderr
    assert "missing deliverable" in proc.stdout + proc.stderr
    assert "learnings-raw.md" in proc.stdout + proc.stderr


# --- AC14 (GUARD) --------------------------------------------------------------

@pytest.mark.parametrize("backend", ["file", "sqlite"])
def test_ac14_guard_extract_without_raw_degrades_to_zero(tmp_path, backend):
    if backend == "sqlite" and shutil.which("sqlite3") is None:
        if os.environ.get("BD_REQUIRE_SQLITE") == "1":
            pytest.fail("sqlite3 binary not available but BD_REQUIRE_SQLITE=1")
        pytest.skip("sqlite3 binary not available")
    cfg = tmp_path / "bytedigger.json"
    cfg.write_text(json.dumps({"learning": {
        "backend": backend, "max_inject": 10, "max_stored": 200,
        "storage_path": ".bytedigger/learnings"}}))
    scratch = tmp_path / "scratch"
    (scratch / "reviews").mkdir(parents=True)
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(tmp_path),
           "BYTEDIGGER_CONFIG": str(cfg)}
    if backend == "sqlite":
        db = tmp_path / "learnings.db"
        schema = (REPO_ROOT / "tests" / "fixtures" / "learning-schema.sql").read_text()
        subprocess.run(["sqlite3", str(db)], input=schema, text=True, check=True,
                       env=env, timeout=60)
        env["LEARNING_DB_URL"] = str(db)
    proc = subprocess.run(
        ["bash", str(REPO_ROOT / "scripts" / "learning-store.sh"), "extract", str(scratch),
         "--config", str(cfg)],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, env=env,
        cwd=str(tmp_path), timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    state = (tmp_path / "build-state.yaml").read_text()
    assert re.search(r"^learnings_extracted: 0$", state, re.M)


# --- AC15 ----------------------------------------------------------------------

def test_ac15_guard_class_i_lint_compileall_config_parses():
    import yaml

    lint = subprocess.run(
        [sys.executable, "-m", "bytedigger_engine.conformance.class_i_lint"],
        cwd=str(ENGINE_PY), capture_output=True, text=True, timeout=180,
    )
    assert lint.returncode == 0, lint.stdout + lint.stderr
    comp = subprocess.run(
        [sys.executable, "-m", "compileall", "-q", "bytedigger_engine"],
        cwd=str(ENGINE_PY), capture_output=True, text=True, timeout=180,
    )
    assert comp.returncode == 0, comp.stdout + comp.stderr
    cfg = yaml.safe_load(
        (ENGINE_PKG / "lib" / "plugins" / "anti_hallucination" / "config.yaml").read_text(encoding="utf-8"))
    assert cfg["plugin"] == "anti_hallucination"
    assert isinstance(cfg["applied_phases"], dict)


def test_ac15_guard_tree_scan_lint_real_tree_clean():
    # F1: deleting _emit_synthesize_disk_truth_telemetry must not leave a stale
    # tree_scan_inventory.json key. Green before GREEN, must stay green.
    from bytedigger_engine.conformance import tree_scan_lint

    assert tree_scan_lint.check(ENGINE_PKG, tree_scan_lint.load_inventory()) == []


def test_ac15b_anti_hallucination_config_drops_phase_7():
    import yaml

    cfg = yaml.safe_load(
        (ENGINE_PKG / "lib" / "plugins" / "anti_hallucination" / "config.yaml").read_text(encoding="utf-8"))
    assert "phase_7_synthesize" not in cfg["applied_phases"]
    assert "phase_5_implement" in cfg["applied_phases"]


# --- AC17-AC22 (r2): derived reviews/learnings-raw.md --------------------------

RAW_REL = "reviews/learnings-raw.md"
_EMDASH = "—"


def _parse_raw(path: Path):
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "bd89_p3c_learnings_parse", REPO_ROOT / "scripts" / "learnings_parse.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.parse(str(path))


def _review_doc(headers: list[str]) -> str:
    body = "Composite review.\n\n"
    for h in headers:
        body += f"{h}\n- File: a.py:1\n\nSome prose about the finding.\n\n"
    return body + "VERDICT: FAIL\n"


_LONG_TITLE = "T" * 250


def _ac17_review() -> str:
    return _review_doc([
        f"### SEVERITY: HIGH {_EMDASH} Missing retry on sync [verify: ok]",
        f"### SEVERITY: MEDIUM -- {_LONG_TITLE}",
        "### SEVERITY: LOW - Naming nit",
    ])


def _by_cat(entries, cat: str) -> list[str]:
    return [lesson for c, lesson in entries if c == cat]


def test_ac17_derived_learnings_raw_parses_with_expected_entries(tmp_path, monkeypatch):
    log = _make_log(tmp_path, monkeypatch)
    log.append("review_findings_audit", {"lost_to_prose": 2, "malformed_headers": 0,
                                         "filtered": 0, "suspect_withhold": False}, "r-audit")
    scratch = tmp_path / "scratch"
    _seed_all(scratch)
    _write(scratch, REVIEW_REL, _ac17_review())
    result = _run_step(_ctx(scratch), log)

    raw = scratch / RAW_REL
    assert raw.is_file()
    entries, errors = _parse_raw(raw)
    assert errors == 0
    assert _by_cat(entries, "review-finding") == [
        "HIGH finding: Missing retry on sync",
        "MEDIUM finding: " + "T" * 200,
        "LOW finding: Naming nit",
    ]
    audit = _by_cat(entries, "review-audit")
    assert len(audit) == 1 and re.search(r"\b2\b", audit[0]), audit
    assert len(entries) == 4
    assert result.status == "ok"
    assert result.data["learnings_written"] is True
    assert result.data["learnings_entries"] == 4
    assert result.data["learnings_raw_path"] == str(raw)
    written = _events(log, "learnings_raw_written")
    assert len(written) == 1
    assert written[0]["payload"]["path"] == str(raw)
    assert written[0]["payload"]["entries"] == 4


def test_ac18_review_finding_entries_capped_at_ten_in_file_order(tmp_path, monkeypatch):
    log = _make_log(tmp_path, monkeypatch)
    scratch = tmp_path / "scratch"
    _seed_all(scratch)
    headers = [f"### SEVERITY: HIGH {_EMDASH} Finding number {i:02d}" for i in range(1, 13)]
    _write(scratch, REVIEW_REL, _review_doc(headers))
    result = _run_step(_ctx(scratch), log)

    assert (scratch / RAW_REL).is_file()
    entries, errors = _parse_raw(scratch / RAW_REL)
    assert errors == 0
    assert _by_cat(entries, "review-finding") == [
        f"HIGH finding: Finding number {i:02d}" for i in range(1, 11)]
    assert result.data["learnings_entries"] == 10


def test_ac19_nothing_derivable_writes_not_derived_file(tmp_path, monkeypatch):
    log = _make_log(tmp_path, monkeypatch)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    result = _run_step(_ctx(scratch), log)

    raw = scratch / RAW_REL
    assert raw.is_file()
    text = raw.read_text(encoding="utf-8")
    assert text.strip() != ""
    assert "# not derived" in text
    assert _parse_raw(raw) == ([], 0)
    assert result.status == "ok"
    assert result.data["report_written"] is True
    assert (scratch / REPORT_REL).is_file()
    assert result.data["learnings_written"] is True
    assert result.data["learnings_entries"] == 0


def test_ac20_fix_process_and_acceptance_entries(tmp_path, monkeypatch):
    log = _make_log(tmp_path, monkeypatch)
    log.append("fix_watchdog_no_progress", {"round": 1}, "r-w1")
    log.append("fix_watchdog_no_progress", {"round": 2}, "r-w2")
    log.append("post_fix_pytest_infra_error",
               {"reason": "timeout", "phase": 6, "step": "run_pytest_post_fix"}, "r-infra")
    scratch = tmp_path / "scratch"
    _seed_all(scratch)
    _write(scratch, SAT_REL, "SATISFACTION: NOT_ASSESSED\n")
    _run_step(_ctx(scratch), log)

    assert (scratch / RAW_REL).is_file()
    entries, errors = _parse_raw(scratch / RAW_REL)
    assert errors == 0
    fix = _by_cat(entries, "fix-process")
    assert any(re.search(r"\b2\b", e) for e in fix), fix
    assert any("timeout" in e and re.search(r"\b1\b", e) for e in fix), fix
    acceptance = _by_cat(entries, "acceptance")
    assert len(acceptance) == 1
    assert "not assessed" in acceptance[0]


def test_ac21_learning_store_extract_stores_derived_entries(tmp_path, monkeypatch):
    log = _make_log(tmp_path, monkeypatch)
    log.append("review_findings_audit", {"lost_to_prose": 2}, "r-audit")
    scratch = tmp_path / "scratch"
    _seed_all(scratch)
    _write(scratch, REVIEW_REL, _ac17_review())
    _run_step(_ctx(scratch), log)

    cfg = tmp_path / "bytedigger.json"
    cfg.write_text(json.dumps({"learning": {
        "backend": "file", "max_inject": 10, "max_stored": 200,
        "storage_path": ".bytedigger/learnings"}}))
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": str(tmp_path),
           "BYTEDIGGER_CONFIG": str(cfg)}
    proc = subprocess.run(
        ["bash", str(REPO_ROOT / "scripts" / "learning-store.sh"), "extract", str(scratch),
         "--config", str(cfg)],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, env=env,
        cwd=str(tmp_path), timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    state = (tmp_path / "build-state.yaml").read_text()
    assert re.search(r"^learnings_extracted: 4$", state, re.M), state
    assert re.search(r"^learnings_parse_errors: 0$", state, re.M), state
    stored = tmp_path / ".bytedigger" / "learnings" / "review-finding.md"
    assert stored.is_file()
    assert sum(1 for ln in stored.read_text().splitlines() if ln.startswith("- ")) == 3


def test_ac22_learnings_write_failure_degrades(tmp_path, monkeypatch):
    log = _make_log(tmp_path, monkeypatch)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    (scratch / "reviews").write_text("i am a file, not a directory", encoding="utf-8")
    result = _run_step(_ctx(scratch), log)

    assert result.status == "ok"
    assert result.error_code is None
    assert (scratch / REPORT_REL).is_file()
    assert result.data["report_written"] is True
    assert result.data["learnings_written"] is False
    skipped = _events(log, "learnings_raw_skipped")
    assert len(skipped) == 1
    assert skipped[0]["payload"]["reason"] == "write_failed"
    assert _events(log, "learnings_raw_written") == []


def test_ac22b_learnings_render_failure_degrades(tmp_path, monkeypatch):
    log = _make_log(tmp_path, monkeypatch)
    scratch = tmp_path / "scratch"
    _seed_all(scratch)

    def _raise(*a, **kw):
        raise RuntimeError("injected learnings render fault")

    # Fault injection on the named pure helper only; the step and writer are real.
    monkeypatch.setattr(p7, "_build_learnings_text", _raise, raising=False)
    result = _run_step(_ctx(scratch), log)

    assert result.status == "ok"
    assert result.error_code is None
    assert (scratch / REPORT_REL).is_file()
    assert result.data["report_written"] is True
    assert result.data["learnings_written"] is False
    skipped = _events(log, "learnings_raw_skipped")
    assert len(skipped) == 1
    assert skipped[0]["payload"]["reason"] == "render_failed"
    assert not (scratch / RAW_REL).exists()


def test_ac22c_report_write_failure_does_not_skip_learnings(tmp_path, monkeypatch):
    # r2.1 F1: op9 is independent of the report outcome when the scratchpad resolves.
    log = _make_log(tmp_path, monkeypatch)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    (scratch / "post-deploy").write_text("i am a file, not a directory", encoding="utf-8")
    result = _run_step(_ctx(scratch), log)

    assert result.status == "ok"
    assert result.error_code is None
    assert result.data["report_written"] is False
    report_skipped = _events(log, "post_deploy_report_skipped")
    assert len(report_skipped) == 1
    assert report_skipped[0]["payload"]["reason"] == "write_failed"
    raw = scratch / RAW_REL
    assert raw.is_file()
    assert "# not derived" in raw.read_text(encoding="utf-8")
    assert result.data["learnings_written"] is True
    assert result.data["learnings_raw_path"] == str(raw)
    assert _events(log, "learnings_raw_skipped") == []
    assert len(_events(log, "learnings_raw_written")) == 1


# --- AC23 (r2.1): refuted / unverified findings, several audits, missing review doc ---

def test_ac23a_refuted_section_skipped_and_unverified_token_kept(tmp_path, monkeypatch):
    log = _make_log(tmp_path, monkeypatch)
    scratch = tmp_path / "scratch"
    _seed_all(scratch)
    _write(scratch, REVIEW_REL, (
        "Composite review.\n\n"
        f"### SEVERITY: HIGH {_EMDASH} Real finding\n- File: a.py:1\n\n"
        f"### SEVERITY: [UNVERIFIED] MEDIUM {_EMDASH} Unchecked claim\n- File: b.py:2\n\n"
        "## Refuted (Semantic)\n\n"
        f"### SEVERITY: HIGH {_EMDASH} Refuted claim\n- File: c.py:3\n\n"
        "## Notes\n\n"
        f"### SEVERITY: LOW {_EMDASH} After refuted section\n- File: d.py:4\n\n"
        "VERDICT: FAIL\n"))
    _run_step(_ctx(scratch), log)

    raw = scratch / RAW_REL
    assert raw.is_file()
    entries, errors = _parse_raw(raw)
    assert errors == 0
    findings = _by_cat(entries, "review-finding")
    assert findings == [
        "HIGH finding: Real finding",
        "UNVERIFIED MEDIUM finding: Unchecked claim",
        "LOW finding: After refuted section",
    ]
    assert not any("Refuted claim" in lesson for _c, lesson in entries)


def test_ac23b_latest_review_findings_audit_event_wins(tmp_path, monkeypatch):
    log = _make_log(tmp_path, monkeypatch)
    log.append("review_findings_audit", {"lost_to_prose": 5}, "r-audit-1")
    log.append("review_findings_audit", {"lost_to_prose": 3}, "r-audit-2")
    scratch = tmp_path / "scratch"
    _seed_all(scratch)
    _run_step(_ctx(scratch), log)

    raw = scratch / RAW_REL
    assert raw.is_file()
    entries, errors = _parse_raw(raw)
    assert errors == 0
    audit = _by_cat(entries, "review-audit")
    assert len(audit) == 1, audit
    assert re.search(r"\b3\b", audit[0]) and not re.search(r"\b5\b", audit[0]), audit


def test_ac23c_missing_review_doc_still_yields_event_derived_entries(tmp_path, monkeypatch):
    log = _make_log(tmp_path, monkeypatch)
    log.append("review_findings_audit", {"lost_to_prose": 2}, "r-audit")
    log.append("fix_watchdog_no_progress", {"round": 1}, "r-w1")
    scratch = tmp_path / "scratch"
    _seed_all(scratch)
    (scratch / REVIEW_REL).unlink()
    result = _run_step(_ctx(scratch), log)

    raw = scratch / RAW_REL
    assert raw.is_file()
    entries, errors = _parse_raw(raw)
    assert errors == 0
    assert _by_cat(entries, "review-finding") == []
    assert len(_by_cat(entries, "review-audit")) == 1
    assert len(_by_cat(entries, "fix-process")) >= 1
    assert result.data["learnings_written"] is True
    assert result.data["learnings_entries"] == len(entries) >= 2


# --- AC16 (GUARD) --------------------------------------------------------------

def test_ac16_guard_gh1124_pr_title_suite_present():
    # The phase 8 PR title/body contract suite must stay in the tree, unedited by this slice.
    assert (ENGINE_PY / "tests" / "test_gh1124_ship_pr_title.py").is_file()
