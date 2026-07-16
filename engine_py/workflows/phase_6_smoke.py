"""Phase-6 signal smoke as a StepContract.

Wraps USER/skills/HALForge/tests/phase-6-signal-smoke.sh — runs it as a
subprocess, parses PASS:/FAIL: counts plus the STATUS=PASS/FAIL summary,
returns {passed, failed, total, status_line, exit_code} in StepResult.data.

Stage 1b proof-of-port: existing shell stays intact, engine becomes the
single mutation point. Hooks become observers in Stage 2.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))
from worktree_root import resolve_worktree_root  # noqa: E402
from bounded_spawn import bounded_run  # noqa: E402

from contracts import StepContract, StepResult, WorkflowDefinition

# engine_py/workflows → engine_py → build → cli → SYSTEM → .claude
HAL_DIR = Path(__file__).resolve().parents[5]
_SMOKE_REL = "USER/skills/HALForge/tests/phase-6-signal-smoke.sh"
SMOKE_SCRIPT = HAL_DIR / _SMOKE_REL

_PASS_RE = re.compile(r"^PASS:", re.MULTILINE)
_FAIL_RE = re.compile(r"^FAIL:", re.MULTILINE)
_STATUS_RE = re.compile(r"^STATUS=(PASS|FAIL)\s*$", re.MULTILINE)


def parse_smoke_output(stdout: str) -> dict:
    """Parse PASS:/FAIL:/STATUS= markers from the smoke script's stdout."""
    passed = len(_PASS_RE.findall(stdout))
    failed = len(_FAIL_RE.findall(stdout))
    status_match = _STATUS_RE.search(stdout)
    return {
        "passed": passed,
        "failed": failed,
        "total": passed + failed,
        "status_line": status_match.group(1) if status_match else None,
    }


def _run_smoke(_ctx, _prev) -> StepResult:
    root = resolve_worktree_root(_ctx, Path(__file__))
    smoke_script = root / _SMOKE_REL
    if not smoke_script.exists():
        return StepResult(
            status="ok",
            data={"skipped": "smoke_script_absent", "smoke_script": str(smoke_script)},
            duration_ms=0,
            step_name="phase_6_smoke",
        )

    if shutil.which("zsh") is None:
        return StepResult(
            status="ok",
            data={"skipped": "zsh_unavailable", "smoke_script": str(smoke_script)},
            duration_ms=0,
            step_name="phase_6_smoke",
        )

    proc = bounded_run(
        ["zsh", str(smoke_script)],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(root),
    )

    if proc.returncode == 124:
        return StepResult(
            status="error",
            data={"timeout_sec": 120, "stdout_partial": (proc.stdout or "")[:2000]},
            duration_ms=120_000,
            step_name="phase_6_smoke",
            error="smoke script exceeded 120s timeout",
            error_code="E_SMOKE_TIMEOUT",
        )

    parsed = parse_smoke_output(proc.stdout)
    data = {
        **parsed,
        "exit_code": proc.returncode,
    }

    ok = (
        proc.returncode == 0
        and parsed["failed"] == 0
        and parsed["status_line"] == "PASS"
    )
    if ok:
        return StepResult(
            status="ok",
            data=data,
            duration_ms=0,
            step_name="phase_6_smoke",
        )
    return StepResult(
        status="error",
        data=data,
        duration_ms=0,
        step_name="phase_6_smoke",
        error=(
            f"smoke failed: passed={parsed['passed']} failed={parsed['failed']} "
            f"exit={proc.returncode} status_line={parsed['status_line']}"
        ),
        error_code="E_SMOKE_FAILED",
    )


def phase_6_smoke_workflow() -> WorkflowDefinition:
    return WorkflowDefinition(
        name="phase_6_smoke",
        steps=[StepContract(name="phase_6_smoke", execute=_run_smoke)],
    )
