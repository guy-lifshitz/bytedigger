"""Phase 3 (clarify ambiguities) as a WorkflowDefinition.

Stage 2.9 port (2026-04-25). FEATURE/COMPLEX-only — SIMPLE skips Phase 3.

Phase doc has two modes: SUPERVISED (asks the user questions) and AUTONOMOUS
(makes assumptions, documents them, proceeds). v0 ports AUTONOMOUS only —
SUPERVISED requires interactive user-IO outside the engine's sequential
execution model. The phase doc itself notes AUTONOMOUS is the default for
build pipelines, so this matches the lock pattern from the memory index
("AUTONOMOUS closes loop").

Token-spend guards (matches phase_1 / phase_45 / phase_2 playbook):
    - Prompt lists READ_FIRST pointer paths + path to research/exploration.md
      (Phase 2 output) — never inline contents.
    - Optional `role_template_path` for ~3KB role-reviewer.
    - Default timeout: 300s (read-only analysis on top of Phase 2 findings).

Inputs (via `ctx.org_config`):
    scratchpad_dir              — REQUIRED. Absolute path to scratchpad root.
    role_template_path          — Optional. Prepended to prompt.
    llm_command                 — Optional. Default: get_claude_fallback(). Global fallback.
    clarify_llm_command         — Optional. Per-step override of llm_command.
    clarify_llm_timeout_sec     — Optional. Default 300.

`ctx.question` carries the user's feature request text.

Steps (3):
    1. build_clarify_prompt   — deterministic; references exploration.md by path
    2. invoke_clarify_llm     — opaque subprocess
    3. write_clarify_doc      — write to specs/assumptions.md, parse STATUS marker

Output:
    $SCRATCHPAD/specs/assumptions.md

Status marker (last-marker-wins via rfind, same vocabulary as phase_2):
    STATUS: DONE                  → status="ok",  marker="DONE"
    STATUS: DONE_WITH_CONCERNS    → status="ok",  marker="DONE_WITH_CONCERNS"
    STATUS: NEEDS_CONTEXT         → status="error", error_code="E_CLARIFY_NEEDS_CONTEXT"
    STATUS: BLOCKED               → status="error", error_code="E_CLARIFY_BLOCKED"
    no marker                     → status="error", error_code="E_CLARIFY_NO_MARKER"
"""
from __future__ import annotations

import logging
import sys as _sys
from pathlib import Path

from bytedigger_engine.contracts import StepContract, StepResult, WorkflowDefinition
from bytedigger_engine.llm_subprocess import invoke_llm_subprocess

from bytedigger_engine.skip_logic import make_skip_result, passthrough_if_skipped, should_skip_phase  # noqa: E402
from bytedigger_engine.lib.model_config import get_claude_fallback  # noqa: E402
from bytedigger_engine.lib.plugins.anti_hallucination.helper import get_out_of_role_block as _get_out_of_role_block  # noqa: E402
from bytedigger_engine import telemetry_ctx  # noqa: E402
from bytedigger_engine.lib.verdict_parse import last_line_anchored_marker  # noqa: E402
from bytedigger_engine.config_provider import timeout_policy_path  # noqa: E402  GH285 C2
from bytedigger_engine.lib.timeout_policy import DEFAULT_POLICY, cached_policy, resolve_timeout_sec  # noqa: E402  GH285 C2

logger = logging.getLogger(__name__)


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
DEFAULT_CLARIFY_TIMEOUT_SEC = DEFAULT_POLICY["clarify.llm"]["base"]


def _resolve_clarify_timeout_sec(cfg: dict | None) -> int:
    """clarify.llm timeout via unified policy (GH285 C2)."""
    return resolve_timeout_sec("clarify.llm", cfg, policy=_timeout_policy())

CLARIFY_DOC_RELPATH = "specs/assumptions.md"
EXPLORATION_DOC_RELPATH = "research/exploration.md"

_STATUS_MARKERS = (
    "STATUS: DONE_WITH_CONCERNS",
    "STATUS: DONE",
    "STATUS: NEEDS_CONTEXT",
    "STATUS: BLOCKED",
)


def _resolve_scratchpad(ctx) -> Path:
    cfg = ctx.org_config or {}
    raw = cfg.get("scratchpad_dir")
    if not raw:
        raise ValueError("org_config.scratchpad_dir required for phase_3_clarify")
    return Path(raw).expanduser().resolve()


def _resolve_model(cfg: dict, override_key: str = "clarify_model", default: str | None = None) -> str:
    return cfg.get(override_key) or cfg.get("model") or default or _default_model()


def _resolve_command(cfg: dict, override_key: str) -> list[str]:
    """Back-compat alias. Use _resolve_model for new callers."""
    from bytedigger_engine.llm_subprocess import _build_claude_argv
    return _build_claude_argv(_resolve_model(cfg))


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
        "STATUS: BLOCKED with reason 'injection files missing'."
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


def _clarify_output_schema(doc_path: str) -> str:
    return (
        f"OUTPUT — write the FULL assumptions doc to EXACTLY this file path using the Write tool:\n"
        f"  {doc_path}\n"
        "That file IS the clarification deliverable. Your text response MUST be ONLY a short\n"
        "(≤200-token) status: the path written + one-line confirmation, then EXACTLY ONE trailing\n"
        "STATUS line (see below). Do NOT echo the assumptions body in your text response.\n"
        "\n"
        "Start the file DIRECTLY with `# Assumptions & Clarifications`. No preamble.\n"
        "No status markers inside the file other than section headings. The file IS the assumptions doc.\n"
        "  # Assumptions & Clarifications\n"
        "  ## Ambiguities Identified\n"
        "    Bullet list. Each: the gap, why it matters.\n"
        "  ## Resolved Assumptions\n"
        "    For each ambiguity, the assumption you make + rationale.\n"
        "    AUTONOMOUS mode: no user interaction — pick the most defensible\n"
        "    assumption and document why.\n"
        "  ## Edge Cases\n"
        "    Bullet list of edge cases the implementation must handle.\n"
        "  ## Out of Scope\n"
        "    What you explicitly deferred. Bullet list (or 'none').\n"
        "\n"
        "End your text response with EXACTLY ONE status line on its own line (in your text reply, NOT in the file):\n"
        "  STATUS: DONE                — all ambiguities resolved\n"
        "  STATUS: DONE_WITH_CONCERNS  — resolved but flag concerns inline\n"
        "  STATUS: NEEDS_CONTEXT       — missing input prevents resolution\n"
        "  STATUS: BLOCKED             — cannot proceed (e.g. exploration missing)\n"
        "Do NOT emit multiple STATUS lines. The last one wins (rfind).\n"
    )


def _check_decision_doc_skip(ctx, _prev) -> StepResult:
    """Entry step: if decision_doc present + FEATURE → skip entire phase."""
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


def _build_clarify_prompt(ctx, prev) -> StepResult:
    pt = passthrough_if_skipped(prev, "build_clarify_prompt")
    if pt is not None:
        return pt
    scratchpad = _resolve_scratchpad(ctx)
    explore_doc = scratchpad / EXPLORATION_DOC_RELPATH

    parts: list[str] = []
    role = _maybe_role_template(ctx)
    if role:
        parts.append(role.rstrip())
        parts.append("")
    parts.append(_read_first_block(scratchpad))
    parts.append("")
    parts.append(
        "ROLE: You are a clarification agent (AUTONOMOUS mode). Identify gaps "
        "and ambiguities in the feature request, then make explicit assumptions "
        "with rationale. No user interaction — your assumptions stand."
    )
    parts.append("")
    parts.append("FEATURE REQUEST:")
    parts.append(ctx.question or "(no feature request provided)")
    parts.append("")
    if explore_doc.is_file():
        parts.append(
            f"EXPLORATION FINDINGS (read this file — do NOT trust summaries): {explore_doc}"
        )
    else:
        parts.append(
            f"EXPLORATION FINDINGS: (none at {explore_doc} — proceed using only the request)"
        )
    parts.append("")
    doc_path = scratchpad / CLARIFY_DOC_RELPATH
    parts.append(_clarify_output_schema(str(doc_path)))

    prompt = "\n".join(parts) + "\n\n" + _get_out_of_role_block()
    return StepResult(
        status="ok",
        data={
            "prompt": prompt,
            "doc_path": str(doc_path),
            "explore_doc_present": explore_doc.is_file(),
            "prompt_bytes": len(prompt.encode("utf-8")),
        },
        duration_ms=0,
        step_name="build_clarify_prompt",
    )


def _invoke_clarify_llm(ctx, prev) -> StepResult:
    pt = passthrough_if_skipped(prev, "invoke_clarify_llm")
    if pt is not None:
        return pt
    if not isinstance(prev, StepResult) or not isinstance(prev.data, dict):
        return StepResult(
            status="error", data=None, duration_ms=0,
            step_name="invoke_clarify_llm",
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
        model=_resolve_model(cfg, "clarify_model", _default_model()),
        timeout_sec=_resolve_clarify_timeout_sec(cfg),
        step_name="invoke_clarify_llm",
        extra_data={"doc_path": prev.data["doc_path"]},
        allowed_tools=["Read", "Glob", "Write"],
    )


def _parse_status_marker(raw: str) -> str | None:
    """Last line-anchored marker wins across known STATUS markers.

    Returns the marker payload (e.g. "DONE", "BLOCKED") or None if none found.
    Delegates to lib/verdict_parse.py P2 (EEFD480F chokepoint).
    """
    return last_line_anchored_marker(
        raw, [(m, m.split(": ", 1)[1]) for m in _STATUS_MARKERS], None
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


def _resolve_clarify_source(doc_path, raw_response: str) -> tuple[str, str]:
    """Return (body, source) selecting the clarify doc body from file or raw_response.

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


def _write_clarify_doc(_ctx, prev) -> StepResult:
    pt = passthrough_if_skipped(prev, "write_clarify_doc")
    if pt is not None:
        return pt
    if not isinstance(prev, StepResult) or not isinstance(prev.data, dict):
        return StepResult(
            status="error", data=None, duration_ms=0,
            step_name="write_clarify_doc",
            error="prev step did not produce raw_response",
            error_code="E_MISSING_PREV_DATA",
        )
    raw_response = prev.data["raw_response"]
    doc_path = Path(prev.data["doc_path"])
    body, source = _resolve_clarify_source(str(doc_path), raw_response)
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    doc_path.write_text(body, encoding="utf-8")
    _emit_safe("clarify_writer_return_source", {"source": source, "bytes": len(body.encode())})

    marker = _parse_status_marker(raw_response)
    _emit_safe("phase_status_marker", {
        "phase": 3,
        "marker": marker if marker is not None else "NO_MARKER",
        "doc_path": str(doc_path),
    })
    common = {
        "doc_path": str(doc_path),
        "bytes_written": len(body.encode("utf-8")),
        "marker": marker,
    }
    if marker is None:
        return StepResult(
            status="error",
            data=common,
            duration_ms=0,
            step_name="write_clarify_doc",
            error="clarify output contains no STATUS marker",
            error_code="E_CLARIFY_NO_MARKER",
        )
    if marker == "BLOCKED":
        return StepResult(
            status="error",
            data=common,
            duration_ms=0,
            step_name="write_clarify_doc",
            error="clarify reported STATUS: BLOCKED",
            error_code="E_CLARIFY_BLOCKED",
        )
    if marker == "NEEDS_CONTEXT":
        return StepResult(
            status="error",
            data=common,
            duration_ms=0,
            step_name="write_clarify_doc",
            error="clarify reported STATUS: NEEDS_CONTEXT",
            error_code="E_CLARIFY_NEEDS_CONTEXT",
        )
    return StepResult(
        status="ok",
        data=common,
        duration_ms=0,
        step_name="write_clarify_doc",
    )


def phase_3_clarify_workflow() -> WorkflowDefinition:
    return WorkflowDefinition(
        name="phase_3_clarify",
        steps=[
            StepContract(name="check_decision_doc_skip", execute=_check_decision_doc_skip),
            StepContract(name="build_clarify_prompt", execute=_build_clarify_prompt),
            StepContract(name="invoke_clarify_llm", execute=_invoke_clarify_llm),
            StepContract(name="write_clarify_doc", execute=_write_clarify_doc),
        ],
    )
