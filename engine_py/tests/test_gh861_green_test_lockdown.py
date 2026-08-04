"""RED tests for GH861 — phase_5 GREEN prompt: §1s test-lockdown front-load + OUTPUT ack.

Spec: SHARED/memory/Decisions/2026-07-15_GH861_green_1s_frontload_spec.md

Contract: GREEN will add a new module-level helper
`_green_test_lockdown_block(red_test_paths: list[str] | None) -> str` in
`phase_5_implement.py`, front-load its output into `_build_green_prompt`
immediately after the scratchpad first-block read (before ROLE:), and
replace the single-line OUTPUT contract with a two-line ack contract.

§1i: no time/singleton ACs — n/a.
§1q: no spec_from_file_location / exec_module — module imported via sys.path,
mirroring tests/test_green_test_feedback_AEC7E800.py.
D1CF5FDF: the not-yet-existing helper `_green_test_lockdown_block` is NEVER
referenced at module import time — every access is via
`getattr(mod, "_green_test_lockdown_block", None)` inside a test body, so
this file COLLECTS today and FAILS at assert time.

All 8 tests are expected to FAIL pre-GREEN, 0 PASS.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).parent
ENGINE_ROOT = HERE.parent

from bytedigger_engine.contracts import StepResult, WorkflowContext  # noqa: E402
from bytedigger_engine.workflows import phase_5_implement as mod  # noqa: E402


# ─── helpers ──────────────────────────────────────────────────────────────────


def _make_ctx(tmp_path: Path, scratchpad: Path) -> WorkflowContext:
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={"scratchpad_dir": str(scratchpad), "git_cwd": str(tmp_path)},
        question="fix the thing",
        session_id="test-session",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_scratchpad_files(scratchpad: Path) -> dict:
    scratchpad.mkdir(parents=True, exist_ok=True)
    spec_path = scratchpad / "spec.md"
    spec_path.write_text("## Spec\nDo thing.\n", encoding="utf-8")
    red_log = scratchpad / "build-red-output.log"
    red_log.write_text("RED COMPLETE\n", encoding="utf-8")
    validation_doc = scratchpad / "validation.md"
    validation_doc.write_text("## Verdict\nPASS\n", encoding="utf-8")
    first_block = scratchpad / "first-block.md"
    first_block.write_text("TASK: fix the thing\n", encoding="utf-8")
    return {
        "spec_path": str(spec_path),
        "red_log_path": str(red_log),
        "validation_doc_path": str(validation_doc),
    }


def _make_stepresult_prev(scratchpad: Path, red_test_paths: list) -> StepResult:
    files = _make_scratchpad_files(scratchpad)
    data = dict(files)
    data["red_test_paths"] = red_test_paths
    return StepResult(status="ok", data=data, duration_ms=0, step_name="gate_on_validation")


def _make_retry_dict_prev(scratchpad: Path) -> dict:
    """§1ab re-entry: retry facade dict WITHOUT red_test_paths (fallback path)."""
    files = _make_scratchpad_files(scratchpad)
    d = dict(files)
    d["cycle"] = 2
    return d


def _get_written_prompt(result: StepResult) -> str:
    data = result.data or {}
    prompt = data.get("prompt")
    assert isinstance(prompt, str), f"expected prompt str in data, got keys={list(data.keys())!r}"
    return prompt


# ═══════════════════════════════════════════════════════════════════════════════
# AC1 — helper returns lockdown block with both paths
# ═══════════════════════════════════════════════════════════════════════════════


class TestAC1HelperBlockWithPaths:

    def test_ac1_helper_returns_block_with_paths(self):
        fn = getattr(mod, "_green_test_lockdown_block", None)
        assert fn is not None, (
            "FAILS pre-GREEN: _green_test_lockdown_block helper does not exist yet in phase_5_implement.py"
        )
        block = fn(["tests/a.py", "tests/b.py"])
        assert "## TEST LOCKDOWN" in block, f"missing header; got {block[:200]!r}"
        assert "E_RED_TESTS_TAMPERED" in block, f"missing error code cite; got {block[:200]!r}"
        assert "§1s" in block, f"missing §1s cite; got {block[:200]!r}"
        for path in ("tests/a.py", "tests/b.py"):
            found = any(
                line.strip() == f"- {path}" for line in block.splitlines()
            )
            assert found, f"expected a '  - {path}' line in block; got:\n{block}"


# ═══════════════════════════════════════════════════════════════════════════════
# AC2 — helper fallback for empty/None
# ═══════════════════════════════════════════════════════════════════════════════


class TestAC2HelperFallback:

    def test_ac2_helper_empty_list_uses_fallback_line(self):
        fn = getattr(mod, "_green_test_lockdown_block", None)
        assert fn is not None, "FAILS pre-GREEN: _green_test_lockdown_block helper missing"
        block = fn([])
        assert "(paths unavailable — treat EVERY test file in the repo as frozen)" in block, (
            f"expected fallback line; got:\n{block}"
        )
        assert "  - " not in block, f"expected no '  - ' path lines on empty input; got:\n{block}"

    def test_ac2_helper_none_uses_fallback_line(self):
        fn = getattr(mod, "_green_test_lockdown_block", None)
        assert fn is not None, "FAILS pre-GREEN: _green_test_lockdown_block helper missing"
        block = fn(None)
        assert "(paths unavailable — treat EVERY test file in the repo as frozen)" in block, (
            f"expected fallback line; got:\n{block}"
        )
        assert "  - " not in block, f"expected no '  - ' path lines on None input; got:\n{block}"


# ═══════════════════════════════════════════════════════════════════════════════
# AC3 — front-load ordering in _build_green_prompt (StepResult prev)
# ═══════════════════════════════════════════════════════════════════════════════


class TestAC3FrontLoadOrdering:

    def test_ac3_lockdown_block_precedes_role_line(self, tmp_path):
        scratchpad = tmp_path / "scratch"
        prev = _make_stepresult_prev(scratchpad, ["tests/x.py"])
        ctx = _make_ctx(tmp_path, scratchpad)

        result = mod._build_green_prompt(ctx, prev)
        assert result.status == "ok", (
            f"expected ok, got {result.status!r} (error_code={result.error_code!r})"
        )
        prompt = _get_written_prompt(result)

        assert "## TEST LOCKDOWN" in prompt, (
            "FAILS pre-GREEN: lockdown block not injected into _build_green_prompt output"
        )
        role_marker = "ROLE: You are the GREEN worker"
        assert role_marker in prompt, f"expected ROLE marker present; prompt head: {prompt[:300]!r}"
        assert prompt.index("## TEST LOCKDOWN") < prompt.index(role_marker), (
            "FAILS pre-GREEN: lockdown block must appear BEFORE the ROLE: line (front-load)"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# AC4 — frozen test file paths listed
# ═══════════════════════════════════════════════════════════════════════════════


class TestAC4FrozenPathsListed:

    def test_ac4_frozen_test_paths_listed_in_prompt(self, tmp_path):
        scratchpad = tmp_path / "scratch"
        prev = _make_stepresult_prev(scratchpad, ["tests/x.py"])
        ctx = _make_ctx(tmp_path, scratchpad)

        result = mod._build_green_prompt(ctx, prev)
        assert result.status == "ok", (
            f"expected ok, got {result.status!r} (error_code={result.error_code!r})"
        )
        prompt = _get_written_prompt(result)
        found = any(line.strip() == "- tests/x.py" for line in prompt.splitlines())
        assert found, (
            "FAILS pre-GREEN: expected '  - tests/x.py' bullet line in FROZEN TEST FILES section; "
            f"prompt does not contain it. Prompt head: {prompt[:400]!r}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# AC5 — retry path (dict prev, no red_test_paths) still gets lockdown w/ fallback
# ═══════════════════════════════════════════════════════════════════════════════


class TestAC5RetryPathFallback:

    def test_ac5_retry_dict_prev_without_red_test_paths_gets_fallback_lockdown(self, tmp_path):
        scratchpad = tmp_path / "scratch"
        prev_dict = _make_retry_dict_prev(scratchpad)
        assert "red_test_paths" not in prev_dict

        ctx = _make_ctx(tmp_path, scratchpad)
        result = mod._build_green_prompt(ctx, prev_dict)
        assert result.status == "ok", (
            f"expected ok, got {result.status!r} (error_code={result.error_code!r})"
        )
        prompt = _get_written_prompt(result)
        assert "## TEST LOCKDOWN" in prompt, (
            "FAILS pre-GREEN: lockdown block missing on retry-dict path (no red_test_paths)"
        )
        assert "(paths unavailable — treat EVERY test file in the repo as frozen)" in prompt, (
            "FAILS pre-GREEN: expected fallback line when red_test_paths absent from retry prev dict"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# AC6 — two-line OUTPUT ack contract, correct order
# ═══════════════════════════════════════════════════════════════════════════════


class TestAC6TwoLineOutputContract:

    def test_ac6_output_contract_has_tests_untouched_before_green_complete(self, tmp_path):
        scratchpad = tmp_path / "scratch"
        prev = _make_stepresult_prev(scratchpad, ["tests/x.py"])
        ctx = _make_ctx(tmp_path, scratchpad)

        result = mod._build_green_prompt(ctx, prev)
        assert result.status == "ok", (
            f"expected ok, got {result.status!r} (error_code={result.error_code!r})"
        )
        prompt = _get_written_prompt(result)

        untouched_line = "TESTS UNTOUCHED: no test file was modified."
        assert untouched_line in prompt, (
            "FAILS pre-GREEN: two-line OUTPUT ack contract not present "
            f"(missing {untouched_line!r})"
        )
        complete_marker = "GREEN COMPLETE — all [N] tests passing"
        assert complete_marker in prompt, f"expected template complete-line marker; got tail: {prompt[-400:]!r}"
        assert prompt.index(untouched_line) < prompt.index(complete_marker), (
            "FAILS pre-GREEN: TESTS UNTOUCHED line must appear BEFORE the GREEN COMPLETE template line"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# AC7 — prompt written to disk (log_path) matches result.data["prompt"]
# ═══════════════════════════════════════════════════════════════════════════════


class TestAC7WrittenPromptOnDisk:

    def test_ac7_disk_prompt_matches_data_prompt_and_has_lockdown(self, tmp_path):
        scratchpad = tmp_path / "scratch"
        prev = _make_stepresult_prev(scratchpad, ["tests/x.py"])
        ctx = _make_ctx(tmp_path, scratchpad)

        result = mod._build_green_prompt(ctx, prev)
        assert result.status == "ok", (
            f"expected ok, got {result.status!r} (error_code={result.error_code!r})"
        )
        data = result.data or {}
        log_path = data.get("log_path")
        assert log_path, f"expected log_path in data, got keys={list(data.keys())!r}"
        on_disk = Path(log_path).read_text(encoding="utf-8")
        assert on_disk == data["prompt"], (
            "on-disk prompt does not match result.data['prompt']"
        )
        assert "## TEST LOCKDOWN" in on_disk, (
            "FAILS pre-GREEN: lockdown block absent from prompt written to disk at log_path"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# AC8 — old single-line OUTPUT wording is absent (replaced, not duplicated)
# ═══════════════════════════════════════════════════════════════════════════════


class TestAC8OldOutputWordingAbsent:

    def test_ac8_old_single_line_output_wording_absent(self, tmp_path):
        scratchpad = tmp_path / "scratch"
        prev = _make_stepresult_prev(scratchpad, ["tests/x.py"])
        ctx = _make_ctx(tmp_path, scratchpad)

        result = mod._build_green_prompt(ctx, prev)
        assert result.status == "ok", (
            f"expected ok, got {result.status!r} (error_code={result.error_code!r})"
        )
        prompt = _get_written_prompt(result)
        old_wording = "end your response with EXACTLY this line:"
        assert old_wording not in prompt, (
            f"FAILS pre-GREEN: the OLD single-line OUTPUT wording {old_wording!r} is still "
            "present today — GREEN must replace it with the two-line ack contract."
        )
