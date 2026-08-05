"""bd#58 — the adversary execution harness + attestation publication.

Spec: `docs/decisions/2026-08-04-bd58-adversary-harness-attestation.md`.
The frozen levels spec: `2026-07-26_bytedigger_conformance_levels.md`
(HAL `fd35e1304`), §4 (Attestation output), §8 (last paragraph), §9 step 1.

Class: a shield that is green because nobody ever raised it. `bd_l2` and `bd_l3` can
say NO — proved by their negative legs. But not a single adversary is run against
the host itself, so on a real log every verdict is
`not-checked`, and there is no publisher at all. §8 forbids exactly this: "a level
is claimed only on the strength of adversaries actually executed; an implementation that silently
counts an unexecuted adversary as passed is itself a conformance failure".

Hence four negative legs in a row here — one per way of NOT defeating
an adversary (`not_executed`, `undefended`, `errored`, a claim above the fact). A grant
issued under any of them is the same inverted oracle one level up.

`conformance.harness` does NOT EXIST on this base; every test fails inside its own
body (the bd#24 discipline — deferred imports, collection stays clean).
"""
from __future__ import annotations


def _h():
    from bytedigger_engine.conformance import harness  # noqa: PLC0415

    return harness


def _tok():
    from bytedigger_engine.conformance import tokens  # noqa: PLC0415

    return tokens


def _attestation(outcomes, *, level_claimed="BD-L2"):
    h = _h()
    return h.build_attestation(
        outcomes,
        level_claimed=level_claimed,
        engine_version="0.0.0-test",
        adapter_identity="test-adapter",
        host_identity="test-host",
        timestamp="2026-08-04T00:00:00Z",
    )


def _all_defended():
    """Every executable adversary is defeated — the common point of reference."""
    h = _h()
    return {name: h.OUTCOME_DEFENDED for name in h.ADVERSARIES}


# ─── AC1: execution is REAL, not a constant ───────────────────────────────

def test_ac1_run_adversaries_covers_the_set_and_actually_defends_one():
    h = _h()
    outcomes = h.run_adversaries()

    assert set(outcomes) == set(h.ADVERSARIES), (
        f"every adversary must have an outcome; missing "
        f"{sorted(set(h.ADVERSARIES) - set(outcomes))!r}"
    )
    assert h.OUTCOME_DEFENDED in outcomes.values(), (
        f"not a single adversary was defeated — a harness that executes nothing is "
        f"precisely the defect this lot closes; got {outcomes!r}"
    )


# ─── AC2-AC4: THREE WAYS TO FAIL, each must revoke the level ─────────────

def test_ac2_not_executed_adversary_removes_the_level():
    """§8: an unexecuted adversary credited as passed is a conformance failure."""
    h, t = _h(), _tok()
    outcomes = _all_defended()
    outcomes["ADV-3"] = t.ADVERSARY_NOT_EXECUTED
    att = _attestation(outcomes)

    assert att["level_achieved"] != "BD-L2", (
        f"BD-L2 granted with ADV-3 unexecuted: {att['level_achieved']!r}"
    )
    assert h.validate_attestation(att), "the BD-L2 claim must be rejected"


def test_ac3_undefended_adversary_removes_the_level():
    h = _h()
    outcomes = _all_defended()
    outcomes["ADV-3"] = h.OUTCOME_UNDEFENDED
    att = _attestation(outcomes)

    assert att["level_achieved"] != "BD-L2"
    assert h.validate_attestation(att)


def test_ac4_errored_adversary_is_not_passed_fail_closed():
    """An execution that raised an exception is NOT defeated, not "absent".

    The spirit of R2.4: a guard that reached no verdict refuses. A harness that counts
    its own failure as an absence of a check reproduces fail-open on exactly
    the layer built to close it.
    """
    h = _h()
    outcomes = _all_defended()
    outcomes["ADV-3"] = h.OUTCOME_ERRORED
    att = _attestation(outcomes)

    assert att["level_achieved"] != "BD-L2"
    assert h.validate_attestation(att)


# ─── AC5: a claim above the fact is rejected ──────────────────────────────

def test_ac5_claim_above_achievement_is_rejected():
    h, t = _h(), _tok()
    outcomes = {name: t.ADVERSARY_NOT_EXECUTED for name in h.ADVERSARIES}
    att = _attestation(outcomes, level_claimed="BD-L3")

    complaints = h.validate_attestation(att)
    assert complaints, (
        "a claimed level above the achieved one must be rejected by the publisher"
    )


# ─── AC6: cumulativeness ──────────────────────────────────────────────────

def test_ac6_level_is_cumulative_l1_failure_sinks_l2():
    """All L2 adversaries defeated, but L1 failed ⇒ BD-L2 is not granted."""
    h = _h()
    outcomes = _all_defended()
    outcomes["ADV-1"] = h.OUTCOME_UNDEFENDED
    att = _attestation(outcomes)

    assert att["level_achieved"] not in ("BD-L1", "BD-L2", "BD-L3"), (
        f"the level is cumulative: an L1 failure must revoke L2, got "
        f"{att['level_achieved']!r}"
    )


# ─── AC7: ADV-9 is present and declared ───────────────────────────────────

def test_ac7_adv9_is_present_and_declared_not_executed():
    """Silence about ADV-9 is the very form §8 forbids: it is
    declarative, and that must be VISIBLE rather than expressed by a missing key."""
    att = _attestation(_all_defended())

    assert "ADV-9" in att["adversaries"], "ADV-9 must be present in the report"
    assert att["adversaries"]["ADV-9"] == _tok().ADVERSARY_NOT_EXECUTED


# ─── AC8/AC9: the §4 schema and the judge ─────────────────────────────────

def test_ac8_attestation_carries_exactly_the_frozen_schema():
    att = _attestation(_all_defended())
    assert set(att) == {
        "level_claimed", "level_achieved", "adversaries", "engine_version",
        "adapter_identity", "host_identity", "timestamp",
    }, f"the §4 schema diverged: {sorted(att)!r}"


def test_ac9_validate_attestation_is_silent_on_a_consistent_one():
    """A judge that always complains is as useless as one that is always silent."""
    h = _h()
    att = _attestation(_all_defended(), level_claimed="BD-L3")
    assert h.validate_attestation(att) == (), (
        f"a consistent attestation must draw no complaint; got "
        f"{h.validate_attestation(att)!r}"
    )


# ─── AC10/AC11: the surface and determinism ───────────────────────────────

def test_ac10_public_surface_equals_dunder_all():
    h = _h()
    public = {n for n in vars(h) if not n.startswith("_")}
    assert public == set(h.__all__), (
        f"the surface diverged from __all__: extra "
        f"{sorted(public - set(h.__all__))!r}"
    )


def test_ac11_run_adversaries_is_deterministic():
    """A harness whose verdict jumps between runs is not an instrument."""
    h = _h()
    assert h.run_adversaries() == h.run_adversaries()
