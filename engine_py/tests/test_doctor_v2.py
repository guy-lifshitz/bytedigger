"""RED tests — AC1-AC9 for bd#77 doctor v2 (5 new backend/runtime checks).

engine_py/doctor.py does not yet have the v2 checks/seams. Imports of `doctor`
are deferred to inside each test function body per the D1CF5FDF
non-collectable-hang rule so this file COLLECTS cleanly and each test FAILS
at call/assert time, never at collection time.
"""
from __future__ import annotations

import json
import pathlib
import subprocess
import sys


def _engine_py_root() -> pathlib.Path:
    """engine_py root: this file lives at engine_py/tests/test_doctor_v2.py."""
    return pathlib.Path(__file__).resolve().parents[1]


def _run_py() -> pathlib.Path:
    return _engine_py_root() / "run.py"


def _run_doctor(*extra_args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_run_py()), "doctor", *extra_args],
        cwd=str(_engine_py_root()),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _import_doctor():
    sys.path.insert(0, str(_engine_py_root()))
    import doctor  # deferred import — v2 seams/checks do not exist yet
    return doctor


# ---------------------------------------------------------------------------
# AC1 — claude-cli: found + rc==0 -> ok, detail contains version string.
# ---------------------------------------------------------------------------

def test_ac1_claude_cli_ok_when_found_and_version_succeeds(monkeypatch):
    doctor = _import_doctor()
    assert hasattr(doctor, "check_claude_cli"), "check_claude_cli not implemented yet"
    monkeypatch.setattr(doctor, "_which", lambda name: "/x/claude")
    monkeypatch.setattr(doctor, "_run_cmd", lambda argv, timeout=10: (0, "1.2.3"))
    result = doctor.check_claude_cli()
    assert result["status"] == "ok", f"expected ok, got {result!r}"
    assert "1.2.3" in result["detail"]


# ---------------------------------------------------------------------------
# AC2 — claude-cli: not found -> warn (not fail).
# ---------------------------------------------------------------------------

def test_ac2_claude_cli_warn_when_not_found(monkeypatch):
    doctor = _import_doctor()
    assert hasattr(doctor, "check_claude_cli"), "check_claude_cli not implemented yet"
    monkeypatch.setattr(doctor, "_which", lambda name: None)
    result = doctor.check_claude_cli()
    assert result["status"] == "warn", f"expected warn, got {result!r}"


# ---------------------------------------------------------------------------
# AC3 — agent-sdk-import: find_spec None -> warn, detail has install hint.
# ---------------------------------------------------------------------------

def test_ac3_agent_sdk_import_warn_with_install_hint_when_missing(monkeypatch):
    doctor = _import_doctor()
    assert hasattr(doctor, "check_agent_sdk_import"), "check_agent_sdk_import not implemented yet"
    monkeypatch.setattr(doctor, "_find_spec", lambda name: None)
    result = doctor.check_agent_sdk_import()
    assert result["status"] == "warn", f"expected warn, got {result!r}"
    assert "pip install claude-agent-sdk" in result["detail"], (
        f"expected install hint in detail, got {result['detail']!r}"
    )


# ---------------------------------------------------------------------------
# AC4 — git-runtime: _which("git") None -> fail, AND aggregated doctor_main
# flips to non-zero exit (real aggregation, §1l forcing function).
# ---------------------------------------------------------------------------

def test_ac4_git_runtime_fail_when_git_missing_flips_aggregate_exit(monkeypatch):
    doctor = _import_doctor()
    assert hasattr(doctor, "check_git_runtime"), "check_git_runtime not implemented yet"
    monkeypatch.setattr(doctor, "_which", lambda name: None)
    result = doctor.check_git_runtime()
    assert result["status"] == "fail", f"expected fail, got {result!r}"

    exit_code = doctor.doctor_main([])
    assert exit_code == 1, (
        f"expected doctor_main to return 1 when git-runtime check fails "
        f"via forced-missing _which seam, got {exit_code}"
    )


# ---------------------------------------------------------------------------
# AC5 — config-json: invalid bytedigger.json in cwd -> fail, non-empty detail.
# ---------------------------------------------------------------------------

def test_ac5_config_json_fail_on_invalid_json(tmp_path, monkeypatch):
    doctor = _import_doctor()
    assert hasattr(doctor, "check_config_json"), "check_config_json not implemented yet"
    (tmp_path / "bytedigger.json").write_text("{not valid json", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    result = doctor.check_config_json()
    assert result["status"] == "fail", f"expected fail, got {result!r}"
    assert result["detail"].strip() != "", "expected non-empty detail on parse error"


# ---------------------------------------------------------------------------
# AC6 — config-json: absent -> ok ("absent" detail); valid -> ok (key-count
# detail); the two details must differ.
# ---------------------------------------------------------------------------

def test_ac6_config_json_ok_for_absent_and_valid_with_differing_details(tmp_path, monkeypatch):
    doctor = _import_doctor()
    assert hasattr(doctor, "check_config_json"), "check_config_json not implemented yet"

    monkeypatch.chdir(tmp_path)
    absent_result = doctor.check_config_json()
    assert absent_result["status"] == "ok", f"expected ok for absent config, got {absent_result!r}"

    (tmp_path / "bytedigger.json").write_text(json.dumps({"a": 1}), encoding="utf-8")
    valid_result = doctor.check_config_json()
    assert valid_result["status"] == "ok", f"expected ok for valid config, got {valid_result!r}"

    assert absent_result["detail"] != valid_result["detail"], (
        "expected absent-case and valid-case details to differ"
    )


# ---------------------------------------------------------------------------
# AC7 — real subprocess `doctor --json`: exit 0, checks length == 13, names
# include all 5 new v2 checks.
# ---------------------------------------------------------------------------

def test_ac7_json_mode_reports_13_checks_including_all_new_v2_names():
    r = _run_doctor("--json")
    assert r.returncode == 0, (
        f"expected exit 0 on healthy checkout, got {r.returncode}; "
        f"stdout={r.stdout!r} stderr={r.stderr!r}"
    )
    payload = json.loads(r.stdout)
    checks = payload["checks"]
    assert len(checks) == 13, f"expected 13 checks, got {len(checks)}: {checks!r}"
    names = {c["name"] for c in checks}
    expected_new = {
        "claude-cli",
        "agent-sdk-import",
        "git-runtime",
        "config-json",
        "env-alias",
    }
    assert expected_new.issubset(names), f"missing new v2 checks: {expected_new - names}"


# ---------------------------------------------------------------------------
# AC8 — human mode: >=13 status lines + summary line present.
# ---------------------------------------------------------------------------

def test_ac8_human_mode_reports_at_least_13_status_lines_and_summary():
    r = _run_doctor()
    assert r.returncode == 0, (
        f"expected exit 0, got {r.returncode}; stdout={r.stdout!r} stderr={r.stderr!r}"
    )
    assert "doctor:" in r.stdout
    status_lines = [
        line for line in r.stdout.splitlines()
        if line.startswith("[OK]") or line.startswith("[WARN]") or line.startswith("[FAIL]")
    ]
    assert len(status_lines) >= 13, (
        f"expected >=13 per-check status lines, got {len(status_lines)}: {r.stdout!r}"
    )


# ---------------------------------------------------------------------------
# AC9 — env-alias: get_config() raising -> check fail.
# ---------------------------------------------------------------------------

def test_ac9_env_alias_fail_when_get_config_raises(monkeypatch):
    doctor = _import_doctor()
    assert hasattr(doctor, "check_env_alias"), "check_env_alias not implemented yet"
    import config_provider

    def _boom():
        raise RuntimeError("config provider exploded")

    monkeypatch.setattr(config_provider, "get_config", _boom)
    result = doctor.check_env_alias()
    assert result["status"] == "fail", f"expected fail, got {result!r}"
