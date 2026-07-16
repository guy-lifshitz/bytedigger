"""Shared helper: get_standards_context(ctx) -> str.

Calls the devops-prompt-context.ts bun shim to retrieve the standards markdown
block for the artifact type recorded in ctx.org_config["artifact_type"].

Returns "" (empty string) in ALL error/skip cases:
- artifact_type key absent, None, or empty string — NO subprocess invoked.
- FileNotFoundError, TimeoutExpired, non-zero rc, JSONDecodeError — all silent.

Never raises. Pure read of ctx + subprocess invocation + return string.

Agreement: 27843297 Stage 3 (2026-05-12).
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "lib"))
from bounded_spawn import bounded_run  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent.parent))
from config_provider import get_config  # noqa: E402


# ─── helpers ─────────────────────────────────────────────────────────────────


def _resolve_hal_dir(ctx) -> Path:
    """Return the HAL root.

    Delegates to get_config().hal_root() (§1g single-source).
    """
    return get_config().hal_root()


# ─── public function ─────────────────────────────────────────────────────────


def get_standards_context(ctx) -> str:
    """Return the standards-context markdown block for ctx's artifact type.

    Resolves hal_dir, builds bun subprocess cmd, parses JSON stdout.
    Returns "" on every error or skip path.
    When non-empty, ensures result ends with exactly "\\n\\n" for clean prepend.
    """
    cfg = ctx.org_config or {}

    # AC1/AC2/AC3: skip if artifact_type absent, None, or empty string
    artifact_type = cfg.get("artifact_type")
    if not artifact_type:
        return ""

    hal_dir = _resolve_hal_dir(ctx)
    shim_path = hal_dir / "SYSTEM" / "cli" / "build" / "devops-prompt-context.ts"

    cmd = [
        "bun",
        str(shim_path),
        "--artifact-type",
        artifact_type,
        "--output",
        "json",
    ]

    # AC6: FileNotFoundError → return ""
    # AC7: TimeoutExpired → return ""
    try:
        proc = bounded_run(
            cmd,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except FileNotFoundError:
        return ""

    if proc.returncode == 124:
        return ""

    # AC8: non-zero rc → return ""
    if proc.returncode != 0:
        return ""

    # AC9: malformed JSON → return ""
    try:
        parsed = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return ""

    # AC5: extract standards_context field
    result = parsed.get("standards_context") or ""
    if not result:
        return ""

    # Normalize: ensure trailing "\n\n" for clean prepend separation
    if not result.endswith("\n\n"):
        result = result.rstrip("\n") + "\n\n"

    return result
