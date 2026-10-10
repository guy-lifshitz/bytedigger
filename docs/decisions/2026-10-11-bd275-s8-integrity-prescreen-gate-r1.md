# bd#275 (bd#218 s8): validation gate r1 (spec r1 + RED)

Audited: `docs/decisions/2026-10-11-bd275-s8-integrity-prescreen.md` (r1), `engine_py/tests/test_bd218_s8_integrity_prescreen.py` (26 tests; the preflight facts say 0 pass / 26 fail).
Code read on 76cbac3: `phase_5_integrity.py` (L459-552, the receipt block L484-523 and `_attempt`), `phase_5_implement.py` L6930-7036 (the s7 block being mirrored), `check_ladder.py` (`script_findings`, `prescreen`, `_validate`, `run_classifier`), `preflight.py` (`receipt_rung`, `verify_receipt`), `lib/git_cwd.py`, `phase_workflows_common.reroll_until_verdict`, and the s5 test file whose harness this RED reuses.
Format follows `2026-10-10-bd218-s7-prescreen-script-rung-gate-r1.md` / `-r2.md`.

## Step 1: spec-internal consistency (literal-token drift)

- `prescreen_verdict`, `preflight_receipt`, `script_rung`, `classifier_status`, `config-error`, `mode="shadow"`, `_rung_rec`, `gate: "integrity"`: consistent across the spec, the RED (`EVENT_KEYS`, `_assert_reject`, `_assert_config_error`) and the existing s7 block. The event key set in the RED equals spec item 6 (the s7 ten keys plus `gate`).
- MINOR N1: the spec's problem statement says the receipt block is at "lines 486-526"; on 76cbac3 it is L484-523. Cosmetic.
- MINOR N2: the header gives the expiry as 2026-10-25 and also says the flag is "shared with s7, whose flag expires 2026-10-24". One shared flag, two dates. If the s7 flag is removed on 10-24 the s8 read of the same key is orphaned for a day. Advisory: use one date.
- MINOR N3: the spec says the sibling list is the output of `grep -l '_invoke_integrity_llm|check_ladder|prescreen|preflight_receipt' engine_py/tests`. That grep also returns `test_llm_subprocess_hard_gate.py` and `test_llm_subprocess_allowed_tools.py`, which are not listed. Both call the integrity step with no `git_cwd` (ambient, so no rung record, no event) and cannot be affected, but the list should be complete or the claim reworded.
- Spec item 5 says `timeout_s=check_ladder.DEFAULT_TIMEOUT_S` always, while the s7 block it mirrors reads `prescreen.timeout_s`. The spec is internally consistent; it is a drift trap for the implementation (see adversarial edge 2 and MINOR N5).

## Step 1.5: rule-overlap simulation

Decision order at the new block, per spec items 2-9:
1. `_rung_rec` is set only when the rung ran and returned (off, ambient, config-error and a raising rung leave `None`).
2. Flag: absent / `None` / `"shadow"` on; `"off"` off; anything else (non-str included) error; non-dict `prescreen` is default.
3. Act iff flag error, or (flag on and `_rung_rec.status` in {fresh, red}).
4. Flag error: one config-error event, no `prescreen`. Otherwise `prescreen(script_findings(_rung_rec), classifier_cmd=None, mode="shadow", timeout_s=default)`; any raise gives one config-error event.

| script_rung | preflight_rung | receipt | classifier cfg | prescreen.mode | first-matching branch, outcome | RED |
|---|---|---|---|---|---|---|
| default / shadow / None / non-dict | default | red/stub | none | any | act; reject/script, clf off, `gate integrity` | AC1, AC4 x5, AC8, AC9, AC10 |
| default | default | red/stub | set | enforce | act; `classifier_cmd=None` forced so `prescreen` never reaches the spawn branch; reject/script, clf off, mode shadow | AC5 |
| default | default | fresh | none | any | act; findings `[]`; escalate/None, clf off | AC2 |
| default | default | stale, stale-red, missing | none | any | no act | AC3 x3 |
| default | `produce: False` | stale/missing (pre-staged) | none | any | no act (reads staged state) | AC3 x3 |
| default | default | `receipt_rung` raises | none | any | `_rung_rec` None; no act | AC3 error |
| default | default, ambient cwd | n/a | none | any | `_rung_rec` None; no act | AC3 ambient |
| default | `mode: off` / `mode: bogus` | n/a | none | any | `_rung_rec` None; no act | AC3 x2 |
| `"off"` | default | red | none | any | flag off; no act | AC4 off |
| `"off"` | default | red | **set** | any | flag off; no act, nothing spawned (spec item 3) | **not tested** |
| default | default | stale / ambient / missing | **set** | any | no evidence; no act, nothing spawned (spec item 3) | **not tested** |
| `"bogus"` / `7` | default | red | none | any | flag error; one config-error event | AC4 x2 |
| `"bogus"` / `7` | `mode: off` | `_rung_rec` None | none | any | flag error acts regardless of evidence; one config-error event | AC4 x2 |
| `"bogus"` | default | red | set | any | flag error; config-error event, no spawn | **not tested** (spec item 4 covers it) |
| default | default | red | none | `prescreen` patched to raise | try fails; config-error event | AC6 |
| default | default | red | none | `script_findings` patched to raise | inside the same try; config-error event | AC6 |
| default | default | red, `prescreen.timeout_s: 0` | none | any | spec: `timeout_s` is not read, so a normal reject event | **not tested** |

Every row that is tested yields the spec's outcome at the first matching branch. In `check_ladder.prescreen`, `classifier_cmd is None` is checked before the enforce/skip branch, and `majors` before the classifier-reject branch, so with `mode` forced to shadow and no classifier a red receipt is always reject/script and a fresh one always escalate/None. The four rows marked "not tested" are where a straight copy of the s7 block (which keys the run on `classifier_cmd is not None` and reads `timeout_s`) would diverge from this spec while passing every test in the RED. See MAJOR-1 and MINOR-5.

## Step 2: section 2 (design) vs section 3 (acceptance criteria) cross-check

- Item 1 (`_rung_rec`) -> AC3 (error, ambient, off, config-error leave it `None`). Item 2 (flag reading) -> AC4. Item 3 (act rule) -> AC2, AC3, AC4. Item 4 (config error, no `prescreen` call) -> AC4, AC6. Item 5 (`classifier_cmd=None`, shadow) -> AC5. Items 6-7 (event shapes) -> AC1, AC2, AC4. Item 8 (order, `extra_data` untouched) -> AC8, AC1, AC9. Item 9 (rung off, typo still reports) -> AC3, AC4. "once per step call" -> AC10. Both billing modes -> AC7. Add-only -> AC9. Siblings -> AC11.
- Every terminal / error branch in the design has an AC, and every AC post-condition has a producing path in the design.
- Gap in the AC wording (not the design): AC3 and AC4 state "no event" for the no-evidence and `"off"` cases without saying what happens when a classifier is also configured. Item 3 of the design says "otherwise nothing is called or emitted" and item 5 says no process is spawned "whatever the config says". AC5 only exercises the red-receipt, default-flag case. Carried into MAJOR-1.

## Step 3: RED adequacy

- Counts (26): AC1 1, AC2 1, AC3 7 (parameter cases), AC4 1 + 5 + 4 = 10, AC5 1, AC6 2, AC7 1, AC8 1, AC9 1, AC10 1. AC11 is a run of the sibling files, as the docstring says; the preflight facts show all eleven sibling files green today. Total 1+1+7+10+1+2+1+1+1+1 = 26, matching the facts.
- Fail at assert time, not collect time: modules are imported lazily; against today's code the integrity step never calls `check_ladder`, so every test fails on a behaviour assert (no event) or on a forcing assert that is true only after GREEN. The AC10 and AC3 forcing asserts about the gate call count already hold today and the later assert fails. None is vacuous.
- No stub-passability: `check_ladder.prescreen` is the real function (a recording spy delegates to it) except in AC6, where the patch is a dependency of the unit under test, and `script_findings` is real except in AC6. Receipts are produced by the real producer in real tmp git repos. Only the gate call and the event sink of the integrity step are replaced, the same seams the s5 tests use.
- Walk: does a CORRECT implementation pass each test?
  - AC1, AC2, AC8, AC9: yes. The repos are built exactly as in s5 (red/stub via `STUB_SRC`, fresh via `RED_SRC`); the in-step producer writes the green receipt, then the rung is read. AC9's dict equality of the two kwargs holds, because the only difference between the runs is the added event; both runs re-run the producer on the same repo with the same result.
  - AC3 stale / stale-red / missing with `produce: False`: yes. `_staged` builds and edits the repo before the first drive, so the staged state is not disturbed. With `{"produce": False}` the config dict has `mode` absent, so it defaults to verify and `produce` is a bool, so the step takes the "read only" path and reads the staged record: stale (red_step kept as `stub` for stale-red) or missing. No evidence, so no `prescreen` call and no event. The spy installed before the forcing drive stays live and is cleared between drives. A correct implementation passes. An implementation that treats stale as evidence, or keys on `red_step`, fails the stale-red case.
  - AC3 error: `receipt_rung` is patched on the module after the forcing drive; the step calls it as a module attribute, so the patch is effective; the `except` leaves `_rung_rec` `None`. Pass.
  - AC3 ambient: no `git_cwd` and a scratch dir with no `.git` above it gives source `cwd`, which is ambient. Pass. (The tmp tree is outside any repo; the check goes through `is_ambient_git_cwd`, not through the process cwd.)
  - AC3 rung-off / config-error: `_rung_rec` stays `None`. Pass.
  - AC4: default-equivalent values (shadow, None, str, list, None) all take the default branch; the flag-error cases give one config-error event with cycle 1, and `bad=7` is a non-str. Pass. `["x"]`-style unhashable values are not tested but `in` over a tuple does not hash, so they are safe.
  - AC5: the spy records `classifier_cmd=None` and `mode="shadow"` only if the implementation passes them as keyword arguments, which the spec states (item 5). The marker stays absent. Pass for the spec's call shape; a positional call would fail the `seen[0].get(...)` read, which is a stricter reading than the spec requires (MINOR N4).
  - AC6: both patches are on the `check_ladder` module attribute, which the spec's `from bytedigger_engine import check_ladder` supports; `script_findings` is called inside the try, so a raise yields the config-error event, and the result passes through with `preflight` in its data. Pass.
  - AC7: the model name and timeout come from config, not from credentials, so identical kwargs across the three environments holds for a correct implementation; an implementation that branches on the environment fails. Per-environment repos differ only in path, which is not part of the kwargs or events. Pass.
  - AC10: the first reply has no standalone verdict, the existing re-roll (default budget 1) calls again, and the new block is outside `_attempt`, so one event. Pass. Caveat: the re-roll budget is read through the host config seam from `HAL_INTEGRITY_VERDICT_RETRY_MAX` first, and the autouse fixture does not clear it. If that variable is set to 0 in a developer shell the test fails for a reason unrelated to GREEN (MINOR N6).
- Wrong-implementation probes that the RED catches: block inside `_attempt` (AC10); emitted after the gate call (AC8); `prescreen` key added to the gate's `extra_data` (AC1, AC2, AC4, AC5, AC9); stale counted as evidence (AC3); error/missing/None treated as fresh (AC3, partially); enforce honoured (AC5); config error also calling `prescreen` (AC4); `"shadow"` / `None` treated as an error (AC4); `gate` key missing or wrong (every event assert); a classifier spawned when the receipt is red (AC5 marker).
- Wrong-implementation probes that the RED does NOT catch (the s7 block copied verbatim, then only the `gate` key and `_ps_extra` removed):
  - run condition `_ps_sr_bad or _ps_clf_cmd is not None or _ps_evidence`: with `script_rung: "off"` and a classifier set, or with a classifier set and no evidence, the classifier command is spawned and an event is emitted. No test sets a classifier outside the red-receipt case.
  - `timeout_s` read from `prescreen`: `timeout_s: 0` turns a reject into a config-error event. No test sets an invalid `timeout_s`.
  - `cycle` hard-coded to 1 passes everything, because every test uses cycle 1.

## Step 4: reachability (section 1y)

- AC1, AC2, AC5, AC7, AC8, AC9. Point: the new `check_ladder.prescreen(...)` call and `_emit_safe("prescreen_verdict", ...)`, placed after `_emit_safe("preflight_receipt", ...)` (L523) and before `_attempt` / `reroll_until_verdict` (L525-552). Host: `phase_5_integrity._invoke_integrity_llm`. Test path: `_drive` -> `p5i._invoke_integrity_llm(_ctx(git_cwd=<real tmp repo>), _prev)`. Reachable.
- AC3 error. Point: the `except` at L519 (`_rung_rec` unset). Host: same function. Test path: `preflight.receipt_rung` patched as a module attribute (called as `preflight.receipt_rung` at L513). Reachable.
- AC4 config error. Point: the flag read and the config-error event. Host: same. Test path: `org_config["prescreen"]`. Reachable, including with `preflight_rung.mode: "off"`, because the new block sits outside the `if not (... mode == "off")` guard at L489.
- AC6. Point: the new try/except. Host: same. Test path: module attributes of `check_ladder`. Reachable.
- AC10. Point: the re-roll in `reroll_until_verdict` (L932-968), which calls `_attempt` again. Host: same function. The re-roll's own `_emit_safe("integrity_verdict_reroll")` goes through the unpatched emitter in `phase_workflows_common`; it is not in the test's event list and does not disturb AC8 (a single call). Reachable.
- Siblings: `test_bd218_s5_green_receipt.py` reads events through a `preflight_receipt` filter and never asserts the full event list, so an added `prescreen_verdict` does not break it. The s7 test and the pre-GREEN event key set are untouched. Reachable and safe.

## Adversarial edges (not covered by the section 3 AC table)

1. **Classifier configured, flag off.** `script_rung: "off"` plus a real `prescreen.classifier_cmd` and a red receipt: nothing must be spawned and no event emitted. AC4 "off" and AC5 are each tested in isolation only. A copy of the s7 run condition spawns the classifier here and passes every test.
2. **Classifier configured, no evidence.** Ambient cwd, stale, missing or rung-off, with `classifier_cmd` set: item 3 says nothing is called or emitted. A copy of the s7 run condition (`classifier_cmd is not None` runs the pre-screen) spawns the classifier and emits an event. No test.
3. **Invalid `prescreen.timeout_s` (0, negative, a string).** The spec hard-codes the default timeout, so the integrity event must still be a normal reject; the s7 code would turn it into a config-error event. No test.
4. **`receipt_rung` returns a record rather than raising**, with `status: "error"`, or a non-dict, or a record missing `red_step` (the existing block subscripts `red_step` after `_rung_rec` could already be set, so the preflight event says `error` while a `_rung_rec` with status red could still reach `prescreen`). Spec item 1 says `_rung_rec` is set "right after `receipt_rung` returns"; item 3 acts on `_rung_rec["status"]`, but the spec does not say whether a non-dict or a key-less record is guarded inside the try. No test.
5. **Cycle above 1 and a missing `cycle` key.** `cycle` in the event must come from `prev.data` (default 1). Every test uses cycle 1, so a hard-coded 1 passes.
6. **Typo in `script_rung` combined with a classifier** (`"bogus"` plus `classifier_cmd`): one config-error event and no spawn is the spec's rule; no test uses the marker script with a bad flag.
7. **Flag value variants** `"OFF"`, `" off "`, `True`, `0`: all fall in "any other value", so one config-error event; only `"bogus"` and `7` are tested. Low risk.
8. **Stale receipt that was red with the flag off** and the reverse (fresh, flag default): no cross-effect; already implied by the matrix, listed for completeness only.

## Findings

1. **MAJOR-1** (run rule not guarded, AC3 / AC4 / AC5). The spec pins two invariants that no test protects: with the flag off, or with no fresh/red receipt, a configured `prescreen.classifier_cmd` must not be spawned and no event emitted (items 3 and 5, and the AC5 title "classifier never used"). The RED sets a classifier only with a red receipt and the default flag. The obvious implementation, a copy of the s7 block, runs the pre-screen on `classifier_cmd is not None`, spawns the classifier in the two uncovered cases, and passes all 26 tests. The spawn of a subprocess at the integrity gate in a state the spec calls "nothing is called" is exactly the failure this slice must not allow. Fix, tests only: (a) extend AC5 with a parameter for `script_rung: "off"` plus the marker classifier on a red repo (assert marker absent, no event); (b) a parameter with the marker classifier plus an ambient run or a stale/missing staged receipt (assert marker absent, no event, `prescreen` not called). Each needs the forcing assert that the marker does appear when the classifier is called directly (or reuse the red-receipt case already in AC5 as the forcing run in the same test).
2. MINOR-1: no test for a non-raising bad receipt record (adversarial 4), nor for `status: "error"` returned. Advisory: patch `receipt_rung` to return `{"status": "error", "phase": "green", "red_step": None}` and assert no event; and state in the spec whether a non-dict record is a config-error or "no evidence".
3. MINOR-2: no test pins `cycle` taken from `prev.data` (adversarial 5). Advisory: one drive with `cycle: 2` in the prior step's data.
4. MINOR-3: no test for invalid `prescreen.timeout_s` (adversarial 3). Advisory: red repo plus `prescreen: {"timeout_s": 0}` expects the normal reject event.
5. MINOR-4 (was N4 in the walk): AC5 reads `seen[0].get("classifier_cmd")` and `seen[0].get("mode")` from keyword arguments, so a correct implementation that passes them positionally would fail. The spec writes keyword arguments, so this is consistent; keep as is or make the spy normalise via `inspect.signature`.
6. MINOR-5: the spec text items N1-N3 above (line numbers, expiry date, sibling list) should be tidied. No test depends on them.
7. MINOR-6: AC10 depends on `HAL_INTEGRITY_VERDICT_RETRY_MAX` being unset. Advisory: delete it in the autouse fixture next to `HAL_KNOWN_REDS_TODAY`.

MAJOR-1 blocks. The remaining findings are advisory.

## Checklist

- AC1: PASS
- AC2: PASS
- AC3: PASS
- AC4: FAIL
- AC5: FAIL
- AC6: PASS
- AC7: PASS
- AC8: PASS
- AC9: PASS
- AC10: PASS
- AC11: PASS

VERDICT: REJECTED
