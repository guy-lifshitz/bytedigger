"""RED tests for bd#212 - shared CHANGELOG helper + lint.

UUT: `helpers.changelog` (engine_py/tests/helpers/changelog.py), which does not
exist yet. It is imported lazily inside each test (`_cl()`), so collection never
fails and each test fails on its own ImportError until GREEN. Fixtures are
synthetic CHANGELOG strings plus the real repo CHANGELOG.md. No sys.path
manipulation: `helpers` is importable through the conftest-import-time setup.
"""
from __future__ import annotations

import re
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1]
REPO_ROOT = ENGINE.parent
THIS_FILE = Path(__file__).resolve()
HELPER_FILE = ENGINE / "tests" / "helpers" / "changelog.py"


def _cl():
    from helpers import changelog  # noqa: PLC0415 - lazy by design

    return changelog


_PREAMBLE = "# Changelog\n\nIntro text.\n\n"

_UNRELEASED = (
    "## [Unreleased]\n\n"
    "### Added\n\n- thing bd#900 shipped\n\n"
    "### Changed\n\n- first change alpha\n\n"
)

_PREHISTORY = (
    "## Pre-history (before the Python engine)\n\n"
    "### Added\n\n- ancient legacy_only_entry\n"
)

DOC_UNRELEASED = _PREAMBLE + _UNRELEASED + _PREHISTORY

DOC_RELEASED = (
    _PREAMBLE
    + "## [Unreleased]\n\n"
    + "## [1.1.1] — 2026-10-03\n\n"
    + "### Added\n\n- thing bd#900 shipped\n\n"
    + "### Changed\n\n- first change alpha\n\n"
    + _PREHISTORY
)

DOC_DUP_CHANGED = (
    _PREAMBLE
    + "## [Unreleased]\n\n"
    + "### Changed\n\n- first change alpha\n\n"
    + "### Added\n\n- something\n\n"
    + "### Changed\n\n- second change beta\n\n"
)


# AC1
def test_ac1_entry_found_in_unreleased_and_after_release_cut():
    cl = _cl()
    found = cl.entry_sections(DOC_UNRELEASED, "bd#900")
    assert [s.title for s in found] == ["[Unreleased]"]
    assert cl.is_unreleased(found[0]) and not cl.is_release(found[0])

    cut = cl.entry_sections(DOC_RELEASED, "bd#900")
    assert [s.title for s in cut] == ["[1.1.1] — 2026-10-03"]
    assert cl.is_release(cut[0]) and not cl.is_unreleased(cut[0])
    assert cl.require_entry(DOC_RELEASED, "bd#900").title.startswith("[1.1.1]")


# AC2
def test_ac2_missing_entry_raises_and_returns_empty():
    import pytest

    cl = _cl()
    needle = "nowhere_to_be_found_xyz"
    assert cl.entry_sections(DOC_UNRELEASED, needle) == []
    with pytest.raises(AssertionError) as ei:
        cl.require_entry(DOC_UNRELEASED, needle)
    assert needle in str(ei.value)


# AC3
def test_ac3_prehistory_only_entry_not_found():
    import pytest

    cl = _cl()
    assert cl.entry_sections(DOC_UNRELEASED, "legacy_only_entry") == []
    with pytest.raises(AssertionError):
        cl.require_entry(DOC_UNRELEASED, "legacy_only_entry")
    titles = [s.title for s in cl.parse_sections(DOC_UNRELEASED)]
    assert titles == ["[Unreleased]", "Pre-history (before the Python engine)"]
    pre = cl.parse_sections(DOC_UNRELEASED)[1]
    assert not cl.is_unreleased(pre) and not cl.is_release(pre)


# AC4
def test_ac4_two_changed_blocks_never_merged_or_dropped():
    cl = _cl()
    sec = cl.parse_sections(DOC_DUP_CHANGED)[0]
    bl = cl.blocks(sec, "Changed")
    assert len(bl) == 2
    assert "alpha" in bl[0] and "beta" not in bl[0]
    assert "beta" in bl[1] and "alpha" not in bl[1]

    second = cl.entry_sections(DOC_DUP_CHANGED, "second change beta", block="Changed")
    first = cl.entry_sections(DOC_DUP_CHANGED, "first change alpha", block="Changed")
    assert [s.title for s in second] == ["[Unreleased]"]
    assert [s.title for s in first] == ["[Unreleased]"]
    # an entry living only in ### Added is not found when scoped to Changed
    assert cl.entry_sections(DOC_DUP_CHANGED, "something", block="Changed") == []

    assert ("[Unreleased]", "Changed", 2) in cl.duplicate_blocks(DOC_DUP_CHANGED)


# AC5
def test_ac5_fenced_headings_ignored():
    cl = _cl()
    doc = (
        _PREAMBLE
        + "## [Unreleased]\n\n"
        + "### Changed\n\n- real entry\n\n"
        + "```\n## [9.9.9]\n### Changed\n- fenced_entry\n```\n\n"
    )
    secs = cl.parse_sections(doc)
    assert [s.title for s in secs] == ["[Unreleased]"]
    assert len(cl.blocks(secs[0], "Changed")) == 1
    assert cl.duplicate_blocks(doc) == []


# AC6
def test_ac6_regex_needle_and_exact_substring_needle():
    cl = _cl()
    doc = _PREAMBLE + "## [Unreleased]\n\n### Added\n\n- bd#133 guard [x] a.b\n"
    assert cl.entry_sections(doc, re.compile(r"(?i)BD#1\d\d"))
    assert not cl.entry_sections(doc, re.compile(r"bd#9\d\d"))
    # substring needle: literal, no regex interpretation
    assert cl.entry_sections(doc, "bd#133 guard [x] a.b")
    assert cl.entry_sections(doc, "[x]")
    assert cl.entry_sections(doc, "a.b")
    assert cl.entry_sections(doc, "a.c") == []
    assert cl.entry_sections(doc, "bd#1.3") == []
    assert cl.entry_sections(doc, "[a-z]") == []


# AC7
def test_ac7_crlf_parses_same_as_lf():
    cl = _cl()
    crlf = DOC_DUP_CHANGED.replace("\n", "\r\n")
    a = cl.parse_sections(DOC_DUP_CHANGED)
    b = cl.parse_sections(crlf)
    assert [s.title for s in a] == [s.title for s in b]
    assert len(cl.blocks(b[0], "Changed")) == 2
    assert cl.duplicate_blocks(crlf) == cl.duplicate_blocks(DOC_DUP_CHANGED)
    assert cl.entry_sections(crlf, "second change beta", block="Changed")


# AC8
def test_ac8_real_changelog_has_no_duplicate_blocks():
    cl = _cl()
    dups = cl.duplicate_blocks(cl.read_changelog())
    assert dups == [], f"duplicate ### blocks in CHANGELOG.md: {dups}"
    assert Path(cl.CHANGELOG_PATH) == REPO_ROOT / "CHANGELOG.md"


def test_ac8_synthetic_duplicate_is_flagged():
    cl = _cl()
    assert cl.duplicate_blocks(DOC_DUP_CHANGED) != []
    assert cl.duplicate_blocks(DOC_UNRELEASED) == []


# --- amendment D / B: parser + lint edge fixtures ---------------------------

def test_d_duplicate_non_changed_heading_in_release_and_prehistory_reported():
    cl = _cl()
    doc = (
        _PREAMBLE
        + "## [Unreleased]\n\n"
        + "## [1.1.1] — 2026-10-03\n\n"
        + "### Added\n\n- one\n\n### Fixed\n\n- f\n\n### Added\n\n- two\n\n"
        + "## Pre-history (before the Python engine)\n\n"
        + "### Added\n\n- a\n\n### Added\n\n- b\n\n### Added\n\n- c\n"
    )
    dups = cl.duplicate_blocks(doc)
    assert ("[1.1.1] — 2026-10-03", "Added", 2) in dups
    assert ("Pre-history (before the Python engine)", "Added", 3) in dups
    assert len(dups) == 2


def test_d_duplicate_heading_inside_fence_not_reported():
    cl = _cl()
    doc = (
        _PREAMBLE
        + "## [Unreleased]\n\n### Added\n\n- x\n\n"
        + "```\n### Added\n- fenced\n```\n"
    )
    assert cl.duplicate_blocks(doc) == []


def test_d_trailing_whitespace_headings_are_stripped():
    cl = _cl()
    doc = (
        _PREAMBLE
        + "## [Unreleased]  \n\n### Changed \n\n- a\n\n### Changed\t\n\n- b\n"
    )
    secs = cl.parse_sections(doc)
    assert [s.title for s in secs] == ["[Unreleased]"]
    assert cl.is_unreleased(secs[0])
    assert len(cl.blocks(secs[0], "Changed")) == 2
    assert ("[Unreleased]", "Changed", 2) in cl.duplicate_blocks(doc)


def test_d_fence_with_info_string_is_ignored():
    cl = _cl()
    doc = (
        _PREAMBLE
        + "## [Unreleased]\n\n### Added\n\n- real\n\n"
        + "```python\n## [9.9.9]\n### Added\n```\n"
    )
    assert [s.title for s in cl.parse_sections(doc)] == ["[Unreleased]"]
    assert cl.duplicate_blocks(doc) == []


def test_d_tilde_and_indented_fences_are_ignored():
    cl = _cl()
    doc = (
        _PREAMBLE
        + "## [Unreleased]\n\n### Added\n\n- real\n\n"
        + "~~~\n## [9.9.9]\n### Added\n~~~\n\n"
        + "  ```\n## [8.8.8]\n  ```\n"
    )
    assert [s.title for s in cl.parse_sections(doc)] == ["[Unreleased]"]
    assert cl.duplicate_blocks(doc) == []


def test_d_fence_closes_only_with_same_char_and_sufficient_length():
    cl = _cl()
    doc = (
        _PREAMBLE
        + "## [Unreleased]\n\n"
        + "````\n```\n## [9.9.9]\n~~~\n## [8.8.8]\n````\n\n"
        + "## [1.0.0] — 2026-01-01\n\n- after\n"
    )
    assert [s.title for s in cl.parse_sections(doc)] == ["[Unreleased]", "[1.0.0] — 2026-01-01"]


def test_d_unclosed_fence_swallows_to_eof():
    cl = _cl()
    doc = (
        _PREAMBLE
        + "## [Unreleased]\n\n- a\n\n```\n## [1.0.0] — 2026-01-01\n\n- swallowed\n"
    )
    secs = cl.parse_sections(doc)
    assert [s.title for s in secs] == ["[Unreleased]"]
    assert "swallowed" in secs[0].body


def test_d_crlf_bodies_equal_lf_bodies():
    cl = _cl()
    crlf = DOC_DUP_CHANGED.replace("\n", "\r\n")
    a = cl.parse_sections(DOC_DUP_CHANGED)
    b = cl.parse_sections(crlf)
    assert [s.body for s in a] == [s.body for s in b]
    assert "\r" not in "".join(s.body for s in b)
    assert cl.blocks(a[0], "Changed") == cl.blocks(b[0], "Changed")
    # an end-of-line anchored needle behaves the same on CRLF
    assert cl.entry_sections(crlf, re.compile(r"alpha$", re.M))


def test_d_duplicate_unreleased_sections_yield_two_in_document_order():
    cl = _cl()
    doc = (
        _PREAMBLE
        + "## [Unreleased]\n\n- first_marker\n\n"
        + "## [Unreleased]\n\n- second_marker\n"
    )
    secs = cl.parse_sections(doc)
    assert [s.title for s in secs] == ["[Unreleased]", "[Unreleased]"]
    assert "first_marker" in secs[0].body and "second_marker" in secs[1].body
    assert len(cl.entry_sections(doc, re.compile("_marker"))) == 2


def test_d_require_entry_error_names_needle_block_and_searched_titles():
    import pytest

    cl = _cl()
    doc = DOC_RELEASED
    with pytest.raises(AssertionError) as ei:
        cl.require_entry(doc, "absent_needle_qq", block="Changed")
    msg = str(ei.value)
    assert "absent_needle_qq" in msg
    assert "Changed" in msg
    assert "[Unreleased]" in msg and "[1.1.1] — 2026-10-03" in msg
    # amendment I: the message names the search, it does not dump the document
    assert "first change alpha" not in msg
    assert "bd#900" not in msg
    assert "legacy_only_entry" not in msg
    assert len(msg) < 1000


def test_d_section_is_namedtuple_and_by_path_loading_works(monkeypatch):
    """tests/ cannot import `helpers`; they load the helper by path (no sys.path
    mutation) with the module registered in sys.modules before exec_module."""
    import importlib.util
    import sys

    assert HELPER_FILE.is_file(), f"missing helper: {HELPER_FILE}"
    name = "_bd212_by_path_probe"
    spec = importlib.util.spec_from_file_location(name, HELPER_FILE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)

    secs = module.parse_sections(DOC_UNRELEASED)
    assert [s.title for s in secs] == ["[Unreleased]", "Pre-history (before the Python engine)"]
    sec = secs[0]
    assert isinstance(sec, tuple) and sec._fields == ("title", "body")
    assert module.require_entry(DOC_UNRELEASED, "bd#900").title == "[Unreleased]"


# --- amendment G: the duplicate-block merge must not drop a bullet ----------
# First <=60 chars (right-stripped) of every top-level bullet that lives in a
# NON-FIRST duplicate block of the CHANGELOG.md as of bd#212 RED (read from the
# worktree file, 2026-10-03). Whole-section hashes are deliberately not frozen.
_UNRELEASED_MERGED_BULLETS = (
    # second `### Added` block
    "- **Deterministic post-deploy report (bd#89 P3c).** Phase 7",
    "- **Each model invocation records an id and an output digest",
    "- **Host-adapter seam for `run_adversaries` (bd#195).** `run",
    "- **The subagent write guard now covers Bash, and state rewr",
    "- **Class-I inventory and lint; the remaining prompt fragment",
    "- **`effective_model` hook on `register_backend`; the hard-g",
    "- **Readiness review gate in engine Phase 6 (bd#141 item 6).",
    "- **`reverted` signal for companion tuning (bd#141 item 5).",
    "- **Readiness start gate in engine Phase 5 (bd#141 item 6).",
    "- **`check_bd_l2` reads real event logs (bd#155).**",
    "- **`bd_l3` host CLI; the checker reads real event logs (bd#",
    "- **`oracle verify` host CLI; the event-log reader refuses",
    "- **Role template declared on the `injections` channel in ev",
    "- **File-sourced prompt segments declared on the `injections",
    "- **Subprocess observed model (bd#141, item 4(e)).**",
    "- **Check ladder (bd#141, item 3).** New `bytedigger_engine/",
    "- **Close gate (bd#141, item 7).** New `bytedigger_engine/cl",
    "- **Claim-vs-evidence check and tool-call loop detector (bd#",
    "- **Subagent write guard (bd#133).** New PreToolUse hook `hoo",
    "- **Weekly companion tuning (bd#117, Part B).** New `scripts",
    "- **`plan-approved` readiness gate (bd#117, Part A).** A rep",
    "- **Verification skills registry (bd#115).** A `SKILL.md`",
    "- **Scored spec review (bd#115).** The phase 4.5 reviewer no",
    "- **Skill companions (#116).** A host can extend a core",
    # second `### Fixed` block
    "- **The package version is now checked against release tags",
    "- **A malformed oracle freeze row refuses instead of escapin",
    # third `### Fixed` block
    "- **An unreadable or malformed effort config no longer passe",
    "- **Callers can no longer forge adapter observations (bd#145",
    "- **Workers write their own deliverables (bd#127).**",
    "- **`ship.sh` ships commits ahead of the base, and the PR ca",
    "- **Phase 5 typecheck baseline sees untracked files (bd#168)",
    "- **D3 prohibition gate reads negations on word boundaries",
    "- **Cross-tree auto-revert keeps edits that predate the run",
)
_UNRELEASED_MIN_BULLETS = 59  # all top-level bullets of the section today

_V011_MERGED_BULLETS = (
    # second `### Added` block of [0.1.1]
    "- **Spec-writer rule 9** — NEW-symbol citation-form ban",
    "- **Agent-SDK stderr-tail capture** — LLM subprocess failure",
    "- **Starter `constitution.md`** — shipped in the repo root",
    "- Digger-1983-style promo card in docs. (#37)",
)
_V011_MIN_BULLETS = 8


def _assert_bullets_preserved(literals, min_bullets, title_prefix=None):
    cl = _cl()
    text = cl.read_changelog()
    sec = cl.require_entry(text, literals[0])
    if title_prefix is not None:
        assert sec.title.startswith(title_prefix), sec.title
    lines = sec.body.splitlines()
    missing = [lit for lit in literals if not any(ln.startswith(lit) for ln in lines)]
    assert missing == [], f"bullets lost from section {sec.title!r}: {missing}"
    count = sum(1 for ln in lines if ln.startswith("- "))
    assert count >= min_bullets, f"{sec.title!r} has {count} bullets, expected >= {min_bullets}"


def test_g_unreleased_merge_preserves_every_bullet_of_later_duplicate_blocks():
    # section located by its content (not by title) so a release cut keeps this valid
    _assert_bullets_preserved(_UNRELEASED_MERGED_BULLETS, _UNRELEASED_MIN_BULLETS)


def test_g_v011_merge_preserves_every_bullet_of_second_added_block():
    _assert_bullets_preserved(_V011_MERGED_BULLETS, _V011_MIN_BULLETS, "[0.1.1]")


# AC9
_PIN_TOKENS = ("re.search", "re.match", ".index(", ".find(", "startswith", "_section(")
_TOP_PIN_TOKENS = ("heads[1].start()", 'split("\\n## ")')


def test_ac9_no_unreleased_or_top_section_pins_in_other_tests():
    offenders: list[str] = []
    roots = [ENGINE / "tests", REPO_ROOT / "tests"]
    for root in roots:
        if not root.is_dir():
            continue
        for p in sorted(root.rglob("*.py")):
            if p.resolve() in (THIS_FILE, HELPER_FILE):
                continue
            try:
                lines = p.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeDecodeError):
                continue
            for n, line in enumerate(lines, 1):
                s = line.strip()
                if s.startswith("#"):
                    continue
                if "Unreleased" in line and any(t in line for t in _PIN_TOKENS):
                    offenders.append(f"{p.relative_to(REPO_ROOT)}:{n}: {s}")
                elif any(t in line for t in _TOP_PIN_TOKENS):
                    offenders.append(f"{p.relative_to(REPO_ROOT)}:{n}: {s}")
    assert offenders == [], "CHANGELOG section pins remain:\n" + "\n".join(offenders)
