"""Tests for phase_0_6_artifact_detect workflow — Stage 1 of agreement 27843297."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

HERE = Path(__file__).parent

from bytedigger_engine.contracts import WorkflowContext  # noqa: E402
from bytedigger_engine.engine import WorkflowEngine  # noqa: E402
from bytedigger_engine.workflows.phase_0_6_artifact_detect import phase_0_6_artifact_detect_workflow  # noqa: E402
from bytedigger_engine import workflows  # noqa: E402


# ─── helpers ──────────────────────────────────────────────────────────────────


def _bun_calls(mock_run):
    """Filter mock subprocess.run calls down to bun-invocation calls only.

    WorkflowEngine itself calls subprocess.run(['git', ...]) for change-tracking
    (engine.py:427). Module-level subprocess patching catches those too. Tests
    that probe phase_0_6's bun invocation must filter to isolate the signal.
    """
    return [
        c for c in mock_run.call_args_list
        if c.args and len(c.args[0]) > 0 and c.args[0][0] == "bun"
    ]


def make_ctx(tmp_path: Path, changed_files: list[str] | None = None, **org_extra) -> WorkflowContext:
    org: dict = {"scratchpad_dir": str(tmp_path), **org_extra}
    if changed_files is not None:
        org["changed_files"] = changed_files
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question="detect artifact type",
        session_id="test-session",
        persona="hal",
        framework=None,
        domain=None,
    )


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


# ─── AC1: workflow definition shape ──────────────────────────────────────────


def test_workflow_definition_shape():
    """AC1: factory returns WorkflowDefinition with correct name and single step."""
    wf = phase_0_6_artifact_detect_workflow()
    assert wf.name == "phase_0_6_artifact_detect"
    assert [s.name for s in wf.steps] == ["detect_artifact"]


# ─── AC2: detects dockerfile ──────────────────────────────────────────────────


def test_detects_dockerfile(tmp_path):
    """AC2: subprocess returns dockerfile detection → data reflects dockerfile."""
    stdout = json.dumps({
        "artifact_type": "dockerfile",
        "detection_method": "filename",
        "detected_files": ["Dockerfile"],
    })
    eng = WorkflowEngine()
    eng.register("p06", phase_0_6_artifact_detect_workflow())
    ctx = make_ctx(tmp_path, changed_files=["Dockerfile"])

    with patch("bytedigger_engine.workflows.phase_0_6_artifact_detect.subprocess.run", return_value=_stub_proc(stdout=stdout)):
        result, _ = eng.execute("p06", ctx)

    assert result.status == "ok"
    assert result.data["artifact_type"] == "dockerfile"
    assert result.data["detection_method"] == "filename"


# ─── AC3: detects terraform ───────────────────────────────────────────────────


def test_detects_terraform(tmp_path):
    """AC3: subprocess returns terraform detection → data reflects terraform."""
    stdout = json.dumps({
        "artifact_type": "terraform",
        "detection_method": "extension",
        "detected_files": ["infra/main.tf"],
    })
    eng = WorkflowEngine()
    eng.register("p06", phase_0_6_artifact_detect_workflow())
    ctx = make_ctx(tmp_path, changed_files=["infra/main.tf"])

    with patch("bytedigger_engine.workflows.phase_0_6_artifact_detect.subprocess.run", return_value=_stub_proc(stdout=stdout)):
        result, _ = eng.execute("p06", ctx)

    assert result.status == "ok"
    assert result.data["artifact_type"] == "terraform"
    assert result.data["detection_method"] == "extension"


# ─── AC4: detects github-actions ─────────────────────────────────────────────


def test_detects_github_actions(tmp_path):
    """AC4: subprocess returns github-actions detection → data reflects github-actions."""
    stdout = json.dumps({
        "artifact_type": "github-actions",
        "detection_method": "filename",
        "detected_files": [".github/workflows/ci.yml"],
    })
    eng = WorkflowEngine()
    eng.register("p06", phase_0_6_artifact_detect_workflow())
    ctx = make_ctx(tmp_path, changed_files=[".github/workflows/ci.yml"])

    with patch("bytedigger_engine.workflows.phase_0_6_artifact_detect.subprocess.run", return_value=_stub_proc(stdout=stdout)):
        result, _ = eng.execute("p06", ctx)

    assert result.status == "ok"
    assert result.data["artifact_type"] == "github-actions"


# ─── AC5: non-devops files yield None ────────────────────────────────────────


def test_non_devops_files_yield_none(tmp_path):
    """AC5: non-DevOps changed_files → artifact_type is None, no exception."""
    stdout = json.dumps({
        "artifact_type": None,
        "detection_method": None,
        "detected_files": [],
    })
    eng = WorkflowEngine()
    eng.register("p06", phase_0_6_artifact_detect_workflow())
    ctx = make_ctx(tmp_path, changed_files=["src/main.py", "README.md"])

    with patch("bytedigger_engine.workflows.phase_0_6_artifact_detect.subprocess.run", return_value=_stub_proc(stdout=stdout)):
        result, _ = eng.execute("p06", ctx)

    assert result.status == "ok"
    assert result.data["artifact_type"] is None


# ─── AC6: empty changed_files skips subprocess ───────────────────────────────


def test_empty_changed_files_skips_subprocess(tmp_path):
    """AC6a: empty changed_files list → subprocess NOT invoked, result ok with None."""
    eng = WorkflowEngine()
    eng.register("p06", phase_0_6_artifact_detect_workflow())
    ctx = make_ctx(tmp_path, changed_files=[])

    with patch("bytedigger_engine.workflows.phase_0_6_artifact_detect.subprocess.run") as mock_run:
        result, _ = eng.execute("p06", ctx)

    assert _bun_calls(mock_run) == [], (
        f"expected no bun invocations when changed_files=[], got {_bun_calls(mock_run)}"
    )
    assert result.status == "ok"
    assert result.data["artifact_type"] is None


def test_absent_changed_files_skips_subprocess(tmp_path):
    """AC6b: key absent from org_config → subprocess NOT invoked, result ok with None."""
    eng = WorkflowEngine()
    eng.register("p06", phase_0_6_artifact_detect_workflow())
    # make_ctx without changed_files= omits the key entirely
    ctx = make_ctx(tmp_path)

    with patch("bytedigger_engine.workflows.phase_0_6_artifact_detect.subprocess.run") as mock_run:
        result, _ = eng.execute("p06", ctx)

    assert _bun_calls(mock_run) == [], (
        f"expected no bun invocations when changed_files absent, got {_bun_calls(mock_run)}"
    )
    assert result.status == "ok"
    assert result.data["artifact_type"] is None


# ─── AC7: FileNotFoundError → non-fatal ──────────────────────────────────────


def test_filenotfound_returns_none_nonfatal(tmp_path):
    """AC7: bun missing (FileNotFoundError) → status=ok, artifact_type=None, error set."""
    eng = WorkflowEngine()
    eng.register("p06", phase_0_6_artifact_detect_workflow())
    ctx = make_ctx(tmp_path, changed_files=["Dockerfile"])

    with patch(
        "bytedigger_engine.workflows.phase_0_6_artifact_detect.subprocess.run",
        side_effect=FileNotFoundError("bun not found"),
    ):
        result, _ = eng.execute("p06", ctx)

    assert result.status == "ok"
    assert result.data["artifact_type"] is None
    error_str = (result.data.get("error") or "").lower()
    assert "filenotfounderror" in error_str or "not found" in error_str or "bun" in error_str, (
        f"expected error indicator in data['error'], got {result.data.get('error')!r}"
    )


# ─── AC8: TimeoutExpired → non-fatal ─────────────────────────────────────────


def test_timeout_returns_none_nonfatal(tmp_path):
    """AC8: subprocess timeout → status=ok, artifact_type=None, error contains 'timeout'."""
    eng = WorkflowEngine()
    eng.register("p06", phase_0_6_artifact_detect_workflow())
    ctx = make_ctx(tmp_path, changed_files=["Dockerfile"])

    with patch(
        "bytedigger_engine.workflows.phase_0_6_artifact_detect.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd=["bun"], timeout=30),
    ):
        result, _ = eng.execute("p06", ctx)

    assert result.status == "ok"
    assert result.data["artifact_type"] is None
    error_str = (result.data.get("error") or "").lower()
    assert "timeout" in error_str, (
        f"expected 'timeout' in data['error'], got {result.data.get('error')!r}"
    )


# ─── AC9: rc != 0 → non-fatal ────────────────────────────────────────────────


def test_subprocess_nonzero_returns_none_nonfatal(tmp_path):
    """AC9: subprocess rc=1 with stderr → status=ok, artifact_type=None, error reflects stderr."""
    eng = WorkflowEngine()
    eng.register("p06", phase_0_6_artifact_detect_workflow())
    ctx = make_ctx(tmp_path, changed_files=["Dockerfile"])

    with patch(
        "bytedigger_engine.workflows.phase_0_6_artifact_detect.subprocess.run",
        return_value=_stub_proc(returncode=1, stderr="internal shim error"),
    ):
        result, _ = eng.execute("p06", ctx)

    assert result.status == "ok"
    assert result.data["artifact_type"] is None
    error_val = result.data.get("error") or ""
    assert error_val, "expected non-empty data['error'] on rc=1"


# ─── AC10: malformed JSON → non-fatal ────────────────────────────────────────


def test_malformed_json_returns_none_nonfatal(tmp_path):
    """AC10: rc=0 but stdout is not valid JSON → status=ok, artifact_type=None, no crash."""
    eng = WorkflowEngine()
    eng.register("p06", phase_0_6_artifact_detect_workflow())
    ctx = make_ctx(tmp_path, changed_files=["Dockerfile"])

    with patch(
        "bytedigger_engine.workflows.phase_0_6_artifact_detect.subprocess.run",
        return_value=_stub_proc(returncode=0, stdout="not-json{{{"),
    ):
        result, _ = eng.execute("p06", ctx)

    assert result.status == "ok"
    assert result.data["artifact_type"] is None


# ─── AC11: registry membership ───────────────────────────────────────────────


def test_registry_includes_phase_0_6():
    """AC11: after register_all, engine.registered() includes phase_0_6_artifact_detect."""
    eng = WorkflowEngine()
    workflows.register_all(eng)
    assert "phase_0_6_artifact_detect" in eng.registered()


# ─── AC12: subprocess invocation shape ───────────────────────────────────────


def test_subprocess_invocation_shape(tmp_path):
    """AC12: subprocess.run called with bun, devops-detect.ts path, --files CSV, --output json."""
    stdout = json.dumps({
        "artifact_type": "dockerfile",
        "detection_method": "filename",
        "detected_files": ["Dockerfile"],
    })
    eng = WorkflowEngine()
    eng.register("p06", phase_0_6_artifact_detect_workflow())
    ctx = make_ctx(tmp_path, changed_files=["Dockerfile"])

    with patch("bytedigger_engine.workflows.phase_0_6_artifact_detect.subprocess.run", return_value=_stub_proc(stdout=stdout)) as mock_run:
        eng.execute("p06", ctx)

    bun_calls = _bun_calls(mock_run)
    assert len(bun_calls) == 1, f"expected exactly 1 bun invocation, got {len(bun_calls)}"
    cmd = list(bun_calls[0].args[0])

    # cmd[0] must be "bun"
    assert cmd[0] == "bun", f"expected cmd[0]='bun', got {cmd[0]!r}"

    # cmd[1] must be the devops-detect.ts shim path (ends with SYSTEM/cli/build/devops-detect.ts)
    shim_path = cmd[1]
    assert shim_path.endswith("SYSTEM/cli/build/devops-detect.ts"), (
        f"expected cmd[1] to end with 'SYSTEM/cli/build/devops-detect.ts', got {shim_path!r}"
    )

    cmd_str = " ".join(cmd)

    # must include --files with the CSV of changed_files
    assert "--files" in cmd_str, f"expected '--files' in cmd argv, got {cmd!r}"
    assert "Dockerfile" in cmd_str, f"expected 'Dockerfile' in cmd argv, got {cmd!r}"

    # must include --output json
    assert "--output" in cmd_str, f"expected '--output' in cmd argv, got {cmd!r}"
    assert "json" in cmd_str, f"expected 'json' in cmd argv, got {cmd!r}"
