"""Tests for phase_5_devops_scan workflow — Stage 2 of agreement 27843297."""
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
from bytedigger_engine.workflows.phase_5_devops_scan import phase_5_devops_scan_workflow  # noqa: E402
from bytedigger_engine import workflows  # noqa: E402


# ─── helpers ──────────────────────────────────────────────────────────────────


def _bun_calls(mock_run):
    """Filter mock subprocess.run calls down to bun-invocation calls only.

    WorkflowEngine itself calls subprocess.run(['git', ...]) for change-tracking
    (engine.py:427). Module-level subprocess patching catches those too. Tests
    that probe phase_5_devops_scan's bun invocation must filter to isolate the signal.
    """
    return [
        c for c in mock_run.call_args_list
        if c.args and len(c.args[0]) > 0 and c.args[0][0] == "bun"
    ]


def make_ctx(
    tmp_path: Path,
    changed_files: list[str] | None = None,
    artifact_type: str | None = "__unset__",
    **org_extra,
) -> WorkflowContext:
    org: dict = {"scratchpad_dir": str(tmp_path), **org_extra}
    if changed_files is not None:
        org["changed_files"] = changed_files
    if artifact_type != "__unset__":
        org["artifact_type"] = artifact_type
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=org,
        question="scan devops artifact",
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
    """AC1: factory returns WorkflowDefinition(name='phase_5_devops_scan', steps=[scan_artifact])."""
    wf = phase_5_devops_scan_workflow()
    assert wf.name == "phase_5_devops_scan"
    assert [s.name for s in wf.steps] == ["scan_artifact"]


# ─── AC2: silent-skip when artifact_type is None ─────────────────────────────


def test_skip_when_artifact_type_none(tmp_path):
    """AC2: artifact_type=None → subprocess NOT invoked; status=ok, skipped_reason='no_artifact_type'."""
    eng = WorkflowEngine()
    eng.register("p5s", phase_5_devops_scan_workflow())
    ctx = make_ctx(tmp_path, changed_files=["Dockerfile"], artifact_type=None)

    with patch("bytedigger_engine.workflows.phase_5_devops_scan.subprocess.run") as mock_run:
        result, _ = eng.execute("p5s", ctx)

    assert _bun_calls(mock_run) == [], (
        f"expected no bun invocations when artifact_type=None, got {_bun_calls(mock_run)}"
    )
    assert result.status == "ok"
    assert result.data["skipped_reason"] == "no_artifact_type"


# ─── AC3: silent-skip when artifact_type key absent ──────────────────────────


def test_skip_when_artifact_type_absent(tmp_path):
    """AC3: artifact_type key absent → subprocess NOT invoked; status=ok, skipped_reason='no_artifact_type'."""
    eng = WorkflowEngine()
    eng.register("p5s", phase_5_devops_scan_workflow())
    # artifact_type="__unset__" default means key is NOT inserted into org_config
    ctx = make_ctx(tmp_path, changed_files=["Dockerfile"])

    with patch("bytedigger_engine.workflows.phase_5_devops_scan.subprocess.run") as mock_run:
        result, _ = eng.execute("p5s", ctx)

    assert _bun_calls(mock_run) == [], (
        f"expected no bun invocations when artifact_type absent, got {_bun_calls(mock_run)}"
    )
    assert result.status == "ok"
    assert result.data["skipped_reason"] == "no_artifact_type"


# ─── AC4: silent-skip when changed_files is empty list ───────────────────────


def test_skip_when_changed_files_empty(tmp_path):
    """AC4: changed_files=[] → subprocess NOT invoked; status=ok, skipped_reason='no_files'."""
    eng = WorkflowEngine()
    eng.register("p5s", phase_5_devops_scan_workflow())
    ctx = make_ctx(tmp_path, changed_files=[], artifact_type="dockerfile")

    with patch("bytedigger_engine.workflows.phase_5_devops_scan.subprocess.run") as mock_run:
        result, _ = eng.execute("p5s", ctx)

    assert _bun_calls(mock_run) == [], (
        f"expected no bun invocations when changed_files=[], got {_bun_calls(mock_run)}"
    )
    assert result.status == "ok"
    assert result.data["skipped_reason"] == "no_files"


# ─── AC5: silent-skip when changed_files key absent ──────────────────────────


def test_skip_when_changed_files_absent(tmp_path):
    """AC5: changed_files key absent → subprocess NOT invoked; status=ok, skipped_reason='no_files'."""
    eng = WorkflowEngine()
    eng.register("p5s", phase_5_devops_scan_workflow())
    # pass no changed_files kwarg → key absent from org_config
    ctx = make_ctx(tmp_path, artifact_type="dockerfile")

    with patch("bytedigger_engine.workflows.phase_5_devops_scan.subprocess.run") as mock_run:
        result, _ = eng.execute("p5s", ctx)

    assert _bun_calls(mock_run) == [], (
        f"expected no bun invocations when changed_files absent, got {_bun_calls(mock_run)}"
    )
    assert result.status == "ok"
    assert result.data["skipped_reason"] == "no_files"


# ─── AC6: happy path (Dockerfile) ────────────────────────────────────────────


def test_happy_path_dockerfile(tmp_path):
    """AC6: Dockerfile happy path → subprocess invoked; data['passed'] and data['scanners_run'] propagated."""
    shim_stdout = json.dumps({
        "passed": True,
        "issues": [],
        "scanners_run": ["hadolint", "credentials"],
        "scanners_missing": ["trivy", "gitleaks"],
        "summary": "No issues found.",
        "stats": {"critical": 0, "high": 0, "medium": 0, "low": 0, "total": 0},
        "skipped_reason": None,
    })
    eng = WorkflowEngine()
    eng.register("p5s", phase_5_devops_scan_workflow())
    ctx = make_ctx(tmp_path, changed_files=["Dockerfile"], artifact_type="dockerfile")

    with patch("bytedigger_engine.workflows.phase_5_devops_scan.subprocess.run", return_value=_stub_proc(stdout=shim_stdout)) as mock_run:
        result, _ = eng.execute("p5s", ctx)

    bun_calls = _bun_calls(mock_run)
    assert len(bun_calls) == 1, f"expected exactly 1 bun invocation, got {len(bun_calls)}"
    assert result.status == "ok"
    assert result.data["passed"] is True
    assert result.data["scanners_run"] == ["hadolint", "credentials"]


# ─── AC7: happy path with HIGH issue ─────────────────────────────────────────


def test_happy_path_high_issue(tmp_path, monkeypatch):
    """AC7 (flipped, GH342B §4): gate ON (default) + HIGH issue → status='error',
    error_code='E_DEVOPS_SCAN_BLOCKED' (HIGH is in default fail_on_severities,
    no allowlist match). See 2026-07-05_GH342B_devops_scan_gate_spec.md §2 table."""
    monkeypatch.delenv("HAL_DEVOPS_SCAN_GATE", raising=False)
    monkeypatch.delenv("HAL_DEVOPS_SCAN_CONFIG", raising=False)
    monkeypatch.delenv("HAL_DEVOPS_SCAN_ALLOWLIST", raising=False)
    shim_stdout = json.dumps({
        "passed": False,
        "issues": [{"severity": "HIGH", "scanner": "hadolint", "file": "Dockerfile", "description": "DL3007 Using latest is bad"}],
        "scanners_run": ["hadolint"],
        "scanners_missing": [],
        "summary": "1 HIGH issue found.",
        "stats": {"critical": 0, "high": 1, "medium": 0, "low": 0, "total": 1},
        "skipped_reason": None,
    })
    eng = WorkflowEngine()
    eng.register("p5s", phase_5_devops_scan_workflow())
    ctx = make_ctx(tmp_path, changed_files=["Dockerfile"], artifact_type="dockerfile")

    with patch("bytedigger_engine.workflows.phase_5_devops_scan.subprocess.run", return_value=_stub_proc(stdout=shim_stdout)):
        result, _ = eng.execute("p5s", ctx)

    assert result.status == "error"
    assert result.error_code == "E_DEVOPS_SCAN_BLOCKED"
    assert result.data["stats"]["high"] == 1


# ─── AC8: FileNotFoundError on bun → fails closed (gate ON, GH342B) ─────────


def test_filenotfound_bun_fails_closed(tmp_path, monkeypatch):
    """AC8 (flipped, GH342B §4): gate ON + bun missing → status='error',
    error_code='E_DEVOPS_SCAN_UNAVAILABLE' (infra failure, fail-closed per #221)."""
    monkeypatch.delenv("HAL_DEVOPS_SCAN_GATE", raising=False)
    eng = WorkflowEngine()
    eng.register("p5s", phase_5_devops_scan_workflow())
    ctx = make_ctx(tmp_path, changed_files=["Dockerfile"], artifact_type="dockerfile")

    with patch(
        "bytedigger_engine.workflows.phase_5_devops_scan.subprocess.run",
        side_effect=FileNotFoundError("bun not found"),
    ):
        result, _ = eng.execute("p5s", ctx)

    assert result.status == "error"
    assert result.error_code == "E_DEVOPS_SCAN_UNAVAILABLE"
    error_str = (result.data.get("error") or "").lower()
    assert "bun" in error_str or "not found" in error_str, (
        f"expected 'bun' or 'not found' in data['error'], got {result.data.get('error')!r}"
    )


# ─── AC9: TimeoutExpired → fails closed (gate ON, GH342B) ────────────────────


def test_timeout_fails_closed(tmp_path, monkeypatch):
    """AC9 (flipped, GH342B §4): gate ON + subprocess timeout → status='error',
    error_code='E_DEVOPS_SCAN_UNAVAILABLE'."""
    monkeypatch.delenv("HAL_DEVOPS_SCAN_GATE", raising=False)
    eng = WorkflowEngine()
    eng.register("p5s", phase_5_devops_scan_workflow())
    ctx = make_ctx(tmp_path, changed_files=["Dockerfile"], artifact_type="dockerfile")

    with patch(
        "bytedigger_engine.workflows.phase_5_devops_scan.subprocess.run",
        side_effect=subprocess.TimeoutExpired(cmd=["bun"], timeout=60),
    ):
        result, _ = eng.execute("p5s", ctx)

    assert result.status == "error"
    assert result.error_code == "E_DEVOPS_SCAN_UNAVAILABLE"
    error_str = (result.data.get("error") or "").lower()
    assert "timeout" in error_str, (
        f"expected 'timeout' in data['error'], got {result.data.get('error')!r}"
    )


# ─── AC10: rc != 0 with stderr → fails closed (gate ON, GH342B) ──────────────


def test_subprocess_nonzero_fails_closed(tmp_path, monkeypatch):
    """AC10 (flipped, GH342B §4): gate ON + subprocess rc=1 → status='error',
    error_code='E_DEVOPS_SCAN_UNAVAILABLE'."""
    monkeypatch.delenv("HAL_DEVOPS_SCAN_GATE", raising=False)
    eng = WorkflowEngine()
    eng.register("p5s", phase_5_devops_scan_workflow())
    ctx = make_ctx(tmp_path, changed_files=["Dockerfile"], artifact_type="dockerfile")

    with patch(
        "bytedigger_engine.workflows.phase_5_devops_scan.subprocess.run",
        return_value=_stub_proc(returncode=1, stderr="internal shim error"),
    ):
        result, _ = eng.execute("p5s", ctx)

    assert result.status == "error"
    assert result.error_code == "E_DEVOPS_SCAN_UNAVAILABLE"
    error_val = result.data.get("error") or ""
    assert error_val, "expected non-empty data['error'] on rc=1"


# ─── AC11: malformed JSON → fails closed (gate ON, GH342B) ───────────────────


def test_malformed_json_fails_closed(tmp_path, monkeypatch):
    """AC11 (flipped, GH342B §4): gate ON + stdout not valid JSON → status='error',
    error_code='E_DEVOPS_SCAN_UNAVAILABLE', no crash."""
    monkeypatch.delenv("HAL_DEVOPS_SCAN_GATE", raising=False)
    eng = WorkflowEngine()
    eng.register("p5s", phase_5_devops_scan_workflow())
    ctx = make_ctx(tmp_path, changed_files=["Dockerfile"], artifact_type="dockerfile")

    with patch(
        "bytedigger_engine.workflows.phase_5_devops_scan.subprocess.run",
        return_value=_stub_proc(returncode=0, stdout="not-json{{{"),
    ):
        result, _ = eng.execute("p5s", ctx)

    assert result.status == "error"
    assert result.error_code == "E_DEVOPS_SCAN_UNAVAILABLE"


# ─── AC12: registry membership ───────────────────────────────────────────────


def test_registry_includes_phase_5_devops_scan():
    """AC12: after register_all(engine), engine.registered() includes 'phase_5_devops_scan'."""
    eng = WorkflowEngine()
    workflows.register_all(eng)
    assert "phase_5_devops_scan" in eng.registered()


# ─── AC13: subprocess argv shape ─────────────────────────────────────────────


def test_subprocess_argv_shape(tmp_path):
    """AC13: cmd[0]='bun'; cmd[1] ends with 'SYSTEM/cli/build/devops-scan.ts'; --files, --artifact-type dockerfile, --output json all present."""
    shim_stdout = json.dumps({
        "passed": True,
        "issues": [],
        "scanners_run": ["hadolint"],
        "scanners_missing": [],
        "summary": "ok",
        "stats": {"critical": 0, "high": 0, "medium": 0, "low": 0, "total": 0},
        "skipped_reason": None,
    })
    eng = WorkflowEngine()
    eng.register("p5s", phase_5_devops_scan_workflow())
    ctx = make_ctx(tmp_path, changed_files=["Dockerfile"], artifact_type="dockerfile")

    with patch("bytedigger_engine.workflows.phase_5_devops_scan.subprocess.run", return_value=_stub_proc(stdout=shim_stdout)) as mock_run:
        eng.execute("p5s", ctx)

    bun_calls = _bun_calls(mock_run)
    assert len(bun_calls) == 1, f"expected exactly 1 bun invocation, got {len(bun_calls)}"
    cmd = list(bun_calls[0].args[0])

    assert cmd[0] == "bun", f"expected cmd[0]='bun', got {cmd[0]!r}"
    assert cmd[1].endswith("SYSTEM/cli/build/devops-scan.ts"), (
        f"expected cmd[1] to end with 'SYSTEM/cli/build/devops-scan.ts', got {cmd[1]!r}"
    )

    cmd_str = " ".join(cmd)
    assert "--files" in cmd_str, f"expected '--files' in cmd argv, got {cmd!r}"
    assert "Dockerfile" in cmd_str, f"expected 'Dockerfile' in cmd argv (CSV of changed_files), got {cmd!r}"
    assert "--artifact-type" in cmd_str, f"expected '--artifact-type' in cmd argv, got {cmd!r}"
    assert "dockerfile" in cmd_str, f"expected 'dockerfile' in cmd argv, got {cmd!r}"
    assert "--output" in cmd_str, f"expected '--output' in cmd argv, got {cmd!r}"
    assert "json" in cmd_str, f"expected 'json' in cmd argv, got {cmd!r}"


# ═══════════════════════════════════════════════════════════════════════════
# GH #342 slice B — severity-gate flip (fail-open → fail-closed)
# Spec: SHARED/memory/Decisions/2026-07-05_GH342B_devops_scan_gate_spec.md §3
# New symbols imported inside test bodies (D1CF5FDF — keep file collectable).
# ═══════════════════════════════════════════════════════════════════════════


# ─── AC1: _gate_enabled() env toggle ─────────────────────────────────────────


def test_ac1_gate_enabled_default_true(monkeypatch):
    """AC1: HAL_DEVOPS_SCAN_GATE unset → _gate_enabled() is True (default ON)."""
    monkeypatch.delenv("HAL_DEVOPS_SCAN_GATE", raising=False)
    from bytedigger_engine.workflows.phase_5_devops_scan import _gate_enabled
    assert _gate_enabled() is True


def test_ac1_gate_enabled_false_when_env_zero(monkeypatch):
    """AC1: HAL_DEVOPS_SCAN_GATE=0 → _gate_enabled() is False (kill-switch)."""
    monkeypatch.setenv("HAL_DEVOPS_SCAN_GATE", "0")
    from bytedigger_engine.workflows.phase_5_devops_scan import _gate_enabled
    assert _gate_enabled() is False


# ─── AC2/AC3: _load_fail_severities ───────────────────────────────────────────


def test_ac2_load_fail_severities_reads_config_fixture(tmp_path, monkeypatch):
    """AC2: HAL_DEVOPS_SCAN_CONFIG points at {"fail_on_severities":["HIGH"]} → ["HIGH"]."""
    cfg_path = tmp_path / "devops-scan-config.json"
    cfg_path.write_text(json.dumps({"fail_on_severities": ["HIGH"]}))
    monkeypatch.setenv("HAL_DEVOPS_SCAN_CONFIG", str(cfg_path))
    from bytedigger_engine.workflows.phase_5_devops_scan import _load_fail_severities
    assert _load_fail_severities(tmp_path) == ["HIGH"]


def test_ac3_load_fail_severities_default_on_missing_path(tmp_path, monkeypatch):
    """AC3: no env override, hal_dir has no config file → default ["CRITICAL","HIGH"]."""
    monkeypatch.delenv("HAL_DEVOPS_SCAN_CONFIG", raising=False)
    from bytedigger_engine.workflows.phase_5_devops_scan import _load_fail_severities
    assert _load_fail_severities(tmp_path) == ["CRITICAL", "HIGH"]


# ─── AC4/AC5: _load_allowlist ─────────────────────────────────────────────────


def test_ac4_load_allowlist_parses_valid_line(tmp_path):
    """AC4: parses "foo.tf :: ABCD1234 :: kill-by:2027-01-01" → 1 entry; comments+blank skipped."""
    allow_path = tmp_path / "allowlist.txt"
    allow_path.write_text(
        "# header comment\n"
        "\n"
        "foo.tf :: ABCD1234 :: kill-by:2027-01-01\n"
    )
    from bytedigger_engine.workflows.phase_5_devops_scan import _load_allowlist
    entries = _load_allowlist(allow_path)
    assert len(entries) == 1
    assert entries[0]["pattern"] == "foo.tf"
    assert entries[0]["agreement"] == "ABCD1234"
    assert str(entries[0]["kill_by"]) == "2027-01-01"


def test_ac5_load_allowlist_malformed_line_skipped_no_raise(tmp_path):
    """AC5: malformed line (no '::') is skipped silently, no raise."""
    allow_path = tmp_path / "allowlist.txt"
    allow_path.write_text("this line has no separator at all\n")
    from bytedigger_engine.workflows.phase_5_devops_scan import _load_allowlist
    assert _load_allowlist(allow_path) == []


def test_ac5_load_allowlist_missing_file_returns_empty(tmp_path):
    """AC5: missing allowlist file → []."""
    from bytedigger_engine.workflows.phase_5_devops_scan import _load_allowlist
    assert _load_allowlist(tmp_path / "does-not-exist.txt") == []


# ─── AC6-9: _split_blocking ───────────────────────────────────────────────────


def test_ac6_split_blocking_critical_empty_allowlist_is_blocking():
    """AC6: CRITICAL issue, empty allowlist → in blocking."""
    import datetime
    from bytedigger_engine.workflows.phase_5_devops_scan import _split_blocking
    issue = {"severity": "CRITICAL", "scanner": "trivy", "file": "a.tf", "description": "bad"}
    blocking, waived = _split_blocking(
        [issue], ["CRITICAL", "HIGH"], [], datetime.date(2026, 7, 5)
    )
    assert issue in blocking
    assert waived == []


def test_ac7_split_blocking_medium_default_sevs_is_warn_only():
    """AC7: MEDIUM issue, default fail_on_severities (no MEDIUM) → neither blocking nor waived."""
    import datetime
    from bytedigger_engine.workflows.phase_5_devops_scan import _split_blocking
    issue = {"severity": "MEDIUM", "scanner": "trivy", "file": "a.tf", "description": "meh"}
    blocking, waived = _split_blocking(
        [issue], ["CRITICAL", "HIGH"], [], datetime.date(2026, 7, 5)
    )
    assert issue not in blocking
    assert issue not in waived
    assert blocking == []
    assert waived == []


def test_ac8_split_blocking_waived_by_unexpired_allowlist_entry():
    """AC8: CRITICAL issue matching an unexpired allowlist entry → in waived, not blocking."""
    import datetime
    from bytedigger_engine.workflows.phase_5_devops_scan import _split_blocking
    issue = {"severity": "CRITICAL", "scanner": "trivy", "file": "foo.tf", "description": "bad"}
    entry = {"pattern": "foo.tf", "agreement": "ABCD1234", "kill_by": datetime.date(2027, 1, 1)}
    blocking, waived = _split_blocking(
        [issue], ["CRITICAL", "HIGH"], [entry], datetime.date(2026, 7, 5)
    )
    assert issue not in blocking
    assert len(waived) == 1


def test_ac9_split_blocking_expired_entry_reverts_to_blocking():
    """AC9: same entry with kill_by < today → issue is back in blocking (expiry, 585E30E3)."""
    import datetime
    from bytedigger_engine.workflows.phase_5_devops_scan import _split_blocking
    issue = {"severity": "CRITICAL", "scanner": "trivy", "file": "foo.tf", "description": "bad"}
    entry = {"pattern": "foo.tf", "agreement": "ABCD1234", "kill_by": datetime.date(2020, 1, 1)}
    blocking, waived = _split_blocking(
        [issue], ["CRITICAL", "HIGH"], [entry], datetime.date(2026, 7, 5)
    )
    assert issue in blocking
    assert waived == []


# ─── AC10-14: _scan_artifact gate-ON/OFF integration ─────────────────────────


def test_ac10_scan_artifact_gate_on_blocking_issue_errors(tmp_path, monkeypatch):
    """AC10: gate ON, 1 HIGH issue passed:false → status='error',
    error_code='E_DEVOPS_SCAN_BLOCKED', blocking_count==1."""
    monkeypatch.delenv("HAL_DEVOPS_SCAN_GATE", raising=False)
    monkeypatch.delenv("HAL_DEVOPS_SCAN_ALLOWLIST", raising=False)
    monkeypatch.delenv("HAL_DEVOPS_SCAN_CONFIG", raising=False)
    from bytedigger_engine.workflows.phase_5_devops_scan import _scan_artifact
    shim_stdout = json.dumps({
        "passed": False,
        "issues": [{"severity": "HIGH", "scanner": "hadolint", "file": "Dockerfile", "description": "DL3007"}],
        "scanners_run": ["hadolint"],
        "scanners_missing": [],
        "summary": "1 HIGH issue found.",
        "stats": {"critical": 0, "high": 1, "medium": 0, "low": 0, "total": 1},
        "skipped_reason": None,
    })
    ctx = make_ctx(tmp_path, changed_files=["Dockerfile"], artifact_type="dockerfile")
    with patch("bytedigger_engine.workflows.phase_5_devops_scan.subprocess.run", return_value=_stub_proc(stdout=shim_stdout)):
        result = _scan_artifact(ctx, None)
    assert result.status == "error"
    assert result.error_code == "E_DEVOPS_SCAN_BLOCKED"
    assert result.data["blocking_count"] == 1


def test_ac11_scan_artifact_gate_off_legacy_ok_shape(tmp_path, monkeypatch):
    """AC11: same input as AC10, HAL_DEVOPS_SCAN_GATE=0 → status='ok' (legacy shape)."""
    monkeypatch.setenv("HAL_DEVOPS_SCAN_GATE", "0")
    from bytedigger_engine.workflows.phase_5_devops_scan import _scan_artifact
    shim_stdout = json.dumps({
        "passed": False,
        "issues": [{"severity": "HIGH", "scanner": "hadolint", "file": "Dockerfile", "description": "DL3007"}],
        "scanners_run": ["hadolint"],
        "scanners_missing": [],
        "summary": "1 HIGH issue found.",
        "stats": {"critical": 0, "high": 1, "medium": 0, "low": 0, "total": 1},
        "skipped_reason": None,
    })
    ctx = make_ctx(tmp_path, changed_files=["Dockerfile"], artifact_type="dockerfile")
    with patch("bytedigger_engine.workflows.phase_5_devops_scan.subprocess.run", return_value=_stub_proc(stdout=shim_stdout)):
        result = _scan_artifact(ctx, None)
    assert result.status == "ok"
    assert result.data["passed"] is False


def test_ac12_scan_artifact_gate_on_timeout_unavailable(tmp_path, monkeypatch):
    """AC12: gate ON, rc=124 → status='error', error_code='E_DEVOPS_SCAN_UNAVAILABLE'."""
    monkeypatch.delenv("HAL_DEVOPS_SCAN_GATE", raising=False)
    from bytedigger_engine.workflows.phase_5_devops_scan import _scan_artifact
    ctx = make_ctx(tmp_path, changed_files=["Dockerfile"], artifact_type="dockerfile")
    with patch("bytedigger_engine.workflows.phase_5_devops_scan.subprocess.run", return_value=_stub_proc(returncode=124)):
        result = _scan_artifact(ctx, None)
    assert result.status == "error"
    assert result.error_code == "E_DEVOPS_SCAN_UNAVAILABLE"


def test_ac12_scan_artifact_gate_off_timeout_legacy_ok(tmp_path, monkeypatch):
    """AC12: gate OFF, rc=124 → status='ok', skipped_reason='timeout' (legacy shape untouched)."""
    monkeypatch.setenv("HAL_DEVOPS_SCAN_GATE", "0")
    from bytedigger_engine.workflows.phase_5_devops_scan import _scan_artifact
    ctx = make_ctx(tmp_path, changed_files=["Dockerfile"], artifact_type="dockerfile")
    with patch("bytedigger_engine.workflows.phase_5_devops_scan.subprocess.run", return_value=_stub_proc(returncode=124)):
        result = _scan_artifact(ctx, None)
    assert result.status == "ok"
    assert result.data["skipped_reason"] == "timeout"


def test_ac13_scan_artifact_gate_on_fully_waived_is_ok(tmp_path, monkeypatch):
    """AC13: gate ON, 1 CRITICAL issue fully waived by allowlist fixture →
    status='ok', data['waived'][0]['agreement']=='ABCD1234'."""
    monkeypatch.delenv("HAL_DEVOPS_SCAN_GATE", raising=False)
    allow_path = tmp_path / "allowlist.txt"
    allow_path.write_text("Dockerfile :: ABCD1234 :: kill-by:2099-01-01\n")
    monkeypatch.setenv("HAL_DEVOPS_SCAN_ALLOWLIST", str(allow_path))
    from bytedigger_engine.workflows.phase_5_devops_scan import _scan_artifact
    shim_stdout = json.dumps({
        "passed": False,
        "issues": [{"severity": "CRITICAL", "scanner": "trivy", "file": "Dockerfile", "description": "bad"}],
        "scanners_run": ["trivy"],
        "scanners_missing": [],
        "summary": "1 CRITICAL issue found.",
        "stats": {"critical": 1, "high": 0, "medium": 0, "low": 0, "total": 1},
        "skipped_reason": None,
    })
    ctx = make_ctx(tmp_path, changed_files=["Dockerfile"], artifact_type="dockerfile")
    with patch("bytedigger_engine.workflows.phase_5_devops_scan.subprocess.run", return_value=_stub_proc(stdout=shim_stdout)):
        result = _scan_artifact(ctx, None)
    assert result.status == "ok"
    assert result.data["waived"][0]["agreement"] == "ABCD1234"


def test_ac14_scan_artifact_gate_on_skip_path_untouched(tmp_path, monkeypatch):
    """AC14: gate ON, changed_files empty → status='ok', skipped_reason='no_files' (skip paths untouched)."""
    monkeypatch.delenv("HAL_DEVOPS_SCAN_GATE", raising=False)
    from bytedigger_engine.workflows.phase_5_devops_scan import _scan_artifact
    ctx = make_ctx(tmp_path, changed_files=[], artifact_type="dockerfile")
    result = _scan_artifact(ctx, None)
    assert result.status == "ok"
    assert result.data["skipped_reason"] == "no_files"
