"""RED tests for DFB5B0CB — reviewer-scoped diff + satisfaction grep-verify mandate.

AC mapping:
    test_ac1_build_review_prompt_injects_build_scope_header_with_sha  → AC1
    test_ac2_build_scope_block_lists_changed_files_and_empty_placeholder → AC2
    test_ac3_build_scope_block_strict_rules_verbatim                   → AC3
    test_ac4_build_satisfaction_prompt_injects_grep_verify_mandate     → AC4
    test_ac5_degraded_path_missing_ref_emits_telemetry_no_scope_block  → AC5
    test_ac6_callables_regression_placeholder                          → AC6

ALL six tests must fail against current production code because:
- _build_review_prompt does NOT yet read pre-red-ref.txt or inject the BUILD SCOPE block.
- _build_satisfaction_prompt does NOT yet inject the GREP-VERIFY MANDATE section.
- reviewer_scope_degraded telemetry event does not yet exist in the codebase.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "workflows"))

from contracts import StepResult, WorkflowContext  # noqa: E402
import telemetry_ctx  # noqa: E402
from phase_6_review import (  # noqa: E402
    _build_review_prompt,
    _build_satisfaction_prompt,
)


# ─── helpers ──────────────────────────────────────────────────────────────────


def make_ctx(scratchpad: Path, *, question: str = "Add foo to bar", **org_extra) -> WorkflowContext:
    org = {"scratchpad_dir": str(scratchpad), **org_extra}
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question=question,
        session_id="test-session-dfb5b0cb",
        persona="hal",
        framework=None,
        domain=None,
    )


class _FakeEventLog:
    """Captures (event_type, payload, run_id) tuples for assertion."""

    def __init__(self):
        self.events: list[tuple[str, dict, str]] = []

    def append(self, event_type: str, payload: dict, run_id: str | None = None) -> None:
        self.events.append((event_type, payload, run_id or "ad-hoc"))


def _bun_calls(mock_run):
    """Filter subprocess calls to only those that invoke bun.
    This workflow does NOT spawn bun — caller asserts len == 0.
    """
    return [
        c for c in mock_run.call_args_list
        if c.args and len(c.args[0]) > 0 and c.args[0][0] == "bun"
    ]


def _make_fake_sha() -> str:
    return "a" * 40


# ─── AC1: _build_review_prompt reads pre-red-ref.txt and injects BUILD SCOPE ─


def test_ac1_build_review_prompt_injects_build_scope_header_with_sha(tmp_path):
    """AC1: _build_review_prompt reads <scratchpad>/integrity/pre-red-ref.txt,
    parses as 40-hex SHA, and injects a BUILD SCOPE block containing both the
    header and the SHA.

    Fails today: _build_review_prompt does not read pre-red-ref.txt, and no
    BUILD SCOPE block exists in the produced prompt.
    """
    scratchpad = tmp_path / "scratch"
    # Create the pre-red-ref.txt with a known SHA
    integrity_dir = scratchpad / "integrity"
    integrity_dir.mkdir(parents=True, exist_ok=True)
    fake_sha = _make_fake_sha()
    (integrity_dir / "pre-red-ref.txt").write_text(fake_sha)

    with patch("phase_6_review.git_diff_files", return_value=["src/foo.py", "src/bar.py"]):
        result = _build_review_prompt(make_ctx(scratchpad), None)

    assert result.status == "ok", (
        f"Expected status='ok', got {result.status!r}. error={getattr(result, 'error', None)}"
    )
    prompt = result.data["prompt"]
    assert "## BUILD SCOPE (authoritative — DO NOT speculate beyond)" in prompt, (
        "BUILD SCOPE header missing from _build_review_prompt output. "
        "Phase_6 must inject the BUILD SCOPE block per DFB5B0CB."
    )
    assert fake_sha in prompt, (
        f"SHA '{fake_sha}' not found in prompt. "
        "_build_review_prompt must read and embed the pre-red-ref.txt SHA."
    )


# ─── AC2: BUILD SCOPE block bullets every changed file; empty-list placeholder ─


def test_ac2_build_scope_block_lists_changed_files_and_empty_placeholder(tmp_path):
    """AC2: BUILD SCOPE block contains bullet list of every changed file;
    when list is empty, shows placeholder.

    Fails today: no BUILD SCOPE block in prompt at all.
    """
    # Scenario 2a: changed files present — assert bullet list
    scratchpad_a = tmp_path / "scratch_a"
    integrity_a = scratchpad_a / "integrity"
    integrity_a.mkdir(parents=True, exist_ok=True)
    (integrity_a / "pre-red-ref.txt").write_text(_make_fake_sha())

    with patch("phase_6_review.git_diff_files", return_value=["src/foo.py", "src/bar.py"]):
        result_a = _build_review_prompt(make_ctx(scratchpad_a), None)

    assert result_a.status == "ok"
    prompt_a = result_a.data["prompt"]
    assert "- src/foo.py" in prompt_a, (
        "'- src/foo.py' bullet missing from BUILD SCOPE block in prompt. "
        "Every changed file must appear as a bullet."
    )
    assert "- src/bar.py" in prompt_a, (
        "'- src/bar.py' bullet missing from BUILD SCOPE block in prompt."
    )

    # Scenario 2b: empty changed-files list — placeholder must appear
    scratchpad_b = tmp_path / "scratch_b"
    integrity_b = scratchpad_b / "integrity"
    integrity_b.mkdir(parents=True, exist_ok=True)
    (integrity_b / "pre-red-ref.txt").write_text(_make_fake_sha())

    with patch("phase_6_review.git_diff_files", return_value=[]):
        result_b = _build_review_prompt(make_ctx(scratchpad_b), None)

    assert result_b.status == "ok"
    prompt_b = result_b.data["prompt"]
    assert "## BUILD SCOPE (authoritative — DO NOT speculate beyond)" in prompt_b, (
        "BUILD SCOPE header must appear even when changed-files list is empty."
    )
    assert "(no files changed yet — this is unusual)" in prompt_b, (
        "Empty-list placeholder '(no files changed yet — this is unusual)' "
        "missing from prompt when git_diff_files returns []."
    )


# ─── AC3: STRICT RULES block verbatim ─────────────────────────────────────────


def test_ac3_build_scope_block_strict_rules_verbatim(tmp_path):
    """AC3: BUILD SCOPE block contains the STRICT RULES section with all 3
    verbatim bullets per spec §4.

    Fails today: no BUILD SCOPE block in prompt at all.
    """
    scratchpad = tmp_path / "scratch"
    integrity_dir = scratchpad / "integrity"
    integrity_dir.mkdir(parents=True, exist_ok=True)
    (integrity_dir / "pre-red-ref.txt").write_text(_make_fake_sha())

    with patch("phase_6_review.git_diff_files", return_value=["src/foo.py"]):
        result = _build_review_prompt(make_ctx(scratchpad), None)

    assert result.status == "ok"
    prompt = result.data["prompt"]

    assert "Do NOT report findings about any file NOT in the above list." in prompt, (
        "STRICT RULES bullet 1 missing: "
        "'Do NOT report findings about any file NOT in the above list.'"
    )
    assert "Every cited path:line MUST be in this list. Verify before citing." in prompt, (
        "STRICT RULES bullet 2 missing: "
        "'Every cited path:line MUST be in this list. Verify before citing.'"
    )
    assert (
        "If you are tempted to cite a path not in the list — STOP. "
        "That file did not change in this build; any finding about it is fabrication."
    ) in prompt, (
        "STRICT RULES bullet 3 missing: "
        "'If you are tempted to cite a path not in the list — STOP...'"
    )


# ─── AC4: _build_satisfaction_prompt injects GREP-VERIFY MANDATE ──────────────


def test_ac4_build_satisfaction_prompt_injects_grep_verify_mandate(tmp_path):
    """AC4: _build_satisfaction_prompt injects the GREP-VERIFY MANDATE section
    verbatim, including the grep example and fabricated_fix_citation failure tag.

    Fails today: _build_satisfaction_prompt does not contain GREP-VERIFY MANDATE.
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)

    # Create dummy files so the prompt builder doesn't short-circuit
    spec_file = tmp_path / "spec.md"
    review_file = tmp_path / "review.md"
    fix_file = tmp_path / "fix.md"
    spec_file.write_text("# Spec\n")
    review_file.write_text("# Review\n")
    fix_file.write_text("# Fix\n")

    prev = StepResult(
        status="ok",
        data={
            "spec_path": str(spec_file),
            "review_doc_path": str(review_file),
            "fix_doc_path": str(fix_file),
        },
        duration_ms=0,
        step_name="prev",
    )

    result = _build_satisfaction_prompt(make_ctx(scratchpad), prev)

    assert result.status == "ok", (
        f"Expected status='ok', got {result.status!r}. error={getattr(result, 'error', None)}"
    )
    prompt = result.data["prompt"]

    assert "GREP-VERIFY MANDATE" in prompt, (
        "GREP-VERIFY MANDATE section missing from _build_satisfaction_prompt output. "
        "_build_satisfaction_prompt must inject this block per DFB5B0CB+88FD8F95."
    )
    assert "fabricated_fix_citation:" in prompt, (
        "'fabricated_fix_citation:' failure tag missing from satisfaction prompt. "
        "Opus must be instructed to return this tag on bad citations."
    )
    assert 'grep -n "<expected content>" <path>' in prompt, (
        "grep example 'grep -n \"<expected content>\" <path>' missing from "
        "GREP-VERIFY MANDATE in satisfaction prompt."
    )


# ─── AC5: Degraded path — missing pre-red-ref.txt emits telemetry, no SCOPE ──


def test_ac5_degraded_path_missing_ref_emits_telemetry_no_scope_block(tmp_path):
    """AC5: if pre-red-ref.txt is absent AND resolve_pre_phase_sha returns empty
    string — prompt assembles without BUILD SCOPE block, emits
    reviewer_scope_degraded event, and does NOT raise.

    Fails today: _build_review_prompt does not attempt to read pre-red-ref.txt
    at all, so no degraded-path branch and no event exists.
    """
    # Scratchpad WITHOUT integrity/pre-red-ref.txt
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    # integrity dir absent intentionally

    log = _FakeEventLog()
    telemetry_ctx.set_current_run(
        event_log=log,
        run_id="test-dfb5b0cb-ac5",
        step_name="build_review_prompt",
        phase="phase_6_review",
    )
    try:
        with patch("phase_6_review.resolve_pre_phase_sha", return_value=""):
            result = _build_review_prompt(make_ctx(scratchpad), None)
    finally:
        telemetry_ctx.clear_current_run()

    assert result.status == "ok", (
        f"Degraded path must not raise or return error; got status={result.status!r}"
    )
    prompt = result.data["prompt"]
    assert "## BUILD SCOPE" not in prompt, (
        "BUILD SCOPE block must NOT appear in prompt when pre-red-ref.txt is missing."
    )

    emitted_types = [e[0] for e in log.events]
    assert "reviewer_scope_degraded" in emitted_types, (
        f"Expected telemetry event 'reviewer_scope_degraded' to be emitted on degraded path. "
        f"Observed events: {emitted_types}"
    )


# ─── AC6: No-regressions placeholder ─────────────────────────────────────────


def test_ac6_callables_regression_placeholder():
    """AC6: _build_review_prompt and _build_satisfaction_prompt are callable.

    Placeholder guard: the real regression check is the full-pytest delta
    (+6 PASS / 0 regressions) which the orchestrator runs independently.
    This test PASSES against current production code intentionally — it
    protects post-GREEN behaviour.
    """
    assert callable(_build_review_prompt), (
        "_build_review_prompt is not callable — symbol missing or import failed."
    )
    assert callable(_build_satisfaction_prompt), (
        "_build_satisfaction_prompt is not callable — symbol missing or import failed."
    )
