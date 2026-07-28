"""Path/import closure staleness gate (hal-v2#828, flip blocker).

Three drift classes bit us during extraction:

1. Engine code referencing files OUTSIDE the package via
   Path(__file__).parents[N] (N >= 2) -- the upstream build-tree nesting does
   not exist in this repo or in a pip install, so the reference must either
   resolve through the packaged-asset fallback
   (config_provider.default_security_asset -> engine_py/security/) or be an
   explicitly allowlisted degradation with a reason.

2. Engine code importing a module that never made it into the tree
   (the reference-backends class: run.py try-imported a package that was
   missing from the extraction for a while).

3. Fixed-depth ancestor indexing -- Path(__file__).resolve().parents[N] with
   an N tuned to one tree's nesting. bd#97: phase_6_smoke.py bound
   parents[5] at import time, which IndexErrors on any checkout shallower
   than the upstream build tree and took 16 of 16 test modules with it.
   AC1 above could not see it: its regex requires quoted literal path
   segments after the index (bd#97 kept the tail in a variable), its
   \\bhal_dir\\b alternative is case-sensitive so it never matched HAL_DIR,
   and -- decisively -- AC1/AC4 skip everything under tests/. AC1 audits
   where an escape LANDS; AC5a/AC5b below audit the depth arithmetic that
   gets there.

All checks extract their facts from the sources at test time -- no
hardcoded inventory to go stale.
"""
from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

ENGINE_PY_ROOT = Path(__file__).resolve().parent.parent

# Out-of-package references that deliberately do NOT ship in the wheel.
# Key: the quoted tail of the reference as it appears in source.
# Value: why the degradation is acceptable (verified behavior, not hope).
ESCAPE_ALLOWLIST = {
    "inject-learnings.ts": "host memory-injection helper; subprocess failure lands in the graceful [memory_db_unavailable] sentinel path, env seam HAL_INJECT_LEARNINGS_TS",
    "MANIFEST.md": "read only when cwd is inside the upstream build tree (recursive self-build); unreachable from a pip install, fail-closed E_MANIFEST_MISSING if reached",
    "baseline_delta_gate.py": "gate emits baseline_delta_gate_skipped(script_missing) and continues; env seam HAL_BASELINE_DELTA_BIN",
    "security-lint.py": "legacy upstream location; packaged fallback security/security_lint.py resolves via default_security_asset, fail-closed E_SEC_LINT_UNAVAILABLE if both missing",
    "secure-codegen-rules.md": "legacy upstream location; packaged fallback via default_security_asset, fail-closed E_SECURITY_RULES_MISSING",
    "secure-codegen-fragment.md": "legacy upstream location; packaged fallback via default_security_asset, fail-closed E_SEC_FRAGMENT_MISSING",
    "devops-scan.ts": "bun/TS scanner, opt-in via org_config artifact_type; fail-closed E_DEVOPS_SCAN_UNAVAILABLE when gate on and shim/bun missing, kill switch HAL_DEVOPS_SCAN_GATE=0",
    "devops-scan-config.json": "documented default severities [CRITICAL, HIGH] when file missing; env seam HAL_DEVOPS_SCAN_CONFIG",
    "devops-scan-allowlist.txt": "missing file parses to empty allowlist (stricter, never weaker); env seam HAL_DEVOPS_SCAN_ALLOWLIST",
    "sibling-test-audit.sh": "warn-only pass; emits red_sibling_audit_skipped(script_missing) and continues; env seam HAL_SIBLING_AUDIT_BIN",
    "devops-prompt-context.ts": "standards-context helper returns '' in ALL error/skip cases by contract; opt-in via org_config artifact_type",
    "devops-detect.ts": "detection step never fails the build; every error path returns ok with artifact_type=None and data.error",
    "persist-learnings.ts": "best-effort learnings upsert; cfg persist_learnings_disabled seam, subprocess failure recorded not raised",
    "suite-boyscout-allowlist.txt": "missing file means empty allowlist (stricter, never weaker)",
    "deregister-session.ts": "best-effort session dereg; failure recorded not raised",
}

# Assets the packaged fallback must actually contain (the resolvable side of
# the allowlist rows above plus the in-package red-lint ruleset).
PACKAGED_ASSETS = [
    "security/__init__.py",
    "security/security_lint.py",
    "security/semgrep-rules.yml",
    "security/secure-codegen-rules.md",
    "security/secure-codegen-fragment.md",
    "security/gitleaks.toml",
    "security/except-pass-allowlist.txt",
    "scripts/red_lint/rules.yml",
]

# bd#10: `conformance` is a shipped package — pyproject packages.find has
# carried `conformance*` since bd#22 — but this list was never updated, so a
# legitimate `import conformance` read as the missing-module extraction class.
_SHIPPED_DIRS = ("lib", "workflows", "security", "conformance")


def _shipped_sources():
    for p in ENGINE_PY_ROOT.glob("*.py"):
        yield p
    for d in _SHIPPED_DIRS:
        yield from (ENGINE_PY_ROOT / d).rglob("*.py")


# parents[N>=2] (or a variable previously bound to it -- build_dir / hal_dir
# conventions in this codebase) followed by a chain of quoted segments.
_ESCAPE_RE = re.compile(
    r'(?:parents\[\s*([2-9])\s*\]|\bbuild_dir\b|\bhal_dir\b)'
    r'((?:\s*/\s*"[^"]+")+)'
)
_SEG_RE = re.compile(r'"([^"]+)"')


def test_ac1_every_out_of_package_path_is_packaged_or_allowlisted():
    unexplained = []
    for src in _shipped_sources():
        if "tests" in src.parts:
            continue
        text = src.read_text(encoding="utf-8")
        for m in _ESCAPE_RE.finditer(text):
            segs = _SEG_RE.findall(m.group(2))
            if not segs:
                continue
            tail = segs[-1]
            if tail in ESCAPE_ALLOWLIST:
                continue
            if (ENGINE_PY_ROOT / "security" / tail).is_file():
                continue
            if (ENGINE_PY_ROOT / Path(*segs)).exists():
                continue
            unexplained.append(f"{src.relative_to(ENGINE_PY_ROOT)}: .../{'/'.join(segs)}")
    assert not unexplained, (
        "out-of-package path reference(s) with no packaged fallback and no "
        "allowlist reason -- ship the asset under engine_py/security/ (and "
        "route through config_provider.default_security_asset) or add an "
        "ESCAPE_ALLOWLIST entry with the verified degradation:\n  "
        + "\n  ".join(unexplained)
    )


def test_ac2_packaged_assets_exist():
    missing = [a for a in PACKAGED_ASSETS if not (ENGINE_PY_ROOT / a).is_file()]
    assert not missing, f"packaged asset(s) missing from tree: {missing}"


def test_ac3_packaged_assets_are_wheel_visible():
    if sys.version_info >= (3, 11):
        import tomllib
    else:  # pragma: no cover
        import tomli as tomllib
    with (ENGINE_PY_ROOT / "pyproject.toml").open("rb") as f:
        cfg = tomllib.load(f)
    include = cfg["tool"]["setuptools"]["packages"]["find"]["include"]
    assert any(pat.startswith("security") for pat in include), include
    assert any(pat.startswith("scripts") for pat in include), include
    pkg_data = cfg["tool"]["setuptools"]["package-data"]
    assert "security" in pkg_data and "scripts.red_lint" in pkg_data, pkg_data


# ─── AC5: fixed-depth ancestor indexing (bd#97) ──────────────────────────────

# parents[0]/[1] stay inside engine_py/<subdir>/ under every layout. An index
# of 3 or more is the first that can walk out of the package root, so it is
# the first that can IndexError -- or silently resolve into an unrelated tree.
_UNSAFE_ANCESTOR_INDEX = 3

# Known evasion surface, documented rather than claimed away: list(p.parents)[N],
# a name/expression index, and getattr/eval forms are not matched. No
# module-level occurrence of any exists today (event_log.py's
# list(resolved.parents) is in-function).


class _ParentsIndexes(ast.NodeVisitor):
    """Collect `<expr>.parents[<int>]`, tagged import-time or deferred.

    Function and lambda bodies are deferred. Class bodies and `try:` bodies
    execute at import, so they get no special case -- that is what stops a
    `try/except IndexError` wrap from passing as a fix.
    """

    def __init__(self):
        self.deferred = 0
        self.hits = []

    def _visit_deferred(self, node):
        self.deferred += 1
        self.generic_visit(node)
        self.deferred -= 1

    visit_FunctionDef = _visit_deferred
    visit_AsyncFunctionDef = _visit_deferred
    visit_Lambda = _visit_deferred

    def visit_Subscript(self, node):
        value = node.value
        if (
            isinstance(value, ast.Attribute)
            and value.attr == "parents"
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, int)
        ):
            self.hits.append((node.lineno, node.slice.value, self.deferred == 0))
        self.generic_visit(node)


def _parents_indexes(path):
    visitor = _ParentsIndexes()
    visitor.visit(ast.parse(path.read_text(encoding="utf-8")))
    return visitor.hits


def _all_sources():
    """Every shipped source PLUS tests/ -- unlike _shipped_sources()."""
    yield from _shipped_sources()
    yield from (ENGINE_PY_ROOT / "tests").rglob("*.py")


def test_ac5a_no_import_time_fixed_depth_ancestor_index():
    """No module-level parents[N>=3] anywhere in the tree, tests included.

    Import-time depth arithmetic is unconditional: it fires on `import`, so a
    tree one directory shallower than the author's turns every consumer into a
    collection error.
    """
    offenders = []
    for src in _all_sources():
        for lineno, idx, import_time in _parents_indexes(src):
            if import_time and idx >= _UNSAFE_ANCESTOR_INDEX:
                offenders.append(
                    f"{src.relative_to(ENGINE_PY_ROOT)}:{lineno}: parents[{idx}]"
                )
    assert not offenders, (
        "import-time fixed-depth ancestor index (bd#97 class) -- IndexErrors, "
        "or silently resolves into an unrelated tree, depending on how deep the "
        "checkout happens to sit. Anchor on tree content via "
        "lib/tree_root.resolve_tree_root (marker / .git / clamped package "
        "root), or defer the computation into a function:\n  "
        + "\n  ".join(offenders)
    )


def test_ac5b_no_fixed_depth_ancestor_index_in_tests():
    """No parents[N>=3] at all under tests/, deferred or not.

    Stricter than AC5a on purpose. A portability guard like

        p = Path(__file__).resolve().parents[5] / "SHARED" / "config.json"
        if not p.is_file():
            pytest.skip(...)

    evaluates the index BEFORE the skip it is meant to trigger, so on a shallow
    clone the test errors instead of skipping and the guard is worthless
    (bd#97 site 3, test_GH375_tier_model_dispatch.py:663).
    """
    offenders = []
    for src in (ENGINE_PY_ROOT / "tests").rglob("*.py"):
        for lineno, idx, _ in _parents_indexes(src):
            if idx >= _UNSAFE_ANCESTOR_INDEX:
                offenders.append(
                    f"{src.relative_to(ENGINE_PY_ROOT)}:{lineno}: parents[{idx}]"
                )
    assert not offenders, (
        "fixed-depth ancestor index under tests/ -- raises before the test's "
        "own pytest.skip portability guard can fire on a shallow clone. Use "
        "lib/tree_root.resolve_tree_root instead:\n  " + "\n  ".join(offenders)
    )


# ─── AC5c: PEP-604 unions need the future import on the declared minimum ─────


def _has_future_annotations(tree):
    return any(
        isinstance(node, ast.ImportFrom)
        and node.module == "__future__"
        and any(alias.name == "annotations" for alias in node.names)
        for node in tree.body
    )


def _annotations(tree):
    """Every annotation expression in the module."""
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = node.args
            for arg in (
                list(args.args)
                + list(args.posonlyargs)
                + list(args.kwonlyargs)
                + [a for a in (args.vararg, args.kwarg) if a is not None]
            ):
                if arg.annotation is not None:
                    yield arg.annotation
            if node.returns is not None:
                yield node.returns
        elif isinstance(node, ast.AnnAssign) and node.annotation is not None:
            yield node.annotation


def _pep604_lines(tree):
    """Lines where an annotation uses `X | Y`, which 3.9 evaluates eagerly."""
    lines = set()
    for annotation in _annotations(tree):
        for node in ast.walk(annotation):
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
                lines.add(node.lineno)
    return sorted(lines)


def test_ac5c_pep604_annotations_carry_the_future_import():
    """A PEP-604 union in a signature is EVALUATED at def time, so on the
    declared minimum interpreter (pyproject requires-python >= 3.9) it raises
    `TypeError: unsupported operand type(s) for |` unless the module carries
    `from __future__ import annotations`.

    For a module inside workflows/__init__.py's import closure that is bd#97's
    blast radius exactly: the whole suite returns to collection errors, on an
    interpreter the author never ran. compileall does not catch it (annotations
    are not evaluated when byte-compiling) and neither does an import smoke test
    on a newer runner.

    Scoped to shipped sources: the wheel's import closure is what breaks
    `import workflows`. PEP-585 (`list[str]`) is valid on 3.9 and is not flagged.
    """
    offenders = []
    for src in _shipped_sources():
        if "tests" in src.parts:
            continue
        tree = ast.parse(src.read_text(encoding="utf-8"))
        if _has_future_annotations(tree):
            continue
        for lineno in _pep604_lines(tree):
            offenders.append(f"{src.relative_to(ENGINE_PY_ROOT)}:{lineno}")
    assert not offenders, (
        "PEP-604 union annotation without `from __future__ import annotations` "
        "-- TypeError at import on Python 3.9, which pyproject.toml still "
        "declares as supported. Add the future import (58 lib modules already "
        "do) or use typing.Optional/Union:\n  " + "\n  ".join(offenders)
    )


def _declared_deps():
    """Import names of everything pyproject declares (deps + all extras),
    read at test time so the set cannot go stale. Distribution names are
    normalized to import names; the few whose import name differs are mapped."""
    if sys.version_info >= (3, 11):
        import tomllib
    else:  # pragma: no cover
        import tomli as tomllib
    with (ENGINE_PY_ROOT / "pyproject.toml").open("rb") as f:
        cfg = tomllib.load(f)
    raw = list(cfg["project"].get("dependencies", []))
    for extra in cfg["project"].get("optional-dependencies", {}).values():
        raw.extend(extra)
    dist_to_import = {"pydantic-ai": "pydantic_ai", "pyyaml": "yaml"}
    names = set()
    for spec in raw:
        dist = re.split(r"[<>=!\[ ]", spec.strip(), maxsplit=1)[0]
        names.add(dist_to_import.get(dist, dist.replace("-", "_")))
    # optional integrations imported behind pip-hint ImportError guards that
    # sit in register() rather than around the import statement itself
    names |= {"anthropic", "claude_agent_sdk", "tomli"}
    return names


def _guarded_spans(tree):
    spans = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            for h in node.handlers:
                spans.append((node.lineno, max(c.lineno for c in node.body)))
                break
    return spans


def test_ac4_unguarded_imports_resolve_in_tree_or_deps():
    # Bare sibling imports (import phase_45_spec from inside workflows/) resolve
    # at runtime because callers put the package dirs on sys.path -- so every
    # shipped module stem anywhere in the tree counts as local.
    local_top = (
        {p.stem for p in ENGINE_PY_ROOT.glob("*.py")}
        | {p.stem for d in _SHIPPED_DIRS for p in (ENGINE_PY_ROOT / d).rglob("*.py")}
        | {p.parent.name for d in _SHIPPED_DIRS for p in (ENGINE_PY_ROOT / d).rglob("__init__.py")}
        | set(_SHIPPED_DIRS)
        | {"scripts"}
    )
    stdlib = set(sys.stdlib_module_names)
    declared = _declared_deps()
    broken = []
    for src in _shipped_sources():
        if "tests" in src.parts:
            continue
        tree = ast.parse(src.read_text(encoding="utf-8"))
        guarded = _guarded_spans(tree)
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name.split(".")[0] for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module.split(".")[0]]
            if not names:
                continue
            if any(lo <= node.lineno <= hi for lo, hi in guarded):
                continue
            for n in names:
                if n in stdlib or n in declared or n in local_top:
                    continue
                broken.append(f"{src.relative_to(ENGINE_PY_ROOT)}:{node.lineno}: import {n}")
    assert not broken, (
        "unguarded import(s) of modules that are neither stdlib, declared "
        "dependencies, nor present in the tree (the missing-module extraction "
        "class):\n  " + "\n  ".join(broken)
    )
