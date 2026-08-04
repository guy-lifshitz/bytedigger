"""RED tests for F60BDC71 — phase_6 satisfaction gate consumes structured SatisfactionVerdict.

Agreement: F60BDC71 (95D3E5F6 G2 band-aid #13)

Contract (GREEN will implement):
1. _parse_satisfaction_structured(raw) -> tuple[SatisfactionVerdict | None, str | None] is NEW.
2. _write_satisfaction_doc gate flip: structured.satisfied governs when block present,
   falls back to markdown SCORE path when absent.
3. _write_satisfaction_doc threads structured_verdict key into StepResult.data.
4. _build_satisfaction_prompt appends the satisfaction-output (structured) addendum.

All tests MUST FAIL until GREEN implements the contract.
Do NOT implement any production change here — RED-only file (F60BDC71).
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).parent
ENGINE_ROOT = HERE.parent
sys.path.insert(0, str(ENGINE_ROOT))

from bytedigger_engine.workflows import phase_6_review  # noqa: E402  (needed for monkeypatch target)
from bytedigger_engine.contracts import StepResult, WorkflowContext  # noqa: E402
from bytedigger_engine.workflows.phase_6_review import (  # noqa: E402
    _build_satisfaction_prompt,
    _write_satisfaction_doc,
    SatisfactionVerdict,          # NEW symbol — not yet exported; RED failure surface
    _parse_satisfaction_structured,  # NEW function — not yet implemented; RED failure surface
)


# ─── structured-block markdown builders ──────────────────────────────────────
# These are the exact formats _parse_satisfaction_structured expects.

_VALID_BLOCK_PASS = (
    "## satisfaction-output (structured)\n"
    "```json\n"
    '{"satisfied": true, "fixes_required": []}\n'
    "```"
)

_VALID_BLOCK_FAIL = (
    "## satisfaction-output (structured)\n"
    "```json\n"
    '{"satisfied": false, "fixes_required": [{"file": "a.py", "issue": "x"}]}\n'
    "```"
)

_BLOCK_SCHEMA_VIOLATION_INT_NOT_BOOL = (
    "## satisfaction-output (structured)\n"
    "```json\n"
    '{"satisfied": 1, "fixes_required": []}\n'
    "```"
)

# Header present but JSON is malformed (truncated) → "malformed"
_BLOCK_MALFORMED_JSON = (
    "## satisfaction-output (structured)\n"
    "```json\n"
    '{"satisfied": true, "fixes_required": [\n'
    "```"
)


def _raw_with_score(score_line: str, block: str | None) -> str:
    """Build a raw LLM response combining a SCORE line and optional structured block."""
    parts = [
        "## Composite",
        score_line,
        "VERDICT: PASS",
        "",
    ]
    if block is not None:
        parts.append(block)
    return "\n".join(parts)


def _raw_fail_with_score(score_line: str, block: str | None) -> str:
    """Build a raw LLM response for a FAIL scenario."""
    parts = [
        "## Composite",
        score_line,
        "VERDICT: FAIL",
        "",
    ]
    if block is not None:
        parts.append(block)
    return "\n".join(parts)


# ─── fixture builders ─────────────────────────────────────────────────────────


def _make_ctx(tmp_path: Path, satisfaction_threshold: int = 80) -> WorkflowContext:
    scratch = tmp_path / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={
            "scratchpad_dir": str(scratch),
            "satisfaction_threshold": satisfaction_threshold,
        },
        question="add feature X",
        session_id="test-F60BDC71",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_satisfaction_prev(
    tmp_path: Path,
    raw_response: str,
) -> StepResult:
    """Construct a prev StepResult that _write_satisfaction_doc expects.

    Mirrors the idiom from test_phase_6_last_findings_persist_c834481a.py.
    A minimal build-review.md is written on disk because the failure path reads it.
    """
    scratch = tmp_path / "scratch"
    reviews_dir = scratch / "reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)

    sat_doc = reviews_dir / "build-satisfaction.md"
    review_doc = reviews_dir / "build-review.md"
    review_doc.write_text("# Review\nVERDICT: FAIL\n", encoding="utf-8")

    return StepResult(
        status="ok",
        data={
            "raw_response": raw_response,
            "doc_path": str(sat_doc),
            "spec_path": str(scratch / "specs" / "build-spec.md"),
            "review_doc_path": str(review_doc),
            "fix_doc_path": str(scratch / "reviews" / "build-fix.md"),
        },
        duration_ms=100,
        step_name="invoke_satisfaction_llm",
    )


def _suppress_emit(monkeypatch) -> list[tuple]:
    """Suppress _emit_safe calls and capture them for optional assertion."""
    captured: list[tuple] = []
    monkeypatch.setattr(
        phase_6_review,
        "_emit_safe",
        lambda et, p, **kw: captured.append((et, p, kw)),
    )
    return captured


# ═══════════════════════════════════════════════════════════════════════════════
# AC1 — _parse_satisfaction_structured: valid block → (SatisfactionVerdict, None)
# ═══════════════════════════════════════════════════════════════════════════════


def test_parse_satisfaction_structured_valid_block_returns_verdict() -> None:
    """AC1: valid ## satisfaction-output (structured) JSON block returns
    (SatisfactionVerdict(satisfied=True, fixes_required=[]), None).

    FAILS on current main: _parse_satisfaction_structured does not exist.
    """
    raw = _raw_with_score("SCORE: 90", _VALID_BLOCK_PASS)
    verdict, reason = _parse_satisfaction_structured(raw)

    assert verdict is not None, (
        "AC1 FAIL: expected a SatisfactionVerdict instance, got None. "
        "_parse_satisfaction_structured not yet implemented — F60BDC71"
    )
    assert isinstance(verdict, SatisfactionVerdict), (
        f"AC1 FAIL: expected SatisfactionVerdict, got {type(verdict)!r}"
    )
    assert verdict.satisfied is True, (
        f"AC1 FAIL: expected satisfied=True; got {verdict.satisfied!r}"
    )
    assert verdict.fixes_required == [], (
        f"AC1 FAIL: expected fixes_required=[]; got {verdict.fixes_required!r}"
    )
    assert reason is None, (
        f"AC1 FAIL: expected reason=None on success; got {reason!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC2 — _parse_satisfaction_structured: no header → (None, "absent")
# ═══════════════════════════════════════════════════════════════════════════════


def test_parse_satisfaction_structured_absent_when_no_header() -> None:
    """AC2: raw with no ## satisfaction-output (structured) header returns (None, "absent").

    FAILS on current main: _parse_satisfaction_structured does not exist.
    """
    raw = _raw_with_score("SCORE: 90", None)  # no structured block
    verdict, reason = _parse_satisfaction_structured(raw)

    assert verdict is None, (
        f"AC2 FAIL: expected verdict=None when header absent; got {verdict!r}"
    )
    assert reason == "absent", (
        f"AC2 FAIL: expected reason='absent'; got {reason!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC3 — _parse_satisfaction_structured: schema violation → (None, "schema_violation: …")
# ═══════════════════════════════════════════════════════════════════════════════


def test_parse_satisfaction_structured_schema_violation() -> None:
    """AC3: JSON present but enforce() fails (e.g. satisfied=int not bool) →
    (None, str starting with "schema_violation:").

    FAILS on current main: _parse_satisfaction_structured does not exist.
    """
    raw = _raw_with_score("SCORE: 90", _BLOCK_SCHEMA_VIOLATION_INT_NOT_BOOL)
    verdict, reason = _parse_satisfaction_structured(raw)

    assert verdict is None, (
        f"AC3 FAIL: expected verdict=None on schema violation; got {verdict!r}"
    )
    assert reason is not None and reason.startswith("schema_violation:"), (
        f"AC3 FAIL: expected reason starting with 'schema_violation:'; got {reason!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC4 — _write_satisfaction_doc: structured PASS overrides low markdown SCORE
# ═══════════════════════════════════════════════════════════════════════════════


def test_write_satisfaction_doc_drift_structured_pass_score_below_fails_closed_9702b73f_supersedes_f60bdc71(
    tmp_path: Path, monkeypatch
) -> None:
    """9702B73F supersedes F60BDC71 AC4: structured.satisfied=True AND SCORE: 40 < threshold 80.

    F60BDC71 AC4 asserted status='ok' (structured overrides score).
    9702B73F reverses this: strict-AND policy means drift → fail-closed.
    Now: status='error', error contains 'drift' AND 'fail-closed',
    error_code='E_SATISFACTION_BELOW_THRESHOLD'.
    """
    captured: list[tuple] = []
    monkeypatch.setattr(
        phase_6_review,
        "_emit_safe",
        lambda et, p, **kw: captured.append((et, p, kw)),
    )
    ctx = _make_ctx(tmp_path, satisfaction_threshold=80)
    raw = _raw_with_score("SCORE: 40", _VALID_BLOCK_PASS)
    prev = _make_satisfaction_prev(tmp_path, raw)

    result = _write_satisfaction_doc(ctx, prev)

    assert result.status == "error", (
        f"9702B73F: expected status='error' (drift fail-closed) when structured.satisfied=True "
        f"but SCORE 40 < threshold 80; got status={result.status!r} — supersedes F60BDC71"
    )
    assert result.error_code == "E_SATISFACTION_BELOW_THRESHOLD", (
        f"9702B73F: expected error_code='E_SATISFACTION_BELOW_THRESHOLD'; got {result.error_code!r}"
    )
    assert "drift" in result.error.lower(), (
        f"9702B73F: expected 'drift' in error; got {result.error!r}"
    )
    assert "fail-closed" in result.error.lower(), (
        f"9702B73F: expected 'fail-closed' in error; got {result.error!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC5 — _write_satisfaction_doc: structured FAIL overrides high markdown SCORE
# ═══════════════════════════════════════════════════════════════════════════════


def test_write_satisfaction_doc_structured_fail_overrides_high_score(
    tmp_path: Path, monkeypatch
) -> None:
    """AC5: raw with structured {"satisfied": false, "fixes_required": [...]} AND SCORE: 95
    → status="error", error_code="E_SATISFACTION_BELOW_THRESHOLD",
    error message references fix count.

    FAILS on current main: gate logic only uses markdown SCORE (95 >= 80 → would pass).
    """
    _suppress_emit(monkeypatch)
    ctx = _make_ctx(tmp_path, satisfaction_threshold=80)
    raw = _raw_fail_with_score("SCORE: 95", _VALID_BLOCK_FAIL)
    prev = _make_satisfaction_prev(tmp_path, raw)

    result = _write_satisfaction_doc(ctx, prev)

    assert result.status == "error", (
        f"AC5 FAIL: expected status='error' when structured says satisfied=False "
        f"(even though SCORE 95 >= threshold 80); got status={result.status!r} — F60BDC71"
    )
    assert result.error_code == "E_SATISFACTION_BELOW_THRESHOLD", (
        f"AC5 FAIL: expected error_code='E_SATISFACTION_BELOW_THRESHOLD'; "
        f"got {result.error_code!r}"
    )
    # error message must reference fix count (spec: "error message references the fix count")
    assert result.error is not None and (
        "1" in result.error or "fix" in result.error.lower()
    ), (
        f"AC5 FAIL: error message must reference fix count; got {result.error!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC6 — _write_satisfaction_doc: no structured block, SCORE ≥ threshold → ok (fallback)
# ═══════════════════════════════════════════════════════════════════════════════


def test_write_satisfaction_doc_no_structured_block_score_pass(
    tmp_path: Path, monkeypatch
) -> None:
    """AC6: raw with NO structured block, SCORE: 88 (>= threshold 80)
    → status="ok" (markdown fallback preserved).

    This backward-compat path already works today; test guards that GREEN preserves it.
    """
    _suppress_emit(monkeypatch)
    ctx = _make_ctx(tmp_path, satisfaction_threshold=80)
    raw = _raw_with_score("SCORE: 88", None)  # no structured block
    prev = _make_satisfaction_prev(tmp_path, raw)

    result = _write_satisfaction_doc(ctx, prev)

    assert result.status == "ok", (
        f"AC6 FAIL: expected status='ok' for markdown-fallback SCORE 88 >= threshold 80; "
        f"got status={result.status!r}, error={getattr(result, 'error', None)!r} — F60BDC71"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC7 — _write_satisfaction_doc: no structured block, SCORE < threshold → error
# ═══════════════════════════════════════════════════════════════════════════════


def test_write_satisfaction_doc_no_structured_block_score_fail(
    tmp_path: Path, monkeypatch
) -> None:
    """AC7: raw with NO structured block, SCORE: 50 (< threshold 80)
    → status="error", error_code="E_SATISFACTION_BELOW_THRESHOLD".

    This path already works today; test guards that GREEN preserves it.
    """
    _suppress_emit(monkeypatch)
    ctx = _make_ctx(tmp_path, satisfaction_threshold=80)
    raw = _raw_with_score("SCORE: 50", None)
    prev = _make_satisfaction_prev(tmp_path, raw)

    result = _write_satisfaction_doc(ctx, prev)

    assert result.status == "error", (
        f"AC7 FAIL: expected status='error' for markdown SCORE 50 < threshold 80 "
        f"with no structured block; got {result.status!r} — F60BDC71"
    )
    assert result.error_code == "E_SATISFACTION_BELOW_THRESHOLD", (
        f"AC7 FAIL: expected error_code='E_SATISFACTION_BELOW_THRESHOLD'; "
        f"got {result.error_code!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC8 — _write_satisfaction_doc: no structured block, no SCORE line → error
# ═══════════════════════════════════════════════════════════════════════════════


def test_write_satisfaction_doc_no_structured_block_no_score(
    tmp_path: Path, monkeypatch
) -> None:
    """AC8: raw with NO structured block, NO SCORE: line at all
    → status="error", error_code="E_SATISFACTION_BELOW_THRESHOLD".

    This path already works today; test guards that GREEN preserves it.
    """
    _suppress_emit(monkeypatch)
    ctx = _make_ctx(tmp_path, satisfaction_threshold=80)
    raw = "## Composite\nThis is an incomplete evaluation.\nVERDICT: PASS\n"
    prev = _make_satisfaction_prev(tmp_path, raw)

    result = _write_satisfaction_doc(ctx, prev)

    assert result.status == "error", (
        f"AC8 FAIL: expected status='error' when no SCORE and no structured block; "
        f"got {result.status!r} — F60BDC71"
    )
    assert result.error_code == "E_SATISFACTION_BELOW_THRESHOLD", (
        f"AC8 FAIL: expected error_code='E_SATISFACTION_BELOW_THRESHOLD'; "
        f"got {result.error_code!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC9 — _build_satisfaction_prompt: output contains structured addendum
# ═══════════════════════════════════════════════════════════════════════════════


def test_build_satisfaction_prompt_contains_structured_addendum(tmp_path: Path) -> None:
    """AC9: _build_satisfaction_prompt output prompt must contain:
    - "## satisfaction-output (structured)"
    - the substring "satisfied"
    - the substring "fixes_required"
    - the word "AUTHORITATIVE"

    FAILS on current main: prompt does not contain this addendum.
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)

    spec_file = tmp_path / "spec.md"
    review_file = tmp_path / "review.md"
    fix_file = tmp_path / "fix.md"
    spec_file.write_text("# Spec\n", encoding="utf-8")
    review_file.write_text("# Review\n", encoding="utf-8")
    fix_file.write_text("# Fix\n", encoding="utf-8")

    ctx = _make_ctx(tmp_path)
    prev = StepResult(
        status="ok",
        data={
            "spec_path": str(spec_file),
            "review_doc_path": str(review_file),
            "fix_doc_path": str(fix_file),
        },
        duration_ms=0,
        step_name="invoke_fix_llm",
    )

    result = _build_satisfaction_prompt(ctx, prev)

    assert result.status == "ok", (
        f"AC9 FAIL: _build_satisfaction_prompt returned error: "
        f"{getattr(result, 'error_code', None)}: {getattr(result, 'error', None)}"
    )
    prompt: str = result.data["prompt"]

    assert "## satisfaction-output (structured)" in prompt, (
        "AC9 FAIL: prompt must contain '## satisfaction-output (structured)'; "
        "not found — F60BDC71 _build_satisfaction_prompt addendum not yet implemented"
    )
    assert '"satisfied"' in prompt or "'satisfied'" in prompt or "satisfied" in prompt, (
        "AC9 FAIL: prompt must reference 'satisfied' field in structured block; "
        "not found — F60BDC71"
    )
    assert "fixes_required" in prompt, (
        "AC9 FAIL: prompt must reference 'fixes_required' field; not found — F60BDC71"
    )
    assert "AUTHORITATIVE" in prompt, (
        "AC9 FAIL: prompt must declare the structured block is AUTHORITATIVE; "
        "not found — F60BDC71"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC10 — _write_satisfaction_doc threads structured_verdict into StepResult.data
# ═══════════════════════════════════════════════════════════════════════════════


def test_write_satisfaction_doc_threads_structured_verdict_key(
    tmp_path: Path, monkeypatch
) -> None:
    """AC10: after _write_satisfaction_doc:
    - AC4 input (structured=true, SCORE 40): result.data["structured_verdict"] is
      a SatisfactionVerdict with .satisfied is True
    - AC6 input (no block, SCORE 88): result.data["structured_verdict"] is None

    FAILS on current main: 'structured_verdict' key is absent from result.data.
    """
    _suppress_emit(monkeypatch)
    ctx = _make_ctx(tmp_path, satisfaction_threshold=80)

    # Part A: structured block present (satisfied=True, SCORE 40 → gate passes via structured)
    raw_a = _raw_with_score("SCORE: 40", _VALID_BLOCK_PASS)
    prev_a = _make_satisfaction_prev(tmp_path, raw_a)
    result_a = _write_satisfaction_doc(ctx, prev_a)

    assert "structured_verdict" in (result_a.data or {}), (
        "AC10 FAIL: 'structured_verdict' key must be present in result.data when "
        "structured block is present; key absent — F60BDC71"
    )
    sv = (result_a.data or {}).get("structured_verdict")
    assert isinstance(sv, SatisfactionVerdict), (
        f"AC10 FAIL: expected SatisfactionVerdict instance; got {type(sv)!r}"
    )
    assert sv.satisfied is True, (
        f"AC10 FAIL: expected .satisfied=True; got {sv.satisfied!r}"
    )

    # Part B: no structured block (SCORE 88 → gate passes via markdown fallback)
    raw_b = _raw_with_score("SCORE: 88", None)
    prev_b = _make_satisfaction_prev(tmp_path, raw_b)
    result_b = _write_satisfaction_doc(ctx, prev_b)

    assert "structured_verdict" in (result_b.data or {}), (
        "AC10 FAIL: 'structured_verdict' key must be present in result.data even when "
        "block is absent (value should be None); key absent — F60BDC71"
    )
    assert (result_b.data or {}).get("structured_verdict") is None, (
        f"AC10 FAIL: expected structured_verdict=None when no block; "
        f"got {(result_b.data or {}).get('structured_verdict')!r}"
    )
