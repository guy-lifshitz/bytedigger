"""bd#79 — detect Cyrillic in the tracked tree.

The repository is English-only. This module is the detector behind the
`cyrillic-prose-lint` registry name; the driver that runs it lives at the repo
root (`cyrillic-prose-lint.py`), which is where the `sys.path` bootstrap is
allowed to live — bd#44 AC7 forbids one inside the package, so there is none
here and this module imports nothing outside the standard library.

The allowlist is deliberately awkward to extend. Each entry carries a reason
AND the exact number of Cyrillic characters the file is licensed to hold, so a
new occurrence arriving in an already-exempt file is still a violation. A
file-scoped exemption with no budget would have licensed the whole file
forever, which is how three previous passes each left something behind.

Only Cyrillic is detected, not "non-ASCII": this prose is full of em dashes,
degree signs and accented names, and a detector that fired on those would need
an allowlist so wide it would stop meaning anything.
"""

from __future__ import annotations

import os
import re
import subprocess
from typing import Dict, Iterator, List, NamedTuple, Sequence

__all__ = [
    "ALLOWLIST",
    "CYRILLIC_RE",
    "Hit",
    "Violation",
    "scan_text",
    "scan_paths",
    "scan_tree",
    "tracked_files",
]

# U+0400–U+04FF is Cyrillic; U+0500–U+052F (Cyrillic Supplement) is included so
# a lookalike from the supplement block cannot walk straight past the gate.
# Built from code points rather than written out, so this module is not the
# one file that has to appear in its own allowlist.
_CYRILLIC_RANGES = ((0x0400, 0x04FF), (0x0500, 0x052F))  # Cyrillic, Cyrillic Supplement
CYRILLIC_RE = re.compile(
    "[" + "".join(chr(lo) + "-" + chr(hi) for lo, hi in _CYRILLIC_RANGES) + "]"
)

ALLOWLIST: Dict[str, Dict[str, object]] = {
    "engine_py/bytedigger_engine/scripts/lib/mutation_two_sidedness_verifier.py": {
        "chars": 118,
        "reason": (
            "Russian morphological vocabulary used as detection patterns, not "
            "prose. Imported by scripts/spec_lint/lint_spec.py and matched "
            "against spec text; the module header declares it frozen verbatim. "
            "Translating it leaves the guard green because it can no longer "
            "match anything."
        ),
    },
    "engine_py/bytedigger_engine/scripts/lib/closure_evidence_verifier.py": {
        "chars": 77,
        "reason": (
            "Same as mutation_two_sidedness_verifier.py: a frozen detection "
            "vocabulary consumed by scripts/spec_lint/lint_spec.py."
        ),
    },
    "engine_py/tests/test_phase_45_spec_decision_doc_injection.py": {
        "chars": 30,
        "reason": (
            "A deliberate multibyte UTF-8 fixture, asserted on directly, with "
            "the byte-width arithmetic spelled out in the comment beside it. An "
            "ASCII replacement removes the property the test measures."
        ),
    },
    "engine_py/tests/test_gh933_agent_sdk_stderr_outage.py": {
        "chars": 15,
        "reason": (
            "A deliberate multibyte UTF-8 fixture for a stderr decode path. "
            "Same reason as the file above."
        ),
    },
}

# Directories that hold no tracked source but plenty of generated text. Only
# consulted when there is no git index to ask (a `git archive` tree, which is
# how the clean-room lane ships the repository).
_WALK_SKIP_DIRS = frozenset({
    ".git", ".venv", "__pycache__", "build", "dist", "node_modules",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".worktrees",
})


class Hit(NamedTuple):
    """One Cyrillic character, with a 1-based line and column."""

    line: int
    column: int
    char: str


class Violation(NamedTuple):
    """A Hit that carries the file it was found in."""

    path: str
    line: int
    column: int
    char: str


def scan_text(text: str) -> Iterator[Hit]:
    """Yield one Hit per Cyrillic character in `text`."""
    for lineno, line in enumerate(text.splitlines(), start=1):
        for match in CYRILLIC_RE.finditer(line):
            yield Hit(lineno, match.start() + 1, match.group())


def tracked_files(root: str) -> List[str]:
    """Root-relative posix paths of the files to scan.

    `git ls-files` when there is an index, otherwise a filesystem walk. The
    walk is a superset of the index, never a subset — an unreadable index must
    not turn into a smaller corpus that quietly passes.
    """
    if os.path.exists(os.path.join(root, ".git")):
        result = subprocess.run(
            ["git", "ls-files", "-z"],
            cwd=root, capture_output=True, text=True,
        )
        if result.returncode == 0:
            return [p for p in result.stdout.split("\0") if p]

    found: List[str] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in _WALK_SKIP_DIRS and not d.endswith(".egg-info")]
        for name in filenames:
            abs_path = os.path.join(dirpath, name)
            found.append(os.path.relpath(abs_path, root).replace(os.sep, "/"))
    return sorted(found)


def _read(path: str):
    """File text, or None when the bytes are not UTF-8 text (an image, a wheel)."""
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError:
        raise
    if b"\0" in raw:
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def scan_paths(paths: Sequence[str], root: str = ".") -> List[Violation]:
    """Scan `paths` (relative to `root`, or absolute) and return the violations.

    An allowlisted file contributes nothing while its Cyrillic count matches
    its declared budget. The moment the count differs — in either direction —
    every hit in that file is reported, because a budget that no longer
    describes the file is an exemption nobody has read.

    Raises OSError if a path cannot be read. Reporting "clean" for a file the
    lint never managed to open is the failure mode this whole lot is about.
    """
    violations: List[Violation] = []
    for path in paths:
        abs_path = path if os.path.isabs(path) else os.path.join(root, path)
        text = _read(abs_path)
        if text is None:
            continue

        rel = os.path.relpath(abs_path, root).replace(os.sep, "/")
        hits = list(scan_text(text))
        entry = ALLOWLIST.get(rel)
        if entry is not None and len(hits) == entry["chars"]:
            continue
        violations.extend(Violation(rel, h.line, h.column, h.char) for h in hits)
    return violations


def scan_tree(root: str = ".") -> List[Violation]:
    """Scan every tracked text file under `root`."""
    return scan_paths(tracked_files(root), root)
