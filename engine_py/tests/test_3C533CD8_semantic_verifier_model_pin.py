"""RED tests for 3C533CD8 — semantic_verifier model-pin fail-closed.

ACs covered:
  AC1 — opus-tier: the chokepoint is called with model == get_claude_critical() alias
  AC2 — haiku-tier: the chokepoint is called with model == get_claude_fallback() alias
  AC3 — fail-closed: stale versioned id raises ValueError BEFORE the chokepoint is called
  AC4 — _ACCEPTED_MODEL_ALIASES == frozenset({"opus","sonnet","haiku"})

bd#82 PR3: the verifier no longer spawns `claude -p` through bounded_run; it calls
llm_subprocess.invoke_llm_subprocess, so these ACs read the chokepoint's `model`
keyword instead of the argv passed to bounded_run. The assertions are unchanged.

Pre-GREEN FAIL reasoning (per §1l / stub-passability):
  AC1/AC2: assert the chokepoint receives model=<alias>. If the
  production code used a hardcoded versioned id (e.g. "claude-opus-4-7"), the assert
  would fail because the received id != get_claude_critical(). Fails today only if prod
  regresses to hardcoded id.
  AC3: assert ValueError raised AND the chokepoint not called when get_claude_critical()
  returns a stale id. Fails if fail-closed guard is absent from prod.
  AC4: assert alias set membership contract. Fails if the constant diverges.

NOTE (sys.path): this test file follows the grandfathered module-level sys.path pattern
used by all sibling tests in this directory (test_semantic_verifier_W15.py,
test_semantic_verifier_F60FED11.py) which pre-date the suite_safety scanner. This is
an Option-D (manual) pipeline — phase_5 / scan_suite_safety does not run; the
orchestrator invokes pytest directly. The conftest singleton does not expose
lib/plugins/anti_hallucination/ so the path extension is required here.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

HERE = Path(__file__).parent
ENGINE_ROOT = HERE.parent
_PLUGIN_PATH = str(ENGINE_ROOT / "bytedigger_engine/lib" / "plugins" / "anti_hallucination")

# Module-level path extension — consistent with sibling tests in this dir.
# Does NOT use spec_from_file_location or exec_module (§1q compliant).

# Top-level imports only of already-existing symbols.
from bytedigger_engine.lib.plugins.anti_hallucination import semantic_verifier  # noqa: E402
from bytedigger_engine.lib.plugins.anti_hallucination.semantic_verifier import (  # noqa: E402
    _ACCEPTED_MODEL_ALIASES,
    _invoke_verifier_agent,
)
from bytedigger_engine.lib import model_config  # noqa: E402  # accessed via module attr to avoid import+patch conflict


# ---------------------------------------------------------------------------
# Shared fixture: a minimal finding dict accepted by _invoke_verifier_agent.
# ---------------------------------------------------------------------------

_MINIMAL_FINDING = {
    "severity": "HIGH",
    "file": "src/engine.py",
    "line": "42",
    "quote": "parents[4]",
    "claim": "index out of bounds on depth-3 path",
}

# Benign chokepoint result: ok with a verdict in raw_response.
def _make_invoke_mock() -> MagicMock:
    from bytedigger_engine.contracts import StepResult

    return MagicMock(return_value=StepResult(
        status="ok", data={"raw_response": "REFUTED:\nreason: no bug\nrationale: code is fine\n"},
        duration_ms=0, step_name="semantic_verify",
    ))


# ---------------------------------------------------------------------------
# AC1 — opus-tier passes --model == get_claude_critical()
# ---------------------------------------------------------------------------

def test_ac1_opus_tier_uses_get_claude_critical() -> None:
    """AC1: _invoke_verifier_agent with model_tier='opus' must call the chokepoint
    with model=<value returned by get_claude_critical()>.

    That value must also be in _ACCEPTED_MODEL_ALIASES (alias, not versioned id).

    Pre-GREEN FAIL: if prod hardcodes a versioned id like 'claude-opus-4-7' instead
    of calling get_claude_critical(), the model value != the alias, assertion fails.
    """
    br_mock = _make_invoke_mock()
    expected_model = semantic_verifier.get_claude_critical()

    with patch.object(semantic_verifier.llm_subprocess, "invoke_llm_subprocess", br_mock):
        _invoke_verifier_agent(_MINIMAL_FINDING, model_tier="opus")

    assert br_mock.called, (
        "AC1: the chokepoint was not called at all for model_tier='opus'"
    )

    actual_model = br_mock.call_args.kwargs["model"]

    assert actual_model == expected_model, (
        f"AC1: model value {actual_model!r} != get_claude_critical() {expected_model!r}. "
        "Production code must use get_claude_critical(), not a hardcoded versioned id."
    )
    assert actual_model in _ACCEPTED_MODEL_ALIASES, (
        f"AC1: model value {actual_model!r} is not in _ACCEPTED_MODEL_ALIASES "
        f"{sorted(_ACCEPTED_MODEL_ALIASES)} — must be an alias, not a versioned id."
    )


# ---------------------------------------------------------------------------
# AC2 — haiku-tier passes --model == get_claude_fallback()
# ---------------------------------------------------------------------------

def test_ac2_haiku_tier_uses_get_claude_fallback() -> None:
    """AC2: _invoke_verifier_agent with model_tier='haiku' must call the chokepoint
    with model=<value returned by get_claude_fallback()>.

    That value must also be in _ACCEPTED_MODEL_ALIASES.

    Pre-GREEN FAIL: if prod hardcodes a versioned id or uses get_claude_critical()
    unconditionally, the model value != get_claude_fallback() alias.
    """
    br_mock = _make_invoke_mock()
    expected_model = semantic_verifier.get_claude_fallback()

    with patch.object(semantic_verifier.llm_subprocess, "invoke_llm_subprocess", br_mock):
        _invoke_verifier_agent(_MINIMAL_FINDING, model_tier="haiku")

    assert br_mock.called, (
        "AC2: the chokepoint was not called at all for model_tier='haiku'"
    )

    actual_model = br_mock.call_args.kwargs["model"]

    assert actual_model == expected_model, (
        f"AC2: model value {actual_model!r} != get_claude_fallback() {expected_model!r}. "
        "Production code must use get_claude_fallback() for non-opus tier."
    )
    assert actual_model in _ACCEPTED_MODEL_ALIASES, (
        f"AC2: model value {actual_model!r} is not in _ACCEPTED_MODEL_ALIASES "
        f"{sorted(_ACCEPTED_MODEL_ALIASES)} — must be an alias, not a versioned id."
    )


# ---------------------------------------------------------------------------
# AC3 — fail-closed: stale versioned id raises ValueError before the chokepoint
# ---------------------------------------------------------------------------

def test_ac3_fail_closed_stale_id_raises_before_spawn() -> None:
    """AC3: when get_claude_critical() returns a stale versioned id (e.g.
    'claude-opus-4-7') that is NOT in _ACCEPTED_MODEL_ALIASES, _invoke_verifier_agent
    must raise ValueError BEFORE spawning (the chokepoint must NOT be called).

    This is the fail-closed guard: an unrecognised model value would be silently
    downgraded by the CLI to the session default (the 2026-06-20 runaway incident).
    Refusing to spawn is the only safe behaviour.

    Pre-GREEN FAIL: if the fail-closed guard is absent, the function spawns with the
    stale id (no ValueError) and the chokepoint IS called — both assertions fail.
    """
    import pytest

    br_mock = _make_invoke_mock()
    stale_id = "claude-opus-4-7"

    # Verify the stale id is indeed NOT in the alias set (test pre-condition).
    assert stale_id not in _ACCEPTED_MODEL_ALIASES, (
        f"Test pre-condition broken: {stale_id!r} is unexpectedly in "
        f"_ACCEPTED_MODEL_ALIASES {sorted(_ACCEPTED_MODEL_ALIASES)}"
    )

    with patch.object(semantic_verifier.llm_subprocess, "invoke_llm_subprocess", br_mock):
        with patch.object(semantic_verifier, "get_claude_critical", return_value=stale_id):
            with pytest.raises(ValueError) as exc_info:
                _invoke_verifier_agent(_MINIMAL_FINDING, model_tier="opus")

    assert not br_mock.called, (
        "AC3: the chokepoint was called even though model id is not an accepted alias. "
        "The fail-closed guard must raise ValueError BEFORE spawning, not after."
    )
    assert stale_id in str(exc_info.value), (
        f"AC3: ValueError message should mention the rejected id {stale_id!r}; "
        f"got: {exc_info.value!r}"
    )


# ---------------------------------------------------------------------------
# AC4 — _ACCEPTED_MODEL_ALIASES contract
# ---------------------------------------------------------------------------

def test_ac4_accepted_model_aliases_contract() -> None:
    """AC4: _ACCEPTED_MODEL_ALIASES must be exactly frozenset({'opus','sonnet','haiku'}).

    This pins the alias contract: anything outside this set is rejected fail-closed
    (AC3). Adding a versioned id to this set would re-open the runaway vector.

    Pre-GREEN FAIL: if the constant is absent, wrong type, or contains unexpected
    members (e.g. includes 'claude-opus-4-7'), this assertion fails.
    """
    expected = frozenset({"opus", "sonnet", "haiku"})

    assert isinstance(_ACCEPTED_MODEL_ALIASES, frozenset), (
        f"AC4: _ACCEPTED_MODEL_ALIASES must be a frozenset; "
        f"got {type(_ACCEPTED_MODEL_ALIASES).__name__!r}"
    )
    assert _ACCEPTED_MODEL_ALIASES == expected, (
        f"AC4: _ACCEPTED_MODEL_ALIASES must be exactly {sorted(expected)}; "
        f"got {sorted(_ACCEPTED_MODEL_ALIASES)}"
    )
