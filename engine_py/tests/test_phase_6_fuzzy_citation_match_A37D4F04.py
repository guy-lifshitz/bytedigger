"""RED tests for A37D4F04 — fuzzy/normalized citation match in `_verify_finding_quote`.

Spec: SHARED/memory/Decisions/2026-05-13_A37D4F04_fuzzy_citation_match_spec.md

Problem: `_verify_finding_quote` currently uses exact rstrip equality only.
LLM reviewers routinely cite findings with off-by-1/2 line numbers or with
normalised whitespace, causing real HIGH/CRITICAL findings to be dropped as
FABRICATED-CANDIDATE. Build #591 lost 12/13 sub-reviewer findings this way.

Fix: tiered match cascade — exact → normalised → windowed-exact → windowed-normalised
     → substring (len >= 16). First hit wins; reason code recorded.

New reason codes (all KEEP):
  OK-NORMALIZED, OK-WINDOWED, OK-WINDOWED-NORMALIZED, OK-SUBSTRING

New constants:
  _QUOTE_WINDOW_LINES = 3
  _MIN_SUBSTRING_LEN  = 16

New telemetry key:
  review_findings_audit.match_kinds: dict[str, int]  — KEEP-reason counts

AC1, AC7, AC8, AC9 are regression guards — acceptable to PASS today.
AC2, AC3, AC4, AC5, AC6, AC10, AC11, AC12 MUST FAIL today (tier-1-only code).
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest  # noqa: F401 — pytest discovery

ENGINE_PY = Path(__file__).resolve().parents[1]
if str(ENGINE_PY) not in sys.path:
    sys.path.insert(0, str(ENGINE_PY))
WORKFLOWS = ENGINE_PY / "bytedigger_engine" / "workflows"
if str(WORKFLOWS) not in sys.path:
    sys.path.insert(0, str(WORKFLOWS))

from bytedigger_engine.contracts import StepResult, WorkflowContext  # noqa: E402
from bytedigger_engine.workflows.phase_6_review import (  # noqa: E402
    VERDICT_FAIL,
    VERDICT_PASS,
    _aggregate_review_findings,
    _verify_finding_quote,
)
from bytedigger_engine.workflows import phase_6_review as _p6  # noqa: E402 — for monkeypatching _emit_safe; MUST be top-level alias (see test_906e37dc_review_findings_audit.py:358-361 — _aggregate_review_findings's globals resolve to the top-level module, not workflows.phase_6_review)


# ─── local helpers (modelled on test_phase_6_review_21792EE7.py) ─────────────


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
        session_id="test-A37D4F04",
        persona="hal",
        framework=None,
        domain=None,
    )


def _seed_role(tmp_path: Path, slug: str, body: str) -> Path:
    """Write <scratch>/reviews/role-<slug>.md with body."""
    scratch = tmp_path / "scratch"
    p = scratch / "reviews" / f"role-{slug}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


def _agg_prev(tmp_path: Path) -> StepResult:
    """Minimal prev StepResult for _aggregate_review_findings."""
    scratch = tmp_path / "scratch"
    return StepResult(
        status="ok",
        data={"raw_response": "Reviewers complete."},
        duration_ms=0,
        step_name="invoke_review_llm",
    )


def _fixture_file(
    tmp_path: Path,
    name: str = "fixture.py",
    content: str = "def foo():\n    return 42\n",
) -> Path:
    """Create a known-content file for quote-verification tests."""
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


def _finding_block(abs_path: str, lineno: int, quote: str, severity: str = "HIGH", title: str = "t") -> str:
    """Build a minimal finding block with one > path:line: quote evidence line."""
    return (
        f"### SEVERITY: {severity} — {title}\n"
        f"> {abs_path}:{lineno}: {quote}\n"
        "Confidence: HIGH\n"
        "Description: some description\n"
    )


def _role_body_with_finding(abs_path: str, lineno: int, quote: str, severity: str = "HIGH", title: str = "t") -> str:
    """Full role-file text for _aggregate_review_findings tests."""
    block = _finding_block(abs_path, lineno, quote, severity, title)
    return f"# role review\n\n{block}\nVERDICT: FAIL\n"


# ─── AC1: exact match → OK (regression guard) ────────────────────────────────


def test_a37d4f04_ac1_exact_match_ok(tmp_path):
    """AC1 (regression guard): exact match at cited line → keep with reason OK.

    This is tier-1 behaviour and MUST pass today AND post-fix.
    """
    fx = _fixture_file(tmp_path, "src.py", "def foo():\n    return 42\n")
    block = _finding_block(str(fx), 2, "    return 42")
    verify_status, reason = _verify_finding_quote(block, {})
    assert verify_status == "verified-exact", f"expected verify_status='verified-exact', got verify_status={verify_status!r}, reason={reason}"
    assert reason == "OK", f"expected reason='OK', got {reason!r}"


# ─── AC2: whitespace-normalised match → OK-NORMALIZED ────────────────────────


def test_a37d4f04_ac2_normalized_match_at_cited_line(tmp_path):
    """AC2: file line has '    return 42' (4-space indent), quote is 'return 42'.

    Today: exact rstrip compare → '    return 42' != 'return 42' → FABRICATED-CANDIDATE.
    Post-fix: normalised compare catches it → OK-NORMALIZED.
    """
    fx = _fixture_file(tmp_path, "src.py", "def foo():\n    return 42\n")
    # Cited line 2 is '    return 42'; quote collapses the indent
    block = _finding_block(str(fx), 2, "return 42")
    verify_status, reason = _verify_finding_quote(block, {})
    assert verify_status == "verified-exact", f"expected verify_status='verified-exact' for normalised match, got verify_status={verify_status!r}, reason={reason}"
    assert reason == "OK-NORMALIZED", f"expected 'OK-NORMALIZED', got {reason!r}"


# ─── AC3: off-by-N exact match → OK-WINDOWED ─────────────────────────────────


def test_a37d4f04_ac3_windowed_exact_match(tmp_path):
    """AC3: matching line is at cited+2; finding cites the earlier line.

    File:
      line 1: def foo():
      line 2:     x = 1
      line 3:     y = 2
      line 4:     return 42

    Finding cites line 2 with quote '    return 42' (which is at line 4, +2).
    Today: exact compare at line 2 fails → FABRICATED-CANDIDATE.
    Post-fix: window scan finds exact match at line 4 → OK-WINDOWED.
    """
    content = "def foo():\n    x = 1\n    y = 2\n    return 42\n"
    fx = _fixture_file(tmp_path, "src.py", content)
    block = _finding_block(str(fx), 2, "    return 42")
    verify_status, reason = _verify_finding_quote(block, {})
    assert verify_status == "verified-windowed", f"expected verify_status='verified-windowed' for windowed match, got verify_status={verify_status!r}, reason={reason}"
    assert reason == "OK-WINDOWED", f"expected 'OK-WINDOWED', got {reason!r}"


# ─── AC4: off-by-N AND normalised → OK-WINDOWED-NORMALIZED ──────────────────


def test_a37d4f04_ac4_windowed_normalized_match(tmp_path):
    """AC4: matching line is at cited+2 AND quote has whitespace differences.

    File:
      line 1: def foo():
      line 2:     x = 1
      line 3:     y = 2
      line 4:     return 42

    Finding cites line 2 with quote 'return 42' (collapses indent, at line 4).
    Today: exact compare at line 2 fails → FABRICATED-CANDIDATE.
    Post-fix: windowed normalised tier finds match → OK-WINDOWED-NORMALIZED.
    """
    content = "def foo():\n    x = 1\n    y = 2\n    return 42\n"
    fx = _fixture_file(tmp_path, "src.py", content)
    block = _finding_block(str(fx), 2, "return 42")
    verify_status, reason = _verify_finding_quote(block, {})
    assert verify_status == "verified-windowed", f"expected verify_status='verified-windowed' for windowed-normalised match, got verify_status={verify_status!r}, reason={reason}"
    assert reason == "OK-WINDOWED-NORMALIZED", f"expected 'OK-WINDOWED-NORMALIZED', got {reason!r}"


# ─── AC5: substring match (len >= 16) → OK-SUBSTRING ────────────────────────


def test_a37d4f04_ac5_substring_match_long_enough(tmp_path):
    """AC5: quote is a substring of the actual line and len >= 16 → OK-SUBSTRING.

    File line: 'await client.put_blob(name, data)  # TOCTOU'
    Quote:     'await client.put_blob'  (len=21 >= 16)
    Today: exact rstrip compare fails → FABRICATED-CANDIDATE.
    Post-fix: substring tier → OK-SUBSTRING.
    """
    line = "await client.put_blob(name, data)  # TOCTOU"
    content = f"{line}\n"
    fx = _fixture_file(tmp_path, "storage.py", content)
    quote = "await client.put_blob"
    assert len(quote.strip()) >= 16, "test setup: quote must be >= 16 chars"
    block = _finding_block(str(fx), 1, quote)
    verify_status, reason = _verify_finding_quote(block, {})
    assert verify_status == "verified-substring", f"expected verify_status='verified-substring' for substring match, got verify_status={verify_status!r}, reason={reason}"
    assert reason == "OK-SUBSTRING", f"expected 'OK-SUBSTRING', got {reason!r}"


# ─── AC6: substring too short → FABRICATED-CANDIDATE ────────────────────────


def test_a37d4f04_ac6_substring_too_short_drops(tmp_path):
    """AC6: quote shorter than _MIN_SUBSTRING_LEN (16) is NOT honoured → drops.

    Quote '})'  (len=2) — even if it's a substring of the file line, it
    should NOT keep the finding. Today this might accidentally pass (exact
    compare fails → drop), but post-fix must also drop (length guard blocks
    substring tier).
    """
    line = "    result = func()})"
    content = f"def foo():\n{line}\n"
    fx = _fixture_file(tmp_path, "src.py", content)
    quote = "})"
    assert len(quote.strip()) < 16, "test setup: quote must be < 16 chars"
    block = _finding_block(str(fx), 2, quote)
    verify_status, reason = _verify_finding_quote(block, {})
    assert verify_status == "suspect-no-match", f"expected verify_status='suspect-no-match' for short substring quote, got verify_status={verify_status!r}, reason={reason}"
    assert reason == "FABRICATED-CANDIDATE", f"expected 'FABRICATED-CANDIDATE', got {reason!r}"


# ─── AC7: beyond-window mismatch → FABRICATED-CANDIDATE (regression guard) ───


def test_a37d4f04_ac7_beyond_window_drops(tmp_path):
    """AC7 (regression guard): matching line exists but is cited+10, beyond window.

    The window is ±3 lines. A match at cited+10 must NOT be found.
    Finding drops as FABRICATED-CANDIDATE. Passes today AND post-fix.
    """
    lines = ["line_{}\n".format(i) for i in range(15)]
    # line 11 (index 10) is "line_10"; cite line 1 with that content
    content = "".join(lines)
    fx = _fixture_file(tmp_path, "src.py", content)
    # Cite line 1, but quote matches line 11 (0-indexed: 10, 1-indexed: 11)
    block = _finding_block(str(fx), 1, "line_10")
    verify_status, reason = _verify_finding_quote(block, {})
    assert verify_status == "suspect-no-match", f"expected verify_status='suspect-no-match' for beyond-window mismatch, got verify_status={verify_status!r}, reason={reason}"
    assert reason == "FABRICATED-CANDIDATE", f"expected 'FABRICATED-CANDIDATE', got {reason!r}"


# ─── AC8: existing 21792EE7 suite still passes (placeholder) ─────────────────


def test_a37d4f04_ac8_existing_21792ee7_suite_unchanged():
    """AC8: existing T01-T12 tests in test_phase_6_review_21792EE7.py must remain 12/12.

    This is an assertion-free placeholder. Verified by orchestrator running
    pytest on test_phase_6_review_21792EE7.py — must remain 12/12 green.
    No assertions here; the external suite is the gate.
    """
    pass


# ─── AC9: LINE-OUT-OF-RANGE unchanged (regression guard) ─────────────────────


def test_a37d4f04_ac9_line_out_of_range_no_whole_file_scan(tmp_path):
    """AC9 (regression guard): cited line beyond EOF → LINE-OUT-OF-RANGE drop.

    File has 2 lines. Quote matches line 1 exactly. But cited line is 99.
    Must NOT fallback to whole-file scan or return OK-WINDOWED/OK-SUBSTRING.
    Passes today AND post-fix.
    """
    content = "def foo():\n    return 42\n"
    fx = _fixture_file(tmp_path, "src.py", content)
    # cite line 99 but quote matches line 1 — out-of-range guard must fire
    block = _finding_block(str(fx), 99, "def foo():")
    verify_status, reason = _verify_finding_quote(block, {})
    assert verify_status == "suspect-file-not-found", f"expected verify_status='suspect-file-not-found' for out-of-range, got verify_status={verify_status!r}, reason={reason}"
    assert reason == "LINE-OUT-OF-RANGE", f"expected 'LINE-OUT-OF-RANGE', got {reason!r}"


# ─── AC10: match_kinds in review_findings_audit event payload ─────────────────


def test_a37d4f04_ac10_match_kinds_in_audit_event(tmp_path, monkeypatch):
    """AC10: review_findings_audit event payload gains match_kinds dict.

    Seed three findings:
      - finding A: exact match at cited line → reason OK
      - finding B: off-by-2 exact match → reason OK-WINDOWED (post-fix)
      - finding C: absolute path, mismatched quote → FABRICATED-CANDIDATE (dropped)

    After _aggregate_review_findings, the emitted review_findings_audit event
    must include:
      payload["match_kinds"]["OK"]         == 1
      payload["match_kinds"]["OK-WINDOWED"] == 1

    Today this FAILS because:
      (a) match_kinds key doesn't exist in the event payload, AND
      (b) finding B is dropped as FABRICATED-CANDIDATE (no windowed search), so
          OK-WINDOWED count would be 0 even if the key existed.
    """
    # file for finding A and B: line 1 = "def bar():", line 3 = "    return 99"
    content_ab = "def bar():\n    x = 0\n    return 99\n"
    fx_ab = _fixture_file(tmp_path, "module_ab.py", content_ab)

    # file for finding C: line 1 = "pass"
    content_c = "pass\n"
    fx_c = _fixture_file(tmp_path, "module_c.py", content_c)

    # finding A: cite line 1, quote matches exactly
    block_a = (
        "### SEVERITY: MEDIUM — finding-A\n"
        f"> {fx_ab}:1: def bar():\n"
        "Confidence: HIGH\n"
        "Description: finding A\n"
    )
    # finding B: cite line 1, but quote is at line 3 (off-by-2) — needs windowed
    block_b = (
        "### SEVERITY: HIGH — finding-B\n"
        f"> {fx_ab}:1:     return 99\n"
        "Confidence: HIGH\n"
        "Description: finding B\n"
    )
    # finding C: cite line 1, quote does NOT match → FABRICATED-CANDIDATE
    block_c = (
        "### SEVERITY: LOW — finding-C\n"
        f"> {fx_c}:1: this_does_not_match\n"
        "Confidence: HIGH\n"
        "Description: finding C\n"
    )

    role_body = (
        "# security review\n\n"
        + block_a + "\n"
        + block_b + "\n"
        + block_c + "\n"
        + "VERDICT: FAIL\n"
    )
    _seed_role(tmp_path, "security", role_body)

    # Capture emitted events
    captured: list[tuple[str, dict]] = []

    def _fake_emit(event_name: str, payload: dict) -> None:
        captured.append((event_name, payload))

    monkeypatch.setattr(_p6, "_emit_safe", _fake_emit)

    ctx = _make_ctx(tmp_path)
    prev = _agg_prev(tmp_path)
    result = _aggregate_review_findings(ctx, prev)

    assert result.status == "ok", f"unexpected status {result.status}: {result.error}"

    audit_events = [(n, p) for n, p in captured if n == "review_findings_audit"]
    assert audit_events, "no review_findings_audit event emitted"

    _, payload = audit_events[-1]
    assert "match_kinds" in payload, (
        f"'match_kinds' key missing from review_findings_audit payload; got keys: {list(payload.keys())}"
    )
    match_kinds = payload["match_kinds"]
    assert match_kinds.get("verified-exact") == 1, (
        f"expected match_kinds['verified-exact']==1, got {match_kinds}"
    )
    assert match_kinds.get("verified-windowed") == 1, (
        f"expected match_kinds['verified-windowed']==1, got {match_kinds}"
    )


# ─── AC11: verdict reflects post-fuzzy-keep counts ───────────────────────────


def test_a37d4f04_ac11_verdict_reflects_fuzzy_kept_finding(tmp_path):
    """AC11: one HIGH finding whose quote matches cited+2 — verdict must be VERDICT_FAIL.

    File has HIGH finding. Quote is at line N+2, finding cites N.

    Today: finding dropped (FABRICATED-CANDIDATE) → no HIGH findings → VERDICT_PASS.
    Post-fix: windowed match → finding kept → HIGH count=1 → VERDICT_FAIL.
    """
    # line 1: "def process():", line 2: "    lock = mutex()", line 3: "    shared_state = 1"
    content = "def process():\n    lock = mutex()\n    shared_state = 1\n"
    fx = _fixture_file(tmp_path, "worker.py", content)

    # Cite line 1, but quote is at line 3 (off-by-2) — needs windowed
    role_body = (
        "# security review\n\n"
        "### SEVERITY: HIGH — thread-safety violation\n"
        f"> {fx}:1:     shared_state = 1\n"
        "Confidence: HIGH\n"
        "Description: Unsynchronised write to shared_state.\n"
        "\nVERDICT: FAIL\n"
    )
    _seed_role(tmp_path, "security", role_body)

    ctx = _make_ctx(tmp_path)
    prev = _agg_prev(tmp_path)
    result = _aggregate_review_findings(ctx, prev)

    assert result.status == "ok", f"unexpected status {result.status}: {result.error}"
    verdict = (result.data or {}).get("verdict")
    assert verdict == VERDICT_FAIL, (
        f"expected VERDICT_FAIL because HIGH finding kept via windowed match, "
        f"got verdict={verdict!r}. "
        f"severity_counts={result.data.get('severity_counts')}, "
        f"findings_count={result.data.get('findings_count')}, "
        f"filtered_count={result.data.get('filtered_count')}"
    )


# ─── AC12: cascade precedence — highest-fidelity tier wins ───────────────────


def test_a37d4f04_ac12_cascade_picks_highest_fidelity_tier(tmp_path):
    """AC12: when multiple tiers could match, cascade returns highest-fidelity tier.

    File:
      line 1: def foo(): pass
      line 2: def foo(): pass  (also has substring + exact match possibility)

    Finding cites line 1 with exact quote 'def foo(): pass'.

    Exact match fires at tier 1 (cited line). The cascade must return OK,
    not OK-SUBSTRING or OK-WINDOWED (which are lower tiers).
    """
    content = "def foo(): pass\ndef foo(): pass\n"
    fx = _fixture_file(tmp_path, "src.py", content)
    # Cite line 1 with exact quote — tier-1 exact match should fire first
    block = _finding_block(str(fx), 1, "def foo(): pass")
    verify_status, reason = _verify_finding_quote(block, {})
    assert verify_status == "verified-exact", f"expected verify_status='verified-exact', got verify_status={verify_status!r}, reason={reason}"
    assert reason == "OK", (
        f"expected 'OK' (tier-1 exact), cascade picked wrong tier: {reason!r}"
    )
