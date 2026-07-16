"""verdict_anchor.py — verdict-anchor lint library (6B4AFA50).

Detects module-level parse functions in Python source that use raw-text
patterns without first normalizing CRLF (the 7C80A9CE recurrence class).

Public API:
  Violation  — dataclass(file, line_no, fn, rule, message)
  lint_text(source: str, filename: str) -> list[Violation]
  lint_file(path) -> list[Violation]
  main(argv=None) -> int

Exit codes (main):
  0  — clean (no violations)
  1  — at least one violation found
  2  — usage error (nonexistent file / AST parse error)
"""
from __future__ import annotations

import ast
import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Violation:
    file: str
    line_no: int
    fn: str
    rule: str
    message: str


def _fn_source_segment(source: str, node: ast.FunctionDef) -> str:
    """Return the raw source lines for a FunctionDef node (1-indexed, inclusive)."""
    lines = source.split("\n")
    start = node.lineno - 1          # 0-indexed
    end = node.end_lineno            # 0-indexed exclusive (end_lineno is 1-indexed inclusive)
    return "\n".join(lines[start:end])


def _has_re_call(node: ast.FunctionDef) -> bool:
    """True if the fn subtree contains any re.<attr>(...) call."""
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            func = n.func
            if (
                isinstance(func, ast.Attribute)
                and isinstance(func.value, ast.Name)
                and func.value.id == "re"
            ):
                return True
    return False


def _has_normalize_call(node: ast.FunctionDef) -> bool:
    """True if the fn subtree contains a call to _normalize(...)."""
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            func = n.func
            if isinstance(func, ast.Name) and func.id == "_normalize":
                return True
    return False


def _has_tab_anchor(node: ast.FunctionDef) -> bool:
    """True if any string constant in the fn subtree contains the literal '[ \\t]'."""
    needle = r"[ \t]"
    for n in ast.walk(node):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            if needle in n.value:
                return True
    return False


def _is_exempt(source: str, node: ast.FunctionDef) -> bool:
    """True if the fn source segment contains 'not normalized' or 'verdict-anchor: exempt' (case-insensitive)."""
    segment = _fn_source_segment(source, node).lower()
    return "not normalized" in segment or "verdict-anchor: exempt" in segment


def lint_text(source: str, filename: str) -> list[Violation]:
    """Parse source and return all Violations for module-level FunctionDef nodes."""
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as exc:
        raise exc

    violations: list[Violation] = []

    for stmt in tree.body:
        if not isinstance(stmt, ast.FunctionDef):
            continue

        node = stmt
        is_parse_fn = _has_re_call(node)
        has_normalize = _has_normalize_call(node)
        has_tab = _has_tab_anchor(node)
        exempt = _is_exempt(source, node)

        # R1: tab_anchor_no_normalize — fires regardless of exempt
        if has_tab and not has_normalize:
            violations.append(Violation(
                file=filename,
                line_no=node.lineno,
                fn=node.name,
                rule="tab_anchor_no_normalize",
                message=(
                    f"fn '{node.name}' uses '[ \\t]' anchor without _normalize(); "
                    "use '\\s' or call _normalize() first"
                ),
            ))

        # R2: entry_no_normalize — only when not exempt
        if is_parse_fn and not has_normalize and not exempt:
            violations.append(Violation(
                file=filename,
                line_no=node.lineno,
                fn=node.name,
                rule="entry_no_normalize",
                message=(
                    f"fn '{node.name}' calls re.* on raw input without _normalize(); "
                    "add _normalize() or annotate 'NOT normalized' / 'verdict-anchor: exempt'"
                ),
            ))

    return violations


def lint_file(path) -> list[Violation]:
    """Read file at path and return violations (raises OSError on read failure)."""
    p = Path(path)
    source = p.read_text(encoding="utf-8")
    return lint_text(source, str(path))


def main(argv=None) -> int:
    """CLI entry point. Returns 0 (clean), 1 (violations), or 2 (usage error)."""
    parser = argparse.ArgumentParser(
        description="Lint Python files for verdict/marker parse fns missing _normalize().",
    )
    parser.add_argument(
        "files",
        nargs="*",
        metavar="FILE",
        help="Python source file(s) to lint. Defaults to engine_py/lib/verdict_parse.py.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit JSON output instead of human-readable text.",
    )

    args = parser.parse_args(argv)

    # Default to the canonical target when no files given
    if not args.files:
        _engine_py_root = Path(__file__).resolve().parent
        default_target = _engine_py_root / "lib" / "verdict_parse.py"
        paths = [str(default_target)]
    else:
        paths = args.files

    all_violations: list[Violation] = []

    for path_str in paths:
        p = Path(path_str)
        if not p.exists() or not p.is_file():
            print(f"error: cannot read file: {path_str}", file=sys.stderr)
            return 2
        try:
            violations = lint_file(p)
        except SyntaxError as exc:
            print(f"error: syntax error in {path_str}: {exc}", file=sys.stderr)
            return 2
        all_violations.extend(violations)

    if args.json_output:
        print(json.dumps({
            "violations": [
                {
                    "file": v.file,
                    "line_no": v.line_no,
                    "fn": v.fn,
                    "rule": v.rule,
                    "message": v.message,
                }
                for v in all_violations
            ],
            "count": len(all_violations),
        }))
    else:
        for v in all_violations:
            print(
                f"VERDICT-ANCHOR {v.file}:{v.line_no} fn={v.fn} {v.rule}: {v.message}"
            )

    return 1 if all_violations else 0


if __name__ == "__main__":
    sys.exit(main())
