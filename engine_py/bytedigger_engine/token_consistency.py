"""token_consistency.py — f0753f5e token-consistency lint: detect backtick literal
tokens whose written form drifts between variants (e.g. `emit_foo` vs `emit.foo`).

Two rules (bd#315): a file name (last dot-segment is a known file suffix) is not
a literal token and is skipped; a difference in letter case alone is not drift.

Part of GH540 — spec-lint batch.
"""
from __future__ import annotations

import re

_FILE_SUFFIXES = {
    "py", "md", "json", "jsonl", "ts", "js", "sh", "yaml", "yml", "toml", "txt",
}

# Backtick-wrapped candidate tokens: identifier-like, may contain _ . -
_RE_BACKTICK_TOKEN = re.compile(r"`([A-Za-z_][A-Za-z0-9_.\-]*)`")

# Separator characters used both to detect multi-segment tokens and to build
# the cluster key.
_RE_SEPARATOR = re.compile(r"[._\-]")


def _is_file_name(token: str) -> bool:
    """True iff the token's last dot-segment is a known file suffix."""
    if "." not in token:
        return False
    return token.rsplit(".", 1)[1].lower() in _FILE_SUFFIXES


def _is_drift(forms: set[str]) -> bool:
    """True iff more than one distinct lowercased form is present."""
    return len({f.lower() for f in forms}) > 1


def scan_token_consistency(spec_text: str) -> list[dict]:
    """Scan spec_text for drifted literal-token variants inside backticks.

    Returns a list of finding dicts:
      {"line": <1-based int>, "rule_id": "token-inconsistency",
       "variants": [<sorted distinct raw forms>], "evidence": <str>}
    """
    lines = spec_text.splitlines()

    # cluster_key -> {"first_line": int, "forms": set[str]}
    clusters: dict[str, dict] = {}

    for i, line in enumerate(lines):
        lineno = i + 1  # 1-based
        for match in _RE_BACKTICK_TOKEN.finditer(line):
            token = match.group(1)
            if not _RE_SEPARATOR.search(token):
                # Single-segment token — ignored.
                continue
            if _is_file_name(token):
                continue

            key = _RE_SEPARATOR.sub("_", token.lower())
            entry = clusters.setdefault(key, {"first_line": lineno, "forms": set()})
            entry["forms"].add(token)

    findings: list[dict] = []
    for key, entry in clusters.items():
        forms = entry["forms"]
        if _is_drift(forms):
            variants = sorted(forms)
            findings.append({
                "line": entry["first_line"],
                "rule_id": "token-inconsistency",
                "variants": variants,
                "evidence": " vs ".join(variants),
            })

    findings.sort(key=lambda f: f["line"])
    return findings
