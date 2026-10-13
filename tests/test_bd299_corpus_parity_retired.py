"""bd#299 S4 RED: HAL_CORPUS_PARITY_ENFORCE is retired (precedent bd#296/#298).

Spec: docs/decisions/2026-10-13-bd299-baseline-delta-driver.md section S4.
No sys.path mutation, no conftest import (workflows.md 1q, scanner-safe): the
wiring runs in a subprocess with PYTHONPATH=engine_py (same seam as
tests/test_bd299_wiring_shield.py). Deterministic, no network, no model calls.
Every test here fails today; the passing guards live in
tests/test_bd299_corpus_parity_guards.py.
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
RETIRED = "HAL_CORPUS_PARITY_ENFORCE"
CATALOG_SRC = REPO / "engine_py" / "bytedigger_engine" / "flags_catalog.py"
LEDGER = REPO / "scripts" / "flip_horizon_ledger.json"
WIRING_SRC = REPO / "engine_py" / "bytedigger_engine" / "workflows" / "_baseline_delta.py"


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


# ------------------------------------------------------------- (a) registry

def test_retired_flag_absent_from_catalog():
    assert RETIRED not in _catalog_keys()


def test_retired_flag_absent_from_flip_horizon_ledger():
    assert RETIRED not in json.loads(LEDGER.read_text())


# --------------------------------------------------------- (b) repo scan

def _is_allowed(path: str) -> bool:
    p = Path(path)
    parts = p.parts
    if p.name == "CHANGELOG.md" and len(parts) == 1:
        return True
    if parts[:2] == ("docs", "decisions"):
        return True
    if p.name.startswith("test_") and p.suffix == ".py" and (
        parts[:1] == ("tests",) or parts[:2] == ("engine_py", "tests")
    ):
        return True
    return False


def test_no_tracked_file_mentions_retired_flag_outside_allowlist():
    out = subprocess.run(
        ["git", "ls-files", "-z"], cwd=str(REPO), capture_output=True, text=True,
        timeout=60, check=True,
    ).stdout
    offenders = []
    for rel in filter(None, out.split("\0")):
        if _is_allowed(rel):
            continue
        fp = REPO / rel
        if not fp.is_file():
            continue
        try:
            text = fp.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if RETIRED in text:
            offenders.append(rel)
    assert offenders == [], offenders


# ------------------------------------------------- (c) wiring never reads it

def test_wiring_source_has_no_string_constant_for_retired_flag():
    tree = ast.parse(WIRING_SRC.read_text())
    hits = [n.lineno for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str) and RETIRED in n.value]
    assert hits == [], hits


_HARNESS = textwrap.dedent("""
    import json, sys
    from pathlib import Path
    from bytedigger_engine.workflows._baseline_delta import run_baseline_delta_gate

    RETIRED = "HAL_CORPUS_PARITY_ENFORCE"
    mode = sys.argv[4]  # "poison": raise if asked; "offer": answer True if asked

    class Cfg:
        asked = []
        def gate_enabled(self, name): return True
        def flag(self, name):
            self.asked.append(name)
            if name == RETIRED:
                if mode == "poison":
                    raise AssertionError("wiring read retired flag " + RETIRED)
                return True
            return False
        def path(self, name, default): return Path(sys.argv[1])

    events = []
    def emit(name, payload=None, **kw):
        events.append([name, payload])

    cfg = Cfg()
    ret = run_baseline_delta_gate(sys.argv[2], "pytest", sys.argv[3], "p5", "s", emit, cfg=cfg)
    print(json.dumps({"ret": ret, "events": events, "asked": cfg.asked}))
""")

_BLOCKED_STUB = """
    import json, sys
    print(json.dumps({"verdict": "BLOCKED", "delta_verdict": "PASS", "new_fails": [],
                      "ledgered": [], "baseline_source": "explicit",
                      "blocked_by": ["E_CORPUS_DIVERGENCE"]}))
    sys.exit(5)
"""


def _run_wiring(tmp_path, mode):
    stub = tmp_path / "stub.py"
    stub.write_text(textwrap.dedent(_BLOCKED_STUB))
    harness = tmp_path / "harness.py"
    harness.write_text(_HARNESS)
    results = tmp_path / "r.txt"
    results.write_text("collected 1 item\n")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO / "engine_py")
    proc = subprocess.run(
        [sys.executable, str(harness), str(stub), str(results), str(tmp_path), mode],
        cwd=str(tmp_path), env=env, capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-1500:]
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_wiring_does_not_call_cfg_flag_for_retired_flag(tmp_path):
    data = _run_wiring(tmp_path, "poison")  # AssertionError in harness if read
    assert RETIRED not in data["asked"]
    assert data["ret"]["verdict"] == "BLOCKED"


def test_wiring_never_promotes_parity_to_block_even_if_stub_offers_flag(tmp_path):
    data = _run_wiring(tmp_path, "offer")
    ret = data["ret"]
    assert not ret.get("parity_block"), ret
    assert ret["would_block"] is False, ret
    verdicts = [p for n, p in data["events"] if n == "baseline_delta_gate_verdict"]
    assert verdicts and verdicts[0]["blocked_by"] == ["E_CORPUS_DIVERGENCE"]
