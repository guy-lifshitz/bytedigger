---
red_tests:
  - engine_py/tests/test_bd308_empty_input_assert_driver.py
sibling_tests:
  - engine_py/tests/test_bd66_empty_input_assert.py
  - engine_py/tests/test_bd81_registry_driver_resolution.py
paths:
  - empty-input-assert-lint.py
  - engine_py/bytedigger_engine/precommit_lints.py
  - engine_py/bytedigger_engine/precommit_enforce.py
  - engine_py/bytedigger_engine/empty_input_assert.py
  - CHANGELOG.md
---
# bd#308: empty-input-assert-lint runs at commit time, ratcheted against HEAD

Slice of #66 (after #303, #306, #307). Refs #66, #308. Base: origin/main @ fa7e297. Tier 2.
Decision (task owner): the 204 existing findings are not fixed. The layer refuses a staged test file
only when it has MORE findings than its version in `HEAD`.

## 1. Measurement (before the spec)

Re-measured on fa7e297 with `summarize_empty_input_asserts` over every tracked file for which
`precommit_lints.is_test_file` is true. The issue's numbers reproduce exactly.

| quantity | value |
|---|---|
| test files (`is_test_file`, tracked) | 626 (unreadable: 0) |
| candidate asserts | 359 |
| findings | 204 |
| files with at least one finding | 94 (15%) |
| of those, under `engine_py/tests/` / `tests/` | 85 / 9 |
| findings per file: 1 / 2 / 3 / 4 / 5 / 6 / 7 / 22 | 51 / 20 / 10 / 5 / 2 / 4 / 1 / 1 |
| largest | `tests/test_s3_spec_sample_check.py`: 22 |

Consequence: without the ratchet, 94 of 626 test files (15%) could never be touched again. With it,
a commit is refused only when it adds a finding. Side measurement: `ast.parse` on three test files
emits `SyntaxWarning` (invalid escape) to stderr; the driver must not forward it.

## 2. Change

1. **Driver** `empty-input-assert-lint.py` at the repo root, mode 100755, beside
   `forbidden-import-lint.py`. `FILE [FILE...] [--json]`. Imports `summarize_empty_input_asserts` and
   `identity_ok` from `bytedigger_engine.empty_input_assert` and `is_test_file` from
   `bytedigger_engine.precommit_lints`; no copy of the predicate. A file whose basename is not a test
   name is skipped. For each file, `identity_ok(candidates, controlled, escaped, len(findings))` false
   is a driver error: stderr line, exit 2 (a scanner that did not walk its population is not "clean").
   `SyntaxWarning` from parsing is suppressed (stderr stays empty).
   - Text: one line per finding, nothing else on stdout:
     `EMPTY-INPUT-ASSERT <file>:<line>: <form> - subject <subject!r> has no live denominator in scope`.
   - `--json`: `{"findings": [{file, line, subject, form}]}`.
   - Exit 0 clean (including zero candidates), 1 at least one finding, 2 usage error (no FILE) or an
     unreadable/undecodable path (2 wins over 1).
2. **Registry.** `empty-input-assert-lint` is appended to `TEST_LINTS`; `LINT_DRIVER_DIRS` maps it to
   `_REPO_ROOT`. Registry: SPEC+TEST+TS denominator 12 -> 13; `registry_names()` (which also sweeps `TEXT_LINTS`) 13 -> 14. New name tuple `RATCHETED_NAMES =
   ("empty-input-assert-lint",)` (deliberately NOT ending in `_LINTS`: `registry_names()` and the
   three `*_fences.py` pins discover every attribute with that suffix) and dict `LINT_OFF_ENVS = {"empty-input-assert-lint":
   "BD_EMPTY_INPUT_ASSERT_LINT"}`, both in `precommit_lints.py`. The module docstring of
   `empty_input_assert.py` stops claiming "no enforcement layer" and names the driver.
3. **Ratchet, in the layer** (`precommit_enforce.run_plan`, only for names in `RATCHETED_NAMES`).
   The driver runs on the working-tree file as today (accepted: the layer lints what is on disk, as every
   other lint; a file that does not parse scans as clean, the scanner's existing contract), stdout and
   stderr captured and stderr forwarded as today. Order of checks on this run is the layer's existing
   one: rc 1 with a `Traceback (most recent call last):` line on stderr is a crash ->
   `BD66-REFUSE-DRIVER-ERROR` (unless `BD_LINT_CRASH_IS_VIOLATION=1`), never a violation and never
   `now=0`; any rc other than 0/1: `BD66-REFUSE-DRIVER-ERROR` as today. rc 0: pass. rc 1 without a
   traceback: `now` = number of non-empty stdout lines. `base` = the same driver run
   (stdout line count; rc 0 -> 0) on `git show HEAD:<path>` written to a temp dir under the same
   basename; unborn HEAD, or a path absent from HEAD (`git show HEAD:<path>` exits non-zero: a new file, and a
   rename, which is a new path) -> `base` = 0. A blob that exists but on which the driver exits
   anything other than 0/1 (or crashes) is a driver error, not a baseline of 0. If `now > base`: print the driver's captured lines to stdout and add
   `BD66-REFUSE-VIOLATION: lint=<name> file=<file> rc=1 findings=<now> head=<base>`. If `now <= base`:
   pass, silent. The comparison is per staged file; a commit staging several files is refused if ANY one worsened.
4. **Switch.** `BD_EMPTY_INPUT_ASSERT_LINT=off` (exact value, case-sensitive; any other value, unset
   or empty is "on"): for each command of that lint print `BD66-LINT-OFF: lint=empty-input-assert-lint
   file=<file>` to stdout and skip; the driver is not run, no refusal. Order in `run_plan`: absent ->
   `BD_SPEC_LINTS_OFF` -> per-lint env -> NOT-FED -> run. The registry pre-pass is unaffected (an off
   lint still needs its driver). `BD_SPEC_LINTS_OFF` is unchanged and still honors only `SPEC_LINTS`.
5. **Not touched:** the scanner's rule, the other twelve registry names' drivers, `DECLARED_ABSENT` (stays 2 held
   names), the 204 existing findings, the TS grammar, the installer docs (separate docs PR).

## 3. Acceptance (RED)

Tests are in `engine_py/tests/test_bd308_empty_input_assert_driver.py`. GREEN also makes the
mechanical consequence of the 13th name in the older pins (they pass before and must pass after):
denominator 12 -> 13 and `TEST_LINTS` equality pins gain the new name in `test_bd66_precommit_enforcement.py`,
`test_bd302_test_lint_drivers(.py|_fences.py)`, `test_bd304_spec_lint_drivers(_fences).py`,
`test_bd305_spec_lint_feed_fences.py`; and `_package_copy` in the bd302/bd304/bd305 tests gets an
exit-0 stand-in driver for the new name. Also: the `with_drivers` set in the bd66 AC7 test, and the
registry name lists at `test_bd304_spec_lint_drivers_fences.py` (~L119) and `test_bd305_spec_lint_feed.py`
(~L221). The `*_fences.py` `_LINTS` attribute-name pins need NO edit (the new tuple has no `_LINTS` suffix).
No other edit to those files.

- AC1 driver text/JSON format, exit 0/1/2, non-test basename skipped, identity-broken scanner exit 2,
  SyntaxWarning file has empty stderr.
- AC2 registry: name in `TEST_LINTS`, in `LINT_DRIVER_DIRS` -> root, driver file exists and is
  executable; `registry_names()` has 14 names, none missing a driver.
- AC3 **real CLI, real hook, temp clone** (`install_git_hooks.py --install`; the commit goes through
  `git commit`): (a) the issue's reference case `assert findings == []` is refused with
  `BD66-REFUSE-VIOLATION` naming `empty-input-assert-lint` and the driver's `file:line` line, no
  commit created; (b) the same test with `assert len(rows) > 0` added commits; (c) a legacy file with
  one finding, edited without adding a finding, commits; (d) the same legacy file with a second
  finding added is refused; (e) a legacy file with two findings reduced to one commits; (f) a new
  file with one finding is refused (base 0).
- AC4 switch, both sides, same real hook: with `BD_EMPTY_INPUT_ASSERT_LINT=off` the case of AC3(a)
  commits and prints `BD66-LINT-OFF: lint=empty-input-assert-lint`; with the value `on`, `""` and
  `OFF` it is refused as with the variable unset.
- AC5 `BD_SPEC_LINTS_OFF=empty-input-assert-lint` disables nothing (prints `BD66-LINT-OFF-UNKNOWN`);
  the commit is still refused.
- AC6 ratchet unit: unborn HEAD with a one-finding file -> refused; HEAD run erroring -> driver error.
- AC7 per-file comparison, one real commit: two legacy files staged together, one reduced, one worsened
  -> refused naming only the worsened file; reduced + unchanged -> commits. Totals-based ratchets fail.
- AC8 a rename of a legacy file with one finding is refused (new path, base 0).
- AC9 layer mapping with the real hook: an undecodable staged test file (driver exit 2) and a driver
  that crashes with a traceback each give `BD66-REFUSE-DRIVER-ERROR`, never `BD66-REFUSE-VIOLATION`.
- AC10 the switch off does not skip the registry pre-pass: driver absent + switch off still gives
  `BD66-REFUSE-MISSING-DRIVER`.
- AC11 a nested path (`tests/sub/test_x.py`) shows `file:line` with the full relative path.
- AC3c/AC3e additionally assert no `EMPTY-INPUT-ASSERT` line in the output of the accepted commit.
