"""RED tests for C76F6F3C: wire v0 semgrep red_lint into phase_5_implement.

Two contracts under test:

A. New helper `_verify_red_lint_rules(ctx, prev) -> StepResult`
   - Mirrors the contract style of `_verify_red_fails_mechanically` (line 802).
   - Reads `prev.data["red_test_paths"]` (list[str], paths relative to git_cwd).
   - Invokes semgrep with rules.yml against those paths.
   - Returns status="error" if F1 (hardcoded-line-number-in-where-assertion,
     ERROR severity) fires.
   - Returns status="ok" if no F1 (F3 schema-referent WARNING is advisory only).
   - Returns status="error" on missing/empty/non-list red_test_paths or missing
     prev.data.

B. `_build_red_prompt` (line 419) emits 3 grounding rules.
   - Returned prompt string in `data["prompt"]` must include a GROUNDING RULES
     section with at least 3 distinct rule entries, plus references to F1 and
     F3 (or their human-readable aliases).

These tests must FAIL today (helper does not exist + prompt lacks the section).
After GREEN-step implementation in phase_5_implement.py, all 6 should pass.

Tests skip cleanly if semgrep is not installed (Contract A only).
"""
from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent

from bytedigger_engine.contracts import StepResult, WorkflowContext  # noqa: E402
from bytedigger_engine.workflows import phase_5_implement  # noqa: E402


# ─── shared fixtures ────────────────────────────────────────────────────────


def _make_ctx(tmp_path: Path) -> WorkflowContext:
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={"git_cwd": str(tmp_path)},
        question="q",
        session_id="test-session",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_prev(red_test_paths) -> StepResult:
    data: dict = {
        "red_log_path": "tests/build-red-output.log",
        "red_bytes_written": 42,
        "cycle": 1,
        "spec_path": "scratchpad/spec.md",
    }
    if red_test_paths is not _SENTINEL:
        data["red_test_paths"] = red_test_paths
    return StepResult(
        status="ok",
        data=data,
        duration_ms=0,
        step_name="commit_red_tests",
    )


_SENTINEL: object = object()


def _semgrep_or_skip() -> None:
    if shutil.which("semgrep") is None:
        pytest.skip("semgrep not installed in PATH; skipping red_lint test")


# ─── Contract A: _verify_red_lint_rules ─────────────────────────────────────


def test_verify_red_lint_rules_passes_on_clean_red_tests(tmp_path):
    """Clean RED test (no hardcoded line numbers in where=...) → status=ok."""
    _semgrep_or_skip()
    from bytedigger_engine.workflows.phase_5_implement import _verify_red_lint_rules

    test_file = tmp_path / "tests" / "test_clean.py"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text(
        '''def test_clean():
    err = "boundary_error where=foo.py schema=ok"
    assert "where=foo.py" in err
'''
    )
    prev = _make_prev(["tests/test_clean.py"])
    result = _verify_red_lint_rules(_make_ctx(tmp_path), prev)
    assert result.status == "ok", f"expected ok, got {result.status} ({result.error_code})"


def test_verify_red_lint_rules_fails_on_F1_violation(tmp_path):
    """F1 hardcoded line number in where=foo.py:42 → status=error."""
    _semgrep_or_skip()
    from bytedigger_engine.workflows.phase_5_implement import _verify_red_lint_rules

    test_file = tmp_path / "tests" / "test_bad.py"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text(
        '''def test_bad():
    err = "boundary_error where=foo.py:42 schema=X"
    assert "where=foo.py:42" in err
'''
    )
    prev = _make_prev(["tests/test_bad.py"])
    result = _verify_red_lint_rules(_make_ctx(tmp_path), prev)
    assert result.status == "error"
    haystack = " ".join(
        str(x) for x in [result.error or "", result.error_code or "", result.data or ""]
    ).lower()
    assert (
        "f1" in haystack
        or "hardcoded-line-number" in haystack
        or "hardcoded line number" in haystack
        or "where=" in haystack
    ), f"error payload should mention F1/hardcoded-line-number, got: {haystack!r}"


def test_verify_red_lint_rules_passes_with_F3_warning(tmp_path):
    """F3 (schema=Foo.bar) is WARNING/advisory → status=ok despite finding."""
    _semgrep_or_skip()
    from bytedigger_engine.workflows.phase_5_implement import _verify_red_lint_rules

    test_file = tmp_path / "tests" / "test_f3.py"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text(
        '''def test_f3_only():
    payload = "schema=NonExistentType.field"
    assert "schema=NonExistentType.field" in payload
'''
    )
    prev = _make_prev(["tests/test_f3.py"])
    result = _verify_red_lint_rules(_make_ctx(tmp_path), prev)
    assert result.status == "ok", (
        f"F3 is advisory; expected ok, got {result.status} ({result.error_code})"
    )


def test_verify_red_lint_rules_missing_red_test_paths_returns_error(tmp_path):
    """prev.data missing 'red_test_paths' key → status=error."""
    from bytedigger_engine.workflows.phase_5_implement import _verify_red_lint_rules

    prev = _make_prev(_SENTINEL)  # key absent entirely
    result = _verify_red_lint_rules(_make_ctx(tmp_path), prev)
    assert result.status == "error"


def test_verify_red_lint_rules_empty_red_test_paths_returns_error(tmp_path):
    """red_test_paths=[] → status=error."""
    from bytedigger_engine.workflows.phase_5_implement import _verify_red_lint_rules

    prev = _make_prev([])
    result = _verify_red_lint_rules(_make_ctx(tmp_path), prev)
    assert result.status == "error"


# ─── Contract B: _build_red_prompt grounding rules ──────────────────────────


def test_build_red_prompt_includes_three_grounding_rules(tmp_path):
    """_build_red_prompt output must include a GROUNDING RULES section
    with >=3 entries plus F1 and F3 references.
    """
    from bytedigger_engine.workflows.phase_5_implement import _build_red_prompt

    # Provide a minimal scratchpad with spec/arch files so prompt builder
    # finds them (mirrors test patterns elsewhere in the suite).
    scratchpad = tmp_path / "scratchpad"
    scratchpad.mkdir(parents=True, exist_ok=True)
    (scratchpad / "spec.md").write_text("# Spec\n\nminimal\n")
    (scratchpad / "architecture.md").write_text("# Arch\n\nminimal\n")

    ctx = WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={"git_cwd": str(tmp_path), "scratchpad_dir": str(scratchpad)},
        question="implement widget",
        session_id="test-session",
        persona="hal",
        framework=None,
        domain=None,
    )
    result = _build_red_prompt(ctx, None)
    assert result.status == "ok", f"prompt build failed: {result.error}"
    prompt = result.data["prompt"]

    # 1. heading containing "GROUNDING RULES" (case-insensitive)
    assert re.search(r"grounding\s+rules", prompt, re.IGNORECASE), (
        "prompt missing GROUNDING RULES heading"
    )

    # 2. at least 3 numbered or bulleted rule entries
    rule_lines = re.findall(r"(?m)^\s*(?:[1-9]\.|[-*])\s+\S", prompt)
    assert len(rule_lines) >= 3, (
        f"prompt should list >=3 numbered/bulleted rule entries, found {len(rule_lines)}"
    )

    # 3. reference to F1 (or its human alias)
    assert re.search(
        r"\bF1\b|hardcoded\s*line\s*number|line\s+number", prompt, re.IGNORECASE
    ), "prompt missing F1 / hardcoded-line-number reference"

    # 4. reference to F3 (or its human alias)
    assert re.search(
        r"\bF3\b|schema\s*=|type\s+referent", prompt, re.IGNORECASE
    ), "prompt missing F3 / schema-referent reference"


# ─── Reviewer-flagged bug fixes (C1, I1, I2) ────────────────────────────────


def test_verify_red_lint_rules_fails_loud_when_semgrep_missing(tmp_path, monkeypatch):
    """C1 (updated 112CB15B Slice 2): which→None → status=error, E_RED_LINT_SEMGREP_MISSING, recoverable=False.

    Contract updated: semgrep is build_critical; absence must fail loud
    (status=error, data=None) instead of silently skipping (status=ok).
    The prior prev.data-merge behaviour is superseded by the fail-loud contract.
    """
    from bytedigger_engine.workflows.phase_5_implement import _verify_red_lint_rules

    monkeypatch.setattr(shutil, "which", lambda name: None)
    prev = StepResult(
        status="ok",
        data={
            "red_test_paths": ["foo.py"],
            "red_log_path": "/tmp/x",
            "spec_path": "/tmp/y",
            "cycle": 1,
        },
        duration_ms=0,
        step_name="commit_red_tests",
    )
    result = _verify_red_lint_rules(_make_ctx(tmp_path), prev)
    assert result.status == "error", (
        f"expected status=error when semgrep missing, got {result.status!r}"
    )
    assert result.error_code == "E_RED_LINT_SEMGREP_MISSING", (
        f"expected E_RED_LINT_SEMGREP_MISSING, got {result.error_code!r}"
    )
    assert result.recoverable is False, (
        f"expected recoverable=False, got {result.recoverable!r}"
    )


def test_verify_red_lint_rules_rejects_path_outside_git_cwd(tmp_path):
    """I1: path traversal — absolute path or `..` segments must be rejected
    BEFORE invoking semgrep (so test runs regardless of semgrep availability).
    """
    from bytedigger_engine.workflows.phase_5_implement import _verify_red_lint_rules

    # safe baseline file inside git_cwd (not used, but realistic prev.data)
    (tmp_path / "safe.py").write_text("# ok\n")

    # Sub-case 1: absolute path escape
    prev_abs = StepResult(
        status="ok",
        data={
            "red_test_paths": ["/etc/passwd"],
            "red_log_path": "/tmp/x",
            "spec_path": "/tmp/y",
            "cycle": 1,
        },
        duration_ms=0,
        step_name="commit_red_tests",
    )
    result = _verify_red_lint_rules(_make_ctx(tmp_path), prev_abs)
    assert result.status == "error", f"expected error for absolute path escape, got {result.status}"
    assert result.error_code == "E_RED_LINT_PATH_ESCAPE"

    # Sub-case 2: traversal escape
    prev_trav = StepResult(
        status="ok",
        data={
            "red_test_paths": ["../../../../etc/passwd"],
            "red_log_path": "/tmp/x",
            "spec_path": "/tmp/y",
            "cycle": 1,
        },
        duration_ms=0,
        step_name="commit_red_tests",
    )
    result = _verify_red_lint_rules(_make_ctx(tmp_path), prev_trav)
    assert result.status == "error", f"expected error for traversal escape, got {result.status}"
    assert result.error_code == "E_RED_LINT_PATH_ESCAPE"


def test_verify_red_lint_rules_handles_semgrep_internal_error(tmp_path, monkeypatch):
    """I2: semgrep internal errors (broken rule, version mismatch) emit
    {"errors":[...],"results":[]} with non-zero exit. Treat as skipped, not
    silent OK; preserve prev.data for downstream steps.
    """
    import subprocess as _subproc

    from bytedigger_engine.workflows.phase_5_implement import _verify_red_lint_rules

    _semgrep_or_skip()  # need rules.yml resolution to proceed past skip branches

    test_file = tmp_path / "tests" / "test_x.py"
    test_file.parent.mkdir(parents=True, exist_ok=True)
    test_file.write_text("def test_x():\n    assert True\n")

    class _StubProc:
        stdout = '{"errors":[{"type":"FatalError","message":"bad rule"}],"results":[]}'
        stderr = ""
        returncode = 2

    def _fake_run(*args, **kwargs):
        return _StubProc()

    # Patch subprocess.run as imported into phase_5_implement module
    monkeypatch.setattr("bytedigger_engine.workflows.phase_5_implement.subprocess.run", _fake_run)

    prev = StepResult(
        status="ok",
        data={
            "red_test_paths": ["tests/test_x.py"],
            "red_log_path": "/tmp/x",
            "spec_path": "/tmp/y",
            "cycle": 1,
        },
        duration_ms=0,
        step_name="commit_red_tests",
    )
    result = _verify_red_lint_rules(_make_ctx(tmp_path), prev)
    assert result.status == "ok", f"expected ok (skipped), got {result.status} ({result.error_code})"
    assert result.data["skipped"] == "semgrep_internal_error"
    assert result.data["red_log_path"] == "/tmp/x"
    assert result.data["spec_path"] == "/tmp/y"
    assert result.data["cycle"] == 1
