"""net_new_delta.py — pure delta-verdict helper for 585E30E3 P2 shadow-mode gate.

Mirrors reproducibility.py / suite_safety.py shape: no side-effects.
All functions are pure; no I/O, no subprocess, no telemetry. Count-based
(585E30E3) and id-based (bd#88) verdicts.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterator

from bytedigger_engine.lib.corpus_parity import normalize_ci_line


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


# ─── bd#88: delta by test ID ──────────────────────────────────────────────────

# The id runs to the " - " message separator, so parametrized ids with spaces
# survive. Single source for the engine and baseline_delta_gate.py.
_PYTEST_FAIL_LINE_RE = re.compile(r"^(?:FAILED|ERROR)\s+(.+?)(?:\s+-\s.*)?$")


def iter_pytest_fail_ids(text: str) -> Iterator[str]:
    """Node ids from pytest's short summary (``FAILED <id>`` / ``ERROR <id>``),
    in output order, after CI-line normalization (ANSI, ``::group::``)."""
    for line in text.splitlines():
        m = _PYTEST_FAIL_LINE_RE.match(normalize_ci_line(line))
        if m:
            yield m.group(1)


def parse_pytest_fail_ids(text: str) -> frozenset[str]:
    return frozenset(iter_pytest_fail_ids(text))


def run_fail_ids(exit_code: int, text: str) -> frozenset[str] | None:
    """Fail ids of one pytest run, or None when the run says nothing reliable.

    rc 0 (all passed) and rc 5 (nothing collected) are available; rc 1 is
    available only when at least one id parsed. Anything else — interrupted,
    internal/usage error, timeout, a failure with no ids — is unavailable,
    never "no failures".
    """
    if exit_code in (0, 5):
        return parse_pytest_fail_ids(text)
    if exit_code == 1:
        ids = parse_pytest_fail_ids(text)
        return ids or None
    return None


def covered_by_baseline(fail_id: str, baseline_ids: "frozenset[str] | set[str]") -> bool:
    """True when the baseline holds this id or one of its ``::`` prefixes —
    a file-level id (collection error at RED) covers every test in that file."""
    if fail_id in baseline_ids:
        return True
    i = fail_id.find("::")
    while i != -1:
        if fail_id[:i] in baseline_ids:
            return True
        i = fail_id.find("::", i + 2)
    return False


@dataclass(frozen=True)
class IdDeltaVerdict:
    """Immutable verdict from id_delta_verdict()."""

    new_ids: tuple[str, ...]      # sorted current ids not covered by the baseline
    classification: str           # "clean" | "preexisting_only" | "net_new_regression" | "baseline_unavailable"
    baseline_available: bool
    would_block: bool             # enforce AND new_ids


def id_delta_verdict(
    baseline_ids: frozenset[str] | None,
    current_ids: frozenset[str],
    *,
    enforce: bool,
    fail_closed: bool,
) -> IdDeltaVerdict:
    """Compare failing test ids against the RED-commit baseline.

    With no baseline, ``fail_closed`` decides: every current failure is new
    (True) or nothing can be called new (False).
    """
    if baseline_ids is None:
        new_ids = tuple(sorted(current_ids)) if fail_closed else ()
        cls = "baseline_unavailable"
    else:
        new_ids = tuple(sorted(i for i in current_ids if not covered_by_baseline(i, baseline_ids)))
        if not current_ids:
            cls = "clean"
        elif new_ids:
            cls = "net_new_regression"
        else:
            cls = "preexisting_only"
    return IdDeltaVerdict(
        new_ids=new_ids,
        classification=cls,
        baseline_available=baseline_ids is not None,
        would_block=enforce and bool(new_ids),
    )
