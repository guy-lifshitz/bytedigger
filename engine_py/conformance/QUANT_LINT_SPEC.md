# Lot spec — bd#24 (L2b): the quantifier-completeness lint (`AC-C5`)

**v2, FROZEN.** Lot **L2b** of the 12-lot split of bd#7, split out of bd#22 under the dispatcher's round-4 exit
criterion, clause 3. Base: `origin/main` @ `08b8413` (this worktree, branch `lot-24`). Carries **exactly 1 AC**:
`AC-C5`. AC accounting stays convergent: 7 = 6 (bd#22, shipped in `dc6f0d0`) + 1 (here).

Depends on bd#22's shipped `conformance` package. It MUST NOT reference L1's emissions or any later lot's design.

**v2 revision, and why the freeze was reopened before the gate rather than after it.** Writing the RED against
v1 and then running §0.9's own self-sweep over it surfaced a corner v1 had not pinned: the **base case of both
binding rules** — a property line or an `EXCLUDES` line with **no preceding anchor at all**. Every fixture in the
file, carried and new, exercises P3 and `[G24:2]` only where an anchor exists. That is structurally the same miss
as round-4's B2 (three fixtures each missing *one* property, none with *zero*), one rule over. bd#22's `[G22:16]`
established the correct response: the RED **flags** an unpinned semantic and it is decided **in the spec**, rather
than invented in a test and discovered by a later round. So it is pinned in §2.5 and given a fixture, and the
spec is re-frozen at v2 before the gate. Nothing else changed: no carried expectation, no `[G24:1]`/`[G24:2]`
wording, no measurement.

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
| `NON-UNIFORMITY: <level> — <description>` | the row discharging that level |
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
- **`[G24:4]` Repeated property lines do not add coverage.** Check 3 is over the **set** of property markers
  present under a seam, not their count: `ATTRIBUTE-PATH` + `BINDING-TIME` + `BINDING-TIME` is a seam missing
  `NORMALISATION`, not a seam with three properties. A repeated line is not itself a finding.
- **`[G24:5]` A marker line with NO anchor preceding it binds to nothing.** The base case of P3 and `[G24:2]`,
  and the v2 addition. A property line before any `SEAM`, and an `EXCLUDES` line before any `NON-UNIFORMITY`
  row, each bind to nothing: they contribute no coverage to anything, they are **not** themselves findings
  (there is no `kind` for an unanchored line and inventing one would exceed the three pinned values), and they
  MUST NOT be credited **forward** to the next anchor. A `SEAM` that follows an orphaned `NORMALISATION:` line
  is still missing `NORMALISATION`. Exercised by `_ORPHAN_MARKER_LINES_SPEC` (§3, C1-12 / C3-16 / F-8).
- **`[G24:6]` A `NON-UNIFORMITY` row whose `<level>` operand names no declared `LEVEL` is still checked**,
  against the **default** admitted set. Reached by the carried `_LEVEL_CASE_MISMATCH_SPEC`, whose row operand
  `audit_case` matches no level once P1 makes the comparison case-sensitive — so v1 left a clause that a carried
  fixture actually walks through. The alternative (skip such rows entirely) would let a typo'd operand hide its
  own row's under-enumeration, which is the defect this AC exists for. The fixture's row covers all four, so
  this pin adds no finding there and contradicts no carried expectation.
- **Finding order** is deterministic for a given input (the idempotence test compares two calls' lists) and
  otherwise unspecified across inputs; no test asserts a position.
- **Out of scope, explicitly:** the behaviour when two `NON-UNIFORMITY` rows name the **same** level. No fixture
  exercises it and this lot does not pin it, rather than pinning a clause nothing measures.

### 2.6 The three checks

| Check | `kind` | Fires when |
|---|---|---|
| 1 | `missing_non_uniformity_row` | a `LEVEL` has no `NON-UNIFORMITY` row naming it (P4, verbatim) |
| 2 | `missing_reductions` | a `NON-UNIFORMITY` row has no `EXCLUDES` line, **or** its `EXCLUDES` does not cover every reduction its level `ADMITS`, **or** either line carries a token outside the four (`[G24:1]`) |
| 3 | `seam_not_pinned` | a `SEAM` is missing any of `ATTRIBUTE-PATH`, `BINDING-TIME`, `NORMALISATION` — **including a `SEAM` with zero property lines** |

Checks 1 and 2 are **independent**: a level with no row yields check 1 only (there is no row to check for
reductions); a level whose row is short yields check 2 only.

---

## 3. Candidate simulation — the deliverable (`[G22:13]`)

Notation: **✝** marks a fixture new in this lot; everything else is carried **verbatim** from bd#22's round-9
artifact (`d39371f`, `engine_py/tests/test_bd22_contracts.py`) — see §5.

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
| `_ORPHAN_MARKER_LINES_SPEC` ✝ | 1 + 3 | a `NORMALISATION:` and an `EXCLUDES:` line that precede **every** anchor in the document |
| `_CONFORMANT_SPEC` | control | every level covered, seam fully pinned; `EXCLUDES` tokens and property lines in **non-canonical order** |
| `_MALFORMED_SPEC` | contract | free-form prose, no markers |
| `_LOWERCASE_MARKERS_SPEC` | grammar | every marker spelled lowercase |
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
| C1-11 | K7/lookahead crash on a trailing bare `LEVEL:` | `"LEVEL: phases"` inline | an `i+1` lookahead raises `IndexError` instead of reporting |
| C1-12 | K7, base case: an **unanchored** `EXCLUDES` credited **forward** to the next `LEVEL` ✝ | `_ORPHAN_MARKER_LINES_SPEC` ✝ | the orphaned `EXCLUDES` precedes `LEVEL: orphan_level`; a forward-crediting lint treats the level as having a discharged row and stops reporting it row-less (`[G24:5]`) |

### 3.3 Check 2 — `missing_reductions`

| ID | Candidate | Killed by | How it dies |
|---|---|---|---|
| C2-1 | K1 presence-only ("row exists ⇒ fine"), i.e. only half of check 2 implemented | `_CHECK2_SPEC` (`phases` has no `EXCLUDES` at all) + all present-but-short rows | the short rows are invisible to it |
| C2-2 | coverage checked **only when an explicit `ADMITS:` is present** | `_CHECK2_SPEC` | no row in that fixture carries `ADMITS`; all five must be flagged |
| C2-3 | K8 default set missing `all` / `any` / `first` / `last` | `_CHECK2_SPEC` | four rows each omit exactly one distinct member; whichever member the lint dropped, that row goes unflagged |
| C2-4 | explicit `ADMITS` override ignored (all four always demanded) | `_CHECK2_EXPLICIT_ADMITS_SPEC` | `payload_field` `ADMITS: any, all` and covers both; a lint demanding four false-flags it |
| C2-5 | K2 first-only / K3 last-only | `_CHECK2_SPEC` (5 offenders), `_CHECK2_EXPLICIT_ADMITS_SPEC`, `_CHECK2_CARDINALITY_SPEC` ✝, `_REDUCTION_TOKEN_VOCABULARY_SPEC` ✝ | in each of the last three the offenders **bracket** a conformant row |
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
| C3-15 | K10 fixed required order for property lines | `_CONFORMANT_SPEC` | the control's seam lists `BINDING-TIME`, `NORMALISATION`, `ATTRIBUTE-PATH` in that order |
| C3-16 | K7, base case: an **unanchored** property line credited **forward** to the next `SEAM` ✝ | `_ORPHAN_MARKER_LINES_SPEC` ✝ | the orphaned `NORMALISATION:` precedes `SEAM: Orphan.Seam`; a forward-crediting lint counts it as that seam's `NORMALISATION` and clears a seam with zero properties of its own (`[G24:5]`). The carried `_SEAM_PROPERTY_WRONG_NEIGHBOUR_SPEC` covers only the case where the line has a *preceding* seam to be taken from |

### 3.5 Whole-function contract

| ID | Candidate | Killed by |
|---|---|---|
| F-1 | over-fires on well-formed input (bd#7's keyword sweep, 11 of 13 mis-scored) | `_CONFORMANT_SPEC` (`findings == []`) |
| F-2 | raises on an unstructured document | `_MALFORMED_SPEC` |
| F-3 | raises on the empty string (`i+1` lookahead over `split("\n")`) | `""` inline |
| F-4 | raises on a trailing bare marker (`.splitlines()` lookahead) | `"LEVEL: phases"`, `"SEAM: tempfile.mkdtemp"` ✝ inline |
| F-5 | markers matched case-**sensitively** | `_LOWERCASE_MARKERS_SPEC` |
| F-6 | accumulates findings in module-level state across calls | the idempotence test: snapshot-before-second-call, plus `second is not first` |
| F-8 | dereferences a "current anchor" that was never set (raises on an unanchored marker line) ✝ | `_ORPHAN_MARKER_LINES_SPEC` ✝ |
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

— extract each `_*_SPEC` constant and each `def test_ac_c5_*` name and confirm containment in
`engine_py/tests/test_bd24_quant_lint.py`. The check is run and its result recorded in the RED report.

Consequently every candidate the round-9 set killed is still killed by the same fixture, unchanged: C1-1,
C1-2/3 (partially), C1-4, C1-5, C1-7, C1-8, C1-9, C1-10, C1-11; C2-1..C2-5, C2-12..C2-17; C3-2..C3-4,
C3-5/6 (partially), C3-7, C3-10, C3-12, C3-13, C3-14, C3-15; F-1..F-7. **The one thing round 3 deleted
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

No carried expectation changes. Zero fixture edits were required to adopt either decision — which is the
evidence that they are decisions about an under-specified corner, not re-specifications of a settled one.

The v2 pins were replayed the same way. `[G24:5]` (unanchored lines) touches no carried fixture: every carried
`EXCLUDES` has a preceding row and every carried property line a preceding seam — which is precisely why the
corner was invisible. `[G24:6]` (a row naming no declared level) is walked by exactly one carried fixture,
`_LEVEL_CASE_MISMATCH_SPEC`, whose row covers all four reductions, so the pin produces no finding there and its
single assertion is unaffected.

**Provenance, stated because §0.9 direction 2 is about additions nobody asked for.** `_ORPHAN_MARKER_LINES_SPEC`
comes from this round's self-sweep, not from a gate finding. It is admitted under the same test as every other
addition: it names candidates (C1-12, C3-16, F-8) that no other fixture in the set kills, and each is a
plausible implementation — forward-crediting and unset-anchor dereferencing are the two things a single-pass
line loop does most naturally when it meets a marker before its anchor.

## 6. Out of scope

Everything bd#22 shipped (`AC-C1`..`AC-C4`, `AC-P1`, `AC-P2`). Verifying that cited fixtures exist or
discriminate (§2.0). Wiring the lint into any build step or CI job — this lot ships the function and its tests,
nothing consumes it yet. Repeated `NON-UNIFORMITY` rows for one level (§2.5).

## 7. Process

Manual Option-D: frozen spec → RED → gate (`hal-gate-agent`, Opus) → GREEN. **GREEN does not start before an
ACCEPTED verdict.** Measured per-test counts recorded every round; inherited numbers re-measured, never trusted.
The PR is not merged by this lot — reported to the dispatcher.
