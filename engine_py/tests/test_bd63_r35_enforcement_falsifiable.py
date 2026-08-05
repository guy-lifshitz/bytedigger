"""bd#63 — R3.5: the enforcement claim becomes refutable.

Spec: `docs/decisions/2026-08-04-bd63-r35-enforcement-falsifiable.md`.

Class: the actor grades itself. `capability_enforcement` comes from a registry
filled in by the backend itself (`register_backend(..., capabilities=...)`).
An enforcement claim issued by the enforced is indistinguishable from a polite lie.

The instrument for refuting it ALREADY exists and needs no host instrumentation: the attestation
payload carries both the claim (`capability_enforcement`) and the evidence
(`observed_tools`, `declared_capabilities`) — in ONE event. A backend that
claimed `runtime-allowlist` and exhibited an escape is refuted by its own
record.

AC3 is not decoration: if an escape is punished even for one who promised nothing,
an honest declaration becomes costlier than a false one. That inversion of incentive is worse than the original
defect, so it gets a leg of its own.
"""
from __future__ import annotations

CLAIM = "runtime-allowlist"
NO_CLAIM = "not-enforced"
CODE = "E_CAPABILITY_ENFORCEMENT_UNSUBSTANTIATED"


def _bd_l3():
    from bytedigger_engine.conformance import bd_l3  # noqa: PLC0415

    return bd_l3


def _tok():
    from bytedigger_engine.conformance import tokens  # noqa: PLC0415

    return tokens


def _ev(*, enforcement, declared, observed):
    from bytedigger_engine.conformance import attest  # noqa: PLC0415

    return {"type": attest.EVENT_TYPE, "payload": {
        "step_name": "s1",
        "backend": "some-backend",
        "model_requested": "sonnet",
        "observed_model": None,
        "capability_enforcement": enforcement,
        "declared_capabilities": declared,
        "observed_tools": observed,
    }}


# ─── AC1: THE HEART — the input on which the mechanism says NO to a self-declarer ─

def test_ac1_declared_enforcement_refuted_by_its_own_escape():
    report = _bd_l3().check_bd_l3([_ev(
        enforcement=CLAIM, declared=["Read"], observed=["bash"])])

    assert report.labels["verdict:R3.5"] == _tok().REQUIREMENT_FAILED, (
        "the backend claimed enforcement and in the same record exhibited an escape — the claim "
        "is refuted by its own evidence"
    )
    assert any(CODE in v for v in report.violations), (
        f"a violation must name a code; got {report.violations!r}"
    )


# ─── AC2: the positive leg ────────────────────────────────────────────────

def test_ac2_declared_enforcement_without_escapes_passes():
    report = _bd_l3().check_bd_l3([_ev(
        enforcement=CLAIM, declared=["Read"], observed=["Read"])])

    assert report.labels["verdict:R3.5"] == _tok().REQUIREMENT_PASSED


# ─── AC3: NEGATIVE — honesty is not punished ─────────────────────────────

def test_ac3_an_honest_backend_is_not_punished_for_the_escape():
    """`not-enforced` promised nothing: its escape is caught by R3.6, not R3.5.

    Otherwise an honest declaration would become costlier than a false one — an inversion of incentive.
    """
    t = _tok()
    report = _bd_l3().check_bd_l3([_ev(
        enforcement=NO_CLAIM, declared=["Read"], observed=["bash"])])

    assert report.labels["verdict:R3.5"] != t.REQUIREMENT_FAILED, (
        "a backend that never claimed enforcement cannot violate R3.5"
    )
    assert report.labels["verdict:R3.6"] == t.REQUIREMENT_FAILED, (
        "the escape must stay caught — R3.6 has not gone anywhere"
    )


# ─── AC4: no evidence — not passed ───────────────────────────────────────

def test_ac4_claim_without_evidence_is_not_checked():
    """Silently crediting an unconfirmed claim is a return to self-assessment."""
    report = _bd_l3().check_bd_l3([_ev(
        enforcement=CLAIM, declared=None, observed=None)])

    assert report.labels["verdict:R3.5"] == _tok().REQUIREMENT_NOT_CHECKED


# ─── AC5: the registry measurement is pinned ─────────────────────────────

def test_ac5_measured_backend_declarations_are_pinned():
    """Registry drift must fail an AC rather than slide past in silence."""
    from bytedigger_engine import llm_subprocess as L  # noqa: PLC0415

    assert L._capability_enforcement("claude-subprocess") == CLAIM
    assert L._capability_enforcement("claude-in-session") == NO_CLAIM


# ─── AC6: ADV-10 does not rest on self-description ───────────────────────

def test_ac6_adv10_does_not_read_the_self_declaration():
    """If the probe read the claim, level BD-L3 would rest on it."""
    import inspect  # noqa: PLC0415

    from bytedigger_engine.conformance import harness  # noqa: PLC0415

    src = inspect.getsource(harness._adv_10)
    assert "capability_enforcement" not in src, (
        "the ADV-10 probe must rest on observation, not on self-description"
    )
    assert harness.run_adversaries()["ADV-10"] == harness.OUTCOME_DEFENDED


# ─── AC7/AC8: the code and the surface ───────────────────────────────────

def test_ac7_error_code_registered():
    from bytedigger_engine.error_codes import ERROR_CODES  # noqa: PLC0415

    assert CODE in ERROR_CODES


def test_ac8_requirements_grew_deliberately():
    bd_l3 = _bd_l3()
    assert "R3.5" in bd_l3.REQUIREMENTS
    public = {n for n in vars(bd_l3) if not n.startswith("_")}
    assert public == set(bd_l3.__all__)
