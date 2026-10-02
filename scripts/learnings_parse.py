#!/usr/bin/env python3
"""learnings_parse.py -- the one parser for reviews/learnings-raw.md (bd#136).

Module API: parse(path) -> (entries, parse_errors), entries = [(category, lesson), ...].
CLI: `python3 learnings_parse.py <raw_md>` prints line 1 = parse_errors, then one
`<category>\\x1f<lesson>` line per entry. Unopenable file: message on stderr, exit 1.
"""
import re
import sys

_PATTERN = re.compile(r'^-\s+\[([^\]]+)\]\s+(?:---?|—)\s+(.+)$')


def _sanitize(raw):
    """Lowercase, runs of non-alphanumerics become one dash, edge dashes stripped."""
    return re.sub(r'[^a-z0-9]+', '-', raw.lower()).strip('-')


def parse(path):
    """Return (entries, parse_errors). Raises OSError if the file cannot be opened/read."""
    entries = []
    errors = 0
    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith(('#', '```')):
                continue  # blank / heading / code fence: not a parse error
            m = _PATTERN.match(stripped)
            if not m:
                errors += 1
                continue
            category = _sanitize(m.group(1))
            if not category:
                errors += 1
                continue
            entries.append((category, m.group(2).strip()))
    return entries, errors


def main(argv):
    if len(argv) != 2:
        sys.stderr.write("usage: learnings_parse.py <raw_md>\n")
        return 1
    try:
        entries, errors = parse(argv[1])
    except OSError as e:
        sys.stderr.write("learnings_parse: cannot read %s: %s\n" % (argv[1], e))
        return 1
    sys.stdout.reconfigure(encoding="utf-8")
    out = [str(errors)]
    out.extend("%s\x1f%s" % (c, l) for c, l in entries)
    sys.stdout.write("\n".join(out) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
