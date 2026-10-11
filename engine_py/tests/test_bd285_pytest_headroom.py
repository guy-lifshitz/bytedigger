"""bd#285: the pytest CI job ceiling has headroom and warns before it is hit.

Spec: docs/decisions/2026-10-11-bd285-pytest-ceiling-headroom.md (AC1..AC7, r2).

Real paths only: the real scripts/ci_pytest_headroom.py is run in a
subprocess against the real .github/workflows/ci.yml (or tmp workflows for
the parser-contract cases). The real ceiling is read from ci.yml here, never
written as a number in this file.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "ci_pytest_headroom.py"
CI_YML = ROOT / ".github" / "workflows" / "ci.yml"
SWITCH = "BD_CI_PYTEST_HEADROOM_WARN"
REFERENCE_RUN_SEC = 1178  # run 38156738352, the issue's reference run


def _job_block(text: str, job: str = "pytest") -> str:
    lines = text.splitlines()
    out, inside = [], False
    for line in lines:
        if not inside:
            if line == f"  {job}:":
                inside = True
                out.append(line)
            continue
        if re.match(r"^  [A-Za-z0-9_-]+:\s*$", line):
            break
        out.append(line)
    return "\n".join(out)


def _ceiling_sec() -> int:
    block = _job_block(CI_YML.read_text())
    m = re.search(
        r"^    timeout-minutes:\s*[\"']?(\d+)[\"']?\s*(?:#.*)?$", block, re.M
    )
    assert m, "pytest job has no job-level timeout-minutes in ci.yml"
    return int(m.group(1)) * 60


def _run(duration, switch=None, workflow=None, cwd=ROOT):
    env = dict(os.environ)
    env.pop(SWITCH, None)
    if switch is not None:
        env[SWITCH] = switch
    cmd = [sys.executable, str(SCRIPT), "--duration-sec", str(duration)]
    if workflow is not None:
        cmd += ["--workflow", str(workflow)]
    return subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, env=env)


def _warnings(stdout: str):
    return [ln for ln in stdout.splitlines() if "::warning::" in ln]


def _write_wf(path, ceiling_line, decoy_ceiling="    timeout-minutes: 5"):
    """Workflow with a decoy `lint` job first, then a `pytest` job whose
    job-level line is `ceiling_line` (None = no job-level key) and a
    step-level timeout-minutes inside its steps."""
    parts = [
        "name: t\n",
        "jobs:\n",
        "  lint:\n",
        "    runs-on: ubuntu-latest\n",
        decoy_ceiling + "\n",
        "    steps:\n",
        "      - run: echo lint\n",
        "  pytest:\n",
        "    runs-on: ubuntu-latest\n",
    ]
    if ceiling_line is not None:
        parts.append(ceiling_line + "\n")
    parts += [
        "    steps:\n",
        "      - run: echo hi\n",
        "        timeout-minutes: 99\n",
    ]
    path.write_text("".join(parts))
    return path


def test_ac1_reference_run_no_warning_when_switch_unset():
    r = _run(REFERENCE_RUN_SEC)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip(), "script produced no output (did not run)"
    assert _warnings(r.stdout) == []


def test_ac2_above_70_percent_warns_once_with_ceiling_and_percent():
    ceiling = _ceiling_sec()
    r = _run(int(ceiling * 0.70) + 1)
    assert r.returncode == 0, r.stderr
    warns = _warnings(r.stdout)
    assert len(warns) == 1, r.stdout
    assert str(ceiling) in warns[0]
    assert "%" in warns[0]


def test_ac2b_exactly_70_percent_does_not_warn():
    ceiling = _ceiling_sec()
    r = _run(round(ceiling * 0.7))
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip(), "script produced no output (did not run)"
    assert _warnings(r.stdout) == []


@pytest.mark.parametrize("switch", ["0", "False", "off", " NO "])
def test_ac3_switch_disabled_is_silent_but_prints(switch):
    duration = int(_ceiling_sec() * 0.70) + 1
    r = _run(duration, switch=switch)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip(), "script produced no output (did not run)"
    assert _warnings(r.stdout) == []


@pytest.mark.parametrize("switch", ["1", ""])
def test_ac3b_other_switch_values_are_enabled(switch):
    duration = int(_ceiling_sec() * 0.70) + 1
    r = _run(duration, switch=switch)
    assert r.returncode == 0, r.stderr
    assert len(_warnings(r.stdout)) == 1, r.stdout


def test_ac3c_switch_off_wins_over_unreadable_workflow(tmp_path):
    r = _run(100, switch="0", workflow=tmp_path / "missing.yml")
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip(), "script produced no output (did not run)"
    assert _warnings(r.stdout) == []


def test_ac4_workflow_without_pytest_job_warns_and_exits_zero(tmp_path):
    wf = tmp_path / "other.yml"
    wf.write_text(
        "name: other\n"
        "jobs:\n"
        "  lint:\n"
        "    runs-on: ubuntu-latest\n"
        "    timeout-minutes: 7\n"
        "    steps:\n"
        "      - run: echo hi\n"
    )
    r = _run(100, workflow=wf)
    assert r.returncode == 0, r.stderr
    assert _warnings(r.stdout), r.stdout


@pytest.mark.parametrize(
    "ceiling_line",
    [
        "    timeout-minutes: ${{ vars.X }}",
        "    timeout-minutes: 0",
        "    timeout-minutes:",
        "    timeout-minutes: abc",
    ],
)
def test_ac4b_unreadable_ceiling_warns_and_exits_zero(tmp_path, ceiling_line):
    wf = _write_wf(tmp_path / "w.yml", ceiling_line)
    r = _run(100, workflow=wf)
    assert r.returncode == 0, r.stderr
    assert _warnings(r.stdout), r.stdout


def test_ac4b_nonexistent_workflow_warns_and_exits_zero(tmp_path):
    r = _run(100, workflow=tmp_path / "nope.yml")
    assert r.returncode == 0, r.stderr
    assert _warnings(r.stdout), r.stdout


@pytest.mark.parametrize("duration", ["abc", "-5"])
def test_ac4b_bad_duration_warns_and_exits_zero(duration):
    r = _run(duration)
    assert r.returncode == 0, r.stderr
    assert _warnings(r.stdout), r.stdout


def test_ac5_ceiling_is_job_level_value_of_named_job(tmp_path):
    # decoy job ceiling 5 min first; pytest job-level "7" (quoted, comment);
    # step-level 99 inside its steps. Ceiling must be 7 * 60 = 420 s.
    wf = _write_wf(tmp_path / "a.yml", '    timeout-minutes: "7"   # tight')
    over = _run(295, workflow=wf)  # 2950 > 2940
    assert over.returncode == 0, over.stderr
    assert len(_warnings(over.stdout)) == 1, over.stdout
    assert "420" in _warnings(over.stdout)[0]

    at = _run(294, workflow=wf)  # 2940 > 2940 is false
    assert at.returncode == 0, at.stderr
    assert at.stdout.strip(), "script produced no output (did not run)"
    assert _warnings(at.stdout) == []

    # 1178 s is far over a 420 s ceiling: no hardcoded real ceiling.
    ref = _run(REFERENCE_RUN_SEC, workflow=wf)
    assert ref.returncode == 0, ref.stderr
    assert len(_warnings(ref.stdout)) == 1, ref.stdout


def test_ac5_second_tmp_workflow_other_ceiling(tmp_path):
    wf = _write_wf(
        tmp_path / "b.yml", "    timeout-minutes: 60", decoy_ceiling="    timeout-minutes: 3"
    )
    quiet = _run(REFERENCE_RUN_SEC, workflow=wf)  # 11780 < 25200
    assert quiet.returncode == 0, quiet.stderr
    assert quiet.stdout.strip(), "script produced no output (did not run)"
    assert _warnings(quiet.stdout) == []

    loud = _run(2521, workflow=wf)  # 25210 > 25200
    assert loud.returncode == 0, loud.stderr
    warns = _warnings(loud.stdout)
    assert len(warns) == 1, loud.stdout
    assert "3600" in warns[0]


def test_ac6_default_workflow_resolves_from_script_not_cwd():
    r = _run(REFERENCE_RUN_SEC, cwd=ROOT / "engine_py")
    assert r.returncode == 0, r.stderr
    assert _warnings(r.stdout) == [], r.stdout
    assert "ok:" in r.stdout, r.stdout


def test_ac7_ci_yml_ceiling_and_wiring():
    doc = yaml.safe_load(CI_YML.read_text())
    job = doc["jobs"]["pytest"]
    assert job["timeout-minutes"] * 60 >= 1.5 * REFERENCE_RUN_SEC

    steps = job["steps"]

    def run_of(s):
        return s.get("run", "") if isinstance(s, dict) else ""

    t0 = [
        i
        for i, s in enumerate(steps)
        if "PYTEST_JOB_T0" in run_of(s) and "GITHUB_ENV" in run_of(s)
    ]
    suite = [
        i
        for i, s in enumerate(steps)
        if "pytest tests/" in run_of(s)
        and "--timeout=120" in run_of(s)
        and "-p no:cacheprovider" in run_of(s)
        and "tests/ -q" in run_of(s)
    ]
    head = [
        i
        for i, s in enumerate(steps)
        if isinstance(s, dict)
        and s.get("continue-on-error") is True
        and "../scripts/ci_pytest_headroom.py" in run_of(s)
        and "--duration-sec" in run_of(s)
        and "PYTEST_JOB_T0" in run_of(s)
    ]
    assert t0, "no step writes PYTEST_JOB_T0 to GITHUB_ENV"
    assert suite, "final suite step not found"
    assert head, "no continue-on-error headroom step with --duration-sec"
    assert min(t0) < suite[-1] < max(head)
