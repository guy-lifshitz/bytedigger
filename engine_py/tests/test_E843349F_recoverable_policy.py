"""RED tests for E843349F — unified recoverable-retry policy + build-class-aware defaults.

Spec: SHARED/memory/Decisions/2026-05-20_E843349F_recoverable_retry_policy_spec.md

Design summary:
  - New module _recoverable_policy.py (workflows/) with policy matrix + resolve_policy()
  - New module recoverable_gate.py (lib/) with RecoverableGateMixin.gated_step_result()
  - Two new telemetry events: recoverable_policy_resolved, recoverable_gate_attempted

Expected pre-GREEN status: ALL 12 ACs MUST FAIL.
  - AC1-AC12: ModuleNotFoundError at import time (collection error).
  - Both new modules (_recoverable_policy, recoverable_gate) do not exist yet.

NOTE: Do NOT add try/except ImportError or pytest.importorskip.
The hard import failure is the desired RED state.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pytest

# ─── sys.path setup (match D3492E45 exemplar pattern exactly) ─────────────────

ENGINE_PY = Path(__file__).resolve().parents[1]
if str(ENGINE_PY) not in sys.path:
    sys.path.insert(0, str(ENGINE_PY))
WORKFLOWS = ENGINE_PY / "bytedigger_engine" / "workflows"
if str(WORKFLOWS) not in sys.path:
    sys.path.insert(0, str(WORKFLOWS))
LIB = ENGINE_PY / "bytedigger_engine" / "lib"
if str(LIB) not in sys.path:
    sys.path.insert(0, str(LIB))

# ─── Production imports — MUST FAIL pre-GREEN with ModuleNotFoundError ────────
# These imports are the RED state. Do NOT guard with try/except.
from bytedigger_engine.workflows._recoverable_policy import (  # noqa: E402
    resolve_policy,
    RecoverablePolicy,
    _DEFAULT_POLICY,
    _POLICY_MATRIX,
)
from bytedigger_engine.lib.recoverable_gate import RecoverableGateMixin  # noqa: E402

# contracts is already present (used for return-type assertions)
from bytedigger_engine.contracts import StepResult  # noqa: E402

# telemetry_ctx: existing tests use plain `import bytedigger_engine.telemetry_ctx` (no engine_py. prefix).
# sys.path includes engine_py/ so telemetry_ctx is importable directly.
# Spec §2.1 writes `from engine_py.telemetry_ctx import emit_safe` inside resolve_policy;
# GREEN must reconcile this with the sys.path convention. For patching, we set the
# attribute on the telemetry_ctx module object — both deferred and top-level imports
# resolve through sys.modules["bytedigger_engine.telemetry_ctx"] (or "engine_py.telemetry_ctx" if that
# alias is registered). We patch the module object directly after import.
from bytedigger_engine import telemetry_ctx as _telemetry_ctx  # noqa: E402

# Module-level aliases for monkeypatching emit_safe:
# - _rp_module: _recoverable_policy module (deferred import path; patching
#   _telemetry_ctx covers it since deferred `from ... import emit_safe` resolves
#   through the already-loaded module object in sys.modules).
# - _rg_module: recoverable_gate module (top-level import; patch _rg_module.emit_safe).
from bytedigger_engine.workflows import _recoverable_policy as _rp_module  # noqa: E402
from bytedigger_engine.lib import recoverable_gate as _rg_module  # noqa: E402


# ─── AC1: resolve_policy("SIMPLE", "green_lint") exact values ─────────────────


def test_e843349f_ac1_resolve_policy_simple_green_lint(monkeypatch):
    """AC1: resolve_policy("SIMPLE", "green_lint") returns
    RecoverablePolicy(slot="recoverable_twice", cycle_cap=2) exactly.

    Pre-GREEN: ModuleNotFoundError at collection (module doesn't exist).
    """
    captured: list[tuple[str, dict]] = []

    def _fake_emit(name: str, payload: dict, *args: Any, **kwargs: Any) -> None:
        captured.append((name, payload))

    monkeypatch.setattr(_telemetry_ctx, "emit_safe", _fake_emit)

    result = resolve_policy("SIMPLE", "green_lint")
    assert result == RecoverablePolicy(slot="recoverable_twice", cycle_cap=2), (
        f"AC1: expected RecoverablePolicy('recoverable_twice', 2), got {result!r}"
    )
    assert result.slot == "recoverable_twice", (
        f"AC1: slot must be 'recoverable_twice', got {result.slot!r}"
    )
    assert result.cycle_cap == 2, (
        f"AC1: cycle_cap must be 2, got {result.cycle_cap!r}"
    )


# ─── AC2: resolve_policy("FEATURE", "green_lint") exact values ────────────────


def test_e843349f_ac2_resolve_policy_feature_green_lint(monkeypatch):
    """AC2: resolve_policy("FEATURE", "green_lint") returns
    RecoverablePolicy(slot="recoverable_once", cycle_cap=1) exactly.

    Pre-GREEN: ModuleNotFoundError at collection.
    """
    captured: list[tuple[str, dict]] = []

    def _fake_emit(name: str, payload: dict, *args: Any, **kwargs: Any) -> None:
        captured.append((name, payload))

    monkeypatch.setattr(_telemetry_ctx, "emit_safe", _fake_emit)

    result = resolve_policy("FEATURE", "green_lint")
    assert result == RecoverablePolicy(slot="recoverable_once", cycle_cap=1), (
        f"AC2: expected RecoverablePolicy('recoverable_once', 1), got {result!r}"
    )
    assert result.slot == "recoverable_once", (
        f"AC2: slot must be 'recoverable_once', got {result.slot!r}"
    )
    assert result.cycle_cap == 1, (
        f"AC2: cycle_cap must be 1, got {result.cycle_cap!r}"
    )


# ─── AC3: resolve_policy("COMPLEX", "integrity") exact values ─────────────────


def test_e843349f_ac3_resolve_policy_complex_integrity_terminal(monkeypatch):
    """AC3: resolve_policy("COMPLEX", "integrity") returns
    RecoverablePolicy(slot="terminal", cycle_cap=0) exactly.

    Pre-GREEN: ModuleNotFoundError at collection.
    """
    captured: list[tuple[str, dict]] = []

    def _fake_emit(name: str, payload: dict, *args: Any, **kwargs: Any) -> None:
        captured.append((name, payload))

    monkeypatch.setattr(_telemetry_ctx, "emit_safe", _fake_emit)

    result = resolve_policy("COMPLEX", "integrity")
    assert result == RecoverablePolicy(slot="terminal", cycle_cap=0), (
        f"AC3: expected RecoverablePolicy('terminal', 0), got {result!r}"
    )
    assert result.slot == "terminal", (
        f"AC3: slot must be 'terminal', got {result.slot!r}"
    )
    assert result.cycle_cap == 0, (
        f"AC3: cycle_cap must be 0, got {result.cycle_cap!r}"
    )


# ─── AC4: All 18 matrix cells parametrized forcing function ───────────────────
#
# CRITICAL: Values are LITERAL TUPLES from spec §0a, NOT _POLICY_MATRIX.items().
# A stub returning _DEFAULT_POLICY for every input would silently pass a loop
# over _POLICY_MATRIX. Literal 18-tuple form is stub-immune.
#
# (build_class, gate, expected_slot, expected_cycle_cap)

_ALL_18_MATRIX_CELLS = [
    # green_lint
    ("SIMPLE",   "green_lint",           "recoverable_twice", 2),
    ("FEATURE",  "green_lint",           "recoverable_once",  1),
    ("COMPLEX",  "green_lint",           "recoverable_once",  1),
    # integrity
    ("SIMPLE",   "integrity",            "terminal",          0),
    ("FEATURE",  "integrity",            "terminal",          0),
    ("COMPLEX",  "integrity",            "terminal",          0),
    # watchdog_post_commit
    ("SIMPLE",   "watchdog_post_commit", "escalate",          0),
    ("FEATURE",  "watchdog_post_commit", "escalate",          0),
    ("COMPLEX",  "watchdog_post_commit", "escalate",          0),
    # review_timeout
    ("SIMPLE",   "review_timeout",       "recoverable_once",  1),
    ("FEATURE",  "review_timeout",       "recoverable_once",  1),
    ("COMPLEX",  "review_timeout",       "recoverable_twice", 2),
    # red_runtime — GH625 §2.5 recalibration: SIMPLE/FEATURE -> recoverable_once/1
    ("SIMPLE",   "red_runtime",          "recoverable_once",  1),
    ("FEATURE",  "red_runtime",          "recoverable_once",  1),
    ("COMPLEX",  "red_runtime",          "terminal",          0),
    # spec_retry — GH625 §2.5 recalibration: all classes -> recoverable_once/1
    ("SIMPLE",   "spec_retry",           "recoverable_once",  1),
    ("FEATURE",  "spec_retry",           "recoverable_once",  1),
    ("COMPLEX",  "spec_retry",           "recoverable_once",  1),
]

assert len(_ALL_18_MATRIX_CELLS) == 18, (
    f"AC4 forcing function: expected exactly 18 cells, got {len(_ALL_18_MATRIX_CELLS)}"
)


@pytest.mark.parametrize(
    "build_class,gate,expected_slot,expected_cycle_cap",
    _ALL_18_MATRIX_CELLS,
    ids=[f"{bc}-{g}" for bc, g, _s, _c in _ALL_18_MATRIX_CELLS],
)
def test_e843349f_ac4_all_18_matrix_cells(
    build_class: str,
    gate: str,
    expected_slot: str,
    expected_cycle_cap: int,
    monkeypatch,
):
    """AC4: PARAMETRIZED forcing function — all 18 matrix cells resolve to exactly
    the (slot, cycle_cap) declared in spec §0a.

    Literal 18-tuple values (NOT _POLICY_MATRIX.items()) make this stub-immune:
    a stub returning _DEFAULT_POLICY("recoverable_once", 1) for every input fails
    on terminal/escalate/recoverable_twice cells.

    Pre-GREEN: ModuleNotFoundError at collection.
    """
    captured: list[tuple[str, dict]] = []

    def _fake_emit(name: str, payload: dict, *args: Any, **kwargs: Any) -> None:
        captured.append((name, payload))

    monkeypatch.setattr(_telemetry_ctx, "emit_safe", _fake_emit)

    result = resolve_policy(build_class, gate)
    assert result.slot == expected_slot, (
        f"AC4 ({build_class}, {gate}): expected slot={expected_slot!r}, got {result.slot!r}"
    )
    assert result.cycle_cap == expected_cycle_cap, (
        f"AC4 ({build_class}, {gate}): expected cycle_cap={expected_cycle_cap}, "
        f"got {result.cycle_cap!r}"
    )


# ─── AC5: unknown build_class falls back to SIMPLE ────────────────────────────


def test_e843349f_ac5_unknown_build_class_falls_back_to_simple(monkeypatch):
    """AC5: resolve_policy("UNKNOWN_CLASS", "green_lint") returns same result as
    resolve_policy("SIMPLE", "green_lint").

    Per spec §2.1: unknown build_class is normalised to "SIMPLE".
    Pre-GREEN: ModuleNotFoundError at collection.
    """
    captured: list[tuple[str, dict]] = []

    def _fake_emit(name: str, payload: dict, *args: Any, **kwargs: Any) -> None:
        captured.append((name, payload))

    monkeypatch.setattr(_telemetry_ctx, "emit_safe", _fake_emit)

    result_unknown = resolve_policy("UNKNOWN_CLASS", "green_lint")
    result_simple = resolve_policy("SIMPLE", "green_lint")
    assert result_unknown == result_simple, (
        f"AC5: unknown build_class should fall back to SIMPLE behavior. "
        f"Got {result_unknown!r}, expected {result_simple!r}"
    )
    assert result_unknown.slot == "recoverable_twice", (
        f"AC5: fallback slot must be 'recoverable_twice' (SIMPLE/green_lint), "
        f"got {result_unknown.slot!r}"
    )
    assert result_unknown.cycle_cap == 2, (
        f"AC5: fallback cycle_cap must be 2 (SIMPLE/green_lint), "
        f"got {result_unknown.cycle_cap!r}"
    )


# ─── AC6: unknown gate returns _DEFAULT_POLICY ────────────────────────────────


def test_e843349f_ac6_unknown_gate_returns_default_policy(monkeypatch):
    """AC6: resolve_policy("SIMPLE", "unknown_gate_xyz") returns _DEFAULT_POLICY
    which equals RecoverablePolicy("recoverable_once", 1).

    Per spec §2.1: unknown gate returns _DEFAULT_POLICY.
    Pre-GREEN: ModuleNotFoundError at collection.
    """
    captured: list[tuple[str, dict]] = []

    def _fake_emit(name: str, payload: dict, *args: Any, **kwargs: Any) -> None:
        captured.append((name, payload))

    monkeypatch.setattr(_telemetry_ctx, "emit_safe", _fake_emit)

    result = resolve_policy("SIMPLE", "unknown_gate_xyz")
    assert result == _DEFAULT_POLICY, (
        f"AC6: unknown gate should return _DEFAULT_POLICY={_DEFAULT_POLICY!r}, "
        f"got {result!r}"
    )
    assert _DEFAULT_POLICY == RecoverablePolicy("recoverable_once", 1), (
        f"AC6: _DEFAULT_POLICY must equal RecoverablePolicy('recoverable_once', 1), "
        f"got {_DEFAULT_POLICY!r}"
    )
    assert result.slot == "recoverable_once", (
        f"AC6: default slot must be 'recoverable_once', got {result.slot!r}"
    )
    assert result.cycle_cap == 1, (
        f"AC6: default cycle_cap must be 1, got {result.cycle_cap!r}"
    )


# ─── AC7: gated_step_result recoverable_twice cycle=1 → retry ────────────────


def test_e843349f_ac7_gated_step_result_recoverable_twice_cycle1_retry(monkeypatch):
    """AC7: gated_step_result(build_class="SIMPLE", gate="green_lint", cycle=1,
    retry_from_step_idx=5, ..., forwarded_data={"k": "v"}) at slot=recoverable_twice →
    status="error", recoverable=True, data["retry_from_step"]==5, data["cycle_count"]==1,
    data["k"]=="v" (forwarded data preserved).

    Pre-GREEN: ModuleNotFoundError at collection.
    """
    captured: list[tuple[str, dict]] = []

    def _fake_emit(name: str, payload: dict, *args: Any, **kwargs: Any) -> None:
        captured.append((name, payload))

    monkeypatch.setattr(_telemetry_ctx, "emit_safe", _fake_emit)

    result = RecoverableGateMixin.gated_step_result(
        build_class="SIMPLE",
        gate="green_lint",
        cycle=1,
        retry_from_step_idx=5,
        error_code="E_GREEN_LINT_RETRY",
        error_msg="lint failed",
        step_name="verify_green",
        forwarded_data={"k": "v"},
    )
    assert isinstance(result, StepResult), (
        f"AC7: expected StepResult instance, got {type(result)!r}"
    )
    assert result.status == "error", (
        f"AC7: expected status='error', got {result.status!r}"
    )
    assert result.recoverable is True, (
        f"AC7: expected recoverable=True (cycle=1 < cap=2), got {result.recoverable!r}"
    )
    assert isinstance(result.data, dict), (
        f"AC7: expected data to be a dict, got {type(result.data)!r}"
    )
    assert result.data["retry_from_step"] == 5, (
        f"AC7: expected data['retry_from_step']==5, got {result.data.get('retry_from_step')!r}"
    )
    assert result.data["cycle_count"] == 1, (
        f"AC7: expected data['cycle_count']==1, got {result.data.get('cycle_count')!r}"
    )
    assert result.data.get("k") == "v", (
        f"AC7: forwarded_data key 'k'='v' must be preserved in result.data, "
        f"got {result.data.get('k')!r}"
    )


# ─── AC8: gated_step_result cap reached → terminal (recoverable=False) ────────


def test_e843349f_ac8_gated_step_result_cap_reached_terminal(monkeypatch):
    """AC8: gated_step_result(build_class="SIMPLE", gate="green_lint", cycle=2, ...)
    at slot=recoverable_twice, cycle==cap → status="error", recoverable=False,
    error_code uses terminal_error_code if provided.

    cycle=2 >= cycle_cap=2 triggers cap path.
    Pre-GREEN: ModuleNotFoundError at collection.
    """
    captured: list[tuple[str, dict]] = []

    def _fake_emit(name: str, payload: dict, *args: Any, **kwargs: Any) -> None:
        captured.append((name, payload))

    monkeypatch.setattr(_telemetry_ctx, "emit_safe", _fake_emit)

    # GH625: cap decision now keyed on per-gate gate_attempts, not raw cycle.
    # Seed 2 prior attempts (== green_lint SIMPLE cap) so this stays a cap case.
    result = RecoverableGateMixin.gated_step_result(
        build_class="SIMPLE",
        gate="green_lint",
        cycle=2,
        retry_from_step_idx=5,
        error_code="E_GREEN_LINT_RETRY",
        error_msg="lint failed at cap",
        step_name="verify_green",
        forwarded_data={"x": 42, "gate_attempts": {"green_lint": 2}},
        terminal_error_code="E_GREEN_LINT_FAIL_CAP2",
    )
    assert isinstance(result, StepResult), (
        f"AC8: expected StepResult instance, got {type(result)!r}"
    )
    assert result.status == "error", (
        f"AC8: expected status='error' at cap, got {result.status!r}"
    )
    assert result.recoverable is False, (
        f"AC8: expected recoverable=False (cap reached: cycle=2 >= cap=2), "
        f"got {result.recoverable!r}"
    )
    assert result.error_code == "E_GREEN_LINT_FAIL_CAP2", (
        f"AC8: expected error_code='E_GREEN_LINT_FAIL_CAP2' (terminal_error_code), "
        f"got {result.error_code!r}"
    )


# ─── AC9: gated_step_result terminal slot → always recoverable=False ──────────


def test_e843349f_ac9_gated_step_result_terminal_slot(monkeypatch):
    """AC9: gated_step_result(build_class="SIMPLE", gate="integrity", cycle=1, ...)
    at slot=terminal → status="error", recoverable=False (cycle is ignored for terminal).

    Pre-GREEN: ModuleNotFoundError at collection.
    """
    captured: list[tuple[str, dict]] = []

    def _fake_emit(name: str, payload: dict, *args: Any, **kwargs: Any) -> None:
        captured.append((name, payload))

    monkeypatch.setattr(_telemetry_ctx, "emit_safe", _fake_emit)

    result = RecoverableGateMixin.gated_step_result(
        build_class="SIMPLE",
        gate="integrity",
        cycle=1,
        retry_from_step_idx=0,
        error_code="E_INTEGRITY_ASSERTION_GAMING",
        error_msg="integrity violated",
        step_name="integrity_check",
        forwarded_data={},
    )
    assert isinstance(result, StepResult), (
        f"AC9: expected StepResult instance, got {type(result)!r}"
    )
    assert result.status == "error", (
        f"AC9: terminal slot must produce status='error', got {result.status!r}"
    )
    assert result.recoverable is False, (
        f"AC9: terminal slot must produce recoverable=False (cycle ignored), "
        f"got {result.recoverable!r}"
    )


# ─── AC10: gated_step_result escalate slot → status="escalate" ────────────────


def test_e843349f_ac10_gated_step_result_escalate_slot(monkeypatch):
    """AC10: gated_step_result(build_class="SIMPLE", gate="watchdog_post_commit", cycle=1, ...)
    at slot=escalate → status="escalate", recoverable=False.

    Pre-GREEN: ModuleNotFoundError at collection.
    """
    captured: list[tuple[str, dict]] = []

    def _fake_emit(name: str, payload: dict, *args: Any, **kwargs: Any) -> None:
        captured.append((name, payload))

    monkeypatch.setattr(_telemetry_ctx, "emit_safe", _fake_emit)

    result = RecoverableGateMixin.gated_step_result(
        build_class="SIMPLE",
        gate="watchdog_post_commit",
        cycle=1,
        retry_from_step_idx=0,
        error_code="E_WATCHDOG_POST_COMMIT",
        error_msg="code already committed — escalate",
        step_name="watchdog",
        forwarded_data={},
    )
    assert isinstance(result, StepResult), (
        f"AC10: expected StepResult instance, got {type(result)!r}"
    )
    assert result.status == "escalate", (
        f"AC10: escalate slot must produce status='escalate', got {result.status!r}"
    )
    assert result.recoverable is False, (
        f"AC10: escalate slot must produce recoverable=False, got {result.recoverable!r}"
    )


# ─── AC11: resolve_policy emits recoverable_policy_resolved event ─────────────


def test_e843349f_ac11_resolve_policy_emits_event(monkeypatch):
    """AC11: resolve_policy(...) emits exactly one 'recoverable_policy_resolved' event
    with payload keys exactly == {"build_class", "gate", "slot", "cycle_cap"}.

    Per spec §1o: fire-and-forget observability, no dispatch.
    Pre-GREEN: ModuleNotFoundError at collection.
    """
    captured: list[tuple[str, dict]] = []

    def _fake_emit(name: str, payload: dict, *args: Any, **kwargs: Any) -> None:
        captured.append((name, payload))

    monkeypatch.setattr(_telemetry_ctx, "emit_safe", _fake_emit)

    resolve_policy("SIMPLE", "green_lint")

    policy_events = [(n, p) for n, p in captured if n == "recoverable_policy_resolved"]
    assert len(policy_events) == 1, (
        f"AC11: expected exactly 1 'recoverable_policy_resolved' event, "
        f"got {len(policy_events)}. "
        f"Pre-GREEN: event not emitted (module + emit_safe don't exist yet)."
    )
    payload = policy_events[0][1]
    expected_keys = {"build_class", "gate", "slot", "cycle_cap"}
    assert set(payload.keys()) == expected_keys, (
        f"AC11: recoverable_policy_resolved payload keys must be exactly {expected_keys}, "
        f"got {set(payload.keys())!r}"
    )
    assert payload["build_class"] == "SIMPLE", (
        f"AC11: expected build_class='SIMPLE', got {payload['build_class']!r}"
    )
    assert payload["gate"] == "green_lint", (
        f"AC11: expected gate='green_lint', got {payload['gate']!r}"
    )
    assert payload["slot"] == "recoverable_twice", (
        f"AC11: expected slot='recoverable_twice', got {payload['slot']!r}"
    )
    assert payload["cycle_cap"] == 2, (
        f"AC11: expected cycle_cap=2, got {payload['cycle_cap']!r}"
    )


# ─── AC12: gated_step_result emits recoverable_gate_attempted for all 4 outcomes


@pytest.mark.parametrize(
    "build_class,gate,cycle,expected_outcome,expected_status",
    [
        # retry: SIMPLE/green_lint cycle=1 < cap=2
        ("SIMPLE",  "green_lint",           1, "retry",    "error"),
        # cap: SIMPLE/green_lint cycle=2 >= cap=2
        ("SIMPLE",  "green_lint",           2, "cap",      "error"),
        # terminal: SIMPLE/integrity (slot=terminal)
        ("SIMPLE",  "integrity",            1, "terminal", "error"),
        # escalate: SIMPLE/watchdog_post_commit (slot=escalate)
        ("SIMPLE",  "watchdog_post_commit", 1, "escalate", "escalate"),
    ],
    ids=["retry", "cap", "terminal", "escalate"],
)
def test_e843349f_ac12_gated_step_result_emits_event(
    build_class: str,
    gate: str,
    cycle: int,
    expected_outcome: str,
    expected_status: str,
    monkeypatch,
):
    """AC12: gated_step_result emits exactly one 'recoverable_gate_attempted' event per call.
    The event payload must include key 'outcome' ∈ {"retry", "cap", "terminal", "escalate"}.
    Parametrized over all 4 distinct outcome values (forcing function: all 4 must be reachable).

    Per spec §1o: fire-and-forget observability.
    Pre-GREEN: ModuleNotFoundError at collection.
    """
    captured: list[tuple[str, dict]] = []

    def _fake_emit(name: str, payload: dict, *args: Any, **kwargs: Any) -> None:
        captured.append((name, payload))

    monkeypatch.setattr(_telemetry_ctx, "emit_safe", _fake_emit)

    # GH625: cap decision now keyed on per-gate gate_attempts, not raw cycle.
    # Seed matching attempts only for the "cap" case so it still hits the cap.
    fwd: dict[str, Any] = (
        {"gate_attempts": {gate: cycle}} if expected_outcome == "cap" else {}
    )
    result = RecoverableGateMixin.gated_step_result(
        build_class=build_class,
        gate=gate,
        cycle=cycle,
        retry_from_step_idx=3,
        error_code="E_TEST_CODE",
        error_msg="test message",
        step_name="test_step",
        forwarded_data=fwd,
        terminal_error_code="E_TEST_TERMINAL",
    )
    assert isinstance(result, StepResult), (
        f"AC12 ({expected_outcome}): expected StepResult, got {type(result)!r}"
    )
    assert result.status == expected_status, (
        f"AC12 ({expected_outcome}): expected status={expected_status!r}, "
        f"got {result.status!r}"
    )

    gate_events = [(n, p) for n, p in captured if n == "recoverable_gate_attempted"]
    assert len(gate_events) == 1, (
        f"AC12 ({expected_outcome}): expected exactly 1 'recoverable_gate_attempted' event, "
        f"got {len(gate_events)}. "
        f"Pre-GREEN: event not emitted (module + emit_safe don't exist yet)."
    )
    payload = gate_events[0][1]
    assert "outcome" in payload, (
        f"AC12 ({expected_outcome}): 'outcome' key must be present in "
        f"recoverable_gate_attempted payload. Got keys: {set(payload.keys())!r}"
    )
    assert payload["outcome"] == expected_outcome, (
        f"AC12 ({expected_outcome}): expected outcome={expected_outcome!r} in payload, "
        f"got {payload['outcome']!r}"
    )
    valid_outcomes = {"retry", "cap", "terminal", "escalate"}
    assert payload["outcome"] in valid_outcomes, (
        f"AC12: outcome must be one of {valid_outcomes}, got {payload['outcome']!r}"
    )
