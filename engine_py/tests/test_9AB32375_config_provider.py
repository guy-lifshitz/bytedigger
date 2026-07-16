"""RED tests for 9AB32375 — E2 config-provider seam (ConfigProvider Protocol + factory).

Covers AC1–AC10.  AC11/AC12 are orchestrator-run (mypy + full suite delta), NOT test funcs.

Pre-GREEN predict:
  AC1:  FAIL — ImportError on deferred `from config_provider import ConfigProvider`
  AC2:  FAIL — ImportError on deferred import
  AC3:  FAIL — ImportError on deferred import
  AC4:  FAIL — ImportError on deferred import
  AC5:  FAIL — ImportError on deferred import
  AC6:  PASS — regression guard; `phase_45_spec._verify_spec_scope_inverse` already
               returns env_skip on HAL_SPEC_SCOPE_GATE=0 (unchanged behavior)
  AC7:  PASS — regression guard; `phase_45_spec._verify_spec_coverage` already
               returns env_skip on HAL_SPEC_COVERAGE_GATE=0 (unchanged behavior)
  AC8:  FAIL — `_verify_red_lint_rules` reads `os.environ.get("HAL_STUB_PASSABILITY_GATE")`;
               with env-unset and provider injected→False, the gate still RUNS pre-GREEN
               because the site hasn't been migrated yet
  AC9:  FAIL — inline `os.environ.get("HAL_SPEC_SCOPE_GATE"` / `"HAL_SPEC_COVERAGE_GATE"` /
               `"HAL_STUB_PASSABILITY_GATE"` still present in the two workflow files pre-GREEN;
               also `get_config(` absent
  AC10: FAIL — env-unset + injected factory returning False → site reads os.environ directly
               (None != "0" = True ⇒ no skip); post-GREEN it reads the provider ⇒ skip fires

Collection safety (§1q / D1CF5FDF): `config_provider` is NOT imported at module level.
Every `from config_provider import ...` is deferred inside the test function body.
The file COLLECTS cleanly; each test fails at assert time, not collection time.

Singleton-resource guard (§1i, workflows.md §1i): the autouse `_reset_factory_after_test`
fixture resets `_DEFAULT_FACTORY` after every test to prevent test-order pollution.
The try/except around the import ensures teardown never errors during the RED phase when
config_provider.py does not exist yet.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

# sys.path is already set by conftest.py (engine_py root + workflows/).
# Do NOT add sys.path.insert here (§1q / 81F97F3D gate).


# ── autouse teardown fixture (§1i — module-global registry reset) ─────────────

@pytest.fixture(autouse=True)
def _reset_factory_after_test():
    """Reset the config_provider factory after every test (§1i singleton-resource).

    Guards against test-order pollution of the module-global _DEFAULT_FACTORY.
    The try/except around the import ensures teardown never errors during the
    RED phase when config_provider.py does not exist yet.
    """
    yield
    try:
        from config_provider import reset_default_config_provider_factory  # type: ignore[import]
        reset_default_config_provider_factory()
    except Exception:
        pass


# ── AC1 — ConfigProvider is a @runtime_checkable Protocol ────────────────────

def test_ac1_configprovider_protocol_isinstance_check():
    """AC1: ConfigProvider is a @runtime_checkable Protocol.

    An object implementing the full provider surface satisfies isinstance;
    a plain object() does NOT. (Surface grows per ship — E2 ship-2 F3F097C9
    added flag/timeout_ms/binary, so a runtime_checkable match now requires
    the full current surface, not just gate_enabled.)

    Pre-GREEN: FAIL — ImportError: config_provider module does not exist.
    """
    from config_provider import ConfigProvider  # type: ignore[import]

    class ConcreteProvider:
        def gate_enabled(self, env_var: str) -> bool:
            return True

        def flag(self, env_var: str) -> bool:
            return False

        def timeout_ms(self, env_var: str, default: int) -> int:
            return default

        def binary(self, env_var: str, default: str) -> str:
            return default

        def hal_root(self) -> Path:
            return Path("/fake/hal")

        def path(self, env_var: str, default: Path) -> Path:
            return default

    obj = ConcreteProvider()
    assert isinstance(obj, ConfigProvider) is True, (
        "An object implementing the full ConfigProvider surface must satisfy the Protocol"
    )

    bare = object()
    assert isinstance(bare, ConfigProvider) is False, (
        "object() with no gate_enabled must NOT satisfy ConfigProvider Protocol"
    )


# ── AC2 — get_config() returns a ConfigProvider instance ─────────────────────

def test_ac2_get_config_returns_configprovider():
    """AC2: get_config() returns an object that satisfies isinstance(obj, ConfigProvider).

    Pre-GREEN: FAIL — ImportError: config_provider module does not exist.
    """
    from config_provider import ConfigProvider, get_config  # type: ignore[import]

    result = get_config()
    assert isinstance(result, ConfigProvider) is True, (
        f"get_config() must return a ConfigProvider, got {type(result)!r}"
    )


# ── AC3 — default provider gate_enabled truth table ──────────────────────────

@pytest.mark.parametrize("env_val,expected", [
    (None,  True),   # unset → enabled
    ("0",   False),  # explicitly "0" → disabled
    ("1",   True),   # "1" → enabled
    ("x",   True),   # any other value → enabled
])
def test_ac3_default_gate_enabled_truth_table(monkeypatch, env_val, expected):
    """AC3: Default provider truth table: unset→True, "0"→False, "1"→True, "x"→True.

    Pre-GREEN: FAIL — ImportError: config_provider module does not exist.
    """
    from config_provider import get_config  # type: ignore[import]

    TEST_VAR = "HAL_TEST_9AB32375_AC3"
    if env_val is None:
        monkeypatch.delenv(TEST_VAR, raising=False)
    else:
        monkeypatch.setenv(TEST_VAR, env_val)

    provider = get_config()
    result = provider.gate_enabled(TEST_VAR)
    assert result is expected, (
        f"gate_enabled({TEST_VAR!r}) with env={env_val!r}: expected {expected}, got {result!r}"
    )


# ── AC4 — get_config resolves through _DEFAULT_FACTORY ───────────────────────

def test_ac4_get_config_resolves_through_default_factory():
    """AC4: get_config() returns the product of _DEFAULT_FACTORY.

    After set_default_config_provider_factory(lambda: rec), get_config() is rec.

    Pre-GREEN: FAIL — ImportError: config_provider module does not exist.
    """
    from config_provider import get_config, set_default_config_provider_factory  # type: ignore[import]

    class RecordingProvider:
        def gate_enabled(self, env_var: str) -> bool:
            return True

    rec = RecordingProvider()
    set_default_config_provider_factory(lambda: rec)

    result = get_config()
    assert result is rec, (
        "After set_default_config_provider_factory(lambda: rec), get_config() must return rec"
    )


# ── AC5 — set/reset factory injection lifecycle ───────────────────────────────

def test_ac5_set_and_reset_factory(monkeypatch):
    """AC5: set_default_config_provider_factory injects custom provider;
    reset_default_config_provider_factory restores env-based default.

    Pre-GREEN: FAIL — ImportError: config_provider module does not exist.
    """
    from config_provider import (  # type: ignore[import]
        get_config,
        set_default_config_provider_factory,
        reset_default_config_provider_factory,
    )

    TEST_VAR = "HAL_TEST_9AB32375_AC5"

    class AlwaysFalseProvider:
        def gate_enabled(self, env_var: str) -> bool:
            return False

    rec = AlwaysFalseProvider()
    set_default_config_provider_factory(lambda: rec)

    # Override is in effect
    assert get_config() is rec, "get_config() must return injected provider after set"
    assert get_config().gate_enabled(TEST_VAR) is False, (
        "Injected AlwaysFalseProvider must return False for any var"
    )

    # Reset restores env-based default
    reset_default_config_provider_factory()
    restored = get_config()
    assert restored is not rec, (
        "After reset, get_config() must NOT return the previously injected provider"
    )

    # Env-based behavior resumes: unset var → True
    monkeypatch.delenv(TEST_VAR, raising=False)
    assert restored.gate_enabled(TEST_VAR) is True, (
        "After reset, gate_enabled(unset_var) must return True (env-based default)"
    )


# ── AC6 — regression guard: scope-gate env-skip (PASSES pre-GREEN) ───────────

def test_ac6_scope_gate_env_skip_regression(monkeypatch):
    """AC6: _verify_spec_scope_inverse with HAL_SPEC_SCOPE_GATE=0 returns
    status="ok" and data["spec_scope_inverse_skipped"]=="env_skip".

    Pre-GREEN: PASS — the behavior is unchanged; the site still reads os.environ.
    Post-GREEN: PASS — the migrated site reads get_config(), same truth value.
    """
    from workflows.phase_45_spec import _verify_spec_scope_inverse  # type: ignore[import]
    from contracts import StepResult  # type: ignore[import]

    monkeypatch.setenv("HAL_SPEC_SCOPE_GATE", "0")

    # prev must have a falsy is_frozen (the gate check precedes spec_path check)
    prev = StepResult(
        status="ok",
        data={"is_frozen": False, "cycle": 1},
        duration_ms=0,
        step_name="prev_step",
    )

    class _FakeCtx:
        org_config: dict[str, Any] = {}

    result = _verify_spec_scope_inverse(_FakeCtx(), prev)

    assert result.status == "ok", (
        f"Expected status='ok' with HAL_SPEC_SCOPE_GATE=0, got {result.status!r}"
    )
    assert isinstance(result.data, dict), (
        f"Expected result.data to be dict, got {type(result.data)!r}"
    )
    assert result.data.get("spec_scope_inverse_skipped") == "env_skip", (
        f"Expected data['spec_scope_inverse_skipped']=='env_skip', got "
        f"{result.data.get('spec_scope_inverse_skipped')!r}"
    )


# ── AC7 — regression guard: coverage-gate env-skip (PASSES pre-GREEN) ─────────

def test_ac7_coverage_gate_env_skip_regression(monkeypatch):
    """AC7: _verify_spec_coverage with HAL_SPEC_COVERAGE_GATE=0 returns
    status="ok" and data["spec_coverage_skipped"]=="env_skip".

    Pre-GREEN: PASS — the behavior is unchanged; the site still reads os.environ.
    Post-GREEN: PASS — the migrated site reads get_config(), same truth value.
    """
    from workflows.phase_45_spec import _verify_spec_coverage  # type: ignore[import]
    from contracts import StepResult  # type: ignore[import]

    monkeypatch.setenv("HAL_SPEC_COVERAGE_GATE", "0")

    prev = StepResult(
        status="ok",
        data={"is_frozen": False, "cycle": 1},
        duration_ms=0,
        step_name="prev_step",
    )

    class _FakeCtx:
        org_config: dict[str, Any] = {}

    result = _verify_spec_coverage(_FakeCtx(), prev)

    assert result.status == "ok", (
        f"Expected status='ok' with HAL_SPEC_COVERAGE_GATE=0, got {result.status!r}"
    )
    assert isinstance(result.data, dict), (
        f"Expected result.data to be dict, got {type(result.data)!r}"
    )
    assert result.data.get("spec_coverage_skipped") == "env_skip", (
        f"Expected data['spec_coverage_skipped']=='env_skip', got "
        f"{result.data.get('spec_coverage_skipped')!r}"
    )


# ── AC8 — stub-passability gate is provider-driven (FAILS pre-GREEN) ──────────

def test_ac8_stub_passability_gate_provider_driven(monkeypatch, tmp_path):
    """AC8: After migration, _verify_red_lint_rules consults the provider for
    HAL_STUB_PASSABILITY_GATE. With the provider returning False (gate OFF) and
    env UNSET, the stub-passability block is skipped (no E_RED_STUB_PASSABLE).

    We test at the forcing-function level: inject a factory whose gate_enabled
    returns False for HAL_STUB_PASSABILITY_GATE; ensure the step does NOT
    return error_code=E_RED_STUB_PASSABLE for a test file that WOULD trigger
    the gate.

    Pre-GREEN: FAIL — the site reads os.environ directly; env is unset ⇒
               `os.environ.get("HAL_STUB_PASSABILITY_GATE","1") != "0"` is True
               (gate ON) ⇒ the stub-passability block runs and would return an
               error OR (if semgrep is absent) a different non-passability error.
               The assertion that E_RED_STUB_PASSABLE is NOT the outcome fails
               because the gate fires before semgrep is reached.
               (After GREEN: site reads provider ⇒ gate OFF ⇒ block skipped.)

    §1i (singleton-resource): config_provider factory reset is handled by the
    autouse _reset_factory_after_test fixture.
    """
    from config_provider import set_default_config_provider_factory  # type: ignore[import]
    from workflows.phase_5_implement import _verify_red_lint_rules  # type: ignore[import]
    from contracts import StepResult  # type: ignore[import]

    # Env UNSET — provider injection must be the only source of gate=OFF
    monkeypatch.delenv("HAL_STUB_PASSABILITY_GATE", raising=False)

    # Inject a factory whose gate_enabled returns False for the stub gate var
    class GateOffProvider:
        def gate_enabled(self, env_var: str) -> bool:
            # Turn OFF the stub-passability gate
            if env_var == "HAL_STUB_PASSABILITY_GATE":
                return False
            # Everything else: read env normally
            import os
            return os.environ.get(env_var) != "0"

    set_default_config_provider_factory(lambda: GateOffProvider())

    # Write a minimal test file that would trigger stub-passability (it mocks
    # a symbol it also imports — the vacuous-RED pattern flagged by the gate)
    red_file = tmp_path / "test_stub_vacuous.py"
    red_file.write_text(
        "from contracts import StepResult\n"
        "from unittest.mock import patch\n"
        "@patch('contracts.StepResult')\n"
        "def test_mock_uut(mock_sr):\n"
        "    assert mock_sr is not None\n",
        encoding="utf-8",
    )

    prev = StepResult(
        status="ok",
        data={
            "red_test_paths": [str(red_file)],
            "git_cwd": str(tmp_path),
        },
        duration_ms=0,
        step_name="prev_step",
    )

    class _FakeCtx:
        org_config: dict[str, Any] = {"git_cwd": str(tmp_path)}

    result = _verify_red_lint_rules(_FakeCtx(), prev)

    # With gate OFF (provider-driven), stub-passability block must be skipped.
    # The result must NOT be E_RED_STUB_PASSABLE.
    assert result.error_code != "E_RED_STUB_PASSABLE", (
        "With provider gate_enabled('HAL_STUB_PASSABILITY_GATE')==False and env UNSET, "
        "the stub-passability block must be skipped (E_RED_STUB_PASSABLE must NOT fire). "
        "Pre-GREEN: gate reads os.environ directly (None != '0' => True => gate runs)."
    )


# ── AC9 — source-grep: inline os.environ replaced by get_config( ─────────────

def test_ac9_inline_os_environ_replaced_in_workflow_files():
    """AC9: After GREEN, the two migrated workflow files must NOT contain the
    three inline os.environ.get(...) gate-flag calls; get_config( must be present.

    Pre-GREEN: FAIL — the inline calls are still present; get_config( absent.

    Note: this assertion is on the SOURCE TEXT of the migrated files (behavior
    proven by AC10). This asserts the migration actually happened, not a pattern
    in a string literal.
    """
    worktree = Path(__file__).parent.parent  # engine_py root
    phase45 = worktree / "workflows" / "phase_45_spec.py"
    phase5 = worktree / "workflows" / "phase_5_implement.py"

    assert phase45.is_file(), f"phase_45_spec.py not found at {phase45}"
    assert phase5.is_file(), f"phase_5_implement.py not found at {phase5}"

    phase45_src = phase45.read_text(encoding="utf-8")
    phase5_src = phase5.read_text(encoding="utf-8")

    # These inline calls must NOT remain after migration
    assert 'os.environ.get("HAL_SPEC_SCOPE_GATE"' not in phase45_src, (
        "phase_45_spec.py still contains inline os.environ.get(\"HAL_SPEC_SCOPE_GATE\") "
        "— migration not yet applied"
    )
    assert 'os.environ.get("HAL_SPEC_COVERAGE_GATE"' not in phase45_src, (
        "phase_45_spec.py still contains inline os.environ.get(\"HAL_SPEC_COVERAGE_GATE\") "
        "— migration not yet applied"
    )
    assert 'os.environ.get("HAL_STUB_PASSABILITY_GATE"' not in phase5_src, (
        "phase_5_implement.py still contains inline os.environ.get(\"HAL_STUB_PASSABILITY_GATE\") "
        "— migration not yet applied"
    )

    # get_config( must be present in each migrated file
    assert "get_config(" in phase45_src, (
        "phase_45_spec.py must import and call get_config() after migration"
    )
    assert "get_config(" in phase5_src, (
        "phase_5_implement.py must import and call get_config() after migration"
    )


# ── AC10 — THE FORCING FUNCTION (FAILS pre-GREEN) ────────────────────────────

def test_ac10_injection_reachability_scope_gate(monkeypatch):
    """AC10 (§1l/§1y forcing function): inject a factory whose gate_enabled
    returns False for HAL_SPEC_SCOPE_GATE while env is UNSET; the scope step
    must skip (data["spec_scope_inverse_skipped"]=="env_skip").

    Pre-GREEN: FAIL — the site reads os.environ.get("HAL_SPEC_SCOPE_GATE","1") == "0"
               directly. With env UNSET, `None != "0"` => the condition is False =>
               the skip branch is NOT taken => no "spec_scope_inverse_skipped" key.
    Post-GREEN: PASS — site calls `not get_config().gate_enabled("HAL_SPEC_SCOPE_GATE")`;
               factory returns False => `not False` => True => skip branch fires.

    §1i: factory reset is handled by the autouse _reset_factory_after_test fixture.
    """
    from config_provider import set_default_config_provider_factory  # type: ignore[import]
    from workflows.phase_45_spec import _verify_spec_scope_inverse  # type: ignore[import]
    from contracts import StepResult  # type: ignore[import]

    # Env UNSET — os.environ path would NOT skip; only provider path skips
    monkeypatch.delenv("HAL_SPEC_SCOPE_GATE", raising=False)

    class ScopeGateOffProvider:
        def gate_enabled(self, env_var: str) -> bool:
            # Return False for the scope gate → causes `not gate_enabled(...)` → True → skip
            if env_var == "HAL_SPEC_SCOPE_GATE":
                return False
            import os
            return os.environ.get(env_var) != "0"

    set_default_config_provider_factory(lambda: ScopeGateOffProvider())

    prev = StepResult(
        status="ok",
        data={"is_frozen": False, "cycle": 1},
        duration_ms=0,
        step_name="prev_step",
    )

    class _FakeCtx:
        org_config: dict[str, Any] = {}

    result = _verify_spec_scope_inverse(_FakeCtx(), prev)

    assert result.status == "ok", (
        f"Expected status='ok' when provider disables scope gate, got {result.status!r}. "
        "Pre-GREEN fail: site reads os.environ directly, env unset => gate doesn't skip."
    )
    assert isinstance(result.data, dict), (
        f"Expected dict data, got {type(result.data)!r}"
    )
    assert result.data.get("spec_scope_inverse_skipped") == "env_skip", (
        f"Expected data['spec_scope_inverse_skipped']=='env_skip' when provider returns "
        f"False for HAL_SPEC_SCOPE_GATE (env UNSET). "
        f"Pre-GREEN fail: site ignores the injected provider and reads os.environ. "
        f"Got data={result.data!r}"
    )
