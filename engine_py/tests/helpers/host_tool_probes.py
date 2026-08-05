"""bd#49 — scanner for undeclared host-tool availability probes.

The instrument for `tests/test_bd49_host_tool_declaration_gate.py`; the argument for why the
domain is exactly this, and the boundary of honesty, are in that file's docstring. Here there is
only the mechanics.

It takes SOURCE TEXT rather than a path: the gate's negative legs are fed
synthetic strings, and a scanner that only understood paths would be unverifiable on
the very set it is written for.

`HOST_TOOLS` is NOT duplicated — it is imported from `helpers.host_tools`, otherwise
there would be a second source of truth for the same list (§1g), and an added
tool would silently stay outside the gate.
"""
from __future__ import annotations

import ast

from .host_tools import HOST_TOOLS

#: The comment by which a body declares a deliberate refusal to skip.
#: The tail after the colon must be non-empty — see AC6.
HARD_FAIL_MARKER = "# host-tool-hard-fail:"

_SUBPROCESS_ENTRYPOINTS = frozenset({"run", "Popen", "check_output", "check_call", "call"})


def _callee_name(node: ast.Call) -> str | None:
    """The name of the callee — by attribute or by bare name.

    We key on the NAME, not on the module: three `shutil` aliases live in the corpus
    (`_shutil`, `_sh`, `_shutil_real`), plus `from shutil import which`.
    Matching against the module would require resolving aliases and would still leak on
    `from ... import which` (AC10).
    """
    func = node.func
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _const_str_arg(node: ast.Call) -> str | None:
    if not node.args:
        return None
    first = node.args[0]
    if isinstance(first, ast.Constant) and isinstance(first.value, str):
        return first.value
    return None


def _body_span(node: ast.FunctionDef | ast.AsyncFunctionDef) -> tuple[int, int] | None:
    """The lines of the function's OWN body (without the signature and decorators).

    Via `node.body`, not via `node.lineno`: with a multi-line signature the
    `def` line and the `) -> None` line have an indent <= that of the def line, and a naive
    line-by-line walk would return the argument list instead of the body. This is exactly the
    defect bd#102 caught in itself (`_extract_function_body`, gate MINOR-3).
    """
    if not node.body:
        return None
    end = node.body[-1].end_lineno
    if end is None:
        return None
    # The start is the def line, NOT `body[0].lineno`: a comment is not an AST
    # node, so a marker standing on the first line before the first statement
    # would fall outside the span and the declaration would not be counted (caught by AC3).
    # This does not leak between bodies: the span is still each function's own,
    # and decorators above the def line are not captured (AC7).
    return node.lineno, end


class _FunctionFacts:
    """Facts taken from a single function: probes, declarations, outgoing calls."""

    __slots__ = ("probes", "declared", "calls", "hard_fail")

    def __init__(self) -> None:
        self.probes: set[str] = set()
        self.declared: set[str] = set()
        self.calls: set[str] = set()
        self.hard_fail: bool = False


def _collect(tree: ast.AST, lines: list[str]) -> dict[str, _FunctionFacts]:
    facts: dict[str, _FunctionFacts] = {}

    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        fact = facts.setdefault(node.name, _FunctionFacts())

        for sub in ast.walk(node):
            if not isinstance(sub, ast.Call):
                continue
            name = _callee_name(sub)
            if name is None:
                continue
            arg = _const_str_arg(sub)
            if name == "which":
                if arg in HOST_TOOLS:
                    fact.probes.add(arg)
            elif name == "skip_without":
                if arg is not None:
                    fact.declared.add(arg)
            elif name not in _SUBPROCESS_ENTRYPOINTS:
                # an outgoing call — an edge for the transitive closure
                fact.calls.add(name)

        # fixtures arrive as parameters rather than calls: a body taking a
        # `tmp_path`-like fixture inherits its probes in exactly the same way.
        for arg_node in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs):
            fact.calls.add(arg_node.arg)

        span = _body_span(node)
        if span is not None:
            start, end = span
            for line in lines[start - 1:end]:
                head, sep, tail = line.partition(HARD_FAIL_MARKER)
                if sep and tail.strip():
                    fact.hard_fail = True
                    break

    return facts


def _close_over_calls(facts: dict[str, _FunctionFacts], attr: str) -> dict[str, set[str]]:
    """Transitive closure of the set `attr` over the edges `calls`.

    The closure is mandatory: `test_gh1338_corpus_parity_gate.py` keeps
    `which("bun")` in `_build_parity_fixture` rather than in the bodies of its eight ACs.
    A scanner without a closure would miss the subject of bd#49 entirely.
    """
    closed = {name: set(getattr(fact, attr)) for name, fact in facts.items()}
    changed = True
    while changed:
        changed = False
        for name, fact in facts.items():
            for callee in fact.calls:
                target = closed.get(callee)
                if target is not None and not target <= closed[name]:
                    closed[name] |= target
                    changed = True
    return closed


def undeclared_host_tool_probes(source: str) -> list[tuple[str, str]]:
    """`test_*` bodies that probe host-tool availability without declaring it.

    Returns a sorted list of `(test_name, tool)`. Empty means
    every probe is declared: either `skip_without(<the same tool>)`
    or `# host-tool-hard-fail: <non-empty argument>` in its own body.

    A `skip_without` declaration is inherited through calls (a helper hiding both
    the probe and the skip is legitimate), while a refusal marker is NOT: it is an argument about a specific
    body, and letting it leak to every caller would restore the silent
    exemption through a shared helper.
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    lines = source.splitlines()
    facts = _collect(tree, lines)
    probes = _close_over_calls(facts, "probes")
    declared = _close_over_calls(facts, "declared")

    offenders: list[tuple[str, str]] = []
    for name, fact in facts.items():
        if not name.startswith("test_"):
            continue
        if fact.hard_fail:
            continue
        for tool in sorted(probes[name] - declared[name]):
            offenders.append((name, tool))

    return sorted(offenders)
