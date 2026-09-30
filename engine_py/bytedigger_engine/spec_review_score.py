"""spec_review_score.py — per-axis scores of a phase 4.5 spec review (bd#115).

Public API:
  AXES                  — the five score axes, in their fixed order
  SCORE_MIN, SCORE_MAX  — the inclusive score range (1..5)
  LOW_SCORE             — an axis scored below this is low
  ScoreResult           — frozen dataclass(status, scores, reason)
  parse_scores(raw)     -> ScoreResult   ("ok" | "absent" | "invalid")
  build_review_json(*, verdict, verdict_before_scores, cycle, result, review_path) -> dict

The reviewer writes a fenced ```json block under a `## Scores` heading. Only the
first such block before the next `## ` heading counts. Stdlib only, no I/O.
Spec: docs/decisions/2026-09-30-bd115-verification-registry-spec-scores.md.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

AXES: tuple[str, ...] = ("completeness", "clarity", "feasibility", "issue_alignment", "consistency")
SCORE_MIN = 1
SCORE_MAX = 5
LOW_SCORE = 3

_SCORES_HEADING_RE = re.compile(r"^##[ \t]+Scores[ \t]*$", re.MULTILINE)
_NEXT_HEADING_RE = re.compile(r"^## ", re.MULTILINE)
_JSON_FENCE_RE = re.compile(r"^```json[ \t]*\n(.*?)^```", re.MULTILINE | re.DOTALL)


@dataclass(frozen=True)
class ScoreResult:
    """Outcome of `parse_scores`.

    status  — "ok", "absent" (no heading or no block under it) or "invalid".
    scores  — axis -> int in `AXES` order when status is "ok", else empty.
    reason  — the first problem found when status is "invalid", else "".
    """

    status: str
    scores: dict[str, int] = field(default_factory=dict)
    reason: str = ""


def _scores_section(raw: str) -> str | None:
    """Return the text between `## Scores` and the next `## ` heading, or None."""
    m = _SCORES_HEADING_RE.search(raw)
    if m is None:
        return None
    rest = raw[m.end():]
    nxt = _NEXT_HEADING_RE.search(rest)
    return rest[: nxt.start()] if nxt else rest


def parse_scores(raw: str) -> ScoreResult:
    """Parse the `## Scores` block of a review; never raises."""
    section = _scores_section(raw or "")
    if section is None:
        return ScoreResult(status="absent", reason="no ## Scores heading")
    block = _JSON_FENCE_RE.search(section)
    if block is None:
        return ScoreResult(status="absent", reason="no ```json block under ## Scores")
    try:
        obj: Any = json.loads(block.group(1))
    except ValueError as exc:
        return ScoreResult(status="invalid", reason=f"not JSON: {exc}")
    if not isinstance(obj, dict):
        return ScoreResult(status="invalid", reason="scores block is not a JSON object")
    for axis in AXES:
        if axis not in obj:
            return ScoreResult(status="invalid", reason=f"missing axis: {axis}")
    for key in obj:
        if key not in AXES:
            return ScoreResult(status="invalid", reason=f"unknown axis: {key}")
    scores: dict[str, int] = {}
    for axis in AXES:
        value = obj[axis]
        if isinstance(value, bool) or not isinstance(value, int):
            return ScoreResult(status="invalid", reason=f"{axis}: score is not an integer")
        if not SCORE_MIN <= value <= SCORE_MAX:
            return ScoreResult(
                status="invalid",
                reason=f"{axis}: score {value} outside {SCORE_MIN}..{SCORE_MAX}",
            )
        scores[axis] = value
    return ScoreResult(status="ok", scores=scores)


def low_axes(result: ScoreResult) -> list[str]:
    """Axes scored below `LOW_SCORE`, in axis order ([] unless status is "ok")."""
    if result.status != "ok":
        return []
    return [a for a in AXES if result.scores.get(a, SCORE_MAX) < LOW_SCORE]


def build_review_json(
    *,
    verdict: str,
    verdict_before_scores: str,
    cycle: int,
    result: ScoreResult,
    review_path: str,
) -> dict[str, Any]:
    """Build the JSON-serialisable `review.json` payload for one review cycle.

    `scores` is non-empty only when `result.status == "ok"`.
    """
    return {
        "schema": 1,
        "cycle": cycle,
        "review_path": review_path,
        "verdict": verdict,
        "verdict_before_scores": verdict_before_scores,
        "scores_status": result.status,
        "scores": dict(result.scores) or None,
        "reason": result.reason,
        "min_score": min(result.scores.values()) if result.scores else None,
        "low_axes": low_axes(result),
    }
