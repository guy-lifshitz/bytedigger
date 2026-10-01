"""RED tests for bd#139 — phase 6 runs one reviewer; phase 5 validation rejects are logged.

Spec: docs/decisions/2026-10-01-bd139-phase6-single-reviewer.md (AC1-AC9).

New symbols (record_validation_reject, the ``fanout`` kwarg, E_REVIEW_FANOUT_INVALID)
are reached lazily inside the tests that need them, so the module collects and each
AC fails at assert time, independently of the others.
"""
from __future__ import annotations

import json
import re
import stat
import sys
import types
from pathlib import Path

import pytest

from bytedigger_engine.contracts import StepResult, WorkflowContext
from bytedigger_engine.workflows import phase_5_implement, phase_6_review
from bytedigger_engine.workflows.phase_6_review import (
    _aggregate_review_findings,
    _build_review_prompt,
    _invoke_review_llm,
    _select_reviewers,
    _write_review_artifact,
)

ENGINE_PY = Path(__file__).resolve().parents[1]

_DEVOPS_ROW = (
    "  - devops-reviewer — model: sonnet — focus: CIS/OWASP/SLSA standards "
    "compliance for the detected devops artifact type"
)
_SIMPLE_TABLE = "\n".join([
    "  - pr-review-toolkit:code-reviewer — model: sonnet",
    "  - pr-review-toolkit:silent-failure-hunter — model: sonnet",
    "  - pr-review-toolkit:pr-test-analyzer — model: sonnet",
])
_FULL_TABLE = "\n".join([
    "  - pr-review-toolkit:code-reviewer — model: sonnet",
    "  - pr-review-toolkit:silent-failure-hunter — model: sonnet",
    "  - pr-review-toolkit:type-design-analyzer — model: sonnet",
    "  - pr-review-toolkit:pr-test-analyzer — model: sonnet",
    "  - pr-review-toolkit:code-simplifier — model: sonnet",
    "  - pr-review-toolkit:comment-analyzer — model: haiku",
])


def _ctx(tmp_path: Path, complexity: str = "FEATURE", **extra) -> WorkflowContext:
    scratch = tmp_path / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    fake_worktree = tmp_path / "fake_worktree"
    fake_worktree.mkdir(parents=True, exist_ok=True)
    org = {
        "scratchpad_dir": str(scratch),
        "current_worktree_path": str(fake_worktree),
        "complexity": complexity,
    }
    org.update(extra)
    return WorkflowContext(
        tenant_id="hal", scope=None, db_path=None, org_config=org,
        question="add feature X", session_id="test-bd139", persona="hal",
        framework=None, domain=None,
    )


def _role_file(reviews: Path, slug: str, severity: str = "LOW", title: str = "x") -> None:
    reviews.mkdir(parents=True, exist_ok=True)
    (reviews / f"role-{slug}.md").write_text(
        f"### SEVERITY: {severity}\n### TITLE: {title}\nStub finding body.\n", encoding="utf-8"
    )


def _agg_prev(scratch: Path, complexity: str) -> StepResult:
    return StepResult(
        status="ok",
        data={"scratchpad": str(scratch), "complexity": complexity,
              "doc_path": str(scratch / "reviews" / "build-review.md")},
        duration_ms=0, step_name="invoke_review_llm",
    )


# ─── AC1 ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("complexity", ["SIMPLE", "FEATURE", "COMPLEX"])
def test_ac1_default_select_reviewers_returns_count_1(complexity):
    table, count = _select_reviewers(complexity, None)
    assert count == 1, f"single mode must be the default: count=1, got {count}"
    assert "devops" not in table


@pytest.mark.parametrize("complexity", ["SIMPLE", "FEATURE", "COMPLEX"])
def test_ac1_default_with_artifact_type_is_count_1_and_has_devops(complexity):
    table, count = _select_reviewers(complexity, "dockerfile")
    assert count == 1, f"count stays 1 with artifact_type, got {count}"
    assert "devops" in table


# ─── AC2 ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "complexity,artifact,expected",
    [
        ("SIMPLE", None, (_SIMPLE_TABLE, 3)),
        ("FEATURE", None, (_FULL_TABLE, 6)),
        ("COMPLEX", None, (_FULL_TABLE, 6)),
        ("SIMPLE", "dockerfile", (_SIMPLE_TABLE + "\n" + _DEVOPS_ROW, 4)),
        ("FEATURE", "dockerfile", (_FULL_TABLE + "\n" + _DEVOPS_ROW, 7)),
        ("COMPLEX", "dockerfile", (_FULL_TABLE + "\n" + _DEVOPS_ROW, 7)),
    ],
)
def test_ac2_parallel_fanout_equals_2622727_output(complexity, artifact, expected):
    assert _select_reviewers(complexity, artifact, fanout="parallel") == expected


# ─── AC3 ─────────────────────────────────────────────────────────────────────

def test_ac3_single_mode_prompt_has_no_orchestrator_language(tmp_path):
    result = _build_review_prompt(_ctx(tmp_path, "FEATURE"), None)
    assert result.status == "ok", f"{result.error_code}: {result.error}"
    prompt = result.data["prompt"]
    assert "Spawn" not in prompt
    assert "parallel" not in prompt.lower()
    assert "Agent tool" not in prompt
    assert "role-composite.md" in prompt
    assert "> path:line:" in prompt


def test_ac3_parallel_mode_prompt_still_spawns_6(tmp_path):
    result = _build_review_prompt(_ctx(tmp_path, "FEATURE", review_fanout="parallel"), None)
    assert result.status == "ok", f"{result.error_code}: {result.error}"
    assert "Spawn 6 parallel" in result.data["prompt"]


_SINGLE_MODE_FORBIDDEN = ("dispatched Agent", "sub-agent", "Agent call", "Spawn")


def _write_prior_findings(scratch: Path) -> Path:
    reviews = scratch / "reviews"
    reviews.mkdir(parents=True, exist_ok=True)
    path = reviews / "last_findings.json"
    path.write_text(json.dumps({
        "attempt": 1, "score": 60, "threshold": 80,
        "review_doc_path": str(reviews / "build-review.md"),
        "structured_findings": [{"id": "1", "severity": "HIGH", "path": "src/foo.py", "description": "t"}],
    }), encoding="utf-8")
    return path


# ─── AC3b ────────────────────────────────────────────────────────────────────

def test_ac3b_single_mode_prior_findings_and_security_blocks_address_the_reviewer(tmp_path):
    ctx = _ctx(tmp_path, "FEATURE", security_classification="HIGH")
    lf = _write_prior_findings(Path(ctx.org_config["scratchpad_dir"]))
    result = _build_review_prompt(ctx, None)
    assert result.status == "ok", f"{result.error_code}: {result.error}"
    prompt = result.data["prompt"]
    assert str(lf) in prompt
    assert "PRIOR — still present" in prompt
    leaked = [w for w in _SINGLE_MODE_FORBIDDEN if w.lower() in prompt.lower()]
    assert not leaked, f"single-mode prompt leaks delegation language: {leaked}"


def test_ac3b_parallel_mode_same_inputs_keeps_dispatched_agent_wording(tmp_path):
    ctx = _ctx(tmp_path, "FEATURE", security_classification="HIGH", review_fanout="parallel")
    _write_prior_findings(Path(ctx.org_config["scratchpad_dir"]))
    result = _build_review_prompt(ctx, None)
    assert result.status == "ok", f"{result.error_code}: {result.error}"
    assert "Each dispatched Agent MUST read" in result.data["prompt"]


# ─── AC3c ────────────────────────────────────────────────────────────────────

_SIX_DIMENSIONS = ("correctness", "silent failures", "test adequacy", "type design", "simplification", "comments")


@pytest.mark.parametrize("artifact", [None, "dockerfile"])
def test_ac3c_single_mode_prompt_carries_composite_table_with_all_dimensions(tmp_path, artifact):
    extra = {"artifact_type": artifact} if artifact else {}
    result = _build_review_prompt(_ctx(tmp_path, "FEATURE", **extra), None)
    assert result.status == "ok", f"{result.error_code}: {result.error}"
    prompt = result.data["prompt"]
    table, count = _select_reviewers("FEATURE", artifact)
    assert count == 1
    # the dimensions live in the composite table AND that table reaches the model verbatim
    missing = [d for d in _SIX_DIMENSIONS if d not in table.lower()]
    assert not missing, f"composite table lacks dimensions {missing}: {table!r}"
    assert table in prompt, "the composite table must be carried verbatim in the prompt"
    assert "role-composite.md" in prompt
    assert "role-devops" not in prompt
    if artifact:
        assert "CIS/OWASP/SLSA" in prompt
    else:
        assert "CIS/OWASP/SLSA" not in prompt


# ─── AC4b ────────────────────────────────────────────────────────────────────

_ABSENT = object()


@pytest.mark.parametrize("value", [_ABSENT, None, "", " Single "])
def test_ac4b_single_spellings_select_single_mode(tmp_path, value):
    extra = {} if value is _ABSENT else {"review_fanout": value}
    ctx = _ctx(tmp_path, "FEATURE", **extra)
    result = _build_review_prompt(ctx, None)
    assert result.status == "ok", f"{result.error_code}: {result.error}"
    assert "Spawn" not in result.data["prompt"]
    assert "role-composite.md" in result.data["prompt"]
    scratch = Path(ctx.org_config["scratchpad_dir"])
    _role_file(scratch / "reviews", "composite")
    agg = _aggregate_review_findings(ctx, _agg_prev(scratch, "FEATURE"))
    assert agg.status == "ok", f"{agg.error_code}: {agg.error}"


def test_ac4b_uppercase_parallel_selects_parallel_mode(tmp_path):
    result = _build_review_prompt(_ctx(tmp_path, "FEATURE", review_fanout="PARALLEL"), None)
    assert result.status == "ok", f"{result.error_code}: {result.error}"
    assert "Spawn 6 parallel" in result.data["prompt"]


# ─── AC4 ─────────────────────────────────────────────────────────────────────

def test_ac4_bogus_fanout_is_step_error(tmp_path):
    result = _build_review_prompt(_ctx(tmp_path, "FEATURE", review_fanout="bogus"), None)
    assert result.status == "error"
    assert result.error_code == "E_REVIEW_FANOUT_INVALID"


def test_ac4_error_code_documented_in_error_codes_md():
    text = (ENGINE_PY / "ERROR_CODES.md").read_text(encoding="utf-8")
    assert re.search(r"^- `E_REVIEW_FANOUT_INVALID`", text, re.MULTILINE)


# ─── AC5 ─────────────────────────────────────────────────────────────────────

def test_ac5_single_one_valid_role_composite_is_ok(tmp_path):
    scratch = tmp_path / "scratch"
    _role_file(scratch / "reviews", "composite")
    result = _aggregate_review_findings(_ctx(tmp_path, "FEATURE"), _agg_prev(scratch, "FEATURE"))
    assert result.status == "ok", f"{result.error_code}: {result.error}"


def test_ac5_single_zero_role_files_is_no_role_files(tmp_path):
    scratch = tmp_path / "scratch"
    (scratch / "reviews").mkdir(parents=True)
    result = _aggregate_review_findings(_ctx(tmp_path, "FEATURE"), _agg_prev(scratch, "FEATURE"))
    assert result.status == "error"
    assert result.error_code == "E_NO_ROLE_FILES"


def test_ac5_parallel_expected_6_keeps_floor_3(tmp_path):
    scratch = tmp_path / "scratch"
    _role_file(scratch / "reviews", "code-reviewer")
    _role_file(scratch / "reviews", "silent-failure-hunter")
    result = _aggregate_review_findings(
        _ctx(tmp_path, "FEATURE", review_fanout="parallel"), _agg_prev(scratch, "FEATURE")
    )
    assert result.error_code == "E_INSUFFICIENT_FANOUT"
    assert result.data.get("min_floor") == 3
    assert result.data.get("expected_reviewers") == 6


# ─── AC6 ─────────────────────────────────────────────────────────────────────

def _capture_invoke(tmp_path, monkeypatch, **org_extra) -> dict:
    captured: dict = {}
    # gate NOTE: an ambient claude-in-session judge would degrade straggler_abort and mask the assertion
    monkeypatch.setenv("HAL_RUNNER_BACKEND", "claude-subprocess")
    monkeypatch.setenv("HAL_RUNNER_BACKEND_JUDGE", "claude-subprocess")

    def _fake_invoke(**kwargs):
        captured.update(kwargs)
        return StepResult(status="ok", data={"raw_response": "x", "response_bytes": 1, "command": []},
                          duration_ms=0, step_name="invoke_review_llm")

    monkeypatch.setattr(phase_6_review, "invoke_llm_subprocess", _fake_invoke)
    scratch = tmp_path / "scratch"
    scratch.mkdir(exist_ok=True)
    ctx = types.SimpleNamespace(org_config={
        "scratchpad_dir": str(scratch), "complexity": "FEATURE", "straggler_abort": True, **org_extra,
    })
    prev = StepResult(status="ok", data={
        "doc_path": "doc.md", "spec_path": "spec.md", "red_log_path": "red.log",
        "green_log_path": "green.log", "prompt": "review this",
    }, duration_ms=0, step_name="build_review_prompt")
    _invoke_review_llm(ctx, prev)
    return captured


def test_ac6_single_mode_passes_straggler_cfg_none(tmp_path, monkeypatch):
    captured = _capture_invoke(tmp_path, monkeypatch)
    assert "straggler_cfg" in captured
    assert captured["straggler_cfg"] is None


def test_ac6_parallel_feature_passes_expected_n_6(tmp_path, monkeypatch):
    captured = _capture_invoke(tmp_path, monkeypatch, review_fanout="parallel")
    assert captured["straggler_cfg"] is not None
    assert captured["straggler_cfg"]["expected_n"] == 6


# ─── AC7 (side effect, §1l) ──────────────────────────────────────────────────
# (AC6b, the stale-file guard, follows _install_fake_llm below.)

def _install_fake_llm(tmp_path: Path, monkeypatch, *, role_file: Path | None, body: str):
    """Real executable script as the LLM, registered through the provider seam.

    The unit under test (the phase 6 steps and the real invoke_llm_subprocess) is
    not mocked; only the binary behind the provider's argv is replaced.
    """
    from bytedigger_engine import llm_subprocess, telemetry_ctx
    from bytedigger_engine.lib.llm_provider import CLAUDE_PROVIDER, ProviderSpec, register_provider, reset_providers

    script = tmp_path / "fake-llm"
    lines = [f"#!{sys.executable}", "import json, sys", "sys.stdin.read()"]
    if role_file is not None:
        lines += [
            "import pathlib",
            f"p = pathlib.Path({str(role_file)!r})",
            "p.parent.mkdir(parents=True, exist_ok=True)",
            f"p.write_text({body!r}, encoding='utf-8')",
        ]
    lines += ["print(json.dumps({'type': 'result', 'subtype': 'success', 'is_error': False, 'result': 'done'}))"]
    script.write_text("\n".join(lines) + "\n", encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)

    register_provider(ProviderSpec(
        name="bd139-fake", build_argv=lambda model: [str(script)], stream_flags=(),
        parse_result=CLAUDE_PROVIDER.parse_result, model_rank={"m": 0},
        model_family=lambda model: None, default_gate_floor="m",
    ))
    monkeypatch.setenv("HAL_LLM_PROVIDER", "bd139-fake")
    monkeypatch.setenv("HAL_RUNNER_BACKEND", "claude-subprocess")
    monkeypatch.setenv("HAL_RUNNER_BACKEND_JUDGE", "claude-subprocess")
    monkeypatch.setattr(llm_subprocess, "emit_resolver_resolved", lambda *a, **kw: None)
    telemetry_ctx.clear_current_run()

    def _teardown():
        llm_subprocess.reset_backends()
        reset_providers()
        telemetry_ctx.clear_current_run()

    return _teardown


def _run_steps_1_to_4(ctx) -> tuple[StepResult, StepResult, StepResult, StepResult]:
    s1 = _build_review_prompt(ctx, None)
    s2 = _invoke_review_llm(ctx, s1)
    s3 = _aggregate_review_findings(ctx, s2)
    s4 = _write_review_artifact(ctx, s3)
    return s1, s2, s3, s4


def test_ac7_single_reviewer_findings_reach_build_review_md(tmp_path, monkeypatch):
    src = tmp_path / "target.py"
    src.write_text("def foo():\n    return 42\n", encoding="utf-8")
    ctx = _ctx(tmp_path, "FEATURE", review_model="m")
    scratch = Path(ctx.org_config["scratchpad_dir"])
    body = (
        "### SEVERITY: HIGH — Composite widget leaks handle\n"
        f"> {src}:1: def foo():\n"
        "Confidence: HIGH\n"
        "Description: handle is never released.\n\n"
        "VERDICT: FAIL\n"
    )
    teardown = _install_fake_llm(tmp_path, monkeypatch, role_file=scratch / "reviews" / "role-composite.md", body=body)
    try:
        s1, s2, s3, s4 = _run_steps_1_to_4(ctx)
    finally:
        teardown()
    assert s1.status == "ok", f"{s1.error_code}: {s1.error}"
    assert s2.status == "ok", f"{s2.error_code}: {s2.error}"
    assert s3.status == "ok", f"{s3.error_code}: {s3.error}"
    assert s4.status == "ok", f"{s4.error_code}: {s4.error}"
    doc = (scratch / "reviews" / "build-review.md").read_text(encoding="utf-8")
    assert "Composite widget leaks handle" in doc
    assert "expected: 1" in doc, "fanout banner must report one expected reviewer"
    assert "missing: (none)" in doc, "composite-row slug must parse so no role is reported missing"
    verified_titles = [f.get("title") for f in (s3.data.get("verified_findings") or [])]
    assert any("Composite widget leaks handle" in (t or "") for t in verified_titles), verified_titles
    assert (scratch / "reviews" / "role-composite.md").is_file()


def test_ac7_single_reviewer_writing_nothing_ends_no_role_files(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path, "FEATURE", review_model="m")
    teardown = _install_fake_llm(tmp_path, monkeypatch, role_file=None, body="")
    try:
        s1 = _build_review_prompt(ctx, None)
        s2 = _invoke_review_llm(ctx, s1)
        s3 = _aggregate_review_findings(ctx, s2)
    finally:
        teardown()
    assert s2.status == "ok", f"{s2.error_code}: {s2.error}"
    assert s3.status == "error"
    assert s3.error_code == "E_NO_ROLE_FILES"


# ─── AC6b (stale-file guard) ─────────────────────────────────────────────────

def test_ac6b_stale_role_composite_is_cleared_before_the_llm_runs(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path, "FEATURE", review_model="m")
    scratch = Path(ctx.org_config["scratchpad_dir"])
    _role_file(scratch / "reviews", "composite", severity="HIGH", title="stale cycle-1 finding")
    _role_file(scratch / "reviews", "code-reviewer", severity="HIGH", title="stale parallel-mode finding")
    teardown = _install_fake_llm(tmp_path, monkeypatch, role_file=None, body="")
    try:
        s1 = _build_review_prompt(ctx, None)
        s2 = _invoke_review_llm(ctx, s1)
        s3 = _aggregate_review_findings(ctx, s2)
    finally:
        teardown()
    assert s2.status == "ok", f"{s2.error_code}: {s2.error}"
    assert not (scratch / "reviews" / "role-composite.md").exists()
    assert not (scratch / "reviews" / "role-code-reviewer.md").exists()
    assert s3.status == "error"
    assert s3.error_code == "E_NO_ROLE_FILES", f"stale file satisfied the floor: {s3.error_code}"


# ─── AC8 / AC9 (side effect, §1l) ────────────────────────────────────────────

_FAIL_RAW = (
    "## Verdict\nVerdict: FAIL\n\n"
    "### SEVERITY: HIGH — Missing edge case 1 for the §1w rule\n"
    "### SEVERITY: HIGH — " + ("L" * 200) + "\n"
    "### SEVERITY: HIGH — Missing edge case 3\n"
    "### SEVERITY: MEDIUM — Missing edge case 4\n"
    "### SEVERITY: MEDIUM — Missing edge case 5\n"
    "### SEVERITY: MEDIUM — Missing edge case 6\n"
    "### SEVERITY: LOW — Missing edge case 7\n"
)


def _gate_prev(verdict: str, raw: str, cycle: int | None = None) -> StepResult:
    return StepResult(
        status="ok",
        data={
            "verdict": verdict,
            "cycle": cycle if cycle is not None else phase_5_implement.MAX_VALIDATION_CYCLES,
            "validation_doc_path": "/tmp/v.md", "spec_path": "/tmp/s.md", "red_log_path": "/tmp/r.log",
            "validation_raw": raw, "red_commit_sha": "deadbeef", "red_test_paths": [],
        },
        duration_ms=0, step_name="write_validation_doc",
    )


def _run_gate(monkeypatch, log: Path, prev: StepResult, ctx=None, *, with_run: bool = True) -> StepResult:
    from bytedigger_engine import telemetry_ctx

    monkeypatch.setenv("HAL_REJECT_LOG", str(log))
    telemetry_ctx.clear_current_run()
    if with_run:
        telemetry_ctx.set_current_run(event_log=None, run_id="bd139-run", step_name="gate_on_validation")
    try:
        return phase_5_implement._gate_on_validation(ctx, prev)
    finally:
        telemetry_ctx.clear_current_run()


def _rows(log: Path) -> list[dict]:
    if not log.is_file():
        return []
    return [json.loads(ln) for ln in log.read_text(encoding="utf-8").splitlines() if ln.strip()]


def _assert_doc_path(row: dict, prev: StepResult) -> None:
    assert row["detail"]["validation_doc_path"] == prev.data["validation_doc_path"], row["detail"]


def test_ac8_fail_verdict_appends_one_validation_failed_row(tmp_path, monkeypatch):
    log = tmp_path / "reject-reasons.jsonl"
    prev = _gate_prev("FAIL", _FAIL_RAW)
    result = _run_gate(monkeypatch, log, prev)
    assert result.status == "error" and result.error_code == "E_VALIDATION_FAILED"
    rows = _rows(log)
    assert len(rows) == 1, rows
    row = rows[0]
    _assert_doc_path(row, prev)
    assert row["phase"] == "phase_5_implement"
    assert row["reason_code"] == "VALIDATION_FAILED"
    assert row["axes"] == ["§1w"]
    assert row["detail"]["cycle"] == phase_5_implement.MAX_VALIDATION_CYCLES
    head = row["detail"]["findings_head"]
    assert len(head) == 3, head
    assert not any("edge case 4" in t or "edge case 7" in t for t in head), head
    assert all(isinstance(t, str) and 0 < len(t) <= 120 for t in head), head
    assert len(head[1]) == 120, head
    assert "Missing edge case 1" in head[0]


def test_ac8_findings_head_caps_at_five(tmp_path, monkeypatch):
    log = tmp_path / "reject-reasons.jsonl"
    raw = "## Verdict\nVerdict: FAIL\n\n" + "".join(
        f"### SEVERITY: HIGH — Cap title {i}\n" for i in range(1, 8)
    )
    prev = _gate_prev("FAIL", raw)
    result = _run_gate(monkeypatch, log, prev)
    assert result.error_code == "E_VALIDATION_FAILED"
    rows = _rows(log)
    assert len(rows) == 1, rows
    _assert_doc_path(rows[0], prev)
    head = rows[0]["detail"]["findings_head"]
    assert len(head) == 5, head
    assert "Cap title 1" in head[0], head


def test_ac8_unknown_verdict_appends_validation_unknown_row(tmp_path, monkeypatch):
    log = tmp_path / "reject-reasons.jsonl"
    prev = _gate_prev("UNKNOWN", _FAIL_RAW)
    result = _run_gate(monkeypatch, log, prev)
    assert result.error_code == "E_VALIDATION_FAILED"
    rows = _rows(log)
    assert len(rows) == 1, rows
    assert rows[0]["reason_code"] == "VALIDATION_UNKNOWN"
    _assert_doc_path(rows[0], prev)


def test_ac8_pass_verdict_writes_no_row(tmp_path, monkeypatch):
    log = tmp_path / "reject-reasons.jsonl"
    result = _run_gate(monkeypatch, log, _gate_prev("PASS", "## Verdict\nVerdict: PASS\n"))
    assert result.status == "ok"
    assert _rows(log) == []


def test_ac8b_rejected_round_below_cap_is_logged_and_outcome_is_unchanged(tmp_path, monkeypatch):
    log = tmp_path / "reject-reasons.jsonl"
    prev = _gate_prev("FAIL", _FAIL_RAW, cycle=1)
    result = _run_gate(monkeypatch, log, prev)
    # outcome exactly as without logging
    assert result.status == "ok"
    assert result.error_code is None
    assert result.data["cycle"] == 2
    assert result.data["verdict"] == "FAIL"
    rows = _rows(log)
    assert len(rows) == 1, rows
    assert rows[0]["phase"] == "phase_5_implement"
    assert rows[0]["reason_code"] == "VALIDATION_FAILED"
    assert rows[0]["detail"]["cycle"] == 1
    assert rows[0]["detail"]["verdict"] == "FAIL"
    _assert_doc_path(rows[0], prev)


def _spec_defect_setup(tmp_path: Path, monkeypatch):
    """Reach the SPEC_DEFECT reroute branches of _gate_on_validation (phase_5_implement.py:6845-6942)."""
    scratch = tmp_path / "gate-scratch"
    scratch.mkdir()
    spec = tmp_path / "spec.md"
    spec.write_text("# defective spec\n", encoding="utf-8")
    monkeypatch.setenv("HAL_SPEC_DEFECT_REROUTE", "1")
    ctx = types.SimpleNamespace(org_config={"scratchpad_dir": str(scratch)}, question="q-bd139")
    structured = types.SimpleNamespace(
        approve=False,
        verdict_category=phase_5_implement.VERDICT_CATEGORY_SPEC_DEFECT,
        reject_reason=None,
    )
    prev = _gate_prev("FAIL", _FAIL_RAW, cycle=1)
    prev.data["structured_verdict"] = structured
    prev.data["spec_path"] = str(spec)
    return ctx, prev, scratch, spec


def test_ac8c_spec_defect_reroute_exit_is_logged_with_its_own_reason(tmp_path, monkeypatch):
    ctx, prev, _scratch, _spec = _spec_defect_setup(tmp_path, monkeypatch)
    log = tmp_path / "reject-reasons.jsonl"
    result = _run_gate(monkeypatch, log, prev, ctx)
    assert result.status == "error"
    assert result.error_code == "E_SPEC_DEFECT"  # unchanged
    rows = _rows(log)
    assert len(rows) == 1, rows
    assert rows[0]["phase"] == "phase_5_implement"
    assert rows[0]["reason_code"] == "VALIDATION_SPEC_DEFECT"
    assert rows[0]["detail"]["cycle"] == 1
    _assert_doc_path(rows[0], prev)


def test_ac8c_spec_defect_no_progress_exit_is_logged(tmp_path, monkeypatch):
    from bytedigger_engine.lib.spec_defect_ledger import record_reroute, spec_sha

    ctx, prev, scratch, spec = _spec_defect_setup(tmp_path, monkeypatch)
    # pre-stage the ledger (§1i): this spec sha was already rejected under this run id
    assert record_reroute(scratch.resolve(), "bd139-run", spec_sha(spec)) is not None
    log = tmp_path / "reject-reasons.jsonl"
    result = _run_gate(monkeypatch, log, prev, ctx)
    assert result.status == "error"
    assert result.error_code == "E_SPEC_DEFECT_BUDGET"  # unchanged
    rows = _rows(log)
    assert len(rows) == 1, rows
    assert rows[0]["reason_code"] == "VALIDATION_SPEC_DEFECT"
    _assert_doc_path(rows[0], prev)


def test_ac8e_budget_exhausted_exit_is_logged(tmp_path, monkeypatch):
    # Pre-stage (§1i): fresh ledger (so the :6872 no-progress branch is skipped) and a marker
    # attempt at the cap, so attempts_floor = cap + 1 > _MAX_SPEC_DEFECT_REROUTES -> the :6904 return.
    ctx, prev, _scratch, _spec = _spec_defect_setup(tmp_path, monkeypatch)
    ctx.org_config["phase_reroute"] = {"attempt": phase_5_implement._MAX_SPEC_DEFECT_REROUTES}
    log = tmp_path / "reject-reasons.jsonl"
    result = _run_gate(monkeypatch, log, prev, ctx)
    assert result.status == "error"
    assert result.error_code == "E_SPEC_DEFECT_BUDGET"  # unchanged
    assert "exhausted" in result.error, f"must be the budget-exhausted site, not no-progress: {result.error}"
    rows = _rows(log)
    assert len(rows) == 1, rows
    assert rows[0]["phase"] == "phase_5_implement"
    assert rows[0]["reason_code"] == "VALIDATION_SPEC_DEFECT"
    assert rows[0]["detail"]["cycle"] == 1
    _assert_doc_path(rows[0], prev)


def test_ac8f_markdown_pass_with_structured_reject_logs_canonical_reject_token(tmp_path, monkeypatch):
    log = tmp_path / "reject-reasons.jsonl"
    prev = _gate_prev("PASS", "## Verdict\nVerdict: PASS\n\n### SEVERITY: HIGH — Structured reject title\n", cycle=1)
    prev.data["structured_verdict"] = types.SimpleNamespace(
        approve=False, verdict_category=None, reject_reason=None,
    )
    result = _run_gate(monkeypatch, log, prev)
    assert result.status == "ok"  # retry branch, outcome unchanged
    assert result.data["verdict"] == phase_5_implement.VERDICT_FAIL
    rows = _rows(log)
    assert len(rows) == 1, rows
    assert rows[0]["reason_code"] == "VALIDATION_FAILED"
    assert rows[0]["detail"]["verdict"] == phase_5_implement.VERDICT_FAIL
    assert rows[0]["detail"]["verdict"] != phase_5_implement.VERDICT_PASS
    _assert_doc_path(rows[0], prev)


# ─── AC3d ────────────────────────────────────────────────────────────────────

def test_ac3d_anti_hallucination_fragment_has_no_sub_agent_but_keeps_label():
    text = (ENGINE_PY / "bytedigger_engine" / "lib" / "plugins" / "anti_hallucination"
            / "prompt_fragment.md").read_text(encoding="utf-8")
    assert "sub-agent" not in text
    assert "COMPOSITE AGGREGATION:" in text
    assert "re-quote each finding" in text


@pytest.mark.parametrize("cycle,code,status", [
    (1, None, "ok"),
    (phase_5_implement.MAX_VALIDATION_CYCLES, "E_VALIDATION_FAILED", "error"),
])
def test_ac8d_no_current_run_writes_no_row_and_outcome_is_unchanged(tmp_path, monkeypatch, cycle, code, status):
    log = tmp_path / "reject-reasons.jsonl"
    result = _run_gate(monkeypatch, log, _gate_prev("FAIL", _FAIL_RAW, cycle=cycle), with_run=False)
    assert result.status == status
    assert result.error_code == code
    assert _rows(log) == []


def test_ac9_retry_branch_unwritable_log_still_returns_ok_next_cycle(tmp_path, monkeypatch):
    log_dir = tmp_path / "reject-reasons.jsonl"
    log_dir.mkdir()
    result = _run_gate(monkeypatch, log_dir, _gate_prev("FAIL", _FAIL_RAW, cycle=1))
    assert result.status == "ok"
    assert result.data["cycle"] == 2


def test_ac9_retry_branch_raising_writer_is_swallowed_and_was_called(tmp_path, monkeypatch):
    from bytedigger_engine import reject_log

    calls: list[tuple] = []

    def _boom(*a, **kw):
        calls.append((a, kw))
        raise OSError("disk on fire")

    monkeypatch.setattr(reject_log, "emit_reject_reason", _boom)
    result = _run_gate(monkeypatch, tmp_path / "reject-reasons.jsonl", _gate_prev("FAIL", _FAIL_RAW, cycle=1))
    assert len(calls) == 1, "the retry branch must route the reject through reject_log.emit_reject_reason"
    assert result.status == "ok"
    assert result.data["cycle"] == 2


def test_ac9_unwritable_log_path_still_returns_e_validation_failed(tmp_path, monkeypatch):
    log_dir = tmp_path / "reject-reasons.jsonl"
    log_dir.mkdir()  # a directory where the file should be
    result = _run_gate(monkeypatch, log_dir, _gate_prev("FAIL", _FAIL_RAW))
    assert result.status == "error"
    assert result.error_code == "E_VALIDATION_FAILED"


def test_ac9_raising_reject_writer_is_swallowed_and_was_called(tmp_path, monkeypatch):
    from bytedigger_engine import reject_log

    calls: list[tuple] = []

    def _boom(*a, **kw):
        calls.append((a, kw))
        raise OSError("disk on fire")

    monkeypatch.setattr(reject_log, "emit_reject_reason", _boom)
    log = tmp_path / "reject-reasons.jsonl"
    result = _run_gate(monkeypatch, log, _gate_prev("FAIL", _FAIL_RAW))
    assert len(calls) == 1, "gate must route the reject through reject_log.emit_reject_reason"
    assert result.status == "error"
    assert result.error_code == "E_VALIDATION_FAILED"
