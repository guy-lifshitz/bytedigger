"""Single-source verdict/marker parser (chokepoint EEFD480F).

Wave A — P1, P3, P4 primitives. Wave B adds P2. Wave C folds phase_6_smoke.
Algorithm changes = 1 file; callers pass domain token tuples.

bd#84 — two passes. P1-P3 first read the reply as written (CRLF-normalized
only), exactly as before; only when that finds nothing do they read it again
through ``llm_output_normalize.normalize_model_output``, which strips the
markdown dressing (bold, short headings, a final code span) that made clear
answers parse as UNKNOWN. The normalized pass can turn UNKNOWN into a
verdict; it can never change a verdict the plain reading already found.
In both passes a choice list (`VERDICT: PASS | FAIL`, `FAIL/PARTIAL`, the
prompt's schema echoed back) where the decisive verdict would be makes the
answer ambiguous → fallback; the search never steps back past it to an
earlier (possibly preliminary) verdict.
"""
from __future__ import annotations

import re
from typing import Callable, Iterator, Sequence, TypeVar

from bytedigger_engine.lib.llm_output_normalize import normalize_model_output

T = TypeVar("T")

# A decisive position held by a choice list: the answer is ambiguous and no
# further reading may replace it.
_AMBIGUOUS = object()

# Choice list: the token, then one or more `<sep> [PREFIX:] <option>` runs
# that END the line (`VERDICT: PASS | FAIL`, `VERDICT: PASS or VERDICT: FAIL`,
# `**STATUS: DONE** | **STATUS: BLOCKED**`). A separator followed by prose is
# a gloss, not a choice: `VERDICT: FAIL / PASS once line 12 is fixed` is FAIL.
_EMPH = r"(?:\*\*|__|`)?"
_HSPACE = r"[^\S\n]*"  # horizontal whitespace only — a choice list never spans lines
_CHOICE_ITEM = (
    rf"{_HSPACE}{_EMPH}{_HSPACE}(?:\||/|\bor\b){_HSPACE}{_EMPH}{_HSPACE}"
    r"(?:[A-Za-z][A-Za-z _]*:" + _HSPACE + r")?(?:{alt})(?![A-Za-z0-9_])" + _EMPH
)


def _normalize(raw):
    """CRLF/CR → LF so trailing-/leading-anchor regexes match uniformly (7C80A9CE).

    Defensive on falsy input (None/"" pass through unchanged) so callers may
    normalise before their own truthiness guard without a TypeError. This is
    the plain reading; the markdown-stripped one is ``_readings``' second.
    """
    if not raw:
        return raw
    return raw.replace("\r\n", "\n").replace("\r", "\n")


def _readings(plain: str) -> Iterator[str]:
    """The plain reading, then the model-output-normalized one when it differs."""
    yield plain
    stripped = normalize_model_output(plain)
    if stripped != plain:
        yield stripped


def _first_found(plain: str, parse: Callable[[str], object], fallback: T) -> T:
    """bd#84: plain reading first; the normalized reading only when the plain
    one matched nothing. A choice list at the decisive position (``parse``
    returns ``_AMBIGUOUS``) ends the search with ``fallback``."""
    if not plain:
        return fallback
    for text in _readings(plain):
        found = parse(text)
        if found is _AMBIGUOUS:
            return fallback
        if found is not None:
            return found  # type: ignore[return-value]
    return fallback


def _token_alt(tokens: Sequence[str]) -> str:
    """Regex alternation of ``tokens``, longest first so a token never
    shadows a longer one it prefixes (`DONE` vs `DONE_WITH_CONCERNS`).
    Builds a pattern; parses no model text — verdict-anchor: exempt."""
    return "|".join(re.escape(t) for t in sorted(tokens, key=len, reverse=True))


def _choice_rx(options: Sequence[str]) -> re.Pattern[str]:
    """Compile the choice-list matcher for ``options`` (builds a pattern;
    parses no model text — verdict-anchor: exempt)."""
    item = _CHOICE_ITEM.format(alt=_token_alt(options))
    return re.compile(rf"(?:{item})+{_HSPACE}[.)\]]?{_HSPACE}$", re.IGNORECASE | re.MULTILINE)


def _is_choice(text: str, token_end: int, choice_rx: re.Pattern[str]) -> bool:
    """True when the token ending at ``token_end`` opens a choice list."""
    return choice_rx.match(text, token_end) is not None


def last_standalone_line_verdict(
    raw: str,
    tokens: Sequence[str],
    *,
    fallback: str,
    prefix: str = "VERDICT:",
    allow_trailing: bool = False,
) -> str:
    """P1 — strict standalone-line verdict (92C57E96 model, generalised).

    Matches lines of the form:
        ^[ \\t]*(?:\\*\\*|__)?[ \\t]*{prefix}[ \\t]*(<token>)[ \\t]*(?:\\*\\*|__)?[ \\t]*$
    with IGNORECASE|MULTILINE.  Tokens sorted longest-first to prevent
    prefix-shadowing.  Last match wins.  Returns match.group(1).upper()
    or fallback when no match, or when the last match opens a choice list.
    Plain reading first, then the normalized one (bd#84, module docstring).

    allow_trailing=False (default): the token must be followed only by
        optional whitespace/bold-close then EOL.
    allow_trailing=True: after the captured token, accept an alphanumeric
        boundary (next char must be EOL or a non-[A-Za-z0-9] char — underscore
        is allowed so `__`-bold close passes), then an optional `**`/`__`
        emphasis close, then arbitrary trailing text to EOL.
        E.g. 'VERDICT: SPEC_CHANGE — prose', '__VERDICT: LEGITIMATE_REFACTOR__',
        and '**Verdict: spec_change**' all match; 'VERDICT: SPEC_CHANGED extra'
        does NOT (next char 'D' is alnum → boundary fails).
        The head anchor is unchanged; mid-prose occurrences are still rejected.
    """
    raw = _normalize(raw)
    if not raw or not tokens:
        return fallback
    alt = _token_alt(tokens)
    if allow_trailing:
        tail = r")(?=$|[^A-Za-z0-9])(?:\*\*|__)?[ \t]*.*$"
    else:
        tail = r")[ \t]*(?:\*\*|__)?[ \t]*$"
    pattern = (
        r"^[ \t]*(?:\*\*|__)?[ \t]*"
        + re.escape(prefix)
        + r"[ \t]*("
        + alt
        + tail
    )
    rx = re.compile(pattern, re.IGNORECASE | re.MULTILINE)
    choice_rx = _choice_rx(tokens)

    def parse(text: str) -> object:
        last = None
        for m in rx.finditer(text):
            last = m
        if last is None:
            return None
        return _AMBIGUOUS if _is_choice(text, last.end(1), choice_rx) else last.group(1).upper()

    return _first_found(raw, parse, fallback)


def verdict_under_heading(
    raw: str,
    tokens: Sequence[str],
    *,
    heading: str = "Verdict",
    aliases: dict[str, str] | None = None,
    fallback: str,
) -> str:
    """P3 — heading-anchored verdict.

    Plain reading (as before bd#84): the first token directly under a
    `## {heading}` line,
        ^##\\s*{heading}\\s*\\n+\\s*(<token>)\\b
    with MULTILINE|IGNORECASE; text before the heading is ignored.

    Normalized reading, only when the plain one finds nothing: every Verdict
    heading line that was markdown-dressed (`### Verdict`, `**Verdict:**`,
    `## Verdict: REVISE`) is a section, and each must hold one clean verdict
    — the token on the same line or the next non-blank one, ending its line
    or followed by punctuation or a dash gloss (`REVISE (two gaps)`,
    `SHIP, minor nits`, `REVISE — the rest can ship`). A section that opens
    with a sentence (`pass rate is 60%`, `Approved-by-author …`), a bullet,
    a choice list, or a gloss naming another verdict in capitals
    (`PASS — no wait, REVISE`) makes the answer ambiguous, as do sections
    that disagree → fallback. An undressed `Verdict: SHIP.` line is prose, as
    before.

    In both readings a choice list (`SHIP | REVISE`) where the verdict would
    be → fallback. Aliases apply after upper().
    """
    raw = _normalize(raw)
    if not raw or not tokens:
        return fallback
    alt = _token_alt(tokens)
    h = re.escape(heading)
    plain_rx = re.compile(rf"^##\s*{h}\s*\n+\s*({alt})\b", re.MULTILINE | re.IGNORECASE)
    # Normalized reading: a dressed Verdict heading line (`### Verdict`,
    # `**Verdict:**`, `## Verdict: REVISE`) — the dressing must wrap the
    # heading itself: a `#` marker, or emphasis / a code span right before it.
    dressed_rx = re.compile(rf"^[ ]{{0,3}}(?:#{{1,6}}[ \t]+|(?:\*\*|__|`)+[ \t]*){h}\b", re.IGNORECASE)
    anchor_rx = re.compile(rf"^[ \t]*{h}[ \t]*(?::[ \t]*(.*))?$", re.IGNORECASE)
    token_end = r"(?=[ \t]*$|[ \t]*(?:[—–:,;(]|--|-[ \t])|[.!](?:[ \t]|$))"
    token_rx = re.compile(rf"[ \t]*({alt}){token_end}(.*)$", re.IGNORECASE)
    other_token_rx = re.compile(rf"(?<![A-Za-z0-9_])(?:{alt})(?![A-Za-z0-9_])")  # case-sensitive: UPPERCASE only
    choice_rx = _choice_rx(tokens)

    def resolve(token: str) -> str:
        token = token.upper()
        return aliases.get(token, token) if aliases else token

    first = plain_rx.search(raw)
    if first:
        return fallback if _is_choice(raw, first.end(1), choice_rx) else resolve(first.group(1))

    def section_verdict(rest: str) -> str:
        """One dressed Verdict section → its token, or fallback when the
        section is not one clean verdict (a sentence, a bullet, a choice
        list, or a gloss naming another verdict: `PASS — no wait, REVISE`)."""
        m = token_rx.match(rest)
        if not m or _is_choice(rest, m.end(1), choice_rx) or other_token_rx.search(m.group(2)):
            return fallback
        return resolve(m.group(1))

    plain_lines = raw.split("\n")
    lines = normalize_model_output(raw).split("\n")  # line-preserving
    found: set[str] = set()
    for i, line in enumerate(lines):
        m = anchor_rx.match(line)
        if not m or not dressed_rx.match(plain_lines[i]):
            continue
        rest = m.group(1) or next((ln for ln in lines[i + 1:] if ln.strip()), "")
        found.add(section_verdict(rest))
    return found.pop() if len(found) == 1 else fallback


def last_line_anchored_marker(raw, markers, fallback):
    """P2 — last line-anchored marker wins (EEFD480F chokepoint).

    markers: list[tuple[str, str]] of (marker_text, return_value).

    For each (marker, value), finds ALL occurrences where the marker appears
    at the start of a line (optional leading whitespace).  Prose-embedded
    mid-line markers do NOT match.  The LAST match (highest start offset)
    across all markers wins; its value is returned.

    Tie-break (prefix-overlap): when two markers share the same start offset
    (e.g. 'STATUS: DONE_WITH_CONCERNS' ⊃ 'STATUS: DONE'), the FIRST marker in
    the markers list wins.  This is load-bearing for DONE_WITH_CONCERNS vs
    DONE disambiguation in phase_2/3/7.

    bd#84: plain reading first, then the normalized one. When the last
    occurrence opens a choice list (`STATUS: DONE | STATUS: BLOCKED`) the
    answer is ambiguous → fallback. The check uses the longest marker at that
    offset, so `STATUS: DONE_WITH_CONCERNS | …` is not read as `STATUS: DONE`.

    If no marker matches, returns fallback (may be None).
    """
    raw = _normalize(raw)
    # Choice options: every full marker plus its token part (`VERDICT: PASS` → `PASS`).
    options = {m for m, _ in markers} | {m.rsplit(":", 1)[1].strip() for m, _ in markers if ":" in m}
    choice_rx = _choice_rx([o for o in options if o])
    patterns = [
        (re.compile(rf"^\s*{re.escape(marker)}", re.IGNORECASE | re.MULTILINE), value)
        for marker, value in markers
    ]

    def parse(text: str) -> object:
        # offset -> (first-listed value, end of the longest marker at that offset)
        at: dict[int, tuple[object, int]] = {}
        for pattern, value in patterns:
            for m in pattern.finditer(text):
                first_value, longest_end = at.get(m.start(), (value, m.end()))
                at[m.start()] = (first_value, max(longest_end, m.end()))
        if not at:
            return None
        value, longest_end = at[max(at)]
        return _AMBIGUOUS if _is_choice(text, longest_end, choice_rx) else value

    return _first_found(raw, parse, fallback)


def find_last_standalone_marker(
    raw: str,
    tokens: Sequence[str],
    *,
    suffix: str = ":",
) -> re.Match | None:  # type: ignore[type-arg]
    """P4 — last standalone marker line.

    Matches lines of the form:
        ^(<token>){suffix}\\s*$
    with MULTILINE (NO ignorecase — tokens are uppercase-by-contract).
    Returns the last re.Match or None.  Caller reads .end() for body parse.
    """
    # NOT normalized: caller slices its OWN original raw via this Match's .end()
    # (semantic_verifier.py:70); normalizing here would desync offsets. `\s*$`
    # in the pattern below is already CRLF-safe. (7C80A9CE)
    if not raw or not tokens:
        return None

    alt = _token_alt(tokens)
    pattern = r"^(" + alt + r")" + re.escape(suffix) + r"\s*$"
    rx = re.compile(pattern, re.MULTILINE)

    last: re.Match | None = None  # type: ignore[type-arg]
    for m in rx.finditer(raw):
        last = m
    return last
