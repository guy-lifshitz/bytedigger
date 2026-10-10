# bd#275 (bd#218 s8): validation gate r2 (spec r1 + RED, revised)

Audited: `docs/decisions/2026-10-11-bd275-s8-integrity-prescreen.md`, `engine_py/tests/test_bd218_s8_integrity_prescreen.py` (32 tests; the preflight facts say 0 pass / 32 fail).
Code read again on 76cbac3: `phase_5_integrity.py` L482-552 (the receipt block, `_attempt`, the re-roll), `preflight.py` (`verify_receipt`, `receipt_rung`), the s5 and s7 test files for the staging pattern.
Round-1 verdict: `2026-10-11-bd275-s8-integrity-prescreen-gate-r1.md` (REJECTED, MAJOR-1, MINOR-1..6, adversarial edges 1-8).

## Round-1 closure

| r1 item | status | evidence |
|---|---|---|
| MAJOR-1: nothing protected "classifier never spawned" outside the red-receipt, default-flag case | **CLOSED** | Three new tests, each with a marker-file classifier: `test_AC5_classifier_cmd_with_script_rung_off_...` (red repo, `script_rung: "off"`: marker absent, no event, `prescreen` not called, with a forcing run on the same repo and classifier that does reach `prescreen`); `test_AC5_classifier_cmd_without_receipt_evidence_is_never_spawned[stale, stale-red, missing, ambient]` (forcing run on a red repo first, then the no-evidence run: marker absent, no event, `prescreen` not called); `test_AC5_classifier_cmd_with_invalid_script_rung_is_config_error_and_never_spawned` (one config-error event, marker absent, `prescreen` not called). |
| MINOR-6: retry-budget variable not cleared | CLOSED | the autouse fixture now deletes `HAL_INTEGRITY_VERDICT_RETRY_MAX`. |
| N1: wrong line range in the spec | CLOSED | the spec now cites 484-523, which matches the code. |
| MINOR-1, MINOR-2, MINOR-3, N2, N3, MINOR-4 | OPEN (advisory) | see Findings. |

Straight-copy check (the case the lot asked about). Take the s7 block with only the `gate` key and `_ps_extra` removed. Its run rule is "classifier set, or the rung is on and the receipt is fresh or red".
- Flag off with a classifier set: the copy still runs the pre-screen, spawns the classifier, writes the marker. The flag-off test asserts the marker is absent, no event and no `prescreen` call: it fails.
- No evidence (stale, stale-red, missing, ambient) with a classifier set: the copy spawns the classifier and emits an event. The four-case test fails on all four.
- Red receipt with a classifier and `mode: "enforce"`: the copy passes the classifier through. The first AC5 test fails on the marker and on `seen[0]["classifier_cmd"] is None`.
- A GREEN that passes `classifier_cmd=None` at the one call site and acts only on a config error or a fresh/red receipt passes every one of them.
MAJOR-1 is closed.

## Step 1: spec-internal consistency (literal-token drift)

- `prescreen_verdict`, `preflight_receipt`, `script_rung`, `classifier_status`, `config-error`, `mode="shadow"`, `_rung_rec`, `gate: "integrity"`: consistent across the spec, the RED (`EVENT_KEYS`, `_assert_reject`, `_assert_config_error`) and the existing s7 block. The event key set equals spec item 6.
- MINOR N2 (still open): the header gives the expiry as 2026-10-25 and also says the shared flag in s7 expires 2026-10-24. One flag, two dates.
- MINOR N3 (still open): the sibling list is described as the output of a grep that also returns `test_llm_subprocess_hard_gate.py` and `test_llm_subprocess_allowed_tools.py`. Both run the step with no `git_cwd` (ambient, no rung record, no event), so they cannot be affected, but the claim and the list differ.
- New MINOR N7: AC5 in the spec still describes only the red-receipt case, while the tests now also cover flag off, no evidence and a bad flag with a classifier. The spec design (items 3, 4, 5) already says this; only the AC wording lags. Advisory: add the three cases to the AC5 row.

## Step 1.5: rule-overlap simulation

Decision order at the new block (spec items 2-9): `_rung_rec` set only when the rung ran and returned; flag absent/None/"shadow" on, "off" off, anything else a config error, non-dict `prescreen` default; act iff config error, or flag on and status in {fresh, red}; `classifier_cmd=None` and `mode="shadow"` always.

| script_rung | preflight_rung | receipt | classifier | prescreen.mode | first-matching branch, outcome | RED |
|---|---|---|---|---|---|---|
| default / shadow / None / non-dict | default | red/stub | none | any | act; reject/script, clf off, `gate integrity` | AC1, AC4 x5, AC8, AC9, AC10 |
| default | default | red/stub | set | enforce | act; classifier forced to None, mode shadow; reject/script, no spawn | AC5 first |
| default | default | fresh | none | any | act; escalate/None | AC2 |
| default | default or `produce: False` | stale, stale-red, missing | none | any | no act | AC3 x3 |
| default | default | `receipt_rung` raises / ambient / mode off / mode bogus | none | any | `_rung_rec` None; no act | AC3 x4 |
| default | `produce: False` | stale, stale-red, missing; ambient | **set** | any | no act, no spawn | AC5 no-evidence x4 |
| `"off"` | default | red | none | any | flag off; no act | AC4 off, AC9 |
| `"off"` | default | red | **set** | any | flag off; no act, no spawn | AC5 flag-off |
| `"bogus"` / `7` | default or `mode: off` | red | none | any | one config-error event, no `prescreen` | AC4 x4 |
| `"bogus"` | default | red | **set** | any | one config-error event, no spawn | AC5 bad flag |
| default | default | red | none | `prescreen` / `script_findings` patched to raise | one config-error event, result passes through | AC6 x2 |
| default | default | red, `prescreen.timeout_s: 0` | none | any | spec: `timeout_s` not read, normal reject | not tested (MINOR-3) |

Every tested row gives the spec's outcome at the first matching branch. In `check_ladder.prescreen`, the no-classifier check comes before the enforce/skip branch, and `majors` before the classifier-reject branch, so with mode forced to shadow and no classifier a red receipt is always reject/script and a fresh one escalate/None.

## Step 2: section 2 (design) vs section 3 (acceptance criteria) cross-check

- Item 1 -> AC3. Item 2 -> AC4. Item 3 -> AC2, AC3, AC4, AC5 (all four sub-cases). Item 4 -> AC4, AC5 bad-flag, AC6. Item 5 -> AC5. Items 6-7 -> AC1, AC2, AC4. Item 8 -> AC8, AC1, AC9. Item 9 -> AC3, AC4. Once per call -> AC10. Billing modes -> AC7. Siblings -> AC11.
- Every terminal / error branch in the design has an AC and a test; every AC post-condition has a producing path. The r1 gap (AC wording silent on classifier plus no evidence) is now covered by tests and only lags in the AC5 wording (N7).

## Step 3: RED adequacy

- Counts (32): AC1 1, AC2 1, AC3 7, AC4 1+5+4 = 10, AC5 1+1+4+1 = 7, AC6 2, AC7 1, AC8 1, AC9 1, AC10 1. Total 1+1+7+10+7+2+1+1+1+1 = 32, matching the facts. AC11 is a run of the sibling files (all eleven pass today).
- Fail at assert time, not collect time: modules are imported lazily. Today the integrity step never touches `check_ladder`, so the new AC5 tests fail on their forcing assert (`len(seen) == 1` or the first `_assert_reject`), the AC3 forcing asserts fail, and the config-error test fails on the event count. The facts confirm 32 fail, none at collection.
- No stub-passability: `check_ladder.prescreen` is the real function (a recording spy delegates to it) except in AC6, where the patch is a dependency of the unit under test; `script_findings` is real except in AC6. Receipts come from the real producer in real tmp git repos; pre-staged receipts use `run_engine_preflight(..., phase="green")`, the same call the s5 tests use. Only the model call and the event sink are replaced.
- Walk: does a CORRECT implementation pass each test?
  - AC1, AC2, AC8, AC9, AC10: yes (unchanged from r1). The re-roll default budget is 1 and the variable that overrides it is now cleared.
  - AC3 (7 cases): yes. Pre-staged states are built before the forcing drive; `{"produce": False}` takes the read-only path at L506; `receipt_rung` as a module attribute is patchable (L513). For stale-red, `verify_receipt` returns stale before it looks at `ok`, and `receipt_rung` keeps `red_step` for stale, so the staging asserts hold and a correct block does not act.
  - AC4: yes, all 1 + 5 + 4 cases.
  - AC5 first test: yes; the marker stays absent because `classifier_cmd=None`; keyword-argument reads match the spec's call shape.
  - AC5 flag-off: yes. The forcing run (same repo, classifier set, flag default) emits and calls `prescreen` once; the marker is absent in both runs for a correct block. The spy is cleared between the runs.
  - AC5 no-evidence x4: yes. The classifier script is created before the repos, the red repo drives first as the forcing run, the case repo second. Ambient has no `git_cwd`, so `_rung_rec` is `None`; the correct block does nothing.
  - AC5 bad flag: yes; one config-error event, no `prescreen` call, marker absent.
  - AC6, AC7: yes (unchanged from r1). The model-call kwargs contain no repo path, so per-environment repos do not disturb the AC7 equality.
- Wrong-implementation probes the RED catches: block inside `_attempt` (AC10); event after the model call (AC8); `prescreen` key added to `extra_data` (AC1, AC2, AC4, AC5, AC9); stale counted as evidence (AC3); enforce honoured (AC5); classifier spawned on a red receipt, with the flag off, with no evidence, or alongside a bad flag (AC5, four tests); config error also calling `prescreen` (AC4, AC5); `"shadow"` / `None` treated as errors (AC4); `gate` key missing (every event assert).
- Probes not caught, all advisory: invalid `prescreen.timeout_s` turning a reject into a config-error event (MINOR-3); `cycle` hard-coded to 1 (MINOR-2); a returned `{"status": "error"}` record (MINOR-1).

## Step 4: reachability (section 1y)

- AC1, AC2, AC5 (all), AC7, AC8, AC9. Point: the new `check_ladder.prescreen(...)` call and `_emit_safe("prescreen_verdict", ...)` between L523 and `_attempt` (L525). Host: `phase_5_integrity._invoke_integrity_llm`. Test path: `_drive` -> `_invoke_integrity_llm(_ctx(git_cwd=<real tmp repo>), _prev)`. Reachable.
- AC3 / AC5 no-evidence. Point: the non-acting path (`_rung_rec` None or stale/missing). Host: same. Test path: pre-staged receipts with `produce: False`, an ambient cwd, a patched `receipt_rung`. Reachable.
- AC4 / AC5 bad flag. Point: the flag read and the config-error event, outside the `mode == "off"` guard at L489. Host: same. Test path: `org_config["prescreen"]`. Reachable.
- AC6. Point: the new try/except. Host: same. Test path: module attributes of `check_ladder`. Reachable.
- AC10. Point: the re-roll at L548. Host: same. Reachable.
- Siblings: no sibling asserts the full event list of the integrity step; the s7 event key set is untouched.

## Adversarial edges (not covered by the section 3 AC table)

1. **Invalid `prescreen.timeout_s` (0, negative, a string)** with a red receipt: the spec hard-codes the default timeout, so the event must still be a normal reject; a copy of the s7 code reads the value and gives a config-error event. No test.
2. **`receipt_rung` returning a record** with `status: "error"`, a non-dict, or a missing `red_step`: spec item 1 sets `_rung_rec` right after the call returns, but does not say whether a non-dict is "no evidence" or a config error. No test.
3. **Cycle above 1 or a missing `cycle` key**: the event's `cycle` must come from the prior step's data (default 1). Every test uses cycle 1, so a hard-coded 1 passes.
4. **Flag value variants** `"OFF"`, `" off "`, `True`, `0`: all fall under "any other value" and give one config-error event; only `"bogus"` and `7` are tested. Low risk.

## Findings

1. MINOR-1: no test for a non-raising bad receipt record (adversarial 2). Advisory.
2. MINOR-2: no test pins `cycle` from the prior step's data (adversarial 3). Advisory.
3. MINOR-3: no test for an invalid `prescreen.timeout_s` (adversarial 1). Advisory.
4. MINOR-4: the first AC5 test reads `classifier_cmd` and `mode` from keyword arguments; the spec writes them as keywords, so a correct implementation passes. Keep as is.
5. MINOR N2 / N3 / N7: spec tidy-ups (one expiry date, the sibling list claim, the AC5 wording). No test depends on them.

No new MAJOR. The round-1 MAJOR is closed and none of the edits introduced a test a correct implementation would fail.

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

VERDICT: APPROVED

<!-- verdict-anchor
spec: docs/decisions/2026-10-11-bd275-s8-integrity-prescreen.md sha256:683975072f87b936e5cafcd11f612ee6ded02d75b46e2f1cb2d475b5172ec502
red: engine_py/tests/test_bd218_s8_integrity_prescreen.py sha256:efb51b7e4e6f1b1a21c578dd00b63d291399d9c5db0963785ecad068f25c9175
-->
