"""restricted_writer_prompt.py — build the cycle-2 restricted writer prompt.

Public API:
    build_writer_prompt(spec, findings) -> str
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence


def _render_findings(findings: Sequence[Mapping[str, str]]) -> str:
    lines: list[str] = []
    for f in findings:
        fid = f.get("id", "?")
        ftype = f.get("type", "issue")
        evidence = f.get("evidence", "")
        required_action = f.get("required_action", "")
        line = f"- FINDING_{fid} [{ftype}]: {evidence}"
        if required_action and required_action != evidence:
            line += f"\n  required_action: {required_action}"
        lines.append(line)
    return "\n".join(lines)


def build_writer_prompt(
    spec: str,
    findings: Sequence[Mapping[str, str]],
    verbatim_reviewer_context: str | None = None,
) -> str:
    """Return the restricted cycle-2 writer prompt.

    The prompt instructs the writer to modify ONLY items flagged by cycle-1
    findings, with no new scope additions.

    When verbatim_reviewer_context is provided (non-None, non-empty), the raw
    reviewer prose is injected verbatim (capped at 4096 UTF-8 bytes) BEFORE the
    structured CYCLE-1 REVIEWER FINDINGS block. When None or empty, the output
    is byte-identical to the original two-argument form (B9B82CE0 backward compat).
    """
    findings_block = _render_findings(findings)
    if verbatim_reviewer_context:
        clamped = verbatim_reviewer_context.encode("utf-8")[:4096].decode("utf-8", errors="ignore")
        verbatim_block = f"REVIEWER VERDICT (verbatim, for context):\n{clamped}\n\n"
    else:
        verbatim_block = ""
    return (
        "You are revising a spec to address reviewer findings on cycle 1.\n"
        "\n"
        "CONSTRAINTS (HARD - violations will be flagged by the reviewer):\n"
        "- ONLY modify lines that directly address one of the cycle-1 findings below.\n"
        "- Do NOT add new ACs, behaviors, validations, scope items, or rationale text"
        " not required by a finding.\n"
        "- Do NOT touch sections unrelated to a finding.\n"
        "- For each finding, address it minimally - either fix the cited issue,"
        " OR move the affected content to ## Open Questions / ## Out of Scope.\n"
        "- You may NOT introduce content the reviewer hasn't asked for.\n"
        "\n"
        f"{verbatim_block}"
        "CYCLE-1 REVIEWER FINDINGS:\n"
        f"{findings_block}\n"
        "\n"
        "ORIGINAL SPEC:\n"
        f"{spec}\n"
        "\n"
        "Output ONLY the full revised spec markdown. No preamble, no explanation."
    )
