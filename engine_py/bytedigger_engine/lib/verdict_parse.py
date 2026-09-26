"""Single-source verdict/marker parser (chokepoint EEFD480F).

Wave A — P1, P3, P4 primitives. Wave B adds P2. Wave C folds phase_6_smoke.
Algorithm changes = 1 file; callers pass domain token tuples.
"""
from __future__ import annotations

import re
from typing import Sequence

from bytedigger_engine.lib.llm_output_normalize import normalize_model_output

# A token followed by a separator and ANOTHER option is one entry of a choice
# list (`VERDICT: PASS | FAIL`, `VERDICT: PASS or VERDICT: FAIL`,
# `SHIP / REVISE`) — typically the model echoing the prompt's schema — never
# a verdict (bd#84). A separator followed by prose is a gloss and does not
# count: `VERDICT: ASSERTION_GAMING / assertEqual removed` is a verdict.
_CHOICE_SEP_CORE = r"(?:\||/|\bor\b)"
_CHOICE_SEP = rf"[ \t]*{_CHOICE_SEP_CORE}[ \t]*(?:[A-Za-z][A-Za-z _]*:[ \t]*)?"


def _normalize(raw):
    """Model-output normalization before any anchored parse (bd#84 chokepoint).

    Delegates to llm_output_normalize.normalize_model_output: CRLF/CR → LF
    (7C80A9CE), plus markdown dressing (emphasis, whole-line code spans,
    short headings) stripped outside fenced code. Defensive on falsy input
    (None/"" pass through unchanged) so callers may normalise before their
    own truthiness guard without a TypeError. Idempotent.
    """
    return normalize_model_output(raw)


def _token_alt(tokens: Sequence[str]) -> str:
    """Regex alternation of ``tokens``, longest first so a token never
    shadows a longer one it prefixes (`DONE` vs `DONE_WITH_CONCERNS`).
    Builds a pattern; parses no model text — verdict-anchor: exempt."""
    return "|".join(re.escape(t) for t in sorted(tokens, key=len, reverse=True))


def _choice_rx(options: Sequence[str]) -> re.Pattern[str]:
    """Compile the choice-list matcher for ``options`` (builds a pattern; parses
    no model text — verdict-anchor: exempt)."""
    return re.compile(_CHOICE_SEP + rf"(?:{_token_alt(options)})(?![A-Za-z0-9_])", re.IGNORECASE)


def _is_choice(raw: str, token_end: int, choice_rx: re.Pattern[str]) -> bool:
    """True when the token ending at ``token_end`` is one option of a choice list."""
    return choice_rx.match(raw, token_end) is not None


def last_standalone_line_verdict(
    raw: str,
    tokens: Sequence[str],
    *,
    fallback: str,
    prefix: str = "VERDICT:",
    allow_trailing: bool = False,
) -> str:
    """P1 — strict standalone-line verdict (92C57E96 model, generalised).

    Input is normalized first (bd#84): emphasis (`**VERDICT: FAIL**`,
    `__VERDICT: X__`), a short `## VERDICT: FAIL` heading and a final
    whole-line code span are stripped before matching. Then lines of the form
        ^[ \\t]*{prefix}[ \\t]*(<token>)[ \\t]*$
    match, with IGNORECASE|MULTILINE. Tokens sorted longest-first to prevent
    prefix-shadowing. Last match wins. Returns match.group(1).upper() or
    fallback when no match. A token that is one option of a choice list
    (`VERDICT: PASS | FAIL`) is skipped.

    allow_trailing=False (default): the token must be followed only by
        optional whitespace then EOL.
    allow_trailing=True: after the captured token, accept an alphanumeric
        boundary (next char must be EOL or a non-[A-Za-z0-9] char), then
        arbitrary trailing text to EOL. E.g. 'VERDICT: SPEC_CHANGE — prose'
        matches; 'VERDICT: SPEC_CHANGED extra' does NOT (next char 'D' is
        alnum → boundary fails). The head anchor is unchanged; mid-prose
        occurrences are still rejected.
    """
    raw = _normalize(raw)
    if not raw or not tokens:
        return fallback

    alt = _token_alt(tokens)
    if allow_trailing:
        tail = r")(?=$|[^A-Za-z0-9]).*$"
    else:
        tail = r")[ \t]*$"
    pattern = (
        r"^[ \t]*"
        + re.escape(prefix)
        + r"[ \t]*("
        + alt
        + tail
    )
    rx = re.compile(pattern, re.IGNORECASE | re.MULTILINE)

    choice_rx = _choice_rx(tokens)
    last = None
    for m in rx.finditer(raw):
        if not _is_choice(raw, m.end(1), choice_rx):
            last = m
    if last is None:
        return fallback
    return last.group(1).upper()


def verdict_under_heading(
    raw: str,
    tokens: Sequence[str],
    *,
    heading: str = "Verdict",
    aliases: dict[str, str] | None = None,
    fallback: str,
) -> str:
    """P3 — heading-anchored verdict.

    After normalization (bd#84) a `## Verdict` heading of any level reads as a
    bare `Verdict` line. Two shapes anchor a verdict, case-insensitively:

        Verdict[:]            (own line)  → first token on the next non-blank line
        Verdict: <token>      (one line)

    The token must end its line or be followed by a dash gloss
    (`REVISE — the rest can ship as-is`); a token that merely opens a
    sentence (`Verdict: pass rate is 60%`, `Approved-by-author …`) does not
    count, nor does one option of a choice list (`SHIP | REVISE`, the
    prompt's schema echoed back). Text before the heading is ignored.
    Aliases apply after upper(). When several Verdict sections resolve to
    different tokens the answer is ambiguous → fallback (fail-closed).
    """
    raw = _normalize(raw)
    if not raw or not tokens:
        return fallback

    alt = _token_alt(tokens)
    h = re.escape(heading)
    # The token ends its line, opens a dash gloss, or opens a choice list
    # (rejected below by _is_choice) — never a sentence (`pass rate is …`).
    token_end = rf"(?=[ \t]*(?:$|[—–-][ \t]|[.!][ \t]*$|{_CHOICE_SEP_CORE}))"
    rx = re.compile(rf"^[ \t]*{h}[ \t]*(?::|\n)\s*({alt}){token_end}", re.MULTILINE | re.IGNORECASE)
    choice_rx = _choice_rx(tokens)

    found: set[str] = set()
    for m in rx.finditer(raw):
        if _is_choice(raw, m.end(1), choice_rx):
            continue
        token = m.group(1).upper()
        found.add(aliases.get(token, token) if aliases else token)
    if len(found) != 1:
        return fallback
    return found.pop()


def last_line_anchored_marker(raw, markers, fallback):
    """P2 — last line-anchored marker wins (EEFD480F chokepoint).

    markers: list[tuple[str, str]] of (marker_text, return_value).

    For each (marker, value), finds ALL occurrences where the marker appears
    at the start of a line (optional leading whitespace).  Prose-embedded
    mid-line markers do NOT match.  The LAST match (highest start offset)
    across all markers wins; its value is returned.

    Tie-break (prefix-overlap): uses STRICT-GREATER (pos > best_pos), so when
    two markers share the same start offset (e.g. 'STATUS: DONE_WITH_CONCERNS'
    ⊃ 'STATUS: DONE'), the FIRST marker in the markers list wins.  This is
    load-bearing for DONE_WITH_CONCERNS vs DONE disambiguation in phase_2/3/7.

    Input is normalized first (bd#84): `**VERDICT: FAIL**`, a final
    `` `VERDICT: FAIL` `` line and a short `## STATUS: DONE` heading all
    anchor. An occurrence that is one option of a choice list
    (`STATUS: DONE | STATUS: BLOCKED`) is skipped; the decision is taken on
    the longest marker at that offset, so a skipped `STATUS: DONE_WITH_CONCERNS`
    option does not resurface as its prefix `STATUS: DONE`.

    If no marker matches, returns fallback (may be None).
    """
    raw = _normalize(raw)
    if not raw:
        return fallback
    # Choice options: every full marker plus its token part (`VERDICT: PASS` → `PASS`).
    options = {m for m, _ in markers} | {m.rsplit(":", 1)[1].strip() for m, _ in markers if ":" in m}
    choice_rx = _choice_rx([o for o in options if o])
    # offset -> (first-listed value, end of the longest marker at that offset)
    at: dict[int, tuple[object, int]] = {}
    for marker, value in markers:
        pattern = re.compile(rf"^\s*{re.escape(marker)}", re.IGNORECASE | re.MULTILINE)
        for m in pattern.finditer(raw):
            first_value, longest_end = at.get(m.start(), (value, m.end()))
            at[m.start()] = (first_value, max(longest_end, m.end()))
    for pos in sorted(at, reverse=True):
        value, longest_end = at[pos]
        if not _is_choice(raw, longest_end, choice_rx):
            return value
    return fallback


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
