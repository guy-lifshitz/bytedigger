"""net_new_delta.py — pure delta-verdict helper for 585E30E3 P2 shadow-mode gate.

Mirrors reproducibility.py / suite_safety.py shape: stdlib-only, no side-effects.
All functions are pure; no I/O, no subprocess, no telemetry.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class NetNewVerdict:
    """Immutable verdict from delta_verdict()."""

    baseline_failed: int | None   # None ⇒ baseline_unavailable
    current_failed: int
    net_new: int                  # max(0, current-baseline); 0 if baseline None
    classification: str           # "clean" | "preexisting_only" | "net_new_regression" | "baseline_unavailable"
    is_regression: bool           # classification == "net_new_regression"
    would_block: bool             # is_regression AND enforce


def classify(baseline_failed: int | None, current_failed: int) -> str:
    """Classify a (baseline, current) failure-count pair.

    Rules (total, deterministic — §0 spec):
      baseline_failed is None          → "baseline_unavailable"
      current_failed == 0              → "clean"  (always clean when current==0)
      0 < current_failed <= baseline   → "preexisting_only"
      current_failed > baseline        → "net_new_regression"
    """
    if baseline_failed is None:
        return "baseline_unavailable"
    if current_failed == 0:
        return "clean"
    if current_failed <= baseline_failed:
        return "preexisting_only"
    return "net_new_regression"


def compute_net_new(baseline_failed: int | None, current_failed: int) -> int:
    """Return the number of net-new failures introduced by the branch.

    Returns 0 if baseline is unavailable (cannot determine net-new count).
    Returns max(0, current_failed - baseline_failed) otherwise.
    """
    if baseline_failed is None:
        return 0
    return max(0, current_failed - baseline_failed)


def delta_verdict(
    baseline_failed: int | None,
    current_failed: int,
    *,
    enforce: bool,
) -> NetNewVerdict:
    """Compute a full delta verdict from (baseline, current) failure counts.

    Args:
        baseline_failed: failure count on the pre-branch baseline, or None if unavailable.
        current_failed:  failure count on the current branch.
        enforce:         when True AND classification=="net_new_regression", would_block=True.

    Returns a frozen NetNewVerdict.
    """
    cls = classify(baseline_failed, current_failed)
    net_new = compute_net_new(baseline_failed, current_failed)
    is_regression = cls == "net_new_regression"
    would_block = is_regression and enforce
    return NetNewVerdict(
        baseline_failed=baseline_failed,
        current_failed=current_failed,
        net_new=net_new,
        classification=cls,
        is_regression=is_regression,
        would_block=would_block,
    )
