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
    # GH1338 §2.3: the parity precondition is activated UNCONDITIONALLY from
    # this wiring — never left to the human argv (M5).
    parity_enforced = bool(cfg.flag("HAL_CORPUS_PARITY_ENFORCE"))

    try:
        proc = subprocess.run(
            [
                sys.executable, str(script), "--results", stdout_path, "--suite", suite,
                "--require-corpus-parity",
            ],
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
    delta_verdict_raw = verdict_json.get("delta_verdict")
    new_fails = verdict_json.get("new_fails", [])
    ledgered = verdict_json.get("ledgered", [])
    baseline_source = verdict_json.get("baseline_source")
    blocked_by = verdict_json.get("blocked_by", [])
    only_in_base = verdict_json.get("only_in_base", [])
    declared_removal_count = verdict_json.get("declared_removal_count", 0)

    # §2.3 gate M2: the delta lane must not be masked by a compound BLOCKED
    # verdict. A missing delta_verdict key (old script via HAL_BASELINE_DELTA_BIN)
    # resolves from the legacy top-level `verdict` field (GH561 semantics) when
    # it is a clean PASS/FAIL; any other value (e.g. BLOCKED, unknown) fails
    # CLOSED under enforce, rather than silently passing.
    if delta_verdict_raw is not None:
        delta_fail = enforced and delta_verdict_raw == "FAIL"
    elif verdict in ("PASS", "FAIL"):
        delta_fail = enforced and verdict == "FAIL"
    else:
        delta_fail = enforced

    parity_block = parity_enforced and verdict == "BLOCKED"
    would_block = delta_fail or parity_block

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
        "blocked_by": blocked_by,
        "only_in_base": only_in_base,
        "declared_removal_count": declared_removal_count,
    }, severity="warning")

    return {
        "verdict": verdict,
        "n_new_fails": len(new_fails),
        "would_block": would_block,
        "parity_block": parity_block,
        "blocked_by": blocked_by,
        "skipped": None,
    }
