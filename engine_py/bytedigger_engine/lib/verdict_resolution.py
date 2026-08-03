"""Shared fail-closed verdict resolution (GH373 Part B, Q4 fix).

Spec: the state dir's memory/Decisions/2026-07-07_GH373_authored_boundary_verdict_spec.md
§2 Part B. Verdict-vocabulary-agnostic — callers pass plain strings (SHIP/REVISE,
PASS/FAIL, etc). resolve_gate_verdict is the single chokepoint for reconciling a
"structured" signal (e.g. a parsed findings block) against a "prose" signal
(e.g. a freeform ## Verdict heading) with a fail-closed default when they
disagree: the conservative verdict wins on divergence, never the lenient one.
"""
from __future__ import annotations

from bytedigger_engine import telemetry_ctx


def resolve_gate_verdict(
    *, structured: str | None, prose: str | None, conservative: str, context: str
) -> tuple[str, str]:
    """Resolve a gate verdict from structured + prose signals.

    Decision table (frozen, §2 Part B):
      structured None, prose None       -> (conservative, "both_absent")
      structured None, prose present    -> (prose, "prose_only")
      structured present, prose None    -> (structured, "structured_only")
      structured == prose               -> (structured, "agree")
      structured != prose (both present)-> (conservative, "divergent")

    Emits telemetry_ctx.emit_safe("verdict_resolution_resolved", {...}) on
    every call, via module-attribute lookup so tests can monkeypatch it.
    """
    if structured is None and prose is None:
        verdict, reason = conservative, "both_absent"
    elif structured is None:
        assert prose is not None
        verdict, reason = prose, "prose_only"
    elif prose is None:
        verdict, reason = structured, "structured_only"
    elif structured == prose:
        verdict, reason = structured, "agree"
    else:
        verdict, reason = conservative, "divergent"

    telemetry_ctx.emit_safe(
        "verdict_resolution_resolved",
        {
            "context": context,
            "structured": structured,
            "prose": prose,
            "resolved": verdict,
            "reason": reason,
        },
    )
    return verdict, reason
