#!/usr/bin/env python3
"""spec-coverage-lint.py - CLI to require an AC covering every bold operation in a spec.

Usage:
    spec-coverage-lint.py --spec PATH [--json] [--taxonomy] [--finalize]

Exit codes:
    0  - clean
    1  - at least one finding
    2  - the spec could not be read, the spec has no AC table, or a usage error

Text output (one line per finding):
    UNCOVERED-OP line <N>: op=<repr of op> - <reason>
    The opt-in findings (--taxonomy, --finalize) go through this same line format.

JSON output (--json):
    {"findings": [{op, line, reason}]}   (opt-in findings are appended to the list)

Known upstream limitation, tracked in #305: text mode indexes f['op'], which
taxonomy/finalize findings lack; use --json with those flags.

ONE rule, ONE carrier: imports `bytedigger_engine.spec_coverage`; no copy of the
rule lives here. The driver sits at the repo root beside
`one-sided-predicate-lint.py` because a hyphenated module inside the package
would break the import smoke. Dormant: not mapped in the registry, so the
pre-commit layer does not run it (tracked in #305).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if str(Path(__file__).resolve().parent / "engine_py") not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent / "engine_py"))

from bytedigger_engine.spec_coverage import (  # noqa: E402
    NoACTableError,
    check_ac_table,
    scan_finalize_coverage,
    scan_spec_coverage,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Lint a spec for operations no AC covers.",
    )
    parser.add_argument("--spec", required=True, metavar="PATH",
                        help="Path to the spec file to lint.")
    parser.add_argument("--json", action="store_true", dest="json_output",
                        help="Emit JSON output instead of text.")
    parser.add_argument("--taxonomy", action="store_true",
                        help="Also flag new error codes with no OWN/DEFER disposition.")
    parser.add_argument("--finalize", action="store_true",
                        help="Also cross-check finalize/cleanup branches against the AC table.")
    args = parser.parse_args()

    try:
        text = Path(args.spec).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        print(f"cannot read spec {args.spec}: {exc}", file=sys.stderr)
        return 2

    try:
        check_ac_table(text)
    except NoACTableError as exc:
        print(f"no AC table found: {args.spec}: {exc}", file=sys.stderr)
        return 2

    findings = scan_spec_coverage(text)

    if args.taxonomy:
        from bytedigger_engine.error_codes import ERROR_CODES  # noqa: E402
        from bytedigger_engine.spec_coverage import scan_error_taxonomy  # noqa: E402

        findings = findings + scan_error_taxonomy(text, ERROR_CODES.keys())

    if args.finalize:
        findings = findings + scan_finalize_coverage(text)

    if args.json_output:
        print(json.dumps({"findings": findings}))
    else:
        for f in findings:
            print(f"UNCOVERED-OP line {f['line']}: op={f['op']!r} - {f['reason']}")

    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
