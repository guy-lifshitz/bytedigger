# Lot spec — bd#22 (L2): conformance package, shared contracts, quantifier-completeness lint

**v8.** Lot **L2** of the 12-lot split of bd#7. Base: `origin/main` @ `a2691f9`. Exactly **6 ACs** after the round-4 split. `AC-C5` (the quantifier-completeness lint) moved to **bd#24**;
accounting stays convergent at 7 = 6 here + 1 there.

Depends on nothing but `main`. Everything after L2 depends on it. It MUST NOT reference L1's emissions or any
later lot's design.

## 0. What this lot inherits, and the two rules L1 taught that are new here

bd#7 was rejected **nine** times at 113 ACs and never passed. L1 (bd#18) was **accepted on its second round** at
24 ACs. That contrast is the reason this lot exists and the reason it is small.

**§0.1 `[G6:quant]`, in its sharpened form.** Any requirement ranging over a collection **where the reduction is
an implementation choice** MUST be asserted with a fixture set that **excludes every reduction the implementation
could have chosen** — not merely "≥2 members, one violating, plus a positive control", which is what bd#7 and L1
both said while a defect survived. L1's `[G18:1]` proved the weaker form insufficient: a fixture non-uniform in
**one ordering only** excluded `any` and `first` but left `last` alive, and the wrong implementation passed all 38
tests. For an ordered collection this means **both orderings**.

Collection levels run in **both directions** — bd#7's round 9 found a rung *below* the ladder its earlier rounds
had been climbing (the two step-event *kinds* asserted only as a merged set), after four rounds of applying the
rule monotonically upward. Containers **and** element kinds **and** payload fields.

**§0.2 Naming a seam is not enough — pin the property the interception depends on.** This bit three times in a
row, each time with the correct seam named:
- `mkdtemp` — mechanism pinned, **binding time** not (bd#7 `[G6:MINOR-4]`);
- `Path.read_text` — mechanism pinned, **path normalisation** not (L1 `[G18:MINOR-4]`); `Path.__eq__` compares
  strings, so a correct implementation spelling the path through `.resolve()` was false-failed;
- `importlib.metadata.version` — attribute path pinned, **binding time** not (L1 `[G18r2:MINOR-5]`); a
  module-level `from importlib.metadata import version` binds at import and no `setattr` patch can reach it.
Each would have false-failed a correct implementation. A seam pin MUST state: the attribute path, **and** whether
resolution happens at call time, **and** any normalisation the comparison depends on.

**§0.3 No exact byte-boundary arithmetic in this lot.** All five of bd#7's Class B defects lived in byte fences.
L2 has no serialisation surface, so there is nothing to compute; if one appears, it belongs to L6.

**§0.4 No assertion that cannot fail.** Forbidden shapes, all of which bd#7 or L1 actually shipped: tautologies
(`x[x.index(y)] == y`; `a is not b` on two freshly-constructed objects); assertions comparing the test's **own
fixture** to itself rather than to the produced artifact; conditionals that silently no-op; **and assertions that
pass pre-implementation** — L1's AC-E6 would have passed on `pytest.raises` alone and needed an
observed-attempt assertion to force RED, and its AC-E7's negative assertions needed a positive control to stop
being trivially true. Every test states, in its docstring, the wrong implementation it kills.

**§0.5 No truthiness where a value is specified**, per **field**, not per AC — L1 pinned `adapter_identity.backend`
by value and left its sibling `source` at non-emptiness one clause later.

**§0.6 Pre-passing tests MUST be declared** as shields in this spec. An undeclared pre-passing test is a finding:
a reader cannot distinguish a shield from a test that never measured anything.

**§0.7 Measurements and line citations are both base-relative and do not transfer.** bd#7 inherited three
measurements from another host and two did not reproduce. L1 carried two `event_log.py` citations from bd#7's
older base and **both were stale**, pointing at unrelated code. Every citation in this spec is resolved against
`a2691f9`; every number is measured on the executing host.

## 0.8 The failure mode this lot has now produced six times `[G22:8]`

Every defect in this lot has been **a requirement discharged exactly as worded, with the defect one level below**.
Not carelessness — each fix satisfied its clause literally:

| Stated | Discharged as | Defect landed at |
|---|---|---|
| `[G22:1]` "the RED asserts against pinned names" | grammar pinned | `Finding`'s attributes, unpinned (`[G22:4]`) |
| `[G22:3]` "force on something that doesn't exist" | `import conformance` | it *did* exist, as a namespace package |
| `[G22:5]` "a level that `ADMITS` four, a row that `EXCLUDES` three" | explicit `ADMITS` line | the `ADMITS`-**absent default** path, never exercised (`[G22:9]`) |
| `[G22:6]` "record all five prohibited acts" | 14 seams, five acts | `os.scandir`/`os.walk` **inside** the scan act (`[G22:10]`) |
| `[G22:7]` "non-uniform within each check" | ≥2 members per check | all three fixtures in **one ordering** (`[G22:11]`) |

**So prose requirements are not enough, and this is the lot whose whole purpose is to mechanise that insight.**
Three rules, stated so they can be checked rather than interpreted:

1. **Both orderings, always — at the level of the fixture SET, per check.** For each check, the fixture set MUST
   place an offending member **first** in at least one fixture and **last** in at least one fixture. A single
   fixture need not appear twice. `[G22:12]` This is the corrected wording: v5 stated rule 1 as an absolute
   property of *every* fixture while `[G22:11]` required only one-of-three, so the flagship checkable rule read as
   violated by three of its own fixtures — the fifth consecutive revision of this spec carrying an internal
   contradiction. The set-level reading is the one that was implemented and the one that is adequate.
2. **Every optional element's DEFAULT path is a separate fixture.** If a grammar element is optional with a
   default, a fixture exercising the element **present** does not exercise the default. Both, separately.
3. **Enumerations are exhausted at the API level, not the concept level.** "Cover the five acts" is discharged by
   covering every *callable* that performs each act, not one per act. Where an enumeration is the requirement, the
   spec MUST list the members, because a concept boundary is not checkable and an API list is.

4. **`[G22:13]` THE TERMINATING RULE — enumerate candidate implementations and simulate each; do not derive
   fixtures from the requirement text.** Rules 1-3 were each derived from a defect and each was satisfied while the
   defect moved one level down. Eight instances now. The recursion has no bottom **as long as fixtures are written
   from the requirement**, because a requirement describes what must hold and a fixture must exclude what an
   implementer might plausibly do instead — and those are different enumerations.
   What the gate does, and the RED must do before submitting: **for each check, list the plausible implementations
   and show each one dies against the fixture set.** For a lint over a marker grammar the standing list is:
   presence-only; first-member-only; last-member-only; document-global presence (ignoring the member operand);
   substring rather than exact operand match; the operand bound to the wrong neighbour (following instead of
   preceding); each *individual member* of a defaulted set omitted; a fixed required order for unordered
   properties; and case-folding where the spec pins verbatim text. Round 3's two blocking findings were
   `document-global presence` and `one specific member of the default set` — both on that list, neither simulated.
   **A round is not ready until that simulation is written down per check.** This rule supersedes 1-3 as the
   primary obligation; they remain as the specific lessons that produced it.

## 1. Measured baseline

`origin/main` @ `a2691f9`, executing host, isolated worktree: **4134 passed, 6 skipped, 0 failed** (279 s).
Contention control per hal#1353 taken before and after (no `Runner.Worker`, no competing suite; matched on
command **prefix**, not substring).

Ship in **0 failed**. Drift invariant is the **property** "identical to this host's own `main` at
`extra_bd == 0`", never a literal count.

## 1.5 The public API surface, pinned here and not in the RED `[G22:1]`

**Why this section exists.** Round 1's RED had to invent the module paths, the exported names and a fixture-document
grammar, because v1 named none of them — so the *test file* became the de facto interface and GREEN would have had
to reverse-engineer it. That inverts bd#7's `§3.0` principle: **the interface is carried by the spec, not the
RED.** bd#7 filed exactly this against itself (`AC-A7b`, where `adversaries[]`'s form lived in the RED and had to
be moved into the spec). The RED agent flagged it rather than proceeding silently, which is why it is fixed here
before GREEN rather than discovered during it.

Normative — GREEN provides exactly these, and the RED asserts against these names:

| Module | Exports |
|---|---|
| `conformance/__init__.py` | nothing re-exported; importing it MUST do no work (AC-C1) |
| `conformance/report.py` | `L0Report` — frozen dataclass, fields `passed`, `requirements`, `violations`, `labels` |
| `conformance/tokens.py` | `REQUIREMENT_PASSED = "passed"`, `REQUIREMENT_FAILED = "failed"`, `REQUIREMENT_NOT_CHECKED = "not-checked"`, `ADVERSARY_NOT_EXECUTED = "not_executed"` |
| `conformance/quant_lint.py` | `lint_quantifier_completeness(text: str) -> list[Finding]` |

`lint_quantifier_completeness` takes the spec document's **text** and returns a list of findings — an empty list
means conformant. It MUST NOT raise on a non-conformant **or** malformed document: returning findings is the
contract, and raising would make "conformant" and "malformed" indistinguishable to the build step that consumes it.

`[G22:4]` **`Finding` is pinned here too, for the same reason the grammar was.** v2 said a finding "identifies its
kind and the subject" and named neither the attributes nor the values — so the RED had to choose them, and the RED
agent flagged that rather than proceeding silently, which is the second time it has caught this class. `Finding`
lives in `conformance/quant_lint.py` beside the function, and is a frozen dataclass with exactly:

| Attribute | Type | Values |
|---|---|---|
| `kind` | `str` | one of `"missing_non_uniformity_row"`, `"missing_reductions"`, `"seam_not_pinned"` |
| `subject` | `str` | the offending level name, row level, or seam name — verbatim from the document |

The three `kind` values correspond 1:1 to AC-C5's three checks, so a caller can tell them apart by value rather
than by parsing a message, and a lint implementing only one check cannot masquerade as implementing three.
`subject` is quoted from the document so a failure names the actual level or seam, not an index.

**The pattern, now three for three:** every time this spec described a shape instead of pinning it — the fixture
grammar (`[G22:1]`), the forcing mechanism's assumption (`[G22:3]`), and now `Finding` — the RED had to invent it
and the invention became the de facto interface. Describing a value is not specifying it.

### 1.6 The AC-C5 fixture-document grammar, pinned `[G22:1]`

The lint reads spec-like documents. Round 1's RED invented a line-marker grammar; it is adopted here, minimally,
so that spec and RED agree and GREEN has something to parse. A fixture document is plain text; these markers are
recognised at line start, case-insensitive, and everything else is prose the lint ignores:

- `LEVEL: <name>` — declares a collection level the document quantifies over.
- `NON-UNIFORMITY: <level> — <description>` — the row discharging that level.
- `EXCLUDES: <reduction>[, <reduction>...]` — which reductions that row's fixtures kill (`any`, `all`, `first`, `last`).
- `SEAM: <name>` — declares an interception seam.
- `ATTRIBUTE-PATH:`, `BINDING-TIME:`, `NORMALISATION:` — the properties a seam declaration must pin.

`[G22:16]` **Two binding/casing semantics the round-6 simulation found unpinned, pinned here rather than guessed.**
The RED agent reached both by simulating candidate implementations and **flagged them as gaps instead of inventing
a semantic to test against** — which is the behaviour `[G22:13]` exists to produce, and the reason these are being
decided in the spec instead of discovered in a later round:
- **Marker prefixes are case-insensitive; OPERANDS are verbatim.** `§1.6` states the markers are recognised
  case-insensitively and says nothing about the text after the colon. Normative: a `LEVEL` name, a row's
  `<level>` operand, and a `SEAM` name are matched **case-sensitively and exactly** — `LEVEL: Audit` is not
  discharged by `NON-UNIFORMITY: audit — …`. This follows from `Finding.subject` being pinned "verbatim from the
  document" (`§1.5`): a lint that case-folds to match cannot also report verbatim.
- **Property lines bind to the NEAREST PRECEDING `SEAM`**, exactly as `ADMITS` binds to the nearest preceding
  `LEVEL`. `§1.6` pinned the latter and left the former to proximity-by-implication, so a lint binding a property
  line to the *following* seam was untestable without inventing the rule. Same rule, stated once for both.

- `ADMITS: <reduction>[, <reduction>...]` — **optional**, on a `LEVEL`; declares which reductions that level's
  fixtures could plausibly choose between. **Absent means all four.**

The three checks are then exactly: a `LEVEL` with no `NON-UNIFORMITY` row (check 1); a `NON-UNIFORMITY` row with
no `EXCLUDES` line, **or one that does not cover every reduction its level `ADMITS`** (check 2); a `SEAM` missing
any of the three property lines (check 3).

`[G22:5]` **Why `ADMITS` exists — check 2 was unimplementable as v1..v3 wrote it.** The clause said a row naming
"fewer than the reductions its level admits" was a finding, and then never said how a lint computes what a level
*admits*. The only computable reading was "all four", and the round-1 conformant control declared a level whose
row named three (`any, first, last`, omitting `all`) while requiring zero findings. Both horns were live: an
implementation reading the clause the only way it could be read **false-failed the control while being exactly
spec-compliant**, and an implementation of only the first half (row has no `EXCLUDES` line at all) **passed all
five AC-C5 tests**, because no fixture anywhere carried an `EXCLUDES` line that was *present but short* — so
`[G18:1]`'s under-enumeration defect, the precise reason this AC exists, was never forced.

Declaring the admitted set makes the check computable without pretending every collection admits all four:
`first` and `last` are meaningless for an unordered collection, so demanding them universally would have been
wrong in the other direction. **Required of the RED `[G22:9]`, corrected:** the present-but-short row's level MUST carry **no** `ADMITS` line,
so the all-four **default** is what makes it a finding. Round 2 gave that row an *explicit* `ADMITS: any, all, first,
last` — which satisfied the wording and is the one variant that never exercises the default, so a lint applying the
coverage check **only when an explicit `ADMITS:` is present** passed all 17 tests. A real spec writing `LEVEL: x` /
`NON-UNIFORMITY: x — …` / `EXCLUDES: any` with no `ADMITS` would have shipped green: exactly the under-enumeration
this AC exists to catch. Keep a separate explicit-`ADMITS` short row as well, per `[G22:8]`'s rule 2 — present and
default are two fixtures, not one. `ADMITS` binds to the **nearest preceding `LEVEL`**; a token outside the four is
itself a finding of kind `missing_reductions`.
  `[G22:14]` **And the MEMBERSHIP of the default set must be load-bearing, member by member.** Round 3 exercised
  the default path with exactly one reduction missing (`all`), and `any` was never the missing reduction in any
  fixture — so a GREEN whose default set is `{all, first, last}`, dropping `any`, passed all 19 tests, and a real
  spec writing `EXCLUDES: all, first, last` with no `ADMITS` would be reported conformant while under-enumerating
  `any`. `[G22:9]` made *whether* the default is exercised load-bearing; *which member is short* sat one level
  below. Required: default-path rows omitting **`any`** and omitting **`first`**, in addition to the existing
  `all` case.

This is the fourth time in this lot that describing a value stood in for pinning one (`[G22:1]` grammar,
`[G22:3]` forcing assumption, `[G22:4]` `Finding`, now this). The tell is the same each time: a clause that reads
as a rule but leaves the reader to supply the operative quantity.

This grammar is **deliberately not** the format of this lot's own spec documents. It is the lint's input format,
and the lint is a tool, not a validator of these files. Conflating the two would make every prose sentence here a
potential lint failure.

## 2. Package and contracts (AC-C1..AC-C4)

- **AC-C1** `engine_py/conformance/` is an importable package with **no import-time side effects**: importing it
  MUST do **no work at all** — that is the normative form, and the list that follows is illustrative rather than
  exhaustive (`[G22:8]` rule 3): it must not read a file, touch the network, resolve a version, spawn a process,
  scan a directory, or create one. Round 2 noted the enumeration said "five acts" while the recorded set covered
  six kinds, directory *scan* not being among the five; "no work" is what the RED actually asserts and what GREEN
  must satisfy.
  `[G22:2]` **The seams MUST be RECORD-AND-DELEGATE, never raise.** This is normative on the *assertion mechanism*,
  because round 1 got it wrong in a way that made the entire file inert. It replaced `builtins.open` with a raiser —
  and `open` is a primitive **pytest itself runs on**: assertion rewriting, traceback source reading, capture, and
  the cache provider all call it. So the first failure broke pytest's own teardown, `monkeypatch`'s undo never
  completed, the patch **leaked permanently**, every later test errored in setup, and pytest died in
  `pytest_sessionfinish` (`_pytest/cacheprovider.py:221` reaching the raiser). Measured: **1 passed, 19 errors,
  session crash** — not the nine clean failures intended, and in the full suite it would have poisoned every test
  that ran after it.
  Required form: wrap each seam so it **records the call and then delegates to the real implementation**, returning
  the real result; purge `conformance*` from `sys.modules` so the import genuinely re-executes; import under the
  recorders; then assert the recording holds no call attributable to the import. Nothing raises, so nothing can
  break teardown even when the assertion fails — and the diagnostic improves, since a recorder can name *which*
  seam was touched and with what argument where a raiser only reports that something was.
  **The general rule this instance illustrates, and §0.3's sharper form: a test may not sabotage a primitive its own
  harness runs on. Observation is safe; substitution is not.**
  `[G22:6]` **The recorded seam set MUST cover all five prohibited acts, and `§0.1` applies to that collection.**
  Rounds 1-4 recorded **three** seams (`builtins.open`, `Path.read_text`, `subprocess.run`) against the five acts
  this AC prohibits, so an implementation choosing any of the other two shipped green — and the test's own
  docstring claimed to kill "scanning a directory to build a registry", which it could not see. The uncovered
  choices, each a plausible eager implementation for a package like this:
  - **directory scan** — `os.listdir`, `Path.iterdir`, `Path.glob`. `FIXTURES = list(Path(__file__).parent.glob("*.md"))`
    in `__init__.py` records nothing. This is the *most* plausible one, which is why its absence mattered most.
  - **directory creation** — `os.mkdir`, `os.makedirs`, `Path.mkdir`, `tempfile.mkdtemp`. Spec-enumerated, zero coverage.
  - **network** — `socket.socket`, `urllib.request.urlopen`. Spec-enumerated, zero coverage.
  - **a file read off the `builtins` path** — `Path.open` routes through `io.open`, and patching the `builtins`
    attribute does not rebind `io.open`, so `Path(x).open().read()` escaped both file recorders. `Path.read_text`
    was caught only because it was patched on the class directly.
  "Resolve a version" was covered only *incidentally* — `importlib.metadata`'s `PathDistribution.read_text` happens
  to route through `Path.read_text`, and only on a cold metadata cache. Incidental coverage is not coverage:
  `importlib.metadata.version` MUST be recorded on the module attribute, at call time, per `[G18r2:MINOR-5]`.
  This is `§0.1` over the collection "prohibited side-effect kinds": the recorded set must exclude **every** choice
  the implementation could have made, and three of five excludes none of the other two.
  `[G22:10]` **Corrected: the enumeration is exhausted at the API level, not the act level** (`[G22:8]` rule 3).
  Round 2 recorded 14 seams covering five acts and still left the *most plausible* act open: `os.scandir` was
  uncovered, and therefore so was **`os.walk`**, which resolves `scandir` as an `os` module global and so reaches
  the real one without touching `os.listdir`, `Path.iterdir` or `Path.glob`. A one-line
  `os.walk`-based registry build in `__init__.py` recorded **nothing**. Same shape one act over: "spawn a process"
  was covered only by `subprocess.run`, while `subprocess.Popen` (which `run` *wraps*, so the coverage does not flow
  backwards), `os.system` and `os.popen` all escaped. And note this AC's own rule forbids the reply that `Path.glob`
  internally calls `os.scandir`: **incidental coverage is not coverage.**
  The recorded set is therefore pinned as this list, and a later round adds to it rather than reasoning about acts:
  `builtins.open`, `io.open`, `os.open`, `Path.open`, `Path.read_text`, `Path.write_text`,
  `os.listdir`, `os.scandir`, `os.walk`, `Path.iterdir`, `Path.glob`, `Path.rglob`,
  `os.mkdir`, `os.makedirs`, `Path.mkdir`, `tempfile.mkdtemp`, `tempfile.NamedTemporaryFile`,
  `subprocess.run`, `subprocess.Popen`, `subprocess.check_output`, `os.system`, `os.popen`,
  `socket.socket`, `socket.create_connection`, `urllib.request.urlopen`,
  `importlib.metadata.version`, `importlib.metadata.distribution`.
  Two mechanical notes for the RED: `socket.socket` is a **class**, so a function wrapper breaks `isinstance`/
  subclassing for the duration of the window — acceptable only because the window contains nothing but imports, and
  it MUST carry an inline comment saying so. And the recorder MUST filter by `threading.get_ident()`, because `calls`
  is process-global and a concurrent thread touching any of these would be attributed to the import.
  `[G22:3]` **`conformance` MUST be a REAL package (`__init__.py` present), and the RED must force on that.**
  Measured after the `[G22:2]` fix: AC-C1 and AC-C4 **passed** where both were expected to fail. Cause — and it is
  mine: this spec document lives at `engine_py/conformance/CONTRACTS_SPEC.md`, and a directory with no
  `__init__.py` is a **namespace package**, so `import conformance` **already succeeds** today, resolving to an
  empty namespace that reads no file and touches no seam. Both tests were therefore vacuous in the precise sense
  §0.4 forbids — they passed pre-implementation and would pass identically after it, measuring nothing.
  The forcing mechanism must be the **real submodules**, which genuinely do not exist: import
  `conformance.report`, `conformance.tokens` and `conformance.quant_lint` under the recorders. AC-C1 MUST also
  assert the package is real rather than a namespace — `conformance.__file__ is not None` — because §1.5 pins
  `conformance/__init__.py`, and a namespace package is not the artifact this lot ships.
  **Worth stating as a lesson, not just a fix:** the RED's forcing mechanism rested on an assumption about the
  filesystem (`import conformance` fails today) that my own choice of where to put a *document* silently
  falsified. A forcing mechanism is a claim about the world and needs measuring like any other.
- **AC-C2** `L0Report` is a **frozen** dataclass carrying `passed`, `requirements`, `violations` and `labels`.
  Attribute assignment MUST raise `FrozenInstanceError`; the type MUST be constructible with those four fields.
  **Field *values* are out of scope here** — their semantics belong to L7-L12. L2 owns the carrier only, and
  saying so is what keeps L2 from quietly becoming the checker lot.
- **AC-C3** The token vocabulary is a **single source of truth**: the requirement verdicts `"passed"`,
  `"failed"`, `"not-checked"` and the adversary status `"not_executed"` are defined once as named constants.
  Asserted **by value** against those exact strings (§0.5), so a re-spelling fails — and asserted to be
  **distinct**, because bd#7's `[G2:9]` deliberately separates the hyphenated verdicts from the underscored
  adversary status, and a lot that unified them would silently break every consumer.
- **AC-C4** No new third-party dependency enters the engine import path. Asserted against the **declared**
  dependency set in `pyproject.toml`, not by inspecting imports — an import scan cannot distinguish a stdlib
  module from a vendored one.

## 3. AC-C5 — MOVED to bd#24 `[G22:17]`

`AC-C5` left this lot under the dispatcher's round-4 exit criterion. Gate round 4 returned REJECTED with a
**type (a)** blocker — one introduced by our own previous fix, proven by diff: round 1's `_SEAM_NOT_PINNED_SPEC`
carried a bare `SEAM:` with zero property lines, and commit `9947142` deleted it while implementing the
`[G22:7]` fix. Clause 3 of the criterion therefore forbids a fifth spec round on that shape and requires the lot
to narrow or split.

**Why the seam falls here.** All eight blocking findings across four gate rounds were in `AC-C5`'s fixtures. The
six ACs that remain were found clean round after round and never produced a blocking finding, so keeping them
behind the lint made L3-L12 wait on the hardest artifact in the split for no reason.

Everything accumulated for the lint — the `[G22:13]` candidate list, the `[G22:16]` semantics, both round-4
findings as input requirements, and `_SEAM_NOT_PINNED_SPEC` as a form not to lose twice — is recorded in bd#24.

**One method change goes with it, and it is the substantive lesson of round 4:** candidate simulation catches
"the implementation picked the wrong reduction"; it does **not** catch "a fixture that covered the base case
disappeared". Round 3 deleted check 3's base-case coverage while satisfying its requirement to the letter, and
neither the requirement nor the simulation table noticed, because both looked at the shape the requirement named.
bd#24 therefore gates on a **coverage diff per fix**: every round that modifies or removes a fixture must show
which candidates the previous set killed and confirm each is still killed.

## 4. Packaging (AC-P1, AC-P2)

- **AC-P1** `pyproject.toml [tool.setuptools.packages.find] include` gains `conformance*`, so the package ships.
  Verified present-tense on this base: `include = ["lib*", "workflows*", "security*", "scripts*"]`.
- **AC-P2** (**pre-passing at RED time — declared**, §0.6) `core_manifest.json` **excludes** `conformance`. This
  is what holds `extra_bd` at zero. An absence-shield: it necessarily passes before the change and gains its
  power at GREEN, where an implementation adding the entry fails it. It is the **only** pre-passing test in this
  lot.

## 5. Out of scope

Every checker, oracle, freeze and attestation AC. `L0Report`'s field *semantics*. Byte fences. The BD-L0 grant.
The `checker_threat_model: "trusted-log"` field — introduced by L11 and removed by L12.

## 6. Process

Manual Option-D: frozen spec → RED → gate → GREEN. **GREEN does not start before an ACCEPTED verdict.** Measured
per-test counts recorded every round; inherited numbers re-measured, never trusted.
