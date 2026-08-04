"""RED tests — GH1082 v2 Part C: backend registry isolation (§2.4), AC16-AC18.

`llm_subprocess.reset_backends()` (llm_subprocess.py:1993) restores the
built-in backends, but no fixture in `conftest.py` calls it at SETUP time —
only individual test files (e.g. `test_phase_45_spec.py:71`) reset on
TEARDOWN. A backend registered by one test therefore leaks into whichever
test happens to run next in the same pytest session — the exact #1092/#1098
mechanism (§0 of the spec).

v2 gate MAJOR 6: AC16/AC17 no longer depend on intra-file pytest execution
order (which would break under the `-n4` xdist distribution mode in §4.3).
Each is rewritten as a **subprocess** pytest invocation with two explicitly
ordered node-ids, targeting two helper tests defined below in this same
file. The helper tests are no-ops (`pytest.skip`) under normal collection —
guarded on an env var the subprocess sets — so they never affect the outer
suite's pass/fail count or ordering; they only execute for real when the
subprocess explicitly names their node-ids. This exercises the REAL
conftest.py fixture (or its absence, pre-GREEN) and is immune to the outer
distribution mode.

AC18 (gate MAJOR 4) is retargeted at the REAL sibling module
`tests/test_2A6986ED_anthropic_api_backend.py`, also run in a subprocess, to
avoid asserting against a test's own stand-in stub.

conftest.py already inserts the engine_py root onto sys.path at import time
(the canonical §1q seam); plain `import llm_subprocess` relies on that.
"""
from __future__ import annotations

import io
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from bytedigger_engine import llm_subprocess
from bytedigger_engine import telemetry_ctx

_ENGINE_PY_ROOT = Path(__file__).parent.parent

_AC16_HELPER_ENV = "_GH1082_AC16_RUN_HELPERS"
_AC17_HELPER_ENV = "_GH1082_AC17_RUN_HELPERS"


def _fake_stub(**kwargs):
    return llm_subprocess.StepResult(
        status="ok",
        data={"raw_response": "LEAKED-FAKE-AGENT-SDK"},
        duration_ms=1,
        step_name=kwargs.get("step_name", "s"),
        error=None,
        error_code=None,
        recoverable=True,
    )


class _FakeEventLog:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def append(self, event_type: str, payload: dict, run_id: str | None = None) -> None:
        self.events.append((event_type, payload))


def _make_success_proc():
    """Real-Popen stand-in for the claude-subprocess dispatch path, mirroring
    the pattern already used by tests/test_GH1001_backend_default_flip.py."""
    system_init = '{"type":"system","subtype":"init","session_id":"s1"}'
    assistant_delta = (
        '{"type":"assistant","message":{"content":[{"type":"text","text":"Hi"}],'
        '"usage":{"input_tokens":0,"output_tokens":0}}}'
    )
    result_ok = (
        '{"type":"result","subtype":"success","result":"AC17_ANSWER",'
        '"usage":{"input_tokens":1,"output_tokens":1,'
        '"cache_read_input_tokens":0,"cache_creation_input_tokens":0},'
        '"total_cost_usd":0.001,"duration_ms":10}'
    )
    stdout = "".join(line + "\n" for line in (system_init, assistant_delta, result_ok))

    proc = MagicMock()
    proc.pid = 12345
    proc.returncode = 0
    proc.stdout = io.StringIO(stdout)
    proc.stderr = io.StringIO("")
    proc.stdin = MagicMock()
    proc.wait = MagicMock(return_value=0)
    proc.communicate = MagicMock(return_value=(stdout, ""))
    return proc


def _run_ordered_subprocess(*node_ids: str) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env[_AC16_HELPER_ENV] = "1"
    env[_AC17_HELPER_ENV] = "1"
    return subprocess.run(
        [sys.executable, "-m", "pytest", *node_ids, "-p", "no:randomly", "-q"],
        cwd=str(_ENGINE_PY_ROOT),
        env=env,
        capture_output=True,
        text=True,
        timeout=120,
    )


# ─── AC16 helper node-ids (skipped under normal collection) ──────────────────


def test_ac16_helper_a_register_fake_agent_sdk_backend():
    """AC16 helper (setup half): pollute the registry with a fake
    'agent-sdk' backend and leave it registered — no cleanup. Only runs for
    real when the AC16 subprocess harness sets _GH1082_AC16_RUN_HELPERS."""
    if not os.environ.get(_AC16_HELPER_ENV):
        pytest.skip("helper test; only meant to run inside the AC16 subprocess harness")
    llm_subprocess.register_backend(
        "agent-sdk", _fake_stub, manifest_source="test-fake", overwrite=True,
    )
    assert "agent-sdk" in llm_subprocess._KNOWN_BACKENDS


def test_ac16_helper_b_assert_clean_registry_at_setup():
    """AC16 helper (assertion half): the NEXT node-id in the ordered
    subprocess run must observe _KNOWN_BACKENDS reset to exactly the two
    built-in backends AT SETUP — before this test's own body does anything.
    Today there is no setup-time reset in conftest.py, so the fake
    'agent-sdk' registered by the prior node-id leaks in."""
    if not os.environ.get(_AC16_HELPER_ENV):
        pytest.skip("helper test; only meant to run inside the AC16 subprocess harness")
    assert llm_subprocess._KNOWN_BACKENDS == ("claude-subprocess", "claude-in-session"), (
        f"expected clean registry at setup; got {llm_subprocess._KNOWN_BACKENDS!r} "
        "(leaked registration from the prior node-id — conftest lacks a setup-time reset)"
    )


def test_ac16_subprocess_ordered_run_is_order_independent():
    """AC16: a subprocess pytest run with two explicitly ordered node-ids —
    first registers a fake 'agent-sdk', then asserts a clean registry at
    setup — must exit 0. Immune to the outer distribution mode (§4.3, `-n4`)
    because it is a fully separate pytest process. FAILS pre-GREEN: no
    setup-time reset exists in conftest.py, so the second node-id observes
    the leaked registration and its assertion fails."""
    this_file = "tests/" + Path(__file__).name
    node_a = f"{this_file}::test_ac16_helper_a_register_fake_agent_sdk_backend"
    node_b = f"{this_file}::test_ac16_helper_b_assert_clean_registry_at_setup"

    result = _run_ordered_subprocess(node_a, node_b)

    assert result.returncode == 0, (
        "AC16: ordered subprocess run must exit 0 (clean setup-time registry "
        f"reset); rc={result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


# ─── AC17 helper node-ids (skipped under normal collection) ──────────────────


def test_ac17_helper_a_register_fake_agent_sdk_backend():
    """AC17 helper (setup half): same pollution as AC16's helper-a, kept
    separate so AC17's subprocess run does not depend on AC16's outcome."""
    if not os.environ.get(_AC17_HELPER_ENV):
        pytest.skip("helper test; only meant to run inside the AC17 subprocess harness")
    llm_subprocess.register_backend(
        "agent-sdk", _fake_stub, manifest_source="test-fake", overwrite=True,
    )
    assert "agent-sdk" in llm_subprocess._KNOWN_BACKENDS


def test_ac17_helper_b_default_path_dispatches_claude_subprocess(monkeypatch):
    """AC17 helper (assertion half): with the fake 'agent-sdk' from the prior
    node-id (if it leaked), a test_phase_45_spec-shaped resolution (env
    HAL_RUNNER_BACKEND deleted -> source=="default") must still resolve to
    'claude-subprocess' — NOT dispatch into whatever backend happens to be
    registered under 'agent-sdk' from the immediately-prior node-id (the
    #1092/#1098 mechanism)."""
    if not os.environ.get(_AC17_HELPER_ENV):
        pytest.skip("helper test; only meant to run inside the AC17 subprocess harness")

    monkeypatch.delenv("HAL_RUNNER_BACKEND", raising=False)  # test_phase_45_spec.py:67 shape (pre-fix)

    log = _FakeEventLog()
    telemetry_ctx.set_current_run(event_log=log, run_id="ac17-run", step_name="s", phase="p")

    from unittest.mock import patch

    try:
        with patch("bytedigger_engine.llm_subprocess.subprocess.Popen", return_value=_make_success_proc()):
            llm_subprocess.invoke_llm_subprocess(
                prompt="x", model="sonnet", timeout_sec=5, step_name="ac17-step", backend=None,
            )
    finally:
        telemetry_ctx.clear_current_run()

    resolved = [p for (t, p) in log.events if t == "runner_backend_resolved"]
    assert resolved, f"expected a runner_backend_resolved event; got {log.events!r}"
    assert resolved[0].get("backend") == "claude-subprocess", (
        "AC17: default-path resolution must fall back to 'claude-subprocess'; "
        f"a leaked 'agent-sdk' registration from the prior node-id won instead: {resolved[0]!r}"
    )


def test_ac17_subprocess_ordered_run_default_path_stays_claude_subprocess():
    """AC17: the same subprocess-ordering shape as AC16, but asserting the
    default-path DISPATCH target (not just registry membership). FAILS
    pre-GREEN for the same reason as AC16 — no setup-time reset means the
    leaked fake 'agent-sdk' wins the default-path dispatch instead of the
    correct claude-subprocess fallback."""
    this_file = "tests/" + Path(__file__).name
    node_a = f"{this_file}::test_ac17_helper_a_register_fake_agent_sdk_backend"
    node_b = f"{this_file}::test_ac17_helper_b_default_path_dispatches_claude_subprocess"

    result = _run_ordered_subprocess(node_a, node_b)

    assert result.returncode == 0, (
        "AC17: ordered subprocess run must exit 0 (default-path dispatch stays "
        f"claude-subprocess); rc={result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


# ─── AC18 — regression against the REAL sibling module ──────────────────────


def test_ac18_real_sibling_reference_backend_module_passes_under_new_conftest():
    """AC18 regression: the REAL sibling module
    tests/test_2A6986ED_anthropic_api_backend.py — which registers its own
    reference backend in-body, the idiom every reference-backend test uses —
    must pass in full when run (in a subprocess, under the real conftest.py)
    both pre- and post-GREEN. Does not register a stand-in stub of its own."""
    result = subprocess.run(
        [sys.executable, "-m", "pytest",
         "tests/test_2A6986ED_anthropic_api_backend.py", "-p", "no:randomly", "-q"],
        cwd=str(_ENGINE_PY_ROOT),
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, (
        "AC18: tests/test_2A6986ED_anthropic_api_backend.py must pass in full; "
        f"rc={result.returncode}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
