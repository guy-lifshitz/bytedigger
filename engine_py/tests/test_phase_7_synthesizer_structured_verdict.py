"""RED tests for 392BDDFB — phase_7 band-aid #14: synthesizer markdown gate → structured SynthesizerVerdict.

Agreement: 392BDDFB (child of 95D3E5F6, band-aid #14 — last structured-verdict flip)
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
ENGINE_ROOT = HERE.parent
sys.path.insert(0, str(ENGINE_ROOT))

from bytedigger_engine.contracts import StepResult, WorkflowContext  # noqa: E402
from bytedigger_engine.workflows import phase_7_synthesize  # noqa: E402
from bytedigger_engine.workflows.phase_7_synthesize import (  # noqa: E402
    REPORT_DOC_RELPATH,
    SPEC_DOC_RELPATH,
    REVIEW_DOC_RELPATH,
    FIX_DOC_RELPATH,
    SATISFACTION_DOC_RELPATH,
    STATUS_DONE,
    STATUS_DONE_WITH_CONCERNS,
    STATUS_NEEDS_CONTEXT,
    STATUS_BLOCKED,
    STATUS_NO_MARKER,
    _parse_synthesizer_status,
    _write_synthesizer_artifact,
)


# ─── helpers ──────────────────────────────────────────────────────────────────


def _make_ctx(scratchpad: Path) -> WorkflowContext:
    return WorkflowContext(
        tenant_id="hal",
        scope=None,
        db_path=None,
        org_config={"scratchpad_dir": str(scratchpad)},
        question="Add foo to bar",
        session_id="test-392BDDFB",
        persona="hal",
        framework=None,
        domain=None,
    )


def _make_synth_prev(scratchpad: Path, raw: str) -> StepResult:
    """Build the prev StepResult that _write_synthesizer_artifact expects.

    Must supply doc_path, spec_path, review_doc_path, fix_doc_path,
    satisfaction_doc_path — mirrors _invoke_synthesizer_llm extra_data keys.
    """
    doc_path = scratchpad / REPORT_DOC_RELPATH
    doc_path.parent.mkdir(parents=True, exist_ok=True)

    spec_path = scratchpad / SPEC_DOC_RELPATH
    spec_path.parent.mkdir(parents=True, exist_ok=True)
    spec_path.write_text("## US1\nAdd foo\n", encoding="utf-8")

    review_doc = scratchpad / REVIEW_DOC_RELPATH
    review_doc.parent.mkdir(parents=True, exist_ok=True)
    review_doc.write_text("# Review\nVERDICT: PASS\n", encoding="utf-8")

    fix_doc = scratchpad / FIX_DOC_RELPATH
    fix_doc.parent.mkdir(parents=True, exist_ok=True)
    fix_doc.write_text("FIX SKIPPED — no findings\n", encoding="utf-8")

    sat_doc = scratchpad / SATISFACTION_DOC_RELPATH
    sat_doc.parent.mkdir(parents=True, exist_ok=True)
    sat_doc.write_text("SCORE: 95\nVERDICT: PASS\n", encoding="utf-8")

    return StepResult(
        status="ok",
        data={
            "raw_response": raw,
            "doc_path": str(doc_path),
            "spec_path": str(spec_path),
            "review_doc_path": str(review_doc),
            "fix_doc_path": str(fix_doc),
            "satisfaction_doc_path": str(sat_doc),
        },
        duration_ms=0,
        step_name="invoke_synthesizer_llm",
    )


def _patch_emit(monkeypatch) -> list[dict]:
    """Capture every _emit_safe call in phase_7_synthesize."""
    captured: list[dict] = []

    def _capture(event_type: str, payload: dict) -> None:
        captured.append({"type": event_type, "payload": payload})

    monkeypatch.setattr(phase_7_synthesize, "_emit_safe", _capture)
    return captured


def _no_op_disk_truth(monkeypatch) -> None:
    """Stub _emit_synthesize_disk_truth_telemetry — avoids needing a real git repo."""
    monkeypatch.setattr(
        phase_7_synthesize,
        "_emit_synthesize_disk_truth_telemetry",
        lambda *a, **k: None,
        raising=False,
    )


# ─── AC1 — SynthesizerVerdict dataclass importable from plugins.disk_truth ────


def test_ac1_synthesizer_verdict_importable_and_has_correct_fields():
    """AC1: SynthesizerVerdict exists as a dataclass with synthesized: bool,
    needs_context: bool, concerns: List[dict]; importable via
    `from bytedigger_engine.lib.plugins.disk_truth import SynthesizerVerdict`.

    FAILS today: SynthesizerVerdict does not exist in schema.py / __init__.py.
    """
    import dataclasses
    from bytedigger_engine.lib.plugins.disk_truth import SynthesizerVerdict  # type: ignore[attr-defined]

    assert dataclasses.is_dataclass(SynthesizerVerdict), "SynthesizerVerdict must be a dataclass"
    fields = {f.name: f for f in dataclasses.fields(SynthesizerVerdict)}
    assert "synthesized" in fields, "SynthesizerVerdict missing field 'synthesized'"
    assert "needs_context" in fields, "SynthesizerVerdict missing field 'needs_context'"
    assert "concerns" in fields, "SynthesizerVerdict missing field 'concerns'"
    # Verify construction with valid values
    v = SynthesizerVerdict(synthesized=True, needs_context=False, concerns=[])
    assert v.synthesized is True
    assert v.needs_context is False
    assert v.concerns == []


# ─── AC2 — enforce strict validation for SynthesizerVerdict ───────────────────


def test_ac2_enforce_synthesizer_verdict_happy_path():
    """AC2: enforce({"synthesized": True, "needs_context": False, "concerns": []}, SynthesizerVerdict)
    returns SynthesizerVerdict(True, False, []).

    FAILS today: SynthesizerVerdict not yet defined.
    """
    from bytedigger_engine.lib.plugins.disk_truth import SynthesizerVerdict, enforce  # type: ignore[attr-defined]

    result = enforce({"synthesized": True, "needs_context": False, "concerns": []}, SynthesizerVerdict)
    assert isinstance(result, SynthesizerVerdict)
    assert result.synthesized is True
    assert result.needs_context is False
    assert result.concerns == []


def test_ac2_enforce_synthesizer_verdict_strict_bool_rejects_int():
    """AC2 (strict bool): enforce({"synthesized": 1, ...}, SynthesizerVerdict)
    raises SchemaViolation — int is not bool.

    FAILS today: SynthesizerVerdict not yet defined.
    """
    from bytedigger_engine.lib.plugins.disk_truth import SynthesizerVerdict, enforce, SchemaViolation  # type: ignore[attr-defined]

    with pytest.raises(SchemaViolation):
        enforce({"synthesized": 1, "needs_context": False, "concerns": []}, SynthesizerVerdict)


def test_ac2_enforce_synthesizer_verdict_missing_concerns_raises():
    """AC2 (missing required field): enforce({"synthesized": True, "needs_context": False}, SynthesizerVerdict)
    raises SchemaViolation — 'concerns' is required.

    FAILS today: SynthesizerVerdict not yet defined.
    """
    from bytedigger_engine.lib.plugins.disk_truth import SynthesizerVerdict, enforce, SchemaViolation  # type: ignore[attr-defined]

    with pytest.raises(SchemaViolation):
        enforce({"synthesized": True, "needs_context": False}, SynthesizerVerdict)


def test_ac2_enforce_synthesizer_verdict_unknown_field_raises():
    """AC2 (unknown field): enforce({..., "extra": 1}, SynthesizerVerdict)
    raises SchemaViolation.

    FAILS today: SynthesizerVerdict not yet defined.
    """
    from bytedigger_engine.lib.plugins.disk_truth import SynthesizerVerdict, enforce, SchemaViolation  # type: ignore[attr-defined]

    with pytest.raises(SchemaViolation):
        enforce(
            {"synthesized": True, "needs_context": False, "concerns": [], "extra": 1},
            SynthesizerVerdict,
        )


# ─── AC3 — _parse_synthesizer_structured happy path ──────────────────────────


def test_ac3_parse_synthesizer_structured_happy_path():
    """AC3: valid structured block → (SynthesizerVerdict(True, False, []), None).

    FAILS today: _parse_synthesizer_structured does not exist in phase_7_synthesize.
    """
    from bytedigger_engine.workflows.phase_7_synthesize import _parse_synthesizer_structured  # type: ignore[attr-defined]
    from bytedigger_engine.lib.plugins.disk_truth import SynthesizerVerdict  # type: ignore[attr-defined]

    raw = (
        "STATUS: DONE\n"
        "\n"
        "## synthesizer-output (structured)\n"
        "```json\n"
        '{"synthesized": true, "needs_context": false, "concerns": []}\n'
        "```\n"
    )
    verdict, reason = _parse_synthesizer_structured(raw)
    assert reason is None, f"expected reason=None, got {reason!r}"
    assert isinstance(verdict, SynthesizerVerdict), f"expected SynthesizerVerdict, got {type(verdict)}"
    assert verdict.synthesized is True
    assert verdict.needs_context is False
    assert verdict.concerns == []


# ─── AC4 — _parse_synthesizer_structured error branches ───────────────────────


def test_ac4_parse_synthesizer_structured_no_block_returns_absent():
    """AC4: no structured block → (None, "absent").

    FAILS today: _parse_synthesizer_structured does not exist.
    """
    from bytedigger_engine.workflows.phase_7_synthesize import _parse_synthesizer_structured  # type: ignore[attr-defined]

    verdict, reason = _parse_synthesizer_structured("STATUS: DONE")
    assert verdict is None
    assert reason == "absent"


def test_ac4_parse_synthesizer_structured_empty_returns_absent():
    """AC4: empty string → (None, "absent").

    FAILS today: _parse_synthesizer_structured does not exist.
    """
    from bytedigger_engine.workflows.phase_7_synthesize import _parse_synthesizer_structured  # type: ignore[attr-defined]

    verdict, reason = _parse_synthesizer_structured("")
    assert verdict is None
    assert reason == "absent"


def test_ac4_parse_synthesizer_structured_malformed_json_returns_malformed():
    """AC4: header present but JSON unparseable → (None, "malformed").

    FAILS today: _parse_synthesizer_structured does not exist.
    """
    from bytedigger_engine.workflows.phase_7_synthesize import _parse_synthesizer_structured  # type: ignore[attr-defined]

    raw = "## synthesizer-output (structured)\n```json\n{not json}\n```\n"
    verdict, reason = _parse_synthesizer_structured(raw)
    assert verdict is None
    assert reason == "malformed"


def test_ac4_parse_synthesizer_structured_schema_violation_returns_schema_violation():
    """AC4: header + valid JSON but missing 'needs_context' and 'concerns' fields →
    (None, "schema_violation: ...").

    FAILS today: _parse_synthesizer_structured does not exist.
    """
    from bytedigger_engine.workflows.phase_7_synthesize import _parse_synthesizer_structured  # type: ignore[attr-defined]

    raw = (
        "## synthesizer-output (structured)\n"
        "```json\n"
        '{"synthesized": true}\n'
        "```\n"
    )
    verdict, reason = _parse_synthesizer_structured(raw)
    assert verdict is None
    assert reason is not None and reason.startswith("schema_violation:"), (
        f"expected reason starting with 'schema_violation:', got {reason!r}"
    )


# ─── AC5 — structured synthesized=true → ok + synth_structured_ok event ──────


def test_ac5_structured_synthesized_true_returns_ok_with_event(tmp_path, monkeypatch):
    """AC5: raw emits STATUS: DONE AND structured block with synthesized=true,
    needs_context=false → _write_synthesizer_artifact returns status='ok';
    result.data['structured_verdict'] is a SynthesizerVerdict with synthesized=True;
    a 'synth_structured_ok' event is emitted with {synthesized: True, needs_context: False, phase: 7}.
    The report doc is written to doc_path.

    FAILS today: structured parsing not implemented; 'structured_verdict' key absent;
    no 'synth_structured_ok' event emitted.
    """
    from bytedigger_engine.lib.plugins.disk_truth import SynthesizerVerdict  # type: ignore[attr-defined]

    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()

    captured = _patch_emit(monkeypatch)
    _no_op_disk_truth(monkeypatch)

    raw = (
        "# Post-Deploy Report\n\n"
        "All good. Files: foo.py\n\n"
        "STATUS: DONE\n"
        "\n"
        "## synthesizer-output (structured)\n"
        "```json\n"
        '{"synthesized": true, "needs_context": false, "concerns": []}\n'
        "```\n"
    )
    prev = _make_synth_prev(scratchpad, raw)
    ctx = _make_ctx(scratchpad)

    result = _write_synthesizer_artifact(ctx, prev)

    assert result.status == "ok", (
        f"expected status='ok', got {result.status!r} ({getattr(result, 'error_code', None)!r})"
    )
    assert isinstance(result.data, dict)
    assert "structured_verdict" in result.data, (
        "'structured_verdict' key missing from result.data"
    )
    sv = result.data["structured_verdict"]
    assert isinstance(sv, SynthesizerVerdict), (
        f"expected SynthesizerVerdict, got {type(sv)}"
    )
    assert sv.synthesized is True

    # Report doc written
    doc_path = Path(prev.data["doc_path"])
    assert doc_path.exists(), "report doc must be written to doc_path"

    ok_events = [e for e in captured if e["type"] == "synth_structured_ok"]
    assert len(ok_events) == 1, (
        f"expected exactly 1 'synth_structured_ok' event, got {len(ok_events)}: {ok_events}"
    )
    payload = ok_events[0]["payload"]
    assert payload.get("synthesized") is True, (
        f"synth_structured_ok payload synthesized should be True, got {payload}"
    )
    assert payload.get("needs_context") is False, (
        f"synth_structured_ok payload needs_context should be False, got {payload}"
    )
    assert payload.get("phase") == 7, (
        f"synth_structured_ok payload phase should be 7, got {payload}"
    )


# ─── AC6 — structured synthesized=false, needs_context=true beats lying marker ─


def test_ac6_structured_needs_context_true_overrides_done_marker(tmp_path, monkeypatch):
    """AC6: raw emits STATUS: DONE (lying) BUT structured block has
    synthesized=false, needs_context=true → _write_synthesizer_artifact returns
    status='error', error_code='E_SYNTHESIZER_NEEDS_CONTEXT'; AND a
    'synth_verdict_drift' event is emitted with marker_status='DONE',
    structured_synthesized=False. result.data['structured_verdict'].synthesized is False.

    FAILS today: structured gate not implemented; lying DONE marker passes through.
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()

    captured = _patch_emit(monkeypatch)
    _no_op_disk_truth(monkeypatch)

    raw = (
        "# Post-Deploy Report\n\n"
        "Could not determine outcome.\n\n"
        "STATUS: DONE\n"
        "\n"
        "## synthesizer-output (structured)\n"
        "```json\n"
        '{"synthesized": false, "needs_context": true, "concerns": []}\n'
        "```\n"
    )
    prev = _make_synth_prev(scratchpad, raw)
    ctx = _make_ctx(scratchpad)

    result = _write_synthesizer_artifact(ctx, prev)

    assert result.status == "error", (
        f"expected status='error' (structured wins over lying marker), got {result.status!r}"
    )
    assert result.error_code == "E_SYNTHESIZER_NEEDS_CONTEXT", (
        f"expected error_code='E_SYNTHESIZER_NEEDS_CONTEXT', got {result.error_code!r}"
    )

    drift_events = [e for e in captured if e["type"] == "synth_verdict_drift"]
    assert len(drift_events) == 1, (
        f"expected exactly 1 'synth_verdict_drift' event, got {len(drift_events)}: {drift_events}"
    )
    drift_payload = drift_events[0]["payload"]
    assert drift_payload.get("marker_status") == STATUS_DONE, (
        f"synth_verdict_drift marker_status should be STATUS_DONE ('{STATUS_DONE}'), "
        f"got {drift_payload.get('marker_status')!r}"
    )
    assert drift_payload.get("structured_synthesized") is False, (
        f"synth_verdict_drift structured_synthesized should be False, got {drift_payload}"
    )
    assert drift_payload.get("phase") == 7

    assert isinstance(result.data, dict)
    sv = result.data.get("structured_verdict")
    assert sv is not None and sv.synthesized is False, (
        f"expected structured_verdict with synthesized=False, got {sv!r}"
    )


# ─── AC7 — structured synthesized=false, needs_context=false → E_SYNTHESIZER_BLOCKED ─


def test_ac7_structured_blocked_returns_error_blocked(tmp_path, monkeypatch):
    """AC7: structured block with synthesized=false, needs_context=false
    (regardless of STATUS marker) → status='error', error_code='E_SYNTHESIZER_BLOCKED',
    recoverable=False.

    FAILS today: structured gate not implemented.
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()

    captured = _patch_emit(monkeypatch)
    _no_op_disk_truth(monkeypatch)

    raw = (
        "# Post-Deploy Report\n\n"
        "Cannot synthesize — dependency loop.\n\n"
        "STATUS: BLOCKED\n"
        "\n"
        "## synthesizer-output (structured)\n"
        "```json\n"
        '{"synthesized": false, "needs_context": false, "concerns": []}\n'
        "```\n"
    )
    prev = _make_synth_prev(scratchpad, raw)
    ctx = _make_ctx(scratchpad)

    result = _write_synthesizer_artifact(ctx, prev)

    assert result.status == "error", (
        f"expected status='error', got {result.status!r}"
    )
    assert result.error_code == "E_SYNTHESIZER_BLOCKED", (
        f"expected error_code='E_SYNTHESIZER_BLOCKED', got {result.error_code!r}"
    )
    assert result.recoverable is False, (
        "E_SYNTHESIZER_BLOCKED must have recoverable=False"
    )


# ─── AC8 — backward-compat: STATUS: DONE only (no block) → ok + missing event ─


def test_ac8_no_structured_block_status_done_ok_with_missing_event(tmp_path, monkeypatch):
    """AC8: raw emits ONLY STATUS: DONE (no structured block) →
    _write_synthesizer_artifact returns status='ok';
    result.data['structured_verdict'] is None;
    a 'synth_structured_missing' event with {reason: "absent", phase: 7} is emitted.

    FAILS today: 'structured_verdict' key absent; 'synth_structured_missing' not emitted.
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()

    captured = _patch_emit(monkeypatch)
    _no_op_disk_truth(monkeypatch)

    raw = "# Post-Deploy Report\n\nAll done.\n\nSTATUS: DONE\n"
    prev = _make_synth_prev(scratchpad, raw)
    ctx = _make_ctx(scratchpad)

    result = _write_synthesizer_artifact(ctx, prev)

    assert result.status == "ok", (
        f"expected status='ok' (legacy marker path), got {result.status!r} ({getattr(result, 'error_code', None)!r})"
    )
    assert isinstance(result.data, dict)
    assert "structured_verdict" in result.data, (
        "'structured_verdict' key missing from result.data (AC8)"
    )
    assert result.data["structured_verdict"] is None, (
        f"expected structured_verdict=None when no block, got {result.data['structured_verdict']!r}"
    )

    missing_events = [e for e in captured if e["type"] == "synth_structured_missing"]
    assert len(missing_events) == 1, (
        f"expected exactly 1 'synth_structured_missing' event, got {len(missing_events)}: {missing_events}"
    )
    payload = missing_events[0]["payload"]
    assert payload.get("reason") == "absent", (
        f"synth_structured_missing reason should be 'absent', got {payload.get('reason')!r}"
    )
    assert payload.get("phase") == 7, (
        f"synth_structured_missing phase should be 7, got {payload}"
    )


# ─── AC9 — backward-compat: legacy marker-only error paths unchanged ──────────


def test_ac9_legacy_blocked_marker_only_returns_error(tmp_path, monkeypatch):
    """AC9: ONLY STATUS: BLOCKED (no structured block) →
    status='error', error_code='E_SYNTHESIZER_BLOCKED', recoverable=False;
    result.data['structured_verdict'] is None.

    FAILS today: 'structured_verdict' key absent from common_data.
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()

    _patch_emit(monkeypatch)
    _no_op_disk_truth(monkeypatch)

    raw = "Synthesis failed. Cannot proceed.\nSTATUS: BLOCKED\n"
    prev = _make_synth_prev(scratchpad, raw)
    ctx = _make_ctx(scratchpad)

    result = _write_synthesizer_artifact(ctx, prev)

    assert result.status == "error"
    assert result.error_code == "E_SYNTHESIZER_BLOCKED"
    assert result.recoverable is False
    assert isinstance(result.data, dict)
    assert "structured_verdict" in result.data, (
        "'structured_verdict' key missing from legacy BLOCKED common_data"
    )
    assert result.data["structured_verdict"] is None


def test_ac9_legacy_needs_context_marker_only_returns_error(tmp_path, monkeypatch):
    """AC9: ONLY STATUS: NEEDS_CONTEXT (no structured block) →
    error_code='E_SYNTHESIZER_NEEDS_CONTEXT'; result.data['structured_verdict'] is None.

    FAILS today: 'structured_verdict' key absent from common_data.
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()

    _patch_emit(monkeypatch)
    _no_op_disk_truth(monkeypatch)

    raw = "Need more context to proceed.\nSTATUS: NEEDS_CONTEXT\n"
    prev = _make_synth_prev(scratchpad, raw)
    ctx = _make_ctx(scratchpad)

    result = _write_synthesizer_artifact(ctx, prev)

    assert result.status == "error"
    assert result.error_code == "E_SYNTHESIZER_NEEDS_CONTEXT"
    assert isinstance(result.data, dict)
    assert "structured_verdict" in result.data, (
        "'structured_verdict' key missing from legacy NEEDS_CONTEXT common_data"
    )
    assert result.data["structured_verdict"] is None


def test_ac9_legacy_no_marker_returns_error(tmp_path, monkeypatch):
    """AC9: no STATUS marker, no block → error_code='E_SYNTHESIZER_NO_MARKER';
    result.data['structured_verdict'] is None.

    FAILS today: 'structured_verdict' key absent from common_data.
    """
    scratchpad = tmp_path / "scratch"
    scratchpad.mkdir()

    _patch_emit(monkeypatch)
    _no_op_disk_truth(monkeypatch)

    raw = "Some text with no status marker whatsoever.\n"
    prev = _make_synth_prev(scratchpad, raw)
    ctx = _make_ctx(scratchpad)

    result = _write_synthesizer_artifact(ctx, prev)

    assert result.status == "error"
    assert result.error_code == "E_SYNTHESIZER_NO_MARKER"
    assert isinstance(result.data, dict)
    assert "structured_verdict" in result.data, (
        "'structured_verdict' key missing from legacy NO_MARKER common_data"
    )
    assert result.data["structured_verdict"] is None


# ─── AC10 — regression guard: existing _parse_synthesizer_status unchanged ────


def test_ac10_parse_synthesizer_status_regression_guard():
    """AC10: _parse_synthesizer_status still returns correct STATUS_* constants.

    This exercises existing behavior and should PASS today AND after GREEN.
    Guards that the #14 changes do not regress the legacy marker parser.
    """
    assert _parse_synthesizer_status("STATUS: DONE\n") == STATUS_DONE
    assert _parse_synthesizer_status("STATUS: DONE_WITH_CONCERNS\n") == STATUS_DONE_WITH_CONCERNS
    assert _parse_synthesizer_status("STATUS: NEEDS_CONTEXT\n") == STATUS_NEEDS_CONTEXT
    assert _parse_synthesizer_status("STATUS: BLOCKED\n") == STATUS_BLOCKED
    assert _parse_synthesizer_status("no marker here") == STATUS_NO_MARKER
    # Last-marker-wins
    assert _parse_synthesizer_status("STATUS: DONE\nSTATUS: BLOCKED") == STATUS_BLOCKED
    assert _parse_synthesizer_status("STATUS: BLOCKED\nSTATUS: DONE") == STATUS_DONE
