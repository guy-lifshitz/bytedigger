#!/usr/bin/env python3
"""stub-passability-lint.py - CLI for the stub-passability (mock-the-UUT) lint gate.

Usage:
    stub-passability-lint.py FILE [FILE...] [--uut NAME[,NAME]] [--json]

Exit codes:
    0  - clean (no violations, no errors)
    1  - at least one violation found
    2  - malformed args OR any file with an error (unreadable or syntax); wins over 1

Text output (one line per finding):
    STUB-PASSABLE <file>:<patch_line> mocks UUT '<symbol>' (subject at :<import_line>)
    errors are printed to stderr as: ERROR <file>: <error>

JSON output (--json):
    {"violations": [{test_file, symbol, import_line, patch_line, kind}],
     "errors": [{test_file, error}]}

ONE rule, ONE carrier: imports `bytedigger_engine.stub_passability`; no copy of
the rule lives here. The driver sits at the repo root beside
`one-sided-predicate-lint.py` because a hyphenated module inside the package
would break the import smoke.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if str(Path(__file__).resolve().parent / "engine_py") not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent / "engine_py"))

from bytedigger_engine.stub_passability import lint_red_file  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Lint test files for stub-passability (mock-the-UUT) violations.",
    )
    parser.add_argument("files", nargs="+", metavar="FILE", help="Test files to lint.")
    parser.add_argument(
        "--uut",
        metavar="NAME[,NAME]",
        default="",
        help="Comma-separated explicit UUT symbol names added to every file.",
    )
    parser.add_argument("--json", action="store_true", dest="json_output",
                        help="Emit JSON output instead of text.")

    try:
        args = parser.parse_args()
    except SystemExit:
        return 2

    explicit_uut: list[str] = [n.strip() for n in args.uut.split(",") if n.strip()]

    violations: list[dict] = []
    errors: list[dict] = []

    for filepath in args.files:
        result = lint_red_file(filepath, uut=explicit_uut if explicit_uut else None)
        if result.error is not None:
            errors.append({"test_file": filepath, "error": result.error})
        else:
            for finding in result.findings:
                violations.append({
                    "test_file": filepath,
                    "symbol": finding.symbol,
                    "import_line": finding.import_line,
                    "patch_line": finding.patch_line,
                    "kind": finding.kind,
                })

    if args.json_output:
        print(json.dumps({"violations": violations, "errors": errors}))
    else:
        for v in violations:
            print(
                f"STUB-PASSABLE {v['test_file']}:{v['patch_line']}"
                f" mocks UUT '{v['symbol']}'"
                f" (subject at :{v['import_line']})"
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
