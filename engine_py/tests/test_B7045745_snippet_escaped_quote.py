"""RED tests for B7045745 — snippet citation verifier: escaped inner quotes.

Contract:
1. extract_citations() captures the FULL fragment of a snippet citation that
   contains escaped inner quotes (e.g. layer: Literal[\"P\", \"D\", \"E\"]).
2. _unescape_snippet('a\\"b\\"c') == 'a"b"c'  (new helper, deferred import per §1q/D1CF5FDF).
3. verify() end-to-end: escaped-quote snippet citation resolves True against a
   temp file whose line contains real (unescaped) quotes.
4. Regression: quote-free snippet citations still verify True (common path).
5. Negative: a snippet whose fragment is absent → verified False.

Tests MUST FAIL until GREEN ships the additions, EXCEPT AC4 and AC5 (guards).
AC2 defers _unescape_snippet import to inside the test body so the file
remains collectable pre-GREEN (§1q / D1CF5FDF — missing symbol = ~30 min hang).

Do NOT implement any contract here — RED-only file.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
ENGINE_ROOT = HERE.parent

from bytedigger_engine.scripts.lib.citation_verifier import (  # noqa: E402
    Citation,
    extract_citations,
    verify,
)


# ─── AC1 — extract_citations captures full fragment with escaped inner quotes ──


def test_extract_citations_escaped_inner_quotes_full_fragment() -> None:
    """AC1: snippet citation with escaped inner quotes → identifier spans full fragment.

    Input text contains:  tools.py:"layer: Literal[\\"P\\", \\"D\\", \\"E\\"]"
    Pre-GREEN: _SNIPPET_CITE_RE char class [^"\\n] truncates at the first
    backslash-quote, yielding identifier == 'layer: Literal[\\' (missing 'E]').
    Post-GREEN: escaped-aware alternation (?:[^"\\\\\\n]|\\\\.) captures all the way
    to the closing unescaped '"', so identifier contains 'E]'.
    """
    # The raw string as it would appear in a spec document (backslash-escaped inner quotes).
    text = 'tools.py:"layer: Literal[\\"P\\", \\"D\\", \\"E\\"]"'
    citations = extract_citations(text)

    assert len(citations) == 1, (
        f"expected exactly 1 citation, got {len(citations)}: {citations!r}"
    )
    cit = citations[0]
    assert cit.kind == "snippet", f"expected kind='snippet', got {cit.kind!r}"
    assert "D" in cit.identifier and cit.identifier.rstrip().endswith("]"), (
        f"expected full fragment, got truncated identifier={cit.identifier!r}"
    )


# ─── AC2 — _unescape_snippet helper exists and reverses \\"-escaping ──────────


def test_unescape_snippet_helper_reverses_backslash_quote() -> None:
    """AC2: _unescape_snippet('a\\\\"b\\\\"c') == 'a"b"c'.

    _unescape_snippet does NOT exist pre-GREEN.
    Import is deferred INSIDE the function body (§1q / D1CF5FDF): a top-level
    import of a missing symbol makes the file non-collectable → ~30 min hang.
    The file must COLLECT cleanly and FAIL here at assert/ImportError time.
    """
    # Deferred import — MUST stay inside the function body.
    from bytedigger_engine.scripts.lib.citation_verifier import _unescape_snippet  # type: ignore  # noqa: F401

    # The call is hoisted out of the f-string: a backslash inside an f-string EXPRESSION is
    # a SyntaxError before Python 3.12 (PEP 701), and bytedigger's clean-room jobs run 3.11.
    # Upstream HAL runs 3.14, where the same line parses — hence the port edit.
    got = _unescape_snippet('a\\"b\\"c')
    assert got == 'a"b"c', (
        f"_unescape_snippet should reverse backslash-quote escaping; got {got!r}"
    )
    # Also verify no-op on input without escapes.
    assert _unescape_snippet("no quotes here") == "no quotes here"


# ─── AC3 — end-to-end: escaped-quote snippet citation verifies True ───────────


def test_verify_escaped_quote_snippet_end_to_end(tmp_path: pytest.TempPathFactory) -> None:
    """AC3: temp file with real quotes; citation with escaped quotes → verified=True.

    File content:  layer: Literal["P", "D", "E"]  (real double-quotes)
    Citation identifier (escaped form):  layer: Literal[\\"P\\", \\"D\\", \\"E\\"]
    Pre-GREEN: verify() uses raw identifier as needle; 'layer: Literal[\\"P\\",…'
    is not in file → verified=False → FAIL.
    Post-GREEN: _unescape_snippet candidate is tried; unescaped form IS in file
    → verified=True.
    """
    # Write a temp file whose line contains real (unescaped) double-quote characters.
    target = tmp_path / "tools.py"
    target.write_text(
        'def foo():\n'
        '    layer: Literal["P", "D", "E"] = "P"\n'
        '    return layer\n',
        encoding="utf-8",
    )

    # Build a snippet Citation using the escaped form (as the contract mandates).
    cit = Citation(
        kind="snippet",
        identifier='layer: Literal[\\"P\\", \\"D\\", \\"E\\"]',
        file_path="tools.py",
        text_offset=0,
        text_snippet='tools.py:"layer: Literal[\\"P\\", \\"D\\", \\"E\\"]"',
    )

    result = verify(cit, tmp_path)

    assert result.verified is True, (
        f"expected verified=True for escaped-quote snippet, "
        f"got verified={result.verified}, evidence={result.evidence!r}"
    )
    assert "snippet found" in result.evidence, (
        f"evidence must contain 'snippet found', got: {result.evidence!r}"
    )


# ─── AC4 — regression: quote-free snippet citation still verifies True ────────


def test_verify_quote_free_snippet_still_passes(tmp_path: pytest.TempPathFactory) -> None:
    """AC4 (regression guard): quote-free snippet → verified=True (common path unbroken).

    This test SHOULD PASS pre-GREEN (no code change needed to the common path).
    If it fails post-GREEN, the fix introduced a regression.
    """
    target = tmp_path / "project_root.py"
    target.write_text(
        "def resolve_project_root(cfg):\n"
        "    \"\"\"Resolve the project root from cfg.\"\"\"\n"
        "    return cfg.hal_root\n",
        encoding="utf-8",
    )

    cit = Citation(
        kind="snippet",
        identifier="def resolve_project_root(cfg):",
        file_path="project_root.py",
        text_offset=0,
        text_snippet='project_root.py:"def resolve_project_root(cfg):"',
    )

    result = verify(cit, tmp_path)

    assert result.verified is True, (
        f"regression: quote-free snippet must still verify True; "
        f"got verified={result.verified}, evidence={result.evidence!r}"
    )


# ─── AC5 — negative control: absent fragment → verified=False ─────────────────


def test_verify_absent_snippet_returns_false(tmp_path: pytest.TempPathFactory) -> None:
    """AC5 (negative control): snippet fragment genuinely absent → verified=False.

    Prevents tautological pass — forces AC3 to be a real content-grep check,
    not a vacuously-True verify().
    """
    target = tmp_path / "tools.py"
    target.write_text(
        "def foo():\n"
        "    x: int = 1\n"
        "    return x\n",
        encoding="utf-8",
    )

    cit = Citation(
        kind="snippet",
        # This fragment is NOT in the file above.
        identifier='layer: Literal[\\"P\\", \\"D\\", \\"E\\"]',
        file_path="tools.py",
        text_offset=0,
        text_snippet='tools.py:"layer: Literal[\\"P\\", \\"D\\", \\"E\\"]"',
    )

    result = verify(cit, tmp_path)

    assert result.verified is False, (
        f"expected verified=False for absent snippet fragment; "
        f"got verified={result.verified}, evidence={result.evidence!r}"
    )
