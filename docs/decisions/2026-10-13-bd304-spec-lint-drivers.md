---
red_tests:
  - engine_py/tests/test_bd304_spec_lint_drivers.py
sibling_tests:
  - engine_py/tests/test_bd304_spec_lint_drivers_fences.py
  - engine_py/tests/test_bd81_registry_driver_resolution.py
  - engine_py/tests/test_bd166_one_sided_predicate.py
paths:
  - scope-inverse-lint.py
  - helper-extraction-lint.py
  - spec-coverage-lint.py
  - token-consistency-lint.py
  - presence-triad-lint.py
  - format-conversion-lint.py
  - closure-evidence-lint.py
  - mutation-two-sidedness-lint.py
  - engine_py/bytedigger_engine/precommit_lints.py
  - engine_py/tests/test_bd66_precommit_enforcement.py
  - engine_py/tests/test_bd302_test_lint_drivers.py
  - CHANGELOG.md
---
# bd#304: drivers for eight of the nine SPEC_LINTS, measured against existing specs before a name leaves DECLARED_ABSENT

Slice 2 of #66. Refs #66, #302, #89, #305. Follow-up for the three held lints and the spec-cite
driver: #305.
Base: origin/main @ 6e15ff5. Tier 3.

## 1. Problem (measured on 6e15ff5)

`precommit_lints.DECLARED_ABSENT` lists the nine `SPEC_LINTS`. The checking modules exist here
(`scope_inverse.py`, `helper_extraction.py`, `spec_cite.py`, `spec_coverage.py`,
`token_consistency.py`, `presence_triad.py`, `format_conversion_cases.py`,
`scripts/lib/closure_evidence_verifier.py`, `scripts/lib/mutation_two_sidedness_verifier.py`, all
under `engine_py/bytedigger_engine/`); no driver file exists for any of them, so a staged
`*_spec.md` is committed with no verdict. Upstream HAL has the nine drivers (38-95 lines each); eight are ported here (2.7).

Measurement (run before the registry edit; the corpus is the five tracked `*_SPEC.md` files, which
are exactly what `is_spec_file` classifies, plus the 37 frozen lot specs under `docs/decisions/`
with `red_tests:` front matter; every driver run from the repo root, the layer's cwd):

| lint | hits on the 5 `*_SPEC.md` | decision |
|---|---|---|
| scope-inverse, helper-extraction, presence-triad, format-conversion, closure-evidence, mutation-two-sidedness | 0 each (also 0 of 37 frozen specs) | leave `DECLARED_ABSENT` |
| spec-cite | exit 1 on 4 of 5 (`missing_file` findings), measured through `spec_cite.lint_spec`, the call its driver makes | stays declared absent; driver not added (2.7) |
| spec-coverage | exit 2 on 5 of 5 (no AC table) | stays declared absent |
| token-consistency | exit 1 on 2 of 5 (5 findings) | stays declared absent |

Existing specs are not edited and a lint with hits is not silenced; its hit list is in the PR body
and in #305.

## 2. Change

1. Eight drivers at the repo root, beside `one-sided-predicate-lint.py` (a hyphenated module inside
   the package breaks the import smoke, #81): `<name>.py` for each name in `SPEC_LINTS` except
   `spec-cite-lint` (2.7). Each:
   first line `#!/usr/bin/env python3`, git mode `100755`, the `engine_py/` directory derived from
   `__file__` and put on `sys.path` by one `sys.path.insert` guarded by `if ... not in sys.path`,
   with the `__file__` expression written inline in the insert call (no intermediate variable,
   bd#302 gate r2 F1), then imports only from the checking module of that lint through the
   package (`bytedigger_engine.<module>`; the two verifiers as
   `bytedigger_engine.scripts.lib.<module>`; `bytedigger_engine.error_codes` only inside
   spec-coverage's `--taxonomy` branch). No copy of a rule lives in a driver. No path, import, env
   name or wording that belongs to HAL. All text is ASCII (` - ` where HAL prints an em dash).
2. Driver contracts, all `--spec PATH` (required) `[--json]`; exit 0 clean, 1 at least one
   finding, 2 unreadable spec (message on stderr, nothing on stdout) or argparse usage error.
   Finding line (text) / `--json` `{"findings": [...]}` item keys:

   | driver | text line | json item keys |
   |---|---|---|
   | scope-inverse | `SCOPE-INVERSE-MISSING line N: <reason>` | as returned by `scan_scope_inverse` |
   | helper-extraction | `UNGUARDED-HELPER line N: <construct> - <text>` | as returned by `scan_helper_extraction` |
   | token-consistency | `TOKEN-INCONSISTENCY line N: <evidence>` | as returned by `scan_token_consistency` |
   | presence-triad | `TRIAD-MISSING line N: <evidence>` | as returned by `scan_presence_triad` |
   | format-conversion | `FORMAT-CASES line N: <evidence>` | as returned by `scan_format_conversion` |
   | closure-evidence | `<rule_id> line N: <evidence>` | `line`, `rule_id`, `evidence` |
   | mutation-two-sidedness | `<rule_id> line N: <evidence>` | `line`, `rule_id`, `evidence` |
   | spec-coverage | `UNCOVERED-OP line N: op=<r> - <reason>` | as returned by `scan_spec_coverage`; a spec with no AC table: `no AC table found: ...` on stderr, exit 2; opt-in `--taxonomy`, `--finalize` as upstream |

   `line` is 1-based. closure-evidence and mutation-two-sidedness compute `line` as
   `spec_text.count("\n", 0, finding.offset) + 1` and pass `Path(spec).parent` to the verifier.
3. `precommit_lints.py`: `LINT_DRIVER_DIRS` gains `_REPO_ROOT` for the six lints that leave
   (scope-inverse, helper-extraction, presence-triad, format-conversion, closure-evidence,
   mutation-two-sidedness). `DECLARED_ABSENT` ends with exactly three names: spec-cite-lint,
   spec-coverage-lint, token-consistency-lint, with a comment naming #305. The two held drivers
   that exist (spec-coverage, token-consistency) are on disk and not mapped: `driver_path` resolves them to the package directory, where no file
   exists, so the layer reports them declared absent and does not run them. The registry stays
   closed both ways (§2.2 of #66).
4. Switch / rollback. No new flag. Per lint, back to declared absent = remove its
   `LINT_DRIVER_DIRS` entry and add the name to `DECLARED_ABSENT`, together. Adding the name alone
   does not disable the lint (a mapped driver on disk is run; `DECLARED_ABSENT` never suppresses
   it, #302 §2.4). Deleting the driver file and re-declaring also works. Turning a held lint on is
   the reverse, in one change (tracked in #305).
5. Existing tests edited by the RED phase only, without removing, relaxing or widening any
   assertion: `test_bd302_test_lint_drivers.py` `_package_copy` (its package-copy stand-ins are
   extended for the six newly mapped lints) and AC4 (`DECLARED_ABSENT` equals the three held names,
   `len == 3`; the "neither test lint is in it" assertion stays) and
   `test_bd66_precommit_enforcement.py` AC7 (`on_disk` is the nine names with a driver on disk:
   the three #302/#166 names plus the six; `declared` is the other three; denominator 12) and AC13
   (`innocent` changes from `scope-inverse-lint` to `spec-cite-lint`, still declared absent). The
   RED agent greps `engine_py/tests` for the nine names and for `DECLARED_ABSENT` and reports any
   other edit it makes.
6. `CHANGELOG.md`: one entry under Unreleased, same style as the bd#302 entry, naming the six, the
   three held with #305, and the rollback.
7. Consumer check, and why the spec-cite driver is not in this PR. `workflows/phase_45_spec.py`
   (`_verify_spec_cite_lint`, and the batch collector near `:2409`) execs the repo-root
   `spec-cite-lint.py` by path with `--spec P --repo-root R --json`. Today the file is absent, so a
   non-frozen spec stops phase 4.5 with `E_SPEC_CITE_LINT_UNAVAILABLE`; putting the file at the
   root would switch on a blocking `/build` gate (`E_SPEC_CITE_LINT_FAIL`) as a side effect of a
   pre-commit slice, on a lint that has findings on 4 of the 5 existing specs. So the driver is
   withheld and moved to #305 with that consumer described. Rule applied to every driver: a
   driver that a live consumer outside `precommit_enforce` executes by path and whose lint has
   findings on existing specs goes to #305; one without findings stays and the consumer is named.
   Checked in the tree for the other eight: no code path execs any of them by file name. The only
   other consumers are library imports in `phase_45_spec.py` (`scan_scope_inverse`,
   `scan_helper_extraction`, and the token-consistency / presence-triad / format-conversion batch
   near `:3113`), which touch no driver file. So the six leaving lints and the two dormant ones
   (spec-coverage, token-consistency) stay.
8. Not touched: `workflows/phase_45_spec.py` and every other consumer, the hook installer, the `empty-input-assert-lint` registry entry,
   `precommit_enforce.py`, every checking module, every existing spec.

## 3. Acceptance

Real-CLI ACs run the real `scripts/precommit_enforce_cli.py` (and the real drivers it execs) in a
temporary git clone with a hermetic git environment (no global/system config, no `BD66_LINT_DIR`,
no `PYTHONPATH`), the pre-commit hook being a one-line script that execs the real CLI by absolute
path, so a refusal is a real `git commit` side effect. Same harness as `test_bd302_test_lint_drivers.py`.
Spec fixtures are authored by the RED agent from the checking module of each lint (minimal text that
the module flags), each violating fixture is clean for the other eight lints, and the clean fixture
is clean for all nine lints.

RED file `test_bd304_spec_lint_drivers.py` (each fails today):

- AC1 (case 1, six leaving lints, real side effect). For each of the six: staged `x_spec.md`
  violating that lint: `git commit` rc != 0, no commit object (`rev-parse --verify HEAD` fails),
  output has `BD66-REFUSE-VIOLATION`, `lint=<name>` as a whole token, and the driver's own line for
  that lint with the 1-based line number of the violation (token column of the table in 2.2; for
  the two verifiers the `rule_id` the module emits). Paired sub-case in the same test: the minimal
  compliant variant of the same spec is accepted.
- AC2 (case 2, three held lints, real registry). A staged `x_spec.md` that the held lint flags
  (a cite of a missing file for spec-cite, shown through `spec_cite.lint_spec`; token drift for
  token-consistency; no AC table for spec-coverage; the last two through their drivers run
  directly): the commit is accepted rc 0 and the output has no `lint=<held>`;
  `registry_prepass(DEFAULT_LINT_DIR)` marks each held name not present while the name is in
  `DECLARED_ABSENT`; for spec-coverage and token-consistency the repo-root `<name>.py` exists and
  is executable and `driver_path` of it with `DEFAULT_LINT_DIR` is not a file; for spec-cite no
  `spec-cite-lint.py` exists at the repo root.
- AC3 (case 3). A clean staged `x_spec.md`: the layer returns 0 and the commit succeeds;
  `build_lint_commands([spec], [], DEFAULT_LINT_DIR)` has a command for each of the nine names,
  `argv[0]` an existing executable file for the six and not an existing file for the three; the
  pre-pass marks exactly the six present.
- AC4 (case 4, closed both ways). `set(DECLARED_ABSENT)` equals the three held names, `len == 3`;
  `LINT_DRIVER_DIRS` maps the six to the repo root and maps none of the three; the registry
  denominator is still 12. In a package copy: (a) a leaving driver file deleted and its name not in
  `DECLARED_ABSENT` -> CLI exit 1 with `BD66-REFUSE-MISSING-DRIVER` naming that lint; (b) each of the
  three held names removed from the copy's `DECLARED_ABSENT` (for spec-coverage and
  token-consistency the dormant file is still at the clone root, unmapped) -> exit 1 with
  `BD66-REFUSE-MISSING-DRIVER` naming it (a dormant file is not resolved).
- AC5 (placement). For the eight: file at the repo root, first line `#!/usr/bin/env python3`, exec
  bit, git index mode `100755` (`git ls-files -s`, rc 0 asserted; the index half skipped with a
  printed reason when no `.git` exists, as in bd302 AC5).
- AC6 (driver contract, direct, the eight). On real files in tmp_path: exit 0 clean, 1 dirty, 2
  for an unreadable `--spec` path (message with the path on stderr, stdout empty), 2 for a missing
  `--spec`; the `--json` key sets asserted exactly as in 2.2; text and json carry the same 1-based
  line; one assertion that line N is the line of the violation in the fixture. spec-coverage: a
  spec without an AC table exits 2 with `no AC table found` on stderr.
- AC7 (rollback, six leaving lints, package copy). (a) mapping kept AND name added to
  `DECLARED_ABSENT` -> the violating staged spec is still refused; (b) mapping removed AND name
  added to `DECLARED_ABSENT` (driver file stays at the clone root) -> the same spec is accepted
  rc 0; (c) driver file deleted AND name added, mapping removed -> accepted.
- AC8 (portability, AST/source scan of the eight files). Imports only from {argparse, json, sys,
  pathlib, __future__} and `bytedigger_engine.<its own module>` (for the verifiers
  `bytedigger_engine.scripts.lib.<module>`, for spec-coverage also `bytedigger_engine.error_codes`);
  no `subprocess`, `os.environ`, `os.getenv`, `socket`, `urllib`, `http`; no occurrence of
  `.claude`, `HAL_`, `SYSTEM/`, `/Users/`; no bare sibling import; the only `sys.path` mutation is
  one insert of the sibling `engine_py` directory with `__file__` inline in the insert arguments,
  and no absolute-path string literal.
- AC9 (measurement pin). Corpus = every tracked-or-present file under the repo root whose
  basename lower-cased ends in `_spec.md` (excluding `.git`); it is non-empty (asserted). Each of
  the six leaving drivers exits 0 on every file; each held driver (spec-coverage, token-consistency) exits non-zero on at least one
  file, and `spec_cite.lint_spec(file, repo_root)` returns a non-zero exit code for at least one file. Failing message says that a held lint now being clean means #305 can flip it.

- AC10 (consumer guard, 2.7). The path `phase_45_spec` computes for the cite driver
  (`Path(phase_45_spec.__file__).parent.parent.parent.parent / "spec-cite-lint.py"`) is not a
  file, `spec-cite-lint.py` is absent at the repo root, and the repo-root `*-lint.py` set equals the
  the six existing drivers (core-boundary, cyrillic-prose, fixture-schema, forbidden-import,
  one-sided-predicate, stub-passability) plus the eight new ones. (Re-freeze amendment: the
  earlier text said "five" and miscounted the set; the AC10 test already used six.)

Sibling file `test_bd304_spec_lint_drivers_fences.py` (passes before and after; lot-preflight
rejects passing tests in the RED file):

- F1. Real clone, staged clean `test_x.py`: accepted, rc 0.
- F2. Real clone, staged `test_x.py` with the forbidden dynamic import: refused with
  `lint=forbidden-import-lint` (the #302 drivers still resolve).
- F3. Registry list names and order (`SPEC_LINTS`, `TEST_LINTS`, `TS_TEST_LINTS`, `TEXT_LINTS`),
  and the four older mappings in `LINT_DRIVER_DIRS`, unchanged.

Existing `test_bd66_precommit_enforcement.py` (after the 2.5 edits), `test_bd302_test_lint_drivers.py`
(after its AC4 edit), `test_bd81_registry_driver_resolution.py`, `test_bd166_one_sided_predicate.py`,
`test_bd302_test_lint_drivers_fences.py` must pass after GREEN. bd66 and bd302 are deliberately not
in `sibling_tests`: their edited ACs are red until GREEN by construction.

## 4. Test plan

Local runs are targeted (the files named above plus `tests/test_bd79_english_only_tree.py` and
`python3 cyrillic-prose-lint.py`); the full suite is read from CI on the PR. GREEN is the eight
driver files, the two registry edits and the CHANGELOG entry; nothing else under
`engine_py/bytedigger_engine`.
