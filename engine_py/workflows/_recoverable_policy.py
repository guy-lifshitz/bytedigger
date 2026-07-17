"""Build-class-aware recoverable-retry policy matrix (E843349F).

Single source of truth for the (build_class, gate_name) -> policy mapping.
Pure-data module: no I/O, no side effects beyond telemetry emission.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import telemetry_ctx  # attribute-lookup idiom so monkeypatch.setattr works in tests

__all__ = ["resolve_policy", "RecoverablePolicy", "_DEFAULT_POLICY", "_POLICY_MATRIX"]

Slot = Literal["terminal", "recoverable_once", "recoverable_twice", "escalate"]
BuildClass = Literal["SIMPLE", "FEATURE", "COMPLEX"]
Gate = Literal[
    "green_lint",
    "green_typecheck",
    "integrity",
    "watchdog_post_commit",
    "review_timeout",
    "red_runtime",
    "spec_retry",
    "red_lint_preflight",
    "validation_execution",
]


@dataclass(frozen=True)
class RecoverablePolicy:
    slot: Slot
    cycle_cap: int  # 0 for terminal/escalate; 1 for recoverable_once; 2 for recoverable_twice
    # GH625: cycle_cap = real retries granted under GH625 attempts-based
    # accounting (per-gate gate_attempts, not the shared cycle counter)


_POLICY_MATRIX: dict[tuple[BuildClass, Gate], RecoverablePolicy] = {
    # green_lint: SIMPLE gets cap-2 (high FP); FEATURE/COMPLEX get cap-1 (cost matters)
    ("SIMPLE",   "green_lint"):           RecoverablePolicy("recoverable_twice", 2),
    ("FEATURE",  "green_lint"):           RecoverablePolicy("recoverable_once",  1),
    ("COMPLEX",  "green_lint"):           RecoverablePolicy("recoverable_once",  1),
    # green_typecheck: mirrors green_lint caps (34AEB235 deterministic mypy gate)
    ("SIMPLE",   "green_typecheck"):      RecoverablePolicy("recoverable_twice", 2),
    ("FEATURE",  "green_typecheck"):      RecoverablePolicy("recoverable_once",  1),
    ("COMPLEX",  "green_typecheck"):      RecoverablePolicy("recoverable_once",  1),
    # integrity: terminal-always (assertion-gaming abort)
    ("SIMPLE",   "integrity"):            RecoverablePolicy("terminal",          0),
    ("FEATURE",  "integrity"):            RecoverablePolicy("terminal",          0),
    ("COMPLEX",  "integrity"):            RecoverablePolicy("terminal",          0),
    # watchdog_post_commit: escalate (code already committed — human review required)
    ("SIMPLE",   "watchdog_post_commit"): RecoverablePolicy("escalate",          0),
    ("FEATURE",  "watchdog_post_commit"): RecoverablePolicy("escalate",          0),
    ("COMPLEX",  "watchdog_post_commit"): RecoverablePolicy("escalate",          0),
    # review_timeout: LLM transient retry; COMPLEX gets extra retry
    ("SIMPLE",   "review_timeout"):       RecoverablePolicy("recoverable_once",  1),
    ("FEATURE",  "review_timeout"):       RecoverablePolicy("recoverable_once",  1),
    ("COMPLEX",  "review_timeout"):       RecoverablePolicy("recoverable_twice", 2),
    # red_runtime: fixture bug — SIMPLE/FEATURE get a one-retry budget (a
    # fresh RED LLM pass can fix a collect failure); COMPLEX surfaces
    # immediately (terminal), no retry. GH625: attempts-based accounting
    # makes cycle_cap the real per-gate retry count, so SIMPLE/FEATURE drop
    # to recoverable_once (1) — one real retry, matching the stated budget.
    ("SIMPLE",   "red_runtime"):          RecoverablePolicy("recoverable_once",  1),
    ("FEATURE",  "red_runtime"):          RecoverablePolicy("recoverable_once",  1),
    ("COMPLEX",  "red_runtime"):          RecoverablePolicy("terminal",          0),
    # spec_retry: all classes get recoverable_once (1) — GH625 attempts-based
    # accounting means cycle_cap is now the real retry budget (no longer
    # inflated to offset the shared cycle counter); one real retry per class.
    ("SIMPLE",   "spec_retry"):           RecoverablePolicy("recoverable_once",  1),
    ("FEATURE",  "spec_retry"):           RecoverablePolicy("recoverable_once",  1),
    ("COMPLEX",  "spec_retry"):           RecoverablePolicy("recoverable_once",  1),
    # red_lint_preflight: terminal RED-lint findings (stub/1q/suite/collect-probe)
    # get a findings-driven delta-retry budget before capping (GH602).
    ("SIMPLE",   "red_lint_preflight"):   RecoverablePolicy("recoverable_twice", 2),
    ("FEATURE",  "red_lint_preflight"):   RecoverablePolicy("recoverable_twice", 2),
    ("COMPLEX",  "red_lint_preflight"):   RecoverablePolicy("recoverable_twice", 2),
    # validation_execution: GH963 — validator self-reported non-execution
    # (zero tool calls / inputs not read). One bounded auto-retry on a fresh
    # subprocess for all build classes (issue's explicit "auto-retry once"
    # ask); a second execution failure is infra, not a test gap -> terminal.
    ("SIMPLE",   "validation_execution"): RecoverablePolicy("recoverable_once",  1),
    ("FEATURE",  "validation_execution"): RecoverablePolicy("recoverable_once",  1),
    ("COMPLEX",  "validation_execution"): RecoverablePolicy("recoverable_once",  1),
}

_DEFAULT_POLICY = RecoverablePolicy("recoverable_once", 1)


def resolve_policy(build_class: str | None, gate: str) -> RecoverablePolicy:
    """Return policy for (build_class, gate).

    Unknown build_class falls back to SIMPLE.
    Unknown gate returns _DEFAULT_POLICY.
    Emits recoverable_policy_resolved telemetry event on every call.
    """
    bc = (build_class or "SIMPLE").upper()
    if bc not in ("SIMPLE", "FEATURE", "COMPLEX"):
        bc = "SIMPLE"
    pol = _POLICY_MATRIX.get((bc, gate), _DEFAULT_POLICY)  # type: ignore[arg-type]
    # payload shape EXACTLY 4 keys per E843349F AC11 — do not add fields
    telemetry_ctx.emit_safe(
        "recoverable_policy_resolved",
        {"build_class": bc, "gate": gate, "slot": pol.slot, "cycle_cap": pol.cycle_cap},
    )
    return pol
