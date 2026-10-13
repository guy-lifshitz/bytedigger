#!/usr/bin/env python3
"""empty-input-assert-lint.py - CLI to detect empty-input asserts with no live denominator.

Usage:
    empty-input-assert-lint.py FILE [FILE...] [--json]

Exit codes:
    0  - clean (no finding, including zero candidates)
    1  - at least one finding
    2  - usage error, or a file could not be read, or the scanner's identity
         check failed (wins over 1)

Text output (one line per finding):
    EMPTY-INPUT-ASSERT <file>:<line>: <form> - subject <subject!r> has no live denominator in scope

JSON output (--json):
    {"findings": [{file, line, subject, form}]}

Files whose basename is not a test name are skipped. ONE rule, ONE carrier:
imports `bytedigger_engine.empty_input_assert`; no copy of the rule or of the
test-file predicate lives here. The driver sits at the repo root beside
`forbidden-import-lint.py` because a hyphenated module inside the package would
break the import smoke.
"""
from __future__ import annotations

import argparse
import json
import sys
import warnings
from pathlib import Path

if str(Path(__file__).resolve().parent / "engine_py") not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent / "engine_py"))

from bytedigger_engine import empty_input_assert  # noqa: E402
from bytedigger_engine.empty_input_assert import identity_ok  # noqa: E402
from bytedigger_engine.precommit_lints import is_test_file  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Lint test files for empty-input asserts with no live denominator.",
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
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            summary = empty_input_assert.summarize_empty_input_asserts(text)
        findings = summary["findings"]
        if not identity_ok(summary["candidates"], summary["controlled"],
                           summary["escaped"], len(findings)):
            print(f"scanner identity check failed for {path}: candidates="
                  f"{summary['candidates']} controlled={summary['controlled']} "
                  f"escaped={summary['escaped']} findings={len(findings)}",
                  file=sys.stderr)
            errored = True
            continue
        for finding in findings:
            all_findings.append({
                "file": path, "line": finding["line"],
                "subject": finding["subject"], "form": finding["form"],
            })

    if args.json_output:
        print(json.dumps({"findings": all_findings}))
    else:
        for f in all_findings:
            print(f"EMPTY-INPUT-ASSERT {f['file']}:{f['line']}: {f['form']} - "
                  f"subject {f['subject']!r} has no live denominator in scope")

    if errored:
        return 2
    return 1 if all_findings else 0


if __name__ == "__main__":
    sys.exit(main())
