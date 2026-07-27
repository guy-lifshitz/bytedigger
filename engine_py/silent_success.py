"""silent_success.py — the silent-success lint library (23457A8C · GH #1186).

Detects checks that structurally cannot answer "no":

  rule A — E_NO_MAIN_GUARD          (file mode, `lint_python_file`)
  rule B — E_GATE_PIPED_TO_HEAD_TAIL (command mode, `lint_command`)
  rule C — E_SELF_MATCHING_LIVENESS  (command mode, `lint_command`)

Public surface (frozen, spec §2.1):

    Finding = dict  # {"rule", "where", "line", "message"}
    lint_python_file(path) -> list[Finding]
    lint_command(command) -> list[Finding]

`where` is the path EXACTLY as handed to `lint_python_file` (never resolved or
absolutized); for `lint_command` it is the literal string "<command>".

Rules A/B/C are independent and findings accumulate — there is no
first-match-wins.

This module is deliberately a guard-less library and MUST stay out of rule-A
scope: scope detection is AST-based (an Attribute/Name/ImportFrom binding the
parser class), never a substring scan of the source text, so merely naming the
argparse parser class in prose does not put a module in scope.
"""
from __future__ import annotations

import ast
import re
import shlex
from pathlib import Path
from typing import Any, Dict, List, Optional

Finding = Dict[str, Any]

__all__ = ["lint_python_file", "lint_command", "Finding"]

# ─────────────────────────── rule A — no __main__ guard ───────────────────────

# The parser class name is compared against AST node fields only. It appears
# here as a plain string CONSTANT, which is exactly what the frozen scope
# predicate must NOT treat as evidence (AC37/AC48).
_PARSER_ATTR = "ArgumentParser"

_SETUP_DENYLIST = frozenset(
    {
        "sys.path.insert",
        "sys.path.append",
        "warnings.filterwarnings",
        "logging.basicConfig",
    }
)

_RULE_A_MESSAGE = (
    "no reachable `if __name__ == \"__main__\"` guard and no top-level work: "
    "running this module as a script is a silent no-op (E_NO_MAIN_GUARD)"
)


def _path_excluded(path_str: str) -> bool:
    """True when the path is a test path (spec §2.1 name/segment exclusions)."""
    p = Path(path_str)
    parts = set(p.parts)
    if "tests" in parts or "__tests__" in parts:
        return True
    name = p.name
    if name.startswith("test_"):
        return True
    if name.endswith("_test.py"):
        return True
    return False


def _binds_parser(tree: ast.Module) -> bool:
    """AST-only detection of the argparse parser class (spec §2.1).

    A bare string literal naming the class does NOT count — only a real
    Attribute access, a Name reference, or an ImportFrom binding it.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr == _PARSER_ATTR:
            return True
        if isinstance(node, ast.Name) and node.id == _PARSER_ATTR:
            return True
        if isinstance(node, ast.ImportFrom) and node.module == "argparse":
            for alias in node.names:
                if alias.name == _PARSER_ATTR:
                    return True
    return False


def _in_rule_a_scope(text: str, tree: ast.Module) -> bool:
    first_line = text.split("\n", 1)[0]
    if first_line.startswith("#!") and "python" in first_line:
        return True
    return _binds_parser(tree)


def _is_dunder_name(node: ast.AST) -> bool:
    return isinstance(node, ast.Name) and node.id == "__name__"


def _is_main_str(node: ast.AST) -> bool:
    return isinstance(node, ast.Constant) and node.value == "__main__"


def _test_is_main_guard(test: ast.AST) -> bool:
    """True for `__name__ == "__main__"` in any operand order, or `in (...)`."""
    if isinstance(test, ast.BoolOp):
        return any(_test_is_main_guard(v) for v in test.values)
    if not isinstance(test, ast.Compare):
        return False
    left: ast.AST = test.left
    for op, comparator in zip(test.ops, test.comparators):
        if isinstance(op, ast.Eq):
            if _is_dunder_name(left) and _is_main_str(comparator):
                return True
            if _is_main_str(left) and _is_dunder_name(comparator):
                return True
        elif isinstance(op, ast.In):
            if _is_dunder_name(left) and isinstance(
                comparator, (ast.Tuple, ast.List, ast.Set)
            ):
                if any(_is_main_str(e) for e in comparator.elts):
                    return True
        left = comparator
    return False


def _has_reachable_guard(body: List[ast.stmt]) -> bool:
    """Guard reachable at import-as-__main__ (spec §2.1, frozen reading).

    Counts an `If` that is an element of the given body, or nested (recursively)
    inside a `Try`/`If`/`With` element of it. A guard inside a FunctionDef or a
    ClassDef never counts — that is failure mode I3.
    """
    for stmt in body:
        if isinstance(stmt, ast.If):
            if _test_is_main_guard(stmt.test):
                return True
            if _has_reachable_guard(stmt.body) or _has_reachable_guard(stmt.orelse):
                return True
        elif isinstance(stmt, ast.Try):
            if (
                _has_reachable_guard(stmt.body)
                or _has_reachable_guard(stmt.orelse)
                or _has_reachable_guard(stmt.finalbody)
            ):
                return True
            for handler in stmt.handlers:
                if _has_reachable_guard(handler.body):
                    return True
        elif isinstance(stmt, (ast.With, ast.AsyncWith)):
            if _has_reachable_guard(stmt.body):
                return True
    return False


def _dotted_callee(func: ast.AST) -> Optional[str]:
    """Full dotted name of a call target, or None when it is not a plain chain."""
    parts: List[str] = []
    current: ast.AST = func
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    parts.append(current.id)
    return ".".join(reversed(parts))


def _has_real_toplevel_work(tree: ast.Module) -> bool:
    """Carve-out (spec §2.1 revision 3).

    Exempt iff Module.body holds an `Expr` statement whose value is a `Call`
    whose dotted callee is NOT in the setup denylist. A Call nested in an
    Assign never exempts.
    """
    for stmt in tree.body:
        if not isinstance(stmt, ast.Expr):
            continue
        value = stmt.value
        if not isinstance(value, ast.Call):
            continue
        name = _dotted_callee(value.func)
        if name is None or name not in _SETUP_DENYLIST:
            return True
    return False


def lint_python_file(path: Any) -> List[Finding]:
    """Rule A. Raises OSError (unreadable) / SyntaxError (unparseable)."""
    where = str(path)
    if _path_excluded(where):
        return []

    text = Path(where).read_text(encoding="utf-8")
    tree = ast.parse(text)

    if not _in_rule_a_scope(text, tree):
        return []
    if _has_reachable_guard(tree.body):
        return []
    if _has_real_toplevel_work(tree):
        return []

    return [
        {
            "rule": "A",
            "where": where,
            "line": 1,
            "message": _RULE_A_MESSAGE,
        }
    ]


# ───────────────────── shared command-string primitives ──────────────────────


def _pipe_split(command: str) -> List[str]:
    """Split on a literal single `|`; `||` is a logical operator, not a pipe."""
    segments: List[str] = []
    buf: List[str] = []
    i = 0
    length = len(command)
    while i < length:
        ch = command[i]
        if ch == "|":
            if i + 1 < length and command[i + 1] == "|":
                buf.append("||")
                i += 2
                continue
            segments.append("".join(buf))
            buf = []
            i += 1
            continue
        buf.append(ch)
        i += 1
    segments.append("".join(buf))
    return segments


_ASSIGN_PREFIX_RE = re.compile(r"^[A-Za-z_][A-Za-z_0-9]*=")
_OPENERS = ('"', "'", "$(", "`", "(", "{")


def _leading_word(segment: str) -> str:
    """Leading command word of a pipe segment.

    Assignment prefixes (`VAR=`) and an opening `$(`/backtick/quote are stripped
    FIRST, so `gate_line_num="$(grep -nF …` reads as `grep`, not as a gate call.
    """
    stripped = segment.strip()
    if not stripped:
        return ""
    token = stripped.split()[0]
    while True:
        match = _ASSIGN_PREFIX_RE.match(token)
        if not match:
            break
        token = token[match.end() :]
    changed = True
    while changed:
        changed = False
        for opener in _OPENERS:
            if token.startswith(opener):
                token = token[len(opener) :]
                changed = True
    return token


def _tokenize(text: str) -> List[str]:
    try:
        lexer = shlex.shlex(text, posix=True)
        lexer.whitespace_split = True
        lexer.commenters = ""
        return list(lexer)
    except ValueError:
        return text.split()


# ──────────────────── rule B — gate piped into head/tail ─────────────────────

_GATE_BASENAME_RE = re.compile(r"(gate|lint|check|verify|drift|canary|audit)")
_HEAD_TAIL_RE = re.compile(r"^(tail|head)(\s|$)")
_PIPEFAIL_RE = re.compile(r"set\s+-[A-Za-z]*o\s+pipefail")
_SELECTION_LEADERS = frozenset({"grep", "printf", "echo"})

_RULE_B_MESSAGE = (
    "a gate/lint/check invocation is piped into head/tail: its exit code is "
    "discarded by the pipe and its output window can hide the failure "
    "(E_GATE_PIPED_TO_HEAD_TAIL)"
)


def _is_gate_token(token: str) -> bool:
    candidate = token.strip().strip("'\"")
    if candidate == "pytest":
        return True
    if not candidate.endswith((".py", ".sh", ".ts")):
        return False
    basename = candidate.rsplit("/", 1)[-1]
    return bool(_GATE_BASENAME_RE.search(basename))


def _segment_has_gate_token(segment: str) -> bool:
    return any(_is_gate_token(tok) for tok in segment.split())


def _rule_b(command: str) -> List[Finding]:
    if _PIPEFAIL_RE.search(command):
        return []
    segments = _pipe_split(command)
    findings: List[Finding] = []
    for index in range(len(segments) - 1):
        following = segments[index + 1].lstrip()
        if not _HEAD_TAIL_RE.match(following):
            continue
        previous = segments[index]
        if _leading_word(previous) in _SELECTION_LEADERS:
            continue
        if _leading_word(previous).startswith("$"):
            continue
        if not _segment_has_gate_token(previous):
            continue
        findings.append(
            {
                "rule": "B",
                "where": "<command>",
                "line": 1,
                "message": _RULE_B_MESSAGE,
            }
        )
    return findings


# ─────────────────── rule C — self-matching liveness probe ───────────────────

_BRACKET_TRICK_RE = re.compile(r"\[[^\]]*\]")
_GREP_V_RE = re.compile(r"grep\s+-[A-Za-z]*v")
_TERMINATORS = frozenset({"&&", "||", ";", "|", "&"})

_RULE_C_MESSAGE = (
    "a full-cmdline process probe matches a literal pattern that the probing "
    "command line itself contains: the predicate is satisfied by the prober "
    "(E_SELF_MATCHING_LIVENESS)"
)


def _has_self_exclusion(command: str) -> bool:
    if _GREP_V_RE.search(command):
        return True
    if "$$" in command:
        return True
    if "$PPID" in command:
        return True
    if "--ignore-ancestors" in command:
        return True
    return False


def _is_literal_pattern(pattern: str) -> bool:
    return "$" not in pattern


def _pattern_after(tokens: List[str], start: int) -> Optional[str]:
    """First non-flag, non-redirect operand at/after `start`, or None."""
    for token in tokens[start:]:
        if token in _TERMINATORS:
            return None
        if token.startswith((">", "<")) or token[1:2] == ">":
            continue
        if token.startswith("-"):
            continue
        return token
    return None


def _rule_c_pgrep(command: str) -> List[Finding]:
    tokens = _tokenize(command)
    findings: List[Finding] = []
    for index, token in enumerate(tokens):
        if token != "pgrep":
            continue
        has_f = False
        has_x = False
        for flag in tokens[index + 1 :]:
            if flag in _TERMINATORS:
                break
            if flag.startswith("--"):
                continue
            if flag.startswith("-"):
                letters = flag[1:]
                if "f" in letters:
                    has_f = True
                if "x" in letters:
                    has_x = True
                continue
            break
        if not has_f or has_x:
            continue
        pattern = _pattern_after(tokens, index + 1)
        if pattern is None:
            continue
        if not _is_literal_pattern(pattern):
            continue
        if _BRACKET_TRICK_RE.search(pattern):
            continue
        findings.append(
            {
                "rule": "C",
                "where": "<command>",
                "line": 1,
                "message": _RULE_C_MESSAGE,
            }
        )
    return findings


def _rule_c_ps_grep(command: str) -> List[Finding]:
    segments = _pipe_split(command)
    findings: List[Finding] = []
    for index in range(len(segments) - 1):
        if _leading_word(segments[index]) != "ps":
            continue
        following = segments[index + 1]
        if _leading_word(following) != "grep":
            continue
        tokens = _tokenize(following)
        if not tokens:
            continue
        pattern = _pattern_after(tokens, 1)
        if pattern is None:
            continue
        if not _is_literal_pattern(pattern):
            continue
        if _BRACKET_TRICK_RE.search(pattern):
            continue
        findings.append(
            {
                "rule": "C",
                "where": "<command>",
                "line": 1,
                "message": _RULE_C_MESSAGE,
            }
        )
    return findings


def _rule_c(command: str) -> List[Finding]:
    if _has_self_exclusion(command):
        return []
    return _rule_c_pgrep(command) + _rule_c_ps_grep(command)


def lint_command(command: str) -> List[Finding]:
    """Rules B and C. Independent — findings accumulate."""
    if not command:
        return []
    return _rule_b(command) + _rule_c(command)
