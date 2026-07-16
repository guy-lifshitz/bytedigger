"""Phase composite — DevOps pipeline that chains artifact detection and security scan.

Sequences ``phase_0_6_artifact_detect``'s ``_detect_artifact`` step with a
wrapper around ``phase_5_devops_scan``'s ``_scan_artifact``. The wrapper
pre-populates ``ctx.org_config["artifact_type"]`` from the previous step's
``StepResult.data`` so the downstream scan step sees the detected artifact
type without orchestrator-level glue.

Closes the production cascade gap surfaced by 27843297 Stage 6 e2e test.

Agreement: 93EBD42D (2026-05-13).
"""
from __future__ import annotations

from contracts import StepContract, StepResult, WorkflowDefinition

from phase_0_6_artifact_detect import _detect_artifact
from phase_5_devops_scan import _scan_artifact


def _scan_artifact_with_propagation(ctx, prev) -> StepResult:
    """Propagate ``prev.data["artifact_type"]`` into ``ctx.org_config`` (when
    not already set by operator), then delegate to the existing ``_scan_artifact``.

    Spec § Algorithm steps 1-6. Honors operator override (ctx wins over prev).
    Frozen-dataclass safe — only mutates the dict pointed to by ``org_config``,
    never reassigns the attribute.
    """
    # Step 1: degenerate prev → unchanged delegation
    if prev is None or prev.status != "ok" or not isinstance(prev.data, dict):
        return _scan_artifact(ctx, prev)

    # Step 2: prev has no artifact_type → unchanged delegation
    prev_artifact_type = prev.data.get("artifact_type")
    if not prev_artifact_type:
        return _scan_artifact(ctx, prev)

    # Step 3: ctx.org_config is None → unchanged delegation (cannot reassign
    # attribute on frozen dataclass; _scan_artifact handles None config via
    # its existing skip path).
    cfg = ctx.org_config
    if cfg is None:
        return _scan_artifact(ctx, prev)

    # Step 4: operator override — ctx already has truthy artifact_type → preserve.
    if cfg.get("artifact_type"):
        return _scan_artifact(ctx, prev)

    # Step 5: propagate (dict-internal mutation; frozen-safe).
    cfg["artifact_type"] = prev_artifact_type

    # Step 6: delegate to existing scan.
    return _scan_artifact(ctx, prev)


def phase_devops_pipeline_workflow() -> WorkflowDefinition:
    return WorkflowDefinition(
        name="phase_devops_pipeline",
        steps=[
            StepContract(name="detect_artifact", execute=_detect_artifact),
            StepContract(name="scan_artifact", execute=_scan_artifact_with_propagation),
        ],
    )
