"""RED tests for bd#89 P2b -- one spec path, one review path.

Spec: docs/decisions/2026-10-02-bd89-p2b-drop-spec-lite-and-simple-fastpath.md
(AC1-AC11 + AC1b).  Drops the SIMPLE-only lite spec workflow and the SIMPLE
fast-path review workflow from the engine.  The needle strings are assembled by
concatenation in this file so that AC9 (corpus closure) does not match its own
source.

AC -> test map
--------------
AC1   test_ac1_register_all_registers_exactly_the_frozen_set
AC1b  test_ac1b_real_engine_registry_resolves_kept_and_refuses_dropped
AC2   test_ac2_dropped_module_files_are_gone                 (x2 params)
      test_ac2_dropped_modules_are_not_importable           (x2 params)
      test_ac2_core_manifest_lists_neither_dropped_module
      test_ac2_every_core_module_entry_resolves_to_a_file
      test_ac2_guard_flags_and_routed_modules_name_neither_stem   (guard, green now)
AC3   test_ac3_oracle_sets_are_exactly_the_full_path
AC4   test_ac4_no_spec_lite_timeout_keys_and_kept_rows_unchanged
AC5   test_ac5_guard_shared_error_codes_still_registered    (guard, green now)
      test_ac5_error_code_descriptions_do_not_name_dropped_workflow  (registry + 2 md)
      test_ac5_the_two_error_codes_md_files_are_byte_identical (guard)
AC6   test_ac6_guard_frozen_simple_skip_and_kill_switch     (guard, green now)
AC7   test_ac7_no_text_residue_in_engine_py_and_error_codes_md
AC8   test_ac8a_guard_full_workflow_has_every_deterministic_gate_in_order
      test_ac8b_guard_real_prelint_emits_result_into_real_event_log
      test_ac8c_guard_enforce_on_gives_simple_the_recoverable_retry
AC9   test_ac9_no_other_test_file_references_dropped_workflows
      test_ac9_bd44_expected_workflows_is_14
      test_ac9_p1_frozen_registry_is_the_same_14
AC10  autouse fixture `_no_claude_no_api_key` + test_ac10_environment_has_no_claude_and_no_api_key
AC11  test_ac11_configuration_doc_has_no_fastpath_or_simple_rewrite_needles

Expected RED today: AC1, AC1b, AC2 (files/importable/manifest), AC3, AC4, AC5
(descriptions), AC7, AC9 (corpus + counts are edited in this RED commit, but the
seven whole-file retirements are still on disk until the orchestrator `git rm`s
them), AC11.  Green today by design: the guards labeled above, AC8 (a)(b)(c),
AC10, the GAP-4 port section.

Section "GAP-4 port" re-homes the retired lite step5 telemetry cases onto the
kept spec path (`phase_45_spec._write_review_doc`).  They PASS today (the
behavior already exists, only the tests were missing; spec 5 GAP-4).

Retired without a main-path twin, recorded here per spec 6.4:
  GAP-1 citation auto-correct (32ED4070): no twin, not ported.
  GAP-2 block-signal downgrade (39CAFF09): intentionally dropped.
  GAP-3 lite loop composite (`build_review_loop_contract` +
        `consume_recoverable_retry`): the full path has no such composite
        (checked: it uses its own retry model), so the cite-enforce L2/L4/L5
        cases and the retry-budget `ac18` case have no main-path behavior to
        port.  RETIRED.
  GAP-5 reviewer cross-check text: absent from the full review schema, so the
        e94afd31 AC1-AC4 cases were retired and only AC5 kept.

No dropped module is imported at module scope (the file collects before and
after GREEN); they are probed through importlib.util.find_spec and file paths.
No singleton resource is raced (workflows.md 1i): cwd, env and PATH are
pre-staged by fixtures before the unit under test runs.
"""
from __future__ import annotations

import importlib
import importlib.util
import json
import os
import re
import shutil
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

# Needles are assembled so this file never contains them verbatim (AC9).
_LITE_SPEC = "phase_45_spec_" + "lite"
_FASTPATH = "phase_6_review_simple_" + "fastpath"
_RESIDUE_RE = re.compile("spec_" + "lite|" + _LITE_SPEC + "|simple_" + "fastpath|spec-" + "lite")
_AC9_NEEDLES = (_LITE_SPEC, _FASTPATH, "spec_" + "lite.")
DROPPED_STEMS = (_LITE_SPEC, _FASTPATH)

FROZEN_REGISTRY = {
    "echo", "phase_0_research", "phase_05_inject", "phase_1_discovery",
    "phase_2_explore", "phase_3_clarify", "phase_4_architect", "phase_45_spec",
    "phase_5_implement", "phase_5_integrity",
    "phase_6_fix_integrity", "phase_6_review",
    "phase_7_synthesize", "phase_8_post_deploy",
}


# --- AC10: no `claude` binary, no API key, for every test in this file ---------

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


# --- shared helpers --------------------------------------------------------------

class _FakeEngine:
    def __init__(self) -> None:
        self.names: list[str] = []

    def register(self, name, wf) -> None:
        self.names.append(name)


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _ctx(scratch: Path) -> WorkflowContext:
    scratch.mkdir(parents=True, exist_ok=True)
    return WorkflowContext(
        tenant_id="hal", scope=None, db_path=None,
        org_config={"scratchpad_dir": str(scratch), "complexity": "SIMPLE"},
        question="add feature X", session_id="test-bd89-p2b", persona="hal",
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
    assert "phase_45_spec" in names and "phase_6_review" in names, names
    ctx = _ctx(tmp_path / "scratch")
    for dropped in DROPPED_STEMS:
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


def _core_modules() -> list[str]:
    data = json.loads(_text(ENGINE_PY / "core_manifest.json"))
    mods = data["core_modules"]
    assert mods and all(isinstance(m, str) for m in mods), "fixture precondition"
    return mods


def test_ac2_core_manifest_lists_neither_dropped_module():
    listed = [m for m in _core_modules() if any(stem in m for stem in DROPPED_STEMS)]
    assert not listed, f"core_manifest.json core_modules still lists: {listed}"


# Measured at base (P1 tree): every core_modules entry resolves, so the frozen
# already-dangling set is empty.  `stale` is checked first (test above).
_FROZEN_DANGLING: frozenset[str] = frozenset()


def test_ac2_every_core_module_entry_resolves_to_a_file():
    stale = [m for m in _core_modules() if any(stem in m for stem in DROPPED_STEMS)]
    assert not stale, f"core_modules routes to dropped modules: {stale}"
    dangling = [m for m in _core_modules()
                if m not in _FROZEN_DANGLING and not (PKG_DIR / m).is_file()]
    assert not dangling, f"core_modules entries point at missing files: {dangling}"


def test_ac2_guard_flags_and_routed_modules_name_neither_stem():
    """GUARD (green at RED on purpose): the flag catalog never routed to either module."""
    from bytedigger_engine import flags_catalog

    flag_modules = [s["module"] for s in flags_catalog.FLAGS.values()
                    if isinstance(s.get("module"), str)]
    assert flag_modules, "fixture precondition: catalog has module entries"
    named = [m for m in flag_modules + list(flags_catalog.ROUTED_MODULES)
             if any(stem in m for stem in DROPPED_STEMS)]
    assert not named, f"catalog names a dropped module: {named}"


# --- AC3 -------------------------------------------------------------------------

def test_ac3_oracle_sets_are_exactly_the_full_path():
    from bytedigger_engine.conformance import oracle

    assert oracle.ORACLE_WORKFLOWS == frozenset({"phase_45_spec"}), oracle.ORACLE_WORKFLOWS
    assert oracle.IMPLEMENTING_WORKFLOWS == frozenset({"phase_5_implement"})


# --- AC4 -------------------------------------------------------------------------

def test_ac4_no_spec_lite_timeout_keys_and_kept_rows_unchanged():
    from bytedigger_engine.lib import timeout_policy

    policy = timeout_policy.DEFAULT_POLICY
    stale = [k for k in policy if k.startswith("spec_" + "lite.")]
    assert not stale, f"timeout policy still has: {stale}"
    assert policy["spec.writer"] == {
        "base": 600, "COMPLEX": 1800, "opus": 900, "override_key": "spec_llm_timeout_sec",
    }
    assert policy["spec.reviewer"] == {
        "base": 300, "FEATURE": 600, "COMPLEX": 900, "opus": 600,
        "override_key": "review_llm_timeout_sec",
    }


# --- AC5 -------------------------------------------------------------------------

_SHARED_CODES = (
    "E_INJECTION_MISSING", "E_MISSING_PREV_DATA", "E_REVIEW_FAILED",
    "E_REVIEW_UNPARSEABLE", "E_ROLE_TEMPLATE_INVALID", "E_VALIDATION_RETRY",
)
_REWORDED_CODES = ("E_INJECTION_MISSING", "E_REVIEW_UNPARSEABLE")
_ERROR_CODES_MD = [ENGINE_PY / "ERROR_CODES.md", PKG_DIR / "ERROR_CODES.md"]


def test_ac5_guard_shared_error_codes_still_registered():
    """GUARD (green at RED): no code may be deleted by P2b."""
    from bytedigger_engine import error_codes

    missing = [c for c in _SHARED_CODES if c not in error_codes.ERROR_CODES]
    assert not missing, f"over-deleted codes: {missing}"


def _description_clean(desc: str) -> bool:
    return not any(n in desc for n in ("spec_" + "lite", _LITE_SPEC))


def test_ac5_error_code_descriptions_do_not_name_dropped_workflow_in_registry():
    from bytedigger_engine import error_codes

    dirty = {c: error_codes.ERROR_CODES[c] for c in _REWORDED_CODES
             if not _description_clean(error_codes.ERROR_CODES[c])}
    assert not dirty, f"descriptions still name the dropped workflow: {dirty}"


@pytest.mark.parametrize("md", _ERROR_CODES_MD, ids=["engine_py", "bytedigger_engine"])
def test_ac5_error_code_descriptions_do_not_name_dropped_workflow_in_md(md):
    text = _text(md)
    dirty = []
    for code in _REWORDED_CODES:
        m = re.search(rf"^- `{code}` — (.*)$", text, re.MULTILINE)
        assert m, f"fixture precondition: {md} documents {code}"
        if not _description_clean(m.group(1)):
            dirty.append(code)
    assert not dirty, f"{md} still names the dropped workflow in: {dirty}"


def test_ac5_the_two_error_codes_md_files_are_byte_identical():
    """GUARD (green at RED, must stay green): the md mirror contract."""
    a, b = _ERROR_CODES_MD
    assert a.read_bytes() == b.read_bytes()


# --- AC6 (guard) -----------------------------------------------------------------

_FROZEN_TEXT = (
    "# Frozen Spec\n\n"
    "**Status:** FROZEN (§1-PREFLIGHT done)\n\n"
    "### §3 Acceptance Criteria\n"
    "| # | AC |\n|---|----|\n| AC1 | thing |\n"
)


def test_ac6_guard_frozen_simple_skip_and_kill_switch(tmp_path, monkeypatch):
    """GUARD (green at RED): the GH531 SIMPLE relax in skip_logic is untouched."""
    from bytedigger_engine.skip_logic import should_skip_phase

    doc = tmp_path / "decision.md"
    doc.write_text(_FROZEN_TEXT, encoding="utf-8")
    monkeypatch.chdir(tmp_path)  # pre-stage containment root (1i); no race
    cfg = {"decision_doc": str(doc), "complexity": "SIMPLE"}

    monkeypatch.delenv("HAL_FROZEN_SHORT_CIRCUIT", raising=False)
    skip, path = should_skip_phase(cfg)
    assert skip is True and path is not None and Path(path).samefile(doc)

    monkeypatch.setenv("HAL_FROZEN_SHORT_CIRCUIT", "0")
    skip, path = should_skip_phase(cfg)
    assert skip is False and path is None


# --- AC7 -------------------------------------------------------------------------

def test_ac7_no_text_residue_in_engine_py_and_error_codes_md():
    files = sorted(PKG_DIR.rglob("*.py")) + _ERROR_CODES_MD
    assert len(files) > 50, "fixture precondition: scanned the engine package"
    hits: dict[str, list[int]] = {}
    for f in files:
        lines = [i for i, line in enumerate(_text(f).splitlines(), 1) if _RESIDUE_RE.search(line)]
        if lines:
            hits[str(f.relative_to(ENGINE_PY))] = lines
    assert not hits, f"text residue of the dropped workflows remains: {hits}"


# --- AC8 (guards: SIMPLE is served by the full path) -----------------------------

def test_ac8a_guard_full_workflow_has_every_deterministic_gate_in_order():
    from bytedigger_engine.workflows import phase_45_spec

    names = [getattr(s, "name", None) for s in phase_45_spec.phase_45_spec_workflow().steps]
    expected = [
        "verify_spec_cite_prelint", "verify_spec_citations", "verify_spec_cite_lint",
        "verify_spec_scope_inverse", "verify_spec_coverage", "verify_spec_ac_dsl",
        "verify_spec_reality", "write_review_doc", "gate_on_review",
    ]
    pos = []
    for n in expected:
        assert n in names, f"{n} missing from {names}"
        pos.append(names.index(n))
    assert pos == sorted(pos), f"gates out of order: {list(zip(expected, pos))}"


def _prelint_fixture(tmp_path: Path):
    (tmp_path / "helpers.py").write_text("def real_helper():\n    pass\n")
    spec = tmp_path / "spec.md"
    spec.write_text(
        "The function `real_helper` in `helpers.py` performs validation.\n"
        "The function `ZzzHallucinated_p2b` in `helpers.py` performs the lookup.\n"
    )
    ctx = SimpleNamespace(org_config={"hal_root": str(tmp_path), "complexity": "SIMPLE"})
    prev = StepResult(status="ok", data={"spec_path": str(spec), "cycle": 1},
                      duration_ms=0, step_name="write_spec_doc")
    return ctx, prev


def test_ac8b_guard_real_prelint_emits_result_into_real_event_log(tmp_path, monkeypatch):
    from bytedigger_engine.event_log import EventLog
    from bytedigger_engine.workflows import phase_45_spec

    monkeypatch.delenv("HAL_SPEC_CITE_PRELINT_ENFORCE", raising=False)
    ctx, prev = _prelint_fixture(tmp_path)
    log = EventLog(tmp_path / "events.jsonl")  # real sink, not a mocked emitter
    telemetry_ctx.set_current_run(event_log=log, run_id="p2b-ac8b",
                                  step_name="verify_spec_cite_prelint", phase="phase_45_spec")
    try:
        sr = phase_45_spec._verify_spec_cite_prelint(ctx, prev)
    finally:
        telemetry_ctx.clear_current_run()

    results = [e["payload"] for e in log.read_all() if e["event_type"] == "spec_cite_prelint_result"]
    assert len(results) == 1, log.read_all()
    assert results[0]["unresolved_count"] >= 1
    assert "ZzzHallucinated_p2b" in results[0]["symbols"]
    assert sr.status == "ok"
    assert "ZzzHallucinated_p2b" in sr.data["spec_cite_prelint_unresolved"]


def test_ac8c_guard_enforce_on_gives_simple_the_recoverable_retry(tmp_path, monkeypatch):
    from bytedigger_engine.workflows import phase_45_spec

    monkeypatch.setenv("HAL_SPEC_CITE_PRELINT_ENFORCE", "1")
    ctx, prev = _prelint_fixture(tmp_path)
    sr = phase_45_spec._verify_spec_cite_prelint(ctx, prev)
    assert sr.status == "error" and sr.recoverable is True, (sr.status, sr.recoverable)
    assert sr.error_code == "E_SPEC_CITE_PRELINT_RETRY"
    assert "ZzzHallucinated_p2b" in sr.data["findings"]


# --- AC9 -------------------------------------------------------------------------

def test_ac9_no_other_test_file_references_dropped_workflows():
    me = Path(__file__).resolve()
    hits: dict[str, list[str]] = {}
    for f in sorted(TESTS_DIR.rglob("*")):
        if not f.is_file() or f.resolve() == me or "__pycache__" in f.parts:
            continue
        try:
            text = _text(f)
        except (UnicodeDecodeError, OSError):
            continue
        found = [n for n in _AC9_NEEDLES if n in text]
        if found:
            hits[str(f.relative_to(TESTS_DIR))] = found
    assert not hits, f"tests still reference dropped workflows: {hits}"


def _load_sibling(stem: str):
    """Load a sibling test module by file path (no sys.path reliance)."""
    spec = importlib.util.spec_from_file_location(f"_p2b_probe_{stem}", TESTS_DIR / f"{stem}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_ac9_bd44_expected_workflows_is_14():
    assert _load_sibling("test_bd44_package_namespace").EXPECTED_WORKFLOWS == 14


def test_ac9_p1_frozen_registry_is_the_same_14():
    assert set(_load_sibling("test_bd89_p1_devops_dropped").FROZEN_REGISTRY) == FROZEN_REGISTRY


# --- AC10 ------------------------------------------------------------------------

def test_ac10_environment_has_no_claude_and_no_api_key():
    assert "ANTHROPIC_API_KEY" not in os.environ
    assert shutil.which("claude") is None


# --- AC11 ------------------------------------------------------------------------

def test_ac11_configuration_doc_has_no_fastpath_or_simple_rewrite_needles():
    text = _text(REPO_ROOT / "docs" / "configuration.md")
    assert text.strip(), "fixture precondition: docs/configuration.md is non-empty"
    present = [n for n in (_FASTPATH, "maybe_rewrite_simple_spec_prompt") if n in text]
    assert not present, f"docs/configuration.md still names: {present}"


# ===================================================================================
# GAP-4 port: step5 telemetry cases, re-homed onto the kept spec path.
# Ported from the retired lite step5 test file.  These PASS today: the behavior
# exists at phase_45_spec._write_review_doc_structured_gate; only tests were missing.
# ===================================================================================

_VERDICT_SHIP_HEADER = "## Verdict\nSHIP\n"
_VERDICT_REVISE_HEADER = "## Verdict\nREVISE\n"
_FINDINGS_EMPTY = '## Findings (structured)\n```json\n[]\n```\n'
_FINDINGS_ONE = (
    '## Findings (structured)\n```json\n'
    '[{"id":"1","type":"missing","evidence":"x","required_action":"y"}]\n```\n'
)
_FINDINGS_MALFORMED = '## Findings (structured)\n```json\n{ malformed\n```\n'
_STEP5_EVENTS = ("spec_findings_block_absent", "spec_structured_verdict", "spec_verdict_drift")


def _review_prev(tmp_path: Path, raw: str, cycle: int = 1) -> StepResult:
    doc_path = tmp_path / "build-plan-review.md"
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    return StepResult(
        status="ok",
        data={
            "raw_response": raw,
            "doc_path": str(doc_path),
            "spec_path": str(tmp_path / "build-spec.md"),
            "cycle": cycle,
        },
        duration_ms=0,
        step_name="invoke_review_llm",
    )


def _run_review_doc(tmp_path: Path, monkeypatch, raw: str, cycle: int = 1) -> list[dict]:
    from bytedigger_engine.workflows import phase_45_spec

    captured: list[dict] = []
    monkeypatch.setattr(phase_45_spec, "_emit_safe",
                        lambda et, p: captured.append({"type": et, "payload": p}))
    phase_45_spec._write_review_doc(None, _review_prev(tmp_path, raw, cycle))
    return captured


def _of(captured: list[dict], etype: str) -> list[dict]:
    return [e for e in captured if e["type"] == etype]


def test_gap4_block_absent_when_structured_block_missing(tmp_path, monkeypatch):
    cap = _run_review_doc(tmp_path, monkeypatch, _VERDICT_SHIP_HEADER)
    assert len(_of(cap, "spec_findings_block_absent")) == 1, cap
    assert not _of(cap, "spec_structured_verdict"), cap
    assert not _of(cap, "spec_verdict_drift"), cap


def test_gap4_block_absent_when_structured_block_malformed(tmp_path, monkeypatch):
    cap = _run_review_doc(tmp_path, monkeypatch, _VERDICT_REVISE_HEADER + _FINDINGS_MALFORMED)
    assert len(_of(cap, "spec_findings_block_absent")) == 1, cap
    assert not _of(cap, "spec_structured_verdict"), cap


def test_gap4_no_drift_when_markdown_and_structured_verdicts_agree(tmp_path, monkeypatch):
    cap = _run_review_doc(tmp_path, monkeypatch, _VERDICT_REVISE_HEADER + _FINDINGS_ONE)
    assert len(_of(cap, "spec_structured_verdict")) == 1, cap
    assert _of(cap, "spec_structured_verdict")[0]["payload"]["structured_verdict"] == "REVISE"
    assert not _of(cap, "spec_verdict_drift"), cap


def test_gap4_no_drift_when_markdown_verdict_unknown(tmp_path, monkeypatch):
    cap = _run_review_doc(tmp_path, monkeypatch, _FINDINGS_EMPTY)
    events = _of(cap, "spec_structured_verdict")
    assert len(events) == 1, cap
    assert events[0]["payload"]["markdown_verdict"] == "UNKNOWN"
    assert not _of(cap, "spec_verdict_drift"), cap


def test_gap4_cycle2_emits_no_step5_events(tmp_path, monkeypatch):
    cap = _run_review_doc(tmp_path, monkeypatch, _VERDICT_SHIP_HEADER + _FINDINGS_EMPTY, cycle=2)
    assert [e for e in cap if e["type"] in _STEP5_EVENTS] == [], cap
