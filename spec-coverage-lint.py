#!/usr/bin/env python3
"""spec-coverage-lint.py - CLI to require an AC covering every bold operation in a spec.

Usage:
    spec-coverage-lint.py --spec PATH [--json] [--taxonomy] [--finalize]

Exit codes:
    0  - clean
    1  - at least one finding
    2  - the spec could not be read, the spec has no AC table, or a usage error

Text output (one line per finding, `<N>` is the 1-based line, `?` when none):
    UNCOVERED-OP line <N>: op=<repr of op> - <reason>
    TAXONOMY-MISSING-DISPOSITION line <N>: token=<token> - <reason>
    FINALIZE-BRANCH-UNCOVERED line <N>: group=<group> - <reason>
    FINALIZE-AC-UNANCHORED line <N>: group=<group> - <reason>
    FINDING line <N>: rule=<rule> - <reason>      (any other rule)

JSON output (--json):
    {"findings": [{op, line, reason}]}   (opt-in findings are appended to the list)

ONE rule, ONE carrier: imports `bytedigger_engine.spec_coverage`; no copy of the
rule lives here. The driver sits at the repo root beside
`one-sided-predicate-lint.py` because a hyphenated module inside the package
would break the import smoke. The pre-commit layer feeds it `--spec` only, and
only a spec that carries an AC table (`precommit_lints.feed_skip_reason`).
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


def _text_line(f: dict) -> str:
    line = f.get("line")
    where = "?" if line is None else str(line)
    reason = f.get("reason", "")
    rule = f.get("rule")
    if rule is None:
        return f"UNCOVERED-OP line {where}: op={f.get('op')!r} - {reason}"
    if rule == "spec_taxonomy_missing_disposition":
        return f"TAXONOMY-MISSING-DISPOSITION line {where}: token={f.get('token')} - {reason}"
    if rule == "spec_finalize_branch_uncovered":
        return f"FINALIZE-BRANCH-UNCOVERED line {where}: group={f.get('group')} - {reason}"
    if rule == "spec_finalize_ac_unanchored":
        return f"FINALIZE-AC-UNANCHORED line {where}: group={f.get('group')} - {reason}"
    return f"FINDING line {where}: rule={rule} - {reason}"


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
            print(_text_line(f))

    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
