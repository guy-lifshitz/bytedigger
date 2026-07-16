"""Phase 6 Fix-Integrity Diff Guard — FIX-side mirror of phase_5_integrity.

Agreement 1C6AFE11. Detects ASSERTION_GAMING in fix-LLM commits the same way
phase_5_integrity detects it for GREEN. SHA boundary differs: this gate diffs
between two committed SHAs (pre_fix_sha..fix_commit_sha) — fix changes are
already committed by _commit_fix_code (7547E02F). Working-tree diff would
always be empty after that ship.

Steps (3): build_fix_integrity_prompt → invoke_fix_integrity_llm → classify_fix_diff_verdict.
Hard gate on ASSERTION_GAMING / no-marker (same cautious semantic).

Inputs (via `ctx.org_config`):
    scratchpad_dir              — REQUIRED. Absolute path to scratchpad root.
    fix_commit_sha              — Optional. Post-fix committed SHA (highest priority).
                                  Falls back to scratchpad/integrity/fix-commit-sha.txt,
                                  then git rev-parse HEAD.
    pre_fix_sha                 — Optional. Pre-fix committed SHA.
                                  Falls back to scratchpad/integrity/pre-fix-ref.txt,
                                  then <fix_commit_sha>~1.
    diff_patterns               — Optional. List of git pathspec patterns.
                                  Default ['*test*', '*spec*', '*.test.*'].
    git_cwd                     — Optional. Working directory for git diff subprocess.
    diff_command                — Optional. Full override of the diff command.
    role_template_path          — Optional. Prepended to prompt.
    fix_integrity_llm_command   — Optional. Per-step LLM command override.
    fix_integrity_llm_timeout_sec — Optional. Default 600.

`ctx.question` carries the user's feature request text.

Steps (3):
    1. build_fix_integrity_prompt    — resolve SHAs, run git diff <pre>..<post>,
                                       write patch file, build prompt.
                                       Equal SHAs or empty diff → verdict_override=NO_CHANGES,
                                       but ONLY after a dirty-worktree guard (GH449): a
                                       non-clean `git status --porcelain` (or an
                                       unverifiable git-status call) at either short-circuit
                                       returns error E_FIX_UNCOMMITTED_CHANGES instead —
                                       uncommitted fix edits must never classify NO_CHANGES.
    2. invoke_fix_integrity_llm      — opaque subprocess (skipped on verdict_override).
    3. classify_fix_diff_verdict     — write review doc, parse VERDICT marker,
                                       HARD GATE on ASSERTION_GAMING / no-marker.

Outputs:
    $SCRATCHPAD/integrity/fix-test-diff.patch
    $SCRATCHPAD/reviews/build-fix-integrity-review.md  (skipped on NO_CHANGES)

Verdict markers (last-marker-wins via rfind):
    VERDICT: SPEC_CHANGE          → status="ok"
    VERDICT: LEGITIMATE_REFACTOR  → status="ok"
    VERDICT: ASSERTION_GAMING     → status="error", E_FIX_INTEGRITY_ASSERTION_GAMING
    VERDICT: NO_CHANGES           → status="ok" (synthetic; only when verdict_override set)
    no marker                     → status="error", E_FIX_INTEGRITY_NO_MARKER
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from contracts import StepContract, StepResult, WorkflowDefinition
from llm_subprocess import invoke_llm_subprocess

sys.path.insert(0, str(Path(__file__).parent.parent / "lib" / "plugins"))
sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))
from lib import git_port  # noqa: E402
from lib.git_cwd import resolve_git_cwd  # noqa: E402  GH381
from anti_hallucination.helper import (  # noqa: E402
    get_prompt_fragment as _get_anti_fab_prompt,
    get_out_of_role_block as _get_out_of_role_block,
)
from model_config import get_claude_critical  # noqa: E402
from verdict_parse import last_standalone_line_verdict  # noqa: E402
from config_provider import timeout_policy_path  # noqa: E402  GH285 C2
from lib.timeout_policy import DEFAULT_POLICY, cached_policy, resolve_timeout_sec  # noqa: E402  GH285 C2


def _timeout_policy() -> dict:
    return cached_policy(str(timeout_policy_path()))


def _default_model() -> str:
    # Fix-integrity gate is HARD GATE (hard_gate=True in invoke_llm_subprocess);
    # ModelConfig "critical" alias resolves to "opus" which passes _is_opus_model.
    # If models.json ever ships a non-Opus critical, the chokepoint refuses with
    # E_HARD_GATE_MODEL_DOWNGRADE — defense in depth.
    return get_claude_critical()


def _default_llm_command() -> list[str]:
    """Back-compat alias returning argv form."""
    from llm_subprocess import _build_claude_argv
    return _build_claude_argv(_default_model())


DEFAULT_LLM_COMMAND = _default_llm_command()
DEFAULT_INTEGRITY_TIMEOUT_SEC = DEFAULT_POLICY["fix_integrity.llm"]["base"]


def _resolve_integrity_timeout_sec(cfg: dict | None) -> int:
    """fix_integrity.llm timeout via unified policy (GH285 C2)."""
    return resolve_timeout_sec("fix_integrity.llm", cfg, policy=_timeout_policy())
DEFAULT_DIFF_PATTERNS = ("*test*", "*spec*", "*.test.*")

DIFF_PATCH_RELPATH = "integrity/fix-test-diff.patch"
PRE_FIX_REF_RELPATH = "integrity/pre-fix-ref.txt"
FIX_COMMIT_SHA_RELPATH = "integrity/fix-commit-sha.txt"
REVIEW_DOC_RELPATH = "reviews/build-fix-integrity-review.md"
SPEC_DOC_RELPATH = "specs/build-spec.md"

VERDICT_SPEC_CHANGE = "SPEC_CHANGE"
VERDICT_LEGITIMATE_REFACTOR = "LEGITIMATE_REFACTOR"
VERDICT_ASSERTION_GAMING = "ASSERTION_GAMING"
VERDICT_NO_CHANGES = "NO_CHANGES"
VERDICT_UNKNOWN = "UNKNOWN"


# ─── SHA boundary resolution (differs from phase_5_integrity) ─────────────────


def _resolve_fix_commit_sha(cfg: dict, scratchpad: Path | None, git_cwd: Path | None) -> str | None:
    """Resolve post-fix HEAD ref. Order:
    1. cfg["fix_commit_sha"]
    2. scratchpad/integrity/fix-commit-sha.txt
    3. fallback: git rev-parse HEAD in git_cwd
    Returns None if all fail (caller raises E_MISSING_FIX_BOUNDARY).
    """
    sha = cfg.get("fix_commit_sha")
    if sha:
        return sha
    if scratchpad is not None:
        f = scratchpad / FIX_COMMIT_SHA_RELPATH
        if f.is_file():
            content = f.read_text().strip()
            if content:
                return content
    cwd = git_cwd or Path.cwd()
    try:
        res = git_port.git_read(["rev-parse", "HEAD"], cwd=str(cwd), timeout=10)
        if res.timed_out:
            return None
        if res.returncode != 0:
            raise subprocess.CalledProcessError(res.returncode, ["git", "rev-parse", "HEAD"])
        sha = res.stdout.strip()
        return sha or None
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def _resolve_pre_fix_sha(cfg: dict, scratchpad: Path | None, fix_commit_sha: str | None, git_cwd: Path | None) -> str | None:
    """Resolve pre-fix HEAD ref. Order:
    1. cfg["pre_fix_sha"]
    2. scratchpad/integrity/pre-fix-ref.txt
    3. fallback: <fix_commit_sha>~1 (resolved via git rev-parse to validate)
    Returns None if all fail.
    """
    pre = cfg.get("pre_fix_sha")
    if pre:
        return pre
    if scratchpad is not None:
        f = scratchpad / PRE_FIX_REF_RELPATH
        if f.is_file():
            content = f.read_text().strip()
            if content:
                return content
    if not fix_commit_sha:
        return None
    cwd = git_cwd or Path.cwd()
    try:
        res = git_port.git_read(["rev-parse", f"{fix_commit_sha}~1"], cwd=str(cwd), timeout=10)
        if res.timed_out:
            return None
        if res.returncode != 0:
            raise subprocess.CalledProcessError(res.returncode, ["git", "rev-parse", f"{fix_commit_sha}~1"])
        sha = res.stdout.strip()
        return sha or None
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def _resolve_fix_diff_command(cfg: dict, scratchpad: Path | None) -> list[str]:
    """Build the git diff argv for the FIX integrity check.
    1. cfg["diff_command"] full override
    2. cfg["pre_fix_sha"] + cfg["fix_commit_sha"] (REQUIRED for this helper)
    Returns: ["git","diff",<pre>,<post>,"--", *patterns]
    Raises ValueError if both SHAs aren't resolvable from cfg alone (this helper
    is called in test contexts where cfg has both; full resolution incl. scratchpad
    happens in step 1).
    """
    override = cfg.get("diff_command")
    if override:
        return list(override)
    pre = cfg.get("pre_fix_sha")
    post = cfg.get("fix_commit_sha")
    if not pre or not post:
        raise ValueError("_resolve_fix_diff_command requires pre_fix_sha and fix_commit_sha in cfg")
    patterns = list(cfg.get("diff_patterns") or DEFAULT_DIFF_PATTERNS)
    cmd = ["git", "diff", pre, post, "--"]
    cmd.extend(patterns)
    return cmd


# ─── Shared helpers (mirrored from phase_5_integrity) ────────────────────────


def _resolve_scratchpad(ctx) -> Path:
    cfg = ctx.org_config or {}
    raw = cfg.get("scratchpad_dir")
    if not raw:
        raise ValueError("org_config.scratchpad_dir required for phase_6_fix_integrity")
    return Path(raw).expanduser().resolve()


def _resolve_model(cfg: dict, override_key: str = "fix_integrity_model", default: str | None = None) -> str:
    return cfg.get(override_key) or cfg.get("model") or default or _default_model()


def _resolve_command(cfg: dict, override_key: str) -> list[str]:
    """Back-compat alias. Use _resolve_model for new callers."""
    from llm_subprocess import _build_claude_argv
    return _build_claude_argv(_resolve_model(cfg))


def _read_first_block(scratchpad: Path) -> str:
    inj = scratchpad / "injection"
    return (
        "READ_FIRST — read these four files before proceeding:\n"
        f"- {inj}/hal-memory.md      (learnings)\n"
        f"- {inj}/constitution.md    (project rules)\n"
        f"- {inj}/quality-gate.md    (zero-cornercutting policy)\n"
        f"- {inj}/active-work.md     (current project focus)\n"
        "If any file is missing or empty: orchestrator Phase 0.5 failed — "
        "VERDICT: ASSERTION_GAMING (cautious default; cannot certify integrity)."
    )


def _maybe_role_template(ctx) -> str:
    cfg = ctx.org_config or {}
    role_path = cfg.get("role_template_path")
    if not role_path:
        return ""
    rp = Path(role_path).expanduser()
    if not rp.is_file():
        return ""
    return rp.read_text(encoding="utf-8").rstrip() + "\n\n"


def _integrity_output_schema() -> str:
    return (
        "OUTPUT (markdown — written verbatim to reviews/build-fix-integrity-review.md):\n"
        "  ## Diff Summary\n"
        "    Files changed, lines added/removed (read the patch file yourself).\n"
        "  ## Hunk-by-Hunk Classification\n"
        "    For EACH test-file hunk, classify:\n"
        "      - SPEC_CHANGE         — spec requirement changed; assertion correctly updated\n"
        "      - LEGITIMATE_REFACTOR — test structure improved without changing assertions\n"
        "      - ASSERTION_GAMING    — assertion values/expectations changed to match broken code\n"
        "    Reference each hunk by file path + line range.\n"
        "  ## Rationale\n"
        "    Why this verdict. Cite specific assertion changes vs spec requirements.\n"
        "\n"
        "End your report with EXACTLY ONE verdict line on its own line. The verdict\n"
        "is the WORST classification across all hunks — ASSERTION_GAMING anywhere\n"
        "means the whole diff blocks. The line must START with VERDICT: <ONE_TOKEN>\n"
        "(UPPERCASE) — plain text, no markdown emphasis (no `**bold**`, no `__bold__`,\n"
        "no italics, no leading `#` heading). A short trailing description after the\n"
        "token is allowed and ignored by the parser:\n"
        "  VERDICT: SPEC_CHANGE          — all hunks legitimate spec-driven updates\n"
        "  VERDICT: LEGITIMATE_REFACTOR  — all hunks structural-only, no assertion changes\n"
        "  VERDICT: ASSERTION_GAMING     — ANY hunk gamed assertions to make broken code pass\n"
        "Examples of WRONG forms — do NOT use any of these:\n"
        "  **VERDICT: ASSERTION_GAMING**  (markdown bold around the marker)\n"
        "  Verdict: assertion_gaming      (lowercase)\n"
        "  ## VERDICT: ASSERTION_GAMING   (heading prefix)\n"
        "Do NOT default to SPEC_CHANGE without evidence. The fix worker's job is\n"
        "to fix CODE, not tests — gaming assertions is the failure mode this gate exists to catch.\n"
    )


def _parse_verdict(raw: str) -> str:
    """Standalone-line regex match; last winning line's marker is returned.

    Routes through lib/verdict_parse.py P1 (EEFD480F) — single-source of truth.
    Fixes the rfind BUG (AC-P2): prose-quoted mid-line occurrences no longer
    flip a clean standalone verdict. Only lines where VERDICT: <MARKER> appears
    alone are matched (optional **/__emphasis and case-insensitive).
    """
    return last_standalone_line_verdict(
        raw,
        (VERDICT_ASSERTION_GAMING, VERDICT_LEGITIMATE_REFACTOR, VERDICT_SPEC_CHANGE, VERDICT_NO_CHANGES),
        fallback=VERDICT_UNKNOWN,
        allow_trailing=True,
    )


def _dirty_worktree_guard(git_cwd: Path) -> StepResult | None:
    """GH449 Change 2: dirty worktree must never yield verdict_override=NO_CHANGES.

    Checks `git status --porcelain` in git_cwd. Non-empty output (any
    uncommitted tracked change or untracked file) means the fix worker's
    edits were applied but never committed — returns an error StepResult
    naming up to 10 dirty paths, instructing the caller to commit the fix
    edits then resume. A git-status failure (nonzero rc / timeout /
    FileNotFoundError) is treated as dirty-unknown (cautious default —
    never silently NO_CHANGES when tree state is unverifiable).

    Returns None when the tree is verified clean (safe to proceed with the
    existing NO_CHANGES override).
    """
    dirty_lines: list[str] | None = None
    try:
        res = git_port.git_read(["status", "--porcelain"], cwd=str(git_cwd), timeout=10)
        if res.timed_out or res.returncode != 0:
            dirty_lines = None
        else:
            dirty_lines = [line for line in res.stdout.splitlines() if line.strip()]
    except FileNotFoundError:
        dirty_lines = None

    if dirty_lines is None or dirty_lines:
        if dirty_lines:
            named = ", ".join(line.strip() for line in dirty_lines[:10])
            detail = f"dirty paths (first 10): {named}"
        else:
            detail = "git status could not be verified (dirty-unknown, cautious default)"
        return StepResult(
            status="error", data=None, duration_ms=0,
            step_name="build_fix_integrity_prompt",
            error=(
                "Uncommitted changes detected in the fix worktree — cannot classify "
                "NO_CHANGES while edits are unverified. Fix edits were applied but never "
                f"committed ({detail}). Commit the fix edits, then resume."
            ),
            error_code="E_FIX_UNCOMMITTED_CHANGES",
            recoverable=False,
        )
    return None


# ─── Step 1: resolve SHAs + collect diff + build prompt ──────────────────────


def _build_fix_integrity_prompt(ctx, _prev) -> StepResult:
    scratchpad = _resolve_scratchpad(ctx)
    cfg = ctx.org_config or {}
    git_cwd = Path(resolve_git_cwd(cfg))

    # 1. Resolve fix_commit_sha (post ref).
    fix_commit_sha = _resolve_fix_commit_sha(cfg, scratchpad, git_cwd)
    if not fix_commit_sha:
        return StepResult(
            status="error", data={}, duration_ms=0,
            step_name="build_fix_integrity_prompt",
            error="cannot resolve fix_commit_sha (no cfg, no scratchpad file, git rev-parse HEAD failed)",
            error_code="E_MISSING_FIX_BOUNDARY",
            recoverable=False,
        )

    # 2. Resolve pre_fix_sha (pre ref).
    pre_fix_sha = _resolve_pre_fix_sha(cfg, scratchpad, fix_commit_sha, git_cwd)
    if not pre_fix_sha:
        return StepResult(
            status="error", data={}, duration_ms=0,
            step_name="build_fix_integrity_prompt",
            error="cannot resolve pre_fix_sha (no cfg, no scratchpad file, fallback ~1 failed)",
            error_code="E_MISSING_FIX_BOUNDARY",
            recoverable=False,
        )

    review_path = scratchpad / REVIEW_DOC_RELPATH
    diff_path = scratchpad / DIFF_PATCH_RELPATH

    # 3. Equal SHAs → short-circuit BEFORE git diff (AC5).
    if pre_fix_sha == fix_commit_sha:
        dirty_guard = _dirty_worktree_guard(git_cwd)
        if dirty_guard is not None:
            return dirty_guard
        return StepResult(
            status="ok",
            data={
                "diff_path": str(diff_path),
                "doc_path": str(review_path),
                "diff_bytes": 0,
                "verdict_override": VERDICT_NO_CHANGES,
                "prompt": None,
                "diff_command": None,
                "pre_fix_sha": pre_fix_sha,
                "fix_commit_sha": fix_commit_sha,
            },
            duration_ms=0,
            step_name="build_fix_integrity_prompt",
        )

    # 4. Build diff command.
    cfg_with_shas = dict(cfg)
    cfg_with_shas.setdefault("pre_fix_sha", pre_fix_sha)
    cfg_with_shas.setdefault("fix_commit_sha", fix_commit_sha)
    try:
        diff_cmd = _resolve_fix_diff_command(cfg_with_shas, scratchpad)
    except ValueError as e:
        return StepResult(
            status="error", data={"diff_command": []}, duration_ms=0,
            step_name="build_fix_integrity_prompt",
            error=str(e), error_code="E_DIFF_EXIT", recoverable=False,
        )

    # 5. Run git diff.
    diff_args = diff_cmd[1:] if diff_cmd and diff_cmd[0] == "git" else diff_cmd
    try:
        res = git_port.git_read(
            diff_args,
            cwd=str(git_cwd or Path.cwd()),
            timeout=60,
        )
    except FileNotFoundError as e:
        return StepResult(
            status="error", data={"diff_command": diff_cmd}, duration_ms=0,
            step_name="build_fix_integrity_prompt",
            error=f"git binary not found: {e}",
            error_code="E_DIFF_CMD_MISSING", recoverable=False,
        )
    if res.timed_out:
        return StepResult(
            status="error", data={"diff_command": diff_cmd}, duration_ms=0,
            step_name="build_fix_integrity_prompt",
            error="git diff exceeded 60s",
            error_code="E_DIFF_TIMEOUT",
        )

    if res.returncode != 0:
        return StepResult(
            status="error",
            data={"diff_command": diff_cmd, "exit_code": res.returncode, "stderr": res.stderr[:2000]},
            duration_ms=0,
            step_name="build_fix_integrity_prompt",
            error=f"git diff exited {res.returncode}",
            error_code="E_DIFF_EXIT",
        )

    diff_text = res.stdout
    diff_path.parent.mkdir(parents=True, exist_ok=True)
    diff_path.write_text(diff_text, encoding="utf-8")

    # 6. Empty diff → NO_CHANGES override (AC4).
    if not diff_text.strip():
        dirty_guard = _dirty_worktree_guard(git_cwd)
        if dirty_guard is not None:
            return dirty_guard
        return StepResult(
            status="ok",
            data={
                "diff_path": str(diff_path),
                "doc_path": str(review_path),
                "diff_bytes": 0,
                "verdict_override": VERDICT_NO_CHANGES,
                "prompt": None,
                "diff_command": diff_cmd,
                "pre_fix_sha": pre_fix_sha,
                "fix_commit_sha": fix_commit_sha,
            },
            duration_ms=0,
            step_name="build_fix_integrity_prompt",
        )

    # 7. Build prompt for non-empty diff.
    spec_path = scratchpad / SPEC_DOC_RELPATH
    parts: list[str] = []
    role = _maybe_role_template(ctx)
    if role:
        parts.append(role.rstrip())
        parts.append("")
    parts.append(_read_first_block(scratchpad))
    parts.append("")
    parts.append(
        "ROLE: You are a Test-Integrity reviewer for FIX-phase changes (HARD GATE). "
        "Read the diff between the pre-fix HEAD and the fix commit, then classify "
        "each test-file hunk. Your job is specifically to catch ASSERTION_GAMING — "
        "the FIX worker changing assertions to match broken code instead of fixing the code. "
        "Do NOT default to SPEC_CHANGE."
    )
    parts.append("")
    parts.append("FEATURE REQUEST:")
    parts.append(ctx.question or "(no feature request provided)")
    parts.append("")
    parts.append(f"DIFF (pre-fix..fix; read this file — do NOT trust summaries): {diff_path}")
    if spec_path.is_file():
        parts.append(f"SPEC (read this file): {spec_path}")
    else:
        parts.append(f"SPEC: (none at {spec_path} — judge against the feature request alone)")
    parts.append("")
    parts.append(_integrity_output_schema())
    parts.append("")
    parts.append(_get_anti_fab_prompt())

    prompt = "\n".join(parts) + "\n\n" + _get_out_of_role_block()
    return StepResult(
        status="ok",
        data={
            "diff_path": str(diff_path),
            "doc_path": str(review_path),
            "diff_bytes": len(diff_text.encode("utf-8")),
            "spec_doc_present": spec_path.is_file(),
            "prompt": prompt,
            "prompt_bytes": len(prompt.encode("utf-8")),
            "diff_command": diff_cmd,
            "pre_fix_sha": pre_fix_sha,
            "fix_commit_sha": fix_commit_sha,
        },
        duration_ms=0,
        step_name="build_fix_integrity_prompt",
    )


# ─── Step 2: invoke LLM (skipped on NO_CHANGES) ──────────────────────────────


def _invoke_fix_integrity_llm(ctx, prev) -> StepResult:
    if not isinstance(prev, StepResult) or not isinstance(prev.data, dict):
        return StepResult(
            status="error", data=None, duration_ms=0,
            step_name="invoke_fix_integrity_llm",
            error="prev step did not produce a prompt",
            error_code="E_MISSING_PREV_DATA",
        )
    if prev.data.get("verdict_override"):
        # Short-circuit: equal SHAs or empty diff, no LLM needed.
        return StepResult(
            status="ok",
            data={
                "doc_path": prev.data["doc_path"],
                "diff_path": prev.data["diff_path"],
                "verdict_override": prev.data["verdict_override"],
                "raw_response": None,
                "skipped": True,
                "pre_fix_sha": prev.data.get("pre_fix_sha"),
                "fix_commit_sha": prev.data.get("fix_commit_sha"),
                "diff_command": prev.data.get("diff_command"),
            },
            duration_ms=0,
            step_name="invoke_fix_integrity_llm",
        )

    cfg = ctx.org_config or {}
    return invoke_llm_subprocess(
        prompt=prev.data["prompt"],
        model=_resolve_model(cfg, "fix_integrity_model", _default_model()),
        timeout_sec=_resolve_integrity_timeout_sec(cfg),
        step_name="invoke_fix_integrity_llm",
        extra_data={
            "doc_path": prev.data["doc_path"],
            "diff_path": prev.data["diff_path"],
        },
        hard_gate=True,
        gate_label="fix_integrity",
        allowed_tools=["Read"],
    )


# ─── Step 3: classify verdict + HARD GATE ────────────────────────────────────


def _classify_fix_diff_verdict(_ctx, prev) -> StepResult:
    if not isinstance(prev, StepResult) or not isinstance(prev.data, dict):
        return StepResult(
            status="error", data=None, duration_ms=0,
            step_name="classify_fix_diff_verdict",
            error="prev step did not produce raw_response",
            error_code="E_MISSING_PREV_DATA",
        )

    doc_path = Path(prev.data["doc_path"])
    diff_path = prev.data["diff_path"]

    if prev.data.get("verdict_override"):
        # NO_CHANGES: do NOT write review doc (AC4, AC5).
        common = {
            "verdict": prev.data["verdict_override"],
            "review_path": str(doc_path),
            "diff_path": diff_path,
            "skipped": True,
        }
        return StepResult(
            status="ok",
            data=common,
            duration_ms=0,
            step_name="classify_fix_diff_verdict",
        )

    raw = prev.data["raw_response"] or ""
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    doc_path.write_text(raw, encoding="utf-8")

    verdict = _parse_verdict(raw)
    common = {
        "verdict": verdict,
        "review_path": str(doc_path),
        "diff_path": diff_path,
        "review_bytes_written": len(raw.encode("utf-8")),
    }
    if verdict == VERDICT_ASSERTION_GAMING:
        return StepResult(
            status="error",
            data=common,
            duration_ms=0,
            step_name="classify_fix_diff_verdict",
            error="fix-integrity gate blocked: ASSERTION_GAMING detected",
            error_code="E_FIX_INTEGRITY_ASSERTION_GAMING",
            recoverable=False,
        )
    if verdict == VERDICT_UNKNOWN:
        return StepResult(
            status="error",
            data=common,
            duration_ms=0,
            step_name="classify_fix_diff_verdict",
            error="fix-integrity reviewer omitted VERDICT marker",
            error_code="E_FIX_INTEGRITY_NO_MARKER",
            recoverable=False,
        )
    return StepResult(
        status="ok",
        data=common,
        duration_ms=0,
        step_name="classify_fix_diff_verdict",
    )


def phase_6_fix_integrity_workflow() -> WorkflowDefinition:
    return WorkflowDefinition(
        name="phase_6_fix_integrity",
        steps=[
            StepContract(name="build_fix_integrity_prompt", execute=_build_fix_integrity_prompt),
            StepContract(name="invoke_fix_integrity_llm", execute=_invoke_fix_integrity_llm),
            StepContract(name="classify_fix_diff_verdict", execute=_classify_fix_diff_verdict),
        ],
    )
