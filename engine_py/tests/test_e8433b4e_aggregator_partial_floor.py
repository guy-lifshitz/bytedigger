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
WORKFLOWS = ENGINE_PY / "bytedigger_engine" / "workflows"
if str(WORKFLOWS) not in sys.path:
    sys.path.insert(0, str(WORKFLOWS))

from bytedigger_engine.contracts import StepResult  # noqa: E402
from bytedigger_engine.workflows.phase_6_review import _aggregate_review_findings, _select_reviewers  # noqa: E402


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


def test_simple_one_role_below_floor_2_fails_with_e_insufficient_fanout(tmp_path):
    """SIMPLE expects N=3 → floor=2. 1 role file → below floor → E_INSUFFICIENT_FANOUT."""
    scratchpad = tmp_path / "scratch"
    reviews = scratchpad / "reviews"
    _make_role_file(reviews, "code-reviewer")

    ctx = _Ctx(scratchpad)
    result = _aggregate_review_findings(ctx, _prev("SIMPLE", scratchpad))

    assert result.status == "error", f"expected error status, got {result.status}"
    assert result.error_code == "E_INSUFFICIENT_FANOUT", (
        f"expected E_INSUFFICIENT_FANOUT for 1<floor=2, got {result.error_code}"
    )
    assert result.recoverable is False
    assert isinstance(result.data, dict)
    assert result.data.get("observed_role_count") == 1
    assert result.data.get("min_floor") == 2
    assert result.data.get("expected_reviewers") == 3


def test_feature_two_roles_below_floor_3_fails(tmp_path):
    """FEATURE expects N=6 → floor=3. 2 role files → below floor → E_INSUFFICIENT_FANOUT."""
    scratchpad = tmp_path / "scratch"
    reviews = scratchpad / "reviews"
    _make_role_file(reviews, "code-reviewer")
    _make_role_file(reviews, "silent-failure-hunter")

    ctx = _Ctx(scratchpad)
    result = _aggregate_review_findings(ctx, _prev("FEATURE", scratchpad))

    assert result.status == "error"
    assert result.error_code == "E_INSUFFICIENT_FANOUT"
    assert result.data.get("observed_role_count") == 2
    assert result.data.get("min_floor") == 3
    assert result.data.get("expected_reviewers") == 6


def test_feature_five_roles_above_floor_3_passes(tmp_path):
    """FEATURE expects N=6 → floor=3. 5 role files → above floor → status=ok."""
    scratchpad = tmp_path / "scratch"
    reviews = scratchpad / "reviews"

    feature_dispatch_table, n = _select_reviewers("FEATURE")
    assert n == 6, f"FEATURE reviewer count must be 6, got {n}"
    slugs = _slugs_from_dispatch_table(feature_dispatch_table)
    assert len(slugs) == 6, f"expected 6 slugs from FEATURE table, got {slugs}"

    for s in slugs[:5]:
        _make_role_file(reviews, s)

    ctx = _Ctx(scratchpad)
    result = _aggregate_review_findings(ctx, _prev("FEATURE", scratchpad))

    assert result.status == "ok", (
        f"expected ok with 5/6, got status={result.status} "
        f"error_code={result.error_code} error={result.error}"
    )


def test_aggregator_output_contains_fanout_banner_with_expected_observed_missing(tmp_path):
    """## Fanout section must list expected, observed, and missing slugs."""
    scratchpad = tmp_path / "scratch"
    reviews = scratchpad / "reviews"

    feature_dispatch_table, n = _select_reviewers("FEATURE")
    slugs = _slugs_from_dispatch_table(feature_dispatch_table)
    assert len(slugs) == n, f"slug-extract sanity: {len(slugs)} vs n={n}"

    # Drop one slug — it becomes the "missing" one.
    missing_slug = slugs[-1]
    for s in slugs[:-1]:
        _make_role_file(reviews, s)

    ctx = _Ctx(scratchpad)
    result = _aggregate_review_findings(ctx, _prev("FEATURE", scratchpad))

    assert result.status == "ok", (
        f"expected ok with {n-1}/{n}, got status={result.status} "
        f"error_code={result.error_code}"
    )
    content = result.data.get("aggregated_content", "") or ""
    assert "## Fanout" in content, "aggregator output must contain '## Fanout' section"

    content_lower = content.lower()
    assert (
        f"expected: {n}" in content_lower or f"expected {n}" in content_lower
    ), f"fanout banner must mention expected={n} (content snippet: {content[:400]!r})"
    assert (
        f"observed: {n-1}" in content_lower or f"observed {n-1}" in content_lower
    ), f"fanout banner must mention observed={n-1}"
    assert missing_slug in content, (
        f"fanout banner must list missing slug {missing_slug!r}"
    )


def test_complexity_missing_skips_floor_check_backward_compat(tmp_path):
    """When prev.data has no 'complexity', floor check is skipped — backward compat."""
    scratchpad = tmp_path / "scratch"
    reviews = scratchpad / "reviews"
    _make_role_file(reviews, "code-reviewer")  # only 1 file

    ctx = _Ctx(scratchpad)
    result = _aggregate_review_findings(ctx, _prev(None, scratchpad))

    # Without complexity, aggregator can't compute floor — must NOT fail with E_INSUFFICIENT_FANOUT.
    assert result.error_code != "E_INSUFFICIENT_FANOUT", (
        "aggregator must skip floor check when complexity unknown (backward compat)"
    )
    assert result.status == "ok", (
        f"expected ok, got status={result.status} error_code={result.error_code} "
        f"error={result.error}"
    )
