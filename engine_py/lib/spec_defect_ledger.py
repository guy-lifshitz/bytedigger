"""Durable spec-defect reroute ledger + budget primitives (GH767 §2.2).

Cross-**process** state: every phase is a separate ``run.py`` process, so an
in-memory retry budget is useless here. This module durably tracks distinct
defective-spec content hashes seen under a given ``run_id`` (the reroute
ledger), one-shot cache-busting consumed markers, and a run-id-independent
attempt floor that also reads the append-only event log (the only bound that
rotates with nothing).

Core-clean: no HAL_* env, no host path literals (core-boundary lint applies).
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from typing import Any

_LIB_DIR = str(Path(__file__).resolve().parent)
if _LIB_DIR not in sys.path:
    sys.path.insert(0, _LIB_DIR)

from io_utils import atomic_write  # noqa: E402


def _resolve_scratchpad_from_ctx(context: Any) -> "Path | None":
    """Return the scratchpad Path from ``context.org_config``, or None if
    unresolvable. Degrades silently (never raises) — mirrors
    lib/step_sentinel._resolve_scratchpad, NOT the raising
    workflows/phase_workflows_common._resolve_scratchpad."""
    cfg = getattr(context, "org_config", None) or {}
    raw = cfg.get("scratchpad_dir")
    if not raw:
        return None
    return Path(raw)


def _ledger_path(scratchpad: "Path | str", run_id: str) -> Path:
    return Path(scratchpad) / "resume" / f"spec-defect-reroutes-{run_id}.json"


def _consumed_marker_path(scratchpad: "Path | str", run_id: str, workflow_name: str, attempt: Any) -> Path:
    return Path(scratchpad) / "resume" / f"reroute-consumed-{run_id}-{workflow_name}-{attempt}.marker"


def spec_sha(spec_path: "Path | str") -> "str | None":
    """sha256(bytes)[:12] of the spec file content. None on OSError (unreadable spec)."""
    try:
        data = Path(spec_path).read_bytes()
    except OSError:
        return None
    return hashlib.sha256(data).hexdigest()[:12]


def read_reroute_ledger(scratchpad: "Path | str", run_id: str) -> "list[str] | None":
    """Read the distinct-spec-sha reroute ledger.

    Returns ``[]`` for a legitimately absent file (durably empty — this is
    genuinely the first reroute). Returns ``None`` for corrupt/unreadable
    content (untrustworthy — never treated the same as a genuine absence).
    """
    path = _ledger_path(scratchpad, run_id)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    reroutes = data.get("reroutes")
    if not isinstance(reroutes, list) or not all(isinstance(item, str) for item in reroutes):
        return None
    return reroutes


def record_reroute(scratchpad: "Path | str", run_id: str, sha: str) -> "list[str] | None":
    """Append-if-absent the given spec sha, write atomically, then read back
    and verify the sha is present. Returns the new ledger on verified
    persistence; returns None if it cannot be durably persisted (unresolvable
    scratchpad, mkdir/write OSError, read-back mismatch, corrupt JSON)."""
    scratchpad = Path(scratchpad)
    current = read_reroute_ledger(scratchpad, run_id)
    if current is None:
        return None
    if sha in current:
        return current
    new_ledger = current + [sha]
    try:
        resume_dir = scratchpad / "resume"
        resume_dir.mkdir(parents=True, exist_ok=True)
        atomic_write(_ledger_path(scratchpad, run_id), json.dumps({"reroutes": new_ledger}))
    except OSError:
        return None
    verify = read_reroute_ledger(scratchpad, run_id)
    if verify is None or sha not in verify:
        return None
    return verify


def reroute_already_consumed(context: Any, run_id: str, workflow_name: str, attempt: Any) -> bool:
    """True iff the one-shot consumed-marker for (run_id, workflow_name, attempt)
    is already durably recorded. Unresolvable scratchpad -> False (never
    treated as already-consumed)."""
    scratchpad = _resolve_scratchpad_from_ctx(context)
    if scratchpad is None:
        return False
    return _consumed_marker_path(scratchpad, run_id, workflow_name, attempt).exists()


def mark_reroute_consumed(context: Any, run_id: str, workflow_name: str, attempt: Any) -> bool:
    """Durably persist the one-shot consumed-marker. Returns False if it
    cannot be written (unresolvable scratchpad, mkdir/write OSError) —
    callers must NOT invalidate anything when this returns False (§2.4
    mark-then-invalidate fail-safe)."""
    scratchpad = _resolve_scratchpad_from_ctx(context)
    if scratchpad is None:
        return False
    try:
        resume_dir = scratchpad / "resume"
        resume_dir.mkdir(parents=True, exist_ok=True)
        path = _consumed_marker_path(scratchpad, run_id, workflow_name, attempt)
        atomic_write(path, json.dumps({"marked": True}))
        return path.exists()
    except OSError:
        return False


def build_key(context: Any) -> str:
    """sha256(question || task_description)[:12] — stable across a reroute
    (the task does not change), distinct across builds. Deliberately NOT
    compute_ctx_hash (which folds decision_doc bytes and returns None when
    both fields are unset). Never None/empty."""
    question = getattr(context, "question", "") or ""
    task = (getattr(context, "org_config", None) or {}).get("task_description") or ""
    return hashlib.sha256(question.encode("utf-8") + b"\0" + str(task).encode("utf-8")).hexdigest()[:12]


def attempts_from_event_log(run_ctx: Any, bkey: str) -> int:
    """Count DISTINCT spec_sha across prior `spec_defect_detected` events
    whose build_key matches bkey. Run-id-agnostic by construction (never
    reads run_id). Unreadable/absent/corrupt log -> 0, never raises."""
    if run_ctx is None:
        return 0
    log = getattr(run_ctx, "event_log", None)
    if log is None:
        return 0
    try:
        events = log.read_all()
    except (ValueError, OSError):
        return 0
    shas: set[str] = set()
    for event in events:
        if event.get("event_type") != "spec_defect_detected":
            continue
        payload = event.get("payload") or event.get("data") or {}
        if payload.get("build_key") != bkey:
            continue
        sha = payload.get("spec_sha")
        if sha:
            shas.add(sha)
    return len(shas)


def attempts_floor(ctx: Any, run_ctx: Any, ledger: "list[str]") -> int:
    """Run-id-independent budget floor — max of three independent sources:
    the ledger (run-scoped, beaten by run_id rotation), the marker attempt
    carried in org_config.phase_reroute (beaten by freezing/garbling it), and
    the event log (rotates with nothing, orchestrator-independent). Each
    source can only ever RAISE the floor, never weaken it."""
    reroute = (getattr(ctx, "org_config", None) or {}).get("phase_reroute") or {}
    try:
        prior_marker = max(0, int(reroute.get("attempt") or 0))
    except (TypeError, ValueError):
        prior_marker = 0
    prior_events = attempts_from_event_log(run_ctx, build_key(ctx))
    return max(len(ledger), prior_marker + 1, prior_events + 1)
