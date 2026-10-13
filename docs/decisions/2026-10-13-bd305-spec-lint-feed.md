---
red_tests:
  - engine_py/tests/test_bd305_spec_lint_feed.py
sibling_tests:
  - engine_py/tests/test_bd305_spec_lint_feed_fences.py
  - engine_py/tests/test_bd304_spec_coverage_flags.py
  - engine_py/tests/test_bd81_registry_driver_resolution.py
  - engine_py/tests/test_bd166_one_sided_predicate.py
paths:
  - spec-coverage-lint.py
  - engine_py/bytedigger_engine/precommit_lints.py
  - engine_py/bytedigger_engine/precommit_enforce.py
  - engine_py/tests/test_bd304_spec_lint_drivers.py
  - engine_py/tests/test_bd302_test_lint_drivers.py
  - engine_py/tests/test_bd66_precommit_enforcement.py
  - CHANGELOG.md
---
# bd#305: the layer decides what each spec lint is fed; a driver crash is not a violation

Slice 3 of #66. Refs #66, #304, #305. Base: origin/main @ c5e0b99. Tier 3.
Decision (MGR, on the fork of issue #305): existing specs are not edited. Per lint, findings on the
five existing specs are split into a feed artifact (the layer gave the lint a spec or a path base it
was never meant to see: narrow the feed, enable the lint) and a real defect of the spec or of the rule
(the lint stays held, listed in the PR and in #305). A lint is never silenced to get a clean count.

## 1. Measurement (before the spec; every lint over every spec, before and after the proposed narrowing)

Run from the repo root (the layer's cwd) on c5e0b99. "Existing" is what `precommit_lints.is_spec_file`
stages: 5 files, all under `engine_py/bytedigger_engine/conformance/`. Frozen is the 37 lot specs under
`docs/decisions/` with `red_tests:` front matter, measured for information (their names do not end in
`_spec.md`, so the layer never feeds them).

Narrowing tried per lint:
- spec-cite: the cited path resolves against the spec's repo root (before) or, in addition, against the
  spec's package root, the top directory of the unbroken `__init__.py` chain above the spec (after, a
  finding stays blocking only when blocking under both). Package root alone was measured too and
  regresses QUANT_LINT_SPEC 0 -> 1, so "after" is the union.
- spec-coverage: the lint is fed only a spec that carries an AC table (`spec_coverage.check_ac_table`
  does not raise). Before: rc 2 `no AC table found`, which the layer reads as a refused commit.
- token-consistency: no feed-level narrowing exists that is not a silencing (the findings are inside
  the spec text); the measurement is the same before and after.

| spec | spec-cite before (blocking) | spec-cite after | spec-coverage before | spec-coverage after | token-consistency (before = after) |
|---|---|---|---|---|---|
| AUTHORSHIP_SPEC | 20 | 3 | rc 2 (no AC table) | not fed | 0 |
| CONTRACTS_SPEC | 8 | 1 | rc 2 | not fed | 0 |
| EMISSIONS_SPEC | 38 | 1 | rc 2 | not fed | 0 |
| ORACLE_SPEC | 38 | 13 | rc 2 | not fed | 2 |
| QUANT_LINT_SPEC | 0 | 0 | rc 2 | not fed | 3 |
| **5 existing, totals** | **4 of 5 blocked** | **4 of 5 blocked, 18 findings** | **5 of 5 rc 2** | **0 findings, 0 refusals** | **2 of 5, 5 findings** |
| 37 frozen (info) | 35 blocked | 35 blocked | 32 rc 2, 5 with a table: 0 findings | 0 findings | 4 of 37 |

Classification:
- **spec-coverage: feed artifact.** The five are conformance contracts, not lot specs; they have no AC
  table by kind. Enabled, fed only specs with an AC table. The five frozen specs that have a table
  give 0 findings.
- **spec-cite: real defects remain after narrowing; stays held, driver file not added.** Package-relative
  resolution removes 20+8+38+38 -> 3+1+1+13 findings (the artifact part). What is left cites files
  that exist nowhere in this repo: `workflows/phase_2_explore.py`, `graphify-shim.sh` (AUTHORSHIP);
  `_pytest/cacheprovider.py` (CONTRACTS); `git_port.py` (EMISSIONS); `run.py`, `phase_45_spec.py`,
  `run-phase.ts`, `run_oss_driver.py`, `oracle.py`, `llm_subprocess.py`,
  `lib/plugins/disk_truth/test_runner.py`, `tests/test_phase_6_FEB64BA8_backtick_strip.py` and a few
  prose tokens read as symbols (`finally`, `signal`, `SyntaxWarning`) (ORACLE). The non-zero
  count is the stop condition of the decision: `spec-cite-lint.py` is not put at the repo root, so the
  blocking phase-4.5 cite gate (`phase_45_spec._verify_spec_cite_lint`, executes the root file by path
  with `--spec P --repo-root R --json`) is NOT switched on by this lot. Stated as its own line in the PR.
- **token-consistency: real findings, stays held** (5 findings in 2 specs):
  `ORACLE_SPEC.md:417` `engine.py` vs `engine_py` (a file and a directory, one cluster by the rule);
  `:952` `TIMEOUT_S` vs `timeout_s` (a constant and a parameter); `QUANT_LINT_SPEC.md:657`
  `Audit_Case` vs `audit_case`, `:833` `Audit_Field` vs `audit_field`, `:975` `Path.Read_Text` vs
  `Path.read_text` (deliberate case-mismatch examples in a spec about case folding). Fixing them is a
  rule change or a spec edit; neither is this lot's call.

## 2. Change

1. **Feed rule, in the layer.** `precommit_lints.py` gains one function,
   `feed_skip_reason(name, path) -> str | None`: for `spec-coverage-lint` it reads `path` as UTF-8 and
   returns `"no AC table"` iff `bytedigger_engine.spec_coverage.check_ac_table` raises `NoACTableError`
   (the checking module is the one carrier of the rule; no copy). Any other name, an unreadable or
   undecodable file: `None` (the driver runs and reports exit 2 itself).
2. **`precommit_enforce.run_plan`**, per command, in this order: (a) driver absent at pre-pass:
   silent skip, as today; (b) the name is in the off set (2.3): print
   `BD66-LINT-OFF: lint=<name> file=<file>` to stdout, skip; (c) `feed_skip_reason` returns a reason:
   print `BD66-LINT-NOT-FED: lint=<name> file=<file> reason=<reason>`, skip; (d) run the driver.
   Skips are not refusals and do not change the return code.
3. **Switch 1, `BD_SPEC_LINTS_OFF=<name>[,<name>]`.** Read by the layer from the process environment.
   Names split on `,`, whitespace stripped, empties dropped, case-sensitive. A name in `SPEC_LINTS` is
   not run (2.2b). A name not in `SPEC_LINTS` is not an off switch: once per run, before the plan,
   `BD66-LINT-OFF-UNKNOWN: name=<name>` to stdout, and nothing is disabled by it. Unset or empty: no
   effect (default). The registry pre-pass is not affected: an off lint must still have a driver or a
   declaration (closed both ways, #66 2.2). This is the one-setting, no-code-edit rollback of any lint
   enabled by this lot.
4. **Driver crash is not a violation; Switch 2, `BD_LINT_CRASH_IS_VIOLATION=1`.** `run_plan` runs the
   driver with stderr captured, writes the captured stderr to the layer's stderr unchanged after the
   driver exits, and: rc 1 with a line starting `Traceback (most recent call last):` in that stderr is
   a crash: `BD66-REFUSE-DRIVER-ERROR: lint=<name> file=<file> rc=1 driver crashed (uncaught exception,
   traceback on stderr)`, commit refused as for any driver error. rc 1 without that line is a violation
   as before; every rc other than 0 and 1 is a driver error as before. With the environment value
   exactly `1` the crash check is skipped and rc 1 is a violation, the pre-#305 reading (rollback).
   Default off. Any other value, including unset and empty, is "off".
5. **`spec-coverage-lint.py`: text format for the opt-in findings; no KeyError (F4, issue item 2).**
   One line per finding, ASCII, `<LABEL> line <N>: <detail> - <reason>`, `<N>` the 1-based line or `?`
   when the scanner gives none; branch on the `rule` key, a finding without `rule` is an op finding:

   | finding | text line |
   |---|---|
   | op (no `rule`) | `UNCOVERED-OP line N: op=<repr> - <reason>` (unchanged) |
   | `rule == spec_taxonomy_missing_disposition` | `TAXONOMY-MISSING-DISPOSITION line N: token=<token> - <reason>` |
   | `rule == spec_finalize_branch_uncovered` | `FINALIZE-BRANCH-UNCOVERED line N: group=<group> - <reason>` |
   | `rule == spec_finalize_ac_unanchored` | `FINALIZE-AC-UNANCHORED line N: group=<group> - <reason>` |
   | any other `rule` | `FINDING line N: rule=<rule> - <reason>` |

   `--json`, exit codes, the flags and the rule code are unchanged. The docstring drops the
   "known upstream limitation" and "dormant" paragraphs and gets the table.
6. **Registry.** `LINT_DRIVER_DIRS` gains `spec-coverage-lint` -> `_REPO_ROOT`. `DECLARED_ABSENT` is
   exactly `spec-cite-lint`, `token-consistency-lint` (len 2), the comment names #305 and 1's
   classification. The layer calls the driver with `--spec` only; `--taxonomy`/`--finalize` stay
   opt-in for direct use.
7. **Not touched:** every checking module, `workflows/phase_45_spec.py`, the other seven drivers
   (their only change is the new AC7 test), the `spec-cite-lint.py` file (absent), the existing specs.
8. **Switches summary.** `BD_SPEC_LINTS_OFF` turns a lint off per name; `BD_LINT_CRASH_IS_VIOLATION=1`
   restores the old reading of a crash. Neither has a code edit as its way back. Re-registering a held
   lint stays "map + undeclare in one change" (#304 2.4).
9. **Existing tests edited in the RED phase only, without removing, relaxing or widening an assertion:**
   `test_bd304_spec_lint_drivers.py` (the held set is two names, `len == 2`; `_DORMANT` is only
   token-consistency; the spec-coverage case moves from the held group to the layer group: the real
   layer on a table-less spec accepts with a NOT-FED line, on a table spec with an uncovered op
   refuses; AC3's command/pre-pass counts: seven present, two not; AC9's "held driver exits non-zero on
   at least one existing file" stays for spec-coverage when run directly, and its failing message keeps
   saying a held lint going clean means #305 can flip it for the two still held),
   `test_bd302_test_lint_drivers.py` AC4 (`DECLARED_ABSENT` the two names), `test_bd66_precommit_enforcement.py`
   AC7 (`on_disk` ten names, `declared` the other two, denominator 12). The RED agent greps
   `engine_py/tests` for the nine names and `DECLARED_ABSENT` and reports any other edit.
10. `CHANGELOG.md`: one entry under Unreleased: spec-coverage on, two held with the list, text format,
    crash label, both switches.

## 3. Acceptance

Real-CLI ACs run the real `scripts/precommit_enforce_cli.py` and real drivers in a temporary git clone
with the hermetic environment of `test_bd304_spec_lint_drivers.py` (same harness, same `_package_copy`
pattern for package-copy cases), a refusal being a real `git commit` side effect. Env switches are set
only in the child environment of the case.

RED file `test_bd305_spec_lint_feed.py` (each fails today):

- AC1 (F4 text, driver run directly). Taxonomy fixture (AC table, a new `E_...` code without OWN/DEFER),
  `--taxonomy` text: exit 1, stdout has `TAXONOMY-MISSING-DISPOSITION line <N>: token=<code>` with the
  1-based line of the code's first mention, stderr has no `Traceback`. Finalize fixture
  (`--finalize`, uncovered branch): `FINALIZE-BRANCH-UNCOVERED line <N>: group=<g>`. The unanchored
  variant `FINALIZE-AC-UNANCHORED` is asserted when a real fixture drives the scanner there; if none
  does, the RED agent says so in its report (no stub findings: no copy of the rule). `--json` for the same fixture has the same line.
- AC2 (the KeyError, exact issue input). AC table + `E_FOO` without disposition, `--taxonomy` without
  `--json`: exit 1, no `KeyError`/`Traceback` in stderr, one finding line. Same for `--finalize`.
- AC3 (feed, real CLI). Staged `x_spec.md`: (a) no AC table: commit accepted rc 0, output has
  `BD66-LINT-NOT-FED` with `lint=spec-coverage-lint`, no `BD66-REFUSE`; (b) AC table and an uncovered
  bold op: refused, `BD66-REFUSE-VIOLATION`, `lint=spec-coverage-lint`, and `UNCOVERED-OP line <N>`;
  (c) AC table, op covered: accepted, no NOT-FED line; (d) a staged copy of each of the five existing
  `*_SPEC.md`, one commit each: accepted rc 0, i.e. the issue's case (b).
- AC4 (the two held lints stay held, real CLI). A staged spec with token drift: accepted, no
  `lint=token-consistency-lint`; `set(DECLARED_ABSENT)` equals the two names; no `spec-cite-lint.py` at
  the repo root and the path `phase_45_spec` computes for it is not a file; the root `*-lint.py` set is
  unchanged. Closed both ways in a package copy: removing either name from the copy's `DECLARED_ABSENT`
  -> exit 1 `BD66-REFUSE-MISSING-DRIVER` naming it; deleting `spec-coverage-lint.py` from the copy root
  with the name not declared -> the same refusal for it.
- AC5 (switch 1, both ways). `BD_SPEC_LINTS_OFF=spec-coverage-lint`: the AC3(b) spec is accepted rc 0
  and output has `BD66-LINT-OFF: lint=spec-coverage-lint`. Unset, empty and whitespace-only: refused as
  in AC3(b). `scope-inverse-lint,spec-coverage-lint` (and with spaces around the comma): both are off,
  one OFF line per lint. Only `spec-coverage-lint` off: a spec violating scope-inverse is still
  refused. `BD_SPEC_LINTS_OFF=forbidden-import-lint`: `BD66-LINT-OFF-UNKNOWN: name=forbidden-import-lint`
  in output and a violating staged test file is still refused (the switch is spec-only). A name typo
  disables nothing.
- AC6 (switch 2, both ways, package copy with a crashing driver). In the copy, `helper-extraction-lint.py`
  is replaced by a script that raises `RuntimeError`. A clean spec staged: default, no variable ->
  refused, output has `BD66-REFUSE-DRIVER-ERROR`, `lint=helper-extraction-lint`, `rc=1`, `crashed`, and
  no `BD66-REFUSE-VIOLATION` for that lint; the traceback text still reaches stderr. With
  `BD_LINT_CRASH_IS_VIOLATION=1`: refused with `BD66-REFUSE-VIOLATION` for that lint and no
  `BD66-REFUSE-DRIVER-ERROR` for it. `=0` and `=` behave as the default. A real violation (driver
  exit 1 with findings, no traceback): `BD66-REFUSE-VIOLATION` under both settings. A driver exiting 2:
  `BD66-REFUSE-DRIVER-ERROR` under both settings.
- F4 (in the sibling file, was AC7: passes today, a pin not a RED; driver exit 2, the eight drivers of #304). For each of the eight, direct: an
  undecodable `--spec` file (bytes `\xff\xfe\x00`) and a directory as `--spec`: exit 2, stdout empty,
  stderr has the path.
- AC8 (no blind `op` index). The driver's text-mode loop has no unconditional `f['op']`/`f["op"]`
  subscript on a finding (AST scan of `spec-coverage-lint.py`); behaviour is AC1/AC2.
- AC9 (measurement pin, 1). On the five existing specs from the repo root:
  `token_consistency.scan_token_consistency` has findings on at least one; under the union rule
  (blocking in both repo-root and package-root resolution) `spec_cite` is blocking on at least one;
  the layer feeds `spec-coverage-lint` to none of them. Failing message: a held lint going clean here
  means #305's remainder can flip it.

Sibling file `test_bd305_spec_lint_feed_fences.py` (passes before and after):
- F1. Real clone, staged clean `test_x.py`: accepted rc 0; staged `test_x.py` with the forbidden dynamic
  import refused with `lint=forbidden-import-lint`.
- F2. Registry list names and order, and the other ten `LINT_DRIVER_DIRS` mappings, unchanged.
- F3. A staged spec violating scope-inverse is refused with `lint=scope-inverse-lint` (the six #304 lints
  still run, with no switch set).

`test_bd304_spec_coverage_flags.py` (JSON mode, #304) must keep passing.

## 4. Test plan

Local runs are targeted: the two new files, the three edited files, `test_bd304_spec_coverage_flags.py`,
`test_bd81_registry_driver_resolution.py`, `test_bd166_one_sided_predicate.py`,
`tests/test_bd79_english_only_tree.py` and `python3 cyrillic-prose-lint.py`. The full suite is read from
CI on the PR. GREEN is `spec-coverage-lint.py`, the two registry-side edits (`precommit_lints.py`,
`precommit_enforce.py`) and the CHANGELOG entry; nothing else under `engine_py/bytedigger_engine`.
