"""bd#89 P3b1b-ii RED (spec FROZEN r1).

Spec: docs/decisions/2026-10-02-bd89-p3b1b-ii-aggregation-helper.md (section 3, AC1-AC13).

Phase 6 folds the aggregation step into the write step through the named helper
``_aggregate_and_write_review_artifact``, the aggregator reads only the fixed
``role-composite.md``, the ``## Fanout`` banner and ``E_NO_ROLE_FILES`` go away.

AC mapping:
    test_ac1_workflow_has_20_steps_without_aggregate_step         -> AC1
    test_ac2_write_step_runs_the_helper_with_retry_policy_1       -> AC2
    test_ac3_write_step_writes_composite_review_doc               -> AC3 (side effect)
    test_ac4_leftover_role_file_is_ignored                        -> AC4
    test_ac5_missing_composite_emits_event_and_uses_stdout        -> AC5
    test_ac6_aggregator_without_composite_is_ok_with_none_content -> AC6
    test_ac7_no_fanout_banner_and_no_banner_keys                  -> AC7
    test_ac8_no_role_files_error_code_is_gone                     -> AC8
    test_ac9_helper_emits_aggregation_error_and_matches_composition -> AC9
    test_ac10_prod_source_has_no_role_glob_banner_or_dead_code    -> AC10
    test_ac11a_guard_framing_template_names_composite_file        -> AC11 (a, GUARD)
    test_ac11b_composite_role_file_constant                       -> AC11 (b)
    test_ac12_guard_class_i_lint_and_inventory_key                -> AC12 (GUARD)
    test_ac13_guard_write_review_artifact_signature_unchanged     -> AC13 (GUARD)

New symbols (``_aggregate_and_write_review_artifact``, ``_COMPOSITE_ROLE_FILE``) are
reached with ``getattr`` inside the tests, so a missing symbol fails that test only.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from bytedigger_engine import error_codes, telemetry_ctx
from bytedigger_engine.contracts import RetryPolicy, StepResult, WorkflowContext
from bytedigger_engine.event_log import EventLog
from bytedigger_engine.lib.plugins.review_schema.canonical import SINGLE_REVIEW_FRAMING_TEMPLATE
from bytedigger_engine.workflows import phase_6_review as p6

ENGINE_PY = Path(__file__).resolve().parents[1]
ENGINE_PKG = ENGINE_PY / "bytedigger_engine"

_BASE_STEPS_MINUS_AGGREGATE = [
    "build_review_prompt", "invoke_review_llm", "write_review_artifact",
    "verify_findings", "verify_findings_semantic", "build_fix_prompt", "invoke_fix_llm", "fix_watchdog",
    "write_fix_artifact", "commit_fix_code", "commit_fix_tests", "run_pytest_post_fix",
    "verify_fix_typecheck", "build_decorr_prompt", "invoke_decorr_llm", "write_decorr_artifact",
    "build_satisfaction_prompt", "invoke_satisfaction_llm", "write_satisfaction_doc", "detect_mass_unverified",
]

_TITLE = "Composite widget leaks handle"
_LEFTOVER_MARKER = "LEFTOVER-SECURITY-MARKER-7731"


# ─── fixtures (copied from the bd139 / 906e37dc templates; not imported) ──────

def _ctx(tmp_path: Path, **extra) -> WorkflowContext:
    scratch = tmp_path / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    worktree = tmp_path / "fake_worktree"
    worktree.mkdir(parents=True, exist_ok=True)
    org = {
        "scratchpad_dir": str(scratch),
        "current_worktree_path": str(worktree),
        "complexity": "FEATURE",
    }
    org.update(extra)
    return WorkflowContext(
        tenant_id="hal", scope=None, db_path=None, org_config=org,
        question="add feature X", session_id="test-bd89-p3b1b-ii", persona="hal",
        framework=None, domain=None,
    )


def _prev(scratch: Path, raw: str = "stub reviewer stdout") -> StepResult:
    return StepResult(
        status="ok",
        data={
            "scratchpad": str(scratch),
            "complexity": "FEATURE",
            "doc_path": str(scratch / "reviews" / "build-review.md"),
            "spec_path": str(scratch / "spec.md"),
            "red_log_path": str(scratch / "red.log"),
            "green_log_path": str(scratch / "green.log"),
            "raw_response": raw,
        },
        duration_ms=0, step_name="invoke_review_llm",
    )


def _target_file(tmp_path: Path) -> Path:
    src = tmp_path / "target.py"
    src.write_text("def foo():\n    return 42\n", encoding="utf-8")
    return src


def _finding_body(src: Path, title: str) -> str:
    # Known-good shape from bd139 AC7: parses and the quote verifies.
    return (
        f"### SEVERITY: HIGH — {title}\n"
        f"> {src}:1: def foo():\n"
        "Confidence: HIGH\n"
        "Description: handle is never released.\n\n"
        "VERDICT: FAIL\n"
    )


def _write_role(scratch: Path, slug: str, body: str) -> Path:
    reviews = scratch / "reviews"
    reviews.mkdir(parents=True, exist_ok=True)
    path = reviews / f"role-{slug}.md"
    path.write_text(body, encoding="utf-8")
    return path


def _step(name: str):
    return next(s for s in p6.phase_6_review_workflow().steps if s.name == name)


def _helper():
    return getattr(p6, "_aggregate_and_write_review_artifact")


class _Events:
    """Real EventLog on tmp_path installed as the current run (the production emit path)."""

    def __init__(self, tmp_path: Path):
        self.path = tmp_path / "events.jsonl"
        self.log = EventLog(self.path)

    def __enter__(self):
        telemetry_ctx.clear_current_run()
        telemetry_ctx.set_current_run(event_log=self.log, run_id="rid-bd89-p3b1b-ii", step_name="write_review_artifact")
        return self

    def __exit__(self, *exc):
        telemetry_ctx.clear_current_run()
        return False

    def of_type(self, event_type: str) -> list[dict]:
        return [e for e in self.log.read_all() if e["event_type"] == event_type]

    def raw_text(self) -> str:
        return self.path.read_text(encoding="utf-8") if self.path.is_file() else ""


# ─── AC1 / AC2 ────────────────────────────────────────────────────────────────

def test_ac1_workflow_has_20_steps_without_aggregate_step():
    names = [s.name for s in p6.phase_6_review_workflow().steps]
    assert len(names) == 20, f"expected 20 steps, got {len(names)}: {names}"
    assert names == _BASE_STEPS_MINUS_AGGREGATE
    assert names[2] == "write_review_artifact"


def test_ac2_write_step_runs_the_helper_with_retry_policy_1():
    helper = _helper()  # AttributeError today -> this test fails alone
    names = [s.name for s in p6.phase_6_review_workflow().steps]
    assert "aggregate_review_findings" not in names
    execute = _step("write_review_artifact").execute
    # step() wraps fn in a closure that owns the RetryPolicy: the helper and the policy are its free vars.
    cells = [c.cell_contents for c in (execute.__closure__ or ())]
    assert any(c is helper for c in cells), "write step must wrap _aggregate_and_write_review_artifact"
    policies = [c for c in cells if isinstance(c, RetryPolicy)]
    assert policies and policies[0].max_retries == 1


# ─── AC3 / AC4 / AC5 (side effects, through the real write step) ──────────────

def test_ac3_write_step_writes_composite_review_doc(tmp_path):
    ctx = _ctx(tmp_path)
    scratch = Path(ctx.org_config["scratchpad_dir"])
    _write_role(scratch, "composite", _finding_body(_target_file(tmp_path), _TITLE))
    prev = _prev(scratch)
    result = _step("write_review_artifact").execute(ctx, prev)
    assert result.status == "ok", f"{result.error_code}: {result.error}"
    doc = Path(prev.data["doc_path"]).read_text(encoding="utf-8")
    assert "# Composite Review" in doc
    assert _TITLE in doc


def test_ac4_leftover_role_file_is_ignored(tmp_path):
    ctx = _ctx(tmp_path)
    scratch = Path(ctx.org_config["scratchpad_dir"])
    src = _target_file(tmp_path)
    composite = _write_role(scratch, "composite", _finding_body(src, _TITLE))
    _write_role(scratch, "security", _finding_body(src, _LEFTOVER_MARKER))
    prev = _prev(scratch)
    result = _helper()(ctx, prev)
    assert result.status == "ok", f"{result.error_code}: {result.error}"
    doc = Path(prev.data["doc_path"]).read_text(encoding="utf-8")
    assert _TITLE in doc
    assert _LEFTOVER_MARKER not in doc
    agg = p6._aggregate_review_findings(ctx, prev)
    assert agg.data["role_files"] == [str(composite)]


def test_ac5_missing_composite_emits_event_and_uses_stdout(tmp_path):
    ctx = _ctx(tmp_path)
    scratch = Path(ctx.org_config["scratchpad_dir"])
    _write_role(scratch, "security", _finding_body(_target_file(tmp_path), _LEFTOVER_MARKER))
    prev = _prev(scratch, raw="stdout-only review body STDOUT-MARKER-5512")
    with _Events(tmp_path) as ev:
        result = _step("write_review_artifact").execute(ctx, prev)
    assert result.status == "ok", f"{result.error_code}: {result.error}"
    missing = ev.of_type("role_report_missing")
    assert len(missing) == 1, [e["event_type"] for e in ev.log.read_all()]
    assert missing[0]["payload"]["path"].endswith("role-composite.md")
    assert "E_NO_ROLE_FILES" not in str(result)
    assert "E_NO_ROLE_FILES" not in ev.raw_text()
    doc = Path(prev.data["doc_path"]).read_text(encoding="utf-8")
    assert "STDOUT-MARKER-5512" in doc
    assert _LEFTOVER_MARKER not in doc


# ─── AC6 / AC7 ────────────────────────────────────────────────────────────────

def test_ac6_aggregator_without_composite_is_ok_with_none_content(tmp_path):
    ctx = _ctx(tmp_path)
    scratch = Path(ctx.org_config["scratchpad_dir"])
    (scratch / "reviews").mkdir(parents=True, exist_ok=True)
    result = p6._aggregate_review_findings(ctx, _prev(scratch))
    assert result.status == "ok", f"{result.error_code}: {result.error}"
    assert result.error_code is None
    assert result.data["aggregated_content"] is None


def test_ac7_no_fanout_banner_and_no_banner_keys(tmp_path):
    ctx = _ctx(tmp_path)
    scratch = Path(ctx.org_config["scratchpad_dir"])
    _write_role(scratch, "composite", _finding_body(_target_file(tmp_path), _TITLE))
    result = p6._aggregate_review_findings(ctx, _prev(scratch))
    assert result.status == "ok", f"{result.error_code}: {result.error}"
    content = result.data["aggregated_content"]
    assert content and _TITLE in content, "premise: the composite finding must be aggregated"
    for banned in ("## Fanout", "\nexpected:", "\nobserved:", "\nmissing:"):
        assert banned not in content, banned
    assert "expected_reviewers" not in result.data
    assert "observed_role_count" not in result.data


# ─── AC8 ──────────────────────────────────────────────────────────────────────

def test_ac8_no_role_files_error_code_is_gone():
    assert "E_NO_ROLE_FILES" not in error_codes.ERROR_CODES
    for md in (ENGINE_PY / "ERROR_CODES.md", ENGINE_PKG / "ERROR_CODES.md"):
        assert "E_NO_ROLE_FILES" not in md.read_text(encoding="utf-8"), str(md)
    proc = subprocess.run(
        [sys.executable, "-m", "bytedigger_engine.error_codes", "--check"],
        cwd=str(ENGINE_PY), capture_output=True, text=True, timeout=180,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


# ─── AC9 ──────────────────────────────────────────────────────────────────────

def test_ac9_helper_emits_aggregation_error_and_matches_composition(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.org_config.pop("scratchpad_dir")  # unresolvable scratchpad
    scratch = tmp_path / "elsewhere"
    scratch.mkdir()
    prev = _prev(scratch)
    helper = _helper()
    with _Events(tmp_path) as ev:
        got = helper(ctx, prev)
    errs = ev.of_type("review_aggregation_error")
    assert len(errs) == 1, [e["event_type"] for e in ev.log.read_all()]
    assert errs[0]["payload"]["error_code"] == "E_MISSING_SCRATCHPAD"
    assert errs[0]["payload"]["phase"] == "phase_6_review"
    expected = p6._write_review_artifact(ctx, p6._aggregate_review_findings(ctx, prev))
    assert (got.status, got.error_code) == (expected.status, expected.error_code)


# ─── AC10 ─────────────────────────────────────────────────────────────────────

def test_ac10_prod_source_has_no_role_glob_banner_or_dead_code():
    import re

    text = Path(p6.__file__).read_text(encoding="utf-8")
    assert not re.search(r"""glob\(\s*["']role-\*\.md["']\s*\)""", text)
    assert "_extract_expected_slugs" not in text
    assert "E_NO_ROLE_FILES" not in text
    assert "## Fanout" not in text


# ─── AC11 ─────────────────────────────────────────────────────────────────────

def test_ac11a_guard_framing_template_names_composite_file():
    assert "role-composite.md" in SINGLE_REVIEW_FRAMING_TEMPLATE


def test_ac11b_composite_role_file_constant():
    assert getattr(p6, "_COMPOSITE_ROLE_FILE") == "role-composite.md"


# ─── AC12 / AC13 (GUARDs) ─────────────────────────────────────────────────────

def test_ac12_guard_class_i_lint_and_inventory_key():
    proc = subprocess.run(
        [sys.executable, "-m", "bytedigger_engine.conformance.class_i_lint"],
        cwd=str(ENGINE_PY), capture_output=True, text=True, timeout=180,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr
    inventory = json.loads((ENGINE_PKG / "conformance" / "class_i_inventory.json").read_text(encoding="utf-8"))
    assert "workflows/phase_6_review.py::_aggregate_review_findings::read_text#0" in inventory["sites"]


def test_ac13_guard_write_review_artifact_signature_unchanged(tmp_path):
    scratch = tmp_path / "scratch"
    content = "# Composite Review\n\n## Aggregated Findings\n\n(no findings)\n\nVERDICT: PASS\n"
    prev = _prev(scratch)
    prev.data["aggregated_content"] = content
    result = p6._write_review_artifact(_ctx(tmp_path), prev)
    assert result.status == "ok", f"{result.error_code}: {result.error}"
    assert Path(prev.data["doc_path"]).read_text(encoding="utf-8") == content
