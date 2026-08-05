"""bd#59 — enforcement: a declared correspondence "verdict ⇄ production refusal".

Spec: `docs/decisions/2026-08-04-bd59-enforcement-map.md`.

Class: a duplicate guard. The exposure measurement (mandated by the issue BEFORE RED)
showed that a production refusal ALREADY exists for all three observable requirements:
R2.2 — `E_RED_STUB_PASSABLE` (recoverable=False, 6 sites, the gate is on by
default); R2.1 — `E_RED_COLLECT_PROBE` (recoverable=True, the enforcement flag
defaults to 0); R2.6 — blocking of `_baseline_delta` (flag default=0).

⇒ Wiring the L2 verdicts into the phases would mean placing a SECOND guard on an already
guarded condition. The price is known by name from bd#29 §5a: the later guard
overwrites the earlier one's code, and the caller receives the wrong cause.

What is genuinely missing is a DECLARED link "requirement → (code, flag, whether it is
on)". Today "there is no refusal" and "there is a refusal, but it is off" look the same, and
that is exactly what makes enforcement unverifiable.
"""
from __future__ import annotations


def _bd_l2():
    from bytedigger_engine.conformance import bd_l2  # noqa: PLC0415

    return bd_l2


# ─── AC1/AC3: the registry and reality do not diverge ────────────────────

def test_ac1_every_observable_requirement_is_bound_to_a_consequence():
    bd_l2 = _bd_l2()
    observable = set(bd_l2.REQUIREMENTS) - set(bd_l2.AWAITING_PRODUCER)
    missing = sorted(observable - set(bd_l2.ENFORCEMENT))
    assert not missing, (
        f"the requirement is observable but the consequence is not declared: {missing!r} — "
        "a verdict without a declared refusal is unverifiable"
    )


def test_ac3_unobservable_requirements_declare_no_consequence():
    """NEGATIVE LEG: one may not declare a consequence for what one does not observe."""
    bd_l2 = _bd_l2()
    overreach = sorted(set(bd_l2.AWAITING_PRODUCER) & set(bd_l2.ENFORCEMENT))
    assert not overreach, (
        f"a consequence is declared for a requirement with no producer: {overreach!r}"
    )


# ─── AC2: the record must name an EXISTING code and flag ─────────────────

def test_ac2_every_entry_names_a_real_code_and_a_real_flag():
    """NEGATIVE LEG. A reference to an invented name must fail the gate — this is
    exactly the mistake I made twice: in bd#9 with event names and in
    bd#59 with payload keys. Both times everything was green, because nobody
    checked the declaration against the registry."""
    from bytedigger_engine.error_codes import ERROR_CODES  # noqa: PLC0415
    from bytedigger_engine import flags_catalog  # noqa: PLC0415

    bd_l2 = _bd_l2()
    flags = set(flags_catalog.FLAGS)

    for req, entry in bd_l2.ENFORCEMENT.items():
        assert entry["error_code"] in ERROR_CODES, (
            f"{req}: code {entry['error_code']!r} is absent from the error_codes registry"
        )
        assert entry["flag"] in flags, (
            f"{req}: flag {entry['flag']!r} is absent from flags_catalog"
        )
        assert isinstance(entry["enforced_by_default"], bool)


# ─── AC4/AC5: THREE states are distinguishable ───────────────────────────

def test_ac4_three_enforcement_states_are_distinguishable():
    """Refusal on · refusal exists but is off · no refusal — three labels."""
    bd_l2 = _bd_l2()
    report = bd_l2.check_bd_l2([])

    enforced = report.labels["enforcement:R2.2"]
    declared_off = report.labels["enforcement:R2.1"]
    absent = report.labels["enforcement:R2.5"]

    assert len({enforced, declared_off, absent}) == 3, (
        f"the three states must be distinguishable, got "
        f"{enforced!r} / {declared_off!r} / {absent!r}"
    )


def test_ac5_a_disabled_consequence_never_reads_as_enforced():
    """NEGATIVE LEG: a disabled refusal has no right to look enabled."""
    bd_l2 = _bd_l2()
    report = bd_l2.check_bd_l2([])

    assert report.labels["enforcement:R2.1"] != report.labels["enforcement:R2.2"], (
        "R2.1 is enforced only under a flag with default=0, R2.2 by default; "
        "an identical label erases the difference"
    )


# ─── AC6: the measurement is pinned and must fail on drift ───────────────

def test_ac6_measured_defaults_are_pinned():
    """If the flag's value in the catalog changes, this AC fails and demands a
    decision rather than sliding past in silence."""
    bd_l2 = _bd_l2()

    assert bd_l2.ENFORCEMENT["R2.2"]["enforced_by_default"] is True
    assert bd_l2.ENFORCEMENT["R2.1"]["enforced_by_default"] is False
    assert bd_l2.ENFORCEMENT["R2.6"]["enforced_by_default"] is False


def test_ac7_public_surface_equals_dunder_all():
    bd_l2 = _bd_l2()
    public = {n for n in vars(bd_l2) if not n.startswith("_")}
    assert public == set(bd_l2.__all__)
