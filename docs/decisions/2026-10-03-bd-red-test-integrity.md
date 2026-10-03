# bd: RED-side test integrity gate (deleted / removed / skipped tests, counted by script)

**Status:** r2 spec (folds gate r1, see ...-gate-r1.md) · **Class:** SYSTEMATIC · **Chokepoint:** the one RED commit site, `_commit_red_tests` in
`workflows/phase_5_implement.py`, right after the GH282 block; one pure module `lib/test_integrity.py`.
**Enforcement layer (Principle C):** deterministic gate in the engine (`E_RED_TEST_INTEGRITY`, recoverable=False).
**Adds no LLM call.** Check ladder position: script (this) -> nothing above it for this class.
**Issue:** bd#226. **Source:** HAL audit hal#2320 finding 1 + class 13; plan step 2.

**Introduced because (provenance):** A/B r3 task 1569: C0 deleted 9 existing test files (-4804 lines), the bd arm
7 (-5471); acceptance cannot see it and nobody checked legitimacy. hal#282: a RED agent deleted 2407 lines (77%) of an
out-of-scope test file. bd#223 turned the GH282 guard ON, but that guard fires only on `deleted >= 120 AND >= 50%`
per file. **Gap:** (a) a whole test file deleted below 120 lines, (b) test functions removed from a file that is
authorized, (c) skip/xfail markers added to existing tests, (d) no aggregate count. Nothing is removed.

## §1 Decision

1. New `lib/test_integrity.py`, pure + git read only (via `git_port.git_read`):
   `compute_test_integrity(base_sha, git_cwd, *, is_authorized, has_pragma) -> dict` with keys
   `deleted_files: list[str]`, `removed_tests: list[{path, names}]`, `added_skips: list[{path, n}]`, `exempted: list[{path, kind}]`, `skipped_files: list[{path, reason}]`, `skip_reason: str|None`. Returned lists are always the raw findings (telemetry keeps counts); thresholds are applied by the caller.
   Enumeration is its own: `git diff --name-status --no-renames <base>` (base vs worktree, so staged and unstaged
   edits count), kept when `_is_test_path(path)` and not `_is_fixture_only_path(path)` (conftest.py / __init__.py are not tests). Untracked new files are not deletions and are ignored for D/M, but
   count as destination of a moved test (see 3).
2. Test definitions (a fixed set of regexes, per line, chosen by file extension): py `def test*` / `async def test*`; js/ts `it(` `test(`
   with a string title; go `func Test*`; bats `@test "..."`. Skip markers: `pytest.mark.skip` (incl. skipif), `pytest.mark.xfail`,
   `pytest.skip(`, `importorskip`, `skipTest(`, `unittest.skip`, `.skip(`, `xit(`, `xdescribe(`, `t.Skip(`, `SkipNow(`; bats `skip` as a command only in `*.bats` files.
3. `removed_tests` for a path = names defined at base in an **M** file (never a D file: a deleted file's tests are
   not double-counted) minus names defined in ANY post-RED changed-or-new test file. Renaming a test function is a
   removal of the old name (policy, AC6a). A deleted file is **a move, not a deletion** when every test name it
   defined at base is defined in some other post-RED changed-or-new test file (`git mv`, AC6c); a deleted file with no
   test names at base is still a deletion. `added_skips` for a path = (skip markers post) - (at base), when positive
   (net per file, documented limit: adding one skip and removing another in the same file cancels).
4. Exemptions: a deleted file is exempt iff `is_authorized(path)` (spec `authorized-test-edits:`; a deleted file
   cannot carry a pragma). A modified file's `removed_tests` and `added_skips` are exempt iff `has_pragma(path)`
   (the existing `# red-mass-deletion: allow` token) **and only if that token already exists in the file at `base_sha`**. The base check lives in the CALLER (`_commit_red_tests` builds `has_pragma` as: token at `base_sha` AND still present post-RED); the module trusts the callback; a pragma the RED itself adds in this cycle exempts nothing here (fail-closed read; GH282's own pragma behaviour is unchanged and out of scope). Exempt entries are listed in
   telemetry as `exempted`, never counted.
5. Thresholds (aggregate over non-exempt entries; violation iff ANY is exceeded): `HAL_RED_TEST_INTEGRITY_MAX_DELETED_FILES`=0,
   `HAL_RED_TEST_INTEGRITY_MAX_REMOVED_TESTS`=0, `HAL_RED_TEST_INTEGRITY_MAX_ADDED_SKIPS`=0 (kind `int`).
6. Flags: `HAL_RED_TEST_INTEGRITY_GATE` (kill-switch, default `"1"`: `=0` disables detection and telemetry entirely)
   and `HAL_RED_TEST_INTEGRITY_ENFORCE` (kill-switch, default `"1"`: `=0` is warn-only). Descriptions carry no
   `flip-by|kill-by|retire-by` token and no ledger line is added (default ON from day one: the evidence for ON is
   the incident, and a shadow period would recreate the stale-ENFORCE problem the audit found).
7. `_commit_red_tests`: after the GH282 block and before GH1600 D1, call the module, emit `red_test_integrity_check`
   (`violations`, `violations_n`, `exempted`, `skipped_files`, `thresholds`, `enforced`, `skip_reason`); when violations and enforced,
   emit `red_test_integrity_blocked` (severity error) and return `StepResult(status="error",
   error_code="E_RED_TEST_INTEGRITY", recoverable=False)` with an error text naming each path and counts, and the
   remedy ("add the path under `authorized-test-edits:` (deleted file) or the `# red-mass-deletion: allow` pragma
   (modified file)"). Fail-open is global ONLY when listing the changed files fails (`skip_reason` set). A file that cannot be read or decoded (binary, bad UTF-8) is skipped and recorded in `skipped_files`, except that a DELETED file whose base content cannot be decoded still counts as a deletion (a move needs readable names); it never drops the findings of other files. An exception in an exemption callback counts the file as NOT exempt (fail-closed).
8. `E_RED_TEST_INTEGRITY` registered in `error_codes.py` and both `ERROR_CODES.md` copies.
9. Host RED prompt line (the existing "MASS-DELETION (GH282, terminal)" bullet) gets one sentence: deleting
   test files, removing tests or adding skip/xfail to existing tests is blocked the same way.

## §2 Acceptance checks (real `_commit_red_tests` on a tmp git repo, as the GH282 tests do; `_emit_safe` wrapped only to observe)

- **Shield note:** AC7 (GATE=0) and AC10a pass before GREEN by design (regression shields), the rest must fail first.
- **AC1** catalog: the 5 new keys exist; the two gates are kind `gate` default `"1"`; the three thresholds kind `int` default 0; none of the descriptions contains a horizon token; `E_RED_TEST_INTEGRITY` is in `error_codes`.
- **AC2 (production side-effect)** base has a 40-line test file containing real `def test_*` functions; RED deletes it from the tree (no authorization) -> status `error`, `error_code == "E_RED_TEST_INTEGRITY"`, `recoverable is False`, event `red_test_integrity_blocked` emitted, error text names the path. (GH282 does not fire: 40 < 120.)
- **AC3** the same deletion with the path under `authorized-test-edits:` in the frozen spec -> not blocked, `exempted` lists the path.
- **AC4** a pre-existing test file with 5 test functions is edited, 2 removed, the file carries `# red-mass-deletion: allow` **committed at base** -> not blocked; the same edit where RED itself adds the pragma (absent at base) -> blocked (self-added pragma exempts nothing); same file WITHOUT the pragma and authorized via `authorized-test-edits:` -> blocked (authorization to edit is not authorization to remove tests), `removed_tests` names the 2.
- **AC5** an existing test gains `@pytest.mark.skip` (authorized, no pragma) -> blocked, `added_skips` has n == 1; with `HAL_RED_TEST_INTEGRITY_MAX_ADDED_SKIPS=1` -> not blocked.
- **AC6 (no false positive / policy)** (a) RED only adds a new test file -> `violations_n == 0`, `enforced: true`; (b) renaming a test function inside an authorized file without a base pragma -> blocked (rename = removal); (c) `git mv` of a whole test file with real tests, names unchanged -> not a deletion, `violations_n == 0`; (d) a test moved from file A to new file B (A shrinks, B has the same names) -> `violations_n == 0`.
- **AC7** `HAL_RED_TEST_INTEGRITY_ENFORCE=0` with the AC2 fixture -> no `E_RED_TEST_INTEGRITY`, check event has `enforced: false`, `violations_n >= 1`. `HAL_RED_TEST_INTEGRITY_GATE=0` -> no check event at all.
- **AC8** aggregate: with `MAX_DELETED_FILES=2`, two deleted unauthorized files pass, three block.
- **AC9 (unit, pure module)** `compute_test_integrity` on a tmp repo returns the documented keys for a deleted file, a removed test, an added skip; `skip_reason` is `"no_base_sha"` for an empty base and the result is fail-open (empty lists) on a bad base SHA.
- **AC11 (fail-open scope)** an unauthorized deletion of a real test file plus a modified non-UTF-8 binary under `tests/` -> still `E_RED_TEST_INTEGRITY`; the binary is listed in `skipped_files`. A failing changed-file listing (bad base SHA) -> no block, `skip_reason` set.
- **AC12** fixture-only paths: deleting `tests/conftest.py` or `tests/__init__.py` alone is not a violation.
- **AC13** both `ERROR_CODES.md` copies carry `E_RED_TEST_INTEGRITY`, and the MASS-DELETION prompt bullet in `phase_5_implement.py` mentions skip/xfail and deleted test files.
- **AC10 (ordering)** an unauthorized RED mass-deletion of 200/300 lines still returns `E_RED_MASS_DELETION` (GH282 keeps precedence), and an unauthorized small-file deletion returns `E_RED_TEST_INTEGRITY` not the GH1600 D1 error.

## §3 Edge cases / siblings (§1a)

Siblings that drive `_commit_red_tests` with deletions or edits of pre-existing test files:
`test_gh282_red_mass_deletion.py`, `test_gh282_pragma_escape.py`, `test_gh1600_red_tests_in_existing_file.py`,
`test_bd_flip_red_mass_deletion.py`. Sweep result (251 passed, 23 skipped over the 4 files plus flag/error-code/horizon tests, 2026-10-03, before GREEN edits on siblings): none needs an edit; the full set of 31 `_commit_red_tests` callers is covered by CI's full-suite delta.
(Original sweep instruction:) fixtures must be swept before
freeze; any that deletes a sub-120-line test file or removes a test def without the pragma sets
`HAL_RED_TEST_INTEGRITY_GATE=0` explicitly (authorized below). GH1600 D1 cases that expect the D1 error on a
modified existing file keep working: modification without removed tests or added skips adds no violation.

## §4 Files in scope / NOT in scope

In scope: `lib/test_integrity.py` (new), `workflows/phase_5_implement.py` (one block + one prompt sentence),
`flags_catalog.py` (5 entries), `error_codes.py`, `ERROR_CODES.md` (both copies), new test
`engine_py/tests/test_bd_red_test_integrity.py`.
authorized-test-edits: none (the sweep showed no sibling needs an edit).
**NOT in scope:** the GH282 function and its threshold, the GREEN side (E_LOT_TEST_READONLY / final-diff integrity:
follow-up reusing `compute_test_integrity`), the host repo, the other `*_ENFORCE` flags (separate PR).

## §5 Baseline (§1b) and known limits

Thresholds are structural zeros, not measured rates, so no live baseline applies; the GH282 events measure a different check. Known limits, accepted: a moved test file is reported in `exempted` as `kind: "moved"`, and a RED can delete a test file and redefine the same names as stubs in a new file (a move by the name rule; same-name-stub limit); a `git mv` still needs `authorized-test-edits:` for GH1600 D1; a same-name stub in another changed file can hide a removed test (names are matched across files); net-per-file skip count; a type change to a symlink is not seen; uncommitted operator changes made before RED are attributed to RED (same as GH282); test files outside recognized test paths are not seen. Enforcement is ON from day one; `=0` kill-switches exist.
