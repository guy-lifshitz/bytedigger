"""surgical_revise.py — point-patch (old->new) REVISE writer contract.

GH#592: instead of asking the writer to re-emit the FULL revised spec on a
cycle>=2 REVISE (delta_retry_prompt.py, GH443), ask it for a small JSON array
of exact-match `old -> new` patches and apply them deterministically. Untouched
spec bytes stay byte-identical; any inapplicable patch routes to a full-rewrite
fallback (never a silent skip).

Public API:
    build_surgical_revise_prompt(spec_path, spec, findings,
                                  verbatim_reviewer_context=None) -> str
    extract_surgical_patches(raw) -> list[dict] | None
    apply_surgical_patches(spec_text, patches) -> tuple[str | None, dict]

No host paths, no HAL_* env reads, no SHARED/ refs in this file
(core-boundary clean, mirrors delta_retry_prompt.py's header contract).
"""
from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence

from .restricted_writer_prompt import _render_findings

_JSON_FENCE_RE = re.compile(r"```[ \t]*[jJ][sS][oO][nN][ \t]*\r?\n?(.*?)```", re.DOTALL)
_ANY_FENCE_RE = re.compile(r"```[ \t]*[A-Za-z0-9_+-]*[ \t]*\r?\n?(.*?)```", re.DOTALL)

_REQUIRED_KEYS = ("finding_id", "old", "new")


def build_surgical_revise_prompt(
    spec_path: str,
    spec: str,
    findings: Sequence[Mapping[str, str]],
    verbatim_reviewer_context: str | None = None,
) -> str:
    """Return the surgical-patch REVISE prompt.

    Instructs the writer to respond with ONLY one fenced ```json array of
    `{"finding_id", "old", "new"}` patch objects — `old` must be a verbatim,
    unique fragment of the CURRENT spec text (echoed below). No Write tool,
    no full spec, no prose outside the fence.
    """
    findings_block = _render_findings(findings)
    if verbatim_reviewer_context:
        clamped = verbatim_reviewer_context.encode("utf-8")[:4096].decode(
            "utf-8", errors="ignore"
        )
        verbatim_block = f"REVIEWER VERDICT (verbatim, for context):\n{clamped}\n\n"
    else:
        verbatim_block = ""
    return (
        "You are revising a spec to address reviewer findings on cycle >=2.\n"
        "\n"
        "OUTPUT CONTRACT (HARD):\n"
        "- Respond with ONLY ONE fenced ```json code block containing a JSON"
        " array of patch objects: [{\"finding_id\": <id>, \"old\": <exact"
        " verbatim fragment of the CURRENT spec below>, \"new\": <replacement"
        " text>}, ...].\n"
        "- Do NOT use the Write tool. Do NOT output the full spec. Do NOT"
        " output any prose outside the fenced json block.\n"
        "- `old` MUST be copied verbatim (byte-for-byte) from the CURRENT"
        " SPEC section below, and MUST be unique within it (occurs exactly"
        " once) — otherwise the patch cannot be applied deterministically.\n"
        "- Address ONLY the findings below. Do NOT widen scope; do NOT add"
        " features, ACs, or rationale not required by a finding.\n"
        "\n"
        f"{verbatim_block}"
        "CYCLE FINDINGS:\n"
        f"{findings_block}\n"
        "\n"
        f"CURRENT SPEC (path: {spec_path}):\n"
        f"{spec}\n"
        "\n"
        "Output ONLY the fenced ```json patch array. No preamble, no"
        " explanation, no full spec rewrite.\n"
    )


def _validate_patch_list(parsed: object) -> list[dict] | None:
    if not isinstance(parsed, list):
        return None
    patches: list[dict] = []
    for item in parsed:
        if not isinstance(item, dict):
            return None
        for key in _REQUIRED_KEYS:
            if key not in item or not isinstance(item[key], str):
                return None
        if not item["old"]:
            return None
        patches.append(item)
    return patches


def extract_surgical_patches(raw: str) -> list[dict] | None:
    """Parse a fenced (or bare) JSON patch array in `raw`, fail-soft.

    Tries, in order: last ```json fence, last generic ``` fence, bare
    `[...]` array slice. First candidate that parses AND validates as a
    patch array (see `_validate_patch_list`) wins. Any deviation -> None.
    """
    if not raw:
        return None

    candidates: list[str] = []
    json_matches = _JSON_FENCE_RE.findall(raw)
    if json_matches:
        candidates.append(json_matches[-1])
    any_matches = _ANY_FENCE_RE.findall(raw)
    if any_matches:
        candidates.append(any_matches[-1])
    start = raw.find("[")
    end = raw.rfind("]")
    if start >= 0 and end > start:
        candidates.append(raw[start : end + 1])

    for body in candidates:
        try:
            parsed = json.loads(body)
        except (json.JSONDecodeError, ValueError):
            continue
        result = _validate_patch_list(parsed)
        if result is not None:
            return result

    return None


def apply_surgical_patches(spec_text: str, patches: list[dict]) -> tuple[str | None, dict]:
    """Sequentially apply patches to spec_text via exact-match single-occurrence replace.

    Returns (patched_text, {"n_patches": N}) on full success, or
    (None, {"reason": <r>, "finding_id": <id|None>}) on first failure.
    reason in {"no_patches", "empty_old", "old_not_found", "old_ambiguous"}.
    """
    if not patches:
        return None, {"reason": "no_patches", "finding_id": None}

    text = spec_text
    n_applied = 0
    for patch in patches:
        finding_id = patch.get("finding_id")
        old = patch.get("old", "")
        new = patch.get("new", "")
        if not old:
            return None, {"reason": "empty_old", "finding_id": finding_id}
        count = text.count(old)
        if count == 0:
            return None, {"reason": "old_not_found", "finding_id": finding_id}
        if count > 1:
            return None, {"reason": "old_ambiguous", "finding_id": finding_id}
        text = text.replace(old, new, 1)
        n_applied += 1

    return text, {"n_patches": n_applied}
