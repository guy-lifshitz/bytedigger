"""One normalizer for model output before any marker/verdict parse (bd#84).

Models dress the same answer differently: ``**VERDICT: FAIL**``,
`` `VERDICT: FAIL` ``, ``### Verdict`` / ``**REVISE**``, a finding header
without the ``SEVERITY:`` word. Every anchored parser used to carry its own
tolerance for some of these and none for the rest, so a clear answer parsed
as UNKNOWN (fail-closed) or a real finding was not counted (fail-open).

This module is the single place that strips that dressing. Parsers stay
strict about *structure* (line anchors, last-wins, choice lists); they no
longer need to know about markdown.

Two entry points:

- ``normalize_model_output`` — for verdict/marker parsers. Line-preserving.
- ``canonicalize_severity_headers`` — for the review aggregator. Rewrites a
  finding-shaped ``##``-``####`` heading into the canonical
  ``### SEVERITY: <LEVEL> — <title>`` form; every other line is untouched.

Both leave lines inside a fenced code block byte-identical: code in a reply
is quoted material, never the reply's own verdict or finding.
"""
from __future__ import annotations

import re
from typing import Callable, Iterator

from bytedigger_engine.lib.plugins.review_schema.canonical import SEVERITY_HDR_LINE_RE

_FENCE_OPEN_RE = re.compile(r"^[ ]{0,3}(`{3,}|~{3,})[^`]*$")
_CODE_SPAN_LINE_RE = re.compile(r"^([ \t]*)`([^`\n]+)`[ \t]*$")
_BOLD_STAR_RE = re.compile(r"\*\*(?=\S)(.+?)(?<=\S)\*\*")
_BOLD_UNDERSCORE_RE = re.compile(r"__(?=\S)(.+?)(?<=\S)__")
_HEADING_RE = re.compile(r"^[ ]{0,3}#{1,6}[ \t]+(.*?)(?:[ \t]+#+)?[ \t]*$")
# A heading is un-headed only when it is short enough to be a marker line
# (`Verdict`, `VERDICT: PASS`, `STATUS: DONE`). A longer heading such as
# `## Verdict: PASS criteria` is a section title and keeps its `#`, so the
# line-anchored parsers keep ignoring it.
_MAX_MARKER_HEADING_WORDS = 2

_LEVELS = "CRITICAL|HIGH|MEDIUM|LOW"
_SEVERITY_HEADING_RE = re.compile(r"^[ ]{0,3}#{2,4}[ \t]+(.*?)[ \t]*$")
_SEVERITY_TEXT_RE = re.compile(
    r"^(?:(?P<word>SEVERITY)[ \t]*:?[ \t]*)?"
    rf"(?P<level>{_LEVELS})\b"
    r"(?P<sep>[ \t]*[—–][ \t]*|[ \t]+-[ \t]+|[ \t]*:[ \t]*)"
    r"(?P<title>\S.*?)[ \t]*$",
    re.IGNORECASE,
)
# `### Critical — none found` / `### LOW — None.` report the absence of
# findings, not a finding.
_PLACEHOLDER_TITLE_RE = re.compile(
    r"^(?:none|n/?a|nothing|no\s+(?:issues|findings|problems))\b", re.IGNORECASE
)


def _unwrap_bold_underscore(m: re.Match[str]) -> str:
    # verdict-anchor: exempt — part of the normalizer itself.
    inner = m.group(1)
    # `__init__` is an identifier, not emphasis: unwrap only prose content.
    return inner if re.search(r"\W", inner) else m.group(0)


def _strip_emphasis(text: str) -> str:
    text = _BOLD_STAR_RE.sub(r"\1", text)
    return _BOLD_UNDERSCORE_RE.sub(_unwrap_bold_underscore, text)


def _normalize_line(line: str) -> str:
    while True:
        before = line
        m = _CODE_SPAN_LINE_RE.match(line)
        if m:
            line = m.group(1) + m.group(2)
        line = _strip_emphasis(line)
        m = _HEADING_RE.match(line)
        if m and len(m.group(1).split()) <= _MAX_MARKER_HEADING_WORDS:
            line = m.group(1)
        line = line.rstrip()
        if line == before:
            return line


def _map_outside_fences(lines: list[str], fn: Callable[[str], str]) -> Iterator[str]:
    """Apply ``fn`` to lines outside fenced code blocks; fenced blocks,
    delimiters included, pass through byte-identical.

    A fence closes on a line of the same character, at least as long as the
    opener, with nothing else on it (CommonMark). An unclosed fence runs to
    the end of the text. verdict-anchor: exempt — part of the normalizer itself.
    """
    closer: re.Pattern[str] | None = None
    for line in lines:
        if closer is None:
            m = _FENCE_OPEN_RE.match(line)
            if m:
                run = m.group(1)
                closer = re.compile(rf"^[ ]{{0,3}}{re.escape(run[0])}{{{len(run)},}}\s*$")
                yield line
                continue
            yield fn(line)
        else:
            if closer.match(line):
                closer = None
            yield line


def normalize_model_output(raw: str | None) -> str | None:
    """Strip markdown dressing from model output so anchored parsers see the text.

    Falsy input passes through unchanged. CRLF/CR become LF. Fenced code
    blocks are kept verbatim, delimiters included, so a second pass never
    reaches fenced content. Outside fences each line is normalized to a
    fixed point: a line that is exactly one code span
    is unwrapped, paired ``**``/``__`` emphasis is removed, and a short
    heading (at most two words) loses its ``#`` marker. Idempotent.
    """
    if not raw:
        return raw
    lines = raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "\n".join(_map_outside_fences(lines, _normalize_line))


def _canonical_severity_line(line: str) -> str:
    if SEVERITY_HDR_LINE_RE.match(line):
        return line  # already parses (GH970 form) — keep byte-identical
    m = _SEVERITY_HEADING_RE.match(line)
    if not m:
        return line
    text = _strip_emphasis(m.group(1))
    t = _SEVERITY_TEXT_RE.match(text)
    if not t:
        return line
    # Without the SEVERITY word a colon is too weak a separator:
    # `### Critical: none` is a section title, not a finding.
    if not t.group("word") and ":" in t.group("sep"):
        return line
    title = t.group("title").strip().strip("*_").strip()
    if not title or _PLACEHOLDER_TITLE_RE.match(title):
        return line
    return f"### SEVERITY: {t.group('level').upper()} — {title}"


def canonicalize_severity_headers(text: str) -> str:
    """Rewrite finding-shaped ``##``-``####`` headings to the canonical form.

    ``### HIGH — title``, ``## **MEDIUM** - title`` and
    ``#### Severity: critical: title`` all become
    ``### SEVERITY: <LEVEL> — <title>``. A line that already parses under
    ``SEVERITY_HDR_LINE_RE`` (e.g. ``## SEVERITY: HIGH - t``) is left as is.
    The heading range matches the GH970
    tolerance of ``SEVERITY_HDR_CORE`` (``#`` and ``#####`` stay invisible).
    The level must open the heading text and be followed by a real separator,
    so ``### High-level summary`` and ``## Low-hanging fruit`` are untouched.
    """
    if not text:
        return text
    return "\n".join(_map_outside_fences(text.split("\n"), _canonical_severity_line))
