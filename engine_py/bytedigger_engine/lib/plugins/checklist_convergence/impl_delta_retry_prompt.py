"""impl_delta_retry_prompt.py — build the cycle-2 delta-retry RED-worker prompt.

GH496 (2026-07-10): extends the GH443 delta-retry design (sibling
`delta_retry_prompt.py`, phase_45_spec) to phase_5_implement's RED writer.
On a REVISE retry (cycle >= 2, findings present), drops the full cycle-1
scaffold (READ_FIRST, FEATURE REQUEST, full RULES, anti-fab block, security
fragment, surface-specific block, full RED-LINT block, behavioral rubric)
and keeps only the invariants a revision needs: role + no-nested-subagents
instruction, spec pointer, the collectability rule, a grounding one-liner,
the REVISION section with findings, and the output marker.

Public API:
    build_impl_delta_retry_prompt(spec_path, cycle, findings,
                                   collectability_rule,
                                   output_marker_block) -> str

No host paths, no environment-variable reads, no shared-state refs in this
file (core-boundary clean).
"""
from __future__ import annotations


def build_impl_delta_retry_prompt(
    spec_path: str,
    cycle: int,
    findings: str,
    collectability_rule: str,
    output_marker_block: str,
) -> str:
    """Return the compact delta-retry prompt for a cycle >=2 REVISE retry."""
    parts: list[str] = [
        (
            f"ROLE: You are the RED worker on revision cycle {cycle}. The "
            "cycle-1 test files ALREADY EXIST in the working tree. Your job "
            "is to REVISE them to address the validator findings below — "
            "not to re-derive coverage from scratch.\n"
            "  - RED_DISALLOW_SUBAGENTS: do not spawn nested Agent subagents "
            "(no Task tool calls). Use Read/Write/Edit/Bash/Grep directly. "
            "Nested subagents cause harness-latency stalls and Phase 5 "
            "timeouts."
        ),
        "",
        f"SPEC (read this file): {spec_path}",
        "Do NOT modify the spec.",
        "",
        collectability_rule,
        "",
        "Citations must be grounded: cite <path>:<line> only for lines you "
        "have actually read this session; never fabricate.",
        "",
        f"## REVISION (cycle {cycle} — address validator findings)",
        "",
        findings,
        "",
        output_marker_block,
    ]
    return "\n".join(parts)
