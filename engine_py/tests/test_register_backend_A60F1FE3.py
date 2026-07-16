"""RED tests for A60F1FE3: public backend-registration injection seam (#302 Ship 1b).

Spec: SHARED/memory/Decisions/2026-06-19_A60F1FE3_302_llm_vendor_decouple_spec.md

FAIL today (register_backend / reset_backends / _DEFAULT_BACKENDS do not exist yet):
  AC1 — register_backend absent → AttributeError in test body → FAIL
  AC2 — register_backend absent → AttributeError → FAIL
  AC3 — register_backend absent → AttributeError → FAIL
  AC4 — register_backend absent → AttributeError → FAIL
  AC5 — register_backend absent → AttributeError → FAIL
  AC6 — register_backend absent → AttributeError → FAIL
  AC7 — reset_backends absent → AttributeError → FAIL
  AC8 — reset_backends absent → AttributeError → FAIL
  AC9 — PASS-today guard (E_LLM_BACKEND_UNKNOWN already fires at L893)

§1q compliance: register_backend / reset_backends / _DEFAULT_BACKENDS are accessed
ONLY inside test bodies via getattr or deferred attribute lookup — file COLLECTS
cleanly (no top-level reference to not-yet-existing symbols), and FAILS at assert
time in the body. Per D1CF5FDF: collection errors manifest as ~30-min hangs under
the engine red_runtime — defer, never import at module level.

§1l compliance: spy backend impl is a FIXTURE (not the UUT); UUTs
(register_backend / reset_backends / invoke_llm_subprocess) are NEVER patched
(stub-passability gate §1l / 7AD3D393 rejects a RED that mocks its own UUT).

§1i compliance: autouse teardown calls reset_backends() (guarded for RED phase
so teardown itself doesn't crash before GREEN lands). Pre-stages the contested
global state deterministically before each test.

§1i singleton-resource citation (8CA8D54C): _BACKENDS/_KNOWN_BACKENDS/_ALLOWED_MANIFEST_SOURCES
are module-level singletons. Each test must operate from a known baseline
(reset in teardown) — never race against leaked state from a sibling test.
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

# conftest.py injects engine_py root into sys.path at import time (§1q singleton).
# Import the stable module that already exists — no top-level access to
# not-yet-existing symbols (register_backend / reset_backends / _DEFAULT_BACKENDS).
import llm_subprocess


# ---------------------------------------------------------------------------
# Spy backend implementation (fixture, NOT the UUT)
# ---------------------------------------------------------------------------

class _SpyBackend:
    """Minimal LLMBackend-Protocol-compatible callable.

    Accepts **kwargs to tolerate the 11-param Protocol without binding every
    parameter name. Records every call; returns a sentinel StepResult so the
    caller can assert "the spy, not an error, was returned".
    """

    SENTINEL_ERROR_CODE = "SPY_SENTINEL_OK"

    def __init__(self, name: str = "spy"):
        self._name = name
        self.calls: list[dict] = []

    def __call__(self, **kwargs) -> object:
        self.calls.append(dict(kwargs))
        # Deferred import so the file collects even before contracts exists
        # (contracts already shipped; this is belt-and-suspenders).
        from contracts import StepResult  # noqa: PLC0415
        return StepResult(
            status="ok",
            data={"spy": self._name},
            duration_ms=0,
            step_name=kwargs.get("step_name", "spy"),
            error=None,
            error_code=None,
            recoverable=True,
        )


# ---------------------------------------------------------------------------
# Autouse fixture: §1i teardown + emit_resolver_resolved no-op
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_backends_after(monkeypatch):
    """§1i singleton-resource guard.

    Teardown: restore built-in backends so registrations don't leak across tests.
    The guard is conditional so teardown never crashes during the RED phase
    (when reset_backends doesn't exist yet — crash in teardown turns a clean
    FAIL into a confusing ERROR).

    Also neutralise emit_resolver_resolved to avoid disk writes into SHARED/state
    during invoke_llm_subprocess calls (spec §6).
    """
    # Neutralise disk-writing side-effects of emit_resolver_resolved.
    monkeypatch.setattr(llm_subprocess, "emit_resolver_resolved", lambda *a, **kw: None)

    yield

    # Teardown: restore defaults if reset_backends exists (GREEN phase).
    reset_fn = getattr(llm_subprocess, "reset_backends", None)
    if reset_fn is not None:
        reset_fn()


# ---------------------------------------------------------------------------
# Helper: minimal valid kwargs for invoke_llm_subprocess
# ---------------------------------------------------------------------------

def _invoke_kwargs(**overrides):
    """Return minimal keyword args for invoke_llm_subprocess.

    idle_timeout_sec=0 skips the watchdog probe (spec §6: dispatch is reached
    for the spy without triggering E_LLM_WATCHDOG_UNSUPPORTED).
    straggler_cfg=None likewise skips straggler watchdog.
    command uses a shell no-op so no real subprocess is launched.
    """
    base = dict(
        prompt="p",
        model="sonnet",
        timeout_sec=1,
        step_name="s",
        idle_timeout_sec=0,
        straggler_cfg=None,
    )
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# AC1: dispatch to registered spy backend
# ---------------------------------------------------------------------------

def test_ac1_registered_backend_is_dispatched_by_invoke_llm_subprocess():
    """AC1: After register_backend("oss-test", spy, manifest_source="harness_tool_record"),
    invoke_llm_subprocess(backend="oss-test", ...) dispatches to spy — spy records
    exactly one call AND returned StepResult carries the spy's sentinel data, NOT
    error_code="E_LLM_BACKEND_UNKNOWN".

    FAILS today: register_backend does not exist (AttributeError in body).
    """
    register_backend = getattr(llm_subprocess, "register_backend", None)
    assert register_backend is not None, (
        "register_backend not found on llm_subprocess — not yet implemented (#302 A60F1FE3)"
    )

    spy = _SpyBackend("ac1-spy")
    register_backend("oss-test", spy, manifest_source="harness_tool_record")

    result = llm_subprocess.invoke_llm_subprocess(
        **_invoke_kwargs(backend="oss-test")
    )

    assert len(spy.calls) == 1, (
        f"spy must record exactly 1 call, got {len(spy.calls)}"
    )
    assert result.error_code != "E_LLM_BACKEND_UNKNOWN", (
        f"dispatch must NOT return E_LLM_BACKEND_UNKNOWN; got error_code={result.error_code!r}"
    )
    assert result.status == "ok", (
        f"dispatch to spy must return status='ok', got {result.status!r}"
    )
    assert (result.data or {}).get("spy") == "ac1-spy", (
        f"StepResult.data must carry spy sentinel, got {result.data!r}"
    )


# ---------------------------------------------------------------------------
# AC2: three maps + _KNOWN_BACKENDS updated on registration
# ---------------------------------------------------------------------------

def test_ac2_all_three_maps_and_known_backends_updated_after_register():
    """AC2: After register_backend("oss-test", spy, manifest_source="harness_tool_record"):
    _BACKENDS["oss-test"] is spy, _BACKEND_MANIFEST_SOURCE["oss-test"] == "harness_tool_record",
    _BACKEND_CAPABILITIES["oss-test"] == frozenset(), "oss-test" in _KNOWN_BACKENDS.

    FAILS today: register_backend absent (AttributeError).
    """
    register_backend = getattr(llm_subprocess, "register_backend", None)
    assert register_backend is not None, (
        "register_backend not found — not yet implemented"
    )

    spy = _SpyBackend("ac2-spy")
    register_backend("oss-test", spy, manifest_source="harness_tool_record")

    assert llm_subprocess._BACKENDS.get("oss-test") is spy, (
        f"_BACKENDS['oss-test'] should be spy, got {llm_subprocess._BACKENDS.get('oss-test')!r}"
    )
    assert llm_subprocess._BACKEND_MANIFEST_SOURCE.get("oss-test") == "harness_tool_record", (
        f"_BACKEND_MANIFEST_SOURCE['oss-test'] should be 'harness_tool_record', "
        f"got {llm_subprocess._BACKEND_MANIFEST_SOURCE.get('oss-test')!r}"
    )
    assert llm_subprocess._BACKEND_CAPABILITIES.get("oss-test") == frozenset(), (
        f"_BACKEND_CAPABILITIES['oss-test'] should be frozenset() (no caps), "
        f"got {llm_subprocess._BACKEND_CAPABILITIES.get('oss-test')!r}"
    )
    assert "oss-test" in llm_subprocess._KNOWN_BACKENDS, (
        f"'oss-test' must be in _KNOWN_BACKENDS after registration; "
        f"got {llm_subprocess._KNOWN_BACKENDS!r}"
    )


# ---------------------------------------------------------------------------
# AC3: new manifest_source propagates to _ALLOWED_MANIFEST_SOURCES
# ---------------------------------------------------------------------------

def test_ac3_new_manifest_source_propagates_to_allowed_manifest_sources():
    """AC3: register_backend("oss2", spy, manifest_source="my_custom_source") ⇒
    "my_custom_source" in _ALLOWED_MANIFEST_SOURCES AND
    _assert_backend_supports_manifest("oss2") is None.

    FAILS today: register_backend absent (AttributeError).
    """
    register_backend = getattr(llm_subprocess, "register_backend", None)
    assert register_backend is not None, (
        "register_backend not found — not yet implemented"
    )

    spy = _SpyBackend("ac3-spy")
    register_backend("oss2", spy, manifest_source="my_custom_source")

    assert "my_custom_source" in llm_subprocess._ALLOWED_MANIFEST_SOURCES, (
        f"'my_custom_source' must be in _ALLOWED_MANIFEST_SOURCES after registration; "
        f"got {llm_subprocess._ALLOWED_MANIFEST_SOURCES!r}"
    )
    gate_result = llm_subprocess._assert_backend_supports_manifest("oss2")
    assert gate_result is None, (
        f"_assert_backend_supports_manifest('oss2') must return None (no error) "
        f"after registration; got {gate_result!r}"
    )


# ---------------------------------------------------------------------------
# AC4: capabilities stored + watchdog gate accepts
# ---------------------------------------------------------------------------

def test_ac4_capabilities_stored_and_watchdog_gate_accepts():
    """AC4: register_backend("oss3", spy, manifest_source="harness_tool_record",
    capabilities={"progress_since","abort"}) ⇒
    _BACKEND_CAPABILITIES["oss3"] == frozenset({"progress_since","abort"}) AND
    _assert_backend_supports_watchdog("oss3", idle_enabled=True, straggler_enabled=True) is None.

    FAILS today: register_backend absent (AttributeError).
    """
    register_backend = getattr(llm_subprocess, "register_backend", None)
    assert register_backend is not None, (
        "register_backend not found — not yet implemented"
    )

    spy = _SpyBackend("ac4-spy")
    register_backend(
        "oss3", spy,
        manifest_source="harness_tool_record",
        capabilities={"progress_since", "abort"},
    )

    assert llm_subprocess._BACKEND_CAPABILITIES.get("oss3") == frozenset({"progress_since", "abort"}), (
        f"_BACKEND_CAPABILITIES['oss3'] must be frozenset({{'progress_since','abort'}}), "
        f"got {llm_subprocess._BACKEND_CAPABILITIES.get('oss3')!r}"
    )
    watchdog_result = llm_subprocess._assert_backend_supports_watchdog(
        "oss3", idle_enabled=True, straggler_enabled=True
    )
    assert watchdog_result is None, (
        f"_assert_backend_supports_watchdog('oss3', ...) must return None after "
        f"registration with capabilities; got {watchdog_result!r}"
    )


# ---------------------------------------------------------------------------
# AC5: duplicate guard and overwrite
# ---------------------------------------------------------------------------

def test_ac5_duplicate_guard_and_overwrite():
    """AC5: second register_backend("oss-test", ...) without overwrite=True raises ValueError;
    with overwrite=True the call succeeds and _BACKENDS["oss-test"] is the replacement.

    FAILS today: register_backend absent (AttributeError).
    """
    register_backend = getattr(llm_subprocess, "register_backend", None)
    assert register_backend is not None, (
        "register_backend not found — not yet implemented"
    )

    spy1 = _SpyBackend("ac5-spy1")
    spy2 = _SpyBackend("ac5-spy2")

    # First registration succeeds.
    register_backend("oss-test", spy1, manifest_source="harness_tool_record")

    # Second without overwrite must raise ValueError.
    with pytest.raises(ValueError, match="already registered"):
        register_backend("oss-test", spy2, manifest_source="harness_tool_record")

    # Confirm original is still registered after the failed duplicate.
    assert llm_subprocess._BACKENDS.get("oss-test") is spy1, (
        f"_BACKENDS['oss-test'] should still be spy1 after rejected duplicate; "
        f"got {llm_subprocess._BACKENDS.get('oss-test')!r}"
    )

    # With overwrite=True the replacement succeeds.
    register_backend("oss-test", spy2, manifest_source="harness_tool_record", overwrite=True)
    assert llm_subprocess._BACKENDS.get("oss-test") is spy2, (
        f"_BACKENDS['oss-test'] should be spy2 after overwrite=True; "
        f"got {llm_subprocess._BACKENDS.get('oss-test')!r}"
    )


# ---------------------------------------------------------------------------
# AC6: input validation
# ---------------------------------------------------------------------------

def test_ac6_input_validation():
    """AC6: empty name raises ValueError; non-callable impl raises TypeError;
    empty manifest_source raises ValueError.

    FAILS today: register_backend absent (AttributeError on first assert).
    """
    register_backend = getattr(llm_subprocess, "register_backend", None)
    assert register_backend is not None, (
        "register_backend not found — not yet implemented"
    )

    spy = _SpyBackend("ac6-spy")

    # Empty name.
    with pytest.raises(ValueError):
        register_backend("", spy, manifest_source="harness_tool_record")

    # Non-callable impl: object() instance is not callable.
    with pytest.raises(TypeError):
        register_backend("x-backend", object(), manifest_source="harness_tool_record")

    # Empty manifest_source.
    with pytest.raises(ValueError):
        register_backend("x-backend", spy, manifest_source="")


# ---------------------------------------------------------------------------
# AC7: reset_backends restores exactly the built-ins
# ---------------------------------------------------------------------------

def test_ac7_reset_backends_restores_builtins():
    """AC7: After register_backend("oss-test", ...) then reset_backends(),
    set(_BACKENDS) == {"claude-subprocess","claude-in-session"},
    "oss-test" not in _KNOWN_BACKENDS/MANIFEST_SOURCE/CAPABILITIES.

    FAILS today: register_backend/reset_backends absent (AttributeError).
    """
    register_backend = getattr(llm_subprocess, "register_backend", None)
    assert register_backend is not None, (
        "register_backend not found — not yet implemented"
    )
    reset_backends = getattr(llm_subprocess, "reset_backends", None)
    assert reset_backends is not None, (
        "reset_backends not found — not yet implemented"
    )

    spy = _SpyBackend("ac7-spy")
    register_backend("oss-test", spy, manifest_source="harness_tool_record")

    # Confirm it was registered.
    assert "oss-test" in llm_subprocess._BACKENDS, (
        "pre-condition: 'oss-test' should be in _BACKENDS before reset"
    )

    reset_backends()

    assert set(llm_subprocess._BACKENDS) == {"claude-subprocess", "claude-in-session"}, (
        f"After reset, _BACKENDS must equal built-in set; got {set(llm_subprocess._BACKENDS)!r}"
    )
    assert "oss-test" not in llm_subprocess._KNOWN_BACKENDS, (
        f"'oss-test' must not be in _KNOWN_BACKENDS after reset; got {llm_subprocess._KNOWN_BACKENDS!r}"
    )
    assert "oss-test" not in llm_subprocess._BACKEND_MANIFEST_SOURCE, (
        f"'oss-test' must not be in _BACKEND_MANIFEST_SOURCE after reset"
    )
    assert "oss-test" not in llm_subprocess._BACKEND_CAPABILITIES, (
        f"'oss-test' must not be in _BACKEND_CAPABILITIES after reset"
    )


# ---------------------------------------------------------------------------
# AC8: built-in regression after register+reset cycle
# ---------------------------------------------------------------------------

def test_ac8_builtin_regression_after_register_reset_cycle():
    """AC8: After a register+reset cycle, built-in backends remain intact:
    "claude-subprocess"/"claude-in-session" in _KNOWN_BACKENDS,
    _assert_backend_supports_manifest("claude-subprocess") is None,
    _BACKEND_CAPABILITIES["claude-subprocess"] == frozenset({"manifest","progress_since","abort"}).

    FAILS today: reset_backends absent (AttributeError on reset_backends assert).
    """
    register_backend = getattr(llm_subprocess, "register_backend", None)
    assert register_backend is not None, (
        "register_backend not found — not yet implemented"
    )
    reset_backends = getattr(llm_subprocess, "reset_backends", None)
    assert reset_backends is not None, (
        "reset_backends not found — not yet implemented"
    )

    spy = _SpyBackend("ac8-spy")
    register_backend("oss-test", spy, manifest_source="harness_tool_record")
    reset_backends()

    # Built-in keys must be present.
    assert "claude-subprocess" in llm_subprocess._KNOWN_BACKENDS, (
        f"'claude-subprocess' must be in _KNOWN_BACKENDS after reset; "
        f"got {llm_subprocess._KNOWN_BACKENDS!r}"
    )
    assert "claude-in-session" in llm_subprocess._KNOWN_BACKENDS, (
        f"'claude-in-session' must be in _KNOWN_BACKENDS after reset"
    )

    # Manifest gate must pass for the built-in.
    manifest_result = llm_subprocess._assert_backend_supports_manifest("claude-subprocess")
    assert manifest_result is None, (
        f"_assert_backend_supports_manifest('claude-subprocess') must return None after reset; "
        f"got {manifest_result!r}"
    )

    # Capabilities must be the shipped defaults (L84 in llm_subprocess.py).
    expected_caps = frozenset({"manifest", "progress_since", "abort"})
    actual_caps = llm_subprocess._BACKEND_CAPABILITIES.get("claude-subprocess")
    assert actual_caps == expected_caps, (
        f"_BACKEND_CAPABILITIES['claude-subprocess'] must equal {expected_caps!r} after reset; "
        f"got {actual_caps!r}"
    )


# ---------------------------------------------------------------------------
# AC9: unknown-backend fail-loud unchanged (PASS-today guard)
# ---------------------------------------------------------------------------

def test_ac9_unknown_backend_returns_e_llm_backend_unknown():
    """AC9: invoke_llm_subprocess(backend="never-registered", ...) returns
    error_code == "E_LLM_BACKEND_UNKNOWN".

    PASSES today — existing fail-loud guard at L893 already handles this.
    Included as a regression guard: the guard must still fire after GREEN lands
    (i.e. the register_backend addition must not break the unknown-backend path).
    """
    result = llm_subprocess.invoke_llm_subprocess(
        **_invoke_kwargs(backend="never-registered")
    )
    assert result.error_code == "E_LLM_BACKEND_UNKNOWN", (
        f"Unknown backend must return E_LLM_BACKEND_UNKNOWN; got {result.error_code!r}"
    )
    assert result.status == "error", (
        f"Unknown backend must return status='error'; got {result.status!r}"
    )
    assert result.recoverable is False, (
        f"Unknown backend result must be non-recoverable; got {result.recoverable!r}"
    )
