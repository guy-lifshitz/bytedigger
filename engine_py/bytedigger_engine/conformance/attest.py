"""bd#10 (BD-L3) — the attestation seam for authorship and inputs (R3.1-R3.6).

Governing frozen text: ``conformance/AUTHORSHIP_SPEC.md`` (FROZEN v6), which in
turn cites ``SHARED/memory/Decisions/2026-07-26_bytedigger_conformance_levels.md``
(CL) §9 item 4 at HAL ``fd35e1304``.

This module is the SEAM, not the checker. It carries the primitives
``llm_subprocess._dispatch_backend`` needs to record what every model invocation
actually was — the assembled prompt's digest, the attributed injection blocks,
the declared capability set — plus the honesty labels that say exactly how far
this engine's claim reaches. bd#28 owns the checker and the report; it READS
``REQUIREMENT_LABELS``, it does not author it (spec §7, ``[bd10:23]``).

AC-C1 of bd#22 still binds the package: importing this module MUST do no work —
no I/O, no network, no subprocess, no directory scan.
"""
from __future__ import annotations

import hashlib
from collections.abc import Sequence
from dataclasses import dataclass
from types import MappingProxyType

# `[bd10:18]` The export surface is CLOSED: anything else this module needs is a
# module-private `_name`.
__all__ = [
    "EVENT_TYPE",
    "hash_text",
    "InjectedBlock",
    "assemble",
    "capability_escapes",
    "REQUIREMENT_LABELS",
]

# Spec §2.2 / AC-P1 — the one event type this lot writes.
EVENT_TYPE = "model_invocation_attested"


def hash_text(text: str) -> str:
    """Return the spec's digest form for *text*: ``"sha256:" + hexdigest``.

    The prefix is part of the recorded value (AC-P2), so a consumer never has to
    guess which algorithm produced a bare hex string.
    """
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class InjectedBlock:
    """One block of prompt text a caller declares through the ``injections``
    channel, with the identifier R3.2 (CL:98) requires it to carry.

    NO runtime validation, deliberately. The chokepoint adjudicates attribution
    (``E_INJECT_UNATTRIBUTED``, AC-I3) and returns an error StepResult; a
    validating ``__post_init__`` would raise at construction instead, turning a
    fail-closed dispatch refusal into an exception the caller never asked for.
    """

    source_id: str
    content: str


def assemble(prompt: str, blocks: "Sequence[InjectedBlock]") -> str:
    """AC-I1: *prompt*, then each block's ``content`` in LIST ORDER, separated
    by exactly ``"\\n\\n"``. An empty block list returns *prompt* unchanged.

    Exported for callers with no placement constraint. `[bd10:16]` separated
    attribution from assembly, so a caller that already places its own text
    (``workflows/phase_2_explore.py:294-297`` prepends its role template) simply
    DECLARES the block instead of routing it through here.
    """
    return "\n\n".join([prompt, *(block.content for block in blocks)])


def _declared_head(entry: str) -> str:
    """The text of a declared capability entry before its first ``"("``.

    Spec §6 AC-C3 pins the matching rule: ``"Bash(graphify-shim.sh:*)"`` admits
    an observed ``"Bash"``. The operand is not part of the comparison — and
    per AC-C6 it never reaches this module at all.
    """
    return entry.split("(", 1)[0]


def capability_escapes(
    observed_tools: "Sequence[str]",
    declared: "Sequence[str]",
) -> "tuple[str, ...]":
    """AC-C3 / R3.6: the SORTED, deduplicated tool heads in *observed_tools*
    that are outside *declared*.

    Matching is exact and case-sensitive against each declared entry's head, so
    ``"bash"`` is an escape under ``"Bash(graphify-shim.sh:*)"``.
    """
    heads = {_declared_head(entry) for entry in declared}
    return tuple(sorted({tool for tool in observed_tools if tool not in heads}))


# AC-P7 / `[bd10:23]` — every narrowing this lot knows about, written down where
# the engine records its own claim rather than where a report renders it.
# Immutable: a caller must not be able to edit the record of what we claim.
# R3.4 carries no label deliberately — it is recorded verbatim (AC-C1) with
# nothing narrowed, and exact-mapping equality is what makes that an assertion.
REQUIREMENT_LABELS: "MappingProxyType[str, str]" = MappingProxyType({
    # CL §8 (the engine hashes at assembly, not on the wire) AND `[bd10:25]`
    # (only invocations under an active run context are attested at all).
    "R3.1": "host-attested-within-run-context",
    # `[bd10:19]` — the channel is enforced; one of eight role-template
    # inlining sites is migrated (AC-I5).
    "R3.2": "injections-channel-only",
    # `[bd10:2]` + bd#29 (2026-08-04) — enforced at the chokepoint for EVERY
    # backend. The in-session path now populates `observed_model`, so it reaches
    # the same adjudication as any other adapter; agreement 220E5F63 (warn-only)
    # is superseded because it contradicted CL:99.
    "R3.3": "chokepoint-enforced",
    # `[bd10:19]` — the backend declares its own enforcement; CL:101 wants a
    # mechanism outside the actor's reach.
    "R3.5": "adapter-declared",
    # AC-C6 — the operand never leaves the adapter.
    "R3.6": "tool-head-only",
})
