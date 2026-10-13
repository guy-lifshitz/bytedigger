"""bd#299 (Refs #89) regression shield: run_baseline_delta_gate must emit NO
baseline_delta_gate_skipped event when the driver exits 0/4/5 with a valid
JSON last line. Passes today; must keep passing after GREEN.

No sys.path mutation, no conftest import: the wiring runs in a subprocess.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

CLEAN_RESULTS = "=== 3 passed in 0.1s ===\n"

_HARNESS = textwrap.dedent("""
    import json, sys
    from pathlib import Path
    from bytedigger_engine.workflows._baseline_delta import run_baseline_delta_gate

    class Cfg:
        def __init__(self, bin_path): self.bin_path = bin_path
        def gate_enabled(self, name): return True
        def flag(self, name): return False
        def path(self, name, default): return Path(self.bin_path)

    events = []
    def emit(name, payload=None, **kw):
        events.append([name, payload])

    ret = run_baseline_delta_gate(sys.argv[2], "pytest", sys.argv[3], "p5", "s", emit,
                                  cfg=Cfg(sys.argv[1]))
    print(json.dumps({"ret": ret, "events": events}))
""")

_VALID_JSON_STUB = """
    import json, sys
    print(json.dumps({{"verdict": "PASS", "delta_verdict": "PASS", "new_fails": []}}))
    sys.exit({rc})
"""


def _run_wiring(tmp_path, stub_body):
    stub = tmp_path / "stub.py"
    stub.write_text(textwrap.dedent(stub_body))
    harness = tmp_path / "harness.py"
    harness.write_text(_HARNESS)
    results = tmp_path / "r.txt"
    results.write_text(CLEAN_RESULTS)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO / "engine_py")
    proc = subprocess.run(
        [sys.executable, str(harness), str(stub), str(results), str(tmp_path)],
        cwd=str(tmp_path), env=env, capture_output=True, text=True, timeout=120,
    )
    assert proc.returncode == 0, proc.stderr[-1500:]
    data = json.loads(proc.stdout.strip().splitlines()[-1])
    return data["ret"], {n: p for n, p in data["events"]}


def test_s2_valid_json_exit_0_4_5_emit_no_skipped_event(tmp_path):
    for rc in (0, 4, 5):
        sub = tmp_path / f"rc{rc}"
        sub.mkdir()
        ret, ev = _run_wiring(sub, _VALID_JSON_STUB.format(rc=rc))
        assert ret["skipped"] is None, (rc, ret)
        assert "baseline_delta_gate_skipped" not in ev, (rc, ev)
        assert "baseline_delta_gate_verdict" in ev, (rc, ev)
