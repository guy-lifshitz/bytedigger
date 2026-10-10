# bd#218 s7: validation gate r2 (spec r2 + RED)

Audited: `docs/decisions/2026-10-10-bd218-s7-prescreen-script-rung.md` (r2), `engine_py/tests/test_bd218_s7_script_rung.py` (51 tests; facts say 0 pass / 51 fail), and the amendment `engine_py/tests/test_bd218_s1_preflight_rung.py::test_AC7`.
Code read again on f7e7aa7: `check_ladder.py` (whole file), `phase_5_implement._invoke_validation_llm` (L6813-6965). Siblings grepped for `prescreen` / `__all__` pins: `test_bd141_check_ladder.py`, `test_bd164_preflight.py`, `test_bd218_s1/s2/s5`.
Round-1 verdict: `2026-10-10-bd218-s7-prescreen-script-rung-gate-r1.md` (MAJOR-1, MAJOR-2, MINOR-1..7, adversarial edge 6).

## Round-1 closure

| r1 item | status | evidence |
|---|---|---|
| MAJOR-1: AC4[stale] staged after `_drive` patched `run_engine_preflight` | **CLOSED** | test L307-309: `red_repo` and `case_repo` (stale / stale-red / missing) are now staged before the forcing `_drive` (L312). A comment pins the reason. |
| MAJOR-2: unknown `script_rung` with a classifier is undefined | **CLOSED** | op3 L51 now says: exactly one config-error-shaped event that replaces the verdict, `prescreen` not called, classifier not spawned even when `classifier_cmd` is set, no `extra_data["prescreen"]`, gate once. AC6 says the same. RED `test_AC6_unknown_value_with_classifier_is_one_config_error_and_no_spawn[str,int]` uses a marker file (forcing: the marker appears under a valid config) and `_assert_config_error` (exactly one event). |
| MINOR-1: explicit `"shadow"` / `None` untested | CLOSED | `test_AC6_shadow_and_none_script_rung_behave_as_default[shadow,none]`. AC6 text lists both. |
| MINOR-2: rung error path untested at phase 5 | PARTIAL | `AC4[error]` (`receipt_rung` raising) added. That takes the `except` at L6913, so `_pr_rec` is never bound and `_rung_rec` is None. A `receipt_rung` that *returns* `{"status": "error"}` is still untested (see N2). |
| MINOR-3: op1 "non-empty" vs AC1 "blank" | CLOSED | op1 L35: "missing, not a string, or blank → `unknown`". |
| MINOR-4: `_rung_rec` vs `_pr_rec` wording | CLOSED | op2 L41 names `_pr_rec` and states when `_rung_rec` is None. |
| MINOR-5: the fresh/red evidence rule is duplicated at the call site | OPEN (advisory) | op2 still hard-codes `status in ("fresh", "red")` in phase 5 (N1). |
| MINOR-6: invalid `timeout_s` with no classifier hides the script verdict | CLOSED (stated + tested) | op3 L52 states it. `test_AC6_invalid_timeout_without_classifier_is_config_error`. |
| MINOR-7: s1 AC7 has no spy on the classifier | OPEN (advisory) | s1 `test_AC7` still infers "no classifier process" from status `off`. That is sound, because `prescreen` with `classifier_cmd=None` short-circuits to `_result("off")` and never reaches `run_classifier`. |
| Adversarial 6: stale-was-red at phase-5 level | CLOSED | `AC4[stale-red]` (STUB_SRC, stale receipt with `red_step == "stub"`). AC4 text: "including a stale receipt that was red". |

## Step 1: spec-internal consistency (literal-token drift)

- `prescreen_verdict`, `preflight_receipt`, `script_rung`, `classifier_status`, `config-error`, `mode="shadow"`, `_pr_rec` / `_rung_rec`, `preflight:<red_step>`, `preflight:unknown`: consistent across §2, §3 and the RED, and match the code (event keys L6845-6856 = `EVENT_KEYS`; config-error shape L6863-6868 = `_assert_config_error`).
- `config-error` appears in two contexts: preflight-rung status (AC4 case) and prescreen `classifier_status` (op3, AC6, AC10). Both are existing meanings, used correctly. No drift.
- MINOR N3: op2 L44 says a classifier that is "set but unreachable, timing out or **malformed**" gives status `error`/`timeout`. This holds for a malformed *answer* (`_parse_answer` gives `error`). It does not hold for a malformed *command*: `classifier_cmd: "not-a-list"` makes `_validate` raise, which gives config-error and drops the script verdict. L18 (pinned) requires that. The text should say "malformed answer". This is a wording issue only: no AC or test asserts the command reading.
- The RED file docstring still says "spec r1". Cosmetic.

## Step 1.5: rule-overlap simulation

Decision order at the call site, as op2/op3 define it:
1. Read `script_rung`. If the value is unknown, emit one config-error event and stop: no `prescreen`, no spawn.
2. Compute `findings`: `script_findings(_rung_rec)` when the rung is on, `[]` when off.
3. Run the pre-screen iff a classifier is configured, or the rung is on and `_rung_rec.status ∈ {fresh, red}`.
4. If `prescreen` raises, emit the config-error event.

| script_rung | classifier | rung rec | first-matching branch -> outcome | RED |
|---|---|---|---|---|
| absent/None/"shadow"/non-dict ps | no | red | step 3 runs; prescreen `majors` -> reject/script, clf off | AC2, AC6 shadow/none, AC6 non-dict, AC8, AC9 |
| default | no | fresh | escalate/None, clf off | AC3 |
| default | no | stale, stale-red, missing, None (error/ambient/rung-off/rung-config-error) | step 3 false -> nothing | AC4 x7 |
| default | yes | red | prescreen, shadow -> classifier runs; `majors` checked first -> reject/script whatever the classifier status | AC5 (ok), AC7 (error/timeout) |
| default | no | red, `timeout_s: 0` | step 4 -> config-error | AC6 invalid-timeout |
| "off" | no | red | nothing | AC6 off |
| "off" | yes | red | findings [] -> escalate, clf ok | AC6 off+classifier |
| unknown | no | red | step 1 -> one config-error event | AC6 unknown x4 |
| unknown | yes | red | step 1 -> one config-error event, no spawn | AC6 unknown+classifier x2 |
| unknown | no | ambient/stale/rung-off | step 1 (op3 is unconditional) -> one config-error event | untested (N4) |
| default | yes | ambient | today's event, findings [] | pinned L17/L17b |
| default | no | (patched prescreen raises) | step 4 -> config-error | AC10 |

In every row the first matching branch gives the outcome the spec expects. In `check_ladder.prescreen`, `classifier_cmd is None` is checked before the enforce/skip branch, and `majors` before the classifier-reject branch. So with mode forced to shadow, the outcome is reject/script whenever a MAJOR finding exists.

## Step 2: §2 vs §3 cross-check

- op1 -> AC1. op2 reorder -> AC9. op2 feed -> AC2/AC5/AC11. op2 run rule -> AC2/AC3/AC4. op2 classifier optional -> AC7. op2 shadow/enforce -> AC5. op2 event/extra_data -> AC2/AC3/AC5/AC8. op2 exception (terminal) -> AC10 and the AC6 invalid-timeout test. op3 off / default / unknown / unknown+classifier / non-dict -> AC6. op3 `prescreen` raising -> AC6 invalid-timeout test (and AC10). Add-only -> AC11. Siblings and the s1 amendment -> AC12.
- Every terminal/error branch in §2 now has an AC, and every AC post-condition has a producing §2 path. The unknown-value branch has its full post-condition (one event, no spawn, no `extra_data` key, gate once) in both op3 and AC6.
- MINOR N5: the invalid-`timeout_s` case is defined in op3 L52 and tested under the `AC6` name, but the AC6 row text does not list it. AC10 ("`prescreen` raising -> config-error") covers it in substance. Advisory: add it to the AC6 or AC10 wording so the table matches the test names.

## Step 3: RED adequacy

- Counts: AC1 17, AC2 1, AC3 1, AC4 7, AC5 1, AC6 14 (1+1+4+2+2+1+3), AC7 3, AC8 3, AC9 1, AC10 1, AC11 2. Total 51, matching the facts (0 pass / 51 fail). AC12 is verified by running the sibling files, as the docstring states.
- Failures happen at assert time, not collect time. Modules are imported lazily (`_cl()/_pf()/_p5()`), and `script_findings` raises AttributeError inside the test body. Against today's code, each phase-5 test fails on a behaviour or forcing assert:
  - AC2/3/8/9/10/11, AC6 shadow/none/non-dict/invalid-timeout/unknown: no classifier means no `prescreen` call and no event.
  - AC4: the forcing `len(seen) == 1` fails.
  - AC5/AC7: the event is escalate/None because findings are `[]`.
  - AC6 off+classifier: the forcing `c0 ... findings` is empty.
  - AC6 unknown+classifier: the forcing passes today, then the marker is re-created because `script_rung` is ignored, so the assert fails.
  None of these is vacuous.
- MAJOR-1 recheck: in `AC4[stale|stale-red|missing]` the receipts are staged by the real `run_engine_preflight` before any `_drive`, so `_staged`'s `res["exit_code"]` is read from a real result. `AC4[error|rung-off|config-error|ambient]` stage nothing case-specific. The spy is installed before the forcing drive and stays installed for the case drive (monkeypatch persists), and `seen.clear()` separates the two drives. The test is now green-able.
- No stub-passability. `check_ladder.prescreen` is real everywhere except AC10, where the patch is a dependency of the unit under test (phase 5), not the unit itself. `script_findings` is never mocked. Receipts are real. Only the gate LLM, the event sink and the in-function producer are replaced, the same seams s1/s2 use.
- Wrong-implementation probes:
  - Always emit: caught by AC4 and AC6-off.
  - Stale counted as a finding, or keyed on `red_step`: caught by AC1 stale-was-red and AC4 stale-red.
  - Unknown value handled as plain "off" with a classifier: caught by the marker and the event shape (status would be `ok`, not `config-error`).
  - Unknown value giving two events: caught by `len == 1`.
  - Strict typo check that rejects `"shadow"` / `None`: caught.
  - `DEFAULT_TIMEOUT_S` substituted when there is no classifier: caught by the invalid-timeout test (the spec pins that behaviour).
  - Enforce honoured: caught by AC5.
  - Block not moved: caught by the exact order list in AC9.
  - `prescreen` key added without a classifier: caught by AC2/3/6/10.
- Remaining holes: N2 and N4, both MINOR (below).

## Step 4: reachability (§1y)

- AC2/3/5/6/7/8/9/11. Point: the new `check_ladder.script_findings(_rung_rec)` call, the `script_rung` read and `_emit_safe("prescreen_verdict", ...)`, all placed after `_emit_safe("preflight_receipt")` (L6917) and before `invoke_llm_subprocess` (L6918). Host: `phase_5_implement._invoke_validation_llm`. Test path: `_drive` -> `p5._invoke_validation_llm(_ctx(git_cwd=<real tmp repo>), _prev)`, with the receipt staged by the real producer. Reachable.
- AC4[error]. Point: the `except` at L6913, so `_pr_rec` is never assigned. Reached through `preflight.receipt_rung` as a module attribute (L6907), patched in the test. Reachable.
- AC10. Point: the existing `except` (L6861 shape), reached through `check_ladder.prescreen` as a module attribute. Reachable.
- AC1. Point: `check_ladder.script_findings`, a pure function called directly. Reachable.
- Siblings: no test pins `check_ladder.__all__` or its public surface. `test_bd164` A10 forbids redefining `prescreen` / `run_classifier` in `preflight.py` only. L16 harnesses are ambient (no `git_cwd`), so `_rung_rec` is None and none of the 7 cfgs has `script_rung`, so the byte-for-byte check holds. L18 is unchanged.

## Adversarial edges (not covered by the §3 AC table)

1. **Unknown `script_rung` with no evidence** (ambient, stale, missing, or `preflight_rung: off`). op3 says the event is emitted unconditionally, but AC6 and the RED exercise only a red receipt. An implementation that checks the value inside the evidence gate passes the RED, contradicts op3, and leaves a typo silent on ambient runs (N4).
2. **`receipt_rung` returning a `{"status": "error"}` record** (rather than raising). If an implementation gates on `status not in ("stale", "missing")`, it calls `prescreen` with `[]` and emits an escalate event. No test catches that (N2).
3. **Concurrent re-entry / double emission across cycles.** cycle 2 with a receipt that is still red emits a second verdict, tagged `cycle: 2`. The behaviour is defined (one event per call). There is no risk here, only an observation.
4. **Decoy fence: `"OFF"` / `" off "`** are not `"off"`, so they take the unknown branch and give a config-error event. This is defined by op3 ("any other value") and is not a hole.

## Findings

1. MINOR N1 (was r1 MINOR-5): the call site still hard-codes the `fresh`/`red` evidence set, while the spec says the call site "adds no mapping of its own". Advisory: expose `check_ladder.SCRIPT_EVIDENCE_STATUSES`, or make the run rule `findings or status == "fresh"`.
2. MINOR N2 (rest of r1 MINOR-2): no phase-5 test for a returned `{"status": "error"}` record. Advisory: add an AC4 param that patches `receipt_rung` to return `{"status": "error", "phase": "red", "red_step": None}`.
3. MINOR N3: op2 "malformed" should read "malformed answer". A malformed `classifier_cmd` gives config-error (L18).
4. MINOR N4: unknown `script_rung` with no evidence is untested (adversarial 1). Advisory: add an AC6 param with an ambient run (no `git_cwd`) and expect one config-error event.
5. MINOR N5: the invalid-`timeout_s` test is named AC6, but the AC6 row text does not list the case. Align the wording.
6. MINOR N6 (was r1 MINOR-7, unchanged): the s1 `test_AC7` amendment does not spy on the classifier. This is acceptable: with status `off`, `run_classifier` cannot be reached.
7. Cosmetic: the RED docstring says "spec r1".

All MINOR findings are advisory and do not block. Both round-1 MAJORs are closed, and the edits introduce no new MAJOR.

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



VERDICT: APPROVED

<!-- verdict-anchor
spec: docs/decisions/2026-10-10-bd218-s7-prescreen-script-rung.md sha256:12cfac4422592d0a2bb0d074909ee4a60fdba089c793669208b8660a34f74520
red: engine_py/tests/test_bd218_s7_script_rung.py sha256:64affee838a9334a95bd219d6c9ee69839be78a431dc013a2c141ca32ffc612b
-->
