"""Common helpers shared by phase_5_implement and phase_6_review.

#261 Stage 0 — single-source-of-truth extraction (ship-id 2C61F0A0).

The 13 helpers + 2 constants below were previously duplicated in both phase
modules.  This file is the ONE canonical copy (= phase_5_implement version for
all 13).  Both phase modules import and re-export every name at module level so
that all three access patterns continue to work:

  * ``from phase_5_implement import X`` / ``from phase_6_review import X``
  * ``phase_5_implement.X`` / ``phase_6_review.X`` (attribute access)
  * ``patch("phase_6_review.X")`` / ``patch.object(phase_5_implement, "X")``
    (the re-exported name in the consumer module is the patch target for callers
    that use the bare-name reference inside that module's own functions)

Do NOT add helpers to this file that are not in the 13-list — those belong in
their respective phase module or a future Stage 1/2 package.
"""
from __future__ import annotations

import logging
import re
import subprocess
import sys
from pathlib import Path

# ── path bootstrap (mirrors phase_5_implement / phase_6_review) ──────────────
# Insert lib/ and lib/plugins/ so sibling imports (bounded_spawn, lib.git_port,
# telemetry_ctx, etc.) resolve when this module is loaded by either phase file.
sys.path.insert(0, str(Path(__file__).parent.parent / "lib" / "plugins"))
sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))

from contracts import StepResult  # noqa: E402
import telemetry_ctx  # noqa: E402
from bounded_spawn import bounded_run  # noqa: E402
from lib import git_port  # noqa: E402  164E4EFA — rc-aware git read adapter
from lib import git_write_port  # noqa: E402  5F06E98D — injectable git write-op seam
from verdict_parse import last_line_anchored_marker  # noqa: E402

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# 1. _git_op_with_lock_retry  (identical across p5/p6)
# ─────────────────────────────────────────────────────────────────────────────

def _git_op_with_lock_retry(cmd: list, *, cwd: str, timeout: int = 30):
    """Run git command, retry on .git/index.lock contention with backoff [1s, 2s]."""
    return git_write_port.git_op_with_lock_retry(cmd, cwd=cwd, timeout=timeout)


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
    }


# ─────────────────────────────────────────────────────────────────────────────
# 6. _maybe_emit_cross_tree_warning  (phase_5 version: if None guard DC1CB656)
# ─────────────────────────────────────────────────────────────────────────────

def _maybe_emit_cross_tree_warning(result: StepResult, worktree_root: Path) -> StepResult:
    """If the result is OK and helper detects cross-tree edits, emit telemetry,
    tag ``result.data``, and best-effort auto-revert via ``git checkout --``.

    Pure observability for the warning path; auto-revert is best-effort
    remediation. Does NOT change result.status.
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
    try:
        revert = _revert_cross_tree_modifications(Path(main_repo_root), list(files))
    except Exception as exc:  # noqa: BLE001
        _emit_safe(
            "cross_tree_revert_failed",
            {
                "step": result.step_name,
                "exception": exc.__class__.__name__,
                "files": list(files),
            },
        )
        return result
    _emit_safe(
        "cross_tree_edit_reverted",
        {
            "step": result.step_name,
            "main_repo_root": main_repo_root,
            "worktree_root": str(worktree_root),
            "files": list(files),
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
# 9. _read_first_block  (phase_5 version: "producer anti-fabrication" text)
# ─────────────────────────────────────────────────────────────────────────────

def _read_first_block(scratchpad: Path) -> str:
    inj = scratchpad / "injection"
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
# 11. _maybe_role_template  (identical across p5/p6)
# ─────────────────────────────────────────────────────────────────────────────

def _maybe_role_template(ctx) -> str:
    cfg = ctx.org_config or {}
    role_path = cfg.get("role_template_path")
    if not role_path:
        return ""
    rp = Path(role_path).expanduser()
    if not rp.is_file():
        return ""
    return rp.read_text(encoding="utf-8").rstrip() + "\n\n"


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
