"""RED tests for 68E964FB: LLM backend registry + Protocol (v2, re-frozen).

Spec: SHARED/memory/Decisions/2026-06-18_68E964FB_llm_backend_registry_spec.md

FAIL today (new behavior — _BACKENDS / _invoke_subprocess absent):
  AC1  — _BACKENDS attr missing (AttributeError at getattr assert)
  AC1b — _KNOWN_BACKENDS == tuple(_BACKENDS) fails (_BACKENDS absent)
  AC2  — dispatcher does not use registry (can't spy via _BACKENDS)
  AC3  — dispatcher does not use registry
  AC6  — _invoke_subprocess not yet extracted
  AC8  — _invoke_subprocess absent → assert fn is not None fires
  AC9  — _invoke_subprocess absent → assert fn is not None fires

PASS today (correctness / regression guards):
  AC4  — _resolve_backend already exists with correct precedence
  AC5  — fail-loud already uses E_LLM_BACKEND_UNKNOWN (L889)
  AC7  — public invoke_llm_subprocess signature already correct
  AC10 — _assert_backend_supports_manifest gate already at L895

§1q compliance: _BACKENDS / _invoke_subprocess accessed ONLY inside test
bodies via getattr — AttributeError fires at assert time (clean FAIL, not
ImportError at collection time).
§1l compliance: spy on *registered handlers* and collaborators
(_assert_hard_gate_opus, _assert_backend_supports_manifest, subprocess.Popen);
NEVER mock the UUT (invoke_llm_subprocess / _BACKENDS / _invoke_subprocess).
§1i: no singleton-resource or timing races — §1i N/A here (no lock/port/cap).
"""
from __future__ import annotations

import inspect
import os
from unittest.mock import MagicMock, call, patch

import pytest

# conftest.py injects engine_py root into sys.path at import time (§1q singleton).
# Import the stable module that already exists — no top-level access to not-yet-existing attrs.
import llm_subprocess


@pytest.fixture(autouse=True)
def _reset_backends_registry():
    """xdist isolation: reset _BACKENDS to the clean default before/after each
    test — a co-located test that imported anthropic_api (module-level
    register() side-effect) would otherwise leak 'anthropic-api' into this
    worker's registry."""
    llm_subprocess.reset_backends()
    yield
    llm_subprocess.reset_backends()


# ─── Minimal kwargs helper (NOT a fixture) ────────────────────────────────────

def _minimal_invoke_kwargs(**overrides):
    """Minimal valid kwargs for invoke_llm_subprocess.

    Routing-path tests monkeypatch _BACKENDS entries to short-circuit before
    any real Popen; subprocess-path tests patch Popen itself.
    """
    base = dict(
        prompt="x",
        model="sonnet",
        timeout_sec=5,
        step_name="test-68E964FB",
    )
    base.update(overrides)
    return base


def _minimal_invoke_subprocess_kwargs(**overrides):
    """Minimal valid kwargs for direct _invoke_subprocess calls (11-param superset)."""
    base = dict(
        prompt="x",
        model="sonnet",
        timeout_sec=5,
        step_name="test-68E964FB",
        extra_data=None,
        allowed_tools=None,
        run_ctx=None,
        hard_gate=False,
        gate_label=None,
        straggler_cfg=None,
        idle_timeout_sec=None,
    )
    base.update(overrides)
    return base


# ─── AC1: _BACKENDS registry dict ─────────────────────────────────────────────

def test_ac1_backends_registry_exists_with_correct_keys():
    """AC1: _BACKENDS is a dict keyed exactly {"claude-subprocess","claude-in-session"},
    both values callable.
    Fails today: _BACKENDS attribute does not exist on llm_subprocess.
    """
    registry = getattr(llm_subprocess, "_BACKENDS", None)
    assert registry is not None, (
        "_BACKENDS not found on llm_subprocess — registry not yet implemented"
    )
    assert isinstance(registry, dict), f"_BACKENDS must be a dict, got {type(registry)}"
    assert set(registry.keys()) == {"claude-subprocess", "claude-in-session"}, (
        f"_BACKENDS keys mismatch: {set(registry.keys())!r}"
    )
    for key, handler in registry.items():
        assert callable(handler), f"_BACKENDS[{key!r}] is not callable: {handler!r}"


# ─── AC1b: _KNOWN_BACKENDS derives from _BACKENDS (§1g single-source) ─────────

def test_ac1b_known_backends_derives_from_registry():
    """AC1b: _KNOWN_BACKENDS == tuple(_BACKENDS) — registry is single source of truth (§1g).
    Fails today: _BACKENDS absent, so tuple(_BACKENDS) raises AttributeError at assert.
    """
    registry = getattr(llm_subprocess, "_BACKENDS", None)
    assert registry is not None, "_BACKENDS not yet implemented — AC1b prerequisite"
    assert llm_subprocess._KNOWN_BACKENDS == tuple(registry), (
        f"_KNOWN_BACKENDS {llm_subprocess._KNOWN_BACKENDS!r} != tuple(_BACKENDS) "
        f"{tuple(registry)!r} — single-source invariant broken"
    )


# ─── AC2: in-session routing ───────────────────────────────────────────────────

def test_ac2_in_session_routing_invokes_in_session_not_subprocess():
    """AC2: backend='claude-in-session' → in-session handler called, subprocess NOT.
    Fails today: dispatcher uses hardcoded if/else, not registry → can't spy.
    """
    registry = getattr(llm_subprocess, "_BACKENDS", None)
    assert registry is not None, "_BACKENDS not yet implemented — AC2 prerequisite"

    in_session_spy = MagicMock(return_value=MagicMock(status="ok"))
    subprocess_spy = MagicMock(return_value=MagicMock(status="ok"))

    patched = dict(registry)
    patched["claude-in-session"] = in_session_spy
    patched["claude-subprocess"] = subprocess_spy

    with patch.object(llm_subprocess, "_BACKENDS", patched):
        llm_subprocess.invoke_llm_subprocess(
            **_minimal_invoke_kwargs(backend="claude-in-session")
        )

    assert in_session_spy.called, (
        "in-session handler was NOT called for backend='claude-in-session'"
    )
    assert not subprocess_spy.called, "subprocess handler was UNEXPECTEDLY called"


# ─── AC3: subprocess routing + default ────────────────────────────────────────

def test_ac3_subprocess_routing_explicit():
    """AC3a: backend='claude-subprocess' → subprocess handler called, in-session NOT.
    Fails today: dispatcher does not route via registry.
    """
    registry = getattr(llm_subprocess, "_BACKENDS", None)
    assert registry is not None, "_BACKENDS not yet implemented — AC3 prerequisite"

    in_session_spy = MagicMock(return_value=MagicMock(status="ok"))
    subprocess_spy = MagicMock(return_value=MagicMock(status="ok"))

    patched = dict(registry)
    patched["claude-in-session"] = in_session_spy
    patched["claude-subprocess"] = subprocess_spy

    with patch.object(llm_subprocess, "_BACKENDS", patched):
        llm_subprocess.invoke_llm_subprocess(
            **_minimal_invoke_kwargs(backend="claude-subprocess")
        )

    assert subprocess_spy.called, (
        "subprocess handler was NOT called for backend='claude-subprocess'"
    )
    assert not in_session_spy.called, "in-session handler was UNEXPECTEDLY called"


def test_ac3_subprocess_routing_default(monkeypatch):
    """AC3b: no backend kwarg + HAL_RUNNER_BACKEND unset → default = subprocess handler.
    Fails today: dispatcher does not route via registry.
    """
    registry = getattr(llm_subprocess, "_BACKENDS", None)
    assert registry is not None, "_BACKENDS not yet implemented — AC3 prerequisite"

    monkeypatch.delenv("HAL_RUNNER_BACKEND", raising=False)

    in_session_spy = MagicMock(return_value=MagicMock(status="ok"))
    subprocess_spy = MagicMock(return_value=MagicMock(status="ok"))

    patched = dict(registry)
    patched["claude-in-session"] = in_session_spy
    patched["claude-subprocess"] = subprocess_spy

    with patch.object(llm_subprocess, "_BACKENDS", patched):
        llm_subprocess.invoke_llm_subprocess(**_minimal_invoke_kwargs())

    assert subprocess_spy.called, "default path did not route to subprocess handler"
    assert not in_session_spy.called, "in-session handler called on default path"


# ─── AC4: _resolve_backend precedence (PASS-today guard) ──────────────────────

def test_ac4_resolve_backend_kwarg_wins(monkeypatch):
    """AC4a: kwarg > env > default — kwarg wins when all three are set."""
    monkeypatch.setenv("HAL_RUNNER_BACKEND", "claude-in-session")
    result, source = llm_subprocess._resolve_backend("claude-subprocess", os.environ)
    assert result == "claude-subprocess", f"kwarg should win, got {result!r}"
    assert source == "kwarg", f"source should be 'kwarg', got {source!r}"


def test_ac4_resolve_backend_env_wins_over_default(monkeypatch):
    """AC4b: env > default — env wins when kwarg is None."""
    monkeypatch.setenv("HAL_RUNNER_BACKEND", "claude-in-session")
    result, source = llm_subprocess._resolve_backend(None, os.environ)
    assert result == "claude-in-session", f"env should win over default, got {result!r}"
    assert source == "env", f"source should be 'env', got {source!r}"


def test_ac4_resolve_backend_default(monkeypatch):
    """AC4c: default == 'agent-sdk' when kwarg=None, env unset (GH1001 C0B6D653 flip, 2026-07-19)."""
    monkeypatch.delenv("HAL_RUNNER_BACKEND", raising=False)
    result, source = llm_subprocess._resolve_backend(None, os.environ)
    assert result == "agent-sdk", (
        f"default should be 'agent-sdk', got {result!r}"
    )
    assert source == "default", f"source should be 'default', got {source!r}"


def test_ac4_backend_env_var_name():
    """AC4d: env var name constant is 'HAL_RUNNER_BACKEND'."""
    assert llm_subprocess._BACKEND_ENV_VAR == "HAL_RUNNER_BACKEND", (
        f"_BACKEND_ENV_VAR is {llm_subprocess._BACKEND_ENV_VAR!r}, "
        "expected 'HAL_RUNNER_BACKEND'"
    )


def test_ac4_default_backend_constant():
    """AC4e: _DEFAULT_BACKEND == 'agent-sdk' (GH1001 C0B6D653 flip, 2026-07-19)."""
    assert llm_subprocess._DEFAULT_BACKEND == "agent-sdk", (
        f"_DEFAULT_BACKEND is {llm_subprocess._DEFAULT_BACKEND!r}"
    )


# ─── AC5: unknown backend → E_LLM_BACKEND_UNKNOWN, no handler invoked ──────────
# PASS-today guard: fail-loud at L882-891 already uses E_LLM_BACKEND_UNKNOWN.

def test_ac5_unknown_backend_returns_error_stepresult_with_correct_code():
    """AC5: unknown backend 'gpt-x' → StepResult(status='error',
    error_code='E_LLM_BACKEND_UNKNOWN', recoverable=False), and NO registry
    handler invoked.
    Passes today (fail-loud preserved at L882-891 with correct error code).
    After GREEN: also verifies no registry handler was called.
    """
    registry = getattr(llm_subprocess, "_BACKENDS", None)

    if registry is not None:
        # Post-GREEN path: spy on registry handlers to confirm neither was called.
        in_session_spy = MagicMock(return_value=MagicMock(status="ok"))
        subprocess_spy = MagicMock(return_value=MagicMock(status="ok"))
        patched = dict(registry)
        patched["claude-in-session"] = in_session_spy
        patched["claude-subprocess"] = subprocess_spy
        ctx = patch.object(llm_subprocess, "_BACKENDS", patched)
    else:
        # Pre-GREEN path: registry absent, just call through.
        from contextlib import nullcontext
        ctx = nullcontext()
        in_session_spy = None
        subprocess_spy = None

    with ctx:
        result = llm_subprocess.invoke_llm_subprocess(
            **_minimal_invoke_kwargs(backend="gpt-x")
        )

    assert result.status == "error", (
        f"expected status='error' for unknown backend, got {result.status!r}"
    )
    assert result.error_code == "E_LLM_BACKEND_UNKNOWN", (
        f"expected error_code='E_LLM_BACKEND_UNKNOWN', got {result.error_code!r}"
    )
    assert result.recoverable is False, (
        f"expected recoverable=False, got {result.recoverable!r}"
    )
    if in_session_spy is not None:
        assert not in_session_spy.called, "in-session handler called for unknown backend"
    if subprocess_spy is not None:
        assert not subprocess_spy.called, "subprocess handler called for unknown backend"


# ─── AC6: _invoke_subprocess module-level callable + superset signature ────────

def test_ac6_invoke_subprocess_exists_and_is_callable():
    """AC6a: _invoke_subprocess exists at module level and is callable.
    Fails today: inline subprocess path not yet extracted.
    """
    fn = getattr(llm_subprocess, "_invoke_subprocess", None)
    assert fn is not None, (
        "_invoke_subprocess not found on llm_subprocess — not yet extracted (§2.1)"
    )
    assert callable(fn), f"_invoke_subprocess is not callable: {fn!r}"


def test_ac6_invoke_subprocess_signature_has_superset_params():
    """AC6b: _invoke_subprocess signature contains all §2.1 superset params
    (8-param in-session base PLUS gate_label, straggler_cfg, idle_timeout_sec).
    Fails today: _invoke_subprocess absent.
    """
    fn = getattr(llm_subprocess, "_invoke_subprocess", None)
    assert fn is not None, "_invoke_subprocess not yet implemented"
    sig = inspect.signature(fn)
    params = set(sig.parameters.keys())
    required = {
        "prompt", "model", "timeout_sec", "step_name",
        "extra_data", "allowed_tools", "run_ctx", "hard_gate",
        "gate_label", "straggler_cfg", "idle_timeout_sec",
    }
    missing = required - params
    assert not missing, (
        f"_invoke_subprocess missing parameters: {sorted(missing)!r}"
    )


# ─── AC7: public invoke_llm_subprocess signature unchanged (PASS-today guard) ──

def test_ac7_invoke_llm_subprocess_signature_unchanged():
    """AC7: public invoke_llm_subprocess retains all L764-777 params.
    Passes today — regression guard.
    """
    sig = inspect.signature(llm_subprocess.invoke_llm_subprocess)
    params = set(sig.parameters.keys())
    required = {
        "prompt", "model", "timeout_sec", "step_name",
        "extra_data", "hard_gate", "gate_label",
        "idle_timeout_sec", "allowed_tools", "straggler_cfg", "backend",
    }
    missing = required - params
    assert not missing, (
        f"invoke_llm_subprocess missing parameters: {sorted(missing)!r}"
    )


# ─── AC8: subprocess handler triggers _assert_hard_gate_opus ──────────────────

def test_ac8_subprocess_hard_gate_triggers_assert_hard_gate_opus():
    """AC8: _invoke_subprocess(..., hard_gate=True, command=['claude','-p',...]) calls
    _assert_hard_gate_opus.
    Fails today: _invoke_subprocess absent → assert fn is not None fires.
    """
    fn = getattr(llm_subprocess, "_invoke_subprocess", None)
    assert fn is not None, (
        "_invoke_subprocess not yet implemented — AC8 cannot proceed"
    )

    # Deferred import per §1q-ext / §1q (D1CF5FDF): contracts already importable.
    from contracts import StepResult  # noqa: PLC0415

    with patch.object(llm_subprocess, "_assert_hard_gate_opus") as spy:
        # Make the gate short-circuit so no real Popen is attempted.
        spy.return_value = StepResult(
            status="error",
            error_code="E_HARD_GATE_MODEL_DOWNGRADE",
            error="gate refused",
            recoverable=False,
            step_name="test-ac8",
            data=None,
            duration_ms=0,
        )
        fn(**_minimal_invoke_subprocess_kwargs(
            model="sonnet",
            hard_gate=True,
            gate_label="test-gate",
        ))

    assert spy.called, (
        "_assert_hard_gate_opus was NOT called by _invoke_subprocess with hard_gate=True"
    )


# ─── AC9: flag auto-injection preserved (E6F86B73) ────────────────────────────

def test_ac9_flag_auto_injection_adds_output_format_stream_json_verbose():
    """AC9: _invoke_subprocess(command=['claude','-p','--model','sonnet'], ...) produces
    an effective command containing '--output-format', 'stream-json', '--verbose'.
    Fails today: _invoke_subprocess absent → assert fn is not None fires.
    §1l: spy on subprocess.Popen (the collaborator), NOT on _invoke_subprocess itself.
    """
    fn = getattr(llm_subprocess, "_invoke_subprocess", None)
    assert fn is not None, (
        "_invoke_subprocess not yet implemented — AC9 cannot proceed"
    )

    captured_cmds = []

    class _FakePopen:
        def __init__(self, cmd, **kwargs):
            captured_cmds.append(list(cmd))
            # Minimal Popen-compatible stub so the function can proceed past the
            # Popen call and return without blocking.
            self.stdout = iter([])
            self.stderr = iter([])
            self.returncode = 0
            self.pid = 0

        def wait(self, timeout=None):
            return 0

        def communicate(self, timeout=None):
            return b"", b""

        def poll(self):
            return 0

        def kill(self):
            pass

        def terminate(self):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    from contracts import StepResult  # noqa: PLC0415

    # Patch Popen to capture the effective command; also stub out the hard_gate
    # path so we never actually check for opus model.
    with patch.object(llm_subprocess, "_assert_hard_gate_opus", return_value=None), \
         patch("llm_subprocess.subprocess.Popen", _FakePopen):
        try:
            fn(**_minimal_invoke_subprocess_kwargs(
                model="sonnet",
                hard_gate=False,
            ))
        except Exception:
            # The stub Popen may not satisfy all internal reads; we only need
            # captured_cmds to have been populated.
            pass

    assert captured_cmds, (
        "subprocess.Popen was never called — _invoke_subprocess did not reach Popen"
    )
    effective_cmd = captured_cmds[0]
    assert "--output-format" in effective_cmd, (
        f"'--output-format' not found in effective command {effective_cmd!r}"
    )
    assert "stream-json" in effective_cmd, (
        f"'stream-json' not found in effective command {effective_cmd!r}"
    )
    assert "--verbose" in effective_cmd, (
        f"'--verbose' not found in effective command {effective_cmd!r}"
    )


# ─── AC10: pre-dispatch gate preserved (PASS-today guard) ─────────────────────

def test_ac10_pre_dispatch_gate_assert_backend_supports_manifest_called():
    """AC10: invoke_llm_subprocess(backend='claude-subprocess', ...) calls
    _assert_backend_supports_manifest.
    Passes today (gate already at L895) — regression guard.
    After GREEN: gate fires before the registry dispatch.
    """
    registry = getattr(llm_subprocess, "_BACKENDS", None)

    if registry is not None:
        # Post-GREEN: spy on the subprocess handler so it short-circuits.
        subprocess_spy = MagicMock(return_value=MagicMock(status="ok"))
        patched_reg = dict(registry)
        patched_reg["claude-subprocess"] = subprocess_spy
        reg_ctx = patch.object(llm_subprocess, "_BACKENDS", patched_reg)
    else:
        from contextlib import nullcontext
        reg_ctx = nullcontext()

    with patch.object(
        llm_subprocess, "_assert_backend_supports_manifest", return_value=None
    ) as manifest_spy, reg_ctx:
        llm_subprocess.invoke_llm_subprocess(
            **_minimal_invoke_kwargs(backend="claude-subprocess")
        )

    assert manifest_spy.called, (
        "_assert_backend_supports_manifest was NOT called by invoke_llm_subprocess "
        "for backend='claude-subprocess' — pre-dispatch gate missing or bypassed"
    )
