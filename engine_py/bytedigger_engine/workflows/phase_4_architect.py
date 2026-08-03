"""Phase 4 (architecture design) as a WorkflowDefinition.

Stage 2.3 port (2026-04-25). Second LLM-heavy phase. Reuses the opaque-LLM-step
pattern from phase_1_discovery: prompt build → subprocess → write.

**Scope of this v0 port — single architect.** Phase-4-architect.md mandates
2-3 parallel architect agents (data-model A, components B, security C). That
parallelism is deliberately deferred until StepContract gains a `fanout` mode
(see the memory index "do not cram parallelism into a single step — pause and design").
v0 produces ONE combined architecture doc covering all three angles. Multi-
architect fanout is Stage 2.4+.

Token-spend guards (same playbook as phase_1_discovery):
    - Prompt does NOT inline injection files; uses READ_FIRST pointer.
    - Prompt does NOT inline research/*.md; lists paths so the LLM opens
      what it needs (matches phase-4-architect.md: "Pass only file paths,
      NOT content" + "do NOT trust summaries").
    - Optional `role_template_path` for ~3KB role-reviewer instead of
      ~10KB CLAUDE.md.
    - llm_timeout_sec defaults to 600 (architecture is heavier than discovery).

Inputs (via `ctx.org_config`):
    scratchpad_dir            — REQUIRED. Absolute path to scratchpad root.
    security_classification   — "HIGH" | "MEDIUM" | "LOW" (default "LOW").
                                Drives a security-focus paragraph in the prompt.
    role_template_path        — Optional. Prepended to prompt if file exists.
    llm_command               — Optional. Default: get_claude_critical().
    llm_timeout_sec           — Optional. Default 600.

`ctx.question` carries the user's feature request text.

Steps (3):
    1. build_architect_prompt  — deterministic prompt assembly + research file list
    2. invoke_architect_llm    — opaque subprocess, prompt→stdin, stdout→raw_response
    3. write_architecture_doc  — write LLM output to architecture/architecture.md

Output doc:
    $SCRATCHPAD/architecture/architecture.md
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

from bytedigger_engine.contracts import StepContract, StepResult, WorkflowDefinition
from bytedigger_engine.llm_subprocess import invoke_llm_subprocess

from bytedigger_engine.lib.plugins.anti_hallucination.helper import (  # noqa: E402
    get_producer_prompt_fragment as _get_producer_anti_fab_prompt,
    get_out_of_role_block as _get_out_of_role_block,
)
from bytedigger_engine.io_utils import atomic_write  # noqa: E402
from bytedigger_engine.lib.model_config import get_claude_critical  # noqa: E402
from bytedigger_engine.skip_logic import make_skip_result, passthrough_if_skipped, should_skip_phase  # noqa: E402
from bytedigger_engine import telemetry_ctx  # noqa: E402
from bytedigger_engine.config_provider import timeout_policy_path  # noqa: E402  GH285 C2
from bytedigger_engine.lib.timeout_policy import DEFAULT_POLICY, cached_policy, resolve_timeout_sec  # noqa: E402  GH285 C2

logger = logging.getLogger(__name__)


def _timeout_policy() -> dict:
    return cached_policy(str(timeout_policy_path()))


def _default_model() -> str:
    return get_claude_critical()


def _default_llm_command() -> list[str]:
    """Back-compat alias returning argv form."""
    from bytedigger_engine.llm_subprocess import _build_claude_argv
    return _build_claude_argv(_default_model())


def _resolve_model(cfg: dict, override_key: str = "architect_model", default: str | None = None) -> str:
    """Per-step override → global model → default."""
    return cfg.get(override_key) or cfg.get("model") or default or _default_model()


def _resolve_command(cfg: dict, override_key: str = "llm_command") -> list[str]:
    """Back-compat alias. Use _resolve_model for new callers."""
    from bytedigger_engine.llm_subprocess import _build_claude_argv
    return _build_claude_argv(_resolve_model(cfg))


# Backward-compat module-level constant (computed once at import via ModelConfig).
DEFAULT_LLM_COMMAND = _default_llm_command()
DEFAULT_LLM_TIMEOUT_SEC = DEFAULT_POLICY["architect.llm"]["base"]


def _resolve_llm_timeout_sec(cfg: dict | None) -> int:
    """architect.llm timeout via unified policy (GH285 C2)."""
    return resolve_timeout_sec("architect.llm", cfg, policy=_timeout_policy())

ARCHITECTURE_DOC_RELPATH = "architecture/architecture.md"
RESEARCH_DIR_RELPATH = "research"

VALID_SECURITY_LEVELS = ("LOW", "MEDIUM", "HIGH")


def _resolve_scratchpad(ctx) -> Path:
    cfg = ctx.org_config or {}
    raw = cfg.get("scratchpad_dir")
    if not raw:
        raise ValueError("org_config.scratchpad_dir required for phase_4_architect")
    return Path(raw).expanduser().resolve()


def _resolve_security_classification(ctx) -> str:
    cfg = ctx.org_config or {}
    s = (cfg.get("security_classification") or "LOW").upper()
    if s not in VALID_SECURITY_LEVELS:
        raise ValueError(
            f"security_classification must be one of {VALID_SECURITY_LEVELS}, got {s!r}"
        )
    return s


def _list_research_files(scratchpad: Path) -> list[Path]:
    research_dir = scratchpad / RESEARCH_DIR_RELPATH
    if not research_dir.is_dir():
        return []
    return sorted(p for p in research_dir.glob("*.md") if p.is_file())


def _security_focus_block(level: str) -> str:
    if level == "HIGH":
        return (
            "SECURITY FOCUS (HIGH):\n"
            "  - Threat model + trust boundaries\n"
            "  - Input validation strategy\n"
            "  - Secret management (rotation, storage, access control)\n"
        )
    if level == "MEDIUM":
        return (
            "SECURITY FOCUS (MEDIUM):\n"
            "  - Validate inputs at trust boundaries\n"
            "  - No secrets in code\n"
        )
    return "SECURITY FOCUS (LOW): standard hygiene only.\n"


def _output_schema_block(doc_path: str) -> str:
    return (
        f"OUTPUT — write the FULL architecture doc to EXACTLY this file path using the Write tool:\n"
        f"  {doc_path}\n"
        "That file IS the architecture deliverable. Your text response MUST be ONLY a short\n"
        "(≤200-token) confirmation: the path written + one-line summary. Do NOT echo the\n"
        "architecture body in your text response.\n"
        "\n"
        "Start the architecture doc DIRECTLY with `## Context`. No preamble. No status\n"
        "markers (`STATUS:`, `DONE`, `Architecture written`) and no meta-commentary inside\n"
        "the file. The file IS the architecture doc.\n"
        "\n"
        "REQUIRED sections, in this exact order, no others:\n"
        "\n"
        "  ## Context\n"
        "  <2-4 sentences anchoring in research findings: what exists, what is\n"
        "   being added, why this approach. Cite research/*.md filenames.>\n"
        "\n"
        "  ## Approach\n"
        "  <implementation approach with reasoning. Stay strictly within the\n"
        "   FEATURE REQUEST + research findings — do not propose unrelated\n"
        "   refactors.>\n"
        "\n"
        "  ## Trade-offs\n"
        "  <acknowledged trade-offs of this approach vs alternatives. Each\n"
        "   trade-off must reference a real constraint, not a hypothetical one.>\n"
        "\n"
        "  ## Files\n"
        "  <files to create or modify, full paths, one per line. Only files\n"
        "   strictly required for the FEATURE REQUEST. No `while we are here`\n"
        "   touches.>\n"
        "\n"
        "  ## Data Model\n"
        "  <if applicable. If the feature does not introduce data, write `n/a`\n"
        "   — do NOT invent a schema to fill the section.>\n"
        "\n"
        "  ## Component Boundaries\n"
        "  <integration strategy. Where the new pieces plug into existing\n"
        "   components named in research/*.md. No invented seams.>\n"
        "\n"
        "  ## Implementation Order\n"
        "  <numbered steps with explicit dependencies. Each step must map to\n"
        "   files listed in ## Files above — no orphan steps.>\n"
        "\n"
        "  ## Out of Scope\n"
        "  <bullets — adjacent features, refactors, or improvements explicitly\n"
        "   NOT in this work. List the plausible-but-unrequested additions a\n"
        "   reader might assume the architecture covers.>\n"
        "\n"
        "  ## Open Questions\n"
        "  <bullets, or `none`. Park ambiguous trade-offs and missing inputs\n"
        "   here. Do NOT invent an answer to keep the design moving.>\n"
        "\n"
        + _get_producer_anti_fab_prompt()
        + "\n"
        "Surface-specific for ARCHITECT:\n"
        "  - No premature factories, plugin layers, `extensible` interfaces with\n"
        "    one implementation, or `for future flexibility` abstractions. The\n"
        "    architecture must serve the FEATURE REQUEST, not hypothetical\n"
        "    extensions.\n"
        "  - Trade-offs must compare REAL alternatives. Do NOT invent a strawman\n"
        "    just to dismiss it.\n"
        "  - Length budget: a focused architecture for a SIMPLE-to-FEATURE task\n"
        "    is 60-150 lines.\n"
    )


def _build_prompt(ctx, scratchpad: Path, security: str, research_files: list[Path]) -> str:
    cfg = ctx.org_config or {}
    parts: list[str] = []

    role_path = cfg.get("role_template_path")
    if role_path:
        rp = Path(role_path).expanduser()
        if rp.is_file():
            parts.append(rp.read_text(encoding="utf-8").rstrip())
            parts.append("")

    inj = scratchpad / "injection"
    parts.append(
        "READ_FIRST — read these five files before proceeding:\n"
        f"- {inj}/hal-memory.md      (learnings)\n"
        f"- {inj}/constitution.md    (project rules)\n"
        f"- {inj}/quality-gate.md    (zero-cornercutting policy)\n"
        f"- {inj}/producer-rules.md  (producer anti-fabrication)\n"
        f"- {inj}/active-work.md     (current project focus)\n"
        "If any file is missing or empty: orchestrator Phase 0.5 failed — "
        "STATUS=block with SUMMARY 'injection files missing'."
    )
    parts.append("")
    parts.append(
        "ROLE: You are an architecture agent. You WRITE the architecture file "
        "(via the Write tool) — not a report about it. Design the implementation. Open "
        "research/*.md and any cited source files yourself — do NOT trust "
        "summaries, do NOT cite anything you have not verified."
    )
    parts.append("")
    parts.append("FEATURE REQUEST:")
    parts.append(ctx.question or "(no feature request provided)")
    parts.append("")

    if research_files:
        parts.append("RESEARCH FINDINGS (read these — do NOT trust summaries):")
        for p in research_files:
            parts.append(f"- {p}")
    else:
        parts.append("RESEARCH FINDINGS: (none — research dir empty)")
    parts.append("")

    doc_path = scratchpad / ARCHITECTURE_DOC_RELPATH
    parts.append(_security_focus_block(security))
    parts.append(_output_schema_block(str(doc_path)))
    return "\n".join(parts)


def _check_decision_doc_skip(ctx, _prev) -> StepResult:
    """Entry step: if decision_doc present + FEATURE/COMPLEX → skip entire phase."""
    cfg = ctx.org_config or {}
    skip, ddoc_path = should_skip_phase(cfg)
    if skip:
        return make_skip_result("check_decision_doc_skip", ddoc_path or "")
    return StepResult(
        status="ok",
        data={"skipped": False},
        duration_ms=0,
        step_name="check_decision_doc_skip",
    )


def _build_architect_prompt(ctx, prev) -> StepResult:
    pt = passthrough_if_skipped(prev, "build_architect_prompt")
    if pt is not None:
        return pt
    scratchpad = _resolve_scratchpad(ctx)
    security = _resolve_security_classification(ctx)
    research_files = _list_research_files(scratchpad)
    prompt = _build_prompt(ctx, scratchpad, security, research_files) + "\n\n" + _get_out_of_role_block()
    doc_path = scratchpad / ARCHITECTURE_DOC_RELPATH
    return StepResult(
        status="ok",
        data={
            "prompt": prompt,
            "doc_path": str(doc_path),
            "security_classification": security,
            "research_file_count": len(research_files),
            "prompt_bytes": len(prompt.encode("utf-8")),
        },
        duration_ms=0,
        step_name="build_architect_prompt",
    )


def _invoke_architect_llm(ctx, prev) -> StepResult:
    pt = passthrough_if_skipped(prev, "invoke_architect_llm")
    if pt is not None:
        return pt
    if not isinstance(prev, StepResult) or not isinstance(prev.data, dict):
        return StepResult(
            status="error",
            data=None,
            duration_ms=0,
            step_name="invoke_architect_llm",
            error="prev step did not produce a prompt",
            error_code="E_MISSING_PREV_DATA",
        )

    _doc = prev.data.get("doc_path")
    if _doc:
        try:
            Path(_doc).unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass
    cfg = ctx.org_config or {}
    return invoke_llm_subprocess(
        prompt=prev.data["prompt"],
        model=_resolve_model(cfg, "architect_model", _default_model()),
        timeout_sec=_resolve_llm_timeout_sec(cfg),
        step_name="invoke_architect_llm",
        extra_data={
            "doc_path": prev.data["doc_path"],
            "security_classification": prev.data["security_classification"],
        },
        allowed_tools=["Read", "Grep", "Glob", "Write"],
    )


def _emit_safe(event_type: str, payload: dict) -> None:
    """Emit telemetry event via current run context; swallow all errors."""
    run_ctx = telemetry_ctx.get_current_run()
    if run_ctx is None or run_ctx.event_log is None:
        return
    try:
        run_ctx.event_log.append(event_type, payload, run_ctx.run_id)
    except Exception as e:  # noqa: BLE001
        logger.warning("telemetry append failed for %s: %s", event_type, e)


def _resolve_architect_source(doc_path, raw_response: str) -> tuple[str, str]:
    """Return (body, source) selecting the architecture doc body from file or raw_response.

    If doc_path is a file with non-whitespace content, return (file_text, "worker_file").
    Otherwise return (raw_response, "raw_response_fallback") — fail-open fallback that
    preserves behavior when the subagent does not write the file.
    (FD2592D9 §1aa named sourceable helper — §2.4)
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


def _write_architecture_doc(_ctx, prev) -> StepResult:
    pt = passthrough_if_skipped(prev, "write_architecture_doc")
    if pt is not None:
        return pt
    if not isinstance(prev, StepResult) or not isinstance(prev.data, dict):
        return StepResult(
            status="error",
            data=None,
            duration_ms=0,
            step_name="write_architecture_doc",
            error="prev step did not produce raw_response",
            error_code="E_MISSING_PREV_DATA",
        )

    raw_response = prev.data["raw_response"]
    doc_path = Path(prev.data["doc_path"])
    body, source = _resolve_architect_source(str(doc_path), raw_response)
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(doc_path, body)
    _emit_safe("architect_writer_return_source", {"source": source, "bytes": len(body.encode())})

    return StepResult(
        status="ok",
        data={
            "doc_path": str(doc_path),
            "bytes_written": len(body.encode("utf-8")),
            "security_classification": prev.data["security_classification"],
        },
        duration_ms=0,
        step_name="write_architecture_doc",
    )


def phase_4_architect_workflow() -> WorkflowDefinition:
    return WorkflowDefinition(
        name="phase_4_architect",
        steps=[
            StepContract(name="check_decision_doc_skip", execute=_check_decision_doc_skip),
            StepContract(name="build_architect_prompt", execute=_build_architect_prompt),
            StepContract(name="invoke_architect_llm", execute=_invoke_architect_llm, resume_sentinel=True),
            StepContract(name="write_architecture_doc", execute=_write_architecture_doc),
        ],
    )
