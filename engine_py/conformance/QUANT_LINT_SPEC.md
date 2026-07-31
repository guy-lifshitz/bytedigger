# Lot spec — bd#39: the quantifier-completeness lint (`AC-C5`), child of bd#24

**v12, FROZEN under THIS lot's number. ACCEPTED by gate round 11.** Child of bd#24, which is itself L2b of the 12-lot split of bd#7. Same
worktree, same branch `lot-24`. Base: `origin/main` @ `08b8413`.

**AC accounting: 1 = 1.** bd#24 **remains the bearer of `AC-C5`**; this lot carries the same one AC forward and
adds none. Nothing downstream re-counts.

Depends on bd#22's shipped `conformance` package. It MUST NOT reference L1's emissions or any later lot's design.

## 0.0 Why this lot exists, and what it inherits — the constitution

**The parent stopped on its own exit criterion.** bd#24's commit `1467aec` classified gate round 4's MAJOR as
**type (a)** — a defect introduced by the round's own previous fix, in its own words, *"against the clause I
introduced in v6"*. One type (a) stops spec rounds. There is therefore **no seventh revision inside the
parent**: bd#24 freezes at **v7 / `1467aec`**, and everything after it belongs here.

**The prescription arrived late, and not through the lot's fault.** The dispatch channel returned `state=busy`
twenty-odd times consecutively while gate rounds 5-8 were running, so the stop never reached the executor. The
defect is recorded separately as **hal#1512: the stop criterion has no enforcement layer** — a criterion that
can only fire through a channel that can be busy is advisory, not gating. That is a finding about the harness,
not about this artifact, and it is filed where it can be fixed.

**Nothing found in rounds 5-8 is discarded.** All of it was confirmed by execution, and it enters here as
**input requirements**, not as rediscoverable work:

- **The marker-case finding (gate round 5).** `ADMITS:`, `NON-UNIFORMITY:` and `EXCLUDES:` appeared across the
  whole fixture set **only in ALL-CAPS** and **only with their anchor already present**, so every clause
  ranging over "the markers" was pinned over eight and measured on five. Closed by `_ROW_MARKER_CASE_SPEC`;
  carried here as the standing instruction that **a clause's scope and its fixtures' scope are counted
  separately, and the count is written down.**
- **Gate round 8's MAJOR.** Anchor independence: the (`LEVEL`, row) cell was recorded as covered by
  `_EXCLUDES_ROW_BINDING_SPEC`, which kills a **different** candidate (C2-10, binding by rule) than the one the
  cell names (a single shared "current declaration" variable). Confirmed at 46/46. Closed by
  `_SHARED_ANCHOR_SPEC`; carried here as the instruction that **a cell is filled by the candidate it names, not
  by a candidate that happens to die in the same document.**
- The `[G24:5]`/`[G24:4]` `ADMITS` halves, the distance axis `[G24:9]`/`[G24:10]`, and §0.9's (3a)/(3b)/(3c)
  corrections all came from the same window and stand as written.
- **Gate round 9's finding is NOT completed here.** Anchor independence is six **ordered** cells, not three
  pairs; three were empty and three candidates were confirmed at 47/47. Its fixture `_ANCHOR_CYCLE_SPEC` is
  present in the RED because deleting a fixture that kills three confirmed candidates is the exact act that
  rejected bd#22 round 4 — and the round was **referred to this lot's gate** rather than declared closed.
  **v2: bd#39's gate has ruled.** The finding is sound, the fixture non-vacuous and self-discriminating, and
  the clause is **pinned, not declared unpinned**. The gate also found the reverse defect the deferral had
  created: the RED was enforcing a semantic the frozen spec did not record — `C2-29` existed in no §3 row,
  `_ANCHOR_CYCLE_SPEC` in no §3.1 inventory row, and §5 still recorded `[G24:10]` as three pairwise cells while
  the RED asserted six. That is §4's own rule ("every test docstring names the candidate ID from §3") broken by
  the deferral itself. All four are corrected in v2, spec-side only, with no fixture edit.

  **v3 (bd#39 gate round 2): two MAJOR, one of them type (a) against the row v2 had just written.**
  `[G24:8]`'s corrected row declared "all **eight** markers" and enumerated **seven** — no indented `ADMITS:`
  existed anywhere in the file, so `indented_admits` passed 50/50. A fix that leaves one member of its own
  enumeration unfilled while declaring the enumeration complete is the parent's exit-criterion shape, arriving
  inside the correction written to close the previous round. `ADMITS` has now been the separately-handled
  marker **four** times (its base case, its duplicate rule, its distance axis, its framing cell); treating it as
  covered by the branch beside it is the inference all four rejected. Closed by `_INDENTED_ADMITS_SPEC`.
  The second: `[G24:3]` pins one finding per `(kind, subject)` **pair** — two axes — and §5 recorded it as a
  single cell, which §0.9(3a) names as the claim under audit. The (different `kind`, same `subject`) cell was
  empty **by construction**: level names in this file are bare identifiers and seam names are dotted, so no
  name was ever both. `dedup_by_subject_only` passed 50/50; closed by `_SAME_SUBJECT_TWO_KINDS_SPEC`. Both
  MAJORs run and confirmed before closing, neither taken on report.

  **v4 (bd#39 gate round 3): one type (a) and one CONTRADICTION.** The type (a) is `[G24:11]` — written in v3
  saying "surrounding whitespace" and measuring one side, which is bd#24 gate round 4's MAJOR **verbatim** on
  the clause written beside it, and the finding the parent's exit criterion fired on. Closed by measuring, per
  §0.0. The second is graver in kind: **§2.4 and §2.6 required opposite outputs** on a level with an invalid
  `ADMITS` and no row, and no fixture reached that input. §5 offers the reference passing as "the first
  mechanical evidence that this spec has no internal contradiction" — and it is not evidence for that, because
  the reference's author resolves such an ambiguity silently and the fixture set never asks. **That claim is
  now scoped accordingly in §5.** Decided in `[G24:12]`, with `[G24:13]` pinning when validation happens; both
  measured by `_TOKEN_VALIDATION_SPEC`, whose assertion is an **exact set** because both survivors are
  invisible to `any(...)`.

  **v5 (bd#39 gate round 4): one inherited, one type (a) — and the type (a) is a rule I added that moved the
  verdict in the permissive direction with no §3 row and no coverage diff.** `[G24:9]` declares position not
  part of the binding, which has **two** consequences — the level is discharged, and its `ADMITS` governs that
  row's coverage — and `_ROW_BEFORE_LEVEL_SPEC` measured the first only, because its level has no `ADMITS`.
  That is `[G24:5]`'s N×M correction one clause over; closed by `_ROW_ORDER_COVERAGE_SPEC`. The type (a) is
  v4's "an empty token is ignored": correct for the trailing comma, stated over tokens in general, and so
  reaching a bare `ADMITS:` — which §2.2 distinguishes from an absent one — and clearing **every** row on that
  level. §0.9 direction 1 requires a round that changes behaviour to say what the previous set decided and
  confirm it still holds; before v4 the empty string was a token outside the four and `[G24:1]` made it a
  finding, which is the safe answer, and the change was recorded nowhere. `[G24:14]` restores the safe reading
  and `_EMPTY_TOKEN_LIST_SPEC` measures it.

  **v6 (bd#39 gate round 5): one MAJOR, and it is the founding case reached by a new route.** The
  empty-operand question had been answered for four of the eight markers and for **none** of the other four:
  §2.6 and `[G24:4]` said *presence*, §0.2 said *pinned*, and no fixture carried an empty property operand, so
  both readings passed 57/57. Under the presence reading a seam with all three markers laid out and none
  filled — a hand-written template — is conformant, which is §0.2's founding case verbatim by a route
  `_SEAM_NOT_PINNED_SPEC` cannot reach. Decided by `[G24:15]`, with §2.6's check-3 row and `[G24:4]` reworded
  from *present* to *pinned*, since the presence wording is what made the permissive reading literal.

**The accepted correction, unchanged and unnarrowed.** Gate round 4's MAJOR is closed by **measuring the second
side** (`_OPERAND_SPACING_SPEC`, C-SPACING), not by declaring the single space part of the marker. The
reasoning is upheld verbatim: narrowing would let the fixture set choose the semantics rather than the reverse,
and a fixture document written by hand carries both spellings.

**Acceptance requirement, and it is measured rather than asserted:** mutant **#29** (`fixed_offset_operand`,
`operand = line[len(key) + 2:].rstrip()`) **must die after the fix.** Run on the executing host at this freeze:

> **MEASURED at the #39 freeze — reference 48/48 passed; mutant #29: 46 passed, 2 failed**, on
> `test_ac_c5_operand_spacing_other_than_one_space` and `test_ac_c5_row_operand_extraction_path`.

**§5's `[G22:13]` asymmetry caveat is preserved VERBATIM and is not to be reworded.** It is the more valuable
artifact of the parent's last four rounds: twice consecutively the gate named a surviving candidate **by
reading**, predicted its exact score, and was right (#28 at 40/40, #29 at 41/41), and neither was in the
27-mutant enumeration. Execution is a floor, not a ceiling. Taken up as **hal#1511** as a measured limitation of
the method.

**The (a)/(b) criterion, sharpened by the dispatcher and in force for all lots from bd#39 v7.** The earlier
wording — "introduced by this round's own change" — is read literally as "a finding against a clause I wrote in
the previous revision", and by that letter three of this lot's findings were filed as **type (a)**. The rule
does not exist for the letter. **Type (a) is a finding that OVERTURNS THE BASIS a revision stands on**: what
the spec rested on turns out to be wrong, and the fix runs *against* a decision already taken. **Type (b) is
everything else** — including "the clause is mine, I wrote it last revision, and I wrote it wrong" — whenever
the fix **upholds** the decision already taken and follows it.

> **The test is one question: after the fix, does the earlier decision still stand, or is it overturned?**
> Stands ⇒ (b). Overturned ⇒ (a).

Without this, the rule is self-sustaining: every lot finds a defect in its own recent clause within two rounds
and must split forever. **The parent was stopped correctly** — bd#24's v6 introduced a clause and v7 repaired
*that clause's own* defect with no enclosing basis dictating the repair. **This lot has such a basis**, in §0.0
above, and it was bought at the price of the parent's stop; that is what the split was for.

**Reclassified accordingly, and the reclassification is applied to every finding this lot filed, not only the
one the dispatcher named:**

| Round | Finding | Filed as | Now | Because the fix… |
|---|---|---|---|---|
| bd#39 r3 MAJOR-1 | `[G24:11]` said "surrounding whitespace" and measured one side | (a) | **(b)** | …does exactly what §0.0 already required — *measure the second side, never narrow*. The basis is not overturned; it is confirmed, and the clause was written inconsistently with it |
| bd#39 r3 MAJOR-2 | §2.4 and §2.6 required opposite outputs | (b) | **(b)** | …resolves a contradiction *within* the revision; no earlier decision is overturned |
| bd#39 r4 MAJOR-2 | v4's "an empty token is ignored" reached a bare `ADMITS:` | (a) | **(b)** | …restores the safe reading `[G24:1]` was minted for and leaves `[G24:11]`'s trailing-comma rule intact. The basis stands |
| bd#39 r6 MAJOR | `[G24:15]` created an unpinned property write policy | (a) | **(b)** | …pins the policy `[G24:4]` already implied ("collapsed, never punished"). `[G24:15]` itself stands unchanged |

**Not one of the four overturned a basis**, which is the substantive point rather than a bookkeeping one: this
lot has been repairing its own execution against a constitution that held throughout, which is the state the
split was created to make possible.

**The RED keeps its filename** (`engine_py/tests/test_bd24_quant_lint.py`). Renaming it would break the
containment script's path to bd#22's round-9 artifact and buy nothing: the parent remains the bearer of
`AC-C5`, so the file is named for the AC's owner, not for the lot currently working on it.

**The revision notes below are bd#24's history, retained as inherited record.** Notes v2-v7 are the parent's
own rounds; **v8-v11 are post-criterion material, now correctly located in this lot** rather than in the
parent. They are not renumbered, because renumbering them would erase the evidence of when each was written —
and when a thing was written is exactly what the exit criterion turns on.

---

**v2 revision, and why the freeze was reopened before the gate rather than after it.** Writing the RED against
v1 and then running §0.9's own self-sweep over it surfaced a corner v1 had not pinned: the **base case of both
binding rules** — a property line or an `EXCLUDES` line with **no preceding anchor at all**. Every fixture in the
file, carried and new, exercises P3 and `[G24:2]` only where an anchor exists. That is structurally the same miss
as round-4's B2 (three fixtures each missing *one* property, none with *zero*), one rule over. bd#22's `[G22:16]`
established the correct response: the RED **flags** an unpinned semantic and it is decided **in the spec**, rather
than invented in a test and discovered by a later round. So it is pinned in §2.5 and given a fixture, and the
spec is re-frozen at v2 before the gate. Nothing else changed: no carried expectation, no `[G24:1]`/`[G24:2]`
wording, no measurement.

**v3 revision — gate round 1 returned REJECTED with four MAJOR findings; all four are closed here.** Two were
**type (a)**, introduced by v2's own additions, and one of those is the important one: `_ORPHAN_MARKER_LINES_SPEC`
was the sole justification for reopening the freeze at v2, and **the candidate it claimed to kill did not die.**
Crediting the orphaned `NORMALISATION:` forward still leaves its seam missing two other properties, so check 3
fires either way; crediting the orphaned `EXCLUDES:` forward adds coverage to a level that still has no *row*, and
check 1 is defined over rows, so it fires either way. Both assertions passed for the very GREEN they were written
to kill. **C1-12 and C3-16 as written in v2 were false statements about their own fixture** — precisely the
"added assertion that cannot fail" §0.9 direction 2 was written to catch, missed by the round that wrote the rule.
They are corrected below and the real kill is carried by `_FORWARD_CREDIT_SPEC`, where the credited marker
*completes* its anchor and therefore moves the verdict.

The other three: `ADMITS` looked up by proximity while `subject` is taken from the operand (two independent axes,
never crossed by any fixture — C2-18); `[G24:6]` pinned with no discriminating fixture, since the only row naming
an undeclared level covers all four reductions (C2-19); and coverage mis-written as set **equality** rather than
**superset**, which no fixture separated because no `EXCLUDES` was ever a strict superset of its `ADMITS`
(C2-20). Two adversarial edges the gate raised as advisory are adopted as pins (`[G24:7]`, `[G24:8]`); the rest
are declared out of scope in §6 rather than left silently unpinned.

**All closures are ADDITIVE.** The gate proposed editing two *carried* fixtures for C2-18 and C2-19; adding
instead keeps §0.9 direction 1 at zero modifications **and** keeps the coverage those carried fixtures already
provide, which an edit would have traded away — `_CHECK2_WRONG_LEVEL_BINDING_SPEC`'s present-but-short row and
`_LEVEL_CASE_MISMATCH_SPEC`'s single assertion are both still needed.

**v4 revision — gate round 2 returned REJECTED with two MAJOR, both type (b) and both from round-1 material.**
Neither is a new mistake; both are the *same* mistake as round 1's C1-12/C3-16, found in a different clause:
**a normative clause pinned, and the fixture that claims to measure it unable to fail.**

- **MAJOR-1 (C2-21).** `[G24:3]` collapses findings to one per `(kind, subject)`, and the only check-2 count
  assertion stood on `alpha_count` — a row missing **exactly one** reduction. A lint emitting one finding per
  missing reduction emits exactly one there and passes. Every other check-2 assertion is `any`/`not any`, blind
  to duplicates. Closed by counting on `short_level`, which is missing **two** and carries no unrecognised token.
- **MAJOR-2 (C3-18, C2-22).** `[G24:4]`'s first clause (set, not count) is discriminated; its second — *a
  repeated line is not itself a finding* — was not, because both duplicate-carrying seams are offenders anyway
  and the collapse rule makes a duplicate-punishing lint produce an identical list. Check 3 as "each marker
  exactly once" passed all 36 while reporting a fully pinned seam as non-conformant. The check-2 mirror (a
  duplicated **recognised** token in `EXCLUDES`) was unmeasured *and unpinned*; `[G24:4]` now covers both.

**The pattern is now explicit, and it is the lot's own lesson turned on itself.** `[G24:5]`, `[G24:6]`, `[G24:3]`
and `[G24:4]` were each written as a clause and each given a fixture that walked the clause without being able
to contradict it. §0.9(2) already says naming a candidate is not the check; **§5 now adds the operational form:
for every normative clause, name the fixture whose EXPECTED OUTPUT changes if the clause is read the other
way.** A clause with no such fixture is unpinned in effect, however carefully it is worded.

Three adversarial edges the gate raised are also closed rather than deferred: anchors tracked independently
(C3-19 — every fixture was levels-first/seams-last, so a single "current anchor" variable satisfied both binding
rules at once), row-before-level order independence (`[G24:9]`, C1-14), and **mixed-case** marker prefixes
(C-CASE — only ALL-CAPS and all-lowercase were walked, so two literal spellings passed for case-insensitivity).

**Containment is now a committed, runnable script**, `engine_py/tests/_bd24_containment_check.py` (gate round-2
MINOR-5: both gate rounds lacked a shell and could endorse no numbers, and a proof nobody can re-run is a claim).
It is deliberately not a pytest test — it shells out to `git`, and CI's pytest job runs from an installed wheel
with no repository present.

**v5 — the candidate simulation was EXECUTED rather than reasoned** (§5). A reference implementation written
from this spec plus 27 single-decision mutants, run against the RED outside the worktree: reference 40/40, all
27 killed. It also disclosed two facts reading had not produced in time — an assertion claimed by round 3 that
never landed in the file, and C-CRLF's candidate being narrower than §3 said.

**v6 — gate round 3 returned REJECTED with one MAJOR, plus nine MINOR.** The MAJOR is the **third** kill
`_ORPHAN_MARKER_LINES_SPEC` has been credited with and could not make: `[G24:5]`'s *not itself a finding* clause
was measured for the `EXCLUDES` half only. The third assertion (`not any(kind == "missing_reductions")`) is
sound because the document contains no row at all — but the document **does** contain a seam, so a spurious
`seam_not_pinned` from the orphaned property line is indistinguishable from the expected one under `any(...)`.
The gate offered a falsifiable prediction: a lint doing `seams.setdefault(cur_seam or "<unnamed>", set())` —
the natural sentinel-default form, not a contrived one — would pass 40/40. **Run as mutant #28: it did.** Closed
by a symmetric guard (C3-20), and re-run: it now fails.

That the gate found a candidate my 27-mutant enumeration did not contain is the substantive point, and it is
recorded here rather than in a footnote: **mutation adequacy is measured against the author's own enumeration of
decisions, and the reference and the spec have one author.** A shared misreading is invisible to both. Execution
raises the floor; it does not replace an adversary. §5's simulation subsection is scoped accordingly.

Also closed: `[G24:7]`'s rationale contradicted §5's own note (MINOR-B) — reconciled by pinning what an operand
**is** (§2.5), with a fixture, since the pin was otherwise unfalsifiable in exactly the way `[G24:5]` and
`[G24:6]` were; §3.5's C-CRLF row rewritten to the precise candidate (MINOR-C); the harness's post-GREEN
disposal stated (MINOR-D); the last five live `C1-13` citations renumbered (MINOR-E); the RED's spec-version
citation, two versions stale (MINOR-F); **the containment script now compares carried test BODIES**, not only
their names (MINOR-G) — with most candidates dying by exactly one test, a deleted assertion silently un-kills
one, which is bd#22 round 4's hole in a different wall; two missing rows added to §5's clause table (MINOR-H);
and "round 3" disambiguated between bd#22's and this lot's (MINOR-I).

**v7 — gate round 4 returned REJECTED with one MAJOR, type (a), against v6's own new clause.** `[G24:7]`'s
framing pin says "**surrounding** whitespace" — both sides — and `_TRAILING_WHITESPACE_SPEC` measured the
trailing side only. Every fixture in the file writes exactly `": "` after every marker, so
`operand = line[len("LEVEL: "):].rstrip()` — the marker plus one *assumed* space — is indistinguishable from
parsing the operand. Predicted by the gate to pass 41/41; **run as mutant #29: it did.** It is silently wrong on
input the spec does not exclude: `LEVEL:phases` yields the subject `hases`, a `Finding` naming a level that
appears nowhere in the document, which is what P1's verbatim guarantee exists to prevent.

The gate offered two closes with no preference: declare the single space part of the marker and narrow the
clause to "trailing", or measure the other side. **Measured** (`_OPERAND_SPACING_SPEC`, C-SPACING). Narrowing
would have let the fixture set choose the semantics instead of the reverse — and a fixture document written by
hand will contain both spellings, so the narrower clause buys nothing but a smaller obligation.

**Twice now the gate has named a surviving candidate by reading, predicted its exact score, and been right**
(#28 sentinel seam, 40/40; #29 fixed offset, 41/41). Both were absent from my mutant enumeration. That is the
`[G22:13]` asymmetry measured rather than argued, and it is the reason §5's simulation subsection is scoped as
a floor rather than a proof.

**v8 — gate round 5 returned REJECTED with three MAJOR: one family, one cause.** `ADMITS:`,
`NON-UNIFORMITY:` and `EXCLUDES:` appear across all 37 fixtures **only in ALL-CAPS** and **only with their
anchor already present**. Every clause ranging over "the markers" or over "the anchored lines" was therefore
measured on the other five markers and on two of the three anchor rules:

- **MAJOR-1** — §2.2 pins case-insensitivity over **eight** markers; `_LOWERCASE_MARKERS_SPEC` and
  `_MIXED_CASE_MARKERS_SPEC` spell the same **five**, neither carrying a row, an `EXCLUDES` or an `ADMITS` line
  at all. A lint case-folding those five and matching the row/reduction three against upper case only passes
  42/42 — C-CASE one marker-family over. **Three §3 rows asserted coverage their fixtures did not have** and
  are corrected below: the C1-12 shape, found by the sweep rather than by me.
- **MAJOR-2** — `[G24:5]` states the no-anchor base case and instantiates it for P3 and `[G24:2]`. There are
  **three** anchored marker rules: P2 (`ADMITS` → nearest preceding `LEVEL`) has the same base case, carried by
  neither orphan fixture, with every `ADMITS` line in the file sitting directly under its own `LEVEL:`. Either
  the clause was two-thirds measured or `ADMITS`' base case was **silently** unpinned — and §6 exists to stop
  the second. One of the two survivors also breaches §2.1's MUST-NOT-RAISE.
- **MAJOR-3** — `[G24:4]` says "`ADMITS` **and** `EXCLUDES` are sets of recognised tokens" and measured only
  `EXCLUDES`. Gate round-2's MAJOR-2 verbatim with `ADMITS` where `EXCLUDES` stood: accepted as real then, real
  now, and introduced by the revision that extended the clause to both while measuring one.

All four predicted survivors were run and **all four passed 42/42**. Closed by two fixtures
(`_ROW_MARKER_CASE_SPEC`, `_ADMITS_BASE_CASE_SPEC`) — two rather than one per §0.8 rule 2, so a failure is
attributable. The gate's `[G24:8]` advisory, instantiated for `LEVEL:`/`SEAM:` only, closes for free via an
indented `Excludes:` inside the first fixture's prose.

**Three rounds, three confirmed predictions, and none of the three candidates was in my enumeration** (#28
sentinel seam, #29 fixed offset, #30 case-sensitive row markers). The generalisation is therefore promoted from
observation to rule, because it has now paid three times: **a clause that names N things is measured on N
things, and the count is written down.** §5's table now records the SIDES each clause ranges over, not one
fixture per clause.

Self-caught in the same pass: §3.1's inventory was missing `_TRAILING_WHITESPACE_SPEC` and
`_OPERAND_SPACING_SPEC`, added in v6 and v7 with candidate rows but never listed. A table that claims to
enumerate the fixture set and does not is the same defect one register down; both rows added.

**v9 — gate round 6 returned REJECTED with one MAJOR, and with the answer to the question v8 asked.**

The MAJOR: `[G24:5]` states **two** consequences for each of its **three** anchor rules — an unanchored line is
not itself a finding, and is not credited forward. That is a **3 × 2 grid, and P2's not-a-finding cell was
empty**. `levels.setdefault(cur_level or "", …)` registers a level named `""` from the orphaned `ADMITS`, which
check 1 then reports as `Finding("missing_non_uniformity_row", "")` — a finding invented from an unanchored
line, and an empty `subject`, the state §6 declines to pin *because* it would arise from an empty operand,
reached here from a document containing none. Predicted 44/44; **run: 44/44.** Closed by one assertion.

**And the instrument was wrong, in a way this lot has already diagnosed once — in its own subject matter.**
v8 promoted "a clause naming N things is measured on N things". That rule counts **one axis**. The defect is a
clause naming things along **two independent axes**, where the measurement is the **product** and counting
either axis alone reads as complete: §5's table counted `[G24:5]`'s three anchor rules, reached three of three,
and recorded the clause as fully measured with a cell of the second axis empty.

This is **K13 — the two-axis lookup — applied to the falsifiability table itself.** K13 entered the candidate
list as gate round-1's MAJOR-1, where `subject` was resolved from the row's operand and `ADMITS` by proximity,
and no fixture crossed the axes. The method has now reproduced the defect it was built to catch, one level up.
That is `[G22:13]`'s recursion arriving at the instrument, and it is recorded here rather than as a footnote:

> **§0.9(3), the cell rule.** For any clause ranging over more than one axis, §5's table records the **cells of
> the cross-product**, not the sides of either axis. **An empty cell is a finding.** Counting harder along one
> axis cannot find an empty cell in the other.

The cross-product was run over every multi-axis clause in v8 — `[G24:1]` (2×2), `[G24:4]` (3×2), `[G24:5]`
(3×2), `[G24:7]` (3×2), §2.2 (8×1), `[G24:3]` (3×1) — and only `[G24:5]` had an empty cell. That bound is what
makes this a closed finding rather than an open-ended one.

Both of round 6's advisories are also closed rather than declared, in one fixture (`_ROW_OPERAND_SPACING_SPEC`,
C-ROWOP): `[G24:7]` was measured on `LEVEL:` and `SEAM:` operands but never on a `NON-UNIFORMITY:` one, which
has its own extraction path (the em-dash split), so a fixed offset applied to that marker alone passed 44/44;
and `ADMITS:` was spelled in two cases where the other seven markers now appear in three.

**v10 — gate round 7 returned REJECTED with one MAJOR: two halves of one gap, and the second half is an axis
nobody had named.**

**Half 1 — `[G24:10]` says "the TWO anchors" and there are three.** P2's anchor is the `LEVEL`, exactly as
`[G24:5]`'s grid was corrected to 3 × 2 last round on the same reasoning. Independence is a property of
**pairs**, so the cross-product is three: (`SEAM`, row) — `_INTERLEAVED_ANCHORS_SPEC`; (`LEVEL`, row) —
`_EXCLUDES_ROW_BINDING_SPEC`, where a lint conflating the two binds `EXCLUDES` to the level and inverts both
levels; (`LEVEL`, `SEAM`) — **empty**. The correction was not carried from `[G24:5]` to `[G24:10]`, which
describes the same three rules one property over, and §5's table still recorded `[G24:10]` as a single cell.

**Half 2 — "nearest PRECEDING" makes two independent claims**, and only one was ever measured: *which* candidate
anchor is chosen when several precede, and *that the rule reaches across intervening lines at all*. **Distance
is an axis, and it was in no table.** Every `ADMITS` line in all 40 fixtures sat on the line **immediately
after** its own `LEVEL:` — distance 1, nothing between, not even a blank line — while P3 and `[G24:2]` are both
exercised at distance. So for P2, "nearest preceding" had never been distinguished from "the previous line".
The *selection* half is measured (`_EXCLUDES_SUPERSET_SPEC`'s three consecutive `ADMITS`-bearing levels kill a
first-`LEVEL`-only binding); the gap was distance alone.

Both predicted survivors — `admits_previous_line_only` and `single_anchor_level_seam` — were run and **both
passed 45/45**. Closed by `_DISTANT_ADMITS_SPEC` (C2-27), which fills the (`LEVEL`, `SEAM`) pair and the
distance axis in one document.

**§0.9(3) is extended, because the cell rule did not catch this and could not have as written.** The rule said
"for a clause ranging over more than one axis, record the cells" — and `[G24:10]` was recorded as a
single-cell clause, so the rule never engaged. Two additions, both from this round's failure:

> **(3a) Enumerating the axes is itself the step that gets skipped.** A clause is not exempt from the cell rule
> because its table row has one cell; the row having one cell is the claim under audit. Every clause's axes are
> re-derived from its *text*, not from its existing row.
>
> **(3b) `distance` is an axis on every binding rule.** "Nearest preceding X" claims both a selection and a
> reach. A fixture where the anchored line is adjacent to its anchor measures selection only.

Applied retroactively: P3 and `[G24:2]` are both exercised at distance (`_SEAM_MISSING_NORMALISATION_SPEC`,
`_INTERLEAVED_ANCHORS_SPEC`), so (3b) leaves no other empty cell.

The gate judged the (`LEVEL`, row) pair optional-to-fill; it is **not** filled with a new fixture because
`_EXCLUDES_ROW_BINDING_SPEC` already discriminates it — a lint conflating the level and row anchors binds both
`EXCLUDES` lines to `beta_bind` and inverts the fixture's two assertions. That is recorded in the table rather
than left implicit.

**v11 — gate round 8 returned REJECTED, and the finding is against the judgement immediately above.** I asked
the gate to rule on whether that (`LEVEL`, row) coverage was real. **It is not.** `_EXCLUDES_ROW_BINDING_SPEC`
kills **C2-10** — a lint binding `EXCLUDES` to the nearest preceding `LEVEL` *by rule*. A lint keeping one
shared "current declaration" variable written by both `LEVEL:` and `NON-UNIFORMITY:` produces **identical
output** there, because each row line is the last thing written before its own `EXCLUDES`, so the shared
variable always holds the right value. Predicted 46/46; **run: 46/46.**

**The two are different candidates and this lot had already ruled that they are.** C3-14 (property lines bound
to the *following* seam) was killed long before C3-19 (a single current-anchor variable serving two rules) was
found alive, and `_INTERLEAVED_ANCHORS_SPEC` exists precisely because the first kill did not imply the second.
The (`LEVEL`, row) pair stands in exactly that relation to C2-10 — so the error was not a missed subtlety but a
precedent in this same document, not applied to the cell I had just written. Closed by `_SHARED_ANCHOR_SPEC`
(C2-28), where a `LEVEL:` is interposed between a row and its `EXCLUDES` — which happens in no other fixture.

**§0.9(3b) gains a grain.** The distance sweep asked "is each binding rule exercised at distance ≥ 2" and the
answer was yes for all three. But **what intervenes is itself a sub-axis, and its members are the other anchor
kinds.** For `[G24:2]` the only intervening material ever used was a property line; a `LEVEL:` had never
intervened. So the distance axis and `[G24:10]`'s pair cross-product are the same question asked twice, and
they must be answered at the same grain:

> **(3c) (v11)** Distance is not one axis but two: *how far* and *across what*. The members of "across what"
> are the other anchor kinds, so §0.9(3b) and `[G24:10]`'s pairwise cross-product are the same table and are
> filled together.

Also corrected, from the same round's MINOR: `_DISTANT_ADMITS_SPEC`'s docstring claimed `distant_invalid`
caught **both** targeted candidates by its positive assertion, while its `ADMITS` sat below a blank line only —
so `single_anchor_level_seam` still bound it correctly and was caught by a negative instead. Its `ADMITS` now
sits below a complete `SEAM` block, which makes the sentence true rather than rewording it. Coverage diff for
that modification: the previous form killed `admits_previous_line_only` through the positive and
`single_anchor_level_seam` through assertion 1; the new form kills both through both. Strictly more, nothing
traded.

---

## 0. What this lot inherits

`§0.1`–`§0.7` of `engine_py/conformance/CONTRACTS_SPEC.md` apply **verbatim** and are not restated here.
In force, in the shapes that have actually bitten:

- **§0.1 `[G6:quant]` sharpened** — a requirement ranging over a collection whose reduction is an implementation
  choice must be asserted by a fixture set excluding **every** reduction the implementation could have chosen.
- **§0.2** — naming a seam is not enough; the interception property must be pinned. This lot's subject matter.
- **§0.4 no assertion that cannot fail**, including one that passes pre-implementation.
- **§0.5 no truthiness where a value is specified, per field.**
- **§0.6 pre-passing tests are declared by name.** See §4.
- **§0.7 measurements and citations are base-relative and do not transfer.** Every number in §1 is measured on
  the executing host at this lot's own base; bd#22's `4134` is treated as an inherited claim, not a measurement.
- **`[G22:2]` / §0.3's sharper form — a test may not sabotage a primitive its own harness runs on.** bd#22
  round 1 replaced `builtins.open` with a raiser, broke pytest's own teardown, leaked the patch and killed the
  session (1 passed / 19 errors / crash). **This lot's RED patches nothing at all**: `lint_quantifier_completeness`
  is a pure text→findings function, so every test is a call and a set of assertions on the returned list. There is
  no monkeypatching anywhere in this RED, and therefore no way for it to reach a harness primitive.

### 0.8 `[G22:13]` — the terminating rule, and the reason this lot exists

> A requirement describes what must hold. A fixture must exclude what an implementer might plausibly do
> **instead**. Those are different enumerations, and rewriting the requirement does not close the gap.

Eight blocking findings across bd#22's four gate rounds were all in `AC-C5`'s fixtures, and each was a
requirement discharged **exactly as worded** with the defect one level below. So: **for each check, enumerate the
plausible implementations and show each one dies.** The simulation tables in §3 are this lot's primary
deliverable — not a justification appended to fixtures chosen some other way, but the thing the fixtures are
derived from.

**Standing candidate list** (extensible, never reducible):

| # | Candidate implementation |
|---|---|
| K1 | presence-only (the element exists ⇒ conformant) |
| K2 | first-member-only |
| K3 | last-member-only |
| K4 | document-global presence (ignores the member operand entirely) |
| K5 | substring operand match, direction **A**: `subject in other` |
| K6 | substring operand match, direction **B**: `other in subject` |
| K7 | operand bound to the wrong neighbour (following instead of preceding, or the wrong marker kind) |
| K8 | each **individual member** of a defaulted set omitted from the implementation's default |
| K9 | **membership independent of cardinality** (counts compared, identities not) |
| K10 | a fixed required order for unordered properties |
| K11 | case-folding where the spec pins verbatim text |
| K12 | a required **relation** mis-written — set **equality** where the spec says superset, or the reverse |
| K13 | a **two-axis lookup** with one axis resolved correctly and the other by proximity (right `subject`, wrong governing declaration) |

### 0.9 `[G22:24]` — coverage diff per fix (the method change this lot adds)

Candidate simulation catches "the implementation picked the wrong reduction". It does **not** catch "a fixture
that covered the base case disappeared". bd#22 round 3 fixed `[G22:7]` by **deleting** `_SEAM_NOT_PINNED_SPEC`
(the only fixture carrying a `SEAM:` with zero property lines) and replacing it with three one-property-missing
fixtures; neither the requirement nor the simulation table noticed, because both looked at the shape the
requirement named. Round 4 rejected on exactly that.

**Gating precondition, both directions:**

1. **Deletion/modification trigger.** Any round whose `git diff` removes or changes a fixture MUST list the
   candidates the *previous* fixture set killed and confirm each is still killed by the new set. Not complete
   until written down.
2. **Addition trigger.** A purely additive round is checked too. An **added assertion that cannot fail** produces
   no deletions in the diff, does not move the pass/fail count, and is later classified as an inherited gap
   although it is this round's. Every added assertion is therefore accompanied, in §3, by the candidate it kills
   — i.e. by a statement of what would have to be true for it to fail.

3. **The cell rule (v9, gate round 6).** For a clause ranging over more than one axis, the measurement is the
   **cross-product**, and §5's table records the **cells**. An **empty cell is a finding**. Counting along one
   axis reads as complete while the other axis has a hole — which is K13, this lot's own candidate #13, applied
   to the instrument instead of to the lint.
   - **(3a) (v10)** Enumerating the axes is itself the step that gets skipped. A clause is **not exempt**
     because its table row already shows one cell — the row having one cell is the claim under audit. Axes are
     re-derived from the clause's **text** each round, never inherited from its existing row. Gate round 7:
     `[G24:10]` said "the two anchors", had three, and its single-cell row meant the cell rule never engaged.
   - **(3e) (bd#39 v12, gate round-11 ruling)** There are **three** dispositions for a cell, not two, and they
     are different kinds of claim: **by fixture**; **by construction** — *no input breaks the reason*; and
     **declared out on input implausibility, with the judgement stated** — *no plausible document contains
     this input*. (3d) exists precisely to stop the last two being written the same way, since the third is
     reopenable by a consuming lot producing such a document and the second is not.
     **The two-legged test for the interior axis**, recorded as the reason rather than the conclusion: a
     character class enters the axis only if **(i)** some clause assigns it a delimiter role **and** **(ii)** a
     hand-written document plausibly contains it inside a name. The **colon** satisfies both — this AC is
     about seams named by attribute path and `pkg.module:function` is the setuptools entry-point convention.
     The **comma** and the **em dash** satisfy (i) only: nobody names a level `a,b` or a seam `a—b`. A
     surviving mutant exists for each and neither is fixtured, because a fixture whose input no document would
     contain buys an assertion against a defect nobody can commit — *inventing a clause per corner*, which is
     what `[G24:6]` was found to be, one register out. **That test is also what makes the axis finite.**
     Likewise `LEVEL : phases`: the marker token is `LEVEL:`, so `LEVEL :` is not a marker and declares
     nothing; `^([A-Za-z-]+)\s*:` survives, but eight markers plus case-insensitivity push implementations
     toward `line.upper().startswith(m + ":")`, which rejects it, and a spec document does not contain a prose
     line beginning with an all-caps marker word and a space-colon. Declared out on both legs.
   - **(3d) (bd#39 v11)** **Every "by construction" cell states the reason AND the input on which the reason
     would fail.** A by-construction cell asserts that no fixture is needed, and it is the only place in this
     method where a claim is recorded with nothing that could contradict it. Of the last four such claims
     three were wrong or under-scoped. The discipline catches them at authoring time: for the **property**
     operands' interior the sentence writes itself — *an interior character cannot change whether a string is
     empty, and emptiness is all that is tested*, so **no input breaks it**, which is what makes the cell
     genuinely closed. For the **reduction** operands the sentence cannot be written, and the attempt is what
     exposes the gap (bd#39 round-10 MAJOR: the recorded reason was "they never become a `subject`" — true,
     and irrelevant, because their interior feeds **recognition**).
   - **(3c) (v11, extended in bd#39 v2)** Distance is **two** axes, not one: *how far*, and **across what**.
     The members of "across what" are the other anchor kinds, so (3b) and `[G24:10]`'s cross-product are the
     same table and are filled together — **and that table is ORDERED**. Independence is not symmetric: "does
     an intervening `X` close a live `Y`?" and "does an intervening `Y` close a live `X`?" are different
     questions with different implementations. Three anchor kinds give **3 × 2 = 6 ordered cells**, and the
     table is then closed — there is no seventh, because "across what" ranges over the other anchor kinds and
     there are only three. Gate round 8: every binding rule was exercised at distance ≥ 2, and a `LEVEL:` had
     still never intervened between a row and its `EXCLUDES`.
   - **(3b) (v10)** **`distance` is an axis on every binding rule.** "Nearest preceding X" claims a *selection*
     (which candidate, when several precede) **and** a *reach* (that it crosses intervening lines at all). A
     fixture whose anchored line is adjacent to its anchor measures selection only. Every `ADMITS` line in the
     lot sat on the line immediately after its `LEVEL:` for ten rounds.

§5 carries this round's coverage diff.

---

## 1. Measured baseline

Base `08b8413` (this host's own `main` at the time of measurement), isolated worktree
`~/Projects/bytedigger-wt/wt-n24`, command `python3 -m pytest tests/ -q -p no:cacheprovider --timeout=120` run
from `engine_py/`, python 3.14.6:

> **MEASURED: 4227 passed, 6 skipped, 0 failed (286.15 s).**

Contention control per hal#1353 taken before the run: no `Runner.Worker` process, no competing `pytest`
(matched on command **prefix**, not substring). bd#22 declared `4134 passed / 6 skipped / 0 failed` at
`a2691f9`; that is an inherited number, it does **not** reproduce here (`main` has advanced by four commits
since `a2691f9`), and it is not used as this lot's baseline (§0.7). The RED measurement in §4 re-establishes
the same figure for the non-bd#24 tests, which is what makes the number above load-bearing rather than
merely recorded.

Ship in **0 failed**. The drift invariant is the **property** "identical to this host's own `main` at
`extra_bd == 0`", never a literal count.

---

## 2. `AC-C5` — the quantifier-completeness lint

### 2.0 Scope limit, unchanged and load-bearing

The lint checks that a spec document **documents** its quantifiers and seams. It does **NOT** verify that the
cited fixtures exist or that they discriminate — that is the gate's judgement and it does not mechanise. bd#7
proved it: a keyword sweep over its own spec mis-scored **11 of 13** ACs. A lint that claimed the gate's job
would be a worse artifact than the prose it replaces.

### 2.1 Public surface — inherited from `CONTRACTS_SPEC.md` §1.5, restated normatively for this lot

`conformance/quant_lint.py` (today an import-only placeholder, `[G22:18]`) gains exactly two public names:

| Name | Form |
|---|---|
| `lint_quantifier_completeness(text: str) -> list[Finding]` | pure function; an empty list means conformant |
| `Finding` | **frozen** dataclass, exactly two attributes |

| `Finding` attribute | Type | Values |
|---|---|---|
| `kind` | `str` | one of `"missing_non_uniformity_row"`, `"missing_reductions"`, `"seam_not_pinned"` |
| `subject` | `str` | the offending level name or seam name, **verbatim from the document** |

The three `kind` values correspond 1:1 to the three checks, so a caller distinguishes them by value rather than
by parsing a message, and a lint implementing one check cannot masquerade as implementing three.

`lint_quantifier_completeness` **MUST NOT raise** on a non-conformant **or** malformed document: returning
findings is the contract, and raising would make "conformant" and "malformed" indistinguishable to the build
step that consumes it.

### 2.2 The fixture-document grammar — inherited from `CONTRACTS_SPEC.md` §1.6

A fixture document is plain text. These markers are recognised **at line start, case-insensitively**; every other
line is prose the lint ignores.

| Marker | Meaning |
|---|---|
| `LEVEL: <name>` | declares a collection level the document quantifies over |
| `ADMITS: <reduction>[, …]` | **optional**, on a `LEVEL`; which reductions that level's fixtures could plausibly choose between. **Absent means all four.** |
| `NON-UNIFORMITY: <level> — <description>` | the row discharging that level. The description and its em dash are **optional** — see `[G24:11]`, which pins the operand as the text up to the **first** em dash or the whole operand when there is none (bd#39 round-3 MINOR-A: §1.6 sketches the shape and does not forbid the short form, but a reader of this table alone concluded the dash was mandatory) |
| `EXCLUDES: <reduction>[, …]` | which reductions that row's fixtures kill |
| `SEAM: <name>` | declares an interception seam |
| `ATTRIBUTE-PATH:` / `BINDING-TIME:` / `NORMALISATION:` | the three properties a seam declaration must pin |

The four reductions are `any`, `all`, `first`, `last`.

**This grammar is deliberately NOT the format of this lot's own spec documents.** It is the lint's input format;
the lint is a tool, not a validator of these files. Conflating the two would make every prose sentence here a
potential lint failure. No fixture in the RED is ever run over `QUANT_LINT_SPEC.md` or `CONTRACTS_SPEC.md`.

### 2.3 Semantics already pinned by `[G22:16]` — carried forward unchanged

- **P1. Marker prefixes are case-insensitive; OPERANDS are verbatim and case-SENSITIVE.** `LEVEL: Audit` is not
  discharged by `NON-UNIFORMITY: audit — …`. This follows from `Finding.subject` being verbatim: a lint that
  case-folds to match cannot also report verbatim. Two `SEAM` names differing only in case are two **distinct**
  seams.
- **P2. `ADMITS` binds to the nearest preceding `LEVEL`.** Never a following one.
- **P3. Property lines bind to the nearest preceding `SEAM`.** Never a following one. Same rule as P2, stated
  once for both.
- **P4. A `NON-UNIFORMITY` row binds to the level named by its own `<level>` operand** — matched verbatim and
  exactly — **not by proximity** to the nearest preceding `LEVEL`.

### 2.4 The two semantics left OPEN by bd#22, decided here

#### `[G24:1]` Reduction tokens are matched **case-sensitively, lowercase only**, in both `ADMITS` and `EXCLUDES`.

bd#22 pinned "operands are verbatim" while enumerating only `LEVEL` names, row `<level>` operands and `SEAM`
names — leaving reduction-token casing open. Decided: **the reduction vocabulary is exactly `any`, `all`,
`first`, `last`, lowercase; any other token — including `Any`, `ALL`, `First` — is unrecognised.**

Rationale, so a later round does not reopen it: `[G22:9]` already pins "a token outside the four is itself a
finding". Recognition must therefore be exact, and introducing a *second* normalisation rule into a grammar
whose entire other operand class is verbatim would be the inconsistency, not the strictness. There is no
verbatim-reporting counter-pressure either, because `Finding.subject` reports the level, never the token.

Consequences, both normative:
- an unrecognised token in `ADMITS` makes the admitted set uncomputable and is itself a `missing_reductions`
  finding on that level (inherited from `[G22:9]`, now including the case variants);
- an unrecognised token in `EXCLUDES` is **likewise** a `missing_reductions` finding on that row's level, and
  contributes nothing to coverage. Symmetric with `ADMITS` deliberately: the asymmetric reading (ignore it, and
  let it show up as under-coverage) is silent exactly when the row is otherwise complete — `EXCLUDES: any, all,
  first, last, sometimes` would ship conformant.

#### `[G24:2]` `EXCLUDES` binds to the nearest preceding `NON-UNIFORMITY` **row** — not to the nearest preceding `LEVEL`.

An `EXCLUDES` line's level is therefore reached **transitively**, through the row's own `<level>` operand (P4),
never by textual proximity to a `LEVEL:` line. This is the only reading consistent with P4: if the row binds by
operand and its `EXCLUDES` binds by proximity, a row naming a non-adjacent level splits from its own reduction
list.

An `EXCLUDES` line with **no preceding `NON-UNIFORMITY` row** in the document binds to nothing: it is ignored,
contributes no coverage to any level, and is not itself a finding (there is no finding kind for an orphaned
line, and inventing one would exceed the three pinned `kind` values).

### 2.5 Two further clauses this lot pins, because §3's simulation reached them

- **`[G24:3]` At most ONE finding per `(kind, subject)` pair; duplicates are collapsed.** A bare seam is missing
  three properties and yields **one** `seam_not_pinned`; a row missing two reductions yields **one**
  `missing_reductions`. Without this, `len(findings)` is not a defined quantity and the conformant control's
  `findings == []` is the only assertable shape in the whole AC. Asserted directly (§3, C2-9 / C3-9).
- **`[G24:4]` Repetition is collapsed, never punished — for property lines AND for reduction tokens.** Check 3
  is over the **set** of properties **pinned** under a seam (v6: *pinned*, not merely *present* — `[G24:15]`), not the count of marker lines: `ATTRIBUTE-PATH` +
  `BINDING-TIME` + `BINDING-TIME` is a seam missing `NORMALISATION`, not a seam with three properties. **And a
  repeated line is not itself a finding**: a seam carrying all three properties with one written twice is
  conformant. The same holds for check 2, extended here (gate round-2 MAJOR-2, previously unpinned): `ADMITS`
  and `EXCLUDES` are **sets** of recognised tokens, so `EXCLUDES: any, all, first, last, all` covers all four
  and is conformant. Exercised by `_BENIGN_DUPLICATE_SPEC` (C3-18, C2-22), where the duplicate-carrying level
  and seam are **conformant** — which is what makes the clause falsifiable. `_SEAM_PROPERTY_CARDINALITY_SPEC`'s
  duplicate-carriers are offenders on the set rule anyway and so cannot separate the two readings.
- **`[G24:5]` A marker line with NO anchor preceding it binds to nothing.** The base case of P3 and `[G24:2]`,
  and the v2 addition. A property line before any `SEAM`, and an `EXCLUDES` line before any `NON-UNIFORMITY`
  row, each bind to nothing: they contribute no coverage to anything, they are **not** themselves findings
  (there is no `kind` for an unanchored line and inventing one would exceed the three pinned values), and they
  MUST NOT be credited **forward** to the next anchor. A `SEAM` that follows an orphaned `NORMALISATION:` line
  is still missing `NORMALISATION`. Exercised for the not-a-finding and does-not-raise halves by `_ORPHAN_MARKER_LINES_SPEC` (§3, F-8), and for the **forward-crediting** half by `_FORWARD_CREDIT_SPEC` (C2-21, C3-17) — see the v3 note for why the first fixture cannot carry that half.
- **`[G24:6]` A `NON-UNIFORMITY` row whose `<level>` operand names no declared `LEVEL` is still checked**,
  against the **default** admitted set. Reached by the carried `_LEVEL_CASE_MISMATCH_SPEC`, whose row operand
  `audit_case` matches no level once P1 makes the comparison case-sensitive — so v1 left a clause that a carried
  fixture actually walks through. The alternative (skip such rows entirely) would let a typo'd operand hide its
  own row's under-enumeration, which is the defect this AC exists for. The fixture's row covers all four, so
  this pin adds no finding there and contradicts no carried expectation.
- **`[G24:7]` What an operand IS: the text after the marker's colon, with surrounding whitespace and the line
  terminator removed.** "Verbatim" (P1) governs **case and interior characters**, not the framing. So a document
  may use CRLF — `LEVEL: phases\r\n` declares `phases`, not `phases\r` — and `SEAM: Trailing.Seam  ` declares
  `Trailing.Seam`. **Reconciled in v6** (gate round-3 MINOR-B): v4 justified this as "the split is where it has
  to be right", while §5's executed simulation then recorded that an implementation splitting on `"\n"` and
  **stripping** its operands is also correct. Both could not stand — and stripping is itself a normalisation,
  which P1 forbids for `subject`. Pinning the operand's framing rather than the mechanism removes the
  contradiction: any mechanism that yields this operand is admissible. Exercised by `_CRLF_SPEC` (C-CRLF) and
  `_TRAILING_WHITESPACE_SPEC` (C-WS); the latter exists because the clause was otherwise unfalsifiable, no
  fixture having carried trailing whitespace on an operand.
- **`[G24:8]` Markers are recognised AT LINE START, and an indented marker is prose.** §1.6's "at line start" is
  the pinned wording; this states its consequence, because every fixture in the RED is flush-left and so
  `line.startswith(...)` and `line.strip().startswith(...)` are otherwise indistinguishable. A quoted or indented
  `LEVEL:`/`SEAM:` inside prose declares nothing. Exercised by `_INDENTED_MARKER_SPEC` (C-INDENT).
- **`[G24:11]` What the DELIMITERS are** (bd#39 v3), pinned the same way `[G24:7]` pinned the operand's
  framing — by saying what the thing **is**, so any mechanism producing it is admissible. The reduction-token
  separator is a **comma with optional whitespace on EITHER SIDE OF IT** — `any,all`, `any, all`, `any ,all`
  and `any , all` all list two tokens — and an **empty token is ignored**, so a trailing comma changes
  nothing. *Surrounding* meant both sides in v3 and only one was measured (bd#39 gate round 3, MAJOR-1), which
  is bd#24 gate round 4's MAJOR reproduced on the clause written beside it; the second side is now measured by
  `_SEPARATOR_SIDES_SPEC` (C2-33), per §0.0's rule that the close is to measure and never to narrow.
  `[G24:14]` **An empty token list is NOT an empty admitted set** (bd#39 gate round 4, MAJOR-2, **type (a)**).
  v4's "an empty token is ignored" was written for the trailing comma and stated over tokens **in general**, so
  it silently reached two inputs it was not written for and moved both in the **permissive** direction. A bare
  `ADMITS:` is **present**, not absent, and under the literal reading its admitted set is empty — so
  `set(excludes) ⊇ ∅` holds for **every** row and a level's rows are conformant however short they are. That is
  this AC's founding defect, manufactured by an extension to the clause `[G24:1]`'s symmetric rule was minted
  to prevent. Decided in the safe direction: a marker line whose operand contributes **zero** tokens reads as
  **absent** for `ADMITS` (default four) and as **no coverage** for `EXCLUDES` (the row is short). The
  trailing-comma rule is untouched — it removes an empty token from a **non-empty** list. Exercised by
  `_EMPTY_TOKEN_LIST_SPEC` (C2-36).
  `[G24:15]` **A property line whose operand is empty or whitespace-only PINS NOTHING** (bd#39 gate round 5).
  The empty-operand question was answered for `ADMITS`/`EXCLUDES` by `[G24:14]` and for `LEVEL`/`SEAM` by §6,
  and for the three **property** markers by two frozen sentences requiring opposite outputs: §2.6's check-3 row
  and `[G24:4]` are worded over marker **presence**, while §0.2 — inherited, and this lot's own subject matter —
  requires the interception property to be **pinned**. Zero fixtures carried an empty property operand, so
  neither reading was falsifiable and both passed 57/57. Decided where §0.2 and `[G24:14]` both point.
  **This is not a corner:** a seam with all three markers laid out and none filled — a hand-written **template**
  — returns no findings under the presence reading. That is §0.2's founding case verbatim, reached by a route
  `_SEAM_NOT_PINNED_SPEC` does not cover, since its bare seam has *zero* property lines rather than three empty
  ones. §6 could not absorb it either: its empty-operand bullet defers `LEVEL:`/`SEAM: ` because
  `Finding.subject` would become the empty string, and a property operand never becomes a subject, so the
  reason does not transfer and the case was **silently** unpinned. Exercised by `_EMPTY_PROPERTY_OPERAND_SPEC`
  (C3-22).
  **And this clause put the three property markers INSIDE `[G24:7]`'s framing clause** (bd#39 gate round 7,
  MAJOR-2): before it, a property operand was never read, so `[G24:7]` correctly ranged over five
  operand-bearing markers; since it, **eight**. `[G24:15]` also names **two** spellings — *empty **or
  whitespace-only*** — and the fixture set carried one, every property line in all 54 fixtures being either
  `MARKER: value` or a bare `MARKER:`. Both survivors passed 59/59 at zero divergence and are wrong in
  opposite directions: reading the raw text for non-emptiness pins `NORMALISATION:␣` — §0.2's founding case by
  the second spelling this clause itself names — and a marker-plus-one-assumed-space offset reports a fully
  pinned seam unpinned, which is mutant **#29** on the marker family that only just became operand-bearing.
  Exercised by `_PROPERTY_OPERAND_FRAMING_SPEC` (C3-24).
  **Write policy** (bd#39 gate round 6): a property is pinned if **any** of its lines carries a non-empty
  operand, and **an unfilled line never unpins a filled one**. Before `[G24:15]` a property line's operand was
  never read, so check 3's store could be a **set of markers**, where repetition is idempotent by construction
  and `[G24:4]` needed no code at all. Making the operand matter turns the natural structure into a
  **mapping**, and a mapping has a write policy a set does not — `props[marker] = operand` (last-wins) and
  `setdefault` (first-wins) each passed 58/58, failing on **opposite orders**. `[G24:4]` settles it in terms —
  repetition is "collapsed, **never punished**" — so this is not a contradiction but a clause reaching an input
  it was not written for. Exercised in **both orders** by `_PROPERTY_WRITE_POLICY_SPEC` (C3-23).

- **`[G24:11]` continued — the ROW-OPERAND delimiter, given its own bullet** (bd#39 gate round-7 MINOR-A: this
  rule had been buried mid-paragraph under `[G24:15]`, the property-line clause, and round 6 reported the
  restructuring closed when it was not — an inaccurate closure report, which is §5's own "the round claimed a
  closure it had not made" one register out; and it was load-bearing, because MAJOR-1 below is a defect in
  exactly this buried sentence). A row's `<level>` operand is the text up to the **first** em dash, or the **whole operand** when the
  row carries none, so `NON-UNIFORMITY: phases` discharges `phases` and a second em dash inside a description
  changes nothing. Exercised by `_REDUCTION_DELIMITER_SPEC` (C2-32). Raised as an adversarial edge in bd#39 rounds 1 and 2 and closed rather than restated a third time; the `split(", ")` spelling is not contrived —
  it is what this lot's own confirmed mutant `fixed_offset_reduction_operand` used.
  `[G24:11]` **em-dash framing** (bd#39 gate round 7, MAJOR-1): the delimiter is **the em dash itself**, and
  **whitespace on either side of it belongs to neither operand** — pinned the way the comma beside it already
  was. The clause named **two** delimiters and framed one, which is §0.0's standing instrument with N = 2;
  every one of the 78 `NON-UNIFORMITY` lines in the RED wrote ` — `, so `split(" — ")` passed 59/59 **at zero
  divergence**. An em dash set closed-up is ordinary house style, and the survivor reports
  `missing_non_uniformity_row` on a conformant document. Exercised by `_ROW_DASH_FRAMING_SPEC` (C1-16).
  *Whitespace here means whitespace, not the space character: a `.strip(" ")` spelling survives a tab, and no
  fixture in the file contains one (round-7 MINOR-C, recorded rather than fixtured — `.strip()` is the default
  spelling and the plausibility is low).*
  
- **`[G24:16]` The operand's INTERIOR is part of the name** (bd#39 gate round 8). P1 already said so —
  *"verbatim governs case and **interior characters**, not the framing"* — but that is a **second axis** of the
  same question `[G24:7]` answers, and §5 enumerated only framing. Normative, so no fixture set can leave it
  implicit again: **an operand may contain interior whitespace, and every character between the framing
  belongs to the name.** `LEVEL: payload field` declares the level `payload field`. Nothing in §2.2 constrains
  `LEVEL: <name>`, and §0.1's own directions are two-word phrases — *element kind*, *payload field* — which
  this fixture set renders with underscores by **house convention only**, the same argument §0.0 accepted for
  C-SPACING. `operand.split()[0]` satisfies all 24 framing cells **by accident** (tokenising discards leading
  and trailing whitespace, the packed form, and `\r`), passed 61/61 at **zero divergence**, and is wrong in
  two directions — a `subject` that appears nowhere in the document, and, worse because it is silent, two
  level names sharing a first token collapsing so that a declared and undischarged level ships conformant.
  Exercised by `_INTERIOR_SPACE_SPEC` (C1-17).
  **The general half, and why it needed its own fixture** (bd#39 gate round 9): this clause names interior
  **whitespace** and then **every character**, and the fixture set instantiated the first three times and the
  second never — `[G24:15]`'s "empty **or whitespace-only**" shape verbatim, filed in round 7, closed in round
  8, and reappearing inside the clause written to close it. No marker line in any of the 57 fixtures carried a
  **second colon**, so `line.split(":")[1]` — the pinned expression minus its maxsplit argument, and the
  commonest spelling of "take the value from a `key: value` line" — passed 62/62 at zero divergence, as did
  `" ".join(operand.split())`. The colon is not exotic **in this AC's own subject matter**: it is about seams
  named by attribute path, and `pkg.module:function` is the setuptools entry-point convention. Exercised by
  `_OPERAND_CHARACTER_SPEC` (C1-18), which carries an interior colon on each `subject`-bearing marker, a
  **doubled** interior space, and the collision pair `pkg.mod:read`/`pkg.mod:write` — where a prefix-keying
  lint sees the union of two blocks as complete and a seam **declared and not pinned** ships conformant.
  **The comma as an interior character** (bd#39 gate round 10): for a *reduction* operand the interior feeds
  **recognition**, not `subject` — `[G24:1]` makes a token outside `any|all|first|last` itself a finding, so a
  token carrying an interior space decides whether a finding fires. No reduction token in any of the 58
  fixtures carried one, so `operand.replace(" ", "").split(",")` passed 63/63 at zero divergence — **and the
  fixture set steers toward it**: `_SEPARATOR_SIDES_SPEC` exists to kill `replace(", ", ",")`, and an
  implementer who fails that fixture reaches for the more aggressive replace, which handles every spacing form
  uniformly and passes. The RED's own corrective pressure pointed at the one spelling nothing measured.
  Exercised by `_TOKEN_INTERIOR_SPEC` (C2-37), carrying the `ADMITS` half in the same document because an
  `EXCLUDES`-only fixture would repeat gate round-5's MAJOR-3 exactly.

- **`[G24:9]` A row discharges its level regardless of ORDER.** P4 binds a row to the level named by its
  operand; position is not part of the binding, so a `NON-UNIFORMITY` row textually **above** its own `LEVEL:`
  line discharges it. Stated because every fixture happened to place the row below, leaving "a level is
  discharged by a row beneath it" alive across the whole set. Exercised by `_ROW_BEFORE_LEVEL_SPEC` (C1-14).
- **`[G24:10]` The two anchors are tracked INDEPENDENTLY.** P3 (property line → nearest preceding `SEAM`) and
  `[G24:2]` (`EXCLUDES` → nearest preceding `NON-UNIFORMITY` row) range over **different** anchor kinds: a row
  is not a seam and a seam is not a row, so material of the two kinds may interleave freely. A single "current
  anchor" variable serving both collapses them. Stated because every fixture was levels-first and seams-last.
  Exercised by `_INTERLEAVED_ANCHORS_SPEC` (C3-19).
- **Finding order** is deterministic for a given input (the idempotence test compares two calls' lists) and
  otherwise unspecified across inputs; no test asserts a position.
- **Out of scope, explicitly:** the behaviour when two `NON-UNIFORMITY` rows name the **same** level. No fixture
  exercises it and this lot does not pin it, rather than pinning a clause nothing measures.

### 2.6 The three checks

| Check | `kind` | Fires when |
|---|---|---|
| 1 | `missing_non_uniformity_row` | a `LEVEL` has no `NON-UNIFORMITY` row naming it (P4, verbatim) |
| 2 | `missing_reductions` | a `NON-UNIFORMITY` row has no `EXCLUDES` line, **or** its `EXCLUDES` does not cover every reduction its level `ADMITS`, **or** either line carries a token outside the four (`[G24:1]`) — **and, independently of any row, a `LEVEL` whose own `ADMITS` carries such a token** (`[G24:12]`; bd#39 round-4 MINOR-A: v4 narrowed the independence sentence below and left this row's subject as a row) |
| 3 | `seam_not_pinned` | a `SEAM` has not **pinned** each of `ATTRIBUTE-PATH`, `BINDING-TIME`, `NORMALISATION` — **including a `SEAM` with zero property lines**, and including a marker line **present but unfilled**, which pins nothing (`[G24:15]`; v6 rewords this row from marker *presence*, which is what made the permissive reading literal) |

The coverage relation in check 2 is **superset, not equality**: `set(excludes) ⊇ set(admits)`. A row excluding
**more** than its level admits is conformant — `ADMITS: any, all` with `EXCLUDES: any, all, first, last` is a
fixture set that kills more than it had to, which is never the defect this AC hunts. Stated explicitly because
gate round 1 found `==` surviving the entire fixture set (K12, C2-20).

Checks 1 and 2 are **independent**: a level with no row yields check 1 and **no coverage-based check-2
finding** (there is no row whose coverage could be short); a level whose row is short yields check 2 only.

`[G24:12]` **The one exception, and it resolves a contradiction v3 shipped** (bd#39 gate round 3, MAJOR-2). An
**unrecognised token in a level's own `ADMITS`** is an independent ground and fires **whether or not the level
has a row**. v3 asserted both sides of this: §2.4 made such a token "itself a `missing_reductions` finding on
that level" with no precondition, while the sentence above said "check 1 **only**" — and on
`LEVEL: x` / `ADMITS: bogus` / no row the two required opposite outputs. **No fixture reached that input**;
every level carrying an invalid `ADMITS` also carried a row, so the contradiction was invisible to the RED, to
the containment check and to the mutant matrix alike. Decided for §2.4, because its rationale survives the
absence of a row and §2.6's does not: an uncomputable admitted set is a defect of the **level**, not of a row
it may or may not have. Exercised by `_TOKEN_VALIDATION_SPEC` (C2-34).

`[G24:13]` **Tokens are validated on the BOUND LEVEL, never eagerly as each line is read.** An orphan `ADMITS`
or `EXCLUDES` carrying an unrecognised token produces **nothing**: `[G24:5]` forbids inventing a finding from
an unanchored line, and an eager validator emits one with an empty or borrowed `subject` — which is C2-26
(gate round 6's MAJOR) one clause over. Same fixture.

---

## 3. Candidate simulation — the deliverable (`[G22:13]`)

Notation: **un-daggered** rows are carried **verbatim** from bd#22's round-9 artifact (`d39371f`,
`engine_py/tests/test_bd22_contracts.py`) — see §5. Everything **daggered** was added by bd#24 or bd#39 to
close a gate finding. **The dagger count no longer encodes the round** (bd#39 round-4 MINOR-B: runs of ten
daggers accumulated while bd#39's rounds all used one, so the notation stopped carrying information); the
round is named in the row text where it matters, which is what §0.9's coverage diff actually needs.

### 3.1 Fixture inventory

| Fixture | Check | Shape |
|---|---|---|
| `_MISSING_ROW_SPEC` | 1 | 3 levels, none with a row; one mixed-case (`Audit_Field`) |
| `_ROW_LEVEL_BINDING_SPEC` | 1 | 4 levels, 2 with rows; offenders at positions 1 and 3, bracketed; `audit` ⊂ `audit_field` |
| `_ROW_LEVEL_SUBSTRING_REVERSE_SPEC` ✝ | 1 | 3 levels, 1 with a row; offenders `audit_field`/`audit_gate` bracket conformant `audit` |
| `_LEVEL_CASE_MISMATCH_SPEC` | 1 | 1 level, 1 row, differing **only** in case |
| `_CHECK2_SPEC` | 2 | 5 rows, **no `ADMITS` anywhere** (default path); one with no `EXCLUDES`, four each omitting exactly one distinct member |
| `_CHECK2_EXPLICIT_ADMITS_SPEC` | 2 | 3 rows with explicit `ADMITS`; two offenders bracket one conformant |
| `_CHECK2_CARDINALITY_SPEC` ✝ | 2 | 3 default-`ADMITS` rows, **all four tokens long**; offenders (invalid token / duplicated token) bracket a conformant row |
| `_REDUCTION_TOKEN_VOCABULARY_SPEC` ✝ | 2 | uppercase `EXCLUDES`, uppercase `ADMITS`, complete-plus-bogus `EXCLUDES`, one lowercase conformant control |
| `_CHECK2_WRONG_LEVEL_BINDING_SPEC` | 2 | row adjacent to `beta_gate`, operand names `alpha_gate` |
| `_EXCLUDES_ROW_BINDING_SPEC` ✝ | 2 | two rows under one `LEVEL` block; correct and proximity binding produce **inverted** outputs |
| `_ADMITS_WRONG_NEIGHBOUR_SPEC` | 2 | `ADMITS` on the preceding level; wrong-neighbour lint inverts both levels |
| `_ADMITS_INVALID_TOKEN_SPEC` | 2 | `ADMITS: any, bogus, all, first, last` |
| `_SEAM_NOT_PINNED_SPEC` ✝ | 3 | **bare `SEAM:` with ZERO property lines**, first and last, bracketing a conformant seam |
| `_SEAM_MISSING_ATTRIBUTE_PATH_SPEC` | 3 | 2 seams, offender **first** |
| `_SEAM_MISSING_BINDING_TIME_SPEC` | 3 | 2 seams, offender **last**, mixed-case name |
| `_SEAM_MISSING_NORMALISATION_SPEC` | 3 | 3 seams, offender **first**, conformant seam trailing |
| `_SEAM_PROPERTY_CARDINALITY_SPEC` ✝ | 3 | offenders carry **three** property lines of which two are the same marker |
| `_SEAM_PROPERTY_WRONG_NEIGHBOUR_SPEC` | 3 | `Seam.Alpha`'s block sits above `Seam.Beta`'s declaration |
| `_SEAM_NAME_SUBSTRING_SPEC` | 3 | conformant short name, offending long name |
| `_SEAM_NAME_SUBSTRING_REVERSE_SPEC` ✝ | 3 | offending short names, conformant long names, both positions |
| `_SEAM_NAME_CASE_MISMATCH_SPEC` | 3 | two seam names differing **only** in case |
| `_ADMITS_CROSS_AXIS_SPEC` ✝✝ | 2 | the row's operand and the nearest preceding `LEVEL` disagree **and** an `ADMITS` line is in play — crossed in both directions |
| `_UNDECLARED_LEVEL_ROW_SPEC` ✝✝ | 2 | a **short** row naming a level the document never declares |
| `_FORWARD_CREDIT_SPEC` ✝✝ | 2 + 3 | an unanchored line that would **complete** the anchor after it |
| `_EXCLUDES_SUPERSET_SPEC` ✝✝ | 2 | `EXCLUDES` a strict **superset** of `ADMITS`, plus the exact and the deficient case |
| `_CRLF_SPEC` ✝✝ | 1 + 3 | CRLF line terminators |
| `_INDENTED_MARKER_SPEC` ✝✝ | grammar | marker-looking lines at an indent, inside prose |
| `_ORPHAN_MARKER_LINES_SPEC` ✝ | 1 + 3 + F | a `NORMALISATION:` and an `EXCLUDES:` line that precede **every** anchor in the document |
| `_BENIGN_DUPLICATE_SPEC` ✝✝✝ | 2 + 3 | a **conformant** level and a **conformant** seam, each carrying a repeated token / line, plus an offender |
| `_INTERLEAVED_ANCHORS_SPEC` ✝✝✝ | 2 + 3 | level material and seam material interleaved in one block |
| `_ROW_BEFORE_LEVEL_SPEC` ✝✝✝ | 1 | a row textually **above** the `LEVEL:` it names |
| `_MIXED_CASE_MARKERS_SPEC` ✝✝✝ | 1 + 3 | the **same five** marker prefixes in **Title** case — corrected in v8 |
| `_TRAILING_WHITESPACE_SPEC` ✝✝✝✝ | 1 + 3 | trailing whitespace after a `LEVEL` and a `SEAM` operand (v8: added to this inventory) |
| `_OPERAND_SPACING_SPEC` ✝✝✝✝✝ | 1 + 3 | no space and three spaces after a marker's colon (v8: added to this inventory) |
| `_SHARED_ANCHOR_SPEC` ✝✝✝✝✝✝✝✝✝ | 2 | a `LEVEL:` interposed between a row and its `EXCLUDES` |
| `_ANCHOR_CYCLE_SPEC` ✝✝✝✝✝✝✝✝✝✝ | 2 + 3 | each anchor kind interposed inside another kind's block — the **reverse** cycle of `[G24:10]`'s ordered product |
| `_REDUCTION_OPERAND_SPACING_SPEC` ✝✝✝✝✝✝✝✝✝✝ | 2 | `ADMITS:`/`EXCLUDES:` packed, padded and trailing-padded |
| `_TOKEN_INTERIOR_SPEC` (bd#39 r11) | 2 | a reduction token with an interior space, in `EXCLUDES` and in `ADMITS` |
| `_OPERAND_CHARACTER_SPEC` (bd#39 r10) | 1 + 3 | interior colons on level, row and seam operands; a doubled interior space; a colliding seam pair |
| `_INTERIOR_SPACE_SPEC` (bd#39 r9) | 1 + 3 | interior spaces in a level name, a seam name and a row operand; two level names sharing a first token |
| `_ROW_DASH_FRAMING_SPEC` (bd#39 r8) | 1 | an em dash set closed-up, and one padded with two spaces each side |
| `_PROPERTY_OPERAND_FRAMING_SPEC` (bd#39 r8) | 3 | a whitespace-only property operand, and three packed ones |
| `_PROPERTY_WRITE_POLICY_SPEC` (bd#39 r7) | 3 | a property marker repeated, once filled and once not, in **both orders** |
| `_EMPTY_PROPERTY_OPERAND_SPEC` (bd#39 r6) | 3 | three property markers laid out unfilled; one empty among two filled; a fully pinned control |
| `_ROW_ORDER_COVERAGE_SPEC` (bd#39 r5) | 2 | a row and its `EXCLUDES` above the `LEVEL` whose `ADMITS` governs them |
| `_EMPTY_TOKEN_LIST_SPEC` (bd#39 r5) | 2 | a bare `ADMITS:` and a bare `EXCLUDES:` |
| `_SEPARATOR_SIDES_SPEC` ✝ | 2 | whitespace before the comma, and a trailing comma |
| `_TOKEN_VALIDATION_SPEC` ✝ | 1 + 2 | an invalid `ADMITS` on a row-less level, and two orphan lines carrying invalid tokens |
| `_INDENTED_ADMITS_SPEC` ✝ | 2 | an `ADMITS:` line quoted at an indent inside prose |
| `_SAME_SUBJECT_TWO_KINDS_SPEC` ✝ | 1 + 3 | one name declared as both a `LEVEL` and a `SEAM` |
| `_REDUCTION_DELIMITER_SPEC` ✝ | 1 + 2 | comma-without-space tokens; a row with no em dash; a second em dash inside a description |
| `_INDENTED_PROPERTY_SPEC` ✝✝✝✝✝✝✝✝✝✝ | 1 + 3 | indented property and row markers quoted inside prose, below a **bare** `SEAM:` |
| `_DISTANT_ADMITS_SPEC` ✝✝✝✝✝✝✝✝ | 2 | `ADMITS` lines far from their `LEVEL` — across a whole `SEAM` block, and across a blank line |
| `_ROW_OPERAND_SPACING_SPEC` ✝✝✝✝✝✝✝ | 2 | a packed and a space-padded `NON-UNIFORMITY:` operand, plus a lower-case `admits:` |
| `_ROW_MARKER_CASE_SPEC` ✝✝✝✝✝✝ | 2 | `Admits:`/`Non-Uniformity:`/`Excludes:` in Title and lower case, plus an **indented** `Excludes:` |
| `_ADMITS_BASE_CASE_SPEC` ✝✝✝✝✝✝ | 2 | an `ADMITS` line before any `LEVEL`, and a duplicated `ADMITS` token |
| `_CONFORMANT_SPEC` | control | every level covered, seam fully pinned; `EXCLUDES` tokens and property lines in **non-canonical order** |
| `_MALFORMED_SPEC` | contract | free-form prose, no markers |
| `_LOWERCASE_MARKERS_SPEC` | grammar | `LEVEL`, `SEAM` and the three property markers in lowercase — **five of eight**, corrected in v8; it carries no row, `EXCLUDES` or `ADMITS` line |
| `""` (inline) | contract | empty document |
| `"LEVEL: phases"` (inline) | contract | document whose last line is a bare `LEVEL:` |
| `"SEAM: tempfile.mkdtemp"` (inline) ✝ | contract | document whose **only** line is a bare `SEAM:` — the literal §0.2 founding case |

### 3.2 Check 1 — `missing_non_uniformity_row`

| ID | Candidate | Killed by | How it dies |
|---|---|---|---|
| C1-1 | K1 presence-only ("the level is declared ⇒ fine") | `_MISSING_ROW_SPEC` | 3 declared levels, 0 rows, 3 findings required; presence-only returns none |
| C1-2 | K2 first-only | `_MISSING_ROW_SPEC`, `_ROW_LEVEL_BINDING_SPEC`, `_ROW_LEVEL_SUBSTRING_REVERSE_SPEC` ✝ | offenders sit at positions 2 and 3 (resp. 3 of 4, 3 of 3); a first-only walk misses them |
| C1-3 | K3 last-only | same three | offenders sit at positions 1 and 2 (resp. 1 of 4, 1 of 3) |
| C1-4 | K4 document-global presence | `_ROW_LEVEL_BINDING_SPEC` | the document **does** contain rows (`workers`, `audit_field`); `phases` and `audit` must still be flagged |
| C1-5 | K5 substring, direction A (`level in row_operand`) | `_ROW_LEVEL_BINDING_SPEC` | `audit` ⊂ `audit_field`; the lint wrongly clears `audit` |
| C1-6 | K6 substring, direction B (`row_operand in level`) ✝ | `_ROW_LEVEL_SUBSTRING_REVERSE_SPEC` ✝ | `audit` ⊂ `audit_field` **and** ⊂ `audit_gate`; the lint wrongly clears both, both must be flagged |
| C1-7 | K7 row bound by proximity, not by operand | `_CHECK2_WRONG_LEVEL_BINDING_SPEC` | the row is adjacent to `beta_gate` and names `alpha_gate`; proximity binding flags `alpha_gate` for a missing row and clears `beta_gate` — the negative assertions invert |
| C1-8 | K9 cardinality: `len(rows) == len(levels)` ⇒ conformant | `_LEVEL_CASE_MISMATCH_SPEC` | 1 level, 1 row, counts equal, identities differ; a counting lint returns no findings |
| C1-9 | K11 case-folding operands | `_LEVEL_CASE_MISMATCH_SPEC` | `Audit_Case` vs `audit_case`; a folding lint clears the level |
| C1-10 | K11 case-folding `subject` on output | `_MISSING_ROW_SPEC` (`Audit_Field`) | `subject` is asserted against the mixed-case literal; a normalising lint reports `audit_field` |
| C1-14 | order-sensitive check 1 (a level is discharged only by a row **below** it) ✝✝✝ | `_ROW_BEFORE_LEVEL_SPEC` ✝✝✝ | `late_level`'s row precedes its declaration; an order-sensitive lint flags a discharged level (`[G24:9]`) |
| C1-11 | K7/lookahead crash on a trailing bare `LEVEL:` | `"LEVEL: phases"` inline | an `i+1` lookahead raises `IndexError` instead of reporting |
| ~~C1-12~~ | **WITHDRAWN, gate round 1 MAJOR-3.** Claimed `_ORPHAN_MARKER_LINES_SPEC` killed forward-crediting for check 1. **False:** crediting an orphaned `EXCLUDES` forward adds *coverage*, and check 1 is defined over *rows*, so the finding fires either way and the assertion passed for the GREEN it was written to kill. Superseded by C2-21 | — | — |

### 3.3 Check 2 — `missing_reductions`

| ID | Candidate | Killed by | How it dies |
|---|---|---|---|
| C2-1 | K1 presence-only ("row exists ⇒ fine"), i.e. only half of check 2 implemented | `_CHECK2_SPEC` (`phases` has no `EXCLUDES` at all) + all present-but-short rows | the short rows are invisible to it |
| C2-2 | coverage checked **only when an explicit `ADMITS:` is present** | `_CHECK2_SPEC` | no row in that fixture carries `ADMITS`; all five must be flagged |
| C2-3 | K8 default set missing `all` / `any` / `first` / `last` | `_CHECK2_SPEC` | four rows each omit exactly one distinct member; whichever member the lint dropped, that row goes unflagged |
| C2-4 | explicit `ADMITS` override ignored (all four always demanded) | `_CHECK2_EXPLICIT_ADMITS_SPEC` | `payload_field` `ADMITS: any, all` and covers both; a lint demanding four false-flags it |
| C2-5 | K2 first-only / K3 last-only | `_CHECK2_SPEC` (5 offenders), `_CHECK2_EXPLICIT_ADMITS_SPEC`, `_CHECK2_CARDINALITY_SPEC` ✝ | in each the offenders **bracket** a conformant row. `_REDUCTION_TOKEN_VOCABULARY_SPEC` is **not** claimed here (gate round 1 MINOR-8): its bracketing work is done by the positive assertions on `upper_excludes` (first) and `bogus_extra` (last), and its `lower_control` negative assertion earns no independent candidate beyond over-firing |
| C2-6 | **K9 membership independent of cardinality** — `len(excludes) < len(admits)` ✝ | `_CHECK2_CARDINALITY_SPEC` ✝ | `EXCLUDES: any, all, first, frist` and `EXCLUDES: any, all, first, all` are both **four** tokens against a four-member default: the comparison passes, `last` is missing, both must be flagged. **This is round-4 finding B1.** |
| C2-7 | K11 reduction tokens case-folded ✝ | `_REDUCTION_TOKEN_VOCABULARY_SPEC` ✝ | `EXCLUDES: ANY, ALL, FIRST, LAST` is four tokens covering nothing (`[G24:1]`); a folding lint clears it. `ADMITS: Any, All` likewise |
| C2-8 | unrecognised token silently ignored ✝ | `_REDUCTION_TOKEN_VOCABULARY_SPEC` ✝ (`bogus_extra`) | `EXCLUDES: any, all, first, last, sometimes` covers all four; only the token rule flags it |
| C2-9 | one finding emitted **per missing reduction** ✝ | `_CHECK2_CARDINALITY_SPEC` ✝ | asserts exactly **one** finding per offending subject (`[G24:3]`) |
| C2-10 | K7 `EXCLUDES` bound to the nearest preceding `LEVEL` ✝ | `_EXCLUDES_ROW_BINDING_SPEC` ✝ | correct binding: `alpha_bind` conformant, `beta_bind` short. Proximity binding: both `EXCLUDES` land on `beta_bind`, which becomes conformant, and `alpha_bind` becomes short — **exactly inverted**, so both assertions fire |
| C2-11 | K7 `EXCLUDES` bound to the **following** row ✝ | `_EXCLUDES_ROW_BINDING_SPEC` ✝ | also inverts: `beta_bind`'s row takes the complete list, `alpha_bind`'s takes none |
| C2-12 | K7 `ADMITS` bound to the following `LEVEL` | `_ADMITS_WRONG_NEIGHBOUR_SPEC` | inverts both levels simultaneously |
| C2-13 | K7 row bound by proximity for check 2 specifically | `_CHECK2_WRONG_LEVEL_BINDING_SPEC` | all four assertions (2 positive, 2 negative) invert |
| C2-14 | invalid `ADMITS` token ignored | `_ADMITS_INVALID_TOKEN_SPEC` | `bogus` must produce the finding although `EXCLUDES` covers all four |
| C2-15 | K10 fixed required order for `EXCLUDES` tokens | `_CONFORMANT_SPEC` | `EXCLUDES: last, any, first, all` is out of the canonical order every other fixture uses; an order-sensitive lint false-flags the control |
| C2-16 | check 2 fires on a level with **no** row | `_CHECK2_WRONG_LEVEL_BINDING_SPEC` | asserts `beta_gate` gets check 1 and **not** check 2 |
| C2-21 | K7, base case done properly: an **unanchored** `EXCLUDES` credited **forward** onto a row that has none of its own ✝✝ | `_FORWARD_CREDIT_SPEC` ✝✝ | the orphaned `EXCLUDES` is complete and `fwd_level`'s row carries no `EXCLUDES`; correct binding flags the row, forward-crediting **completes** it and clears it — the verdict moves, which is what C1-12 lacked. *(Renumbered from C1-13 in v4: gate round-2 MINOR-1 — it was filed under check 1 while asserting `missing_reductions`.)* |
| C2-18 | K13 `subject` from the row's operand, `ADMITS` from the nearest preceding `LEVEL` ✝✝ | `_ADMITS_CROSS_AXIS_SPEC` ✝✝ | the two axes are crossed in both directions: `alpha_cross` (own `ADMITS` two, row under an `ADMITS`-less level) is false-flagged by the proximity lookup; `gamma_cross` (default four, row under a level admitting two) is cleared by it. **Gate round 1 MAJOR-1** — no carried fixture crossed the axes at all |
| C2-19 | a row naming an **undeclared** level skipped entirely (`if operand not in levels: continue`) ✝✝ | `_UNDECLARED_LEVEL_ROW_SPEC` ✝✝ | the undeclared row is **short**, so skipping it returns nothing and `[G24:6]` is discriminated. **Gate round 1 MAJOR-2** — v2 pinned the clause with no fixture that could fail |
| C2-20 | K12 coverage as set **equality** instead of superset ✝✝ | `_EXCLUDES_SUPERSET_SPEC` ✝✝ | `superset_level` admits two and excludes four; `==` false-flags it. **Gate round 1 MAJOR-4** — no fixture had a strict superset, and the one row with an extra token carries an *unrecognised* one, so it is flagged under both readings and separates nothing |
| C2-22 | one finding **per missing reduction** instead of one per `(kind, subject)` ✝✝✝ | `_EXCLUDES_SUPERSET_SPEC` ✝✝ (count assertion added in round 3) | `short_level` is missing **two** reductions and carries no unrecognised token, so the collapse rule can finally fail. **Gate round-2 MAJOR-1** — the only prior count assertion stood on a row missing exactly one |
| C2-23 | a **duplicated recognised token** in `EXCLUDES` treated as a defect ✝✝✝ | `_BENIGN_DUPLICATE_SPEC` ✝✝✝ | `dup_level` covers all four with `all` written twice and is **conformant**; a duplicate-punishing lint false-flags it (`[G24:4]`) |
| C-CASE2 | `ADMITS`/`NON-UNIFORMITY`/`EXCLUDES` matched against **ALL-CAPS only** while the other five are case-folded ✝✝✝✝✝✝ | `_ROW_MARKER_CASE_SPEC` ✝✝✝✝✝✝ | `title_row_level` is conformant only if `Admits:` and `Excludes:` are read; `lower_row_level` is flagged only if its lower-case row and short `excludes:` are. **Gate round-5 MAJOR-1**, confirmed at 42/42 |
| C2-24 | an **unanchored `ADMITS`** raised on, or credited forward to the next `LEVEL` ✝✝✝✝✝✝ | `_ADMITS_BASE_CASE_SPEC` ✝✝✝✝✝✝ | the orphan would **complete** `fwd_admits_level`, which must be flagged. **Gate round-5 MAJOR-2** — `[G24:5]`'s third anchor rule; a raising lint also breaches §2.1 |
| C2-25 | a **duplicated recognised token in `ADMITS`** treated as a defect ✝✝✝✝✝✝ | `_ADMITS_BASE_CASE_SPEC` ✝✝✝✝✝✝ | `dup_admits_level` admits {any, all} with `any` twice and covers both: conformant. **Gate round-5 MAJOR-3**, the `ADMITS` mirror of round-2's MAJOR-2 |
| C2-29 | an anchor of one kind **closing a live anchor** of another (`if row: cur_level = None`, `if LEVEL: cur_seam = None`, `if SEAM: cur_row = None`) ✝✝✝✝✝✝✝✝✝✝ | `_ANCHOR_CYCLE_SPEC` ✝✝✝✝✝✝✝✝✝✝ | the reverse cycle of the three cells already filled; each mutant is the exact mirror of one already killed, and all three passed 47/47. **bd#39 gate round-1 MAJOR-1** — the RED enforced this before the spec recorded it |
| C2-31 | `ADMITS:` recognised after a **strip** while the other seven markers require flush-left ✝ | `_INDENTED_ADMITS_SPEC` ✝ | a quoted `ADMITS: any, all` **narrows** the admitted set of the level above it, so a genuinely short row reads as complete and its finding is suppressed. **bd#39 round-2 MAJOR-1, type (a)**, confirmed at 50/50 |
| C2-35 | coverage evaluated **in reading order**, against the admitted set as known when the `EXCLUDES` arrives (bd#39 r5) | `_ROW_ORDER_COVERAGE_SPEC` (bd#39 r5) | `late_admits`' row and `EXCLUDES` precede the `LEVEL` whose narrow `ADMITS` governs them, and cover it exactly: conformant. Inline evaluation sees the default four and flags it. **bd#39 round-4 MAJOR-1**, confirmed at 55/55 — `[G24:9]`'s second consequence, unmeasured because `_ROW_BEFORE_LEVEL_SPEC`'s level has no `ADMITS` |
| C2-36 | a **bare `ADMITS:`** read as an empty admitted set, clearing every row on that level; a **bare `EXCLUDES:`** read as ignorable (bd#39 r5) | `_EMPTY_TOKEN_LIST_SPEC` (bd#39 r5) | both readings passed 55/55 — the spec decided the input via `[G24:11]` while §6 declared it undecided. **bd#39 round-4 MAJOR-2, type (a)**; closed by `[G24:14]` in the safe direction |
| C2-37 | interior spaces stripped from reduction tokens (`operand.replace(" ", "")`) (bd#39 r11) | `_TOKEN_INTERIOR_SPEC` (bd#39 r11) | `fi rst` is outside the four, so the pinned reading flags the row twice over while the survivor reads `first` and returns nothing — §2.4's own rationale verbatim, *silent exactly when the row is otherwise complete*. **bd#39 round-10 MAJOR**, 63/63 at zero divergence |
| C1-18 | the operand split on **every** colon (`line.split(":")[1]`), or runs of interior whitespace collapsed (bd#39 r10) | `_OPERAND_CHARACTER_SPEC` (bd#39 r10) | `pkg.mod:read`/`pkg.mod:write` collide on the prefix, the union of their blocks looks complete, and a seam declared and not pinned ships conformant — §0.2's founding case, silent. **bd#39 round-9 MAJOR**, both 62/62 at zero divergence |
| C1-17 | the operand taken as its **first whitespace token** (`operand.split()[0]`) (bd#39 r9) | `_INTERIOR_SPACE_SPEC` (bd#39 r9) | satisfies all 24 framing cells by accident; `payload field` yields a `subject` absent from the document, and `audit gate`/`audit step` collapse so a declared, undischarged level is **silently** cleared. **bd#39 round-8 MAJOR**, 61/61 at zero divergence |
| C1-16 | the row-operand delimiter read as `" — "` (dash **with** its spaces) rather than the dash itself (bd#39 r8) | `_ROW_DASH_FRAMING_SPEC` (bd#39 r8) | a closed-up dash makes the survivor take the whole operand, match no level, and report a conformant document. **bd#39 round-7 MAJOR-1**, 59/59 at **zero divergence** |
| C3-24 | a **whitespace-only** property operand read as pinning; a property operand taken at a fixed offset (bd#39 r8) | `_PROPERTY_OPERAND_FRAMING_SPEC` (bd#39 r8) | wrong in opposite directions — one ships §0.2's founding case, the other reports a fully pinned seam unpinned. **bd#39 round-7 MAJOR-2**, both 59/59 at **zero divergence** |
| C2-33 | whitespace **before** the comma not admitted; a trailing comma yielding an unrecognised empty token ✝ | `_SEPARATOR_SIDES_SPEC` ✝ | `any ,all , first,last` covers four and is conformant — `replace(", ", ",").split(",")` reads `any `/`all ` and flags it. **bd#39 round-3 MAJOR-1, type (a)**, confirmed at 53/53 |
| C2-34 | an invalid `ADMITS` token requiring a row before it fires; or tokens validated **eagerly** on the line rather than on the bound level ✝ | `_TOKEN_VALIDATION_SPEC` ✝ | exact-set assertion: `bad_admits_no_row` must yield **both** kinds, and the two orphan lines carrying invalid tokens must yield nothing (`[G24:12]`, `[G24:13]`). **bd#39 round-3 MAJOR-2** — a contradiction between two frozen sentences, not a coverage gap |
| C2-32 | the token separator read as the two-character `", "`; a row operand requiring an em dash, or split on the **last** one ✝ | `_REDUCTION_DELIMITER_SPEC` ✝ | `any,all,first,last` is four tokens, `NON-UNIFORMITY: no_dash_row` discharges its level, and a second em dash inside a description changes nothing (`[G24:11]`) |
| C2-30 | a **fixed offset** applied to reduction lines only (`line[line.index(":") + 2:]`, no strip) ✝✝✝✝✝✝✝✝✝✝ | `_REDUCTION_OPERAND_SPACING_SPEC` ✝✝✝✝✝✝✝✝✝✝ | `EXCLUDES:any, …` yields the token `ny`, so a conformant row is reported both short and carrying an unrecognised token — F-1's over-firing class. **bd#39 gate round-1 MAJOR-2**, confirmed at 48/48 |
| C2-28 | one shared "current declaration" variable written by both `LEVEL:` and `NON-UNIFORMITY:` ✝✝✝✝✝✝✝✝✝ | `_SHARED_ANCHOR_SPEC` ✝✝✝✝✝✝✝✝✝ | a `LEVEL:` is interposed between a row and its `EXCLUDES` — the case no other fixture contains. **Gate round-8 MAJOR**, confirmed at 46/46, against v10's explicit claim that `_EXCLUDES_ROW_BINDING_SPEC` covered this cell |
| C2-27 | `ADMITS` bound only when the **literal previous line** was its `LEVEL:`, or one "current declaration" variable serving `LEVEL` and `SEAM` ✝✝✝✝✝✝✝✝ | `_DISTANT_ADMITS_SPEC` ✝✝✝✝✝✝✝✝ | `distant_admits`' `ADMITS` reaches back across a whole `SEAM` block; `blank_gap_admits`' across a blank line — the house style one line earlier. `distant_invalid` gives the positive discriminator: its distant `ADMITS` carries an unrecognised token, so a lint that never binds it falls back to the default four, sees full coverage and reports nothing. **Gate round-7 MAJOR**, both candidates confirmed at 45/45 |
| C2-26 | an **unanchored `ADMITS`** registered under a **sentinel level** (`levels.setdefault(cur_level or "", …)`), reported by check 1 with an empty `subject` ✝✝✝✝✝✝✝ | `_ADMITS_BASE_CASE_SPEC`'s third assertion ✝✝✝✝✝✝✝ | both levels there carry rows, so **no** check-1 finding is correct in that document. **Gate round-6 MAJOR** — `[G24:5]`'s empty cell, confirmed at 44/44 |
| C-ROWOP | a **fixed offset** on `NON-UNIFORMITY:` alone, whose operand has its own em-dash extraction path; and `admits:` unrecognised in lower case ✝✝✝✝✝✝✝ | `_ROW_OPERAND_SPACING_SPEC` ✝✝✝✝✝✝✝ | `packed_row` is conformant only if the packed row operand AND the lower-case `admits:` are both read; `spaced_row` is flagged only if its leading spaces are stripped. **Gate round-6 advisories 1 and 2** |
| C2-17 | check 1 fires on a level whose row is merely short | `_CHECK2_SPEC` | asserts **no** `missing_non_uniformity_row` anywhere in that fixture |

### 3.4 Check 3 — `seam_not_pinned`

| ID | Candidate | Killed by | How it dies |
|---|---|---|---|
| C3-1 | **a seam registered only on encountering a property line** ✝ | `_SEAM_NOT_PINNED_SPEC` ✝, `"SEAM: tempfile.mkdtemp"` inline ✝ | a bare `SEAM:` with zero property lines is never registered and never flagged. **This is round-4 finding B2, and the fixture bd#22 deleted in `9947142`.** |
| C3-2 | K1 presence-only ("some property line exists ⇒ pinned") | the three one-property-missing fixtures | each offender carries the other two properties |
| C3-3 | only `ATTRIBUTE-PATH` checked | `_SEAM_MISSING_BINDING_TIME_SPEC`, `_SEAM_MISSING_NORMALISATION_SPEC` | offenders pin `ATTRIBUTE-PATH` correctly |
| C3-4 | only `ATTRIBUTE-PATH` + `BINDING-TIME` checked | `_SEAM_MISSING_NORMALISATION_SPEC` | offender pins both |
| C3-5 | K2 first-only | `_SEAM_MISSING_BINDING_TIME_SPEC` (offender last), `_SEAM_NOT_PINNED_SPEC` ✝, `_SEAM_NAME_SUBSTRING_REVERSE_SPEC` ✝, `_SEAM_PROPERTY_CARDINALITY_SPEC` ✝ | in each of the last three an offender is **last** |
| C3-6 | K3 last-only | `_SEAM_MISSING_ATTRIBUTE_PATH_SPEC`, `_SEAM_MISSING_NORMALISATION_SPEC`, and the same three ✝ | in each an offender is **first** |
| C3-7 | K4 document-global presence of the property markers | `_SEAM_MISSING_ATTRIBUTE_PATH_SPEC` | the document contains `ATTRIBUTE-PATH` (on the conformant seam); the offender must still be flagged |
| C3-8 | **K9 cardinality: `len(property_lines) >= 3`** ✝ | `_SEAM_PROPERTY_CARDINALITY_SPEC` ✝ | offenders carry three lines of which two repeat one marker (`[G24:4]`); the count passes, the set is short |
| C3-9 | one finding emitted **per missing property** ✝ | `_SEAM_NOT_PINNED_SPEC` ✝ | the bare seam is missing all three; asserts exactly **one** finding for it (`[G24:3]`) |
| C3-10 | K5 substring, direction A (long credited from short) | `_SEAM_NAME_SUBSTRING_SPEC` | `Path.read` conformant, `Path.read_text` offending |
| C3-11 | K6 substring, direction B (short credited from long) ✝ | `_SEAM_NAME_SUBSTRING_REVERSE_SPEC` ✝ | `Path.read` / `subprocess.ru` offending, `Path.read_text` / `subprocess.run` conformant |
| C3-12 | K11 seam names case-folded into one seam | `_SEAM_NAME_CASE_MISMATCH_SPEC` | the union of both blocks looks complete; `Path.Read_Text` must still be flagged |
| C3-13 | K11 `subject` normalised on output | `_SEAM_MISSING_BINDING_TIME_SPEC` (`MixedCase.Attribute`), `_SEAM_NOT_PINNED_SPEC` ✝ (`MixedCase.BareSeam`) | asserted against the mixed-case literal |
| C3-14 | K7 property lines bound to the **following** seam | `_SEAM_PROPERTY_WRONG_NEIGHBOUR_SPEC` | inverts both seams simultaneously |
| C3-18 | K9's twin: **exactly-once** property counting rather than set membership ✝✝✝ | `_BENIGN_DUPLICATE_SPEC` ✝✝✝ | `Dup.Seam` carries all three with `NORMALISATION:` twice and is **conformant**. **Gate round-2 MAJOR-2** — `_SEAM_PROPERTY_CARDINALITY_SPEC`'s duplicate-carriers are offenders on the set rule anyway, so they cannot separate the readings |
| C3-19 | `[G24:10]` a **single** "current anchor" variable serving both binding rules ✝✝✝ | `_INTERLEAVED_ANCHORS_SPEC` ✝✝✝ | a row sits between `Mixed.Seam` and its `NORMALISATION:`; a single-anchor lint loses the property and flags a conformant seam |
| C3-15 | K10 fixed required order for property lines | `_CONFORMANT_SPEC` | the control's seam lists `BINDING-TIME`, `NORMALISATION`, `ATTRIBUTE-PATH` in that order |
| ~~C3-16~~ | **WITHDRAWN, gate round 1 MAJOR-3.** Claimed `_ORPHAN_MARKER_LINES_SPEC` killed forward-crediting for check 3. **False:** the credited `NORMALISATION:` still leaves `Orphan.Seam` without `ATTRIBUTE-PATH` and `BINDING-TIME`, so the finding fires either way. Superseded by C3-17 | — | — |
| C3-17 | K7, base case done properly: an **unanchored** property line credited **forward** onto the one property its next seam lacks ✝✝ | `_FORWARD_CREDIT_SPEC` ✝✝ | `Fwd.Seam` carries `ATTRIBUTE-PATH` and `BINDING-TIME` and the orphaned line is exactly the missing `NORMALISATION`; correct binding flags it, forward-crediting clears it |

### 3.5 Whole-function contract

| ID | Candidate | Killed by |
|---|---|---|
| F-1 | over-fires on well-formed input (bd#7's keyword sweep, 11 of 13 mis-scored) | `_CONFORMANT_SPEC` (`findings == []`) |
| F-2 | raises on an unstructured document | `_MALFORMED_SPEC` |
| F-3 | raises on the empty string (`i+1` lookahead over `split("\n")`) | `""` inline |
| F-4 | raises on a marker line that is the document's **last** line, with no following line to look ahead to | `"LEVEL: phases"`, `"SEAM: tempfile.mkdtemp"` ✝ inline |
| F-5 | markers matched case-**sensitively** | `_LOWERCASE_MARKERS_SPEC` for `LEVEL`/`SEAM`/the property markers; `_ROW_MARKER_CASE_SPEC` ✝✝✝✝✝✝ for `ADMITS`/`NON-UNIFORMITY`/`EXCLUDES` — v8: the first alone covered five of eight |
| F-6 | accumulates findings in module-level state across calls | the idempotence test: snapshot-before-second-call, plus `second is not first` |
| F-8 | dereferences a "current anchor" that was never set (raises on an unanchored marker line) ✝ | `_ORPHAN_MARKER_LINES_SPEC` ✝ |
| F-9 | `Finding` **widened** with a third field (`message`, `line_number`) ✝✝ | a set-equality assertion on `dataclasses.fields` — §2.1 pins "exactly two attributes" and nothing measured the *exactly*. Gate round 1 MINOR-7 |
| C-CRLF | an operand kept **verbatim after splitting on `"\n"`**, so `"\r"` stays inside `subject` ✝✝ | `_CRLF_SPEC` ✝✝ — every other fixture is LF-only (`[G24:7]`). Precise form, v6: an implementation that splits on `"\n"` and then **strips** is correct and is not the candidate |
| C-SPACING | the operand taken at a **fixed offset** (marker + one assumed space), or leading whitespace kept ✝✝✝✝✝ | `_OPERAND_SPACING_SPEC` ✝✝✝✝✝ — `LEVEL:packed` and `LEVEL:   padded` are declared without a row, so both subjects must be reported verbatim. **Gate round-4 MAJOR**, confirmed by mutant #29 passing 41/41 without it |
| C-WS | trailing whitespace kept inside the operand ✝✝✝✝ | `_TRAILING_WHITESPACE_SPEC` ✝✝✝✝ — `LEVEL: phases␣␣␣` must still be discharged by its row, and `SEAM: Trailing.Seam␣␣` reported without the spaces |
| C-COLLAPSE | the collapse step keyed on `subject` **alone** rather than on `(kind, subject)` ✝ | `_SAME_SUBJECT_TWO_KINDS_SPEC` ✝ | `cache` is a row-less `LEVEL` **and** a bare `SEAM`, so two findings of different `kind` share one `subject`; keying on `subject` drops one, and which one depends on emission order, unspecified across inputs. **bd#39 round-2 MAJOR-2**, confirmed at 50/50 |
| C1-15 | `NON-UNIFORMITY:` recognised after a strip (the check-1 half of `_INDENTED_PROPERTY_SPEC`, split out per bd#39 round-2 MINOR-C) ✝ | `_INDENTED_PROPERTY_SPEC` assertion 2 ✝ | the indented row markers below `quoted_row_level` discharge nothing, so the level must still be flagged |
| C3-23 | a **mapping** write policy for pinned properties — last-wins (`props[m] = operand`) or first-wins (`setdefault`) (bd#39 r7) | `_PROPERTY_WRITE_POLICY_SPEC` (bd#39 r7) | the two fail on **opposite orders**, so both are carried: `Filled.Then.Empty` kills last-wins, `Empty.Then.Filled` kills first-wins, `Never.Filled` is the positive. **bd#39 round-6 MAJOR**, both confirmed at 58/58 |
| C3-22 | a property marker **present but unfilled** counted as pinning its property (bd#39 r6) | `_EMPTY_PROPERTY_OPERAND_SPEC` (bd#39 r6) | a template seam with all three markers laid out and none filled returns **no findings** under that reading — §0.2's founding case, by a route C3-1's fixture does not cover. **bd#39 round-5 MAJOR**, both readings confirmed at 57/57 |
| C3-21 | the three **property** markers recognised after a strip while declarations and reduction lines require flush-left ✝✝✝✝✝✝✝✝✝✝ | `_INDENTED_PROPERTY_SPEC` ✝✝✝✝✝✝✝✝✝✝ | indented properties quoted below a bare `SEAM:` silently **complete** it and suppress the finding — a seam declared and not pinned, shipped conformant, this AC's founding case (§0.2). **bd#39 gate round-1 MAJOR-3**, confirmed at 48/48 |
| C3-20 | an unanchored property line collected under a **sentinel seam** (`seams.setdefault(cur_seam or "<unnamed>", …)`) and reported as a finding ✝✝✝✝ | `_ORPHAN_MARKER_LINES_SPEC`'s fourth assertion ✝✝✝✝ — `[G24:5]`'s not-a-finding clause was measured for the `EXCLUDES` half only; the seam half needed a guard naming the one seam that legitimately appears. **Gate round-3 MAJOR**, predicted by the gate and confirmed by mutant #28 passing 40/40 without it |
| C-CASE | marker recognition as **two literal spellings** (`startswith(("LEVEL:", "level:"))`) rather than case-insensitive ✝✝✝ | `_MIXED_CASE_MARKERS_SPEC` ✝✝✝ — only ALL-CAPS and all-lowercase were walked; `Title.Conformant` additionally separates recognition of the three property markers from recognition of `Seam:` alone |
| C-INDENT | `line.strip().startswith(...)` treats an indented marker as a declaration ✝✝ | `_INDENTED_MARKER_SPEC` ✝✝ — every other fixture is flush-left (`[G24:8]`) |
| F-7 | `Finding` is not a frozen dataclass (plain object, dict, `NamedTuple`, mutable dataclass) | `_MISSING_ROW_SPEC`'s test: `dataclasses.is_dataclass` + `FrozenInstanceError` |

---

## 4. RED obligations

- **Every test must fail today**, at `ModuleNotFoundError`/`ImportError` on
  `from conformance.quant_lint import lint_quantifier_completeness` — the module exists as a placeholder and
  **defines no public names** (`[G22:18]`), so the import fails at attribute resolution.
- **Pre-passing tests: NONE are permitted in this lot and none are declared** (§0.6). Every test in the RED
  calls `lint_quantifier_completeness`; there is no absence-shield here (bd#22's `AC-P2` shipped with bd#22).
  A RED measurement showing any passing test is a defect, not a shield.
- **Deferred-import discipline**: every `conformance.*` import happens inside a test body, never at module
  level, so collection stays clean.
- **No monkeypatching anywhere** (§0.8's `[G22:2]` note).
- Every test docstring names the candidate ID(s) from §3 it kills.

## 5. Coverage diff for this round — the §0.9 obligation

**Direction 1 — deletions and modifications: NONE.** This round's fixture set is a strict superset of bd#22's
round-9 AC-C5 set. Every fixture constant carried from `d39371f` is byte-identical, and every round-9 AC-C5
test function is present. Mechanically verifiable:

```
git show d39371f:engine_py/tests/test_bd22_contracts.py
```

— extract each `_*_SPEC` constant and each `def test_ac_c5_*` name (via `ast`, comparing constants by
`literal_eval` rather than by eye) and confirm containment in `engine_py/tests/test_bd24_quant_lint.py`.

**RUN on the executing host, output recorded here rather than promised** (gate round 1 MINOR-9: an unexecuted
check is exactly the state round 4 rejected on):

```
carried fixture constants: 17/17 present, 0 modified
  absent : NONE
  changed: NONE
carried tests: 20/20 present
  absent : NONE
```

Re-run after round 2's additions with the same result — round 2 is additive, so containment cannot have
regressed, and the check is run anyway rather than reasoned about.

Consequently every candidate the round-9 set killed is still killed by the same fixture, unchanged: C1-1,
C1-2/3 (partially), C1-4, C1-5, C1-7, C1-8, C1-9, C1-10, C1-11; C2-1..C2-5, C2-12..C2-17; C3-2..C3-4,
C3-5/6 (partially), C3-7, C3-10, C3-12, C3-13, C3-14, C3-15; F-1..F-7. **The one thing bd#22's round 3 deleted
(`_SEAM_NOT_PINNED_SPEC`, C3-1) is restored under its original name**, which is what round 4 rejected on.

**Direction 2 — additions.** Eight new fixture constants, one new inline input, nine new tests. Per §0.9(2), each added assertion is
listed in §3 with the candidate it kills; an assertion is admissible only if a plausible implementation exists
that it would fail. The additions that are *not* traceable to a candidate would be the finding — there are
none: every ✝ row in §3 names both the candidate and the mechanism by which the candidate dies.

Added-contradiction check (the second half of the corrected (a)/(b) discriminator): the two new semantics
`[G24:1]` and `[G24:2]` were replayed against every carried fixture before freezing:

| Carried fixture | `[G24:1]` reduction-token casing | `[G24:2]` `EXCLUDES`→row binding |
|---|---|---|
| `_CHECK2_SPEC` | all tokens lowercase — unaffected | each `EXCLUDES` follows its own row — unaffected |
| `_CHECK2_EXPLICIT_ADMITS_SPEC` | lowercase — unaffected | follows its own row — unaffected |
| `_CHECK2_WRONG_LEVEL_BINDING_SPEC` | lowercase — unaffected | the single `EXCLUDES` follows the `alpha_gate` row, so it binds to `alpha_gate` — **which is what that test already asserted** |
| `_ADMITS_WRONG_NEIGHBOUR_SPEC` | lowercase — unaffected | each `EXCLUDES` follows its own row — unaffected |
| `_ADMITS_INVALID_TOKEN_SPEC` | `bogus` was already a finding — unaffected | unaffected |
| `_ROW_LEVEL_BINDING_SPEC` | lowercase — unaffected | unaffected |
| `_CONFORMANT_SPEC` | lowercase, out of order — unaffected | unaffected |
| all check-1/check-3 fixtures | carry no reduction tokens | carry no `EXCLUDES` |

**Known carried artefact, recorded not repaired** (gate round 1 MINOR-6): the carried
`test_ac_c5_flags_missing_non_uniformity_row_both_directions` docstring refers to "the module docstring's v6
note", which belonged to bd#22's file and has no referent here. It is left **untouched** deliberately — byte
identity of Part 1 is worth more than a tidy cross-reference, and repairing it would put a modification in the
diff that §0.9 direction 1 would then have to account for.

No carried expectation changes. Zero fixture edits were required to adopt either decision — which is the
evidence that they are decisions about an under-specified corner, not re-specifications of a settled one.

The v2 pins were replayed the same way. `[G24:5]` (unanchored lines) touches no carried fixture: every carried
`EXCLUDES` has a preceding row and every carried property line a preceding seam — which is precisely why the
corner was invisible. `[G24:6]` (a row naming no declared level) is walked by exactly one carried fixture,
`_LEVEL_CASE_MISMATCH_SPEC`, whose row covers all four reductions, so the pin produces no finding there and its
single assertion is unaffected.

### Round 2 (gate round 1 closures) — coverage diff

**Direction 1: still zero deletions and zero fixture modifications.** Six fixtures added, seven tests added,
36 tests total. The gate proposed *editing* `_CHECK2_WRONG_LEVEL_BINDING_SPEC` (give `alpha_gate` an `ADMITS`
line) and `_LEVEL_CASE_MISMATCH_SPEC` (shorten its row). Both were closed by **addition** instead, because each
edit would have destroyed the coverage the carried fixture already carries: the first is the only fixture with a
present-but-short row bound by operand across a level boundary, and the second's row must stay complete for its
case-folding assertion to isolate casing. Adding costs two fixtures; editing would have traded C2-13 and C1-9 for
C2-18 and C2-19 and left the round net-flat — the exact bookkeeping §0.9 exists to make visible.

Two things **were** edited, neither a fixture: the module docstring's spec-version citation (MINOR-5), and the
prose of `_ORPHAN_MARKER_LINES_SPEC`'s comment and its test's docstring, which asserted a mechanism that does not
exist. The fixture *string* and all three of its assertions are unchanged, so its real kills (F-8 and the
manufactures-a-finding candidate) are untouched; only the false claim about forward-crediting is withdrawn.

**Direction 2: added-assertion audit.** Every assertion added in round 2 is listed in §3 with its candidate.
The round-1 failure this audit exists to catch happened *inside* round 1 — C1-12 and C3-16 named candidates that
their fixture could not kill, and the pass/fail count did not move because the assertions passed for both the
right and the wrong implementation. The lesson is recorded as a sharpening of §0.9(2): **naming a candidate is
not the check; the check is stating what would have to change in the output for the assertion to fail.** Every
✝✝ row in §3 states that movement explicitly ("the verdict moves", "false-flags it", "clears it").

### Round 3 (gate round 2 closures) — coverage diff

**Direction 1: still zero deletions and zero fixture modifications.** Four fixtures added, four tests added, one
**assertion** added to an existing round-2 test (`short_level`'s count), and two docstrings edited to drop the
withdrawn C1-12/C3-16 citations. No fixture string changed anywhere, verified by the committed script:

```
$ python3 engine_py/tests/_bd24_containment_check.py
carried fixture constants: 17/17 present, 0 modified
carried tests: 20/20 present
totals now: 35 fixtures (17 carried + 18 added), 40 tests
CONTAINMENT HOLDS — the round is additive over the round-9 fixture set.
```

**Direction 2, in its sharpened form — the clause-falsifiability audit.** Gate rounds 1 and 2 produced four
findings of one shape between them (`[G24:5]`, `[G24:6]`, `[G24:3]`, `[G24:4]`): a clause carefully worded, given
a fixture that *walks* it, and never given a fixture whose expected output would **differ** if the clause were
read the other way. So the audit below is now run over every normative clause, not only over new assertions:

| Clause | Axes → cells (§0.9(3)) | Fixture whose expected output flips, per cell |
|---|---|---|
| P1 operands verbatim/case-sensitive | `_LEVEL_CASE_MISMATCH_SPEC`, `_SEAM_NAME_CASE_MISMATCH_SPEC` |
| P2 `ADMITS` → preceding `LEVEL` | {direction, selection, **distance**} | `_ADMITS_WRONG_NEIGHBOUR_SPEC` (direction), `_EXCLUDES_SUPERSET_SPEC` (selection among three consecutive), `_DISTANT_ADMITS_SPEC` (distance, **v10** — §0.9(3b)) |
| P3 property → preceding `SEAM` | {direction, selection, distance} | `_SEAM_PROPERTY_WRONG_NEIGHBOUR_SPEC` (direction/selection), `_SEAM_MISSING_NORMALISATION_SPEC` + `_INTERLEAVED_ANCHORS_SPEC` (distance) |
| P4 row → level by operand | `_CHECK2_WRONG_LEVEL_BINDING_SPEC`, `_ROW_LEVEL_BINDING_SPEC` |
| `[G24:1]` token casing/vocabulary | {`ADMITS`, `EXCLUDES`} × {wrong-case, non-vocabulary} = **4 cells** | `upper_excludes`, `bogus_extra`/`frist`, `Upper_Admits`, `_ADMITS_INVALID_TOKEN_SPEC` |
| `[G24:2]` `EXCLUDES` → preceding row | `_EXCLUDES_ROW_BINDING_SPEC` (inverts under both wrong directions) |
| `[G24:3]` collapse to one per `(kind, subject)` | {same, different `kind`} × {same, different `subject`} = **4 cells**, one trivial | same/same: `_SEAM_NOT_PINNED_SPEC` (check 3), `_EXCLUDES_SUPERSET_SPEC`'s `short_level` count (check 2). Different `kind`/same `subject`: `_SAME_SUBJECT_TWO_KINDS_SPEC` (**bd#39 v3**, C-COLLAPSE) — empty by construction until then, since level names were bare identifiers and seam names dotted, so no name was ever both |
| `[G24:4]` repetition collapsed, not punished | {property lines, `EXCLUDES`, `ADMITS`} × {set-not-count, not-punished} = **6 cells** | `_BENIGN_DUPLICATE_SPEC` (first two; carriers **conformant**, so the clause can fail) + `_ADMITS_BASE_CASE_SPEC` (third, v8) |
| `[G24:5]` unanchored lines | {P2, P3, `[G24:2]`} × {not-a-finding, not-credited-forward} = **6 cells** | not-a-finding: `_ORPHAN_MARKER_LINES_SPEC` assertions 3 and 4 (`[G24:2]`, P3) + `_ADMITS_BASE_CASE_SPEC` assertion 3 (P2, **v9 — the empty cell**). Not-credited-forward: `_FORWARD_CREDIT_SPEC` (`fwd_level`, `Fwd.Seam`) + `_ADMITS_BASE_CASE_SPEC` assertion 1 (P2) |
| `[G24:6]` undeclared-level row still checked | `_UNDECLARED_LEVEL_ROW_SPEC` — the row is **short** |
| `[G24:8]` markers at line start | all **eight** markers, enumerated | `LEVEL`, `SEAM`: `_INDENTED_MARKER_SPEC`. `EXCLUDES`: `_ROW_MARKER_CASE_SPEC` (v8). `ATTRIBUTE-PATH`, `BINDING-TIME`, `NORMALISATION`, `NON-UNIFORMITY`: `_INDENTED_PROPERTY_SPEC` (bd#39 v2). **`ADMITS`: `_INDENTED_ADMITS_SPEC` (bd#39 v3, C2-31)** — v2 declared this row complete at eight and named seven, which is the parent's exit-criterion shape inside the correction written to close round 1 |
| `[G24:9]` order-independent row binding | {discharge, coverage} × {row above, row below} = **4 cells** | discharge: `_ROW_BEFORE_LEVEL_SPEC` (above), everywhere (below). Coverage: `_ROW_ORDER_COVERAGE_SPEC` (above, **bd#39 v5** — the empty cell), every `ADMITS`-bearing fixture (below) |
| `[G24:10]` anchors tracked independently | {`LEVEL`, `SEAM`, row} **ordered** (victim × intruder) = **6 cells**, and the table is then closed | victim `SEAM`/intruder row: `_INTERLEAVED_ANCHORS_SPEC`. Victim `LEVEL`/intruder `SEAM`: `_DISTANT_ADMITS_SPEC` (v10). Victim row/intruder `LEVEL`: `_SHARED_ANCHOR_SPEC` (v11 — v10 recorded `_EXCLUDES_ROW_BINDING_SPEC` here, which kills C2-10, a **different** candidate). The reverse cycle — victim `LEVEL`/intruder row, victim `SEAM`/intruder `LEVEL`, victim row/intruder `SEAM`: `_ANCHOR_CYCLE_SPEC` (**bd#39 v2**, C2-29). Third consecutive round this row was wrong: recorded as one cell, then three with one mis-filled, then three when the text gives six |
| §2.2 markers case-insensitive | all **eight** markers | `_LOWERCASE_MARKERS_SPEC` + `_MIXED_CASE_MARKERS_SPEC` (five) + `_ROW_MARKER_CASE_SPEC` (the other three, v8) |
| §2.6 coverage is ⊇, not = | `_EXCLUDES_SUPERSET_SPEC` |
| §2.2 `ADMITS` absent means all four | `_CHECK2_SPEC` — no row carries `ADMITS`, so every finding there depends on the default |
| §2.1 must not raise; `Finding` shape | `_MALFORMED_SPEC`, `""`, `"LEVEL: phases"` (F-2/F-3/F-4) and the `is_dataclass`/`FrozenInstanceError`/field-set assertions (F-7, F-9) |
| `[G24:7]`/P1 what the operand IS — **{framing, interior}** (bd#39 v9; v8 enumerated framing only, §0.9(3a)). Framing: **19 of 24 cells by fixture, 5 by construction** — the three property markers share one dispatch branch, so `Whitespace.Only` collapses two trailing cells and all three terminator cells (round-8 MINOR-A: v8 declared 24 and accounted for 15). Interior: **{space, repeated space, colon} × the operand-bearing markers** — plus **comma and em dash, DECLARED OUT on input implausibility** (§0.9(3e), gate round-11 ruling): both satisfy the delimiter-role leg and neither satisfies the plausible-document leg, so a surviving mutant exists for each and neither is fixtured. Reopenable if a consuming lot ever produces such a document; that is what distinguishes this disposition from *by construction*, where no input breaks the reason at all (bd#39 v10: one cell per marker was what let the space stand for every character, §0.9(3a) one grain down). Filled by `_INTERIOR_SPACE_SPEC`, `_OPERAND_CHARACTER_SPEC` and `_TOKEN_INTERIOR_SPEC`. **BY CONSTRUCTION, with the input that would break the reason stated beside it** (bd#39 round-10 discipline): *property* operands — an interior character cannot change whether a string is empty, and emptiness is all that is tested, so **no input breaks it**; that is what makes the cell genuinely closed. The *reduction* operands were recorded closed in v10 on the reason that they never become a `subject` — **true and irrelevant**, since their interior feeds **recognition** (`[G24:1]`), not `subject`. The sentence could not be written for them, and the attempt is what exposed the gap (bd#39 round-10 MAJOR) | {terminator, trailing, leading} × the **eight operand-bearing markers** {`LEVEL`, `SEAM`, `NON-UNIFORMITY`, `ADMITS`, `EXCLUDES`, `ATTRIBUTE-PATH`, `BINDING-TIME`, `NORMALISATION`} = **24 cells** — five markers until `[G24:15]` made property operands matter (bd#39 round-7 MAJOR-2, §0.9(3a): the row saying five was the claim under audit) | `LEVEL`/`SEAM`: `_CRLF_SPEC`, `_TRAILING_WHITESPACE_SPEC`, `_OPERAND_SPACING_SPEC`. `NON-UNIFORMITY`: `_ROW_OPERAND_SPACING_SPEC` (v9). `ADMITS`/`EXCLUDES`: `_REDUCTION_OPERAND_SPACING_SPEC` (bd#39 v2, C2-30). The three **property** markers: `_PROPERTY_OPERAND_FRAMING_SPEC` (**bd#39 v8**, C3-24) — every reduction line in 43 fixtures wrote exactly one space after its colon, so a fixed offset on those lines alone passed 48/48 |
| §2.6 checks 1 and 2 independent | `_CHECK2_WRONG_LEVEL_BINDING_SPEC` (2×2), `_CHECK2_SPEC` |

### Round 3 — EXECUTED candidate simulation, not reasoned

`[G22:13]` asks for the plausible implementations to be enumerated and each shown to die. Through gate round 2
that was done **on paper**, by both me and the gate — neither gate round had a shell. Round 3 ran it as code: a
reference implementation written strictly from this spec, plus 27 mutants each flipping exactly one decision,
executed against the RED outside the worktree (the RED module is loaded by path with a scratch `conformance`
package ahead of it on `sys.path`; nothing in the repo is touched, and the reference is **deliberately not
committed** — GREEN must be written against the spec, not copied from a validation harness).

**Result: the reference passes 64/64 and every one of the 64 mutants fails at least one test** (v11 figures;
v5 recorded 40/40 over 27, before gate round 3 contributed the sentinel-seam candidate and gate round 4 the
fixed-offset one — neither of which my own enumeration contained).

**Scope of this evidence, and its limit** (gate round-3's weighing, adopted): mutation adequacy is measured
against **the author's own enumeration of decisions**, and the reference and this spec have one author, so a
shared misreading is invisible to both. That is `[G22:13]` restated. The proof is mutant **#28**: the gate
named a surviving candidate — an unanchored property line collected under a sentinel seam — that my 27-mutant
list did not contain, predicted it would pass 40/40, and it did. Execution raises the floor. It does not
replace an adversary, and this subsection is not offered as if it did.

**A limit of the same family, found in this lot's own harness** (bd#39 round-3 MINOR-C). The first
`row_last_dash` mutant was written as `rsplit("—")[0]`, which returns the same first segment as `split` — so it
expressed no candidate at all, passed 53/53, and reported nothing. **A mutant that cannot express its own
candidate is the same defect as an assertion that cannot fail**, one layer out: it produces a green line in the
matrix that looks like a kill. Corrected to `rsplit("—", 1)[0]` and re-run. The matrix is therefore only as
good as each mutant's faithfulness to the candidate it names, which is a second reason — beside the `[G22:13]`
asymmetry above — that execution is a floor.

**The differential precondition — ADOPTED** (bd#39 gate rounds 5 and 6, MINOR-C; asked twice, so its status is
recorded here rather than in a reply). "Mutant M failed N tests" is an **observation**; it becomes a **checked
claim** only once M is shown to diverge from the reference on at least one fixture. The harness now computes
both outputs over every fixture before running the suite, **exits non-zero if they are identical**, and
otherwise reports how many fixtures diverge — a faithful single-decision mutant diverges on few, a broken one
on many.

**The inference from it was wrong, and the correction matters more than the guard** (bd#39 gate round-7
MINOR-B). Being identical to the reference on every fixture has **two** causes and the guard cannot tell them
apart: the mutant failed to express its candidate, **or the candidate is real and no fixture reaches it** —
which is exactly what the gate exists to find. v7 recorded the first as the conclusion, so an author following
the procedure would have rewritten or discarded a genuine finding. **Both of round 7's MAJORs are of the second
kind**, and the guard reported both as "expresses no candidate" when they were run. Corrected: a zero-divergence
mutant is a **FORK**, not a verdict — adjudicate, either fix the mutant or file the fixture gap, and never
silently discard.

**It caught a defect on its first run, and the defect was mine.** `first_wins_property_value` was written so
that an empty first occurrence still admitted the later filled one; it was **identical to the reference on all
54 fixtures** and expressed no candidate at all. That is the third harness defect in four rounds — one mutant
expressing nothing (`rsplit("—")[0]`), one expressing far more than its candidate (an early `return`), and this
one — and the **first caught mechanically rather than by the number looking wrong**. Rewritten faithfully, it
now diverges on exactly one fixture and dies.

What the precondition still cannot catch is the fourth class: a **reference** silently choosing one of two
indistinguishable branches, which happened twice (`empty_admits_*`, `empty_property_*`). Nothing in the matrix
can see it, because both branches are consistent with the matrix. Only an adversary naming the input finds it.

**Disposal** (gate round-3 MINOR-D): the harness is uncommitted **only until GREEN lands**, so GREEN cannot be
copied from it. Once GREEN is in, the reference and the mutant set are committed beside the containment script,
so the matrix becomes re-runnable — by §5's own standard, an unrepeatable proof is a claim, and the exception
is temporary and for one stated reason.

The reference passing is a result in its own right and one no gate round could produce: it shows the fixture set
is **satisfiable** — no assertion contradicts another, and the spec as written is implementable **at the points
the fixtures ask about**. It is **not** evidence that the spec is free of internal contradiction (bd#39 gate
round 3, MAJOR-2): where two clauses disagree on an input no fixture carries, the reference's author resolves
it silently and the run cannot see it. That is the one claim this subsection was offered for that it does not
support, and it is withdrawn here rather than defended. Five revisions
of bd#22's spec shipped with an internal contradiction; this is the first mechanical evidence that this one has
none.

| Candidate | Clause it attacks | Tests failed |
|---|---|---|
| `needs_a_property_line` (B2) | `[G24:3]`/check 3 base case | 4 |
| `cardinality` (B1) | K9, C2-6 | 1 |
| `equality` | K12, C2-20 | 1 |
| `per_missing_reduction` | `[G24:3]` check 2, C2-22 | 1 |
| `per_missing_property` | `[G24:3]` check 3, C3-9 | 1 |
| `props_exactly_once` | `[G24:4]`, C3-18 | 1 |
| `dup_token_is_defect` | `[G24:4]` mirror, C2-23 | 1 |
| `admits_by_proximity` | K13, C2-18 | 1 |
| `skip_undeclared` | `[G24:6]`, C2-19 | 1 |
| `excludes_by_level` | `[G24:2]`, C2-10 | 5 |
| `forward_credit` | `[G24:5]`, C2-21/C3-17 | 1 |
| `single_anchor` | `[G24:10]`, C3-19 | 1 |
| `row_below_only` | `[G24:9]`, C1-14 | 1 |
| `ignore_bad_tokens` | `[G24:1]`, C2-8 | 1 |
| `default_drops_any` | K8, C2-3 | 1 |
| `case_fold_operand` | K11, C1-9 | 1 |
| `substring_a` / `substring_b` | K5 / K6, C1-5 / C1-6 | 1 / 1 |
| `global_presence` | K4, C1-4 | 6 |
| `first_only` / `last_only` | K2 / K3 | 7 / 11 |
| `seam_first_only` | K2 on check 3 | 9 |
| `seam_name_substring` | K5/K6 on check 3 | 2 |
| `attribute_path_only` | C3-3 | 11 |
| `verbatim_operand` | `[G24:7]`, C-CRLF | 1 |
| `strip_start` | `[G24:8]`, C-INDENT | 1 |
| `two_spellings` | C-CASE | 1 |

**Two things the simulation found that reading did not**, and both are recorded rather than quietly fixed:

1. **The assertion closing gate round-2 MAJOR-1 was not in the file.** The edit that added
   `assert len([f for f in findings if f.subject == "short_level"]) == 1` was written but never landed (the
   script that applied it aborted before writing), while the docstring describing it *did* land. So the round-3
   commit claimed a closure it had not made, and `per_missing_reduction` passed 40/40. Added and re-measured.
   This is the same defect class as C1-12 — a clause with prose but no assertion — arriving by a mechanical
   route rather than an analytical one, which is exactly why the simulation is executed and not reasoned.
2. **A `.split("\n")` implementation that `.strip()`s its operands is not a defect** — stripping removes the
   `\r` and the behaviour is correct. C-CRLF's real target is the implementation that keeps the operand
   verbatim after splitting on `"\n"` (`verbatim_operand`), which fails exactly one test: `_CRLF_SPEC`'s. The
   candidate is genuine and uniquely killed, but §3's C-CRLF row overstated it as "`.split(\"\\n\")` leaves
   `\r` inside `subject`"; the precise form is recorded here.

**Provenance, stated because §0.9 direction 2 is about additions nobody asked for.** `_ORPHAN_MARKER_LINES_SPEC`
comes from round 1's self-sweep, not from a gate finding. Of the three candidates it was admitted on, **two did not
survive gate round 1**: C1-12 and C3-16 are withdrawn above, and what remains is F-8 (a single-pass loop
dereferencing an anchor variable it never assigned) plus the manufactures-a-finding candidate in its third
assertion, which is real because the document contains no row at all. The fixture stays because those two are
genuine and no other fixture kills them; the forward-crediting half it was originally admitted on moved to
`_FORWARD_CREDIT_SPEC`.

## 6. Out of scope

Everything bd#22 shipped (`AC-C1`..`AC-C4`, `AC-P1`, `AC-P2`). Verifying that cited fixtures exist or
discriminate (§2.0). Wiring the lint into any build step or CI job — this lot ships the function and its tests,
nothing consumes it yet. Repeated `NON-UNIFORMITY` rows for one level (§2.5).

**Declared unpinned, rather than left silently unpinned** (gate round 1's advisory edge list; each would need a
decision no fixture currently forces, and inventing one per corner is how a spec acquires clauses nothing
measures — the defect `[G24:6]` was found to be):
- two `LEVEL:` lines declaring the **same name**. The v2 reason — that it had to be decided together with
  `[G24:3]`'s collapse — **no longer holds**: the cross-kind case is decided and fixtured
  (`_SAME_SUBJECT_TWO_KINDS_SPEC`). It stays unpinned on its own reason, which is that duplicate
  *declarations* are a different question from cross-kind *subjects*, and no fixture forces it (bd#39 round-3
  MINOR-B). `[G24:12]` gives it a second interaction the bullet must name: a level-level finding is now
  possible independently of rows, so two declarations of one name — one carrying an invalid `ADMITS` — make the
  **number** of findings depend on this undecided question (bd#39 round-4 advisory);
- an **empty or whitespace-only operand** (`LEVEL:`, `SEAM: `), which would make `Finding.subject` the empty
  string — **and, since `[G24:11]`, the second route to one**: a row whose em dash comes first
  (`NON-UNIFORMITY: — description`), whose operand is empty by that clause, **and a third**: a wholly bare `NON-UNIFORMITY:`, which `[G24:11]`'s "the whole operand when the row carries none" also makes empty (bd#39 rounds 3 and 5 advisories — same state, three routes, all named here);
- ~~an `EXCLUDES:` line with an **empty token list**~~ — **decided in v5 by `[G24:14]`** (no coverage, so the row is short) and removed from this list. It was declared undecided here while `[G24:11]` determined it, which is two frozen sentences about one input (bd#39 round-4 MAJOR-2);
- **multiplicity of anchored lines under one anchor**: two `ADMITS` lines under one `LEVEL`, or two `EXCLUDES`
  lines under one row — union, last-wins, or a finding? Decided for property lines (`[G24:4]`: repetition collapsed, **and since `[G24:15]` also the write policy — a
  property is pinned if any of its lines is filled**; the earlier "a set" description was accurate while the
  store was a set of markers and is now incomplete) and declared here for the other two. Noted rather than pinned because no fixture forces the
  decision, and inventing a clause per corner is what `[G24:6]` was found to be. Recorded because
  `_ROW_MARKER_CASE_SPEC`'s analysis leans on a union reading for its *wrong-lint* branch — the indented
  `Excludes:` completing a row — so the reading is load-bearing somewhere while unpinned (gate round-8
  advisory);
- an `EXCLUDES:` line appearing after a `SEAM:` but before **any** row in the document — `[G24:2]` and
  `[G24:5]` between them already answer it (nearest preceding **row**; none, so it binds to nothing), and
  `_INTERLEAVED_ANCHORS_SPEC` now walks the interleaving case where a row does exist, but the no-row-at-all
  variant has no fixture of its own.

A later lot that consumes the lint over real documents is where these acquire a forcing case.

## 7. Process

Manual Option-D: frozen spec → RED → gate (`hal-gate-agent`, Opus) → GREEN. **GREEN does not start before an
ACCEPTED verdict.** Measured per-test counts recorded every round; inherited numbers re-measured, never trusted.
The PR is not merged by this lot — reported to the dispatcher.
