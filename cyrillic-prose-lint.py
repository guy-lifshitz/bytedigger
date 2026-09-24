#!/usr/bin/env python3
"""cyrillic-prose-lint.py — CLI for the bd#79 English-only-tree lint gate.

Usage:
    cyrillic-prose-lint.py [FILE ...] [--root PATH] [--json]

With no FILE, the whole tracked tree under --root is scanned. That is the form
CI runs: a per-staged-file check alone would never see a file nobody happens to
touch, which is how 316 characters survived three translation passes.

Exit codes:
    0  — no unlicensed Cyrillic
    1  — at least one violation
    2  — driver error (a path that could not be read)

Text output (one line per violation):
    CYRILLIC <file>:<line>:<column> <char> U+04XX

JSON output (--json):
    {"ok": bool, "violations": [{path, line, column, char, codepoint}, ...]}

Part of bd#79. Lives at the repo root, beside core-boundary-lint.py and
fixture-schema-lint.py, because the sys.path bootstrap below is a host-side
shim: bd#44 AC7 forbids one inside the package, and the registry's names are
hyphenated, which a module inside `bytedigger_engine/` may not be.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

_ENGINE_PY_ROOT = Path(__file__).resolve().parent / "engine_py"
sys.path.insert(0, str(_ENGINE_PY_ROOT))

from bytedigger_engine.cyrillic_scan import scan_paths, scan_tree  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Lint tracked text files for Cyrillic outside the declared allowlist.",
    )
    parser.add_argument("files", nargs="*", metavar="FILE",
                        help="Files to lint (default: the whole tracked tree).")
    parser.add_argument("--root", metavar="PATH", default=None,
                        help="Repository root. Defaults to the working directory when FILEs "
                             "are named, and to the directory holding this driver otherwise.")
    parser.add_argument("--json", action="store_true", dest="json_output",
                        help="Emit JSON output instead of text.")

    try:
        args = parser.parse_args()
    except SystemExit:
        return 2

    # bd#81: a named FILE is relative to the WORKING DIRECTORY, not to wherever
    # this driver happens to live. The pre-commit hook runs the driver from the
    # repository being committed to and hands it repo-relative paths; anchoring
    # those on the driver's own directory resolved them against the wrong tree
    # and exited 2 on every one of them. The tree scan keeps the driver's own
    # repository as its default, which is the only sensible corpus for it.
    root = args.root or (os.getcwd() if args.files else str(Path(__file__).resolve().parent))

    try:
        violations = (scan_paths(args.files, root) if args.files
                      else scan_tree(root))
    except OSError as exc:
        # Fail CLOSED. A lint that cannot open its input has not checked it.
        print(f"ERROR: {exc}", file=sys.stderr)
        if args.json_output:
            print(json.dumps({"ok": False, "violations": [], "errors": [str(exc)]}))
        return 2

    if args.json_output:
        print(json.dumps({
            "ok": not violations,
            "violations": [
                {"path": v.path, "line": v.line, "column": v.column,
                 "char": v.char, "codepoint": "U+%04X" % ord(v.char)}
                for v in violations
            ],
        }))
    else:
        for v in violations:
            print("CYRILLIC %s:%d:%d %s U+%04X" % (v.path, v.line, v.column, v.char, ord(v.char)))
        if violations:
            files = len({v.path for v in violations})
            print(
                "\n%d unlicensed Cyrillic character(s) in %d file(s). Translate the "
                "prose, or add the file to bytedigger_engine/cyrillic_scan.py::ALLOWLIST "
                "with a reason and an exact character budget." % (len(violations), files),
                file=sys.stderr,
            )

    return 1 if violations else 0


if __name__ == "__main__":
    sys.exit(main())
