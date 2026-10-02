"""Adversary execution harness + attestation publisher (bd#58).

Spec: `docs/decisions/2026-08-04-bd58-adversary-harness-attestation.md`.
Frozen levels spec: `2026-07-26_bytedigger_conformance_levels.md` (HAL
`fd35e1304`), §4 (adversary table + Attestation output), §8, §9 step 1.

WHY THIS MODULE EXISTS. `bd_l2` and `bd_l3` can say NO — their negative legs
prove it. But nothing ever RAN an adversary against this host, so on a real log
every verdict was `not-checked`, and no attestation left the process at all.
`not-checked` is honest on its own; paired with a missing publisher it reads as
"question closed". Frozen spec §8 forbids exactly that: a level is claimed only
over adversaries actually executed, and "an implementation that quietly counts
an unexecuted adversary as passed is itself a conformance failure".

THE FOUR OUTCOMES, AND WHY `errored` IS NOT `not_executed`.

  defended     — the adversary ran AND the host repelled it.
  undefended   — it ran and the host did NOT repel it.
  errored      — running it raised. Counted as NOT passed (fail-closed), never
                 as absent. This is R2.4's rule turned on the harness itself: a
                 guard that cannot reach a verdict refuses. A harness that
                 treated its own crash as "no check performed" would reproduce
                 fail-open on the very layer built to close it.
  not_executed — never run. REMOVES the level rather than leaving it untouched.

A level is granted only when every adversary at that level AND every level
below it is `defended`. ADV-9 is declarative (§8) and is published as
`not_executed` — its absence from the report would be the silence §8 forbids,
so it is present and stated.

The probes below use the real primitives (`oracle`, `stub_passability`,
`known_reds_ledger` via `bd_l2`, `attest`) — this module executes, it does not
re-implement. Import aliasing is deliberate (B-2): the public surface must
equal `__all__`.

HOST-ADAPTER SEAM (bd#195, `docs/decisions/2026-10-02-bd195-harness-adapter-seam.md`).
`run_adversaries(adapter=...)` runs ADV-1 and ADV-2 through the caller's adapter
(freeze, control, mutate, attack) instead of the engine's own probes. Every other
adversary is `not_executed` under an adapter, so an adapter attestation tops out
at BD-L1 (ADV-3..ADV-10 stay unexecuted until bd#197). An adapter that fails,
hangs or answers badly yields `indeterminate`, which sinks the level like
`errored`. `adapter_identity` reads the identity from the adapter itself, and
the CLI (`--adapter module:factory`) publishes the attestation.
"""
from typing import TYPE_CHECKING as _TYPE_CHECKING

import tempfile as _tempfile
import threading as _threading
from collections.abc import Mapping as _Mapping
from dataclasses import dataclass as _dataclass
from pathlib import Path as _Path

from . import attest as _attest
from . import bd_l2 as _bd_l2
from . import bd_l3 as _bd_l3
from . import oracle as _oracle
from . import tokens as _tokens

if _TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

__all__ = [
    "ADVERSARIES",
    "OUTCOME_DEFENDED",
    "OUTCOME_UNDEFENDED",
    "OUTCOME_ERRORED",
    "OUTCOME_INDETERMINATE",
    "AdapterState",
    "adapter_identity",
    "run_adversaries",
    "build_attestation",
    "validate_attestation",
]

OUTCOME_DEFENDED = "defended"
OUTCOME_UNDEFENDED = "undefended"
OUTCOME_ERRORED = "errored"
#: The adapter could not reach a verdict (raised, hung, malformed answer).
#: Never counts as defended, so it sinks a level like `errored`.
OUTCOME_INDETERMINATE = "indeterminate"


@_dataclass(frozen=True)
class AdapterState:
    """What the harness hands an adapter's `evaluate`."""

    root: "_Path"
    frozen: str
    members: "tuple[_Path, ...]"

#: The v1 EXECUTABLE set (§8). ADV-9 is declarative and is not here — it is
#: published separately as `not_executed`.
ADVERSARIES: "tuple[str, ...]" = (
    "ADV-1", "ADV-2", "ADV-3", "ADV-4", "ADV-5", "ADV-6",
    "ADV-7", "ADV-8", "ADV-10",
)

_DECLARATIVE = ("ADV-9",)

_LEVEL_OF = {
    "ADV-1": "BD-L1", "ADV-2": "BD-L1",
    "ADV-3": "BD-L2", "ADV-4": "BD-L2", "ADV-5": "BD-L2", "ADV-6": "BD-L2",
    "ADV-7": "BD-L3", "ADV-8": "BD-L3", "ADV-10": "BD-L3",
}

#: Ascending, so a level is only granted with every level below it.
_LEVELS = ("BD-L0", "BD-L1", "BD-L2", "BD-L3")

_VACUOUS_RED = '''\
from unittest.mock import patch
from mypkg.subject import compute_digest

def test_thing():
    with patch("mypkg.subject.compute_digest") as m:
        m.return_value = "x"
        assert compute_digest() == "x"
'''


def _ev(event_type: str, **payload):
    return {"type": event_type, "payload": dict(payload)}


def _verdict_failed(check, events, requirement) -> bool:
    """True when `check` adjudicates `requirement` as failed on `events`."""
    report = check(events)
    return report.labels.get(f"verdict:{requirement}") == _tokens.REQUIREMENT_FAILED


def _adv_1() -> bool:
    """Rewrite an oracle member after freeze — the digest must stop matching."""
    with _tempfile.TemporaryDirectory() as tmp:
        docs = _oracle.doc_dir(tmp)
        docs.mkdir(parents=True)
        (docs / "a.md").write_text("original\n", encoding="utf-8")
        members = _oracle.list_members(tmp)
        frozen = _oracle.compute_digest(tmp, members)
        (docs / "a.md").write_text("rewritten\n", encoding="utf-8")
        return _oracle.compute_digest(tmp, members) != frozen


def _adv_2() -> bool:
    """Add a member after freeze — an addition is a mismatch (R1.4)."""
    with _tempfile.TemporaryDirectory() as tmp:
        docs = _oracle.doc_dir(tmp)
        docs.mkdir(parents=True)
        (docs / "a.md").write_text("a\n", encoding="utf-8")
        frozen_members = _oracle.list_members(tmp)
        (docs / "b.md").write_text("b\n", encoding="utf-8")
        return _oracle.list_members(tmp) != frozen_members


def _adv_3() -> bool:
    """An oracle that mocks its own UUT must be detected as vacuous."""
    from .. import stub_passability as _stub  # noqa: PLC0415

    with _tempfile.TemporaryDirectory() as tmp:
        path = _Path(tmp) / "test_vacuous.py"
        path.write_text(_VACUOUS_RED, encoding="utf-8")
        result = _stub.lint_red_file(str(path))
        if result.has_violation():
            return True
        # Fall back to the checker's own adjudication over the REAL event the
        # phase-5 gate emits (bd#61), so the probe exercises a rejection path
        # that a production log can actually produce.
        return _verdict_failed(_bd_l2.check_bd_l2, [_ev(
            "red_stub_passability_violation", phase=5,
            hits=["tests/test_vacuous.py:5 'compute_digest'"])], "R2.2")


def _adv_4() -> bool:
    """An oracle that fails to load must NOT be counted as a rejection."""
    return _verdict_failed(_bd_l2.check_bd_l2, [_ev(
        "red_test_outcome", group="py", exit_code=5, n_passed=0, n_failed=0,
        phase=5, counted_as="rejected")], "R2.1")


def _adv_5() -> bool:
    """A gate that raises must be treated as failed, never as absent."""
    return _verdict_failed(_bd_l2.check_bd_l2, [_ev(
        "gate_decision", gate="probe", raised="RuntimeError: boom",
        outcome="absent")], "R2.4")


def _adv_6() -> bool:
    """A tolerated failure without an owner or a live expiry must be refused."""
    no_owner = _verdict_failed(_bd_l2.check_bd_l2, [_ev(
        "known_reds_ledger_scan",
        rows=[{"issue": "", "kill_by": "2099-01-01", "status": "active"}])],
        "R2.5")
    expired = _verdict_failed(_bd_l2.check_bd_l2, [_ev(
        "known_reds_ledger_scan",
        rows=[{"issue": "#1", "kill_by": "2020-01-01", "status": "expired"}])],
        "R2.5")
    # BOTH halves of the disjunction — checking one leaves the other unreachable.
    return no_owner and expired


def _adv_7() -> bool:
    """A model of a different family than pinned must be caught (R3.3)."""
    return _verdict_failed(_bd_l3.check_bd_l3, [{
        "type": _attest.EVENT_TYPE,
        "payload": {"step_name": "probe", "model_requested": "sonnet",
                    "observed_model": "haiku", "declared_capabilities": None,
                    "observed_tools": None},
    }], "R3.3")


def _adv_8() -> bool:
    """Injected content must be attributable: the block carries its source_id
    and the assembled prompt actually contains it (R3.2's channel)."""
    block = _attest.InjectedBlock(source_id="probe-source", content="INJECTED")
    assembled = _attest.assemble("base prompt", [block])
    digest = _attest.hash_text(block.content)
    return (
        "INJECTED" in assembled
        and block.source_id == "probe-source"
        and digest.startswith("sha256:")
        and digest != _attest.hash_text("something else")
    )


def _adv_10() -> bool:
    """A tool outside the declared set must be reported as an escape (R3.6)."""
    escapes = _attest.capability_escapes(["bash"], ["Read"])
    return bool(escapes) and _verdict_failed(_bd_l3.check_bd_l3, [{
        "type": _attest.EVENT_TYPE,
        "payload": {"step_name": "probe", "model_requested": "sonnet",
                    "observed_model": None, "declared_capabilities": ["Read"],
                    "observed_tools": ["bash"]},
    }], "R3.6")


_PROBES = {
    "ADV-1": _adv_1, "ADV-2": _adv_2, "ADV-3": _adv_3, "ADV-4": _adv_4,
    "ADV-5": _adv_5, "ADV-6": _adv_6, "ADV-7": _adv_7, "ADV-8": _adv_8,
    "ADV-10": _adv_10,
}


_ADAPTER_ADVERSARIES = ("ADV-1", "ADV-2")


def _adapter_members(root: "_Path") -> "list[_Path]":
    """Regular files directly under `root/specs`, absolute, sorted by name."""
    return sorted(
        (p for p in (root / "specs").iterdir() if p.is_file()),
        key=lambda p: p.name,
    )


def _guarded_freeze(adapter, paths, root, timeout_s):
    """Run `adapter.freeze` on a daemon worker; return a token or None.

    Mirrors `oracle.evaluate_guarded`: the worker is abandoned (never joined
    unconditionally) when the timeout expires; KeyboardInterrupt and SystemExit
    are re-raised; any other Exception, or a result that is not a non-empty
    str, yields None (indeterminate).
    """
    box: "dict[str, object]" = {}

    def _run() -> None:
        try:
            box["value"] = adapter.freeze(paths, root=root)
        except BaseException as exc:  # noqa: BLE001 — triaged below, in the caller
            box["exc"] = exc

    worker = _threading.Thread(
        target=_run, name="conformance-freeze-guard", daemon=True
    )
    worker.start()
    worker.join(timeout_s)
    if worker.is_alive():
        return None
    if "exc" in box:
        exc = box["exc"]
        if not isinstance(exc, Exception):
            raise exc  # type: ignore[misc]
        return None
    value = box.get("value")
    if not isinstance(value, str) or not value:
        return None
    return value


def _adapter_adversary(adapter, name: str, timeout_s: float) -> str:
    """ADV-1 / ADV-2 through the adapter: freeze, control, mutate, attack."""
    accepted = _oracle.OracleOutcome.ACCEPTED
    rejected = _oracle.OracleOutcome.REJECTED
    try:
        with _tempfile.TemporaryDirectory() as tmp:
            root = _Path(tmp).resolve()
            specs = root / "specs"
            specs.mkdir()
            (specs / "a.md").write_text("original\n", encoding="utf-8")

            token = _guarded_freeze(
                adapter, _adapter_members(root), root, timeout_s
            )
            if token is None:
                return OUTCOME_INDETERMINATE

            control, _reason = _oracle.evaluate_guarded(
                adapter,
                AdapterState(
                    root=root, frozen=token,
                    members=tuple(_adapter_members(root)),
                ),
                timeout_s=timeout_s,
            )
            if control is _oracle.OracleOutcome.INDETERMINATE:
                return OUTCOME_INDETERMINATE
            if control is rejected:
                return OUTCOME_UNDEFENDED

            if name == "ADV-1":
                (specs / "a.md").write_text("rewritten\n", encoding="utf-8")
            else:
                (specs / "b.md").write_text("added\n", encoding="utf-8")

            attack, _reason = _oracle.evaluate_guarded(
                adapter,
                AdapterState(
                    root=root, frozen=token,
                    members=tuple(_adapter_members(root)),
                ),
                timeout_s=timeout_s,
            )
            if attack is rejected:
                return OUTCOME_DEFENDED
            if attack is accepted:
                return OUTCOME_UNDEFENDED
            return OUTCOME_INDETERMINATE
    except Exception:  # noqa: BLE001 — under an adapter a failure is indeterminate
        return OUTCOME_INDETERMINATE


def adapter_identity(adapter) -> "dict[str, str]":
    """Return exactly `{"backend", "source"}` read from `adapter.identity`.

    The values are copied, never interpreted. A missing or malformed identity
    raises `ValueError`.
    """
    try:
        ident = adapter.identity
    except Exception as exc:  # noqa: BLE001 — any failed read is a bad identity
        raise ValueError(
            f"adapter has no readable identity ({type(exc).__name__}: {exc})"
        ) from exc
    if not isinstance(ident, _Mapping):
        raise ValueError("adapter identity must be a mapping")
    result: "dict[str, str]" = {}
    for key in ("backend", "source"):
        value = ident.get(key)
        if not isinstance(value, str) or not value:
            raise ValueError(
                f"adapter identity {key!r} must be a non-empty str"
            )
        result[key] = value
    return result


def run_adversaries(
    only: "Iterable[str] | None" = None,
    *,
    adapter: "object | None" = None,
    timeout_s: float = 30.0,
) -> "dict[str, str]":
    """Execute the adversaries against this host; return name -> outcome.

    A probe that raises yields `OUTCOME_ERRORED`, never a missing key and never
    `not_executed`: the harness must not be able to hide its own failure as an
    absence of checking.

    With `adapter`, only ADV-1 and ADV-2 run (through the adapter); every other
    name is `not_executed` and the adapter is not called for it.
    """
    names = tuple(only) if only is not None else ADVERSARIES
    outcomes: "dict[str, str]" = {}
    for name in names:
        if adapter is not None:
            if name in _ADAPTER_ADVERSARIES:
                outcomes[name] = _adapter_adversary(adapter, name, timeout_s)
            else:
                outcomes[name] = _tokens.ADVERSARY_NOT_EXECUTED
            continue
        probe = _PROBES.get(name)
        if probe is None:
            outcomes[name] = _tokens.ADVERSARY_NOT_EXECUTED
            continue
        try:
            outcomes[name] = (
                OUTCOME_DEFENDED if probe() else OUTCOME_UNDEFENDED
            )
        except Exception:  # noqa: BLE001 — any failure is fail-closed, by design
            outcomes[name] = OUTCOME_ERRORED
    return outcomes


def _level_achieved(outcomes: "Mapping[str, str]") -> str:
    """Highest level whose adversaries — and every level below — are defended."""
    achieved = _LEVELS[0]
    for level in _LEVELS[1:]:
        required = [a for a in ADVERSARIES if _LEVEL_OF.get(a) == level]
        if not required:
            break
        if all(outcomes.get(a) == OUTCOME_DEFENDED for a in required):
            achieved = level
        else:
            break
    return achieved


def build_attestation(
    outcomes: "Mapping[str, str]",
    *,
    level_claimed: str,
    engine_version: str,
    adapter_identity: str,
    host_identity: str,
    timestamp: str,
) -> "dict[str, object]":
    """Build the §4 attestation artifact. This, not the README, is the record."""
    published = {name: outcomes.get(name, _tokens.ADVERSARY_NOT_EXECUTED)
                 for name in ADVERSARIES}
    for name in _DECLARATIVE:
        # Present and stated: absence would be exactly the silence §8 forbids.
        published[name] = _tokens.ADVERSARY_NOT_EXECUTED
    return {
        "level_claimed": level_claimed,
        "level_achieved": _level_achieved(published),
        "adversaries": published,
        "engine_version": engine_version,
        "adapter_identity": adapter_identity,
        "host_identity": host_identity,
        "timestamp": timestamp,
    }


def validate_attestation(attestation: "Mapping[str, object]") -> "tuple[str, ...]":
    """Return complaints against `attestation`; empty when it is publishable.

    Publishing a level we have not measured against ourselves is the one
    failure mode the frozen document exists to prevent, so the claim is checked
    against the recorded outcomes rather than trusted.
    """
    complaints: "list[str]" = []

    adversaries = attestation.get("adversaries")
    if not isinstance(adversaries, dict):
        return ("attestation carries no adversaries mapping",)

    claimed = attestation.get("level_claimed")
    achieved = attestation.get("level_achieved")
    recomputed = _level_achieved(adversaries)
    if achieved != recomputed:
        complaints.append(
            f"level_achieved {achieved!r} does not follow from the recorded "
            f"outcomes (recomputed {recomputed!r})"
        )
    if claimed in _LEVELS and recomputed in _LEVELS:
        if _LEVELS.index(claimed) > _LEVELS.index(recomputed):
            not_defended = sorted(
                a for a in ADVERSARIES
                if adversaries.get(a) != OUTCOME_DEFENDED
            )
            complaints.append(
                f"level_claimed {claimed!r} exceeds what was measured "
                f"({recomputed!r}); not defended: {not_defended}"
            )
    for name in ADVERSARIES:
        if name not in adversaries:
            complaints.append(f"{name}: no outcome recorded")
    for name in _DECLARATIVE:
        if name not in adversaries:
            complaints.append(
                f"{name}: declarative adversary must be published as "
                f"{_tokens.ADVERSARY_NOT_EXECUTED!r}, not omitted"
            )

    return tuple(complaints)


def _main(argv: "list[str] | None" = None) -> int:
    """CLI: load `module:factory`, run the adapter adversaries, publish.

    Exit 0: written and publishable. 1: written, with complaints on stderr.
    2: nothing written (bad usage, unloadable adapter, bad identity).
    """
    import argparse  # noqa: PLC0415
    import importlib  # noqa: PLC0415
    import json  # noqa: PLC0415
    import sys  # noqa: PLC0415
    from datetime import datetime, timezone  # noqa: PLC0415

    parser = argparse.ArgumentParser(
        prog="python -m bytedigger_engine.conformance.harness",
        allow_abbrev=False,
    )
    parser.add_argument("--adapter", required=True, help="module:factory")
    parser.add_argument("--level-claimed", required=True)
    parser.add_argument("--engine-version", required=True)
    parser.add_argument("--host-identity", required=True)
    parser.add_argument("--timestamp", default=None)
    parser.add_argument("--timeout-s", type=float, default=30.0)
    parser.add_argument("--out", default=None)
    args = parser.parse_args(argv)  # usage errors exit 2, nothing on stdout

    module_name, sep, factory_name = args.adapter.partition(":")
    if not sep or not module_name or not factory_name:
        print(f"error: --adapter must be module:factory, got {args.adapter!r}",
              file=sys.stderr)
        return 2
    try:
        module = importlib.import_module(module_name)
        factory = getattr(module, factory_name)
        adapter = factory()
        identity = adapter_identity(adapter)
    except Exception as exc:  # noqa: BLE001 — every load refusal is exit 2
        print(f"error: cannot load adapter {args.adapter!r}: "
              f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return 2

    timestamp = args.timestamp or (
        datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    )
    outcomes = run_adversaries(adapter=adapter, timeout_s=args.timeout_s)
    att = build_attestation(
        outcomes,
        level_claimed=args.level_claimed,
        engine_version=args.engine_version,
        adapter_identity=identity,  # type: ignore[arg-type]
        host_identity=args.host_identity,
        timestamp=timestamp,
    )
    text = json.dumps(att, sort_keys=True) + "\n"
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
    else:
        sys.stdout.write(text)
    complaints = validate_attestation(att)
    for complaint in complaints:
        print(complaint, file=sys.stderr)
    return 1 if complaints else 0


if __name__ == "__main__":
    raise SystemExit(_main())
