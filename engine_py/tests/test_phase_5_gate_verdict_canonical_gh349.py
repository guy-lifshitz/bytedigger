"""RED tests for GH349/ENG-3 — phase_5 gate emits ONE canonical verdict token (§1g).

Agreement: 5186F5C9-9F98-4C08-B211-9D620723F2A9 · Issue #349

Contract (GREEN will implement):
1. New module-level pure helper ``_canonical_gate_verdict(passed, markdown_verdict)``
   in workflows/phase_5_implement.py: returns VERDICT_PASS when passed is True,
   VERDICT_FAIL when passed is False and markdown_verdict == VERDICT_PASS
   (drift-inversion), else returns markdown_verdict unchanged.
2. ``_gate_on_validation`` computes ``gate_verdict = _canonical_gate_verdict(passed, verdict)``
   and uses it (not the raw markdown ``verdict``) as the ``"verdict"`` key in every
   returned StepResult.data, while ALSO adding a new ``"markdown_verdict"`` key that
   preserves the raw markdown verdict for observability.
3. On divergence (gate_verdict != verdict) the gate emits exactly one
   ``gate_verdict_canonicalized`` event via ``_emit_safe`` with payload
   ``{"markdown_verdict": verdict, "gate_verdict": gate_verdict, "cycle": cycle, "phase": 5}``.

D1CF5FDF: ``_canonical_gate_verdict`` does not exist pre-GREEN. It is NEVER imported
at module top level here — every test that needs it resolves it via
``getattr(phase_5_implement, "_canonical_gate_verdict", None)`` inside the test body,
so this file stays pytest-collectable and fails at ASSERT time, not COLLECT time.

Do NOT implement any production change here — RED-only file. Do NOT patch/mock
``_gate_on_validation`` or ``_canonical_gate_verdict`` (stub-passability lint, 7AD3D393) —
they are the units under test. ``_emit_safe`` monkeypatch-capture is allowed (infra).
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))

from bytedigger_engine.workflows import phase_5_implement  # noqa: E402  (needed for monkeypatch target + getattr resolve)
from bytedigger_engine.contracts import StepResult  # noqa: E402
from bytedigger_engine.workflows.phase_5_implement import (  # noqa: E402
    _gate_on_validation,
    VERDICT_PASS,
    VERDICT_FAIL,
    VERDICT_UNKNOWN,
    MAX_VALIDATION_CYCLES,
    ValidationVerdict,
)
from bytedigger_engine.engine import _extract_marker_text  # noqa: E402  (AC2 — exists today)


# ─── fixture builder (copied from test_phase_5_gate_structured_verdict.py:80-107) ──


def _make_gate_prev(
    verdict: str,
    cycle: int,
    structured: "ValidationVerdict | None",
) -> StepResult:
    """Prev step result for _gate_on_validation (simulates write_validation_doc output).

    Only injects the 'structured_verdict' key when structured is not None, so
    the backward-compat "key absent" path is also exercisable.
    """
    base: dict = {
        "verdict": verdict,
        "cycle": cycle,
        "validation_doc_path": "/tmp/v.md",
        "spec_path": "/tmp/s.md",
        "red_log_path": "/tmp/r.log",
        "validation_raw": "...",
        "red_commit_sha": "deadbeef",
        "red_test_paths": [],
    }
    if structured is not None:
        base["structured_verdict"] = structured
    return StepResult(
        status="ok",
        data=base,
        duration_ms=0,
        step_name="write_validation_doc",
    )


# ═══════════════════════════════════════════════════════════════════════════════
# TestGateCanonicalVerdict
# ═══════════════════════════════════════════════════════════════════════════════


class TestGateCanonicalVerdict:
    """AC1-AC9: _gate_on_validation must emit ONE canonical verdict token that
    the LoopRunner until_marker="PASS" check can trust on every drift direction."""

    def test_gate_core_drift_loop_continue_canonicalizes_verdict(self) -> None:
        """AC1: markdown PASS + structured reject at cycle 1 (< cap) must NOT
        loop-continue with verdict="PASS" (today's bug — marker would falsely
        terminate the loop as success). Canonical verdict must be FAIL."""
        prev = _make_gate_prev(
            VERDICT_PASS,
            1,
            ValidationVerdict(approve=False, reject_reason="x"),
        )
        r = _gate_on_validation(None, prev)

        assert r.status == "ok"
        assert r.data["verdict"] == VERDICT_FAIL, (
            "expected canonical verdict FAIL on markdown-PASS/structured-reject "
            f"drift, got {r.data.get('verdict')!r} (today's bug: raw markdown 'PASS' "
            "leaks through and falsely satisfies LoopRunner's until_marker)"
        )
        assert VERDICT_PASS not in str(r.data["verdict"])
        assert r.data["cycle"] == 2
        assert "findings" in r.data
        assert r.data["markdown_verdict"] == VERDICT_PASS

    def test_gate_marker_equivalence_on_drift(self) -> None:
        """AC2: LoopRunner's _extract_marker_text over the gate's returned data
        must NOT contain VERDICT_PASS on markdown-PASS/structured-reject drift —
        i.e. the marker check and the gate decision must agree."""
        prev = _make_gate_prev(
            VERDICT_PASS,
            1,
            ValidationVerdict(approve=False, reject_reason="x"),
        )
        r = _gate_on_validation(None, prev)

        marker_text = _extract_marker_text(r, "verdict")
        assert VERDICT_PASS not in marker_text, (
            f"marker text {marker_text!r} must not contain VERDICT_PASS when the "
            "gate rejected on structured.approve=False — LoopRunner would falsely "
            "terminate the loop as success"
        )

    def test_gate_inverse_drift_proceed_canonicalizes_verdict(self) -> None:
        """AC3: markdown FAIL + structured approve must proceed with canonical
        verdict PASS (not leak the raw markdown FAIL into the proceed dict)."""
        prev = _make_gate_prev(
            VERDICT_FAIL,
            1,
            ValidationVerdict(approve=True, reject_reason=None),
        )
        r = _gate_on_validation(None, prev)

        assert r.status == "ok"
        assert "red_commit_sha" in r.data
        assert "findings" not in r.data
        assert r.data["verdict"] == VERDICT_PASS, (
            "expected canonical verdict PASS on markdown-FAIL/structured-approve "
            f"inverse drift, got {r.data.get('verdict')!r}"
        )
        assert r.data["markdown_verdict"] == VERDICT_FAIL

    def test_gate_unknown_markdown_with_structured_approve_canonicalizes_to_pass(
        self,
    ) -> None:
        """AC4: markdown UNKNOWN + structured approve must proceed with
        canonical verdict PASS."""
        prev = _make_gate_prev(
            VERDICT_UNKNOWN,
            1,
            ValidationVerdict(approve=True, reject_reason=None),
        )
        r = _gate_on_validation(None, prev)

        assert r.status == "ok"
        assert r.data["verdict"] == VERDICT_PASS, (
            f"expected canonical verdict PASS, got {r.data.get('verdict')!r}"
        )

    def test_gate_emits_canonicalization_telemetry_on_drift(self, monkeypatch) -> None:
        """AC5: on markdown-PASS/structured-reject drift, the gate must emit
        exactly ONE 'gate_verdict_canonicalized' event with the expected payload."""
        recorded: list[tuple[str, dict]] = []

        def _recorder(event, payload, severity="warning"):
            recorded.append((event, payload))

        monkeypatch.setattr(phase_5_implement, "_emit_safe", _recorder)

        prev = _make_gate_prev(
            VERDICT_PASS,
            1,
            ValidationVerdict(approve=False, reject_reason="x"),
        )
        _gate_on_validation(None, prev)

        matches = [
            (event, payload)
            for event, payload in recorded
            if event == "gate_verdict_canonicalized"
        ]
        assert len(matches) == 1, (
            f"expected exactly ONE gate_verdict_canonicalized event, got "
            f"{len(matches)}: {recorded!r}"
        )
        _, payload = matches[0]
        assert payload.get("markdown_verdict") == VERDICT_PASS
        assert payload.get("gate_verdict") == VERDICT_FAIL
        assert payload.get("cycle") == 1
        assert payload.get("phase") == 5

    def test_gate_no_drift_no_emit_regression_guard(self, monkeypatch) -> None:
        """AC6 (declared regression guard — expected to PASS pre-GREEN too):
        no markdown/structured drift (both agree FAIL) must NOT emit
        gate_verdict_canonicalized, and verdict stays FAIL."""
        recorded: list[tuple[str, dict]] = []

        def _recorder(event, payload, severity="warning"):
            recorded.append((event, payload))

        monkeypatch.setattr(phase_5_implement, "_emit_safe", _recorder)

        prev = _make_gate_prev(
            VERDICT_FAIL,
            1,
            ValidationVerdict(approve=False, reject_reason="x"),
        )
        r = _gate_on_validation(None, prev)

        matches = [e for e, _ in recorded if e == "gate_verdict_canonicalized"]
        assert len(matches) == 0, (
            f"expected zero gate_verdict_canonicalized events on no-drift, got {matches!r}"
        )
        assert r.data["verdict"] == VERDICT_FAIL

    def test_gate_terminal_drift_at_cap_canonicalizes_verdict(self) -> None:
        """AC7: markdown PASS + structured reject at MAX_VALIDATION_CYCLES (cap)
        must terminate with canonical verdict FAIL, not the leaked markdown PASS."""
        prev = _make_gate_prev(
            VERDICT_PASS,
            MAX_VALIDATION_CYCLES,
            ValidationVerdict(approve=False, reject_reason="x"),
        )
        r = _gate_on_validation(None, prev)

        assert r.status == "error"
        assert r.error_code == "E_VALIDATION_FAILED"
        assert r.data["verdict"] == VERDICT_FAIL, (
            f"expected canonical terminal verdict FAIL, got {r.data.get('verdict')!r}"
        )
        assert r.data["markdown_verdict"] == VERDICT_PASS

    def test_gate_backward_compat_no_structured_key_adds_markdown_verdict(self) -> None:
        """AC8: with no 'structured_verdict' key (backward-compat fallback to
        markdown), both proceed and loop-continue branches must ALSO carry the
        new 'markdown_verdict' key equal to the (unchanged) canonical verdict."""
        # (a) markdown PASS, no structured key -> proceed
        prev_pass = _make_gate_prev(VERDICT_PASS, 1, None)
        r_pass = _gate_on_validation(None, prev_pass)

        assert r_pass.status == "ok"
        assert r_pass.data["verdict"] == VERDICT_PASS
        assert r_pass.data["markdown_verdict"] == VERDICT_PASS, (
            "expected 'markdown_verdict' key present even on backward-compat "
            "no-structured proceed path"
        )

        # (b) markdown FAIL, no structured key, cycle < cap -> loop-continue
        prev_fail = _make_gate_prev(VERDICT_FAIL, 1, None)
        r_fail = _gate_on_validation(None, prev_fail)

        assert r_fail.status == "ok"
        assert r_fail.data["verdict"] == VERDICT_FAIL
        assert r_fail.data["markdown_verdict"] == VERDICT_FAIL, (
            "expected 'markdown_verdict' key present even on backward-compat "
            "no-structured loop-continue path"
        )

    def test_canonical_gate_verdict_helper_invariant_matrix(self) -> None:
        """AC9: the pure helper _canonical_gate_verdict must exist and satisfy
        (VERDICT_PASS in result) == passed for every (passed, markdown) pair,
        and must return the markdown verdict unchanged when passed is False and
        markdown != VERDICT_PASS (no coercion of an already-non-PASS token)."""
        helper = getattr(phase_5_implement, "_canonical_gate_verdict", None)
        assert helper is not None, (
            "expected phase_5_implement._canonical_gate_verdict to exist "
            "(GH349/ENG-3 canonicalization helper) — not found pre-GREEN"
        )

        markdown_values = [VERDICT_PASS, VERDICT_FAIL, VERDICT_UNKNOWN, "PARTIAL"]
        for passed in (True, False):
            for md in markdown_values:
                result = helper(passed, md)
                assert (VERDICT_PASS in result) == passed, (
                    f"invariant broken for passed={passed}, markdown={md!r}: "
                    f"got {result!r}"
                )
                if passed is False and md != VERDICT_PASS:
                    assert result == md, (
                        f"expected markdown verdict {md!r} preserved unchanged "
                        f"when passed=False and markdown != PASS, got {result!r}"
                    )
