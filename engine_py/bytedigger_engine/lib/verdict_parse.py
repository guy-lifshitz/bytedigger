"""Single-source verdict/marker parser (chokepoint EEFD480F).

Wave A — P1, P3, P4 primitives. Wave B adds P2. Wave C folds phase_6_smoke.
Algorithm changes = 1 file; callers pass domain token tuples.
"""
from __future__ import annotations

import re
from typing import Sequence

from bytedigger_engine.lib.llm_output_normalize import normalize_model_output

# A token directly followed by one of these is one option of a choice list
# (`VERDICT: PASS | FAIL`, `VERDICT: PASS or VERDICT: FAIL`, `SHIP / REVISE`)
# — typically the model echoing the prompt's schema — never a verdict (bd#84).
_CHOICE_SEP_RE = re.compile(r"[ \t]*(?:\||/|\bor\b)", re.IGNORECASE)


def _normalize(raw):
    """Model-output normalization before any anchored parse (bd#84 chokepoint).

    Delegates to llm_output_normalize.normalize_model_output: CRLF/CR → LF
    (7C80A9CE), plus markdown dressing (emphasis, whole-line code spans,
    short headings) stripped outside fenced code. Defensive on falsy input
    (None/"" pass through unchanged) so callers may normalise before their
    own truthiness guard without a TypeError. Idempotent.
    """
    return normalize_model_output(raw)


def _is_choice(raw: str, token_end: int) -> bool:
    """True when the token ending at ``token_end`` is followed by a choice separator."""
    return _CHOICE_SEP_RE.match(raw, token_end) is not None


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
    or fallback when no match.

    allow_trailing=False (default): byte-identical to prior behavior — the
        token must be followed only by optional whitespace/bold-close then EOL.
    allow_trailing=True: after the captured token, accept an alphanumeric
        boundary (next char must be EOL or a non-[A-Za-z0-9] char — underscore
        is allowed so `__`-bold close passes), then an optional `**`/`__`
        emphasis close, then arbitrary trailing text to EOL.
        E.g. 'VERDICT: SPEC_CHANGE — prose', '__VERDICT: LEGITIMATE_REFACTOR__',
        and '**Verdict: spec_change**' all match; 'VERDICT: SPEC_CHANGED extra'
        does NOT (next char 'D' is alnum → boundary fails).
        The head anchor is unchanged; mid-prose occurrences are still rejected.

    Input is normalized first (bd#84), so `**VERDICT: FAIL**`, a line that is
    one code span, and a short `## VERDICT: FAIL` heading all match. A token
    followed by a choice separator (`VERDICT: PASS | FAIL`) is skipped.
    """
    raw = _normalize(raw)
    if not raw or not tokens:
        return fallback

    sorted_tokens = sorted(tokens, key=len, reverse=True)
    alt = "|".join(re.escape(t) for t in sorted_tokens)
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

    last = None
    for m in rx.finditer(raw):
        if not _is_choice(raw, m.end(1)):
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

    The token may carry trailing prose (`REVISE — the rest can ship as-is`),
    but a token followed by a choice separator (`SHIP | REVISE`, the prompt's
    schema echoed back) does not count. Text before the heading is ignored.
    Aliases apply after upper(). When several Verdict sections resolve to
    different tokens the answer is ambiguous → fallback (fail-closed).
    """
    raw = _normalize(raw)
    if not raw or not tokens:
        return fallback

    sorted_tokens = sorted(tokens, key=len, reverse=True)
    alt = "|".join(re.escape(t) for t in sorted_tokens)
    h = re.escape(heading)
    pattern = (
        rf"^[ \t]*{h}[ \t]*(?::[ \t]*\n|\n)\s*({alt})\b"
        rf"|^[ \t]*{h}[ \t]*:[ \t]*({alt})\b"
    )
    rx = re.compile(pattern, re.MULTILINE | re.IGNORECASE)

    found: set[str] = set()
    for m in rx.finditer(raw):
        group = 1 if m.group(1) is not None else 2
        if _is_choice(raw, m.end(group)):
            continue
        token = m.group(group).upper()
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

    Input is normalized first (bd#84): `**VERDICT: FAIL**`, `` `VERDICT: FAIL` ``
    and a short `## STATUS: DONE` heading all anchor. A marker followed by a
    choice separator (`STATUS: DONE | STATUS: BLOCKED`) is skipped.

    If no marker matches, returns fallback (may be None).
    """
    raw = _normalize(raw)
    best_pos = -1
    best_value = fallback
    for marker, value in markers:
        pattern = re.compile(rf"^\s*{re.escape(marker)}", re.IGNORECASE | re.MULTILINE)
        ms = [m for m in pattern.finditer(raw or "") if not _is_choice(raw, m.end())]
        if ms:
            pos = ms[-1].start()
            if pos > best_pos:
                best_pos = pos
                best_value = value
    return best_value


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

    sorted_tokens = sorted(tokens, key=len, reverse=True)
    alt = "|".join(re.escape(t) for t in sorted_tokens)
    pattern = r"^(" + alt + r")" + re.escape(suffix) + r"\s*$"
    rx = re.compile(pattern, re.MULTILINE)

    last: re.Match | None = None  # type: ignore[type-arg]
    for m in rx.finditer(raw):
        last = m
    return last
