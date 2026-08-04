"""RED tests for 95D3E5F6 Step 4 — phase_5 validation gate structured JSON verdict + schema enforce.

Contract: GREEN will add `_parse_validation_structured` helper and modify
`_write_validation_doc` to emit telemetry events based on structured JSON block.

All tests MUST FAIL until GREEN implements the contract.
Do NOT implement any contract here — RED-only file.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).parent
ENGINE_ROOT = HERE.parent
sys.path.insert(0, str(ENGINE_ROOT))

from bytedigger_engine.workflows import phase_5_implement  # noqa: E402
from bytedigger_engine.workflows.phase_5_implement import _parse_validation_structured, _write_validation_doc  # noqa: E402
from bytedigger_engine.contracts import StepResult  # noqa: E402


# ─── raw markdown builders ────────────────────────────────────────────────────

_VALID_BLOCK = """\
## validation-output (structured)
```json
{"approve": true, "reject_reason": null}
```"""

_VALID_BLOCK_REJECT = """\
## validation-output (structured)
```json
{"approve": false, "reject_reason": "missing test coverage for edge case"}
```"""

_BLOCK_MISSING_REJECT_REASON = """\
## validation-output (structured)
```json
{"approve": true}
```"""

_BLOCK_EXTRA_FIELD = """\
## validation-output (structured)
```json
{"approve": true, "reject_reason": null, "unknown_key": "surprise"}
```"""

_BLOCK_MALFORMED_JSON = """\
## validation-output (structured)
```json
{ malformed
```"""

_VERDICT_PASS = "## Verdict\nVerdict: PASS\n"
_VERDICT_FAIL = "## Verdict\nVerdict: FAIL\n"


def _raw(verdict: str, block: str | None) -> str:
    parts = [verdict]
    if block is not None:
        parts.append(block)
    return "\n".join(parts)


def _make_prev(tmp_path: Path, raw: str) -> StepResult:
    doc_path = tmp_path / "validation.md"
    return StepResult(
        status="ok",
        data={
            "raw_response": raw,
            "doc_path": str(doc_path),
            "spec_path": str(tmp_path / "spec.md"),
            "red_log_path": str(tmp_path / "red.log"),
            "red_test_paths": [],
        },
        duration_ms=0,
        step_name="invoke_validation_llm",
    )


def _patch_emit(monkeypatch) -> list[dict]:
    captured: list[dict] = []
    monkeypatch.setattr(
        phase_5_implement,
        "_emit_safe",
        lambda et, p, severity="warning": captured.append(
            {"type": et, "payload": p, "severity": severity}
        ),
    )
    return captured


# ═══════════════════════════════════════════════════════════════════════════════
# TestParseValidationStructured
# ═══════════════════════════════════════════════════════════════════════════════


class TestParseValidationStructured:

    def test_parse_returns_validation_verdict_on_valid_block(self) -> None:
        """Valid structured block → (ValidationVerdict(approve=True, reject_reason=None), None)."""
        raw = _raw(_VERDICT_PASS, _VALID_BLOCK)
        verdict, reason = _parse_validation_structured(raw)

        assert verdict is not None, "Expected ValidationVerdict instance, got None"
        assert verdict.approve is True
        assert verdict.reject_reason is None
        assert reason is None

    def test_parse_returns_none_absent_when_no_block(self) -> None:
        """Raw WITHOUT structured block → (None, 'absent')."""
        raw = _VERDICT_PASS  # no structured block at all
        verdict, reason = _parse_validation_structured(raw)

        assert verdict is None
        assert reason == "absent"

    def test_parse_returns_none_schema_violation_on_missing_field(self) -> None:
        """Structured block missing reject_reason → (None, 'schema_violation: ...')."""
        raw = _raw(_VERDICT_PASS, _BLOCK_MISSING_REJECT_REASON)
        verdict, reason = _parse_validation_structured(raw)

        assert verdict is None
        assert reason is not None
        assert "schema_violation" in reason
        assert "reject_reason" in reason

    def test_parse_returns_none_json_error_on_malformed(self) -> None:
        """Malformed JSON in structured block → (None, 'json_error')."""
        raw = _raw(_VERDICT_PASS, _BLOCK_MALFORMED_JSON)
        verdict, reason = _parse_validation_structured(raw)

        assert verdict is None
        assert reason == "json_error"

    def test_parse_handles_extra_field_strictly(self) -> None:
        """Valid fields + extra unknown_key → (None, 'schema_violation: ...')."""
        raw = _raw(_VERDICT_PASS, _BLOCK_EXTRA_FIELD)
        verdict, reason = _parse_validation_structured(raw)

        assert verdict is None
        assert reason is not None
        assert "schema_violation" in reason
        assert "unknown_key" in reason


# ═══════════════════════════════════════════════════════════════════════════════
# TestWriteValidationDocTelemetry
# ═══════════════════════════════════════════════════════════════════════════════


class TestWriteValidationDocTelemetry:

    def test_write_emits_validation_structured_ok_event_on_valid_block(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Valid structured block (approve=True) + PASS markdown → 1 validation_structured_ok event."""
        raw = _raw(_VERDICT_PASS, _VALID_BLOCK)
        prev = _make_prev(tmp_path, raw)
        captured = _patch_emit(monkeypatch)

        _write_validation_doc(None, prev)

        ok_events = [e for e in captured if e["type"] == "validation_structured_ok"]
        assert len(ok_events) == 1, (
            f"expected 1 validation_structured_ok event, got {len(ok_events)}; all events: {captured}"
        )
        assert ok_events[0]["payload"]["approve"] is True
        assert ok_events[0]["payload"]["phase"] == 5

    def test_write_emits_validation_verdict_drift_event_on_mismatch(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """FAIL markdown but structured approve=true → 1 validation_verdict_drift event."""
        raw = _raw(_VERDICT_FAIL, _VALID_BLOCK)
        prev = _make_prev(tmp_path, raw)
        captured = _patch_emit(monkeypatch)

        _write_validation_doc(None, prev)

        drift_events = [e for e in captured if e["type"] == "validation_verdict_drift"]
        assert len(drift_events) == 1, (
            f"expected 1 validation_verdict_drift event, got {len(drift_events)}; all events: {captured}"
        )
        assert drift_events[0]["payload"]["markdown_verdict"] == "FAIL"
        assert drift_events[0]["payload"]["structured_approve"] is True

    def test_write_emits_validation_block_absent_event_when_no_block(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """No structured block → exactly 1 validation_block_absent event."""
        raw = _VERDICT_PASS
        prev = _make_prev(tmp_path, raw)
        captured = _patch_emit(monkeypatch)

        _write_validation_doc(None, prev)

        absent_events = [e for e in captured if e["type"] == "validation_block_absent"]
        assert len(absent_events) == 1, (
            f"expected 1 validation_block_absent event, got {len(absent_events)}; all events: {captured}"
        )
        assert absent_events[0]["payload"]["phase"] == 5

    def test_write_emits_schema_violation_event_on_bad_block(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Structured block missing required field → exactly 1 validation_schema_violation event."""
        raw = _raw(_VERDICT_PASS, _BLOCK_MISSING_REJECT_REASON)
        prev = _make_prev(tmp_path, raw)
        captured = _patch_emit(monkeypatch)

        _write_validation_doc(None, prev)

        violation_events = [e for e in captured if e["type"] == "validation_schema_violation"]
        assert len(violation_events) == 1, (
            f"expected 1 validation_schema_violation event, got {len(violation_events)}; all events: {captured}"
        )
        assert "reason" in violation_events[0]["payload"]
        assert violation_events[0]["payload"]["phase"] == 5

    def test_write_does_not_change_markdown_verdict_logic(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Structured block invalid → result.data['verdict'] still driven by markdown regex."""
        raw = _raw(_VERDICT_PASS, _BLOCK_MISSING_REJECT_REASON)
        prev = _make_prev(tmp_path, raw)
        _patch_emit(monkeypatch)

        result = _write_validation_doc(None, prev)

        assert result.data is not None
        assert result.data["verdict"] == "PASS", (
            f"Expected verdict='PASS' from markdown regex regardless of bad structured block, "
            f"got verdict={result.data.get('verdict')!r}"
        )
