"""BD-L1 oracle freeze-and-verify (bd#8).

Frozen spec: engine_py/conformance/ORACLE_SPEC.md (FROZEN v7).

`[bd8:5]` This module is the seam: pure stdlib, NO I/O at import (the bd#22
AC-C1 package invariant binds it). It exposes the digest constructions, the
comparison, the two payload builders and the log lookup. It does not know
about `run.py`, the workflow registry or the CLI — `run.py` passes paths and
payloads and computes nothing (§1f).

`[bd8:1]` The oracle set is the NON-RECURSIVE, regular-files-only listing of
`<scratchpad_dir>/specs`, recorded relative to `scratchpad_dir`. This is a
reversal of v1-v4, which took the set from `phase_artifacts.written` and
justified it by §1g. G9 measured that premise false: `self._written` is fed
only by the git delta of `org_config["git_cwd"]` (engine.py:493), while both
spec workflows write their documents under `org_config["scratchpad_dir"]`.
`phase_artifacts.written` survives as a recorded cross-check (`[bd8:1b]`),
never as the source.

LISTING DISCIPLINE. `Path.iterdir()`, never `glob.glob("*")` — the latter
drops leading-dot names, which would let an implementing actor smuggle
`specs/.hidden.md` past ADV-2. This applies to the member listing at freeze
time as well as to the scope listing, and only the latter is test-forced
(gate round 5, warning 2).

`bd#154` The bd#8 half also carries `_main`, a read-only host CLI
(run this module with `python -m`, subcommand `verify`) that prints the
`verify` verdict as one JSON line. It imports no `bytedigger_engine` module, so
the seam stays below `run.py`.

────────────────────────────────────────────────────────────────────────
SECOND LOT IN THIS MODULE — bd#38 (L3, child of bd#27)

This file is shared by two lots that collided by FILE NAME only; the symbol
sets are disjoint and the halves complement each other. Everything above
belongs to bd#8 (freeze-and-verify). Everything below the divider belongs to
bd#38: `OracleOutcome`, `Oracle` (Protocol) and `evaluate_guarded`.

Frozen spec for that half: the second spec in ORACLE_SPEC.md.

The bd#38 half exists so that an INDETERMINATE result cannot silently collapse
into a pass or a fail: `bool(outcome)` raises, `outcome == True` raises, there
is no boolean constructor, and there is no mixin base. `evaluate_guarded`
converts every failure to evaluate — exception, load error, timeout,
non-`OracleOutcome` return — into INDETERMINATE with a reason, never into
REJECTED, and re-raises `KeyboardInterrupt`/`SystemExit` rather than catching
them. Its timeout is deliberately NOT `signal`-based, and bd#38's `AC-E9`
asserts by AST that this module imports `signal` in no form — an assertion that
now covers bd#8's code as well.
"""
from __future__ import annotations

import hashlib
import json
import threading
from enum import Enum
from pathlib import Path
from typing import Any, Iterable, Protocol

# ── `[bd8:7]` The mapping is DECLARED, under registry names, never inferred ──
ORACLE_WORKFLOWS: frozenset[str] = frozenset({"phase_45_spec"})
IMPLEMENTING_WORKFLOWS: frozenset[str] = frozenset({"phase_5_implement"})

# `[bd8:1]` The document directory, relative to scratchpad_dir. External
# provenance: phase_45_spec.py:330-331 (SPEC_DOC_RELPATH, REVIEW_DOC_RELPATH).
DOC_DIR = "specs"

FROZEN_EVENT = "oracle_frozen"
AMENDED_EVENT = "oracle_amended"
AMENDMENT_REASON_KEY = "oracle_amendment_reason"  # `[bd8:10a]`, via --ctx-json

# `[bd8:2b]` Category tokens (AC-4): the message carries exactly one.
TOKEN_CONTENT = "mutated:content"
TOKEN_ADDED = "mutated:added"
TOKEN_REMOVED = "mutated:removed"


class OracleRefusal(Exception):
    """A BD-L1 refusal, carrying the §5 code.

    `[bd8:6a]`: this is never allowed to escape `run.py main()`'s try block —
    `run.py` catches it and reports it on the StepResult with `error_code` set
    verbatim and `recoverable=False`. Raising past `main()` would map it to
    `E_RUNNER`/`E_FILE_NOT_FOUND` and hide every refusal from the restart
    governor, `--status` and `derive_state`.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class OracleMalformedFreeze(OracleRefusal):
    """`[bd8:6a]` bd#158: an `oracle_frozen` / `oracle_amended` row whose shape is
    unreadable. Is an `OracleRefusal`, so `run.py`'s existing handler reports it
    as `E_ORACLE_INDETERMINATE` (recoverable=False); the record is unreadable,
    which is not a mutation."""

    def __init__(self, message: str) -> None:
        super().__init__("E_ORACLE_INDETERMINATE", "malformed freeze event: " + message)


def check_freeze_payload(payload: Any) -> None:
    """bd#158: pure shape check of a freeze/amendment payload; raises
    `OracleMalformedFreeze`. Shape only: empty strings pass, extra keys are
    ignored, legacy bare-`str` members and absent/`None` members/scope pass."""
    if not isinstance(payload, dict):
        raise OracleMalformedFreeze(f"payload is {type(payload).__name__}, not an object")
    if not isinstance(payload.get("digest"), str):
        raise OracleMalformedFreeze("digest is missing or not a string")
    paths: list[str] = []
    members = payload.get("members")
    if members is not None:
        if not isinstance(members, list):
            raise OracleMalformedFreeze("members is not a list")
        for m in members:
            if isinstance(m, str):
                paths.append(m)
            elif isinstance(m, dict):
                if not isinstance(m.get("path"), str):
                    raise OracleMalformedFreeze("member path is missing or not a string")
                if not isinstance(m.get("digest"), str):
                    raise OracleMalformedFreeze("member digest is missing or not a string")
                paths.append(m["path"])
            else:
                raise OracleMalformedFreeze(
                    f"member is {type(m).__name__}, not a string or object")
    scope = payload.get("scope")
    if scope is not None and scope != []:
        if not isinstance(scope, list):
            raise OracleMalformedFreeze("scope is not a list")
        if not all(isinstance(s, str) for s in scope):
            raise OracleMalformedFreeze("scope holds a non-string entry")
        paths.extend(scope)
    if not isinstance(payload.get("scope_digest"), str):
        raise OracleMalformedFreeze("scope_digest is missing or not a string")
    if any("\x00" in p for p in paths):  # the OS path layer rejects NUL with ValueError
        raise OracleMalformedFreeze("a member path or scope entry contains a NUL byte")


def is_oracle_workflow(workflow_name: str) -> bool:
    return workflow_name in ORACLE_WORKFLOWS


def is_implementing_workflow(workflow_name: str) -> bool:
    return workflow_name in IMPLEMENTING_WORKFLOWS


# ─────────────────────────────────────────────────────────────────────────
# The set, and the two digest constructions
# ─────────────────────────────────────────────────────────────────────────

def doc_dir(scratchpad_dir: str | Path) -> Path:
    return Path(scratchpad_dir) / DOC_DIR


def list_members(scratchpad_dir: str | Path) -> list[str]:
    """`[bd8:1]`/`[bd8:3]`: sorted paths relative to `scratchpad_dir`.

    Non-recursive, regular files only, `iterdir()` so dotfiles are INCLUDED.
    Raises `OracleRefusal(E_ORACLE_INDETERMINATE)` when the directory is
    absent, unreadable, or holds no regular file (`[bd8:4a]`, AC-16) — an
    empty oracle makes every subsequent verify pass trivially.
    """
    d = doc_dir(scratchpad_dir)
    try:
        entries = sorted(p for p in d.iterdir() if p.is_file())
    except (FileNotFoundError, NotADirectoryError) as e:
        raise OracleRefusal(
            "E_ORACLE_INDETERMINATE",
            f"oracle document directory is absent: {d} ({e.__class__.__name__})",
        ) from e
    except OSError as e:
        raise OracleRefusal(
            "E_ORACLE_INDETERMINATE",
            f"oracle document directory could not be listed: {d} ({e})",
        ) from e
    if not entries:
        raise OracleRefusal(
            "E_ORACLE_INDETERMINATE",
            f"oracle document directory holds no regular file: {d} — a zero-member "
            "oracle makes every subsequent verify pass trivially",
        )
    return [f"{DOC_DIR}/{p.name}" for p in entries]


def _read_member(scratchpad_dir: str | Path, relpath: str, when: str) -> bytes:
    """`[bd8:4]`/AC-17(i): a member that cannot be read is NOT a zero-byte
    member, at freeze OR at verify."""
    try:
        return (Path(scratchpad_dir) / relpath).read_bytes()
    except OSError as e:
        raise OracleRefusal(
            "E_ORACLE_INDETERMINATE",
            f"{when}: oracle member could not be read: {relpath} ({e.__class__.__name__})",
        ) from e


def member_digest(scratchpad_dir: str | Path, relpath: str, when: str = "freeze") -> str:
    """`[bd8:2]`: BARE lowercase hex sha256 of the member's bytes."""
    return hashlib.sha256(_read_member(scratchpad_dir, relpath, when)).hexdigest()


def compute_digest(scratchpad_dir: str | Path, relpaths: list[str],
                   when: str = "freeze") -> str:
    """`[bd8:2]`: `<relpath>\\0<sha256>` per member in sorted order, joined by
    "\\n", UTF-8; digest = "sha256:" + sha256(that)."""
    lines = [
        f"{rel}\0{member_digest(scratchpad_dir, rel, when)}"
        for rel in sorted(relpaths)
    ]
    return "sha256:" + hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def compute_scope(relpaths: list[str]) -> list[str]:
    """`[bd8:2b]`: the directories holding at least one member, non-recursively.
    Derived from the member paths and from nothing else."""
    return sorted({str(Path(rel).parent) for rel in relpaths})


def compute_scope_digest(scratchpad_dir: str | Path, scope: list[str],
                         when: str = "freeze") -> str:
    """`[bd8:2b]`: per scope dir, `<reldir>\\0<file names sorted, "\\n"-joined>`;
    lines joined by "\\n", UTF-8.

    Regular files only — a subdirectory appearing or disappearing does not move
    the digest. `iterdir()`, so dotfiles count (that is the ADV-2 shape E34
    forced). A scope directory gone at verify time is `mutated:removed`
    (AC-17(ii)), never an escaping exception.
    """
    lines = []
    for reldir in sorted(scope):
        d = Path(scratchpad_dir) / reldir
        try:
            names = sorted(p.name for p in d.iterdir() if p.is_file())
        except (FileNotFoundError, NotADirectoryError) as e:
            raise OracleRefusal(
                "E_ORACLE_MUTATED",
                f"{TOKEN_REMOVED}: oracle scope directory no longer exists: {reldir} "
                f"({e.__class__.__name__})",
            ) from e
        except OSError as e:
            raise OracleRefusal(
                "E_ORACLE_INDETERMINATE",
                f"{when}: oracle scope directory could not be listed: {reldir} ({e})",
            ) from e
        lines.append(f"{reldir}\0" + "\n".join(names))
    return "sha256:" + hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


# ─────────────────────────────────────────────────────────────────────────
# `[bd8:1b]` The cross-check — recorded, never a gate, never a constant
# ─────────────────────────────────────────────────────────────────────────

def crosscheck_from_payload(payload: dict[str, Any] | None) -> Any:
    """Copy the oracle phase's own `phase_artifacts.written` VERBATIM.

    A truncated payload contributes its MARKER, never the binary-searched
    sample the payload also carries (engine.py:1329-1336) — otherwise the log
    would imply the engine saw a membership it did not (the surviving half of
    G1).
    """
    if not payload:
        return []
    if payload.get("written_truncated") is True:
        return {
            "truncated": True,
            "count": payload.get("written_count"),
            "digest": payload.get("written_digest"),
        }
    return list(payload.get("written") or [])


# ─────────────────────────────────────────────────────────────────────────
# Payload builders
# ─────────────────────────────────────────────────────────────────────────

def build_freeze_payload(phase: str, run_id: str | None, scratchpad_dir: str | Path,
                         written_crosscheck: Any) -> dict[str, Any]:
    """`[bd8:8]` + `[bd8:2]`/`[bd8:2b]`/`[bd8:1b]`."""
    members = list_members(scratchpad_dir)
    scope = compute_scope(members)
    return {
        "phase": phase,
        "run_id": run_id,
        "member_count": len(members),
        "digest": compute_digest(scratchpad_dir, members),
        "members": [
            {"path": rel, "digest": member_digest(scratchpad_dir, rel)}
            for rel in members
        ],
        "scope": scope,
        "scope_digest": compute_scope_digest(scratchpad_dir, scope),
        "written_crosscheck": written_crosscheck,
    }


def build_amendment_payload(phase: str, run_id: str | None, scratchpad_dir: str | Path,
                            reason: str | None, previous_digest: str,
                            written_crosscheck: Any) -> dict[str, Any]:
    """`[bd8:10]`: the FULL payload, with `scope`/`scope_digest` RECOMPUTED.

    An amendment that omitted the scope half — paired with a verify that skips
    the scope check when the event carries none — would disable ADV-2 for the
    rest of any build that amends, which is the normal multi-cycle spec path.
    """
    if not (reason or "").strip():
        raise OracleRefusal(
            "E_ORACLE_AMENDMENT_UNREASONED",
            "oracle amendment requires a non-empty "
            f"org_config[{AMENDMENT_REASON_KEY!r}]; re-entering the oracle phase "
            "without one is not a way to change the frozen set",
        )
    payload = build_freeze_payload(phase, run_id, scratchpad_dir, written_crosscheck)
    payload["reason"] = reason
    payload["previous_digest"] = previous_digest
    return payload


# ─────────────────────────────────────────────────────────────────────────
# `[bd8:8]`/`[bd8:8a]`/`[bd8:9]` The lookup — LOG-scoped, run_id cross-checked
# ─────────────────────────────────────────────────────────────────────────

def read_log_events(event_log_path: str | Path | None) -> list[dict[str, Any]]:
    """§5: a log that cannot be read is `E_ORACLE_INDETERMINATE`, never an
    escaping ValueError (which `run.py` would report as `E_BAD_CTX`)."""
    if not event_log_path:
        return []
    p = Path(event_log_path)
    if not p.exists():
        return []
    try:
        text = p.read_bytes().decode("utf-8")
    except OSError as e:
        raise OracleRefusal(
            "E_ORACLE_INDETERMINATE", f"event log could not be read: {p} ({e})"
        ) from e
    except UnicodeDecodeError as e:  # [bd8:5] bd#154: strict UTF-8, no escaping ValueError
        raise OracleRefusal(
            "E_ORACLE_INDETERMINATE", f"event log is not valid UTF-8: {p} ({e})"
        ) from e
    events = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            ev = json.loads(line)
        except ValueError as e:
            raise OracleRefusal(
                "E_ORACLE_INDETERMINATE",
                f"event log line {lineno} is not valid JSON: {p} ({e})",
            ) from e
        if not isinstance(ev, dict):  # bd#154: find_last_freeze calls .get on every row
            raise OracleRefusal(
                "E_ORACLE_INDETERMINATE",
                f"event log line {lineno} is not a JSON object: {p}",
            )
        events.append(ev)
    return events


def find_last_freeze(events: list[dict[str, Any]], run_id: str | None) -> dict[str, Any] | None:
    """`[bd8:9]`: FILTER by run_id first (when BOTH carry one), THEN take the
    last survivor.

    Last-then-cross-check would make a second build sharing one log fail on the
    first build's freeze.
    """
    candidates = []
    for e in events:
        if e.get("event_type") not in (FROZEN_EVENT, AMENDED_EVENT):
            continue
        row_payload = e.get("payload")  # bd#158: a non-dict payload must not raise here
        ev_run = e.get("run_id") or (
            row_payload.get("run_id") if isinstance(row_payload, dict) else None)
        if run_id and ev_run and ev_run != run_id:
            continue  # `[bd8:8a]` fail-closed cross-check
        candidates.append(e)
    if not candidates:
        return None
    last = candidates[-1]
    # bd#158: only the SELECTED row is shape-checked, so callers can index
    # `last["payload"]` as a well-formed dict.
    if "payload" not in last:
        raise OracleMalformedFreeze("row has no payload")
    check_freeze_payload(last["payload"])
    return last


def last_phase_artifacts(events: list[dict[str, Any]], phase: str,
                         run_id: str | None) -> dict[str, Any] | None:
    """The oracle phase's OWN `phase_artifacts` payload, for `[bd8:1b]`."""
    found = None
    for e in events:
        if e.get("event_type") != "phase_artifacts":
            continue
        payload = e.get("payload") or {}
        if payload.get("phase") != phase:
            continue
        if run_id and e.get("run_id") and e.get("run_id") != run_id:
            continue
        found = payload
    return found


def has_sentinel_resume(events: list[dict[str, Any]], run_id: str | None) -> bool:
    """`[bd8:10b]`: a sentinel-served phase did not `execute()`, so it is
    neither a re-entry nor an amendment — it is a no-op for this lot."""
    for e in events:
        if e.get("event_type") != "phase_sentinel_resumed":
            continue
        if run_id and e.get("run_id") and e.get("run_id") != run_id:
            continue
        return True
    return False


# ─────────────────────────────────────────────────────────────────────────
# The comparison
# ─────────────────────────────────────────────────────────────────────────

def verify_against(frozen_payload: dict[str, Any], scratchpad_dir: str | Path) -> None:
    """Recompute BOTH digests over the live tree and refuse on either.

    Order is load-bearing and is pinned by AC-4 / AC-3 / AC-17 through the
    exactly-one-token rule: removal before scope, unreadable before content,
    content before addition.
    """
    check_freeze_payload(frozen_payload)  # bd#158: public entry, may get a bare payload
    try:
        frozen_members = [
            m["path"] if isinstance(m, dict) else m
            for m in frozen_payload.get("members") or []
        ]
        scope = list(frozen_payload.get("scope") or compute_scope(frozen_members))

        # 1. Removal — a member gone, or the whole scope directory gone.
        #    compute_scope_digest raises mutated:removed for the directory case.
        missing = [rel for rel in frozen_members
                   if not (Path(scratchpad_dir) / rel).exists()]
        live_scope_digest = compute_scope_digest(scratchpad_dir, scope, when="verify")
        if missing:
            raise OracleRefusal(
                "E_ORACLE_MUTATED",
                f"{TOKEN_REMOVED}: frozen oracle member(s) no longer exist: "
                f"{', '.join(sorted(missing))}",
            )

        # 2. Content — reads each member; an unreadable one is INDETERMINATE
        #    (AC-17(i)), not a zero-byte member and not a content mismatch.
        changed = []
        for m in frozen_payload.get("members") or []:
            if not isinstance(m, dict):
                continue
            live = member_digest(scratchpad_dir, m["path"], when="verify")
            if live != m.get("digest"):
                changed.append(m["path"])
        if changed:
            raise OracleRefusal(
                "E_ORACLE_MUTATED",
                f"{TOKEN_CONTENT}: frozen oracle member(s) rewritten: "
                f"{', '.join(sorted(changed))}",
            )

        live_digest = compute_digest(scratchpad_dir, frozen_members, when="verify")
        if live_digest != frozen_payload.get("digest"):
            raise OracleRefusal(
                "E_ORACLE_MUTATED",
                f"{TOKEN_CONTENT}: oracle digest mismatch over members "
                f"{sorted(frozen_members)}",
            )

        # 3. Addition — membership is inside `digest`, but a NEW file beside the
        #    members leaves it invariant (`[bd8:2b]`), so the scope digest carries
        #    this half.
        if live_scope_digest != frozen_payload.get("scope_digest"):
            raise OracleRefusal(
                "E_ORACLE_MUTATED",
                f"{TOKEN_ADDED}: oracle scope changed in {scope} — a file was added "
                "to the oracle set without re-entering the oracle phase",
            )
    except (ValueError, OSError) as e:  # bd#158: path-layer error, never an escape
        raise OracleRefusal(
            "E_ORACLE_INDETERMINATE",
            f"verify: path layer error ({e.__class__.__name__}: {e})",
        ) from e


def _main(argv: "list[str] | None" = None) -> int:
    """`[bd8:5]` bd#154: read-only host CLI, `verify` only. Prints one JSON line;
    rc 0 on any verdict, rc 2 on usage or malformed-freeze input (stderr only).
    argparse/sys are imported here so the module gains no attribute and does no
    work at import."""
    import argparse
    import sys

    class _Parser(argparse.ArgumentParser):
        def error(self, message):  # type: ignore[override]
            sys.stderr.write(f"oracle: {message}\n")
            raise SystemExit(2)

    parser = _Parser(prog="oracle", allow_abbrev=False)
    sub = parser.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("verify", allow_abbrev=False)
    v.add_argument("--event-log", required=True)
    v.add_argument("--run-id", required=True)
    v.add_argument("--scratchpad-dir", required=True)
    args = parser.parse_args(argv)

    if not args.run_id.strip():
        # find_last_freeze skips its run filter on a falsy id (another run's freeze).
        sys.stderr.write("oracle: --run-id must be non-empty\n")
        return 2

    refusal: "OracleRefusal | None" = None
    event_type = None
    frozen_digest = None
    current_digest = None
    try:
        events = read_log_events(args.event_log)
        frozen = find_last_freeze(events, args.run_id)
        if frozen is None:
            refusal = OracleRefusal("E_ORACLE_UNFROZEN", "no oracle freeze found for this run")
        else:
            payload = frozen["payload"]
            if not isinstance(payload, dict) or not isinstance(payload.get("digest"), str):
                raise ValueError("freeze payload is not an object with a string digest")
            event_type = frozen.get("event_type")
            frozen_digest = payload["digest"]
            try:
                verify_against(payload, args.scratchpad_dir)
            except OracleMalformedFreeze as e:  # bd#158: before OracleRefusal
                sys.stderr.write(f"oracle: {e.message}\n")
                return 2
            except OracleRefusal as e:
                refusal = e
            paths = [
                m["path"] if isinstance(m, dict) else m
                for m in payload.get("members") or []
            ]
            try:
                current_digest = compute_digest(args.scratchpad_dir, paths, when="verify")
            except OracleRefusal:
                current_digest = None
    except OracleMalformedFreeze as e:  # bd#158: before OracleRefusal
        sys.stderr.write(f"oracle: {e.message}\n")
        return 2
    except OracleRefusal as e:
        refusal = e
    except (KeyError, TypeError, AttributeError, ValueError) as e:
        sys.stderr.write(f"oracle: malformed freeze event: {e.__class__.__name__}: {e}\n")
        return 2

    outcome = "verified"
    token = None
    if refusal is not None:
        outcome = {
            "E_ORACLE_UNFROZEN": "unfrozen",
            "E_ORACLE_MUTATED": "mutated",
            "E_ORACLE_INDETERMINATE": "indeterminate",
        }[refusal.code]
        for t in (TOKEN_CONTENT, TOKEN_ADDED, TOKEN_REMOVED):
            if outcome == "mutated" and refusal.message.startswith(t):
                token = t
    sys.stdout.write(json.dumps({
        "outcome": outcome,
        "code": refusal.code if refusal is not None else None,
        "token": token,
        "message": refusal.message if refusal is not None else None,
        "event_type": event_type,
        "frozen_digest": frozen_digest,
        "current_digest": current_digest,
        "run_id": args.run_id,
    }, separators=(",", ":"), allow_nan=False) + "\n")
    return 0

# ═════════════════════════════════════════════════════════════════════════
# bd#38 (L3) — three-state outcome + guarded evaluation
# Nothing below this line is referenced by the bd#8 half above, and nothing
# above is referenced below. Two lots, one file, disjoint symbols.
# ═════════════════════════════════════════════════════════════════════════



class OracleOutcome(Enum):
    """A verdict that cannot be collapsed into a boolean.

    `AC-O5`: no mixin base — the MRO is exactly `(OracleOutcome, Enum, object)`,
    so the value never serialises as a bare scalar.
    """

    REJECTED = "rejected"
    ACCEPTED = "accepted"
    INDETERMINATE = "indeterminate"

    def __bool__(self) -> bool:
        """AC-O1. Enum members are truthy by default, so `if outcome:` would read
        INDETERMINATE as accepted — the exact collapse this type exists to
        prevent."""
        raise TypeError(
            "OracleOutcome has no truth value: compare members explicitly "
            "(e.g. `outcome is OracleOutcome.ACCEPTED`)"
        )

    def __eq__(self, other: object) -> bool:
        """AC-O2. Equality against `bool` is the second collapse path.

        Non-`bool` operands compare by identity as usual, so members remain
        usable in the ordinary way.
        """
        if isinstance(other, bool):
            raise TypeError(
                "OracleOutcome cannot be compared to bool: an indeterminate "
                "verdict must not collapse into a pass or a fail"
            )
        return self is other

    def __ne__(self, other: object) -> bool:
        if isinstance(other, bool):
            raise TypeError(
                "OracleOutcome cannot be compared to bool: an indeterminate "
                "verdict must not collapse into a pass or a fail"
            )
        return self is not other

    # Defining __eq__ sets __hash__ to None, which would break set and dict-key
    # use of the members. Restored explicitly.
    __hash__ = object.__hash__


class Oracle(Protocol):
    """Structural interface an oracle plugin must satisfy.

    `freeze` is declared for typing; the module-level freeze implementation
    (`AC-F1`..`AC-F14`) belongs to a different lot and is not defined here.
    """

    def freeze(self, paths: Iterable[Path], *, root: Path) -> str: ...

    def evaluate(self, state: Any) -> OracleOutcome: ...


def evaluate_guarded(
    oracle: Oracle, state: Any, *, timeout_s: float | None = None
) -> tuple[OracleOutcome, str | None]:
    """Evaluate `oracle` against `state`, converting every failure mode into
    `INDETERMINATE` rather than into a verdict.

    Returns `(outcome, reason)`. `reason` is `None` for a clean `ACCEPTED` or
    `REJECTED` and a non-empty string whenever the outcome is `INDETERMINATE`.

    `KeyboardInterrupt` and `SystemExit` are re-raised, never converted.
    """
    box: dict[str, Any] = {}

    def _run() -> None:
        try:
            box["value"] = oracle.evaluate(state)
        except BaseException as exc:  # noqa: BLE001 — triaged below, in the caller
            box["exc"] = exc

    worker = threading.Thread(
        target=_run, name="conformance-oracle-guard", daemon=True
    )
    worker.start()
    worker.join(timeout_s)

    # AC-E3/AC-E10: abandon the worker, never join it unconditionally. It is a
    # daemon thread, so leaving it running cannot hang interpreter shutdown.
    if worker.is_alive():
        return (
            OracleOutcome.INDETERMINATE,
            f"oracle did not finish within the {timeout_s}s guard",
        )

    if "exc" in box:
        exc = box["exc"]
        # AC-E7: KeyboardInterrupt/SystemExit are not Exception subclasses and
        # are not ours to convert. Re-raised in the caller's thread, since a
        # BaseException raised in a worker would otherwise be swallowed.
        if not isinstance(exc, Exception):
            raise exc
        # AC-E1/AC-E2: any Exception, load errors included, is a failure to
        # evaluate — not a rejection.
        return (
            OracleOutcome.INDETERMINATE,
            f"oracle raised {type(exc).__name__}: {exc}",
        )

    value = box.get("value")

    # AC-E4: an adapter returning a bool (or a lookalike, or a same-named member
    # of a different Enum) has not implemented the interface. Coercing it would
    # reintroduce the collapse, so it is refused rather than interpreted.
    if not isinstance(value, OracleOutcome):
        return (
            OracleOutcome.INDETERMINATE,
            f"oracle returned {type(value).__name__}, not an OracleOutcome",
        )

    # AC-E6: clean verdicts pass through untouched.
    return (value, None)


if __name__ == "__main__":
    import sys
    sys.exit(_main())
