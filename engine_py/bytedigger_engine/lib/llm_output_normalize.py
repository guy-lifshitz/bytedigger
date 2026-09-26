"""One normalizer for model output before any marker/verdict parse (bd#84).

Models dress the same answer differently: ``**VERDICT: FAIL**``,
`` `VERDICT: FAIL` ``, ``### Verdict`` / ``**REVISE**``, a finding header
without the ``SEVERITY:`` word. Every anchored parser used to carry its own
tolerance for some of these and none for the rest, so a clear answer parsed
as UNKNOWN (fail-closed) or a real finding was not counted (fail-open).

This module is the single place that strips that dressing. Parsers stay
strict about *structure* (line anchors, last-wins, choice lists); they no
longer need to know about markdown.

Entry point: ``normalize_model_output`` (line-preserving; fenced code blocks
stay byte-identical — code in a reply is quoted material, never the reply's
own verdict). ``fence_mask`` and ``strip_emphasis`` are shared with
``review_schema.canonical.canonicalize_severity_headers``, which applies the
same dressing rules to finding headers.
"""
from __future__ import annotations

import re

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

def _unwrap_bold_underscore(m: re.Match[str]) -> str:
    # verdict-anchor: exempt — part of the normalizer itself.
    inner = m.group(1)
    # `__init__` is an identifier, not emphasis: unwrap only prose content.
    return inner if re.search(r"\W", inner) else m.group(0)


def strip_emphasis(text: str) -> str:
    text = _BOLD_STAR_RE.sub(r"\1", text)
    return _BOLD_UNDERSCORE_RE.sub(_unwrap_bold_underscore, text)


def _normalize_line(line: str, *, unwrap_code_span: bool = False) -> str:
    while True:
        before = line
        if unwrap_code_span and (m := _CODE_SPAN_LINE_RE.match(line)):
            line = m.group(1) + m.group(2)
        line = strip_emphasis(line)
        m = _HEADING_RE.match(line)
        if m and len(m.group(1).split()) <= _MAX_MARKER_HEADING_WORDS:
            line = m.group(1)
        line = line.rstrip()
        if line == before:
            return line


def fence_mask(lines: list[str]) -> list[bool]:
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
    fenced = fence_mask(lines)
    out = [line if f else _normalize_line(line) for line, f in zip(lines, fenced)]
    _unwrap_final_code_span(out, fenced)
    return "\n".join(out)
