# Lot spec — bd#22 (L2): conformance package, shared contracts, quantifier-completeness lint

**v1.** Lot **L2** of the 12-lot split of bd#7. Base: `origin/main` @ `a2691f9`. Exactly **7 ACs**, pinned in the
issue before freeze so the split's 113-AC accounting stays checkable.

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

The three checks are then exactly: a `LEVEL` with no `NON-UNIFORMITY` row (check 1); a `NON-UNIFORMITY` row with
no `EXCLUDES` line, or one naming fewer than the reductions its level admits (check 2); a `SEAM` missing any of
the three property lines (check 3).

This grammar is **deliberately not** the format of this lot's own spec documents. It is the lint's input format,
and the lint is a tool, not a validator of these files. Conflating the two would make every prose sentence here a
potential lint failure.

## 2. Package and contracts (AC-C1..AC-C4)

- **AC-C1** `engine_py/conformance/` is an importable package with **no import-time side effects**: importing it
  MUST NOT read a file, touch the network, resolve a version, spawn a process, or create a directory.
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

## 3. AC-C5 — the quantifier-completeness lint `[A3]`

bd#7's `[G6:quant]` rule was **live and normative for four rounds while the defect it forbids kept recurring**,
because its audit was prose plus a hand-built table. Rounds 4-9 climbed the ladder upward; round 9 found a rung
below it; L1 round 1 found a fixture non-uniform in one ordering only. Three failures of the same rule, in three
different directions, all while the rule was written down and being cited.

**Prose does not hold this. AC-C5 makes it mechanical.**

- **AC-C5** A lint, **failing the build**, that reads a conformance spec and reports:
  1. **Every collection level named in the spec that has no non-uniformity row** — in **both** directions,
     containers and element kinds and payload fields. A named collection without a row is a lint failure, not a
     review note.
  2. **Every quantified requirement whose row does not enumerate the reductions it excludes.** This is the part
     `[G18:1]` requires and the part a level-only enumeration misses: a row saying "non-uniform, ≥2 members" is
     insufficient; it must name which of `any` / `all` / `first` / `last` its fixtures kill.
  3. **Every named seam that does not pin its interception property** (§0.2) — attribute path, call-time
     resolution, normalisation. Three consecutive lots were bitten with the seam correctly named.
  Asserted on **fixture spec documents**, at least one of which is non-conformant in each of the three ways, plus
  a conformant control that MUST pass. Per §0.1 the fixture set is non-uniform in each dimension independently,
  so a lint implementing only one of the three checks fails.

The lint lives here, not in L8, because the defect class is the **enumeration** — in L8 it would be one row for
one collection. It is L2's business because L2 owns the cross-lot invariants.

**Scope limit, deliberate:** the lint checks that a spec *documents* its quantifiers and seams. It does **not**
verify that the cited fixtures exist or discriminate — that is the gate's judgement and cannot be mechanised, as
bd#7 proved when a keyword sweep over its own specs mis-scored 11 of 13 ACs. A lint that claimed to do the gate's
job would be a worse artifact than the prose it replaces.

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
