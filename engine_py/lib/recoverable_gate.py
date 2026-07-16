"""RecoverableGateMixin — producer-side helper to emit retry StepResult shapes
aligned with the policy matrix (E843349F).

Place at engine_py/lib/recoverable_gate.py so RED test's sys.path insert of
engine_py/lib makes it importable as bare `import recoverable_gate`.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

# Sibling-module imports: use sys.path-rooted bare names, not engine_py-prefixed.
# This matches the codebase convention (per sibling tests + RED file lines 27-35).
_WORKFLOWS = Path(__file__).resolve().parent.parent / "workflows"
if str(_WORKFLOWS) not in sys.path:
    sys.path.insert(0, str(_WORKFLOWS))

from contracts import StepResult  # noqa: E402
from _recoverable_policy import resolve_policy, RecoverablePolicy  # noqa: E402
import telemetry_ctx  # attribute-lookup idiom so monkeypatch.setattr works  # noqa: E402

__all__ = ["RecoverableGateMixin"]


class RecoverableGateMixin:
    """Mixin (intentionally stateless — methods are @staticmethod) providing
    the canonical retry-StepResult factory.

    Producers call gated_step_result(...) to translate
    (build_class, gate, cycle, retry_from_step_idx, error_code,
     error_msg, data_payload) -> StepResult with correct
    (status, recoverable, cycle_count) per policy."""

    @staticmethod
    def gated_step_result(
        *,
        build_class: str | None,
        gate: str,
        cycle: int,                         # 1-indexed current cycle (1 = first try)
        retry_from_step_idx: int,
        error_code: str,
        error_msg: str,
        step_name: str,
        forwarded_data: dict[str, Any],
        terminal_error_code: str | None = None,  # used at cap; if None, reuses error_code
        terminal_error_msg: str | None = None,   # used at terminal/escalate/cap; if None, reuses error_msg
    ) -> StepResult:
        """Build and return a StepResult shaped per policy matrix.

        Outcome mapping:
          terminal slot     -> status="error",    recoverable=False
          escalate slot     -> status="escalate", recoverable=False
          cycle >= cycle_cap -> status="error",    recoverable=False  (cap)
          else              -> status="error",    recoverable=True   (retry)
        """
        pol = resolve_policy(build_class, gate)
        bc_upper = (build_class or "SIMPLE").upper()
        _terminal_msg = terminal_error_msg if terminal_error_msg is not None else error_msg

        # GH625: per-gate attempt accounting — a gate's own spend, not the
        # shared cycle counter (which is eaten by the GREEN test-red loop
        # and replay), decides cap for recoverable slots.
        ga = forwarded_data.get("gate_attempts")
        attempts = int(ga.get(gate, 0)) if isinstance(ga, dict) else 0

        if pol.slot == "terminal":
            outcome = "terminal"
            sr = StepResult(
                status="error",
                data=forwarded_data,
                duration_ms=0,
                step_name=step_name,
                error=_terminal_msg,
                error_code=terminal_error_code or error_code,
                recoverable=False,
            )
        elif pol.slot == "escalate":
            outcome = "escalate"
            sr = StepResult(
                status="escalate",
                data=forwarded_data,
                duration_ms=0,
                step_name=step_name,
                error=_terminal_msg,
                error_code=terminal_error_code or error_code,
                recoverable=False,
            )
        elif attempts >= pol.cycle_cap:
            outcome = "cap"
            sr = StepResult(
                status="error",
                data=forwarded_data,
                duration_ms=0,
                step_name=step_name,
                error=_terminal_msg,
                error_code=terminal_error_code or error_code,
                recoverable=False,
            )
        else:
            outcome = "retry"
            retry_data = {
                **forwarded_data,
                "retry_from_step": retry_from_step_idx,
                "cycle_count": cycle,
                "gate_attempts": {**(ga or {}), gate: attempts + 1},
                "gate_budget_ok": True,
            }
            sr = StepResult(
                status="error",
                data=retry_data,
                duration_ms=0,
                step_name=step_name,
                error=error_msg,
                error_code=error_code,
                recoverable=True,
            )

        # Emit gate-attempt event after constructing StepResult
        # payload 7 keys (GH625 adds "attempts" — supersedes the E843349F
        # 6-key freeze; per-gate accounting needs the spend visible here)
        telemetry_ctx.emit_safe(
            "recoverable_gate_attempted",
            {
                "gate": gate,
                "build_class": bc_upper,
                "slot": pol.slot,
                "cycle": cycle,
                "cycle_cap": pol.cycle_cap,
                "outcome": outcome,
                "attempts": attempts,
            },
        )
        return sr
