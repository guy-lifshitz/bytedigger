"""Empty-input-assert scanner for bytedigger pytest files (port of HAL Rule E, hal#1541).

Rule
----
An assertion claiming a VERDICT population is empty (`assert findings == []`,
`assert len(offenders) == 0`, `assert not violations`) passes in two different
worlds: the scanner ran and honestly found nothing, and the scanner never ran at
all (empty corpus, broken glob, filtered-out scope). Both worlds print the same
green assertion. A live denominator in the same scope — a positive-magnitude
assert whose subject is NOT itself an exit code / status label — is required to
tell them apart.

Port, not copy
--------------
HAL's `empty_input_assert.py` scans TypeScript and is regex-based by necessity:
its corpus has no parser at hand, so it walks `expect(` openings and matches
balanced parens by counting. bytedigger's corpus is pytest — 467 test modules
against 8 TS files — so the same rule is expressed against `ast`, and the forms
are Python's, not `expect().toEqual([])`. Copying the TS scanner here would have
produced an instrument that finds nothing on 467 files, which is the failure the
rule itself is about: a check that cannot tell "ran and found nothing" from
"never ran".

What the parser buys over the original: the subject is the exact source segment
of the compared expression, not a paren-counted slice; a candidate inside a
string literal or a comment cannot be matched by accident; and `assert a == []`
spread over three lines is one node rather than a line-window heuristic.

`_CODEY` is DUPLICATED here, and that is a declared defect, not a choice: HAL
imports it from Rule P (`one_sided_predicate.py`, §1g — one denominator-exclusion
class, not two), and Rule P is not ported to bytedigger. See bd#66. When Rule P
lands, this constant must be deleted and imported.

NO ENFORCEMENT LAYER EXISTS FOR THIS MODULE IN BYTEDIGGER. The registry
`precommit_lints.py` names three lint drivers, zero of which exist on disk, and
nothing in the engine calls `build_lint_commands`. This scanner is callable from
tests and from its own CLI; it is not wired into any pre-commit hook, and adding
its name to that registry would create the appearance of a gate without the
gate. bd#66 tracks the missing layer.

Frozen forms (2026-08-04)
--------------------------
Candidate (a claim of verdict-population emptiness), subject must contain a word
from `_VERDICT_POP`:
    assert <subj> == []            (also {}, (), set(), 0)
    assert len(<subj>) == 0
    assert not <subj>

Control (a denominator — a magnitude that COULD have been zero), subject must
NOT match `_CODEY`:
    assert len(<subj>) > 0 / >= 1 / == <pos int>
    assert <subj> > 0 / >= 1
    assert <subj>                  (bare truthiness)

Escape: `empty-ok: #<issue> <reason, >=12 chars>` on the candidate's own line or
up to 3 lines above (4-line window, same as HAL).  Escaping outranks control: a
candidate with both is `escaped`, never counted twice.

Scope: a `def test_*` (or `async def test_*`) opens a block that runs to the end
of that function. A candidate outside any test function is scoped to the whole
module — the same "no block start" convention HAL uses.
"""

from __future__ import annotations

import ast
import re
import sys
from typing import Iterator, TypedDict

_VERDICT_WORDS = frozenset(
    "findings violations offenders divergences unwaived errors matches candidates records hits".split()
)
_CODEY_WORDS = frozenset("code rc exit exitcode returncode status".split())

_TOKEN_SPLIT = re.compile(r"[^A-Za-z0-9]+|(?<=[a-z0-9])(?=[A-Z])")


def _tokens(subject: str) -> frozenset[str]:
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


def _names_population(subject: str) -> bool:
    return bool(_tokens(subject) & _VERDICT_WORDS)


def _is_codey(subject: str) -> bool:
    # Duplicated from HAL's one_sided_predicate.py — see "Port, not copy" and bd#66.
    return bool(_tokens(subject) & _CODEY_WORDS)

_ESCAPE_DETAIL = re.compile(r"empty-ok:\s*(#\d+)\s+(.*)")
_ESCAPE_WINDOW = 4  # the candidate's own line plus the three above it
_MIN_REASON = 12

_EMPTY_LITERALS = ("[]", "{}", "()", "set()", "0")


class Finding(TypedDict):
    line: int
    subject: str
    form: str


class Summary(TypedDict):
    candidates: int
    controlled: int
    escaped: int
    findings: list[Finding]
    identity_ok: bool


def _seg(src: str, node: ast.AST) -> str:
    return (ast.get_source_segment(src, node) or "").strip()


def _is_empty_literal(src: str, node: ast.AST) -> bool:
    """`[]`, `{}`, `()`, `set()` or the integer 0 — the right-hand side of a claim
    that a population is empty. `0` counts because `len(x) == 0` and `n_errors == 0`
    make the same claim; `_VERDICT_POP` on the subject keeps unrelated zeroes out."""
    if isinstance(node, ast.Constant) and node.value == 0 and not isinstance(node.value, bool):
        return True
    return _seg(src, node).replace(" ", "") in _EMPTY_LITERALS


def _len_arg(node: ast.AST) -> ast.AST | None:
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "len" and node.args:
        return node.args[0]
    return None


def _positive_int(node: ast.AST) -> int | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, int) and not isinstance(node.value, bool):
        return node.value
    return None


def _classify(src: str, test: ast.expr) -> tuple[str, str, str] | None:
    """(kind, subject, form) for one assert's test expression, or None.

    kind is "candidate" or "control". The subject is reported as source text so a
    finding names what the reader sees, not an AST repr."""
    # assert not <subj>
    if isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
        subj = _seg(src, test.operand)
        return ("candidate", subj, "assert not <subj>") if _names_population(subj) else None

    if isinstance(test, ast.Compare) and len(test.ops) == 1:
        left, op, right = test.left, test.ops[0], test.comparators[0]
        inner = _len_arg(left)
        subj = _seg(src, inner if inner is not None else left)
        form_subj = "len(<subj>)" if inner is not None else "<subj>"

        if isinstance(op, ast.Eq):
            if _is_empty_literal(src, right) and _names_population(subj):
                return ("candidate", subj, f"assert {form_subj} == {_seg(src, right) or '0'}")
            n = _positive_int(right)
            if n is not None and n > 0 and not _is_codey(subj):
                return ("control", subj, f"assert {form_subj} == {n}")
            return None

        if isinstance(op, (ast.Gt, ast.GtE)):
            n = _positive_int(right)
            sym = ">" if isinstance(op, ast.Gt) else ">="
            # `> 0` and `>= 1` are denominators; `>= 0` is vacuous and is NOT one.
            live = n is not None and (n >= 0 if isinstance(op, ast.Gt) else n >= 1)
            if live and not _is_codey(subj):
                return ("control", subj, f"assert {form_subj} {sym} {n}")
        return None

    # assert <subj>  — bare truthiness is a denominator when it names a population
    if isinstance(test, (ast.Name, ast.Attribute, ast.Call, ast.Subscript)):
        subj = _seg(src, test)
        if _names_population(subj) and not _is_codey(subj):
            return ("control", subj, "assert <subj>")
    return None


def _escaped(lines: list[str], lineno: int) -> bool:
    """True when a well-formed `empty-ok:` sits in the 4-line window ending at the
    candidate. A malformed escape (no issue number, or a reason under 12 chars) is
    NOT an escape — otherwise the escape hatch becomes the way to pass the rule."""
    for i in range(max(0, lineno - _ESCAPE_WINDOW), lineno):
        m = _ESCAPE_DETAIL.search(lines[i])
        if m and len(m.group(2).strip()) >= _MIN_REASON:
            return True
    return False


def _scopes(tree: ast.Module) -> list[tuple[int, int, list[ast.stmt]]]:
    """(start_line, end_line, body) per test function. A module with no test
    function yields one whole-module scope, matching HAL's convention."""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("test"):
            out.append((node.lineno, getattr(node, "end_lineno", node.lineno), list(ast.walk(node))))
    return out or [(1, 10**9, list(ast.walk(tree)))]


def _asserts(nodes: list[ast.stmt]) -> Iterator[ast.Assert]:
    for n in nodes:
        if isinstance(n, ast.Assert):
            yield n


def summarize_empty_input_asserts(text: str) -> Summary:
    """Single walker for both the findings and the counters (§1g): a second pass
    would be a second predicate, and the two would drift."""
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return {"candidates": 0, "controlled": 0, "escaped": 0, "findings": [], "identity_ok": True}

    lines = text.splitlines()
    candidates = controlled = escaped = 0
    findings: list[Finding] = []

    for _start, _end, body in _scopes(tree):
        scope_has_control = False
        scope_candidates: list[tuple[int, str, str]] = []
        for node in _asserts(body):
            got = _classify(text, node.test)
            if not got:
                continue
            kind, subj, form = got
            if kind == "control":
                scope_has_control = True
            else:
                scope_candidates.append((node.lineno, subj, form))
        for lineno, subj, form in scope_candidates:
            candidates += 1
            # Escaping outranks control, so a candidate carrying both is counted once.
            if _escaped(lines, lineno):
                escaped += 1
            elif scope_has_control:
                controlled += 1
            else:
                findings.append({"line": lineno, "subject": subj, "form": form})

    return {
        "candidates": candidates,
        "controlled": controlled,
        "escaped": escaped,
        "findings": findings,
        "identity_ok": candidates == controlled + escaped + len(findings),
    }


def scan_empty_input_asserts(text: str) -> list[Finding]:
    return summarize_empty_input_asserts(text)["findings"]


def identity_ok(candidates: int, controlled: int, escaped: int, num_findings: int) -> bool:
    """Every candidate lands in exactly one bucket. A scanner whose buckets do not
    add up is reporting about a population it did not fully walk."""
    return candidates == controlled + escaped + num_findings


def main(argv: list[str]) -> int:
    if not argv:
        print("usage: empty_input_assert.py <test_file.py> [...]", file=sys.stderr)
        return 2
    total = 0
    for path in argv:
        with open(path, encoding="utf-8") as fh:
            s = summarize_empty_input_asserts(fh.read())
        for f in s["findings"]:
            print(f"{path}:{f['line']}: {f['form']} — subject {f['subject']!r} has no live denominator in scope")
        total += len(s["findings"])
    return 1 if total else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
