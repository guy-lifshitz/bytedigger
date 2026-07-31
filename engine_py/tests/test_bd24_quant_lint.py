"""RED tests for bd#39 (child of bd#24): the quantifier-completeness lint.

One AC, `AC-C5`, of which **bd#24 remains the bearer** -- which is why this
file keeps its name. Renaming it would break the containment script's path to
bd#22's round-9 artifact and buy nothing.

Spec: engine_py/conformance/QUANT_LINT_SPEC.md (bd#39 **v11**, FROZEN under this
lot's number, §0.0 for why the lot exists), which inherits
CONTRACTS_SPEC.md §0.1-§0.7, §1.5 (the public surface and `Finding`) and §1.6
(the fixture-document grammar) as its normative interface.

`conformance.quant_lint` exists on this base as an import-only PLACEHOLDER
that defines no public names (`[G22:18]`), so every test here fails today at
`ImportError` on `from conformance.quant_lint import
lint_quantifier_completeness`. There are **no** declared pre-passing tests in
this lot (spec §4): every test calls the lint, and a passing test at RED time
is a defect rather than a shield.

Deferred-import discipline: every `conformance.*` import happens inside a test
body, never at module level, so collection stays clean.

No monkeypatching anywhere. bd#22 round 1 replaced `builtins.open` with a
raiser -- a primitive pytest itself runs on -- and killed the session
(`[G22:2]`, measured 1 passed / 19 errors / crash). `lint_quantifier_
completeness` is a pure text -> findings function, so every test here is a
call plus assertions on the returned list; there is no seam to substitute.

PROVENANCE OF THE PARTS, and it is load-bearing rather than tidy. Parts 1-4
were cut inside bd#24, up to and including the correction its exit criterion
accepted -- gate round 4's MAJOR, closed by MEASURING the second side of
`[G24:7]`'s framing clause (`_OPERAND_SPACING_SPEC`) rather than narrowing the
clause. Parts 5-8 were cut after that criterion should have fired and did not,
because the dispatch channel was returning `state=busy` (hal#1512). Their
findings are real, every one confirmed by execution, and spec §0.0 carries them
as this lot's INPUT REQUIREMENTS -- they are not rediscoverable work. The last
of them, `_ANCHOR_CYCLE_SPEC`, was carried in unclosed -- deleting a fixture
that kills three confirmed candidates is the exact act that rejected bd#22
round 4 -- and referred to this lot's gate. **bd#39 round 1 ruled**: the
finding is sound, the fixture non-vacuous, the clause pinned. The deferral also
produced the inverse of the usual defect -- the RED enforcing a semantic the
frozen spec did not record -- which spec §0.0 v2 records and closes.

STRUCTURE, and it is deliberate (spec §0.9 / §5, the coverage-diff
obligation). Part 1 below is bd#22's round-9 AC-C5 artifact
(`d39371f:engine_py/tests/test_bd22_contracts.py`) carried over
BYTE-IDENTICALLY -- every fixture constant and every test function, unmodified
and undeleted -- so that no candidate the previous set killed can silently
stop being killed. bd#22 round 3 lost check 3's base-case fixture
(`_SEAM_NOT_PINNED_SPEC`) exactly that way, and round 4 rejected on it. Part 2
holds this lot's additions, including that fixture restored under its original
name. Every docstring names the §3 candidate IDs it kills.
"""
from __future__ import annotations

import pytest


# =========================================================================
# PART 1 -- carried verbatim from bd#22 round 9 (d39371f). Do not edit:
# byte-identity with that artifact is what makes the coverage diff in spec
# §5 mechanically checkable.
# =========================================================================

# AC-C5 — quantifier-completeness lint (three independent checks)
# ─────────────────────────────────────────────────────────────────────────

# §1.6 fixture-document grammar, verbatim: LEVEL / NON-UNIFORMITY / EXCLUDES /
# ADMITS / SEAM / ATTRIBUTE-PATH / BINDING-TIME / NORMALISATION, recognised
# at line start, case-insensitive. `ADMITS` is optional on a LEVEL; absent
# means all four reductions (any/all/first/last). This is the lint's input
# format, not this lot's own spec-document format (§1.6) — these fixtures
# are never run over CONTRACTS_SPEC.md itself.

# Three levels, one per direction named in §0.1 (container, element kind,
# payload field), all missing a non-uniformity row — a lint walking only
# two of the three directions (bd#7's round-9 gap: containers and element
# kinds, but not payload fields) still fails this fixture. `Audit_Field`'s
# name is deliberately mixed-case (proactive, `[G22:13]`'s "case-folding
# where the spec pins verbatim text" candidate) — the other two level names
# here are already lowercase, so a GREEN that case-folds level names before
# comparison would still match the expected literal for them; only the
# mixed-case one forces `Finding.subject` to preserve the document's
# verbatim casing (§1.5) rather than a normalised form of it.
_MISSING_ROW_SPEC = """\
# Fixture Spec — missing non-uniformity rows, all three directions

LEVEL: phases

LEVEL: step_events

LEVEL: Audit_Field

SEAM: Path.read_text
ATTRIBUTE-PATH: pathlib.Path.read_text
BINDING-TIME: call-time
NORMALISATION: none
"""

# `[G22:5]`/`[G22:7]`/`[G22:9]`/`[G22:14]` check-2 default-ADMITS fixture:
# FIVE rows, none carrying an ADMITS line, so every row is checked against
# the ADMITS-absent DEFAULT (all four) — the fix for round 2's mistake
# (explicit ADMITS, never exercising the default at all). `phases` has no
# EXCLUDES line at all. The other four each omit exactly ONE reduction from
# the default set — `step_events` omits `all`, `retry_window` omits `any`,
# `audit_gate` omits `first`, `commit_step` omits `last` — so the default
# set's MEMBERSHIP is load-bearing member by member (`[G22:14]`): round 3
# only ever omitted `all` (or `first`+`last` together on the explicit path),
# so a GREEN whose default set silently drops `any` (or `first`, or `last`)
# passed everything. All five rows are non-conformant, so this fixture needs
# no conformant row to be ordering-safe (§0.8 rule 1) — a first-only or
# last-only lint still misses at least one of the five and is caught. A
# third row is in the *separate* `_CHECK2_EXPLICIT_ADMITS_SPEC` below, per
# rule 2 (`[G22:8]`): present and default are two fixtures, not one.
_CHECK2_SPEC = """\
# Fixture Spec — check 2: no-EXCLUDES-at-all, and each of the four default
# reductions individually omitted, in the same document

LEVEL: phases
NON-UNIFORMITY: phases — fixture set has >=2 members, one violating, plus control

LEVEL: step_events
NON-UNIFORMITY: step_events — fixture set is non-uniform in both orderings
EXCLUDES: any, first, last

LEVEL: Retry_Window
NON-UNIFORMITY: Retry_Window — fixture set has >=2 members, one violating, plus control
EXCLUDES: all, first, last

LEVEL: audit_gate
NON-UNIFORMITY: audit_gate — fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all, last

LEVEL: commit_step
NON-UNIFORMITY: commit_step — fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all, first

SEAM: Path.read_text
ATTRIBUTE-PATH: pathlib.Path.read_text
BINDING-TIME: call-time
NORMALISATION: none
"""

# `[G22:9]` check-2 EXPLICIT-ADMITS fixture, kept separate from the
# default-path fixture above per rule 2. TWO offending rows (`audit_step`,
# `retry_budget`) bracket ONE conformant row (`payload_field`) in the
# middle — mirroring `_CHECK2_SPEC`'s "both offenders individually asserted"
# shape rather than a single-offender/single-conformant pair, which a
# first-only or last-only evaluator could pass vacuously by landing on
# whichever member happens to sit at the position it checks (found during
# the `§0.8` self-sweep on this round; not present in the gate's own
# findings). `payload_field` explicitly ADMITS only `any, all` (an unordered
# collection, where `first`/`last` are meaningless) and its row fully covers
# that admitted set. `audit_step` and `retry_budget` both explicitly ADMIT
# all four and both EXCLUDES only two (present-but-short against an
# EXPLICIT admitted set, not the default).
_CHECK2_EXPLICIT_ADMITS_SPEC = """\
# Fixture Spec — check 2: explicit ADMITS, two offenders bracketing a
# conformant row

LEVEL: audit_step
ADMITS: any, all, first, last
NON-UNIFORMITY: audit_step — fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all

LEVEL: payload_field
ADMITS: any, all
NON-UNIFORMITY: payload_field — fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all

LEVEL: retry_budget
ADMITS: any, all, first, last
NON-UNIFORMITY: retry_budget — fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all

SEAM: Path.read_text
ATTRIBUTE-PATH: pathlib.Path.read_text
BINDING-TIME: call-time
NORMALISATION: none
"""

# Check-2 row-to-level binding fixture (flagged as a gap in the round-7
# report, closed here): a present-but-short row bound to the WRONG level —
# every prior check-2 fixture had its row immediately follow its own level,
# so "incidental coverage" of the binding via check 1's fixture was never
# verified for check 2 specifically (`[G22:10]`'s own rule: incidental
# coverage is not coverage). The row here is textually adjacent to
# `beta_gate` but its `<level>` operand names `alpha_gate` — a lint binding
# by PROXIMITY (nearest preceding LEVEL) instead of reading the row's own
# operand would misattribute it to `beta_gate`, wrongly reporting `beta_gate`
# as present-but-short and `alpha_gate` as missing a row entirely (check 1),
# rather than the reverse — exactly what correct (operand-text) binding
# produces.
_CHECK2_WRONG_LEVEL_BINDING_SPEC = """\
# Fixture Spec — check 2: present-but-short row adjacent to one level,
# naming another

LEVEL: alpha_gate

LEVEL: beta_gate
NON-UNIFORMITY: alpha_gate — fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all, first

SEAM: Path.read_text
ATTRIBUTE-PATH: pathlib.Path.read_text
BINDING-TIME: call-time
NORMALISATION: none
"""

# `[G22:15]` check-1 row-to-level BINDING fixture: kills two GREENs that
# survived all 19 tests through round 3 — document-global presence (any
# `NON-UNIFORMITY:` line anywhere ⇒ nothing missing) and substring rather
# than exact operand matching (`LEVEL: audit` discharged by a row naming
# `audit_field`). `phases` and `audit` each have NO row of their own, but
# the document DOES contain rows — for `workers` and `audit_field`
# respectively — so a document-global-presence lint would wrongly conclude
# nothing is missing, and a substring-matching lint would wrongly credit
# `audit_field`'s row to `audit`. Both non-adjacent offenders (`phases` at
# position 1, `audit` at position 3 of 4) are bracketed by conformant levels
# (`workers`, `audit_field`), so this fixture is also self-sufficient against
# first-only/last-only evaluators without needing a sibling fixture.
_ROW_LEVEL_BINDING_SPEC = """\
# Fixture Spec — check 1: row-to-level binding (wrong level named, and a
# level name that is a substring of another's)

LEVEL: phases

LEVEL: workers
NON-UNIFORMITY: workers — fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all, first, last

LEVEL: audit

LEVEL: audit_field
NON-UNIFORMITY: audit_field — fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all, first, last

SEAM: Path.read_text
ATTRIBUTE-PATH: pathlib.Path.read_text
BINDING-TIME: call-time
NORMALISATION: none
"""

# `[G22:16]` case-sensitive operand matching, pinned in spec v7: `LEVEL:
# Audit_Case` is NOT discharged by `NON-UNIFORMITY: audit_case — …`, since
# `Finding.subject` is verbatim and a case-folding lint cannot both fold to
# match and report verbatim. `Audit_Case` (mixed-case level) and
# `audit_case` (all-lowercase row operand) differ ONLY in casing — a lint
# that case-folds operands before comparing would wrongly see the row as
# discharging the level; the level must still be flagged.
_LEVEL_CASE_MISMATCH_SPEC = """\
# Fixture Spec — level and row differ only in casing

LEVEL: Audit_Case
NON-UNIFORMITY: audit_case — fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all, first, last
"""

# Bonus coverage (proactive, not required by the gate this round, but on
# `[G22:13]`'s standing candidate list — "operand bound to the wrong
# neighbour"): `ADMITS` binds to the NEAREST PRECEDING `LEVEL` (§1.6),
# never a following one. `alpha_level` explicitly `ADMITS: any, all` and its
# row covers exactly that (conformant *only* if its own `ADMITS` applies to
# it, not to `beta_level` after it). `beta_level` has NO `ADMITS` of its own
# (default: all four) and its row covers only `any, all` — conformant *only*
# if it is checked against the default, not against `alpha_level`'s `ADMITS`
# migrating forward onto it. A wrong-neighbour-binding lint gets BOTH
# levels backwards: it flags `alpha_level` (checks it against the default
# instead of its own `ADMITS`) and clears `beta_level` (checks it against
# `alpha_level`'s `ADMITS` instead of the default) — the opposite of a
# correct, preceding-binding lint.
_ADMITS_WRONG_NEIGHBOUR_SPEC = """\
# Fixture Spec — ADMITS binds to the nearest preceding LEVEL, not following

LEVEL: alpha_level
ADMITS: any, all
NON-UNIFORMITY: alpha_level — fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all

LEVEL: beta_level
NON-UNIFORMITY: beta_level — fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all
"""

# `[G22:7]`/`[G22:11]` check-3 fixtures: THREE documents, each with TWO (or
# three) seams — one fully conformant, one missing exactly one of the three
# property lines — so a lint testing presence of only one property line
# cannot pass all three, and a lint reporting only the first or only the
# last SEAM block cannot pass all three either. The offending seam sits
# FIRST in the ATTRIBUTE-PATH and NORMALISATION fixtures (the latter also
# carries a third, trailing conformant seam), and LAST in the BINDING-TIME
# fixture — both orderings represented across the set (`[G22:8]` rule 1;
# round 2 put the offender last in all three, so a last-block-only lint
# passed everything).
_SEAM_MISSING_ATTRIBUTE_PATH_SPEC = """\
# Fixture Spec — check 3: one seam missing ATTRIBUTE-PATH only (offender first)

SEAM: importlib.metadata.version
BINDING-TIME: call-time
NORMALISATION: none

SEAM: Path.read_text
ATTRIBUTE-PATH: pathlib.Path.read_text
BINDING-TIME: call-time
NORMALISATION: none
"""

# Offender name is deliberately MIXED-CASE (`MixedCase.Attribute`, not the
# usual all-lowercase `importlib.metadata.version`) — the other two check-3
# fixtures' offender happens to be all-lowercase already, so a GREEN that
# case-folds every line before parsing (or that stores `subject` upper/
# lower-cased) would still match the expected literal there, and the
# negative guards against `Path.read_text` were vacuous for the same
# reason (a case-folding GREEN can never produce that exact mixed-case
# string). This fixture forces `Finding.subject` to preserve the document's
# verbatim casing (§1.5), not a normalised form of it.
_SEAM_MISSING_BINDING_TIME_SPEC = """\
# Fixture Spec — check 3: one seam missing BINDING-TIME only (offender
# last, mixed-case name)

SEAM: Path.read_text
ATTRIBUTE-PATH: pathlib.Path.read_text
BINDING-TIME: call-time
NORMALISATION: none

SEAM: MixedCase.Attribute
ATTRIBUTE-PATH: MixedCase.Attribute
NORMALISATION: none
"""

_SEAM_MISSING_NORMALISATION_SPEC = """\
# Fixture Spec — check 3: one seam missing NORMALISATION only (offender
# first, plus a third conformant seam trailing it)

SEAM: importlib.metadata.version
ATTRIBUTE-PATH: importlib.metadata.version
BINDING-TIME: call-time

SEAM: Path.read_text
ATTRIBUTE-PATH: pathlib.Path.read_text
BINDING-TIME: call-time
NORMALISATION: none

SEAM: subprocess.run
ATTRIBUTE-PATH: subprocess.run
BINDING-TIME: call-time
NORMALISATION: none
"""

# `[G22:16]` property-line-to-seam wrong-neighbour binding, pinned in spec
# v7 (same rule as `ADMITS`→`LEVEL`, now stated for `SEAM` too): property
# lines bind to the NEAREST PRECEDING `SEAM`, never a following one.
# `Seam.Alpha`'s three property lines sit directly above `Seam.Beta`'s
# declaration; `Seam.Beta` supplies only two lines of its own
# (ATTRIBUTE-PATH, BINDING-TIME — missing NORMALISATION under correct
# binding). A following-binding lint attributes `Seam.Alpha`'s block
# forward to `Seam.Beta` (the nearest SEAM *after* those lines), so
# `Seam.Beta` looks fully pinned (NORMALISATION "borrowed" from above) and
# `Seam.Alpha` looks entirely unpinned (its own lines never attributed back
# to it, and nothing follows it to inherit from) — the reverse of correct,
# preceding-binding output.
_SEAM_PROPERTY_WRONG_NEIGHBOUR_SPEC = """\
# Fixture Spec — property lines bind to the nearest preceding SEAM, not
# following

SEAM: Seam.Alpha
ATTRIBUTE-PATH: pathlib.Path.read_text
BINDING-TIME: call-time
NORMALISATION: none

SEAM: Seam.Beta
ATTRIBUTE-PATH: Seam.Beta
BINDING-TIME: call-time
"""

# Check-3 seam-name substring confusion (flagged as a gap in the round-7
# report, closed here) — the check-3 analogue of check 1's `audit`/
# `audit_field`. `Path.read` is a substring/prefix of `Path.read_text`
# and is fully conformant; `Path.read_text` is the longer name and is
# offending (missing NORMALISATION). A substring-matching lint might credit
# `Path.read_text`'s missing property from `Path.read`'s complete block,
# since `Path.read` textually matches as a substring of `Path.read_text`.
_SEAM_NAME_SUBSTRING_SPEC = """\
# Fixture Spec — one seam name is a substring of another's

SEAM: Path.read
ATTRIBUTE-PATH: pathlib.Path.read
BINDING-TIME: call-time
NORMALISATION: none

SEAM: Path.read_text
ATTRIBUTE-PATH: pathlib.Path.read_text
BINDING-TIME: call-time
"""

# `[G22:16]` seam-name case-sensitivity, distinct from the substring fixture
# above (whose two names differ in more than case) — flagged as a live-but-
# untested candidate in the round-9 report, closed here rather than left
# for a later round. `path.read_text` (lowercase) and `Path.Read_Text`
# (mixed-case) are the SAME text except for casing — pinned by `[G22:16]`
# as two DISTINCT seams, not one. `path.read_text` is fully conformant;
# `Path.Read_Text` is missing NORMALISATION. A case-folding lint that
# conflates the two (treating them as one seam) would see the union of
# both blocks' properties as complete — ATTRIBUTE-PATH and BINDING-TIME
# from `Path.Read_Text`'s own lines, NORMALISATION borrowed from
# `path.read_text`'s block — and wrongly clear `Path.Read_Text`.
_SEAM_NAME_CASE_MISMATCH_SPEC = """\
# Fixture Spec — two seam names differ only in casing

SEAM: path.read_text
ATTRIBUTE-PATH: pathlib.Path.read_text
BINDING-TIME: call-time
NORMALISATION: none

SEAM: Path.Read_Text
ATTRIBUTE-PATH: pathlib.Path.read_text
BINDING-TIME: call-time
"""

# `[G22:9]` bonus coverage (not required by the gate, flagged as such in the
# report): an ADMITS token outside the four reductions is itself pinned as a
# `missing_reductions` finding (§1.6), and ADMITS binds to the *nearest
# preceding* LEVEL.
_ADMITS_INVALID_TOKEN_SPEC = """\
# Fixture Spec — ADMITS names a token outside any/all/first/last

LEVEL: phases
ADMITS: any, bogus, all, first, last
NON-UNIFORMITY: phases — fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all, first, last
"""

# Conformant control: every level's row covers exactly the reductions its
# level ADMITS (default four, since neither level overrides ADMITS) — the
# `[G22:5]` fix. A round-1 control named only three reductions for
# `step_events` while requiring zero findings, which false-failed any GREEN
# reading check 2 the only computable way ("cover all four").
# `step_events`'s EXCLUDES tokens and the seam's property lines are each in
# a deliberately NON-canonical order (proactive, `[G22:13]`'s "fixed
# required order for unordered properties" candidate) — every other fixture
# in this file lists EXCLUDES tokens and property lines in the same
# canonical order every time, so a GREEN requiring that exact order (instead
# of treating both as unordered sets) would pass every other fixture and
# only be caught here, where this control's own emptiness assertion
# (`findings == []`) would fail for it.
_CONFORMANT_SPEC = """\
# Fixture Spec — fully conformant control

LEVEL: phases
NON-UNIFORMITY: phases — fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all, first, last

LEVEL: step_events
NON-UNIFORMITY: step_events — fixture set is non-uniform in both orderings
EXCLUDES: last, any, first, all

SEAM: Path.read_text
BINDING-TIME: call-time
NORMALISATION: none
ATTRIBUTE-PATH: pathlib.Path.read_text
"""

# Free-form prose with none of the §1.6 markers at all — exercises the
# spec's explicit "MUST NOT raise" contract point (§1.5) rather than the
# three checks, which the other fixtures already exercise.
_MALFORMED_SPEC = """\
This is not a spec-fixture document at all. It is free-form prose with no
LEVEL, NON-UNIFORMITY, EXCLUDES or SEAM markers anywhere in it, and a stray
mention of "excludes" and "level" in ordinary sentences, which are not
markers because they are not at line start in the pinned form.
"""

# §1.6 markers spelled lowercase — exercises the "recognised... case
# insensitive" clause directly. Kills a case-sensitive GREEN that only
# matches uppercase LEVEL:/SEAM:/etc, which would see this whole document as
# unstructured prose (no findings at all, and no seam recognised either).
_LOWERCASE_MARKERS_SPEC = """\
# Fixture Spec — grammar markers spelled lowercase (§1.6 case-insensitivity)

level: phases

seam: Path.read_text
attribute-path: pathlib.Path.read_text
binding-time: call-time
normalisation: none
"""


class TestQuantifierCompletenessLint:
    def test_ac_c5_flags_missing_non_uniformity_row_both_directions(self):
        """AC-C5(1). Kills a lint that only walks collection levels in one or
        two of the three directions §0.1 names (containers, element kinds,
        payload fields) — e.g. containers-and-elements only, the exact shape
        of bd#7's rounds 4-8 (which climbed the ladder upward) and round 9
        (which found a rung *below* it, at the payload-field/merged-set
        direction). `phases`, `step_events` and `Audit_Field` — one per
        direction — are all missing a non-uniformity row entirely and must
        all three be flagged independently — kills a lint that reports only
        one or two of the three. `Audit_Field`'s mixed-case name (self-sweep
        addition) also forces `Finding.subject` to preserve verbatim casing,
        not a case-folded form — see the module docstring's v6 note.

        `[G22:4]` coverage: the last two assertions cover `Finding`'s pinned
        type contract (§1.5) using a `Finding` already in hand from this
        fixture's result — a frozen dataclass. Kills a plain object,
        `NamedTuple`, or dict masquerading via `.kind`/`.subject` attribute
        access (not a dataclass), and a mutable dataclass (assignment does
        not raise).
        """
        import dataclasses

        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_MISSING_ROW_SPEC)

        assert any(
            f.kind == "missing_non_uniformity_row" and f.subject == "phases"
            for f in findings
        )
        assert any(
            f.kind == "missing_non_uniformity_row" and f.subject == "step_events"
            for f in findings
        )
        assert any(
            f.kind == "missing_non_uniformity_row" and f.subject == "Audit_Field"
            for f in findings
        )

        finding = findings[0]
        assert dataclasses.is_dataclass(finding)
        with pytest.raises(dataclasses.FrozenInstanceError):
            finding.kind = "mutated"

    def test_ac_c5_check1_row_binds_to_exact_named_level_not_globally_or_by_substring(self):
        """AC-C5(1). `[G22:15]`: kills two GREENs that survived every prior
        round — document-global presence (any `NON-UNIFORMITY:` line
        anywhere in the document ⇒ nothing missing, so `phases` is wrongly
        cleared by `workers`'s row) and substring operand matching (`audit`
        wrongly discharged by `audit_field`'s row, since "audit" is a
        substring of "audit_field"). `phases` and `audit` each have no row of
        their own and must still be flagged, even though the document
        contains rows for other levels and one of those other levels'
        names contains the un-named level's name as a substring.
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_ROW_LEVEL_BINDING_SPEC)

        assert any(
            f.kind == "missing_non_uniformity_row" and f.subject == "phases"
            for f in findings
        )
        assert any(
            f.kind == "missing_non_uniformity_row" and f.subject == "audit"
            for f in findings
        )
        assert not any(f.subject == "workers" for f in findings)
        assert not any(f.subject == "audit_field" for f in findings)

    def test_ac_c5_level_operand_matched_case_sensitively(self):
        """AC-C5(1). `[G22:16]`: `LEVEL: Audit_Case` and
        `NON-UNIFORMITY: audit_case — …` differ only in casing — pinned in
        spec v7 as NOT a match (`Finding.subject` is verbatim from the
        document, and a lint that case-folds operands to match them cannot
        also report verbatim). Kills a lint that lower-cases (or otherwise
        folds) operands before comparing `LEVEL` names against row
        `<level>` operands; `Audit_Case` must still be flagged as missing
        its own row.
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_LEVEL_CASE_MISMATCH_SPEC)

        assert any(
            f.kind == "missing_non_uniformity_row" and f.subject == "Audit_Case"
            for f in findings
        )

    def test_ac_c5_admits_binds_to_preceding_level_not_following(self):
        """AC-C5(2), bonus coverage (proactive, `[G22:13]`'s standing
        candidate list — "operand bound to the wrong neighbour"; not
        explicitly required this round). `alpha_level` explicitly `ADMITS:
        any, all` and its row covers exactly that set — conformant only
        under correct (preceding) binding; a wrong-neighbour lint would
        check it against the default instead and wrongly flag it.
        `beta_level` has no `ADMITS` of its own (default: all four) and its
        row covers only `any, all` — non-conformant only under correct
        binding; a wrong-neighbour lint would misattribute `alpha_level`'s
        `ADMITS` onto it and wrongly clear it. A wrong-neighbour-binding
        lint gets both backwards simultaneously.
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_ADMITS_WRONG_NEIGHBOUR_SPEC)

        assert not any(f.subject == "alpha_level" for f in findings)
        assert any(
            f.kind == "missing_reductions" and f.subject == "beta_level"
            for f in findings
        )

    def test_ac_c5_check2_row_binds_by_operand_not_proximity(self):
        """AC-C5(2). Flagged as a gap in the round-7 report, closed here:
        a present-but-short row textually adjacent to `beta_gate` but whose
        `<level>` operand names `alpha_gate`. Correct (operand-text)
        binding: `alpha_gate` gets the present-but-short row (missing
        `last`); `beta_gate` has no row of its own at all (check 1). A
        proximity-binding lint (attributing the row to the nearest
        preceding `LEVEL` instead of reading its operand) would get this
        backwards or miss it, since it was never forced by any prior check-2
        fixture (all of which had rows immediately following their own
        level).
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_CHECK2_WRONG_LEVEL_BINDING_SPEC)

        assert any(
            f.kind == "missing_reductions" and f.subject == "alpha_gate"
            for f in findings
        )
        assert any(
            f.kind == "missing_non_uniformity_row" and f.subject == "beta_gate"
            for f in findings
        )
        assert not any(
            f.kind == "missing_reductions" and f.subject == "beta_gate"
            for f in findings
        )
        assert not any(
            f.kind == "missing_non_uniformity_row" and f.subject == "alpha_gate"
            for f in findings
        )

    def test_ac_c5_flags_row_not_enumerating_reductions(self):
        """AC-C5(2). `[G22:5]`/`[G22:7]`/`[G22:9]`/`[G22:14]`: kills a lint
        implementing only the "no EXCLUDES line at all" half of check 2 —
        `phases` has no EXCLUDES line at all. The other four rows are each
        present-but-short against the ADMITS-absent **default** set (none of
        the five rows in this fixture carries an ADMITS line, so this
        specifically exercises the default path, not an explicit override),
        and each omits exactly ONE of the four reductions — `step_events`
        omits `all`, `retry_window` omits `any`, `audit_gate` omits `first`,
        `commit_step` omits `last` — so the default set's membership is
        load-bearing member by member (`[G22:14]`: round 3 only ever
        omitted `all`, so a GREEN whose default set silently dropped `any`,
        `first`, or `last` individually passed everything). Also kills a
        lint reporting only the first offending row (five rows here, all
        non-conformant) and one that only checks coverage when an explicit
        `ADMITS:` line is present. Also confirms check (1) does not
        spuriously fire, since every row here is present.
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_CHECK2_SPEC)

        assert any(
            f.kind == "missing_reductions" and f.subject == "phases"
            for f in findings
        )
        assert any(
            f.kind == "missing_reductions" and f.subject == "step_events"
            for f in findings
        )
        assert any(
            f.kind == "missing_reductions" and f.subject == "Retry_Window"
            for f in findings
        )
        assert any(
            f.kind == "missing_reductions" and f.subject == "audit_gate"
            for f in findings
        )
        assert any(
            f.kind == "missing_reductions" and f.subject == "commit_step"
            for f in findings
        )
        assert not any(f.kind == "missing_non_uniformity_row" for f in findings)

    def test_ac_c5_flags_row_not_enumerating_reductions_explicit_admits(self):
        """AC-C5(2). `[G22:8]` rule 2: the explicit-`ADMITS` present-but-short
        case, kept as its own fixture separate from the default-path test
        above. `payload_field` explicitly `ADMITS` only `any, all` (an
        unordered collection — `first`/`last` are meaningless for it) and its
        row covers that admitted set exactly (conformant, must NOT be
        flagged) — kills a lint that ignores an `ADMITS` override and demands
        all four regardless. `audit_step` and `retry_budget` both explicitly
        `ADMITS` all four and both cover only two (present-but-short against
        an explicit admitted set, not the default) — kills a lint that only
        checks coverage against the default, and (self-sweep finding, this
        round) `audit_step`/`retry_budget` bracket the conformant
        `payload_field` first-and-last, so a lint evaluating only the first
        or only the last row cannot pass — it would catch one offender and
        silently miss the other.
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_CHECK2_EXPLICIT_ADMITS_SPEC)

        assert not any(f.subject == "payload_field" for f in findings)
        assert any(
            f.kind == "missing_reductions" and f.subject == "audit_step"
            for f in findings
        )
        assert any(
            f.kind == "missing_reductions" and f.subject == "retry_budget"
            for f in findings
        )

    def test_ac_c5_flags_admits_token_outside_the_four_reductions(self):
        """AC-C5(2), bonus coverage flagged in the RED report (not explicitly
        requested by the gate, but now-pinned spec text §1.6): an `ADMITS`
        token outside `any`/`all`/`first`/`last` is itself a `missing_reductions`
        finding, and `ADMITS` binds to the nearest preceding `LEVEL` — kills a
        lint that silently ignores an unrecognised token instead of flagging
        it, or that mis-attributes an `ADMITS` line to the wrong level.
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_ADMITS_INVALID_TOKEN_SPEC)

        assert any(
            f.kind == "missing_reductions" and f.subject == "phases"
            for f in findings
        )

    def test_ac_c5_flags_seam_missing_attribute_path(self):
        """AC-C5(3). `[G22:7]`/`[G22:11]`: one of three fixtures, each
        omitting exactly one of the three property lines, so each is
        independently load-bearing — a lint testing only ATTRIBUTE-PATH
        presence would pass this fixture's `Path.read_text` row (conformant)
        but must still flag `importlib.metadata.version` (ATTRIBUTE-PATH
        missing here), and must not flag the conformant seam (non-uniform
        within the check: two seams, one conformant, one not). The
        offending seam is placed **first** here (round 2 put it last in all
        three fixtures, so a lint evaluating only the final `SEAM:` block —
        a loop-variable escape — passed everything; this fixture kills that).
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_SEAM_MISSING_ATTRIBUTE_PATH_SPEC)

        assert any(
            f.kind == "seam_not_pinned" and f.subject == "importlib.metadata.version"
            for f in findings
        )
        assert not any(f.subject == "Path.read_text" for f in findings)

    def test_ac_c5_flags_seam_missing_binding_time(self):
        """AC-C5(3). `[G22:7]`/`[G22:11]`: kills a lint that tests only
        ATTRIBUTE-PATH presence — the offender here pins ATTRIBUTE-PATH
        correctly (as `mkdtemp` and `importlib.metadata.version` both
        historically did, per §0.2) and omits only BINDING-TIME, which such
        a lint would silently pass. The offending seam is placed **last**
        here — the other position from the ATTRIBUTE-PATH fixture above, so
        a lint evaluating only the *first* `SEAM:` block is caught by this
        one instead. The offender's name (`MixedCase.Attribute`) is
        deliberately mixed-case, not the usual all-lowercase
        `importlib.metadata.version` used elsewhere — a GREEN that
        case-folds lines before parsing (or normalises `subject`'s casing)
        fails the exact-match assertion below, where an all-lowercase
        offender would have let it through undetected; the negative guard
        also now discriminates, since a case-folding GREEN could never
        reproduce the mixed-case `Path.read_text` literal either.
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_SEAM_MISSING_BINDING_TIME_SPEC)

        assert any(
            f.kind == "seam_not_pinned" and f.subject == "MixedCase.Attribute"
            for f in findings
        )
        assert not any(f.subject == "Path.read_text" for f in findings)

    def test_ac_c5_flags_seam_missing_normalisation(self):
        """AC-C5(3). `[G22:7]`/`[G22:11]`: kills a lint that tests only
        ATTRIBUTE-PATH (or ATTRIBUTE-PATH + BINDING-TIME) presence —
        `importlib.metadata.version` here pins both correctly (mirroring
        `Path.read_text`'s historical gap, which pinned the seam and
        BINDING-TIME but not NORMALISATION) and omits only NORMALISATION.
        The offending seam is placed **first**, with a *second* conformant
        seam (`subprocess.run`) trailing it — mirroring check 2's
        offender-then-conformant shape, so neither a first-only nor a
        last-only evaluator passes this fixture on its own.
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_SEAM_MISSING_NORMALISATION_SPEC)

        assert any(
            f.kind == "seam_not_pinned" and f.subject == "importlib.metadata.version"
            for f in findings
        )
        assert not any(f.subject == "Path.read_text" for f in findings)
        assert not any(f.subject == "subprocess.run" for f in findings)

    def test_ac_c5_seam_property_line_binds_to_preceding_seam_not_following(self):
        """AC-C5(3). `[G22:16]`: property lines bind to the NEAREST
        PRECEDING `SEAM`, never a following one (spec v7, same rule as
        `ADMITS`→`LEVEL`). `Seam.Alpha`'s three property lines sit directly
        above `Seam.Beta`'s declaration; `Seam.Beta` supplies only two lines
        of its own. A following-binding lint attributes `Seam.Alpha`'s block
        forward onto `Seam.Beta` (making it look fully pinned) and leaves
        `Seam.Alpha` with nothing (making it look entirely unpinned) — the
        reverse of correct output. Kills that lint on both halves at once.
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_SEAM_PROPERTY_WRONG_NEIGHBOUR_SPEC)

        assert not any(f.subject == "Seam.Alpha" for f in findings)
        assert any(
            f.kind == "seam_not_pinned" and f.subject == "Seam.Beta"
            for f in findings
        )

    def test_ac_c5_flags_seam_name_substring_confusion(self):
        """AC-C5(3). Flagged as a gap in the round-7 report, closed here —
        the check-3 analogue of check 1's `audit`/`audit_field`. `Path.read`
        is a substring/prefix of `Path.read_text` and is fully conformant;
        `Path.read_text` is the longer name and is missing NORMALISATION. A
        substring-matching lint (crediting `Path.read_text`'s properties
        from any seam whose name it contains as a substring) would wrongly
        treat `Path.read_text` as fully pinned via `Path.read`'s complete
        block.
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_SEAM_NAME_SUBSTRING_SPEC)

        assert any(
            f.kind == "seam_not_pinned" and f.subject == "Path.read_text"
            for f in findings
        )
        assert not any(f.subject == "Path.read" for f in findings)

    def test_ac_c5_flags_seam_name_case_mismatch_as_distinct_seams(self):
        """AC-C5(3). `[G22:16]`: flagged as a live-but-untested candidate in
        the round-9 report (distinct from `_SEAM_NAME_SUBSTRING_SPEC`, whose
        two names differ in more than case), closed here. `path.read_text`
        and `Path.Read_Text` are the same text except for casing — pinned
        as two DISTINCT seams, not one, for the same reason a level and a
        row differing only in casing are distinct (`Finding.subject` is
        verbatim, and a lint that case-folds to conflate them cannot also
        report each verbatim). `path.read_text` is fully conformant;
        `Path.Read_Text` is missing NORMALISATION. A case-folding lint that
        treats them as one seam sees the union of both blocks' properties as
        complete and wrongly clears `Path.Read_Text`.
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_SEAM_NAME_CASE_MISMATCH_SPEC)

        assert any(
            f.kind == "seam_not_pinned" and f.subject == "Path.Read_Text"
            for f in findings
        )
        assert not any(f.subject == "path.read_text" for f in findings)

    def test_ac_c5_conformant_control_passes(self):
        """AC-C5 control. A document with every level's non-uniformity row
        (both directions) fully covering its ADMITS-ed reductions (`[G22:5]`
        — both levels here cover all four, the default admitted set), and a
        seam with its full interception property, must produce zero
        findings. Kills a lint that over-fires on well-formed input (e.g. a
        keyword sweep that mis-scores conformant documents — the failure
        mode §3's scope-limit note calls out from bd#7's own history: 11 of
        13 ACs mis-scored). Also kills a lint requiring EXCLUDES tokens or
        seam property lines in a fixed canonical order — `step_events`'s
        EXCLUDES and the seam's property lines are both listed out of the
        order every other fixture in this file happens to use (proactive,
        self-sweep addition).
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_CONFORMANT_SPEC)

        assert findings == []

    def test_ac_c5_does_not_raise_on_malformed_document(self):
        """§1.5's explicit contract point: `lint_quantifier_completeness`
        MUST NOT raise on a non-conformant (here: entirely unstructured)
        document — raising would make "conformant" and "malformed"
        indistinguishable to the build step consuming the lint's output.
        Kills an implementation that raises KeyError/IndexError/ValueError
        parsing a document with none of the §1.6 markers instead of
        returning a (here, empty) findings list.
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_MALFORMED_SPEC)

        assert findings == []

    def test_ac_c5_does_not_raise_on_empty_input(self):
        """§1.6/gate item 5: kills a GREEN doing an `i+1` lookahead over
        `text.split("\\n")` (or similar) that raises `IndexError` indexing
        into an empty sequence, instead of returning an empty findings list
        (nothing quantified yet). Paired with the trailing-bare-`LEVEL:`
        test below, which covers the `.splitlines()` variant instead — that
        one starts nonempty (`"".splitlines() == []`, so an empty string
        alone cannot force a `.splitlines()`-based lookahead to run at all).
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness("")

        assert findings == []

    def test_ac_c5_does_not_raise_on_trailing_bare_level_line(self):
        """§1.6/gate item 5: kills the same `i+1`-lookahead GREEN on a
        document whose *last* line is a bare `LEVEL:` with nothing after it
        — the lookahead has no next line to read. Must still report the
        missing-row finding for that level rather than raising.
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness("LEVEL: phases")

        assert any(
            f.kind == "missing_non_uniformity_row" and f.subject == "phases"
            for f in findings
        )

    def test_ac_c5_recognises_markers_case_insensitively(self):
        """§1.6: markers are recognised at line start, case-insensitive.
        Kills a case-sensitive GREEN that only matches uppercase
        `LEVEL:`/`SEAM:`/etc — it would see this entire fixture as
        unstructured prose: no missing-row finding for `phases` (from the
        lowercase `level:` line) and no recognition of `seam:` as a seam
        declaration at all.
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_LOWERCASE_MARKERS_SPEC)

        assert any(
            f.kind == "missing_non_uniformity_row" and f.subject == "phases"
            for f in findings
        )
        assert not any(f.subject == "Path.read_text" for f in findings)

    def test_ac_c5_lint_is_idempotent_on_finding_producing_document(self):
        """Gate item 5 (round 4 correction): kills a GREEN that accumulates
        findings into module-level state across calls (e.g. an
        `_ALL_FINDINGS.append(...)` instead of building a fresh list per
        call). The round-2 version called the *conformant* document twice
        and asserted both `[]` (vacuous: a conformant document appends zero
        findings either way, so it only discriminated via cross-test
        pollution). The round-3 fix called a finding-producing document
        twice and compared `second == first` — but a GREEN whose
        accumulator *returns the same list object it appended to* (simpler,
        and likelier, than returning a defensive copy) makes `first` and
        `second` literally the same object: any mutation from the second
        call is visible through `first` too, so `second == first` degenerates
        to `x == x` and cannot fail. Fixed by snapshotting `first` into a
        plain list *before* the second call, and asserting object identity
        directly: an accumulating GREEN either returns the same object twice
        (`second is first` — caught by the identity assertion) or returns a
        growing copy each time (caught by the snapshot-equality assertion).
        """
        from conformance.quant_lint import lint_quantifier_completeness

        first = lint_quantifier_completeness(_MISSING_ROW_SPEC)
        assert first != []
        first_snapshot = list(first)

        second = lint_quantifier_completeness(_MISSING_ROW_SPEC)

        assert second == first_snapshot
        assert second is not first


# =========================================================================
# PART 2 -- bd#24's additions. Every fixture and every assertion below is
# traceable to a candidate ID in QUANT_LINT_SPEC.md §3; an addition that
# names no candidate would itself be the finding (spec §0.9, direction 2).
# =========================================================================

# ── Round-4 finding B2, and the fixture bd#22 lost twice ─────────────────
# `_SEAM_NOT_PINNED_SPEC` existed in bd#22 round 1 (e3700b8) carrying a
# `SEAM:` with ZERO property lines, and commit 9947142 DELETED it when the
# [G22:7] fix replaced it with three one-property-missing fixtures. Round 4
# rejected on the gap: a lint that registers a seam only upon encountering a
# property line never sees a bare one, so `SEAM: mkdtemp` with nothing
# pinned ships conformant -- the literal founding case of §0.2. Restored
# here under its original name (spec C3-1).
#
# The bare seams are placed FIRST and LAST, bracketing a fully conformant
# one (C3-5/C3-6), and the trailing bare seam is also the document's final
# line (F-4: a `.splitlines()` lookahead has no next line to read). The
# last one's name is deliberately mixed-case (C3-13): the other two check-3
# offenders that carry mixed-case names are in carried fixtures, and a
# case-normalising GREEN must fail here too, not only there.
_SEAM_NOT_PINNED_SPEC = """\
# Fixture Spec — seam named, interception property not pinned at all

SEAM: tempfile.mkdtemp

SEAM: Path.read_text
ATTRIBUTE-PATH: pathlib.Path.read_text
BINDING-TIME: call-time
NORMALISATION: none

SEAM: MixedCase.BareSeam
"""

# ── Round-4 finding B1: membership is not cardinality (spec C2-6) ────────
# Every prior check-2 offending row was short BY COUNT exactly when it was
# short BY MEMBERSHIP, so `if len(excludes) < len(admits)` passed every one
# of them and `EXCLUDES: any, all, first, frist` shipped conformant. NO row
# here carries an ADMITS line, so all three are checked against the
# four-member DEFAULT ([G22:9]: the default path is the one that ships), and
# both offenders carry exactly FOUR tokens:
#   `alpha_count` -- one token invalid (`frist`, a typo for `first`);
#   `gamma_count` -- one token duplicated (`all` twice).
# Both cover only {any, all, first} and are missing `last`; a cardinality
# comparison sees 4 >= 4 and clears both. The conformant row sits BETWEEN
# them, so first-only and last-only evaluators die here too (C2-5).
_CHECK2_CARDINALITY_SPEC = """\
# Fixture Spec — check 2: EXCLUDES rows whose cardinality matches the
# default admitted set while their membership does not

LEVEL: alpha_count
NON-UNIFORMITY: alpha_count — fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all, first, frist

LEVEL: beta_count
NON-UNIFORMITY: beta_count — fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all, first, last

LEVEL: gamma_count
NON-UNIFORMITY: gamma_count — fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all, first, all
"""

# ── [G24:1] reduction-token vocabulary and casing (spec C2-7, C2-8) ──────
# Left open by bd#22 ([G22:16] pinned LEVEL names, row operands and SEAM
# names verbatim and said nothing about reduction tokens), decided in spec
# §2.4: the vocabulary is exactly {any, all, first, last}, lowercase, in
# BOTH `ADMITS` and `EXCLUDES`; anything else is unrecognised and is itself
# a `missing_reductions` finding on the level.
#   `upper_excludes` -- four tokens, all unrecognised: covers nothing.
#   `lower_control`  -- the conformant control, second of four, so neither a
#                       first-only nor a last-only evaluator passes (C2-5).
#   `Upper_Admits`   -- ADMITS tokens uppercase; the admitted set is
#                       uncomputable even though its EXCLUDES is complete.
#                       Mixed-case level name also forces verbatim `subject`.
#   `bogus_extra`    -- EXCLUDES covers all four AND carries a fifth,
#                       unrecognised token. This row is the one that
#                       discriminates the symmetric rule from the weaker
#                       "ignore unrecognised tokens, let under-coverage
#                       catch them" reading, under which it ships
#                       conformant (C2-8).
_REDUCTION_TOKEN_VOCABULARY_SPEC = """\
# Fixture Spec — reduction tokens outside the lowercase four

LEVEL: upper_excludes
NON-UNIFORMITY: upper_excludes — fixture set has >=2 members, one violating, plus control
EXCLUDES: ANY, ALL, FIRST, LAST

LEVEL: lower_control
NON-UNIFORMITY: lower_control — fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all, first, last

LEVEL: Upper_Admits
ADMITS: Any, All
NON-UNIFORMITY: Upper_Admits — fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all, first, last

LEVEL: bogus_extra
NON-UNIFORMITY: bogus_extra — fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all, first, last, sometimes
"""

# ── [G24:2] EXCLUDES binds to the preceding ROW, not the preceding LEVEL ──
# The other semantics bd#22 left open. Every carried check-2 fixture places
# each `EXCLUDES` immediately under a row that immediately follows its own
# `LEVEL`, so row-binding and level-binding are INDISTINGUISHABLE across the
# whole inherited set. Here they produce exactly INVERTED output (C2-10):
#   correct (nearest preceding NON-UNIFORMITY row, level reached through the
#   row's own operand): EXCLUDES#1 -> the `alpha_bind` row, complete, so
#   `alpha_bind` is conformant; EXCLUDES#2 -> the `beta_bind` row, missing
#   `last`, so `beta_bind` is flagged.
#   nearest-preceding-LEVEL binding: both lines land on `beta_bind`, whose
#   coverage becomes complete, while the `alpha_bind` row is left with no
#   EXCLUDES at all and is flagged -- the reverse on both levels at once.
# A lint binding EXCLUDES to the FOLLOWING row inverts identically (C2-11),
# so one fixture kills both wrong-neighbour directions.
_EXCLUDES_ROW_BINDING_SPEC = """\
# Fixture Spec — two rows under one LEVEL block, each with its own EXCLUDES

LEVEL: alpha_bind

LEVEL: beta_bind
NON-UNIFORMITY: alpha_bind — fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all, first, last
NON-UNIFORMITY: beta_bind — fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all, first
"""

# ── Substring operand match, direction B (spec C1-6) ─────────────────────
# The carried `_ROW_LEVEL_BINDING_SPEC` kills direction A only: a level name
# contained in a row operand (`audit` in `audit_field`). The mirror image --
# a row operand contained in a LEVEL name -- was never forced, and [G22:13]'s
# candidate list names substring matching in BOTH directions. Here the only
# row names `audit`, and `audit_field` and `audit_gate` both CONTAIN it, so
# a lint testing `row_operand in level_name` wrongly clears both. They
# bracket the conformant level (C1-2/C1-3).
_ROW_LEVEL_SUBSTRING_REVERSE_SPEC = """\
# Fixture Spec — check 1: a row operand that is a substring of two other
# levels' names

LEVEL: audit_field

LEVEL: audit
NON-UNIFORMITY: audit — fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all, first, last

LEVEL: audit_gate
"""

# ── Seam-name substring, direction B (spec C3-11) ────────────────────────
# The carried `_SEAM_NAME_SUBSTRING_SPEC` kills direction A only (the longer
# name credited from the shorter one's complete block). Here the OFFENDERS
# are the shorter names -- `Path.read` (missing NORMALISATION) and
# `subprocess.ru` (missing NORMALISATION) -- and the complete blocks belong
# to the longer `Path.read_text` and `subprocess.run` that CONTAIN them, so
# a lint crediting a seam from any seam whose name contains it wrongly
# clears both offenders. One offender is first, one last (C3-5/C3-6).
_SEAM_NAME_SUBSTRING_REVERSE_SPEC = """\
# Fixture Spec — check 3: offending seam names are substrings of conformant
# ones

SEAM: Path.read
ATTRIBUTE-PATH: pathlib.Path.read
BINDING-TIME: call-time

SEAM: Path.read_text
ATTRIBUTE-PATH: pathlib.Path.read_text
BINDING-TIME: call-time
NORMALISATION: none

SEAM: subprocess.run
ATTRIBUTE-PATH: subprocess.run
BINDING-TIME: call-time
NORMALISATION: none

SEAM: subprocess.ru
ATTRIBUTE-PATH: subprocess.ru
BINDING-TIME: call-time
"""

# ── Check 3's analogue of B1: property membership vs cardinality (C3-8) ──
# Applying K9 uniformly: if check 2 could compare COUNTS instead of
# identities, so could check 3. Every carried check-3 offender is short by
# count exactly when it is short by membership (two lines against three), so
# `len(property_lines) >= 3` passes all of them. Both offenders here carry
# THREE property lines of which two repeat one marker ([G24:4]: a repeated
# line adds no coverage), so the count is satisfied and the set is not:
#   `Duplicate.First` -- ATTRIBUTE-PATH, BINDING-TIME x2, no NORMALISATION;
#   `Duplicate.Last`  -- ATTRIBUTE-PATH, NORMALISATION x2, no BINDING-TIME.
# They bracket a conformant seam (C3-5/C3-6), and their mixed-case names
# also carry the verbatim-`subject` obligation (C3-13).
_SEAM_PROPERTY_CARDINALITY_SPEC = """\
# Fixture Spec — check 3: three property lines, two of them the same marker

SEAM: Duplicate.First
ATTRIBUTE-PATH: pathlib.Path.read_text
BINDING-TIME: call-time
BINDING-TIME: call-time

SEAM: Path.read_text
ATTRIBUTE-PATH: pathlib.Path.read_text
BINDING-TIME: call-time
NORMALISATION: none

SEAM: Duplicate.Last
ATTRIBUTE-PATH: subprocess.run
NORMALISATION: none
NORMALISATION: none
"""


class TestQuantifierCompletenessLintBd24:
    """bd#24's additions. Kept in a separate class from the carried block so
    that the round-9 artifact stays byte-identical and the coverage diff in
    spec §5 is checkable by mechanical containment rather than by reading.
    """

    def test_ac_c5_check3_flags_bare_seam_with_zero_property_lines(self):
        """AC-C5(3). Round-4 finding B2, spec C3-1 / C3-5 / C3-6 / C3-9 /
        C3-13. Kills a lint that registers a seam only when it encounters a
        property line: the three carried one-property-missing fixtures never
        show it a `SEAM:` with ZERO property lines, so `SEAM: mkdtemp` with
        nothing pinned -- §0.2's founding case, and the exact fixture bd#22
        round 3 deleted -- ships conformant. Both bare seams must be
        flagged: one is the document's FIRST seam (killing a last-only
        evaluator) and one its LAST line (killing a first-only evaluator,
        and a lookahead that assumes a following line, F-4).

        The final assertion pins `[G24:3]`: the bare seam is missing all
        THREE properties and must still yield exactly ONE finding, killing a
        lint that emits one finding per missing property -- under which
        `len(findings)` is not a defined quantity and the conformant
        control's `findings == []` becomes the only assertable shape in the
        whole AC.
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_SEAM_NOT_PINNED_SPEC)

        assert any(
            f.kind == "seam_not_pinned" and f.subject == "tempfile.mkdtemp"
            for f in findings
        )
        assert any(
            f.kind == "seam_not_pinned" and f.subject == "MixedCase.BareSeam"
            for f in findings
        )
        assert not any(f.subject == "Path.read_text" for f in findings)
        assert (
            len([f for f in findings if f.subject == "tempfile.mkdtemp"]) == 1
        )

    def test_ac_c5_check3_bare_seam_as_the_entire_document(self):
        """AC-C5(3). Spec C3-1 / F-4, the degenerate form of the same case:
        a document whose ONLY line is a bare `SEAM:`. Kills a lint that
        needs a following line to close a seam block (an `i+1` lookahead, or
        a flush-on-next-SEAM loop that never flushes the final block) --
        which returns an empty list here instead of the finding, or raises
        `IndexError`. Distinct from the fixture above, where the bare seam
        at least has a preceding conformant block to have opened the loop.
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness("SEAM: tempfile.mkdtemp")

        assert any(
            f.kind == "seam_not_pinned" and f.subject == "tempfile.mkdtemp"
            for f in findings
        )

    def test_ac_c5_check2_membership_is_not_cardinality(self):
        """AC-C5(2). Round-4 finding B1, spec C2-6 / C2-9 / C2-5. Kills
        `if len(excludes) < len(admits)`: every previously-existing offending
        row was short by COUNT exactly when it was short by MEMBERSHIP, so
        that comparison passed all of them. Both offenders here carry
        exactly FOUR tokens against the four-member default admitted set
        (neither row carries an `ADMITS` line) -- `alpha_count` via an
        invalid token (`frist`), `gamma_count` via a duplicated one (`all`)
        -- and both are missing `last`. A cardinality comparison clears
        both; a membership comparison flags both. The conformant row sits
        between them, so first-only and last-only evaluators die here too.

        The final assertion pins `[G24:3]` on check 2: one finding per
        (kind, subject), not one per missing reduction.
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_CHECK2_CARDINALITY_SPEC)

        assert any(
            f.kind == "missing_reductions" and f.subject == "alpha_count"
            for f in findings
        )
        assert any(
            f.kind == "missing_reductions" and f.subject == "gamma_count"
            for f in findings
        )
        assert not any(f.subject == "beta_count" for f in findings)
        assert len([f for f in findings if f.subject == "alpha_count"]) == 1

    def test_ac_c5_check2_reduction_tokens_are_lowercase_and_verbatim(self):
        """AC-C5(2). Spec C2-7 / C2-8 / C2-5, closing `[G24:1]` -- one of the
        two semantics bd#22 left open. The reduction vocabulary is exactly
        {any, all, first, last}, lowercase, in both `ADMITS` and `EXCLUDES`.

        `upper_excludes` carries four UPPERCASE tokens: a case-folding lint
        reads them as complete coverage and clears the row, where the pinned
        semantics make it cover nothing. `Upper_Admits` carries uppercase
        `ADMITS` tokens, so its admitted set is uncomputable even though its
        own `EXCLUDES` is complete -- the same shape the carried
        `_ADMITS_INVALID_TOKEN_SPEC` asserts for a non-case typo. And
        `bogus_extra` covers all four AND carries a fifth unrecognised token:
        it is the only row in the file that discriminates the pinned
        symmetric rule from the weaker reading (ignore unrecognised tokens
        and let under-coverage catch them), under which it ships conformant.
        `lower_control` is second of four, so neither a first-only nor a
        last-only evaluator passes.
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_REDUCTION_TOKEN_VOCABULARY_SPEC)

        assert any(
            f.kind == "missing_reductions" and f.subject == "upper_excludes"
            for f in findings
        )
        assert any(
            f.kind == "missing_reductions" and f.subject == "Upper_Admits"
            for f in findings
        )
        assert any(
            f.kind == "missing_reductions" and f.subject == "bogus_extra"
            for f in findings
        )
        assert not any(f.subject == "lower_control" for f in findings)

    def test_ac_c5_check2_excludes_binds_to_preceding_row_not_preceding_level(self):
        """AC-C5(2). Spec C2-10 / C2-11, closing `[G24:2]` -- the other
        semantics bd#22 left open. Every carried check-2 fixture places each
        `EXCLUDES` under a row that itself immediately follows its own
        `LEVEL`, so row-binding and level-binding are indistinguishable
        across the entire inherited set.

        Here they invert. Correct binding (nearest preceding
        `NON-UNIFORMITY` row, level reached through that row's own operand):
        the first `EXCLUDES` completes `alpha_bind`'s row, so `alpha_bind` is
        conformant, and the second leaves `beta_bind` missing `last`.
        Nearest-preceding-`LEVEL` binding lands both lines on `beta_bind`,
        clearing it, and leaves `alpha_bind`'s row with no `EXCLUDES` at all,
        flagging it -- the reverse on both levels simultaneously, so each of
        the two assertions below independently catches it. A lint binding
        `EXCLUDES` to the FOLLOWING row inverts identically.
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_EXCLUDES_ROW_BINDING_SPEC)

        assert any(
            f.kind == "missing_reductions" and f.subject == "beta_bind"
            for f in findings
        )
        assert not any(f.subject == "alpha_bind" for f in findings)

    def test_ac_c5_check1_level_not_discharged_by_substring_row_operand(self):
        """AC-C5(1). Spec C1-6 / C1-2 / C1-3. `[G22:13]`'s candidate list
        names substring operand matching in BOTH directions; the carried
        `_ROW_LEVEL_BINDING_SPEC` kills only direction A (a level name
        contained in a row operand: `audit` in `audit_field`). This is the
        mirror image: the document's one row names `audit`, and both
        `audit_field` and `audit_gate` CONTAIN that operand, so a lint
        testing `row_operand in level_name` wrongly credits that single row
        to both of them. Both must still be flagged, and `audit` -- which
        does have its own row -- must not be. The two offenders bracket the
        conformant level, so first-only and last-only evaluators die too.
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_ROW_LEVEL_SUBSTRING_REVERSE_SPEC)

        assert any(
            f.kind == "missing_non_uniformity_row" and f.subject == "audit_field"
            for f in findings
        )
        assert any(
            f.kind == "missing_non_uniformity_row" and f.subject == "audit_gate"
            for f in findings
        )
        assert not any(f.subject == "audit" for f in findings)

    def test_ac_c5_check3_seam_not_credited_from_a_longer_seam_name(self):
        """AC-C5(3). Spec C3-11 / C3-5 / C3-6, the check-3 mirror of the test
        above. The carried `_SEAM_NAME_SUBSTRING_SPEC` kills direction A (the
        longer name credited from the shorter one's complete block); here the
        OFFENDERS are the shorter names and the complete blocks belong to the
        longer names that contain them, so a lint crediting a seam from any
        seam whose name it is a substring of wrongly clears both. `Path.read`
        is first and `subprocess.ru` last, so neither a first-only nor a
        last-only evaluator passes either.
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_SEAM_NAME_SUBSTRING_REVERSE_SPEC)

        assert any(
            f.kind == "seam_not_pinned" and f.subject == "Path.read"
            for f in findings
        )
        assert any(
            f.kind == "seam_not_pinned" and f.subject == "subprocess.ru"
            for f in findings
        )
        assert not any(f.subject == "Path.read_text" for f in findings)
        assert not any(f.subject == "subprocess.run" for f in findings)

    def test_ac_c5_check3_property_membership_is_not_cardinality(self):
        """AC-C5(3). Spec C3-8, applying B1's own candidate (K9, membership
        independent of cardinality) uniformly across the checks rather than
        only where the gate happened to find it. Every carried check-3
        offender is short by count exactly when it is short by membership --
        two property lines against three -- so `len(property_lines) >= 3`
        passes all of them, the check-3 twin of the `len(excludes) <
        len(admits)` defect B1 named for check 2.

        Both offenders here carry THREE property lines of which two repeat
        one marker (`[G24:4]`: a repeated line adds no coverage, and is not
        itself a finding), so the count is satisfied and the set is not:
        `Duplicate.First` has no NORMALISATION, `Duplicate.Last` no
        BINDING-TIME. Different missing markers on purpose, so a lint
        checking only two of the three properties cannot pass either. They
        bracket a conformant seam, and their mixed-case names carry the
        verbatim-`subject` obligation.
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_SEAM_PROPERTY_CARDINALITY_SPEC)

        assert any(
            f.kind == "seam_not_pinned" and f.subject == "Duplicate.First"
            for f in findings
        )
        assert any(
            f.kind == "seam_not_pinned" and f.subject == "Duplicate.Last"
            for f in findings
        )
        assert not any(f.subject == "Path.read_text" for f in findings)

# ── Orphaned marker lines: no anchor at all (spec F-8, and C2-9's mirror)
# Found by this round's own self-sweep, not by the gate, and pinned in spec
# §2.5 rather than invented here. P3 and `[G24:2]` say property lines bind to
# the nearest PRECEDING `SEAM` and `EXCLUDES` to the nearest PRECEDING
# `NON-UNIFORMITY` row -- and every fixture in the file, carried or new,
# exercises those rules only where an anchor EXISTS. "Nearest preceding
# anchor, when there is none" is the same shape as B2 ("a SEAM with zero
# property lines" against three fixtures that all had one or two): the
# degenerate case of a rule, invisible to fixtures written from the rule's
# own wording. It gets its own fixture for exactly that reason.
#
# Here a `NORMALISATION:` line and an `EXCLUDES:` line both precede every
# anchor in the document. Correct behaviour (spec §2.4/§2.5): both bind to
# nothing, contribute nothing, and are not themselves findings.
#
# CORRECTED after gate round 1 (spec §5, MAJOR-3). This fixture was first
# written claiming to kill FORWARD-crediting, and it does not: crediting the
# orphaned `NORMALISATION:` forward still leaves `Orphan.Seam` without
# ATTRIBUTE-PATH and BINDING-TIME, so check 3 fires and the assertion passes
# anyway; and crediting the orphaned `EXCLUDES:` forward adds coverage to a
# level that still has no ROW, so check 1 fires and that assertion passes
# anyway -- checks 1 and 2 are defined over different objects. What this
# fixture does kill is F-8 (a parser dereferencing a "current anchor" that
# was never assigned raises instead of returning, failing all three
# assertions) and the manufactures-a-finding candidate (assertion 3: there
# is no row anywhere in this document, so any `missing_reductions` here can
# only have come from the unanchored line). Forward-crediting is killed by
# `_FORWARD_CREDIT_SPEC` below, where the credited marker COMPLETES its
# anchor and therefore changes the verdict.
_ORPHAN_MARKER_LINES_SPEC = """\
# Fixture Spec — property and EXCLUDES lines that precede every anchor

NORMALISATION: none
EXCLUDES: any, all, first, last

LEVEL: orphan_level

SEAM: Orphan.Seam
"""


class TestQuantifierCompletenessLintBd24Orphans:
    """The base case of the two binding rules, in its own class because it is
    the v2 addition (spec §2.5 `[G24:5]`) and its provenance -- this round's
    self-sweep rather than a gate finding -- should stay legible in the diff.
    """

    def test_ac_c5_unanchored_marker_lines_bind_to_nothing(self):
        """AC-C5(1) and AC-C5(3). Spec F-8, pinning
        `[G24:5]`. P3 and `[G24:2]` bind property lines and `EXCLUDES` to the
        nearest PRECEDING anchor; every other fixture in this file exercises
        those rules only where an anchor exists, so "no anchor at all" is the
        same unexercised base case that cost bd#22 round 4 on check 3 --
        three fixtures each missing one property, none with zero.

        CORRECTED after gate round 1 (MAJOR-3): this docstring previously
        claimed the fixture kills FORWARD-crediting. It does not. Crediting
        the orphaned `NORMALISATION:` forward leaves `Orphan.Seam` still
        missing ATTRIBUTE-PATH and BINDING-TIME, so check 3 fires either
        way; crediting the orphaned `EXCLUDES:` forward adds coverage to a
        level that still has no ROW, and check 1 is defined over rows, so it
        fires either way. Both assertions passed for the very GREEN they
        were written to kill. Forward-crediting is killed by
        `_FORWARD_CREDIT_SPEC` instead, where the credited marker COMPLETES
        its anchor.

        What this fixture does kill: F-8 -- a parser that dereferences a
        current-anchor variable it never assigned raises instead of
        returning, failing all three assertions -- and, in the third
        assertion, a lint that manufactures a finding out of an unanchored
        line. There is no `kind` for one (`[G24:5]`), and
        `missing_reductions` is the only kind it could be spelled as, since
        no row exists in this document at all.

        The fourth assertion closes gate round-3's MAJOR (spec C3-20), and it
        is the third kill this one fixture has been credited with and could
        not make. The third assertion is sound only for the `EXCLUDES` half:
        the document contains NO row, so any `missing_reductions` must have
        come from the orphan. That reasoning does not transfer to the
        property-line half, because the document DOES contain a seam, so a
        spurious `seam_not_pinned` is indistinguishable from the expected one
        under an `any(...)` assertion. The surviving implementation is not
        contrived -- it is the natural one, a dict keyed by the current anchor
        with a sentinel default: `seams.setdefault(cur_seam or "<unnamed>",
        set()).add(marker)` collects the orphaned `NORMALISATION:` under
        `<unnamed>`, finds it short of three properties, and reports a
        finding about a seam that does not exist. Verified: that mutant
        passes 40/40 without this assertion, and fails with it.
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_ORPHAN_MARKER_LINES_SPEC)

        assert any(
            f.kind == "missing_non_uniformity_row" and f.subject == "orphan_level"
            for f in findings
        )
        assert any(
            f.kind == "seam_not_pinned" and f.subject == "Orphan.Seam"
            for f in findings
        )
        assert not any(f.kind == "missing_reductions" for f in findings)
        assert not any(
            f.kind == "seam_not_pinned" and f.subject != "Orphan.Seam"
            for f in findings
        )


# =========================================================================
# PART 3 -- closures for gate round 1 (spec §5). Purely additive: no fixture
# in Parts 1 or 2 is edited, so the containment check against d39371f stays
# 17/17 unmodified and the round-1 candidate set stays killed by the same
# fixtures. The gate proposed editing two CARRIED fixtures for MAJOR-1 and
# MAJOR-2; adding instead keeps §0.9 direction 1 at zero modifications AND
# keeps the coverage those carried fixtures already provide, which an edit
# would have traded away.
# =========================================================================

# ── MAJOR-1: the two check-2 axes are never crossed (spec C2-18) ─────────
# A lint may resolve `subject` from the row's own `<level>` operand (correct,
# and what every assertion in the file checks) while resolving WHICH LEVEL'S
# `ADMITS` APPLIES from the nearest preceding `LEVEL:` line -- and pass all
# 29 tests of round 1. `_CHECK2_WRONG_LEVEL_BINDING_SPEC` is the only carried
# fixture where operand != nearest-preceding-LEVEL, and there NEITHER level
# carries an `ADMITS` line, so both resolve to the same default four and the
# outputs coincide. Every fixture that does carry an explicit `ADMITS` places
# its row immediately under its own level. Operand-binding and ADMITS-lookup
# are two independent axes and nothing crossed them.
#
# Crossed here, in both directions, so one fixture kills both mis-lookups:
#   `alpha_cross` ADMITS {any, all} and its row covers exactly that -- but the
#   row sits under `beta_cross`, which has NO ADMITS. Correct: conformant.
#   Proximity lookup: checked against the default four, so it is FALSE-FLAGGED.
#   `gamma_cross` has NO ADMITS (default four) and its row covers only
#   {any, all} -- but the row sits under `delta_cross`, which ADMITS {any, all}.
#   Correct: flagged. Proximity lookup: it looks covered and is CLEARED.
# A proximity-lookup lint therefore gets one level wrong in each direction,
# and the two assertions catch one each.
_ADMITS_CROSS_AXIS_SPEC = """\
# Fixture Spec — the row's operand and the ADMITS lookup disagree

LEVEL: alpha_cross
ADMITS: any, all

LEVEL: beta_cross
NON-UNIFORMITY: alpha_cross — fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all

LEVEL: gamma_cross

LEVEL: delta_cross
ADMITS: any, all
NON-UNIFORMITY: gamma_cross — fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all
"""

# ── MAJOR-2: [G24:6] had no discriminating fixture (spec C2-19) ──────────
# The v2 pin says a row whose `<level>` operand names no declared `LEVEL` is
# still checked, against the DEFAULT admitted set, and §2.5 states the reason
# in its own words: skipping such rows lets a typo'd operand hide its own
# row's under-enumeration. But the only row in the file naming an undeclared
# level was `_LEVEL_CASE_MISMATCH_SPEC`'s `audit_case`, whose EXCLUDES covers
# all four -- complete under either reading. So `if row_operand not in
# levels: continue`, the exact alternative §2.5 rejects, passed all 29 tests.
# Here the undeclared row is SHORT (missing `last`), so skipping it returns
# no finding and the assertion fires. This also pins the `subject` of a
# finding on an undeclared level, which nothing measured before.
_UNDECLARED_LEVEL_ROW_SPEC = """\
# Fixture Spec — a row naming a level the document never declares

LEVEL: declared_level
NON-UNIFORMITY: declared_level — fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all, first, last

NON-UNIFORMITY: undeclared_level — fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all, first
"""

# ── MAJOR-3: forward-crediting, killed properly this time (C2-21/C3-17) ──
# `[G24:5]`'s central clause is that an unanchored marker line MUST NOT be
# credited FORWARD to the next anchor. `_ORPHAN_MARKER_LINES_SPEC` cannot
# test it: there, forward-crediting leaves both anchors non-conformant for
# other reasons, so both assertions pass for the GREEN they were meant to
# kill. The credited marker has to be the one that COMPLETES its anchor:
#   the orphaned `EXCLUDES` is complete and the following row has NO EXCLUDES
#   of its own -- correct: `fwd_level`'s row is flagged; forward-crediting:
#   the row looks fully enumerated and is cleared.
#   the orphaned `NORMALISATION:` is exactly the property `Fwd.Seam` lacks --
#   correct: `Fwd.Seam` is flagged; forward-crediting: it looks fully pinned.
# Both assertions are positive, so a forward-crediting lint fails both.
_FORWARD_CREDIT_SPEC = """\
# Fixture Spec — an unanchored line that would COMPLETE the anchor after it

LEVEL: fwd_level
EXCLUDES: any, all, first, last
NON-UNIFORMITY: fwd_level — fixture set has >=2 members, one violating, plus control

NORMALISATION: none
SEAM: Fwd.Seam
ATTRIBUTE-PATH: pathlib.Path.read_text
BINDING-TIME: call-time
"""

# ── MAJOR-4: coverage as EQUALITY rather than SUPERSET (spec C2-20) ──────
# §2.6 pins `set(excludes) >= set(admits)`. The mis-write `==` survived all
# 29 tests because no fixture had an `EXCLUDES` that is a STRICT superset of
# its level's `ADMITS`: every explicit-ADMITS row covers exactly
# (`payload_field`, `alpha_level`), and `bogus_extra`'s fifth token is
# unrecognised, so it is flagged under both readings and separates nothing.
# `superset_level` ADMITS two and excludes all four -- conformant under the
# pinned superset reading, false-flagged under equality. `exact_level` is the
# equal case and `short_level` the deficient one, so the fixture is
# non-uniform within the check and an under-firing lint is caught too.
_EXCLUDES_SUPERSET_SPEC = """\
# Fixture Spec — EXCLUDES covering MORE than the level admits

LEVEL: superset_level
ADMITS: any, all
NON-UNIFORMITY: superset_level — fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all, first, last

LEVEL: exact_level
ADMITS: any, all
NON-UNIFORMITY: exact_level — fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all

LEVEL: short_level
ADMITS: any, all, first, last
NON-UNIFORMITY: short_level — fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all
"""

# ── [G24:7] line terminators are not part of an operand (spec C-CRLF) ────
# `.split("\n")` over a CRLF document yields `"LEVEL: phases\r"`, so the
# operand becomes `"phases\r"` -- and P1 forbids normalising a `subject`
# after the fact, so such a GREEN cannot repair it on output either. Every
# other fixture in this file is LF-only, so nothing forced the choice
# between `.split("\n")` and `.splitlines()`. Flagged by the gate as a live
# adversarial edge; pinned in spec §2.5 rather than left to a later round.
_CRLF_SPEC = (
    "# Fixture Spec — CRLF line terminators\r\n"
    "\r\n"
    "LEVEL: phases\r\n"
    "\r\n"
    "SEAM: Bare.Seam\r\n"
)

# ── [G24:8] markers are recognised AT LINE START (spec C-INDENT) ─────────
# §1.6 says the markers are recognised at line start; every fixture in the
# file is flush-left, so `line.strip().startswith(...)` and
# `line.startswith(...)` are indistinguishable across the whole set. An
# indented marker is prose: the lint must not see `indented_level` as a
# declared level or `Indented.Seam` as a seam. A stripping GREEN reports two
# findings on a document that has none.
_INDENTED_MARKER_SPEC = """\
# Fixture Spec — indented marker-looking lines are prose

LEVEL: real_level
NON-UNIFORMITY: real_level — fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all, first, last

Worked example, quoted at an indent so it is not itself a declaration:

    LEVEL: indented_level
    SEAM: Indented.Seam
"""


class TestQuantifierCompletenessLintBd24GateRound1:
    """Closures for the four MAJOR findings of gate round 1, plus the two
    adversarial edges the gate raised as advisory and the `Finding`
    attribute-set contract it found unmeasured. Additive only.
    """

    def test_ac_c5_check2_admits_looked_up_by_row_operand_not_by_proximity(self):
        """AC-C5(2). Gate round-1 MAJOR-1, spec C2-18. Kills the lint that
        takes `subject` from the row's operand -- as every assertion in the
        file already required -- while taking the ADMITTED SET from the
        nearest preceding `LEVEL:` line. Those are two independent axes and
        no carried fixture crossed them: the one fixture where operand and
        nearest-preceding-level differ has no `ADMITS` on either level, so
        both resolve to the default four and the two lints agree.

        Crossed here in both directions. `alpha_cross` ADMITS {any, all} and
        its row covers exactly that, but the row sits under the
        `ADMITS`-less `beta_cross`: correct binding clears it, proximity
        lookup checks it against the default four and false-flags it.
        `gamma_cross` has no `ADMITS` (default four) and its row covers only
        {any, all}, but sits under `delta_cross`, which ADMITS {any, all}:
        correct binding flags it, proximity lookup clears it. One assertion
        catches each direction.
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_ADMITS_CROSS_AXIS_SPEC)

        assert not any(f.subject == "alpha_cross" for f in findings)
        assert any(
            f.kind == "missing_reductions" and f.subject == "gamma_cross"
            for f in findings
        )
        assert any(
            f.kind == "missing_non_uniformity_row" and f.subject == "beta_cross"
            for f in findings
        )
        assert any(
            f.kind == "missing_non_uniformity_row" and f.subject == "delta_cross"
            for f in findings
        )

    def test_ac_c5_check2_row_naming_an_undeclared_level_is_still_checked(self):
        """AC-C5(2). Gate round-1 MAJOR-2, spec C2-19, closing `[G24:6]`.
        Kills `if row_operand not in levels: continue` -- the alternative
        §2.5 names and rejects, which passed all 29 tests of round 1 because
        the only row naming an undeclared level (`audit_case`, in the
        carried `_LEVEL_CASE_MISMATCH_SPEC`) covers all four reductions and
        so is conformant under either reading.

        Here the undeclared row is SHORT: it omits `last` against the
        default admitted set. A lint that skips such rows returns nothing
        for it, letting a typo'd operand hide its own row's
        under-enumeration -- the defect this AC exists for. The negative
        assertion also pins that the conformant declared level is not
        dragged in, and the last one that an undeclared name does not
        additionally produce a check-1 finding: it is not a declared level,
        so there is no level to report as row-less.
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_UNDECLARED_LEVEL_ROW_SPEC)

        assert any(
            f.kind == "missing_reductions" and f.subject == "undeclared_level"
            for f in findings
        )
        assert not any(f.subject == "declared_level" for f in findings)
        assert not any(f.kind == "missing_non_uniformity_row" for f in findings)

    def test_ac_c5_unanchored_line_is_not_credited_forward_to_the_next_anchor(self):
        """AC-C5(2) and AC-C5(3). Gate round-1 MAJOR-3, spec C2-21/C3-17,
        closing the half of `[G24:5]` that `_ORPHAN_MARKER_LINES_SPEC` could
        not reach. There, forward-crediting leaves both anchors
        non-conformant for unrelated reasons, so both assertions pass for the
        very GREEN they were written to kill -- an assertion that could not
        fail for its stated candidate, which is exactly what §0.9 direction 2
        exists to catch and what the gate caught instead.

        The credited marker must COMPLETE its anchor for the verdict to
        move. The orphaned `EXCLUDES` is complete and the row after it has
        none of its own: correct binding flags `fwd_level`, forward-crediting
        clears it. The orphaned `NORMALISATION:` is precisely the property
        `Fwd.Seam` lacks: correct binding flags it, forward-crediting clears
        it. Both assertions are positive, so a forward-crediting lint fails
        both rather than one.
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_FORWARD_CREDIT_SPEC)

        assert any(
            f.kind == "missing_reductions" and f.subject == "fwd_level"
            for f in findings
        )
        assert any(
            f.kind == "seam_not_pinned" and f.subject == "Fwd.Seam"
            for f in findings
        )

    def test_ac_c5_check2_coverage_is_superset_not_equality(self):
        """AC-C5(2). Gate round-1 MAJOR-4, spec C2-20. §2.6 pins coverage as
        `set(excludes) >= set(admits)`; the mis-write `==` passed all 29
        tests of round 1, because no fixture had an `EXCLUDES` that is a
        STRICT superset of its level's `ADMITS` -- every explicit-`ADMITS`
        row covered exactly, and the one row with an extra token carries an
        UNRECOGNISED one, so it is flagged under both readings and separates
        nothing.

        `superset_level` ADMITS two and excludes all four: conformant under
        the pinned reading, false-flagged under equality (F-1's over-firing
        class). `exact_level` is the boundary case and `short_level` the
        deficient one, so this fixture is non-uniform within the check and an
        under-firing lint that clears everything fails the third assertion.

        The fourth assertion closes gate round-2 MAJOR-1 (spec C2-21).
        `[G24:3]` pins at most one finding per `(kind, subject)`, and until
        now the only check-2 count assertion stood on `alpha_count`, which is
        missing exactly ONE reduction -- so a lint emitting one finding per
        missing reduction produced exactly one there and passed all 36 tests.
        `short_level` is missing TWO (`first` and `last`) and carries no
        unrecognised token, so it is the first row in the file where the
        collapse rule can fail, and the clause whose stated purpose is
        "without this, `len(findings)` is not a defined quantity" finally
        measures something for check 2.
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_EXCLUDES_SUPERSET_SPEC)

        assert not any(f.subject == "superset_level" for f in findings)
        assert not any(f.subject == "exact_level" for f in findings)
        assert any(
            f.kind == "missing_reductions" and f.subject == "short_level"
            for f in findings
        )
        assert len([f for f in findings if f.subject == "short_level"]) == 1

    def test_ac_c5_line_terminators_are_not_part_of_the_operand(self):
        """Spec `[G24:7]` / C-CRLF, raised by the gate as a live adversarial
        edge and pinned rather than deferred. Kills a GREEN splitting on
        `"\\n"`: over a CRLF document every operand acquires a trailing
        `"\\r"`, so `subject` becomes `"phases\\r"` -- and P1 forbids
        normalising `subject` after the fact, so such a GREEN cannot repair
        it on output either. Every other fixture in this file is LF-only, so
        nothing else forces the choice between `.split("\\n")` and
        `.splitlines()`. The seam half makes the same point on check 3's
        subject.
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_CRLF_SPEC)

        assert any(
            f.kind == "missing_non_uniformity_row" and f.subject == "phases"
            for f in findings
        )
        assert any(
            f.kind == "seam_not_pinned" and f.subject == "Bare.Seam"
            for f in findings
        )

    def test_ac_c5_indented_marker_lines_are_prose(self):
        """Spec `[G24:8]` / C-INDENT, the second advisory edge. §1.6 pins the
        markers as recognised AT LINE START, but every fixture in this file
        is flush-left, so `line.strip().startswith(...)` and
        `line.startswith(...)` are indistinguishable across the entire set.
        An indented marker is prose the lint ignores: a stripping GREEN sees
        `indented_level` as a declared level with no row and
        `Indented.Seam` as a seam with no properties, and reports two
        findings on a document that has none. The conformant `real_level`
        keeps this from being satisfiable by a lint that returns `[]`
        unconditionally -- that one is killed by every other fixture here.
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_INDENTED_MARKER_SPEC)

        assert findings == []

    def test_ac_c5_finding_has_exactly_the_two_pinned_attributes(self):
        """Gate round-1 MINOR-7. §2.1 pins `Finding` as a frozen dataclass
        with EXACTLY two attributes, `kind` and `subject`, and nothing
        measured the "exactly". A GREEN adding a third field (a `message`, a
        `line_number`) satisfies every other assertion in the file, and each
        such field is interface every later lot would then depend on --
        `Finding` is a shared carrier, so widening it silently is the same
        class of defect as bd#22's `[G22:20]` mutable container.

        The carried `test_ac_c5_flags_missing_non_uniformity_row_both_directions`
        asserts `is_dataclass` and frozen-ness on a `Finding` in hand; this
        adds the field-set equality it does not make. Asserted by set
        equality, not by membership, since membership is what a widened
        `Finding` would still satisfy.
        """
        import dataclasses

        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_MISSING_ROW_SPEC)

        assert findings != []
        field_names = {f.name for f in dataclasses.fields(findings[0])}
        assert field_names == {"kind", "subject"}


# =========================================================================
# PART 4 -- closures for gate round 2. Additive again: no fixture in Parts
# 1-3 is edited. Two assertions were added to existing round-2 tests (the
# `short_level` count assertion above), and two docstrings dropped the
# withdrawn C1-12/C3-16 citations; no fixture string changed, so containment
# against d39371f is untouched -- re-run `_bd24_containment_check.py`.
# =========================================================================

# ── MAJOR-2: `[G24:4]`'s "a repeated line is not itself a finding" (C3-18)
# and its check-2 mirror (C2-22) ─────────────────────────────────────────
# `_SEAM_PROPERTY_CARDINALITY_SPEC` discriminates the SET-not-COUNT half of
# `[G24:4]`, but not its second clause. Both seams carrying a duplicate there
# are offenders on the set rule anyway, and under `[G24:3]`'s (kind, subject)
# collapse a lint that ALSO treats a duplicate as a defect emits an identical
# findings list. So check 3 as "each of the three markers appears EXACTLY
# once" passed all 36 tests while reporting a fully pinned seam with one
# repeated `NORMALISATION:` as non-conformant -- which §2.5 forbids in terms.
# No conformant seam anywhere in the file carried a duplicate.
#
# The check-2 mirror -- a duplicated RECOGNISED token in `EXCLUDES` -- was
# unmeasured for the same reason (`gamma_count`'s duplicate sits on a row
# that is short anyway) and was not even pinned. Spec §2.5 now extends
# `[G24:4]` to both, and this fixture walks both:
#   `dup_level`  -- complete coverage, `all` listed twice: conformant.
#   `Dup.Seam`   -- all three properties, `NORMALISATION:` twice: conformant.
#   `Dup.Offender` -- `ATTRIBUTE-PATH` twice and no `NORMALISATION`: still a
#                     finding, so the fixture is non-uniform within the check
#                     and a lint that skips any seam carrying a duplicate
#                     (rather than de-duplicating it) is caught too.
_BENIGN_DUPLICATE_SPEC = """\
# Fixture Spec — repeated lines and repeated tokens that change nothing

LEVEL: dup_level
NON-UNIFORMITY: dup_level — fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all, first, last, all

SEAM: Dup.Seam
ATTRIBUTE-PATH: pathlib.Path.read_text
BINDING-TIME: call-time
NORMALISATION: none
NORMALISATION: none

SEAM: Dup.Offender
ATTRIBUTE-PATH: subprocess.run
ATTRIBUTE-PATH: subprocess.run
BINDING-TIME: call-time
"""

# ── Adversarial edge 1: level material and seam material INTERLEAVED ─────
# All 31 previous fixtures are levels-first, seams-last, so a lint keeping a
# SINGLE "current anchor" variable for both P3 (property line -> nearest
# preceding SEAM) and `[G24:2]` (EXCLUDES -> nearest preceding row) passes
# every one of them while violating the independence of the two rules. Here
# a `NON-UNIFORMITY` row sits between `Mixed.Seam` and its `NORMALISATION:`
# line, and an `EXCLUDES` sits after that property line. Correct binding
# reads the two anchors separately: `NORMALISATION:` still belongs to
# `Mixed.Seam` (a row is not a seam) and `EXCLUDES` still belongs to the row
# (a seam is not a row), so both are conformant. A single-anchor lint has
# the row current when the property line arrives and the property line
# current when the `EXCLUDES` arrives, and flags one or both.
_INTERLEAVED_ANCHORS_SPEC = """\
# Fixture Spec — level and seam material interleaved in one block

LEVEL: mixed_level

SEAM: Mixed.Seam
ATTRIBUTE-PATH: pathlib.Path.read_text
BINDING-TIME: call-time
NON-UNIFORMITY: mixed_level — fixture set has >=2 members, one violating, plus control
NORMALISATION: none
EXCLUDES: any, all, first, last

SEAM: Mixed.Offender
ATTRIBUTE-PATH: subprocess.run
BINDING-TIME: call-time
"""

# ── Adversarial edge 2: a row that PRECEDES its own level (spec [G24:9]) ─
# P4 pins the row to the level named by its operand and says nothing about
# order; every fixture in the file puts the row after its level, so an
# order-sensitive check 1 ("a level is discharged by a row BELOW it")
# survives the whole set. `late_level`'s row precedes its declaration and
# must still discharge it; `unrowed_level` has no row anywhere and must
# still be flagged, so a lint that answers by returning nothing is caught.
_ROW_BEFORE_LEVEL_SPEC = """\
# Fixture Spec — the row comes before the LEVEL it names

NON-UNIFORMITY: late_level — fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all, first, last

LEVEL: late_level

LEVEL: unrowed_level
"""

# ── Adversarial edge 4: MIXED-case marker prefixes (spec C-CASE) ─────────
# §2.2 pins marker recognition as case-INSENSITIVE, but only ALL-CAPS
# (everywhere) and all-lowercase (`_LOWERCASE_MARKERS_SPEC`) are walked, so
# `line.startswith(("LEVEL:", "level:"))` -- two literal spellings rather
# than a case-insensitive comparison -- passes all 36 tests. Title case is
# neither. `Title.Conformant` carries all three properties under Title-case
# markers and must NOT be flagged, which is what discriminates recognition
# of the PROPERTY markers from recognition of `Seam:` alone: a lint reading
# `Seam:` but not `Attribute-Path:` sees a bare seam and flags it.
_MIXED_CASE_MARKERS_SPEC = """\
# Fixture Spec — marker prefixes in Title case

Level: title_level

Seam: Title.Seam
Attribute-Path: pathlib.Path.read_text
Binding-Time: call-time

Seam: Title.Conformant
Attribute-Path: subprocess.run
Binding-Time: call-time
Normalisation: none
"""


class TestQuantifierCompletenessLintBd24GateRound2:
    """Closures for gate round 2: both MAJORs and the three adversarial
    edges it raised that were not already covered. Additive only.
    """

    def test_ac_c5_repeated_lines_and_tokens_are_not_themselves_findings(self):
        """AC-C5(2) and AC-C5(3). Gate round-2 MAJOR-2, spec C3-18 / C2-22,
        closing the second clause of `[G24:4]`. The set-not-count half is
        discriminated by `_SEAM_PROPERTY_CARDINALITY_SPEC`; this half was
        not, because both duplicate-carrying seams there are offenders on the
        set rule anyway and `[G24:3]`'s collapse makes a duplicate-punishing
        lint produce an identical findings list. So check 3 as "each marker
        appears EXACTLY once" passed all 36 tests while reporting a fully
        pinned seam as non-conformant.

        `Dup.Seam` carries all three properties with `NORMALISATION:` twice
        and `dup_level`'s row covers all four reductions with `all` listed
        twice -- both conformant, and both false-flagged by a lint that
        treats repetition as a defect (the check-2 mirror was not previously
        pinned at all). `Dup.Offender` repeats `ATTRIBUTE-PATH` and omits
        `NORMALISATION`, so it is still a finding: de-duplicating is required,
        skipping a seam that carries a duplicate is not.
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_BENIGN_DUPLICATE_SPEC)

        assert not any(f.subject == "Dup.Seam" for f in findings)
        assert not any(f.subject == "dup_level" for f in findings)
        assert any(
            f.kind == "seam_not_pinned" and f.subject == "Dup.Offender"
            for f in findings
        )

    def test_ac_c5_seam_and_level_anchors_are_tracked_independently(self):
        """AC-C5(2) and AC-C5(3). Gate round-2 adversarial edge 1, spec
        C3-19. P3 and `[G24:2]` are two separate binding rules over two
        separate anchor kinds, but every fixture in the file is levels-first
        and seams-last, so a lint keeping ONE "current anchor" variable for
        both satisfies all 36 tests while collapsing the two rules into one.

        Here a `NON-UNIFORMITY` row sits between `Mixed.Seam` and its
        `NORMALISATION:` line, and the row's `EXCLUDES` sits after that
        property line. Correct binding reads the anchors independently: a row
        is not a seam, so `NORMALISATION:` still belongs to `Mixed.Seam`; a
        seam is not a row, so `EXCLUDES` still belongs to the row. Both are
        therefore conformant. A single-anchor lint has the row current when
        the property line arrives -- losing `Mixed.Seam`'s `NORMALISATION` --
        and flags it. `Mixed.Offender` keeps the fixture non-uniform, so a
        lint that clears everything here is caught by the third assertion.
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_INTERLEAVED_ANCHORS_SPEC)

        assert not any(f.subject == "Mixed.Seam" for f in findings)
        assert not any(f.subject == "mixed_level" for f in findings)
        assert any(
            f.kind == "seam_not_pinned" and f.subject == "Mixed.Offender"
            for f in findings
        )

    def test_ac_c5_row_discharges_its_level_regardless_of_order(self):
        """AC-C5(1). Gate round-2 adversarial edge 2, spec `[G24:9]` / C1-14.
        P4 binds a row to the level named by its operand and says nothing
        about position; every fixture in the file happens to place the row
        after its level, so a lint reading check 1 as "a level is discharged
        by a row BELOW it" survives the entire set. `late_level`'s row
        precedes its declaration and must still discharge it. `unrowed_level`
        has no row anywhere, so a lint that answers by reporting nothing at
        all fails the second assertion.
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_ROW_BEFORE_LEVEL_SPEC)

        assert not any(f.subject == "late_level" for f in findings)
        assert any(
            f.kind == "missing_non_uniformity_row" and f.subject == "unrowed_level"
            for f in findings
        )

    def test_ac_c5_marker_prefixes_recognised_in_mixed_case(self):
        """Gate round-2 adversarial edge 4, spec C-CASE. §2.2 pins marker
        recognition as case-INSENSITIVE, and `[G24:8]` pins the other half of
        the same predicate (at line start). But only ALL-CAPS and, in
        `_LOWERCASE_MARKERS_SPEC`, all-lowercase are walked -- so
        `line.startswith(("LEVEL:", "level:"))`, two literal spellings rather
        than a case-insensitive comparison, passes all 36 tests and fails on
        the Title case a real document would contain.

        `title_level` must be flagged (no row) and `Title.Seam` must be
        flagged (no `NORMALISATION:`); a two-spelling lint sees this document
        as prose and returns nothing, failing both. `Title.Conformant` must
        NOT be flagged, which separates recognition of the three PROPERTY
        markers from recognition of `Seam:` alone -- a lint reading `Seam:`
        but not `Attribute-Path:` sees a bare seam and false-flags it.
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_MIXED_CASE_MARKERS_SPEC)

        assert any(
            f.kind == "missing_non_uniformity_row" and f.subject == "title_level"
            for f in findings
        )
        assert any(
            f.kind == "seam_not_pinned" and f.subject == "Title.Seam"
            for f in findings
        )
        assert not any(f.subject == "Title.Conformant" for f in findings)


# =========================================================================
# PART 5 -- gate round 3. One MAJOR (closed by the assertion added to
# `test_ac_c5_unanchored_marker_lines_bind_to_nothing` above, not by a new
# fixture) and one MINOR that needed a fixture: `[G24:7]`'s rationale said
# "the split is where it has to be right", while §5's simulation note said a
# stripping implementation is also correct. Both could not stand. Resolved in
# spec §2.5 by pinning the operand's FRAMING (surrounding whitespace and the
# line terminator are not part of it; "verbatim" governs case and interior
# characters), and that pin needs a fixture to be falsifiable.
# =========================================================================

# Trailing spaces are written as explicit concatenation rather than inside a
# triple-quoted block: trailing whitespace in a source line is exactly what an
# editor, a linter or a `git` whitespace filter silently removes, and this
# fixture is worthless the moment that happens.
_TRAILING_WHITESPACE_SPEC = (
    "# Fixture Spec — operands followed by trailing whitespace\n"
    "\n"
    "LEVEL: phases   \n"
    "NON-UNIFORMITY: phases — fixture set has >=2 members, one violating, plus control\n"
    "EXCLUDES: any, all, first, last\n"
    "\n"
    "SEAM: Trailing.Seam  \n"
    "ATTRIBUTE-PATH: pathlib.Path.read_text\n"
    "BINDING-TIME: call-time\n"
)


class TestQuantifierCompletenessLintBd24GateRound3:
    """Gate round 3's MINOR-B: the operand's framing, pinned and measured."""

    def test_ac_c5_trailing_whitespace_is_not_part_of_the_operand(self):
        """Spec `[G24:7]` as reconciled in v6, C-WS. `[G24:7]` originally
        justified itself with "the split is where it has to be right", while
        §5's executed simulation recorded that a `.split("\\n")` implementation
        which strips its operands behaves correctly -- and stripping is itself
        a normalisation of the operand, which P1 forbids for `subject`. The
        two statements could not both stand in a frozen spec. Resolved by
        pinning what the operand IS: the text after the marker's colon with
        surrounding whitespace and the line terminator removed. "Verbatim"
        governs case and interior characters, not the framing.

        Nothing measured that before -- no fixture carried trailing
        whitespace on an operand -- so the pin was unfalsifiable in exactly
        the way `[G24:5]` and `[G24:6]` were. Here `LEVEL: phases   ` must
        still be discharged by `NON-UNIFORMITY: phases — …` (a lint keeping
        the trailing spaces sees two different names and reports a missing
        row), and `SEAM: Trailing.Seam  ` must be reported with its name
        exactly, not with the spaces attached.
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_TRAILING_WHITESPACE_SPEC)

        assert not any(f.kind == "missing_non_uniformity_row" for f in findings)
        assert any(
            f.kind == "seam_not_pinned" and f.subject == "Trailing.Seam"
            for f in findings
        )


# ── Gate round-4 MAJOR: `[G24:7]`'s framing clause is TWO-sided (C-SPACING) ──
# v6 pinned the operand as "the text after the marker's colon, with SURROUNDING
# whitespace and the line terminator removed" -- and then measured the trailing
# side only. No fixture carried anything but exactly one space after a colon, so
# `operand = line[len("LEVEL: "):].rstrip()` -- marker plus one assumed space --
# lands correctly on all 36 fixtures and passes 41/41. Verified as mutant #29:
# it did.
#
# It is wrong on input the spec does not exclude, and silently: `LEVEL:phases`
# yields the subject `"hases"`, a Finding naming a level that does not appear in
# the document, which is exactly what P1's verbatim guarantee exists to prevent.
#
# The gate offered two closes and no preference: declare the single space part
# of the marker in §6, or measure the other side. Measuring is the one taken --
# narrowing the clause to what already happens to be covered is how a spec ends
# up with semantics chosen by its fixtures rather than the reverse, and a real
# document written by hand will contain both spellings.
#
# `packed` and `padded` are declared with NO row, so their subjects must be
# reported verbatim: a fixed-offset lint reports `acked` for the first, and a
# lint stripping only the trailing side reports `   padded` for the second.
# `normal` (one space, discharged) and `Normal.Seam` (one space, fully pinned)
# keep the fixture non-uniform, so a lint that flags everything fails too.
_OPERAND_SPACING_SPEC = """\
# Fixture Spec — marker-to-operand spacing other than exactly one space

LEVEL:packed

LEVEL:   padded

LEVEL: normal
NON-UNIFORMITY: normal — fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all, first, last

SEAM:Packed.Seam
ATTRIBUTE-PATH: pathlib.Path.read_text
BINDING-TIME: call-time

SEAM:   Padded.Seam
ATTRIBUTE-PATH: pathlib.Path.read_text
BINDING-TIME: call-time

SEAM: Normal.Seam
ATTRIBUTE-PATH: pathlib.Path.read_text
BINDING-TIME: call-time
NORMALISATION: none
"""


class TestQuantifierCompletenessLintBd24GateRound4:
    """Gate round-4's MAJOR: the leading side of `[G24:7]`'s framing clause."""

    def test_ac_c5_operand_spacing_other_than_one_space(self):
        """Spec `[G24:7]` / C-SPACING, closing gate round-4's MAJOR -- a
        finding against v6's own new clause, which pinned "surrounding
        whitespace" and then measured only the trailing side. Every fixture in
        the file writes exactly `": "` after every marker, so
        `line[len("LEVEL: "):].rstrip()` -- marker plus one assumed space --
        is indistinguishable from parsing the operand, and passes 41/41
        (verified as mutant #29). It is silently wrong on `LEVEL:phases`,
        reporting a level named `hases` that appears nowhere in the document.

        `packed` (no space) and `padded` (three) are declared without a row, so
        check 1 must report both subjects VERBATIM: a fixed-offset lint reports
        `acked`, and a lint stripping only the trailing side reports
        `   padded`. `Packed.Seam` and `Padded.Seam` carry the same test on
        check 3's subject. `normal` and `Normal.Seam` use the ordinary single
        space and are conformant, so a lint that mangles every operand -- or
        one that simply flags everything -- fails the negative assertions too.
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_OPERAND_SPACING_SPEC)

        assert any(
            f.kind == "missing_non_uniformity_row" and f.subject == "packed"
            for f in findings
        )
        assert any(
            f.kind == "missing_non_uniformity_row" and f.subject == "padded"
            for f in findings
        )
        assert any(
            f.kind == "seam_not_pinned" and f.subject == "Packed.Seam"
            for f in findings
        )
        assert any(
            f.kind == "seam_not_pinned" and f.subject == "Padded.Seam"
            for f in findings
        )
        assert not any(f.subject == "normal" for f in findings)
        assert not any(f.subject == "Normal.Seam" for f in findings)


# =========================================================================
# PART 6 -- gate round 5. Three MAJORs with ONE cause: `ADMITS:`,
# `NON-UNIFORMITY:` and `EXCLUDES:` appear across all 37 previous fixtures
# only in ALL-CAPS and only with their anchor already present. So every
# clause ranging over "the markers" or over "the anchored lines" was measured
# on the other five markers and on two of the three anchor rules. Two
# fixtures close all three.
# =========================================================================

# ── MAJOR-1: §2.2's case-insensitivity, measured for five of eight markers ──
# `_LOWERCASE_MARKERS_SPEC` and `_MIXED_CASE_MARKERS_SPEC` both spell the SAME
# five: LEVEL, SEAM and the three property markers. Neither carries a row, an
# `EXCLUDES` or an `ADMITS` line at all, and every other fixture writes those
# three in upper case without exception -- so a lint recognising five markers
# case-insensitively and the row/reduction three only in ALL-CAPS passes 42/42
# (verified as mutant #30). It is C-CASE one marker-family over, and an
# implementer made to pass those two fixtures has pressure on exactly the five
# they spell and none at all on the other three.
#
# `title_row_level` is conformant ONLY if `Admits:` and `Excludes:` are both
# recognised; a case-sensitive lint misses its row entirely and reports it under
# check 1 instead. `lower_row_level` is flagged ONLY if its lower-case row and
# short `excludes:` are read. The two assertions therefore fail in different
# ways for the same GREEN.
#
# The trailing indented `Excludes:` also closes gate round-5's advisory on
# `[G24:8]` for free: `_INDENTED_MARKER_SPEC` instantiates "an indented marker
# is prose" for `LEVEL:` and `SEAM:` only, so a lint using `line.startswith` for
# declarations and `line.strip().startswith` for the rest passed. Here a
# stripping lint reads it as `lower_row_level`'s coverage -- it is the nearest
# preceding row -- completes the row and clears a level that must be flagged.
_ROW_MARKER_CASE_SPEC = """\
# Fixture Spec — row and reduction markers in cases other than upper

Level: title_row_level
Admits: any, all
Non-Uniformity: title_row_level — fixture set has >=2 members, one violating, plus control
Excludes: any, all

level: lower_row_level
non-uniformity: lower_row_level — fixture set has >=2 members, one violating, plus control
excludes: any, all

Quoted at an indent, so it declares nothing and discharges nothing:

    Excludes: any, all, first, last
"""

# ── MAJOR-2 and MAJOR-3: the ADMITS half of two clauses ─────────────────
# `[G24:5]` states the no-anchor base case and then instantiates it for P3 and
# `[G24:2]`. There are THREE anchored marker rules: P2 (`ADMITS` -> nearest
# preceding `LEVEL`) has the same base case, it is in neither orphan fixture,
# and every fixture carrying an `ADMITS` line places it directly under its own
# `LEVEL:`. So `[G24:5]` was either two-thirds measured or `ADMITS`' base case
# was silently unpinned -- and §6 exists to stop the second.
# `[G24:4]` says "`ADMITS` **and** `EXCLUDES` are sets of recognised tokens" and
# measured only `EXCLUDES`: `_BENIGN_DUPLICATE_SPEC` carries its duplicate on an
# `EXCLUDES` line. That is gate round-2's MAJOR-2 verbatim with `ADMITS` where
# `EXCLUDES` stood -- accepted as real then, so real now.
#
# The orphaned `ADMITS` is the one that would COMPLETE the level after it (the
# `_FORWARD_CREDIT_SPEC` construction applied to the third rule): `fwd_admits_level`
# defaults to four, covers two, and must be flagged -- a forward-crediting lint
# clears it, and a lint dereferencing an unset current level raises and fails
# everything. `dup_admits_level` admits {any, all} with `any` written twice and
# covers both: conformant, and false-flagged by a duplicate-punishing lint.
_ADMITS_BASE_CASE_SPEC = """\
# Fixture Spec — an ADMITS line before any LEVEL, and a duplicated ADMITS token

ADMITS: any, all

LEVEL: fwd_admits_level
NON-UNIFORMITY: fwd_admits_level — fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all

LEVEL: dup_admits_level
ADMITS: any, all, any
NON-UNIFORMITY: dup_admits_level — fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all
"""


class TestQuantifierCompletenessLintBd24GateRound5:
    """The row/reduction marker family: case-insensitivity for the three
    markers no fixture spelled in another case, and the `ADMITS` side of the
    two clauses that named it and measured `EXCLUDES`.
    """

    def test_ac_c5_row_and_reduction_markers_recognised_in_any_case(self):
        """Gate round-5 MAJOR-1, spec C-CASE2 (and `[G24:8]`'s advisory).
        §2.2 pins case-insensitive recognition over EIGHT markers, and the two
        fixtures certifying it -- `_LOWERCASE_MARKERS_SPEC` and
        `_MIXED_CASE_MARKERS_SPEC` -- spell the same FIVE. Neither carries a
        row, an `EXCLUDES` or an `ADMITS` line, and every other fixture writes
        those three in ALL-CAPS, so a lint case-folding five markers and
        matching the row/reduction three against upper case only passes 42/42.

        `title_row_level` is conformant only if `Admits:` and `Excludes:` are
        both recognised -- a case-sensitive lint sees no row for it and
        reports it under check 1, failing the first assertion.
        `lower_row_level` is flagged only if its lower-case row and short
        `excludes:` are read, failing the second. One GREEN, two different
        failures.

        The indented `Excludes:` at the end closes the `[G24:8]` advisory: a
        lint using `line.strip().startswith(...)` for reduction lines reads it
        as `lower_row_level`'s coverage -- it is the nearest preceding row --
        completing the row and clearing a level that must be flagged.
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_ROW_MARKER_CASE_SPEC)

        assert not any(f.subject == "title_row_level" for f in findings)
        assert any(
            f.kind == "missing_reductions" and f.subject == "lower_row_level"
            for f in findings
        )

    def test_ac_c5_admits_base_case_and_duplicate_admits_token(self):
        """Gate round-5 MAJOR-2 and MAJOR-3, spec C2-24 / C2-25.

        `[G24:5]` pins the no-anchor base case and instantiates it for P3 and
        `[G24:2]` -- but there are THREE anchored marker rules, and P2
        (`ADMITS` to the nearest preceding `LEVEL`) has the same base case in
        neither orphan fixture. Every fixture with an `ADMITS` line puts it
        directly under its own `LEVEL:`. The orphan here is the one that would
        COMPLETE the level after it, so `fwd_admits_level` defaults to four,
        covers two and must be flagged: a forward-crediting lint clears it,
        and one that dereferences an unset current level raises, failing both
        assertions rather than returning findings (§2.1's MUST-NOT-RAISE, for
        the one anchored marker `_ORPHAN_MARKER_LINES_SPEC` does not carry).

        `[G24:4]` says "`ADMITS` **and** `EXCLUDES` are sets of recognised
        tokens" and measured only `EXCLUDES`. `dup_admits_level` admits
        {any, all} with `any` written twice and covers both, so it is
        conformant and a duplicate-punishing lint false-flags it. That is gate
        round-2's MAJOR-2 with `ADMITS` where `EXCLUDES` stood; the clause was
        extended to both in the revision that measured one.

        The third assertion closes gate round-6's MAJOR (spec C2-26).
        `[G24:5]` states TWO consequences for each of its three anchor rules --
        an unanchored line is not itself a finding, and is not credited
        forward -- so the clause is a 3x2 grid, and P2's not-a-finding cell was
        the one empty square. `levels.setdefault(cur_level or "", ...)`
        registers a level named `""` carrying the orphaned `ADMITS` and no row,
        which check 1 then reports as `Finding("missing_non_uniformity_row",
        "")`: a finding invented from an unanchored line, which `[G24:5]`
        forbids in terms, and an empty `subject`, which §6 declines to pin
        precisely because it would arise from an empty operand -- reached here
        from a document that contains none. Verified: that mutant passes 44/44
        without this assertion. Non-vacuous because both levels in this
        fixture carry rows, so no check-1 finding is correct here at all --
        the same reasoning that makes `_ORPHAN_MARKER_LINES_SPEC`'s third
        assertion sound.
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_ADMITS_BASE_CASE_SPEC)

        assert any(
            f.kind == "missing_reductions" and f.subject == "fwd_admits_level"
            for f in findings
        )
        assert not any(f.subject == "dup_admits_level" for f in findings)
        assert not any(f.kind == "missing_non_uniformity_row" for f in findings)


# ── Gate round-6 advisories 1 and 2, closed rather than declared (C-ROWOP) ──
# Advisory 1: `[G24:7]` is measured on `LEVEL:` and `SEAM:` operands and never
# on a `NON-UNIFORMITY:` one -- and the row operand has its OWN extraction path,
# the em-dash split. So `line[len("NON-UNIFORMITY: "):].split("—")[0].strip()`,
# a fixed offset on that marker alone, passes 44/44. The gate declined to file
# it because every existing row has a space before its em dash, which forces a
# strip on that path; it costs one fixture to remove the argument entirely.
# Advisory 2: `ADMITS:` was spelled in two cases where the other seven markers
# are now spelled in three. `admits:` here makes the count uniform at eight
# markers x three cases.
#
# `packed_row` is conformant ONLY if the packed row operand is read as
# `packed_row` (a fixed offset yields `acked_row`, leaving the level row-less)
# AND the lower-case `admits:` is recognised (otherwise the level defaults to
# four and its two-token `EXCLUDES` is short). `spaced_row` is flagged ONLY if
# the leading spaces are stripped from its row operand -- a lint keeping them
# binds the row to nothing, which turns a check-2 finding into a check-1 one.
# The third assertion catches that inversion directly: both levels here carry
# rows, so no check-1 finding is correct in this document at all.
_ROW_OPERAND_SPACING_SPEC = """\
# Fixture Spec — the row marker's own operand-extraction path

LEVEL: packed_row
admits: any, all
NON-UNIFORMITY:packed_row — fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all

LEVEL: spaced_row
NON-UNIFORMITY:   spaced_row — fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all
"""


class TestQuantifierCompletenessLintBd24GateRound6:
    """Gate round-6's two advisories, closed. The MAJOR closed with a single
    assertion on an existing test and needed no fixture.
    """

    def test_ac_c5_row_operand_extraction_path(self):
        """Spec C-ROWOP, closing gate round-6's advisories 1 and 2.

        `[G24:7]`'s framing pin is measured on `LEVEL:` and `SEAM:` operands
        only, and the `NON-UNIFORMITY:` operand has a separate extraction path
        -- the em-dash split -- so a fixed offset applied to that one marker
        survives the whole file. Every existing row happens to put a space
        before its em dash, which forces a strip on that path and hides the
        difference; `packed_row` removes that accident.

        `packed_row` is conformant only if BOTH hold: the packed row operand
        reads as `packed_row` (a fixed offset yields `acked_row`, leaving the
        level with no row), and the lower-case `admits:` is recognised
        (otherwise the level takes the default four and its two-token
        `EXCLUDES` is short). `spaced_row` is flagged only if the leading
        spaces are stripped from its row operand; a lint that keeps them binds
        the row to nothing, which converts a check-2 finding into a check-1
        one -- caught directly by the third assertion, since both levels here
        carry rows and no check-1 finding is correct in this document at all.
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_ROW_OPERAND_SPACING_SPEC)

        assert not any(f.subject == "packed_row" for f in findings)
        assert any(
            f.kind == "missing_reductions" and f.subject == "spaced_row"
            for f in findings
        )
        assert not any(f.kind == "missing_non_uniformity_row" for f in findings)


# ── Gate round-7 MAJOR: `[G24:10]` has three anchors, and P2 was never ──────
# ── exercised at DISTANCE (C2-27) ──────────────────────────────────────────
# Two halves of one gap, both invisible to §0.9(3) as applied, because §5 still
# recorded `[G24:10]` as a single-cell clause and v9's rewrite did not convert
# it to axes -> cells.
#
# Half 1: `[G24:10]` names "the TWO anchors" -- P3's `SEAM` and `[G24:2]`'s row.
# There are THREE, because there are three binding rules: P2's anchor is the
# `LEVEL`. Independence is a property of PAIRS, so the cross-product is three:
# (SEAM, row) -- `_INTERLEAVED_ANCHORS_SPEC`; (LEVEL, row) --
# `_EXCLUDES_ROW_BINDING_SPEC`, where a lint conflating the two binds `EXCLUDES`
# to the level and inverts both levels; and (LEVEL, SEAM) -- nothing. That is
# the same correction `[G24:5]`'s grid took last round, one property over.
#
# Half 2: "nearest PRECEDING" makes two independent claims -- WHICH candidate
# anchor is chosen when several precede, and THAT the rule reaches across
# intervening lines at all. The second is an axis nobody named. Every `ADMITS`
# line in all 40 previous fixtures sits on the line IMMEDIATELY after its own
# `LEVEL:` -- distance 1, nothing between, not even a blank line. P3 and
# `[G24:2]` are both exercised at distance; P2 never was, so "nearest preceding"
# has never been distinguished from "the previous line" for it. The selection
# half IS measured (`_EXCLUDES_SUPERSET_SPEC`'s three consecutive
# `ADMITS`-bearing levels kill a first-`LEVEL`-only binding); the gap is
# distance.
#
# `distant_admits` is conformant only if its `ADMITS` reaches back across an
# entire `SEAM` block -- filling the (LEVEL, SEAM) pair and the distance axis at
# once. `blank_gap_admits` is conformant only if a blank line does not break the
# binding, which is what kills a previous-line-only lint specifically: every
# fixture in this file separates blocks with blank lines, so that is the house
# style applied one line earlier, not exotic input. `Between.Seam` is fully
# pinned and must not be flagged, so a lint mis-assigning the `ADMITS` to the
# seam is caught on the seam side too. `control_default` keeps the document
# non-uniform, and the last assertion catches the check-2 -> check-1 inversion.
_DISTANT_ADMITS_SPEC = """\
# Fixture Spec — an ADMITS line far from its LEVEL, across other material

LEVEL: distant_admits

SEAM: Between.Seam
ATTRIBUTE-PATH: pathlib.Path.read_text
BINDING-TIME: call-time
NORMALISATION: none

ADMITS: any, all
NON-UNIFORMITY: distant_admits — fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all

LEVEL: blank_gap_admits

ADMITS: any, all
NON-UNIFORMITY: blank_gap_admits — fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all

LEVEL: control_default
NON-UNIFORMITY: control_default — fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all, first, last

LEVEL: distant_invalid

SEAM: Second.Between
ATTRIBUTE-PATH: subprocess.run
BINDING-TIME: call-time
NORMALISATION: none

ADMITS: any, bogus
NON-UNIFORMITY: distant_invalid — fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all, first, last
"""


class TestQuantifierCompletenessLintBd24GateRound7:
    """`[G24:10]`'s third anchor pair, and the distance axis of P2."""

    def test_ac_c5_admits_binds_across_distance_and_intervening_material(self):
        """AC-C5(2). Gate round-7 MAJOR, spec C2-27, closing two halves of one
        gap: `[G24:10]` named two anchor kinds where there are three (P2's is
        the `LEVEL`), and "nearest preceding" was never distinguished from
        "the previous line" for P2 -- every `ADMITS` in the file sat on the
        line immediately after its own `LEVEL:`, distance 1, with nothing
        between, not even a blank line. P3 and `[G24:2]` are both exercised at
        distance; P2 was not.

        `distant_admits` admits {any, all} and covers exactly that, but its
        `ADMITS` sits below an entire intervening `SEAM` block: conformant only
        if the binding reaches back across it, and this fills the
        (`LEVEL`, `SEAM`) pair of `[G24:10]`'s cross-product at the same time.
        A lint keeping one "current declaration" variable for both kinds gives
        it the default four against a two-token `EXCLUDES` and flags it.
        `blank_gap_admits` is separated from its `ADMITS` by a blank line only:
        a previous-line-only lint flags that one too, and every block in this
        file is separated exactly that way, so it is the house style one line
        earlier rather than exotic input.

        `Between.Seam` is fully pinned and must not be flagged, catching a lint
        that mis-assigns the `ADMITS` to the seam instead. `control_default`
        takes the default four and covers it, so a lint that clears everything
        is caught by the positives elsewhere and one that flags everything is
        caught here. The last assertion catches the check-2 -> check-1
        inversion, since all five levels carry rows.

        `distant_invalid` supplies the POSITIVE discriminator this fixture
        would otherwise lack -- five negatives alone are all satisfied by a
        lint that returns nothing, and although that lint dies elsewhere, a
        fixture should discriminate within itself (§0.8 rule 1). Its distant
        `ADMITS` carries an unrecognised token, so `[G24:1]` makes the level a
        finding whatever its `EXCLUDES` says -- while a lint that never binds
        the distant `ADMITS` at all falls back to the default four, sees an
        `EXCLUDES` covering exactly those four, and reports nothing. The two
        candidates this fixture targets are therefore caught by a positive
        assertion as well as by the negatives.

        Corrected after gate round 8: `distant_invalid`'s `ADMITS` originally
        sat below a blank line only, so `single_anchor_level_seam` still bound
        it correctly and was caught by assertion 1 rather than by the positive
        -- the sentence above was true of one candidate, not two. Its `ADMITS`
        now sits below a complete `SEAM` block as well, which makes the claim
        true as written. Coverage diff for the change: the previous form killed
        `admits_previous_line_only` through this assertion and
        `single_anchor_level_seam` through assertion 1; the new form kills both
        through both. Strictly more, nothing traded.
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_DISTANT_ADMITS_SPEC)

        assert not any(f.subject == "distant_admits" for f in findings)
        assert not any(f.subject == "blank_gap_admits" for f in findings)
        assert not any(f.subject == "Between.Seam" for f in findings)
        assert not any(f.subject == "control_default" for f in findings)
        assert any(
            f.kind == "missing_reductions" and f.subject == "distant_invalid"
            for f in findings
        )
        assert not any(f.kind == "missing_non_uniformity_row" for f in findings)


# ── Gate round-8 MAJOR: the (LEVEL, row) anchor pair (C2-28) ────────────────
# Round 7 recorded this pair as covered by `_EXCLUDES_ROW_BINDING_SPEC`. That
# was WRONG, and the gate traced it: that fixture kills C2-10 -- a lint binding
# `EXCLUDES` to the nearest preceding `LEVEL` BY RULE -- and a lint with one
# shared "current declaration" variable written by both `LEVEL:` and
# `NON-UNIFORMITY:` behaves identically there, because each row line is the last
# thing written before its own `EXCLUDES`. Both assertions pass.
#
# The two are different candidates and this lot has already ruled that they are:
# C3-14 (property lines bound to the FOLLOWING seam) was killed long before
# C3-19 (a single current-anchor variable serving two rules) was found alive,
# and `_INTERLEAVED_ANCHORS_SPEC` exists because the first kill did not imply
# the second. The (LEVEL, row) pair stands in exactly that relation to C2-10.
#
# It is also the sub-axis §0.9(3b)'s sweep stopped one grain short of. That
# sweep asked "is each binding rule exercised at distance >= 2" and the answer
# was yes -- but WHAT INTERVENES is itself a sub-axis whose members are the
# other anchor kinds, and for `[G24:2]` the only intervening material ever used
# was a property line. A `LEVEL:` had never intervened.
#
# `shared_anchor`'s `EXCLUDES` sits below an interposed `LEVEL:`; `[G24:2]` binds
# it to the nearest preceding ROW, which is `shared_anchor`'s, so `shared_anchor`
# is conformant. A shared-variable lint has `interposed_level` current when that
# `EXCLUDES` arrives, leaves `shared_anchor`'s row with no coverage, and flags
# it. `interposed_level` is short by two and must be flagged -- the positive
# discriminator, per §0.8 rule 1.
_SHARED_ANCHOR_SPEC = """\
# Fixture Spec — a LEVEL interposed between a row and its EXCLUDES

LEVEL: shared_anchor

NON-UNIFORMITY: shared_anchor — fixture set has >=2 members, one violating, plus control
LEVEL: interposed_level
EXCLUDES: any, all, first, last

NON-UNIFORMITY: interposed_level — fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all

LEVEL: shared_control
NON-UNIFORMITY: shared_control — fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all, first, last
"""


class TestQuantifierCompletenessLintBd24GateRound8:
    """The (`LEVEL`, row) cell of `[G24:10]`, which round 7 recorded as filled
    by a fixture that kills a different candidate.
    """

    def test_ac_c5_level_and_row_anchors_are_tracked_independently(self):
        """AC-C5(2). Gate round-8 MAJOR, spec C2-28. Round 7 claimed
        `_EXCLUDES_ROW_BINDING_SPEC` discriminated this pair; it does not. That
        fixture kills a lint binding `EXCLUDES` to the nearest preceding
        `LEVEL` by rule (C2-10), while a lint keeping ONE shared "current
        declaration" variable written by both `LEVEL:` and `NON-UNIFORMITY:`
        produces identical output there -- each row line is the last thing
        written before its own `EXCLUDES`, so the shared variable always holds
        the right value.

        Here a `LEVEL:` is interposed between a row and its `EXCLUDES`, which
        happens in no other fixture: every `EXCLUDES` in the file immediately
        follows its row except in `_INTERLEAVED_ANCHORS_SPEC`, where a property
        line intervenes. `[G24:2]` binds by nearest preceding ROW, so
        `shared_anchor`'s `EXCLUDES` is its own and it is conformant; the
        shared-variable lint has `interposed_level` current and leaves
        `shared_anchor` with no coverage, flagging it.

        `interposed_level` is short by two and must be flagged, so the fixture
        discriminates within itself rather than relying on other documents.
        `shared_control` holds the negative against an over-flagging lint, and
        the last assertion catches the check-2 to check-1 inversion, since all
        three levels carry rows.
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_SHARED_ANCHOR_SPEC)

        assert not any(f.subject == "shared_anchor" for f in findings)
        assert any(
            f.kind == "missing_reductions" and f.subject == "interposed_level"
            for f in findings
        )
        assert not any(f.subject == "shared_control" for f in findings)
        assert not any(f.kind == "missing_non_uniformity_row" for f in findings)


# ── Gate round-9 MAJOR: anchor independence is SIX ORDERED cells (C2-29) ────
# §0.9(3c) and every table so far treated "across what" as a set of UNORDERED
# pairs -- three cells, all three marked filled after round 9. But independence
# is NOT symmetric. "Does an intervening X close a live Y?" and "does an
# intervening Y close a live X?" are different questions with different
# implementations, and each existing fixture answers exactly one direction:
#
#   victim SEAM  / intruder row    -> _INTERLEAVED_ANCHORS_SPEC
#   victim LEVEL / intruder SEAM   -> _DISTANT_ADMITS_SPEC
#   victim row   / intruder LEVEL  -> _SHARED_ANCHOR_SPEC
#   victim LEVEL / intruder row    -> EMPTY
#   victim SEAM  / intruder LEVEL  -> EMPTY
#   victim row   / intruder SEAM   -> EMPTY
#
# The three filled cells form one cycle; the three empty ones are its reverse.
# Verified against every fixture: no `ADMITS` ever follows its own level's row,
# no property line ever follows an intervening `LEVEL:`, and no `EXCLUDES` ever
# follows an intervening `SEAM:`. Each surviving lint is `if <other kind>:
# cur_<this kind> = None` -- an anchor of one kind closing a live anchor of
# another, which is exactly what `[G24:10]` forbids -- and each is the MIRROR of
# a candidate already fixtured here. `_INTERLEAVED_ANCHORS_SPEC` exists because
# a row must not close a live seam; nothing in that reasoning is
# direction-specific, it was simply written in one direction.
# All three confirmed at 47/47 before this fixture.
#
# With this, the table is CLOSED: "across what" ranges over the other anchor
# kinds, there are three kinds, so there are 3 x 2 = 6 ordered cells and no
# seventh.
_ANCHOR_CYCLE_SPEC = """\
# Fixture Spec — each anchor kind interposed inside another kind's block

LEVEL: cyc_level
NON-UNIFORMITY: cyc_level — fixture set has >=2 members, one violating, plus control
ADMITS: any, all
EXCLUDES: any, all

SEAM: Cyc.Seam
ATTRIBUTE-PATH: pathlib.Path.read_text
BINDING-TIME: call-time
LEVEL: cyc_between
NORMALISATION: none
NON-UNIFORMITY: cyc_between — fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all, first, last

LEVEL: cyc_seam_gap
NON-UNIFORMITY: cyc_seam_gap — fixture set has >=2 members, one violating, plus control
SEAM: Cyc.Interposed
ATTRIBUTE-PATH: subprocess.run
BINDING-TIME: call-time
NORMALISATION: none
EXCLUDES: any, all, first, last

LEVEL: cyc_short
NON-UNIFORMITY: cyc_short — fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all
"""


class TestQuantifierCompletenessLintBd24GateRound9:
    """The reverse cycle of `[G24:10]`'s ordered cross-product — the three
    cells whose mirrors were fixtured and which were never asked in this
    direction.
    """

    def test_ac_c5_no_anchor_kind_closes_a_live_anchor_of_another_kind(self):
        """AC-C5(2) and AC-C5(3). Gate round-9 MAJOR, spec C2-29. Anchor
        independence is not symmetric: an intervening `X` closing a live `Y`
        and an intervening `Y` closing a live `X` are different
        implementations, and the three fixtures that covered this clause
        answered one direction each, forming a cycle. This fixture is its
        reverse.

        `cyc_level` puts its `ADMITS` BELOW its own row: it admits {any, all}
        and covers exactly that, so it is conformant -- `level_block_ends_at_row`
        (`if row: cur_level = None`) gives it the default four and flags it.
        `Cyc.Seam` puts its `NORMALISATION:` below an interposed `LEVEL:`: fully
        pinned, so `seam_block_ends_at_level` loses the property and flags a
        conformant seam -- that is C3-19 with `LEVEL` where the row stood, and
        C3-19 was accepted as real. `cyc_seam_gap` puts its `EXCLUDES` below an
        interposed `SEAM` block: covers four, so `row_block_ends_at_seam` loses
        the line and flags it.

        Three mutants, three different subjects, so one document keeps them
        attributable. `cyc_short` is the positive discriminator (§0.8 rule 1)
        and the last assertion catches the check-2 to check-1 inversion, since
        every level here carries a row.
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_ANCHOR_CYCLE_SPEC)

        assert not any(f.subject == "cyc_level" for f in findings)
        assert not any(f.subject == "Cyc.Seam" for f in findings)
        assert not any(f.subject == "cyc_seam_gap" for f in findings)
        assert not any(f.subject == "Cyc.Interposed" for f in findings)
        assert not any(f.subject == "cyc_between" for f in findings)
        assert any(
            f.kind == "missing_reductions" and f.subject == "cyc_short"
            for f in findings
        )
        assert not any(f.kind == "missing_non_uniformity_row" for f in findings)


# =========================================================================
# PART 9 -- bd#39 gate round 1. Two fixtures; the third MAJOR is spec-only.
# Both close the SAME shape one marker-family over: a clause pinned over N
# markers and measured on fewer, which is the standing instrument spec §0.0
# carries forward from the parent's round-5 finding.
# =========================================================================

# ── MAJOR-2: `[G24:7]`'s framing clause has FIVE operand-bearing markers ───
# `[G24:7]` pins, generically, "the text after the marker's colon, with
# surrounding whitespace and the line terminator removed". Five markers carry an
# operand: LEVEL, SEAM, NON-UNIFORMITY, ADMITS, EXCLUDES. Three were measured --
# `_TRAILING_WHITESPACE_SPEC` and `_OPERAND_SPACING_SPEC` cover LEVEL and SEAM,
# `_ROW_OPERAND_SPACING_SPEC` covers NON-UNIFORMITY. Every one of the reduction
# lines in all 43 previous fixtures writes exactly one space after the colon,
# ", " between tokens, no trailing whitespace, LF only; `_CRLF_SPEC` carries no
# reduction line at all. So `raw = line[line.index(":") + 2:]` on reduction
# lines alone -- marker plus one ASSUMED space, no strip -- passes 48/48.
# Verified before this fixture.
#
# It is silently wrong on input the spec does not exclude: `EXCLUDES:any, …`
# yields the first token `ny`, so a fully conformant row is reported both as
# short AND as carrying an unrecognised token -- a finding about a conformant
# document, the over-firing class F-1 exists to prevent.
#
# `control_red` is the positive discriminator: it is genuinely short, so a lint
# that reports nothing fails too. The trailing-space line is built by
# concatenation, not inside the triple-quoted block, for the reason
# `_TRAILING_WHITESPACE_SPEC` gives -- a whitespace filter eats it otherwise.
_REDUCTION_OPERAND_SPACING_SPEC = (
    "# Fixture Spec — reduction lines spaced other than exactly one space\n"
    "\n"
    "LEVEL: packed_red\n"
    "ADMITS:any, all\n"
    "NON-UNIFORMITY: packed_red — fixture set has >=2 members, one violating, plus control\n"
    "EXCLUDES:any, all\n"
    "\n"
    "LEVEL: padded_red\n"
    "ADMITS:   any, all\n"
    "NON-UNIFORMITY: padded_red — fixture set has >=2 members, one violating, plus control\n"
    "EXCLUDES:   any, all\n"
    "\n"
    "LEVEL: trailing_red\n"
    "NON-UNIFORMITY: trailing_red — fixture set has >=2 members, one violating, plus control\n"
    "EXCLUDES: any, all, first, last   \n"
    "\n"
    "LEVEL: control_red\n"
    "NON-UNIFORMITY: control_red — fixture set has >=2 members, one violating, plus control\n"
    "EXCLUDES: any, all\n"
)

# ── MAJOR-3: `[G24:8]`'s line-start clause covers EIGHT markers ────────────
# `[G24:8]` pins "markers are recognised at line start, and an indented marker
# is prose" over all eight. Exactly three indented marker-shaped lines existed
# in the whole RED: `LEVEL:` and `SEAM:` in `_INDENTED_MARKER_SPEC`, and one
# `Excludes:` in `_ROW_MARKER_CASE_SPEC`. The v8 argument that produced that
# third one was that "a lint using `line.startswith` for declarations and
# `line.strip().startswith` FOR THE REST" survived -- and "the rest" is five
# markers, of which one got a fixture.
#
# So a lint recognising the three PROPERTY markers after a strip, while
# declarations and reduction lines require flush-left, passes 48/48. Verified
# before this fixture. And it is wrong in the exact direction §0.2 exists to
# prevent: an indented `NORMALISATION:` quoted inside prose after a bare `SEAM:`
# silently COMPLETES the seam and suppresses the finding -- a seam declared and
# not pinned, shipped conformant, which is this AC's founding case.
#
# `Bare.Quoted` must still be flagged, `quoted_row_level` must still be flagged
# (the indented row markers discharge nothing), and the flush-left
# `Flush.Conformant` must not be -- so a lint that simply flags every seam is
# caught as well.
_INDENTED_PROPERTY_SPEC = """\
# Fixture Spec — indented property and row markers, quoted inside prose

SEAM: Bare.Quoted

A worked example, quoted at an indent so that it declares nothing:

    ATTRIBUTE-PATH: pathlib.Path.read_text
    BINDING-TIME: call-time
    NORMALISATION: none

LEVEL: quoted_row_level

and the same for a row, quoted the same way:

    NON-UNIFORMITY: quoted_row_level — fixture set has >=2 members, one violating, plus control
    EXCLUDES: any, all, first, last

SEAM: Flush.Conformant
ATTRIBUTE-PATH: subprocess.run
BINDING-TIME: call-time
NORMALISATION: none
"""


class TestQuantifierCompletenessLintBd39GateRound1:
    """bd#39 gate round 1: `[G24:7]` and `[G24:8]` each pinned over more
    markers than they were measured on.
    """

    def test_ac_c5_reduction_line_operand_framing(self):
        """AC-C5(2). bd#39 gate round-1 MAJOR-2, spec C2-30. `[G24:7]` pins
        the operand's framing for FIVE operand-bearing markers and was
        measured on three: `LEVEL` and `SEAM` (`_TRAILING_WHITESPACE_SPEC`,
        `_OPERAND_SPACING_SPEC`) and `NON-UNIFORMITY`
        (`_ROW_OPERAND_SPACING_SPEC`). Every reduction line in the file wrote
        exactly one space after its colon, so a fixed offset applied to
        reduction lines alone survived all 48 tests.

        `packed_red` and `padded_red` are conformant only if `ADMITS:` and
        `EXCLUDES:` are parsed rather than sliced: a fixed offset turns
        `ADMITS:any, all` into the tokens `ny`/`all`, which `[G24:1]` then
        makes a finding -- a `Finding` about a conformant document.
        `trailing_red` covers all four with trailing spaces on the line, so a
        lint that does not rstrip reads the last token as `last␣␣␣` and flags
        it. `control_red` is genuinely short by two, so a lint that reports
        nothing at all fails the last assertion rather than passing by
        silence.
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_REDUCTION_OPERAND_SPACING_SPEC)

        assert not any(f.subject == "packed_red" for f in findings)
        assert not any(f.subject == "padded_red" for f in findings)
        assert not any(f.subject == "trailing_red" for f in findings)
        assert any(
            f.kind == "missing_reductions" and f.subject == "control_red"
            for f in findings
        )
        assert not any(f.kind == "missing_non_uniformity_row" for f in findings)

    def test_ac_c5_indented_property_and_row_markers_are_prose(self):
        """AC-C5(1) and AC-C5(3). bd#39 gate round-1 MAJOR-3, spec C3-21.
        `[G24:8]` pins line-start recognition over all EIGHT markers and the
        whole RED contained three indented marker-shaped lines: `LEVEL:` and
        `SEAM:` in `_INDENTED_MARKER_SPEC`, and one `Excludes:` in
        `_ROW_MARKER_CASE_SPEC`. The v8 argument that produced that third one
        named "the rest" -- five markers -- and gave one of them a fixture.

        A lint recognising the three PROPERTY markers after a strip, while
        requiring declarations and reduction lines to be flush-left, therefore
        passed all 48. It is wrong in the direction §0.2 exists to prevent:
        the indented `ATTRIBUTE-PATH:`/`BINDING-TIME:`/`NORMALISATION:` quoted
        below `Bare.Quoted` silently COMPLETE that seam, suppressing the
        finding -- a seam declared and not pinned, shipped conformant, which
        is this AC's founding case. The indented row markers below
        `quoted_row_level` carry the same test for check 1.
        `Flush.Conformant` is declared flush-left with all three properties
        and must not be flagged, so a lint that flags every seam is caught
        too.
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_INDENTED_PROPERTY_SPEC)

        assert any(
            f.kind == "seam_not_pinned" and f.subject == "Bare.Quoted"
            for f in findings
        )
        assert any(
            f.kind == "missing_non_uniformity_row" and f.subject == "quoted_row_level"
            for f in findings
        )
        assert not any(f.subject == "Flush.Conformant" for f in findings)


# =========================================================================
# PART 10 -- bd#39 gate round 2. Two MAJORs, plus the four adversarial edges
# the gate raised twice and said it would not restate.
# =========================================================================

# ── MAJOR-1 (type (a), against the row THIS lot rewrote last round) ────────
# v2's `[G24:8]` row declares the axis as "all EIGHT markers" and then names
# seven: LEVEL and SEAM, EXCLUDES, the three property markers, NON-UNIFORMITY.
# `ADMITS` is in no cell and in no fixture -- there is no indented `ADMITS:`
# anywhere in the file. That is a fix leaving one member of its own enumeration
# unfilled while declaring the enumeration complete: the shape the parent's exit
# criterion turned on, now inside the correction written to close round 1.
#
# `ADMITS:` gets its own dispatch branch in any natural line classifier (the
# three property markers share one, which is why C3-21's mutant is one line --
# `ADMITS`' is likewise one), and this lot has now found `ADMITS` to be the
# separately-handled marker FOUR times: its base case, its duplicate rule, its
# distance axis, its framing cell. Assuming it covered by the branch beside it
# is the inference all four rejected. Confirmed: `indented_admits` passed 50/50.
#
# The consequence is the suppression class one marker over from C3-21: a quoted
# `ADMITS: any, all` NARROWS the admitted set of the level above it, so a
# genuinely short row reads as complete and its finding is suppressed. A worked
# example quoted in prose silences a real finding about the level above it.
_INDENTED_ADMITS_SPEC = """\
# Fixture Spec — an ADMITS line quoted at an indent inside prose

LEVEL: quoted_admits_level

A worked example, quoted at an indent so that it declares nothing:

    ADMITS: any, all

NON-UNIFORMITY: quoted_admits_level — fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all

LEVEL: flush_control
ADMITS: any, all
NON-UNIFORMITY: flush_control — fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all
"""

# ── MAJOR-2: `[G24:3]`'s (different kind, same subject) cell ───────────────
# `[G24:3]` pins "at most ONE finding per (kind, subject) PAIR". A pair is two
# axes, and §5 recorded the clause as a single cell -- which §0.9(3a) says is
# the claim under audit. The (different kind, same subject) cell was empty by
# construction: level names in this file are bare identifiers and seam names are
# dotted, so no name was ever both, and nothing distinguished collapse-by-pair
# from collapse-by-subject. `dedup_by_subject_only` confirmed at 50/50.
#
# A collapse step MUST exist -- that is why the clause was minted, since
# `per_missing_reduction` and `per_missing_property` are both plausible -- and
# keying it on `subject` alone is one of exactly two natural spellings of it.
# It silently drops one of two real findings, and WHICH one depends on emission
# order, which §2.5 leaves unspecified across inputs.
_SAME_SUBJECT_TWO_KINDS_SPEC = """\
# Fixture Spec — one name declared as both a LEVEL and a SEAM

LEVEL: cache

SEAM: cache

LEVEL: distinct_control
NON-UNIFORMITY: distinct_control — fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all, first, last

SEAM: Distinct.Control
ATTRIBUTE-PATH: pathlib.Path.read_text
BINDING-TIME: call-time
NORMALISATION: none
"""

# ── The four adversarial edges, closed rather than restated a third time ───
# `[G24:11]` pins what the delimiters ARE, which is the same move `[G24:7]`
# made for the operand's framing: the token separator is a comma with optional
# surrounding whitespace, and a row's `<level>` operand is the text up to the
# FIRST em dash, or the whole operand when there is none.
#
# `no_space_tokens` is conformant only if the separator is the comma and not
# the two-character `", "` -- a `split(", ")` lint reads one token
# `any,all,first,last`, unrecognised under `[G24:1]`, and flags a conformant
# row. `no_dash_row` carries no em dash at all: a lint requiring one binds the
# row to nothing and reports the level as row-less. `two_dash_row` has a second
# em dash inside its description: a lint splitting on the LAST dash takes the
# operand `two_dash_row — fixture set is non-uniform`, which matches no level,
# so again the level is reported row-less. `delim_short` is the positive
# discriminator.
_REDUCTION_DELIMITER_SPEC = """\
# Fixture Spec — token separators and row-operand delimiters

LEVEL: no_space_tokens
NON-UNIFORMITY: no_space_tokens — fixture set has >=2 members, one violating, plus control
EXCLUDES: any,all,first,last

LEVEL: no_dash_row
NON-UNIFORMITY: no_dash_row
EXCLUDES: any, all, first, last

LEVEL: two_dash_row
NON-UNIFORMITY: two_dash_row — fixture set is non-uniform — in both orderings
EXCLUDES: any, all, first, last

LEVEL: delim_short
NON-UNIFORMITY: delim_short — fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all
"""


class TestQuantifierCompletenessLintBd39GateRound2:
    """`[G24:8]`'s eighth marker, `[G24:3]`'s second axis, and `[G24:11]`."""

    def test_ac_c5_indented_admits_line_is_prose(self):
        """AC-C5(2). bd#39 gate round-2 MAJOR-1, spec C2-31. Type **(b)** under the
        sharpened criterion (§0.0): the fix completes the enumeration §0.0's
        standing instrument already demanded.
        v2's `[G24:8]` row declared "all eight markers" and enumerated seven --
        no indented `ADMITS:` existed anywhere in the file, so a lint
        recognising `ADMITS:` after a strip while requiring the other seven
        flush-left passed 50/50.

        `quoted_admits_level` has no `ADMITS` of its own once the indented one
        is read as prose, so it takes the default four and its two-token
        `EXCLUDES` is short: it MUST be flagged. Under the surviving lint the
        quoted `ADMITS` narrows its admitted set to {any, all}, the row reads
        as complete, and the finding is suppressed -- a worked example quoted
        in prose silencing a real finding about the level above it, which is
        C3-21's suppression class one marker over. `flush_control` declares
        the same `ADMITS` flush-left and is genuinely conformant, so a lint
        that ignores `ADMITS` everywhere is caught by the second assertion.
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_INDENTED_ADMITS_SPEC)

        assert any(
            f.kind == "missing_reductions" and f.subject == "quoted_admits_level"
            for f in findings
        )
        assert not any(f.subject == "flush_control" for f in findings)
        assert not any(f.kind == "missing_non_uniformity_row" for f in findings)

    def test_ac_c5_same_subject_different_kinds_are_not_collapsed(self):
        """AC-C5(1) and AC-C5(3). bd#39 gate round-2 MAJOR-2, spec C-COLLAPSE.
        `[G24:3]` pins one finding per `(kind, subject)` PAIR -- two axes -- and
        the (different kind, same subject) cell was empty by construction:
        every level name in the file is a bare identifier and every seam name
        is dotted, so no name was ever both, and collapse-by-pair was
        indistinguishable from collapse-by-`subject`.

        `cache` is declared as a row-less `LEVEL` and as a bare `SEAM`, so the
        correct output carries BOTH `("missing_non_uniformity_row", "cache")`
        and `("seam_not_pinned", "cache")`. A lint keying its collapse on
        `subject` alone emits one and silently drops the other -- and which one
        it drops depends on emission order, which §2.5 leaves unspecified
        across inputs, so the defect is non-deterministic in which real finding
        it suppresses. The two controls are conformant, so a lint that flags
        everything is caught as well.
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_SAME_SUBJECT_TWO_KINDS_SPEC)

        assert any(
            f.kind == "missing_non_uniformity_row" and f.subject == "cache"
            for f in findings
        )
        assert any(
            f.kind == "seam_not_pinned" and f.subject == "cache"
            for f in findings
        )
        assert not any(f.subject == "distinct_control" for f in findings)
        assert not any(f.subject == "Distinct.Control" for f in findings)

    def test_ac_c5_token_and_row_operand_delimiters(self):
        """AC-C5(1) and AC-C5(2). Spec `[G24:11]` / C2-32, closing the four
        adversarial edges the gate raised in bd#39 rounds 1 and 2 and declined
        to restate a third time. `[G24:11]` pins what the delimiters ARE --
        the same move `[G24:7]` made for the operand's framing: the token
        separator is a **comma with optional surrounding whitespace**, and a
        row's `<level>` operand is the text up to the **first** em dash, or the
        whole operand when there is none.

        `no_space_tokens` writes `any,all,first,last`: conformant only if the
        separator is the comma, since a `split(", ")` lint reads one
        unrecognised token and flags a conformant row -- and that is not a
        contrived spelling, it is what this lot's own confirmed mutant
        `fixed_offset_reduction_operand` used. `no_dash_row` carries no em dash
        at all, so a lint requiring one binds the row to nothing and reports
        the level as row-less. `two_dash_row` carries a second em dash inside
        its description, so a lint splitting on the last dash takes an operand
        matching no level, with the same consequence. `delim_short` is the
        positive discriminator, and the final assertion catches all three
        binding failures at once, since every level here carries a row.
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_REDUCTION_DELIMITER_SPEC)

        assert not any(f.subject == "no_space_tokens" for f in findings)
        assert not any(f.subject == "no_dash_row" for f in findings)
        assert not any(f.subject == "two_dash_row" for f in findings)
        assert any(
            f.kind == "missing_reductions" and f.subject == "delim_short"
            for f in findings
        )
        assert not any(f.kind == "missing_non_uniformity_row" for f in findings)


# =========================================================================
# PART 11 -- bd#39 gate round 3. One type (a) against the clause round 2
# wrote, and one CONTRADICTION between two frozen normative sentences.
# =========================================================================

# ── MAJOR-1 (type (a)): `[G24:11]` says "SURROUNDING whitespace" ───────────
# and measured one side. Every reduction line in all 48 fixtures writes either
# `tok, tok` or `tok,tok`; not one contains ` ,`. That is bd#24 gate round 4's
# MAJOR verbatim -- `[G24:7]` pinned "surrounding whitespace" and measured the
# trailing side only -- reproduced on the clause written next to it, and it is
# the finding the parent's exit criterion fired on. §0.0 carries the resolution
# as this lot's constitution: MEASURE THE SECOND SIDE, do not narrow.
#
# `separator_is_comma_space` (`operand.replace(", ", ",").split(",")`) is the
# natural spelling for a clause admitting both forms and handles every line in
# the file. Confirmed at 53/53. On `any , all` it yields `any ` and ` all`,
# neither recognised, so a FULLY CONFORMANT row is reported `missing_reductions`
# on two independent grounds -- F-1's over-firing class, on a spelling a
# hand-written fixture document carries.
#
# `trailing_comma` closes the nearest live neighbour the gate raised in the
# same breath: `[G24:11]` makes the separator the comma, so a trailing one
# yields an empty final token. §6 declares an EMPTY token LIST unpinned, not an
# empty token within a non-empty one; v4 pins it (empty tokens are ignored) and
# this row measures it.
_SEPARATOR_SIDES_SPEC = """\
# Fixture Spec — whitespace before the comma, and a trailing comma

LEVEL: before_comma
NON-UNIFORMITY: before_comma — fixture set has >=2 members, one violating, plus control
EXCLUDES: any ,all , first,last

LEVEL: trailing_comma
NON-UNIFORMITY: trailing_comma — fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all, first, last,

LEVEL: sep_short
NON-UNIFORMITY: sep_short — fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all
"""

# ── MAJOR-2: the contradiction, and WHEN a token is validated ──────────────
# Two frozen normative sentences disagreed. §2.4 `[G24:1]`: "an unrecognised
# token in `ADMITS` makes the admitted set uncomputable and IS ITSELF a
# `missing_reductions` finding on that level" -- no precondition, and its
# rationale is that there is nothing to compute. §2.6: "a level with no row
# yields check 1 ONLY (there is no row to check for reductions)" -- which
# forbids exactly that finding. On `LEVEL: x` / `ADMITS: bogus` / no row the two
# sentences require opposite outputs, and NO fixture reached that input: every
# level carrying an invalid `ADMITS` also carried a row.
#
# Decided in spec v4 for §2.4's reading, because its rationale survives the
# absence of a row and §2.6's does not: an uncomputable admitted set is a defect
# of the LEVEL, not of a row it may or may not have. §2.6's independence
# sentence is narrowed to say so.
#
# The second half is WHEN validation happens. `[G24:5]` forbids inventing a
# finding from an unanchored line, so tokens are validated ON THE BOUND LEVEL,
# never eagerly as each line is read: `eager_token_validation` emits
# `Finding("missing_reductions", cur_level or "")` from an orphan -- C2-26
# exactly, one clause over, and C2-26 was gate round 6's MAJOR.
#
# The assertion is an EXACT SET, so it constrains what else the list may
# contain: `admits_token_needs_row` drops a required finding and
# `eager_token_validation` adds two forbidden ones.
_TOKEN_VALIDATION_SPEC = """\
# Fixture Spec — when a reduction token is validated, and on what

ADMITS: orphan_bogus, all
EXCLUDES: also_bogus, all

LEVEL: bad_admits_no_row
ADMITS: any, bogus

LEVEL: valid_control
ADMITS: any, all
NON-UNIFORMITY: valid_control — fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all
"""


class TestQuantifierCompletenessLintBd39GateRound3:
    """`[G24:11]`'s second side, and the §2.4/§2.6 contradiction."""

    def test_ac_c5_whitespace_before_the_comma_and_a_trailing_comma(self):
        """AC-C5(2). bd#39 gate round-3 MAJOR-1, spec C2-33. Type **(b)** under the
        sharpened criterion (§0.0): the fix does exactly what §0.0 already
        required -- measure the second side, never narrow -- so the basis is
        confirmed, not overturned.
        `[G24:11]` pins the separator as "a comma with SURROUNDING whitespace"
        and the fixtures measured `tok, tok` and `tok,tok` -- never ` ,`. That
        is bd#24 gate round 4's MAJOR reproduced on the clause written beside
        it, and §0.0 carries its resolution as this lot's constitution:
        measure the second side, do not narrow.

        `before_comma` mixes ` ,` and ` , ` and covers all four, so it is
        conformant -- `separator_is_comma_space`
        (`replace(", ", ",").split(",")`) reads `any ` and `all `, neither
        recognised, and reports a conformant row on two independent grounds.
        `trailing_comma` ends its list with a comma, so the final token is
        empty and must be ignored (v4's `[G24:11]` extension; §6 declared an
        empty token LIST unpinned, not an empty token inside a non-empty one).
        `sep_short` is the positive discriminator and the last assertion
        catches the check-2 to check-1 inversion.
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_SEPARATOR_SIDES_SPEC)

        assert not any(f.subject == "before_comma" for f in findings)
        assert not any(f.subject == "trailing_comma" for f in findings)
        assert any(
            f.kind == "missing_reductions" and f.subject == "sep_short"
            for f in findings
        )
        assert not any(f.kind == "missing_non_uniformity_row" for f in findings)

    def test_ac_c5_invalid_token_is_validated_on_its_bound_level(self):
        """AC-C5(1) and AC-C5(2). bd#39 gate round-3 MAJOR-2, spec C2-34,
        closing a CONTRADICTION between two frozen normative sentences rather
        than a coverage gap. §2.4 made an unrecognised `ADMITS` token "itself a
        `missing_reductions` finding on that level" with no precondition; §2.6
        said a level with no row yields check 1 "only". On `LEVEL: x` /
        `ADMITS: bogus` / no row the two required opposite outputs, and no
        fixture reached that input -- every level with an invalid `ADMITS` also
        carried a row, so the contradiction was invisible to the RED, to the
        containment check and to the mutant matrix.

        Spec v4 decides for §2.4: an uncomputable admitted set is a defect of
        the LEVEL, and that rationale survives the absence of a row where
        §2.6's does not. So `bad_admits_no_row` yields BOTH findings -- check 1
        for the missing row, check 2 for the uncomputable set -- which is also
        the (different kind, same subject) cell of `[G24:3]`, asserted here a
        second time from a different direction.

        The second half is WHEN validation happens. `[G24:5]` forbids inventing
        a finding from an unanchored line, so tokens are validated on the bound
        level, never eagerly: the two orphan lines here each carry an invalid
        token and must produce nothing. The assertion is an EXACT SET because
        both surviving implementations are invisible to `any(...)` --
        `admits_token_needs_row` drops a required finding, `eager_token_validation`
        adds forbidden ones with an empty or borrowed `subject`.
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_TOKEN_VALIDATION_SPEC)

        assert {(f.kind, f.subject) for f in findings} == {
            ("missing_non_uniformity_row", "bad_admits_no_row"),
            ("missing_reductions", "bad_admits_no_row"),
        }


# =========================================================================
# PART 12 -- bd#39 gate round 4. One inherited, one type (a) against v4's
# own extension.
# =========================================================================

# ── MAJOR-1: `[G24:9]` has TWO consequences and one was measured ───────────
# "Position is not part of the binding" has two observable consequences: the
# level is DISCHARGED (check 1 silent), and the level's `ADMITS` GOVERNS that
# row's coverage (check 2 evaluated against it). `_ROW_BEFORE_LEVEL_SPEC`
# measures the first only -- `late_level` carries no `ADMITS` and covers all
# four, which is also the default, so the second is satisfied identically under
# either evaluation order. That is `[G24:5]`'s 3x2 correction one clause over.
#
# Verified empty across the set: for the two readings to diverge a document
# needs a row whose `EXCLUDES` is read BEFORE its level's `ADMITS` is declared,
# with that `ADMITS` narrower than four. In every fixture carrying an `ADMITS`
# the line precedes the `EXCLUDES` it governs -- including `_ANCHOR_CYCLE_SPEC`,
# where the `ADMITS` sits after the row but still before its `EXCLUDES`.
#
# `coverage_in_reading_order` -- check 2 evaluated inline against the admitted
# set as known at that point -- passed 55/55. It is this lot's dominant
# candidate family (C2-10, C2-27, C2-28, C3-19, C2-29 were all single-pass
# "current declaration" shapes), applied to the comparison rather than to the
# binding, and it over-fires on a document shape `[G24:9]` exists to declare
# legal.
_ROW_ORDER_COVERAGE_SPEC = """\
# Fixture Spec — a row and its EXCLUDES above the LEVEL whose ADMITS governs them

NON-UNIFORMITY: late_admits — fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all

LEVEL: late_admits
ADMITS: any, all

LEVEL: order_short
ADMITS: any, all, first, last
NON-UNIFORMITY: order_short — fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all
"""

# ── MAJOR-2 (type (a)): v4's "an empty token is ignored" ───────────────────
# The extension is right for the trailing comma, which `_SEPARATOR_SIDES_SPEC`
# measures. But it was stated over tokens IN GENERAL and so reached two inputs
# it was not written for, silently and in the PERMISSIVE direction:
#
#   `EXCLUDES:` with an empty list -- which §6 still declared UNPINNED, so the
#   spec simultaneously decided the input and declared it undecided;
#   `ADMITS:` with an empty list -- and this is the one that bites. §2.2 pins
#   "ABSENT means all four"; a bare `ADMITS:` is PRESENT. Under v4's literal
#   reading its token set is empty, so `set(excludes) >= set()` holds for EVERY
#   row and the level's row is conformant no matter how short. An
#   under-enumeration ships green -- the AC's founding defect, produced by the
#   clause `[G24:1]`'s symmetric rule was minted to prevent.
#
# Before v4 both had a determinate and SAFE answer: the empty string was a token
# outside the four, so `[G24:1]` made it a finding. v4 changed that answer with
# no §3 row and no coverage diff. Both readings confirmed at 55/55.
#
# `[G24:14]` decides it in the safe direction: an empty token list is NOT an
# empty admitted set. For `ADMITS` it is treated as ABSENT (default four); for
# `EXCLUDES` it is no coverage, so the row is short.
_EMPTY_TOKEN_LIST_SPEC = """\
# Fixture Spec — marker lines whose operand contributes zero tokens

LEVEL: bare_admits
ADMITS:
NON-UNIFORMITY: bare_admits — fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all

LEVEL: bare_excludes
NON-UNIFORMITY: bare_excludes — fixture set has >=2 members, one violating, plus control
EXCLUDES:

LEVEL: empty_control
NON-UNIFORMITY: empty_control — fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all, first, last
"""


class TestQuantifierCompletenessLintBd39GateRound4:
    """`[G24:9]`'s coverage consequence, and `[G24:14]`'s empty token list."""

    def test_ac_c5_admits_governs_coverage_regardless_of_reading_order(self):
        """AC-C5(2). bd#39 gate round-4 MAJOR-1, spec C2-35. `[G24:9]` says
        position is not part of the binding, which has TWO consequences: the
        level is discharged, and its `ADMITS` governs that row's coverage.
        `_ROW_BEFORE_LEVEL_SPEC` measures the first only, because `late_level`
        has no `ADMITS` and covers all four -- the default -- so the second is
        satisfied identically under either evaluation order.

        Here the row and its `EXCLUDES` are read before `late_admits` is
        declared, and that level admits only {any, all}, which the row covers
        exactly: conformant. A lint evaluating coverage inline, against the
        admitted set as known when the `EXCLUDES` arrives, sees the default
        four and flags a conformant row -- F-1's over-firing class on a
        document shape `[G24:9]` exists to declare legal. `order_short` is the
        positive discriminator and the last assertion catches the check-2 to
        check-1 inversion.
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_ROW_ORDER_COVERAGE_SPEC)

        assert not any(f.subject == "late_admits" for f in findings)
        assert any(
            f.kind == "missing_reductions" and f.subject == "order_short"
            for f in findings
        )
        assert not any(f.kind == "missing_non_uniformity_row" for f in findings)

    def test_ac_c5_empty_token_list_is_not_an_empty_admitted_set(self):
        """AC-C5(2). bd#39 gate round-4 MAJOR-2, spec `[G24:14]` / C2-36. Type **(b)**
        under the sharpened criterion (§0.0): the fix restores the safe reading
        `[G24:1]` was minted for and leaves `[G24:11]`'s trailing-comma rule
        intact, so the basis stands. v4 extended `[G24:11]` with "an empty token is ignored" -- right
        for the trailing comma it was written for, and stated over tokens in
        general, so it reached two inputs it was not written for and moved both
        in the PERMISSIVE direction with no §3 row and no coverage diff.

        A bare `ADMITS:` is PRESENT, not absent, so under the literal v4
        reading its admitted set is empty and `set(excludes) >= set()` holds
        for every row -- a level whose rows are conformant however short they
        are, which is this AC's founding defect produced by the clause
        `[G24:1]` was minted to prevent. `[G24:14]` decides it in the safe
        direction: an empty token list is not an empty admitted set. For
        `ADMITS` it reads as ABSENT, so `bare_admits` takes the default four
        and its two-token row is short. For `EXCLUDES` it is no coverage, so
        `bare_excludes` is short too. Both must be flagged; `empty_control`
        must not, so a lint that flags every row fails as well.
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_EMPTY_TOKEN_LIST_SPEC)

        assert any(
            f.kind == "missing_reductions" and f.subject == "bare_admits"
            for f in findings
        )
        assert any(
            f.kind == "missing_reductions" and f.subject == "bare_excludes"
            for f in findings
        )
        assert not any(f.subject == "empty_control" for f in findings)
        assert not any(f.kind == "missing_non_uniformity_row" for f in findings)


# ── bd#39 gate round 5 MAJOR: a property line with an EMPTY operand ────────
# The empty-operand question was answered for four of the eight markers and for
# none of the other four. `[G24:14]` (v5) covers `ADMITS:`/`EXCLUDES:`; §6
# defers `LEVEL:`/`SEAM: ` with a stated reason; the three PROPERTY markers were
# governed by two frozen sentences requiring opposite outputs:
#
#   §2.6's check-3 row and `[G24:4]` are worded over marker PRESENCE -- a bare
#   `ATTRIBUTE-PATH:` is present, so the property is pinned, so no finding;
#   §0.2 (inherited, and this lot's own subject matter) says naming a seam is
#   not enough and the interception property MUST BE PINNED -- a bare
#   `ATTRIBUTE-PATH:` pins nothing, so the seam is still missing it.
#
# Zero fixtures carried a property line with an empty operand, so neither
# reading was falsifiable; both confirmed at 57/57.
#
# The permissive reading is not a harmless corner. On a seam with all three
# markers laid out and none filled it returns NO FINDINGS: a seam declared,
# nothing pinned, shipped conformant -- §0.2's founding case verbatim, the same
# outcome `_SEAM_NOT_PINNED_SPEC` (C3-1) exists to prevent, reached by a route
# that fixture does not cover because its bare seam has ZERO property lines
# rather than three empty ones. A document written by hand as a TEMPLATE, with
# the three markers laid out to be filled in, is exactly this input.
#
# §6 could not absorb it either: its empty-operand bullet defers
# `LEVEL:`/`SEAM: ` because `Finding.subject` would become the empty string, and
# a property-line operand never becomes a subject -- so the reason does not
# transfer and the case was silently unpinned, which is the state §6's own
# preamble exists to prevent.
#
# `[G24:15]` decides it where §0.2 and `[G24:14]` both point: a property line
# whose operand is empty or whitespace-only DOES NOT PIN its property. §2.6's
# check-3 row and `[G24:4]` are reworded from "markers present" to "properties
# pinned", since the presence wording is what made the permissive reading
# literal.
_EMPTY_PROPERTY_OPERAND_SPEC = """\
# Fixture Spec — property markers laid out as a template, with no values

SEAM: tempfile.mkdtemp
ATTRIBUTE-PATH:
BINDING-TIME:
NORMALISATION:

SEAM: Partial.Pinned
ATTRIBUTE-PATH: pathlib.Path.read_text
BINDING-TIME:
NORMALISATION: none

SEAM: Fully.Pinned
ATTRIBUTE-PATH: subprocess.run
BINDING-TIME: call-time
NORMALISATION: none
"""


class TestQuantifierCompletenessLintBd39GateRound5:
    """`[G24:15]`: a property marker present but unfilled pins nothing."""

    def test_ac_c5_property_line_with_an_empty_operand_pins_nothing(self):
        """AC-C5(3). bd#39 gate round-5 MAJOR, spec `[G24:15]` / C3-22. Two
        frozen sentences required opposite outputs on a bare
        `ATTRIBUTE-PATH:` -- §2.6's check-3 row and `[G24:4]` are worded over
        marker PRESENCE, while §0.2 requires the interception property to be
        PINNED -- and no fixture carried a property line with an empty operand,
        so neither reading was falsifiable. Both passed 57/57.

        `tempfile.mkdtemp` is the case that makes this MAJOR rather than a §6
        corner: all three markers laid out, none filled, which the presence
        reading returns as fully conformant. That is §0.2's founding case
        verbatim -- a seam declared and nothing pinned -- reached by a route
        `_SEAM_NOT_PINNED_SPEC` does not cover, since its bare seam has zero
        property lines rather than three empty ones. A hand-written TEMPLATE,
        with the markers laid out to be filled in, is exactly this document.

        `Partial.Pinned` carries one empty among two filled, which
        discriminates the general rule from the all-three-empty special case:
        a lint that only rejects wholly-empty blocks passes the first
        assertion and fails the second. `Fully.Pinned` holds the negative, so
        a lint that flags every seam is caught too.
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_EMPTY_PROPERTY_OPERAND_SPEC)

        assert any(
            f.kind == "seam_not_pinned" and f.subject == "tempfile.mkdtemp"
            for f in findings
        )
        assert any(
            f.kind == "seam_not_pinned" and f.subject == "Partial.Pinned"
            for f in findings
        )
        assert not any(f.subject == "Fully.Pinned" for f in findings)


# ── bd#39 gate round 6 MAJOR (type (b)): the property WRITE POLICY ─────────
# Before v6 a property line's operand was never read, so check 3's store could
# be a SET of marker names -- `props.add(marker)` -- where repetition is
# idempotent by construction and `[G24:4]`'s "repetition is collapsed" needs no
# code at all. `[G24:15]` made the operand matter, so the natural structure
# becomes a MAPPING `marker -> operand`, and a mapping has a write policy that a
# set does not. Nothing pinned it and no fixture measured it: every duplicated
# property marker in the RED has BOTH occurrences filled.
#
# `last_wins_property_value` (`props[marker] = operand`, the natural spelling of
# a mapping write) and `first_wins_property_value` (`setdefault`) each passed
# 58/58. The correct answer IS derivable -- `[G24:4]` says repetition is
# "collapsed, NEVER PUNISHED" and "a repeated line is not itself a finding", and
# an unfilled line pins nothing but does not UNPIN what a filled one pinned --
# so this is not a contradiction between clauses. It is the other class: a
# clause whose wording reaches an input it was not written for, creating an
# implementation decision point that did not exist one revision earlier.
#
# `[G24:15]` v7 pins it: a property is pinned if ANY of its lines carries a
# non-empty operand, and an unfilled line never unpins a filled one. Both
# ORDERS are measured, per §0.8 rule 1's own both-orderings discipline, because
# last-wins and first-wins fail on opposite orders.
_PROPERTY_WRITE_POLICY_SPEC = """\
# Fixture Spec — a property marker repeated, once filled and once not

SEAM: Filled.Then.Empty
ATTRIBUTE-PATH: pathlib.Path.read_text
BINDING-TIME: call-time
NORMALISATION: none
NORMALISATION:

SEAM: Empty.Then.Filled
ATTRIBUTE-PATH: subprocess.run
BINDING-TIME: call-time
NORMALISATION:
NORMALISATION: none

SEAM: Never.Filled
ATTRIBUTE-PATH: os.scandir
BINDING-TIME: call-time
NORMALISATION:
"""


class TestQuantifierCompletenessLintBd39GateRound6:
    """`[G24:15]`'s write policy: an unfilled line never unpins a filled one."""

    def test_ac_c5_an_unfilled_property_line_does_not_unpin_a_filled_one(self):
        """AC-C5(3). bd#39 gate round-6 MAJOR, spec `[G24:15]` v7 / C3-23.
        Type **(b)** under the sharpened criterion (§0.0): the fix pins the
        policy `[G24:4]` already implied, and `[G24:15]` itself stands. `[G24:15]` made a property line's operand matter, which turned
        check 3's store from a SET of markers -- where repetition is idempotent
        by construction -- into a MAPPING, which has a write policy. The policy
        was pinned nowhere and measured nowhere: every duplicated property
        marker in the file had both occurrences filled.

        `[G24:4]` settles the answer -- repetition is "collapsed, never
        punished" -- so a property is pinned if ANY of its lines carries a
        non-empty operand. `Filled.Then.Empty` kills `props[marker] = operand`
        (last-wins overwrites with the empty string and flags a conformant
        seam, resurrecting C2-23/C3-18's duplicate-punishing defect through a
        new mechanism, on the clause written to fix a different problem).
        `Empty.Then.Filled` kills `setdefault` (first-wins keeps the empty
        string), which fails only on the reverse order -- so both orders are
        required, exactly as §0.8 rule 1 demands of a fixture set.
        `Never.Filled` is the positive discriminator: its `NORMALISATION` is
        never filled at all, so it must be flagged under every reading.
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_PROPERTY_WRITE_POLICY_SPEC)

        assert not any(f.subject == "Filled.Then.Empty" for f in findings)
        assert not any(f.subject == "Empty.Then.Filled" for f in findings)
        assert any(
            f.kind == "seam_not_pinned" and f.subject == "Never.Filled"
            for f in findings
        )


# =========================================================================
# PART 13 -- bd#39 gate round 7. Two MAJORs, both type (b), both of the class
# the mutant matrix is STRUCTURALLY blind to: a candidate that diverges from
# the reference on ZERO fixtures, because no fixture reaches its input.
# =========================================================================

# ── MAJOR-1: `[G24:11]` names TWO delimiters and frames one ────────────────
# The token separator is pinned as "a comma with optional whitespace on either
# side of it" and measured on both sides since round 3. The row-operand
# delimiter -- "the text up to the first em dash" -- has NO whitespace framing
# pinned at all. That is §0.0's standing instrument with N = 2 and one side
# done, and every one of the 78 `NON-UNIFORMITY` lines in the file writes
# ` — ` (exactly one space each side) or carries no dash.
#
# `row_operand_dash_with_space` (`operand.split(" — ")[0]`) passed 59/59 at zero
# divergence. It is not contrived: §2.2's grammar table writes the shape with
# spaces, which is the same mechanism that made the *presence* wording literal
# in round 5, and it is this lot's own accepted defect twice over -- mutant #29's
# marker-plus-one-assumed-space, and round 3's `replace(", ", ",")`, which is
# `[G24:11]`'s OTHER half, fixed one revision ago while this half was left.
#
# `closed_dash` kills it: an em dash set closed-up is ordinary house style, and
# the survivor takes the whole operand, matches no declared level, and reports
# `missing_non_uniformity_row` on a conformant document. `padded_dash` kills the
# neighbouring variant that splits on the dash but does not strip.
_ROW_DASH_FRAMING_SPEC = """\
# Fixture Spec — the em dash set closed-up, and padded

LEVEL: closed_dash
NON-UNIFORMITY: closed_dash—fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all, first, last

LEVEL: padded_dash
NON-UNIFORMITY: padded_dash  —  fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all, first, last

LEVEL: dash_rowless
"""

# ── MAJOR-2: `[G24:15]` made the property markers OPERAND-BEARING ──────────
# Round 6 found that making a property operand matter created a write policy.
# It created something else from the same change: `[G24:7]`'s framing clause
# used to range over five operand-bearing markers, and since `[G24:15]` it
# ranges over EIGHT -- the three new ones contributing nine cells, all empty.
# §0.9(3a) verbatim: the row saying five is the claim under audit.
#
# `[G24:15]` itself names two spellings -- "empty OR WHITESPACE-ONLY" -- and the
# fixture set carried one. Across all 54 fixtures every property line was either
# `MARKER: value` (one space, nothing trailing) or `MARKER:` (bare colon).
#
# Two survivors, wrong in opposite directions, both 59/59 at zero divergence:
#   `whitespace_only_property_pins` (truthiness on the raw text, no strip) --
#   `NORMALISATION:␣` reports the property PINNED, so a hand-written template
#   whose author typed a trailing space rather than nothing ships conformant
#   with nothing pinned. That is §0.2's founding case, reached by the second
#   spelling `[G24:15]` itself names, one round after `[G24:15]` was minted to
#   prevent it by the first spelling.
#   `fixed_offset_property_operand` (marker plus one assumed space) -- this is
#   mutant #29, this lot's standing acceptance requirement, on the marker family
#   that only just became operand-bearing. #29 is re-run every round on
#   `LEVEL`/`SEAM` and cannot reach here.
#
# The whitespace-only line is built by concatenation, not inside a
# triple-quoted block, for the reason `_TRAILING_WHITESPACE_SPEC` gives.
_PROPERTY_OPERAND_FRAMING_SPEC = (
    "# Fixture Spec — property operands whitespace-only, and packed\n"
    "\n"
    "SEAM: Whitespace.Only\n"
    "ATTRIBUTE-PATH: pathlib.Path.read_text\n"
    "BINDING-TIME: call-time\n"
    "NORMALISATION:   \n"
    "\n"
    "SEAM: Packed.Properties\n"
    "ATTRIBUTE-PATH:pathlib.Path.read_text\n"
    "BINDING-TIME:call-time\n"
    "NORMALISATION:-\n"
    "\n"
    "SEAM: Framing.Control\n"
    "ATTRIBUTE-PATH: subprocess.run\n"
    "BINDING-TIME: call-time\n"
    "NORMALISATION: none\n"
)


class TestQuantifierCompletenessLintBd39GateRound7:
    """The em dash's whitespace framing, and the property markers' — both
    clauses reaching inputs no fixture carried.
    """

    def test_ac_c5_em_dash_framing_is_not_part_of_either_operand(self):
        """AC-C5(1). bd#39 gate round-7 MAJOR-1, spec `[G24:11]` v8 / C1-16.
        `[G24:11]` names two delimiters and frames one: the comma's whitespace
        is pinned on both sides and measured, the em dash's is pinned nowhere.
        All 78 `NON-UNIFORMITY` lines in the file write ` — ` or no dash at
        all, so `operand.split(" — ")[0]` passed 59/59 while diverging from
        the reference on ZERO fixtures.

        `closed_dash` writes the dash closed-up, which is ordinary house
        style: the survivor takes the whole operand, matches no declared
        level, and reports `missing_non_uniformity_row` on a conformant
        document -- F-1's over-firing class, against the row `[G24:11]` exists
        to bind. `padded_dash` writes it with two spaces each side and kills
        the neighbouring variant that splits on the dash without stripping.
        `dash_rowless` is the positive discriminator: it has no row at all, so
        a lint that reports nothing fails too.
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_ROW_DASH_FRAMING_SPEC)

        assert not any(f.subject == "closed_dash" for f in findings)
        assert not any(f.subject == "padded_dash" for f in findings)
        assert any(
            f.kind == "missing_non_uniformity_row" and f.subject == "dash_rowless"
            for f in findings
        )

    def test_ac_c5_property_operand_framing(self):
        """AC-C5(3). bd#39 gate round-7 MAJOR-2, spec `[G24:15]` v8 / C3-24.
        `[G24:15]` made property operands matter, which put the three property
        markers inside `[G24:7]`'s framing clause -- eight operand-bearing
        markers where §5 recorded five, nine cells all empty -- and named two
        spellings, "empty OR whitespace-only", of which the fixture set carried
        one.

        `Whitespace.Only`'s `NORMALISATION:` operand is spaces, so it must be
        flagged: a lint testing the raw text for non-emptiness reports it
        pinned, and a hand-written template whose author left a trailing space
        rather than nothing ships conformant with nothing pinned -- §0.2's
        founding case, by the second spelling `[G24:15]` itself names, one
        round after that clause was minted to prevent it by the first.
        `Packed.Properties` writes all three operands with no space after the
        colon and must NOT be flagged: a marker-plus-one-assumed-space offset
        reads its one-character `NORMALISATION` operand as empty and reports a
        fully pinned seam as unpinned. That is mutant #29 -- this lot's
        standing acceptance requirement -- on the marker family that only just
        became operand-bearing, which #29's own fixtures cannot reach.
        `Framing.Control` holds the ordinary spelling.
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_PROPERTY_OPERAND_FRAMING_SPEC)

        assert any(
            f.kind == "seam_not_pinned" and f.subject == "Whitespace.Only"
            for f in findings
        )
        assert not any(f.subject == "Packed.Properties" for f in findings)
        assert not any(f.subject == "Framing.Control" for f in findings)


# ── bd#39 gate round 8 MAJOR: the operand's INTERIOR, not its framing ──────
# `[G24:7]` pins the operand's FRAMING -- {terminator, trailing, leading} x 8
# markers, 24 cells, all filled by v8. P1 pins the operand's CONTENTS:
# "verbatim governs case and INTERIOR CHARACTERS, not the framing". Those are
# two axes of one question -- what the operand IS -- and §5's row enumerated
# only the first. §0.9(3a): the row's existing axis list is the claim under
# audit, never an exemption.
#
# Verified exhaustively: NO `LEVEL`, `SEAM` or row `<level>` operand in any of
# the 56 fixtures contains an interior space. `operand.split()[0]` -- take the
# first whitespace token -- therefore satisfies every one of the 24 framing
# cells BY ACCIDENT: tokenising discards leading and trailing whitespace, the
# packed form, and the line terminator (`\r` is whitespace, so `_CRLF_SPEC`
# passes). It passed 61/61 at ZERO divergence, the fourth consecutive round the
# matrix is structurally blind to the finding.
#
# §2.2 constrains `LEVEL: <name>` not at all, and §0.1's own directions are
# two-word phrases -- "element kind", "payload field" -- which this fixture set
# renders with underscores by house convention only. That is the same
# house-style argument §0.0 accepted for C-SPACING and the gate accepted for
# ` — `.
#
# Two independent consequences, and the silent one is graver:
#   `payload field` yields the subject `payload`, which appears nowhere in the
#   document -- mutant #29's consequence on the axis #29's fixtures cannot test;
#   `audit gate` and `audit step` collapse to one key, so `audit step` -- a
#   declared level with NO row -- is silently CLEARED. A level quantified over
#   and never discharged ships conformant: check 1's founding case.
_INTERIOR_SPACE_SPEC = """\
# Fixture Spec — interior spaces in level, seam and row operands

LEVEL: payload field

LEVEL: audit gate
NON-UNIFORMITY: audit gate — fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all, first, last

LEVEL: audit step

SEAM: Two Word.Seam
ATTRIBUTE-PATH: pathlib.Path.read_text
BINDING-TIME: call-time

SEAM: Interior.Control
ATTRIBUTE-PATH: subprocess.run
BINDING-TIME: call-time
NORMALISATION: none
"""


class TestQuantifierCompletenessLintBd39GateRound8:
    """P1's *interior characters* axis, which `[G24:7]`'s framing cells
    satisfy by accident because every operand in the file is one token.
    """

    def test_ac_c5_operand_interior_whitespace_is_part_of_the_name(self):
        """AC-C5(1) and AC-C5(3). bd#39 gate round-8 MAJOR, spec C1-17.
        `[G24:7]` pins the operand's FRAMING and P1 its CONTENTS -- two axes of
        one question, and §5 enumerated one. No `LEVEL`, `SEAM` or row operand
        in the file carries an interior space, so `operand.split()[0]`
        satisfies all 24 framing cells by accident and passed 61/61 at zero
        divergence.

        Three independent kills. `payload field` is row-less and must be
        reported with that exact literal: a tokenising lint reports `payload`,
        a subject appearing nowhere in the document -- mutant #29's
        consequence on the axis #29 cannot reach. `audit gate` and
        `audit step` share a first token and only the first has a row, so a
        tokenising lint collapses both to one key and silently CLEARS
        `audit step` -- a declared level, never discharged, shipped
        conformant, which is check 1's founding case and the graver half
        because it is invisible in the output rather than wrong in it.
        `Two Word.Seam` carries the same test on check 3's subject.
        `audit gate` and `Interior.Control` hold the negatives, so a lint that
        flags everything fails too.
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_INTERIOR_SPACE_SPEC)

        assert any(
            f.kind == "missing_non_uniformity_row" and f.subject == "payload field"
            for f in findings
        )
        assert any(
            f.kind == "missing_non_uniformity_row" and f.subject == "audit step"
            for f in findings
        )
        assert any(
            f.kind == "seam_not_pinned" and f.subject == "Two Word.Seam"
            for f in findings
        )
        assert not any(f.subject == "audit gate" for f in findings)
        assert not any(f.subject == "Interior.Control" for f in findings)


# ── bd#39 gate round 9 MAJOR: "EVERY character between the framing" ────────
# `[G24:16]` names two things: interior WHITESPACE explicitly, then the general
# rule -- "every character between the framing belongs to the name".
# `_INTERIOR_SPACE_SPEC` instantiates the narrow half three times and the
# general half never. That is `[G24:15]`'s "empty OR whitespace-only" shape
# verbatim -- two spellings in one clause, the fixture set following whichever
# the clause names first -- filed in round 7, closed in round 8, and reappearing
# inside the clause written in round 9 to close its predecessor.
#
# Verified exhaustively: no marker line in any of the 57 fixtures contains a
# SECOND COLON, in any case-spelling, indented or flush-left, in triple-quoted
# or concatenated form. So `line.split(":")[1]` -- the pinned expression minus
# its maxsplit argument, and the single most common spelling of "take the value
# from a key: value line" -- passed 62/62 at ZERO divergence. So did
# `" ".join(operand.split())`, which collapses runs of interior whitespace and
# which single-space names cannot separate.
#
# The colon spelling is not exotic in THIS AC's subject matter: it is about
# interception seams named by attribute path, and `pkg.module:function` is the
# setuptools entry-point convention while pytest node IDs use `::`. A
# hand-written document naming a seam is at least as likely to write
# `pathlib:Path.read_text` as `pathlib.Path.read_text`. Same house-convention
# argument §0.0 accepted for C-SPACING, the gate accepted for ` — `, and v9
# accepted for `payload field` -- except here the alternative is a live standard.
#
# Wrong in the same two directions, the silent one again graver: `pkg.mod:func`
# yields a subject absent from the document, and `pkg.mod:read` (fully pinned)
# collides with `pkg.mod:write` (missing NORMALISATION) so that the union of the
# two blocks is complete and a seam DECLARED AND NOT PINNED ships conformant --
# §0.2's founding case, invisible in the output rather than wrong in it.
_OPERAND_CHARACTER_SPEC = """\
# Fixture Spec — interior colons, and a doubled interior space

LEVEL: a:b

LEVEL: c:d
NON-UNIFORMITY: c:d — fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all, first, last

LEVEL: payload  field

SEAM: pkg.mod:func
ATTRIBUTE-PATH: pkg.mod:func
BINDING-TIME: call-time

SEAM: pkg.mod:read
ATTRIBUTE-PATH: pkg.mod:read
BINDING-TIME: call-time
NORMALISATION: none

SEAM: pkg.mod:write
ATTRIBUTE-PATH: pkg.mod:write
BINDING-TIME: call-time

SEAM: Char.Control
ATTRIBUTE-PATH: subprocess.run
BINDING-TIME: call-time
NORMALISATION: none
"""


class TestQuantifierCompletenessLintBd39GateRound9:
    """`[G24:16]`'s general half: every character, not only whitespace."""

    def test_ac_c5_operand_admits_every_interior_character(self):
        """AC-C5(1) and AC-C5(3). bd#39 gate round-9 MAJOR, spec `[G24:16]`
        v10 / C1-18. The clause names interior whitespace and then "every
        character between the framing"; the fixture set instantiated the first
        half only, which is `[G24:15]`'s two-spelling shape reappearing inside
        the clause written to close it.

        No marker line in the file carried a second colon, so
        `line.split(":")[1]` -- the pinned expression minus its maxsplit
        argument -- passed 62/62 at zero divergence, as did
        `" ".join(operand.split())`.

        `a:b` is row-less and must be reported with that exact literal;
        `c:d` has a row and must not be flagged, crossing the row-operand path.
        `payload  field` carries a DOUBLED interior space and must be reported
        verbatim, which is the cheapest possible test of "every character" and
        the only thing separating the whitespace-collapsing lint from the
        pinned rule. `pkg.mod:func` carries the subject half on check 3.
        `pkg.mod:read` and `pkg.mod:write` carry the COLLISION half: they share
        everything up to the colon, the first is fully pinned and the second is
        not, so a lint keying on the prefix sees the union as complete and
        `pkg.mod:write` -- declared and not pinned -- ships conformant. A
        negative on `pkg.mod:read` alone would not catch that; the positive on
        `pkg.mod:write` is what does.
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_OPERAND_CHARACTER_SPEC)

        assert any(
            f.kind == "missing_non_uniformity_row" and f.subject == "a:b"
            for f in findings
        )
        assert any(
            f.kind == "missing_non_uniformity_row" and f.subject == "payload  field"
            for f in findings
        )
        assert any(
            f.kind == "seam_not_pinned" and f.subject == "pkg.mod:func"
            for f in findings
        )
        assert any(
            f.kind == "seam_not_pinned" and f.subject == "pkg.mod:write"
            for f in findings
        )
        assert not any(f.subject == "c:d" for f in findings)
        assert not any(f.subject == "pkg.mod:read" for f in findings)
        assert not any(f.subject == "Char.Control" for f in findings)


# ── bd#39 gate round 10 MAJOR: a reduction token's INTERIOR ────────────────
# v10 recorded the reduction operands' interior cells "empty by construction",
# on the reason that they never become a `subject`. The reason is TRUE AND
# IRRELEVANT: for a reduction operand the interior determines RECOGNITION.
# `[G24:1]` pins the vocabulary as exactly any|all|first|last and makes any
# other token itself a finding, so whether a token's interior carries a space
# decides whether a finding fires and whether coverage is complete.
#
# Verified exhaustively: every token in every one of the 100+ reduction lines
# across all 58 fixtures is a contiguous run of non-space characters. Every
# space is adjacent to a comma, or the one after the colon, or trailing. So
# `operand.replace(" ", "").split(",")` passed 63/63 at ZERO divergence.
#
# THE FIXTURE SET STEERS TOWARD IT, which is what raises it above a curiosity.
# `_SEPARATOR_SIDES_SPEC` exists to kill `replace(", ", ",")` -- round 3's
# confirmed MAJOR. An implementer who fails that fixture reaches for the MORE
# AGGRESSIVE replace, because it handles ` ,`, `, `, ` , ` and `,` uniformly
# and passes everything. The RED's own corrective pressure points at the one
# spelling nothing measures.
#
# Wrong silently, in `[G24:1]`'s founding direction: `fi rst` is outside the
# four, so the pinned reading flags the row on two independent grounds -- an
# unrecognised token AND `first` uncovered -- while the survivor reads `first`,
# sees all four covered, and returns nothing. A mistyped reduction ships green,
# which is §2.4's own rationale verbatim: "the asymmetric reading is silent
# exactly when the row is otherwise complete." The input is the class this lot
# already fixtures deliberately -- `frist` (transposition) and `sometimes`
# (invented) -- with a different keystroke.
#
# The `ADMITS` half is carried in the same document because an `EXCLUDES`-only
# fixture would repeat gate round-5's MAJOR-3 exactly ("`[G24:4]` says ADMITS
# AND EXCLUDES and measured only EXCLUDES"), which this lot has now been bitten
# by four times.
_TOKEN_INTERIOR_SPEC = """\
# Fixture Spec — a reduction token carrying an interior space

LEVEL: excl_token_space
NON-UNIFORMITY: excl_token_space — fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all, fi rst, last

LEVEL: adm_token_space
ADMITS: an y, all
NON-UNIFORMITY: adm_token_space — fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all

LEVEL: token_control
NON-UNIFORMITY: token_control — fixture set has >=2 members, one violating, plus control
EXCLUDES: any, all, first, last
"""


class TestQuantifierCompletenessLintBd39GateRound10:
    """The reduction operands' interior — recognition, not `subject`."""

    def test_ac_c5_reduction_token_interior_space_is_not_removed(self):
        """AC-C5(2). bd#39 gate round-10 MAJOR, spec C2-37. The reduction
        operands' interior cells were recorded empty *by construction* on the
        reason that they never become a `subject` -- true, and irrelevant:
        their interior feeds RECOGNITION. `[G24:1]` makes a token outside the
        four itself a finding, so an interior space decides whether a finding
        fires. No reduction token in any of the 58 fixtures carried one, so
        `operand.replace(" ", "").split(",")` passed 63/63 at zero divergence.

        `excl_token_space` writes `fi rst`, which is outside the four: the
        pinned reading flags it on two independent grounds -- unrecognised
        token, and `first` uncovered -- while the survivor reads `first`, sees
        all four covered, and returns nothing. A mistyped reduction ships
        green, which is §2.4's own rationale for the symmetric rule verbatim.
        `adm_token_space` carries the `ADMITS` half: `an y` makes the admitted
        set uncomputable and fires with or without a row (`[G24:12]`), where
        the survivor reads `{any, all}` and clears it -- and an
        `EXCLUDES`-only fixture here would repeat gate round-5's MAJOR-3
        exactly. `token_control` is the negative, so a lint that flags every
        row fails too.
        """
        from conformance.quant_lint import lint_quantifier_completeness

        findings = lint_quantifier_completeness(_TOKEN_INTERIOR_SPEC)

        assert any(
            f.kind == "missing_reductions" and f.subject == "excl_token_space"
            for f in findings
        )
        assert any(
            f.kind == "missing_reductions" and f.subject == "adm_token_space"
            for f in findings
        )
        assert not any(f.subject == "token_control" for f in findings)
