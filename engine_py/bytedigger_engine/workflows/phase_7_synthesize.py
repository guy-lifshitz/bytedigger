"""Phase 7 (synthesize) as a WorkflowDefinition.

Stage 2.7 port (2026-04-25). Sixth LLM-heavy phase. Closes the build:
synthesizer writes post-deploy report (bullets + learnings + Final
Checkpoint). Living Doc updates are delegated to /ship cascade in
orchestrator mode — not run here.

**Scope of this v0 port — single-pass, single-agent.**
- SYNTHESIZE: ONE Haiku synthesizer (writes post-deploy report,
  enumerates learnings inline as structured block).
- DOCS: Delegated to /ship cascade (orchestrator + Task subagents can
  edit DOCS/*.md; claude -p subprocess cannot — sensitive-file gate).

phase-7-synthesize.md describes additional patterns DEFERRED in v0:

1. **Mode-aware pause (SUPERVISED).** Engine is mode-agnostic in v0 —
   caller decides whether to surface the post-deploy-report.md to the
   user. AUTONOMOUS vs SUPERVISED is an orchestrator-side concern, not
   a step in the engine's frozen state machine.
2. **Direct sqlite INSERT of learning_entries from orchestrator.**
   In v0 the synthesizer LLM emits learnings as a structured `LEARNINGS`
   block inside the report; persistence to memory.db is a post-engine
   side effect handled by the bridge (or a future `phase_7_persist_learnings`
   workflow). Engine stays free of sqlite I/O so event log remains the
   single audit trail.

Hard-gate semantics:
    SYNTHESIZER_BLOCKED → ``E_SYNTHESIZER_BLOCKED`` (manual intervention).
    SYNTHESIZER_NEEDS_CONTEXT → ``E_SYNTHESIZER_NEEDS_CONTEXT`` (caller must expand context).
    SYNTHESIZER_NO_MARKER → ``E_SYNTHESIZER_NO_MARKER`` (truncated output).

Token-spend guards (same playbook as phase_1 / phase_4 / phase_45 / phase_5 / phase_6):
    - Prompt lists READ_FIRST pointer paths only.
    - Synthesizer prompt references spec + review + fix + satisfaction by path.
    - Optional ``role_template_path`` (~3KB) replaces full CLAUDE.md (~10KB).
    - Default timeout: 600s synthesizer.

Inputs (via ``ctx.org_config``):
    scratchpad_dir              — REQUIRED. Absolute path to scratchpad root.
    role_template_path          — Optional. Prepended to synthesizer prompt.
    llm_command                 — Optional. Default: get_claude_fallback(). Global
                                  fallback for synthesizer subprocess call.
    synthesizer_llm_command     — Optional. Per-step override.
    synthesizer_llm_timeout_sec — Optional. Default 600.

``ctx.question`` carries the user's feature request text.

Steps (3):
    1. build_synthesizer_prompt   — deterministic; spec + review/fix/sat paths
    2. invoke_synthesizer_llm     — opaque subprocess (Haiku synthesizer)
    3. write_synthesizer_artifact — capture stdout to post-deploy/post-deploy-report.md;
                                    parse STATUS (DONE / DONE_WITH_CONCERNS /
                                    NEEDS_CONTEXT / BLOCKED);
                                    BLOCKED → E_SYNTHESIZER_BLOCKED

Outputs:
    $SCRATCHPAD/post-deploy/post-deploy-report.md
"""
from __future__ import annotations

import logging
import re
import sys
from pathlib import Path

from bytedigger_engine.contracts import StepContract, StepResult, WorkflowDefinition
from bytedigger_engine.derive_state import query_run_events
from bytedigger_engine.llm_subprocess import invoke_llm_subprocess
from bytedigger_engine import telemetry_ctx

from bytedigger_engine.lib.plugins.anti_hallucination.helper import (  # noqa: E402
    get_producer_prompt_fragment as _get_producer_anti_fab_prompt,
    get_out_of_role_block as _get_out_of_role_block,
)
from bytedigger_engine.io_utils import atomic_write  # noqa: E402
from bytedigger_engine.lib.model_config import get_claude_fallback  # noqa: E402
from bytedigger_engine.lib.plugins.disk_truth import git_diff_files, resolve_pre_phase_sha, parse_structured_block, enforce, SynthesizerVerdict, SchemaViolation  # noqa: E402
from bytedigger_engine.lib.verdict_parse import last_line_anchored_marker  # noqa: E402
from bytedigger_engine.lib.worktree_root import resolve_worktree_root as _resolve_worktree_root  # noqa: E402
from bytedigger_engine.config_provider import timeout_policy_path  # noqa: E402  GH285 C2
from bytedigger_engine.lib.timeout_policy import DEFAULT_POLICY, cached_policy, resolve_timeout_sec  # noqa: E402  GH285 C2


def _timeout_policy() -> dict:
    return cached_policy(str(timeout_policy_path()))


def _default_model() -> str:
    # 955657B2: function-form for runtime reset_cache() honoring.
    return get_claude_fallback()


def _default_llm_command() -> list[str]:
    """Back-compat alias returning argv form."""
    from bytedigger_engine.llm_subprocess import _build_claude_argv
    return _build_claude_argv(_default_model())


DEFAULT_LLM_COMMAND = _default_llm_command()
DEFAULT_SYNTHESIZER_TIMEOUT_SEC = DEFAULT_POLICY["synthesize.llm"]["base"]


def _resolve_synthesizer_timeout_sec(cfg: dict | None) -> int:
    """synthesize.llm timeout via unified policy (GH285 C2)."""
    return resolve_timeout_sec("synthesize.llm", cfg, policy=_timeout_policy())

REPORT_DOC_RELPATH = "post-deploy/post-deploy-report.md"
SPEC_DOC_RELPATH = "specs/build-spec.md"
REVIEW_DOC_RELPATH = "reviews/build-review.md"
FIX_DOC_RELPATH = "reviews/build-fix.md"
SATISFACTION_DOC_RELPATH = "reviews/build-satisfaction.md"

STATUS_DONE = "DONE"
STATUS_DONE_WITH_CONCERNS = "DONE_WITH_CONCERNS"
STATUS_NEEDS_CONTEXT = "NEEDS_CONTEXT"
STATUS_BLOCKED = "BLOCKED"
STATUS_NO_MARKER = "NO_MARKER"

logger = logging.getLogger(__name__)


# ─── helpers ─────────────────────────────────────────────────────────────────


def _emit_safe(event_type: str, payload: dict) -> None:
    """Emit telemetry event via current run context; swallow all errors."""
    run_ctx = telemetry_ctx.get_current_run()
    if run_ctx is None or run_ctx.event_log is None:
        return
    try:
        run_ctx.event_log.append(event_type, payload, run_ctx.run_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("telemetry append failed for %s: %s", event_type, e)


def _resolve_scratchpad(ctx) -> Path:
    cfg = ctx.org_config or {}
    raw = cfg.get("scratchpad_dir")
    if not raw:
        raise ValueError("org_config.scratchpad_dir required for phase_7_synthesize")
    return Path(raw).expanduser().resolve()


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


def _resolve_model(cfg: dict, override_key: str = "synthesizer_model", default: str | None = None) -> str:
    """Per-step override → global model → default.

    25e75663: renamed from _resolve_command; returns str model name.
    Mirrors phase_5_implement / phase_6_review pattern.
    """
    return cfg.get(override_key) or cfg.get("model") or default or _default_model()


def _resolve_command(cfg: dict, override_key: str) -> list[str]:
    """Back-compat alias. Use _resolve_model for new callers."""
    from bytedigger_engine.llm_subprocess import _build_claude_argv
    return _build_claude_argv(_resolve_model(cfg))


def _telemetry_digest(ctx) -> str:
    """Per-phase wall + cost summary for synthesizer prompt (9AB90CA0).

    Opt-in via ``org_config.include_telemetry_digest=True`` so existing
    tests stay deterministic. Reads default build-events.jsonl, filters
    to workflow_finished + subprocess_exited events from phases other
    than phase_7_synthesize itself, and renders ≤10 lines.

    Best-effort: any error returns empty string so synthesis never fails
    on telemetry issues.
    """
    cfg = ctx.org_config or {}
    if not cfg.get("include_telemetry_digest"):
        return ""
    try:
        finished = query_run_events(event_type="workflow_finished")
        exited = query_run_events(event_type="subprocess_exited")
    except Exception:
        return ""
    if not finished and not exited:
        return ""

    phase_wall: dict[str, int] = {}
    for evt in finished:
        payload = evt.get("payload") or {}
        wn = payload.get("workflow_name", "")
        if not wn or wn == "phase_7_synthesize":
            continue
        wall = payload.get("wall_ms")
        if isinstance(wall, int):
            phase_wall[wn] = wall  # latest occurrence wins

    phase_cost: dict[str, float] = {}
    phase_n: dict[str, int] = {}
    for evt in exited:
        payload = evt.get("payload") or {}
        ph = payload.get("phase", "")
        if not ph or ph == "phase_7_synthesize":
            continue
        cost = payload.get("cost_usd")
        if isinstance(cost, (int, float)):
            phase_cost[ph] = phase_cost.get(ph, 0.0) + float(cost)
        phase_n[ph] = phase_n.get(ph, 0) + 1

    phases = sorted(set(phase_wall) | set(phase_cost))
    if not phases:
        return ""

    lines = ["TELEMETRY (per-phase wall + cost from build-events.jsonl):"]
    for ph in phases:
        wall_s = (phase_wall.get(ph, 0) or 0) / 1000.0
        cost = phase_cost.get(ph, 0.0)
        n = phase_n.get(ph, 0)
        cost_part = f", ${cost:.4f} / {n} subproc" if n else ""
        lines.append(f"  - {ph}: {wall_s:.1f}s{cost_part}")
    total_cost = sum(phase_cost.values())
    total_n = sum(phase_n.values())
    if total_n:
        lines.append(f"  Total subprocess: ${total_cost:.4f} / {total_n} invocations")
    return "\n".join(lines)


def _collect_completed_phases(ctx) -> list[str]:
    """Deduped, first-seen-order list of completed workflow phases (GH450).

    Best-effort: any error returns [] so synthesis never fails on this.
    """
    try:
        finished = query_run_events(event_type="workflow_finished")
    except Exception:
        return []
    if not finished:
        return []

    phases: list[str] = []
    seen: set[str] = set()
    for evt in finished:
        payload = evt.get("payload") or {}
        wn = payload.get("workflow_name", "")
        if not wn or wn == "phase_7_synthesize":
            continue
        if wn in seen:
            continue
        seen.add(wn)
        phases.append(wn)
    return phases


def _completed_phase_digest(ctx) -> str:
    """Engine-derived completed-phase evidence for the synthesizer (GH450).

    Best-effort: any error returns empty string so synthesis never fails
    on digest issues (mirrors _telemetry_digest semantics).
    """
    phases = _collect_completed_phases(ctx)
    if not phases:
        return ""
    lines = ["COMPLETED PHASES (engine-derived from event log — authoritative):"]
    for ph in phases:
        lines.append(f"  - {ph}")
    return "\n".join(lines)


def _maybe_role_template(ctx) -> str:
    cfg = ctx.org_config or {}
    role_path = cfg.get("role_template_path")
    if not role_path:
        return ""
    rp = Path(role_path).expanduser()
    if not rp.is_file():
        return ""
    return rp.read_text(encoding="utf-8").rstrip() + "\n\n"


def _last_marker_wins(raw: str, markers: list[tuple[str, str]], fallback: str) -> str:
    """Return value of last line-anchored marker (case-insensitive).

    Delegates to lib/verdict_parse.py P2 (EEFD480F chokepoint).
    """
    return last_line_anchored_marker(raw, markers, fallback)


def _parse_synthesizer_status(raw: str) -> str:
    """Last-marker-wins: trailing STATUS line is the synthesizer's verdict.

    Aligned with phase-7-synthesize.md "Agent Status Protocol":
        STATUS: DONE | DONE_WITH_CONCERNS | NEEDS_CONTEXT | BLOCKED

    Tolerates the prompt's output-schema listing all four values.
    BLOCKED routes to E_SYNTHESIZER_BLOCKED; NO_MARKER signals truncation.
    """
    return _last_marker_wins(
        raw,
        [
            ("STATUS: DONE_WITH_CONCERNS", STATUS_DONE_WITH_CONCERNS),
            ("STATUS: NEEDS_CONTEXT", STATUS_NEEDS_CONTEXT),
            ("STATUS: BLOCKED", STATUS_BLOCKED),
            ("STATUS: DONE", STATUS_DONE),
        ],
        STATUS_NO_MARKER,
    )


def _parse_synthesizer_structured(raw: str) -> "tuple[SynthesizerVerdict | None, str | None]":
    """Parse the structured JSON synthesizer-output block from synthesizer output.

    Returns:
        (SynthesizerVerdict instance, None) on success.
        (None, "absent") if no structured block found.
        (None, "malformed") if block header present but JSON unparseable.
        (None, f"schema_violation: <field/reason>") on disk_truth schema rejection.
    """
    if not raw:
        return None, "absent"
    payload = parse_structured_block(raw, "synthesizer-output")
    if payload is None:
        if re.search(r"^##\s+synthesizer-output\s*\(\s*structured\s*\)", raw, re.MULTILINE):
            return None, "malformed"
        return None, "absent"
    if not isinstance(payload, dict):
        return None, "schema_violation: payload is not a dict"
    try:
        verdict = enforce(payload, SynthesizerVerdict)
    except SchemaViolation as e:
        return None, f"schema_violation: {e}"
    return verdict, None


# ─── Step 1: build synthesizer prompt ────────────────────────────────────────

# GH705 §2/§1d: maximal contiguous CALL-INVARIANT static instruction run
# hoisted from the synthesizer-prompt scaffold — the trailing "Surface-specific
# for SYNTHESIZER" block through the structured-output instructions. Byte-
# identical to the inline literal it replaces.
_SYNTHESIZER_STABLE_PREFIX = (
    "Surface-specific for SYNTHESIZER:\n"
    "  - Files line MUST come from `git diff --stat` you actually ran. Don't\n"
    "    copy a file list from another doc without verifying. Doc claim ≠\n"
    "    diff = synthesis discrepancy → flag as concern. If the working diff\n"
    "    is empty, take the Files line from the build's commits in\n"
    "    `git log --stat`.\n"
    "  - Learnings MUST be grounded in THIS build's review/fix/satisfaction\n"
    "    outcomes. No plausible-sounding generic learnings. If nothing\n"
    "    surprising happened, write fewer bullets — even one.\n"
    "  - LEARNINGS BLOCK confidence reflects calibration. Default 0.5-0.7\n"
    "    unless THIS build has empirical evidence for higher.\n"
    "  - STATUS: DONE only if review = PASS AND satisfaction ≥ threshold.\n"
    "    Below either → DONE_WITH_CONCERNS at minimum.\n"
    "\n"
    "End your response with EXACTLY one of:\n"
    "  STATUS: DONE                  ← clean ship, all gates green\n"
    "  STATUS: DONE_WITH_CONCERNS    ← shipped but list concerns above\n"
    "  STATUS: NEEDS_CONTEXT         ← cannot synthesize without more info\n"
    "  STATUS: BLOCKED               ← cannot synthesize at all (explain why)\n"
    "No status = invalid synthesis = blocks pipeline.\n"
    "\n"
    "ADDITIONALLY, after the trailing STATUS line, emit a structured JSON block:\n"
    "\n"
    "  ## synthesizer-output (structured)\n"
    "  ```json\n"
    '  {"synthesized": true, "needs_context": false, "concerns": []}\n'
    "  ```\n"
    "\n"
    "Set synthesized=true iff your STATUS is DONE or DONE_WITH_CONCERNS, with needs_context=false.\n"
    "For STATUS: DONE_WITH_CONCERNS, populate concerns with a list of "
    '{"title": "<concern>", "evidence": "<doc/section it traces to>"} objects (still synthesized=true).\n'
    "For STATUS: NEEDS_CONTEXT, set synthesized=false, needs_context=true, concerns=[].\n"
    "For STATUS: BLOCKED, set synthesized=false, needs_context=false, concerns=[] (explain in the report body above).\n"
    "This block is the AUTHORITATIVE gate signal — the engine reads it, not the STATUS line.\n"
    "The STATUS line stays for human audit. Both the STATUS line and this block are required."
)


def _build_synthesizer_prompt(ctx, _prev) -> StepResult:
    scratchpad = _resolve_scratchpad(ctx)
    spec_path = scratchpad / SPEC_DOC_RELPATH
    review_doc = scratchpad / REVIEW_DOC_RELPATH
    fix_doc = scratchpad / FIX_DOC_RELPATH
    sat_doc = scratchpad / SATISFACTION_DOC_RELPATH
    report_path = scratchpad / REPORT_DOC_RELPATH

    parts: list[str] = []
    role = _maybe_role_template(ctx)
    if role:
        parts.append(role.rstrip())
        parts.append("")
    parts.append(_read_first_block(scratchpad))
    parts.append("")
    parts.append(
        "ROLE: You are the Haiku synthesizer. Your response IS the post-deploy "
        "report file itself — not a report about reporting. Read the spec, "
        "review, fix, and satisfaction docs (paths below — open them yourself, "
        "do NOT trust summaries). Produce a Final Checkpoint and a structured "
        "LEARNINGS block grounded in what actually happened. NEVER call Skill "
        "tool, NEVER invoke /build, /bugfix, or any slash command."
    )
    parts.append("")
    parts.append("FEATURE REQUEST:")
    parts.append(ctx.question or "(no feature request provided)")
    parts.append("")
    digest = _telemetry_digest(ctx)
    if digest:
        parts.append(digest)
        parts.append("")
    completed_phases = _collect_completed_phases(ctx)
    if completed_phases:
        phase_lines = ["COMPLETED PHASES (engine-derived from event log — authoritative):"]
        for ph in completed_phases:
            phase_lines.append(f"  - {ph}")
        parts.append("\n".join(phase_lines))
        parts.append("")

    spec_present = spec_path.is_file()
    review_present = review_doc.is_file()
    fix_present = fix_doc.is_file()
    satisfaction_present = sat_doc.is_file()

    parts.append(f"SPEC (read this file): {spec_path}")
    parts.append(f"REVIEW (read this file): {review_doc}")
    parts.append(f"FIX (read this file): {fix_doc}")
    parts.append(f"SATISFACTION (read this file): {sat_doc}")
    parts.append("")
    parts.append("ARTIFACTS ON DISK (engine-verified):")
    parts.append(f"  - spec: {'PRESENT' if spec_present else 'MISSING'}")
    parts.append(f"  - review: {'PRESENT' if review_present else 'MISSING'}")
    parts.append(f"  - fix: {'PRESENT' if fix_present else 'MISSING'}")
    parts.append(f"  - satisfaction: {'PRESENT' if satisfaction_present else 'MISSING'}")
    parts.append("")
    parts.append(
        "Run `git diff --stat` to verify which files actually changed. "
        "If a doc reports a file changed but `git diff --stat` does not list "
        "it, that's a synthesis discrepancy — call it out as a concern. "
        "If `git diff --stat` is empty, the build's changes were likely already "
        "committed (resumed run) — check `git log --stat -10` before concluding "
        "no code was produced; an empty working diff is NOT evidence the build halted."
    )
    parts.append("")
    parts.append(
        "OUTPUT — your response IS the file content of\n"
        "post-deploy/post-deploy-report.md. Start your response DIRECTLY with\n"
        "`# Post-Deploy Report`. No preamble. No status markers other than the\n"
        "trailing STATUS line. The file IS the report.\n"
        "\n"
        "REQUIRED sections, in this exact order, no others:\n"
        "\n"
        "  # Post-Deploy Report\n"
        "\n"
        "  ## Final Checkpoint\n"
        "  Done: <feature summary, ≤120 chars — based on what actually shipped>\n"
        "  Files: <comma-separated list from `git diff --stat` output>\n"
        "  Review: <verdict from review doc + score from satisfaction doc>\n"
        "  Docs: See /ship cascade — Living Docs updated post-commit by Haiku in orchestrator mode.\n"
        "  Next: <manual test / PR / done>\n"
        "\n"
        "  ## Learnings\n"
        "  <3-5 bullets on what was learned during THIS build — surprising-only,\n"
        "   not generic `always test` tips. Each bullet must reference a specific\n"
        "   review finding, fix, or satisfaction concern — not a hypothetical.>\n"
        "\n"
        "  ## LEARNINGS BLOCK (machine-parseable; one per line)\n"
        "  ```\n"
        "  pattern=<short-slug> | domain=<area> | confidence=0.85 | "
        "approach=<FULL SENTENCE describing what was learned and why it matters>\n"
        "  ```\n"
        "\n"
        "  ## Concerns\n"
        "  <bullets — anything that would block shipping; `none` if clean.\n"
        "   Concerns must trace to evidence in review/fix/satisfaction docs —\n"
        "   do NOT invent a concern, do NOT swallow a real one.>\n"
        "\n"
        + _get_producer_anti_fab_prompt()
        + "\n"
        + _SYNTHESIZER_STABLE_PREFIX
    )

    prompt = "\n".join(parts) + "\n\n" + _get_out_of_role_block()
    return StepResult(
        status="ok",
        data={
            "prompt": prompt,
            "doc_path": str(report_path),
            "spec_path": str(spec_path),
            "review_doc_path": str(review_doc),
            "fix_doc_path": str(fix_doc),
            "satisfaction_doc_path": str(sat_doc),
            "spec_present": spec_present,
            "review_present": review_present,
            "fix_present": fix_present,
            "satisfaction_present": satisfaction_present,
            "completed_phases": completed_phases,
            "prompt_bytes": len(prompt.encode("utf-8")),
            "stable_prefix": _SYNTHESIZER_STABLE_PREFIX,
        },
        duration_ms=0,
        step_name="build_synthesizer_prompt",
    )


# ─── Step 2: invoke synthesizer LLM ──────────────────────────────────────────


def _invoke_synthesizer_llm(ctx, prev) -> StepResult:
    if not isinstance(prev, StepResult) or not isinstance(prev.data, dict):
        return StepResult(
            status="error", data=None, duration_ms=0,
            step_name="invoke_synthesizer_llm",
            error="prev step did not produce a prompt",
            error_code="E_MISSING_PREV_DATA",
        )
    cfg = ctx.org_config or {}
    return invoke_llm_subprocess(
        prompt=prev.data["prompt"],
        model=_resolve_model(cfg, "synthesizer_model", _default_model()),
        timeout_sec=_resolve_synthesizer_timeout_sec(cfg),
        step_name="invoke_synthesizer_llm",
        extra_data={
            "doc_path": prev.data["doc_path"],
            "spec_path": prev.data["spec_path"],
            "review_doc_path": prev.data["review_doc_path"],
            "fix_doc_path": prev.data["fix_doc_path"],
            "satisfaction_doc_path": prev.data["satisfaction_doc_path"],
        },
        allowed_tools=["Read", "Write", "Glob"],
        stable_prefix=prev.data.get("stable_prefix", ""),
    )


def _parse_files_line(raw: str) -> list[str] | None:
    """Parse a ``Files: a.py, b.py, c.py`` line from the post-deploy report.

    Returns ``None`` if no such line is present; an empty list if the line
    is present but lists nothing. Tolerant to leading whitespace and
    surrounding quotes/brackets.
    """
    if not raw:
        return None
    m = re.search(
        r"^\s*Files\s*:\s*(.*)$",
        raw,
        re.MULTILINE | re.IGNORECASE,
    )
    if not m:
        return None
    inner = m.group(1).strip()
    # Strip outer brackets if present (mirrors ``Files: [a, b, c]`` style)
    if inner.startswith("[") and inner.endswith("]"):
        inner = inner[1:-1]
    if not inner:
        return []
    parts = [p.strip().strip("'\"") for p in inner.split(",")]
    return [p for p in parts if p]


def _emit_synthesize_disk_truth_telemetry(ctx, prev, raw: str, doc_path: Path) -> None:
    """Emit ``synthesize_disk_truth`` event after a successful artifact write.

    Telemetry-first additive observability — never raises, never affects verdict gate.
    """
    try:
        scratchpad = _resolve_scratchpad(ctx)
        worktree_root = _resolve_worktree_root(ctx, scratchpad)
        # Pre-phase SHA: prefer prev.data["pre_phase_sha"] if upstream stages
        # ever populate it; else best-effort current HEAD.
        pre_sha = ""
        if isinstance(prev.data, dict):
            pre_sha = str(prev.data.get("pre_phase_sha") or "")
        if not pre_sha:
            try:
                pre_sha = resolve_pre_phase_sha(worktree_root)
            except Exception:  # noqa: BLE001
                pre_sha = ""
        try:
            disk_actual = git_diff_files(pre_sha, worktree_root)
        except Exception:  # noqa: BLE001
            disk_actual = []
        if not isinstance(disk_actual, list):
            disk_actual = list(disk_actual or [])

        claimed = _parse_files_line(raw)
        if claimed is None:
            files_in_synthesis_doc: list[str] = []
            files_line_present = False
        else:
            files_in_synthesis_doc = list(claimed)
            files_line_present = True

        disk_set = set(disk_actual)
        doc_set = set(files_in_synthesis_doc)
        drift = bool(doc_set - disk_set)
        extra_in_doc = sorted(list(doc_set - disk_set))
        missing_from_doc = sorted(list(disk_set - doc_set))

        _emit_safe(
            "synthesize_disk_truth",
            {
                "files_actual": list(disk_actual),
                "files_in_synthesis_doc": files_in_synthesis_doc,
                "drift": drift,
                "doc_path": str(doc_path),
                "files_line_present": files_line_present,
                "extra_in_doc": extra_in_doc,
                "missing_from_doc": missing_from_doc,
            },
        )
    except Exception as exc:  # noqa: BLE001
        # Telemetry must never break the workflow.
        logger.warning("synthesize_disk_truth telemetry skipped: %s", exc)
        try:
            _emit_safe(
                "synthesize_disk_truth_error",
                {
                    "error_class": type(exc).__name__,
                    "error_msg": str(exc),
                    "doc_path": str(doc_path),
                },
            )
        except Exception:  # noqa: BLE001
            pass


# ─── Step 3: write synthesizer artifact ──────────────────────────────────────


def _write_synthesizer_artifact(_ctx, prev) -> StepResult:
    if not isinstance(prev, StepResult) or not isinstance(prev.data, dict):
        return StepResult(
            status="error", data=None, duration_ms=0,
            step_name="write_synthesizer_artifact",
            error="prev step did not produce raw_response",
            error_code="E_MISSING_PREV_DATA",
        )
    raw = prev.data["raw_response"]
    doc_path = Path(prev.data["doc_path"])
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(doc_path, raw)

    status = _parse_synthesizer_status(raw)
    structured, sr_reason = _parse_synthesizer_structured(raw)
    if structured is not None:
        _emit_safe("synth_structured_ok", {"synthesized": structured.synthesized, "needs_context": structured.needs_context, "phase": 7})
    else:
        _emit_safe("synth_structured_missing", {"reason": sr_reason, "phase": 7})

    md_says_synthesized = status in (STATUS_DONE, STATUS_DONE_WITH_CONCERNS)
    if structured is not None and bool(structured.synthesized) != md_says_synthesized:
        _emit_safe("synth_verdict_drift", {
            "marker_status": status,
            "structured_synthesized": structured.synthesized,
            "phase": 7,
        })

    common_data = {
        "report_doc_path": str(doc_path),
        "spec_path": prev.data["spec_path"],
        "review_doc_path": prev.data["review_doc_path"],
        "fix_doc_path": prev.data["fix_doc_path"],
        "satisfaction_doc_path": prev.data["satisfaction_doc_path"],
        "report_bytes_written": len(raw.encode("utf-8")),
        "synthesizer_status": status,
        "structured_verdict": structured,
    }

    if structured is not None:
        if structured.synthesized:
            pass  # fall through to ok
        elif structured.needs_context:
            return StepResult(
                status="error", data=common_data, duration_ms=0,
                step_name="write_synthesizer_artifact",
                error="synthesizer reported needs-context (structured) — caller must expand context",
                error_code="E_SYNTHESIZER_NEEDS_CONTEXT",
            )
        else:
            return StepResult(
                status="error", data=common_data, duration_ms=0,
                step_name="write_synthesizer_artifact",
                error="synthesizer reported blocked (structured) — manual intervention required",
                error_code="E_SYNTHESIZER_BLOCKED",
                recoverable=False,
            )
    else:
        # No structured block — legacy marker-based gate (unchanged behavior).
        if status == STATUS_BLOCKED:
            return StepResult(
                status="error", data=common_data, duration_ms=0,
                step_name="write_synthesizer_artifact",
                error="synthesizer reported BLOCKED — manual intervention required",
                error_code="E_SYNTHESIZER_BLOCKED",
                recoverable=False,
            )
        if status == STATUS_NEEDS_CONTEXT:
            return StepResult(
                status="error", data=common_data, duration_ms=0,
                step_name="write_synthesizer_artifact",
                error="synthesizer reported NEEDS_CONTEXT — caller must expand context",
                error_code="E_SYNTHESIZER_NEEDS_CONTEXT",
            )
        if status == STATUS_NO_MARKER:
            return StepResult(
                status="error", data=common_data, duration_ms=0,
                step_name="write_synthesizer_artifact",
                error="synthesizer output missing STATUS marker — likely truncated",
                error_code="E_SYNTHESIZER_NO_MARKER",
            )
    _emit_synthesize_disk_truth_telemetry(_ctx, prev, raw, doc_path)
    return StepResult(
        status="ok",
        data=common_data,
        duration_ms=0,
        step_name="write_synthesizer_artifact",
    )


# ─── workflow definition ─────────────────────────────────────────────────────


def phase_7_synthesize_workflow() -> WorkflowDefinition:
    return WorkflowDefinition(
        name="phase_7_synthesize",
        steps=[
            StepContract(name="build_synthesizer_prompt", execute=_build_synthesizer_prompt),
            StepContract(name="invoke_synthesizer_llm", execute=_invoke_synthesizer_llm),
            StepContract(name="write_synthesizer_artifact", execute=_write_synthesizer_artifact),
        ],
    )
