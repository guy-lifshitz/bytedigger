#!/usr/bin/env python3
"""spec-cite-lint.py - CLI to flag spec citations that name a missing file or symbol.

Usage:
    spec-cite-lint.py --spec PATH [--repo-root R] [--json]

Exit codes:
    0  - clean
    1  - at least one blocking finding
    2  - the spec could not be read, or a usage error

Text output (one line per blocking finding):
    SPEC-CITE <status> <file> <symbol>

JSON output (--json):
    {"findings": [{file, symbol, status}]}

ONE rule, ONE carrier: imports `bytedigger_engine.spec_cite` (`lint_spec_union`);
no copy of the rule lives here. --repo-root defaults to the directory of this
driver. The driver sits at the repo root because a hyphenated module inside the
package would break the import smoke.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if str(Path(__file__).resolve().parent / "engine_py") not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent / "engine_py"))

from bytedigger_engine import spec_cite  # noqa: E402


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):  # usage errors exit 2, never 0
        self.print_usage(sys.stderr)
        print(f"{self.prog}: error: {message}", file=sys.stderr)
        sys.exit(2)


def main() -> int:
    parser = _Parser(
        description="Lint a spec for citations of a missing file or symbol.",
    )
    parser.add_argument("--spec", required=True, metavar="PATH",
                        help="Path to the spec file to lint.")
    parser.add_argument("--repo-root", metavar="R", default=None,
                        help="Repo root citations resolve against (default: the driver's directory).")
    parser.add_argument("--json", action="store_true", dest="json_output",
                        help="Emit JSON output instead of text.")
    args = parser.parse_args()

    repo_root = Path(args.repo_root) if args.repo_root else Path(__file__).resolve().parent
    if not repo_root.is_dir():
        print(f"repo root is not a directory: {repo_root}", file=sys.stderr)
        return 2

    try:
        code, findings = spec_cite.lint_spec_union(Path(args.spec), repo_root)
    except (OSError, UnicodeDecodeError) as exc:
        print(f"cannot read spec {args.spec}: {exc}", file=sys.stderr)
        return 2

    if args.json_output:
        print(json.dumps({"findings": [
            {"file": f.file, "symbol": f.symbol, "status": f.status} for f in findings
        ]}))
    else:
        for f in findings:
            if f.status in spec_cite.BLOCKING_STATUSES:
                print(f"SPEC-CITE {f.status} {f.file} {f.symbol}")

    return 1 if code else 0


if __name__ == "__main__":
    sys.exit(main())
