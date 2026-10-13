#!/usr/bin/env python3
"""scope-inverse-lint.py - CLI to require a 'Files NOT in scope' block in a spec.

Usage:
    scope-inverse-lint.py --spec PATH [--json]

Exit codes:
    0  - clean
    1  - at least one finding
    2  - the spec could not be read, or a usage error

Text output (one line per finding):
    SCOPE-INVERSE-MISSING line <N>: <reason>

JSON output (--json):
    {"findings": [{line, reason}]}

ONE rule, ONE carrier: imports `bytedigger_engine.scope_inverse`; no copy of the
rule lives here. The driver sits at the repo root beside
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

from bytedigger_engine.scope_inverse import scan_scope_inverse  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Lint a spec for a missing 'Files NOT in scope' block.",
    )
    parser.add_argument("--spec", required=True, metavar="PATH",
                        help="Path to the spec file to lint.")
    parser.add_argument("--json", action="store_true", dest="json_output",
                        help="Emit JSON output instead of text.")
    args = parser.parse_args()

    try:
        text = Path(args.spec).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(f"cannot read spec {args.spec}: {exc}", file=sys.stderr)
        return 2

    findings = scan_scope_inverse(text)

    if args.json_output:
        print(json.dumps({"findings": findings}))
    else:
        for f in findings:
            print(f"SCOPE-INVERSE-MISSING line {f['line']}: {f['reason']}")

    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
