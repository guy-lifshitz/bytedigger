"""24E29725 — RED tests: Bash must be absent from FIX allowed_tools + prompt.

Three acceptance criteria:
  AC1: _invoke_fix_llm main path passes allowed_tools without "Bash".
  AC2: _write_fix_artifact NO_MARKER retry path passes allowed_tools without "Bash".
  AC3: _build_fix_prompt output does NOT contain "Edit/Write/Bash"; DOES contain "Edit/Write".
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
sys.path.insert(0, str(HERE.parent / "workflows"))

from contracts import WorkflowContext, StepResult  # noqa: E402
from phase_6_review import (  # noqa: E402
    FIX_NO_MARKER,
    _build_fix_prompt,
    _invoke_fix_llm,
    _write_fix_artifact,
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


def _make_fix_llm_prev(tmp_path: Path) -> StepResult:
    """Minimal prev StepResult as produced by _build_fix_prompt."""
    return StepResult(
        status="ok",
        data={
            "prompt": "x",
            "log_path": str(tmp_path / "fix.log"),
            "spec_path": str(tmp_path / "spec.md"),
            "review_doc_path": str(tmp_path / "review.md"),
            "verdict": "FAIL",
        },
        duration_ms=0,
        step_name="build_fix_prompt",
    )


def _make_build_fix_prompt_prev(tmp_path: Path) -> StepResult:
    """Minimal prev StepResult as consumed by _build_fix_prompt."""
    spec = tmp_path / "spec.md"
    spec.write_text("# spec\n")
    review = tmp_path / "review.md"
    review.write_text("## Aggregated Findings\n\nFAIL — 1 finding.\n")
    return StepResult(
        status="ok",
        data={
            "spec_path": str(spec),
            "review_doc_path": str(review),
            "verdict": "FAIL",
        },
        duration_ms=0,
        step_name="invoke_review_llm",
    )


# ─── AC1: main invocation path ────────────────────────────────────────────────


def test_invoke_fix_llm_main_allowed_tools_no_bash(tmp_path, monkeypatch):
    """AC1: _invoke_fix_llm must NOT include 'Bash' in allowed_tools."""
    captured = {}

    def fake_invoke_llm_subprocess(**kwargs):
        captured.update(kwargs)
        return StepResult(
            status="ok",
            data={
                "raw_response": "FIX COMPLETE — 1 of 1 findings fixed. Files: [x.py]",
                "log_path": kwargs["extra_data"]["log_path"],
                "spec_path": kwargs["extra_data"]["spec_path"],
                "review_doc_path": kwargs["extra_data"]["review_doc_path"],
                "verdict": kwargs["extra_data"].get("verdict", ""),
                "prompt": kwargs["extra_data"]["prompt"],
                "tokens_out": 10,
            },
            duration_ms=0,
            step_name="invoke_fix_llm",
        )

    import phase_6_review
    monkeypatch.setattr(phase_6_review, "invoke_llm_subprocess", fake_invoke_llm_subprocess)

    ctx = _make_ctx(tmp_path)
    prev = _make_fix_llm_prev(tmp_path)

    _invoke_fix_llm(ctx, prev)

    assert "allowed_tools" in captured, "invoke_llm_subprocess was not called with allowed_tools kwarg"
    assert "Bash" not in captured["allowed_tools"], (
        f"'Bash' must not be in allowed_tools for FIX main path, got: {captured['allowed_tools']!r}"
    )
    assert captured["allowed_tools"] == ["Read", "Write", "Edit", "Grep", "Glob"], (
        f"Expected exact 5-tool list, got: {captured['allowed_tools']!r}"
    )


# ─── AC2: NO_MARKER retry path ────────────────────────────────────────────────


def test_invoke_fix_llm_retry_allowed_tools_no_bash(tmp_path, monkeypatch):
    """AC2: the NO_MARKER retry invocation must also NOT include 'Bash' in allowed_tools."""
    all_captured = []

    def fake_invoke_llm_subprocess(**kwargs):
        all_captured.append(dict(kwargs))
        raw = "FIX COMPLETE — 1 of 1 findings fixed. Files: [x.py]"
        return StepResult(
            status="ok",
            data={
                "raw_response": raw,
                "log_path": kwargs["extra_data"]["log_path"],
                "spec_path": kwargs["extra_data"]["spec_path"],
                "review_doc_path": kwargs["extra_data"]["review_doc_path"],
                "verdict": kwargs["extra_data"].get("verdict", ""),
                "prompt": kwargs["extra_data"]["prompt"],
                "tokens_out": 10,
            },
            duration_ms=0,
            step_name="invoke_fix_llm",
        )

    import phase_6_review
    monkeypatch.setattr(phase_6_review, "invoke_llm_subprocess", fake_invoke_llm_subprocess)

    ctx = _make_ctx(tmp_path)
    log_path = tmp_path / "scratch" / "fix.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    prev = StepResult(
        status="ok",
        data={
            "raw_response": "fix output with no status marker",
            "log_path": str(log_path),
            "spec_path": str(tmp_path / "spec.md"),
            "review_doc_path": str(tmp_path / "review.md"),
            "verdict": "FAIL",
            "prompt": "x",
            "tokens_out": 10,
        },
        duration_ms=0,
        step_name="invoke_fix_llm",
    )

    _write_fix_artifact(ctx, prev)

    assert len(all_captured) >= 1, "invoke_llm_subprocess was never called (retry path not triggered)"
    retry_kwargs = all_captured[0]
    assert "allowed_tools" in retry_kwargs, "retry invoke missing allowed_tools kwarg"
    assert "Bash" not in retry_kwargs["allowed_tools"], (
        f"'Bash' must not be in retry allowed_tools, got: {retry_kwargs['allowed_tools']!r}"
    )
    assert retry_kwargs["allowed_tools"] == ["Read", "Write", "Edit", "Grep", "Glob"], (
        f"Expected exact 5-tool list in retry, got: {retry_kwargs['allowed_tools']!r}"
    )


# ─── AC3: prompt text ─────────────────────────────────────────────────────────


def test_fix_prompt_no_edit_write_bash_phrase(tmp_path):
    """AC3: _build_fix_prompt must not contain 'Edit/Write/Bash'; must contain 'Edit/Write'."""
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)

    first_block_path = scratchpad / "build-first-block.md"
    first_block_path.write_text("# build context\n")

    org = {"scratchpad_dir": str(scratchpad)}
    ctx = WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question="Drop Bash from fix worker",
        session_id="test-session",
        persona="hal",
        framework=None,
        domain=None,
    )

    prev = _make_build_fix_prompt_prev(tmp_path)

    result = _build_fix_prompt(ctx, prev)

    assert result.status == "ok", f"_build_fix_prompt returned error: {result.error!r}"
    prompt = result.data["prompt"]

    assert "Edit/Write/Bash" not in prompt, (
        "'Edit/Write/Bash' phrase must be removed from the FIX prompt ROLE section"
    )
    assert "Edit/Write" in prompt, (
        "'Edit/Write' must remain in the FIX prompt (after dropping the '/Bash' suffix)"
    )
