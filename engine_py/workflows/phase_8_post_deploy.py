"""Phase 8 (cleanup + close) as a WorkflowDefinition.

Stage 2.8 port (2026-04-25). Seventh phase ported, but FIRST one without
LLM calls — phase-8 is "always Haiku — mechanical" in the source spec, so
the engine port collapses the bash-driven Haiku into deterministic Python
steps. Precedent: phase_0_research and phase_05_inject are deterministic-only.

**Scope of this v0 port — deterministic mechanical cleanup.**
- Worktree cleanup (skip current; remove only merged)
- Gone-branch pruning
- Scratchpad subdir cleanup (preserve `post-deploy/` so report survives)
- Eval rotation (keep last 10 by mtime)
- Cleanup report writer

phase-8-post-deploy.md describes additional steps DEFERRED in v0:

1. **build-state.yaml deletion + session deregistration.** Stage 2.11
   retires `state-writer.ts` + `build-state.yaml` entirely, at which point
   these steps become no-ops. Including them now would couple the engine
   to a YAML file we are removing.
2. **`forge/emit post-deploy` shell event.** Engine event log already
   captures `workflow_finished` which is the canonical audit trail.
3. **`gh pr view` network check** when deciding whether a worktree branch
   is merged. v0 uses `git branch --merged main` only — local check, no
   network. Avoids a network failure mode in the cleanup path.
4. **MagicMock artifact removal** (`find . -name '"<MagicMock*'`). PPBA-
   specific concern; not portable into the engine yet.

Step contract:
    Each cleanup step returns ``status="ok"`` even when a sub-action fails
    (best-effort cleanup must not block the build close). Failures surface
    in the per-step ``data`` dict (e.g. ``errors: [{"path": ..., "reason": ...}]``)
    and propagate to the final cleanup report. The only HARD GATE is the
    report writer — if it cannot write to disk, the workflow errors out
    with ``E_CLEANUP_REPORT_WRITE_FAILED``.

Token-spend guards: N/A — this workflow makes zero LLM calls.

Inputs (via ``ctx.org_config``):
    scratchpad_dir         — REQUIRED. Absolute path to scratchpad root.
    working_dir            — Optional. Defaults to current working dir.
                             All git operations run with this CWD.
    current_worktree_path  — Optional. Path to the current build's worktree
                             (skipped during worktree cleanup). Pass when
                             /build runs inside a worktree.
    main_branch            — Optional. Default "main". Used for "merged"
                             check.
    eval_keep_count        — Optional. Default 10.

Steps (9):
    0. preserve_events_log         — copy <working_dir>/<foreign_state_dirname()>/events.jsonl → post-deploy/ (best-effort)
    1. persist_run_artifacts       — copy 6 known artifacts to the state dir's build-runs/<run_id>/ (best-effort)
    2. persist_learnings_to_db    — invoke bun persist-learnings.ts to upsert LEARNINGS block → memory.db (best-effort, optional via cfg["persist_learnings_disabled"])
    3. cleanup_worktrees           — git worktree list → remove merged, skip current
    4. cleanup_gone_branches       — fetch --prune → delete `: gone]` branches
    5. cleanup_scratchpad_subdirs  — rm research/architecture/specs/tests/reviews/injection
    6. cleanup_eval_rotation       — keep newest N entries in $SCRATCHPAD/evals/
    7. write_cleanup_report        — aggregate prior data → post-deploy/cleanup-report.md
    8. deregister_session          — best-effort active-sessions.json removal via deregister-session.ts CLI

Outputs:
    $SCRATCHPAD/post-deploy/cleanup-report.md
"""
from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import telemetry_ctx
sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))
from bounded_spawn import bounded_run  # noqa: E402
from lib.run_allowlist import remove_run_allowlist, resolve_zones_config_path  # noqa: E402  1DA29C33
try:
    from .phase_workflows_common import _git_read, _git_write  # noqa: E402,F401  D52228C3 §2.9 re-export
except ImportError:  # pragma: no cover — bare fallback for sys.path-rooted test imports (GH881)
    from phase_workflows_common import _git_read, _git_write  # type: ignore[no-redef]  # noqa: E402,F401  D52228C3 §2.9 re-export
from contracts import StepContract, StepResult, WorkflowDefinition
from config_provider import get_config, build_runs_relpath, foreign_state_dirname  # noqa: E402
from lib.plugins.disk_truth.test_runner import run_test_command
from lib.plugins.disk_truth.suite_boyscout import (
    parse_failing_nodeids,
    parse_allowlist,
    evaluate as boyscout_evaluate,
    _today as boyscout_today,
)
from net_new_delta import delta_verdict

logger = logging.getLogger(__name__)


def _emit_safe(event_type: str, payload: dict) -> None:
    """Emit telemetry event via current run context; swallow all errors.
    Mirror of the helper in phase_45_spec / phase_45_spec_lite / phase_5_implement."""
    run_ctx = telemetry_ctx.get_current_run()
    if run_ctx is None or run_ctx.event_log is None:
        return
    try:
        run_ctx.event_log.append(event_type, payload, run_ctx.run_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("telemetry append failed for %s: %s", event_type, e)

DEFAULT_MAIN_BRANCH = "main"
DEFAULT_EVAL_KEEP_COUNT = 10
SCRATCHPAD_SUBDIRS_TO_CLEAN = (
    "research",
    "architecture",
    "specs",
    "tests",
    "reviews",
    "injection",
)
CLEANUP_REPORT_RELPATH = "post-deploy/cleanup-report.md"


def __getattr__(name: str):
    """PEP-562 lazy module attribute (GH444): BUILD_RUNS_RELPATH is a
    backward-compat name (locked by test_42D33BB5 AC6) computed from
    config_provider.build_runs_relpath() at ACCESS time instead of a fixed
    import-time literal. Production sites already call build_runs_relpath()
    directly (42D33BB5) — this attr exists for external/test attribute access.
    """
    if name == "BUILD_RUNS_RELPATH":
        return build_runs_relpath()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

PERSIST_ARTIFACTS: tuple[tuple[str, str], ...] = (
    ("working_dir", "events.jsonl"),
    ("scratchpad",  "specs/build-spec.md"),
    ("scratchpad",  "reviews/build-opus-validation.md"),
    ("scratchpad",  "reviews/build-integrity-review.md"),
    ("scratchpad",  "reviews/build-satisfaction.md"),
    ("scratchpad",  "post-deploy/post-deploy-report.md"),
)


# ─── helpers ─────────────────────────────────────────────────────────────────


def _resolve_hal_dir(cfg: dict) -> Path:
    """Resolve HAL root: per-build cfg["hal_dir"] wins over global singleton.

    Restores the per-build hal_dir override capability that the 8B5D45CF
    migration dropped when it replaced cfg.get("hal_dir") with the global
    get_config().hal_root() singleton (which ignores per-build org_config).
    """
    raw = cfg.get("hal_dir")
    if raw:
        return Path(str(raw)).expanduser().resolve()
    return get_config().hal_root()


def _resolve_scratchpad(ctx) -> Path:
    cfg = ctx.org_config or {}
    raw = cfg.get("scratchpad_dir")
    if not raw:
        raise ValueError("org_config.scratchpad_dir required for phase_8_post_deploy")
    return Path(raw).expanduser().resolve()


def _resolve_working_dir(ctx) -> Path:
    cfg = ctx.org_config or {}
    raw = cfg.get("working_dir")
    if not raw:
        return Path.cwd()
    return Path(raw).expanduser().resolve()


def _path_contains(outer: Path, inner: Path) -> bool:
    """True iff resolved ``inner`` equals or descends from resolved ``outer``.

    Returns True on resolution failure (broken symlink, permission denied,
    ELOOP). Used by `_cleanup_worktrees` to guard against destroying the host
    worktree; failing safe means a transient FS error never re-enables the
    2026-04-27 destruction path.
    """
    try:
        return inner.resolve().is_relative_to(outer.resolve())
    except (OSError, ValueError):
        return True


def _accumulate_summary(prev, my_step_name: str, my_data: dict) -> dict:
    """Forward a flat ``step_summary`` map with each step's data keyed by name.

    Each step calls this once to merge the previous step's snapshot into the
    accumulating summary, then continues to add its own keys to ``my_data``.
    The report writer reads the final summary to compose the cleanup report
    without walking a nested chain (which is what broke the first attempt).
    """
    if isinstance(prev, StepResult) and isinstance(prev.data, dict):
        summary = dict(prev.data.get("step_summary", {}))
        snapshot = {k: v for k, v in prev.data.items() if k != "step_summary"}
        summary[prev.step_name] = snapshot
    else:
        summary = {}
    my_data["step_summary"] = summary
    return my_data


def _gh(args: list[str], cwd: Path, *, timeout: int = 30) -> tuple[int, str, str]:
    """Run a gh command, capture stdout/stderr. Never raises.

    Returns (returncode, stdout, stderr). Caller decides how to handle non-zero.
    Binary is env-overridable via HAL_GH_BIN (§1h).
    """
    gh_bin = get_config().binary("HAL_GH_BIN", "gh")
    try:
        proc = bounded_run(
            [gh_bin, *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError:
        return 127, "", "gh: command not found"
    if proc.returncode == 124:
        return 124, "", f"gh {' '.join(args)}: timeout after {timeout}s"
    return proc.returncode, proc.stdout, proc.stderr


def _parse_pr_url(stdout: str) -> str | None:
    """Return the last non-empty line that looks like a GitHub PR URL, else None.

    Matches lines of the form https://...github.com/.../pull/<n>.
    """
    pattern = re.compile(r"https://[^\s]*github\.com/[^\s]*/pull/\d+")
    last_url: str | None = None
    for line in stdout.splitlines():
        match = pattern.search(line.strip())
        if match:
            last_url = match.group(0)
    return last_url


def _strip_untracked(porcelain: str) -> str:
    """Drop untracked (`??`) entries from `git status --porcelain` output.

    `git push` ships only committed commits — never untracked files — so an
    untracked file outside the build's changed set must NOT block the ship
    (E_SHIP_DIRTY_TREE was over-broad: it hard-failed on any `??` line, e.g.
    auto-staged memory-backups or another session's scratch in a shared
    checkout). Tracked modifications (staged/unstaged) still represent
    uncommitted build work and DO block.
    """
    kept = [ln for ln in porcelain.splitlines() if ln.strip() and not ln.startswith("??")]
    return "\n".join(kept)


def _parse_worktree_list(porcelain: str) -> list[dict[str, str]]:
    """Parse `git worktree list --porcelain` output into list of dicts.

    Each entry: ``{"path": ..., "branch": ...}`` (branch may be "" for detached).
    Source format:
        worktree /abs/path
        HEAD <sha>
        branch refs/heads/<name>
        <blank line>
    """
    entries: list[dict[str, str]] = []
    current: dict[str, str] = {}
    for line in porcelain.splitlines():
        if line.startswith("worktree "):
            if current.get("path"):
                entries.append(current)
            current = {"path": line[len("worktree "):].strip(), "branch": ""}
        elif line.startswith("branch "):
            ref = line[len("branch "):].strip()
            if ref.startswith("refs/heads/"):
                current["branch"] = ref[len("refs/heads/"):]
            else:
                current["branch"] = ref
        elif line == "" and current.get("path"):
            entries.append(current)
            current = {}
    if current.get("path"):
        entries.append(current)
    return entries


def _rebase_onto_origin_main(cwd: Path, main: str) -> tuple[str, str, str]:
    """Rebase the current branch onto `origin/<main>` before ship (GH1119).

    Returns ``(outcome, detail, head_after)`` where outcome is one of
    ``"rebased"`` / ``"noop"`` / ``"skipped"`` / ``"conflict"``.

    A conflict deliberately leaves the worktree mid-rebase (no ``--abort``, no
    ``-X``/``--strategy``) so a human can finish the resolution by hand.
    """
    rc_head, head_out, _ = _git_read(["rev-parse", "--verify", "HEAD"], cwd)
    if rc_head != 0:
        return "skipped", "no HEAD", ""
    head_before = head_out.strip()

    rc_remote, _, _ = _git_read(["remote", "get-url", "origin"], cwd)
    if rc_remote != 0:
        return "skipped", "no origin remote", head_before

    # Explicit refspec: `git rebase origin/<main>` reads the remote-tracking ref,
    # so the fetch must really move refs/remotes/origin/<main>.
    refspec = f"+refs/heads/{main}:refs/remotes/origin/{main}"
    rc_fetch, _, fetch_err = _git_write(["fetch", "origin", refspec], cwd, timeout=120)
    if rc_fetch != 0:
        return "skipped", f"fetch failed: {fetch_err.strip()}", head_before

    rc_verify, _, _ = _git_read(["rev-parse", "--verify", f"origin/{main}"], cwd)
    if rc_verify != 0:
        return "skipped", f"no origin/{main} ref", head_before

    rc_rb, rb_out, rb_err = _git_write(["rebase", f"origin/{main}"], cwd, timeout=300)
    if rc_rb != 0:
        detail = rb_err.strip() or rb_out.strip() or f"git rebase rc={rc_rb}"
        return "conflict", detail, head_before

    rc_after, after_out, _ = _git_read(["rev-parse", "--verify", "HEAD"], cwd)
    head_after = after_out.strip() if rc_after == 0 else head_before
    outcome = "rebased" if head_after != head_before else "noop"
    return outcome, "", head_after


def _detect_phantom_deletions(cwd: Path, main: str) -> list[str]:
    """Files the PR diff would report as deleted that the branch never deleted (GH1119).

    Second belt behind the rebase: two-dot ``<base>..HEAD`` compares against the
    *current* tip of `origin/<main>`, which is exactly where a file merged
    mid-build lives. Files the branch's own commits genuinely deleted are
    subtracted, so a legitimate deletion is not a false positive.
    """
    base = ""
    for candidate in (f"origin/{main}", main):
        if _git_read(["rev-parse", "--verify", candidate], cwd)[0] == 0:
            base = candidate
            break
    if not base:
        return []

    rng = f"{base}..HEAD"
    _, tip_out, _ = _git_read(["diff", "--diff-filter=D", "--name-only", rng], cwd)
    _, own_out, _ = _git_read(
        ["log", "--diff-filter=D", "--name-only", "--pretty=format:", rng], cwd
    )
    # `git log --pretty=format:` prints blank separator lines — drop them.
    tip_deletions = {ln.strip() for ln in tip_out.splitlines() if ln.strip()}
    own_deletions = {ln.strip() for ln in own_out.splitlines() if ln.strip()}
    return sorted(tip_deletions - own_deletions)


def _ship_error(
    prev: StepResult | None, branch: str, outcome: str, error: str, code: str
) -> StepResult:
    """Build the shared non-recoverable ship-error StepResult (GH1119)."""
    return StepResult(
        status="error",
        data=_accumulate_summary(prev, "ship_to_pr", {
            "shipped": False,
            "skipped": None,
            "pr_url": None,
            "idempotent_hit": False,
            "branch": branch,
            "rebase": outcome,
        }),
        duration_ms=0,
        step_name="ship_to_pr",
        error=error,
        error_code=code,
        recoverable=False,
    )


def _ship_rebase_preflight(
    prev: StepResult | None, cwd: Path, main: str, branch: str
) -> tuple[StepResult | None, str]:
    """Rebase onto origin/<main>, then run the phantom-deletion belt (GH1119).

    Returns ``(None, outcome)`` when the ship may proceed, or
    ``(StepResult, outcome)`` for either terminal outcome.
    """
    outcome, detail, _head_after = _rebase_onto_origin_main(cwd, main)
    if outcome == "conflict":
        return _ship_error(
            prev, branch, outcome,
            f"rebase onto origin/{main} conflicted — worktree left mid-rebase for "
            f"manual resolution: {detail}",
            "E_SHIP_REBASE_CONFLICT",
        ), outcome

    # Second belt: refuse to open a PR whose diff deletes files the branch never touched.
    phantom = _detect_phantom_deletions(cwd, main)
    if phantom:
        return _ship_error(
            prev, branch, outcome,
            f"branch is stale w.r.t. origin/{main} — the PR diff would phantom-delete: "
            f"{', '.join(phantom)}",
            "E_SHIP_PHANTOM_DELETION",
        ), outcome

    return None, outcome


def _push_needs_lease(cwd: Path, branch: str, rebase_outcome: str) -> bool:
    """True when the rebase rewrote a branch that already exists on origin."""
    if rebase_outcome != "rebased":
        return False
    return _git_read(["rev-parse", "--verify", f"origin/{branch}"], cwd)[0] == 0


def _parse_report_summary(report_text: str) -> str:
    """Return the value of the first ``Done: ...`` line, truncated to 100 chars."""
    match = re.search(r"^\s*Done:\s*(.+?)\s*$", report_text, re.MULTILINE)
    if not match:
        return ""
    return match.group(1)[:100]


def _parse_issue_from_branch(branch: str) -> int | None:
    """Extract an issue number from a branch name (e.g. batch/1378, gh1124-x)."""
    match = re.search(r"(?:batch/|gh)(\d+)", branch, re.IGNORECASE)
    if not match:
        return None
    return int(match.group(1))


def _issue_type_prefix(cwd: Path, issue_n: int | None) -> str:
    """Return conventional-commit prefix ('fix'/'feat') from the issue's labels.

    Best-effort metadata lookup — must never raise or break ship.
    """
    if issue_n is None:
        return "fix"
    try:
        rc, out, _ = _gh(
            ["issue", "view", str(issue_n), "--json", "labels", "-q",
             "[.labels[].name] | join(\",\")"],
            cwd,
        )
        if rc != 0:
            return "fix"
        tokens = {t.strip() for t in out.lower().split(",")}
        if tokens & {"bug", "defect"}:
            return "fix"
        if tokens & {"enhancement", "feature"}:
            return "feat"
        return "fix"
    except Exception:  # noqa: BLE001 — metadata best-effort, never breaks ship
        return "fix"


def _compose_pr_title(summary: str, issue_n: int | None, prefix: str) -> str:
    """Compose a conventional-commit-style PR title from summary + issue metadata."""
    if not summary:
        return ""
    if issue_n is None:
        return summary
    return f"{prefix}(#{issue_n}): {summary}"


def _derive_pr_metadata(ctx: Any, org_config: dict[str, Any], cwd: Path, branch: str, main: str) -> tuple[str, str]:
    """Derive PR title/body — overrides win, else synthesizer report, else legacy fallbacks."""
    title = org_config.get("ship_pr_title") or ""
    if not title:
        report = _resolve_scratchpad(ctx) / "post-deploy/post-deploy-report.md"
        summary = ""
        if report.exists():
            try:
                summary = _parse_report_summary(report.read_text(encoding="utf-8"))
            except (OSError, UnicodeDecodeError):
                summary = ""
        if summary:
            issue_n = _parse_issue_from_branch(branch)
            prefix = _issue_type_prefix(cwd, issue_n)
            title = _compose_pr_title(summary, issue_n, prefix)
        if not title:
            rc_t, t_out, _ = _git_read(["log", "-1", "--format=%s"], cwd)
            title = t_out.strip() if rc_t == 0 else branch

    body = org_config.get("ship_pr_body") or ""
    if not body:
        report = _resolve_scratchpad(ctx) / "post-deploy/post-deploy-report.md"
        if report.exists():
            try:
                body = report.read_text(encoding="utf-8").strip()
            except (OSError, UnicodeDecodeError):
                body = ""
        if not body:
            cleanup_report = _resolve_scratchpad(ctx) / "post-deploy" / "cleanup-report.md"
            if cleanup_report.exists():
                body = cleanup_report.read_text(encoding="utf-8")
            else:
                rc_log, log_out, _ = _git_read(["log", f"{main}..HEAD", "--format=- %s"], cwd)
                body = log_out.strip() if rc_log == 0 else ""

    return title, body


# ─── Step 1 (new): ship to PR ───────────────────────────────────────────────


def _ship_to_pr(ctx, prev) -> StepResult:
    """Durable, idempotent ship step — push branch + create (or find) GitHub PR.

    Config-gated default-OFF: org_config["ship_pr"] must be truthy to activate.
    When disabled (default), returns ok with skipped="ship_pr disabled" and
    makes zero subprocess calls — zero behavior change for all current builds.
    """
    org_config = ctx.org_config or {}

    # Gate: default-off (AC2). HAL_BUILD_SHIP_PR=1 is a deterministic env-fallback
    # (2754DD92) so the batch path activates ship without the agent threading
    # ship_pr into ctx-JSON. Exact "1" only — any other value is treated as unset.
    ship_pr_enabled = bool(org_config.get("ship_pr")) or get_config().flag("HAL_BUILD_SHIP_PR")
    if not ship_pr_enabled:
        return StepResult(
            status="ok",
            data=_accumulate_summary(prev, "ship_to_pr", {
                "shipped": False,
                "skipped": "ship_pr disabled",
                "pr_url": None,
                "idempotent_hit": False,
                "branch": None,
            }),
            duration_ms=0,
            step_name="ship_to_pr",
        )

    cwd = _resolve_working_dir(ctx)
    main = org_config.get("main_branch", "main")

    # Resolve current branch
    rc_b, branch_out, _ = _git_read(["rev-parse", "--abbrev-ref", "HEAD"], cwd)
    branch = branch_out.strip() if rc_b == 0 else ""

    # Skip if on main or detached (AC3)
    if branch in (main, "HEAD", ""):
        return StepResult(
            status="ok",
            data=_accumulate_summary(prev, "ship_to_pr", {
                "shipped": False,
                "skipped": "on main / detached",
                "pr_url": None,
                "idempotent_hit": False,
                "branch": branch or None,
            }),
            duration_ms=0,
            step_name="ship_to_pr",
        )

    # Skip if 0 commits ahead of main (AC4)
    rc_c, count_out, _ = _git_read(["rev-list", "--count", f"{main}..HEAD"], cwd)
    commits_ahead = int(count_out.strip()) if rc_c == 0 and count_out.strip().isdigit() else 0
    if commits_ahead == 0:
        return StepResult(
            status="ok",
            data=_accumulate_summary(prev, "ship_to_pr", {
                "shipped": False,
                "skipped": "no commits ahead",
                "pr_url": None,
                "idempotent_hit": False,
                "branch": branch,
            }),
            duration_ms=0,
            step_name="ship_to_pr",
        )

    # Dirty-tree guard (AC5) — do NOT push uncommitted work
    rc_st, status_out, _ = _git_read(["status", "--porcelain"], cwd)
    if _strip_untracked(status_out).strip():
        return StepResult(
            status="error",
            data=_accumulate_summary(prev, "ship_to_pr", {
                "shipped": False,
                "skipped": None,
                "pr_url": None,
                "idempotent_hit": False,
                "branch": branch,
            }),
            duration_ms=0,
            step_name="ship_to_pr",
            error="dirty working tree — uncommitted changes present",
            error_code="E_SHIP_DIRTY_TREE",
            recoverable=False,
        )

    # Rebase onto origin/<main> before pushing (GH1119) — a branch that was cut
    # at launch time silently "deletes" whatever landed on main during the run.
    preflight, rebase_outcome = _ship_rebase_preflight(prev, cwd, main, branch)
    if preflight is not None:
        return preflight

    # Push branch (AC6/AC8). A rebased branch that already exists on origin is no
    # longer fast-forward — use --force-with-lease (never bare --force).
    if _push_needs_lease(cwd, branch, rebase_outcome):
        rc_push, _, push_err = _git_write(
            ["push", "--force-with-lease", "-u", "origin", branch], cwd
        )
    else:
        rc_push, _, push_err = _git_write(["push", "-u", "origin", branch], cwd)
    if rc_push != 0:
        return StepResult(
            status="error",
            data=_accumulate_summary(prev, "ship_to_pr", {
                "shipped": False,
                "skipped": None,
                "pr_url": None,
                "idempotent_hit": False,
                "branch": branch,
            }),
            duration_ms=0,
            step_name="ship_to_pr",
            error=push_err.strip() or f"git push rc={rc_push}",
            error_code="E_SHIP_PUSH_FAILED",
            recoverable=True,
        )

    # PR-exists check — idempotency seam for DBOS replay (AC7)
    rc_pl, pl_out, _ = _gh(["pr", "list", "--head", branch, "--state", "open",
                             "--json", "url,number"], cwd)
    if rc_pl == 0:
        try:
            pr_list = json.loads(pl_out)
        except (json.JSONDecodeError, ValueError):
            pr_list = []
        if isinstance(pr_list, list) and pr_list:
            existing_url = pr_list[0].get("url")
            return StepResult(
                status="ok",
                data=_accumulate_summary(prev, "ship_to_pr", {
                    "shipped": True,
                    "skipped": None,
                    "pr_url": existing_url,
                    "idempotent_hit": True,
                    "branch": branch,
                }),
                duration_ms=0,
                step_name="ship_to_pr",
            )

    # Derive PR metadata (AC10) — prefer synthesizer report over commit subject/trail
    title, body = _derive_pr_metadata(ctx, org_config, cwd, branch, main)

    # Create PR (AC6/AC9)
    rc_pc, pc_out, pc_err = _gh(
        ["pr", "create", "--head", branch, "--base", main, "--title", title, "--body", body],
        cwd,
    )
    if rc_pc != 0:
        return StepResult(
            status="error",
            data=_accumulate_summary(prev, "ship_to_pr", {
                "shipped": False,
                "skipped": None,
                "pr_url": None,
                "idempotent_hit": False,
                "branch": branch,
            }),
            duration_ms=0,
            step_name="ship_to_pr",
            error=pc_err.strip() or pc_out.strip() or f"gh pr create rc={rc_pc}",
            error_code="E_SHIP_PR_FAILED",
            recoverable=True,
        )

    pr_url = _parse_pr_url(pc_out)
    return StepResult(
        status="ok",
        data=_accumulate_summary(prev, "ship_to_pr", {
            "shipped": True,
            "skipped": None,
            "pr_url": pr_url,
            "idempotent_hit": False,
            "branch": branch,
        }),
        duration_ms=0,
        step_name="ship_to_pr",
    )


# ─── Step 0: preserve events log ────────────────────────────────────────────


def _preserve_events_log(ctx, prev) -> StepResult:
    cfg = ctx.org_config or {}
    override = cfg.get("events_log_path")
    if override:
        try:
            src = Path(override).expanduser().resolve()
        except (RuntimeError, OSError) as exc:
            return StepResult(
                status="ok",
                data=_accumulate_summary(prev, "preserve_events_log", {
                    "copied": False,
                    "error": repr(exc),
                }),
                duration_ms=0,
                step_name="preserve_events_log",
            )
    else:
        src = _resolve_working_dir(ctx) / foreign_state_dirname() / "events.jsonl"

    dst = _resolve_scratchpad(ctx) / "post-deploy" / "events.jsonl"

    if not src.exists():
        return StepResult(
            status="ok",
            data=_accumulate_summary(prev, "preserve_events_log", {
                "skipped": True,
                "reason": "source_missing",
            }),
            duration_ms=0,
            step_name="preserve_events_log",
        )

    try:
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)
    except OSError as exc:
        return StepResult(
            status="ok",
            data=_accumulate_summary(prev, "preserve_events_log", {
                "copied": False,
                "src": str(src),
                "error": repr(exc),
            }),
            duration_ms=0,
            step_name="preserve_events_log",
        )

    return StepResult(
        status="ok",
        data=_accumulate_summary(prev, "preserve_events_log", {
            "copied": True,
            "src": str(src),
            "dst": str(dst),
        }),
        duration_ms=0,
        step_name="preserve_events_log",
    )


# ─── Step 1: persist run artifacts ──────────────────────────────────────────


def _persist_run_artifacts(ctx, prev) -> StepResult:
    """Copy 6 known build artifacts to the state dir's build-runs/<run_id>/ (best-effort).

    Skips silently when org_config.run_id is absent.  All failure paths
    return status="ok" so the step never blocks build close.
    """
    cfg = ctx.org_config or {}
    run_id = cfg.get("run_id")
    if not run_id:
        return StepResult(
            status="ok",
            data=_accumulate_summary(prev, "persist_run_artifacts", {
                "skipped": True,
                "reason": "no_run_id",
            }),
            duration_ms=0,
            step_name="persist_run_artifacts",
        )

    hal_dir = _resolve_hal_dir(cfg)

    working_dir = _resolve_working_dir(ctx)
    scratchpad = _resolve_scratchpad(ctx)
    dst_dir = hal_dir / build_runs_relpath() / run_id

    copied: list[str] = []
    skipped_missing: list[str] = []
    errors: list[dict] = []

    for kind, relpath in PERSIST_ARTIFACTS:
        base = Path(relpath).name
        src = (
            (working_dir / foreign_state_dirname() / relpath)
            if kind == "working_dir"
            else (scratchpad / relpath)
        )
        if not src.exists():
            skipped_missing.append(base)
            continue
        try:
            dst_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst_dir / base)
            copied.append(base)
        except OSError as exc:
            errors.append({
                "src": str(src),
                "basename": base,
                "error": repr(exc),
            })

    return StepResult(
        status="ok",
        data=_accumulate_summary(prev, "persist_run_artifacts", {
            "copied": copied,
            "skipped_missing": skipped_missing,
            "errors": errors,
            "dst_dir": str(dst_dir),
            "run_id": run_id,
        }),
        duration_ms=0,
        step_name="persist_run_artifacts",
    )


# ─── Step 2: persist learnings to memory.db ──────────────────────────────────


def _resolve_persist_report_path(ctx) -> tuple:
    """Return (path, source) where source ∈ {"durable","scratchpad","missing"}.

    Branch 1 (durable-first): run_id set AND durable archive exists.
    Branch 2 (fallback): scratchpad ephemeral report exists.
    Branch 3: (None, "missing").
    """
    cfg = ctx.org_config or {}

    hal_dir = _resolve_hal_dir(cfg)

    run_id = cfg.get("run_id")
    if run_id:
        durable = hal_dir / build_runs_relpath() / str(run_id) / "post-deploy-report.md"
        if durable.exists():
            return (durable, "durable")

    scratchpad = _resolve_scratchpad(ctx)
    ephemeral = scratchpad / "post-deploy" / "post-deploy-report.md"
    if ephemeral.exists():
        return (ephemeral, "scratchpad")

    return (None, "missing")


def _persist_learnings_to_db(ctx, prev) -> StepResult:
    cfg = ctx.org_config or {}

    # Opt-out (ops / tests can disable without removing the step)
    if cfg.get("persist_learnings_disabled") is True:
        telemetry_ctx.emit_safe("learnings_persist_outcome", {
            "outcome": "skipped", "reason": "disabled", "source": None,
            "report_path": None, "inserted": 0, "updated": 0, "skipped": 0,
            "exit_code": None,
        })
        return StepResult(status="ok",
            data=_accumulate_summary(prev, "persist_learnings_to_db",
                {"skipped": True, "reason": "disabled"}),
            duration_ms=0, step_name="persist_learnings_to_db")

    report_path, source = _resolve_persist_report_path(ctx)
    if report_path is None:
        telemetry_ctx.emit_safe("learnings_persist_outcome", {
            "outcome": "skipped", "reason": "report_missing", "source": "missing",
            "report_path": None, "inserted": 0, "updated": 0, "skipped": 0,
            "exit_code": None,
        })
        return StepResult(status="ok",
            data=_accumulate_summary(prev, "persist_learnings_to_db",
                {"skipped": True, "reason": "report_missing",
                 "report_path": None}),
            duration_ms=0, step_name="persist_learnings_to_db")

    hal_dir = _resolve_hal_dir(cfg)
    bridge = hal_dir / "SYSTEM" / "cli" / "build" / "persist-learnings.ts"

    bun_bin = get_config().binary("HAL_PERSIST_LEARNINGS_BUN_BIN", "bun")
    cmd = [bun_bin, str(bridge), "--report", str(report_path)]
    db_override = cfg.get("persist_learnings_db_path")
    if db_override:
        cmd += ["--db", str(db_override)]

    timeout_s = int(cfg.get("persist_learnings_timeout_sec") or 60)

    try:
        proc = bounded_run(cmd, capture_output=True, text=True,
                           timeout=timeout_s, check=False)
    except FileNotFoundError as exc:
        telemetry_ctx.emit_safe("learnings_persist_outcome", {
            "outcome": "error", "reason": "FileNotFoundError", "source": source,
            "report_path": str(report_path), "inserted": 0, "updated": 0, "skipped": 0,
            "exit_code": None,
        })
        return StepResult(status="ok",
            data=_accumulate_summary(prev, "persist_learnings_to_db",
                {"persisted": False, "error": f"FileNotFoundError: {exc}",
                 "source": source,
                 "inserted": 0, "updated": 0, "skipped": 0}),
            duration_ms=0, step_name="persist_learnings_to_db")

    if proc.returncode == 124:
        telemetry_ctx.emit_safe("learnings_persist_outcome", {
            "outcome": "error", "reason": "timeout", "source": source,
            "report_path": str(report_path), "inserted": 0, "updated": 0, "skipped": 0,
            "exit_code": None,
        })
        return StepResult(status="ok",
            data=_accumulate_summary(prev, "persist_learnings_to_db",
                {"persisted": False, "error": f"timeout after {timeout_s}s",
                 "source": source,
                 "inserted": 0, "updated": 0, "skipped": 0}),
            duration_ms=0, step_name="persist_learnings_to_db")

    # Parse final JSON event from stderr (bridge writes ONE JSON object per line to stderr).
    inserted = updated = skipped_n = 0
    final_event = None
    for line in (proc.stderr or "").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(obj, dict) and obj.get("event", "").startswith("learnings_persist_"):
            final_event = obj
    if isinstance(final_event, dict):
        inserted = int(final_event.get("inserted") or 0)
        updated = int(final_event.get("updated") or 0)
        skipped_n = int(final_event.get("skipped") or 0)

    if proc.returncode != 0:
        telemetry_ctx.emit_safe("learnings_persist_outcome", {
            "outcome": "error", "reason": "nonzero_exit", "source": source,
            "report_path": str(report_path),
            "inserted": inserted, "updated": updated, "skipped": skipped_n,
            "exit_code": proc.returncode,
        })
        return StepResult(status="ok",
            data=_accumulate_summary(prev, "persist_learnings_to_db",
                {"persisted": False,
                 "error": (proc.stderr or "").strip()[-400:] or f"rc={proc.returncode}",
                 "exit_code": proc.returncode,
                 "source": source,
                 "inserted": inserted, "updated": updated, "skipped": skipped_n}),
            duration_ms=0, step_name="persist_learnings_to_db")

    telemetry_ctx.emit_safe("learnings_persist_outcome", {
        "outcome": "persisted", "reason": None, "source": source,
        "report_path": str(report_path),
        "inserted": inserted, "updated": updated, "skipped": skipped_n,
        "exit_code": 0,
    })
    return StepResult(status="ok",
        data=_accumulate_summary(prev, "persist_learnings_to_db",
            {"persisted": True, "exit_code": 0,
             "source": source,
             "inserted": inserted, "updated": updated, "skipped": skipped_n,
             "report_path": str(report_path),
             "event": (final_event or {}).get("event")}),
        duration_ms=0, step_name="persist_learnings_to_db")


# ─── Step 3: cleanup worktrees ───────────────────────────────────────────────


def _cleanup_worktrees(ctx, _prev) -> StepResult:
    cfg = ctx.org_config or {}
    cwd = _resolve_working_dir(ctx)
    main_branch = cfg.get("main_branch") or DEFAULT_MAIN_BRANCH
    current_raw = cfg.get("current_worktree_path")
    current_resolved: Path | None = (
        Path(current_raw).expanduser().resolve() if current_raw else None
    )

    rc, stdout, stderr = _git_read(["worktree", "list", "--porcelain"], cwd=cwd)
    if rc != 0:
        # 6CBC19FA: fail-open with logger.warning — see agreement note in commit history.
        logger.warning(
            "6CBC19FA: cleanup_worktrees fail-open: 'git worktree list' rc=%d (cwd=%s); "
            "stderr=%s. Worktrees may accumulate; next run will retry.",
            rc, cwd, (stderr or "").strip()[:200],
        )
        return StepResult(
            status="ok",
            data=_accumulate_summary(_prev, "cleanup_worktrees", {
                "removed": [],
                "skipped": [],
                "kept_unmerged": [],
                "errors": [{"action": "list", "reason": stderr.strip() or f"rc={rc}"}],
            }),
            duration_ms=0,
            step_name="cleanup_worktrees",
        )

    worktrees = _parse_worktree_list(stdout)

    rc_merged, merged_out, _ = _git_read(["branch", "--merged", main_branch], cwd=cwd)
    merged_branches: set[str] = set()
    if rc_merged == 0:
        # `git branch --merged` prefixes the current branch with "* " and
        # branches checked out in other worktrees with "+ ". Strip both —
        # otherwise the worktree-cleanup path silently keeps merged worktrees
        # because their "+ name" lines fail to match this set.
        for line in merged_out.splitlines():
            name = line.strip().lstrip("*+").strip()
            if name:
                merged_branches.add(name)

    removed: list[str] = []
    skipped: list[str] = []
    kept_unmerged: list[str] = []
    errors: list[dict[str, str]] = []

    # Skip any worktree whose path equals or contains the live cwd or the
    # caller-declared current path. Exact-string equality alone misses
    # `cwd=<wt>/sub` vs `path=<wt>` and lets cleanup destroy its own host
    # worktree mid-pytest (2026-04-27 incident, agreement E6041CDA).
    skip_anchors: list[Path] = [a for a in (current_resolved, cwd) if a is not None]

    for wt in worktrees:
        path = wt["path"]
        branch = wt["branch"]
        candidate = Path(path).expanduser().resolve()

        if any(_path_contains(candidate, anchor) for anchor in skip_anchors):
            skipped.append(path)
            continue
        if branch == main_branch:
            skipped.append(path)
            continue
        if not branch:
            kept_unmerged.append(path)
            continue
        if branch not in merged_branches:
            kept_unmerged.append(path)
            continue

        rc_rm, _, rm_err = _git_write(["worktree", "remove", "--force", path], cwd=cwd)
        if rc_rm == 0:
            removed.append(path)
        else:
            errors.append({"path": path, "reason": rm_err.strip() or f"rc={rc_rm}"})

    return StepResult(
        status="ok",
        data=_accumulate_summary(_prev, "cleanup_worktrees", {
            "removed": removed,
            "skipped": skipped,
            "kept_unmerged": kept_unmerged,
            "errors": errors,
            "main_branch": main_branch,
        }),
        duration_ms=0,
        step_name="cleanup_worktrees",
    )


# ─── Step 4: cleanup gone branches ───────────────────────────────────────────


def _cleanup_gone_branches(ctx, prev) -> StepResult:
    cwd = _resolve_working_dir(ctx)
    errors: list[dict[str, str]] = []

    rc_fetch, _, fetch_err = _git_write(["fetch", "--prune"], cwd=cwd, timeout=60)
    if rc_fetch != 0:
        logger.warning(
            "6CBC19FA: cleanup_gone_branches fetch failed: 'git fetch --prune' rc=%d (cwd=%s); "
            "stderr=%s. Will attempt to list+delete from cached refs.",
            rc_fetch, cwd, (fetch_err or "").strip()[:200],
        )
        errors.append({"action": "fetch", "reason": fetch_err.strip() or f"rc={rc_fetch}"})

    rc_list, branch_out, _ = _git_read(["branch", "-vv"], cwd=cwd)
    if rc_list != 0:
        logger.warning(
            "6CBC19FA: cleanup_gone_branches fail-open: 'git branch -vv' rc=%d (cwd=%s). "
            "Gone branches may accumulate; next run will retry.",
            rc_list, cwd,
        )
        return StepResult(
            status="ok",
            data=_accumulate_summary(prev, "cleanup_gone_branches", {
                "deleted": [],
                "errors": errors + [{"action": "list", "reason": f"rc={rc_list}"}],
            }),
            duration_ms=0,
            step_name="cleanup_gone_branches",
        )

    gone_branches: list[str] = []
    for line in branch_out.splitlines():
        if ": gone]" in line:
            stripped = line.strip().lstrip("*+").strip()
            name = stripped.split(None, 1)[0] if stripped else ""
            if name:
                gone_branches.append(name)

    deleted: list[str] = []
    for branch in gone_branches:
        rc_del, _, del_err = _git_write(["branch", "-d", branch], cwd=cwd)
        if rc_del == 0:
            deleted.append(branch)
        else:
            errors.append({"branch": branch, "reason": del_err.strip() or f"rc={rc_del}"})

    return StepResult(
        status="ok",
        data=_accumulate_summary(prev, "cleanup_gone_branches", {
            "deleted": deleted,
            "errors": errors,
        }),
        duration_ms=0,
        step_name="cleanup_gone_branches",
    )


# ─── Step 5: cleanup scratchpad subdirs ──────────────────────────────────────


def _cleanup_scratchpad_subdirs(ctx, prev) -> StepResult:
    """Remove research/architecture/specs/tests/reviews/injection — preserve post-deploy/.

    `post-deploy/` is deliberately NOT cleaned: it holds the synthesizer
    report (phase-7) and the cleanup report we are about to write. Removing
    it would lose the audit trail.
    """
    scratchpad = _resolve_scratchpad(ctx)

    # GH1086 M2 (agreement 6604CC4B): guard against a foreign session's
    # scratchpad being cleaned up (parallel-lane cleanup race). If ctx has a
    # session_id and it doesn't match the scratchpad dir name, skip entirely.
    sid = getattr(ctx, "session_id", None)
    if sid and scratchpad.name != sid:
        _emit_safe(
            "scratchpad_ownership_mismatch",
            {
                "severity": "warning",
                "phase": 8,
                "step": "cleanup_scratchpad_subdirs",
                "scratchpad": str(scratchpad),
                "session_id": sid,
            },
        )
        return StepResult(
            status="ok",
            data=_accumulate_summary(prev, "cleanup_scratchpad_subdirs", {
                "skipped_foreign": True,
                "scratchpad": str(scratchpad),
                "session_id": sid,
                "removed": [],
                "errors": [],
            }),
            duration_ms=0,
            step_name="cleanup_scratchpad_subdirs",
        )

    removed: list[str] = []
    errors: list[dict[str, str]] = []

    for sub in SCRATCHPAD_SUBDIRS_TO_CLEAN:
        target = scratchpad / sub
        if not target.exists():
            continue
        try:
            shutil.rmtree(target)
            removed.append(str(target))
        except OSError as exc:
            errors.append({"path": str(target), "reason": str(exc)})

    return StepResult(
        status="ok",
        data=_accumulate_summary(prev, "cleanup_scratchpad_subdirs", {
            "removed": removed,
            "errors": errors,
            "preserved": ["post-deploy"],
        }),
        duration_ms=0,
        step_name="cleanup_scratchpad_subdirs",
    )


# ─── Step 6: cleanup eval rotation ───────────────────────────────────────────


def _cleanup_eval_rotation(ctx, prev) -> StepResult:
    cfg = ctx.org_config or {}
    scratchpad = _resolve_scratchpad(ctx)
    keep = int(cfg.get("eval_keep_count") or DEFAULT_EVAL_KEEP_COUNT)
    eval_dir = scratchpad / "evals"

    if not eval_dir.is_dir():
        return StepResult(
            status="ok",
            data=_accumulate_summary(prev, "cleanup_eval_rotation", {
                "kept": 0,
                "removed_count": 0,
                "removed": [],
                "errors": [],
                "skipped_reason": "no evals/ dir",
            }),
            duration_ms=0,
            step_name="cleanup_eval_rotation",
        )

    def _entry_mtime(p: Path) -> float:
        try:
            if p.is_symlink() and not p.exists():
                return 0.0
            return p.lstat().st_mtime
        except OSError:
            return 0.0

    entries = sorted(
        eval_dir.iterdir(),
        key=_entry_mtime,
        reverse=True,
    )
    to_keep = entries[:keep]
    to_remove = entries[keep:]
    errors: list[dict[str, str]] = []
    removed: list[str] = []

    for entry in to_remove:
        try:
            if entry.is_dir() and not entry.is_symlink():
                shutil.rmtree(entry)
            else:
                entry.unlink()
            removed.append(str(entry))
        except OSError as exc:
            errors.append({"path": str(entry), "reason": str(exc)})

    return StepResult(
        status="ok",
        data=_accumulate_summary(prev, "cleanup_eval_rotation", {
            "kept": len(to_keep),
            "removed_count": len(removed),
            "removed": removed,
            "errors": errors,
        }),
        duration_ms=0,
        step_name="cleanup_eval_rotation",
    )


# ─── Step 6b: cleanup run_allowlist (1DA29C33) ───────────────────────────────


def _cleanup_run_allowlist(ctx, prev) -> StepResult:
    """Remove this run's `run_allowlist` entry from the containment-zones
    config (GH276 step-2 writer counterpart). Best-effort — ALWAYS returns
    ok; a missing run_id/config/entry is a no-op skip, never a phase_8 fail.
    """
    cfg = ctx.org_config or {}
    run_id = cfg.get("run_id")
    result: dict = {"status": "skipped", "reason": "no_run_id"}
    if run_id:
        cfg_path = resolve_zones_config_path(cfg)
        if cfg_path is None:
            result = {"status": "skipped", "reason": "no_config"}
        else:
            try:
                result = remove_run_allowlist(cfg_path, run_id)
            except Exception as exc:  # noqa: BLE001 — best-effort cleanup
                result = {"status": "error", "reason": str(exc)}

    if result.get("status") in ("written", "removed"):
        _emit_safe(
            "run_allowlist_removed",
            {"run_id": run_id, "n_entries": result.get("n_entries")},
        )

    return StepResult(
        status="ok",
        data=_accumulate_summary(prev, "cleanup_run_allowlist", {"result": result}),
        duration_ms=0,
        step_name="cleanup_run_allowlist",
    )


# ─── Step 7: write cleanup report ────────────────────────────────────────────


def _write_cleanup_report(ctx, prev) -> StepResult:
    """Aggregate prior step data → post-deploy/cleanup-report.md.

    HARD GATE: if the file cannot be written, return E_CLEANUP_REPORT_WRITE_FAILED
    so callers know the audit trail did not close cleanly.
    """
    scratchpad = _resolve_scratchpad(ctx)
    report_path = scratchpad / CLEANUP_REPORT_RELPATH

    by_step: dict[str, dict] = {}
    if isinstance(prev, StepResult) and isinstance(prev.data, dict):
        by_step = dict(prev.data.get("step_summary", {}))
        snap = {k: v for k, v in prev.data.items() if k != "step_summary"}
        by_step[prev.step_name] = snap

    lines: list[str] = ["# Phase 8 Post-Deploy Cleanup Report", ""]

    pe = by_step.get("preserve_events_log", {})
    lines.append("## Events Log")
    if pe.get("skipped"):
        lines.append(f"- Skipped: {pe.get('reason', 'unknown')}")
    elif pe.get("copied") is False:
        lines.append(f"- Copy failed: {pe.get('error', '')}")
        if pe.get("src"):
            lines.append(f"- Source: {pe['src']}")
    elif pe.get("copied"):
        lines.append(f"- Copied: {pe.get('src')} -> {pe.get('dst')}")
    else:
        lines.append("- Not recorded")
    lines.append("")

    wt = by_step.get("cleanup_worktrees", {})
    lines.append("## Worktrees")
    lines.append(f"- Removed: {len(wt.get('removed', []))} {wt.get('removed', [])}")
    lines.append(f"- Skipped (current): {wt.get('skipped', [])}")
    lines.append(f"- Kept (unmerged): {wt.get('kept_unmerged', [])}")
    if wt.get("errors"):
        lines.append(f"- Errors: {wt['errors']}")
    lines.append("")

    gb = by_step.get("cleanup_gone_branches", {})
    lines.append("## Gone Branches")
    lines.append(f"- Deleted: {len(gb.get('deleted', []))} {gb.get('deleted', [])}")
    if gb.get("errors"):
        lines.append(f"- Errors: {gb['errors']}")
    lines.append("")

    sp = by_step.get("cleanup_scratchpad_subdirs", {})
    lines.append("## Scratchpad Subdirs")
    lines.append(f"- Removed: {len(sp.get('removed', []))}")
    lines.append(f"- Preserved: {sp.get('preserved', [])}")
    if sp.get("errors"):
        lines.append(f"- Errors: {sp['errors']}")
    lines.append("")

    ev = by_step.get("cleanup_eval_rotation", {})
    lines.append("## Eval Rotation")
    lines.append(f"- Kept: {ev.get('kept', 0)}  Removed: {ev.get('removed_count', 0)}")
    if ev.get("skipped_reason"):
        lines.append(f"- Skipped: {ev['skipped_reason']}")
    if ev.get("errors"):
        lines.append(f"- Errors: {ev['errors']}")
    lines.append("")

    lrn = by_step.get("persist_learnings_to_db", {})
    lines.append("## Learnings")
    if "persisted" not in lrn:
        reason = lrn.get("reason")
        lines.append(f"- Skipped: {reason}" if reason else "- Not recorded")
    elif lrn.get("persisted"):
        lines.append(
            f"- Persisted: inserted={lrn.get('inserted', 0)}, "
            f"updated={lrn.get('updated', 0)}, skipped={lrn.get('skipped', 0)}"
        )
        if lrn.get("event"):
            lines.append(f"- Event: {lrn['event']}")
    else:
        lines.append(f"- Persistence failed: {lrn.get('error', 'unknown error')}")
        lines.append(f"- Inserted: {lrn.get('inserted', 0)}, Updated: {lrn.get('updated', 0)}")
    if lrn.get("errors"):
        lines.append(f"- Errors: {lrn['errors']}")
    lines.append("")

    lines.append("phase_8_post_deploy: complete")
    body = "\n".join(lines) + "\n"

    try:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(body, encoding="utf-8")
    except OSError as exc:
        return StepResult(
            status="error",
            data={
                "report_path": str(report_path),
                "reason": str(exc),
                "step_summary": by_step,
            },
            duration_ms=0,
            step_name="write_cleanup_report",
            error=f"failed to write cleanup report: {exc}",
            error_code="E_CLEANUP_REPORT_WRITE_FAILED",
            recoverable=False,
        )

    return StepResult(
        status="ok",
        data={
            "report_path": str(report_path),
            "report_bytes_written": len(body.encode("utf-8")),
            "step_summary": by_step,
        },
        duration_ms=0,
        step_name="write_cleanup_report",
    )


# ─── Helpers: full-suite delta gate ──────────────────────────────────────────


def _full_suite_cmds(cfg: dict) -> list[list[str]]:
    """Return the configured full-suite commands from cfg.

    Returns [] when missing, non-list, or empty — callers treat that as
    baseline_unavailable (repo-agnostic; engine hardcodes NO suite).
    Each element is validated as list[str].
    """
    raw = cfg.get("full_suite_cmds")
    if not isinstance(raw, list) or not raw:
        return []
    result: list[list[str]] = []
    for item in raw:
        if isinstance(item, list) and item:
            result.append(item)
    return result


def _infer_framework(cmd: list[str]) -> str:
    """Infer the test framework from a command list.

    Returns "pytest" if "pytest" appears in cmd tokens (e.g. ["python3", "-m", "pytest"]),
    "bun" if the first token is "bun", else "unknown".
    """
    cmd_str = " ".join(cmd)
    if "pytest" in cmd_str:
        return "pytest"
    if cmd and cmd[0] == "bun":
        return "bun"
    return "unknown"


def _run_full_suite(
    cmds: list[list[str]], cwd: str, timeout: int
) -> tuple[int | None, list[tuple[str, str]]]:
    """Run every cmd in cwd, sum n_failed. Return additive tuple (n_failed, stdout_paths).

    Returns:
        (total_n_failed, [(framework, stdout_path), ...])
        Where total_n_failed is None on timeout or missing binary.

    Timeout sentinel: exit_code == 124 AND n_failed == sys.maxsize → return (None, paths_so_far).
    FileNotFoundError (runner binary missing) → return (None, []).
    Else accumulate n_failed and return (total, paths_list).
    """
    total = 0
    stdout_paths: list[tuple[str, str]] = []
    for cmd in cmds:
        framework = _infer_framework(cmd)
        try:
            result = run_test_command(cmd, cwd, timeout=timeout)
        except FileNotFoundError:
            return (None, stdout_paths)
        stdout_paths.append((framework, result.stdout_path))
        # Timeout sentinel: both conditions must hold (A1 advisory)
        if result.exit_code == 124 and result.n_failed == sys.maxsize:
            return (None, stdout_paths)
        total += result.n_failed
    return (total, stdout_paths)


def _compute_full_suite_baseline(
    cmds: list[list[str]],
    main_ref: str,
    git_cwd: str,
    timeout: int,
) -> int | None:
    """Run full suite at the main_ref worktree, return failure count or None.

    D4-analog invariant: temp worktree and its parent dir are ALWAYS cleaned up
    (success, failure, or exception). The primary checkout (git_cwd) is never
    mutated.
    """
    if not cmds:
        return None

    parent = tempfile.mkdtemp(prefix="fsd_baseline_")
    wt = os.path.join(parent, "wt")
    worktree_added = False
    try:
        rc, _, _ = _git_write(["worktree", "add", "--detach", wt, main_ref], Path(git_cwd))
        if rc != 0:
            return None
        worktree_added = True
        baseline_result = _run_full_suite(cmds, wt, timeout)
        return baseline_result[0]
    finally:
        if worktree_added:
            try:
                _git_write(["worktree", "remove", "--force", wt], Path(git_cwd))
            except Exception:
                pass
        try:
            shutil.rmtree(parent, ignore_errors=True)
        except Exception:
            pass


# ─── Step 8 (new): full-suite delta gate ─────────────────────────────────────


def _full_suite_delta_gate(ctx, prev) -> StepResult:
    """Default-OFF gate: compare HEAD full-suite failure count against main baseline.

    When enforce is falsy → zero subprocess calls, zero worktree, ran=False.
    When enforce is truthy and net-new regressions found → status="error" halts
    the workflow before ship_to_pr (§1o-traced: engine.py:234→321).
    """
    cfg = ctx.org_config or {}
    enforce = bool(cfg.get("full_suite_delta_enforce", False))  # flip-by:2026-09-01 692E4583

    if not enforce:
        return StepResult(
            status="ok",
            data=_accumulate_summary(prev, "full_suite_delta_gate", {
                "ran": False,
                "skipped": "full_suite_delta disabled",
                "classification": None,
                "baseline_failed": None,
                "current_failed": None,
                "net_new": 0,
                "would_block": False,
            }),
            duration_ms=0,
            step_name="full_suite_delta_gate",
        )

    cwd = _resolve_working_dir(ctx)
    main = cfg.get("main_branch", "main")
    cmds = _full_suite_cmds(cfg)
    timeout = int(cfg.get("full_suite_delta_timeout_sec", 1020))

    current_result = _run_full_suite(cmds, str(cwd), timeout)
    current = current_result[0]  # additive tuple: read count from element [0]
    baseline = _compute_full_suite_baseline(cmds, main, str(cwd), timeout)

    # A2 advisory: if baseline is None or current is None → non-blocking baseline_unavailable
    if baseline is None or current is None:
        data = _accumulate_summary(prev, "full_suite_delta_gate", {
            "ran": True,
            "classification": "baseline_unavailable",
            "baseline_failed": None,
            "current_failed": current,
            "net_new": 0,
            "would_block": False,
        })
        return StepResult(
            status="ok",
            data=data,
            duration_ms=0,
            step_name="full_suite_delta_gate",
        )

    verdict = delta_verdict(baseline, current, enforce=enforce)
    data = _accumulate_summary(prev, "full_suite_delta_gate", {
        "ran": True,
        "classification": verdict.classification,
        "baseline_failed": verdict.baseline_failed,
        "current_failed": verdict.current_failed,
        "net_new": verdict.net_new,
        "would_block": verdict.would_block,
    })

    if verdict.would_block:
        return StepResult(
            status="error",
            data=data,
            duration_ms=0,
            step_name="full_suite_delta_gate",
            error=(
                f"full-suite net-new regression: "
                f"baseline={verdict.baseline_failed} "
                f"current={verdict.current_failed} "
                f"net_new={verdict.net_new}"
            ),
            error_code="E_SHIP_FULL_SUITE_REGRESSION",
            recoverable=False,
        )

    return StepResult(
        status="ok",
        data=data,
        duration_ms=0,
        step_name="full_suite_delta_gate",
    )


# ─── Step 9 (new): suite-level boy-scout gate ────────────────────────────────


def _suite_boyscout_gate(ctx, prev) -> StepResult:
    """Default-OFF gate: detect unallowlisted failing node-ids in the full suite.

    Observe-only by default (full_suite_boyscout_enforce=False).
    When enforce is truthy and any uncovered or expired node-id is found →
    status="error" halts the workflow before ship_to_pr.
    """
    cfg = ctx.org_config or {}
    # full_suite_boyscout_enforce default False — rollout 8C9F758C flip-by:2026-09-01
    enforce = bool(cfg.get("full_suite_boyscout_enforce", False))

    # Resolve HAL_DIR for allowlist path (mirror how other phase_8 repo paths work)
    hal_dir = _resolve_hal_dir(cfg)

    allowlist_path = hal_dir / "SYSTEM" / "cli" / "build" / "suite-boyscout-allowlist.txt"

    # Load allowlist; missing file → empty allowlist (not an error)
    allowlist = {}
    if allowlist_path.exists():
        try:
            allowlist_text = allowlist_path.read_text(encoding="utf-8")
            allowlist = parse_allowlist(allowlist_text)
        except ValueError:
            raise  # propagate parse errors (Opus advisory: do not swallow)

    # Gather failing node-ids from _run_full_suite stdout files
    # We use the (framework, stdout_path) tuples from a synthetic _run_full_suite call.
    # For the boyscout gate we rely on already-run suite output from the delta gate's
    # _run_full_suite result. Since we can't re-use that result here without coupling,
    # we invoke _run_full_suite again OR accept a monkeypatched stub for tests.
    cmds = cfg.get("full_suite_cmds", [])
    if not isinstance(cmds, list):
        cmds = []

    cwd = _resolve_working_dir(ctx)
    timeout = int(cfg.get("full_suite_delta_timeout_sec", 1020))

    suite_result = _run_full_suite(cmds, str(cwd), timeout)
    n_failed_raw, stdout_path_list = suite_result

    # Gather all failing node-ids from each (framework, stdout_path) pair
    failing: list[str] = []
    for framework, stdout_path in stdout_path_list:
        if not os.path.exists(stdout_path):
            continue
        try:
            content = open(stdout_path, encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        failing.extend(parse_failing_nodeids(content, framework))

    today = boyscout_today()
    verdict = boyscout_evaluate(failing, allowlist, today=today, enforce=enforce)

    data = _accumulate_summary(prev, "suite_boyscout_gate", {
        "ran": True,
        "enforce": enforce,
        "n_failing": len(verdict.failing),
        "n_covered": len(verdict.covered),
        "n_expired": len(verdict.expired),
        "n_uncovered": len(verdict.uncovered),
        "would_block": verdict.would_block,
    })

    if verdict.would_block:
        offenders = verdict.uncovered + verdict.expired
        return StepResult(
            status="error",
            data=data,
            duration_ms=0,
            step_name="suite_boyscout_gate",
            error=(
                f"suite boy-scout: unallowlisted or expired failing node-ids: "
                + ", ".join(offenders)
            ),
            error_code="E_SHIP_UNALLOWLISTED_RED",
            recoverable=False,
        )

    return StepResult(
        status="ok",
        data=data,
        duration_ms=0,
        step_name="suite_boyscout_gate",
    )


# ─── Step 10 (was 9): deregister session ─────────────────────────────────────


def _deregister_session(ctx, prev) -> StepResult:
    """Best-effort `active-sessions.json` cleanup via deregister-session.ts CLI.

    Bug B fix: phase 8 must call the sibling CLI on clean exit so future builds
    aren't blocked by stale entries. This step NEVER blocks Phase 8 — every
    failure path returns status="ok" with a diagnostic in `data`.
    """
    session_id = ctx.session_id
    # Engine boundary normally catches None/empty (required_ctx_fields=["session_id"]);
    # this guard remains defensively for direct workflow callers bypassing run.py.
    if not session_id:
        return StepResult(
            status="ok",
            data=_accumulate_summary(prev, "deregister_session", {
                "skipped": True,
                "reason": "no_session_id",
            }),
            duration_ms=0,
            step_name="deregister_session",
        )

    cfg = ctx.org_config or {}
    hal_dir = _resolve_hal_dir(cfg)

    # Agreement 3575E771: pass --cwd so deregister-session resolves the
    # sessions path the same way pre-build-gate did at registration time
    # (cwd-aware via defaultSessionsPath). Without this flag the CLI would
    # default to its own process.cwd(), which here equals where the engine
    # was invoked — matches in practice but explicit is safer.
    working_dir = _resolve_working_dir(ctx)
    cmd = [
        "bun",
        str(hal_dir / "SYSTEM" / "cli" / "build" / "deregister-session.ts"),
        "--session-id",
        session_id,
        "--cwd",
        str(working_dir),
    ]

    try:
        proc = bounded_run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except FileNotFoundError as exc:
        return StepResult(
            status="ok",
            data=_accumulate_summary(prev, "deregister_session", {
                "deregistered": False,
                "error": f"FileNotFoundError: {exc}",
                "session_id": session_id,
            }),
            duration_ms=0,
            step_name="deregister_session",
        )

    if proc.returncode == 124:
        return StepResult(
            status="ok",
            data=_accumulate_summary(prev, "deregister_session", {
                "deregistered": False,
                "error": "timeout after 30s",
                "session_id": session_id,
            }),
            duration_ms=0,
            step_name="deregister_session",
        )

    if proc.returncode != 0:
        return StepResult(
            status="ok",
            data=_accumulate_summary(prev, "deregister_session", {
                "deregistered": False,
                "error": (proc.stderr or "").strip() or f"rc={proc.returncode}",
                "session_id": session_id,
            }),
            duration_ms=0,
            step_name="deregister_session",
        )

    removed: bool | None
    try:
        parsed = json.loads(proc.stdout)
        removed = parsed.get("removed") if isinstance(parsed, dict) else None
    except (json.JSONDecodeError, ValueError):
        removed = None

    return StepResult(
        status="ok",
        data=_accumulate_summary(prev, "deregister_session", {
            "removed": removed,
            "session_id": session_id,
        }),
        duration_ms=0,
        step_name="deregister_session",
    )


# ─── workflow definition ─────────────────────────────────────────────────────


def phase_8_post_deploy_workflow() -> WorkflowDefinition:
    return WorkflowDefinition(
        name="phase_8_post_deploy",
        steps=[
            StepContract(name="preserve_events_log", execute=_preserve_events_log),
            StepContract(name="persist_run_artifacts", execute=_persist_run_artifacts),
            StepContract(name="persist_learnings_to_db", execute=_persist_learnings_to_db),
            StepContract(name="cleanup_worktrees", execute=_cleanup_worktrees),
            StepContract(name="cleanup_gone_branches", execute=_cleanup_gone_branches),
            StepContract(name="cleanup_scratchpad_subdirs", execute=_cleanup_scratchpad_subdirs),
            StepContract(name="cleanup_eval_rotation", execute=_cleanup_eval_rotation),
            StepContract(name="cleanup_run_allowlist", execute=_cleanup_run_allowlist),
            StepContract(name="write_cleanup_report", execute=_write_cleanup_report),
            StepContract(name="full_suite_delta_gate", execute=_full_suite_delta_gate),
            StepContract(name="suite_boyscout_gate", execute=_suite_boyscout_gate),
            StepContract(name="ship_to_pr", execute=_ship_to_pr),
            StepContract(
                name="deregister_session",
                execute=_deregister_session,
                required_ctx_fields=["session_id"],
            ),
        ],
    )
