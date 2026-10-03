# bd#226 RED test-integrity gate: validation gate r1 (spec + RED audit)

**Audited:** spec `docs/decisions/2026-10-03-bd-red-test-integrity.md`; RED `engine_py/tests/test_bd_red_test_integrity.py` (19 tests).
**Read site:** `engine_py/bytedigger_engine/workflows/phase_5_implement.py` `_commit_red_tests`: AD14A3ED scope gate (2334-2392), GH282 (2393-2437), bd#226 block (2438-2497), GH1600 D1 (2500-2530).
**GREEN already in tree** (`engine_py/bytedigger_engine/lib/test_integrity.py` + the block above). The spec and RED are judged on their own merits. GREEN defects are side notes at the end.
**Tree hygiene:** this file is the only write. It is the output path the orchestrator gave, so `git status --porcelain` will show exactly this one untracked file.

## Step 1: spec-internal consistency (literal-token drift)

- These tokens are spelled the same in spec, RED and prod: `HAL_RED_TEST_INTEGRITY_{GATE,ENFORCE,MAX_DELETED_FILES,MAX_REMOVED_TESTS,MAX_ADDED_SKIPS}`, `E_RED_TEST_INTEGRITY`, `red_test_integrity_check`, `red_test_integrity_blocked`, `# red-mass-deletion: allow`, `authorized-test-edits:`. No name drift.
- **Return-contract drift.** §1.1 says the result has 4 keys (`deleted_files, removed_tests, added_skips, skip_reason`). §1.4 and §1.7 also need `exempted` in telemetry, and §1.1 does not say where it comes from. GREEN added a 5th key. Finding 5.
- **§1.3 vs RED AC9.** §1.3 defines `removed_tests` as "names defined at base in a **D/M** file minus …". Under that text, a deleted file's tests also count as removed. RED `test_ac9_unit_documented_keys…` asserts `removed_tests == [{tests/test_shrunk.py…}]` only, so the deleted `test_gone.py`'s `test_g1` must NOT be counted. Spec and RED contradict each other. Finding 2.
- **§1.3 rename claim.** §1.3 says "a move or rename of a test … is not a removal". The mechanism (a set difference of names) counts any renamed function as removed. Whole-file renames go through `--no-renames`, so they become a D entry and count as a deleted file. The claim is false for both. Finding 3.
- **Gap (b) vs §1.4.** The provenance section lists as a gap "test functions removed from a file that is authorized/**pragma'd**". §1.4 then exempts pragma'd files from `removed_tests` and `added_skips`. The pragma'd half of gap (b) stays open. Finding 1.
- §4 `authorized-test-edits: any of the four sibling files above that the sweep shows…` is conditional prose, not concrete paths. Finding 12.

## Step 1.5: rule-overlap simulation (dispatcher order: scope gate → GH282 → bd#226 → D1)

The scope gate never returns in any fixture (`enforce_red_scope_allowlist` defaults to False).

| AC | GH282 | bd#226 | D1 | First match / outcome | Matches AC? |
|---|---|---|---|---|---|
| AC2 40-line unauth delete | 40<120, no | D → deleted_files=1 > 0 → block | not reached | E_RED_TEST_INTEGRITY | yes |
| AC3 authorized delete | no | exempted | authorized → skip | passes the gate | yes, but the fixture has 0 test defs (F2) |
| AC4a pragma+auth, −2 defs | ~8 lines, no | removed {d,e}, pragma → exempted | authorized → skip | not blocked | yes |
| AC4b auth, no pragma | no | removed {d,e} → block | — | E_RED_TEST_INTEGRITY | yes |
| AC5 +skip / MAX=1 | no | +1 > 0 → block; with MAX=1, 1 is not > 1 | — | block / pass | yes |
| AC6a rename + pragma | no | `test_b` counted as removed, then exempted **by the pragma** | authorized | 0 violations | passes for the wrong reason (F3) |
| AC6b move A→new B | no | post_names includes B's names → none removed | A authorized, B new | 0 | yes |
| AC7a ENFORCE=0 | no | over, not enforced | may fire on `tests/test_old.py` if deleted paths are in red_test_paths | not integrity | yes (see G6) |
| AC7b GATE=0 | no | skipped | as today | no events | yes |
| AC8 2/3 files, MAX=2 | no | 2 is not > 2 / 3 > 2 | — | pass / block | yes |
| AC10a 300→100 | 200≥120 and 200·2≥300 → block | not reached | — | E_RED_MASS_DELETION | yes |
| AC10b | no | block | not reached | E_RED_TEST_INTEGRITY | yes |

Extra cases outside the AC table:
- **Unauthorized** modified file, RED adds the pragma, 2 defs removed, less than 120 lines deleted: bd#226 exempts it, then D1 blocks it (not authorized, not in `_mdl_exempted`). D1 backstops this case.
- **Unauthorized** file, more than 120 lines and at least 50% deleted, RED adds the pragma: GH282 exempts it, bd#226 exempts it (pragma), and D1 skips it because the path is in `_d1_exempted`. **All three gates are bypassed by one comment the audited agent writes itself** (F1).
- **Authorized** file, RED adds the pragma and removes tests: bd#226 exempts it and D1 skips it (authorized). This defeats AC4's stated rationale, "authorization to edit is not authorization to remove tests" (F1).

## Step 2: decision paths vs ACs (spec §1 producing paths ↔ spec §2 ACs)

| §1 path | AC |
|---|---|
| block + `red_test_integrity_blocked` + StepResult(recoverable=False) | AC2, AC4b, AC5, AC8 |
| exempt → `exempted` | AC3, AC4a |
| warn-only | AC7a |
| gate off | AC7b |
| fail-open (`skip_reason`) | AC9, **unit only**: no `_commit_red_tests`-level AC shows `skip_reason` in the check event (F14) |
| ordering | AC10 |
| §1.8 ERROR_CODES.md (both copies) | **no AC** (F11). Both copies do contain the entry. |
| §1.9 RED prompt sentence | **no AC** (F11) |

No AC asserts a post-condition without a producing path. Two producing ops (§1.8, §1.9) have no AC.

## Step 3: RED adequacy

- The UUT is not mocked. Only `_emit_safe` is wrapped, and the real `_commit_red_tests` runs on a real tmp git repo. Unit tests call the real `compute_test_integrity`. No stub-passability.
- Collection is clean: the module under test is imported inside the test bodies, with no `sys.path` hacks. Before GREEN: AC1 fails on KeyError, AC9 fails on ImportError, and AC2-AC6, AC7a, AC8 and AC10b fail on asserts.
- **Pass before GREEN:** AC7b (no events exist yet) and AC10a (GH282 already blocks). Both are valid regression shields but are not labelled as such. The AC7b comment ("Discriminator vs today…") is misleading (F10).
- **Non-discriminating fixtures:** AC2/AC3/AC7/AC8 use `_lines(40)` (`L0..L39`), which has no test definitions. So no AC checks how an authorized or unauthorized deletion of a file that *contains tests* interacts with `removed_tests` (F2). AC6a passes only because of the pragma (F3).
- AC5 does not assert `added_skips` n=1, which the spec requires (F9).

## Step 4: reachability (§1y Point → Host → Test)

- **Blocked return:** Point `phase_5_implement.py:2490-2496` → Host `_commit_red_tests` → Test `test_ac2_…`, `test_ac4_…blocks…`, `test_ac5_added_skip_blocks`, `test_ac8_three…`, `test_ac10_small…`. Reached.
- **Events:** check event at :2473, blocked event at :2481 → `_commit_red_tests` → AC3/4/5/6/7 (check) and AC2 (blocked). Reached.
- **Detection:** `lib/test_integrity.py:91-166` → `compute_test_integrity`, called from :2452 → AC9 unit tests plus every integration AC. Reached.
- **Fail-open:** `test_integrity.py:88-89, 95-96, 167-168` → AC9 unit tests only.

## Adversarial edges (not covered by any §2 AC)

1. **Binary / non-UTF-8 bypass.** `git_port.git_read` decodes with `text=True` and strict UTF-8. If RED modifies any non-UTF-8 file under a test path (for example `tests/fixtures/x.png`), `git show base:path` raises UnicodeDecodeError. The module's catch-all then returns `integrity_failed` and **drops every finding, including unrelated deleted test files**. One touched binary fixture silently disables the whole gate.
2. **Pragma self-authorization.** RED writes `# red-mass-deletion: allow` itself (see Step 1.5 extra cases).
3. **Whole-file rename/move** (`git mv tests/test_a.py tests/test_a2.py`, or delete plus a new untracked copy). Every name survives in the destination, yet the D entry is still a terminal `deleted_files` violation.
4. **Replace a test with a same-name stub.** `post_names` is the union over all changed and untracked test files, not qualified by file or class. RED can delete a real `test_x` from file A and add `def test_x(): assert False` to its new RED file, and this is not a removal. Same-name methods in different `Test*` classes also collapse into one name.
5. **Non-test files under `tests/`.** `_is_test_path` returns True for any segment named `tests`/`test`/`__tests__`. Deleting `tests/fixtures/old.json`, `tests/conftest.py`, `tests/__init__.py` or `tests/README.md` is therefore a terminal "deleted test file". The D1 enumeration excludes conftest/`__init__` through `_is_fixture_only_path`; this gate does not.
6. **Skip-count false positives and negatives.** Counting is the net per file. A new RED test with `xfail` in an authorized existing file counts as a skip "added to existing tests". The bats pattern `^\s*skip(\s|$)` matches the Python line `skip = …`. All patterns apply to all languages, and comments and docstrings count. Adding one skip while removing a pre-existing one cancels out. `self.skipTest(`, `pytest.importorskip`, `t.SkipNow()` are not detected.
7. **Typechange evasion.** Replacing a test file with a symlink (status `T`) is ignored, because only D/M are handled.
8. **Cycle ≥2 re-entry with a persisted `pre-red-ref.txt`.** No test runs the gate where the base is the frozen cycle-1 SHA and the cycle-1 RED commit has already landed. Example: deletions committed under ENFORCE=0 in cycle 1 must be re-blocked in cycle 2, and RED's own cycle-1 files must stay status A and not count as M.
9. **Dirty tree before RED.** In cycle 1 the base is HEAD, so the operator's uncommitted deletions or edits under tests/ that existed before RED are blamed on RED.
10. **Test files outside the classifier.** `*.spec.ts`, `*.bats` and `*.test.py`-style files outside a tests dir are invisible because `_is_test_path` returns False.

## Findings

### MAJOR (block)

1. **The pragma exemption can be authored by the audited agent and contradicts gap (b).** §1.4 exempts `removed_tests`/`added_skips` when the post-RED file contains `# red-mass-deletion: allow`, and RED can write that token in the same diff. AC4's own fixture adds the pragma in the RED edit. This (a) leaves the pragma'd half of the declared gap (b) open, (b) turns AC4's "authorization to edit ≠ authorization to remove tests" into an opt-out for authorized files, and (c) together with GH282's pragma exemption lets an unauthorized file with more than 120 lines and at least 50% deleted skip GH282, bd#226 and D1 all at once. **Fix (spec):** honour the pragma only if it is present at `base_sha` (operator-authored, read via `git show base:path`), or require a spec-side signal (`authorized-test-edits:` plus a reason or a dedicated list). Alternatively, explicitly accept the risk and strike "pragma'd" from gap (b). Add an AC: RED adds the pragma to an authorized file and removes 2 defs → blocked.
2. **§1.3 "D/M file" contradicts RED AC9, and the deletion fixtures have no tests in them.** Read literally, the spec counts a deleted file's test names as `removed_tests`. That double-counts, and because a deleted file "cannot carry a pragma" (§1.4), **every authorized deletion of a real test file would still block**, which contradicts AC3. RED AC9 pins the opposite: D-file names are not counted. AC2/AC3 use `L0..L39` content, so neither reading is exercised on realistic input. **Fix:** change §1.3 to "M file" (the D-entry's names are covered by `deleted_files`), and give the AC2/AC3 fixtures real `def test_*` content.
3. **The "rename is not a removal" claim is false, and AC6a cannot detect it.** An in-file function rename (`test_b` → `test_b_renamed`) is counted as removal of `test_b`. AC6a passes only because its file carries the pragma. A whole-file rename or move becomes `D` plus `A`/untracked under `--no-renames`, which is a terminal `deleted_files` violation, even though §1.1 says untracked files "count as destination of a moved test". **Fix:** either (a) drop "rename" from §1.3 and make AC6a pragma-free, asserting that an in-file rename IS a removal; or (b) define "a D entry whose base names are all ⊆ post_names (and is non-empty) is a move, not a deleted file", with an AC for `git mv`. State the chosen policy.
4. **Fail-open is all-or-nothing and can be triggered by RED (binary files).** §1.7 says "Fail-open on any git error" without saying at what level. Combined with strict UTF-8 decoding in `git_read`, touching one non-UTF-8 test-path file zeroes all findings (adversarial edge 1). An `untracked_failed` result or an exception in a callback does the same. **Fix (spec):** fail open globally only when enumeration fails (`diff`, `ls-files`). When reading or decoding one file fails, skip that file and record it (`skipped_paths`). Add an AC: unauthorized deleted test file plus a modified binary under `tests/` → still E_RED_TEST_INTEGRITY.

### MINOR (advisory)

5. §1.1 return contract omits `exempted`. Add it as a documented key and assert it in AC9.
6. `deleted_files` uses `_is_test_path`, which covers every file under `tests/` (fixtures, conftest, `__init__`, data, docs). Decide: exclude `_is_fixture_only_path` and non-test files (for example, require ≥1 test definition at base), or accept this and document it.
7. Skip-marker semantics: net per file, patterns not scoped by language, `skip = …` false positive, missing `skipTest`/`importorskip`/`SkipNow`, and add/remove cancellation (adversarial edge 6). Either scope "added to existing tests" to base test names or reword gap (c).
8. Name matching is a union over all changed files and is not qualified by file or class, so a same-name stub hides a removal (adversarial edge 4). At least document it as a known limit.
9. AC5 does not assert `added_skips[0]["n"] == 1`.
10. AC7b and AC10a pass before GREEN. Label them as regression shields and reword the AC7b comment.
11. §1.8 (both ERROR_CODES.md copies) and §1.9 (RED prompt sentence) have no AC (§1w op ↔ AC).
12. §3 names 4 siblings, but 31 test files drive `_commit_red_tests`, and the gate scans the whole repo diff, not `red_test_paths`. No sweep result is recorded. §4's `authorized-test-edits:` line is conditional prose. Record the `--require-clean` sweep output and list concrete paths (or "none").
13. The thresholds of 0 and day-one ENFORCE have no replay of *this* detector over historical RED diffs (§1b). The GH282 telemetry (232 runs) measures a different predicate.
14. No integration AC for the fail-open path (`skip_reason` present in `red_test_integrity_check`, no block).

### GREEN side notes (not blocking this gate; fix when GREEN is revised)

- G1: The UnicodeDecodeError bypass in finding 4 is real in `test_integrity.py:147-168`. `except Exception` wraps the whole loop.
- G2: Below the threshold, `violations` is reported as empty lists (`phase_5_implement.py:2468-2472`). In the AC5 MAX=1 run, the observed skip does not appear in telemetry at all. Add an `observed` field.
- G3: An exception in `is_authorized`/`has_pragma` fails the whole gate open (same catch-all). `untracked_failed` also drops deletions already found.
- G4: The bats `skip` regex is applied to Python/JS files (adversarial edge 6).
- G5: The `scratchpad` re-resolution at :2499 is redundant but harmless.
- G6: If deleted paths are part of `red_test_paths`, then with ENFORCE=0 a deletion falls through to D1's default message ("RED wrote new tests into pre-existing … a new test file is required"), which gives the wrong remedy for a deletion. This is a pre-existing D1 issue that this gate's warn-only mode now exposes.

VERDICT: REJECTED

MAJOR: 1 (pragma self-authorization / gap-(b) contradiction), 2 (§1.3 D/M vs RED AC9, fixtures without tests), 3 (rename claim false, AC6a non-discriminating), 4 (all-or-nothing fail-open, binary bypass). MINOR: 5-14. GREEN side notes: G1-G6.
