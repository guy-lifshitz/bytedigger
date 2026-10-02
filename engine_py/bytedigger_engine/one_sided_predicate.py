"""One-sided predicate scanner for RED test files (port of HAL Rule P, bd#166).

Rule
----
A negative predicate on a code-like exit-code subject (`expect(r.code)
.not.toBe(0)`) proves only that the observed value was not the one named —
it does NOT prove the test's actual claim about what the code SHOULD be. A
live positive control in the same test block (`expect(r.code).toBe(9)`)
supplies the missing half: without it, the assertion is indistinguishable
from a vacuous one that would pass on any wrong exit code.

Class: same as Rule M (two-sidedness of declarations) and Rule E
(`empty_input_assert.py`), applied to test-file predicates.

Port from HAL (bd#166)
----------------------
Ported from HAL's `one_sided_predicate.py` (GH1373, Rule P). The forms and the
algorithm are copied verbatim: `_FORM_NOT_TO_BE_INT`, `_FORM_NOT_TO_BE_NULL`,
`_POSCTL`, `_BLOCK_START`, `_PRED_ESCAPE`, the block/scope/escape/control walkers
and the `CANDIDATE_*` verdicts are frozen by the HAL spec.

Declared divergence D1 — the codey test. HAL's `_CODEY` regex anchors on
`[^a-z0-9_]`, so `exit_code` / `statusCode` are NOT codey (measured, and
documented in the former `empty_input_assert._tokens`). The port replaces the
regex at its two call sites (`_iter_candidates`, `_scope_has_control`) with the
token-based `is_codey`, which splits on separators AND camelCase humps. That
helper is moved here from `empty_input_assert.py` (which now imports it): ONE
codey definition, not two. It is not a strict superset of HAL's regex — it also
makes unrelated humps such as `zipCode` codey (a false positive the
`one-sided-ok:` escape covers, and a false negative when such a subject is the
only "control"). `_CODEY` is not kept.

Algorithm
---------
- block boundaries: lines matching `_BLOCK_START` open a block that runs to
  the next block start or EOF;
- a candidate BEFORE the first block start, or in a file with no blocks at
  all, is scoped to the WHOLE FILE (its positive control may be anywhere in
  the file);
- candidates: `_FORM_NOT_TO_BE_INT` / `_FORM_NOT_TO_BE_NULL` matches whose
  subject is codey;
- escape: `_PRED_ESCAPE` on the same line or up to 3 lines above (a 4-line
  window: the line itself plus three above) silences; the 4th line above
  does NOT;
- positive control: `_POSCTL` with a codey subject anywhere in the same
  scope (block, or whole file) silences;
- otherwise: finding.

One scanner (`scan_one_sided_predicates`); `summarize_predicates` reuses it —
no second copy of the predicate lives anywhere else.
"""
from __future__ import annotations

import re
from typing import Any, Mapping

# Suffix set Rule P scans (the TS/JS test corpus).
TS_TEST_SUFFIXES = (".test.ts", ".test.js", ".spec.ts", ".spec.js")

# ─── D1 — token-based codey test (moved from empty_input_assert) ─────────────

_CODEY_WORDS = frozenset("code rc exit exitcode returncode status".split())

_TOKEN_SPLIT = re.compile(r"[^A-Za-z0-9]+|(?<=[a-z0-9])(?=[A-Z])")


def identifier_tokens(subject: str) -> frozenset[str]:
    """Identifier words in `subject`, split on separators AND on camelCase humps.

    HAL states both classes as `\\b`-anchored regexes, which is correct for its
    TypeScript corpus and WRONG here, measured: `\\bcode\\b` does not fire inside
    `exit_code` because `_` is a word character, so `assert exit_code == 2` counted
    as a live denominator — the exact defect edition E of the HAL rule exists to
    fix. `\\brecords\\b` missed `corpus_records` the same way, under-detecting
    controls. One root, two faces: a boundary written for camelCase does not hold
    on snake_case. Splitting into tokens covers both spellings and is why this is a
    port rather than a copy.
    """
    return frozenset(t.lower() for t in _TOKEN_SPLIT.split(subject) if t)


def is_codey(subject: str) -> bool:
    return bool(identifier_tokens(subject) & _CODEY_WORDS)


# ─── frozen verbatim from HAL (do not edit without re-freezing the spec) ─────

_FORM_NOT_TO_BE_INT = re.compile(r"expect\(\s*(?P<subj>[^)]*?)\s*(?:,[^)]*)?\)\s*\.not\.toBe\(\s*-?\d+\s*\)")
_FORM_NOT_TO_BE_NULL = re.compile(r"expect\(\s*(?P<subj>[^)]*?)\s*(?:,[^)]*)?\)\s*\.not\.toBeNull\(\s*\)")
_POSCTL = re.compile(r"expect\(\s*(?P<subj>[^)]*?)\s*(?:,[^)]*)?\)\s*\.(?:toBe|toEqual)\(\s*-?\d+\s*\)")
_BLOCK_START = re.compile(r"^\s*(?:test|it)(?:\.\w+)?\s*\(")
_PRED_ESCAPE = "one-sided-ok:"

_CANDIDATE_FORMS: tuple[tuple["re.Pattern[str]", str], ...] = (
    (_FORM_NOT_TO_BE_INT, "not.toBe(<int>)"),
    (_FORM_NOT_TO_BE_NULL, "not.toBeNull()"),
)


def _blocks(lines: list[str]) -> list[tuple[int, int]]:
    """(start, end) 0-based line-index ranges for each test()/it() block. A
    block runs from its start line to the next block start or EOF."""
    starts = [i for i, ln in enumerate(lines) if _BLOCK_START.match(ln)]
    ranges: list[tuple[int, int]] = []
    for k, s in enumerate(starts):
        e = starts[k + 1] if k + 1 < len(starts) else len(lines)
        ranges.append((s, e))
    return ranges


def _scope_for_line(idx: int, blocks: list[tuple[int, int]], n_lines: int) -> tuple[int, int]:
    for b in blocks:
        if b[0] <= idx < b[1]:
            return b
    return (0, n_lines)  # outside any block, or no blocks at all -> whole file


def _has_escape(lines: list[str], idx: int) -> bool:
    for k in range(max(0, idx - 3), idx + 1):
        if _PRED_ESCAPE in lines[k]:
            return True
    return False


def _scope_has_control(lines: list[str], scope: tuple[int, int]) -> bool:
    s, e = scope
    for k in range(s, e):
        for m in _POSCTL.finditer(lines[k]):
            if is_codey(m.group("subj")):
                return True
    return False


# ONE canonical candidate walker. Both public functions consume it, so the
# codey/escape/control decision cannot drift between "what the lint reports" and
# "what the summary counts" — and a mutation of the rule has exactly one site.
CANDIDATE_ESCAPED = "escaped"
CANDIDATE_CONTROLLED = "controlled"
CANDIDATE_ONE_SIDED = "one_sided"


def _iter_candidates(text: str):
    """Yield (line_no_1based, subject, form_label, verdict) for every
    code-bearing candidate predicate. verdict is one of CANDIDATE_ESCAPED /
    CANDIDATE_CONTROLLED / CANDIDATE_ONE_SIDED."""
    lines = text.split("\n")
    blocks = _blocks(lines)
    control_cache: dict[tuple[int, int], bool] = {}

    for idx, line in enumerate(lines):
        for pattern, form_label in _CANDIDATE_FORMS:
            for m in pattern.finditer(line):
                subject = m.group("subj").strip()
                if not is_codey(subject):
                    continue
                if _has_escape(lines, idx):
                    yield idx + 1, subject, form_label, CANDIDATE_ESCAPED
                    continue
                scope = _scope_for_line(idx, blocks, len(lines))
                if scope not in control_cache:
                    control_cache[scope] = _scope_has_control(lines, scope)
                if control_cache[scope]:
                    yield idx + 1, subject, form_label, CANDIDATE_CONTROLLED
                else:
                    yield idx + 1, subject, form_label, CANDIDATE_ONE_SIDED


def scan_one_sided_predicates(text: str) -> list[dict]:
    """Return findings for negative code-exit predicates without a live
    positive control in the same scope, unescaped."""
    findings: list[dict] = []
    for lineno, subject, form_label, verdict in _iter_candidates(text):
        if verdict != CANDIDATE_ONE_SIDED:
            continue
        findings.append({
            "line": lineno,
            "subject": subject,
            "form": form_label,
            "reason": (
                f"negative predicate on code-like subject {subject!r} "
                f"({form_label}) with no live positive control in scope"
            ),
        })
    findings.sort(key=lambda f: f["line"])
    return findings


def summarize_predicates(text: str) -> dict:
    """Non-vacuity summary: distinguishes "clean" from "no candidate was ever
    inspected" and makes escapes enumerable. `escaped`, `controlled` and
    `one_sided` are mutually exclusive; their sum equals `candidates`."""
    counts = {"candidates": 0, "one_sided": 0, "controlled": 0, "escaped": 0}
    for _lineno, _subject, _form, verdict in _iter_candidates(text):
        counts["candidates"] += 1
        counts[verdict] += 1
    return counts


# The shape of the partition is held by these identities. Comparison is EXACT:
# `<=` would accept a LOST unit, and the partition has to be complete in BOTH
# directions (double-counting AND loss).
_PREDICATE_IDENTITIES = (
    "candidates == one_sided + controlled + escaped",
    "controlled <= candidates",
    "one_sided <= candidates",
)


def check_predicate_identities(summary: Mapping[str, Any]) -> list[str]:
    """Names of the Rule P identities VIOLATED by `summary` ([] when clean)."""
    candidates = int(summary.get("candidates", 0))
    one_sided = int(summary.get("one_sided", 0))
    controlled = int(summary.get("controlled", 0))
    escaped = int(summary.get("escaped", 0))

    violated: list[str] = []
    if candidates != one_sided + controlled + escaped:
        violated.append(_PREDICATE_IDENTITIES[0])
    if controlled > candidates:
        violated.append(_PREDICATE_IDENTITIES[1])
    if one_sided > candidates:
        violated.append(_PREDICATE_IDENTITIES[2])
    return violated
