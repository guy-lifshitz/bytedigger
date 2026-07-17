"""Phase 5 (RED → Opus validate → GREEN) as a WorkflowDefinition.

Stage 2.5 port (2026-04-25). Fourth LLM-heavy phase. Longest workflow yet:
THREE LLM calls in one workflow with a HARD GATE between calls 2 and 3.

**Scope of this v0 port — single-pass, single-worker per role.**
- RED writes failing tests (one worker; no file-level fanout)
- Opus validator HARD-GATEs GREEN with a PASS/FAIL verdict
- GREEN writes implementation (one worker)

phase-5-implement.md describes additional patterns DEFERRED in v0:

1. **COMPLEX worker dispatch** — file-level fanout (one impl worker per
   self-contained task group). Same parked design pass as multi-architect
   fanout in phase-4. Both wait for ``StepContract.fanout``.
2. **Workflow-level 3-cycle GREEN retry loop**. REPLACED in this port by
   pushing iteration INTO the LLM subprocess: the GREEN prompt instructs
   the worker to do its own 3-cycle red→green loop and report outcome via
   ``GREEN COMPLETE`` / ``GREEN BLOCKED`` markers. ``write_green_artifact``
   parses the marker and returns ``E_GREEN_BLOCKED`` so the caller can
   decide what to do. Cheaper tokens (no full prompt re-read between
   attempts), better quality (LLM retains context across attempts), and
   no engine-level loop primitive needed. The original workflow-level
   design pass stays parked alongside the REVISE retry loop in phase-4.5.
3. **Test-Integrity Diff Guard (Step 3.5)** — Opus reviewer that classifies
   test diffs as ASSERTION_GAMING vs SPEC_CHANGE. Lives downstream of this
   workflow; out of scope for the v0 port.

See ``project_workflow_engine_fanout_deferred.md``.

Hard-gate semantics:
    The Opus verdict is parsed as PASS / FAIL / UNKNOWN. FAIL or UNKNOWN
    halts the workflow with ``error_code=E_VALIDATION_FAILED`` so the
    GREEN steps never run. This matches phase-5-implement.md "Opus HARD
    GATE — MANDATORY. Cannot proceed without PASS. No exceptions." and
    the ``never_skip_opus_validation_gate`` learning. UNKNOWN is treated
    as FAIL on purpose: silent omission of a verdict must not pass.

Token-spend guards (same playbook as phase_1 / phase_4 / phase_45):
    - All three prompts list READ_FIRST pointer paths only.
    - RED prompt references spec by path (Phase 4.5 output) — never inlined.
    - Validation prompt references spec + the just-written RED log by path.
    - GREEN prompt references spec + RED log + validation doc by path.
    - Optional ``role_template_path`` (~3KB) replaces full CLAUDE.md (~10KB).
    - Default timeouts: 1200s RED (2400s for FEATURE/COMPLEX, see EDBDCDB2), 600s validate, 900s GREEN.

Inputs (via ``ctx.org_config``):
    scratchpad_dir              — REQUIRED. Absolute path to scratchpad root.
    role_template_path          — Optional. Prepended to ALL three prompts.
    git_cwd                     — Optional. Working directory for git operations
                                  in commit_red_tests. Defaults to Path.cwd().
    llm_command                 — Optional. Default: get_claude_primary(). Global
                                  fallback for all three subprocess calls.
    red_llm_command             — Optional. Per-step override of llm_command.
    validation_llm_command      — Optional. Per-step override of llm_command.
                                  Recommended: pin to Opus here so the harness
                                  cannot silently downgrade the hard gate
                                  (see learning ``never_skip_opus_validation_gate``).
    green_llm_command           — Optional. Per-step override of llm_command.
    red_llm_timeout_sec         — Optional. Default 600.
    validation_llm_timeout_sec  — Optional. Default 600.
    green_llm_timeout_sec       — Optional. Default 900 (SIMPLE), 1500 (FEATURE), 1800 (COMPLEX).

``ctx.question`` carries the user's feature request text.

Steps (12):
    1.  build_red_prompt          — deterministic; spec path
    2.  invoke_red_llm            — opaque subprocess (RED writes test files itself)
    3.  write_red_artifact        — capture LLM stdout to tests/build-red-output.log
    4.  commit_red_tests          — commit RED test files; write pre-red-ref.txt for
                                    integrity; sentinel HEAD if working tree clean
    5.  build_validation_prompt   — deterministic; spec + RED log paths
    6.  invoke_validation_llm     — opaque subprocess (Opus validator, read-only)
    7.  write_validation_doc      — write reviews/build-opus-validation.md;
                                    parse PASS/FAIL/UNKNOWN
    8.  gate_on_validation        — HARD GATE: error if verdict != PASS
    9.  build_green_prompt        — deterministic; spec + test artifact + validation doc
    10. invoke_green_llm          — opaque subprocess (GREEN writes code itself)
    11. check_green_token_budget  — observability ALERT: emits green_token_budget_alert event when GREEN tokens_out > threshold (B7442146 downgrade; GH727: complexity-aware, COMPLEX→40000)
    12. write_green_artifact      — capture LLM stdout to tests/build-green-output.log

Outputs:
    $SCRATCHPAD/tests/build-red-output.log
    $SCRATCHPAD/integrity/pre-red-ref.txt  (40-char SHA written by commit_red_tests)
    $SCRATCHPAD/reviews/build-opus-validation.md
    $SCRATCHPAD/tests/build-green-output.log
"""
from __future__ import annotations

import ast
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

logger = logging.getLogger(__name__)

from dataclasses import replace as _replace
from contracts import LoopStepContract, RetryPolicy, StepContract, StepResult, WorkflowDefinition, step
from config_provider import get_config, int_value, timeout_policy_path, default_security_asset  # noqa: E402  GH285 C2
import flags_catalog  # noqa: E402  GH529
from suite_safety import scan_suite_safety
from stub_passability import scan_stub_passability
from reproducibility import verify_count_reproducible, _pin_pytest_collection, _REPRODUCIBILITY_RUNS
from engine import LoopRunner
from llm_subprocess import invoke_llm_subprocess, manifest_from_result, _ManifestMissingError, _ManifestError, prev_data_corruption_reason
import telemetry_ctx

sys.path.insert(0, str(Path(__file__).parent.parent / "lib" / "plugins"))
sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))
from bounded_spawn import bounded_run  # noqa: E402
from lib import git_port  # noqa: E402  164E4EFA — rc-aware git read adapter
from lib import git_write_port  # noqa: E402  5F06E98D — injectable git write-op seam
from anti_hallucination.helper import (  # noqa: E402
    get_prompt_fragment as _get_anti_fab_prompt,
    get_producer_prompt_fragment as _get_producer_anti_fab_prompt,
    get_behavioral_assertion_rubric as _get_behavioral_rubric,
    get_out_of_role_block as _get_out_of_role_block,
    verify_validation_doc as _verify_validation_doc_impl,
)
from model_config import get_claude_critical, get_claude_fallback, get_claude_primary  # noqa: E402
from plugins.disk_truth import git_diff_files, resolve_pre_phase_sha, run_test_command, test_subprocess_env, parse_structured_block, enforce, ValidationVerdict, SchemaViolation  # noqa: E402
from net_new_delta import delta_verdict  # noqa: E402  585E30E3-P2
try:
    from ._baseline_delta import run_baseline_delta_gate  # noqa: E402  GH561 §1r lane-2
except ImportError:  # pragma: no cover — bare fallback for sys.path-rooted test imports (GH881)
    from _baseline_delta import run_baseline_delta_gate  # type: ignore[no-redef]  # noqa: E402  GH561 §1r lane-2
from lib.plugins.checklist_convergence.impl_delta_retry_prompt import build_impl_delta_retry_prompt  # noqa: E402  GH496
try:
    from ._standards_context import get_standards_context  # noqa: E402
except ImportError:  # pragma: no cover — bare fallback for sys.path-rooted test imports (GH881)
    from _standards_context import get_standards_context  # type: ignore[no-redef]  # noqa: E402
from recoverable_gate import RecoverableGateMixin  # noqa: E402  E843349F
from verdict_parse import last_line_anchored_marker  # noqa: E402
from verdict_gate import run_gate as _run_verdict_gate  # noqa: E402  GH517 34E0B77B
from worktree_root import resolve_worktree_root as _resolve_worktree_root  # noqa: E402
from lib.git_cwd import resolve_git_cwd  # noqa: E402  GH381
from lib.directed_repair import (  # noqa: E402  457DC7DC GH371 §2.2
    attempt_directed_repair,
    _directed_repair_enabled,
    _resolve_directed_repair_model,
    _repair_cap,
)
from lib import authored_boundary  # noqa: E402  GH373 §2 Part A
from lib.timeout_policy import DEFAULT_POLICY, cached_policy, resolve_timeout_sec  # noqa: E402  GH285 C2
from spec_defect_ledger import (  # noqa: E402  GH767 §2.2/§2.4b
    attempts_floor,
    attempts_from_event_log,
    build_key,
    mark_reroute_consumed,
    record_reroute,
    read_reroute_ledger,
    reroute_already_consumed,
    spec_sha,
)
from lib.step_sentinel import invalidate_cycle_sentinels  # noqa: E402  GH767 §2.4b (§1g: reuse verbatim)


def _timeout_policy() -> dict:
    return cached_policy(str(timeout_policy_path()))
try:
    from .phase_workflows_common import (_emit_safe, _filter_gitignored_paths, _filter_phantom_deleted_paths, _git_op_with_lock_retry, _last_marker_wins, _maybe_emit_cross_tree_warning, _maybe_role_template, _paths_have_staged_changes, _read_engine_mode, _read_first_block, _resolve_command, _resolve_model, _resolve_scratchpad, _revert_cross_tree_modifications, _verify_no_cross_tree_edits, _worktree_edit_boundary_block, _CROSS_TREE_PROMPT_TEMPLATE, _ENGINE_MODE_RE, resolve_engine_mode)  # noqa: E402,F401  #261 Stage 0  3F5599A6  GH268
except ImportError:  # pragma: no cover — bare fallback for sys.path-rooted test imports (GH881)
    from phase_workflows_common import (_emit_safe, _filter_gitignored_paths, _filter_phantom_deleted_paths, _git_op_with_lock_retry, _last_marker_wins, _maybe_emit_cross_tree_warning, _maybe_role_template, _paths_have_staged_changes, _read_engine_mode, _read_first_block, _resolve_command, _resolve_model, _resolve_scratchpad, _revert_cross_tree_modifications, _verify_no_cross_tree_edits, _worktree_edit_boundary_block, _CROSS_TREE_PROMPT_TEMPLATE, _ENGINE_MODE_RE, resolve_engine_mode)  # type: ignore[no-redef]  # noqa: E402,F401  #261 Stage 0  3F5599A6  GH268

def _default_red_model() -> str:
    return get_claude_primary()


def _default_validation_model() -> str:
    return get_claude_critical()


def _default_green_model() -> str:
    return get_claude_primary()


def _tier_haiku_model() -> str:
    """A3398552: SIMPLE tier savings — fallback (haiku) model."""
    return get_claude_fallback()


# 25e75663: old argv-returning builders kept as back-compat aliases for callers
# that still reference them (e.g. tests in PASS 1 migration). They now delegate
# to the new model-string builders.
def _default_red_llm_command() -> list[str]:
    from llm_subprocess import _build_claude_argv
    return _build_claude_argv(_default_red_model())


def _default_validation_llm_command() -> list[str]:
    from llm_subprocess import _build_claude_argv
    return _build_claude_argv(_default_validation_model())


def _default_green_llm_command() -> list[str]:
    from llm_subprocess import _build_claude_argv
    return _build_claude_argv(_default_green_model())


def _tier_haiku_llm_command() -> list[str]:
    """A3398552: SIMPLE tier savings — fallback (haiku) model. Back-compat alias."""
    from llm_subprocess import _build_claude_argv
    return _build_claude_argv(_tier_haiku_model())


# Backward-compat module-level constants (computed once at import via ModelConfig).
DEFAULT_RED_LLM_COMMAND = _default_red_llm_command()
DEFAULT_VALIDATION_LLM_COMMAND = _default_validation_llm_command()
DEFAULT_GREEN_LLM_COMMAND = _default_green_llm_command()
TIER_HAIKU_LLM_COMMAND = _tier_haiku_llm_command()
DEFAULT_RED_TIMEOUT_SEC = DEFAULT_POLICY["implement.red"]["base"]
DEFAULT_RED_TIMEOUT_SEC_FEATURE = DEFAULT_POLICY["implement.red"]["FEATURE"]  # FEATURE/COMPLEX RED needs more headroom — see EDBDCDB2 (RC1).
DEFAULT_VALIDATION_TIMEOUT_SEC = DEFAULT_POLICY["implement.validation"]["base"]
DEFAULT_GREEN_TIMEOUT_SEC = DEFAULT_POLICY["implement.green"]["base"]
# D3F7B377: COMPLEX validation/GREEN need wider headroom. Opus auditing large
# RED tests + Sonnet writing 200+ line impls hit the 600s/900s wall on
# real COMPLEX runs. Mirror 80CC602D phase_45_spec scaling pattern.
DEFAULT_VALIDATION_TIMEOUT_SEC_COMPLEX = DEFAULT_POLICY["implement.validation"]["COMPLEX"]  # 20 min for COMPLEX validation
DEFAULT_GREEN_TIMEOUT_SEC_FEATURE = DEFAULT_POLICY["implement.green"]["FEATURE"]       # 25 min for FEATURE GREEN impl (4+ files; 6F08F5F5 evidence)
DEFAULT_GREEN_TIMEOUT_SEC_COMPLEX = DEFAULT_POLICY["implement.green"]["COMPLEX"]       # 30 min for COMPLEX GREEN impl
GREEN_OUTPUT_TOKEN_BUDGET = 5000
GREEN_WATCHDOG_WALL_MULTIPLIER = 2
GREEN_WATCHDOG_TOKEN_MULTIPLIER = 5
# 9356C4D1: COMPLEX builds need ~10x token budget (W1 batch-build run wrote
# 266 LOC at 7.4x and was killed despite 48/48 RED-pass). Non-COMPLEX keeps 5x.
GREEN_WATCHDOG_TOKEN_MULTIPLIER_COMPLEX = 10
# GH727: COMPLEX GREEN legitimately writes ~21-31k out (cal7: 6.3x base), well
# within the 10x=50k watchdog budget. The flat-5000 observability alert must
# scale for COMPLEX too, else it noise-fires 4-6x over every cycle. 8x=40000
# sits above cal7's legit max (31k) with headroom, below the watchdog's 50k.
# Loose default; cfg/env overridable per §1b (single COMPLEX datapoint).
GREEN_OUTPUT_TOKEN_BUDGET_MULTIPLIER_COMPLEX = 8


def _resolve_red_timeout_sec(cfg: dict | None) -> int:
    """implement.red timeout via unified policy (GH285 C2). Precedence:
    cfg.red_llm_timeout_sec > COMPLEX/FEATURE > base. Used by invoke_red_llm."""
    return resolve_timeout_sec("implement.red", cfg, policy=_timeout_policy())


def _resolve_validation_timeout_sec(cfg: dict | None) -> int:
    """implement.validation timeout via unified policy (GH285 C2). Precedence:
    cfg.validation_llm_timeout_sec > COMPLEX > base. D3F7B377: COMPLEX → 1200."""
    return resolve_timeout_sec("implement.validation", cfg, policy=_timeout_policy())


def _resolve_green_timeout_sec(cfg: dict | None) -> int:
    """implement.green timeout via unified policy (GH285 C2). Precedence:
    cfg.green_llm_timeout_sec > COMPLEX > FEATURE > base. Used by
    invoke_green_llm, cwd_preflight retry path, and green_watchdog wall-limit
    calculation. D3F7B377: COMPLEX → 1800. 477B3143: FEATURE → 1500."""
    return resolve_timeout_sec("implement.green", cfg, policy=_timeout_policy())


def _resolve_green_watchdog_token_multiplier(cfg: dict | None) -> int:
    """Resolves token-budget multiplier for the GREEN watchdog.

    Order: explicit cfg override > complexity-aware default > 5x baseline.
    COMPLEX builds get 10x to accommodate ~250-line impls (W1 batch-build
    case: 266 LOC at 7.4x budget would otherwise abort despite RED-pass).
    """
    if not cfg:
        return GREEN_WATCHDOG_TOKEN_MULTIPLIER
    raw = cfg.get("green_watchdog_token_multiplier")
    if raw is not None:
        try:
            return max(1, int(raw))  # clamp to ≥1; reject 0/negative
        except (TypeError, ValueError):
            pass  # fall through to default
    complexity = str(cfg.get("complexity") or "").upper()
    if complexity == "COMPLEX":
        return GREEN_WATCHDOG_TOKEN_MULTIPLIER_COMPLEX
    return GREEN_WATCHDOG_TOKEN_MULTIPLIER


def _resolve_green_output_token_budget(cfg: "dict | None") -> int:
    """Threshold for the green_token_budget_alert observability signal (Step 11).
    Precedence: env HAL_GREEN_OUTPUT_TOKEN_BUDGET (absolute) >
    cfg['green_output_token_budget'] (absolute) > COMPLEX default
    (8 x GREEN_OUTPUT_TOKEN_BUDGET = 40000) > base GREEN_OUTPUT_TOKEN_BUDGET (5000).
    Malformed override -> fall through. Mirrors the _resolve_* cfg pattern in
    this module (max(1,int); falsy 0 falls through)."""
    # env override via the boundary-clean config seam (core must not read os.environ
    # for HAL_* directly — core-boundary gate). int_value returns the default when
    # unset; wrap so unset/malformed/falsy means "no override → fall through".
    try:
        env_budget = int_value("HAL_GREEN_OUTPUT_TOKEN_BUDGET", 0)
        if env_budget:
            return max(1, env_budget)
    except (TypeError, ValueError):
        pass
    cfg = cfg or {}
    raw = cfg.get("green_output_token_budget")
    if raw:
        try:
            return max(1, int(raw))
        except (TypeError, ValueError):
            pass
    if str(cfg.get("complexity") or "").upper() == "COMPLEX":
        return GREEN_OUTPUT_TOKEN_BUDGET_MULTIPLIER_COMPLEX * GREEN_OUTPUT_TOKEN_BUDGET
    return GREEN_OUTPUT_TOKEN_BUDGET

RED_LOG_RELPATH = "tests/build-red-output.log"
VALIDATION_DOC_RELPATH = "reviews/build-opus-validation.md"
GREEN_LOG_RELPATH = "tests/build-green-output.log"
PRE_RED_REF_RELPATH = "integrity/pre-red-ref.txt"
RED_TEST_HASHES_RELPATH = "integrity/red-test-hashes.json"
RED_TEST_PATHS_RELPATH = "integrity/red-test-paths.txt"
# GH483: GREEN-complete resume seam marker (crash-resume after GREEN already
# written to the working tree, RED now passes → skip re-invoking GREEN LLM).
GREEN_COMPLETE_RESUME_RELPATH = "resume/green-complete-resume.json"

# Design A pipeline recovery (decree 2026-04-26): cap retries at 2 cycles.
# v1: hardcoded; v2: configurable post-telemetry.
MAX_VALIDATION_CYCLES = 2

# GH706: validation-cycle cap is cfg-overridable so a high-AC COMPLEX spec
# (cal6: 16 ACs) gets enough directed-convergence budget. Base default 2 is
# byte-identical for SIMPLE/FEATURE. The COMPLEX 2->3 bump is an
# enforcement-flag default:
#   flip-by:2026-08-15 Refs #706 (rollout-completion-check token)
_VALIDATION_CYCLE_CAP_COMPLEX = 3


def _resolve_validation_cycle_cap(cfg: "dict | None") -> int:
    """GH706: resolve the validation-cycle cap. Precedence:
    cfg['validation_max_cycles'] (truthy -> max(1,int); malformed -> fall
    through) > COMPLEX tier (_VALIDATION_CYCLE_CAP_COMPLEX) > base
    MAX_VALIDATION_CYCLES. Base default 2 unchanged (SIMPLE/FEATURE)."""
    cfg = cfg or {}
    raw = cfg.get("validation_max_cycles")
    if raw:
        try:
            return max(1, int(raw))
        except (TypeError, ValueError):
            pass
    if str(cfg.get("complexity") or "").upper() == "COMPLEX":
        return _VALIDATION_CYCLE_CAP_COMPLEX
    return MAX_VALIDATION_CYCLES


# W13 (/ultrareview HIGH #11): cap findings payload re-injected into cycle-2
# RED prompt. Verbose Opus validation docs can run multi-MB; tail-truncate
# from the LEFT to preserve trailing VERDICT marker + final rationale.
FINDINGS_MAX_CHARS = 32_000

# AEC7E800: cap GREEN test-failure retry loop at 2 cycles (one feedback retry
# before terminal abort — mirrors MAX_VALIDATION_CYCLES cap for RED).
GREEN_TEST_CYCLE_CAP = 2  # AEC7E800: one feedback retry before terminal abort

# Per-cycle artifact paths. Cycle 1 keeps the legacy single-shot filename
# so existing tooling and tests still resolve. Cycle 2+ gets a versioned
# suffix so cycle-1 output is preserved for audit + cycle-2 prompt revision.
def _red_log_relpath(cycle: int) -> str:
    return RED_LOG_RELPATH if cycle <= 1 else f"tests/build-red-output-cycle-{cycle}.log"


def _validation_doc_relpath(cycle: int) -> str:
    return VALIDATION_DOC_RELPATH if cycle <= 1 else f"reviews/build-validation-cycle-{cycle}.md"
SPEC_DOC_RELPATH = "specs/build-spec.md"
ARCHITECTURE_DOC_RELPATH = "architecture/architecture.md"

VERDICT_PASS = "PASS"
VERDICT_FAIL = "FAIL"
VERDICT_UNKNOWN = "UNKNOWN"

# GH767 §2.1: verdict-category taxonomy. SPEC_DEFECT means the RED author
# CANNOT fix the gap (tests/ is his only writable surface, §1s) — the blocker
# is in the spec itself. TEST_GAP is today's behaviour (retry RED).
VERDICT_CATEGORY_NONE = "NONE"
VERDICT_CATEGORY_TEST_GAP = "TEST_GAP"
VERDICT_CATEGORY_SPEC_DEFECT = "SPEC_DEFECT"

# GH767 §2.2c: reroute budget — class/module constant, not a config flag
# (keeps the rollout-completion-check flag surface untouched, GH750 §2.3
# precedent).
_MAX_SPEC_DEFECT_REROUTES = 2

GREEN_COMPLETE = "COMPLETE"


GREEN_BLOCKED = "BLOCKED"
GREEN_NO_MARKER = "NO_MARKER"


def _parse_verdict(raw: str) -> str:
    """Last line-anchored marker wins (case-insensitive)."""
    return _last_marker_wins(
        raw,
        [("VERDICT: PASS", VERDICT_PASS), ("VERDICT: FAIL", VERDICT_FAIL)],
        VERDICT_UNKNOWN,
    )


def _canonical_gate_verdict(passed: bool, markdown_verdict: str) -> str:
    """GH349/ENG-3 (§1g): single canonical verdict token.

    The gate decision (`passed`) is the ONE source of truth; the returned
    token is what LoopRunner's until_marker="PASS" substring check consumes.
    Invariant: (VERDICT_PASS in returned) == passed, for every markdown input.
    """
    if passed:
        return VERDICT_PASS
    if markdown_verdict == VERDICT_PASS:
        return VERDICT_FAIL
    return markdown_verdict


def _resolve_verdict_category(structured: "ValidationVerdict | None") -> str:
    """GH767 §2.1: SPEC_DEFECT only on an exact structured match; every other
    shape (None/absent/None-value/"NONE"/unknown-garbage) safe-defaults to
    TEST_GAP — today's behaviour. Structured-first, no new markdown regex."""
    if structured is None:
        return VERDICT_CATEGORY_TEST_GAP
    category = getattr(structured, "verdict_category", None)
    if category == VERDICT_CATEGORY_SPEC_DEFECT:
        return VERDICT_CATEGORY_SPEC_DEFECT
    return VERDICT_CATEGORY_TEST_GAP


def _parse_validation_structured(raw: str) -> "tuple[ValidationVerdict | None, str | None]":
    """Parse the structured JSON validation-output block from validator output.

    Returns:
        (ValidationVerdict instance, None) on success.
        (None, "absent") if no structured block found.
        (None, "json_error") if block present but JSON unparseable.
        (None, f"schema_violation: <field/reason>") on disk_truth schema rejection.
    """
    if not raw:
        return None, "absent"
    payload = parse_structured_block(raw, "validation-output")
    if payload is None:
        # Could be either absent OR malformed JSON. parse_structured_block
        # returns None for both; distinguish by re-checking heading presence.
        if re.search(r"^##\s+validation-output\s*\(\s*structured\s*\)", raw, re.MULTILINE):
            return None, "json_error"
        return None, "absent"
    if not isinstance(payload, dict):
        return None, "schema_violation: payload is not a dict"
    # reject_reason must be explicitly present (even as null) — both fields required.
    if "reject_reason" not in payload:
        return None, "schema_violation: Missing required field 'reject_reason' in ValidationVerdict"
    try:
        verdict = enforce(payload, ValidationVerdict)
    except SchemaViolation as e:
        return None, f"schema_violation: {e}"
    return verdict, None


def _parse_green_status(raw: str) -> str:
    """Detect the worker's completion marker.

    Replaces a workflow-level retry loop (deferred) with marker-based
    signalling: the LLM does its 3-cycle internal loop within one subprocess
    (cheaper tokens, retains context across attempts) and reports outcome via
    a single-line marker. Engine just reads the marker and returns
    success/error to the caller.

    BLOCKED / NO_MARKER both signal failure — caller decides whether to
    escalate, spawn a fresh worker, or fail the pipeline.
    """
    return _last_marker_wins(
        raw,
        [("GREEN BLOCKED", GREEN_BLOCKED), ("GREEN COMPLETE", GREEN_COMPLETE)],
        GREEN_NO_MARKER,
    )



from util.path_classifier import (  # noqa: E402
    _fn,
    _TEST_FILENAME_PATTERNS,
    _TEST_PATH_SEGMENTS,
    _is_test_path,
)


def _is_fixture_only_path(path: str) -> bool:
    """5325B280 BUG2: True for test-support files holding fixtures / package
    markers but no test functions (conftest.py, __init__.py). They satisfy
    _is_test_path (live under tests/) yet collect zero tests, so they must NOT
    enter red_test_paths — else _check_red_executable probes them, pytest exits 5
    (NOTESTSCOLLECTED), and the validation loop never converges. Exact-basename
    match only; a real test module is never named conftest.py/__init__.py."""
    return os.path.basename(path) in ("conftest.py", "__init__.py")


def _derive_red_paths_via_git_diff(git_cwd: str, segment_filter: str = "tests/", frozen_sha: str | None = None) -> list[str]:
    """Derive red test paths using disk_truth git_diff_files (Step 0 lib).

    Uses resolve_pre_phase_sha to get HEAD, then calls git_diff_files with
    untracked=True to enumerate test files matching _is_test_path.

    NOTE: 67F3404F — segment_filter parameter retained for backward compat
    but no longer authoritative; _is_test_path is the actual predicate.
    Old narrow segment_filter excluded sibling-naming test files (e.g. *.test.sh).

    frozen_sha: when provided (8D162396), use it as the diff base instead of
    calling resolve_pre_phase_sha(git_cwd). Allows cycle-2+ re-entry to diff
    against the original pre-phase boundary rather than live HEAD.

    Returns [] (gracefully) if the cwd is not a git repo (RuntimeError from
    resolve_pre_phase_sha).
    """
    try:
        if frozen_sha is not None:
            pre_sha = frozen_sha
        else:
            pre_sha = resolve_pre_phase_sha(git_cwd)
    except RuntimeError:
        return []
    # 67F3404F: pass segment_filter=None to git_diff_files (we apply our own
    # broader predicate after). Old narrow segment_filter excluded sibling-
    # naming test files (e.g. *.test.sh).
    all_changed = git_diff_files(pre_sha, git_cwd, untracked=True, segment_filter=None)
    return [p for p in all_changed if _is_test_path(p) and not _is_fixture_only_path(p)]


def _red_paths_present_on_disk(candidates, git_cwd: str) -> list[str]:
    """Filter candidate RED test paths to those that exist as files under git_cwd.
    Guarded fallback for _commit_red_tests when git-diff is empty (A4461B8F regression):
    on-disk verification keeps the gamma-cleanup anti-fabrication intent (a path that
    does not exist is rejected) while restoring resume/recovery robustness.
    Normalizes input: None / "<missing>" -> []; a str -> [str]; a list/tuple -> list.
    Order-preserving + de-duplicated."""
    if candidates is None or candidates == "<missing>":
        normalized: list[str] = []
    elif isinstance(candidates, str):
        normalized = [candidates]
    else:
        normalized = list(candidates)
    seen: set[str] = set()
    result: list[str] = []
    base = Path(git_cwd)
    for p in normalized:
        if p not in seen and (base / p).is_file():
            seen.add(p)
            result.append(p)
    return result


def _resolve_frozen_pre_red_sha(scratchpad, git_cwd: str) -> str:
    """H1 (8D162396): return the frozen pre-red boundary SHA.

    If scratchpad/PRE_RED_REF_RELPATH exists and holds a 40-hex SHA, return it
    (the cycle-1 frozen boundary). Otherwise fall back to git rev-parse HEAD of
    git_cwd (cycle-1 path: nothing committed yet, so HEAD == true pre-phase).

    Pure — no side-effects or writes.
    """
    ref_path = Path(scratchpad) / PRE_RED_REF_RELPATH
    if ref_path.exists():
        candidate = ref_path.read_text().strip()
        if len(candidate) == 40 and all(c in "0123456789abcdef" for c in candidate):
            return candidate
    # Fallback: current HEAD of git_cwd
    result = git_port.git_read(
        ["rev-parse", "HEAD"],
        cwd=git_cwd,
        timeout=30,
    )
    if result.returncode == 124:
        return ""
    return result.stdout.strip()


def _persist_pre_red_ref(scratchpad, sha: str) -> bool:
    """H2 (8D162396): write sha to scratchpad/PRE_RED_REF_RELPATH, write-once.

    Writes sha only if the file does not already exist (preserving the
    cycle-1 frozen boundary on re-entry). Creates parent dirs as needed.
    Returns True if it wrote, False if it preserved an existing file.
    """
    ref_path = Path(scratchpad) / PRE_RED_REF_RELPATH
    ref_path.parent.mkdir(parents=True, exist_ok=True)
    if ref_path.exists():
        return False
    ref_path.write_text(sha)
    return True


def _persist_red_test_hashes(scratchpad, manifest: dict) -> bool:
    """GH639 (7C0FDE44): write-once frozen-hash manifest for committed RED
    tests, mirrors ``_persist_pre_red_ref``. Returns False when scratchpad is
    falsy, when the manifest file already exists (resume idempotency — a
    re-entered ``_commit_red_tests`` must NOT overwrite the frozen hashes),
    or on OSError. Else writes ``json.dumps(manifest, sort_keys=True)`` and
    returns True."""
    if not scratchpad:
        return False
    ref_path = Path(scratchpad) / RED_TEST_HASHES_RELPATH
    try:
        if ref_path.exists():
            return False
        ref_path.parent.mkdir(parents=True, exist_ok=True)
        ref_path.write_text(json.dumps(manifest, sort_keys=True))
        return True
    except OSError:
        return False


def _read_red_test_hashes(scratchpad) -> "dict | None":
    """GH639 (7C0FDE44): fail-safe read of the frozen-hash manifest. None on
    falsy scratchpad / absent file / unreadable / non-dict JSON."""
    if not scratchpad:
        return None
    ref_path = Path(scratchpad) / RED_TEST_HASHES_RELPATH
    try:
        obj = json.loads(ref_path.read_text())
    except (OSError, ValueError):
        return None
    return obj if isinstance(obj, dict) else None


def _persist_red_test_paths(scratchpad, paths: list[str]) -> bool:
    """Persist discovered RED test paths to scratchpad/RED_TEST_PATHS_RELPATH.

    No-ops (returns False) when paths is empty — never overwrites a prior good
    list with an empty one, protecting the durable-resume fallback chain.
    When paths is non-empty, overwrites any existing file (last-good-wins so a
    refined re-invocation updates the record). Creates parent dirs as needed.
    Returns True if it wrote, False if it no-op'd on empty input.
    """
    if not paths:
        return False
    ref_path = Path(scratchpad) / RED_TEST_PATHS_RELPATH
    ref_path.parent.mkdir(parents=True, exist_ok=True)
    ref_path.write_text("\n".join(paths))
    return True


def _read_red_test_paths(scratchpad) -> list[str]:
    """Read persisted RED test paths from scratchpad/RED_TEST_PATHS_RELPATH.

    Returns the non-blank stripped lines in order. Returns [] if the file is
    absent or any read error occurs. Pure — no writes.
    """
    ref_path = Path(scratchpad) / RED_TEST_PATHS_RELPATH
    try:
        if not ref_path.exists():
            return []
        return [line for line in ref_path.read_text().splitlines() if line.strip()]
    except Exception:
        return []


def _derive_green_paths_from_git(git_cwd: str) -> list[str]:
    """Return sorted unique list of GREEN-modified production .py paths.

    Excludes test files (test_*.py, *_test.py, paths containing tests/ or __tests__/).
    Returns [] on git failure; never raises.
    """
    TEST_PATTERNS = (
        "test_*.py",
        "*_test.py",
    )
    TEST_SEGMENTS = ("tests/", "__tests__/")

    try:
        result = git_port.git_read(
            ["status", "--porcelain"],
            cwd=git_cwd,
            timeout=30,
        )
    except Exception as exc:
        logger.warning(
            "_derive_green_paths_from_git: git status failed: %s", exc,
        )
        return []

    if result.returncode != 0:
        logger.warning(
            "_derive_green_paths_from_git: git status failed (rc=%d, stderr=%s)",
            result.returncode, result.stderr[:200],
        )
        return []

    import fnmatch as _fnmatch
    from pathlib import Path as _Path

    git_cwd_resolved = _Path(git_cwd).resolve()

    paths: list[str] = []
    for line in result.stdout.splitlines():
        if len(line) < 4:
            continue
        # porcelain format: XY<space><path>  (3-char prefix: 2 status + 1 space)
        raw_path = line[3:].strip()
        # handle rename format: "old -> new"
        if " -> " in raw_path:
            raw_path = raw_path.split(" -> ", 1)[1].strip()

        # Untracked directories show as "dir/" — expand to individual files
        if raw_path.endswith("/"):
            dir_path = _Path(git_cwd) / raw_path
            if dir_path.is_dir():
                for f in dir_path.rglob("*"):
                    if f.is_symlink():
                        try:
                            target = f.resolve(strict=True)
                        except (OSError, RuntimeError):
                            continue
                        try:
                            target.relative_to(git_cwd_resolved)
                        except ValueError:
                            continue
                    if not f.is_file():
                        continue
                    try:
                        rel = str(f.relative_to(git_cwd))
                    except ValueError:
                        continue
                    paths.append(rel)
            continue

        paths.append(raw_path)

    # Single-pass filter + dedup: keep only .py files, exclude test files
    final: list[str] = []
    seen: set[str] = set()
    for p in paths:
        if p in seen:
            continue
        seen.add(p)
        # Only Python source files
        if not p.endswith(".py"):
            continue
        filename = p.split("/")[-1] if "/" in p else p
        normalized = p.replace("\\", "/")
        # Exclude test files by name pattern
        is_test = any(_fnmatch.fnmatch(filename, pat) for pat in TEST_PATTERNS)
        if not is_test:
            # Exclude by path segment
            is_test = any(seg in normalized for seg in TEST_SEGMENTS)
        if not is_test:
            final.append(p)

    return sorted(final)


def _get_security_fragment(cfg: dict) -> str:
    """SECBUILD Child 1c (gh-340): static secure-codegen fragment for RED/GREEN
    prompts. Path overridable via org_config['security_fragment_path'] (B2 OSS
    seam). Raises FileNotFoundError — callers fail CLOSED (E_SEC_FRAGMENT_MISSING)."""
    path = Path(cfg.get("security_fragment_path") or default_security_asset(
        "secure-codegen-fragment.md", Path(__file__).parents[2] / "security" / "secure-codegen-fragment.md"))
    return path.read_text(encoding="utf-8")


_RE_1P_WANTS = re.compile(r"\.sh\b")
_RE_1I_WANTS = re.compile(r"singleton|time\.time|sleep\(|timestamp|mtime|TTL\b|clock|date \+%", re.IGNORECASE)

# GH513/§2.5: remediation hint interpolated into the E_RED_TESTS_TAMPERED error string.
_TAMPER_REMEDIATION_HINT = (
    "if this edit is a spec-required contract migration, add the path under the "
    "frozen spec's `authorized-test-edits:` block (GH436/GH513) and resume; "
    "otherwise this is assertion tampering"
)


def _spec_wants_1p(spec_text: str) -> bool:
    """GH501/§1p (157DA4CA): True if spec_text references a bash UUT (`.sh`)."""
    return bool(_RE_1P_WANTS.search(spec_text))


def _spec_wants_1i(spec_text: str) -> bool:
    """GH501/§1i (8CA8D54C): True if spec_text mentions singleton/time-dependent tokens."""
    return bool(_RE_1I_WANTS.search(spec_text))


_RE_PY_UUT_WANTS = re.compile(r"\.py\b")


def _spec_wants_py_gates(spec_text: str) -> bool:
    """GH596: True if spec references a python UUT/test surface (`.py`)."""
    return bool(_RE_PY_UUT_WANTS.search(spec_text))


# ─── Step 1: build RED prompt ────────────────────────────────────────────────

# GH496 §2.2a — hoisted from the inline RULES/OUTPUT literals below so both
# the full cycle-1 scaffold and the delta-retry prompt (impl_delta_retry_prompt.py)
# share a single source of truth.
RED_COLLECTABILITY_RULE = (
    "  - COLLECTABILITY (D1CF5FDF): the GREEN production code does not exist yet at RED time. If a test imports a not-yet-existing module or symbol, DEFER that import to INSIDE the test function body — never at module top level. A module-top import of a missing target fails at pytest COLLECTION (ImportError), which the engine treats as a terminal E_RED_COLLECT_FAILED, not a clean assert-time failure. The RED must COLLECT and FAIL at assert time. Use a function-body import, or pytest.importorskip, or getattr(module, \"name\", None) probed inside the test.\n"
)

RED_OUTPUT_MARKER_BLOCK = (
    "OUTPUT: end your response with EXACTLY this line:\n"
    "  RED COMPLETE — [N] tests written, all failing. Files: [path1, path2, ...]\n"
    "Examples (USE BRACKETS — they prevent ambiguity when paths contain spaces):\n"
    "  RED COMPLETE — 1 test written, all failing. Files: [tests/foo_test.py]\n"
    "  RED COMPLETE — 2 tests written, all failing. Files: [tests/foo_test.py, tests/bar_test.py]\n"
    "If you encountered SPEC ambiguities, append a `Notes:` line listing them.\n"
    "Do NOT include a status report instead of writing the tests — your test\n"
    "files written via Edit/Write ARE the deliverable; the marker line is the\n"
    "trailer, not a substitute."
)

# GH334 §2.6/§1aa: maximal contiguous CALL-INVARIANT static instruction run
# hoisted out of the RED-prompt scaffold (identical bytes regardless of
# ctx.question, spec text, surface, cycle, findings). This is the exact
# original "Surface-specific for RED" + "## RED-LINT GROUNDING RULES" literal
# text — substituting a single parts.append(_RED_STABLE_PREFIX) for the two
# original parts.append(...) calls preserves the joined prompt byte-for-byte
# (parts.join("\n") already inserted the same "\n" between the two blocks).
_RED_STABLE_PREFIX = (
    "Surface-specific for RED:\n"
    "  - The SPEC's ## Acceptance Criteria, ## Behavior, ## Constraints are\n"
    "    the only test sources. SPEC silent → flag the gap in your final\n"
    "    report; do NOT add tests for what the SPEC does not say.\n"
    "  - Coverage = distinct invariants, not test lines. Don't pad with\n"
    "    trivial variants of the same behavior.\n"
    "  - Don't test incidental implementation details (private helpers,\n"
    "    intermediate data structures) the SPEC does not constrain. Test\n"
    "    the contract.\n"
    "  - Ambiguous SPEC criterion → strictest reasonable reading + note in\n"
    "    final report. Do NOT silently invent a more permissive read.\n"
    "\n"
    "## RED-LINT GROUNDING RULES\n"
    "These rules are mechanically enforced post-write by semgrep against your test files.\n"
    "Violations halt the build — fix them before emitting the RED COMPLETE marker.\n"
    "  1. F1 (no hardcoded line numbers): do not write `where=foo.py:42`-style assertions. "
    "Line numbers shift with innocuous edits and make tests brittle. Use filename-only "
    "matchers (`where=foo.py`) or symbol-based assertions instead.\n"
    "  2. F3 (verify schema referents): when writing `schema=Type.field`-style assertions, "
    "you MUST have read the type definition with the Read tool in this turn. Do not guess "
    "type names — `grep -rn 'class <TypeName>' engine_py/` first if unsure.\n"
    "  3. F-CITATION (read before you cite): every `<path>:<line>` you reference in test "
    "rationale, docstrings, or comments MUST come from a file you opened with the Read "
    "tool in THIS turn. If unverifiable, omit the citation rather than fabricate it.\n"
)


_RED_LINT_DIGEST_FALLBACK = (
    "RED-LINT RULESET (enforced post-write by semgrep; ERROR hits are TERMINAL):\n"
    "  - see scripts/red_lint/rules.yml (digest unavailable)\n"
)

_RED_LINT_DIGEST_EXCLUDED_IDS = frozenset(
    {
        # already spelled out in _RED_STABLE_PREFIX (F1/F3):
        "hardcoded-line-number-in-where-assertion",
        "schema-referent-needs-verification",
        # already spelled out in the conditional §1p RULES block (a,c,d,e,f,g):
        "bash-quoted-eregex-rhs",
        "bash-case-expansion",
        "bash-grep-pcre",
        "bash-pcre-class-in-ere",
        "bash-find-tmp-no-slash",
        "jq-fromdateiso8601-fractional",
    }
)

_RE_TSJS_WANTS = re.compile(r"\.(ts|js)\b")


def _red_lint_prompt_digest(spec_text: str, rules_path: Path | None = None) -> str:
    """GH855 §2.1 — single-source digest of scripts/red_lint/rules.yml for
    the RED prompt (mirrors _green_lint_prompt_digest, GH724 §2.2, plus
    per-rule language gating). Deterministic regex parse (no yaml import).
    Never raises: missing/unreadable file or zero parsed ids falls back to a
    static one-line block (§1n OWN); ids parsed but none survive gating/
    exclusion returns "" (nothing applies, not a parse failure)."""
    if rules_path is None:
        rules_path = Path(__file__).resolve().parent.parent / "scripts" / "red_lint" / "rules.yml"
    try:
        text = rules_path.read_text(encoding="utf-8")
    except OSError:
        return _RED_LINT_DIGEST_FALLBACK

    lines = text.splitlines()
    entries: list[tuple[str, str, list[str] | None]] = []
    i = 0
    while i < len(lines):
        m = re.match(r"^  - id: (\S+)", lines[i])
        if m:
            rule_id = m.group(1)
            hint = ""
            languages: list[str] | None = None
            j = i + 1
            while j < len(lines) and not re.match(r"^  - id: ", lines[j]):
                lang_m = re.match(r"^\s*languages:\s*\[([^\]]*)\]", lines[j])
                if lang_m:
                    languages = [tok.strip() for tok in lang_m.group(1).split(",") if tok.strip()]
                msg_m = re.match(r"^\s*message:\s*(.*)$", lines[j])
                if msg_m:
                    k = j + 1
                    candidate = msg_m.group(1).strip()
                    if candidate and candidate not in ("|", ">", "|-", ">-"):
                        hint = candidate
                    else:
                        while k < len(lines):
                            cand_line = lines[k].strip()
                            if cand_line:
                                hint = cand_line
                                break
                            k += 1
                j += 1
            entries.append((rule_id, hint[:100], languages))
        i += 1

    if not entries:
        return _RED_LINT_DIGEST_FALLBACK

    wants_py = _spec_wants_py_gates(spec_text)
    wants_bash = _spec_wants_1p(spec_text)
    wants_tsjs = bool(_RE_TSJS_WANTS.search(spec_text))

    def _lang_gate(lang: str) -> bool:
        if lang == "python":
            return wants_py
        if lang == "bash":
            return wants_bash
        if lang in ("ts", "js"):
            return wants_tsjs
        return False

    survivors: list[tuple[str, str]] = []
    for rule_id, hint, languages in entries:
        if rule_id in _RED_LINT_DIGEST_EXCLUDED_IDS:
            continue
        if languages is None:
            included = True
        else:
            included = any(_lang_gate(lang) for lang in languages)
        if included:
            survivors.append((rule_id, hint))

    if not survivors:
        return ""

    out = ["RED-LINT RULESET (enforced post-write by semgrep; ERROR hits are TERMINAL):\n"]
    for rule_id, hint in survivors:
        out.append(f"  - {rule_id}: {hint}\n")
    return "".join(out)


def _build_red_prompt(ctx, _prev, findings: str | None = None) -> StepResult:
    """Build the RED-worker prompt.

    On retry (cycle ≥ 2) the cycle/findings reach this step via two prev shapes:
      - dict ``{"cycle": N, "findings": str}`` — legacy engine retry hook
        ``initial_data`` path (kept for backward-compat with engine.py:264).
      - StepResult — D352C2D1 LoopRunner iteration N≥2 path. Prev = the
        previous iteration's gate_on_validation result, whose
        ``data["cycle"]`` was incremented by the gate for this iteration.

    We extract cycle + findings, append a REVISION section to the prompt with
    the validator's prior-cycle findings, and version the artifact path so
    earlier-cycle output is preserved.
    """
    scratchpad = _resolve_scratchpad(ctx)
    spec_path = scratchpad / SPEC_DOC_RELPATH
    arch_path = scratchpad / ARCHITECTURE_DOC_RELPATH

    cycle = 1
    if isinstance(_prev, dict):
        cycle = int(_prev.get("cycle", 1))
        if findings is None:
            findings = _prev.get("findings")
    elif isinstance(_prev, StepResult) and isinstance(_prev.data, dict):
        cycle = int(_prev.data.get("cycle", 1))
        if findings is None:
            findings = _prev.data.get("findings")

    red_log_path = scratchpad / _red_log_relpath(cycle)

    # GH496 §2.2b — delta-retry branch: cycle>=2 + findings + gate ON drops
    # the full cycle-1 scaffold and returns a compact revision-only prompt
    # (mirrors GH443 phase_45_spec.py:690-720).
    delta_enabled = get_config().gate_enabled("HAL_IMPL_DELTA_RETRY")
    if cycle >= 2 and findings and delta_enabled:
        delta = build_impl_delta_retry_prompt(
            str(spec_path), cycle, findings,
            RED_COLLECTABILITY_RULE, RED_OUTPUT_MARKER_BLOCK,
        )
        prompt = (
            delta + "\n\n"
            + _worktree_edit_boundary_block(_resolve_worktree_root(ctx, scratchpad))
            + "\n\n" + _get_out_of_role_block()
        )
        return StepResult(
            status="ok",
            data={
                "prompt": prompt,
                "log_path": str(red_log_path),
                "spec_path": str(spec_path),
                "spec_present": spec_path.is_file(),
                "arch_present": arch_path.is_file(),
                "prompt_bytes": len(prompt.encode("utf-8")),
                "cycle": cycle,
                "delta_retry": True,
            },
            duration_ms=0,
            step_name="build_red_prompt",
        )

    parts: list[str] = []
    role = _maybe_role_template(ctx)
    if role:
        parts.append(role.rstrip())
        parts.append("")
    parts.append(_read_first_block(scratchpad))
    parts.append("")
    parts.append(
        "ROLE: You are the RED worker. You write FAILING tests that define the "
        "feature's behavior — your output IS the test files plus a final report "
        "line, not a narrative report about testing. Stay strictly within the "
        "SPEC's Acceptance Criteria + Behavior. If a test passes against the "
        "empty/current code, the test is wrong — fix the test, never the spec. "
        "Open files yourself — do NOT trust summaries."
    )
    parts.append("")
    parts.append("FEATURE REQUEST:")
    parts.append(ctx.question or "(no feature request provided)")
    parts.append("")
    if spec_path.is_file():
        parts.append(f"SPEC (read this file): {spec_path}")
    else:
        parts.append(f"SPEC: (none at {spec_path} — phase 4.5 missing; STATUS=block)")
    if arch_path.is_file():
        parts.append(f"ARCHITECTURE (read this file): {arch_path}")
    parts.append("")
    if spec_path.is_file():
        try:
            spec_text = spec_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            spec_text = None
        if spec_text is not None:
            if _spec_wants_1p(spec_text):
                parts.append(
                    "RULES §1p (bash-3.2, 157DA4CA):\n"
                    "  a. hoist unquoted `=~` regex into a variable before the `[[ ]]` test.\n"
                    "  b. no top-level `local` outside a function.\n"
                    "  c. use `tr` for lowercase, not `${var,,}`.\n"
                    "  d. use `grep -E/-i`, not `grep -P`.\n"
                    "  e. use alnum char-classes (`[[:alnum:]]`), not `\\w`.\n"
                    "  f. `find /tmp/` with trailing slash (macOS `/tmp` is a symlink).\n"
                    "  g. strip fractional seconds before `fromdateiso8601`.\n"
                    "  h. `[[ -r \"$f\" ]]` BEFORE any file-read redirection under errexit "
                    "(+ a missing-file test case).\n"
                )
                parts.append("")
            if _spec_wants_1i(spec_text):
                parts.append(
                    "RULES §1i (singleton/time ACs, 8CA8D54C):\n"
                    "  - pre-stage singletons/timestamps in fixtures before invoking the UUT;\n"
                    "    never race the wall clock.\n"
                    "  - time-dependent assertions use injected/frozen time or pre-staged files,\n"
                    "    not sleep-and-hope.\n"
                )
                parts.append("")
            if _spec_wants_py_gates(spec_text):
                parts.append(
                    "RULES python RED gates (GH596, mechanically enforced post-write):\n"
                    "  - STUB-PASSABILITY (7AD3D393, terminal E_RED_STUB_PASSABLE): NEVER patch/patch.object a symbol\n"
                    "    you from-import'ed as the UUT in the same file — that is a vacuous RED. Mock the UUT's\n"
                    "    DEPENDENCIES instead, or use the module-attr form (`import mod; mod.uut`) which is exempt.\n"
                    "  - §1q (FBCF22FA, terminal): no spec_from_file_location/exec_module in tests or conftest —\n"
                    "    use conftest-import-time singleton + swap-pin instead.\n"
                    "  - MASS-DELETION (GH282, terminal): do not delete existing test lines beyond the spec's\n"
                    "    authorized-test-edits block; large deletions inside allowlisted paths block the build.\n"
                    "  - SUITE-SAFETY (585E30E3, terminal): no sys.path.insert / os.chdir / global-state mutation\n"
                    "    at module top level in test files — suite-unsafe patterns block the build.\n"
                )
                parts.append("")
            digest = _red_lint_prompt_digest(spec_text)
            if digest:
                parts.append(digest)
                parts.append("")
    parts.append(
        "RULES:\n"
        "  - Every acceptance criterion in the SPEC → at least one test case.\n"
        "  - Every behavior item / edge case in the SPEC → at least one test case.\n"
        "  - Run the test suite and confirm tests FAIL. If any test passes, fix the test.\n"
        "  - For threshold-based assertions, compute the actual value on real data first.\n"
        "  - You MAY use Edit/Write to create test files. Do NOT modify the spec.\n"
        "  - RED_DISALLOW_SUBAGENTS: do not spawn nested Agent subagents (no Task tool calls). Use Read/Write/Edit/Bash/Grep directly. Nested subagents cause harness-latency stalls and Phase 5 timeouts.\n"
        f"{RED_COLLECTABILITY_RULE}"
    )
    parts.append(_get_producer_anti_fab_prompt())
    cfg = ctx.org_config or {}
    try:
        parts.append(_get_security_fragment(cfg))
    except FileNotFoundError as e:
        return StepResult(
            status="error", data=None, duration_ms=0,
            step_name="build_red_prompt",
            error=f"security fragment missing — build-critical; fail closed: {e}",
            error_code="E_SEC_FRAGMENT_MISSING",
        )
    parts.append(_RED_STABLE_PREFIX)
    parts.append(_get_behavioral_rubric())
    parts.append(RED_OUTPUT_MARKER_BLOCK)
    parts.append("")
    parts.append(_worktree_edit_boundary_block(_resolve_worktree_root(ctx, scratchpad)))

    if findings:
        parts.append("")
        parts.append(f"## REVISION (cycle {cycle} — address validator findings)")
        parts.append("")
        parts.append(findings)
        parts.append("")

    prompt = "\n".join(parts) + "\n\n" + _get_out_of_role_block()
    _standards_block = get_standards_context(ctx)
    if _standards_block:
        prompt = _standards_block + prompt
    return StepResult(
        status="ok",
        data={
            "prompt": prompt,
            "log_path": str(red_log_path),
            "spec_path": str(spec_path),
            "spec_present": spec_path.is_file(),
            "arch_present": arch_path.is_file(),
            "prompt_bytes": len(prompt.encode("utf-8")),
            "cycle": cycle,
            "stable_prefix": _RED_STABLE_PREFIX,
        },
        duration_ms=0,
        step_name="build_red_prompt",
    )


# ─── Step 2: invoke RED LLM ──────────────────────────────────────────────────


def _invoke_red_llm(ctx, prev) -> StepResult:
    if not isinstance(prev, StepResult) or not isinstance(prev.data, dict):
        return StepResult(
            status="error", data=None, duration_ms=0,
            step_name="invoke_red_llm",
            error="prev step did not produce a prompt",
            error_code="E_MISSING_PREV_DATA",
        )
    cfg = ctx.org_config or {}
    complexity = str(cfg.get("complexity") or "").upper()
    # A3398552: SIMPLE tier uses Haiku for RED to save 5-7 min/build; FEATURE/COMPLEX stay Sonnet.
    red_default = _tier_haiku_model() if complexity == "SIMPLE" else _default_red_model()
    resolved_model = _resolve_model(cfg, "red_model", red_default)
    result = invoke_llm_subprocess(
        prompt=prev.data["prompt"],
        model=resolved_model,
        timeout_sec=_resolve_red_timeout_sec(cfg),
        step_name="invoke_red_llm",
        extra_data={
            "log_path": prev.data["log_path"],
            "spec_path": prev.data["spec_path"],
            "cycle": prev.data.get("cycle", 1),
        },
        allowed_tools=["Read", "Write", "Edit", "Bash", "Grep", "Glob"],
        stable_prefix=prev.data.get("stable_prefix", ""),
    )
    if result.status == "ok":
        # γ cleanup 8.5 (A4461B8F): _parse_red_test_paths deleted; red_test_paths
        # is no longer derived from LLM output. _commit_red_tests uses git_diff
        # as the sole source of truth. Set to None so downstream telemetry
        # (phase_5_red_artifact) records that no LLM marker was parsed.
        result.data["red_test_paths"] = None
        # F3 cross-tree edit guard (A4479061): observability only, no auto-revert.
        scratchpad = _resolve_scratchpad(ctx)
        worktree_root = _resolve_worktree_root(ctx, scratchpad)
        result = _maybe_emit_cross_tree_warning(result, worktree_root)
    return result


# ─── Step 3: write RED artifact ──────────────────────────────────────────────


def _write_red_artifact(ctx, prev) -> StepResult:
    if not isinstance(prev, StepResult) or not isinstance(prev.data, dict):
        return StepResult(
            status="error", data=None, duration_ms=0,
            step_name="write_red_artifact",
            error="prev step did not produce raw_response",
            error_code="E_MISSING_PREV_DATA",
        )
    raw = prev.data["raw_response"]
    log_path = Path(prev.data["log_path"])
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(raw, encoding="utf-8")
    # #270: prefer any caller-provided red_test_paths; only derive+persist from
    # the working tree when absent (the γ-cleanup prod case where prev.data is
    # None) AND a ctx is available. Guards against clobbering an injected value
    # and against _resolve_scratchpad(None) when called without a ctx.
    red_test_paths = prev.data.get("red_test_paths") or []
    if not red_test_paths and ctx is not None:
        git_cwd = _resolve_git_cwd(ctx, prev)
        derived = _derive_red_paths_via_git_diff(git_cwd)
        if derived:
            red_test_paths = derived
            _persist_red_test_paths(_resolve_scratchpad(ctx), derived)
    if not red_test_paths:
        logger.warning("write_red_artifact: red_test_paths is absent or empty; commit step will fail")
    _emit_safe("phase_5_red_artifact", {
        "red_log_path": str(log_path),
        "red_test_paths": red_test_paths,
        "cycle": prev.data.get("cycle", 1),
        "red_bytes_written": len(raw.encode("utf-8")),
    })
    return StepResult(
        status="ok",
        data={
            "red_log_path": str(log_path),
            "spec_path": prev.data["spec_path"],
            "red_bytes_written": len(raw.encode("utf-8")),
            "cycle": prev.data.get("cycle", 1),
            "red_test_paths": red_test_paths,
        },
        duration_ms=0,
        step_name="write_red_artifact",
    )


# ─── Step 4: commit RED tests (capture pre-RED baseline for integrity) ───────


def _build_red_commit_message(cycle: int, paths: list[str], git_cwd: str) -> str:
    """Construct an enriched RED-commit message (B1AAACFB-sub1).

    Subject (line 1, must remain greppable):
        build: red cycle {cycle} tests [{branch}]
    or, if branch detection fails:
        build: red cycle {cycle} tests

    Body lines (basenames; first 3 + ``...+N more`` when >5):
        Files: <basename1>, <basename2>, <basename3> ...+M more

    All operator-readable. Tooling that pattern-matches the canonical
    ``build: red cycle N tests`` prefix on the subject line still works.
    """
    branch = ""
    try:
        rb = git_port.git_read(
            ["rev-parse", "--abbrev-ref", "HEAD"],
            cwd=git_cwd,
            timeout=10,
        )
        if rb.returncode == 124:
            branch = ""
        elif rb.returncode == 0:
            cand = rb.stdout.strip()
            # 'HEAD' means detached — not a useful name.
            if cand and cand != "HEAD":
                branch = cand
    except OSError:
        branch = ""

    subject = f"build: red cycle {cycle} tests"
    if branch:
        subject = f"{subject} [{branch}]"

    basenames = [Path(p).name for p in paths]
    if len(basenames) > 5:
        head = ", ".join(basenames[:3])
        body_files = f"Files: {head} ...+{len(basenames) - 3} more"
    else:
        body_files = "Files: " + ", ".join(basenames)

    return f"{subject}\n\n{body_files}\n"


# Canonical `## Files` / `... files in scope ...` parser moved to
# lib.run_allowlist (§1g single source, agreement 1DA29C33). Re-exported
# (not copied) so this module attribute stays an identity match with the
# canonical function — used by `_commit_red_tests` to enforce spec-declared
# RED scope (agreement AD14A3ED L1, defense-in-depth against cross-session
# checkout pollution).
from lib.run_allowlist import parse_spec_files_allowlist as _parse_spec_files_allowlist  # noqa: E402

# GH436 §2.3: same re-export pattern — spec-§5 authorized-test-edits
# whitelist parser, canonical in lib.run_allowlist (§1g single source).
from lib.run_allowlist import parse_authorized_test_edits as _parse_authorized_test_edits  # noqa: E402
# GH639 (7C0FDE44): reason-aware sibling of _parse_authorized_test_edits.
from lib.run_allowlist import parse_authorized_test_edits_with_reasons as _parse_authorized_test_edits_with_reasons  # noqa: E402

# GH569: same re-export pattern — derived test-scope helpers, canonical in
# lib.run_allowlist (§1g single source).
from lib.run_allowlist import is_test_shaped as _is_test_shaped  # noqa: E402
from lib.run_allowlist import derived_test_scope_match as _derived_test_scope_match  # noqa: E402


def _path_matches_allowlist(path: str, allowlist: "list[str]") -> bool:
    """Return True if path matches any entry in allowlist (exact OR fnmatch glob).

    Exact match short-circuits before fnmatch to ensure literal paths containing
    glob meta-chars (`[`, `?`, `*`) match themselves before fnmatch interprets
    them as glob patterns.
    """
    import fnmatch as _fnmatch
    for entry in allowlist:
        if path == entry:
            return True
        if _fnmatch.fnmatch(path, entry):
            return True
    return False


# GH483: GREEN-complete resume seam (§2.1) — detect/persist/read helpers.


def _detect_green_complete_resume(prev_data: dict, git_cwd: str) -> list:
    """Pure detection: does the working tree already carry a GREEN
    implementation matching this RED cycle's spec allowlist? Fail-closed to
    `[]` on gate-off, missing spec allowlist, missing red_commit_sha, or any
    git error — the legacy E_RED_NOT_FAILING path takes over in all those
    cases.
    """
    if not get_config().gate_enabled("HAL_GREEN_COMPLETE_RESUME_GATE"):
        return []
    spec_path = prev_data.get("spec_path")
    allowlist = _parse_spec_files_allowlist(spec_path)
    if not allowlist:
        return []
    red_sha = prev_data.get("red_commit_sha")
    if not red_sha:
        return []
    try:
        changed = git_diff_files(red_sha, git_cwd, untracked=True, segment_filter=None)
    except Exception:  # noqa: BLE001
        return []
    import fnmatch as _fnmatch
    _TEST_PATTERNS = ("test_*.py", "*_test.py", "*.test.ts", "*.test.sh")
    _TEST_SEGMENTS = ("tests/", "__tests__/")
    kept: list = []
    for p in changed:
        filename = p.split("/")[-1] if "/" in p else p
        normalized = p.replace("\\", "/")
        is_test = any(_fnmatch.fnmatch(filename, pat) for pat in _TEST_PATTERNS)
        if not is_test:
            is_test = any(seg in normalized for seg in _TEST_SEGMENTS)
        if is_test:
            continue
        if _path_matches_allowlist(p, allowlist):
            kept.append(p)
    return sorted(kept)


def _persist_green_complete_resume(scratchpad, red_sha: str, paths: list) -> bool:
    """Write the resume marker JSON. OSError propagates to the caller."""
    ref_path = Path(scratchpad) / GREEN_COMPLETE_RESUME_RELPATH
    ref_path.parent.mkdir(parents=True, exist_ok=True)
    ref_path.write_text(json.dumps({"red_commit_sha": red_sha, "paths": paths}))
    return True


def _read_green_complete_resume(scratchpad, red_commit_sha):
    """Read+validate the resume marker. Returns `paths` iff the file exists,
    parses as a JSON dict, its `red_commit_sha` matches the argument, and
    `paths` is a list of str. Never raises — returns None on any mismatch.
    """
    ref_path = Path(scratchpad) / GREEN_COMPLETE_RESUME_RELPATH
    try:
        obj = json.loads(ref_path.read_text())
    except Exception:  # noqa: BLE001
        return None
    if not isinstance(obj, dict):
        return None
    if obj.get("red_commit_sha") != red_commit_sha:
        return None
    paths = obj.get("paths")
    if not isinstance(paths, list) or not all(isinstance(p, str) for p in paths):
        return None
    return paths


def _resolve_git_cwd(ctx, prev=None) -> str:
    """Single source of truth for the build's git working dir (§1g, DC6BD331, GH381).
    Thin delegate onto lib.git_cwd.resolve_git_cwd. Precedence: org_config['git_cwd']
    -> prev.data['git_cwd'] -> org_config['current_worktree_path'] -> scratchpad-climb
    to enclosing .git -> Path.cwd(). Never returns None.
    All phase_5 sites MUST use this — do not re-derive inline."""
    cfg = getattr(ctx, "org_config", None) or {}
    prev_data = getattr(prev, "data", None)
    prev_data = prev_data if isinstance(prev_data, dict) else None
    return resolve_git_cwd(cfg, prev_data)


def _red_mass_deletion_violations(red_paths, base_sha, git_cwd, max_deleted_lines):
    """GH282: detect RED-agency mass deletions inside allowlisted paths.

    Violation iff deleted >= max_deleted_lines AND deleted*2 >= base_lines
    (>=50% of the pre-RED file deleted). Fail-open on detection errors —
    warn-only rollout must never brick the build (§1n, spec 2026-07-10
    GH282_red_mass_deletion_guard).
    """
    if not base_sha:
        return [], "no_base_sha"
    diff = git_port.git_read(
        ["diff", "--numstat", base_sha, "--", *red_paths], cwd=git_cwd, timeout=30,
    )
    if diff.returncode != 0:
        return [], "numstat_failed"
    try:
        violations = []
        for line in diff.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) != 3:
                continue
            added_s, deleted_s, path = parts
            if deleted_s == "-" or added_s == "-":
                continue  # binary file
            deleted = int(deleted_s)
            if deleted <= 0:
                continue
            show = git_port.git_read(
                ["show", f"{base_sha}:{path}"], cwd=git_cwd, timeout=30,
            )
            if show.returncode != 0:
                continue  # new file, absent at base — skip
            base_lines = len(show.stdout.splitlines())
            if deleted >= max_deleted_lines and deleted * 2 >= base_lines:
                violations.append({"path": path, "deleted": deleted, "base_lines": base_lines})
        return violations, None
    except Exception:
        return [], "numstat_failed"


_MASS_DELETION_ALLOW_PRAGMA = "# red-mass-deletion: allow"


def _has_mass_deletion_allow_pragma(abs_path: str) -> bool:
    """GH282 escape, symmetric to `# 1q: allow` / `# stub-passability: allow`:
    a RED that legitimately mass-deletes a test file authorizes it by leaving
    the literal token `# red-mass-deletion: allow` anywhere in the file's
    post-RED content. Fail-CLOSED on read error (unreadable -> not exempted ->
    the deletion still counts as a violation)."""
    try:
        return _MASS_DELETION_ALLOW_PRAGMA in Path(abs_path).read_text(errors="replace")
    except OSError:
        return False


def _commit_red_tests(ctx, prev) -> StepResult:
    # Accept duck-typed prev objects (StepResult or MagicMock in tests) as long as
    # they have a .data dict.  isinstance(prev, StepResult) is checked first so
    # production callers keep the strict path; duck-type fallback is for test fixtures.
    _prev_data = getattr(prev, "data", None)
    if not isinstance(_prev_data, dict) and not (isinstance(prev, StepResult) and isinstance(prev.data, dict)):
        return StepResult(status="error", data=None, duration_ms=0, step_name="commit_red_tests",
                          error="prev step did not produce data", error_code="E_MISSING_PREV_DATA")
    red_test_paths = prev.data.get("red_test_paths", "<missing>")

    # ── test_only mode opt-out (885AAE33) ─────────────────────────────────────
    # When the spec file carries `<!-- engine-mode: test_only -->` on line 1 or 2,
    # skip git operations entirely and return a no-op ok result.  This handles
    # builds whose intent is a test-only patch (fix lives in tests, not in prod).
    _spec_path_raw = prev.data.get("spec_path")
    if resolve_engine_mode(_spec_path_raw if isinstance(_spec_path_raw, str) else None, ctx) == "test_only":
        _emit_safe(
            "commit_red_tests_skipped",
            {"phase": 5, "step": "commit_red_tests", "reason": "test_only_mode"},
        )
        return StepResult(
            status="ok", duration_ms=0, step_name="commit_red_tests",
            data={**prev.data, "red_test_paths": [], "red_commit_sha": None,
                  "commit_red_tests_skipped": "test_only_mode"},
        )
    # ── end test_only block ───────────────────────────────────────────────────

    # ── disk_truth: git-first path enumeration (γ cleanup 8.5 / A4461B8F) ───────
    # git_diff_files is the single source of truth for red test file paths.
    # LLM output and ctx-json override are both ignored — only git matters.
    _git_cwd_early = _resolve_git_cwd(ctx, prev)
    # 8D162396: resolve scratchpad early so _resolve_frozen_pre_red_sha can
    # read the cycle-1 frozen boundary on re-entry (W1).
    scratchpad = _resolve_scratchpad(ctx)
    git_paths = _derive_red_paths_via_git_diff(
        _git_cwd_early,
        frozen_sha=_resolve_frozen_pre_red_sha(scratchpad, _git_cwd_early),
    )

    if git_paths:
        # git is non-empty — emit drift event unconditionally so the event
        # remains observable post-cleanup (llm_paths always [] after γ).
        _emit_safe(
            "red_files_drift",
            {
                "llm_paths": [],
                "git_paths": list(git_paths),
                "chosen": "git",
                "phase": 5,
            },
            severity="warning",
        )
        red_test_paths = git_paths
    else:
        persisted = _read_red_test_paths(scratchpad)
        recovered = _red_paths_present_on_disk(persisted, _git_cwd_early)
        if recovered:
            _emit_safe(
                "red_files_recovered_via_persisted_paths",
                {"present": recovered, "chosen": "persisted_paths", "phase": 5},
                severity="warning",
            )
            red_test_paths = recovered
        else:
            present = _red_paths_present_on_disk(prev.data.get("red_test_paths"), _git_cwd_early)
            if present:
                _emit_safe(
                    "red_files_recovered_via_disk_fallback",
                    {"present": present, "chosen": "disk_fallback", "phase": 5},
                    severity="warning",
                )
                red_test_paths = present
            else:
                return StepResult(
                    status="error", data=None, duration_ms=0, step_name="commit_red_tests",
                    error=(
                        f"RED test files not found: git diff vs frozen pre-red SHA is empty "
                        f"AND no ctx red_test_paths exist on disk under git_cwd={_git_cwd_early} "
                        f"(frozen ref may be stale, RED was never written, or this is the wrong "
                        f"repo — set org_config.git_cwd to the project root, GH#381)."
                    ),
                    error_code="E_RED_NO_MARKER",
                )
    # ── end disk_truth block ───────────────────────────────────────────────────
    if red_test_paths == []:
        return StepResult(status="error", data=None, duration_ms=0, step_name="commit_red_tests",
                          error="RED LLM output had 'Files: []' marker but listed no files", error_code="E_RED_EMPTY_FILES")
    if red_test_paths == "<missing>":
        return StepResult(status="error", data=None, duration_ms=0, step_name="commit_red_tests",
                          error="red_test_paths key missing from prev.data", error_code="E_MISSING_PREV_DATA")
    # ── AD14A3ED L1 — spec-scope allowlist gate ──
    # flip-by:2026-08-01 (Refs #569: telemetry 66/68 violations incl. populated allowlists —
    # ## Files semantics does not cover RED test paths; flip blocked until #569 lands + ≥5 clean ships).
    _enforce_scope = bool((ctx.org_config or {}).get("enforce_red_scope_allowlist", False))
    _allowlist = _parse_spec_files_allowlist(_spec_path_raw)  # None if path None / missing / unreadable / no ## Files
    if _allowlist is None:
        _emit_safe("red_scope_check", {
            "phase": 5,
            "step": "commit_red_tests",
            "allowlist_size": 0,
            "red_paths_n": len(red_test_paths),
            "violations_n": 0,
            "enforced": _enforce_scope,
            "no_allowlist_found": True,
        })
    else:
        # GH569: effective allowlist = ## Files (`_allowlist`, unchanged for
        # `allowlist_size` telemetry comparability) UNION authorized-test-edits
        # UNION derived test-scope (test-shaped RED path co-located with — or
        # up to 2 ancestor levels above — a ## Files entry's dirname).
        _authorized_edits = (
            _parse_authorized_test_edits(_spec_path_raw)
            if isinstance(_spec_path_raw, str) and _spec_path_raw
            else []
        )
        _violations: "list[str]" = []
        _authorized_test_edits_n = 0
        _derived_matched_n = 0
        for p in red_test_paths:
            # authorized-test-edits checked BEFORE the direct ## Files match:
            # the marker block nests inside the ## Files section in real specs,
            # so its bullets also land in `_allowlist` — checking direct first
            # would keep `authorized_test_edits_n` permanently 0.
            if _path_matches_allowlist(p, _authorized_edits):
                _authorized_test_edits_n += 1
                continue
            if _path_matches_allowlist(p, _allowlist):
                continue
            if _derived_test_scope_match(p, _allowlist):
                _derived_matched_n += 1
                continue
            _violations.append(p)
        _emit_safe("red_scope_check", {
            "phase": 5,
            "step": "commit_red_tests",
            "allowlist_size": len(_allowlist),
            "red_paths_n": len(red_test_paths),
            "violations_n": len(_violations),
            "enforced": _enforce_scope,
            "authorized_test_edits_n": _authorized_test_edits_n,
            "derived_matched_n": _derived_matched_n,
        })
        if _violations and _enforce_scope:
            return StepResult(
                status="error", data=None, duration_ms=0, step_name="commit_red_tests",
                error=f"RED scope violation: paths outside spec ## Files allowlist: {', '.join(sorted(_violations))}",
                error_code="E_RED_SCOPE_VIOLATION",
            )
    # ── end AD14A3ED gate ──
    # ── GH282 — RED mass-deletion guard ──
    if get_config().gate_enabled("HAL_RED_MASS_DELETION_GATE"):        # kill-switch, default ON
        _mdl_max = int_value("HAL_RED_MASS_DELETION_MAX_LINES", 120)
        _mdl_enforce = get_config().flag("HAL_RED_MASS_DELETION_ENFORCE")
        # warn-only rollout: HAL_RED_MASS_DELETION_ENFORCE default OFF —
        # flip-by:2026-07-24 Refs #282 (rollout-completion-check token)
        _mdl_base = _resolve_frozen_pre_red_sha(scratchpad, _git_cwd_early)
        _mdl_all, _mdl_skip = _red_mass_deletion_violations(
            red_test_paths, _mdl_base, _git_cwd_early, _mdl_max)
        # GH282 pragma-escape: a violation whose post-RED file carries the
        # literal `# red-mass-deletion: allow` token is authorized -> exempt.
        _mdl_exempted = []
        _mdl_violations = []
        for _v in _mdl_all:
            if _has_mass_deletion_allow_pragma(str(Path(_git_cwd_early) / _v["path"])):
                _mdl_exempted.append(_v)
            else:
                _mdl_violations.append(_v)
        _emit_safe("red_mass_deletion_check", {
            "phase": 5, "step": "commit_red_tests",
            "red_paths_n": len(red_test_paths),
            "violations": _mdl_violations, "violations_n": len(_mdl_violations),
            "exempted": _mdl_exempted, "exempted_n": len(_mdl_exempted),
            "max_deleted_lines": _mdl_max, "enforced": _mdl_enforce,
            "skip_reason": _mdl_skip,
        })
        if _mdl_violations and _mdl_enforce:
            _emit_safe("red_mass_deletion_blocked", {
                "phase": 5, "step": "commit_red_tests",
                "violations": _mdl_violations,
            }, severity="error")
            return StepResult(
                status="error", data=None, duration_ms=0, step_name="commit_red_tests",
                error=("RED mass-deletion blocked: " +
                       ", ".join(f"{v['path']} (-{v['deleted']}/{v['base_lines']})"
                                 for v in _mdl_violations)),
                error_code="E_RED_MASS_DELETION", recoverable=False,
            )
    # ── end GH282 gate ──
    cfg = ctx.org_config or {}
    scratchpad = _resolve_scratchpad(ctx)
    git_cwd = _resolve_git_cwd(ctx, prev)
    cycle = int(prev.data.get("cycle", 1))

    gd = git_port.git_read(
        ["rev-parse", "--git-dir"], cwd=git_cwd, timeout=30,
    )
    if gd.returncode == 0:
        gd_path = gd.stdout.strip()
        git_dir = Path(gd_path) if Path(gd_path).is_absolute() else Path(git_cwd) / gd_path
        bad_state_paths = [
            git_dir / "MERGE_HEAD",
            git_dir / "REBASE_HEAD",
            git_dir / "rebase-merge",
            git_dir / "rebase-apply",
            git_dir / "CHERRY_PICK_HEAD",
        ]
        if any(p.exists() for p in bad_state_paths):
            return StepResult(
                status="error", data=None, duration_ms=0,
                step_name="commit_red_tests",
                error="git working tree in MERGE/REBASE/CHERRY-PICK state — cannot commit RED tests automatically",
                error_code="E_GIT_BAD_STATE",
            )

    st = git_port.git_read(
        ["status", "--porcelain"], cwd=git_cwd, timeout=30
    )
    if st.returncode != 0:
        return StepResult(status="error", data=None, duration_ms=0, step_name="commit_red_tests",
                          error=f"git status: {st.stderr[:500]}", error_code="E_GIT_COMMIT_FAILED")
    pre_rev = git_port.git_read(
        ["rev-parse", "HEAD"], cwd=git_cwd, timeout=30
    )
    if pre_rev.returncode != 0:
        return StepResult(status="error", data=None, duration_ms=0, step_name="commit_red_tests",
                          error=f"git rev-parse: {pre_rev.stderr[:500]}", error_code="E_GIT_COMMIT_FAILED")
    pre_red_sha = pre_rev.stdout.strip()
    if not pre_red_sha or len(pre_red_sha) != 40 or not all(c in "0123456789abcdef" for c in pre_red_sha):
        return StepResult(status="error", data=None, duration_ms=0, step_name="commit_red_tests",
                          error=f"git rev-parse returned invalid SHA: {pre_red_sha!r}",
                          error_code="E_GIT_COMMIT_FAILED")
    # GH637: persist the frozen pre-red boundary BEFORE any git mutation.
    # pre_red_sha here is HEAD *before* the RED commit. Persisting it now
    # (write-once) closes the crash window between the RED commit and the
    # persist: on resume the ref already holds the true pre-red parent, so
    # _resolve_frozen_pre_red_sha never falls back to the advanced HEAD (= the
    # RED commit itself) → no vacuous GREEN (tamper-scan against itself).
    try:
        _persist_pre_red_ref(scratchpad, pre_red_sha)
    except OSError as e:
        return StepResult(status="error", data=None, duration_ms=0, step_name="commit_red_tests",
                          error=f"scratchpad write failed: {e}", error_code="E_GIT_COMMIT_FAILED")
    # Probe gitignore status of red_test_paths — paths inside .gitignore'd dirs
    # cause `git add` to fail atomically. Filter them out for a degraded-but-OK
    # commit, or skip the commit entirely if all paths are ignored.
    trackable_paths = _filter_phantom_deleted_paths(_filter_gitignored_paths(red_test_paths, git_cwd), git_cwd)
    red_commit_sha = pre_red_sha
    if not trackable_paths:
        logger.warning(
            "all red_test_paths gitignored; skipping git add/commit, "
            "using pre_red_sha as red_commit_sha (degraded mode)",
        )
    elif st.stdout.strip():
        add, add_outcome = _git_op_with_lock_retry(
            ["git", "add", "--"] + trackable_paths, cwd=git_cwd, timeout=30
        )
        if add_outcome == "lock_persisted":
            return StepResult(status="error", data=None, duration_ms=0, step_name="commit_red_tests",
                              error=f"git add: index.lock contention persisted after 3 attempts: {add.stderr[:500]}",
                              error_code="E_GIT_LOCKED")
        if add_outcome == "non_lock_error":
            return StepResult(status="error", data=None, duration_ms=0, step_name="commit_red_tests",
                              error=f"git add: {add.stderr[:500]}", error_code="E_GIT_COMMIT_FAILED")
        if add_outcome == "timeout":
            return StepResult(status="error", data=None, duration_ms=0, step_name="commit_red_tests",
                              error="git add: timeout after 30s", error_code="E_GIT_TIMEOUT")
        if add_outcome == "os_error":
            return StepResult(status="error", data=None, duration_ms=0, step_name="commit_red_tests",
                              error="git add: OS error invoking subprocess", error_code="E_GIT_OS_ERROR")
        if not _paths_have_staged_changes(git_cwd, trackable_paths):
            _emit_safe(
                "commit_red_tests_idempotent_skip",
                {"phase": 5, "step": "commit_red_tests",
                 "reason": "no_staged_diff", "pre_red_sha": pre_red_sha},
            )
            # red_commit_sha stays = pre_red_sha (set earlier); no new commit.
        else:
            commit_message = _build_red_commit_message(cycle, trackable_paths, git_cwd)
            cm, cm_outcome = _git_op_with_lock_retry(
                ["git", "commit", "-o", "-m", commit_message, "--", *trackable_paths],
                cwd=git_cwd, timeout=30,
            )
            if cm_outcome == "lock_persisted":
                return StepResult(status="error", data=None, duration_ms=0, step_name="commit_red_tests",
                                  error=f"git commit: index.lock contention persisted after 3 attempts: {cm.stderr[:500]}",
                                  error_code="E_GIT_LOCKED")
            if cm_outcome == "non_lock_error":
                return StepResult(status="error", data=None, duration_ms=0, step_name="commit_red_tests",
                                  error=f"git commit: {cm.stderr[:500]}", error_code="E_GIT_COMMIT_FAILED")
            if cm_outcome == "timeout":
                return StepResult(status="error", data=None, duration_ms=0, step_name="commit_red_tests",
                                  error="git commit: timeout after 30s", error_code="E_GIT_TIMEOUT")
            if cm_outcome == "os_error":
                return StepResult(status="error", data=None, duration_ms=0, step_name="commit_red_tests",
                                  error="git commit: OS error invoking subprocess", error_code="E_GIT_OS_ERROR")
            post_rev = git_port.git_read(
                ["rev-parse", "HEAD"], cwd=git_cwd, timeout=30
            )
            if post_rev.returncode != 0:
                return StepResult(status="error", data=None, duration_ms=0, step_name="commit_red_tests",
                                  error=f"git rev-parse: {post_rev.stderr[:500]}", error_code="E_GIT_COMMIT_FAILED")
            red_commit_sha = post_rev.stdout.strip()
    # GH639 (7C0FDE44): freeze a content-hash manifest over red_test_paths
    # (NEVER trackable_paths — degraded/gitignored mode leaves trackable_paths
    # empty but red_test_paths still holds the gitignored test path; freezing
    # over trackable_paths would yield an empty manifest in exactly the
    # git-diff-blind mode this ship exists to cover).
    _rth_manifest = authored_boundary.compute_red_test_hashes(red_test_paths, git_cwd)
    _rth_persisted = _persist_red_test_hashes(scratchpad, _rth_manifest)
    _emit_safe("red_test_hashes_frozen", {"n": len(_rth_manifest), "persisted": _rth_persisted, "cycle": cycle})
    return StepResult(status="ok", data={**prev.data, "red_commit_sha": red_commit_sha,
                                         "red_test_paths": red_test_paths}, duration_ms=0,
                      step_name="commit_red_tests")


# ─── Step 4b: verify RED fails mechanically (Pillar 3) ───────────────────────


def _venv_pytest(base: str) -> str | None:
    """BF7890C8: Return the path to the venv pytest under base, or None.

    Probes <base>/.venv/bin/pytest then <base>/venv/bin/pytest; returns the
    first that is_file() and os.X_OK. Pure path logic, no subprocess.
    """
    for cand in (
        Path(base) / ".venv" / "bin" / "pytest",
        Path(base) / "venv" / "bin" / "pytest",
    ):
        if cand.is_file() and os.access(cand, os.X_OK):
            return str(cand)
    return None


def _main_checkout_root(git_cwd: str) -> str | None:
    """BF7890C8: Resolve the main checkout root from a (possibly-worktree) git_cwd.

    Runs `git -C <git_cwd> rev-parse --git-common-dir` (timeout=5s, fail-soft).
    Returns the parent of the common .git dir only when git_cwd is genuinely a
    worktree (i.e. root != realpath(git_cwd)); returns None for the main checkout
    itself, non-git dirs, or any subprocess error. Never raises, never hangs.
    """
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
    except (subprocess.CalledProcessError,
            FileNotFoundError, OSError, ValueError):
        return None


# 69AA5237 (2026-05-03): multi-language test routing. Restored from
# CodeForge→HALForge migration regression (2026-02-27). Source preserved in
# Tools/ForgeV2Pipeline.ts:504-519. Routing table — extension → runner argv.
# Returns dict per group: {"argv": [...], "kind": "<lang>"} or None when no
# supported runner matches that path's extension.
def _runner_for_path(path: str, git_cwd: str | None = None) -> dict | None:
    p = path.lower()
    if p.endswith((".ts", ".tsx", ".js")):
        return {"kind": "ts", "argv_prefix": ["bun", "test"]}
    if p.endswith(".swift"):
        return {"kind": "swift", "argv_prefix": ["swift", "test"]}
    if p.endswith(".rs"):
        return {"kind": "rust", "argv_prefix": ["cargo", "test"]}
    if p.endswith(".py"):
        prefix: list[str] = ["python3", "-m", "pytest", "-x", "--tb=no", "-q"]
        hit: str | None = None
        if git_cwd is not None:
            hit = _venv_pytest(git_cwd)
            if hit is None:
                root = _main_checkout_root(git_cwd)
                if root is not None:
                    hit = _venv_pytest(root)
        if hit is not None:
            prefix = [hit, "-x", "--tb=no", "-q"]
        return {"kind": "py", "argv_prefix": prefix}
    if p.endswith(".sh"):
        return {"kind": "sh", "argv_prefix": ["bash"]}
    return None


def _collect_probe_argv(red_path: str, git_cwd: str | None = None) -> list[str] | None:
    """4238DD14: Return the argv for the collectability probe for red_path.

    Dispatches based on the test runner group detected by _runner_for_path:
    - "py"  → [<pytest-prefix>..., "--collect-only", "-q", red_path]
    - "sh"  → ["bash", "-n", red_path]
    - None  → None (unsupported extension — caller routes to E_RED_NOT_EXECUTABLE)
    """
    runner = _runner_for_path(red_path, git_cwd)
    if runner is None:
        return None
    if runner["kind"] == "sh":
        return ["bash", "-n", red_path]
    if runner["kind"] == "py":
        # GH714: pin --rootdir to the red file's own directory so pytest does not
        # auto-discover a high rootdir and recursively walk unrelated filesystem
        # trees (296k nodes / 1.19M stat calls / 82s measured) when red_path's dir
        # diverges from cwd — which trips the 30s collect cap in _check_red_executable.
        # rootdir does not narrow conftest scope (confcutdir follows the located ini),
        # so collectability detection is unchanged; it only bounds the collection walk.
        rootdir = str(Path(red_path).parent)
        return runner["argv_prefix"] + ["--rootdir", rootdir, "--collect-only", "-q", red_path]
    # Other language groups (ts, swift, rust) — not supported for collect probe
    return None


def _infer_test_command_for_paths(paths: list[str], git_cwd: str | None = None) -> dict:
    """Group paths by language and return a dispatch plan.

    Returns one of:
      {"groups": [{"kind": str, "argv": [...], "paths": [...]}, ...]}
        — one or more language groups; caller dispatches each.
        Swift/Rust groups omit per-file paths from argv (those runners don't
        accept ad-hoc test-file args). TS/Py groups append paths.
      {"skipped": True, "reason": "..."}
        — none of the paths matched a supported runner.
    """
    groups: dict[str, list[str]] = {}
    unsupported: list[str] = []
    for p in paths:
        r = _runner_for_path(p, git_cwd=git_cwd)
        if r is None:
            unsupported.append(p)
            continue
        groups.setdefault(r["kind"], []).append(p)
    if not groups:
        exts = sorted({Path(p).suffix or "<no-ext>" for p in unsupported})
        return {"skipped": True, "reason": f"no supported test runner for extensions: {','.join(exts)}"}
    plan = []
    for kind, kind_paths in groups.items():
        _r = _runner_for_path(kind_paths[0], git_cwd=git_cwd)
        assert _r is not None  # DC1CB656: type-safety (boy-scout) — paths in groups always had a runner
        prefix = _r["argv_prefix"]
        # Swift/Rust: per-file args don't fit; invoke runner over the package.
        if kind in ("swift", "rust"):
            argv = list(prefix)
        else:
            argv = list(prefix) + list(kind_paths)
        plan.append({"kind": kind, "argv": argv, "paths": kind_paths})
    return {"groups": plan}


def _verify_red_fails_mechanically(ctx, prev) -> StepResult:
    if not isinstance(prev, StepResult) or not isinstance(prev.data, dict):
        return StepResult(
            status="error", data=None, duration_ms=0,
            step_name="verify_red_fails_mechanically",
            error="prev step did not produce data", error_code="E_MISSING_PREV_DATA",
        )
    if prev.data.get("commit_red_tests_skipped") == "test_only_mode":
        _emit_safe(
            "red_verify_skipped_test_only",
            {"phase": 5, "step": "verify_red_fails_mechanically", "reason": "test_only_mode"},
        )
        return StepResult(
            status="ok", data={**prev.data}, duration_ms=0,
            step_name="verify_red_fails_mechanically",
        )
    red_test_paths = prev.data.get("red_test_paths") or []
    if not red_test_paths:
        return StepResult(
            status="error", data=None, duration_ms=0,
            step_name="verify_red_fails_mechanically",
            error="no red_test_paths to verify",
            error_code="E_RED_NO_PATHS",
        )
    cfg = ctx.org_config or {}
    git_cwd = _resolve_git_cwd(ctx, prev)
    plan = _infer_test_command_for_paths(list(red_test_paths), git_cwd=git_cwd)
    if plan.get("skipped"):
        return StepResult(
            status="ok",
            data={**prev.data, "skipped": plan["reason"], "skip_reason": plan["reason"]},
            duration_ms=0,
            step_name="verify_red_fails_mechanically",
        )
    # Pillar 3 contract: ALL groups must individually fail. Any all-passing
    # group = fake RED for that language → error. Track per-group results.
    passing_groups: list[str] = []
    last_stdout = ""
    for group in plan["groups"]:
        argv = group["argv"]
        kind = group["kind"]
        try:
            proc = bounded_run(
                argv, capture_output=True, text=True, cwd=git_cwd, timeout=120,
                env=test_subprocess_env(git_cwd),
            )
        except FileNotFoundError as e:
            # Pytest path keeps its historical error code; other runners share E_TEST_RUNNER_MISSING.
            if kind == "py":
                return StepResult(
                    status="error", data=None, duration_ms=0,
                    step_name="verify_red_fails_mechanically",
                    error=f"pytest binary missing: {e}",
                    error_code="E_PYTEST_MISSING",
                    recoverable=False,
                )
            return StepResult(
                status="error", data=None, duration_ms=0,
                step_name="verify_red_fails_mechanically",
                error=f"{argv[0]} binary missing: {e}",
                error_code="E_TEST_RUNNER_MISSING",
                recoverable=False,
            )
        if proc.returncode == 124:
            # Mirror the FileNotFoundError branching: pytest keeps historical code.
            if kind == "py":
                return StepResult(
                    status="error", data=None, duration_ms=0,
                    step_name="verify_red_fails_mechanically",
                    error=f"pytest exceeded 120s timeout running red_test_paths",
                    error_code="E_RED_PYTEST_TIMEOUT",
                )
            return StepResult(
                status="error", data=None, duration_ms=0,
                step_name="verify_red_fails_mechanically",
                error=f"{argv[0]} exceeded 120s timeout running red_test_paths",
                error_code="E_RED_TEST_RUNNER_TIMEOUT",
            )
        # Telemetry: emit red_test_outcome once per group using disk_truth for parsed counts.
        # Note: this re-runs the test command; future optimization could reuse proc.stdout.
        try:
            dt_result = run_test_command(argv, git_cwd, timeout=120)
            _emit_safe("red_test_outcome", {
                "group": kind,
                "exit_code": dt_result.exit_code,
                "n_passed": dt_result.n_passed,
                "n_failed": dt_result.n_failed,
                "phase": 5,
            }, severity="warning")
        except Exception as e:  # noqa: BLE001
            logger.warning("red_test_outcome telemetry failed for group %s: %s", kind, e)
        last_stdout = proc.stdout
        if proc.returncode == 0:
            passing_groups.append(kind)
    if passing_groups:
        # GH483: crash-resume seam — a prior GREEN may have already written the
        # implementation to the working tree, so RED now passes for a
        # legitimate reason (not a fake RED). Detect + persist a marker so the
        # GREEN LLM step can be skipped downstream; fall through to legacy
        # E_RED_NOT_FAILING on gate-off / no detection / persist failure.
        resume_paths = _detect_green_complete_resume(prev.data, git_cwd)
        resume_red_sha = prev.data.get("red_commit_sha")
        if resume_paths and resume_red_sha:
            try:
                _persist_green_complete_resume(
                    _resolve_scratchpad(ctx), resume_red_sha, resume_paths
                )
            except OSError:
                pass
            else:
                _emit_safe("green_complete_resume_detected", {
                    "phase": 5, "step": "verify_red_fails_mechanically",
                    "paths": resume_paths, "n_paths": len(resume_paths),
                    "passing_groups": passing_groups,
                }, severity="warning")
                return StepResult(
                    status="ok",
                    data={**prev.data, "green_complete_resume": True, "green_resume_paths": resume_paths},
                    duration_ms=0,
                    step_name="verify_red_fails_mechanically",
                )
        kinds = ",".join(passing_groups)
        return StepResult(
            status="error",
            data={"red_test_paths": list(red_test_paths), "stdout_tail": last_stdout[-2000:]},
            duration_ms=0,
            step_name="verify_red_fails_mechanically",
            error=f"RED tests in groups [{kinds}] all passed — RED is fake",
            error_code="E_RED_NOT_FAILING",
            recoverable=False,
        )
    return StepResult(
        status="ok",
        data=dict(prev.data),
        duration_ms=0,
        step_name="verify_red_fails_mechanically",
    )


# ─── GH535/§1a: sibling-test-audit helpers (warn-only rollout) ─────────────

_GH535_SKIP_MODULES = {
    "pytest", "unittest", "unittest.mock", "typing", "pathlib", "os", "sys",
    "json", "re", "dataclasses", "collections", "__future__",
}


def _red_import_symbols(src: str) -> list[str]:
    """Derive public symbol names imported by a RED test file (§2.1)."""
    try:
        tree = ast.parse(src)
    except SyntaxError:
        return []
    symbols: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.module is None or node.module in _GH535_SKIP_MODULES:
            continue
        for alias in node.names:
            name = alias.name
            if name == "*" or name.startswith("_"):
                continue
            if name not in symbols:
                symbols.append(name)
    return symbols[:8]


def _sibling_audit_warn(resolved_paths: list[str], git_cwd: str) -> None:
    """Warn-only §1a sibling-test-audit.sh pass (§2.2). Never alters StepResult."""
    symbols: list[str] = []
    for rp in resolved_paths:
        try:
            src = Path(rp).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for sym in _red_import_symbols(src):
            if sym not in symbols:
                symbols.append(sym)
        if len(symbols) >= 8:
            break
    symbols = symbols[:8]
    if not symbols:
        _emit_safe("red_sibling_audit_skipped", {"phase": 5, "reason": "no_symbols"})
        return

    default_script = Path(__file__).resolve().parents[2] / "sibling-test-audit.sh"
    try:
        script = get_config().path("HAL_SIBLING_AUDIT_BIN", default_script)
    except AttributeError:  # minimal-Protocol providers without .path (9AB32375 isinstance surface)
        script = default_script
    if not script.is_file():
        _emit_safe("red_sibling_audit_skipped", {
            "phase": 5, "reason": "script_missing", "script": str(script),
        })
        return

    try:
        proc = subprocess.run(
            ["bash", str(script), "--substrings", ",".join(symbols), "--glob", "tests/*.py"],
            cwd=git_cwd, capture_output=True, text=True, timeout=30,
        )
    except (subprocess.TimeoutExpired, OSError):
        _emit_safe("red_sibling_audit_skipped", {"phase": 5, "reason": "exec_error"})
        return

    if proc.returncode > 1:
        _emit_safe("red_sibling_audit_skipped", {
            "phase": 5, "reason": f"rc_{proc.returncode}",
        })
        return

    resolved_set = {str(Path(p).resolve()) for p in resolved_paths}
    hits: list[str] = []
    for line in (proc.stdout or "").splitlines():
        fields = line.split("\t")
        if len(fields) < 2:
            continue
        try:
            field0_resolved = str((Path(git_cwd) / fields[0]).resolve())
        except OSError:
            continue
        if field0_resolved in resolved_set:
            continue
        hits.append(line)

    if hits:
        _emit_safe("red_sibling_audit_warn", {
            "phase": 5, "count": len(hits), "hits": hits[:20], "symbols": symbols,
        }, severity="warn")


# ─── Step 4.6: red_lint semgrep gate (C76F6F3C) ─────────────────────────────


def _red_collect_probe(resolved_paths: list[str], git_cwd: str) -> tuple[list[str], str]:
    """GH542/§1q: pytest --co -q probe over .py RED files.
    Returns (violations, skip_reason). violations = ["<path>: <last-lines-of-output>"] on
    collection failure/timeout; skip_reason non-empty when probe not applicable."""
    py_paths = [p for p in resolved_paths if p.endswith(".py")]
    if not py_paths:
        return ([], "no_python_red_files")
    _cfg = get_config()
    _tms = _cfg.timeout_ms("HAL_RED_COLLECT_PROBE_TIMEOUT_MS", 60000) if hasattr(_cfg, "timeout_ms") else 60000
    _probe_timeout_s = _tms / 1000
    try:
        proc = bounded_run(
            [sys.executable, "-m", "pytest", "--co", "-q"] + py_paths,
            capture_output=True, text=True, cwd=git_cwd, timeout=_probe_timeout_s,
        )
    except FileNotFoundError:
        return ([], "pytest_unavailable")
    if proc.returncode == 0:
        return ([], "")
    if proc.returncode == 124:
        return (["collect-probe timeout"], "")
    tail = ((proc.stdout or "") + (proc.stderr or ""))[-400:]
    return ([f"{', '.join(py_paths)}: {tail}"], "")


_PREFLIGHT_RETRY_ELIGIBLE_CODES = frozenset({
    "E_RED_STUB_PASSABLE", "E_RED_1Q_EXEC_IMPORT",
    "E_RED_SUITE_UNSAFE", "E_RED_COLLECT_PROBE",
})


def _preflight_retry_eligible(batch: list[dict]) -> bool:
    """True when EVERY finding in the batch is retryable via delta-retry.
    E_RED_LINT_F1 (semgrep) stays terminal (GH602 §2.1)."""
    return all(f.get("error_code") in _PREFLIGHT_RETRY_ELIGIBLE_CODES for f in batch)


def _collect_red_lint_findings(
    resolved_paths: list[str], git_cwd: str, ctx, cfg,
) -> list[dict]:
    """GH595 §2.1 (§1aa named helper): run the deterministic content-lints
    (suite-safety -> stub-passability -> 1q-exec-import ->
    collect-probe(@enforce)) against resolved_paths, normalizing every hit
    into {path, line, rule, evidence, error_code, recoverable} and returning
    them in canonical order. Emits the SAME per-lint violation / gate_disabled
    events as the legacy sequential path, but never returns early — every
    content-lint runs regardless of earlier hits. semgrep F1 is run
    separately by the caller (_verify_red_lint_rules) so its bounded_run
    call/except-FileNotFoundError contract is source-greppable on that
    function per BFEC3E71 (§1a source-grep contract)."""
    step = "verify_red_lint_rules"
    batch: list[dict] = []

    # ── 1. suite-safety (always-on, no per-lint kill switch today) ──
    for rp in resolved_paths:
        try:
            source = Path(rp).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for v in scan_suite_safety(source):
            lineno_str = v.split(":")[0].strip()
            try:
                lineno = int(lineno_str)
            except ValueError:
                lineno = None
            batch.append({
                "path": rp, "line": lineno, "rule": "suite-safety",
                "evidence": v, "error_code": "E_RED_SUITE_UNSAFE", "recoverable": True,
            })

    # ── 2. stub-passability ──
    if get_config().gate_enabled("HAL_STUB_PASSABILITY_GATE"):
        stub_hits: list[str] = []
        for rp in resolved_paths:
            try:
                src = Path(rp).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            try:
                for f in scan_stub_passability(src):
                    stub_hits.append(f"{rp}:{f.patch_line} '{f.symbol}'")
                    batch.append({
                        "path": rp, "line": f.patch_line, "rule": "stub-passability",
                        "evidence": f.symbol, "error_code": "E_RED_STUB_PASSABLE", "recoverable": False,
                    })
            except SyntaxError:
                continue
        if stub_hits:
            _emit_safe("red_stub_passability_violation",
                       {"phase": 5, "hits": stub_hits}, severity="error")
    else:
        _emit_safe("gate_disabled", {
            "gate": "HAL_STUB_PASSABILITY_GATE", "step": step, "reason": "env_kill_switch",
        })

    # ── 3. §1q exec-import ──
    _re_1q = re.compile(r"spec_from_file_location|exec_module")
    if get_config().gate_enabled("HAL_RED_1Q_GATE"):
        q1_hits: list[str] = []
        for rp in resolved_paths:
            try:
                src = Path(rp).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for q1_lineno, q1_line in enumerate(src.splitlines(), start=1):
                if _re_1q.search(q1_line) and "# 1q: allow" not in q1_line:
                    q1_hits.append(f"{rp}:{q1_lineno}")
                    batch.append({
                        "path": rp, "line": q1_lineno, "rule": "1q-exec-import",
                        "evidence": q1_line.strip(), "error_code": "E_RED_1Q_EXEC_IMPORT", "recoverable": False,
                    })
        if q1_hits:
            _emit_safe("red_1q_exec_import_violation",
                       {"phase": 5, "hits": q1_hits}, severity="error")
    else:
        _emit_safe("gate_disabled", {
            "gate": "HAL_RED_1Q_GATE", "step": step, "reason": "env_kill_switch",
        })

    # ── 4. collect-probe (findings enter batch ONLY under enforce, N6) ──
    if get_config().gate_enabled("HAL_RED_COLLECT_PROBE_GATE"):
        _cp_violations, _cp_skip = _red_collect_probe(resolved_paths, git_cwd)
        _cp_cfg = get_config()
        _cp_enforce = _cp_cfg.flag("HAL_RED_COLLECT_PROBE_ENFORCE") if hasattr(_cp_cfg, "flag") else False
        _emit_safe("red_collect_probe_check", {
            "phase": 5, "step": step, "red_paths_n": len(resolved_paths),
            "violations_n": len(_cp_violations), "violations": _cp_violations,
            "enforced": _cp_enforce, "skip_reason": _cp_skip,
        })
        if _cp_violations and _cp_enforce:
            for v in _cp_violations:
                batch.append({
                    "path": resolved_paths[0] if resolved_paths else "",
                    "line": None, "rule": "collect-probe", "evidence": v,
                    "error_code": "E_RED_COLLECT_PROBE", "recoverable": True,
                })
    else:
        _emit_safe("gate_disabled", {
            "gate": "HAL_RED_COLLECT_PROBE_GATE", "step": step, "reason": "env_kill_switch",
        })

    return batch


def _verify_red_lint_rules_legacy(ctx, prev, step, cfg, git_cwd, resolved_paths) -> StepResult:
    """Legacy sequential first-fail RED-lint path (pre-GH595 semantics),
    preserved verbatim as the HAL_RED_LINT_PREFLIGHT_BATCH=0 fallback."""

    # ── 585E30E3 P1a: AST suite-safety gate (runs BEFORE semgrep early-return) ──
    # Must enforce even on hosts without semgrep installed.
    suite_violations: list[str] = []
    for rp in resolved_paths:
        try:
            source = Path(rp).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for v in scan_suite_safety(source):
            # v is "<lineno>: <token>" — prepend file path for error reporting
            lineno = v.split(":")[0].strip()
            suite_violations.append(f"{rp}:{lineno}")
    if suite_violations:
        offending = ", ".join(suite_violations)
        remediation = (
            "consume tests/conftest.py fixtures via pytest injection + "
            "B1 sys.modules save/restore guard; remove module-level sys.path.insert / "
            "from conftest import"
        )
        return StepResult(
            status="error",
            data={**prev.data},
            duration_ms=0, step_name=step,
            error=f"suite-unsafe patterns detected: {offending}. Remediation: {remediation}",
            error_code="E_RED_SUITE_UNSAFE",
            recoverable=True,
        )

    # ── 7AD3D393: stub-passability gate (mock-the-UUT vacuous RED) ──
    if get_config().gate_enabled("HAL_STUB_PASSABILITY_GATE"):     # kill switch, default ON
        stub_hits: list[str] = []          # "<rp>:<patch_line> '<symbol>'"
        stub_findings: list[dict] = []     # 457DC7DC: normalized loci for directed repair
        for rp in resolved_paths:
            try:
                src = Path(rp).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            try:
                for f in scan_stub_passability(src):
                    stub_hits.append(f"{rp}:{f.patch_line} '{f.symbol}'")
                    stub_findings.append({
                        "path": rp,
                        "line": f.patch_line,
                        "rule": "stub-passability",
                        "evidence": f.symbol,
                    })
            except SyntaxError:
                continue       # collectability is _check_red_executable's job, not this lint's
        if stub_hits:
            _emit_safe("red_stub_passability_violation",
                       {"phase": 5, "hits": stub_hits}, severity="error")
            # 457DC7DC GH371 §2.2: cheap directed-repair pre-stage IN FRONT OF
            # the unchanged terminal E_RED_STUB_PASSABLE return (recoverable=False
            # preserved on non-convergence, AC6).
            if _directed_repair_enabled(ctx) and stub_findings:
                rr = attempt_directed_repair(
                    gate="stub_passability",
                    artifact_path=str(stub_findings[0].get("path") or ""),
                    findings=stub_findings,
                    rerun_gate=lambda: _verify_red_lint_rules(ctx, prev),
                    cheap_model=_resolve_directed_repair_model(cfg),
                    repair_step_name="repair_stub_passability",
                    max_attempts=_repair_cap(cfg),
                    ctx=ctx,
                )
                if rr.converged and rr.final is not None:
                    return rr.final
            return StepResult(
                status="error", data={**prev.data}, duration_ms=0, step_name=step,
                error="RED mocks its own UUT(s) — vacuous test: " + ", ".join(stub_hits),
                error_code="E_RED_STUB_PASSABLE", recoverable=False,
            )
    else:
        _emit_safe("gate_disabled", {
            "gate": "HAL_STUB_PASSABILITY_GATE",
            "step": step,
            "reason": "env_kill_switch",
        })

    # ── GH501/§1q (D1CF5FDF/FBCF22FA): reject spec_from_file_location/exec_module in RED ──
    _re_1q = re.compile(r"spec_from_file_location|exec_module")
    if get_config().gate_enabled("HAL_RED_1Q_GATE"):     # kill switch, default ON
        q1_hits: list[str] = []
        for rp in resolved_paths:
            try:
                src = Path(rp).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for q1_lineno, q1_line in enumerate(src.splitlines(), start=1):
                if _re_1q.search(q1_line) and "# 1q: allow" not in q1_line:
                    q1_hits.append(f"{rp}:{q1_lineno}")
        if q1_hits:
            _emit_safe("red_1q_exec_import_violation",
                       {"phase": 5, "hits": q1_hits}, severity="error")
            return StepResult(
                status="error", data={**prev.data}, duration_ms=0, step_name=step,
                error="RED uses spec_from_file_location/exec_module (§1q, non-collectable-hang risk): " + ", ".join(q1_hits),
                error_code="E_RED_1Q_EXEC_IMPORT", recoverable=False,
            )
    else:
        _emit_safe("gate_disabled", {
            "gate": "HAL_RED_1Q_GATE",
            "step": step,
            "reason": "env_kill_switch",
        })

    # ── GH542/§1q: collect-probe — non-collectable RED (D1CF5FDF 30-min-hang class) ──
    if get_config().gate_enabled("HAL_RED_COLLECT_PROBE_GATE"):      # kill-switch, default ON
        _cp_violations, _cp_skip = _red_collect_probe(resolved_paths, git_cwd)
        _cp_cfg = get_config()
        _cp_enforce = _cp_cfg.flag("HAL_RED_COLLECT_PROBE_ENFORCE") if hasattr(_cp_cfg, "flag") else False
        # warn-only rollout: HAL_RED_COLLECT_PROBE_ENFORCE default OFF —
        # flip-by:2026-07-24 Refs #542 (rollout-completion-check token)
        _emit_safe("red_collect_probe_check", {
            "phase": 5, "step": step, "red_paths_n": len(resolved_paths),
            "violations_n": len(_cp_violations), "violations": _cp_violations,
            "enforced": _cp_enforce, "skip_reason": _cp_skip,
        })
        if _cp_violations and _cp_enforce:
            return StepResult(
                status="error", data={**prev.data}, duration_ms=0, step_name=step,
                error="RED not collectable (§1q collect-probe, D1CF5FDF hang class): " + "; ".join(_cp_violations),
                error_code="E_RED_COLLECT_PROBE", recoverable=True,
            )
    else:
        _emit_safe("gate_disabled", {"gate": "HAL_RED_COLLECT_PROBE_GATE", "step": step, "reason": "env_kill_switch"})

    # ── GH535/§1a: sibling-test-audit (warn-only rollout — GH535 flip-by:2026-07-24) ──
    if get_config().gate_enabled("HAL_SIBLING_AUDIT_GATE"):     # kill switch, default ON
        _sibling_audit_warn(resolved_paths, git_cwd)
    else:
        _emit_safe("gate_disabled", {
            "gate": "HAL_SIBLING_AUDIT_GATE",
            "step": step,
            "reason": "env_kill_switch",
        })

    # semgrep is build-critical (112CB15B): fail loud at use-time when absent — never silently drop the SAST gate.
    if shutil.which("semgrep") is None:
        return StepResult(
            status="error", data=None, duration_ms=0, step_name=step,
            error="semgrep (SAST linter) not on PATH — build-critical; install semgrep and re-run /build",
            error_code="E_RED_LINT_SEMGREP_MISSING",
            recoverable=False,
        )

    rules_path = Path(__file__).parent.parent / "scripts" / "red_lint" / "rules.yml"
    if not rules_path.is_file():
        return StepResult(
            status="ok",
            data={**prev.data, "skipped": "rules_file_missing", "rules_path": str(rules_path)},
            duration_ms=0, step_name=step,
        )

    try:
        proc = bounded_run(
            ["semgrep", "--quiet", "--json", "--config", str(rules_path)] + resolved_paths,
            capture_output=True, text=True, cwd=git_cwd, timeout=60,
        )
    except FileNotFoundError as e:
        return StepResult(
            status="error", data=None, duration_ms=0, step_name=step,
            error=f"semgrep binary missing at invocation: {e}",
            error_code="E_RED_LINT_SEMGREP_MISSING",
            recoverable=False,
        )
    if proc.returncode == 124:
        return StepResult(
            status="error", data=None, duration_ms=0, step_name=step,
            error="semgrep exceeded 60s timeout running red_lint rules",
            error_code="E_RED_LINT_TIMEOUT",
        )

    try:
        payload = json.loads(proc.stdout) if proc.stdout.strip() else {"results": []}
    except json.JSONDecodeError as e:
        return StepResult(
            status="error", data=None, duration_ms=0, step_name=step,
            error=f"semgrep emitted non-JSON output: {e}; stderr={proc.stderr[-500:]!r}",
            error_code="E_RED_LINT_BAD_JSON",
        )

    # I2 fix: semgrep internal errors (broken rule, version mismatch) emit
    # {"errors":[...],"results":[]}. Treat as skipped (preserve prev.data),
    # not silent OK that rubber-stamps the cycle.
    semgrep_errors = payload.get("errors") or []
    if semgrep_errors:
        return StepResult(
            status="ok",
            data={
                **prev.data,
                "skipped": "semgrep_internal_error",
                "errors": semgrep_errors[:5],
            },
            duration_ms=0, step_name=step,
        )

    raw_findings = payload.get("results", []) or []
    findings: list[dict] = []
    errors: list[dict] = []
    warnings: list[dict] = []
    for f in raw_findings:
        check_id = str(f.get("check_id", ""))
        # extra.severity is the semgrep canonical place; fall back to F-rule mapping.
        severity = str((f.get("extra") or {}).get("severity") or "").upper()
        if not severity:
            if check_id.endswith("hardcoded-line-number-in-where-assertion"):
                severity = "ERROR"
            elif check_id.endswith("schema-referent-needs-verification"):
                severity = "WARNING"
        record = {
            "check_id": check_id,
            "severity": severity,
            "path": f.get("path"),
            "start": (f.get("start") or {}).get("line"),
        }
        findings.append(record)
        if severity == "ERROR":
            errors.append(record)
        elif severity == "WARNING":
            warnings.append(record)

    if errors:
        first = errors[0]
        # 457DC7DC GH371 §2.2: directed-repair pre-stage before the unchanged
        # terminal E_RED_LINT_F1 return (recoverable=False preserved on
        # non-convergence). The gate (semgrep F1) re-runs on the patch (§2.5).
        dr_findings = [
            {
                "path": str((Path(git_cwd) / (r.get("path") or "")).resolve()) if r.get("path") else "",
                "line": r.get("start"),
                "rule": r.get("check_id"),
                "evidence": f"{r.get('check_id')} ({r.get('severity')})",
            }
            for r in errors
        ]
        _dr_artifact = str(dr_findings[0].get("path") or "") if dr_findings else ""
        if _directed_repair_enabled(ctx) and _dr_artifact:
            rr = attempt_directed_repair(
                gate="red_lint",
                artifact_path=_dr_artifact,
                findings=dr_findings,
                rerun_gate=lambda: _verify_red_lint_rules(ctx, prev),
                cheap_model=_resolve_directed_repair_model(cfg),
                repair_step_name="repair_red_lint",
                max_attempts=_repair_cap(cfg),
                ctx=ctx,
            )
            if rr.converged and rr.final is not None:
                return rr.final
        return StepResult(
            status="error",
            data={"findings": findings, "errors": errors, "rule": "F1"},
            duration_ms=0, step_name=step,
            error=(
                f"red_lint F1 (hardcoded-line-number) violation: "
                f"{first.get('check_id')} at {first.get('path')}:{first.get('start')} "
                f"({len(errors)} total)"
            ),
            error_code="E_RED_LINT_F1",
            recoverable=False,
        )

    return StepResult(
        status="ok",
        data={
            "findings": findings,
            "warnings_count": len(warnings),
            **{k: v for k, v in prev.data.items() if k != "findings"},
        },
        duration_ms=0, step_name=step,
    )


def _verify_red_lint_rules(ctx, prev) -> StepResult:
    step = "verify_red_lint_rules"
    if not isinstance(prev, StepResult) or not isinstance(prev.data, dict):
        return StepResult(
            status="error", data=None, duration_ms=0, step_name=step,
            error="prev step did not produce data", error_code="E_MISSING_PREV_DATA",
        )
    if prev.data.get("commit_red_tests_skipped") == "test_only_mode":
        _emit_safe(
            "red_verify_skipped_test_only",
            {"phase": 5, "step": "verify_red_lint_rules", "reason": "test_only_mode"},
        )
        return StepResult(
            status="ok", data={**prev.data}, duration_ms=0,
            step_name="verify_red_lint_rules",
        )
    if "red_test_paths" not in prev.data:
        return StepResult(
            status="error", data=None, duration_ms=0, step_name=step,
            error="prev.data missing 'red_test_paths'", error_code="E_MISSING_PREV_DATA",
        )
    red_test_paths = prev.data["red_test_paths"]
    if not isinstance(red_test_paths, list):
        return StepResult(
            status="error", data=None, duration_ms=0, step_name=step,
            error=f"red_test_paths must be list, got {type(red_test_paths).__name__}",
            error_code="E_INVALID_PREV_DATA",
        )
    if not red_test_paths:
        return StepResult(
            status="error", data=None, duration_ms=0, step_name=step,
            error="red_test_paths is empty", error_code="E_RED_EMPTY_FILES",
        )

    # ── Hoist git_cwd resolution (N4: single source for both suite-safety and semgrep) ──
    # I1 fix: enforce git_cwd boundary on red_test_paths (LLM-provided list).
    # Reject absolute paths and `..` traversals — information-disclosure prevention.
    cfg = ctx.org_config or {}
    git_cwd = _resolve_git_cwd(ctx, prev)
    git_cwd_resolved = Path(git_cwd).resolve()
    resolved_paths: list[str] = []
    for p in red_test_paths:
        resolved = (Path(git_cwd) / p).resolve()
        try:
            resolved.relative_to(git_cwd_resolved)
        except ValueError:
            return StepResult(
                status="error",
                data={**prev.data},
                duration_ms=0, step_name=step,
                error=f"red_test_paths entry escapes git_cwd: {p!r}",
                error_code="E_RED_LINT_PATH_ESCAPE",
                recoverable=False,
            )
        resolved_paths.append(str(resolved))

    # ── GH595 §2.2: batch pre-flight dispatcher, master kill switch ──
    if not get_config().gate_enabled("HAL_RED_LINT_PREFLIGHT_BATCH"):   # default ON
        _emit_safe("gate_disabled", {
            "gate": "HAL_RED_LINT_PREFLIGHT_BATCH", "step": step, "reason": "env_kill_switch",
        })
        return _verify_red_lint_rules_legacy(ctx, prev, step, cfg, git_cwd, resolved_paths)

    # ── GH535/§1a: sibling-test-audit (warn-only rollout — GH535 flip-by:2026-07-24) ──
    # §2.1: stays outside the batch (no error code) but still called as today.
    if get_config().gate_enabled("HAL_SIBLING_AUDIT_GATE"):     # kill switch, default ON
        _sibling_audit_warn(resolved_paths, git_cwd)
    else:
        _emit_safe("gate_disabled", {
            "gate": "HAL_SIBLING_AUDIT_GATE",
            "step": step,
            "reason": "env_kill_switch",
        })

    batch = _collect_red_lint_findings(resolved_paths, git_cwd, ctx, cfg)

    # ── GH595 §2.1 item 5 / BFEC3E71 §1a: semgrep F1, inlined literally in
    # this function's body so bounded_run(/except FileNotFoundError are
    # source-greppable directly on _verify_red_lint_rules. ──
    sg_batch: list[dict] = []
    sg_all_findings: list[dict] = []
    sg_warnings_count = 0
    skip_reason = None
    if shutil.which("semgrep") is None:
        skip_reason = "missing"
    else:
        rules_path = Path(__file__).parent.parent / "scripts" / "red_lint" / "rules.yml"
        if not rules_path.is_file():
            skip_reason = "rules_file_missing"
        else:
            proc = None
            try:
                proc = bounded_run(
                    ["semgrep", "--quiet", "--json", "--config", str(rules_path)] + resolved_paths,
                    capture_output=True, text=True, cwd=git_cwd, timeout=60,
                )
            except FileNotFoundError:
                skip_reason = "missing"
            if skip_reason is None and proc is not None:
                if proc.returncode == 124:
                    skip_reason = "timeout"
                else:
                    try:
                        payload = json.loads(proc.stdout) if proc.stdout.strip() else {"results": []}
                    except json.JSONDecodeError:
                        skip_reason = "bad_json"
                        payload = None
                    if skip_reason is None and payload is not None:
                        semgrep_errors = payload.get("errors") or []
                        if semgrep_errors:
                            skip_reason = "internal_error"
                        else:
                            raw_findings = payload.get("results", []) or []
                            sg_errors: list[dict] = []
                            sg_warnings: list[dict] = []
                            for f in raw_findings:
                                check_id = str(f.get("check_id", ""))
                                severity = str((f.get("extra") or {}).get("severity") or "").upper()
                                if not severity:
                                    if check_id.endswith("hardcoded-line-number-in-where-assertion"):
                                        severity = "ERROR"
                                    elif check_id.endswith("schema-referent-needs-verification"):
                                        severity = "WARNING"
                                sg_record = {
                                    "check_id": check_id, "severity": severity,
                                    "path": f.get("path"), "start": (f.get("start") or {}).get("line"),
                                }
                                sg_all_findings.append(sg_record)
                                if severity == "ERROR":
                                    sg_errors.append(sg_record)
                                elif severity == "WARNING":
                                    sg_warnings.append(sg_record)
                            sg_warnings_count = len(sg_warnings)
                            sg_batch = [
                                {
                                    "path": str((Path(git_cwd) / (r.get("path") or "")).resolve()) if r.get("path") else "",
                                    "line": r.get("start"),
                                    "rule": r.get("check_id"),
                                    "evidence": f"{r.get('check_id')} ({r.get('severity')})",
                                    "error_code": "E_RED_LINT_F1",
                                    "recoverable": False,
                                }
                                for r in sg_errors
                            ]
    batch = batch + sg_batch

    if not batch:
        # §2.3: empty batch — infra outcomes surface exactly as legacy today.
        if skip_reason == "missing":
            return StepResult(
                status="error", data=None, duration_ms=0, step_name=step,
                error="semgrep (SAST linter) not on PATH — build-critical; install semgrep and re-run /build",
                error_code="E_RED_LINT_SEMGREP_MISSING",
                recoverable=False,
            )
        if skip_reason == "timeout":
            return StepResult(
                status="error", data=None, duration_ms=0, step_name=step,
                error="semgrep exceeded 60s timeout running red_lint rules",
                error_code="E_RED_LINT_TIMEOUT",
            )
        if skip_reason == "bad_json":
            return StepResult(
                status="error", data=None, duration_ms=0, step_name=step,
                error="semgrep emitted non-JSON output",
                error_code="E_RED_LINT_BAD_JSON",
            )
        if skip_reason == "rules_file_missing":
            rules_path = Path(__file__).parent.parent / "scripts" / "red_lint" / "rules.yml"
            return StepResult(
                status="ok",
                data={**prev.data, "skipped": "rules_file_missing", "rules_path": str(rules_path)},
                duration_ms=0, step_name=step,
            )
        if skip_reason == "internal_error":
            return StepResult(
                status="ok",
                data={**prev.data, "skipped": "semgrep_internal_error"},
                duration_ms=0, step_name=step,
            )
        return StepResult(
            status="ok",
            data={
                "findings": sg_all_findings,
                "warnings_count": sg_warnings_count,
                **{k: v for k, v in prev.data.items() if k != "findings"},
            },
            duration_ms=0, step_name=step,
        )

    # §2.2: non-empty batch — single combined report + single directed repair.
    codes = list(dict.fromkeys(f["error_code"] for f in batch))
    _emit_safe("red_lint_preflight_batch", {
        "phase": 5, "step": step, "codes": codes, "findings_n": len(batch),
    }, severity="error")

    # 457DC7DC: single distinct error_code -> legacy gate/repair_step_name;
    # multi-code batch -> combined "red_lint_preflight" naming (RED AC2).
    if len(codes) == 1 and codes[0] == "E_RED_STUB_PASSABLE":
        _dr_gate, _dr_step_name = "stub_passability", "repair_stub_passability"
    elif len(codes) == 1 and codes[0] == "E_RED_LINT_F1":
        _dr_gate, _dr_step_name = "red_lint", "repair_red_lint"
    else:
        _dr_gate, _dr_step_name = "red_lint_preflight", "repair_red_lint_preflight"

    if _directed_repair_enabled(ctx):
        rr = attempt_directed_repair(
            gate=_dr_gate,
            artifact_path=str(batch[0].get("path") or ""),
            findings=batch,
            rerun_gate=lambda: _verify_red_lint_rules(ctx, prev),
            cheap_model=_resolve_directed_repair_model(cfg),
            repair_step_name=_dr_step_name,
            max_attempts=_repair_cap(cfg),
            ctx=ctx,
        )
        if rr.converged and rr.final is not None:
            return rr.final

    error_parts = []
    for f in batch:
        loc = f"{f['error_code']} at {f['path']}:{f['line']}"
        if f.get("evidence"):
            loc += f" '{f['evidence']}'"
        error_parts.append(loc)
    data = {**prev.data, "preflight_error_codes": codes, "preflight_findings": batch}
    if skip_reason:
        data["semgrep_skipped"] = skip_reason

    # GH602: findings-driven delta-retry — terminal RED-lint gates (stub/1q/
    # suite/collect-probe) get a cap-2 retry budget instead of always dying.
    delta_gate_on = get_config().gate_enabled("HAL_RED_PREFLIGHT_DELTA_RETRY")
    if not delta_gate_on:
        _emit_safe("gate_disabled", {
            "gate": "HAL_RED_PREFLIGHT_DELTA_RETRY", "step": step, "reason": "env_kill_switch",
        })
    if delta_gate_on and _preflight_retry_eligible(batch):
        cycle = int(prev.data.get("cycle", 1)) if isinstance(prev.data, dict) else 1
        findings_str = "; ".join(error_parts)
        build_class = (ctx.org_config or {}).get("complexity", "SIMPLE").upper()
        return RecoverableGateMixin.gated_step_result(
            build_class=build_class,
            gate="red_lint_preflight",
            cycle=cycle,
            retry_from_step_idx=0,
            error_code=codes[0],
            error_msg="red-lint preflight (recoverable retry): " + "; ".join(error_parts),
            step_name=step,
            forwarded_data={**data, "findings": findings_str},
            terminal_error_code="E_RED_LINT_FAIL_CAP2",
            terminal_error_msg="red-lint preflight cap exhausted: " + "; ".join(error_parts),
        )
    return StepResult(
        status="error",
        data=data,
        duration_ms=0, step_name=step,
        error="red-lint preflight: " + "; ".join(error_parts),
        error_code=codes[0],
        recoverable=False,
    )


# ─── F23A1FDF sibling-expand helpers ─────────────────────────────────────────

def _module_tokens_for(prod_rel_path: str) -> set[str]:
    """Derive importable tokens from a repo-relative production path.

    Example: ``bark/core/strategy.py`` → ``{"strategy", "bark.core.strategy", "bark.core"}``.
    """
    normalized = prod_rel_path.replace("\\", "/").rstrip("/")
    if normalized.endswith(".py"):
        normalized = normalized[:-3]
    parts = [p for p in normalized.split("/") if p and p != "."]
    if not parts:
        return set()
    stem = parts[-1]
    tokens: set[str] = set()
    if stem == "__init__":
        # Drop __init__, treat parent dir as the effective dotted path
        parts = parts[:-1]
        if not parts:
            return set()
        dotted_full = ".".join(parts)
        tokens.add(dotted_full)
        if len(parts) >= 2:
            tokens.add(".".join(parts[:-1]))
    else:
        tokens.add(stem)
        dotted_full = ".".join(parts)
        tokens.add(dotted_full)
        if len(parts) >= 2:
            tokens.add(".".join(parts[:-1]))
    return {t for t in tokens if t}


def _grep_import_sibling_tests(
    changed_prod_paths: list[str],
    git_cwd: str,
    test_roots: list[str],
) -> set[str]:
    """Return abs paths of test files in *test_roots* that import any changed prod module.

    Pure filesystem reads — no git.  Over-inclusion is the safe failure mode.
    """
    import fnmatch  # local import

    token_sets = [_module_tokens_for(p) for p in changed_prod_paths]
    if all(not ts for ts in token_sets):
        return set()

    all_tokens: set[str] = set()
    for ts in token_sets:
        all_tokens |= ts
    dotted_tokens = {t for t in all_tokens if "." in t}
    stem_tokens = {t for t in all_tokens if "." not in t}

    matched: set[str] = set()
    for root in test_roots:
        root_path = Path(root)
        if not root_path.is_dir():
            continue
        for f in root_path.rglob("*.py"):
            if not any(fnmatch.fnmatch(f.name, pat) for pat in _TEST_FILENAME_PATTERNS):
                continue
            try:
                text = f.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            found = False
            for line in text.splitlines():
                if found:
                    break
                s = line.lstrip()
                if not (s.startswith("import ") or s.startswith("from ")):
                    continue
                for tok in dotted_tokens:
                    if tok in line:
                        matched.add(str(f.resolve()))
                        found = True
                        break
                if found:
                    break
                for tok in stem_tokens:
                    if re.search(r"\b" + re.escape(tok) + r"\b", line):
                        matched.add(str(f.resolve()))
                        found = True
                        break
    return matched


def _conventional_sibling_candidates(prod_path: str) -> list[str]:
    """Return conventional sibling test candidate paths for a production file.

    For ``dir/<stem>.py`` returns (as absolute strings resolved against the
    file's own directory — callers pass abs paths or resolve relative to git_cwd
    before calling):
        dir/test_<stem>.py
        dir/<stem>_test.py
        dir/tests/test_<stem>.py
        dir/__tests__/test_<stem>.py
    """
    p = Path(prod_path)
    stem = p.stem
    parent = p.parent
    return [
        str((parent / f"test_{stem}.py").resolve()),
        str((parent / f"{stem}_test.py").resolve()),
        str((parent / "tests" / f"test_{stem}.py").resolve()),
        str((parent / "__tests__" / f"test_{stem}.py").resolve()),
    ]


def _sibling_test_paths(red_test_paths: list[str], red_sha: str | None, git_cwd: str) -> list[str]:
    """Return red_test_paths union existing conventional sibling tests of changed prod files.

    Pure-ish (only git/fs reads).  Returns a sorted, deduped list of resolved
    absolute paths.  When ``red_sha`` is None, returns ``red_test_paths`` sorted.
    """
    import fnmatch as _fnmatch  # local import (same pattern as rest of module)

    # Resolve red_test_paths to absolute (A1 advisory)
    out: set[str] = set()
    for rp in red_test_paths:
        p = Path(rp)
        if p.is_absolute():
            out.add(str(p.resolve()))
        else:
            out.add(str((Path(git_cwd) / rp).resolve()))

    if not red_sha:
        return sorted(out)

    changed = git_diff_files(red_sha, git_cwd, untracked=True, segment_filter=None)

    _TEST_PATTERNS = _TEST_FILENAME_PATTERNS  # §1g single-source
    _TEST_SEGMENTS = ("tests/", "__tests__/")

    prod_files: list[str] = []
    for p in changed:
        if not p.endswith(".py"):
            continue
        fn = p.split("/")[-1]
        norm = p.replace("\\", "/")
        if any(_fnmatch.fnmatch(fn, pat) for pat in _TEST_PATTERNS):
            continue
        if any(seg in norm for seg in _TEST_SEGMENTS):
            continue
        # Resolve prod path relative to git_cwd if not absolute
        abs_prod = str((Path(git_cwd) / p).resolve()) if not Path(p).is_absolute() else str(Path(p).resolve())
        for cand in _conventional_sibling_candidates(abs_prod):
            if os.path.isfile(cand):
                out.add(cand)
        prod_files.append(p)

    test_roots = [str(Path(git_cwd) / "tests"), str(Path(git_cwd) / "test")]
    out |= _grep_import_sibling_tests(prod_files, git_cwd, test_roots)

    return sorted(out)


def _run_plan_failed_total(plan: dict, git_cwd: str) -> int | None:
    """Run each group in *plan* on HEAD and return the total n_failed count.

    Mirrors the current-run loop in ``_verify_green_passing`` (:1839-1871) but
    returns a total rather than mutating ``failing_groups``.

    Returns:
        int  — total n_failed across all groups (may be 0).
        None — best-effort unavailable: ``FileNotFoundError`` (runner missing)
               or timeout sentinel (exit_code==124 + n_failed==sys.maxsize).
    """
    total = 0
    for group in plan.get("groups", []):
        try:
            r = run_test_command(group["argv"], git_cwd, timeout=120)
        except FileNotFoundError:
            return None
        if r.exit_code == 124 and r.n_failed == sys.maxsize:
            return None
        total += r.n_failed
    return total


# ─── 585E30E3-P2 baseline helper (colocated with workflow helpers) ────────────


def _compute_baseline_failed(plan: dict, git_cwd: str) -> int | None:
    """Stash the working tree, re-run each test group on the pre-branch baseline,
    return the total n_failed count, then pop the stash.

    Returns:
        int   — total n_failed across all groups on the baseline (may be 0).
        None  — baseline unavailable: any failure of stash, rerun, or pop is
                swallowed and returns None (shadow-safe per D3).

    D4 invariant: working tree is NEVER left stashed on exit — the finally block
    always attempts git stash pop (with its own inner try/except so a raising pop
    cannot escape this helper).  Opus M3 binding.
    """
    stashed = False
    baseline_failed: int = 0
    try:
        r = git_write_port.git_op_capture(
            ["git", "stash", "push", "-u", "-m", "p2-baseline-585e30e3"],
            cwd=git_cwd,
            timeout=30,
        )
        # "No local changes to save" appears on stdout when the working tree is clean.
        # Handle both returncode!=0 (some git versions) and the stdout sentinel.
        if r.returncode != 0 or "No local changes to save" in r.stdout:
            return None  # no independent baseline — tree was already clean
        stashed = True

        for group in plan["groups"]:
            try:
                result = run_test_command(group["argv"], git_cwd, timeout=120)
            except FileNotFoundError:
                return None  # runner binary missing — baseline unavailable (OWN per §1n)
            # Timeout sentinel: exit_code==124 and n_failed==sys.maxsize
            if result.exit_code == 124 and result.n_failed == sys.maxsize:
                return None  # timeout — baseline unavailable (OWN per §1n)
            baseline_failed += result.n_failed

        return baseline_failed

    except Exception:
        _emit_safe("p2_baseline_error", {"phase": 5}, severity="error")
        return None

    finally:
        if stashed:
            try:
                git_write_port.git_op_capture(
                    ["git", "stash", "pop"],
                    cwd=git_cwd,
                    timeout=30,
                )
            except Exception:
                _emit_safe("p2_baseline_stash_pop_failed", {"phase": 5}, severity="error")


# ─── 5C14EF32 baseline helper (typecheck — colocated with _compute_baseline_failed) ──


def _compute_baseline_typecheck_count(resolved_paths: list[str], git_cwd: str) -> int | None:
    """Stash the working tree, run mypy on the pre-branch baseline for the given
    resolved paths, return the total findings count, then pop the stash.

    Returns:
        int   — total mypy findings on the baseline (may be 0 when all paths are
                newly-added and vanish after stash, or when mypy reports none).
        None  — baseline unavailable: stash reported no local changes, or any
                failure of stash/mypy/pop is swallowed and returns None.

    D4 invariant: working tree is NEVER left stashed on exit — the finally block
    always attempts git stash pop (with its own inner try/except so a raising pop
    cannot escape this helper).  Mirrors _compute_baseline_failed exactly.
    """
    stashed = False
    try:
        r = git_write_port.git_op_capture(
            ["git", "stash", "push", "-u", "-m", "p2-typecheck-baseline-5c14ef32"],
            cwd=git_cwd,
            timeout=30,
        )
        # "No local changes to save" appears on stdout when the working tree is clean.
        # Handle both returncode!=0 (some git versions) and the stdout sentinel.
        if r.returncode != 0 or "No local changes to save" in r.stdout:
            return None  # no independent baseline — tree was already clean
        stashed = True

        # After stashing, filter to paths that still exist on disk.
        # Newly-ADDED files vanish at baseline and are excluded — their findings
        # count fully as net-new (mirrors lint's "added files always hard-fail").
        existing_paths = [p for p in resolved_paths if Path(p).exists()]

        # If no paths remain (all newly-added) → baseline has zero findings.
        if not existing_paths:
            return 0

        try:
            proc = bounded_run(
                _mypy_base_argv(git_cwd) + existing_paths,
                cwd=git_cwd,
                capture_output=True,
                text=True,
                timeout=60,
            )
        except FileNotFoundError as exc:
            _emit_safe("p2_typecheck_baseline_error", {"phase": 5, "reason": str(exc)}, severity="error")
            return None
        except Exception as exc:
            _emit_safe("p2_typecheck_baseline_error", {"phase": 5, "reason": str(exc)}, severity="error")
            return None
        if proc.returncode == 124:
            _emit_safe("p2_typecheck_baseline_error", {"phase": 5, "reason": "timeout"}, severity="error")
            return None

        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        return len(_parse_mypy_output(stdout + "\n" + stderr))

    except Exception:
        _emit_safe("p2_typecheck_baseline_error", {"phase": 5}, severity="error")
        return None

    finally:
        if stashed:
            try:
                git_write_port.git_op_capture(
                    ["git", "stash", "pop"],
                    cwd=git_cwd,
                    timeout=30,
                )
            except Exception:
                _emit_safe("p2_typecheck_baseline_stash_pop_failed", {"phase": 5}, severity="error")


# ─── Step 4.8: verify_green_passing — mechanical test-runner gate (95D3E5F6) ──


def _verify_green_passing(ctx, prev) -> StepResult:
    step = "verify_green_passing"
    if not isinstance(prev, StepResult) or not isinstance(prev.data, dict):
        return StepResult(
            status="error", data=None, duration_ms=0, step_name=step,
            error="prev step did not produce data", error_code="E_MISSING_PREV_DATA",
        )
    red_test_paths = prev.data.get("red_test_paths") or []
    if not red_test_paths:
        # No paths to verify — skip cleanly with telemetry.
        _emit_safe("verify_green_skipped", {"reason": "no_red_test_paths", "phase": 5}, severity="warning")
        return StepResult(status="ok", data=dict(prev.data), duration_ms=0, step_name=step)
    cfg = ctx.org_config or {}
    git_cwd = _resolve_git_cwd(ctx, prev)
    plan = _infer_test_command_for_paths(list(red_test_paths), git_cwd=git_cwd)
    if plan.get("skipped"):
        _emit_safe("verify_green_skipped", {"reason": plan["reason"], "phase": 5}, severity="warning")
        return StepResult(
            status="ok",
            data={**prev.data, "skipped": plan["reason"], "skip_reason": plan["reason"]},
            duration_ms=0,
            step_name=step,
        )
    # ── P1b reproducibility gate (585E30E3) — py groups only ─────────────────
    for group in plan["groups"]:
        if group["kind"] != "py":
            continue
        argv_pinned = _pin_pytest_collection(group["argv"])
        try:
            ok, counts = verify_count_reproducible(run_test_command, argv_pinned, git_cwd)
        except FileNotFoundError as e:
            return StepResult(
                status="error", data=None, duration_ms=0, step_name=step,
                error=f"{argv_pinned[0]} binary missing: {e}",
                error_code="E_GREEN_TEST_RUNNER_MISSING",
                recoverable=False,
            )
        _emit_safe(
            "green_reproducibility_verdict",
            {
                "group": group["kind"],
                "n_runs": _REPRODUCIBILITY_RUNS,
                "is_reproducible": ok,
                "counts": [list(c) for c in counts],
                "phase": 5,
            },
            severity="warning",
        )
        if not ok:
            return StepResult(
                status="error",
                data={**prev.data},
                duration_ms=0,
                step_name=step,
                error=f"green test counts non-reproducible across 5 runs: {counts}",
                error_code="E_GREEN_NOT_REPRODUCIBLE",
                recoverable=True,
            )

    failing_groups: list[str] = []
    failing_group_tails: list[dict] = []  # AEC7E800: capture stdout tails for feedback
    current_failed_total: int = 0  # 585E30E3-P2 Edit A: accumulate failures across groups
    baseline_delta_blocks: list = []  # GH561 §1r lane-2 warn-only baseline-delta gate
    for group in plan["groups"]:
        argv = group["argv"]
        kind = group["kind"]
        try:
            dt_result = run_test_command(argv, git_cwd, timeout=120)
        except FileNotFoundError as e:
            return StepResult(
                status="error", data=None, duration_ms=0, step_name=step,
                error=f"{argv[0]} binary missing: {e}",
                error_code="E_GREEN_TEST_RUNNER_MISSING",
                recoverable=False,
            )
        # Detect timeout via the dataclass exit_code convention (124 + n_failed=sys.maxsize)
        if dt_result.exit_code == 124 and dt_result.n_failed == sys.maxsize:
            return StepResult(
                status="error", data=None, duration_ms=0, step_name=step,
                error=f"{argv[0]} exceeded 120s timeout running green tests",
                error_code="E_GREEN_TEST_TIMEOUT",
            )
        _emit_safe(
            "green_test_outcome",
            {
                "group": kind,
                "exit_code": dt_result.exit_code,
                "n_passed": dt_result.n_passed,
                "n_failed": dt_result.n_failed,
                "phase": 5,
            },
            severity="warning",
        )
        if dt_result.exit_code != 0 or dt_result.n_failed > 0:
            failing_groups.append(kind)
            current_failed_total += dt_result.n_failed  # 585E30E3-P2 Edit A
            # AEC7E800: capture stdout tail for test-failure feedback loop
            group_stdout_tail = ""
            try:
                if dt_result.stdout_path:
                    with open(dt_result.stdout_path, encoding="utf-8", errors="replace") as _f:
                        group_stdout_tail = _f.read()
            except Exception:
                pass
            failing_group_tails.append({"group": kind, "tail": group_stdout_tail})
            # GH561 §1r lane-2: warn-only baseline-delta gate (ledger-aware verdict)
            if getattr(dt_result, "stdout_path", None):
                _bd = run_baseline_delta_gate(
                    dt_result.stdout_path, "pytest" if kind == "py" else "bun",
                    git_cwd, 5, step, _emit_safe,
                )
                if _bd.get("would_block"):
                    baseline_delta_blocks.append({"group": kind, "n_new_fails": _bd.get("n_new_fails")})
    if baseline_delta_blocks:
        return StepResult(
            status="error", data={**prev.data}, duration_ms=0, step_name=step,
            error=f"baseline-delta gate: new fails outside baseline+ledger in {len(baseline_delta_blocks)} group(s)",
            error_code="E_BASELINE_DELTA", recoverable=True,
        )
    # ── F23A1FDF sibling-expanded net-new-delta (runs regardless of failing_groups) ──
    red_sha = (prev.data.get("red_commit_sha") or cfg.get("red_commit_sha")) if isinstance(prev.data, dict) else None
    if bool(cfg.get("verify_green_delta_sibling_expand", True)) and red_sha:
        sib_paths = _sibling_test_paths(list(red_test_paths), red_sha, git_cwd)
        new_sib = [p for p in sib_paths if p not in set(
            (str((Path(git_cwd) / x).resolve()) if not Path(str(x)).is_absolute() else str(Path(str(x)).resolve()))
            for x in red_test_paths)]
        if new_sib:
            sib_plan = _infer_test_command_for_paths(sib_paths, git_cwd=git_cwd)
            if not sib_plan.get("skipped"):
                sib_current = _run_plan_failed_total(sib_plan, git_cwd)
                if sib_current is not None:
                    sib_baseline = _compute_baseline_failed(sib_plan, git_cwd)
                    sib_verdict = delta_verdict(sib_baseline, sib_current,
                                                enforce=bool(cfg.get("verify_green_delta_enforce", True)))
                    _emit_safe("verify_green_sibling_delta_verdict", {
                        "baseline_failed": sib_verdict.baseline_failed,
                        "current_failed": sib_verdict.current_failed,
                        "net_new": sib_verdict.net_new,
                        "classification": sib_verdict.classification,
                        "n_sibling_paths": len(new_sib),
                        "phase": 5,
                    }, severity="warning")
                    if sib_verdict.would_block:
                        return StepResult(
                            status="error", data=None, duration_ms=0, step_name=step,
                            error=f"GREEN net-new sibling-test regressions ({sib_verdict.net_new}) outside scoped red paths",
                            error_code="E_GREEN_NOT_PASSING",
                            recoverable=False,
                        )
    # fall through to existing `if failing_groups:` logic unchanged
    if failing_groups:
        # ── NEW: 14F6DCD4 / #674 — net-new-added RED test hard-fail ───────────
        # When the failing test paths correspond to files ADDED since red_sha
        # (committed, staged, or untracked), this is a TDD-contract violation:
        # GREEN added a RED test and shipped without making it pass. Hard-fail
        # regardless of the verify_green_passing step outcome — a failed green
        # always errors (the opt-out escalate path was removed, GH297).
        # Codifies 688D733F §1l 6-layer production-side-effect anchor recipe.
        red_sha = (prev.data.get("red_commit_sha") or cfg.get("red_commit_sha")) if isinstance(prev.data, dict) else None
        if red_sha:
            added_files = _get_diff_added_files(red_sha, git_cwd)
            # Resolve red_test_paths to absolute (matching _get_diff_added_files output).
            added_test_failures: list[str] = []
            for p in red_test_paths:
                abs_p = str((Path(git_cwd) / p).resolve()) if not Path(p).is_absolute() else str(Path(p).resolve())
                if abs_p in added_files:
                    added_test_failures.append(abs_p)
            if added_test_failures:
                _emit_safe(
                    "verify_green_added_test_failed",
                    {
                        "net_new_added_failures": added_test_failures,
                        "n_added_failures": len(added_test_failures),
                        "phase": 5,
                    },
                    severity="warning",
                )
                return StepResult(
                    status="error", data=None, duration_ms=0, step_name=step,
                    error=f"GREEN net-new-added RED tests failing ({len(added_test_failures)} added test file(s)) — TDD contract violation",
                    error_code="E_GREEN_NOT_PASSING",
                    recoverable=False,
                )
        # ── P2 585E30E3 net-new-delta SHADOW gate ────────────────────────────
        enforce_delta = bool(cfg.get("verify_green_delta_enforce", True))   # default True (C0B5C6E1 Phase 1b flip)
        baseline_failed = _compute_baseline_failed(plan, git_cwd)  # best-effort; None on any failure
        verdict = delta_verdict(baseline_failed, current_failed_total, enforce=enforce_delta)
        _emit_safe(
            "verify_green_delta_verdict",
            {
                "baseline_failed": verdict.baseline_failed,
                "current_failed": verdict.current_failed,
                "net_new": verdict.net_new,
                "classification": verdict.classification,
                "enforced": enforce_delta,
                "phase": 5,
            },
            severity="warning",
        )
        if verdict.would_block:                 # enforce True AND net_new>0 (default-off this ship)
            return StepResult(
                status="error", data=None, duration_ms=0, step_name=step,
                error=f"GREEN net-new test failures ({verdict.net_new}) — branch added regressions",
                error_code="E_GREEN_NOT_PASSING",
                recoverable=False,
            )
        # ── end P2 shadow gate — existing code continues unchanged from here ──
        kinds = ",".join(failing_groups)
        # AEC7E800: recoverable feedback loop — mirror lint/typecheck cycle-cap pattern.
        # Collect raw tails, join, tail-cap from LEFT at FINDINGS_MAX_CHARS, store as
        # list[str] so " ".join(str(f) for f in findings) in AC4 measures clean text.
        _raw_tails = [str(e.get("tail", "")) for e in failing_group_tails]
        _joined = " ".join(_raw_tails)
        if len(_joined) > FINDINGS_MAX_CHARS:
            _joined = _joined[-FINDINGS_MAX_CHARS:]  # tail-truncate from LEFT
        # Store as single-element list so the AC4 payload measure equals len(_joined).
        green_test_findings: list = [_joined]
        cycle_count = int(prev.data.get("cycle_count", 1)) if isinstance(prev.data, dict) else 1
        if cycle_count < GREEN_TEST_CYCLE_CAP:
            return StepResult(
                status="error",
                data={
                    **(prev.data if isinstance(prev.data, dict) else {}),
                    "green_test_findings": green_test_findings,
                    "cycle_count": cycle_count,
                    "retry_from_step": 1,
                },
                duration_ms=0,
                step_name=step,
                error=f"GREEN tests in groups [{kinds}] still failing (cycle {cycle_count}/{GREEN_TEST_CYCLE_CAP})",
                error_code="E_GREEN_NOT_PASSING",
                recoverable=True,
            )
        # At or beyond cap — terminal abort (AC3).
        return StepResult(
            status="error",
            data={
                **(prev.data if isinstance(prev.data, dict) else {}),
                "green_test_findings": green_test_findings,
            },
            duration_ms=0,
            step_name=step,
            error=f"GREEN tests in groups [{kinds}] still failing (cycle {cycle_count}/{GREEN_TEST_CYCLE_CAP} — terminal)",
            error_code="E_GREEN_NOT_PASSING",
            recoverable=False,
        )
    return StepResult(status="ok", data=dict(prev.data), duration_ms=0, step_name=step)


# ─── Helpers for 56D695F2 scoped-diff green_lint ─────────────────────────────


def _get_diff_added_files(red_sha: str, git_cwd: str) -> set[str]:
    """Return the set of absolute paths that were ADDED (status A) since red_sha.

    Checks three sources:
    1. Committed changes (``git diff --name-status <red_sha> HEAD``)
    2. Staged-but-not-committed new files
       (``git diff --name-status --cached --diff-filter=A``)
    3. Untracked new files (``git ls-files --others --exclude-standard``) —
       these are files GREEN wrote but _commit_green_code has not yet staged.
       By definition they did not exist at red_sha, so they are always "added".

    Returns empty set on any git failure.
    """
    added: set[str] = set()
    # Committed additions
    try:
        result = git_port.git_read(
            ["diff", "--name-status", red_sha, "HEAD"],
            cwd=git_cwd,
            timeout=30,
        )
        if result.returncode == 0:
            for line in result.stdout.splitlines():
                if line.startswith("A\t"):
                    rel_path = line[2:].strip()
                    added.add(str((Path(git_cwd) / rel_path).resolve()))
    except Exception as exc:
        logger.warning("_get_diff_added_files: git diff committed failed: %s", exc)
    # Staged-but-not-yet-committed additions
    try:
        result2 = git_port.git_read(
            ["diff", "--name-status", "--cached", "--diff-filter=A"],
            cwd=git_cwd,
            timeout=30,
        )
        if result2.returncode == 0:
            for line in result2.stdout.splitlines():
                if line.startswith("A\t"):
                    rel_path = line[2:].strip()
                    added.add(str((Path(git_cwd) / rel_path).resolve()))
    except Exception as exc:
        logger.warning("_get_diff_added_files: git diff staged failed: %s", exc)
    # Untracked new files (GREEN wrote them but commit_green_code hasn't staged yet)
    try:
        result3 = git_port.git_read(
            ["ls-files", "--others", "--exclude-standard"],
            cwd=git_cwd,
            timeout=30,
        )
        if result3.returncode == 0:
            for line in result3.stdout.splitlines():
                rel_path = line.strip()
                if rel_path:
                    added.add(str((Path(git_cwd) / rel_path).resolve()))
    except Exception as exc:
        logger.warning("_get_diff_added_files: git ls-files untracked failed: %s", exc)
    return added


def _finding_in_diff_hunks(abs_path: str, red_sha: str, finding_line: int | None, git_cwd: str) -> bool:
    """Return True if *finding_line* falls within a ``+`` hunk of the diff since *red_sha*.

    Parses ``git diff -U0 <red_sha> -- <abs_path>`` (working tree vs sha —
    covers both committed and staged GREEN changes) and checks each
    ``@@ -A,B +C,D @@`` header.  A finding at line N is in-scope when
    ``C <= N < C + max(D, 1)`` for any hunk.

    Returns True (hard-fail) on any parsing or git failure — conservative.
    """
    if finding_line is None:
        return True  # no line info → conservative hard-fail
    try:
        result = git_port.git_read(
            ["diff", "-U0", red_sha, "--", abs_path],
            cwd=git_cwd,
            timeout=30,
        )
    except Exception as exc:
        logger.warning("_finding_in_diff_hunks: git diff failed: %s", exc)
        return True  # conservative
    if result.returncode != 0:
        return True  # conservative
    hunk_re = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
    for line in result.stdout.splitlines():
        m = hunk_re.match(line)
        if m:
            hunk_start = int(m.group(1))
            hunk_len_str = m.group(2)
            hunk_len = int(hunk_len_str) if hunk_len_str is not None else 1
            # When hunk_len == 0 the hunk is a pure-deletion — no added lines.
            if hunk_len == 0:
                continue
            if hunk_start <= finding_line < hunk_start + hunk_len:
                return True
    return False


# ─── Step 4.7: green_lint semgrep gate (79092C00) ────────────────────────────


def _verify_green_lint_rules(ctx, prev) -> StepResult:
    step = "verify_green_lint_rules"
    if not isinstance(prev, StepResult) or not isinstance(prev.data, dict):
        return StepResult(
            status="error", data=None, duration_ms=0, step_name=step,
            error="prev step did not produce data", error_code="E_MISSING_PREV_DATA",
        )

    # semgrep is build-critical (112CB15B): fail loud at use-time when absent — never silently drop the SAST gate.
    if shutil.which("semgrep") is None:
        return StepResult(
            status="error", data=None, duration_ms=0, step_name=step,
            error="semgrep (SAST linter) not on PATH — build-critical; install semgrep and re-run /build",
            error_code="E_GREEN_LINT_SEMGREP_MISSING",
            recoverable=False,
        )

    rules_path = Path(__file__).parent.parent / "scripts" / "green_lint" / "rules.yml"
    if not rules_path.is_file():
        return StepResult(
            status="ok",
            data={**prev.data, "skipped": "rules_file_missing", "rules_path": str(rules_path)},
            duration_ms=0, step_name=step,
        )

    cfg = ctx.org_config or {}
    git_cwd = _resolve_git_cwd(ctx, prev)

    # 56D695F2 Change 1: scope paths to diff since red_commit_sha (not whole
    # working-tree dirty files).  Fall back to _derive_green_paths_from_git
    # when red_sha is absent (e.g. _commit_red_tests was skipped).
    red_sha: str | None = None
    sha_fallback = False
    if isinstance(prev.data, dict):
        red_sha = prev.data.get("red_commit_sha") or cfg.get("red_commit_sha")
    if red_sha:
        # Collect files changed since red_sha: committed, staged, AND untracked new files.
        # untracked=True mirrors _commit_green_code (line 2887) which also uses
        # untracked=True — so lint and commit see the same file set.
        # This ensures GREEN's brand-new (untracked) files are included before
        # _commit_green_code stages and commits them.
        all_paths = git_diff_files(red_sha, git_cwd, untracked=True, segment_filter=None)
        # Filter to .py production files (mirroring _derive_green_paths_from_git logic)
        import fnmatch as _fnmatch
        _TEST_PATTERNS = ("test_*.py", "*_test.py")
        _TEST_SEGMENTS = ("tests/", "__tests__/")
        filtered: list[str] = []
        for p in all_paths:
            if not p.endswith(".py"):
                continue
            filename = p.split("/")[-1] if "/" in p else p
            normalized = p.replace("\\", "/")
            is_test = any(_fnmatch.fnmatch(filename, pat) for pat in _TEST_PATTERNS)
            if not is_test:
                is_test = any(seg in normalized for seg in _TEST_SEGMENTS)
            if not is_test:
                filtered.append(p)
        green_paths = filtered
    else:
        sha_fallback = True
        green_paths = _derive_green_paths_from_git(git_cwd)

    if not green_paths:
        extra: dict = {"skipped": "no_production_files", "findings": []}
        if sha_fallback:
            extra["green_lint_sha_fallback"] = True
        return StepResult(
            status="ok",
            data={**prev.data, **extra},
            duration_ms=0, step_name=step,
        )

    # Defensive boundary check: each derived path must resolve within git_cwd.
    # Paths come from git status so escapes should not happen, but verify anyway.
    git_cwd_resolved = Path(git_cwd).resolve()
    resolved_paths: list[str] = []
    for p in green_paths:
        resolved = (Path(git_cwd) / p).resolve()
        try:
            resolved.relative_to(git_cwd_resolved)
        except ValueError:
            return StepResult(
                status="error",
                data={**prev.data},
                duration_ms=0, step_name=step,
                error=f"green_paths entry escapes git_cwd: {p!r}",
                error_code="E_GREEN_LINT_PATH_ESCAPE",
                recoverable=False,
            )
        resolved_paths.append(str(resolved))

    # GH529 flag-registration gate — warn-only rollout, flip-by:2026-07-24 (issue #529)
    flag_unregistered_warnings: list[str] = []
    if get_config().gate_enabled("HAL_FLAG_UNREGISTERED_GATE"):  # kill switch, default ON
        unregistered = flags_catalog.unregistered_tokens_in_files(resolved_paths)
        if unregistered:
            if get_config().flag("HAL_FLAG_UNREGISTERED_ENFORCE"):   # default OFF
                _fu_cycle = int(prev.data.get("cycle_count", 1)) if isinstance(prev.data, dict) else 1
                _fu_build_class = (ctx.org_config or {}).get("complexity", "SIMPLE").upper()
                _fu_fwd: dict = {
                    "red_commit_sha": red_sha,
                    "spec_path": prev.data.get("spec_path") if isinstance(prev.data, dict) else None,
                    "red_log_path": prev.data.get("red_log_path") if isinstance(prev.data, dict) else None,
                    "validation_doc_path": prev.data.get("validation_doc_path") if isinstance(prev.data, dict) else None,
                    "unregistered_flags": unregistered,
                }
                # GH625 §2.3a: _fu_fwd is hand-built (no **prev.data spread),
                # thread gate_attempts through explicitly or the gate never
                # sees prior spend.
                if isinstance(prev.data, dict) and isinstance(prev.data.get("gate_attempts"), dict):
                    _fu_fwd["gate_attempts"] = prev.data["gate_attempts"]
                return RecoverableGateMixin.gated_step_result(
                    build_class=_fu_build_class,
                    gate="green_lint",
                    cycle=_fu_cycle,
                    retry_from_step_idx=1,
                    error_code="E_FLAG_UNREGISTERED",
                    error_msg=(
                        "unregistered HAL_* flags found in GREEN diff (no flags_catalog entry): "
                        + ", ".join(unregistered)
                    ),
                    step_name=step,
                    forwarded_data=_fu_fwd,
                    terminal_error_code="E_FLAG_UNREGISTERED_CAP2",
                )
            else:
                _emit_safe("flag_unregistered_warn", {"step": step, "tokens": unregistered})
                flag_unregistered_warnings = [
                    f"unregistered HAL flag: {t} (no flags_catalog entry)" for t in unregistered
                ]
    else:
        _emit_safe("gate_disabled", {"gate": "HAL_FLAG_UNREGISTERED_GATE", "step": step, "reason": "env_kill_switch"})

    try:
        proc = bounded_run(
            ["semgrep", "--quiet", "--json", "--config", str(rules_path)] + resolved_paths,
            capture_output=True, text=True, cwd=git_cwd, timeout=60,
        )
    except FileNotFoundError as e:
        return StepResult(
            status="error", data=None, duration_ms=0, step_name=step,
            error=f"semgrep binary missing at invocation: {e}",
            error_code="E_GREEN_LINT_SEMGREP_MISSING",
            recoverable=False,
        )
    if proc.returncode == 124:
        return StepResult(
            status="error", data=None, duration_ms=0, step_name=step,
            error="semgrep exceeded 60s timeout running green_lint rules",
            error_code="E_GREEN_LINT_TIMEOUT",
        )

    try:
        payload = json.loads(proc.stdout) if proc.stdout.strip() else {"results": []}
    except json.JSONDecodeError as e:
        return StepResult(
            status="error", data=None, duration_ms=0, step_name=step,
            error=f"semgrep emitted non-JSON output: {e}; stderr={proc.stderr[-500:]!r}",
            error_code="E_GREEN_LINT_BAD_JSON",
        )

    # semgrep internal errors (broken rule, version mismatch) emit
    # {"errors":[...],"results":[]}. Treat as skipped (preserve prev.data),
    # not silent OK that rubber-stamps the cycle.
    semgrep_errors = payload.get("errors") or []
    if semgrep_errors:
        return StepResult(
            status="ok",
            data={
                **prev.data,
                "skipped": "semgrep_internal_error",
                "errors": semgrep_errors[:5],
            },
            duration_ms=0, step_name=step,
        )

    raw_findings = payload.get("results", []) or []
    findings: list[dict] = []
    errors: list[dict] = []
    # 56D695F2 Change 1: compute added-file set for hunk demotion logic
    added_files: set[str] = set()
    if red_sha:
        added_files = _get_diff_added_files(red_sha, git_cwd)

    for f in raw_findings:
        check_id = str(f.get("check_id", ""))
        # extra.severity is the semgrep canonical place; all green_lint v0 rules are ERROR.
        # Fallback to "ERROR" when extra.severity is missing (be defensive).
        severity = str((f.get("extra") or {}).get("severity") or "").upper()
        if not severity:
            severity = "ERROR"
        record = {
            "check_id": check_id,
            "severity": severity,
            "path": f.get("path"),
            "start": (f.get("start") or {}).get("line"),
        }
        findings.append(record)
        if severity == "ERROR":
            errors.append(record)

    # 56D695F2 Change 1: demote findings outside diff hunks to warnings.
    # Only applies when we have red_sha (scoped-diff mode).
    # Added files: every finding is in-scope (hard-fail).
    # Modified files: demote findings whose start line falls outside diff hunks.
    warnings: list[dict] = []
    if red_sha and errors:
        hard_errors: list[dict] = []
        for record in errors:
            abs_path = str((Path(git_cwd) / (record.get("path") or "")).resolve()) if record.get("path") else ""
            if abs_path in added_files:
                # Newly-added file: always hard-fail
                hard_errors.append(record)
            elif abs_path and _finding_in_diff_hunks(abs_path, red_sha, record.get("start"), git_cwd):
                # Finding inside a diff hunk: hard-fail
                hard_errors.append(record)
            else:
                # Pre-existing finding outside any diff hunk: demote to warning
                warnings.append(record)
        errors = hard_errors

    if errors:
        first = errors[0]
        # 56D695F2 Change 2: recoverable gate with cap-2 retry loop.
        # E843349F: policy matrix is the only path (F3167205 removed flag-off branch).
        cycle_count = int(prev.data.get("cycle_count", 1)) if isinstance(prev.data, dict) else 1
        findings_str = "; ".join(
            f"{r.get('check_id')} at {r.get('path')}:{r.get('start')}"
            for r in errors
        )
        fwd: dict = {
            # Forwarded keys (engine merges into initial_data via Change 6):
            "red_commit_sha": red_sha,
            "spec_path": prev.data.get("spec_path") if isinstance(prev.data, dict) else None,
            "red_log_path": prev.data.get("red_log_path") if isinstance(prev.data, dict) else None,
            "validation_doc_path": prev.data.get("validation_doc_path") if isinstance(prev.data, dict) else None,
            "green_paths": resolved_paths,
            "green_lint_findings": errors,
            "errors": errors,
            "green_lint_warnings": warnings,
            # Human-readable findings string (engine forwards into initial_data as "findings"):
            "findings": findings_str,
        }
        # GH625 §2.3a: fwd is hand-built (no **prev.data spread), so thread
        # gate_attempts through explicitly or the gate never sees prior spend.
        if isinstance(prev.data, dict) and isinstance(prev.data.get("gate_attempts"), dict):
            fwd["gate_attempts"] = prev.data["gate_attempts"]
        build_class = (ctx.org_config or {}).get("complexity", "SIMPLE").upper()
        # 457DC7DC GH371 §2.2: cheap directed-repair pre-stage IN FRONT OF the
        # unchanged RecoverableGateMixin regen return. Non-convergence falls
        # through to today's exact recoverable-regen StepResult (AC6 delta-equal).
        dr_findings = [
            {
                "path": str((Path(git_cwd) / (r.get("path") or "")).resolve()) if r.get("path") else "",
                "line": r.get("start"),
                "rule": r.get("check_id"),
                "evidence": f"{r.get('check_id')} ({r.get('severity')})",
            }
            for r in errors
        ]
        if _directed_repair_enabled(ctx) and dr_findings:
            _dr_artifact = str(dr_findings[0].get("path") or "")
            rr = attempt_directed_repair(
                gate="green_lint",
                artifact_path=_dr_artifact,
                findings=dr_findings,
                rerun_gate=lambda: _verify_green_lint_rules(ctx, prev),
                cheap_model=_resolve_directed_repair_model(cfg),
                repair_step_name="repair_green_lint",
                max_attempts=_repair_cap(cfg),
                ctx=ctx,
            )
            if rr.converged and rr.final is not None:
                return rr.final
        return RecoverableGateMixin.gated_step_result(
            build_class=build_class,
            gate="green_lint",
            cycle=cycle_count,
            retry_from_step_idx=1,
            error_code="E_GREEN_LINT_RETRY",
            error_msg=(
                f"green_lint violation (recoverable retry): "
                f"{first.get('check_id')} at {first.get('path')}:{first.get('start')} "
                f"({len(errors)} total)"
            ),
            step_name=step,
            forwarded_data=fwd,
            terminal_error_code="E_GREEN_LINT_FAIL_CAP2",
        )

    # Human-readable findings string (mirrors the error branch at :3404-3407):
    findings_str = "; ".join(
        f"{r.get('check_id')} at {r.get('path')}:{r.get('start')}"
        for r in findings
    )
    ok_data: dict = {
        "findings": findings_str,
        "green_lint_findings_raw": findings,
        **{k: v for k, v in prev.data.items() if k not in ("findings", "green_lint_findings_raw")},
    }
    if warnings:
        ok_data["green_lint_warnings"] = warnings
    if sha_fallback:
        ok_data["green_lint_sha_fallback"] = True
    if flag_unregistered_warnings:
        ok_data["flag_unregistered_warnings"] = flag_unregistered_warnings
    return StepResult(
        status="ok",
        data=ok_data,
        duration_ms=0, step_name=step,
    )


# ─── Step 4.75: security-lint gate (gh-issue-341, SECBUILD 2-B) ─────────────

_SECURITY_PRAGMA_RE = re.compile(r"(?:#|//)[ \t]*security-lint:[ \t]*allow[ \t]+(\S[^\n]*)")


def _derive_security_lint_paths(prev_data: dict, cfg: dict, git_cwd: str) -> tuple[list[str], bool]:
    """Derive the changed-file set to scan (gh-issue-341, mirrors green_lint's
    _derive_green_paths_from_git fallback shape, but keeps ANY extension —
    only test files are excluded, not restricted to .py)."""
    red_sha = prev_data.get("red_commit_sha") or cfg.get("red_commit_sha")
    if red_sha:
        all_paths = git_diff_files(red_sha, git_cwd, untracked=True, segment_filter=None)
        import fnmatch as _fnmatch
        _TEST_PATTERNS = ("test_*.py", "*_test.py", "*.test.ts", "*.test.sh")
        _TEST_SEGMENTS = ("tests/", "__tests__/")
        filtered: list[str] = []
        for p in all_paths:
            filename = p.split("/")[-1] if "/" in p else p
            normalized = p.replace("\\", "/")
            is_test = any(_fnmatch.fnmatch(filename, pat) for pat in _TEST_PATTERNS)
            if not is_test:
                is_test = any(seg in normalized for seg in _TEST_SEGMENTS)
            if not is_test:
                filtered.append(p)
        return filtered, False
    return _derive_green_paths_from_git(git_cwd), True


def _scan_security_pragma(resolved_paths: list[str]) -> tuple[list[str], list[dict]]:
    """Exclude files carrying a `security-lint: allow <reason>` pragma from the
    scan list (gh-issue-341). A bare `allow` with no reason does NOT exclude —
    the regex requires a non-empty (\\S) reason."""
    scan_paths: list[str] = []
    pragma_hits: list[dict] = []
    for p in resolved_paths:
        try:
            text = Path(p).read_text(errors="replace")
        except OSError:
            scan_paths.append(p)
            continue
        m = _SECURITY_PRAGMA_RE.search(text)
        if m:
            pragma_hits.append({"path": p, "reason": m.group(1).strip()})
        else:
            scan_paths.append(p)
    return scan_paths, pragma_hits


_SECURITY_PRAGMA_ALLOW_RE = re.compile(r"^\s*security-lint-pragma-allow:\s*(\S+)\s*$", re.MULTILINE)


def _classify_pragma_origin(rel_path: str, abs_path: str, red_sha: str | None, git_cwd: str) -> str:
    """GH640 §2.1 — classify whether a security-lint pragma on rel_path was
    already present at red_sha ("baseline") or added since ("added_in_diff").
    Returns "indeterminate" when red_sha is falsy (degraded input)."""
    if not red_sha:
        return "indeterminate"
    try:
        proc = bounded_run(
            ["git", "show", f"{red_sha}:{rel_path}"],
            capture_output=True, text=True, cwd=git_cwd, timeout=30,
        )
        pre_text = proc.stdout if proc.returncode == 0 else ""
    except OSError:
        pre_text = ""
    try:
        post_text = Path(abs_path).read_text(errors="replace")
    except OSError:
        post_text = ""
    tokens = authored_boundary.scan_added_content(pre_text, post_text, [])
    return "added_in_diff" if tokens else "baseline"


def _parse_authorized_pragma_paths(spec_text: str) -> set[str]:
    """GH640 §2.2 — exact-match (no glob) parse of spec §5
    `security-lint-pragma-allow: <path>` authorization lines."""
    return {m.group(1) for m in _SECURITY_PRAGMA_ALLOW_RE.finditer(spec_text or "")}


def _verify_security_lint(ctx, prev) -> StepResult:
    step = "verify_security_lint"
    if not isinstance(prev, StepResult) or not isinstance(prev.data, dict):
        return StepResult(
            status="error", data=None, duration_ms=0, step_name=step,
            error="prev step did not produce data", error_code="E_MISSING_PREV_DATA",
        )

    if not get_config().gate_enabled("HAL_SECURITY_LINT_GATE"):
        _emit_safe("gate_disabled", {
            "gate": "HAL_SECURITY_LINT_GATE",
            "step": step,
            "reason": "env_kill_switch",
        })
        return StepResult(
            status="ok",
            data={**prev.data, "security_lint_skipped": "gate_disabled"},
            duration_ms=0, step_name=step,
        )

    cfg = ctx.org_config or {}
    git_cwd = _resolve_git_cwd(ctx, prev)

    paths, sha_fallback = _derive_security_lint_paths(prev.data, cfg, git_cwd)
    if not paths:
        extra: dict = {"security_lint_skipped": "no_changed_files"}
        if sha_fallback:
            extra["security_lint_sha_fallback"] = True
        return StepResult(
            status="ok",
            data={**prev.data, **extra},
            duration_ms=0, step_name=step,
        )

    # Boundary check: each path must resolve under git_cwd (mirrors green_lint).
    git_cwd_resolved = Path(git_cwd).resolve()
    resolved_paths: list[str] = []
    resolved_rel: dict[str, str] = {}
    for p in paths:
        resolved = (Path(git_cwd) / p).resolve()
        try:
            resolved.relative_to(git_cwd_resolved)
        except ValueError:
            return StepResult(
                status="error",
                data={**prev.data},
                duration_ms=0, step_name=step,
                error=f"security_lint path entry escapes git_cwd: {p!r}",
                error_code="E_SEC_LINT_PATH_ESCAPE",
                recoverable=False,
            )
        resolved_str = str(resolved)
        resolved_paths.append(resolved_str)
        resolved_rel[resolved_str] = p

    scan_paths, pragma_hits = _scan_security_pragma(resolved_paths)
    if pragma_hits:
        # record pragma paths repo-relative to git_cwd (capture abs path
        # BEFORE the rel-rewrite — GH640 needs both for classification).
        abs_by_rel: dict[str, str] = {}
        for hit in pragma_hits:
            abs_path = hit["path"]
            rel = resolved_rel.get(abs_path, abs_path)
            abs_by_rel[rel] = abs_path
            hit["path"] = rel
        _emit_safe("security_lint_pragma_used", {"phase": 5, "files": pragma_hits})

        red_sha = prev.data.get("red_commit_sha") or cfg.get("red_commit_sha")
        try:
            sp = _resolve_scratchpad(ctx) / SPEC_DOC_RELPATH
            spec_text = sp.read_text(encoding="utf-8", errors="replace") if sp.is_file() else ""
        except Exception:
            spec_text = ""
        authorized = _parse_authorized_pragma_paths(spec_text)

        self_exempt: list[dict] = []
        for hit in pragma_hits:
            rel = hit["path"]
            abs_path = abs_by_rel.get(rel, rel)
            origin = _classify_pragma_origin(rel, abs_path, red_sha, git_cwd)
            if origin == "baseline":
                _emit_safe("security_lint_pragma_authorized", {"phase": 5, "path": rel, "reason": "baseline"})
            elif origin == "indeterminate":
                _emit_safe("security_lint_pragma_origin_indeterminate", {"phase": 5, "path": rel})
            else:  # added_in_diff
                if rel in authorized:
                    _emit_safe("security_lint_pragma_authorized", {"phase": 5, "path": rel, "reason": "spec_authorized"})
                else:
                    self_exempt.append({"path": rel, "reason": hit.get("reason", ""), "token": "security-lint: allow"})
                    _emit_safe("security_lint_self_exempt_rejected", {
                        "phase": 5, "path": rel, "reason": hit.get("reason", ""), "token": "security-lint: allow",
                    })

        if self_exempt:
            return StepResult(
                status="error", data={**prev.data, "security_lint_self_exempt": self_exempt},
                duration_ms=0, step_name=step,
                error=f"security-lint REJECT: {len(self_exempt)} self-exempt pragma(s) added in fix diff: "
                      + ", ".join(h["path"] for h in self_exempt),
                error_code="E_SEC_LINT_SELF_EXEMPT", recoverable=False,
            )
    if not scan_paths:
        return StepResult(
            status="ok",
            data={
                **prev.data,
                "security_lint_skipped": "all_files_pragma_allowed",
                "security_lint_pragma_files": pragma_hits,
            },
            duration_ms=0, step_name=step,
        )

    script = Path(cfg.get("security_lint_script") or default_security_asset(
        "security_lint.py", Path(__file__).parents[2] / "security-lint.py"))
    if not script.is_file():
        return StepResult(
            status="error", data=None, duration_ms=0, step_name=step,
            error=f"security-lint.py not found at {script} — build-critical; fail closed",
            error_code="E_SEC_LINT_UNAVAILABLE",
            recoverable=False,
        )

    proc = bounded_run(
        [sys.executable, str(script), *scan_paths, "--json"],
        capture_output=True, text=True, cwd=git_cwd, timeout=180,
    )

    def _parse_stdout(stdout: str):
        try:
            lines = [ln for ln in stdout.splitlines() if ln.strip()]
            if not lines:
                return None
            return json.loads(lines[-1])
        except (json.JSONDecodeError, ValueError):
            return None

    if proc.returncode == 124:
        return StepResult(
            status="error", data=None, duration_ms=0, step_name=step,
            error="security-lint.py exceeded 180s timeout",
            error_code="E_SEC_LINT_TIMEOUT",
            recoverable=False,
        )

    if proc.returncode == 1:
        payload = _parse_stdout(proc.stdout)
        findings = (payload or {}).get("findings", []) if payload else []
        if findings:
            summary = ", ".join(
                f"{f.get('rule_id')} {f.get('path')}:{f.get('line')}" for f in findings[:3]
            )
            msg = f"security-lint REJECT: {len(findings)} HIGH finding(s): {summary}"
        else:
            msg = f"security-lint REJECT: {proc.stdout[-500:]}"
        # 457DC7DC GH371 §2.2: cheap directed-repair pre-stage IN FRONT OF the
        # unchanged terminal E_SEC_LINT return (recoverable=False preserved on
        # non-convergence, AC6). The gate re-runs on the patch — a bad edit
        # cannot pass (§2.5 security invariant).
        dr_findings = [
            {
                "path": str((Path(git_cwd) / (f.get("path") or "")).resolve()) if f.get("path") else "",
                "line": f.get("line"),
                "rule": f.get("rule_id"),
                "evidence": f"{f.get('rule_id')} ({f.get('severity')})",
            }
            for f in findings
        ]
        if _directed_repair_enabled(ctx) and dr_findings:
            _dr_artifact = str(dr_findings[0].get("path") or "")
            rr = attempt_directed_repair(
                gate="security_lint",
                artifact_path=_dr_artifact,
                findings=dr_findings,
                rerun_gate=lambda: _verify_security_lint(ctx, prev),
                cheap_model=_resolve_directed_repair_model(cfg),
                repair_step_name="repair_security_lint",
                max_attempts=_repair_cap(cfg),
                ctx=ctx,
            )
            if rr.converged and rr.final is not None:
                return rr.final
        return StepResult(
            status="error",
            data={**prev.data, "security_lint_findings": findings},
            duration_ms=0, step_name=step,
            error=msg,
            error_code="E_SEC_LINT",
            recoverable=False,
        )

    if proc.returncode == 0:
        payload = _parse_stdout(proc.stdout)
        json_unparsed = payload is None
        findings = (payload or {}).get("findings", []) if payload else []
        warnings = sum(1 for f in findings if f.get("severity") == "MEDIUM")
        ok_data: dict = {**prev.data, "security_lint_warnings": warnings}
        if pragma_hits:
            ok_data["security_lint_pragma_files"] = pragma_hits
        if sha_fallback:
            ok_data["security_lint_sha_fallback"] = True
        if json_unparsed:
            ok_data["security_lint_json_unparsed"] = True
        return StepResult(
            status="ok",
            data=ok_data,
            duration_ms=0, step_name=step,
        )

    # rc == 2 (driver error) or any other unexpected rc: fail closed.
    return StepResult(
        status="error", data=None, duration_ms=0, step_name=step,
        error=f"security-lint.py driver error (rc={proc.returncode}): {proc.stderr[-500:]}",
        error_code="E_SEC_LINT_UNAVAILABLE",
        recoverable=False,
    )


# ─── Helpers: mypy typecheck gate (34AEB235) ─────────────────────────────────
# GH316 §2.2: moved to lib/mypy_baseline.py; re-aliased here under private names
# so all existing call-sites (_mypy_base_argv / _parse_mypy_output / _MYPY_LINE_RE)
# keep working unchanged and object-identity is preserved (AC3).
from lib.mypy_baseline import (  # noqa: E402
    mypy_base_argv as _mypy_base_argv,
    parse_mypy_output as _parse_mypy_output,
    MYPY_LINE_RE as _MYPY_LINE_RE,
)


def _verify_green_typecheck(ctx, prev) -> StepResult:
    """Deterministic mypy typecheck gate (34AEB235).

    Mirrors _verify_green_lint_rules structurally: diff-scoped to changed
    production .py files since red_sha, recoverable retry with cap per policy.
    """
    step = "verify_green_typecheck"
    if not isinstance(prev, StepResult) or not isinstance(prev.data, dict):
        return StepResult(
            status="error", data=None, duration_ms=0, step_name=step,
            error="prev step did not produce data", error_code="E_MISSING_PREV_DATA",
        )

    cfg = ctx.org_config or {}
    git_cwd = _resolve_git_cwd(ctx, prev)

    # Diff-scoping: identical logic to _verify_green_lint_rules (:2453-2482)
    red_sha: str | None = None
    sha_fallback = False
    if isinstance(prev.data, dict):
        red_sha = prev.data.get("red_commit_sha") or cfg.get("red_commit_sha")
    if red_sha:
        all_paths = git_diff_files(red_sha, git_cwd, untracked=True, segment_filter=None)
        import fnmatch as _fnmatch
        _TEST_PATTERNS = ("test_*.py", "*_test.py")
        _TEST_SEGMENTS = ("tests/", "__tests__/")
        filtered: list[str] = []
        for p in all_paths:
            if not p.endswith(".py"):
                continue
            filename = p.split("/")[-1] if "/" in p else p
            normalized = p.replace("\\", "/")
            is_test = any(_fnmatch.fnmatch(filename, pat) for pat in _TEST_PATTERNS)
            if not is_test:
                is_test = any(seg in normalized for seg in _TEST_SEGMENTS)
            if not is_test:
                filtered.append(p)
        green_paths = filtered
    else:
        sha_fallback = True
        green_paths = _derive_green_paths_from_git(git_cwd)

    if not green_paths:
        extra: dict = {"skipped": "no_production_files", "typecheck_findings": []}
        if sha_fallback:
            extra["green_typecheck_sha_fallback"] = True
        return StepResult(
            status="ok",
            data={**prev.data, **extra},
            duration_ms=0, step_name=step,
        )

    # Boundary check: each path must resolve within git_cwd
    git_cwd_resolved = Path(git_cwd).resolve()
    resolved_paths: list[str] = []
    for p in green_paths:
        resolved = (Path(git_cwd) / p).resolve()
        try:
            resolved.relative_to(git_cwd_resolved)
        except ValueError:
            return StepResult(
                status="error",
                data={**prev.data},
                duration_ms=0, step_name=step,
                error=f"green_paths entry escapes git_cwd: {p!r}",
                error_code="E_GREEN_TYPECHECK_PATH_ESCAPE",
                recoverable=False,
            )
        resolved_paths.append(str(resolved))

    try:
        proc = bounded_run(
            _mypy_base_argv(git_cwd) + resolved_paths,
            capture_output=True, text=True, cwd=git_cwd, timeout=60,
        )
    except FileNotFoundError as e:
        return StepResult(
            status="error", data=None, duration_ms=0, step_name=step,
            error=f"mypy binary missing at invocation: {e}",
            error_code="E_GREEN_TYPECHECK_MYPY_MISSING",
            recoverable=False,
        )
    if proc.returncode == 124:
        return StepResult(
            status="error", data=None, duration_ms=0, step_name=step,
            error="mypy exceeded 60s timeout running typecheck",
            error_code="E_GREEN_TYPECHECK_TIMEOUT",
            recoverable=False,
        )

    findings = _parse_mypy_output((proc.stdout or "") + "\n" + (proc.stderr or ""))

    if not findings:
        no_findings_data: dict[str, object] = {**prev.data, "typecheck_findings": []}
        if sha_fallback:
            no_findings_data["green_typecheck_sha_fallback"] = True
        return StepResult(
            status="ok",
            data=no_findings_data,
            duration_ms=0, step_name=step,
        )

    # ── 5C14EF32 net-new-vs-baseline delta gate ───────────────────────────────
    current_count = len(findings)
    baseline_count = _compute_baseline_typecheck_count(resolved_paths, git_cwd)
    enforce = bool(cfg.get("verify_green_typecheck_delta_enforce", True))
    verdict = delta_verdict(baseline_count, current_count, enforce=enforce)
    _emit_safe(
        "verify_green_typecheck_delta_verdict",
        {
            "baseline_failed": verdict.baseline_failed,
            "current_failed": verdict.current_failed,
            "net_new": verdict.net_new,
            "classification": verdict.classification,
            "enforced": enforce,
            "phase": 5,
        },
        severity="warning",
    )
    if not verdict.would_block:
        # Pre-existing or baseline-unavailable findings: surface as warnings, do NOT block.
        warnings_data: dict[str, object] = {
            **prev.data,
            "typecheck_findings": findings,
            "green_typecheck_warnings": findings,
        }
        if sha_fallback:
            warnings_data["green_typecheck_sha_fallback"] = True
        return StepResult(
            status="ok",
            data=warnings_data,
            duration_ms=0, step_name=step,
        )
    # Falls through to recoverable gate below (verdict.would_block == True).

    # Findings: recoverable gate — same key extraction as green_lint (:2605)
    cycle_count = int(prev.data.get("cycle_count", 1)) if isinstance(prev.data, dict) else 1
    build_class = (ctx.org_config or {}).get("complexity") or (
        prev.data.get("build_class") if isinstance(prev.data, dict) else None
    ) or "SIMPLE"
    build_class = build_class.upper()

    findings_str = "; ".join(
        f"{r.get('check_id')} at {r.get('path')}:{r.get('start')}"
        for r in findings
    )
    fwd: dict = {
        **prev.data,
        "typecheck_findings": findings,
        "green_typecheck_findings": findings,
    }
    if sha_fallback:
        fwd["green_typecheck_sha_fallback"] = True
    return RecoverableGateMixin.gated_step_result(
        build_class=build_class,
        gate="green_typecheck",
        cycle=cycle_count,
        retry_from_step_idx=1,
        error_code="E_GREEN_TYPECHECK_RETRY",
        error_msg=(
            f"green_typecheck violation (recoverable retry): "
            f"{findings[0].get('check_id')} at {findings[0].get('path')}:{findings[0].get('start')} "
            f"({len(findings)} total) [net_new={verdict.net_new}]"
        ),
        step_name=step,
        forwarded_data=fwd,
        terminal_error_code="E_GREEN_TYPECHECK_FAIL_CAP2",
    )


# ─── Step 5: build validation prompt ────────────────────────────────────────

# GH705 §2/§1d: maximal contiguous CALL-INVARIANT static instruction run
# hoisted from the validation-prompt scaffold — the OUTPUT format-contract
# literal followed by the ADDITIONALLY structured-JSON literal. Substituting
# one parts.append(_VALIDATION_STABLE_PREFIX) for the two original adjacent
# parts.append(...) calls preserves the joined prompt byte-for-byte (the
# "\n".join(parts) already inserted the same "\n" between the two blocks).
_VALIDATION_STABLE_PREFIX = (
    "OUTPUT — your response IS the file content of\n"
    "reviews/build-opus-validation.md. Start your response DIRECTLY with\n"
    "`## Forward Map`. No preamble. No status markers other than the trailing\n"
    "Verdict line. The file IS the validation doc.\n"
    "\n"
    "REQUIRED sections, in this exact order, no others:\n"
    "\n"
    "  ## Forward Map\n"
    "  <bullets — every spec scenario/criterion → matching test, by name.\n"
    "   If a criterion has no test, write `MISSING` and STOP defaulting to PASS.>\n"
    "\n"
    "  ## Reverse Map\n"
    "  <bullets — every test → matching spec scenario, or `orphan` if the\n"
    "   test has no spec basis. Orphan tests are a PASS-blocker.>\n"
    "\n"
    "  ## Spec Compliance\n"
    "  <bullets — each acceptance criterion → present|missing|partial in test code.>\n"
    "\n"
    "  ## Reachability & Cross-Check\n"
    "  <bullets — for each side-effect/cleanup/terminal AC: Point->Host->Test-path\n"
    "   reachable? §2-vs-§3 parity OK? Any spec token-drift or multi-branch rule\n"
    "   overlap? `none` only if genuinely verified. Unreached Host or §2/§3 mismatch\n"
    "   is a PASS-blocker.>\n"
    "   ### GRAPH-FIRST EVIDENCE: graph-query run (yes/no), node count returned, OR\n"
    "   the grep-fallback reason if the graph was unavailable.\n"
    "\n"
    "  ## Quality Findings\n"
    "  <bullets, or `none`. Threshold-assertion sanity, isolation issues,\n"
    "   missing negative tests, edge-case gaps.>\n"
    "\n"
    "  ### Adversarial edges\n"
    "  <List ONLY adversarial edge scenarios NOT already covered by the spec's\n"
    "   §3 AC-table (e.g. empty-scope, regression-shield, decoy-fence,\n"
    "   boundary off-by-one, concurrent re-entry). Do NOT restate or\n"
    "   duplicate an existing AC — an AC-restatement adds no value and is\n"
    "   forbidden here. If every adversarial edge is already AC-covered,\n"
    "   write `none`.>\n"
    "\n"
    "  ## Verdict Category\n"
    "  <one line: `Category: TEST_GAP` or `Category: SPEC_DEFECT` (omit/`NONE` iff Verdict: PASS).\n"
    "   SPEC_DEFECT iff the blocker is in the SPEC ITSELF — an AC that no test could satisfy\n"
    "   as written, a §2-vs-§3 contradiction, rule-overlap without precedence, token-drift.\n"
    "   Ask: \"could a test author fix this by editing ONLY tests/?\" If NO -> SPEC_DEFECT.\n"
    "   TEST_GAP iff the spec is sound and the RED tests are wrong/missing/weak.>\n"
    "\n"
    "  ## Verdict\n"
    "  <one line: `Verdict: PASS` or `Verdict: FAIL`>\n"
    "\n"
    "ANTI-FABRICATION — producer rules in injection/producer-rules.md\n"
    "(## Anti-Fabrication — Producer Rules) apply. Surface-specific for RED-validator:\n"
    "  - Cite only test names that appear in the actual test files. Don't\n"
    "    invent tests to fill the Forward Map. Missing coverage is a FAIL\n"
    "    signal — surface it, never paper over.\n"
    "  - Don't default to PASS to keep the pipeline moving. Job is to find\n"
    "    gaps; PASSing must be the result of verification.\n"
    "\n"
    "End your response with EXACTLY one of: `Verdict: PASS` or `Verdict: FAIL`.\n"
    "No verdict = invalid validation, blocks the pipeline."
    "\n"
    "\nADDITIONALLY, after the Verdict line, emit a structured JSON block:\n"
    "\n"
    "  ## validation-output (structured)\n"
    "  ```json\n"
    '  {"approve": true, "reject_reason": null, "verdict_category": "NONE"}\n'
    "  ```\n"
    "\n"
    "Set approve=true iff Verdict=PASS. Set reject_reason to a one-sentence summary\n"
    "of the blocking issue iff Verdict=FAIL (else null). Set verdict_category to\n"
    "TEST_GAP or SPEC_DEFECT iff Verdict=FAIL (else NONE) — mirror the ## Verdict\n"
    "Category line above. Engine parses ONLY this block for telemetry; the Verdict\n"
    "line is the authoritative gate. All three fields required."
)


def _build_validation_prompt(ctx, prev) -> StepResult:
    if not isinstance(prev, StepResult) or not isinstance(prev.data, dict):
        return StepResult(
            status="error", data=None, duration_ms=0,
            step_name="build_validation_prompt",
            error="prev step did not produce red_log_path",
            error_code="E_MISSING_PREV_DATA",
        )
    scratchpad = _resolve_scratchpad(ctx)
    red_log = Path(prev.data["red_log_path"])
    spec_path = Path(prev.data["spec_path"])
    cycle = int(prev.data.get("cycle", 1))
    validation_doc_path = scratchpad / _validation_doc_relpath(cycle)

    parts: list[str] = []
    role = _maybe_role_template(ctx)
    if role:
        parts.append(role.rstrip())
        parts.append("")
    parts.append(_read_first_block(scratchpad))
    parts.append("")
    parts.append(
        "ROLE: You are the Opus validator. This is a VALIDATION-ONLY task — you "
        "MUST NOT write or modify test files. Audit the RED tests against the "
        "spec. Do NOT approve by default — your job is to find gaps the test "
        "author missed."
    )
    parts.append("")
    parts.append("FEATURE REQUEST:")
    parts.append(ctx.question or "(no feature request provided)")
    parts.append("")
    parts.append(f"SPEC TO VALIDATE AGAINST (read this file): {spec_path}")
    parts.append(f"RED WORKER REPORT (read this file for test paths): {red_log}")
    parts.append(
        "Then open each test file the RED report names and read it directly. "
        "Do NOT trust the report summary."
    )
    parts.append("")
    parts.append(
        "FOUR-STEP AUDIT:\n"
        "  1. Forward map: every spec scenario → matching test\n"
        "  2. Reverse map: every test → matching spec scenario (flag orphans)\n"
        "  3. Spec compliance: every acceptance criterion present in test code\n"
        "  4. Quality: meaningful assertions, isolation, negative tests, edge cases\n"
    )
    parts.append(
        "EXTENDED HAL AUDIT (workflows.md §3 + §1y) — perform AFTER the four-step audit:\n"
        "  5. §2-vs-§3 cross-check: for every spec §3 AC asserting a post-condition on a\n"
        "     cleanup / finalize / on-failure / terminal / rollback path, confirm the spec\n"
        "     §2 implementation path actually produces it; inversely every §2 cleanup/\n"
        "     finalize/on-exit branch needs >=1 covering AC. Mismatch = FAIL.\n"
        "  6. Reachability trace (§1y): for each side-effect/emit AC, trace Point (the\n"
        "     production line that produces the value) -> Host (the enclosing fn that runs\n"
        "     at test time) -> Test-path (the test's call-entry reaches that Host AND the\n"
        "     fixture makes the asserted value reachable). An AC whose test never reaches\n"
        "     its emit Host, or whose fixture cannot produce the asserted value = FAIL.\n"
        "  7. Spec-internal-consistency: scan the spec for literal-token forms (helper\n"
        "     names, env-var names, event names) and flag inconsistent variants (dot-vs-\n"
        "     underscore etc.) -- a single drift yields 6/6 PASS-FAIL post-GREEN.\n"
        "  8. Rule-overlap simulation: for any AC over a multi-branch dispatcher/classifier,\n"
        "     simulate ALL rule branches in order and confirm the first-matching branch\n"
        "     yields the expected outcome (catches rule N-vs-M ambiguity at spec-time).\n"
    )
    parts.append(
        "GRAPH-FIRST PROTOCOL (DA48BEAC) — for the §1o consumer-dispatch + §1y reachability\n"
        "trace (steps 6 above), navigate via the code graph BEFORE grepping or reading files:\n"
        "  graphify query \"<consumer / reachability question>\" --budget 2000\n"
        "  graphify explain <node_id>            # confirm a Host fn / consumer\n"
        "  graphify affected <node_id>           # §1o consumers / blast radius\n"
        "  graphify path <from_node> <to_node>   # §1y Point -> Host reachability\n"
        "Use grep/Read ONLY when graphify query returns no relevant node OR the staleness\n"
        "gate is dirty (graph.json missing or stale).\n"
    )
    parts.append(_get_anti_fab_prompt())
    red_test_paths: list[str] = prev.data.get("red_test_paths", [])
    red_paths_bullet_list = "\n".join(f"  - {p}" for p in red_test_paths) if red_test_paths else "  (none reported)"
    parts.append(
        "PHASE-SPECIFIC ANTI-FAB:\n"
        "\n"
        "  1. Reverse Map COMPLETENESS:\n"
        "     Your Reverse Map MUST contain a bullet for EVERY file in red_test_paths.\n"
        "     If a test file from red_test_paths does not appear in Reverse Map,\n"
        "     the validation is INCOMPLETE — you MUST list it (mapped or orphan)\n"
        "     before issuing a Verdict. Skipping a test file = Verdict: FAIL.\n"
        "\n"
        f"     red_test_paths for this build:\n{red_paths_bullet_list}\n"
        "\n"
        "  2. TDD-pure GREEN-regression deferral:\n"
        "     A test that trivially passes BEFORE the fix is a GREEN regression,\n"
        "     NOT a missing RED test. Such tests belong under Quality Findings\n"
        "     as `[GREEN-regression-deferral]`, NOT as a FAIL signal. Demanding\n"
        "     RED tests for trivially-passing regressions contradicts strict-RED\n"
        "     TDD orthodoxy. Apply this when an AC's pre-fix behavior already\n"
        "     satisfies the test (e.g., no-duplication invariant, caller-non-\n"
        "     mutation, passthrough behaviors).\n"
    )
    parts.append(_get_behavioral_rubric())
    parts.append(_VALIDATION_STABLE_PREFIX)

    prompt = "\n".join(parts) + "\n\n" + _get_out_of_role_block()
    return StepResult(
        status="ok",
        data={
            "prompt": prompt,
            "doc_path": str(validation_doc_path),
            "spec_path": str(spec_path),
            "red_log_path": str(red_log),
            "red_test_paths": red_test_paths,
            "prompt_bytes": len(prompt.encode("utf-8")),
            "cycle": cycle,
            # 4C0056FA: thread red_commit_sha through to gate_on_validation
            # so commit_green_code receives it as the diff boundary.
            "red_commit_sha": prev.data.get("red_commit_sha"),
            "stable_prefix": _VALIDATION_STABLE_PREFIX,
        },
        duration_ms=0,
        step_name="build_validation_prompt",
    )


# ─── Step 5.1: check RED test is collectable ─────────────────────────────────


def _check_red_executable(ctx, prev) -> StepResult:
    """7C4D70ED: run pytest --collect-only on each red_test_paths entry.

    If collection fails (ImportError, SyntaxError, fixture-resolution failure),
    route through RecoverableGateMixin so SIMPLE/FEATURE gets one retry and
    COMPLEX surfaces immediately.  Healthy path is a transparent pass-through.
    """
    red_test_paths = prev.data.get("red_test_paths", []) if isinstance(prev.data, dict) else []
    if not red_test_paths:
        return StepResult(status="ok", data=prev.data, duration_ms=0, step_name="check_red_executable")

    _break_with_failure = False
    _failure_red_path = ""
    _failure_stderr_tail = ""

    git_cwd = _resolve_git_cwd(ctx, prev)

    for red_path in red_test_paths:
        argv = _collect_probe_argv(red_path, git_cwd)
        if argv is None:
            # Unrecognized extension (e.g. placeholder/non-.py-non-.sh path). Pre-existing
            # semantics: old code pytest-collected it → "file or directory not found" → skipped.
            # Preserve skip (the verdict gate, not this probe, owns such cases). 4238DD14.
            continue

        result = bounded_run(
            argv, cwd=git_cwd, capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 124:
            _failure_stderr_tail = "collect timed out (30s)"
            _failure_red_path = red_path
            _break_with_failure = True
            break

        if result.returncode == 0:
            continue

        # 5325B280 BUG1: pytest rc==5 = EXIT_NOTESTSCOLLECTED — the file imported &
        # collected cleanly but yielded zero test items (fixture-only conftest.py,
        # all-skipped module). Collection SUCCEEDED; nothing to run. NOT a collect
        # failure (malformed RED returns rc 2/3/4, never 5). The verdict gate owns
        # "has a failing test", not this executability probe.
        if result.returncode == 5:
            continue

        stderr_lower = (result.stderr or "").lower()
        if ("file or directory not found" in stderr_lower      # pytest collect missing
                or "no such file or directory" in stderr_lower):  # bash -n missing (.sh)
            # Path not on disk yet (write_red_artifact pending, or fixture-only path).
            # Not a collect-failure — skip and continue to next path.
            continue

        # Real collect failure (SyntaxError, ImportError, fixture resolution, etc.)
        _failure_stderr_tail = (result.stderr or "")[-200:]
        _failure_red_path = red_path
        _break_with_failure = True
        break

    if _break_with_failure:
        # Route through mixin — real failure or timeout
        build_class = (ctx.org_config or {}).get("complexity", "SIMPLE").upper()
        return RecoverableGateMixin.gated_step_result(
            build_class=build_class,
            gate="red_runtime",
            cycle=int(prev.data.get("cycle", 1)),
            retry_from_step_idx=0,
            error_code="E_RED_COLLECT_FAILED",
            error_msg=f"collect probe failed for {_failure_red_path}: {_failure_stderr_tail}",
            step_name="check_red_executable",
            forwarded_data=prev.data,
            terminal_error_code="E_RED_NOT_EXECUTABLE",
        )

    # All paths collected cleanly (or skipped — not yet on disk)
    return StepResult(status="ok", data=prev.data, duration_ms=0, step_name="check_red_executable")


# ─── Step 5: invoke validation LLM ───────────────────────────────────────────


def _invoke_validation_llm(ctx, prev) -> StepResult:
    if not isinstance(prev, StepResult) or not isinstance(prev.data, dict):
        return StepResult(
            status="error", data=None, duration_ms=0,
            step_name="invoke_validation_llm",
            error="prev step did not produce a prompt",
            error_code="E_MISSING_PREV_DATA",
        )
    cfg = ctx.org_config or {}
    model = _resolve_model(cfg, "validation_model", _default_validation_model())
    return invoke_llm_subprocess(
        prompt=prev.data["prompt"],
        model=model,
        timeout_sec=_resolve_validation_timeout_sec(cfg),
        step_name="invoke_validation_llm",
        extra_data={
            "doc_path": prev.data["doc_path"],
            "spec_path": prev.data["spec_path"],
            "red_log_path": prev.data["red_log_path"],
            "red_test_paths": prev.data.get("red_test_paths", []),
            "cycle": prev.data.get("cycle", 1),
            # 4C0056FA: carry red_commit_sha through to write_validation_doc.
            "red_commit_sha": prev.data.get("red_commit_sha"),
        },
        hard_gate=True,
        gate_label="validation",
        allowed_tools=["Read", "Grep", "Glob", "Bash(graphify-shim.sh:*)"],
        stable_prefix=prev.data.get("stable_prefix", ""),
    )


# ─── Step 6: write validation doc ────────────────────────────────────────────


def _write_validation_doc(_ctx, prev) -> StepResult:
    if not isinstance(prev, StepResult) or not isinstance(prev.data, dict):
        return StepResult(
            status="error", data=None, duration_ms=0,
            step_name="write_validation_doc",
            error="prev step did not produce raw_response",
            error_code="E_MISSING_PREV_DATA",
        )
    raw = prev.data["raw_response"]
    doc_path = Path(prev.data["doc_path"])
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    doc_path.write_text(raw, encoding="utf-8")
    verdict = _parse_verdict(raw)
    # ── disk_truth: structured validation verdict (95D3E5F6 Step 4) ─────────────
    # Telemetry-first migration: parse the structured JSON block, emit drift/
    # schema events. Markdown verdict still drives the gate decision.
    structured, error_reason = _parse_validation_structured(raw)
    if structured is not None:
        _emit_safe(
            "validation_structured_ok",
            {"approve": structured.approve, "phase": 5},
            severity="warning",
        )
        # Cross-check: structured.approve vs markdown verdict
        md_says_pass = (verdict == VERDICT_PASS)
        structured_says_pass = bool(structured.approve)
        if md_says_pass != structured_says_pass:
            _emit_safe(
                "validation_verdict_drift",
                {
                    "markdown_verdict": verdict,
                    "structured_approve": structured.approve,
                    "phase": 5,
                },
                severity="warning",
            )
    elif error_reason == "absent":
        _emit_safe("validation_block_absent", {"phase": 5}, severity="warning")
    elif error_reason and error_reason.startswith("schema_violation"):
        _emit_safe(
            "validation_schema_violation",
            {"reason": error_reason, "phase": 5},
            severity="warning",
        )
    elif error_reason == "json_error":
        _emit_safe(
            "validation_schema_violation",
            {"reason": error_reason, "phase": 5},
            severity="warning",
        )
    # ────────────────────────────────────────────────────────────────────────────
    return StepResult(
        status="ok",
        data={
            "validation_doc_path": str(doc_path),
            "spec_path": prev.data["spec_path"],
            "red_log_path": prev.data["red_log_path"],
            "red_test_paths": prev.data.get("red_test_paths", []),
            "validation_bytes_written": len(raw.encode("utf-8")),
            "verdict": verdict,
            "cycle": prev.data.get("cycle", 1),
            "validation_raw": raw,
            # 4C0056FA: thread red_commit_sha through to gate_on_validation.
            "red_commit_sha": prev.data.get("red_commit_sha"),
            # F2C256A5: thread structured verdict so gate can use approve directly.
            "structured_verdict": structured,
        },
        duration_ms=0,
        step_name="write_validation_doc",
    )


# ─── Step 7: verify validation citations ─────────────────────────────────────


def _verify_validation_citations(ctx, prev) -> StepResult:
    return _verify_validation_doc_impl(ctx, prev)


# ─── Step 8: HARD GATE on validation verdict ─────────────────────────────────


def _gate_on_validation(_ctx, prev) -> StepResult:
    """HARD GATE — never_skip_opus_validation_gate. UNKNOWN treated as FAIL.

    D352C2D1: LoopRunner now drives the RED→validation cycle via
    LoopStepContract(until_marker=PASS, marker_field='verdict'). On
    FAIL/UNKNOWN with ``cycle < MAX_VALIDATION_CYCLES``, return status='ok'
    with verdict (so the marker check sees it != PASS and re-iterates) and
    ``cycle`` incremented for next iteration's ``_build_red_prompt``. Findings
    threaded for the cycle-2 REVISION block. On the final cycle, return the
    terminal E_VALIDATION_FAILED so the caller surfaces the abort.

    Pre-D352C2D1 the gate returned status='error' error_code=E_VALIDATION_RETRY
    on cycle<cap; the engine retry hook (engine.py:217-) consumed that and
    restarted from step 0. That hook is preserved for phase_4 spec citations
    but no longer fires for phase_5_implement.
    """
    if not isinstance(prev, StepResult) or not isinstance(prev.data, dict):
        return StepResult(
            status="error", data=None, duration_ms=0,
            step_name="gate_on_validation",
            error="prev step did not produce a verdict",
            error_code="E_MISSING_PREV_DATA",
        )
    verdict = prev.data.get("verdict", VERDICT_UNKNOWN)
    cycle = int(prev.data.get("cycle", 1))
    cap = _resolve_validation_cycle_cap(getattr(_ctx, "org_config", None))
    structured = prev.data.get("structured_verdict")
    if structured is not None:
        passed = bool(structured.approve)
    else:
        passed = (verdict == VERDICT_PASS)
    gate_verdict = _canonical_gate_verdict(passed, verdict)
    if gate_verdict != verdict:
        _emit_safe(
            "gate_verdict_canonicalized",
            {"markdown_verdict": verdict, "gate_verdict": gate_verdict, "cycle": cycle, "phase": 5},
            severity="warning",
        )
    if not passed:
        # GH767 §2.3: bounded auto-reroute of a SPEC_DEFECT verdict back to
        # phase_45_spec. Every precondition below is FAIL-SAFE: on any doubt,
        # fall through to the legacy TEST_GAP path below. A reroute is only
        # ever taken on a fully-trusted, durable budget.
        category = _resolve_verdict_category(structured)
        if category == VERDICT_CATEGORY_SPEC_DEFECT and get_config().flag("HAL_SPEC_DEFECT_REROUTE"):
            run_ctx = telemetry_ctx.get_current_run()
            raw_spec = prev.data.get("spec_path")  # .get, not [] — mirrors L4553 (§1n)
            try:
                scratchpad = _resolve_scratchpad(_ctx)
            except ValueError:
                scratchpad = None
            sha = spec_sha(Path(raw_spec)) if raw_spec else None
            if run_ctx is not None and sha and scratchpad is not None:
                run_id = run_ctx.run_id
                ledger = read_reroute_ledger(scratchpad, run_id)
                if ledger is None:
                    # untrustworthy store -> NEVER reroute (AC15); fall through.
                    _emit_safe("spec_defect_ledger_unavailable", {"cycle": cycle})
                elif sha in ledger:
                    # NO-PROGRESS: phase_45 handed back a spec we already
                    # rejected. Terminal by design — never loop. Ledger NOT
                    # grown (idempotent).
                    _emit_safe(
                        "spec_defect_no_progress",
                        {"spec_sha": sha, "cycle": cycle, "attempts": len(ledger)},
                    )
                    return StepResult(
                        status="error",
                        data={
                            "verdict": gate_verdict,
                            "spec_path": raw_spec,
                            "validation_doc_path": prev.data.get("validation_doc_path"),
                            "spec_sha": sha,
                        },
                        duration_ms=0,
                        step_name="gate_on_validation",
                        error=(
                            f"spec-defect reroute: no progress on repeated spec (sha={sha}, "
                            f"attempts={len(ledger)})"
                        ),
                        error_code="E_SPEC_DEFECT_BUDGET",
                        recoverable=False,
                    )
                else:
                    new_ledger = record_reroute(scratchpad, run_id, sha)
                    if new_ledger is None:
                        # could not durably persist -> NEVER reroute (AC15).
                        _emit_safe("spec_defect_ledger_unavailable", {"cycle": cycle})
                    else:
                        # THE SINGLE budget check (§2.2c) — the monotone floor,
                        # never len(new_ledger).
                        floor = attempts_floor(_ctx, run_ctx, new_ledger)
                        bkey = build_key(_ctx)
                        if floor > _MAX_SPEC_DEFECT_REROUTES:
                            _emit_safe(
                                "spec_defect_budget_exhausted",
                                {"spec_sha": sha, "attempts": floor, "build_key": bkey},
                            )
                            return StepResult(
                                status="error",
                                data={
                                    "verdict": gate_verdict,
                                    "spec_path": raw_spec,
                                    "validation_doc_path": prev.data.get("validation_doc_path"),
                                    "spec_sha": sha,
                                },
                                duration_ms=0,
                                step_name="gate_on_validation",
                                error=f"spec-defect reroute budget ({_MAX_SPEC_DEFECT_REROUTES}) exhausted",
                                error_code="E_SPEC_DEFECT_BUDGET",
                                recoverable=False,
                            )
                        reject_reason = getattr(structured, "reject_reason", None) if structured is not None else None
                        validation_raw = prev.data.get("validation_raw", "") or ""
                        spec_defect_reason = validation_raw[-FINDINGS_MAX_CHARS:]
                        if reject_reason:
                            spec_defect_reason = f"{reject_reason}\n\n{spec_defect_reason}"
                        _emit_safe(
                            "spec_defect_detected",
                            {"spec_sha": sha, "cycle": cycle, "attempt": floor, "build_key": bkey},
                        )
                        return StepResult(
                            status="error",
                            data={
                                "verdict": gate_verdict,
                                "spec_path": raw_spec,
                                "validation_doc_path": prev.data.get("validation_doc_path"),
                                "spec_sha": sha,
                                "reroute_attempt": floor,
                                "spec_defect_reason": spec_defect_reason,
                            },
                            duration_ms=0,
                            step_name="gate_on_validation",
                            error=f"spec defect detected, rerouting to phase_45_spec (attempt {floor})",
                            error_code="E_SPEC_DEFECT",
                            recoverable=False,
                        )
        if cycle < cap:
            # LoopRunner drives iteration. Return ok with verdict (so the
            # marker check sees != PASS and continues) and cycle incremented
            # so next iteration's _build_red_prompt versions the artifact path
            # and threads the REVISION block.
            findings_raw = prev.data.get("validation_raw", "") or ""
            findings = findings_raw[-FINDINGS_MAX_CHARS:]
            # GH706: surface the structured reject_reason at the head of the
            # threaded findings so the cycle-(N+1) RED revision is DIRECTED
            # at the one-sentence gap summary (directed convergence, #697
            # pattern). Computed fresh each call -> idempotent across §1ab
            # resume/re-entry. Existing field only.
            reject_reason = getattr(structured, "reject_reason", None) if structured is not None else None
            if reject_reason:
                findings = f"VALIDATOR REJECT_REASON (cycle {cycle}): {reject_reason}\n\n{findings}"
            return StepResult(
                status="ok",
                data={
                    "verdict": gate_verdict,
                    "markdown_verdict": verdict,
                    "validation_doc_path": prev.data["validation_doc_path"],
                    "spec_path": prev.data.get("spec_path"),
                    "red_log_path": prev.data.get("red_log_path"),
                    "cycle": cycle + 1,
                    "findings": findings,
                },
                duration_ms=0,
                step_name="gate_on_validation",
            )
        return StepResult(
            status="error",
            data={
                "verdict": gate_verdict,
                "markdown_verdict": verdict,
                "validation_doc_path": prev.data["validation_doc_path"],
                "cycle_count": cycle,
            },
            duration_ms=0,
            step_name="gate_on_validation",
            error=f"Opus validation gate blocked workflow: verdict={gate_verdict} (markdown={verdict}, cycle {cycle})",
            error_code="E_VALIDATION_FAILED",
            recoverable=False,
        )
    # GH517 (34E0B77B) — deterministic verdict-gate lint at the phase_5
    # acceptance seam, fires only on the passed=True (APPROVED) path.
    # Kill-switch HAL_VERDICT_GATE_LINT=0. 34E0B77B flip-by:2026-07-17
    if get_config().gate_enabled("HAL_VERDICT_GATE_LINT"):
        try:
            gate_rc, gate_report = _run_verdict_gate(
                prev.data["validation_doc_path"], os.getcwd(), os.environ
            )
        except Exception as exc:  # noqa: BLE001 — driver error, fail-OPEN unless enforce
            gate_rc, gate_report = 2, {"result": "DRIVER_ERROR", "error": str(exc)}
        if gate_rc != 0:
            _emit_safe(
                "verdict_gate_lint_warn",
                {
                    "doc": prev.data["validation_doc_path"],
                    "result": gate_report.get("result"),
                    "rc": gate_rc,
                    "cycle": cycle,
                    "phase": 5,
                },
                severity="warning",
            )
            if get_config().flag("HAL_VERDICT_GATE_LINT_ENFORCE"):
                return StepResult(
                    status="error",
                    data={
                        "verdict": gate_verdict,
                        "validation_doc_path": prev.data["validation_doc_path"],
                        "gate_report": gate_report,
                    },
                    duration_ms=0,
                    step_name="gate_on_validation",
                    error=f"verdict gate lint blocked APPROVED verdict: {gate_report.get('result')}",
                    error_code="E_VERDICT_GATE_LINT",
                    recoverable=False,
                )

    return StepResult(
        status="ok",
        data={
            "verdict": gate_verdict,
            "markdown_verdict": verdict,
            "validation_doc_path": prev.data["validation_doc_path"],
            "spec_path": prev.data["spec_path"],
            "red_log_path": prev.data["red_log_path"],
            "cycle": cycle,
            # Forward red_commit_sha so commit_green_code (4C0056FA) can use it
            # as the diff boundary. Set to None if _commit_red_tests did not run.
            "red_commit_sha": prev.data.get("red_commit_sha"),
            "red_test_paths": prev.data.get("red_test_paths", []),
        },
        duration_ms=0,
        step_name="gate_on_validation",
    )


# ─── Step 8: build GREEN prompt ──────────────────────────────────────────────


_GREEN_STRICT_RAMP_BLOCK: str = (
    "STRICT-TYPING BOY SCOUT (GH292 mypy strict-ramp):\n"
    "  - Any engine_py production `.py` you CREATE or MODIFY must be strict-clean\n"
    "    typed: full annotations, no implicit Any, passes `mypy --strict`. Write it\n"
    "    clean now — do not defer typing.\n"
    "  - After making it strict-clean, append its path (relative to\n"
    "    SYSTEM/cli/build/engine_py/) to `mypy-strict-modules.txt`. The list grows\n"
    "    monotonically — append, never remove.\n"
    "  - WHY: the boy-scout diff-vs-list gate (GH292 Step 2) runs in CI over your\n"
    "    diff. Any new or touched engine_py prod `.py` that is NOT in\n"
    "    mypy-strict-modules.txt (and carries no allowlist deferral) FAILS the gate\n"
    "    and turns CI red. To defer instead of promoting, add an explicit entry\n"
    "    `<module> :: #<issue> :: kill-by:YYYY-MM-DD` to\n"
    "    `mypy-strict-boyscout-allowlist.txt`.\n"
    "  - Modules already listed are enforced at `mypy --strict` by canary Group\n"
    "    G-strict: keep them strict-clean (a new strict error in a listed module\n"
    "    fails the canary).\n"
)


_GREEN_NO_DEFAULT_FALLBACK_BLOCK = (
    "NO-DEFAULT-FALLBACK (#221 fail-open — HARD, green_lint enforces post-write):\n"
    "  - Never mask a missing or failed value with a silent default. No\n"
    "    `x or <default>`, no `.get(k, <default>)` standing in for a required\n"
    "    key, no `except: return <default>/None/[]/{}` that swallows the error.\n"
    "    That is fail-open (#221).\n"
    "  - If a value can be absent, either the SPEC says how to handle it\n"
    "    explicitly or you let it raise/surface — never fabricate a default.\n"
    "    green_lint HARD-FAILs no-default-fallback, silent-fallback-empty-\n"
    "    default-no-log, numeric-or-falsy-default.\n"
)


_GREEN_LINT_DIGEST_FALLBACK = (
    "GREEN-LINT RULESET (enforced post-write; each hit = E_GREEN_LINT retry):\n"
    "  - see scripts/green_lint/rules.yml (digest unavailable)\n"
)

_GREEN_LINT_DIGEST_EXCLUDED_IDS = frozenset(
    {
        "no-default-fallback",
        "silent-fallback-empty-default-no-log",
        "numeric-or-falsy-default",
    }
)


def _green_lint_prompt_digest(rules_path: Path | None = None) -> str:
    """GH724 §2.2 — single-source digest of scripts/green_lint/rules.yml for
    the GREEN prompt. Deterministic regex parse (no yaml import). Never
    raises: missing/unreadable file or zero parsed ids falls back to a
    static one-line block (§1n OWN)."""
    if rules_path is None:
        rules_path = Path(__file__).resolve().parent.parent / "scripts" / "green_lint" / "rules.yml"
    try:
        text = rules_path.read_text(encoding="utf-8")
    except OSError:
        # §2.2: best-effort emit if an emit helper is importable in module
        # scope; none is present here, so this is a documented no-op.
        return _GREEN_LINT_DIGEST_FALLBACK

    lines = text.splitlines()
    entries: list[tuple[str, str]] = []
    i = 0
    while i < len(lines):
        m = re.match(r"^  - id: (\S+)", lines[i])
        if m:
            rule_id = m.group(1)
            hint = ""
            j = i + 1
            while j < len(lines) and not re.match(r"^  - id: ", lines[j]):
                msg_m = re.match(r"^\s*message:\s*(.*)$", lines[j])
                if msg_m:
                    k = j + 1
                    candidate = msg_m.group(1).strip()
                    if candidate and candidate not in ("|", ">", "|-", ">-"):
                        hint = candidate
                    else:
                        while k < len(lines):
                            cand_line = lines[k].strip()
                            if cand_line:
                                hint = cand_line
                                break
                            k += 1
                    break
                j += 1
            if rule_id not in _GREEN_LINT_DIGEST_EXCLUDED_IDS:
                entries.append((rule_id, hint[:100]))
        i += 1

    if not entries:
        return _GREEN_LINT_DIGEST_FALLBACK

    out = ["GREEN-LINT RULESET (enforced post-write; each hit = E_GREEN_LINT retry):\n"]
    for rule_id, hint in entries:
        out.append(f"  - {rule_id}: {hint}\n")
    return "".join(out)


def _green_test_lockdown_block(red_test_paths: list[str] | None) -> str:
    """GH861 §1s: front-loaded test-lockdown block for the GREEN prompt."""
    if not isinstance(red_test_paths, list) or not red_test_paths:
        paths_section = "  (paths unavailable — treat EVERY test file in the repo as frozen)"
    else:
        paths_section = "\n".join(f"  - {p}" for p in red_test_paths)
    return (
        "## TEST LOCKDOWN (READ FIRST — §1s)\n"
        "Test files READ-ONLY. FROZEN — any edit triggers E_RED_TESTS_TAMPERED.\n"
        "If wrong, report 'GREEN BLOCKED — test contract dispute. Diagnosis: [why]'.\n"
        "FROZEN TEST FILES:\n"
        f"{paths_section}"
    )


def _build_green_prompt(ctx, prev) -> StepResult:
    # 56D695F2 Change 5: retry-mode detection.
    # Engine threads initial_data dict as prev when start_step > 0 (retry path).
    # Normal execution: prev is the StepResult of gate_on_validation.
    cycle = 1
    lint_findings: list[dict] = []
    typecheck_findings: list[dict] = []
    test_findings: list = []  # AEC7E800: test-failure feedback (populated in retry path)
    if isinstance(prev, dict):
        # Retry from engine recoverable-retry hook (Class B green_lint cycle 2+).
        cycle = int(prev.get("cycle", 1))
        lint_findings = list(prev.get("green_lint_findings") or [])
        typecheck_findings = list(prev.get("green_typecheck_findings") or [])
        test_findings = list(prev.get("green_test_findings") or [])  # AEC7E800
        prev_data_facade: dict = {
            "spec_path": prev.get("spec_path"),
            "red_log_path": prev.get("red_log_path"),
            "validation_doc_path": prev.get("validation_doc_path"),
        }
        if not all(prev_data_facade.values()):
            return StepResult(
                status="error", data=None, duration_ms=0,
                step_name="build_green_prompt",
                error="retry initial_data missing required forwarded keys",
                error_code="E_RETRY_FORWARDED_DATA_MISSING",
            )
        # Build a full facade with all keys the rest of the function may read
        prev_data_full: dict = dict(prev)
        prev_data_full.update(prev_data_facade)
    elif isinstance(prev, StepResult) and isinstance(prev.data, dict):
        prev_data_facade = prev.data
        prev_data_full = prev.data
    else:
        return StepResult(
            status="error", data=None, duration_ms=0,
            step_name="build_green_prompt",
            error="prev step did not pass through paths",
            error_code="E_MISSING_PREV_DATA",
        )
    scratchpad = _resolve_scratchpad(ctx)
    spec_path = Path(prev_data_facade["spec_path"])
    red_log = Path(prev_data_facade["red_log_path"])
    validation_doc = Path(prev_data_facade["validation_doc_path"])
    green_log_path = scratchpad / GREEN_LOG_RELPATH
    try:
        spec_text = spec_path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        spec_text = ""

    parts: list[str] = []
    role = _maybe_role_template(ctx)
    if role:
        parts.append(role.rstrip())
        parts.append("")
    parts.append(_read_first_block(scratchpad))
    parts.append("")
    parts.append(_green_test_lockdown_block(prev_data_full.get("red_test_paths") or []))
    parts.append("")
    parts.append(
        "ROLE: You are the GREEN worker. Your output IS the implementation code "
        "(written via Edit/Write) plus a final report line — not a narrative "
        "report about implementing. Make all RED tests pass."
    )
    parts.append("")
    parts.append("FEATURE REQUEST:")
    parts.append(ctx.question or "(no feature request provided)")
    parts.append("")
    parts.append(f"SPEC (read this file): {spec_path}")
    parts.append(f"RED WORKER REPORT (lists test files to satisfy): {red_log}")
    parts.append(f"OPUS VALIDATION (verdict PASS — proceed): {validation_doc}")
    parts.append("")
    parts.append(
        "RULES (Boy Scout):\n"
        "  - Apply Boy Scout Rule to EVERY file you touch — clean dead imports,\n"
        "    unclear names, stale comments, unused vars. ALL severities.\n"
        "  - Iteration cap: 3 red→green cycles. If still failing, STOP and\n"
        "    report 'GREEN BLOCKED — [N] tests still failing after 3 iterations.\n"
        "    Diagnosis: [root cause]'.\n"
        "  - EARLY-RETURN: if the impl already exists and tests already pass, run\n"
        "    tests once to confirm, output 'GREEN COMPLETE — N tests passing\n"
        "    (impl pre-existing). Files: [unchanged]', and return.\n"
    )
    if _spec_wants_py_gates(spec_text):
        parts.append(_GREEN_NO_DEFAULT_FALLBACK_BLOCK)
        parts.append(_green_lint_prompt_digest())
    if _spec_wants_py_gates(spec_text):
        parts.append(
            "  - TYPECHECK (GH596): the engine runs a python typecheck gate over your edits after the\n"
            "    marker; run/verify types mentally before emitting GREEN COMPLETE — undefined names,\n"
            "    wrong signatures, and bad imports block the build.\n"
        )
        parts.append(_GREEN_STRICT_RAMP_BLOCK)
    parts.append(_get_producer_anti_fab_prompt())
    cfg = ctx.org_config or {}
    try:
        parts.append(_get_security_fragment(cfg))
    except FileNotFoundError as e:
        return StepResult(
            status="error", data=None, duration_ms=0,
            step_name="build_green_prompt",
            error=f"security fragment missing — build-critical; fail closed: {e}",
            error_code="E_SEC_FRAGMENT_MISSING",
        )
    parts.append(
        "Surface-specific for GREEN:\n"
        "  - SPEC + RED tests are the only authoritative inputs. Don't add CLI\n"
        "    flags, config options, validation paths, or logging absent from\n"
        "    both.\n"
        "  - Boy Scout cleanup is scoped to files you ALREADY touch for the\n"
        "    test fix — NOT permission to roam the repo. If a file doesn't\n"
        "    need a change to pass tests, don't open it for cleanup.\n"
        "  - No new abstractions (factories, plugin layers, single-impl\n"
        "    interfaces) unless the SPEC + ARCHITECTURE require them. Three\n"
        "    similar lines beats a premature abstraction.\n"
        "  - No unrequested error handling or validation for scenarios the\n"
        "    SPEC + RED tests do not cover — but never silence a missing or\n"
        "    failed value with a default fallback; let it surface (#221 fail-open).\n"
    )
    parts.append(
        "RESPONSE BUDGET (HARD):\n"
        "  - Keep response under 2000 tokens.\n"
        "  - Do not echo, do not dump, do not paste, do not include file contents\n"
        "    you edited. No fenced code blocks of edited files. Edit calls ARE\n"
        "    the deliverable; the harness reads the diff, not your reply.\n"
        "  - No narrative, no prose, no commentary, no preamble, no explanation\n"
        "    of changes. Do not explain what you did or why. Skip the recap.\n"
        "  - Allowed in reply: Edit/Write tool calls + the final GREEN\n"
        "    COMPLETE marker. Engine runs tests; do not run pytest yourself.\n"
    )
    # 56D695F2 Change 5: inject GREEN_LINT_VIOLATIONS block before OUTPUT
    # on cycle >= 2 (retry-mode).
    if cycle >= 2 and lint_findings:
        _MAX_FINDINGS_RENDER = 50
        rendered_lines: list[str] = []
        for finding in lint_findings[:_MAX_FINDINGS_RENDER]:
            cid = finding.get("check_id", "unknown")
            path = finding.get("path", "unknown")
            start = finding.get("start", "?")
            rendered_lines.append(f"  {cid} at {path}:{start}")
        extra_count = len(lint_findings) - _MAX_FINDINGS_RENDER
        if extra_count > 0:
            rendered_lines.append(f"  … {extra_count} more")
        violations_block = (
            f"## GREEN_LINT_VIOLATIONS (cycle {cycle})\n"
            "\n"
            "The previous GREEN attempt produced code with the following green_lint violations.\n"
            "Fix all of them in this cycle. These are HARD-FAIL severity (ERROR) — your output\n"
            "will be rejected again if any remain. Findings:\n"
            "\n"
            + "\n".join(rendered_lines)
            + "\n\nGREEN_LINT runs scoped to your diff since the RED commit, so these are violations\n"
            "YOU introduced. Pre-existing violations in untouched code regions are demoted to\n"
            "warnings and do not block."
        )
        parts.append(violations_block)

    # 34AEB235: inject GREEN_TYPECHECK_VIOLATIONS block on cycle >= 2 (retry-mode).
    if cycle >= 2 and typecheck_findings:
        _MAX_FINDINGS_RENDER = 50
        tc_rendered_lines: list[str] = []
        for finding in typecheck_findings[:_MAX_FINDINGS_RENDER]:
            cid = finding.get("check_id", "unknown")
            path = finding.get("path", "unknown")
            start = finding.get("start", "?")
            tc_rendered_lines.append(f"  {cid} at {path}:{start}")
        tc_extra_count = len(typecheck_findings) - _MAX_FINDINGS_RENDER
        if tc_extra_count > 0:
            tc_rendered_lines.append(f"  … {tc_extra_count} more")
        typecheck_block = (
            f"## GREEN_TYPECHECK_VIOLATIONS (cycle {cycle})\n"
            "\n"
            "The previous GREEN attempt produced code with the following mypy type errors.\n"
            "Fix these mypy type errors in your diff. These are HARD-FAIL — your output\n"
            "will be rejected again if any remain. Findings:\n"
            "\n"
            + "\n".join(tc_rendered_lines)
            + "\n\nGREEN_TYPECHECK runs scoped to your diff since the RED commit, so these are\n"
            "type errors YOU introduced. Fix the type errors in the diff."
        )
        parts.append(typecheck_block)

    # AEC7E800: inject ## TEST FAILURES block on cycle >= 2 when test findings present.
    if cycle >= 2 and test_findings:
        tf_rendered_lines: list[str] = []
        for entry in test_findings:
            grp = entry.get("group", "unknown") if isinstance(entry, dict) else "unknown"
            tail = entry.get("tail", str(entry)) if isinstance(entry, dict) else str(entry)
            if tail:
                tf_rendered_lines.append(f"  [{grp}]\n{tail.rstrip()}")
            else:
                tf_rendered_lines.append(f"  [{grp}] (no output captured)")
        test_failures_block = (
            f"## TEST FAILURES (cycle {cycle} — make these pass)\n"
            "\n"
            "The previous GREEN attempt left the following tests failing.\n"
            "Fix the implementation code to make ALL of these tests pass.\n"
            "Do NOT modify test assertions — fix the production code only.\n"
            "\n"
            + "\n".join(tf_rendered_lines)
        )
        parts.append(test_failures_block)

    parts.append(
        "OUTPUT: end your response with EXACTLY these two lines:\n"
        "  TESTS UNTOUCHED: no test file was modified.\n"
        "  GREEN COMPLETE — all [N] tests passing. Files modified: [path1, path2, ...]\n"
        "If the TESTS UNTOUCHED line would be untrue, report GREEN BLOCKED instead."
    )
    parts.append("")
    parts.append(_worktree_edit_boundary_block(_resolve_worktree_root(ctx, scratchpad)))

    prompt = "\n".join(parts) + "\n\n" + _get_out_of_role_block()
    _standards_block = get_standards_context(ctx)
    if _standards_block:
        prompt = _standards_block + prompt
    # Write prompt to scratchpad log so test AC6 can find it
    try:
        green_log_path.parent.mkdir(parents=True, exist_ok=True)
        green_log_path.write_text(prompt, encoding="utf-8")
    except Exception as exc:
        logger.warning("_build_green_prompt: could not write prompt to log: %s", exc)
    return StepResult(
        status="ok",
        data={
            "prompt": prompt,
            "log_path": str(green_log_path),
            "green_prompt_path": str(green_log_path),
            "spec_path": str(spec_path),
            "red_log_path": str(red_log),
            "validation_doc_path": str(validation_doc),
            "prompt_bytes": len(prompt.encode("utf-8")),
            # Carry cycle_count forward so _verify_green_lint_rules can read it
            # (chain: build_green_prompt → cwd_preflight → … → verify_green_lint_rules).
            "cycle_count": cycle,
            # MED #7: carry gate verdict forward so write_green_artifact can
            # include it in final data — engine iteration_finished reads it.
            "verdict": prev_data_full.get("verdict", ""),
            # 4C0056FA: carry red_commit_sha so commit_green_code can use it
            # as the diff boundary to enumerate production files to commit.
            "red_commit_sha": prev_data_full.get("red_commit_sha"),
            "red_test_paths": prev_data_full.get("red_test_paths", []),
        },
        duration_ms=0,
        step_name="build_green_prompt",
    )


# ─── Step 9: invoke GREEN LLM ────────────────────────────────────────────────


def _invoke_green_llm(ctx, prev) -> StepResult:
    if not isinstance(prev, StepResult) or not isinstance(prev.data, dict):
        return StepResult(
            status="error", data=None, duration_ms=0,
            step_name="invoke_green_llm",
            error="prev step did not produce a prompt",
            error_code="E_MISSING_PREV_DATA",
        )
    # GH483: crash-resume seam — a prior GREEN already wrote the
    # implementation to the working tree; skip re-invoking the LLM.
    resume_paths = _read_green_complete_resume(_resolve_scratchpad(ctx), prev.data.get("red_commit_sha"))
    if resume_paths is not None:
        _emit_safe("invoke_green_llm_skipped_green_complete", {
            "phase": 5, "step": "invoke_green_llm", "n_paths": len(resume_paths),
        }, severity="warning")
        return StepResult(
            status="ok",
            data={**prev.data,
                  "raw_response": "GREEN COMPLETE\n\n(green_complete_resume gh483: implementation pre-existing in working tree; GREEN LLM skipped)",
                  "tokens_out": 0, "green_complete_resume": True, "green_resume_paths": list(resume_paths)},
            duration_ms=0,
            step_name="invoke_green_llm",
        )
    cfg = ctx.org_config or {}
    # A3398552: SIMPLE tier uses Haiku for GREEN to save 5-7 min/build; FEATURE/COMPLEX stay Sonnet.
    green_complexity = str(cfg.get("complexity") or "").upper()
    green_default = _tier_haiku_model() if green_complexity == "SIMPLE" else _default_green_model()
    result = invoke_llm_subprocess(
        prompt=prev.data["prompt"],
        model=_resolve_model(cfg, "green_model", green_default),
        timeout_sec=_resolve_green_timeout_sec(cfg),
        step_name="invoke_green_llm",
        extra_data={
            "log_path": prev.data["log_path"],
            "spec_path": prev.data["spec_path"],
            "red_log_path": prev.data["red_log_path"],
            "validation_doc_path": prev.data["validation_doc_path"],
            # MED #7: carry gate verdict so final write_green_artifact data
            # includes it for engine iteration_finished telemetry.
            "verdict": prev.data.get("verdict", ""),
            # 5AE4164A: carry prompt forward so write_green_artifact can
            # retry-once on GREEN_NO_MARKER (transient truncation mitigation).
            "prompt": prev.data["prompt"],
            # 4C0056FA: carry red_commit_sha through to write_green_artifact.
            "red_commit_sha": prev.data.get("red_commit_sha"),
            "red_test_paths": prev.data.get("red_test_paths", []),
            # 56D695F2: carry cycle_count through so _verify_green_lint_rules
            # can detect cap-2 (change 2).
            "cycle_count": prev.data.get("cycle_count", 1),
        },
        allowed_tools=["Read", "Write", "Edit", "Grep", "Glob"],
    )
    # F3 cross-tree edit guard (A4479061): observability only, no auto-revert.
    if result.status == "ok":
        scratchpad = _resolve_scratchpad(ctx)
        worktree_root = _resolve_worktree_root(ctx, scratchpad)
        result = _maybe_emit_cross_tree_warning(result, worktree_root)
    return result


def _check_green_token_budget(ctx, prev) -> StepResult:
    if not isinstance(prev, StepResult) or not isinstance(prev.data, dict):
        return StepResult(status="error", data=None, duration_ms=0,
                          step_name="check_green_token_budget",
                          error="prev step did not produce data",
                          error_code="E_MISSING_PREV_DATA")
    budget = _resolve_green_output_token_budget(getattr(ctx, "org_config", None))
    tokens_out = prev.data.get("tokens_out")
    if tokens_out is None or tokens_out <= budget:
        return StepResult(status="ok", data=prev.data, duration_ms=0,
                          step_name="check_green_token_budget")
    # 1E8EF652: ALERT-class — severity="error" on event_log fallback so a
    # silent-failure-hunter pass doesn't lose the signal when telemetry is
    # also degraded.
    _emit_safe("green_token_budget_alert", {
        "tokens_out": tokens_out,
        "threshold": budget,
        "exceeded_by": tokens_out - budget,
    }, severity="error")
    return StepResult(status="ok", data=prev.data, duration_ms=0,
                      step_name="check_green_token_budget")


# ─── Step 10: write GREEN artifact ───────────────────────────────────────────


def _write_green_artifact(ctx, prev) -> StepResult:
    if not isinstance(prev, StepResult) or not isinstance(prev.data, dict):
        return StepResult(
            status="error", data=None, duration_ms=0,
            step_name="write_green_artifact",
            error="prev step did not produce raw_response",
            error_code="E_MISSING_PREV_DATA",
        )
    raw = prev.data["raw_response"]
    log_path = Path(prev.data["log_path"])
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(raw, encoding="utf-8")

    green_status = _parse_green_status(raw)

    # Retry-once on GREEN_NO_MARKER — transient truncation mitigation (5AE4164A).
    # Mirrors phase_6_review.py _write_fix_artifact (DE71F5F4 sub-3).
    if green_status == GREEN_NO_MARKER:
        cfg = ctx.org_config or {}
        # Re-resolve command from cfg with SIMPLE-tier awareness (matches
        # _invoke_green_llm so retry preserves Haiku for SIMPLE complexity).
        green_complexity = str(cfg.get("complexity") or "").upper()
        green_default = (
            _tier_haiku_model()
            if green_complexity == "SIMPLE"
            else _default_green_model()
        )
        # Push retry step_name into telemetry_ctx so subprocess_spawned/exited
        # events surface 'invoke_green_llm_retry' (else outer ctx step_name
        # 'write_green_artifact' masks the retry in observability/replay).
        prev_run_ctx = telemetry_ctx.get_current_run()
        if prev_run_ctx is not None:
            telemetry_ctx.set_current_run_from(prev_run_ctx, step_name="invoke_green_llm_retry")  # GH375
        try:
            retry_result = invoke_llm_subprocess(
                prompt=prev.data["prompt"],
                model=_resolve_model(cfg, "green_model", green_default),
                timeout_sec=_resolve_green_timeout_sec(cfg),
                step_name="invoke_green_llm_retry",
                extra_data={
                    "log_path": prev.data["log_path"],
                    "spec_path": prev.data["spec_path"],
                    "red_log_path": prev.data["red_log_path"],
                    "validation_doc_path": prev.data["validation_doc_path"],
                    "verdict": prev.data.get("verdict", ""),
                    "prompt": prev.data["prompt"],
                    # 4C0056FA: carry red_commit_sha in case retry error propagates.
                    "red_commit_sha": prev.data.get("red_commit_sha"),
                    "red_test_paths": prev.data.get("red_test_paths", []),
                },
                allowed_tools=["Read", "Write", "Edit", "Grep", "Glob"],
            )
        finally:
            # Restore outer step's telemetry context.
            if prev_run_ctx is not None:
                telemetry_ctx.set_current_run_from(prev_run_ctx, step_name=prev_run_ctx.step_name)  # GH375
        if retry_result.status == "ok" and isinstance(retry_result.data, dict):
            retry_raw = retry_result.data["raw_response"]
            retry_status = _parse_green_status(retry_raw)
            if retry_status != GREEN_NO_MARKER:
                # Retry succeeded — overwrite artifact with retry output.
                log_path.write_text(retry_raw, encoding="utf-8")
                raw = retry_raw
                green_status = retry_status
        elif retry_result.status != "ok":
            # Retry itself failed — propagate error.
            return retry_result
        else:
            # status == "ok" but data is not a dict — malformed subprocess result.
            raise AssertionError(
                f"invoke_llm_subprocess returned ok but data is not a dict: {type(retry_result.data)}"
            )

    common_data = {
        "green_log_path": str(log_path),
        "spec_path": prev.data["spec_path"],
        "red_log_path": prev.data["red_log_path"],
        "validation_doc_path": prev.data["validation_doc_path"],
        "green_bytes_written": len(raw.encode("utf-8")),
        "green_status": green_status,
        # MED #7: verdict carried from gate_on_validation so engine
        # iteration_finished emits raw verdict (never coerced to PASS).
        "verdict": prev.data.get("verdict", ""),
        # 4C0056FA: carry red_commit_sha so commit_green_code has the
        # diff boundary even after write_green_artifact replaces prev.data.
        "red_commit_sha": prev.data.get("red_commit_sha"),
        "red_test_paths": prev.data.get("red_test_paths", []),
        # 56D695F2: carry cycle_count so _verify_green_lint_rules can detect
        # cap-2 (change 2). Default 1 on first cycle.
        "cycle_count": prev.data.get("cycle_count", 1),
    }
    if green_status == GREEN_BLOCKED:
        return StepResult(
            status="error", data=common_data, duration_ms=0,
            step_name="write_green_artifact",
            error="GREEN worker reported BLOCKED — internal 3-cycle loop exhausted",
            error_code="E_GREEN_BLOCKED",
        )
    if green_status == GREEN_NO_MARKER:
        return StepResult(
            status="error", data=common_data, duration_ms=0,
            step_name="write_green_artifact",
            error="GREEN worker output missing completion marker — likely truncated",
            error_code="E_GREEN_NO_MARKER",
        )
    return StepResult(
        status="ok",
        data={**prev.data, **common_data},
        duration_ms=0,
        step_name="write_green_artifact",
    )


# ─── Step 8.5: cwd_preflight (before invoke_green_llm) ──────────────────────


def _cwd_preflight(ctx, prev) -> StepResult:
    """F96D3539: abort GREEN before invoke_llm_subprocess if git_cwd was destroyed.
    Prevents the $87 incident pattern where LLM keeps trying after worktree is gone."""
    cfg = ctx.org_config or {}
    git_cwd = _resolve_git_cwd(ctx, prev)
    if git_cwd and not Path(git_cwd).exists():
        return StepResult(
            status="error",
            data=None,
            duration_ms=0,
            step_name="cwd_preflight",
            error=f"git_cwd path does not exist (likely worktree destroyed): {git_cwd}",
            error_code="E_GREEN_CWD_GONE",
            recoverable=False,
        )
    # Pass through prev data so downstream invoke_green_llm sees the build_green_prompt output
    return StepResult(
        status="ok",
        data=prev.data if isinstance(prev, StepResult) else None,
        duration_ms=0,
        step_name="cwd_preflight",
    )


# ─── Step 10.5: green_watchdog (after invoke_green_llm) ────────────────────


def _green_watchdog(ctx, prev) -> StepResult:
    """post-LLM watchdog — abort if wall-clock 2x SLA; non-fatal ALERT (green_watchdog_token_alert) if tokens exceed budget (32C49788). Wall-clock is the only terminal runaway signal."""
    if not isinstance(prev, StepResult) or not isinstance(prev.data, dict):
        return StepResult(status="error", data=None, duration_ms=0,
                          step_name="green_watchdog",
                          error="prev step did not produce data",
                          error_code="E_MISSING_PREV_DATA")
    cfg = ctx.org_config or {}
    timeout_sec = _resolve_green_timeout_sec(cfg)
    wall_limit_ms = GREEN_WATCHDOG_WALL_MULTIPLIER * timeout_sec * 1000
    token_multiplier = _resolve_green_watchdog_token_multiplier(cfg)
    token_limit = token_multiplier * GREEN_OUTPUT_TOKEN_BUDGET
    duration_ms = prev.data.get("duration_ms") or 0
    raw_tokens_out = prev.data.get("tokens_out")
    if raw_tokens_out is None:
        # llm_subprocess.py:163 — token JSON parse can fail; surface degraded-telemetry GREEN calls.
        # 1E8EF652: ALERT-class — severity="error" on event_log fallback.
        _emit_safe("green_watchdog_tokens_unknown", {"duration_ms": duration_ms}, severity="error")
    tokens_out = raw_tokens_out or 0
    if duration_ms >= wall_limit_ms:
        build_class = (ctx.org_config or {}).get("complexity", "SIMPLE").upper()
        return RecoverableGateMixin.gated_step_result(
            build_class=build_class,
            gate="watchdog_post_commit",
            cycle=int(prev.data.get("cycle_count", 1)),
            retry_from_step_idx=0,
            error_code="E_GREEN_WATCHDOG",
            error_msg=f"GREEN wall-clock {duration_ms}ms exceeds 2x timeout ({wall_limit_ms}ms) — duration watchdog",
            step_name="green_watchdog",
            forwarded_data=prev.data,
            terminal_error_code="E_GREEN_WATCHDOG_ESCALATE",
        )
    if tokens_out >= token_limit:
        # 32C49788: post-commit token VOLUME is not a correctness signal — the GREEN commit
        # already passed RED/Opus/tests/green_lint. Non-fatal ALERT (mirrors _check_green_token_budget,
        # B7442146); wall-clock (above) remains the only terminal runaway signal.
        _emit_safe("green_watchdog_token_alert", {
            "tokens_out": tokens_out,
            "token_limit": token_limit,
            "token_multiplier": token_multiplier,
            "exceeded_by": tokens_out - token_limit,
            "complexity": (ctx.org_config or {}).get("complexity", "SIMPLE").upper(),
        }, severity="error")
        # fall through — do NOT abort a committed, gate-passed GREEN
    # Healthy — pass through
    return StepResult(status="ok", data=prev.data, duration_ms=0, step_name="green_watchdog")


# ─── workflow definition ─────────────────────────────────────────────────────


def build_validation_loop_contract(max_cycles: int = MAX_VALIDATION_CYCLES) -> LoopStepContract:
    """D352C2D1: declarative RED→validation cycle (cap=MAX_VALIDATION_CYCLES).

    Replaces the engine-level E_VALIDATION_RETRY recursion (engine.py:217-)
    with a LoopStepContract driven by LoopRunner. Body = the 11 cyclic steps
    in their original order; until_marker=PASS on data['verdict'] from
    gate_on_validation; max_iterations matches MAX_VALIDATION_CYCLES so cap
    semantics stay identical. Cycle-cap FAIL still emits the terminal
    E_VALIDATION_FAILED via _gate_on_validation; LoopRunner short-circuits
    on body errors with terminated_by='error'.
    """
    return LoopStepContract(
        name="phase_5_validation_cycle",
        body=[
            StepContract(name="build_red_prompt", execute=_build_red_prompt),
            StepContract(name="invoke_red_llm", execute=_invoke_red_llm, resume_sentinel=True),
            StepContract(name="write_red_artifact", execute=_write_red_artifact),
            StepContract(name="commit_red_tests", execute=_commit_red_tests),
            StepContract(name="verify_red_fails_mechanically", execute=_verify_red_fails_mechanically),
            StepContract(name="verify_red_lint_rules", execute=_verify_red_lint_rules),
            StepContract(name="build_validation_prompt", execute=_build_validation_prompt),
            StepContract(name="check_red_executable", execute=_check_red_executable),
            StepContract(name="invoke_validation_llm", execute=_invoke_validation_llm, resume_sentinel=True),
            StepContract(name="write_validation_doc", execute=_write_validation_doc),
            StepContract(name="verify_validation_citations", execute=_verify_validation_citations),
            StepContract(name="gate_on_validation", execute=_gate_on_validation),
        ],
        max_iterations=max_cycles,
        until_marker=VERDICT_PASS,
        marker_field="verdict",
    )


def _validation_cycle_loop_execute(ctx, prev) -> StepResult:
    """Composite step: drive the validation-cycle LoopStepContract via
    LoopRunner. Override step_name so observers see the composite name, not
    the last body step's name.

    GH767 §2.4b — the return leg of the spec-defect reroute. The engine's
    top-level hook (§2.4a, engine.py::execute) cannot see loop-BODY steps
    (invoke_red_llm / invoke_validation_llm live in this LoopStepContract's
    body, not workflow.steps) — so this composite busts its own loop-body
    cycle-1 sentinels itself, using the SAME primitives (§1g), keyed on the
    loop's OWN name so its one-shot consumed-marker cannot collide with
    engine's top-level phase_5_implement-keyed marker. Must run BEFORE
    LoopRunner drives the body so the re-invoked cycle genuinely re-derives
    against the revised spec.
    """
    cap = _resolve_validation_cycle_cap(getattr(ctx, "org_config", None))
    contract = build_validation_loop_contract(cap)
    reroute = (getattr(ctx, "org_config", None) or {}).get("phase_reroute")
    run_ctx = telemetry_ctx.get_current_run()
    if reroute and run_ctx is not None:
        attempt = reroute.get("attempt")
        if not reroute_already_consumed(ctx, run_ctx.run_id, contract.name, attempt) \
                and mark_reroute_consumed(ctx, run_ctx.run_id, contract.name, attempt):
            removed = invalidate_cycle_sentinels(
                ctx, contract.body, 1, run_ctx.run_id,
                lambda et, payload, rid: _emit_safe(et, payload),
                workflow_name=contract.name,
            )
            _emit_safe(
                "phase_reroute_entry",
                {
                    "workflow_name": contract.name,
                    "from_phase": reroute.get("from_phase"),
                    "attempt": attempt,
                    "sentinels_invalidated": len(removed),
                },
            )
    result = LoopRunner(contract).run(ctx, prev)
    return _replace(result, step_name="validation_cycle_loop")


def _build_green_commit_message(cycle: int, paths: list[str]) -> str:
    """Construct the GREEN-commit message.

    Subject:
        build: green cycle <cycle>

    Body:
        Files: <basename1>, <basename2>, ... (first 3 + ...+M more when >5)
    """
    basenames = [Path(p).name for p in paths]
    if len(basenames) > 5:
        head = ", ".join(basenames[:3])
        body_files = f"Files: {head} ...+{len(basenames) - 3} more"
    else:
        body_files = "Files: " + ", ".join(basenames)
    return f"build: green cycle {cycle}\n\n{body_files}\n"


def _commit_green_code(ctx, prev) -> StepResult:
    """Step 5+: engine-authoritative GREEN commit (4C0056FA).

    Reads red_commit_sha from prev.data as the SHA boundary, enumerates
    production (non-test) paths via git_diff_files since that SHA, commits
    them, and emits a green_commit telemetry event.
    """
    _pd_err = prev_data_corruption_reason(prev)
    if _pd_err is not None:
        return StepResult(status="error", data=None, duration_ms=0,
            step_name="commit_green_code",
            error=f"manifest contract violation at consumer: {_pd_err}",
            error_code="E_LLM_MANIFEST_MISSING_AT_CONSUMER", recoverable=False)

    # ── AC2: validate red_commit_sha ─────────────────────────────────────────
    red_commit_sha = (prev.data or {}).get("red_commit_sha")
    hex_chars = set("0123456789abcdef")
    if (
        not red_commit_sha
        or not isinstance(red_commit_sha, str)
        or len(red_commit_sha) != 40
        or not all(c in hex_chars for c in red_commit_sha)
    ):
        return StepResult(
            status="error",
            data=None,
            duration_ms=0,
            step_name="commit_green_code",
            error=(
                f"red_commit_sha missing or invalid in prev.data "
                f"(got {red_commit_sha!r}); cannot determine GREEN diff boundary"
            ),
            error_code="E_MISSING_RED_BOUNDARY",
        )

    cfg = ctx.org_config or {}
    git_cwd = _resolve_git_cwd(ctx, prev)
    scratchpad_dir = cfg.get("scratchpad_dir")
    cycle = int(prev.data.get("cycle", 1))

    # ── 4961254A: manifest-based commit (allowlist inversion) ────────────────
    # Commit exactly the paths the worker wrote — never the dirty tree.
    # Source: worker_written_paths from invoke_llm_subprocess via prev.data.
    # 4C03CCED Ship 1C G1-AC3: canonical accessor replaces legacy .get() pattern.
    if prev.data.get("green_complete_resume"):
        # GH483: resume path — manifest is the detected paths, not the (skipped)
        # LLM subprocess result. Do NOT call manifest_from_result (§1g: its
        # closed manifest_source set stays untouched).
        manifest = list(prev.data.get("green_resume_paths") or [])
        _manifest_src = "green_complete_resume"
    else:
        try:
            manifest, _manifest_src = manifest_from_result(prev)
        except _ManifestError as e:
            return StepResult(
                status="error",
                data=None,
                duration_ms=0,
                step_name="commit_green_code",
                error=f"manifest contract violation at consumer: {e}",
                error_code="E_LLM_MANIFEST_MISSING_AT_CONSUMER",
                recoverable=False,
            )
    prod_paths = _filter_phantom_deleted_paths(
        _filter_gitignored_paths(
            [p for p in manifest if not _is_test_path(p)],
            git_cwd,
        ),
        git_cwd,
    )

    # AC13 (R-MEDIUM-1): telemeter manifest resolution BEFORE git add so an
    # under-commit (transcript dropped a tool call → manifest smaller than
    # reality) is observable in the event log.
    _emit_safe(
        "commit_manifest_resolved",
        {
            "n_manifest": len(manifest),
            "n_committed": len(prod_paths),
            "step": "commit_green_code",
            "phase": 5,
        },
    )

    if not prod_paths:
        _emit_safe(
            "green_commit_skipped",
            {"reason": "empty_manifest", "phase": 5},
            severity="warning",
        )
        # GH483: GC the resume marker even on the empty-manifest skip path.
        if scratchpad_dir:
            try:
                (Path(scratchpad_dir) / GREEN_COMPLETE_RESUME_RELPATH).unlink()
            except OSError:
                pass
        return StepResult(
            status="ok",
            data={**prev.data, "green_commit_sha": None},
            duration_ms=0,
            step_name="commit_green_code",
        )

    # ── GH373 Part A: authored-diff boundary scan (before git add) ──────────
    if get_config().gate_enabled("HAL_AUTHORED_BOUNDARY_GATE"):
        # GH436 §2.3: spec-§5 `authorized-test-edits:` whitelist narrows the
        # tamper leg. Parser never raises — [] on missing/unreadable spec.
        _spec_path = prev.data.get("spec_path")
        authorized_test_edits = (
            _parse_authorized_test_edits(_spec_path)
            if isinstance(_spec_path, str) and _spec_path
            else []
        )
        try:
            scan_result = authored_boundary.scan_boundary(
                "green_commit",
                base_sha=red_commit_sha,
                paths=prod_paths,
                git_cwd=git_cwd,
                is_test_path=_is_test_path,
                authorized_test_edits=authorized_test_edits,
            )
        except RuntimeError as exc:
            return StepResult(
                status="error", data=None, duration_ms=0, step_name="commit_green_code",
                error=f"authored-diff boundary scan failed: {exc}",
                error_code="E_BOUNDARY_SCAN_FAILED",
                recoverable=False,
            )
        if scan_result.suppression_hits:
            return StepResult(
                status="error", data=None, duration_ms=0, step_name="commit_green_code",
                error=f"authored-diff boundary scan found new suppression tokens: {scan_result.suppression_hits!r}",
                error_code="E_BOUNDARY_SUPPRESSION",
                recoverable=False,
            )
        if scan_result.tampered_tests:
            return StepResult(
                status="error", data=None, duration_ms=0, step_name="commit_green_code",
                error=f"authored-diff boundary scan found tampered RED test paths: {scan_result.tampered_tests!r} — {_TAMPER_REMEDIATION_HINT}",
                error_code="E_RED_TESTS_TAMPERED",
                recoverable=False,
            )
        # ── GH639 (7C0FDE44): frozen-hash manifest leg, git-diff-blind residual
        # coverage. No-op when nothing was ever frozen (backward-compat).
        _frozen = _read_red_test_hashes(scratchpad_dir)
        if _frozen:
            _hash_tampered = authored_boundary.verify_red_test_hashes(_frozen, git_cwd, authorized_test_edits)
            if _hash_tampered:
                return StepResult(
                    status="error", data=None, duration_ms=0, step_name="commit_green_code",
                    error=f"frozen-hash integrity check found tampered RED test paths: {_hash_tampered!r} — {_TAMPER_REMEDIATION_HINT}",
                    error_code="E_RED_TESTS_TAMPERED",
                    recoverable=False,
                )
            _reasons = (
                _parse_authorized_test_edits_with_reasons(_spec_path)
                if isinstance(_spec_path, str) and _spec_path
                else {}
            )
            _current = authored_boundary.compute_red_test_hashes(list(_frozen.keys()), git_cwd)
            for _p in (authorized_test_edits or []):
                if _p in _frozen and _current.get(_p) != _frozen[_p]:
                    _emit_safe("red_authorized_test_edit", {"path": _p, "reason": _reasons.get(_p, ""), "boundary": "green_commit"})
    else:
        _emit_safe("gate_disabled", {
            "gate": "HAL_AUTHORED_BOUNDARY_GATE",
            "step": "commit_green_code",
            "reason": "env_kill_switch",
        })

    # ── AC4+AC8: git add ──────────────────────────────────────────────────────
    add, add_outcome = _git_op_with_lock_retry(
        ["git", "add", "--"] + prod_paths, cwd=git_cwd, timeout=30
    )
    if add_outcome == "lock_persisted":
        return StepResult(
            status="error", data=None, duration_ms=0, step_name="commit_green_code",
            error=f"git add: index.lock contention persisted after 3 attempts: {add.stderr[:500]}",
            error_code="E_GIT_LOCKED",
        )
    if add_outcome == "non_lock_error":
        return StepResult(
            status="error", data=None, duration_ms=0, step_name="commit_green_code",
            error=f"git add: {add.stderr[:500]}",
            error_code="E_GREEN_COMMIT_FAILED",
        )
    if add_outcome == "timeout":
        return StepResult(
            status="error", data=None, duration_ms=0, step_name="commit_green_code",
            error="git add: timeout after 30s",
            error_code="E_GIT_TIMEOUT",
        )
    if add_outcome == "os_error":
        return StepResult(
            status="error", data=None, duration_ms=0, step_name="commit_green_code",
            error="git add: OS error invoking subprocess",
            error_code="E_GIT_OS_ERROR",
        )

    # ── AC4+AC8: git commit ───────────────────────────────────────────────────
    # 5325B280 BUG3: idempotent re-entry guard (mirror commit_red_tests guard).
    # On cycle re-run the green commit may already be in the tree → git add stages
    # nothing → "nothing to commit" rc=1 → E_GREEN_COMMIT_FAILED on a successful
    # GREEN. Skip the commit when no staged diff; HEAD (captured below) is the prior
    # green commit. Reuse 9EDB7588 helper (fail-toward-commit on ambiguous git rc).
    if _paths_have_staged_changes(git_cwd, prod_paths):
        commit_message = _build_green_commit_message(cycle, prod_paths)
        cm, cm_outcome = _git_op_with_lock_retry(
            ["git", "commit", "-o", "-m", commit_message, "--", *prod_paths], cwd=git_cwd, timeout=30
        )
        if cm_outcome == "lock_persisted":
            return StepResult(
                status="error", data=None, duration_ms=0, step_name="commit_green_code",
                error=f"git commit: index.lock contention persisted after 3 attempts: {cm.stderr[:500]}",
                error_code="E_GIT_LOCKED",
            )
        if cm_outcome == "non_lock_error":
            return StepResult(
                status="error", data=None, duration_ms=0, step_name="commit_green_code",
                error=f"git commit: {cm.stderr[:500]}",
                error_code="E_GREEN_COMMIT_FAILED",
            )
        if cm_outcome == "timeout":
            return StepResult(
                status="error", data=None, duration_ms=0, step_name="commit_green_code",
                error="git commit: timeout after 30s",
                error_code="E_GIT_TIMEOUT",
            )
        if cm_outcome == "os_error":
            return StepResult(
                status="error", data=None, duration_ms=0, step_name="commit_green_code",
                error="git commit: OS error invoking subprocess",
                error_code="E_GIT_OS_ERROR",
            )
    else:
        _emit_safe(
            "commit_green_code_idempotent_skip",
            {"phase": 5, "step": "commit_green_code", "reason": "no_staged_diff", "cycle": cycle},
        )

    # ── capture post-commit HEAD ──────────────────────────────────────────────
    post_rev = git_port.git_read(
        ["rev-parse", "HEAD"], cwd=git_cwd, timeout=30
    )
    if post_rev.returncode != 0:
        return StepResult(
            status="error", data=None, duration_ms=0, step_name="commit_green_code",
            error=f"git rev-parse HEAD after commit: {post_rev.stderr[:500]}",
            error_code="E_GREEN_COMMIT_FAILED",
        )
    green_sha = post_rev.stdout.strip()

    # ── AC4: emit green_commit telemetry ──────────────────────────────────────
    _emit_safe(
        "green_commit",
        {
            "paths": prod_paths,
            "commit_sha": green_sha,
            "cycle": cycle,
            "n_files": len(prod_paths),
            "phase": 5,
        },
        severity="warning",
    )

    # GH483: GC the resume marker on the normal commit path.
    if scratchpad_dir:
        try:
            (Path(scratchpad_dir) / GREEN_COMPLETE_RESUME_RELPATH).unlink()
        except OSError:
            pass

    # ── AC6: return ok with **prev.data spread + green_commit_sha ────────────
    return StepResult(
        status="ok",
        data={**prev.data, "green_commit_sha": green_sha},
        duration_ms=0,
        step_name="commit_green_code",
    )


def phase_5_implement_workflow() -> WorkflowDefinition:
    return WorkflowDefinition(
        name="phase_5_implement",
        steps=[
            StepContract(name="validation_cycle_loop", execute=_validation_cycle_loop_execute),
            StepContract(name="build_green_prompt", execute=_build_green_prompt),
            StepContract(name="cwd_preflight", execute=_cwd_preflight),
            StepContract(name="invoke_green_llm", execute=_invoke_green_llm, resume_sentinel=True),
            StepContract(name="check_green_token_budget", execute=_check_green_token_budget),
            StepContract(name="write_green_artifact", execute=_write_green_artifact),
            StepContract(name="verify_green_lint_rules", execute=_verify_green_lint_rules),
            StepContract(name="verify_security_lint", execute=_verify_security_lint),
            StepContract(name="verify_green_passing", execute=_verify_green_passing),
            StepContract(name="verify_green_typecheck", execute=_verify_green_typecheck),
            StepContract(name="commit_green_code", execute=_commit_green_code),
            step(
                "green_watchdog",
                _green_watchdog,
                retries=RetryPolicy(max_retries=2, initial_delay_sec=30.0, backoff="exponential"),
                timeout_sec=10.0,
            ),
        ],
    )
