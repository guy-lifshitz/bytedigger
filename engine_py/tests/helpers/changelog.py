"""Shared CHANGELOG.md helper + lint (bd#212). Stdlib only, pure functions.

Tests must not pin an entry to the `Unreleased` section or to the topmost
section: cutting a release moves entries. Use `require_entry` / `entry_sections`
(search Unreleased and every release section, never Pre-history) instead.

Importable as `from helpers.changelog import ...` (engine tests) or loadable by
path via importlib (tests/), hence the plain NamedTuple and no relative imports.
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path
from typing import Iterator, List, NamedTuple, Optional, Tuple, Union



def _changelog_path() -> Path:
    """Nearest ancestor carrying CHANGELOG.md; never counts ancestors (bd#97)."""
    here = Path(__file__).resolve()
    for ancestor in here.parents:
        if (ancestor / "CHANGELOG.md").is_file() and (ancestor / "engine_py").is_dir():
            return ancestor / "CHANGELOG.md"
    return here.parent / "CHANGELOG.md"


CHANGELOG_PATH = _changelog_path()

_FENCE_RE = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_RELEASE_RE = re.compile(r"^\[\d+\.\d+\.\d+[^\]]*\]")
_UNRELEASED_RE = re.compile(r"^\[unreleased\]$", re.IGNORECASE)

Needle = Union[str, "re.Pattern[str]"]


class Section(NamedTuple):
    title: str
    body: str


def read_changelog() -> str:
    return CHANGELOG_PATH.read_text(encoding="utf-8")


def _norm(text: str) -> str:
    return text.replace("\r\n", "\n")


def _unfenced_lines(text: str) -> Iterator[Tuple[str, bool]]:
    """Yield (line_with_eol, in_fence). Fence delimiter lines count as in-fence."""
    fence: Optional[Tuple[str, int]] = None
    for line in text.splitlines(keepends=True):
        stripped = line.rstrip("\n")
        m = _FENCE_RE.match(stripped)
        if fence is None:
            if m:
                fence = (m.group(1)[0], len(m.group(1)))
                yield line, True
            else:
                yield line, False
        else:
            if m and m.group(1)[0] == fence[0] and len(m.group(1)) >= fence[1] and not m.group(2).strip():
                fence = None
            yield line, True


def parse_sections(text: str) -> List[Section]:
    sections: List[Section] = []
    title: Optional[str] = None
    body: List[str] = []
    for line, in_fence in _unfenced_lines(_norm(text)):
        if not in_fence and line.startswith("## "):
            if title is not None:
                sections.append(Section(title, "".join(body)))
            title = line[3:].strip()
            body = []
        elif title is not None:
            body.append(line)
    if title is not None:
        sections.append(Section(title, "".join(body)))
    return sections


def is_unreleased(section: Section) -> bool:
    return bool(_UNRELEASED_RE.match(section.title))


def is_release(section: Section) -> bool:
    return bool(_RELEASE_RE.match(section.title))


def _heading_blocks(section: Section) -> List[Tuple[str, str]]:
    out: List[Tuple[str, List[str]]] = []
    for line, in_fence in _unfenced_lines(section.body):
        if not in_fence and line.startswith("### "):
            out.append((line[4:].strip(), []))
        elif out:
            out[-1][1].append(line)
    return [(h, "".join(b)) for h, b in out]


def blocks(section: Section, heading: str) -> List[str]:
    return [b for h, b in _heading_blocks(section) if h == heading]


def _matches(hay: str, needle: Needle) -> bool:
    if isinstance(needle, str):
        return needle in hay
    return needle.search(hay) is not None


def _searched(text: str) -> List[Section]:
    return [s for s in parse_sections(text) if is_unreleased(s) or is_release(s)]


def entry_sections(text: str, needle: Needle, *, block: Optional[str] = None) -> List[Section]:
    found: List[Section] = []
    for sec in _searched(text):
        hay = "".join(blocks(sec, block)) if block is not None else sec.body
        if _matches(hay, needle):
            found.append(sec)
    return found


def require_entry(text: str, needle: Needle, *, block: Optional[str] = None) -> Section:
    found = entry_sections(text, needle, block=block)
    if found:
        return found[0]
    shown = needle if isinstance(needle, str) else needle.pattern
    titles = [s.title for s in _searched(text)]
    raise AssertionError(
        f"CHANGELOG entry not found: needle={shown!r} block={block!r}; searched sections: {titles}"
    )


def duplicate_blocks(text: str) -> List[Tuple[str, str, int]]:
    out: List[Tuple[str, str, int]] = []
    for sec in parse_sections(text):
        counts = Counter(h for h, _ in _heading_blocks(sec))
        for heading, n in counts.items():
            if n > 1:
                out.append((sec.title, heading, n))
    return out
