"""BD-L3 conformance checker + attestation report (bd#28, split from bd#10 §7).

Spec: `docs/decisions/2026-08-04-bd28-bd-l3-conformance-checker.md`.

Reads the `model_invocation_attested` events written by the bd#10 chokepoint
(`llm_subprocess._dispatch_backend`) and adjudicates R3.3 and R3.6 into an
`L0Report`. It does not grant the level: BD-L0/L1/L2 do not exist yet.

WHY THE `failed` BRANCH IS WRITTEN FIRST AND GUARDED HARDEST. Round 1 of bd#10
shipped an oracle whose `failed` branch was unreachable — `REQUIREMENT_FAILED`
was not even bound in the test file, so three plausible GREENs passed all 36
tests: drop `failed` entirely, return `passed=True` unconditionally, return
`violations=()` always. An oracle that cannot fail is not a weak control, it is
an INVERTED one: it converts absence of evidence into a claim of conformance.

TWO CONSEQUENCES ARE LOAD-BEARING HERE:

1. A verdict is RECOMPUTED from the payload, never read from a recorded flag.
   Trusting a recorded verdict would let the emitter grade itself.
2. `passed` is true only when EVERY requirement is `passed`. `not-checked` is
   NOT `passed`, so an empty log reports `passed=False` — it carries no
   evidence of conformance, and saying otherwise is the inverted oracle again.

IMPORT ALIASING IS DELIBERATE (B-2). The public surface of this module must
equal `__all__`. Imported names would otherwise show up as public attributes —
and this module necessarily imports `REQUIREMENT_FAILED`, so the natural fix
for the unreachable-`failed` defect is exactly what would break the surface
equality. Every import below is therefore bound to an underscore-prefixed name.
"""
# NOTE: no `from __future__ import annotations` and no bare `typing` imports —
# both bind PUBLIC module attributes (`annotations`, `Any`, `TYPE_CHECKING`) and
# would break the `__all__`-equality this module owes (B-2). Every annotation
# here is a string literal instead, so nothing needs to be imported for typing.
from typing import TYPE_CHECKING as _TYPE_CHECKING

from . import attest as _attest
from . import tokens as _tokens
from .report import L0Report as _L0Report
from ..lib.llm_provider import get_provider as _get_provider

if _TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

__all__ = ["REQUIREMENTS", "AWAITING_PRODUCER", "SILENT_BACKENDS", "check_bd_l3",
           "validate_report"]

#: The requirements this checker adjudicates. R3.1/R3.2/R3.5 are out of scope.
REQUIREMENTS: "tuple[str, ...]" = ("R3.3", "R3.5", "R3.6")

#: bd#68: requirements whose observation field is not yet written on every
#: production path. Declared rather than silent — a checker adjudicating a
#: field nothing writes stays green while observing nothing, which is how
#: R3.5/R3.6 shipped inert under bd#28 and bd#63. `observed_model` is written
#: only by `_invoke_in_session`, so R3.3 is observable for that backend alone.
AWAITING_PRODUCER: "tuple[str, ...]" = ("R3.3",)

#: bd#71: reference backends that do NOT yet write the observation fields.
#: Declared rather than silent — `agent-sdk` was the DEFAULT backend and wrote
#: nothing, so all three L3 requirements were mute on real traffic while the
#: checkers stayed green. The gate that keeps this current
#: (`test_bd71_*::test_ac7`) covers EVERY registered backend, not just the one
#: a lot happened to touch: fixing only where the defect was found is how the
#: same class survived three lots in a row.
SILENT_BACKENDS: "tuple[str, ...]" = (
    "anthropic_api", "anthropic_oauth", "pydantic_anthropic", "pydantic_openai",
)

_VERDICT_KEY = "verdict:{}"
_ADV_9 = "ADV-9"

# Bound as a bare quoted literal: `error_codes.CODE_RE` harvests
# `["']E_[A-Z0-9_]+["']`, so a code embedded in a longer message reads as DEAD
# to the drift gate (measured on bd#9).
_CODE_ENFORCEMENT_UNSUBSTANTIATED = "E_CAPABILITY_ENFORCEMENT_UNSUBSTANTIATED"

_ENFORCEMENT_CLAIM = "runtime-allowlist"


def _family(model: "str | None") -> "str | None":
    if not isinstance(model, str) or not model:
        return None
    return _get_provider().model_family(model)


def _r33(payload: "Mapping[str, object]") -> "tuple[bool, str | None]":
    """(observed?, violation-or-None) for R3.3 — model-pin drift.

    Observation is non-null `observed_model`. A drift is a family mismatch
    against `model_requested`; an unrecognised token on either side resolves to
    no family and is therefore NOT evidence of drift (bd#10's explicit ruling —
    this checker consumes that decision, it does not revisit it).
    """
    observed = payload.get("observed_model")
    if not isinstance(observed, str) or not observed:
        return (False, None)
    requested = payload.get("model_requested")
    observed_family = _family(observed)
    requested_family = _family(requested if isinstance(requested, str) else None)
    if observed_family is None or requested_family is None:
        return (True, None)
    if observed_family == requested_family:
        return (True, None)
    return (True, (
        f"R3.3: step {payload.get('step_name')!r} requested {requested!r} "
        f"({requested_family}) but the adapter reported {observed!r} "
        f"({observed_family})"
    ))


def _r35(payload: "Mapping[str, object]") -> "tuple[bool, str | None]":
    """(observed?, violation-or-None) for R3.5 — a claim checked against its own evidence.

    `capability_enforcement` is written by the backend's own registry entry
    (`register_backend(..., capabilities=...)`), so on its own it is the actor
    grading itself. It becomes falsifiable the moment it is read together with
    the escape evidence recorded in the SAME invocation: a backend that claimed
    `runtime-allowlist` and simultaneously let a tool outside the declared set
    through has been refuted by its own record.

    A backend that claimed NOTHING is not in scope here — it promised nothing,
    and its escape is R3.6's business. Punishing it under R3.5 would make an
    honest declaration costlier than a false one, which is worse than the
    defect this predicate exists to close.
    """
    claim = payload.get("capability_enforcement")
    if claim != _ENFORCEMENT_CLAIM:
        return (False, None)
    observed_tools = payload.get("observed_tools")
    declared = payload.get("declared_capabilities")
    if not isinstance(observed_tools, (list, tuple)):
        return (False, None)
    if not isinstance(declared, (list, tuple)):
        return (False, None)
    escapes = _attest.capability_escapes(list(observed_tools), list(declared))
    if not escapes:
        return (True, None)
    return (True, (
        f"R3.5: {_CODE_ENFORCEMENT_UNSUBSTANTIATED} — step "
        f"{payload.get('step_name')!r} declared {claim!r} yet the same "
        f"invocation let {list(escapes)!r} through"
    ))


def _r36(payload: "Mapping[str, object]") -> "tuple[bool, str | None]":
    """(observed?, violation-or-None) for R3.6 — capability escape.

    Observation requires BOTH `observed_tools` and `declared_capabilities` to
    be non-null: with either side missing there is nothing to compare, and
    calling that a pass would manufacture evidence.
    """
    observed_tools = payload.get("observed_tools")
    declared = payload.get("declared_capabilities")
    if not isinstance(observed_tools, (list, tuple)):
        return (False, None)
    if not isinstance(declared, (list, tuple)):
        return (False, None)
    escapes = _attest.capability_escapes(list(observed_tools), list(declared))
    if not escapes:
        return (True, None)
    return (True, (
        f"R3.6: step {payload.get('step_name')!r} used {list(escapes)!r} "
        f"outside declared capabilities {list(declared)!r}"
    ))


_CHECKS = {"R3.3": _r33, "R3.5": _r35, "R3.6": _r36}


def check_bd_l3(events: "Iterable[Mapping[str, object]]") -> _L0Report:
    """Adjudicate R3.3/R3.6 over `events`, returning an `L0Report`.

    Only events whose `type` is `attest.EVENT_TYPE` are read; every other event
    type is ignored. That filter is not incidental — a real event log is
    heterogeneous while a fixture corpus tends to be uniform, so a checker
    without it is indistinguishable on the corpus and wrong on the real log.
    """
    observed: "dict[str, bool]" = {req: False for req in REQUIREMENTS}
    violations: "list[str]" = []

    for event in events:
        if not isinstance(event, dict):
            continue
        if event.get("type") != _attest.EVENT_TYPE:
            continue
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        for req in REQUIREMENTS:
            saw, violation = _CHECKS[req](payload)
            if saw:
                observed[req] = True
            if violation is not None:
                violations.append(violation)

    failed = {req for req in REQUIREMENTS
              if any(v.startswith(f"{req}:") for v in violations)}

    labels: "dict[str, object]" = {}
    for req in REQUIREMENTS:
        if req in failed:
            verdict = _tokens.REQUIREMENT_FAILED
        elif not observed[req]:
            verdict = _tokens.REQUIREMENT_NOT_CHECKED
        else:
            verdict = _tokens.REQUIREMENT_PASSED
        labels[_VERDICT_KEY.format(req)] = verdict

    # ADV-9 is recorded, not run: `validate_report` is the judge (CL:221-224).
    labels[_ADV_9] = _tokens.ADVERSARY_NOT_EXECUTED
    labels.update(_attest.REQUIREMENT_LABELS)

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
    """Return the complaints against `report`, empty when self-consistent.

    The judge exists because a report is a claim about a claim: `passed` must
    follow from the recorded verdicts rather than sit beside them. A judge that
    always returns `()` is the same inverted oracle one level up, so each
    clause below is asserted from both sides by the lot's ACs.
    """
    complaints: "list[str]" = []

    for req in report.requirements:
        key = _VERDICT_KEY.format(req)
        if key not in report.labels:
            complaints.append(f"{req}: no verdict recorded under {key!r}")

    verdicts = {
        req: report.labels.get(_VERDICT_KEY.format(req))
        for req in report.requirements
    }
    expected_passed = bool(report.requirements) and all(
        v == _tokens.REQUIREMENT_PASSED for v in verdicts.values()
    )
    if report.passed and not expected_passed:
        offending = sorted(
            req for req, v in verdicts.items() if v != _tokens.REQUIREMENT_PASSED
        )
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

    return tuple(complaints)
