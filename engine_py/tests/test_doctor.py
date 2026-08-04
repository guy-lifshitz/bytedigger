"""RED tests — AC1-AC8 for bd#16 `bytedigger doctor` self-check command.

engine_py/doctor.py does NOT exist yet. Imports of `doctor` are deferred to
inside each test function body per the D1CF5FDF non-collectable-hang rule so
that this file COLLECTS cleanly and each test FAILS at call/assert time,
never at collection time.
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess

from helpers.engine_subprocess import engine_env
import sys
import tempfile


def _engine_py_root() -> pathlib.Path:
    """engine_py root: this file lives at engine_py/tests/test_doctor.py."""
    return pathlib.Path(__file__).resolve().parents[1]


def _run_py() -> pathlib.Path:
    return _engine_py_root() / "bytedigger_engine" / "run.py"


def _run_doctor(*extra_args: str, timeout: int = 120) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_run_py()), "doctor", *extra_args], env=engine_env(),
        cwd=str(_engine_py_root()),
        capture_output=True,
        text=True,
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# AC1 — `python3 run.py doctor --json` exits 0, stdout parses as JSON with
# `status` and `checks`.
# ---------------------------------------------------------------------------

def test_ac1_doctor_json_exits_0_and_parses_with_status_and_checks():
    from helpers.host_tools import skip_without
    skip_without("git")
    r = _run_doctor("--json")
    assert r.returncode == 0, (
        f"expected exit 0 on healthy checkout, got {r.returncode}; "
        f"stdout={r.stdout!r} stderr={r.stderr!r}"
    )
    payload = json.loads(r.stdout)
    assert "status" in payload
    assert "checks" in payload


# ---------------------------------------------------------------------------
# AC2 — checks[].name superset of the 8 contract names; every status in
# {ok, warn}.
# ---------------------------------------------------------------------------

def test_ac2_json_checks_cover_contract_names_and_statuses_ok_or_warn():
    from helpers.host_tools import skip_without
    skip_without("git")
    r = _run_doctor("--json")
    payload = json.loads(r.stdout)
    checks = payload["checks"]
    names = {c["name"] for c in checks}
    expected = {
        "python-version",
        "engine-imports",
        "gates-importable",
        "optional-deps",
        "config-resolve",
        "event-log-writable",
        "backend-registry",
        "engine-smoke",
    }
    assert expected.issubset(names), f"missing checks: {expected - names}"
    for c in checks:
        assert c["status"] in ("ok", "warn"), (
            f"check {c['name']!r} has unexpected status {c['status']!r} "
            f"(detail={c.get('detail')!r}) on a healthy checkout"
        )


# ---------------------------------------------------------------------------
# AC3 — human mode exits 0, stdout has `doctor:` summary + one [OK]/[WARN]
# line per check.
# ---------------------------------------------------------------------------

def test_ac3_human_mode_exits_0_with_summary_and_per_check_lines():
    from helpers.host_tools import skip_without
    skip_without("git")
    r = _run_doctor()
    assert r.returncode == 0, (
        f"expected exit 0, got {r.returncode}; stdout={r.stdout!r} stderr={r.stderr!r}"
    )
    assert "doctor:" in r.stdout
    ok_or_warn_lines = [
        line for line in r.stdout.splitlines()
        if line.startswith("[OK]") or line.startswith("[WARN]")
    ]
    assert len(ok_or_warn_lines) >= 13, (
        f"expected >=13 per-check [OK]/[WARN] lines, got {len(ok_or_warn_lines)}: "
        f"{r.stdout!r}"
    )


# ---------------------------------------------------------------------------
# AC4 — check_event_log("<unwritable dir>") returns status fail.
# ---------------------------------------------------------------------------

def test_ac4_check_event_log_returns_fail_on_unwritable_dir():
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        import pytest
        pytest.skip("running as root — chmod 0o000 has no effect")

    sys.path.insert(0, str(_engine_py_root()))
    from bytedigger_engine import doctor  # deferred import — module does not exist yet

    unwritable = tempfile.mkdtemp()
    os.chmod(unwritable, 0o000)
    try:
        result = doctor.check_event_log(unwritable)
        assert result["status"] == "fail", (
            f"expected status 'fail' for unwritable dir, got {result!r}"
        )
    finally:
        os.chmod(unwritable, 0o755)


# ---------------------------------------------------------------------------
# AC5 — run_doctor with forced-fail check (monkeypatched _mkdtemp pointing at
# an unwritable dir) -> exit code 1, event-log-writable check fails.
# ---------------------------------------------------------------------------

def test_ac5_run_doctor_exits_1_when_mkdtemp_seam_forced_to_unwritable_dir(monkeypatch):
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        import pytest
        pytest.skip("running as root — chmod 0o000 has no effect")

    sys.path.insert(0, str(_engine_py_root()))
    from bytedigger_engine import doctor  # deferred import — module does not exist yet

    unwritable = tempfile.mkdtemp()
    os.chmod(unwritable, 0o000)
    try:
        monkeypatch.setattr(doctor, "_mkdtemp", lambda: unwritable)
        exit_code = doctor.doctor_main([])
        assert exit_code == 1, (
            f"expected doctor_main to return 1 when event-log-writable check "
            f"fails via forced-unwritable _mkdtemp seam, got {exit_code}"
        )
    finally:
        os.chmod(unwritable, 0o755)


# ---------------------------------------------------------------------------
# AC6 — `python3 run.py doctor --bogus-flag` exits 2 with a usage message on
# stderr.
# ---------------------------------------------------------------------------

def test_ac6_unknown_flag_exits_2_with_usage_on_stderr():
    r = _run_doctor("--bogus-flag")
    assert r.returncode == 2, (
        f"expected exit 2 for unknown flag, got {r.returncode}; "
        f"stdout={r.stdout!r} stderr={r.stderr!r}"
    )
    assert r.stderr.strip() != "", "expected a usage message on stderr"


# ---------------------------------------------------------------------------
# AC7 — engine-smoke check ok on healthy checkout, detail mentions 'echo'.
# ---------------------------------------------------------------------------

def test_ac7_engine_smoke_check_ok_and_detail_mentions_echo():
    r = _run_doctor("--json")
    payload = json.loads(r.stdout)
    smoke = next(c for c in payload["checks"] if c["name"] == "engine-smoke")
    assert smoke["status"] == "ok", (
        f"expected engine-smoke ok on healthy checkout, got {smoke!r}"
    )
    assert "echo" in smoke["detail"], (
        f"expected 'echo' in engine-smoke detail, got {smoke['detail']!r}"
    )


# ---------------------------------------------------------------------------
# AC8 — run.py without `doctor` unchanged: --list still exits 0, prints JSON
# array containing "echo".
# ---------------------------------------------------------------------------

def test_ac8_run_py_list_unaffected_by_doctor_dispatch():
    r = subprocess.run(
        [sys.executable, str(_run_py()), "--list"], env=engine_env(),
        cwd=str(_engine_py_root()),
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert r.returncode == 0, (
        f"expected --list to still exit 0, got {r.returncode}; "
        f"stdout={r.stdout!r} stderr={r.stderr!r}"
    )
    workflows = json.loads(r.stdout)
    assert isinstance(workflows, list)
    assert "echo" in workflows, f"expected 'echo' in --list output, got {workflows!r}"
