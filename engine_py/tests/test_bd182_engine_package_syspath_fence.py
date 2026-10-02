"""bd#182: no test may put anything inside bytedigger_engine/ on sys.path.

Runtime fence (sys.path entries + flat lib names not importable) and a static
AST fence over every *.py under engine_py/tests that catches sys.path mutations
targeting the package, including ones that run later inside a test body.
"""

from __future__ import annotations

import ast
import importlib.util
import re
import sys
import textwrap
import warnings
from pathlib import Path

import pytest

_TESTS_DIR = Path(__file__).resolve().parent
_ENGINE_PKG_DIR = (_TESTS_DIR.parent / "bytedigger_engine").resolve()
_PKG = "bytedigger_engine"
_MIN_FILES_PARSED = 40

_FLAT_LIB_NAMES = [
    "model_config",
    "verdict_parse",
    "authored_boundary",
    "ground_truth_verifier",
]


# ---------------------------------------------------------------- runtime ---


def test_no_sys_path_entry_inside_engine_package() -> None:
    offenders = []
    for entry in sys.path:
        try:
            resolved = Path(entry or ".").resolve()
        except (OSError, RuntimeError, ValueError):
            continue
        if resolved == _ENGINE_PKG_DIR or resolved.is_relative_to(_ENGINE_PKG_DIR):
            offenders.append(entry)
    assert not offenders, (
        f"bd#182 fence breached: sys.path entries inside {_ENGINE_PKG_DIR}: "
        f"{offenders!r}"
    )


@pytest.mark.parametrize("name", _FLAT_LIB_NAMES)
def test_flat_lib_name_not_importable(name: str) -> None:
    spec = importlib.util.find_spec(name)
    assert spec is None, (
        f"flat name {name!r} resolved to {spec.origin!r} -- bd#182 fence "
        f"breached by a test-suite sys.path leak"
    )


# ----------------------------------------------------------------- scanner ---

_MUTATING_METHODS = {"insert", "append", "extend"}


def _bare_names(target: ast.AST) -> list[str]:
    """Names bound by a target; Subscript / Attribute targets bind nothing."""
    if isinstance(target, ast.Name):
        return [target.id]
    if isinstance(target, (ast.Tuple, ast.List)):
        out: list[str] = []
        for elt in target.elts:
            out.extend(_bare_names(elt))
        return out
    if isinstance(target, ast.Starred):
        return _bare_names(target.value)
    return []


def _collect_bindings(tree: ast.AST) -> dict[str, list[ast.AST]]:
    """name -> value exprs; "()name" -> return exprs of a module-local function."""
    bindings: dict[str, list[ast.AST]] = {}

    def bind(target: ast.AST, value: ast.AST | None) -> None:
        if value is None:
            return
        for name in _bare_names(target):
            bindings.setdefault(name, []).append(value)

    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for t in node.targets:
                bind(t, node.value)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            bind(node.target, node.value)
        elif isinstance(node, (ast.For, ast.AsyncFor)):
            bind(node.target, node.iter)
        elif isinstance(node, ast.NamedExpr):
            bind(node.target, node.value)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            rets = [
                r.value
                for r in ast.walk(node)
                if isinstance(r, ast.Return) and r.value is not None
            ]
            bindings.setdefault("()" + node.name, []).extend(rets)
    return bindings


class _Aliases:
    def __init__(self, tree: ast.AST) -> None:
        self.sys_mods = {"sys"}
        self.path_names: set[str] = set()
        self.site_mods = {"site"}
        self.addsitedir_names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    if a.name == "sys":
                        self.sys_mods.add(a.asname or "sys")
                    elif a.name == "site":
                        self.site_mods.add(a.asname or "site")
            elif isinstance(node, ast.ImportFrom) and node.level == 0:
                for a in node.names:
                    if node.module == "sys" and a.name == "path":
                        self.path_names.add(a.asname or "path")
                    elif node.module == "site" and a.name == "addsitedir":
                        self.addsitedir_names.add(a.asname or "addsitedir")

    def is_sys_path(self, node: ast.AST) -> bool:
        if isinstance(node, ast.Name):
            return node.id in self.path_names
        return (
            isinstance(node, ast.Attribute)
            and node.attr == "path"
            and isinstance(node.value, ast.Name)
            and node.value.id in self.sys_mods
        )

    def is_sys_path_target(self, t: ast.AST) -> bool:
        return self.is_sys_path(t) or (
            isinstance(t, ast.Subscript) and self.is_sys_path(t.value)
        )

    def is_addsitedir(self, func: ast.AST) -> bool:
        if isinstance(func, ast.Name):
            return func.id in self.addsitedir_names
        return (
            isinstance(func, ast.Attribute)
            and func.attr == "addsitedir"
            and isinstance(func.value, ast.Name)
            and func.value.id in self.site_mods
        )


def _reachable_strings(
    expr: ast.AST, bindings: dict[str, list[ast.AST]], seen: set[str]
) -> list[str]:
    out: list[str] = []
    for n in ast.walk(expr):
        if isinstance(n, ast.Constant) and isinstance(n.value, str):
            out.append(n.value)
        elif isinstance(n, ast.Name) and n.id not in seen:
            seen.add(n.id)
            for val in bindings.get(n.id, []):
                out.extend(_reachable_strings(val, bindings, seen))
        elif isinstance(n, ast.Call) and isinstance(n.func, ast.Name):
            key = "()" + n.func.id
            if key in bindings and key not in seen:
                seen.add(key)
                for val in bindings[key]:
                    out.extend(_reachable_strings(val, bindings, seen))
    return out


def _inside_package(exprs: list[ast.AST], bindings: dict[str, list[ast.AST]]) -> bool:
    seen: set[str] = set()
    for e in exprs:
        for s in _reachable_strings(e, bindings, seen):
            if _PKG in re.split(r"[/\\]", s):
                return True
    return False


def _scan_source(src: str, filename: str) -> list[str]:
    tree = ast.parse(src)
    bindings = _collect_bindings(tree)
    al = _Aliases(tree)
    found: dict[int, str] = {}

    def check(lineno: int, exprs: list[ast.AST], what: str) -> None:
        if _inside_package(exprs, bindings):
            found.setdefault(
                lineno, f"{filename}:{lineno}: {what} targets {_PKG}/ on sys.path"
            )

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            args = list(node.args) + [k.value for k in node.keywords]
            f = node.func
            if (
                isinstance(f, ast.Attribute)
                and f.attr in _MUTATING_METHODS
                and al.is_sys_path(f.value)
            ):
                check(node.lineno, args, f"sys.path.{f.attr}")
            elif isinstance(f, ast.Attribute) and f.attr == "syspath_prepend":
                check(node.lineno, args, "monkeypatch.syspath_prepend")
            elif al.is_addsitedir(f):
                check(node.lineno, args, "site.addsitedir")
        elif isinstance(node, ast.Assign):
            if any(al.is_sys_path_target(t) for t in node.targets):
                check(node.lineno, [node.value], "sys.path assignment")
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            if node.value is not None and al.is_sys_path_target(node.target):
                check(node.lineno, [node.value], "sys.path assignment")
    return sorted(found.values(), key=lambda s: int(s.split(":")[1]))


# ------------------------------------------------------------- tree scan ----


def test_tests_tree_has_no_engine_package_syspath_mutation() -> None:
    offenders: list[str] = []
    unparseable: list[str] = []
    parsed = 0
    for path in sorted(_TESTS_DIR.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = str(path.relative_to(_TESTS_DIR))
        try:
            src = path.read_text(encoding="utf-8")
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", SyntaxWarning)
                hits = _scan_source(src, rel)
        except (SyntaxError, UnicodeDecodeError, ValueError) as exc:
            unparseable.append(f"{rel}: {type(exc).__name__}: {exc}")
            continue
        parsed += 1
        offenders.extend(hits)
    for line in offenders:
        print(line)
    assert not unparseable, (
        f"bd#182: {len(unparseable)} file(s) could not be parsed:\n"
        + "\n".join(unparseable)
    )
    assert parsed >= _MIN_FILES_PARSED, (
        f"bd#182: only {parsed} files parsed (< {_MIN_FILES_PARSED}); "
        f"scan root {_TESTS_DIR} looks wrong"
    )
    assert not offenders, (
        f"bd#182: {len(offenders)} sys.path mutation(s) target {_PKG}/:\n"
        + "\n".join(offenders)
    )


# ------------------------------------------------------- scanner self-tests ---


def _scan(snippet: str) -> list[str]:
    return _scan_source(textwrap.dedent(snippet), "snippet.py")


def test_scanner_flags_direct_insert() -> None:
    assert _scan(
        """
        import sys
        sys.path.insert(0, str(ENGINE / "bytedigger_engine" / "lib"))
        """
    )


def test_scanner_flags_module_constant_used_in_function() -> None:
    hits = _scan(
        """
        import sys
        LIB = ROOT / "bytedigger_engine" / "lib"

        def helper():
            sys.path.insert(0, str(LIB))
        """
    )
    assert len(hits) == 1 and hits[0].startswith("snippet.py:6:")


def test_scanner_flags_for_loop_tuple() -> None:
    assert _scan(
        """
        import sys
        for p in (str(ROOT), str(ROOT / "bytedigger_engine" / "lib")):
            sys.path.insert(0, p)
        """
    )


def test_scanner_flags_os_path_join_append() -> None:
    assert _scan(
        """
        import os, sys
        sys.path.append(os.path.join(ROOT, "bytedigger_engine", "scripts", "lib"))
        """
    )


def test_scanner_flags_sys_alias() -> None:
    assert _scan(
        """
        import sys as _sys
        _sys.path.append(str(ROOT / "bytedigger_engine" / "lib"))
        """
    )


def test_scanner_flags_slice_assignment() -> None:
    assert _scan(
        """
        import sys
        X = "bytedigger_engine/lib"
        sys.path[:0] = [X]
        """
    )


def test_scanner_flags_augassign() -> None:
    assert _scan(
        """
        import sys
        X = "bytedigger_engine/lib"
        sys.path += [X]
        """
    )


def test_scanner_flags_path_reassignment() -> None:
    assert _scan(
        """
        import sys
        X = "bytedigger_engine/lib"
        sys.path = [X] + sys.path
        """
    )


def test_scanner_flags_monkeypatch_syspath_prepend() -> None:
    assert _scan(
        """
        def test_x(monkeypatch):
            X = ROOT / "bytedigger_engine" / "lib"
            monkeypatch.syspath_prepend(str(X))
        """
    )


def test_scanner_terminates_and_flags_cyclic_bindings() -> None:
    assert _scan(
        """
        import sys
        a = b
        b = a
        a = "bytedigger_engine/lib"
        sys.path.insert(0, a)
        """
    )


def test_scanner_flags_from_sys_import_path_insert() -> None:
    assert _scan(
        """
        from sys import path
        path.insert(0, str(ROOT / "bytedigger_engine" / "lib"))
        """
    )


def test_scanner_flags_from_sys_import_path_alias_append() -> None:
    assert _scan(
        """
        from sys import path as p
        p.append(str(ROOT / "bytedigger_engine" / "lib"))
        """
    )


def test_scanner_flags_from_sys_import_path_extend() -> None:
    assert _scan(
        """
        from sys import path
        path.extend([str(ROOT / "bytedigger_engine" / "lib")])
        """
    )


def test_scanner_flags_from_sys_import_path_subscript_assign() -> None:
    assert _scan(
        """
        from sys import path as sp
        sp[:0] = ["bytedigger_engine/lib"]
        """
    )


def test_scanner_flags_from_sys_import_path_assign() -> None:
    assert _scan(
        """
        from sys import path
        path = ["bytedigger_engine/lib"]
        """
    )


def test_scanner_flags_from_sys_import_path_augassign() -> None:
    assert _scan(
        """
        from sys import path
        path += ["bytedigger_engine/lib"]
        """
    )


def test_scanner_flags_site_addsitedir() -> None:
    assert _scan(
        """
        import site
        site.addsitedir(str(ROOT / "bytedigger_engine" / "lib"))
        """
    )


def test_scanner_flags_site_alias_addsitedir() -> None:
    assert _scan(
        """
        import site as s
        s.addsitedir(str(ROOT / "bytedigger_engine" / "lib"))
        """
    )


def test_scanner_flags_from_site_import_addsitedir() -> None:
    assert _scan(
        """
        from site import addsitedir
        addsitedir(str(ROOT / "bytedigger_engine" / "lib"))
        """
    )


def test_scanner_flags_from_site_import_addsitedir_alias() -> None:
    assert _scan(
        """
        from site import addsitedir as add
        add(str(ROOT / "bytedigger_engine" / "lib"))
        """
    )


def test_scanner_follows_local_function_return() -> None:
    assert _scan(
        """
        import sys
        def _lib():
            return ROOT / "bytedigger_engine" / "lib"
        sys.path.insert(0, str(_lib()))
        """
    )


def test_scanner_follows_function_return_transitively_and_cycle_safe() -> None:
    assert _scan(
        """
        import sys
        def _a():
            return _b()
        def _b():
            if X:
                return _a()
            return "bytedigger_engine/lib"
        sys.path.insert(0, _a())
        """
    )


def test_scanner_flags_fstring() -> None:
    assert _scan(
        """
        import sys
        sys.path.insert(0, f"{ROOT}/bytedigger_engine/lib")
        """
    )


def test_scanner_flags_joinpath() -> None:
    assert _scan(
        """
        import sys
        sys.path.insert(0, str(ROOT.joinpath("bytedigger_engine", "lib")))
        """
    )


def test_scanner_flags_keyword_argument() -> None:
    assert _scan(
        """
        import site
        site.addsitedir(sitedir=str(ROOT / "bytedigger_engine"))
        """
    )


def test_scanner_flags_walrus_binding() -> None:
    assert _scan(
        """
        import sys
        if (lib := ROOT / "bytedigger_engine"):
            sys.path.insert(0, str(lib))
        """
    )


def test_scanner_reports_two_hits_in_one_file() -> None:
    hits = _scan(
        """
        import sys
        sys.path.insert(0, "bytedigger_engine/lib")
        x = 1
        sys.path.append("bytedigger_engine")
        """
    )
    assert len(hits) == 2
    assert hits[0].startswith("snippet.py:3:") and hits[1].startswith("snippet.py:5:")


def test_scanner_ignores_engine_root_insert() -> None:
    assert not _scan(
        """
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).parent.parent))
        """
    )


def test_scanner_ignores_tmp_path_insert() -> None:
    assert not _scan(
        """
        import sys
        def test_x(tmp_path):
            sys.path.insert(0, str(tmp_path))
        """
    )


def test_scanner_ignores_docstring_mention() -> None:
    assert not _scan(
        '''
        """Notes: do not insert bytedigger_engine/lib on sys.path."""
        import sys
        sys.path.insert(0, str(ROOT))
        '''
    )


def test_scanner_ignores_unrelated_variable_holding_package_name() -> None:
    assert not _scan(
        """
        import sys
        ROOT = Path(__file__).parent.parent
        OTHER = "bytedigger_engine"
        sys.path.insert(0, str(ROOT))
        """
    )


def test_scanner_ignores_function_returning_engine_py_root() -> None:
    assert not _scan(
        """
        import sys
        from pathlib import Path
        def _engine_py_root():
            return Path(__file__).resolve().parents[1]
        sys.path.insert(0, str(_engine_py_root()))
        """
    )


def test_scanner_subscript_assign_target_does_not_bind_name() -> None:
    assert not _scan(
        """
        import sys
        cfg = {}
        cfg["k"] = "bytedigger_engine/lib"
        sys.path.insert(0, str(cfg))
        """
    )
