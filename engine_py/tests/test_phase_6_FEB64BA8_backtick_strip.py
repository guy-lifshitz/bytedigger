"""RED tests for FEB64BA8 — composite-reviewer drop fix in phase_6_review.

Spec: SHARED/memory/Decisions/2026-05-14_FEB64BA8_phase_6_review_backtick_strip_spec.md

3 atomic changes under test:
  (a) _verify_finding_quote: strip a single leading+trailing backtick pair before
      the 5-tier match cascade (after Option-C separator-space strip, before tiers).
  (b) _aggregate_review_findings: emit one 'composite_finding_dropped' event per
      dropped finding with 6-key payload.
  (c) review_findings_audit: tighten 'consistent' to also require filtered_count==0;
      add new 'high_filter_rate' bool key.

Expected pre-fix status:
  AC1  FAIL — backtick-wrapped quote not stripped → FABRICATED-CANDIDATE today
  AC2  PASS — regression guard (plain form, no backticks)
  AC3  PASS — regression guard (internal backticks, no outer pair → passes tier-1 unchanged)
  AC4  PASS — regression guard (degenerate single-backtick, length guard prevents strip)
  AC5  FAIL — real-incident repro; backtick-wrapped → FABRICATED-CANDIDATE today
  AC6  FAIL — composite_finding_dropped event not yet emitted
  AC7  FAIL — composite_finding_dropped event not yet emitted
  AC8  FAIL — composite_finding_dropped event not yet emitted
  AC9  FAIL — consistent=True today even with filtered_count>0 (incident root cause)
  AC10 PASS — regression guard (consistent=True, no drops)
  AC11 FAIL — high_filter_rate key does not exist yet
  AC12 FAIL — high_filter_rate key does not exist yet
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest  # noqa: F401

ENGINE_PY = Path(__file__).resolve().parents[1]
if str(ENGINE_PY) not in sys.path:
    sys.path.insert(0, str(ENGINE_PY))
WORKFLOWS = ENGINE_PY / "workflows"
if str(WORKFLOWS) not in sys.path:
    sys.path.insert(0, str(WORKFLOWS))

from contracts import StepResult, WorkflowContext  # noqa: E402
from phase_6_review import (  # noqa: E402
    _aggregate_review_findings,
    _verify_finding_quote,
)
import phase_6_review as _p6  # noqa: E402 — top-level alias for monkeypatching _emit_safe.
# CRITICAL: _aggregate_review_findings's globals resolve to the top-level
# "phase_6_review" module, NOT "workflows.phase_6_review". Patch _p6, not the
# workflows sub-module. See test_906e37dc_review_findings_audit.py:358-363.


# ─── helpers ──────────────────────────────────────────────────────────────────


def _make_ctx(tmp_path: Path, complexity: str = "SIMPLE") -> types.SimpleNamespace:
    """Minimal context for _aggregate_review_findings.

    Uses SimpleNamespace so we don't depend on WorkflowContext's full ctor
    for aggregate tests (matches 906E37DC pattern).
    """
    scratch = tmp_path / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    return types.SimpleNamespace(
        org_config={"scratchpad_dir": str(scratch)},
        question="test question",
    )


def _write_role_file(
    reviews_dir: Path,
    slug: str,
    *,
    blocks: list[tuple[str, str, str]],
    selfcount: int | None,
) -> None:
    """Write reviews_dir/role-<slug>.md.

    blocks: list of (severity, title, quote_evidence_line) triples.
    The quote_evidence_line is placed verbatim after "> " prefix.
    selfcount: if not None, appended as <!-- role-findings-count: N -->.
    """
    reviews_dir.mkdir(parents=True, exist_ok=True)
    lines: list[str] = [f"# {slug} Review", ""]
    for severity, title, quote_evidence in blocks:
        lines.append(f"### SEVERITY: {severity} — {title}")
        lines.append(f"> {quote_evidence}")
        lines.append("Confidence: HIGH")
        lines.append("Description: x")
        lines.append("")
    lines.append("VERDICT: PARTIAL")
    if selfcount is not None:
        lines.append(f"<!-- role-findings-count: {selfcount} -->")
    (reviews_dir / f"role-{slug}.md").write_text("\n".join(lines), encoding="utf-8")


def _prev_ok(scratchpad: Path) -> StepResult:
    """Minimal prev StepResult for _aggregate_review_findings."""
    return StepResult(status="ok", data={}, duration_ms=0, step_name="x")


def _run(ctx: types.SimpleNamespace, tmp_path: Path) -> StepResult:
    """Call _aggregate_review_findings with a minimal prev StepResult."""
    scratch = tmp_path / "scratch"
    return _aggregate_review_findings(ctx, _prev_ok(scratch))


def _finding_block_with_quote(
    abs_path: str,
    lineno: int,
    quote_content: str,
    severity: str = "HIGH",
    title: str = "test-finding",
) -> str:
    """Build a finding block with the raw quote_content placed after 'path:N: '.

    quote_content is the literal string after the ": " separator, verbatim.
    Use this to test backtick-wrapped vs plain forms.
    """
    return (
        f"### SEVERITY: {severity} — {title}\n"
        f"> {abs_path}:{lineno}: {quote_content}\n"
        "Confidence: HIGH\n"
        "Description: test description\n"
    )


# ─── AC1: backtick-wrapped quote → tier-1 OK after strip ──────────────────────


def test_ac1_backtick_wrapped_strip(tmp_path):
    """AC1 (MUST FAIL today): backtick-wrapped quote stripped → tier-1 OK.

    File line 5 is '    pass'. Evidence: > path:5: `    pass` (outer backticks).
    Pre-fix: outer backticks remain → exact compare '`    pass`' != '    pass'
    → cascade fails all tiers → FABRICATED-CANDIDATE.
    Post-fix: outer pair stripped → '    pass' == '    pass' → (True, 'OK').
    """
    # Create file where line 5 = '    pass'
    file_lines = [
        "def foo():\n",        # line 1
        "    x = 1\n",         # line 2
        "    y = 2\n",         # line 3
        "    z = 3\n",         # line 4
        "    pass\n",          # line 5  ← target
    ]
    target_file = tmp_path / "foo.py"
    target_file.write_text("".join(file_lines), encoding="utf-8")

    # Evidence wraps the quote in backticks: `    pass`
    block = _finding_block_with_quote(str(target_file), 5, "`    pass`")

    verify_status, reason = _verify_finding_quote(block, {})
    assert verify_status == "suspect-no-match", (
        f"AC1 (1F39FB1A co-change): post-pivot (FEB64BA8 strip REMOVED), backtick-wrapped "
        f"quote must return verify_status='suspect-no-match'. Got {verify_status!r}. "
        f"Pre-pivot (FEB64BA8 strip): strip fired -> ('verified-exact', 'OK')."
    )
    assert reason == "FABRICATED-CANDIDATE", (
        f"AC1 (1F39FB1A co-change): expected reason='FABRICATED-CANDIDATE' (all 5 tiers fail "
        f"without strip), got {reason!r}"
    )


# ─── AC2: plain form (no backticks) — regression guard ────────────────────────


def test_ac2_plain_form_regression(tmp_path):
    """AC2 (regression guard, PASS today): plain quote form unchanged.

    File line 5 = '    pass'. Evidence: > path:5:     pass (no backticks).
    Must return (True, 'OK') both pre-fix and post-fix.
    """
    file_lines = [
        "def foo():\n",
        "    x = 1\n",
        "    y = 2\n",
        "    z = 3\n",
        "    pass\n",
    ]
    target_file = tmp_path / "foo.py"
    target_file.write_text("".join(file_lines), encoding="utf-8")

    block = _finding_block_with_quote(str(target_file), 5, "    pass")

    verify_status, reason = _verify_finding_quote(block, {})
    assert verify_status == "verified-exact", (
        f"AC2: regression — plain form must verify_status='verified-exact', got {verify_status!r}"
    )
    assert reason == "OK", (
        f"AC2: regression — plain form must return 'OK', got {reason!r}"
    )


# ─── AC3: internal backticks preserved — regression guard ─────────────────────


def test_ac3_internal_backticks_preserved(tmp_path):
    """AC3 (regression guard, PASS today): internal backticks are NOT stripped.

    File line 1 = 'actual = \"`literal`\"'.
    Evidence: > path:1: actual = \"`literal`\"  (NO outer wrapping backtick pair).
    The quote starts with 'a', ends with '"' — outer-pair check doesn't trigger.
    Tier-1 exact match must hit → (True, 'OK').

    This guards that the strip logic only fires on outer-pair and doesn't
    accidentally eat internal backticks.
    """
    line_content = 'actual = "`literal`"'
    target_file = tmp_path / "bar.py"
    target_file.write_text(line_content + "\n", encoding="utf-8")

    # Evidence has NO outer wrapping backtick — the literal itself contains backticks
    block = _finding_block_with_quote(str(target_file), 1, line_content)

    verify_status, reason = _verify_finding_quote(block, {})
    assert verify_status == "verified-exact", (
        f"AC3: expected verify_status='verified-exact' for internal-backtick quote, got {verify_status!r}"
    )
    assert reason == "OK", (
        f"AC3: expected reason='OK', got {reason!r}"
    )


# ─── AC4: degenerate inputs — length guard, no crash ─────────────────────────


def test_ac4_degenerate_inputs_unchanged(tmp_path):
    """AC4 (regression guard, PASS today): degenerate single-backtick not stripped.

    Case 1: quote is a single backtick '`'. File line 1 = '`'.
    len('`') == 1 < 2 → length guard fires → strip skipped → exact compare
    '`' == '`' → (True, 'OK').

    Case 2: quote is empty after separator-space strip (evidence '> path:1: ').
    _verify_finding_quote must not crash (returns FABRICATED-CANDIDATE or similar).
    """
    # Case 1: single backtick
    single_bt_file = tmp_path / "x.py"
    single_bt_file.write_text("`\n", encoding="utf-8")

    block1 = _finding_block_with_quote(str(single_bt_file), 1, "`")
    verify_status1, reason1 = _verify_finding_quote(block1, {})
    assert verify_status1 == "verified-exact", (
        f"AC4 case1: single backtick file/quote must verify_status='verified-exact' (no strip), "
        f"got {verify_status1!r}, reason={reason1!r}"
    )
    assert reason1 == "OK", (
        f"AC4 case1: expected 'OK', got {reason1!r}"
    )

    # Case 2: empty quote — '> path:1: ' (single trailing space after colon).
    # The separator-space strip removes the space, leaving empty string.
    # Must not raise; should return (False, ...) gracefully.
    empty_file = tmp_path / "y.py"
    empty_file.write_text("something\n", encoding="utf-8")

    # Build block manually — quote_content is empty so no space before newline
    empty_block = (
        f"### SEVERITY: LOW — empty-quote-test\n"
        f"> {empty_file}:1: \n"
        "Confidence: HIGH\n"
        "Description: degenerate\n"
    )
    # Should not raise — just return a drop or keep, we don't assert keep here
    result = _verify_finding_quote(empty_block, {})
    assert isinstance(result, tuple) and len(result) == 2, (
        f"AC4 case2: _verify_finding_quote must return 2-tuple, got {result!r}"
    )


# ─── AC5: real-incident reproduction ─────────────────────────────────────────


def test_ac5_real_incident_reproduction(tmp_path):
    """AC5 (MUST FAIL today): INCIDENT.md verbatim repro.

    File line 105 = '              STATE=$(az containerapp revision show \\'.
    Evidence line: > path:105: `              STATE=$(az containerapp revision show \\``
    (outer backtick pair wrapping the content, which itself ends with a backslash).

    Pre-fix: outer backticks remain → compare '`              STATE=...\`' vs
    '              STATE=...' → all tiers fail → FABRICATED-CANDIDATE.
    Post-fix: outer backtick pair stripped → content = '              STATE=$(az...\\'
    → tier-1 exact match → (True, 'OK').
    """
    # Build a file with 105 lines where line 105 matches the incident content
    line_105_content = r"              STATE=$(az containerapp revision show \\"
    # 104 filler lines + the target line at position 105
    filler = ["# line {}\n".format(i) for i in range(1, 105)]
    target_line = line_105_content + "\n"
    all_lines = filler + [target_line]
    assert len(all_lines) == 105, f"fixture must have exactly 105 lines, got {len(all_lines)}"

    deploy_file = tmp_path / "deploy.yml"
    deploy_file.write_text("".join(all_lines), encoding="utf-8")

    # Evidence: outer backtick wrapping the content
    # The actual file line ends with a single backslash; evidence wraps in backticks
    evidence_content = "`" + line_105_content + "`"
    block = _finding_block_with_quote(str(deploy_file), 105, evidence_content)

    verify_status, reason = _verify_finding_quote(block, {})
    assert verify_status == "suspect-no-match", (
        f"AC5 (1F39FB1A co-change): post-pivot (FEB64BA8 strip REMOVED), incident repro must "
        f"return verify_status='suspect-no-match'. Got {verify_status!r}, reason={reason!r}. "
        f"Pre-pivot (FEB64BA8 strip): strip fired -> ('verified-exact', 'OK')."
    )
    assert reason == "FABRICATED-CANDIDATE", (
        f"AC5 (1F39FB1A co-change): expected 'FABRICATED-CANDIDATE' (all 5 tiers fail "
        f"without strip), got {reason!r}"
    )


# ─── AC6: composite_finding_dropped event count ───────────────────────────────


def test_ac6_composite_drop_event_count(tmp_path, monkeypatch):
    """AC6 (MUST FAIL today): N drops → exactly N composite_finding_dropped events.

    Setup: role-a has 1 finding whose quote matches the file (KEPT).
           role-b has 2 findings whose quotes don't match any file line (DROPPED).
    After _aggregate_review_findings, spy must observe exactly 2
    composite_finding_dropped events (one per role-b finding).
    """
    # File for role-a's good finding
    good_file = tmp_path / "scratch" / "good.py"
    good_file.parent.mkdir(parents=True, exist_ok=True)
    good_file.write_text("def kept_function():\n    return True\n", encoding="utf-8")

    reviews_dir = tmp_path / "scratch" / "reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)

    # role-a: 1 good finding (exact match at line 1)
    _write_role_file(
        reviews_dir,
        "role-a",
        blocks=[("HIGH", "kept-finding", f"{good_file}:1: def kept_function():")],
        selfcount=1,
    )

    # role-b: 2 bad findings with absolute paths that don't exist
    _write_role_file(
        reviews_dir,
        "role-b",
        blocks=[
            ("CRITICAL", "dropped-finding-1", "/nonexistent/feb64ba8_1.py:99: x = 1"),
            ("HIGH", "dropped-finding-2", "/nonexistent/feb64ba8_2.py:99: y = 2"),
        ],
        selfcount=2,
    )

    captured: list[tuple[str, dict]] = []

    def _fake_emit(event_name: str, payload: dict) -> None:
        captured.append((event_name, payload))

    monkeypatch.setattr(_p6, "_emit_safe", _fake_emit)

    ctx = _make_ctx(tmp_path)
    _run(ctx, tmp_path)

    # 1F39FB1A co-change: composite_finding_dropped is GONE; composite_finding_quote_verified
    # is emitted for ALL findings (1 good + 2 suspect = 3 events total).
    # The 2 "bad" findings are now suspect-file-not-found (nonexistent absolute paths).
    drop_events = [(n, p) for n, p in captured if n == "composite_finding_dropped"]
    assert len(drop_events) == 0, (
        f"AC6 (1F39FB1A co-change): composite_finding_dropped must NOT be emitted post-pivot. "
        f"Got {len(drop_events)} events. Event is GONE — replaced by composite_finding_quote_verified."
    )
    verified_events = [(n, p) for n, p in captured if n == "composite_finding_quote_verified"]
    assert len(verified_events) == 3, (
        f"AC6 (1F39FB1A co-change): expected 3 composite_finding_quote_verified events "
        f"(1 per finding: 1 verified + 2 suspect), got {len(verified_events)}."
    )


# ─── AC7: composite_finding_dropped event schema ──────────────────────────────


def test_ac7_composite_drop_event_schema(tmp_path, monkeypatch):
    """AC7 (MUST FAIL today): each composite_finding_dropped has all 6 required keys.

    Uses same setup as AC6. For each drop event, verifies:
      - 'phase' == 'phase_6_review'
      - 'role' present (string)
      - 'severity' present (string)
      - 'title' present (string)
      - 'drop_reason' present (string)
      - 'quote_line_present' present (bool)
    """
    good_file = tmp_path / "scratch" / "good.py"
    good_file.parent.mkdir(parents=True, exist_ok=True)
    good_file.write_text("def schema_test():\n    pass\n", encoding="utf-8")

    reviews_dir = tmp_path / "scratch" / "reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)

    # role-a: 1 good finding (kept)
    _write_role_file(
        reviews_dir,
        "role-a",
        blocks=[("MEDIUM", "good-schema", f"{good_file}:1: def schema_test():")],
        selfcount=1,
    )

    # role-b: 1 dropped finding (absolute nonexistent path)
    _write_role_file(
        reviews_dir,
        "role-b",
        blocks=[("CRITICAL", "dropped-schema", "/nonexistent/feb64ba8_schema.py:5: x = 0")],
        selfcount=1,
    )

    captured: list[tuple[str, dict]] = []

    def _fake_emit(event_name: str, payload: dict) -> None:
        captured.append((event_name, payload))

    monkeypatch.setattr(_p6, "_emit_safe", _fake_emit)

    ctx = _make_ctx(tmp_path)
    _run(ctx, tmp_path)

    # 1F39FB1A co-change: composite_finding_dropped is GONE. Check composite_finding_quote_verified.
    drop_events = [(n, p) for n, p in captured if n == "composite_finding_dropped"]
    assert len(drop_events) == 0, (
        f"AC7 (1F39FB1A co-change): composite_finding_dropped must NOT be emitted post-pivot. "
        f"Event is GONE — replaced by composite_finding_quote_verified."
    )

    # Verify the new event has the required keys (7 keys; drop_reason replaced by verify_reason).
    verified_events = [(n, p) for n, p in captured if n == "composite_finding_quote_verified"]
    assert len(verified_events) >= 1, (
        f"AC7 (1F39FB1A co-change): expected at least 1 composite_finding_quote_verified event."
    )
    required_keys = {"phase", "role", "severity", "title", "verify_status", "verify_reason", "quote_line_present"}
    for _evt_name, payload in verified_events:
        missing = required_keys - set(payload.keys())
        assert not missing, (
            f"AC7 (1F39FB1A co-change): composite_finding_quote_verified payload missing keys: {missing}. "
            f"Payload: {payload}"
        )
        assert payload["phase"] == "phase_6_review", (
            f"AC7: payload['phase'] must be 'phase_6_review', got {payload['phase']!r}"
        )
        assert isinstance(payload["role"], (str, type(None))), (
            f"AC7: 'role' must be str or None, got {type(payload['role'])}"
        )
        assert isinstance(payload["severity"], (str, type(None))), (
            f"AC7: 'severity' must be str or None, got {type(payload['severity'])}"
        )
        assert isinstance(payload["title"], (str, type(None))), (
            f"AC7: 'title' must be str or None, got {type(payload['title'])}"
        )
        assert isinstance(payload["verify_reason"], (str, type(None))), (
            f"AC7: 'verify_reason' must be str or None, got {type(payload['verify_reason'])}"
        )
        assert isinstance(payload["quote_line_present"], bool), (
            f"AC7: 'quote_line_present' must be bool, got {type(payload['quote_line_present'])}"
        )


# ─── AC8: quote_line_present flag correctness ────────────────────────────────


def test_ac8_quote_line_present_flag(tmp_path, monkeypatch):
    """AC8 (MUST FAIL today): quote_line_present reflects _QUOTE_LINE_RE match.

    Setup two dropped findings:
      - FABRICATED-CANDIDATE: block has a '> path:N:' line but content mismatches
        → quote_line_present=True
      - MISSING-QUOTE: block has no '> path:N:' line at all
        → quote_line_present=False
    """
    good_file = tmp_path / "scratch" / "good.py"
    good_file.parent.mkdir(parents=True, exist_ok=True)
    good_file.write_text("def ac8_anchor():\n    pass\n", encoding="utf-8")

    # File that exists but whose line doesn't match → FABRICATED-CANDIDATE
    real_file = tmp_path / "scratch" / "real.py"
    real_file.write_text("actual_content = 1\n", encoding="utf-8")

    reviews_dir = tmp_path / "scratch" / "reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)

    # role-keep: 1 good finding (anchor to keep the function running)
    _write_role_file(
        reviews_dir,
        "role-keep",
        blocks=[("LOW", "anchor", f"{good_file}:1: def ac8_anchor():")],
        selfcount=1,
    )

    # role-fab: FABRICATED-CANDIDATE (has quote line, content mismatches)
    fab_block_lines = [
        "# fab review",
        "",
        "### SEVERITY: HIGH — fabricated-with-quote",
        f"> {real_file}:1: this_does_not_match_actual_content",
        "Confidence: HIGH",
        "Description: fabricated",
        "",
        "VERDICT: FAIL",
        "<!-- role-findings-count: 1 -->",
    ]
    (reviews_dir / "role-fab.md").write_text("\n".join(fab_block_lines), encoding="utf-8")

    # role-missing: MISSING-QUOTE (no > path:N: line in the block)
    missing_block_lines = [
        "# missing review",
        "",
        "### SEVERITY: CRITICAL — missing-quote-finding",
        "No evidence line here — the reviewer forgot the > path:N: citation.",
        "Confidence: HIGH",
        "Description: no citation",
        "",
        "VERDICT: FAIL",
        "<!-- role-findings-count: 1 -->",
    ]
    (reviews_dir / "role-missing.md").write_text("\n".join(missing_block_lines), encoding="utf-8")

    captured: list[tuple[str, dict]] = []

    def _fake_emit(event_name: str, payload: dict) -> None:
        captured.append((event_name, payload))

    monkeypatch.setattr(_p6, "_emit_safe", _fake_emit)

    ctx = _make_ctx(tmp_path)
    _run(ctx, tmp_path)

    # 1F39FB1A co-change: composite_finding_dropped is GONE. Use composite_finding_quote_verified.
    # fabricated-with-quote → suspect-no-match (quote present, content mismatches)
    # missing-quote-finding → suspect-no-quote (no > path:N: line)
    drop_events = {
        p.get("title"): p
        for n, p in captured
        if n == "composite_finding_dropped"
    }
    assert len(drop_events) == 0, (
        f"AC8 (1F39FB1A co-change): composite_finding_dropped must NOT be emitted post-pivot."
    )

    verified_events_by_title = {
        p.get("title"): p
        for n, p in captured
        if n == "composite_finding_quote_verified"
    }
    assert "fabricated-with-quote" in verified_events_by_title, (
        f"AC8 (1F39FB1A co-change): expected composite_finding_quote_verified event for "
        f"'fabricated-with-quote', got titles: {list(verified_events_by_title.keys())}."
    )
    assert "missing-quote-finding" in verified_events_by_title, (
        f"AC8 (1F39FB1A co-change): expected composite_finding_quote_verified event for "
        f"'missing-quote-finding', got titles: {list(verified_events_by_title.keys())}."
    )

    fab_payload = verified_events_by_title["fabricated-with-quote"]
    missing_payload = verified_events_by_title["missing-quote-finding"]

    assert fab_payload["quote_line_present"] is True, (
        f"AC8: FABRICATED-CANDIDATE finding must have quote_line_present=True, "
        f"got {fab_payload['quote_line_present']!r}"
    )
    assert missing_payload["quote_line_present"] is False, (
        f"AC8: MISSING-QUOTE finding must have quote_line_present=False, "
        f"got {missing_payload['quote_line_present']!r}"
    )


# ─── AC9: consistent=False when filtered_count > 0 ───────────────────────────


def test_ac9_consistent_false_when_filter_drops(tmp_path, monkeypatch):
    """AC9 (MUST FAIL today): consistent=False when selfcount matches parsed_blocks
    but filtered_count > 0.

    Setup: role-a has 1 finding, selfcount=1 (honest), quote matches → KEPT.
           role-b has 1 finding, selfcount=1 (honest), quote doesn't match → DROPPED.
    Pre-fix: self_reported=2, parsed_blocks=2, filtered=1 →
             consistent = (2 == 2) = True  ← BUG (incident root cause)
    Post-fix: consistent = (2 == 2) AND (filtered==0) = False.
    Audit must show: parsed_blocks=2, aggregated=1, filtered=1, consistent=False.
    """
    good_file = tmp_path / "scratch" / "good.py"
    good_file.parent.mkdir(parents=True, exist_ok=True)
    good_file.write_text("def ac9_kept():\n    return 1\n", encoding="utf-8")

    reviews_dir = tmp_path / "scratch" / "reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)

    # role-a: quote matches → KEPT, selfcount=1
    _write_role_file(
        reviews_dir,
        "role-a",
        blocks=[("HIGH", "kept-ac9", f"{good_file}:1: def ac9_kept():")],
        selfcount=1,
    )

    # role-b: absolute nonexistent path → FILE-NOT-FOUND → DROPPED, selfcount=1
    _write_role_file(
        reviews_dir,
        "role-b",
        blocks=[("CRITICAL", "dropped-ac9", "/nonexistent/feb64ba8_ac9.py:42: x = 0")],
        selfcount=1,
    )

    # Don't bother monkeypatching emit for this test — we read audit from result.data
    monkeypatch.setattr(_p6, "_emit_safe", lambda *a, **kw: None)

    ctx = _make_ctx(tmp_path)
    result = _run(ctx, tmp_path)

    assert result.status == "ok", f"AC9: expected status='ok', got {result.status}: {result.error}"

    audit = (result.data or {}).get("findings_audit")
    assert audit is not None, "AC9: findings_audit key missing from StepResult.data"

    assert audit.get("parsed_blocks") == 2, (
        f"AC9: expected parsed_blocks=2, got {audit.get('parsed_blocks')}"
    )
    assert audit.get("aggregated") == 1, (
        f"AC9: expected aggregated=1, got {audit.get('aggregated')}"
    )
    assert audit.get("filtered") == 1, (
        f"AC9: expected filtered=1, got {audit.get('filtered')}"
    )
    assert audit.get("consistent") is False, (
        f"AC9: expected consistent=False (selfcount matches parsed_blocks BUT filtered>0), "
        f"got consistent={audit.get('consistent')!r}. "
        f"Pre-fix behavior: consistent=True even with filtered=1 (the incident bug)."
    )


# ─── AC10: consistent=True only when BOTH conditions clean ───────────────────


def test_ac10_consistent_true_only_when_clean(tmp_path, monkeypatch):
    """AC10 (regression guard, PASS today): consistent=True requires zero drops.

    Setup: 1 role file, 1 finding, selfcount=1, quote matches → KEPT, filtered=0.
    Audit: parsed_blocks=1, aggregated=1, filtered=0, consistent=True.
    """
    good_file = tmp_path / "scratch" / "good.py"
    good_file.parent.mkdir(parents=True, exist_ok=True)
    good_file.write_text("def ac10_clean():\n    return True\n", encoding="utf-8")

    reviews_dir = tmp_path / "scratch" / "reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)

    _write_role_file(
        reviews_dir,
        "role-a",
        blocks=[("MEDIUM", "clean-finding", f"{good_file}:1: def ac10_clean():")],
        selfcount=1,
    )

    monkeypatch.setattr(_p6, "_emit_safe", lambda *a, **kw: None)

    ctx = _make_ctx(tmp_path)
    result = _run(ctx, tmp_path)

    assert result.status == "ok", f"AC10: expected ok, got {result.status}: {result.error}"

    audit = (result.data or {}).get("findings_audit")
    assert audit is not None, "AC10: findings_audit key missing"

    assert audit.get("parsed_blocks") == 1, (
        f"AC10: expected parsed_blocks=1, got {audit.get('parsed_blocks')}"
    )
    assert audit.get("aggregated") == 1, (
        f"AC10: expected aggregated=1, got {audit.get('aggregated')}"
    )
    assert audit.get("filtered") == 0, (
        f"AC10: expected filtered=0, got {audit.get('filtered')}"
    )
    assert audit.get("consistent") is True, (
        f"AC10: expected consistent=True (honest selfcount + zero drops), "
        f"got {audit.get('consistent')!r}"
    )


# ─── AC11: high_filter_rate threshold (>0.5) ─────────────────────────────────


def test_ac11_high_filter_rate_threshold(tmp_path, monkeypatch):
    """AC11 (MUST FAIL today): high_filter_rate=True when filtered/(agg+filtered)>0.5.

    Sub-test A: aggregated=1, filtered=7 → ratio=7/8=0.875 > 0.5 → True.
    Sub-test B: aggregated=1, filtered=1 → ratio=1/2=0.5, NOT strictly > 0.5 → False.

    Pre-fix: 'high_filter_rate' key doesn't exist → KeyError/assertion fails.
    """
    monkeypatch.setattr(_p6, "_emit_safe", lambda *a, **kw: None)

    # ── Sub-test A: 7-of-8 drops ──────────────────────────────────────────────
    good_file_a = tmp_path / "scratch_a" / "good.py"
    good_file_a.parent.mkdir(parents=True, exist_ok=True)
    good_file_a.write_text("def ac11_anchor():\n    pass\n", encoding="utf-8")

    reviews_dir_a = tmp_path / "scratch_a" / "reviews"
    reviews_dir_a.mkdir(parents=True, exist_ok=True)

    # 1 kept finding
    _write_role_file(
        reviews_dir_a,
        "role-good",
        blocks=[("LOW", "good-ac11a", f"{good_file_a}:1: def ac11_anchor():")],
        selfcount=1,
    )
    # 7 dropped findings (absolute nonexistent paths)
    drop_blocks = [
        ("HIGH", f"drop-{i}", f"/nonexistent/feb64ba8_ac11a_{i}.py:1: x = {i}")
        for i in range(7)
    ]
    _write_role_file(
        reviews_dir_a,
        "role-bad",
        blocks=drop_blocks,
        selfcount=7,
    )

    ctx_a = types.SimpleNamespace(
        org_config={"scratchpad_dir": str(tmp_path / "scratch_a")},
        question="test question",
    )
    result_a = _aggregate_review_findings(ctx_a, _prev_ok(tmp_path / "scratch_a"))
    assert result_a.status == "ok", f"AC11A: unexpected error {result_a.error}"

    audit_a = (result_a.data or {}).get("findings_audit")
    assert audit_a is not None, "AC11A: findings_audit missing"
    assert "high_filter_rate" in audit_a, (
        f"AC11A: 'high_filter_rate' key missing from findings_audit. "
        f"Got keys: {list(audit_a.keys())}. Pre-fix: key not present."
    )
    agg_a = audit_a.get("aggregated", 0)
    flt_a = audit_a.get("filtered", 0)
    assert audit_a["high_filter_rate"] is True, (
        f"AC11A: aggregated={agg_a}, filtered={flt_a} → ratio={flt_a/(agg_a+flt_a) if (agg_a+flt_a) else 'N/A'} > 0.5 → "
        f"expected high_filter_rate=True, got {audit_a['high_filter_rate']!r}"
    )

    # ── Sub-test B: 1-of-2 drops (exactly 0.5, NOT strictly greater) ─────────
    good_file_b = tmp_path / "scratch_b" / "good.py"
    good_file_b.parent.mkdir(parents=True, exist_ok=True)
    good_file_b.write_text("def ac11b_anchor():\n    pass\n", encoding="utf-8")

    reviews_dir_b = tmp_path / "scratch_b" / "reviews"
    reviews_dir_b.mkdir(parents=True, exist_ok=True)

    # 1 kept + 1 dropped = ratio 0.5 exactly
    _write_role_file(
        reviews_dir_b,
        "role-good",
        blocks=[("LOW", "good-ac11b", f"{good_file_b}:1: def ac11b_anchor():")],
        selfcount=1,
    )
    _write_role_file(
        reviews_dir_b,
        "role-bad",
        blocks=[("HIGH", "drop-ac11b", "/nonexistent/feb64ba8_ac11b.py:1: x = 0")],
        selfcount=1,
    )

    ctx_b = types.SimpleNamespace(
        org_config={"scratchpad_dir": str(tmp_path / "scratch_b")},
        question="test question",
    )
    result_b = _aggregate_review_findings(ctx_b, _prev_ok(tmp_path / "scratch_b"))
    assert result_b.status == "ok", f"AC11B: unexpected error {result_b.error}"

    audit_b = (result_b.data or {}).get("findings_audit")
    assert audit_b is not None, "AC11B: findings_audit missing"
    assert "high_filter_rate" in audit_b, (
        f"AC11B: 'high_filter_rate' key missing. Got keys: {list(audit_b.keys())}"
    )
    agg_b = audit_b.get("aggregated", 0)
    flt_b = audit_b.get("filtered", 0)
    assert audit_b["high_filter_rate"] is False, (
        f"AC11B: ratio=0.5 (not strictly >0.5) → expected high_filter_rate=False, "
        f"got {audit_b['high_filter_rate']!r}. aggregated={agg_b}, filtered={flt_b}"
    )


# ─── AC12: high_filter_rate=False for clean PASS (zero findings, zero drops) ──


def test_ac12_high_filter_rate_zero_case(tmp_path, monkeypatch):
    """AC12 (MUST FAIL today): high_filter_rate=False when aggregated=0, filtered=0.

    Setup: 1 role file with no SEVERITY blocks → zero findings, zero drops.
    Audit: aggregated=0, filtered=0 → denominator=0 → high_filter_rate=False (no
    divide-by-zero).

    Pre-fix: 'high_filter_rate' key doesn't exist → KeyError/assertion fails.
    """
    reviews_dir = tmp_path / "scratch" / "reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)

    # Role file with no SEVERITY blocks — just a VERDICT line
    (reviews_dir / "role-clean.md").write_text(
        "# clean review\n\nNo issues found.\n\nVERDICT: PASS\n<!-- role-findings-count: 0 -->\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(_p6, "_emit_safe", lambda *a, **kw: None)

    ctx = _make_ctx(tmp_path)
    result = _run(ctx, tmp_path)

    assert result.status == "ok", f"AC12: expected ok, got {result.status}: {result.error}"

    audit = (result.data or {}).get("findings_audit")
    assert audit is not None, "AC12: findings_audit key missing"

    assert audit.get("aggregated") == 0, (
        f"AC12: expected aggregated=0, got {audit.get('aggregated')}"
    )
    assert audit.get("filtered") == 0, (
        f"AC12: expected filtered=0, got {audit.get('filtered')}"
    )
    assert "high_filter_rate" in audit, (
        f"AC12: 'high_filter_rate' key missing from findings_audit. "
        f"Got keys: {list(audit.keys())}. Pre-fix: key not present."
    )
    assert audit["high_filter_rate"] is False, (
        f"AC12: aggregated=0, filtered=0 → expected high_filter_rate=False (no divide-by-zero), "
        f"got {audit['high_filter_rate']!r}"
    )
