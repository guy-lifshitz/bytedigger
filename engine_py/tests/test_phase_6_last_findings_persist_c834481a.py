"""RED tests for C834481A — phase_6_review must persist last_findings.json on satisfaction
failure and inject Prior-Attempt Context into the review prompt on re-attempt.

AC1: _write_satisfaction_doc writes <scratchpad>/reviews/last_findings.json on failure.
AC2: The write uses atomic_write (verified structurally via file presence — behavior test).
AC3: _build_review_prompt injects ## Prior-Attempt Context block when last_findings.json exists.
AC4: _build_review_prompt output unchanged (no injection) when last_findings.json is absent.
AC5+AC6: Telemetry events are emitted (phase_6_persist_last_findings, phase_6_inject_prior_context).

ALL tests MUST FAIL on current main — production code does not yet write last_findings.json
or inject prior context. Do NOT implement the fix here — RED-only file (C834481A).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).parent
ENGINE_ROOT = HERE.parent
sys.path.insert(0, str(ENGINE_ROOT))
sys.path.insert(0, str(ENGINE_ROOT / "lib"))
sys.path.insert(0, str(ENGINE_ROOT / "workflows"))

from contracts import StepResult, WorkflowContext  # noqa: E402
from phase_6_review import (  # noqa: E402
    _build_review_prompt,
    _write_satisfaction_doc,
)

# ─── shared fixtures ──────────────────────────────────────────────────────────

LAST_FINDINGS_RELPATH = "reviews/last_findings.json"

_SAMPLE_STRUCTURED_FINDINGS = [
    {"id": "1", "severity": "HIGH", "path": "src/foo.py", "description": "missing error handling"},
    {"id": "2", "severity": "MEDIUM", "path": "src/bar.py", "description": "unused variable"},
]

_REVIEW_DOC_WITH_FINDINGS = """\
# Composite Review

Some review text here.

## Findings (structured)
```json
[
  {"id": "1", "severity": "HIGH", "path": "src/foo.py", "description": "missing error handling"},
  {"id": "2", "severity": "MEDIUM", "path": "src/bar.py", "description": "unused variable"}
]
```

VERDICT: FAIL
"""

_REVIEW_DOC_MALFORMED_JSON = """\
# Composite Review

Some review text here.

## Findings (structured)
```json
[
  {"id": "1", "severity": "HIGH", "path": "src/foo.py", "description": "truncated
```

VERDICT: FAIL
"""


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
        session_id="test-C834481A",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_satisfaction_prev(
    tmp_path: Path,
    raw_response: str,
    review_doc_content: str = _REVIEW_DOC_WITH_FINDINGS,
) -> StepResult:
    """Construct a prev StepResult that _write_satisfaction_doc expects."""
    scratch = tmp_path / "scratch"
    reviews_dir = scratch / "reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)

    sat_doc = reviews_dir / "build-satisfaction.md"
    review_doc = reviews_dir / "build-review.md"
    review_doc.write_text(review_doc_content, encoding="utf-8")

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
    """Suppress _emit_safe calls and capture them for assertion."""
    import phase_6_review
    captured = []
    monkeypatch.setattr(
        phase_6_review,
        "_emit_safe",
        lambda et, p, **kw: captured.append((et, p, kw)),
    )
    return captured


# ═══════════════════════════════════════════════════════════════════════════════
# T01 — AC1+AC2: satisfaction failure writes last_findings.json
# ═══════════════════════════════════════════════════════════════════════════════


def test_satisfaction_failure_writes_last_findings_json(tmp_path: Path, monkeypatch) -> None:
    """AC1+AC2 (C834481A): _write_satisfaction_doc must write last_findings.json on failure.

    Trigger: score=50 < threshold=80. The file must exist after the call with
    the correct JSON shape. FAILS on current main (file not written).
    """
    _suppress_emit(monkeypatch)
    ctx = _make_ctx(tmp_path, satisfaction_threshold=80)
    prev = _make_satisfaction_prev(tmp_path, raw_response="SCORE: 50\nNeeds work.")

    result = _write_satisfaction_doc(ctx, prev)

    # Must fail with recoverable=False
    assert result.status == "error", (
        f"AC1 FAIL: expected error status from _write_satisfaction_doc on score<threshold; "
        f"got {result.status!r}"
    )
    assert result.recoverable is False, (
        f"AC1 FAIL: expected recoverable=False; got {result.recoverable!r}"
    )

    scratch = tmp_path / "scratch"
    last_findings_path = scratch / LAST_FINDINGS_RELPATH
    assert last_findings_path.is_file(), (
        f"AC1 FAIL: last_findings.json was NOT written after satisfaction failure. "
        f"Expected at: {last_findings_path}. "
        f"scratchpad contents: {list(scratch.rglob('*'))} — spec C834481A"
    )

    data = json.loads(last_findings_path.read_text(encoding="utf-8"))

    assert data.get("attempt") == 1, (
        f"AC1 FAIL: attempt must be 1 (no prior file); got attempt={data.get('attempt')!r}"
    )
    assert data.get("score") == 50, (
        f"AC1 FAIL: score must be 50; got {data.get('score')!r}"
    )
    assert data.get("threshold") == 80, (
        f"AC1 FAIL: threshold must be 80; got {data.get('threshold')!r}"
    )
    assert "review_doc_path" in data and isinstance(data["review_doc_path"], str), (
        f"AC1 FAIL: review_doc_path must be a string in last_findings.json; "
        f"got {data.get('review_doc_path')!r}"
    )
    assert "structured_findings" in data and isinstance(data["structured_findings"], list), (
        f"AC1 FAIL: structured_findings must be a list in last_findings.json; "
        f"got {data.get('structured_findings')!r}"
    )
    assert len(data["structured_findings"]) == 2, (
        f"AC1 FAIL: structured_findings must have 2 items (from review doc); "
        f"got {len(data['structured_findings'])} items: {data['structured_findings']}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# T02 — AC1: attempt counter increments on successive failures
# ═══════════════════════════════════════════════════════════════════════════════


def test_satisfaction_failure_attempt_increments(tmp_path: Path, monkeypatch) -> None:
    """AC1 (C834481A): when last_findings.json already exists with attempt=1,
    a second failure must write attempt=2.

    FAILS on current main (file not written at all, let alone incremented).
    """
    _suppress_emit(monkeypatch)
    scratch = tmp_path / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    reviews_dir = scratch / "reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)

    # Pre-write last_findings.json with attempt=1
    prior = {
        "attempt": 1,
        "score": 55,
        "threshold": 80,
        "review_doc_path": str(reviews_dir / "build-review.md"),
        "structured_findings": [],
    }
    (reviews_dir / "last_findings.json").write_text(
        json.dumps(prior), encoding="utf-8"
    )

    ctx = _make_ctx(tmp_path, satisfaction_threshold=80)
    prev = _make_satisfaction_prev(tmp_path, raw_response="SCORE: 60\nStill needs work.")

    result = _write_satisfaction_doc(ctx, prev)

    assert result.status == "error", (
        f"T02 FAIL: expected error status; got {result.status!r}"
    )

    last_findings_path = scratch / LAST_FINDINGS_RELPATH
    assert last_findings_path.is_file(), (
        f"T02 FAIL: last_findings.json missing after second failure — spec C834481A"
    )

    data = json.loads(last_findings_path.read_text(encoding="utf-8"))
    assert data.get("attempt") == 2, (
        f"T02 FAIL: attempt must be 2 (prior attempt was 1); "
        f"got attempt={data.get('attempt')!r} — spec C834481A"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# T03 — AC1: score=None path also writes last_findings.json
# ═══════════════════════════════════════════════════════════════════════════════


def test_satisfaction_score_none_also_writes(tmp_path: Path, monkeypatch) -> None:
    """AC1 (C834481A): when the satisfaction response has no SCORE: line (score=None),
    _write_satisfaction_doc must still write last_findings.json with score=null.

    FAILS on current main (file not written).
    """
    _suppress_emit(monkeypatch)
    ctx = _make_ctx(tmp_path, satisfaction_threshold=80)
    # Raw response with NO SCORE: line → score parses to None
    prev = _make_satisfaction_prev(tmp_path, raw_response="The implementation is incomplete.\nNo score given.")

    result = _write_satisfaction_doc(ctx, prev)

    assert result.status == "error", (
        f"T03 FAIL: expected error status when score=None; got {result.status!r}"
    )

    scratch = tmp_path / "scratch"
    last_findings_path = scratch / LAST_FINDINGS_RELPATH
    assert last_findings_path.is_file(), (
        f"T03 FAIL: last_findings.json not written when score=None — spec C834481A. "
        f"Expected at {last_findings_path}"
    )

    data = json.loads(last_findings_path.read_text(encoding="utf-8"))
    assert data.get("score") is None, (
        f"T03 FAIL: score must be null (JSON None) when no SCORE: line; "
        f"got {data.get('score')!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# T04 — AC3: _build_review_prompt injects Prior-Attempt Context when file present
# ═══════════════════════════════════════════════════════════════════════════════


def test_build_review_prompt_injects_prior_context_when_file_present(
    tmp_path: Path, monkeypatch
) -> None:
    """AC3 (C834481A): _build_review_prompt must inject ## Prior-Attempt Context block
    when <scratchpad>/reviews/last_findings.json exists.

    Block must contain RE-ATTEMPT marker, prior findings JSON, and PRIOR directive.
    Block must appear BEFORE the STRUCTURED FINDINGS section in the prompt.
    FAILS on current main (no injection).
    """
    _suppress_emit(monkeypatch)
    scratch = tmp_path / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    reviews_dir = scratch / "reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)

    prior_findings = [
        {"id": "1", "severity": "HIGH", "path": "x.py", "description": "test finding"}
    ]
    prior_data = {
        "attempt": 1,
        "score": 60,
        "threshold": 80,
        "review_doc_path": str(reviews_dir / "build-review.md"),
        "structured_findings": prior_findings,
    }
    (reviews_dir / "last_findings.json").write_text(
        json.dumps(prior_data), encoding="utf-8"
    )

    ctx = _make_ctx(tmp_path)
    result = _build_review_prompt(ctx, None)

    assert result.status == "ok", (
        f"T04 FAIL: _build_review_prompt returned error: {result.error_code}: {result.error}"
    )
    prompt: str = result.data["prompt"]

    assert "## Prior-Attempt Context" in prompt, (
        f"T04 FAIL: prompt must contain '## Prior-Attempt Context' when last_findings.json exists; "
        f"not found — spec C834481A AC3"
    )

    assert "re-attempt 1" in prompt.lower(), (
        f"T04 FAIL: prompt must contain 'RE-ATTEMPT 1' (case-insensitive) to identify attempt number; "
        f"not found — spec C834481A AC3"
    )

    assert "x.py" in prompt, (
        f"T04 FAIL: prompt must contain prior findings JSON (found 'x.py' marker); "
        f"not found — spec C834481A AC3"
    )

    assert "prior — still present" in prompt.lower(), (
        f"T04 FAIL: prompt must contain directive 'PRIOR — still present' (case-insensitive); "
        f"not found — spec C834481A AC3"
    )

    # Block must appear BEFORE the STRUCTURED FINDINGS schema section
    prior_ctx_pos = prompt.lower().find("prior-attempt context")
    structured_pos = prompt.lower().find("structured findings")
    assert prior_ctx_pos != -1 and structured_pos != -1, (
        f"T04 FAIL: both 'Prior-Attempt Context' and 'STRUCTURED FINDINGS' must exist; "
        f"prior_ctx_pos={prior_ctx_pos}, structured_pos={structured_pos}"
    )
    assert prior_ctx_pos < structured_pos, (
        f"T04 FAIL: '## Prior-Attempt Context' block must appear BEFORE 'STRUCTURED FINDINGS' "
        f"in the prompt (positions: prior={prior_ctx_pos}, structured={structured_pos}) — "
        f"spec C834481A AC3"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# T05 — AC4: _build_review_prompt unchanged when no prior file
# ═══════════════════════════════════════════════════════════════════════════════


def test_build_review_prompt_unchanged_when_no_prior_file(
    tmp_path: Path, monkeypatch
) -> None:
    """AC4 (C834481A): when last_findings.json does NOT exist, _build_review_prompt
    must NOT contain any Prior-Attempt Context injection.

    This test is expected to PASS on current main (no injection exists yet),
    but serves as a regression guard after GREEN lands.
    """
    _suppress_emit(monkeypatch)
    ctx = _make_ctx(tmp_path)
    # No last_findings.json written — scratchpad exists but reviews/ is empty
    result = _build_review_prompt(ctx, None)

    assert result.status == "ok", (
        f"T05 FAIL: _build_review_prompt returned error: {result.error_code}: {result.error}"
    )
    prompt: str = result.data["prompt"]

    assert "Prior-Attempt Context" not in prompt, (
        f"T05 FAIL: prompt must NOT contain 'Prior-Attempt Context' when no prior file; "
        f"found in prompt — spec C834481A AC4"
    )
    assert "RE-ATTEMPT" not in prompt.upper(), (
        f"T05 FAIL: prompt must NOT contain 'RE-ATTEMPT' when no prior file; "
        f"found in prompt — spec C834481A AC4"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# T06 — AC1 fallback: malformed JSON in review doc writes structured_findings=[]
# ═══════════════════════════════════════════════════════════════════════════════


def test_extract_structured_findings_parse_failure_falls_back_to_empty_list(
    tmp_path: Path, monkeypatch
) -> None:
    """AC1 fallback (C834481A): when review doc has malformed structured-findings JSON,
    last_findings.json must still be written with structured_findings=[].
    Must NOT raise.

    FAILS on current main (file not written at all).
    """
    _suppress_emit(monkeypatch)
    ctx = _make_ctx(tmp_path, satisfaction_threshold=80)
    prev = _make_satisfaction_prev(
        tmp_path,
        raw_response="SCORE: 45\nNeeds more work.",
        review_doc_content=_REVIEW_DOC_MALFORMED_JSON,
    )

    # Must not raise even with malformed review doc JSON
    try:
        result = _write_satisfaction_doc(ctx, prev)
    except Exception as exc:
        raise AssertionError(
            f"T06 FAIL: _write_satisfaction_doc raised on malformed review JSON: {exc!r} "
            f"— spec C834481A says parse failure → empty list, not exception"
        ) from exc

    assert result.status == "error", (
        f"T06 FAIL: expected error status (score<threshold); got {result.status!r}"
    )

    scratch = tmp_path / "scratch"
    last_findings_path = scratch / LAST_FINDINGS_RELPATH
    assert last_findings_path.is_file(), (
        f"T06 FAIL: last_findings.json not written despite malformed review doc JSON. "
        f"Expected at {last_findings_path} — spec C834481A"
    )

    data = json.loads(last_findings_path.read_text(encoding="utf-8"))
    assert data.get("structured_findings") == [], (
        f"T06 FAIL: structured_findings must be [] (empty list) on parse failure; "
        f"got {data.get('structured_findings')!r} — spec C834481A AC1"
    )
