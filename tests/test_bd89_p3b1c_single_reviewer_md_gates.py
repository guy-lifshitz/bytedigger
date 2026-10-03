"""bd#89 P3b1c RED (spec FROZEN r3).

Spec: docs/decisions/2026-10-03-bd89-p3b1c-single-reviewer-md-gates.md (section 3).

The orchestrator-flow md, the gate config parsers and bytedigger.json are brought to the
single composite reviewer reality. Reads the real repo files; no mocks, no engine import.

AC mapping (GUARD = green before GREEN):
    test_ac1_phase6_md_has_no_multi_reviewer_text                -> AC1
    test_ac2_phase6_md_names_role_composite_file                 -> AC2 (first clause, red)
    test_ac2_guard_phase6_md_keeps_verdict_pass                  -> AC2 (GUARD)
    test_ac2_guard_phase6_md_keeps_findings_skipped              -> AC2 (GUARD)
    test_ac3_policy_table_has_no_agent_count_rows                -> AC3
    test_ac3_no_n_agents_or_n_x_reviewers_lines                  -> AC3
    test_ac4_build_md_has_no_exact_agents_or_launched_expected   -> AC4
    test_ac4_build_md_6_1_references_phase6_md                   -> AC4
    test_ac5_classify_and_dynamic_context_have_no_multi_reviewer -> AC5
    test_ac5_classify_and_dynamic_context_say_composite          -> AC5
    test_f1_*  (phase-6 "Reviewer counts", build.md mismatch/SIMPLE=3, classify "3 for SIMPLE")
    test_ac8_removal_note_is_one_line_with_backticked_keys       -> AC8 (section 8/9)
    test_ac6_bytedigger_json_has_no_reviewers_count_keys         -> AC6 (first clause, red)
    test_ac6_guard_bytedigger_json_reviewers_mode_auto           -> AC6 (GUARD)
    test_ac7_gate_sources_have_no_legacy_reviewer_count_tokens   -> AC7
    test_ac7_guard_ts_gate_still_exports_parse_reviewer_mode     -> AC7 (GUARD)
    test_ac8_docs_do_not_name_legacy_keys_outside_removal_note   -> AC8
    test_ac8_plugin_md_mentions_composite_reviewer               -> AC8
    test_ac10_guard_no_decorrelated_verifier_text                -> AC10 (GUARD)
    test_ac11_guard_satisfaction_step_untouched                  -> AC11 (GUARD)
AC9 lives in tests/build-gate.bats (bash half) and post-review-gate.test.ts P6-F10-08 (TS half).
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

_LEGACY_KEYS = ("simple_reviewers", "feature_reviewers", "complex_reviewers")


def _read(rel: str) -> str:
    return (REPO / rel).read_text(encoding="utf-8")


# ─── AC1 ─────────────────────────────────────────────────────────────────────

_AC1_FORBIDDEN = [
    "phase_6_reviewers_launched", "phase_6_reviewers_expected", "<3|4|6|7>",
    "Determine Reviewer Count", "Launch ALL agents", "2 Haiku", "reviews/{agent-name}.md",
    "launched != expected", "launched ≠ expected",
]  # section 9: structural tokens only ("Reviewer counts" is test_f1_phase6_md_...)


def test_ac1_phase6_md_has_no_multi_reviewer_text():
    text = _read("phases/phase-6-review.md")
    present = [s for s in _AC1_FORBIDDEN if s in text]
    assert not present, present


# ─── AC2 ─────────────────────────────────────────────────────────────────────

def test_ac2_phase6_md_names_role_composite_file():
    assert "reviews/role-composite.md" in _read("phases/phase-6-review.md")


def test_ac2_guard_phase6_md_keeps_verdict_pass():
    assert "VERDICT: PASS" in _read("phases/phase-6-review.md")


def test_ac2_guard_phase6_md_keeps_findings_skipped():
    assert "phase_6_findings_skipped" in _read("phases/phase-6-review.md")


# ─── AC3 ─────────────────────────────────────────────────────────────────────

def test_ac3_policy_table_has_no_agent_count_rows():
    rows = [ln for ln in _read("phases/phase-6-review.md").splitlines()
            if re.search(r"^\|.*\|\s*[2-9]:", ln)]
    assert not rows, rows


def test_ac3_no_n_agents_or_n_x_reviewers_lines():
    lines = [ln for ln in _read("phases/phase-6-review.md").splitlines()
             if re.search(r"[3467] agents", ln) or re.search(r"[3467]x reviewers", ln)]
    assert not lines, lines


# ─── AC4 ─────────────────────────────────────────────────────────────────────

def test_ac4_build_md_has_no_exact_agents_or_launched_expected():
    text = _read("commands/build.md")
    present = [s for s in ("EXACT agents", "launched ≠ expected", "launched != expected") if s in text]
    assert not present, present


def test_ac4_build_md_6_1_references_phase6_md():
    text = _read("commands/build.md")
    m = re.search(r"6\.1\b.*?(?=6\.2\b)", text, re.S)
    assert m, "no 6.1 block found in commands/build.md"
    assert "phases/phase-6-review.md" in m.group(0)


# ─── AC5 ─────────────────────────────────────────────────────────────────────

_AC5_FILES = ("phases/phase-0-classify.md", "templates/dynamic-context.md")


def test_ac5_classify_and_dynamic_context_have_no_multi_reviewer():
    bad = {}
    for rel in _AC5_FILES:
        text = _read(rel)
        hit = [s for s in ("3x reviewers", "6x reviewers", "<3|4|6|7>", "Review Agent Roster",
                           "phase_6_reviewers", "launched != expected", "launched ≠ expected")
               if s in text]
        if hit:
            bad[rel] = hit
    assert not bad, bad


def test_ac5_dynamic_context_has_no_per_tier_reviewer_count_rows():
    text = _read("templates/dynamic-context.md")
    rows = re.findall(r"^\|\s*[A-Z][A-Z/+ ]*\|\s*[2-9]\s*\|.*$", text, re.M)
    assert not rows, rows


def test_ac5_classify_and_dynamic_context_say_composite():
    missing = [rel for rel in _AC5_FILES if "1x composite reviewer" not in _read(rel)]
    assert not missing, missing


# ─── AC6 ─────────────────────────────────────────────────────────────────────

def test_ac6_bytedigger_json_has_no_reviewers_count_keys():
    cfg = json.loads(_read("bytedigger.json"))
    bad = [k for k in cfg if re.fullmatch(r".*_reviewers", k)]
    assert not bad, bad


def test_ac6_guard_bytedigger_json_reviewers_mode_auto():
    cfg = json.loads(_read("bytedigger.json"))
    assert cfg["reviewers"] == {"mode": "auto"}


# ─── AC7 ─────────────────────────────────────────────────────────────────────

def test_ac7_gate_sources_have_no_legacy_reviewer_count_tokens():
    # F4: case-insensitive, so FEATURE_REVIEWERS / COMPLEX_REVIEWERS are covered too.
    tokens = ("simple_reviewers", "feature_reviewers", "complex_reviewers", "parseReviewerCount")
    bad = {}
    for rel in ("scripts/build-gate.sh", "scripts/ts/build-phase-gate.ts"):
        text = _read(rel).lower()
        hit = [t for t in tokens if t.lower() in text]
        if hit:
            bad[rel] = hit
    assert not bad, bad


def test_ac7_guard_ts_gate_still_exports_parse_reviewer_mode():
    assert re.search(r"export\s+(?:async\s+)?(?:function|const)\s+parseReviewerMode\b",
                     _read("scripts/ts/build-phase-gate.ts"))


# ─── AC8 ─────────────────────────────────────────────────────────────────────

def test_ac8_docs_do_not_name_legacy_keys_outside_removal_note():
    bad = {}
    for rel in ("docs/plugin.md", "README.md"):
        lines = _read(rel).splitlines()
        if rel == "docs/plugin.md":
            lines = [ln for ln in lines if "were removed and are ignored" not in ln]
        hit = sorted({k for ln in lines for k in _LEGACY_KEYS if k in ln})
        if hit:
            bad[rel] = hit
    assert not bad, bad


def test_ac8_plugin_md_mentions_composite_reviewer():
    assert "composite reviewer" in _read("docs/plugin.md")


def test_ac8_removal_note_is_one_line_with_backticked_keys():
    # Spec section 8: one physical line, keys backticked (the engine sibling
    # test_ac12_docs_rows_say_one_composite_reviewer[docs/plugin.md] needs that shape).
    lines = [ln for ln in _read("docs/plugin.md").splitlines() if "were removed and are ignored" in ln]
    assert len(lines) == 1, lines
    for key in _LEGACY_KEYS:
        assert f"`{key}`" in lines[0], key
    assert "one composite reviewer" in lines[0].lower()


# ─── Errata r2 (gate r1) + section 9 narrowing: structural count tokens ──────

def test_f1_phase6_md_has_no_reviewer_counts_phrase():
    assert "reviewer counts" not in _read("phases/phase-6-review.md").lower()


def test_f1_build_md_has_no_reviewer_count_mismatch_or_simple_3():
    text = _read("commands/build.md")
    present = [s for s in ("Reviewer count mismatch", "SIMPLE=3") if s in text]
    assert not present, present


def test_f1_classify_md_dry_run_has_no_3_for_simple():
    assert "3 for SIMPLE" not in _read("phases/phase-0-classify.md")


# ─── AC10 (GUARD) ────────────────────────────────────────────────────────────

_SKIP_TOP = {"docs/decisions", "engine_py"}
_SKIP_PARTS = {"node_modules", ".git", "__tests__"}
_SKIP_FILES = {"CHANGELOG.md", "docs/configuration.md"}


def test_ac10_guard_no_decorrelated_verifier_text():
    # A1: tracked files only (git ls-files), so untracked artifacts cannot redden the guard.
    out = subprocess.run(["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True).stdout
    this_file = Path(__file__).resolve().relative_to(REPO).as_posix()
    hits = []
    for relp in out.splitlines():
        path = REPO / relp
        rel = Path(relp)
        if path.suffix not in (".md", ".sh", ".ts", ".json") or not path.is_file():
            continue
        if relp == this_file:
            continue
        if _SKIP_PARTS & set(rel.parts):
            continue
        if any(relp == s or relp.startswith(s + "/") for s in _SKIP_TOP):
            continue
        if relp in _SKIP_FILES:
            continue
        if "decorr" in path.read_text(encoding="utf-8", errors="replace").lower():
            hits.append(relp)
    assert not hits, hits


# ─── AC11 (GUARD) ────────────────────────────────────────────────────────────

def test_ac11_guard_satisfaction_step_untouched():
    text = _read("phases/phase-6-review.md")
    assert "3-Agent Majority Vote" in text
    assert "Launch 3 Opus agents in parallel" in text
    thresholds = json.loads(_read("bytedigger.json"))["satisfaction_thresholds"]
    assert (thresholds["SIMPLE"], thresholds["FEATURE"], thresholds["COMPLEX"]) == (80, 85, 90)
