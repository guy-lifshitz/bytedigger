"""bd#128 RED: $CONSTITUTION_BLOCK / $QUALITY_GATE_BLOCK are defined in one plugin document.

Spec: docs/decisions/2026-10-12-bd128-injection-blocks-defined.md (AC1-AC8, r2).
Reads the real repo files; no mocks, no engine import.

    test_ac1_*  test_ac2_*  test_ac3_*  test_ac4_*  test_ac5_*  test_ac6_*  test_ac8_*  red
    test_ac7_guard_*                                                       green now
"""
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
DOC = "templates/injection-blocks.md"
VARS = ("$CONSTITUTION_BLOCK", "$QUALITY_GATE_BLOCK")
PLUGIN_DIRS = ("phases", "commands", "templates", "skills", "scripts")
BOY_SCOUT = "Boy Scout Rule: leave every file you touch CLEANER than you found it."


def _read(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


def _plugin_md():
    for d in PLUGIN_DIRS:
        for p in sorted((REPO / d).rglob("*.md")):
            yield p.relative_to(REPO).as_posix(), p.read_text(encoding="utf-8")


def _headings(text: str):
    out, fenced = [], False
    for ln in text.splitlines():
        if ln.lstrip().startswith("```"):
            fenced = not fenced
        elif not fenced and re.match(r"^#{1,6}\s", ln):
            out.append(ln)
    return out


def _section(text: str, var: str) -> str:
    lines, out, inside, fenced = text.splitlines(), [], False, False
    for ln in lines:
        if ln.lstrip().startswith("```"):
            fenced = not fenced
        is_head = not fenced and re.match(r"^#{1,6}\s", ln) is not None
        if is_head:
            inside = var in ln
        if inside:
            out.append(ln)
    assert out, f"no section for {var}"
    return "\n".join(out)


def test_ac1_doc_has_one_heading_per_variable():
    assert (REPO / DOC).is_file(), f"{DOC} missing"
    heads = _headings(_read(DOC))
    for var in VARS:
        assert sum(var in h for h in heads) == 1, (var, heads)


def test_ac2_each_variable_has_exactly_one_definition_heading_in_the_plugin_tree():
    for var in VARS:
        defs = [(rel, h) for rel, t in _plugin_md() for h in _headings(t) if var in h]
        assert len(defs) == 1, (var, defs)


def test_ac3_constitution_section_names_config_key():
    sec = _section(_read(DOC), "$CONSTITUTION_BLOCK")
    assert "constitution_path" in sec and "bytedigger.json" in sec


def test_ac4_quality_gate_section_has_boy_scout_and_test_integrity():
    sec = _section(_read(DOC), "$QUALITY_GATE_BLOCK")
    assert "Boy Scout" in sec and "TEST INTEGRITY" in sec
    assert re.search(r"never\s+(adjust|modify|change)[^.\n]*assertion", sec, re.I), sec
    assert re.search(r"orchestrator[^.\n]*(add|append)|(add|append)[^.\n]*orchestrator", sec, re.I), sec
    assert not re.search(r"engine[^.\n]*writes[^.\n]*TEST INTEGRITY", sec, re.I), sec


def test_ac5_every_phase5_mention_points_to_the_definition():
    lines = [ln for ln in _read("phases/phase-5-implement.md").splitlines()
             if any(v in ln for v in VARS)]
    assert lines, "phase-5 no longer mentions the blocks"
    bad = [ln for ln in lines if DOC not in re.sub(r'"[^"]*"', "", ln)]
    assert not bad, bad


def test_ac5b_assembly_and_default_are_stated():
    text = _read(DOC)
    for tok in ("RED", "Gherkin", "GREEN", "./constitution.md"):
        assert tok in text, tok
    assert text.index("$CONSTITUTION_BLOCK") < text.index("$QUALITY_GATE_BLOCK")
    assert re.search(r"order|then", text, re.I)


def test_ac6_no_undefined_block_token_in_plugin_docs():
    used, defined = set(), set()
    for _, t in _plugin_md():
        used |= set(re.findall(r"\$[A-Z][A-Z_]*_BLOCK\b", t))
        for h in _headings(t):
            defined |= set(re.findall(r"\$[A-Z][A-Z_]*_BLOCK\b", h))
    assert used <= defined, sorted(used - defined)


def test_ac7_guard_boy_scout_sentence_matches_engine_text():
    engine = _read("engine_py/bytedigger_engine/workflows/phase_05_inject.py")
    assert BOY_SCOUT in engine
    if (REPO / DOC).is_file():
        assert BOY_SCOUT in _read(DOC)


def test_ac8_changelog_unreleased_mentions_bd128():
    text = _read("CHANGELOG.md")
    unreleased = text.split("## [Unreleased]", 1)[1].split("\n## [", 1)[0]
    assert "bd#128" in unreleased
