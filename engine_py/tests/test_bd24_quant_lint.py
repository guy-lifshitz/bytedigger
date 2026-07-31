"""RED tests for bd#24 (L2b): the quantifier-completeness lint (1 AC: AC-C5).

Spec: engine_py/conformance/QUANT_LINT_SPEC.md (v6, FROZEN), which inherits
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
