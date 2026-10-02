"""RED tests for bd#89 P1 -- drop devops_scan / devops_pipeline / canary / smoke / artifact-detect.

Spec: docs/decisions/2026-10-02-bd89-p1-drop-devops-branches.md (AC1-AC10).

AC -> test map
--------------
AC1  test_ac1_register_all_registers_exactly_the_frozen_set
AC2  test_ac2_dropped_module_files_are_gone
     test_ac2_dropped_modules_are_not_importable
     test_ac2_core_manifest_lists_none_of_them
AC3  test_ac3_dropped_error_codes_absent_from_registry
     test_ac3_dropped_error_codes_absent_from_both_error_codes_md
AC4  test_ac4_every_flag_module_and_routed_module_resolves_to_a_file
     test_ac4_no_devops_scan_flags
AC5  test_ac5_select_reviewers_has_no_artifact_type_parameter
     test_ac5_review_plan_has_no_artifact_type_and_no_devops_row
     test_ac5_review_prompt_with_artifact_type_has_no_devops_and_one_reviewer
AC6  test_ac6_write_spec_doc_normal_path_has_no_canary_sidecar_event_or_data_key
     test_ac6_write_spec_doc_surgical_path_has_no_canary_sidecar_event_or_data_key
AC7  test_ac7_no_get_standards_context_attribute
     test_ac7_spec_and_red_prompts_do_not_shell_out_to_devops_prompt_context
AC8  test_ac8_no_devops_in_md_flow_and_gate_script
     test_ac8_no_dropped_stage_names_in_md_flow_gate_script_and_ts
AC9  test_ac9_environment_has_no_claude_and_no_api_key
     (plus the autouse fixture `_no_claude_no_api_key` applied to every test here)
AC10 test_ac10_dispatcher_report_has_no_canary_prefix_rule
     test_ac10_live_engine_code_still_classifies_engine   (guard, passes today)
AC6 also: test_ac6_write_spec_doc_file_sourced_body_has_no_canary_sidecar_event_or_data_key
AC7 covers _build_spec_prompt, _build_red_prompt and _build_green_prompt.

PRE-GREEN FAILURE (HEAD still has the modules, flags, codes, md text):
  AC1, AC2 (all), AC3 (both), AC4 (both: HAL_DEVOPS_SCAN_* flags and the
  ROUTED_MODULES / FLAGS entries routing to phase_5_devops_scan.py exist today),
  AC5 (all 3), AC6 (both), AC7 (both), AC8 (devops grep).
  AC4 note: flag "module" paths resolve against bytedigger_engine/ or engine_py/
  (scripts/spec_lint/lint_spec.py lives under engine_py/); SYSTEM/... entries are
  host-side tools outside this repo and are skipped.
  AC10 (CANARY_ prefix rule exists at lib/dispatcher_report.py today).
  AC8's second grep, AC9 and the live-engine-code AC10 test are guards that pass
  today and must stay green after GREEN.

No dropped module is imported at module scope (the file collects on HEAD and after
GREEN); they are probed through importlib.util.find_spec / file paths only.
No singleton resource is raced (workflows.md 1i): the PATH / env is pre-staged by
an autouse fixture, the build-gate fixtures are written to temp dirs before the run.
"""
from __future__ import annotations

import importlib
import importlib.util
import inspect
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from bytedigger_engine import telemetry_ctx
from bytedigger_engine.contracts import StepResult, WorkflowContext

ENGINE_PY = Path(__file__).resolve().parents[1]
PKG_DIR = ENGINE_PY / "bytedigger_engine"
REPO_ROOT = Path(__file__).resolve().parents[2]

DROPPED_MODULES = (
    "phase_0_6_artifact_detect",
    "phase_5_devops_scan",
    "phase_devops_pipeline",
    "phase_5_integration_canary",
    "phase_6_smoke",
    "_standards_context",
)
DROPPED_ERROR_CODES = (
    "E_CANARY_BAD_CONFIG",
    "E_CANARY_EVENTS_MISSING",
    "E_CANARY_NO_MATCH",
    "E_DEVOPS_SCAN_BLOCKED",
    "E_DEVOPS_SCAN_UNAVAILABLE",
    "E_SMOKE_FAILED",
    "E_SMOKE_TIMEOUT",
)
FROZEN_REGISTRY = {
    "echo", "phase_0_research", "phase_05_inject", "phase_45_spec",
    "phase_5_implement", "phase_5_integrity",
    "phase_6_fix_integrity", "phase_6_review",
    "phase_7_synthesize", "phase_8_post_deploy",
}


# --- AC9: no `claude` binary, no API key, for every test in this file ----------

@pytest.fixture(autouse=True)
def _no_claude_no_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    candidates = [str(Path(sys.executable).parent), "/usr/bin", "/bin"]
    kept: list[str] = []
    for d in candidates:
        if d in kept or not os.path.isdir(d):
            continue
        if shutil.which("claude", path=d) is None:
            kept.append(d)
    monkeypatch.setenv("PATH", os.pathsep.join(kept))
    telemetry_ctx.clear_current_run()
    yield
    telemetry_ctx.clear_current_run()


# --- shared helpers ------------------------------------------------------------

class _FakeEngine:
    def __init__(self) -> None:
        self.names: list[str] = []

    def register(self, name, wf) -> None:
        self.names.append(name)


class _FakeEventLog:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict, str]] = []

    def append(self, event_type: str, payload: dict, run_id: str | None = None) -> None:
        self.events.append((event_type, payload, run_id or "ad-hoc"))


def _ctx(scratch: Path, **org_extra) -> WorkflowContext:
    scratch.mkdir(parents=True, exist_ok=True)
    org = {
        "scratchpad_dir": str(scratch),
        "git_cwd": str(scratch),
        "current_worktree_path": str(scratch),
        "complexity": "FEATURE",
        **org_extra,
    }
    return WorkflowContext(
        tenant_id="hal", scope=None, db_path=None, org_config=org,
        question="add feature X", session_id="test-bd89-p1", persona="hal",
        framework=None, domain=None,
    )


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# --- AC1 -----------------------------------------------------------------------

def test_ac1_register_all_registers_exactly_the_frozen_set():
    from bytedigger_engine.workflows import register_all

    eng = _FakeEngine()
    register_all(eng)
    assert set(eng.names) == FROZEN_REGISTRY, (
        f"extra={sorted(set(eng.names) - FROZEN_REGISTRY)} "
        f"missing={sorted(FROZEN_REGISTRY - set(eng.names))}"
    )
    assert len(eng.names) == len(set(eng.names)), "a stage was registered twice"


# --- AC2 -----------------------------------------------------------------------

@pytest.mark.parametrize("stem", DROPPED_MODULES)
def test_ac2_dropped_module_files_are_gone(stem):
    assert not (PKG_DIR / "workflows" / f"{stem}.py").exists(), f"{stem}.py still on disk"


@pytest.mark.parametrize("stem", DROPPED_MODULES)
def test_ac2_dropped_modules_are_not_importable(stem):
    assert importlib.util.find_spec(f"bytedigger_engine.workflows.{stem}") is None


def test_ac2_core_manifest_lists_none_of_them():
    data = json.loads(_text(ENGINE_PY / "core_manifest.json"))
    strings: list[str] = []

    def walk(node):
        if isinstance(node, str):
            strings.append(node)
        elif isinstance(node, dict):
            for k, v in node.items():
                walk(k)
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(data)
    assert strings, "core_manifest.json parsed to no strings -- fixture precondition"
    listed = [s for s in strings if any(stem in s for stem in DROPPED_MODULES)]
    assert not listed, f"core_manifest.json still lists: {listed}"


# --- AC3 -----------------------------------------------------------------------

def test_ac3_dropped_error_codes_absent_from_registry():
    from bytedigger_engine import error_codes

    present = [c for c in DROPPED_ERROR_CODES if c in error_codes.ERROR_CODES]
    assert not present, f"still registered: {present}"
    assert "E_NO_ROLE_FILES" in error_codes.ERROR_CODES, "fixture precondition: live codes stay"


@pytest.mark.parametrize("md", [ENGINE_PY / "ERROR_CODES.md", PKG_DIR / "ERROR_CODES.md"],
                         ids=["engine_py", "bytedigger_engine"])
def test_ac3_dropped_error_codes_absent_from_both_error_codes_md(md):
    text = _text(md)
    assert "E_NO_ROLE_FILES" in text, "fixture precondition: md lists live codes"
    present = [c for c in DROPPED_ERROR_CODES if c in text]
    assert not present, f"{md.name} still documents: {present}"


# --- AC4 -----------------------------------------------------------------------

def _resolves(rel: str) -> bool:
    # Entries are relative to the package dir, or (scripts/...) to engine_py/.
    # SYSTEM/... entries are host-side tools outside this repo (not checked).
    return (PKG_DIR / rel).is_file() or (ENGINE_PY / rel).is_file()


# Pre-existing dangling entries at base (row-7 flag residue, resolved in P3).
_FROZEN_DANGLING = frozenset({
    "engine-py-audit-gate.py + workflows/phase_5_implement.py",
    "canary.sh",
})


def test_ac4_every_flag_module_and_routed_module_resolves_to_a_file():
    from bytedigger_engine import flags_catalog

    in_repo_flags = {
        name: spec["module"] for name, spec in flags_catalog.FLAGS.items()
        if isinstance(spec.get("module"), str) and not spec["module"].startswith("SYSTEM/")
    }
    assert in_repo_flags, "fixture precondition: catalog has in-repo module entries"
    # Check the dropped-module routing FIRST: it is the forcing reason for this AC.
    stale = [m for m in list(in_repo_flags.values()) + list(flags_catalog.ROUTED_MODULES)
             if any(stem in m for stem in DROPPED_MODULES)]
    assert not stale, f"catalog still routes to dropped modules: {stale}"
    dangling = {n: m for n, m in in_repo_flags.items()
                if m not in _FROZEN_DANGLING and not _resolves(m)}
    dangling_routed = [m for m in flags_catalog.ROUTED_MODULES
                       if m not in _FROZEN_DANGLING and not _resolves(m)]
    assert not dangling, f"FLAGS point at missing files: {dangling}"
    assert not dangling_routed, f"ROUTED_MODULES point at missing files: {dangling_routed}"


def test_ac4_no_devops_scan_flags():
    from bytedigger_engine import flags_catalog

    bad = [k for k in flags_catalog.FLAGS if k.startswith("HAL_DEVOPS_SCAN_")]
    assert not bad, f"still declared: {bad}"


# --- AC5 -----------------------------------------------------------------------

def test_ac5_select_reviewers_has_no_artifact_type_parameter():
    from bytedigger_engine.workflows import phase_6_review

    params = list(inspect.signature(phase_6_review._select_reviewers).parameters)
    assert "artifact_type" not in params, params
    assert params[0] == "complexity" and "fanout" in params, params
    assert not hasattr(phase_6_review, "_ROW_DEVOPS_REVIEWER")
    assert not hasattr(phase_6_review, "_LINE_COMPOSITE_DEVOPS")


def test_ac5_review_plan_has_no_artifact_type_and_no_devops_row(tmp_path):
    from bytedigger_engine.workflows import phase_6_review

    params = list(inspect.signature(phase_6_review._review_plan).parameters)
    assert params == ["ctx", "complexity"], params
    ctx = _ctx(tmp_path / "scratch", artifact_type="dockerfile")
    fanout, table, count = phase_6_review._review_plan(ctx, "FEATURE")
    assert fanout == "single"
    assert count == 1, count
    assert "devops" not in table.lower(), table


def test_ac5_review_prompt_with_artifact_type_has_no_devops_and_one_reviewer(tmp_path):
    from bytedigger_engine.workflows import phase_6_review

    ctx = _ctx(tmp_path / "scratch", artifact_type="dockerfile")
    result = phase_6_review._build_review_prompt(ctx, None)
    assert result.status == "ok", f"{result.error_code}: {result.error}"
    prompt = result.data["prompt"]
    assert "devops" not in prompt.lower()
    assert "CIS/OWASP/SLSA" not in prompt
    assert "role-composite.md" in prompt
    table, count = phase_6_review._select_reviewers("FEATURE")
    assert count == 1
    assert table in prompt, "the single composite row must reach the model verbatim"


# --- AC6 -----------------------------------------------------------------------

_CANARY_SPEC = (
    "## Context\n\nSome context. OLD_MARK_BD89\n\n"
    "## Canary Integration\n\n"
    "event_type: foo\n\n"
    "## Goals\n\nGoals.\n"
)


def _run_write_spec_doc(tmp_path: Path, prev_extra: dict, raw_response: str):
    from bytedigger_engine.workflows import phase_45_spec

    scratch = tmp_path / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    doc_path = scratch / "specs" / "build-spec.md"
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    prev = StepResult(
        status="ok",
        data={"raw_response": raw_response, "doc_path": str(doc_path), **prev_extra},
        duration_ms=0, step_name="call_opus_writer",
    )
    log = _FakeEventLog()
    telemetry_ctx.set_current_run(event_log=log, run_id="bd89-p1-ac6",
                                  step_name="write_spec_doc", phase="phase_45_spec")
    try:
        result = phase_45_spec._write_spec_doc(_ctx(scratch), prev)
    finally:
        telemetry_ctx.clear_current_run()
    return result, scratch, doc_path, log


def _assert_no_canary_artifacts(result, scratch: Path, doc_path: Path, log: _FakeEventLog):
    assert result.status == "ok", f"{result.error_code}: {result.error}"
    assert doc_path.is_file() and "event_type: foo" in _text(doc_path), (
        "fixture precondition: the spec with the Canary block was written"
    )
    assert not (scratch / "integration" / "canary-meta.json").exists()
    emitted = [e[0] for e in log.events if e[0] == "canary_integration_parsed"]
    assert not emitted, f"canary_integration_parsed still emitted: {emitted}"
    assert "canary_event_type" not in (result.data or {}), sorted(result.data or {})


def test_ac6_write_spec_doc_normal_path_has_no_canary_sidecar_event_or_data_key(tmp_path):
    result, scratch, doc_path, log = _run_write_spec_doc(tmp_path, {"cycle": 1}, _CANARY_SPEC)
    _assert_no_canary_artifacts(result, scratch, doc_path, log)


def test_ac6_write_spec_doc_surgical_path_has_no_canary_sidecar_event_or_data_key(tmp_path):
    patches = [{"finding_id": "F1", "old": "OLD_MARK_BD89", "new": "NEW_MARK_BD89"}]
    raw = "```json\n" + json.dumps(patches) + "\n```\n"
    result, scratch, doc_path, log = _run_write_spec_doc(
        tmp_path,
        {"surgical_revise": True, "surgical_base_spec": _CANARY_SPEC, "cycle": 2},
        raw,
    )
    assert "NEW_MARK_BD89" in _text(doc_path), "fixture precondition: surgical patch applied"
    _assert_no_canary_artifacts(result, scratch, doc_path, log)


def test_ac6_write_spec_doc_file_sourced_body_has_no_canary_sidecar_event_or_data_key(tmp_path):
    scratch = tmp_path / "scratch"
    doc_path = scratch / "specs" / "build-spec.md"
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    doc_path.write_text(_CANARY_SPEC, encoding="utf-8")
    result, scratch, doc_path, log = _run_write_spec_doc(
        tmp_path, {"cycle": 1}, "Wrote spec. Done."
    )
    _assert_no_canary_artifacts(result, scratch, doc_path, log)


# --- AC7 -----------------------------------------------------------------------

def test_ac7_no_get_standards_context_attribute():
    from bytedigger_engine.workflows import phase_45_spec, phase_5_implement

    assert not hasattr(phase_45_spec, "get_standards_context")
    assert not hasattr(phase_5_implement, "get_standards_context")


def test_ac7_spec_and_red_prompts_do_not_shell_out_to_devops_prompt_context(tmp_path, monkeypatch):
    from bytedigger_engine.workflows import phase_45_spec, phase_5_implement

    real_run = subprocess.run
    calls: list[list[str]] = []

    def _recording_run(cmd, *args, **kwargs):
        argv = [str(c) for c in (cmd if isinstance(cmd, (list, tuple)) else [cmd])]
        calls.append(argv)
        if any("devops-prompt-context" in a for a in argv):
            return subprocess.CompletedProcess(argv, 1, "", "")
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", _recording_run)

    scratch = tmp_path / "scratch"
    spec_dir = scratch / "specs"
    spec_dir.mkdir(parents=True, exist_ok=True)
    (spec_dir / "build-spec.md").write_text(
        "## Context\nModify `engine_py/workflows/foo.py`; RED test in `tests/test_foo.py`.\n",
        encoding="utf-8",
    )
    ctx = _ctx(scratch, artifact_type="dockerfile")

    spec_prompt = phase_45_spec._build_spec_prompt(ctx, None)
    assert spec_prompt.status == "ok", f"{spec_prompt.error_code}: {spec_prompt.error}"
    red_prompt = phase_5_implement._build_red_prompt(ctx, None)
    assert red_prompt.status == "ok", f"{red_prompt.error_code}: {red_prompt.error}"
    green_prev = StepResult(
        status="ok",
        data={
            "spec_path": str(scratch / "specs" / "build-spec.md"),
            "red_log_path": str(scratch / "tests" / "build-red-output.log"),
            "validation_doc_path": str(scratch / "reviews" / "build-opus-validation.md"),
            "verdict": "PASS",
        },
        duration_ms=0, step_name="gate_on_validation",
    )
    green_prompt = phase_5_implement._build_green_prompt(ctx, green_prev)
    assert green_prompt.status == "ok", f"{green_prompt.error_code}: {green_prompt.error}"

    offending = [a for a in calls if any("devops-prompt-context" in x for x in a)]
    assert not offending, f"prompt builders still shell out: {offending}"


# --- AC8 -----------------------------------------------------------------------

def _md_flow_files() -> list[Path]:
    files = [REPO_ROOT / "commands" / "build.md",
             REPO_ROOT / "skills" / "bytedigger" / "SKILL.md",
             REPO_ROOT / "scripts" / "build-gate.sh"]
    files += sorted((REPO_ROOT / "phases").glob("*.md"))
    files += sorted(p for p in (REPO_ROOT / "templates").rglob("*") if p.is_file())
    for f in files:
        assert f.is_file(), f"fixture precondition: {f} must exist"
    assert len(files) >= 8
    return files


def test_ac8_no_devops_in_md_flow_and_gate_script():
    hits = {
        str(f.relative_to(REPO_ROOT)): len(re.findall("devops", _text(f), re.IGNORECASE))
        for f in _md_flow_files()
    }
    hits = {k: v for k, v in hits.items() if v}
    assert not hits, f"devops mentions remain: {hits}"


def test_ac8_no_dropped_stage_names_in_md_flow_gate_script_and_ts():
    ts_files = sorted((REPO_ROOT / "scripts" / "ts").glob("*.ts"))
    assert ts_files, "fixture precondition: scripts/ts/*.ts exist"
    pat = re.compile(r"integration_canary|phase_6_smoke|devops_scan|artifact_detect")
    hits = [str(f.relative_to(REPO_ROOT)) for f in _md_flow_files() + ts_files if pat.search(_text(f))]
    assert not hits, f"dropped stage names remain in: {hits}"


# --- AC9 -----------------------------------------------------------------------

def test_ac9_environment_has_no_claude_and_no_api_key():
    assert "ANTHROPIC_API_KEY" not in os.environ
    assert shutil.which("claude") is None


# --- AC10 ----------------------------------------------------------------------

def test_ac10_dispatcher_report_has_no_canary_prefix_rule():
    from bytedigger_engine.lib import dispatcher_report

    tails = [tail for tail, _cls in dispatcher_report._PREFIX_RULES]
    assert not [t for t in tails if t.startswith("CANARY")], tails
    # behavior: a synthetic E_CANARY_* code no longer classifies as engine
    assert dispatcher_report.classify_error_code("E_CANARY_SYNTHETIC") == "unknown"


def test_ac10_live_engine_code_still_classifies_engine():
    from bytedigger_engine.lib import dispatcher_report

    assert dispatcher_report.classify_error_code("E_STEP_TIMEOUT") == "engine"
    assert dispatcher_report.classify_error_code("E_RESTART_CAP") == "engine"
