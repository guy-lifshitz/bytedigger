"""frontmatter.py — the one SKILL.md frontmatter parser (bd#115, moved by bd#116).

Public API:
  FrontmatterError        — an unreadable `metadata` block that mentions a guarded key
  GUARDED_METADATA_KEYS   — the keys whose mention makes an unreadable block an error
  BOM                     — the UTF-8 byte order mark as text ("\\ufeff")
  body_start(lines) -> int — index of the first line after the frontmatter block
  parse_frontmatter(text) -> dict | None

A deterministic YAML subset: top-level `key: value` scalars and one level of
nesting under `metadata:`. A leading UTF-8 BOM is ignored and CRLF reads as LF.
Stdlib only; used by `verification_registry` and `skill_companion`.
"""
from __future__ import annotations

import re
from typing import Any

GUARDED_METADATA_KEYS = ("verification", "overridable")

_KEY_RE = re.compile(r"^([A-Za-z_][\w.-]*):(?:[ \t]+(.*))?$")
_GUARDED_KEY_RE = re.compile(
    r"(^|[\s{,])(?:" + "|".join(GUARDED_METADATA_KEYS) + r")\s*:", re.MULTILINE,
)
_UNSCALAR_START = frozenset(">|[{&*!")
BOM = chr(0xFEFF)  # the UTF-8 byte order mark, decoded


class FrontmatterError(ValueError):
    """A `metadata` value the subset parser cannot read that mentions a guarded key."""


def _unquote(value: str) -> str:
    """Strip one pair of matching quotes around a scalar ('' unescapes in single quotes)."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        inner = value[1:-1]
        return inner.replace("''", "'") if value[0] == "'" else inner
    return value


def _strip_comment(value: str) -> str:
    """Cut `value` at the first whitespace-then-`#` outside a quoted string, then trim."""
    quote = ""
    for i, ch in enumerate(value):
        if quote:
            if ch == quote:
                quote = ""
        elif ch in ("'", '"'):
            quote = ch
        elif ch == "#" and i > 0 and value[i - 1] in " \t":
            return value[:i].strip()
    return value.strip()


def _is_scalar(value: str) -> bool:
    return bool(value) and value[0] not in _UNSCALAR_START


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" \t"))


def _parse_metadata(value: str, children: list[str]) -> dict[str, str] | None:
    """Parse the one-level `metadata:` map; None when unreadable and harmless.

    Raises FrontmatterError when the block is unreadable and mentions a guarded key.
    """
    raw = value + "\n" + "\n".join(children)

    def unreadable() -> dict[str, str] | None:
        if _GUARDED_KEY_RE.search(raw):
            raise FrontmatterError(
                "unsupported metadata block mentions " + " or ".join(GUARDED_METADATA_KEYS)
            )
        return None

    if value:
        return unreadable()
    content = [c for c in children if c.strip() and not c.lstrip().startswith("#")]
    if not content:
        return {}
    indent = _indent(content[0])
    if indent == 0:
        return unreadable()
    meta: dict[str, str] = {}
    for line in content:
        if _indent(line) != indent:
            return unreadable()
        m = _KEY_RE.match(line.strip())
        if m is None:
            return unreadable()
        item = _strip_comment(m.group(2) or "")
        if not _is_scalar(item):
            return unreadable()
        meta[m.group(1)] = _unquote(item)
    return meta


def _close_index(lines: list[str]) -> int | None:
    """Index of the line closing a leading `---` block, or None when there is none."""
    if not lines or lines[0].rstrip() != "---":
        return None
    return next((i for i in range(1, len(lines)) if lines[i].rstrip() == "---"), None)


def body_start(lines: list[str]) -> int:
    """Index of the first line after the frontmatter block (0 when there is none).

    `lines` are the text's lines without line breaks (BOM already removed).
    """
    end = _close_index(lines)
    return 0 if end is None else end + 1


def parse_frontmatter(text: str) -> dict[str, Any] | None:
    """Parse the leading `---` block with a deterministic YAML subset.

    Top-level `key: value` scalars and one level of nesting under `metadata:`.
    Other constructs (lists, folded scalars, other maps) are skipped. A leading
    UTF-8 BOM is ignored and CRLF reads as LF. Returns None when there is no
    frontmatter block; raises FrontmatterError only for an unreadable `metadata`
    value that mentions a guarded key (`GUARDED_METADATA_KEYS`).
    """
    if text.startswith(BOM):
        text = text[len(BOM):]
    text = text.replace("\r\n", "\n")
    lines = text.splitlines()
    end = _close_index(lines)
    if end is None:
        return None
    body = lines[1:end]
    result: dict[str, Any] = {}
    i = 0
    while i < len(body):
        line = body[i]
        m = _KEY_RE.match(line) if line and line[0] not in " \t-#" else None
        if m is None:
            i += 1
            continue
        j = i + 1
        while j < len(body) and (not body[j].strip() or body[j][0] in " \t-"):
            j += 1
        key, value = m.group(1), _strip_comment(m.group(2) or "")
        if key == "metadata":
            meta = _parse_metadata(value, body[i + 1:j])
            if meta is not None:
                result["metadata"] = meta
        elif _is_scalar(value):
            result[key] = _unquote(value)
        i = j
    return result
