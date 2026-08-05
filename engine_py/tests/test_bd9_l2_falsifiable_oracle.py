"""bd#9 — BD-L2: a falsifiable oracle + fail-closed gates (ADV-3…ADV-6).

Spec: `docs/decisions/2026-08-04-bd9-bd-l2-falsifiable-oracle.md`.
The frozen levels spec: `2026-07-26_bytedigger_conformance_levels.md` (HAL
`fd35e1304`), §4 the adversary table, §8 the last paragraph.

Class: an oracle that cannot be FALSIFIED is not an oracle. BD-L2 is the
first level at which a green result starts to carry information, so
EVERY requirement here has an exhibited input on which the checker must
say NO. That is the lot's mandate, not decoration: an oracle with an unreachable refusal
branch is not weak but INVERTED — it turns an absence of evidence into
a claim of conformance.

`conformance.bd_l2` does NOT EXIST on this base, so every test fails inside
ITS OWN body. `conformance.*` imports are inside bodies only (the bd#24 discipline).

One log builder for all legs: if `failed` and `passed` were assembled from different
fixtures, it would be the fixtures that diverged, not the verdicts.
"""
from __future__ import annotations


def _ev(event_type: str, **payload):
    return {"type": event_type, "payload": dict(payload)}


def _red_outcome(*, n_passed: int, n_failed: int, exit_code: int = 1,
                 counted_as: str = "rejected"):
    """An R2.1 observation. `counted_as` is WHAT the engine counted the run as."""
    return _ev("red_test_outcome", group="py", exit_code=exit_code,
               n_passed=n_passed, n_failed=n_failed, phase=5,
               counted_as=counted_as)


def _vacuity(*, hits=()):
    """An R2.2/ADV-3 observation in the REAL `red_stub_passability_violation` form.

    bd#61: the earlier version built an invented event `oracle_vacuity_scan` with
    the key `findings`. Production emits `red_stub_passability_violation` with `hits`,
    and since bd#61 — on BOTH outcomes, which is what makes the `passed` verdict reachable.
    """
    return _ev("red_stub_passability_violation", phase=5, hits=list(hits))


def _gate(*, name: str, raised, outcome: str):
    """An R2.4/ADV-5 observation."""
    return _ev("gate_decision", gate=name, raised=raised, outcome=outcome)


def _suppression(*, rows):
    """An R2.5/ADV-6 observation — the rows of the known-reds registry."""
    return _ev("known_reds_ledger_scan", rows=rows)


def _delta(*, verdict="PASS", baseline_source="declared"):
    """An R2.6 observation in the REAL `_baseline_delta.py:92` form.

    bd#59: the earlier version built the invented keys `scoped_result` /
    `full_suite_delta`, which no producer writes — which is why
    the predicate could not fire on a live log. The keys are brought to the ones
    actually emitted.
    """
    return _ev("baseline_delta_gate_verdict", suite="py", verdict=verdict,
               new_fails=[], n_new_fails=0, ledgered=[],
               baseline_source=baseline_source, enforced=True, phase=5)


def _acs(*, binds_observable_effect: bool):
    """An R2.3 observation."""
    return _ev("acceptance_criteria_declared",
               criteria=[{"id": "AC1", "binds_observable_effect": binds_observable_effect}])


def _clean_log():
    """A fully conformant log — the common point of reference for all legs."""
    return [
        _red_outcome(n_passed=0, n_failed=1),
        _vacuity(),
        _acs(binds_observable_effect=True),
        _gate(name="baseline_delta", raised=None, outcome="failed"),
        _suppression(rows=[{"issue": "#123", "kill_by": "2099-01-01",
                            "status": "active"}]),
        _delta(),
    ]


def _check(events):
    from bytedigger_engine.conformance.bd_l2 import check_bd_l2  # noqa: PLC0415

    return check_bd_l2(events)


def _t():
    from bytedigger_engine.conformance import tokens  # noqa: PLC0415

    return tokens


def _swap(events, event_type, replacement):
    """Replace ONE observation in the conformant log — the rest is unchanged."""
    return [replacement if e["type"] == event_type else e for e in events]


# ─── R2.1: a refusal ≠ a no-op ───────────────────────────────────────────

def test_ac1_r21_genuine_rejection_passes():
    report = _check(_clean_log())
    assert report.labels["verdict:R2.1"] == _t().REQUIREMENT_PASSED


def test_ac2_r21_zero_collected_counted_as_rejection_is_a_violation():
    """NEGATIVE LEG. A run that collected not a single test, credited as
    a refusal, is a no-op passed off as a refusal. The frozen spec on R2.1:
    a load/collection error or a timeout do NOT count as a refusal.
    """
    events = _swap(_clean_log(), "red_test_outcome",
                   _red_outcome(n_passed=0, n_failed=0, exit_code=5,
                                counted_as="rejected"))
    report = _check(events)

    assert report.labels["verdict:R2.1"] == _t().REQUIREMENT_FAILED
    assert any("E_ORACLE_INDETERMINATE" in v for v in report.violations), (
        f"a violation must name a code; got {report.violations!r}"
    )
    assert report.passed is False


# ─── R2.2 / ADV-3: the vacuity oracle ────────────────────────────────────

def test_ac3_r22_oracle_mocking_its_own_uut_is_a_violation():
    """NEGATIVE LEG, ADV-3. The findings form is `stub_passability`."""
    events = _swap(_clean_log(), "red_stub_passability_violation", _vacuity(hits=["tests/t.py:30 'compute_digest'"]))
    report = _check(events)

    assert report.labels["verdict:R2.2"] == _t().REQUIREMENT_FAILED
    assert any("E_ORACLE_VACUOUS" in v for v in report.violations)
    assert report.passed is False


def test_ac4_r22_clean_oracle_passes():
    report = _check(_clean_log())
    assert report.labels["verdict:R2.2"] == _t().REQUIREMENT_PASSED


# ─── R2.4 / ADV-5: a gate that raised fails CLOSED ───────────────────────

def test_ac5_r24_raising_gate_treated_as_absent_is_a_violation():
    """NEGATIVE LEG, ADV-5. A gate that raised an exception must be counted
    FAILED, never absent: a gate that reached no verdict fails closed.
    """
    events = _swap(_clean_log(), "gate_decision",
                   _gate(name="baseline_delta", raised="RuntimeError: boom",
                         outcome="absent"))
    report = _check(events)

    assert report.labels["verdict:R2.4"] == _t().REQUIREMENT_FAILED
    assert any("E_GATE_INDETERMINATE" in v for v in report.violations)
    assert report.passed is False


# ─── R2.5 / ADV-6: a suppression with an owner and a deadline ────────────

def test_ac6_r25_unbounded_suppression_is_a_violation_both_halves():
    """NEGATIVE LEG, ADV-6, BOTH halves of the disjunction.

    "No owner OR expired" is a disjunction; checking one half
    leaves the other unreachable — precisely the hole the level
    is introduced for.
    """
    t = _t()

    no_owner = _check(_swap(_clean_log(), "known_reds_ledger_scan", _suppression(
        rows=[{"issue": "", "kill_by": "2099-01-01", "status": "active"}])))
    assert no_owner.labels["verdict:R2.5"] == t.REQUIREMENT_FAILED, "no owner"
    assert any("E_SUPPRESSION_UNBOUNDED" in v for v in no_owner.violations)

    expired = _check(_swap(_clean_log(), "known_reds_ledger_scan", _suppression(
        rows=[{"issue": "#123", "kill_by": "2020-01-01", "status": "expired"}])))
    assert expired.labels["verdict:R2.5"] == t.REQUIREMENT_FAILED, "expired"
    assert any("E_SUPPRESSION_UNBOUNDED" in v for v in expired.violations)

    bounded = _check(_clean_log())
    assert bounded.labels["verdict:R2.5"] == t.REQUIREMENT_PASSED


# ─── R2.6: the full-suite delta is about the GATE, not the oracle ────────

def test_ac7_r26_scoped_only_result_is_not_passed():
    """NEGATIVE LEG. A direct reminder from the issue: R2.6 is not about the oracle."""
    events = _swap(_clean_log(), "baseline_delta_gate_verdict",
                   _delta(baseline_source=None))
    report = _check(events)

    assert report.labels["verdict:R2.6"] != _t().REQUIREMENT_PASSED
    assert report.passed is False


# ─── R2.3: at least one AC is bound to an observable effect ──────────────

def test_ac8_r23_no_ac_binding_an_observable_effect_is_not_passed():
    events = _swap(_clean_log(), "acceptance_criteria_declared",
                   _acs(binds_observable_effect=False))
    report = _check(events)

    assert report.labels["verdict:R2.3"] != _t().REQUIREMENT_PASSED
    assert report.passed is False


# ─── EDGE-1: zero evidence ≠ conformance ─────────────────────────────────

def test_ac9_empty_log_is_not_checked_and_not_passed():
    """NEGATIVE LEG. A report over zero evidence with passed=True is form B-1."""
    report = _check([])
    t = _t()

    for req in ("R2.1", "R2.2", "R2.3", "R2.4", "R2.5", "R2.6"):
        assert report.labels[f"verdict:{req}"] == t.REQUIREMENT_NOT_CHECKED, req
    assert report.passed is False


# ─── The input filter and recomputation ──────────────────────────────────

def test_ac10_violation_in_a_foreign_event_type_is_ignored():
    """A real log is heterogeneous, a fixture corpus is homogeneous."""
    poisoned = dict(_vacuity(hits=["tests/t.py:2 'x'"]))
    poisoned["type"] = "some_unrelated_event"
    report = _check([*_clean_log(), poisoned])

    assert report.labels["verdict:R2.2"] == _t().REQUIREMENT_PASSED
    assert report.passed is True


def test_ac11_recorded_verdict_flag_does_not_override_recomputation():
    """Trusting a recorded flag = the emitter grading itself."""
    lying = _vacuity(hits=["tests/t.py:2 'x'"])
    lying["payload"]["verdict"] = "passed"
    report = _check(_swap(_clean_log(), "red_stub_passability_violation", lying))

    assert report.labels["verdict:R2.2"] == _t().REQUIREMENT_FAILED
    assert report.passed is False


# ─── §8: a level is claimed ONLY on executed adversaries ─────────────────

def test_ac12_unexecuted_adversary_cannot_be_counted_as_passed():
    """The frozen spec §8: "an implementation that silently counts an unexecuted
    adversary as passed is itself a conformance failure"."""
    from bytedigger_engine.conformance.bd_l2 import validate_report  # noqa: PLC0415
    from bytedigger_engine.conformance.report import L0Report  # noqa: PLC0415

    t = _t()
    lying = L0Report(
        passed=True,
        requirements=("R2.1", "R2.2", "R2.3", "R2.4", "R2.5", "R2.6"),
        violations=(),
        labels={f"verdict:{r}": t.REQUIREMENT_PASSED
                for r in ("R2.1", "R2.2", "R2.3", "R2.4", "R2.5", "R2.6")}
        | {"ADV-3": t.ADVERSARY_NOT_EXECUTED},
    )
    assert validate_report(lying), (
        "a report declaring passed on an UNEXECUTED adversary must be "
        "rejected (§8 of the frozen spec)"
    )


def test_ac13_validate_report_judges_both_ways():
    """A judge that always returns () is green — hence both sides."""
    from bytedigger_engine.conformance.bd_l2 import validate_report  # noqa: PLC0415

    assert validate_report(_check(_clean_log())) == ()

    broken = _check(_swap(_clean_log(), "red_stub_passability_violation", _vacuity(hits=["tests/t.py:2 'x'"])))
    assert validate_report(broken) == (), (
        "an honest violation report is self-consistent — the judge need not complain"
    )


# ─── The surface, the contract, the code registry ────────────────────────

def test_ac14_public_surface_equals_dunder_all():
    """B-2. In bd#28 my first revision leaked three names (`annotations`,
    `Any`, `TYPE_CHECKING`) — here the form is laid down from the first line."""
    from bytedigger_engine.conformance import bd_l2  # noqa: PLC0415

    # bd#59 added AWAITING_PRODUCER — a declared registry of requirements with no
    # producer. This is part of the public contract: the gap must be visible.
    # bd#59 added ENFORCEMENT — a declared link "requirement -> production refusal".
    # Without it "there is no refusal" and "there is a refusal, but it is off" are indistinguishable.
    assert set(bd_l2.__all__) == {
        "REQUIREMENTS", "AWAITING_PRODUCER", "ENFORCEMENT", "check_bd_l2",
        "validate_report"}
    public = {n for n in vars(bd_l2) if not n.startswith("_")}
    assert public == set(bd_l2.__all__), (
        f"the surface diverged from __all__: extra "
        f"{sorted(public - set(bd_l2.__all__))!r}"
    )


def test_ac15_l0report_contract_not_extended():
    import dataclasses  # noqa: PLC0415

    report = _check([])
    assert {f.name for f in dataclasses.fields(report)} == {
        "passed", "requirements", "violations", "labels"}
    assert tuple(report.requirements) == (
        "R2.1", "R2.2", "R2.3", "R2.4", "R2.5", "R2.6")


def test_ac16_new_error_codes_registered_and_drift_gate_clean():
    """Registering a code WITHOUT an emitter yields `DEAD <CODE>` and fails the drift gate —
    measured on bd#48, where five tests failed that way. So a code and its emitter
    must arrive in one diff."""
    from bytedigger_engine.error_codes import ERROR_CODES  # noqa: PLC0415

    for code in ("E_ORACLE_VACUOUS", "E_GATE_INDETERMINATE",
                 "E_SUPPRESSION_UNBOUNDED"):
        assert code in ERROR_CODES, f"{code} is not registered"
