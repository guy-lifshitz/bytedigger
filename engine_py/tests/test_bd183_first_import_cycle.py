"""RED tests for bd#183 -- first-import circular import in lib.recoverable_gate.

In a fresh interpreter, `import bytedigger_engine.lib.recoverable_gate` raises
ImportError (cannot import name 'RecoverableGateMixin' from partially
initialised module): recoverable_gate imports workflows._recoverable_policy at
module top, which runs workflows/__init__ -> phase_45_spec -> back into
recoverable_gate. The suite stays green only because an earlier test imports
`bytedigger_engine.workflows` first, so these tests run in child interpreters.

Spec: bd#183 FROZEN rev 2 (gate r1 findings folded in).

AC1 (behavioural): fresh-interpreter import of lib.recoverable_gate succeeds
     AND, in the same child, RecoverableGateMixin.gated_step_result(...) returns
     a retry StepResult (status "error", recoverable True, retry_from_step 0,
     gate_attempts {"green_lint": 1}). This kills a try/except-fallback fix
     that loads the module but breaks the call.
AC2: class guard -- every module found by pkgutil.walk_packages imports
     successfully as the FIRST engine import (one child process, purging all
     bytedigger_engine* keys from sys.modules before each import). Sweep must
     cover > 100 modules.

Hermetic: no network, no API keys. The child cwd is derived from the location
of the importable package, so this also works against an installed wheel.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

TIMEOUT_S = 90  # measured sweep ~0.7 s; stays below CI's 120 s pytest --timeout


def _package_parent_dir() -> str:
    spec = importlib.util.find_spec("bytedigger_engine")
    assert spec is not None and spec.origin, "bytedigger_engine is not importable"
    return str(Path(spec.origin).resolve().parent.parent)


def _run_child(code: str) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            [sys.executable, "-c", code],
            cwd=_package_parent_dir(),
            env=dict(os.environ),
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as exc:
        def _txt(v):
            if v is None:
                return ""
            if isinstance(v, bytes):
                return v.decode("utf-8", errors="replace")
            return v

        raise AssertionError(
            f"child interpreter timed out after {TIMEOUT_S}s\n"
            f"STDOUT:\n{_txt(exc.stdout)}\nSTDERR:\n{_txt(exc.stderr)}"
        ) from exc


_AC1_CHILD = """
import bytedigger_engine.lib.recoverable_gate as rg
import json
import sys

sr =rg.RecoverableGateMixin.gated_step_result(
    build_class="SIMPLE",
    gate="green_lint",
    cycle=1,
    retry_from_step_idx=0,
    error_code="E_X",
    error_msg="m",
    step_name="s",
    forwarded_data={},
)
print("RESULT " + json.dumps({
    "status": sr.status,
    "recoverable": sr.recoverable,
    "retry_from_step": sr.data.get("retry_from_step"),
    "gate_attempts": sr.data.get("gate_attempts"),
    "policy_module_loaded": "bytedigger_engine.workflows._recoverable_policy" in sys.modules,
}))
"""

_AC2_CHILD = r"""
import importlib
import pkgutil
import sys
import traceback

import bytedigger_engine

walk_errors = []


def _onerror(name):
    walk_errors.append(name)


names = ["bytedigger_engine"]
names += [
    m.name
    for m in pkgutil.walk_packages(
        bytedigger_engine.__path__, "bytedigger_engine.", onerror=_onerror
    )
]
names = sorted(set(names) | set(walk_errors))
print("COUNT %d" % len(names))
if len(names) <= 100:
    print("FAIL <enumeration>: only %d modules found (need > 100)" % len(names))
    sys.exit(2)

failed = 0
for name in names:
    for key in [k for k in sys.modules if k == "bytedigger_engine" or k.startswith("bytedigger_engine.")]:
        del sys.modules[key]
    importlib.invalidate_caches()
    try:
        importlib.import_module(name)
    except BaseException as exc:  # noqa: BLE001 - collect every failure
        failed += 1
        last = traceback.format_exception_only(type(exc), exc)[-1].strip()
        print("FAIL %s: %s" % (name, last))
sys.exit(1 if failed else 0)
"""


def test_ac1_recoverable_gate_importable_as_first_engine_import():
    proc = _run_child(_AC1_CHILD)
    msg = f"rc={proc.returncode}\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    assert proc.returncode == 0, "child failed (import or call): " + msg
    lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("RESULT ")]
    assert len(lines) == 1, "no RESULT line from child: " + msg
    res = json.loads(lines[0][len("RESULT "):])
    assert res["status"] == "error", f"status != 'error': {res}"
    assert res["recoverable"] is True, f"recoverable is not True: {res}"
    assert res["retry_from_step"] == 0, f"retry_from_step != 0: {res}"
    assert res["gate_attempts"] == {"green_lint": 1}, f"gate_attempts wrong: {res}"
    assert res["policy_module_loaded"] is True, (
        f"the real workflows._recoverable_policy module must be used (no fallback): {res}"
    )


def test_ac2_every_engine_module_imports_as_first_engine_import():
    proc = _run_child(_AC2_CHILD)
    failures = [ln for ln in proc.stdout.splitlines() if ln.startswith("FAIL ")]
    counts = [ln for ln in proc.stdout.splitlines() if ln.startswith("COUNT ")]
    msg = (
        f"rc={proc.returncode}\nfailures={failures}\n"
        f"STDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    )
    assert counts and int(counts[0].split()[1]) > 100, msg
    assert failures == [], msg
    assert proc.returncode == 0, msg
