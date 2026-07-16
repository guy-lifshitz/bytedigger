"""Phase 0.6 — DevOps artifact-type detection via bun shim.

Slots between phase_05_inject and phase_1_discovery.  Reads the list of
changed files from ``ctx.org_config["changed_files"]``, delegates detection
to the ``devops-detect.ts`` Bun shim (Architecture D), and stores the result
in ``StepResult.data`` for downstream phases.

Never fails the build: every error path returns ``status="ok"`` with
``artifact_type=None`` and an ``error`` key explaining what went wrong.

Agreement: 27843297 Stage 1 (2026-05-11).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))
from bounded_spawn import bounded_run  # noqa: E402
from contracts import StepContract, StepResult, WorkflowDefinition

sys.path.insert(0, str(Path(__file__).parent.parent))
from config_provider import get_config  # noqa: E402


# ─── helpers ─────────────────────────────────────────────────────────────────


def _resolve_hal_dir(ctx) -> Path:
    """Return the HAL root.

    Delegates to get_config().hal_root() (§1g single-source).
    """
    return get_config().hal_root()


# ─── step ────────────────────────────────────────────────────────────────────


def _detect_artifact(ctx, _prev) -> StepResult:
    """Invoke devops-detect.ts for the changed_files list; return detection result.

    Returns status="ok" in all cases (failure modes surface via data["error"]).
    """
    cfg = ctx.org_config or {}
    files: list[str] = cfg.get("changed_files", [])

    # AC6: skip subprocess entirely when no files provided
    if not files:
        return StepResult(
            status="ok",
            data={"artifact_type": None, "detection_method": None, "detected_files": []},
            duration_ms=0,
            step_name="detect_artifact",
        )

    hal_dir = _resolve_hal_dir(ctx)
    shim_path = hal_dir / "SYSTEM" / "cli" / "build" / "devops-detect.ts"

    cmd = [
        "bun",
        str(shim_path),
        "--files",
        ",".join(files),
        "--output",
        "json",
    ]

    try:
        proc = bounded_run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except FileNotFoundError as exc:
        return StepResult(
            status="ok",
            data={"artifact_type": None, "error": f"FileNotFoundError: {exc}"},
            duration_ms=0,
            step_name="detect_artifact",
        )

    if proc.returncode == 124:
        return StepResult(
            status="ok",
            data={"artifact_type": None, "error": "timeout after 30s"},
            duration_ms=0,
            step_name="detect_artifact",
        )

    if proc.returncode != 0:
        return StepResult(
            status="ok",
            data={
                "artifact_type": None,
                "error": (proc.stderr or "").strip() or f"rc={proc.returncode}",
            },
            duration_ms=0,
            step_name="detect_artifact",
        )

    try:
        parsed = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        return StepResult(
            status="ok",
            data={"artifact_type": None, "error": f"JSONDecodeError: {exc}"},
            duration_ms=0,
            step_name="detect_artifact",
        )

    return StepResult(
        status="ok",
        data={
            "artifact_type": parsed.get("artifact_type"),
            "detection_method": parsed.get("detection_method"),
            "detected_files": parsed.get("detected_files", []),
        },
        duration_ms=0,
        step_name="detect_artifact",
    )


# ─── public factory ───────────────────────────────────────────────────────────


def phase_0_6_artifact_detect_workflow() -> WorkflowDefinition:
    return WorkflowDefinition(
        name="phase_0_6_artifact_detect",
        steps=[
            StepContract(name="detect_artifact", execute=_detect_artifact),
        ],
    )
