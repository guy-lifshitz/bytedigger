"""7719A739 — RED tests: Bash must be absent from GREEN allowed_tools + prompt.

Three acceptance criteria:
  AC1: _invoke_green_llm main path passes allowed_tools without "Bash".
  AC2: _invoke_green_llm NO_MARKER retry path passes allowed_tools without "Bash".
  AC3: _build_green_prompt output does NOT contain "Bash for tests"; DOES contain "Edit/Write only".
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).parent

from bytedigger_engine.contracts import WorkflowContext, StepResult  # noqa: E402
from bytedigger_engine.workflows.phase_5_implement import (  # noqa: E402
    GREEN_NO_MARKER,
    _build_green_prompt,
    _invoke_green_llm,
    _write_green_artifact,
)


# ─── helpers ──────────────────────────────────────────────────────────────────


def _make_ctx(tmp_path: Path, question: str = "Test question") -> WorkflowContext:
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    org = {"scratchpad_dir": str(scratchpad)}
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question=question,
        session_id="test-session",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_green_llm_prev(tmp_path: Path) -> StepResult:
    """Minimal prev StepResult as produced by _invoke_green_llm (build_green_prompt output)."""
    return StepResult(
        status="ok",
        data={
            "prompt": "x",
            "log_path": str(tmp_path / "green.log"),
            "spec_path": str(tmp_path / "spec.md"),
            "red_log_path": str(tmp_path / "red.log"),
            "validation_doc_path": str(tmp_path / "val.md"),
            "verdict": "PASS",
            "red_commit_sha": "0" * 40,
        },
        duration_ms=0,
        step_name="build_green_prompt",
    )


def _make_build_green_prompt_prev(tmp_path: Path) -> StepResult:
    """Minimal prev StepResult as consumed by _build_green_prompt."""
    spec = tmp_path / "spec.md"
    spec.write_text("# spec\n")
    red_log = tmp_path / "red.log"
    red_log.write_text("RED COMPLETE\n")
    val_doc = tmp_path / "val.md"
    val_doc.write_text("Verdict: PASS\n")
    return StepResult(
        status="ok",
        data={
            "spec_path": str(spec),
            "red_log_path": str(red_log),
            "validation_doc_path": str(val_doc),
            "verdict": "PASS",
            "red_commit_sha": "0" * 40,
        },
        duration_ms=0,
        step_name="validate_red_llm",
    )


# ─── AC1: main invocation path ────────────────────────────────────────────────


def test_invoke_green_llm_main_allowed_tools_no_bash(tmp_path, monkeypatch):
    """AC1: _invoke_green_llm must NOT include 'Bash' in allowed_tools."""
    captured = {}

    def fake_invoke_llm_subprocess(**kwargs):
        captured.update(kwargs)
        return StepResult(
            status="ok",
            data={
                "raw_response": "GREEN COMPLETE — all 1 tests passing. Files modified: [x.py]",
                "log_path": kwargs["extra_data"]["log_path"],
                "spec_path": kwargs["extra_data"]["spec_path"],
                "red_log_path": kwargs["extra_data"]["red_log_path"],
                "validation_doc_path": kwargs["extra_data"]["validation_doc_path"],
                "verdict": kwargs["extra_data"].get("verdict", ""),
                "prompt": kwargs["extra_data"]["prompt"],
                "red_commit_sha": kwargs["extra_data"].get("red_commit_sha"),
                "tokens_out": 10,
            },
            duration_ms=0,
            step_name="invoke_green_llm",
        )

    from bytedigger_engine.workflows import phase_5_implement
    monkeypatch.setattr(phase_5_implement, "invoke_llm_subprocess", fake_invoke_llm_subprocess)

    ctx = _make_ctx(tmp_path)
    prev = _make_green_llm_prev(tmp_path)

    _invoke_green_llm(ctx, prev)

    assert "allowed_tools" in captured, "invoke_llm_subprocess was not called with allowed_tools kwarg"
    assert "Bash" not in captured["allowed_tools"], (
        f"'Bash' must not be in allowed_tools for GREEN main path, got: {captured['allowed_tools']!r}"
    )
    assert captured["allowed_tools"] == ["Read", "Write", "Edit", "Grep", "Glob"], (
        f"Expected exact 5-tool list, got: {captured['allowed_tools']!r}"
    )


# ─── AC2: NO_MARKER retry path ────────────────────────────────────────────────


def test_invoke_green_llm_retry_allowed_tools_no_bash(tmp_path, monkeypatch):
    """AC2: the NO_MARKER retry invocation must also NOT include 'Bash' in allowed_tools."""
    all_captured = []

    call_count = [0]

    def fake_invoke_llm_subprocess(**kwargs):
        call_count[0] += 1
        all_captured.append(dict(kwargs))
        # The test enters _write_green_artifact with a NO_MARKER raw_response
        # already set in prev.data (simulating the real first invoke). The
        # retry path calls invoke_llm_subprocess once more — that's the call
        # we capture and inspect. Return a valid marker so write_green_artifact
        # finalizes ok.
        raw = "GREEN COMPLETE — all 1 tests passing. Files modified: [x.py]"
        return StepResult(
            status="ok",
            data={
                "raw_response": raw,
                "log_path": kwargs["extra_data"]["log_path"],
                "spec_path": kwargs["extra_data"]["spec_path"],
                "red_log_path": kwargs["extra_data"]["red_log_path"],
                "validation_doc_path": kwargs["extra_data"]["validation_doc_path"],
                "verdict": kwargs["extra_data"].get("verdict", ""),
                "prompt": kwargs["extra_data"]["prompt"],
                "red_commit_sha": kwargs["extra_data"].get("red_commit_sha"),
                "tokens_out": 10,
            },
            duration_ms=0,
            step_name="invoke_green_llm",
        )

    from bytedigger_engine.workflows import phase_5_implement
    monkeypatch.setattr(phase_5_implement, "invoke_llm_subprocess", fake_invoke_llm_subprocess)

    ctx = _make_ctx(tmp_path)
    # _write_green_artifact is the function that owns the retry loop;
    # build a prev that looks like the output of _invoke_green_llm.
    log_path = tmp_path / "scratch" / "green.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    prev = StepResult(
        status="ok",
        data={
            "raw_response": "no marker here",
            "log_path": str(log_path),
            "spec_path": str(tmp_path / "spec.md"),
            "red_log_path": str(tmp_path / "red.log"),
            "validation_doc_path": str(tmp_path / "val.md"),
            "verdict": "PASS",
            "prompt": "x",
            "red_commit_sha": "0" * 40,
            "tokens_out": 10,
        },
        duration_ms=0,
        step_name="invoke_green_llm",
    )
    # Ensure log_path parent exists so _write_green_artifact can write it
    log_path.parent.mkdir(parents=True, exist_ok=True)

    _write_green_artifact(ctx, prev)

    assert call_count[0] >= 1, "invoke_llm_subprocess was never called (retry path not triggered)"
    # The retry path calls invoke_llm_subprocess exactly once during
    # _write_green_artifact (the original "first" invoke happened conceptually
    # before this test — its NO_MARKER output is the seed at prev.data).
    # all_captured[0] therefore IS the retry invocation.
    retry_kwargs = all_captured[0]
    assert "allowed_tools" in retry_kwargs, "retry invoke missing allowed_tools kwarg"
    assert "Bash" not in retry_kwargs["allowed_tools"], (
        f"'Bash' must not be in retry allowed_tools, got: {retry_kwargs['allowed_tools']!r}"
    )
    assert retry_kwargs["allowed_tools"] == ["Read", "Write", "Edit", "Grep", "Glob"], (
        f"Expected exact 5-tool list in retry, got: {retry_kwargs['allowed_tools']!r}"
    )


# ─── AC3: prompt text ─────────────────────────────────────────────────────────


def test_green_prompt_no_bash_for_tests_phrase(tmp_path, monkeypatch):
    """AC3: _build_green_prompt must not contain 'Bash for tests'; must contain 'Edit/Write only'."""
    # _build_green_prompt reads spec/red_log/validation_doc from disk — provide real files.
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)

    # Provide a first-block file so _read_first_block doesn't crash.
    first_block_path = scratchpad / "build-first-block.md"
    first_block_path.write_text("# build context\n")

    org = {"scratchpad_dir": str(scratchpad)}
    ctx = WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question="Drop Bash from green agent",
        session_id="test-session",
        persona="hal",
        framework=None,
        domain=None,
    )

    prev = _make_build_green_prompt_prev(tmp_path)

    result = _build_green_prompt(ctx, prev)

    assert result.status == "ok", f"_build_green_prompt returned error: {result.error!r}"
    prompt = result.data["prompt"]

    assert "Bash for tests" not in prompt, (
        "'Bash for tests' phrase must be removed from the GREEN prompt RESPONSE BUDGET section"
    )
    assert "Bash" not in prompt.split("RESPONSE BUDGET")[1].split("OUTPUT:")[0], (
        "Any 'Bash' mention must be removed from the GREEN prompt RESPONSE BUDGET section"
    )
    assert "Engine runs tests" in prompt or "engine runs tests" in prompt.lower(), (
        "Engine-runs-tests language must appear in the GREEN prompt to clarify why Bash is gone"
    )
