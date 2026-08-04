"""RED tests for GH922 — snippet cite-lint multiline `\\n` escape decode.

AC1-3 target `_decode_ws_escapes` directly (deferred import — not yet
implemented, non-collectable-RED rule D1CF5FDF: import happens inside the
test body so the file still collects).  AC4-8 exercise verify() end-to-end.

Do NOT implement any contract here — RED-only file.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
ENGINE_ROOT = HERE.parent

from bytedigger_engine.scripts.lib.citation_verifier import Citation, verify  # noqa: E402


# ─── AC1 — _decode_ws_escapes importable; \n -> single space ──────────────


def test_decode_ws_escapes_literal_backslash_n_becomes_space() -> None:
    """AC1 (MUST FAIL pre-GREEN): _decode_ws_escapes does not exist yet."""
    from bytedigger_engine.scripts.lib.citation_verifier import _decode_ws_escapes  # noqa: E402

    # Python literal "a\\nb" = runtime 5 chars: a \ n b -> a, backslash, n, b
    src = "a\\nb"
    assert len(src) == 4
    assert src.count("\\") == 1

    result = _decode_ws_escapes(src)
    assert result == "a b", f"expected 'a b', got {result!r}"


# ─── AC2 — \t and \r also decode to single space ───────────────────────────


def test_decode_ws_escapes_tab_and_cr_become_space() -> None:
    """AC2 (MUST FAIL pre-GREEN): \\t and \\r decode to single space too."""
    from bytedigger_engine.scripts.lib.citation_verifier import _decode_ws_escapes  # noqa: E402

    src = "a\\tb\\rc"
    result = _decode_ws_escapes(src)
    assert result == "a b c", f"expected 'a b c', got {result!r}"


# ─── AC3 — other escapes untouched ──────────────────────────────────────────


def test_decode_ws_escapes_leaves_other_escapes_untouched() -> None:
    """AC3 (MUST FAIL pre-GREEN): \\" and \\d (and similar) pass through unchanged."""
    from bytedigger_engine.scripts.lib.citation_verifier import _decode_ws_escapes  # noqa: E402

    quoted = 'a\\"b'
    assert _decode_ws_escapes(quoted) == quoted, (
        f"escaped-quote sequence must remain untouched, got {_decode_ws_escapes(quoted)!r}"
    )

    regex_src = "re.sub(r'\\\\d+')"
    result = _decode_ws_escapes(regex_src)
    assert "\\d" in result, (
        f"\\d must remain intact (no accidental n/t/r-in-\\d decoding), got {result!r}"
    )
    assert result == regex_src, f"expected no change to {regex_src!r}, got {result!r}"


# ─── AC4 — repro: GH922 multiline-wrapped snippet verifies True ───────────


def test_verify_multiline_wrapped_snippet_repro_gh922(tmp_path: Path) -> None:
    """AC4 (MUST FAIL pre-GREEN): literal-\\n-wrapped citation matches real 2-line source.

    File content (real newline between the two physical lines):
        this script is
            standalone and must not import demo modules
    Citation identifier serializes the wrap as a literal 2-char backslash-n
    followed by the 4-space indent, all on one line.
    """
    target = tmp_path / "mark_orphaned_evidence.py"
    target.write_text(
        "this script is\n"
        "    standalone and must not import demo modules\n",
        encoding="utf-8",
    )

    # Python literal with "\\n" = runtime: backslash + n (2 chars), NOT a real newline.
    identifier = "this script is\\n    standalone and must not import demo modules"
    assert "\n" not in identifier
    assert "\\n" in identifier

    cit = Citation(
        kind="snippet",
        identifier=identifier,
        file_path="mark_orphaned_evidence.py",
        text_offset=0,
        text_snippet=f'mark_orphaned_evidence.py:"{identifier}"',
    )

    result = verify(cit, tmp_path)

    assert result.verified is True, (
        f"expected verified=True for multiline-wrapped snippet; "
        f"got verified={result.verified}, evidence={result.evidence!r}"
    )
    assert "snippet found" in result.evidence, (
        f"expected 'snippet found' substring in evidence, got {result.evidence!r}"
    )


# ─── AC5 — regression: plain single-line exact snippet still verifies True ──


def test_verify_plain_single_line_snippet_regression(tmp_path: Path) -> None:
    """AC5 (regression guard, passes pre & post GREEN): no \\n involved at all."""
    target = tmp_path / "plain.py"
    target.write_text("def foo():\n    return 42\n", encoding="utf-8")

    identifier = "return 42"
    cit = Citation(
        kind="snippet",
        identifier=identifier,
        file_path="plain.py",
        text_offset=0,
        text_snippet=f'plain.py:"{identifier}"',
    )

    result = verify(cit, tmp_path)

    assert result.verified is True, (
        f"regression AC5: plain single-line snippet must verify True; "
        f"got verified={result.verified}, evidence={result.evidence!r}"
    )


# ─── AC6 — absent snippet with literal \n → verified False ────────────────


def test_verify_absent_snippet_with_literal_backslash_n_returns_false(
    tmp_path: Path,
) -> None:
    """AC6 (negative control, passes pre & post GREEN): decoded-but-absent content."""
    target = tmp_path / "other.py"
    target.write_text("totally unrelated content\n", encoding="utf-8")

    identifier = "this fragment is\\n    nowhere in the file"
    cit = Citation(
        kind="snippet",
        identifier=identifier,
        file_path="other.py",
        text_offset=0,
        text_snippet=f'other.py:"{identifier}"',
    )

    result = verify(cit, tmp_path)

    assert result.verified is False, (
        f"expected verified=False for absent multiline-wrapped snippet; "
        f"got verified={result.verified}, evidence={result.evidence!r}"
    )
    assert "snippet not found in file" in result.evidence, (
        f"expected 'snippet not found in file' substring, got {result.evidence!r}"
    )


# ─── AC7 — decoded-but-absent tail content → verified False ────────────────


def test_verify_decoded_but_absent_tail_returns_false(tmp_path: Path) -> None:
    """AC7 (negative control, passes pre & post GREEN): head matches, tail does not."""
    target = tmp_path / "alpha.py"
    target.write_text("alpha\nWWWYY\n", encoding="utf-8")

    identifier = "alpha\\nQQQZZ"
    cit = Citation(
        kind="snippet",
        identifier=identifier,
        file_path="alpha.py",
        text_offset=0,
        text_snippet=f'alpha.py:"{identifier}"',
    )

    result = verify(cit, tmp_path)

    assert result.verified is False, (
        f"expected verified=False when only the head of a wrapped snippet matches; "
        f"got verified={result.verified}, evidence={result.evidence!r}"
    )


# ─── AC8 — compose: escaped-quote AND literal \n in same identifier ────────


def test_verify_compose_escaped_quote_and_multiline_wrap(tmp_path: Path) -> None:
    """AC8 (MUST FAIL pre-GREEN): identifier combines \\" and literal \\n; source has

    a real quote character split across a real line wrap.
    """
    target = tmp_path / "compose.py"
    target.write_text(
        'layer: Literal["P"] = start\n'
        '    finish\n',
        encoding="utf-8",
    )

    # Python literal: 'layer: Literal[\\"P\\"] = start\\n    finish'
    # Runtime: layer: Literal[\"P\"] = start<backslash>n    finish
    identifier = 'layer: Literal[\\"P\\"] = start\\n    finish'
    assert '\\"' in identifier
    assert "\\n" in identifier
    assert "\n" not in identifier

    cit = Citation(
        kind="snippet",
        identifier=identifier,
        file_path="compose.py",
        text_offset=0,
        text_snippet=f'compose.py:"{identifier}"',
    )

    result = verify(cit, tmp_path)

    assert result.verified is True, (
        f"expected verified=True for composed escaped-quote + multiline-wrap snippet; "
        f"got verified={result.verified}, evidence={result.evidence!r}"
    )
