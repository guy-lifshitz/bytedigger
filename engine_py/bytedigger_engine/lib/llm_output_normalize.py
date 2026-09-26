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
  finding-shaped heading or bold line into the canonical
  ``### SEVERITY: <LEVEL> — <title>`` form; every other line is untouched.

Both leave fenced code blocks byte-identical: code in a reply is quoted
material, never the reply's own verdict or finding.
"""
from __future__ import annotations

import re

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

# Finding-shaped lines: a `##`-`####` heading (the GH970 range — `#` and
# `#####` stay invisible) or a line that opens with bold.
_SEVERITY_HEADING_RE = re.compile(r"^[ ]{0,3}#{2,4}[ \t]+(.*?)[ \t]*$")
_BOLD_LEAD_RE = re.compile(r"^[ ]{0,3}(\*\*.*?)[ \t]*$")
_SEVERITY_TEXT_RE = re.compile(
    r"^(?:(?P<word>SEVERITY)[ \t]*:?[ \t]*)?"
    r"(?P<level>CRITICAL|HIGH|MEDIUM|LOW)\b"
    r"(?:[ \t]*[—–][ \t]*|[ \t]+-[ \t]+|[ \t]*:[ \t]*)"
    r"(?P<title>\S.*?)[ \t]*$",
    re.IGNORECASE,
)
# `### Critical — No critical issues found` / `### LOW — None.` report the
# absence of findings. Whole-title match only: `None of the error paths are
# tested` is a real finding.
_PLACEHOLDER_TITLE_RE = re.compile(
    r"^(?:(?:none|nothing|(?:no|0|zero)\s+(?:\w+\s+){0,2}(?:issues?|findings?|problems?|bugs?))"
    r"(?:\s+(?:found|identified|reported|to\s+report))?|n/?a)[.!]?$",
    re.IGNORECASE,
)


def _unwrap_bold_underscore(m: re.Match[str]) -> str:
    # verdict-anchor: exempt — part of the normalizer itself.
    inner = m.group(1)
    # `__init__` is an identifier, not emphasis: unwrap only prose content.
    return inner if re.search(r"\W", inner) else m.group(0)


def _strip_emphasis(text: str) -> str:
    text = _BOLD_STAR_RE.sub(r"\1", text)
    return _BOLD_UNDERSCORE_RE.sub(_unwrap_bold_underscore, text)


def _normalize_line(line: str, *, unwrap_code_span: bool = False) -> str:
    while True:
        before = line
        m = _CODE_SPAN_LINE_RE.match(line)
        if m and unwrap_code_span:
            line = m.group(1) + m.group(2)
        line = _strip_emphasis(line)
        m = _HEADING_RE.match(line)
        if m and len(m.group(1).split()) <= _MAX_MARKER_HEADING_WORDS:
            line = m.group(1)
        line = line.rstrip()
        if line == before:
            return line


def _fence_mask(lines: list[str]) -> list[bool]:
    """Per line: True when it belongs to a fenced code block (delimiters included).

    A fence closes on a line of the same character, at least as long as the
    opener, with nothing else on it (CommonMark). An unclosed fence runs to
    the end of the text. verdict-anchor: exempt — part of the normalizer itself.
    """
    mask: list[bool] = []
    closer: re.Pattern[str] | None = None
    for line in lines:
        if closer is None:
            m = _FENCE_OPEN_RE.match(line)
            if m:
                run = m.group(1)
                closer = re.compile(rf"^[ ]{{0,3}}{re.escape(run[0])}{{{len(run)},}}\s*$")
            mask.append(m is not None)
        else:
            if closer.match(line):
                closer = None
            mask.append(True)
    return mask


def _unwrap_final_code_span(lines: list[str], fenced: list[bool]) -> None:
    """Unwrap the reply's last non-blank line when it is exactly one code span.

    Models often close with `` `VERDICT: FAIL` ``. A code span anywhere else
    is quoted material (an example, the expected output), and so is a final
    one whose key (text before its first colon) already opens an earlier
    plain line: `VERDICT: FAIL` … `` `VERDICT: PASS` `` keeps FAIL.
    verdict-anchor: exempt — part of the normalizer itself.
    """
    last = next((i for i in range(len(lines) - 1, -1, -1) if lines[i].strip()), None)
    if last is None or fenced[last]:
        return
    m = _CODE_SPAN_LINE_RE.match(lines[last])
    if not m:
        return
    inner = m.group(2)
    if ":" in inner:
        key = inner.split(":", 1)[0].strip().lower() + ":"
        if any(not fenced[i] and lines[i].lstrip().lower().startswith(key) for i in range(last)):
            return
    lines[last] = _normalize_line(lines[last], unwrap_code_span=True)


def normalize_model_output(raw: str | None) -> str | None:
    """Strip markdown dressing from model output so anchored parsers see the text.

    Falsy input passes through unchanged. CRLF/CR become LF. Fenced code
    blocks are kept verbatim, delimiters included, so a second pass never
    reaches fenced content. Outside fences each line is normalized to a
    fixed point: paired ``**``/``__`` emphasis is removed and a short heading
    (at most two words) loses its ``#`` marker. The final line is unwrapped
    when it is exactly one code span (see ``_unwrap_final_code_span``).
    Idempotent.
    """
    if not raw:
        return raw
    lines = raw.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    fenced = _fence_mask(lines)
    out = [line if f else _normalize_line(line) for line, f in zip(lines, fenced)]
    _unwrap_final_code_span(out, fenced)
    return "\n".join(out)


def _canonical_severity_line(line: str) -> str:
    if SEVERITY_HDR_LINE_RE.match(line):
        return line  # already parses (GH970 form) — keep byte-identical
    m = _SEVERITY_HEADING_RE.match(line) or _BOLD_LEAD_RE.match(line)
    if not m:
        return line
    t = _SEVERITY_TEXT_RE.match(_strip_emphasis(m.group(1)))
    if not t:
        return line
    level = t.group("level")
    # Without the SEVERITY word only an UPPERCASE level marks a finding:
    # `### High — Summary of review` and `## Low - priority items` are titles.
    if not t.group("word") and level != level.upper():
        return line
    title = t.group("title").strip().strip("*_").strip()
    if not title or _PLACEHOLDER_TITLE_RE.match(title):
        return line
    return f"### SEVERITY: {level.upper()} — {title}"


def canonicalize_severity_headers(text: str) -> str:
    """Rewrite finding-shaped lines to ``### SEVERITY: <LEVEL> — <title>``.

    A finding-shaped line is a ``##``-``####`` heading (the GH970 tolerance
    of ``SEVERITY_HDR_CORE``; ``#`` and ``#####`` stay invisible) or a line
    that opens with bold, whose text starts with ``SEVERITY[:] <level>`` (any
    case) or an UPPERCASE level, then a dash or colon and a real title:
    ``### HIGH — t``, ``**SEVERITY: LOW** — t``, ``#### Severity: critical: t``.
    A line that already parses under ``SEVERITY_HDR_LINE_RE`` is left as is,
    as are placeholders (``— none found``), fenced lines and plain body lines.
    """
    if not text:
        return text
    lines = text.split("\n")
    return "\n".join(
        line if f else _canonical_severity_line(line) for line, f in zip(lines, _fence_mask(lines))
    )
