"""RED tests for bd#90 - review aggregation resolves an unparseable citation by reading the file.

Spec: docs/decisions/2026-10-10-bd90-review-citation-fallback.md (AC1-AC17, r2).

Unit under test: `_aggregate_review_findings` and `_persist_fix_feed` in
workflows/phase_6_review.py. New names (ABSENT pre-GREEN): `_resolve_unquoted_citation`,
verify status `verified-resolved`, reason `OK-RESOLVED-BY-READ`, event
`review_citation_resolved`, gate flag `HAL_REVIEW_CITATION_FALLBACK` (default ON).

Fixtures are real files: a scratchpad with `reviews/role-composite.md` and a worktree
root holding `src/mod.py` (30 lines), driven through the real aggregator with a
SimpleNamespace ctx (org_config scratchpad_dir + current_worktree_path). Events are
captured by replacing the module's `_emit_safe` (same seam as test_gh1591_fix_gate_boundary).
Pre-GREEN every test fails at assert time: the new private symbol is touched only after
an explicit existence assertion. No sleeps, no network, no model calls, no singleton or
timing-contended resource (workflows.md section 1i): every input is pre-staged on tmp_path.
No sys.path mutation.
"""
from __future__ import annotations

import subprocess
import sys
import types
from pathlib import Path

import pytest

from bytedigger_engine import llm_subprocess
from bytedigger_engine.contracts import StepResult
from bytedigger_engine.llm_subprocess import reset_backends
from bytedigger_engine.workflows import phase_6_review as p6

REPO_ROOT = Path(__file__).resolve().parents[2]
FLAG = "HAL_REVIEW_CITATION_FALLBACK"
FLAG_ALIASES = ("BD_REVIEW_CITATION_FALLBACK", "BYTEDIGGER_REVIEW_CITATION_FALLBACK")
RESOLVED_EVENT = "review_citation_resolved"
OK_RESOLVED = "OK-RESOLVED-BY-READ"
SUSPECT_SECTION = "## Suspect Findings (LOW CONFIDENCE)"
WITHHELD_NOTICE = "## Suspect Findings Withheld"
MOD_LINES = [f"value_{i} = {i}" for i in range(1, 31)]  # src/mod.py, 30 lines
MOD_LINES[19] = ""        # line 20 is blank
MOD_LINES[20] = "    "    # line 21 is whitespace-only
MARKER = "SUSPECT FINDINGS WITHHELD FROM FIX WORKER:"


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    for name in (FLAG, *FLAG_ALIASES):
        monkeypatch.delenv(name, raising=False)
    yield
    reset_backends()


class _World:
    """Scratchpad + worktree root on tmp_path, plus the captured event rows."""

    def __init__(self, tmp_path: Path, monkeypatch) -> None:
        self.tmp = tmp_path
        self.scratch = tmp_path / "scratch"
        self.wt = tmp_path / "wt"
        (self.wt / "src").mkdir(parents=True)
        (self.wt / "src" / "mod.py").write_text("\n".join(MOD_LINES) + "\n", encoding="utf-8")
        self.scratch.mkdir()
        self.events: list[dict] = []
        monkeypatch.setattr(
            p6, "_emit_safe", lambda et, payload, **kw: self.events.append({"type": et, "payload": payload})
        )
        self.ctx = types.SimpleNamespace(
            org_config={"scratchpad_dir": str(self.scratch), "current_worktree_path": str(self.wt)},
            question="q",
        )

    def rows(self, event_type: str) -> list[dict]:
        return [e["payload"] for e in self.events if e["type"] == event_type]

    def run(self, blocks: list[str]) -> StepResult:
        reviews = self.scratch / "reviews"
        reviews.mkdir(parents=True, exist_ok=True)
        text = "# composite Review\n\n" + "\n".join(blocks) + f"\nVERDICT: PARTIAL\n<!-- role-findings-count: {len(blocks)} -->\n"
        (reviews / "role-composite.md").write_text(text, encoding="utf-8")
        prev = StepResult(status="ok", data={}, duration_ms=0, step_name="x")
        result = p6._aggregate_review_findings(self.ctx, prev)
        assert result.status == "ok", f"setup: aggregation failed: {result.error!r}"
        return result

    def fix_doc(self, result: StepResult) -> str:
        doc_path = self.tmp / "out" / "review.md"
        doc_path.parent.mkdir(parents=True, exist_ok=True)
        data = result.data
        fix = p6._persist_fix_feed(
            doc_path, data["aggregated_content"], data["verified_findings"], data["suspect_findings"],
            data["verdict"], suspect_withhold=data["suspect_withhold"],
        )
        assert not isinstance(fix, StepResult), f"setup: fix doc write failed: {fix!r}"
        return fix.read_text(encoding="utf-8")


@pytest.fixture
def world(tmp_path, monkeypatch) -> _World:
    return _World(tmp_path, monkeypatch)


def _finding(severity: str, title: str, cite_lines: list[str]) -> str:
    body = "\n".join(cite_lines)
    return (
        f"### SEVERITY: {severity} — {title}\n"
        f"{body}\n"
        "Confidence: HIGH\n"
        "Description: the guard is missing here\n"
    )


def _file_cited(title: str, cite: str = "src/mod.py:12", severity: str = "HIGH") -> str:
    return _finding(severity, title, [f"**File:** {cite}"])


def _three_file_cited() -> list[str]:
    return [_file_cited(f"cite-only-{i}") for i in range(3)]


def _evidence(path: str, line: int, text: str | None = None) -> str:
    return f"> {path}:{line}: {MOD_LINES[line - 1] if text is None else text}"


def _split(content: str) -> tuple[str, str]:
    """(aggregated section, suspect section) of the composite review content."""
    tail = content.split("## Aggregated Findings\n", 1)[1].split("\n## Summary", 1)[0]
    head, _, suspect = tail.partition(p6.SUSPECT_FINDINGS_SECTION_HEADER)
    return head, suspect


def _all_findings(result: StepResult) -> dict[str, dict]:
    data = result.data
    return {f["title"]: f for f in data["verified_findings"] + data["suspect_findings"]}


def _last_line(finding: dict) -> str:
    return finding["block"].rstrip().splitlines()[-1]


def _suspect_part(doc: str) -> str:
    assert SUSPECT_SECTION in doc, "fix doc has no LOW CONFIDENCE suspect section"
    return doc.split(SUSPECT_SECTION, 1)[1]


# ---------------------------------------------------------------- AC1

@pytest.mark.parametrize("env", [None, "1"], ids=["unset", "one"])
def test_ac1_file_line_citation_is_resolved_not_withheld(world, monkeypatch, env):
    if env is not None:
        monkeypatch.setenv(FLAG, env)
    result = world.run(_three_file_cited())
    agg, suspect = _split(result.data["aggregated_content"])
    for i in range(3):
        assert f"cite-only-{i}" in agg
        assert f"cite-only-{i}" not in suspect
    assert len(result.data["verified_findings"]) == 3
    assert result.data["suspect_withhold"] is False
    assert result.data["verdict"] == p6.VERDICT_FAIL
    evidence = _evidence("src/mod.py", 12)
    for f in result.data["verified_findings"]:
        assert _last_line(f) == evidence
    doc = world.fix_doc(result)
    for i in range(3):
        assert f"cite-only-{i}" in doc
    assert doc.splitlines().count(evidence) == 3
    assert WITHHELD_NOTICE not in doc


# ---------------------------------------------------------------- AC2

def test_ac2_range_citation_is_resolved_with_first_line_evidence(world):
    blocks = [_finding("HIGH", f"range-{i}", ["> src/mod.py:10-14: some text"]) for i in range(3)]
    result = world.run(blocks)
    found = _all_findings(result)
    for i in range(3):
        f = found[f"range-{i}"]
        assert f["verify_status"] == "verified-resolved"
        assert f["verify_reason"] == OK_RESOLVED
        assert _last_line(f) == _evidence("src/mod.py", 10)
    assert result.data["suspect_findings"] == []


# ---------------------------------------------------------------- AC3

def test_ac3_path_without_line_is_not_resolved_but_fed(world):
    blocks = [_finding("HIGH", f"pathonly-{i}", ["Location: src/mod.py"]) for i in range(3)]
    result = world.run(blocks)
    found = _all_findings(result)
    for i in range(3):
        assert found[f"pathonly-{i}"]["verify_status"] == "suspect-no-quote"
    assert result.data["suspect_withhold"] is False
    doc = world.fix_doc(result)
    assert WITHHELD_NOTICE not in doc
    suspect = _suspect_part(doc)
    for i in range(3):
        assert f"pathonly-{i}" in suspect


# ---------------------------------------------------------------- AC4

def test_ac4_line_beyond_file_is_not_resolved_and_reaches_fix_doc(world):
    blocks = [_file_cited("good-a"), _file_cited("good-b"), _file_cited("beyond-end", cite="src/mod.py:999")]
    result = world.run(blocks)
    found = _all_findings(result)
    assert found["good-a"]["verify_status"] == "verified-resolved"
    assert found["good-b"]["verify_status"] == "verified-resolved"
    assert found["beyond-end"]["verify_status"] == "suspect-no-quote"
    assert result.data["suspect_withhold"] is False
    doc = world.fix_doc(result)
    assert "beyond-end" in _suspect_part(doc)
    assert WITHHELD_NOTICE not in doc


# ---------------------------------------------------------------- AC5

def test_ac5_unresolvable_findings_are_unrated_and_always_fed(world):
    blocks = [
        _file_cited("absent-file", cite="src/missing.py:3"),
        _finding("HIGH", "no-path", ["The caller ignores the error."]),
        _file_cited("out-of-range", cite="src/mod.py:999", severity="MEDIUM"),
    ]
    result = world.run(blocks)
    found = _all_findings(result)
    for title in ("absent-file", "no-path", "out-of-range"):
        assert found[title]["verify_status"] == "suspect-no-quote"
    assert result.data["suspect_rate"] == 0.0
    assert result.data["suspect_rate_threshold_exceeded"] is False
    assert result.data["suspect_withhold"] is False
    assert world.rows("reviewer_suspect_rate") == []
    doc = world.fix_doc(result)
    assert WITHHELD_NOTICE not in doc
    suspect = _suspect_part(doc)
    for title in ("absent-file", "no-path", "out-of-range"):
        assert title in suspect


# ---------------------------------------------------------------- AC6

def test_ac6_escape_and_absolute_paths_are_never_resolved(world):
    outside = world.tmp / "outside.py"
    outside.write_text("secret = 1\n", encoding="utf-8")
    inside_abs = world.wt / "src" / "mod.py"
    blocks = [
        _file_cited("inside"),
        _file_cited("escapes", cite="../outside.py:1"),
        _file_cited("abs-outside", cite=f"{outside}:1"),
        _file_cited("abs-inside", cite=f"{inside_abs}:12"),
    ]
    result = world.run(blocks)
    found = _all_findings(result)
    assert found["inside"]["verify_status"] == "verified-resolved"
    for title in ("escapes", "abs-outside", "abs-inside"):
        assert found[title]["verify_status"] == "suspect-no-quote", title
        assert found[title]["verify_reason"] != OK_RESOLVED, title


# ---------------------------------------------------------------- AC7

def test_ac7_matching_quote_is_unchanged_and_emits_no_resolved_event(world):
    quoted = _finding("HIGH", "has-quote", [f"> src/mod.py:12: {MOD_LINES[11]}"])
    blocks = [quoted, _file_cited("cite-a"), _file_cited("cite-b")]
    result = world.run(blocks)
    found = _all_findings(result)
    assert found["has-quote"]["verify_status"] == "verified-exact"
    assert found["has-quote"]["block"].count("> src/mod.py:12:") == 1
    assert found["cite-a"]["verify_status"] == "verified-resolved"
    resolved_titles = sorted(r["title"] for r in world.rows(RESOLVED_EVENT))
    assert resolved_titles == ["cite-a", "cite-b"]


# ---------------------------------------------------------------- AC8

def test_ac8_mixed_population_withholds_only_the_rated_mismatches(world):
    blocks = [_finding("HIGH", f"mismatch-{i}", ["> src/mod.py:12: not the line"]) for i in range(3)]
    blocks.append(_file_cited("format-only"))
    blocks.append(_finding("HIGH", "no-path", ["The caller ignores the error."]))
    result = world.run(blocks)
    found = _all_findings(result)
    for i in range(3):
        assert found[f"mismatch-{i}"]["verify_status"] == "suspect-no-match"
    assert found["format-only"]["verify_status"] == "verified-resolved"
    assert found["no-path"]["verify_status"] == "suspect-no-quote"
    assert result.data["suspect_rate"] == 0.75
    assert result.data["suspect_withhold"] is True
    doc = world.fix_doc(result)
    assert WITHHELD_NOTICE in doc
    assert "3 suspect finding(s) withheld" in doc
    for i in range(3):
        assert f"mismatch-{i}" not in doc
    assert "format-only" in doc
    assert "no-path" in _suspect_part(doc)
    withheld = world.rows("fix_feed_suspect_withheld")
    assert len(withheld) == 1
    assert withheld[0]["withheld_count"] == 3
    # no unresolved no-quote finding is counted as withheld or rated anywhere
    rate_rows = world.rows("reviewer_suspect_rate")
    assert len(rate_rows) == 1 and rate_rows[0]["suspect_count"] == 3
    for warn in world.rows("reviewer_grep_accuracy_warning"):
        assert "no-path" not in [fid["title"] for fid in warn["finding_ids"]]
    marker = [ln for ln in result.data["aggregated_content"].splitlines() if ln.startswith(MARKER)]
    assert len(marker) == 1 and marker[0].endswith("withheld=3")


# ---------------------------------------------------------------- AC16b

def test_ac16b_composite_marker_agrees_with_the_fix_feed(world):
    blocks = [_finding("HIGH", f"mismatch-{i}", ["> src/mod.py:12: not the line"]) for i in range(3)]
    blocks.append(_file_cited("format-only"))
    blocks.append(_finding("HIGH", "no-path", ["The caller ignores the error."]))
    result = world.run(blocks)
    marker = [ln for ln in result.data["aggregated_content"].splitlines() if ln.startswith(MARKER)]
    assert marker == [f"{MARKER} rate=0.75 threshold=0.4 withheld=3"]
    world.fix_doc(result)
    withheld = world.rows("fix_feed_suspect_withheld")
    assert len(withheld) == 1
    assert marker[0].endswith(f"withheld={withheld[0]['withheld_count']}")


# ---------------------------------------------------------------- AC9

def test_ac9_kill_switch_restores_previous_behaviour(world, monkeypatch):
    monkeypatch.setenv(FLAG, "0")
    calls: list[tuple] = []
    assert hasattr(p6, "_resolve_unquoted_citation"), "_resolve_unquoted_citation must exist"
    monkeypatch.setattr(p6, "_resolve_unquoted_citation", lambda *a, **kw: calls.append(a))
    result = world.run(_three_file_cited())
    found = _all_findings(result)
    for i in range(3):
        f = found[f"cite-only-{i}"]
        assert f["verify_status"] == "suspect-no-quote"
        assert "> src/mod.py:12:" not in f["block"]
    assert result.data["suspect_withhold"] is True
    assert calls == []
    assert world.rows(RESOLVED_EVENT) == []
    doc = world.fix_doc(result)
    assert WITHHELD_NOTICE in doc


# ---------------------------------------------------------------- AC10

def test_ac10_one_event_row_per_resolved_finding(world):
    blocks = _three_file_cited() + [_file_cited("unresolvable", cite="src/missing.py:3")]
    world.run(blocks)
    rows = world.rows(RESOLVED_EVENT)
    assert sorted(r["title"] for r in rows) == [f"cite-only-{i}" for i in range(3)]
    for row in rows:
        assert row["phase"] == "phase_6_review"
        assert row["reason"] == OK_RESOLVED
        assert row["path"] == "src/mod.py"
        assert row["line"] == 12
        assert row["role"] == "composite"
        assert row["severity"] == "HIGH"
    final = {r["title"]: r["verify_status"] for r in world.rows("composite_finding_quote_verified")}
    assert final["cite-only-0"] == "verified-resolved"
    assert final["unresolvable"] == "suspect-no-quote"


# ---------------------------------------------------------------- AC11

@pytest.mark.parametrize("api_key", [None, "sk-test-not-real"], ids=["key-unset", "key-set"])
def test_ac11_resolution_calls_no_llm_entry(world, monkeypatch, api_key):
    if api_key is None:
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    else:
        monkeypatch.setenv("ANTHROPIC_API_KEY", api_key)
    spy_calls: list[tuple[str, dict]] = []

    def _spy_for(name: str):
        def _spy(*args, **kwargs):
            spy_calls.append((name, kwargs))
            return StepResult(status="ok", data={"raw_response": ""}, duration_ms=1, step_name="spy")
        return _spy

    monkeypatch.setattr(llm_subprocess, "invoke_llm_subprocess", _spy_for("invoke_llm_subprocess"))
    for name in list(llm_subprocess._BACKENDS):
        monkeypatch.setitem(llm_subprocess._BACKENDS, name, _spy_for(name))
    result = world.run(_three_file_cited())
    found = _all_findings(result)
    for i in range(3):
        assert found[f"cite-only-{i}"]["verify_status"] == "verified-resolved"
    assert spy_calls == []


# ---------------------------------------------------------------- AC12

def test_ac12_flag_catalogued_and_owner_lint_passes():
    from bytedigger_engine import flags_catalog  # noqa: PLC0415

    assert FLAG in flags_catalog.FLAGS, "kill switch must be catalogued"
    entry = flags_catalog.FLAGS[FLAG]
    assert entry.get("kind") == "gate"
    assert entry.get("default") == "1"
    assert isinstance(entry.get("description"), str) and entry["description"].strip()
    assert isinstance(entry.get("owner"), str) and entry["owner"].strip()
    assert str(entry.get("provenance", "")).startswith("introduced:")

    lint = REPO_ROOT / "scripts" / "flag_owner_lint.py"
    result = subprocess.run([sys.executable, str(lint)], capture_output=True, text=True,
                            cwd=str(REPO_ROOT))
    assert result.returncode == 0, f"{result.stdout!r} {result.stderr!r}"


# ---------------------------------------------------------------- AC14 (guard)

def test_ac14_changelog_fixed_block_mentions_flag():
    from helpers.changelog import read_changelog, require_entry  # noqa: PLC0415

    require_entry(read_changelog(), FLAG, block="Fixed")


# ---------------------------------------------------------------- AC15

def test_ac15_oversized_cited_file_is_not_resolved(world):
    big = world.wt / "src" / "big.py"
    big.write_text("x\n" * (p6._QUOTE_FILE_MAX_BYTES // 2 + 1), encoding="utf-8")
    assert big.stat().st_size > p6._QUOTE_FILE_MAX_BYTES
    blocks = [_file_cited("small-ok"), _file_cited("too-big", cite="src/big.py:1")]
    result = world.run(blocks)
    found = _all_findings(result)
    assert found["small-ok"]["verify_status"] == "verified-resolved"
    assert found["too-big"]["verify_status"] == "suspect-no-quote"


# ---------------------------------------------------------------- AC16

def test_ac16_fallback_dir_resolves_and_worktree_wins(world):
    spec_dir = world.scratch / "reviews"
    spec_dir.mkdir(parents=True, exist_ok=True)
    (spec_dir / "spec.md").write_text("spec line one\nspec line two\nspec line three\n", encoding="utf-8")
    (world.wt / "shared").mkdir()
    (world.scratch / "shared").mkdir()
    (world.wt / "shared" / "both.py").write_text("worktree text\n", encoding="utf-8")
    (world.scratch / "shared" / "both.py").write_text("scratch text\n", encoding="utf-8")
    blocks = [
        _file_cited("scratch-only", cite="reviews/spec.md:2"),
        _file_cited("in-both", cite="shared/both.py:1"),
        _file_cited("plain"),
    ]
    result = world.run(blocks)
    found = _all_findings(result)
    assert found["scratch-only"]["verify_status"] == "verified-resolved"
    assert _last_line(found["scratch-only"]) == "> reviews/spec.md:2: spec line two"
    assert found["in-both"]["verify_status"] == "verified-resolved"
    assert _last_line(found["in-both"]) == "> shared/both.py:1: worktree text"


# ---------------------------------------------------------------- AC17

def test_ac17_url_and_version_tokens_are_skipped_first_resolving_wins(world):
    # files that exist locally, so only the skip rule keeps these tokens out
    (world.wt / "example.com").mkdir()
    (world.wt / "example.com" / "a.py").write_text("u1\nu2\nu3\nu4\n", encoding="utf-8")
    (world.wt / "1.2.3").write_text("v1\nv2\nv3\nv4\nv5\n", encoding="utf-8")
    blocks = [
        _finding("HIGH", "tokens-a", ["See https://example.com/a.py:3 and 1.2.3:4", "**File:** src/mod.py:12"]),
        _file_cited("tokens-b"),
        _file_cited("tokens-c"),
    ]
    result = world.run(blocks)
    f = _all_findings(result)["tokens-a"]
    assert f["verify_status"] == "verified-resolved"
    assert _last_line(f) == _evidence("src/mod.py", 12)


def test_ac17_first_resolving_candidate_wins_over_later_ones(world):
    (world.wt / "src" / "other.py").write_text("o1\no2\no3\no4\n", encoding="utf-8")
    blocks = [
        _finding("HIGH", "two-ok", ["**File:** src/mod.py:12", "**File:** src/other.py:3"]),
        _finding("HIGH", "gone-first", ["**File:** src/gone.py:3", "**File:** src/mod.py:12"]),
        _file_cited("plain"),
    ]
    found = _all_findings(world.run(blocks))
    assert found["two-ok"]["verify_status"] == "verified-resolved"
    assert _last_line(found["two-ok"]) == _evidence("src/mod.py", 12)
    assert found["gone-first"]["verify_status"] == "verified-resolved"
    assert _last_line(found["gone-first"]) == _evidence("src/mod.py", 12)


def test_ac17_url_tail_is_not_a_candidate(world):
    (world.wt / "example.com" / "src").mkdir(parents=True)
    blocks = [
        _finding("HIGH", "url-only", ["See https://example.com/src/mod.py:12 for details"]),
        _file_cited("fine-a"),
        _file_cited("fine-b"),
    ]
    found = _all_findings(world.run(blocks))
    assert found["fine-a"]["verify_status"] == "verified-resolved"
    assert found["url-only"]["verify_status"] == "suspect-no-quote"


def test_ac17_backtick_and_parenthesised_tokens_resolve(world):
    blocks = [
        _finding("HIGH", "ticks", ["**File:** `src/mod.py:12`"]),
        _finding("HIGH", "parens", ["Seen in the loop (src/mod.py:12)."]),
        _finding("HIGH", "trailing", ["Seen at src/mod.py:12."]),
    ]
    found = _all_findings(world.run(blocks))
    for title in ("ticks", "parens", "trailing"):
        assert found[title]["verify_status"] == "verified-resolved", title
        assert _last_line(found[title]) == _evidence("src/mod.py", 12), title


def test_ac17_blank_target_line_is_not_resolved(world):
    blocks = [
        _file_cited("blank-line", cite="src/mod.py:20"),
        _file_cited("space-line", cite="src/mod.py:21"),
        _file_cited("fine"),
    ]
    found = _all_findings(world.run(blocks))
    assert found["fine"]["verify_status"] == "verified-resolved"
    assert found["blank-line"]["verify_status"] == "suspect-no-quote"
    assert found["space-line"]["verify_status"] == "suspect-no-quote"


def test_ac17_candidate_scan_is_capped_at_five(world):
    misses = [f"src/nope{i}.py:1" for i in range(1, 6)]
    cites = [f"**File:** {m}" for m in misses] + ["**File:** src/mod.py:12"]
    blocks = [_finding("HIGH", "sixth-only", cites), _file_cited("fine-a"), _file_cited("fine-b")]
    result = world.run(blocks)
    found = _all_findings(result)
    assert found["fine-a"]["verify_status"] == "verified-resolved"
    assert found["sixth-only"]["verify_status"] == "suspect-no-quote"


def test_ac17_extensionless_token_is_not_a_candidate(world):
    (world.wt / "Makefile").write_text("a\nb\nc\nd\n", encoding="utf-8")
    (world.wt / "src" / "mod").write_text("a\nb\nc\nd\n", encoding="utf-8")
    blocks = [
        _file_cited("makefile", cite="Makefile:3"),
        _file_cited("noext", cite="src/mod:3"),
        _file_cited("fine"),
    ]
    result = world.run(blocks)
    found = _all_findings(result)
    assert found["fine"]["verify_status"] == "verified-resolved"
    assert found["makefile"]["verify_status"] == "suspect-no-quote"
    assert found["noext"]["verify_status"] == "suspect-no-quote"
