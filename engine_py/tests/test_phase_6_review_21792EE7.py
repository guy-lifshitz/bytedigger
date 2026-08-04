"""Agreement 21792EE7 — anchor-bias guard + verify-quote filter RED tests.

Two-part fix in phase_6_review.py:
1. Anchor-bias guard text injected into _build_review_prompt: warns reviewer
   that SPEC may have drifted from IMPL; requires verbatim-quote verification
   per finding; explains that mismatched quotes are dropped by aggregator.
2. Verify-quote filter in _aggregate_review_findings: parses the
   `> path:line: <verbatim>` evidence line per finding, reads the cited file
   at HEAD, verifies match. Mismatch → FABRICATED-CANDIDATE (dropped from
   severity counts).

Policy for T05-T08: DROP (FABRICATED-CANDIDATE) — strict. Finding must NOT
appear in `aggregated_content` AND must NOT count toward severity totals.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

from bytedigger_engine.contracts import StepResult, WorkflowContext  # noqa: E402
from bytedigger_engine.workflows.phase_6_review import (  # noqa: E402
    REVIEW_DOC_RELPATH,
    VERDICT_FAIL,
    VERDICT_PARTIAL,
    VERDICT_PASS,
    VERDICT_SUSPECT,
    _aggregate_review_findings,
    _build_review_prompt,
)


# ─── helpers ──────────────────────────────────────────────────────────────────


def _make_ctx(tmp_path: Path, complexity: str = "SIMPLE") -> WorkflowContext:
    scratch = tmp_path / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={
            "scratchpad_dir": str(scratch),
            "complexity": complexity,
        },
        question="add feature X",
        session_id="test-21792EE7",
        persona="hal",
        framework=None,
        domain=None,
    )


def _seed_role(tmp_path: Path, slug: str, body: str) -> Path:
    """Write a per-role review file at <scratch>/reviews/role-<slug>.md."""
    scratch = tmp_path / "scratch"
    p = scratch / "reviews" / f"role-{slug}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def _agg_prev(tmp_path: Path) -> StepResult:
    """Minimal prev StepResult expected by _aggregate_review_findings."""
    scratch = tmp_path / "scratch"
    return StepResult(
        status="ok",
        data={
            "raw_response": "Reviewers complete.",
            "doc_path": str(scratch / REVIEW_DOC_RELPATH),
        },
        duration_ms=0,
        step_name="invoke_review_llm",
    )


def _fixture_file(tmp_path: Path, name: str = "fixture.py", content: str = "def foo():\n    return 42\n") -> Path:
    """Create a known-content fixture file for quote-verification tests."""
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


# ─── Prompt-side tests (T01-T03) ──────────────────────────────────────────────


def test_21792EE7_t01_anchor_bias_guard_in_prompt(tmp_path):
    """T01: _build_review_prompt must contain an anchor-bias guard warning.

    The guard must warn that SPEC may have drifted from IMPL and instruct the
    reviewer to verify citations against the current file. Today this text does
    not exist → test FAILS.
    """
    ctx = _make_ctx(tmp_path)
    result = _build_review_prompt(ctx, None)
    assert result.status == "ok"
    prompt: str = result.data["prompt"]

    # Stable sentinel: the guard should mention spec/impl drift
    assert "drift" in prompt.lower(), (
        "anchor-bias guard missing: prompt must warn about SPEC/IMPL drift; "
        "expected substring 'drift' not found"
    )
    # And it should instruct verification against the current file
    assert "verify" in prompt.lower() and "citation" in prompt.lower(), (
        "anchor-bias guard missing: prompt must instruct reviewer to verify citations "
        "against current file; 'verify' and 'citation' not found in prompt"
    )


def test_21792EE7_t02_prompt_mandates_verbatim_quote_schema(tmp_path):
    """T02: prompt must explicitly instruct the reviewer to verify EVERY citation
    against the current file — not just recommend it.

    The new anchor-bias guard must include a phrase like 'verify EVERY citation
    against the current file' or 'verify every citation against current'.
    The existing anti-fab EVIDENCE QUOTE section says 'mandatory' but doesn't
    reference spec/impl drift context. The NEW guard must contain the
    'verify every citation' phrase to pin the strengthened instruction.
    Today → FAILS (guard text does not exist yet).
    """
    ctx = _make_ctx(tmp_path)
    result = _build_review_prompt(ctx, None)
    prompt: str = result.data["prompt"]

    lower_prompt = prompt.lower()
    # The new anchor-bias guard MUST contain 'verify every citation' — distinct
    # from the existing anti-fab wording and specifically tied to drift context.
    assert "verify every citation" in lower_prompt, (
        "T02: anchor-bias guard must instruct reviewer to 'verify every citation' "
        "against the current file; this specific phrase not found in prompt"
    )


def test_21792EE7_t03_prompt_explains_drop_on_mismatch(tmp_path):
    """T03: prompt must explicitly warn that the Python aggregator will DROP
    findings whose quoted line doesn't match the actual file.

    The new anchor-bias guard must contain a phrase like 'aggregator will drop'
    or 'will be dropped' or 'dropped by the aggregator'. The existing anti-fab
    prompt says 'fabrication' in a heading but does NOT mention the aggregator
    actively dropping mismatched findings.
    Today → FAILS (no such warning exists).
    """
    ctx = _make_ctx(tmp_path)
    result = _build_review_prompt(ctx, None)
    prompt: str = result.data["prompt"]

    lower_prompt = prompt.lower()
    # The new guard must warn that MISMATCHED quote → finding dropped by aggregator.
    # Pin specific phrases that don't exist today.
    drop_phrases = [
        "will be dropped",
        "aggregator will drop",
        "dropped by the aggregator",
        "mismatch.*drop",
        "quote.*mismatch",
    ]
    import re as _re
    found = any(
        (phrase in lower_prompt if "*" not in phrase
         else bool(_re.search(phrase, lower_prompt)))
        for phrase in drop_phrases
    )
    assert found, (
        "T03: anchor-bias guard must warn reviewer that mismatched/unverifiable quotes "
        "will be DROPPED by the aggregator; none of the expected phrases found in prompt. "
        f"Checked: {drop_phrases}"
    )


# ─── Aggregator-side tests (T04-T10) ─────────────────────────────────────────


def test_21792EE7_t04_quote_matches_file_finding_kept(tmp_path):
    """T04: finding whose `> path:line:` quote matches the actual file content
    at that line must be KEPT in aggregated output and counted.

    This is the happy-path test — should also pass TODAY (aggregator keeps all
    findings). If it fails today, the aggregator is broken in a different way.
    """
    fixture = _fixture_file(tmp_path, "fixture.py", "def foo():\n    return 42\n")
    # Line 1 of fixture.py is "def foo():"
    body = (
        "# code-reviewer Review\n\n"
        f"### SEVERITY: HIGH — Missing error handling\n"
        f"> {fixture}:1: def foo():\n"
        "Confidence: HIGH\n"
        "Description: no validation.\n\n"
        "VERDICT: FAIL\n"
    )
    _seed_role(tmp_path, "code-reviewer", body)
    ctx = _make_ctx(tmp_path)
    result = _aggregate_review_findings(ctx, _agg_prev(tmp_path))

    assert result.status == "ok", f"expected ok: {result.error_code}: {result.error}"
    content = result.data["aggregated_content"]
    assert "Missing error handling" in content, (
        "T04: valid finding with matching quote must appear in aggregated_content"
    )
    counts = result.data["severity_counts"]
    assert counts["HIGH"] == 1, (
        f"T04: valid HIGH finding must count as 1; got severity_counts={counts}"
    )


def test_21792EE7_t05_quote_mismatch_finding_dropped(tmp_path):
    """T05: finding whose `> path:line:` quote does NOT match actual file content
    must be DROPPED (FABRICATED-CANDIDATE) — excluded from counts.

    File has 'def foo():' at line 1; finding claims 'def bar():'.
    Today aggregator keeps all findings → this test FAILS.
    """
    fixture = _fixture_file(tmp_path, "fixture.py", "def foo():\n    return 42\n")
    # Intentional mismatch: file has "def foo():" but finding claims "def bar():"
    body = (
        "# code-reviewer Review\n\n"
        f"### SEVERITY: CRITICAL — Wrong function signature\n"
        f"> {fixture}:1: def bar():\n"
        "Confidence: HIGH\n"
        "Description: function is named wrong.\n\n"
        "VERDICT: FAIL\n"
    )
    _seed_role(tmp_path, "code-reviewer", body)
    ctx = _make_ctx(tmp_path)
    result = _aggregate_review_findings(ctx, _agg_prev(tmp_path))

    assert result.status == "ok", f"unexpected error: {result.error_code}: {result.error}"
    content = result.data["aggregated_content"]
    counts = result.data["severity_counts"]

    # Finding must be dropped: not counted
    assert counts["CRITICAL"] == 0, (
        f"T05: CRITICAL finding with mismatched quote must be dropped; "
        f"got CRITICAL={counts['CRITICAL']}"
    )
    assert result.data["findings_count"] == 0, (
        f"T05: findings_count must be 0 after dropping fabricated finding; "
        f"got {result.data['findings_count']}"
    )


def test_21792EE7_t06_cited_file_nonexistent_finding_dropped(tmp_path):
    """T06: finding citing a nonexistent file must be dropped/FABRICATED-CANDIDATE.

    Today aggregator keeps all findings → this test FAILS.
    """
    nonexistent = tmp_path / "nonexistent.py"
    body = (
        "# code-reviewer Review\n\n"
        f"### SEVERITY: HIGH — Missing guard\n"
        f"> {nonexistent}:5: some_code()\n"
        "Confidence: HIGH\n"
        "Description: file doesn't exist.\n\n"
        "VERDICT: FAIL\n"
    )
    _seed_role(tmp_path, "code-reviewer", body)
    ctx = _make_ctx(tmp_path)
    result = _aggregate_review_findings(ctx, _agg_prev(tmp_path))

    assert result.status == "ok"
    counts = result.data["severity_counts"]
    assert counts["HIGH"] == 0, (
        f"T06: finding citing nonexistent file must be dropped; "
        f"got HIGH={counts['HIGH']}"
    )
    assert result.data["findings_count"] == 0, (
        f"T06: findings_count must be 0; got {result.data['findings_count']}"
    )


def test_21792EE7_t07_cited_line_beyond_eof_finding_dropped(tmp_path):
    """T07: finding citing a line number beyond EOF must be dropped.

    File has 2 lines; finding cites line 99. Today → kept → FAILS.
    """
    fixture = _fixture_file(tmp_path, "short.py", "x = 1\ny = 2\n")
    body = (
        "# code-reviewer Review\n\n"
        f"### SEVERITY: HIGH — Out of bounds ref\n"
        f"> {fixture}:99: nonexistent_line()\n"
        "Confidence: HIGH\n"
        "Description: cites a line past EOF.\n\n"
        "VERDICT: FAIL\n"
    )
    _seed_role(tmp_path, "code-reviewer", body)
    ctx = _make_ctx(tmp_path)
    result = _aggregate_review_findings(ctx, _agg_prev(tmp_path))

    assert result.status == "ok"
    counts = result.data["severity_counts"]
    assert counts["HIGH"] == 0, (
        f"T07: finding citing line beyond EOF must be dropped; "
        f"got HIGH={counts['HIGH']}"
    )


def test_21792EE7_t08_finding_without_quote_line_dropped(tmp_path):
    """T08: finding with no `> path:line:` evidence line must be DROPPED
    (MISSING-QUOTE policy — strict mode for anchor-bias fix).

    Today aggregator keeps all findings regardless → FAILS.
    """
    body = (
        "# code-reviewer Review\n\n"
        "### SEVERITY: HIGH — No evidence provided\n"
        "Confidence: HIGH\n"
        "Description: finding with no verbatim quote line.\n\n"
        "VERDICT: FAIL\n"
    )
    _seed_role(tmp_path, "code-reviewer", body)
    ctx = _make_ctx(tmp_path)
    result = _aggregate_review_findings(ctx, _agg_prev(tmp_path))

    assert result.status == "ok"
    counts = result.data["severity_counts"]
    assert counts["HIGH"] == 0, (
        f"T08: finding with no quote line must be dropped (MISSING-QUOTE policy); "
        f"got HIGH={counts['HIGH']}"
    )
    assert result.data["findings_count"] == 0, (
        f"T08: findings_count must be 0; got {result.data['findings_count']}"
    )


def test_21792EE7_t09_verdict_reflects_post_filter_counts(tmp_path):
    """T09: verdict must be computed from POST-filter severity counts.

    Setup: 2 findings in one role file:
      - LOW with valid matching quote → kept
      - CRITICAL with mismatched quote → dropped

    Pre-filter: CRITICAL present → verdict=FAIL.
    Post-filter: CRITICAL dropped, only LOW → verdict=PARTIAL.

    Today aggregator doesn't filter → CRITICAL kept → verdict=FAIL → FAILS.
    """
    fixture = _fixture_file(tmp_path, "verdict.py", "def good():\n    pass\n")
    body = (
        "# code-reviewer Review\n\n"
        # LOW finding with VALID quote (line 1 = "def good():")
        f"### SEVERITY: LOW — minor style issue\n"
        f"> {fixture}:1: def good():\n"
        "Confidence: LOW\n"
        "Description: naming convention.\n\n"
        # CRITICAL finding with MISMATCHED quote
        f"### SEVERITY: CRITICAL — fabricated critical bug\n"
        f"> {fixture}:1: def totally_wrong():\n"
        "Confidence: HIGH\n"
        "Description: wrong function name cited.\n\n"
        "VERDICT: FAIL\n"
    )
    _seed_role(tmp_path, "code-reviewer", body)
    ctx = _make_ctx(tmp_path)
    result = _aggregate_review_findings(ctx, _agg_prev(tmp_path))

    assert result.status == "ok"
    counts = result.data["severity_counts"]
    # CRITICAL must be dropped (mismatched quote), LOW must be kept (valid quote)
    assert counts["CRITICAL"] == 0, (
        f"T09: CRITICAL with mismatched quote must be dropped; got CRITICAL={counts['CRITICAL']}"
    )
    assert counts["LOW"] == 1, (
        f"T09: LOW with valid quote must be kept; got LOW={counts['LOW']}"
    )
    # Verdict must be PARTIAL (only LOW present), not FAIL
    assert result.data["verdict"] == VERDICT_PARTIAL, (
        f"T09: verdict must be PARTIAL (post-filter only LOW remains); "
        f"got verdict={result.data['verdict']!r}"
    )


def test_21792EE7_t10_dedup_after_verify_quote(tmp_path):
    """T10: dedup must apply AFTER verify-quote filter.

    Case A: 2 roles, same (severity, normalized_title), both with VALID quotes
    → dedup applies → final count 1.

    Case B: 2 roles, same key, both quotes MISMATCH → both dropped → final count 0.

    Today Case B fails (aggregator doesn't filter → count stays 1 after dedup).
    """
    fixture = _fixture_file(tmp_path, "dedup.py", "class MyClass:\n    pass\n")

    # Case A: same finding, both valid quotes (line 1 = "class MyClass:")
    body_a1 = (
        "# role-a Review\n\n"
        f"### SEVERITY: HIGH — missing init\n"
        f"> {fixture}:1: class MyClass:\n"
        "Confidence: HIGH\n"
        "Description: no __init__.\n\n"
        "VERDICT: FAIL\n"
    )
    body_a2 = (
        "# role-b Review\n\n"
        f"### SEVERITY: HIGH — missing init\n"
        f"> {fixture}:1: class MyClass:\n"
        "Confidence: HIGH\n"
        "Description: same finding from second reviewer.\n\n"
        "VERDICT: FAIL\n"
    )
    _seed_role(tmp_path, "role-a", body_a1)
    _seed_role(tmp_path, "role-b", body_a2)

    ctx = _make_ctx(tmp_path)
    result_a = _aggregate_review_findings(ctx, _agg_prev(tmp_path))
    assert result_a.status == "ok"
    counts_a = result_a.data["severity_counts"]
    assert counts_a["HIGH"] == 1, (
        f"T10 Case A: dedup must leave 1 (not 2) valid matching findings; "
        f"got HIGH={counts_a['HIGH']}"
    )

    # Reset role files for Case B (both quotes mismatch)
    scratch = tmp_path / "scratch"
    reviews_dir = scratch / "reviews"
    for p in reviews_dir.glob("role-*.md"):
        p.unlink()

    body_b1 = (
        "# role-a Review\n\n"
        f"### SEVERITY: HIGH — missing init\n"
        f"> {fixture}:1: class WRONG_CLASS:\n"
        "Confidence: HIGH\n"
        "Description: fabricated.\n\n"
        "VERDICT: FAIL\n"
    )
    body_b2 = (
        "# role-b Review\n\n"
        f"### SEVERITY: HIGH — missing init\n"
        f"> {fixture}:1: class ALSO_WRONG:\n"
        "Confidence: HIGH\n"
        "Description: also fabricated.\n\n"
        "VERDICT: FAIL\n"
    )
    _seed_role(tmp_path, "role-a", body_b1)
    _seed_role(tmp_path, "role-b", body_b2)

    result_b = _aggregate_review_findings(ctx, _agg_prev(tmp_path))
    assert result_b.status == "ok"
    counts_b = result_b.data["severity_counts"]
    assert counts_b["HIGH"] == 0, (
        f"T10 Case B: both quotes mismatch → both dropped → count must be 0; "
        f"got HIGH={counts_b['HIGH']}"
    )
    assert result_b.data["findings_count"] == 0, (
        f"T10 Case B: findings_count must be 0; got {result_b.data['findings_count']}"
    )


def test_21792EE7_t11_indented_python_line_kept(tmp_path):
    """T11: finding quoting an indented Python line must be KEPT (not dropped).

    Regression for fix-1 (Option C): the old `\\s?` regex consumed one leading
    space, so `> f.py:2:    x = foo()` stored group(3) as `'   x = foo()'` (3
    spaces) while the file had 4 spaces → mismatch → false FABRICATED-CANDIDATE.

    With fix-1: group(3) captures `' x = foo()'` raw; normalisation strips
    exactly one leading space → `'   x = foo()'`… wait, that still has 3.
    Actually the separator space is the first space after the colon; the
    remaining 3 are indentation from the file line `'    x = foo()'`.
    Let's use the exact content: file line 2 = `'    x = foo()'` (4 spaces).
    Role cites it as `> f.py:2:    x = foo()` — that is space+4spaces = the
    separator-space convention from the prompt.  After Option C normalisation:
    strip leading space → `'    x = foo()'` (4 spaces) == actual → KEPT.
    """
    fixture = _fixture_file(
        tmp_path,
        "indent.py",
        "def bar():\n    x = foo()\n    return x\n",
    )
    # Line 2 of indent.py is "    x = foo()" (4-space indent)
    # Role file emits "> indent.py:2:    x = foo()" — separator-space + 4-space indent
    body = (
        "# code-reviewer Review\n\n"
        f"### SEVERITY: HIGH — bad assignment\n"
        f"> {fixture}:2:     x = foo()\n"
        "Confidence: HIGH\n"
        "Description: should use walrus operator.\n\n"
        "VERDICT: FAIL\n"
    )
    _seed_role(tmp_path, "code-reviewer", body)
    ctx = _make_ctx(tmp_path)
    result = _aggregate_review_findings(ctx, _agg_prev(tmp_path))

    assert result.status == "ok", f"T11: unexpected error: {result.error}"
    counts = result.data["severity_counts"]
    assert counts["HIGH"] == 1, (
        f"T11: indented Python line should be KEPT (not FABRICATED-CANDIDATE); "
        f"got severity_counts={counts}"
    )


def test_21792EE7_t12_all_filtered_verdict_suspect(tmp_path):
    """T12: when every finding is dropped by verify-quote, verdict must be SUSPECT.

    SUSPECT != PASS: PASS means genuinely clean build; SUSPECT means all
    reviewers submitted unverifiable citations — possible mass fabrication.

    filtered_count must be exposed in result.data.
    """
    fixture = _fixture_file(tmp_path, "suspect.py", "def real():\n    pass\n")
    # All 3 findings have mismatched quotes → all dropped → findings_count=0,
    # filtered_count=3 → verdict=SUSPECT (not PASS).
    body = (
        "# code-reviewer Review\n\n"
        f"### SEVERITY: HIGH — bug A\n"
        f"> {fixture}:1: def WRONG_A():\n"
        "Confidence: HIGH\n"
        "Description: fabricated A.\n\n"
        f"### SEVERITY: MEDIUM — bug B\n"
        f"> {fixture}:1: def WRONG_B():\n"
        "Confidence: MED\n"
        "Description: fabricated B.\n\n"
        f"### SEVERITY: LOW — bug C\n"
        f"> {fixture}:1: def WRONG_C():\n"
        "Confidence: LOW\n"
        "Description: fabricated C.\n\n"
        "VERDICT: FAIL\n"
    )
    _seed_role(tmp_path, "code-reviewer", body)
    ctx = _make_ctx(tmp_path)
    result = _aggregate_review_findings(ctx, _agg_prev(tmp_path))

    assert result.status == "ok", f"T12: unexpected error: {result.error}"
    assert result.data["findings_count"] == 0, (
        f"T12: all findings dropped → findings_count must be 0; "
        f"got {result.data['findings_count']}"
    )
    assert result.data.get("filtered_count", -1) == 3, (
        f"T12: filtered_count must be 3; got {result.data.get('filtered_count')}"
    )
    assert result.data["verdict"] == VERDICT_SUSPECT, (
        f"T12: all-filtered verdict must be SUSPECT (not PASS); "
        f"got verdict={result.data['verdict']!r}"
    )
