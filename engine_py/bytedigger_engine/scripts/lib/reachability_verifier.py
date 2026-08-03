"""Reachability verifier for HAL engine_py spec_lint (5153EDD4 Ship 3, §1y gate;
GH776 Ship added FP-guard 6, caller-cites-callee residual check).

Detects when a spec co-cites a Point line (file:LN) and a Host function on the same
markdown line, but the cited Point line is NOT enclosed by the cited Host function's
ast-parsed range, AND none of the other co-cited defined names is referenced
(by ast.Name Load or ast.Attribute Load) on the cited line itself — i.e. the
cited Point line is not reached via a call/attribute-access to any co-cited
function (FP-guard 6: caller-cites-callee). Lib-tier: hard-requires its
`scripts/lib` sibling `citation_verifier` on sys.path (as every caller already
provides); no longer swallows ImportError — an unresolvable sibling is now a
loud import-time failure, not a silent fail-open.
"""
from __future__ import annotations

import ast
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from bytedigger_engine.scripts.lib import citation_verifier

logger = logging.getLogger(__name__)

# Regex to find backtick-wrapped identifiers on a markdown line.
_BACKTICK_IDENT_RE = re.compile(r"`([A-Za-z_]\w*)`")


@dataclass(frozen=True)
class ReachFinding:
    """A single §1y Point→Host reachability finding from a spec."""

    offset: int    # text offset in spec of the triggering line-citation
    rule_id: str   # always "reachability-point-host-mismatch"
    evidence: str  # "<cited_basename>:L<n>-not-in-<fn>"


def find_unreachable_acs(spec_text: str, hal_root: Path) -> list[ReachFinding]:
    """Return reachability findings for *spec_text* relative to *hal_root*.

    Algorithm:
    1. _line_citations(spec_text) → list of (file_path, line_no, offset).
    2. For each citation:
       - Resolve to existing .py via _resolve_existing_py; skip non-Python/missing.
       - _function_ranges(py) → dict name→list[(lo,hi)]; skip on parse failure.
       - Line-bounds guard: skip if n out of range (FP-guard 2).
       - _same_line_backticked_idents(spec_text, offset) → idents on same line.
       - defined = sorted idents that are functions in the file (FP-guard 3).
       - If not defined: skip (no Point↔Host association).
       - If any co-cited defined fn encloses n: skip (FP-guard 4).
       - Else compute residual = co-cited defined names that neither enclose n
         nor are referenced (ast.Name/ast.Attribute Load) on line n
         (FP-guard 6: caller-cites-callee). If residual is empty: skip.
       - Else: append ReachFinding (evidence uses residual[0]).
    3. Return sorted by offset.
    """
    if not spec_text:
        return []

    cites = _line_citations(spec_text)
    if not cites:
        return []

    findings: list[ReachFinding] = []

    for file_path, n, offset in cites:
        py = _resolve_existing_py(file_path, hal_root)
        if py is None:
            continue

        func_ranges = _function_ranges(py)
        if not func_ranges:
            continue

        # FP-guard 2: line-bounds check
        try:
            line_count = len(py.read_text(encoding="utf-8", errors="replace").splitlines())
        except (OSError, Exception):
            continue
        if n < 1 or n > line_count:
            continue

        idents = _same_line_backticked_idents(spec_text, offset)
        defined = sorted(i for i in idents if i in func_ranges)

        # FP-guard 3: no Point↔Host association
        if not defined:
            continue

        enclosing = {
            name for name, rs in func_ranges.items()
            if any(lo <= n <= hi for (lo, hi) in rs)
        }
        if not enclosing:
            # FP-guard 5: Point line is not inside any function (module-level / comment /
            # range-header above the def) — not a "Point in a sibling fn" case.
            continue

        # FP-guard 4: at least one co-cited defined fn encloses n
        if set(defined) & enclosing:
            # A co-cited host encloses N → reachable, no mismatch.
            continue

        # FP-guard 6: caller-cites-callee — a co-cited defined name is referenced
        # (ast.Name/ast.Attribute Load) on the cited line itself.
        ref_lines = _symbol_reference_lines(py)
        referenced = {name for name in defined if n in ref_lines.get(name, set())}
        residual = [name for name in defined if name not in enclosing and name not in referenced]
        if not residual:
            continue

        evidence = f"{Path(file_path).name}:L{n}-not-in-{residual[0]}"
        findings.append(ReachFinding(offset=offset, rule_id="reachability-point-host-mismatch", evidence=evidence))

    return sorted(findings, key=lambda f: f.offset)


def _line_citations(spec_text: str) -> list[tuple[str, int, int]]:
    """Return (file_path, line_no, offset) for every line-citation in *spec_text*.

    Reuses citation_verifier.extract_citations; keeps only kind=="line" entries;
    parses int from identifier (e.g. "L9" → 9).
    """
    results: list[tuple[str, int, int]] = []
    for c in citation_verifier.extract_citations(spec_text):
        if c.kind == "line":
            try:
                n = int(c.identifier.lstrip("L"))
            except ValueError:
                continue
            results.append((c.file_path, n, c.text_offset))
    return results


def _resolve_existing_py(file_path: str, hal_root: Path) -> Path | None:
    """Resolve *file_path* under *hal_root* and return the Path only if it exists
    and has a .py suffix; else return None (non-Python or missing → skip).

    Uses citation_verifier._resolve_path for resolution.
    Fail-open: any resolution exception → falls back to a direct join+resolve.
    """
    try:
        p = citation_verifier._resolve_path(file_path, hal_root)
    except Exception:
        p = (hal_root / file_path).resolve()

    if p.suffix != ".py":
        return None
    if not p.exists():
        return None
    return p


def _function_ranges(py_path: Path) -> dict[str, list[tuple[int, int]]]:
    """Return name → list[(lineno, end_lineno)] for every function in *py_path*.

    Collects FunctionDef and AsyncFunctionDef nodes via ast.walk (captures methods,
    nested functions, and async defs). Fail-open: any FileNotFoundError, OSError,
    SyntaxError, or unexpected exception → {}.
    """
    try:
        source = py_path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=str(py_path))
    except (FileNotFoundError, OSError, SyntaxError, Exception):
        return {}

    ranges: dict[str, list[tuple[int, int]]] = {}
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = node.end_lineno
            if end is None:  # ast guarantees end_lineno on 3.8+, but the type is int | None
                continue
            name = node.name
            if name not in ranges:
                ranges[name] = []
            ranges[name].append((node.lineno, end))
    return ranges


def _symbol_reference_lines(py_path: Path) -> dict[str, set[int]]:
    """Return name → set of line numbers where *name* is referenced (Load context)
    in *py_path*, as either a bare ast.Name or an ast.Attribute access.

    Collects ast.Name nodes with ctx=Load (by node.id) and ast.Attribute nodes
    with ctx=Load (by node.attr), keyed by lineno. Fail-open: any
    FileNotFoundError, OSError, SyntaxError, or unexpected exception → {}.
    """
    try:
        source = py_path.read_text(encoding="utf-8", errors="replace")
        tree = ast.parse(source, filename=str(py_path))
    except (FileNotFoundError, OSError, SyntaxError, Exception):
        return {}

    refs: dict[str, set[int]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            refs.setdefault(node.id, set()).add(node.lineno)
        elif isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
            refs.setdefault(node.attr, set()).add(node.lineno)
    return refs


def _same_line_backticked_idents(spec_text: str, offset: int) -> list[str]:
    """Return backtick-wrapped identifiers on the markdown line containing *offset*.

    Finds the line by scanning to the nearest \\n boundaries on each side of offset,
    then applies `([A-Za-z_]\\w*)` over that slice only.
    """
    # Find start of the line containing offset
    line_start = spec_text.rfind("\n", 0, offset)
    line_start = line_start + 1 if line_start != -1 else 0

    # Find end of the line containing offset
    line_end = spec_text.find("\n", offset)
    if line_end == -1:
        line_end = len(spec_text)

    line_slice = spec_text[line_start:line_end]
    return _BACKTICK_IDENT_RE.findall(line_slice)
