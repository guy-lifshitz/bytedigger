#!/usr/bin/env python3
"""forbidden-import-lint.py - CLI to detect forbidden dynamic-import patterns in test files.

Usage:
    forbidden-import-lint.py FILE [FILE...] [--json]

Exit codes:
    0  - clean (no forbidden pattern found)
    1  - at least one forbidden pattern found
    2  - at least one file could not be read (wins over 1)

Text output (one line per finding):
    FORBIDDEN-IMPORT <file>:<line>: <pattern> - <text>

JSON output (--json):
    {"findings": [{file, line, pattern, text}]}

Files whose basename is not a test name are skipped. ONE rule, ONE carrier:
imports `bytedigger_engine.forbidden_import`; no copy of the rule lives here.
The driver sits at the repo root beside `one-sided-predicate-lint.py` because a
hyphenated module inside the package would break the import smoke.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if str(Path(__file__).resolve().parent / "engine_py") not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent / "engine_py"))

from bytedigger_engine.forbidden_import import is_test_file, scan_forbidden_dynamic_import  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Lint test files for forbidden dynamic-import patterns.",
    )
    parser.add_argument("files", nargs="+", metavar="FILE",
                        help="Path(s) to the file(s) to lint.")
    parser.add_argument("--json", action="store_true", dest="json_output",
                        help="Emit JSON output instead of text.")
    args = parser.parse_args()

    all_findings: list[dict] = []
    errored = False

    for path in args.files:
        if not is_test_file(path):
            continue
        try:
            text = Path(path).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            print(f"cannot read file {path}: {exc}", file=sys.stderr)
            errored = True
            continue
        for finding in scan_forbidden_dynamic_import(text):
            finding["file"] = path
            all_findings.append(finding)

    if args.json_output:
        print(json.dumps({"findings": all_findings}))
    else:
        for f in all_findings:
            print(f"FORBIDDEN-IMPORT {f['file']}:{f['line']}: {f['pattern']} - {f['text']}")

    if errored:
        return 2
    return 1 if all_findings else 0


if __name__ == "__main__":
    sys.exit(main())
