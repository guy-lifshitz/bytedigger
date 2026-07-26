"""RED tests for bd#98 — state the pip>=21.3 (PEP 660) floor in the docs.

`pip install -e engine_py` is the first command the README gives; on stock
macOS `/usr/bin/python3` (3.9.6, pip 21.2.4) it fails, because an editable
install of a pyproject-only project needs PEP 660 support, added in pip 21.3.
The floor is nowhere stated in the repo (no document contains `21.3` today).

The unit under test is the on-disk documentation. Guarded documents are the
SEVEN that hand a reader an editable-install command:

    README.md, CONTRIBUTING.md, examples/README.md, examples/library/README.md,
    examples/verified-tdd-run/README.md, docs/article.md, npm/README.md

`engine_py/README.md` is deliberately EXCLUDED: it instructs only the
non-editable `pip install .`, which is measured to succeed on pip 21.2.4
(rc=0, `bytedigger-engine-0.1.1` installed), so no floor applies to it.

Frozen coverage rule (evaluated PER OCCURRENCE, against ALL floor lines):
an editable-install occurrence at line L in document D is covered iff a floor
line exists at line F <= L (SAME LINE COUNTS) that is either in the same
section as L, or in D's preamble (above the first heading). A floor line is
one that mentions `pip` (case-insensitive) and `21.3` on the same line.

Section-delimiter choice (documented, per spec): a line is a section start iff
it begins with two or more `#` followed by whitespace (`## ` and deeper) AND
it is outside a fenced code block. Fence awareness matters because these docs
carry ```bash fences whose shell comments start with `#`.

Conventions: repo root is located from this file the way conftest.py does
(`Path(__file__).parent.parent` is the engine_py root; the repo root is one
level above). No module-level sys.path mutation (81F97F3D gate), no conftest
import, plain pytest, no plugins, no network, fully hermetic. Nothing here is
stub-passable: only real edits to the seven documents can turn these green.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

# conftest idiom: engine_py root == tests/.. ; repo root == engine_py/..
_ENGINE_ROOT = Path(__file__).parent.parent
_REPO_ROOT = _ENGINE_ROOT.parent

# AC6 census — pre-fix count of editable-install occurrences per document,
# verified by `grep -rnE "pip +install +(-e|--editable)" --include="*.md"`:
#   README.md:33,94,109,110                     -> 4
#   CONTRIBUTING.md:11,28                       -> 2
#   examples/README.md:24                       -> 1
#   examples/library/README.md:8,41             -> 2
#   examples/verified-tdd-run/README.md:12      -> 1
#   docs/article.md:112                         -> 1
#   npm/README.md:20                            -> 1
#                                          total 12 occurrences / 7 documents
# Pinning these counts is what stops the guard from being satisfied by simply
# deleting the instructions instead of documenting the floor.
_CENSUS: Dict[str, int] = {
    "README.md": 4,
    "CONTRIBUTING.md": 2,
    "examples/README.md": 1,
    "examples/library/README.md": 2,
    "examples/verified-tdd-run/README.md": 1,
    "docs/article.md": 1,
    "npm/README.md": 1,
}
_GUARDED_DOCS: Sequence[str] = tuple(_CENSUS)

# An editable-install instruction, in every shape that appears or could appear:
#   pip install -e .        pip install -e "engine_py[extra]"
#   pip install -e.         pip install --editable .
#   python3 -m pip install -e ".[test]"      cd engine_py && pip install -e .
# `-e` / `--editable` must stand as its own token (not a fragment of a word or
# of another flag), but may be followed immediately by `.` (`-e.`).
_EDITABLE_RE = re.compile(r"pip\s+install\s+[^\n]*?(?<![\w-])(?:-e|--editable)(?![\w-])")

# A stated pip 21.3 floor: the line mentions pip AND the version 21.3.
# No proximity window (dropped deliberately) — the two facts on one line are
# the whole requirement. `21.2.4` does not contain `21.3`, and the version-less
# upgrade comment names no version, so neither qualifies.
_PIP_RE = re.compile(r"\bpip\b", re.IGNORECASE)
_VERSION_RE = re.compile(r"\b21\.3\b")

# Python 3.9 named on the same line (AC1a): matches "3.9", "3.9+", "3.9.6".
_PY39_RE = re.compile(r"\b3\.9\b")

# An upgrade command (AC1b), common forms:
#   pip install -U pip / pip install --upgrade pip / python3 -m pip install -U pip
_UPGRADE_RE = re.compile(r"pip\s+install\s+(?:-U|--upgrade)\s+pip", re.IGNORECASE)

_FENCE_RE = re.compile(r"^\s*(?:```|~~~)")
_HEADING_RE = re.compile(r"^#{2,6}\s")

_PREAMBLE = -1  # section id for everything above the first heading


# ─── the rule, as pure functions over lines (so it is unit-testable) ─────────


def _section_ids(lines: Sequence[str]) -> List[int]:
    """Section id per line (0-based list, parallel to `lines`).

    `_PREAMBLE` until the first heading; then 0, 1, 2 ... one per heading.
    A heading line belongs to the section it opens. Fenced code blocks are
    skipped so `# comment` / `## comment` inside ```bash fences are not
    mistaken for headings.
    """
    ids: List[int] = []
    current = _PREAMBLE
    in_fence = False
    for line in lines:
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            ids.append(current)
            continue
        if not in_fence and _HEADING_RE.match(line):
            current = current + 1 if current != _PREAMBLE else 0
        ids.append(current)
    return ids


def _editable_lines(lines: Sequence[str]) -> List[Tuple[int, str]]:
    """1-based (lineno, text) for every editable-install instruction."""
    return [(i, ln) for i, ln in enumerate(lines, start=1) if _EDITABLE_RE.search(ln)]


def _floor_lines(lines: Sequence[str]) -> List[Tuple[int, str]]:
    """1-based (lineno, text) for every line stating the pip 21.3 floor."""
    return [
        (i, ln)
        for i, ln in enumerate(lines, start=1)
        if _PIP_RE.search(ln) and _VERSION_RE.search(ln)
    ]


def _uncovered_occurrences(lines: Sequence[str], label: str) -> List[str]:
    """Violation messages (path:line + remedy) for one document's lines.

    Per occurrence, evaluated against ALL floor lines: covered iff some floor
    at F <= L sits in the same section as L or in the preamble.
    """
    sections = _section_ids(lines)
    floors = _floor_lines(lines)
    violations: List[str] = []

    for lineno, text in _editable_lines(lines):
        target_section = sections[lineno - 1]
        covering: Optional[int] = None
        for floor_lineno, _floor_text in floors:
            if floor_lineno > lineno:
                continue
            floor_section = sections[floor_lineno - 1]
            if floor_section == target_section or floor_section == _PREAMBLE:
                covering = floor_lineno
                break
        if covering is None:
            where = (
                "the document preamble"
                if target_section == _PREAMBLE
                else "section #%d" % target_section
            )
            violations.append(
                f"{label}:{lineno}: editable-install instruction "
                f"{text.strip()!r} is not covered: no line mentioning pip and "
                f"21.3 exists at or above line {lineno} in {where} or in the "
                f"preamble (document has {len(floors)} floor line(s) total). "
                f"Add a pip 21.3+ statement to that section of {label}."
            )
    return violations


def _lines_of(rel: str) -> List[str]:
    path = _REPO_ROOT / rel
    assert path.is_file(), f"guarded document missing: {path}"
    return path.read_text(encoding="utf-8").splitlines()


def _assert_document_covered(rel: str) -> None:
    lines = _lines_of(rel)
    occurrences = _editable_lines(lines)
    assert occurrences, (
        f"{rel} contains no editable-install instruction — the guard would be "
        f"vacuous (census pins {_CENSUS[rel]}); see the AC6 census test."
    )
    violations = _uncovered_occurrences(lines, rel)
    assert not violations, (
        f"{len(violations)}/{len(occurrences)} editable-install instruction(s) "
        f"in {rel} lack a pip 21.3 floor in scope:\n" + "\n".join(violations)
    )


# ─── AC1: README states the full requirement ────────────────────────────────


def test_ac1_readme_names_python_39_and_pip_213_before_first_editable_install():
    """AC1a — a single line at or before README's FIRST editable-install
    command names both Python 3.9 and pip 21.3."""
    lines = _lines_of("README.md")
    occurrences = _editable_lines(lines)
    assert occurrences, "README.md has no editable-install instruction to guard"
    first = occurrences[0][0]
    hits = [
        i
        for i, ln in enumerate(lines[:first], start=1)
        if _PY39_RE.search(ln) and _VERSION_RE.search(ln) and _PIP_RE.search(ln)
    ]
    assert hits, (
        f"README.md:{first}: the first editable-install instruction "
        f"({occurrences[0][1].strip()!r}) is not preceded by a requirements "
        "line naming both Python 3.9 and pip 21.3. Add one line stating e.g. "
        "'Requires Python 3.9+ and pip 21.3+ (PEP 660) for editable installs.' "
        f"at or above README.md:{first}."
    )


def test_ac1_readme_quickstart_has_upgrade_command_and_states_floor():
    """AC1b — the `## Quickstart` section carries an upgrade command AND
    states the pip 21.3 floor inside that same section."""
    lines = _lines_of("README.md")
    sections = _section_ids(lines)

    quickstart = [
        sections[i]
        for i, ln in enumerate(lines)
        if ln.strip().lower().startswith("## quickstart")
    ]
    assert quickstart, "README.md has no `## Quickstart` heading"
    sid = quickstart[0]
    body = [(i + 1, ln) for i, ln in enumerate(lines) if sections[i] == sid]

    upgrades = [i for i, ln in body if _UPGRADE_RE.search(ln)]
    assert upgrades, (
        "README.md `## Quickstart` has no pip upgrade command "
        "(`pip install -U pip` / `pip install --upgrade pip`); it is the "
        "documented escape from the pre-PEP660 pip."
    )

    floors = [i for i, ln in body if _PIP_RE.search(ln) and _VERSION_RE.search(ln)]
    assert floors, (
        f"README.md:{upgrades[0]}: `## Quickstart` carries the upgrade command "
        "but never states the version it is upgrading past. State pip 21.3 "
        "inside the Quickstart section (e.g. annotate the upgrade line "
        "'# editable installs need pip >= 21.3 (PEP 660)')."
    )


# ─── AC2: every occurrence in all 7 guarded documents is covered ────────────


def test_ac2_readme_editable_installs_covered():
    _assert_document_covered("README.md")


def test_ac2_contributing_editable_installs_covered():
    _assert_document_covered("CONTRIBUTING.md")


def test_ac2_examples_readme_editable_installs_covered():
    _assert_document_covered("examples/README.md")


def test_ac2_examples_library_readme_editable_installs_covered():
    _assert_document_covered("examples/library/README.md")


def test_ac2_examples_verified_tdd_run_readme_editable_installs_covered():
    _assert_document_covered("examples/verified-tdd-run/README.md")


def test_ac2_docs_article_editable_installs_covered():
    _assert_document_covered("docs/article.md")


def test_ac2_npm_readme_editable_installs_covered():
    _assert_document_covered("npm/README.md")


def test_ac2_no_guarded_document_has_an_uncovered_occurrence():
    """AC2/AC3 — one sweep over all seven guarded documents, so a new snippet
    added to a section with no floor fails CI naming file and line."""
    violations: List[str] = []
    for rel in _GUARDED_DOCS:
        violations.extend(_uncovered_occurrences(_lines_of(rel), rel))
    assert not violations, (
        f"{len(violations)} editable-install instruction(s) across "
        f"{len(_GUARDED_DOCS)} guarded document(s) lack a pip 21.3 floor in "
        "scope:\n" + "\n".join(violations)
    )


# ─── AC3: the coverage rule itself, on synthetic documents ──────────────────


def test_ac3_coverage_rule_semantics_per_occurrence():
    """AC3 — the frozen rule, exercised on synthetic docs: same-line counts,
    a preamble floor covers the whole document, a same-section floor covers
    only its own section, a later floor covers nothing."""
    same_line = ["# D", "", "## Setup", "pip install -e .  # needs pip 21.3+"]
    assert _uncovered_occurrences(same_line, "same_line.md") == []

    preamble = ["# D", "Requires pip 21.3+.", "", "## Setup", "pip install -e ."]
    assert _uncovered_occurrences(preamble, "preamble.md") == []

    same_section = ["# D", "## Setup", "Needs pip 21.3.", "pip install -e ."]
    assert _uncovered_occurrences(same_section, "same_section.md") == []

    other_section = [
        "# D",
        "## Setup",
        "Needs pip 21.3.",
        "## Extras",
        "pip install -e '.[test]'",
    ]
    assert len(_uncovered_occurrences(other_section, "other.md")) == 1, (
        "a floor in a different (non-preamble) section must NOT cover an "
        "occurrence in this one"
    )

    floor_after = ["# D", "## Setup", "pip install -e .", "Needs pip 21.3."]
    assert len(_uncovered_occurrences(floor_after, "after.md")) == 1, (
        "a floor stated below the occurrence must not cover it"
    )

    # Two occurrences, one covered and one not: the rule is per occurrence,
    # not "first floor vs first occurrence".
    mixed = [
        "# D",
        "## Setup",
        "Needs pip 21.3.",
        "pip install -e .",
        "## Extras",
        "pip install --editable '.[test]'",
    ]
    msgs = _uncovered_occurrences(mixed, "mixed.md")
    assert len(msgs) == 1 and "mixed.md:6" in msgs[0], msgs

    # Fenced `## ` lines are shell comments, not headings.
    fenced = [
        "# D",
        "## Setup",
        "Needs pip 21.3.",
        "```bash",
        "## not a heading",
        "pip install -e .",
        "```",
    ]
    assert _uncovered_occurrences(fenced, "fenced.md") == []


# ─── AC6: the census cannot be gamed by deleting instructions ───────────────


def test_ac6_editable_install_census_not_reduced():
    """AC6 — each guarded document still carries at least its pre-fix count of
    editable-install instructions (census inline at the top of this file)."""
    shortfalls: List[str] = []
    for rel, expected in _CENSUS.items():
        found = len(_editable_lines(_lines_of(rel)))
        if found < expected:
            shortfalls.append(
                f"{rel}: {found} editable-install instruction(s), expected at "
                f"least {expected} (pre-fix census). bd#98 documents the pip "
                "21.3 floor; it must not delete the instructions."
            )
    assert not shortfalls, "\n".join(shortfalls)


# ─── matcher sanity (F6/F8) ─────────────────────────────────────────────────


def test_floor_matcher_rejects_versionless_and_wrong_versions():
    """A floor line mentions pip AND 21.3 on one line — nothing looser."""

    def is_floor(line: str) -> bool:
        return bool(_PIP_RE.search(line) and _VERSION_RE.search(line))

    assert not is_floor(
        "pip install -U pip   # stock macOS 3.9 ships a pre-PEP660 pip that "
        "cannot editable-install"
    ), "the version-less upgrade comment must not count as a stated floor"
    assert not is_floor("requires pip 21.2.4 or later")
    assert not is_floor("Python 3.9+ supported")
    assert not is_floor("version 21.3 of something unrelated")
    for phrasing in (
        "pip 21.3+",
        "pip >= 21.3",
        "pip>=21.3",
        "Requires Python 3.9+ and pip 21.3 or newer.",
        "PIP  >=  21.3",
        "# editable installs need pip 21.3 (PEP 660)",
    ):
        assert is_floor(phrasing), f"floor matcher missed {phrasing!r}"


def test_editable_matcher_covers_all_command_shapes():
    """The occurrence matcher must not be dodgeable by spelling."""
    for line in (
        "pip install -e .",
        "pip install -e.",
        "pip install --editable .",
        "pip install --editable=.",
        'pip install -e ".[test]"',
        "python3 -m pip install -e engine_py",
        "cd engine_py && pip install -e . && cd ..",
        '`pip install -e "engine_py[agentic-pydantic]"`, then run it',
    ):
        assert _EDITABLE_RE.search(line), f"editable matcher missed {line!r}"
    for line in (
        "pip install .",
        "pip install bytedigger-engine",
        "pip install -U pip   # pre-PEP660 pip cannot editable-install",
    ):
        assert not _EDITABLE_RE.search(line), f"editable matcher over-matched {line!r}"


# ─── AC5: the fix stays docs-only ───────────────────────────────────────────


def test_ac5_engine_py_has_no_setup_py_or_setup_cfg():
    """AC5 — no legacy packaging shim: it was measured to still fail on pip
    21.2.4 and would turn a 2-line error into a ~90-line traceback."""
    for name in ("setup.py", "setup.cfg"):
        candidate = _ENGINE_ROOT / name
        assert not candidate.exists(), (
            f"{candidate} exists — bd#98 is a docs-only fix; a legacy packaging "
            "shim was explicitly rejected (it still fails on pip 21.2.4)."
        )
