"""Common helpers shared by phase_5_implement and phase_6_review.

#261 Stage 0 — single-source-of-truth extraction (ship-id 2C61F0A0).

The 13 helpers + 2 constants below were previously duplicated in both phase
modules.  This file is the ONE canonical copy (= phase_5_implement version for
all 13).  Both phase modules import and re-export every one of THOSE 13 names at
module level so that all three access patterns continue to work:

  * ``from phase_5_implement import X`` / ``from phase_6_review import X``
  * ``phase_5_implement.X`` / ``phase_6_review.X`` (attribute access)
  * ``patch("phase_6_review.X")`` / ``patch.object(phase_5_implement, "X")``
    (the re-exported name in the consumer module is the patch target for callers
    that use the bare-name reference inside that module's own functions)

Do NOT add helpers to this file that are not in the 13-list — those belong in
their respective phase module or a future Stage 1/2 package.  ONE exception,
section 8a (hal#1674): the injection-input contract lives here because §4.1
of that spec prescribes this module as its home and §1g requires a single owner
for it — the three gates that consume it span phase_45_spec
and phase_5_implement, so no one phase module can own it.  Section 8a's names
are NOT re-exported by any phase module and must not be: every consumer imports
`detect_injection_block` from here directly.
"""
from __future__ import annotations

import dataclasses
import logging
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

# ── path bootstrap (mirrors phase_5_implement / phase_6_review) ──────────────
# Insert lib/ and lib/plugins/ so sibling imports (bounded_spawn, lib.git_port,
# telemetry_ctx, etc.) resolve when this module is loaded by either phase file.

from bytedigger_engine.contracts import StepResult, WorkflowContext  # noqa: E402
from bytedigger_engine import telemetry_ctx  # noqa: E402
from bytedigger_engine.lib.bounded_spawn import bounded_run  # noqa: E402
from bytedigger_engine.lib import git_port  # noqa: E402  164E4EFA — rc-aware git read adapter
from bytedigger_engine.lib import git_write_port  # noqa: E402  5F06E98D — injectable git write-op seam
from bytedigger_engine.lib.verdict_parse import last_line_anchored_marker  # noqa: E402
from bytedigger_engine.config_provider import int_value  # noqa: E402  GH786 retry knob
from bytedigger_engine.role_template import load_role_template  # noqa: E402  bd#119
from bytedigger_engine.conformance.attest import InjectedBlock, hash_text  # noqa: E402  bd#141 4(d), bd#147

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# 1. _git_op_with_lock_retry  (identical across p5/p6)
# ─────────────────────────────────────────────────────────────────────────────

def _git_op_with_lock_retry(cmd: list, *, cwd: str, timeout: int = 30):
    """Run git command, retry on .git/index.lock contention with backoff [1s, 2s]."""
    return git_write_port.git_op_with_lock_retry(cmd, cwd=cwd, timeout=timeout)


# ─────────────────────────────────────────────────────────────────────────────
# 1b. _git_read / _git_write  (D52228C3 §2.9 — hoisted from phase_8/phase_6)
# ─────────────────────────────────────────────────────────────────────────────

def _git_read(args: list[str], cwd: Path | str, *, timeout: int = 30) -> tuple[int, str, str]:
    """READ-only git via the injectable git_read seam; returns a `(rc, stdout, stderr)` tuple.

    Behavior-preserving: same (rc, stdout, stderr) contract and the
    FileNotFoundError->127 / timeout->124 edge semantics. Reads route
    through git_port.git_read so an OSS host can swap the git backend.

    Single source (§1g) — phase_8_post_deploy re-exports this name as a
    settable module attribute; its call sites keep dispatching through that
    attribute so the pre-existing teardown monkeypatches stay hooked.
    """
    try:
        res = git_port.git_read(args, cwd=str(cwd), timeout=timeout)
    except FileNotFoundError:
        return 127, "", "git: command not found"
    if res.timed_out:
        return 124, "", f"git {' '.join(args)}: timeout after {timeout}s"
    return res.returncode, res.stdout, res.stderr


def _git_write(args: list[str], cwd: Path | str, *, timeout: int = 30) -> tuple[int, str, str]:
    """WRITE git via the injectable git_op_capture seam; returns a `(rc, stdout, stderr)` tuple.

    Returns the same (rc, stdout, stderr) contract and the same
    FileNotFoundError->127 / timeout->124 edge semantics. Writes route through
    git_write_port.git_op_capture so an OSS host can swap the git backend.

    Single source (§1g) — phase_8_post_deploy and phase_6_review both re-export
    this name as a settable module attribute (RED tests patch it).
    """
    try:
        res = git_write_port.git_op_capture(["git", *args], cwd=str(cwd), timeout=timeout)
    except FileNotFoundError:
        return 127, "", "git: command not found"
    if res.timed_out:
        return 124, "", f"git {' '.join(args)}: timeout after {timeout}s"
    return res.returncode, res.stdout, res.stderr


# ─────────────────────────────────────────────────────────────────────────────
# 2. _emit_safe  (phase_5 SUPERSET — adds severity kwarg, 1E8EF652)
# ─────────────────────────────────────────────────────────────────────────────

def _emit_safe(event_type: str, payload: dict, severity: str = "warning") -> None:
    """Emit a telemetry event, falling back to a logger line if event_log fails.

    Agreement 1E8EF652 — `severity` controls the fallback log level when
    `event_log.append` raises:
      - "warning" (default): logger.warning(...) — preserves prior behavior
        for general-purpose events.
      - "error":             logger.error(...) — for ALERT-class events
        (e.g. `green_token_budget_alert`, `green_watchdog_tokens_unknown`)
        where the underlying signal already indicates a degraded run; a
        broken telemetry channel on top of that warrants higher severity.
    Unrecognized values fall back to warning so a typo never crashes the run.
    """
    run_ctx = telemetry_ctx.get_current_run()
    if run_ctx is None or run_ctx.event_log is None:
        return
    try:
        run_ctx.event_log.append(event_type, payload, run_ctx.run_id)
    except Exception as e:  # noqa: BLE001
        log_fn = logger.error if severity == "error" else logger.warning
        log_fn("telemetry append failed for %s: %s", event_type, e)


# ─────────────────────────────────────────────────────────────────────────────
# 3. _resolve_scratchpad  (phase_5 version: ctx.org_config direct)
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_scratchpad(ctx) -> Path:
    cfg = getattr(ctx, "org_config", None) or {}
    raw = cfg.get("scratchpad_dir")
    if not raw:
        raise ValueError("org_config.scratchpad_dir required for phase_5_implement")
    return Path(raw).expanduser().resolve()


# ─────────────────────────────────────────────────────────────────────────────
# 4. _verify_no_cross_tree_edits  (phase_5 version: git_port.git_read)
# ─────────────────────────────────────────────────────────────────────────────

def _verify_no_cross_tree_edits(worktree_root: Path) -> dict:
    """Detect modifications that landed in the MAIN checkout while the build is
    running inside a secondary worktree (the F3 cross-tree leak).

    Returns dict with keys:
      - ``cross_tree_detected`` (bool)
      - ``main_repo_root`` (str | None)
      - ``modified_files`` (list[str])  — paths relative to ``main_repo_root``

    Pure observability: never auto-reverts, never raises. ``main_repo_root`` is
    derived from ``git worktree list --porcelain`` (first ``worktree`` entry is
    canonical main). When ``worktree_root`` IS the main repo, the function
    returns early with no detection — single-worktree builds can't cross-tree.
    """
    empty: dict[str, object] = {"cross_tree_detected": False, "main_repo_root": None, "modified_files": []}
    try:
        proc = git_port.git_read(
            ["worktree", "list", "--porcelain"],
            cwd=str(worktree_root),
            timeout=10,
        )
    except (FileNotFoundError, OSError):
        return empty
    if proc.returncode == 124:
        return empty
    if proc.returncode != 0:
        return empty
    main_root: Path | None = None
    for line in proc.stdout.splitlines():
        if line.startswith("worktree "):
            main_root = Path(line[len("worktree "):]).resolve()
            break
    if main_root is None:
        return empty
    try:
        worktree_resolved = worktree_root.resolve()
    except OSError:
        worktree_resolved = worktree_root
    # No-op when worktree_root IS the main repo OR a subpath of it (e.g. cwd
    # falls inside the same checkout). Without this, running git status against
    # the parent repo would flag legitimate uncommitted edits and — with
    # auto-revert active — destroy them.
    try:
        if main_root == worktree_resolved or worktree_resolved.is_relative_to(main_root):
            return {"cross_tree_detected": False, "main_repo_root": str(main_root), "modified_files": []}
    except ValueError:
        # is_relative_to raised on incompatible paths — fall through to git status.
        pass
    try:
        st = git_port.git_read(
            ["status", "--porcelain"],
            cwd=str(main_root),
            timeout=10,
        )
    except (FileNotFoundError, OSError):
        return {"cross_tree_detected": False, "main_repo_root": str(main_root), "modified_files": []}
    if st.returncode == 124:
        return {"cross_tree_detected": False, "main_repo_root": str(main_root), "modified_files": []}
    if st.returncode != 0:
        return {"cross_tree_detected": False, "main_repo_root": str(main_root), "modified_files": []}
    modified: list[str] = []
    for raw_line in st.stdout.splitlines():
        if not raw_line:
            continue
        # Porcelain format: 'XY <path>' where X+Y are status codes (2 chars).
        status = raw_line[:2]
        path = raw_line[3:].strip() if len(raw_line) > 3 else ""
        if not path:
            continue
        # Skip untracked-only entries to avoid noise; F3 leak is tracked-file pollution.
        if status == "??":
            continue
        modified.append(path)
    return {
        "cross_tree_detected": bool(modified),
        "main_repo_root": str(main_root),
        "modified_files": modified,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 4b. _filter_cross_tree_to_worker_manifest  (GH1562 — bound the revert to
#     this build's OWN worker manifest, never subtract from the unbounded
#     dirty tree; C8E48307 §2.2)
# ─────────────────────────────────────────────────────────────────────────────

def _filter_cross_tree_to_worker_manifest(
    main_repo_root: Path, files: list[str], result: "StepResult"
) -> "tuple[list[str], list[str], str]":
    """Partition repo-relative <files> into (owned, refused, manifest_source).

    owned   — files the worker manifest proves THIS build wrote inside main_repo_root
    refused — everything else (another process's uncommitted work)
    Fail-closed: an absent/malformed/empty manifest refuses EVERY file.
    Never raises.
    """
    # N4: function-level import — phase_workflows_common is a core module
    # imported standalone by several tests; do not widen its module-level
    # import graph.
    from bytedigger_engine.llm_subprocess import manifest_from_result, _ManifestError  # noqa: PLC0415

    def _refused_reason(exc: Exception) -> str:
        name = exc.__class__.__name__
        if name == "_ManifestMalformedError":
            return "unavailable:malformed"
        if name == "_ManifestInvalidSourceError":
            return "unavailable:bad_source"
        return "unavailable:missing"

    try:
        manifest, manifest_source = manifest_from_result(result)
    except _ManifestError as exc:  # missing | malformed | bad_source
        return [], list(files), _refused_reason(exc)
    except Exception:  # noqa: BLE001 — outer belt: wrapper contract at :282 never raises
        return [], list(files), "unavailable:unknown"

    resolved_entries: list[str] = []
    for entry in manifest:
        try:
            if os.path.isabs(entry):
                resolved_entries.append(os.path.realpath(entry))
            else:
                resolved_entries.append(os.path.realpath(os.path.join(str(main_repo_root), entry)))
        except (OSError, ValueError, TypeError):
            # Defensive belt (§2.2 D4): a non-str entry never reaches here (the
            # whole manifest is rejected first by _validate_manifest_or_raise);
            # kept for a well-formed str entry with an embedded NUL, which
            # makes os.path.realpath raise ValueError.
            continue

    owned: list[str] = []
    refused: list[str] = []
    for p in files:
        try:
            resolved_p = os.path.realpath(os.path.join(str(main_repo_root), p))
        except (OSError, ValueError, TypeError):
            refused.append(p)
            continue
        if resolved_p in resolved_entries:
            owned.append(p)
        else:
            refused.append(p)

    return owned, refused, manifest_source


# ─────────────────────────────────────────────────────────────────────────────
# 5. _revert_cross_tree_modifications  (phase_5 version: error="timeout")
# ─────────────────────────────────────────────────────────────────────────────

def _revert_cross_tree_modifications(main_repo_root: Path, files: list[str]) -> dict:
    """Best-effort restore of tracked files in the MAIN checkout via
    ``git -C <main_repo_root> checkout -- <file>`` per file.

    Behavior:
      - Per file: returncode==0 → reverted=True, error=None.
      - Otherwise reverted=False, error=stderr (or stringified exception).
      - Each call goes through the ``git_write_port`` seam; on timeout it
        returns a result with ``returncode=124`` and the file is recorded
        with ``error="timeout"``.  ``FileNotFoundError``/``OSError`` are
        caught; the wrapper itself NEVER raises.

    Returns dict::

        {
          "revert_attempted": True,
          "results": [{"file": str, "reverted": bool, "error": str|None}, ...],
          "reverted_count": int,
          "failed_count": int,
          "refused_count": int,  # GH1562 — always 0 here; manifest filtering
          "refused_files": [],   # happens only at the wrapper level (§2.4)
        }
    """
    results: list[dict] = []
    reverted_count = 0
    failed_count = 0
    port = git_write_port.get_git_write()
    for file in files:
        try:
            res = port.op_capture(
                ["git", "-C", str(main_repo_root), "checkout", "--", file],
                cwd=str(main_repo_root),
                timeout=10,
            )
            if res.returncode == 124:
                results.append({"file": file, "reverted": False, "error": "timeout"})
                failed_count += 1
            elif res.returncode == 0:
                results.append({"file": file, "reverted": True, "error": None})
                reverted_count += 1
            else:
                results.append({
                    "file": file,
                    "reverted": False,
                    "error": (res.stderr or "").strip() or None,
                })
                failed_count += 1
        except (FileNotFoundError, OSError) as exc:
            results.append({"file": file, "reverted": False, "error": str(exc)})
            failed_count += 1
    return {
        "revert_attempted": True,
        "results": results,
        "reverted_count": reverted_count,
        "failed_count": failed_count,
        "refused_count": 0,
        "refused_files": [],
    }


# ─────────────────────────────────────────────────────────────────────────────
# 6. _maybe_emit_cross_tree_warning  (phase_5 version: if None guard DC1CB656)
# ─────────────────────────────────────────────────────────────────────────────

def _hash_worktree_blobs(root: str, paths: list[str]) -> "dict[str, str | None]":
    """One read-only ``git hash-object -- <paths>`` (no ``-w``) in <root>.

    Returns {path: blob_sha}; every value is None on any failure (the caller
    still treats the path as dirty).  Never raises.
    """
    unknown: dict[str, str | None] = {p: None for p in paths}
    if not paths:
        return unknown
    try:
        proc = git_port.git_read(["hash-object", "--", *paths], cwd=root, timeout=10)
        if proc.returncode != 0:
            return unknown
        shas = proc.stdout.split()
        if len(shas) != len(paths):
            return unknown
        return dict(zip(paths, shas))
    except Exception:  # noqa: BLE001 — helper contract: never raises
        return unknown


def _snapshot_main_checkout_state(worktree_root: Path) -> dict:
    """bd#170: record which tracked paths of the MAIN checkout are already
    dirty BEFORE a worker runs, so the cross-tree auto-revert never resets a
    user's pre-existing edit.  Read-only (git_port.git_read); never raises.

    Returns ``{"ok": True, "main_repo_root": str, "dirty": {relpath: blob|None}}``
    or ``{"ok": False, "reason": str}``.
    """
    try:
        proc = git_port.git_read(["worktree", "list", "--porcelain"], cwd=str(worktree_root), timeout=10)
        if proc.returncode != 0:
            return {"ok": False, "reason": f"worktree_list_rc_{proc.returncode}"}
        main_root: Path | None = None
        for line in proc.stdout.splitlines():
            if line.startswith("worktree "):
                main_root = Path(line[len("worktree "):]).resolve()
                break
        if main_root is None:
            return {"ok": False, "reason": "main_root_not_found"}
        wt_resolved = Path(worktree_root).resolve()
        if main_root == wt_resolved or wt_resolved.is_relative_to(main_root):
            return {"ok": True, "main_repo_root": str(main_root), "dirty": {}}
        st = git_port.git_read(["status", "--porcelain"], cwd=str(main_root), timeout=10)
        if st.returncode != 0:
            return {"ok": False, "reason": f"status_rc_{st.returncode}"}
        paths: list[str] = []
        for raw_line in st.stdout.splitlines():
            path = raw_line[3:].strip() if len(raw_line) > 3 else ""
            if path and raw_line[:2] != "??":
                paths.append(path)
        return {
            "ok": True,
            "main_repo_root": str(main_root),
            "dirty": _hash_worktree_blobs(str(main_root), paths),
        }
    except Exception as exc:  # noqa: BLE001 — helper contract: never raises
        return {"ok": False, "reason": f"{exc.__class__.__name__}: {exc}"}


def _pre_state_usable(pre_state: object, main_repo_root: object) -> bool:
    """True iff pre_state is a well-formed snapshot of THIS main checkout."""
    try:
        return bool(
            isinstance(pre_state, dict)
            and pre_state.get("ok") is True
            and isinstance(pre_state.get("dirty"), dict)
            and os.path.realpath(str(pre_state.get("main_repo_root")))
            == os.path.realpath(str(main_repo_root))
        )
    except Exception:  # noqa: BLE001
        return False


def _maybe_emit_cross_tree_warning(
    result: StepResult, worktree_root: Path, pre_state: "dict | None" = None,
) -> StepResult:
    """If the result is OK and helper detects cross-tree edits, emit telemetry,
    tag ``result.data``, and best-effort auto-revert via ``git checkout --``.

    Pure observability for the warning path; auto-revert is best-effort
    remediation. Does NOT change result.status.

    bd#170: ``pre_state`` is the ``_snapshot_main_checkout_state`` taken before
    the worker ran.  An owned path that was already dirty then (or any owned
    path when no usable snapshot exists) is held back, never reset.
    """
    if result.status != "ok" or not isinstance(result.data, dict):
        return result
    findings = _verify_no_cross_tree_edits(worktree_root)
    if not findings.get("cross_tree_detected"):
        return result
    files = findings.get("modified_files", [])
    main_repo_root = findings.get("main_repo_root")
    _emit_safe(
        "cross_tree_edit_detected",
        {
            "step": result.step_name,
            "main_repo_root": main_repo_root,
            "worktree_root": str(worktree_root),
            "modified_files": files,
        },
    )
    result.data["cross_tree_warning"] = True
    result.data["cross_tree_files"] = list(files)
    result.metadata["cross_tree_warning"] = True
    result.metadata["cross_tree_files"] = list(files)
    # Best-effort auto-revert. Wrapper never raises.
    if main_repo_root is None:  # DC1CB656: type-safety (boy-scout)
        return result
    # GH1562 (C8E48307): bound the revert to this build's OWN worker
    # manifest — never subtract from the unbounded dirty tree. Detection
    # observability above stays unfiltered; only the mutation narrows.
    owned, refused, manifest_source = _filter_cross_tree_to_worker_manifest(
        Path(main_repo_root), list(files), result,
    )
    if refused:
        result.data["cross_tree_refused_files"] = list(refused)
        result.metadata["cross_tree_refused_files"] = list(refused)
        _emit_safe(
            "cross_tree_revert_refused",
            {
                "step": result.step_name,
                "main_repo_root": main_repo_root,
                "refused_files": list(refused),
                "refused_count": len(refused),
                "manifest_source": manifest_source,
            },
            severity="warning",
        )
    held: list[str] = []
    if _pre_state_usable(pre_state, main_repo_root):
        assert isinstance(pre_state, dict)
        start_dirty = pre_state["dirty"]
        held = [p for p in owned if p in start_dirty]
        hold_reason = "dirty_at_start"
    else:
        start_dirty = {}
        held = list(owned)
        hold_reason = "pre_state_unavailable"
    if held:
        now_blobs = _hash_worktree_blobs(str(main_repo_root), held)
        changed: dict[str, bool | None] = {}
        for p in held:
            before = start_dirty.get(p) if hold_reason == "dirty_at_start" else None
            after = now_blobs.get(p)
            changed[p] = None if before is None or after is None else (before != after)
        result.data["cross_tree_prestate_refused_files"] = list(held)
        result.metadata["cross_tree_prestate_refused_files"] = list(held)
        _emit_safe(
            "cross_tree_revert_prestate_refused",
            {
                "step": result.step_name,
                "main_repo_root": main_repo_root,
                "files": list(held),
                "reason": hold_reason,
                "changed_since_start": changed,
            },
            severity="warning",
        )
        owned = [p for p in owned if p not in held]
    if not owned:
        return result
    try:
        revert = _revert_cross_tree_modifications(Path(main_repo_root), owned)
    except Exception as exc:  # noqa: BLE001
        _emit_safe(
            "cross_tree_revert_failed",
            {
                "step": result.step_name,
                "exception": exc.__class__.__name__,
                "files": list(owned),
            },
        )
        return result
    _emit_safe(
        "cross_tree_edit_reverted",
        {
            "step": result.step_name,
            "main_repo_root": main_repo_root,
            "worktree_root": str(worktree_root),
            "files": list(owned),
            "reverted_count": revert.get("reverted_count", 0),
            "failed_count": revert.get("failed_count", 0),
        },
    )
    result.data["cross_tree_revert"] = revert
    result.metadata["cross_tree_revert"] = revert
    return result


# ─────────────────────────────────────────────────────────────────────────────
# 7. _CROSS_TREE_PROMPT_TEMPLATE  (byte-identical across p5/p6)
# ─────────────────────────────────────────────────────────────────────────────

_CROSS_TREE_PROMPT_TEMPLATE = (
    "WORKTREE EDIT BOUNDARY:\n"
    "Your build root is: {worktree_root}\n"
    "Resolve `worktree_root` from build-state.yaml or your CWD. ALL file edits "
    "(Edit/Write tools) MUST use:\n"
    "  (a) relative paths from your CWD, OR\n"
    "  (b) absolute paths under the worktree root (e.g. {worktree_root}/SYSTEM/foo.py).\n"
    "NEVER edit files via `/home/user/<anything>/repo/...` paths directly — those resolve\n"
    "to the MAIN checkout, NOT your worktree, and silently corrupt the parent repo.\n"
    "If a task description gives you an absolute path, sanity-check it: it must start with\n"
    "your worktree root. If it doesn't, REWRITE it relative to your CWD before editing."
)


# ─────────────────────────────────────────────────────────────────────────────
# 8. _worktree_edit_boundary_block  (identical across p5/p6)
# ─────────────────────────────────────────────────────────────────────────────

def _worktree_edit_boundary_block(worktree_root: Path) -> str:
    return _CROSS_TREE_PROMPT_TEMPLATE.format(worktree_root=str(worktree_root))


# ─────────────────────────────────────────────────────────────────────────────
# 8a. injection inputs  (hal#1674 §4.1 — ONE place that knows where the
#     READ_FIRST files live, what "present" means for them, and how to tell a
#     DECLARED infrastructure block from a reviewer quoting the prompt)
# ─────────────────────────────────────────────────────────────────────────────

# The five files _read_first_block below points every READ_FIRST worker at.
# (phase_05_inject.INJECTION_FILES is the PRODUCER's own list — extensionless
# and six long; these are the names this prompt actually promises.)
READ_FIRST_INJECTION_FILES: tuple[str, ...] = (
    "hal-memory.md",
    "constitution.md",
    "quality-gate.md",
    "producer-rules.md",
    "active-work.md",
)

# A worker DECLARES a block by opening a LINE with STATUS=block — that is what
# the prompt prose below asks it for. A substantive answer that QUOTES the same
# sentence mid-line is not a declaration, and the difference is exactly the one
# between an infrastructure failure and reviewer disagreement (hal#1674 §1).
_DECLARED_BLOCK_RE = re.compile(r"^STATUS=block", re.MULTILINE)


def injection_dir(scratchpad: Path) -> Path:
    """The injection directory phase_05_inject writes and READ_FIRST consumes."""
    return Path(scratchpad) / "injection"


def require_injection_files(scratchpad: Path) -> str | None:
    """None when every READ_FIRST file is present and non-empty, else a message
    naming the absolute directory (when it is missing) or the offending file.

    Empty means empty AFTER strip(): a whitespace-only file has st_size > 0, so
    a size check waves it through while the worker still has nothing to read.
    """
    inj = injection_dir(scratchpad)
    if not inj.is_dir():
        return f"injection files missing: expected directory {inj} does not exist"
    for name in READ_FIRST_INJECTION_FILES:
        path = inj / name
        try:
            body = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            # UnicodeDecodeError is a ValueError, NOT an OSError: a file in some
            # other encoding is just as unreadable to the worker as one the OS
            # refuses, and must not crash a hard gate with a traceback.
            return f"injection files missing: {path} could not be read"
        if not body.strip():
            return f"injection files missing: {path} is empty"
    return None


def detect_injection_block(
    scratchpad_raw: str | Path | None, raw_output: str | None
) -> str | None:
    """CONJUNCTIVE classifier for a gate: the worker DECLARED a block AND the
    inputs really are absent. Returns the reason message, or None.

    Either half alone misclassifies: the block sentence is verbatim prompt
    prose, so a reviewer may quote it while genuinely disagreeing, and
    phase_8_post_deploy removes `injection/` as routine housekeeping, so its
    absence is normal after a ship. Without a scratchpad the disk half cannot
    be evaluated at all — the caller keeps its verdict axis unchanged.
    """
    if not scratchpad_raw:
        return None
    if not raw_output or not _DECLARED_BLOCK_RE.search(raw_output):
        return None
    return require_injection_files(Path(scratchpad_raw))


# ─────────────────────────────────────────────────────────────────────────────
# 9. _read_first_block  (phase_5 version: "producer anti-fabrication" text)
# ─────────────────────────────────────────────────────────────────────────────

def _read_first_block(scratchpad: Path) -> str:
    inj = injection_dir(scratchpad)
    return (
        "READ_FIRST — read these five files before proceeding:\n"
        f"- {inj}/hal-memory.md      (learnings)\n"
        f"- {inj}/constitution.md    (project rules)\n"
        f"- {inj}/quality-gate.md    (zero-cornercutting policy)\n"
        f"- {inj}/producer-rules.md  (producer anti-fabrication)\n"
        f"- {inj}/active-work.md     (current project focus)\n"
        "If any file is missing or empty: orchestrator Phase 0.5 failed — "
        "STATUS=block with SUMMARY 'injection files missing'."
    )


# ─────────────────────────────────────────────────────────────────────────────
# 10. _resolve_model  (25e75663: renamed from _resolve_command; returns str)
# ─────────────────────────────────────────────────────────────────────────────

def _resolve_model(cfg: dict, override_key: str, default: str | None = None) -> str:
    """Per-step override → global model → per-step default.

    25e75663: renamed from _resolve_command; now returns a model string
    instead of an argv list. Config keys renamed *_llm_command → *_model.
    Lets harness pin different models per step (e.g. Opus for validation,
    Sonnet for GREEN, Haiku for SIMPLE RED) without coupling them through
    a single global. Locking the validator model at config time prevents
    silent downgrade of the hard gate (``never_skip_opus_validation_gate``).
    """
    return cfg.get(override_key) or cfg.get("model") or default or ""


# Back-compat alias so any external code using _resolve_command still resolves.
# Internal callers (phase_5_implement, phase_6_review) use _resolve_model directly.
_resolve_command = _resolve_model  # type: ignore[assignment]


# ─────────────────────────────────────────────────────────────────────────────
# 11. _maybe_role_template  (bd#119: thin wrapper; reader lives in role_template.py)
# ─────────────────────────────────────────────────────────────────────────────

def _role_template(ctx):
    """bd#141 4(d): the attributable reader (RoleTemplate | None); one read per build."""
    return load_role_template(ctx.org_config)


def _role_template_record(rt) -> "dict | None":
    """bd#141 4(d): the `data["role_template"]` shape (same as phase_2's)."""
    if rt is None:
        return None
    return {"source_id": rt.source_id, "content": rt.content}


def _declared_injections(data) -> "tuple[InjectedBlock, ...]":
    """bd#141 4(d) (R3.2): declare the role-template block a builder stored in its
    data dict. Pass the DICT that carried the prompt, never the StepResult."""
    block = data.get("role_template") if isinstance(data, dict) else None
    out: "tuple[InjectedBlock, ...]" = ()
    if block:
        out = (InjectedBlock(source_id=block["source_id"], content=block["content"]),)
    if not isinstance(data, dict):
        return out
    # bd#147: bound-record blocks, only alongside the prompt they were built with.
    record = data.get("injected_blocks")
    if not record or not isinstance(record, dict):
        return out
    blocks = record.get("blocks")
    if not isinstance(blocks, list):
        return out
    prompt = data.get("prompt")
    if prompt is None:
        prompt = ""
    if not isinstance(prompt, str) or record.get("prompt_sha256") != hash_text(prompt):
        return out
    extra = tuple(
        InjectedBlock(el.get("source_id"), el.get("content")) if isinstance(el, dict)
        else InjectedBlock(None, None)  # type: ignore[arg-type]
        for el in blocks
    )
    return out + extra  # type: ignore[arg-type]


def _injected_blocks_record(prompt: str, blocks: "list[dict]") -> "dict | None":
    """bd#147: `data["injected_blocks"]` shape, bound to the final prompt's hash."""
    if not blocks:
        return None
    return {
        "prompt_sha256": hash_text(prompt),
        "blocks": [{"source_id": b["source_id"], "content": b["content"]} for b in blocks],
    }


def _maybe_role_template(ctx) -> str:
    rt = _role_template(ctx)
    return rt.content if rt else ""


# ─────────────────────────────────────────────────────────────────────────────
# 12. _last_marker_wins  (identical across p5/p6)
# ─────────────────────────────────────────────────────────────────────────────

def _last_marker_wins(raw: str, markers: list[tuple[str, str]], fallback: str) -> str:
    """Return value of last line-anchored marker (case-insensitive).

    Delegates to lib/verdict_parse.py P2 (EEFD480F chokepoint).
    """
    return last_line_anchored_marker(raw, markers, fallback)


# ─────────────────────────────────────────────────────────────────────────────
# 13. _ENGINE_MODE_RE  (byte-identical across p5/p6)
# ─────────────────────────────────────────────────────────────────────────────

_ENGINE_MODE_RE = re.compile(r"^<!-- engine-mode: ([a-z_]+) -->$")


# ─────────────────────────────────────────────────────────────────────────────
# 14. _read_engine_mode  (phase_5 version: with docstring)
# ─────────────────────────────────────────────────────────────────────────────

def _read_engine_mode(spec_path: str) -> str | None:
    """Read the engine-mode marker from the first two lines of a spec file.

    Returns the mode string (e.g. "test_only") if a valid marker is found on
    line 1 or 2, or None otherwise (file unreadable, no marker, line 3+).
    """
    try:
        text = Path(spec_path).read_text(encoding="utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return None
    for line in text.splitlines()[:2]:
        m = _ENGINE_MODE_RE.match(line)
        if m:
            return m.group(1)
    return None


# ─────────────────────────────────────────────────────────────────────────────
# GH268 — test-only mode resolver (shared resolver, §1g)
# ─────────────────────────────────────────────────────────────────────────────

def resolve_engine_mode(spec_path: str | None, ctx: WorkflowContext) -> str | None:
    r"""Single source of truth for the effective engine mode (GH268, §1g;
    narrowed by GH1245). The mode is a DECLARED field: the explicit spec
    marker `<!-- engine-mode: <mode> -->` on line 1 or 2, and nothing else.

    GH1245: the former task-prose autodetect (`detect_test_only_intent`) is
    removed. Its first alternative `\btests?[- ]only\b` matches any correct
    description of TDD RED discipline, so ordinary TDD builds classified
    themselves as test-only, skipped the RED commit, and died in
    commit_green_code with E_MISSING_RED_BOUNDARY (incident
    forge-1785057041-6079411c, `source:"task_intent"`).
    `ctx` is retained in the signature: every call site passes it positionally.
    """
    if isinstance(spec_path, str) and spec_path:
        mode = _read_engine_mode(spec_path)
        if mode is not None:
            return mode
    return None


# ─────────────────────────────────────────────────────────────────────────────
# 15. _filter_gitignored_paths  (phase_5 version: git_port.git_read)
# ─────────────────────────────────────────────────────────────────────────────

def _filter_gitignored_paths(paths: list[str], git_cwd: str) -> list[str]:
    """Return paths not gitignored in git_cwd.  Degraded-but-OK on check-ignore
    failure (returns all paths).  Emits commit_gitignored_paths_skipped when
    any path is filtered."""
    if not paths:
        return paths
    ci = git_port.git_read(
        ["check-ignore", "--"] + paths,
        cwd=git_cwd,
        timeout=30,
    )
    if ci.returncode not in (0, 1):
        logger.warning(
            "git check-ignore failed (rc=%d, stderr=%s); proceeding with all paths",
            ci.returncode, ci.stderr[:200],
        )
        return list(paths)
    ignored: set[str] = {ln for ln in ci.stdout.splitlines() if ln.strip()}
    if ignored:
        _emit_safe(
            "commit_gitignored_paths_skipped",
            {"paths": sorted(ignored), "step": "", "phase": 0},
        )
    return [p for p in paths if p not in ignored]


def _filter_phantom_deleted_paths(paths: list[str], git_cwd: str) -> list[str]:
    """Return paths `git add` can stage: present on disk OR tracked by git.
    Drops 'phantom' paths — absent from disk AND untracked (e.g. a deletion
    already committed) — which make `git add -- <p>` fail atomically with
    'pathspec did not match any files'.  Degraded-but-OK on ls-files failure
    (returns all paths).  Emits commit_phantom_paths_skipped when any path
    is filtered.  GH514(2)."""
    if not paths:
        return paths
    missing = [
        p for p in paths
        if not (Path(p) if Path(p).is_absolute() else Path(git_cwd) / p).exists()
    ]
    if not missing:
        return list(paths)
    lf = git_port.git_read(["ls-files", "--"] + missing, cwd=git_cwd, timeout=30)
    if lf.returncode != 0:
        logger.warning(
            "git ls-files failed (rc=%d, stderr=%s); proceeding with all paths",
            lf.returncode, lf.stderr[:200],
        )
        return list(paths)
    tracked = {ln for ln in lf.stdout.splitlines() if ln.strip()}
    phantom = {p for p in missing if p not in tracked}
    if phantom:
        _emit_safe(
            "commit_phantom_paths_skipped",
            {"paths": sorted(phantom), "step": "", "phase": 0},
        )
    return [p for p in paths if p not in phantom]


# ─────────────────────────────────────────────────────────────────────────────
# 16. _paths_have_staged_changes (phase_5 origin: 9EDB7588; centralized by 3F5599A6)
# ─────────────────────────────────────────────────────────────────────────────

def _paths_have_staged_changes(git_cwd: str, paths: list[str], timeout: int = 30) -> bool:
    """True if `git diff --cached --quiet -- <paths>` reports staged changes
    (rc==1); False if none (rc==0). Any other rc OR OSError/SubprocessError →
    True (fail-toward-commit: never silently skip the RED commit on an ambiguous
    git state — keep the legacy commit path + its E_GIT_* handlers reachable).
    9EDB7588."""
    if not paths:
        return False
    try:
        r = git_port.git_read(
            ["diff", "--cached", "--quiet", "--", *paths],
            cwd=git_cwd,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return True
    if r.returncode == 0:
        return False
    if r.returncode == 1:
        return True
    return True  # ambiguous rc → fail-toward-commit


def resolve_integrity_verdict_retries(cfg: dict[str, Any] | None) -> int:
    """GH786: bounded same-prompt re-roll count for the integrity gates.

    Shared by phase_5_integrity and phase_6_fix_integrity (bd#84). Resolution
    order: env HAL_INTEGRITY_VERDICT_RETRY_MAX > cfg["integrity_verdict_retry_max"]
    > default 1. Routed through the config seam (int_value) so the OSS core
    stays host-decoupled. Result clamped >= 0; env "0" disables retry.
    Non-int env propagates ValueError per the seam's int_value contract.
    """
    cfg = cfg or {}
    default = 1
    raw = cfg.get("integrity_verdict_retry_max")
    if raw is not None:
        try:
            default = max(0, int(raw))
        except (TypeError, ValueError):
            default = 1
    return max(0, int_value("HAL_INTEGRITY_VERDICT_RETRY_MAX", default))


def reroll_until_verdict(
    attempt: Callable[[], StepResult],
    has_verdict: Callable[[str], bool],
    cfg: dict[str, Any] | None,
) -> StepResult:
    """GH786 / bd#84: a missing verdict marker is re-rolled, not terminal.

    Calls ``attempt`` (which must re-send the IDENTICAL prompt — a pure
    re-roll, GH705) until an ok reply's ``raw_response`` satisfies
    ``has_verdict`` or the ``resolve_integrity_verdict_retries(cfg)`` budget
    is spent. A non-ok result
    (subprocess/timeout/error) is a different failure class and ends the
    loop. The returned result carries ``verdict_completeness_retries`` and,
    once a reply was re-rolled, ``discarded_raw_responses`` (the replies
    without a verdict, oldest first) so their analysis is not lost; each
    re-roll also emits ``integrity_verdict_reroll``.
    """
    max_retries = resolve_integrity_verdict_retries(cfg)
    discarded: list[str] = []
    result = attempt()
    while result.status == "ok":
        raw = (result.data or {}).get("raw_response") or ""
        if has_verdict(raw) or len(discarded) >= max_retries:
            break
        discarded.append(raw)
        _emit_safe("integrity_verdict_reroll", {
            "step": result.step_name,
            "attempt": len(discarded),
            "discarded_excerpt": raw[-300:],
        })
        result = attempt()
    if result.status != "ok" and not discarded:
        return result
    data = {**(result.data or {}), "verdict_completeness_retries": len(discarded)}
    if discarded:
        data["discarded_raw_responses"] = discarded
    return dataclasses.replace(result, data=data)
