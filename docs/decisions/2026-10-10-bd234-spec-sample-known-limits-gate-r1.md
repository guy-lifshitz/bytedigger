# bd#234 gate r1 - spec + RED audit

Scope: spec `docs/decisions/2026-10-10-bd234-spec-sample-known-limits.md`, RED `tests/test_bd234_spec_sample_known_limits.py` (113 tests, all failing at assert time through the `_load` key-set check), script `scripts/spec_sample_check.py`, prior spec, and the 2-line sibling change. Known and accepted, not re-raised: non-numeric ACs and `WAIVER_*` reporting; AC8d using an ignored `node_modules` path.

## 1. Four-step audit

### Step 1 - spec-internal consistency (literal-token drift)
- `HAL_SPEC_SAMPLE_ENFORCE`, `FLAG_EXPIRES`/`2026-10-17`, `RED_EXPECTED_FROM_INLINE_FIXTURE`, `SAMPLE_NOT_REAL`, `git_failed`, `waived` are spelled identically in spec, RED and script. No drift found.
- The RED `NEW_KEYS` equals the spec key set `{status, mode, would_block, findings, waived, verified, unverified, flag, reason}`. The shadow-log key set equals the spec item 10 set.
- Chokepoint line in the spec header is incomplete: percent (item 9) also changes `_satisfies` pairing and the ok-set in `_check_acs`, and the waiver line stripping touches `_parse_blocks`/`_analyze`, but the header lists only `_analyze` for item 9. MINOR (advisory).
- Spec AC5 says a fence "hides an AC line and a threshold", while section 2 item 5 only defines fence open/close and says nothing about thresholds. See finding 1.

### Step 1.5 - rule-overlap simulation
- Operator dispatcher (`<=`, `>=`, `==`, `!=`, `=>`, bare `=`, phrases): simulated on `p95 = 200 ms`, `200 = p95`, `timeout=200`, `p95 != 200`, `x => 200`, `<= 200`, `less than or equal to 250`, `not less than 250`. First-matching branch gives the expected outcome in each case the RED pins. Unpinned overlaps are listed under adversarial edges.
- Percent rule: the six spec examples plus two extra cases resolve as the RED expects.
- Waiver line vs AC block parsing: the AC first line is stored with the `AC<n>:` prefix stripped, so `- AC1: waive: X reason: ...` looks like a line-start waiver to a naive parser. The spec does not decide it (adversarial edge A6).
- Fence vs waiver: today every line inside a fence is still appended to the open AC block (`_parse_blocks`), only AC/heading detection is suppressed. Waiver detection therefore needs a per-line fence flag that the current data model does not carry. The RED pins this (AC13 fenced case).

### Step 2 - section 2 vs section 3 cross-check
- Item 1 to AC1, 2 to AC2, 3 to AC3, 4 to AC4, 5 to AC5, 6 to AC6, 7 to AC7, 8 to AC8, 9 to AC9, 10 to AC10-13, key sets to AC14, flag to AC15, flow to AC16, independence to AC17.
- Section 2 sentences with no AC coverage: (a) item 9 "for the RED ok-set a percent value contributes both its raw and its /100 form"; (b) item 7/8 "a failure listing ignored files is also `git_failed`"; (c) item 5 / AC5 "a fence hides a threshold" (AC text exists, no producing section 2 rule, no test). See findings 1-3.

### Step 3 - RED adequacy
- Collection is safe (no import of the script); failures occur at assert time inside `_load`. No test mocks the unit under test; the git wrapper delegates to the real git.
- Verified non-vacuous by simulating the current script: AC1/AC3/AC4/AC5/AC6/AC7/AC8a/AC8b/AC10-13 each fail on a lazy or unchanged implementation for behavioural reasons, not only for the key set. AC2-short-phrase, AC4 `==`/`<=`, AC3 `v1.25` and AC9 same-side cases are regression shields that would pass on old behaviour apart from the key set; that is acceptable.
- No test found that a correct implementation cannot pass. One coupling: AC8 hard-codes the git subcommands `cat-file`, `ls-tree`, `show` as the only intercepted ones; an implementation using `rev-parse base:path` or `ls-files` for tracked-ness would pass the real git path but fail AC8a/b. The spec fixes this list in AC8, so accepted (MINOR note).
- Gaps are listed in the findings.

### Step 4 - reachability (side-effect ACs)
- Waiver / `waived` key / shadow-log `waived`: Point `_scan_inner` verdict and rec assembly, Host `_scan_inner`, Test-path AC10, AC14, AC16 via subprocess. Reachable.
- `_tracked_at` git failure: Point the two `cat-file -e` sites in `_scan_inner` and `_judge_sample`, Host those functions, Test-path AC8a and AC8b. Reachable. The ignored-listing failure site has no test path (finding 3).
- Gitignored lot set: Point `_scan_inner` lot builder, Host `_scan_inner`, Test-path AC7. Reachable.

## 2. Adversarial edges (not already covered by section 3 ACs)

- A1 Multiple findings of the same code in one AC (two missing samples, two contradicting measured values): does one waiver line waive all of them, and does the shadow-log `waived` list repeat the code? Unspecified and untested.
- A2 Two waiver lines in one AC (one valid, one invalid; or two valid with different codes): union, first wins, or reject all? Unspecified.
- A3 Invalid code and short reason on the same line (`waive: BOGUS reason: x`): which of `WAIVER_INVALID` / `WAIVER_NO_REASON` is emitted, or both? Unspecified; RED only checks `in`.
- A4 Percent boundary: plain side exactly 1 or 0 (`<25%` vs measured `1`), both sides percent (`<25%` vs `31%`), and `25 %` with a space before the sign (the existing regex puts `%?` immediately after the number). No test.
- A5 Leading-dot in `measured:` (`measured: .31`, `measured: -.5`) and in a cited sample for verified/unverified matching (`.25` in sample vs measured `0.25`). Spec item 3 only states threshold and fixture matching.
- A6 `- AC1: waive: NO_SAMPLE reason: ...` (waiver text directly after the AC id on the AC line itself).
- A7 Fence opener edge cases: an opener with an info string (` ```python ` as opener), a one-line ` ```x``` text ` at line start, a backtick fence info containing backticks, a closer with trailing text.
- A8 Thousands: a digit directly before the group (`1234,567`), `1,000.5`, `1,000%`, a list `100,200,300` in a threshold line, and grouped numbers matched against a real sample for the RED exemption (not only against lot fixtures).
- A9 Gitignored skip list: only `node_modules` is tested; each of `__pycache__`, `.venv`, `venv`, `.tox`, `.pytest_cache`, `.mypy_cache`, `.ruff_cache` is unpinned, and so is exact-component matching (`fixtures/venvdata.json` and `src/node_modules_x/data.json` must still count as lot-authored; a substring match would silently shrink the lot set and weaken the circularity check).
- A10 `_tracked_at` on a path that is a tree, symlink or gitlink at base, and on a path containing glob characters (`samples/a[1].txt`): `--literal-pathspecs` is only suggested and untested.
- A11 f-string and bytes length rule: a replacement field counting as one character toward the 8-char minimum (`f"{a}{b}{c}{d}4321"`), nested format specs (`f"{x:>10} total=4321"`), and bytes length measured on bytes versus decoded text.
- A12 A number present both in a bytes/f-string literal and in a plain string literal: which detail wins is unspecified.
- A13 `less than or equal to` word-boundary and prefix interplay: `not less than or equal to 250`, `unless than or equal to 250`, a phrase wrapped across a newline.
- A14 Unused waiver on an AC that is clean: whether that AC still calibrates the RED ok-set and appears in `verified` is stated by spec but not pinned (AC11 unused-waiver test checks only status and `waived == []`).

## 3. Findings

1. MAJOR - Fence content and thresholds (spec AC5 vs section 2 item 5, Step 2 mismatch). AC5 states a fence "hides ... a threshold"; item 5 does not. Today, lines inside a fence under an open AC block remain in the block and their numbers count as thresholds (`_parse_blocks` appends them; the sibling test `test_ac13_hash_line_in_fence_does_not_end_the_ac_block` relies on fenced lines staying in the block). The RED pins only an AC line inside a fence. A GREEN that starts dropping fenced numbers from AC blocks would weaken fail-closed behaviour (fewer numeric ACs, fewer findings) and pass the whole RED; a GREEN that keeps them also passes. Fix: state the rule explicitly in item 5 (fenced lines stay in the block for numbers, or are excluded) and add a test with a threshold inside a fence under a real AC line, in both fence styles.
2. MAJOR - Item 9 RED ok-set sentence is covered by no AC and no test. "A percent value contributes both its raw and its /100 form" can be skipped by GREEN with every test passing (AC9 only checks codes). Fix: add a test where a clean AC with a percent value (for example `< 25%` with `measured: 10%`) lets a RED assertion of `0.1` against a lot fixture holding `0.1` pass, and one where `0.25` is exempted likewise; also a negative where an unrelated number is still flagged.
3. MAJOR - Fail-closed gap: item 7 says "a failure listing ignored files is also `git_failed`", but the wrapper in AC8 only intercepts `cat-file`, `ls-tree` and `show`, so a GREEN that treats a failed `ls-files --others --ignored` as an empty list (for example by calling the non-raising helper) passes the whole RED. Fix: extend the AC8 wrapper so a variant exits 128 when the argument list contains `--ignored`, and assert exit 2 `git_failed` with no findings.
4. MINOR - Only one of eight skip components (`node_modules`) and no exact-component matching case is pinned (A9). Parametrize over all eight and add a near miss.
5. MINOR - Grouped and leading-dot equivalence is pinned only against lot fixtures; matching against real sample text (RED exemption and `verified`) is pinned only for `1,250` in AC16 (A5, A8).
6. MINOR - Waiver edge semantics undefined (A1, A2, A3, A6, A14); GREEN could diverge from the manager's intent. Decide and add one test each, or list under "Remaining known limits".
7. MINOR - Spec chokepoint header omits `_satisfies`/`_check_acs` for item 9 and the waiver line stripping location.
8. MINOR - AC8 hard-codes the intercepted git subcommands; any correct implementation not using `ls-tree`, `cat-file` or `show` would fail AC8a/b. Acceptable since the spec names them; keep the suggested `ls-tree` implementation.
9. MINOR - Item 6 minimum length for bytes is not defined (bytes versus decoded text), and the "replacement field counts as one character" rule is not pinned (A11).

## Checklist

- AC1: PASS
- AC2: PASS
- AC3: PASS
- AC4: PASS
- AC5: FAIL
- AC6: PASS
- AC7: PASS
- AC8: FAIL
- AC9: FAIL
- AC10: PASS
- AC11: PASS
- AC12: PASS
- AC13: PASS
- AC14: PASS
- AC15: PASS
- AC16: PASS
- AC17: PASS

VERDICT: REJECTED
