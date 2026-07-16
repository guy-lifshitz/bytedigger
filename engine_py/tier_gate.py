"""tier_gate.py — deterministic tier-misclassification lint for the MICRO lane.

Part of 1373F257 — closes "engine_py prod-path fix mis-tiered as MICRO skips
the mandatory Opus gate" (SYSTEMATIC).

Exposes:
    is_engine_py_prod(path)           -> bool
    resolves_to_existing(path)        -> bool
    scan_tier_violation(paths, tier)  -> list[TierFinding]
    lint_paths(paths, tier)           -> TierLintResult
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ENGINE_PY_MARKER = "/SYSTEM/cli/build/engine_py/"
MICRO_TIER = "MICRO"

# tier_gate.py lives at <repo>/SYSTEM/cli/build/engine_py/tier_gate.py
_REPO_ROOT = Path(__file__).resolve().parents[4]


@dataclass
class TierFinding:
    path: str
    reason: str = "engine_py_prod_micro"


@dataclass
class TierLintResult:
    tier: str
    findings: list[TierFinding]
    error: str | None = None

    def has_violation(self) -> bool:
        return self.error is None and bool(self.findings)


def is_engine_py_prod(path: str) -> bool:
    """Return True iff `path` resolves to an engine_py production .py file.

    Rules:
    - abspath + forward-slash normalize
    - must contain ENGINE_PY_MARKER as a substring
    - must end with ".py"
    - must NOT have "tests" as any path segment after the marker
    - must NOT have a basename starting with "test_"
    - must NOT have basename "conftest.py"

    Does NOT check file existence.
    """
    norm = os.path.abspath(path).replace(os.sep, "/")

    if ENGINE_PY_MARKER not in norm:
        return False

    if not norm.endswith(".py"):
        return False

    after = norm.split(ENGINE_PY_MARKER, 1)[1]
    parts = after.split("/")

    if "tests" in parts:
        return False

    base = parts[-1]
    if base.startswith("test_"):
        return False
    if base == "conftest.py":
        return False

    return True


def resolves_to_existing(path: str, *, exists=os.path.exists) -> bool:
    """True iff `path` denotes an existing file via EITHER anchor:
    cwd (legacy behavior) OR the repo root derived from this module's
    location (cwd-independent). Absolute paths use exists() directly.
    """
    if os.path.isabs(path):
        return exists(path)
    return exists(path) or exists(os.path.join(str(_REPO_ROOT), path))


def scan_tier_violation(
    paths: list[str],
    tier: str,
    *,
    exists=os.path.exists,
) -> list[TierFinding]:
    """Return one TierFinding per path that is an existing engine_py prod file.

    Only MICRO tier can be a mis-tier; any other tier returns [] immediately.
    The `exists` parameter is injectable for unit tests.
    """
    if tier != MICRO_TIER:
        return []

    findings = []
    for p in paths:
        if is_engine_py_prod(p) and resolves_to_existing(p, exists=exists):
            findings.append(TierFinding(path=p))
    return findings


def lint_paths(paths: list[str], tier: str) -> TierLintResult:
    """CLI wrapper: call scan_tier_violation and wrap in TierLintResult.

    OSError is caught and surfaced via TierLintResult.error.
    """
    try:
        findings = scan_tier_violation(paths, tier)
    except OSError as e:
        return TierLintResult(tier=tier, findings=[], error=str(e))
    return TierLintResult(tier=tier, findings=findings)
