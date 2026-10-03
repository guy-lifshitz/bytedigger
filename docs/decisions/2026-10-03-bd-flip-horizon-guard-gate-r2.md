# Gate r2: bd-flip-horizon-guard (spec r2 + amended RED)

- Spec: `docs/decisions/2026-10-03-bd-flip-horizon-guard.md` (r2)
- RED: `engine_py/tests/test_bd_flip_horizon_guard.py` (29 test items with parametrization; UUT `scripts/flip_horizon.py` absent)
- Ledger: `scripts/flip_horizon_ledger.json` (16 entries)
- Catalog scanned: `engine_py/bytedigger_engine/flags_catalog.py`
- Previous round: `docs/decisions/2026-10-03-bd-flip-horizon-guard-gate-r1.md` (REJECTED, F1 MAJOR)
- Audit date: 2026-10-03. Read-only audit: nothing was executed and no audited file was changed.

## r1 findings: closure check

| r1 | Status | Evidence |
|---|---|---|
| F1 MAJOR, wall-clock default unpinned | **Closed** (residual noted as N1) | §2.3 names the seam `default_today()`, which returns the UTC date and is the only clock read. `main` uses it when `--today` is absent. AC9a: `default_today() == datetime.now(timezone.utc).date()`. AC9b: `monkeypatch.setattr(fh, "default_today", 2099)`, then `main(["--check"], ledger_path=LEDGER)` must return 1 while the real catalog has tokens. See the attack analysis below |
| F2 terminal state | Closed | The `assert ledger` non-empty check is gone from AC8. AC7-2099 and AC9b branch on `fh.find_tokens(FLAGS)` and expect 0 when no tokens remain. AC6 now injects synthetic `flags=SYN`, so it no longer depends on the real catalog |
| F3 interface undeclared | Closed | §2.3 declares `main(argv, ledger_path=None, flags=None) -> int` and `default_today()` |
| F4 off-by-one | Closed | AC10 has two tests: `until == today` is clean and `today-1` is LAPSED; a token at `today-1` is UNCOVERED and a token at `today` is not |
| F5 `sys.path` fallback untested | Accepted in spec §7 | Explicit acceptance. That is a legitimate disposition for a MINOR |
| F6 time bomb, UTC | Closed | §2.3/§2.4 pin UTC. §7 records the 2026-10-17 expiry; the ledger has exactly two entries with `until` 2026-10-17 |
| F7 setup ERROR rather than FAIL | Unchanged (NIT) | The module-scoped fixture still asserts `SCRIPT.is_file()`. pytest reports this as a setup error that names the missing UUT, not a collect error. Acceptable |
| F8 token-shape evasion | Closed | §2.1 adds the near-miss rule. AC11 covers 3 near-miss shapes plus 2 prose decoys |
| F9 unasserted shapes | Mostly closed | E4 wrong-shape ledger and E9 two overdue tokens are now AC12. E8 is resolved by the rule "one line per flag per code; several codes may fire". Still open: catalog order (NIT) and non-string descriptions (see N2) |

### F1 attack analysis: can a hardcoded-date or hardcoded-output GREEN still pass?

1. **Constant inside `main`**, for example `today = args.today or date(2026,10,3)`, or `main` reading `datetime` directly and bypassing the seam. AC9b patches `fh.default_today` to 2099, but `main` never calls it. The run then sees 2026-10-03 against the real ledger, gets exit 0, and the test expects 1, so it **FAILS**. Closed.
2. **Seam bound at definition time**, for example `def main(..., _today=default_today)`. AC9b's module-attribute patch is not seen, so the test **FAILS**. Closed. This also forces a call-time global lookup, which is the intended behaviour.
3. **Constant inside the seam**: `def default_today(): return date(2026,10,3)`. AC9a compares against the real UTC clock. It passes only on 2026-10-03 UTC, the day it is written, and fails from 2026-10-04 on every CI run. This is not *permanently* passable as it was in r1: it reddens main within one day, and the guard does not go silently dead. The residual risk is that a same-day GREEN plus same-day verify would ship it, with the failure surfacing on the next PR. See N1 (MINOR).
4. **Hardcoded output**, for example `check` returning canned lines or `main` returning a fixed code. The synthetic fixtures across AC1–AC6 and AC10–AC12 use different flags, codes and counts. AC7a needs exit 0 and AC7b/AC9b need exit 1 on the same real inputs, separated only by the date. No constant return satisfies all of them.
5. **`default_today` returning the local date** (`date.today()`) instead of UTC. In CI the runner TZ is UTC, so AC9a cannot tell them apart. It differs only for a local run between 00:00 and 02:00 Berlin time. See N3 (MINOR).

## Step 1: spec-internal consistency (literal-token drift)

- The codes `UNCOVERED LAPSED TOO_FAR NO_REASON BAD_DATE STALE BAD_TOKEN` are consistent across §2.1, §3 and the RED.
- The kinds `flip-by`/`kill-by`/`retire-by` are consistent.
- The interface names `default_today`, `main(argv, ledger_path, flags)`, `find_tokens` and `check` are consistent across §2.3, AC6, AC9 and the RED.
- The near-miss rule (§2.1) and AC11 agree: canonical is lowercase `kind:YYYY-MM-DD`, and `Flip-By:` counts as a near miss, so matching is case-insensitive.
- The two ledger entries with `until` 2026-10-17 match §7.
- The other 14 entries are 2026-11-03, which is today+31 and within the 45-day limit. All 16 keys are exactly the 16 overdue `flip-by:` flags.
- NIT: the RED module docstring still says "AC1..AC8". It should say AC1..AC12.

## Step 1.5: rule-overlap simulation (`check` classifier, new r2 fixtures)

| Fixture | Codes that apply | RED expectation | Consistent? |
|---|---|---|---|
| AC10 `until == today`, overdue token | none (`until < today` false) | `[]` | yes |
| AC10 `until == today-1` | LAPSED | ==1 LAPSED | yes |
| AC10 token `today-1`, empty ledger | UNCOVERED | ==1 | yes |
| AC11 `flip-by 2026-08-07` / `Flip-By:2026-08-07` / `flip-by: 2026-08-07` | BAD_TOKEN (near miss). Whether a near miss is *also* an overdue token is unspecified (see N4) | ==1 BAD_TOKEN, total not asserted | yes |
| AC11 "GH1199 kill-by enforcement" / "kill-by classification" | none (no date follows) | `[]`, `find_tokens == []` | yes |
| AC5 `flip-by:2026-13-40` | BAD_TOKEN from the canonical-shape path. The near-miss path could also match (`\d{4}-\d{1,2}-\d{1,2}`); the "one line per flag per code" rule dedups it | ==1 BAD_TOKEN | yes, the r2 dedupe rule removes the double-report risk |
| AC12 two overdue tokens on one flag | UNCOVERED, deduped | ==1 and len==1 | yes |
| AC1 D "mentions flip-by soon and 2026-08-01" | none (near miss requires only `:`/spaces between `-by` and the date) | not in `find_tokens` | yes |

No first-match contradiction.

### Near-miss rule vs. the real catalog (requested scan)

I ran a case-insensitive scan for `(flip|kill|retire)[-_ ]?by` over the whole `flags_catalog.py`. It found 22 hits:

- **18 canonical tokens** at lines 235, 337, 361, 391, 470, 485, 516, 528, 535, 607, 619, 631, 643, 667, 691, 697, 739, 745.
  - All are lowercase, use a colon with no space, and have a two-digit month and day.
  - All 18 are excluded from BAD_TOKEN by the "not exactly canonical" clause.
  - The tokens at lines 470 and 485 sit inside parenthesised implicit string concatenation, so the description is still one `str`.
- **Line 534** `# flip-by:2026-07-26 Refs #681` is a source comment, not a description. It is out of scope by §6.
- **Line 687** `"HAL_KNOWN_REDS_KILL_BY_ENFORCE"` is a dict key, not a description. It uses an underscore and no date follows.
- **Line 691** "GH1199 kill-by enforcement ... flip-by:2026-08-08":
  - "kill-by" is followed by " enforcement", not by `:`/spaces and a date, so it is not a near miss.
  - The later `flip-by:2026-08-08` is canonical.
  - A *lenient* GREEN regex (`\W{0,3}`, `.*?`, `[^\d]*`) would still be safe at "kill-by", because it is followed by letters. A `.*?` regex, however, would span to `2026-08-08` and produce a false BAD_TOKEN. The spec's wording "optional `:`/spaces" forbids that, and AC7a (real catalog, exit 0) would catch it.
- **Line 733** "kill-by classification" has no date, so it is not a near miss.
- "Kill-switch" (lines 462, 477, 528, 667) does not contain `-by`.

**Conclusion: a spec-conformant near-miss regex produces zero false BAD_TOKEN on the current catalog.** Any GREEN that does produce one fails AC7a, because exit 0 is required on the real catalog at the real date. So the regression is fenced deterministically.

## Step 2: §2-vs-§3 cross-check

| §2 path | AC / test |
|---|---|
| exit 0 clean | AC6 (missing ledger, covered fixture), AC7a |
| exit 1 + stdout lines | AC6 exit-1, AC7b, AC9b |
| exit 2, unreadable JSON | AC6 malformed |
| exit 2, JSON not an object of objects | AC12 `[]`, `{"SYN_A": "str"}` (stderr non-empty asserted) |
| missing ledger = empty | AC6 |
| `default_today` = UTC, only clock read | AC9a/AC9b |
| near-miss → BAD_TOKEN; prose not a token | AC11 |
| one line per flag per code | AC12, AC5 |
| non-string descriptions ignored | **no AC, no test** (N2, MINOR) |
| ledger entries in catalog order | unasserted (NIT; the committed ledger does follow catalog order) |
| `sys.path` fallback | accepted in §7 |

Inverse direction: every §3 AC (AC1–AC12) has a producing §2 clause.

## Step 3: RED adequacy

- **Coverage:** every AC from AC1 to AC12 and every §4 edge has at least one assertion.
- **Failure mode:**
  - The `fh`-fixture tests error at setup with "missing production script".
  - `test_ac7_real_catalog_real_ledger_real_today_clean` fails at assert time (the subprocess exits 2 on the missing file).
  - Collection succeeds: no top-level import of the UUT, and `bytedigger_engine` is imported inside the test bodies.
- **Stub-passability:** the RED does not mock its UUT. AC9b monkeypatches only the *clock seam*, which is input injection, not mocking the unit, and `main`/`check`/`find_tokens` run for real. The F1 attacks are analysed above. Only the same-day seam constant remains (N1).
- **Determinism:**
  - Every synthetic test injects `TODAY` or `--today`.
  - AC7a and AC9a read the real clock by design.
  - AC7b and AC9b are date-independent, because 2099 is beyond every ledger `until`.
  - AC9a can race across UTC midnight (the UUT call happens before the comparison read). The window is microseconds (NIT).

## Step 4: reachability (§1y Point → Host → Test)

| Side-effect AC | Point | Host | Test-path |
|---|---|---|---|
| AC7a | problem lines + exit code | `main()` via `__main__` of `scripts/flip_horizon.py`, date from `default_today()` | subprocess, real `FLAGS`, real ledger, real UTC date |
| AC7b | same | same | subprocess with `--today 2099-01-01` |
| AC9b | `main` → `default_today()` lookup at call time | `main(argv, ledger_path, flags=None)` | in-process, seam patched, real `FLAGS` + committed ledger |
| AC8 | the committed ledger | n/a | direct JSON read + `find_tokens(FLAGS)` |

Every trace reaches the real functions without mocks. The production lines do not exist yet, so the traces end at the declared names.

## Adversarial edges (not covered by §3 ACs)

- **E1 Same-day seam constant:** `default_today()` returning a literal equal to the authoring date passes AC9a on that day only. A fake clock injected into the module (for example by patching `fh.datetime` with a class whose `now(tz)` returns 2099) would catch it on any day. See N1.
- **E2 Non-string / missing description:** `description: None`, `description` absent, or a non-str value. §2.1 says these are ignored, but no test exists. A naive `re.finditer(pat, d["description"])` raises `TypeError`/`KeyError`, and AC7a would only catch that once such a row lands in the real catalog.
- **E3 Near miss double-counted as a token:** `flip-by: 2026-08-07` could produce both BAD_TOKEN and UNCOVERED. AC11 asserts only the BAD_TOKEN count. The spec's `find_tokens` definition implies a near miss is not a token, but the RED does not pin it.
- **E4 Ledger entry dict missing `until`** (`{"HAL_X": {"ref": "r", "reason": "x"}}`): BAD_DATE or a crash? Unspecified. It is an object of objects, so exit 2 does not apply.
- **E5 Near miss with an underscore** (`flip_by:2026-08-07`): the §2.1 regex uses `-by`, so this shape escapes both detectors silently. It does not occur in the current catalog.
- **E6 Local-date seam:** `date.today()` instead of the UTC date is indistinguishable in a UTC-TZ CI. See N3.

## Findings

1. **MINOR, N1: F1 residual, the same-day seam constant.** A GREEN whose `default_today()` returns a literal of the authoring date passes AC9a on that UTC day only. It goes red on the next day's CI, so the guard cannot go silently dead as it could in r1. This is no longer a MAJOR. Optional hardening: in AC9a, patch the module's `datetime` reference with a fake whose `now(tz)` returns a far date, and assert that `default_today()` reflects it. Alternatively, require the GREEN verify run to include a reviewer read of `default_today`.
2. **MINOR, N2: the "non-string descriptions are ignored" clause (§2.1) has no AC or test.** Add one `find_tokens`/`check` case with `description: None` and a row without `description` → `[]`, no exception (E2).
3. **MINOR, N3: the UTC requirement is not distinguishable in CI.** AC9a passes for `date.today()` when TZ=UTC. The impact is limited to local runs between 00:00 and 02:00 Berlin time. Accept it, or make the fake clock from N1 return a tz-dependent instant.
4. **MINOR, N4: near-miss exclusivity is unpinned.** AC11 does not assert `len(out) == 1` or `find_tokens(flags) == []` for the near-miss shapes (E3). One extra assert per parametrized case would close it.
5. **NIT, N5: stale docstring.** The RED docstring says "AC1..AC8" but the file covers AC1..AC12. Catalog order of ledger entries (§2.2) is still unasserted.
6. **NIT, N6: setup-ERROR rather than FAIL.** Unchanged from r1 F7. Acceptable.
7. **Info, N7: catalog scan result.** The r2 near-miss rule produces zero false BAD_TOKEN on the current `flags_catalog.py` (18/18 canonical; the "kill-by enforcement" and "kill-by classification" prose and the `KILL_BY` dict key are not matched). Any regression of that kind is fenced by AC7a.

No MAJOR findings. Every r1 MAJOR/MINOR is closed or explicitly accepted. GREEN may proceed. Folding N1–N4 into the RED before GREEN is recommended but does not block.

VERDICT: APPROVED
