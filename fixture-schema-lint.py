#!/usr/bin/env python3
"""fixture-schema-lint.py — CLI for the GH891 fixture-schema (reference-DDL
subset) RED lint gate.

Usage:
    fixture-schema-lint.py --spec SPEC.md FILE [FILE...] [--json]

Exit codes:
    0  — clean (no violations); or spec has no Ground-Truth block (informational)
    1  — at least one violation found
    2  — spec missing/unreadable (driver error)

Text output (one line per finding):
    FIXTURE-SCHEMA-DRIFT <file>:<line> table '<table>' kind=<kind> extra=<a,b>

JSON output (--json, §1e):
    {"violations": [{test_file, table, kind, extra_columns, line}], "errors": [...]}

Part of GH891 — fixture-schema deterministic gate.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Add engine_py root (the parent of the bytedigger_engine package) to sys.path
# so the engine is importable from a source checkout. This shim lives OUTSIDE
# the distribution, so the insert is a host-side bootstrap, not a package
# path hack — bd#44 removed all 63 of those from inside the package.
_ENGINE_PY_ROOT = Path(__file__).resolve().parent / "engine_py"
sys.path.insert(0, str(_ENGINE_PY_ROOT))

from bytedigger_engine.fixture_schema import parse_reference_ddl, scan_fixture_schema  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Lint RED test files for fixture-schema (reference-DDL subset) violations.",
    )
    parser.add_argument("--spec", required=True, metavar="SPEC", help="Path to the spec markdown file.")
    parser.add_argument("files", nargs="+", metavar="FILE", help="Test files to lint.")
    parser.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Emit JSON output instead of text.",
    )

    try:
        args = parser.parse_args()
    except SystemExit:
        return 2

    spec_path = Path(args.spec)
    try:
        spec_text = spec_path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        print(f"ERROR: cannot read spec file {spec_path}: {exc}", file=sys.stderr)
        return 2

    reference = parse_reference_ddl(spec_text)
    if not reference:
        print(f"NO-GROUND-TRUTH-BLOCK {spec_path}")
        return 0

    violations = []
    errors = []

    for filepath in args.files:
        try:
            source = Path(filepath).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            errors.append({"test_file": filepath, "error": str(exc)})
            continue
        for finding in scan_fixture_schema(source, reference):
            violations.append({
                "test_file": filepath,
                "table": finding.table,
                "kind": finding.kind,
                "extra_columns": list(finding.extra_columns),
                "line": finding.line,
            })

    if args.json_output:
        print(json.dumps({"violations": violations, "errors": errors}))
    else:
        for v in violations:
            print(
                f"FIXTURE-SCHEMA-DRIFT {v['test_file']}:{v['line']}"
                f" table '{v['table']}' kind={v['kind']}"
                f" extra={','.join(v['extra_columns'])}"
            )
        for e in errors:
            print(f"ERROR {e['test_file']}: {e['error']}", file=sys.stderr)

    if errors:
        return 2
    if violations:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
