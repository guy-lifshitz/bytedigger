"""RED tests for 4B9DF7D3 — satisfaction deterministic-first override (Principle A).

Agreement: 4B9DF7D3 (PENDING, HIGH) · Class: SYSTEMATIC
Spec: SHARED/memory/Decisions/2026-06-21_4B9DF7D3_satisfaction_deterministic_first_spec.md

Canonical clean-state triggering the override (AC1):
  passed=False, score=35, threshold=70, all_findings_suspect=True,
  fix_commit_sha=None, structured.satisfied=False/fixes_required=[].

Signal-correction (2026-06-21, §2): the override now takes a pre-computed boolean
`all_findings_suspect` (not `review_verdict`). The caller (_write_satisfaction_doc)
derives this boolean as:
  (review_verdict=="SUSPECT") and ("## Suspect Findings" in review_doc_text)
This tightens the predicate to the aggregation path only, excluding degraded-fallback SUSPECT.

All 13 tests MUST FAIL against current production code:
  AC1–AC7: TypeError — _deterministic_satisfaction_override exists but takes `review_verdict`
            (old kwarg), not `all_findings_suspect` → TypeError on call.
  AC8:    _write_satisfaction_doc returns error (old wiring: review_verdict=SUSPECT alone
          fires → currently may return ok, but review_doc lacks "## Suspect Findings" so
          after GREEN the test would fail if the section is absent; current code fires on
          SUSPECT verdict alone → returns ok → but we assert ok AND verify the section
          was required; however the fixture now WRITES the section, so AC8 tests the full path.
          Against OLD code the doc path check differs. See body for precise failing condition.
  AC9:    regression guard — review_verdict='FAIL' must block. Likely passes today (no override).
          We add the no-emit assertion to give it RED teeth post-GREEN.
  AC10:   "review_verdict"/"fix_commit_sha" keys absent from _build_satisfaction_prompt data
  AC11:   "review_verdict"/"fix_commit_sha" keys absent from invoke_llm_subprocess extra_data
  AC12:   disclaimer substring "NOT grounds to FAIL" absent from prompt
  AC13:   NEW — review_verdict="SUSPECT" but NO "## Suspect Findings" section in review_doc
          must still block. Current code fires on review_verdict==SUSPECT alone → returns ok
          → asserting error_code fails → TRUE RED.

Import discipline (D1CF5FDF §1q): ALL from-phase_6_review imports deferred to
function bodies with # noqa: PLC0415 — file COLLECTS even before GREEN.
Conftest puts engine root + workflows/ on sys.path at import time.
"""
from __future__ import annotations

from pathlib import Path

import pytest

HERE = Path(__file__).parent

# ─────────────────────────────────────────────────────────────────────────────
# Shared builders (pure Python, no prod imports at module level)
# ─────────────────────────────────────────────────────────────────────────────

def _make_ctx(tmp_path: Path, satisfaction_threshold: int = 70, complexity: str = "SIMPLE"):
    """Build a minimal WorkflowContext — mirrors the 9702B73F sibling pattern."""
    from contracts import WorkflowContext  # noqa: PLC0415
    scratch = tmp_path / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    cfg: dict = {
        "scratchpad_dir": str(scratch),
        "satisfaction_threshold": satisfaction_threshold,
        "complexity": complexity,
    }
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config=cfg,
        question="deterministic override test",
        session_id="test-4B9DF7D3",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_sat_prev(
    tmp_path: Path,
    *,
    raw_response: str,
    review_verdict: str = "SUSPECT",
    fix_commit_sha=None,
    review_doc_content: "str | None" = None,
) -> "object":
    """Construct a prev StepResult for _write_satisfaction_doc.

    `review_doc_content` controls what is written to the review_doc file:
      - None  → default stub WITHOUT the '## Suspect Findings' section
      - pass a string containing '## Suspect Findings' to simulate the aggregation path
      - pass a string without that section to simulate the degraded-fallback path (AC13)
    """
    from contracts import StepResult  # noqa: PLC0415
    scratch = tmp_path / "scratch"
    reviews_dir = scratch / "reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)
    specs_dir = scratch / "specs"
    specs_dir.mkdir(parents=True, exist_ok=True)

    sat_doc = reviews_dir / "build-satisfaction.md"
    review_doc = reviews_dir / "build-review.md"

    # Write the review_doc with controllable content.
    # DEFAULT: no "## Suspect Findings" section — safe baseline.
    if review_doc_content is None:
        review_doc.write_text(
            "# Composite Review\n\n## Aggregated Findings\n\nVERDICT: PASS\n",
            encoding="utf-8",
        )
    else:
        review_doc.write_text(review_doc_content, encoding="utf-8")

    fix_doc = reviews_dir / "build-fix.md"
    fix_doc.write_text("FIX SKIPPED\n", encoding="utf-8")
    spec_doc = specs_dir / "build-spec.md"
    spec_doc.write_text("# Spec\n", encoding="utf-8")

    data: dict = {
        "raw_response": raw_response,
        "doc_path": str(sat_doc),
        "spec_path": str(spec_doc),
        "review_doc_path": str(review_doc),
        "fix_doc_path": str(fix_doc),
        "review_verdict": review_verdict,
        "fix_commit_sha": fix_commit_sha,
    }
    return StepResult(
        status="ok",
        data=data,
        duration_ms=0,
        step_name="invoke_satisfaction_llm",
    )


def _make_prompt_prev(
    tmp_path: Path,
    *,
    review_verdict: str = "SUSPECT",
    fix_commit_sha=None,
) -> "object":
    """Construct a prev StepResult for _build_satisfaction_prompt and _invoke_satisfaction_llm."""
    from contracts import StepResult  # noqa: PLC0415
    scratch = tmp_path / "scratch"
    reviews_dir = scratch / "reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)
    specs_dir = scratch / "specs"
    specs_dir.mkdir(parents=True, exist_ok=True)

    sat_doc = reviews_dir / "build-satisfaction.md"
    review_doc = reviews_dir / "build-review.md"
    review_doc.write_text("# Review\nVERDICT: SUSPECT\n", encoding="utf-8")
    fix_doc = reviews_dir / "build-fix.md"
    fix_doc.write_text("FIX SKIPPED\n", encoding="utf-8")
    spec_doc = specs_dir / "build-spec.md"
    spec_doc.write_text("# Spec\n", encoding="utf-8")

    data: dict = {
        "prompt": "dummy prompt",
        "doc_path": str(sat_doc),
        "spec_path": str(spec_doc),
        "review_doc_path": str(review_doc),
        "fix_doc_path": str(fix_doc),
        "prompt_bytes": 12,
        "review_verdict": review_verdict,
        "fix_commit_sha": fix_commit_sha,
    }
    return StepResult(
        status="ok",
        data=data,
        duration_ms=0,
        step_name="build_satisfaction_prompt",
    )


def _canonical_clean_raw(score: int = 35) -> str:
    """LLM raw_response encoding the canonical clean state: score below threshold,
    structured satisfied=false, fixes_required=[]. review_verdict=SUSPECT, fix_commit_sha=None."""
    return (
        f"SCORE: {score}\n"
        "VERDICT: FAIL\n"
        "\n"
        "## satisfaction-output (structured)\n"
        "```json\n"
        '{"satisfied": false, "fixes_required": []}\n'
        "```\n"
    )


def _canonical_clean_raw_fail_verdict(score: int = 35) -> str:
    """Same as above but review_verdict will be FAIL (AC9)."""
    return _canonical_clean_raw(score)


def _suppress_emit(monkeypatch) -> list:
    """Suppress _emit_safe calls and capture (event_type, payload) tuples."""
    import phase_6_review as _p6r  # noqa: PLC0415
    captured: list = []
    monkeypatch.setattr(
        _p6r,
        "_emit_safe",
        lambda et, p, **kw: captured.append((et, p)),
    )
    return captured


def _make_clean_structured():
    """Build a SatisfactionVerdict for the canonical clean state (satisfied=False, fixes_required=[])."""
    from phase_6_review import SatisfactionVerdict  # noqa: PLC0415
    return SatisfactionVerdict(satisfied=False, fixes_required=[])


# ─────────────────────────────────────────────────────────────────────────────
# AC1 — override fires on canonical clean state (all_findings_suspect=True)
# ─────────────────────────────────────────────────────────────────────────────

def test_ac1_override_fires_on_canonical_clean_state() -> None:
    """AC1: _deterministic_satisfaction_override fires → (True, 'corroborated_ungroundable_skip').

    New signature takes all_findings_suspect=True (boolean, not review_verdict string).
    FAILS today: current helper takes `review_verdict` not `all_findings_suspect` → TypeError.
    """
    import phase_6_review as _p6r  # noqa: PLC0415
    _override = getattr(_p6r, "_deterministic_satisfaction_override", None)
    assert _override is not None, (
        "AC1 FAIL: _deterministic_satisfaction_override not found in phase_6_review — "
        "GREEN has not added the helper yet (4B9DF7D3)"
    )
    structured = _make_clean_structured()
    result = _override(
        False,
        "score_only_below_threshold",
        score=35,
        threshold=70,
        all_findings_suspect=True,
        fix_commit_sha=None,
        structured=structured,
    )
    assert result == (True, "corroborated_ungroundable_skip"), (
        f"AC1 FAIL: canonical clean state must return (True, 'corroborated_ungroundable_skip'); "
        f"got {result!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# AC2 — override does NOT fire when all_findings_suspect=False
# ─────────────────────────────────────────────────────────────────────────────

def test_ac2_override_not_fired_when_all_findings_suspect_false() -> None:
    """AC2: all_findings_suspect=False → override does not fire; returns unchanged (False, orig).

    Covers: verified findings present OR degraded-fallback review.
    FAILS today: current helper takes `review_verdict` not `all_findings_suspect` → TypeError.
    """
    import phase_6_review as _p6r  # noqa: PLC0415
    _override = getattr(_p6r, "_deterministic_satisfaction_override", None)
    assert _override is not None, (
        "AC2 FAIL: _deterministic_satisfaction_override not found in phase_6_review"
    )
    structured = _make_clean_structured()
    orig_code = "score_only_below_threshold"
    result = _override(
        False,
        orig_code,
        score=35,
        threshold=70,
        all_findings_suspect=False,
        fix_commit_sha=None,
        structured=structured,
    )
    assert result == (False, orig_code), (
        f"AC2 FAIL: all_findings_suspect=False must NOT fire override; "
        f"expected (False, {orig_code!r}), got {result!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# AC3 — override does NOT fire when fix_commit_sha is non-None
# ─────────────────────────────────────────────────────────────────────────────

def test_ac3_override_not_fired_when_fix_commit_sha_present() -> None:
    """AC3: fix_commit_sha='abc123def' → override does not fire.

    FAILS today: current helper takes `review_verdict` not `all_findings_suspect` → TypeError.
    """
    import phase_6_review as _p6r  # noqa: PLC0415
    _override = getattr(_p6r, "_deterministic_satisfaction_override", None)
    assert _override is not None, (
        "AC3 FAIL: _deterministic_satisfaction_override not found"
    )
    structured = _make_clean_structured()
    orig_code = "score_only_below_threshold"
    result = _override(
        False,
        orig_code,
        score=35,
        threshold=70,
        all_findings_suspect=True,
        fix_commit_sha="abc123def",
        structured=structured,
    )
    assert result == (False, orig_code), (
        f"AC3 FAIL: non-None fix_commit_sha must NOT fire override; got {result!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# AC4 — override does NOT fire when structured.fixes_required is non-empty
# ─────────────────────────────────────────────────────────────────────────────

def test_ac4_override_not_fired_when_fixes_required_nonempty() -> None:
    """AC4: structured.fixes_required non-empty → override does not fire.

    FAILS today: current helper takes `review_verdict` not `all_findings_suspect` → TypeError.
    """
    import phase_6_review as _p6r  # noqa: PLC0415
    from phase_6_review import SatisfactionVerdict  # noqa: PLC0415
    _override = getattr(_p6r, "_deterministic_satisfaction_override", None)
    assert _override is not None, (
        "AC4 FAIL: _deterministic_satisfaction_override not found"
    )
    structured_with_fixes = SatisfactionVerdict(
        satisfied=False,
        fixes_required=[{"file": "x.py", "issue": "y"}],
    )
    orig_code = "score_only_below_threshold"
    result = _override(
        False,
        orig_code,
        score=35,
        threshold=70,
        all_findings_suspect=True,
        fix_commit_sha=None,
        structured=structured_with_fixes,
    )
    assert result == (False, orig_code), (
        f"AC4 FAIL: non-empty fixes_required must NOT fire override; got {result!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# AC5 — override does NOT fire when passed=True
# ─────────────────────────────────────────────────────────────────────────────

def test_ac5_override_not_fired_when_already_passed() -> None:
    """AC5: passed=True → override must not fire; reason_code unchanged, NOT 'corroborated_ungroundable_skip'.

    FAILS today: current helper takes `review_verdict` not `all_findings_suspect` → TypeError.
    """
    import phase_6_review as _p6r  # noqa: PLC0415
    _override = getattr(_p6r, "_deterministic_satisfaction_override", None)
    assert _override is not None, (
        "AC5 FAIL: _deterministic_satisfaction_override not found"
    )
    structured = _make_clean_structured()
    orig_code = "concurring_pass"
    result = _override(
        True,
        orig_code,
        score=80,
        threshold=70,
        all_findings_suspect=True,
        fix_commit_sha=None,
        structured=structured,
    )
    assert result[0] is True, (
        f"AC5 FAIL: passed=True must remain True after override; got {result!r}"
    )
    assert result[1] != "corroborated_ungroundable_skip", (
        f"AC5 FAIL: passed=True must NOT produce 'corroborated_ungroundable_skip'; got {result!r}"
    )
    assert result[1] == orig_code, (
        f"AC5 FAIL: reason_code must be unchanged when passed=True; expected {orig_code!r}, got {result[1]!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# AC6 — override does NOT fire when score is None or score >= threshold
# ─────────────────────────────────────────────────────────────────────────────

def test_ac6_override_not_fired_on_none_score_or_above_threshold() -> None:
    """AC6: score=None → unchanged; score=90 (>=threshold=70) → unchanged.

    FAILS today: current helper takes `review_verdict` not `all_findings_suspect` → TypeError.
    """
    import phase_6_review as _p6r  # noqa: PLC0415
    _override = getattr(_p6r, "_deterministic_satisfaction_override", None)
    assert _override is not None, (
        "AC6 FAIL: _deterministic_satisfaction_override not found"
    )
    structured = _make_clean_structured()
    orig_code = "no_signals"

    # score=None
    result_none = _override(
        False,
        orig_code,
        score=None,
        threshold=70,
        all_findings_suspect=True,
        fix_commit_sha=None,
        structured=structured,
    )
    assert result_none == (False, orig_code), (
        f"AC6 FAIL: score=None must NOT fire override; got {result_none!r}"
    )

    # score=90 >= threshold=70
    result_above = _override(
        False,
        "score_only_below_threshold",
        score=90,
        threshold=70,
        all_findings_suspect=True,
        fix_commit_sha=None,
        structured=structured,
    )
    assert result_above[1] != "corroborated_ungroundable_skip", (
        f"AC6 FAIL: score >= threshold must NOT fire override; got {result_above!r}"
    )
    assert result_above[0] is False, (
        f"AC6 FAIL: override must not flip passed when score>=threshold; got {result_above!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# AC7 — on fire, satisfaction_relaxed_corroborated emitted exactly once; absent on no-fire
# ─────────────────────────────────────────────────────────────────────────────

def test_ac7_emit_on_fire_absent_on_no_fire(monkeypatch) -> None:
    """AC7: override-fire (all_findings_suspect=True) emits satisfaction_relaxed_corroborated
    with phase=6 exactly once. Override-no-fire (all_findings_suspect=False) emits nothing.

    FAILS today: current helper takes `review_verdict` not `all_findings_suspect` → TypeError.
    """
    import phase_6_review as _p6r  # noqa: PLC0415
    _override = getattr(_p6r, "_deterministic_satisfaction_override", None)
    assert _override is not None, (
        "AC7 FAIL: _deterministic_satisfaction_override not found"
    )
    captured = _suppress_emit(monkeypatch)
    structured = _make_clean_structured()

    # Fire state — all_findings_suspect=True
    _override(
        False,
        "score_only_below_threshold",
        score=35,
        threshold=70,
        all_findings_suspect=True,
        fix_commit_sha=None,
        structured=structured,
    )

    fire_events = [et for et, _ in captured if et == "satisfaction_relaxed_corroborated"]
    assert len(fire_events) == 1, (
        f"AC7 FAIL: expected exactly 1 'satisfaction_relaxed_corroborated' emit on fire; "
        f"got {len(fire_events)} — emits={captured!r}"
    )
    # Verify phase=6 in the payload
    phase_payloads = [p for et, p in captured if et == "satisfaction_relaxed_corroborated"]
    assert phase_payloads[0].get("phase") == 6, (
        f"AC7 FAIL: emit payload must carry phase=6; got {phase_payloads[0]!r}"
    )

    # No-fire state (all_findings_suspect=False)
    captured.clear()
    _override(
        False,
        "score_only_below_threshold",
        score=35,
        threshold=70,
        all_findings_suspect=False,
        fix_commit_sha=None,
        structured=structured,
    )
    no_fire_events = [et for et, _ in captured if et == "satisfaction_relaxed_corroborated"]
    assert len(no_fire_events) == 0, (
        f"AC7 FAIL: 'satisfaction_relaxed_corroborated' must NOT be emitted on no-fire; "
        f"got {no_fire_events!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# AC8 — _write_satisfaction_doc returns ok when review_doc CONTAINS ## Suspect Findings
# ─────────────────────────────────────────────────────────────────────────────

def test_ac8_write_satisfaction_doc_ok_on_canonical_clean_state(
    tmp_path: Path, monkeypatch
) -> None:
    """AC8: _write_satisfaction_doc returns status='ok' when:
      - review_verdict='SUSPECT'
      - review_doc file CONTAINS '## Suspect Findings' (aggregation-path positive marker)
      - below-threshold raw_response (SCORE 35, structured satisfied:false, fixes_required:[])

    Current code fires override on review_verdict==SUSPECT alone (old loose predicate), but the
    new code must fire ONLY when the section is present. This test writes the section → expects ok.
    Against OLD code: may return ok (verdict alone fires) but with the new wiring it asserts the
    stricter path. The test is RED because the new helper signature + wiring don't exist yet.
    """
    from phase_6_review import _write_satisfaction_doc  # noqa: PLC0415
    _suppress_emit(monkeypatch)
    ctx = _make_ctx(tmp_path, satisfaction_threshold=70, complexity="SIMPLE")
    raw = _canonical_clean_raw(score=35)
    # review_doc CONTAINS the aggregation-path marker
    review_doc_with_section = (
        "# Composite Review\n\n"
        "## Suspect Findings\n\n"
        "- L42: some suspect finding (verify_status=suspect-grep)\n\n"
        "## Aggregated Findings\n\n"
        "VERDICT: SUSPECT\n"
    )
    prev = _make_sat_prev(
        tmp_path,
        raw_response=raw,
        review_verdict="SUSPECT",
        fix_commit_sha=None,
        review_doc_content=review_doc_with_section,
    )

    result = _write_satisfaction_doc(ctx, prev)

    assert result.status == "ok", (
        f"AC8 FAIL: _write_satisfaction_doc with canonical clean state (review_verdict=SUSPECT + "
        f"'## Suspect Findings' section in review_doc) must return ok (override must fire); "
        f"got status={result.status!r}, "
        f"error_code={getattr(result, 'error_code', None)!r}, "
        f"error={getattr(result, 'error', None)!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# AC9 — _write_satisfaction_doc returns E_SATISFACTION_BELOW_THRESHOLD when review_verdict='FAIL'
# ─────────────────────────────────────────────────────────────────────────────

def test_ac9_write_satisfaction_doc_still_fails_when_verdict_fail(
    tmp_path: Path, monkeypatch
) -> None:
    """AC9: review_verdict='FAIL' → override must not fire → E_SATISFACTION_BELOW_THRESHOLD.
    Also asserts no 'satisfaction_relaxed_corroborated' emit.

    This test may partially pass today (already returns error), but the emit-absence assertion
    gives it RED teeth: today the override doesn't exist so the emit definitely won't happen;
    after GREEN, the no-fire branch must also not emit.
    """
    from phase_6_review import _write_satisfaction_doc  # noqa: PLC0415
    captured = _suppress_emit(monkeypatch)
    ctx = _make_ctx(tmp_path, satisfaction_threshold=70, complexity="SIMPLE")
    raw = _canonical_clean_raw_fail_verdict(score=35)
    prev = _make_sat_prev(tmp_path, raw_response=raw, review_verdict="FAIL", fix_commit_sha=None)

    result = _write_satisfaction_doc(ctx, prev)

    assert result.status == "error", (
        f"AC9 FAIL: review_verdict='FAIL' must still produce error; got {result.status!r}"
    )
    assert getattr(result, "error_code", None) == "E_SATISFACTION_BELOW_THRESHOLD", (
        f"AC9 FAIL: expected E_SATISFACTION_BELOW_THRESHOLD; got {result.error_code!r}"
    )
    # Override must NOT have fired — no corroborated_ungroundable_skip emit
    override_emits = [et for et, _ in captured if et == "satisfaction_relaxed_corroborated"]
    assert len(override_emits) == 0, (
        f"AC9 FAIL: 'satisfaction_relaxed_corroborated' must NOT be emitted when "
        f"review_verdict='FAIL' (override must not fire); got {override_emits!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# AC10 — _build_satisfaction_prompt returns review_verdict + fix_commit_sha in data
# ─────────────────────────────────────────────────────────────────────────────

def test_ac10_build_satisfaction_prompt_forwards_verdict_and_sha(
    tmp_path: Path, monkeypatch
) -> None:
    """AC10: _build_satisfaction_prompt forwards review_verdict + fix_commit_sha from prev.data.

    FAILS today: return data dict (L2498-2505) does not include these keys.
    """
    from phase_6_review import _build_satisfaction_prompt  # noqa: PLC0415
    _suppress_emit(monkeypatch)
    ctx = _make_ctx(tmp_path, satisfaction_threshold=70, complexity="SIMPLE")
    prev = _make_prompt_prev(tmp_path, review_verdict="SUSPECT", fix_commit_sha=None)

    result = _build_satisfaction_prompt(ctx, prev)

    assert result.status == "ok", (
        f"AC10 setup FAIL: _build_satisfaction_prompt returned error: {getattr(result, 'error', None)!r}"
    )
    data = result.data or {}
    assert "review_verdict" in data, (
        f"AC10 FAIL: 'review_verdict' key missing from _build_satisfaction_prompt return data; "
        f"got keys={list(data.keys())} — GREEN must forward this from prev.data"
    )
    assert "fix_commit_sha" in data, (
        f"AC10 FAIL: 'fix_commit_sha' key missing from _build_satisfaction_prompt return data; "
        f"got keys={list(data.keys())} — GREEN must forward this from prev.data"
    )
    assert data["review_verdict"] == "SUSPECT", (
        f"AC10 FAIL: review_verdict value mismatch; expected 'SUSPECT', got {data['review_verdict']!r}"
    )
    assert data["fix_commit_sha"] is None, (
        f"AC10 FAIL: fix_commit_sha value mismatch; expected None, got {data['fix_commit_sha']!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# AC11 — _invoke_satisfaction_llm SIMPLE-path extra_data carries both new keys
# ─────────────────────────────────────────────────────────────────────────────

def test_ac11_invoke_satisfaction_llm_simple_extra_data_has_verdict_and_sha(
    tmp_path: Path, monkeypatch
) -> None:
    """AC11: _invoke_satisfaction_llm SIMPLE-path passes review_verdict + fix_commit_sha
    in extra_data to invoke_llm_subprocess.

    FAILS today: extra_data dict (L2676-2681) does not include these keys.
    """
    import phase_6_review as _p6r  # noqa: PLC0415
    from phase_6_review import _invoke_satisfaction_llm  # noqa: PLC0415
    _suppress_emit(monkeypatch)

    captured_extra: list[dict] = []

    def _fake_invoke_llm_subprocess(
        prompt, model, timeout_sec, step_name, extra_data=None,
        hard_gate=False, gate_label=None, allowed_tools=None, **kw
    ):
        from contracts import StepResult  # noqa: PLC0415
        captured_extra.append(dict(extra_data or {}))
        return StepResult(
            status="ok",
            data={"raw_response": "SCORE: 90\nVERDICT: PASS\n"},
            duration_ms=0,
            step_name="invoke_satisfaction_llm",
        )

    monkeypatch.setattr(_p6r, "invoke_llm_subprocess", _fake_invoke_llm_subprocess)

    ctx = _make_ctx(tmp_path, satisfaction_threshold=70, complexity="SIMPLE")
    # prev shaped like what _build_satisfaction_prompt now returns (including new keys)
    prev = _make_prompt_prev(tmp_path, review_verdict="SUSPECT", fix_commit_sha=None)

    _invoke_satisfaction_llm(ctx, prev)

    assert len(captured_extra) == 1, (
        f"AC11 FAIL: expected invoke_llm_subprocess called once; got {len(captured_extra)}"
    )
    ed = captured_extra[0]
    assert "review_verdict" in ed, (
        f"AC11 FAIL: 'review_verdict' missing from SIMPLE-path extra_data; "
        f"got keys={list(ed.keys())} — GREEN must add it (L2676-2681)"
    )
    assert "fix_commit_sha" in ed, (
        f"AC11 FAIL: 'fix_commit_sha' missing from SIMPLE-path extra_data; "
        f"got keys={list(ed.keys())} — GREEN must add it (L2676-2681)"
    )
    assert ed["review_verdict"] == "SUSPECT", (
        f"AC11 FAIL: review_verdict value mismatch; expected 'SUSPECT', got {ed['review_verdict']!r}"
    )
    assert ed["fix_commit_sha"] is None, (
        f"AC11 FAIL: fix_commit_sha value mismatch; expected None, got {ed['fix_commit_sha']!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# AC12 — _build_satisfaction_prompt prompt contains SUSPECT-FINDINGS NOTICE disclaimer
# ─────────────────────────────────────────────────────────────────────────────

def test_ac12_build_satisfaction_prompt_contains_suspect_notice(
    tmp_path: Path, monkeypatch
) -> None:
    """AC12: _build_satisfaction_prompt prompt string contains 'NOT grounds to FAIL'.

    FAILS today: disclaimer not yet appended in _build_satisfaction_prompt (spec §2.A).
    """
    from phase_6_review import _build_satisfaction_prompt  # noqa: PLC0415
    _suppress_emit(monkeypatch)
    ctx = _make_ctx(tmp_path, satisfaction_threshold=70, complexity="SIMPLE")
    prev = _make_prompt_prev(tmp_path, review_verdict="SUSPECT", fix_commit_sha=None)

    result = _build_satisfaction_prompt(ctx, prev)

    assert result.status == "ok", (
        f"AC12 setup FAIL: _build_satisfaction_prompt returned error: {getattr(result, 'error', None)!r}"
    )
    prompt = (result.data or {}).get("prompt", "")
    assert "NOT grounds to FAIL" in prompt, (
        f"AC12 FAIL: SUSPECT-FINDINGS NOTICE disclaimer ('NOT grounds to FAIL') "
        f"not found in prompt string — GREEN must append §2.A disclaimer block; "
        f"prompt excerpt (last 500 chars): {prompt[-500:]!r}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# AC13 — fallback-exclusion: SUSPECT verdict WITHOUT ## Suspect Findings section must still block
# ─────────────────────────────────────────────────────────────────────────────

def test_ac13_fallback_suspect_without_section_still_blocks(
    tmp_path: Path, monkeypatch
) -> None:
    """AC13: _write_satisfaction_doc STILL returns E_SATISFACTION_BELOW_THRESHOLD when:
      - review_verdict='SUSPECT' (would fire the OLD loose predicate)
      - review_doc does NOT contain '## Suspect Findings' (degraded-fallback SUSPECT, not aggregation)
      - below-threshold raw_response

    This is the FALSE-PASS guard: old code fires on review_verdict==SUSPECT alone → returns ok.
    New code requires the section marker → does NOT fire → returns E_SATISFACTION_BELOW_THRESHOLD.

    TRUE RED: old code returns ok → asserting error_code==E_SATISFACTION_BELOW_THRESHOLD FAILS today.
    Also asserts NO 'satisfaction_relaxed_corroborated' emit (the override must be silent).
    """
    from phase_6_review import _write_satisfaction_doc  # noqa: PLC0415
    captured = _suppress_emit(monkeypatch)
    ctx = _make_ctx(tmp_path, satisfaction_threshold=70, complexity="SIMPLE")
    raw = _canonical_clean_raw(score=35)

    # review_doc WITHOUT '## Suspect Findings' — simulates degraded-fallback SUSPECT path
    review_doc_without_section = (
        "# Composite Review\n\n"
        "## Aggregated Findings\n\n"
        "VERDICT: PASS\n"
    )
    prev = _make_sat_prev(
        tmp_path,
        raw_response=raw,
        review_verdict="SUSPECT",
        fix_commit_sha=None,
        review_doc_content=review_doc_without_section,
    )

    result = _write_satisfaction_doc(ctx, prev)

    assert result.status == "error", (
        f"AC13 FAIL: review_verdict='SUSPECT' WITHOUT '## Suspect Findings' section must "
        f"NOT fire override → must return error; got status={result.status!r}. "
        f"OLD code fires on verdict alone → false-PASS. NEW code requires section marker."
    )
    assert getattr(result, "error_code", None) == "E_SATISFACTION_BELOW_THRESHOLD", (
        f"AC13 FAIL: expected E_SATISFACTION_BELOW_THRESHOLD (override must not fire when "
        f"section absent); got error_code={getattr(result, 'error_code', None)!r}"
    )
    # Override must NOT have emitted — degraded-fallback SUSPECT is NOT corroborated
    override_emits = [et for et, _ in captured if et == "satisfaction_relaxed_corroborated"]
    assert len(override_emits) == 0, (
        f"AC13 FAIL: 'satisfaction_relaxed_corroborated' must NOT be emitted when "
        f"review_doc lacks '## Suspect Findings' section (fallback-SUSPECT); "
        f"got {override_emits!r}"
    )
