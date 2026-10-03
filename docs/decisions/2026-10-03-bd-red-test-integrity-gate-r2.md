# bd#226 RED test-integrity gate: validation gate r2 (spec r2 + RED r2 re-audit)

**Audited:** spec r2 `docs/decisions/2026-10-03-bd-red-test-integrity.md`; RED r2 `engine_py/tests/test_bd_red_test_integrity.py` (26 test items, AC12 parametrized x2). Baseline is the r1 report `docs/decisions/2026-10-03-bd-red-test-integrity-gate-r1.md`.
**Read sites:** `phase_5_implement.py` `_commit_red_tests` (disk-truth 2251-2333, scope gate 2334-2392, GH282 2393-2437, bd#226 2438-2497, GH1600 D1 2500-2530), `_is_fixture_only_path` :531, `_resolve_frozen_pre_red_sha` :595, `_red_mass_deletion_violations` :2076, `_path_dirty_relative_to_head` :2143, `lib/test_integrity.py` (r1 GREEN), `lib/util/path_classifier.py`, `lib/plugins/disk_truth/git_diff.py`.
**GREEN is not judged.** The in-tree GREEN is r1 and is only used to check that RED r2 fails for the right reasons.
**Tree hygiene:** this file is the only write.

## Finding ledger (r1 → r2)

| # | r1 finding | Status | Evidence |
|---|---|---|---|
| 1 | MAJOR: the pragma can be self-authored, which contradicts gap (b) | **RESOLVED** | §1.4: the pragma exempts only if the token "already exists in the file at `base_sha`", and "a pragma the RED itself adds in this cycle exempts nothing". Provenance gap (b) no longer says "pragma'd". AC4 adds the self-added case. RED `test_ac4_pragma_added_by_red_itself_exempts_nothing` asserts E_RED_TEST_INTEGRITY with `test_d`/`test_e` in violations, and fails on r1 GREEN, which reads the post-RED file. Caveat on the locus of the check: see N5. |
| 2 | MAJOR: §1.3 "D/M" contradicts AC9; fixtures had no tests | **RESOLVED** | §1.3 now says "an **M** file (never a D file: … not double-counted)". AC9 asserts `test_g1` is absent from `removed_tests`. `_real_test_file_40()` holds 3 real `def test_*` (with a 40-line self-assert) and is used by AC2/AC3/AC7/AC10b/AC11. AC8 fixtures carry 2 defs per file. AC3 now proves that an authorized deletion of a file with real tests is exempt. |
| 3 | MAJOR: the "rename is not a removal" claim was false; AC6a was non-discriminating | **RESOLVED** | §1.3 policy: an in-file rename IS a removal, and `git mv` of a whole file is a move. AC6b is pragma-free and asserts a block with `test_b` in `removed_tests`. AC6c uses a staged `git mv` with no authorization and asserts `violations_n == 0`, which fails on r1 GREEN (terminal D). Pipeline-level caveat: see N2. |
| 4 | MAJOR: fail-open was all-or-nothing (binary bypass) | **RESOLVED** | §1.7: the gate fails open globally "ONLY when listing the changed files fails". An unreadable or undecodable file goes to `skipped_files`, and an exemption-callback exception is fail-closed. AC11 unit and AC11 integration (binary under `tests/fixtures/` plus an unauthorized real-test deletion → still E_RED_TEST_INTEGRITY) both fail on r1 GREEN. `_is_fixture_only_path` matches only the basename, so `blob.bin` stays in scope and the fixture is a real discriminator. Caveats: N3, N4, N6. |
| 5 | MINOR: `exempted` was missing from the return contract | **RESOLVED** | §1.1 now lists 5 keys including `exempted`. AC9 asserts `exempted == []`. A new key, `skipped_files`, has the same kind of drift: N3. |
| 6 | MINOR: `deleted_files` covers non-test files under `tests/` | **PARTIAL** | conftest/`__init__` are excluded (§1.1, AC12 x2). §1.3 adds that "a deleted file with no test names at base is still a deletion", so deleting `tests/fixtures/data.json`, `tests/helpers.py` or `tests/README.md` stays a terminal violation at threshold 0. That reads as a choice, but §5 does not list it as an accepted limit, and no AC pins it (adversarial edge E3). |
| 7 | MINOR: skip-marker semantics | **PARTIAL** | §1.2 adds `importorskip`, `skipTest(`, `SkipNow(`, and limits bats `skip` to `*.bats`. §1.3/§5 document the net-per-file cancellation. No AC pins any new pattern or the `*.bats` scoping (the r1 `skip = …` false positive). A new RED test with `xfail` inside an authorized existing file still counts as "added to existing tests", and that is not listed in §5. |
| 8 | MINOR: same-name stub hides a removal | **RESOLVED (documented)** | §5 now lists it as a known limit. The r2 move rule extends this hole to whole-file deletions: N1. |
| 9 | MINOR: AC5 did not assert n == 1 | **RESOLVED** | `assert skips[0]["n"] == 1`. |
| 10 | MINOR: shields were unlabelled | **RESOLVED** | §2 shield note plus the RED docstring/comments on AC7 GATE=0 and AC10a. The RED docstring also flags AC13 as a shipped-state guard. |
| 11 | MINOR: §1.8/§1.9 had no AC | **RESOLVED** | AC13 checks both ERROR_CODES.md copies and the prompt bullet (`phase_5_implement.py:1394-1396`, which already contains the sentence). |
| 12 | MINOR: no sweep record; conditional `authorized-test-edits` | **RESOLVED** | §3 records the sweep (251 passed / 23 skipped) and §4 reads `authorized-test-edits: none`. The sweep was taken under r1 semantics, but the only r2 tightening (pragma must be at base) cannot affect the 4 siblings: every sibling pragma fixture (`test_gh282_pragma_escape.py:203-206`, `test_gh1600…:643-647, 711-718`, `test_bd_flip…:100-103`) deletes only `L<n>` lines or appends. No test def is removed and no skip is added. |
| 13 | MINOR: no §1b replay | **RESOLVED (by decision)** | §5 argues that the thresholds are structural zeros, not measured rates. Accepted. The residual false-positive risk is concentrated in item 6. |
| 14 | MINOR: no integration AC for fail-open | **RESOLVED** | `test_ac11_bad_base_sha_sets_skip_reason_and_does_not_block`. The base is a non-existent 40-hex SHA via `pre-red-ref.txt`. `git_diff_files` does not raise on a non-zero git exit (`git_diff.py:21-22, 67-85`), so `red_test_paths` stays non-empty (untracked `test_new_red.py`), GH282 fails open (`numstat_failed`), and the bd#226 block is reached. The test is reachable. |

r1 GREEN side notes: G1/G3 are now spec-mandated through §1.7 and covered by AC11. G4 is spec-mandated by §1.2 but has no AC. G2 is still open (telemetry shows empty `violations` below the threshold). G6 is now material: see N2.

## Step 1: spec-internal consistency (literal-token drift)

- `HAL_RED_TEST_INTEGRITY_{GATE,ENFORCE,MAX_DELETED_FILES,MAX_REMOVED_TESTS,MAX_ADDED_SKIPS}`, `E_RED_TEST_INTEGRITY`, `red_test_integrity_check`, `red_test_integrity_blocked`, `# red-mass-deletion: allow`, `authorized-test-edits:`, `skip_reason`, `exempted`: the spelling is the same in spec, RED and prod. No drift.
- **`skipped_files`** appears in §1.7 and AC11 and is asserted as a documented key in RED AC9 (`for key in (… "skipped_files")`). It is **missing from the §1.1 key list** ("with keys deleted_files, removed_tests, added_skips, exempted, skip_reason") and from the §1.7 check-event field list. This is the same class as r1 F5 (N3).
- §1.4 "`has_pragma(path)` … **and only if that token already exists in the file at `base_sha`**" does not say whether the base check lives in the module or in the caller's callback. RED AC9 (`test_ac9_unit_exemptions_via_callbacks`) passes `has_pragma=lambda p: p == "tests/test_shrunk.py"` for a file whose base carries no pragma, and expects it to be exempt. So RED fixes the locus to the **caller callback**, and the spec should say so (N5).
- Cosmetic: AC10 is listed after AC13.

## Step 1.5: rule-overlap simulation (scope gate → GH282 → bd#226 → D1)

The scope gate never returns (`enforce_red_scope_allowlist` is unset). Each row runs the r2 spec semantics.

| Test | GH282 | bd#226 (r2 spec) | D1 | Outcome | Matches |
|---|---|---|---|---|---|
| AC2 / AC10b | 40 < 120 | D, unauth, names {old_one,two,three} not in post (only `test_brand_new_red`) → deletion → block | not reached | E_RED_TEST_INTEGRITY | yes |
| AC3 | no | D, authorized → exempted | authorized → skip | not integrity | yes |
| AC4 base pragma | 8 lines | M, removed {d,e}, pragma at base → exempted | authorized | not integrity | yes |
| AC4 self pragma | 8 lines | removed {d,e}, no pragma at base → block | — | E_RED_TEST_INTEGRITY | yes |
| AC4 no pragma | no | block, names d,e | — | E_RED_TEST_INTEGRITY | yes |
| AC5 / MAX=1 | no | +1 skip > 0 → block; 1 is not > 1 → pass | authorized | block / pass | yes |
| AC6a | no | test_old.py unchanged; only the new file is untracked | — | 0 | yes |
| AC6b | no | M, `test_b` not in post (`test_b_renamed` ≠ `test_b`) → block | — | E_RED_TEST_INTEGRITY | yes |
| AC6c | 8 < 120 | D `test_a.py`, {one,two} ⊆ names of A `test_a2.py` → move → 0 | **`tests/test_a.py` exists at frozen SHA, dirty, unauthorized → D1 refuses** | **E_RED_TESTS_IN_EXISTING_FILE** | gate yes; pipeline: N2 |
| AC6d | no | M A loses {two,three}, both in untracked B → 0 | A authorized; B new | 0 | yes |
| AC7a | no | over, not enforced | D1 refuses `tests/test_old.py` (r1 G6) | not integrity | yes (test asserts only `!=`) |
| AC8 2/3 | no | 2 is not > 2 / 3 > 2 | — | pass / block | yes |
| AC10a | 200 ≥ 120 and 400 ≥ 300 → block | — | — | E_RED_MASS_DELETION | yes |
| AC11 int | blob numstat `-` → skipped | D deletion kept; blob → `skipped_files` | — | E_RED_TEST_INTEGRITY | yes |
| AC12 | no | conftest/`__init__` filtered | not in `red_test_paths` | 0 | yes |

## Step 2: §1 producing paths ↔ §2 ACs

| §1 path | AC |
|---|---|
| block + event + `recoverable=False` | AC2, AC4b/c, AC5, AC6b, AC8, AC10b, AC11 |
| exempt: authorized D / base pragma | AC3, AC4a, AC9 |
| move rule (D ⊆ post names) | AC6c |
| fixture-only filter | AC12 |
| per-file skip → `skipped_files` | AC11 (unit) |
| global fail-open `skip_reason` | AC9, AC11 (integration) |
| warn-only / gate-off | AC7 |
| ordering | AC10 |
| §1.8/§1.9 docs and prompt | AC13 |
| **exemption-callback exception → not exempt (fail-closed)** | **no AC** (N6) |
| **"a deleted file with no test names at base is still a deletion"** | **no AC** (E3) |

No AC asserts a post-condition without a producing path. Two producing clauses have no AC (MINOR).

## Step 3: RED adequacy

- The UUT is never mocked. Only `_emit_safe` is wrapped. Real `_commit_red_tests` and real git repos are used. The module is imported inside the test bodies, so collection is clean and nothing touches `sys.path`.
- Fail-for-the-right-reason against r1 GREEN: `test_ac4_pragma_added_by_red_itself…` (post-file pragma), `test_ac6c…` (terminal D), `test_ac9_unit_documented_keys…` (no `skipped_files`), `test_ac11_unit…` and `test_ac11_binary…` (strict decode → catch-all), and `test_ac12…` x2 (`tests/conftest.py` / `__init__.py` counted). That is 7 items, matching the orchestrator's expected RED count. All 7 fail at assert time, none at collection.
- Tests that pass on r1 GREEN and pin behaviour r1 already has (AC2/3/5/6b/8/9-other/10b) were RED against "no GREEN" in round 1. Acceptable.
- Weak but not vacuous: AC6b uses the substring check `"test_b" in json.dumps(removed_tests)` (it would also match `test_b_renamed`). `status == "error"` is asserted next to it, so it still discriminates.
- AC9 exemption test asserts `test_s2` inside `exempted`. The spec does not say that exempt entries carry names (slight over-pinning; r1 GREEN already does it).

## Step 4: reachability (§1y Point → Host → Test)

- Block: `phase_5_implement.py:2490-2496` → `_commit_red_tests` → AC2/4/5/6b/8/10b/11. Reached.
- Check event `:2473` / blocked event `:2481` → `_commit_red_tests` → AC3/4/5/6/7/11/12 and AC2. Reached.
- Detection: `lib/test_integrity.py` `compute_test_integrity` → called at `:2452` → AC9/AC11 unit and all integration tests. Reached.
- Base-pragma read (new in r2): has no Point yet. Its Host must be the `has_pragma` lambda at `:2456` (per N5). Test: AC4 self-added. Reached once GREEN lands.
- Fail-open: diff-listing failure → AC11 integration (bad base via `pre-red-ref.txt`, reachable as shown in ledger #14).

## Adversarial edges (not covered by any §2 AC)

1. **E1: same-name stub now defeats `deleted_files`.** Under the r2 move rule, RED deletes `tests/test_parser.py` (`test_parse_basic`, `test_parse_empty`) and its new RED file defines `def test_parse_basic(): assert False` and `def test_parse_empty(): …`. The deletion is classified as a "move", nothing is counted, and nothing appears in `exempted`. This also happens by accident when a "rewrite" reuses generic names, or when another changed M file already defines the same names. This is gap (a) itself, the headline incident class (9 test files deleted).
2. **E2: `git mv` is still terminal at pipeline level, through D1.** In AC6c the source path exists at the frozen SHA, is dirty and is unauthorized, so GH1600 D1 returns E_RED_TESTS_IN_EXISTING_FILE with the remedy "write a new test file". The RED asserts only `!= E_RED_TEST_INTEGRITY`, which hides this.
3. **E3: a deleted non-test file under `tests/`** (`tests/fixtures/data.json`, `tests/helpers.py`). The spec says this is a deletion (zero names), but no AC pins it, in either direction.
4. **E4: a D entry whose base cannot be decoded.** Example: a real Python test file with a latin-1 byte, or a binary fixture that is deleted. The move rule must read the base to collect names. Under §1.7 an unreadable file "is skipped", which would drop a real deletion. The spec does not say which way D entries go.
5. **E5: the triple combo.** An unauthorized file with at least 120 lines and at least 50% deleted, containing real test defs, where RED adds the pragma. GH282 exempts it (post-file pragma), D1 skips it (`_d1_exempted`), and only bd#226 (pragma not at base) stops it. This is the exact r1 F1(c) bypass, but no AC drives it end to end.
6. **E6: an exception in an exemption callback** (`is_authorized` / `has_pragma` raising). §1.7 says fail-closed, with no AC.
7. **E7: RED deletes the base pragma line together with tests.** The pragma is present at base and absent post. Whether this is exempt depends on the unstated callback semantics (N5).
8. **E8: cycle ≥2 re-entry** with a persisted `pre-red-ref.txt` and the cycle-1 RED commit landed. Carried over from r1 edge 8. Still no AC: RED's own cycle-1 files must stay A, not M.

## Findings

VERDICT: APPROVED

No MAJOR findings remain. All 4 r1 MAJORs are resolved in both spec and RED, and the RED discriminates for the right reasons. The NEW items below are MINOR (advisory). Fold them into the spec before GREEN where the change is cheap (N1, N3, N4, N5 are one-line spec edits). Adding ACs for them is optional.

### MAJOR

none

### MINOR (advisory)

1. **N1 (new, r2-introduced): the move rule widens the same-name-stub limit to whole-file deletions (E1).** §5's known-limit text covers only "a removed test". Either extend §5 explicitly to deleted files, or tighten the move rule: require all base names to appear in **one** destination that is new (A or untracked), not in an M file, and emit moved entries in `exempted` as `{kind: "moved", path, dest}` so that telemetry can catch rewrite-by-stub. Telemetry visibility is the minimum.
2. **N2 (new): AC6c's "not a deletion" holds only inside this gate. D1 still refuses `git mv` (E2, r1 G6).** State in §3 that a whole-file move of a pre-existing test file still needs `authorized-test-edits:` because of GH1600 D1. Consider asserting the real outcome in AC6c, so the behaviour is pinned rather than masked.
3. **N3 (new): `skipped_files` is missing from the §1.1 return-contract key list and from the §1.7 check-event field list, while RED AC9 asserts it as a documented key.** Add it to both. Without it in the event, a skipped file never reaches telemetry.
4. **N4 (new): §1.7 per-file skip vs D entries (E4).** Say that a D entry whose base cannot be read counts as a deletion (fail-closed: a move cannot be proven), so `skipped_files` applies only to M/A reads.
5. **N5 (new): the locus of the base-pragma check (§1.4) is unstated.** RED AC9 requires the module to trust `has_pragma`, so the base check must be in the caller callback (`git show <base>:<path>`, fail-closed). Write that down, and say whether presence post-RED is also required (E7).
6. **N6 (§1w): no AC for the fail-closed exemption-callback exception (E6),** or for "a zero-name deleted file is a deletion" (E3).
7. r1 #6 PARTIAL: list in §5 the accepted false positive "deleting non-test data/helpers under `tests/` blocks at threshold 0".
8. r1 #7 PARTIAL: no AC for the new skip patterns or the `*.bats` scoping. §5 should also list "a new test with `xfail` in an authorized existing file counts".
9. E5 (the r1 F1(c) triple combo) has no end-to-end AC. One integration test would lock in the precedence that justifies the r2 pragma change.
10. Cosmetic: AC order (AC10 after AC13). AC6b substring check. AC9 over-pins names inside `exempted`.

VERDICT: APPROVED
