# Gate r2: bd#154 / bd#141 item 4(b), `oracle verify` host CLI

**Audited:** spec `docs/decisions/2026-10-02-bd141-p4b-oracle-cli.md` (r2) and RED
`engine_py/tests/test_bd141_p4b_oracle_cli.py` (48 cases), against `oracle.py` / `run.py` on `aaa9c3a`.
**Round:** 2 of 3 (TIER 2). **Method:** read-only. I did not run anything. The orchestrator's
measurement (48 cases, all red at assert time, no collection errors) is taken as given.

## r1 findings: resolution check (spec AND RED)

| r1 | spec r2 | RED r2 | test that reddens | status |
| --- | --- | --- | --- | --- |
| MAJOR 1 `--run-id ""` fails open | §2.2 :44-47 requires non-empty after `strip()`; rc-2 list :78 | AC9 `empty_run_id`, `blank_run_id` (test :337-339) | `test_ac9_usage_errors_exit_2[empty_run_id]`: without the check, `find_last_freeze(events, "")` skips the filter (oracle.py:312), so the result is rc 0 `verified` against run A's freeze. `[blank_run_id]`: `"  "` is truthy and `!= "run-A"`, so the result is rc 0 `unfrozen`. Both are red. | resolved |
| MAJOR 2 missing `D` has no AC | AC2(d) :107; AC7 list :122 | `_s_removed_dir` (test :119-124), used in AC2 and AC7 | `test_ac2_mutated_one_per_token[removed_dir]`: a GREEN that pre-checks `isdir(D)` and exits 2 fails `_report` (rc 0 expected). A GREEN that takes the verdict from the `current_digest` computation gets `INDETERMINATE`, because `_read_member` raises FileNotFoundError → OSError → oracle.py:141, and so fails `token`. `test_ac7_engine_parity[removed_dir]` also pins the engine code. | resolved |
| MINOR 3 catch scope | :80-83 one `try` over `find_last_freeze`, `frozen["payload"]`, `verify_against`, `current_digest`; (KeyError, TypeError, AttributeError, ValueError) | `payload_str_no_run_id`, `no_payload_key` | | resolved |
| MINOR 4 AC drift | :30 "(AC11)", :87 "`test_bd8_l1_oracle.py` AC-12" | | | resolved |
| MINOR 5 `current_digest` derivation | :70 names the derivation and notes the `added` invariant | AC2 `added` asserts `current_digest == frozen_digest` (test :193-194) | | resolved |
| MINOR 6 bd_l3 divergence | :56-58 | | | resolved |
| MINOR 7 AC7/AC11 | AC7 adds `utf8`/`nonobj`/`removed_dir`; AC11 `sys` | test :270-271, :395 | | resolved |
| MINOR 8 NaN / non-str digest | :82-83 `allow_nan=False`, non-str `digest` is malformed | AC9 `digest_int` | | resolved (see new finding 2) |
| MINOR 9 engine-side follow-up | §5 :152-153 | | | acknowledged; no issue number yet (finding 4) |

## Step 1: spec-internal consistency (token drift)

- Flags, outcome literals, codes, `TOKEN_*`, and the 8-key set agree across §2.2, §3 and the RED
  `KEYS` (test :29-30). The AC references in §1.4 and §2.2 are corrected. The AC7 scenario list
  (:122) matches the RED parametrize list (test :269-271) one to one. AC3(b) and AC4(e) are
  correctly excluded.
- I found no new drift.

## Step 1.5: rule-overlap simulation (the new and changed cases)

CLI order: argv check → read → `find_last_freeze` → `None` ⇒ unfrozen, otherwise payload
validation → `verify_against` → refusal code. `current_digest` is computed independently.

| scenario | first matching branch | expected | ok |
| --- | --- | --- | --- |
| AC2(d) `rmtree(D)` | `missing` = both members (:365), then `compute_scope_digest` → FileNotFoundError on `specs/` → :185 MUTATED `mutated:removed` raised before the `missing` raise. `current_digest`: `_read_member` OSError → INDETERMINATE → caught → `null` | mutated:removed, `current_digest` null | yes |
| AC2(b) add `.new.md` | members unchanged; `compute_digest(D, [a,b])` equals the freeze digest (`when` does not enter the digest); scope digest differs → :402 added | `current_digest == frozen_digest` | yes |
| AC9 `empty_run_id` | argv check, before reading the log | rc 2 | yes |
| AC9 `blank_run_id` | argv check (`strip()`) | rc 2 | yes |
| AC9 `payload_str_no_run_id` | `find_last_freeze` :311 `("x" or {}).get` → AttributeError inside the `try` | rc 2, `malformed` | yes |
| AC9 `no_payload_key` | top-level `run_id` passes :311; `frozen["payload"]` KeyError | rc 2, `malformed` | yes |
| AC9 `digest_int` (`members: []`, `scope: []`, `digest: 1`) | only the explicit "digest not a str" check catches this. Without it, `verify_against` reaches :392, `sha256(...) != 1`, and raises MUTATED `mutated:content` (rc 0) | rc 2, `malformed` | yes, if the check runs BEFORE `verify_against` (finding 1) |
| AC7 `utf8` / `nonobj` | engine `_oracle_entry_verify` :142 → fixed reader raises OracleRefusal INDETERMINATE → :153 → `error_code` INDETERMINATE. The CLI gives the same code | parity | yes, after the reader fix. On base the engine raises UnicodeDecodeError / AttributeError out of the test, which is still red |
| AC7 `removed_dir` | engine verify_against → MUTATED. The log is in `logs/` and the copy in `copy/`, both outside `D`, so `rmtree(D)` does not touch them | parity | yes |
| AC7 `dir_log` | engine `read_text` on a directory → IsADirectoryError → INDETERMINATE. The `phase_refused` append to the directory fails inside run.py:123 `except Exception` | parity | yes |

Every new AC9/AC7 case is reachable by a GREEN that follows the spec. None of them requires
behaviour the spec does not state.

## Step 2: §2 vs §3 cross-check

- §2.2 rc-2 list (:77-83) maps to AC9's 14 cases one to one: no args, bogus, 3× missing flag,
  empty/blank id, unknown flag, positional, payload `"x"` with and without top-level `run_id`,
  members without `path`, no `payload` key, non-str digest.
- §2.2 faithfulness consequences: missing `P` → AC3(b); directory `P` → AC4(a); missing `D` →
  AC2(d). All covered now.
- §2.1 reader → AC4(c,d), AC5, AC7. Read-only → AC10. Hygiene → AC11.
- Inverse: every AC has a producing §2 path. No mismatch.

## Step 3: RED adequacy

- Collection: all `bytedigger_engine` imports are still deferred (test :54, :245, :273, :380, :401).
  `shutil` is imported locally. The file collects.
- Assert-time reds on base. The `-m` subprocess exits 0 with empty stdout, so:
  - `_report` fails at `len(lines) == 1`;
  - AC9 fails at `returncode == 2`;
  - AC5 fails at `pytest.raises`;
  - AC11 fails at `_main`.
- Nothing mocks the UUT. Freezes come from the production writer, and the malformed rows are
  hand-written only where the production writer cannot produce them.
- The code change that turns each new case red:
  - `empty_run_id` / `blank_run_id`: drop the strip check.
  - `removed_dir`: an `isdir(D)` pre-check, or taking the verdict from the `current_digest`
    computation.
  - AC2 `added`: emitting `null` for every non-content mutation.
  - `payload_str_no_run_id`: a `try` that excludes `find_last_freeze`.
  - `no_payload_key`: a `try` that excludes `frozen["payload"]`.
  - `digest_int`: no type check, or a type check placed after `verify_against`.
  - AC11 `sys`: a top-level `import sys`.
- Siblings: there is still no top-level `sys`/`argparse`/`_main` in oracle.py (imports :46-53).
  `bytedigger_engine/__init__.py` imports nothing (:14-21), so `runpy` raises no "found in
  sys.modules" RuntimeWarning that would break AC9's `stderr.startswith("oracle: ")`.
  `test_bd8` AC-12 deletes and re-imports the module (:1349-1350). That is harmless here, because
  every test re-imports `oracle` locally.
- The case count is 1+4+3+5+2+1+11+3+14+2+1+1 = 48, which matches the orchestrator's count.

## Step 4: reachability (§1y)

- Point: the new `_main` in `oracle.py`, plus the `read_log_events` edits at :280-296.
- Host: `_main`, reached through the `__main__` guard. `read_log_events` is also reached through
  `run._oracle_entry_verify` :142 and `_oracle_after_execute` :169/:186.
- Test path:
  - the subprocess runs `-m` → `_main` → reader / `find_last_freeze` / `verify_against` /
    `compute_digest` → one stdout line;
  - in-process `_main` (AC12);
  - direct reader call (AC5);
  - the engine path via `_oracle_entry_verify` (AC7).
- §1l side-effect: the real freeze row written at run.py:210 is read back in AC1 (test :172-174).
  Reachable.

## Adversarial edges (not covered by §3)

- **Payload check order vs `verify_against`:** for `digest_int`, the verdict depends on whether the
  type check runs first. The RED pins "first". The spec only says "plus" (finding 1).
- **Prefix abbreviations:** argparse's default `allow_abbrev=True` makes `--event`, `--run` and
  `--scratch` silently resolve to the real flags. The unknown-flag case (`--bogus`) does not cover
  this, so the host contract is wider than the three flags listed (finding 3).
- **`--event-log ""`:** `read_log_events("")` returns `[]`, which gives `unfrozen`, the same as the
  engine. This is consistent and only noted.
- **Missing `digest` key (`None`) in a hand-written payload:** classified as malformed (rc 2) by
  the "not a str" rule, whereas the engine would report `mutated:content`. This is the same
  divergence class as finding 2 and has no extra AC.
- **Embedded-NUL member path:** on Python ≥3.8, `Path.exists()` returns False for it, so it falls
  into `mutated:removed`, the same as the engine. It is only noted.

## Findings

1. **MINOR: the order of payload validation is unstated.**
   - Spec :82-83 says only "plus a found payload that is not an object or whose `digest` is not a
     `str`". The RED `digest_int` passes only if this check runs before `verify_against`, because
     otherwise a MUTATED verdict is produced first.
   - Fix: add "validated immediately after `frozen["payload"]`, before `verify_against`". The test
     already forces this order, so GREEN converges either way.
2. **MINOR: there is an undeclared engine divergence on a non-str `digest`.**
   - §2.2 :49 says the verdict is "exactly what `run._oracle_entry_verify` decides". For
     `digest: 1` (or a missing `digest`), the engine returns `E_ORACLE_MUTATED` and the CLI returns
     rc 2.
   - This divergence is deliberate and correct, because it keeps stdout valid JSON and the row is
     not producible by the writer. State it next to the bd_l3 sentence. It is not in AC7, so no
     test conflicts with it.
3. **MINOR: argparse abbreviations.**
   - Consider `allow_abbrev=False` so that the host contract is exactly the three flags. This is
     optional and needs no AC.
4. **MINOR (carried from r1 #9): the follow-up has no number yet.**
   - §5 names the engine-side malformed-payload follow-up but gives no issue number. File it and
     cite the issue number.

No MAJOR findings. All r1 MAJORs are resolved in both the spec and the RED, and each has a named
reddening case. The new cases are reachable by a conformant GREEN and parity holds after the
reader fix.

VERDICT: APPROVED
