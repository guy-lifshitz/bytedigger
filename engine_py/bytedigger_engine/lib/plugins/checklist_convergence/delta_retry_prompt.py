"""delta_retry_prompt.py — build the cycle-2 delta-retry writer prompt.

GH443 parts 1+2 (2026-07-09): drops the full cycle-1 scaffold (~10-15KB)
on REVISE retries with structured findings, keeping only an invariant
header plus the existing restricted-writer body (single source of truth
for constraints/findings/spec rendering, reused verbatim).

Public API:
    build_delta_retry_prompt(spec_path, spec, findings,
                              verbatim_reviewer_context=None) -> str

No host paths, env reads, or host-tree string refs in this file
(core-boundary clean).
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence

from .restricted_writer_prompt import build_writer_prompt

_INVARIANT_HEADER = (
    "Write the FULL revised spec markdown to {spec_path} (full body, not a"
    " diff; start with `## Context`; SpecKit-style sections only).\n"
    "\n"
    "Citations must be grounded: cite `<path>:<line>` only for lines you"
    " have actually read this session; never fabricate.\n"
    "\n"
    "Do NOT widen scope; do NOT add features, ACs, or rationale not"
    " required by a finding.\n"
)


def build_delta_retry_prompt(
    spec_path: str,
    spec: str,
    findings: Sequence[Mapping[str, str]],
    verbatim_reviewer_context: str | None = None,
) -> str:
    """Return the compact delta-retry prompt for a cycle >=2 REVISE retry."""
    parts: list[str] = [
        _INVARIANT_HEADER.format(spec_path=spec_path),
        "",
        build_writer_prompt(spec, findings, verbatim_reviewer_context=verbatim_reviewer_context),
        "",
        "ADDRESS EACH FINDING (by id):",
    ]
    for f in findings:
        fid = f.get("id", "?")
        req = f.get("required_action", "")
        parts.append(f"- FINDING_{fid}: {req}")
    return "\n".join(parts)
