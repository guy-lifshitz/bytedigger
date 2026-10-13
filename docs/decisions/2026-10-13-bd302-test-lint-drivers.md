---
red_tests:
  - engine_py/tests/test_bd302_test_lint_drivers.py
sibling_tests:
  - engine_py/tests/test_bd302_test_lint_drivers_fences.py
  - engine_py/tests/test_bd81_registry_driver_resolution.py
  - engine_py/tests/test_bd166_one_sided_predicate.py
paths:
  - forbidden-import-lint.py
  - stub-passability-lint.py
  - engine_py/bytedigger_engine/precommit_lints.py
  - engine_py/tests/test_bd66_precommit_enforcement.py
  - CHANGELOG.md
---
# bd#302: port the forbidden-import and stub-passability lint drivers

Slice of #66. Closes #302. Refs #66, #78, #81, #89. Base: origin/main @ 1023487. Tier 2.

## 1. Problem (measured on 1023487)

`precommit_lints.DECLARED_ABSENT` lists eleven names; two are the test lints
`forbidden-import-lint` and `stub-passability-lint`. The #78 layer licenses a declared name to have
no driver, so a staged `test_*.py` with a forbidden dynamic import or a mock of the unit under
test is committed with no verdict. The engine modules underneath already exist here
(`bytedigger_engine/forbidden_import.py`, `bytedigger_engine/stub_passability.py`); only the CLI
drivers are missing. Upstream HAL has them as `forbidden-import-lint.py` (69 lines) and
`stub-passability-lint.py` (96 lines).

## 2. Change

1. Two new drivers at the repo root, beside `one-sided-predicate-lint.py` (a hyphenated module
   inside the package breaks the import smoke, #81): `forbidden-import-lint.py`,
   `stub-passability-lint.py`. Each: first line `#!/usr/bin/env python3`, git mode `100755`
   (`precommit_enforce.run_plan` executes the driver directly), puts `engine_py/` derived from
   `__file__` on `sys.path` behind an `if ... not in sys.path` guard, and imports only from
   `bytedigger_engine.forbidden_import` / `bytedigger_engine.stub_passability`. No copy of a rule
   lives in a driver. No path, import, env name or wording that belongs to HAL.
2. `precommit_lints.py`: `LINT_DRIVER_DIRS` gains both names mapped to `_REPO_ROOT`;
   `DECLARED_ABSENT` loses both names and ends with exactly the nine `SPEC_LINTS`. The registry
   stays closed both ways (§2.2 of #66): a registry name with neither a driver nor a declaration
   still refuses with `BD66-REFUSE-MISSING-DRIVER`.
3. Driver contracts (port of HAL behaviour, with the two declared deviations in 3a):
   - `forbidden-import-lint.py FILE [FILE...] [--json]`. Files that are not test files by
     `forbidden_import.is_test_file` are skipped. Exit 0 clean, 1 at least one finding, 2 on an
     unreadable file (message with the path on stderr). Text line:
     `FORBIDDEN-IMPORT <file>:<line>: <pattern> - <text>`. `--json`: `{"findings": [{file, line, pattern, text}]}`.
   - `stub-passability-lint.py FILE [FILE...] [--uut NAME[,NAME]] [--json]`. Exit 0 clean, 1 at
     least one violation, 2 on malformed args or any file with an error (unreadable or syntax).
     Text line: `STUB-PASSABLE <file>:<patch_line> mocks UUT '<symbol>' (subject at :<import_line>)`;
     errors as `ERROR <file>: <error>` on stderr. `--json`: `{"violations": [{test_file, symbol, import_line, patch_line, kind}], "errors": [{test_file, error}]}`.
   - 3a. Deviations: the text separator is ASCII ` - ` (HAL prints an em dash); `test_file` is the
     path as given (HAL's `finding.import_line and filepath or filepath` is a no-op expression, dropped).
4. Switch / rollback. The #78 layer has no per-lint switch: `BD66_LINT_DIR` is ignored for names in
   `LINT_DRIVER_DIRS`, and `DECLARED_ABSENT` licenses an absence but never suppresses a driver that
   is on disk (§2.2). So no new flag is added and the documented rollback is two edits together:
   delete the driver file and add the name back to `DECLARED_ABSENT`. Adding the name alone does
   not disable the lint. CHANGELOG states this.
5. Existing test `test_bd66_precommit_enforcement.py` is edited by the RED phase only, without
   removing, relaxing or widening any assertion: AC7 (`on_disk` is now the three names
   `forbidden-import-lint`, `one-sided-predicate-lint`, `stub-passability-lint`; `declared` is the
   other nine; denominator 12 unchanged; docstring updated) and every place that uses
   `stub-passability-lint` as a stand-in driver written into `BD66_LINT_DIR` or the package copy
   (AC5, AC8, AC10, AC11 of that file): those names are now resolved through
   `LINT_DRIVER_DIRS`, so the stand-in must be written where the registry says the driver lives (in
   a package copy that is `<clone>/`, the copied registry recomputes its root), or the test must drive
   the real driver with a real violating input. The AC11 `DECLARED_ABSENT` rebind of that name
   becomes a no-op and is dropped, and AC11 keeps its discriminating power on the canonical lint
   directory: its stand-in is an UNMAPPED, undeclared name (`phantom-canon-lint` appended to the copied
   `TEST_LINTS`, driver written into the package copy, `BD66_LINT_DIR` deleted), so only
   `resolve_lint_dir()` falling back to `DEFAULT_LINT_DIR` can produce the refusal. AC13's named
   innocent changes from `forbidden-import-lint` (now a present driver) to `scope-inverse-lint` (still
   declared absent). No other file under `engine_py/tests` is expected to change; the
   RED agent greps for the two names and reports any other use it changes.
6. `CHANGELOG.md`: one entry under Unreleased, same style as the bd#296 entry, naming the rollback.
7. Not touched: the nine `SPEC_LINTS` drivers, the hook installer, `empty-input-assert-lint`
   registry entry, `precommit_enforce.py`, the engine modules `forbidden_import.py` and
   `stub_passability.py`.

## 3. Acceptance

All real-CLI ACs run the real `scripts/precommit_enforce_cli.py` (and the real drivers it execs)
in a temporary git clone with a hermetic git environment (no global/system config, no
`BD66_LINT_DIR`, no `PYTHONPATH`). A clone's `pre-commit` hook is a one-line script that execs the
real CLI by absolute path, so the refusal is a real `git commit` side effect. Fixture source text for
forbidden tokens is assembled from string pieces so the test file itself carries no NAME token.

RED file `test_bd302_test_lint_drivers.py` (each fails today):

- AC1 (case 1, real side effect). Staged `test_x.py` whose code calls the forbidden dynamic import
  (the `spec_from_file_location` NAME token on a known line) plus a clean control function:
  `git commit` rc != 0, `rev-parse --verify HEAD` fails (no commit object), the output contains
  `BD66-REFUSE-VIOLATION`, `lint=forbidden-import-lint` as a whole token, and the driver's own line
  `FORBIDDEN-IMPORT test_x.py:<that line>`. The same token inside a string literal or a comment
  does not refuse (paired sub-case in the same test: that commit is accepted).
- AC2 (case 2, real side effect). Staged `test_x.py` that imports `my_func` from a local module and
  patches that same symbol (`mock.patch`) in the test body: `git commit` rc != 0, no commit object,
  output contains `BD66-REFUSE-VIOLATION`, `lint=stub-passability-lint` as a whole token and
  `STUB-PASSABLE test_x.py:<patch line> mocks UUT 'my_func'`. Paired sub-case: patching a symbol
  that is not imported from the unit under test is accepted.
- AC3 (case 3). A clean staged test file: the layer returns 0 and the commit succeeds; and the layer
  reported both lints as run, i.e. `registry_prepass(DEFAULT_LINT_DIR)` marks both names present,
  and `build_lint_commands([], [test_x.py], DEFAULT_LINT_DIR)` has, for each of the two names,
  an `argv[0]` that is an existing executable file. (Pre-GREEN both are absent: this fails.)
- AC4 (case 4; case 5 is the closing paragraph below: the three existing files stay green). `DECLARED_ABSENT` equals `SPEC_LINTS` as a set and has length 9; neither name is in it; registry
  denominator is still 12; closed the other way: in a package copy (the copied registry resolves its
  repo root to the clone) with `forbidden-import-lint.py` deleted from the clone and the name NOT in
  `DECLARED_ABSENT`, the CLI exits 1 with `BD66-REFUSE-MISSING-DRIVER` naming the lint.
- AC5 (placement). `LINT_DRIVER_DIRS` maps both names to the repo root; `driver_path(name, <any dir>)`
  is `<repo root>/<name>.py` and an existing file; first line is `#!/usr/bin/env python3`; working
  tree exec bit set; git index mode `100755` (`git ls-files -s`, rc 0 asserted), the index half
  skipped with a printed reason when no `.git` exists (clean-room archive lane, same convention as
  bd66 AC15 / bd166 AC27).
- AC6 (driver contract, direct). Both drivers: exit 0 / 1 / 2 as in §2.3 on real files in tmp_path
  (clean, dirty, unreadable path; stub-passability also a file with a syntax error -> 2);
  `--json` shapes as in §2.3 with the key sets asserted exactly; `--uut NAME` makes
  stub-passability flag a patched symbol that the import heuristic alone would not flag (the file
  is clean without `--uut`, exit 0, and dirty with it, exit 1); forbidden-import skips a violating
  file whose basename is not a test name (exit 0, no output); one call with a violating file plus an unreadable file (and, for
  stub-passability, plus a syntax-error file) exits 2, not 1.
- AC7 (rollback claim, both halves). In a package copy: (a) driver present AND name added to
  `DECLARED_ABSENT` -> the violating staged file is still refused (declaration does not suppress a
  present driver); (b) driver file deleted AND name added to `DECLARED_ABSENT` -> the same staged
  file is accepted, rc 0 (the documented rollback works).
- AC8 (portability, AST/source scan of the two driver files). Imports are only stdlib modules from
  {argparse, json, sys, pathlib, __future__} and `bytedigger_engine.forbidden_import` /
  `bytedigger_engine.stub_passability`; no `subprocess`, `os.environ`, `os.getenv`, `socket`,
  `urllib`, `http`; no occurrence of `.claude`, `HAL_`, `SYSTEM/`, `/Users/`, or a bare
  `import forbidden_import` / `from stub_passability`; the only `sys.path` mutation is one insert of
  the sibling `engine_py` directory derived from `__file__`.

Sibling file `test_bd302_test_lint_drivers_fences.py` (passes before and after, lot-preflight
rejects passing tests in the RED file):

- F1. Real clone, clean staged `test_x.py`: commit accepted, rc 0 (the lanes the change must not
  break).
- F2. Real clone, staged TS test with a one-sided predicate and no control: refused with
  `lint=one-sided-predicate-lint` (the existing driver still resolves through the same mapping).
- F3. `cyrillic-prose-lint` and `one-sided-predicate-lint` remain in `LINT_DRIVER_DIRS`, and the
  registry list names and their order (`SPEC_LINTS`, `TEST_LINTS`, `TS_TEST_LINTS`, `TEXT_LINTS`)
  are unchanged.

`test_bd66_precommit_enforcement.py` is deliberately not in `sibling_tests`: preflight requires siblings
to pass today and its four edited ACs (5, 7, 8, 11) are red until GREEN by construction; they flip
green with the drivers and the gate reads that diff. Existing files `test_bd66_precommit_enforcement.py`, `test_bd81_registry_driver_resolution.py`,
`test_bd166_one_sided_predicate.py` must pass after GREEN (bd66 after the edits of §2.5).

## 4. Test plan

Local runs are targeted (the four files above plus `tests/test_bd79_english_only_tree.py` and
`python3 cyrillic-prose-lint.py`); the full suite is read from CI on the PR. GREEN is the two driver
files, the two registry edits and the CHANGELOG entry; nothing else under `engine_py/bytedigger_engine`.
