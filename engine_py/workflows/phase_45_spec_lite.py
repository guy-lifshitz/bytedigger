"""Phase 4.5-lite (SIMPLE spec review) as a WorkflowDefinition.

Q2 5EB54FE4 (2026-04-26). Closes the structural hole surfaced by verify-
spike-2: SIMPLE tier had NO independent spec review — Phase 1 wrote spec
inline and went straight to Phase 5 RED. If Phase 1 fabricated, RED tests
got written against a bad spec. Per fix-cause-AND-catch doctrine, Q1
SpecKit prompt fix (commit fac514e3) is the cause-fix; this workflow is
the catch.

**Defense-in-depth, not a replacement.** This workflow runs AFTER
phase_1_discovery for SIMPLE complexity. Cycle 1 reads the spec Phase 1
already wrote and reviews it (Opus). On REVISE the engine retry hook
(Decree #1, commit 11671280) recurses with start_step=0 and cycle=2;
cycle 2 RE-WRITES the spec (Sonnet) with revision findings, then re-
reviews. Cap N=2 — same as Phase 5 validation pattern. Cycle-2 REVISE
yields terminal E_REVIEW_FAILED (recoverable=False).

**Why composite (rewrite + review in one workflow) instead of two:** the
engine retry hook recurses inside ONE workflow. To re-run the spec
writer with reviewer findings (the agreement's central requirement) the
re-write must live in the same WorkflowDefinition as the review. Cycle 1
short-circuits the rewrite steps because Phase 1 already wrote the spec.

Steps (7):
    0. maybe_rewrite_simple_spec_prompt  — no-op cycle 1; build SIMPLE
       prompt + REVISION section cycle 2.
    1. maybe_invoke_spec_rewrite         — no-op cycle 1; Sonnet
       subprocess cycle 2.
    2. maybe_write_spec_rewrite          — no-op cycle 1; write
       versioned (`specs/build-spec-cycle-2.md`) + overwrite canonical
       (`specs/build-spec.md`) cycle 2.
    3. build_review_prompt               — Opus reviewer prompt; spec
       referenced BY PATH (read once on disk).
    4. invoke_review_llm                 — Opus subprocess.
    5. write_review_doc                  — write
       `specs/build-plan-review.md`; parse SHIP/REVISE marker.
    6. gate_on_review                    — HARD GATE. REVISE on cycle<2
       → E_VALIDATION_RETRY recoverable=True (engine retries). REVISE on
       cycle 2 → E_REVIEW_FAILED recoverable=False (terminal abort).
       SHIP → ok.

Inputs (`ctx.org_config`):
    scratchpad_dir              — REQUIRED. Absolute path.
    role_template_path          — Optional. Prepended to BOTH prompts.
    llm_command                 — Optional. Default: get_claude_spec_writer().
    spec_llm_command            — Optional. Sonnet recommended (rewrite path).
    review_llm_command          — Optional. **Pin Opus** — review is the
                                  whole point; harness must not silently
                                  downgrade.
    spec_llm_timeout_sec        — Optional. Default 600.
    review_llm_timeout_sec      — Optional. Default 300.

Outputs:
    $SCRATCHPAD/specs/build-spec.md            (cycle 2 only — cycle 1
                                                preserves Phase 1's write)
    $SCRATCHPAD/specs/build-spec-cycle-2.md    (cycle 2 only)
    $SCRATCHPAD/specs/build-plan-review.md     (cycle 1)
    $SCRATCHPAD/specs/build-plan-review-cycle-2.md (cycle 2)

Decree:    the state dir's memory/Decisions/2026-04-26_pipeline-recovery-design.md
Findings:  the state dir's memory/Decisions/2026-04-26_verify-spike-2-findings.md
"""
from __future__ import annotations

import logging
import re
import sys
from pathlib import Path

# lib/ on sys.path — also relied on by phase_45_spec import below
sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))

import telemetry_ctx
from dataclasses import replace
from contracts import (
    LoopStepContract,
    StepContract,
    StepResult,
    WorkflowDefinition,
    format_boundary_error,
)
from engine import LoopRunner
from llm_subprocess import invoke_llm_subprocess
from io_utils import atomic_write
from model_config import get_claude_critical, get_claude_spec_writer, get_claude_spec_reviewer
from verdict_parse import verdict_under_heading  # noqa: E402
from phase_1_discovery import _build_prompt as _build_phase1_prompt
from phase_45_spec import (
    _verify_spec_cite_prelint,
    _verify_spec_completeness,
    _verify_spec_lint,
)
from skip_logic import detect_frozen_spec
from plugins.checklist_convergence import (
    build_reviewer_prompt as _restricted_reviewer_prompt,
    build_writer_prompt as _restricted_writer_prompt,
    extract_findings_for_writer,
    extract_structured_findings,
    parse_per_finding_verdicts,
)
from lib.spec_retry_cycle import (  # CF480CAE SSOT-01
    truncate_findings,
    resolve_command,
    resolve_model,
    resolve_review_model,
    read_first_block,
    _FINDINGS_MAX_BYTES,
    VERDICT_SHIP,
    VERDICT_REVISE,
    VERDICT_UNKNOWN,
)
from lib.run_allowlist import write_run_allowlist_for_spec  # noqa: E402  1DA29C33
from config_provider import timeout_policy_path  # noqa: E402  GH285 C2
from lib.timeout_policy import DEFAULT_POLICY, cached_policy, resolve_timeout_sec  # noqa: E402  GH285 C2


def _timeout_policy() -> dict:
    return cached_policy(str(timeout_policy_path()))

# The retry-cycle helpers now live in lib.spec_retry_cycle; only thin
# aliases remain below so existing call-sites and sibling tests that reference
# the private underscore names (e.g. phase_45_spec_lite._read_first_block) still
# resolve without modification.
_truncate_findings = truncate_findings
_resolve_command = resolve_command
_resolve_model = resolve_model
_resolve_review_model = resolve_review_model
_read_first_block = read_first_block

logger = logging.getLogger(__name__)


def _emit_safe(event_type: str, payload: dict) -> None:
    """Emit telemetry event via current run context; swallow all errors.
    Mirror of the helper in phase_05_inject / phase_5_implement / skip_logic."""
    run_ctx = telemetry_ctx.get_current_run()
    if run_ctx is None or run_ctx.event_log is None:
        return
    try:
        run_ctx.event_log.append(event_type, payload, run_ctx.run_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("telemetry append failed for %s: %s", event_type, e)


_FREETEXT_LIST_ITEM_RE = re.compile(r"^\d+\.", re.MULTILINE)


def _count_freetext_findings(raw: str) -> int:
    """Count numbered list items in the ## Findings (free-text) section.
    Returns 0 if section is absent, empty, or contains only 'none'."""
    block = re.search(r"## Findings\n(.*?)(?=\n##|\Z)", raw, re.DOTALL)
    if not block:
        return 0
    content = block.group(1).strip()
    if not content or content.lower() == "none":
        return 0
    return len(_FREETEXT_LIST_ITEM_RE.findall(content))

def _default_spec_model() -> str:
    return get_claude_spec_writer()


def _default_review_model() -> str:
    """GH597: model-mix — models.json spec_reviewer role, else legacy critical."""
    return get_claude_spec_reviewer() or get_claude_critical()


def _default_spec_llm_command() -> list[str]:
    """Back-compat alias returning argv form."""
    from llm_subprocess import _build_claude_argv
    return _build_claude_argv(_default_spec_model())


def _default_review_llm_command() -> list[str]:
    """Back-compat alias returning argv form."""
    from llm_subprocess import _build_claude_argv
    return _build_claude_argv(_default_review_model())


# Backward-compat module-level constants (computed once at import via ModelConfig).
DEFAULT_SPEC_LLM_COMMAND = _default_spec_llm_command()
DEFAULT_REVIEW_LLM_COMMAND = _default_review_llm_command()
DEFAULT_SPEC_TIMEOUT_SEC = DEFAULT_POLICY["spec_lite.writer"]["base"]
DEFAULT_REVIEW_TIMEOUT_SEC = DEFAULT_POLICY["spec_lite.reviewer"]["base"]
# D1F51D7A — complexity-aware timeouts. Defensive symmetric extension of
# phase_45_spec resolver shape. lite-path is invoked for SIMPLE only today
# (phase_4 dispatch); FEATURE/COMPLEX tiers added pre-emptively so that any
# future re-routing of FEATURE-class through lite (or explicit cfg override
# carrying complexity=FEATURE) gets the matching timeout floor.
DEFAULT_REVIEW_TIMEOUT_SEC_FEATURE = DEFAULT_POLICY["spec_lite.reviewer"]["FEATURE"]  # NEW
DEFAULT_REVIEW_TIMEOUT_SEC_COMPLEX = DEFAULT_POLICY["spec_lite.reviewer"]["COMPLEX"]  # NEW (mirrors phase_45_spec 900s reviewer ceiling)


def _resolve_spec_timeout_sec(cfg: dict | None) -> int:
    """spec_lite.writer timeout via unified policy (GH285 C2)."""
    return resolve_timeout_sec("spec_lite.writer", cfg, policy=_timeout_policy())


def _resolve_review_timeout_sec(cfg: dict | None) -> int:
    """spec_lite.reviewer timeout via unified policy (GH285 C2)."""
    return resolve_timeout_sec("spec_lite.reviewer", cfg, policy=_timeout_policy())


SIMPLE_SPEC_RELPATH = "specs/build-spec.md"
REVIEW_DOC_RELPATH = "specs/build-plan-review.md"

# Cap matches Phase 5 validation pattern. Hard-coded for v1; configurable
# in v2 once telemetry data exists.
MAX_REVIEW_CYCLES = 2

# VERDICT_SHIP / VERDICT_REVISE / VERDICT_UNKNOWN imported from lib.spec_retry_cycle above.
# _FINDINGS_MAX_BYTES imported from lib.spec_retry_cycle above.

# 39CAFF09: severity classifier — gate downgrades reviewer-fabricated SHIP
# when underlying spec carries a block signal. Gate consumer must treat
# SHIP_WITH_CONCERNS distinctly from SHIP (no auto-promote to phase_5).
VERDICT_SHIP_WITH_CONCERNS = "SHIP_WITH_CONCERNS"

# 39CAFF09: signals in the spec body that mark it unshippable even if the
# reviewer fabricated SHIP. Case-insensitive substring match. Conservative
# list: only literals observed in real failure modes (2026-05-01 incident).
_BLOCK_SIGNALS = ("STATUS=BLOCK", "(no feature request provided)")

# 32ED4070: citation auto-correct (F88217EB-P3). Engine-side post-process for
# reviewer findings whose `> path:lineno: quote` citations are off-by-N from
# actual file content. Tier-B exact-in-window match only this ship (Out of
# Scope per spec § OOS 1). Mirrors phase_6_review._QUOTE_LINE_RE shape and
# _QUOTE_WINDOW_LINES value.
_CITATION_LINE_RE = re.compile(r"^>\s+([^:]+):(\d+):(.*)$", re.MULTILINE)
_CITATION_WINDOW_LINES = 3


def _detect_block_signals(spec_body: str) -> list[str]:
    """39CAFF09: scan spec body case-insensitive for any signal in
    ``_BLOCK_SIGNALS``. Returns list of matched signals (empty if none).
    Used by ``_gate_on_review`` to downgrade fabricated SHIP verdicts."""
    if not spec_body:
        return []
    haystack = spec_body.lower()
    return [sig for sig in _BLOCK_SIGNALS if sig.lower() in haystack]


def _resolve_scratchpad(ctx) -> Path:
    cfg = ctx.org_config or {}
    raw = cfg.get("scratchpad_dir")
    if not raw:
        raise ValueError("org_config.scratchpad_dir required for phase_45_spec_lite")
    return Path(raw).expanduser().resolve()


def _maybe_role_template(ctx) -> str:
    cfg = ctx.org_config or {}
    role_path = cfg.get("role_template_path")
    if not role_path:
        return ""
    rp = Path(role_path).expanduser()
    if not rp.is_file():
        return ""
    return rp.read_text(encoding="utf-8").rstrip() + "\n\n"


def _spec_cycle_relpath(cycle: int) -> str:
    """Per-cycle spec rewrite path. Cycle 1 keeps the canonical Phase-1
    output (this workflow does not rewrite cycle 1). Cycle ≥2 versions
    the rewrite so cycle-1 output is preserved for audit."""
    return f"specs/build-spec-cycle-{cycle}.md"


def _review_cycle_relpath(cycle: int) -> str:
    """Per-cycle review-doc path. Cycle 1 keeps the legacy single-shot
    filename so existing tooling/audit paths still resolve."""
    return REVIEW_DOC_RELPATH if cycle <= 1 else f"specs/build-plan-review-cycle-{cycle}.md"


def _review_output_schema() -> str:
    return (
        "OUTPUT — your response IS the file content of specs/build-plan-review.md.\n"
        "Start your response DIRECTLY with `## Verdict`. No preamble.\n"
        "\n"
        "REQUIRED sections:\n"
        "\n"
        "  ## Verdict\n"
        "  SHIP | REVISE\n"
        "\n"
        "  ## Findings (structured)\n"
        "  REQUIRED — emit on every review (even SHIP). Omitting this section\n"
        "  deactivates W1 cycle-2 restricted review and forces REVISE-cap on the\n"
        "  next iteration. If verdict is SHIP and no issues, emit `[]`.\n"
        "  ```json\n"
        "  [\n"
        '    {"id": "1", "type": "fabrication",\n'
        '     "evidence": "spec line 17 says \'exit code 3\' but FEATURE REQUEST does not authorize this",\n'
        '     "required_action": "move exit-code policy to Open Questions or cite FR text"}\n'
        "  ]\n"
        "  ```\n"
        "\n"
        "  ## Findings\n"
        "  - <specific issues if REVISE; `none` if SHIP>\n"
        "\n"
        "  ## Concerns Checked\n"
        "  - <bullets — gaps, contradictions, missing edge cases, impossible reqs,\n"
        "    fabricated features (added beyond FEATURE REQUEST), missing Constraints\n"
        "    or Out of Scope, vague Acceptance Criteria without Validation>\n"
        "\n"
        "  ## Rationale\n"
        "  <why this verdict>\n"
        "\n"
        "Do NOT approve by default — your job is to find gaps the spec author missed.\n"
        "Specifically scrutinize: features in the spec that are NOT in the FEATURE\n"
        "REQUEST (fabrication: --dry-run, --verbose, retry-on-error, run_id filtering,\n"
        "anything labelled `optional enhancement` or `low-cost high-value`); criteria\n"
        "without `Validation:` lines (untestable); empty or missing ## Out of Scope\n"
        "(no anti-scope discipline); spec length > 80 lines for a 1-3 file SIMPLE task\n"
        "(invented scope).\n"
        "\n"
        "Cross-check against engine source:\n"
        "- ctx-json keys: verify every key in any ctx-json invocation example actually\n"
        "  exists in the target engine source (parse_ctx schema, StepContract field\n"
        "  names). Open the target workflow file to confirm.\n"
        "- Override keys: verify every override key against `_resolve_command` lookups\n"
        "  in the target workflow source — flag any key not present in that dispatch table.\n"
        "- org_config wrapper: check whether the engine boundary requires options wrapped\n"
        "  in org_config; invocation examples must include or exclude the wrapper\n"
        "  accordingly.\n"
        "- Invocation example consistency: if the spec shows multiple invocation examples,\n"
        "  they must not contradict each other (same key set to same value, same wrapper\n"
        "  structure). Flag any self-contradiction across examples.\n"
    )


# ─── Step 0-2: cycle-aware spec rewrite (no-op on cycle 1) ──────────────────


def _maybe_rewrite_simple_spec_prompt(ctx, _prev) -> StepResult:
    """Cycle 1: pass-through — Phase 1 already wrote the spec; just record cycle.
    Cycle ≥2: build SIMPLE spec prompt (reuse Phase 1's prompt) and append a
    REVISION section with reviewer findings.

    9ADF28E5 — line-count override (intentional asymmetry with phase_45_spec):
        SIMPLE specs are 30-80 lines by design (reviewer prompt line 235:
        "spec length > 80 lines for a 1-3 file SIMPLE task = invented scope").
        The FEATURE/COMPLEX completeness gate uses MIN_SPEC_LINES as a stub-
        detection floor. For SIMPLE, the same numeric floor would wrongly
        block legitimate short specs. 4196B1A2: SIMPLE-tier default of 0
        is now applied at the read site in _verify_spec_completeness
        (complexity-aware default), so this function no longer mutates
        ctx.org_config. Explicit caller-provided spec_min_lines still
        takes precedence at the read site.
    """
    cycle = 1
    findings: str | None = None
    # Two prev shapes:
    #   - dict (legacy / engine-retry initial_data): {"cycle": N, "findings": ...}
    #   - StepResult (LoopRunner iteration N≥2): prev = gate_on_review result,
    #     where data["cycle"] was incremented for this iteration.
    if isinstance(_prev, dict):
        cycle = int(_prev.get("cycle", 1))
        findings = _prev.get("findings")
    elif isinstance(_prev, StepResult) and isinstance(_prev.data, dict):
        cycle = int(_prev.data.get("cycle", 1))
        findings = _prev.data.get("findings")

    scratchpad = _resolve_scratchpad(ctx)
    spec_path = scratchpad / SIMPLE_SPEC_RELPATH

    if cycle <= 1:
        return StepResult(
            status="ok",
            data={
                "cycle": cycle,
                "spec_path": str(spec_path),
                "rewrite": False,
            },
            duration_ms=0,
            step_name="maybe_rewrite_simple_spec_prompt",
        )

    # Cycle ≥2: try restricted writer if prev cycle-1 review has structured JSON findings.
    # Only activates when the reviewer explicitly emitted ## Findings (structured) JSON block.
    # Free-text ## Findings (without JSON) → falls through to legacy free-rewrite path.
    prev_review_path = scratchpad / _review_cycle_relpath(cycle - 1)
    prev_review_text = ""
    if prev_review_path.exists():
        try:
            prev_review_text = prev_review_path.read_text(encoding="utf-8")
        except OSError:
            prev_review_text = ""
    # Use structured findings only — gated on JSON block presence.
    structured_findings = extract_structured_findings(prev_review_text) or []

    if structured_findings:
        # Restricted mode: only address flagged items, no new scope.
        try:
            spec_text = spec_path.read_text(encoding="utf-8")
        except OSError:
            spec_text = ""
        prompt = _restricted_writer_prompt(
            spec=spec_text, findings=structured_findings,
            verbatim_reviewer_context=prev_review_text,
        )
        return StepResult(
            status="ok",
            data={
                "cycle": cycle,
                "spec_path": str(spec_path),
                "rewrite": True,
                "prompt": prompt,
                "prompt_bytes": len(prompt.encode("utf-8")),
                "findings": findings or "",
                "restricted_writer": True,
                "findings_count": len(structured_findings),
            },
            duration_ms=0,
            step_name="maybe_rewrite_simple_spec_prompt",
        )

    # Fallback: free-rewrite path (prev review had no structured findings block).
    base_prompt = _build_phase1_prompt(ctx, scratchpad, "SIMPLE")
    parts = [base_prompt, ""]
    parts.append(f"## REVISION (cycle {cycle} — address reviewer findings)")
    parts.append("")
    parts.append(findings or "(no findings text captured)")
    parts.append("")
    parts.append(
        "RULES FOR THIS REVISION:\n"
        "  - Address every concern listed under ## Findings above.\n"
        "  - Do NOT widen scope. If a finding asks for clarification, write\n"
        "    `## Open Questions` instead of inventing an answer.\n"
        "  - Do NOT add features the FEATURE REQUEST does not explicitly name.\n"
        "  - Output rules unchanged: response IS the spec file; start with\n"
        "    `## Context`; SpecKit-style sections only.\n"
    )
    prompt = "\n".join(parts)
    return StepResult(
        status="ok",
        data={
            "cycle": cycle,
            "spec_path": str(spec_path),
            "rewrite": True,
            "prompt": prompt,
            "prompt_bytes": len(prompt.encode("utf-8")),
            "findings": findings or "",
        },
        duration_ms=0,
        step_name="maybe_rewrite_simple_spec_prompt",
    )


def _maybe_invoke_spec_rewrite(ctx, prev) -> StepResult:
    if not isinstance(prev, StepResult) or not isinstance(prev.data, dict):
        return StepResult(
            status="error", data=None, duration_ms=0,
            step_name="maybe_invoke_spec_rewrite",
            error="prev step did not produce data",
            error_code="E_MISSING_PREV_DATA",
        )
    # E6602155: frozen spec fast-path — spec already ingested by step-0; skip rewrite.
    if prev.data.get("is_frozen"):
        return StepResult(
            status="skip",
            data={**prev.data},
            duration_ms=0,
            step_name="maybe_invoke_spec_rewrite",
        )
    if not prev.data.get("rewrite"):
        # Cycle 1 — short-circuit; preserve cycle/spec_path for downstream.
        return StepResult(
            status="ok",
            data={
                "cycle": prev.data["cycle"],
                "spec_path": prev.data["spec_path"],
                "rewrite": False,
            },
            duration_ms=0,
            step_name="maybe_invoke_spec_rewrite",
        )
    cfg = ctx.org_config or {}
    sub = invoke_llm_subprocess(
        prompt=prev.data["prompt"],
        model=_resolve_model(cfg, "spec_model", _default_spec_model()),
        timeout_sec=_resolve_spec_timeout_sec(cfg),
        step_name="maybe_invoke_spec_rewrite",
        extra_data={
            "cycle": prev.data["cycle"],
            "spec_path": prev.data["spec_path"],
            "rewrite": True,
        },
        allowed_tools=["Read", "Write", "Glob"],
    )
    return sub


def _maybe_write_spec_rewrite(_ctx, prev) -> StepResult:
    if not isinstance(prev, StepResult) or not isinstance(prev.data, dict):
        return StepResult(
            status="error", data=None, duration_ms=0,
            step_name="maybe_write_spec_rewrite",
            error="prev step did not produce data",
            error_code="E_MISSING_PREV_DATA",
        )
    if not prev.data.get("rewrite"):
        return StepResult(
            status="ok",
            data={
                "cycle": prev.data["cycle"],
                "spec_path": prev.data["spec_path"],
                "rewrite": False,
            },
            duration_ms=0,
            step_name="maybe_write_spec_rewrite",
        )
    raw = prev.data["raw_response"]
    canonical = Path(prev.data["spec_path"])
    cycle = int(prev.data["cycle"])
    cycle_path = canonical.parent / Path(_spec_cycle_relpath(cycle)).name
    canonical.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(cycle_path, raw)
    atomic_write(canonical, raw)  # HIGH #4: atomic write prevents stale canonical on kill
    return StepResult(
        status="ok",
        data={
            "cycle": cycle,
            "spec_path": str(canonical),
            "spec_cycle_path": str(cycle_path),
            "rewrite": True,
            "spec_bytes_written": len(raw.encode("utf-8")),
        },
        duration_ms=0,
        step_name="maybe_write_spec_rewrite",
    )


# ─── Step 3-5: build → invoke → write review ────────────────────────────────


def _build_review_prompt(ctx, prev) -> StepResult:
    if not isinstance(prev, StepResult) or not isinstance(prev.data, dict):
        return StepResult(
            status="error", data=None, duration_ms=0,
            step_name="build_review_prompt",
            error="prev step did not produce data",
            error_code="E_MISSING_PREV_DATA",
        )
    cycle = int(prev.data.get("cycle", 1))
    scratchpad = _resolve_scratchpad(ctx)
    spec_path = Path(prev.data["spec_path"])
    review_path = scratchpad / _review_cycle_relpath(cycle)

    if cycle >= 2:
        # Restricted reviewer: only audits cycle-1 findings for RESOLVED/UNRESOLVED.
        prev_review_path = scratchpad / _review_cycle_relpath(cycle - 1)
        prev_review_text = ""
        if prev_review_path.exists():
            try:
                prev_review_text = prev_review_path.read_text(encoding="utf-8")
            except OSError:
                prev_review_text = ""
        # Gate on JSON block presence — free-text ## Findings falls through.
        structured_findings = extract_structured_findings(prev_review_text) or []

        if structured_findings:
            try:
                spec_text = spec_path.read_text(encoding="utf-8")
            except OSError:
                spec_text = ""
            prompt = _restricted_reviewer_prompt(structured_findings, spec_text)
            return StepResult(
                status="ok",
                data={
                    "prompt": prompt,
                    "doc_path": str(review_path),
                    "spec_path": str(spec_path),
                    "cycle": cycle,
                    "prompt_bytes": len(prompt.encode("utf-8")),
                    "restricted_reviewer": True,
                },
                duration_ms=0,
                step_name="build_review_prompt",
            )
        # Fallback to free-form if prev review had no structured findings.

    parts: list[str] = []
    role = _maybe_role_template(ctx)
    if role:
        parts.append(role.rstrip())
        parts.append("")
    parts.append(_read_first_block(scratchpad))
    parts.append("")
    parts.append(
        "ROLE: You are a SIMPLE-tier spec reviewer. The spec writer is a separate "
        "agent. Find fabrication, missing Out of Scope discipline, criteria without "
        "Validation lines, and length-bloat (SIMPLE specs for 1-3 file tasks should "
        "be 30-80 lines). Do NOT approve by default."
    )
    parts.append("")
    parts.append("FEATURE REQUEST (compare spec against this — fabrication = features in spec but not here):")
    parts.append(ctx.question or "(no feature request provided)")
    parts.append("")
    parts.append(f"SPEC TO REVIEW (read this file): {spec_path}")
    parts.append("")
    parts.append(_review_output_schema())

    prompt = "\n".join(parts)
    return StepResult(
        status="ok",
        data={
            "prompt": prompt,
            "doc_path": str(review_path),
            "spec_path": str(spec_path),
            "cycle": cycle,
            "prompt_bytes": len(prompt.encode("utf-8")),
        },
        duration_ms=0,
        step_name="build_review_prompt",
    )


def _invoke_review_llm(ctx, prev) -> StepResult:
    if not isinstance(prev, StepResult) or not isinstance(prev.data, dict):
        return StepResult(
            status="error", data=None, duration_ms=0,
            step_name="invoke_review_llm",
            error="prev step did not produce a prompt",
            error_code="E_MISSING_PREV_DATA",
        )
    cfg = ctx.org_config or {}
    return invoke_llm_subprocess(
        prompt=prev.data["prompt"],
        model=_resolve_review_model(cfg),
        timeout_sec=_resolve_review_timeout_sec(cfg),
        step_name="invoke_review_llm",
        extra_data={
            "doc_path": prev.data["doc_path"],
            "spec_path": prev.data["spec_path"],
            "cycle": prev.data["cycle"],
        },
        hard_gate=True,
        gate_label="spec-lite-review",
        allowed_tools=["Read"],
    )


def _parse_verdict(raw: str) -> str:
    """Parse SHIP/REVISE from the canonical ## Verdict section header.

    Routes through lib/verdict_parse.py P3 (EEFD480F) — single-source of truth.
    Only the first token directly under a '## Verdict' heading is considered.
    PASS/APPROVED aliases map to SHIP. UNKNOWN if no matching header — treated
    as REVISE by gate_on_review (fail-closed).
    """
    return verdict_under_heading(
        raw,
        ("SHIP", "PASS", "APPROVED", "REVISE"),
        aliases={"PASS": VERDICT_SHIP, "APPROVED": VERDICT_SHIP},
        fallback=VERDICT_UNKNOWN,
    )


def _repair_finding_citation(
    finding: dict,
    file_cache: dict,
    cwd,
) -> tuple[bool, dict | None]:
    """Repair an off-by-N line citation in `finding["evidence"]`.

    Scans evidence for the first `> path:lineno: quote` pattern. If the
    cited line content does not match the quoted text BUT an exact match
    exists within ±_CITATION_WINDOW_LINES (excluding the cited line),
    rewrites the line number in-place and returns (True, info_dict).
    Returns (False, None) when the citation is already correct, no
    citation present, the file is unreadable, or no match within window.

    Mutates `finding["evidence"]` in-place ONLY when repair succeeds.

    Sibling: phase_6_review._verify_finding_quote (A37D4F04). This is
    Tier-B exact-in-window only — no normalized / substring tiers.
    """
    evidence = finding.get("evidence", "")
    m = _CITATION_LINE_RE.search(evidence)
    if not m:
        return False, None

    raw_path = m.group(1).strip()
    lineno_str = m.group(2)
    quoted_raw = m.group(3)

    # Option-C normalization (identical to phase_6_review.py:1269):
    # strip exactly one leading separator-space if present.
    quoted = quoted_raw[1:] if quoted_raw.startswith(" ") else quoted_raw

    try:
        lineno = int(lineno_str)
    except ValueError:
        return False, None

    # Resolve path: absolute as-is; relative under cwd.
    path = Path(raw_path)
    if not path.is_absolute():
        path = Path(cwd) / raw_path
    if not path.exists() or not path.is_file():
        return False, None

    cache_key = str(path)
    if cache_key not in file_cache:
        try:
            file_cache[cache_key] = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            file_cache[cache_key] = None
    lines = file_cache[cache_key]
    if lines is None:
        return False, None
    if lineno < 1 or lineno > len(lines):
        return False, None

    target = quoted.rstrip("\n\r")

    # Tier-A: exact rstrip equality at cited line → already correct, no-op.
    if lines[lineno - 1].rstrip() == target:
        return False, None

    # Tier-B: exact rstrip equality in ±_CITATION_WINDOW_LINES window
    # (excluding the cited line itself). First match wins.
    window_start = max(1, lineno - _CITATION_WINDOW_LINES)
    window_end = min(len(lines), lineno + _CITATION_WINDOW_LINES)
    for i in range(window_start, window_end + 1):
        if i == lineno:
            continue
        if lines[i - 1].rstrip() == target:
            delta = i - lineno
            # Build the corrected citation line (preserve quoted_raw verbatim,
            # including any leading separator-space). Use str.replace with
            # count=1 to avoid re.sub replacement-special-char hazards
            # (Opus A3 — quoted_raw could contain \1, \g<...>, etc.).
            old_line = m.group(0)
            new_line = f"> {raw_path}:{i}:{quoted_raw}"
            finding["evidence"] = evidence.replace(old_line, new_line, 1)
            return True, {
                "original_lineno": lineno,
                "new_lineno": i,
                "delta": delta,
                "path": str(path),
                "match_tier": "B",
            }

    return False, None


def _auto_correct_citations(
    findings: list,
    spec_path: str | None,
) -> set:
    """Run citation auto-correct on a list of Finding TypedDicts.

    Iterates findings, calling `_repair_finding_citation` per finding.
    For each repair, emits a `spec_lite_citation_auto_corrected`
    telemetry event and adds the finding's `id` to the returned set.
    Mutates findings' `evidence` in-place when repair succeeds.

    Returns set of finding ids that were auto-corrected. Verdict
    counting in `_write_review_doc` filters these from the REVISE-count
    so cycle-1 SHIPs when only citation-only mismatches were present.
    """
    cwd = Path(spec_path).parent if spec_path else Path.cwd()
    file_cache: dict = {}
    auto_corrected_ids: set = set()
    for f in findings:
        fid = f.get("id")
        if not fid:
            continue
        repaired, info = _repair_finding_citation(f, file_cache, cwd)
        if repaired:
            assert info is not None
            auto_corrected_ids.add(fid)
            _emit_safe(
                "spec_lite_citation_auto_corrected",
                {
                    "phase": "phase_45_spec_lite",
                    "finding_id": fid,
                    "original_lineno": info["original_lineno"],
                    "new_lineno": info["new_lineno"],
                    "delta": info["delta"],
                    "path": info["path"],
                    "match_tier": info["match_tier"],
                },
            )
    return auto_corrected_ids


def _write_review_doc(_ctx, prev) -> StepResult:
    if not isinstance(prev, StepResult) or not isinstance(prev.data, dict):
        return StepResult(
            status="error", data=None, duration_ms=0,
            step_name="write_review_doc",
            error="prev step did not produce raw_response",
            error_code="E_MISSING_PREV_DATA",
        )
    raw = prev.data["raw_response"]
    review_path = Path(prev.data["doc_path"])
    cycle = int(prev.data.get("cycle", 1))
    structured_verdict_for_gate: str | None = None
    review_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(review_path, raw)  # HIGH #4: atomic write prevents stale canonical on kill

    _emit_safe(
        "phase_45_spec_writer_complete",
        {
            "phase": "phase_45_spec_lite",
            "cycle": cycle,
            "response_bytes": len(raw.encode("utf-8")),
            "duration_ms": prev.duration_ms or 0,
        },
    )

    if cycle >= 2:
        # Restricted-mode: try per-finding parser first.
        # Falls back to classic ## Verdict parser when reviewer was free-form
        # (i.e. no structured findings were available for cycle-1, so the
        # cycle-2 reviewer prompt was also free-form and won't have FINDING_ lines).
        parsed = parse_per_finding_verdicts(raw)
        fv = parsed["final_verdict"]
        if fv != "UNPARSED":
            # Restricted reviewer output — map to engine verdicts.
            if fv == "PASS":
                verdict = VERDICT_SHIP
            else:  # REVISE
                verdict = VERDICT_REVISE
            _rc2_structured = extract_structured_findings(raw)
            _rc2_n_structured = len(_rc2_structured) if _rc2_structured is not None else 0
            _rc2_n_total = _count_freetext_findings(raw) + _rc2_n_structured
            _emit_safe(
                "phase_45_spec_review_complete",
                {
                    "phase": "phase_45_spec_lite",
                    "cycle": cycle,
                    "verdict": verdict,
                    "n_findings_total": _rc2_n_total,
                    "n_findings_structured": _rc2_n_structured,
                    "duration_ms": prev.duration_ms or 0,
                },
            )
            return StepResult(
                status="ok",
                data={
                    "review_path": str(review_path),
                    "spec_path": prev.data["spec_path"],
                    "review_bytes_written": len(raw.encode("utf-8")),
                    "verdict": verdict,
                    "cycle": cycle,
                    "review_raw": raw,
                    "per_finding": parsed["per_finding"],
                    "n_resolved": parsed["n_resolved"],
                    "n_total": parsed["n_total"],
                },
                duration_ms=0,
                step_name="write_review_doc",
            )
        # UNPARSED: no per-finding lines → reviewer was free-form → fall through
        # to classic ## Verdict parser (backward-compat).

    # α₀ BA456198: findings_block_compliance telemetry (cycle-1 only).
    if cycle <= 1:
        structured_for_telemetry = extract_structured_findings(raw)  # may be None
        freetext_findings_count = _count_freetext_findings(raw)
        _emit_safe(
            "findings_block_compliance",
            {
                "phase": "phase_45_spec_lite",
                "cycle": 1,
                "json_block_present": structured_for_telemetry is not None,
                "json_findings_count": (
                    len(structured_for_telemetry) if structured_for_telemetry is not None else None
                ),
                "freetext_findings_count": freetext_findings_count,
            },
        )

    # Step 5: structured-findings telemetry (additive; markdown still authoritative).
    if cycle <= 1:
        structured_findings = extract_structured_findings(raw)
        md_verdict = _parse_verdict(raw)  # may run twice; pure function, cheap
        if structured_findings is None:
            _emit_safe(
                "spec_lite_findings_block_absent",
                {"markdown_verdict": md_verdict, "cycle": 1, "phase": "phase_45_spec_lite"},
            )
        else:
            # 32ED4070: citation auto-correct (F88217EB-P3). Run BEFORE verdict
            # counting so cycle-1 SHIPs when only citation-only mismatches are
            # present. cycle <= 1 only (spec § OOS §4).
            auto_corrected_ids = _auto_correct_citations(
                structured_findings, prev.data.get("spec_path")
            )
            n_total = len(structured_findings)
            n = sum(
                1
                for f in structured_findings
                if f.get("id") not in auto_corrected_ids
            )
            structured_verdict = VERDICT_SHIP if n == 0 else VERDICT_REVISE
            structured_verdict_for_gate = structured_verdict
            _emit_safe(
                "spec_lite_structured_verdict",
                {
                    "markdown_verdict": md_verdict,
                    "structured_verdict": structured_verdict,
                    "n_findings": n,
                    "n_findings_total": n_total,
                    "n_findings_auto_corrected": n_total - n,
                    "cycle": 1,
                    "phase": "phase_45_spec_lite",
                },
            )
            # Drift only when both verdicts in {SHIP, REVISE} and they disagree.
            if md_verdict in (VERDICT_SHIP, VERDICT_REVISE) and md_verdict != structured_verdict:
                _emit_safe(
                    "spec_lite_verdict_drift",
                    {
                        "markdown_verdict": md_verdict,
                        "structured_verdict": structured_verdict,
                        "n_findings": n,
                        "cycle": 1,
                        "phase": "phase_45_spec_lite",
                    },
                )

    # Cycle 1 (or cycle ≥ 2 fallback when reviewer was free-form):
    # use classic ## Verdict header parser.
    if cycle <= 1 and structured_verdict_for_gate is not None:
        verdict = structured_verdict_for_gate
    else:
        verdict = _parse_verdict(raw)
    _final_structured = extract_structured_findings(raw)
    _final_n_structured = len(_final_structured) if _final_structured is not None else 0
    _final_n_total = _count_freetext_findings(raw) + _final_n_structured
    _emit_safe(
        "phase_45_spec_review_complete",
        {
            "phase": "phase_45_spec_lite",
            "cycle": cycle,
            "verdict": verdict,
            "n_findings_total": _final_n_total,
            "n_findings_structured": _final_n_structured,
            "duration_ms": prev.duration_ms or 0,
        },
    )
    return StepResult(
        status="ok",
        data={
            "review_path": str(review_path),
            "spec_path": prev.data["spec_path"],
            "review_bytes_written": len(raw.encode("utf-8")),
            "verdict": verdict,
            "cycle": cycle,
            "review_raw": raw,
        },
        duration_ms=0,
        step_name="write_review_doc",
    )


# ─── Step 6: HARD GATE on review verdict ────────────────────────────────────


def _gate_on_review(_ctx, prev) -> StepResult:
    """HARD GATE — REVISE on cycle < cap → status=ok with verdict=REVISE
    and cycle incremented for next iteration (LoopRunner drives the cycle
    via marker_field=verdict). REVISE on cycle == cap → E_REVIEW_FAILED
    recoverable=False (terminal abort). SHIP → ok.

    UNKNOWN treated as REVISE — never silently approve.
    """
    if not isinstance(prev, StepResult) or not isinstance(prev.data, dict):
        return StepResult(
            status="error", data=None, duration_ms=0,
            step_name="gate_on_review",
            error="prev step did not produce a verdict",
            error_code="E_MISSING_PREV_DATA",
        )
    verdict = prev.data.get("verdict", VERDICT_UNKNOWN)
    cycle = int(prev.data.get("cycle", 1))

    if verdict == VERDICT_SHIP:
        # 39CAFF09: severity classifier — even when the reviewer marker is
        # SHIP, inspect the underlying spec for block signals. Reviewers can
        # fabricate SHIP on a STATUS=BLOCK spec (observed 2026-05-01); gate
        # must not honor that verbatim. Fail-open on read errors — defensive
        # check on top of the reviewer.
        spec_path_raw = prev.data.get("spec_path")
        signals: list[str] = []
        if spec_path_raw:
            try:
                body = Path(spec_path_raw).read_text(encoding="utf-8")
                signals = _detect_block_signals(body)
            except OSError as exc:
                # spec missing/unreadable → log debug, treat as no signals.
                logger.debug(
                    "phase_45_spec_lite: cannot read spec for block-signal check (%s): %s",
                    spec_path_raw,
                    exc,
                )
                signals = []
        out_data: dict = {
            "verdict": verdict,
            "review_path": prev.data["review_path"],
            "spec_path": prev.data["spec_path"],
            "cycle": cycle,
        }
        if signals:
            out_data["verdict"] = VERDICT_SHIP_WITH_CONCERNS
            out_data["concern"] = (
                f"spec body contains block signals: {', '.join(signals)}"
            )
        _ship_raw = prev.data.get("review_raw", "") or ""
        _ship_structured = extract_structured_findings(_ship_raw)
        _ship_n_at_ship = len(_ship_structured) if _ship_structured is not None else 0
        _emit_safe(
            "phase_45_spec_ship",
            {
                "phase": "phase_45_spec_lite",
                "cycle": cycle,
                "n_findings_at_ship": _ship_n_at_ship,
            },
        )
        _allowlist_cfg = getattr(_ctx, "org_config", None) or {}
        _allowlist_outcome = write_run_allowlist_for_spec(_allowlist_cfg, prev.data["spec_path"])
        _allowlist_status = _allowlist_outcome.get("status")
        _allowlist_event = {
            "written": "run_allowlist_written",
            "skipped": "run_allowlist_skipped",
            "error": "run_allowlist_write_failed",
        }.get(_allowlist_status or "error", "run_allowlist_write_failed")
        _emit_safe(
            _allowlist_event,
            {
                "run_id": _allowlist_cfg.get("run_id"),
                "n_entries": _allowlist_outcome.get("n_entries"),
                "reason": _allowlist_outcome.get("reason"),
            },
        )
        return StepResult(
            status="ok",
            data=out_data,
            duration_ms=0,
            step_name="gate_on_review",
        )

    # HIGH #3: UNKNOWN + empty/whitespace review → terminal E_REVIEW_UNPARSEABLE.
    # Reviewer crashed or produced blank output — retrying is pointless.
    raw_review = prev.data.get("review_raw", "") or ""
    if verdict == VERDICT_UNKNOWN and not raw_review.strip():
        _emit_safe(
            "phase_45_spec_abort",
            {
                "phase": "phase_45_spec_lite",
                "cap_reached": False,
                "last_verdict": verdict,
                "terminal_reason": "unparseable",
            },
        )
        return StepResult(
            status="error",
            data={
                "verdict": verdict,
                "review_path": prev.data["review_path"],
                "cycle": cycle,
            },
            duration_ms=0,
            step_name="gate_on_review",
            error="reviewer returned empty output — cannot parse verdict (terminal)",
            error_code="E_REVIEW_UNPARSEABLE",
            recoverable=False,
        )

    # HIGH #2: cap findings before threading to next cycle to prevent token-budget breach.
    findings, was_truncated = _truncate_findings(raw_review)
    if cycle < MAX_REVIEW_CYCLES:
        # LoopRunner now drives iteration. Return ok with verdict (so
        # marker_field='verdict' check sees REVISE and continues) and
        # cycle incremented for next iteration's step 0.
        _revise_structured = extract_structured_findings(findings)
        _revise_n_unresolved = len(_revise_structured) if _revise_structured is not None else 0
        _emit_safe(
            "phase_45_spec_revise",
            {
                "phase": "phase_45_spec_lite",
                "cycle": cycle,
                "n_findings_unresolved": _revise_n_unresolved,
            },
        )
        data: dict = {
            "verdict": verdict,
            "review_path": prev.data["review_path"],
            "spec_path": prev.data.get("spec_path"),
            "cycle": cycle + 1,
            "findings": findings,
        }
        if was_truncated:
            data["findings_truncated"] = True
            data["findings_original_bytes"] = len(raw_review.encode("utf-8"))
        return StepResult(
            status="ok",
            data=data,
            duration_ms=0,
            step_name="gate_on_review",
        )

    # 439A6481: cycle-2 cap-exceeded telemetry. Emit BEFORE returning so
    # post-build telemetry can spot this failure class without re-parsing
    # error_code paths. last_verdict is the raw reviewer verdict (REVISE
    # or UNKNOWN) — never coerced. spec_path locked into payload so
    # downstream tooling can locate the offending spec without re-deriving
    # from run_id.
    # 03192214 site 2 (error) + site 3 (telemetry): boundary_error wiring.
    # Note: error site uses producer=review_cycle (the cycle that produced the verdict),
    # while telemetry site uses producer=gate_on_review (the function checking the verdict).
    boundary_error_for_error = format_boundary_error(
        phase="phase_45_spec_lite",
        field="review_verdict",
        producer="phase_45_spec_lite.review_cycle",
        where="phase_45_spec_lite.py",
        schema="StepResult.data.verdict",
    )
    boundary_error_for_telemetry = format_boundary_error(
        phase="phase_45_spec_lite",
        field="review_verdict",
        producer="phase_45_spec_lite.gate_on_review",
        where="phase_45_spec_lite.py",
        schema="StepResult.data.verdict",
    )
    _emit_safe(
        "spec_lite_cycle2_abort",
        {
            "cycle_no": cycle,
            "last_verdict": verdict,
            "spec_path": prev.data.get("spec_path", ""),
            "boundary_context": boundary_error_for_telemetry,
        },
    )
    _emit_safe(
        "phase_45_spec_abort",
        {
            "phase": "phase_45_spec_lite",
            "cap_reached": True,
            "last_verdict": verdict,
            "terminal_reason": "cap_reached",
        },
    )
    return StepResult(
        status="error",
        data={
            "verdict": verdict,
            "review_path": prev.data["review_path"],
            "cycle_count": cycle,
        },
        duration_ms=0,
        step_name="gate_on_review",
        error=f"SIMPLE spec REVISE verdict on cycle {cycle} (cap reached) — terminal abort | {boundary_error_for_error}",
        error_code="E_REVIEW_FAILED",
        recoverable=False,
    )


# ─── E6602155: frozen-spec fast-path helpers (lite) ──────────────────────────

_FROZEN_THREADING_KEYS_LITE = ("is_frozen", "frozen_doc_path", "frozen_fallback", "spec_path", "cycle")


def _fwd_frozen_lite(prev_data: dict, extra: dict | None = None) -> dict:
    """Thread frozen-spec keys from prev_data into the next step (lite variant)."""
    out: dict = {}
    if extra:
        out.update(extra)
    for k in _FROZEN_THREADING_KEYS_LITE:
        if k in prev_data and k not in out:
            out[k] = prev_data[k]
    return out


def _step_detect_frozen_spec_lite(ctx, prev) -> StepResult:
    """Step-0 (lite): detect frozen spec and ingest into SIMPLE_SPEC_RELPATH."""
    cfg = ctx.org_config or {}
    # Accept both StepResult.data dicts AND raw dicts (engine threads initial_data
    # as a plain dict on retry, not wrapped in StepResult).
    prev_data = (
        prev.data if isinstance(prev, StepResult) and isinstance(prev.data, dict)
        else (prev if isinstance(prev, dict) else {})
    )
    # Read retry-threaded values so downstream steps see the real cycle/findings on
    # retry, not a hardcoded 1 that would cause an infinite retry loop.
    incoming_cycle = int(prev_data.get("cycle", 1))
    frozen_fallback = bool(prev_data.get("frozen_fallback", False))

    is_frozen_raw, frozen_path = detect_frozen_spec(cfg.get("decision_doc"))
    effective_frozen = is_frozen_raw and not frozen_fallback

    base_data: dict = {
        **{k: v for k, v in prev_data.items() if k not in _FROZEN_THREADING_KEYS_LITE},
        "is_frozen": effective_frozen,
        "frozen_doc_path": str(frozen_path) if frozen_path is not None else None,
        "frozen_fallback": frozen_fallback,
        "cycle": incoming_cycle,
        "findings": prev_data.get("findings", ""),
    }

    if effective_frozen and frozen_path is not None:
        scratchpad = _resolve_scratchpad(ctx)
        spec_dest = scratchpad / SIMPLE_SPEC_RELPATH
        spec_dest.parent.mkdir(parents=True, exist_ok=True)
        frozen_bytes = frozen_path.read_bytes()
        spec_dest.write_bytes(frozen_bytes)
        _emit_safe("frozen_spec_ingested", {
            "doc_path": str(frozen_path),
            "ac_table": True,
            "preflight": True,
        })
        base_data["spec_path"] = str(spec_dest)
        base_data["doc_path"] = str(spec_dest)

    return StepResult(
        status="ok",
        data=base_data,
        duration_ms=0,
        step_name="detect_frozen_spec",
    )


def build_review_loop_contract() -> LoopStepContract:
    """350956CC: declarative review-cycle loop replaces the engine
    E_VALIDATION_RETRY hand-rolled retry. Body = the 7 existing steps in
    declared order; LoopRunner drives iteration via marker_field='verdict'
    + until_marker=SHIP, capped at MAX_REVIEW_CYCLES."""
    # GH641: consume_recoverable_retry=True makes this composite the SOLE
    # cycle driver — a body-step recoverable retry (verify_spec_completeness)
    # is consumed as an internal LoopRunner iteration instead of escalating
    # to the outer WorkflowEngine._execute_steps, which previously stacked a
    # second cycle counter and froze `cycle` via a stale-sentinel re-read
    # (spec 2026-07-12_46173687_gh641_lite_double_cycle_spec.md §0/§2).
    # resume_sentinel=True on the two LLM-backed steps is the GH374/GH641
    # durable-resume deliverable, safe now that the double-cycle is removed.
    return LoopStepContract(
        name="phase_45_review_cycle",
        body=[
            StepContract(name="maybe_rewrite_simple_spec_prompt", execute=_maybe_rewrite_simple_spec_prompt),
            StepContract(name="maybe_invoke_spec_rewrite", execute=_maybe_invoke_spec_rewrite, resume_sentinel=True),
            StepContract(name="maybe_write_spec_rewrite", execute=_maybe_write_spec_rewrite),
            StepContract(name="verify_spec_completeness", execute=_verify_spec_completeness),
            StepContract(name="verify_spec_cite_prelint", execute=_verify_spec_cite_prelint),
            StepContract(name="verify_spec_lint", execute=_verify_spec_lint),
            StepContract(name="build_review_prompt", execute=_build_review_prompt),
            StepContract(name="invoke_review_llm", execute=_invoke_review_llm, resume_sentinel=True),
            StepContract(name="write_review_doc", execute=_write_review_doc),
            StepContract(name="gate_on_review", execute=_gate_on_review),
        ],
        max_iterations=MAX_REVIEW_CYCLES,
        until_marker=VERDICT_SHIP,
        marker_field="verdict",
        consume_recoverable_retry=True,
    )


def _review_cycle_loop_execute(ctx, prev) -> StepResult:
    """Composite step: drive the review-cycle LoopStepContract via LoopRunner.
    Override step_name so observers see the composite name, not the last
    body step's name."""
    contract = build_review_loop_contract()
    result = LoopRunner(contract).run(ctx, prev)
    return replace(result, step_name="review_cycle_loop")


def phase_45_spec_lite_workflow() -> WorkflowDefinition:
    # detect_frozen_spec is a top-level step BEFORE the review cycle loop.
    # E6602155: AC10 finds it here (top-level probe first, then loop-body probe).
    return WorkflowDefinition(
        name="phase_45_spec_lite",
        steps=[
            StepContract(name="detect_frozen_spec", execute=_step_detect_frozen_spec_lite),
            StepContract(name="review_cycle_loop", execute=_review_cycle_loop_execute),
        ],
    )
