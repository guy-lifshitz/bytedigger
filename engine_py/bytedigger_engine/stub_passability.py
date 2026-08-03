"""stub_passability.py — deterministic AST-based stub-passability (mock-the-UUT) scanner.

Detects vacuous RED tests where the unit under test (UUT) is both imported/referenced
as the test subject AND patched/mocked away in the same file.

A RED test that mocks its own UUT degrades to asserting only mock behavior — structurally
vacuous. Detected deterministically by AST inspection; no LLM required (Principle A).

Part of 7AD3D393 — closes the enforcement gap for §1l/§1y (orchestrator-discipline only →
deterministic-gate in engine phase_5 and CLI lane).
"""
from __future__ import annotations

import ast
from dataclasses import dataclass, field


@dataclass
class StubPassabilityFinding:
    """One mock-the-UUT finding in a RED test file."""
    symbol: str        # the mocked UUT symbol (final dotted component)
    import_line: int   # line where symbol was imported/used as subject (0 if --uut only)
    patch_line: int    # line of the patch/mock call
    kind: str = "mock_uut"


@dataclass
class LintResult:
    """File-level wrapper for lint results (CLI + convenience)."""
    test_file: str
    findings: list[StubPassabilityFinding] = field(default_factory=list)
    error: str | None = None   # set on syntax/read failure

    def has_violation(self) -> bool:
        return self.error is None and bool(self.findings)


def _uut_candidates(tree: ast.AST) -> dict[str, int]:
    """Return a mapping of candidate UUT symbol names -> first import line.

    Walks the WHOLE tree (ast.walk), not just module body, to catch deferred
    imports inside function bodies (D1CF5FDF idiom).

    Only from-import form detected:
      - ``from <mod> import <name> [as <alias>]``  -> record ORIGINAL <name>
        (alias maps back to original so patching the original is still detected)

    The ``import <mod>; <mod>.<name>(...)`` call form is intentionally excluded:
    patching via module-attr is the normal infra/dep mock pattern, not a
    vacuous-UUT smell. Explicit UUT injection is handled by the ``uut`` arg.
    """
    candidates: dict[str, int] = {}

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            # from <mod> import <name> [as <alias>]
            if node.names:
                for alias in node.names:
                    original_name = alias.name  # record ORIGINAL name
                    if original_name and original_name != "*":
                        if original_name not in candidates:
                            candidates[original_name] = node.lineno

    return candidates


def _patch_targets(tree: ast.AST) -> dict[str, int]:
    """Return a mapping of patched symbol names (final component) -> line number.

    Detects both call form and decorator form:
      - patch.object(<x>, "<name>", ...)      -> 2nd positional str arg
      - patch("<a.b.NAME>") / mock.patch(...) -> last dotted component of 1st str arg
      - @patch(...) / @patch.object(...)      -> same, decorator form
    """
    targets: dict[str, int] = {}

    def _extract_from_call(call_node: ast.Call) -> str | None:
        """Extract the patched symbol name from a patch(...) or patch.object(...) call."""
        func = call_node.func

        # Determine if this is patch.object or patch (string-form)
        if isinstance(func, ast.Attribute):
            if func.attr == "object":
                # patch.object(<x>, "<name>", ...) — name is 2nd positional arg
                if len(call_node.args) >= 2:
                    arg = call_node.args[1]
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        return arg.value
            elif func.attr == "patch":
                # mock.patch("<a.b.NAME>") — last component of 1st str arg
                if call_node.args:
                    arg = call_node.args[0]
                    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                        parts = arg.value.split(".")
                        return parts[-1] if parts else None
        elif isinstance(func, ast.Name) and func.id == "patch":
            # patch("<a.b.NAME>") — last component of 1st str arg
            if call_node.args:
                arg = call_node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    parts = arg.value.split(".")
                    return parts[-1] if parts else None

        return None

    for node in ast.walk(tree):
        # Decorator form: @patch(...) or @patch.object(...)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for decorator in node.decorator_list:
                call_node: ast.Call | None = None
                if isinstance(decorator, ast.Call):
                    call_node = decorator
                if call_node is not None:
                    name = _extract_from_call(call_node)
                    if name and name not in targets:
                        targets[name] = call_node.lineno

        # Call form: patch(...) or patch.object(...) used as context manager or direct call
        elif isinstance(node, ast.Call):
            name = _extract_from_call(node)
            if name and name not in targets:
                targets[name] = node.lineno

    return targets


def scan_stub_passability(
    source: str,
    uut: list[str] | None = None,
) -> list[StubPassabilityFinding]:
    """Primary engine API — parallel to suite_safety.scan_suite_safety(source).

    ast.parse(source) (let SyntaxError propagate to caller).

    Algorithm:
      cand = _uut_candidates(tree) ∪ {name: 0 for name in (uut or [])}
      targets = _patch_targets(tree)
      for name in sorted(set(cand) & set(targets)):
          if the patch's physical source line contains '# stub-passability: allow' -> skip
          else emit a StubPassabilityFinding

    Returns a list of findings (empty = clean).
    """
    tree = ast.parse(source)  # SyntaxError propagates to caller

    cand = _uut_candidates(tree)
    # Add explicit UUT overrides (line 0 = no import line)
    for name in (uut or []):
        if name not in cand:
            cand[name] = 0

    targets = _patch_targets(tree)

    # Split source into lines for pragma checking (1-indexed)
    source_lines = source.splitlines()

    findings: list[StubPassabilityFinding] = []
    for name in sorted(set(cand) & set(targets)):
        patch_line = targets[name]
        # Check pragma on the physical source line (1-indexed -> 0-indexed)
        line_text = ""
        if 1 <= patch_line <= len(source_lines):
            line_text = source_lines[patch_line - 1]
        if "# stub-passability: allow" in line_text:
            continue
        findings.append(StubPassabilityFinding(
            symbol=name,
            import_line=cand[name],
            patch_line=patch_line,
        ))

    return findings


def lint_red_file(path: str, uut: list[str] | None = None) -> LintResult:
    """Lint a single RED test file for stub-passability violations.

    On OSError (unreadable) or SyntaxError (unparseable): returns LintResult with
    error set and findings=[].

    Otherwise: returns LintResult with findings from scan_stub_passability.
    """
    try:
        text = open(path, encoding="utf-8", errors="replace").read()
    except OSError as e:
        return LintResult(test_file=path, findings=[], error=str(e))

    try:
        findings = scan_stub_passability(text, uut)
    except SyntaxError as e:
        return LintResult(test_file=path, findings=[], error=str(e))

    return LintResult(test_file=path, findings=findings)
