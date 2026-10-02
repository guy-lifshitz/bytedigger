"""Class-I call-site inventory lint (bd#150).

Every call that can pull bytes off disk or out of a subprocess inside
``workflows/`` and ``lib/`` is keyed ``<relpath>::<qualname or <module>>::<callee>#<n>``
and must be classified in ``class_i_inventory.json``. ``check()`` reports a site
with no key, a key with no site (stale), and a malformed entry.

The lint classifies; it does not decide what reaches a prompt. Which classes a
given issue may carry is enforced by the tests that read the inventory.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any

# Wrapper helpers whose bodies hold the real read/spawn: each CALLER is keyed.
WRAPPER_CALLEES = frozenset({
    "bounded_run",
    "run_test_command",
    "_read_text_or_empty",
    "_read_or_empty",
    "_git_read",
    "git_read",
    "rev_parse",
    "status_porcelain",
    "ls_files_others",
    "worktree_list_porcelain",
    "_run_git",
})

# Canonical dotted names, matched after import-alias resolution.
_STDLIB_CALLEES = frozenset({
    "json.load",
    "subprocess.run",
    "subprocess.check_output",
    "subprocess.Popen",
})

_READ_CALLEES = frozenset({"read_text", "read_bytes"})

_SCAN_DIRS = ("workflows", "lib")

VALID_CLASSES = frozenset({"I-declared", "I-deferred", "E", "M", "not-prompt"})

_INVENTORY_PATH = Path(__file__).with_name("class_i_inventory.json")


def load_inventory() -> dict[str, Any]:
    """Return the shipped inventory: ``{"version": 1, "sites": {key: entry}}``."""
    data = json.loads(_INVENTORY_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _collect_bindings(tree: ast.AST) -> dict[str, str]:
    """Map a locally bound name to the dotted name it was imported as (module-wide)."""
    bindings: dict[str, str] = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            for alias in n.names:
                if alias.asname:
                    bindings[alias.asname] = alias.name
                else:
                    top = alias.name.split(".", 1)[0]
                    bindings[top] = top
        elif isinstance(n, ast.ImportFrom):
            base = "." * n.level + (n.module or "")
            for alias in n.names:
                if alias.name == "*":
                    continue
                bindings[alias.asname or alias.name] = f"{base}.{alias.name}"
    return bindings


def _dotted(func: ast.expr) -> list[str] | None:
    """Flatten ``a.b.c`` into ``["a", "b", "c"]``; None when the root is not a plain name."""
    parts: list[str] = []
    cur: ast.expr = func
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if not isinstance(cur, ast.Name):
        return None
    parts.append(cur.id)
    parts.reverse()
    return parts


def _callee(func: ast.expr, bindings: dict[str, str]) -> str | None:
    """Canonical callee name of a call, or None when the call is not inventoried."""
    dotted = _dotted(func)
    resolved: str | None = None
    if dotted is not None:
        resolved = ".".join([bindings.get(dotted[0], dotted[0])] + dotted[1:])
        if resolved in _STDLIB_CALLEES:
            return resolved
    if isinstance(func, ast.Attribute):
        last = func.attr
    elif isinstance(func, ast.Name):
        last = (resolved or func.id).rsplit(".", 1)[-1]
    else:
        return None
    if last in WRAPPER_CALLEES:
        return last
    if last in _READ_CALLEES:
        return last
    if isinstance(func, ast.Attribute) and last == "open":
        return ".open"
    if isinstance(func, ast.Name) and last == "open":
        return "open"
    return None


class _Visitor(ast.NodeVisitor):
    def __init__(self, bindings: dict[str, str]) -> None:
        self._bindings = bindings
        self._stack: list[tuple[str, bool]] = []  # (qualname, is_function)
        self.hits: list[tuple[str, int, int, str]] = []  # (qualname, line, col, callee)

    def _qual(self) -> str:
        return self._stack[-1][0] if self._stack else "<module>"

    def _child(self, name: str) -> str:
        if not self._stack:
            return name
        parent, parent_is_func = self._stack[-1]
        return f"{parent}.<locals>.{name}" if parent_is_func else f"{parent}.{name}"

    def _visit_args_outer(self, args: ast.arguments) -> None:
        # Defaults and annotations are evaluated in the CONTAINING scope.
        for d in args.defaults:
            self.visit(d)
        for kd in args.kw_defaults:
            if kd is not None:
                self.visit(kd)
        every = [*args.posonlyargs, *args.args, *args.kwonlyargs]
        if args.vararg is not None:
            every.append(args.vararg)
        if args.kwarg is not None:
            every.append(args.kwarg)
        for a in every:
            if a.annotation is not None:
                self.visit(a.annotation)

    def _visit_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for dec in node.decorator_list:
            self.visit(dec)
        self._visit_args_outer(node.args)
        if node.returns is not None:
            self.visit(node.returns)
        self._stack.append((self._child(node.name), True))
        for stmt in node.body:
            self.visit(stmt)
        self._stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self._visit_args_outer(node.args)
        self._stack.append((self._child("<lambda>"), True))
        self.visit(node.body)
        self._stack.pop()

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for dec in node.decorator_list:
            self.visit(dec)
        for base in node.bases:
            self.visit(base)
        for kw in node.keywords:
            self.visit(kw)
        self._stack.append((self._child(node.name), False))
        for stmt in node.body:
            self.visit(stmt)
        self._stack.pop()

    def visit_Call(self, node: ast.Call) -> None:
        name = _callee(node.func, self._bindings)
        if name is not None:
            self.hits.append((self._qual(), node.lineno, node.col_offset, name))
        self.generic_visit(node)


def _scan(root: Path) -> list[tuple[str, str, int]]:
    """Return ``(key, relpath, line)`` for every inventoried call site under *root*."""
    out: list[tuple[str, str, int]] = []
    for sub in _SCAN_DIRS:
        base = root / sub
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*.py")):
            rel = path.relative_to(root).as_posix()
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
            visitor = _Visitor(_collect_bindings(tree))
            visitor.visit(tree)
            grouped: dict[tuple[str, str], list[tuple[int, int]]] = {}
            for qual, line, col, callee in visitor.hits:
                grouped.setdefault((qual, callee), []).append((line, col))
            for (qual, callee), positions in grouped.items():
                for ordinal, (line, _col) in enumerate(sorted(positions)):
                    out.append((f"{rel}::{qual}::{callee}#{ordinal}", rel, line))
    return out


def call_sites(root: Path) -> list[str]:
    """Sorted site keys found under ``root/workflows`` and ``root/lib``."""
    return sorted(key for key, _rel, _line in _scan(Path(root)))


def _entry_problems(entry: Any) -> list[str]:
    if not isinstance(entry, dict):
        return ["entry is not an object"]
    problems: list[str] = []
    cls = entry.get("class")
    if cls not in VALID_CLASSES:
        problems.append(f"unknown class {cls!r}")
    issue = entry.get("issue")
    if cls == "I-deferred":
        if not (isinstance(issue, str) and issue.strip()):
            problems.append("class I-deferred requires a non-empty `issue`")
    elif "issue" in entry:
        problems.append(f"class {cls!r} must not carry `issue`")
    note = entry.get("note")
    if not (isinstance(note, str) and note.strip()):
        problems.append("note must be a non-empty string")
    return problems


def check(root: Path, inventory: dict[str, Any]) -> list[str]:
    """Return problems: unkeyed sites, stale keys, malformed entries. ``[]`` means clean."""
    raw_sites = inventory.get("sites")
    sites: dict[str, Any] = raw_sites if isinstance(raw_sites, dict) else {}
    found: dict[str, tuple[str, int]] = {}
    for key, rel, line in _scan(Path(root)):
        found[key] = (rel, line)
    problems: list[str] = []
    for key in sorted(found):
        rel, line = found[key]
        if key not in sites:
            problems.append(f"{rel}:{line}: unkeyed call site {key}")
            continue
        for msg in _entry_problems(sites[key]):
            problems.append(f"{rel}:{line}: {key}: {msg}")
    for key in sorted(set(sites) - set(found)):
        problems.append(f"stale inventory key (no call site): {key}")
    return problems
