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


def _run_gate(monkeypatch, log: Path, prev: StepResult) -> StepResult:
    from bytedigger_engine import telemetry_ctx

    monkeypatch.setenv("HAL_REJECT_LOG", str(log))
    telemetry_ctx.set_current_run(event_log=None, run_id="bd139-run", step_name="gate_on_validation")
    try:
        return phase_5_implement._gate_on_validation(None, prev)
    finally:
        telemetry_ctx.clear_current_run()


def _rows(log: Path) -> list[dict]:
    if not log.is_file():
        return []
    return [json.loads(ln) for ln in log.read_text(encoding="utf-8").splitlines() if ln.strip()]


def test_ac8_fail_verdict_appends_one_validation_failed_row(tmp_path, monkeypatch):
    log = tmp_path / "reject-reasons.jsonl"
    result = _run_gate(monkeypatch, log, _gate_prev("FAIL", _FAIL_RAW))
    assert result.status == "error" and result.error_code == "E_VALIDATION_FAILED"
    rows = _rows(log)
    assert len(rows) == 1, rows
    row = rows[0]
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
    result = _run_gate(monkeypatch, log, _gate_prev("FAIL", raw))
    assert result.error_code == "E_VALIDATION_FAILED"
    rows = _rows(log)
    assert len(rows) == 1, rows
    head = rows[0]["detail"]["findings_head"]
    assert len(head) == 5, head
    assert "Cap title 1" in head[0], head


def test_ac8_unknown_verdict_appends_validation_unknown_row(tmp_path, monkeypatch):
    log = tmp_path / "reject-reasons.jsonl"
    result = _run_gate(monkeypatch, log, _gate_prev("UNKNOWN", _FAIL_RAW))
    assert result.error_code == "E_VALIDATION_FAILED"
    rows = _rows(log)
    assert len(rows) == 1, rows
    assert rows[0]["reason_code"] == "VALIDATION_UNKNOWN"


def test_ac8_pass_verdict_writes_no_row(tmp_path, monkeypatch):
    log = tmp_path / "reject-reasons.jsonl"
    result = _run_gate(monkeypatch, log, _gate_prev("PASS", "## Verdict\nVerdict: PASS\n"))
    assert result.status == "ok"
    assert _rows(log) == []


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
