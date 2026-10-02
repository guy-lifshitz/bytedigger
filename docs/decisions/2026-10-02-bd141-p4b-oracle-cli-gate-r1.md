# Gate r1: bd#154 / bd#141 item 4(b), `oracle verify` host CLI

**Audited:** spec `docs/decisions/2026-10-02-bd141-p4b-oracle-cli.md` (r1) and RED
`engine_py/tests/test_bd141_p4b_oracle_cli.py`, against `oracle.py` / `run.py` on `2222a1f`.
**Round:** 1 of 3 (TIER 2). **Method:** read-only. I did not run anything; the orchestrator's
collect and assert-time-red measurement is taken as given.

## Step 1: spec-internal consistency (token drift)

- Flag names (`--event-log`, `--run-id`, `--scratchpad-dir`), outcome literals, codes, `TOKEN_*`
  values and the 8-key set match across §2.2, §3 and the RED `KEYS` (test :28). No drift there.
- **Drift:** §1.4 (spec :29) says the new `_main` imports no `bytedigger_engine` module "(AC12)".
  That is AC11. AC12 is the in-process test. §2.2 (:79) also says "AC-12 no-I/O-at-import", which
  means the sibling `test_bd8 AC-12`. One spec now has two "AC12"s that mean different things
  (finding 4).

## Step 1.5: rule-overlap simulation (CLI dispatch, then `verify_against` order)

CLI dispatch is: read → `find_last_freeze` → `None` ⇒ unfrozen, otherwise `verify_against` →
refusal code. Inside `verify_against` (oracle.py:350-407) the order is scope-listing → removed →
content → digest → added.

| scenario | first matching branch | expected | ok |
| --- | --- | --- | --- |
| AC2(c) delete `b.md` | scope dir still listable; `missing=[b.md]` → :369 removed | mutated:removed | yes |
| AC2(a) rewrite `a.md` | no missing; :384 content | mutated:content | yes |
| AC2(b) add `.new.md` | no missing, digests equal; :402 added | mutated:added | yes |
| AC4(e) `chmod 000 a.md` | `exists()` true; `member_digest` OSError → :141 INDETERMINATE | indeterminate | yes |
| AC3(c) freeze A, `--run-id B` | :312 skips A → `None` | unfrozen | yes |
| AC6 amend | last candidate is `oracle_amended` (:315) | verified, amended digest | yes |
| AC9 `payload "x"` | event has top-level `run_id`, so :311 short-circuits; `"x".get` in verify_against :359 → AttributeError | rc 2 | yes (only because `run_id` is top-level, see finding 3) |
| AC9 members without `path` | :358 `m["path"]` KeyError | rc 2 | yes |
| missing `D` (§2.2 claim) | :184 scope dir FileNotFoundError → mutated:removed, raised before the `missing` check | mutated:removed | yes, but no AC (finding 2) |
| `--run-id ""` | :312 `if run_id and …` is false, so nothing is filtered and the last freeze of ANY run wins | not specified | **fail-open (finding 1)** |

Token extraction: every `E_ORACLE_MUTATED` message starts with one `TOKEN_*` (:187, :371, :387,
:395, :405). No INDETERMINATE message starts with `mutated:`. The rule is sound.

## Step 2: §2 vs §3 cross-check

- §2.1 reader: UTF-8 → AC4(c)/AC5. Non-object → AC4(d)/AC5. The unchanged paths: missing → AC3(b),
  OSError → AC4(a), bad JSON → AC4(b). Covered.
- §2.2 rc-2 list: no subcommand, bogus subcommand, the three missing flags, unknown flag and
  positional are all in AC9. "Payload is not an object" is covered only for the variant where
  `run_id` is top-level. "verify_against raises TypeError" has no case (finding 3).
- §2.2 faithfulness consequences: missing `P` → AC3(b). Directory `P` → AC4(a). **Missing `D` →
  `mutated:removed` has no AC** (finding 2).
- §2.2 read-only → AC10. Module hygiene → AC11 (partial, finding 7).
- Inverse: every AC has a producing §2 path.

## Step 3: RED adequacy

- All imports of `bytedigger_engine.*` are deferred (test :53, :233, :261, :348, :368), so the file
  collects. On base `python -m` exits 0 with empty stdout, so `_report` fails at
  `len(lines)==1` (:80), and AC5/AC11/AC12 fail on missing behaviour or attributes. These are
  assert-time reds.
- Nothing mocks the UUT. Freezes come from the production writer `run._oracle_after_execute`
  (:57), and the CLI runs as a real subprocess (:66). Stub-passability: none found.
- The code change that turns each test red:
  - AC1: `current_digest` not computed, or the wrong event's digest.
  - AC2: wrong token parsing, or `current_digest` emitted after a removal.
  - AC3: rc 2 on a missing log, or creating the parent dir.
  - AC4: rc 2 for log errors, or dropping the reader fix (c, d).
  - AC5: reverting §2.1.
  - AC6: first-match instead of last-match.
  - AC7: any code remap.
  - AC8: an extra or missing key, or pretty-printed output.
  - AC9: `parse_known_args`, default argparse `error()` (stderr starts `usage:`), or a catch that
    misses KeyError/AttributeError.
  - AC10: any append, write or mkdir.
  - AC11: top-level `import argparse`, or `from bytedigger_engine …` / `from . import`.
  - AC12: printing from the guard instead of from `_main`.
- AC7 soundness (question 5): the CLI runs first against the original log. The engine then runs
  on a byte copy (:272-276), so the `phase_refused` row that `_oracle_refusal_result` appends
  (run.py:116-122) lands only in the copy, after the CLI verdict is already captured. In the
  dir-log case the copy is the directory itself; that append fails inside run.py's
  `except Exception` (:123) and cannot affect the earlier CLI result. The test is sound.
- Siblings: `test_bd8 AC-12` (:1328-1355) patches `open`/`scandir`/`listdir`/`subprocess` only at
  import. Function-local `argparse`/`sys` imports keep it green. `test_bd27 AC-E9` is an AST check
  on `signal` only. `conformance/__init__.py` is empty, so `-m` cannot double-import the module.
  No sibling feeds `read_log_events` invalid-UTF-8 or non-object lines; the only `\xff` event-log
  fixture (`test_dispatcher_report.py:430`) goes through a different reader. No sibling breaks.

## Step 4: reachability (§1y)

- Point: the new `_main` in `oracle.py`, plus the `read_log_events` edits at :280-296.
- Host: `_main`, reached from the `if __name__ == "__main__"` guard. `read_log_events` is also
  reached from `run._oracle_entry_verify` (:142) and `_oracle_after_execute` (:169, :186).
- Test path: subprocess `-m` → guard → `_main` → `read_log_events` / `find_last_freeze` /
  `verify_against` / `compute_digest` → one stdout line (AC1/2/3/4/6/7/8/10). In-process `_main`
  (AC12). Direct `read_log_events` (AC5).
- The §1l side-effect is the real freeze row that `get_event_sink(...).append` writes (run.py:210).
  AC1 reads it back (test :163-165). Reachable.

## Judgement on the five questions

1. **Faithfulness vs "same contract as bd_l3".** Accepted. bd_l3 makes missing, unreadable,
   non-JSON and non-UTF-8 logs rc 2 (bd_l3 spec :51-55). bd_l3 has no engine-side verdict to
   match. The oracle does: ORACLE_SPEC §5 (:340) makes "log could not be read" an
   `E_ORACLE_INDETERMINATE` verdict, and run.py treats a missing log as unfrozen. A shadow adapter
   has to match the engine, and all three choices fail closed (each is a refusal verdict, never
   `verified`). The "same contract" part that does carry over is: one JSON line, rc 0 for every
   verdict, rc 2 + `prefix: ` on stderr for usage errors. The spec should say this explicitly
   (finding 6), and the missing-`D` half needs a test (finding 2).
2. **Reader class fix.** In scope and correct. It is the chokepoint for both run.py call sites.
   `UnicodeDecodeError` is a `ValueError` and slips past `except OSError` (:282). Non-object lines
   crash `.get` in `find_last_freeze` (:309) and in `has_sentinel_resume`/`last_phase_artifacts`
   (:325, :339) on the freeze path too. Both are inside §5's "log could not be read". No sibling
   depends on the old behaviour.
3. **rc 2 for malformed freeze payloads.** Accepted. §5 has no code for "a freeze row the
   production writer cannot produce", and inventing one is out of scope. The catch scope is
   under-specified, though (finding 3). The engine still escapes on the same input, which is a
   follow-up (finding 9).
4. **`current_digest` under "no new logic".** Accepted. It is one call to an existing public
   function. It does not feed the verdict and is a reporting field only. Two gaps: the
   member-path derivation is unspecified, and the `mutated:added` case (where
   `current_digest == frozen_digest` by construction) is not asserted (finding 5).
5. **AC7 parity.** Sound (see Step 3). It could be stronger (finding 7).

## Adversarial edges (not covered by §3)

- **Empty `--run-id` (decoy-fence bypass):** `--run-id ""` disables the `[bd8:8a]` cross-check
  and returns another run's freeze (finding 1).
- **Missing `--scratchpad-dir` target:** the §2.2 claim of `mutated:removed` is untested
  (finding 2).
- **Freeze row without a top-level `run_id` and with a non-object payload, or with no `payload`
  key:** the crash happens in `find_last_freeze` (:311) or at `frozen["payload"]`, outside a catch
  placed around `verify_against` (finding 3).
- **`members` entry whose `path` is not a string** (e.g. `{"path": 5}`): TypeError from
  `Path / 5`. A path with an embedded NUL raises ValueError, which is not in the spec's
  KeyError/TypeError/AttributeError list (finding 3).
- **Non-string or NaN `digest` in a hand-written payload:** echoed verbatim into `frozen_digest`.
  With `json.dumps` default `allow_nan=True`, NaN produces stdout that is not valid JSON
  (finding 8).
- **Empty `--scratchpad-dir ""`:** `Path("")` is the CWD. This matches the engine's behaviour for
  `scratchpad_dir=""`, so it is noted only, not a finding.

## Findings

1. **MAJOR: `--run-id ""` fails open, and no engine state corresponds to it.**
   - Evidence: oracle.py:312 `if run_id and ev_run and ev_run != run_id` skips filtering when
     `run_id` is falsy. The engine never passes an empty id: run.py:332
     `_resolved_run_id = args.run_id or _uuid_mod.uuid4().hex[:12]`.
   - Effect: argparse's `required=True` accepts `""`. A host calling
     `--run-id "$UNSET"` against a shared log gets `verified` against another run's freeze. That
     breaks §2.2's "exactly what `_oracle_entry_verify` decides for the same run id".
   - Fix:
     - Spec §2.2: add to the rc-2 list "`--run-id` empty or whitespace-only (the engine never
       verifies with an empty id, run.py:332)". The same can optionally apply to
       `--scratchpad-dir`.
     - RED AC9: add case `empty_run_id`. Real freeze under run `A` with an untouched tree, then
       `verify … --run-id ""` must give rc 2, empty stdout, stderr starting `oracle: `. Without
       the check this returns rc 0 `verified`, so the case reddens.
2. **MAJOR: the §2.2 "missing `D` → `mutated:removed`, not rc 2" consequence has no AC (Step 2
   mismatch).**
   - Evidence: spec :51-53 states it. No test removes `D` (test :187-259 only removes a member).
   - Effect: a GREEN that pre-checks `os.path.isdir(D)` and exits 2 passes every test, and this
     is exactly faithfulness question 1.
   - Fix: add AC3/AC2 case (d). Real freeze, `shutil.rmtree(D)`, CLI gives rc 0,
     `outcome=="mutated"`, `token=="mutated:removed"`, `current_digest is None`. Add the same
     scenario to the AC7 parity list. (Mechanism: oracle.py:184 raises from
     `compute_scope_digest` before the `missing` check.)
3. **MINOR: the malformed-payload catch scope is narrower than the case list.**
   - Evidence: spec :73-75 lists "a freeze event whose `payload` is not an object" and "makes
     `verify_against` raise". A freeze row with no top-level `run_id` and `payload: "x"` crashes
     in `find_last_freeze` (oracle.py:311, `("x" or {}).get`). A row with no `payload` key
     crashes at `frozen["payload"]`. `{"path": "\x00"}` raises ValueError.
   - Effect: all of these go to rc 1 with a traceback if GREEN wraps only `verify_against`.
   - Fix: state that the catch encloses `find_last_freeze`, `frozen["payload"]`,
     `verify_against` and the `current_digest` derivation, for
     `(KeyError, TypeError, AttributeError, ValueError)`. Add AC9 cases `payload_str_no_run_id`
     (event without top-level `run_id`) and `no_payload_key`.
4. **MINOR: AC-number drift.**
   - Evidence: spec :29 "(AC12)" should read "(AC11)". Spec :79 "AC-12" should read
     "`test_bd8_l1_oracle.py` AC-12".
   - Fix: edit the text.
5. **MINOR: the `current_digest` derivation is under-specified and AC2(b) does not pin it.**
   - Evidence: spec :65 "<frozen member paths>". verify_against derives them as
     `m["path"] if isinstance(m, dict) else m` (oracle.py:357-360). Test :180-184 asserts
     `current_digest` only for content and removed.
   - Fix: name that derivation in the spec, and note that for `mutated:added`,
     `current_digest == frozen_digest` by construction (membership is inside `digest`; `[bd8:2b]`).
     Assert it in AC2(b). A GREEN emitting `null` for every non-content mutation then reddens.
6. **MINOR: the bd_l3 divergence is not called out.**
   - Evidence: the bd_l3 spec (:51-55) makes missing, directory, non-JSON and non-UTF-8 logs rc 2.
     This spec makes all four verdicts (:51-53, AC3(b), AC4(a-c)) without citing bd_l3.
   - Fix: add one sentence to §2.2: "Unlike bd_l3, log-read failures are verdicts, because
     ORACLE_SPEC §5 (:340) assigns them `E_ORACLE_INDETERMINATE` and the engine reports them that
     way."
7. **MINOR: AC7 and AC11 could pin more.**
   - (a) AC7 omits `utf8`/`nonobj` (test :257-259). Adding them proves at the run.py level the
     §1.2 claim that the reader fix "closes it for run.py too". On base the engine would raise
     instead of returning a refusal.
   - (b) AC11 checks `"argparse" not in vars(oracle)` (test :362) but not `"sys"`, although spec
     :79-80 says no new module attribute. Add `assert "sys" not in vars(oracle)`.
8. **MINOR: stdout validity on hand-written digests.** Spec :64 echoes `digest` verbatim. Use
   `json.dumps(..., allow_nan=False)`, or document that a non-string `digest` is malformed (rc 2),
   so the one stdout line is always valid JSON.
9. **MINOR (follow-up, out of scope): engine-side malformed payload.** `run._oracle_entry_verify`
   (run.py:152) still lets AttributeError/KeyError escape on the AC9 malformed rows, so they map
   to a non-oracle code. File a follow-up issue rather than widening this lot (`run.py` is §5
   out-of-scope).

VERDICT: REJECTED
