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


def _names_in_target(target: ast.AST) -> list[str]:
    return [n.id for n in ast.walk(target) if isinstance(n, ast.Name)]


def _collect_bindings(tree: ast.AST) -> dict[str, list[ast.AST]]:
    bindings: dict[str, list[ast.AST]] = {}

    def bind(target: ast.AST, value: ast.AST | None) -> None:
        if value is None:
            return
        for name in _names_in_target(target):
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
    return bindings


def _sys_aliases(tree: ast.AST) -> set[str]:
    aliases = {"sys"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name == "sys":
                    aliases.add(a.asname or "sys")
    return aliases


def _is_sys_path(node: ast.AST, aliases: set[str]) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == "path"
        and isinstance(node.value, ast.Name)
        and node.value.id in aliases
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
    aliases = _sys_aliases(tree)
    found: dict[int, str] = {}

    def check(lineno: int, exprs: list[ast.AST], what: str) -> None:
        if _inside_package(exprs, bindings):
            found.setdefault(
                lineno, f"{filename}:{lineno}: {what} targets {_PKG}/ on sys.path"
            )

    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            f = node.func
            args = list(node.args) + [k.value for k in node.keywords]
            if f.attr in _MUTATING_METHODS and _is_sys_path(f.value, aliases):
                check(node.lineno, args, f"sys.path.{f.attr}")
            elif f.attr == "syspath_prepend":
                check(node.lineno, args, "monkeypatch.syspath_prepend")
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if _is_sys_path(t, aliases) or (
                    isinstance(t, ast.Subscript) and _is_sys_path(t.value, aliases)
                ):
                    check(node.lineno, [node.value], "sys.path assignment")
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            t = node.target
            if node.value is not None and (
                _is_sys_path(t, aliases)
                or (isinstance(t, ast.Subscript) and _is_sys_path(t.value, aliases))
            ):
                check(node.lineno, [node.value], "sys.path assignment")
    return sorted(found.values(), key=lambda s: int(s.split(":")[1]))


# ------------------------------------------------------------- tree scan ----


def test_tests_tree_has_no_engine_package_syspath_mutation() -> None:
    offenders: list[str] = []
    for path in sorted(_TESTS_DIR.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        try:
            src = path.read_text(encoding="utf-8")
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", SyntaxWarning)
                offenders.extend(_scan_source(src, str(path.relative_to(_TESTS_DIR))))
        except (SyntaxError, UnicodeDecodeError):
            continue
    for line in offenders:
        print(line)
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
