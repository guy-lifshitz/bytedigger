"""Tree-scan call-site inventory lint (bd#94).

Every call that enumerates the working tree or asks git which paths changed is a
"tree-scan site". The sites are keyed
``<relpath>::<qualname or <module>>::<kind>#<n>`` and each one must be classified in
``tree_scan_inventory.json`` as

  * ``filters``     - the enclosing scope drops engine-owned paths (it references a member of
                      ``FILTER_NAMES``); or
  * ``not-a-gate``  - the read set never feeds a gate, a commit or a hash, with a note saying why.

``check()`` reports a site with no key, a key with no site (stale), a malformed entry, a
``filters`` entry whose scope references no filter, and any ``git add`` site that is not
``filters``. The lint classifies; it does not decide what is a correct filter.

Scope: every ``*.py`` under the package root except ``conformance/`` and ``tests/``.
Qualnames are ``Class.method`` and ``outer.<locals>.inner``; a lambda is not its own scope.
Each call yields at most one site.
"""
from __future__ import annotations

import ast
import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

FILTER_NAMES = frozenset({
    "drop_engine_owned",
    "drop_engine_owned_porcelain",
    "is_engine_owned_path",
    "is_engine_state_path",
    "engine_owned_pathspecs",
    "prune_engine_owned_dirs",
    "_filter_gitignored_paths",
})

# Canonical dotted names, matched after import-alias resolution (alias resolution wins over
# the attribute rule below).
_RESOLVED_KINDS = frozenset({"os.walk", "os.scandir", "os.listdir", "glob.glob", "glob.iglob"})

# Attribute calls on anything (pathlib objects).
_ATTR_KINDS = frozenset({"rglob", "glob", "iterdir"})

# Helpers keyed by their terminal callee name.
_HELPER_KINDS = frozenset({
    "git_diff_files",
    "diff_files",
    "status_porcelain",
    "git_status_porcelain",
    "ls_files_others",
})

_GIT_KINDS = frozenset({"git-status", "git-ls-files", "git-add", "git-diff-names"})

KINDS = frozenset(_RESOLVED_KINDS | _ATTR_KINDS | _HELPER_KINDS | _GIT_KINDS)

VALID_CLASSES = frozenset({"filters", "not-a-gate"})

_SKIP_TOP_DIRS = ("conformance", "tests")

# git options that consume the NEXT token as their value.
_VALUE_OPTIONS = frozenset({"-C", "-c", "--git-dir", "--work-tree"})

# `git diff` flags that turn the output into a list of paths.
_NAME_FLAGS = frozenset({"--name-only", "--name-status", "--numstat", "--stat"})

_INVENTORY_PATH = Path(__file__).with_name("tree_scan_inventory.json")


def load_inventory() -> Dict[str, Any]:
    """Return the shipped inventory: ``{"version": 1, "sites": {key: entry}}``."""
    data = json.loads(_INVENTORY_PATH.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _collect_bindings(tree: ast.AST) -> Dict[str, str]:
    """Map a locally bound name to the dotted name it was imported as (module-wide)."""
    bindings: Dict[str, str] = {}
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
                bindings[alias.asname or alias.name] = base + "." + alias.name
    return bindings


def _dotted(func: ast.expr) -> Optional[List[str]]:
    """Flatten ``a.b.c`` into ``["a", "b", "c"]``; None when the root is not a plain name."""
    parts: List[str] = []
    cur: ast.expr = func
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if not isinstance(cur, ast.Name):
        return None
    parts.append(cur.id)
    parts.reverse()
    return parts


def _terminal(func: ast.expr) -> Optional[str]:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _list_node(node: ast.expr) -> Optional[ast.expr]:
    """The list/tuple literal that is `node`, or the leftmost operand of a `+` chain."""
    cur: ast.expr = node
    while isinstance(cur, ast.BinOp) and isinstance(cur.op, ast.Add):
        cur = cur.left
    if isinstance(cur, (ast.List, ast.Tuple)):
        return cur
    return None


def _is_str(node: ast.expr) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


def _tokens(elements: List[ast.expr]) -> List[Optional[str]]:
    """String literals as themselves, anything else as a None placeholder; starred items skipped."""
    out: List[Optional[str]] = []
    for el in elements:
        if isinstance(el, ast.Starred):
            continue
        if isinstance(el, ast.Constant) and isinstance(el.value, str):
            out.append(el.value)
        else:
            out.append(None)
    return out


def _verb_kind(tokens: List[Optional[str]]) -> Optional[str]:
    """Kind of the git verb in `tokens`, or None when the verb is not an inventoried one."""
    i = 0
    if tokens and tokens[0] == "git":
        i = 1
    while i < len(tokens):
        tok = tokens[i]
        if tok is None:
            return None
        if tok in _VALUE_OPTIONS:
            i += 2
            continue
        if tok.startswith("-"):
            i += 1
            continue
        rest = [t for t in tokens[i + 1:] if t is not None]
        if tok == "status":
            return "git-status"
        if tok == "ls-files":
            return "git-ls-files"
        if tok == "add":
            return "git-add"
        if tok == "diff" and any(t in _NAME_FLAGS for t in rest):
            return "git-diff-names"
        return None
    return None


def _git_kind(call: ast.Call) -> Optional[str]:
    term = _terminal(call.func)
    seq = _list_node(call.args[0]) if call.args else None
    if seq is not None:
        assert isinstance(seq, (ast.List, ast.Tuple))
        toks = _tokens(list(seq.elts))
        gated = toks[:1] == ["git"] or (term is not None and "git" in term.lower())
        if not gated:
            return None
        return _verb_kind(toks)
    if term is None or term.lstrip("_") != "git":
        return None
    args = list(call.args)
    start = 0
    while start < len(args) and not _is_str(args[start]):
        start += 1
    return _verb_kind(_tokens(args[start:]))


def _classify(call: ast.Call, bindings: Dict[str, str]) -> Optional[str]:
    """The site kind of a call, or None when it is not a tree-scan site."""
    dotted = _dotted(call.func)
    if dotted is not None:
        resolved = ".".join([bindings.get(dotted[0], dotted[0])] + dotted[1:])
        if resolved in _RESOLVED_KINDS:
            return resolved
    if isinstance(call.func, ast.Attribute) and call.func.attr in _ATTR_KINDS:
        return call.func.attr
    term = _terminal(call.func)
    if term is not None and term in _HELPER_KINDS:
        return term
    return _git_kind(call)


_Hit = Tuple[str, int, int, str, ast.AST]  # (qualname, line, col, kind, scope node)


class _Visitor(ast.NodeVisitor):
    def __init__(self, bindings: Dict[str, str], module: ast.AST) -> None:
        self._bindings = bindings
        self._stack: List[Tuple[str, bool, ast.AST]] = []  # (qualname, is_function, node)
        self._module = module
        self.hits: List[_Hit] = []

    def _qual(self) -> str:
        return self._stack[-1][0] if self._stack else "<module>"

    def _scope_node(self) -> ast.AST:
        return self._stack[-1][2] if self._stack else self._module

    def _child(self, name: str) -> str:
        if not self._stack:
            return name
        parent, parent_is_func, _node = self._stack[-1]
        return parent + ".<locals>." + name if parent_is_func else parent + "." + name

    def _visit_args_outer(self, args: ast.arguments) -> None:
        # Defaults and annotations are evaluated in the CONTAINING scope.
        for default in args.defaults:
            self.visit(default)
        for kw_default in args.kw_defaults:
            if kw_default is not None:
                self.visit(kw_default)
        every = [*args.posonlyargs, *args.args, *args.kwonlyargs]
        if args.vararg is not None:
            every.append(args.vararg)
        if args.kwarg is not None:
            every.append(args.kwarg)
        for arg in every:
            if arg.annotation is not None:
                self.visit(arg.annotation)

    def _visit_function(self, node: Any) -> None:
        for dec in node.decorator_list:
            self.visit(dec)
        self._visit_args_outer(node.args)
        if node.returns is not None:
            self.visit(node.returns)
        self._stack.append((self._child(node.name), True, node))
        for stmt in node.body:
            self.visit(stmt)
        self._stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for dec in node.decorator_list:
            self.visit(dec)
        for base in node.bases:
            self.visit(base)
        for kw in node.keywords:
            self.visit(kw)
        self._stack.append((self._child(node.name), False, node))
        for stmt in node.body:
            self.visit(stmt)
        self._stack.pop()

    # A lambda is NOT its own scope: its calls belong to the enclosing def (no visit_Lambda).

    def visit_Call(self, node: ast.Call) -> None:
        kind = _classify(node, self._bindings)
        if kind is not None:
            self.hits.append((self._qual(), node.lineno, node.col_offset, kind, self._scope_node()))
        self.generic_visit(node)


def _referenced_names(scope: ast.AST) -> Set[str]:
    names: Set[str] = set()
    for sub in ast.walk(scope):
        if isinstance(sub, ast.Name):
            names.add(sub.id)
        elif isinstance(sub, ast.Attribute):
            names.add(sub.attr)
    return names


# (key, relpath, line, kind, scope_references_a_filter)
_Site = Tuple[str, str, int, str, bool]


def _scan(root: Path) -> List[_Site]:
    out: List[_Site] = []
    for path in sorted(root.rglob("*.py")):
        rel_path = path.relative_to(root)
        if len(rel_path.parts) > 1 and rel_path.parts[0] in _SKIP_TOP_DIRS:
            continue
        rel = rel_path.as_posix()
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        visitor = _Visitor(_collect_bindings(tree), tree)
        visitor.visit(tree)
        grouped: Dict[Tuple[str, str], List[Tuple[int, int, ast.AST]]] = {}
        for qual, line, col, kind, scope in visitor.hits:
            grouped.setdefault((qual, kind), []).append((line, col, scope))
        refs_cache: Dict[int, bool] = {}
        for (qual, kind), positions in grouped.items():
            ordered = sorted(positions, key=lambda p: (p[0], p[1]))
            for ordinal, (line, _col, scope) in enumerate(ordered):
                if id(scope) not in refs_cache:
                    refs_cache[id(scope)] = bool(_referenced_names(scope) & FILTER_NAMES)
                key = rel + "::" + qual + "::" + kind + "#" + str(ordinal)
                out.append((key, rel, line, kind, refs_cache[id(scope)]))
    return out


def call_sites(root: Path) -> List[str]:
    """Sorted site keys found under *root*."""
    return sorted(key for key, _rel, _line, _kind, _filtered in _scan(Path(root)))


def _entry_problems(entry: Any) -> List[str]:
    if not isinstance(entry, dict):
        return ["entry is not an object"]
    problems: List[str] = []
    cls = entry.get("class")
    if cls not in VALID_CLASSES:
        problems.append("unknown class " + repr(cls))
    note = entry.get("note")
    if not (isinstance(note, str) and note.strip()):
        problems.append("note must be a non-empty string")
    return problems


def check(root: Path, inventory: Dict[str, Any]) -> List[str]:
    """Return problems for the tree under *root*; ``[]`` means clean."""
    raw_sites = inventory.get("sites")
    sites: Dict[str, Any] = raw_sites if isinstance(raw_sites, dict) else {}
    found: Dict[str, Tuple[str, int, str, bool]] = {}
    for key, rel, line, kind, filtered in _scan(Path(root)):
        found[key] = (rel, line, kind, filtered)
    problems: List[str] = []
    for key in sorted(found):
        rel, line, kind, filtered = found[key]
        where = rel + ":" + str(line) + ": "
        if key not in sites:
            problems.append(where + "no key for call site " + key)
            continue
        entry = sites[key]
        bad = _entry_problems(entry)
        if bad:
            problems.append(where + key + ": malformed inventory entry (" + "; ".join(bad) + ")")
            continue
        cls = entry["class"]
        if cls == "filters" and not filtered:
            problems.append(
                where + key + ": filters without filter call "
                "(the enclosing scope references none of FILTER_NAMES)"
            )
        elif cls != "filters" and kind == "git-add":
            problems.append(
                where + key + ": git-add must filter "
                "(a git add site cannot be classified not-a-gate)"
            )
    for key in sorted(set(sites) - set(found)):
        problems.append("stale inventory key (no call site): " + key)
    return problems
