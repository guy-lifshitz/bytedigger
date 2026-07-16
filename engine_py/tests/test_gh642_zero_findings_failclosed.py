"""RED tests for 34537047 — GH642 phase_45_spec zero-findings SHIP fail-open →
unparseable-prose fail-closed.

Agreement: 34537047-6EA1-4BE9-92BB-525DC66A49F3 (child SYSTEMATIC GH373 verdict-resolution
fail-closed umbrella).

Contract: GREEN will modify the cycle-1 gate-resolution block in `_write_review_doc`
(phase_45_spec.py) so that when structured findings are empty ([]) AND the markdown
`## Verdict` heading is unparseable (prose=None), the zero-findings SHIP heuristic no
longer ships unopposed — it flips to conservative VERDICT_REVISE and emits
`spec_zero_findings_unparseable_fail_closed`. A sibling case (explicit prose REVISE
overriding zero-findings SHIP) gains a new observability event
`spec_revise_without_findings` without changing its (already-correct) verdict.

Expected RED pass/fail:
  FAIL pre-fix: AC1, AC2, AC3 (event half), AC5, AC7  ->  5 failing tests
  PASS pre+post: AC3 (verdict half, folded into the AC3 test which as a whole FAILs
      pre-fix due to the missing event), AC4, AC6, AC8  ->  regression guards
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
    VERDICT_SHIP,
    VERDICT_REVISE,
    VERDICT_UNKNOWN,
    MAX_REVIEW_CYCLES,
)
from contracts import StepResult  # noqa: E402


# ─── raw markdown fixtures ─────────────────────────────────────────────────────
# Format mirrors test_phase_45_spec_cycle1_verdict.py.

_SHIP_HDR = "## Verdict\nSHIP\nAll good.\n"
_REVISE_HDR = "## Verdict\nREVISE\nNeeds work.\n"
_NO_VERDICT_HDR = "Some free-form reviewer prose with no verdict heading.\n"
_F_EMPTY = '\n## Findings (structured)\n```json\n[]\n```\n'
_F_ONE = '\n## Findings (structured)\n```json\n[{"id":"1","type":"missing","evidence":"x","required_action":"y"}]\n```\n'


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
    — NO severity kwarg.
    """
    captured: list[dict] = []
    monkeypatch.setattr(
        phase_45_spec,
        "_emit_safe",
        lambda et, p: captured.append({"type": et, "payload": p}),
    )
    return captured


# ═══════════════════════════════════════════════════════════════════════════════
# TestUnparseableProseFailClosed — AC1, AC2 (FAIL pre-fix)
# ═══════════════════════════════════════════════════════════════════════════════


class TestUnparseableProseFailClosed:

    def test_ac1_empty_findings_unparseable_prose_flips_to_revise(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """AC1 (FAIL pre-fix): [] findings + no parseable '## Verdict' heading
        (prose UNKNOWN) -> data["verdict"] must be VERDICT_REVISE, not the zero-findings
        SHIP heuristic shipping unopposed.
        Pre-fix: resolve_gate_verdict(structured=SHIP, prose=None) -> structured_only SHIP.
        Post-fix: fail-closed override flips SHIP -> REVISE.
        """
        raw = _NO_VERDICT_HDR + _F_EMPTY
        _capture(monkeypatch)
        r = _write_review_doc(None, _make_prev(tmp_path, raw, cycle=1))

        assert r.status == "ok", f"expected status ok, got {r.status!r}: {r.error}"
        assert r.data is not None, "expected result.data to be a dict, got None"
        assert r.data["verdict"] == VERDICT_REVISE, (
            f"AC1 FAIL: [] findings + unparseable prose -> expected VERDICT_REVISE "
            f"(fail-closed), got {r.data['verdict']!r}. Pre-fix: zero-findings SHIP "
            f"heuristic ships unopposed (structured_only SHIP)."
        )

    def test_ac2_unparseable_prose_emits_fail_closed_event(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """AC2 (FAIL pre-fix): same fixture as AC1 -> exactly 1
        spec_zero_findings_unparseable_fail_closed event with
        n_findings==0, markdown_verdict==VERDICT_UNKNOWN, phase=="phase_45_spec".
        Pre-fix: this event type does not exist -> 0 events.
        """
        raw = _NO_VERDICT_HDR + _F_EMPTY
        captured = _capture(monkeypatch)
        _write_review_doc(None, _make_prev(tmp_path, raw, cycle=1))

        events = [
            e for e in captured
            if e["type"] == "spec_zero_findings_unparseable_fail_closed"
        ]
        assert len(events) == 1, (
            f"AC2 FAIL: expected exactly 1 spec_zero_findings_unparseable_fail_closed "
            f"event, got {len(events)}; all events: {captured}"
        )
        payload = events[0]["payload"]
        assert payload["n_findings"] == 0, (
            f"AC2: expected n_findings==0, got {payload.get('n_findings')!r}"
        )
        assert payload["markdown_verdict"] == VERDICT_UNKNOWN, (
            f"AC2: expected markdown_verdict==VERDICT_UNKNOWN, "
            f"got {payload.get('markdown_verdict')!r}"
        )
        assert payload["phase"] == "phase_45_spec", (
            f"AC2: expected phase=='phase_45_spec', got {payload.get('phase')!r}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# TestDivergentReviseObservability — AC3 (FAIL pre-fix — event half missing)
# ═══════════════════════════════════════════════════════════════════════════════


class TestDivergentReviseObservability:

    def test_ac3_divergent_revise_still_revise_and_gains_event(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """AC3 (FAIL pre-fix): [] findings + explicit prose REVISE.
        Verdict half (PASS pre+post, GH373 already fixed this): data["verdict"]==VERDICT_REVISE.
        Event half (FAIL pre-fix, new observability): exactly 1 spec_revise_without_findings
        event with markdown_verdict==VERDICT_REVISE. Pre-fix: verdict correct but no event
        -> whole test fails on the event assertion.
        """
        raw = _REVISE_HDR + _F_EMPTY
        captured = _capture(monkeypatch)
        r = _write_review_doc(None, _make_prev(tmp_path, raw, cycle=1))

        assert r.status == "ok", f"expected status ok, got {r.status!r}: {r.error}"
        assert r.data["verdict"] == VERDICT_REVISE, (
            f"AC3: [] findings + prose REVISE -> expected VERDICT_REVISE (conservative, "
            f"GH373-preexisting), got {r.data['verdict']!r}"
        )

        events = [e for e in captured if e["type"] == "spec_revise_without_findings"]
        assert len(events) == 1, (
            f"AC3 FAIL: expected exactly 1 spec_revise_without_findings event (new "
            f"observability signal), got {len(events)}; all events: {captured}"
        )
        assert events[0]["payload"]["markdown_verdict"] == VERDICT_REVISE, (
            f"AC3: expected markdown_verdict==VERDICT_REVISE, "
            f"got {events[0]['payload'].get('markdown_verdict')!r}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# TestAffirmativeShipUnchanged — AC4 (regression guard, PASS pre+post)
# ═══════════════════════════════════════════════════════════════════════════════


class TestAffirmativeShipUnchanged:

    def test_ac4_empty_findings_explicit_ship_stays_ship_no_new_events(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """AC4 (regression guard, PASS pre+post): [] findings + explicit prose SHIP
        (agree case) -> data["verdict"]==VERDICT_SHIP, and NEITHER new GH642 event
        fires — this is a genuine affirmative approval, not a heuristic-only SHIP.
        """
        raw = _SHIP_HDR + _F_EMPTY
        captured = _capture(monkeypatch)
        r = _write_review_doc(None, _make_prev(tmp_path, raw, cycle=1))

        assert r.status == "ok", f"expected status ok, got {r.status!r}: {r.error}"
        assert r.data["verdict"] == VERDICT_SHIP, (
            f"AC4: [] findings + prose SHIP (agree) -> expected VERDICT_SHIP, "
            f"got {r.data['verdict']!r}"
        )
        fail_closed = [
            e for e in captured
            if e["type"] == "spec_zero_findings_unparseable_fail_closed"
        ]
        revise_without = [
            e for e in captured if e["type"] == "spec_revise_without_findings"
        ]
        assert len(fail_closed) == 0, (
            f"AC4: expected 0 spec_zero_findings_unparseable_fail_closed events "
            f"(genuine approval, not unparseable), got {len(fail_closed)}: {fail_closed}"
        )
        assert len(revise_without) == 0, (
            f"AC4: expected 0 spec_revise_without_findings events (verdict is SHIP, "
            f"not REVISE), got {len(revise_without)}: {revise_without}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# TestWorkflowLevelGateConsumesFlippedVerdict — AC5 (FAIL pre-fix)
# ═══════════════════════════════════════════════════════════════════════════════


class TestWorkflowLevelGateConsumesFlippedVerdict:

    def test_ac5_gate_takes_revise_branch_on_flipped_verdict(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """AC5 (FAIL pre-fix): the AC1 fixture ([] findings + unparseable prose) fed into
        _gate_on_review must take the REVISE branch (cycle 1 < MAX_REVIEW_CYCLES):
          g.status == "error", g.error_code == "E_VALIDATION_RETRY", g.recoverable is True,
          g.data["verdict"] == VERDICT_REVISE, g.data["retry_from_step"] == 0,
          "findings" in g.data.
        Pre-fix: _write_review_doc still returns VERDICT_SHIP for this fixture, so the
        gate takes the SHIP branch (status=="ok") instead.
        """
        _capture(monkeypatch)

        raw = _NO_VERDICT_HDR + _F_EMPTY
        r = _write_review_doc(None, _make_prev(tmp_path, raw, cycle=1))
        g = _gate_on_review(None, r)

        assert g.status == "error", (
            f"AC5 FAIL: expected gate status 'error' (REVISE-under-cap -> "
            f"E_VALIDATION_RETRY), got {g.status!r}. Pre-fix: gate sees VERDICT_SHIP "
            f"and returns status='ok'."
        )
        assert g.error_code == "E_VALIDATION_RETRY", (
            f"AC5: expected error_code 'E_VALIDATION_RETRY', got {g.error_code!r}"
        )
        assert g.recoverable is True, (
            f"AC5: expected recoverable=True, got {g.recoverable!r}"
        )
        assert g.data is not None, "AC5: expected g.data to be a dict, got None"
        assert g.data["verdict"] == VERDICT_REVISE, (
            f"AC5 FAIL: expected gate data verdict==VERDICT_REVISE (fail-closed flip), "
            f"got {g.data['verdict']!r}. Pre-fix: gate sees VERDICT_SHIP."
        )
        assert g.data["retry_from_step"] == 0, (
            f"AC5: expected retry_from_step==0, got {g.data.get('retry_from_step')!r}"
        )
        assert "findings" in g.data, (
            f"AC5: expected 'findings' key in gate data (REVISE branch populates it), "
            f"got keys: {list(g.data.keys())}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# TestNonEmptyFindingsRegression — AC6 (regression guard, PASS pre+post)
# ═══════════════════════════════════════════════════════════════════════════════


class TestNonEmptyFindingsRegression:

    def test_ac6_nonempty_findings_ship_stays_revise_no_new_events(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """AC6 (regression guard, PASS pre+post): 1 non-empty finding + prose SHIP
        -> data["verdict"]==VERDICT_REVISE (n>0, unchanged), and no new GH642 events
        (the fail-closed/observability logic only fires for n_findings==0).
        """
        raw = _SHIP_HDR + _F_ONE
        captured = _capture(monkeypatch)
        r = _write_review_doc(None, _make_prev(tmp_path, raw, cycle=1))

        assert r.status == "ok", f"expected status ok, got {r.status!r}: {r.error}"
        assert r.data["verdict"] == VERDICT_REVISE, (
            f"AC6: 1 finding + prose SHIP -> expected VERDICT_REVISE (n>0 unchanged), "
            f"got {r.data['verdict']!r}"
        )
        gh642_events = [
            e for e in captured
            if e["type"] in (
                "spec_zero_findings_unparseable_fail_closed",
                "spec_revise_without_findings",
            )
        ]
        assert len(gh642_events) == 0, (
            f"AC6: expected 0 GH642 events (n_findings>0, out of scope for the "
            f"zero-findings fail-closed logic), got {len(gh642_events)}: {gh642_events}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# TestReEntryStability — AC7 (§1ab re-entry, FAIL pre-fix)
# ═══════════════════════════════════════════════════════════════════════════════


class TestReEntryStability:

    def test_ac7_repeated_invocation_stable_revise_deterministic_emit(
        self, tmp_path: Path
    ) -> None:
        """AC7 (§1ab re-entry, FAIL pre-fix): _write_review_doc is a pure function of
        prev.data for verdict purposes — no durable counter is touched here. Re-running
        it twice on identical input (simulating in-phase retry / auto-resume / DBOS-replay)
        must produce the SAME VERDICT_REVISE both times (no flip to SHIP on the second
        call), and the spec_zero_findings_unparseable_fail_closed event must fire
        deterministically on EACH call (no dedup, no state carry across calls).
        Pre-fix: both calls return VERDICT_SHIP and neither call emits the (nonexistent)
        event -> the per-call event-count assertion fails.
        """
        raw = _NO_VERDICT_HDR + _F_EMPTY

        # Call 1 — fresh capture window.
        import pytest

        mp1 = pytest.MonkeyPatch()
        captured1 = _capture(mp1)
        r1 = _write_review_doc(None, _make_prev(tmp_path, raw, cycle=1))
        mp1.undo()

        # Call 2 — independent fresh capture window (identical prev semantics).
        mp2 = pytest.MonkeyPatch()
        captured2 = _capture(mp2)
        r2 = _write_review_doc(None, _make_prev(tmp_path, raw, cycle=1))
        mp2.undo()

        assert r1.data["verdict"] == VERDICT_REVISE, (
            f"AC7 FAIL (call 1): expected VERDICT_REVISE, got {r1.data['verdict']!r}"
        )
        assert r2.data["verdict"] == VERDICT_REVISE, (
            f"AC7 FAIL (call 2): expected VERDICT_REVISE (stable, no flip on re-entry), "
            f"got {r2.data['verdict']!r}"
        )

        evts1 = [
            e for e in captured1
            if e["type"] == "spec_zero_findings_unparseable_fail_closed"
        ]
        evts2 = [
            e for e in captured2
            if e["type"] == "spec_zero_findings_unparseable_fail_closed"
        ]
        assert len(evts1) == 1, (
            f"AC7 FAIL (call 1): expected exactly 1 fail-closed event, got {len(evts1)}: "
            f"{captured1}"
        )
        assert len(evts2) == 1, (
            f"AC7 FAIL (call 2): expected exactly 1 fail-closed event (deterministic "
            f"re-emit, no dedup/counter), got {len(evts2)}: {captured2}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# TestStructuredBlockAbsentRegression — AC8 (regression guard, PASS pre+post)
# ═══════════════════════════════════════════════════════════════════════════════


class TestStructuredBlockAbsentRegression:

    def test_ac8_no_structured_block_falls_back_to_markdown_ship(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """AC8 (regression guard, PASS pre+post): structured block absent entirely
        + prose SHIP -> falls back to _parse_verdict(raw) -> VERDICT_SHIP, unaffected
        by the GH642 fix (that logic only applies when a structured block IS present).
        """
        raw = _SHIP_HDR
        _capture(monkeypatch)
        r = _write_review_doc(None, _make_prev(tmp_path, raw, cycle=1))

        assert r.status == "ok", f"expected status ok, got {r.status!r}: {r.error}"
        assert r.data["verdict"] == VERDICT_SHIP, (
            f"AC8: no structured block + prose SHIP -> expected VERDICT_SHIP "
            f"(markdown fallback, unaffected by GH642 fix), got {r.data['verdict']!r}"
        )
