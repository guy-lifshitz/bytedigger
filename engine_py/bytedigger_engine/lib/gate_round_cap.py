"""Gate round cap per tier/complexity (bd#163).

Pure, deterministic, host-neutral: no LLM call, no provider/backend branch.
Resolves the maximum number of gate retry rounds for a run from an optional
``{"<TIER>": int}`` table and a tier/complexity label.

``DEFAULT_CAP`` equals ``WorkflowEngine._MAX_VALIDATION_CYCLES`` (Design A
decree 2026-04-26); ``HARD_MAX`` equals ``_GATE_BUDGET_HARD_BACKSTOP`` (GH625).
A malformed table never raises and never lifts the cap: it degrades to
``DEFAULT_CAP`` with source ``"fallback"`` and one warning.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

DEFAULT_CAP = 2
HARD_MAX = 6


@dataclass(frozen=True)
class GateCap:
    cap: int
    source: str  # "default" | "table" | "fallback"
    label: str | None
    warnings: list[dict] = field(default_factory=list)


def _invalid(label: str | None, reason: str) -> GateCap:
    return GateCap(
        DEFAULT_CAP,
        "fallback",
        label,
        [{"event": "gate_round_cap_table_invalid", "reason": reason}],
    )


def resolve_gate_round_cap(label: str | None, table_raw: Any) -> GateCap:
    """Resolve the gate round cap for ``label`` from ``table_raw`` (dict or JSON str)."""
    if table_raw is None or (isinstance(table_raw, str) and not table_raw.strip()):
        return GateCap(DEFAULT_CAP, "default", label, [])

    table: Any = table_raw
    if isinstance(table_raw, str):
        try:
            table = json.loads(table_raw)
        except (ValueError, TypeError):
            return _invalid(label, "table is not valid JSON")
    if not isinstance(table, dict):
        return _invalid(label, "table is not a JSON object")
    if not table or not label:
        return GateCap(DEFAULT_CAP, "default", label, [])

    wanted = str(label).strip().upper()
    for key, value in table.items():
        if not isinstance(key, str) or key.strip().upper() != wanted:
            continue
        if isinstance(value, bool) or not isinstance(value, int):
            return _invalid(label, f"cap for {key!r} is not an integer")
        if value < 1 or value > HARD_MAX:
            return _invalid(label, f"cap for {key!r} is outside 1..{HARD_MAX}")
        return GateCap(value, "table", label, [])
    return GateCap(DEFAULT_CAP, "default", label, [])
