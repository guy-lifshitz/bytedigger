"""resume_key_lint.py — resume-key lint library (00321AB4).

Detects inline resume-sentinel filename literals that bypass the single-source
chokepoint `resume_sentinel_name()` or that omit the run_id component
(the 7E274B85 / 4C03CCED stale-replay recurrence class).

Public API:
  Violation  — dataclass(file, line_no, context, rule, message)
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
import re
import sys
from dataclasses import dataclass
from pathlib import Path

_SENTINEL_RE = re.compile(r"[\w{}.]*_done_c[\w{}.]*\.json")
_RUN_ID_TEMPLATE_RE = re.compile(r"_r[{\d]")
_PRAGMA = "resume-key: allow"
_CHOKEPOINT_FN = "resume_sentinel_name"


@dataclass
class Violation:
    file: str
    line_no: int
    context: str
    rule: str
    message: str


def _build_template(node: ast.expr) -> str | None:
    """Build a template string from an ast.Constant(str) or ast.JoinedStr node.

    For Constant: returns the string value.
    For JoinedStr (f-string): concatenates literal Constant parts verbatim
    and renders each FormattedValue as "{}".
    Returns None if the node is not a string-bearing node.
    """
    if isinstance(node, ast.Constant):
        if isinstance(node.value, str):
            return node.value
        return None
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for part in node.values:
            if isinstance(part, ast.Constant) and isinstance(part.value, str):
                parts.append(part.value)
            elif isinstance(part, ast.FormattedValue):
                parts.append("{}")
        return "".join(parts)
    return None


def _has_run_id(src: str, template: str) -> bool:
    """True if the node has a run_id component."""
    src_lower = src.lower()
    return (
        "run_id" in src_lower
        or "rid" in src_lower
        or bool(_RUN_ID_TEMPLATE_RE.search(template))
    )


def _walk(
    node: ast.AST,
    source_lines: list[str],
    fn_name: str,
    fn_exempt: bool,
    violations: list[Violation],
    filename: str,
) -> None:
    """Recursively walk AST nodes, tracking enclosing function context.

    When entering a FunctionDef/AsyncFunctionDef, the fn_name and fn_exempt
    are updated for that function's scope. String-bearing nodes are checked
    for sentinel shape and rule violations.
    """
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        new_fn_name = node.name
        # Compute exempt flag for this function's source segment
        start = node.lineno - 1   # 0-indexed
        end = node.end_lineno     # end_lineno is 1-indexed inclusive → use as exclusive for 0-idx
        segment = "\n".join(source_lines[start:end])
        new_exempt = _PRAGMA in segment
        # Recurse into function body with updated context
        for child in ast.iter_child_nodes(node):
            _walk(child, source_lines, new_fn_name, new_exempt, violations, filename)
        return

    # Check string-bearing nodes
    template = _build_template(node)  # type: ignore[arg-type]
    if template is not None:
        if _SENTINEL_RE.fullmatch(template):
            src = ast.unparse(node)
            is_chokepoint = fn_name == _CHOKEPOINT_FN
            has_rid = _has_run_id(src, template)

            # R1: inline sentinel outside the chokepoint helper
            if not is_chokepoint and not fn_exempt:
                violations.append(Violation(
                    file=filename,
                    line_no=getattr(node, "lineno", 0),
                    context=fn_name,
                    rule="inline_sentinel_no_helper",
                    message=(
                        f"sentinel filename built inline in '{fn_name}' instead of "
                        f"calling resume_sentinel_name()"
                    ),
                ))

            # R2: sentinel missing run_id (fires even inside chokepoint)
            if not has_rid and not fn_exempt:
                violations.append(Violation(
                    file=filename,
                    line_no=getattr(node, "lineno", 0),
                    context=fn_name,
                    rule="sentinel_missing_run_id",
                    message=(
                        f"sentinel filename in '{fn_name}' is missing the run_id "
                        f"component (_r{{run_id}}); add run_id to prevent stale-replay"
                    ),
                ))
        # Always recurse into child nodes of this node (e.g. inside JoinedStr there
        # may be nested expressions, though in practice templates are leaf nodes).
        # For Constant nodes there are no children; safe to iterate.

    # Recurse into all children for non-function, non-string nodes
    for child in ast.iter_child_nodes(node):
        _walk(child, source_lines, fn_name, fn_exempt, violations, filename)


def lint_text(source: str, filename: str) -> list[Violation]:
    """Parse source and return all resume-key Violations."""
    try:
        tree = ast.parse(source, filename=filename)
    except SyntaxError as exc:
        raise exc

    source_lines = source.split("\n")
    violations: list[Violation] = []

    # Walk the module body; module-level context = "<module>", not exempt unless
    # the whole source contains the pragma (rare; handled by fn_exempt=False here
    # since module-level sentinels are unusual and module scope has no fn segment).
    for child in ast.iter_child_nodes(tree):
        _walk(child, source_lines, "<module>", False, violations, filename)

    return violations


def lint_file(path) -> list[Violation]:
    """Read file at path and return violations (raises OSError on read failure)."""
    p = Path(path)
    source = p.read_text(encoding="utf-8")
    return lint_text(source, str(path))


def main(argv=None) -> int:
    """CLI entry point. Returns 0 (clean), 1 (violations), or 2 (usage error)."""
    parser = argparse.ArgumentParser(
        description=(
            "Lint Python files for inline resume-sentinel filenames that bypass "
            "the resume_sentinel_name() chokepoint or omit run_id."
        ),
    )
    parser.add_argument(
        "files",
        nargs="*",
        metavar="FILE",
        help=(
            "Python source file(s) to lint. Defaults to "
            "engine_py/lib/resume_keying.py and engine_py/workflows/phase_6_review.py."
        ),
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit JSON output instead of human-readable text.",
    )

    args = parser.parse_args(argv)

    # Default to the canonical targets when no files given
    if not args.files:
        _engine_py_root = Path(__file__).resolve().parent
        paths = [
            str(_engine_py_root / "lib" / "resume_keying.py"),
            str(_engine_py_root / "workflows" / "phase_6_review.py"),
        ]
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
                    "context": v.context,
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
                f"RESUME-KEY {v.file}:{v.line_no} ctx={v.context} {v.rule}: {v.message}"
            )

    return 1 if all_violations else 0


if __name__ == "__main__":
    sys.exit(main())
