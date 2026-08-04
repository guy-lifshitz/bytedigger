"""BD-L2 conformance checker: falsifiable oracle + fail-closed gates (bd#9).

Spec: `docs/decisions/2026-08-04-bd9-bd-l2-falsifiable-oracle.md`.
Frozen levels spec: `2026-07-26_bytedigger_conformance_levels.md` (HAL
`fd35e1304`), §4 adversary table, §8 final paragraph.

BD-L2 is the level at which a green result starts carrying information, so
every requirement here has an input on which this checker must say NO. An
oracle whose rejecting branch is unreachable is not a weak control — it is an
INVERTED one, because it turns absence of evidence into a claim of conformance.

WHAT THIS MODULE DOES NOT DO. It does not re-implement falsifiability. Every
primitive already exists in the tree and is CONSUMED here, not rewritten:
`stub_passability` (the mock-the-UUT AST scan behind ADV-3),
`known_reds_ledger.classify_kill_by` (owner/expiry behind ADV-6), the
`red_test_outcome` observation, and the `baseline_delta_gate_verdict` event.
This module's only job is to ADJUDICATE them into one report.

TWO RULES ARE LOAD-BEARING, both inherited from bd#28's B-1 finding:

1. A verdict is RECOMPUTED from the payload, never read from a recorded flag —
   otherwise the emitter grades itself.
2. `passed` is true only when EVERY requirement is `passed`. `not-checked` is
   NOT `passed`, so an empty log reports `passed=False`.

And one inherited from the frozen spec §8: a level is claimed only over the
adversaries ACTUALLY EXECUTED. `validate_report` rejects a report that claims
`passed` while an adversary is recorded as not executed — "an implementation
that quietly counts an unexecuted adversary as passed is itself a conformance
failure".

IMPORT ALIASING IS DELIBERATE (B-2). The public surface must equal `__all__`,
and this module necessarily imports `REQUIREMENT_FAILED`. In bd#28 my first
draft leaked `annotations`, `Any` and `TYPE_CHECKING` — all module attributes.
Here every import is underscore-bound and every annotation is a string.
"""
from typing import TYPE_CHECKING as _TYPE_CHECKING

from . import tokens as _tokens
from .report import L0Report as _L0Report

if _TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

__all__ = ["REQUIREMENTS", "AWAITING_PRODUCER", "ENFORCEMENT", "check_bd_l2",
           "validate_report"]

REQUIREMENTS: "tuple[str, ...]" = ("R2.1", "R2.2", "R2.3", "R2.4", "R2.5", "R2.6")

_VERDICT_KEY = "verdict:{}"

#: bd#59: requirements whose observation event has NO production emitter yet.
#: Declared rather than silent — a checker that adjudicates a shape nothing
#: emits stays green forever while observing nothing, which is how bd#9 shipped
#: a contract written from its own fixtures. `test_bd59_*::test_ac1` keeps this
#: registry honest in BOTH directions: an undeclared gap fails, and so does a
#: stale entry once a producer appears.
AWAITING_PRODUCER: "tuple[str, ...]" = ("R2.3", "R2.4", "R2.5")

#: bd#59: requirement -> the production refusal that ALREADY guards it.
#:
#: Measured, not designed: every observable requirement was found to have a
#: refusal in the engine already, so wiring the L2 verdict into a phase would
#: put a SECOND guard on an already-guarded condition — the defect found in
#: bd#29 §5a, where the later guard overwrites the earlier one's code and the
#: caller is told the wrong reason.
#:
#: What was genuinely missing is this binding. Without it, "no refusal exists"
#: and "a refusal exists but its flag is off" are indistinguishable, which is
#: what made enforcement unverifiable rather than absent.
#:
#: `enforced_by_default` is a MEASUREMENT of `flags_catalog` at bd#59 time, and
#: `test_bd59_enforcement_map.py::test_ac6` pins it: a flag flip elsewhere makes
#: that AC fail and demand a decision instead of drifting silently.
ENFORCEMENT: "dict[str, dict[str, object]]" = {
    "R2.1": {
        "error_code": "E_RED_COLLECT_PROBE",
        "flag": "HAL_RED_COLLECT_PROBE_ENFORCE",
        "enforced_by_default": False,
    },
    "R2.2": {
        "error_code": "E_RED_STUB_PASSABLE",
        "flag": "HAL_STUB_PASSABILITY_GATE",
        "enforced_by_default": True,
    },
    "R2.6": {
        "error_code": "E_BASELINE_DELTA",
        "flag": "HAL_BASELINE_DELTA_ENFORCE",
        "enforced_by_default": False,
    },
}

_ENFORCED = "enforced"
_DECLARED_NOT_ENFORCED = "declared-not-enforced"
_NO_CONSEQUENCE = "no-consequence"

# Bound as bare quoted literals on purpose: `error_codes.CODE_RE` harvests
# `["']E_[A-Z0-9_]+["']`, so a code embedded inside a longer message string is
# invisible to the drift gate and the registration reads as DEAD. Measured on
# this lot — all three codes came back DEAD until they were named here.
_CODE_INDETERMINATE = "E_ORACLE_INDETERMINATE"
_CODE_VACUOUS = "E_ORACLE_VACUOUS"
_CODE_GATE_INDETERMINATE = "E_GATE_INDETERMINATE"
_CODE_SUPPRESSION_UNBOUNDED = "E_SUPPRESSION_UNBOUNDED"

#: requirement -> the ONE event type carrying its observation. A checker that
#: reads any event shape it happens to recognise is indistinguishable on a
#: uniform fixture corpus and wrong on a real, heterogeneous log.
_EVENT_FOR = {
    "R2.1": "red_test_outcome",
    "R2.2": "red_stub_passability_violation",
    "R2.3": "acceptance_criteria_declared",
    "R2.4": "gate_decision",
    "R2.5": "known_reds_ledger_scan",
    "R2.6": "baseline_delta_gate_verdict",
}

#: adversary -> the event whose presence proves it was actually executed (§8).
_ADVERSARY_EVENT = {
    "ADV-3": "red_stub_passability_violation",
    "ADV-4": "red_test_outcome",
    "ADV-5": "gate_decision",
    "ADV-6": "known_reds_ledger_scan",
}


def _r21(p: "Mapping[str, object]") -> "str | None":
    """R2.1 — rejection must be distinguished from failure-to-execute.

    A run that collected nothing has not rejected anything; counting it as a
    rejection is the exact substitution the frozen spec forbids (a load error,
    collection error or timeout MUST NOT count as rejection).
    """
    n_passed = p.get("n_passed")
    n_failed = p.get("n_failed")
    if not isinstance(n_passed, int) or not isinstance(n_failed, int):
        return None
    # bd#59: keyed on what `phase_5_implement.py:2635` ACTUALLY emits — group,
    # exit_code, n_passed, n_failed, phase. The previous version required a
    # `counted_as` key that no producer writes, so this predicate could never
    # fire on a real log.
    if n_passed + n_failed == 0:
        return (
            f"R2.1: {_CODE_INDETERMINATE} — run collected zero tests "
            f"(exit_code={p.get('exit_code')!r}); a run that executed nothing "
            "has rejected nothing, and failure-to-execute is not rejection"
        )
    return None


def _r22(p: "Mapping[str, object]") -> "str | None":
    """R2.2 / ADV-3 — an oracle that mocks its own UUT constrains nothing."""
    # bd#61: keyed on `red_stub_passability_violation`, which
    # `phase_5_implement` actually emits — and, since bd#61, emits on BOTH
    # outcomes, which is what makes `passed` reachable at all.
    hits = p.get("hits")
    if not isinstance(hits, (list, tuple)):
        return None
    if not hits:
        return None
    return (
        f"R2.2: {_CODE_VACUOUS} — oracle substitutes the subject of its own "
        f"assertions: {list(hits)!r}"
    )


def _r23(p: "Mapping[str, object]") -> "str | None":
    """R2.3 — at least one AC must bind an observable effect."""
    criteria = p.get("criteria")
    if not isinstance(criteria, (list, tuple)) or not criteria:
        return None
    if any(isinstance(c, dict) and c.get("binds_observable_effect")
           for c in criteria):
        return None
    return (
        "R2.3: no acceptance criterion binds an observable effect of the "
        "delivered artifact; internal calls alone do not constrain it"
    )


def _r24(p: "Mapping[str, object]") -> "str | None":
    """R2.4 / ADV-5 — a gate that raises FAILED; it was never absent.

    Treating it as absent is the fail-open reading: the one state where the
    gate could not reach a verdict is exactly where it must refuse.
    """
    raised = p.get("raised")
    if raised is None:
        return None
    outcome = p.get("outcome")
    if outcome in ("failed", "fail"):
        return None
    return (
        f"R2.4: {_CODE_GATE_INDETERMINATE} — gate {p.get('gate')!r} raised "
        f"{raised!r} but was recorded as {outcome!r}; a gate that cannot reach "
        "a verdict fails closed"
    )


def _r25(p: "Mapping[str, object]") -> "str | None":
    """R2.5 / ADV-6 — a tolerated failure needs BOTH an owner and an expiry.

    The condition is a disjunction (no owner OR past expiry), so both halves
    are checked; testing one leaves the other unreachable.
    """
    rows = p.get("rows")
    if not isinstance(rows, (list, tuple)) or not rows:
        return None
    offenders = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        owner = row.get("issue")
        status = row.get("status")
        if not (isinstance(owner, str) and owner.strip()):
            offenders.append(f"{row!r} (no owner)")
        elif status != "active":
            offenders.append(f"{row!r} (kill-by {status!r})")
    if not offenders:
        return None
    return (
        f"R2.5: {_CODE_SUPPRESSION_UNBOUNDED} — tolerated failures without a live "
        f"owner+expiry: {offenders!r}"
    )


def _r26(p: "Mapping[str, object]") -> "str | None":
    """R2.6 — the delta of the FULL suite against a declared baseline.

    This one is about the gate, not the oracle: a scoped result alone says
    nothing about what the change did to everything else.
    """
    # bd#59: keyed on what `_baseline_delta.py:92` ACTUALLY emits. `verdict`
    # is the full-suite delta's outcome; `baseline_source` names the baseline
    # it was measured against. A verdict without a declared baseline is a
    # number with nothing to compare it to.
    source = p.get("baseline_source")
    verdict = p.get("verdict")
    if isinstance(source, str) and source.strip() and verdict is not None:
        return None
    return (
        f"R2.6: delta verdict {verdict!r} carries no declared baseline "
        f"(baseline_source={source!r}); a scoped number alone says nothing "
        "about what the change did to everything else"
    )


_CHECKS = {"R2.1": _r21, "R2.2": _r22, "R2.3": _r23,
           "R2.4": _r24, "R2.5": _r25, "R2.6": _r26}


def check_bd_l2(events: "Iterable[Mapping[str, object]]") -> _L0Report:
    """Adjudicate R2.1-R2.6 over `events`, returning an `L0Report`."""
    observed = {req: False for req in REQUIREMENTS}
    executed = {adv: False for adv in _ADVERSARY_EVENT}
    violations: "list[str]" = []

    for event in events:
        if not isinstance(event, dict):
            continue
        event_type = event.get("type")
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        for adv, adv_event in _ADVERSARY_EVENT.items():
            if event_type == adv_event:
                executed[adv] = True
        for req in REQUIREMENTS:
            if event_type != _EVENT_FOR[req]:
                continue
            observed[req] = True
            violation = _CHECKS[req](payload)
            if violation is not None:
                violations.append(violation)

    labels: "dict[str, object]" = {}
    for req in REQUIREMENTS:
        if any(v.startswith(f"{req}:") for v in violations):
            verdict = _tokens.REQUIREMENT_FAILED
        elif not observed[req]:
            verdict = _tokens.REQUIREMENT_NOT_CHECKED
        else:
            verdict = _tokens.REQUIREMENT_PASSED
        labels[_VERDICT_KEY.format(req)] = verdict

    for adv, ran in executed.items():
        labels[adv] = ("executed" if ran else _tokens.ADVERSARY_NOT_EXECUTED)

    # bd#59: three distinct states, because "no refusal" and "a refusal whose
    # flag is off" used to look identical from the outside.
    for req in REQUIREMENTS:
        entry = ENFORCEMENT.get(req)
        if entry is None:
            state = _NO_CONSEQUENCE
        elif entry["enforced_by_default"]:
            state = _ENFORCED
        else:
            state = _DECLARED_NOT_ENFORCED
        labels[f"enforcement:{req}"] = state

    passed = all(
        labels[_VERDICT_KEY.format(req)] == _tokens.REQUIREMENT_PASSED
        for req in REQUIREMENTS
    )

    return _L0Report(
        passed=passed,
        requirements=REQUIREMENTS,
        violations=tuple(violations),
        labels=labels,
    )


def validate_report(report: _L0Report) -> "tuple[str, ...]":
    """Return complaints against `report`; empty when self-consistent.

    A report is a claim about a claim, so `passed` must FOLLOW from the
    recorded verdicts rather than sit beside them. A judge that always returns
    `()` is the inverted oracle one level up, which is why the lot asserts this
    from both sides.
    """
    complaints: "list[str]" = []

    verdicts = {}
    for req in report.requirements:
        key = _VERDICT_KEY.format(req)
        if key not in report.labels:
            complaints.append(f"{req}: no verdict recorded under {key!r}")
        verdicts[req] = report.labels.get(key)

    expected = bool(report.requirements) and all(
        v == _tokens.REQUIREMENT_PASSED for v in verdicts.values()
    )
    if report.passed and not expected:
        offending = sorted(r for r, v in verdicts.items()
                           if v != _tokens.REQUIREMENT_PASSED)
        complaints.append(
            f"passed=True but these requirements are not 'passed': {offending}"
        )
    if report.passed and report.violations:
        complaints.append(
            f"passed=True while {len(report.violations)} violation(s) recorded"
        )
    if report.violations and not any(
        v == _tokens.REQUIREMENT_FAILED for v in verdicts.values()
    ):
        complaints.append(
            "violations recorded but no requirement carries the 'failed' verdict"
        )

    # Frozen spec §8: a level is claimed only over adversaries actually run.
    if report.passed:
        unexecuted = sorted(
            adv for adv in _ADVERSARY_EVENT
            if report.labels.get(adv) == _tokens.ADVERSARY_NOT_EXECUTED
        )
        if unexecuted:
            complaints.append(
                f"passed=True while these adversaries were never executed: "
                f"{unexecuted} — an unexecuted adversary counted as passed is "
                f"itself a conformance failure"
            )

    return tuple(complaints)
