"""RED tests for 532CDEC8 — phase_45_spec (FEATURE/COMPLEX) cycle-1 verdict from structured findings count.

Agreement: 532CDEC8-C5F9-4DFC-8A04-3326A88D1999 (child of 95D3E5F6 — disk-truth UMBRELLA, G1 band-aid #2)
Band-aid #2: FEATURE/COMPLEX sibling of band-aid #1 (9DE0330B, `test_phase_45_spec_lite_cycle1_verdict.py`).

Contract: GREEN will modify _write_review_doc in phase_45_spec.py so that on cycle<=1,
when extract_structured_findings(raw) returns a non-None list, the final verdict placed in
StepResult.data["verdict"] becomes VERDICT_SHIP if len(findings)==0 else VERDICT_REVISE
(the structured/disk-truth value) instead of _parse_verdict(raw) (the markdown-prose regex).
When the structured block is absent or malformed (extract_structured_findings returns None)
→ falls back to _parse_verdict(raw) exactly as today. On cycle>=2 → unchanged.

Expected RED pass/fail:
  FAIL pre-fix: AC1, AC2, AC6 (events i+ii), AC7  →  4 failing tests
  PASS pre+post: AC3, AC4, AC5  →  3 regression guards that pass today
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).parent
ENGINE_ROOT = HERE.parent
for p in (str(ENGINE_ROOT), str(ENGINE_ROOT / "lib"), str(ENGINE_ROOT / "workflows")):
    if p not in sys.path:
        sys.path.insert(0, p)

import phase_45_spec  # noqa: E402  (monkeypatch target for _emit_safe)
from phase_45_spec import (  # noqa: E402
    _write_review_doc,
    _gate_on_review,
    _parse_verdict,
    VERDICT_SHIP,
    VERDICT_REVISE,
    VERDICT_UNKNOWN,
    MAX_REVIEW_CYCLES,
)
from contracts import StepResult  # noqa: E402


# ─── raw markdown fixtures ─────────────────────────────────────────────────────
# Format mirrors test_phase_45_spec_lite_cycle1_verdict.py and the spec's draft constants.
# extract_structured_findings scans for a fenced ```json block under ## Findings (structured).

_SHIP_HDR = "## Verdict\nSHIP\nAll good.\n"
_REVISE_HDR = "## Verdict\nREVISE\nNeeds work.\n"
_F_EMPTY = '\n## Findings (structured)\n```json\n[]\n```\n'
_F_ONE = '\n## Findings (structured)\n```json\n[{"id":"1","type":"missing","evidence":"x","required_action":"y"}]\n```\n'
_F_TWO = '\n## Findings (structured)\n```json\n[{"id":"1","type":"missing","evidence":"x","required_action":"y"},{"id":"2","type":"vague","evidence":"z","required_action":"w"}]\n```\n'
_F_MALFORMED = '\n## Findings (structured)\n```json\n{ this is not a list\n```\n'


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


def _capture(monkeypatch) -> list[dict]:
    """Monkeypatch phase_45_spec._emit_safe; capture all calls.

    NOTE: _emit_safe in phase_45_spec has signature (event_type: str, payload: dict)
    — NO severity kwarg (confirmed in spec Open Questions).
    Lambda must match exactly: lambda et, p: ...
    """
    captured: list[dict] = []
    monkeypatch.setattr(
        phase_45_spec,
        "_emit_safe",
        lambda et, p: captured.append({"type": et, "payload": p}),
    )
    return captured


# ═══════════════════════════════════════════════════════════════════════════════
# TestCycle1StructuredVerdictOverride — AC1, AC2 (FAIL pre-fix)
# ═══════════════════════════════════════════════════════════════════════════════


class TestCycle1StructuredVerdictOverride:

    def test_cycle1_structured_one_finding_overrides_markdown_ship(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """AC1 (core proof, FAIL pre-fix): cycle=1, markdown says SHIP, structured has 1 finding
        → structured wins → data["verdict"] == VERDICT_REVISE.
        Pre-fix: _parse_verdict(raw) returns SHIP (markdown wins); verdict==SHIP.
        Post-fix: structured block present, n==1 → VERDICT_REVISE.
        """
        raw = _SHIP_HDR + _F_ONE
        _capture(monkeypatch)
        r = _write_review_doc(None, _make_prev(tmp_path, raw, cycle=1))

        assert r.status == "ok", f"expected status ok, got {r.status!r}: {r.error}"
        assert r.data is not None, "expected result.data to be a dict, got None"
        assert r.data["verdict"] == VERDICT_REVISE, (
            f"AC1 FAIL: markdown=SHIP but structured has 1 finding → expected VERDICT_REVISE, "
            f"got {r.data['verdict']!r}. Pre-fix: _parse_verdict returns SHIP (markdown wins). "
            f"Post-fix: structured block wins when present."
        )

    def test_cycle1_structured_empty_divergent_conservative_revise(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """GH373 Class 1 / spec §2 Part B (Q4 fix): cycle=1, markdown says REVISE,
        structured findings=[] → structured(SHIP) and prose(REVISE) DIVERGE →
        resolve_gate_verdict resolves to the conservative verdict (REVISE), not
        the fail-open structured value.
        Pre-GH373: structured block present, n==0 → VERDICT_SHIP (fail-open).
        Post-GH373: divergent signals → conservative (REVISE) wins.
        """
        raw = _REVISE_HDR + _F_EMPTY
        _capture(monkeypatch)
        r = _write_review_doc(None, _make_prev(tmp_path, raw, cycle=1))

        assert r.status == "ok", f"expected status ok, got {r.status!r}: {r.error}"
        assert r.data is not None, "expected result.data to be a dict, got None"
        assert r.data["verdict"] == VERDICT_REVISE, (
            f"AC2 FAIL: markdown=REVISE, structured findings=[] (divergent) → expected "
            f"VERDICT_REVISE (conservative wins), got {r.data['verdict']!r}. "
            f"Pre-GH373: structured wins outright → VERDICT_SHIP (fail-open)."
        )


# ═══════════════════════════════════════════════════════════════════════════════
# TestCycle1BackwardCompat — AC3 (regression guard, PASS pre+post)
# ═══════════════════════════════════════════════════════════════════════════════


class TestCycle1BackwardCompat:

    def test_cycle1_no_structured_block_falls_back_to_markdown(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """AC3 (regression guard, PASS pre+post): when no structured block is present,
        _parse_verdict (markdown regex) remains the verdict source.
        (a) SHIP header alone → VERDICT_SHIP
        (b) REVISE header alone → VERDICT_REVISE
        (c) prose with no headers → VERDICT_UNKNOWN
        """
        _capture(monkeypatch)

        # (a) markdown=SHIP, no structured block → VERDICT_SHIP
        r_ship = _write_review_doc(None, _make_prev(tmp_path, _SHIP_HDR, cycle=1))
        assert r_ship.status == "ok"
        assert r_ship.data["verdict"] == VERDICT_SHIP, (
            f"AC3a: no structured block + markdown SHIP → expected VERDICT_SHIP, "
            f"got {r_ship.data['verdict']!r}"
        )

        # (b) markdown=REVISE, no structured block → VERDICT_REVISE
        r_revise = _write_review_doc(
            None, _make_prev(tmp_path, _REVISE_HDR, cycle=1)
        )
        assert r_revise.status == "ok"
        assert r_revise.data["verdict"] == VERDICT_REVISE, (
            f"AC3b: no structured block + markdown REVISE → expected VERDICT_REVISE, "
            f"got {r_revise.data['verdict']!r}"
        )

        # (c) no headers at all → VERDICT_UNKNOWN
        r_unknown = _write_review_doc(
            None, _make_prev(tmp_path, "prose with no headers\n", cycle=1)
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
        → extract_structured_findings returns None → falls back to _parse_verdict(raw).
        """
        raw = _REVISE_HDR + _F_MALFORMED
        _capture(monkeypatch)
        r = _write_review_doc(None, _make_prev(tmp_path, raw, cycle=1))

        assert r.status == "ok", f"expected ok, got {r.status!r}"
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
        is inactive (cycle-1-only gate). Free-form raw (no FINDING_N: lines) → restricted
        parser (parse_per_finding_verdicts) returns UNPARSED → falls through to
        _parse_verdict → markdown SHIP wins.
        """
        raw = _SHIP_HDR + _F_ONE
        _capture(monkeypatch)
        r = _write_review_doc(None, _make_prev(tmp_path, raw, cycle=2))

        assert r.status == "ok", f"expected ok, got {r.status!r}"
        assert r.data["verdict"] == VERDICT_SHIP, (
            f"AC5: cycle=2 with markdown SHIP + structured findings → expected VERDICT_SHIP "
            f"(flip is cycle-1-only; restricted parser produces UNPARSED → falls to markdown), "
            f"got {r.data['verdict']!r}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# TestTelemetry — AC6 (FAIL pre-fix for events i+ii; event iii PASS pre+post)
# ═══════════════════════════════════════════════════════════════════════════════


class TestTelemetry:

    def test_cycle1_structured_verdict_and_drift_events_emitted(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """AC6 (FAIL pre-fix — events i and ii don't exist yet):
        markdown=SHIP, structured=2 findings (REVISE drift):
        (i)  exactly 1 spec_structured_verdict event with n_findings==2,
             structured_verdict==VERDICT_REVISE, markdown_verdict==VERDICT_SHIP,
             phase=="phase_45_spec";
        (ii) a spec_verdict_drift event (SHIP != REVISE);
        (iii) existing findings_block_compliance event still fires with
             json_findings_count==2, json_block_present==True (PASS pre+post).
        """
        raw = _SHIP_HDR + _F_TWO
        captured = _capture(monkeypatch)
        _write_review_doc(None, _make_prev(tmp_path, raw, cycle=1))

        # (iii) findings_block_compliance — already exists today → regression guard
        fbc = [e for e in captured if e["type"] == "findings_block_compliance"]
        assert len(fbc) == 1, (
            f"AC6(iii): expected 1 findings_block_compliance event, got {len(fbc)}; "
            f"all events: {captured}"
        )
        assert fbc[0]["payload"]["json_block_present"] is True, (
            f"AC6(iii): expected json_block_present==True, got {fbc[0]['payload']['json_block_present']}"
        )
        assert fbc[0]["payload"]["json_findings_count"] == 2, (
            f"AC6(iii): expected json_findings_count==2, got {fbc[0]['payload']['json_findings_count']}"
        )

        # (i) spec_structured_verdict — new event, does not exist pre-fix → FAIL
        sv = [e for e in captured if e["type"] == "spec_structured_verdict"]
        assert len(sv) == 1, (
            f"AC6(i) FAIL: expected 1 spec_structured_verdict event, got {len(sv)}; "
            f"all events: {captured}"
        )
        assert sv[0]["payload"]["n_findings"] == 2, (
            f"AC6(i): expected n_findings==2, got {sv[0]['payload']['n_findings']}"
        )
        assert sv[0]["payload"]["structured_verdict"] == VERDICT_REVISE, (
            f"AC6(i): expected structured_verdict==VERDICT_REVISE, "
            f"got {sv[0]['payload']['structured_verdict']!r}"
        )
        assert sv[0]["payload"]["markdown_verdict"] == VERDICT_SHIP, (
            f"AC6(i): expected markdown_verdict==VERDICT_SHIP, "
            f"got {sv[0]['payload']['markdown_verdict']!r}"
        )
        assert sv[0]["payload"]["phase"] == "phase_45_spec", (
            f"AC6(i): expected phase=='phase_45_spec', got {sv[0]['payload']['phase']!r}"
        )

        # (ii) spec_verdict_drift — new event, does not exist pre-fix → FAIL
        drift = [e for e in captured if e["type"] == "spec_verdict_drift"]
        assert len(drift) == 1, (
            f"AC6(ii) FAIL: expected 1 spec_verdict_drift event (SHIP != REVISE), "
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
        _write_review_doc's result. When markdown=SHIP + structured has 1 finding →
        after fix _write_review_doc returns VERDICT_REVISE → gate takes REVISE branch
        (cycle 1 < MAX_REVIEW_CYCLES=2):
          g.status == "error"
          g.error_code == "E_VALIDATION_RETRY"
          g.recoverable is True
          g.data["verdict"] == VERDICT_REVISE
          g.data["retry_from_step"] == 0
          "findings" in g.data
        Pre-fix: gate sees VERDICT_SHIP from markdown→_parse_verdict, takes the SHIP
        branch (status="ok") instead of REVISE, so status=="ok" not "error".
        """
        _capture(monkeypatch)

        r = _write_review_doc(
            None, _make_prev(tmp_path, _SHIP_HDR + _F_ONE, cycle=1)
        )
        g = _gate_on_review(None, r)

        assert g.status == "error", (
            f"AC7 FAIL: expected gate status 'error' (REVISE-under-cap → E_VALIDATION_RETRY), "
            f"got {g.status!r}. Pre-fix: gate sees VERDICT_SHIP and returns status='ok'."
        )
        assert g.error_code == "E_VALIDATION_RETRY", (
            f"AC7: expected error_code 'E_VALIDATION_RETRY', got {g.error_code!r}"
        )
        assert g.recoverable is True, (
            f"AC7: expected recoverable=True, got {g.recoverable!r}"
        )
        assert g.data is not None, "AC7: expected g.data to be a dict, got None"
        assert g.data["verdict"] == VERDICT_REVISE, (
            f"AC7 FAIL: expected gate data verdict==VERDICT_REVISE (structured wins), "
            f"got {g.data['verdict']!r}. Pre-fix: gate sees VERDICT_SHIP (markdown)."
        )
        assert g.data["retry_from_step"] == 0, (
            f"AC7: expected retry_from_step==0, got {g.data.get('retry_from_step')!r}"
        )
        assert "findings" in g.data, (
            f"AC7: expected 'findings' key in gate data (REVISE branch populates it), "
            f"got keys: {list(g.data.keys())}"
        )
