"""Phase 2 (codebase exploration) as a WorkflowDefinition.

Stage 2.9 port (2026-04-25). FEATURE/COMPLEX-only — SIMPLE skips Phase 2.

Phase doc spawns 2-3 parallel hal-explorer agents (patterns / deps / security)
and their findings persist in `research/findings-{name}.md` files. v0 collapses
that fanout into ONE opaque-LLM-step that enumerates the perspectives in the
prompt and writes a single consolidated `research/exploration.md`. Fanout as
a workflow primitive is killed-by-construction (project_workflow_engine_fanout_deferred.md);
single-pass with prompt-level perspective enumeration is the locked replacement.

Token-spend guards (matches phase_1 / phase_45 playbook):
    - Prompt lists READ_FIRST pointer paths only — never inline injection files.
    - Optional `role_template_path` for ~3KB role-reviewer instead of ~10KB CLAUDE.md
      (matches phase doc: explore agents use omitProjectContext + role-reviewer).
    - Default timeout: 600s (large repo grep + read).

Inputs (via `ctx.org_config`):
    scratchpad_dir              — REQUIRED. Absolute path to scratchpad root.
    complexity                  — Optional. "FEATURE" | "COMPLEX". Default "FEATURE".
                                  COMPLEX adds the security perspective; FEATURE skips it.
    role_template_path          — Optional. Prepended to prompt.
    llm_command                 — Optional. Default: get_claude_explore(). Global fallback.
    explore_llm_command         — Optional. Per-step override of llm_command.
                                  Pin Sonnet for COMPLEX, Haiku for FEATURE per phase doc.
    explore_llm_timeout_sec     — Optional. Default 600.

`ctx.question` carries the user's feature request text.

Steps (3):
    1. build_explore_prompt   — deterministic; lists perspectives the LLM must cover
    2. invoke_explore_llm     — opaque subprocess
    3. write_explore_doc      — write to research/exploration.md, parse STATUS marker

Output:
    $SCRATCHPAD/research/exploration.md

Status marker (last-marker-wins via rfind):
    STATUS: DONE                  → status="ok",  marker="DONE"
    STATUS: DONE_WITH_CONCERNS    → status="ok",  marker="DONE_WITH_CONCERNS"
    STATUS: NEEDS_CONTEXT         → status="error", error_code="E_EXPLORE_NEEDS_CONTEXT"
    STATUS: BLOCKED               → status="error", error_code="E_EXPLORE_BLOCKED"
    no marker                     → status="error", error_code="E_EXPLORE_NO_MARKER"

The doc is always written before the marker-driven status is set, so the partial
output is preserved on disk for human inspection even when blocked.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

_ENGINE_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_ENGINE_ROOT))
sys.path.insert(0, str(_ENGINE_ROOT / "lib"))
sys.path.insert(0, str(_ENGINE_ROOT / "lib" / "plugins"))

from contracts import StepContract, StepResult, WorkflowDefinition  # noqa: E402
from llm_subprocess import invoke_llm_subprocess  # noqa: E402
from skip_logic import make_skip_result, passthrough_if_skipped, should_skip_phase  # noqa: E402
from anti_hallucination.helper import (  # noqa: E402
    get_producer_prompt_fragment as _get_producer_anti_fab_prompt,
    get_out_of_role_block as _get_out_of_role_block,
)
from model_config import get_claude_explore  # noqa: E402
import telemetry_ctx  # noqa: E402
try:
    from .graph_source import ensure_graph  # noqa: E402
except ImportError:  # pragma: no cover — bare fallback for sys.path-rooted test imports (GH881)
    from graph_source import ensure_graph  # type: ignore[no-redef]  # noqa: E402
from project_root import resolve_project_root  # noqa: E402
from verdict_parse import last_line_anchored_marker  # noqa: E402
from config_provider import timeout_policy_path  # noqa: E402  GH285 C2
from lib.timeout_policy import DEFAULT_POLICY, cached_policy, resolve_timeout_sec  # noqa: E402  GH285 C2

logger = logging.getLogger(__name__)


def _timeout_policy() -> dict:
    return cached_policy(str(timeout_policy_path()))


def _default_model() -> str:
    # 955657B2: function-form so reset_cache() in tests is honored at runtime.
    return get_claude_explore()


def _default_llm_command() -> list[str]:
    """Back-compat alias returning argv form."""
    from llm_subprocess import _build_claude_argv
    return _build_claude_argv(_default_model())


DEFAULT_LLM_COMMAND = _default_llm_command()
DEFAULT_EXPLORE_TIMEOUT_SEC = DEFAULT_POLICY["explore.llm"]["base"]


def _resolve_explore_timeout_sec(cfg: dict | None) -> int:
    """explore.llm timeout via unified policy (GH285 C2)."""
    return resolve_timeout_sec("explore.llm", cfg, policy=_timeout_policy())

EXPLORE_DOC_RELPATH = "research/exploration.md"

VALID_COMPLEXITIES = ("FEATURE", "COMPLEX")

# ─── dispatch/coordination mechanisms enrichment (16FD9C1C) ───────────────────
# Post-mortem: build forge-1777578916 ($17.75/68min) produced wrong architecture
# because Phase 1/2 prompts never asked the LLM to enumerate existing dispatch
# infrastructure (UnifiedHookOrchestrator.HOOK_CONFIGS.SubagentStart was missed).
# Forcing this enumeration BEFORE Out of Scope shapes integration decisions.
_DISPATCH_MECHANISMS_BLOCK = (
    "  ## Existing Dispatch/Coordination Mechanisms\n"
    "  List existing dispatch/coordination mechanisms in the target domain so\n"
    "  integration points are not invented from scratch. For each item, name the\n"
    "  symbol with file:line citations — do NOT speculate. Cover three categories:\n"
    "  - Registries: lookup tables / config maps / dispatch dicts (e.g.\n"
    "    `HOOK_CONFIGS`, plugin registries, command tables) that route by key.\n"
    "  - Orchestrators: classes/modules that coordinate multi-step or multi-agent\n"
    "    flows (e.g. `UnifiedHookOrchestrator`, workflow engines, schedulers).\n"
    "  - Native event types: framework-provided events the runtime already emits\n"
    "    (e.g. `SubagentStart`, `PreToolUse`, lifecycle hooks) before considering\n"
    "    new event types or custom dispatch.\n"
    "  If none exist in the target domain, state `none found` with the search you\n"
    "  performed (paths grepped, symbols looked up). Missing this section means\n"
    "  re-inventing infrastructure that already ships in the codebase.\n"
)

# Order matters for last-marker-wins via rfind: scan all, pick the rightmost.
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
        raise ValueError("org_config.scratchpad_dir required for phase_2_explore")
    return Path(raw).expanduser().resolve()


def _resolve_complexity(ctx) -> str:
    cfg = ctx.org_config or {}
    c = (cfg.get("complexity") or "FEATURE").upper()
    if c not in VALID_COMPLEXITIES:
        raise ValueError(
            f"complexity must be one of {VALID_COMPLEXITIES}, got {c!r}"
        )
    return c


def _resolve_model(cfg: dict, override_key: str = "explore_model", default: str | None = None) -> str:
    return cfg.get(override_key) or cfg.get("model") or default or _default_model()


def _resolve_command(cfg: dict, override_key: str) -> list[str]:
    """Back-compat alias. Use _resolve_model for new callers."""
    from llm_subprocess import _build_claude_argv
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


def _perspectives_block(complexity: str) -> str:
    base = (
        "PERSPECTIVES (cover all in your single report — enumerate findings under each):\n"
        "  ### Patterns\n"
        "    Similar features, data flow, naming conventions, structural patterns.\n"
        "    Each finding: `path/to/file.py:LINE` + one-line summary.\n"
        "  ### Dependencies & Integration\n"
        "    Dependencies, testing patterns, extension points.\n"
        "    Each finding: `path/to/file.py:LINE` + one-line summary.\n"
    )
    if complexity == "COMPLEX":
        base += (
            "  ### Security & Error Handling\n"
            "    Security considerations, error handling patterns, failure modes.\n"
            "    Each finding: `path/to/file.py:LINE` + one-line summary.\n"
        )
    return base


def _explore_output_schema(complexity: str, doc_path: str) -> str:
    return (
        f"OUTPUT — write the FULL exploration doc to EXACTLY this file path using the Write tool:\n"
        f"  {doc_path}\n"
        "That file IS the exploration deliverable. Your text response MUST be ONLY a short\n"
        "(≤200-token) status: the path written + one-line confirmation, then EXACTLY ONE trailing\n"
        "STATUS line (see below). Do NOT echo the exploration body in your text response.\n"
        "\n"
        "Start the exploration doc DIRECTLY with `# Codebase Exploration`. No preamble.\n"
        "No status markers inside the file other than section headings. The file IS the exploration doc.\n"
        "\n"
        "REQUIRED sections, in this exact order, no others (the Perspectives\n"
        "block below adds an optional security subsection only for COMPLEX):\n"
        "\n"
        "  # Codebase Exploration\n"
        "\n"
        "  ## Relevant Files\n"
        "  <bullet list of `path/to/file.py:LINE` with one-line purpose. Each\n"
        "   path MUST be a real file you opened. Line numbers MUST point at the\n"
        "   construct named in the summary. If you did not open the file, do not\n"
        "   list it.>\n"
        "\n"
        + _perspectives_block(complexity)
        + "\n"
        "  ## Integration Points\n"
        "  <where the new feature plugs in. File paths + functions, all real.>\n"
        "\n"
        + _DISPATCH_MECHANISMS_BLOCK
        + "\n"
        "  ## Summary For Architects\n"
        "  <3-7 bullets distilling findings for Phase 4. Each bullet must\n"
        "   reference a finding from a section above — no new claims here.>\n"
        "\n"
        "  ## Out of Scope\n"
        "  <bullets — areas you considered but did NOT explore in depth, with\n"
        "   one-line reason. List the plausible-but-tangential threads a reader\n"
        "   might assume you covered.>\n"
        "\n"
        "  ## Open Questions\n"
        "  <bullets, or `none`. Use this when evidence is missing — do NOT\n"
        "   invent a finding to fill a perspective. Park the gap here.>\n"
        "\n"
        + _get_producer_anti_fab_prompt()
        + "\n"
        "Surface-specific for EXPLORE:\n"
        "  - `patterns` and `integration points` require concrete file citations.\n"
        "    Unbacked speculation goes to ## Open Questions, not ## Summary.\n"
        "  - Drive-by refactor opportunities and tangential cleanups → ## Out of\n"
        "    Scope, not ## Summary. Exploration's job is to map, not to expand.\n"
        "\n"
        "End your text response with EXACTLY ONE status line on its own line"
        " (in your text reply, NOT in the file):\n"
        "  STATUS: DONE                — all perspectives covered, no concerns\n"
        "  STATUS: DONE_WITH_CONCERNS  — coverage complete but flag concerns inline\n"
        "  STATUS: NEEDS_CONTEXT       — missing input prevents exploration\n"
        "  STATUS: BLOCKED             — cannot proceed (e.g. injection files missing)\n"
        "Do NOT emit multiple STATUS lines. The last one wins (rfind).\n"
    )


def _check_decision_doc_skip(ctx, _prev) -> StepResult:
    """Entry step: if decision_doc present + FEATURE → skip entire phase."""
    cfg = ctx.org_config or {}
    skip, ddoc_path = should_skip_phase(cfg)
    if skip:
        return make_skip_result("check_decision_doc_skip", ddoc_path or "")
    # Not skipping — return a sentinel so subsequent steps proceed normally.
    return StepResult(
        status="ok",
        data={"skipped": False},
        duration_ms=0,
        step_name="check_decision_doc_skip",
    )


def _build_explore_prompt(ctx, prev) -> StepResult:
    pt = passthrough_if_skipped(prev, "build_explore_prompt")
    if pt is not None:
        return pt
    scratchpad = _resolve_scratchpad(ctx)
    complexity = _resolve_complexity(ctx)
    doc_path = scratchpad / EXPLORE_DOC_RELPATH

    parts: list[str] = []
    role = _maybe_role_template(ctx)
    if role:
        parts.append(role.rstrip())
        parts.append("")
    parts.append(_read_first_block(scratchpad))
    parts.append("")
    parts.append(
        "ROLE: You are a codebase exploration agent. You WRITE the exploration "
        "file (via the Write tool) — not a report about it. Read existing code "
        "to find patterns, dependencies, and integration points the architect "
        "needs. Open files yourself — do NOT trust summaries, do NOT cite "
        "paths or line numbers you have not verified."
    )
    parts.append(f"COMPLEXITY: {complexity}")
    parts.append("")
    parts.append("FEATURE REQUEST:")
    parts.append(ctx.question or "(no feature request provided)")
    parts.append("")
    parts.append(_explore_output_schema(complexity, str(doc_path)))
    parts.append(
        "\nGRAPH-FIRST PROTOCOL (DA48BEAC):\n"
        "Before grepping or reading files, run:\n"
        "  graphify query \"<your exploration question>\" --budget 2000\n"
        "  graphify affected <node_id>  # for blast-radius / path analysis\n"
        "  graphify path <from> <to>    # for dependency path tracing\n"
        "Use grep/Read ONLY when graphify query returns no relevant node OR "
        "the staleness gate is dirty (graph.json missing or stale).\n"
        "\n### GRAPH-FIRST EVIDENCE\n"
        "Report here: graph-query run (yes/no), node count returned, "
        "OR the grep-fallback reason if graph was unavailable."
    )

    prompt = "\n".join(parts) + "\n\n" + _get_out_of_role_block()
    return StepResult(
        status="ok",
        data={
            "prompt": prompt,
            "doc_path": str(doc_path),
            "complexity": complexity,
            "prompt_bytes": len(prompt.encode("utf-8")),
        },
        duration_ms=0,
        step_name="build_explore_prompt",
    )


def _invoke_explore_llm(ctx, prev) -> StepResult:
    pt = passthrough_if_skipped(prev, "invoke_explore_llm")
    if pt is not None:
        return pt
    if not isinstance(prev, StepResult) or not isinstance(prev.data, dict):
        return StepResult(
            status="error", data=None, duration_ms=0,
            step_name="invoke_explore_llm",
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
    _project_root, _ = resolve_project_root(cfg)
    graph_src = ensure_graph(str(_project_root))
    return invoke_llm_subprocess(
        prompt=prev.data["prompt"],
        model=_resolve_model(cfg, "explore_model", _default_model()),
        timeout_sec=_resolve_explore_timeout_sec(cfg),
        step_name="invoke_explore_llm",
        extra_data={
            "doc_path": prev.data["doc_path"],
            "complexity": prev.data["complexity"],
            "graph_source": graph_src,
        },
        allowed_tools=["Read", "Grep", "Glob", "WebSearch", "WebFetch", "Write", "Bash(graphify-shim.sh:*)"],
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


def _resolve_explore_source(doc_path, raw_response: str) -> tuple[str, str]:
    """Return (body, source) selecting the explore doc body from file or raw_response.

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


def _write_explore_doc(_ctx, prev) -> StepResult:
    pt = passthrough_if_skipped(prev, "write_explore_doc")
    if pt is not None:
        return pt
    if not isinstance(prev, StepResult) or not isinstance(prev.data, dict):
        return StepResult(
            status="error", data=None, duration_ms=0,
            step_name="write_explore_doc",
            error="prev step did not produce raw_response",
            error_code="E_MISSING_PREV_DATA",
        )
    raw_response = prev.data["raw_response"]
    doc_path = Path(prev.data["doc_path"])
    body, source = _resolve_explore_source(str(doc_path), raw_response)
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    doc_path.write_text(body, encoding="utf-8")
    _emit_safe("explore_writer_return_source", {"source": source, "bytes": len(body.encode())})

    marker = _parse_status_marker(raw_response)
    _emit_safe("phase_status_marker", {
        "phase": 2,
        "marker": marker if marker is not None else "NO_MARKER",
        "doc_path": str(doc_path),
    })
    common = {
        "doc_path": str(doc_path),
        "bytes_written": len(body.encode("utf-8")),
        "complexity": prev.data["complexity"],
        "marker": marker,
    }
    if marker is None:
        return StepResult(
            status="error",
            data=common,
            duration_ms=0,
            step_name="write_explore_doc",
            error="explore output contains no STATUS marker",
            error_code="E_EXPLORE_NO_MARKER",
        )
    if marker == "BLOCKED":
        return StepResult(
            status="error",
            data=common,
            duration_ms=0,
            step_name="write_explore_doc",
            error="explore reported STATUS: BLOCKED",
            error_code="E_EXPLORE_BLOCKED",
        )
    if marker == "NEEDS_CONTEXT":
        return StepResult(
            status="error",
            data=common,
            duration_ms=0,
            step_name="write_explore_doc",
            error="explore reported STATUS: NEEDS_CONTEXT",
            error_code="E_EXPLORE_NEEDS_CONTEXT",
        )
    # DONE or DONE_WITH_CONCERNS
    return StepResult(
        status="ok",
        data=common,
        duration_ms=0,
        step_name="write_explore_doc",
    )


def phase_2_explore_workflow() -> WorkflowDefinition:
    return WorkflowDefinition(
        name="phase_2_explore",
        steps=[
            StepContract(name="check_decision_doc_skip", execute=_check_decision_doc_skip),
            StepContract(name="build_explore_prompt", execute=_build_explore_prompt),
            StepContract(name="invoke_explore_llm", execute=_invoke_explore_llm),
            StepContract(name="write_explore_doc", execute=_write_explore_doc),
        ],
    )
