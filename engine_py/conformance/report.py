"""L0Report — the shared carrier used by L3-L12's checkers (AC-C2).

L2 owns the carrier's *structure* only; field *values* and their semantics
belong to later lots (L7-L12).
"""
from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Mapping


@dataclass(frozen=True)
class L0Report:
    """Frozen dataclass carrying a conformance verdict.

    `frozen=True` blocks attribute *rebinding*, not in-place mutation of a
    mutable container field, so the three collection fields are coerced to
    immutable containers at construction time (`__post_init__` +
    `object.__setattr__`, the standard frozen-dataclass idiom): `tuple` for
    `requirements`/`violations`, and a `MappingProxyType` wrapping a *copy*
    of `labels` (not the caller's own dict) for `labels`, so a producer
    cannot mutate the record's labels after construction via the object it
    passed in.
    """

    passed: bool
    requirements: tuple[str, ...]
    violations: tuple[str, ...]
    labels: Mapping[str, Any]

    def __post_init__(self) -> None:
        object.__setattr__(self, "requirements", tuple(self.requirements))
        object.__setattr__(self, "violations", tuple(self.violations))
        object.__setattr__(self, "labels", MappingProxyType(dict(self.labels)))
