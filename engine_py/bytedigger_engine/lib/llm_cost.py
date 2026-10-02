"""llm_cost - pure cost/billing helpers for the per-call ledger (bd#167).

Stdlib only; every function is total (never raises). Provider-agnostic: rates
come only from the caller's table, there are no guessed multipliers and an
unpriced call is ``None``, never 0.

Spec: docs/decisions/2026-10-02-bd167-cost-cache-billing.md (section 1).
"""
from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

BILLING_MODES = ("metered", "subscription", "unknown")

_FAMILIES = ("fable", "opus", "sonnet", "haiku")
_USAGE_FIELDS = (
    "tokens_in", "tokens_out", "cache_read_tokens", "cache_write_tokens", "cache_write_1h_tokens",
)


def _count(value: Any) -> "int | float | None":
    """A usable token count: int/float (bool excluded), finite, non-negative; else None."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or value < 0:
        return None
    return value


def _first_count(src: Mapping, *keys: str) -> "int | float | None":
    for key in keys:
        if key in src:
            return _count(src.get(key))
    return None


def _one_hour_part(src: Mapping) -> "int | float | None":
    nested = src.get("cache_creation")
    if isinstance(nested, Mapping) and "ephemeral_1h_input_tokens" in nested:
        return _count(nested.get("ephemeral_1h_input_tokens"))
    return _count(src.get("cache_write_1h_tokens"))


def normalize_usage(data: Any) -> "dict | None":
    """Normalize usage from ``data["usage"]`` (a dict) or the flat keys of ``data``.

    Accepts the Anthropic dialect and the flat dialect. ``None`` when no count
    survives validation ("no usage reported").
    """
    try:
        if not isinstance(data, Mapping):
            return None
        usage = data.get("usage")
        src = usage if isinstance(usage, Mapping) else data
        out: dict = {
            "tokens_in": _first_count(src, "input_tokens", "tokens_in"),
            "tokens_out": _first_count(src, "output_tokens", "tokens_out"),
            "cache_read_tokens": _first_count(src, "cache_read_input_tokens", "cache_read_tokens"),
            "cache_write_tokens": _first_count(src, "cache_creation_input_tokens", "cache_write_tokens"),
            "cache_write_1h_tokens": _one_hour_part(src),
        }
        total, one_h = out["cache_write_tokens"], out["cache_write_1h_tokens"]
        if total is not None and one_h is not None and one_h > total:
            out["cache_write_1h_tokens"] = total
        if all(out[k] is None for k in _USAGE_FIELDS):
            return None
        return out
    except Exception:  # noqa: BLE001
        return None


def _rate_entry(model: Any, rates_table: Any) -> "Mapping | None":
    """Same lookup rule as llm_subprocess._price_for_model: exact lowercase alias, then family substring."""
    if not model or not isinstance(model, str) or not isinstance(rates_table, Mapping):
        return None
    normalized = model.lower()
    entry = rates_table.get(normalized)
    if entry is None:
        for alias in _FAMILIES:
            if alias in normalized and alias in rates_table:
                entry = rates_table[alias]
                break
    return entry if isinstance(entry, Mapping) else None


def _num(value: Any) -> "int | float | None":
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return None
    return value


def _write_split(usage: Mapping) -> "tuple[int | float, int | float]":
    """(5m part, 1h part) of the cache write, 1h clamped to the write total; None counts as 0.

    A missing write total with a 1h part means the total is that 1h part."""
    one_h = _count(usage.get("cache_write_1h_tokens")) or 0
    total = _count(usage.get("cache_write_tokens"))
    if total is None:
        total = one_h
    one_h = min(one_h, total)
    return total - one_h, one_h


def price_usage(usage: Any, model: Any, rates_table: Any) -> "float | None":
    """Cost in USD of normalized *usage* at *rates_table* (per MTok), or None.

    None when usage is None, the model is unknown, or a token class with a
    NONZERO count has no numeric rate. ``cache_write`` aliases ``cache_write_5m``.
    """
    try:
        if not isinstance(usage, Mapping):
            return None
        entry = _rate_entry(model, rates_table)
        if entry is None:
            return None
        write_5m, write_1h = _write_split(usage)
        rate_5m = entry.get("cache_write_5m", entry.get("cache_write"))
        classes = (
            (_count(usage.get("tokens_in")) or 0, entry.get("in")),
            (_count(usage.get("tokens_out")) or 0, entry.get("out")),
            (_count(usage.get("cache_read_tokens")) or 0, entry.get("cache_read")),
            (write_5m, rate_5m),
            (write_1h, entry.get("cache_write_1h")),
        )
        total = 0.0
        for tokens, rate in classes:
            if tokens == 0:
                continue
            numeric = _num(rate)
            if numeric is None:
                return None
            total += tokens * numeric
        return total / 1_000_000.0
    except Exception:  # noqa: BLE001
        return None


def cost_kind(billing_mode: Any) -> str:
    """metered -> metered, subscription -> notional, anything else -> unknown."""
    if billing_mode == "metered":
        return "metered"
    if billing_mode == "subscription":
        return "notional"
    return "unknown"


def billing_mode_from_auth(api_key_source: Any, env: Any) -> str:
    """Billing mode of a Claude CLI / agent-sdk session.

    Ladder: Bedrock/Vertex env => metered; the runtime-reported ``api_key_source``
    ("none" => subscription, any other non-empty string => metered); then the
    rest of the env (API key => metered, OAuth token => subscription); else unknown.
    """
    try:
        get = env.get if isinstance(env, Mapping) else (lambda _k: None)
        if get("CLAUDE_CODE_USE_BEDROCK") == "1" or get("CLAUDE_CODE_USE_VERTEX") == "1":
            return "metered"
        if isinstance(api_key_source, str) and api_key_source:
            return "subscription" if api_key_source == "none" else "metered"
        if get("ANTHROPIC_API_KEY"):
            return "metered"
        if get("CLAUDE_CODE_OAUTH_TOKEN"):
            return "subscription"
    except Exception:  # noqa: BLE001
        pass
    return "unknown"


def divergence(derived: Any, reported: Any, threshold_pct: Any) -> "dict | None":
    """Divergence record when both costs are numbers and differ by more than
    *threshold_pct* percent of the larger AND by more than 1e-6 USD; else None.
    ``delta_usd`` is signed: reported - derived."""
    try:
        d, r, pct = _num(derived), _num(reported), _num(threshold_pct)
        if d is None or r is None or pct is None:
            return None
        gap = abs(d - r)
        if gap > pct / 100.0 * max(d, r) and gap > 1e-6:
            return {
                "derived_cost_usd": d,
                "reported_cost_usd": r,
                "delta_usd": r - d,
                "threshold_pct": threshold_pct,
            }
    except Exception:  # noqa: BLE001
        pass
    return None
