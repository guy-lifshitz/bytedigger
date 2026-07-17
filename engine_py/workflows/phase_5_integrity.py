"""Phase 5 Test-Integrity Diff Guard as a WorkflowDefinition.

Stage 2.10 port (2026-04-25). Ports Step 3.5 of phase-5-implement.md as a
SEPARATE workflow — chains downstream of `phase_5_implement` rather than
folding into it. Two reasons for separation:

1. `phase_5_implement` already has 10 steps + a HARD GATE; bolting a second
   gate on doubles step count and tangles error codes (E_VALIDATION_FAILED
   from validation gate vs E_INTEGRITY_ASSERTION_GAMING from this gate).
2. Some callers run phase-5 implement without integrity (SIMPLE; iterative
   debug). Keeping integrity as a chained workflow lets the orchestrator
   skip it cheaply.

What this workflow does:
    1. `git diff <pre_red_ref> -- <test patterns>` collects test-file
       changes between the pre-RED commit and the current working tree.
    2. If diff is empty → ideal case (phase doc Step 3.5.3): no LLM call
       needed; verdict_override = NO_CHANGES short-circuits the LLM step.
    3. Otherwise: Opus reviewer (HARD GATE) classifies each diff hunk as
       SPEC_CHANGE / LEGITIMATE_REFACTOR / ASSERTION_GAMING. Any
       ASSERTION_GAMING anywhere → workflow errors with
       E_INTEGRITY_ASSERTION_GAMING (cautious default, matches phase doc
       'fix CODE, not tests' rule).

Hard-gate semantics (mirrors phase_5_implement._gate_on_validation):
    ASSERTION_GAMING and missing-marker BOTH block. Same cautious default
    as the validation gate — silent omission of a verdict by the reviewer
    must not pass. Caller decides recovery (revert test changes, fix code).

Token-spend guards:
    - Diff is written to scratchpad and referenced by path; never inlined
      into the prompt (diffs can be huge — same playbook as architecture
      docs in phase_4 / phase_45).
    - Spec doc referenced by path so the reviewer can compare diff scope
      against the spec without paying spec-doc tokens twice.
    - Optional `role_template_path` (~3KB) replaces full CLAUDE.md (~10KB).
    - Default timeout: 600s (read-only diff classification).

Inputs (via `ctx.org_config`):
    scratchpad_dir              — REQUIRED. Absolute path to scratchpad root.
    pre_red_ref                 — Optional. Git ref override (highest priority after
                                  diff_command). Normally the SHA is resolved from
                                  scratchpad/integrity/pre-red-ref.txt (written by
                                  commit_red_tests in phase_5_implement). Falls back
                                  to DEFAULT_PRE_RED_REF = "HEAD~1" only when the
                                  scratchpad file is absent (legacy / SIMPLE path).
    diff_patterns               — Optional. List of git pathspec patterns.
                                  Default ['*test*', '*spec*', '*.test.*'].
    git_cwd                     — Optional. Working directory for git diff subprocess.
                                  Defaults to Path.cwd().
    diff_command                — Optional. Full override of the diff command
                                  (advanced — for callers that need a different
                                  diff source like a stash ref).
    role_template_path          — Optional. Prepended to prompt.
    llm_command                 — Optional. Default: get_claude_critical().
    integrity_llm_command       — Optional. Per-step override of llm_command.
                                  Recommended: pin to Opus here so the harness
                                  cannot silently downgrade the gate (analogue
                                  of `never_skip_opus_validation_gate`).
    integrity_llm_timeout_sec   — Optional. Default 600.

`ctx.question` carries the user's feature request text (used in the prompt
as context for the reviewer).

Steps (3):
    1. build_integrity_prompt    — read RED SHA from scratchpad/integrity/pre-red-ref.txt
                                   (written by commit_red_tests); run working-tree diff
                                   against that ref; write patch file; build prompt.
                                   If diff empty → verdict_override="NO_CHANGES",
                                   no prompt assembled.
    2. invoke_integrity_llm      — opaque subprocess (skipped on verdict_override).
    3. classify_diff_verdict     — write review doc, parse VERDICT marker,
                                   HARD GATE on ASSERTION_GAMING / no-marker.

Outputs:
    $SCRATCHPAD/integrity/test-diff.patch
    $SCRATCHPAD/reviews/build-integrity-review.md  (skipped on NO_CHANGES)

Verdict markers (last-marker-wins via rfind):
    VERDICT: SPEC_CHANGE          → status="ok"
    VERDICT: LEGITIMATE_REFACTOR  → status="ok"
    VERDICT: ASSERTION_GAMING     → status="error", E_INTEGRITY_ASSERTION_GAMING
    VERDICT: NO_CHANGES           → status="ok" (synthetic; only when verdict_override set)
    no marker                     → status="error", E_INTEGRITY_NO_MARKER
"""
from __future__ import annotations

import dataclasses
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))
from lib import git_port  # noqa: E402
from lib.git_cwd import resolve_git_cwd  # noqa: E402  GH381
from lib.schema_smoke import run_schema_smoke  # noqa: E402  GH892
from contracts import StepContract, StepResult, WorkflowContext, WorkflowDefinition
from llm_subprocess import invoke_llm_subprocess

sys.path.insert(0, str(Path(__file__).parent.parent / "lib" / "plugins"))
sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))
from anti_hallucination.helper import (  # noqa: E402
    get_prompt_fragment as _get_anti_fab_prompt,
    get_out_of_role_block as _get_out_of_role_block,
)
from model_config import get_claude_critical  # noqa: E402
from verdict_parse import last_standalone_line_verdict  # noqa: E402
from config_provider import get_config, int_value, timeout_policy_path  # noqa: E402  GH285 C2  GH892
from lib.timeout_policy import DEFAULT_POLICY, cached_policy, resolve_timeout_sec  # noqa: E402  GH285 C2


def _timeout_policy() -> dict:
    return cached_policy(str(timeout_policy_path()))


def _default_model() -> str:
    # 955657B2: integrity gate is HARD GATE (hard_gate=True in invoke_llm_subprocess);
    # ModelConfig "critical" alias resolves to "opus" which passes _is_opus_model
    # via `m == "opus"`. If models.json ever ships a non-Opus critical, the
    # chokepoint refuses with E_HARD_GATE_MODEL_DOWNGRADE — defense in depth.
    return get_claude_critical()


def _default_llm_command() -> list[str]:
    """Back-compat alias returning argv form."""
    from llm_subprocess import _build_claude_argv
    return _build_claude_argv(_default_model())


DEFAULT_LLM_COMMAND = _default_llm_command()
DEFAULT_INTEGRITY_TIMEOUT_SEC = DEFAULT_POLICY["integrity.llm"]["base"]


def _resolve_integrity_timeout_sec(cfg: dict | None) -> int:
    """integrity.llm timeout via unified policy (GH285 C2)."""
    return resolve_timeout_sec("integrity.llm", cfg, policy=_timeout_policy())


def _resolve_integrity_verdict_retries(cfg: dict[str, Any] | None) -> int:
    """GH786: bounded completeness-retry count for Step 2.

    Resolution order: env HAL_INTEGRITY_VERDICT_RETRY_MAX > cfg
    ["integrity_verdict_retry_max"] > default 1. Routed through the config
    seam (get_config().int_value) so the OSS core stays host-decoupled — no
    direct os.environ / HAL_* read (core-boundary lint). Result clamped >= 0;
    env "0" disables retry. Non-int env propagates ValueError per the seam's
    int_value contract (same as timeout_ms).
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
DEFAULT_PRE_RED_REF = "HEAD~1"
DEFAULT_DIFF_PATTERNS = ("*test*", "*spec*", "*.test.*")

SCHEMA_SMOKE_REPORT_RELPATH = "integrity/schema-smoke.json"
DEFAULT_SCHEMA_SMOKE_TIMEOUT_SEC = 60

DIFF_PATCH_RELPATH = "integrity/test-diff.patch"
PRE_RED_REF_RELPATH = "integrity/pre-red-ref.txt"
REVIEW_DOC_RELPATH = "reviews/build-integrity-review.md"
SPEC_DOC_RELPATH = "specs/build-spec.md"

VERDICT_SPEC_CHANGE = "SPEC_CHANGE"
VERDICT_LEGITIMATE_REFACTOR = "LEGITIMATE_REFACTOR"
VERDICT_ASSERTION_GAMING = "ASSERTION_GAMING"
VERDICT_NO_CHANGES = "NO_CHANGES"
VERDICT_UNKNOWN = "UNKNOWN"



def _resolve_scratchpad(ctx) -> Path:
    cfg = ctx.org_config or {}
    raw = cfg.get("scratchpad_dir")
    if not raw:
        raise ValueError("org_config.scratchpad_dir required for phase_5_integrity")
    return Path(raw).expanduser().resolve()


def _resolve_model(cfg: dict, override_key: str = "integrity_model", default: str | None = None) -> str:
    return cfg.get(override_key) or cfg.get("model") or default or _default_model()


def _resolve_command(cfg: dict, override_key: str) -> list[str]:
    """Back-compat alias. Use _resolve_model for new callers."""
    from llm_subprocess import _build_claude_argv
    return _build_claude_argv(_resolve_model(cfg))


def _resolve_diff_command(cfg: dict, scratchpad: Path | None = None) -> list[str]:
    """Build the git diff argv. Resolution order for pre-RED baseline:
    1. `diff_command` full override  2. `org_config.pre_red_ref`
    3. scratchpad/integrity/pre-red-ref.txt  4. DEFAULT_PRE_RED_REF
    Diff form: working-tree (no ..HEAD) so GREEN's uncommitted changes are captured.
    """
    override = cfg.get("diff_command")
    if override:
        return list(override)
    pre_ref = cfg.get("pre_red_ref")
    if not pre_ref and scratchpad is not None:
        ref_file = scratchpad / PRE_RED_REF_RELPATH
        if ref_file.is_file():
            content = ref_file.read_text().strip()
            if not content:
                raise ValueError(
                    "integrity/pre-red-ref.txt exists but is empty — "
                    "commit_red_tests may have failed silently"
                )
            pre_ref = content
    if not pre_ref:
        pre_ref = DEFAULT_PRE_RED_REF
    patterns = list(cfg.get("diff_patterns") or DEFAULT_DIFF_PATTERNS)
    cmd = ["git", "diff", pre_ref, "--"]
    cmd.extend(patterns)
    return cmd


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
        "OUTPUT (markdown — written verbatim to reviews/build-integrity-review.md):\n"
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
        "\n"
        "PRE-SUBMISSION CHECKLIST — self-audit before you finish. If any answer is "
        "\"no\", fix it before you submit:\n"
        "  [ ] My report ENDS with a standalone line that STARTS with `VERDICT: ` followed\n"
        "      by EXACTLY ONE uppercase token: SPEC_CHANGE, LEGITIMATE_REFACTOR, or\n"
        "      ASSERTION_GAMING. Prose alone is NOT a verdict.\n"
        "  [ ] That verdict line is plain text on its OWN line — NOT embedded in a sentence,\n"
        "      NOT inside a heading, list item, table cell, block-quote, or bold/italic\n"
        "      emphasis. A review with no standalone VERDICT line is REJECTED by the gate\n"
        "      (fail-closed) even when the prose reasoning is sound — the omission itself blocks.\n"
    )


# ─── Step 1: collect diff + build prompt ─────────────────────────────────────


def _integrity_stable_prefix() -> str:
    """GH705 §2/§1d: named sourceable helper (§1aa) re-deriving the
    call-invariant TAIL of the integrity prompt — the output schema followed
    by the anti-fabrication rules. Zero-arg, pure; re-computes the exact
    substring already present in the ``parts`` join (does not alter it)."""
    return _integrity_output_schema() + "\n\n" + _get_anti_fab_prompt()


def _build_integrity_prompt(ctx, _prev) -> StepResult:
    scratchpad = _resolve_scratchpad(ctx)
    cfg = ctx.org_config or {}
    try:
        diff_cmd = _resolve_diff_command(cfg, scratchpad)
    except ValueError as e:
        return StepResult(
            status="error", data={"diff_command": []}, duration_ms=0,
            step_name="build_integrity_prompt",
            error=str(e),
            error_code="E_DIFF_EXIT",
            recoverable=False,
        )

    diff_args = diff_cmd[1:] if diff_cmd and diff_cmd[0] == "git" else diff_cmd
    try:
        res = git_port.git_read(
            diff_args,
            cwd=str(resolve_git_cwd(cfg)),
            timeout=60,
        )
    except FileNotFoundError as e:
        return StepResult(
            status="error", data={"diff_command": diff_cmd}, duration_ms=0,
            step_name="build_integrity_prompt",
            error=f"git binary not found: {e}",
            error_code="E_DIFF_CMD_MISSING",
            recoverable=False,
        )

    if res.timed_out:
        return StepResult(
            status="error", data={"diff_command": diff_cmd}, duration_ms=0,
            step_name="build_integrity_prompt",
            error="git diff exceeded 60s",
            error_code="E_DIFF_TIMEOUT",
        )

    if res.returncode != 0:
        return StepResult(
            status="error",
            data={
                "diff_command": diff_cmd,
                "exit_code": res.returncode,
                "stderr": res.stderr[:2000],
            },
            duration_ms=0,
            step_name="build_integrity_prompt",
            error=f"git diff exited {res.returncode}",
            error_code="E_DIFF_EXIT",
        )

    diff_text = res.stdout
    diff_path = scratchpad / DIFF_PATCH_RELPATH
    diff_path.parent.mkdir(parents=True, exist_ok=True)
    diff_path.write_text(diff_text, encoding="utf-8")

    review_path = scratchpad / REVIEW_DOC_RELPATH

    if not diff_text.strip():
        # Phase doc Step 3.5.3 ideal case — skip the LLM entirely.
        return StepResult(
            status="ok",
            data={
                "diff_path": str(diff_path),
                "doc_path": str(review_path),
                "diff_bytes": 0,
                "verdict_override": VERDICT_NO_CHANGES,
                "prompt": None,
                "diff_command": diff_cmd,
            },
            duration_ms=0,
            step_name="build_integrity_prompt",
        )

    spec_path = scratchpad / SPEC_DOC_RELPATH
    parts: list[str] = []
    role = _maybe_role_template(ctx)
    if role:
        parts.append(role.rstrip())
        parts.append("")
    parts.append(_read_first_block(scratchpad))
    parts.append("")
    parts.append(
        "ROLE: You are a Test-Integrity reviewer (HARD GATE). Read the diff "
        "below and the spec, then classify each test-file hunk. Your job is "
        "specifically to catch ASSERTION_GAMING — assertions changed to match "
        "broken code instead of fixing the code. Do NOT default to SPEC_CHANGE."
    )
    parts.append("")
    parts.append("FEATURE REQUEST:")
    parts.append(ctx.question or "(no feature request provided)")
    parts.append("")
    parts.append(f"DIFF (read this file — do NOT trust summaries): {diff_path}")
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
            "stable_prefix": _integrity_stable_prefix(),
        },
        duration_ms=0,
        step_name="build_integrity_prompt",
    )


# ─── Step 2: invoke LLM (skipped on NO_CHANGES) ──────────────────────────────


def _invoke_integrity_llm(ctx, prev) -> StepResult:
    if not isinstance(prev, StepResult) or not isinstance(prev.data, dict):
        return StepResult(
            status="error", data=None, duration_ms=0,
            step_name="invoke_integrity_llm",
            error="prev step did not produce a prompt",
            error_code="E_MISSING_PREV_DATA",
        )
    if prev.data.get("verdict_override"):
        # Short-circuit: empty diff, no LLM needed.
        return StepResult(
            status="ok",
            data={
                "doc_path": prev.data["doc_path"],
                "diff_path": prev.data["diff_path"],
                "verdict_override": prev.data["verdict_override"],
                "raw_response": None,
                "skipped": True,
            },
            duration_ms=0,
            step_name="invoke_integrity_llm",
        )

    cfg = ctx.org_config or {}

    def _attempt() -> StepResult:
        return invoke_llm_subprocess(
            prompt=prev.data["prompt"],
            model=_resolve_model(cfg, "integrity_model", _default_model()),
            timeout_sec=_resolve_integrity_timeout_sec(cfg),
            step_name="invoke_integrity_llm",
            extra_data={
                "doc_path": prev.data["doc_path"],
                "diff_path": prev.data["diff_path"],
            },
            hard_gate=True,
            gate_label="integrity",
            allowed_tools=["Read"],
            stable_prefix=prev.data.get("stable_prefix", ""),
        )

    # GH786: deterministic completeness gate wrapping the LLM call — bounded
    # re-roll on an "ok-but-incomplete" (no standalone VERDICT marker) reply.
    # Reuses _parse_verdict as the sole completeness oracle (never a new/
    # looser recognizer). GH705 invariant preserved: every attempt re-sends
    # the IDENTICAL prompt/stable_prefix (pure re-roll, no prompt mutation).
    max_retries = _resolve_integrity_verdict_retries(cfg)
    retries_performed = 0
    result = _attempt()
    while True:
        if result.status != "ok":
            # Different failure class (subprocess/timeout/error) — passthrough,
            # no completeness retry (AC8).
            return result
        raw = (result.data or {}).get("raw_response") or ""
        complete = _parse_verdict(raw) != VERDICT_UNKNOWN
        if complete or retries_performed >= max_retries:
            break
        retries_performed += 1
        result = _attempt()

    data = dict(result.data or {})
    data["verdict_completeness_retries"] = retries_performed
    return dataclasses.replace(result, data=data)


# ─── Step 3: classify verdict + HARD GATE ────────────────────────────────────


def _parse_verdict(raw: str) -> str:
    """Standalone-line regex match; last winning line's marker is returned.

    Routes through lib/verdict_parse.py P1 (EEFD480F) — single-source of truth.
    Markers embedded in prose, block-quotes, or headings are ignored.
    Last standalone-line match wins by position.
    """
    return last_standalone_line_verdict(
        raw,
        (VERDICT_ASSERTION_GAMING, VERDICT_LEGITIMATE_REFACTOR, VERDICT_SPEC_CHANGE, VERDICT_NO_CHANGES),
        fallback=VERDICT_UNKNOWN,
        allow_trailing=True,
    )


def _classify_diff_verdict(_ctx, prev) -> StepResult:
    if not isinstance(prev, StepResult) or not isinstance(prev.data, dict):
        return StepResult(
            status="error", data=None, duration_ms=0,
            step_name="classify_diff_verdict",
            error="prev step did not produce raw_response",
            error_code="E_MISSING_PREV_DATA",
        )

    doc_path = Path(prev.data["doc_path"])
    diff_path = prev.data["diff_path"]

    if prev.data.get("verdict_override"):
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
            step_name="classify_diff_verdict",
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
            step_name="classify_diff_verdict",
            error="test-integrity gate blocked: ASSERTION_GAMING detected",
            error_code="E_INTEGRITY_ASSERTION_GAMING",
            recoverable=False,
        )
    if verdict == VERDICT_UNKNOWN:
        return StepResult(
            status="error",
            data=common,
            duration_ms=0,
            step_name="classify_diff_verdict",
            error="test-integrity reviewer omitted VERDICT marker",
            error_code="E_INTEGRITY_NO_MARKER",
            recoverable=False,
        )
    return StepResult(
        status="ok",
        data=common,
        duration_ms=0,
        step_name="classify_diff_verdict",
    )


# ─── Step 4: schema smoke (deterministic, no LLM) ────────────────────────────


_SCHEMA_SMOKE_OVERALL_ERROR_CODE = {
    "mismatch": "E_SCHEMA_SMOKE_MISMATCH",
    "unavailable": "E_SCHEMA_SMOKE_UNAVAILABLE",
    "dryrun_error": "E_SCHEMA_SMOKE_DRYRUN_ERROR",
}

_INTEGRITY_PASSTHROUGH_KEYS = ("verdict", "review_path", "diff_path")


def _integrity_passthrough(prev: object) -> dict[str, Any]:
    """Carry classify_diff_verdict's verdict payload through to the final
    (now schema_smoke) StepResult — schema_smoke is the workflow's last step,
    and existing consumers read `result.data["verdict"]` off the final
    result. Copied verbatim; caller's own keys are applied AFTER this and
    win on any clash (e.g. schema_smoke's own "skipped" semantics)."""
    if not isinstance(prev, StepResult) or not isinstance(prev.data, dict):
        return {}
    return {k: prev.data[k] for k in _INTEGRITY_PASSTHROUGH_KEYS if k in prev.data}


def _resolve_schema_smoke_timeout_sec(cfg: dict[str, Any] | None) -> int:
    """GH892: schema-smoke per-target subprocess timeout, resolved from
    org_config (mirrors the `_resolve_integrity_timeout_sec` shape used
    elsewhere in this file — no inline `int(cfg.get("...timeout_sec"))`
    callsite)."""
    cfg = cfg or {}
    raw = cfg.get("schema_smoke_timeout_sec")
    if raw is None:
        return DEFAULT_SCHEMA_SMOKE_TIMEOUT_SEC
    try:
        return int(raw)
    except (TypeError, ValueError):
        return DEFAULT_SCHEMA_SMOKE_TIMEOUT_SEC


def _schema_smoke(ctx: WorkflowContext, prev: object) -> StepResult:
    """GH892: dry-run configured artifacts against real schema snapshots.

    Deterministic 4th step — no LLM call. Kill-switch and not-configured
    both produce explicit `data={"skipped": True, "reason": ...}` (never a
    silent pass, per GH882/#814). See spec §2.1/§2.3 for the full contract.

    schema_smoke is the workflow's LAST step, so its StepResult.data also
    passes through classify_diff_verdict's verdict payload (verdict/
    review_path/diff_path/skipped) for existing consumers.
    """
    step = "schema_smoke"
    scratchpad = _resolve_scratchpad(ctx)
    cfg = ctx.org_config or {}
    passthrough = _integrity_passthrough(prev)

    if not get_config().gate_enabled("HAL_SCHEMA_SMOKE_GATE"):
        return StepResult(
            status="ok",
            data={**passthrough, "skipped": True, "reason": "gate_disabled"},
            duration_ms=0,
            step_name=step,
        )

    targets = cfg.get("schema_smoke_targets")
    if not targets:
        return StepResult(
            status="ok",
            data={**passthrough, "skipped": True, "reason": "not_configured"},
            duration_ms=0,
            step_name=step,
        )

    timeout_sec = _resolve_schema_smoke_timeout_sec(cfg)
    git_cwd = resolve_git_cwd(cfg)

    report = run_schema_smoke(targets, cwd=Path(git_cwd), timeout_sec=timeout_sec)

    report_path = scratchpad / SCHEMA_SMOKE_REPORT_RELPATH
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")

    overall = str(report["overall"])
    if overall == "pass":
        return StepResult(
            status="ok",
            data={**passthrough, "report_path": str(report_path), "overall": overall},
            duration_ms=0,
            step_name=step,
        )

    error_code = _SCHEMA_SMOKE_OVERALL_ERROR_CODE[overall]
    data: dict[str, Any] = {**passthrough, "report_path": str(report_path), "overall": overall}
    if overall == "mismatch":
        data["category"] = "FIXTURE_SCHEMA_MISMATCH"
    return StepResult(
        status="error",
        data=data,
        duration_ms=0,
        step_name=step,
        error=f"schema smoke: overall={overall}",
        error_code=error_code,
        recoverable=False,
    )


def phase_5_integrity_workflow() -> WorkflowDefinition:
    return WorkflowDefinition(
        name="phase_5_integrity",
        steps=[
            StepContract(name="build_integrity_prompt", execute=_build_integrity_prompt),
            StepContract(name="invoke_integrity_llm", execute=_invoke_integrity_llm),
            StepContract(name="classify_diff_verdict", execute=_classify_diff_verdict),
            StepContract(name="schema_smoke", execute=_schema_smoke),
        ],
    )
