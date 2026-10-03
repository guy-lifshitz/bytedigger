# Gate r1: bd-flip-horizon-guard (spec + RED audit)

- Spec: `docs/decisions/2026-10-03-bd-flip-horizon-guard.md` (r1)
- RED: `engine_py/tests/test_bd_flip_horizon_guard.py` (20 tests; UUT `scripts/flip_horizon.py` absent)
- Ledger: `scripts/flip_horizon_ledger.json` (16 entries)
- Catalog: `engine_py/bytedigger_engine/flags_catalog.py`
- Audit date: 2026-10-03. Read-only audit: nothing was executed and no audited file was changed.

## Step 1: spec-internal consistency (literal-token drift)

- The problem codes (`UNCOVERED LAPSED TOO_FAR NO_REASON BAD_DATE STALE BAD_TOKEN`) match across §2, §3 and the RED.
- The token kinds (`flip-by`/`kill-by`/`retire-by`) are spelled the same in §2, AC1 and the RED.
- **Interface drift (MINOR, F3):** the RED calls `fh.main(argv, ledger_path=Path) -> int`. Neither `main()` nor the `ledger_path` keyword is in the spec. §2.3 describes only the CLI. The RED (read-only for GREEN) is the de-facto contract, so GREEN can still build it. The spec should name the in-process entry point anyway.
- §2.5 says the ledger "covers exactly the currently overdue set (16 flags)". Checked against the catalog: there are 18 tokens. 16 are `flip-by:` and overdue (dates 2026-07-24 .. 2026-08-16). 2 are `retire-by:2027-01-15` (`HAL_AC_DSL_GATE_ENFORCE`, `HAL_SPEC_DEFECT_REROUTE`) and are in the future. The 16 ledger keys equal the 16 overdue flags exactly, in catalog order. Every `until` is ≤ 2026-11-03 (today+31), with a non-blank reason and ref. This is consistent.

## Step 1.5: rule-overlap simulation (`check` is a multi-code classifier)

I traced each crafted RED fixture through every code to see which ones fire.

| Fixture | Codes that apply | RED expectation | Consistent? |
|---|---|---|---|
| AC2: overdue token, empty ledger | UNCOVERED | exactly 1 line | yes |
| AC2: overdue token, entry until today+17 | none (covered, until ≥ today, ≤ +45, reason+ref present) | `[]` | yes |
| AC3 LAPSED: until today-1 | LAPSED only. The entry exists, so not UNCOVERED; the token is overdue, so not STALE | len==1 | yes, but GREEN must treat "entry present" as covered even when the entry is lapsed. That follows from §2's "no ledger entry" wording |
| AC3 TOO_FAR +46 | TOO_FAR only | len==1 | yes |
| AC3 NO_REASON (A blank reason, B no ref) | NO_REASON ×2. Neither flag counts as UNCOVERED | len==2 | yes. This pins "an invalid entry still covers". Defensible, and consistent with the BAD_DATE row |
| AC3 BAD_DATE `2026-02-31` | BAD_DATE only. LAPSED/TOO_FAR must not be evaluated on an unparsable date, and the flag is not UNCOVERED | len==1 | yes. This forces a short-circuit after BAD_DATE, which is good |
| AC4 future token + entry; no token + entry | STALE ×2 | count per flag; total not asserted | yes |
| AC5 `flip-by:2026-13-40`, empty ledger | BAD_TOKEN. The token cannot be "overdue", so no UNCOVERED is expected (not asserted either way) | BAD_TOKEN ==1 | yes. The regex must be digit-shaped, not calendar-validated |
| §4 token date == today | nothing | `[]` | yes (`<`) |
| §4 one overdue + one future token, empty ledger | UNCOVERED ×1 | ==1 | yes. Unspecified: whether two overdue tokens on one flag give one line or two (no fixture) |

No branch ordering yields a wrong first match on any fixture. There are two unspecified combinations, and neither is exercised by the RED, so there is no contradiction: BAD_TOKEN-only flag with a ledger entry (STALE or not?), and NO_REASON plus STALE on the same entry. They are listed under the adversarial edges.

## Step 2: §2-vs-§3 cross-check (terminal/error paths)

| §2 path | AC / test |
|---|---|
| CLI exit 0 clean | AC6 `test_ac6_exit_0_clean_empty_ledger`, AC7 real-today |
| CLI exit 1 + problem lines on stdout | AC6 `test_ac6_exit_1_on_problem_with_line_on_stdout`, AC7 2099 |
| CLI exit 2 + stderr on unreadable ledger JSON | AC6 `test_ac6_exit_2_on_malformed_ledger` |
| missing ledger file = empty | AC6 `test_ac6_exit_0_clean_missing_ledger` |
| bad token never raises | AC5 |
| fall back to `engine_py` on `sys.path` when the package is not importable | **no AC** (see F5, MINOR; CI and local both exercise only one branch each) |
| default `today` = real wall-clock date (§2.4 "at the real date") | **no assertion distinguishes it from a constant** (F1, MAJOR) |

Inverse: every §3 AC has a producing §2 path. One gap: §2.3 "exit 2 on unreadable ledger JSON" says nothing about JSON that parses but has the wrong shape (a list, or an entry that is not a dict). See edge E4.

## Step 3: RED adequacy

- **AC coverage:** AC1–AC8 and all four §4 edges each have at least one assertion.
- **RED failure mode:** the AC7 tests fail at assert time, because the subprocess exits 2 on a missing file and `returncode` is asserted. Every `fh`-fixture test fails because of `assert SCRIPT.is_file()` inside a module-scoped fixture. pytest reports that as an ERROR at setup, not a FAIL at call time. Collection itself succeeds, and the failure names the missing UUT, so this is acceptable. Cleaner: load the module inside each test or helper (F7, NIT).
- **Stub-passability / trivial-GREEN attack (question 1):**
  - `check()` ignoring the ledger fails AC2's covered branch and AC3/AC4.
  - A `check()` that returns constant lists cannot satisfy the synthetic fixtures (different flags and codes).
  - A CLI that ignores the committed ledger fails AC7 at real today (16 UNCOVERED).
  - A CLI that ignores `--today` fails AC7 at 2099 and AC6 exit 1.
  - The RED does not mock its UUT.
  - **Gap (F1, MAJOR):** the CLI's *default* date is never pinned to the wall clock. `today = args.today or datetime.date(2026, 10, 3)` (or any constant ≥ 2026-10-03 and ≤ 2026-10-10) passes all 20 tests. Every test that touches the date either injects `TODAY`, passes `--today`, or (AC7a) runs on 2026-10-03 itself, where wall-clock and constant agree. That GREEN would keep AC7a green forever, which disarms the one property the spec calls "the enforcement" (§2.4, Principle C). AC7 is the §1l production side-effect AC, and its defining property is passable by a constant.
- **Boundaries (question 2):**
  - TOO_FAR 45 vs 46: pinned, both sides, relative to injected `TODAY`.
  - Token date == today is not overdue: pinned.
  - LAPSED boundary (`until == today`): **not pinned.** Only `today-1` is tested, so `until <= today` and `until < today` both pass (F4, MINOR).
  - Overdue boundary on the other side (token == today-1 must be UNCOVERED): not pinned. AC2 uses 2026-08-01, 63 days back. A GREEN using `token < today - N` for small N would pass (NIT, folded into F4).
- **AC7 robustness (question 3):**
  - *Future flips:* a follow-up that deletes a catalog token but keeps its ledger line gets STALE at AC7a and fails AC8's subset check. That is the intended forcing function, and it is good. A follow-up that deletes both stays green.
  - *Terminal state, not robust:*
    - **(a)** AC8 asserts `assert ledger, "committed ledger must not be empty"`. This is not in the spec's AC8 and contradicts §2.2/AC6, where an empty or absent ledger is valid. The last flip follow-up, which empties the ledger, turns this test red (F2, MINOR).
    - **(b)** AC7-2099 and `test_ac6_exit_1_on_problem_with_line_on_stdout` both need at least one token to exist in the real catalog. Today the two `retire-by:2027-01-15` tokens keep that true. Once they are removed, both tests fail. The spec claims "follow-up flips cannot break this AC", which holds only while some token remains (F2).
  - *Wall clock:* AC7a is deterministic until the earliest `until` (2026-10-10, two entries). On 2026-10-11 every PR and main go red until the follow-up flips land or the entries are renewed. That is the designed time bomb, not flakiness. CI runs in UTC while authors work in Europe/Berlin, which can shift the red by up to one local day. Acceptable, noted (F6, MINOR advisory).
- **CI import topology (question 5):**
  - The `pytest` job runs `python -m pytest tests/` with cwd `engine_py` and the wheel installed.
  - `ROOT = parents[2]` resolves to the checkout root, so `scripts/` exists.
  - Index 2 < `_UNSAFE_ANCESTOR_INDEX = 3`, so `test_engine_path_closure` AC5a/AC5b do not flag it.
  - The RED does not mutate `sys.path`, so the bd#182 fence is not tripped. That fence scans only `tests/`, so the CLI's own `sys.path` insert in `scripts/` is outside its reach.
  - AC8's in-process `from bytedigger_engine.flags_catalog import FLAGS` resolves to the source tree (cwd on `sys.path` under `python -m`).
  - The AC7 subprocess (`sys.executable scripts/flip_horizon.py`, cwd = root) resolves to the installed wheel, because `sys.path[0]` = `scripts/`. Both come from the same commit, so `FLAGS` are identical. Locally without an install, the §2.3 fallback branch is taken.
  - Imports work in CI. Only the fallback branch is never exercised in CI (F5, MINOR).
- **Catalog token-shape scan (question 4):** I scanned the real `flags_catalog.py` for `(flip|kill|retire|sunset|remove|expire|enforce)[-_ ]?(by|after|on)`, for every `20dd-dd-dd`, and for horizon-like dict keys (`kill_by`, `until`, `expires`, ...).
  - All 18 dated horizons use the canonical `kind:YYYY-MM-DD` form, inside `description` values.
  - There is no colon-less `flip-by 2026-…`, no `flip_by`, no `flip-by: 2026` with a space, and no capitalised form.
  - No date sits in `module`/`default`/`kind` fields, and there is no separate horizon key.
  - The only dates outside descriptions are the module docstring (line 4, 2026-07-09, no token) and the source comment `# flip-by:2026-07-26 Refs #681` at line 534. That comment duplicates the description token at line 535 of the same flag, so nothing is lost.
  - Non-dated mentions are correctly *not* tokens: "GH1199 kill-by enforcement" (line 691) and "kill-by classification" (line 733). The strict regex keeps them out. A lenient matcher must too.
  - **Verdict for the current catalog: complete coverage.** The forward risk is evasion. A future author who writes `flip-by 2026-12-01`, `flip-by: 2026-12-01` or `Flip-By:` silently escapes the guard, and nothing reports it (F8, MINOR).

## Step 4: reachability (§1y Point → Host → Test)

| Side-effect AC | Point | Host | Test-path |
|---|---|---|---|
| AC7a (CI red when a horizon passes) | `check()` problem lines plus non-zero exit | `main()` invoked by `__main__` of `scripts/flip_horizon.py` | `test_ac7_real_catalog_real_ledger_real_today_clean` → subprocess → real `FLAGS` + real ledger. **The date source is unpinned (F1)** |
| AC7b | same | same | `test_ac7_far_future_fails_with_uncovered_or_lapsed` |
| AC6 exit codes | `main()` return value + stdout/stderr | `main(argv, ledger_path)` | in-process AC6 tests (these read the real `FLAGS` through `main`, not "clean fixtures" as AC6 wording says; robust because `--today 2026-01-01` precedes every token) |
| AC8 committed ledger | the ledger file itself | n/a | `test_ac8_committed_ledger_valid_and_subset_of_token_flags` |

The production code is not written yet, so the traces end at the declared function names. Every test path reaches the real `main`/`check`/`find_tokens` without any mock.

## Adversarial edges (not covered by §3 ACs)

- **E1 Frozen-clock GREEN (regression shield for the enforcement itself):** the CLI is run with no `--today`, and the date source is forced to a far-future date, so the result must be non-zero. Today no test separates the wall clock from a constant. This is F1.
- **E2 LAPSED off-by-one:** `until == today` must be clean, and `until == today-1` must be LAPSED. Only the second is tested.
- **E3 Overdue off-by-one:** a token at `today-1` must give UNCOVERED. Only far-past tokens and token == today are tested.
- **E4 Wrong-shape ledger:** valid JSON that is not an object (`[]`, `"x"`), or an entry that is not a dict (`"HAL_X": "2026-10-20"`). The spec does not say whether this is exit 2 or a per-entry code. A naive GREEN raises `AttributeError`, so a traceback ends with exit 1, which is indistinguishable from "a problem was found".
- **E5 Non-string or missing `description`** in a catalog entry (`None`, a missing key). `find_tokens` must not raise. There is no instance today, but every future catalog row is free-form.
- **E6 Token-shape evasion (decoy fence):** colon-less, space-after-colon, or upper-case variants are neither recognised nor rejected. See F8.
- **E7 Terminal state:** the ledger is empty and the catalog has no tokens. Per the spec the guard should then exit 0 and be valid, but AC8 plus the RED's AC6-exit-1 and AC7-2099 all fail. See F2.
- **E8 Combined codes:** a BAD_TOKEN-only flag that has a ledger entry (STALE or not?). An entry that is both STALE and NO_REASON (one line or two?). The behaviour is unspecified.
- **E9 Two overdue tokens on one flag:** one UNCOVERED line or two? This is unspecified, and the RED fixture has only one overdue token.

## Findings

1. **MAJOR, F1: the wall-clock default is unpinned, and the RED is passable by a constant.** `scripts/flip_horizon.py` with `today = args.today or date(2026,10,3)` passes all 20 tests and disarms §2.4's enforcement permanently. Fix: in the spec, name a seam such as `default_today() -> datetime.date` (returning `datetime.date.today()`), which `main` uses when `--today` is absent. Add RED tests for both halves:
   - (a) `fh.default_today() == datetime.date.today()`;
   - (b) `monkeypatch.setattr(fh, "default_today", lambda: datetime.date(2099,1,1))`, then `fh.main(["--check"], ledger_path=LEDGER) == 1`. This proves `main` routes through the seam, so a constant inside `main` fails (b) and a constant inside the seam fails (a).
2. **MINOR, F2: the terminal state is not robust.** AC8's `assert ledger` (not in the spec, and contradicting §2.2/AC6) fails when the last flip empties the ledger. AC7-2099 and AC6-exit-1 depend on at least one real token existing (today the two `retire-by` tokens hold it), so the spec's "follow-up flips cannot break this AC" is over-claimed. Fix: drop the non-empty assert. Make the 2099 checks conditional on `fh.find_tokens(FLAGS)` being non-empty (if empty, assert exit 0 instead), or move the exit-1 proof to a synthetic catalog via a `flags=` seam on `main`.
3. **MINOR, F3: the spec does not declare the interface the RED binds.** Add to §2: `main(argv, ledger_path=DEFAULT_LEDGER) -> int`, and say that problem lines go to `sys.stdout` at call time.
4. **MINOR, F4: the LAPSED boundary is unpinned.** Add `until == today` → `[]`, and `token == today-1` → UNCOVERED (edges E2/E3).
5. **MINOR, F5: the `sys.path` fallback branch (§2.3) has no test, and CI exercises only the installed-wheel branch.** A cheap test: run the subprocess with `-I` (isolated mode, so no user site-packages) or a `PYTHONPATH`-free env when the package is not installed. Or accept it explicitly in the spec.
6. **MINOR (advisory), F6: AC7a is a designed time bomb.** Two entries expire 2026-10-10, and from 2026-10-11 every PR and main go red until the follow-up lands. This is intentional. Record in the spec that CI evaluates the date in UTC, and that renewal is the documented escape (a reviewed ledger edit).
7. **NIT, F7: the RED reports as a setup ERROR, not a FAIL.** `fh`-based tests fail in fixture setup on the missing UUT. They are not collect-time errors, so they are acceptable. Loading inside the test would make them report as FAILED.
8. **MINOR, F8: the token-shape evasion surface (question 4).** The current catalog is fully canonical (18/18 tokens are `kind:YYYY-MM-DD`; the only non-description token is a duplicate comment at line 534). Nothing stops a future `flip-by 2026-12-01` / `flip-by: …` / `Flip-By:` from silently escaping. Suggested: report any `(?i)(flip|kill|retire)[-_ ]by\W{0,3}\d{4}-\d{2}-\d{2}` that is not canonical as `BAD_TOKEN`, with one RED case. The non-dated "kill-by enforcement" / "kill-by classification" texts must stay non-tokens.
9. **NIT, F9: unasserted shapes.** §2.2's "entries in catalog order" is unasserted (the committed ledger does follow catalog order). Edges E4/E5/E8/E9 are unspecified. Specify E4 (wrong-shape ledger → exit 2), because otherwise a traceback is indistinguishable from exit 1.

VERDICT: REJECTED
