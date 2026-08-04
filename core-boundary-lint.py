#!/usr/bin/env python3
"""core-boundary-lint.py — CLI for the E0 core/host boundary lint gate.

Usage:
    core-boundary-lint.py [--manifest PATH] [--root PATH] [--json]
                          [--validate-only] [--closure] [--list-closure]
                          [--packaging [--pyproject PATH]]

Exit codes:
    0  — all manifested modules clean (ok=True, no violations, no errors)
    1  — >=1 violation found across manifested modules
    2  — driver error (manifest missing/malformed, module missing, SyntaxError,
         or --validate-only schema failure)

Text output (one line per violation):
    BOUNDARY-VIOLATION <module>:<line> <kind> <detail>

JSON output (--json, §1e):
    {"ok": bool, "violations": [{module, line, kind, detail}, ...], "errors": [...]}

--validate-only: load the manifest and run schema validation ONLY (no module
scans). Exit 0 if the manifest schema is valid; exit 2 with each schema error
line on stderr (or {"errors": [...]} with --json) otherwise. Used by
core-boundary-precommit.sh (GATE-1d) to self-protect the manifest itself.

--list-closure: print the transitive-closure module set (the same list --closure
scans: transitive_closure minus host_modules), one root-relative posix path per
line, or {"modules": [...]} with --json. Exit 0 on success, 2 on manifest/schema
error; never 1. Used by core-boundary-precommit.sh as the staged-file trigger set.

--packaging: lint `[tool.setuptools] py-modules` of --pyproject PATH (default
<root>/pyproject.toml) against the host-subtracted transitive closure — exact
set equality, both directions (Rule R, GH1125). Exit 0 clean, 1 on a differing
module, 2 when the pyproject.toml is unreadable/malformed or carries no
py-modules key (fail CLOSED — the ManifestError is caught, never a traceback).

Part of D58780A2 — E0 core/host decoupling boundary enforcer.
Entry-point holds imports + call-sites only (§1f).
"""
from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from pathlib import Path

# Add engine_py root to sys.path so core_boundary is importable.
_ENGINE_PY_ROOT = Path(__file__).resolve().parent / "engine_py"
sys.path.insert(0, str(_ENGINE_PY_ROOT))

from bytedigger_engine.core_boundary import (  # noqa: E402
    ManifestError,
    lint_closure,
    lint_manifest,
    lint_packaging,
    load_manifest,
    transitive_closure,
    validate_manifest_schema,
)

# Default manifest path: core_manifest.json at the engine_py root.
_DEFAULT_MANIFEST = str(_ENGINE_PY_ROOT / "core_manifest.json")
# bd#44: manifest paths are relative to the PACKAGE root, not engine_py/.
# `.github/workflows/ci.yml` ("manifest parity") resolves them the same way,
# so the default must be the package dir — otherwise every module path is
# unresolvable and the gate fails closed unless --root is passed by hand.
_DEFAULT_ROOT = str(_ENGINE_PY_ROOT / "bytedigger_engine")


def _run_packaging_mode(args: argparse.Namespace) -> int:
    """--packaging: Rule-R two-way check. Returns the driver exit code.

    lint_packaging raises ManifestError on an unreadable/malformed/py-modules-less
    pyproject.toml; main() has no global try/except, so an escaping exception
    would exit 1 (a traceback that reads like a real violation). It is caught
    here and mapped to the documented driver-error code 2, matching
    --validate-only (§1n). 0 clean / 1 violation / 2 driver error.
    """
    pyproject = args.pyproject or str(Path(args.root) / "pyproject.toml")
    try:
        result = lint_packaging(args.manifest, args.root, pyproject)
    except ManifestError as exc:
        if args.json_output:
            print(json.dumps({"errors": [str(exc)]}))
        else:
            print(f"ERROR {exc}", file=sys.stderr)
        return 2

    if args.json_output:
        print(json.dumps({
            "ok": result.ok,
            "violations": [dataclasses.asdict(v) for v in result.violations],
            "errors": result.errors,
        }))
    else:
        for v in result.violations:
            print(f"PACKAGING-VIOLATION {v.module} {v.kind} {v.detail}")
        for err in result.errors:
            print(f"ERROR {err}", file=sys.stderr)

    if result.errors:
        return 2
    if result.violations:
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Lint engine_py core modules for host-coupling boundary violations.",
    )
    parser.add_argument(
        "--manifest",
        metavar="PATH",
        default=_DEFAULT_MANIFEST,
        help="Path to the core_manifest.json (default: engine_py/core_manifest.json).",
    )
    parser.add_argument(
        "--root",
        metavar="PATH",
        default=_DEFAULT_ROOT,
        help="Root directory for resolving module paths (default: engine_py/).",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit JSON output instead of text.",
    )
    parser.add_argument(
        "--validate-only",
        action="store_true",
        dest="validate_only",
        help="Only validate the manifest schema (no module scans).",
    )
    parser.add_argument(
        "--closure",
        action="store_true",
        dest="closure",
        help="Scan the transitive import closure of core_modules (minus host_modules) instead of just the manifested modules.",
    )
    parser.add_argument(
        "--packaging",
        action="store_true",
        dest="packaging",
        help="Lint pyproject.toml [tool.setuptools] py-modules against the "
             "host-subtracted transitive closure (Rule R, GH1125).",
    )
    parser.add_argument(
        "--pyproject",
        metavar="PATH",
        default=None,
        help="pyproject.toml to lint with --packaging (default: <root>/pyproject.toml).",
    )
    parser.add_argument(
        "--list-closure",
        action="store_true",
        dest="list_closure",
        help="Print the transitive-closure module set (root-relative, one per line; "
             "with --json: {\"modules\": [...]}) instead of scanning. Never exits 1.",
    )

    try:
        args = parser.parse_args()
    except SystemExit:
        return 2

    if args.validate_only:
        try:
            manifest = load_manifest(args.manifest)
        except ManifestError as exc:
            if args.json_output:
                print(json.dumps({"errors": [str(exc)]}))
            else:
                print(f"ERROR {exc}", file=sys.stderr)
            return 2

        schema_errors = validate_manifest_schema(manifest)
        if schema_errors:
            if args.json_output:
                print(json.dumps({"errors": schema_errors}))
            else:
                for err in schema_errors:
                    print(f"ERROR {err}", file=sys.stderr)
            return 2
        return 0

    # --list-closure: enumeration mode. Owns exit 0 (success) and 2 (driver
    # error); never returns 1 — it reports no violations (§1n). Checked after
    # --validate-only (which wins) and ahead of the scan modes, so it returns
    # before the exit-code mapping below. `--closure` is inert once set.
    if args.list_closure:
        try:
            manifest = load_manifest(args.manifest)
        except ManifestError as exc:
            if args.json_output:
                print(json.dumps({"errors": [str(exc)]}))
            else:
                print(f"ERROR {exc}", file=sys.stderr)
            return 2

        schema_errors = validate_manifest_schema(manifest)
        if schema_errors:
            if args.json_output:
                print(json.dumps({"errors": schema_errors}))
            else:
                for err in schema_errors:
                    print(f"ERROR {err}", file=sys.stderr)
            return 2

        host_modules = set(manifest.get("host_modules") or [])
        modules = [m for m in transitive_closure(manifest, args.root) if m not in host_modules]

        if args.json_output:
            print(json.dumps({"modules": modules}))
        else:
            for mod in modules:
                print(mod)
        return 0

    # --packaging: Rule R (GH1125). Body in _run_packaging_mode (§1f/§1aa).
    if args.packaging:
        return _run_packaging_mode(args)

    result = lint_closure(args.manifest, args.root) if args.closure else lint_manifest(args.manifest, args.root)

    if args.json_output:
        payload = {
            "ok": result.ok,
            "violations": [dataclasses.asdict(v) for v in result.violations],
            "errors": result.errors,
        }
        print(json.dumps(payload))
    else:
        for v in result.violations:
            print(f"BOUNDARY-VIOLATION {v.module}:{v.line} {v.kind} {v.detail}")
        for err in result.errors:
            print(f"ERROR {err}", file=sys.stderr)

    # Exit-code mapping (§2.3 fail-CLOSED semantics):
    if result.errors:
        return 2
    if result.violations:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
