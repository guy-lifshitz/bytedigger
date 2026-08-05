"""bd#28 — BD-L3 conformance checker + attestation report.

Spec: `docs/decisions/2026-08-04-bd28-bd-l3-conformance-checker.md`.

Class: an oracle that cannot refuse (gate B-1 of bd#10 round 1, form
`[G22:18]`, the inversion of P2 from CL §1). A checker with an unreachable `failed` branch is not
weak but INVERTED: it turns an absence of evidence into a claim of
conformance. The bd#10 gate named three plausible GREENs that passed all 36
tests of round 1 — the `failed` branch omitted entirely; `passed=True` unconditionally;
`violations=()` always. So here the `failed` branch is asserted FIRST, and
`passed`/`not-checked` are built by the same log builder as it.

`conformance.bd_l3` does NOT EXIST on this base, so every test fails on
`ImportError` inside ITS OWN body. `conformance.*` imports are inside bodies only
(the bd#24 discipline): collection stays clean, and RED comes out assert/import-time
inside the test rather than a collection error over the whole file.

The interface is carried by SPEC §3, not by this file (`CONTRACTS_SPEC.md` §1.5 / bd#7 §3.0).

The fixture form is taken from the live producer (`llm_subprocess._attest_payload`,
`:1060-1085`): nine keys — step_name, backend, model_requested,
prompt_sha256, injections, declared_capabilities, capability_enforcement,
observed_model, observed_tools.
"""
from __future__ import annotations

from typing import Any


def _attested(
    *,
    model_requested: str = "sonnet",
    observed_model: "str | None" = None,
    declared_capabilities: "list[str] | None" = None,
    observed_tools: "list[str] | None" = None,
    capability_enforcement: str = "runtime-allowlist",
    event_type: "str | None" = None,
    extra: "dict[str, Any] | None" = None,
) -> "dict[str, Any]":
    """One event in the form of the live producer.

    ONE builder for all branches — the issue's requirement to "assert in both directions
    on one set of fixtures". If `failed` and `passed` were built from different
    fixtures, it would be the fixtures that diverged, not the verdicts.
    """
    if event_type is None:
        from bytedigger_engine.conformance import attest  # noqa: PLC0415

        event_type = attest.EVENT_TYPE
    payload: "dict[str, Any]" = {
        "step_name": "s1",
        "backend": "claude-subprocess",
        "model_requested": model_requested,
        # bd#73 pins the FORM of the hash, not merely the key's presence — the fixture
        # is brought to a real one: sha256:<64 hex>.
        "prompt_sha256": "sha256:" + "0" * 64,
        # bd#73: R3.2 with no injections gives not-checked (an empty list is legitimate, but
        # there is nothing to observe), so a FULLY conformant invocation carries an
        # attributed block — like a real one going through the injection channel.
        "injections": [{"source_id": "role-template",
                        "sha256": "sha256:" + "0" * 64}],
        "declared_capabilities": declared_capabilities,
        "capability_enforcement": "declared",
        "observed_model": observed_model,
        "observed_tools": observed_tools,
        # bd#63: R3.5 reads the claim together with the escape evidence. A fully
        # conformant invocation CLAIMS enforcement and does not violate it; a backend
        # that claimed nothing leaves R3.5 at not-checked — there is nothing to check.
        "capability_enforcement": capability_enforcement,
    }
    if extra:
        payload.update(extra)
    return {"type": event_type, "payload": payload}


def _check(events):
    from bytedigger_engine.conformance.bd_l3 import check_bd_l3  # noqa: PLC0415

    return check_bd_l3(events)


def _tokens():
    from bytedigger_engine.conformance import tokens  # noqa: PLC0415

    return tokens


# ─── AC1/AC2: the `failed` branch — asserted FIRST ───────────────────────

def test_ac1_r33_family_drift_is_a_violation():
    """The branch that did not exist in round 1's RED: `REQUIREMENT_FAILED` was
    not even bound in the test file, and no fixture supplied a violation."""
    report = _check([_attested(model_requested="sonnet", observed_model="haiku")])

    assert report.labels["verdict:R3.3"] == _tokens().REQUIREMENT_FAILED
    assert report.passed is False, "a violation must fail the report"
    assert report.violations, "a violation must be listed"


def test_ac2_r36_capability_escape_is_a_violation():
    report = _check([_attested(
        declared_capabilities=["Read"], observed_tools=["bash"],
    )])

    assert report.labels["verdict:R3.6"] == _tokens().REQUIREMENT_FAILED
    assert report.passed is False
    assert report.violations


# ─── AC3: the `passed` branch, THE SAME builder ──────────────────────────

def test_ac3_conformant_invocation_passes_both_requirements():
    report = _check([_attested(
        model_requested="sonnet", observed_model="sonnet",
        declared_capabilities=["Read"], observed_tools=["Read"],
    )])
    t = _tokens()

    assert report.labels["verdict:R3.3"] == t.REQUIREMENT_PASSED
    assert report.labels["verdict:R3.6"] == t.REQUIREMENT_PASSED
    assert report.passed is True
    assert report.violations == ()


# ─── AC4: the `not-checked` branch ───────────────────────────────────────

def test_ac4_null_observations_are_not_checked():
    report = _check([_attested(observed_model=None, observed_tools=None)])
    t = _tokens()

    assert report.labels["verdict:R3.3"] == t.REQUIREMENT_NOT_CHECKED
    assert report.labels["verdict:R3.6"] == t.REQUIREMENT_NOT_CHECKED


# ─── AC5: EDGE-1, NEGATIVE LEG — zero evidence ≠ conformance ─────────────

def test_ac5_empty_log_is_not_checked_and_not_passed():
    """A report over zero evidence returning passed=True is the purest
    form of B-1: an empty log has no non-zero observations, and `passed` is
    not determined by any aggregation clause. `not-checked` is NOT `passed`.
    """
    report = _check([])
    t = _tokens()

    assert report.labels["verdict:R3.3"] == t.REQUIREMENT_NOT_CHECKED
    assert report.labels["verdict:R3.6"] == t.REQUIREMENT_NOT_CHECKED
    assert report.passed is False, (
        "an empty log yields no evidence of conformance — passed must be False"
    )


# ─── AC6: EDGE-8, NEGATIVE LEG — the input filter ────────────────────────

def test_ac6_violation_in_a_foreign_event_type_is_ignored():
    """A real log is heterogeneous while a fixture corpus is homogeneous — a GREEN without
    a filter is indistinguishable on a homogeneous corpus and lies on a real one.

    The violating payload sits in an event of a FOREIGN type; the attested event is
    clean. The verdict must be `passed`.
    """
    events = [
        _attested(event_type="runner_result_consumed",
                  model_requested="sonnet", observed_model="haiku",
                  declared_capabilities=["Read"], observed_tools=["bash"]),
        _attested(model_requested="sonnet", observed_model="sonnet",
                  declared_capabilities=["Read"], observed_tools=["Read"]),
    ]
    report = _check(events)
    t = _tokens()

    assert report.labels["verdict:R3.3"] == t.REQUIREMENT_PASSED, (
        "a violation in an event of a foreign type must be ignored"
    )
    assert report.labels["verdict:R3.6"] == t.REQUIREMENT_PASSED
    assert report.passed is True


# ─── AC7: NEGATIVE LEG — recomputation from the payload, not a recorded flag ─

def test_ac7_recorded_verdict_flag_does_not_override_recomputation():
    """Trusting a recorded flag means letting the emitter grade itself —
    subtype (3) of hal#1373 at the level of the whole system."""
    report = _check([_attested(
        model_requested="sonnet", observed_model="haiku",
        extra={"verdict": "passed", "verdict:R3.3": "passed"},
    )])

    assert report.labels["verdict:R3.3"] == _tokens().REQUIREMENT_FAILED, (
        "the verdict must be recomputed from the payload, not read from a flag"
    )
    assert report.passed is False


# ─── AC8: B-2 — the export surface against __all__ ───────────────────────

def test_ac8_public_surface_equals_dunder_all():
    """Measured: instances of builtin types do NOT carry `__module__`
    (`"x".__module__` -> AttributeError), so the form
    `getattr(value, "__module__", module.__name__) == module.__name__`
    counts imported constants as exports. `bd_l3` must import
    `REQUIREMENT_FAILED` — that is, the B-1 fix DETONATES B-2. Therefore
    equality is asserted against `__all__` rather than against a computation.
    """
    from bytedigger_engine.conformance import bd_l3  # noqa: PLC0415

    assert hasattr(bd_l3, "__all__"), "the module must declare __all__ (B-2)"
    # bd#63 did not change the surface — REQUIREMENTS grew, the names are the same.
    # bd#68 added AWAITING_PRODUCER — a declared registry of requirements whose observation
    # field is not written on every production path. The gap must be visible.
    # bd#71 added SILENT_BACKENDS — a declared list of backends that write no
    # observations. The default one wrote nothing, and the checkers were green.
    assert set(bd_l3.__all__) == {
        "REQUIREMENTS", "AWAITING_PRODUCER", "SILENT_BACKENDS",
        "LABEL_EXCEPTIONS", "check_bd_l3", "validate_report"}
    for name in bd_l3.__all__:
        assert hasattr(bd_l3, name), f"__all__ names a missing symbol {name!r}"
    public = {n for n in vars(bd_l3) if not n.startswith("_")}
    assert public == set(bd_l3.__all__), (
        f"the public surface diverged from __all__: extra "
        f"{sorted(public - set(bd_l3.__all__))!r}"
    )


# ─── AC9: validate_report as a judge, both sides ─────────────────────────

def test_ac9_validate_report_judges_both_ways():
    """A judge that always returns () is green — hence both sides."""
    from bytedigger_engine.conformance.bd_l3 import validate_report  # noqa: PLC0415
    from bytedigger_engine.conformance.report import L0Report  # noqa: PLC0415

    t = _tokens()
    consistent = _check([_attested(
        model_requested="sonnet", observed_model="sonnet",
        declared_capabilities=["Read"], observed_tools=["Read"],
    )])
    assert validate_report(consistent) == ()

    lying = L0Report(
        passed=True,
        requirements=("R3.3", "R3.6"),
        violations=("R3.3: drift",),
        labels={"verdict:R3.3": t.REQUIREMENT_FAILED,
                "verdict:R3.6": t.REQUIREMENT_PASSED},
    )
    assert validate_report(lying), (
        "passed=True with a failed verdict must be rejected by the judge"
    )


# ─── AC10/AC11: ADV-9 and the unextended contract ────────────────────────

def test_ac10_adv9_recorded_as_not_executed():
    report = _check([])
    assert report.labels["ADV-9"] == _tokens().ADVERSARY_NOT_EXECUTED


def test_ac11_l0report_contract_not_extended():
    """Four fields — the bd#22 contract (`CONTRACTS_SPEC.md` §2 AC-C2)."""
    import dataclasses  # noqa: PLC0415

    report = _check([])
    names = {f.name for f in dataclasses.fields(report)}
    assert names == {"passed", "requirements", "violations", "labels"}
    # bd#63 added R3.5: the enforcement claim became refutable.
    # bd#73 added R3.1/R3.2: they carried a label with no verdict.
    assert tuple(report.requirements) == ("R3.1", "R3.2", "R3.3", "R3.5", "R3.6")
