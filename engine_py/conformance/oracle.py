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
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

# ── `[bd8:7]` The mapping is DECLARED, under registry names, never inferred ──
ORACLE_WORKFLOWS: frozenset[str] = frozenset({"phase_45_spec", "phase_45_spec_lite"})
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
        text = p.read_text(encoding="utf-8")
    except OSError as e:
        raise OracleRefusal(
            "E_ORACLE_INDETERMINATE", f"event log could not be read: {p} ({e})"
        ) from e
    events = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            events.append(json.loads(line))
        except ValueError as e:
            raise OracleRefusal(
                "E_ORACLE_INDETERMINATE",
                f"event log line {lineno} is not valid JSON: {p} ({e})",
            ) from e
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
        ev_run = e.get("run_id") or (e.get("payload") or {}).get("run_id")
        if run_id and ev_run and ev_run != run_id:
            continue  # `[bd8:8a]` fail-closed cross-check
        candidates.append(e)
    return candidates[-1] if candidates else None


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
