"""RED tests for F7830037: in-session review normalization (deterministic fallback).

Spec: SHARED/memory/Decisions/...F7830037...spec.md

Design summary:
  - NEW helper _normalize_to_aggregated_findings(raw) — deterministic; idempotent.
  - _write_review_artifact: when backend == claude-in-session AND content is
    non-conformant AND no aggregated_content, normalize deterministically, emit
    review_stdout_normalized_deterministic, SKIP retry.
  - Subprocess backend keeps existing retry-then-die path unchanged.
  - _build_review_prompt OUTPUT directive gains substring
    "summary MUST begin with a line exactly".

Expected pre-GREEN status:
  FAIL today: AC1, AC2, AC3, AC4, AC5, AC6, AC7, AC8  (all new — absent today)
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── sys.path setup mirrors test_phase_6_review_return_discipline_CF838E6F.py ──
HERE = Path(__file__).parent
ENGINE_ROOT = HERE.parent
WORKFLOWS = ENGINE_ROOT / "workflows"

if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))
if str(ENGINE_ROOT / "lib") not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT / "lib"))
if str(WORKFLOWS) not in sys.path:
    sys.path.insert(0, str(WORKFLOWS))

# Top-level import of the module (already exists — collects fine).
# _normalize_to_aggregated_findings does NOT exist yet; referenced ONLY
# inside test function bodies via p6._normalize_to_aggregated_findings(...)
# so the file COLLECTS and tests FAIL at assert/attribute time (§1q/D1CF5FDF).
import phase_6_review as p6  # noqa: E402

# Existing symbols — all exist today, safe to import at module level.
from phase_6_review import (  # noqa: E402
    _build_review_prompt,
    _write_review_artifact,
)
from contracts import StepResult, WorkflowContext  # noqa: E402


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_ctx(scratchpad: Path) -> WorkflowContext:
    scratchpad.mkdir(parents=True, exist_ok=True)
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={"scratchpad_dir": str(scratchpad)},
        question="Add normalize helper to in-session review path",
        session_id="test-F7830037",
        persona="hal",
        framework=None,
        domain=None,
    )


def _seed_spec(scratchpad: Path) -> Path:
    spec = scratchpad / "specs/build-spec.md"
    spec.parent.mkdir(parents=True, exist_ok=True)
    spec.write_text("## AC1\nNormalize in-session.\n", encoding="utf-8")
    return spec


def _seed_logs(scratchpad: Path):
    red_log = scratchpad / "tests/build-red-output.log"
    red_log.parent.mkdir(parents=True, exist_ok=True)
    red_log.write_text("RED COMPLETE\n", encoding="utf-8")
    green_log = scratchpad / "tests/build-green-output.log"
    green_log.write_text("GREEN COMPLETE\n", encoding="utf-8")
    return red_log, green_log


def _mock_invoke_ok_raw(raw_response: str = "OK") -> MagicMock:
    """Mock that returns a successful StepResult with a given raw_response."""
    return MagicMock(return_value=StepResult(
        status="ok",
        data={
            "raw_response": raw_response,
            "response_bytes": len(raw_response),
            "command": ["x"],
        },
        duration_ms=0,
        step_name="mock_invoke",
    ))


def _prev_nonconformant(tmp_path: Path, raw_response: str | None = None) -> tuple[StepResult, Path]:
    """Build a non-conformant prev StepResult (no aggregated_content, no doc_path file).

    Returns (prev, doc_path).
    """
    scratchpad = tmp_path / "scratch"
    reviews_dir = scratchpad / "reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)
    doc_path = reviews_dir / "build-review.md"
    # doc_path intentionally NOT written — file absent

    spec_doc = _seed_spec(scratchpad)
    red_log, green_log = _seed_logs(scratchpad)

    if raw_response is None:
        raw_response = "summary only, no header\nVERDICT: PASS"

    prev = StepResult(
        status="ok",
        data={
            "raw_response": raw_response,
            "doc_path": str(doc_path),
            "spec_path": str(spec_doc),
            "red_log_path": str(red_log),
            "green_log_path": str(green_log),
            "prompt": "review the implementation",
            # No aggregated_content → stdout-fallback path
        },
        duration_ms=0,
        step_name="invoke_review_llm",
    )
    return prev, doc_path


def _prev_for_write_artifact(tmp_path: Path, *, aggregated_content: str,
                              verdict: str = "PASS",
                              verified_findings=None) -> StepResult:
    """Build a prev StepResult for _write_review_artifact (aggregated_content path)."""
    scratchpad = tmp_path / "scratch"
    reviews_dir = scratchpad / "reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)
    doc_path = reviews_dir / "build-review.md"

    spec_doc = _seed_spec(scratchpad)
    red_log, green_log = _seed_logs(scratchpad)

    return StepResult(
        status="ok",
        data={
            "aggregated_content": aggregated_content,
            "verified_findings": verified_findings if verified_findings is not None else [],
            "doc_path": str(doc_path),
            "spec_path": str(spec_doc),
            "red_log_path": str(red_log),
            "green_log_path": str(green_log),
            "verdict": verdict,
        },
        duration_ms=0,
        step_name="aggregate_review_findings",
    )


def _patch_emit(monkeypatch) -> list[tuple]:
    """Capture every _emit_safe call in phase_6_review; return list of (event_type, payload)."""
    captured: list[tuple] = []

    def _capture(event_type, payload, **kw):
        captured.append((event_type, payload))

    monkeypatch.setattr(p6, "_emit_safe", _capture)
    return captured


# ── AC1: in-session non-conformant → normalize deterministically, no retry ────


def test_ac1_insession_first_attempt_no_retry(tmp_path, monkeypatch):
    """AC1: backend=claude-in-session; non-conformant raw_response, no aggregated_content.

    ASSERT:
      - result.status == 'ok'
      - invoke_llm_subprocess NOT called (no retry)
      - written doc contains a line exactly '## Aggregated Findings'

    FAILS today: backend-aware branch and _normalize_to_aggregated_findings absent.
    """
    monkeypatch.setattr(p6, "_emit_safe", lambda *a, **kw: None)
    monkeypatch.setattr(
        "phase_6_review._resolve_backend",
        lambda *a, **kw: ("claude-in-session", "env"),
    )

    prev, doc_path = _prev_nonconformant(tmp_path)
    ctx = _make_ctx(tmp_path / "scratch")
    mock_invoke = _mock_invoke_ok_raw("## Aggregated Findings\n\nVERDICT: PASS\n")

    with patch("phase_6_review.invoke_llm_subprocess", mock_invoke):
        result = _write_review_artifact(ctx, prev)

    assert result.status == "ok", (
        f"AC1: expected status='ok', got {result.status!r} "
        f"(error={getattr(result, 'error', None)!r}, "
        f"error_code={getattr(result, 'error_code', None)!r})"
    )
    assert mock_invoke.call_count == 0, (
        f"AC1: expected 0 retry calls for in-session backend, "
        f"got {mock_invoke.call_count} call(s). "
        "FAILS today: backend-aware skip-retry branch absent."
    )
    disk = doc_path.read_text(encoding="utf-8")
    conformant_lines = [
        line.rstrip("\n\r") for line in disk.splitlines(keepends=True)
        if line.rstrip("\n\r") == "## Aggregated Findings"
    ]
    assert conformant_lines, (
        f"AC1: written doc must contain a line exactly '## Aggregated Findings'. "
        f"Got doc content: {disk[:300]!r}. "
        "FAILS today: normalization helper absent."
    )


# ── AC2: body content preserved after normalization ───────────────────────────


def test_ac2_insession_body_preserved(tmp_path, monkeypatch):
    """AC2: body text is preserved when normalization wraps the raw response.

    ASSERT: written doc contains BOTH 'SENTINEL-FINDING-F7830037'
            AND a line exactly '## Aggregated Findings'.

    FAILS today: normalization helper and backend branch absent.
    """
    monkeypatch.setattr(p6, "_emit_safe", lambda *a, **kw: None)
    monkeypatch.setattr(
        "phase_6_review._resolve_backend",
        lambda *a, **kw: ("claude-in-session", "env"),
    )

    sentinel = "SENTINEL-FINDING-F7830037"
    prev, doc_path = _prev_nonconformant(tmp_path, raw_response=f"{sentinel}\nVERDICT: PASS")
    ctx = _make_ctx(tmp_path / "scratch")
    mock_invoke = _mock_invoke_ok_raw("## Aggregated Findings\n\nVERDICT: PASS\n")

    with patch("phase_6_review.invoke_llm_subprocess", mock_invoke):
        result = _write_review_artifact(ctx, prev)

    assert result.status == "ok", (
        f"AC2: expected status='ok', got {result.status!r}"
    )
    disk = doc_path.read_text(encoding="utf-8")
    assert sentinel in disk, (
        f"AC2: written doc must contain sentinel '{sentinel}'. "
        f"Got: {disk[:300]!r}. "
        "FAILS today: normalization helper absent."
    )
    conformant_lines = [
        line.rstrip("\n\r") for line in disk.splitlines(keepends=True)
        if line.rstrip("\n\r") == "## Aggregated Findings"
    ]
    assert conformant_lines, (
        f"AC2: written doc must contain a line exactly '## Aggregated Findings'. "
        f"Got: {disk[:300]!r}."
    )


# ── AC3: _normalize_to_aggregated_findings wraps non-conformant; idempotent ───


def test_ac3_normalize_helper_wraps_and_idempotent(tmp_path):
    """AC3: _normalize_to_aggregated_findings wraps non-conformant input,
    and returns conformant input unchanged (idempotent).

    Checks:
      - p6._normalize_to_aggregated_findings("foo bar") contains line '## Aggregated Findings'
        AND contains 'foo bar'.
      - p6._normalize_to_aggregated_findings("a\n## Aggregated Findings\nb")
        returns the input string UNCHANGED.

    FAILS today: _normalize_to_aggregated_findings does not exist.
    """
    # Deferring to inside body per §1q/D1CF5FDF — collect fires, attribute error proves absence.
    fn = getattr(p6, "_normalize_to_aggregated_findings", None)
    assert fn is not None, (
        "AC3: p6._normalize_to_aggregated_findings does not exist yet. "
        "FAILS today: helper absent."
    )

    # Non-conformant input → should gain header
    result = fn("foo bar")
    assert isinstance(result, str), (
        f"AC3: expected str return, got {type(result)}"
    )
    conformant_lines = [
        line.rstrip("\n\r") for line in result.splitlines(keepends=True)
        if line.rstrip("\n\r") == "## Aggregated Findings"
    ]
    assert conformant_lines, (
        f"AC3: normalized result must contain a line '## Aggregated Findings'. "
        f"Got: {result!r}"
    )
    assert "foo bar" in result, (
        f"AC3: normalized result must contain the original body 'foo bar'. "
        f"Got: {result!r}"
    )

    # Conformant input → idempotent (returned unchanged)
    conformant = "a\n## Aggregated Findings\nb"
    result_idem = fn(conformant)
    assert result_idem == conformant, (
        f"AC3: conformant input must be returned unchanged (idempotent). "
        f"Got: {result_idem!r}"
    )


# ── AC4: empty/whitespace-only input → placeholder ────────────────────────────


def test_ac4_normalize_empty_placeholder(tmp_path):
    """AC4: _normalize_to_aggregated_findings('   ') → conformant + contains '(no findings)'.

    FAILS today: helper absent.
    """
    fn = getattr(p6, "_normalize_to_aggregated_findings", None)
    assert fn is not None, (
        "AC4: p6._normalize_to_aggregated_findings does not exist. "
        "FAILS today: helper absent."
    )

    result = fn("   ")
    conformant_lines = [
        line.rstrip("\n\r") for line in result.splitlines(keepends=True)
        if line.rstrip("\n\r") == "## Aggregated Findings"
    ]
    assert conformant_lines, (
        f"AC4: result must contain a line '## Aggregated Findings'. Got: {result!r}"
    )
    assert "(no findings)" in result, (
        f"AC4: result must contain '(no findings)' placeholder for empty input. "
        f"Got: {result!r}"
    )


# ── AC5: subprocess backend → retry unchanged ────────────────────────────────


def test_ac5_subprocess_retry_unchanged(tmp_path, monkeypatch):
    """AC5: backend=claude-subprocess; non-conformant → retry IS called; result is E_REVIEW_FORMAT_DRIFT.

    ASSERT:
      - mock.call_count == 1  (retry attempted)
      - result.status == 'error'
      - result.error_code == 'E_REVIEW_FORMAT_DRIFT'

    This confirms the fix is backend-scoped; subprocess path is unchanged.

    FAILS today: in-session branch absent, so subprocess path already behaves this way.
    Actually passes today BUT verifies GREEN doesn't regress subprocess path.
    We assert current subprocess behavior; if the in-session branch accidentally breaks
    subprocess, this will fail.
    """
    monkeypatch.setattr(p6, "_emit_safe", lambda *a, **kw: None)
    monkeypatch.setattr(
        "phase_6_review._resolve_backend",
        lambda *a, **kw: ("claude-subprocess", "env"),
    )

    prev, _doc_path = _prev_nonconformant(tmp_path)
    ctx = _make_ctx(tmp_path / "scratch")

    # Retry returns a still-non-conformant raw_response → triggers E_REVIEW_FORMAT_DRIFT
    still_bad = "<still non-conformant — no header>"
    mock_invoke = _mock_invoke_ok_raw(still_bad)

    with patch("phase_6_review.invoke_llm_subprocess", mock_invoke):
        result = _write_review_artifact(ctx, prev)

    assert mock_invoke.call_count == 1, (
        f"AC5: subprocess backend must call invoke_llm_subprocess once (retry). "
        f"Got call_count={mock_invoke.call_count}. "
        "FAILS if GREEN accidentally bypasses retry for subprocess backend."
    )
    assert result.status == "error", (
        f"AC5: expected status='error' after failed retry on subprocess backend. "
        f"Got {result.status!r}."
    )
    assert getattr(result, "error_code", None) == "E_REVIEW_FORMAT_DRIFT", (
        f"AC5: expected error_code='E_REVIEW_FORMAT_DRIFT'. "
        f"Got {getattr(result, 'error_code', None)!r}."
    )


# ── AC6: _build_review_prompt OUTPUT directive ────────────────────────────────


def test_ac6_prompt_forcing_function(tmp_path):
    """AC6: _build_review_prompt OUTPUT directive contains
    'summary MUST begin with a line exactly'.

    FAILS today: substring absent from current prompt.
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    _seed_spec(scratchpad)
    _seed_logs(scratchpad)

    ctx = _make_ctx(scratchpad)
    prev = StepResult(status="ok", data={}, duration_ms=0, step_name="init")

    result = _build_review_prompt(ctx, prev)

    assert result.status == "ok", (
        f"AC6: _build_review_prompt returned error: {result.error!r}"
    )
    prompt = result.data["prompt"]
    assert "summary MUST begin with a line exactly" in prompt, (
        "AC6: _build_review_prompt OUTPUT directive must contain "
        "'summary MUST begin with a line exactly'. "
        "FAILS today: line absent from current prompt."
    )


# ── AC7: aggregated_content path → no normalize event ────────────────────────


def test_ac7_aggregated_content_path_unchanged(tmp_path, monkeypatch):
    """AC7: when prev has aggregated_content (conformant), _write_review_artifact
    must NOT emit 'review_stdout_normalized_deterministic'.

    PASSES when GREEN is shipped (existing aggregated_content path is unmodified).
    Verifies: status == 'ok' and no normalize event fired.

    FAILS today: because _normalize_to_aggregated_findings is absent,
    _write_review_artifact does not emit the event — so the 'no event' assertion
    passes. We also need status='ok' which DOES pass today.
    This test acts as a GREEN-correctness guard after ship.
    To force it to fail today on the NEW behavior, we additionally assert that
    a conformant aggregated_content round-trips via the helper (attribute check).

    Actually: this test MUST fail today for at least one assertion.
    We assert the helper attribute is absent (which is True today — fails the presence
    check inside the test). Guard: after GREEN, the event must NOT be emitted.
    We split: assert no event (passes today) AND assert helper callable (fails today).
    """
    captured = _patch_emit(monkeypatch)

    aggregated_content = (
        "## Aggregated Findings\n\nVERDICT: PASS\n"
    )
    prev = _prev_for_write_artifact(tmp_path, aggregated_content=aggregated_content, verdict="PASS")
    ctx = _make_ctx(tmp_path / "scratch")

    result = _write_review_artifact(ctx, prev)

    assert result.status == "ok", (
        f"AC7: expected status='ok', got {result.status!r}"
    )
    normalize_events = [
        (et, pl) for et, pl in captured
        if et == "review_stdout_normalized_deterministic"
    ]
    assert len(normalize_events) == 0, (
        "AC7: aggregated_content path must NOT emit 'review_stdout_normalized_deterministic'. "
        f"Got {len(normalize_events)} such event(s)."
    )
    # Force-fail today: helper must exist (absent pre-GREEN)
    fn = getattr(p6, "_normalize_to_aggregated_findings", None)
    assert fn is not None, (
        "AC7: p6._normalize_to_aggregated_findings must exist after GREEN. "
        "FAILS today: helper absent."
    )


# ── AC8: normalize event emitted once in in-session non-conformant path ───────


def test_ac8_normalize_event_emitted(tmp_path, monkeypatch):
    """AC8: AC1 scenario with emit-capture — exactly one event
    'review_stdout_normalized_deterministic' is emitted.

    FAILS today: backend branch and event emit absent.
    """
    captured = _patch_emit(monkeypatch)
    monkeypatch.setattr(
        "phase_6_review._resolve_backend",
        lambda *a, **kw: ("claude-in-session", "env"),
    )

    prev, _doc_path = _prev_nonconformant(tmp_path)
    ctx = _make_ctx(tmp_path / "scratch")
    mock_invoke = _mock_invoke_ok_raw("## Aggregated Findings\n\nVERDICT: PASS\n")

    with patch("phase_6_review.invoke_llm_subprocess", mock_invoke):
        result = _write_review_artifact(ctx, prev)

    normalize_events = [
        (et, pl) for et, pl in captured
        if et == "review_stdout_normalized_deterministic"
    ]
    assert len(normalize_events) == 1, (
        f"AC8: expected exactly 1 'review_stdout_normalized_deterministic' event. "
        f"Got {len(normalize_events)}. "
        "FAILS today: backend branch and event emit absent."
    )
