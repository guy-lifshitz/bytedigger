"""Tests for _standards_context helper + wiring into phase_45_spec / phase_5_implement.

Stage 3 of agreement 27843297.

AC1-AC9: Unit tests for the `get_standards_context` helper in `_standards_context.py`
         (module does NOT exist yet — all tests fail with ImportError until GREEN ships).
AC10-AC12: Wiring tests proving `get_standards_context` is called and its return value
           is prepended to the prompt in the three target prompt-builders.

Wiring test form (AC10-12): SENTINEL-IN-PROMPT (preferred).
Rationale: `_build_spec_prompt`, `_build_red_prompt`, and `_build_green_prompt` all
assemble the prompt via a `parts` list joined before return; they never read spec/arch
files into the string (only embed their paths). A minimal `ctx` with `scratchpad_dir`
set in `org_config` is sufficient to reach the prepend site without crashing.
For `_build_green_prompt` a stub `StepResult` with `spec_path`, `red_log_path`, and
`validation_doc_path` keys is required to pass the early-exit guard at L2116-2122.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

HERE = Path(__file__).parent

from bytedigger_engine.contracts import WorkflowContext, StepResult  # noqa: E402


# ─── helpers (verbatim from test_phase_5_devops_scan.py) ─────────────────────


def _bun_calls(mock_run):
    """Filter mock subprocess.run calls down to bun-invocation calls only.

    WorkflowEngine itself calls subprocess.run(['git', ...]) for change-tracking
    (engine.py:427). Module-level subprocess patching catches those too. Tests
    that probe _standards_context's bun invocation must filter to isolate the signal.
    """
    return [
        c for c in mock_run.call_args_list
        if c.args and len(c.args[0]) > 0 and c.args[0][0] == "bun"
    ]


def _stub_proc(
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> MagicMock:
    p = MagicMock(spec=subprocess.CompletedProcess)
    p.returncode = returncode
    p.stdout = stdout
    p.stderr = stderr
    return p


def make_ctx(
    tmp_path: Path,
    artifact_type: str | None = "__unset__",
    **org_extra,
) -> WorkflowContext:
    org: dict = {"scratchpad_dir": str(tmp_path), **org_extra}
    if artifact_type != "__unset__":
        org["artifact_type"] = artifact_type
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question="test standards context",
        session_id="test-session",
        persona="hal",
        framework=None,
        domain=None,
    )


# ─── AC1: missing artifact_type key → no subprocess, returns "" ──────────────


def test_helper_returns_empty_when_artifact_type_key_missing():
    """AC1: org_config has no 'artifact_type' key → helper returns '' without invoking subprocess."""
    from bytedigger_engine.workflows import _standards_context  # ImportError until GREEN ships

    ctx = WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={},  # no artifact_type key at all
        question="test",
        session_id="test-session",
        persona="hal",
        framework=None,
        domain=None,
    )

    with patch("bytedigger_engine.workflows._standards_context.subprocess.run") as mock_run:
        result = _standards_context.get_standards_context(ctx)

    assert result == "", f"expected '' when artifact_type key absent, got {result!r}"
    assert _bun_calls(mock_run) == [], (
        f"expected no bun invocations when artifact_type key absent, got {_bun_calls(mock_run)}"
    )


# ─── AC2: artifact_type is None → no subprocess, returns "" ──────────────────


def test_helper_returns_empty_when_artifact_type_none(tmp_path):
    """AC2: artifact_type=None → helper returns '' without invoking subprocess."""
    from bytedigger_engine.workflows import _standards_context

    ctx = make_ctx(tmp_path, artifact_type=None)

    with patch("bytedigger_engine.workflows._standards_context.subprocess.run") as mock_run:
        result = _standards_context.get_standards_context(ctx)

    assert result == "", f"expected '' when artifact_type=None, got {result!r}"
    assert _bun_calls(mock_run) == [], (
        f"expected no bun invocations when artifact_type=None, got {_bun_calls(mock_run)}"
    )


# ─── AC3: artifact_type is "" → no subprocess, returns "" ────────────────────


def test_helper_returns_empty_when_artifact_type_empty_string(tmp_path):
    """AC3: artifact_type='' (empty string) → helper returns '' without invoking subprocess."""
    from bytedigger_engine.workflows import _standards_context

    ctx = make_ctx(tmp_path, artifact_type="")

    with patch("bytedigger_engine.workflows._standards_context.subprocess.run") as mock_run:
        result = _standards_context.get_standards_context(ctx)

    assert result == "", f"expected '' when artifact_type='', got {result!r}"
    assert _bun_calls(mock_run) == [], (
        f"expected no bun invocations when artifact_type='', got {_bun_calls(mock_run)}"
    )


# ─── AC4: subprocess argv shape ──────────────────────────────────────────────


def test_helper_subprocess_argv_shape(tmp_path):
    """AC4: cmd[0]='bun'; cmd[1] ends with 'SYSTEM/cli/build/devops-prompt-context.ts';
    '--artifact-type' + value + '--output' + 'json' all present in argv.
    """
    from bytedigger_engine.workflows import _standards_context

    ctx = make_ctx(tmp_path, artifact_type="dockerfile")
    shim_stdout = json.dumps({
        "artifact_type": "dockerfile",
        "standards_context": "## TEST STANDARDS\n",
        "skipped_reason": None,
    })

    with patch(
        "bytedigger_engine.workflows._standards_context.subprocess.run",
        return_value=_stub_proc(stdout=shim_stdout),
    ) as mock_run:
        _standards_context.get_standards_context(ctx)

    bun = _bun_calls(mock_run)
    assert len(bun) == 1, f"expected exactly 1 bun call, got {len(bun)}"
    cmd = list(bun[0].args[0])

    assert cmd[0] == "bun", f"expected cmd[0]='bun', got {cmd[0]!r}"
    assert cmd[1].endswith("SYSTEM/cli/build/devops-prompt-context.ts"), (
        f"expected cmd[1] to end with 'SYSTEM/cli/build/devops-prompt-context.ts', got {cmd[1]!r}"
    )
    cmd_str = " ".join(cmd)
    assert "--artifact-type" in cmd_str, f"expected '--artifact-type' in argv, got {cmd!r}"
    assert "dockerfile" in cmd_str, f"expected artifact value in argv, got {cmd!r}"
    assert "--output" in cmd_str, f"expected '--output' in argv, got {cmd!r}"
    assert "json" in cmd_str, f"expected 'json' in argv, got {cmd!r}"


# ─── AC5: happy path dockerfile ──────────────────────────────────────────────


def test_helper_happy_path_dockerfile(tmp_path):
    """AC5: artifact_type='dockerfile' + mocked subprocess → returns string starting
    with '## TEST STANDARDS' and ending with '\\n\\n'.
    """
    from bytedigger_engine.workflows import _standards_context

    ctx = make_ctx(tmp_path, artifact_type="dockerfile")
    shim_stdout = json.dumps({
        "artifact_type": "dockerfile",
        "standards_context": "## TEST STANDARDS",
        "skipped_reason": None,
    })

    with patch(
        "bytedigger_engine.workflows._standards_context.subprocess.run",
        return_value=_stub_proc(stdout=shim_stdout),
    ):
        result = _standards_context.get_standards_context(ctx)

    assert isinstance(result, str), f"expected str, got {type(result)}"
    assert result.startswith("## TEST STANDARDS"), (
        f"expected result to start with '## TEST STANDARDS', got {result!r}"
    )
    assert result.endswith("\n\n"), (
        f"expected result to end with '\\n\\n', got {result!r}"
    )


# ─── AC6: FileNotFoundError → returns "" ─────────────────────────────────────


def test_helper_filenotfound_returns_empty(tmp_path):
    """AC6: FileNotFoundError on bun invocation → helper returns '' without raising."""
    from bytedigger_engine.workflows import _standards_context

    ctx = make_ctx(tmp_path, artifact_type="dockerfile")

    with patch(
        "bytedigger_engine.workflows._standards_context.subprocess.run",
        side_effect=FileNotFoundError("bun not found"),
    ):
        result = _standards_context.get_standards_context(ctx)

    assert result == "", f"expected '' on FileNotFoundError, got {result!r}"


# ─── AC7: TimeoutExpired → returns "" ────────────────────────────────────────


def test_helper_timeout_returns_empty(tmp_path):
    """AC7: subprocess.TimeoutExpired → helper returns '' without raising."""
    from bytedigger_engine.workflows import _standards_context

    ctx = make_ctx(tmp_path, artifact_type="dockerfile")

    with patch(
        "bytedigger_engine.workflows._standards_context.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd=["bun"], timeout=30),
    ):
        result = _standards_context.get_standards_context(ctx)

    assert result == "", f"expected '' on TimeoutExpired, got {result!r}"


# ─── AC8: subprocess rc != 0 → returns "" ────────────────────────────────────


def test_helper_subprocess_nonzero_returns_empty(tmp_path):
    """AC8: subprocess rc=1 → helper returns '' without raising."""
    from bytedigger_engine.workflows import _standards_context

    ctx = make_ctx(tmp_path, artifact_type="dockerfile")

    with patch(
        "bytedigger_engine.workflows._standards_context.subprocess.run",
        return_value=_stub_proc(returncode=1, stderr="internal error"),
    ):
        result = _standards_context.get_standards_context(ctx)

    assert result == "", f"expected '' on rc=1, got {result!r}"


# ─── AC9: malformed JSON → returns "" ────────────────────────────────────────


def test_helper_malformed_json_returns_empty(tmp_path):
    """AC9: rc=0 but stdout is malformed JSON → helper returns '' without raising."""
    from bytedigger_engine.workflows import _standards_context

    ctx = make_ctx(tmp_path, artifact_type="dockerfile")

    with patch(
        "bytedigger_engine.workflows._standards_context.subprocess.run",
        return_value=_stub_proc(returncode=0, stdout="not-json{{{"),
    ):
        result = _standards_context.get_standards_context(ctx)

    assert result == "", f"expected '' on json decode error, got {result!r}"


# ─── AC10: phase_45_spec._build_spec_prompt prepends sentinel ────────────────


def test_spec_prompt_prepends_standards_when_artifact_type_set(tmp_path):
    """AC10: patch phase_45_spec.get_standards_context → sentinel appears at prompt[0].

    Form: SENTINEL-IN-PROMPT (preferred).
    _build_spec_prompt assembles via parts list; no file reads happen in the
    prompt string — only path references are embedded. A minimal ctx with
    scratchpad_dir is sufficient to reach the prepend site without crashing.
    get_standards_context is imported into phase_45_spec at module level; we
    patch it there. Import error until GREEN ships (module not yet wired).
    """
    from bytedigger_engine.workflows import phase_45_spec  # noqa: F401 — will exist, wiring import is the target

    ctx = make_ctx(tmp_path, artifact_type="dockerfile")
    sentinel = "### TEST_STANDARDS_SENTINEL ###\n\n"

    with patch("bytedigger_engine.workflows.phase_45_spec.get_standards_context", return_value=sentinel) as mock_helper:
        step_result = phase_45_spec._build_spec_prompt(ctx, _prev=None)

    assert isinstance(step_result, StepResult), (
        f"expected StepResult, got {type(step_result)}"
    )
    assert step_result.status == "ok", (
        f"expected status='ok', got {step_result.status!r} (error: {step_result.error!r})"
    )
    prompt = step_result.data["prompt"]
    assert isinstance(prompt, str), f"expected prompt to be str, got {type(prompt)}"
    assert "### TEST_STANDARDS_SENTINEL ###" in prompt, (
        f"expected sentinel in prompt, got prompt starting with {prompt[:120]!r}"
    )
    assert prompt.startswith("### TEST_STANDARDS_SENTINEL ###"), (
        f"expected prompt to START with sentinel (index 0), got {prompt[:120]!r}"
    )
    mock_helper.assert_called_once_with(ctx)


# ─── AC11: phase_5_implement._build_red_prompt prepends sentinel ─────────────


def test_red_prompt_prepends_standards_when_artifact_type_set(tmp_path):
    """AC11: patch phase_5_implement.get_standards_context → sentinel at prompt[0].

    Form: SENTINEL-IN-PROMPT (preferred).
    _build_red_prompt accepts _prev=None (dict/StepResult guard only affects
    cycle/findings extraction, not crash). Minimal ctx with scratchpad_dir sufficient.
    Import error until GREEN ships (module not yet wired).
    """
    from bytedigger_engine.workflows import phase_5_implement  # noqa: F401 — will exist, wiring import is the target

    ctx = make_ctx(tmp_path, artifact_type="dockerfile")
    sentinel = "### TEST_STANDARDS_SENTINEL ###\n\n"

    with patch("bytedigger_engine.workflows.phase_5_implement.get_standards_context", return_value=sentinel) as mock_helper:
        step_result = phase_5_implement._build_red_prompt(ctx, _prev=None, findings=None)

    assert isinstance(step_result, StepResult), (
        f"expected StepResult, got {type(step_result)}"
    )
    assert step_result.status == "ok", (
        f"expected status='ok', got {step_result.status!r} (error: {step_result.error!r})"
    )
    prompt = step_result.data["prompt"]
    assert isinstance(prompt, str), f"expected prompt to be str, got {type(prompt)}"
    assert "### TEST_STANDARDS_SENTINEL ###" in prompt, (
        f"expected sentinel in prompt, got prompt starting with {prompt[:120]!r}"
    )
    assert prompt.startswith("### TEST_STANDARDS_SENTINEL ###"), (
        f"expected prompt to START with sentinel (index 0), got {prompt[:120]!r}"
    )
    mock_helper.assert_called_once_with(ctx)


# ─── AC12: phase_5_implement._build_green_prompt prepends sentinel ───────────


def test_green_prompt_prepends_standards_when_artifact_type_set(tmp_path):
    """AC12: patch phase_5_implement.get_standards_context → sentinel at prompt[0].

    Form: SENTINEL-IN-PROMPT (preferred).
    _build_green_prompt has an early-exit guard requiring prev to be a StepResult
    with data keys: spec_path, red_log_path, validation_doc_path. We stub those
    with tmp_path-relative paths (the builder embeds them as strings only —
    no file reads occur in the string assembly path). Minimal ctx with
    scratchpad_dir sufficient. Import error until GREEN ships.
    """
    from bytedigger_engine.workflows import phase_5_implement  # noqa: F401

    ctx = make_ctx(tmp_path, artifact_type="dockerfile")
    sentinel = "### TEST_STANDARDS_SENTINEL ###\n\n"

    # Minimal StepResult stub to pass the guard at _build_green_prompt:L2116-2122
    prev_stub = StepResult(
        status="ok",
        data={
            "spec_path": str(tmp_path / "specs" / "build-spec.md"),
            "red_log_path": str(tmp_path / "tests" / "build-red-output.log"),
            "validation_doc_path": str(tmp_path / "tests" / "build-validation.md"),
        },
        duration_ms=0,
        step_name="stub_prev",
    )

    with patch("bytedigger_engine.workflows.phase_5_implement.get_standards_context", return_value=sentinel) as mock_helper:
        step_result = phase_5_implement._build_green_prompt(ctx, prev=prev_stub)

    assert isinstance(step_result, StepResult), (
        f"expected StepResult, got {type(step_result)}"
    )
    assert step_result.status == "ok", (
        f"expected status='ok', got {step_result.status!r} (error: {step_result.error!r})"
    )
    prompt = step_result.data["prompt"]
    assert isinstance(prompt, str), f"expected prompt to be str, got {type(prompt)}"
    assert "### TEST_STANDARDS_SENTINEL ###" in prompt, (
        f"expected sentinel in prompt, got prompt starting with {prompt[:120]!r}"
    )
    assert prompt.startswith("### TEST_STANDARDS_SENTINEL ###"), (
        f"expected prompt to START with sentinel (index 0), got {prompt[:120]!r}"
    )
    mock_helper.assert_called_once_with(ctx)
