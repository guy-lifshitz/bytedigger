"""RED tests for bd#89 P2a -- drop phases 1-4 (discovery, explore, clarify, architect).

Spec: docs/decisions/2026-10-02-bd89-p2a-drop-phases-1-4.md (FROZEN r3), AC1-AC16.
AC8 was removed in r2 (spec_lite bridge landed in P2b); its number is kept and no
test exists for it.

AC -> test map
--------------
AC1   test_ac1_register_all_registers_exactly_the_frozen_set
      test_ac1b_real_engine_registry_resolves_kept_and_refuses_dropped   (real WorkflowEngine)
AC2   test_ac2_dropped_module_files_are_gone / _are_not_importable   (x5 params)
      test_ac2_core_manifest_lists_none_of_them
      test_ac2_dropped_md_phase_and_agent_files_are_gone             (x6 params)
AC3   test_ac3_dropped_error_codes_absent_from_registry
      test_ac3_dropped_error_codes_absent_from_both_error_codes_md   (x2)
      test_ac3_guard_error_codes_md_files_stay_byte_identical        (GUARD, green now)
      test_ac3_frozen_short_circuit_flag_is_gone
AC4   test_ac4_timeout_policy_has_no_dropped_stage_keys
      test_ac4_model_config_has_no_discovery_or_explore_role
      test_ac4_anti_hallucination_config_names_no_dropped_phase
      test_ac4_except_pass_allowlist_names_no_dropped_module
AC5   test_ac5_spec_writer_prompt_has_no_architecture_or_research_input
      test_ac5_reviewer_prompt_has_no_exploration_findings
      test_ac5_red_prompt_has_no_architecture_line
      test_ac5_green_prompt_does_not_require_architecture
      test_ac5_architecture_doc_relpath_attribute_is_gone
      test_ac5_result_data_has_no_arch_presence_keys
AC6   test_ac6_facts_block_replaces_files_and_no_model_call
AC7   test_ac7_skip_logic_has_none_of_the_deleted_helpers
      test_ac7_guard_detect_frozen_spec_still_works                  (GUARD, green now)
AC9   test_ac9_facts_pack_kill_switch_builds_prompt_without_facts_block   (GUARD, green now)
      test_ac9_facts_collection_failure_below_the_seam_is_visible_not_fatal (GUARD, green now)
      test_ac9_facts_block_for_raising_does_not_crash_the_prompt
AC10  test_ac10_phase5_entry_with_plan_review_and_no_phase4_state_is_allowed
      test_ac10_phase5_entry_without_plan_review_soft_blocks_every_tier
      test_ac10_unreadable_state_hard_blocks_ts_gate                 (GUARD, green now)
      test_ac10_stale_phase4_state_passes_through_and_writes_no_stale_line
      test_ac10_phase45_applies_plan_review_check_to_every_tier
      test_ac10_bash_and_ts_soft_block_reasons_are_byte_identical     (F3 parity)
AC11 autouse fixture `_no_claude_no_api_key` + test_ac11_environment_has_no_claude_and_no_api_key
AC12  test_ac12_no_dropped_phase_residue_in_md_and_gate_scripts
      test_ac12_build_md_states_the_single_flow_for_all_tiers
      test_ac12_no_simple_skips_phases_wording
      test_ac12_phase_45_spec_md_requires_plan_review_for_every_tier
      test_ac12_build_md_does_not_scope_plan_review_to_feature_complex
      test_ac12_build_md_resumable_routes_stale_phases_1_4_to_phase_45
      (templates/driver-resume.sh is covered by the residue test: templates/* is in scope)
AC13  test_ac13_no_stale_test_references_ast
AC14  test_ac14_bd44_expected_workflows_is_10                 (GUARD, green by construction)
      test_ac14_frozen_registry_sets_are_the_same_10          (GUARD, green by construction)
      test_ac14_bd141_pins_are_12_drivers_and_15_dispatches   (GUARD, green by construction)
      test_ac14_no_other_registry_count_of_14                 (GUARD, green by construction)
AC16  test_ac16_guard_stale_phases_csv_resume_is_refused_loudly   (GUARD, green now)
AC15  test_ac15_guard_probe_triggers_fire_on_the_feature_request   (GUARD, green now)
      test_ac15_guard_neutral_request_triggers_nothing             (GUARD, green now)
      test_ac15_architecture_doc_text_does_not_trigger_the_probe
      test_ac15_decision_doc_text_triggers_the_probe

Expected RED today: everything not marked GUARD.  GUARDs are correctness guards
that protect post-GREEN behaviour and pass against the current tree.

Dropped modules are never imported at module scope (the file collects before and
after GREEN); they are probed through importlib.util.find_spec and file paths.
No singleton resource is raced (workflows.md 1i): cwd, env and PATH are pre-staged
by fixtures before the unit under test runs; gates run as subprocesses against a
temp project.  No sys.path mutation, no conftest import.
"""
from __future__ import annotations

import ast
import importlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from bytedigger_engine import telemetry_ctx
from bytedigger_engine.contracts import StepResult, WorkflowContext

ENGINE_PY = Path(__file__).resolve().parents[1]
PKG_DIR = ENGINE_PY / "bytedigger_engine"
REPO_ROOT = Path(__file__).resolve().parents[2]
TESTS_DIR = Path(__file__).resolve().parent
BASH_GATE = REPO_ROOT / "scripts" / "build-gate.sh"
TS_GATE = REPO_ROOT / "scripts" / "ts" / "build-phase-gate.ts"

# Resolved at import time, before the autouse fixture strips PATH (AC11).
_BUN = shutil.which("bun")

DROPPED_STEMS = (
    "phase_1_discovery", "phase_2_explore", "phase_3_clarify",
    "phase_4_architect", "graph_source",
)
DROPPED_STAGES = DROPPED_STEMS[:4]
DROPPED_MD = (
    "phases/phase-1-discovery.md", "phases/phase-2-explore.md",
    "phases/phase-3-clarify.md", "phases/phase-4-architect.md",
    "agents/explorer.md", "agents/architect.md",
)
DROPPED_CODES = (
    "E_CLARIFY_BLOCKED", "E_CLARIFY_NEEDS_CONTEXT", "E_CLARIFY_NO_MARKER",
    "E_EXPLORE_BLOCKED", "E_EXPLORE_NEEDS_CONTEXT", "E_EXPLORE_NO_MARKER",
)
DELETED_NAMES = (
    "ARCHITECTURE_DOC_RELPATH", "should_skip_phase", "make_skip_result",
    "passthrough_if_skipped", "frozen_short_circuit_enabled",
    "get_claude_discovery", "get_claude_explore",
)
FROZEN_REGISTRY = {
    "echo", "phase_0_research", "phase_05_inject", "phase_45_spec",
    "phase_5_implement", "phase_5_integrity", "phase_6_fix_integrity",
    "phase_6_review", "phase_7_synthesize", "phase_8_post_deploy",
}
_ERROR_CODES_MD = [ENGINE_PY / "ERROR_CODES.md", PKG_DIR / "ERROR_CODES.md"]


# --- AC11: no `claude` binary, no API key, for every test in this file ---------

@pytest.fixture(autouse=True)
def _no_claude_no_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    for var in ("GRAPHIFY_OUT", "HAL_FACTS_PACK", "HAL_SPEC_REALITY_GATE"):
        monkeypatch.delenv(var, raising=False)
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


# --- shared helpers --------------------------------------------------------------

class _FakeEngine:
    def __init__(self) -> None:
        self.names: list[str] = []

    def register(self, name, wf) -> None:
        self.names.append(name)


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _ctx_plain(scratch: Path) -> WorkflowContext:
    scratch.mkdir(parents=True, exist_ok=True)
    return WorkflowContext(
        tenant_id="hal", scope=None, db_path=None,
        org_config={"scratchpad_dir": str(scratch), "complexity": "FEATURE"},
        question="add feature X", session_id="test-bd89-p2a", persona="hal",
        framework=None, domain=None,
    )


# --- AC1 -------------------------------------------------------------------------

def test_ac1_register_all_registers_exactly_the_frozen_set():
    from bytedigger_engine.workflows import register_all

    eng = _FakeEngine()
    register_all(eng)
    assert set(eng.names) == FROZEN_REGISTRY, (
        f"extra={sorted(set(eng.names) - FROZEN_REGISTRY)} "
        f"missing={sorted(FROZEN_REGISTRY - set(eng.names))}"
    )
    assert len(eng.names) == len(set(eng.names)), "a stage was registered twice"


def test_ac1b_real_engine_registry_resolves_kept_and_refuses_dropped(tmp_path):
    from bytedigger_engine.engine import WorkflowEngine
    from bytedigger_engine.workflows import register_all

    eng = WorkflowEngine(event_log=None)
    register_all(eng)
    names = eng.registered()
    assert "phase_45_spec" in names and "phase_5_implement" in names, names
    ctx = _ctx_plain(tmp_path / "scratch")
    for dropped in DROPPED_STAGES:
        assert dropped not in names, f"{dropped} still registered"
        with pytest.raises(KeyError, match="not registered"):
            eng.execute(dropped, ctx)


# --- AC2 -------------------------------------------------------------------------

@pytest.mark.parametrize("stem", DROPPED_STEMS)
def test_ac2_dropped_module_files_are_gone(stem):
    assert not (PKG_DIR / "workflows" / f"{stem}.py").exists(), f"{stem}.py still on disk"


@pytest.mark.parametrize("stem", DROPPED_STEMS)
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
    listed = [s for s in strings if any(stem in s for stem in DROPPED_STEMS)]
    assert not listed, f"core_manifest.json still lists: {listed}"


@pytest.mark.parametrize("rel", DROPPED_MD)
def test_ac2_dropped_md_phase_and_agent_files_are_gone(rel):
    assert (REPO_ROOT / "phases" / "phase-45-spec.md").is_file(), "fixture precondition"
    assert not (REPO_ROOT / rel).exists(), f"{rel} still on disk"


# --- AC3 -------------------------------------------------------------------------

def test_ac3_dropped_error_codes_absent_from_registry():
    from bytedigger_engine import error_codes

    present = [c for c in DROPPED_CODES if c in error_codes.ERROR_CODES]
    assert not present, f"still registered: {present}"
    assert "E_NO_ROLE_FILES" in error_codes.ERROR_CODES, "fixture precondition: live codes stay"


@pytest.mark.parametrize("md", _ERROR_CODES_MD, ids=["engine_py", "bytedigger_engine"])
def test_ac3_dropped_error_codes_absent_from_both_error_codes_md(md):
    text = _text(md)
    assert "E_NO_ROLE_FILES" in text, "fixture precondition: md lists live codes"
    present = [c for c in DROPPED_CODES if c in text]
    assert not present, f"{md} still documents: {present}"


def test_ac3_guard_error_codes_md_files_stay_byte_identical():
    """GUARD (green at RED, must stay green): the md mirror contract."""
    a, b = _ERROR_CODES_MD
    assert a.read_bytes() == b.read_bytes()


def test_ac3_frozen_short_circuit_flag_is_gone():
    from bytedigger_engine import flags_catalog

    assert flags_catalog.FLAGS, "fixture precondition: catalog is populated"
    assert "HAL_FROZEN_SHORT_CIRCUIT" not in flags_catalog.FLAGS


# --- AC4 -------------------------------------------------------------------------

def test_ac4_timeout_policy_has_no_dropped_stage_keys():
    from bytedigger_engine.lib import timeout_policy

    policy = timeout_policy.DEFAULT_POLICY
    assert "spec.writer" in policy and "implement.red" in policy, "fixture precondition"
    stale = [k for k in policy if k.split(".")[0] in ("discovery", "explore", "clarify", "architect")]
    assert not stale, f"timeout policy still has: {stale}"


def test_ac4_model_config_has_no_discovery_or_explore_role():
    from bytedigger_engine.lib import model_config

    assert hasattr(model_config, "get_claude_spec_writer"), "fixture precondition"
    leaked = [n for n in ("get_claude_discovery", "get_claude_explore") if hasattr(model_config, n)]
    assert not leaked, f"getters still present: {leaked}"
    roles = model_config.FALLBACK_CONFIG["claude"]
    assert roles, "fixture precondition"
    assert not [r for r in ("discovery", "explore") if r in roles], sorted(roles)


def test_ac4_anti_hallucination_config_names_no_dropped_phase():
    cfg = PKG_DIR / "lib" / "plugins" / "anti_hallucination" / "config.yaml"
    try:
        import yaml  # noqa: PLC0415
        phases = list(yaml.safe_load(_text(cfg))["applied_phases"])
    except ImportError:  # loader-level fallback: top-level keys under applied_phases
        phases, inside = [], False
        for line in _text(cfg).splitlines():
            body = line.split("#", 1)[0].rstrip()
            if not body:
                continue
            if body.startswith("applied_phases:"):
                inside = True
            elif inside and re.match(r"^  \w+:\s*$", body):
                phases.append(body.strip().rstrip(":"))
    assert "phase_45_spec" in phases, f"fixture precondition: {phases}"
    stale = [p for p in phases if p in DROPPED_STAGES]
    assert not stale, f"config.yaml still enables: {stale}"


def test_ac4_except_pass_allowlist_names_no_dropped_module():
    from bytedigger_engine.security import security_lint

    allow = security_lint._load_except_pass_allowlist(
        {"HAL_EXCEPT_PASS_ALLOWLIST": str(PKG_DIR / "security" / "except-pass-allowlist.txt")}
    )
    assert allow, "fixture precondition: allowlist parsed to rows"
    stale = [p for p in allow if any(stem in p for stem in DROPPED_STEMS)]
    assert not stale, f"allowlist still has rows for: {stale}"


# --- AC5 / AC6 / AC9 / AC15 fixtures: a real repo, real prompt builders ----------

_MARK_ARCH = "P2A_ARCH_MARK_91c7"
_MARK_ARCH_ANY = "P2A_ARCH_ANY_MARK_3d21"
_MARK_RESEARCH = "P2A_RESEARCH_MARK_55ab"
_RESEARCH_FILE = "p2a_research_note_44d0.md"


def _repo(root: Path) -> Path:
    repo = root / "repo"
    (repo / "pkg").mkdir(parents=True, exist_ok=True)
    (repo / "tests").mkdir(parents=True, exist_ok=True)
    (repo / "pkg" / "mod.py").write_text(
        "def existing_helper(x):\n    return x + 1\n\n\ndef other():\n    return existing_helper(1)\n",
        encoding="utf-8",
    )
    (repo / "tests" / "test_mod.py").write_text(
        "from pkg.mod import existing_helper\n\n\ndef test_existing_helper_works():\n"
        "    assert existing_helper(1) == 2\n",
        encoding="utf-8",
    )
    return repo


def _ctx(scratch: Path, repo: Path, *, question: str = "Change `existing_helper`", **extra) -> WorkflowContext:
    scratch.mkdir(parents=True, exist_ok=True)
    inj = scratch / "injection"
    inj.mkdir(parents=True, exist_ok=True)
    for name in ("hal-memory", "constitution", "quality-gate", "producer-rules", "active-work"):
        (inj / f"{name}.md").write_text("")
    return WorkflowContext(
        tenant_id="t", scope=None, db_path=None,
        org_config={"scratchpad_dir": str(scratch), "hal_root": str(repo), **extra},
        question=question, session_id="p2a", persona="p", framework=None, domain=None,
    )


def _seed_scratch(scratch: Path, *, arch_text: str | None = None) -> None:
    """Scratchpad holding what phases 1-4 used to write; the engine must ignore all of it."""
    (scratch / "architecture").mkdir(parents=True, exist_ok=True)
    (scratch / "architecture" / "anything.md").write_text(_MARK_ARCH_ANY + "\n", encoding="utf-8")
    (scratch / "architecture" / "architecture.md").write_text(
        (arch_text if arch_text is not None else _MARK_ARCH) + "\n", encoding="utf-8")
    (scratch / "research").mkdir(parents=True, exist_ok=True)
    (scratch / "research" / _RESEARCH_FILE).write_text(_MARK_RESEARCH + "\n", encoding="utf-8")
    (scratch / "research" / "anything.md").write_text(_MARK_RESEARCH + "\n", encoding="utf-8")


_SPEC = (
    "## Context\nChange `existing_helper` in `pkg/mod.py`.\n\n## Acceptance\n"
    "| AC | criterion |\n|---|---|\n| AC1 | `existing_helper` in `pkg/mod.py` returns x + 2 |\n"
)


def _write_spec(scratch: Path) -> Path:
    spec = scratch / "specs" / "build-spec.md"
    spec.parent.mkdir(parents=True, exist_ok=True)
    spec.write_text(_SPEC, encoding="utf-8")
    return spec


def _prompt(result: StepResult) -> str:
    assert result.status == "ok", result
    return result.data["prompt"]


_ARCH_NEEDLES = (
    _MARK_ARCH, _MARK_ARCH_ANY, _MARK_RESEARCH, _RESEARCH_FILE,
    "ARCHITECTURE DECISION", "EXPLORATION FINDINGS",
    "architecture/architecture.md", "architecture/anything.md",
)


def _present(prompt: str) -> list[str]:
    return [n for n in _ARCH_NEEDLES if n in prompt]


# --- AC5 (production side effect, no mocks of the unit under test) ----------------

def test_ac5_spec_writer_prompt_has_no_architecture_or_research_input(tmp_path):
    from bytedigger_engine.workflows import phase_45_spec

    repo = _repo(tmp_path)
    scratch = tmp_path / "s"
    ctx = _ctx(scratch, repo)
    _seed_scratch(scratch)
    prompt = _prompt(phase_45_spec._build_spec_prompt(ctx, None))
    assert "FEATURE REQUEST:" in prompt, "fixture precondition: real spec-writer prompt"
    assert not _present(prompt), f"spec-writer prompt still carries: {_present(prompt)}"


def test_ac5_reviewer_prompt_has_no_exploration_findings(tmp_path):
    from bytedigger_engine.workflows import phase_45_spec

    repo = _repo(tmp_path)
    scratch = tmp_path / "s"
    ctx = _ctx(scratch, repo)
    _seed_scratch(scratch)
    spec = _write_spec(scratch)
    prev = StepResult(status="ok", data={"spec_path": str(spec), "cycle": 1},
                      duration_ms=0, step_name="write_spec_doc")
    prompt = _prompt(phase_45_spec._build_review_prompt(ctx, prev))
    assert "SPEC TO REVIEW" in prompt, "fixture precondition: real reviewer prompt"
    assert not _present(prompt), f"reviewer prompt still carries: {_present(prompt)}"


def test_ac5_red_prompt_has_no_architecture_line(tmp_path):
    from bytedigger_engine.workflows import phase_5_implement

    repo = _repo(tmp_path)
    scratch = tmp_path / "s"
    ctx = _ctx(scratch, repo, question="Improve the module")
    _seed_scratch(scratch)
    _write_spec(scratch)
    prompt = _prompt(phase_5_implement._build_red_prompt(ctx, None))
    assert "SPEC (read this file)" in prompt, "fixture precondition: real RED prompt"
    assert not _present(prompt), f"RED prompt still carries: {_present(prompt)}"


def test_ac5_green_prompt_does_not_require_architecture(tmp_path):
    from bytedigger_engine.workflows import phase_5_implement

    repo = _repo(tmp_path)
    scratch = tmp_path / "s"
    ctx = _ctx(scratch, repo, question="Improve the module")
    _seed_scratch(scratch)
    spec = _write_spec(scratch)
    val_doc = scratch / "validation" / "validation.md"
    val_doc.parent.mkdir(parents=True, exist_ok=True)
    val_doc.write_text("VERDICT: PASS\n")
    prev = StepResult(status="ok", data={
        "spec_path": str(spec), "validation_doc_path": str(val_doc),
        "red_log_path": str(scratch / "tests" / "build-red-output.log"),
        "red_test_paths": ["tests/test_mod.py"], "cycle": 1,
    }, duration_ms=0, step_name="prev")
    prompt = _prompt(phase_5_implement._build_green_prompt(ctx, prev))
    assert "SPEC" in prompt, "fixture precondition: real GREEN prompt"
    assert "SPEC + ARCHITECTURE" not in prompt
    assert not _present(prompt), f"GREEN prompt still carries: {_present(prompt)}"


def test_ac5_architecture_doc_relpath_attribute_is_gone():
    from bytedigger_engine.workflows import phase_45_spec, phase_5_implement

    assert hasattr(phase_45_spec, "_build_spec_prompt") and hasattr(phase_5_implement, "_build_red_prompt")
    stale = [m.__name__ for m in (phase_45_spec, phase_5_implement)
             if hasattr(m, "ARCHITECTURE_DOC_RELPATH")]
    assert not stale, f"ARCHITECTURE_DOC_RELPATH still defined in: {stale}"


def test_ac5_result_data_has_no_arch_presence_keys(tmp_path):
    from bytedigger_engine.workflows import phase_45_spec, phase_5_implement

    repo = _repo(tmp_path)
    scratch = tmp_path / "s"
    ctx = _ctx(scratch, repo, question="Improve the module")
    _seed_scratch(scratch)
    _write_spec(scratch)
    spec_res = phase_45_spec._build_spec_prompt(ctx, None)
    red_res = phase_5_implement._build_red_prompt(ctx, None)
    assert spec_res.status == "ok" and red_res.status == "ok"
    assert "arch_doc_present" not in spec_res.data, sorted(spec_res.data)
    assert "arch_present" not in red_res.data, sorted(red_res.data)


# --- AC6: facts_pack replaces the files; no model call while building the prompt --

def test_ac6_facts_block_replaces_files_and_no_model_call(tmp_path, monkeypatch):
    from bytedigger_engine import facts_pack, llm_subprocess
    from bytedigger_engine.workflows import phase_45_spec

    calls: list[str] = []

    def _boom(name):
        def inner(*a, **k):
            calls.append(name)
            raise AssertionError(f"{name} must not be called while building a prompt")
        return inner

    monkeypatch.setattr(phase_45_spec, "invoke_llm_subprocess", _boom("phase_45_spec.invoke_llm_subprocess"))
    monkeypatch.setattr(llm_subprocess, "invoke_llm_subprocess", _boom("llm_subprocess.invoke_llm_subprocess"))
    monkeypatch.setattr(subprocess, "run", _boom("subprocess.run"))
    monkeypatch.setattr(subprocess, "Popen", _boom("subprocess.Popen"))

    repo = _repo(tmp_path)
    scratch = tmp_path / "s"
    ctx = _ctx(scratch, repo, question="Rename `ghost_helper` to use `existing_helper`")
    _seed_scratch(scratch)
    prompt = _prompt(phase_45_spec._build_spec_prompt(ctx, None))
    assert not calls, calls
    # facts_pack's real output, not a stub: it found the repo symbol and flagged the ghost.
    assert facts_pack.FACTS_HEADER in prompt
    facts = prompt[prompt.index(facts_pack.FACTS_HEADER):]
    assert "pkg/mod.py:1" in facts and "ghost_helper" in facts
    # ...and it is the ONLY input: the architecture file reference is gone.
    assert "ARCHITECTURE DECISION" not in prompt and _MARK_ARCH not in prompt


# --- AC7 -------------------------------------------------------------------------

_FROZEN_TEXT = (
    "# Frozen Spec\n\n**Status:** FROZEN (§1-PREFLIGHT done)\n\n"
    "### §3 Acceptance Criteria\n| # | AC |\n|---|----|\n| AC1 | thing |\n"
)


def test_ac7_skip_logic_has_none_of_the_deleted_helpers():
    from bytedigger_engine import skip_logic

    assert hasattr(skip_logic, "detect_frozen_spec"), "fixture precondition"
    left = [n for n in ("should_skip_phase", "make_skip_result", "passthrough_if_skipped",
                        "frozen_short_circuit_enabled") if hasattr(skip_logic, n)]
    assert not left, f"skip_logic still defines: {left}"


def test_ac7_guard_detect_frozen_spec_still_works(tmp_path, monkeypatch):
    """GUARD (green at RED): frozen-spec detection survives the helper deletion."""
    from bytedigger_engine import skip_logic

    doc = tmp_path / "decision.md"
    doc.write_text(_FROZEN_TEXT, encoding="utf-8")
    monkeypatch.chdir(tmp_path)  # pre-stage the containment root (1i); no race
    frozen, path = skip_logic.detect_frozen_spec(str(doc))
    assert frozen is True and path is not None and Path(path).samefile(doc)
    assert skip_logic.is_frozen_spec_text(_FROZEN_TEXT) is True
    assert skip_logic.FROZEN_SPEC_PREFLIGHT_MARKER in _FROZEN_TEXT
    resolved = skip_logic._resolve_decision_doc_path("decision.md")
    assert resolved is not None and Path(resolved).samefile(doc)
    plain = tmp_path / "plain.md"
    plain.write_text("# just notes\n", encoding="utf-8")
    assert skip_logic.detect_frozen_spec(str(plain))[0] is False


# --- AC9: degrade, don't crash ---------------------------------------------------

def test_ac9_facts_pack_kill_switch_builds_prompt_without_facts_block(tmp_path, monkeypatch):
    """GUARD (green at RED): HAL_FACTS_PACK=0 means 'no facts block', not a crash."""
    from bytedigger_engine import facts_pack
    from bytedigger_engine.workflows import phase_45_spec

    monkeypatch.setenv("HAL_FACTS_PACK", "0")
    repo = _repo(tmp_path)
    ctx = _ctx(tmp_path / "s", repo)
    prompt = _prompt(phase_45_spec._build_spec_prompt(ctx, None))
    assert "FEATURE REQUEST:" in prompt
    assert facts_pack.FACTS_HEADER not in prompt


def test_ac9_facts_collection_failure_below_the_seam_is_visible_not_fatal(tmp_path, monkeypatch):
    """GUARD (green at RED): collect() blowing up is absorbed inside facts_pack."""
    from bytedigger_engine import facts_pack
    from bytedigger_engine.workflows import phase_45_spec

    def boom(*a, **k):
        raise RuntimeError("walk exploded")

    events: list = []
    monkeypatch.setattr(facts_pack, "_emit_safe", lambda name, payload: events.append((name, payload)))
    monkeypatch.setattr(facts_pack, "collect", boom)
    repo = _repo(tmp_path)
    ctx = _ctx(tmp_path / "s", repo)
    prompt = _prompt(phase_45_spec._build_spec_prompt(ctx, None))
    assert "FEATURE REQUEST:" in prompt
    assert "walk exploded" not in prompt
    assert facts_pack.FACTS_HEADER not in prompt, "facts must degrade to the empty string"
    failed = [p for n, p in events if n == "facts_pack_failed"]
    assert failed and "walk exploded" in failed[0]["error"], events


def test_ac9_facts_block_for_raising_does_not_crash_the_prompt(tmp_path, monkeypatch):
    from bytedigger_engine import facts_pack
    from bytedigger_engine.workflows import phase_45_spec

    def boom(*a, **k):
        raise RuntimeError("facts seam exploded")

    monkeypatch.setattr(facts_pack, "facts_block_for", boom)
    repo = _repo(tmp_path)
    ctx = _ctx(tmp_path / "s", repo)
    try:
        res = phase_45_spec._build_spec_prompt(ctx, None)
    except Exception as exc:  # noqa: BLE001
        pytest.fail(f"spec-writer prompt crashed when the facts seam raised: {exc!r}")
    prompt = _prompt(res)
    assert "FEATURE REQUEST:" in prompt
    assert facts_pack.FACTS_HEADER not in prompt
    assert "facts seam exploded" not in prompt, "call-site degrades to facts = '' (no error text in the prompt)"


# --- AC10: both gates, real subprocess side effects -----------------------------

TIERS = ("SIMPLE", "FEATURE", "COMPLEX")
GATES = ("bash", "ts")


def _gate_project(tmp_path: Path, phase: str, complexity: str, extra: str = "",
                  *, scratch_files: dict[str, str] | None = None) -> Path:
    scratch = tmp_path / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    for rel, body in (scratch_files or {}).items():
        p = scratch / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body, encoding="utf-8")
    (tmp_path / "bytedigger.json").write_text(json.dumps({"gates_enabled": True, "tdd_mandatory": True}))
    (tmp_path / "build-state.yaml").write_text(
        'task: "t"\n'
        f"complexity: {complexity}\n"
        "mode: AUTONOMOUS\n"
        f'current_phase: "{phase}"\n'
        'last_updated: "2026-10-02T00:00:00Z"\n'
        f'scratchpad_dir: "{scratch}"\n'
        f"{extra}"
    )
    return scratch


def _run_gate(gate: str, tmp_path: Path):
    if gate == "ts" and _BUN is None:
        pytest.skip("bun is not installed: the TS gate twin cannot be exercised here")
    path = os.pathsep.join(
        [str(Path(sys.executable).parent), "/usr/bin", "/bin"]
        + ([str(Path(_BUN).parent)] if _BUN else [])
    )
    env = {"PATH": path, "HOME": str(tmp_path), "BYTEDIGGER_CONFIG": str(tmp_path / "bytedigger.json")}
    cmd = ["bash", str(BASH_GATE)] if gate == "bash" else [_BUN, "run", str(TS_GATE)]
    return subprocess.run(cmd, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                          env=env, cwd=str(tmp_path), timeout=60)


_PHASE5_KEPT = "phase_5_implement: complete\nopus_validation: pass\n"


@pytest.mark.parametrize("tier", TIERS)
@pytest.mark.parametrize("gate", GATES)
def test_ac10_phase5_entry_with_plan_review_and_no_phase4_state_is_allowed(gate, tier, tmp_path):
    _gate_project(tmp_path, "5", tier, "plan_review: pass\n" + _PHASE5_KEPT)
    assert not (tmp_path / "scratch" / "architecture").exists(), "fixture: no phase-4 artifacts"
    proc = _run_gate(gate, tmp_path)
    assert proc.returncode == 0, proc.stdout + proc.stderr


@pytest.mark.parametrize("plan_review", [None, "fail"], ids=["missing", "not-pass"])
@pytest.mark.parametrize("tier", TIERS)
@pytest.mark.parametrize("gate", GATES)
def test_ac10_phase5_entry_without_plan_review_soft_blocks_every_tier(gate, tier, plan_review, tmp_path):
    extra = _PHASE5_KEPT + (f"plan_review: {plan_review}\n" if plan_review else "")
    _gate_project(tmp_path, "5", tier, extra)
    proc = _run_gate(gate, tmp_path)
    assert proc.returncode == 2, f"{tier}: {proc.stdout}{proc.stderr}"
    assert "plan_review" in proc.stdout
    assert "phase_4_architect" not in proc.stdout, "gate still demands the dropped phase"


@pytest.mark.skipif(hasattr(os, "geteuid") and os.geteuid() == 0, reason="chmod 000 has no effect as root")
def test_ac10_unreadable_state_hard_blocks_ts_gate(tmp_path):
    """GUARD (green at RED): unreadable state is a hard block in the TS gate (exit 1).

    The bash gate has no such contract today (its pipefail path exits 2), and the
    spec says unreadable state keeps today's behaviour, so bash is not pinned here.
    """
    _gate_project(tmp_path, "5", "FEATURE", "plan_review: pass\n" + _PHASE5_KEPT)
    state = tmp_path / "build-state.yaml"
    state.chmod(0o000)
    try:
        proc = _run_gate("ts", tmp_path)
    finally:
        state.chmod(0o644)
    assert proc.returncode == 1, proc.stdout + proc.stderr


@pytest.mark.parametrize("stale_findings", [False, True], ids=["no-findings", "stale-findings"])
@pytest.mark.parametrize("tier", TIERS)
@pytest.mark.parametrize("gate", GATES)
def test_ac10_stale_phase4_state_passes_through_and_writes_no_stale_line(gate, tier, stale_findings, tmp_path):
    files = {"research/findings-old.md": ""} if stale_findings else None
    _gate_project(tmp_path, "4", tier, scratch_files=files)
    proc = _run_gate(gate, tmp_path)
    assert proc.returncode == 0, f"{gate}/{tier}: {proc.stdout}{proc.stderr}"
    assert "scratchpad_stale" not in (tmp_path / "build-state.yaml").read_text()


@pytest.mark.parametrize("tier", TIERS)
@pytest.mark.parametrize("gate", GATES)
def test_ac10_phase45_applies_plan_review_check_to_every_tier(gate, tier, tmp_path):
    _gate_project(tmp_path, "4.5", tier)
    proc = _run_gate(gate, tmp_path)
    assert proc.returncode == 2, f"{gate}/{tier} must soft-block without plan_review: {proc.stdout}{proc.stderr}"
    assert "plan_review" in proc.stdout
    ok_dir = tmp_path / "ok"
    ok_dir.mkdir()
    _gate_project(ok_dir, "4.5", tier, "plan_review: pass\n")
    assert _run_gate(gate, ok_dir).returncode == 0


def _reason(proc) -> str:
    return json.loads(proc.stdout.strip().splitlines()[-1])["reason"]


@pytest.mark.parametrize("phase", ["4.5", "5"])
@pytest.mark.parametrize("tier", TIERS)
def test_ac10_bash_and_ts_soft_block_reasons_are_byte_identical(tier, phase, tmp_path):
    """F3: gate_backend=shadow compares stdout byte for byte; both gates must say
    `plan_review=pass (got: <missing>)` for the same missing field."""
    reasons = {}
    for gate in GATES:
        proj = tmp_path / gate
        proj.mkdir()
        _gate_project(proj, phase, tier, _PHASE5_KEPT if phase == "5" else "")
        proc = _run_gate(gate, proj)
        assert proc.returncode == 2, f"{gate}/{tier}/{phase}: {proc.stdout}{proc.stderr}"
        reasons[gate] = _reason(proc)
    assert reasons["bash"] == reasons["ts"], reasons
    assert "plan_review=pass (got: <missing>)" in reasons["ts"], reasons


# --- AC11 ------------------------------------------------------------------------

def test_ac11_environment_has_no_claude_and_no_api_key():
    assert "ANTHROPIC_API_KEY" not in os.environ
    assert shutil.which("claude") is None


# --- AC12: md flow and gate scripts ----------------------------------------------

_AC12_FILES = (
    [REPO_ROOT / "commands" / "build.md", REPO_ROOT / "skills" / "bytedigger" / "SKILL.md",
     REPO_ROOT / "examples" / "claude-code-skill" / "SKILL.md", BASH_GATE]
    + sorted((REPO_ROOT / "phases").glob("*.md"))
    + sorted(p for p in (REPO_ROOT / "templates").glob("*") if p.is_file())
    + sorted((REPO_ROOT / "scripts" / "ts").glob("*.ts"))
)
_RESIDUE_RE = re.compile(
    r"phase_1_discovery|phase_2_explore|phase_3_clarify|phase_4_architect|phase_4_approach"
    r"|phase_4_files_count|scratchpad_stale|build-architecture|approach-\*|findings-\*"
    r"|phase-(?:1-discovery|2-explore|3-clarify|4-architect)"
)


def test_ac12_no_dropped_phase_residue_in_md_and_gate_scripts():
    assert len(_AC12_FILES) > 10 and all(f.is_file() for f in _AC12_FILES), "fixture precondition"
    hits: dict[str, list[int]] = {}
    for f in _AC12_FILES:
        lines = [i for i, line in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1)
                 if _RESIDUE_RE.search(line)]
        if lines:
            hits[str(f.relative_to(REPO_ROOT))] = lines
    assert not hits, f"dropped-phase residue remains: {hits}"


_ARROW = r"\s*(?:->|→)\s*"
_FLOW_RE = re.compile(_ARROW.join(["0", r"0\.5", r"4\.5", "5", "6", "7"]))
_SIMPLE_SKIPS_RE = re.compile(r"skips?\s+(?:phases?\s+)?2\s*[-–]\s*4|Phases?\s+2\s*[-–]\s*4\)", re.IGNORECASE)
_SKIP_4_5_HEADING_RE = re.compile(r"^#+.*4\.5.*Skip if SIMPLE", re.IGNORECASE | re.MULTILINE)
_START_GATE_RE = re.compile(r"Phase 1 spec")


def test_ac12_build_md_states_the_single_flow_for_all_tiers():
    text = _text(REPO_ROOT / "commands" / "build.md")
    assert _FLOW_RE.search(text), "commands/build.md does not state the flow 0 -> 0.5 -> 4.5 -> 5 -> 6 -> 7"
    assert not _SKIP_4_5_HEADING_RE.search(text), "Phase 4.5 still has a 'Skip if SIMPLE' heading"


@pytest.mark.parametrize("rel", [
    "commands/build.md", "skills/bytedigger/SKILL.md", "phases/phase-0-classify.md",
    "examples/claude-code-skill/SKILL.md",
])
def test_ac12_no_simple_skips_phases_wording(rel):
    text = _text(REPO_ROOT / rel)
    assert text.strip(), "fixture precondition"
    assert not _SIMPLE_SKIPS_RE.search(text), f"{rel}: SIMPLE still skips phases 2-4"
    assert not _START_GATE_RE.search(text), f"{rel}: start gate still 'after its Phase 1 spec'"


_P45_SKIP_RE = re.compile(r"SIMPLE[^\n]*skip", re.IGNORECASE)
_MERGED_P1_RE = re.compile(r"merged with Phase 1", re.IGNORECASE)
_PLAN_REVIEW_TIER_SCOPED_RE = re.compile(r"plan_review[^\n]*\(?for FEATURE/COMPLEX\)?|Plan-Review Gate \(MANDATORY FEATURE/COMPLEX\)")
_EVERY_TIER_RE = re.compile(r"\b(?:every|all)\s+tiers?\b|\bSIMPLE/FEATURE/COMPLEX\b|\bincluding SIMPLE\b", re.IGNORECASE)


def test_ac12_phase_45_spec_md_requires_plan_review_for_every_tier():
    text = _text(REPO_ROOT / "phases" / "phase-45-spec.md")
    assert "plan_review" in text, "fixture precondition: plan review is documented"
    assert not _P45_SKIP_RE.search(text), "phase-45-spec.md still says SIMPLE skips the plan-review gate"
    assert not _MERGED_P1_RE.search(text), "phase-45-spec.md still merges SIMPLE spec writing into Phase 1"
    assert not _PLAN_REVIEW_TIER_SCOPED_RE.search(text), "plan review is still scoped to FEATURE/COMPLEX"
    assert _EVERY_TIER_RE.search(text), "phase-45-spec.md does not say plan review + plan_review apply to every tier"


def test_ac12_build_md_does_not_scope_plan_review_to_feature_complex():
    text = _text(REPO_ROOT / "commands" / "build.md")
    assert "plan_review" in text, "fixture precondition"
    assert not _PLAN_REVIEW_TIER_SCOPED_RE.search(text), "build.md still scopes plan review to FEATURE/COMPLEX"


def test_ac12_build_md_resumable_routes_stale_phases_1_4_to_phase_45():
    lines = [l for l in _text(REPO_ROOT / "commands" / "build.md").splitlines() if "Resumable" in l]
    assert lines, "fixture precondition: build.md has a Resumable paragraph"
    para = " ".join(lines)
    assert "Phase 2 (explore)" not in para, "Resumable still re-runs the explore phase"
    assert re.search(r"1\s*[-–]\s*4|1,\s*2,\s*3", para) and "4.5" in para, (
        "Resumable does not route a stale current_phase 1-4 to Phase 4.5")


# --- AC13: no stale test references (AST, not text) ------------------------------

_EXEMPT_FROM_AC13 = {
    "test_bd89_p2a_phases_1_4_dropped.py",
    "test_bd89_p1_devops_dropped.py",
    "test_bd89_p2b_one_spec_one_review_path.py",
}
_STRING_SINK_CALLS = {
    "patch", "object", "dict", "setattr", "delattr", "setitem", "import_module",
    "__import__", "find_spec", "getattr", "hasattr", "spec_from_file_location",
}


def _is_stale_ident(ident: str) -> bool:
    return any(ident.startswith(s) for s in DROPPED_STEMS) or ident in DELETED_NAMES


def _is_stale_string(s: str) -> bool:
    return any(stem in s for stem in DROPPED_STEMS) or any(n in s for n in DELETED_NAMES)


def _stale_refs(tree: ast.AST) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out += [(node.lineno, a.name) for a in node.names if _is_stale_string(a.name)]
        elif isinstance(node, ast.ImportFrom):
            if node.module and _is_stale_string(node.module):
                out.append((node.lineno, node.module))
            out += [(node.lineno, a.name) for a in node.names if _is_stale_ident(a.name)]
        elif isinstance(node, ast.Attribute) and _is_stale_ident(node.attr):
            out.append((node.lineno, node.attr))
        elif isinstance(node, ast.Name) and _is_stale_ident(node.id):
            out.append((node.lineno, node.id))
        elif isinstance(node, ast.Call):
            fn = node.func
            fname = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if fname in _STRING_SINK_CALLS:
                for arg in list(node.args) + [k.value for k in node.keywords]:
                    for sub in ast.walk(arg):
                        if isinstance(sub, ast.Constant) and isinstance(sub.value, str) \
                                and _is_stale_string(sub.value):
                            out.append((node.lineno, sub.value))
    return out


def test_ac13_no_stale_test_references_ast():
    files = [f for f in sorted(TESTS_DIR.rglob("*.py"))
             if "__pycache__" not in f.parts and f.name not in _EXEMPT_FROM_AC13]
    assert len(files) > 100, "fixture precondition: scanned the engine test tree"
    hits: dict[str, list[tuple[int, str]]] = {}
    for f in files:
        try:
            tree = ast.parse(_text(f), filename=str(f))
        except SyntaxError:
            continue
        found = _stale_refs(tree)
        if found:
            hits[f.name] = found[:3]
    assert not hits, f"{len(hits)} test files still reference deleted modules/names: {hits}"


# --- AC14: hardcoded counts ------------------------------------------------------

def _module_assignment(path: Path, name: str):
    tree = ast.parse(_text(path), filename=str(path))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError(f"{path.name}: no module-level {name}")


def test_ac14_bd44_expected_workflows_is_10():
    assert _module_assignment(TESTS_DIR / "test_bd44_package_namespace.py", "EXPECTED_WORKFLOWS") == 10


@pytest.mark.parametrize("stem", ["test_bd89_p1_devops_dropped", "test_bd89_p2b_one_spec_one_review_path"])
def test_ac14_frozen_registry_sets_are_the_same_10(stem):
    assert set(_module_assignment(TESTS_DIR / f"{stem}.py", "FROZEN_REGISTRY")) == FROZEN_REGISTRY


def _compare_constants(path: Path, left_src: str) -> list[int]:
    tree = ast.parse(_text(path), filename=str(path))
    vals: list[int] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Compare) and ast.unparse(node.left) == left_src:
            vals += [c.value for c in node.comparators
                     if isinstance(c, ast.Constant) and isinstance(c.value, int)]
    return vals


def test_ac14_bd141_pins_are_12_drivers_and_15_dispatches():
    path = TESTS_DIR / "test_bd141_p4d_role_template_injections.py"
    assert _compare_constants(path, "len(_DRIVERS)") == [12]
    assert _compare_constants(path, "len(rows)") == [15]


def test_ac14_no_other_registry_count_of_14():
    me = Path(__file__).resolve()
    hits: dict[str, list[int]] = {}
    for f in sorted(TESTS_DIR.rglob("*.py")):
        if f.resolve() == me or "__pycache__" in f.parts:
            continue
        try:
            tree = ast.parse(_text(f), filename=str(f))
        except SyntaxError:
            continue
        lines = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            sides = [node.left, *node.comparators]
            if any(isinstance(s, ast.Constant) and s.value == 14 and type(s.value) is int for s in sides) \
                    and any(re.search(r"regist|EXPECTED_WORKFLOWS|FROZEN_REGISTRY", ast.unparse(s)) for s in sides):
                lines.append(node.lineno)
        if lines:
            hits[f.name] = lines
    assert not hits, f"registry-count pins of 14 remain: {hits}"


# --- AC15: probe still fires on the request alone ---------------------------------

_RE_ENTRY = "RE-ENTRY AC MANDATE"
_PY_RULE = "PYTHON WIRING RULE"
_FMT_RULE = "FORMAT-CONVERSION RULE"
_NEUTRAL = "Add a greeting banner"
_STATEFUL_Q = "Add a durable cache for the greeting banner"
_PY_Q = "Edit the greeting in banner.py"
_FMT_Q = "Migrate the greeting storage format"
_ALL_TOKENS_TEXT = "The sentinel cache must resume from banner.py after a storage format migration.\n"


def _triggers(prompt: str) -> set[str]:
    return {name for name, marker in (("reentry", _RE_ENTRY), ("py", _PY_RULE), ("fmt", _FMT_RULE))
            if marker in prompt}


def _spec_prompt_for(tmp_path: Path, question: str, *, arch_text: str | None = None,
                     decision_text: str | None = None, monkeypatch=None) -> str:
    from bytedigger_engine.workflows import phase_45_spec

    repo = _repo(tmp_path)
    scratch = tmp_path / "s"
    extra = {}
    if decision_text is not None:
        doc = repo / "decision.md"
        doc.write_text(decision_text, encoding="utf-8")
        extra["decision_doc"] = str(doc)
        monkeypatch.chdir(repo)  # pre-stage the containment root (1i); no race
    ctx = _ctx(scratch, repo, question=question, **extra)
    if arch_text is not None:
        _seed_scratch(scratch, arch_text=arch_text)
    return _prompt(phase_45_spec._build_spec_prompt(ctx, None))


def test_ac15_guard_probe_triggers_fire_on_the_feature_request(tmp_path, monkeypatch):
    """GUARD (green at RED): the request alone drives each of the three consumers."""
    assert _triggers(_spec_prompt_for(tmp_path / "a", _STATEFUL_Q)) >= {"reentry"}
    assert "py" in _triggers(_spec_prompt_for(tmp_path / "b", _PY_Q))
    assert "fmt" in _triggers(_spec_prompt_for(tmp_path / "c", _FMT_Q))


def test_ac15_guard_neutral_request_triggers_nothing(tmp_path):
    """GUARD (green at RED): a request matching none yields none of the three additions."""
    assert _triggers(_spec_prompt_for(tmp_path, _NEUTRAL)) == set()


def test_ac15_architecture_doc_text_does_not_trigger_the_probe(tmp_path):
    prompt = _spec_prompt_for(tmp_path, _NEUTRAL, arch_text=_ALL_TOKENS_TEXT)
    assert _triggers(prompt) == set(), (
        "architecture/architecture.md text still feeds the probe: "
        f"{sorted(_triggers(prompt))}")


def test_ac15_decision_doc_text_triggers_the_probe(tmp_path, monkeypatch):
    prompt = _spec_prompt_for(tmp_path, _NEUTRAL, decision_text=_ALL_TOKENS_TEXT, monkeypatch=monkeypatch)
    assert "sentinel cache must resume" in prompt, "fixture precondition: decision doc is inlined"
    assert _triggers(prompt) == {"reentry", "py", "fmt"}, sorted(_triggers(prompt))


# --- AC16: a pre-upgrade resume is refused loudly (edge 1) ------------------------

@pytest.mark.parametrize("dropped", DROPPED_STAGES)
def test_ac16_guard_stale_phases_csv_resume_is_refused_loudly(dropped, tmp_path, monkeypatch, capsys):
    """GUARD (green at RED): the real run.main refuses a dropped stage name.

    A resume driven by a pre-upgrade PHASES_CSV naming phase_1_discovery (etc.) hits
    the real registry (engine.py KeyError "not registered") and the real run.main
    maps it to error_code E_NOT_REGISTERED, exit 2.  Only the DBOS plumbing is
    replaced (init/teardown no-ops; the durable wrapper delegates to the real
    engine.execute); registry, engine and main's error mapping are production code.
    """
    from bytedigger_engine import run

    ctx_json = json.dumps({
        "tenant_id": "hal", "question": "resume", "session_id": "p2a-ac16",
        "persona": "hal",
        "org_config": {"scratchpad_dir": str(tmp_path / "scratch"), "complexity": "FEATURE"},
    })
    eng = run.make_engine(None)
    assert "phase_45_spec" in eng.registered(), "fixture precondition: real registry"

    def _durable(name, ctx_dict, run_id, event_log_path=None):
        eng.execute(name, _ctx_plain(tmp_path / "scratch"))  # KeyError for a dropped name
        raise AssertionError(f"{name} executed: a dropped stage must not resolve")

    monkeypatch.setattr(run, "init_dbos", lambda *a, **k: None)
    monkeypatch.setattr(run, "teardown_dbos", lambda *a, **k: None)
    monkeypatch.setattr(run, "execute_durable_workflow", _durable)
    monkeypatch.setattr(sys, "argv", ["run.py", "--workflow", dropped, "--ctx-json", ctx_json])
    rc = run.main()
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert rc == 2, out
    assert out["error_code"] == "E_NOT_REGISTERED", out
    assert "not registered" in out["error"] and dropped in out["error"], out
