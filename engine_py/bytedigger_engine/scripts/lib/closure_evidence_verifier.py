"""Closure-evidence verifier for HAL engine_py spec_lint (GH1409).

Rule
----
A spec that claims a set of VALUES is closed must bind that claim to an
**observed output** — an event, an artifact, a stream — and not to a reading of
the carrier's source text or AST.

Established by execution, not by argument: lot GH1400 was rejected in six
independent gate rounds over exactly this class (forms m18, m24, m31-m39). Each
round tightened a list of spellings over the carrier's Python source; each next
round found a spelling the previous author had not imagined
(`refuse("E_" + type(exc).__name__.upper())`, an alias binding, `"fail" + "ed"`,
`os.execv` past a blacklist containing `exec(`, `ctypes.CDLL(None).fork()` past a
whitelist of modules). Neither a blacklist (as wide as the author's imagination)
nor a whitelist (it bounds the FORM of the writing, not the VALUE) closes a set
of values. Only an observation does.

Difference from §1l (`≥1 AC anchored on a real production side-effect`)
----------------------------------------------------------------------
§1l is an EXISTENTIAL quantifier over the spec: somewhere, some AC touches a real
side-effect. This module is a UNIVERSAL quantifier over closedness CLAIMS: every
claiming line carries a declared edge to an artifact it is observed on. GH1400
rev4/rev5 satisfied §1l — AC1..AC11 asserted on the stdout events of a real
subprocess — and still landed the closedness of the error-code set on the
carrier's AST. §1l does not bind a particular claim to a particular observation.

Declaration grammar (the schema this module introduces)
------------------------------------------------------
    closure-evidence: AC<N> ← <artifact>

`<-` is accepted as an ASCII spelling of the arrow. The substrate is DECLARED,
never inferred from prose — an enforcement predicate cannot be a regex over prose
(learning_no_enforcement_predicate_over_prose): both directions of the error are
available at once, so the claim must be a declared field.

Trigger — two signals on the same AC line
-----------------------------------------
A claim is a line that (a) names at least one `AC<N>`, (b) carries a closedness
term, and (c) carries a set noun — outside fenced blocks and inline-code spans.
Measured over the 884-spec corpus of SHARED/memory/Decisions (spec §1b/C):
vocabulary anywhere 426 specs; on an AC line 61; inline-code stripped 50; with
the set-noun conjunct **7**. The single-signal forms are why this trigger is a
conjunction: `ROLLOUT_CHECK_ALLOWLIST`, `parse_allowlist` and "chicken-and-egg
закрыт" are not claims about a set of values.

Honest borders
--------------
1. Truthfulness of the declaration is NOT checked. An author writing
   `closure-evidence: AC13 ← stdout` over an AC13 that reads AST is not caught
   here — that is the gate's job, now reading ONE declared line instead of
   re-deriving substrate from a 500-line spec. What this module deterministically
   kills is the MUTE case, where no substrate was named at all: all six GH1400
   rounds.
2. Granularity is the LINE, not the AC body. A claim spread across lines binds
   through whichever line carries both signals.
3. No vocabulary hit, no trigger. The refusal direction is conservative by
   construction.

Standalone: scripts/lib is import-isolated from engine_py — no engine imports.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

# Signal (b): the closedness term. Frozen, enumerated; every element is pinned
# against a literal tuple in the test AND demonstrated reachable-and-observed by
# one real driver run per element (spec §3 AC6/AC6b).
CLOSURE_CLAIM_TERMS: tuple[str, ...] = (
    "закрыт",
    "исчерпывающ",
    "closed set",
    "exhaustive",
)

# Signal (c): the thing being closed must be a SET OF VALUES. Without this
# conjunct the trigger fires on 50 corpus specs instead of 7 (see module docstring).
CLOSED_SET_NOUNS: tuple[str, ...] = (
    "множеств", "набор", "перечен", "перечисл", "реестр", "лексик",
    "значени", "литерал", "enum", "set", "vocabular",
)

# Streams that count as an observed output on their own.
OBSERVED_STREAM_TOKENS: frozenset[str] = frozenset({"stdout", "stderr"})

# Suffixes of a produced artifact. This IS a whitelist — legitimately so: it
# bounds the grammar of a field THIS module defines, not the forms of some other
# language. GH1400's whitelists tried to bound arbitrary Python, a language the
# lot did not define and whose spellings are inexhaustible by construction.
OBSERVED_ARTIFACT_SUFFIXES: tuple[str, ...] = (
    ".jsonl", ".json", ".log", ".txt", ".md", ".db", ".csv",
)

RULE_CLAIM_UNBACKED = "closure-claim-unbacked"
RULE_EVIDENCE_DANGLING = "closure-evidence-dangling"
RULE_EVIDENCE_SOURCE_SUBSTRATE = "closure-evidence-source-substrate"

_DECLARATION_RE = re.compile(
    r"^[ \t]*closure-evidence:[ \t]*(AC\d+)[ \t]*(?:←|<-)[ \t]*(\S+)[ \t]*$",
    re.MULTILINE,
)

_AC_ID_RE = re.compile(r"\bAC\d+\b")
_CLAIM_TERM_RE = re.compile("|".join(re.escape(t) for t in CLOSURE_CLAIM_TERMS), re.I)
_SET_NOUN_RE = re.compile("|".join(re.escape(t) for t in CLOSED_SET_NOUNS), re.I)

_FENCE_RE = re.compile(r"```.*?```", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`[^`\n]*`")


@dataclass(frozen=True)
class ClosureFinding:
    offset: int
    rule_id: str
    evidence: str


@dataclass(frozen=True)
class ClosureClaim:
    offset: int
    ac_ids: tuple[str, ...]
    excerpt: str


def _blank(text: str, start: int, end: int) -> str:
    """Blank out [start:end) while preserving every offset and every newline."""
    span = text[start:end]
    return text[:start] + "".join("\n" if ch == "\n" else " " for ch in span) + text[end:]


def _blank_all(text: str, pattern: "re.Pattern[str]", source: str | None = None) -> str:
    out = text
    for m in pattern.finditer(source if source is not None else text):
        out = _blank(out, m.start(), m.end())
    return out


def _scannable(spec_text: str) -> str:
    """Spec text with fenced blocks, inline code and declaration lines blanked.

    Offsets are preserved so every finding still points at the real spec line.
    Quoted material is not a claim; an identifier (`ROLLOUT_CHECK_ALLOWLIST`) is
    not a claim; and the declaration line must not count as its own trigger.
    """
    out = _blank_all(spec_text, _FENCE_RE)
    out = _blank_all(out, _INLINE_CODE_RE, source=out)
    return _blank_all(out, _DECLARATION_RE, source=out)


def find_declarations(spec_text: str) -> list[tuple[int, int, str, str]]:
    """Return (start, end, ac_id, artifact) for every declaration outside a fence."""
    fenceless = _blank_all(spec_text, _FENCE_RE)
    return [
        (m.start(), m.end(), m.group(1), m.group(2))
        for m in _DECLARATION_RE.finditer(fenceless)
    ]


def find_closure_claims(spec_text: str) -> list[ClosureClaim]:
    """Return every closedness claim: an AC line carrying BOTH trigger signals."""
    scannable = _scannable(spec_text)
    claims: list[ClosureClaim] = []
    offset = 0
    for line in scannable.split("\n"):
        ids = tuple(dict.fromkeys(_AC_ID_RE.findall(line)))
        if ids and _CLAIM_TERM_RE.search(line) and _SET_NOUN_RE.search(line):
            claims.append(ClosureClaim(
                offset=offset, ac_ids=ids, excerpt=" ".join(line.split())[:90],
            ))
        offset += len(line) + 1
    return claims


def is_observed_artifact(artifact: str) -> bool:
    """True iff `artifact` names an observed output rather than a source substrate."""
    token = artifact.strip().strip("`\"'")
    if token.lower() in OBSERVED_STREAM_TOKENS:
        return True
    return any(token.lower().endswith(suffix) for suffix in OBSERVED_ARTIFACT_SUFFIXES)


def find_unbacked_closure_claims(
    spec_text: str, hal_root: Path | None = None,  # noqa: ARG001 - sibling parity
) -> list[ClosureFinding]:
    """Return findings for closedness claims not bound to an observed output."""
    findings: list[ClosureFinding] = []

    declarations = find_declarations(spec_text)
    declared_ids = {ac_id for _s, _e, ac_id, _art in declarations}

    # 1. Every declaration is validated, claim or no claim.
    for off, end, ac_id, artifact in declarations:
        elsewhere = _blank(spec_text, off, end)
        if not re.search(rf"\b{re.escape(ac_id)}\b", elsewhere):
            findings.append(ClosureFinding(
                offset=off,
                rule_id=RULE_EVIDENCE_DANGLING,
                evidence=f"closure-evidence names {ac_id}, which the spec never defines",
            ))
        if not is_observed_artifact(artifact):
            findings.append(ClosureFinding(
                offset=off,
                rule_id=RULE_EVIDENCE_SOURCE_SUBSTRATE,
                evidence=(
                    f"closure-evidence artifact {artifact!r} is not an observed "
                    f"output — a closed set of VALUES cannot be closed by reading "
                    f"a source/AST substrate"
                ),
            ))

    # 2. UNIVERSAL over claims: every claiming line needs a declaration naming an
    #    AC id that appears on that line. One honest declaration elsewhere in the
    #    spec does NOT back a claim it does not name.
    for claim in find_closure_claims(spec_text):
        if declared_ids.intersection(claim.ac_ids):
            continue
        findings.append(ClosureFinding(
            offset=claim.offset,
            rule_id=RULE_CLAIM_UNBACKED,
            evidence=(
                f"closedness claim on {'/'.join(claim.ac_ids)} carries no "
                f"`closure-evidence: <that AC> <- <observed artifact>` "
                f"declaration: {claim.excerpt}"
            ),
        ))

    findings.sort(key=lambda f: f.offset)
    return findings
