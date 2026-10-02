"""RED tests for bd#89 P3b1 — phase 6 runs ONE composite reviewer; the parallel mode is deleted.

Spec (FROZEN r2): docs/decisions/2026-10-02-bd89-p3b1-single-reviewer.md (section 3, AC1-AC15).

AC -> test map
  AC1   test_ac1_phase6_symbol_removed[*], test_ac1_review_schema_template_removed[*],
        test_ac1_select_reviewers_signature, test_ac1_review_plan_returns_pair[*],
        test_ac1_guard_kept_symbols
  AC2   test_ac2_ignored_fanout_value_gives_unchanged_single_prompt[*]
  AC3   test_ac3_ignored_value_emits_one_event[*], test_ac3_single_spellings_emit_no_event[*]
  AC4   test_ac4_single_prompt_is_byte_exact_render[*], test_ac4_complex_prompt_under_37000_bytes,
        test_ac4_guard_template_constants_sha256_pinned[*], test_ac4_guard_composite_row_sha256_pinned
  AC5   test_ac5_prompt_identical_across_provider_envs[*], test_ac5_resolve_backend_unaffected
  AC6   test_ac6_one_composite_file_has_no_floor[*]
  AC7   test_ac7_removed_error_code_not_registered[*], test_ac7_removed_error_code_not_documented[*],
        test_ac7_removed_error_code_not_in_any_engine_source[*], test_ac7_guard_kept_error_codes,
        test_ac7_guard_no_role_files_still_raised, test_ac7_guard_error_codes_md_identical,
        test_ac7_guard_error_codes_check_cli
  AC8   test_ac8a_guard_pre_upgrade_sentinel_replays, test_ac8b_guard_leftover_role_files_aggregate,
        test_ac8c_guard_no_cached_aggregate, test_ac8d_stale_role_files_cleared_before_dispatch
  AC9   test_ac9a_guard_reviewer_outage_keeps_existing_error_path,
        test_ac9b_guard_no_role_files_falls_back_to_suspect_doc,
        test_ac9c_guard_report_without_findings_or_pass_is_suspect
  AC10  test_ac10_pre_upgrade_parallel_config_end_to_end
  AC11  test_ac11_guard_phase6_step_list_unchanged, test_ac11_guard_ten_workflow_modules
  AC12  test_ac12_readme_says_composite_reviewer, test_ac12_docs_rows_say_one_composite_reviewer[*],
        test_ac12_no_other_md_names_review_fanout, test_ac12_guard_reviewer_counts_untouched
  AC13  test_ac13_guard_test_corpus_closure
  AC14  every test in this file runs under the autouse provider fixture below
  AC15  test_ac15_phase6_never_arms_straggler[*], test_ac15_phase6_straggler_residue_symbols_gone,
        test_ac15_phase6_only_literal_straggler_cfg_none, test_ac15_guard_no_other_workflow_arms_straggler,
        test_ac15_guard_invoke_llm_subprocess_keeps_straggler_param

Expected RED today (fail pre-GREEN):
  AC1 all except the GUARD; AC2 for "parallel"/"PARALLEL"/" parallel "/"bogus"/7; AC3 for
  "parallel"/"bogus"/7; AC6 all; AC7 removal half (codes registered, documented, in source);
  AC8b cell partial-1-specialist only (1 < floor 3 still rejects today; the floor-removal witness);
  AC8d; AC10; AC12 docs half + no-other-md; AC15 matrix cells with straggler_abort=True
  (parallel or in-session), residue symbols, literal-only check.

GUARD (green today on purpose): AC1 kept symbols, AC2 for None/""/"single", AC3 single spellings,
  AC4, AC5, AC7 kept half, AC8a, AC8b cells full-panel-6 and mixed-composite-plus-specialists (3 files meet floor 3 today), AC8c, AC9, AC11, AC12 unchanged half, AC13, AC15 single-workflow
  and kept-parameter halves.

orchestrator: git rm  -- none. Sibling files are re-pointed/retired IN PLACE (no whole file is retired).

Notes for the orchestrator
  - AC4: sha256 pins (computed by the orchestrator at base HEAD) cover SINGLE_REVIEW_FRAMING_TEMPLATE,
    PER_ROLE_SCHEMA_TEMPLATE and _ROW_COMPOSITE_REVIEWER; they are GUARDs (green today). The
    substring/re-render pins on the rendered prompt are kept as well.
  - AC8a is a premise GUARD for step_sentinel, not UUT coverage.
  - AC12 "scripts unchanged": checked by content (both gate scripts still parse the
    *_reviewers keys), not by a git diff against a merge-base (no git call in the test).
  - AC15 "source residue": checked on the AST (names, imports, string constants), not on raw text.

Singleton/timing: none (workflows.md 1i not applicable; nothing here races).
Provider-agnostic (AC14): the autouse fixture removes ANTHROPIC_API_KEY and strips any `claude`
binary from PATH for every test.
"""
from __future__ import annotations

import ast
import inspect
import logging
import os
import re
import stat
import subprocess
import sys
import types
from pathlib import Path

import pytest

from bytedigger_engine import error_codes, telemetry_ctx
from bytedigger_engine.contracts import StepResult, WorkflowContext
from bytedigger_engine.lib.plugins.review_schema import canonical as rs_canonical
from bytedigger_engine.lib.plugins import review_schema as rs_pkg
from bytedigger_engine.lib.step_sentinel import (
    compute_ctx_hash,
    effective_ctx_hash,
    maybe_read_sentinel,
    write_step_sentinel,
)
from bytedigger_engine.workflows import phase_6_review as p6

TESTS_DIR = Path(__file__).resolve().parent
ENGINE_PY = TESTS_DIR.parent
REPO_ROOT = ENGINE_PY.parent
ENGINE_PKG = ENGINE_PY / "bytedigger_engine"
THIS_FILE = Path(__file__).resolve()

_ABSENT = object()
_COMPLEXITIES = ["SIMPLE", "FEATURE", "COMPLEX"]


# ─── shared fixtures and helpers ─────────────────────────────────────────────

class _Sink:
    """Real event-log sink: what telemetry_ctx.emit_safe appends to."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def append(self, event_type: str, payload: dict, run_id: str | None = None) -> None:
        self.events.append((event_type, payload))

    def names(self) -> list[str]:
        return [e[0] for e in self.events]


@pytest.fixture(autouse=True)
def _provider_agnostic(monkeypatch):
    """AC5/AC14: no API key, no `claude` on PATH, no leaked run context."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    kept = [d for d in os.environ.get("PATH", "").split(os.pathsep) if d and not (Path(d) / "claude").exists()]
    monkeypatch.setenv("PATH", os.pathsep.join(kept))
    telemetry_ctx.clear_current_run()
    yield
    telemetry_ctx.clear_current_run()


def _sink() -> _Sink:
    sink = _Sink()
    telemetry_ctx.set_current_run(event_log=sink, run_id="bd89-p3b1", step_name="s", phase="p6")
    return sink


def _ctx(tmp_path: Path, complexity: str = "FEATURE", **extra) -> WorkflowContext:
    scratch = tmp_path / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    worktree = tmp_path / "fake_worktree"
    worktree.mkdir(parents=True, exist_ok=True)
    org = {
        "scratchpad_dir": str(scratch),
        "current_worktree_path": str(worktree),
        "complexity": complexity,
    }
    org.update(extra)
    return WorkflowContext(
        tenant_id="hal", scope=None, db_path=None, org_config=org,
        question="add feature X", session_id="test-bd89-p3b1", persona="hal",
        framework=None, domain=None,
    )


def _scratch(ctx) -> Path:
    return Path(ctx.org_config["scratchpad_dir"])


def _build(tmp_path: Path, complexity: str = "FEATURE", **extra) -> StepResult:
    return p6._build_review_prompt(_ctx(tmp_path, complexity, **extra), None)


def _report(src: Path, title: str = "Minor naming issue", severity: str = "LOW") -> str:
    return (
        f"### SEVERITY: {severity} — {title}\n"
        f"> {src}:1: def foo():\n"
        "Confidence: HIGH\n"
        "Description: rename it.\n\n"
        "VERDICT: PARTIAL\n"
        "<!-- role-findings-count: 1 -->\n"
    )


def _write_role(scratch: Path, slug: str, body: str) -> Path:
    reviews = scratch / "reviews"
    reviews.mkdir(parents=True, exist_ok=True)
    path = reviews / f"role-{slug}.md"
    path.write_text(body, encoding="utf-8")
    return path


def _target(tmp_path: Path) -> Path:
    src = tmp_path / "fake_worktree" / "target.py"
    src.parent.mkdir(parents=True, exist_ok=True)
    src.write_text("def foo():\n    return 42\n", encoding="utf-8")
    return src


def _agg_prev(scratch: Path, complexity: str | None = "FEATURE") -> StepResult:
    data = {"scratchpad": str(scratch), "doc_path": str(scratch / p6.REVIEW_DOC_RELPATH),
            "spec_path": str(scratch / "spec.md"), "red_log_path": str(scratch / "red.log"),
            "green_log_path": str(scratch / "green.log"), "raw_response": "done"}
    if complexity:
        data["complexity"] = complexity
    return StepResult(status="ok", data=data, duration_ms=0, step_name="invoke_review_llm")


def _review_prev() -> StepResult:
    return StepResult(status="ok", duration_ms=0, step_name="build_review_prompt", data={
        "doc_path": "doc.md", "spec_path": "spec.md", "red_log_path": "red.log",
        "green_log_path": "green.log", "prompt": "review this",
    })


class _Spy:
    """Spy at invoke_llm_subprocess (a dependency; the unit under test stays real)."""

    def __init__(self, scratch: Path, write: tuple[str, str] | None = None, result: StepResult | None = None) -> None:
        self.scratch = scratch
        self.write = write
        self.result = result
        self.calls: list[dict] = []

    def __call__(self, **kwargs):
        reviews = self.scratch / "reviews"
        rec = dict(kwargs)
        rec["role_files_at_call"] = sorted(p.name for p in reviews.glob("role-*.md")) if reviews.is_dir() else []
        self.calls.append(rec)
        if self.write is not None:
            _write_role(self.scratch, self.write[0], self.write[1])
        if self.result is not None:
            return self.result
        return StepResult(
            status="ok",
            data={**(kwargs.get("extra_data") or {}), "raw_response": "done", "response_bytes": 4, "command": []},
            duration_ms=0, step_name="invoke_review_llm",
        )


def _install_spy(monkeypatch, scratch: Path, **kw) -> _Spy:
    spy = _Spy(scratch, **kw)
    monkeypatch.setattr(p6, "invoke_llm_subprocess", spy)
    return spy


def _step(name: str):
    return next(s for s in p6.phase_6_review_workflow().steps if s.name == name)


# ─── AC1 symbols and signatures ──────────────────────────────────────────────

_P6_REMOVED = [
    "_resolve_review_fanout", "_ROW_CODE_REVIEWER", "_ROW_SILENT_FAILURE_HUNTER",
    "_ROW_TYPE_DESIGN_ANALYZER", "_ROW_PR_TEST_ANALYZER", "_ROW_CODE_SIMPLIFIER",
    "_ROW_COMMENT_ANALYZER", "PARALLEL_DISPATCH_FRAMING_TEMPLATE",
]


@pytest.mark.parametrize("name", _P6_REMOVED)
def test_ac1_phase6_symbol_removed(name):
    assert not hasattr(p6, name), f"phase_6_review.{name} must be deleted (parallel mode removed)"


@pytest.mark.parametrize("mod", [rs_pkg, rs_canonical], ids=["review_schema", "review_schema.canonical"])
def test_ac1_review_schema_template_removed(mod):
    assert not hasattr(mod, "PARALLEL_DISPATCH_FRAMING_TEMPLATE")


def test_ac1_select_reviewers_signature():
    assert list(inspect.signature(p6._select_reviewers).parameters) == ["complexity"]


@pytest.mark.parametrize("complexity", _COMPLEXITIES)
def test_ac1_review_plan_returns_pair(tmp_path, complexity):
    plan = p6._review_plan(_ctx(tmp_path, complexity), complexity)
    assert len(plan) == 2, f"_review_plan must return (table, count), got {len(plan)} values"
    table, count = plan
    assert count == 1
    assert table == p6._ROW_COMPOSITE_REVIEWER


def test_ac1_guard_kept_symbols():
    assert callable(p6._select_reviewers) and callable(p6._review_plan)
    assert isinstance(p6._ROW_COMPOSITE_REVIEWER, str)
    assert isinstance(rs_canonical.SINGLE_REVIEW_FRAMING_TEMPLATE, str)
    assert isinstance(rs_canonical.PER_ROLE_SCHEMA_TEMPLATE, str)


# ─── AC2 degrade, not fail ───────────────────────────────────────────────────

_FANOUT_VALUES = [
    pytest.param("parallel", id="parallel"),
    pytest.param("PARALLEL", id="PARALLEL"),
    pytest.param(" parallel ", id="padded-parallel"),
    pytest.param("bogus", id="bogus"),
    pytest.param(7, id="int-7"),
    pytest.param(None, id="None"),
    pytest.param("", id="empty"),
    pytest.param("single", id="single"),
]


@pytest.mark.parametrize("value", _FANOUT_VALUES)
def test_ac2_ignored_fanout_value_gives_unchanged_single_prompt(tmp_path, value):
    baseline = _build(tmp_path, "FEATURE")
    assert baseline.status == "ok", f"{baseline.error_code}: {baseline.error}"
    result = _build(tmp_path, "FEATURE", review_fanout=value)
    assert result.status != "error", f"{result.error_code}: {result.error}"
    assert result.error_code is None
    prompt = result.data["prompt"]
    assert prompt == baseline.data["prompt"], "review_fanout must not change the prompt"
    assert "SINGLE REVIEW" in prompt and "role-composite.md" in prompt
    for forbidden in ("Spawn", "PARALLEL DISPATCH", "review orchestrator", "role-code-reviewer"):
        assert forbidden not in prompt, forbidden


# ─── AC3 ignored key is visible ──────────────────────────────────────────────

@pytest.mark.parametrize("raw", ["parallel", "bogus", 7], ids=["parallel", "bogus", "int-7"])
def test_ac3_ignored_value_emits_one_event(tmp_path, raw):
    sink = _sink()
    result = _build(tmp_path, "FEATURE", review_fanout=raw)
    assert result.status == "ok", f"{result.error_code}: {result.error}"
    ignored = [p for n, p in sink.events if n == "review_fanout_ignored"]
    assert len(ignored) == 1, sink.names()
    assert ignored[0]["value"] == str(raw)


@pytest.mark.parametrize("value", [_ABSENT, None, "", "single", " Single "],
                         ids=["unset", "None", "empty", "single", "padded-Single"])
def test_ac3_single_spellings_emit_no_event(tmp_path, value):
    sink = _sink()
    extra = {} if value is _ABSENT else {"review_fanout": value}
    result = _build(tmp_path, "FEATURE", **extra)
    assert result.status == "ok", f"{result.error_code}: {result.error}"
    assert "review_fanout_ignored" not in sink.names()


# ─── AC4 single prompt unchanged (GUARD) ─────────────────────────────────────

def _expected_pieces(ctx, complexity: str) -> list[str]:
    scratch = _scratch(ctx)
    table, count = p6._select_reviewers(complexity)
    role_line = (
        "ROLE: You are the sole reviewer. Review the work yourself, write your findings "
        f"to {str(scratch / 'reviews')}/role-composite.md, then write the aggregated review into "
        f"{scratch / p6.REVIEW_DOC_RELPATH}. VERIFICATION-ONLY — do NOT edit code or test files."
    )
    framing = rs_canonical.SINGLE_REVIEW_FRAMING_TEMPLATE.format(
        reviewer_count=count, abs_reviews_dir=str(scratch / "reviews"),
        per_role_schema=rs_canonical.PER_ROLE_SCHEMA_TEMPLATE, dispatch_table=table,
    )
    return [role_line, framing]


_SECURITY_ADDENDUM = (
    "\nSECURITY ADDENDUM (security_classification=HIGH): include an inline "
    "security review covering OWASP Top 10 — injection vectors, auth bypass "
    "paths, secret exposure — as an additional ## section in the aggregated "
    "review."
)


@pytest.mark.parametrize("security", [None, "HIGH"], ids=["plain", "security-high"])
@pytest.mark.parametrize("complexity", _COMPLEXITIES)
def test_ac4_single_prompt_is_byte_exact_render(tmp_path, complexity, security):
    extra = {} if security is None else {"security_classification": security}
    ctx = _ctx(tmp_path, complexity, **extra)
    result = p6._build_review_prompt(ctx, None)
    assert result.status == "ok", f"{result.error_code}: {result.error}"
    prompt = result.data["prompt"]
    for piece in _expected_pieces(ctx, complexity):
        assert piece in prompt
    assert (_SECURITY_ADDENDUM in prompt) is (security == "HIGH")


_TEMPLATE_SHA256 = {
    "SINGLE_REVIEW_FRAMING_TEMPLATE": "f42cf190c77b4392d942c22a55e0b81cb8fdec723fe1fbe36665ca9527356fec",
    "PER_ROLE_SCHEMA_TEMPLATE": "b99ae1ececcc4b956d3c9db16d8843ca51b1cfe3cbdc71bc7574d0646f0cb16a",
}
_ROW_COMPOSITE_SHA256 = "bacfccc4a9f38d1c2d76e99c05b33a674e08098d625f5e9a2ec1814d0f6cd774"


@pytest.mark.parametrize("name", sorted(_TEMPLATE_SHA256))
def test_ac4_guard_template_constants_sha256_pinned(name):
    import hashlib

    value = getattr(rs_canonical, name)
    assert hashlib.sha256(value.encode()).hexdigest() == _TEMPLATE_SHA256[name], \
        f"{name} bytes changed (spec: byte-identical)"


def test_ac4_guard_composite_row_sha256_pinned():
    import hashlib

    assert hashlib.sha256(p6._ROW_COMPOSITE_REVIEWER.encode()).hexdigest() == _ROW_COMPOSITE_SHA256


def test_ac4_complex_prompt_under_37000_bytes(tmp_path):
    ctx = _ctx(tmp_path, "COMPLEX", security_classification="HIGH")
    scratch = _scratch(ctx)
    (scratch / "reviews").mkdir(parents=True, exist_ok=True)
    import json
    (scratch / "reviews" / "last_findings.json").write_text(json.dumps({
        "attempt": 2, "score": 60, "threshold": 90,
        "review_doc_path": str(scratch / p6.REVIEW_DOC_RELPATH),
        "structured_findings": [
            {"id": str(i), "severity": "HIGH", "path": f"src/mod_{i}.py", "description": "finding " + "x" * 60}
            for i in range(40)
        ],
    }), encoding="utf-8")
    (scratch / "red.log").write_text("red", encoding="utf-8")
    (scratch / "green.log").write_text("green", encoding="utf-8")
    result = p6._build_review_prompt(ctx, None)
    assert result.status == "ok", f"{result.error_code}: {result.error}"
    size = len(result.data["prompt"].encode("utf-8"))
    assert size <= 37000, f"COMPLEX single prompt is {size} bytes; cap is 37000"


# ─── AC5 provider-agnostic (GUARD) ───────────────────────────────────────────

def _stub_claude(tmp_path: Path) -> Path:
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    stub = bindir / "claude"
    stub.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    return bindir


@pytest.mark.parametrize("backend", ["claude-in-session", "claude-subprocess"])
@pytest.mark.parametrize("with_claude", [False, True], ids=["no-claude", "stub-claude"])
@pytest.mark.parametrize("with_key", [False, True], ids=["no-key", "api-key"])
def test_ac5_prompt_identical_across_provider_envs(tmp_path, monkeypatch, with_key, with_claude, backend):
    reference = _build(tmp_path, "FEATURE").data["prompt"]
    if with_key:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-not-real")
    if with_claude:
        monkeypatch.setenv("PATH", str(_stub_claude(tmp_path)) + os.pathsep + os.environ["PATH"])
    monkeypatch.setenv("HAL_RUNNER_BACKEND", backend)
    monkeypatch.setenv("HAL_RUNNER_BACKEND_JUDGE", backend)
    result = _build(tmp_path, "FEATURE")
    assert result.status == "ok", f"{result.error_code}: {result.error}"
    assert result.data["prompt"] == reference


def test_ac5_resolve_backend_unaffected():
    from bytedigger_engine.llm_subprocess import _resolve_backend

    assert _resolve_backend(None, {"HAL_RUNNER_BACKEND_JUDGE": "claude-in-session"}, role="judge")[0] == "claude-in-session"
    assert _resolve_backend(None, {"HAL_RUNNER_BACKEND_JUDGE": "claude-subprocess"}, role="judge")[0] == "claude-subprocess"
    assert _resolve_backend(None, {"HAL_RUNNER_BACKEND": "claude-subprocess"}) == ("claude-subprocess", "env")


# ─── AC6 aggregator has no floor ─────────────────────────────────────────────

@pytest.mark.parametrize("pin", [False, True], ids=["no-pin", "pre-upgrade-parallel-pin"])
@pytest.mark.parametrize("complexity", _COMPLEXITIES)
def test_ac6_one_composite_file_has_no_floor(tmp_path, complexity, pin):
    extra = {"review_fanout": "parallel"} if pin else {}
    ctx = _ctx(tmp_path, complexity, **extra)
    scratch = _scratch(ctx)
    _write_role(scratch, "composite", _report(_target(tmp_path)))
    result = p6._aggregate_review_findings(ctx, _agg_prev(scratch, complexity))
    assert result.status == "ok", f"{result.error_code}: {result.error}"
    assert result.error_code is None
    assert "min_floor" not in result.data
    content = result.data["aggregated_content"]
    assert "## Fanout" not in content
    assert result.data.get("verdict")


# ─── AC7 error codes ─────────────────────────────────────────────────────────

_REMOVED_CODES = ["E_INSUFFICIENT_FANOUT", "E_REVIEW_FANOUT_INVALID"]


@pytest.mark.parametrize("code", _REMOVED_CODES)
def test_ac7_removed_error_code_not_registered(code):
    assert code not in error_codes.ERROR_CODES


@pytest.mark.parametrize("md", [ENGINE_PY / "ERROR_CODES.md", ENGINE_PKG / "ERROR_CODES.md"],
                         ids=["engine_py", "bytedigger_engine"])
@pytest.mark.parametrize("code", _REMOVED_CODES)
def test_ac7_removed_error_code_not_documented(md, code):
    assert code not in md.read_text(encoding="utf-8")


@pytest.mark.parametrize("code", _REMOVED_CODES)
def test_ac7_removed_error_code_not_in_any_engine_source(code):
    hits = [
        str(p.relative_to(ENGINE_PKG))
        for p in ENGINE_PKG.rglob("*.py")
        if "__pycache__" not in p.parts and re.search(rf"\b{code}\b", p.read_text(encoding="utf-8", errors="replace"))
    ]
    assert not hits, hits


def test_ac7_guard_kept_error_codes():
    assert "E_MISSING_SCRATCHPAD" in error_codes.ERROR_CODES
    assert "E_REVIEW_DEGRADED" in error_codes.ERROR_CODES


def test_ac7_no_role_files_is_ok_with_none_content(tmp_path):
    ctx = _ctx(tmp_path, "FEATURE")
    scratch = _scratch(ctx)
    (scratch / "reviews").mkdir(parents=True, exist_ok=True)
    result = p6._aggregate_review_findings(ctx, _agg_prev(scratch))
    assert result.status == "ok" and result.error_code is None
    assert result.data["aggregated_content"] is None


def test_ac7_guard_error_codes_md_identical():
    assert (ENGINE_PY / "ERROR_CODES.md").read_bytes() == (ENGINE_PKG / "ERROR_CODES.md").read_bytes()


def test_ac7_guard_error_codes_check_cli():
    proc = subprocess.run(
        [sys.executable, "-m", "bytedigger_engine.error_codes", "--check"],
        cwd=str(ENGINE_PY), capture_output=True, text=True, timeout=180,
    )
    assert proc.returncode == 0, proc.stdout + proc.stderr


# ─── AC8 resume of a pre-upgrade run ─────────────────────────────────────────

def test_ac8a_guard_pre_upgrade_sentinel_replays(tmp_path, monkeypatch):
    # PREMISE GUARD for step_sentinel (prompt not hashed); NOT coverage of the UUT (gate F7).
    ctx = _ctx(tmp_path, "FEATURE", review_fanout="parallel", straggler_abort=True,
               task_description="bd89 p3b1 pre-upgrade run")
    scratch = _scratch(ctx)
    spy = _install_spy(monkeypatch, scratch)
    step = _step("invoke_review_llm")
    assert step.resume_sentinel  # step_sentinel.py:139 gates on this
    ctx_hash = effective_ctx_hash(compute_ctx_hash(ctx), step, None)
    payload = {"raw_response": "cached review", "doc_path": "doc.md"}
    write_step_sentinel(scratch, "invoke_review_llm", 1, payload, "run-1", ctx_hash, "phase_6_review")
    cached = maybe_read_sentinel(ctx, step, 1, "run-1", lambda *a, **k: None, "phase_6_review", None)
    assert cached is not None and cached.status == "ok" and cached.data == payload
    assert spy.calls == []


_FULL_PANEL = ["code-reviewer", "silent-failure-hunter", "type-design-analyzer",
               "pr-test-analyzer", "code-simplifier", "comment-analyzer"]


# bd#89 P3b1b-ii: leftover legacy role-<slug>.md files (a pre-upgrade panel replayed via
# task_resume) are ignored; only role-composite.md is read.
@pytest.mark.parametrize("slugs", [
    pytest.param(_FULL_PANEL, id="full-panel-6"),
    pytest.param(["comment-analyzer"], id="partial-1-specialist"),
    pytest.param(["composite", "code-reviewer", "comment-analyzer"], id="mixed-composite-plus-specialists"),
])
def test_ac8b_guard_leftover_role_files_ignored(tmp_path, slugs):
    ctx = _ctx(tmp_path, "FEATURE", review_fanout="parallel")
    scratch = _scratch(ctx)
    src = _target(tmp_path)
    for slug in slugs:
        _write_role(scratch, slug, _report(src, title=f"Finding from {slug}"))
    result = p6._aggregate_review_findings(ctx, _agg_prev(scratch, "FEATURE"))
    assert result.status == "ok", f"{result.error_code}: {result.error}"
    assert result.error_code is None
    content = result.data["aggregated_content"]
    if "composite" in slugs:
        assert "Finding from composite" in content
        assert "Finding from code-reviewer" not in content
        assert "Finding from comment-analyzer" not in content
        assert result.data["role_files"] == [str(scratch / "reviews" / "role-composite.md")]
    else:
        assert content is None


def test_ac8c_guard_no_cached_aggregate(tmp_path):
    assert not _step("write_review_artifact").resume_sentinel
    ctx = _ctx(tmp_path, "FEATURE")
    scratch = _scratch(ctx)
    resume = scratch / "resume"
    resume.mkdir(parents=True, exist_ok=True)
    planted = resume / "phase_6_review__aggregate_review_findings.json"
    planted.write_text('{"aggregated_content": "PLANTED-STALE-AGGREGATE"}', encoding="utf-8")
    before = {p.name for p in resume.glob("*aggregate_review_findings*")}
    _write_role(scratch, "composite", _report(_target(tmp_path)))
    result = p6._aggregate_review_findings(ctx, _agg_prev(scratch))
    assert result.status == "ok"
    assert "PLANTED-STALE-AGGREGATE" not in result.data["aggregated_content"]
    assert {p.name for p in resume.glob("*aggregate_review_findings*")} == before  # nothing written


def test_ac8d_stale_role_files_cleared_before_dispatch(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path, "FEATURE", review_fanout="parallel", review_model="m")
    scratch = _scratch(ctx)
    _write_role(scratch, "composite", _report(_target(tmp_path), title="stale composite"))
    spy = _install_spy(monkeypatch, scratch)
    result = p6._invoke_review_llm(ctx, _review_prev())
    assert result.status == "ok", f"{result.error_code}: {result.error}"
    assert len(spy.calls) == 1
    assert spy.calls[0]["role_files_at_call"] == [], "stale role-*.md must be gone before the reviewer runs"


# ─── AC9 degrade paths (GUARD) ───────────────────────────────────────────────

def test_ac9a_guard_reviewer_outage_keeps_existing_error_path(tmp_path, monkeypatch):
    assert not _step("invoke_review_llm").skip_on_error
    ctx = _ctx(tmp_path, "FEATURE")
    outage = StepResult(status="error", data=None, duration_ms=0, step_name="invoke_review_llm",
                        error="reviewer timed out", error_code="E_LLM_TIMEOUT")
    _install_spy(monkeypatch, _scratch(ctx), result=outage)
    result = p6._invoke_review_llm(ctx, _review_prev())
    assert result.status == "error"
    assert result.error_code == "E_LLM_TIMEOUT"
    assert result.error_code in error_codes.ERROR_CODES


def test_ac9b_guard_no_role_files_falls_back_to_suspect_doc(tmp_path):
    ctx = _ctx(tmp_path, "FEATURE")
    scratch = _scratch(ctx)
    (scratch / "reviews").mkdir(parents=True, exist_ok=True)
    sink = _sink()
    agg = p6._aggregate_review_findings(ctx, _agg_prev(scratch))
    assert agg.status == "ok" and agg.error_code is None
    assert agg.data["aggregated_content"] is None
    written = p6._write_review_artifact(
        ctx, StepResult(status="ok", data=agg.data, duration_ms=0, step_name="aggregate_review_findings"))
    assert written.status == "ok", f"{written.error_code}: {written.error}"
    doc = scratch / p6.REVIEW_DOC_RELPATH
    assert doc.is_file(), "the review doc must be written to disk by the stdout fallback"
    assert "stdout_fallback_used" in sink.names()
    assert written.data["verdict"] == p6.VERDICT_SUSPECT
    assert written.data["verdict"] != p6.VERDICT_PASS


def test_ac9c_guard_report_without_findings_or_pass_is_suspect(tmp_path):
    ctx = _ctx(tmp_path, "FEATURE")
    scratch = _scratch(ctx)
    _write_role(scratch, "composite", "I looked at everything and it seems fine.\n")
    result = p6._aggregate_review_findings(ctx, _agg_prev(scratch))
    assert result.status == "ok"
    assert result.data["verdict"] == p6.VERDICT_SUSPECT
    assert result.data["verdict"] != p6.VERDICT_PASS


# ─── AC10 end to end (production side effects on disk and in the sink) ───────

def test_ac10_pre_upgrade_parallel_config_end_to_end(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path, "FEATURE", review_fanout="parallel", review_model="m")
    scratch = _scratch(ctx)
    src = _target(tmp_path)
    good_title, phantom_title = "Composite widget leaks handle", "Phantom defect in missing symbol"
    body = (
        f"### SEVERITY: HIGH — {good_title}\n"
        f"> {src}:1: def foo():\n"
        "Confidence: HIGH\n"
        "Description: handle is never released.\n\n"
        f"### SEVERITY: MEDIUM — {phantom_title}\n"
        f"> {src}:1: def totally_made_up_symbol_zz():\n"
        "Confidence: LOW\n"
        "Description: this quote is not in the file.\n\n"
        "VERDICT: FAIL\n"
        "<!-- role-findings-count: 2 -->\n"
    )
    sink = _sink()
    spy = _install_spy(monkeypatch, scratch, write=("composite", body))
    s1 = p6._build_review_prompt(ctx, None)
    s2 = p6._invoke_review_llm(ctx, s1)
    s4 = p6._aggregate_and_write_review_artifact(ctx, s2)
    for s in (s1, s2, s4):
        assert s.status == "ok" and s.error_code is None, f"{s.step_name}: {s.error_code}: {s.error}"
    assert len(spy.calls) == 1
    doc = (scratch / p6.REVIEW_DOC_RELPATH).read_text(encoding="utf-8")
    assert doc.startswith("# Composite Review")
    assert "## Fanout" not in doc
    suspect_at = doc.index(p6.SUSPECT_FINDINGS_SECTION_HEADER)
    assert 0 <= doc.index(good_title) < suspect_at, "verified finding must be in the verified section"
    # The raw role body is echoed above the suspect section, so scope the search to the tail.
    tail = doc[suspect_at:]
    phantom_line = next(ln for ln in tail.splitlines() if phantom_title in ln)
    assert "[verify: suspect" in phantom_line
    assert good_title not in tail
    assert "review_fanout_ignored" in sink.names()
    assert "review_findings_audit" in sink.names()


# ─── AC11 invariants (GUARD) ─────────────────────────────────────────────────

_PHASE6_STEPS = [
    "build_review_prompt", "invoke_review_llm", "write_review_artifact",
    "verify_findings", "verify_findings_semantic", "build_fix_prompt", "invoke_fix_llm", "fix_watchdog",
    "write_fix_artifact", "commit_fix_code", "commit_fix_tests", "run_pytest_post_fix",
    "verify_fix_typecheck", "build_decorr_prompt", "invoke_decorr_llm", "write_decorr_artifact",
    "build_satisfaction_prompt", "invoke_satisfaction_llm", "write_satisfaction_doc", "detect_mass_unverified",
]


def test_ac11_guard_phase6_step_list_unchanged():
    assert [s.name for s in p6.phase_6_review_workflow().steps] == _PHASE6_STEPS
    assert len(_PHASE6_STEPS) == 20


def test_ac11_guard_ten_workflow_modules():
    from bytedigger_engine.workflows import register_all

    names: list[str] = []
    register_all(types.SimpleNamespace(register=lambda name, wf: names.append(name)))
    assert len(names) == 10, names
    assert "10 workflow modules" in (REPO_ROOT / "README.md").read_text(encoding="utf-8")


# ─── AC12 docs ───────────────────────────────────────────────────────────────

def test_ac12_readme_says_composite_reviewer():
    text = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert not re.search(r"(?i)parallel reviewer panel", text)
    assert "composite reviewer" in text.lower()


def _reviewer_count_lines(path: Path) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    return [ln for ln in lines if re.search(r"`(simple|feature|complex)_reviewers`", ln) and not ln.lstrip().startswith("{")]


@pytest.mark.parametrize("rel", ["docs/configuration.md", "docs/plugin.md"])
def test_ac12_docs_rows_say_one_composite_reviewer(rel):
    rows = _reviewer_count_lines(REPO_ROOT / rel)
    assert rows, f"no *_reviewers rows found in {rel}"
    for row in rows:
        assert "one composite reviewer" in row.lower(), row[:120]


def test_ac12_no_other_md_names_review_fanout():
    skip_dirs = {".git", "node_modules", ".venv", "venv", "__pycache__"}
    hits = []
    for path in REPO_ROOT.rglob("*.md"):
        rel = path.relative_to(REPO_ROOT)
        if skip_dirs & set(rel.parts):
            continue
        if rel.parts[:2] == ("docs", "decisions") or rel.parts[:2] == ("engine_py", "tests") or rel.name == "CHANGELOG.md":
            continue
        if "review_fanout" in path.read_text(encoding="utf-8", errors="replace"):
            hits.append(str(rel))
    assert not hits, hits


def test_ac12_guard_reviewer_counts_untouched():
    import json

    cfg = json.loads((REPO_ROOT / "bytedigger.json").read_text(encoding="utf-8"))
    assert (cfg["simple_reviewers"], cfg["feature_reviewers"], cfg["complex_reviewers"]) == (3, 6, 6)
    assert cfg["reviewers"] == {"mode": "auto"}
    for rel in ("scripts/build-gate.sh", "scripts/ts/build-phase-gate.ts"):
        assert "simple_reviewers" in (REPO_ROOT / rel).read_text(encoding="utf-8"), rel


# ─── AC13 test-corpus closure (GUARD once the sibling edits are made) ────────

_DEAD_NAMES = {
    "_resolve_review_fanout", "PARALLEL_DISPATCH_FRAMING_TEMPLATE", "E_INSUFFICIENT_FANOUT",
    "E_REVIEW_FANOUT_INVALID", "_ROW_CODE_REVIEWER", "_ROW_SILENT_FAILURE_HUNTER",
    "_ROW_TYPE_DESIGN_ANALYZER", "_ROW_PR_TEST_ANALYZER", "_ROW_CODE_SIMPLIFIER", "_ROW_COMMENT_ANALYZER",
}


def _is_parallel_const(node) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value.strip().lower() == "parallel"


def _corpus_violations(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    docstrings = {id(n.value) for n in ast.walk(tree)
                  if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str)}
    out: list[str] = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Name) and n.id in _DEAD_NAMES:
            out.append(f"{path.name}:{n.lineno} name {n.id}")
        elif isinstance(n, ast.Attribute) and n.attr in _DEAD_NAMES:
            out.append(f"{path.name}:{n.lineno} attr {n.attr}")
        elif isinstance(n, ast.ImportFrom) and any(a.name in _DEAD_NAMES for a in n.names):
            out.append(f"{path.name}:{n.lineno} import")
        elif isinstance(n, ast.Constant) and isinstance(n.value, str) and n.value in _DEAD_NAMES and id(n) not in docstrings:
            out.append(f"{path.name}:{n.lineno} const {n.value}")
        elif isinstance(n, ast.Call):
            fn = n.func.id if isinstance(n.func, ast.Name) else getattr(n.func, "attr", "")
            for kw in n.keywords:
                if fn == "_select_reviewers" and kw.arg == "fanout":
                    out.append(f"{path.name}:{n.lineno} _select_reviewers(fanout=)")
                if kw.arg == "review_fanout" and _is_parallel_const(kw.value):
                    out.append(f"{path.name}:{n.lineno} review_fanout='parallel' kwarg")
        elif isinstance(n, ast.Dict):
            for k, v in zip(n.keys, n.values):
                if isinstance(k, ast.Constant) and k.value == "review_fanout" and _is_parallel_const(v):
                    out.append(f"{path.name}:{n.lineno} {{'review_fanout': 'parallel'}}")
    return out


def test_ac13_guard_test_corpus_closure():
    violations: list[str] = []
    for path in sorted(TESTS_DIR.rglob("*.py")):
        if path.resolve() == THIS_FILE or "__pycache__" in path.parts:
            continue
        violations.extend(_corpus_violations(path))
    assert not violations, violations


# ─── AC15 no straggler arming in phase 6 ─────────────────────────────────────

@pytest.mark.parametrize("backend", ["claude-subprocess", "claude-in-session"])
@pytest.mark.parametrize("patience", [_ABSENT, 7], ids=["no-patience", "patience-7"])
@pytest.mark.parametrize("fanout", [_ABSENT, "parallel"], ids=["no-fanout", "parallel"])
@pytest.mark.parametrize("abort", [_ABSENT, True], ids=["no-abort", "abort"])
def test_ac15_phase6_never_arms_straggler(tmp_path, monkeypatch, caplog, abort, fanout, patience, backend):
    monkeypatch.setenv("HAL_RUNNER_BACKEND", backend)
    monkeypatch.setenv("HAL_RUNNER_BACKEND_JUDGE", backend)
    org = {"scratchpad_dir": str(tmp_path)}
    if abort is not _ABSENT:
        org["straggler_abort"] = abort
    if fanout is not _ABSENT:
        org["review_fanout"] = fanout
    if patience is not _ABSENT:
        org["straggler_patience_sec"] = patience
    sink = _sink()
    spy = _install_spy(monkeypatch, tmp_path)
    with caplog.at_level(logging.WARNING):
        p6._invoke_review_llm(types.SimpleNamespace(org_config=org), _review_prev())
    assert len(spy.calls) == 1
    assert "straggler_cfg" not in spy.calls[0]
    assert "straggler_abort_skipped_in_session" not in sink.names()
    assert "auto-degrading" not in caplog.text


_STRAGGLER_NAMES = {
    "STRAGGLER_PATIENCE_SEC", "STRAGGLER_POLL_INTERVAL_SEC", "straggler_abort",
    "straggler_patience_sec", "straggler_poll_interval_sec",
}


def test_ac15_phase6_straggler_residue_symbols_gone():
    tree = ast.parse(Path(p6.__file__).read_text(encoding="utf-8"))
    docstrings = {id(n.value) for n in ast.walk(tree)
                  if isinstance(n, ast.Expr) and isinstance(n.value, ast.Constant) and isinstance(n.value.value, str)}
    found = sorted({
        (n.id if isinstance(n, ast.Name) else n.attr if isinstance(n, ast.Attribute) else getattr(n, "value", None)
         if isinstance(n, ast.Constant) else None)
        for n in ast.walk(tree)
        if (isinstance(n, ast.Name) and n.id in _STRAGGLER_NAMES)
        or (isinstance(n, ast.Attribute) and n.attr in _STRAGGLER_NAMES)
        or (isinstance(n, ast.Constant) and n.value in _STRAGGLER_NAMES and id(n) not in docstrings)
    } | {a.name for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) for a in n.names if a.name in _STRAGGLER_NAMES})
    assert not found, found


def test_ac15_phase6_only_literal_straggler_cfg_none():
    # bd#89 P3b1b-i: the literal is gone too; zero occurrences of straggler_cfg.
    src = Path(p6.__file__).read_text(encoding="utf-8")
    assert src.count("straggler_cfg") == 0


def test_ac15_guard_no_other_workflow_arms_straggler():
    offenders = [
        p.name for p in (ENGINE_PKG / "workflows").glob("*.py")
        if p.name != "phase_6_review.py" and "straggler" in p.read_text(encoding="utf-8").lower()
    ]
    assert not offenders, offenders


def test_ac15_guard_invoke_llm_subprocess_keeps_straggler_param():
    from bytedigger_engine.llm_subprocess import invoke_llm_subprocess

    assert "straggler_cfg" in inspect.signature(invoke_llm_subprocess).parameters
