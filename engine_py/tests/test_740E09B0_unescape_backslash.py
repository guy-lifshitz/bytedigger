"""RED tests for 740E09B0 — snippet citation verifier: escape-insensitive backslash fallback.

AC1 is the ONLY pre-GREEN failure.  AC2, AC3, AC4 are regression/correctness guards
that must pass both before and after GREEN ships.

Contract:
  verify() must return verified=True when the citation identifier contains DOUBLED
  backslashes (e.g. r'\\[BF-\\d+\\]') and the actual file line contains the same
  content with SINGLE backslashes (e.g. r'\[BF-\d+\]').  This covers upstream
  serialization that doubles backslash characters in snippet identifiers.

Do NOT implement any contract here — RED-only file.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
ENGINE_ROOT = HERE.parent

from bytedigger_engine.scripts.lib.citation_verifier import Citation, verify  # noqa: E402


# ─── AC1 — doubled-backslash identifier matches single-backslash file content ──


def test_verify_doubled_backslash_identifier_matches_single_backslash_file(
    tmp_path: Path,
) -> None:
    """AC1 (MUST FAIL pre-GREEN): doubled-backslash identifier → verified=True.

    File line (single backslashes, as written in actual Python source):
        x = re.sub(r'\[BF-\d+\]', '', s)
    Runtime string in file:  x = re.sub(r'\\[BF-\\d+\\]', '', s)
      (each \\[ in this Python literal = a literal single backslash followed by [)

    Citation identifier (doubled backslashes, as if upstream serializer doubled them):
        In Python literal:  "x = re.sub(r'\\\\[BF-\\\\d+\\\\]', '', s)"
        Runtime string:     x = re.sub(r'\\[BF-\\d+\\]', '', s)
      (each \\\\ = two chars \\, so runtime has \\ before each [, d, ])

    Current verify() calls _unescape_snippet(identifier) which only unescapes \\"→".
    It does NOT unescape \\→\, so the doubled-backslash needle does not match
    the single-backslash haystack.  This test fails pre-GREEN, passes post-GREEN
    when an escape-insensitive backslash fallback is added.
    """
    # Write file with SINGLE backslashes in the regex literal.
    # Python string "\\[" = literal two-char sequence: backslash + [
    file_line = "x = re.sub(r'\\[BF-\\d+\\]', '', s)\n"
    # Verify our intent: the runtime string contains single backslashes.
    # file_line at runtime: x = re.sub(r'\[BF-\d+\]', '', s)\n
    assert file_line.count("\\") == 3, (
        f"expected 3 single backslashes in file line, got {file_line.count(chr(92))}: {file_line!r}"
    )

    prod_py = tmp_path / "prod.py"
    prod_py.write_text(file_line, encoding="utf-8")

    # Build identifier with DOUBLED backslashes (upstream serialization artifact).
    # Python string "\\\\[" = literal two-char sequence: backslash + backslash + [
    doubled_id = "x = re.sub(r'\\\\[BF-\\\\d+\\\\]', '', s)"
    # Verify our intent: the runtime string contains double backslashes.
    # doubled_id at runtime: x = re.sub(r'\\[BF-\\d+\\]', '', s)
    assert doubled_id.count("\\\\") == 3, (
        f"expected 3 double-backslash pairs in identifier, got {doubled_id.count(chr(92)*2)}: {doubled_id!r}"
    )

    cit = Citation(
        kind="snippet",
        identifier=doubled_id,
        file_path="prod.py",
        text_offset=0,
        text_snippet=f'prod.py:"{doubled_id}"',
    )

    result = verify(cit, tmp_path)

    assert result.verified is True, (
        f"expected verified=True for doubled-backslash identifier matching single-backslash file; "
        f"got verified={result.verified}, evidence={result.evidence!r}. "
        f"identifier runtime value: {doubled_id!r}, "
        f"file content: {file_line!r}"
    )


# ─── AC2 — regression: escaped-quote snippet citation still verifies True ──────


def test_verify_escaped_quote_snippet_regression(tmp_path: Path) -> None:
    """AC2 (regression guard, passes pre & post GREEN): escaped-quote case (#133).

    File line contains real double-quote characters.
    Citation identifier uses backslash-escaped quotes (\\" form).
    This was fixed in B7045745 and must remain working after 740E09B0 GREEN.
    """
    target = tmp_path / "tools.py"
    target.write_text(
        'def foo():\n'
        '    layer: Literal["P", "D", "E"] = "P"\n'
        '    return layer\n',
        encoding="utf-8",
    )

    # Identifier: layer: Literal[\"P\", \"D\", \"E\"]
    # In Python literal:  'layer: Literal[\\"P\\", \\"D\\", \\"E\\"]'
    # Runtime string:     layer: Literal[\"P\", \"D\", \"E\"]
    cit = Citation(
        kind="snippet",
        identifier='layer: Literal[\\"P\\", \\"D\\", \\"E\\"]',
        file_path="tools.py",
        text_offset=0,
        text_snippet='tools.py:"layer: Literal[\\"P\\", \\"D\\", \\"E\\"]"',
    )

    result = verify(cit, tmp_path)

    assert result.verified is True, (
        f"regression AC2: escaped-quote snippet must verify True; "
        f"got verified={result.verified}, evidence={result.evidence!r}"
    )


# ─── AC3 — regression: single-backslash needle == file line → verified True ────


def test_verify_single_backslash_needle_matches_file(tmp_path: Path) -> None:
    """AC3 (regression guard, passes pre & post GREEN): identifier == file line exactly.

    When identifier already has single backslashes (matching what is in the file),
    the existing normalize+grep path must continue to return verified=True.
    No escape transformation needed — direct match.
    """
    # File and identifier both have single backslashes.
    file_line = "pattern = re.compile(r'\\d+\\.\\w+')\n"
    # Runtime: pattern = re.compile(r'\d+\.\w+')\n  (single backslashes)
    assert "\\\\" not in file_line, (
        f"AC3 setup error: file_line must not have doubled backslashes: {file_line!r}"
    )

    prod_py = tmp_path / "utils.py"
    prod_py.write_text(file_line, encoding="utf-8")

    # Identifier is the SAME as what is in the file (single backslashes).
    identifier = "pattern = re.compile(r'\\d+\\.\\w+')"
    # Runtime: pattern = re.compile(r'\d+\.\w+')

    cit = Citation(
        kind="snippet",
        identifier=identifier,
        file_path="utils.py",
        text_offset=0,
        text_snippet=f'utils.py:"{identifier}"',
    )

    result = verify(cit, tmp_path)

    assert result.verified is True, (
        f"regression AC3: single-backslash needle must verify True; "
        f"got verified={result.verified}, evidence={result.evidence!r}"
    )


# ─── AC4 — negative control: absent doubled-backslash fragment → verified False ─


def test_verify_absent_doubled_backslash_fragment_returns_false(tmp_path: Path) -> None:
    """AC4 (negative control, passes pre & post GREEN): absent fragment → verified=False.

    Uses a doubled-backslash identifier whose content (even after stripping all
    backslashes) does not appear in the file.  This prevents the escape-insensitive
    fallback from vacuously matching anything.

    File contains only a simple assignment with no backslashes and no overlap with
    the identifier's non-backslash characters.
    """
    prod_py = tmp_path / "prod.py"
    prod_py.write_text(
        "x = 1\n"
        "y = 2\n",
        encoding="utf-8",
    )

    # Identifier with doubled backslashes; content after stripping \ = "re.sub(QQQQ, '', s)"
    # which is genuinely absent from the file above.
    absent_id = "re.sub(r'\\\\QQQQ\\\\d+', '', s)"
    # Runtime: re.sub(r'\\QQQQ\\d+', '', s)
    # After stripping backslashes: re.sub(r'QQQQd+', '', s) — not in file.

    cit = Citation(
        kind="snippet",
        identifier=absent_id,
        file_path="prod.py",
        text_offset=0,
        text_snippet=f'prod.py:"{absent_id}"',
    )

    result = verify(cit, tmp_path)

    assert result.verified is False, (
        f"expected verified=False for absent doubled-backslash fragment; "
        f"got verified={result.verified}, evidence={result.evidence!r}. "
        f"File content must not contain any form of the identifier."
    )
