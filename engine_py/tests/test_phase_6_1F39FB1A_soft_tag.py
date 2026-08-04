"""RED tests for 1F39FB1A — composite reviewer HARD-DROP -> SOFT-TAG architectural pivot.

Spec: SHARED/memory/Decisions/2026-05-14_1F39FB1A_composite_verify_soft_tag_spec.md

Pivot summary:
  - _verify_finding_quote: return (verify_status: str, reason: str) enum instead of
    (keep: bool, reason: str). 6 status codes: verified-exact, verified-windowed,
    verified-substring, suspect-no-match, suspect-no-quote, suspect-file-not-found.
  - REMOVE FEB64BA8 outer-backtick-strip block (L1271-1277 in phase_6_review.py).
  - _aggregate_review_findings: annotate-and-keep ALL findings; no drops.
  - Rename event composite_finding_dropped -> composite_finding_quote_verified; emit for ALL.
  - Composite doc: verified in ## Aggregated Findings; suspect in ## Suspect Findings (NOT
    ## Filtered Findings). Suspect header includes [verify: <verify_status>] tag.
  - helper.check_citation (Path 2): strip outer backtick pair (post-lstrip) before substring
    check; rename [UNVERIFIED: ...] tags -> [verify: suspect-*] enum.

Expected pre-pivot status: ALL 16 ACs MUST FAIL (current code has wrong return types,
wrong event name, wrong section heading, FEB64BA8 strip still present, old tag strings).
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest  # noqa: F401

ENGINE_PY = Path(__file__).resolve().parents[1]
if str(ENGINE_PY) not in sys.path:
    sys.path.insert(0, str(ENGINE_PY))
WORKFLOWS = ENGINE_PY / "bytedigger_engine" / "workflows"
if str(WORKFLOWS) not in sys.path:
    sys.path.insert(0, str(WORKFLOWS))

from bytedigger_engine.contracts import StepResult  # noqa: E402
from bytedigger_engine.workflows.phase_6_review import (  # noqa: E402
    _aggregate_review_findings,
    _verify_finding_quote,
)
from bytedigger_engine.workflows import phase_6_review as _p6  # noqa: E402 — top-level alias for monkeypatching _emit_safe.
# CRITICAL: patch _p6 (the top-level "phase_6_review" module), NOT "workflows.phase_6_review".
# _aggregate_review_findings's globals resolve to the top-level module — see test_906e37dc:358-363.

from bytedigger_engine.lib.plugins.anti_hallucination.helper import check_citation  # noqa: E402


# ─── helpers ──────────────────────────────────────────────────────────────────


def _make_ctx(tmp_path: Path) -> types.SimpleNamespace:
    """Minimal context for _aggregate_review_findings."""
    scratch = tmp_path / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    return types.SimpleNamespace(
        org_config={"scratchpad_dir": str(scratch)},
        question="test question",
    )


def _prev_ok(scratchpad: Path) -> StepResult:
    """Minimal prev StepResult."""
    return StepResult(status="ok", data={}, duration_ms=0, step_name="x")


def _run_agg(ctx: types.SimpleNamespace, tmp_path: Path) -> StepResult:
    """Call _aggregate_review_findings with a minimal prev."""
    scratch = tmp_path / "scratch"
    return _aggregate_review_findings(ctx, _prev_ok(scratch))


def _write_role_file(
    reviews_dir: Path,
    slug: str,
    *,
    blocks: list[tuple[str, str, str]],
    selfcount: int | None,
) -> None:
    """Write reviews_dir/role-<slug>.md.

    blocks: list of (severity, title, evidence_after_gt_space) triples.
    The evidence string is placed after '> ' verbatim (it may contain path:N:content).
    """
    reviews_dir.mkdir(parents=True, exist_ok=True)
    lines: list[str] = [f"# {slug} Review", ""]
    for severity, title, evidence in blocks:
        lines.append(f"### SEVERITY: {severity} — {title}")
        lines.append(f"> {evidence}")
        lines.append("Confidence: HIGH")
        lines.append("Description: test description")
        lines.append("")
    lines.append("VERDICT: PARTIAL")
    if selfcount is not None:
        lines.append(f"<!-- role-findings-count: {selfcount} -->")
    (reviews_dir / f"role-{slug}.md").write_text("\n".join(lines), encoding="utf-8")


def _finding_block(abs_path: str, lineno: int, quote_content: str,
                   severity: str = "HIGH", title: str = "test-finding") -> str:
    """Build a finding block with the given raw quote_content after 'path:N: '."""
    return (
        f"### SEVERITY: {severity} — {title}\n"
        f"> {abs_path}:{lineno}: {quote_content}\n"
        "Confidence: HIGH\n"
        "Description: test description\n"
    )


# ─── AC1: verified-exact on plain exact match ─────────────────────────────────


def test_ac1_verified_exact_plain_match(tmp_path):
    """AC1: _verify_finding_quote returns ("verified-exact", "OK") on plain exact match.

    File line 3 = 'def hello():'. Evidence: plain form, no normalization needed.
    Post-pivot: return type is (str, str) with verify_status="verified-exact".
    Pre-pivot: returns (True, "OK") — wrong type shape (bool, not str).
    """
    target = tmp_path / "greet.py"
    target.write_text("# line 1\n# line 2\ndef hello():\n    pass\n", encoding="utf-8")

    block = _finding_block(str(target), 3, "def hello():")
    result = _verify_finding_quote(block, {})

    assert isinstance(result, tuple) and len(result) == 2, (
        f"AC1: _verify_finding_quote must return 2-tuple, got {result!r}"
    )
    verify_status, reason = result
    assert verify_status == "verified-exact", (
        f"AC1: expected verify_status='verified-exact' (tier-1 plain exact), "
        f"got {verify_status!r}. Pre-pivot returns (True, 'OK') — bool, not str."
    )
    assert reason == "OK", (
        f"AC1: expected reason='OK' (legacy granular code preserved), got {reason!r}"
    )


# ─── AC2: verified-exact on whitespace-normalized match ───────────────────────


def test_ac2_verified_exact_normalized_match(tmp_path):
    """AC2: _verify_finding_quote returns ("verified-exact", "OK-NORMALIZED") on tier-2 hit.

    File line 1 = 'x  =   1' (double spaces). Evidence: 'x = 1' (normalized).
    Tier-1 fails (exact rstrip); tier-2 passes (normalize_for_match).
    Post-pivot: verify_status="verified-exact", reason="OK-NORMALIZED".
    Pre-pivot: returns (True, "OK-NORMALIZED") — bool instead of str enum.
    """
    target = tmp_path / "norm.py"
    target.write_text("x  =   1\n", encoding="utf-8")

    block = _finding_block(str(target), 1, "x = 1")
    result = _verify_finding_quote(block, {})

    assert isinstance(result, tuple) and len(result) == 2, (
        f"AC2: must return 2-tuple, got {result!r}"
    )
    verify_status, reason = result
    assert verify_status == "verified-exact", (
        f"AC2: expected verify_status='verified-exact' (tier-2 normalized at cited line), "
        f"got {verify_status!r}. Pre-pivot returns (True, 'OK-NORMALIZED')."
    )
    assert reason == "OK-NORMALIZED", (
        f"AC2: expected reason='OK-NORMALIZED' (granular tier code), got {reason!r}"
    )


# ─── AC3: verified-windowed on windowed exact match ──────────────────────────


def test_ac3_verified_windowed_exact_match(tmp_path):
    """AC3: _verify_finding_quote returns ("verified-windowed", "OK-WINDOWED") on tier-3 hit.

    File has 7 lines. Cited line=4 but the quote matches line 2 (within +-3 window).
    Tier-1 and tier-2 fail at line 4; tier-3 exact scan finds it at line 2.
    Post-pivot: verify_status="verified-windowed", reason="OK-WINDOWED".
    Pre-pivot: returns (True, "OK-WINDOWED") — bool instead of str enum.
    """
    target = tmp_path / "windowed.py"
    target.write_text(
        "# line 1\n"
        "TARGET_LINE_HERE\n"  # line 2 — the actual match
        "# line 3\n"
        "# line 4 — cited but wrong\n"  # line 4 — cited, no match
        "# line 5\n"
        "# line 6\n"
        "# line 7\n",
        encoding="utf-8",
    )

    # Cite line 4 but quote matches line 2 (within +-3 window of line 4)
    block = _finding_block(str(target), 4, "TARGET_LINE_HERE")
    result = _verify_finding_quote(block, {})

    assert isinstance(result, tuple) and len(result) == 2, (
        f"AC3: must return 2-tuple, got {result!r}"
    )
    verify_status, reason = result
    assert verify_status == "verified-windowed", (
        f"AC3: expected verify_status='verified-windowed' (tier-3 windowed exact), "
        f"got {verify_status!r}. Pre-pivot returns (True, 'OK-WINDOWED')."
    )
    assert reason == "OK-WINDOWED", (
        f"AC3: expected reason='OK-WINDOWED', got {reason!r}"
    )


# ─── AC4: verified-substring on tier-5 substring match ────────────────────────


def test_ac4_verified_substring_match(tmp_path):
    """AC4: _verify_finding_quote returns ("verified-substring", "OK-SUBSTRING") on tier-5 hit.

    File line 1 = 'x = some_very_long_variable_name_here + extra_stuff'.
    Quote = 'some_very_long_variable_name_here' (>= _MIN_SUBSTRING_LEN=16, substring).
    Tiers 1-4 fail (not equal, normalized, windowed, windowed-normalized).
    Tier 5 substring match fires.
    Post-pivot: verify_status="verified-substring", reason="OK-SUBSTRING".
    Pre-pivot: returns (True, "OK-SUBSTRING") — bool instead of str enum.
    """
    long_fragment = "some_very_long_variable_name_here"
    assert len(long_fragment.strip()) >= 16, "fixture must satisfy _MIN_SUBSTRING_LEN"
    target = tmp_path / "substring.py"
    target.write_text(
        f"x = {long_fragment} + extra_stuff\n",
        encoding="utf-8",
    )

    block = _finding_block(str(target), 1, long_fragment)
    result = _verify_finding_quote(block, {})

    assert isinstance(result, tuple) and len(result) == 2, (
        f"AC4: must return 2-tuple, got {result!r}"
    )
    verify_status, reason = result
    assert verify_status == "verified-substring", (
        f"AC4: expected verify_status='verified-substring' (tier-5 substring), "
        f"got {verify_status!r}. Pre-pivot returns (True, 'OK-SUBSTRING')."
    )
    assert reason == "OK-SUBSTRING", (
        f"AC4: expected reason='OK-SUBSTRING', got {reason!r}"
    )


# ─── AC5: suspect-no-match on FEB64BA8 outer-backtick case ────────────────────


def test_ac5_suspect_no_match_backtick_wrap_feb64ba8_removed(tmp_path):
    """AC5: _verify_finding_quote returns ("suspect-no-match", "FABRICATED-CANDIDATE")
    on backtick-wrapped quote after FEB64BA8 strip is REMOVED.

    File line 5 = '    pass'. Evidence: > path:5: `    pass` (outer backtick pair).
    POST-PIVOT: FEB64BA8 strip block removed. Backtick-wrapped quote goes through all 5
    tiers and fails them all (file has '    pass', not '`    pass`'). Result: suspect-no-match.
    PRE-PIVOT (today's code): FEB64BA8 strip fires, strips backtick pair, tier-1 matches ->
    returns (True, "OK"). This AC MUST FAIL today and PASS only after strip is removed.

    Regression guard for Frozen Design item (2): verifies the strip is gone.
    """
    target = tmp_path / "foo.py"
    target.write_text(
        "def foo():\n"
        "    x = 1\n"
        "    y = 2\n"
        "    z = 3\n"
        "    pass\n",
        encoding="utf-8",
    )

    # Outer backtick wrapping — what FEB64BA8 used to strip
    block = _finding_block(str(target), 5, "`    pass`")
    result = _verify_finding_quote(block, {})

    assert isinstance(result, tuple) and len(result) == 2, (
        f"AC5: must return 2-tuple, got {result!r}"
    )
    verify_status, reason = result
    assert verify_status == "suspect-no-match", (
        f"AC5: post-pivot (FEB64BA8 strip REMOVED), backtick-wrapped quote must return "
        f"verify_status='suspect-no-match'. Got {verify_status!r}. "
        f"Pre-pivot behavior: strip fires -> returns (True, 'OK'). "
        f"This AC MUST FAIL today (strip still present)."
    )
    assert reason == "FABRICATED-CANDIDATE", (
        f"AC5: expected reason='FABRICATED-CANDIDATE' (all 5 tiers fail), got {reason!r}"
    )


# ─── AC6: suspect-no-quote on missing > path:line: ───────────────────────────


def test_ac6_suspect_no_quote_missing_citation(tmp_path):
    """AC6: _verify_finding_quote returns ("suspect-no-quote", "MISSING-QUOTE")
    when the finding block has no > path:line: line.

    Post-pivot: verify_status="suspect-no-quote", reason="MISSING-QUOTE".
    Pre-pivot: returns (False, "MISSING-QUOTE") — bool False instead of str enum.
    """
    block = (
        "### SEVERITY: HIGH — no-citation-finding\n"
        "No evidence line here.\n"
        "Confidence: HIGH\n"
        "Description: finding with no quote line\n"
    )
    result = _verify_finding_quote(block, {})

    assert isinstance(result, tuple) and len(result) == 2, (
        f"AC6: must return 2-tuple, got {result!r}"
    )
    verify_status, reason = result
    assert verify_status == "suspect-no-quote", (
        f"AC6: expected verify_status='suspect-no-quote' (no > path:line: line), "
        f"got {verify_status!r}. Pre-pivot returns (False, 'MISSING-QUOTE') — bool."
    )
    assert reason == "MISSING-QUOTE", (
        f"AC6: expected reason='MISSING-QUOTE', got {reason!r}"
    )


# ─── AC7: suspect-file-not-found for missing file AND line-out-of-range ───────


@pytest.mark.parametrize("case,path_factory,lineno,expected_reason", [
    (
        "FILE-NOT-FOUND",
        lambda tmp_path: "/nonexistent/1f39fb1a_no_such_file.py",
        1,
        "FILE-NOT-FOUND",
    ),
    (
        "LINE-OUT-OF-RANGE",
        lambda tmp_path: str(tmp_path / "short.py"),
        999,
        "LINE-OUT-OF-RANGE",
    ),
])
def test_ac7_suspect_file_not_found_cases(tmp_path, case, path_factory, lineno, expected_reason):
    """AC7: _verify_finding_quote returns ("suspect-file-not-found", <reason>) for both
    FILE-NOT-FOUND and LINE-OUT-OF-RANGE cases.

    Both old reasons collapse to the same verify_status="suspect-file-not-found".
    The reason string distinguishes them for forensic queries.
    Pre-pivot: returns (False, "FILE-NOT-FOUND") or (False, "LINE-OUT-OF-RANGE") — bool.
    """
    if case == "LINE-OUT-OF-RANGE":
        (tmp_path / "short.py").write_text("one line only\n", encoding="utf-8")

    path_str = path_factory(tmp_path)
    block = _finding_block(path_str, lineno, "does not matter")
    result = _verify_finding_quote(block, {})

    assert isinstance(result, tuple) and len(result) == 2, (
        f"AC7[{case}]: must return 2-tuple, got {result!r}"
    )
    verify_status, reason = result
    assert verify_status == "suspect-file-not-found", (
        f"AC7[{case}]: expected verify_status='suspect-file-not-found', "
        f"got {verify_status!r}. Pre-pivot returns (False, {expected_reason!r}) — bool."
    )
    assert reason == expected_reason, (
        f"AC7[{case}]: expected reason={expected_reason!r} (granular code for forensics), "
        f"got {reason!r}"
    )


# ─── AC8: suspect-file-not-found on unresolvable relative path ────────────────


def test_ac8_suspect_file_not_found_relative_path(tmp_path):
    """AC8: _verify_finding_quote returns ("suspect-file-not-found", "OK-UNVERIFIABLE-RELATIVE")
    when the path is relative and doesn't resolve.

    Post-pivot: verify_status="suspect-file-not-found" (honest — can't verify).
    Reason preserves the legacy code "OK-UNVERIFIABLE-RELATIVE" for forensic queries.
    Pre-pivot: returns (True, "OK-UNVERIFIABLE-RELATIVE") — bool True (benefit of doubt),
    now wrong. Must change to suspect (truthful).
    """
    # Use a relative path that won't exist under cwd
    block = _finding_block("relative/nonexistent_1f39fb1a.py", 1, "some content")
    result = _verify_finding_quote(block, {})

    assert isinstance(result, tuple) and len(result) == 2, (
        f"AC8: must return 2-tuple, got {result!r}"
    )
    verify_status, reason = result
    assert verify_status == "suspect-file-not-found", (
        f"AC8: expected verify_status='suspect-file-not-found' (relative path, can't verify), "
        f"got {verify_status!r}. Pre-pivot returns (True, 'OK-UNVERIFIABLE-RELATIVE') — "
        f"benefit-of-the-doubt KEEP is now wrong; post-pivot is honest suspect."
    )
    assert reason == "OK-UNVERIFIABLE-RELATIVE", (
        f"AC8: expected reason='OK-UNVERIFIABLE-RELATIVE' (preserved for forensics), got {reason!r}"
    )


# ─── AC9: _aggregate_review_findings keeps ALL findings (no drops) ─────────────


def test_ac9_aggregate_keeps_all_findings(tmp_path, monkeypatch):
    """AC9: _aggregate_review_findings passes ALL findings through (no drops).

    Setup:
      - role-verified: 1 finding with exact-match citation -> verified-exact
      - role-noquote: 1 finding with no > path:line: line -> suspect-no-quote
      - role-backtick: 1 finding with outer-backtick-wrapped quote -> suspect-no-match

    Post-pivot: all 3 appear in composite output.
      - verified-exact finding in ## Aggregated Findings
      - 2 suspect findings in ## Suspect Findings (NOT ## Filtered Findings)
      - local filtered_findings list == [] (no drops)

    Pre-pivot: backtick-wrap and no-quote findings are DROPPED -> not in output.
    """
    good_file = tmp_path / "scratch" / "good.py"
    good_file.parent.mkdir(parents=True, exist_ok=True)
    good_file.write_text("def verified_func():\n    pass\n", encoding="utf-8")

    reviews_dir = tmp_path / "scratch" / "reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)

    # role-verified: exact match
    _write_role_file(
        reviews_dir, "role-verified",
        blocks=[("MEDIUM", "verified-finding", f"{good_file}:1: def verified_func():")],
        selfcount=1,
    )

    # role-noquote: no > path:line: line at all
    no_quote_lines = [
        "# noquote review", "",
        "### SEVERITY: LOW — no-citation-finding",
        "No evidence line — reviewer forgot the citation.",
        "Confidence: HIGH", "Description: no citation", "",
        "VERDICT: PARTIAL",
        "<!-- role-findings-count: 1 -->",
    ]
    (reviews_dir / "role-noquote.md").write_text("\n".join(no_quote_lines), encoding="utf-8")

    # role-backtick: outer backtick wrap (post-pivot: suspect-no-match since strip removed)
    backtick_lines = [
        "# backtick review", "",
        "### SEVERITY: HIGH — backtick-wrapped-finding",
        f"> {good_file}:1: `def verified_func():`",
        "Confidence: HIGH", "Description: backtick wrap", "",
        "VERDICT: FAIL",
        "<!-- role-findings-count: 1 -->",
    ]
    (reviews_dir / "role-backtick.md").write_text("\n".join(backtick_lines), encoding="utf-8")

    monkeypatch.setattr(_p6, "_emit_safe", lambda *a, **kw: None)

    ctx = _make_ctx(tmp_path)
    result = _run_agg(ctx, tmp_path)

    assert result.status == "ok", f"AC9: expected ok, got {result.status}: {result.error}"

    content = (result.data or {}).get("aggregated_content", "")

    assert "## Aggregated Findings" in content, (
        "AC9: '## Aggregated Findings' section missing from composite output"
    )
    assert "## Suspect Findings" in content, (
        "AC9: '## Suspect Findings' section missing. Post-pivot: suspect findings go here, "
        "NOT ## Filtered Findings. Pre-pivot: section doesn't exist."
    )
    assert "## Filtered Findings" not in content, (
        "AC9: '## Filtered Findings' must NOT appear post-pivot (renamed to ## Suspect Findings)"
    )
    assert "verified-finding" in content, (
        "AC9: verified-exact finding must appear in composite output"
    )
    assert "no-citation-finding" in content, (
        "AC9: suspect-no-quote finding must appear in composite output (no drops post-pivot)"
    )
    assert "backtick-wrapped-finding" in content, (
        "AC9: suspect-no-match finding must appear in composite output (no drops post-pivot)"
    )


# ─── AC10: composite_finding_quote_verified emitted for ALL; no composite_finding_dropped ──


def test_ac10_event_name_renamed_and_emitted_for_all(tmp_path, monkeypatch):
    """AC10: _aggregate_review_findings emits exactly N composite_finding_quote_verified
    events for N input findings; zero composite_finding_dropped events.

    Post-pivot: event name is composite_finding_quote_verified; emitted once per finding
    regardless of verify status. Old event name composite_finding_dropped is GONE.
    Pre-pivot: emits composite_finding_dropped only for dropped findings; zero for kept ones.
    This AC MUST FAIL today (wrong event name + wrong count per finding).
    """
    good_file = tmp_path / "scratch" / "good.py"
    good_file.parent.mkdir(parents=True, exist_ok=True)
    good_file.write_text("def ac10_func():\n    pass\n", encoding="utf-8")

    reviews_dir = tmp_path / "scratch" / "reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)

    # 2 findings: 1 verified, 1 suspect (no-quote)
    _write_role_file(
        reviews_dir, "role-a",
        blocks=[("HIGH", "verified-ac10", f"{good_file}:1: def ac10_func():")],
        selfcount=1,
    )
    no_quote_lines = [
        "# ac10 review", "",
        "### SEVERITY: MEDIUM — suspect-ac10",
        "No citation line.",
        "Confidence: HIGH", "Description: no citation", "",
        "VERDICT: PARTIAL",
        "<!-- role-findings-count: 1 -->",
    ]
    (reviews_dir / "role-b.md").write_text("\n".join(no_quote_lines), encoding="utf-8")

    captured: list[tuple[str, dict]] = []

    def _fake_emit(event_name: str, payload: dict) -> None:
        captured.append((event_name, payload))

    monkeypatch.setattr(_p6, "_emit_safe", _fake_emit)

    ctx = _make_ctx(tmp_path)
    _run_agg(ctx, tmp_path)

    verified_events = [
        (n, p) for n, p in captured if n == "composite_finding_quote_verified"
    ]
    dropped_events = [
        (n, p) for n, p in captured if n == "composite_finding_dropped"
    ]

    assert len(dropped_events) == 0, (
        f"AC10: composite_finding_dropped must NOT be emitted post-pivot (event name is GONE). "
        f"Got {len(dropped_events)} composite_finding_dropped events. "
        f"Pre-pivot behavior: dropped findings emit composite_finding_dropped."
    )
    # 2 input findings -> 2 composite_finding_quote_verified events
    assert len(verified_events) == 2, (
        f"AC10: expected 2 composite_finding_quote_verified events (1 per finding), "
        f"got {len(verified_events)}. Pre-pivot: event doesn't exist at all."
    )


# ─── AC11: composite_finding_quote_verified payload has all 7 keys ─────────────


def test_ac11_event_payload_has_all_7_keys(tmp_path, monkeypatch):
    """AC11: each composite_finding_quote_verified payload contains all 7 required keys
    with correct types/values.

    Keys: phase, role, severity, title, verify_status, verify_reason, quote_line_present.
    phase == "phase_6_review". verify_status is one of 6 enum codes.
    Pre-pivot: event not emitted at all (wrong name) -> assertion fails.
    """
    good_file = tmp_path / "scratch" / "good.py"
    good_file.parent.mkdir(parents=True, exist_ok=True)
    good_file.write_text("def ac11_func():\n    pass\n", encoding="utf-8")

    reviews_dir = tmp_path / "scratch" / "reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)

    _write_role_file(
        reviews_dir, "role-a",
        blocks=[("CRITICAL", "ac11-finding", f"{good_file}:1: def ac11_func():")],
        selfcount=1,
    )

    captured: list[tuple[str, dict]] = []

    def _fake_emit(event_name: str, payload: dict) -> None:
        captured.append((event_name, payload))

    monkeypatch.setattr(_p6, "_emit_safe", _fake_emit)

    ctx = _make_ctx(tmp_path)
    _run_agg(ctx, tmp_path)

    verified_events = [
        (n, p) for n, p in captured if n == "composite_finding_quote_verified"
    ]
    assert len(verified_events) >= 1, (
        f"AC11: expected at least 1 composite_finding_quote_verified event, got 0. "
        f"Pre-pivot: event name is composite_finding_dropped (wrong)."
    )

    valid_statuses = {
        "verified-exact", "verified-windowed", "verified-substring",
        "suspect-no-match", "suspect-no-quote", "suspect-file-not-found",
    }
    required_keys = {"phase", "role", "severity", "title", "verify_status", "verify_reason",
                     "quote_line_present"}

    for _evt_name, payload in verified_events:
        missing = required_keys - set(payload.keys())
        assert not missing, (
            f"AC11: composite_finding_quote_verified payload missing keys: {missing}. "
            f"Payload keys: {list(payload.keys())}"
        )
        assert payload["phase"] == "phase_6_review", (
            f"AC11: payload['phase'] must be 'phase_6_review', got {payload['phase']!r}"
        )
        assert payload["verify_status"] in valid_statuses, (
            f"AC11: payload['verify_status']={payload['verify_status']!r} not in valid enum codes: "
            f"{valid_statuses}"
        )
        assert isinstance(payload["quote_line_present"], bool), (
            f"AC11: 'quote_line_present' must be bool, got {type(payload['quote_line_present'])}"
        )
        assert isinstance(payload.get("verify_reason"), (str, type(None))), (
            f"AC11: 'verify_reason' must be str or None, got {type(payload.get('verify_reason'))}"
        )


# ─── AC12: ## Suspect Findings section with [verify: status] tag on headers ───


def test_ac12_suspect_findings_section_and_tag(tmp_path, monkeypatch):
    """AC12: composite doc has ## Suspect Findings (NOT ## Filtered Findings) when
    >= 1 finding has suspect status. Each suspect header includes [verify: <status>] tag.
    Verified findings remain in ## Aggregated Findings (no [verify: ...] tag).

    Setup: 1 CRITICAL verified + 1 HIGH suspect-no-match + 1 MEDIUM suspect-no-quote.
    Post-pivot: both sections present; tags on suspect headers.
    Pre-pivot: ## Filtered Findings used instead; no [verify: ...] tags. MUST FAIL today.
    """
    good_file = tmp_path / "scratch" / "good.py"
    good_file.parent.mkdir(parents=True, exist_ok=True)
    good_file.write_text("def verified_critical():\n    pass\n", encoding="utf-8")

    reviews_dir = tmp_path / "scratch" / "reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)

    # role-verified: CRITICAL, exact match
    _write_role_file(
        reviews_dir, "role-verified",
        blocks=[("CRITICAL", "verified-critical-finding", f"{good_file}:1: def verified_critical():")],
        selfcount=1,
    )

    # role-suspect-match: HIGH, backtick-wrapped (post-pivot: suspect-no-match; strip removed)
    suspect_match_lines = [
        "# suspect-match review", "",
        "### SEVERITY: HIGH — high-suspect-no-match",
        f"> {good_file}:1: `def verified_critical():`",
        "Confidence: HIGH", "Description: backtick wrapped -> suspect-no-match post pivot", "",
        "VERDICT: FAIL",
        "<!-- role-findings-count: 1 -->",
    ]
    (reviews_dir / "role-suspect-match.md").write_text(
        "\n".join(suspect_match_lines), encoding="utf-8"
    )

    # role-suspect-quote: MEDIUM, no citation line
    suspect_quote_lines = [
        "# suspect-quote review", "",
        "### SEVERITY: MEDIUM — medium-suspect-no-quote",
        "No evidence citation line present.",
        "Confidence: HIGH", "Description: no citation", "",
        "VERDICT: PARTIAL",
        "<!-- role-findings-count: 1 -->",
    ]
    (reviews_dir / "role-suspect-quote.md").write_text(
        "\n".join(suspect_quote_lines), encoding="utf-8"
    )

    monkeypatch.setattr(_p6, "_emit_safe", lambda *a, **kw: None)

    ctx = _make_ctx(tmp_path)
    result = _run_agg(ctx, tmp_path)
    assert result.status == "ok", f"AC12: expected ok, got {result.status}: {result.error}"

    content = (result.data or {}).get("aggregated_content", "")

    assert "## Suspect Findings" in content, (
        "AC12: '## Suspect Findings' section must be present post-pivot. "
        "Pre-pivot: '## Filtered Findings' used instead."
    )
    assert "## Filtered Findings" not in content, (
        "AC12: '## Filtered Findings' must NOT appear post-pivot."
    )
    assert "## Aggregated Findings" in content, (
        "AC12: '## Aggregated Findings' must still be present."
    )
    assert "verified-critical-finding" in content, (
        "AC12: verified finding must appear in ## Aggregated Findings"
    )

    # Each suspect finding header must include [verify: <status>] tag
    assert "[verify: suspect-no-match]" in content, (
        "AC12: suspect-no-match finding header must include '[verify: suspect-no-match]' tag. "
        "Pre-pivot: '## DROPPED (FABRICATED-CANDIDATE) ...' format, no [verify: ...] tag."
    )
    assert "[verify: suspect-no-quote]" in content, (
        "AC12: suspect-no-quote finding header must include '[verify: suspect-no-quote]' tag."
    )


# ─── AC13: ## Summary severity counts reflect verified findings only ───────────


def test_ac13_summary_counts_verified_only(tmp_path, monkeypatch):
    """AC13: ## Summary severity counts reflect VERIFIED findings only.
    VERDICT computed from verified only.

    Setup: 1 HIGH verified + 1 HIGH suspect (backtick-wrapped -> suspect-no-match).
    Expected: ## Summary shows HIGH: 1 (not 2). VERDICT FAIL (from verified HIGH).
    Pre-pivot: the suspect finding is dropped so same count=1, but the mechanism differs;
    the real test is that all 3 appear in output while summary=1.
    """
    good_file = tmp_path / "scratch" / "good.py"
    good_file.parent.mkdir(parents=True, exist_ok=True)
    good_file.write_text("def ac13_func():\n    pass\n", encoding="utf-8")

    reviews_dir = tmp_path / "scratch" / "reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)

    # 1 HIGH verified
    _write_role_file(
        reviews_dir, "role-verified",
        blocks=[("HIGH", "high-verified-ac13", f"{good_file}:1: def ac13_func():")],
        selfcount=1,
    )

    # 1 HIGH suspect (backtick-wrapped -> suspect-no-match after FEB64BA8 strip removed)
    suspect_lines = [
        "# suspect review", "",
        "### SEVERITY: HIGH — high-suspect-ac13",
        f"> {good_file}:1: `def ac13_func():`",
        "Confidence: HIGH", "Description: backtick wrap -> suspect", "",
        "VERDICT: FAIL",
        "<!-- role-findings-count: 1 -->",
    ]
    (reviews_dir / "role-suspect.md").write_text("\n".join(suspect_lines), encoding="utf-8")

    monkeypatch.setattr(_p6, "_emit_safe", lambda *a, **kw: None)

    ctx = _make_ctx(tmp_path)
    result = _run_agg(ctx, tmp_path)
    assert result.status == "ok", f"AC13: expected ok, got {result.status}: {result.error}"

    content = (result.data or {}).get("aggregated_content", "")

    # Summary must show HIGH: 1 (not 2) — suspect not counted in summary
    assert "HIGH: 1" in content, (
        f"AC13: ## Summary must show 'HIGH: 1' (verified only), not 2. "
        f"Content around Summary: {content[content.find('## Summary'):content.find('## Summary')+200]!r}"
    )

    # Both findings must be visible in the doc (verified in Aggregated, suspect in Suspect)
    assert "high-verified-ac13" in content, (
        "AC13: verified HIGH finding must appear in composite output"
    )
    assert "high-suspect-ac13" in content, (
        "AC13: suspect HIGH finding must appear in composite output (not dropped)"
    )

    # VERDICT must be FAIL (from verified HIGH finding)
    assert "VERDICT: FAIL" in content, (
        "AC13: VERDICT must be FAIL (verified HIGH present)"
    )

    # findings_count in result.data reflects verified only
    findings_count = (result.data or {}).get("findings_count", -1)
    assert findings_count == 1, (
        f"AC13: findings_count in result.data must be 1 (verified only), got {findings_count}"
    )


# ─── AC14: Incident 1 verbatim repro (silent-failure-hunter outer-backtick) ───


def test_ac14_incident1_silent_failure_hunter_repro(tmp_path):
    """AC14: Incident 1 (forge-1778740290-9e9bbed1) verbatim reproduction.

    silent-failure-hunter quote:
      > .github/workflows/deploy-docker.yml:105: `              STATE=$(az containerapp revision show \\`

    File line 105 = '              STATE=$(az containerapp revision show \\'
    (no backtick wrapping in actual file).

    POST-PIVOT (FEB64BA8 strip removed):
      - No backtick strip fires
      - All 5 tiers fail (backtick-wrapped quote != plain file line)
      - Returns ("suspect-no-match", "FABRICATED-CANDIDATE")
      - Finding passes THROUGH to ## Suspect Findings — not silently dropped

    PRE-PIVOT (FEB64BA8 strip present, today's code):
      - Outer backtick pair stripped -> plain content -> tier-1 exact match
      - Returns (True, "OK") -> kept in ## Aggregated Findings (wrong: strip hid the issue)
      - This AC MUST FAIL today.

    Reference: SHARED/state/composite-reviewer-drop-incidents/2026-05-14_forge-1778740290-9e9bbed1/INCIDENT.md
    """
    # Build file with 105 lines; line 105 is the incident content (no wrapping backtick)
    line_105_content = r'              STATE=$(az containerapp revision show \\'
    filler = ["# line {}\n".format(i) for i in range(1, 105)]
    all_lines = filler + [line_105_content + "\n"]
    assert len(all_lines) == 105, f"fixture must have 105 lines, got {len(all_lines)}"

    deploy_file = tmp_path / "deploy-docker.yml"
    deploy_file.write_text("".join(all_lines), encoding="utf-8")

    # Evidence: backtick-wrapped (what the silent-failure-hunter sub-agent produced)
    # Use single-quoted string + explicit escape to avoid Python 3.12+ SyntaxWarning
    evidence_content = '`' + line_105_content + '`'
    block = _finding_block(str(deploy_file), 105, evidence_content)

    result = _verify_finding_quote(block, {})

    assert isinstance(result, tuple) and len(result) == 2, (
        f"AC14: must return 2-tuple, got {result!r}"
    )
    verify_status, reason = result
    assert verify_status == "suspect-no-match", (
        f"AC14 (Incident 1 repro): post-pivot must return verify_status='suspect-no-match' "
        f"(FEB64BA8 strip removed, all 5 tiers fail). "
        f"Got {verify_status!r}. Pre-pivot (today): strip fires -> (True, 'OK'). "
        f"MUST FAIL today."
    )
    assert reason == "FABRICATED-CANDIDATE", (
        f"AC14: expected reason='FABRICATED-CANDIDATE', got {reason!r}"
    )


# ─── AC15: Incident 2 verbatim repro (Issue A — leading-ws+outer-backtick) ────


def test_ac15_incident2_leading_ws_outer_backtick(tmp_path):
    """AC15: Incident 2 (forge-1778762066-9e350777) Issue A verbatim reproduction.

    code-reviewer quote from role-code-reviewer.md:
      > tests/test_deploy_docker_workflow.py:113:         `        The regex must match both
         escaped (\\`100\\`) and unescaped (`100`) forms.`

    File line 113 = '        The regex must match both escaped (\\`100\\`) and unescaped (`100`) forms.'
    (no outer backtick wrapping in actual file; line is inside a Python docstring).

    POST-PIVOT:
      - FEB64BA8 strip: `quoted.startswith('`')` -> False (leading spaces before backtick)
        -> strip doesn't fire (same as pre-pivot, actually)
      - All 5 tiers fail (backtick-wrapped quote != plain file line)
      - Returns ("suspect-no-match", "FABRICATED-CANDIDATE")
      - Finding passes through to ## Suspect Findings

    PRE-PIVOT:
      - Same result: (False, "FABRICATED-CANDIDATE") because leading spaces prevent startswith.
      - But the return type is (bool, str) not (str, str) — test fails on verify_status check.
      - This AC MUST FAIL today (wrong return type shape).

    Reference: SHARED/state/composite-reviewer-drop-incidents/2026-05-14_forge-1778762066-9e350777/INCIDENT.md
    """
    # Actual file line content (no outer backtick wrapping)
    actual_line = (
        r'        The regex must match both escaped (\`100\`) and unescaped (`100`) forms.'
    )
    filler = ["# line {}\n".format(i) for i in range(1, 113)]
    all_lines = filler + [actual_line + "\n"]
    assert len(all_lines) == 113, f"fixture must have 113 lines, got {len(all_lines)}"

    target_file = tmp_path / "test_deploy_docker_workflow.py"
    target_file.write_text("".join(all_lines), encoding="utf-8")

    # Evidence: what code-reviewer produced — 8 leading spaces, then backtick-wrapped content
    # Using explicit string concatenation to avoid f-string backslash SyntaxWarning (Python 3.12+)
    leading_spaces = "        "
    backtick_wrapped = leading_spaces + "`" + actual_line.lstrip() + "`"
    block = _finding_block(str(target_file), 113, backtick_wrapped)

    result = _verify_finding_quote(block, {})

    assert isinstance(result, tuple) and len(result) == 2, (
        f"AC15: must return 2-tuple, got {result!r}"
    )
    verify_status, reason = result
    assert verify_status == "suspect-no-match", (
        f"AC15 (Incident 2 Issue A repro): expected verify_status='suspect-no-match' "
        f"(leading-ws+outer-backtick: all tiers fail, finding passes through). "
        f"Got {verify_status!r}. Pre-pivot returns (False, 'FABRICATED-CANDIDATE') — "
        f"bool not str, so this assertion MUST FAIL today."
    )
    assert reason == "FABRICATED-CANDIDATE", (
        f"AC15: expected reason='FABRICATED-CANDIDATE', got {reason!r}"
    )


# ─── AC16: Incident 2 Issue B — check_citation backtick handling + tag rename ─


def test_ac16_incident2_issue_b_class_testshellsafety(tmp_path):
    """AC16: Incident 2 (forge-1778762066-9e350777) Issue B verbatim reproduction.

    code-reviewer citation (Path 2 via helper.check_citation):
      > tests/test_deploy_docker_workflow.py:211: `class TestShellSafety:`

    File line 211 = 'class TestShellSafety:' (no wrapping backticks).
    Opus satisfaction confirmed exact match: 'citation re-verifies as an exact match'.

    POST-PIVOT (helper.py change 6a — strip outer backtick pair after lstrip()):
      - quote_text = '`class TestShellSafety:`'
      - After lstrip(): still '`class TestShellSafety:`' (no leading ws)
      - Outer pair stripped: 'class TestShellSafety:'
      - _normalize_ws match: 'class TestShellSafety:' in 'class TestShellSafety:' -> True
      - check_citation returns None (verified — no tag)

    PRE-PIVOT (today's code):
      - No backtick strip in helper.py
      - _normalize_ws('`class TestShellSafety:`') not in _normalize_ws('class TestShellSafety:')
      - Returns '[UNVERIFIED: quote mismatch]'

    Also covers tag rename AC (6b): a failing case must return '[verify: suspect-no-match]'
    NOT '[UNVERIFIED: quote mismatch]'.

    Reference: SHARED/state/composite-reviewer-drop-incidents/2026-05-14_forge-1778762066-9e350777/INCIDENT.md
    """
    # Build file with 211 lines; line 211 = 'class TestShellSafety:'
    filler = ["# line {}\n".format(i) for i in range(1, 211)]
    all_lines = filler + ["class TestShellSafety:\n"]
    assert len(all_lines) == 211, f"fixture must have 211 lines, got {len(all_lines)}"

    target_file = tmp_path / "test_deploy_docker_workflow.py"
    target_file.write_text("".join(all_lines), encoding="utf-8")

    # Build a minimal ctx for check_citation
    ctx = types.SimpleNamespace(cwd=str(tmp_path))

    # Issue B primary assertion: backtick-wrapped quote must verify as None post-pivot
    quote_text = "`class TestShellSafety:`"
    result = check_citation(str(target_file), 211, quote_text, ctx)

    assert result is None, (
        f"AC16 (Incident 2 Issue B repro): check_citation with backtick-wrapped quote "
        f"'`class TestShellSafety:`' against file line 'class TestShellSafety:' must return "
        f"None (verified — outer backtick stripped, then substring match passes). "
        f"Got {result!r}. Pre-pivot returns '[UNVERIFIED: quote mismatch]'. MUST FAIL today."
    )

    # Tag rename assertion: failing case must use new enum, not old [UNVERIFIED: ...] tag
    # Use a quote that genuinely mismatches (to test the renamed tag)
    mismatch_quote = "definitely_not_in_file_1f39fb1a"
    mismatch_result = check_citation(str(target_file), 211, mismatch_quote, ctx)

    assert mismatch_result == "[verify: suspect-no-match]", (
        f"AC16 tag rename: a genuinely mismatching quote must return '[verify: suspect-no-match]' "
        f"(not '[UNVERIFIED: quote mismatch]'). "
        f"Got {mismatch_result!r}. Pre-pivot returns '[UNVERIFIED: quote mismatch]'. MUST FAIL today."
    )
