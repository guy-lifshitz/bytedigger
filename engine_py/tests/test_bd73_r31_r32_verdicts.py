"""bd#73 — R3.1 and R3.2 get a verdict.

Spec: `docs/decisions/2026-08-05-bd73-r31-r32-verdicts.md`.

Class: declared ≠ checked. `attest.REQUIREMENT_LABELS` declares five
requirements (R3.1, R3.2, R3.3, R3.5, R3.6), `bd_l3.REQUIREMENTS` adjudicates three.
R3.1 and R3.2 carry a label that reads as a verdict, and the report's silence about them is
indistinguishable from "conformant". The fourth variety of the family after bd#59,
bd#68 and bd#71.

THE HONEST BOUNDARY OF R3.2 (AC8): the chokepoint validates injections BEFORE the dispatch and on
refusal emits nothing, so an unattributed block never reaches the log by
construction. The R3.2 verdict on a real log is a CONFIRMATION and a chokepoint
regression guard, not an independent check. Passing it off as independent would
build exactly the green shield we have been dissecting all the way through.
"""
from __future__ import annotations

GOOD_HASH = "sha256:" + "a" * 64


def _bd_l3():
    from bytedigger_engine.conformance import bd_l3  # noqa: PLC0415

    return bd_l3


def _tok():
    from bytedigger_engine.conformance import tokens  # noqa: PLC0415

    return tokens


def _ev(**overrides):
    from bytedigger_engine.conformance import attest  # noqa: PLC0415

    payload = {
        "step_name": "s1",
        "backend": "b",
        "model_requested": "sonnet",
        "prompt_sha256": GOOD_HASH,
        "injections": [{"source_id": "role-template", "sha256": GOOD_HASH}],
        "declared_capabilities": None,
        "capability_enforcement": "not-enforced",
        "observed_model": None,
        "observed_tools": None,
    }
    payload.update(overrides)
    return {"type": attest.EVENT_TYPE, "payload": payload}


def _verdict(req, **overrides):
    return _bd_l3().check_bd_l3([_ev(**overrides)]).labels[f"verdict:{req}"]


# ─── R3.1: the hash of the effective prompt ──────────────────────────────

def test_ac1_valid_prompt_hash_passes():
    assert _verdict("R3.1") == _tok().REQUIREMENT_PASSED


def test_ac2_missing_prompt_hash_is_a_violation():
    """NEGATIVE LEG."""
    assert _verdict("R3.1", prompt_sha256=None) == _tok().REQUIREMENT_FAILED


def test_ac3_malformed_hash_is_a_violation_form_not_presence():
    """NEGATIVE LEG: catches a "the key is there" check.

    `"ok"` would satisfy presence and is not a hash — if the predicate
    pins only the key's presence, this leg is what exposes it.
    """
    assert _verdict("R3.1", prompt_sha256="ok") == _tok().REQUIREMENT_FAILED


# ─── R3.2: injection attribution, BOTH halves of the conjunction ─────────

def test_ac4_unattributed_injection_is_a_violation_both_halves():
    """NEGATIVE LEG x2: `source_id` AND `sha256`.

    A conjunction: checking one half leaves the other unreachable.
    """
    t, bd_l3 = _tok(), _bd_l3()

    no_src = bd_l3.check_bd_l3([_ev(injections=[{"sha256": GOOD_HASH}])])
    assert no_src.labels["verdict:R3.2"] == t.REQUIREMENT_FAILED, "no source_id"
    assert any("E_INJECT_UNATTRIBUTED" in v for v in no_src.violations), (
        f"a violation must name an existing code; {no_src.violations!r}"
    )

    no_hash = bd_l3.check_bd_l3([_ev(injections=[{"source_id": "x"}])])
    assert no_hash.labels["verdict:R3.2"] == t.REQUIREMENT_FAILED, "no sha256"


def test_ac5_fully_attributed_injection_passes():
    assert _verdict("R3.2") == _tok().REQUIREMENT_PASSED


def test_ac6_no_injections_is_not_a_violation():
    """An empty list is legitimate: requiring injections of every step is not our case."""
    assert _verdict("R3.2", injections=[]) != _tok().REQUIREMENT_FAILED


# ─── AC7: THE GATE — every label has a verdict or is declared an exception ─

def test_ac7_every_declared_label_has_a_verdict_or_is_declared_exempt():
    """Both sides. The absence of this gate is what produced the gap: R3.1/R3.2 carried
    a label and had no verdict, and the report stayed silent."""
    from bytedigger_engine.conformance import attest  # noqa: PLC0415

    bd_l3 = _bd_l3()
    labelled = set(attest.REQUIREMENT_LABELS)
    judged = set(bd_l3.REQUIREMENTS)
    exempt = set(bd_l3.LABEL_EXCEPTIONS)

    unjudged = sorted(labelled - judged - exempt)
    assert not unjudged, (
        f"a label is declared, there is no verdict and no exception is claimed: {unjudged!r} — "
        "the report's silence is indistinguishable from \"conformant\""
    )
    stale = sorted(exempt & judged)
    assert not stale, (
        f"a requirement is declared an exception yet is adjudicated: {stale!r} — an exception "
        "must disappear once a verdict appears"
    )


# ─── AC8: the R3.2 boundary is declared in the source, not only in the spec ─

def test_ac8_r32_states_that_it_corroborates_rather_than_checks():
    import inspect  # noqa: PLC0415

    doc = inspect.getdoc(_bd_l3()._r32) or ""
    low = doc.lower()
    assert "corroborat" in low or "подтвержд" in low, (
        "the R3.2 docstring must state that on a real log this is a "
        "confirmation and a regression guard, not an independent check"
    )


def test_ac9_requirements_grew_and_surface_matches():
    bd_l3 = _bd_l3()
    assert {"R3.1", "R3.2"} <= set(bd_l3.REQUIREMENTS)
    public = {n for n in vars(bd_l3) if not n.startswith("_")}
    assert public == set(bd_l3.__all__)
