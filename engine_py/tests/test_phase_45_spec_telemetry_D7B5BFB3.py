"""RED tests for D7B5BFB3 — phase_45_spec_* telemetry emission.

Adds 5 new observability events emitted symmetrically from both
phase_45_spec_lite.py and phase_45_spec.py:
  - phase_45_spec_writer_complete  (top of _write_review_doc)
  - phase_45_spec_review_complete  (end of _write_review_doc, before each return)
  - phase_45_spec_ship             (_gate_on_review SHIP branch)
  - phase_45_spec_revise           (_gate_on_review REVISE-retry branch)
  - phase_45_spec_abort            (_gate_on_review terminal branches)

All events carry a "phase" discriminator key.
Existing events are NOT modified (additive-only contract).

All 12 test functions MUST FAIL until GREEN implements the emission calls.
Do NOT implement any production change here — RED-only file.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).parent
ENGINE_ROOT = HERE.parent
sys.path.insert(0, str(ENGINE_ROOT))

from bytedigger_engine.workflows import phase_45_spec_lite  # noqa: E402
from bytedigger_engine.workflows import phase_45_spec  # noqa: E402
from bytedigger_engine.workflows.phase_45_spec_lite import (  # noqa: E402
    _write_review_doc as _write_review_doc_lite,
    _gate_on_review as _gate_on_review_lite,
    _truncate_findings,
    VERDICT_SHIP,
    VERDICT_REVISE,
    VERDICT_UNKNOWN,
    MAX_REVIEW_CYCLES,
)
from bytedigger_engine.workflows.phase_45_spec import (  # noqa: E402
    _write_review_doc as _write_review_doc_full,
    _gate_on_review as _gate_on_review_full,
)
from bytedigger_engine.contracts import StepResult  # noqa: E402


# ─── Parametrize targets ──────────────────────────────────────────────────────

# Each tuple: (module, write_fn, gate_fn, phase_label)
BOTH_PHASES = [
    pytest.param(
        phase_45_spec_lite,
        _write_review_doc_lite,
        _gate_on_review_lite,
        "phase_45_spec_lite",
        id="spec_lite",
    ),
    pytest.param(
        phase_45_spec,
        _write_review_doc_full,
        _gate_on_review_full,
        "phase_45_spec",
        id="spec_full",
    ),
]


# ─── Fixtures / helpers ───────────────────────────────────────────────────────

_RAW_SHIP = "## Verdict\nSHIP\n"
_RAW_REVISE = "## Verdict\nREVISE\n## Findings\n1. fix this\n"
_RAW_THREE_FINDINGS = "## Verdict\nREVISE\nfinding1\nfinding2\nfinding3\n"


def _make_write_prev(tmp_path: Path, raw: str, cycle: int = 1) -> StepResult:
    """Build a StepResult simulating what _invoke_review_llm returns,
    suitable for passing directly to _write_review_doc."""
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
        duration_ms=100,
        step_name="invoke_review_llm",
    )


def _make_gate_prev(
    tmp_path: Path,
    verdict: str,
    cycle: int = 1,
    raw_review: str = "",
    gate_attempts: dict | None = None,
) -> StepResult:
    """Build a StepResult simulating what _write_review_doc returns,
    suitable for passing directly to _gate_on_review.

    GH625: `gate_attempts` optionally seeds per-gate attempt accounting for
    phase_45_spec.py's `_gate_on_review` (spec_full), which now decides
    cap via `attempts >= pol.cycle_cap` (phase_45_spec.py:3116), not raw
    cycle. phase_45_spec_lite's `_gate_on_review` is unaffected (still uses
    `cycle < MAX_REVIEW_CYCLES` directly), so this key is a no-op there.
    """
    review_path = tmp_path / "build-plan-review.md"
    review_path.write_text(raw_review or "## Verdict\n" + verdict + "\n")
    spec_path = tmp_path / "build-spec.md"
    spec_path.write_text("## Context\nstub spec\n")
    data: dict = {
        "verdict": verdict,
        "cycle": cycle,
        "review_path": str(review_path),
        "spec_path": str(spec_path),
        "review_raw": raw_review or ("## Verdict\n" + verdict + "\n"),
    }
    if gate_attempts is not None:
        data["gate_attempts"] = gate_attempts
    return StepResult(
        status="ok",
        data=data,
        duration_ms=100,
        step_name="write_review_doc",
    )


def _patch_emit(monkeypatch, module) -> list[tuple[str, dict]]:
    """Monkeypatch module._emit_safe; return captured (event_type, payload) list.

    Pattern A (monkeypatch + events-list) — mirrors existing sibling tests.
    """
    captured: list[tuple[str, dict]] = []
    monkeypatch.setattr(module, "_emit_safe", lambda et, p: captured.append((et, p)))
    return captured


def _events_of(captured: list[tuple[str, dict]], event_type: str) -> list[dict]:
    return [p for et, p in captured if et == event_type]


# ═══════════════════════════════════════════════════════════════════════════════
# AC1 + AC2 — phase_45_spec_writer_complete emitted once, phase key matches
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("module, write_fn, gate_fn, phase_label", BOTH_PHASES)
def test_ac1_writer_complete_emitted_once(
    module, write_fn, gate_fn, phase_label, tmp_path, monkeypatch
):
    """AC1: phase_45_spec_writer_complete emitted exactly once per _write_review_doc call.
    AC2 (covered here): payload phase key matches producing file."""
    prev = _make_write_prev(tmp_path, _RAW_SHIP, cycle=1)
    captured = _patch_emit(monkeypatch, module)

    write_fn(None, prev)

    events = _events_of(captured, "phase_45_spec_writer_complete")
    assert len(events) == 1, (
        f"[{phase_label}] expected 1 phase_45_spec_writer_complete, got {len(events)}; "
        f"all events: {[(et, p) for et, p in captured]}"
    )
    payload = events[0]
    # AC2: phase discriminator
    assert payload.get("phase") == phase_label, (
        f"[{phase_label}] payload['phase'] should be {phase_label!r}, got {payload.get('phase')!r}"
    )
    # AC1: required keys present
    for key in ("cycle", "response_bytes", "duration_ms"):
        assert key in payload, (
            f"[{phase_label}] missing key {key!r} in phase_45_spec_writer_complete payload: {payload}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# AC3 + AC4 — phase_45_spec_review_complete emitted once per _write_review_doc path
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("module, write_fn, gate_fn, phase_label", BOTH_PHASES)
def test_ac3_review_complete_emitted_once_cycle1_ship(
    module, write_fn, gate_fn, phase_label, tmp_path, monkeypatch
):
    """AC3 (cycle-1 free-form SHIP path): exactly one phase_45_spec_review_complete."""
    prev = _make_write_prev(tmp_path, _RAW_SHIP, cycle=1)
    captured = _patch_emit(monkeypatch, module)

    write_fn(None, prev)

    events = _events_of(captured, "phase_45_spec_review_complete")
    assert len(events) == 1, (
        f"[{phase_label}] expected 1 phase_45_spec_review_complete on cycle-1 SHIP, "
        f"got {len(events)}; all: {[(et, p) for et, p in captured]}"
    )


@pytest.mark.parametrize("module, write_fn, gate_fn, phase_label", BOTH_PHASES)
def test_ac3_review_complete_emitted_once_cycle1_revise(
    module, write_fn, gate_fn, phase_label, tmp_path, monkeypatch
):
    """AC3 (cycle-1 REVISE path): exactly one phase_45_spec_review_complete."""
    prev = _make_write_prev(tmp_path, _RAW_REVISE, cycle=1)
    captured = _patch_emit(monkeypatch, module)

    write_fn(None, prev)

    events = _events_of(captured, "phase_45_spec_review_complete")
    assert len(events) == 1, (
        f"[{phase_label}] expected 1 phase_45_spec_review_complete on cycle-1 REVISE, "
        f"got {len(events)}; all: {[(et, p) for et, p in captured]}"
    )


@pytest.mark.parametrize("module, write_fn, gate_fn, phase_label", BOTH_PHASES)
def test_ac4_review_complete_payload_keys(
    module, write_fn, gate_fn, phase_label, tmp_path, monkeypatch
):
    """AC4: phase_45_spec_review_complete payload has all required keys."""
    prev = _make_write_prev(tmp_path, _RAW_SHIP, cycle=1)
    captured = _patch_emit(monkeypatch, module)

    write_fn(None, prev)

    events = _events_of(captured, "phase_45_spec_review_complete")
    assert len(events) >= 1, (
        f"[{phase_label}] no phase_45_spec_review_complete emitted; "
        f"all: {[(et, p) for et, p in captured]}"
    )
    payload = events[0]
    for key in ("phase", "cycle", "verdict", "n_findings_total", "n_findings_structured", "duration_ms"):
        assert key in payload, (
            f"[{phase_label}] missing key {key!r} in phase_45_spec_review_complete payload: {payload}"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# AC5 — phase_45_spec_ship emitted on SHIP branch (both files)
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("module, write_fn, gate_fn, phase_label", BOTH_PHASES)
def test_ac5_ship_event_emitted_on_ship_verdict(
    module, write_fn, gate_fn, phase_label, tmp_path, monkeypatch
):
    """AC5: phase_45_spec_ship emitted exactly once when gate receives SHIP verdict."""
    prev = _make_gate_prev(tmp_path, VERDICT_SHIP, cycle=1)
    captured = _patch_emit(monkeypatch, module)

    gate_fn(None, prev)

    events = _events_of(captured, "phase_45_spec_ship")
    assert len(events) == 1, (
        f"[{phase_label}] expected 1 phase_45_spec_ship on SHIP verdict, "
        f"got {len(events)}; all: {[(et, p) for et, p in captured]}"
    )
    payload = events[0]
    assert payload.get("phase") == phase_label, (
        f"[{phase_label}] phase_45_spec_ship payload['phase'] should be {phase_label!r}, "
        f"got {payload.get('phase')!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC6 — phase_45_spec_ship NOT emitted on REVISE (negative case)
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("module, write_fn, gate_fn, phase_label", BOTH_PHASES)
def test_ac6_ship_not_emitted_on_revise(
    module, write_fn, gate_fn, phase_label, tmp_path, monkeypatch
):
    """AC6 (negative): phase_45_spec_ship NOT emitted when verdict is REVISE."""
    prev = _make_gate_prev(tmp_path, VERDICT_REVISE, cycle=1, raw_review=_RAW_REVISE)
    captured = _patch_emit(monkeypatch, module)

    gate_fn(None, prev)

    ship_events = _events_of(captured, "phase_45_spec_ship")
    assert len(ship_events) == 0, (
        f"[{phase_label}] phase_45_spec_ship should NOT emit on REVISE, "
        f"got {len(ship_events)} events; all: {[(et, p) for et, p in captured]}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC7 — phase_45_spec_revise emitted on REVISE+cycle<cap (both files)
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("module, write_fn, gate_fn, phase_label", BOTH_PHASES)
def test_ac7_revise_event_emitted_on_revise_retry(
    module, write_fn, gate_fn, phase_label, tmp_path, monkeypatch
):
    """AC7: phase_45_spec_revise emitted exactly once on REVISE+cycle<cap."""
    prev = _make_gate_prev(tmp_path, VERDICT_REVISE, cycle=1, raw_review=_RAW_REVISE)
    captured = _patch_emit(monkeypatch, module)

    gate_fn(None, prev)

    events = _events_of(captured, "phase_45_spec_revise")
    assert len(events) == 1, (
        f"[{phase_label}] expected 1 phase_45_spec_revise on REVISE+cycle=1<cap, "
        f"got {len(events)}; all: {[(et, p) for et, p in captured]}"
    )
    payload = events[0]
    assert payload.get("phase") == phase_label, (
        f"[{phase_label}] phase_45_spec_revise payload['phase'] mismatch: {payload.get('phase')!r}"
    )
    assert payload.get("cycle") == 1, (
        f"[{phase_label}] phase_45_spec_revise payload['cycle'] should be 1, got {payload.get('cycle')!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC8 — phase_45_spec_abort on REVISE-at-cap; cap_reached=True (both files)
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("module, write_fn, gate_fn, phase_label", BOTH_PHASES)
def test_ac8_abort_emitted_on_revise_at_cap(
    module, write_fn, gate_fn, phase_label, tmp_path, monkeypatch
):
    """AC8: phase_45_spec_abort emitted exactly once on REVISE+cycle==cap; cap_reached=True.

    GH625: phase_45_spec.py's _gate_on_review (spec_full) now decides cap via
    per-gate gate_attempts (spec_retry cap=1, recoverable_once), not raw
    cycle (phase_45_spec.py:3116); seed 1 prior attempt (== cap) so it still
    terminates. phase_45_spec_lite's _gate_on_review is untouched (still
    `cycle < MAX_REVIEW_CYCLES`), so the seed is a no-op there.
    """
    cap_cycle = MAX_REVIEW_CYCLES
    _gate_attempts = {"spec_retry": 1} if phase_label == "phase_45_spec" else None
    prev = _make_gate_prev(
        tmp_path, VERDICT_REVISE, cycle=cap_cycle, raw_review=_RAW_REVISE,
        gate_attempts=_gate_attempts,
    )
    captured = _patch_emit(monkeypatch, module)

    gate_fn(None, prev)

    events = _events_of(captured, "phase_45_spec_abort")
    assert len(events) == 1, (
        f"[{phase_label}] expected 1 phase_45_spec_abort on REVISE+cycle=cap, "
        f"got {len(events)}; all: {[(et, p) for et, p in captured]}"
    )
    payload = events[0]
    assert payload.get("cap_reached") is True, (
        f"[{phase_label}] phase_45_spec_abort payload['cap_reached'] should be True, "
        f"got {payload.get('cap_reached')!r}"
    )
    assert payload.get("terminal_reason") == "cap_reached", (
        f"[{phase_label}] phase_45_spec_abort payload['terminal_reason'] should be 'cap_reached', "
        f"got {payload.get('terminal_reason')!r}"
    )
    assert payload.get("phase") == phase_label, (
        f"[{phase_label}] phase_45_spec_abort payload['phase'] mismatch: {payload.get('phase')!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC9 — phase_45_spec_abort on UNKNOWN+empty; cap_reached=False (both files)
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("module, write_fn, gate_fn, phase_label", BOTH_PHASES)
def test_ac9_abort_emitted_on_unknown_empty(
    module, write_fn, gate_fn, phase_label, tmp_path, monkeypatch
):
    """AC9: phase_45_spec_abort emitted on UNKNOWN+empty-review; cap_reached=False, terminal_reason='unparseable'."""
    # UNKNOWN + empty raw_review triggers the E_REVIEW_UNPARSEABLE terminal branch
    prev = _make_gate_prev(tmp_path, VERDICT_UNKNOWN, cycle=1, raw_review="   ")
    captured = _patch_emit(monkeypatch, module)

    gate_fn(None, prev)

    events = _events_of(captured, "phase_45_spec_abort")
    assert len(events) == 1, (
        f"[{phase_label}] expected 1 phase_45_spec_abort on UNKNOWN+empty review, "
        f"got {len(events)}; all: {[(et, p) for et, p in captured]}"
    )
    payload = events[0]
    assert payload.get("cap_reached") is False, (
        f"[{phase_label}] phase_45_spec_abort payload['cap_reached'] should be False, "
        f"got {payload.get('cap_reached')!r}"
    )
    assert payload.get("terminal_reason") == "unparseable", (
        f"[{phase_label}] phase_45_spec_abort payload['terminal_reason'] should be 'unparseable', "
        f"got {payload.get('terminal_reason')!r}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC10 — phase_45_spec_abort NOT emitted on REVISE+cycle<cap (negative case)
# ═══════════════════════════════════════════════════════════════════════════════


@pytest.mark.parametrize("module, write_fn, gate_fn, phase_label", BOTH_PHASES)
def test_ac10_abort_not_emitted_on_revise_retry(
    module, write_fn, gate_fn, phase_label, tmp_path, monkeypatch
):
    """AC10 (negative): phase_45_spec_abort NOT emitted on REVISE+cycle<cap."""
    prev = _make_gate_prev(tmp_path, VERDICT_REVISE, cycle=1, raw_review=_RAW_REVISE)
    captured = _patch_emit(monkeypatch, module)

    gate_fn(None, prev)

    abort_events = _events_of(captured, "phase_45_spec_abort")
    assert len(abort_events) == 0, (
        f"[{phase_label}] phase_45_spec_abort should NOT emit on REVISE+cycle<cap, "
        f"got {len(abort_events)} events; all: {[(et, p) for et, p in captured]}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC11 — Forcing-function: exact n_findings_unresolved from _truncate_findings output
# (spec_lite only — the spec mandates this for phase_45_spec_lite._gate_on_review)
# ═══════════════════════════════════════════════════════════════════════════════


def test_ac11_revise_payload_exact_n_findings_unresolved(tmp_path, monkeypatch):
    """AC11 (forcing-function): n_findings_unresolved in phase_45_spec_revise payload
    equals the count _truncate_findings returns for the given raw_review input.
    Derived deterministically — not hardcoded."""
    raw_review = "finding1\nfinding2\nfinding3\n"
    prev = _make_gate_prev(
        tmp_path, VERDICT_REVISE, cycle=1, raw_review=raw_review
    )
    captured = _patch_emit(monkeypatch, phase_45_spec_lite)

    _gate_on_review_lite(None, prev)

    events = _events_of(captured, "phase_45_spec_revise")
    assert len(events) == 1, (
        f"expected 1 phase_45_spec_revise, got {len(events)}; "
        f"all: {[(et, p) for et, p in captured]}"
    )
    payload = events[0]

    # Derive expected N the same way GREEN will: _truncate_findings returns the
    # (possibly-truncated) text; GREEN counts structured findings from it if
    # parseable (which free-form text is not) → falls back to 0 per spec design note.
    # For this raw_review (non-JSON, non-empty) the spec says:
    # "len of structured findings if parseable, else 0 (graceful)"
    # So expected = 0 when text is freetext (not parseable as JSON findings).
    #
    # We derive by calling _truncate_findings and then checking if the result
    # contains parseable structured findings. Since the raw is freetext, the
    # result is 0. We express this as a deterministic computation, not a literal.
    truncated_text, _was_truncated = _truncate_findings(raw_review)
    # Attempt structured parse to mimic what GREEN will do
    try:
        from bytedigger_engine.lib.plugins.checklist_convergence import extract_structured_findings
        parsed = extract_structured_findings(truncated_text)
        expected_n = len(parsed) if parsed is not None else 0
    except Exception:
        expected_n = 0

    assert payload == {
        "phase": "phase_45_spec_lite",
        "cycle": 1,
        "n_findings_unresolved": expected_n,
    }, (
        f"phase_45_spec_revise payload mismatch.\n"
        f"Expected: {{'phase': 'phase_45_spec_lite', 'cycle': 1, 'n_findings_unresolved': {expected_n}}}\n"
        f"Got: {payload}"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# AC12 — Additive guarantee: spec_lite_cycle2_abort STILL fires at cap
#         alongside the new phase_45_spec_abort (spec_lite only)
# ═══════════════════════════════════════════════════════════════════════════════


def test_ac12_both_abort_events_fire_at_cap_spec_lite(tmp_path, monkeypatch):
    """AC12: on REVISE+cycle==cap in spec_lite, BOTH spec_lite_cycle2_abort
    (existing, line 1012) AND phase_45_spec_abort (new) are present in captured events."""
    cap_cycle = MAX_REVIEW_CYCLES
    prev = _make_gate_prev(tmp_path, VERDICT_REVISE, cycle=cap_cycle, raw_review=_RAW_REVISE)
    captured = _patch_emit(monkeypatch, phase_45_spec_lite)

    _gate_on_review_lite(None, prev)

    legacy_events = _events_of(captured, "spec_lite_cycle2_abort")
    new_events = _events_of(captured, "phase_45_spec_abort")

    assert len(legacy_events) >= 1, (
        f"spec_lite_cycle2_abort (existing event) should still fire at cap; "
        f"all captured: {[(et, p) for et, p in captured]}"
    )
    assert len(new_events) == 1, (
        f"phase_45_spec_abort (new event) should also fire at cap; "
        f"all captured: {[(et, p) for et, p in captured]}"
    )
