"""suite_safety.py — deterministic AST-based suite-safety scanner.

Detects two classes of suite-unsafe patterns in RED/GREEN test files:
  1. Module-level sys.path.insert(...) — only at Module.body level (not inside functions/classes).
  2. from conftest import ... — at ANY scope (module or function level).

These patterns cause sys.modules collisions that pollute sibling tests.

Part of 585E30E3 P1a — engine-deterministic suite-safety gate.
"""
from __future__ import annotations

import ast


def scan_suite_safety(source: str) -> list[str]:
    """Scan Python source for suite-unsafe patterns.

    Returns a list of violation strings, each formatted as:
      "<lineno>: module-level sys.path.insert"
      "<lineno>: from conftest import"

    Returns [] for empty source, unparseable source (SyntaxError — not our
    concern; the file failing to parse is a separate issue), or clean source.
    """
    if not source.strip():
        return []

    try:
        tree = ast.parse(source)
    except SyntaxError:
        # A non-parseable file is not a suite-safety concern here; return clean.
        return []

    violations: list[str] = []

    # ── Pass 1: module-level sys.path.insert ──────────────────────────────────
    # Only flag ast.Call nodes that appear directly in Module.body (wrapped in
    # ast.Expr or bare). NOT flagged when nested inside function/class bodies.
    for node in tree.body:
        call_node: ast.Call | None = None
        if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            call_node = node.value
        elif isinstance(node, ast.Call):
            call_node = node

        if call_node is not None and _is_sys_path_insert(call_node):
            violations.append(f"{call_node.lineno}: module-level sys.path.insert")

    # ── Pass 2: from conftest import ... at ANY scope ─────────────────────────
    for sub in ast.walk(tree):
        if isinstance(sub, ast.ImportFrom) and sub.module == "conftest":
            violations.append(f"{sub.lineno}: from conftest import")

    return violations


def _is_sys_path_insert(call: ast.Call) -> bool:
    """Return True if the ast.Call node represents sys.path.insert(...)."""
    func = call.func
    # sys.path.insert → Attribute(value=Attribute(value=Name(id='sys'), attr='path'), attr='insert')
    if not isinstance(func, ast.Attribute):
        return False
    if func.attr != "insert":
        return False
    value = func.value
    if not isinstance(value, ast.Attribute):
        return False
    if value.attr != "path":
        return False
    if not isinstance(value.value, ast.Name):
        return False
    return value.value.id == "sys"
