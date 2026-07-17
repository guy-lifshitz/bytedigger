"""Phase 1 (discovery) as a WorkflowDefinition.

Stage 2.2 port (2026-04-25). First LLM-heavy phase. Following the watch-out
in the memory index NEXT ACTION, the LLM call sits inside ONE opaque subprocess
step (`invoke_discovery_llm`) — engine still owns prompt assembly and the
post-LLM file write so events + replay still describe what happened.

Token-spend optimizations baked in:
    - Prompt does NOT inline the 4 injection files; it lists READ_FIRST
      paths to `$SCRATCHPAD/injection/{hal-memory,constitution,quality-gate,active-work}.md`
      so the LLM opens them on demand (matches phase-05-inject.md §8).
    - No CLAUDE.md / project-context injection — orchestrator wires
      `omitProjectContext` flags through `llm_command`.
    - Optional `role_template_path` — orchestrator can prepend a ~3KB
      role-reviewer template for read-only safety rules without paying
      ~10KB CLAUDE.md tax.

Inputs (via `ctx.org_config`):
    scratchpad_dir       — REQUIRED. Absolute path to scratchpad root.
    complexity           — "SIMPLE" | "FEATURE" | "COMPLEX". Drives output
                           filename and prompt scaffolding. Default "FEATURE".
    role_template_path   — Optional. If set + file exists, prepended to prompt.
    llm_command          — Optional. Argv list for subprocess; prompt is fed
                           via stdin, response is captured from stdout.
                           Default: get_claude_discovery(). Tests stub with a fake.
    llm_timeout_sec      — Optional. Default 300.

`ctx.question` carries the user's feature request text.

Steps (3):
    1. build_discovery_prompt — deterministic prompt assembly
    2. invoke_discovery_llm   — opaque subprocess, prompt→stdin, stdout→raw_response
    3. write_discovery_doc    — write LLM output to scratchpad doc path

Output doc:
    SIMPLE                → $SCRATCHPAD/specs/build-spec.md
    FEATURE | COMPLEX     → $SCRATCHPAD/research/discovery.md
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))

import telemetry_ctx  # noqa: E402
from contracts import StepContract, StepResult, WorkflowDefinition
from io_utils import atomic_write  # noqa: E402
from llm_subprocess import invoke_llm_subprocess
from model_config import get_claude_discovery
from skip_logic import (  # noqa: E402
    frozen_short_circuit_enabled,
    make_skip_result,
    passthrough_if_skipped,
    should_skip_phase,
)
try:
    from ._task_description import normalize_task_description  # noqa: E402
except ImportError:  # pragma: no cover — bare fallback for sys.path-rooted test imports (GH881)
    from _task_description import normalize_task_description  # type: ignore[no-redef]  # noqa: E402
try:
    from .graph_source import ensure_graph  # noqa: E402
except ImportError:  # pragma: no cover — bare fallback for sys.path-rooted test imports (GH881)
    from graph_source import ensure_graph  # type: ignore[no-redef]  # noqa: E402
from project_root import resolve_project_root  # noqa: E402
from config_provider import timeout_policy_path  # noqa: E402  GH285 C2
from lib.timeout_policy import DEFAULT_POLICY, cached_policy, resolve_timeout_sec  # noqa: E402  GH285 C2

logger = logging.getLogger(__name__)


def _timeout_policy() -> dict:
    return cached_policy(str(timeout_policy_path()))


def _default_model() -> str:
    return get_claude_discovery()


def _default_llm_command() -> list[str]:
    """Back-compat alias returning argv form."""
    from llm_subprocess import _build_claude_argv
    return _build_claude_argv(_default_model())


def _resolve_model(cfg: dict, override_key: str = "discovery_model", default: str | None = None) -> str:
    """Per-step override → global model → default."""
    return cfg.get(override_key) or cfg.get("model") or default or _default_model()


def _resolve_command(cfg: dict, override_key: str = "llm_command") -> list[str]:
    """Back-compat alias. Use _resolve_model for new callers."""
    from llm_subprocess import _build_claude_argv
    return _build_claude_argv(_resolve_model(cfg))


# Backward-compat module-level constant (computed once at import via ModelConfig).
DEFAULT_LLM_COMMAND = _default_llm_command()
DEFAULT_LLM_TIMEOUT_SEC = DEFAULT_POLICY["discovery.llm"]["base"]


def _resolve_llm_timeout_sec(cfg: dict | None) -> int:
    """discovery.llm timeout via unified policy (GH285 C2)."""
    return resolve_timeout_sec("discovery.llm", cfg, policy=_timeout_policy())

SIMPLE_DOC_RELPATH = "specs/build-spec.md"
FEATURE_DOC_RELPATH = "research/discovery.md"

VALID_COMPLEXITIES = ("SIMPLE", "FEATURE", "COMPLEX")


# ─── dispatch/coordination mechanisms enrichment (16FD9C1C) ───────────────────
# Post-mortem: build forge-1777578916 ($17.75/68min) produced wrong architecture
# because Phase 1/2 prompts never asked the LLM to enumerate existing dispatch
# infrastructure (UnifiedHookOrchestrator.HOOK_CONFIGS.SubagentStart was missed).
# Forcing this enumeration BEFORE Out of Scope shapes scope decisions correctly.
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


def _emit_safe(event_type: str, payload: dict) -> None:
    """Emit telemetry event via current run context; swallow all errors.
    Mirror of the helper in phase_45_spec / phase_2_explore / phase_5_implement."""
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
        raise ValueError("org_config.scratchpad_dir required for phase_1_discovery")
    return Path(raw).expanduser().resolve()


def _resolve_complexity(ctx) -> str:
    cfg = ctx.org_config or {}
    c = (cfg.get("complexity") or "FEATURE").upper()
    if c not in VALID_COMPLEXITIES:
        raise ValueError(
            f"complexity must be one of {VALID_COMPLEXITIES}, got {c!r}"
        )
    return c


def _doc_path_for(scratchpad: Path, complexity: str) -> Path:
    rel = SIMPLE_DOC_RELPATH if complexity == "SIMPLE" else FEATURE_DOC_RELPATH
    return scratchpad / rel


def _role_block(complexity: str) -> str:
    """Role line — complexity-conditional. SIMPLE writes the spec; FEATURE/COMPLEX discover."""
    if complexity == "SIMPLE":
        return (
            "ROLE: You are the build-spec writer. You WRITE the spec file (via the Write tool) "
            "— not a report about it. SIMPLE tier means: bug fix or small "
            "addition, 1-3 files, no new architecture. Stay strictly within the request."
        )
    return "ROLE: You are a discovery agent — understand what needs to be built."


def _output_schema_block(complexity: str, doc_path: str) -> str:
    if complexity == "SIMPLE":
        # Structure mirrors SpecKit-style specs (see specs/SmartRouterV2/,
        # specs/ContinuityFix/, SYSTEM/specs/PAI/speckit-integration.spec.md
        # for prior-art examples). Sections kept lightweight for SIMPLE tier
        # (bug fix / small addition); Out of Scope + Constraints are first-class
        # to make anti-fabrication structural, not a rule in a comment.
        return (
            f"OUTPUT — write the FULL spec body to EXACTLY this file path using the Write tool:\n"
            f"  {doc_path}\n"
            "That file IS the spec deliverable. Your text response MUST be ONLY a ≤200-token status:\n"
            "the path written + one-line confirmation. Do NOT echo the spec body in your text response.\n"
            "Report the written path in worker_written_paths.\n"
            "\n"
            "Start the spec DIRECTLY with `## Context`. No preamble. No status\n"
            "markers (`STATUS:`, `DONE`, `Spec written`). No meta-commentary about\n"
            "what you discovered or decided. The file IS the spec; do not describe it.\n"
            "\n"
            "REQUIRED sections, in this exact order, no others:\n"
            "\n"
            "  ## Context\n"
            "  <one sentence: what this change is and why it is needed>\n"
            "\n"
            "  ## Files\n"
            "  <paths to create or modify, one per line>\n"
            "\n"
            "  ## Behavior\n"
            "  <what it should do, edge cases, error handling — only what the\n"
            "   FEATURE REQUEST asks for>\n"
            "\n"
            "  ## Constraints\n"
            "  <what must NOT be changed in surrounding code; compatibility rules>\n"
            "\n"
            + _DISPATCH_MECHANISMS_BLOCK
            + "\n"
            "  ## Out of Scope\n"
            "  <features and behaviors explicitly NOT in this work — list the\n"
            "   plausible-but-unrequested additions that a reader might assume>\n"
            "\n"
            "  ## Acceptance Criteria\n"
            "  <numbered list of testable invariants. Each criterion MUST be\n"
            "   followed by a `Validation:` line stating how to verify it.>\n"
            "\n"
            "  ## Open Questions\n"
            "  <bullets, or `none`. Use this when the request is ambiguous —\n"
            "   do NOT invent an answer.>\n"
            "\n"
            "  ## Authorized Test Edits\n"
            "  <REQUIRED. If any Acceptance Criterion changes an EXISTING function/data\n"
            "   contract that a PRE-EXISTING test file asserts, list each such test file\n"
            "   below — GREEN is only allowed to edit listed files; edits to unlisted\n"
            "   pre-existing tests terminal-FAIL E_RED_TESTS_TAMPERED. Emit the marker\n"
            "   EXACTLY as shown:\n"
            "  authorized-test-edits:\n"
            "  - `<path/to/pre-existing/test.py>` — <which AC/contract change requires the edit>\n"
            "   If no pre-existing test asserts a changed contract, emit the marker\n"
            "   line followed by the single word `none` on the next line.>\n"
            "\n"
            "ANTI-FABRICATION — producer rules in injection/producer-rules.md\n"
            "(## Anti-Fabrication — Producer Rules) apply. Surface-specific for SIMPLE specs:\n"
            "  - Length budget: SIMPLE specs for 1-3 file tasks should be 30-80\n"
            "    lines of markdown. If you write more, you are inventing scope.\n"
            "  - Out of Scope is mandatory: list the plausible-but-unrequested\n"
            "    additions a reader might assume (`--dry-run`, `--verbose`, extra\n"
            "    flags, `for debugging`, `as enhancement`, `low-cost high-value`).\n"
            "  - Path grounding: before claiming any file path, import statement,\n"
            "    attribute name, or canonical convention in the spec, you MUST\n"
            "    verify it exists by reading the actual target file (use your\n"
            "    Read or Grep tool). Do not write `<path>` or 'X is already\n"
            "    imported' or 'Y is the canonical location' without grounding.\n"
            "    If you cannot verify a claim, list it under `## Open Questions`\n"
            "    instead of guessing.\n"
            "\n"
            "EXAMPLE — for the request `add a --quiet flag to scripts/notify.sh\n"
            "that suppresses the success line on exit 0`, a well-formed spec is:\n"
            "\n"
            "  ## Context\n"
            "  Add an opt-in `--quiet` flag to scripts/notify.sh so callers can\n"
            "  suppress success output in cron contexts.\n"
            "\n"
            "  ## Files\n"
            "  scripts/notify.sh\n"
            "  tests/test_notify.sh\n"
            "\n"
            "  ## Behavior\n"
            "  - When invoked without `--quiet`, behavior unchanged.\n"
            "  - When invoked with `--quiet` and exit code is 0, suppress the\n"
            "    `notification sent` line on stdout. Stderr unchanged.\n"
            "  - When `--quiet` is combined with non-zero exit, error output\n"
            "    is unchanged (only the success line is suppressed).\n"
            "\n"
            "  ## Constraints\n"
            "  - Existing positional arguments and flag order must keep working.\n"
            "  - Exit codes must not change.\n"
            "\n"
            "  ## Out of Scope\n"
            "  - A `--verbose` flag (not requested).\n"
            "  - Logging suppression in other scripts (notify.sh only).\n"
            "  - Config-file precedence for the flag (CLI-only).\n"
            "\n"
            "  ## Acceptance Criteria\n"
            "  1. Without `--quiet`, exit-0 prints the success line.\n"
            "     Validation: `bash scripts/notify.sh test | grep -q 'notification sent'`.\n"
            "  2. With `--quiet`, exit-0 prints nothing on stdout.\n"
            "     Validation: `bash scripts/notify.sh --quiet test` produces empty stdout.\n"
            "  3. With `--quiet` and a forced failure, stderr is unchanged.\n"
            "     Validation: `bash scripts/notify.sh --quiet --force-fail 2>&1 1>/dev/null`\n"
            "                 still prints the existing error line.\n"
            "\n"
            "  ## Open Questions\n"
            "  none\n"
            "\n"
            "  ## Authorized Test Edits\n"
            "  authorized-test-edits:\n"
            "  none\n"
            "\n"
            "Notice what the example does NOT include: no `--verbose` (out of\n"
            "scope), no `--log-level` (not asked), no config-file knob, no\n"
            "structured-JSON-output mode. These are exactly the kind of\n"
            "plausible-but-unrequested additions that cause fabrication.\n"
        )
    # FEATURE/COMPLEX discovery doc — input to Phase 2 (explore) and Phase 4
    # (architect). Not the spec yet (Phase 4.5 writes that). Job: scope the
    # problem space, separate signal from noise. Anti-fabrication: list
    # unrequested-but-plausible additions under ## Out of Scope; park
    # ambiguity under ## Open Questions instead of inventing answers.
    return (
        f"OUTPUT — write the FULL discovery doc to EXACTLY this file path using the Write tool:\n"
        f"  {doc_path}\n"
        "That file IS the discovery deliverable. Your text response MUST be ONLY a ≤200-token status:\n"
        "the path written + one-line confirmation. Do NOT echo the discovery body in your text response.\n"
        "Report the written path in worker_written_paths.\n"
        "\n"
        "Start the discovery doc DIRECTLY with `## Context`. No preamble. No status\n"
        "markers (`STATUS:`, `DONE`, `Discovery written`). No meta-commentary\n"
        "about what you decided. The file IS the discovery doc.\n"
        "\n"
        "REQUIRED sections, in this exact order, no others:\n"
        "\n"
        "  ## Context\n"
        "  <2-4 sentences: what exists today, what the request is, why it matters>\n"
        "\n"
        "  ## Requirements\n"
        "  <bounded summary — only what the FEATURE REQUEST states or strictly implies>\n"
        "\n"
        "  ## In Scope\n"
        "  <bullets — capabilities this work delivers>\n"
        "\n"
        + _DISPATCH_MECHANISMS_BLOCK
        + "\n"
        "  ## Out of Scope\n"
        "  <bullets — features and behaviors explicitly NOT included. List the\n"
        "   plausible-but-unrequested additions a reader might assume.>\n"
        "\n"
        "  ## Open Questions\n"
        "  <bullets, or `none`. Park unclear items here. Do NOT invent answers.>\n"
        "\n"
        "ANTI-FABRICATION — producer rules in injection/producer-rules.md\n"
        "(## Anti-Fabrication — Producer Rules) apply. No FEATURE/COMPLEX-discovery-specific\n"
        "additions — discovery's job is to scope, not invent.\n"
    )


def _build_prompt(ctx, scratchpad: Path, complexity: str) -> str:
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
    parts.append(_role_block(complexity))
    parts.append(f"COMPLEXITY: {complexity}")
    parts.append("")
    cfg = ctx.org_config or {}
    task_description = normalize_task_description(cfg)
    parts.append("FEATURE REQUEST:")
    if ctx.question:
        parts.append(ctx.question)
        if task_description is not None:
            parts.append("")
            parts.append("TASK CONTEXT:")
            parts.append(task_description)
    elif task_description is not None:
        parts.append(task_description)
    else:
        parts.append("(no feature request provided)")
    parts.append("")
    doc_path = _doc_path_for(scratchpad, complexity)
    parts.append(_output_schema_block(complexity, str(doc_path)))
    parts.append(
        "\nGRAPH-FIRST PROTOCOL (DA48BEAC):\n"
        "Before grepping or reading files, run:\n"
        "  graphify query \"<your discovery question>\" --budget 2000\n"
        "  graphify explain <node_id>   # for any relevant node\n"
        "  graphify affected <node_id>  # for blast-radius analysis\n"
        "Use grep/Read ONLY when graphify query returns no relevant node OR "
        "the staleness gate is dirty (graph.json missing or stale).\n"
        "\n### GRAPH-FIRST EVIDENCE\n"
        "Report here: graph-query run (yes/no), node count returned, "
        "OR the grep-fallback reason if graph was unavailable."
    )
    return "\n".join(parts)


def _check_frozen_spec_skip(ctx, _prev) -> StepResult:
    """Entry step: if decision_doc frozen + SIMPLE (or FEATURE/COMPLEX) → skip phase."""
    if not frozen_short_circuit_enabled():
        return StepResult(
            status="ok",
            data={"skipped": False},
            duration_ms=0,
            step_name="check_frozen_spec_skip",
        )
    cfg = ctx.org_config or {}
    skip, ddoc_path = should_skip_phase(cfg)
    if skip:
        return make_skip_result("check_frozen_spec_skip", ddoc_path or "")
    return StepResult(
        status="ok",
        data={"skipped": False},
        duration_ms=0,
        step_name="check_frozen_spec_skip",
    )


def _build_discovery_prompt(ctx, prev) -> StepResult:
    pt = passthrough_if_skipped(prev, "build_discovery_prompt")
    if pt is not None:
        return pt
    scratchpad = _resolve_scratchpad(ctx)
    complexity = _resolve_complexity(ctx)
    prompt = _build_prompt(ctx, scratchpad, complexity)
    doc_path = _doc_path_for(scratchpad, complexity)
    return StepResult(
        status="ok",
        data={
            "prompt": prompt,
            "doc_path": str(doc_path),
            "complexity": complexity,
            "prompt_bytes": len(prompt.encode("utf-8")),
        },
        duration_ms=0,
        step_name="build_discovery_prompt",
    )


def _invoke_discovery_llm(ctx, prev) -> StepResult:
    pt = passthrough_if_skipped(prev, "invoke_discovery_llm")
    if pt is not None:
        return pt
    if not isinstance(prev, StepResult) or not isinstance(prev.data, dict):
        return StepResult(
            status="error",
            data=None,
            duration_ms=0,
            step_name="invoke_discovery_llm",
            error="prev step did not produce a prompt",
            error_code="E_MISSING_PREV_DATA",
        )

    cfg = ctx.org_config or {}
    _project_root, _ = resolve_project_root(cfg)
    graph_src = ensure_graph(str(_project_root))
    _doc = prev.data.get("doc_path")
    if _doc:
        try:
            Path(_doc).unlink()
        except FileNotFoundError:
            pass
        except OSError:
            pass
    return invoke_llm_subprocess(
        prompt=prev.data["prompt"],
        model=_resolve_model(cfg, "discovery_model", _default_model()),
        timeout_sec=_resolve_llm_timeout_sec(cfg),
        step_name="invoke_discovery_llm",
        extra_data={
            "doc_path": prev.data["doc_path"],
            "complexity": prev.data["complexity"],
            "graph_source": graph_src,
        },
        allowed_tools=["Read", "Grep", "Glob", "Write", "Bash(graphify-shim.sh:*)"],
    )


def _resolve_discovery_source(doc_path, raw_response: str) -> tuple[str, str]:
    """Return (raw, source) selecting the discovery body from file or raw_response.

    If doc_path is a file with non-whitespace content, return (file_text, "worker_file").
    Otherwise return (raw_response, "raw_response_fallback") — fail-open fallback that
    preserves today's behavior when the subagent does not write the file.
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


def _write_discovery_doc(_ctx, prev) -> StepResult:
    pt = passthrough_if_skipped(prev, "write_discovery_doc")
    if pt is not None:
        return pt
    if not isinstance(prev, StepResult) or not isinstance(prev.data, dict):
        return StepResult(
            status="error",
            data=None,
            duration_ms=0,
            step_name="write_discovery_doc",
            error="prev step did not produce raw_response",
            error_code="E_MISSING_PREV_DATA",
        )

    raw, source = _resolve_discovery_source(prev.data.get("doc_path"), prev.data["raw_response"])
    _emit_safe("discovery_writer_return_source", {"source": source, "bytes": len(raw.encode())})
    doc_path = Path(prev.data["doc_path"])
    doc_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(doc_path, raw)

    return StepResult(
        status="ok",
        data={
            "doc_path": str(doc_path),
            "bytes_written": len(raw.encode("utf-8")),
            "complexity": prev.data["complexity"],
        },
        duration_ms=0,
        step_name="write_discovery_doc",
    )


def phase_1_discovery_workflow() -> WorkflowDefinition:
    return WorkflowDefinition(
        name="phase_1_discovery",
        steps=[
            StepContract(name="check_frozen_spec_skip", execute=_check_frozen_spec_skip),
            StepContract(name="build_discovery_prompt", execute=_build_discovery_prompt),
            StepContract(name="invoke_discovery_llm", execute=_invoke_discovery_llm),
            StepContract(name="write_discovery_doc", execute=_write_discovery_doc),
        ],
    )
