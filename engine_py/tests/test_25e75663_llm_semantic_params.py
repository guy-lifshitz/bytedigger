"""RED tests for 25e75663: LLM backend semantic-params (`command:list[str]` -> `model:str`).

Spec: SHARED/memory/Decisions/2026-06-20_25e75663_llm_semantic_params_spec.md

FAIL today (pre-GREEN, current `command=`-based code):
  AC1  - _build_claude_argv does not exist yet (AttributeError in body)
  AC2  - invoke_llm_subprocess has `command` param, not `model`
  AC3  - LLMBackend.__call__ has `command` param, not `model`
  AC4  - _invoke_subprocess receives command=, not model=; no _build_claude_argv
  AC5  - (same path as AC4; auto-injection behavior checked separately)
  AC6  - (same path as AC4; allowed_tools injection checked separately)
  AC7  - _is_opus_class_model takes list[str], not str; `("sonnet")` and `(None)` behavior differs
  AC8  - _resolve_model does not exist yet (AttributeError in body)
  AC9  - _default_*_model() functions do not exist (AttributeError); current builders return list[str]
  AC10 - '"claude", "-p"' still appears in workflows/*.py builders (not yet removed)
  AC11 - _invoke_in_session uses command= not model=; model not in runner_request_built payload directly
  AC12 - dispatch passes command= not model= to spy backend
  AC13 - command param still present in invoke_llm_subprocess (overlap with AC2; see below)

PASS today (correctness guards that must survive GREEN):
  (none — all ACs target new behavior)

§1q / D1CF5FDF compliance:
  _build_claude_argv, _resolve_model, _default_*_model (phase builders) are NOT
  imported at module top level. They do NOT exist yet, so any top-level import
  would cause ImportError at collection time → ~30min hang under red_runtime.
  Every not-yet-existing symbol is accessed INSIDE test-function bodies via
  `getattr(mod, "name", None)` and asserted there.

  Symbols that DO exist and are safe to import at module level:
    llm_subprocess (the module), invoke_llm_subprocess, LLMBackend,
    register_backend, reset_backends, telemetry_ctx, StepResult.
  Phase modules (phase_1_discovery, phase_5_implement, phase_45_spec) exist
  at module level but their new symbols (_default_model, _default_green_model,
  _default_spec_model) are accessed via getattr inside test bodies.

§1l / 7AD3D393 stub-passability: UUTs
  (_build_claude_argv, invoke_llm_subprocess, _resolve_model, _is_opus_class_model,
  _default_*_model builders) are NEVER patched. Only spawn primitives
  (subprocess.Popen) and the in-session servicer (file-protocol artifacts)
  are mocked — those are infra, not the UUT.

§1i (8CA8D54C): _BACKENDS is a module-level singleton; autouse fixture calls
  reset_backends() in teardown. §1i cited — no racy time-dependent resources here.
"""
from __future__ import annotations

import inspect
import io
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# conftest.py injects engine_py root + workflows dir into sys.path at import time
# (§1q singleton — this file must NOT manipulate sys.path itself).
from bytedigger_engine import llm_subprocess
from bytedigger_engine.llm_subprocess import (
    invoke_llm_subprocess,
    LLMBackend,
    register_backend,
    reset_backends,
)
from bytedigger_engine import telemetry_ctx
from bytedigger_engine.contracts import StepResult


# ---------------------------------------------------------------------------
# §1i autouse teardown: restore _BACKENDS singleton after every test
# (mirrors test_register_backend_A60F1FE3 pattern)
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_backends_and_telemetry(monkeypatch):
    """§1i: restore _BACKENDS singleton + clear telemetry context between tests.

    Also neutralise emit_resolver_resolved to prevent disk writes into
    SHARED/state during invoke_llm_subprocess calls.
    """
    monkeypatch.setattr(llm_subprocess, "emit_resolver_resolved", lambda *a, **kw: None)
    telemetry_ctx.clear_current_run()
    yield
    telemetry_ctx.clear_current_run()
    reset_backends()


# ---------------------------------------------------------------------------
# Minimal Popen mock (mirrors test_llm_subprocess_allowed_tools.py pattern)
# ---------------------------------------------------------------------------

_RESULT_EVENT = (
    '{"type":"result","subtype":"success",'
    '"result":"OK",'
    '"usage":{"input_tokens":1,"output_tokens":1,'
    '"cache_read_input_tokens":0,"cache_creation_input_tokens":0},'
    '"total_cost_usd":0.001,"duration_ms":100}\n'
)


def _make_popen_side_effect():
    """Return (captured_argv_list, side_effect_fn) for patching subprocess.Popen."""
    captured: list[list[str]] = []

    def _side_effect(argv, **kwargs):
        captured.append(list(argv))
        proc = MagicMock()
        proc.pid = 99999
        proc.returncode = 0
        proc.stdout = io.StringIO(_RESULT_EVENT)
        proc.stderr = io.StringIO("")
        proc.stdin = MagicMock()
        proc.wait = MagicMock(return_value=0)
        proc.communicate = MagicMock(return_value=(_RESULT_EVENT, ""))
        return proc

    return captured, _side_effect


# ---------------------------------------------------------------------------
# Spy backend (mirrors test_register_backend_A60F1FE3._SpyBackend)
# ---------------------------------------------------------------------------

class _SpyBackend:
    """LLMBackend-compatible callable that records its kwargs."""

    def __init__(self, name: str = "spy-25e75663"):
        self._name = name
        self.calls: list[dict] = []

    def __call__(self, **kwargs) -> StepResult:
        self.calls.append(dict(kwargs))
        return StepResult(
            status="ok",
            data={"spy": self._name, "worker_written_paths": [], "manifest_source": "harness_tool_record"},
            duration_ms=0,
            step_name=kwargs.get("step_name", "spy"),
            error=None,
            error_code=None,
            recoverable=True,
        )


# ---------------------------------------------------------------------------
# AC1: _build_claude_argv("opus") == ["claude", "-p", "--model", "opus"]
# ---------------------------------------------------------------------------

def test_ac1_build_claude_argv_returns_correct_list():
    """AC1: _build_claude_argv('opus') returns ['claude','-p','--model','opus'].

    FAILS today: _build_claude_argv does not exist in llm_subprocess yet.
    After GREEN: the helper exists and returns the expected argv list.
    """
    fn = getattr(llm_subprocess, "_build_claude_argv", None)
    assert fn is not None, (
        "_build_claude_argv not found on llm_subprocess — not yet implemented (25e75663 §2.1). "
        "GREEN must add this helper."
    )
    result = fn("opus")
    assert result == ["claude", "-p", "--model", "opus"], (
        f"_build_claude_argv('opus') expected ['claude','-p','--model','opus'], "
        f"got {result!r}"
    )
    result2 = fn("claude-sonnet-4-5")
    assert result2 == ["claude", "-p", "--model", "claude-sonnet-4-5"], (
        f"_build_claude_argv('claude-sonnet-4-5') expected correct list, got {result2!r}"
    )


# ---------------------------------------------------------------------------
# AC2: invoke_llm_subprocess has `model` param, not `command`; command= raises TypeError
# ---------------------------------------------------------------------------

def test_ac2_invoke_llm_subprocess_has_model_not_command():
    """AC2: invoke_llm_subprocess signature has 'model' kwarg; 'command' raises TypeError.

    FAILS today: current signature has `command: list[str]`, not `model: str`.
    After GREEN: `model` present, `command` absent — call with command= raises TypeError.
    """
    sig = inspect.signature(invoke_llm_subprocess)
    params = set(sig.parameters)

    assert "model" in params, (
        f"invoke_llm_subprocess must have 'model' parameter after 25e75663 GREEN; "
        f"current params: {sorted(params)!r}"
    )
    assert "command" not in params, (
        f"invoke_llm_subprocess must NOT have 'command' parameter after 25e75663 GREEN; "
        f"found 'command' in params: {sorted(params)!r}"
    )

    # Calling with command= must raise TypeError (pure flag-day, no compat alias)
    with pytest.raises(TypeError):
        invoke_llm_subprocess(
            prompt="test",
            command=["claude", "-p"],  # old-style — must TypeError after GREEN
            timeout_sec=5,
            step_name="test_ac2",
        )


# ---------------------------------------------------------------------------
# AC3: LLMBackend.__call__ signature has `model: str`, no `command`
# ---------------------------------------------------------------------------

def test_ac3_llmbackend_call_has_model_not_command():
    """AC3: LLMBackend.__call__ Protocol signature has 'model' param, not 'command'.

    FAILS today: LLMBackend.__call__ has `command: list[str]` in its signature.
    After GREEN: 'model' present, 'command' absent.
    """
    sig = inspect.signature(LLMBackend.__call__)
    params = set(sig.parameters)

    assert "model" in params, (
        f"LLMBackend.__call__ must have 'model' parameter after 25e75663 GREEN; "
        f"current params: {sorted(params)!r}"
    )
    assert "command" not in params, (
        f"LLMBackend.__call__ must NOT have 'command' parameter after 25e75663 GREEN; "
        f"found 'command' in: {sorted(params)!r}"
    )


# ---------------------------------------------------------------------------
# AC4: claude-subprocess backend with model="sonnet" spawns argv containing
#      ["claude", "-p", "--model", "sonnet"]
# ---------------------------------------------------------------------------

def test_ac4_subprocess_backend_builds_argv_from_model(monkeypatch):
    """AC4: With model='sonnet', the spawned argv starts with claude -p --model sonnet.

    FAILS today: invoke_llm_subprocess has no `model` param (TypeError on call).
    After GREEN: _invoke_subprocess calls _build_claude_argv(model) and spawns
    an argv containing ['claude', '-p', '--model', 'sonnet'].

    Mocks subprocess.Popen (infra, not the UUT) to capture spawned argv.
    """
    monkeypatch.setenv("HAL_RUNNER_BACKEND", "claude-subprocess")
    captured, side = _make_popen_side_effect()

    with patch("bytedigger_engine.llm_subprocess.subprocess.Popen", side_effect=side):
        invoke_llm_subprocess(
            prompt="test prompt",
            model="sonnet",
            timeout_sec=10,
            step_name="test_ac4",
        )

    assert len(captured) == 1, f"Expected 1 Popen call, got {len(captured)}"
    argv = captured[0]

    # The argv head must contain the four tokens produced by _build_claude_argv
    assert "claude" in argv, f"'claude' not in spawned argv={argv!r}"
    assert "-p" in argv, f"'-p' not in spawned argv={argv!r}"
    assert "--model" in argv, f"'--model' not in spawned argv={argv!r}"

    model_idx = argv.index("--model")
    assert model_idx + 1 < len(argv), f"'--model' has no following value in argv={argv!r}"
    assert argv[model_idx + 1] == "sonnet", (
        f"argv[--model+1] must be 'sonnet', got {argv[model_idx + 1]!r}\nargv={argv!r}"
    )

    # Confirm the head is exactly ["claude", "-p", "--model", "sonnet"]
    assert argv[:4] == ["claude", "-p", "--model", "sonnet"], (
        f"argv head must be ['claude','-p','--model','sonnet'], got {argv[:4]!r}"
    )


# ---------------------------------------------------------------------------
# AC5: spawned argv still contains --output-format stream-json --verbose
# ---------------------------------------------------------------------------

def test_ac5_subprocess_backend_still_injects_output_format(monkeypatch):
    """AC5: auto-injected --output-format stream-json --verbose still present post-refactor.

    FAILS today: invoke_llm_subprocess has no `model` param.
    After GREEN: argv must contain these tokens (no regression on 23680DDA injection).
    """
    monkeypatch.setenv("HAL_RUNNER_BACKEND", "claude-subprocess")
    captured, side = _make_popen_side_effect()

    with patch("bytedigger_engine.llm_subprocess.subprocess.Popen", side_effect=side):
        invoke_llm_subprocess(
            prompt="test prompt",
            model="claude-sonnet-4-5",
            timeout_sec=10,
            step_name="test_ac5",
        )

    assert len(captured) == 1
    argv = captured[0]

    assert "--output-format" in argv, (
        f"--output-format not in spawned argv (23680DDA regression).\nargv={argv!r}"
    )
    fmt_idx = argv.index("--output-format")
    assert fmt_idx + 1 < len(argv) and argv[fmt_idx + 1] == "stream-json", (
        f"--output-format value must be 'stream-json', got argv={argv!r}"
    )
    assert "--verbose" in argv, (
        f"--verbose not in spawned argv (23680DDA regression).\nargv={argv!r}"
    )


# ---------------------------------------------------------------------------
# AC6: allowed_tools=["Read"] still injected as --allowed-tools
# ---------------------------------------------------------------------------

def test_ac6_allowed_tools_still_injected(monkeypatch):
    """AC6: allowed_tools=['Read'] still injects --allowed-tools 'Read' into argv.

    FAILS today: invoke_llm_subprocess has no `model` param.
    After GREEN: 845F2C2C injection still works via model= path.
    """
    monkeypatch.setenv("HAL_RUNNER_BACKEND", "claude-subprocess")
    captured, side = _make_popen_side_effect()

    with patch("bytedigger_engine.llm_subprocess.subprocess.Popen", side_effect=side):
        invoke_llm_subprocess(
            prompt="test prompt",
            model="claude-haiku-4",
            timeout_sec=10,
            step_name="test_ac6",
            allowed_tools=["Read"],
        )

    assert len(captured) == 1
    argv = captured[0]

    assert "--allowed-tools" in argv, (
        f"--allowed-tools not injected when allowed_tools=['Read'].\nargv={argv!r}"
    )
    at_idx = argv.index("--allowed-tools")
    assert at_idx + 1 < len(argv) and argv[at_idx + 1] == "Read", (
        f"--allowed-tools value must be 'Read', got argv={argv!r}"
    )


# ---------------------------------------------------------------------------
# AC7: _is_opus_class_model(model: str | None) — new string-based signature
# ---------------------------------------------------------------------------

def test_ac7_is_opus_class_model_takes_str():
    """AC7: _is_opus_class_model(str | None) — True for opus, False for sonnet/None.

    FAILS today: current _is_opus_class_model takes list[str] | None;
    _is_opus_class_model("claude-opus-4-8") would return False (no argv parsing),
    and _is_opus_class_model(None) behavior differs from new spec.

    After GREEN: new string-based implementation:
      "claude-opus-4-8" -> True
      "sonnet" -> False
      None -> False
    """
    import importlib
    phase_45 = importlib.import_module("bytedigger_engine.workflows.phase_45_spec")
    fn = getattr(phase_45, "_is_opus_class_model", None)
    assert fn is not None, (
        "_is_opus_class_model not found on phase_45_spec"
    )

    # True case: string containing "opus"
    result_true = fn("claude-opus-4-8")
    assert result_true is True, (
        f"_is_opus_class_model('claude-opus-4-8') must be True; got {result_true!r}"
    )

    # False case: non-opus model string
    result_sonnet = fn("sonnet")
    assert result_sonnet is False, (
        f"_is_opus_class_model('sonnet') must be False; got {result_sonnet!r}"
    )

    # False case: None
    result_none = fn(None)
    assert result_none is False, (
        f"_is_opus_class_model(None) must be False; got {result_none!r}"
    )


# ---------------------------------------------------------------------------
# AC8: _resolve_model(cfg, key, default) -> str
# ---------------------------------------------------------------------------

def test_ac8_resolve_model_returns_correct_str():
    """AC8: _resolve_model({"green_model":"haiku"}, "green_model", "opus") == "haiku";
    absent key -> "opus".

    FAILS today: _resolve_model does not exist (currently _resolve_command returns list[str]).
    After GREEN: returns a str, not a list.
    """
    # Try phase_workflows_common first (the common module), then llm_subprocess
    import importlib
    common = importlib.import_module("bytedigger_engine.workflows.phase_workflows_common")
    fn = getattr(common, "_resolve_model", None)
    if fn is None:
        # Fallback: check llm_subprocess if common doesn't have it
        fn = getattr(llm_subprocess, "_resolve_model", None)
    assert fn is not None, (
        "_resolve_model not found on phase_workflows_common or llm_subprocess — "
        "not yet implemented (25e75663 §2.8)"
    )

    # Key present: return cfg value
    result_present = fn({"green_model": "haiku"}, "green_model", "opus")
    assert result_present == "haiku", (
        f"_resolve_model(cfg, 'green_model', 'opus') with cfg['green_model']='haiku' "
        f"must return 'haiku'; got {result_present!r}"
    )
    assert isinstance(result_present, str), (
        f"_resolve_model must return str, got {type(result_present)!r}"
    )

    # Key absent: return default
    result_absent = fn({}, "green_model", "opus")
    assert result_absent == "opus", (
        f"_resolve_model({{}}, 'green_model', 'opus') with absent key "
        f"must return default 'opus'; got {result_absent!r}"
    )


# ---------------------------------------------------------------------------
# AC9: per-phase _default_*_model() returns str equal to their getter
# ---------------------------------------------------------------------------

def test_ac9_default_model_builders_return_str():
    """AC9: representative _default_*_model() functions return a str equal to getter.

    Tests three representative builders:
      - phase_1_discovery._default_model() == get_claude_discovery()
      - phase_5_implement._default_green_model() == get_claude_primary()
      - phase_45_spec._default_spec_model() == get_claude_spec_writer()

    FAILS today: these functions don't exist yet (current builders are named
    _default_llm_command / _default_green_llm_command / _default_spec_llm_command
    and return list[str], not str).
    After GREEN: each returns a model string from its getter.
    """
    import importlib
    from bytedigger_engine.lib.model_config import get_claude_discovery, get_claude_primary, get_claude_spec_writer

    # -- phase_1_discovery._default_model()
    p1 = importlib.import_module("bytedigger_engine.workflows.phase_1_discovery")
    fn_discovery = getattr(p1, "_default_model", None)
    assert fn_discovery is not None, (
        "phase_1_discovery._default_model not found — not yet renamed from "
        "_default_llm_command (25e75663 §2.7)"
    )
    result_discovery = fn_discovery()
    assert isinstance(result_discovery, str), (
        f"phase_1_discovery._default_model() must return str, got {type(result_discovery)!r}: "
        f"{result_discovery!r}"
    )
    assert result_discovery == get_claude_discovery(), (
        f"phase_1_discovery._default_model() must equal get_claude_discovery()="
        f"{get_claude_discovery()!r}; got {result_discovery!r}"
    )

    # -- phase_5_implement._default_green_model()
    p5 = importlib.import_module("bytedigger_engine.workflows.phase_5_implement")
    fn_green = getattr(p5, "_default_green_model", None)
    assert fn_green is not None, (
        "phase_5_implement._default_green_model not found — not yet renamed from "
        "_default_green_llm_command (25e75663 §2.7)"
    )
    result_green = fn_green()
    assert isinstance(result_green, str), (
        f"phase_5_implement._default_green_model() must return str, got {type(result_green)!r}"
    )
    assert result_green == get_claude_primary(), (
        f"phase_5_implement._default_green_model() must equal get_claude_primary()="
        f"{get_claude_primary()!r}; got {result_green!r}"
    )

    # -- phase_45_spec._default_spec_model()
    p45 = importlib.import_module("bytedigger_engine.workflows.phase_45_spec")
    fn_spec = getattr(p45, "_default_spec_model", None)
    assert fn_spec is not None, (
        "phase_45_spec._default_spec_model not found — not yet renamed from "
        "_default_spec_llm_command (25e75663 §2.7)"
    )
    result_spec = fn_spec()
    assert isinstance(result_spec, str), (
        f"phase_45_spec._default_spec_model() must return str, got {type(result_spec)!r}"
    )
    assert result_spec == get_claude_spec_writer(), (
        f"phase_45_spec._default_spec_model() must equal get_claude_spec_writer()="
        f"{get_claude_spec_writer()!r}; got {result_spec!r}"
    )


# ---------------------------------------------------------------------------
# AC10: no residual '"claude", "-p"' literal in workflows/*.py builders/resolvers
# ---------------------------------------------------------------------------

def test_ac10_no_residual_claude_argv_literal_in_workflows():
    """AC10: deterministic source grep — no '"claude", "-p"' literal in workflows/*.py.

    FAILS today: all _default_*_llm_command() builders construct exactly this literal.
    After GREEN: builders are renamed + rewritten to return model strings; the
    literal only lives inside _build_claude_argv in llm_subprocess.py.

    Implementation: reads workflows/*.py files and asserts the substring count is 0.
    This is a deterministic gate over production source (spec §3 AC10 — §1l).
    """
    workflows_dir = Path(__file__).parent.parent / "bytedigger_engine" / "workflows"
    assert workflows_dir.is_dir(), f"workflows dir not found at {workflows_dir}"

    target_literal = '"claude", "-p"'
    occurrences: list[tuple[str, int, str]] = []  # (filename, lineno, line)

    for py_file in sorted(workflows_dir.glob("*.py")):
        content = py_file.read_text(encoding="utf-8")
        for lineno, line in enumerate(content.splitlines(), start=1):
            if target_literal in line:
                occurrences.append((py_file.name, lineno, line.strip()))

    assert len(occurrences) == 0, (
        f"Found {len(occurrences)} residual '{target_literal}' literal(s) in "
        f"workflows/*.py — these must be removed by 25e75663 GREEN:\n"
        + "\n".join(f"  {fn}:{ln}: {src}" for fn, ln, src in occurrences)
    )


# ---------------------------------------------------------------------------
# AC11: claude-in-session backend receives + uses `model` (telemetry model field)
# ---------------------------------------------------------------------------

def test_ac11_in_session_backend_uses_model(monkeypatch, tmp_path):
    """AC11: claude-in-session backend receives model= and propagates it to telemetry.

    Strategy: force in-session via env pin (§1i deterministic pre-stage),
    set up a RunContext with a fake event log, provide a pre-written result
    artifact so the poll loop completes without real I/O, then assert the
    runner_request_built event carries the correct model value.

    FAILS today: invoke_llm_subprocess has `command=` not `model=`; calling
    with model= raises TypeError before any in-session code runs.
    After GREEN: model propagates into the runner_request_built telemetry event.
    """
    monkeypatch.setenv("HAL_RUNNER_BACKEND", "claude-in-session")

    # Set up fake event log (mirrors test_CF2EE8ED pattern)
    class _FakeEventLog:
        def __init__(self):
            self.events: list[tuple[str, dict, str]] = []
        def append(self, event_type, payload, run_id=None):
            self.events.append((event_type, dict(payload), run_id or ""))

    log = _FakeEventLog()
    telemetry_ctx.set_current_run(
        event_log=log, run_id="ac11-run", step_name="test_ac11", phase="test"
    )

    # Pre-stage: set request dir + make it exist (in-session needs it for atomic write)
    request_dir = str(tmp_path / "requests")
    os.makedirs(request_dir, exist_ok=True)
    monkeypatch.setenv("HAL_RUNNER_REQUEST_DIR", request_dir)

    # Neutralise atomic write (no real filesystem dependency for this test)
    monkeypatch.setattr(llm_subprocess, "_atomic_write_json", lambda path, payload: None)

    # Patch _read_result_artifact to return a success result immediately (no real poll wait)
    def _instant_result(result_path, nonce):
        return {
            "request_nonce": nonce,
            "status": "ok",
            "raw_response": "AC11 OK",
            "response_bytes": 8,
            "tokens_in": 1,
            "tokens_out": 1,
            "duration_ms": 10,
            "worker_written_paths": [],
            "manifest_source": "orchestrator_observed",
            "model": "claude-haiku-4-test",
        }

    monkeypatch.setattr(llm_subprocess, "_read_result_artifact", _instant_result)

    invoke_llm_subprocess(
        prompt="test in-session model propagation",
        model="claude-haiku-4-test",
        timeout_sec=5,
        step_name="test_ac11",
    )

    # Assert: runner_request_built event was emitted with the correct model
    event_types = [e[0] for e in log.events]
    assert "runner_request_built" in event_types, (
        f"runner_request_built event not emitted; got event_types={event_types!r}. "
        "Pre-GREEN FAIL: invoke_llm_subprocess has command= not model=."
    )

    request_built_events = [e for e in log.events if e[0] == "runner_request_built"]
    payload = request_built_events[0][1]
    assert "model" in payload, (
        f"runner_request_built payload must contain 'model' key; got {payload!r}"
    )
    assert payload["model"] == "claude-haiku-4-test", (
        f"runner_request_built payload['model'] must be 'claude-haiku-4-test'; "
        f"got {payload['model']!r}"
    )


# ---------------------------------------------------------------------------
# AC12: dispatch passes `model=` to the resolved backend (spy backend)
# ---------------------------------------------------------------------------

def test_ac12_dispatch_passes_model_kwarg_to_spy():
    """AC12: dispatch passes model= kwarg to registered backend; command= absent.

    Strategy: register a spy backend, invoke with model=, assert spy recorded
    `model` kwarg and NOT `command` kwarg.

    FAILS today: invoke_llm_subprocess has `command=` not `model=`, so calling
    with model= raises TypeError; and dispatch passes command= to backends.
    After GREEN: spy.calls[0] has 'model' key, no 'command' key.
    """
    spy = _SpyBackend("spy-ac12")
    register_backend(
        "test-spy-25e75663",
        spy,
        manifest_source="harness_tool_record",
        overwrite=True,
    )

    invoke_llm_subprocess(
        prompt="dispatch model test",
        model="claude-sonnet-4-5-spy",
        timeout_sec=5,
        step_name="test_ac12",
        backend="test-spy-25e75663",
        idle_timeout_sec=0,
        straggler_cfg=None,
    )

    assert len(spy.calls) == 1, (
        f"Spy backend must be called exactly once; got {len(spy.calls)} calls"
    )
    kwargs_seen = spy.calls[0]

    assert "model" in kwargs_seen, (
        f"Dispatch must pass 'model' kwarg to backend; "
        f"kwargs seen: {sorted(kwargs_seen.keys())!r}"
    )
    assert kwargs_seen["model"] == "claude-sonnet-4-5-spy", (
        f"Dispatched model must be 'claude-sonnet-4-5-spy'; "
        f"got {kwargs_seen.get('model')!r}"
    )
    assert "command" not in kwargs_seen, (
        f"Dispatch must NOT pass 'command' kwarg to backend after 25e75663; "
        f"found 'command' in kwargs: {sorted(kwargs_seen.keys())!r}"
    )


# ---------------------------------------------------------------------------
# AC13: no `command` kwarg leaks through dispatch to spy backend
#        (lenient: asserts dispatch passes model= and not command=, overlapping AC12)
# ---------------------------------------------------------------------------

def test_ac13_no_command_kwarg_in_dispatch_to_any_backend():
    """AC13: `command` kwarg must not appear in any dispatch call to a backend.

    Lenient form per spec: the real assertion is that `command` is not a
    parameter of invoke_llm_subprocess (already AC2). This test adds the
    additional runtime check: a spy registered for the default backend also
    never receives `command=`.

    FAILS today: current dispatch at L937 passes command=command to backend.
    After GREEN: dispatch passes model=model; command= gone from dispatch path.
    """
    spy = _SpyBackend("spy-ac13")
    # Register as the claude-subprocess backend (overwrite default to capture dispatch)
    register_backend(
        "claude-subprocess",
        spy,
        manifest_source="harness_tool_record",
        overwrite=True,
    )

    invoke_llm_subprocess(
        prompt="ac13 dispatch test",
        model="opus",
        timeout_sec=5,
        step_name="test_ac13",
        idle_timeout_sec=0,
        straggler_cfg=None,
    )

    assert len(spy.calls) >= 1, (
        "Spy backend (replacing claude-subprocess) was never called; "
        "check dispatch path."
    )
    kwargs_seen = spy.calls[0]

    assert "command" not in kwargs_seen, (
        f"AC13: 'command' kwarg must NOT be dispatched to backends after 25e75663; "
        f"found 'command' in spy kwargs: {sorted(kwargs_seen.keys())!r}. "
        f"Pre-GREEN FAIL: dispatch currently passes command=command at L937."
    )
    assert "model" in kwargs_seen, (
        f"AC13: 'model' kwarg must be dispatched to backends; "
        f"kwargs seen: {sorted(kwargs_seen.keys())!r}"
    )
