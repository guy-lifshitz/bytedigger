"""core_boundary.py — AST-based core/host boundary lint library.

D58780A2 / E0 boundary enforcer for the OSS core/host decoupling plan.

Pure functions; no I/O at import time. Public surface:
  - load_manifest(path) -> dict
  - validate_manifest_schema(manifest) -> list[str]
  - scan_module(src_path, forbidden) -> list[Violation]
  - lint_manifest(manifest_path, root) -> Result
  - lint_packaging(manifest_path, root, pyproject_path) -> Result

Exit semantics (enforced by CLI, mirrored here in Result.ok):
  ok=True, errors==[], violations==[]  -> exit 0
  violations present, no errors        -> exit 1
  any errors                           -> exit 2 (fail-CLOSED, #221 lesson)
"""
from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# ─── Exceptions ───────────────────────────────────────────────────────────────

class ManifestError(Exception):
    """Raised when the manifest is missing or contains malformed JSON."""


class ModuleScanError(Exception):
    """Raised when a module file is missing or has a SyntaxError."""


# ─── Data classes ─────────────────────────────────────────────────────────────

@dataclass
class Violation:
    """A single boundary violation found in a core module."""
    module: str
    line: int
    kind: str   # one of: path_literal, string_ref, env_read, host_import
    detail: str


@dataclass
class Result:
    """Aggregate result of linting all manifested modules."""
    ok: bool
    violations: list[Violation] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


# ─── Manifest loading ─────────────────────────────────────────────────────────

def load_manifest(path: str) -> dict:
    """Read and parse the manifest JSON file.

    Raises ManifestError if the file is missing or contains malformed JSON.
    """
    try:
        with open(path, "r", encoding="utf-8") as fh:
            text = fh.read()
    except FileNotFoundError as exc:
        raise ManifestError(f"Manifest file not found: {path}") from exc
    except OSError as exc:
        raise ManifestError(f"Cannot read manifest: {path}: {exc}") from exc

    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ManifestError(f"Malformed JSON in manifest {path}: {exc}") from exc


# ─── Manifest schema validation (fail-CLOSED, GH#354) ────────────────────────

_REQUIRED_FORBIDDEN_SUBKEYS = ("path_literals", "string_refs", "env_prefixes", "import_denylist")


def validate_manifest_schema(manifest: dict) -> list[str]:
    """Validate the manifest shape. Pure, no I/O.

    Returns a list of error strings (empty list == valid). A missing/wrong-typed/
    empty `core_modules` or `forbidden` (or any of its 4 required sub-keys) is a
    schema error, not a silent "scan nothing" default (#221/#354 fail-open lesson).
    """
    errors: list[str] = []

    core_modules = manifest.get("core_modules")
    if not isinstance(core_modules, list) or not core_modules:
        errors.append("manifest schema: core_modules missing or empty")

    forbidden = manifest.get("forbidden")
    if not isinstance(forbidden, dict) or not forbidden:
        errors.append("manifest schema: forbidden missing or empty")
        return errors

    for key in _REQUIRED_FORBIDDEN_SUBKEYS:
        value = forbidden.get(key)
        if not isinstance(value, list) or not value:
            errors.append(f"manifest schema: forbidden.{key} missing or empty")

    if "host_modules" in manifest and not isinstance(manifest["host_modules"], list):
        errors.append("manifest schema: host_modules must be a list")

    return errors


# ─── Module scanning ──────────────────────────────────────────────────────────

def _top_level_package(dotted_name: str) -> str:
    """Return the first segment of a dotted module name."""
    return dotted_name.split(".")[0] if dotted_name else dotted_name


def _extract_string_arg(node: ast.expr | None) -> str | None:
    """Return the string value of a Constant node, or None."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


_PRAGMA_MARKER = "# core-boundary: allow"


def _is_pragma_suppressed(source_lines: list[str], lineno: int) -> bool:
    """A source line carrying `# core-boundary: allow <reason>` suppresses ALL
    violation kinds attributed to that line (Amendment B, §2.4). A bare pragma
    with no non-space reason text after "allow" does NOT suppress.
    """
    if lineno < 1 or lineno > len(source_lines):
        return False
    line = source_lines[lineno - 1]
    idx = line.find(_PRAGMA_MARKER)
    if idx == -1:
        return False
    rest = line[idx + len(_PRAGMA_MARKER):]
    return rest.strip() != ""


def _segment_anchored_patterns(path_literals: list[str]) -> list[tuple[str, re.Pattern]]:
    """Precompile a segment-boundary regex for each trailing-slash path_literals entry.

    ".claude/" also anchors on ".claude" at a path-segment boundary (start/end of
    string, or adjacent to "/" or "\\") — catches os.path.join(HOME, ".claude") and
    HOME + "/.claude" without over-matching lookalikes like ".claudette".
    """
    patterns: list[tuple[str, re.Pattern]] = []
    for entry in path_literals:
        if entry.endswith("/"):
            stripped = entry[:-1]
            if stripped:
                pattern = re.compile(r"(?:^|[/\\])" + re.escape(stripped) + r"(?:$|[/\\])")
                patterns.append((entry, pattern))
    return patterns


_DOCSTRING_OWNERS = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


def _docstring_node_ids(tree: ast.AST) -> set[int]:
    """Return `id()` of every str Constant that is a docstring (GH1111 / D1).

    A docstring is `body[0].value` where `body[0]` is an `ast.Expr` wrapping a
    str `Constant`, and the owning node is a Module/ClassDef/FunctionDef/
    AsyncFunctionDef — NOT any node that merely has a `.body` (if/for/while/
    try/with bodies keep their string constants scanned). Keyed on `id()`, not
    `lineno`, so an exemption cannot leak to another node sharing a line.

    The caller MUST keep `tree` alive while using the returned set, since `id()`
    identity is only meaningful for live objects.
    """
    ids: set[int] = set()
    for owner in ast.walk(tree):
        if not isinstance(owner, _DOCSTRING_OWNERS):
            continue
        body = getattr(owner, "body", None)
        if not body:
            continue
        first = body[0]
        if (
            isinstance(first, ast.Expr)
            and isinstance(first.value, ast.Constant)
            and isinstance(first.value.value, str)
        ):
            ids.add(id(first.value))
    return ids


def scan_module(src_path: str, forbidden: dict) -> list[Violation]:
    """AST-walk one source file and return all boundary violations.

    Raises ModuleScanError on missing file or SyntaxError.

    Detection rules (all AST-based — comments are immune):
      host_import  — import / from import whose top-level package is in import_denylist
      env_read     — os.getenv("K"), os.environ.get("K"), os.environ["K"]
                     where K starts with any env_prefixes entry
      path_literal — any str Constant whose value contains any path_literals substring,
                     or matches a trailing-slash entry at a path-segment boundary
      string_ref   — any str Constant whose value contains any string_refs substring

    Docstrings (module/class/function/async-function) are exempt from
    path_literal and string_ref (GH1111 / D1) — documentation about the
    boundary cannot couple the module to a host at runtime. host_import and
    env_read are unaffected.
    """
    import_denylist: list[str] = forbidden.get("import_denylist", [])
    env_prefixes: list[str] = forbidden.get("env_prefixes", [])
    path_literals: list[str] = forbidden.get("path_literals", [])
    string_refs: list[str] = forbidden.get("string_refs", [])

    try:
        source = Path(src_path).read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ModuleScanError(f"Module file not found: {src_path}") from exc
    except OSError as exc:
        raise ModuleScanError(f"Cannot read module: {src_path}: {exc}") from exc

    try:
        tree = ast.parse(source, filename=src_path)
    except SyntaxError as exc:
        raise ModuleScanError(f"SyntaxError in {src_path}: {exc}") from exc

    violations: list[Violation] = []
    module_name = src_path  # callers may pass full path or bare name
    anchored_patterns = _segment_anchored_patterns(path_literals)
    source_lines = source.splitlines()
    docstring_ids = _docstring_node_ids(tree)

    for node in ast.walk(tree):
        # ── host_import ──────────────────────────────────────────────────────
        if isinstance(node, ast.Import):
            for alias in node.names:
                pkg = _top_level_package(alias.name)
                if pkg in import_denylist:
                    violations.append(Violation(
                        module=module_name,
                        line=node.lineno,
                        kind="host_import",
                        detail=pkg,
                    ))

        elif isinstance(node, ast.ImportFrom):
            raw = node.module or ""
            pkg = _top_level_package(raw)
            if pkg in import_denylist:
                violations.append(Violation(
                    module=module_name,
                    line=node.lineno,
                    kind="host_import",
                    detail=pkg,
                ))

        # ── env_read ─────────────────────────────────────────────────────────
        elif isinstance(node, ast.Call):
            # os.getenv("K") or os.environ.get("K")
            func = node.func
            is_getenv = (
                isinstance(func, ast.Attribute)
                and func.attr == "getenv"
                and isinstance(func.value, ast.Name)
                and func.value.id == "os"
            )
            is_environ_get = (
                isinstance(func, ast.Attribute)
                and func.attr == "get"
                and isinstance(func.value, ast.Attribute)
                and func.value.attr == "environ"
                and isinstance(func.value.value, ast.Name)
                and func.value.value.id == "os"
            )
            if (is_getenv or is_environ_get) and node.args:
                key = _extract_string_arg(node.args[0])
                if key is not None:
                    for prefix in env_prefixes:
                        if key.startswith(prefix):
                            violations.append(Violation(
                                module=module_name,
                                line=node.lineno,
                                kind="env_read",
                                detail=key,
                            ))
                            break

        elif isinstance(node, ast.Subscript):
            # os.environ["K"]
            val = node.value
            is_environ_sub = (
                isinstance(val, ast.Attribute)
                and val.attr == "environ"
                and isinstance(val.value, ast.Name)
                and val.value.id == "os"
            )
            if is_environ_sub:
                # In Python 3.9+ the slice is direct; pre-3.9 it's wrapped in ast.Index
                slice_node = node.slice
                if isinstance(slice_node, ast.Index):  # type: ignore[attr-defined]
                    slice_node = slice_node.value  # type: ignore[attr-defined]
                key = _extract_string_arg(slice_node)
                if key is not None:
                    for prefix in env_prefixes:
                        if key.startswith(prefix):
                            violations.append(Violation(
                                module=module_name,
                                line=node.lineno,
                                kind="env_read",
                                detail=key,
                            ))
                            break

        # ── path_literal and string_ref ──────────────────────────────────────
        if (
            isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstring_ids
        ):
            s = node.value
            flagged_path_literal = False
            for substr in path_literals:
                if substr in s:
                    violations.append(Violation(
                        module=module_name,
                        line=node.lineno,
                        kind="path_literal",
                        detail=substr,
                    ))
                    flagged_path_literal = True
                    break  # one path_literal violation per constant

            if not flagged_path_literal:
                for entry, pattern in anchored_patterns:
                    if pattern.search(s):
                        violations.append(Violation(
                            module=module_name,
                            line=node.lineno,
                            kind="path_literal",
                            detail=entry,
                        ))
                        break  # one path_literal violation per constant

            for substr in string_refs:
                if substr in s:
                    violations.append(Violation(
                        module=module_name,
                        line=node.lineno,
                        kind="string_ref",
                        detail=substr,
                    ))
                    break  # one string_ref violation per constant

    return [v for v in violations if not _is_pragma_suppressed(source_lines, v.line)]


# ─── Manifest-level lint (the UUT) ────────────────────────────────────────────

def lint_manifest(manifest_path: str, root: str) -> Result:
    """Load manifest and scan every listed core module under root.

    Returns Result with:
      ok=True   iff no violations AND no errors.
      ok=False  on any violation OR any error (fail-CLOSED per #221 lesson).
    """
    try:
        manifest = load_manifest(manifest_path)
    except ManifestError as exc:
        return Result(ok=False, violations=[], errors=[str(exc)])

    schema_errors = validate_manifest_schema(manifest)
    if schema_errors:
        return Result(ok=False, violations=[], errors=schema_errors)

    forbidden: dict = manifest.get("forbidden", {})
    core_modules: list[str] = manifest.get("core_modules", [])

    all_violations: list[Violation] = []
    all_errors: list[str] = []

    root_path = Path(root)
    for module_name in core_modules:
        module_path = root_path / module_name
        try:
            violations = scan_module(str(module_path), forbidden)
            all_violations.extend(violations)
        except ModuleScanError as exc:
            all_errors.append(str(exc))

    ok = (not all_violations) and (not all_errors)
    return Result(ok=ok, violations=all_violations, errors=all_errors)


# ─── Packaging lint (GH1125) ──────────────────────────────────────────────────

def _load_py_modules(pyproject_path: str) -> list[str]:
    """Read `[tool.setuptools] py-modules` out of a pyproject.toml.

    Fails CLOSED (ManifestError) on a missing/unreadable/malformed file, on an
    absent [tool.setuptools] table, on an absent py-modules key, and when
    tomllib itself is unavailable — never "no violations".
    """
    try:
        import tomllib
    except ImportError as exc:  # py<3.11: no TOML parser -> cannot decide
        raise ManifestError(
            f"tomllib unavailable; cannot parse {pyproject_path}: {exc}"
        ) from exc

    try:
        with open(pyproject_path, "rb") as fh:
            data = tomllib.load(fh)
    except FileNotFoundError as exc:
        raise ManifestError(f"pyproject.toml not found: {pyproject_path}") from exc
    except OSError as exc:
        raise ManifestError(f"Cannot read pyproject.toml {pyproject_path}: {exc}") from exc
    except tomllib.TOMLDecodeError as exc:
        raise ManifestError(f"Malformed TOML in {pyproject_path}: {exc}") from exc

    setuptools_table = data.get("tool", {}).get("setuptools")
    if not isinstance(setuptools_table, dict):
        raise ManifestError(f"No [tool.setuptools] table in {pyproject_path}")

    modules = setuptools_table.get("py-modules")
    if not isinstance(modules, list):
        raise ManifestError(
            f"No [tool.setuptools] py-modules list in {pyproject_path}"
        )
    return [str(m) for m in modules]


def expected_py_modules(manifest: dict[str, Any], root: str | Path) -> set[str]:
    """The Rule-R expected `py-modules` set: top-level members of the transitive
    closure, minus host_modules, stems only.

    The host_modules subtraction is load-bearing: transitive_closure() is
    AST-based and blind to try/except guards, so it still reaches a host module
    imported (guarded) by a core one — run.py -> hal_config_provider (GH1125).
    Same semantics as the driver's --list-closure branch.
    """
    host_modules: set[str] = set(manifest.get("host_modules") or [])
    return {
        m[:-3]
        for m in transitive_closure(manifest, root)
        if m not in host_modules and "/" not in m and m.endswith(".py")
    }


def lint_packaging(manifest_path: str, root: str, pyproject_path: str) -> Result:
    """Rule R (GH1125): `py-modules` == the host-subtracted top-level closure.

    Exact set equality, both directions — an `extra` entry is a host/tooling
    module leaking into the public wheel; a `missing` entry is a core module the
    wheel would fail to import. Manifest problems land in Result.errors (as the
    other lint modes do); pyproject problems raise ManifestError (fail CLOSED).
    """
    try:
        manifest = load_manifest(manifest_path)
    except ManifestError as exc:
        return Result(ok=False, violations=[], errors=[str(exc)])

    schema_errors = validate_manifest_schema(manifest)
    if schema_errors:
        return Result(ok=False, violations=[], errors=schema_errors)

    declared = set(_load_py_modules(pyproject_path))
    expected = expected_py_modules(manifest, root)

    violations: list[Violation] = []
    for module in sorted(declared - expected):
        violations.append(Violation(
            module=module,
            line=0,
            kind="packaging_extra",
            detail="extra: declared in py-modules but outside the core closure",
        ))
    for module in sorted(expected - declared):
        violations.append(Violation(
            module=module,
            line=0,
            kind="packaging_missing",
            detail="missing: in the core closure but not declared in py-modules",
        ))

    return Result(ok=not violations, violations=violations, errors=[])


# ─── Transitive closure (GH444) ────────────────────────────────────────────────

_CLOSURE_FORBIDDEN_KINDS = ("path_literals", "string_refs", "import_denylist")


def _module_file_relpath(root_path: Path, parts: list[str]) -> str | None:
    """Given dotted-name segments, return the repo-relative module path
    (posix-style, '/'-joined) if it resolves under root_path as either
    `<parts>.py` or `<parts>/__init__.py`. None if neither exists.
    """
    if not parts:
        return None
    rel = "/".join(parts)
    py_path = rel + ".py"
    if (root_path / py_path).is_file():
        return py_path
    init_path = rel + "/__init__.py"
    if (root_path / init_path).is_file():
        return init_path
    return None


def _resolve_absolute(root_path: Path, dotted: str) -> str | None:
    """Resolve an absolute dotted import name to a repo-relative module path.

    Also tries the `workflows/` sys.path alias for bare (single-segment)
    names, since run.py inserts `workflows/` onto sys.path.
    """
    if not dotted:
        return None
    parts = dotted.split(".")
    hit = _module_file_relpath(root_path, parts)
    if hit is not None:
        return hit
    if len(parts) == 1:
        return _module_file_relpath(root_path, ["workflows"] + parts)
    return None


def transitive_closure(manifest: dict, root: str | Path) -> list[str]:
    """Return the sorted union of repo-relative module paths reachable by
    import from `manifest["core_modules"]`, AST-based. Includes the manifest
    modules themselves. Resolves absolute imports, relative imports (any
    level), bare-name imports resolving under `workflows/`, and
    `from pkg import name` where `pkg/name.py` exists. Missing/unparsable
    files are skipped for walk purposes (pure, no raising).
    """
    root_path = Path(root)
    core_modules: list[str] = manifest.get("core_modules", [])

    visited: set[str] = set()
    queue: list[str] = list(core_modules)

    while queue:
        mod = queue.pop()
        if mod in visited:
            continue
        visited.add(mod)

        mod_path = root_path / mod
        try:
            source = mod_path.read_text(encoding="utf-8")
            tree = ast.parse(source, filename=str(mod_path))
        except (OSError, SyntaxError):
            continue

        mod_dir_parts = list(Path(mod).parent.parts)
        if mod_dir_parts == ["."]:
            mod_dir_parts = []

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    hit = _resolve_absolute(root_path, alias.name)
                    if hit is not None:
                        queue.append(hit)

            elif isinstance(node, ast.ImportFrom):
                if node.level and node.level > 0:
                    # Relative import: level 1 = current package dir,
                    # level N = go up (N-1) directories from there.
                    base_parts = list(mod_dir_parts)
                    up = node.level - 1
                    if up:
                        base_parts = base_parts[:-up] if up <= len(base_parts) else []
                    from_module_parts = node.module.split(".") if node.module else []
                    pkg_parts = base_parts + from_module_parts

                    hit = _module_file_relpath(root_path, pkg_parts)
                    if hit is not None:
                        queue.append(hit)

                    for alias in node.names:
                        sub_hit = _module_file_relpath(root_path, pkg_parts + [alias.name])
                        if sub_hit is not None:
                            queue.append(sub_hit)
                else:
                    dotted = node.module or ""
                    hit = _resolve_absolute(root_path, dotted)
                    if hit is not None:
                        queue.append(hit)

                    dotted_parts = dotted.split(".") if dotted else []
                    for alias in node.names:
                        sub_hit = _module_file_relpath(root_path, dotted_parts + [alias.name])
                        if sub_hit is not None:
                            queue.append(sub_hit)
                        elif len(dotted_parts) == 1:
                            sub_hit2 = _module_file_relpath(
                                root_path, ["workflows"] + dotted_parts + [alias.name]
                            )
                            if sub_hit2 is not None:
                                queue.append(sub_hit2)

    return sorted(visited)


def lint_closure(manifest_path: str, root: str) -> Result:
    """Closure-scope lint: transitive_closure(manifest) minus host_modules,
    scanned with `forbidden` filtered to {path_literals, string_refs,
    import_denylist} (no env_prefixes — env_read is out of scope, #446).
    Same Result semantics as lint_manifest (fail-CLOSED).
    """
    try:
        manifest = load_manifest(manifest_path)
    except ManifestError as exc:
        return Result(ok=False, violations=[], errors=[str(exc)])

    schema_errors = validate_manifest_schema(manifest)
    if schema_errors:
        return Result(ok=False, violations=[], errors=schema_errors)

    forbidden: dict = manifest.get("forbidden", {})
    closure_forbidden = {k: v for k, v in forbidden.items() if k in _CLOSURE_FORBIDDEN_KINDS}

    host_modules: set[str] = set(manifest.get("host_modules") or [])
    closure_modules = [m for m in transitive_closure(manifest, root) if m not in host_modules]

    all_violations: list[Violation] = []
    all_errors: list[str] = []

    root_path = Path(root)
    for module_name in closure_modules:
        module_path = root_path / module_name
        try:
            violations = scan_module(str(module_path), closure_forbidden)
            all_violations.extend(violations)
        except ModuleScanError as exc:
            all_errors.append(str(exc))

    ok = (not all_violations) and (not all_errors)
    return Result(ok=ok, violations=all_violations, errors=all_errors)
