#!/usr/bin/env python3
"""one-sided-predicate-lint.py — thin CLI for Rule P (bd#166, port of HAL GH1373).

Usage:
    one-sided-predicate-lint.py FILE [FILE...] [--json]

Exit codes (unconditional — this CLI is not enforce-gated):
    0  — clean (no one-sided negative code-exit predicate found)
    1  — at least one finding
    2  — usage error (unreadable file)

ONE rule, ONE carrier: imports
`bytedigger_engine.one_sided_predicate.scan_one_sided_predicates`. No copy of
the predicate lives here. The driver sits at the repo root beside
`cyrillic-prose-lint.py` because a hyphenated module inside the package would
break the import smoke.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_ENGINE_PY_DIR = str(Path(__file__).resolve().parent / "engine_py")
if _ENGINE_PY_DIR not in sys.path:
    sys.path.insert(0, _ENGINE_PY_DIR)

from bytedigger_engine.one_sided_predicate import scan_one_sided_predicates  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Lint TS/JS test file(s) for one-sided negative code-exit predicates (Rule P, bd#166).",
    )
    parser.add_argument("files", nargs="+", metavar="FILE",
                        help="Path(s) to the test file(s) to lint.")
    parser.add_argument("--json", action="store_true", dest="json_output",
                        help="Emit JSON output instead of text.")
    args = parser.parse_args()

    all_findings: list[dict] = []
    for path_str in args.files:
        path = Path(path_str)
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            print(f"cannot read file {path}: {exc}", file=sys.stderr)
            return 2
        for f in scan_one_sided_predicates(text):
            all_findings.append({"path": str(path), **f})

    if args.json_output:
        print(json.dumps({"findings": all_findings}))
    else:
        for f in all_findings:
            print(f"{f['path']}:{f['line']} {f['form']} subject={f['subject']}: {f['reason']}")

    return 1 if all_findings else 0


if __name__ == "__main__":
    sys.exit(main())
