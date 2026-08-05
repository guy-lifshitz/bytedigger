"""bd#59 — the BD-L2 observation contract: the events that are actually emitted.

Spec: `docs/decisions/2026-08-04-bd59-l2-observation-contract.md`.

Class: an observation with no producer. The exposure measurement, mandated by the issue
BEFORE RED, showed: five of the six BD-L2 requirements lack not only a consequence but
an OBSERVATION — nobody emits the events `bd_l2` reads, and the two
remaining ones are emitted with DIFFERENT keys. On a real log `bd_l2` renders not
a single verdict.

THIS IS AGAINST MY OWN bd#9. `_EVENT_FOR` was written from names invented in that lot's
fixtures; all 16 ACs passed, because every fixture was
synthetic — the checker adjudicated exactly the form I fed it.
The bd#9 negative legs are sound (the function can say NO), but a §1l anchor to
real production emission was missing there. It is added here, and AC1 is precisely the
gate whose absence let the defect through.
"""
from __future__ import annotations

from pathlib import Path

_PROD_ROOT = Path(__file__).resolve().parents[1] / "bytedigger_engine"


def _bd_l2():
    from bytedigger_engine.conformance import bd_l2  # noqa: PLC0415

    return bd_l2


def _tok():
    from bytedigger_engine.conformance import tokens  # noqa: PLC0415

    return tokens


def _emitters(event_type: str) -> list[str]:
    """Production files emitting `event_type` (outside `conformance/` — those are consumers)."""
    needle = f'"{event_type}"'
    found = []
    for path in _PROD_ROOT.rglob("*.py"):
        if "conformance" in path.parts or "__pycache__" in path.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if f"_emit_safe({needle}" in text or f"emit({needle}" in text:
            found.append(path.name)
    return found


# ─── AC1: THE GATE AGAINST A RELAPSE ──────────────────────────────────────

def test_ac1_every_observed_requirement_has_a_production_emitter():
    """A requirement declared observable must have a producer.

    Without this AC the checker can adjudicate for years a form that does not exist in production and
    stay green — which is exactly what happened in bd#9. A requirement without
    a producer is not forbidden: it must be DECLARED in the pending
    registry rather than stay silent.
    """
    bd_l2 = _bd_l2()

    observed = set(bd_l2._EVENT_FOR) - set(bd_l2.AWAITING_PRODUCER)
    missing = {
        req: bd_l2._EVENT_FOR[req]
        for req in sorted(observed)
        if not _emitters(bd_l2._EVENT_FOR[req])
    }
    assert not missing, (
        f"requirements are declared observable, but there is no producer of the event: "
        f"{missing!r}. Either an emitter appears, or the requirement must be in "
        f"AWAITING_PRODUCER."
    )

    stale = {
        req for req in bd_l2.AWAITING_PRODUCER
        if _emitters(bd_l2._EVENT_FOR.get(req, ""))
    }
    assert not stale, (
        f"a producer has appeared, but the requirement is still listed as pending: "
        f"{sorted(stale)!r} — the registry must drain, otherwise it turns into "
        f"a permanent excuse"
    )


# ─── AC2/AC3: R2.1 on the REAL payload (without `counted_as`) ────────────

def _real_red_outcome(*, n_passed, n_failed, exit_code):
    """The form emitted by `phase_5_implement.py:2635` — exactly these keys."""
    return {"type": "red_test_outcome", "payload": {
        "group": "py", "exit_code": exit_code, "n_passed": n_passed,
        "n_failed": n_failed, "phase": 5,
    }}


def test_ac2_zero_collected_on_the_real_payload_is_not_a_rejection():
    """The discriminator must work WITHOUT the invented key `counted_as`."""
    report = _bd_l2().check_bd_l2([_real_red_outcome(
        n_passed=0, n_failed=0, exit_code=5)])

    assert report.labels["verdict:R2.1"] == _tok().REQUIREMENT_FAILED, (
        "a run that collected zero tests is a no-op, not a refusal; "
        "the discriminator must read the keys actually emitted"
    )


def test_ac3_real_rejection_passes():
    """NEGATIVE LEG: a "fix" that fails everything indiscriminately is green on AC2."""
    report = _bd_l2().check_bd_l2([_real_red_outcome(
        n_passed=0, n_failed=3, exit_code=1)])

    assert report.labels["verdict:R2.1"] == _tok().REQUIREMENT_PASSED


# ─── AC4/AC5: R2.6 on the REAL payload ────────────────────────────────────

def _real_delta_verdict(*, verdict, n_new_fails, enforced=True):
    """The form of `_baseline_delta.py:92` — exactly these keys."""
    return {"type": "baseline_delta_gate_verdict", "payload": {
        "suite": "py", "verdict": verdict, "new_fails": [],
        "n_new_fails": n_new_fails, "ledgered": [],
        "baseline_source": "declared", "enforced": enforced, "phase": 5,
    }}


def test_ac4_missing_baseline_source_is_not_a_measured_delta():
    report = _bd_l2().check_bd_l2([{
        "type": "baseline_delta_gate_verdict", "payload": {
            "suite": "py", "verdict": "PASS", "new_fails": [],
            "n_new_fails": 0, "ledgered": [], "baseline_source": None,
            "enforced": True, "phase": 5,
        }}])

    assert report.labels["verdict:R2.6"] != _tok().REQUIREMENT_PASSED, (
        "without a declared baseline the delta is not measured against it"
    )


def test_ac5_real_measured_delta_passes():
    """NEGATIVE LEG for R2.6."""
    report = _bd_l2().check_bd_l2([_real_delta_verdict(
        verdict="PASS", n_new_fails=0)])

    assert report.labels["verdict:R2.6"] == _tok().REQUIREMENT_PASSED


# ─── AC6: the declared gap, both sides ────────────────────────────────────

def test_ac6_awaiting_producer_is_declared_and_yields_not_checked():
    """The gap must be DECLARED rather than expressed by silence."""
    bd_l2, t = _bd_l2(), _tok()

    assert bd_l2.AWAITING_PRODUCER, (
        "on this base four requirements have no producer — the registry cannot "
        "be empty"
    )
    for req in bd_l2.AWAITING_PRODUCER:
        assert req in bd_l2.REQUIREMENTS
        report = bd_l2.check_bd_l2([])
        assert report.labels[f"verdict:{req}"] == t.REQUIREMENT_NOT_CHECKED


# ─── AC7: the surface ─────────────────────────────────────────────────────

def test_ac7_public_surface_equals_dunder_all():
    bd_l2 = _bd_l2()
    public = {n for n in vars(bd_l2) if not n.startswith("_")}
    assert public == set(bd_l2.__all__), (
        f"the surface diverged from __all__: extra "
        f"{sorted(public - set(bd_l2.__all__))!r}"
    )
