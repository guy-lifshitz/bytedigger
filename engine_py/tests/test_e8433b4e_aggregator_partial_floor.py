"""RED tests for E8433B4E Commit 1 — aggregator min-floor + fanout banner.

Design: SHARED/memory/Decisions/2026-05-03_E8433B4E_straggler_abort_design.md
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest  # noqa: F401  — pytest discovery convention

ENGINE_PY = Path(__file__).resolve().parents[1]
if str(ENGINE_PY) not in sys.path:
    sys.path.insert(0, str(ENGINE_PY))

from bytedigger_engine.contracts import StepResult  # noqa: E402
from bytedigger_engine.workflows.phase_6_review import _aggregate_review_findings  # noqa: E402


# Dispatch-table row format: "  - pr-review-toolkit:<slug> — model: <model>"
# Slug == part after the colon, before the ` — model:` segment.
_SLUG_RE = re.compile(r":([\w-]+?)\s+—\s+model:")


def _slugs_from_dispatch_table(table: str) -> list[str]:
    return _SLUG_RE.findall(table)


def _make_role_file(reviews_dir: Path, slug: str, severity: str = "LOW", title: str = "x") -> None:
    reviews_dir.mkdir(parents=True, exist_ok=True)
    body = (
        f"### SEVERITY: {severity}\n"
        f"### TITLE: {title}\n"
        "Stub finding body.\n"
    )
    (reviews_dir / f"role-{slug}.md").write_text(body, encoding="utf-8")


class _Ctx:
    """Minimal ctx stand-in. _resolve_scratchpad reads ctx.org_config['scratchpad_dir']."""

    def __init__(self, scratchpad: Path) -> None:
        self.org_config = {"scratchpad_dir": str(scratchpad)}


def _prev(complexity: str | None, scratchpad: Path) -> StepResult:
    data = {"scratchpad": str(scratchpad)}
    if complexity is not None:
        data["complexity"] = complexity
    return StepResult(status="ok", data=data, duration_ms=0, step_name="prior")


# bd#89 P3b1: the two floor tests (below-floor -> insufficient-fanout error) are retired;
# the aggregator has no floor any more (see test_bd89_p3b1_single_reviewer_only.py AC6).


def test_feature_five_roles_passes_without_floor(tmp_path):
    """No floor: 5 role files (e.g. a pre-upgrade run) aggregate fine -> status=ok."""
    scratchpad = tmp_path / "scratch"
    reviews = scratchpad / "reviews"

    slugs = ["code-reviewer", "silent-failure-hunter", "type-design-analyzer",
             "pr-test-analyzer", "code-simplifier"]
    for s in slugs:
        _make_role_file(reviews, s)

    ctx = _Ctx(scratchpad)
    result = _aggregate_review_findings(ctx, _prev("FEATURE", scratchpad))

    assert result.status == "ok", (
        f"expected ok with 5 files, got status={result.status} "
        f"error_code={result.error_code} error={result.error}"
    )


def test_aggregator_output_contains_fanout_banner_with_expected_observed_missing(tmp_path):
    """## Fanout section lists expected (1), observed and missing slugs."""
    scratchpad = tmp_path / "scratch"
    reviews = scratchpad / "reviews"

    # Composite absent -> it is the "missing" slug; two other role files observed.
    _make_role_file(reviews, "code-reviewer")
    _make_role_file(reviews, "silent-failure-hunter")

    ctx = _Ctx(scratchpad)
    result = _aggregate_review_findings(ctx, _prev("FEATURE", scratchpad))

    assert result.status == "ok", (
        f"expected ok, got status={result.status} error_code={result.error_code}"
    )
    content = result.data.get("aggregated_content", "") or ""
    assert "## Fanout" in content, "aggregator output must contain '## Fanout' section"

    content_lower = content.lower()
    assert "expected: 1" in content_lower, (
        f"fanout banner must mention expected=1 (content snippet: {content[:400]!r})"
    )
    assert "observed: 2" in content_lower, "fanout banner must mention observed=2"
    assert "missing: composite" in content_lower, "fanout banner must list the missing composite slug"


def test_complexity_missing_skips_floor_check_backward_compat(tmp_path):
    """When prev.data has no 'complexity', floor check is skipped — backward compat."""
    scratchpad = tmp_path / "scratch"
    reviews = scratchpad / "reviews"
    _make_role_file(reviews, "code-reviewer")  # only 1 file

    ctx = _Ctx(scratchpad)
    result = _aggregate_review_findings(ctx, _prev(None, scratchpad))

    # Without complexity the aggregator has no expected count; one file still aggregates.
    assert result.status == "ok", (
        f"expected ok, got status={result.status} error_code={result.error_code} "
        f"error={result.error}"
    )
