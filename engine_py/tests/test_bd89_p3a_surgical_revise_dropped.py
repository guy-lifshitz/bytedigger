"""RED tests for bd#89 P3a -- drop surgical revise and the restricted cycle-2 reviewer.

Spec: docs/decisions/2026-10-02-bd89-p3a-drop-surgical-revise.md (AC1-AC14, plus the
section 6 Q4 in-flight-resume degrade AC, numbered AC15 here).

AC -> test map
--------------
AC1  test_ac1_dropped_plugin_files_are_gone / test_ac1_dropped_plugin_modules_not_importable
     test_ac1_package_namespace_drops_surgical_and_restricted_reviewer
     test_ac1_guard_kept_plugin_surface_still_importable              (GUARD, green today)
AC2  test_ac2_phase_45_spec_namespace_has_no_dropped_symbols
     test_ac2_guard_phase_45_spec_keeps_delta_and_parser_symbols      (GUARD, green today)
     test_ac2_no_residue_of_dropped_lanes_in_engine_sources
AC3  test_ac3_flags_catalog_drops_both_kill_switches
     test_ac3_guard_surviving_flags_still_declared                    (GUARD, green today)
     test_ac3_dropped_env_switches_are_inert_for_writer_and_reviewer_prompts
AC4  test_ac4_cycle2_writer_is_the_delta_lane_through_the_real_chain
     test_ac4_legacy_surgical_fallback_marker_is_ignored
     test_ac4_gate_retry_takes_the_same_delta_lane
AC5  test_ac5_invoke_spec_llm_always_unlinks_and_grants_full_tools
AC6  test_ac6_write_spec_doc_ignores_stale_surgical_state_with_worker_file
     test_ac6_write_spec_doc_ignores_stale_surgical_state_without_worker_file
     test_ac6_guard_write_spec_doc_without_stale_keys                 (GUARD, green today)
AC7  test_ac7_cycle2_reviewer_prompt_is_the_cycle1_prompt
AC8  test_ac8_role_template_reaches_cycle2_reviewer
AC9  test_ac9_guard_full_format_review_at_cycle2_takes_classic_verdict   (GUARD)
     test_ac9_guard_cycle2_low_axis_ship_is_downgraded                   (GUARD)
AC10 test_ac10_cycle2_end_to_end_no_surgical_artifacts_or_events
AC11 test_ac11_phase_doc_and_configuration_doc_reworded
     test_ac11_no_dropped_lane_wording_in_repo_markdown
AC12 test_ac12_mypy_strict_list_has_no_restricted_reviewer_row
     test_ac12_guard_mypy_strict_rows_of_the_plugin_resolve          (GUARD)
     test_ac12_guard_core_manifest_names_no_dropped_module           (GUARD)
     test_ac12_guard_no_error_code_deleted                           (GUARD)
AC13 test_ac13_dropped_test_files_are_gone
     test_ac13_guard_test_corpus_has_no_stale_reference               (GUARD once the sibling edits and
                                                                       the git rm of the four files land)
AC14 autouse fixture `_no_claude_no_api_key` + test_ac14_environment_has_no_claude_and_no_api_key (GUARD)
AC15 test_ac15_inflight_resume_with_stale_surgical_state_degrades_to_full_revise (spec section 6 Q4)

Forcing reasons today: cycle-2 writer takes the surgical lane (patch-array contract, Write tool
withheld, `surgical_revise` data key, sidecar `surgical-delta-cycle-N.json`); the cycle-2 reviewer
takes the restricted/delta lane (`restricted_reviewer`, no role template); three plugin files, two
flags, a lint row and two doc lines exist.

No dropped module is imported at module scope (the file collects before and after GREEN); they are
probed through importlib.util.find_spec and file paths only. No sys.path mutation, no conftest
import. No singleton resource is raced (workflows.md 1i): the env is pre-staged by the autouse
fixture, every fixture is written to tmp_path before the unit under test runs, and the only
doubled seam is `invoke_llm_subprocess` (a dependency of the UUTs, not a UUT).

Local names in this file are chosen not to collide with the stale identifiers the AC13 scan looks for.
"""
from __future__ import annotations

import ast
import importlib
import importlib.util
import json
import os
import re
import shutil
import sys
from pathlib import Path

import pytest

from bytedigger_engine import telemetry_ctx
from bytedigger_engine.contracts import StepResult, WorkflowContext

TESTS_DIR = Path(__file__).resolve().parent
ENGINE_PY = TESTS_DIR.parent
PKG_DIR = ENGINE_PY / "bytedigger_engine"
REPO_ROOT = ENGINE_PY.parent
PLUGIN_DIR = PKG_DIR / "lib" / "plugins" / "checklist_convergence"
PLUGIN_PKG = "bytedigger_engine.lib.plugins.checklist_convergence"

DROPPED_PLUGIN_STEMS = ("surgical_revise", "delta_reviewer_prompt", "restricted_reviewer_prompt")
DROPPED_TEST_FILES = (
    "test_gh592_surgical_revise.py",
    "test_gh605_delta_rereview.py",
    "test_gh638_delta_rereview_content_vote.py",
    "test_gh726_surgical_parse_failsoft.py",
)

BASE_SPEC = (
    "## Context\nSection 1 body, prior cycle-1 spec text.\n\n"
    "## Findings\nOLD_FRAGMENT_TO_REPLACE_F1\n\n"
    "## Out of Scope\nSection 3 body.\n"
)
REWRITTEN_SPEC = (
    "## Context\nRewritten cycle-2 body written whole by the worker.\n\n"
    "## Findings\nCONCRETE_TEXT_F1\n\n"
    "## Out of Scope\nSection 3 body.\n"
)
STRUCTURED = [{
    "id": "F1", "type": "gap",
    "evidence": "spec Findings has an unresolved placeholder fragment",
    "required_action": "replace OLD_FRAGMENT_TO_REPLACE_F1 with concrete text",
}]
STRUCTURED_REVIEW_TEXT = (
    "## Verdict\nREVISE\n\n"
    "## Findings (structured)\n```json\n" + json.dumps(STRUCTURED) + "\n```\n\n"
    "## Findings\n- placeholder fragment must be replaced\n"
)
SHIP_REVIEW_TEXT = (
    "## Verdict\nSHIP\n\n"
    "## Scores\n```json\n"
    '{"completeness": 4, "clarity": 4, "feasibility": 4, "issue_alignment": 4, "consistency": 4}\n'
    "```\n"
)
LOW_AXIS_SHIP_REVIEW_TEXT = (
    "## Verdict\nSHIP\n\n"
    "## Scores\n```json\n"
    '{"completeness": 2, "clarity": 4, "feasibility": 4, "issue_alignment": 4, "consistency": 4}\n'
    "```\n"
)
PATCH_ARRAY_RAW = "```json\n" + json.dumps(
    [{"finding_id": "F1", "old": "OLD_FRAGMENT_TO_REPLACE_F1", "new": "PATCHED_FRAGMENT_F1"}]
) + "\n```\n"
SURGICAL_EVENTS = ("surgical_revise_applied", "surgical_revise_fallback",
                   "delta_rereview_used", "delta_rereview_fallback")


# --- AC14: no `claude` binary, no API key, for every test in this file ---------

@pytest.fixture(autouse=True)
def _no_claude_no_api_key(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    kept: list[str] = []
    for d in (str(Path(sys.executable).parent), "/usr/bin", "/bin"):
        if d in kept or not os.path.isdir(d):
            continue
        if shutil.which("claude", path=d) is None:
            kept.append(d)
    monkeypatch.setenv("PATH", os.pathsep.join(kept))
    # Pre-stage the spec-revise env switches to unset: a leaked value cannot decide a test.
    monkeypatch.delenv("HAL_SPEC_DELTA_RETRY", raising=False)
    monkeypatch.delenv("HAL_SURGICAL_REVISE", raising=False)
    monkeypatch.delenv("HAL_DELTA_REREVIEW", raising=False)
    telemetry_ctx.clear_current_run()
    yield
    telemetry_ctx.clear_current_run()


class _Sink:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict, str]] = []

    def append(self, event_type: str, payload: dict, run_id: str | None = None) -> None:
        self.events.append((event_type, payload, run_id or "ad-hoc"))

    def names(self) -> list[str]:
        return [e[0] for e in self.events]

    def payloads(self, name: str) -> list[dict]:
        return [e[1] for e in self.events if e[0] == name]


@pytest.fixture
def sink():
    s = _Sink()
    telemetry_ctx.set_current_run(event_log=s, run_id="bd89-p3a", step_name="t", phase="phase_45_spec")
    yield s
    telemetry_ctx.clear_current_run()


class _Spy:
    """Module-attribute spy for `invoke_llm_subprocess`: records kwargs and whether the doc file
    existed at call time; optionally writes `body` to the doc path the way a worker would."""

    def __init__(self, raw: str = "canned response\n", body: str | None = None) -> None:
        self.raw = raw
        self.body = body
        self.calls: list[dict] = []
        self.doc_existed: list[bool] = []

    def __call__(self, **kwargs) -> StepResult:
        self.calls.append(dict(kwargs))
        extra = dict(kwargs.get("extra_data") or {})
        doc = extra.get("doc_path")
        self.doc_existed.append(bool(doc) and Path(doc).exists())
        if self.body is not None and doc:
            Path(doc).parent.mkdir(parents=True, exist_ok=True)
            Path(doc).write_text(self.body, encoding="utf-8")
        data = {"raw_response": self.raw, "worker_written_paths": []}
        data.update(extra)
        return StepResult(status="ok", data=data, duration_ms=0,
                          step_name=kwargs.get("step_name", "spy"))


# --- shared helpers ------------------------------------------------------------

def _mod():
    return importlib.import_module("bytedigger_engine.workflows.phase_45_spec")


def _ctx(scratch: Path, **org_extra) -> WorkflowContext:
    scratch.mkdir(parents=True, exist_ok=True)
    org = {"scratchpad_dir": str(scratch), "git_cwd": str(scratch),
           "current_worktree_path": str(scratch), "model": "sonnet", **org_extra}
    return WorkflowContext(
        tenant_id="hal", scope=None, db_path=None, org_config=org,
        question="bd89 p3a feature request", session_id="test-bd89-p3a", persona="hal",
        framework=None, domain=None,
    )


def _step(**data) -> StepResult:
    return StepResult(status="ok", data=dict(data), duration_ms=0, step_name="prev")


def _write_spec(scratch: Path, text: str = BASE_SPEC) -> Path:
    p = scratch / _mod().SPEC_DOC_RELPATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def _write_cycle1_review(scratch: Path, text: str = STRUCTURED_REVIEW_TEXT) -> Path:
    p = scratch / _mod()._review_cycle_relpath(1)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _squash(text: str) -> str:
    return " ".join(text.split())


def _cycle2_writer_prompt_via_gate(scratch: Path, prev_extra: dict | None = None):
    """Real `_gate_on_review` (cycle-1 REVISE with a structured fence, persists the findings thread)
    then real `_build_spec_prompt` at cycle 2 from a post-evict prev. Returns the StepResult."""
    mod = _mod()
    _write_spec(scratch)
    ctx = _ctx(scratch)
    mod._gate_on_review(ctx, _step(
        verdict="REVISE", review_path="review.md", spec_path=str(scratch / "specs" / "build-spec.md"),
        cycle=1, review_raw=STRUCTURED_REVIEW_TEXT))
    return mod._build_spec_prompt(ctx, {"cycle": 2, **(prev_extra or {})})


# --- AC1 -----------------------------------------------------------------------

@pytest.mark.parametrize("stem", DROPPED_PLUGIN_STEMS)
def test_ac1_dropped_plugin_files_are_gone(stem):
    assert not (PLUGIN_DIR / f"{stem}.py").exists(), f"{stem}.py still on disk"


@pytest.mark.parametrize("stem", DROPPED_PLUGIN_STEMS)
def test_ac1_dropped_plugin_modules_not_importable(stem):
    assert importlib.util.find_spec(f"{PLUGIN_PKG}.{stem}") is None


def test_ac1_package_namespace_drops_surgical_and_restricted_reviewer():
    pkg = importlib.import_module(PLUGIN_PKG)
    present = [n for n in ("build_reviewer_prompt", "apply_surgical_patches",
                           "build_surgical_revise_prompt", "extract_surgical_patches")
               if hasattr(pkg, n)]
    assert not present, f"package still exports: {present}"


def test_ac1_guard_kept_plugin_surface_still_importable():
    pkg = importlib.import_module(PLUGIN_PKG)
    for name in ("Finding", "extract_findings_for_writer", "extract_findings_section",
                 "extract_structured_findings", "extract_structured_findings_raw",
                 "build_writer_prompt", "ReviewerVerdict", "parse_per_finding_verdicts"):
        assert hasattr(pkg, name), f"kept symbol {name} missing"
    for stem in ("delta_retry_prompt", "restricted_writer_prompt", "impl_delta_retry_prompt",
                 "findings_extractor", "verdict_parser"):
        assert (PLUGIN_DIR / f"{stem}.py").is_file(), f"kept module {stem}.py missing"


# --- AC2 -----------------------------------------------------------------------

_DROPPED_PHASE_ATTRS = (
    "_restricted_reviewer_prompt", "build_surgical_revise_prompt", "apply_surgical_patches",
    "extract_surgical_patches", "build_delta_reviewer_prompt", "extract_affected_sections",
    "_load_surgical_delta",
)


def test_ac2_phase_45_spec_namespace_has_no_dropped_symbols():
    mod = _mod()
    present = [n for n in _DROPPED_PHASE_ATTRS if hasattr(mod, n)]
    assert not present, f"phase_45_spec still binds: {present}"


def test_ac2_guard_phase_45_spec_keeps_delta_and_parser_symbols():
    mod = _mod()
    for name in ("build_delta_retry_prompt", "_restricted_writer_prompt",
                 "parse_per_finding_verdicts", "extract_structured_findings"):
        assert callable(getattr(mod, name, None)), f"{name} must stay bound"


_RESIDUE_RE = re.compile(
    r"surgical_|_surgical|surgical-delta|delta_rereview|delta_reviewer|restricted_reviewer"
    r"|HAL_DELTA_REREVIEW|extract_affected_sections|map_findings_to_patches|build_reviewer_prompt"
)


def _residue_files() -> list[Path]:
    files = [p for p in PKG_DIR.rglob("*.py") if "__pycache__" not in p.parts]
    files += list(ENGINE_PY.rglob("ERROR_CODES.md"))
    files.append(PKG_DIR / "mypy-strict-modules.txt")
    return sorted(set(files))


def test_ac2_no_residue_of_dropped_lanes_in_engine_sources():
    files = _residue_files()
    assert (PKG_DIR / "workflows" / "phase_45_spec.py") in files, "fixture precondition"
    hits: list[str] = []
    for f in files:
        for n, line in enumerate(_text(f).splitlines(), 1):
            if _RESIDUE_RE.search(line):
                hits.append(f"{f.relative_to(ENGINE_PY)}:{n}")
    assert not hits, f"{len(hits)} residue hits, first: {hits[:12]}"


# --- AC3 -----------------------------------------------------------------------

def test_ac3_flags_catalog_drops_both_kill_switches():
    from bytedigger_engine import flags_catalog

    present = [k for k in ("HAL_SURGICAL_REVISE", "HAL_DELTA_REREVIEW") if k in flags_catalog.FLAGS]
    assert not present, f"still declared: {present}"


def test_ac3_guard_surviving_flags_still_declared():
    from bytedigger_engine import flags_catalog

    for k in ("HAL_SPEC_DELTA_RETRY", "HAL_SPEC_HIGH_BINDING_PARITY", "HAL_IMPL_DELTA_RETRY"):
        assert k in flags_catalog.FLAGS, k


def test_ac3_dropped_env_switches_are_inert_for_writer_and_reviewer_prompts(tmp_path, monkeypatch):
    mod = _mod()
    scratch = tmp_path / "scratch"
    _write_spec(scratch)
    _write_cycle1_review(scratch)
    ctx = _ctx(scratch)
    stale_sidecar = scratch / "specs" / "surgical-delta-cycle-2.json"
    stale_sidecar.write_text(json.dumps({"cycle": 2, "patches": [
        {"finding_id": "F1", "old": "OLD_FRAGMENT_TO_REPLACE_F1", "new": "x"}]}), encoding="utf-8")
    spec_path = scratch / "specs" / "build-spec-cycle-2.md"
    spec_path.write_text(BASE_SPEC, encoding="utf-8")

    def writer_prompt() -> str:
        res = mod._build_spec_prompt(ctx, {"cycle": 2, "findings": STRUCTURED_REVIEW_TEXT,
                                           "structured_findings": STRUCTURED})
        assert res.status == "ok", res.error
        return res.data["prompt"]

    def reviewer_prompt() -> str:
        res = mod._build_review_prompt(ctx, _step(cycle=2, spec_path=str(spec_path)))
        assert res.status == "ok", res.error
        return res.data["prompt"]

    writer, reviewer = {}, {}
    for flag, store, build in (("HAL_SURGICAL_REVISE", writer, writer_prompt),
                               ("HAL_DELTA_REREVIEW", reviewer, reviewer_prompt)):
        for value in (None, "0", "1"):
            if value is None:
                monkeypatch.delenv(flag, raising=False)
            else:
                monkeypatch.setenv(flag, value)
            store[value] = build()
        monkeypatch.delenv(flag, raising=False)
    assert writer[None] == writer["0"] == writer["1"], "HAL_SURGICAL_REVISE still changes the writer prompt"
    assert reviewer[None] == reviewer["0"] == reviewer["1"], "HAL_DELTA_REREVIEW still changes the reviewer prompt"


# --- AC4 -----------------------------------------------------------------------

_SURGICAL_ONLY_TEXT = ("OUTPUT CONTRACT (HARD)", "fenced ```json", "Do NOT use the Write tool")


def test_ac4_cycle2_writer_is_the_delta_lane_through_the_real_chain(tmp_path, sink):
    scratch = tmp_path / "scratch"
    res = _cycle2_writer_prompt_via_gate(scratch)
    assert res.status == "ok", res.error
    data = res.data
    assert data["delta_retry"] is True
    assert "surgical_revise" not in data and "surgical_base_spec" not in data, sorted(data)
    prompt = data["prompt"]
    assert "Write the FULL revised spec markdown to" in prompt
    assert data["doc_path"] in prompt
    assert "FINDING_F1" in prompt
    for marker in _SURGICAL_ONLY_TEXT:
        assert marker not in prompt, f"surgical contract text present: {marker!r}"
    assert data["role_template"] is None
    events = sink.payloads("spec_prompt_high_binding")
    assert len(events) == 1 and events[0]["path"] == "delta", events
    assert events[0]["missing"] == [], events


def test_ac4_legacy_surgical_fallback_marker_is_ignored(tmp_path):
    base_res = _cycle2_writer_prompt_via_gate(tmp_path / "a")
    marked = _cycle2_writer_prompt_via_gate(tmp_path / "b", {"surgical_fallback": True})
    assert base_res.status == marked.status == "ok"
    assert "surgical_revise" not in base_res.data
    # The two scratchpads differ in the path only; normalise it and compare byte for byte.
    norm_a = base_res.data["prompt"].replace(str(tmp_path / "a"), "<S>")
    norm_b = marked.data["prompt"].replace(str(tmp_path / "b"), "<S>")
    norm_a = norm_a.replace(base_res.data["doc_path"], "<D>")
    norm_b = norm_b.replace(marked.data["doc_path"], "<D>")
    assert norm_a == norm_b, "a legacy surgical_fallback marker must not change the cycle-2 writer prompt"
    assert "surgical_revise" not in marked.data


def test_ac4_gate_retry_takes_the_same_delta_lane(tmp_path):
    mod = _mod()
    scratch = tmp_path / "scratch"
    _write_spec(scratch)
    ctx = _ctx(scratch)
    res = mod._build_spec_prompt(ctx, {
        "cycle": 2, "retry_source": mod.SPEC_GATES_RETRY_SOURCE,
        "findings": "- spec gate: section Rollback is missing",
        "structured_findings": STRUCTURED,  # stale review thread: must not replace the gate findings
    })
    assert res.status == "ok", res.error
    assert res.data["delta_retry"] is True
    assert "surgical_revise" not in res.data, sorted(res.data)
    assert "FINDING_G1" in res.data["prompt"], "the gate finding must drive the delta lane"
    assert "FINDING_F1" not in res.data["prompt"], "the stale review thread must not replace gate findings"
    for marker in _SURGICAL_ONLY_TEXT:
        assert marker not in res.data["prompt"], marker


# --- AC5 -----------------------------------------------------------------------

@pytest.mark.parametrize("stale", [False, True], ids=["clean_prev", "stale_surgical_prev"])
def test_ac5_invoke_spec_llm_always_unlinks_and_grants_full_tools(tmp_path, monkeypatch, stale):
    mod = _mod()
    scratch = tmp_path / "scratch"
    doc = _write_spec(scratch)
    data = {"prompt": "PROMPT", "doc_path": str(doc), "cycle": 2}
    if stale:
        data.update(surgical_revise=True, surgical_base_spec=BASE_SPEC, structured_findings=STRUCTURED)
    spy = _Spy()
    monkeypatch.setattr(mod, "invoke_llm_subprocess", spy)
    res = mod._invoke_spec_llm(_ctx(scratch), _step(**data))
    assert res.status == "ok", res.error
    assert len(spy.calls) == 1
    assert spy.doc_existed == [False], "the stale spec file must be unlinked before the worker runs"
    assert spy.calls[0]["allowed_tools"] == ["Read", "Write", "Glob"]
    extra = spy.calls[0]["extra_data"]
    for key in ("surgical_revise", "surgical_base_spec", "structured_findings"):
        assert key not in extra, f"{key} still threaded into the writer dispatch"


# --- AC6 -----------------------------------------------------------------------

def _stale_prev(doc: Path, raw: str = PATCH_ARRAY_RAW, **extra) -> StepResult:
    return _step(surgical_revise=True, surgical_base_spec=BASE_SPEC, raw_response=raw,
                 doc_path=str(doc), cycle=2, **extra)


def test_ac6_write_spec_doc_ignores_stale_surgical_state_with_worker_file(tmp_path, sink):
    scratch = tmp_path / "scratch"
    doc = _write_spec(scratch, REWRITTEN_SPEC)
    res = _mod()._write_spec_doc(_ctx(scratch), _stale_prev(doc))
    assert res.status == "ok", f"{res.error_code}: {res.error}"
    assert _text(doc).strip() == REWRITTEN_SPEC.strip(), "no patch may be applied over the worker's file"
    cycle_copy = Path(res.data["spec_cycle_path"])
    assert cycle_copy.name == "build-spec-cycle-2.md" and _text(cycle_copy).strip() == REWRITTEN_SPEC.strip()
    assert [p["source"] for p in sink.payloads("spec_writer_return_source")] == ["worker_file"]
    assert not [n for n in sink.names() if n.startswith("surgical_revise")], sink.names()
    assert not list(scratch.rglob("surgical-delta-cycle-*.json"))
    assert "surgical_revise" not in res.data, sorted(res.data)


def test_ac6_write_spec_doc_ignores_stale_surgical_state_without_worker_file(tmp_path, sink):
    scratch = tmp_path / "scratch"
    doc = scratch / "specs" / "build-spec.md"
    doc.parent.mkdir(parents=True, exist_ok=True)
    res = _mod()._write_spec_doc(_ctx(scratch), _stale_prev(doc))
    assert res.status == "ok", f"{res.error_code}: {res.error}"
    assert _text(doc).strip() == PATCH_ARRAY_RAW.strip(), "body is raw_response, nothing is patched"
    assert [p["source"] for p in sink.payloads("spec_writer_return_source")] == ["raw_response_fallback"]
    assert not [n for n in sink.names() if n.startswith("surgical_revise")], sink.names()
    assert not list(scratch.rglob("surgical-delta-cycle-*.json"))
    assert "surgical_revise" not in res.data, sorted(res.data)


def test_ac6_guard_write_spec_doc_without_stale_keys(tmp_path):
    scratch = tmp_path / "scratch"
    doc = _write_spec(scratch, REWRITTEN_SPEC)
    res = _mod()._write_spec_doc(_ctx(scratch), _step(raw_response="done", doc_path=str(doc), cycle=2))
    assert res.status == "ok", res.error
    assert Path(res.data["spec_path"]) == doc
    assert Path(res.data["spec_cycle_path"]).is_file()


# --- AC7 / AC8 -----------------------------------------------------------------

def _review_fixture(tmp_path: Path, **org_extra):
    scratch = tmp_path / "scratch"
    spec = scratch / "specs" / "build-spec-cycle-2.md"
    spec.parent.mkdir(parents=True, exist_ok=True)
    spec.write_text(BASE_SPEC, encoding="utf-8")
    _write_cycle1_review(scratch)
    (scratch / "specs" / "surgical-delta-cycle-2.json").write_text(json.dumps({"cycle": 2, "patches": [
        {"finding_id": "F1", "old": "OLD_FRAGMENT_TO_REPLACE_F1", "new": "x"}]}), encoding="utf-8")
    return scratch, spec, _ctx(scratch, **org_extra)


def test_ac7_cycle2_reviewer_prompt_is_the_cycle1_prompt(tmp_path):
    mod = _mod()
    scratch, spec, ctx = _review_fixture(tmp_path)
    res2 = mod._build_review_prompt(ctx, _step(cycle=2, spec_path=str(spec)))
    res1 = mod._build_review_prompt(ctx, _step(cycle=1, spec_path=str(spec)))
    assert res2.status == res1.status == "ok"
    assert "restricted_reviewer" not in res2.data and "delta_rereview" not in res2.data, sorted(res2.data)
    p2 = res2.data["prompt"]
    for needle in ("ROLE: You are a spec reviewer", "SPEC TO REVIEW (read this file):",
                   "FEATURE REQUEST:", "RULE-AXES REQUIREMENT"):
        assert needle in p2, f"cycle-2 reviewer prompt lacks {needle!r}"
    for needle in ("CYCLE-1 FINDINGS (the ONLY valid scope", "you may NOT introduce new findings",
                   "auditing whether cycle-N surgical patches"):
        assert needle not in p2, f"restricted/delta reviewer text present: {needle!r}"

    def norm(res) -> str:
        out = res.data["prompt"]
        for cyc in (1, 2):
            out = out.replace(str(scratch / mod._review_cycle_relpath(cyc)), "<REVIEW_DOC>")
        return out

    assert norm(res2) == norm(res1), "cycle-2 reviewer prompt must equal the cycle-1 prompt byte for byte"


def test_ac8_role_template_reaches_cycle2_reviewer(tmp_path, monkeypatch):
    mod = _mod()
    role = tmp_path / "role.md"
    role.write_text("ROLE TEMPLATE LINE 1\nROLE TEMPLATE LINE 2\n", encoding="utf-8")
    scratch, spec, ctx = _review_fixture(tmp_path, role_template_path=str(role))
    res = mod._build_review_prompt(ctx, _step(cycle=2, spec_path=str(spec)))
    assert res.status == "ok", res.error
    assert "ROLE TEMPLATE LINE 1" in res.data["prompt"]
    record = res.data["role_template"]
    assert isinstance(record, dict) and record["source_id"] == str(role), record
    assert "ROLE TEMPLATE LINE 2" in record["content"]
    spy = _Spy()
    monkeypatch.setattr(mod, "invoke_llm_subprocess", spy)
    mod._invoke_review_llm(ctx, res)
    assert spy.calls, "fixture precondition: the reviewer was dispatched"
    for call in spy.calls:
        injections = tuple(call.get("injections") or ())
        assert injections and injections[0].source_id == str(role), "dispatch must declare the role template"


# --- AC9 (guards) --------------------------------------------------------------

def _cycle2_review_doc(tmp_path: Path, raw: str):
    scratch = tmp_path / "scratch"
    spec = scratch / "specs" / "build-spec-cycle-2.md"
    spec.parent.mkdir(parents=True, exist_ok=True)
    spec.write_text(BASE_SPEC, encoding="utf-8")
    review = scratch / "specs" / "build-plan-review-cycle-2.md"
    res = _mod()._write_review_doc(_ctx(scratch), _step(
        raw_response=raw, doc_path=str(review), spec_path=str(spec), cycle=2))
    return res, review


def test_ac9_guard_full_format_review_at_cycle2_takes_classic_verdict(tmp_path):
    res, review = _cycle2_review_doc(tmp_path, SHIP_REVIEW_TEXT)
    assert res.status == "ok", res.error
    assert res.data["verdict"] == _mod().VERDICT_SHIP
    assert "per_finding" not in res.data, "a full-format review has no per-finding result"
    assert (review.parent / "review-cycle-2.json").is_file()


def test_ac9_guard_cycle2_low_axis_ship_is_downgraded(tmp_path, sink):
    res, _ = _cycle2_review_doc(tmp_path, LOW_AXIS_SHIP_REVIEW_TEXT)
    assert res.status == "ok", res.error
    assert res.data["verdict"] == _mod().VERDICT_REVISE
    downgrade = sink.payloads("spec_review_score_downgrade")
    assert len(downgrade) == 1 and downgrade[0]["cycle"] == 2, downgrade


# --- AC10: end to end, real side effects on disk and on the event sink --------

def test_ac10_cycle2_end_to_end_no_surgical_artifacts_or_events(tmp_path, monkeypatch, sink):
    mod = _mod()
    scratch = tmp_path / "scratch"
    prompt_res = _cycle2_writer_prompt_via_gate(scratch)  # cycle-1 REVISE -> cycle-2 writer prompt
    assert prompt_res.status == "ok", prompt_res.error
    _write_cycle1_review(scratch)
    ctx = _ctx(scratch)

    writer_spy = _Spy(raw="Wrote the revised spec.", body=REWRITTEN_SPEC)
    monkeypatch.setattr(mod, "invoke_llm_subprocess", writer_spy)
    invoked = mod._invoke_spec_llm(ctx, prompt_res)
    assert invoked.status == "ok", invoked.error
    written = mod._write_spec_doc(ctx, invoked)
    assert written.status == "ok", f"{written.error_code}: {written.error}"

    canonical = scratch / "specs" / "build-spec.md"
    assert _text(canonical).strip() == REWRITTEN_SPEC.strip()
    assert (scratch / "specs" / "build-spec-cycle-2.md").is_file()

    review_prompt = mod._build_review_prompt(ctx, _step(cycle=2, spec_path=written.data["spec_path"]))
    assert review_prompt.status == "ok", review_prompt.error
    review_spy = _Spy(raw=SHIP_REVIEW_TEXT)
    monkeypatch.setattr(mod, "invoke_llm_subprocess", review_spy)
    reviewed = mod._invoke_review_llm(ctx, review_prompt)
    assert reviewed.status == "ok", reviewed.error
    review_doc = mod._write_review_doc(ctx, reviewed)
    assert review_doc.status == "ok", review_doc.error
    assert review_doc.data["verdict"] == mod.VERDICT_SHIP

    assert (scratch / "specs" / "build-plan-review-cycle-2.md").is_file()
    assert (scratch / "specs" / "review-cycle-2.json").is_file()
    assert not list(scratch.rglob("surgical-delta-cycle-*.json"))
    leaked = [n for n in sink.names() if n in SURGICAL_EVENTS]
    assert not leaked, f"dropped-lane events emitted: {leaked}"
    hb = sink.payloads("spec_prompt_high_binding")
    assert [p["path"] for p in hb] == ["delta"], hb


# --- AC11 ----------------------------------------------------------------------

def test_ac11_phase_doc_and_configuration_doc_reworded():
    phase_doc = _squash(_text(REPO_ROOT / "phases" / "phase-45-spec.md"))
    config_doc = _squash(_text(REPO_ROOT / "docs" / "configuration.md"))
    assert "record-only" not in phase_doc
    assert re.search(r"(?i)cycle-2 review is the full reviewer prompt, the same as cycle 1", phase_doc)
    assert not re.search(r"(?i)restricted spec reviewer", config_doc)
    assert "Delta-retry prompts and the restricted spec writer carry no template" in config_doc


_WORDING_RE = re.compile(
    r"(?i)restricted (spec )?reviewer|restricted cycle-2 review|delta[- ]re-?review"
    r"|surgical[- ](revise|patch)|point-patch"
)
_SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", ".pytest_cache", ".mypy_cache"}


def test_ac11_no_dropped_lane_wording_in_repo_markdown():
    hits: list[str] = []
    seen = 0
    for dirpath, dirnames, filenames in os.walk(REPO_ROOT):
        rel_dir = Path(dirpath).relative_to(REPO_ROOT)
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        if rel_dir.parts[:2] == ("docs", "decisions") or rel_dir.parts[:3] == (
                "engine_py", "bytedigger_engine", "conformance"):
            dirnames[:] = []
            continue
        for fn in filenames:
            if not fn.endswith(".md") or (rel_dir == Path(".") and fn == "CHANGELOG.md"):
                continue
            seen += 1
            text = _squash((Path(dirpath) / fn).read_text(encoding="utf-8", errors="replace"))
            if _WORDING_RE.search(text):
                hits.append(str(rel_dir / fn))
    assert seen > 10, "fixture precondition: markdown files were scanned"
    assert not hits, f"dropped-lane wording remains in: {hits}"


# --- AC12 ----------------------------------------------------------------------

def _mypy_rows() -> list[str]:
    rows = [ln.strip() for ln in _text(PKG_DIR / "mypy-strict-modules.txt").splitlines()]
    return [r for r in rows if r and not r.startswith("#")]


def test_ac12_mypy_strict_list_has_no_restricted_reviewer_row():
    rows = _mypy_rows()
    assert "lib/plugins/checklist_convergence/delta_retry_prompt.py" in rows, "fixture precondition"
    assert not [r for r in rows if "restricted_reviewer_prompt" in r]


def test_ac12_guard_mypy_strict_rows_of_the_plugin_resolve():
    # Rows elsewhere in the file dangle at base for unrelated reasons (scripts/, removed lints);
    # this guard covers the victim set only: the plugin package rows.
    rows = [r for r in _mypy_rows() if r.startswith("lib/plugins/checklist_convergence/")]
    assert rows
    dangling = [r for r in rows if not (PKG_DIR / r).is_file() and "restricted_reviewer_prompt" not in r]
    assert not dangling, dangling


def test_ac12_guard_core_manifest_names_no_dropped_module():
    text = _text(ENGINE_PY / "core_manifest.json")
    assert not [s for s in DROPPED_PLUGIN_STEMS if s in text]


def test_ac12_guard_no_error_code_deleted():
    from bytedigger_engine import error_codes

    anchors = {"E_VALIDATION_RETRY", "E_MISSING_PREV_DATA", "E_SPEC_CITATION_FATAL", "E_STEP_TIMEOUT",
               "E_RESTART_CAP", "E_NO_ROLE_FILES"}
    assert anchors <= set(error_codes.ERROR_CODES), sorted(anchors - set(error_codes.ERROR_CODES))
    assert not [c for c in error_codes.ERROR_CODES if "SURGICAL" in c or "REREVIEW" in c]


# --- AC13 ----------------------------------------------------------------------

@pytest.mark.parametrize("name", DROPPED_TEST_FILES)
def test_ac13_dropped_test_files_are_gone(name):
    assert not (TESTS_DIR / name).exists(), f"{name} must be git rm'ed"


_STALE_NAMES = frozenset({
    "surgical_revise", "surgical_base_spec", "surgical_fallback", "restricted_reviewer",
    "delta_rereview", "HAL_SURGICAL_REVISE", "HAL_DELTA_REREVIEW", "surgical_revise_applied",
    "surgical_revise_fallback", "delta_rereview_used", "delta_rereview_fallback",
    "apply_surgical_patches", "extract_surgical_patches", "build_surgical_revise_prompt",
    "build_delta_reviewer_prompt", "extract_affected_sections", "_load_surgical_delta",
    "_restricted_reviewer_prompt", "build_reviewer_prompt",
})
_STALE_SUFFIXES = (".surgical_revise", ".delta_reviewer_prompt", ".restricted_reviewer_prompt")


def _stale_refs(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = node.body
            if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) \
                    and isinstance(body[0].value.value, str):
                docstrings.add(id(body[0].value))
    out: list[str] = []
    for node in ast.walk(tree):
        found: str | None = None
        if isinstance(node, ast.Import):
            found = next((a.name for a in node.names if a.name.endswith(_STALE_SUFFIXES)
                          or a.name in _STALE_NAMES), None)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            if mod.endswith(_STALE_SUFFIXES) or mod in _STALE_NAMES:
                found = mod
            else:
                found = next((a.name for a in node.names if a.name in _STALE_NAMES), None)
        elif isinstance(node, ast.Name) and node.id in _STALE_NAMES:
            found = node.id
        elif isinstance(node, ast.Attribute) and node.attr in _STALE_NAMES:
            found = node.attr
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) \
                and id(node) not in docstrings and node.value in _STALE_NAMES:
            found = node.value
        if found:
            out.append(f"{path.name}:{getattr(node, 'lineno', '?')} {found}")
    return out


def test_ac13_guard_test_corpus_has_no_stale_reference():
    files = [p for p in sorted(TESTS_DIR.rglob("*.py"))
             if p.resolve() != Path(__file__).resolve() and "__pycache__" not in p.parts]
    assert len(files) > 50, "fixture precondition: the test corpus was scanned"
    hits = [h for f in files for h in _stale_refs(f)]
    assert not hits, f"{len(hits)} stale references, first: {hits[:12]}"


# --- AC14 ----------------------------------------------------------------------

def test_ac14_environment_has_no_claude_and_no_api_key():
    assert "ANTHROPIC_API_KEY" not in os.environ
    assert shutil.which("claude") is None


# --- AC15 (spec section 6 Q4): in-flight resume across the upgrade degrades --------

def test_ac15_inflight_resume_with_stale_surgical_state_degrades_to_full_revise(tmp_path, monkeypatch, sink):
    """A run that built a surgical cycle-2 prompt before the upgrade resumes with stale surgical
    state in the step data. The upgraded engine must ignore it: unlink, full tools, take the
    worker's full body, no fallback error, no surgical artifact."""
    mod = _mod()
    scratch = tmp_path / "scratch"
    doc = _write_spec(scratch)
    ctx = _ctx(scratch)
    stale_built = _step(prompt="PRE-UPGRADE SURGICAL PROMPT", doc_path=str(doc), cycle=2,
                        delta_retry=True, surgical_revise=True, surgical_base_spec=BASE_SPEC,
                        structured_findings=STRUCTURED)
    spy = _Spy(raw="Wrote the revised spec.", body=REWRITTEN_SPEC)
    monkeypatch.setattr(mod, "invoke_llm_subprocess", spy)
    invoked = mod._invoke_spec_llm(ctx, stale_built)
    assert spy.doc_existed == [False]
    assert spy.calls[0]["allowed_tools"] == ["Read", "Write", "Glob"]
    written = mod._write_spec_doc(ctx, invoked)
    assert written.status == "ok", f"{written.error_code}: {written.error}"
    assert written.error_code is None
    assert _text(doc).strip() == REWRITTEN_SPEC.strip()
    assert not list(scratch.rglob("surgical-delta-cycle-*.json"))
    assert not [n for n in sink.names() if n in SURGICAL_EVENTS], sink.names()
