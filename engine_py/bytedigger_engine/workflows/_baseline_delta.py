"""GH561 (§1r lane 2) — wire baseline_delta_gate into phase_5/phase_6 verify paths.

Spec: the state cache dir under memory/Decisions/2026-07-11_GH561_baseline_delta_wiring_spec.md

Single public function `run_baseline_delta_gate` invokes the lane-1 deterministic
script (`baseline_delta_gate.py`) as a subprocess, parses its JSON verdict, and
emits a warn-only telemetry event. Warn-only by default (enforce flag default 0).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from bytedigger_engine.config_provider import get_config


def run_baseline_delta_gate(stdout_path, suite, git_cwd, phase, step, emit, cfg=None) -> dict:
    cfg = cfg or get_config()

    if not cfg.gate_enabled("HAL_BASELINE_DELTA_GATE"):
        return {"skipped": "gate_disabled"}

    default_script = Path(__file__).resolve().parents[3] / "baseline_delta_gate.py"
    try:
        script = cfg.path("HAL_BASELINE_DELTA_BIN", default_script)
    except AttributeError:  # minimal-Protocol providers without .path
        script = default_script
    script = Path(script)

    if not script.is_file():
        emit("baseline_delta_gate_skipped", {"reason": "script_missing", "script": str(script)})
        return {"skipped": "script_missing"}

    enforced = bool(cfg.flag("HAL_BASELINE_DELTA_ENFORCE"))

    try:
        proc = subprocess.run(
            [sys.executable, str(script), "--results", stdout_path, "--suite", suite],
            cwd=git_cwd, capture_output=True, text=True, timeout=60,
        )
    except (subprocess.TimeoutExpired, OSError):
        emit("baseline_delta_gate_skipped", {"reason": "exec_error"})
        return {"skipped": "exec_error", "would_block": enforced}

    if proc.returncode == 2:
        emit("baseline_delta_gate_skipped", {"reason": "driver_error"})
        return {"skipped": "driver_error", "would_block": enforced}

    lines = [ln for ln in (proc.stdout or "").splitlines() if ln.strip()]
    verdict_json = None
    if lines:
        try:
            verdict_json = json.loads(lines[-1])
        except (json.JSONDecodeError, ValueError):
            verdict_json = None

    if verdict_json is None:
        return {"skipped": "parse_error", "would_block": enforced}

    verdict = verdict_json.get("verdict")
    new_fails = verdict_json.get("new_fails", [])
    ledgered = verdict_json.get("ledgered", [])
    baseline_source = verdict_json.get("baseline_source")
    would_block = enforced and verdict == "FAIL"

    emit("baseline_delta_gate_verdict", {
        "suite": suite,
        "verdict": verdict,
        "new_fails": new_fails,
        "n_new_fails": len(new_fails),
        "ledgered": ledgered,
        "baseline_source": baseline_source,
        "enforced": enforced,
        "phase": phase,
        "step": step,
    }, severity="warning")

    return {"verdict": verdict, "n_new_fails": len(new_fails), "would_block": would_block, "skipped": None}
