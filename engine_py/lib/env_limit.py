"""GH514(3) — single-source account spend/usage-limit marker list + classifier.

Spec: GH514 spec (2026-07-10 EABE6D3A)
"""
from __future__ import annotations

ENV_LIMIT_MARKERS: tuple[str, ...] = (
    "spend limit",              # "You've hit your monthly spend limit" (test_gh382 fixture :120)
    "usage limit reached",      # Max-plan CLI: "Claude AI usage limit reached|<epoch>"
    "credit balance is too low",
    "api_error_status=429",
    "rate_limit_error",
    "\"status\": 429",
)


def detect_env_limit(*texts: object) -> str | None:
    """Return the FIRST marker (in ENV_LIMIT_MARKERS order) found case-insensitively
    in any of the given texts (None/non-str inputs skipped), else None.
    """
    haystacks = [t.lower() for t in texts if isinstance(t, str)]
    if not haystacks:
        return None
    for marker in ENV_LIMIT_MARKERS:
        for haystack in haystacks:
            if marker in haystack:
                return marker
    return None


PAUSE_LANE_ERROR_CODES: frozenset[str] = frozenset({"E_LLM_SPEND_LIMIT"})


def is_paused_result(status: object, error_code: object) -> bool:
    """True iff a terminal result is the pausable env-limit lane (GH576 C474E073)."""
    return status == "error" and error_code in PAUSE_LANE_ERROR_CODES
