"""Deterministic MICRO-scope eligibility kernel — agreement 761D8C4F.

Pure function classify_scope() encodes delegation.md MICRO triggers:
single NEW file · ≤80 logic LOC · ≤12 ACs · no blocking deps · spec fits 1 page.
Parent SYSTEMATIC: 93467759 (/build cost-governance).
"""
from __future__ import annotations

MICRO_MAX_LOGIC_LOC = 80
MICRO_MAX_AC = 12
REQUIRED_KEYS = (
    "new_files",
    "modified_files",
    "logic_loc",
    "ac_count",
    "blocking_deps",
    "spec_fits_one_page",
)


def classify_scope(scope: dict) -> dict:
    """Classify a scope dict as MICRO or ESCALATE.

    Args:
        scope: dict with keys defined in REQUIRED_KEYS.

    Returns:
        {"tier": "MICRO"|"ESCALATE", "eligible": bool, "reasons": list[str]}

    Raises:
        ValueError: if any REQUIRED_KEYS are missing; message names every missing key.
    """
    missing = [k for k in REQUIRED_KEYS if k not in scope]
    if missing:
        raise ValueError(
            "Missing required keys: " + ", ".join(missing)
        )

    reasons: list[str] = []

    if scope["new_files"] != 1:
        reasons.append("not-single-new-file")

    if scope["modified_files"] != 0:
        reasons.append("modifies-existing-production")

    if scope["logic_loc"] > MICRO_MAX_LOGIC_LOC:
        reasons.append("loc-over-cap")

    if scope["ac_count"] > MICRO_MAX_AC:
        reasons.append("ac-over-cap")

    if scope["blocking_deps"]:
        reasons.append("blocking-deps")

    if not scope["spec_fits_one_page"]:
        reasons.append("spec-over-one-page")

    eligible = len(reasons) == 0
    tier = "MICRO" if eligible else "ESCALATE"

    return {"tier": tier, "eligible": eligible, "reasons": reasons}
