"""RED tests for 9DE0330B — phase_45_spec_lite cycle-1 verdict computed from structured findings count.

Agreement: 9DE0330B-6BDB-4036-A629-EC387F5D8561 (child of 95D3E5F6 — disk-truth UMBRELLA, G1 band-aid #1)
Contract: GREEN will modify _write_review_doc in phase_45_spec_lite.py so that on cycle<=1,
when extract_structured_findings(raw) returns a non-None list, the final verdict placed in
StepResult.data["verdict"] becomes VERDICT_SHIP if len(findings)==0 else VERDICT_REVISE
(the structured/disk-truth value) instead of _parse_verdict(raw) (the markdown-prose regex).
When the structured block is absent or malformed (extract_structured_findings returns None)
→ falls back to _parse_verdict(raw) exactly as today. On cycle>=2 → unchanged.

AC1, AC2, AC7 FAIL pre-fix. AC3, AC4, AC5, AC6 are regression guards that PASS pre+post.
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
    _gate_on_review,
    _parse_verdict,
    VERDICT_SHIP,
    VERDICT_REVISE,
    VERDICT_UNKNOWN,
    MAX_REVIEW_CYCLES,
)
from bytedigger_engine.contracts import StepResult  # noqa: E402


# ─── raw markdown fixtures ─────────────────────────────────────────────────────

_VERDICT_SHIP_HEADER = "## Verdict\nSHIP\n"
_VERDICT_REVISE_HEADER = "## Verdict\nREVISE\n"
_FINDINGS_EMPTY = '## Findings (structured)\n```json\n[]\n```\n'
_FINDINGS_ONE = '## Findings (structured)\n```json\n[{"id":"1","type":"missing","evidence":"x","required_action":"y"}]\n```\n'
_FINDINGS_TWO = '## Findings (structured)\n```json\n[{"id":"1","type":"missing","evidence":"x","required_action":"y"},{"id":"2","type":"untestable","evidence":"x2","required_action":"y2"}]\n```\n'
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
# TestCycle1StructuredVerdictOverride — AC1, AC2 (FAIL pre-fix)
# ═══════════════════════════════════════════════════════════════════════════════


class TestCycle1StructuredVerdictOverride:

    def test_cycle1_structured_findings_override_markdown_ship(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """AC1 (core proof, FAIL pre-fix): cycle=1, markdown says SHIP, structured has 1 finding
        → structured wins → verdict == VERDICT_REVISE."""
        raw = _VERDICT_SHIP_HEADER + _FINDINGS_ONE
        _patch_emit(monkeypatch)
        r = _write_review_doc(None, _make_prev(tmp_path, raw, cycle=1))

        assert r.status == "ok", f"expected status ok, got {r.status}: {r.error}"
        assert r.data is not None, "expected result.data to be a dict, got None"
        assert r.data["verdict"] == VERDICT_REVISE, (
            f"AC1 FAIL: markdown=SHIP but structured has 1 finding → expected VERDICT_REVISE, "
            f"got {r.data['verdict']!r}. Pre-fix: _parse_verdict returns SHIP (markdown wins). "
            f"Post-fix: structured block wins when present."
        )

    def test_cycle1_structured_empty_overrides_markdown_revise(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """AC2 (inverse, FAIL pre-fix): cycle=1, markdown says REVISE, structured findings=[]
        → structured wins → verdict == VERDICT_SHIP."""
        raw = _VERDICT_REVISE_HEADER + _FINDINGS_EMPTY
        _patch_emit(monkeypatch)
        r = _write_review_doc(None, _make_prev(tmp_path, raw, cycle=1))

        assert r.status == "ok", f"expected status ok, got {r.status}: {r.error}"
        assert r.data is not None, "expected result.data to be a dict, got None"
        assert r.data["verdict"] == VERDICT_SHIP, (
            f"AC2 FAIL: markdown=REVISE but structured findings=[] → expected VERDICT_SHIP, "
            f"got {r.data['verdict']!r}. Pre-fix: _parse_verdict returns REVISE (markdown wins)."
        )


# ═══════════════════════════════════════════════════════════════════════════════
# TestCycle1BackwardCompat — AC3 (regression guard, PASS pre+post)
# ═══════════════════════════════════════════════════════════════════════════════


class TestCycle1BackwardCompat:

    def test_cycle1_no_structured_block_falls_back_to_markdown(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """AC3 (regression guard, PASS pre+post): when no structured block is present,
        _parse_verdict (markdown regex) remains the verdict source."""
        _patch_emit(monkeypatch)

        # (a) markdown=SHIP, no structured block → VERDICT_SHIP
        r_ship = _write_review_doc(None, _make_prev(tmp_path, _VERDICT_SHIP_HEADER, cycle=1))
        assert r_ship.status == "ok"
        assert r_ship.data["verdict"] == VERDICT_SHIP, (
            f"AC3a: no structured block + markdown SHIP → expected VERDICT_SHIP, "
            f"got {r_ship.data['verdict']!r}"
        )

        # (b) markdown=REVISE, no structured block → VERDICT_REVISE
        r_revise = _write_review_doc(
            None, _make_prev(tmp_path, _VERDICT_REVISE_HEADER, cycle=1)
        )
        assert r_revise.status == "ok"
        assert r_revise.data["verdict"] == VERDICT_REVISE, (
            f"AC3b: no structured block + markdown REVISE → expected VERDICT_REVISE, "
            f"got {r_revise.data['verdict']!r}"
        )

        # (c) no headers at all → VERDICT_UNKNOWN
        r_unknown = _write_review_doc(
            None, _make_prev(tmp_path, "prose with no headers at all\n", cycle=1)
        )
        assert r_unknown.status == "ok"
        assert r_unknown.data["verdict"] == VERDICT_UNKNOWN, (
            f"AC3c: no structured block + no markdown header → expected VERDICT_UNKNOWN, "
            f"got {r_unknown.data['verdict']!r}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# TestCycle1MalformedBlock — AC4 (regression guard, PASS pre+post)
# ═══════════════════════════════════════════════════════════════════════════════


class TestCycle1MalformedBlock:

    def test_cycle1_malformed_structured_block_falls_back_to_markdown(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """AC4 (regression guard, PASS pre+post): malformed JSON in structured block
        → extract_structured_findings returns None → falls back to _parse_verdict."""
        raw = _VERDICT_REVISE_HEADER + _FINDINGS_MALFORMED
        _patch_emit(monkeypatch)
        r = _write_review_doc(None, _make_prev(tmp_path, raw, cycle=1))

        assert r.status == "ok", f"expected ok, got {r.status}"
        assert r.data["verdict"] == VERDICT_REVISE, (
            f"AC4: malformed structured block → expected markdown fallback VERDICT_REVISE, "
            f"got {r.data['verdict']!r}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# TestCycle2Unaffected — AC5 (regression guard, PASS pre+post)
# ═══════════════════════════════════════════════════════════════════════════════


class TestCycle2Unaffected:

    def test_cycle2_freeform_unaffected_by_structured_block(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """AC5 (regression guard, PASS pre+post): on cycle=2, the structured-block flip
        is inactive (cycle-1-only). Free-form raw (no FINDING_N: lines) → restricted
        parser returns UNPARSED → falls through to _parse_verdict → markdown SHIP wins."""
        raw = _VERDICT_SHIP_HEADER + _FINDINGS_ONE
        _patch_emit(monkeypatch)
        r = _write_review_doc(None, _make_prev(tmp_path, raw, cycle=2))

        assert r.status == "ok", f"expected ok, got {r.status}"
        assert r.data["verdict"] == VERDICT_SHIP, (
            f"AC5: cycle=2 with markdown SHIP + structured findings → expected VERDICT_SHIP "
            f"(flip is cycle-1-only; restricted parser produces UNPARSED → falls to markdown), "
            f"got {r.data['verdict']!r}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# TestTelemetryPreserved — AC6 (regression guard, PASS pre+post)
# ═══════════════════════════════════════════════════════════════════════════════


class TestTelemetryPreserved:

    def test_cycle1_step5_telemetry_still_emitted(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """AC6 (regression guard, PASS pre+post): Step-5 telemetry events still fire after
        the fix. markdown=SHIP, structured=2 findings (REVISE) → spec_lite_structured_verdict
        with n_findings==2, structured_verdict=REVISE, markdown_verdict=SHIP; AND
        spec_lite_verdict_drift because SHIP != REVISE."""
        raw = _VERDICT_SHIP_HEADER + _FINDINGS_TWO
        captured = _patch_emit(monkeypatch)
        _write_review_doc(None, _make_prev(tmp_path, raw, cycle=1))

        sv = [e for e in captured if e["type"] == "spec_lite_structured_verdict"]
        assert len(sv) == 1, (
            f"AC6: expected 1 spec_lite_structured_verdict event, got {len(sv)}; "
            f"all events: {captured}"
        )
        assert sv[0]["payload"]["n_findings"] == 2, (
            f"AC6: expected n_findings==2, got {sv[0]['payload']['n_findings']}"
        )
        assert sv[0]["payload"]["structured_verdict"] == VERDICT_REVISE, (
            f"AC6: expected structured_verdict==VERDICT_REVISE, got {sv[0]['payload']['structured_verdict']!r}"
        )
        assert sv[0]["payload"]["markdown_verdict"] == VERDICT_SHIP, (
            f"AC6: expected markdown_verdict==VERDICT_SHIP, got {sv[0]['payload']['markdown_verdict']!r}"
        )

        drift = [e for e in captured if e["type"] == "spec_lite_verdict_drift"]
        assert len(drift) == 1, (
            f"AC6: expected 1 spec_lite_verdict_drift event (SHIP != REVISE), "
            f"got {len(drift)}; all events: {captured}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# TestWorkflowLevelGate — AC7 (FAIL pre-fix)
# ═══════════════════════════════════════════════════════════════════════════════


class TestWorkflowLevelGate:

    def test_gate_consumes_flipped_verdict_takes_revise_branch(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """AC7 (done-criterion, FAIL pre-fix): _gate_on_review reads the verdict from
        _write_review_doc's result. When markdown=SHIP + structured has 1 finding → after fix
        _write_review_doc returns VERDICT_REVISE → gate takes REVISE branch
        (cycle 1 < MAX_REVIEW_CYCLES=2) → g.status=='ok', g.data['verdict']==VERDICT_REVISE,
        g.data['cycle']==2, 'findings' in g.data."""
        # spec file must exist so _detect_block_signals (called on SHIP path) doesn't error
        (tmp_path / "build-spec.md").write_text("# spec\n")
        _patch_emit(monkeypatch)

        r = _write_review_doc(
            None, _make_prev(tmp_path, _VERDICT_SHIP_HEADER + _FINDINGS_ONE, cycle=1)
        )
        g = _gate_on_review(None, r)

        assert g.status == "ok", (
            f"AC7: expected gate status 'ok' (REVISE under cap), got {g.status!r}: {g.error}"
        )
        assert g.data["verdict"] == VERDICT_REVISE, (
            f"AC7 FAIL: expected gate to see VERDICT_REVISE (structured wins), "
            f"got {g.data['verdict']!r}. Pre-fix: gate sees VERDICT_SHIP (markdown) and "
            f"takes the SHIP branch instead of REVISE."
        )
        assert g.data["cycle"] == 2, (
            f"AC7: expected gate to return cycle==2 (incremented from 1), got {g.data['cycle']}"
        )
        assert "findings" in g.data, (
            f"AC7: expected 'findings' key in gate data (REVISE branch populates it), "
            f"got keys: {list(g.data.keys())}"
        )
