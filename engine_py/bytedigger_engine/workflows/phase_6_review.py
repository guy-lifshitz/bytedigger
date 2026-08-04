"""Phase 6 (review → fix → satisfaction) as a WorkflowDefinition.

Stage 2.6 port (2026-04-25). Fifth LLM-heavy phase. Replaces the partial
``phase_6_smoke`` zsh wrapper as the canonical phase-6 implementation;
``phase_6_smoke`` stays registered for raw-shell sanity but is no longer
the "phase-6 workflow".

**Scope — prompt-driven fanout for review; single-agent for fix and satisfaction.**
- REVIEW: 3 (SIMPLE) or 6 (FEATURE/COMPLEX) parallel sub-agents via ``pr-review-toolkit:*``
  (Agent tool, Approach A, implemented 2026-04-27; tier-aware fanout added 2026-04-27).
  Outer Sonnet dispatches parallel Agent calls and aggregates into ``reviews/build-review.md``.
- FIX: ONE fix worker (instead of parallel fix-by-severity buckets)
- SATISFACTION: ONE Opus evaluator (instead of 3-5 parallel evaluators)

phase-6-review.md describes additional patterns still DEFERRED:

1. **Parallel fix-by-severity** (CRITICAL / HIGH / MEDIUM buckets in
   parallel). Single fix worker handles all severities in v0; LLM-internal
   iteration (mirrors phase-5 GREEN improvement C') replaces workflow-level
   retry. ``write_fix_artifact`` parses ``FIX COMPLETE`` / ``FIX BLOCKED``
   / ``FIX SKIPPED`` markers and routes to ``E_FIX_BLOCKED`` on failure.
2. **Multi-evaluator satisfaction vote** — single Opus evaluator for
   SIMPLE/FEATURE; 3 parallel Opus evaluators with majority-PASS + median-SCORE
   aggregation (degraded to ≥2 survivors; single-eval semantics on 0–1 survivors)
   for COMPLEX. Threshold check in ``write_satisfaction_doc``
   (HARD GATE — ``never_skip_opus_satisfaction_gate``
   mirrors ``never_skip_opus_validation_gate`` from phase-5).

Hard-gate semantics (satisfaction):
    The Opus evaluator returns ``SCORE: <0-100>`` and a verdict. Score
    below ``satisfaction_threshold`` (or missing/malformed score) halts the
    workflow with ``error_code=E_SATISFACTION_BELOW_THRESHOLD``. Default
    threshold is 80 (SIMPLE); caller pins 85 (FEATURE) / 90 (COMPLEX).
    Missing score is treated as below-threshold by design — silent omission
    must not pass.

Token-spend guards (same playbook as phase_1 / phase_4 / phase_45 / phase_5):
    - All three prompts list READ_FIRST pointer paths only.
    - Review prompt references spec + RED log + GREEN log by path — never inlined.
    - Fix prompt references the just-written review doc by path.
    - Satisfaction prompt references spec + GREEN log + review/fix docs by path.
    - Optional ``role_template_path`` (~3KB) replaces full CLAUDE.md (~10KB).
    - Default timeouts: 600s review, 900s fix (writes code), 600s satisfaction.

Inputs (via ``ctx.org_config``):
    scratchpad_dir                  — REQUIRED. Absolute path to scratchpad root.
    complexity                      — Optional. SIMPLE | FEATURE | COMPLEX. Default: FEATURE.
                                      Controls reviewer count (3 for SIMPLE, 6 for FEATURE/COMPLEX).
    role_template_path              — Optional. Prepended to ALL three prompts.
    llm_command                     — Optional. Default: get_claude_primary(). Global
                                      fallback for all three subprocess calls.
    review_llm_command              — Optional. Per-step override.
    fix_llm_command                 — Optional. Per-step override.
    satisfaction_llm_command        — Optional. Per-step override.
                                      Recommended: pin to Opus to lock in the
                                      satisfaction gate (mirrors validation gate
                                      pinning in phase-5).
    review_llm_timeout_sec          — Optional. Default 600.
    fix_llm_timeout_sec             — Optional. Default 900.
    satisfaction_llm_timeout_sec    — Optional. Default 600.
    satisfaction_threshold          — Optional. Default 80 (0-100). Pin to 85 for
                                      FEATURE, 90 for COMPLEX per phase-6-review.md.
    security_classification         — Optional. "HIGH" adds OWASP/security checks
                                      to the composite reviewer prompt.

``ctx.question`` carries the user's feature request text.

Steps (10):
    1. build_review_prompt          — deterministic; spec + RED + GREEN log paths
    2. invoke_review_llm            — opaque subprocess (composite reviewer)
    3. write_review_artifact        — capture stdout to reviews/build-review.md;
                                      parse VERDICT (PASS / PARTIAL / FAIL)
    4. verify_findings              — pure-Python post-processor; demotes findings
                                      with fabricated path:line citations to
                                      ## Unverified Findings (Auto-Filtered);
                                      recomputes verdict after demotion
    5. build_fix_prompt             — deterministic; review doc + spec paths
    6. invoke_fix_llm               — opaque subprocess (fix worker; LLM iterates)
    7. write_fix_artifact           — capture stdout to reviews/build-fix.md;
                                      parse FIX COMPLETE / SKIPPED / BLOCKED;
                                      BLOCKED → E_FIX_BLOCKED
    GH379 (Class 5): build_decorr_prompt / invoke_decorr_llm / write_decorr_artifact
        — inserted after verify_fix_typecheck, before build_satisfaction_prompt.
          Decorrelated (cross-prompt) adversarial-refute verifier, FEATURE/COMPLEX
          only (SIMPLE skips). Writes reviews/build-decorr-verify.md. Advisory by
          default; enforce mode (org_config.decorrelated_verify_enforce, agreement
          769EFDA3, flip on >=5 real decorr_verify_verdict emissions reviewed clean —
          GH392 re-scope; flip-by:2026-08-01 backstop-review) blocks on SUSPECT with
          E_DECORR_VERIFY_SUSPECT.
    8. build_satisfaction_prompt    — deterministic; spec + GREEN log + review doc
    9. invoke_satisfaction_llm      — opaque subprocess (Opus satisfaction)
    10. write_satisfaction_doc      — capture stdout to reviews/build-satisfaction.md;
                                      parse SCORE; HARD GATE on threshold

Outputs:
    $SCRATCHPAD/reviews/build-review.md
    $SCRATCHPAD/reviews/build-fix.md
    $SCRATCHPAD/reviews/build-satisfaction.md
"""
from __future__ import annotations

import concurrent.futures
import json
import logging
import os
import re
import shutil
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from bytedigger_engine import telemetry_ctx
from bytedigger_engine.contracts import RetryPolicy, StepContract, StepResult, WorkflowDefinition, step
from bytedigger_engine.llm_subprocess import invoke_llm_subprocess, STRAGGLER_PATIENCE_SEC, STRAGGLER_POLL_INTERVAL_SEC, manifest_from_result, _ManifestMissingError, _ManifestError, prev_data_corruption_reason, _resolve_backend

from bytedigger_engine.lib.bounded_spawn import bounded_run  # noqa: E402
from bytedigger_engine.lib import git_write_port  # noqa: E402  5F06E98D — injectable git write-op seam
from bytedigger_engine.lib import git_port  # noqa: E402
from bytedigger_engine.lib.git_cwd import resolve_git_cwd, resolve_git_cwd_with_source, is_ambient_git_cwd  # noqa: E402  GH381/GH1220
from bytedigger_engine.lib.plugins.anti_hallucination.helper import (  # noqa: E402
    check_citation as _check_citation_impl,
    verify_findings as _verify_findings_impl,
    get_prompt_fragment as _get_anti_fab_prompt,
    get_behavioral_assertion_rubric as _get_behavioral_rubric,
    get_out_of_role_block as _get_out_of_role_block,
    _SEVERITY_HDR_RE as _SEVERITY_HDR_RE_PLUGIN,
    _EVIDENCE_QUOTE_RE as _EVIDENCE_QUOTE_RE_PLUGIN,
)
from bytedigger_engine.lib.plugins.anti_hallucination.semantic_verifier import (  # noqa: E402
    verify_findings_semantic as _verify_findings_semantic_impl,
)
from bytedigger_engine.lib.model_config import get_claude_critical, get_claude_primary, get_claude_decorrelated_verifier  # noqa: E402
from bytedigger_engine.lib.verdict_parse import last_line_anchored_marker  # noqa: E402
from bytedigger_engine.verdict_verify import _classify_parity, file_sha256  # noqa: E402  GH749/GH751
from bytedigger_engine.lib.worktree_root import resolve_worktree_root as _resolve_worktree_root  # noqa: E402
try:
    from ._baseline_delta import run_baseline_delta_gate  # noqa: E402  GH561 §1r lane-2
except ImportError:  # pragma: no cover — bare fallback for sys.path-rooted test imports (GH881)
    from bytedigger_engine.workflows._baseline_delta import run_baseline_delta_gate  # type: ignore[no-redef]  # noqa: E402  GH561 §1r lane-2
try:
    from .phase_workflows_common import (_emit_safe, _filter_gitignored_paths, _git_op_with_lock_retry, _git_write, _last_marker_wins, _maybe_emit_cross_tree_warning, _maybe_role_template, _paths_have_staged_changes, _read_engine_mode, _read_first_block, _resolve_command, _resolve_model, _resolve_scratchpad, _revert_cross_tree_modifications, _verify_no_cross_tree_edits, _worktree_edit_boundary_block, _CROSS_TREE_PROMPT_TEMPLATE, _ENGINE_MODE_RE, resolve_engine_mode)  # noqa: E402,F401  #261 Stage 0  3F5599A6  GH268
except ImportError:  # pragma: no cover — bare fallback for sys.path-rooted test imports (GH881)
    from bytedigger_engine.workflows.phase_workflows_common import (_emit_safe, _filter_gitignored_paths, _git_op_with_lock_retry, _git_write, _last_marker_wins, _maybe_emit_cross_tree_warning, _maybe_role_template, _paths_have_staged_changes, _read_engine_mode, _read_first_block, _resolve_command, _resolve_model, _resolve_scratchpad, _revert_cross_tree_modifications, _verify_no_cross_tree_edits, _worktree_edit_boundary_block, _CROSS_TREE_PROMPT_TEMPLATE, _ENGINE_MODE_RE, resolve_engine_mode)  # type: ignore[no-redef]  # noqa: E402,F401  #261 Stage 0  3F5599A6  GH268

# Step 7 (95D3E5F6) — W1 + disk-truth wiring. Phase 6 reviews CODE
# (schema {id, severity, path, description}), not specs
# (schema {id, type, evidence, required_action}).  We reuse the W1 lib's
# schema-agnostic parser core ``extract_structured_findings_raw`` and apply
# the phase_6 projection locally — no duplicate regex needed.
from bytedigger_engine.lib.plugins.checklist_convergence import (  # noqa: E402
    extract_structured_findings_raw as _extract_structured_findings_raw,
)
from bytedigger_engine.lib.plugins.disk_truth import git_diff_files, resolve_pre_phase_sha, run_test_command, parse_structured_block, enforce, SatisfactionVerdict, FixVerdict, SchemaViolation  # noqa: E402
from bytedigger_engine.lib.plugins.review_schema import (  # noqa: E402  812D2503 Ship B: canonical schema source
    PER_ROLE_SCHEMA_TEMPLATE,
    STRUCTURED_FINDINGS_DIRECTIVE_SHORT,
    ROLE_FINDINGS_COUNT_MARKER_RE,
    PARALLEL_DISPATCH_FRAMING_TEMPLATE,
    SEVERITY_HDR_LINE_RE,  # GH970: tolerant SEVERITY-header parse
    SEVERITY_HDR_MULTILINE_RE,  # GH970
    lint_role_report,  # GH970 D2: malformed-header lint
)
from bytedigger_engine.io_utils import atomic_write  # noqa: E402  DD34EEBF: scratchpad ref persistence
from bytedigger_engine.reject_log import record_satisfaction_reject  # noqa: E402  EECA708D
from bytedigger_engine.net_new_delta import delta_verdict  # noqa: E402  GH316 post-fix typecheck gate
from bytedigger_engine.lib.mypy_baseline import mypy_base_argv as _mypy_base_argv_p6, parse_mypy_output as _parse_mypy_output_p6  # noqa: E402  GH316
from bytedigger_engine.config_provider import timeout_policy_path, state_dir_prefix  # noqa: E402  GH285 C2
from bytedigger_engine.lib.timeout_policy import DEFAULT_POLICY, cached_policy, resolve_timeout_sec  # noqa: E402  GH285 C2


def _timeout_policy() -> dict:
    return cached_policy(str(timeout_policy_path()))


def _default_review_model() -> str:
    return get_claude_primary()


def _default_fix_model() -> str:
    return get_claude_primary()


def _default_satisfaction_model() -> str:
    return get_claude_critical()


# 25e75663: old argv-returning builders kept as back-compat aliases.
def _default_review_llm_command() -> list[str]:
    from bytedigger_engine.llm_subprocess import _build_claude_argv
    return _build_claude_argv(_default_review_model())


def _default_fix_llm_command() -> list[str]:
    from bytedigger_engine.llm_subprocess import _build_claude_argv
    return _build_claude_argv(_default_fix_model())


def _default_satisfaction_llm_command() -> list[str]:
    from bytedigger_engine.llm_subprocess import _build_claude_argv
    return _build_claude_argv(_default_satisfaction_model())


# Backward-compat module-level constants (computed once at import via ModelConfig).
DEFAULT_REVIEW_LLM_COMMAND = _default_review_llm_command()
DEFAULT_FIX_LLM_COMMAND = _default_fix_llm_command()
DEFAULT_SATISFACTION_LLM_COMMAND = _default_satisfaction_llm_command()
DEFAULT_REVIEW_TIMEOUT_SEC = DEFAULT_POLICY["review.reviewer"]["base"]
DEFAULT_REVIEW_TIMEOUT_SEC_FEATURE = DEFAULT_POLICY["review.reviewer"]["FEATURE"]  # 920C6935: FEATURE-class composite review
DEFAULT_REVIEW_TIMEOUT_SEC_COMPLEX = DEFAULT_POLICY["review.reviewer"]["COMPLEX"]  # 920C6935: COMPLEX-class composite review
DEFAULT_FIX_TIMEOUT_SEC = DEFAULT_POLICY["review.fix"]["base"]
FIX_WATCHDOG_NO_PROGRESS_MS = 60_000  # 775D6752: 60s matches idle_timeout_sec=60 for invoke_fix_llm
DEFAULT_SATISFACTION_TIMEOUT_SEC = DEFAULT_POLICY["review.satisfaction"]["base"]
DEFAULT_SATISFACTION_TIMEOUT_SEC_FEATURE = DEFAULT_POLICY["review.satisfaction"]["FEATURE"]  # 6CAFEB9B: FEATURE-class satisfaction step
DEFAULT_SATISFACTION_TIMEOUT_SEC_COMPLEX = DEFAULT_POLICY["review.satisfaction"]["COMPLEX"]  # 6CAFEB9B: COMPLEX-class satisfaction step
DEFAULT_SATISFACTION_THRESHOLD = 80
POST_FIX_PYTEST_TIMEOUT_SEC = 180  # 7A940850: post-fix sibling-regression gate
GIT_REV_PARSE_TIMEOUT_SEC = 10  # 3CE7007C R5: bound the synthetic-env probe


def _resolve_review_timeout_sec(cfg: dict | None) -> int:
    """review.reviewer timeout via unified policy (GH285 C2). Precedence:
    cfg.review_llm_timeout_sec > COMPLEX > FEATURE > base."""
    return resolve_timeout_sec("review.reviewer", cfg, policy=_timeout_policy())


def _resolve_fix_timeout_sec(cfg: dict | None) -> int:
    """review.fix timeout via unified policy (GH285 C2). Precedence:
    cfg.fix_llm_timeout_sec > base."""
    return resolve_timeout_sec("review.fix", cfg, policy=_timeout_policy())


def _resolve_satisfaction_timeout_sec(cfg: dict | None) -> int:
    """review.satisfaction timeout via unified policy (GH285 C2). Precedence:
    cfg.satisfaction_llm_timeout_sec > COMPLEX > FEATURE > base."""
    return resolve_timeout_sec("review.satisfaction", cfg, policy=_timeout_policy())


VALID_COMPLEXITIES: tuple[str, ...] = ("SIMPLE", "FEATURE", "COMPLEX")

# Reviewer row constants — shared between SIMPLE and FEATURE/COMPLEX dispatch tables.
# Single definition prevents silent drift when reviewer names or model pins change.
_ROW_CODE_REVIEWER = "  - pr-review-toolkit:code-reviewer — model: sonnet"
_ROW_SILENT_FAILURE_HUNTER = "  - pr-review-toolkit:silent-failure-hunter — model: sonnet"
_ROW_TYPE_DESIGN_ANALYZER = "  - pr-review-toolkit:type-design-analyzer — model: sonnet"
_ROW_PR_TEST_ANALYZER = "  - pr-review-toolkit:pr-test-analyzer — model: sonnet"
_ROW_CODE_SIMPLIFIER = "  - pr-review-toolkit:code-simplifier — model: sonnet"
_ROW_COMMENT_ANALYZER = "  - pr-review-toolkit:comment-analyzer — model: haiku"
_ROW_DEVOPS_REVIEWER = "  - devops-reviewer — model: sonnet — focus: CIS/OWASP/SLSA standards compliance for the detected devops artifact type"

REVIEW_DOC_RELPATH = "reviews/build-review.md"
REVIEW_FIX_DOC_RELPATH = "reviews/build-review-fix.md"
FIX_DOC_RELPATH = "reviews/build-fix.md"
SATISFACTION_DOC_RELPATH = "reviews/build-satisfaction.md"
SPEC_DOC_RELPATH = "specs/build-spec.md"
RED_LOG_RELPATH = "tests/build-red-output.log"
GREEN_LOG_RELPATH = "tests/build-green-output.log"

VERDICT_PASS = "PASS"
VERDICT_PARTIAL = "PARTIAL"
VERDICT_FAIL = "FAIL"
VERDICT_UNKNOWN = "UNKNOWN"
VERDICT_SUSPECT = "SUSPECT"  # 21792EE7: all findings filtered → could be fabrication, not clean
SUSPECT_FINDINGS_SECTION_HEADER = "## Suspect Findings"  # 4B9DF7D3: single-source — review-doc section written by aggregation (L1508) AND read by the satisfaction override gate

FIX_COMPLETE = "COMPLETE"
FIX_BLOCKED = "BLOCKED"
FIX_SKIPPED = "SKIPPED"
FIX_NO_MARKER = "NO_MARKER"

# Regexes for _verify_findings citation parsing — canonical source in plugin.
_SEVERITY_HDR_RE = _SEVERITY_HDR_RE_PLUGIN
_EVIDENCE_QUOTE_RE = _EVIDENCE_QUOTE_RE_PLUGIN

logger = logging.getLogger(__name__)


def extract_structured_findings(review_text: str) -> list[dict] | None:
    """Parse the ``## Findings (structured)`` ```json``` block, projecting each item
    onto the phase_6 code-review schema ``{id, severity, path, description}``.
    Parsing is delegated to ``checklist_convergence.extract_structured_findings_raw``
    (the schema-agnostic core); only the projection below is phase_6-specific.
    ``path`` is intentionally NOT ``str()``-coerced — ``_emit_fix_disk_truth_telemetry``
    handles a list value."""
    raw = _extract_structured_findings_raw(review_text)
    if raw is None:
        return None
    return [
        {
            "id": str(i.get("id", "")),
            "severity": str(i.get("severity", "")),
            "path": i.get("path", ""),
            "description": str(i.get("description", "")),
        }
        for i in raw
    ]


def _parse_files_modified(raw: str) -> list[str] | None:
    """Parse a ``files_modified: a.py, b.py, c.py`` line from the fix doc.

    Returns ``None`` if no such line is present; an empty list if the line
    is present but lists nothing. Tolerant to leading whitespace and
    surrounding quotes/brackets.
    """
    if not raw:
        return None
    m = re.search(
        r"^\s*files_modified\s*:\s*(.*)$",
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


def _emit_fix_disk_truth_telemetry(
    ctx, prev: StepResult, raw: str
) -> None:
    """Emit ``fix_disk_truth_coverage`` (always) and ``fix_files_drift``
    (only when fix doc carries an LLM-claimed ``files_modified:`` line)
    after a fix artifact has been written. Telemetry-first additive
    observability — never raises, never affects the verdict gate.
    """
    try:
        scratchpad = _resolve_scratchpad(ctx)
        worktree_root = _resolve_worktree_root(ctx, scratchpad)
        # Pre-fix SHA: prefer prev.data["pre_fix_sha"] if upstream stages
        # ever populate it; else best-effort current HEAD. Telemetry is
        # additive; an approximate diff is acceptable while the engine
        # learns to thread pre-phase SHAs explicitly.
        pre_fix_sha = ""
        if isinstance(prev.data, dict):
            pre_fix_sha = str(prev.data.get("pre_fix_sha") or "")
        if not pre_fix_sha:
            try:
                pre_fix_sha = resolve_pre_phase_sha(worktree_root)
            except Exception:  # noqa: BLE001
                pre_fix_sha = ""
        try:
            disk_actual = git_diff_files(pre_fix_sha, worktree_root)
        except Exception:  # noqa: BLE001
            disk_actual = []
        if not isinstance(disk_actual, list):
            disk_actual = list(disk_actual or [])

        # Read the review doc (from prev.data) and parse structured findings.
        review_doc_path = ""
        if isinstance(prev.data, dict):
            review_doc_path = str(prev.data.get("review_doc_path") or "")
        review_text = ""
        if review_doc_path:
            try:
                review_text = Path(review_doc_path).read_text(encoding="utf-8")
            except OSError:
                review_text = ""
        findings = extract_structured_findings(review_text)
        structured_block_present = findings is not None
        if findings is None:
            findings = []

        n_findings = len(findings)
        disk_actual_set = set(disk_actual)
        n_addressed = 0
        for f in findings:
            paths_field = f.get("path", "")
            if isinstance(paths_field, list):
                f_paths = [str(p) for p in paths_field if p]
            else:
                f_paths = [str(paths_field)] if paths_field else []
            if any(p in disk_actual_set for p in f_paths):
                n_addressed += 1
        n_uncovered = n_findings - n_addressed
        coverage_ratio = (
            round(n_addressed / n_findings, 3) if n_findings > 0 else 0.0
        )
        _emit_safe(
            "fix_disk_truth_coverage",
            {
                "n_findings": n_findings,
                "n_addressed": n_addressed,
                "n_uncovered": n_uncovered,
                "coverage_ratio": coverage_ratio,
                "structured_block_present": structured_block_present,
            },
        )

        llm_claimed = _parse_files_modified(raw)
        if llm_claimed is not None:
            llm_set = set(llm_claimed)
            extra_in_llm = sorted(llm_set - disk_actual_set)
            missing_from_llm = sorted(disk_actual_set - llm_set)
            _emit_safe(
                "fix_files_drift",
                {
                    "llm_claimed": list(llm_claimed),
                    "disk_actual": list(disk_actual),
                    "extra_in_llm": extra_in_llm,
                    "missing_from_llm": missing_from_llm,
                },
            )
    except Exception as exc:  # noqa: BLE001
        # Telemetry must never break the workflow.
        logger.warning("fix_disk_truth_telemetry skipped: %s", exc)


# ─── helpers ─────────────────────────────────────────────────────────────────


# GH358: the StepContract.resume_sentinel seam (engine.py maybe_read/write_sentinel
# + lib/step_sentinel.py + lib/resume_keying.py) is the only mid-phase resume path.


def _security_high(ctx) -> bool:
    cfg = ctx.org_config or {}
    return str(cfg.get("security_classification") or "").upper() == "HIGH"


def _resolve_complexity(ctx) -> str:
    """Return complexity string from org_config, defaulting to FEATURE if not provided.

    None → FEATURE: callers that omit complexity receive the standard 6-reviewer path.
    The SIMPLE 3-reviewer path is available when complexity is explicitly set to 'SIMPLE'.
    """
    cfg = ctx.org_config or {}
    raw = cfg.get("complexity")
    if raw is None:
        _emit_safe("complexity_default_used", {"fallback": "FEATURE", "reason": "complexity_not_provided"})
        return "FEATURE"
    if raw not in VALID_COMPLEXITIES:
        raise ValueError(f"complexity must be one of {VALID_COMPLEXITIES}, got {raw!r}")
    return raw


def _select_reviewers(complexity: str, artifact_type: str | None = None) -> tuple[str, int]:
    if complexity == "SIMPLE":
        rows_list = [_ROW_CODE_REVIEWER, _ROW_SILENT_FAILURE_HUNTER, _ROW_PR_TEST_ANALYZER]
        count = 3
    else:
        rows_list = [_ROW_CODE_REVIEWER, _ROW_SILENT_FAILURE_HUNTER, _ROW_TYPE_DESIGN_ANALYZER,
                     _ROW_PR_TEST_ANALYZER, _ROW_CODE_SIMPLIFIER, _ROW_COMMENT_ANALYZER]
        count = 6
    # Stage 4 (27843297): append devops reviewer when artifact_type is non-empty.
    if artifact_type:  # truthy check — empty string treated as falsy per AC12
        rows_list.append(_ROW_DEVOPS_REVIEWER)
        count += 1
    return "\n".join(rows_list), count


def _parse_review_verdict(raw: str) -> str:
    """Last-marker-wins: trailing VERDICT line is the final answer.

    Tolerates the prompt's own output-schema listing of all three verdicts
    (PASS / PARTIAL / FAIL) — only the LATEST occurrence is treated as the
    LLM's verdict. Real LLM output ends with EXACTLY one verdict per spec,
    making this both safe and aligned with the prompt instruction.
    """
    return _last_marker_wins(
        raw,
        [
            ("VERDICT: PASS", VERDICT_PASS),
            ("VERDICT: PARTIAL", VERDICT_PARTIAL),
            ("VERDICT: FAIL", VERDICT_FAIL),
        ],
        VERDICT_UNKNOWN,
    )


def _derive_fallback_verdict(content: str) -> str:
    """stdout-fallback-path verdict: derive from the ## Findings (structured)
    block (mirrors _aggregate_review_findings); never auto-PASS when the block
    is absent. 4E0BAC38 (band-aid #10 residual)."""
    findings = extract_structured_findings(content)
    if findings is not None:
        counts = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
        for f in findings:
            sev = str(f.get("severity", "")).strip().upper()
            if sev in counts:
                counts[sev] += 1
        if counts["CRITICAL"] > 0 or counts["HIGH"] > 0:
            return VERDICT_FAIL
        if counts["MEDIUM"] > 0 or counts["LOW"] > 0:
            return VERDICT_PARTIAL
        return VERDICT_PASS
    mv = _parse_review_verdict(content)
    if mv == VERDICT_PASS:
        return VERDICT_SUSPECT
    if mv in (VERDICT_PARTIAL, VERDICT_FAIL):
        return mv
    return VERDICT_SUSPECT


def _parse_fix_status(raw: str) -> str:
    """Last-marker-wins: trailing FIX line is the final answer.

    Replaces workflow-level retry loop (deferred) with marker-based
    signalling — the LLM does its own iteration within one subprocess
    (cheaper tokens, retains context across attempts). Mirrors phase-5
    GREEN improvement C'.

    BLOCKED / NO_MARKER both signal failure. SKIPPED is a no-op success
    (review verdict was PASS, no findings to fix). Tolerates the prompt's
    output-schema listing all three markers — last in response wins.
    """
    return _last_marker_wins(
        raw,
        [
            ("FIX COMPLETE", FIX_COMPLETE),
            ("FIX SKIPPED", FIX_SKIPPED),
            ("FIX BLOCKED", FIX_BLOCKED),
        ],
        FIX_NO_MARKER,
    )


def _parse_satisfaction_score(raw: str) -> int | None:
    """Extract `SCORE: <0-100>` from evaluator output. Missing/malformed → None.

    Tolerant to surrounding markdown (e.g. ``**SCORE: 87**``, ``SCORE: 87/100``).
    None means "no parseable score" — gate treats that as below-threshold by
    design (silent omission must not pass).
    """
    ms = list(re.finditer(r"^\s*\**\s*SCORE:\s*\**\s*(\d{1,3})", raw, re.IGNORECASE | re.MULTILINE))
    if not ms:
        return None
    score = int(ms[-1].group(1))
    return score if 0 <= score <= 100 else None


def _parse_satisfaction_structured(raw: str) -> "tuple[SatisfactionVerdict | None, str | None]":
    """Parse the structured JSON satisfaction-output block from evaluator output.

    Returns:
        (SatisfactionVerdict instance, None) on success.
        (None, "absent") if no structured block found.
        (None, "malformed") if block header present but JSON unparseable.
        (None, f"schema_violation: <field/reason>") on disk_truth schema rejection.
    """
    if not raw:
        return None, "absent"
    payload = parse_structured_block(raw, "satisfaction-output")
    if payload is None:
        if re.search(r"^##\s+satisfaction-output\s*\(\s*structured\s*\)", raw, re.MULTILINE):
            return None, "malformed"
        return None, "absent"
    if not isinstance(payload, dict):
        return None, "schema_violation: payload is not a dict"
    try:
        verdict = enforce(payload, SatisfactionVerdict)
    except SchemaViolation as e:
        return None, f"schema_violation: {e}"
    return verdict, None


def _parse_fix_structured(raw: str) -> "tuple[FixVerdict | None, str | None]":
    """Parse the structured JSON fix-output block from fix-worker output.

    Returns:
        (FixVerdict instance, None) on success.
        (None, "absent") if no structured block found.
        (None, "malformed") if block header present but JSON unparseable.
        (None, f"schema_violation: <field/reason>") on disk_truth schema rejection.
    """
    if not raw:
        return None, "absent"
    payload = parse_structured_block(raw, "fix-output")
    if payload is None:
        if re.search(r"^##\s+fix-output\s*\(\s*structured\s*\)", raw, re.MULTILINE):
            return None, "malformed"
        return None, "absent"
    if not isinstance(payload, dict):
        return None, "schema_violation: payload is not a dict"
    try:
        verdict = enforce(payload, FixVerdict)
    except SchemaViolation as e:
        return None, f"schema_violation: {e}"
    return verdict, None


def _normalize_ws(s: str) -> str:
    return re.sub(r"\s+", " ", s).strip()


# Backward-compat alias — tests import _check_citation from this module.
_check_citation = _check_citation_impl


# ─── Step 1: build review prompt ─────────────────────────────────────────────


# 3F5599A6 D1 (55802041): load the prior fix-cycle's post-fix pytest report for
# injection into the review prompt (§1aa named helper). Uses
# _POSTFIX_PYTEST_REPORT_RELPATH as the single source of truth for the relpath
# (§1g) — the _run_pytest_post_fix writer references the same constant.
def _load_postfix_pytest_report(scratchpad: Path) -> tuple[str, bool] | None:
    """Return (text, truncated) for the post-fix pytest report artifact under
    scratchpad (relpath = _POSTFIX_PYTEST_REPORT_RELPATH).

    `text` is the full decoded content when the file is <=
    _POSTFIX_REPORT_MAX_BYTES; otherwise the LAST _POSTFIX_REPORT_MAX_BYTES
    bytes (tail-capped), decoded with errors="replace". Returns None when the
    file is missing or unreadable (OSError).
    """
    report_path = scratchpad / _POSTFIX_PYTEST_REPORT_RELPATH
    try:
        raw = report_path.read_bytes()
    except OSError:
        return None
    truncated = len(raw) > _POSTFIX_REPORT_MAX_BYTES
    tail = raw[-_POSTFIX_REPORT_MAX_BYTES:] if truncated else raw
    return tail.decode("utf-8", errors="replace"), truncated


# GH705 §2/§1d: maximal contiguous CALL-INVARIANT static instruction run
# hoisted from the review-prompt scaffold — the ANCHOR-BIAS GUARD literal
# followed by the TEST-QUALITY GATE literal. Substituting one
# parts.append(_REVIEW_STABLE_PREFIX) for the two original adjacent
# parts.append(...) calls preserves the joined prompt byte-for-byte.
_REVIEW_STABLE_PREFIX = (
    "ANCHOR-BIAS GUARD (read this once, apply to every finding):\n"
    "\n"
    "The SPEC describes design intent at the time it was written; the IMPLEMENTATION\n"
    "in the worktree may have intentionally drifted to better alternatives during\n"
    "the GREEN phase. When the SPEC and the IMPL conflict, TRUST THE IMPL — spec/impl\n"
    "drift is expected and is NOT a bug.\n"
    "\n"
    "For every finding you would report, you MUST verify every citation against\n"
    "the current file via the Read tool. Quote the actual line content verbatim\n"
    "in the format `> path:line: <verbatim line content>`. If the cited line does\n"
    "NOT contain the bug shape you describe, REJECT YOUR OWN FINDING before writing\n"
    "it — the bug you imagined exists only in the spec, not the code.\n"
    "\n"
    "Findings without a verifiable verbatim quote (or with a quote that does not\n"
    "match the file at HEAD) WILL BE DROPPED by the deterministic Python aggregator.\n"
    "They will not count toward the severity totals or the final verdict — your\n"
    "review will be silently lighter than it appears. Verify every citation."
    "\n"
    "TEST-QUALITY GATE: if any test in the GREEN report matches the "
    "FORBIDDEN shape in the rubric below — pattern-presence on source "
    "files instead of function call + observable side-effect — report "
    "it as a HIGH finding with severity HIGH and a verbatim quote of "
    "the offending test line. Pattern-presence-only tests passed dead "
    "code in W1 (B4D83B40); reviewers must catch them."
)


# GH705 §2.1: maximal contiguous CALL-INVARIANT static instruction run hoisted
# from the satisfaction-prompt scaffold — the PHASE-SPECIFIC ANTI-FAB
# (SATISFACTION) literal. Substituting parts.append(_SATISFACTION_STABLE_PREFIX)
# for the original literal parts.append(...) preserves the joined prompt
# byte-for-byte.
_SATISFACTION_STABLE_PREFIX = (
    "PHASE-SPECIFIC ANTI-FAB (SATISFACTION):\n"
    "  - Each dimension score MUST be backed by an evidence bullet citing\n"
    "    a real file/test. Don't score blind. Don't default to 90+ because\n"
    "    nothing looks broken at a glance.\n"
    "  - Composite is MIN, not average. A 60 in any dimension = composite\n"
    "    60. Don't smooth it out.\n"
    "  - Genuinely n/a dimension (e.g. Boy Scout for SIMPLE) → write `n/a`\n"
    "    and exclude from MIN. Do NOT invent a score.\n"
    "  - Concerns = `none` only when you actually looked. Skimmed diff →\n"
    "    surface unverified parts as a concern.\n"
    "\n"
    "End your response with EXACTLY one of: `VERDICT: PASS` or `VERDICT: FAIL`.\n"
    "SCORE line MUST be present and numeric — missing/malformed score blocks pipeline."
)


def _build_review_prompt(ctx, _prev) -> StepResult:
    scratchpad = _resolve_scratchpad(ctx)
    # C834481A: load prior findings for orchestrator-driven re-attempt injection.
    _prior_findings_data: dict | None = None
    _last_findings_path = scratchpad / "reviews" / "last_findings.json"
    if _last_findings_path.is_file():
        try:
            _prior_findings_data = json.loads(
                _last_findings_path.read_text(encoding="utf-8")
            )
        except (OSError, ValueError, TypeError):
            _prior_findings_data = None
    complexity = _resolve_complexity(ctx)
    _artifact_type = (ctx.org_config or {}).get("artifact_type") if ctx else None
    dispatch_table, reviewer_count = _select_reviewers(complexity, artifact_type=_artifact_type)
    spec_path = scratchpad / SPEC_DOC_RELPATH
    red_log = scratchpad / RED_LOG_RELPATH
    green_log = scratchpad / GREEN_LOG_RELPATH
    review_doc_path = scratchpad / REVIEW_DOC_RELPATH

    # Agreement E52F241F (+ duplicate 62DC65FA): pin all reviews/* path refs
    # to ABSOLUTE scratchpad paths in the prompt. Sub-agents dispatched via
    # Agent tool inherit the worktree-root CWD, NOT scratchpad — relative
    # ``reviews/role-X.md`` would land at ``<worktree_root>/reviews/...``,
    # outside the aggregator glob and leaving stray files in the repo tree.
    reviews_dir = scratchpad / "reviews"
    abs_review_doc = str(review_doc_path)
    abs_reviews_dir = str(reviews_dir)

    parts: list[str] = []
    role = _maybe_role_template(ctx)
    if role:
        parts.append(role.rstrip())
        parts.append("")
    parts.append(_read_first_block(scratchpad))
    parts.append("")
    parts.append(
        f"ROLE: You are a review orchestrator. Spawn {reviewer_count} parallel pr-review-toolkit "
        "sub-agent reviews via the Agent tool, then aggregate their findings into "
        f"{abs_review_doc}. VERIFICATION-ONLY — do NOT edit code or test files."
    )
    parts.append("")
    parts.append("FEATURE REQUEST:")
    parts.append(ctx.question or "(no feature request provided)")
    parts.append("")
    if spec_path.is_file():
        parts.append(f"SPEC (read this file): {spec_path}")
    else:
        parts.append(f"SPEC: (none at {spec_path} — phase 4.5 missing; STATUS=block)")
    if resolve_engine_mode(str(spec_path), ctx) == "test_only":
        parts.append("")
        parts.append(
            "## TEST-ONLY BUILD\n"
            "\n"
            "The RED step was skipped because this build is test-only "
            "(engine-mode: test_only). The fix lives in the test patches; there is "
            "**no separate production diff** for this build.\n"
            "\n"
            "STRICT for every reviewer:\n"
            "- Do NOT report \"missing production implementation\", \"no prod diff\", "
            "or \"tests have no corresponding source change\" as a finding — that is "
            "the intended, declared shape of a test-only build."
        )
        _emit_safe(
            "phase_6_test_only_note_injected",
            {"phase": 6, "step": "build_review_prompt", "engine_mode": "test_only"},
        )
    if red_log.is_file():
        parts.append(f"RED WORKER REPORT (read this file for test paths): {red_log}")
    if green_log.is_file():
        parts.append(f"GREEN WORKER REPORT (read this file for impl paths): {green_log}")
    # 3F5599A6 D1 (55802041): inject the prior fix-cycle's post-fix pytest
    # report so reviewers see regression evidence instead of re-deriving it.
    _postfix = _load_postfix_pytest_report(scratchpad)
    if _postfix is not None:
        parts.append("")
        parts.append("## POST-FIX PYTEST REPORT (prior fix-cycle regression evidence)")
        parts.append(
            "Reviewers MUST read this report and file explicit findings for any "
            "sibling-test regressions it lists (do not rely on root-cause dedupe)."
        )
        parts.append(_postfix[0])
        _emit_safe(
            "composite_postfix_pytest_consumed",
            {
                "phase": 6,
                "step": "build_review_prompt",
                "report_bytes": len(_postfix[0]),
                "truncated": _postfix[1],
            },
        )
    parts.append("")
    parts.append(
        PARALLEL_DISPATCH_FRAMING_TEMPLATE.format(
            reviewer_count=reviewer_count,
            abs_reviews_dir=abs_reviews_dir,
            per_role_schema=PER_ROLE_SCHEMA_TEMPLATE,
            dispatch_table=dispatch_table,
        )
    )
    # DFB5B0CB: inject BUILD SCOPE block so reviewers cannot hallucinate findings
    # about files that were not changed in this build.
    _pre_red_ref_path = scratchpad / "integrity" / "pre-red-ref.txt"
    _build_start_sha: str = ""
    if _pre_red_ref_path.is_file():
        _build_start_sha = _pre_red_ref_path.read_text(encoding="utf-8").strip()
    if not _build_start_sha:
        # Degraded path: fall back to resolve_pre_phase_sha; if still empty, skip block.
        _worktree_root = _resolve_worktree_root(ctx, scratchpad)
        try:
            _build_start_sha = resolve_pre_phase_sha(_worktree_root) or ""
        except Exception:  # noqa: BLE001
            _build_start_sha = ""
        if not _build_start_sha:
            _emit_safe("reviewer_scope_degraded", {"reason": "ref_missing"})
    if _build_start_sha:
        _worktree_root = _resolve_worktree_root(ctx, scratchpad)
        _changed_files = git_diff_files(_build_start_sha, _worktree_root, untracked=True)
        _excluded_count = 0
        _changed_files_filtered: list[str] = []
        for _p in _changed_files:
            if any(_p.startswith(_pre) for _pre in _build_scope_exclude_prefixes()):
                _excluded_count += 1
            else:
                _changed_files_filtered.append(_p)
        _changed_files = _changed_files_filtered
        if _excluded_count > 0:
            _emit_safe(
                "build_scope_excluded",
                {
                    "count": _excluded_count,
                    "prefixes": list(_build_scope_exclude_prefixes()),
                },
            )
        if _changed_files:
            _files_block = "\n".join(f"- {p}" for p in _changed_files)
        else:
            _files_block = "(no files changed yet — this is unusual)"
        parts.append("")
        parts.append(
            f"## BUILD SCOPE (authoritative — DO NOT speculate beyond)\n"
            f"\n"
            f"Build start SHA: {_build_start_sha}\n"
            f"Files changed in this build (verified via `git diff {_build_start_sha}..HEAD --name-only`):\n"
            f"{_files_block}\n"
            f"\n"
            f"STRICT RULES for every reviewer:\n"
            f"- Do NOT report findings about any file NOT in the above list.\n"
            f"- Every cited path:line MUST be in this list. Verify before citing.\n"
            f"- If you are tempted to cite a path not in the list — STOP. That file did not change in this build; any finding about it is fabrication."
        )
    # 7CA211D2: inject sub-agent propagation directive when last_findings.json is present.
    if _prior_findings_data is not None:
        _prop_attempt = _prior_findings_data.get("attempt", 1)
        _prop_structured = _prior_findings_data.get("structured_findings", [])
        _abs_last_findings = str(scratchpad / "reviews" / "last_findings.json")
        parts.append("")
        parts.append(
            f"## SUB-AGENT PRIOR-CONTEXT PROPAGATION\n"
            f"\n"
            f"Each dispatched Agent MUST read the prior findings file before reviewing:\n"
            f"    {_abs_last_findings}\n"
            f"\n"
            f"For each finding in that file:\n"
            f"- If still present in the latest commit, re-flag it with the tag "
            f"\"PRIOR — still present\".\n"
            f"- If clearly resolved by the latest commit, do NOT re-flag it — "
            f"verify file:line before re-listing.\n"
            f"- New findings are allowed only if they are NOT already in the prior list."
        )
        _emit_safe(
            "phase_6_subagent_prior_context_propagated",
            {
                "prior_attempt": _prop_attempt,
                "finding_count": len(_prop_structured),
            },
        )
    # C834481A: inject Prior-Attempt Context when last_findings.json is present.
    if _prior_findings_data is not None:
        _prior_attempt = _prior_findings_data.get("attempt", 1)
        _prior_score = _prior_findings_data.get("score")
        _prior_threshold = _prior_findings_data.get("threshold", DEFAULT_SATISFACTION_THRESHOLD)
        _prior_structured = _prior_findings_data.get("structured_findings", [])
        _prior_findings_json = json.dumps(_prior_structured, indent=2)
        parts.append("")
        parts.append(
            f"## Prior-Attempt Context\n"
            f"\n"
            f"This is RE-ATTEMPT {_prior_attempt} of phase_6_review for this build. "
            f"Prior attempt scored {_prior_score}/{_prior_threshold} threshold. "
            f"Below are the findings raised previously — the orchestrator has since made "
            f"fix commits and is re-invoking phase_6:\n"
            f"\n"
            f"{_prior_findings_json}\n"
            f"\n"
            f"DIRECTIVES:\n"
            f"- Focus on whether each prior finding is now correctly resolved by the latest commit. "
            f"If unresolved, re-flag with note \"PRIOR — still present\".\n"
            f"- New findings allowed ONLY if NOT in the prior list.\n"
            f"- Re-flagging a finding that the latest commit clearly resolves is a hallucination "
            f"— verify file:line before re-listing."
        )
        _emit_safe(
            "phase_6_inject_prior_context",
            {
                "prior_attempt": _prior_attempt,
                "prior_score": _prior_score,
                "finding_count": len(_prior_structured),
            },
        )
    parts.append("")
    parts.append("STRUCTURED FINDINGS")  # 812D2503 Ship B: marker-line escape valve — preserves sibling-test anchors (F34E2C82 R-B2 precedent)
    parts.append(STRUCTURED_FINDINGS_DIRECTIVE_SHORT)
    if _security_high(ctx):
        parts.append(
            "\nSECURITY ADDENDUM (security_classification=HIGH): after all sub-agent "
            "responses are collected, include an inline security-reviewer synthesis "
            "covering OWASP Top 10 — injection vectors, auth bypass paths, secret "
            "exposure. No 7th Agent call — synthesize as an additional ## section "
            "in the aggregated review."
        )
    parts.append("## Aggregated Findings")  # passthrough_stub conformance marker — Python aggregator owns the real schema (F34E2C82, parent 9D520664)
    parts.append("")
    parts.append(_get_anti_fab_prompt())
    parts.append("")
    parts.append(_REVIEW_STABLE_PREFIX)
    parts.append(_get_behavioral_rubric())
    parts.append(
        "OUTPUT — Use the Write tool to write the full aggregated review to the review file named above. "
        "Your final response is a status summary under 200 tokens, ending with one line: "
        "VERDICT: <PASS|PARTIAL|FAIL>. Do NOT echo the review body — it is already on disk. "
        "Your status summary MUST begin with a line exactly `## Aggregated Findings`."
    )

    prompt = "\n".join(parts) + "\n\n" + _get_out_of_role_block()
    try:
        spec_sha = file_sha256(spec_path) if spec_path.is_file() else None
    except (OSError, ValueError):
        spec_sha = None
    _emit_safe("review_spec_anchor", {"spec_sha": spec_sha, "spec_path": str(spec_path), "phase": 6})  # GH751 drift-anchor
    return StepResult(
        status="ok",
        data={
            "prompt": prompt,
            "doc_path": str(review_doc_path),
            "spec_path": str(spec_path),
            "spec_sha": spec_sha,
            "red_log_path": str(red_log),
            "green_log_path": str(green_log),
            "spec_present": spec_path.is_file(),
            "red_present": red_log.is_file(),
            "green_present": green_log.is_file(),
            "prompt_bytes": len(prompt.encode("utf-8")),
            "stable_prefix": _REVIEW_STABLE_PREFIX,
        },
        duration_ms=0,
        step_name="build_review_prompt",
    )


# ─── Step 2: invoke review LLM ───────────────────────────────────────────────


def _invoke_review_llm(ctx, prev) -> StepResult:
    if not isinstance(prev, StepResult) or not isinstance(prev.data, dict):
        return StepResult(
            status="error", data=None, duration_ms=0,
            step_name="invoke_review_llm",
            error="prev step did not produce a prompt",
            error_code="E_MISSING_PREV_DATA",
        )

    cfg = ctx.org_config or {}
    # E8433B4E: thread complexity to aggregate_review_findings via prev.data so
    # it can compute min_floor / fanout banner. Resolved from ctx.org_config
    # (defaults to FEATURE if absent — matches _build_review_prompt path).
    try:
        complexity = _resolve_complexity(ctx)
    except Exception:
        complexity = None
    extra: dict = {
        "doc_path": prev.data["doc_path"],
        "spec_path": prev.data["spec_path"],
        "red_log_path": prev.data["red_log_path"],
        "green_log_path": prev.data["green_log_path"],
        "prompt": prev.data["prompt"],
    }
    if complexity:
        extra["complexity"] = complexity

    # CCBB65DC: straggler-abort watchdog wiring — opt-in via org_config["straggler_abort"].
    # CF2EE8ED §3.2: claude-in-session lacks 'abort' capability. If org_config
    # requests straggler_abort under in-session backend, WARN loudly and
    # auto-degrade. Ratified 2026-05-24.
    if cfg.get("straggler_abort") and _resolve_backend(None, os.environ)[0] == "claude-in-session":
        logger.warning(
            "straggler_abort=true under claude-in-session backend is unsupported "
            "(no 'abort' capability); auto-degrading to straggler_abort=false for "
            "this call. Fix org_config to silence this warning."
        )
        _emit_safe(
            "straggler_abort_skipped_in_session",
            {"reason": "in-session backend lacks abort capability"},
        )
        cfg = {**cfg, "straggler_abort": False}
    straggler_cfg = None
    _scfg = cfg
    if _scfg.get("straggler_abort"):
        try:
            _scratchpad = _resolve_scratchpad(ctx)
            _artifact_type = _scfg.get("artifact_type") if ctx else None
            _, _reviewer_count = _select_reviewers(complexity or "FEATURE", _artifact_type)
            straggler_cfg = {
                "reviews_dir": str(_scratchpad / "reviews"),
                "expected_n": int(_reviewer_count),
                "patience_sec": float(_scfg.get("straggler_patience_sec") or STRAGGLER_PATIENCE_SEC),
                "poll_interval_sec": float(_scfg.get("straggler_poll_interval_sec") or STRAGGLER_POLL_INTERVAL_SEC),
            }
        except Exception:
            logger.warning(
                "failed to build straggler_cfg; disabling straggler abort for this call",
                exc_info=True,
            )
            straggler_cfg = None

    result = invoke_llm_subprocess(
        prompt=prev.data["prompt"],
        model=_resolve_model(cfg, "review_model", _default_review_model()),
        timeout_sec=_resolve_review_timeout_sec(cfg),
        step_name="invoke_review_llm",
        extra_data=extra,
        allowed_tools=["Read", "Grep", "Glob", "Write"],
        straggler_cfg=straggler_cfg,
        stable_prefix=prev.data.get("stable_prefix", ""),
    )
    return result


# ─── Step 3: aggregate review findings (Option A — Python dedup) ─────────────
#
# 319C2DCF follow-up: replace outer-Sonnet aggregation with deterministic
# Python dedup over per-role review files. Each pr-review-toolkit Agent writes
# its own ``reviews/role-<slug>.md`` directly via Write tool. After dispatch
# returns, this step globs role-*.md, parses ``### SEVERITY:`` headers,
# deduplicates by (severity, normalized title), and produces the canonical
# composite-review content. ``write_review_artifact`` consumes the result.
#
# Backward-compat: when no role files exist (legacy stubs / prompt drift),
# this step returns E_NO_ROLE_FILES with skip_on_error=True so the workflow
# continues and write_review_artifact falls back to the legacy stdout/disk
# resolution path.

# Header pattern: "### SEVERITY: <LEVEL> — <title>" (em-dash) or "- <title>" (hyphen).
# GH970: tolerant of a 2-4 hash prefix (## / ### / ####); source of truth moved
# to plugins.review_schema.canonical.SEVERITY_HDR_MULTILINE_RE (§1g).
_AGG_SEVERITY_HDR_RE = SEVERITY_HDR_MULTILINE_RE
# 906E37DC: per-role self-count anchor — sub-reviewers write `<!-- role-findings-count: N -->`
# as the last line of their role file. Last match wins per file; absent => 0 contribution.
# 812D2503 Ship B: regex source-of-truth moved to plugins.review_schema.canonical;
# local alias retained to minimize call-site churn (permitted per spec §1.2 B1).
_ROLE_SELFCOUNT_RE = ROLE_FINDINGS_COUNT_MARKER_RE
_AGG_TITLE_NORMALIZE_RE = re.compile(r"[^a-z0-9]+")
_SEVERITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}


def _normalize_finding_title(title: str) -> str:
    """Lowercase + collapse non-alphanum runs to single space + strip."""
    return _AGG_TITLE_NORMALIZE_RE.sub(" ", title.lower()).strip()


def _slug_from_role_filename(path: Path) -> str:
    """``reviews/role-code-reviewer.md`` → ``code-reviewer``."""
    name = path.stem  # strip .md
    return name[len("role-"):] if name.startswith("role-") else name


def _extract_expected_slugs(dispatch_table: str) -> list[str]:
    """Parse expected reviewer slugs from the dispatch table string.

    Dispatch lines have format:  ``  - pr-review-toolkit:<slug> — model: <model>``
    """
    import re as _re
    slugs: list[str] = []
    for m in _re.finditer(r":([\w-]+?)\s+—\s+model:", dispatch_table):
        slugs.append(m.group(1))
    return slugs


def _parse_role_findings(content: str) -> list[dict]:
    """Extract findings from a role file. Each entry: {severity, title, block}.

    block = full text from `### SEVERITY:` header through next `### SEVERITY:`
    header (or end of file). Trailing `VERDICT:` lines are excluded from the
    last finding's block.
    """
    findings: list[dict] = []
    matches = list(_AGG_SEVERITY_HDR_RE.finditer(content))
    for i, m in enumerate(matches):
        severity = m.group(1).upper()
        title = m.group(2).strip()
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        block = content[start:end].rstrip()
        # Strip trailing VERDICT line if present (it belongs to the file, not the finding)
        block = re.sub(r"\n\s*VERDICT:\s*\S+\s*$", "", block, flags=re.IGNORECASE)
        findings.append({"severity": severity, "title": title, "block": block.rstrip()})
    return findings


# GH1354: tail tags APPENDED to a finding header AFTER the producer wrote it
# (verify_findings' ' [UNCITED]' suffix, a second phase-6 cycle's doubled tag).
# Literal duplicate of the two strings in
# anti_hallucination.semantic_verifier._LOW_TRUST_TAGS — tied by an
# executable set-equality assertion in test_gh1354 (AC14); importing the
# plugin's private tuple here would make that drift test a tautology.
POST_PRODUCER_HEADER_TAGS = ("[UNCITED]", "[UNVERIFIED CITATION]")
VERIFY_TAG_RE = re.compile(r"\s*\[verify:[^\]]*\]\s*$", re.IGNORECASE)


def _strip_header_tags(title: str) -> str:
    """Iteratively strip trailing post-producer tags: ' [verify: ...]' and each
    of POST_PRODUCER_HEADER_TAGS, until the tail stops changing (a second
    phase-6 cycle can leave '... [UNCITED] [UNCITED]'). Bracket tails outside
    the set (e.g. '... [legacy]') are left untouched."""
    changed = True
    while changed:
        changed = False
        new_title = VERIFY_TAG_RE.sub("", title)
        if new_title != title:
            title = new_title
            changed = True
            continue
        for tag in POST_PRODUCER_HEADER_TAGS:
            suffix = " " + tag
            if title.endswith(suffix):
                title = title[: -len(suffix)]
                changed = True
                break
    return title


def _read_or_empty(path) -> str:
    """Read a text file; any OSError (including IsADirectoryError) or
    UnicodeDecodeError degrades to '' rather than raising (house idiom,
    cf. _resolve_review_content)."""
    try:
        return Path(path).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return ""


def _doc_section_body(doc: str, header: str) -> str:
    """Body of the section headed by the LAST line exactly equal to `header`,
    up to (not including) the next line starting with '## ', or EOF. Last
    match — the aggregator writes canonical sections AFTER raw role sections,
    so a role that quoted the header verbatim in its own body must not
    hijack the locator (AC11). No matching header ⇒ ''."""
    lines = doc.splitlines()
    start = None
    for i, line in enumerate(lines):
        if line == header:
            start = i
    if start is None:
        return ""
    body_lines: list[str] = []
    for line in lines[start + 1:]:
        if line.startswith("## "):
            break
        body_lines.append(line)
    return "\n".join(body_lines)


def _finding_keys(section_text: str) -> list[tuple[str, str]]:
    """(severity.upper(), normalized+tag-stripped title) per header line that
    parses under the canonical SEVERITY_HDR_LINE_RE. Tag-stripping is
    mandatory: without it the same finding gets different keys in the
    review doc (' [verify: ...]' from the aggregator) and the fix doc
    (' [UNCITED]' from verify_findings), and the consumer-guard would
    fail-closed a healthy build."""
    keys: list[tuple[str, str]] = []
    for line in section_text.splitlines():
        m = SEVERITY_HDR_LINE_RE.match(line.rstrip())
        if m:
            severity = m.group(1).upper()
            title = _normalize_finding_title(_strip_header_tags(m.group(2).strip()))
            keys.append((severity, title))
    return keys


def _parse_finding_blocks(section_text: str) -> list[dict]:
    """Inverse-parse a section into [{'block': <header line through the next
    header line, rstripped>}, ...] — the shape _render_fix_doc already
    consumes."""
    lines = section_text.splitlines()
    header_idxs = [i for i, line in enumerate(lines) if SEVERITY_HDR_LINE_RE.match(line.rstrip())]
    blocks: list[dict] = []
    for j, idx in enumerate(header_idxs):
        end = header_idxs[j + 1] if j + 1 < len(header_idxs) else len(lines)
        blocks.append({"block": "\n".join(lines[idx:end]).rstrip()})
    return blocks


# 21792EE7 fix-1 (Option C): capture everything after the lineno colon verbatim,
# then normalise the optional single separator-space during comparison.
# `\s?` (the old pattern) ate one leading space, causing indented Python lines
# like "    x = foo()" to be stored as "   x = foo()" → mismatch → false
# FABRICATED-CANDIDATE.  Capturing raw (no whitespace consumption) and stripping
# at most one leading space in the comparison preserves indentation detection
# while tolerating sub-agents that omit the separator space.
# 3F5599A6 D2: group(1) also accepts an optional single-letter drive prefix
# (e.g. "C:\repo\mod.py") so Windows-style absolute cites don't get truncated
# at the drive-letter colon before reaching the lineno colon.
_QUOTE_LINE_RE = re.compile(r"^>\s+((?:[A-Za-z]:)?[^:]+):(\d+):(.*)$", re.MULTILINE)

# A37D4F04: fuzzy/normalised citation match constants.
_QUOTE_WINDOW_LINES = 3   # ±3 lines around cited line for tiers 3/4/5
_REVIEWER_SUSPECT_RATE_THRESHOLD: float = 0.4  # D3492E45: > this triggers reviewer_grep_accuracy_warning
_MIN_SUBSTRING_LEN  = 16  # minimum quote length (after strip) for tier 5 substring match

# 3F5599A6 D3: reject oversized cited files before read — stat-only, no content
# read occurs, cache is left untouched.
_QUOTE_FILE_MAX_BYTES = 4 * 1024 * 1024

# 3F5599A6 D1: single source of truth (§1g) for the post-fix pytest report
# relpath — referenced by both _load_postfix_pytest_report and the
# _run_pytest_post_fix writer. This is the ONLY place the relpath literal
# is spelled out in this module.
_POSTFIX_PYTEST_REPORT_RELPATH = "reviews/post-fix-pytest.md"
_POSTFIX_REPORT_MAX_BYTES = 16 * 1024

# E2F171DB: BUILD SCOPE excludes evidence-preservation directories that get
# picked up by `git_diff_files(..., untracked=True)`. Test fixtures and
# incident scratchpads under the state dir are NOT in-scope for any build —
# they are preserved artifacts of past runs.
def _build_scope_exclude_prefixes() -> tuple[str, ...]:
    """GH444: module __getattr__ does NOT fire for bare-name lookups inside
    this module, so in-module consumers call this helper directly instead of
    the legacy _BUILD_SCOPE_EXCLUDE_PREFIXES constant name."""
    return (state_dir_prefix(),)


def __getattr__(name: str):
    """PEP-562 lazy module attribute (GH444): _BUILD_SCOPE_EXCLUDE_PREFIXES
    is a backward-compat name (locked by test_phase_6_build_scope_exclude_E2F171DB)
    for external/test attribute access only."""
    if name == "_BUILD_SCOPE_EXCLUDE_PREFIXES":
        return _build_scope_exclude_prefixes()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

# 65EA1B86: per-file byte cap for test-file inlining in the fix-worker prompt.
_FIX_TEST_INLINE_CAP_BYTES: int = 20000


def _inline_inscope_test_files(ctx, scratchpad) -> tuple[str, int, int]:
    """Return (block_text, file_count, total_bytes) for in-scope test files.

    Reads `scratchpad/integrity/pre-red-ref.txt` for the build-start SHA.
    If absent or empty → returns ("", 0, 0) (helper is INERT).

    Changed files are discovered via git_diff_files(sha, worktree_root,
    untracked=True), mirroring the BUILD-SCOPE precedent.  Only files that
    (a) do NOT match _BUILD_SCOPE_EXCLUDE_PREFIXES and (b) satisfy
    _is_test_py_path are inlined.  Files exceeding _FIX_TEST_INLINE_CAP_BYTES
    are truncated with a marker line.  Unreadable files (OSError) are skipped.

    Codifies 012F2C02 RCA-3.1 — fix-worker gets authoritative test source
    rather than re-deriving assertions from spec ACs.
    """
    ref_file = scratchpad / "integrity" / "pre-red-ref.txt"
    try:
        sha = ref_file.read_text(encoding="utf-8").strip()
    except OSError:
        return ("", 0, 0)
    if not sha:
        return ("", 0, 0)

    worktree_root = _resolve_worktree_root(ctx, scratchpad)
    changed = git_diff_files(sha, worktree_root, untracked=True)

    # Filter: drop excluded prefixes; keep only test-py paths.
    surviving = [
        p for p in changed
        if not any(p.startswith(_pre) for _pre in _build_scope_exclude_prefixes())
        and _is_test_py_path(p)
    ]
    if not surviving:
        return ("", 0, 0)

    file_blocks: list[str] = []
    total_bytes = 0
    for rel_path in surviving:
        full_path = worktree_root / rel_path
        try:
            content = full_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if len(content) > _FIX_TEST_INLINE_CAP_BYTES:
            content = (
                content[:_FIX_TEST_INLINE_CAP_BYTES]
                + f"\n[TRUNCATED — file exceeds 20000 bytes; Read the full file at {rel_path}]"
            )
        total_bytes += len(content.encode("utf-8", errors="replace"))
        file_blocks.append(f"### {rel_path}\n```python\n{content}\n```")

    if not file_blocks:
        return ("", 0, 0)

    instruction = (
        "When a finding requires you to re-derive or re-check a test assertion, treat the\n"
        "content below as ground truth. Do NOT re-derive assertions from the spec ACs alone\n"
        "(codifies 012F2C02 RCA-3.1)."
    )
    block_parts = [
        "## CURRENT TEST FILE CONTENT (HEAD — authoritative)",
        "",
        instruction,
        "",
    ] + file_blocks
    block_text = "\n".join(block_parts)
    return (block_text, len(file_blocks), total_bytes)


def _normalize_for_match(s: str) -> str:
    """Collapse runs of whitespace to single space, strip ends. Used by tiers 2/4."""
    return re.sub(r"\s+", " ", s).strip()


def _verify_finding_quote(
    finding_block: str,
    file_cache: dict[str, list[str] | None],
    base_dir: Path | None = None,
    fallback_dir: Path | None = None,
) -> tuple[str, str]:
    """Parse the first `> path:line: <quoted>` line from a finding block.

    1F39FB1A: Returns (verify_status: str, reason: str) where verify_status is one of
    6 enum codes. The old (bool, str) return is replaced — callers must unpack as
    (verify_status, reason). reason retains the granular legacy code for forensic queries.

    verify_status codes:
      - "verified-exact"          — quote matches file at the cited line (exact or normalized)
      - "verified-windowed"       — quote matches a line within ±_QUOTE_WINDOW_LINES
      - "verified-substring"      — quoted.strip() is substring of a window line
      - "suspect-no-quote"        — no `> path:line:` line found in finding block
      - "suspect-file-not-found"  — cited path does not exist, unreadable, too large,
                                    escapes the containment root, or line out of range;
                                    also used for relative paths that can't be verified
      - "suspect-no-match"        — quote present but content mismatch in all 5 tiers

    reason codes (granular, retained for telemetry forensics):
      "OK", "OK-NORMALIZED", "OK-WINDOWED", "OK-WINDOWED-NORMALIZED", "OK-SUBSTRING",
      "OK-UNVERIFIABLE-RELATIVE", "MISSING-QUOTE", "FILE-NOT-FOUND",
      "LINE-OUT-OF-RANGE", "FABRICATED-CANDIDATE", "FILE-TOO-LARGE", "PATH-ESCAPES-ROOT"

    NOTE: FEB64BA8 outer-backtick-strip block is REMOVED (design item 2). No backtick
    normalization occurs in this function. Soft-tag semantics handle producer-format drift
    at the rendering layer; Opus satisfaction uses LLM judgment + Read tool for re-verify.
    """
    m = _QUOTE_LINE_RE.search(finding_block)
    if not m:
        return "suspect-no-quote", "MISSING-QUOTE"

    raw_path, lineno_str, quoted_raw = m.group(1).strip(), m.group(2), m.group(3)
    lineno = int(lineno_str)  # 1-based

    # Option C normalisation (21792EE7 fix-1): strip exactly one leading space
    # from the captured group (the optional separator space from "> path:N: text"
    # format).  This is explicit rather than consuming it in the regex, so
    # indentation beyond the separator space is preserved for the comparison.
    quoted = quoted_raw[1:] if quoted_raw.startswith(" ") else quoted_raw

    # 1F39FB1A: FEB64BA8 outer-backtick-strip block REMOVED. No backtick
    # normalization here. Soft-tag semantics (suspect-no-match) replace drops.

    # Resolve path:
    # 1. If absolute — use as-is; if it doesn't exist → suspect-file-not-found.
    # 2. If relative — try to resolve under cwd; if not found → suspect-file-not-found
    #    (1F39FB1A: honest "can't verify" instead of old "benefit of the doubt" KEEP).
    path = Path(raw_path)
    if not path.is_absolute():
        resolved = (base_dir or Path.cwd()) / raw_path
        root_dir = base_dir or Path.cwd()
        if not resolved.exists():
            # 3F5599A6 A2: base_dir/cwd missed — consult fallback_dir (scratchpad)
            # for pipeline-artifact cites that live outside the worktree. A
            # base_dir hit (above) is always checked FIRST and wins; fallback_dir
            # is consulted ONLY on base miss (§2 D1 precedence).
            if fallback_dir is not None:
                fallback_candidate = fallback_dir / raw_path
                if fallback_candidate.exists():
                    resolved = fallback_candidate
                    root_dir = fallback_dir
                else:
                    # 1F39FB1A: was (True, "OK-UNVERIFIABLE-RELATIVE") — now suspect (honest).
                    # reason code preserved for forensic queries.
                    return "suspect-file-not-found", "OK-UNVERIFIABLE-RELATIVE"
            else:
                # 1F39FB1A: was (True, "OK-UNVERIFIABLE-RELATIVE") — now suspect (honest).
                # reason code preserved for forensic queries.
                return "suspect-file-not-found", "OK-UNVERIFIABLE-RELATIVE"
        # 3F5599A6 D4/A2: relative-path containment — a relative cite that resolves
        # (following symlinks) outside root_dir (base_dir/cwd, or fallback_dir when
        # the fallback candidate above was adopted) is a path-escape, not a
        # legitimate quote. Order preserved: runs AFTER the exists() check above
        # (AC5 lock — nonexistent relative stays OK-UNVERIFIABLE-RELATIVE), BEFORE
        # `path = resolved`. §2.3 DEFER: resolve()/is_relative_to OSError falls
        # through to the existing FILE-NOT-FOUND handler.
        try:
            root = root_dir.resolve()
            if not resolved.resolve().is_relative_to(root):
                return "suspect-file-not-found", "PATH-ESCAPES-ROOT"
        except OSError:
            return "suspect-file-not-found", "FILE-NOT-FOUND"
        path = resolved

    # 21792EE7 fix-2: is_file() guard prevents hanging on device files
    # (e.g. /dev/zero) that pass exists() but block on read_text().
    if not path.is_file():
        return "suspect-file-not-found", "FILE-NOT-FOUND"

    # 3F5599A6 D3: size cap — stat every call (even for already-cached keys),
    # reject oversized files before any read occurs (cache left untouched).
    # §2.3 DEFER: a stat() OSError falls through to the existing FILE-NOT-FOUND
    # handler below.
    try:
        if path.stat().st_size > _QUOTE_FILE_MAX_BYTES:
            return "suspect-file-not-found", "FILE-TOO-LARGE"
    except OSError:
        return "suspect-file-not-found", "FILE-NOT-FOUND"

    # File-cache lookup to avoid repeated reads.
    cache_key = str(path)
    if cache_key not in file_cache:
        try:
            file_cache[cache_key] = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            file_cache[cache_key] = None

    lines = file_cache[cache_key]
    if lines is None:
        return "suspect-file-not-found", "FILE-NOT-FOUND"

    if lineno < 1 or lineno > len(lines):
        return "suspect-file-not-found", "LINE-OUT-OF-RANGE"

    # A37D4F04: tiered match cascade — first hit wins.
    # Tier 1: exact rstrip equality at the cited line.
    if lines[lineno - 1].rstrip() == quoted.rstrip("\n\r"):
        return "verified-exact", "OK"

    # Tier 2: normalised equality at the cited line.
    if _normalize_for_match(lines[lineno - 1]) == _normalize_for_match(quoted):
        return "verified-exact", "OK-NORMALIZED"

    # Tiers 3/4: window scan (excluding the cited line itself).
    window_start = max(1, lineno - _QUOTE_WINDOW_LINES)
    window_end = min(len(lines), lineno + _QUOTE_WINDOW_LINES)
    for i in range(window_start, window_end + 1):
        if i == lineno:
            continue
        if lines[i - 1].rstrip() == quoted.rstrip("\n\r"):
            return "verified-windowed", "OK-WINDOWED"

    for i in range(window_start, window_end + 1):
        if i == lineno:
            continue
        if _normalize_for_match(lines[i - 1]) == _normalize_for_match(quoted):
            return "verified-windowed", "OK-WINDOWED-NORMALIZED"

    # Tier 5: substring match in the window (including the cited line), length-guarded.
    if len(quoted.strip()) >= _MIN_SUBSTRING_LEN:
        for i in range(window_start, window_end + 1):
            if quoted.strip() in lines[i - 1]:
                return "verified-substring", "OK-SUBSTRING"

    return "suspect-no-match", "FABRICATED-CANDIDATE"


def _aggregate_review_findings(ctx, prev) -> StepResult:
    """Deterministic aggregation of per-role review files.

    Reads ``<scratchpad>/reviews/role-*.md``, parses ``### SEVERITY:`` headers,
    deduplicates by (severity, normalized title), composes the canonical
    ``# Composite Review`` document, and computes the verdict from severity
    counts. Result data carries ``aggregated_content`` for the next step.

    Error semantics:
      - No role files → ``E_NO_ROLE_FILES``, recoverable=False, but
        ``skip_on_error=True`` on the StepContract means the workflow
        continues with prev.data forwarded so write_review_artifact can
        fall back to the legacy stdout/disk path.
    """
    forwarded = dict(prev.data) if isinstance(getattr(prev, "data", None), dict) else {}

    try:
        scratchpad = _resolve_scratchpad(ctx)
    except ValueError as exc:
        return StepResult(
            status="error", data=forwarded, duration_ms=0,
            step_name="aggregate_review_findings",
            error=str(exc),
            error_code="E_MISSING_SCRATCHPAD",
            recoverable=False,
        )

    reviews_dir = scratchpad / "reviews"
    role_files = sorted(reviews_dir.glob("role-*.md")) if reviews_dir.is_dir() else []

    if not role_files:
        return StepResult(
            status="error",
            data={**forwarded, "aggregated_content": None},
            duration_ms=0,
            step_name="aggregate_review_findings",
            error="no per-role review files found",
            error_code="E_NO_ROLE_FILES",
            recoverable=False,
        )

    # E8433B4E: min-floor enforcement + fanout banner.
    # Read expected reviewer count from prev.data["complexity"] (threaded by
    # _invoke_review_llm). Backward-compat: if complexity absent, skip floor
    # check entirely (callers may not yet thread it).
    complexity = forwarded.get("complexity")
    expected_reviewers: int | None = None
    expected_slugs: list[str] = []
    min_floor: int | None = None
    if complexity:
        try:
            _agg_artifact_type = (ctx.org_config or {}).get("artifact_type") if ctx else None
            dispatch_table, expected_reviewers = _select_reviewers(complexity, artifact_type=_agg_artifact_type)
            expected_slugs = _extract_expected_slugs(dispatch_table)
            min_floor = max(2, (expected_reviewers + 1) // 2)  # ceil(N/2), N>=1
        except Exception:
            # Defensive: bad complexity value → skip floor check, log and continue.
            logger.warning("E8433B4E: failed to compute floor for complexity=%r", complexity, exc_info=True)
            expected_reviewers = None
            min_floor = None

    observed_count = len(role_files)
    if min_floor is not None and 0 < observed_count < min_floor:
        return StepResult(
            status="error",
            data={
                **forwarded,
                "aggregated_content": None,
                "observed_role_count": observed_count,
                "expected_reviewers": expected_reviewers,
                "min_floor": min_floor,
            },
            duration_ms=0,
            step_name="aggregate_review_findings",
            error=(
                f"insufficient fanout: {observed_count} role file(s) below "
                f"min_floor={min_floor} (expected_reviewers={expected_reviewers})"
            ),
            error_code="E_INSUFFICIENT_FANOUT",
            recoverable=False,
        )

    # Per-role section bodies + flat findings list with role attribution.
    role_sections: list[tuple[str, str]] = []   # (slug, raw_content)
    all_findings: list[dict] = []                # entries: {severity, title, block, role}
    # 906E37DC: audit accumulators (computed while content is already in scope — no re-read).
    _total_parsed_blocks: int = 0
    _self_reported_total: int = 0
    _any_selfcount: bool = False
    # GH970 D2: deterministic malformed-SEVERITY-header lint accumulators.
    _malformed_total: int = 0
    _malformed_roles: set[str] = set()
    for rf in role_files:
        slug = _slug_from_role_filename(rf)
        try:
            content = rf.read_text(encoding="utf-8")
        except OSError:
            content = "(failed to read role file)"
        role_sections.append((slug, content.rstrip()))
        for f in _parse_role_findings(content):
            all_findings.append({**f, "role": slug})
        # 906E37DC: per-file audit counts (same content the parser saw).
        _parsed_blocks_this_role = len(list(_AGG_SEVERITY_HDR_RE.finditer(content)))
        _total_parsed_blocks += _parsed_blocks_this_role
        _m = list(_ROLE_SELFCOUNT_RE.finditer(content))
        _selfcount_this_role = int(_m[-1].group(1)) if _m else None
        if _selfcount_this_role is not None:
            _self_reported_total += _selfcount_this_role
            _any_selfcount = True
        # GH970 D2: lines that look like a SEVERITY header but don't parse.
        _malformed = lint_role_report(content)
        if _malformed:
            _emit_safe("role_report_malformed", {
                "phase": "phase_6_review",
                "role": slug,
                "count": len(_malformed),
                "lines": _malformed[:5],
            })
            _malformed_total += len(_malformed)
            _malformed_roles.add(slug)

    # 1F39FB1A: Soft-tag pivot — annotate ALL findings with verify_status + verify_reason.
    # No findings are dropped. verified_findings = status starts with "verified";
    # suspect_findings (rendered separately) = status starts with "suspect".
    # A37D4F04: accumulate match_kinds by verify_status (new enum codes, per M2 advisory).
    file_cache: dict[str, list[str] | None] = {}
    filtered_findings: list[dict] = []  # 1F39FB1A: always empty (no drops); retained for audit compat
    match_kinds: dict[str, int] = {}
    target_root = _resolve_worktree_root(ctx, scratchpad)
    for f in all_findings:
        # 3F5599A6 A2: fallback_dir=scratchpad — pipeline artifacts (reviews/*.md,
        # spec.md, role files) live in the scratchpad, not the worktree; consulted
        # only when the worktree-root resolution misses (§2 D1 precedence).
        verify_status, reason = _verify_finding_quote(
            f["block"], file_cache, base_dir=target_root, fallback_dir=scratchpad
        )
        f["verify_status"] = verify_status
        f["verify_reason"] = reason
        match_kinds[verify_status] = match_kinds.get(verify_status, 0) + 1
    # all_findings NOT replaced — every finding kept.

    # 1F39FB1A: emit composite_finding_quote_verified for ALL findings (replaces
    # composite_finding_dropped which only fired for drops). One event per finding,
    # verify_status field enables suspect-rate computation from event log.
    for _f in all_findings:
        _emit_safe("composite_finding_quote_verified", {
            "phase": "phase_6_review",
            "role": _f.get("role"),
            "severity": _f.get("severity"),
            "title": _f.get("title"),
            "verify_status": _f.get("verify_status"),
            "verify_reason": _f.get("verify_reason"),
            "quote_line_present": bool(_QUOTE_LINE_RE.search(_f.get("block", ""))),
        })

    # D3492E45: aggregate suspect-rate canary. Emit reviewer_suspect_rate once per
    # call (when total > 0); if rate > threshold, also emit reviewer_grep_accuracy_warning
    # with per-finding identifiers for the suspect set. Reviewer-grep accuracy is the
    # canary that distinguishes "model fabricated citations" from "code drifted under tests".
    verified_count = sum(v for k, v in match_kinds.items() if k.startswith("verified"))
    suspect_count = sum(v for k, v in match_kinds.items() if k.startswith("suspect"))
    total_verified_suspect = verified_count + suspect_count
    if total_verified_suspect > 0:
        rate = suspect_count / total_verified_suspect
        threshold_exceeded = rate > _REVIEWER_SUSPECT_RATE_THRESHOLD
        _emit_safe("reviewer_suspect_rate", {
            "phase": "phase_6_review",
            "verified_count": verified_count,
            "suspect_count": suspect_count,
            "total": total_verified_suspect,
            "rate": rate,
            "threshold": _REVIEWER_SUSPECT_RATE_THRESHOLD,
            "threshold_exceeded": threshold_exceeded,
        })
        if threshold_exceeded:
            _emit_safe("reviewer_grep_accuracy_warning", {
                "phase": "phase_6_review",
                "rate": rate,
                "threshold": _REVIEWER_SUSPECT_RATE_THRESHOLD,
                "suspect_count": suspect_count,
                "finding_ids": [
                    {
                        "role": _f.get("role"),
                        "severity": _f.get("severity"),
                        "title": _f.get("title"),
                        "verify_status": _f.get("verify_status"),
                    }
                    for _f in all_findings
                    if str(_f.get("verify_status", "")).startswith("suspect")
                ],
            })

    # Dedup by (severity, normalized_title); first-seen wins. Track cross-role
    # overlaps for annotation.
    seen: dict[tuple[str, str], dict] = {}
    overlaps: dict[tuple[str, str], list[str]] = {}
    for f in all_findings:
        key = (f["severity"], _normalize_finding_title(f["title"]))
        if key in seen:
            overlaps.setdefault(key, []).append(f["role"])
        else:
            seen[key] = f

    # Sort dedup'd findings by severity, then original order (insertion order
    # of dict preserves first-seen which already encodes role-file order).
    dedup_sorted = sorted(
        seen.values(),
        key=lambda f: _SEVERITY_ORDER.get(f["severity"], 99),
    )

    # 1F39FB1A: Partition dedup'd findings into verified vs suspect for rendering.
    # Severity counts and verdict come from verified_findings only (spec Q1 frozen NO).
    verified_findings = [f for f in dedup_sorted if f.get("verify_status", "").startswith("verified")]
    suspect_findings = [f for f in dedup_sorted if f.get("verify_status", "").startswith("suspect")]

    # Severity counts (post-dedup, verified only).
    counts = {"CRITICAL": 0, "HIGH": 0, "MEDIUM": 0, "LOW": 0}
    for f in verified_findings:
        counts[f["severity"]] = counts.get(f["severity"], 0) + 1

    findings_count = len(verified_findings)
    filtered_count = len(suspect_findings)  # 1F39FB1A: "filtered" = suspect count (same audit role)
    if counts["CRITICAL"] > 0 or counts["HIGH"] > 0:
        verdict = VERDICT_FAIL
    elif counts["MEDIUM"] > 0 or counts["LOW"] > 0:
        verdict = VERDICT_PARTIAL
    elif findings_count == 0 and filtered_count > 0:
        # 1F39FB1A: all dedup'd findings are suspect (none verified). SUSPECT verdict
        # distinguishes this from PASS (genuinely no findings). Preserves 21792EE7 fix-3
        # semantics — soft-tag doesn't collapse the SUSPECT verdict path.
        verdict = VERDICT_SUSPECT
    else:
        verdict = VERDICT_PASS

    # Compose final content.
    out: list[str] = ["# Composite Review", ""]
    if expected_reviewers is not None:
        observed_slugs_set = {_slug_from_role_filename(p) for p in role_files}
        missing_slugs = [s for s in expected_slugs if s not in observed_slugs_set]
        out.append("## Fanout")
        out.append(f"expected: {expected_reviewers}")
        out.append(f"observed: {observed_count}")
        if missing_slugs:
            out.append(f"missing: {', '.join(missing_slugs)}")
        else:
            out.append("missing: (none)")
        out.append("")
    for slug, body in role_sections:
        out.append(f"## {slug}")
        out.append(body)
        out.append("")

    out.append("## Aggregated Findings")
    out.append("")
    if findings_count == 0:
        out.append("(no findings)")
        out.append("")
    else:
        for f in verified_findings:
            out.append(f["block"])
            key = (f["severity"], _normalize_finding_title(f["title"]))
            if key in overlaps:
                others = ", ".join(sorted(set(overlaps[key])))
                out.append(f"(also flagged by: {others})")
            out.append("")

    # 1F39FB1A: ## Suspect Findings section replaces old ## Filtered Findings.
    # Suspect findings are VISIBLE to Opus satisfaction (LLM judgment + Read tool
    # for re-verify) but NOT consumed by the fix worker. Each header gets
    # [verify: <verify_status>] tag per spec design item (5).
    if suspect_findings:
        out.append(SUSPECT_FINDINGS_SECTION_HEADER)
        out.append("")
        for f in suspect_findings:
            vs = f.get("verify_status", "suspect-no-match")
            # Inject [verify: <status>] tag into the ### SEVERITY: header line.
            # Only the first line of the block (the header) is modified.
            block_lines = f["block"].splitlines(keepends=True)
            if block_lines and SEVERITY_HDR_LINE_RE.match(block_lines[0].rstrip("\n\r")):
                block_lines[0] = block_lines[0].rstrip("\n\r") + f" [verify: {vs}]\n"
            tagged_block = "".join(block_lines)
            out.append(tagged_block)
            key = (f["severity"], _normalize_finding_title(f["title"]))
            if key in overlaps:
                others = ", ".join(sorted(set(overlaps[key])))
                out.append(f"(also flagged by: {others})")
            out.append("")

    out.append("## Summary")
    out.append(
        f"Findings total: {findings_count} "
        f"(CRITICAL: {counts['CRITICAL']} / HIGH: {counts['HIGH']} / MEDIUM: {counts['MEDIUM']})"
    )
    out.append("")
    out.append(f"VERDICT: {verdict}")
    out.append("")

    # 906E37DC: findings audit reconciliation section.
    _lost_to_prose: int = (
        (_self_reported_total - _total_parsed_blocks)
        if (_any_selfcount and _self_reported_total > _total_parsed_blocks)
        else 0
    )
    findings_audit = {
        "self_reported": _self_reported_total if _any_selfcount else None,
        "parsed_blocks": _total_parsed_blocks,
        "aggregated": findings_count,
        "filtered": filtered_count,
        "role_files": len(role_files),
        "consistent": (
            (_self_reported_total == _total_parsed_blocks) and (filtered_count == 0)
        ) if _any_selfcount else None,
        "lost_to_prose": _lost_to_prose,
        "match_kinds": match_kinds,  # A37D4F04: KEEP-reason counts for fuzzy citation audit
        "high_filter_rate": (
            (filtered_count / (findings_count + filtered_count)) > 0.5
            if (findings_count + filtered_count) > 0 else False
        ),
        "malformed_headers": _malformed_total,  # GH970 D2
    }
    out.append("## Findings Audit")
    out.append(
        f"self-reported (sub-reviewer counts): "
        f"{findings_audit['self_reported'] if findings_audit['self_reported'] is not None else 'n/a'}"
    )
    out.append(f"parsed as ### SEVERITY: blocks: {findings_audit['parsed_blocks']}")
    out.append(f"aggregated (post-dedup, post-quote-verify): {findings_audit['aggregated']}")
    out.append(f"quote-filtered (21792EE7): {findings_audit['filtered']}")
    if _lost_to_prose > 0:
        out.append(
            f"⚠ AUDIT WARNING: {_lost_to_prose} finding(s) self-reported "
            "but not emitted as ### SEVERITY: blocks — likely written in prose. "
            "Reviewer prompt requires all findings inline. Audit trail incomplete for this build."
        )
    if _malformed_total > 0:
        out.append(
            f"⚠ AUDIT WARNING: {_malformed_total} malformed SEVERITY header line(s) "
            "not parseable by the aggregator — findings may be invisible. "
            f"Roles: {', '.join(sorted(_malformed_roles))}"
        )
    out.append("")
    _emit_safe("review_findings_audit", findings_audit)

    aggregated_content = "\n".join(out)

    return StepResult(
        status="ok",
        data={
            **forwarded,
            "aggregated_content": aggregated_content,
            "verdict": verdict,
            "role_files": [str(p) for p in role_files],
            "findings_count": findings_count,
            "filtered_count": filtered_count,  # 21792EE7 fix-3: expose for SUSPECT detection
            "severity_counts": counts,
            "observed_role_count": observed_count,
            "expected_reviewers": expected_reviewers,
            "min_floor": min_floor,
            "findings_audit": findings_audit,  # 906E37DC
            "verified_findings": verified_findings,  # 65695203: forwarded to step 4 for fix-doc render
            "suspect_findings": suspect_findings,  # CA50885D: forwarded for fail-OPEN fix-feed
        },
        duration_ms=0,
        step_name="aggregate_review_findings",
    )


# ─── Step 4: write review artifact ───────────────────────────────────────────


def _render_fix_doc(verified_findings: list, verdict: str = "", suspect_findings: list | None = None) -> str:
    """Render the fix doc body (§1aa named helper, 65695203, CA50885D).

    Fail-OPEN contract (CA50885D): the verified '## Aggregated Findings' section
    is always rendered first. When suspect_findings is non-empty, a LOW-CONFIDENCE
    suspect section is appended so the fix worker can Read-verify before applying.
    When suspect_findings is None or empty, the output is byte-identical to the
    pre-CA50885D verified-only output (back-compat with 65695203 callers).

    Mirrors the aggregated-section rendering logic from _aggregate_review_findings
    (L1643-1655) so the format is consistent.
    """
    out: list[str] = ["## Aggregated Findings", ""]
    if not verified_findings:
        out.append("(no findings)")
        out.append("")
    else:
        for f in verified_findings:
            out.append(f["block"])
            out.append("")
    if suspect_findings:
        out.append("## Suspect Findings (LOW CONFIDENCE)")
        out.append("")
        out.append(
            "These findings did not pass quote-verification. "
            "Read the cited file yourself and fix ONLY if the issue is real."
        )
        out.append("")
        for f in suspect_findings:
            out.append(f["block"])
            out.append("")
    return "\n".join(out)


def _is_review_conformant(raw: str) -> bool:
    """Return True iff raw contains a line that, after rstrip('\\n\\r'), exactly equals
    '## Aggregated Findings'.  Mirrors helper.py:151 per-line rstrip semantics.
    NO regex, NO lenient matching — exact string comparison only."""
    for line in raw.splitlines(keepends=True):
        if line.rstrip("\n\r") == "## Aggregated Findings":
            return True
    return False


def _normalize_to_aggregated_findings(raw: str) -> str:
    """Deterministically wrap raw in a conformant '## Aggregated Findings' block.

    Idempotent: if raw is already conformant (passes _is_review_conformant),
    return it unchanged.  Otherwise prepend the header and preserve the body
    verbatim.  Empty/whitespace-only body is replaced with '(no findings)'.
    Postcondition: _is_review_conformant(result) is True for all inputs.
    """
    if _is_review_conformant(raw):
        return raw
    body = raw.strip()
    return "## Aggregated Findings\n\n" + (body or "(no findings)") + "\n"


def _resolve_review_content(raw: str, doc_path: Path) -> str:
    """Return the authoritative review content.

    A134BBD0: real LLMs use the Write tool — stdout is a short summary, the
    canonical review is at doc_path.  Test stubs emit stdout-only so doc_path
    may not exist yet; fall back to stdout in that case.
    """
    if doc_path.is_file():
        try:
            on_disk = doc_path.read_text(encoding="utf-8")
        except OSError:
            return raw
        if on_disk.strip():
            return on_disk
    return raw


def _persist_fix_feed(
    doc_path: Path,
    review_content: str,
    verified: list,
    suspect: list,
    verdict: str,
) -> "Path | StepResult":
    """GH1354 producer chokepoint: the ONLY place that builds the fix-doc
    name, renders its content, and writes it. Path on success,
    StepResult(status='error') on failure."""
    fix_path = doc_path.parent / Path(REVIEW_FIX_DOC_RELPATH).name
    if not verified and not suspect:
        # No structured lists (stdout-fallback) ⇒ derive from the SAME bytes
        # persisted to the review document (and read by satisfaction), not a
        # second independent parse.
        verified = _parse_finding_blocks(_doc_section_body(review_content, "## Aggregated Findings"))
        suspect = _parse_finding_blocks(_doc_section_body(review_content, SUSPECT_FINDINGS_SECTION_HEADER))
        # This derivation path is already observable via the existing
        # 'stdout_fallback_used' event emitted on the same branch upstream —
        # no new event is introduced here (GH402 orphan-emit gate).
    try:
        fix_path.write_text(_render_fix_doc(verified, verdict, suspect), encoding="utf-8")
    except OSError as exc:
        return StepResult(
            status="error", data=None, duration_ms=0,
            step_name="write_review_artifact",
            error=str(exc),
            error_code="E_REVIEW_WRITE_FAILED",
        )
    return fix_path


def _write_review_artifact(ctx, prev) -> StepResult:
    if not isinstance(prev, StepResult) or not isinstance(prev.data, dict):
        return StepResult(
            status="error", data=None, duration_ms=0,
            step_name="write_review_artifact",
            error="prev step did not produce raw_response",
            error_code="E_MISSING_PREV_DATA",
        )

    # 319C2DCF Option A: prefer aggregated_content from aggregate_review_findings
    # over outer-LLM stdout. The aggregator is the canonical source when
    # per-role files exist; outer LLM stdout is now just a stub. When the
    # aggregator yielded no aggregated_content (legacy / no role files), fall
    # through to the existing stdout/disk resolution path.
    aggregated_content = prev.data.get("aggregated_content")
    if aggregated_content:
        doc_path = Path(prev.data["doc_path"])
        doc_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            doc_path.write_text(aggregated_content, encoding="utf-8")
        except OSError as exc:
            return StepResult(
                status="error", data=None, duration_ms=0,
                step_name="write_review_artifact",
                error=str(exc),
                error_code="E_REVIEW_WRITE_FAILED",
            )
        # Verdict: prefer aggregator's, parse as fallback safety check.
        verdict = prev.data.get("verdict") or _parse_review_verdict(aggregated_content)
        # CA50885D: fail-OPEN fix doc — verified first, then suspect (LOW CONFIDENCE) if any.
        verified_findings = prev.data.get("verified_findings") or []
        suspect_findings = prev.data.get("suspect_findings") or []
        fix_result = _persist_fix_feed(doc_path, aggregated_content, verified_findings, suspect_findings, verdict)
        if isinstance(fix_result, StepResult):
            return fix_result
        fix_doc_path_obj = fix_result
        _emit_safe("review_writer_return_source", {
            "phase": "phase_6_review",
            "source": "aggregated_content",
            "bytes_written": len(aggregated_content.encode("utf-8")),
        })
        return StepResult(
            status="ok",
            data={
                "review_doc_path": str(doc_path),
                "review_fix_doc_path": str(fix_doc_path_obj),
                "spec_path": prev.data["spec_path"],
                "red_log_path": prev.data["red_log_path"],
                "green_log_path": prev.data["green_log_path"],
                "review_bytes_written": len(aggregated_content.encode("utf-8")),
                "verdict": verdict,
            },
            duration_ms=0,
            step_name="write_review_artifact",
        )

    # 23EC338E: surface A134BBD0 stdout fallback in telemetry. Fires when the
    # aggregator yielded no aggregated_content and we fell through to the
    # outer-LLM-stdout path. Visible in event log so prod incidents are tracked.
    _emit_safe("stdout_fallback_used", {
        "phase": 6,
        "doc_path": str(prev.data.get("doc_path", "")),
        "had_raw_response": "raw_response" in prev.data,
    })

    if "raw_response" not in prev.data:
        return StepResult(
            status="error", data=None, duration_ms=0,
            step_name="write_review_artifact",
            error="prev step did not produce raw_response",
            error_code="E_MISSING_PREV_DATA",
        )
    raw = prev.data["raw_response"]
    doc_path = Path(prev.data["doc_path"])
    doc_path.parent.mkdir(parents=True, exist_ok=True)

    # ── Format-conformance check (sub-finding 1) ──────────────────────────────
    # Enforce unconditionally: any reviewer response missing '## Aggregated
    # Findings' is non-conformant and is recovered DETERMINISTICALLY, whatever
    # backend produced it (GH1399).
    # A134BBD0: check disk-first — real LLMs write the review via Write tool;
    # stdout is only a summary.  Test stubs that don't write disk fall back to
    # stdout via _resolve_review_content.
    content = _resolve_review_content(raw, doc_path)
    # Opus advisory 2 (CF838E6F): capture source discriminator BEFORE any persist,
    # because after doc_path.write_text(content) the disk==content check is True for
    # both paths and would mislabel stdout as doc_file.
    _src = (
        "doc_file"
        if (doc_path.is_file() and doc_path.read_text(encoding="utf-8") == content and content != raw)
        else "stdout"
    )
    if not _is_review_conformant(content):
        # GH1399: the rescue is selected by the RESPONSE's own property — its
        # non-conformance — and never by the identity of the backend that
        # produced it. `_normalize_to_aggregated_findings` is total (its
        # postcondition is `_is_review_conformant(result)` for ALL inputs, body
        # preserved verbatim), so no paid retry can add anything: the retry
        # branch, its E_REVIEW_FORMAT_DRIFT outcomes and the retry-model choice
        # are removed with it (§1c-ОТМЕНА). Backend is reported, not consulted.
        content = _normalize_to_aggregated_findings(content)
        _emit_safe("review_stdout_normalized_deterministic",
                   {"phase": 6, "backend": _resolve_backend(None, os.environ)[0],
                    "bytes": len(content.encode("utf-8"))})
    # ─────────────────────────────────────────────────────────────────────────

    # Persist canonical content; skip rewrite if disk already holds it.
    if not (doc_path.is_file() and doc_path.read_text(encoding="utf-8") == content):
        try:
            doc_path.write_text(content, encoding="utf-8")
        except OSError as exc:
            return StepResult(
                status="error", data=None, duration_ms=0,
                step_name="write_review_artifact",
                error=str(exc),
                error_code="E_REVIEW_WRITE_FAILED",
            )

    verdict = _derive_fallback_verdict(content)
    _emit_safe("review_stdout_fallback_verdict", {
        "phase": 6,
        "verdict": verdict,
        "had_structured_block": extract_structured_findings(content) is not None,
    })
    # CA50885D: fail-OPEN fix doc on stdout-fallback path too — suspect forwarded.
    # GH1354: derived from the SAME `content` just persisted to doc_path, and
    # OSError here now fails closed like the aggregated branch (no more
    # swallowed 'pass' — a broken fix-doc write is the exact failure mode
    # the loop in the issue was invisible against).
    _fallback_verified = prev.data.get("verified_findings") or []
    _fallback_suspect = prev.data.get("suspect_findings") or []
    _fix_result_fb = _persist_fix_feed(doc_path, content, _fallback_verified, _fallback_suspect, verdict)
    if isinstance(_fix_result_fb, StepResult):
        return _fix_result_fb
    _fix_doc_path_fb = _fix_result_fb
    _emit_safe("review_writer_return_source", {
        "phase": "phase_6_review",
        "source": _src,
        "bytes_written": len(content.encode("utf-8")),
    })
    return StepResult(
        status="ok",
        data={
            "review_doc_path": str(doc_path),
            "review_fix_doc_path": str(_fix_doc_path_fb),
            "spec_path": prev.data["spec_path"],
            "red_log_path": prev.data["red_log_path"],
            "green_log_path": prev.data["green_log_path"],
            "review_bytes_written": len(content.encode("utf-8")),
            "verdict": verdict,
        },
        duration_ms=0,
        step_name="write_review_artifact",
    )


# ─── Step 4: verify findings (pure-Python citation post-processor) ────────────


def _verify_findings(ctx, prev) -> StepResult:
    """Wrap helper.verify_findings impl + emit phase_6_unverified_count event.

    Source-of-truth for agreement 5F9817F6 detector: every cycle's UNVERIFIED
    count is logged so the post-satisfaction detector can read events.jsonl
    and re-emit ``review_aggregator_mass_unverified`` when threshold met.

    Always emit the count event (even at zero) so downstream tooling can
    confirm the verifier ran for a given run_id.
    """
    result = _verify_findings_impl(ctx, prev)
    if isinstance(result.data, dict):
        count = int(result.data.get("verify_unverified", 0) or 0)
        verified = int(result.data.get("verify_verified", 0) or 0)
        uncited = int(result.data.get("verify_uncited", 0) or 0)
        _emit_safe(
            "phase_6_unverified_count",
            {"count": count, "total": count + verified + uncited},
        )
    return result


def _verify_findings_semantic(ctx, prev) -> StepResult:
    return _verify_findings_semantic_impl(ctx, prev)


# ─── Step 5: build fix prompt ────────────────────────────────────────────────


def _build_fix_prompt(ctx, prev) -> StepResult:
    if not isinstance(prev, StepResult) or not isinstance(prev.data, dict):
        return StepResult(
            status="error", data=None, duration_ms=0,
            step_name="build_fix_prompt",
            error="prev step did not pass through paths",
            error_code="E_MISSING_PREV_DATA",
        )
    scratchpad = _resolve_scratchpad(ctx)
    spec_path = Path(prev.data["spec_path"])
    review_doc = Path(prev.data["review_doc_path"])
    # 65695203: fix-worker receives the verified-only doc (never sees suspect findings).
    # review_fix_doc_path is a DISTINCT local from fix_doc_path (the fix-worker OUTPUT log).
    review_fix_doc_path = Path(
        prev.data.get("review_fix_doc_path") or prev.data["review_doc_path"]
    )

    # GH1354 consumer-guard: the fix feed on disk must cover the review's
    # '## Aggregated Findings' section on disk before the fix-worker gets a
    # prompt. Catches a stale fix doc left from a previous cycle (real input,
    # AC7) fail-closed, before the fix-LLM is invoked.
    required = set(_finding_keys(_doc_section_body(_read_or_empty(review_doc), "## Aggregated Findings")))
    present = set(_finding_keys(_read_or_empty(review_fix_doc_path)))
    if not required <= present:
        missing = required - present
        return StepResult(
            status="error", data=None, duration_ms=0,
            step_name="build_fix_prompt",
            error=(
                "fix feed does not cover the review's aggregated findings: missing "
                + ", ".join(f"{s}:{t}" for s, t in sorted(missing))
            ),
            error_code="E_REVIEW_FIX_FEED_DIVERGENCE",
            recoverable=False,
        )

    fix_doc_path = scratchpad / FIX_DOC_RELPATH
    verdict = prev.data["verdict"]

    parts: list[str] = []
    role = _maybe_role_template(ctx)
    if role:
        parts.append(role.rstrip())
        parts.append("")
    parts.append(_read_first_block(scratchpad))
    parts.append("")
    parts.append(
        "ROLE: You are a fix worker. Your output IS the code edits (via Edit/Write) "
        "plus a final marker line — not a narrative report. Read the review "
        "findings and fix EVERY one. Use Edit/Write directly. Open files "
        "yourself — do NOT trust summaries. NEVER call Skill tool, NEVER invoke "
        "/build, /bugfix, or any slash command (you don't have access — attempts "
        "waste turns)."
    )
    parts.append("")
    parts.append("FEATURE REQUEST:")
    parts.append(ctx.question or "(no feature request provided)")
    parts.append("")
    parts.append(f"SPEC (read this file): {spec_path}")
    parts.append(f"REVIEW FINDINGS (read this file — fix every finding): {review_fix_doc_path}")
    parts.append(f"REVIEW VERDICT: {verdict}")
    parts.append("")
    # 65EA1B86: inline HEAD content of in-scope test files so the fix-worker
    # does not re-derive assertions from spec ACs alone (012F2C02 RCA-3.1).
    _inline_block, _inline_n, _inline_b = _inline_inscope_test_files(ctx, scratchpad)
    if _inline_block:
        parts.append(_inline_block)
        parts.append("")
    if _inline_n > 0:
        _emit_safe("fix_prompt_test_files_inlined", {"count": _inline_n, "bytes": _inline_b})
    parts.append(
        "RULES (Boy Scout + Test Integrity):\n"
        "  - Every finding passed confidence ≥80 — they are all real. Fix all.\n"
        "  - The codebase must be CLEANER after this build, not just 'not worse'.\n"
        "  - TEST INTEGRITY: If fixing a finding causes a test to fail, fix the\n"
        "    CODE — never adjust test assertions to match broken behavior. The\n"
        "    only valid reason to change a test is if the SPEC changed.\n"
        "  - PRIOR-COMMIT ANTI-REVERT: Treat current test assertions as\n"
        "    authoritative — do not revert assertion changes from recent\n"
        "    commits. If a finding requires reverting a test assertion,\n"
        "    emit FIX BLOCKED with diagnosis.\n"
        "  - EARLY-RETURN: if review verdict is PASS (no findings), DO NOT touch\n"
        "    any file. Engine runs the test suite to verify; emit FIX SKIPPED.\n"
        "  - Iteration cap: 40 tool calls after the second verification PASS.\n"
        "    Exceeding the cap = emit FIX BLOCKED with diagnosis.\n"
    )
    parts.append(
        "ANTI-FABRICATION — producer rules in injection/producer-rules.md\n"
        "(## Anti-Fabrication — Producer Rules) apply. Surface-specific for FIX-WORKER:\n"
        "  - Fix ONLY filed findings. Each Edit maps to a specific finding\n"
        "    ID/title. No adjacent improvements, no `while we are here`.\n"
        "  - RESOLVED requires the same command/tool the review used to now\n"
        "    succeed. Did-not-change-production-code-path = NOT RESOLVED.\n"
        "  - No test changes to silence a finding (only valid reason is SPEC\n"
        "    change). No mass-delete as `simplification` — each deletion\n"
        "    traces to a specific finding.\n"
        "\n"
        "OUTPUT: end your response with EXACTLY one of:\n"
        "  FIX COMPLETE — [N] of [N] findings fixed. Files: [path1, path2, ...]\n"
        "  FIX SKIPPED  — review verdict PASS, no findings to fix.\n"
        "  FIX BLOCKED  — [N] of [M] findings fixed. Diagnosis: [root cause].\n"
        "                 Remaining: [list]"
    )
    parts.append(
        "ADDITIONALLY, after the trailing FIX marker line, emit a structured JSON block:\n"
        "\n"
        "  ## fix-output (structured)\n"
        "  ```json\n"
        '  {"fix_complete": true, "remaining": []}\n'
        "  ```\n"
        "\n"
        "Set fix_complete=true iff your marker is FIX COMPLETE or FIX SKIPPED. If FIX BLOCKED,\n"
        'set fix_complete=false and remaining to a list of {"file": "<path>", "issue": "<what still needs fixing>"} objects.\n'
        "This block is the AUTHORITATIVE gate signal — the engine reads it, not the FIX marker line.\n"
        "The FIX marker line stays for human audit. Both the marker line and this block are required."
    )
    parts.append(
        f"OUTPUT — Use the Write tool to write your full fix report to the fix report file: {fix_doc_path}. "
        "Your final response must be a status summary under 200 tokens consisting of only "
        "the FIX marker line and the ## fix-output (structured) JSON block (both required above). "
        "Do NOT echo the fix report body — it is already on disk."
    )
    parts.append("")
    parts.append(_worktree_edit_boundary_block(_resolve_worktree_root(ctx, scratchpad)))

    prompt = "\n".join(parts) + "\n\n" + _get_out_of_role_block()
    return StepResult(
        status="ok",
        data={
            "prompt": prompt,
            "log_path": str(fix_doc_path),
            "spec_path": str(spec_path),
            "review_doc_path": str(review_doc),
            "verdict": verdict,
            "prompt_bytes": len(prompt.encode("utf-8")),
        },
        duration_ms=0,
        step_name="build_fix_prompt",
    )


# ─── Step 6: invoke fix LLM ──────────────────────────────────────────────────


def _invoke_fix_llm(ctx, prev) -> StepResult:
    if not isinstance(prev, StepResult) or not isinstance(prev.data, dict):
        return StepResult(
            status="error", data=None, duration_ms=0,
            step_name="invoke_fix_llm",
            error="prev step did not produce a prompt",
            error_code="E_MISSING_PREV_DATA",
        )

    cfg = ctx.org_config or {}
    _doc = prev.data.get("log_path")
    if _doc:
        try:
            Path(_doc).unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass
    result = invoke_llm_subprocess(
        prompt=prev.data["prompt"],
        model=_resolve_model(cfg, "fix_model", _default_fix_model()),
        timeout_sec=_resolve_fix_timeout_sec(cfg),
        step_name="invoke_fix_llm",
        extra_data={
            "log_path": prev.data["log_path"],
            "spec_path": prev.data["spec_path"],
            "review_doc_path": prev.data["review_doc_path"],
            "verdict": prev.data["verdict"],
            "prompt": prev.data["prompt"],
        },
        # idle_timeout disabled — outer timeout_sec is sufficient (6923B6AC 2026-05-08).
        # BB8BFEFE proved 60s false-positives on legitimate tool-use sessions
        # with 60+s inter-event gaps; outer timeout_sec catches genuine hangs.
        allowed_tools=["Read", "Write", "Edit", "Grep", "Glob"],
    )
    # F3 cross-tree edit guard (A4479061): observability only, no auto-revert.
    if result.status == "ok":
        scratchpad = _resolve_scratchpad(ctx)
        worktree_root = _resolve_worktree_root(ctx, scratchpad)
        result = _maybe_emit_cross_tree_warning(result, worktree_root)
    return result


# ─── Step 6.5: fix_watchdog (after invoke_fix_llm) ──────────────────────────


def _fix_watchdog(ctx, prev) -> StepResult:
    """775D6752: post-LLM watchdog — abort on 0-token output after >= 60s.

    Trips on the hang signature (duration accumulated WITHOUT any tokens), the
    OPPOSITE of _green_watchdog which catches "you went too far". Here the
    signal is "nothing happened at all". recoverable=True because hangs are
    transient model hiccups (vs _green_watchdog recoverable=False for resource
    blow-up).
    """
    if not isinstance(prev, StepResult) or not isinstance(prev.data, dict):
        return StepResult(
            status="error", data=None, duration_ms=0,
            step_name="fix_watchdog",
            error="prev step did not produce data",
            error_code="E_MISSING_PREV_DATA",
        )
    duration_ms = prev.data.get("duration_ms") or 0
    tokens_out = prev.data.get("tokens_out")
    if duration_ms >= FIX_WATCHDOG_NO_PROGRESS_MS and (tokens_out is None or tokens_out == 0):
        _emit_safe("fix_watchdog_no_progress", {
            "duration_ms": duration_ms,
            "tokens_out": tokens_out,
            "phase": "phase_6",
        })
        return StepResult(
            status="error", data=prev.data, duration_ms=0,
            step_name="fix_watchdog",
            error=f"fix_llm produced 0 tokens after {duration_ms}ms — hang signature",
            error_code="E_FIX_LLM_NO_PROGRESS",
            recoverable=True,
        )
    return StepResult(status="ok", data=prev.data, duration_ms=0, step_name="fix_watchdog")


# ─── Step 7: write fix artifact ──────────────────────────────────────────────


def _resolve_fix_source(doc_path, raw_response: str) -> tuple[str, str]:
    """Return (body, source): fix-report body from worker file or raw_response.
    File with non-blank content -> (file_text, "worker_file");
    else fail-open -> (raw_response, "raw_response_fallback"). (FD2592D9 §1aa)"""
    try:
        p = Path(doc_path)
        if p.is_file():
            text = p.read_text(encoding="utf-8")
            if text.strip():
                return (text, "worker_file")
    except (OSError, TypeError):
        pass
    return (raw_response, "raw_response_fallback")


def _resolve_satisfaction_source(doc_path, raw_response: str) -> tuple[str, str]:
    """Return (body, source): satisfaction-doc body from worker file or raw_response.
    File with non-blank content -> (file_text, "worker_file");
    else fail-open -> (raw_response, "raw_response_fallback"). (FD2592D9 slice-6c §2.3)"""
    try:
        p = Path(doc_path)
        if p.is_file():
            text = p.read_text(encoding="utf-8")
            if text.strip():
                return (text, "worker_file")
    except (OSError, TypeError):
        pass
    return (raw_response, "raw_response_fallback")


def _write_fix_artifact(ctx, prev) -> StepResult:
    if not isinstance(prev, StepResult) or not isinstance(prev.data, dict):
        return StepResult(
            status="error", data=None, duration_ms=0,
            step_name="write_fix_artifact",
            error="prev step did not produce raw_response",
            error_code="E_MISSING_PREV_DATA",
        )
    raw = prev.data["raw_response"]
    log_path = Path(prev.data["log_path"])
    log_path.parent.mkdir(parents=True, exist_ok=True)

    fix_status = _parse_fix_status(raw)

    # Retry-once on FIX_NO_MARKER — transient truncation mitigation (DE71F5F4 sub-3)
    if fix_status == FIX_NO_MARKER:
        cfg = ctx.org_config or {}
        # Push retry step_name into telemetry_ctx so subprocess events surface
        # under invoke_fix_llm_retry (else outer write_fix_artifact masks it).
        # Mirrors phase_5_implement GREEN retry (5AE4164A).
        prev_run_ctx = telemetry_ctx.get_current_run()
        if prev_run_ctx is not None:
            telemetry_ctx.set_current_run_from(prev_run_ctx, step_name="invoke_fix_llm_retry")  # GH375
        # Unlink log_path so retry worker starts with a fresh file
        try:
            log_path.unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass
        try:
            retry_result = invoke_llm_subprocess(
                prompt=prev.data["prompt"],
                model=_resolve_model(cfg, "fix_model", _default_fix_model()),
                timeout_sec=_resolve_fix_timeout_sec(cfg),
                step_name="invoke_fix_llm_retry",
                extra_data={
                    "log_path": prev.data["log_path"],
                    "spec_path": prev.data["spec_path"],
                    "review_doc_path": prev.data["review_doc_path"],
                    "verdict": prev.data["verdict"],
                    "prompt": prev.data["prompt"],
                },
                # idle_timeout disabled — outer timeout_sec is sufficient (6923B6AC 2026-05-08).
                # Mirrors primary _invoke_fix_llm callsite; both opt out together
                # to keep contract symmetric.
                allowed_tools=["Read", "Write", "Edit", "Grep", "Glob"],
            )
        finally:
            if prev_run_ctx is not None:
                telemetry_ctx.set_current_run_from(prev_run_ctx, step_name=prev_run_ctx.step_name)  # GH375
        if retry_result.status == "ok" and isinstance(retry_result.data, dict):
            retry_raw = retry_result.data["raw_response"]
            retry_status = _parse_fix_status(retry_raw)
            if retry_status != FIX_NO_MARKER:
                # Retry succeeded — update raw/fix_status; single write below handles artifact
                raw = retry_raw
                fix_status = retry_status
        elif retry_result.status != "ok":
            # Retry itself had a subprocess/LLM error — propagate the retry error
            return retry_result
        else:
            # status == "ok" but data is not a dict — malformed subprocess result
            if not isinstance(retry_result.data, dict):
                raise AssertionError(
                    f"invoke_llm_subprocess returned ok but data is not a dict: {type(retry_result.data)}"
                )

    # Single resolver-driven write (FD2592D9 §2.4): prefer worker-written file,
    # fall back to raw if absent/empty.
    body, source = _resolve_fix_source(str(log_path), raw)
    try:
        log_path.write_text(body, encoding="utf-8")
    except OSError as exc:
        return StepResult(
            status="error", data=None, duration_ms=0,
            step_name="write_fix_artifact",
            error=str(exc),
            error_code="E_FIX_WRITE_FAILED",
        )
    _emit_safe("fix_writer_return_source", {"source": source, "bytes": len(body.encode())})

    # #12 (AA333D9B): structured fix-output verdict is the authoritative gate signal.
    structured, fr_reason = _parse_fix_structured(raw)
    if structured is not None:
        _emit_safe("fix_structured_ok", {"fix_complete": structured.fix_complete, "phase": 6})
    else:
        _emit_safe("fix_structured_missing", {"reason": fr_reason, "phase": 6})

    md_says_complete = fix_status in (FIX_COMPLETE, FIX_SKIPPED)
    if structured is not None and bool(structured.fix_complete) != md_says_complete:
        _emit_safe("fix_verdict_drift", {
            "marker_status": fix_status,
            "structured_fix_complete": structured.fix_complete,
            "phase": 6,
        })

    common_data = {
        "fix_doc_path": str(log_path),
        "spec_path": prev.data["spec_path"],
        "review_doc_path": prev.data["review_doc_path"],
        "review_verdict": prev.data["verdict"],
        "fix_bytes_written": len(body.encode("utf-8")),
        "fix_status": fix_status,
        "structured_verdict": structured,
        # 4C03CCED Ship 1C: thread manifest through synthesized prev.data so
        # downstream _run_pytest_post_fix / _commit_fix_code can resolve via
        # manifest_from_result. Default to empty manifest when upstream lacks
        # the field (only possible in test synthesis); production runs always
        # populate via invoke_llm_subprocess.
        "worker_written_paths": prev.data.get("worker_written_paths", []),
        "manifest_source": prev.data.get("manifest_source", "harness_tool_record"),
    }

    if structured is not None:
        if not structured.fix_complete:
            return StepResult(
                status="error", data=common_data, duration_ms=0,
                step_name="write_fix_artifact",
                error=f"fix worker reported not-complete — {len(structured.remaining)} finding(s) remain",
                error_code="E_FIX_BLOCKED",
                recoverable=False,
            )
        # else: structured.fix_complete is True → fall through to ok path
    else:
        # No structured block — legacy marker-based gate (unchanged behavior).
        if fix_status == FIX_BLOCKED:
            return StepResult(
                status="error", data=common_data, duration_ms=0,
                step_name="write_fix_artifact",
                error="fix worker reported BLOCKED — manual intervention required",
                error_code="E_FIX_BLOCKED",
                recoverable=False,
            )
        if fix_status == FIX_NO_MARKER:
            return StepResult(
                status="error", data=common_data, duration_ms=0,
                step_name="write_fix_artifact",
                error="fix worker output missing completion marker — likely truncated",
                error_code="E_FIX_NO_MARKER",
                recoverable=False,
            )
    # Step 7 (95D3E5F6) — telemetry-first additive observability. Emit
    # ``fix_disk_truth_coverage`` (always) and ``fix_files_drift`` (only
    # when the LLM claims a ``files_modified:`` line). No verdict-gate
    # behavior change — telemetry only.
    _emit_fix_disk_truth_telemetry(ctx, prev, body)
    return StepResult(
        status="ok",
        data=common_data,
        duration_ms=0,
        step_name="write_fix_artifact",
    )


# ─── Step 8: build satisfaction prompt ───────────────────────────────────────


def _build_satisfaction_prompt(ctx, prev) -> StepResult:
    if not isinstance(prev, StepResult) or not isinstance(prev.data, dict):
        return StepResult(
            status="error", data=None, duration_ms=0,
            step_name="build_satisfaction_prompt",
            error="prev step did not pass through paths",
            error_code="E_MISSING_PREV_DATA",
        )
    scratchpad = _resolve_scratchpad(ctx)
    spec_path = Path(prev.data["spec_path"])
    review_doc = Path(prev.data["review_doc_path"])
    fix_doc = Path(prev.data["fix_doc_path"])
    sat_doc_path = scratchpad / SATISFACTION_DOC_RELPATH

    parts: list[str] = []
    role = _maybe_role_template(ctx)
    if role:
        parts.append(role.rstrip())
        parts.append("")
    parts.append(_read_first_block(scratchpad))
    parts.append("")
    cfg = getattr(ctx, "org_config", None) or {}
    is_complex = (cfg.get("complexity") == "COMPLEX")

    # GH1065 (BE7C9CA0): tell the model the EXACT AC ids the deterministic
    # cross-check will demand. House idiom (mirrors :3102-3106): degrade to ""
    # on an unreadable spec, never raise. Zero ids -> "" -> the AC_CHECKLIST
    # block stays byte-identical to the pre-GH1065 text.
    _spec_text = ""
    try:
        _spec_text = spec_path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError, KeyError, TypeError):
        pass
    _ac_ids_directive = _ac_checklist_ids_directive(_spec_text)
    _ac_ids_directive_block = f"  {_ac_ids_directive}\n" if _ac_ids_directive else ""

    if is_complex:
        parts.append(
            "ROLE: You are the Opus satisfaction evaluator. Your response IS the "
            "satisfaction file itself — not a report about scoring. This is a "
            "SCORING-ONLY task — you MUST NOT edit any files. Score how well the "
            "implementation satisfies the spec across multiple dimensions. Open "
            "files yourself — do NOT trust summaries. Do NOT default to PASS to "
            "keep the pipeline moving."
        )
    else:
        parts.append(
            "ROLE: You are the Opus satisfaction evaluator. You WRITE the satisfaction "
            "evaluation to the file using the Write tool; your text response carries "
            "only the control signals. This is a "
            "SCORING-ONLY task — you MUST NOT edit any source files / open files "
            "yourself / do NOT default to PASS to keep the pipeline moving."
        )
    parts.append("")
    parts.append("FEATURE REQUEST:")
    parts.append(ctx.question or "(no feature request provided)")
    parts.append("")
    parts.append(f"SPEC (read this file): {spec_path}")
    parts.append(f"REVIEW FINDINGS (read this file): {review_doc}")
    parts.append(f"FIX REPORT (read this file): {fix_doc}")
    parts.append(
        "SUSPECT-FINDINGS NOTICE: any findings under the review doc's "
        "`## Suspect Findings` section FAILED deterministic grep-verification "
        "(verify_status=suspect-*). They are NOT grounds to FAIL satisfaction. "
        "Score ONLY the verified `## Aggregated Findings` section plus the actual diff."
    )
    parts.append("")
    parts.append(
        "DIMENSIONS (score 0-100 each, then composite as min):\n"
        "  - Spec compliance: every acceptance criterion met\n"
        "  - Test quality: meaningful assertions, edge cases, coverage\n"
        "  - Code quality: maintainability, conventions, complexity\n"
        "  - Completeness: all behavior items implemented (FEATURE+)\n"
        "  - Boy Scout: codebase cleaner than before (FEATURE+)\n"
    )
    parts.append(
        "The engine deterministically cross-checks the AC Checklist section against the spec's "
        "## Acceptance Criteria — a missing section, a missing AC id, or any FAIL entry "
        "fails the gate."
    )
    if is_complex:
        parts.append(
            "OUTPUT — your response IS the file content of\n"
            "reviews/build-satisfaction.md. Start your response DIRECTLY with\n"
            "`# Satisfaction Evaluation`. No preamble. No status markers other than\n"
            "the trailing SCORE + VERDICT lines. The file IS the evaluation doc.\n"
            "\n"
            "REQUIRED sections, in this exact order, no others:\n"
            "\n"
            "  # Satisfaction Evaluation\n"
            "\n"
            "  ## Per-Dimension Scores\n"
            "  - Spec compliance: <0-100>\n"
            "  - Test quality: <0-100>\n"
            "  - Code quality: <0-100>\n"
            "  - Completeness: <0-100>\n"
            "  - Boy Scout: <0-100>\n"
            "\n"
            "  ## Evidence Per Dimension\n"
            "  <one bullet per dimension citing the file or test that justifies\n"
            "   the score. No score may stand without an evidence bullet.>\n"
            "\n"
            f"  {AC_CHECKLIST_HEADER}\n"
            "  <one bullet per Acceptance Criterion in the SPEC's ## Acceptance Criteria\n"
            f"   section, in spec order, format exactly: `{AC_CHECKLIST_ENTRY_FORMAT}`.\n"
            "   Judge each AC on the IMPLEMENTATION's behavior (Read the code;\n"
            "   spec/impl drift is OK when the behavior satisfies the AC's intent). Any\n"
            "   FAIL must also appear under Concerns. If the spec has no ## Acceptance\n"
            "   Criteria section, write `- none`.>\n"
            f"{_ac_ids_directive_block}"
            "\n"
            "  ## Concerns\n"
            "  <bullets — what would prevent shipping; `none` only if you have\n"
            "   actively looked for concerns and found none.>\n"
            "\n"
            "  ## Composite\n"
            "  SCORE: <0-100>   ← MIN over dimensions (not avg, not max)\n"
        )
    else:
        parts.append(
            f"OUTPUT — WRITE the FULL evaluation (REQUIRED sections below) to EXACTLY this file path using the Write tool:\n"
            f"  {sat_doc_path}\n"
            "The FILE starts with `# Satisfaction Evaluation`. Do NOT echo the file body in your text response.\n"
            "In your text response return ONLY (≤200 tokens): the path written, the SCORE line, "
            "the VERDICT line, and the `## satisfaction-output` structured block "
            "— these are the control signals the engine reads.\n"
            "\n"
            "REQUIRED sections, in this exact order, no others:\n"
            "\n"
            "  # Satisfaction Evaluation\n"
            "\n"
            "  ## Per-Dimension Scores\n"
            "  - Spec compliance: <0-100>\n"
            "  - Test quality: <0-100>\n"
            "  - Code quality: <0-100>\n"
            "  - Completeness: <0-100>\n"
            "  - Boy Scout: <0-100>\n"
            "\n"
            "  ## Evidence Per Dimension\n"
            "  <one bullet per dimension citing the file or test that justifies\n"
            "   the score. No score may stand without an evidence bullet.>\n"
            "\n"
            f"  {AC_CHECKLIST_HEADER}\n"
            "  <one bullet per Acceptance Criterion in the SPEC's ## Acceptance Criteria\n"
            f"   section, in spec order, format exactly: `{AC_CHECKLIST_ENTRY_FORMAT}`.\n"
            "   Judge each AC on the IMPLEMENTATION's behavior (Read the code;\n"
            "   spec/impl drift is OK when the behavior satisfies the AC's intent). Any\n"
            "   FAIL must also appear under Concerns. If the spec has no ## Acceptance\n"
            "   Criteria section, write `- none`.>\n"
            f"{_ac_ids_directive_block}"
            "\n"
            "  ## Concerns\n"
            "  <bullets — what would prevent shipping; `none` only if you have\n"
            "   actively looked for concerns and found none.>\n"
            "\n"
            "  ## Composite\n"
            "  SCORE: <0-100>   ← MIN over dimensions (not avg, not max)\n"
        )
    parts.append("")
    parts.append(_get_anti_fab_prompt())
    threshold = int(cfg.get("satisfaction_threshold") or DEFAULT_SATISFACTION_THRESHOLD)
    parts.append(
        f"INVARIANT: SCORE >= {threshold} ⟺ VERDICT=PASS ⟺ structured.satisfied=true.\n"
        f"SCORE <  {threshold} ⟺ VERDICT=FAIL ⟺ structured.satisfied=false.\n"
        "Drift between SCORE and structured.satisfied is detected by the engine and treated as FAIL.\n"
        "Pick SCORE from the rubric; derive VERDICT and structured.satisfied from SCORE."
    )
    parts.append(
        "GREP-VERIFY MANDATE (before returning PASS):\n"
        "For every path:line citation in fix-doc you are validating, run an equivalent of:\n"
        '    grep -n "<expected content>" <path>\n'
        "If grep does NOT confirm the citation (path missing OR line content different) — return FAIL with finding `fabricated_fix_citation: <path>:<line>`. Do NOT PASS based on fix-doc claims alone."
    )
    parts.append(_SATISFACTION_STABLE_PREFIX)
    parts.append(
        "RULE-AXES REQUIREMENT (GH497): on VERDICT: FAIL, you MUST also emit a line\n"
        "`RULE-AXES: §<rule>, §<rule>, ...` citing every workflows.md rule axis your\n"
        "Concerns invoke (or `RULE-AXES: NONE` if none apply). This is a separate\n"
        "line from VERDICT/SCORE and is required whenever VERDICT is FAIL."
    )
    parts.append(
        "ADDITIONALLY, after the trailing VERDICT line, emit a structured JSON block:\n"
        "\n"
        "  ## satisfaction-output (structured)\n"
        "  ```json\n"
        '  {"satisfied": true, "fixes_required": []}\n'
        "  ```\n"
        "\n"
        "Set satisfied=true iff VERDICT=PASS. If VERDICT=FAIL, set satisfied=false and\n"
        'fixes_required to a list of {"file": "<path>", "issue": "<what must change>"} objects.\n'
        "This block is the AUTHORITATIVE gate signal — the engine reads it, not the SCORE line.\n"
        "The SCORE/VERDICT lines stay for human audit. This block sits ALONGSIDE the audit doc;\n"
        "it does not replace any section. Both the SCORE line and this block are required."
    )

    prompt = "\n".join(parts) + "\n\n" + _get_out_of_role_block()
    return StepResult(
        status="ok",
        data={
            "prompt": prompt,
            "stable_prefix": _SATISFACTION_STABLE_PREFIX,
            "doc_path": str(sat_doc_path),
            "spec_path": str(spec_path),
            "review_doc_path": str(review_doc),
            "fix_doc_path": str(fix_doc),
            "prompt_bytes": len(prompt.encode("utf-8")),
            "review_verdict": prev.data.get("review_verdict"),
            "fix_commit_sha": prev.data.get("fix_commit_sha"),
        },
        duration_ms=0,
        step_name="build_satisfaction_prompt",
    )


# ─── Multi-evaluator satisfaction helpers (021D8FAE) ─────────────────────────


def _run_satisfaction_evaluators_parallel(
    prompt: str,
    model: str,
    timeout_sec: int,
    extra_data: dict,
    n: int = 3,
    stable_prefix: str = "",
) -> "list[StepResult]":
    """Dispatch n identical invoke_llm_subprocess calls concurrently via ThreadPoolExecutor.

    Spec §1 (021D8FAE): MUST use concurrent.futures.ThreadPoolExecutor(max_workers=n),
    not a serial loop. Returns list[StepResult] in submission/index order.
    25e75663: command param replaced by model:str.
    GH705: stable_prefix threaded through the shipped {prompt, stable_prefix}
    caching seam — prompt stays full/byte-identical.
    """
    with concurrent.futures.ThreadPoolExecutor(max_workers=n) as executor:
        futures = [
            executor.submit(
                invoke_llm_subprocess,
                prompt=prompt,
                model=model,
                timeout_sec=timeout_sec,
                step_name="invoke_satisfaction_llm",
                extra_data=extra_data,
                hard_gate=True,
                gate_label="satisfaction",
                allowed_tools=["Read"],
                stable_prefix=stable_prefix,
            )
            for _ in range(n)
        ]
    return [f.result() for f in futures]


def _decide_satisfaction_passed(
    structured: "SatisfactionVerdict | None",
    score: "int | None",
    threshold: int,
) -> "tuple[bool, str]":
    """Return (passed, reason_code) applying strict-AND drift-aware policy.

    reason_code ∈ {
      "concurring_pass", "concurring_fail",
      "drift_structured_pass_score_below", "drift_structured_fail_score_above",
      "structured_only_no_score", "score_only_below_threshold",
      "no_signals",
    }

    9702B73F: supersedes F60BDC71 unconditional structured-override.
    """
    md_says_pass = score is not None and score >= threshold
    if structured is not None and score is not None:
        s_pass = bool(structured.satisfied)
        if s_pass and md_says_pass:
            return True, "concurring_pass"
        if (not s_pass) and (not md_says_pass):
            return False, "concurring_fail"
        return False, (
            "drift_structured_pass_score_below" if s_pass
            else "drift_structured_fail_score_above"
        )
    if structured is not None:
        # structured present but no SCORE — cannot verify numerically
        return False, "structured_only_no_score"
    if score is not None:
        if md_says_pass:
            return True, "concurring_pass"  # single-signal pass; reuse label for AC6 backcompat
        return False, "score_only_below_threshold"
    return False, "no_signals"


def _deterministic_satisfaction_override(
    passed: bool,
    reason_code: str,
    *,
    score: "int | None",
    threshold: int,
    all_findings_suspect: bool,
    fix_commit_sha: "str | None",
    structured: "SatisfactionVerdict | None",
) -> "tuple[bool, str]":
    """Principle-A deterministic-first override (4B9DF7D3).

    Relax a below-threshold isolated-LLM satisfaction FAIL ONLY when three
    independent deterministic signals all corroborate that the findings are
    ungroundable and the fix correctly changed nothing. Conservative AND —
    never turns a genuine bad build into a PASS. Layered AFTER (never inside)
    _decide_satisfaction_passed; its strict-AND policy is unchanged.
    """
    if (
        passed is False
        and score is not None
        and score < threshold
        and all_findings_suspect
        and fix_commit_sha is None
        and not (structured is not None and structured.fixes_required)
    ):
        _emit_safe(
            "satisfaction_relaxed_corroborated",
            {
                "score": score,
                "threshold": threshold,
                "all_findings_suspect": all_findings_suspect,
                "fix_commit_sha": None,
                "phase": 6,
            },
        )
        return True, "corroborated_ungroundable_skip"
    return passed, reason_code


def _aggregate_satisfaction(evals: "list[dict]", threshold: int) -> dict:
    """Aggregate multi-evaluator satisfaction results: majority vote + median score.

    Args:
        evals: list of dicts with keys:
            index: int
            score: int | None
            structured: SatisfactionVerdict | None
            status: "ok" | "error"
            error_code: str | None
        threshold: int  satisfaction_threshold

    Returns dict with keys:
        n_valid, n_attempted, per_eval, median_score, majority_passed,
        passed_count, agreement, degraded, fixes_required
    """
    valid = [
        e for e in evals
        if e["status"] == "ok" and (e["score"] is not None or e["structured"] is not None)
    ]
    n_valid = len(valid)
    per_eval_list = []
    for e in valid:
        passed, reason_code = _decide_satisfaction_passed(e["structured"], e["score"], threshold)
        per_eval_list.append({
            "index": e["index"],
            "status": e["status"],
            "score": e["score"],
            "structured_ok": e["structured"] is not None,
            "satisfied": (bool(e["structured"].satisfied) if e["structured"] is not None else None),
            "passed": passed,
            "reason_code": reason_code,
        })
    per_eval = per_eval_list
    # Emit per-evaluator drift events (single source of truth — parse-loop emit removed)
    for pe in per_eval:
        if pe["reason_code"].startswith("drift_"):
            e = next(ev for ev in valid if ev["index"] == pe["index"])
            _emit_safe(
                "satisfaction_verdict_drift",
                {
                    "score": pe["score"],
                    "threshold": threshold,
                    "structured_satisfied": bool(e["structured"].satisfied),
                    "gate_decision": "fail-closed",
                    "evaluator_index": pe["index"],
                    "phase": 6,
                },
            )
    eff = [e["score"] for e in valid if e["score"] is not None]
    passed_count = sum(1 for p in per_eval if p["passed"])
    median_score = int(round(statistics.median(eff))) if eff else None
    majority_passed = passed_count * 2 > n_valid  # strict — 1-1 tie ⇒ False (fail-closed)
    if n_valid == 1:
        agreement = "single"
    elif passed_count in (0, n_valid):
        agreement = "unanimous"
    elif n_valid == 2:
        agreement = "split"  # 1-1
    else:
        agreement = "2-1"  # n_valid==3, passed_count in {1,2}
    fixes_required = [
        f
        for e in valid
        if e["structured"] is not None
        for f in e["structured"].fixes_required
    ]
    return {
        "n_valid": n_valid,
        "n_attempted": len(evals),
        "per_eval": per_eval,
        "median_score": median_score,
        "majority_passed": majority_passed,
        "passed_count": passed_count,
        "agreement": agreement,
        "degraded": n_valid < 2,
        "fixes_required": fixes_required,
    }


# ─── Step 9: invoke satisfaction LLM ─────────────────────────────────────────


def _invoke_satisfaction_llm(ctx, prev) -> StepResult:
    if not isinstance(prev, StepResult) or not isinstance(prev.data, dict):
        return StepResult(
            status="error", data=None, duration_ms=0,
            step_name="invoke_satisfaction_llm",
            error="prev step did not produce a prompt",
            error_code="E_MISSING_PREV_DATA",
        )
    cfg = ctx.org_config or {}
    sat_model = _resolve_model(cfg, "satisfaction_model", _default_satisfaction_model())
    timeout_sec = _resolve_satisfaction_timeout_sec(cfg)
    extra_data = {
        "doc_path": prev.data["doc_path"],
        "spec_path": prev.data["spec_path"],
        "review_doc_path": prev.data["review_doc_path"],
        "fix_doc_path": prev.data["fix_doc_path"],
        "review_verdict": prev.data.get("review_verdict"),
        "fix_commit_sha": prev.data.get("fix_commit_sha"),
    }

    # AC15: read cfg.get("complexity") directly — never call _resolve_complexity
    # (avoids double complexity_default_used emit; no new ValueError path).
    if cfg.get("complexity") != "COMPLEX":
        # SIMPLE / FEATURE / unset: single evaluator
        # Unlink stale doc_path before dispatch (mirror _invoke_fix_llm :2167).
        try:
            Path(prev.data["doc_path"]).unlink()
        except (FileNotFoundError, OSError):
            pass
        return invoke_llm_subprocess(
            prompt=prev.data["prompt"],
            model=sat_model,
            timeout_sec=timeout_sec,
            step_name="invoke_satisfaction_llm",
            extra_data=extra_data,
            hard_gate=True,
            gate_label="satisfaction",
            allowed_tools=["Read", "Write"],
        )

    # COMPLEX: 3 parallel evaluators via ThreadPoolExecutor (spec §1, 021D8FAE)
    results: list[StepResult] = _run_satisfaction_evaluators_parallel(
        prev.data["prompt"], sat_model, timeout_sec, extra_data, n=3,
        stable_prefix=prev.data.get("stable_prefix", ""),
    )
    ok_results = [r for r in results if r.status == "ok"]
    if not ok_results:
        # All 3 subprocesses errored → return first error as-is (AC13)
        # error_code is the LLM error (e.g. E_LLM_TIMEOUT), NOT E_SATISFACTION_BELOW_THRESHOLD
        return results[0]

    return StepResult(
        status="ok",
        step_name="invoke_satisfaction_llm",
        duration_ms=0,
        data={
            # backward-compat: _write_satisfaction_doc's last_findings + non-multi fallback read this
            "raw_response": ok_results[0].data["raw_response"],
            "is_multi_evaluator": True,
            "evaluator_responses": [
                {
                    "index": i,
                    "status": r.status,
                    "raw_response": (r.data["raw_response"] if r.status == "ok" else ""),
                    "error_code": (None if r.status == "ok" else r.error_code),
                }
                for i, r in enumerate(results)
            ],
            "doc_path": prev.data["doc_path"],
            "spec_path": prev.data["spec_path"],
            "review_doc_path": prev.data["review_doc_path"],
            "fix_doc_path": prev.data["fix_doc_path"],
        },
    )


# ─── GH388: per-AC checklist forcing function ────────────────────────────────
# Deterministic cross-check of the satisfaction evaluator's "## AC Checklist"
# output against the frozen spec's "## Acceptance Criteria" section. Closes
# the impl-vs-spec-AC-beyond-green-bar gap (7ED75C4B: 18 HIGH slipped past
# a green satisfaction bar because "Spec compliance" was one unverified bullet).
_AC_SECTION_HEADER_RE = re.compile(r"^#{2,6}\s.*acceptance criteria", re.IGNORECASE)

# §1g: single source of truth for the AC-checklist prompt/parser contract (GH432).
AC_CHECKLIST_HEADER = "## AC Checklist"
AC_CHECKLIST_ENTRY_FORMAT = "- AC<id>: PASS|FAIL — <path:line evidence>"
AC_CHECKLIST_ENTRY_EXAMPLE = "- AC1: PASS — path/file.py:12 evidence"

_AC_CHECKLIST_HEADER_RE = re.compile(
    r"^#{2,6}\s*" + re.escape(AC_CHECKLIST_HEADER.lstrip("# ")), re.IGNORECASE
)


def _parse_spec_ac_ids(spec_text: str) -> list:
    """GH388: parse ordered, deduped AC ids out of a spec's ## Acceptance Criteria section."""
    lines = spec_text.splitlines()
    start = None
    for idx, line in enumerate(lines):
        if _AC_SECTION_HEADER_RE.match(line):
            start = idx + 1
            break
    if start is None:
        return []
    end = len(lines)
    for idx in range(start, len(lines)):
        if re.match(r"^#{1,2}\s", lines[idx]):
            end = idx
            break
    ids: list = []
    seen: set = set()
    numbered_re = re.compile(r"^\s{0,3}(\d+)[.)]\s")
    table_re = re.compile(r"^\|\s*AC[- ]?([A-Za-z0-9_.-]+)\s*\|")
    for line in lines[start:end]:
        m = numbered_re.match(line)
        if not m:
            m = table_re.match(line)
        if m:
            ac_id = m.group(1)
            if ac_id not in seen:
                seen.add(ac_id)
                ids.append(ac_id)
    return ids


def _ac_checklist_ids_directive(spec_text: str) -> str:
    """GH1065 (BE7C9CA0): enumerate the EXACT AC ids the checklist must use.

    §1g — ONE canonical source: the ids come from `_parse_spec_ac_ids` and are
    emitted VERBATIM (no stripping, case-folding, padding or re-parsing) in
    parse order. NO TRUNCATION: every parsed id is named, at any n — capping or
    eliding the list would silently reinstate `parsed-ids != checklist-ids`.
    Returns "" when no ids parse, so the prompt stays byte-identical to today's.
    """
    ids = _parse_spec_ac_ids(spec_text or "")
    if not ids:
        return ""
    enumerated = ", ".join(f"AC{ac_id}" for ac_id in ids)
    return (
        "AC ID MANDATE (GH1065): the engine cross-checks your bullet ids against the "
        "ids parsed from the spec. Use EXACTLY these ids, in this order, one bullet "
        f"each: {enumerated}. Ignore any other AC labels embedded in the AC prose, "
        "headings or bold text; they are not ids."
    )


def _parse_ac_checklist(doc_text: str):
    """GH388: parse the satisfaction response's ## AC Checklist section into {id: PASS|FAIL}."""
    lines = doc_text.splitlines()
    start = None
    for idx, line in enumerate(lines):
        if _AC_CHECKLIST_HEADER_RE.match(line):
            start = idx + 1
            break
    if start is None:
        return None
    end = len(lines)
    for idx in range(start, len(lines)):
        if re.match(r"^#{1,6}\s", lines[idx]):
            end = idx
            break
    bullet_re = re.compile(
        r"^\s*[-*]\s*(?:\*\*|`)?AC[- ]?([A-Za-z0-9_.-]+)(?:\*\*|`)?\s*:\s*(?:\*\*|`)?(PASS|FAIL)\b",
        re.IGNORECASE,
    )
    checklist: dict = {}
    for line in lines[start:end]:
        m = bullet_re.match(line)
        if m:
            checklist[m.group(1)] = m.group(2).upper()
    return checklist


def _verify_ac_checklist(spec_text: str, response_text: str):
    """GH388: deterministic cross-check — verdict in {"skip","fail","pass"} + detail dict."""
    if not get_config().gate_enabled("HAL_AC_CHECKLIST_GATE"):
        return "skip", {"reason": "env_disabled"}
    spec_ids = _parse_spec_ac_ids(spec_text)
    if not spec_ids:
        return "skip", {"reason": "no_spec_acs"}
    checklist = _parse_ac_checklist(response_text)
    if checklist is None:
        return "fail", {"reason": "missing_section", "missing": spec_ids, "failed": [], "spec_ac_count": len(spec_ids)}
    res = _classify_parity(spec_ids, checklist, ignore_extra=True)
    if res.result in ("MISSING", "FAIL_CLAIMED"):
        return "fail", {
            "reason": "entries",
            "missing": res.details["missing"],
            "failed": res.details["failed"],
            "spec_ac_count": res.details["spec_ac_count"],
        }
    return "pass", {
        "missing": [],
        "failed": [],
        "spec_ac_count": res.details["spec_ac_count"],
    }


# ─── Step 10: write satisfaction doc + HARD GATE on threshold ────────────────


def _write_satisfaction_doc(ctx, prev) -> StepResult:
    """Write doc, parse score, HARD GATE on threshold.

    Doc is always written before the gate runs so caller can inspect even
    on failure (mirrors phase-5 write_validation_doc + gate_on_validation
    sequencing — but inlined here since the satisfaction gate has no
    downstream steps that need the verdict on success).

    Multi-evaluator branch (021D8FAE): if prev.data has is_multi_evaluator=True
    and evaluator_responses (list, len>1), dispatches the 3-evaluator aggregation
    path. Otherwise falls through to the existing single-evaluator body.
    """
    if not isinstance(prev, StepResult) or not isinstance(prev.data, dict):
        return StepResult(
            status="error", data=None, duration_ms=0,
            step_name="write_satisfaction_doc",
            error="prev step did not produce raw_response",
            error_code="E_MISSING_PREV_DATA",
        )

    # ── multi-evaluator path (COMPLEX, 021D8FAE) ─────────────────────────────
    evaluator_responses = prev.data.get("evaluator_responses")
    is_multi = (
        bool(prev.data.get("is_multi_evaluator"))
        and isinstance(evaluator_responses, list)
        and len(evaluator_responses) > 1
    )

    if is_multi:
        return _write_satisfaction_doc_multi(ctx, prev, evaluator_responses)  # type: ignore[arg-type]

    # ── single-evaluator path (SIMPLE / FEATURE / unset) ─────────────────────
    raw = prev.data["raw_response"]
    doc_path = Path(prev.data["doc_path"])
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    body, return_source = _resolve_satisfaction_source(str(doc_path), raw)
    try:
        doc_path.write_text(body, encoding="utf-8")
    except OSError as exc:
        return StepResult(
            status="error", data=None, duration_ms=0,
            step_name="write_satisfaction_doc",
            error=str(exc),
            error_code="E_SATISFACTION_WRITE_FAILED",
        )
    _emit_safe("satisfaction_writer_return_source", {"source": return_source, "bytes": len(body.encode("utf-8"))})

    cfg = ctx.org_config or {}
    threshold = int(cfg.get("satisfaction_threshold") or DEFAULT_SATISFACTION_THRESHOLD)
    score = _parse_satisfaction_score(raw)
    structured, sv_reason = _parse_satisfaction_structured(raw)

    if structured is not None:
        _emit_safe("satisfaction_structured_ok", {"satisfied": structured.satisfied, "phase": 6})
    else:
        _emit_safe("satisfaction_structured_missing", {"reason": sv_reason, "phase": 6})

    # 9702B73F: strict-AND policy via helper (supersedes F60BDC71 unconditional structured-override)
    passed, reason_code = _decide_satisfaction_passed(structured, score, threshold)

    # 4B9DF7D3: Principle-A deterministic override — relax isolated-LLM FAIL when all three
    # deterministic corroborators agree the findings are ungroundable and the fix was correct.
    review_doc_text = ""
    try:
        review_doc_text = Path(prev.data.get("review_doc_path") or "").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError, TypeError):
        review_doc_text = ""
    all_findings_suspect = (
        prev.data.get("review_verdict") == VERDICT_SUSPECT
        and SUSPECT_FINDINGS_SECTION_HEADER in review_doc_text
    )
    passed, reason_code = _deterministic_satisfaction_override(
        passed,
        reason_code,
        score=score,
        threshold=threshold,
        all_findings_suspect=all_findings_suspect,
        fix_commit_sha=prev.data.get("fix_commit_sha"),
        structured=structured,
    )

    # GH388: per-AC checklist deterministic cross-check (only downgrades a would-be PASS —
    # non-masking invariant, an already-failing build keeps its original reason_code).
    spec_text = ""
    try:
        spec_text = Path(prev.data["spec_path"]).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError, KeyError, TypeError):
        pass  # unreadable spec -> _verify_ac_checklist sees "" -> skip (no_spec_acs)
    ac_verdict, ac_detail = _verify_ac_checklist(spec_text, body)
    try:
        _sat_spec_sha = file_sha256(prev.data["spec_path"])
    except (OSError, ValueError, KeyError, TypeError):
        _sat_spec_sha = None
    _emit_safe("satisfaction_ac_checklist", {
        "verdict": ac_verdict, "reason": ac_detail.get("reason"),
        "spec_ac_count": ac_detail.get("spec_ac_count", 0),
        "missing_count": len(ac_detail.get("missing", [])),
        "failed_count": len(ac_detail.get("failed", [])), "phase": 6,
        "source": return_source,
        "spec_sha": _sat_spec_sha,
    })
    if ac_verdict == "fail" and passed:
        passed, reason_code = False, "ac_checklist_fail"

    # Drift event: only when both signals present and disagree (reason_code.startswith("drift_"))
    # Note 5: single-eval drift emit has no evaluator_index (implicit-None preserved)
    if reason_code.startswith("drift_"):
        _emit_safe(
            "satisfaction_verdict_drift",
            {
                "score": score,
                "threshold": threshold,
                "structured_satisfied": structured.satisfied,  # type: ignore[union-attr]
                "gate_decision": "fail-closed",
                "phase": 6,
            },
        )

    common_data = {
        "satisfaction_doc_path": str(doc_path),
        "spec_path": prev.data["spec_path"],
        "review_doc_path": prev.data["review_doc_path"],
        "fix_doc_path": prev.data["fix_doc_path"],
        "satisfaction_bytes_written": len(body.encode("utf-8")),
        "score": score,
        "threshold": threshold,
        "structured_verdict": structured,
    }

    if not passed:
        # C834481A: persist last_findings.json so orchestrator-driven re-attempts
        # can inject prior context into the reviewer prompt, avoiding re-flagging
        # the same findings without awareness of what the latest commit addressed.
        _persist_satisfaction_last_findings(ctx, prev, score, threshold)

    if not passed:
        # Map reason_code to FAIL error message (9702B73F — no duplicated policy)
        if reason_code.startswith("drift_"):
            # Both signals present but disagree → fail-closed
            error_msg = (
                f"satisfaction verdict drift — score {score} vs threshold {threshold}, "
                f"structured.satisfied={structured.satisfied} "  # type: ignore[union-attr]
                f"({len(structured.fixes_required)} fix(es) listed); fail-closed"  # type: ignore[union-attr]
            )
        elif reason_code == "structured_only_no_score":
            # Structured present but SCORE absent → fail-closed (cannot verify numerically)
            error_msg = (
                f"satisfaction evaluator omitted SCORE — structured.satisfied="
                f"{structured.satisfied} cannot override missing numeric verification; fail-closed"  # type: ignore[union-attr]
            )
        elif reason_code == "concurring_fail":
            # Both signals agree on fail
            error_msg = f"satisfaction not satisfied — {len(structured.fixes_required)} fix(es) required"  # type: ignore[union-attr]
        elif reason_code == "no_signals":
            error_msg = "satisfaction evaluator omitted SCORE and structured verdict — invalid evaluation"
        elif reason_code == "ac_checklist_fail":
            # GH388: per-AC checklist gate — missing section or explicit FAIL entries.
            error_msg = (f"AC checklist gate failed — missing={ac_detail.get('missing', [])} "
                         f"failed={ac_detail.get('failed', [])}")
        else:
            # score_only_below_threshold
            error_msg = f"satisfaction score {score} < threshold {threshold}"
        record_satisfaction_reject(reason_code, error_msg, score, threshold, axes_text=body)
        return StepResult(
            status="error", data={**common_data, "invalidate_cycle_sentinels_on_fail": True}, duration_ms=0,
            step_name="write_satisfaction_doc",
            error=error_msg,
            error_code=("E_SATISFACTION_AC_CHECKLIST" if reason_code == "ac_checklist_fail"
                        else "E_SATISFACTION_BELOW_THRESHOLD"),
            recoverable=False,
        )
    return StepResult(
        status="ok",
        data=common_data,
        duration_ms=0,
        step_name="write_satisfaction_doc",
    )


def _persist_satisfaction_last_findings(ctx, prev, score, threshold: int) -> None:
    """Persist reviews/last_findings.json for the satisfaction FAIL path.

    Reused by both single-eval and multi-eval FAIL paths (C834481A).
    Wrapped in try/except — swallows all errors to avoid masking the
    satisfaction gate result itself.
    """
    try:
        scratchpad = _resolve_scratchpad(ctx)
        last_findings_path = scratchpad / "reviews" / "last_findings.json"
        attempt = 1
        if last_findings_path.is_file():
            try:
                prior = json.loads(last_findings_path.read_text(encoding="utf-8"))
                attempt = int(prior.get("attempt", 0)) + 1
            except (OSError, ValueError, TypeError):
                attempt = 1
        review_doc_path_str = prev.data.get("review_doc_path", "")
        structured_findings: list = []
        try:
            review_text = Path(review_doc_path_str).read_text(encoding="utf-8")
            parsed = extract_structured_findings(review_text)
            if isinstance(parsed, list):
                structured_findings = parsed
        except (OSError, TypeError, ValueError):
            structured_findings = []
        last_findings_payload = json.dumps(
            {
                "attempt": attempt,
                "score": score,
                "threshold": threshold,
                "review_doc_path": review_doc_path_str,
                "structured_findings": structured_findings,
            },
            indent=2,
        )
        last_findings_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(last_findings_path, last_findings_payload)
        _emit_safe(
            "phase_6_persist_last_findings",
            {
                "path": str(last_findings_path),
                "attempt": attempt,
                "score": score,
                "finding_count": len(structured_findings),
            },
        )
    except Exception:  # noqa: BLE001
        logger.warning("C834481A: failed to write last_findings.json", exc_info=True)


def _write_satisfaction_doc_multi(ctx, prev, evaluator_responses: list) -> StepResult:
    """Multi-evaluator satisfaction gate (COMPLEX path, 021D8FAE).

    Parses each evaluator's raw response, aggregates via majority vote +
    median score, writes a composite doc, emits telemetry, applies gate.
    """
    cfg = ctx.org_config or {}
    threshold = int(cfg.get("satisfaction_threshold") or DEFAULT_SATISFACTION_THRESHOLD)
    doc_path = Path(prev.data["doc_path"])

    # GH388: read spec text once for the per-AC checklist cross-check (same try/except
    # shape as the single-eval path — unreadable spec -> "" -> skip(no_spec_acs)).
    spec_text = ""
    try:
        spec_text = Path(prev.data["spec_path"]).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError, KeyError, TypeError):
        pass
    try:
        _sat_spec_sha = file_sha256(prev.data["spec_path"])
    except (OSError, ValueError, KeyError, TypeError):
        _sat_spec_sha = None

    # Parse each evaluator response
    evals: list[dict] = []
    for entry in evaluator_responses:
        i = entry["index"]
        status = entry.get("status", "error")
        if status == "ok":
            raw_i: str = entry.get("raw_response", "")
            score_i = _parse_satisfaction_score(raw_i)
            structured_i, sv_reason_i = _parse_satisfaction_structured(raw_i)
            # Per-evaluator structured/drift telemetry (spec §3 step 1)
            if structured_i is not None:
                _emit_safe(
                    "satisfaction_structured_ok",
                    {"satisfied": structured_i.satisfied, "phase": 6, "evaluator_index": i},
                )
            else:
                _emit_safe(
                    "satisfaction_structured_missing",
                    {"reason": sv_reason_i, "phase": 6, "evaluator_index": i},
                )
            # GH388: per-evaluator AC-checklist cross-check
            ac_verdict_i, ac_detail_i = _verify_ac_checklist(spec_text, raw_i)
            _emit_safe("satisfaction_ac_checklist", {
                "verdict": ac_verdict_i, "reason": ac_detail_i.get("reason"),
                "spec_ac_count": ac_detail_i.get("spec_ac_count", 0),
                "missing_count": len(ac_detail_i.get("missing", [])),
                "failed_count": len(ac_detail_i.get("failed", [])), "phase": 6,
                "evaluator_index": i,
                "spec_sha": _sat_spec_sha,
            })
        else:
            score_i = None
            structured_i = None
            ac_verdict_i = None

        evals.append({
            "index": i,
            "score": score_i,
            "structured": structured_i,
            "status": status,
            "error_code": entry.get("error_code"),
            "ac_verdict": ac_verdict_i,
        })

    # GH388: majority checklist-fail over valid (status=="ok") evaluators. Skip verdicts
    # (None from errored evaluators) count as neither pass nor fail.
    _ac_valid_verdicts = [e["ac_verdict"] for e in evals if e["status"] == "ok"]
    _ac_n_valid = len(_ac_valid_verdicts)
    _ac_n_fail = sum(1 for v in _ac_valid_verdicts if v == "fail")
    ac_majority_fail = _ac_n_valid > 0 and (_ac_n_fail * 2 > _ac_n_valid)
    ac_checklist_summary = ", ".join(
        (e["ac_verdict"] if e["status"] == "ok" and e["ac_verdict"] is not None else "skip")
        for e in evals
    )

    agg = _aggregate_satisfaction(evals, threshold)
    n_valid = agg["n_valid"]
    n_attempted = agg["n_attempted"]

    # Build composite doc (spec §3 step 3) — ALWAYS written (overwrites)
    # Table header: 1-indexed evaluator numbers
    table_rows = []
    for entry in evaluator_responses:
        i = entry["index"]
        status = entry.get("status", "error")
        # find the parsed eval
        ev = next((e for e in evals if e["index"] == i), None)
        if status == "ok" and ev is not None:
            score_disp = str(ev["score"]) if ev["score"] is not None else "—"
            # passed according to per_eval
            pe = next((p for p in agg["per_eval"] if p["index"] == i), None)
            verdict_disp = ("PASS" if pe["passed"] else "FAIL") if pe is not None else "FAIL"
            struct_disp = "ok" if (ev["structured"] is not None) else "absent"
        else:
            score_disp = "—"
            verdict_disp = "—"
            struct_disp = "—"
        table_rows.append(
            f"| {i + 1} | {status} | {score_disp} | {verdict_disp} | {struct_disp} |"
        )

    table_str = "\n".join(table_rows)
    majority_verdict = "PASS" if agg["majority_passed"] else "FAIL"
    gate_result = "PASS" if (
        (n_valid >= 2 and agg["majority_passed"])
        or (n_valid == 1 and (
            next((e for e in evals if e["status"] == "ok" and
                  (e["score"] is not None or e["structured"] is not None)), None) is not None
            and _single_eval_passed(evals, threshold)
        ))
    ) else "FAIL"

    # Section per evaluator
    evaluator_sections = []
    for entry in evaluator_responses:
        i = entry["index"]
        status = entry.get("status", "error")
        if status == "ok":
            raw_i = entry.get("raw_response", "")
            evaluator_sections.append(f"## Evaluator {i + 1}\n{raw_i}")
        else:
            ec = entry.get("error_code", "unknown")
            evaluator_sections.append(f"## Evaluator {i + 1} — ERRORED ({ec})")

    composite_text = (
        f"# Multi-Evaluator Satisfaction Review (COMPLEX — {n_attempted} evaluators)\n\n"
        f"## Aggregation Summary\n"
        f"| Evaluator | Status | Score | Verdict | Structured |\n"
        f"|---|---|---|---|---|\n"
        f"{table_str}\n\n"
        f"- Valid evaluators: {n_valid} / {n_attempted}\n"
        f"- Median score: {agg['median_score']}  (threshold: {threshold})\n"
        f"- Majority verdict: {majority_verdict}  (agreement: {agg['agreement']})\n"
        f"- Gate result: {gate_result}\n"
        f"- AC checklist: {ac_checklist_summary}\n\n"
        "---\n" + "\n---\n".join(evaluator_sections) + "\n"
    )

    doc_path.parent.mkdir(parents=True, exist_ok=True)
    doc_path.write_text(composite_text, encoding="utf-8")
    satisfaction_bytes_written = len(composite_text.encode("utf-8"))

    # Telemetry
    if n_valid >= 2:
        _emit_safe(
            "satisfaction_multi_evaluator",
            {
                "phase": 6,
                "n_attempted": n_attempted,
                "n_valid": n_valid,
                "evaluators": [
                    {
                        "index": p["index"],
                        "status": p["status"],
                        "score": p["score"],
                        "structured_ok": p["structured_ok"],
                        "satisfied": p["satisfied"],
                        "passed": p["passed"],
                    }
                    for p in agg["per_eval"]
                ],
                "median_score": agg["median_score"],
                "majority_passed": agg["majority_passed"],
                "passed_count": agg["passed_count"],
                "agreement": agg["agreement"],
                "threshold": threshold,
                "degraded": False,
            },
        )
    else:
        reason = "no_valid_evaluators" if n_valid == 0 else "only_one_valid_evaluator"
        _emit_safe(
            "satisfaction_multi_evaluator_degraded",
            {
                "phase": 6,
                "n_attempted": len(evals),
                "n_valid": n_valid,
                "reason": reason,
            },
        )

    # Gate decision
    if n_valid >= 2:
        passed = agg["majority_passed"]
        score_out = agg["median_score"]
        fixes = agg["fixes_required"]
    elif n_valid == 1:
        # single-eval semantics on the surviving valid evaluator (9702B73F: use helper)
        survivor = next(
            e for e in evals
            if e["status"] == "ok" and (e["score"] is not None or e["structured"] is not None)
        )
        structured_s = survivor["structured"]
        score_s = survivor["score"]
        passed, reason_code_s = _decide_satisfaction_passed(structured_s, score_s, threshold)
        fixes = structured_s.fixes_required if structured_s is not None else []
        score_out = score_s
        # Emit drift event for surviving evaluator if drift detected
        if reason_code_s.startswith("drift_"):
            _emit_safe(
                "satisfaction_verdict_drift",
                {
                    "score": score_s,
                    "threshold": threshold,
                    "structured_satisfied": structured_s.satisfied,  # type: ignore[union-attr]
                    "gate_decision": "fail-closed",
                    "evaluator_index": survivor["index"],
                    "phase": 6,
                },
            )
    else:
        # n_valid == 0: fail-closed
        passed = False
        score_out = None
        fixes = []

    # GH388: majority checklist-fail forces the REAL gate decision to error — only
    # downgrades a would-be PASS (non-masking invariant, mirrors the single-eval path).
    ac_checklist_override = False
    if ac_majority_fail and passed:
        passed = False
        ac_checklist_override = True

    common_data: dict = {
        "satisfaction_doc_path": str(doc_path),
        "spec_path": prev.data["spec_path"],
        "review_doc_path": prev.data["review_doc_path"],
        "fix_doc_path": prev.data["fix_doc_path"],
        "satisfaction_bytes_written": satisfaction_bytes_written,
        "score": score_out,
        "threshold": threshold,
        "structured_verdict": None,
        "multi_evaluator": {
            "n_valid": agg["n_valid"],
            "median_score": agg["median_score"],
            "majority_passed": agg["majority_passed"],
            "agreement": agg["agreement"],
            "degraded": agg["degraded"],
            "passed_count": agg["passed_count"],
        },
    }

    # F81D5EF7: fail-loud when multi-eval is structurally degraded (n_valid < 2).
    # Closes issue #73 — verdict status="ok" silently masked degraded reviews.
    if agg["degraded"]:
        _degrade_reason = "no_valid_evaluators" if agg["n_valid"] == 0 else "only_one_valid_evaluator"
        return StepResult(
            status="error",
            data=common_data,
            duration_ms=0,
            step_name="write_satisfaction_doc",
            error=f"review degraded: {_degrade_reason} (n_valid={agg['n_valid']}, n_attempted={agg.get('n_attempted', len(evals))})",
            error_code="E_REVIEW_DEGRADED",
            recoverable=False,
        )

    if not passed:
        _persist_satisfaction_last_findings(ctx, prev, score_out, threshold)
        # GH388: majority checklist-fail on a would-be PASS gate — own error branch,
        # bypasses the score-based message ladder below (record_satisfaction_reject
        # scoped to this new flip branch only per §2.3 advisory 6).
        if ac_checklist_override:
            error_msg = (
                f"AC checklist gate failed — {_ac_n_fail}/{_ac_n_valid} valid evaluators "
                f"missing or failing the AC checklist (majority)"
            )
            record_satisfaction_reject("ac_checklist_fail", error_msg, score_out, threshold, axes_text=composite_text)
            return StepResult(
                status="error",
                data={**common_data, "invalidate_cycle_sentinels_on_fail": True},
                duration_ms=0,
                step_name="write_satisfaction_doc",
                error=error_msg,
                error_code="E_SATISFACTION_AC_CHECKLIST",
                recoverable=False,
            )
        # Build descriptive error message
        if n_valid >= 2:
            error_msg = (
                f"satisfaction multi-evaluator FAIL — median score {score_out} "
                f"(threshold {threshold}), agreement={agg['agreement']}, "
                f"majority_passed=False"
            )
        elif n_valid == 1:
            # Mirror single-eval error branches (9702B73F Note 4)
            if reason_code_s.startswith("drift_"):
                error_msg = (
                    f"satisfaction verdict drift — score {score_out} vs threshold {threshold}, "
                    f"structured.satisfied={structured_s.satisfied} "  # type: ignore[union-attr]
                    f"({len(structured_s.fixes_required)} fix(es) listed); fail-closed"  # type: ignore[union-attr]
                )
            elif reason_code_s == "structured_only_no_score":
                error_msg = (
                    f"satisfaction evaluator omitted SCORE — structured.satisfied="
                    f"{structured_s.satisfied} cannot override missing numeric verification; fail-closed"  # type: ignore[union-attr]
                )
            elif reason_code_s == "concurring_fail":
                error_msg = f"satisfaction not satisfied — {len(structured_s.fixes_required)} fix(es) required"  # type: ignore[union-attr]
            elif reason_code_s == "no_signals":
                error_msg = "satisfaction evaluator omitted SCORE and structured verdict — invalid evaluation"
            else:
                error_msg = f"satisfaction score {score_out} < threshold {threshold}"
        else:
            error_msg = (
                "satisfaction evaluator omitted SCORE and structured verdict — invalid evaluation"
            )
        return StepResult(
            status="error",
            data={**common_data, "invalidate_cycle_sentinels_on_fail": True},
            duration_ms=0,
            step_name="write_satisfaction_doc",
            error=error_msg,
            error_code="E_SATISFACTION_BELOW_THRESHOLD",
            recoverable=False,
        )
    return StepResult(
        status="ok",
        data=common_data,
        duration_ms=0,
        step_name="write_satisfaction_doc",
    )


def _single_eval_passed(evals: list, threshold: int) -> bool:
    """Helper: check if the single surviving valid evaluator passes.

    Used only for the composite doc gate_result string display.
    """
    survivor = next(
        (e for e in evals if e["status"] == "ok" and
         (e["score"] is not None or e["structured"] is not None)),
        None,
    )
    if survivor is None:
        return False
    if survivor["structured"] is not None:
        return bool(survivor["structured"].satisfied)
    return survivor["score"] is not None and survivor["score"] >= threshold


# ─── Step 12: CONTROL gate — mass-UNVERIFIED detector (agreement 5F9817F6) ───


_MASS_UNVERIFIED_THRESHOLD = 3


def _detect_mass_unverified(ctx, prev) -> StepResult:
    """Visibility-only CONTROL gate: emit alert if mass-UNVERIFIED + sat PASS.

    Last step in phase_6_review workflow — runs only when write_satisfaction_doc
    succeeded (failed satisfaction returns recoverable=False, aborting). So
    "satisfaction verdict == PASS" is implicit.

    Reads events.jsonl for current run_id, takes the LAST
    ``phase_6_unverified_count`` event (final retry's count). If count >=
    ``_MASS_UNVERIFIED_THRESHOLD``, emits ``review_aggregator_mass_unverified``.
    Always returns ok — never blocks the pipeline.
    """
    forwarded = dict(prev.data) if isinstance(getattr(prev, "data", None), dict) else {}

    run_ctx = telemetry_ctx.get_current_run()
    if run_ctx is None or run_ctx.event_log is None:
        return StepResult(
            status="ok", data=forwarded, duration_ms=0,
            step_name="detect_mass_unverified",
        )

    try:
        events = run_ctx.event_log.read_all()
    except (OSError, ValueError) as exc:
        logger.warning("detect_mass_unverified: read_all failed: %s", exc)
        return StepResult(
            status="ok", data=forwarded, duration_ms=0,
            step_name="detect_mass_unverified",
        )

    last_count: int | None = None
    for evt in events:
        if (
            evt.get("run_id") == run_ctx.run_id
            and evt.get("event_type") == "phase_6_unverified_count"
        ):
            payload = evt.get("payload") or {}
            try:
                last_count = int(payload.get("count", 0))
            except (TypeError, ValueError):
                continue

    if last_count is not None and last_count >= _MASS_UNVERIFIED_THRESHOLD:
        _emit_safe(
            "review_aggregator_mass_unverified",
            {
                "count": last_count,
                "threshold": _MASS_UNVERIFIED_THRESHOLD,
                "satisfaction_verdict": "PASS",
            },
        )

    return StepResult(
        status="ok",
        data={**forwarded, "mass_unverified_count": last_count or 0},
        duration_ms=0,
        step_name="detect_mass_unverified",
    )


# ─── 7547E02F: engine-owned FIX commit helpers (mirror 4C0056FA pattern) ─────

from bytedigger_engine.lib.util.path_classifier import (  # noqa: E402
    _fn,
    _TEST_FILENAME_PATTERNS,
    _TEST_PATH_SEGMENTS,
    _is_test_path,
)
from bytedigger_engine.config_provider import get_config  # noqa: E402  GH373 §2 Part A
from bytedigger_engine.lib import authored_boundary  # noqa: E402  GH373 §2 Part A


def _is_test_py_path(path: str) -> bool:
    """True if path is a pytest-convention test file (basename starts with ``test_`` + ``.py``).

    v1 scope: pytest ``test_*.py`` only. ``*_test.py`` (Go-style) convention
    and TS/bats expansion deferred to OFIs (7A940850).
    """
    if not path:
        return False
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    return _fn.fnmatch(name, "test_*.py")


def _is_synthetic_test_env(cfg: dict, git_cwd: str) -> bool:
    """True ⇒ no real git repo ⇒ skip commit/pytest steps (synthetic pytest tmp_path env).

    D8CB354F R5 project-mode correctness. Replaces the triplicated inline guard
    `not cfg.get("git_cwd") and scratchpad_dir and not ...startswith(scratchpad)`.
      - git_cwd explicitly set in org_config  → real project    → False (run).
      - git_cwd NOT explicit                  → probe `git rev-parse --git-dir`
        at git_cwd; synthetic iff no git repo (rev-parse fails / git missing / timeout).
    """
    if cfg.get("git_cwd"):
        return False
    try:
        r = git_port.git_read(["rev-parse", "--git-dir"], cwd=git_cwd, timeout=GIT_REV_PARSE_TIMEOUT_SEC)
        if r.timed_out:
            return True
        return r.returncode != 0
    except (FileNotFoundError, OSError):
        return True


def _venv_pytest(base: str) -> str | None:
    for cand in (
        Path(base) / ".venv" / "bin" / "pytest",
        Path(base) / "venv" / "bin" / "pytest",
    ):
        if cand.is_file() and os.access(cand, os.X_OK):
            return str(cand)
    return None


def _main_checkout_root(git_cwd: str) -> str | None:
    try:
        proc = git_port.git_read(
            ["rev-parse", "--git-common-dir"],
            dir_=git_cwd,
            timeout=5,
        )
        if proc.returncode == 124:
            return None
        if proc.returncode != 0:
            return None
        common = os.path.realpath(os.path.join(git_cwd, proc.stdout.strip()))
        root = os.path.dirname(common)
        if root != os.path.realpath(git_cwd):
            return root
        return None
    except (subprocess.CalledProcessError, FileNotFoundError, OSError, ValueError):
        return None


def _resolve_pytest_argv(git_cwd: str | None = None) -> list[str]:
    """R4-secondary: prefer <git_cwd>/.venv/bin/pytest (then venv/) if executable;
    else climb to the main checkout root (git-common-dir parent) and retry the
    same probe there before falling back to the system invocation. Mirrors
    phase_5 _runner_for_path venv probe (D8CB354F cycle 2, commit 88a4f2f8)
    plus BF7890C8's parent-checkout climb. --tb=short -q preserved verbatim
    from the prior hardcoded argv."""
    if git_cwd is not None:
        hit = _venv_pytest(git_cwd)
        if hit is None:
            root = _main_checkout_root(git_cwd)
            if root is not None:
                hit = _venv_pytest(root)
        if hit is not None:
            return [hit, "--tb=short", "-q"]
    return ["python3", "-m", "pytest", "--tb=short", "-q"]


def _build_fix_test_commit_message(cycle: int, test_paths: list[str]) -> str:
    """Construct the FIX-TEST-commit message (8FE3D757).

    Subject:
        fix(test): cycle <cycle> — phase_6 fix-worker test patches

    Body:
        One path per line (preserve order, no bullets)
    """
    body = "\n".join(test_paths)
    return f"fix(test): cycle {cycle} — phase_6 fix-worker test patches\n\n{body}\n"


# ─── 998C880F: clean-tree detector helpers (3F5599A6) ──────────────────────


def _count_working_tree_changes(git_cwd: str) -> int:
    """Line count of `git status --porcelain`; -1 on any git/OS failure. Never raises."""
    try:
        st = git_port.git_read(["status", "--porcelain"], cwd=git_cwd, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return -1
    if st.returncode != 0:
        return -1
    return len([ln for ln in st.stdout.splitlines() if ln])


def _assert_clean_tree(paths: list, git_cwd: str, step_name: str) -> bool:
    """998C880F detector. `git status --porcelain -- <paths>`: empty → True.
    Non-empty → emit fix_commit_dirty_residue {step, phase:6, n_dirty, dirty_paths
    (porcelain lines, capped 20)} and return False. Any git/OS failure → True
    (degraded-OK, logger.warning). NEVER raises, NEVER alters step control flow."""
    try:
        st = git_port.git_read(
            ["status", "--porcelain", "--", *paths], cwd=git_cwd, timeout=30
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("_assert_clean_tree: git status failed for step %s: %s", step_name, exc)
        return True
    if st.returncode != 0:
        logger.warning(
            "_assert_clean_tree: git status rc=%d for step %s: %s",
            st.returncode, step_name, st.stderr[:200],
        )
        return True
    dirty_lines = [ln for ln in st.stdout.splitlines() if ln]
    if not dirty_lines:
        return True
    _emit_safe(
        "fix_commit_dirty_residue",
        {
            "step": step_name,
            "phase": 6,
            "n_dirty": len(dirty_lines),
            "dirty_paths": dirty_lines[:20],
        },
    )
    return False


def _build_fix_commit_message(cycle: int, paths: list) -> str:
    """Construct the FIX-commit message.

    Subject:
        build: fix cycle <cycle>

    Body:
        Files: <basename1>, <basename2>, ... (first 3 + ...+M more when >5)
    """
    basenames = [Path(p).name for p in paths]
    if len(basenames) > 5:
        head = ", ".join(basenames[:3])
        body_files = f"Files: {head} ...+{len(basenames) - 3} more"
    else:
        body_files = "Files: " + ", ".join(basenames)
    return f"build: fix cycle {cycle}\n\n{body_files}\n"


def _autocommit_fix_tail(
    cfg: dict, git_cwd: str, git_cwd_source: str, cycle: int, pre_fix_sha: "str | None", step_name: str
) -> "StepResult | dict":
    """GH886 Change 1: auto-commit any dirty tail left after a fix-phase
    manifest commit, so the worktree never reaches the integrity gate dirty.

    Returns {"tail_committed": bool, ...} on no-op/success, or a
    StepResult(status="error", ...) when the tail cannot be safely committed
    (boundary violation / git-lock persisted / git failure).
    """
    try:
        st = git_port.git_read(["status", "--porcelain"], cwd=git_cwd, timeout=30)
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("_autocommit_fix_tail: git status failed for step %s: %s", step_name, exc)
        return {"tail_committed": False}
    if st.returncode != 0:
        logger.warning(
            "_autocommit_fix_tail: git status rc=%d for step %s: %s",
            st.returncode, step_name, st.stderr[:200],
        )
        return {"tail_committed": False}
    porcelain_lines = [ln for ln in st.stdout.splitlines() if ln]
    if not porcelain_lines:
        return {"tail_committed": False}

    # GH1220 B7: widened to every ambient-contaminated label; reason literal
    # "cwd_default" is pinned (AC14b, §1o) — unchanged so existing consumers
    # dispatching on it are unaffected.
    if is_ambient_git_cwd(git_cwd_source):
        _emit_safe("fix_tail_skipped", {"reason": "cwd_default", "phase": 6, "step": step_name})
        return {"tail_committed": False}

    tail_paths = [ln[3:].strip() for ln in porcelain_lines]
    tail_paths = _filter_gitignored_paths(tail_paths, git_cwd)
    if not tail_paths:
        return {"tail_committed": False}

    if get_config().gate_enabled("HAL_AUTHORED_BOUNDARY_GATE"):
        assert pre_fix_sha is not None
        try:
            scan_result = authored_boundary.scan_boundary(
                "fix_commit",
                base_sha=pre_fix_sha,
                paths=tail_paths,
                git_cwd=git_cwd,
                is_test_path=_is_test_path,
            )
        except RuntimeError as exc:
            return StepResult(
                status="error", data=None, duration_ms=0, step_name=step_name,
                error=f"authored-diff boundary scan failed on tail: {exc}",
                error_code="E_BOUNDARY_SCAN_FAILED",
                recoverable=False,
            )
        if scan_result.suppression_hits:
            return StepResult(
                status="error", data=None, duration_ms=0, step_name=step_name,
                error=f"authored-diff boundary scan found new suppression tokens in tail: {scan_result.suppression_hits!r}",
                error_code="E_BOUNDARY_SUPPRESSION",
                recoverable=False,
            )
        if scan_result.tampered_tests:
            return StepResult(
                status="error", data=None, duration_ms=0, step_name=step_name,
                error=f"authored-diff boundary scan found tampered RED test paths in tail: {scan_result.tampered_tests!r}",
                error_code="E_RED_TESTS_TAMPERED",
                recoverable=False,
            )

    add, add_outcome = _git_op_with_lock_retry(
        ["git", "add", "--"] + tail_paths, cwd=git_cwd, timeout=30
    )
    if add_outcome == "lock_persisted":
        return StepResult(
            status="error", data=None, duration_ms=0, step_name=step_name,
            error=f"git add (tail): index.lock contention persisted after 3 attempts: {add.stderr[:500]}",
            error_code="E_GIT_LOCKED",
        )
    if add_outcome == "non_lock_error":
        return StepResult(
            status="error", data=None, duration_ms=0, step_name=step_name,
            error=f"git add (tail): {add.stderr[:500]}",
            error_code="E_FIX_COMMIT_FAILED",
        )
    if add_outcome == "timeout":
        return StepResult(
            status="error", data=None, duration_ms=0, step_name=step_name,
            error="git add (tail): timeout after 30s",
            error_code="E_GIT_TIMEOUT",
        )
    if add_outcome == "os_error":
        return StepResult(
            status="error", data=None, duration_ms=0, step_name=step_name,
            error="git add (tail): OS error invoking subprocess",
            error_code="E_GIT_OS_ERROR",
        )

    subject = f"build: fix cycle {cycle} (auto-commit tail)"
    basenames = [Path(p).name for p in tail_paths]
    body_files = "Files: " + ", ".join(basenames)
    commit_message = f"{subject}\n\n{body_files}\n"
    cm, cm_outcome = _git_op_with_lock_retry(
        ["git", "commit", "-m", commit_message], cwd=git_cwd, timeout=30
    )
    if cm_outcome == "lock_persisted":
        return StepResult(
            status="error", data=None, duration_ms=0, step_name=step_name,
            error=f"git commit (tail): index.lock contention persisted after 3 attempts: {cm.stderr[:500]}",
            error_code="E_GIT_LOCKED",
        )
    if cm_outcome == "non_lock_error":
        return StepResult(
            status="error", data=None, duration_ms=0, step_name=step_name,
            error=f"git commit (tail): {cm.stderr[:500]}",
            error_code="E_FIX_COMMIT_FAILED",
        )
    if cm_outcome == "timeout":
        return StepResult(
            status="error", data=None, duration_ms=0, step_name=step_name,
            error="git commit (tail): timeout after 30s",
            error_code="E_GIT_TIMEOUT",
        )
    if cm_outcome == "os_error":
        return StepResult(
            status="error", data=None, duration_ms=0, step_name=step_name,
            error="git commit (tail): OS error invoking subprocess",
            error_code="E_GIT_OS_ERROR",
        )

    post_rev = git_port.git_read(["rev-parse", "HEAD"], cwd=git_cwd, timeout=30)
    if post_rev.returncode != 0:
        return StepResult(
            status="error", data=None, duration_ms=0, step_name=step_name,
            error=f"git rev-parse HEAD after tail commit: {post_rev.stderr[:500]}",
            error_code="E_FIX_COMMIT_FAILED",
        )
    tail_sha = post_rev.stdout.strip()

    cfg = cfg or {}
    scratchpad_dir = cfg.get("scratchpad_dir")
    if scratchpad_dir:
        _sidecar = Path(scratchpad_dir) / "integrity" / "fix-commit-sha.txt"
        _sidecar.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(_sidecar, tail_sha)

    _emit_safe(
        "fix_tail_autocommit",
        {
            "cycle": cycle,
            "n_files": len(tail_paths),
            "commit_sha": tail_sha,
            "phase": 6,
        },
    )
    return {"tail_committed": True, "commit_sha": tail_sha}


# GH947: fix-worker surface guard helpers
def _partition_fix_surface(
    prod_paths: list[str], pre_fix_sha: str, review_text: str, git_cwd: str
) -> tuple[list[str], list[str]]:
    """Split prod_paths into (allowed, violations).

    A path is a violation iff it did NOT exist at the pre-fix boundary
    (`git cat-file -e {pre_fix_sha}:{p}` rc != 0) AND its literal string does
    not occur in review_text. Repo-level driver failure (e.g. git_cwd is not
    a git repository) fails open: remaining paths are treated as allowed.
    """
    allowed: list[str] = []
    violations: list[str] = []
    for idx, p in enumerate(prod_paths):
        try:
            res = git_port.git_read(
                ["cat-file", "-e", f"{pre_fix_sha}:{p}"], cwd=git_cwd, timeout=30
            )
        except OSError:
            allowed.extend(prod_paths[idx:])
            return allowed, violations
        stderr = (res.stderr or "")
        if res.returncode == 0:
            allowed.append(p)
            continue
        if "not a git repository" in stderr.lower():
            allowed.extend(prod_paths[idx:])
            return allowed, violations
        if p in review_text:
            allowed.append(p)
        else:
            violations.append(p)
    return allowed, violations


def _drop_add_ignored_paths(
    stderr: str, prod_paths: list[str]
) -> tuple[list[str], list[str]]:
    """Split prod_paths into (retained, dropped) per a git-add ignored-paths
    stderr listing. A path is dropped iff some stripped non-empty, non-prose
    (no space) stderr line L matches it exactly or as a directory prefix
    (`p.startswith(L.rstrip("/") + "/")`)."""
    ignore_lines = [
        line.strip() for line in (stderr or "").splitlines()
        if line.strip() and " " not in line.strip()
    ]
    retained: list[str] = []
    dropped: list[str] = []
    for p in prod_paths:
        hit = any(
            p == line or p.startswith(line.rstrip("/") + "/")
            for line in ignore_lines
        )
        (dropped if hit else retained).append(p)
    return retained, dropped


def _commit_fix_code(ctx, prev) -> StepResult:
    """Step 6+: engine-authoritative FIX commit (7547E02F).

    Reads pre_fix_sha from prev.data as the SHA boundary (falls back to
    resolve_pre_phase_sha), enumerates production (non-test) paths via
    git_diff_files since that SHA, commits them, and emits a fix_commit
    telemetry event.
    """
    _pd_err = prev_data_corruption_reason(prev)
    if _pd_err is not None:
        return StepResult(status="error", data=None, duration_ms=0,
            step_name="commit_fix_code",
            error=f"manifest contract violation at consumer: {_pd_err}",
            error_code="E_LLM_MANIFEST_MISSING_AT_CONSUMER", recoverable=False)

    cfg = ctx.org_config or {}
    git_cwd, _git_cwd_source = resolve_git_cwd_with_source(cfg)
    git_cwd = str(git_cwd)
    scratchpad_dir = cfg.get("scratchpad_dir")

    # Test-env guard: skip commit when there is genuinely no git repo to act on.
    if _is_synthetic_test_env(cfg, git_cwd):
        _emit_safe("project_mode_skipped", {"step": "commit_fix_code", "reason": "no_git_repo", "phase": 6})
        _emit_safe(
            "fix_commit_skipped",
            {"reason": "no_git_repo", "phase": 6},
        )
        return StepResult(
            status="ok",
            data={**(prev.data or {}), "fix_commit_sha": None},
            duration_ms=0,
            step_name="commit_fix_code",
        )

    # ── AC2: SHA boundary resolution ─────────────────────────────────────────
    hex_chars = set("0123456789abcdef")

    def _is_valid_sha(sha) -> bool:
        return (
            sha
            and isinstance(sha, str)
            and len(sha) == 40
            and all(c in hex_chars for c in sha)
        )

    pre_fix_sha = (prev.data or {}).get("pre_fix_sha")
    if not _is_valid_sha(pre_fix_sha):
        # fall back to resolve_pre_phase_sha; tolerate non-git-repo by skipping cleanly
        try:
            pre_fix_sha = resolve_pre_phase_sha(git_cwd)
        except (RuntimeError, OSError) as exc:
            _emit_safe(
                "fix_commit_skipped",
                {"reason": "no_git_repo", "phase": 6, "err": str(exc)[:200]},
            )
            return StepResult(
                status="ok",
                data={**(prev.data or {}), "fix_commit_sha": None},
                duration_ms=0,
                step_name="commit_fix_code",
            )

    if not _is_valid_sha(pre_fix_sha):
        return StepResult(
            status="error",
            data=None,
            duration_ms=0,
            step_name="commit_fix_code",
            error=(
                f"pre_fix_sha missing or invalid in prev.data and fallback failed "
                f"(got {(prev.data or {}).get('pre_fix_sha')!r}); cannot determine FIX diff boundary"
            ),
            error_code="E_MISSING_FIX_BOUNDARY",
        )

    # DD34EEBF (hoisted 7B6A9AD1): persist pre-fix SHA before any skip path
    if scratchpad_dir:
        _pre_ref = Path(scratchpad_dir) / "integrity" / "pre-fix-ref.txt"
        _pre_ref.parent.mkdir(parents=True, exist_ok=True)
        assert pre_fix_sha is not None
        atomic_write(_pre_ref, pre_fix_sha)

    cycle = int((prev.data or {}).get("cycle", 1))

    # ── 4961254A: manifest-based commit (allowlist inversion) ────────────────
    # Commit exactly the paths the worker wrote — never the dirty tree.
    # Source: worker_written_paths from invoke_llm_subprocess via prev.data.
    # 4C03CCED Ship 1C G1-AC3: canonical accessor replaces legacy .get() pattern.
    try:
        manifest, _manifest_src = manifest_from_result(prev)
    except _ManifestError as e:
        return StepResult(
            status="error",
            data=None,
            duration_ms=0,
            step_name="commit_fix_code",
            error=f"manifest contract violation at consumer: {e}",
            error_code="E_LLM_MANIFEST_MISSING_AT_CONSUMER",
            recoverable=False,
        )
    prod_paths = _filter_gitignored_paths(
        [p for p in manifest if not _is_test_path(p)],
        git_cwd,
    )

    # AC13 (R-MEDIUM-1): telemeter manifest resolution BEFORE git add.
    _emit_safe(
        "commit_manifest_resolved",
        {
            "n_manifest": len(manifest),
            "n_committed": len(prod_paths),
            "step": "commit_fix_code",
            "phase": 6,
        },
    )

    if not prod_paths:
        # GH449 Change 1: before skipping, enumerate the dirty tree since the
        # SHA boundary — the worker may have applied fix edits that were
        # never self-reported into the manifest (e.g. commit-hook rejection
        # on a prior attempt left them uncommitted). Only when BOTH the
        # manifest AND the dirty-tree enumeration are empty do we skip.
        #
        # Amendment 1 (AC8, GH381 hazard): only trust the dirty-tree fallback
        # when git_cwd resolution came from an explicit source. When the
        # resolver defaulted to Path.cwd() (no cfg git_cwd / prev_data /
        # current_worktree_path / scratchpad_climb hit), never enumerate or
        # commit the ambient cwd — keep the plain skip.
        _fallback_paths: list[str] = []
        if _git_cwd_source != "cwd":
            _fallback_paths = _filter_gitignored_paths(
                [
                    p
                    for p in git_diff_files(pre_fix_sha, git_cwd, untracked=True, segment_filter=None)
                    if not _is_test_path(p)
                ],
                git_cwd,
            )
        if _fallback_paths:
            prod_paths = _fallback_paths
            _emit_safe(
                "fix_commit_manifest_fallback",
                {"n_fallback_paths": len(prod_paths), "phase": 6},
            )
        else:
            _emit_safe(
                "fix_commit_skipped",
                {
                    "reason": (
                        "empty_manifest_cwd_default"
                        if _git_cwd_source == "cwd"
                        else "empty_manifest"
                    ),
                    "phase": 6,
                },
            )
            return StepResult(
                status="ok",
                data={**(prev.data or {}), "fix_commit_sha": None},
                duration_ms=0,
                step_name="commit_fix_code",
            )

    # ── GH1220 B3: refuse an ambient git_cwd before the FIRST mutating op ────
    # (the GH947 surface guard's Path.unlink() below, not merely `git add`).
    if is_ambient_git_cwd(_git_cwd_source):
        return StepResult(
            status="error", data=None, duration_ms=0, step_name="commit_fix_code",
            error=f"git_cwd resolved from the ambient process CWD (source={_git_cwd_source!r}) — refusing to run `git add`/`git commit`",
            error_code="E_GIT_CWD_AMBIENT", recoverable=False,
        )

    # ── GH947: fix-worker surface guard ─────────────────────────────────────
    # A stray write outside the findings surface (not cited in the review doc,
    # not pre-existing at the pre-fix boundary) must be dropped/reverted rather
    # than silently committed. Fail-open when there is no review doc to check.
    _review_doc_path = (prev.data or {}).get("review_doc_path")
    _review_text = ""
    if _review_doc_path:
        try:
            _review_text = Path(_review_doc_path).read_text(encoding="utf-8")
        except OSError:
            _review_text = ""
    if not _review_text:
        _emit_safe(
            "fix_surface_guard_skipped",
            {"reason": "no_review_doc", "step": "commit_fix_code", "phase": 6},
        )
    else:
        assert isinstance(pre_fix_sha, str)  # GH947: narrowed by _is_valid_sha gate above
        _allowed, _violations = _partition_fix_surface(
            prod_paths, pre_fix_sha, _review_text, git_cwd
        )
        for _v in _violations:
            _deleted = False
            try:
                _v_path = Path(_v)
                if not _v_path.is_absolute() and ".." not in _v_path.parts:
                    _ls = git_port.git_read(["ls-files", "--", _v], cwd=git_cwd, timeout=30)
                    _tracked = bool((_ls.stdout or "").strip())
                    _full_path = Path(git_cwd) / _v_path
                    if not _tracked and _full_path.exists():
                        _full_path.unlink()
                        _deleted = True
            except OSError:
                _deleted = False
            _emit_safe(
                "fix_surface_violation",
                {"path": _v, "deleted": _deleted, "step": "commit_fix_code", "phase": 6},
            )
        prod_paths = _allowed
        if not prod_paths:
            _emit_safe("fix_commit_skipped", {"reason": "all_paths_off_surface", "phase": 6})
            return StepResult(
                status="ok",
                data={**(prev.data or {}), "fix_commit_sha": None},
                duration_ms=0,
                step_name="commit_fix_code",
            )

    # ── GH373 Part A: authored-diff boundary scan (before git add) ──────────
    if get_config().gate_enabled("HAL_AUTHORED_BOUNDARY_GATE"):
        assert pre_fix_sha is not None
        try:
            scan_result = authored_boundary.scan_boundary(
                "fix_commit",
                base_sha=pre_fix_sha,
                paths=prod_paths,
                git_cwd=git_cwd,
                is_test_path=_is_test_path,
            )
        except RuntimeError as exc:
            return StepResult(
                status="error", data=None, duration_ms=0, step_name="commit_fix_code",
                error=f"authored-diff boundary scan failed: {exc}",
                error_code="E_BOUNDARY_SCAN_FAILED",
                recoverable=False,
            )
        if scan_result.suppression_hits:
            return StepResult(
                status="error", data=None, duration_ms=0, step_name="commit_fix_code",
                error=f"authored-diff boundary scan found new suppression tokens: {scan_result.suppression_hits!r}",
                error_code="E_BOUNDARY_SUPPRESSION",
                recoverable=False,
            )
        if scan_result.tampered_tests:
            return StepResult(
                status="error", data=None, duration_ms=0, step_name="commit_fix_code",
                error=f"authored-diff boundary scan found tampered RED test paths: {scan_result.tampered_tests!r}",
                error_code="E_RED_TESTS_TAMPERED",
                recoverable=False,
            )
    else:
        _emit_safe("gate_disabled", {
            "gate": "HAL_AUTHORED_BOUNDARY_GATE",
            "step": "commit_fix_code",
            "reason": "env_kill_switch",
        })

    # ── AC4: git add ──────────────────────────────────────────────────────────
    add, add_outcome = _git_op_with_lock_retry(
        ["git", "add", "--"] + prod_paths, cwd=git_cwd, timeout=30
    )
    if add_outcome == "lock_persisted":
        return StepResult(
            status="error", data=None, duration_ms=0, step_name="commit_fix_code",
            error=f"git add: index.lock contention persisted after 3 attempts: {add.stderr[:500]}",
            error_code="E_GIT_LOCKED",
        )
    if add_outcome == "non_lock_error":
        # GH947: an ignored-path git-add failure is recoverable — drop the
        # ignored paths and retry ONCE rather than hard-failing the whole step.
        if "ignored by one of your .gitignore files" in (add.stderr or ""):
            _retained, _dropped = _drop_add_ignored_paths(add.stderr, prod_paths)
            _emit_safe(
                "commit_gitignored_paths_skipped",
                {"paths": sorted(_dropped), "step": "commit_fix_code", "phase": 6},
            )
            if not _retained:
                _emit_safe("fix_commit_skipped", {"reason": "all_paths_gitignored", "phase": 6})
                return StepResult(
                    status="ok",
                    data={**(prev.data or {}), "fix_commit_sha": None},
                    duration_ms=0,
                    step_name="commit_fix_code",
                )
            prod_paths = _retained
            add, add_outcome = _git_op_with_lock_retry(
                ["git", "add", "--"] + prod_paths, cwd=git_cwd, timeout=30
            )
            if add_outcome == "lock_persisted":
                return StepResult(
                    status="error", data=None, duration_ms=0, step_name="commit_fix_code",
                    error=f"git add: index.lock contention persisted after 3 attempts: {add.stderr[:500]}",
                    error_code="E_GIT_LOCKED",
                )
            if add_outcome == "non_lock_error":
                return StepResult(
                    status="error", data=None, duration_ms=0, step_name="commit_fix_code",
                    error=f"git add: {add.stderr[:500]}",
                    error_code="E_FIX_COMMIT_FAILED",
                )
            if add_outcome == "timeout":
                return StepResult(
                    status="error", data=None, duration_ms=0, step_name="commit_fix_code",
                    error="git add: timeout after 30s",
                    error_code="E_GIT_TIMEOUT",
                )
            if add_outcome == "os_error":
                return StepResult(
                    status="error", data=None, duration_ms=0, step_name="commit_fix_code",
                    error="git add: OS error invoking subprocess",
                    error_code="E_GIT_OS_ERROR",
                )
        else:
            return StepResult(
                status="error", data=None, duration_ms=0, step_name="commit_fix_code",
                error=f"git add: {add.stderr[:500]}",
                error_code="E_FIX_COMMIT_FAILED",
            )
    if add_outcome == "timeout":
        return StepResult(
            status="error", data=None, duration_ms=0, step_name="commit_fix_code",
            error="git add: timeout after 30s",
            error_code="E_GIT_TIMEOUT",
        )
    if add_outcome == "os_error":
        return StepResult(
            status="error", data=None, duration_ms=0, step_name="commit_fix_code",
            error="git add: OS error invoking subprocess",
            error_code="E_GIT_OS_ERROR",
        )

    # ── AC4: git commit ───────────────────────────────────────────────────────
    # 3F5599A6 noop guard (mirror phase_5_implement._commit_green_code L4100):
    # skip the commit block when the manifest prod paths have no staged diff —
    # a benign worker-noop or idempotent re-entry must not hard-fail the step.
    if _paths_have_staged_changes(git_cwd, prod_paths):
        commit_message = _build_fix_commit_message(cycle, prod_paths)
        cm, cm_outcome = _git_op_with_lock_retry(
            ["git", "commit", "-m", commit_message], cwd=git_cwd, timeout=30
        )
        if cm_outcome == "lock_persisted":
            return StepResult(
                status="error", data=None, duration_ms=0, step_name="commit_fix_code",
                error=f"git commit: index.lock contention persisted after 3 attempts: {cm.stderr[:500]}",
                error_code="E_GIT_LOCKED",
            )
        if cm_outcome == "non_lock_error":
            return StepResult(
                status="error", data=None, duration_ms=0, step_name="commit_fix_code",
                error=f"git commit: {cm.stderr[:500]}",
                error_code="E_FIX_COMMIT_FAILED",
            )
        if cm_outcome == "timeout":
            return StepResult(
                status="error", data=None, duration_ms=0, step_name="commit_fix_code",
                error="git commit: timeout after 30s",
                error_code="E_GIT_TIMEOUT",
            )
        if cm_outcome == "os_error":
            return StepResult(
                status="error", data=None, duration_ms=0, step_name="commit_fix_code",
                error="git commit: OS error invoking subprocess",
                error_code="E_GIT_OS_ERROR",
            )
    else:
        _emit_safe(
            "fix_commit_noop",
            {
                "step": "commit_fix_code",
                "phase": 6,
                "cycle": cycle,
                "n_manifest_paths": len(prod_paths),
                "n_working_tree_changes": _count_working_tree_changes(git_cwd),
            },
        )

    # ── capture post-commit HEAD ──────────────────────────────────────────────
    post_rev = git_port.git_read(["rev-parse", "HEAD"], cwd=git_cwd, timeout=30)
    if post_rev.returncode != 0:
        return StepResult(
            status="error", data=None, duration_ms=0, step_name="commit_fix_code",
            error=f"git rev-parse HEAD after commit: {post_rev.stderr[:500]}",
            error_code="E_FIX_COMMIT_FAILED",
        )
    fix_sha = post_rev.stdout.strip()

    # DD34EEBF: persist post-fix commit SHA so phase_6_fix_integrity can
    # read the boundary directly instead of querying git rev-parse HEAD.
    if scratchpad_dir:
        _post_ref = Path(scratchpad_dir) / "integrity" / "fix-commit-sha.txt"
        _post_ref.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(_post_ref, fix_sha)

    # 998C880F: clean-tree detector (observability-only, no control-flow impact).
    _assert_clean_tree(prod_paths, git_cwd, step_name="commit_fix_code")

    # ── AC4: emit fix_commit telemetry ────────────────────────────────────────
    _emit_safe(
        "fix_commit",
        {
            "paths": prod_paths,
            "commit_sha": fix_sha,
            "cycle": cycle,
            "n_files": len(prod_paths),
            "phase": 6,
        },
    )

    # ── AC6: return ok with **prev.data spread + fix_commit_sha ──────────────
    return StepResult(
        status="ok",
        data={**prev.data, "fix_commit_sha": fix_sha},
        duration_ms=0,
        step_name="commit_fix_code",
    )


def _commit_fix_tests(ctx, prev) -> StepResult:
    """Step 6+: engine-authoritative FIX-TEST commit (8FE3D757).

    Mirror of _commit_fix_code, but selects test paths from git_diff_files
    via _is_test_path. Skipped cleanly when there are no test paths.
    """
    _pd_err = prev_data_corruption_reason(prev)
    if _pd_err is not None:
        return StepResult(status="error", data=None, duration_ms=0,
            step_name="commit_fix_tests",
            error=f"manifest contract violation at consumer: {_pd_err}",
            error_code="E_LLM_MANIFEST_MISSING_AT_CONSUMER", recoverable=False)

    cfg = ctx.org_config or {}
    git_cwd, _git_cwd_source = resolve_git_cwd_with_source(cfg)
    git_cwd = str(git_cwd)
    scratchpad_dir = cfg.get("scratchpad_dir")

    # Test-env guard: skip commit when there is genuinely no git repo to act on.
    if _is_synthetic_test_env(cfg, git_cwd):
        _emit_safe("project_mode_skipped", {"step": "commit_fix_tests", "reason": "no_git_repo", "phase": 6})
        _emit_safe(
            "fix_test_commit_skipped",
            {"reason": "no_git_repo", "phase": 6},
        )
        return StepResult(
            status="ok",
            data={**(prev.data or {}), "fix_test_commit_sha": None},
            duration_ms=0,
            step_name="commit_fix_tests",
        )

    # ── SHA boundary resolution ───────────────────────────────────────────────
    hex_chars = set("0123456789abcdef")

    def _is_valid_sha(sha) -> bool:
        return (
            sha
            and isinstance(sha, str)
            and len(sha) == 40
            and all(c in hex_chars for c in sha)
        )

    pre_fix_sha = (prev.data or {}).get("pre_fix_sha")
    if not _is_valid_sha(pre_fix_sha):
        # fall back to resolve_pre_phase_sha; if that also fails, error out
        try:
            pre_fix_sha = resolve_pre_phase_sha(git_cwd)
        except (RuntimeError, OSError) as exc:
            return StepResult(
                status="error",
                data=None,
                duration_ms=0,
                step_name="commit_fix_tests",
                error=(
                    f"pre_fix_sha missing or invalid in prev.data and fallback failed "
                    f"({exc!r}); cannot determine FIX-TEST diff boundary"
                ),
                error_code="E_MISSING_FIX_BOUNDARY",
            )

    if not _is_valid_sha(pre_fix_sha):
        return StepResult(
            status="error",
            data=None,
            duration_ms=0,
            step_name="commit_fix_tests",
            error=(
                f"pre_fix_sha missing or invalid in prev.data and fallback failed "
                f"(got {(prev.data or {}).get('pre_fix_sha')!r}); cannot determine FIX-TEST diff boundary"
            ),
            error_code="E_MISSING_FIX_BOUNDARY",
        )

    cycle = int((prev.data or {}).get("cycle", 1))

    # ── 4961254A: manifest-based commit (allowlist inversion) ────────────────
    # Commit exactly the test paths the worker wrote — from the manifest.
    # 4C03CCED Ship 1C G1-AC3: canonical accessor replaces legacy .get() pattern.
    try:
        manifest, _manifest_src = manifest_from_result(prev)
    except _ManifestError as e:
        return StepResult(
            status="error",
            data=None,
            duration_ms=0,
            step_name="commit_fix_tests",
            error=f"manifest contract violation at consumer: {e}",
            error_code="E_LLM_MANIFEST_MISSING_AT_CONSUMER",
            recoverable=False,
        )
    test_paths = [p for p in manifest if _is_test_path(p)]

    # AC13 (R-MEDIUM-1): telemeter manifest resolution BEFORE git add.
    _emit_safe(
        "commit_manifest_resolved",
        {
            "n_manifest": len(manifest),
            "n_committed": len(test_paths),
            "step": "commit_fix_tests",
            "phase": 6,
        },
    )

    if not test_paths:
        _emit_safe(
            "fix_test_commit_skipped",
            {"reason": "no_test_paths", "phase": 6},
        )
        return StepResult(
            status="ok",
            data={**prev.data, "fix_test_commit_sha": None},
            duration_ms=0,
            step_name="commit_fix_tests",
        )

    # ── GH1220 B4: refuse an ambient git_cwd before the first mutating op ────
    if is_ambient_git_cwd(_git_cwd_source):
        return StepResult(
            status="error", data=None, duration_ms=0, step_name="commit_fix_tests",
            error=f"git_cwd resolved from the ambient process CWD (source={_git_cwd_source!r}) — refusing to run `git add`/`git commit`",
            error_code="E_GIT_CWD_AMBIENT", recoverable=False,
        )

    # ── git add ───────────────────────────────────────────────────────────────
    add, add_outcome = _git_op_with_lock_retry(
        ["git", "add", "--"] + test_paths, cwd=git_cwd, timeout=30
    )
    if add_outcome == "lock_persisted":
        return StepResult(
            status="error", data=None, duration_ms=0, step_name="commit_fix_tests",
            error=f"git add: index.lock contention persisted after 3 attempts: {add.stderr[:500]}",
            error_code="E_GIT_LOCKED",
        )
    if add_outcome == "non_lock_error":
        return StepResult(
            status="error", data=None, duration_ms=0, step_name="commit_fix_tests",
            error=f"git add: {add.stderr[:500]}",
            error_code="E_FIX_TEST_COMMIT_FAILED",
        )
    if add_outcome == "timeout":
        return StepResult(
            status="error", data=None, duration_ms=0, step_name="commit_fix_tests",
            error="git add: timeout after 30s",
            error_code="E_GIT_TIMEOUT",
        )
    if add_outcome == "os_error":
        return StepResult(
            status="error", data=None, duration_ms=0, step_name="commit_fix_tests",
            error="git add: OS error invoking subprocess",
            error_code="E_GIT_OS_ERROR",
        )

    # ── git commit ────────────────────────────────────────────────────────────
    # 3F5599A6 noop guard (mirror _commit_fix_code / phase_5_implement L4100).
    if _paths_have_staged_changes(git_cwd, test_paths):
        commit_message = _build_fix_test_commit_message(cycle, test_paths)
        cm, cm_outcome = _git_op_with_lock_retry(
            ["git", "commit", "-m", commit_message], cwd=git_cwd, timeout=30
        )
        if cm_outcome == "lock_persisted":
            return StepResult(
                status="error", data=None, duration_ms=0, step_name="commit_fix_tests",
                error=f"git commit: index.lock contention persisted after 3 attempts: {cm.stderr[:500]}",
                error_code="E_GIT_LOCKED",
            )
        if cm_outcome == "non_lock_error":
            return StepResult(
                status="error", data=None, duration_ms=0, step_name="commit_fix_tests",
                error=f"git commit: {cm.stderr[:500]}",
                error_code="E_FIX_TEST_COMMIT_FAILED",
            )
        if cm_outcome == "timeout":
            return StepResult(
                status="error", data=None, duration_ms=0, step_name="commit_fix_tests",
                error="git commit: timeout after 30s",
                error_code="E_GIT_TIMEOUT",
            )
        if cm_outcome == "os_error":
            return StepResult(
                status="error", data=None, duration_ms=0, step_name="commit_fix_tests",
                error="git commit: OS error invoking subprocess",
                error_code="E_GIT_OS_ERROR",
            )
    else:
        _emit_safe(
            "fix_commit_noop",
            {
                "step": "commit_fix_tests",
                "phase": 6,
                "cycle": cycle,
                "n_manifest_paths": len(test_paths),
                "n_working_tree_changes": _count_working_tree_changes(git_cwd),
            },
        )

    # ── capture post-commit HEAD ──────────────────────────────────────────────
    post_rev = git_port.git_read(["rev-parse", "HEAD"], cwd=git_cwd, timeout=30)
    if post_rev.returncode != 0:
        return StepResult(
            status="error", data=None, duration_ms=0, step_name="commit_fix_tests",
            error=f"git rev-parse HEAD after test commit: {post_rev.stderr[:500]}",
            error_code="E_FIX_TEST_COMMIT_FAILED",
        )
    fix_test_sha = post_rev.stdout.strip()

    # Persist post-fix-test commit SHA to integrity directory
    if scratchpad_dir:
        _post_ref = Path(scratchpad_dir) / "integrity" / "fix-test-commit-sha.txt"
        _post_ref.parent.mkdir(parents=True, exist_ok=True)
        atomic_write(_post_ref, fix_test_sha)

    # 998C880F: clean-tree detector (observability-only, no control-flow impact).
    _assert_clean_tree(test_paths, git_cwd, step_name="commit_fix_tests")

    # ── emit fix_test_commit telemetry ────────────────────────────────────────
    _emit_safe(
        "fix_test_commit",
        {
            "paths": test_paths,
            "commit_sha": fix_test_sha,
            "cycle": cycle,
            "n_files": len(test_paths),
            "phase": 6,
        },
    )

    # GH886 Change 1: auto-commit any dirty tail left behind by the fix
    # worker after its own manifest commit (before returning ok).
    tail_result = _autocommit_fix_tail(
        cfg, git_cwd, _git_cwd_source, cycle, pre_fix_sha, "commit_fix_tests"
    )
    if isinstance(tail_result, StepResult):
        return tail_result

    # ── return ok with **prev.data spread + fix_test_commit_sha ──────────────
    return StepResult(
        status="ok",
        data={**prev.data, "fix_test_commit_sha": fix_test_sha},
        duration_ms=0,
        step_name="commit_fix_tests",
    )


# ─── GH316: injectable git-write seam — hoisted to phase_workflows_common
# (D52228C3 §2.9); re-exported at module scope above so the bare-name call
# sites below and the RED monkeypatches keep resolving the module attribute.


# ─── GH316: post-fix mypy typecheck delta gate ───────────────────────────────


def _verify_fix_typecheck(ctx, prev) -> StepResult:  # noqa: C901
    """Deterministic post-fix mypy typecheck delta gate (GH316).

    Runs after run_pytest_post_fix.  Computes the mypy error delta between
    pre_fix_sha (baseline worktree) and HEAD (current), blocks on net-new errors
    when enforce=True.  Mirrors phase_5._verify_green_typecheck skip-discipline
    + phase_8._compute_full_suite_baseline worktree-baseline pattern.
    """
    import fnmatch as _fnmatch
    cfg = ctx.org_config or {}
    git_cwd, git_cwd_source = resolve_git_cwd_with_source(cfg)
    git_cwd = str(git_cwd)
    step_name = "verify_fix_typecheck"

    # ── 1. Synthetic-env guard ────────────────────────────────────────────────
    if _is_synthetic_test_env(cfg, git_cwd):
        _emit_safe(
            "post_fix_typecheck_skipped",
            {"reason": "no_git_repo", "phase": 6, "step": step_name},
        )
        return StepResult(status="ok", data=dict(prev.data or {}), duration_ms=0, step_name=step_name)

    # ── 2. SHA boundary resolution ────────────────────────────────────────────
    pre_fix_sha = (prev.data or {}).get("pre_fix_sha")
    if not pre_fix_sha:
        try:
            pre_fix_sha = resolve_pre_phase_sha(git_cwd)
        except (RuntimeError, OSError):
            _emit_safe(
                "post_fix_typecheck_skipped",
                {"reason": "no_sha_boundary", "phase": 6, "step": step_name},
            )
            return StepResult(status="ok", data=dict(prev.data or {}), duration_ms=0, step_name=step_name)

    # ── 3. Production .py scope resolution ───────────────────────────────────
    _TEST_PATTERNS = ("test_*.py", "*_test.py")
    _TEST_SEGMENTS = ("tests/", "__tests__/")
    all_changed = git_diff_files(pre_fix_sha, git_cwd, untracked=True)
    prod_paths: list[str] = []
    for p in all_changed:
        if not p.endswith(".py"):
            continue
        filename = p.split("/")[-1] if "/" in p else p
        normalized = p.replace("\\", "/")
        is_test = any(_fnmatch.fnmatch(filename, pat) for pat in _TEST_PATTERNS)
        if not is_test:
            is_test = any(seg in normalized for seg in _TEST_SEGMENTS)
        if not is_test:
            prod_paths.append(p)

    if not prod_paths:
        _emit_safe(
            "post_fix_typecheck_skipped",
            {"reason": "no_python_scope", "phase": 6, "step": step_name},
        )
        return StepResult(status="ok", data=dict(prev.data or {}), duration_ms=0, step_name=step_name)

    # ── 4. Boundary check and path resolution ─────────────────────────────────
    git_cwd_resolved = Path(git_cwd).resolve()
    resolved_paths: list[str] = []
    for p in prod_paths:
        resolved = (Path(git_cwd) / p).resolve()
        try:
            resolved.relative_to(git_cwd_resolved)
        except ValueError:
            return StepResult(
                status="error",
                data=dict(prev.data or {}),
                duration_ms=0,
                step_name=step_name,
                error=f"prod path escapes git_cwd: {p!r}",
                error_code="E_POST_FIX_TYPECHECK_PATH_ESCAPE",
                recoverable=False,
            )
        resolved_paths.append(str(resolved))

    # ── 5. Current mypy count ─────────────────────────────────────────────────
    try:
        proc = bounded_run(
            _mypy_base_argv_p6(git_cwd) + resolved_paths,
            cwd=git_cwd,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except FileNotFoundError:
        _emit_safe(
            "post_fix_typecheck_skipped",
            {"reason": "mypy_missing", "phase": 6, "step": step_name},
        )
        return StepResult(status="ok", data=dict(prev.data or {}), duration_ms=0, step_name=step_name)

    if proc.returncode == 124:
        _emit_safe(
            "post_fix_typecheck_skipped",
            {"reason": "timeout", "phase": 6, "step": step_name},
        )
        return StepResult(status="ok", data=dict(prev.data or {}), duration_ms=0, step_name=step_name)

    current_findings = _parse_mypy_output_p6((proc.stdout or "") + "\n" + (proc.stderr or ""))
    current_count = len(current_findings)

    # ── 6. Baseline count via detached worktree at pre_fix_sha ───────────────
    # GH1220 B10: refuse an ambient git_cwd before `git worktree add --detach`
    # — skip the worktree-based baseline scan entirely, falling through to
    # step 7 with baseline_count=None (reuses the step's existing "typecheck
    # unavailable" degrade; no new StepResult shape needed).
    baseline_count: int | None = None
    if is_ambient_git_cwd(git_cwd_source):
        _emit_safe("fix_typecheck_skipped_ambient_cwd", {
            "phase": 6, "step": step_name, "source": git_cwd_source,
        })
    else:
        parent = tempfile.mkdtemp(prefix="tc_baseline_")
        wt = os.path.join(parent, "wt")
        worktree_added = False
        try:
            rc_wt, _, _ = _git_write(["worktree", "add", "--detach", wt, pre_fix_sha], Path(git_cwd))
            if rc_wt == 0:
                worktree_added = True
                # Map resolved paths into the worktree
                wt_paths: list[str] = []
                for rp in resolved_paths:
                    try:
                        rel = Path(rp).relative_to(git_cwd_resolved)
                        wt_paths.append(str(Path(wt) / rel))
                    except ValueError:
                        pass  # skip unmappable paths
                try:
                    bl_proc = bounded_run(
                        _mypy_base_argv_p6(wt) + wt_paths,
                        cwd=wt,
                        capture_output=True,
                        text=True,
                        timeout=60,
                    )
                    if bl_proc.returncode != 124:
                        baseline_count = len(
                            _parse_mypy_output_p6(
                                (bl_proc.stdout or "") + "\n" + (bl_proc.stderr or "")
                            )
                        )
                except Exception:
                    baseline_count = None
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

    # ── 7. Delta verdict and emit ─────────────────────────────────────────────
    enforce_flag = bool(cfg.get("post_fix_typecheck_delta_enforce", True))
    verdict = delta_verdict(baseline_count, current_count, enforce=enforce_flag)
    _emit_safe(
        "verify_fix_typecheck_delta_verdict",
        {
            "baseline_failed": verdict.baseline_failed,
            "current_failed": verdict.current_failed,
            "net_new": verdict.net_new,
            "classification": verdict.classification,
            "enforced": enforce_flag,
            "phase": 6,
        },
        severity="warning",
    )

    if verdict.would_block:
        return StepResult(
            status="error",
            data=dict(prev.data or {}),
            duration_ms=0,
            step_name=step_name,
            error=(
                f"post-fix mypy regression: {verdict.net_new} net-new error(s) "
                f"(baseline={verdict.baseline_failed}, current={verdict.current_failed})"
            ),
            error_code="E_POST_FIX_TYPECHECK_REGRESSION",
            recoverable=True,
        )

    return StepResult(status="ok", data=dict(prev.data or {}), duration_ms=0, step_name=step_name)


# ─── GH379: Class 5 decorrelated verifier (advisory→enforce, agreement 769EFDA3) ─

DECORR_DOC_RELPATH = "reviews/build-decorr-verify.md"
_DECORR_VERDICT_MARKERS = [("DECORR VERDICT: CLEAR", "CLEAR"), ("DECORR VERDICT: SUSPECT", "SUSPECT")]


def _build_decorr_prompt(ctx, prev) -> StepResult:
    """Step: build the adversarial-refute prompt for the decorrelated verifier.

    SIMPLE tier skips (ratified scope); FEATURE/COMPLEX both proceed. References
    artifacts by path only — never inlines file bodies (Q8 token lesson).
    """
    _prev_data = prev.data if isinstance(prev, StepResult) and isinstance(prev.data, dict) else {}
    complexity = _resolve_complexity(ctx)
    if complexity == "SIMPLE":
        _emit_safe("decorr_verify_skipped", {"complexity": complexity})
        return StepResult(
            status="ok",
            data={**_prev_data, "decorr_skipped": "simple_tier"},
            duration_ms=0, step_name="build_decorr_prompt",
        )

    scratchpad = _resolve_scratchpad(ctx)
    spec_path = scratchpad / SPEC_DOC_RELPATH
    review_path = scratchpad / REVIEW_DOC_RELPATH
    fix_path = scratchpad / FIX_DOC_RELPATH
    green_log_path = scratchpad / GREEN_LOG_RELPATH

    parts: list[str] = []
    role = _maybe_role_template(ctx)
    if role:
        parts.append(role.rstrip())
        parts.append("")
    parts.append(
        "ROLE: You are the DECORRELATED VERIFIER — a second, independent pass "
        "whose job is to REFUTE this ship, not confirm it. Assume the prior "
        "review and fix passes already missed something. Hunt for bypasses, "
        "self-exemptions, and fail-open edges in the FINAL authored state."
    )
    parts.append("")
    parts.append("READ_FIRST (paths only — open and read these yourself; do NOT trust summaries):")
    parts.append(f"  - Spec: {spec_path}")
    parts.append(f"  - Review doc: {review_path}")
    parts.append(f"  - Fix doc: {fix_path}")
    parts.append(f"  - GREEN diff ref: {green_log_path}")
    parts.append("")
    parts.append(
        "Actively try to REFUTE the ship. Do not default to CLEAR to keep the "
        "pipeline moving."
    )
    parts.append("")
    parts.append(
        "End your reply with exactly one final line, verbatim, one of:\n"
        "DECORR VERDICT: CLEAR\n"
        "DECORR VERDICT: SUSPECT"
    )
    prompt = "\n".join(parts)
    return StepResult(status="ok", data={**_prev_data, "prompt": prompt}, duration_ms=0, step_name="build_decorr_prompt")


def _invoke_decorr_llm(ctx, prev) -> StepResult:
    """Step: dispatch the decorrelated-verifier subprocess.

    §1n OWN: this step owns all subprocess failure modes — it never returns
    status="error" itself. Failures degrade to data={"decorr_error": ..., "stdout": ""}
    so the chain-terminal `error` status is reserved for `_write_decorr_artifact`'s
    single enforce-mode decision point (§1g single source).
    """
    if not isinstance(prev, StepResult) or not isinstance(prev.data, dict):
        return StepResult(
            status="error", data=None, duration_ms=0,
            step_name="invoke_decorr_llm",
            error="prev step did not produce a prompt",
            error_code="E_MISSING_PREV_DATA",
        )
    if prev.data.get("decorr_skipped"):
        return StepResult(status="ok", data=dict(prev.data), duration_ms=0, step_name="invoke_decorr_llm")

    cfg = getattr(ctx, "org_config", None) or {}
    result = invoke_llm_subprocess(
        prompt=prev.data.get("prompt", ""),
        model=get_claude_decorrelated_verifier(),
        timeout_sec=_resolve_review_timeout_sec(cfg),
        step_name="invoke_decorr_llm",
        hard_gate=False,
    )
    if result.status != "ok" or not isinstance(result.data, dict):
        error_code = result.error_code or "E_DECORR_INVOKE_FAILED"
        _emit_safe("decorr_verify_invoke_failed", {"error_code": error_code, "error": result.error})
        return StepResult(
            status="ok",
            data={**prev.data, "decorr_error": error_code, "stdout": ""},
            duration_ms=0, step_name="invoke_decorr_llm",
        )

    return StepResult(
        status="ok",
        data={**prev.data, "stdout": result.data.get("raw_response", "")},
        duration_ms=0, step_name="invoke_decorr_llm",
    )


def _write_decorr_artifact(ctx, prev) -> StepResult:
    """Step: persist decorrelated-verifier stdout, resolve verdict, gate on enforce.

    Absent stdout, unparseable marker, or an upstream `decorr_error` all
    resolve to the conservative SUSPECT verdict (Class-1 conservative default).
    Emits exactly one `decorr_verify_verdict` event per non-skipped run,
    including on the enforce-mode error-terminal branch (emit-before-return).
    """
    if not isinstance(prev, StepResult) or not isinstance(prev.data, dict):
        return StepResult(
            status="error", data=None, duration_ms=0,
            step_name="write_decorr_artifact",
            error="prev step did not produce stdout",
            error_code="E_MISSING_PREV_DATA",
        )
    if prev.data.get("decorr_skipped"):
        return StepResult(status="ok", data=dict(prev.data), duration_ms=0, step_name="write_decorr_artifact")

    stdout = prev.data.get("stdout") or ""
    decorr_error = prev.data.get("decorr_error")

    parse_source = "marker"
    if decorr_error or not stdout:
        verdict = VERDICT_SUSPECT
        parse_source = "conservative_default"
    else:
        verdict = last_line_anchored_marker(stdout, _DECORR_VERDICT_MARKERS, None)
        if verdict is None:
            verdict = VERDICT_SUSPECT
            parse_source = "conservative_default"

    if stdout:
        scratchpad = _resolve_scratchpad(ctx)
        doc_path = scratchpad / DECORR_DOC_RELPATH
        doc_path.parent.mkdir(parents=True, exist_ok=True)
        doc_path.write_text(stdout, encoding="utf-8")

    model = get_claude_decorrelated_verifier()
    # GH379 advisory rollout — agreement 769EFDA3; GH392 re-scope: flip on >=5 real
    # decorr_verify_verdict emissions (FEATURE/COMPLEX builds) reviewed clean, NOT
    # calendar date. flip-by:2026-08-01 (backstop re-review if still zero emissions)
    enforce = bool(ctx.org_config.get("decorrelated_verify_enforce"))

    _emit_safe("decorr_verify_verdict", {
        "verdict": verdict,
        "model": model,
        "enforce": enforce,
        "parse_source": parse_source,
    })

    if verdict == VERDICT_SUSPECT and enforce:
        return StepResult(
            status="error", data=dict(prev.data), duration_ms=0,
            step_name="write_decorr_artifact",
            error="decorrelated verifier flagged SUSPECT under enforce mode",
            error_code="E_DECORR_VERIFY_SUSPECT",
            recoverable=False,
        )

    return StepResult(status="ok", data=dict(prev.data), duration_ms=0, step_name="write_decorr_artifact")


def _run_pytest_post_fix(ctx, prev) -> StepResult:
    """Step 6+: deterministic post-fix sibling-regression gate (7A940850).

    Runs pytest over the test files touched since pre_fix_sha, fails closed if
    any tests are still red after the fix-worker's patch.  No -x: we want ALL
    sibling failures, not just the first.
    """
    import time as _time
    _pd_err = prev_data_corruption_reason(prev)
    if _pd_err is not None:
        return StepResult(status="error", data=None, duration_ms=0,
            step_name="run_pytest_post_fix",
            error=f"manifest contract violation at consumer: {_pd_err}",
            error_code="E_LLM_MANIFEST_MISSING_AT_CONSUMER", recoverable=False)

    cfg = ctx.org_config or {}
    git_cwd = str(resolve_git_cwd(cfg))
    scratchpad_dir = cfg.get("scratchpad_dir")

    # Test-env guard: skip pytest when there is genuinely no git repo to act on.
    if _is_synthetic_test_env(cfg, git_cwd):
        _emit_safe("project_mode_skipped", {"step": "run_pytest_post_fix", "reason": "no_git_repo", "phase": 6})
        _emit_safe(
            "post_fix_pytest_skipped",
            {"reason": "no_git_repo", "phase": 6, "step": "run_pytest_post_fix"},
        )
        return StepResult(
            status="ok",
            data=dict(prev.data or {}),
            duration_ms=0,
            step_name="run_pytest_post_fix",
        )

    # ── SHA boundary resolution ───────────────────────────────────────────────
    pre_fix_sha = (prev.data or {}).get("pre_fix_sha")
    if not pre_fix_sha:
        try:
            pre_fix_sha = resolve_pre_phase_sha(git_cwd)
        except (RuntimeError, OSError):
            _emit_safe("post_fix_pytest_skipped", {"reason": "no_sha_boundary", "phase": 6, "step": "run_pytest_post_fix"})
            return StepResult(
                status="ok",
                data=dict(prev.data or {}),
                duration_ms=0,
                step_name="run_pytest_post_fix",
            )

    # ── Test scope resolution (priority: red_test_paths > manifest) ──────────
    # 4961254A: git_diff_files fallback removed — commit-steps now own manifest;
    # scope follows the same bounded manifest to prevent ambient dirt from slipping in.
    red_test_paths = (prev.data or {}).get("red_test_paths")
    if red_test_paths is not None:
        # red_test_paths channel is present — filter to pytest files, short-circuit manifest
        py_test_paths = [p for p in red_test_paths if _is_test_py_path(p)]
    else:
        # Fallback: manifest filtered to pytest test files (no git_diff_files call)
        # 4C03CCED Ship 1C G1-AC3: canonical accessor replaces legacy .get() pattern.
        try:
            manifest, _manifest_src = manifest_from_result(prev)
        except _ManifestError as e:
            return StepResult(
                status="error",
                data=None,
                duration_ms=0,
                step_name="run_pytest_post_fix",
                error=f"manifest contract violation at consumer: {e}",
                error_code="E_LLM_MANIFEST_MISSING_AT_CONSUMER",
                recoverable=False,
            )
        py_test_paths = [p for p in manifest if _is_test_py_path(p)]

    if not py_test_paths:
        _emit_safe("post_fix_pytest_skipped", {"reason": "no_test_scope", "phase": 6, "step": "run_pytest_post_fix"})
        return StepResult(
            status="ok",
            data=dict(prev.data or {}),
            duration_ms=0,
            step_name="run_pytest_post_fix",
        )

    # ── Invoke pytest ─────────────────────────────────────────────────────────
    argv = _resolve_pytest_argv(git_cwd) + py_test_paths
    t0 = _time.monotonic()
    try:
        result = run_test_command(argv, git_cwd, timeout=POST_FIX_PYTEST_TIMEOUT_SEC)
    except FileNotFoundError:
        _emit_safe("post_fix_pytest_infra_error", {"reason": "pytest_missing", "phase": 6, "step": "run_pytest_post_fix"})
        return StepResult(
            status="error",
            data=None,
            duration_ms=0,
            step_name="run_pytest_post_fix",
            error="pytest binary not found",
            error_code="E_POST_FIX_PYTEST_INFRA",
            recoverable=False,
        )
    except subprocess.TimeoutExpired:
        _emit_safe("post_fix_pytest_infra_error", {"reason": "timeout", "phase": 6, "step": "run_pytest_post_fix"})
        return StepResult(
            status="error",
            data=None,
            duration_ms=0,
            step_name="run_pytest_post_fix",
            error=f"pytest timed out after {POST_FIX_PYTEST_TIMEOUT_SEC}s",
            error_code="E_POST_FIX_PYTEST_INFRA",
            recoverable=False,
        )
    duration_ms = int((_time.monotonic() - t0) * 1000)

    # ── Branch on outcome ─────────────────────────────────────────────────────
    if result.exit_code == 0:
        # PASS branch
        _emit_safe(
            "post_fix_pytest_pass",
            {
                "n_passed": result.n_passed,
                "n_test_paths": len(py_test_paths),
                "duration_ms": duration_ms,
                "phase": 6,
                "step": "run_pytest_post_fix",
            },
        )
        return StepResult(
            status="ok",
            data={
                **prev.data,
                "post_fix_pytest": {"passed": True, "n_passed": result.n_passed, "n_failed": 0},
            },
            duration_ms=duration_ms,
            step_name="run_pytest_post_fix",
        )

    # exit_code != 0
    if result.n_failed > 0:
        # REGRESSIONS branch
        # Parse failing test nodeids from stdout file
        stdout_text = ""
        try:
            stdout_text = Path(result.stdout_path).read_text(errors="replace")
        except Exception:  # noqa: BLE001
            pass
        import re as _re
        failing_tests = _re.findall(r"^FAILED (\S+)", stdout_text, _re.MULTILINE)

        # GH561 §1r lane-2: telemetry-only baseline-delta gate (no behavior change)
        run_baseline_delta_gate(result.stdout_path, "pytest", git_cwd, 6, "run_pytest_post_fix", _emit_safe)

        # Write artifact
        artifact_path = None
        if scratchpad_dir:
            # 3F5599A6 D1: single source of truth (§1g) for the relpath.
            artifact_path = Path(scratchpad_dir) / _POSTFIX_PYTEST_REPORT_RELPATH
            artifact_path.parent.mkdir(parents=True, exist_ok=True)
            stdout_tail = stdout_text[-4096:] if len(stdout_text) > 4096 else stdout_text
            failing_list = "\n".join(f"- {t}" for t in failing_tests) if failing_tests else "(none parsed)"
            artifact_path.write_text(
                "# Post-fix pytest report\n\n"
                f"**Step:** `run_pytest_post_fix`\n"
                f"**Exit code:** {result.exit_code}\n"
                f"**Tests failed:** {result.n_failed}\n"
                f"**Tests passed:** {result.n_passed}\n"
                f"**Test paths run:** {len(py_test_paths)}\n\n"
                "## Failing tests\n\n"
                f"{failing_list}\n\n"
                "## stdout tail (last 4 KB)\n\n"
                f"```\n{stdout_tail}\n```\n"
            )

        artifact_relpath = (
            str(artifact_path.relative_to(scratchpad_dir))
            if (artifact_path and scratchpad_dir)
            else None
        )

        _emit_safe(
            "post_fix_pytest_fail_with_regressions",
            {
                "n_failed": result.n_failed,
                "failing_tests": failing_tests,
                "n_test_paths": len(py_test_paths),
                "exit_code": result.exit_code,
                "artifact_relpath": artifact_relpath,
                "phase": 6,
                "step": "run_pytest_post_fix",
            },
        )
        return StepResult(
            status="error",
            data={
                **prev.data,
                "post_fix_pytest": {
                    "passed": False,
                    "n_failed": result.n_failed,
                    "failing_tests": failing_tests,
                    "artifact_path": str(artifact_path) if artifact_path else None,
                },
            },
            duration_ms=duration_ms,
            step_name="run_pytest_post_fix",
            error=f"post-fix pytest: {result.n_failed} test(s) still failing",
            error_code="E_POST_FIX_PYTEST_FAILED",
            recoverable=False,
        )

    # exit_code != 0 AND n_failed == 0 — collection error / plugin crash
    _emit_safe("post_fix_pytest_infra_error", {"reason": "exit_no_failures", "phase": 6, "step": "run_pytest_post_fix"})
    return StepResult(
        status="error",
        data=None,
        duration_ms=duration_ms,
        step_name="run_pytest_post_fix",
        error=f"pytest exited {result.exit_code} with 0 failures — collection or plugin error",
        error_code="E_POST_FIX_PYTEST_INFRA",
        recoverable=False,
    )


# ─── workflow definition ─────────────────────────────────────────────────────


def phase_6_review_workflow() -> WorkflowDefinition:
    return WorkflowDefinition(
        name="phase_6_review",
        steps=[
            StepContract(name="build_review_prompt", execute=_build_review_prompt),
            StepContract(name="invoke_review_llm", execute=_invoke_review_llm, resume_sentinel=True),
            # 319C2DCF Option A: deterministic Python aggregation of per-role
            # review files. skip_on_error=True: when no role files exist
            # (legacy / drift), the workflow falls through to the legacy
            # stdout/disk resolution path in write_review_artifact.
            StepContract(
                name="aggregate_review_findings",
                execute=_aggregate_review_findings,
                skip_on_error=True,
            ),
            step(
                "write_review_artifact",
                _write_review_artifact,
                retries=RetryPolicy(max_retries=1),
            ),
            StepContract(name="verify_findings", execute=_verify_findings),
            StepContract(name="verify_findings_semantic", execute=_verify_findings_semantic),
            StepContract(name="build_fix_prompt", execute=_build_fix_prompt),
            StepContract(name="invoke_fix_llm", execute=_invoke_fix_llm, resume_sentinel=True),
            StepContract(name="fix_watchdog", execute=_fix_watchdog),  # 775D6752
            step(
                "write_fix_artifact",
                _write_fix_artifact,
                retries=RetryPolicy(max_retries=1),
            ),
            StepContract(name="commit_fix_code", execute=_commit_fix_code),
            StepContract(name="commit_fix_tests", execute=_commit_fix_tests),   # 8FE3D757
            StepContract(name="run_pytest_post_fix", execute=_run_pytest_post_fix),
            StepContract(name="verify_fix_typecheck", execute=_verify_fix_typecheck),  # GH316
            StepContract(name="build_decorr_prompt", execute=_build_decorr_prompt),  # GH379
            StepContract(name="invoke_decorr_llm", execute=_invoke_decorr_llm, resume_sentinel=True),  # GH379
            StepContract(name="write_decorr_artifact", execute=_write_decorr_artifact),  # GH379
            StepContract(name="build_satisfaction_prompt", execute=_build_satisfaction_prompt),
            StepContract(name="invoke_satisfaction_llm", execute=_invoke_satisfaction_llm),
            StepContract(name="write_satisfaction_doc", execute=_write_satisfaction_doc),
            StepContract(name="detect_mass_unverified", execute=_detect_mass_unverified),
        ],
    )
