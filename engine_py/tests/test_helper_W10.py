"""W10 RED tests for anti_hallucination/helper.py defects.

Bug A (triage #3, MED): verify_findings does direct subscript
    `prev.data["review_doc_path"]` at L123. When key missing, raises
    uncaught KeyError. Symmetric fix: extend the existing guard to
    return StepResult(status="error", error_code="E_MISSING_REVIEW_DOC_PATH").

Bug B (triage #14, MED): verify_validation_doc duplicates each
    unverified citation — Pass 1 (L388-390) tags it in-place via
    new_lines[i] = tagged AND appends the same tagged line to
    unverified_citation_lines. Rebuild step (L425-428) appends
    "## Unverified Citations (Auto-Filtered)" + extends with
    unverified_citation_lines → citation appears TWICE in output.

    Decision: unverified citations should appear EXACTLY ONCE,
    under the "## Unverified Citations (Auto-Filtered)" section
    (matches docstring intent — demote, not duplicate). Verified
    citations stay in place untouched (regression preserved).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from bytedigger_engine.contracts import StepResult  # noqa: E402

from bytedigger_engine.lib.plugins.anti_hallucination import helper  # noqa: E402
from bytedigger_engine.lib.plugins.anti_hallucination.helper import (  # noqa: E402
    verify_findings,
    verify_validation_doc,
)


# ---------------------------------------------------------------------------
# Bug A — verify_findings missing review_doc_path
# ---------------------------------------------------------------------------


class _FakeCtx:
    """Minimal ctx — verify_* functions only read .cwd via getattr."""

    def __init__(self, cwd: Path | None = None):
        self.cwd = str(cwd) if cwd is not None else None


def _make_prev(data):
    """Build a StepResult-like prev object with .data attribute."""
    return StepResult(
        status="ok",
        data=data,
        duration_ms=0,
        step_name="phase_6_review",
    )


def test_W10_verify_findings_missing_review_doc_path_returns_error():
    """prev.data without 'review_doc_path' must NOT raise KeyError.

    Symmetric with existing E_MISSING_PREV_DATA guard at L107-113;
    extend it to also catch the missing-key case.
    """
    ctx = _FakeCtx()
    prev = _make_prev({})  # data is a dict but lacks "review_doc_path"

    result = verify_findings(ctx, prev)

    assert isinstance(result, StepResult), f"expected StepResult, got {type(result)!r}"
    assert result.status == "error", (
        f"expected status='error', got {result.status!r}"
    )
    assert result.error_code == "E_MISSING_REVIEW_DOC_PATH", (
        f"expected error_code='E_MISSING_REVIEW_DOC_PATH', got {result.error_code!r}"
    )
    assert result.step_name == "verify_findings"


def test_W10_verify_findings_with_review_doc_path_does_not_error_on_missing_check(
    tmp_path,
):
    """Regression guard: when 'review_doc_path' present but file missing,
    existing FileNotFoundError handler still returns E_VERIFY_READ_FAILED.
    """
    ctx = _FakeCtx(cwd=tmp_path)
    nonexistent = tmp_path / "does_not_exist.md"
    prev = _make_prev({"review_doc_path": str(nonexistent)})

    result = verify_findings(ctx, prev)

    assert isinstance(result, StepResult)
    assert result.status == "error"
    assert result.error_code == "E_VERIFY_READ_FAILED", (
        f"expected E_VERIFY_READ_FAILED, got {result.error_code!r}"
    )


# ---------------------------------------------------------------------------
# Bug B — verify_validation_doc unverified citations duplicated
# ---------------------------------------------------------------------------


def _make_validation_prev(doc_path: Path, red_test_paths=None):
    return _make_prev(
        {
            "validation_doc_path": str(doc_path),
            "red_test_paths": red_test_paths or [],
        }
    )


def _patch_check_citation(monkeypatch, verdict_for_path):
    """Replace helper.check_citation with deterministic stub.

    verdict_for_path: dict mapping path_str → tag string (None = verified).
    Default for unknown paths: None (verified).
    """
    def _fake(path_str, line_num, quote_text, ctx):
        return verdict_for_path.get(path_str, None)

    monkeypatch.setattr(helper, "check_citation", _fake)


def _count_occurrences(haystack: str, needle: str) -> int:
    return haystack.count(needle)


def test_W10_verify_validation_doc_unverified_citation_appears_once(
    tmp_path, monkeypatch
):
    """1 verified + 1 unverified citation → unverified appears EXACTLY ONCE
    (under "## Unverified Citations (Auto-Filtered)" section, not in
    original position).
    """
    doc = tmp_path / "build-opus-validation.md"
    doc.write_text(
        "# Validation\n"
        "\n"
        "## Forward Map\n"
        "> verified.py:1: ok_quote\n"
        "> unverified.py:2: bad_quote\n"
        "\n",
        encoding="utf-8",
    )

    # Stub: only "unverified.py" returns a tag; "verified.py" → None.
    _patch_check_citation(
        monkeypatch,
        {"unverified.py": "[UNVERIFIED: path not found]"},
    )

    ctx = _FakeCtx(cwd=tmp_path)
    prev = _make_validation_prev(doc)

    result = verify_validation_doc(ctx, prev)
    assert result.status == "ok", f"unexpected error: {result.error!r}"

    rewritten = doc.read_text(encoding="utf-8")

    # The unverified citation line content (the path:line: quote part)
    # must appear EXACTLY ONCE in the output.
    n = _count_occurrences(rewritten, "unverified.py:2: bad_quote")
    assert n == 1, (
        f"unverified citation appears {n} times (expected 1). Doc:\n{rewritten}"
    )

    # Section header must be present.
    assert "## Unverified Citations (Auto-Filtered)" in rewritten

    # Verified citation is untouched and still in place.
    assert "verified.py:1: ok_quote" in rewritten


def test_W10_verify_validation_doc_two_unverified_citations_each_appear_once(
    tmp_path, monkeypatch
):
    """2 unverified citations → each appears EXACTLY ONCE under the
    Auto-Filtered section. Original positions must NOT contain either
    citation line content.
    """
    doc = tmp_path / "build-opus-validation.md"
    doc.write_text(
        "# Validation\n"
        "\n"
        "## Forward Map\n"
        "> bad1.py:10: quote_one\n"
        "\n"
        "## Spec Compliance\n"
        "> bad2.py:20: quote_two\n"
        "\n",
        encoding="utf-8",
    )

    _patch_check_citation(
        monkeypatch,
        {
            "bad1.py": "[UNVERIFIED: path not found]",
            "bad2.py": "[UNVERIFIED: line out of bounds]",
        },
    )

    ctx = _FakeCtx(cwd=tmp_path)
    prev = _make_validation_prev(doc)

    result = verify_validation_doc(ctx, prev)
    assert result.status == "ok"

    rewritten = doc.read_text(encoding="utf-8")

    n1 = _count_occurrences(rewritten, "bad1.py:10: quote_one")
    n2 = _count_occurrences(rewritten, "bad2.py:20: quote_two")
    assert n1 == 1, f"bad1 appears {n1} times (expected 1). Doc:\n{rewritten}"
    assert n2 == 1, f"bad2 appears {n2} times (expected 1). Doc:\n{rewritten}"

    # Section present.
    assert "## Unverified Citations (Auto-Filtered)" in rewritten

    # Both unverified citations appear AFTER the Auto-Filtered section header
    # (i.e. they are no longer in their original section positions).
    section_idx = rewritten.index("## Unverified Citations (Auto-Filtered)")
    assert rewritten.index("bad1.py:10: quote_one") > section_idx, (
        "bad1 still appears in original position before Auto-Filtered section"
    )
    assert rewritten.index("bad2.py:20: quote_two") > section_idx, (
        "bad2 still appears in original position before Auto-Filtered section"
    )


def test_W10_verify_validation_doc_verified_citations_stay_in_place_regression(
    tmp_path, monkeypatch
):
    """Regression: when ALL citations are verified, doc is not rewritten,
    no Auto-Filtered section appears, citations stay in place.
    """
    doc = tmp_path / "build-opus-validation.md"
    original = (
        "# Validation\n"
        "\n"
        "## Forward Map\n"
        "> good1.py:1: q1\n"
        "> good2.py:2: q2\n"
        "\n"
    )
    doc.write_text(original, encoding="utf-8")

    # All citations verify (stub returns None for everything).
    _patch_check_citation(monkeypatch, {})

    ctx = _FakeCtx(cwd=tmp_path)
    prev = _make_validation_prev(doc)

    result = verify_validation_doc(ctx, prev)
    assert result.status == "ok"

    rewritten = doc.read_text(encoding="utf-8")

    # No Auto-Filtered section.
    assert "## Unverified Citations (Auto-Filtered)" not in rewritten

    # Citations stay in original positions, exactly once each.
    assert _count_occurrences(rewritten, "good1.py:1: q1") == 1
    assert _count_occurrences(rewritten, "good2.py:2: q2") == 1

    # Doc content unchanged (no rewrite triggered when nothing to demote).
    assert rewritten == original
