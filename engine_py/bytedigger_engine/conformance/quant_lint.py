"""The quantifier-completeness lint (`AC-C5`).

Spec: `engine_py/conformance/QUANT_LINT_SPEC.md` (bd#39 v12, FROZEN), which
inherits `CONTRACTS_SPEC.md` §1.5 (this public surface) and §1.6 (the
fixture-document grammar) as its normative interface.

The lint checks that a spec document DOCUMENTS its quantifiers and seams:
every declared collection level carries a non-uniformity row, every row
enumerates the reductions its level admits, and every declared seam pins its
three interception properties. It does NOT verify that the fixtures a
document cites exist or that they discriminate — that is the gate's
judgement and it does not mechanise (§2.0).

`lint_quantifier_completeness` is a pure text → findings function. It never
raises: returning findings is the contract, and raising would make
"conformant" and "malformed" indistinguishable to the build step that
consumes it (§2.1). Nothing consumes it yet — this lot ships the function
and its tests (§6).
"""
from __future__ import annotations

from dataclasses import dataclass

__all__ = ["Finding", "lint_quantifier_completeness"]


@dataclass(frozen=True)
class Finding:
    """One documented-completeness defect.

    Exactly two attributes (§2.1). `kind` is one of the three values below,
    so a caller distinguishes the checks by value rather than by parsing a
    message; `subject` is the offending level or seam name VERBATIM from the
    document, which is what forbids normalising it on the way out (P1).
    """

    kind: str
    subject: str


KIND_MISSING_ROW = "missing_non_uniformity_row"
KIND_MISSING_REDUCTIONS = "missing_reductions"
KIND_SEAM_NOT_PINNED = "seam_not_pinned"

# The reduction vocabulary is exactly these four, lowercase, matched
# case-SENSITIVELY: any other token — including `Any`, `ALL`, `First` — is
# unrecognised and is itself a finding (`[G24:1]`).
REDUCTIONS = frozenset({"any", "all", "first", "last"})

_LEVEL = "LEVEL:"
_ADMITS = "ADMITS:"
_ROW = "NON-UNIFORMITY:"
_EXCLUDES = "EXCLUDES:"
_SEAM = "SEAM:"

# The three properties a seam declaration must pin (§1.6).
_ATTRIBUTE_PATH = "ATTRIBUTE-PATH:"
_BINDING_TIME = "BINDING-TIME:"
_NORMALISATION = "NORMALISATION:"
_PROPERTIES = (_ATTRIBUTE_PATH, _BINDING_TIME, _NORMALISATION)

# All eight markers carry their colon, so `LEVEL :` is not a marker and
# declares nothing (§0.9(3e)). No marker is a prefix of another.
_MARKERS = (_LEVEL, _ADMITS, _ROW, _EXCLUDES, _SEAM) + _PROPERTIES

_EM_DASH = "—"


def _match_marker(line: str) -> tuple[str, str] | None:
    """Recognise a marker at LINE START, case-insensitively.

    Returns `(marker, operand)`, or `None` for prose.

    Two clauses meet here. `[G24:8]`: markers are recognised at line start,
    so an indented or quoted `LEVEL:` inside prose is prose — the comparison
    is against the raw line, never against `line.strip()`. `[G24:7]`: an
    operand is the text after the marker's colon with SURROUNDING whitespace
    and the line terminator removed, which is both sides and not a fixed
    `len(marker) + 1` offset assuming a single space.

    The case fold is applied to the marker-length PREFIX of the original
    line, never to the whole line before slicing it: `str.upper` is not
    length-preserving, so slicing a folded line by a marker's length can
    lose or gain characters from the operand.
    """
    for marker in _MARKERS:
        if line[: len(marker)].upper() == marker:
            return marker, line[len(marker):].strip()
    return None


def _reduction_tokens(operand: str) -> list[str]:
    """Split a reduction operand into its tokens (`[G24:11]`).

    The separator is a comma with optional whitespace on EITHER SIDE OF IT,
    so `any,all`, `any, all`, `any ,all` and `any , all` all list two
    tokens. An empty token is ignored, so a trailing comma changes nothing.

    Whitespace is stripped from a token's FRAMING only: a token's interior
    feeds recognition (`[G24:1]` makes a token outside the four itself a
    finding), so `fi rst` must survive as that token and be unrecognised
    rather than be repaired into `first`.
    """
    return [token for token in (raw.strip() for raw in operand.split(",")) if token]


def _row_subject(operand: str) -> str:
    """The level a `NON-UNIFORMITY` row names (`[G24:11]`).

    The text up to the FIRST em dash, or the whole operand when the row
    carries none — so `NON-UNIFORMITY: phases` discharges `phases` and a
    second em dash inside a description changes nothing. The delimiter is
    the em dash ITSELF, and whitespace on either side of it belongs to
    neither operand; an em dash set closed-up is ordinary house style.
    """
    return operand.split(_EM_DASH, 1)[0].strip()


class _Level:
    """A declared `LEVEL`, with whatever its own `ADMITS` lines say."""

    __slots__ = ("admits", "declared_admits", "invalid_token")

    def __init__(self) -> None:
        self.admits: set[str] = set()
        self.declared_admits = False
        self.invalid_token = False


class _Row:
    """A `NON-UNIFORMITY` row, keyed by the level name it names."""

    __slots__ = ("excludes", "has_excludes", "invalid_token")

    def __init__(self) -> None:
        self.excludes: set[str] = set()
        self.has_excludes = False
        self.invalid_token = False


def lint_quantifier_completeness(text: str) -> list[Finding]:
    """Return the document's completeness findings; empty means conformant.

    Never raises, on a non-conformant or a malformed document alike (§2.1).
    """
    levels: dict[str, _Level] = {}
    rows: dict[str, _Row] = {}
    seams: dict[str, set[str]] = {}

    # The three anchor kinds are tracked INDEPENDENTLY (`[G24:10]`): a row is
    # not a seam and a seam is not a row, so material of the three kinds may
    # interleave freely and no kind closes another kind's live anchor. A
    # single shared "current declaration" variable collapses them.
    current_level: str | None = None
    current_row: str | None = None
    current_seam: str | None = None

    # Parsed in full before anything is judged: a row and its `EXCLUDES` may
    # sit textually ABOVE the `LEVEL` whose `ADMITS` governs them, since
    # position is not part of the binding (`[G24:9]`).
    for line in text.split("\n"):
        matched = _match_marker(line)
        if matched is None:
            continue
        marker, operand = matched

        if marker == _LEVEL:
            current_level = operand
            levels.setdefault(operand, _Level())

        elif marker == _SEAM:
            current_seam = operand
            # Registered on its own declaration line, so a bare `SEAM:` with
            # ZERO property lines is a seam and is flagged — §0.2's founding
            # case, and the fixture bd#22 round 3 deleted.
            seams.setdefault(operand, set())

        elif marker == _ROW:
            # P4: the row binds to the level named by its own operand,
            # matched verbatim and exactly, never by proximity.
            current_row = _row_subject(operand)
            rows.setdefault(current_row, _Row())

        elif marker == _ADMITS:
            # P2: nearest preceding `LEVEL`, at any distance and across
            # intervening material of any kind. With no anchor at all the
            # line binds to nothing (`[G24:5]`) — and its tokens are not
            # validated either, since validation happens on the BOUND level
            # and an eager validator would invent a finding from an
            # unanchored line (`[G24:13]`).
            if current_level is None:
                continue
            tokens = _reduction_tokens(operand)
            if not tokens:
                # `[G24:14]`: a marker line contributing zero tokens is not
                # an empty admitted set. A bare `ADMITS:` reads as ABSENT,
                # so the level keeps the default four; the permissive
                # reading makes every row conformant however short it is.
                continue
            level = levels[current_level]
            level.declared_admits = True
            for token in tokens:
                if token in REDUCTIONS:
                    level.admits.add(token)
                else:
                    level.invalid_token = True

        elif marker == _EXCLUDES:
            # `[G24:2]`: nearest preceding `NON-UNIFORMITY` ROW, never the
            # nearest preceding `LEVEL` — the level is reached transitively
            # through the row's own operand. With no row before it the line
            # binds to nothing, contributes no coverage, and is not itself a
            # finding (`[G24:5]`; there is no kind for an unanchored line).
            if current_row is None:
                continue
            row = rows[current_row]
            # Present but empty reads as NO COVERAGE — the row is short —
            # rather than as an empty requirement (`[G24:14]`).
            row.has_excludes = True
            for token in _reduction_tokens(operand):
                if token in REDUCTIONS:
                    row.excludes.add(token)
                else:
                    row.invalid_token = True

        else:
            # P3: a property line binds to the nearest preceding `SEAM`,
            # never a following one, and with none before it binds to
            # nothing — in particular it is NOT collected under a sentinel
            # seam and reported (`[G24:5]`), and it is not credited forward.
            if current_seam is None:
                continue
            if operand:
                # `[G24:15]`: a line whose operand is empty or
                # whitespace-only PINS NOTHING — a seam with all three
                # markers laid out and none filled is a hand-written
                # template, which §0.2 requires be reported. `[G24:4]`:
                # repetition is collapsed, never punished, and an unfilled
                # line never unpins a filled one — which is why the store is
                # a set of pinned markers rather than a mapping with a
                # first-wins or last-wins write policy.
                seams[current_seam].add(marker)

    findings: list[Finding] = []
    seen: set[tuple[str, str]] = set()

    def emit(kind: str, subject: str) -> None:
        """At most ONE finding per `(kind, subject)` PAIR (`[G24:3]`).

        Keyed on the pair and never on the subject alone: one name may be
        both a row-less `LEVEL` and a bare `SEAM`, and keying on the subject
        would silently drop one of the two.
        """
        key = (kind, subject)
        if key not in seen:
            seen.add(key)
            findings.append(Finding(kind, subject))

    # Check 1 — a declared level that no row names (P4, verbatim: never a
    # substring match in either direction, never case-folded).
    for name in levels:
        if name not in rows:
            emit(KIND_MISSING_ROW, name)

    # Check 2, the row-independent ground (`[G24:12]`): an unrecognised token
    # in a level's OWN `ADMITS` makes its admitted set uncomputable, which is
    # a defect of the level whether or not it has a row.
    for name, level in levels.items():
        if level.invalid_token:
            emit(KIND_MISSING_REDUCTIONS, name)

    # Check 2 proper. Checks 1 and 2 are independent: a level with no row
    # yields check 1 and no coverage-based finding, there being no row whose
    # coverage could be short.
    for subject, row in rows.items():
        level = levels.get(subject)
        if level is not None and level.declared_admits:
            admits = level.admits
        else:
            # An absent `ADMITS` means all four — and so does a row naming a
            # level the document never declares, which is still checked
            # against the default rather than skipped (`[G24:6]`), or a
            # typo'd operand would hide its own row's under-enumeration.
            admits = REDUCTIONS
        # Superset, not equality (K12): a row excluding MORE than its level
        # admits kills more than it had to, which is never the defect this
        # check hunts.
        if not row.has_excludes or row.invalid_token or not row.excludes >= admits:
            emit(KIND_MISSING_REDUCTIONS, subject)

    # Check 3 — over the SET of properties PINNED under the seam, not the
    # count of marker lines (`[G24:4]`).
    for name, pinned in seams.items():
        if len(pinned) < len(_PROPERTIES):
            emit(KIND_SEAM_NOT_PINNED, name)

    return findings
