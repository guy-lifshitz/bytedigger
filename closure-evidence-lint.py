#!/usr/bin/env python3
"""closure-evidence-lint.py - CLI to require observed evidence behind closedness claims in a spec.

Usage:
    closure-evidence-lint.py --spec PATH [--json]

Exit codes:
    0  - clean
    1  - at least one finding
    2  - the spec could not be read, or a usage error

Text output (one line per finding):
    <rule_id> line <N>: <evidence>

JSON output (--json):
    {"findings": [{line, rule_id, evidence}]}

ONE rule, ONE carrier: imports
`bytedigger_engine.scripts.lib.closure_evidence_verifier`; no copy of the rule
lives here. The driver sits at the repo root beside
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

from bytedigger_engine.scripts.lib.closure_evidence_verifier import find_unbacked_closure_claims  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Lint a spec for closedness claims with no observed evidence.",
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

    findings = [
        {
            "line": text.count("\n", 0, f.offset) + 1,
            "rule_id": f.rule_id,
            "evidence": f.evidence,
        }
        for f in find_unbacked_closure_claims(text, Path(args.spec).parent)
    ]

    if args.json_output:
        print(json.dumps({"findings": findings}))
    else:
        for f in findings:
            print(f"{f['rule_id']} line {f['line']}: {f['evidence']}")

    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
