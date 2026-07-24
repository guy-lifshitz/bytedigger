"""Phase 4.5 (spec) as a WorkflowDefinition.

Stage 2.4 port (2026-04-25). Third LLM-heavy phase. First phase with TWO
LLM calls in one workflow: a spec writer (Opus) and an independent
Plan-Review reviewer (Opus). Uses the shared ``llm_subprocess`` helper.

**Engine-side retry loop (surface-15, 05F83B1B, 2026-04-28).** On REVISE
verdict, engine retries from step 0 with cycle counter incremented (max
MAX_REVIEW_CYCLES=2, matching spec_lite). Pattern mirrors phase_45_spec_lite:
  - gate_on_review step (step 6) fires E_VALIDATION_RETRY (recoverable=True)
    on REVISE with cycle < MAX_REVIEW_CYCLES.
  - Engine retry hook (engine.py Decree #1) recurses from step 0 with
    initial_data={"cycle": N+1, "findings": <raw review>}.
  - build_spec_prompt (step 0) detects cycle ≥2 via initial_data and appends
    a ## REVISION block with reviewer findings to the spec-writer prompt.
  - Cycle 2 REVISE yields E_REVIEW_FAILED (recoverable=False) — terminal abort.
  - UNKNOWN treated as REVISE (fail-closed).

Token-spend guards (same playbook as phase_1 / phase_4):
    - Prompts list READ_FIRST pointer paths only — never inline injection
      files. Spec writer additionally points to architecture/architecture.md
      (Phase 4 output) by path, not inline.
    - Reviewer prompt points to the just-written spec by path so the LLM
      reads it once on disk, not twice in two contexts.
    - Optional `role_template_path` for ~3KB role-reviewer instead of
      ~10KB CLAUDE.md (matches phase doc: Plan-Review uses
      omitProjectContext + role-reviewer template).
    - Default timeouts: 600s spec, 300s reviewer (review is read-only).

Inputs (via `ctx.org_config`):
    scratchpad_dir              — REQUIRED. Absolute path to scratchpad root.
    role_template_path          — Optional. Prepended to BOTH prompts.
    llm_command                 — Optional. Default: get_claude_spec_writer(). Global
                                  fallback for both subprocess calls.
    spec_llm_command            — Optional. Per-step override of llm_command.
    review_llm_command          — Optional. Per-step override of llm_command.
                                  Recommended: pin to Opus here so the harness
                                  cannot silently downgrade Plan-Review (separate
                                  agent from the spec writer is the whole point).
    spec_llm_timeout_sec        — Optional. Default 600.
    review_llm_timeout_sec      — Optional. Default 300.

`ctx.question` carries the user's feature request text.

Steps (7):
    0. build_spec_prompt        — deterministic; references arch doc by path;
                                  cycle ≥2 appends ## REVISION block with findings
    1. invoke_spec_llm          — opaque subprocess
    2. write_spec_doc           — cycle 1: write specs/build-spec.md;
                                  cycle ≥2: write versioned + overwrite canonical
    3. build_review_prompt      — deterministic; references just-written spec by path
    4. invoke_review_llm        — opaque subprocess (separate agent for plan-review)
    5. write_review_doc         — write specs/build-plan-review[-cycle-N].md
    6. gate_on_review           — HARD GATE: REVISE→retry or abort; SHIP→ok

Outputs:
    $SCRATCHPAD/specs/build-spec.md
    $SCRATCHPAD/specs/build-spec-cycle-2.md          (cycle 2 only)
    $SCRATCHPAD/specs/build-plan-review.md
    $SCRATCHPAD/specs/build-plan-review-cycle-2.md   (cycle 2 only)
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any, cast

logger = logging.getLogger(__name__)

from contracts import StepContract, StepResult, WorkflowContext, WorkflowDefinition, format_boundary_error
from config_provider import get_config, timeout_policy_path, repo_top_level_dirs  # noqa: E402  GH285 C2
from llm_subprocess import invoke_llm_subprocess
from skip_logic import _resolve_decision_doc_path, detect_frozen_spec
try:
    from ._standards_context import get_standards_context
except ImportError:  # pragma: no cover — bare fallback for sys.path-rooted test imports (GH881)
    from _standards_context import get_standards_context  # type: ignore[no-redef]

sys.path.insert(0, str(Path(__file__).parent.parent / "lib" / "plugins"))
sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))
import telemetry_ctx  # noqa: E402
from anti_hallucination.helper import (  # noqa: E402
    get_prompt_fragment as _get_anti_fab_prompt,
    get_behavioral_assertion_rubric as _get_behavioral_rubric,
    get_out_of_role_block as _get_out_of_role_block,
)
from io_utils import atomic_write  # noqa: E402
from findings_sidecar import persist_findings_thread, load_findings_thread  # noqa: E402  GH636
from model_config import get_claude_critical, get_claude_spec_writer, get_claude_spec_reviewer  # noqa: E402
from verdict_parse import verdict_under_heading  # noqa: E402
from recoverable_gate import RecoverableGateMixin  # noqa: E402  E843349F
try:
    from ._recoverable_policy import resolve_policy  # noqa: E402  E843349F
except ImportError:  # pragma: no cover — bare fallback for sys.path-rooted test imports (GH881)
    from _recoverable_policy import resolve_policy  # type: ignore[no-redef]  # noqa: E402  E843349F
from plugins.checklist_convergence import (  # noqa: E402
    build_reviewer_prompt as _restricted_reviewer_prompt,
    build_writer_prompt as _restricted_writer_prompt,
    extract_structured_findings,
    extract_findings_for_writer,
    parse_per_finding_verdicts,
)
from plugins.checklist_convergence.delta_retry_prompt import (  # noqa: E402  GH443
    build_delta_retry_prompt,
)
from plugins.checklist_convergence.surgical_revise import (  # noqa: E402  GH592
    apply_surgical_patches,
    build_surgical_revise_prompt,
    extract_surgical_patches,
)
from plugins.checklist_convergence.delta_reviewer_prompt import (  # noqa: E402  GH605
    extract_affected_sections,
    build_delta_reviewer_prompt,
)
from lib.project_root import resolve_project_root  # noqa: E402
from lib.git_cwd import resolve_git_cwd  # noqa: E402  GH381
from lib.directed_repair import (  # noqa: E402  457DC7DC GH371 §2.2
    attempt_directed_repair,
    _directed_repair_enabled,
    _resolve_directed_repair_model,
    _repair_cap,
)
from reject_log import record_plan_review_reject  # noqa: E402  EECA708D
from scope_inverse import scan_scope_inverse  # noqa: E402  EECB919C §1v
from reentry_ac import scan_reentry_ac, stateful_probe  # noqa: E402  GH823 §1ab/§1ac
from helper_extraction import scan_helper_extraction  # noqa: E402  GH863 §1aa
from spec_coverage import scan_spec_coverage, scan_error_taxonomy, scan_finalize_coverage  # noqa: E402  EECB919C §1w / GH824
from token_consistency import scan_token_consistency  # noqa: E402  GH559
from presence_triad import scan_presence_triad  # noqa: E402  GH559
from format_conversion_cases import scan_format_conversion  # noqa: E402  GH559
import ac_dsl  # noqa: E402  GH517 A2 — module-attr import so monkeypatch(ac_dsl, "admit", ...) works
from bounded_spawn import bounded_run  # noqa: E402
from lib.git_port import git_read  # noqa: E402
from lib.run_allowlist import write_run_allowlist_for_spec  # noqa: E402  1DA29C33
from lib.verdict_resolution import resolve_gate_verdict  # noqa: E402  GH373 Part B
from lib.spec_retry_cycle import (  # noqa: E402  CF480CAE SSOT-01
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
from lib.timeout_policy import DEFAULT_POLICY, cached_policy, resolve_timeout_sec  # noqa: E402  GH285 C2
from spec_cite import lint_spec  # noqa: E402  GH675A
from error_codes import ERROR_CODES  # noqa: E402  GH824


def _timeout_policy() -> dict[str, Any]:
    return cached_policy(str(timeout_policy_path()))


# The retry-cycle helpers now live in lib.spec_retry_cycle; only thin
# aliases remain below so existing call-sites and sibling tests that reference
# the private underscore names (e.g. phase_45_spec._read_first_block) still
# resolve without modification.
_truncate_findings = truncate_findings
_resolve_command = resolve_command
_resolve_model = resolve_model
_resolve_review_model = resolve_review_model
_read_first_block = read_first_block

def _default_spec_model() -> str:
    return cast(str, get_claude_spec_writer())


def _default_review_model() -> str:
    """GH597: model-mix — models.json spec_reviewer role, else legacy critical."""
    return cast(str, get_claude_spec_reviewer() or get_claude_critical())


def _default_spec_llm_command() -> list[str]:
    """Back-compat alias returning argv form."""
    from llm_subprocess import _build_claude_argv
    return _build_claude_argv(_default_spec_model())


def _default_review_llm_command() -> list[str]:
    """Back-compat alias returning argv form."""
    from llm_subprocess import _build_claude_argv
    return _build_claude_argv(_default_review_model())


# Backward-compat module-level constants (computed once at import via ModelConfig).
# Live runtime callers should use the _default_*() functions above to honor
# reset_cache() + on-disk edits to ~/.claude/SHARED/config/models.json.
DEFAULT_SPEC_LLM_COMMAND = _default_spec_llm_command()
DEFAULT_REVIEW_LLM_COMMAND = _default_review_llm_command()
DEFAULT_SPEC_TIMEOUT_SEC = DEFAULT_POLICY["spec.writer"]["base"]
DEFAULT_REVIEW_TIMEOUT_SEC = DEFAULT_POLICY["spec.reviewer"]["base"]


def _emit_safe(event_type: str, payload: dict[str, Any]) -> None:
    """Emit telemetry event via current run context; swallow all errors.
    Mirror of the helper in phase_45_spec_lite / phase_5_implement / skip_logic."""
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


def _resolve_unresolved_count(
    raw_review: str,
    n_total: object,
    n_resolved: object,
) -> tuple[int, str]:
    """Robust unresolved-findings count + provenance tag for reject capture.

    Returns (count: int, source: str) where source in {"structured","per_finding","freetext"}.
    Fallback chain (richest signal first), all over the UNTRUNCATED review:
      1. structured ## Findings (structured) json block present  -> (len, "structured")
      2. else per-finding tally available (n_total not None)     -> (max(0, n_total-n_resolved), "per_finding")
      3. else free-text numbered ## Findings list               -> (_count_freetext_findings(raw_review), "freetext")
    Pure, no I/O.
    """
    structured = extract_structured_findings(raw_review)
    if structured is not None:
        return (len(structured), "structured")
    if n_total is not None:
        total = int(n_total) if isinstance(n_total, (int, str)) else 0
        resolved = int(n_resolved) if isinstance(n_resolved, (int, str)) else 0
        return max(0, total - resolved), "per_finding"
    return (_count_freetext_findings(raw_review), "freetext")


# 80CC602D — complexity-aware timeouts. Baseline 600/300s too tight for COMPLEX
# with large decision_doc + multi-section spec (forge-1777986051-b6619743 cycle 2
# hit exact 300s SIGKILL on Opus reviewer). Same pattern as 9356C4D1 in
# phase_5_implement._resolve_green_watchdog_token_multiplier.
DEFAULT_SPEC_TIMEOUT_SEC_COMPLEX = DEFAULT_POLICY["spec.writer"]["COMPLEX"]   # 30 min for COMPLEX writer
DEFAULT_REVIEW_TIMEOUT_SEC_COMPLEX = DEFAULT_POLICY["spec.reviewer"]["COMPLEX"]  # 15 min for COMPLEX reviewer

# F88217EB Prong 1 — model-aware Opus floors (non-COMPLEX only).
# Opus reviewer SIGKILLed at 300s during FEATURE-class run forge-1778620958.
DEFAULT_SPEC_TIMEOUT_SEC_OPUS = DEFAULT_POLICY["spec.writer"]["opus"]    # floor when spec-writer is Opus, non-COMPLEX
DEFAULT_REVIEW_TIMEOUT_SEC_OPUS = DEFAULT_POLICY["spec.reviewer"]["opus"]  # floor when reviewer is Opus, non-COMPLEX
DEFAULT_REVIEW_TIMEOUT_SEC_FEATURE = DEFAULT_POLICY["spec.reviewer"]["FEATURE"]  # D1F51D7A — Sonnet-reviewer floor for FEATURE-class spec review


def _is_opus_class_model(model: str | list[Any] | None) -> bool:
    """Return True if model contains 'opus' (case-insensitive).

    25e75663: primary signature is now str|None (model name string).
    Retains back-compat with list[str] argv form (F88217EB callers):
      - list input: extracts --model value from the argv list, then checks.
      - str input: direct case-insensitive substring match on 'opus'.
      - None or empty list: returns False.

    List extraction handles both space form ('--model', 'opus') and
    equals form ('--model=opus').
    """
    if model is None:
        return False
    if isinstance(model, list):
        if not model:
            return False
        # Extract --model value from argv list
        model_str: str | None = None
        for i, tok in enumerate(model):
            if isinstance(tok, str) and tok.startswith("--model="):
                model_str = tok[len("--model="):]
                break
            if tok == "--model" and i + 1 < len(model):
                model_str = model[i + 1]
                break
        if not model_str:
            return False
        return "opus" in model_str.lower()
    # str path
    return bool(model) and "opus" in model.lower()


def _resolve_spec_timeout_sec(cfg: dict[str, Any] | None) -> int:
    """spec.writer timeout via unified policy (GH285 C2). Precedence:
    cfg.spec_llm_timeout_sec > COMPLEX > Opus-class spec writer > base."""
    cfg = cfg or {}
    # Check new str key first, then legacy list key (spec_llm_command, F88217EB back-compat).
    spec_model_val = (
        cfg.get("spec_model")
        or _resolve_model(cfg, "spec_model", None)
        or cfg.get("spec_llm_command")
    )
    return resolve_timeout_sec(
        "spec.writer", cfg, policy=_timeout_policy(), model_is_opus=_is_opus_class_model(spec_model_val)
    )


def _resolve_review_timeout_sec(cfg: dict[str, Any] | None) -> int:
    """spec.reviewer timeout via unified policy (GH285 C2). Precedence:
    cfg.review_llm_timeout_sec > COMPLEX > FEATURE > Opus-class reviewer > base."""
    cfg = cfg or {}
    # Check new str key first, then legacy list key (review_llm_command, F88217EB back-compat).
    review_model_val = (
        cfg.get("review_model")
        or _resolve_model(cfg, "review_model", None)
        or cfg.get("review_llm_command")
    )
    return resolve_timeout_sec(
        "spec.reviewer", cfg, policy=_timeout_policy(), model_is_opus=_is_opus_class_model(review_model_val)
    )

SPEC_DOC_RELPATH = "specs/build-spec.md"
REVIEW_DOC_RELPATH = "specs/build-plan-review.md"
ARCHITECTURE_DOC_RELPATH = "architecture/architecture.md"
# 36269734: sidecar for canary event_type; counterpart in phase_5_integration_canary.py
CANARY_META_RELPATH = "integration/canary-meta.json"

# Cap matches Phase 5 validation pattern and phase_45_spec_lite. Hard-coded
# for v1; configurable in v2 once telemetry data exists.
MAX_REVIEW_CYCLES = 2

# VERDICT_SHIP / VERDICT_REVISE / VERDICT_UNKNOWN imported from lib.spec_retry_cycle above.

# GH601: mirrors phase_5_implement._RE_PY_UUT_WANTS/_spec_wants_py_gates pattern
# for the spec-writer prompt (Blocks B/C, §2.3 of the prompt-parity spec).
_RE_SPEC_PY_WANTS = re.compile(r"\.py\b")
_RE_SPEC_FMT_WANTS = re.compile(r"format|migrat|legacy|storage|schema|jsonl?\b", re.IGNORECASE)

# ─── verify_spec_completeness constants (8A9C0F24) ───────────────────────────
# Real SpecKit COMPLEX spec: ~10 sections × ~10-15 lines ≈ 100-150 lines min.
# 80 chosen as conservative lower bound (≥80 = ok, <80 = stub/changelog).
MIN_SPEC_LINES = 80
REQUIRED_FIRST_SECTION = "## Context"

# 13361031: parse the A813CA08 "Citation grounding count" line (runtime digit form)
# for telemetry. Whitespace-tolerant; case-insensitive on grounded/ungrounded.
_GROUNDING_COUNT_RE = re.compile(
    r"Citation grounding count:\s*(\d+)\s*/\s*(\d+)\s+grounded\s*\(\s*(\d+)\s+ungrounded\s*\)",
    re.IGNORECASE,
)

def _spec_cycle_relpath(cycle: int) -> str:
    """Per-cycle spec path. Cycle 1 uses the canonical name. Cycle ≥2 is versioned
    (preserves cycle-1 output for audit), and also overwrites canonical."""
    return SPEC_DOC_RELPATH if cycle <= 1 else f"specs/build-spec-cycle-{cycle}.md"


def _review_cycle_relpath(cycle: int) -> str:
    """Per-cycle review-doc path. Cycle 1 keeps the legacy single-shot filename
    so existing tooling/audit paths still resolve."""
    return REVIEW_DOC_RELPATH if cycle <= 1 else f"specs/build-plan-review-cycle-{cycle}.md"


def _resolve_scratchpad(ctx: WorkflowContext) -> Path:
    cfg = ctx.org_config or {}
    raw = cfg.get("scratchpad_dir")
    if not raw:
        raise ValueError("org_config.scratchpad_dir required for phase_45_spec")
    return Path(raw).expanduser().resolve()


# Subtask C (8A9C0F24) — decision_doc text injection into spec-writer prompt.
# Cap chosen to keep the inlined block ≈20K tokens (head 60KB + tail 15KB +
# marker << 80KB) so the writer stays under model context budget. Path is
# always referenced so the writer can re-Read the unredacted file via Read.
DECISION_DOC_INLINE_BYTE_CAP = 80_000


def _read_decision_doc_block(cfg: dict[str, Any] | None) -> str:
    """Read cfg['decision_doc'] text (resolved via skip_logic) and return a
    formatted '## DECISION DOC' block for the spec-writer prompt. Returns ''
    when unset, missing, or unreadable so the prompt section is omitted entirely.

    Truncates files larger than DECISION_DOC_INLINE_BYTE_CAP using head + tail
    around a `... (truncated — read full file at <path> for omitted middle ...)`
    marker. The full resolved path is always referenced so the writer can
    re-Read the unredacted file.

    utf-8-sig encoding absorbs UTF-8 BOM (lesson from sibling agreement
    83140A09 in the same umbrella — same writer-pipeline class of bug).
    """
    if not cfg:
        logger.debug("decision_doc inline skipped (reason=%s)", "no_cfg")
        return ""
    raw = cfg.get("decision_doc") or ""
    if not raw:
        logger.debug("decision_doc inline skipped (reason=%s)", "unset")
        return ""
    resolved = _resolve_decision_doc_path(raw)
    if resolved is None:
        logger.debug("decision_doc inline skipped (reason=%s)", "unresolved")
        return ""
    try:
        text = resolved.read_text(encoding="utf-8-sig")
    except OSError as e:
        logger.debug(
            "decision_doc inline skipped (reason=%s, path=%s, err=%s)",
            "read_error",
            resolved,
            e,
        )
        return ""
    except UnicodeDecodeError:
        # F3 (8A9C0F24 Subtask C code review): UnicodeDecodeError is a
        # ValueError subclass, NOT an OSError — without this explicit except,
        # a binary blob accidentally pointed at via --decision-doc would crash
        # _build_spec_prompt. Silent skip = graceful degradation (no DECISION
        # DOC heading, no exception).
        logger.debug(
            "decision_doc inline skipped (reason=%s, path=%s)",
            "decode_error",
            resolved,
        )
        return ""
    body = text
    nbytes = len(text.encode("utf-8"))
    if nbytes > DECISION_DOC_INLINE_BYTE_CAP:
        # Head 60KB + tail 15KB with marker between; total well under cap.
        head_chars = 60_000
        tail_chars = 15_000
        if len(text) <= head_chars + tail_chars:
            # F2 (8A9C0F24 Subtask C code review): multi-byte content
            # (Cyrillic / CJK) can exceed the byte cap while char count
            # fits within head+tail without overlap. Slicing head=text[:60_000]
            # returns the WHOLE text; appending the truncation marker + a
            # tail re-slice of the SAME text would duplicate content with a
            # misleading "truncated" marker. Inline as-is instead.
            body = text
        else:
            head = text[:head_chars]
            tail = text[-tail_chars:]
            body = (
                f"{head}\n\n"
                f"... (truncated — {nbytes} bytes total; full file at {resolved} for omitted middle) ...\n\n"
                f"{tail}"
            )
    return (
        f"## DECISION DOC (full file at {resolved} — text inlined below)\n\n"
        f"{body}\n"
    )


def _maybe_role_template(ctx: WorkflowContext) -> str:
    cfg = ctx.org_config or {}
    role_path = cfg.get("role_template_path")
    if not role_path:
        return ""
    rp = Path(role_path).expanduser()
    if not rp.is_file():
        return ""
    return rp.read_text(encoding="utf-8").rstrip() + "\n\n"


def _spec_output_schema(doc_path: str) -> str:
    # Structure mirrors SpecKit-style specs (see specs/SmartRouterV2/,
    # specs/ContinuityFix/, SYSTEM/specs/PAI/speckit-integration.spec.md
    # for prior-art examples). Constraints + Out of Scope + Open Questions
    # make anti-fabrication structural — not a rule that gets ignored.
    # Acceptance Criteria with mandatory `Validation:` lines forces testable
    # invariants over invented test cases.
    return (
        f"OUTPUT — write the FULL spec body to EXACTLY this file path using the Write tool:\n"
        f"  {doc_path}\n"
        f"That file IS the spec deliverable. Your text response MUST be ONLY a ≤200-token status:\n"
        f"the path written + one-line confirmation. Do NOT echo the spec body in your text response.\n"
        f"Report the written path in worker_written_paths.\n"
        "\n"
        "Start the spec DIRECTLY with `## Context`. No preamble. No status\n"
        "markers (`STATUS:`, `DONE`, `Spec written`). No meta-commentary about\n"
        "what you discovered or decided. The file IS the spec; do not describe it.\n"
        "\n"
        "REQUIRED sections, in this exact order, no others:\n"
        "\n"
        "  ## Context\n"
        "  <2-4 sentences: what exists, what this work changes, why>\n"
        "\n"
        "  ## User Stories\n"
        "  Min 2 stories. BDD format. P1 alone = working MVP.\n"
        "  ### US1 - [Title] (P1 — MVP)\n"
        "    **Why P1**: ...\n"
        "    **Acceptance**: Given ..., When ..., Then ...\n"
        "\n"
        "  ## Files\n"
        "  CREATE: <path>\n  MODIFY: <path> (<change>)\n"
        "\n"
        "  ## Interfaces\n"
        "  <function signatures, types, return values>\n"
        "\n"
        "  ## Data Model\n"
        "  <entities with field names, types, relationships>\n"
        "\n"
        "  ## Behavior\n"
        "  <edge cases, error handling, validation — only what the request asks for>\n"
        "\n"
        "  ## Constraints\n"
        "  <what must NOT be changed in surrounding code; backward-compat rules>\n"
        "\n"
        "  ## Out of Scope\n"
        "  <features and behaviors explicitly NOT in this work — list the\n"
        "   plausible-but-unrequested additions a reader might assume>\n"
        "\n"
        "  ## Acceptance Criteria\n"
        "  <numbered list of testable invariants. Each criterion MUST be followed\n"
        "   by a `Validation:` line stating how to verify it.>\n"
        "\n"
        "  ## Producer Guard Reachability\n"
        "  <REQUIRED. For every NEW branch or Acceptance-Criterion fixture that touches a\n"
        "   producer/citation function you cite (a function whose return value an AC\n"
        "   asserts on), enumerate every early-return guard of that function IN ORDER\n"
        "   (e.g. `if not document_url: return`, `if category != 'X': return []`) and\n"
        "   prove the new branch / asserted value is reachable past ALL of them. If a\n"
        "   guard short-circuits your branch, the branch is dead — move it before the\n"
        "   guard or drop the AC. Confirm the AC fixture sets every field the producer\n"
        "   reads before returning, else the producer returns empty and the AC is\n"
        "   untestable. If no Acceptance Criterion touches a cited producer function,\n"
        "   write `none`.>\n"
        "\n"
        "  ## Open Questions\n"
        "  <bullets, or `none`. Park unclear items here. Do NOT invent answers.>\n"
        "\n"
        "  ## Authorized Test Edits\n"
        "  <REQUIRED. If any Acceptance Criterion changes an EXISTING function/data\n"
        "   contract that a PRE-EXISTING test file asserts, list each such test file\n"
        "   below — GREEN is only allowed to edit listed files; edits to unlisted\n"
        "   pre-existing tests terminal-FAIL E_RED_TESTS_TAMPERED. Grep the test\n"
        "   tree for each changed symbol before declaring. Emit the machine-read\n"
        "   marker EXACTLY as shown (the engine parses it):\n"
        "\n"
        "  authorized-test-edits:\n"
        "  - `<path/to/pre-existing/test.py>` — <which AC/contract change requires the edit>\n"
        "\n"
        "   If no pre-existing test asserts a changed contract, emit the marker\n"
        "   line followed by the single word `none` on the next line.>\n"
        "\n"
        "ANTI-FABRICATION — producer rules in injection/producer-rules.md\n"
        "(## Anti-Fabrication — Producer Rules) apply. Surface-specific for FEATURE/COMPLEX spec:\n"
        "  - The FEATURE REQUEST and ARCHITECTURE DECISION are the only\n"
        "    authoritative sources for scope. Anything not in either of them is\n"
        "    out of scope unless you list it under ## Open Questions.\n"
        "\n"
        "SECURE-CODING DEFAULTS — security rules in injection/security-rules.md\n"
        "(## Secure Coding Defaults for Generated Code) apply. When the spec's design or\n"
        "ACs touch external input, subprocess execution, filesystem paths, secrets/config,\n"
        "or error handling, the ## Design and ## Acceptance Criteria MUST adopt the matching\n"
        "defaults from that file. They are defaults, not suggestions: deviating requires an\n"
        "explicit line in ## Constraints saying why.\n"
        "\n"
        "PRE-SUBMISSION CHECKLIST — verify your spec answers YES to each before responding:\n"
        "\n"
        "  [ ] Every file under ## Files comes from the FEATURE REQUEST or ARCHITECTURE\n"
        "      DECISION verbatim. No added MODIFY: paths the request doesn't touch.\n"
        "      (F2 path-divergence guard.)\n"
        "\n"
        "  [ ] Every interface, function, type added under ## Interfaces / ## Data Model\n"
        "      is referenced by >=1 Acceptance Criterion's Validation: line. If it isn't\n"
        "      tested, it's dead code — remove it. (F1 dead-code-paths guard.)\n"
        "\n"
        "  [ ] Every Acceptance Criterion has a Validation: line that names the file or\n"
        "      command that exercises the behavior. \"Manual review\", \"code inspection\",\n"
        "      \"documentation check\" are NOT validations.\n"
        "\n"
        "  [ ] ## Out of Scope is non-empty AND lists the plausible-but-unrequested\n"
        "      additions a reader might assume (e.g., logging, metrics, error retry,\n"
        "      backwards compat shims that the request doesn't ask for).\n"
        "\n"
        "  [ ] ## Open Questions lists every ambiguity in the FEATURE REQUEST that you\n"
        "      did NOT silently resolve. If the request is silent on a point and you\n"
        "      decided either way without flagging, it goes here.\n"
        "\n"
        "  [ ] authorized-test-edits: grepped the test tree for every changed contract symbol\n"
        "      and listed every PRE-EXISTING test file asserting the old contract; an\n"
        "      unlisted pre-existing-test edit terminal-FAILs (E_RED_TESTS_TAMPERED);\n"
        "      over-broad whitelists are also flagged by the reviewer.\n"
        "\n"
        "  [ ] Producer Guard Reachability section enumerates every early-return guard of\n"
        "      each cited producer function and proves each new branch / AC value is\n"
        "      reachable past them; a branch after a short-circuit guard is dead (F1), and\n"
        "      an AC fixture that omits a field the producer reads is untestable (F2).\n"
        "      `none` only if no AC touches a cited producer function.\n"
        "\n"
        "If any answer is \"no\", revise BEFORE responding. The reviewer applies these\n"
        "exact checks; failing any returns REVISE and forces a cycle-2 retry.\n"
    )


def _grounded_citation_contract() -> str:
    # A813CA08: source-side anti-fabrication for `<path>:<line>` citations.
    # Pairs with DBA495CF (output-side semgrep lint). Counterfactual that
    # motivates this block: forge-1777701765 cycle-1 fabricated
    # «`run-phase.ts` reads gate output» when the real consumer is
    # `build-compact.md:54`. The writer never opened run-phase.ts in that
    # turn — Read-tool grounding would have caught it.
    return (
        "## GROUNDED CITATION CONTRACT\n"
        "\n"
        "Every `<path>:<line>` citation in the spec must be grounded in a file you\n"
        "opened with the Read tool in this turn. Do not transcribe citations from\n"
        "memory, prior conversations, or training data — they will be wrong, and\n"
        "the reviewer will catch them.\n"
        "\n"
        "Rules:\n"
        "  1. Every `<path>:<line>` you cite in the spec MUST come from a file you\n"
        "     opened with the Read tool in this turn. Guesses, recall, and\n"
        "     transcription from prior context do not count as grounding.\n"
        "  2. If a behavior, contract, or invariant cannot be verified by Reading\n"
        "     a file in this turn, do NOT invent a `<path>:<line>` for it. Park\n"
        "     the unverified claim under `## Open Questions` instead.\n"
        "  3. Before emitting the spec, re-check every citation: does it correspond\n"
        "     to a file path you Read this turn, and does the line number fall\n"
        "     within that file? If not, drop the citation or move the claim to\n"
        "     `## Open Questions`.\n"
        f"  4. All `<path>:<line>` citations MUST be repo-rooted — the path\n"
        f"     portion must start at a real repository directory (one of the\n"
        f"     top-level directories of this repo: "
        f"{', '.join(repo_top_level_dirs())}). Bare filenames never\n"
        f"     resolve (the lint runs from the repo root) and will be flagged.\n"
        f"       WRONG path: `run-phase.ts` — bare filename, lint resolves from\n"
        f"                   repo root and fails.\n"
        f"       RIGHT path: `SYSTEM/cli/build/run-phase.ts` — repo-rooted,\n"
        f"                   resolves cleanly. Append `:<line>` to cite a line.\n"
        "  5. PREFER the snippet form `<path>:\"<verbatim fragment>\"` over\n"
        "     `<path>:<line>` citations. Line numbers drift on any file edit;\n"
        "     a quoted snippet anchor is verified by content-grep and remains\n"
        "     valid regardless of surrounding edits. Quote a short exact fragment\n"
        "     (3–200 chars) from the cited line using the syntax `<path>:\"<text>`\".\n"
        "     If the verbatim fragment contains a double-quote character, escape\n"
        "     each inner quote as \\\" so the citation parser finds the closing\n"
        "     delimiter (e.g. tools.py:\\\"layer: Literal[\\\\\\\"P\\\\\\\"]\\\" ).\n"
        "     The verifier unescapes before content-grep, so the file is the\n"
        "     ground truth — see citation_verifier.py _SNIPPET_CITE_RE /\n"
        "     _unescape_snippet (anti-drift, B7045745).\n"
        "     Use `<path>:<line>` only as a fallback when no short unique fragment\n"
        "     can be extracted from the target line.\n"
        "  6. This same grounding requirement applies to bare backtick SYMBOL\n"
        "     citations, not just `<path>:<line>` citations. Every code symbol\n"
        "     you cite as EXISTING (e.g. a bare backtick reference like\n"
        "     `SomeClass()` or `some_func`) MUST have been seen in a file you\n"
        "     opened with the Read tool in this turn. Recall or guessing does\n"
        "     not count as grounding for symbols either.\n"
        "  7. A symbol you intend to CREATE (does not exist yet) MUST be\n"
        "     explicitly labelled NEW — e.g. \"NEW: `foo`\" — so it is treated\n"
        "     as to-be-created, not as an existing symbol you claim to have\n"
        "     verified this turn.\n"
        "  8. Never cite a symbol name you have not verified by Reading its\n"
        "     file this turn.\n"
        # GH934: forbid citation-form for spec-introduced NEW symbols
        "  9. A NEW symbol (field, variable, function, event, config key) that this\n"
        "     spec itself INTRODUCES must NEVER appear in citation form — neither\n"
        "     `<path>:\"<symbol>\"` nor `<path>:<line>` pointing at it. The cite-lint\n"
        "     content-greps the file and a not-yet-existing symbol ALWAYS fails\n"
        "     (E_SPEC_CITE_LINT_FAIL). Write introduced symbols as plain backtick\n"
        "     code labelled NEW (rule 7), with no file: prefix. Citation form is\n"
        "     reserved for EXISTING code you Read this turn.\n"
    )


def _citation_grounding_rubric() -> str:
    # A813CA08: reviewer-side counterpart to the GROUNDED CITATION CONTRACT.
    # Asks the reviewer to verify each citation and report the grounding count
    # so the verdict explicitly accounts for fabrication risk.
    return (
        "## CITATION GROUNDING CHECK\n"
        "\n"
        "Every `<path>:<line>` citation in the spec is a fabrication risk. Verify\n"
        "each one before issuing a verdict:\n"
        "\n"
        "  - For each `<path>:<line>` reference in the spec, check that the file\n"
        "    exists, that the line number falls within the file, and that the\n"
        "    cited line's content actually supports the surrounding claim.\n"
        "  - For each snippet citation `<path>:\"<fragment>\"`, grounding means\n"
        "    confirming the quoted fragment appears verbatim (modulo whitespace)\n"
        "    in the file content — independent of line number. A snippet citation\n"
        "    is grounded if and only if the fragment is present in the file.\n"
        "  - Treat any citation you cannot verify as an ungrounded citation —\n"
        "    flag it under `## Findings` with the path and line, and add it to\n"
        "    the grounding count below.\n"
        "  - Report the grounding count as the FIRST line of `## Concerns Checked`, in\n"
        "    EXACTLY this form (the engine parses this line — do not reword it):\n"
        "    `Citation grounding count: <grounded>/<total> grounded (<ungrounded> ungrounded)`\n"
        "    MANDATORY: if `<ungrounded>` is 1 or more — i.e. any citation you could not\n"
        "    verify appears outside `## Open Questions` — your `## Verdict` MUST be `REVISE`,\n"
        "    never `SHIP`. A `SHIP` verdict reported alongside `<ungrounded>` >= 1 is a\n"
        "    contract violation.\n"
    )


def _review_output_schema() -> str:
    return (
        "OUTPUT — your response IS the file content of specs/build-plan-review.md.\n"
        "Start your response DIRECTLY with `## Verdict`. No preamble.\n"
        "\n"
        "REQUIRED sections:\n"
        "\n"
        "  ## Verdict\n"
        "  SHIP | REVISE\n"
        "  (CITATION GROUNDING CHECK: if <ungrounded> is 1 or more outside ## Open Questions, this MUST be REVISE.)\n"
        "\n"
        "  ## Findings (structured)\n"
        "  REQUIRED — emit on every review (even SHIP). Omitting this section\n"
        "  deactivates W1 cycle-2 restricted review and forces REVISE-cap on the\n"
        "  next iteration. If verdict is SHIP and no issues, emit `[]`.\n"
        "  Each finding REQUIRES a `root` field: \"spec\" (fixable by rewriting\n"
        "  the spec), \"upstream\" (defect lives in the architecture doc /\n"
        "  discovery / task inputs — no spec rewrite can fix it), or\n"
        "  \"already-done\" (the cited defect is verified already fixed at HEAD —\n"
        "  no spec change or code change is needed).\n"
        "  ```json\n"
        "  [\n"
        '    {"id": "1", "type": "fabrication",\n'
        '     "evidence": "spec line 17 says \'exit code 3\' but FEATURE REQUEST does not authorize this",\n'
        '     "required_action": "move exit-code policy to Open Questions or cite FR text",\n'
        '     "root": "spec"}\n'
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
        "Specifically scrutinize: features in the spec that are not in the request\n"
        "(fabrication); criteria without `Validation:` lines (untestable); empty or\n"
        "missing ## Out of Scope (no anti-scope discipline).\n"
        "authorized-test-edits coherence: every contract-changing AC must have its\n"
        "pre-existing asserting test files listed under `authorized-test-edits:`\n"
        "(missing entry = night-run terminal FAIL), and every listed file must be\n"
        "justified by a specific AC (over-broad whitelist = tamper-gate bypass —\n"
        "flag it).\n"
    )


def _spec_defect_nudge_block(reroute: dict[str, Any]) -> str:
    """GH767 §2.5: verbatim nudge injected when org_config['phase_reroute'] is
    present — the previous spec was rejected by the phase_5 validator as
    DEFECTIVE (not the tests). Verbatim (not summarized) validator finding is
    the highest-signal nudge for the rewrite."""
    reason = reroute.get("reason", "")
    attempt = reroute.get("attempt", 1)
    return (
        f"SPEC-DEFECT REROUTE (attempt {attempt} of 2) — the previous spec was REJECTED by the phase_5 validator\n"
        "as DEFECTIVE (not the tests). You are REWRITING the spec to remove this defect. Verbatim validator finding:\n"
        "<<<\n"
        f"{reason}\n"
        ">>>\n"
        "Do NOT reproduce the defect. If an AC is unsatisfiable as written, restate it so a test CAN satisfy it.\n"
        "If two rules overlap, declare precedence explicitly."
    )


def _spec_preflight_block() -> str:
    """GH443 part 2 — cycle-1 self-audit block, top 3 cycle-1 reject axes."""
    return (
        "SPEC PREFLIGHT (top cycle-1 reject axes — self-audit BEFORE writing):\n"
        "1. §1o consumer cite-verify — for any new status/sentinel/enum/return\n"
        "   value: read+cite every consumer that dispatches on it before\n"
        "   claiming the contract is safe.\n"
        "2. §1l/§1y forcing-function + reachability — every Acceptance\n"
        "   Criterion must anchor on a production side-effect and trace\n"
        "   Point (the prod line) -> Host (the enclosing fn that runs at\n"
        "   test time) -> Test-path (the test reaches that Host with the\n"
        "   value reachable).\n"
        "3. §1aa helper-extraction — logic an AC asserts on (inline math,\n"
        "   emits inside control-flow bodies) must live in a named,\n"
        "   independently-invocable helper, not inline.\n"
        "4. §1n error-taxonomy OWN/DEFER — a new gate or terminal E_-code must\n"
        "   enumerate existing error codes on the affected path and declare\n"
        "   OWN or DEFER for each on the code's line (Principle-B taxonomy).\n"
        "5. §2-vs-§3 finalize coverage — every §2 branch mentioning cleanup,\n"
        "   finalize, on-failure, on-terminal, on-exit, rollback, or merged-only\n"
        "   MUST have a covering AC in §3, and inversely every such AC needs the\n"
        "   §2 branch (DG-45 finalize xcheck rejects the mismatch).\n"
        "\n"
        "6. §1a sibling-shape-audit — for any changed/renamed/removed PUBLIC\n"
        "   symbol (function, constant, event name, StepResult data key, numbered\n"
        "   prompt-block axis) enumerate sibling-test coverage BY ARTIFACT/SHAPE,\n"
        "   not by identifier-grep alone: name every consumer that couples on the\n"
        "   symbol's SHAPE (dict keys, tuple/list cardinality, ordering/sequence,\n"
        "   numeric byte/growth caps, enum membership) EVEN WHEN it never spells the\n"
        "   symbol. An empty symbol grep means the audit is INAPPLICABLE (wrong\n"
        "   query) — NOT that the change is sibling-clean. Each such consumer gets a\n"
        "   covering AC or an authorized-test-edits entry.\n"
    ) + _spec_named_sections_block()


def _spec_named_sections_block() -> str:
    """4197B484 (GH1120-C) §1.5 — named-section mandate (§1aa named helper).

    Budgeted at <= 600 UTF-8 bytes; appended VERBATIM to _spec_preflight_block(),
    which covers both the cycle-1 and the cycle>=2 high-binding prompt paths.
    """
    return (
        "\n"
        "7. MANDATORY NAMED SECTIONS — the spec ALWAYS emits both, even when\n"
        "   inapplicable:\n"
        "   - `## §3.2 Sibling-test audit` — if inapplicable the body is exactly\n"
        "     `sibling-test audit: n/a — <reason>`.\n"
        "   - `## Data-Model Ground Truth` — if inapplicable the body is exactly\n"
        "     `ground-truth: n/a — <reason>`, written INSIDE that section.\n"
        "   An audit section lists the audited symbols by name; naming a symbol\n"
        "   there is what silences its sibling-audit finding.\n"
    )


def _spec_reentry_block() -> str:
    """GH823 §1ab+§1ac — re-entry AC mandate block, injected when the probe
    (arch text + question) mentions durable state."""
    return (
        "§1ab RE-ENTRY AC MANDATE: if the UUT reads/writes durable state\n"
        "(sentinel/cache/counter/resume/DBOS/governor/frozen-ref/findings-thread/\n"
        "cycle-cap) OR adds a gate/terminal E_-code, the spec MUST carry a\n"
        "`## Re-entry ACs` section with >=1 Acceptance Criterion per reachable\n"
        "entry path:\n"
        "  (a) fresh — first-ever entry, no prior state\n"
        "  (b) in-phase retry — re-entry within the same phase/cycle\n"
        "  (c) auto-resume — same run-id resumed after interruption\n"
        "  (d) DBOS-replay — durable-workflow replay of the same step\n"
        "Counter idempotency on (c)/(d): re-entry must NOT re-increment\n"
        "already-counted (idempoten) work. Model/config re-resolve on (b)/(c).\n"
        "§1ac: enumerate every cycle-keyed durable artifact the retry path\n"
        "replays, and declare a bounded retry budget. A new terminal E_-code\n"
        "must declare its legitimate exit (repair-stage / retry-class /\n"
        "documented by-design dead-end).\n"
    )


# GH705 §2/§1d: maximal contiguous CALL-INVARIANT static instruction run
# hoisted from the spec-writer scaffold — the VALIDATION-LINE GUARD literal
# followed by the SPEC-LINT PARITY RULES literal (separated only by a
# comment in source, contiguous in the join). Substituting one
# parts.append(_SPEC_STABLE_PREFIX) for the two original adjacent
# parts.append(...) calls preserves the joined prompt byte-for-byte.
_SPEC_STABLE_PREFIX = (
    "VALIDATION-LINE GUARD: each Acceptance Criterion's `Validation:` "
    "line shapes the downstream RED test. Apply the rubric below to "
    "your Validation lines — each must name a function/command "
    "invocation plus an observable side-effect check, not a source-"
    "file pattern grep."
    "\n"
    "SPEC-LINT PARITY RULES (GH601, mechanically enforced post-write by spec_lint_batch):\n"
    "  - TOKEN-CONSISTENCY: every literal name (helper, env var, event name) is written in ONE\n"
    "    canonical form throughout the spec — dot-vs-underscore drift (emit_foo vs emit.foo) = REJECT.\n"
    "  - PRESENCE-TRIAD (§1y): every side-effect AC names (1) the prod line, (2) the enclosing\n"
    "    host function that runs at test time, (3) the test path that reaches it.\n"
    "  - §1v FILES-NOT-IN-SCOPE: the spec's §5 MUST include a \"Files NOT in scope\" allowlist-inverse.\n"
    "  - §1w OP↔AC CROSS-LINK: every enumerated operation has ≥1 covering AC AND a handler-cite;\n"
    "    every AC maps back to an enumerated op.\n"
    "  - DATA-MODEL GROUND TRUTH (§1ae): a UUT touching an EXISTING prod DB table REQUIRES a\n"
    "    'Data-Model Ground Truth' section: verbatim reference DDL per table (```sql fence,\n"
    "    committed schema / .schema snapshot) + linkage columns; fixtures CREATE TABLE only from it.\n"
)


# GH729 §2.1 — greppable single source-of-truth axis tokens. Each is a
# verbatim substring of _spec_preflight_block() / _SPEC_STABLE_PREFIX below,
# so cycle-1 parity holds by construction (no cycle-1 byte change).
SPEC_HIGH_BINDING_AXES: tuple[str, ...] = (
    "§1o consumer cite-verify",
    "§1l/§1y forcing-function + reachability",
    "§1aa helper-extraction",
    "VALIDATION-LINE GUARD",
    "SPEC-LINT PARITY RULES",
    "TOKEN-CONSISTENCY",
    "PRESENCE-TRIAD (§1y)",
    "§1v FILES-NOT-IN-SCOPE",
    "§1w OP↔AC CROSS-LINK",
    "§2-vs-§3 finalize coverage",
    "DATA-MODEL GROUND TRUTH (§1ae)",
    "§1a sibling-shape-audit",
    "MANDATORY NAMED SECTIONS",
)


def _spec_high_binding_block() -> str:
    """GH729 §2.1.2 — bounded high-binding block for cycle>=2 REVISE prompts.

    Composed BY CALL to the existing cycle-1 helpers (§1g single-source) —
    never a copy — so a future edit to either helper propagates here too.
    """
    return (
        "HIGH-BINDING SPEC RULES (hold on EVERY cycle, including this revision — apply to every\n"
        "Acceptance Criterion you add, rewrite, or patch below):\n"
        "\n"
        + _spec_preflight_block()
        + "\n"
        + _SPEC_STABLE_PREFIX
    )


def missing_high_binding_axes(prompt: str) -> list[str]:
    """GH729 §2.1.3 — deterministic parity check; [] == full parity."""
    return [axis for axis in SPEC_HIGH_BINDING_AXES if axis not in prompt]


_SHIP_SIDECAR_NAME = ".build-spec.ship.json"
_SHIP_SIDECAR_FAIL_OPEN_EXC = (json.JSONDecodeError, OSError, ValueError, KeyError, TypeError)


def _ship_sidecar_path(spec_path: str) -> Path:
    return Path(spec_path).parent / _SHIP_SIDECAR_NAME


def _write_ship_sidecar(spec_path: str) -> None:
    """GH770 §2.1: persist a SHIP-quality marker sidecar recording the
    sha256 of the on-disk spec at the moment it passed lint gates. Fail-open
    on write errors — never fails the SHIP step."""
    try:
        text = Path(spec_path).read_text(encoding="utf-8")
        _run_ctx = telemetry_ctx.get_current_run()
        sidecar = {
            "spec_sha": hashlib.sha256(text.encode("utf-8")).hexdigest(),
            "run_id": (_run_ctx.run_id if _run_ctx is not None else None),
            "ts": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        }
        _ship_sidecar_path(spec_path).write_text(json.dumps(sidecar), encoding="utf-8")
    except _SHIP_SIDECAR_FAIL_OPEN_EXC as e:
        _emit_safe("spec_prior_base_write_failed", {"error": str(e), "spec_path": str(spec_path)})


def _read_ship_sidecar(spec_path: str) -> dict[str, Any] | None:
    """GH770 §2.1: read the SHIP sidecar; fail-open (return None) on any
    absence/corruption rather than raising."""
    try:
        sidecar_path = _ship_sidecar_path(spec_path)
        if not sidecar_path.is_file():
            return None
        return cast(dict[str, Any], json.loads(sidecar_path.read_text(encoding="utf-8")))
    except _SHIP_SIDECAR_FAIL_OPEN_EXC:
        return None


def _prior_ship_base_block(spec_path: str) -> str:
    """GH770 §2.2: build the cycle-1 "prior SHIP-spec base" block when a
    prior SHIP-quality spec exists on disk AND its sidecar-recorded sha256
    still matches the current on-disk content (i.e. it has not mutated
    since it last passed lint). Fail-open (empty string) otherwise, with
    an observability event on the inactive branches."""
    run_ctx = telemetry_ctx.get_current_run()
    run_id = run_ctx.run_id if run_ctx is not None else None
    try:
        path = Path(spec_path)
        if not path.is_file():
            _emit_safe("spec_prior_base_spec_missing", {"run_id": run_id, "spec_path": str(spec_path)})
            return ""
        spec_text = path.read_text(encoding="utf-8")
        sidecar = _read_ship_sidecar(spec_path)
        if sidecar is None:
            _emit_safe("spec_prior_base_spec_missing", {"run_id": run_id, "spec_path": str(spec_path)})
            return ""
        current_sha = hashlib.sha256(spec_text.encode("utf-8")).hexdigest()
        if sidecar.get("spec_sha") != current_sha:
            _emit_safe("spec_prior_base_stale", {"run_id": run_id})
            return ""
    except _SHIP_SIDECAR_FAIL_OPEN_EXC as e:
        _emit_safe("spec_prior_base_spec_missing", {"run_id": run_id, "spec_path": str(spec_path), "error": str(e)})
        return ""

    _emit_safe("spec_prior_base_reused", {"run_id": run_id, "prior_sha": current_sha})
    return (
        "## PRIOR SHIP-SPEC BASE (surgical revise mode)\n\n"
        f"{spec_text.strip()}\n\n"
        "REVISE SURGICALLY: change ONLY the sections required by the task nudge\n"
        "DO NOT change citation form\n"
        "NEW symbols/files MUST use the new-symbol convention\n\n"
        "REQUIRED OUTPUT SECTION: ## Unchanged Sections\n"
    )


def _build_spec_prompt(ctx: WorkflowContext, _prev: Any) -> StepResult:
    # Detect cycle from engine's initial_data injection (retry hook threads
    # initial_data={"cycle": N, "findings": <raw>} on recoverable retry).
    cycle = 1
    findings: str | None = None
    if isinstance(_prev, dict):
        cycle = int(_prev.get("cycle", 1))
        findings = _prev.get("findings")
    elif isinstance(_prev, StepResult) and isinstance(_prev.data, dict):
        cycle = int(_prev.data.get("cycle", 1))
        findings = _prev.data.get("findings")

    scratchpad = _resolve_scratchpad(ctx)
    arch_doc = scratchpad / ARCHITECTURE_DOC_RELPATH
    spec_path = scratchpad / SPEC_DOC_RELPATH

    # GH443 parts 1+2: hoist cycle≥2 delta-retry detection ahead of scaffold
    # assembly. Default ON; HAL_SPEC_DELTA_RETRY=0 restores legacy behavior
    # byte-identically (economics fix, no flip-by needed — escape hatch only).
    delta_enabled = get_config().gate_enabled("HAL_SPEC_DELTA_RETRY")
    threaded = None
    if isinstance(_prev, dict):
        threaded = _prev.get("structured_findings")
    elif isinstance(_prev, StepResult) and isinstance(_prev.data, dict):
        threaded = _prev.data.get("structured_findings")
    if cycle >= 2 and not threaded and delta_enabled:
        threaded = load_findings_thread(scratchpad)  # GH636: recover thread evicted from DBOS operation_outputs on ERROR-retry
    if cycle >= 2 and threaded and delta_enabled:
        structured_findings = threaded
    elif cycle >= 2 and findings:
        structured_findings = extract_structured_findings(findings)  # status-quo fallback
    else:
        structured_findings = None
    if cycle >= 2 and structured_findings and delta_enabled:
        try:
            spec_text = spec_path.read_text(encoding="utf-8")
        except OSError:
            spec_text = ""
        # GH592: prefer point-patch surgical revise over full-rewrite delta
        # retry, unless the kill-switch is off, the prior cycle already
        # fell back to full-rewrite (surgical_fallback), or there is no
        # spec text on disk to patch.
        surgical_fallback_flag = None
        if isinstance(_prev, dict):
            surgical_fallback_flag = _prev.get("surgical_fallback")
        elif isinstance(_prev, StepResult) and isinstance(_prev.data, dict):
            surgical_fallback_flag = _prev.data.get("surgical_fallback")
        surgical_enabled = get_config().gate_enabled("HAL_SURGICAL_REVISE")
        _prev_data_delta = _prev.data if isinstance(_prev, StepResult) and isinstance(_prev.data, dict) else {}
        # GH729 §2.2 kill-switch: HAL_SPEC_HIGH_BINDING_PARITY=0 restores the
        # pre-GH729 cycle>=2 bodies byte-identically.
        high_binding_enabled = get_config().gate_enabled("HAL_SPEC_HIGH_BINDING_PARITY")
        if surgical_enabled and not surgical_fallback_flag and spec_text:
            surgical_prompt = build_surgical_revise_prompt(
                str(spec_path), spec_text, structured_findings,
                verbatim_reviewer_context=findings,
            )
            if high_binding_enabled:
                prompt = _spec_high_binding_block() + "\n\n" + surgical_prompt + "\n\n" + _get_out_of_role_block()
            else:
                prompt = surgical_prompt + "\n\n" + _get_out_of_role_block()
            high_binding_missing = missing_high_binding_axes(prompt)
            _emit_safe("spec_prompt_high_binding", {
                "cycle": cycle, "path": "surgical", "missing": high_binding_missing,
            })
            return StepResult(
                status="ok",
                data=_fwd_frozen(_prev_data_delta, {
                    "prompt": prompt,
                    "doc_path": str(spec_path),
                    "arch_doc_present": arch_doc.is_file(),
                    "prompt_bytes": len(prompt.encode("utf-8")),
                    "cycle": cycle,
                    "delta_retry": True,
                    "surgical_revise": True,
                    "surgical_base_spec": spec_text,
                    "structured_findings": structured_findings,
                    "high_binding_missing": high_binding_missing,
                }),
                duration_ms=0,
                step_name="build_spec_prompt",
            )
        delta_prompt = build_delta_retry_prompt(
            str(spec_path), spec_text, structured_findings,
            verbatim_reviewer_context=findings,
        )
        if high_binding_enabled:
            prompt = _spec_high_binding_block() + "\n\n" + delta_prompt + "\n\n" + _get_out_of_role_block()
        else:
            prompt = delta_prompt + "\n\n" + _get_out_of_role_block()
        high_binding_missing = missing_high_binding_axes(prompt)
        _emit_safe("spec_prompt_high_binding", {
            "cycle": cycle, "path": "delta", "missing": high_binding_missing,
        })
        return StepResult(
            status="ok",
            data=_fwd_frozen(_prev_data_delta, {
                "prompt": prompt,
                "doc_path": str(spec_path),
                "arch_doc_present": arch_doc.is_file(),
                "prompt_bytes": len(prompt.encode("utf-8")),
                "cycle": cycle,
                "delta_retry": True,
                "high_binding_missing": high_binding_missing,
            }),
            duration_ms=0,
            step_name="build_spec_prompt",
        )

    # GH823 §2.4: hoist arch_text/probe computation above parts assembly so
    # both the reentry-block gate (below) and the existing L1082/L1090
    # consumers reuse a single computation (§1g).
    try:
        arch_text = arch_doc.read_text(encoding="utf-8", errors="replace")
    except OSError:
        arch_text = None
    probe = (arch_text or "") + "\n" + (ctx.question or "")

    parts: list[str] = []
    reroute = (getattr(ctx, "org_config", None) or {}).get("phase_reroute")
    if reroute:
        parts.append(_spec_defect_nudge_block(reroute))
        parts.append("")
    role = _maybe_role_template(ctx)
    if role:
        parts.append(role.rstrip())
        parts.append("")
    parts.append(_read_first_block(scratchpad))
    parts.append("")
    parts.append(
        "ROLE: You are a spec writer. Turn the architecture into a concrete, "
        "verifiable specification. Open files yourself — do NOT trust summaries."
    )
    parts.append("")
    # GH823 §2.4: re-entry AC prompt block — axis-B high-binding position,
    # immediately after the ROLE paragraph and before FEATURE REQUEST:.
    # Gated on stateful-token match over the probe (arch text + question).
    if (cycle == 1 or get_config().gate_enabled("HAL_SPEC_HIGH_BINDING_PARITY")) and stateful_probe(probe):
        parts.append(_spec_reentry_block())
        parts.append("")
    parts.append("FEATURE REQUEST:")
    parts.append(ctx.question or "(no feature request provided)")
    parts.append("")
    if arch_doc.is_file():
        parts.append(f"ARCHITECTURE DECISION (read this file — do NOT trust summaries): {arch_doc}")
    else:
        parts.append(f"ARCHITECTURE DECISION: (none at {arch_doc} — proceed without)")
    parts.append("")
    # Subtask C (8A9C0F24) — inline decision_doc text after architecture, before
    # grounding rules + OUTPUT schema, so writer reads the decision text before
    # being told how to format the response.
    decision_block = _read_decision_doc_block(ctx.org_config)
    if decision_block:
        parts.append(decision_block)
        parts.append("")
    # A813CA08: anti-fabrication contract for `<path>:<line>` citations.
    # Lives BEFORE the OUTPUT schema so the writer reads the grounding rules
    # before the response template. Persists across cycles (cycle-2 retries
    # must keep the discipline — fabrication on retry is just as harmful).
    parts.append(_grounded_citation_contract())
    parts.append("")
    # GH770 §2.2: cycle-1 prior-SHIP-base reuse block. Empty string when
    # inactive (no prior spec / sidecar / sha mismatch) — guarded so no
    # empty-block artifact is introduced into the prompt (AC2/AC12 parity).
    prior_block = _prior_ship_base_block(str(spec_path))
    if prior_block:
        parts.append(prior_block)
        parts.append("")
    # GH443 part 2 — cycle-1 self-audit block, immediately BEFORE the
    # VALIDATION-LINE GUARD paragraph (writer reads audit axes before
    # formatting rules). GH729 §2.2: also present on cycle>=2 scaffold paths
    # (restricted-writer / legacy REVISION) unless the kill-switch is off, in
    # which case the guard is re-armed to cycle-1-only (byte-identical to
    # pre-GH729).
    if cycle == 1 or get_config().gate_enabled("HAL_SPEC_HIGH_BINDING_PARITY"):
        parts.append(_spec_preflight_block())
        parts.append("")
    # GH601/GH596 (§2.3): mirror gate rules into the spec-writer prompt so
    # the produced spec is prevention-first-compliant with the gates that
    # will later mechanically enforce it (spec_lint_batch + stub-passability).
    parts.append(_SPEC_STABLE_PREFIX)

    if _RE_SPEC_PY_WANTS.search(probe):
        parts.append(
            "PYTHON WIRING RULE (GH596): the spec MUST NOT prescribe patching/mocking the UUT symbol\n"
            "inside the UUT's own tests — the stub-passability gate terminally rejects such REDs\n"
            "(E_RED_STUB_PASSABLE). Prescribe mocking the UUT's dependencies instead."
        )

    if _RE_SPEC_FMT_WANTS.search(probe):
        parts.append(
            "FORMAT-CONVERSION RULE (§1x, b95a1a20): a spec changing a storage format MUST include a\n"
            "\"writer-behavior-on-legacy-input\" section enumerating per-input-format case behavior\n"
            "(append-if-new / auto-migrate / refuse-if-unknown / create-if-absent)."
        )

    parts.append(_get_behavioral_rubric())
    parts.append(_spec_output_schema(str(spec_path)))

    # Cycle ≥2, no delta path taken above: prefer restricted writer when
    # prev cycle-1 review carries a structured findings JSON block. Falls
    # back to the legacy free-rewrite ## REVISION block when no structured
    # findings present (backward-compat). Mirrors phase_45_spec_lite W1
    # wiring (commit 702ea109).
    if cycle >= 2 and structured_findings:
        # Restricted writer: only address flagged items, no scope widening.
        spec_path_for_writer = scratchpad / SPEC_DOC_RELPATH
        try:
            spec_text = spec_path_for_writer.read_text(encoding="utf-8")
        except OSError:
            spec_text = ""
        restricted_prompt = _restricted_writer_prompt(
            spec=spec_text, findings=structured_findings,
            verbatim_reviewer_context=findings,
        )
        parts.append("")
        parts.append(restricted_prompt)
        # Per-finding ID markers — surfaces "FINDING_<id>:" tokens in the prompt
        # body so the writer can address each ID by reference. The
        # restricted_writer_prompt itself renders bracketed `FINDING_<id> [type]:`;
        # this addendum is an explicit per-ID checklist for the writer to address.
        parts.append("")
        parts.append("ADDRESS EACH FINDING (by id):")
        for f in structured_findings:
            fid = f.get("id", "?")
            req = f.get("required_action", "")
            parts.append(f"- FINDING_{fid}: {req}")
    elif cycle >= 2:
        parts.append("")
        parts.append(f"## REVISION (cycle {cycle} — address reviewer findings)")
        parts.append("")
        parts.append(findings or "(no findings text captured)")
        parts.append("")
        parts.append(
            "RULES FOR THIS REVISION:\n"
            f"  - Your Write to {spec_path} IS the spec file (full body, not a diff). Start with\n"
            "    `## Context`; SpecKit-style sections only.\n"
            "  - Do NOT describe what you changed. Do NOT list changes made — rewrite\n"
            "    the FULL spec body, not a changelog.\n"
            "  - Address every concern listed under ## Findings above.\n"
            "  - Do NOT widen scope. If a finding asks for clarification, write\n"
            "    `## Open Questions` instead of inventing an answer.\n"
            "  - Do NOT add features the FEATURE REQUEST does not explicitly name.\n"
        )

    prompt = "\n".join(parts) + "\n\n" + _get_out_of_role_block()
    _standards_block = get_standards_context(ctx)
    if _standards_block:
        prompt = _standards_block + prompt
    _prev_data_bsp = _prev.data if isinstance(_prev, StepResult) and isinstance(_prev.data, dict) else {}
    high_binding_missing = missing_high_binding_axes(prompt)
    _emit_safe("spec_prompt_high_binding", {
        "cycle": cycle, "path": "scaffold", "missing": high_binding_missing,
    })
    return StepResult(
        status="ok",
        data=_fwd_frozen(_prev_data_bsp, {
            "prompt": prompt,
            "doc_path": str(spec_path),
            "arch_doc_present": arch_doc.is_file(),
            "prompt_bytes": len(prompt.encode("utf-8")),
            "cycle": cycle,
            "delta_retry": False,
            "stable_prefix": _SPEC_STABLE_PREFIX,
            "high_binding_missing": high_binding_missing,
        }),
        duration_ms=0,
        step_name="build_spec_prompt",
    )


def _invoke_spec_llm(ctx: WorkflowContext, prev: Any) -> StepResult:
    if not isinstance(prev, StepResult) or not isinstance(prev.data, dict):
        return StepResult(
            status="error", data=None, duration_ms=0,
            step_name="invoke_spec_llm",
            error="prev step did not produce a prompt",
            error_code="E_MISSING_PREV_DATA",
        )
    # E6602155: frozen spec fast-path — spec already ingested by step-0; skip LLM call.
    if prev.data.get("is_frozen"):
        return StepResult(
            status="skip",
            data={**prev.data},
            duration_ms=0,
            step_name="invoke_spec_llm",
        )
    cfg = ctx.org_config or {}
    is_surgical = bool(prev.data.get("surgical_revise"))
    _doc = prev.data.get("doc_path")
    if _doc and not is_surgical:
        # GH592: base spec must survive on disk for the surgical apply/fallback
        # branch of _write_spec_doc — skip the unlink on the surgical path.
        try:
            Path(_doc).unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass
    # GH625: thread frozen-threading keys (incl. gate_attempts) the same way
    # _invoke_review_llm does at :2670 — extra_data is hand-built here, so
    # without this the LLM-call return drops gate_attempts silently.
    _frozen_fwd = {k: prev.data[k] for k in _FROZEN_THREADING_KEYS if k in prev.data}
    extra_data = {
        "doc_path": prev.data["doc_path"],
        "cycle": prev.data.get("cycle", 1),
        **_frozen_fwd,
    }
    allowed_tools = ["Read", "Write", "Glob"]
    if is_surgical:
        extra_data["surgical_revise"] = True
        extra_data["surgical_base_spec"] = prev.data.get("surgical_base_spec")
        extra_data["structured_findings"] = prev.data.get("structured_findings")
        allowed_tools = ["Read"]
    return invoke_llm_subprocess(
        prompt=prev.data["prompt"],
        model=_resolve_model(cfg, "spec_model", _default_spec_model()),
        timeout_sec=_resolve_spec_timeout_sec(cfg),
        step_name="invoke_spec_llm",
        extra_data=extra_data,
        allowed_tools=allowed_tools,
        stable_prefix=prev.data.get("stable_prefix", ""),
    )


# Negative lookbehind rejects alphanumerics/dots/slashes/dashes — excludes already-rooted
# paths (e.g. SYSTEM/foo/bar.py) and partial paths (e.g. engine_py/foo.py) when they
# follow another path component. DA5330E9: capture class now allows `/` so multi-segment
# shorthand like `renderers/agreement.py` is captured and resolved via the suffix map.
_BARE_CITATION_RE = re.compile(
    r"(?<![A-Za-z0-9_./-])([A-Za-z0-9_./-]+\.(?:ts|py|md|sh|tsx|js|yml)):(\d+)"
)


def _build_suffix_map(repo_root: Path) -> dict[str, str]:
    """For each git-tracked path, register every suffix tail (basename → 2-seg → ... → full).
    Keep only suffixes that resolve to exactly one full path; drop ambiguous ones.

    DA5330E9 (R5, BARK ppba#657 §3.4): generalises 6DBB248D's basename-only map. Multi-app
    monorepo basenames (e.g. `function_app.py` × 3 paths) stay dropped via the same uniqueness
    rule, but their LONGER unique suffixes (e.g. `app-a/function_app.py`) are now resolvable.
    """
    try:
        result = git_read(
            ["-c", "core.quotepath=false", "ls-files", "-z"],
            cwd=str(repo_root),
            timeout=5,
        )
    except FileNotFoundError:
        logger.debug("autoprefix git_ls_files failed (reason=%s)", "git_not_found")
        return {}
    except OSError as e:
        logger.debug(
            "autoprefix git_ls_files failed (reason=%s, errno=%s)",
            "os_error",
            getattr(e, "errno", None),
        )
        return {}
    if result.returncode != 0:
        logger.debug(
            "autoprefix git_ls_files failed (reason=%s, rc=%s)",
            "nonzero_rc",
            result.returncode,
        )
        return {}
    candidates: dict[str, set[str]] = {}
    for line in result.stdout.split("\0"):
        line = line.strip()
        if not line:
            continue
        parts = line.split("/")
        for k in range(1, len(parts) + 1):
            suffix = "/".join(parts[-k:])
            candidates.setdefault(suffix, set()).add(line)
    return {s: next(iter(paths)) for s, paths in candidates.items() if len(paths) == 1}


def _autoprefix_bare_citations(text: str, repo_root: Path) -> str:
    if not text:
        return ""
    # 6DBB248D F3 — short-circuit when zero bare citations: avoids spawning
    # a git subprocess for every spec that already cites repo-rooted paths.
    if not _BARE_CITATION_RE.search(text):
        return text
    suffix_map = _build_suffix_map(repo_root)
    if not suffix_map:
        return text

    def repl(m: re.Match[str]) -> str:
        key = m.group(1)
        line = m.group(2)
        full = suffix_map.get(key)
        # 6DBB248D F4 + DA5330E9 R5: `full == key` only fires for repo-root files
        # (e.g. README.md → key='README.md', full='README.md') AND for already-rooted
        # citations whose entire path is also a unique suffix (e.g. citing
        # SYSTEM/foo.py when SYSTEM/foo.py is the only path with that suffix). This
        # guard is the sole defense against rewrites that would be no-ops or churn.
        if full is None or full == key:
            return m.group(0)
        return f"{full}:{line}"

    return _BARE_CITATION_RE.sub(repl, text)


_CANARY_EVENT_TYPE_RE = re.compile(r"^\s*(?:-\s*)?event_type:\s*(\S+)\s*$")


def _parse_canary_integration(raw: str) -> dict[str, Any]:
    """Parse a '## Canary Integration' block from a spec document.

    Scans for the line whose stripped text == '## Canary Integration',
    then reads following lines until the next '## ' heading or EOF.
    Returns {"event_type": <value>} for the FIRST matching event_type line,
    or {} if the heading is absent or contains no event_type line.

    Agreement 36269734 — counterpart sidecar path: CANARY_META_RELPATH.
    """
    lines = raw.splitlines()
    in_section = False
    for line in lines:
        stripped = line.strip()
        if not in_section:
            if stripped == "## Canary Integration":
                in_section = True
            continue
        # Stop at the next heading
        if stripped.startswith("## "):
            break
        m = _CANARY_EVENT_TYPE_RE.match(line)
        if m:
            return {"event_type": m.group(1)}
    return {}


def _resolve_spec_source(doc_path: str, raw_response: str) -> tuple[str, str]:
    """Return (raw, source) selecting the spec body from file or raw_response.

    If doc_path is a file with non-whitespace content, return (file_text, "worker_file").
    Otherwise return (raw_response, "raw_response_fallback") — fail-open fallback that
    preserves today's behavior when the subagent does not write the file.
    (FD2592D9 §1aa named sourceable helper — §2.2)
    """
    try:
        p = Path(doc_path)
        if p.is_file():
            text = p.read_text(encoding="utf-8")
            if text.strip():
                return (text, "worker_file")
    except (OSError, TypeError):
        pass
    return (raw_response, "raw_response_fallback")


def _write_spec_doc(_ctx: WorkflowContext, prev: Any) -> StepResult:
    if not isinstance(prev, StepResult) or not isinstance(prev.data, dict):
        return StepResult(
            status="error", data=None, duration_ms=0,
            step_name="write_spec_doc",
            error="prev step did not produce raw_response",
            error_code="E_MISSING_PREV_DATA",
        )
    # E6602155: frozen spec fast-path — spec already on disk from step-0; skip write.
    if prev.data.get("is_frozen"):
        return StepResult(
            status="skip",
            data={**prev.data},
            duration_ms=0,
            step_name="write_spec_doc",
        )
    # GH592: point-patch surgical revise — raw_response is a patches JSON
    # array, not a spec body; _resolve_spec_source does NOT apply here.
    if prev.data.get("surgical_revise"):
        cycle = int(prev.data.get("cycle", 1))
        base = prev.data.get("surgical_base_spec") or ""
        structured_findings_fwd = prev.data.get("structured_findings") or []

        def _surgical_fallback(reason: str, finding_id: str | None = None) -> StepResult:
            _emit_safe("surgical_revise_fallback", {
                "cycle": cycle,
                "reason": reason,
                "finding_id": finding_id,
            })
            # GH592 amendment 1: invalidate the poisoned resume sentinel — otherwise the engine's
            # resume_sentinel would replay the cached surgical LLM result on the same-cycle retry.
            try:
                scratchpad = _resolve_scratchpad(_ctx)
                try:
                    for p in (scratchpad / "resume").glob(f"invoke_spec_llm_done_c{cycle}_*.json"):
                        p.unlink()
                except OSError:
                    pass
                # GH605: unlink same-cycle delta sidecar so a same-cycle retry
                # (full rewrite fallback) never leaves a stale surgical-delta
                # sidecar behind for a later review to pick up.
                try:
                    sidecar = scratchpad / "specs" / f"surgical-delta-cycle-{cycle}.json"
                    if sidecar.exists():
                        sidecar.unlink()
                except OSError:
                    pass
            except ValueError:
                pass
            _sf_data = {
                "retry_from_step": 0,
                "cycle_count": cycle - 1,
                "surgical_fallback": True,
                "structured_findings": structured_findings_fwd,
                "findings": prev.data.get("findings", "") or "",
            }
            # GH625: thread gate_attempts through the fallback-retry data or
            # the accumulated spend is wiped every time the surgical patch
            # fails to parse, restarting the revise gate at attempts=0.
            if isinstance(prev.data.get("gate_attempts"), dict):
                _sf_data["gate_attempts"] = prev.data["gate_attempts"]
            return StepResult(
                status="error",
                data=_sf_data,
                duration_ms=0,
                step_name="write_spec_doc",
                error=f"surgical revise patches inapplicable (reason={reason}) — falling back to full rewrite",
                error_code="E_VALIDATION_RETRY",
                recoverable=True,
            )

        patches = extract_surgical_patches(prev.data.get("raw_response", ""))
        if patches is None:
            return _surgical_fallback("parse_error")
        patched, meta = apply_surgical_patches(base, patches)
        if patched is None:
            return _surgical_fallback(meta["reason"], meta.get("finding_id"))

        _emit_safe("surgical_revise_applied", {
            "cycle": cycle,
            "n_patches": meta["n_patches"],
            "spec_bytes": len(patched.encode("utf-8")),
        })
        # GH605: sidecar carries the applied patch diffs so the delta re-review
        # path can vote on the diff instead of re-embedding the full spec.
        scratchpad_for_delta = _resolve_scratchpad(_ctx)
        delta_sidecar = scratchpad_for_delta / "specs" / f"surgical-delta-cycle-{cycle}.json"
        delta_sidecar.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(delta_sidecar, json.dumps({"cycle": cycle, "patches": patches}))
        raw = patched
        canonical = Path(prev.data["doc_path"])
        canonical.parent.mkdir(parents=True, exist_ok=True)
        git_cwd = Path(resolve_git_cwd(_ctx.org_config or {}))
        raw = _autoprefix_bare_citations(raw, git_cwd)

        surgical_cycle_path: str | None = None
        if cycle >= 2:
            versioned = canonical.parent / Path(_spec_cycle_relpath(cycle)).name
            atomic_write(versioned, raw)
            surgical_cycle_path = str(versioned)
        atomic_write(canonical, raw)

        surgical_data = {
            "spec_path": str(canonical),
            "spec_bytes_written": len(raw.encode("utf-8")),
            "cycle": cycle,
            "surgical_revise": True,
        }
        if surgical_cycle_path is not None:
            surgical_data["spec_cycle_path"] = surgical_cycle_path

        meta_canary = _parse_canary_integration(raw)
        if meta_canary.get("event_type"):
            scratchpad = _resolve_scratchpad(_ctx)
            (scratchpad / "integration").mkdir(parents=True, exist_ok=True)
            (scratchpad / CANARY_META_RELPATH).write_text(
                json.dumps({"event_type": meta_canary["event_type"]}), encoding="utf-8"
            )
            surgical_data["canary_event_type"] = meta_canary["event_type"]
            _emit_safe("canary_integration_parsed", {"event_type": meta_canary["event_type"]})

        # GH625: surgical_data is hand-built (no **prev.data spread), thread
        # gate_attempts through explicitly or the gate never sees prior spend.
        if isinstance(prev.data, dict) and isinstance(prev.data.get("gate_attempts"), dict):
            surgical_data["gate_attempts"] = prev.data["gate_attempts"]

        return StepResult(
            status="ok",
            data=surgical_data,
            duration_ms=0,
            step_name="write_spec_doc",
        )

    raw, source = _resolve_spec_source(
        cast(str, prev.data.get("doc_path")), prev.data["raw_response"]
    )
    _emit_safe("spec_writer_return_source", {"source": source, "bytes": len(raw.encode())})
    cycle = int(prev.data.get("cycle", 1))
    canonical = Path(prev.data["doc_path"])
    canonical.parent.mkdir(parents=True, exist_ok=True)

    # D02C615D: rewrite bare citations to repo-rooted paths before persist.
    git_cwd = Path(resolve_git_cwd(_ctx.org_config or {}))
    raw = _autoprefix_bare_citations(raw, git_cwd)

    spec_cycle_path: str | None = None
    if cycle >= 2:
        # Write versioned copy (preserves cycle-1 output for audit) + overwrite canonical.
        versioned = canonical.parent / Path(_spec_cycle_relpath(cycle)).name
        atomic_write(versioned, raw)
        spec_cycle_path = str(versioned)
    atomic_write(canonical, raw)  # HIGH #4: atomic to avoid canonical corruption on kill

    data: dict[str, Any] = {
        "spec_path": str(canonical),
        "spec_bytes_written": len(raw.encode("utf-8")),
        "cycle": cycle,
    }
    if spec_cycle_path is not None:
        data["spec_cycle_path"] = spec_cycle_path

    # 36269734: parse Canary Integration block and write sidecar if event_type present.
    meta = _parse_canary_integration(raw)
    if meta.get("event_type"):
        scratchpad = _resolve_scratchpad(_ctx)
        (scratchpad / "integration").mkdir(parents=True, exist_ok=True)
        (scratchpad / CANARY_META_RELPATH).write_text(
            json.dumps({"event_type": meta["event_type"]}), encoding="utf-8"
        )
        data["canary_event_type"] = meta["event_type"]
        _emit_safe("canary_integration_parsed", {"event_type": meta["event_type"]})

    # GH625: data is hand-built (no **prev.data spread), thread gate_attempts
    # through explicitly or the gate never sees prior spend.
    if isinstance(prev.data, dict) and isinstance(prev.data.get("gate_attempts"), dict):
        data["gate_attempts"] = prev.data["gate_attempts"]

    return StepResult(
        status="ok",
        data=data,
        duration_ms=0,
        step_name="write_spec_doc",
    )


# ─── 8A9C0F24: spec completeness check (pre-citation, pre-Opus short-circuit) ─


def _verify_spec_completeness(ctx: WorkflowContext, prev: Any) -> StepResult:
    """Detect stub-spec output (changelog instead of full spec body) BEFORE
    the expensive Opus reviewer fires.  Saves ~123s + ~$1.64 per cycle.

    min_spec_lines: read from ctx.org_config["spec_min_lines"] if present.
    Default depends on complexity: 0 for SIMPLE (short specs are by
    design — 9ADF28E5), MIN_SPEC_LINES otherwise (FEATURE/COMPLEX use
    the line-count floor as a stub-detection heuristic). 4196B1A2
    moved the SIMPLE default from runtime setdefault mutation (was in
    phase_45_spec_lite) to this read site.

    Predicate (spec is incomplete):
        len(spec.splitlines()) < min_spec_lines  OR
        no line starts with REQUIRED_FIRST_SECTION ("## Context")

    On incomplete + cycle < MAX_REVIEW_CYCLES  → recoverable E_SPEC_INCOMPLETE
    On incomplete + cycle >= MAX_REVIEW_CYCLES → terminal  E_SPEC_INCOMPLETE_FATAL
    On ok                                      → status="ok", data inherits prev.data
    """
    step = "verify_spec_completeness"
    if not isinstance(prev, StepResult) or not isinstance(prev.data, dict):
        return StepResult(
            status="error", data=None, duration_ms=0, step_name=step,
            error="prev step did not produce data", error_code="E_MISSING_PREV_DATA",
            recoverable=False,
        )
    # E6602155: frozen spec fast-path — AC table already present; skip completeness check.
    if prev.data.get("is_frozen"):
        return StepResult(
            status="skip",
            data={**prev.data},
            duration_ms=0,
            step_name=step,
        )
    if "spec_path" not in prev.data:
        return StepResult(
            status="error", data=None, duration_ms=0, step_name=step,
            error="prev.data missing 'spec_path'", error_code="E_MISSING_PREV_DATA",
            recoverable=False,
        )

    spec_path = Path(prev.data["spec_path"])
    cycle = int(prev.data.get("cycle", 1))
    cfg = ctx.org_config or {}
    _default_min = 0 if str(cfg.get("complexity", "")).upper() == "SIMPLE" else MIN_SPEC_LINES
    min_lines = int(cfg.get("spec_min_lines", _default_min))

    if not spec_path.is_file():
        fwd = prev.data if isinstance(prev.data, dict) else {}
        build_class = (ctx.org_config or {}).get("complexity", "SIMPLE").upper()
        return cast(StepResult, RecoverableGateMixin.gated_step_result(
            build_class=build_class,
            gate="spec_retry",
            cycle=cycle,
            retry_from_step_idx=0,
            error_code="E_SPEC_FILE_MISSING",
            error_msg=f"spec file not on disk: {spec_path}",
            step_name=step,
            forwarded_data={**fwd, "missing_path": str(spec_path)},
            terminal_error_code="E_SPEC_FILE_MISSING",
        ))

    # utf-8-sig: transparently drops a leading BOM (0xEF 0xBB 0xBF) which real
    # claude-p outputs occasionally include.  Plain utf-8 leaves the BOM as
    # U+FEFF on the first character — and .strip() does NOT remove it (BOM is
    # non-whitespace) — so the header check below false-positives a stub-spec
    # error on otherwise-valid specs.  Agreement 83140A09.
    spec_text = spec_path.read_text(encoding="utf-8-sig")
    lines = spec_text.splitlines()
    line_count = len(lines)

    too_short = line_count < min_lines
    missing_header = not any(ln.strip() == REQUIRED_FIRST_SECTION for ln in lines)

    if not (too_short or missing_header):
        return StepResult(
            status="ok",
            data={**prev.data, "completeness_verified": True},
            duration_ms=0, step_name=step,
        )

    # Build actionable findings_text
    reasons: list[str] = []
    if too_short:
        reasons.append(
            f"Spec file has {line_count} lines; minimum is {min_lines} for SpecKit format."
        )
    if missing_header:
        reasons.append(
            f"Spec missing required '{REQUIRED_FIRST_SECTION}' section as first heading."
        )
    reason_str = " ".join(reasons)
    findings_text = (
        f"{reason_str} "
        "REWRITE the FULL spec body — do not produce a changelog or summary of revisions. "
        f"Include all required sections starting with '{REQUIRED_FIRST_SECTION}'."
    )

    fwd_incomplete = prev.data if isinstance(prev.data, dict) else {}
    build_class_incomplete = (ctx.org_config or {}).get("complexity", "SIMPLE").upper()
    # 457DC7DC GH371 §2.2: directed-repair pre-stage before the unchanged
    # recoverable-regen return.
    dr_findings = [
        {"path": str(spec_path), "line": None, "rule": "spec_completeness",
         "evidence": r}
        for r in reasons
    ]
    if _directed_repair_enabled(ctx) and dr_findings:
        rr = attempt_directed_repair(
            gate="spec_completeness",
            artifact_path=str(spec_path),
            findings=dr_findings,
            rerun_gate=lambda: _verify_spec_completeness(ctx, prev),
            cheap_model=_resolve_directed_repair_model(cfg),
            repair_step_name="repair_spec_completeness",
            max_attempts=_repair_cap(cfg),
            ctx=ctx,
        )
        if rr.converged and rr.final is not None:
            return rr.final
    return cast(StepResult, RecoverableGateMixin.gated_step_result(
        build_class=build_class_incomplete,
        gate="spec_retry",
        cycle=cycle,
        retry_from_step_idx=0,
        error_code="E_SPEC_INCOMPLETE",
        error_msg=reason_str,
        step_name=step,
        forwarded_data={
            **fwd_incomplete,
            "findings_text": findings_text,  # backward-compat with unit tests
            "findings": findings_text,       # engine retry hook threads this into next-cycle prompt
        },
        terminal_error_code="E_SPEC_INCOMPLETE_FATAL",
    ))


# ─── D04A3BA8: spec_lint wiring (fabricated-citation subprocess gate) ────────


def _normalize_spec_lint_findings(findings: list[str], spec_path: str) -> list[dict[str, Any]]:
    """457DC7DC GH371 §2.3/AC7: normalize each `<basename>:L<line>:<rule>:<evidence>`
    finding string into a locus dict `{path, line, rule, evidence}`. Backward-safe:
    if the `L<line>` field is absent (pre-#371 driver), line falls back to None.
    Evidence may itself contain colons — everything after the rule is preserved
    verbatim.
    """
    out: list[dict[str, Any]] = []
    for s in findings:
        parts = s.split(":")
        idx = 1  # parts[0] is the spec basename
        line_val: int | None = None
        if len(parts) > idx and parts[idx].startswith("L") and parts[idx][1:].isdigit():
            line_val = int(parts[idx][1:])
            idx += 1
        rule = parts[idx] if len(parts) > idx else ""
        evidence = ":".join(parts[idx + 1:]) if len(parts) > idx + 1 else ""
        out.append({"path": spec_path, "line": line_val, "rule": rule, "evidence": evidence})
    return out


def _sibling_vocab_hint(findings: list[str]) -> str:
    """4197B484 (GH1120-C) §1.5 — marker-vocabulary hint for the retrying agent.

    Returns "" unless at least one finding's rule id is `sibling-audit-missing`.
    Every marker below is a verbatim copy of
    sibling_test_verifier.AUDIT_MARKER_VOCABULARY (drift pinned by AC10).
    """
    if not any(":sibling-audit-missing:" in f for f in findings):
        return ""
    return (
        " To resolve a sibling-audit finding, add a `## §3.2 Sibling-test audit` "
        "section that NAMES the cited symbol; scoping is per symbol, so a section "
        "not naming it (including an `n/a` body) does not silence it. Accepted "
        "section markers (case-insensitive): §3.2 | sibling-test audit | "
        "sibling-test-audit | sibling test audit | sibling-audit:"
    )


def _spec_lint_fail_message(findings: list[str], spec_path: str) -> str:
    """4197B484 §1.5/§1aa — named sourceable composer for the E_SPEC_LINT_FAIL
    terminal message. Preserves today's exact text verbatim and appends the
    sibling marker-vocabulary hint when it applies."""
    first = findings[0] if findings else "(no findings text)"
    return (
        f"spec_lint detected {len(findings)} unverified citation(s) in "
        f"{spec_path}; first: {first}"
    ) + _sibling_vocab_hint(findings)


def _verify_spec_lint(ctx: WorkflowContext, prev: Any) -> StepResult:
    """D04A3BA8 Step 5B.2: invoke scripts/spec_lint/lint_spec.py against the
    just-written spec.  Hard-gates on any findings (E_SPEC_LINT_FAIL,
    recoverable=False).  Degrades gracefully when the driver is absent.
    """
    step = "verify_spec_lint"
    # 1. Validate prev.data
    if not isinstance(prev, StepResult) or not isinstance(prev.data, dict):
        return StepResult(
            status="error", data=None, duration_ms=0, step_name=step,
            error="prev step did not produce data", error_code="E_MISSING_PREV_DATA",
        )
    # E6602155: frozen spec fast-path — spec already verified upstream; skip lint.
    if prev.data.get("is_frozen"):
        return StepResult(
            status="skip",
            data={**prev.data},
            duration_ms=0,
            step_name=step,
        )
    spec_path = prev.data.get("spec_path")
    if not spec_path:
        # No spec to lint — skip cleanly.
        return StepResult(
            status="ok",
            data={**prev.data, "spec_lint_skipped": "no_spec_path"},
            duration_ms=0, step_name=step,
        )

    # Test-isolation opt-out: flow tests that exercise the review-cycle/verdict
    # path (not the lint gate) set org_config["spec_lint_skip"]. Default falsy
    # ⇒ gate RUNS in production. Default-safe (skip only when explicitly set).
    if (ctx.org_config or {}).get("spec_lint_skip"):
        return StepResult(
            status="ok",
            data={**prev.data, "spec_lint_skipped": "config_skip"},
            duration_ms=0, step_name=step,
        )

    # 2. Locate driver
    lint_path = Path(__file__).parent.parent / "scripts" / "spec_lint" / "lint_spec.py"
    if not lint_path.is_file():
        logger.warning("spec_lint driver missing at %s — skipping", lint_path)
        _emit_safe("gate_disabled", {
            "gate": "spec_lint",
            "step": step,
            "reason": "driver_missing",
            "detail": str(lint_path),
        })
        return StepResult(
            status="error",
            data={**prev.data},
            duration_ms=0, step_name=step,
            error=(
                f"spec_lint driver missing at {lint_path} — bootstrap incomplete? "
                f"see bootstrap-manifest.txt"
            ),
            error_code="E_SPEC_LINT_UNAVAILABLE",
            recoverable=False,
        )

    # 3. Resolve hal_root
    cfg = ctx.org_config or {}
    # AE0F261A: checklist thread-through if not already in cfg
    if "checklist" not in cfg and isinstance(prev, StepResult) and isinstance(prev.data, dict):
        cfg = {**cfg, "checklist": prev.data.get("checklist", {})}
    checklist = cfg.get("checklist") or {}
    _resolved_root, _resolver_source = resolve_project_root(cfg)
    hal_root = str(_resolved_root)

    # 4. Subprocess invoke
    try:
        proc = bounded_run(
            [sys.executable, str(lint_path), str(spec_path), "--hal-root", hal_root],
            capture_output=True, text=True, timeout=15,
        )
    except FileNotFoundError as e:
        return StepResult(
            status="ok",
            data={**prev.data, "spec_lint_skipped": "python_exec_missing", "detail": str(e)},
            duration_ms=0, step_name=step,
        )

    if proc.returncode == 124:
        return StepResult(
            status="error",
            data={**prev.data},
            duration_ms=0, step_name=step,
            error=f"spec_lint exceeded 15s timeout on {spec_path}",
            error_code="E_SPEC_LINT_TIMEOUT",
            recoverable=False,
        )

    # 5. Parse output
    findings = [line.strip() for line in (proc.stdout or "").splitlines() if line.strip()]
    rc = proc.returncode

    if rc == 0:
        logger.info("spec_lint: no findings for %s", spec_path)
        _write_ship_sidecar(str(spec_path))  # GH770 §2.1: SHIP-quality marker
        return StepResult(
            status="ok",
            data={**prev.data, "spec_lint_findings": []},
            duration_ms=0, step_name=step,
        )
    if rc == 2:
        logger.error(
            "spec_lint driver error (rc=2) on %s — failing closed; stderr: %s",
            spec_path, (proc.stderr or "")[-200:],
        )
        return StepResult(
            status="error",
            data={**prev.data, "spec_lint_driver_error": (proc.stderr or "")[-200:]},
            duration_ms=0, step_name=step,
            error=(
                f"spec_lint driver error (rc=2) on {spec_path} — failing closed "
                f"(a broken lint driver must NOT pass an unenforced spec); "
                f"stderr: {(proc.stderr or '')[-200:]}"
            ),
            error_code="E_SPEC_LINT_DRIVER_ERROR",
            recoverable=False,
        )
    if rc == 1:
        # 457DC7DC GH371 §2.2: cheap directed-repair pre-stage IN FRONT OF the
        # unchanged terminal return. Non-convergence falls through to today's
        # exact E_SPEC_LINT_FAIL terminal (recoverable=False).
        normalized = _normalize_spec_lint_findings(findings, str(spec_path))
        if _directed_repair_enabled(ctx) and normalized:
            rr = attempt_directed_repair(
                gate="spec_lint",
                artifact_path=str(spec_path),
                findings=normalized,
                rerun_gate=lambda: _verify_spec_lint(ctx, prev),
                cheap_model=_resolve_directed_repair_model(cfg),
                repair_step_name="repair_spec_lint",
                max_attempts=_repair_cap(cfg),
                ctx=ctx,
            )
            if rr.converged and rr.final is not None:
                return rr.final
        return StepResult(
            status="error",
            data={**prev.data, "spec_lint_findings": findings},
            duration_ms=0, step_name=step,
            error=_spec_lint_fail_message(findings, str(spec_path)),
            error_code="E_SPEC_LINT_FAIL",
            recoverable=False,
        )
    # Unexpected rc
    return StepResult(
        status="error",
        data={**prev.data},
        duration_ms=0, step_name=step,
        error=f"spec_lint returned unexpected rc={rc}; stderr={(proc.stderr or '')[-200:]}",
        error_code="E_SPEC_LINT_UNEXPECTED_RC",
        recoverable=False,
    )


# ─── 09EE3939: spec_cite_lint wiring (citation-existence subprocess gate, #269) ───


def _parse_cite_unresolved(stdout: str) -> list[dict[str, Any]]:
    """Pure helper (§1aa, GH689): parse spec-cite-lint.py's ``--json`` stdout
    and return only the ``unresolved_symbol``-status findings.

    Fail-soft: malformed/empty/None input, or a non-dict parse result, returns
    []  instead of raising — the caller (rc==1 branch) must never crash on a
    driver-format drift.
    """
    try:
        parsed = json.loads(stdout)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(parsed, dict):
        return []
    return [
        f for f in parsed.get("findings", [])
        if isinstance(f, dict) and f.get("status") == "unresolved_symbol"
    ]


def _parse_cite_status_counts(stdout: str) -> dict[str, int]:
    """Pure helper (§1aa, GH799): parse spec-cite-lint.py's ``--json`` stdout
    and return a per-status finding count (e.g. {"wrong_file": 3, ...}).

    Fail-soft, mirroring _parse_cite_unresolved: malformed/empty/None input,
    or a non-dict parse result, returns {} instead of raising — telemetry must
    never crash the gate (§1n OWN-all).
    """
    try:
        parsed = json.loads(stdout)
    except (json.JSONDecodeError, TypeError):
        return {}
    if not isinstance(parsed, dict):
        return {}
    counts: dict[str, int] = {}
    for f in parsed.get("findings", []):
        if not isinstance(f, dict):
            continue
        status = f.get("status")
        if not status:
            continue
        counts[status] = counts.get(status, 0) + 1
    return counts


def _emit_cite_status_telemetry(stdout: str, spec_path: Any, rc: int) -> None:
    """§2.2 (GH799): single emit seam for the cite-lint status distribution.
    Observation-only, NON-GATING. Never raises (§1n OWN-all)."""
    counts = _parse_cite_status_counts(stdout)
    payload = {
        "wrong_file": counts.get("wrong_file", 0),
        "unresolved_symbol": counts.get("unresolved_symbol", 0),
        "new_symbol": counts.get("new_symbol", 0),
        "planned_file": counts.get("planned_file", 0),
        "missing_file": counts.get("missing_file", 0),
        "resolved": counts.get("resolved", 0),
        "total_findings": sum(counts.values()),
        "rc": rc,
        "spec_path": str(spec_path),
    }
    _emit_safe("spec_cite_advisory", payload)


def _verify_spec_cite_lint(ctx: WorkflowContext, prev: Any) -> StepResult:
    """09EE3939: invoke spec-cite-lint.py against the just-written spec.
    Hard-gates on unresolved symbol citations (E_SPEC_CITE_LINT_FAIL,
    recoverable=False).  Degrades gracefully when the driver is absent or
    returns rc=2 (usage error).  This is a near-exact structural mirror of
    _verify_spec_lint except: driver path, subprocess flags, and rc=2 is
    graceful (not fail-closed) per issue #269.
    """
    step = "verify_spec_cite_lint"
    # 1. Validate prev.data
    if not isinstance(prev, StepResult) or not isinstance(prev.data, dict):
        return StepResult(
            status="error", data=None, duration_ms=0, step_name=step,
            error="prev step did not produce data", error_code="E_MISSING_PREV_DATA",
        )
    # frozen spec fast-path — spec already verified upstream; skip cite lint.
    if prev.data.get("is_frozen"):
        return StepResult(
            status="skip",
            data={**prev.data},
            duration_ms=0,
            step_name=step,
        )
    spec_path = prev.data.get("spec_path")
    if not spec_path:
        # No spec to lint — skip cleanly.
        return StepResult(
            status="ok",
            data={**prev.data, "spec_cite_lint_skipped": "no_spec_path"},
            duration_ms=0, step_name=step,
        )

    # Kill-switch: org_config["spec_cite_lint_skip"] skips before subprocess.
    # Default falsy ⇒ gate RUNS in production. AC6.
    if (ctx.org_config or {}).get("spec_cite_lint_skip"):
        _emit_safe("gate_disabled", {
            "gate": "spec_cite_lint",
            "step": step,
            "reason": "config_skip",
            "detail": "org_config.spec_cite_lint_skip",
        })
        return StepResult(
            status="ok",
            data={**prev.data, "spec_cite_lint_skipped": "config_skip"},
            duration_ms=0, step_name=step,
        )

    # 2. Locate driver (SYSTEM/cli/build/spec-cite-lint.py — outside engine_py)
    cite_path = Path(__file__).parent.parent.parent / "spec-cite-lint.py"
    if not cite_path.is_file():
        logger.warning("spec_cite_lint driver missing at %s — skipping", cite_path)
        _emit_safe("gate_disabled", {
            "gate": "spec_cite_lint",
            "step": step,
            "reason": "driver_missing",
            "detail": str(cite_path),
        })
        return StepResult(
            status="error",
            data={**prev.data},
            duration_ms=0, step_name=step,
            error=(
                f"spec_cite_lint driver missing at {cite_path} — bootstrap incomplete? "
                f"see bootstrap-manifest.txt"
            ),
            error_code="E_SPEC_CITE_LINT_UNAVAILABLE",
            recoverable=False,
        )

    # 3. Resolve hal_root
    cfg = ctx.org_config or {}
    # AE0F261A: checklist thread-through if not already in cfg
    if "checklist" not in cfg and isinstance(prev, StepResult) and isinstance(prev.data, dict):
        cfg = {**cfg, "checklist": prev.data.get("checklist", {})}
    _resolved_root, _resolver_source = resolve_project_root(cfg)
    hal_root = str(_resolved_root)

    # 4. Subprocess invoke (note --spec/--repo-root flags, not spec_lint's positional+--hal-root)
    # GH689: --json requests machine-readable structured output so the
    # engine can dispatch on finding status instead of line-count inflation.
    try:
        proc = bounded_run(
            [sys.executable, str(cite_path), "--spec", str(spec_path), "--repo-root", hal_root, "--json"],
            capture_output=True, text=True, timeout=15,
        )
    except FileNotFoundError as e:
        _emit_safe("gate_disabled", {
            "gate": "spec_cite_lint",
            "step": step,
            "reason": "python_exec_missing",
            "detail": str(e),
        })
        return StepResult(
            status="ok",
            data={**prev.data, "spec_cite_lint_skipped": "python_exec_missing", "detail": str(e)},
            duration_ms=0, step_name=step,
        )

    if proc.returncode == 124:
        return StepResult(
            status="error",
            data={**prev.data},
            duration_ms=0, step_name=step,
            error=f"spec_cite_lint exceeded 15s timeout on {spec_path}",
            error_code="E_SPEC_CITE_LINT_TIMEOUT",
            recoverable=False,
        )

    # 5. Parse output
    rc = proc.returncode

    if rc == 0:
        logger.info("spec_cite_lint: no findings for %s", spec_path)
        _emit_cite_status_telemetry(proc.stdout, spec_path, 0)
        return StepResult(
            status="ok",
            data={**prev.data, "spec_cite_lint_findings": []},
            duration_ms=0, step_name=step,
        )
    if rc == 1:
        _emit_cite_status_telemetry(proc.stdout, spec_path, 1)
        # GH689: parse --json stdout and keep only unresolved_symbol entries —
        # advisory statuses (new_symbol/planned_file/missing_file/resolved/
        # wrong_file) must NOT inflate the repair/error findings count.
        unresolved = _parse_cite_unresolved(proc.stdout)
        cite_findings = [
            {
                "path": str(spec_path),
                "line": None,
                "rule": "spec_cite_lint",
                "evidence": f"unresolved citation: symbol {u.get('symbol')!r} not found in {u.get('file')}",
            }
            for u in unresolved
        ]
        first = cite_findings[0]["evidence"] if cite_findings else "(no unresolved citation parsed)"
        # 457DC7DC GH371 §2.2: directed-repair pre-stage before the unchanged
        # terminal E_SPEC_CITE_LINT_FAIL return.
        if _directed_repair_enabled(ctx) and cite_findings:
            rr = attempt_directed_repair(
                gate="spec_cite_lint",
                artifact_path=str(spec_path),
                findings=cite_findings,
                rerun_gate=lambda: _verify_spec_cite_lint(ctx, prev),
                cheap_model=_resolve_directed_repair_model(cfg),
                repair_step_name="repair_spec_cite_lint",
                max_attempts=_repair_cap(cfg),
                ctx=ctx,
            )
            if rr.converged and rr.final is not None:
                return rr.final
        return StepResult(
            status="error",
            data={**prev.data, "spec_cite_lint_findings": cite_findings},
            duration_ms=0, step_name=step,
            error=(
                f"spec_cite_lint detected {len(cite_findings)} unresolved citation(s) in "
                f"{spec_path}; first: {first}"
            ),
            error_code="E_SPEC_CITE_LINT_FAIL",
            recoverable=False,
        )
    if rc == 2:
        # DELIBERATE DIVERGENCE from _verify_spec_lint: graceful (not fail-closed).
        # rc=2 = usage error (missing/unreadable --spec); spec_path is already
        # non-empty-guarded above, so this is near-impossible in a healthy freeze.
        # Hard enforcement comes from rc=1 + the kill-switch. Issue #269.
        logger.warning(
            "spec_cite_lint usage error (rc=2) on %s — skipping gracefully; stderr: %s",
            spec_path, (proc.stderr or "")[-200:],
        )
        _emit_safe("gate_disabled", {
            "gate": "spec_cite_lint",
            "step": step,
            "reason": "driver_error",
            "detail": (proc.stderr or "")[-200:],
        })
        return StepResult(
            status="error", data={**prev.data}, duration_ms=0, step_name=step,
            error=f"spec_cite_lint driver usage error (rc=2); stderr={(proc.stderr or '')[-200:]}",
            error_code="E_SPEC_CITE_LINT_UNEXPECTED_RC", recoverable=False,
        )
    # Unexpected rc
    return StepResult(
        status="error",
        data={**prev.data},
        duration_ms=0, step_name=step,
        error=f"spec_cite_lint returned unexpected rc={rc}; stderr={(proc.stderr or '')[-200:]}",
        error_code="E_SPEC_CITE_LINT_UNEXPECTED_RC",
        recoverable=False,
    )


# ─── GH747: phase_4.5 spec-gate pre-flight batch aggregator ─────────────────


def _collect_spec_gate_findings(
    ctx: WorkflowContext,
    prev: Any,
    spec_path: str,
    hal_root: str,
    cfg: dict[str, Any],
    git_cwd: str,
) -> list[dict[str, Any]]:
    """§1aa named sourceable helper. Runs the spec_lint driver then the
    spec_cite_lint driver — NEVER early-returns between them — and returns
    the combined, normalized 6-key finding list (GH595 schema)."""
    findings: list[dict[str, Any]] = []

    # 1. spec_lint driver.
    lint_path = Path(__file__).parent.parent / "scripts" / "spec_lint" / "lint_spec.py"
    try:
        lint_proc = bounded_run(
            [sys.executable, str(lint_path), str(spec_path), "--hal-root", hal_root],
            capture_output=True, text=True, timeout=15,
        )
    except FileNotFoundError:
        lint_proc = None
    if lint_proc is not None:
        if lint_proc.returncode == 1:
            lines = [ln.strip() for ln in (lint_proc.stdout or "").splitlines() if ln.strip()]
            if not lines:
                lines = ["(no findings text)"]
            for ln in lines:
                findings.append({
                    "path": str(spec_path),
                    "line": None,
                    "rule": "spec-lint",
                    "evidence": ln,
                    "error_code": "E_SPEC_LINT_FAIL",
                    "recoverable": False,
                })
        elif lint_proc.returncode == 2:
            # Fail CLOSED — a broken driver is a violation, not a silent pass.
            findings.append({
                "path": str(spec_path),
                "line": None,
                "rule": "spec-lint",
                "evidence": "driver-error rc=2",
                "error_code": "E_SPEC_LINT_FAIL",
                "recoverable": False,
            })

    # 2. spec_cite_lint driver — never early-returns before this.
    cite_path = Path(__file__).parent.parent.parent / "spec-cite-lint.py"
    try:
        cite_proc = bounded_run(
            [sys.executable, str(cite_path), "--spec", str(spec_path), "--repo-root", hal_root, "--json"],
            capture_output=True, text=True, timeout=15,
        )
    except FileNotFoundError:
        cite_proc = None
    if cite_proc is not None and cite_proc.returncode == 1:
        for u in _parse_cite_unresolved(cite_proc.stdout):
            findings.append({
                "path": str(spec_path),
                "line": None,
                "rule": "spec-cite-lint",
                "evidence": str(u.get("symbol")),
                "error_code": "E_SPEC_CITE_LINT_FAIL",
                "recoverable": False,
            })
    elif cite_proc is not None and cite_proc.returncode == 2:
        findings.append({
            "path": str(spec_path),
            "line": None,
            "rule": "spec-cite-lint",
            "evidence": "driver_error",
            "error_code": "E_SPEC_CITE_LINT_UNEXPECTED_RC",
            "recoverable": False,
        })

    return findings


def _verify_spec_preflight_batch(ctx: WorkflowContext, prev: Any) -> StepResult:
    """GH747: single pre-flight pass batching the two phase_4.5 spec hard
    gates (spec_lint + spec_cite_lint) into ONE collect-then-report step,
    mirroring GH595's RED-lint batch. Default-OFF opt-in rollout — when the
    flag is off, this is a pure no-op passthrough and the legacy staged
    steps 8 & 9 remain the enforcement path."""
    step = "verify_spec_preflight_batch"
    if not isinstance(prev, StepResult) or not isinstance(prev.data, dict):
        return StepResult(
            status="error", data=None, duration_ms=0, step_name=step,
            error="prev step did not produce data", error_code="E_MISSING_PREV_DATA",
        )

    # GH747 rollout flip-by:2026-08-13
    if not get_config().flag("HAL_SPEC_PREFLIGHT_BATCH"):
        _emit_safe("gate_disabled", {
            "gate": "HAL_SPEC_PREFLIGHT_BATCH",
            "step": step,
            "reason": "opt_in_default_off",
        })
        return StepResult(status="ok", data={**prev.data}, duration_ms=0, step_name=step)

    spec_path = prev.data.get("spec_path")
    if not spec_path:
        return StepResult(
            status="ok",
            data={**prev.data, "spec_preflight_batch_skipped": "no_spec_path"},
            duration_ms=0, step_name=step,
        )

    cfg = ctx.org_config or {}
    if "checklist" not in cfg and isinstance(prev, StepResult) and isinstance(prev.data, dict):
        cfg = {**cfg, "checklist": prev.data.get("checklist", {})}
    _resolved_root, _resolver_source = resolve_project_root(cfg)
    hal_root = str(_resolved_root)
    git_cwd = resolve_git_cwd(cfg, prev.data)

    findings = _collect_spec_gate_findings(ctx, prev, str(spec_path), hal_root, cfg, git_cwd)
    codes = list(dict.fromkeys(f["error_code"] for f in findings))
    gates_fired = list(dict.fromkeys(f["rule"] for f in findings))
    _emit_safe("spec_preflight_gate_batch", {
        "phase": "4.5",
        "step": step,
        "codes": codes,
        "gates_fired": gates_fired,
        "findings_n": len(findings),
        "has_findings": bool(findings),
    })

    if not findings:
        return StepResult(status="ok", data={**prev.data}, duration_ms=0, step_name=step)

    if _directed_repair_enabled(ctx):
        rr = attempt_directed_repair(
            gate="spec_preflight_batch",
            artifact_path=str(spec_path),
            findings=findings,
            rerun_gate=lambda: _verify_spec_preflight_batch(ctx, prev),
            cheap_model=_resolve_directed_repair_model(cfg),
            repair_step_name="repair_spec_preflight",
            max_attempts=_repair_cap(cfg),
            ctx=ctx,
        )
        if rr.converged and rr.final is not None:
            return rr.final

    return StepResult(
        status="error",
        data={"findings": findings},
        duration_ms=0, step_name=step,
        error=(
            f"spec_preflight_batch detected {len(findings)} finding(s) across "
            f"{len(gates_fired)} gate(s) in {spec_path}"
        ),
        error_code="E_SPEC_PREFLIGHT_BATCH",
        recoverable=False,
    )


# ─── GH675A: in-phase deterministic cite pre-lint (warn-only, non-blocking) ──


def _inphase_unresolved_symbols(spec_path: Path, repo_root: Path) -> list[str]:
    """Pure helper (§1aa): sorted, de-duplicated unresolved-symbol citations
    in ``spec_path`` per ``spec_cite.lint_spec``. No I/O beyond the lint call
    itself; no emits, no side effects."""
    _rc, findings = lint_spec(spec_path, repo_root)
    return sorted({f.symbol for f in findings if f.status == "unresolved_symbol"})


def _format_prelint_cite_directive(syms: list[str]) -> str:
    """Pure helper (§1aa): self-labeling CITE-GROUNDING PRE-LINT directive
    (GH675/GH681) for the bounded spec-cite re-prompt. No I/O, no emits."""
    lines = [
        "CITE-GROUNDING PRE-LINT (GH675/GH681): the following symbol(s) "
        "cited in the spec could not be grounded against the repo:",
    ]
    for sym in syms:
        lines.append(f"  - {sym}")
    lines.append(
        "For each symbol above, either (a) correct the citation to an "
        "existing repo location, or (b) explicitly mark it as NEW "
        "(to-be-created by this build) in the spec text."
    )
    return "\n".join(lines)


def _verify_spec_cite_prelint(ctx: WorkflowContext, prev: Any) -> StepResult:
    """GH675A Layer 2: deterministic in-phase pre-lint of the just-written
    spec for bare-symbol grounding, reusing spec_cite.lint_spec directly (no
    subprocess). Default is warn-only telemetry (never blocks). GH681 adds an
    opt-in bounded 1x writer re-prompt behind HAL_SPEC_CITE_PRELINT_ENFORCE
    (default OFF): on unresolved>0 it returns ONE recoverable gate
    (status="error", recoverable) via gate "spec_cite_prelint", then advances
    (warn) on cap-exhaustion. Terminal cite enforcement remains owned by
    _verify_spec_cite_lint (window B / #674)."""
    step = "verify_spec_cite_prelint"
    data = prev.data if hasattr(prev, "data") else prev

    # Frozen fast-path — spec already verified upstream; skip pre-lint entirely.
    if data.get("is_frozen"):
        return StepResult(
            status="skip",
            data={**data},
            duration_ms=0, step_name=step,
        )

    # Kill-switch: org_config["spec_cite_prelint_skip"] skips before lint.
    if (ctx.org_config or {}).get("spec_cite_prelint_skip"):
        return StepResult(
            status="ok",
            data={**data, "spec_cite_prelint_skipped": "config_skip"},
            duration_ms=0, step_name=step,
        )

    spec_path = data.get("spec_path")
    if not spec_path:
        # No spec to lint — skip cleanly (defensive, mirrors sibling gate).
        return StepResult(
            status="ok",
            data={**data, "spec_cite_prelint_skipped": "no_spec_path"},
            duration_ms=0, step_name=step,
        )

    cfg = ctx.org_config or {}
    root, _source = resolve_project_root(cfg)
    syms = _inphase_unresolved_symbols(Path(spec_path), root)

    try:
        spec_bytes = Path(spec_path).stat().st_size
    except OSError:
        spec_bytes = 0

    _emit_safe("spec_cite_prelint_result", {
        "unresolved_count": len(syms),
        "symbols": syms,
        "spec_bytes": spec_bytes,
    })

    if not syms:
        return StepResult(
            status="ok",
            data={**data, "spec_cite_prelint_clean": True},
            duration_ms=0, step_name=step,
        )

    _emit_safe("spec_cite_prelint_warn", {
        "unresolved_count": len(syms),
        "symbols": syms,
    })

    cycle = int(data.get("cycle", 1))
    if get_config().flag("HAL_SPEC_CITE_PRELINT_ENFORCE"):  # default OFF — flip-by:2026-07-26 Refs #681
        build_class = (ctx.org_config or {}).get("complexity", "SIMPLE").upper()
        fwd = {**data, "spec_cite_prelint_unresolved": syms,
               "findings": _format_prelint_cite_directive(syms)}
        sr: StepResult = RecoverableGateMixin.gated_step_result(
            build_class=build_class,
            gate="spec_cite_prelint",
            cycle=cycle,
            retry_from_step_idx=0,
            error_code="E_SPEC_CITE_PRELINT_RETRY",
            error_msg="spec cites ungrounded symbol(s); bounded cite-grounding re-prompt",
            step_name=step,
            forwarded_data=fwd,
        )
        if sr.recoverable:
            _emit_safe("spec_cite_prelint_enforce_retry", {
                "unresolved_count": len(syms),
                "symbols": syms,
                "cycle": cycle,
            })
            return sr
        else:
            _emit_safe("spec_cite_prelint_enforce_exhausted", {
                "unresolved_count": len(syms),
                "symbols": syms,
                "cycle": cycle,
            })

    return StepResult(
        status="ok",
        data={**data, "spec_cite_prelint_unresolved": syms},
        duration_ms=0, step_name=step,
    )


# ─── EECB919C §1v: scope-inverse gate (pre-Opus, recoverable) ────────────────


def _verify_spec_scope_inverse(ctx: WorkflowContext, prev: Any) -> StepResult:
    # EECB919C §1v — wires scope_inverse.scan_scope_inverse into phase_4.5 pipeline
    step = "verify_spec_scope_inverse"
    if not isinstance(prev, StepResult) or not isinstance(prev.data, dict):
        return StepResult(
            status="error", data=None, duration_ms=0, step_name=step,
            error="prev step did not produce data", error_code="E_MISSING_PREV_DATA",
            recoverable=False,
        )
    if prev.data.get("is_frozen"):
        return StepResult(status="skip", data={**prev.data}, duration_ms=0, step_name=step)
    if not get_config().gate_enabled("HAL_SPEC_SCOPE_GATE"):
        _emit_safe("gate_disabled", {
            "gate": "HAL_SPEC_SCOPE_GATE",
            "step": step,
            "reason": "env_kill_switch",
        })
        return StepResult(
            status="ok",
            data={**prev.data, "spec_scope_inverse_skipped": "env_skip"},
            duration_ms=0, step_name=step,
        )
    if "spec_path" not in prev.data:
        return StepResult(
            status="error", data=None, duration_ms=0, step_name=step,
            error="prev.data missing 'spec_path'", error_code="E_MISSING_PREV_DATA",
            recoverable=False,
        )
    spec_path = Path(prev.data["spec_path"])
    cycle = int(prev.data.get("cycle", 1))
    build_class = (ctx.org_config or {}).get("complexity", "SIMPLE").upper()
    if not spec_path.is_file():
        return cast(StepResult, RecoverableGateMixin.gated_step_result(
            build_class=build_class,
            gate="spec_retry",
            cycle=cycle,
            retry_from_step_idx=0,
            error_code="E_SPEC_FILE_MISSING",
            error_msg=f"spec file not on disk: {spec_path}",
            step_name=step,
            forwarded_data={**prev.data, "missing_path": str(spec_path)},
            terminal_error_code="E_SPEC_FILE_MISSING",
        ))
    spec_text = spec_path.read_text(encoding="utf-8-sig")
    findings = scan_scope_inverse(spec_text)
    if findings:
        rendered = "; ".join(f"L{f['line']}: {f['reason']}" for f in findings)
        _emit_safe("spec_scope_inverse_violation", {"count": len(findings), "spec_path": str(spec_path)})
        # 457DC7DC GH371 §2.2: directed-repair pre-stage before the unchanged
        # recoverable-regen return.
        _cfg_si = ctx.org_config or {}
        dr_findings = [
            {"path": str(spec_path), "line": f.get("line"),
             "rule": "spec_scope_inverse", "evidence": f.get("reason")}
            for f in findings
        ]
        if _directed_repair_enabled(ctx) and dr_findings:
            rr = attempt_directed_repair(
                gate="spec_scope_inverse",
                artifact_path=str(spec_path),
                findings=dr_findings,
                rerun_gate=lambda: _verify_spec_scope_inverse(ctx, prev),
                cheap_model=_resolve_directed_repair_model(_cfg_si),
                repair_step_name="repair_spec_scope_inverse",
                max_attempts=_repair_cap(_cfg_si),
                ctx=ctx,
            )
            if rr.converged and rr.final is not None:
                return rr.final
        return cast(StepResult, RecoverableGateMixin.gated_step_result(
            build_class=build_class,
            gate="spec_retry",
            cycle=cycle,
            retry_from_step_idx=0,
            error_code="E_SPEC_SCOPE_INVERSE",
            error_msg=rendered,
            step_name=step,
            forwarded_data={**prev.data, "findings": rendered, "findings_structured": findings},
            terminal_error_code="E_SPEC_SCOPE_INVERSE_FATAL",
        ))
    return StepResult(
        status="ok",
        data={**prev.data, "spec_scope_inverse_findings": []},
        duration_ms=0, step_name=step,
    )


def _verify_spec_reentry(ctx: WorkflowContext, prev: Any) -> StepResult:
    # GH823 §1ab/§1ac — wires reentry_ac.scan_reentry_ac into phase_4.5
    # pipeline, immediately after verify_spec_scope_inverse.
    step = "verify_spec_reentry"
    if not isinstance(prev, StepResult) or not isinstance(prev.data, dict):
        return StepResult(
            status="error", data=None, duration_ms=0, step_name=step,
            error="prev step did not produce data", error_code="E_MISSING_PREV_DATA",
            recoverable=False,
        )
    if prev.data.get("is_frozen"):
        return StepResult(status="skip", data={**prev.data}, duration_ms=0, step_name=step)
    if not get_config().gate_enabled("HAL_SPEC_REENTRY_GATE"):
        _emit_safe("gate_disabled", {
            "gate": "HAL_SPEC_REENTRY_GATE",
            "step": step,
            "reason": "env_kill_switch",
        })
        return StepResult(
            status="ok",
            data={**prev.data, "spec_reentry_skipped": "env_skip"},
            duration_ms=0, step_name=step,
        )
    if "spec_path" not in prev.data:
        return StepResult(
            status="error", data=None, duration_ms=0, step_name=step,
            error="prev.data missing 'spec_path'", error_code="E_MISSING_PREV_DATA",
            recoverable=False,
        )
    spec_path = Path(prev.data["spec_path"])
    cycle = int(prev.data.get("cycle", 1))
    build_class = (ctx.org_config or {}).get("complexity", "SIMPLE").upper()
    if not spec_path.is_file():
        return cast(StepResult, RecoverableGateMixin.gated_step_result(
            build_class=build_class,
            gate="spec_retry",
            cycle=cycle,
            retry_from_step_idx=0,
            error_code="E_SPEC_FILE_MISSING",
            error_msg=f"spec file not on disk: {spec_path}",
            step_name=step,
            forwarded_data={**prev.data, "missing_path": str(spec_path)},
            terminal_error_code="E_SPEC_FILE_MISSING",
        ))
    spec_text = spec_path.read_text(encoding="utf-8-sig")
    findings = scan_reentry_ac(spec_text)
    if not findings:
        return StepResult(
            status="ok",
            data={**prev.data, "spec_reentry_findings": []},
            duration_ms=0, step_name=step,
        )
    enforce = get_config().flag("HAL_SPEC_REENTRY_ENFORCE")
    _emit_safe("spec_reentry_violation", {
        "count": len(findings),
        "spec_path": str(spec_path),
        "enforce": bool(enforce),
    })
    # GH823 warn-mode rollout — flip-by:2026-07-29 (issue #823)
    if not enforce:
        return StepResult(
            status="ok",
            data={**prev.data, "spec_reentry_findings": findings},
            duration_ms=0, step_name=step,
        )
    rendered = "; ".join(f"L{f['line']}: {f['reason']}" for f in findings)
    _cfg_ra = ctx.org_config or {}
    dr_findings = [
        {"path": str(spec_path), "line": f.get("line"),
         "rule": f.get("rule"), "evidence": f.get("reason")}
        for f in findings
    ]
    if _directed_repair_enabled(ctx) and dr_findings:
        rr = attempt_directed_repair(
            gate="spec_reentry",
            artifact_path=str(spec_path),
            findings=dr_findings,
            rerun_gate=lambda: _verify_spec_reentry(ctx, prev),
            cheap_model=_resolve_directed_repair_model(_cfg_ra),
            repair_step_name="repair_spec_reentry",
            max_attempts=_repair_cap(_cfg_ra),
            ctx=ctx,
        )
        if rr.converged and rr.final is not None:
            return rr.final
    return cast(StepResult, RecoverableGateMixin.gated_step_result(
        build_class=build_class,
        gate="spec_retry",
        cycle=cycle,
        retry_from_step_idx=0,
        error_code="E_SPEC_REENTRY",
        error_msg=rendered,
        step_name=step,
        forwarded_data={**prev.data, "findings": rendered, "findings_structured": findings},
        terminal_error_code="E_SPEC_REENTRY_FATAL",
    ))


def _verify_spec_helper_extraction(ctx: WorkflowContext, prev: Any) -> StepResult:
    # GH863 §1aa — wires helper_extraction.scan_helper_extraction into phase_4.5
    # pipeline, immediately after verify_spec_reentry.
    step = "verify_spec_helper_extraction"
    if not isinstance(prev, StepResult) or not isinstance(prev.data, dict):
        return StepResult(
            status="error", data=None, duration_ms=0, step_name=step,
            error="prev step did not produce data", error_code="E_MISSING_PREV_DATA",
            recoverable=False,
        )
    if prev.data.get("is_frozen"):
        return StepResult(status="skip", data={**prev.data}, duration_ms=0, step_name=step)
    if not get_config().gate_enabled("HAL_SPEC_HELPER_EXTRACTION_GATE"):
        _emit_safe("gate_disabled", {
            "gate": "HAL_SPEC_HELPER_EXTRACTION_GATE",
            "step": step,
            "reason": "env_kill_switch",
        })
        return StepResult(
            status="ok",
            data={**prev.data, "spec_helper_extraction_skipped": "env_skip"},
            duration_ms=0, step_name=step,
        )
    if "spec_path" not in prev.data:
        return StepResult(
            status="error", data=None, duration_ms=0, step_name=step,
            error="prev.data missing 'spec_path'", error_code="E_MISSING_PREV_DATA",
            recoverable=False,
        )
    spec_path = Path(prev.data["spec_path"])
    cycle = int(prev.data.get("cycle", 1))
    build_class = (ctx.org_config or {}).get("complexity", "SIMPLE").upper()
    if not spec_path.is_file():
        return cast(StepResult, RecoverableGateMixin.gated_step_result(
            build_class=build_class,
            gate="spec_retry",
            cycle=cycle,
            retry_from_step_idx=0,
            error_code="E_SPEC_FILE_MISSING",
            error_msg=f"spec file not on disk: {spec_path}",
            step_name=step,
            forwarded_data={**prev.data, "missing_path": str(spec_path)},
            terminal_error_code="E_SPEC_FILE_MISSING",
        ))
    spec_text = spec_path.read_text(encoding="utf-8-sig")
    findings = scan_helper_extraction(spec_text)
    if not findings:
        return StepResult(
            status="ok",
            data={**prev.data, "spec_helper_extraction_findings": []},
            duration_ms=0, step_name=step,
        )
    enforce = get_config().flag("HAL_SPEC_HELPER_EXTRACTION_ENFORCE")
    _emit_safe("spec_helper_extraction_violation", {
        "count": len(findings),
        "spec_path": str(spec_path),
        "enforce": bool(enforce),
    })
    # warn-only rollout: HAL_SPEC_HELPER_EXTRACTION_ENFORCE default OFF — flip-by:2026-07-29 Refs #863 (rollout-completion-check token)
    if not enforce:
        return StepResult(
            status="ok",
            data={**prev.data, "spec_helper_extraction_findings": findings},
            duration_ms=0, step_name=step,
        )
    rendered = "; ".join(f"L{f['line']}: {f['construct']} — {f['text']}" for f in findings)
    _cfg_he = ctx.org_config or {}
    dr_findings = [
        {"path": str(spec_path), "line": f.get("line"),
         "rule": f.get("construct"), "evidence": f.get("text")}
        for f in findings
    ]
    if _directed_repair_enabled(ctx) and dr_findings:
        rr = attempt_directed_repair(
            gate="spec_helper_extraction",
            artifact_path=str(spec_path),
            findings=dr_findings,
            rerun_gate=lambda: _verify_spec_helper_extraction(ctx, prev),
            cheap_model=_resolve_directed_repair_model(_cfg_he),
            repair_step_name="repair_spec_helper_extraction",
            max_attempts=_repair_cap(_cfg_he),
            ctx=ctx,
        )
        if rr.converged and rr.final is not None:
            return rr.final
    return cast(StepResult, RecoverableGateMixin.gated_step_result(
        build_class=build_class,
        gate="spec_retry",
        cycle=cycle,
        retry_from_step_idx=0,
        error_code="E_SPEC_HELPER_EXTRACTION",
        error_msg=rendered,
        step_name=step,
        forwarded_data={**prev.data, "findings": rendered, "findings_structured": findings},
        terminal_error_code="E_SPEC_HELPER_EXTRACTION_FATAL",
    ))


# ─── EECB919C §1w: spec-coverage gate (pre-Opus, recoverable) ────────────────


def _verify_spec_coverage(ctx: WorkflowContext, prev: Any) -> StepResult:
    # EECB919C §1w — wires spec_coverage.scan_spec_coverage into phase_4.5 pipeline
    step = "verify_spec_coverage"
    if not isinstance(prev, StepResult) or not isinstance(prev.data, dict):
        return StepResult(
            status="error", data=None, duration_ms=0, step_name=step,
            error="prev step did not produce data", error_code="E_MISSING_PREV_DATA",
            recoverable=False,
        )
    if prev.data.get("is_frozen"):
        return StepResult(status="skip", data={**prev.data}, duration_ms=0, step_name=step)
    if not get_config().gate_enabled("HAL_SPEC_COVERAGE_GATE"):
        _emit_safe("gate_disabled", {
            "gate": "HAL_SPEC_COVERAGE_GATE",
            "step": step,
            "reason": "env_kill_switch",
        })
        return StepResult(
            status="ok",
            data={**prev.data, "spec_coverage_skipped": "env_skip"},
            duration_ms=0, step_name=step,
        )
    if "spec_path" not in prev.data:
        return StepResult(
            status="error", data=None, duration_ms=0, step_name=step,
            error="prev.data missing 'spec_path'", error_code="E_MISSING_PREV_DATA",
            recoverable=False,
        )
    spec_path = Path(prev.data["spec_path"])
    cycle = int(prev.data.get("cycle", 1))
    build_class = (ctx.org_config or {}).get("complexity", "SIMPLE").upper()
    if not spec_path.is_file():
        return cast(StepResult, RecoverableGateMixin.gated_step_result(
            build_class=build_class,
            gate="spec_retry",
            cycle=cycle,
            retry_from_step_idx=0,
            error_code="E_SPEC_FILE_MISSING",
            error_msg=f"spec file not on disk: {spec_path}",
            step_name=step,
            forwarded_data={**prev.data, "missing_path": str(spec_path)},
            terminal_error_code="E_SPEC_FILE_MISSING",
        ))
    spec_text = spec_path.read_text(encoding="utf-8-sig")
    findings = scan_spec_coverage(spec_text)
    if findings:
        rendered = "; ".join(f"L{f['line']}: op '{f['op']}' {f['reason']}" for f in findings)
        _emit_safe("spec_coverage_violation", {"count": len(findings), "spec_path": str(spec_path)})
        # 457DC7DC GH371 §2.2: directed-repair pre-stage before the unchanged
        # recoverable-regen return.
        _cfg_cov = ctx.org_config or {}
        dr_findings = [
            {"path": str(spec_path), "line": f.get("line"),
             "rule": "spec_coverage",
             "evidence": f"op '{f.get('op')}' {f.get('reason')}"}
            for f in findings
        ]
        if _directed_repair_enabled(ctx) and dr_findings:
            rr = attempt_directed_repair(
                gate="spec_coverage",
                artifact_path=str(spec_path),
                findings=dr_findings,
                rerun_gate=lambda: _verify_spec_coverage(ctx, prev),
                cheap_model=_resolve_directed_repair_model(_cfg_cov),
                repair_step_name="repair_spec_coverage",
                max_attempts=_repair_cap(_cfg_cov),
                ctx=ctx,
            )
            if rr.converged and rr.final is not None:
                return rr.final
        return cast(StepResult, RecoverableGateMixin.gated_step_result(
            build_class=build_class,
            gate="spec_retry",
            cycle=cycle,
            retry_from_step_idx=0,
            error_code="E_SPEC_COVERAGE",
            error_msg=rendered,
            step_name=step,
            forwarded_data={**prev.data, "findings": rendered, "findings_structured": findings},
            terminal_error_code="E_SPEC_COVERAGE_FATAL",
        ))
    # GH824 warn-mode rollout — flip-by:2026-07-29 (issue #824)
    if get_config().gate_enabled("HAL_SPEC_TAXONOMY_GATE"):
        tax = scan_error_taxonomy(spec_text, ERROR_CODES.keys())
        if tax:
            _emit_safe("spec_taxonomy_violation", {"count": len(tax), "spec_path": str(spec_path)})
    else:
        tax = []
        _emit_safe("gate_disabled", {"gate": "HAL_SPEC_TAXONOMY_GATE", "step": step, "reason": "env_kill_switch"})

    if get_config().gate_enabled("HAL_SPEC_FINALIZE_XCHECK_GATE"):
        fin = scan_finalize_coverage(spec_text)
        if fin:
            _emit_safe("spec_finalize_xcheck_violation", {"count": len(fin), "spec_path": str(spec_path)})
    else:
        fin = []
        _emit_safe("gate_disabled", {"gate": "HAL_SPEC_FINALIZE_XCHECK_GATE", "step": step, "reason": "env_kill_switch"})

    return StepResult(
        status="ok",
        data={
            **prev.data,
            "spec_coverage_findings": [],
            "spec_taxonomy_findings": tax,
            "spec_finalize_findings": fin,
        },
        duration_ms=0, step_name=step,
    )


# ─── GH559: spec-lint batch gate (GH540 wiring, warn-only) ──────────────────


def _verify_spec_lint_batch(ctx: WorkflowContext, prev: Any) -> StepResult:
    # GH559 — wires token_consistency/presence_triad/format_conversion_cases
    # (GH540) into phase_4.5 pipeline as ONE warn-only batch step.
    step = "verify_spec_lint_batch"
    if not isinstance(prev, StepResult) or not isinstance(prev.data, dict):
        return StepResult(
            status="error", data=None, duration_ms=0, step_name=step,
            error="prev step did not produce data", error_code="E_MISSING_PREV_DATA",
            recoverable=False,
        )
    if prev.data.get("is_frozen"):
        return StepResult(status="skip", data={**prev.data}, duration_ms=0, step_name=step)
    if not get_config().gate_enabled("HAL_SPEC_LINT_BATCH_GATE"):
        _emit_safe("gate_disabled", {
            "gate": "HAL_SPEC_LINT_BATCH_GATE",
            "step": step,
            "reason": "env_kill_switch",
        })
        return StepResult(
            status="ok",
            data={**prev.data, "spec_lint_batch_skipped": "env_skip"},
            duration_ms=0, step_name=step,
        )
    if "spec_path" not in prev.data:
        return StepResult(
            status="error", data=None, duration_ms=0, step_name=step,
            error="prev.data missing 'spec_path'", error_code="E_MISSING_PREV_DATA",
            recoverable=False,
        )
    spec_path = Path(prev.data["spec_path"])
    cycle = int(prev.data.get("cycle", 1))
    build_class = (ctx.org_config or {}).get("complexity", "SIMPLE").upper()
    if not spec_path.is_file():
        # §1n divergence: this gate OWNS no error code on missing-file — the
        # warn-only rollout must never brick; upstream completeness/scope
        # gates already fail hard on a missing spec.
        _emit_safe("spec_lint_batch_warn", {
            "reason": "spec_file_missing",
            "spec_path": str(spec_path),
        })
        return StepResult(
            status="ok",
            data={**prev.data, "spec_lint_batch_skipped": "spec_file_missing"},
            duration_ms=0, step_name=step,
        )
    spec_text = spec_path.read_text(encoding="utf-8-sig")
    findings = [
        {**f, "lint": name}
        for name, scanner in (
            ("token-consistency-lint", scan_token_consistency),
            ("presence-triad-lint", scan_presence_triad),
            ("format-conversion-lint", scan_format_conversion),
        )
        for f in scanner(spec_text)
    ]
    if not findings:
        return StepResult(
            status="ok",
            data={**prev.data, "spec_lint_batch_findings": []},
            duration_ms=0, step_name=step,
        )
    _emit_safe("spec_lint_batch_warn", {
        "count": len(findings),
        "rules": sorted({f["rule_id"] for f in findings}),
        "spec_path": str(spec_path),
    })
    # warn-only rollout: HAL_SPEC_LINT_BATCH_ENFORCE default OFF — flip-by:2026-07-24 Refs #559 (rollout-completion-check token)
    if get_config().flag("HAL_SPEC_LINT_BATCH_ENFORCE"):
        rendered = "; ".join(
            f"L{f['line']} {f['rule_id']}: {f['evidence']}" for f in findings
        )
        return cast(StepResult, RecoverableGateMixin.gated_step_result(
            build_class=build_class,
            gate="spec_retry",
            cycle=cycle,
            retry_from_step_idx=0,
            error_code="E_SPEC_LINT_BATCH",
            error_msg=rendered,
            step_name=step,
            forwarded_data={**prev.data, "findings_structured": findings},
            terminal_error_code="E_SPEC_LINT_BATCH_FATAL",
        ))
    return StepResult(
        status="ok",
        data={
            **prev.data,
            "spec_lint_batch_findings": findings,
            "spec_lint_batch_warn_count": len(findings),
        },
        duration_ms=0, step_name=step,
    )


# ─── GH517 A2: AC-DSL admission wiring (A1 ac_dsl.admit, warn-only) ─────────


def _verify_spec_ac_dsl(ctx: WorkflowContext, prev: Any) -> StepResult:
    # GH517 A2 — wires ac_dsl.admit() (A1) into phase_4.5 as a warn-only step.
    step = "verify_spec_ac_dsl"
    if not isinstance(prev, StepResult) or not isinstance(prev.data, dict):
        return StepResult(
            status="error", data=None, duration_ms=0, step_name=step,
            error="prev step did not produce data", error_code="E_MISSING_PREV_DATA",
            recoverable=False,
        )
    if "spec_path" not in prev.data:
        return StepResult(
            status="error", data=None, duration_ms=0, step_name=step,
            error="prev.data missing 'spec_path'", error_code="E_MISSING_PREV_DATA",
            recoverable=False,
        )
    if not get_config().gate_enabled("HAL_AC_DSL_GATE"):
        _emit_safe("gate_disabled", {
            "gate": "HAL_AC_DSL_GATE",
            "step": step,
            "reason": "env_kill_switch",
        })
        return StepResult(
            status="ok",
            data={**prev.data, "spec_ac_dsl_skipped": "env_skip"},
            duration_ms=0, step_name=step,
        )
    spec_path = Path(prev.data["spec_path"])
    cycle = int(prev.data.get("cycle", 1))
    build_class = (ctx.org_config or {}).get("complexity", "SIMPLE").upper()
    spec_text = spec_path.read_text(encoding="utf-8-sig")
    try:
        report = ac_dsl.admit(spec_text)
    except Exception as e:  # noqa: BLE001 — driver isolation, fail-open in warn phase
        _emit_safe("spec_ac_dsl_driver_error", {"error": str(e), "spec_path": str(spec_path)})
        if get_config().flag("HAL_AC_DSL_GATE_ENFORCE"):
            return cast(StepResult, RecoverableGateMixin.gated_step_result(
                build_class=build_class,
                gate="spec_retry",
                cycle=cycle,
                retry_from_step_idx=0,
                error_code="E_SPEC_AC_UNCOMPILABLE",
                error_msg=f"ac_dsl.admit() driver error: {e}",
                step_name=step,
                forwarded_data={**prev.data, "findings_structured": [{
                    "line": 0, "rule_id": "ac-dsl/driver-error", "evidence": str(e), "lint": "ac-dsl",
                }]},
                terminal_error_code="E_SPEC_AC_UNCOMPILABLE",
            ))
        return StepResult(
            status="ok",
            data={**prev.data, "spec_ac_dsl_driver_error": str(e)},
            duration_ms=0, step_name=step,
        )
    if report.result == "ACCEPT":
        _emit_safe("spec_ac_dsl_ok", {"ac_count": len(report.per_ac)})
        return StepResult(
            status="ok",
            data={**prev.data, "spec_ac_dsl_findings": []},
            duration_ms=0, step_name=step,
        )
    agg = list(report.reasons) + [
        f"{ac_id}: {info['reason']}"
        for ac_id, info in report.per_ac.items()
        if info.get("status") == "REJECT"
    ]
    _emit_safe("spec_ac_dsl_warn", {
        "reasons": agg,
        "spec_path": str(spec_path),
    })
    if get_config().flag("HAL_AC_DSL_GATE_ENFORCE"):  # flip-by:2026-07-25 Refs #517
        findings = [
            {"line": 0, "rule_id": "ac-dsl/check", "evidence": reason, "lint": "ac-dsl"}
            for reason in agg
        ]
        rendered = "; ".join(agg)
        # GH634 §2: directed-repair pre-stage before the UNCHANGED terminal.
        cfg = ctx.org_config or {}
        dr_findings = [
            {"path": str(spec_path), "line": None, "rule": "spec_ac_dsl",
             "evidence": f"{ac_id}: {info['reason']}"}
            for ac_id, info in report.per_ac.items()
            if info.get("status") == "REJECT"
        ]
        if _directed_repair_enabled(ctx) and dr_findings:
            rr = attempt_directed_repair(
                gate="spec_ac_dsl",
                artifact_path=str(spec_path),
                findings=dr_findings,
                rerun_gate=lambda: _verify_spec_ac_dsl(ctx, prev),
                cheap_model=_resolve_directed_repair_model(cfg),
                repair_step_name="repair_spec_ac_dsl",
                max_attempts=_repair_cap(cfg),
                ctx=ctx,
            )
            if rr.converged and rr.final is not None:
                return rr.final
        return cast(StepResult, RecoverableGateMixin.gated_step_result(
            build_class=build_class,
            gate="spec_retry",
            cycle=cycle,
            retry_from_step_idx=0,
            error_code="E_SPEC_AC_UNCOMPILABLE",
            error_msg=rendered,
            step_name=step,
            forwarded_data={**prev.data, "findings_structured": findings},
            terminal_error_code="E_SPEC_AC_UNCOMPILABLE",
        ))
    return StepResult(
        status="ok",
        data={**prev.data, "spec_ac_dsl_findings": agg},
        duration_ms=0, step_name=step,
    )


# ─── DBA495CF: spec citation lint (pre-Opus reviewer short-circuit) ──────────


# Match `<path>:<line>` citations: paths with extensions ts/py/md/sh/tsx/js/yml
# followed by a colon and integer line number. Digits included in path class
# because real-world paths contain them (e.g. `phase_45_spec.py`).
_CITATION_RE = re.compile(r"([A-Za-z0-9_./-]+\.(?:ts|py|md|sh|tsx|js|yml)):(\d+)")


def _render_citation_findings(findings: list[dict[str, Any]], cycle: int) -> tuple[str, bool]:
    lines = [f"SPEC CITATION LINT FINDINGS (cycle {cycle}):"]
    for f in findings:
        line = f"  {f['severity']}: {f['path']}:{f['line']} — {f['reason']}"
        # GH954 (agreement A633F67D): surface fuzzy-suggest candidates to the
        # retry writer so a still-fabricated citation gets a concrete fix
        # hint instead of just "file does not exist".
        candidates = f.get("candidates")
        if candidates:
            line += f" (did you mean: {', '.join(candidates)}?)"
        lines.append(line)
    # A11A5779: append a remediation hint so the retry writer knows HOW to
    # fix bare-filename citations. The lint resolves paths relative to the
    # repo root (git_cwd), so any citation that does not start at a top-level
    # directory cannot resolve and will keep failing on retry.
    lines.append("")
    lines.append("REMEDIATION:")
    lines.append(
        "  Citation paths must be repo-rooted — they must start at a real "
        "repository directory (one of the top-level directories of this repo: "
        f"{', '.join(repo_top_level_dirs())})."
    )
    lines.append(
        "  Bare filenames are flagged because the lint resolves paths relative "
        "to the repo root; a bare filename never resolves and will fail again."
    )
    lines.append(
        "  WRONG path: `run-phase.ts` (bare filename — never resolves)."
    )
    lines.append(
        "  RIGHT path: `SYSTEM/cli/build/run-phase.ts` (repo-rooted — resolves cleanly)."
    )
    return _truncate_findings("\n".join(lines))


def _resolve_citation_target(
    path_str: str,
    git_cwd: Path,
    git_cwd_resolved: Path,
    scratchpad_root: Path | None,
    suffix_map: dict[str, str] | None,
) -> tuple[Path | None, str]:
    """Resolve a spec citation path against git_cwd, then the git suffix map
    (bare-basename fallback), then scratchpad_dir (pipeline-artifact
    fallback). Each root attempt is wrapped in the same escape-guard as the
    pre-fallback check (mirrors 1656-1660): a path that resolves outside its
    root is a miss for that root, not an error.

    Returns (resolved_path, resolved_via) on a hit. On a miss: (None,
    "missing") if at least one root CONTAINED the path but no file was found
    there; (None, "escape") if every attempted root was climbed out of —
    callers `continue` on "escape" to preserve the pre-fallback silent-skip
    semantics for adversarial paths (3F5599A6 §2.1).
    """
    contained = False

    try:
        candidate = (git_cwd / path_str).resolve()
        candidate.relative_to(git_cwd_resolved)
        contained = True
        if candidate.is_file():
            return candidate, "git_cwd"
    except (ValueError, OSError):
        pass

    if suffix_map:
        full = suffix_map.get(path_str)
        if full and full != path_str:
            try:
                candidate = (git_cwd / full).resolve()
                candidate.relative_to(git_cwd_resolved)
                contained = True
                if candidate.is_file():
                    return candidate, "suffix_map"
            except (ValueError, OSError):
                pass

    if scratchpad_root is not None:
        try:
            candidate = (scratchpad_root / path_str).resolve()
            candidate.relative_to(scratchpad_root)
            contained = True
            if candidate.is_file():
                return candidate, "scratchpad"
        except (ValueError, OSError):
            pass

    return (None, "missing") if contained else (None, "escape")


def _neighbor_dir_resolve(
    path_str: str,
    resolved_dirs: set[str],
    git_cwd: Path,
    git_cwd_resolved: Path,
) -> tuple[Path | None, list[str]]:
    """GH954 pass-2 retry (agreement A633F67D): try `path_str` under each dir
    that already yielded a git_cwd/suffix_map hit this call (a sibling-cite's
    dir is a plausible home for a bare-basename miss — the #954 repro). Same
    escape guard as `_resolve_citation_target`: a candidate that climbs out
    of git_cwd is skipped, not an error. Distinctness is by resolved absolute
    path (two neighbor dirs may collapse to the same dir).

    Returns (the_path, [rel_str]) on exactly ONE distinct hit. Returns
    (None, sorted_rel_strs) on zero or ambiguous (>=2) hits — the candidates
    are surfaced for the ERROR finding's `candidates` key either way.
    """
    hits: dict[str, Path] = {}
    for d in sorted(resolved_dirs):
        try:
            candidate = (git_cwd / d / path_str).resolve()
            candidate.relative_to(git_cwd_resolved)
        except (ValueError, OSError):
            continue
        if not candidate.is_file():
            continue
        hits[str(candidate)] = candidate

    if len(hits) == 1:
        (only,) = hits.values()
        return only, [str(only.relative_to(git_cwd_resolved))]
    return None, sorted(str(p.relative_to(git_cwd_resolved)) for p in hits.values())


def _citation_suggestions(
    path_str: str,
    git_cwd: Path,
    _tracked_box: list[list[str]] | None = None,
) -> list[str]:
    """GH954 fuzzy basename-suggest fallback (agreement A633F67D), called
    only for still-unresolved misses. Same `git_read` guard pattern as
    `_build_suffix_map` — every except-arm returns [] (§1n OWN, never
    raises). `_tracked_box` is an optional one-slot cache (implementation
    detail — local, not module-level) a caller can pass to reuse one
    `git ls-files` result across multiple misses in the same
    `_verify_spec_citations` call: empty on entry means "not fetched yet"
    (this call fetches and fills it); non-empty means "reuse `[0]`".
    """
    if _tracked_box is not None and _tracked_box:
        tracked = _tracked_box[0]
    else:
        try:
            result = git_read(
                ["-c", "core.quotepath=false", "ls-files", "-z"],
                cwd=str(git_cwd),
                timeout=5,
            )
        except FileNotFoundError:
            logger.debug("citation_suggestions git_ls_files failed (reason=%s)", "git_not_found")
            tracked = []
        except OSError as e:
            logger.debug(
                "citation_suggestions git_ls_files failed (reason=%s, errno=%s)",
                "os_error",
                getattr(e, "errno", None),
            )
            tracked = []
        else:
            if result.returncode != 0:
                logger.debug(
                    "citation_suggestions git_ls_files failed (reason=%s, rc=%s)",
                    "nonzero_rc",
                    result.returncode,
                )
                tracked = []
            else:
                tracked = [line.strip() for line in result.stdout.split("\0") if line.strip()]
        if _tracked_box is not None:
            _tracked_box.append(tracked)

    target_name = Path(path_str).name
    matches = sorted(p for p in tracked if Path(p).name == target_name)
    return matches[:5]


def _retry_pending_misses(
    pending_misses: list[tuple[str, int]],
    resolved_dirs: set[str],
    git_cwd: Path,
    git_cwd_resolved: Path,
) -> list[dict[str, Any]]:
    """GH954 pass 2: retry each pass-1 miss against dirs that resolved a
    sibling citation this call. A unique neighbor hit gets the SAME
    line-range check as the main loop; a still-unresolved miss gets the
    ERROR finding (unchanged reason literal) plus an OPTIONAL `candidates`
    key — neighbor-ambiguous rels first, else a fuzzy basename suggest.
    `tracked_box` is a call-local one-slot cache so `_citation_suggestions`
    only spawns `git ls-files` once even across several unresolved misses.
    """
    retry_findings: list[dict[str, Any]] = []
    tracked_box: list[list[str]] = []
    for path_str, line_num in pending_misses:
        neighbor_path, neighbor_candidates = _neighbor_dir_resolve(
            path_str, resolved_dirs, git_cwd, git_cwd_resolved,
        )
        if neighbor_path is not None:
            try:
                total_lines = len(
                    neighbor_path.read_text(encoding="utf-8", errors="replace").splitlines()
                )
            except OSError as e:
                retry_findings.append({
                    "path": path_str,
                    "line": line_num,
                    "severity": "ERROR",
                    "reason": f"unreadable: {e}",
                })
                continue

            if line_num > total_lines or line_num < 1:
                retry_findings.append({
                    "path": path_str,
                    "line": line_num,
                    "severity": "ERROR",
                    "reason": f"line {line_num} > total {total_lines}",
                })
            else:
                retry_findings.append({
                    "path": path_str,
                    "line": line_num,
                    "severity": "WARNING",
                    "reason": "exists; symbol-proximity not checked (A813CA08)",
                    "resolved_via": "neighbor_dir",
                })
            continue

        finding: dict[str, Any] = {
            "path": path_str,
            "line": line_num,
            "severity": "ERROR",
            "reason": "file does not exist",
        }
        candidates = neighbor_candidates
        if not candidates:
            candidates = _citation_suggestions(path_str, git_cwd, tracked_box)
        if candidates:
            finding["candidates"] = candidates
        retry_findings.append(finding)

    return retry_findings


def _verify_spec_citations(ctx: WorkflowContext, prev: Any) -> StepResult:
    """Pre-Opus regex scan of the just-written spec for fabricated `<path>:<line>`
    citations. ERROR (missing file or out-of-range line) short-circuits to a
    cycle-2 retry, saving the Opus reviewer call ($1.64/cycle on cycle-1 catch).

    WARNING (file exists, line in range) is logged but does not block — symbol
    proximity is A813CA08's job, not v0 here.
    """
    step = "verify_spec_citations"
    if not isinstance(prev, StepResult) or not isinstance(prev.data, dict):
        return StepResult(
            status="error", data=None, duration_ms=0, step_name=step,
            error="prev step did not produce data", error_code="E_MISSING_PREV_DATA",
        )
    # E6602155: frozen spec fast-path — citations already verified upstream; skip.
    if prev.data.get("is_frozen"):
        return StepResult(
            status="skip",
            data={**prev.data},
            duration_ms=0,
            step_name=step,
        )
    if "spec_path" not in prev.data:
        return StepResult(
            status="error", data=None, duration_ms=0, step_name=step,
            error="prev.data missing 'spec_path'", error_code="E_MISSING_PREV_DATA",
        )

    spec_path = Path(prev.data["spec_path"])
    cycle = int(prev.data.get("cycle", 1))

    if not spec_path.is_file():
        fwd_cite_missing = prev.data if isinstance(prev.data, dict) else {}
        build_class_cite = (ctx.org_config or {}).get("complexity", "SIMPLE").upper()
        return cast(StepResult, RecoverableGateMixin.gated_step_result(
            build_class=build_class_cite,
            gate="spec_retry",
            cycle=cycle,
            retry_from_step_idx=0,
            error_code="E_SPEC_FILE_MISSING",
            error_msg=f"spec file not on disk: {spec_path}",
            step_name=step,
            forwarded_data={**fwd_cite_missing, "missing_path": str(spec_path)},
            terminal_error_code="E_SPEC_FILE_MISSING",
        ))

    cfg = ctx.org_config or {}
    git_cwd = Path(resolve_git_cwd(cfg))
    git_cwd_resolved = git_cwd.resolve()

    scratchpad_root: Path | None = None
    try:
        scratchpad_root = _resolve_scratchpad(ctx)
    except ValueError:
        pass  # no scratchpad_dir configured — fallback disabled, not fatal (§1n OWN)

    # utf-8-sig: drop leading BOM if present (defense-in-depth — BOM does not
    # affect citation regex matching but matches _verify_spec_completeness and
    # _read_decision_doc_block.  Agreement 83140A09.
    content = spec_path.read_text(encoding="utf-8-sig")

    findings: list[dict[str, Any]] = []
    # Suffix map is built lazily ONCE per call and kept as a LOCAL (not
    # module-level/global) so the direct-call `_build_suffix_map` spy suites'
    # isolation stays intact (3F5599A6 §3.2 sibling audit).
    suffix_map: dict[str, str] | None = None
    # GH954 (agreement A633F67D): pass-1 accumulates resolved_dirs from
    # git_cwd/suffix_map hits ONLY (not scratchpad, not pass-2 results — no
    # chaining) and stashes misses for the pass-2 neighbor-dir retry instead
    # of erroring immediately.
    resolved_dirs: set[str] = set()
    pending_misses: list[tuple[str, int]] = []
    for m in _CITATION_RE.finditer(content):
        path_str = m.group(1)
        line_num = int(m.group(2))

        # Lazy-build trigger: only on a git_cwd MISS where the path stayed
        # inside git_cwd — an escaping path must not force the git ls-files
        # subprocess (mirrors the escape guard below).
        if suffix_map is None:
            try:
                probe = (git_cwd / path_str).resolve()
                probe.relative_to(git_cwd_resolved)
                git_cwd_missed = not probe.is_file()
            except (ValueError, OSError):
                git_cwd_missed = False
            if git_cwd_missed:
                suffix_map = _build_suffix_map(git_cwd)

        # Path-escape guard mirrors _verify_red_lint_rules: an adversarial
        # citation that climbs out of every attempted root is silently
        # skipped — we don't want spec phase to terminate over weird paths
        # in prose.
        resolved, resolved_via = _resolve_citation_target(
            path_str, git_cwd, git_cwd_resolved, scratchpad_root, suffix_map,
        )
        if resolved_via == "escape":
            continue

        if resolved is None:
            pending_misses.append((path_str, line_num))
            continue

        if resolved_via in ("git_cwd", "suffix_map"):
            resolved_dirs.add(str(resolved.parent.relative_to(git_cwd_resolved)))

        try:
            total_lines = len(resolved.read_text(encoding="utf-8", errors="replace").splitlines())
        except OSError as e:
            findings.append({
                "path": path_str,
                "line": line_num,
                "severity": "ERROR",
                "reason": f"unreadable: {e}",
            })
            continue

        if line_num > total_lines or line_num < 1:
            findings.append({
                "path": path_str,
                "line": line_num,
                "severity": "ERROR",
                "reason": f"line {line_num} > total {total_lines}",
            })
        else:
            findings.append({
                "path": path_str,
                "line": line_num,
                "severity": "WARNING",
                "reason": "exists; symbol-proximity not checked (A813CA08)",
                "resolved_via": resolved_via,
            })

    # GH954 pass 2: retry each pass-1 miss via neighbor-dir + fuzzy-suggest
    # before declaring it fabricated (extracted per §1aa — see
    # _retry_pending_misses docstring for the exact retry semantics).
    findings.extend(
        _retry_pending_misses(pending_misses, resolved_dirs, git_cwd, git_cwd_resolved)
    )

    errors = [f for f in findings if f["severity"] == "ERROR"]
    warnings = [f for f in findings if f["severity"] == "WARNING"]

    if errors:
        first = errors[0]
        reason = (
            f"fabricated citation: {first['path']}:{first['line']} ({first['reason']}); "
            f"{len(errors)} total"
        )
        # findings rendered to string for engine retry hook (engine.py:226 slices
        # `findings[:500]` and calls `.encode("utf-8")` — list would AttributeError).
        # Structured list preserved under findings_structured for observers.
        rendered_findings, was_truncated = _render_citation_findings(findings, cycle)
        fwd_cite = prev.data if isinstance(prev.data, dict) else {}
        build_class_citemal = (ctx.org_config or {}).get("complexity", "SIMPLE").upper()
        return cast(StepResult, RecoverableGateMixin.gated_step_result(
            build_class=build_class_citemal,
            gate="spec_retry",
            cycle=cycle,
            retry_from_step_idx=0,
            error_code="E_SPEC_CITATION_MALFORMED",
            error_msg=reason,
            step_name=step,
            forwarded_data={
                **fwd_cite,
                "findings": rendered_findings,
                "findings_structured": findings,
                "findings_truncated": was_truncated,
                "errors": errors,
            },
            terminal_error_code="E_SPEC_CITATION_FATAL",
        ))

    return StepResult(
        status="ok",
        data={
            **prev.data,
            "citation_findings": findings,
            "warnings_count": len(warnings),
        },
        duration_ms=0, step_name=step,
    )


def _load_surgical_delta(scratchpad: Path, cycle: int) -> tuple[dict[str, Any] | None, str | None]:
    """GH605: load the surgical-delta sidecar for this cycle.

    Returns (delta_dict, None) on success, or (None, reason) where reason is
    one of: no_sidecar, parse_error, cycle_mismatch.
    """
    sidecar = scratchpad / "specs" / f"surgical-delta-cycle-{cycle}.json"
    if not sidecar.is_file():
        return None, "no_sidecar"
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None, "parse_error"
    if not isinstance(data, dict) or "patches" not in data:
        return None, "parse_error"
    if data.get("cycle") != cycle:
        return None, "cycle_mismatch"
    return data, None


def _build_review_prompt(ctx: WorkflowContext, prev: Any) -> StepResult:
    if not isinstance(prev, StepResult) or not isinstance(prev.data, dict):
        return StepResult(
            status="error", data=None, duration_ms=0,
            step_name="build_review_prompt",
            error="prev step did not produce spec_path",
            error_code="E_MISSING_PREV_DATA",
        )
    cycle = int(prev.data.get("cycle", 1))
    scratchpad = _resolve_scratchpad(ctx)
    spec_path = Path(prev.data["spec_path"])
    review_path = scratchpad / _review_cycle_relpath(cycle)

    # Cycle ≥2: if prev cycle-1 review on disk carries a structured findings
    # JSON block, use the restricted reviewer (per-finding RESOLVED|UNRESOLVED).
    # Falls through to free-form below when no structured findings present.
    if cycle >= 2:
        prev_review_path = scratchpad / _review_cycle_relpath(cycle - 1)
        prev_review_text = ""
        if prev_review_path.exists():
            try:
                prev_review_text = prev_review_path.read_text(encoding="utf-8")
            except OSError:
                prev_review_text = ""
        structured_findings = extract_structured_findings(prev_review_text) or []
        if structured_findings:
            try:
                spec_text = spec_path.read_text(encoding="utf-8")
            except OSError:
                spec_text = ""
            rule_axes_suffix = (
                "\n\nRULE-AXES REQUIREMENT (GH497): on REVISE (any finding left "
                "UNRESOLVED), also emit `RULE-AXES: §<rule>, §<rule>, ...` citing "
                "every workflows.md rule axis invoked (or `RULE-AXES: NONE`)."
            )
            _prev_data_brp = prev.data if isinstance(prev.data, dict) else {}

            # GH605: delta-only re-review — vote on the surgical patch diff +
            # affected spec sections instead of re-embedding the full spec.
            delta_extra: dict[str, Any] | None = None
            if get_config().gate_enabled("HAL_DELTA_REREVIEW"):
                delta, reason = _load_surgical_delta(scratchpad, cycle)
                if delta is None:
                    _emit_safe("delta_rereview_fallback", {"cycle": cycle, "reason": reason})
                else:
                    sections = extract_affected_sections(spec_text, delta["patches"])
                    if not sections:
                        _emit_safe("delta_rereview_fallback", {"cycle": cycle, "reason": "no_sections"})
                    else:
                        delta_prompt = build_delta_reviewer_prompt(
                            structured_findings, delta["patches"], sections
                        ) + rule_axes_suffix
                        delta_extra = {
                            "prompt": delta_prompt,
                            "doc_path": str(review_path),
                            "spec_path": str(spec_path),
                            "cycle": cycle,
                            "prompt_bytes": len(delta_prompt.encode("utf-8")),
                            "restricted_reviewer": True,
                            "delta_rereview": True,
                        }
                        _emit_safe("delta_rereview_used", {
                            "cycle": cycle,
                            "prompt_bytes": len(delta_prompt.encode("utf-8")),
                            "full_spec_bytes": len(spec_text.encode("utf-8")),
                            "n_patches": len(delta["patches"]),
                            "n_sections": len(sections),
                        })

            if delta_extra is not None:
                return StepResult(
                    status="ok",
                    data=_fwd_frozen(_prev_data_brp, delta_extra),
                    duration_ms=0,
                    step_name="build_review_prompt",
                )

            prompt = _restricted_reviewer_prompt(structured_findings, spec_text) + rule_axes_suffix
            return StepResult(
                status="ok",
                data=_fwd_frozen(_prev_data_brp, {
                    "prompt": prompt,
                    "doc_path": str(review_path),
                    "spec_path": str(spec_path),
                    "cycle": cycle,
                    "prompt_bytes": len(prompt.encode("utf-8")),
                    "restricted_reviewer": True,
                }),
                duration_ms=0,
                step_name="build_review_prompt",
            )

    parts: list[str] = []
    role = _maybe_role_template(ctx)
    if role:
        parts.append(role.rstrip())
        parts.append("")
    parts.append(_read_first_block(scratchpad))
    parts.append("")
    parts.append(
        "ROLE: You are a spec reviewer (separate agent from the spec writer). "
        "Find gaps, contradictions, missing edge cases, and impossible requirements. "
        "Do NOT approve by default."
    )
    parts.append("")
    parts.append("FEATURE REQUEST:")
    parts.append(ctx.question or "(no feature request provided)")
    parts.append("")
    parts.append(f"SPEC TO REVIEW (read this file): {spec_path}")
    parts.append("")
    research_dir = scratchpad / "research"
    if research_dir.is_dir():
        research_files = sorted(p for p in research_dir.glob("*.md") if p.is_file())
        if research_files:
            parts.append("EXPLORATION FINDINGS (read for context — do NOT trust summaries):")
            for p in research_files:
                parts.append(f"- {p}")
            parts.append("")
    # A813CA08: reviewer-side citation-grounding rubric. Must precede the
    # output schema — LLM reads top-down and starts emitting once it has the
    # schema; instructions arriving after risk being honored as afterthought.
    # Persists across cycles — cycle-2 reviews retain the discipline.
    parts.append(_citation_grounding_rubric())
    parts.append("")
    parts.append(_review_output_schema())
    parts.append("")
    parts.append(
        "RULE-AXES REQUIREMENT (GH497): on REVISE, also emit a line "
        "`RULE-AXES: §<rule>, §<rule>, ...` citing every workflows.md rule "
        "axis your findings invoke (or `RULE-AXES: NONE` if none apply)."
    )
    parts.append("")
    parts.append(_get_anti_fab_prompt())

    prompt = "\n".join(parts)
    _prev_data_brp2 = prev.data if isinstance(prev.data, dict) else {}
    return StepResult(
        status="ok",
        data=_fwd_frozen(_prev_data_brp2, {
            "prompt": prompt,
            "doc_path": str(review_path),
            "spec_path": str(spec_path),
            "cycle": cycle,
            "prompt_bytes": len(prompt.encode("utf-8")),
        }),
        duration_ms=0,
        step_name="build_review_prompt",
    )


def _ship_reachable(ship_n: int, revise_n: int, remaining: int) -> bool:
    """GH707: can SHIP still reach strict majority if every remaining re-poll
    votes SHIP? `revise_n` only grows and `ship_n` can rise by at most
    `remaining`, so SHIP is unreachable once ship_n + remaining <= revise_n.
    Used to early-terminate the repoll loop on outcome-irrelevant polls
    (verdict-preserving; never breaks while SHIP majority is still achievable)."""
    return ship_n + remaining > revise_n


def _frozen_revise_repoll(cfg: dict[str, Any], first: StepResult, invoke_kwargs: dict[str, Any], frozen: bool = True) -> StepResult:
    """GH514(1)/GH541: REVISE majority re-poll, shared for frozen and non-frozen specs.

    The reviewer LLM is nondeterministic; a REVISE verdict is both the likeliest
    dice roll and the most expensive one (full writer-cycle fallback), for FROZEN
    (human-ratified) specs and non-frozen specs alike (GH541). Re-poll the
    reviewer `spec_frozen_review_repolls` (frozen=True) or `spec_review_repolls`
    (frozen=False) more times (default 2 → majority-of-3); SHIP wins only on
    strict majority of parsable verdicts (ship_n > revise_n; UNKNOWN counts as
    REVISE fail-closed, errored re-polls cast no vote).  Returns the SHIP-voting
    poll's StepResult on a SHIP majority (its raw_response threads downstream so
    write_review_doc / gate_on_review parse SHIP), else `first` unchanged.  Emits
    phase_45_spec_review_repoll either way.  GH707: the loop early-terminates once
    SHIP majority becomes unreachable (`_ship_reachable`) — `n_repolls` (cfg
    `spec_frozen_review_repolls`/`spec_review_repolls`) is the cost cap, not a
    mandatory spawn count."""
    cfg_key = "spec_frozen_review_repolls" if frozen else "spec_review_repolls"
    n_repolls = int(cfg.get(cfg_key, 2))
    if n_repolls <= 0:
        return first  # kill-switch: 0 disables, behavior identical to pre-GH514(1)
    votes = [VERDICT_REVISE]
    ship_result: StepResult | None = None
    for i in range(n_repolls):
        remaining = n_repolls - (i + 1)
        extra = invoke_llm_subprocess(**invoke_kwargs)
        if extra.status != "ok" or not isinstance(extra.data, dict):
            votes.append("ERROR")  # no vote — degraded-but-OK, never fails the step
            if not _ship_reachable(votes.count(VERDICT_SHIP), votes.count(VERDICT_REVISE), remaining):
                break
            continue
        v = _parse_verdict(extra.data.get("raw_response", "") or "")
        v = v if v == VERDICT_SHIP else VERDICT_REVISE  # UNKNOWN → REVISE fail-closed
        votes.append(v)
        if v == VERDICT_SHIP and ship_result is None:
            ship_result = extra
        if not _ship_reachable(votes.count(VERDICT_SHIP), votes.count(VERDICT_REVISE), remaining):
            break
    ship_n = votes.count(VERDICT_SHIP)
    revise_n = votes.count(VERDICT_REVISE)
    final = VERDICT_SHIP if (ship_n > revise_n and ship_result is not None) else VERDICT_REVISE
    _emit_safe(
        "phase_45_spec_review_repoll",
        {"phase": "phase_45_spec", "votes": votes, "final": final,
         "n_repolls": n_repolls, "cycle": int((invoke_kwargs.get("extra_data") or {}).get("cycle", 1)),
         "frozen": frozen},
    )
    return ship_result if (final == VERDICT_SHIP and ship_result is not None) else first


def _invoke_review_llm(ctx: WorkflowContext, prev: Any) -> StepResult:
    if not isinstance(prev, StepResult) or not isinstance(prev.data, dict):
        return StepResult(
            status="error", data=None, duration_ms=0,
            step_name="invoke_review_llm",
            error="prev step did not produce a prompt",
            error_code="E_MISSING_PREV_DATA",
        )
    cfg = ctx.org_config or {}
    rev_model = _resolve_review_model(cfg)
    _frozen_fwd = {k: prev.data[k] for k in _FROZEN_THREADING_KEYS if k in prev.data}
    invoke_kwargs = dict(
        prompt=prev.data["prompt"],
        model=rev_model,
        timeout_sec=_resolve_review_timeout_sec(cfg),
        step_name="invoke_review_llm",
        extra_data={
            "doc_path": prev.data["doc_path"],
            "spec_path": prev.data["spec_path"],
            "cycle": prev.data.get("cycle", 1),
            **_frozen_fwd,
        },
        hard_gate=True,
        gate_label="plan-review",
        allowed_tools=["Read"],
    )
    res = invoke_llm_subprocess(**invoke_kwargs)
    if (
        res.status == "ok"
        and isinstance(res.data, dict)
        and _parse_verdict(res.data.get("raw_response", "") or "") == VERDICT_REVISE
    ):
        return _frozen_revise_repoll(cfg, res, invoke_kwargs, frozen=bool(prev.data.get("is_frozen")))
    return res


def _parse_verdict(raw: str) -> str:
    """Parse SHIP/REVISE from the canonical ## Verdict section header.

    Routes through lib/verdict_parse.py P3 (EEFD480F) — single-source of truth.
    Only the first token directly under a '## Verdict' heading is considered.
    PASS/APPROVED aliases map to SHIP. UNKNOWN if no matching header — treated
    as REVISE by gate_on_review (fail-closed).
    """
    return cast(str, verdict_under_heading(
        raw,
        ("SHIP", "PASS", "APPROVED", "REVISE"),
        aliases={"PASS": VERDICT_SHIP, "APPROVED": VERDICT_SHIP},
        fallback=VERDICT_UNKNOWN,
    ))


def _write_review_doc(_ctx: WorkflowContext, prev: Any) -> StepResult:
    if not isinstance(prev, StepResult) or not isinstance(prev.data, dict):
        return StepResult(
            status="error", data=None, duration_ms=0,
            step_name="write_review_doc",
            error="prev step did not produce raw_response",
            error_code="E_MISSING_PREV_DATA",
        )
    raw = prev.data["raw_response"]
    cycle = int(prev.data.get("cycle", 1))
    review_path = Path(prev.data["doc_path"])
    structured_verdict_for_gate: str | None = None
    review_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(review_path, raw)  # HIGH #4: atomic write prevents stale canonical on kill

    _emit_safe(
        "phase_45_spec_writer_complete",
        {
            "phase": "phase_45_spec",
            "cycle": cycle,
            "response_bytes": len(raw.encode("utf-8")),
            "duration_ms": prev.duration_ms or 0,
        },
    )

    if cycle >= 2:
        # Restricted-mode: try per-finding parser first.
        # Falls back to classic ## Verdict parser when reviewer was free-form
        # (i.e. cycle-1 had no structured findings, so cycle-2 reviewer prompt
        # was also free-form and won't have FINDING_ lines).
        parsed = parse_per_finding_verdicts(raw)
        fv = parsed["final_verdict"]
        if fv != "UNPARSED":
            if fv == "PASS":
                verdict = VERDICT_SHIP
            else:  # REVISE
                verdict = VERDICT_REVISE
            _spec_rc2_structured = extract_structured_findings(raw)
            _spec_rc2_n_structured = len(_spec_rc2_structured) if _spec_rc2_structured is not None else 0
            _spec_rc2_n_total = _count_freetext_findings(raw) + _spec_rc2_n_structured
            _emit_safe(
                "phase_45_spec_review_complete",
                {
                    "phase": "phase_45_spec",
                    "cycle": cycle,
                    "verdict": verdict,
                    "n_findings_total": _spec_rc2_n_total,
                    "n_findings_structured": _spec_rc2_n_structured,
                    "duration_ms": prev.duration_ms or 0,
                },
            )
            _prev_data_wrd = prev.data if isinstance(prev.data, dict) else {}
            return StepResult(
                status="ok",
                data=_fwd_frozen(_prev_data_wrd, {
                    "review_path": str(review_path),
                    "spec_path": prev.data["spec_path"],
                    "review_bytes_written": len(raw.encode("utf-8")),
                    "verdict": verdict,
                    "cycle": cycle,
                    "review_raw": raw,
                    "per_finding": parsed["per_finding"],
                    "n_resolved": parsed["n_resolved"],
                    "n_total": parsed["n_total"],
                }),
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
                "phase": "phase_45_spec",
                "cycle": 1,
                "json_block_present": structured_for_telemetry is not None,
                "json_findings_count": (
                    len(structured_for_telemetry) if structured_for_telemetry is not None else None
                ),
                "freetext_findings_count": freetext_findings_count,
            },
        )

    # Step 5 / G1 #2: structured-findings drive the gate verdict on cycle 1 (disk-truth).
    if cycle <= 1:
        structured_findings = extract_structured_findings(raw)
        md_verdict = _parse_verdict(raw)  # also computed below for fallback; pure & cheap
        if structured_findings is None:
            _emit_safe(
                "spec_findings_block_absent",
                {"markdown_verdict": md_verdict, "cycle": 1, "phase": "phase_45_spec"},
            )
        else:
            n = len(structured_findings)
            structured_verdict = VERDICT_SHIP if n == 0 else VERDICT_REVISE
            structured_verdict_for_gate = structured_verdict
            _emit_safe(
                "spec_structured_verdict",
                {
                    "markdown_verdict": md_verdict,
                    "structured_verdict": structured_verdict,
                    "n_findings": n,
                    "cycle": 1,
                    "phase": "phase_45_spec",
                },
            )
            # Drift only when both verdicts in {SHIP, REVISE} and they disagree.
            if md_verdict in (VERDICT_SHIP, VERDICT_REVISE) and md_verdict != structured_verdict:
                _emit_safe(
                    "spec_verdict_drift",
                    {
                        "markdown_verdict": md_verdict,
                        "structured_verdict": structured_verdict,
                        "n_findings": n,
                        "cycle": 1,
                        "phase": "phase_45_spec",
                    },
                )

    if cycle <= 1 and structured_verdict_for_gate is not None:
        prose = md_verdict if md_verdict in (VERDICT_SHIP, VERDICT_REVISE) else None
        verdict, _verdict_reason = resolve_gate_verdict(
            structured=structured_verdict_for_gate,
            prose=prose,
            conservative=VERDICT_REVISE,
            context="phase_45_cycle1",
        )
        # GH642: a zero-findings structured SHIP is a heuristic, not affirmative
        # approval. When prose is unparseable (md_verdict UNKNOWN → prose None),
        # resolve returns it as `structured_only` SHIP — a fail-OPEN pass. Flip to
        # conservative REVISE (existing E_VALIDATION_RETRY taxonomy); never ship on
        # emptiness alone when the reviewer verdict cannot be parsed.
        if structured_verdict_for_gate == VERDICT_SHIP and prose is None and verdict == VERDICT_SHIP:
            verdict = VERDICT_REVISE
            _emit_safe("spec_zero_findings_unparseable_fail_closed", {
                "phase": "phase_45_spec",
                "cycle": cycle,
                "markdown_verdict": md_verdict,
                "n_findings": 0,
            })
        # GH642: explicit prose REVISE overriding a zero-findings SHIP (GH373
        # divergent→conservative) is correct but was silent — emit an observable signal.
        elif verdict == VERDICT_REVISE and structured_verdict_for_gate == VERDICT_SHIP:
            _emit_safe("spec_revise_without_findings", {
                "phase": "phase_45_spec",
                "cycle": cycle,
                "markdown_verdict": md_verdict,
            })
    else:
        verdict = _parse_verdict(raw)
    # 13361031: telemetry-only hook — parse the citation grounding count line
    # (emitted by the reviewer per _citation_grounding_rubric). NO verdict change.
    _gm = _GROUNDING_COUNT_RE.search(raw)
    if _gm is not None:
        _emit_safe("citation_grounding_self_enforcement", {
            "grounded": int(_gm.group(1)),
            "total": int(_gm.group(2)),
            "ungrounded": int(_gm.group(3)),
            "verdict": verdict,
            "compliant": not (int(_gm.group(3)) >= 1 and verdict == VERDICT_SHIP),
        })
    else:
        _emit_safe("citation_grounding_count_missing", {"verdict": verdict})
    _spec_final_structured = extract_structured_findings(raw)
    _spec_final_n_structured = len(_spec_final_structured) if _spec_final_structured is not None else 0
    _spec_final_n_total = _count_freetext_findings(raw) + _spec_final_n_structured
    _emit_safe(
        "phase_45_spec_review_complete",
        {
            "phase": "phase_45_spec",
            "cycle": cycle,
            "verdict": verdict,
            "n_findings_total": _spec_final_n_total,
            "n_findings_structured": _spec_final_n_structured,
            "duration_ms": prev.duration_ms or 0,
        },
    )
    _prev_data_wrd2 = prev.data if isinstance(prev.data, dict) else {}
    return StepResult(
        status="ok",
        data=_fwd_frozen(_prev_data_wrd2, {
            "review_path": str(review_path),
            "spec_path": prev.data["spec_path"],
            "review_bytes_written": len(raw.encode("utf-8")),
            "verdict": verdict,
            "cycle": cycle,
            "review_raw": raw,
        }),
        duration_ms=0,
        step_name="write_review_doc",
    )


def _gate_on_review(_ctx: WorkflowContext, prev: Any) -> StepResult:
    """HARD GATE — REVISE on cycle < cap → E_VALIDATION_RETRY recoverable=True
    (engine retries from step 0, re-running spec writer with findings).
    REVISE on cycle == cap → E_REVIEW_FAILED recoverable=False (terminal abort).
    SHIP → ok.

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
        _spec_ship_raw = prev.data.get("review_raw", "") or ""
        _spec_ship_structured = extract_structured_findings(_spec_ship_raw)
        _spec_ship_n = len(_spec_ship_structured) if _spec_ship_structured is not None else 0
        _emit_safe(
            "phase_45_spec_ship",
            {
                "phase": "phase_45_spec",
                "cycle": cycle,
                "n_findings_at_ship": _spec_ship_n,
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
            data={
                "verdict": verdict,
                "review_path": prev.data["review_path"],
                "spec_path": prev.data["spec_path"],
                "cycle": cycle,
            },
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
                "phase": "phase_45_spec",
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

    # GH443 part 3 §2.2: upstream-root escalation — checked FIRST among the new
    # branches, BEFORE the durable hard-cap and the frozen-fallback branch. An
    # upstream-rooted finding can never be resolved by a spec rewrite, so we
    # escalate to phase_4 instead of retrying (and do NOT bump the durable
    # counter — escalation is a routing signal, not a wasted spec attempt).
    from plugins.checklist_convergence import (
        extract_structured_findings_raw as _extract_structured_findings_raw,
    )

    _spec_gate_structured = _extract_structured_findings_raw(raw_review) or []

    # GH1006 §2.1: ALREADY_DONE verdict — every structured finding is
    # root=="already-done" (the cited defects are verified already fixed at
    # HEAD). Predicate is all(), not any(): a mixed list still needs the
    # existing upstream/spec branches below. Stateless w.r.t. durable state —
    # never bumps the GH443 REVISE counter (early return skips that block).
    _spec_gate_already_done = [
        f for f in _spec_gate_structured
        if isinstance(f, dict) and f.get("root") == "already-done"
    ]
    if _spec_gate_structured and len(_spec_gate_already_done) == len(_spec_gate_structured):
        _emit_safe(
            "phase_45_spec_already_done",
            {"phase": "phase_45_spec", "cycle": cycle, "n_findings": len(_spec_gate_already_done)},
        )
        return StepResult(
            status="ok",
            data={
                "verdict": "ALREADY_DONE",
                "review_path": prev.data["review_path"],
                "cycle": cycle,
                "already_done_findings": _spec_gate_already_done,
            },
            duration_ms=0,
            step_name="gate_on_review",
        )

    _spec_gate_upstream = [
        f for f in _spec_gate_structured
        if isinstance(f, dict) and f.get("root") == "upstream"
    ]
    if _spec_gate_upstream:
        _emit_safe(
            "phase_45_spec_upstream_escalation",
            {"phase": "phase_45_spec", "cycle": cycle, "n_upstream": len(_spec_gate_upstream)},
        )
        return StepResult(
            status="error",
            data={
                "verdict": verdict,
                "review_path": prev.data["review_path"],
                "cycle": cycle,
                "escalate_to": "phase_4",
                "upstream_findings": _spec_gate_upstream,
            },
            duration_ms=0,
            step_name="gate_on_review",
            error="reviewer flagged upstream-rooted findings — escalating to phase_4 (terminal)",
            error_code="E_SPEC_UPSTREAM_REVISE",
            recoverable=False,
        )

    # GH443 part 3 §2.3: durable cross-invocation REVISE counter + hard-cap.
    # Fires BEFORE the frozen-fallback branch so the previously-uncapped
    # frozen loop is now bounded by the same durable cap. Missing
    # scratchpad_dir → skip counter entirely (behavior unchanged).
    _spec_gate_cfg = getattr(_ctx, "org_config", None) or {}
    _spec_gate_scratchpad_raw = _spec_gate_cfg.get("scratchpad_dir")
    if _spec_gate_scratchpad_raw:
        from revise_counter import bump_revise_count  # local import — lib/ on sys.path via engine bootstrap

        _spec_gate_total = bump_revise_count(Path(_spec_gate_scratchpad_raw), _spec_gate_cfg.get("run_id"), cycle)
        _spec_gate_hard_cap = int(_spec_gate_cfg.get("spec_revise_hard_cap", 6))
        if _spec_gate_total >= _spec_gate_hard_cap:
            _emit_safe(
                "phase_45_spec_exhausted",
                {
                    "phase": "phase_45_spec",
                    "total_revises": _spec_gate_total,
                    "hard_cap": _spec_gate_hard_cap,
                    "cycle": cycle,
                },
            )
            return StepResult(
                status="error",
                data={
                    "verdict": verdict,
                    "review_path": prev.data["review_path"],
                    "cycle_count": cycle,
                    "total_revises": _spec_gate_total,
                },
                duration_ms=0,
                step_name="gate_on_review",
                error=f"durable REVISE counter reached hard cap ({_spec_gate_total} >= {_spec_gate_hard_cap}) — terminal abort",
                error_code="E_SPEC_REVISE_EXHAUSTED",
                recoverable=False,
            )

    # GH419: frozen spec REVISE → emit fallback event + retry IN-PROCESS (retry_from_step=0)
    # so the engine re-enters step 0 in the same invocation instead of surfacing a
    # cross-invocation recoverable error that a fresh run-phase.ts call can't resolve.
    # Fires before the normal REVISE/cap logic so a frozen-spec cycle always falls back
    # to the full writer path on any REVISE (no cycle cap applies here).
    if prev.data.get("is_frozen"):
        _emit_safe("frozen_spec_fallback_to_full", {
            "phase": "phase_45_spec",
            "cycle": cycle,
            "verdict": verdict,
        })
        return StepResult(
            status="error",
            data={
                "frozen_fallback": True,
                "findings": raw_review,
                "retry_from_step": 0,
                "cycle_count": cycle,
            },
            duration_ms=0,
            step_name="gate_on_review",
            error="frozen spec received REVISE — falling back to full writer cycle (in-process)",
            error_code="E_VALIDATION_RETRY",
            recoverable=True,
        )

    # HIGH #2: cap findings before threading to next cycle to prevent token-budget breach.
    findings, was_truncated = _truncate_findings(raw_review)   # KEEP — forward thread still token-capped
    _emit_safe("phase_45_spec_revise", {
        "phase": "phase_45_spec",
        "cycle": cycle,
        "n_findings_unresolved": (
            _spec_revise_ruc := _resolve_unresolved_count(
                raw_review, prev.data.get("n_total"), prev.data.get("n_resolved"))
        )[0],
        "findings_source": _spec_revise_ruc[1],
    })
    _spec_revise_n_unresolved = _spec_revise_ruc[0]
    _spec_revise_source = _spec_revise_ruc[1]
    record_plan_review_reject(raw_review, cycle, _spec_revise_n_unresolved, findings_source=_spec_revise_source)
    revise_fwd: dict[str, Any] = {
        "verdict": verdict,
        "review_path": prev.data["review_path"],
        "findings": findings,
    }
    if was_truncated:
        revise_fwd["findings_truncated"] = True
        revise_fwd["findings_original_bytes"] = len(raw_review.encode("utf-8"))
    # GH625 §2.3a: revise_fwd is hand-built (no **prev.data spread), thread
    # gate_attempts through explicitly or the gate never sees prior spend.
    if isinstance(prev.data, dict) and isinstance(prev.data.get("gate_attempts"), dict):
        revise_fwd["gate_attempts"] = prev.data["gate_attempts"]
    # Resolve policy to decide retry vs terminate, then preserve pre-existing
    # terminal-path boundary-error formatting + abort event when cap reached.
    # Sibling regression guards: 5× boundary tests + test_ac8_abort_emitted_on_revise_at_cap.
    build_class_revise = ((_ctx.org_config if _ctx is not None else None) or {}).get("complexity", "SIMPLE").upper()
    pol = resolve_policy(build_class_revise, "spec_retry")
    # GH625: cap decision must use the gate's own attempts spend, not the
    # shared cycle counter (cycle is eaten by upstream retries/replay too).
    _rf_ga = revise_fwd.get("gate_attempts")
    attempts = _rf_ga.get("spec_retry", 0) if isinstance(_rf_ga, dict) else 0
    will_terminate = (pol.slot in ("terminal", "escalate")) or (attempts >= pol.cycle_cap)

    if will_terminate:
        boundary_error_str = format_boundary_error(
            phase="phase_45_spec",
            field="review_verdict",
            producer="phase_45_spec.review_cycle",
            where="phase_45_spec.py",
            schema="StepResult.data.verdict",
        )  # 03192214 site 1: E_REVIEW_FAILED
        _emit_safe(
            "phase_45_spec_abort",
            {
                "phase": "phase_45_spec",
                "cap_reached": True,
                "last_verdict": verdict,
                "terminal_reason": "cap_reached",
            },
        )
        terminal_msg = f"FEATURE/COMPLEX spec REVISE verdict on cycle {cycle} (cap reached) — terminal abort | {boundary_error_str}"
        terminal_data = {
            "verdict": verdict,
            "review_path": prev.data["review_path"],
            "cycle_count": cycle,
        }
        # GH625: thread gate_attempts through the terminal path too.
        if isinstance(_rf_ga, dict):
            terminal_data["gate_attempts"] = _rf_ga
        return cast(StepResult, RecoverableGateMixin.gated_step_result(
            build_class=build_class_revise,
            gate="spec_retry",
            cycle=cycle,
            retry_from_step_idx=0,
            error_code="E_VALIDATION_RETRY",
            error_msg=f"FEATURE/COMPLEX spec REVISE verdict on cycle {cycle} — engine retry",
            terminal_error_msg=terminal_msg,
            step_name="gate_on_review",
            forwarded_data=terminal_data,
            terminal_error_code="E_REVIEW_FAILED",
        ))

    # GH530: thread structured findings losslessly from the FULL pre-truncation
    # raw_review, so a truncated JSON fence in `findings` no longer kills the
    # cycle≥2 delta-retry path. Free-text fallback when reviewer omits the block.
    _threaded_sf = extract_structured_findings(raw_review)  # full text, pre-truncation
    _sf_source = "structured"
    if not _threaded_sf:  # None or []
        _threaded_sf = extract_findings_for_writer(raw_review)  # free-text fallback
        _sf_source = "freetext_fallback" if _threaded_sf else "none"
    if _threaded_sf:
        revise_fwd["structured_findings"] = list(_threaded_sf)
        try:  # GH636: persist thread so it survives ERROR-row evict of DBOS operation_outputs
            persist_findings_thread(_resolve_scratchpad(_ctx), list(_threaded_sf), cycle=cycle)
        except Exception:
            pass  # persistence must NEVER break the build (Opus-gate required)
    _emit_safe("spec_revise_findings_threaded", {
        "phase": "phase_45_spec",
        "cycle": cycle,
        "n_findings": len(_threaded_sf or []),
        "source": _sf_source,
    })

    return cast(StepResult, RecoverableGateMixin.gated_step_result(
        build_class=build_class_revise,
        gate="spec_retry",
        cycle=cycle,
        retry_from_step_idx=0,
        error_code="E_VALIDATION_RETRY",
        error_msg=f"FEATURE/COMPLEX spec REVISE verdict on cycle {cycle} — engine retry",
        step_name="gate_on_review",
        forwarded_data=revise_fwd,
        terminal_error_code="E_REVIEW_FAILED",
    ))


# ─── E6602155: frozen-spec fast-path helpers ─────────────────────────────────

_FROZEN_THREADING_KEYS = ("is_frozen", "frozen_doc_path", "frozen_fallback", "spec_path", "cycle", "gate_attempts")  # GH625: survive upstream threading


def _fwd_frozen(prev_data: dict[str, Any], extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Build a data dict that threads frozen-spec keys from prev_data into the next step.

    extra values take precedence over prev_data frozen keys (caller can override).
    """
    out: dict[str, Any] = {}
    if extra:
        out.update(extra)
    for k in _FROZEN_THREADING_KEYS:
        if k in prev_data and k not in out:
            out[k] = prev_data[k]
    return out


def _step_detect_frozen_spec(ctx: WorkflowContext, prev: Any) -> StepResult:
    """Step-0: detect whether decision_doc is a frozen spec and, if so, ingest it.

    Reads cfg["decision_doc"], calls detect_frozen_spec(), and either:
    - Sets is_frozen=True + copies frozen doc to specs/build-spec.md (fast-path).
    - Sets is_frozen=False + passes through (normal writer cycle).

    frozen_fallback=True in prev.data suppresses re-detection (retry after REVISE).
    """
    cfg = ctx.org_config or {}
    # Accept both StepResult.data dicts AND raw dicts (engine threads initial_data
    # as a plain dict on retry, not wrapped in StepResult).
    prev_data = (
        prev.data if isinstance(prev, StepResult) and isinstance(prev.data, dict)
        else (prev if isinstance(prev, dict) else {})
    )
    # Read retry-threaded values so _build_spec_prompt and _gate_on_review see the
    # real cycle/findings on retry, not a hardcoded 1 that causes an infinite loop.
    incoming_cycle = int(prev_data.get("cycle", 1))
    frozen_fallback = bool(prev_data.get("frozen_fallback", False))

    is_frozen_raw, frozen_path = detect_frozen_spec(cfg.get("decision_doc"))
    # effective_frozen: False when fallback flag set (engine retry runs full writer).
    effective_frozen = is_frozen_raw and not frozen_fallback

    base_data: dict[str, Any] = {
        **{k: v for k, v in prev_data.items() if k not in _FROZEN_THREADING_KEYS},
        "is_frozen": effective_frozen,
        "frozen_doc_path": str(frozen_path) if frozen_path is not None else None,
        "frozen_fallback": frozen_fallback,
        "cycle": incoming_cycle,
        "findings": prev_data.get("findings", ""),
    }
    # GH625: gate_attempts is in _FROZEN_THREADING_KEYS (excluded from the
    # spread above) but not rebuilt explicitly like the other frozen keys —
    # thread it through or spec_retry never sees prior spend.
    if isinstance(prev_data.get("gate_attempts"), dict):
        base_data["gate_attempts"] = prev_data["gate_attempts"]

    if effective_frozen and frozen_path is not None:
        scratchpad = _resolve_scratchpad(ctx)
        spec_dest = scratchpad / SPEC_DOC_RELPATH
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


def phase_45_spec_workflow() -> WorkflowDefinition:
    # Lambdas ensure monkeypatching of module-level function names works in tests
    # (Python looks up globals at call time, not at lambda-definition time).
    return WorkflowDefinition(
        name="phase_45_spec",
        steps=[
            StepContract(name="detect_frozen_spec", execute=lambda ctx, prev: _step_detect_frozen_spec(ctx, prev)),
            StepContract(name="build_spec_prompt", execute=lambda ctx, prev: _build_spec_prompt(ctx, prev)),
            StepContract(name="invoke_spec_llm", execute=lambda ctx, prev: _invoke_spec_llm(ctx, prev), resume_sentinel=True),
            StepContract(name="write_spec_doc", execute=lambda ctx, prev: _write_spec_doc(ctx, prev)),
            StepContract(name="verify_spec_completeness", execute=lambda ctx, prev: _verify_spec_completeness(ctx, prev)),
            StepContract(name="verify_spec_cite_prelint", execute=lambda ctx, prev: _verify_spec_cite_prelint(ctx, prev)),
            StepContract(name="verify_spec_citations", execute=lambda ctx, prev: _verify_spec_citations(ctx, prev)),
            StepContract(name="verify_spec_preflight_batch", execute=lambda ctx, prev: _verify_spec_preflight_batch(ctx, prev)),
            StepContract(name="verify_spec_lint", execute=lambda ctx, prev: _verify_spec_lint(ctx, prev)),
            StepContract(name="verify_spec_cite_lint", execute=lambda ctx, prev: _verify_spec_cite_lint(ctx, prev)),
            StepContract(name="verify_spec_scope_inverse", execute=lambda ctx, prev: _verify_spec_scope_inverse(ctx, prev)),
            StepContract(name="verify_spec_reentry", execute=lambda ctx, prev: _verify_spec_reentry(ctx, prev)),
            StepContract(name="verify_spec_helper_extraction", execute=lambda ctx, prev: _verify_spec_helper_extraction(ctx, prev)),
            StepContract(name="verify_spec_coverage", execute=lambda ctx, prev: _verify_spec_coverage(ctx, prev)),
            StepContract(name="verify_spec_lint_batch", execute=lambda ctx, prev: _verify_spec_lint_batch(ctx, prev)),
            StepContract(name="verify_spec_ac_dsl", execute=lambda ctx, prev: _verify_spec_ac_dsl(ctx, prev)),
            StepContract(name="build_review_prompt", execute=lambda ctx, prev: _build_review_prompt(ctx, prev)),
            StepContract(name="invoke_review_llm", execute=lambda ctx, prev: _invoke_review_llm(ctx, prev), resume_sentinel=True),
            StepContract(name="write_review_doc", execute=lambda ctx, prev: _write_review_doc(ctx, prev)),
            StepContract(name="gate_on_review", execute=lambda ctx, prev: _gate_on_review(ctx, prev)),
        ],
    )
