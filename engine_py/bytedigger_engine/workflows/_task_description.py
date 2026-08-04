"""Shared helper: normalize_task_description(cfg) -> str | None.

Single source of truth for "what counts as an absent task description" across
phase_05_inject (memory-FTS query gate) and phase_1_discovery (FEATURE REQUEST
block). Returns None for: cfg falsy/None, "task_description" key absent, value
not a str, value empty or whitespace-only. Otherwise returns value.strip().

Never mutates cfg. Never raises. Pure read.

Agreement: 740FF3CD (2026-05-12).
"""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def normalize_task_description(cfg: Mapping[str, Any] | None) -> str | None:
    if not cfg:
        return None
    raw = cfg.get("task_description")
    if not isinstance(raw, str):
        return None
    stripped = raw.strip()
    return stripped or None
