"""Phase 0 (research init) as a WorkflowDefinition.

First port of a HAL phase to engine workflows (Stage 2 step 1, 2026-04-25).
Captures the deterministic file-system bootstrap from `phase-0-classify.md`:
scratchpad scaffolding + a research stub doc that downstream phases overwrite.

LLM-driven judgements (complexity classification, task wording) stay outside —
the orchestrator passes them through `WorkflowContext` (`question` = task,
`org_config["scratchpad_dir"]` = absolute scratchpad path).

Two steps, both deterministic:
    1. init_scratchpad   — mkdir -p .hal-build/{research,architecture,specs,tests,reviews}
    2. seed_research_doc — write .hal-build/research/phase-0-init.md
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from contracts import StepContract, StepResult, WorkflowDefinition

SUBDIRS = ("research", "architecture", "specs", "tests", "reviews")
RESEARCH_STUB_FILENAME = "phase-0-init.md"


def _resolve_scratchpad(ctx) -> Path:
    """org_config.scratchpad_dir wins; else <cwd>/.hal-build."""
    cfg = ctx.org_config or {}
    raw = cfg.get("scratchpad_dir")
    if raw:
        return Path(raw).expanduser().resolve()
    return (Path.cwd() / ".hal-build").resolve()


def _init_scratchpad(ctx, _prev) -> StepResult:
    scratchpad = _resolve_scratchpad(ctx)
    created: list[str] = []
    for sub in SUBDIRS:
        d = scratchpad / sub
        d.mkdir(parents=True, exist_ok=True)
        created.append(str(d))
    return StepResult(
        status="ok",
        data={"scratchpad_dir": str(scratchpad), "subdirs": created},
        duration_ms=0,
        step_name="init_scratchpad",
    )


def _seed_research_doc(ctx, prev) -> StepResult:
    if not isinstance(prev, StepResult) or not isinstance(prev.data, dict):
        return StepResult(
            status="error",
            data=None,
            duration_ms=0,
            step_name="seed_research_doc",
            error="prev step did not produce scratchpad_dir",
            error_code="E_MISSING_PREV_DATA",
        )
    scratchpad = Path(prev.data["scratchpad_dir"])
    doc = scratchpad / "research" / RESEARCH_STUB_FILENAME
    body = (
        f"# Phase 0 — Research Stub\n\n"
        f"- task: {ctx.question or '(none)'}\n"
        f"- session_id: {ctx.session_id}\n"
        f"- generated_at: {datetime.now(timezone.utc).isoformat()}\n\n"
        f"_Auto-emitted by `engine_py/workflows/phase_0_research.py`. "
        f"Downstream phases overwrite or extend this doc._\n"
    )
    doc.write_text(body, encoding="utf-8")
    return StepResult(
        status="ok",
        data={"doc_path": str(doc), "bytes_written": len(body.encode("utf-8"))},
        duration_ms=0,
        step_name="seed_research_doc",
    )


def phase_0_research_workflow() -> WorkflowDefinition:
    return WorkflowDefinition(
        name="phase_0_research",
        steps=[
            StepContract(name="init_scratchpad", execute=_init_scratchpad),
            StepContract(name="seed_research_doc", execute=_seed_research_doc),
        ],
    )
