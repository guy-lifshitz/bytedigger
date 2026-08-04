"""red_skeleton — provenance of RED-authored production stub files (GH1406).

Frozen spec: 2026-07-29_9CE8E906_gh1406_red_skeleton_provenance_spec.md

The GH961 dirty-tree guard kills phase 5 on ANY uncommitted in-allowlist
production path. That makes a STRONG RED (a skeleton the behavioural test
actually fails against) more expensive than a WEAK one (an `ImportError` that
leaves the tree clean) — the failure direction is inverted, not merely
false-positive. This module supplies the discriminator the guard lacks:
PROVENANCE. A path is RED-authored iff it was clean before `invoke_red_llm`
and dirty after, never by inspecting what the file contains (#1400: closure of
a set of VALUES does not hold under a predicate over the artifact's text).

Public API (spec §3.1):
    snapshot_dirty_paths(git_cwd, timeout=30) -> (list[(xy, path)], err|None)
    ADDABLE_XY / addable_paths(entries)       -> list[str]
    PROCESS_TOKEN / build_stamp(ctx, cycle)   -> dict
    persist_pre_red_dirty(scratchpad, paths, stamp) -> bool
    read_pre_red_dirty(scratchpad, expected_stamp)  -> list[str] | None
    red_authored_paths(pre_red_dirty, dirty_now_addable, allowlist,
                       red_test_paths) -> list[str]

Fail-open by module contract on git/OS errors (never raises); every CALLER is
fail-closed — an unavailable snapshot yields an empty RED-authored set, i.e.
today's guard behaviour byte for byte.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path
from typing import Any

from bytedigger_engine.lib import dirty_tree_guard, git_port

PRE_RED_DIRTY_RELPATH = "integrity/pre-red-dirty.json"

# §3.1a — a WHITELIST OF WHOLE CODES, never a predicate over characters. The
# per-character version (`frozenset("?AM")`) admitted `AA` (unmerged "both
# added"), reachable from a conflicted `git stash pop` where MERGE_HEAD is
# absent so E_GIT_BAD_STATE does not fire either — the engine would have
# committed a file full of conflict markers as a "skeleton".
ADDABLE_XY = frozenset({"??", " M", "M ", "MM", "A ", "AM"})

# §3.1b — a per-process nonce, computed ONCE at import. `invoke_red_llm` is a
# resume_sentinel step: on durable resume the engine returns the cache and the
# body never runs, so run_id/cycle/HEAD all still match and a stamp built from
# them alone would accept a snapshot this process never took. The token is the
# only component that cannot survive a new process.
PROCESS_TOKEN = uuid.uuid4().hex


def snapshot_dirty_paths(
    git_cwd: str, timeout: int = 30
) -> "tuple[list[tuple[str, str]], str | None]":
    """`git status --porcelain` -> (sorted (XY, path) pairs, err). Never raises.

    Keeps its OWN `git_port.git_read` call site: the sibling suite monkeypatches
    `dirty_tree_guard.git_port.git_read` at 18 points, and routing that call
    through a shared module would silently kill that seam (§1g invariant).
    """
    try:
        result = git_port.git_read(
            ["status", "--porcelain"], cwd=git_cwd, timeout=timeout
        )
    except Exception as exc:  # noqa: BLE001 — module contract: fail-open
        return [], f"snapshot_dirty_paths: git status failed: {exc}"

    if result.returncode != 0:
        return [], (
            f"snapshot_dirty_paths: git status failed "
            f"(rc={result.returncode}, stderr={result.stderr[:200]})"
        )
    return dirty_tree_guard.parse_porcelain_lines(result.stdout), None


def addable_paths(entries: "list[tuple[str, str]]") -> "list[str]":
    """Paths whose WHOLE XY code is in `ADDABLE_XY`.

    Everything else is rejected: D/AD/DD (deletion), R*/C* (rename/copy),
    U*/AA (conflict), T* (typechange), and any code not enumerated. A rejected
    path simply stays dirty, so the guard blocks — the safe direction (§3.1a).
    """
    return sorted({p for xy, p in (entries or []) if xy in ADDABLE_XY})


def build_stamp(ctx: Any, cycle: int) -> "dict[str, Any]":
    """THE single stamp builder — both chokepoints must call exactly this one.

    Two independently-built stamps would drift, the feature would die silently
    in production and stay green in tests (gate r2 MAJOR-B; AC25 pins it).
    """
    run_id = getattr(ctx, "run_id", None)
    if run_id is None:
        cfg = getattr(ctx, "org_config", None) or {}
        try:
            run_id = cfg.get("run_id")
        except AttributeError:
            run_id = None
    return {
        "process_token": PROCESS_TOKEN,
        "run_id": run_id,
        "cycle": int(cycle),
    }


def persist_pre_red_dirty(
    scratchpad: Any, paths: "list[str]", stamp: "dict[str, Any]"
) -> bool:
    """Write-through `{"paths": [...], "stamp": {...}}`. False on OSError."""
    try:
        target = Path(scratchpad) / PRE_RED_DIRTY_RELPATH
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps({"paths": list(paths or []), "stamp": dict(stamp or {})}),
            encoding="utf-8",
        )
    except OSError:
        return False
    return True


def read_pre_red_dirty(
    scratchpad: Any, expected_stamp: "dict[str, Any]"
) -> "list[str] | None":
    """The snapshot, or None when it is UNAVAILABLE.

    None ⇔ absent / unreadable / wrong shape / **stamp mismatch**. `[]` is a
    valid, permissive value ("clean before RED") and is never conflated with
    None — collapsing the two would destroy the whole discriminator.
    """
    try:
        raw = (Path(scratchpad) / PRE_RED_DIRTY_RELPATH).read_text(encoding="utf-8")
    except (OSError, ValueError):
        return None
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    paths = payload.get("paths")
    stamp = payload.get("stamp")
    if not isinstance(paths, list) or not isinstance(stamp, dict):
        return None
    expected = dict(expected_stamp or {})
    for key in ("process_token", "run_id", "cycle"):
        if stamp.get(key) != expected.get(key):
            return None
    return [str(p) for p in paths]


def red_authored_paths(
    pre_red_dirty: "list[str] | None",
    dirty_now_addable: "list[str]",
    allowlist: "list[str]",
    red_test_paths: "list[str]",
) -> "list[str]":
    """THE classifier. `[]` when the snapshot is unavailable (fail-closed)."""
    if pre_red_dirty is None:
        return []
    before = {dirty_tree_guard._normalize(p) for p in pre_red_dirty}
    allowed = {dirty_tree_guard._normalize(p) for p in (allowlist or [])}
    excluded = {dirty_tree_guard._normalize(p) for p in (red_test_paths or [])}

    authored: "set[str]" = set()
    for raw in dirty_now_addable or []:
        norm = dirty_tree_guard._normalize(raw)
        if not norm or norm in before or norm in excluded:
            continue
        if norm not in allowed:
            continue
        if dirty_tree_guard._is_test_segment(norm):
            continue
        authored.add(norm)
    return sorted(authored)
