"""restricted_reviewer_prompt.py — build the cycle-2 restricted reviewer prompt.

Public API:
    build_reviewer_prompt(findings, new_spec) -> str
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


def build_reviewer_prompt(
    findings: Sequence[Mapping[str, str]], new_spec: str
) -> str:
    """Return the restricted cycle-2 reviewer prompt.

    The reviewer must assess each cycle-1 finding as RESOLVED or UNRESOLVED,
    and may NOT introduce new findings beyond the cycle-1 list.
    """
    findings_block = _render_findings(findings)
    return (
        "You are auditing whether a cycle-2 revised spec resolves the cycle-1 reviewer findings.\n"
        "\n"
        "CYCLE-1 FINDINGS (the ONLY valid scope for this review -"
        " you may NOT introduce new findings):\n"
        f"{findings_block}\n"
        "\n"
        "For each finding listed above, emit one line:\n"
        "  FINDING_<id>: <RESOLVED|UNRESOLVED> - <one-sentence evidence pointing to the spec change>\n"
        "\n"
        "Then emit one final line:\n"
        "  VERDICT: <PASS|REVISE>\n"
        "\n"
        "Rules:\n"
        "- VERDICT=PASS iff ALL findings RESOLVED.\n"
        "- You may NOT introduce findings beyond the cycle-1 list."
        " New issues you spot are OUT OF SCOPE for this audit.\n"
        "- Be strict about resolution: a finding is RESOLVED only if the spec change"
        " directly addresses the cited issue.\n"
        "\n"
        "CYCLE-2 SPEC:\n"
        f"{new_spec}\n"
        "\n"
        "Begin output:"
    )
