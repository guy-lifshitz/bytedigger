"""Path/import closure staleness gate (hal-v2#828, flip blocker).

Two drift classes bit us during extraction:

1. Engine code referencing files OUTSIDE the package via
   Path(__file__).parents[N] (N >= 2) -- the upstream build-tree nesting does
   not exist in this repo or in a pip install, so the reference must either
   resolve through the packaged-asset fallback
   (config_provider.default_security_asset -> engine_py/security/) or be an
   explicitly allowlisted degradation with a reason.

2. Engine code importing a module that never made it into the tree
   (the reference-backends class: run.py try-imported a package that was
   missing from the extraction for a while).

Both checks extract their facts from the sources at test time -- no
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

_SHIPPED_DIRS = ("lib", "workflows", "security")


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
