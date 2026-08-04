"""delta_reviewer_prompt.py — GH#605 diff-only cycle-N re-review prompt.

Public API:
    extract_affected_sections(spec_text, patches) -> list[str]
    build_delta_reviewer_prompt(findings, patches, sections) -> str

Spec: 2026-07-11_GH605_delta_rereview_spec.md §2.1 (upstream Decisions dir)
"""
from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from .restricted_reviewer_prompt import _render_findings

_HEADING_RE = re.compile(r"^(#{1,6})\s", re.MULTILINE)


def _enclosing_section(spec_text: str, match_start: int) -> str:
    """Return the markdown section enclosing match_start, or a ±20-line window
    if the match falls before the first heading."""
    headings = [(m.start(), len(m.group(1))) for m in _HEADING_RE.finditer(spec_text)]
    enclosing_idx = None
    for i, (pos, _level) in enumerate(headings):
        if pos <= match_start:
            enclosing_idx = i
        else:
            break
    if enclosing_idx is None:
        # No preceding heading — ±20-line window around the match.
        lines = spec_text.splitlines(keepends=True)
        offset = 0
        line_idx = 0
        for idx, line in enumerate(lines):
            if offset + len(line) > match_start:
                line_idx = idx
                break
            offset += len(line)
        start = max(0, line_idx - 20)
        end = min(len(lines), line_idx + 20)
        return "".join(lines[start:end])

    start_pos, level = headings[enclosing_idx]
    end_pos = len(spec_text)
    for pos, lvl in headings[enclosing_idx + 1:]:
        if lvl <= level:
            end_pos = pos
            break
    return spec_text[start_pos:end_pos]


def extract_affected_sections(spec_text: str, patches: list[dict]) -> list[str]:
    """For each patch, locate patch["new"] in spec_text (exact substring) and
    return the enclosing section (or ±20-line window). Order-preserving dedup
    by normalized section body. Unfound insertions are skipped; a deletion
    patch (empty patch["new"]) is NOT skipped — it contributes a synthesized
    "[REMOVED]\\n<old text>" section (GH#638 AC4/AC6/AC12)."""
    sections: list[str] = []
    seen: set[str] = set()
    for patch in patches:
        new_text = patch.get("new") or ""
        if not new_text:
            old_text = patch.get("old") or ""
            section = f"[REMOVED]\n{old_text}"
        else:
            idx = spec_text.find(new_text)
            if idx == -1:
                continue
            section = _enclosing_section(spec_text, idx)
        key = " ".join(section.split())
        if key in seen:
            continue
        seen.add(key)
        sections.append(section)
    return sections


def map_findings_to_patches(
    findings: Sequence[Mapping[str, object]], patches: list[dict]
) -> dict[str, list[dict]]:
    """Group patches by str(finding_id), keyed for EVERY finding (GH#638 AC1-3).

    Every finding in `findings` gets an entry in the returned mapping, even if
    no patch is labelled with its id (in which case the value is []).
    Matching is string-normalized so int vs str ids still match.
    """
    mapping: dict[str, list[dict]] = {str(f.get("id")): [] for f in findings}
    for patch in patches:
        key = str(patch.get("finding_id"))
        if key in mapping:
            mapping[key].append(patch)
    return mapping


def build_delta_reviewer_prompt(
    findings: Sequence[Mapping[str, str]],
    patches: list[dict],
    sections: list[str],
) -> str:
    """Return the delta (diff-only) cycle-N restricted reviewer prompt.

    Same output contract as restricted_reviewer_prompt.build_reviewer_prompt
    (findings block, FINDING_<id>: RESOLVED|UNRESOLVED lines, VERDICT line,
    may-NOT-introduce-findings rule) but embeds the surgical patch diffs +
    affected spec sections instead of the full spec.
    """
    findings_block = _render_findings(findings)
    mapping = map_findings_to_patches(findings, patches)

    patch_lines: list[str] = []
    for patch in patches:
        finding_id = patch.get("finding_id", "?")
        old_text = patch.get("old", "")
        new_text = patch.get("new", "")
        patch_lines.append(
            f"PATCH for FINDING_{finding_id}:\n--- old\n{old_text}\n+++ new\n{new_text}"
        )
    patches_block = "\n\n".join(patch_lines)
    sections_block = "\n\n".join(sections)

    hint_lines: list[str] = []
    for finding in findings:
        fid = str(finding.get("id"))
        if not mapping.get(fid):
            hint_lines.append(
                f"  FINDING_{fid}: no patch labelled with this id below -"
                " judge from the full diff and affected sections whether the"
                " cited content was addressed anyway."
            )
    hints_block = "\n".join(hint_lines)

    return (
        "You are auditing whether cycle-N surgical patches resolve the cycle-1 reviewer findings.\n"
        "\n"
        "CYCLE-1 FINDINGS (the ONLY valid scope for this review -"
        " you may NOT introduce new findings):\n"
        f"{findings_block}\n"
        "\n"
        "Vote on the CONTENT of the surgical diff below, not on whether a patch"
        " happens to be labelled with a finding's id -"
        " a finding whose cited/offending content was DELETED"
        " (patch new text is empty) is RESOLVED, same as if it were rewritten.\n"
        f"{hints_block}\n"
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
        "SURGICAL PATCHES APPLIED (old → new, per finding):\n"
        f"{patches_block}\n"
        "\n"
        "AFFECTED SPEC SECTIONS (post-patch state — the ONLY spec context you get):\n"
        f"{sections_block}\n"
        "\n"
        "Begin output:"
    )
