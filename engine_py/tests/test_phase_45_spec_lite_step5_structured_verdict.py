"""RED tests for 95D3E5F6 Step 5 — phase_45_spec_lite cycle-1 verdict from structured findings count.

Contract: GREEN will augment `_write_review_doc` in phase_45_spec_lite.py to emit
telemetry events based on the ## Findings (structured) JSON block when cycle==1.

Telemetry is ADDITIVE — markdown verdict (`_parse_verdict`) remains authoritative;
these events are observability-only until the cleanup wave (Step 8.5-equivalent).

All tests MUST FAIL until GREEN implements the contract.
Do NOT implement any contract here — RED-only file.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).parent
ENGINE_ROOT = HERE.parent
sys.path.insert(0, str(ENGINE_ROOT))

from bytedigger_engine.workflows import phase_45_spec_lite  # noqa: E402
from bytedigger_engine.workflows.phase_45_spec_lite import (  # noqa: E402
    _write_review_doc,
    VERDICT_SHIP,
    VERDICT_REVISE,
    VERDICT_UNKNOWN,
)
from bytedigger_engine.contracts import StepResult  # noqa: E402


# ─── raw markdown fixtures ─────────────────────────────────────────────────────

_VERDICT_SHIP_HEADER = "## Verdict\nSHIP\n"
_VERDICT_REVISE_HEADER = "## Verdict\nREVISE\n"
_FINDINGS_EMPTY = '## Findings (structured)\n```json\n[]\n```\n'
_FINDINGS_TWO = '## Findings (structured)\n```json\n[{"id":"1","type":"missing","evidence":"x","required_action":"y"},{"id":"2","type":"untestable","evidence":"x2","required_action":"y2"}]\n```\n'
_FINDINGS_ONE = '## Findings (structured)\n```json\n[{"id":"1","type":"missing","evidence":"x","required_action":"y"}]\n```\n'
_FINDINGS_MALFORMED = '## Findings (structured)\n```json\n{ malformed\n```\n'


# ─── helpers ──────────────────────────────────────────────────────────────────


def _make_prev(tmp_path: Path, raw: str, cycle: int = 1) -> StepResult:
    """Build a fake StepResult matching what _invoke_review_llm returns."""
    doc_path = tmp_path / "build-plan-review.md"
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    return StepResult(
        status="ok",
        data={
            "raw_response": raw,
            "doc_path": str(doc_path),
            "spec_path": str(tmp_path / "build-spec.md"),
            "cycle": cycle,
        },
        duration_ms=0,
        step_name="invoke_review_llm",
    )


def _patch_emit(monkeypatch) -> list[dict]:
    """Monkeypatch phase_45_spec_lite._emit_safe; capture all calls.

    NOTE: _emit_safe in phase_45_spec_lite has signature (event_type: str, payload: dict)
    — NO severity kwarg (unlike phase_5_implement._emit_safe which has severity).
    Lambda must match exactly: lambda et, p: ...
    """
    captured: list[dict] = []
    monkeypatch.setattr(
        phase_45_spec_lite,
        "_emit_safe",
        lambda et, p: captured.append({"type": et, "payload": p}),
    )
    return captured


# ═══════════════════════════════════════════════════════════════════════════════
# TestEmitsStructuredVerdict — ACs 1, 2, 3, 8, 9
# ═══════════════════════════════════════════════════════════════════════════════


class TestEmitsStructuredVerdict:

    def test_emits_ship_on_empty_list(self, tmp_path: Path, monkeypatch) -> None:
        """AC1: cycle-1 with empty findings list + SHIP markdown → 1 spec_lite_structured_verdict,
        structured_verdict=SHIP, n_findings=0, markdown_verdict=SHIP, cycle=1."""
        raw = _VERDICT_SHIP_HEADER + _FINDINGS_EMPTY
        prev = _make_prev(tmp_path, raw, cycle=1)
        captured = _patch_emit(monkeypatch)

        _write_review_doc(None, prev)

        events = [e for e in captured if e["type"] == "spec_lite_structured_verdict"]
        assert len(events) == 1, (
            f"expected 1 spec_lite_structured_verdict, got {len(events)}; all events: {captured}"
        )
        p = events[0]["payload"]
        assert p["structured_verdict"] == VERDICT_SHIP, f"expected SHIP, got {p['structured_verdict']}"
        assert p["n_findings"] == 0, f"expected n_findings=0, got {p['n_findings']}"
        assert p["markdown_verdict"] == VERDICT_SHIP, f"expected markdown_verdict=SHIP, got {p['markdown_verdict']}"
        assert p["cycle"] == 1, f"expected cycle=1, got {p['cycle']}"

    def test_emits_revise_on_nonempty_list(self, tmp_path: Path, monkeypatch) -> None:
        """AC2: cycle-1 with 2-item findings list + REVISE markdown → 1 spec_lite_structured_verdict,
        structured_verdict=REVISE, n_findings=2."""
        raw = _VERDICT_REVISE_HEADER + _FINDINGS_TWO
        prev = _make_prev(tmp_path, raw, cycle=1)
        captured = _patch_emit(monkeypatch)

        _write_review_doc(None, prev)

        events = [e for e in captured if e["type"] == "spec_lite_structured_verdict"]
        assert len(events) == 1, (
            f"expected 1 spec_lite_structured_verdict, got {len(events)}; all events: {captured}"
        )
        p = events[0]["payload"]
        assert p["structured_verdict"] == VERDICT_REVISE, f"expected REVISE, got {p['structured_verdict']}"
        assert p["n_findings"] == 2, f"expected n_findings=2, got {p['n_findings']}"

    def test_emits_drift_on_markdown_ship_structured_revise(self, tmp_path: Path, monkeypatch) -> None:
        """AC3: cycle-1 with markdown SHIP but 1 finding (structured=REVISE) →
        emits spec_lite_structured_verdict AND spec_lite_verdict_drift with
        markdown_verdict=SHIP, structured_verdict=REVISE, n_findings=1."""
        raw = _VERDICT_SHIP_HEADER + _FINDINGS_ONE
        prev = _make_prev(tmp_path, raw, cycle=1)
        captured = _patch_emit(monkeypatch)

        _write_review_doc(None, prev)

        verdict_events = [e for e in captured if e["type"] == "spec_lite_structured_verdict"]
        drift_events = [e for e in captured if e["type"] == "spec_lite_verdict_drift"]

        assert len(verdict_events) == 1, (
            f"expected 1 spec_lite_structured_verdict, got {len(verdict_events)}; all events: {captured}"
        )
        assert len(drift_events) == 1, (
            f"expected 1 spec_lite_verdict_drift, got {len(drift_events)}; all events: {captured}"
        )
        dp = drift_events[0]["payload"]
        assert dp["markdown_verdict"] == VERDICT_SHIP, f"expected markdown_verdict=SHIP, got {dp['markdown_verdict']}"
        assert dp["structured_verdict"] == VERDICT_REVISE, f"expected structured_verdict=REVISE, got {dp['structured_verdict']}"
        assert dp["n_findings"] == 1, f"expected n_findings=1, got {dp['n_findings']}"

    def test_no_drift_when_markdown_revise_structured_revise(self, tmp_path: Path, monkeypatch) -> None:
        """AC8: cycle-1 with markdown REVISE + non-empty findings → spec_lite_structured_verdict emits,
        ZERO drift events."""
        raw = _VERDICT_REVISE_HEADER + _FINDINGS_ONE
        prev = _make_prev(tmp_path, raw, cycle=1)
        captured = _patch_emit(monkeypatch)

        _write_review_doc(None, prev)

        verdict_events = [e for e in captured if e["type"] == "spec_lite_structured_verdict"]
        drift_events = [e for e in captured if e["type"] == "spec_lite_verdict_drift"]

        assert len(verdict_events) == 1, (
            f"expected 1 spec_lite_structured_verdict, got {len(verdict_events)}; all events: {captured}"
        )
        assert len(drift_events) == 0, (
            f"expected 0 drift events when verdicts agree (both REVISE), got {len(drift_events)}; all events: {captured}"
        )

    def test_no_drift_when_markdown_unknown(self, tmp_path: Path, monkeypatch) -> None:
        """AC9: cycle-1 with no ## Verdict header (markdown_verdict=UNKNOWN) + empty findings →
        spec_lite_structured_verdict emits, ZERO drift events (UNKNOWN does not participate in drift)."""
        # No verdict header at all → _parse_verdict returns UNKNOWN
        raw = _FINDINGS_EMPTY
        prev = _make_prev(tmp_path, raw, cycle=1)
        captured = _patch_emit(monkeypatch)

        _write_review_doc(None, prev)

        verdict_events = [e for e in captured if e["type"] == "spec_lite_structured_verdict"]
        drift_events = [e for e in captured if e["type"] == "spec_lite_verdict_drift"]

        assert len(verdict_events) == 1, (
            f"expected 1 spec_lite_structured_verdict, got {len(verdict_events)}; all events: {captured}"
        )
        p = verdict_events[0]["payload"]
        assert p["markdown_verdict"] == VERDICT_UNKNOWN, (
            f"expected markdown_verdict=UNKNOWN, got {p['markdown_verdict']}"
        )
        assert len(drift_events) == 0, (
            f"expected 0 drift events when markdown=UNKNOWN, got {len(drift_events)}; all events: {captured}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# TestEmitsBlockAbsent — ACs 4, 5
# ═══════════════════════════════════════════════════════════════════════════════


class TestEmitsBlockAbsent:

    def test_emits_block_absent_when_missing(self, tmp_path: Path, monkeypatch) -> None:
        """AC4: cycle-1 raw with no structured block at all → exactly 1 spec_lite_findings_block_absent,
        zero spec_lite_structured_verdict events, zero drift events."""
        raw = _VERDICT_SHIP_HEADER  # no structured findings block
        prev = _make_prev(tmp_path, raw, cycle=1)
        captured = _patch_emit(monkeypatch)

        _write_review_doc(None, prev)

        absent_events = [e for e in captured if e["type"] == "spec_lite_findings_block_absent"]
        verdict_events = [e for e in captured if e["type"] == "spec_lite_structured_verdict"]
        drift_events = [e for e in captured if e["type"] == "spec_lite_verdict_drift"]

        assert len(absent_events) == 1, (
            f"expected 1 spec_lite_findings_block_absent, got {len(absent_events)}; all events: {captured}"
        )
        assert len(verdict_events) == 0, (
            f"expected 0 spec_lite_structured_verdict, got {len(verdict_events)}; all events: {captured}"
        )
        assert len(drift_events) == 0, (
            f"expected 0 drift events, got {len(drift_events)}; all events: {captured}"
        )

    def test_emits_block_absent_when_malformed_json(self, tmp_path: Path, monkeypatch) -> None:
        """AC5: cycle-1 raw with ## Findings (structured) heading but malformed JSON →
        extract_structured_findings returns None → exactly 1 spec_lite_findings_block_absent."""
        raw = _VERDICT_REVISE_HEADER + _FINDINGS_MALFORMED
        prev = _make_prev(tmp_path, raw, cycle=1)
        captured = _patch_emit(monkeypatch)

        _write_review_doc(None, prev)

        absent_events = [e for e in captured if e["type"] == "spec_lite_findings_block_absent"]
        verdict_events = [e for e in captured if e["type"] == "spec_lite_structured_verdict"]

        assert len(absent_events) == 1, (
            f"expected 1 spec_lite_findings_block_absent on malformed JSON, got {len(absent_events)}; all events: {captured}"
        )
        assert len(verdict_events) == 0, (
            f"expected 0 spec_lite_structured_verdict on malformed JSON, got {len(verdict_events)}; all events: {captured}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# TestCycle1VerdictFromStructuredFindings — AC 6 (post-9DE0330B: structured findings count is authoritative on cycle 1)
# ═══════════════════════════════════════════════════════════════════════════════


class TestCycle1VerdictFromStructuredFindings:

    def test_returned_verdict_from_structured_findings(self, tmp_path: Path, monkeypatch) -> None:
        """AC6 (post-9DE0330B G1 band-aid #1): on cycle 1, when a ## Findings (structured) block is present,
        _write_review_doc's returned data["verdict"] is computed from the findings count (SHIP iff 0 findings),
        NOT from the markdown ## Verdict header. Test: markdown=SHIP but structured findings has 1 item →
        returned verdict=REVISE (structured wins)."""
        raw = _VERDICT_SHIP_HEADER + _FINDINGS_ONE
        prev = _make_prev(tmp_path, raw, cycle=1)
        captured = _patch_emit(monkeypatch)

        result = _write_review_doc(None, prev)

        assert result.status == "ok", f"expected ok, got {result.status}: {result.error}"
        assert result.data is not None, "expected result.data, got None"
        returned_verdict = result.data.get("verdict")
        assert returned_verdict == VERDICT_REVISE, (
            "expected structured-authoritative verdict=REVISE (1 finding), got {!r}. "
            "On cycle 1 the findings count drives the verdict, not the markdown header.".format(returned_verdict)
        )


# ═══════════════════════════════════════════════════════════════════════════════
# TestCycle2Unchanged — AC 7
# ═══════════════════════════════════════════════════════════════════════════════


class TestCycle2Unchanged:

    def test_cycle2_emits_no_step5_events(self, tmp_path: Path, monkeypatch) -> None:
        """AC7: when cycle >= 2, none of the new Step 5 events emit.
        Cycle-2 path uses parse_per_finding_verdicts (W1 restricted-mode) which
        is out-of-scope for Step 5. Free-form cycle-2 text (no FINDING_N: lines)
        will produce UNPARSED → fall through to classic ## Verdict parser, but
        the Step 5 telemetry block must NOT execute for cycle >= 2."""
        # Free-form reviewer output without FINDING_N: PASS|REVISE lines →
        # parse_per_finding_verdicts returns UNPARSED → falls through to _parse_verdict.
        raw = _VERDICT_SHIP_HEADER + _FINDINGS_EMPTY  # has structured block, but cycle=2
        prev = _make_prev(tmp_path, raw, cycle=2)
        captured = _patch_emit(monkeypatch)

        _write_review_doc(None, prev)

        step5_events = [
            e for e in captured
            if e["type"] in (
                "spec_lite_structured_verdict",
                "spec_lite_verdict_drift",
                "spec_lite_findings_block_absent",
            )
        ]
        assert len(step5_events) == 0, (
            f"expected 0 Step 5 events on cycle=2, got {len(step5_events)}; all events: {captured}"
        )
