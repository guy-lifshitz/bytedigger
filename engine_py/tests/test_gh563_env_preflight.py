"""GH #563 — deterministic env-preflight guard (Studio env-class).

Root cause of the 44F full-run class on Mac Studio (2026-07-11): the engine's
subprocess pytest seam (`_runner_for_path`, phase_5_implement.py) spawns bare
`python3 -m pytest` from ambient PATH. On a machine where PATH `python3` lacks
pytest (or engine requirements like dbos), every test that exercises that seam
fails with a diffuse AssertionError — 44 scattered reds instead of one clear
signal.

This preflight turns the whole class into ONE named red with a remediation
command. If these tests fail, fix the machine env — do not chase the 40+
downstream failures.

Remediation (mirror the laptop env):
    python3 -m pip install --break-system-packages \
        -r SYSTEM/cli/build/engine_py/requirements.txt pytest==9.0.3 PyYAML

NB: homebrew python site-packages are per-minor-version — a `brew upgrade`
that bumps python3 (e.g. 3.14 -> 3.15) silently drops these packages and
re-triggers this guard. That is by design: the guard is the tripwire.
"""
from __future__ import annotations

import subprocess

_REMEDIATION = (
    "env-precondition #563: PATH python3 is missing engine test deps. Fix: "
    "python3 -m pip install --break-system-packages "
    "-r SYSTEM/cli/build/engine_py/requirements.txt pytest==9.0.3 PyYAML"
)


def _path_python3(args: list[str]) -> subprocess.CompletedProcess:
    # Bare "python3" on purpose — this must probe the SAME interpreter the
    # engine's subprocess seam resolves at runtime (phase_5_implement.py:1467).
    return subprocess.run(
        ["python3", *args], capture_output=True, text=True, timeout=60
    )


def test_path_python3_has_pytest():
    proc = _path_python3(["-m", "pytest", "--version"])
    assert proc.returncode == 0, (
        f"{_REMEDIATION}\n`python3 -m pytest --version` failed: "
        f"{(proc.stderr or proc.stdout).strip()[:300]}"
    )


def test_path_python3_has_engine_requirements():
    proc = _path_python3(["-c", "import dbos"])
    assert proc.returncode == 0, (
        f"{_REMEDIATION}\n`python3 -c 'import dbos'` failed: "
        f"{(proc.stderr or proc.stdout).strip()[:300]}"
    )
