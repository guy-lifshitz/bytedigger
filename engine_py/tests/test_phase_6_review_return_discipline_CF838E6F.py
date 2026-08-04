"""RED tests for CF838E6F: phase_6 review subagent-return-discipline (FD2592D9 slice-6a).

Spec: SHARED/memory/Decisions/2026-06-12_CF838E6F_phase6_review_return_discipline_spec.md

Design summary:
  - Add "Write" to _invoke_review_llm allowed_tools (main dispatch + retry).
  - Add terse/no-echo OUTPUT directive to _build_review_prompt (landmarks
    "OUTPUT —" and "Do NOT echo").
  - Emit review_writer_return_source event in _write_review_artifact on both
    happy path (source="aggregated_content") and disk-conformant fallback
    (source="doc_file").

Expected pre-GREEN status:
  FAIL today: AC1, AC2, AC3, AC5, AC6  (new Write/directive/emit — absent today)
  PASS today: AC4, AC7, AC8, AC9, AC10  (regression guards — existing behaviour)
"""
from __future__ import annotations

import types
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# ── sys.path setup mirrors test_phase_6_verified_only_gate_65695203.py ─────────
import sys

HERE = Path(__file__).parent
ENGINE_ROOT = HERE.parent
WORKFLOWS = ENGINE_ROOT / "bytedigger_engine" / "workflows"

if str(ENGINE_ROOT) not in sys.path:
    sys.path.insert(0, str(ENGINE_ROOT))
if str(WORKFLOWS) not in sys.path:
    sys.path.insert(0, str(WORKFLOWS))

# Top-level import of the module (already exists — collects fine).
# NOT-YET-EXISTING symbols (review_writer_return_source emit) are exercised
# inside test bodies; the module itself imports fine today.
from bytedigger_engine.workflows import phase_6_review as _p6  # noqa: E402

# Import existing symbols used in regression-guard tests (all exist today).
from bytedigger_engine.workflows.phase_6_review import (  # noqa: E402
    REVIEW_DOC_RELPATH,
    _build_review_prompt,
    _write_review_artifact,
    _invoke_review_llm,
)
from bytedigger_engine.contracts import StepResult, WorkflowContext  # noqa: E402


# ── helpers ────────────────────────────────────────────────────────────────────


def _make_ctx(scratchpad: Path) -> WorkflowContext:
    scratchpad.mkdir(parents=True, exist_ok=True)
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={"scratchpad_dir": str(scratchpad)},
        question="Add foo to bar",
        session_id="test-CF838E6F",
        persona="hal",
        framework=None,
        domain=None,
    )


def _mock_invoke_ok_raw(raw_response: str = "OK") -> MagicMock:
    """Mock that returns a successful StepResult with a given raw_response."""
    return MagicMock(return_value=StepResult(
        status="ok",
        data={"raw_response": raw_response, "response_bytes": len(raw_response), "command": ["x"]},
        duration_ms=0,
        step_name="mock_invoke",
    ))


def _seed_spec(scratchpad: Path) -> Path:
    spec = scratchpad / "specs/build-spec.md"
    spec.parent.mkdir(parents=True, exist_ok=True)
    spec.write_text("## US1\nAdd foo\n", encoding="utf-8")
    return spec


def _seed_logs(scratchpad: Path):
    red_log = scratchpad / "tests/build-red-output.log"
    red_log.parent.mkdir(parents=True, exist_ok=True)
    red_log.write_text("RED COMPLETE\n", encoding="utf-8")
    green_log = scratchpad / "tests/build-green-output.log"
    green_log.write_text("GREEN COMPLETE\n", encoding="utf-8")
    return red_log, green_log


def _prev_for_write_artifact(tmp_path: Path, *, aggregated_content: str,
                              verdict: str = "PASS",
                              verified_findings=None) -> StepResult:
    """Build a prev StepResult for _write_review_artifact happy-path (aggregated_content path)."""
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

    monkeypatch.setattr(_p6, "_emit_safe", _capture)
    return captured


# ── AC1: _invoke_review_llm passes allowed_tools containing "Write" ────────────


def test_ac1_invoke_review_llm_includes_write_in_allowed_tools(tmp_path):
    """AC1: main dispatch _invoke_review_llm must pass allowed_tools containing 'Write'.

    FAILS today: allowed_tools=["Read", "Grep", "Glob"] — "Write" absent.
    """
    scratchpad = tmp_path / "scratch"
    spec_path = tmp_path / "spec.md"
    red_log_path = tmp_path / "red.log"
    green_log_path = tmp_path / "green.log"

    prev = StepResult(
        status="ok",
        data={
            "prompt": "review the implementation",
            "doc_path": str(tmp_path / "review.md"),
            "spec_path": str(spec_path),
            "red_log_path": str(red_log_path),
            "green_log_path": str(green_log_path),
        },
        duration_ms=0,
        step_name="build_review_prompt",
    )

    ctx = _make_ctx(scratchpad)
    mock_invoke = _mock_invoke_ok_raw("## Aggregated Findings\n\nVERDICT: PASS\n")

    with patch("bytedigger_engine.workflows.phase_6_review.invoke_llm_subprocess", mock_invoke):
        _invoke_review_llm(ctx, prev)

    assert mock_invoke.call_count >= 1, (
        "_invoke_review_llm did not call invoke_llm_subprocess"
    )
    _, kwargs = mock_invoke.call_args_list[0]
    actual = kwargs.get("allowed_tools", "__MISSING__")

    assert actual != "__MISSING__", (
        "_invoke_review_llm: invoke_llm_subprocess called but allowed_tools kwarg "
        f"was NOT passed at all. kwargs seen: {list(kwargs.keys())}"
    )
    assert "Write" in actual, (
        f"AC1: _invoke_review_llm must pass allowed_tools containing 'Write'. "
        f"Got: {actual!r}. FAILS today: current list is ['Read', 'Grep', 'Glob']."
    )


# ── AC2: conformance-retry callsite passes allowed_tools containing "Write" ────


# СНЯТО GH1399 (§1c-ОТМЕНА): test_ac2_conformance_retry_includes_write_in_allowed_tools
# CF838E6F AC2 — allowed_tools ретрай-вызова: сам вызов удалён GH1399.
# Остальные AC CF838E6F ретрая не касаются и остаются в силе.


# ── AC3: built prompt contains "OUTPUT —" and "Do NOT echo" ────────────────────


def test_ac3_build_review_prompt_contains_terse_directive(tmp_path):
    """AC3: _build_review_prompt must produce a prompt containing landmarks
    'OUTPUT —' and 'Do NOT echo' (whitespace-normalized, multi-line safe).

    FAILS today: neither landmark is present in the current prompt.
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    _seed_spec(scratchpad)
    _seed_logs(scratchpad)

    ctx = _make_ctx(scratchpad)
    # _build_review_prompt ignores prev entirely (_prev param)
    prev = StepResult(status="ok", data={}, duration_ms=0, step_name="init")

    result = _build_review_prompt(ctx, prev)

    assert result.status == "ok", (
        f"_build_review_prompt returned error: {result.error!r}"
    )
    prompt = result.data["prompt"]
    norm = " ".join(prompt.split())

    assert "OUTPUT —" in norm, (
        "AC3: built prompt must contain landmark 'OUTPUT —' (whitespace-normalized). "
        "FAILS today: terse/no-echo directive not yet added to _build_review_prompt."
    )
    assert "Do NOT echo" in norm, (
        "AC3: built prompt must contain landmark 'Do NOT echo' (whitespace-normalized). "
        "FAILS today: terse/no-echo directive not yet added to _build_review_prompt."
    )


# ── AC4: built prompt does NOT contain forbidden literals ──────────────────────


def test_ac4_build_review_prompt_absence_preserved(tmp_path):
    """AC4 (regression guard): _build_review_prompt must NOT contain '# Composite Review'
    nor 'AGGREGATION DEFERRED TO PYTHON STEP'.

    PASSES today: absence of these literals is already preserved (F34E2C82 guard).
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir(parents=True, exist_ok=True)
    _seed_spec(scratchpad)
    _seed_logs(scratchpad)

    ctx = _make_ctx(scratchpad)
    prev = StepResult(status="ok", data={}, duration_ms=0, step_name="init")

    result = _build_review_prompt(ctx, prev)

    assert result.status == "ok", (
        f"_build_review_prompt returned error: {result.error!r}"
    )
    prompt = result.data["prompt"]

    assert "# Composite Review" not in prompt, (
        "AC4: built prompt must NOT contain '# Composite Review'. "
        "Absence must remain after the new directive is added."
    )
    assert "AGGREGATION DEFERRED TO PYTHON STEP" not in prompt, (
        "AC4: built prompt must NOT contain 'AGGREGATION DEFERRED TO PYTHON STEP'. "
        "Absence must remain after the new directive is added."
    )


# ── AC5: happy path emits review_writer_return_source with source="aggregated_content" ──


def test_ac5_happy_path_emits_review_writer_return_source(tmp_path, monkeypatch):
    """AC5: _write_review_artifact (aggregated_content path) must emit
    'review_writer_return_source' with source="aggregated_content" and correct bytes_written.

    FAILS today: _emit_safe("review_writer_return_source", ...) is not called yet.
    """
    captured = _patch_emit(monkeypatch)

    aggregated_content = (
        "## Aggregated Findings\n\n"
        "### SEVERITY: HIGH — TestFinding\n\n"
        "VERDICT: FAIL\n"
    )
    expected_bytes = len(aggregated_content.encode("utf-8"))

    prev = _prev_for_write_artifact(tmp_path, aggregated_content=aggregated_content, verdict="FAIL")
    ctx = _make_ctx(tmp_path / "scratch")

    result = _write_review_artifact(ctx, prev)

    assert result.status == "ok", (
        f"AC5: _write_review_artifact returned {result.status}: {getattr(result, 'error', None)}"
    )

    source_events = [
        (et, payload)
        for et, payload in captured
        if et == "review_writer_return_source"
    ]
    assert len(source_events) >= 1, (
        "AC5: 'review_writer_return_source' event was NOT emitted by _write_review_artifact. "
        "FAILS today: emit call not yet added to happy path."
    )
    _, payload = source_events[0]
    assert payload.get("source") == "aggregated_content", (
        f"AC5: expected source='aggregated_content', got source={payload.get('source')!r}. "
        f"Full payload: {payload}"
    )
    assert payload.get("bytes_written") == expected_bytes, (
        f"AC5: expected bytes_written={expected_bytes}, got {payload.get('bytes_written')}."
    )


# ── AC6: fallback with conformant disk doc emits source="doc_file" ─────────────


def test_ac6_fallback_disk_conformant_emits_doc_file_source(tmp_path, monkeypatch):
    """AC6: when fallback path runs and _resolve_review_content returns on-disk content,
    _write_review_artifact must emit 'review_writer_return_source' with source='doc_file'.

    Setup: pre-write a conformant doc_path file; raw_response is short summary (non-conformant
    as stdout but disk is conformant). No aggregated_content → fallback path.

    FAILS today: emit call not yet added to fallback path.
    """
    captured = _patch_emit(monkeypatch)

    scratchpad = tmp_path / "scratch"
    reviews_dir = scratchpad / "reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)
    doc_path = reviews_dir / "build-review.md"

    # Pre-stage: conformant on-disk content (disk-first wins in _resolve_review_content)
    conformant_disk_content = (
        "# Composite Review\n\n## Aggregated Findings\n\nVERDICT: PASS\n"
    )
    doc_path.write_text(conformant_disk_content, encoding="utf-8")

    spec_doc = _seed_spec(scratchpad)
    red_log, green_log = _seed_logs(scratchpad)

    # raw_response is a short summary (no ## Aggregated Findings) — non-conformant stdout
    # but disk is conformant, so _resolve_review_content returns on-disk content.
    short_summary = "Review complete. VERDICT: PASS"

    prev = StepResult(
        status="ok",
        data={
            "raw_response": short_summary,
            "doc_path": str(doc_path),
            "spec_path": str(spec_doc),
            "red_log_path": str(red_log),
            "green_log_path": str(green_log),
            # No aggregated_content → fallback path
        },
        duration_ms=0,
        step_name="invoke_review_llm",
    )

    ctx = _make_ctx(scratchpad)
    result = _write_review_artifact(ctx, prev)

    assert result.status == "ok", (
        f"AC6: _write_review_artifact returned {result.status}: {getattr(result, 'error', None)}"
    )

    source_events = [
        (et, payload)
        for et, payload in captured
        if et == "review_writer_return_source"
    ]
    assert len(source_events) >= 1, (
        "AC6: 'review_writer_return_source' event was NOT emitted on fallback path. "
        "FAILS today: emit call not yet added to fallback path."
    )
    _, payload = source_events[0]
    assert payload.get("source") == "doc_file", (
        f"AC6: expected source='doc_file' (disk-conformant fallback). "
        f"Got source={payload.get('source')!r}. Full payload: {payload}"
    )


# ── AC7: verified-only invariant — aggregated_content wins over raw / pre-existing file ──


def test_ac7_aggregated_content_wins_over_raw_and_preexisting_disk(tmp_path, monkeypatch):
    """AC7: when aggregated_content is present, doc_path must contain aggregated_content —
    NOT raw_response and NOT the pre-existing file content.

    PASSES today: _write_review_artifact already writes aggregated_content when present
    (65695203 verified-only invariant is unchanged).
    """
    monkeypatch.setattr(_p6, "_emit_safe", lambda *a, **kw: None)

    scratchpad = tmp_path / "scratch"
    reviews_dir = scratchpad / "reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)
    doc_path = reviews_dir / "build-review.md"

    # Pre-stage a DIFFERENT existing file — aggregated_content must clobber it.
    preexisting = "## Old Review\n\nSome old content\n"
    doc_path.write_text(preexisting, encoding="utf-8")

    aggregated_content = (
        "## Aggregated Findings\n\n"
        "### SEVERITY: HIGH — AC7Verified\n\n"
        "VERDICT: FAIL\n"
    )
    raw_response = "Review complete. VERDICT: FAIL"  # different from aggregated_content

    spec_doc = _seed_spec(scratchpad)
    red_log, green_log = _seed_logs(scratchpad)

    prev = StepResult(
        status="ok",
        data={
            "aggregated_content": aggregated_content,
            "raw_response": raw_response,
            "verified_findings": [],
            "doc_path": str(doc_path),
            "spec_path": str(spec_doc),
            "red_log_path": str(red_log),
            "green_log_path": str(green_log),
            "verdict": "FAIL",
        },
        duration_ms=0,
        step_name="aggregate_review_findings",
    )

    ctx = _make_ctx(scratchpad)
    result = _write_review_artifact(ctx, prev)

    assert result.status == "ok", (
        f"AC7: _write_review_artifact returned {result.status}: {getattr(result, 'error', None)}"
    )

    disk_content = doc_path.read_text(encoding="utf-8")
    assert disk_content == aggregated_content, (
        f"AC7: doc_path content must equal aggregated_content. "
        f"Got: {disk_content[:200]!r}\nExpected: {aggregated_content[:200]!r}"
    )
    assert disk_content != raw_response, (
        "AC7: doc_path must NOT equal raw_response."
    )
    assert disk_content != preexisting, (
        "AC7: doc_path must NOT equal preexisting file content."
    )


# ── AC8: happy path returns both review_doc_path and review_fix_doc_path ───────


def test_ac8_happy_path_returns_both_path_keys(tmp_path, monkeypatch):
    """AC8 (regression guard): _write_review_artifact must return both
    'review_doc_path' and 'review_fix_doc_path' keys and the fix doc must exist.

    PASSES today: both keys are already returned (65695203 ship).
    """
    monkeypatch.setattr(_p6, "_emit_safe", lambda *a, **kw: None)

    aggregated_content = (
        "## Aggregated Findings\n\n"
        "### SEVERITY: MEDIUM — AC8Finding\n\n"
        "VERDICT: PASS\n"
    )
    prev = _prev_for_write_artifact(tmp_path, aggregated_content=aggregated_content, verdict="PASS")
    ctx = _make_ctx(tmp_path / "scratch")

    result = _write_review_artifact(ctx, prev)

    assert result.status == "ok", (
        f"AC8: _write_review_artifact returned {result.status}: {getattr(result, 'error', None)}"
    )
    assert "review_doc_path" in result.data, (
        "AC8: 'review_doc_path' key missing from result.data"
    )
    assert "review_fix_doc_path" in result.data, (
        "AC8: 'review_fix_doc_path' key missing from result.data"
    )
    fix_doc = Path(result.data["review_fix_doc_path"])
    assert fix_doc.exists(), (
        f"AC8: fix doc {fix_doc} does not exist after _write_review_artifact"
    )


# ── AC9: disk-first-summary scenario — conformant disk + summary stdout ────────


def test_ac9_disk_first_summary_no_retry_disk_intact(tmp_path, monkeypatch):
    """AC9 (regression guard): conformant disk doc + summary stdout →
    result status='ok', no retry (doc_path content unchanged), doc contains
    '## Aggregated Findings'.

    Reproduces test_review_conformance_reads_disk_when_stdout_is_summary scenario
    at the _write_review_artifact unit level (avoids full WorkflowEngine invocation).

    PASSES today: disk-first read is already implemented (A134BBD0).
    """
    monkeypatch.setattr(_p6, "_emit_safe", lambda *a, **kw: None)

    scratchpad = tmp_path / "scratch"
    reviews_dir = scratchpad / "reviews"
    reviews_dir.mkdir(parents=True, exist_ok=True)
    doc_path = reviews_dir / "build-review.md"

    conformant_disk = (
        "# Composite Review\n\n## Aggregated Findings\n\n## Summary\nVERDICT: PASS\n"
    )
    doc_path.write_text(conformant_disk, encoding="utf-8")

    spec_doc = _seed_spec(scratchpad)
    red_log, green_log = _seed_logs(scratchpad)

    # short summary stdout — non-conformant as raw, but disk is conformant
    short_summary = "Review complete. VERDICT: PASS"

    prev = StepResult(
        status="ok",
        data={
            "raw_response": short_summary,
            "doc_path": str(doc_path),
            "spec_path": str(spec_doc),
            "red_log_path": str(red_log),
            "green_log_path": str(green_log),
            "prompt": "review the implementation",
            # No aggregated_content → fallback path
        },
        duration_ms=0,
        step_name="invoke_review_llm",
    )

    ctx = _make_ctx(scratchpad)
    invoke_call_count = [0]

    def _mock_invoke(*a, **kw):
        invoke_call_count[0] += 1
        return StepResult(
            status="ok",
            data={"raw_response": "OK", "response_bytes": 2, "command": ["x"]},
            duration_ms=0,
            step_name="mock_retry",
        )

    with patch("bytedigger_engine.workflows.phase_6_review.invoke_llm_subprocess", side_effect=_mock_invoke):
        result = _write_review_artifact(ctx, prev)

    assert result.status == "ok", (
        f"AC9: expected status='ok' for disk-conformant+summary-stdout case. "
        f"Got {result.status} (error_code={getattr(result, 'error_code', None)})"
    )
    assert invoke_call_count[0] == 0, (
        f"AC9: expected 0 retry calls (disk was conformant). "
        f"Got {invoke_call_count[0]} invoke_llm_subprocess calls."
    )
    disk_after = doc_path.read_text(encoding="utf-8")
    assert "## Aggregated Findings" in disk_after, (
        "AC9: disk doc must still contain '## Aggregated Findings' after _write_review_artifact. "
        f"Disk content after: {disk_after[:200]!r}"
    )


# ── AC10: verdict forwarding — happy path uses aggregator verdict ───────────────


def test_ac10_happy_path_forwards_aggregator_verdict(tmp_path, monkeypatch):
    """AC10 (regression guard): happy path must forward verdict from prev.data["verdict"]
    and expose it as result.data["verdict"].

    PASSES today: verdict forwarding is already implemented.
    """
    monkeypatch.setattr(_p6, "_emit_safe", lambda *a, **kw: None)

    for expected_verdict in ["PASS", "FAIL", "PARTIAL"]:
        aggregated_content = (
            f"## Aggregated Findings\n\n"
            f"VERDICT: {expected_verdict}\n"
        )
        prev = _prev_for_write_artifact(
            tmp_path / expected_verdict,
            aggregated_content=aggregated_content,
            verdict=expected_verdict,
        )
        ctx = _make_ctx(tmp_path / expected_verdict / "scratch")

        result = _write_review_artifact(ctx, prev)

        assert result.status == "ok", (
            f"AC10: _write_review_artifact returned {result.status} for verdict={expected_verdict}"
        )
        assert "verdict" in result.data, (
            f"AC10: 'verdict' key missing from result.data for verdict={expected_verdict}"
        )
        assert result.data["verdict"] == expected_verdict, (
            f"AC10: expected result.data['verdict']={expected_verdict!r}, "
            f"got {result.data['verdict']!r}"
        )
