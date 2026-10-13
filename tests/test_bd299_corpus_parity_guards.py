"""bd#299 S4 guards: must pass before and after the retirement of
HAL_CORPUS_PARITY_ENFORCE. No sys.path mutation, no conftest import."""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RETIRED = "HAL_CORPUS_PARITY_ENFORCE"
LIVE_FLAG = "HAL_BASELINE_DELTA_ENFORCE"
CATALOG_SRC = REPO / "engine_py" / "bytedigger_engine" / "flags_catalog.py"
LEDGER = REPO / "scripts" / "flip_horizon_ledger.json"
DRIVER = REPO / "baseline_delta_gate.py"


def _catalog_keys():
    tree = ast.parse(CATALOG_SRC.read_text())
    for node in tree.body:
        target = value = None
        if isinstance(node, ast.AnnAssign):
            target, value = node.target, node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            target, value = node.targets[0], node.value
        if isinstance(target, ast.Name) and target.id in ("FLAGS", "CATALOG") \
                and isinstance(value, ast.Dict):
            return {k.value for k in value.keys if isinstance(k, ast.Constant)}
    raise AssertionError("flags catalog dict not found")


def test_baseline_delta_enforce_still_registered_with_unchanged_expiry():
    assert LIVE_FLAG in _catalog_keys()
    ledger = json.loads(LEDGER.read_text())
    assert LIVE_FLAG in ledger
    assert ledger[LIVE_FLAG]["until"] == "2026-11-03"


def _clean_env(**overrides):
    env = dict(os.environ)
    for k in (LIVE_FLAG, "HAL_BASELINE_DELTA_GATE_ENFORCE", RETIRED,
              "HAL_CORPUS_ALLOW_REMOVED", "HAL_KNOWN_REDS_TODAY"):
        env.pop(k, None)
    env.update(overrides)
    return env


def test_driver_blocked_precondition_exits_5_without_any_flag(tmp_path):
    results = tmp_path / "results.txt"
    results.write_text("ImportError: collection failed\n")  # no summary -> BLOCKED
    base = tmp_path / "base.txt"
    base.write_text("")
    proc = subprocess.run(
        [sys.executable, str(DRIVER), "--results", str(results), "--suite", "pytest",
         "--baseline", str(base), "--require-corpus-parity", "--base-ref", "HEAD"],
        cwd=str(REPO), env=_clean_env(), capture_output=True, text=True, timeout=120,
    )
    out = json.loads(proc.stdout.strip().splitlines()[-1])
    assert out["verdict"] == "BLOCKED", out
    assert proc.returncode == 5, (proc.returncode, proc.stderr[-500:])
