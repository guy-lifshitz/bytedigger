#!/usr/bin/env python3
"""commit_subject_lint.py — bd#79: refuse a commit subject that is not English.

Usage:
    commit_subject_lint.py COMMIT_MSG_FILE   # the commit-msg hook form
    commit_subject_lint.py --subjects A B    # CI form, subjects on the CLI

Exit codes:
    0  — every subject is Cyrillic-free
    1  — at least one subject carries Cyrillic
    2  — driver error (the message file could not be read)

The SUBJECT only, never the body. A commit that legitimately quotes the Russian
pattern it is changing must stay possible; a subject is what `git log --oneline`
and the GitHub UI show, and that is the surface this repository is English on.

Git history is not rewritten (bd#79 declares that out of scope), so subjects
already on `main` are not this check's corpus — only the commits a pull request
adds.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# Built from code points rather than written out, so this module is not the
# one file that has to appear in its own allowlist.
_CYRILLIC_RANGES = ((0x0400, 0x04FF), (0x0500, 0x052F))  # Cyrillic, Cyrillic Supplement
CYRILLIC_RE = re.compile(
    "[" + "".join(chr(lo) + "-" + chr(hi) for lo, hi in _CYRILLIC_RANGES) + "]"
)


def offending(subjects):
    return [s for s in subjects if CYRILLIC_RE.search(s)]


def main() -> int:
    parser = argparse.ArgumentParser(description="Refuse Cyrillic in a commit subject.")
    parser.add_argument("message_file", nargs="?", metavar="COMMIT_MSG_FILE")
    parser.add_argument("--subjects", nargs="*", default=None, metavar="SUBJECT")

    try:
        args = parser.parse_args()
    except SystemExit:
        return 2

    if args.subjects is not None:
        subjects = list(args.subjects)
    elif args.message_file:
        try:
            text = Path(args.message_file).read_text(encoding="utf-8")
        except OSError as exc:
            print(f"ERROR: cannot read {args.message_file}: {exc}", file=sys.stderr)
            return 2
        # The subject is the first line. A message whose first lines are
        # comments (`git commit` with no -m, then an empty message) has no
        # subject to check, and an empty subject aborts the commit anyway.
        lines = [l for l in text.splitlines() if not l.startswith("#")]
        subjects = lines[:1]
    else:
        print("ERROR: pass a COMMIT_MSG_FILE or --subjects", file=sys.stderr)
        return 2

    bad = offending(subjects)
    for subject in bad:
        print(f"CYRILLIC-SUBJECT: {subject}")
    if bad:
        print(
            "\nCommit subjects in this repository are English (bd#79). Rewrite the "
            "subject; quoting a Cyrillic token in the BODY is fine.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
