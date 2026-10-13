#!/usr/bin/env python3
"""format-conversion-lint.py - CLI to require writer cases in a format-conversion spec.

Usage:
    format-conversion-lint.py --spec PATH [--json]

Exit codes:
    0  - clean
    1  - at least one finding
    2  - the spec could not be read, or a usage error

Text output (one line per finding):
    FORMAT-CASES line <N>: <evidence>

JSON output (--json):
    {"findings": [{line, rule_id, missing, evidence}]}

ONE rule, ONE carrier: imports `bytedigger_engine.format_conversion_cases`; no
copy of the rule lives here. The driver sits at the repo root beside
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

from bytedigger_engine.format_conversion_cases import scan_format_conversion  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Lint a spec for a missing format-conversion writer-cases section.",
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

    findings = scan_format_conversion(text)

    if args.json_output:
        print(json.dumps({"findings": findings}))
    else:
        for f in findings:
            print(f"FORMAT-CASES line {f['line']}: {f['evidence']}")

    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
