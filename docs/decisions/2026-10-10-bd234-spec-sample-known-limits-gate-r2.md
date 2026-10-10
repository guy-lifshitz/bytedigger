# bd#234 gate r2 - spec + RED audit

Scope: revised spec `docs/decisions/2026-10-10-bd234-spec-sample-known-limits.md`, revised RED `tests/test_bd234_spec_sample_known_limits.py` (125 tests, 0 pass / 125 fail at assert time through the `_load` key-set check, per the preflight facts), `scripts/spec_sample_check.py`, the 2-line sibling change in `tests/test_s3_spec_sample_check.py`, and the r1 report. MINOR items from r1 are not re-raised.

## 1. Closure of the r1 MAJORs

1. r1 finding 1 (fence content and thresholds): CLOSED.
   - Spec item 5 now states that lines inside a fence are never AC lines or headings but stay in the enclosing AC block and are parsed for thresholds, `sample:` and `measured:`. This matches the current `_parse_blocks` (fenced lines are appended to the open block) and the sibling S3 behaviour, so there is no change of documented semantics.
   - AC5 text and tests agree. `test_ac5_fenced_threshold_under_an_ac_line_still_counts[```|~~~]` puts `prose share < 0.25` inside the fence under a non-numeric AC line with `measured: 0.31`; it only yields `MEASURED_CONTRADICTS` if the fenced number is kept (a GREEN that drops fenced lines gets a non-numeric AC and no finding). `test_ac5_hash_line_inside_a_fence_does_not_end_the_block[both kinds]` pins that a `#` line in a fence does not end the block. Parity across both fence kinds is pinned.
2. r1 finding 2 (RED ok-set percent rule): CLOSED.
   - `test_ac9_percent_measured_calibrates_red_ok_set` uses a clean AC (`< 40%` with `measured: 31%`; both sides percent, raw compare, holds). Asserted `31` and `0.31` against a lot fixture with the same value are not flagged (raw and /100 forms); asserted `0.32` is still flagged (negative control). Simulated on the current script: `31` already passes, `0.31` is flagged today, so the `0.31` case really forces the /100 form. Spec AC9 text matches the test.
3. r1 finding 3 (ignored-listing failure): CLOSED.
   - `test_ac8e_failure_listing_ignored_files_is_git_failed` installs a wrapper that exits 128 on any argument equal to `--ignored` and otherwise delegates to the real git; it asserts exit 2, `unavailable`, `git_failed`. A GREEN that swallows a failed listing (empty list) fails it. Spec item 7/8 and AC8(e) agree, and the listing command in item 7 contains the literal `--ignored` flag.

No test contradicts documented S3 behaviour. The sibling tests `test_ac4_percent_suffix_is_stripped_and_read_as_31` (`< 25` vs `31%`) and `..._31_satisfies_greater_than_25` still hold under item 9 (plain side 25 has `|value| > 1`, so raw compare). The sibling `git_failed` cases (non-git root, bad base) fail earlier than the new listing and are unaffected. The two key-set edits in the sibling file match the new `waived` key.

## 2. Four-step audit (delta over r1)

### Step 1 - spec-internal consistency
Literal tokens (`HAL_SPEC_SAMPLE_ENFORCE`, `FLAG_EXPIRES` / `2026-10-17`, `RED_EXPECTED_FROM_INLINE_FIXTURE`, `SAMPLE_NOT_REAL`, `git_failed`, `waived`, `WAIVER_INVALID`, `WAIVER_NO_REASON`) are spelled identically across spec and RED. `NEW_KEYS` equals the spec key set. The AC5 sentence "a threshold inside a fence still counts" now has a producing rule in item 5 (r1 mismatch removed). Status line still reads "DRAFT r1" and the chokepoint header omits `_check_acs` for the waiver/percent ok-set: cosmetic, MINOR.

### Step 1.5 - rule-overlap simulation
Percent dispatcher simulated on all eight RED cases (`<25%` vs `0.31`, `0.1`, `31`, `20`; `<0.25` vs `31%`, `10%`; `<40` vs `45%`, `35%`): each resolves to the expected branch first. Fence state machine simulated on the eight open/close cases: each yields the expected AC set. Waiver line vs fence vs prose backticks vs mid-line: first-matching branch matches the expected outcome in all AC13 cases.

### Step 2 - section 2 vs section 3
Every section 2 item has an AC (1-AC1, 2-AC2, 3-AC3, 4-AC4, 5-AC5, 6-AC6, 7-AC7 and AC8e, 8-AC8, 9-AC9, 10-AC10..13, key sets AC14). The three r1 uncovered sentences are now covered. Every terminal path (`git_failed` on three git sites, `WAIVER_*`, waived move) has an AC.

### Step 3 - RED adequacy
Collection is safe (no import of the script); failure at assert time. No test mocks its unit under test; the git wrapper delegates to real git. New tests are discriminating (checked above for AC5 fenced threshold, AC9 ok-set `0.31`, AC8e).

### Step 4 - reachability
- Ok-set percent: Point `_check_acs` ok-values assembly, Host `_check_acs`, Test-path `test_ac9_percent_measured_calibrates_red_ok_set` via subprocess.
- Ignored-listing failure: Point the `ls-files --others --ignored` call in `_scan_inner`, Host `_scan_inner`, Test-path `test_ac8e`.
- Fence retention: Point `_parse_blocks`, Host `_parse_blocks`, Test-path AC5 tests.

## 3. Adversarial edges (not covered by section 3 ACs; none introduced by the revision)

- The `--ignored` wrapper matches only the exact long flag; an implementation using `-i` / `--ignored=traditional` would bypass the wrapper and leave its failure path untested (spec item 7 names the long form, so acceptable).
- Percent ok-set for a percent threshold (`40%` contributing `0.4`) and a percent value present only in a threshold are not pinned (only the measured side is).
- Percent boundary: plain side exactly 1 or 0, both sides percent with different magnitudes, `25 %` with a space.
- Leading-dot in `measured:` and in cited sample text for verified/unverified matching.
- Waiver edge semantics (two waiver lines in one AC, multiple findings of one code, waiver text on the AC line itself, invalid code plus short reason on one line).
- Fence opener with trailing text on the closer, one-line ```` ```x``` ````.
- `_tracked_at` on trees, symlinks, gitlinks, glob characters in the path.
- Bytes/f-string length rule corner cases (replacement field counted as one character; nested format specs).

All are MINOR / advisory and may go to "Remaining known limits".

## 4. Findings

No BLOCKER. No MAJOR.

1. MINOR - Spec status line reads "DRAFT r1" and the chokepoint header omits `_check_acs`/waiver stripping location.
2. MINOR - AC8 `--ignored` wrapper bypassable by short flag forms (see section 3).
3. MINOR - Percent ok-set pinned only for the measured side.
4. MINOR - r1 MINOR items 4-9 remain open by agreement (waiver edge semantics, leading-dot against real samples, bytes length).

## Checklist

- AC1: PASS
- AC2: PASS
- AC3: PASS
- AC4: PASS
- AC5: PASS
- AC6: PASS
- AC7: PASS
- AC8: PASS
- AC9: PASS
- AC10: PASS
- AC11: PASS
- AC12: PASS
- AC13: PASS
- AC14: PASS
- AC15: PASS
- AC16: PASS
- AC17: PASS

VERDICT: APPROVED

<!-- verdict-anchor
spec: docs/decisions/2026-10-10-bd234-spec-sample-known-limits.md sha256:cf326e5cf768c7a8f58e99961c5498b8a59437c83e24c0dfaa77bb253beb0241
red: tests/test_bd234_spec_sample_known_limits.py sha256:0839d53655841dc73968cb171b47df90dd05e4ac75ba2c82e01e574f74e4fcdd
-->
